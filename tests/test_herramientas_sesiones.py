"""Comandos de sesión de Claude Code: servidor MCP «en proceso» ``ccd_session_mgmt`` (AIChat #431).

Sin red y sin el programa real: con ``GestorSesiones`` de verdad sobre una base temporal y,
para el recorrido entero, el CLI falso ``tests/fakes/fake_claude.py``.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from app.services import herramientas_propias as hp
from app.services import herramientas_sesiones as hs
from app.services.agentes.base import AjustesSesion, Estado, Evento, TipoEvento
from app.services.agentes.claude import ProveedorClaude
from app.services.almacen import Almacen
from app.services.gestor import FilaSesion, GestorSesiones, MensajeVista

FAKE_CLAUDE = Path(__file__).parent / "fakes" / "fake_claude.py"
OPCIONES = {"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False}


class Reloj:
	def __init__(self) -> None:
		self.ahora = 1000.0

	def __call__(self) -> float:
		return self.ahora


class Usos:
	def __init__(self) -> None:
		self.llamadas: list[tuple[str, dict]] = []

	def __call__(self, accion: str, datos: dict) -> None:
		self.llamadas.append((accion, datos))


@pytest.fixture
def mundo(tmp_path, data_temporal):
	"""Un gestor con sesiones propias, una importada y una de otro programa."""
	recibidos: list[tuple[str, Evento]] = []
	almacen = Almacen(tmp_path / "sesiones.db")
	proveedor = ProveedorClaude(orden_base=[sys.executable, str(FAKE_CLAUDE)])
	gestor = GestorSesiones(almacen, {"claude": proveedor}, lambda c, e: recibidos.append((c, e)),
		lambda clave, defecto: OPCIONES.get(clave, defecto))
	yo = gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Esta sesión"))
	otra = gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Otra sesión"))
	favorita = gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="La favorita"))
	gestor.marcar_favorita(favorita, True)
	almacen.anadir_mensaje(otra, "usuario", "Hablamos de la canción del verano y de su letra.")
	almacen.anadir_mensaje(otra, "asistente", "token=abc123secreto y más cosas")
	ajena = FilaSesion("claude:ajena", "claude", "Sesión de la terminal", carpeta="C:\\otra", origen="terminal", actividad=500.0,
		id_externo="id-ajena")
	gestor._listar_externas = lambda: [FilaSesion(ajena.clave, ajena.proveedor, ajena.titulo, carpeta=ajena.carpeta,
		origen=ajena.origen, actividad=ajena.actividad, id_externo=ajena.id_externo)]  # noqa: SLF001
	reloj = Reloj()
	yield type("Mundo", (), {"gestor": gestor, "almacen": almacen, "yo": yo, "otra": otra, "favorita": favorita,
		"reloj": reloj, "usos": Usos(), "recibidos": recibidos,
		"herramientas": hs.HerramientasSesiones(gestor, yo, reloj)})
	gestor.cerrar_todo()
	almacen.cerrar()


def _llamar(mundo, nombre: str, **argumentos) -> str:
	return mundo.herramientas.llamar(nombre, argumentos, mundo.usos)["content"][0]["text"]


def _listar(mundo, **argumentos) -> list[dict]:
	return json.loads(_llamar(mundo, "list_sessions", **argumentos))


# -- Definición --------------------------------------------------------------------


def test_nombres_y_parametros_de_la_app_de_escritorio():
	"""Los de Claude Code 2.1.308: comandos como /wrap los llaman por su nombre completo."""
	definiciones = {h["name"]: h for h in hs.HERRAMIENTAS}
	assert hs.NOMBRE_SERVIDOR == "ccd_session_mgmt"
	assert {"list_sessions", "get_session", "archive_session", "search_session_transcripts"} <= set(definiciones)
	assert definiciones["get_session"]["inputSchema"]["required"] == ["session_id"]
	assert definiciones["archive_session"]["inputSchema"]["required"] == ["session_id"]
	assert "reason" in definiciones["archive_session"]["inputSchema"]["properties"]
	assert definiciones["search_session_transcripts"]["inputSchema"]["required"] == ["query"]
	assert {"limit", "include_archived"} <= set(definiciones["list_sessions"]["inputSchema"]["properties"])


def test_lo_que_cambia_lo_dicen_las_anotaciones():
	cambian = {h["name"] for h in hs.HERRAMIENTAS if not h["annotations"]["readOnlyHint"]}
	assert cambian == {"archive_session", "unarchive_session"}


def test_no_hay_herramientas_peligrosas():
	nombres = {h["name"] for h in hs.HERRAMIENTAS}
	assert not nombres & {"delete_session", "send_message", "stop_session", "clear_session", "set_session_permission_mode"}


def test_atiende_el_protocolo_con_su_nombre(mundo):
	inicio = mundo.herramientas.atender({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, mundo.usos)
	assert inicio["result"]["serverInfo"]["name"] == "ccd_session_mgmt"
	lista = mundo.herramientas.atender({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, mundo.usos)
	assert [h["name"] for h in lista["result"]["tools"]] == [h["name"] for h in hs.HERRAMIENTAS]
	desconocida = mundo.herramientas.atender({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
		"params": {"name": "avisar", "arguments": {}}}, mundo.usos)
	assert desconocida["error"]["code"] == -32602


def test_el_servidor_ctrlia_sigue_igual(mundo):
	lista = hp.atender_mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, lambda *a: {})
	assert len(lista["result"]["tools"]) == 3


# -- Leer ----------------------------------------------------------------------------


def test_lista_sin_esta_sesion_y_con_los_campos_de_la_app(mundo):
	filas = _listar(mundo)
	claves = {f["sessionId"] for f in filas}
	assert mundo.yo not in claves and {mundo.otra, mundo.favorita, "claude:ajena"} == claves
	otra = next(f for f in filas if f["sessionId"] == mundo.otra)
	assert otra["title"] == "Otra sesión" and otra["isArchived"] is False and otra["isRunning"] is False
	assert otra["readOnly"] is False and otra["lastActivityAt"].endswith("Z")
	assert next(f for f in filas if f["sessionId"] == "claude:ajena")["readOnly"] is True


def test_lista_lo_mas_reciente_primero_con_limite(mundo):
	mundo.almacen.actualizar_sesion(mundo.otra, actividad=9e9)
	filas = _listar(mundo, limit=1)
	assert [f["sessionId"] for f in filas] == [mundo.otra]


def test_lista_sin_ocultas_salvo_que_se_pida(mundo):
	mundo.gestor.ocultar(mundo.otra)
	assert mundo.otra not in {f["sessionId"] for f in _listar(mundo)}
	mundo.reloj.ahora += hs.VIGENCIA_LISTA
	filas = _listar(mundo, include_archived=True)
	assert next(f for f in filas if f["sessionId"] == mundo.otra)["isArchived"] is True


def test_lista_de_un_grupo(mundo):
	identificador = mundo.almacen.crear_grupo("Obra")
	mundo.almacen.anadir_a_grupo(mundo.otra, identificador)
	filas = _listar(mundo, group="Obra")
	assert [f["sessionId"] for f in filas] == [mundo.otra] and filas[0]["groups"] == ["Obra"]


def test_detalles_de_esta_sesion_y_por_id_del_agente(mundo):
	ficha = json.loads(_llamar(mundo, "get_session", session_id="self"))
	assert ficha["sessionId"] == mundo.yo and ficha["isCurrentSession"] is True
	assert json.loads(_llamar(mundo, "get_session", session_id="id-ajena"))["sessionId"] == "claude:ajena"


def test_sesion_que_no_existe(mundo):
	with pytest.raises(hp.ErrorHerramienta, match="No hay ninguna sesión"):
		_llamar(mundo, "get_session", session_id="nada")
	with pytest.raises(hp.ErrorHerramienta, match="Falta session_id"):
		_llamar(mundo, "get_session")


def test_buscar_sin_acentos_con_aviso_y_sin_esta_sesion(mundo):
	mundo.almacen.anadir_mensaje(mundo.yo, "usuario", "En esta también sale la cancion")
	texto = _llamar(mundo, "search_session_transcripts", query="cancion")
	assert texto.startswith(hs.AVISO_DATOS)
	filas = json.loads(texto.split("\n\n", 1)[1])
	assert [f["sessionId"] for f in filas] == [mundo.otra] and "canción del verano" in filas[0]["snippet"]


def test_una_coincidencia_por_sesion(mundo):
	mundo.almacen.anadir_mensaje(mundo.otra, "asistente", "Otra vez la canción")
	filas = json.loads(_llamar(mundo, "search_session_transcripts", query="canción").split("\n\n", 1)[1])
	assert len(filas) == 1


def test_buscar_sin_resultados_y_consulta_mala(mundo):
	assert "No hay ninguna sesión" in _llamar(mundo, "search_session_transcripts", query="palabrainexistente")
	with pytest.raises(hp.ErrorHerramienta, match="al menos 2"):
		_llamar(mundo, "search_session_transcripts", query="a")
	with pytest.raises(hp.ErrorHerramienta, match="pasa de"):
		_llamar(mundo, "search_session_transcripts", query="x" * (hs.MAX_CONSULTA + 1))


def test_la_busqueda_se_corta_a_tiempo(mundo, monkeypatch):
	def buscar(_consulta, cancelado, _maximo):
		mundo.reloj.ahora += hs.TIEMPO_BUSQUEDA
		assert cancelado()
		return []

	monkeypatch.setattr(mundo.gestor, "buscar", buscar)
	assert "se cortó" in _llamar(mundo, "search_session_transcripts", query="canción")


def test_leer_otra_sesion_es_datos_y_va_sin_secretos(mundo):
	texto = _llamar(mundo, "list_events", session_id=mundo.otra)
	assert texto.startswith(hs.AVISO_DATOS)
	assert "canción del verano" in texto and "abc123secreto" not in texto
	assert texto.index("canción") < texto.index("más cosas")


def test_leer_con_cursor_hacia_atras(mundo):
	texto = _llamar(mundo, "list_events", session_id=mundo.otra, limit=1)
	assert "canción" not in texto and "before_uuid=1" in texto
	assert "canción" in _llamar(mundo, "list_events", session_id=mundo.otra, limit=1, before_uuid="1")
	with pytest.raises(hp.ErrorHerramienta, match="cursor"):
		_llamar(mundo, "list_events", session_id=mundo.otra, before_uuid="abc")


def test_leer_esta_sesion_no(mundo):
	with pytest.raises(hp.ErrorHerramienta, match="otras sesiones"):
		_llamar(mundo, "list_events", session_id="self")


def test_fragmento_recorta_alrededor_de_lo_buscado():
	texto = "a" * 500 + " canción " + "b" * 500
	fragmento = hs._fragmento(texto, "cancion")  # noqa: SLF001
	assert "canción" in fragmento and len(fragmento) <= hs.MAX_FRAGMENTO + 2
	assert fragmento.startswith("…") and fragmento.endswith("…")


def test_respuesta_demasiado_larga_se_recorta_y_se_dice(monkeypatch):
	monkeypatch.setattr(hs, "MAX_RESPUESTA", 100)
	texto = hs._json_con_tope([{"x": "y" * 30} for _ in range(8)])  # noqa: SLF001
	assert "omitido" in texto and len(texto.split("\n\n")[0]) <= 100
	assert json.loads(texto.split("\n\n")[0])


def test_fecha_iso():
	assert hs.fecha_iso(0) == ""
	assert hs.fecha_iso(1785247846.075) == "2026-07-28T14:10:46.075Z"


# -- Cambiar ---------------------------------------------------------------------------


def _oculta(mundo, clave: str) -> bool:
	meta = mundo.almacen.metadatos().get(clave)
	return bool(meta and meta.oculta)


def test_archivar_es_ocultar_y_avisa(mundo):
	assert "Hecho" in _llamar(mundo, "archive_session", session_id=mundo.otra, reason="PR  fusionada")
	assert _oculta(mundo, mundo.otra)
	assert mundo.usos.llamadas == [("ocultar_sesion", {"sesion": mundo.otra, "titulo": "Otra sesión", "motivo": "PR fusionada"})]
	assert mundo.otra not in {f["sessionId"] for f in _listar(mundo)}


def test_archivar_dos_veces_no_avisa_otra_vez(mundo):
	_llamar(mundo, "archive_session", session_id=mundo.otra)
	assert "ya estaba oculta" in _llamar(mundo, "archive_session", session_id=mundo.otra)
	assert len(mundo.usos.llamadas) == 1


def test_volver_a_mostrar(mundo):
	_llamar(mundo, "archive_session", session_id=mundo.otra)
	assert "vuelve a estar" in _llamar(mundo, "unarchive_session", session_id=mundo.otra)
	assert not _oculta(mundo, mundo.otra)
	assert mundo.usos.llamadas[-1][0] == "mostrar_sesion"
	assert "no estaba oculta" in _llamar(mundo, "unarchive_session", session_id=mundo.otra)


@pytest.mark.parametrize("herramienta", ["archive_session", "unarchive_session"])
def test_nunca_toca_las_sesiones_de_otros_programas(mundo, herramienta):
	with pytest.raises(hp.ErrorHerramienta, match="solo lectura"):
		_llamar(mundo, herramienta, session_id="claude:ajena")
	assert not _oculta(mundo, "claude:ajena")
	assert mundo.usos.llamadas == []


def test_nunca_toca_las_importadas(mundo, tmp_path):
	from app.services.almacen import SesionPropia, nueva_clave_importada

	clave = nueva_clave_importada()
	mundo.almacen.guardar_sesion(SesionPropia(clave=clave, proveedor="claude", titulo="Importada", carpeta=str(tmp_path),
		creada=1.0, actividad=1.0))
	with pytest.raises(hp.ErrorHerramienta, match="solo lectura"):
		_llamar(mundo, "archive_session", session_id=clave)
	assert mundo.usos.llamadas == []


def test_no_archiva_esta_sesion(mundo):
	with pytest.raises(hp.ErrorHerramienta, match="esta misma sesión"):
		_llamar(mundo, "archive_session", session_id="self")
	assert not _oculta(mundo, mundo.yo)


def test_no_archiva_una_que_trabaja(mundo, monkeypatch):
	monkeypatch.setattr(mundo.gestor, "ocupada", lambda clave: clave == mundo.otra)
	with pytest.raises(hp.ErrorHerramienta, match="está trabajando"):
		_llamar(mundo, "archive_session", session_id=mundo.otra)
	assert not _oculta(mundo, mundo.otra)


def test_no_archiva_la_abierta_en_la_ventana(mundo):
	mundo.gestor.sesion_abierta = mundo.otra
	with pytest.raises(hp.ErrorHerramienta, match="tiene abierta"):
		_llamar(mundo, "archive_session", session_id=mundo.otra)


def test_no_archiva_una_favorita(mundo):
	with pytest.raises(hp.ErrorHerramienta, match="favorita"):
		_llamar(mundo, "archive_session", session_id=mundo.favorita)


def test_motivo_demasiado_largo(mundo):
	with pytest.raises(hp.ErrorHerramienta, match="motivo"):
		_llamar(mundo, "archive_session", session_id=mundo.otra, reason="x" * (hp.MAX_MOTIVO + 1))


# -- Frecuencia y cierre -----------------------------------------------------------------


def test_limite_de_consultas_por_minuto(mundo):
	for _ in range(hs.MAX_CONSULTAS):
		_listar(mundo)
	with pytest.raises(hp.ErrorHerramienta, match="Demasiadas veces"):
		_listar(mundo)
	mundo.reloj.ahora += hs.VENTANA_LIMITES
	assert _listar(mundo)


def test_limite_de_busquedas(mundo):
	for _ in range(hs.MAX_BUSQUEDAS):
		_llamar(mundo, "search_session_transcripts", query="canción")
	with pytest.raises(hp.ErrorHerramienta, match="volver a buscar"):
		_llamar(mundo, "search_session_transcripts", query="canción")


def test_limite_de_cambios(mundo):
	for _ in range(hs.MAX_CAMBIOS // 2):
		_llamar(mundo, "archive_session", session_id=mundo.otra)
		_llamar(mundo, "unarchive_session", session_id=mundo.otra)
	with pytest.raises(hp.ErrorHerramienta, match="cambiar otra sesión"):
		_llamar(mundo, "archive_session", session_id=mundo.otra)
	assert not _oculta(mundo, mundo.otra)


def test_una_sesion_cerrada_no_contesta(mundo):
	mundo.herramientas.retirada = True
	with pytest.raises(hp.ErrorHerramienta, match="ya no está abierta"):
		_listar(mundo)


def test_la_lista_se_reutiliza_unos_segundos(mundo, monkeypatch):
	llamadas = []
	original = mundo.gestor.listar
	monkeypatch.setattr(mundo.gestor, "listar", lambda: llamadas.append(1) or original())
	_listar(mundo)
	_listar(mundo)
	assert len(llamadas) == 1
	mundo.reloj.ahora += hs.VIGENCIA_LISTA
	_listar(mundo)
	assert len(llamadas) == 2


def test_open_session_in_explica_que_no_se_puede(mundo):
	with pytest.raises(hp.ErrorHerramienta, match="no está disponible"):
		_llamar(mundo, "open_session_in", session_id=mundo.otra)


def test_los_datos_salen_en_mensajes_vista(mundo):
	"""Un mensaje de herramienta dice cuál fue."""
	texto = hs.HerramientasSesiones._evento(MensajeVista("herramienta", "Leer", 0.0, herramienta="Read"))  # noqa: SLF001
	assert texto == "[Herramienta (Read)] Leer"


def test_estados_que_cuentan_como_ocupada(mundo):
	assert Estado.ESPERANDO_USUARIO in hs.ESTADOS_OCUPADA and Estado.INACTIVA not in hs.ESTADOS_OCUPADA


# -- Con la conversación ---------------------------------------------------------------


@pytest.fixture
def activadas(monkeypatch):
	monkeypatch.setattr(hp, "activadas", lambda: True)
	yield
	with hp._candado:  # noqa: SLF001
		hp._sesiones.clear()  # noqa: SLF001


def _lineas(ruta: Path) -> list[dict]:
	return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]


def _turno(mundo, tmp_path, monkeypatch, llamadas: list) -> tuple[list[dict], threading.Event]:
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "mcp_sesiones")
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro))
	monkeypatch.setenv("FAKE_CLAUDE_LLAMADAS", json.dumps(llamadas))
	fin = threading.Event()
	anterior = mundo.gestor._al_evento  # noqa: SLF001

	def al_evento(clave, evento):
		anterior(clave, evento)
		if evento.tipo is TipoEvento.FIN_TURNO:
			fin.set()

	mundo.gestor._al_evento = al_evento  # noqa: SLF001
	mundo.gestor.enviar(mundo.yo, "/wrap")
	assert fin.wait(20)
	return _lineas(registro), fin


def test_claude_declara_y_permite_los_dos_servidores(mundo, tmp_path, monkeypatch, activadas):
	lineas, _fin = _turno(mundo, tmp_path, monkeypatch, [])
	argv = lineas[0]["argv"]
	permitidas = set(argv[argv.index("--allowedTools"):])
	assert {"mcp__ctrlia", "mcp__ccd_session_mgmt__list_sessions", "mcp__ccd_session_mgmt__get_session"} <= permitidas
	# Leer los mensajes de otra sesión y ocultarla pide permiso, como cualquier otra herramienta.
	assert "mcp__ccd_session_mgmt" not in permitidas
	assert not {"mcp__ccd_session_mgmt__" + h for h in ("search_session_transcripts", "list_events",
		"archive_session", "unarchive_session")} & permitidas
	assert next(l["inicio"] for l in lineas if "inicio" in l)["sdkMcpServers"] == ["ctrlia", "ccd_session_mgmt"]
	inicio, lista, otro = next(l["mcp"] for l in lineas if isinstance(l.get("mcp"), list))
	assert inicio["response"]["mcp_response"]["result"]["serverInfo"]["name"] == "ccd_session_mgmt"
	assert len(lista["response"]["mcp_response"]["result"]["tools"]) == len(hs.HERRAMIENTAS)
	assert otro["subtype"] == "error" and otro["error"] == "Servidor MCP desconocido"


def test_un_comando_lista_y_archiva_de_verdad(mundo, tmp_path, monkeypatch, activadas):
	lineas, _fin = _turno(mundo, tmp_path, monkeypatch,
		[["list_sessions", {}], ["archive_session", {"session_id": mundo.otra, "reason": "Terminada"}]])
	respuestas = next(l["mcp"] for l in lineas if isinstance(l.get("mcp"), list))
	lista = respuestas[3]["response"]["mcp_response"]["result"]
	assert mundo.otra in {f["sessionId"] for f in json.loads(lista["content"][0]["text"])}
	archivar = respuestas[4]["response"]["mcp_response"]["result"]
	assert archivar["isError"] is False
	assert _oculta(mundo, mundo.otra)
	avisos = [e for c, e in mundo.recibidos if c == mundo.yo and e.tipo is TipoEvento.AVISO and e.datos.get("herramienta_ctrlia")]
	assert [a.datos["herramienta_ctrlia"] for a in avisos] == ["ocultar_sesion"]
	assert avisos[0].texto == "Claude ha ocultado la sesión «Otra sesión»: Terminada. Está en Ver, Mostrar, Ocultas."
	assert any("ha ocultado" in m.texto for m in mundo.almacen.mensajes(mundo.yo))


def test_un_uso_rechazado_vuelve_al_modelo_como_error(mundo, tmp_path, monkeypatch, activadas):
	lineas, _fin = _turno(mundo, tmp_path, monkeypatch, [["archive_session", {"session_id": "claude:ajena"}]])
	respuesta = next(l["mcp"] for l in lineas if isinstance(l.get("mcp"), list))[3]
	resultado = respuesta["response"]["mcp_response"]["result"]
	assert resultado["isError"] is True and "solo lectura" in resultado["content"][0]["text"]


def test_sin_la_opcion_no_se_declara_ninguno(mundo, tmp_path, monkeypatch):
	monkeypatch.setattr(hp, "activadas", lambda: False)
	lineas, _fin = _turno(mundo, tmp_path, monkeypatch, [])
	assert "mcp__ccd_session_mgmt" not in lineas[0]["argv"]
	assert "sdkMcpServers" not in next(l["inicio"] for l in lineas if "inicio" in l)


def test_sin_gestor_no_se_declara_el_de_sesiones(tmp_path, activadas):
	conversacion = ProveedorClaude(orden_base=[sys.executable, str(FAKE_CLAUDE)]).crear_conversacion(
		AjustesSesion(carpeta=tmp_path), lambda evento: None)
	conversacion._preparar_herramientas_ctrlia("Claude")  # noqa: SLF001
	assert conversacion._servidores_en_proceso() == ["ctrlia"]  # noqa: SLF001
	conversacion.cerrar()


def test_cerrar_la_conversacion_retira_sus_herramientas(mundo):
	conversacion = mundo.gestor.conversacion(mundo.yo)
	herramientas = conversacion.herramientas_sesiones
	assert herramientas.clave == mundo.yo and not herramientas.retirada
	mundo.gestor.cerrar_conversacion(mundo.yo)
	assert herramientas.retirada


# -- Interfaz ----------------------------------------------------------------------------


def test_la_ventana_pone_al_dia_la_lista(app_wx, tmp_path, monkeypatch, data_temporal):
	from app.core.configuracion import Configuracion
	from app.ui import avisos
	from app.ui.ventana_principal import VentanaPrincipal
	from tests.ayudas_wx import procesar_eventos

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"historial.ollama": False, "general.confirmar_salida": False, "general.abrir_ultima_sesion": False})
	marco = VentanaPrincipal(config, None, almacen=Almacen(tmp_path / "ventana.db"),
		proveedores={"claude": ProveedorClaude(orden_base=[sys.executable, str(FAKE_CLAUDE)])})
	try:
		procesar_eventos()
		yo = marco.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Yo"))
		otra = marco.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Otra"))
		marco._al_listar(marco.gestor.listar(), True)  # noqa: SLF001
		assert otra in {f.clave for f in marco._visibles}  # noqa: SLF001
		marco.gestor.ocultar(otra)
		marco._al_evento(yo, Evento(TipoEvento.AVISO, "Claude ha ocultado la sesión «Otra».",  # noqa: SLF001
			{"herramienta_ctrlia": "ocultar_sesion", "sesion": otra, "titulo": "Otra", "motivo": ""}))
		assert marco.fila_de(otra).oculta and otra not in {f.clave for f in marco._visibles}  # noqa: SLF001
		assert any("ha ocultado" in texto for texto in dichos)
		marco.gestor.ocultar(otra, False)
		marco._al_evento(yo, Evento(TipoEvento.AVISO, "Claude ha vuelto a mostrar la sesión «Otra».",  # noqa: SLF001
			{"herramienta_ctrlia": "mostrar_sesion", "sesion": otra, "titulo": "Otra"}))
		assert otra in {f.clave for f in marco._visibles}  # noqa: SLF001
	finally:
		marco.salir()
		procesar_eventos()
