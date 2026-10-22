"""Herramientas propias de Ctrl+IA para los agentes (servidor MCP «ctrlia») y deshacer de Claude Code.

Sin red (solo 127.0.0.1), sin wx y con los agentes falsos de ``tests/fakes``.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.services import herramientas_propias as hp
from app.services.agentes.base import AjustesSesion, TipoEvento

RAIZ = Path(__file__).resolve().parent.parent
PUENTE = RAIZ / "app" / "puente_mcp.py"
FAKE_CLAUDE = Path(__file__).parent / "fakes" / "fake_claude.py"
FAKE_CODEX = Path(__file__).parent / "fakes" / "fake_codex.py"
FAKE_AGY = Path(__file__).parent / "fakes" / "fake_agy.py"


@pytest.fixture(autouse=True)
def activadas(monkeypatch):
	"""Herramientas activadas (sin configuración cargada estarían apagadas) y todo limpio al acabar."""
	monkeypatch.setattr(hp, "activadas", lambda: True)
	yield
	hp.detener_servidor()
	with hp._candado:
		hp._sesiones.clear()


class Usos:
	"""Recoge las llamadas a ``al_usar``."""

	def __init__(self) -> None:
		self.llamadas: list[tuple[str, dict]] = []

	def __call__(self, accion: str, datos: dict) -> None:
		self.llamadas.append((accion, datos))


class Reloj:
	def __init__(self) -> None:
		self.ahora = 1000.0

	def __call__(self) -> float:
		return self.ahora


def _llamada(nombre: str, argumentos: dict, ident: int = 1) -> dict:
	return {"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {"name": nombre, "arguments": argumentos}}


def _texto(respuesta: dict) -> str:
	return "".join(b["text"] for b in respuesta["result"]["content"])


# ---------------------------------------------------------------------------
# Núcleo JSON-RPC
# ---------------------------------------------------------------------------


def test_initialize_lista_y_notificaciones():
	sesion = hp.SesionHerramientas(Usos())
	inicio = sesion.atender({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
	assert inicio["result"]["protocolVersion"] == "2025-11-25"
	assert inicio["result"]["serverInfo"]["name"] == "ctrlia"
	assert "tools" in inicio["result"]["capabilities"]
	rara = sesion.atender({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
	assert rara["result"]["protocolVersion"] == hp.VERSION_PREDETERMINADA
	assert sesion.atender({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
	assert sesion.atender({"jsonrpc": "2.0", "id": 5, "result": {}}) is None  # una respuesta: nada que contestar
	lista = sesion.atender({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
	assert [h["name"] for h in lista] == ["avisar", "dar_por_terminada", "renombrar_sesion"]
	assert all(h["_meta"] == {"anthropic/alwaysLoad": True} and h["inputSchema"]["type"] == "object" for h in lista)
	assert sesion.atender({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}


def test_errores_jsonrpc():
	sesion = hp.SesionHerramientas(Usos())
	assert sesion.atender({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["error"]["code"] == -32601
	# Antigravity pregunta antes «server/discover»: con -32601 sigue con «initialize».
	assert sesion.atender({"jsonrpc": "2.0", "id": 2, "method": "server/discover"})["error"]["code"] == -32601
	assert sesion.atender(_llamada("borrar_todo", {}))["error"]["code"] == -32602
	assert sesion.atender(["no", "es", "un", "objeto"])["error"]["code"] == -32600


def test_avisar_con_limites_de_longitud_y_frecuencia():
	usos, reloj = Usos(), Reloj()
	sesion = hp.SesionHerramientas(usos, reloj=reloj)
	correcta = sesion.atender(_llamada("avisar", {"texto": "  Ya   he\n\nterminado ", "importante": True}))
	assert correcta["result"]["isError"] is False
	assert usos.llamadas == [("avisar", {"texto": "Ya he\nterminado", "importante": True})]
	# Otro aviso enseguida: error para el modelo, sin molestar a la persona.
	seguida = sesion.atender(_llamada("avisar", {"texto": "Otro"}))
	assert seguida["result"]["isError"] is True and "espera" in _texto(seguida)
	reloj.ahora += hp.INTERVALO_AVISOS
	assert sesion.atender(_llamada("avisar", {"texto": "Otro"}))["result"]["isError"] is False
	assert usos.llamadas[-1] == ("avisar", {"texto": "Otro", "importante": False})
	reloj.ahora += hp.INTERVALO_AVISOS
	larga = sesion.atender(_llamada("avisar", {"texto": "x" * (hp.MAX_AVISO + 1)}))
	assert larga["result"]["isError"] is True and str(hp.MAX_AVISO) in _texto(larga)
	vacia = sesion.atender(_llamada("avisar", {"texto": "   "}))
	assert vacia["result"]["isError"] is True
	assert len(usos.llamadas) == 2


def test_terminar_y_renombrar():
	usos, reloj = Usos(), Reloj()
	sesion = hp.SesionHerramientas(usos, reloj=reloj)
	assert sesion.atender(_llamada("dar_por_terminada", {"motivo": "Todo hecho"}))["result"]["isError"] is False
	assert sesion.atender(_llamada("dar_por_terminada", {}))["result"]["isError"] is False
	larga = sesion.atender(_llamada("renombrar_sesion", {"titulo": "t" * (hp.MAX_TITULO + 1)}))
	assert larga["result"]["isError"] is True
	correcta = sesion.atender(_llamada("renombrar_sesion", {"titulo": " Arreglo\tdel   menú "}))
	assert "«Arreglo del menú»" in _texto(correcta)
	assert sesion.atender(_llamada("renombrar_sesion", {"titulo": "Otro"}))["result"]["isError"] is True
	assert usos.llamadas == [("dar_por_terminada", {"motivo": "Todo hecho"}), ("dar_por_terminada", {"motivo": ""}),
		("renombrar_sesion", {"titulo": "Arreglo del menú"})]


def test_fallos_de_ctrlia_y_sesion_retirada():
	def roto(_accion, _datos):
		raise RuntimeError("roto")

	sesion = hp.registrar(roto, "Claude")
	respuesta = sesion.atender(_llamada("avisar", {"texto": "hola"}))
	assert respuesta["result"]["isError"] is True and "error interno" in _texto(respuesta)
	assert hp.sesion_de_token(sesion.token) is sesion
	hp.retirar(sesion)
	assert hp.sesion_de_token(sesion.token) is None
	assert sesion.atender(_llamada("dar_por_terminada", {}))["result"]["isError"] is True
	hp.retirar(sesion)  # idempotente
	assert hp.sesion_de_token("") is None and hp.sesion_de_token("inventado") is None


def test_sin_configuracion_estan_desactivadas(monkeypatch):
	monkeypatch.undo()
	from app.core import configuracion

	monkeypatch.setattr(configuracion, "_instancia", None, raising=False)
	assert hp.activadas() is False


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------


def _post(url: str, cuerpo: bytes, cabeceras: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
	partes = urlsplit(url)
	conexion = http.client.HTTPConnection(partes.hostname, partes.port, timeout=10)
	try:
		conexion.request("POST", partes.path, body=cuerpo, headers=cabeceras)
		respuesta = conexion.getresponse()
		return respuesta.status, dict(respuesta.getheaders()), respuesta.read()
	finally:
		conexion.close()


def test_servidor_http_real():
	usos = Usos()
	sesion = hp.registrar(usos, "Codex")
	url = hp.url_servidor()
	assert url.startswith("http://127.0.0.1:") and url.endswith("/mcp")
	assert hp.url_servidor() == url  # uno solo
	bueno = {"Authorization": "Bearer " + sesion.token, "Content-Type": "application/json"}
	estado, cabeceras, cuerpo = _post(url, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
		"params": {"protocolVersion": "2025-06-18"}}).encode(), bueno)
	assert estado == 200 and cabeceras["Content-Type"].startswith("application/json")
	assert json.loads(cuerpo)["result"]["serverInfo"]["name"] == "ctrlia"
	estado, _cabeceras, cuerpo = _post(url, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(), bueno)
	assert estado == 202 and cuerpo == b""
	estado, _cabeceras, cuerpo = _post(url, json.dumps(_llamada("avisar", {"texto": "Hola desde HTTP"})).encode(), bueno)
	assert json.loads(cuerpo)["result"]["isError"] is False
	assert usos.llamadas == [("avisar", {"texto": "Hola desde HTTP", "importante": False})]
	# Token de otra sesión, sin token o de una sesión cerrada: 401 con Content-Length 0 (Codex lo necesita).
	for cabecera in ({"Authorization": "Bearer malo"}, {}):
		estado, cabeceras, cuerpo = _post(url, b"{}", cabecera)
		assert estado == 401 and cabeceras["Content-Length"] == "0" and cuerpo == b""
	assert _post(url, b"no es json", bueno)[0] == 400
	assert _post(url, b"x" * (hp.MAX_CUERPO + 1), bueno)[0] == 413
	assert _post(url, b"{}", {**bueno, "Origin": "https://ejemplo.com"})[0] == 403
	partes = urlsplit(url)
	conexion = http.client.HTTPConnection(partes.hostname, partes.port, timeout=10)
	conexion.request("GET", "/mcp")
	assert conexion.getresponse().status == 405
	conexion.close()
	assert _post(url.replace("/mcp", "/otra"), b"{}", bueno)[0] == 404
	hp.retirar(sesion)
	assert _post(url, b"{}", bueno)[0] == 401


def test_enrutado_de_antigravity_por_conversacion():
	usos = Usos()
	sesion = hp.registrar(usos, "Antigravity")
	sesion.conversacion_externa = "91a44580-e19b-43c6-9df0-a33d7c7126e2"
	url = hp.url_servidor()
	cabeceras = {"Authorization": "Bearer " + hp._token_enrutado}
	lista = json.loads(_post(url, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(), cabeceras)[2])
	assert len(lista["result"]["tools"]) == 3
	llamada = _llamada("renombrar_sesion", {"titulo": "Desde agy"})
	llamada["params"]["_meta"] = {"antigravity.google/conversation_id": sesion.conversacion_externa}
	assert json.loads(_post(url, json.dumps(llamada).encode(), cabeceras)[2])["result"]["isError"] is False
	assert usos.llamadas == [("renombrar_sesion", {"titulo": "Desde agy"})]
	llamada["params"]["_meta"] = {"antigravity.google/conversation_id": "otra"}
	ajena = json.loads(_post(url, json.dumps(llamada).encode(), cabeceras)[2])
	assert ajena["result"]["isError"] is True and "no está abierta" in _texto(ajena)


# ---------------------------------------------------------------------------
# Puente stdio
# ---------------------------------------------------------------------------


def _puente(entorno: dict[str, str], mensajes: list[dict]) -> list[dict]:
	"""Lanza el puente, le pasa los mensajes y devuelve lo que escribe (una respuesta por línea)."""
	env = {k: v for k, v in os.environ.items() if not k.startswith("CTRLIA_")}
	env.update(entorno)
	entrada = "".join(json.dumps(m) + "\n" for m in mensajes)
	resultado = subprocess.run([sys.executable, "-I", str(PUENTE)], input=entrada.encode("utf-8"), capture_output=True,
		env=env, timeout=60, check=False)
	assert resultado.returncode == 0, resultado.stderr
	return [json.loads(linea) for linea in resultado.stdout.decode("utf-8").splitlines() if linea.strip()]


def test_puente_stdio_con_url_y_token():
	usos = Usos()
	sesion = hp.registrar(usos, "Gemini CLI")
	salida = _puente({"CTRLIA_URL": hp.url_servidor(), "CTRLIA_TOKEN": sesion.token}, [
		{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
		{"jsonrpc": "2.0", "method": "notifications/initialized"},
		{"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
		_llamada("avisar", {"texto": "Por el puente"}, 3),
	])
	assert [m["id"] for m in salida] == [1, 2, 3]  # la notificación no tiene respuesta
	assert salida[0]["result"]["serverInfo"]["name"] == "ctrlia"
	assert salida[2]["result"]["isError"] is False
	assert usos.llamadas == [("avisar", {"texto": "Por el puente", "importante": False})]


def test_puente_con_archivo_de_conexion_y_errores(tmp_path):
	usos = Usos()
	sesion = hp.registrar(usos)
	conexion = tmp_path / "conexion.json"
	conexion.write_text(json.dumps({"url": hp.url_servidor(), "token": sesion.token}), encoding="utf-8")
	salida = _puente({"CTRLIA_CONEXION": str(conexion)}, [_llamada("dar_por_terminada", {}, 7)])
	assert salida[0]["id"] == 7 and salida[0]["result"]["isError"] is False
	# Token que no vale: error JSON-RPC claro, nunca silencio.
	salida = _puente({"CTRLIA_URL": hp.url_servidor(), "CTRLIA_TOKEN": "malo"}, [_llamada("avisar", {"texto": "x"}, 8)])
	assert salida[0]["id"] == 8 and "no reconoce" in salida[0]["error"]["message"]
	# Ctrl+IA cerrado (nadie escucha) o una dirección que no es de este equipo.
	hp.detener_servidor()
	for url in ("http://127.0.0.1:9/mcp", "http://ejemplo.com:80/mcp"):
		salida = _puente({"CTRLIA_URL": url, "CTRLIA_TOKEN": "t"}, [_llamada("avisar", {"texto": "x"}, 9)])
		assert salida[0]["error"]["code"] == -32000
	salida = _puente({"CTRLIA_URL": "http://127.0.0.1:9/mcp", "CTRLIA_TOKEN": "t"}, [{"jsonrpc": "2.0", "method": "x"}])
	assert salida == []  # una notificación nunca recibe respuesta


# ---------------------------------------------------------------------------
# Claude Code: servidor «en proceso» y deshacer
# ---------------------------------------------------------------------------


class Recolector:
	def __init__(self) -> None:
		self.eventos = []
		self.fin = threading.Event()

	def __call__(self, evento) -> None:
		self.eventos.append(evento)
		if evento.tipo is TipoEvento.FIN_TURNO:
			self.fin.set()

	def de_tipo(self, tipo):
		return [e for e in self.eventos if e.tipo is tipo]


@pytest.fixture
def registro_claude(tmp_path, monkeypatch):
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro))
	monkeypatch.delenv("FAKE_CLAUDE_PUNTOS", raising=False)
	return registro


def _lineas(registro: Path) -> list[dict]:
	return [json.loads(linea) for linea in registro.read_text(encoding="utf-8").splitlines()]


def _claude(tmp_path, recolector, minutos=15):
	from app.services.agentes.claude import ProveedorClaude

	proveedor = ProveedorClaude(orden_base=[sys.executable, str(FAKE_CLAUDE)], minutos_abierto=minutos)
	return proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), recolector)


def test_claude_habla_con_el_servidor_en_proceso(tmp_path, registro_claude, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "mcp")
	recolector = Recolector()
	conversacion = _claude(tmp_path, recolector)
	try:
		conversacion.enviar("Avísame")
		assert recolector.fin.wait(20)
		lineas = _lineas(registro_claude)
		argv = lineas[0]["argv"]
		assert "mcp__ctrlia" in argv[argv.index("--allowedTools"):]
		assert next(l["inicio"] for l in lineas if "inicio" in l)["sdkMcpServers"] == ["ctrlia"]
		inicio, acuse, lista, llamada = next(l["mcp"] for l in lineas if isinstance(l.get("mcp"), list))
		assert inicio["subtype"] == "success" and inicio["response"]["mcp_response"]["result"]["serverInfo"]["name"] == "ctrlia"
		assert acuse["response"]["mcp_response"] == {"jsonrpc": "2.0", "result": {}, "id": 0}
		assert len(lista["response"]["mcp_response"]["result"]["tools"]) == 3
		assert llamada["response"]["mcp_response"]["id"] == 103
		aviso = next(e for e in recolector.de_tipo(TipoEvento.AVISO) if e.datos.get("herramienta_ctrlia"))
		assert aviso.datos["herramienta_ctrlia"] == "avisar" and aviso.datos["importante"] is True
		assert aviso.texto == "Claude te avisa: Prueba hecha"
		assert recolector.de_tipo(TipoEvento.MENSAJE)[-1].texto == "mcp:Hecho: Ctrl+IA ha avisado a la persona."
		# Las respuestas MCP nunca abren un turno «espontáneo».
		assert len(recolector.de_tipo(TipoEvento.FIN_TURNO)) == 1
		# El mensaje lleva su uuid, el mismo que queda en ``ultimo_uuid_usuario``.
		mensaje = next(l["mensaje"] for l in lineas if "mensaje" in l)
		assert mensaje["uuid"] == conversacion.ultimo_uuid_usuario and uuid.UUID(mensaje["uuid"])
		token = conversacion.herramientas_ctrlia.token
	finally:
		conversacion.cerrar()
	assert hp.sesion_de_token(token) is None


def test_claude_sin_herramientas_no_declara_nada(tmp_path, registro_claude, monkeypatch):
	monkeypatch.setattr(hp, "activadas", lambda: False)
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	recolector = Recolector()
	conversacion = _claude(tmp_path, recolector)
	try:
		conversacion.enviar("Hola")
		assert recolector.fin.wait(20)
		assert "mcp__ctrlia" not in _lineas(registro_claude)[0]["argv"]
		assert conversacion.herramientas_ctrlia is None
	finally:
		conversacion.cerrar()


def test_claude_deshacer_con_el_proceso_abierto(tmp_path, registro_claude, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	recolector = Recolector()
	conversacion = _claude(tmp_path, recolector)
	try:
		conversacion.enviar("Escribe algo")
		assert recolector.fin.wait(20)
		marca = conversacion.ultimo_uuid_usuario
		prueba = conversacion.deshacer_archivos(marca, prueba=True)
		assert prueba == {"canRewind": True, "error": "", "filesChanged": ["C:\\proyecto\\nota.txt", "C:\\proyecto\\otro.txt"],
			"insertions": 1, "deletions": 2}
		real = conversacion.deshacer_archivos(marca)
		assert real["canRewind"] is True and real["filesChanged"] == []
		with pytest.raises(ValueError, match="no guardó"):
			conversacion.deshacer_archivos(str(uuid.uuid4()))
		with pytest.raises(ValueError, match="no guardó"):
			conversacion.deshacer_archivos(str(uuid.uuid4()), prueba=True)
		with pytest.raises(ValueError):
			conversacion.deshacer_archivos("no-es-un-uuid")
		lineas = _lineas(registro_claude)
		assert len([l for l in lineas if "argv" in l]) == 1  # en el mismo proceso
		peticiones = [l["control"] for l in lineas if (l.get("control") or {}).get("subtype") == "rewind_files"]
		assert peticiones[0] == {"subtype": "rewind_files", "user_message_id": marca, "dry_run": True}
		assert peticiones[1]["dry_run"] is False
		# Las respuestas esperadas no abren turnos ni emiten nada.
		assert len(recolector.de_tipo(TipoEvento.FIN_TURNO)) == 1
	finally:
		conversacion.cerrar()


def test_claude_deshacer_con_un_proceso_nuevo(tmp_path, registro_claude, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	recolector = Recolector()
	conversacion = _claude(tmp_path, recolector, minutos=0)  # un proceso por mensaje: al acabar se cierra
	try:
		conversacion.enviar("Escribe algo")
		assert recolector.fin.wait(20)
		marca = conversacion.ultimo_uuid_usuario
		limite = time.monotonic() + 10
		while conversacion._vivo is not None and time.monotonic() < limite:  # noqa: SLF001
			time.sleep(0.05)
		monkeypatch.setenv("FAKE_CLAUDE_PUNTOS", marca)  # el proceso nuevo «recuerda» el punto de ese mensaje
		resultado = conversacion.deshacer_archivos(marca, prueba=True)
		assert resultado["canRewind"] is True and resultado["insertions"] == 1
		procesos = [l["argv"] for l in _lineas(registro_claude) if "argv" in l]
		assert len(procesos) == 2
		nuevo = procesos[1]
		assert nuevo[nuevo.index("--resume") + 1] == conversacion.id_externo and "--strict-mcp-config" in nuevo
	finally:
		conversacion.cerrar()


def test_claude_deshacer_solo_con_el_agente_parado(tmp_path, registro_claude, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "cuelga")
	recolector = Recolector()
	conversacion = _claude(tmp_path, recolector)
	try:
		with pytest.raises(RuntimeError, match="no existe"):
			conversacion.deshacer_archivos(str(uuid.uuid4()))
		conversacion.enviar("Trabaja")
		with pytest.raises(RuntimeError, match="trabajando"):
			conversacion.deshacer_archivos(conversacion.ultimo_uuid_usuario)
	finally:
		conversacion.cerrar()


def test_resultado_deshacer_y_otros_agentes(tmp_path):
	from app.services.agentes.base import Conversacion
	from app.services.agentes.claude import _resultado_deshacer

	no_activado = _resultado_deshacer({"response": {"subtype": "success", "response": {"canRewind": False,
		"error": "File rewinding is not enabled."}}})
	assert no_activado["canRewind"] is False and "no tiene activado" in no_activado["error"]
	with pytest.raises(RuntimeError, match="Algo raro"):
		_resultado_deshacer({"response": {"subtype": "error", "error": "Algo raro"}})

	class Otra(Conversacion):
		enviar = detener = responder = cerrar = lambda *a, **k: None

	otra = Otra(AjustesSesion(carpeta=tmp_path), lambda e: None)
	assert otra.deshacer_archivos(str(uuid.uuid4())) is None and otra.ultimo_uuid_usuario == ""


# ---------------------------------------------------------------------------
# Codex, ACP y Antigravity: lo que se les declara
# ---------------------------------------------------------------------------


def test_codex_lleva_el_servidor_en_el_hilo(tmp_path):
	from app.services.agentes.codex import ProveedorCodex

	registro = tmp_path / "codex.jsonl"
	proveedor = ProveedorCodex(orden=[sys.executable, str(FAKE_CODEX), "normal", str(registro)])
	recolector = Recolector()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), recolector)
	try:
		conversacion.enviar("hola")
		assert recolector.fin.wait(20)
		inicio = next(json.loads(l) for l in registro.read_text(encoding="utf-8").splitlines()
			if json.loads(l).get("method") == "thread/start")
		config = inicio["params"]["config"]
		token = conversacion.herramientas_ctrlia.token
		assert config == {"mcp_servers.ctrlia.url": hp.url_servidor(),
			"mcp_servers.ctrlia.http_headers": {"Authorization": "Bearer " + token},
			"mcp_servers.ctrlia.default_tools_approval_mode": "approve"}
		# Por SSH, no: Codex del otro equipo no llega al 127.0.0.1 de Windows.
		conversacion.ajustes.extra["remoto"] = {"equipo": "mac", "carpeta": "/Users/x"}
		assert conversacion._params_herramientas_ctrlia() == {}  # noqa: SLF001
	finally:
		conversacion.cerrar()
		proveedor.cerrar()
	assert hp.sesion_de_token(token) is None


def test_acp_http_o_puente(tmp_path):
	from app.services.agentes.acp import ProveedorAcp

	proveedor = ProveedorAcp("prueba", "Gemini CLI (Google)", "Agente falso", [sys.executable, "falso.py"])
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda e: None)
	try:
		conversacion._capacidades_agente = {"mcpCapabilities": {"http": True}}  # noqa: SLF001
		http_ = conversacion._servidores_mcp()  # noqa: SLF001
		token = conversacion.herramientas_ctrlia.token
		assert http_ == [{"type": "http", "name": "ctrlia", "url": hp.url_servidor(),
			"headers": [{"name": "Authorization", "value": "Bearer " + token}]}]
		conversacion._capacidades_agente = {}  # noqa: SLF001
		(stdio,) = conversacion._servidores_mcp()  # noqa: SLF001
		assert stdio["name"] == "ctrlia" and Path(stdio["command"]).is_absolute()
		assert stdio["args"][-1].endswith("puente_mcp.py")
		assert {"name": "CTRLIA_TOKEN", "value": token} in stdio["env"]
		assert {"name": "CTRLIA_URL", "value": hp.url_servidor()} in stdio["env"]
		assert conversacion.herramientas_ctrlia.agente == "Gemini CLI"
		inventario = conversacion.inventario()
		assert inventario.servidores_mcp[0][0] == "ctrlia"
		conversacion.ajustes.extra["remoto"] = {"equipo": "mac", "carpeta": "/Users/x"}
		assert conversacion._servidores_mcp() == []  # noqa: SLF001
	finally:
		conversacion.cerrar()


def test_antigravity_agente_regla_y_enrutado(tmp_path, monkeypatch):
	from app.services.agentes import antigravity

	ajustes_agy = tmp_path / "settings.json"
	monkeypatch.setattr(antigravity, "ruta_ajustes_cli", lambda: ajustes_agy)
	recolector = Recolector()
	conversacion = antigravity.ConversacionAntigravity(AjustesSesion(carpeta=tmp_path), recolector,
		[sys.executable, str(FAKE_AGY)])
	try:
		orden = conversacion._crear_proceso().orden  # noqa: SLF001 - sin arrancarlo
		carpeta = hp.carpeta_agente_agy()
		# Un complemento, nunca un agente propio (agy lo dejaría sin herramientas de edición).
		assert "--agent" not in orden and orden[orden.index("--add-dir") + 1] == str(carpeta)
		assert not (carpeta / ".agents" / "agents").exists()
		complemento = carpeta / ".agents" / "plugins" / "ctrlia"
		assert json.loads((complemento / "plugin.json").read_text(encoding="utf-8"))["name"] == "ctrlia"
		definicion = (complemento / "mcp_config.json").read_text(encoding="utf-8")
		servidor = json.loads(definicion)["mcpServers"]["ctrlia"]
		assert "CTRLIA_CONEXION" in servidor["env"] and "puente_mcp.py" in " ".join(servidor["args"])
		# Nada que cambie entre arranques.
		assert conversacion.herramientas_ctrlia.token not in definicion and hp.url_servidor() not in definicion
		conexion = json.loads(hp.ruta_conexion().read_text(encoding="utf-8"))
		assert conexion == {"url": hp.url_servidor(), "token": hp._token_enrutado}
		assert antigravity.leer_reglas(ajustes_agy) == ["mcp(ctrlia_ctrlia/*)"]
		# La sesión se reconoce por la conversación de agy.
		conversacion._fijar_id("conv-1")  # noqa: SLF001
		assert conversacion.herramientas_ctrlia.conversacion_externa == "conv-1"
		salida = _puente({"CTRLIA_CONEXION": str(hp.ruta_conexion())}, [{**_llamada("avisar", {"texto": "Desde agy"}, 4),
			"params": {"name": "avisar", "arguments": {"texto": "Desde agy"},
				"_meta": {"antigravity.google/conversation_id": "conv-1"}}}])
		assert salida[0]["result"]["isError"] is False
		aviso = next(e for e in recolector.de_tipo(TipoEvento.AVISO) if e.datos.get("herramienta_ctrlia"))
		assert aviso.texto == "Antigravity te avisa: Desde agy"
		assert conversacion.inventario().servidores_mcp[0][0] == "ctrlia"
		# Desactivadas: ni agente ni regla nueva.
		monkeypatch.setattr(hp, "activadas", lambda: False)
		orden = conversacion._crear_proceso().orden  # noqa: SLF001
		assert "--add-dir" not in orden and conversacion.herramientas_ctrlia is None
	finally:
		conversacion.cerrar()


def test_la_ventana_atiende_los_usos(app_wx, tmp_path, monkeypatch, data_temporal):
	"""La interfaz renombra, da por terminada y avisa (sin mostrar nada ni hablar)."""
	from app.core.configuracion import Configuracion
	from app.services.agentes.base import Evento
	from app.services.agentes.claude import ProveedorClaude
	from app.services.almacen import Almacen
	from app.ui import avisos
	from app.ui.ventana_principal import VentanaPrincipal
	from tests.ayudas_wx import procesar_eventos

	dichos: list[str] = []
	importantes: list[str] = []
	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	monkeypatch.setattr(avisos, "notificar", lambda texto, *a, **k: importantes.append(texto))
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"historial.ollama": False, "general.confirmar_salida": False, "general.abrir_ultima_sesion": False})
	marco = VentanaPrincipal(config, None, almacen=Almacen(tmp_path / "ventana.db"),
		proveedores={"claude": ProveedorClaude(orden_base=[sys.executable, str(FAKE_CLAUDE)])})
	try:
		procesar_eventos()
		clave = marco.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Antes"))
		marco._al_listar(marco.gestor.listar(), True)  # noqa: SLF001

		def evento(texto_evento: str, /, **datos) -> Evento:
			return Evento(TipoEvento.AVISO, texto_evento, datos)

		marco._al_evento(clave, evento("Claude te avisa: Listo", herramienta_ctrlia="avisar", texto="Listo", importante=False))  # noqa: SLF001
		marco._al_evento(clave, evento("Claude te avisa: Urgente", herramienta_ctrlia="avisar", texto="Urgente",  # noqa: SLF001
			importante=True))
		assert any(t.endswith("Claude te avisa: Listo") for t in dichos)
		assert any(t.endswith("Claude te avisa: Urgente") for t in importantes)
		marco._al_evento(clave, evento("Claude ha renombrado la sesión: «Después».", herramienta_ctrlia="renombrar_sesion",  # noqa: SLF001
			titulo="Después"))
		assert marco.almacen.sesion(clave).titulo == "Después" and marco.fila_de(clave).titulo == "Después"
		marco._al_evento(clave, evento("Claude ha dado la sesión por terminada.", herramienta_ctrlia="dar_por_terminada",  # noqa: SLF001
			motivo=""))
		assert marco.fila_de(clave).terminada is True
		assert marco.gestor.listar() and next(f for f in marco.gestor.listar() if f.clave == clave).terminada
	finally:
		marco.salir()
		procesar_eventos()


def test_orden_del_puente(monkeypatch, tmp_path):
	orden = hp.orden_puente()
	assert orden[1] == "-I" and Path(orden[2]) == PUENTE
	monkeypatch.setattr(sys, "frozen", True, raising=False)
	monkeypatch.setattr(sys, "executable", str(tmp_path / "CtrlIA.exe"))
	assert hp.orden_puente() == [str((tmp_path / "ctrlia-mcp.exe").resolve())]
