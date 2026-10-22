"""Pruebas del agente Codex contra un ``codex app-server`` falso (sin red ni Codex real)."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from app.core.version import APP_VERSION
from app.services.agentes.base import (
	AjustesSesion,
	ClaseOpcion,
	Estado,
	Evento,
	RespuestaPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.agentes.codex import MAX_COLA, MAX_SALIDA, ProveedorCodex

FALSO = Path(__file__).resolve().parent / "fakes" / "fake_codex.py"
ESPERA = 15.0


class Recolector:
	"""Recibe los eventos (desde hilos de fondo) y permite esperar a uno."""

	def __init__(self) -> None:
		self.eventos: list[Evento] = []
		self._condicion = threading.Condition()

	def __call__(self, evento: Evento) -> None:
		with self._condicion:
			self.eventos.append(evento)
			self._condicion.notify_all()

	def de_tipo(self, tipo: TipoEvento) -> list[Evento]:
		with self._condicion:
			return [evento for evento in self.eventos if evento.tipo is tipo]

	def esperar(self, tipo: TipoEvento, cuantos: int = 1, tiempo: float = ESPERA) -> list[Evento]:
		limite = time.monotonic() + tiempo
		with self._condicion:
			while True:
				encontrados = [evento for evento in self.eventos if evento.tipo is tipo]
				if len(encontrados) >= cuantos:
					return encontrados
				restante = limite - time.monotonic()
				if restante <= 0:
					tipos = [evento.tipo.value for evento in self.eventos]
					raise AssertionError("No llegó %s x%d; eventos: %s" % (tipo, cuantos, tipos))
				self._condicion.wait(restante)

	def estados(self) -> list[Estado]:
		return [evento.datos["estado"] for evento in self.de_tipo(TipoEvento.ESTADO)]


def leer_registro(ruta: Path) -> list[dict]:
	if not ruta.exists():
		return []
	return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]


def peticiones(ruta: Path, metodo: str) -> list[dict]:
	return [m for m in leer_registro(ruta) if m.get("method") == metodo and "id" in m]


def esperar_registro(ruta: Path, metodo: str, cuantos: int = 1, tiempo: float = ESPERA) -> list[dict]:
	limite = time.monotonic() + tiempo
	while time.monotonic() < limite:
		encontradas = peticiones(ruta, metodo)
		if len(encontradas) >= cuantos:
			return encontradas
		time.sleep(0.02)
	raise AssertionError("El servidor falso no recibió %s" % metodo)


def esperar_estado(conversacion, estado: Estado, tiempo: float = ESPERA) -> None:
	"""El cambio de estado se emite justo después del evento que lo provoca."""
	limite = time.monotonic() + tiempo
	while conversacion.estado is not estado:
		assert time.monotonic() < limite, "La conversación sigue en %s" % conversacion.estado
		time.sleep(0.01)


@pytest.fixture
def entorno(tmp_path):
	"""Fábrica de (proveedor, conversación, recolector, registro) que limpia al final."""
	creados: list = []

	def crear(escenario: str = "normal", **ajustes):
		registro = tmp_path / ("registro-%s-%d.jsonl" % (escenario, len(creados)))
		proveedor = ProveedorCodex(orden=[sys.executable, str(FALSO), escenario, str(registro)])
		recolector = Recolector()
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, **ajustes), recolector)
		creados.append((proveedor, conversacion))
		return proveedor, conversacion, recolector, registro

	yield crear
	for proveedor, conversacion in creados:
		conversacion.cerrar()
		proveedor.cerrar()


def test_saludo_y_turno_completo(entorno, tmp_path):
	proveedor, conversacion, eventos, registro = entorno("normal", titulo="Mi prueba")
	conversacion.enviar("hola")
	fin = eventos.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["correcto"] is True and fin.datos["interrumpido"] is False

	iniciado = eventos.de_tipo(TipoEvento.INICIADO)[0]
	assert iniciado.datos["id_externo"] == "hilo-1"
	assert iniciado.datos["modelo"] == "gpt-falso"
	assert conversacion.id_externo == "hilo-1"
	assert "".join(e.texto for e in eventos.de_tipo(TipoEvento.TEXTO_PARCIAL)) == "Eco: hola"
	mensaje = eventos.de_tipo(TipoEvento.MENSAJE)[0]
	assert mensaje.texto == "Eco: hola" and mensaje.datos["id"] == "msg-1"
	assert eventos.de_tipo(TipoEvento.PLAN)[0].datos["pasos"] == [("Leer", "hecho"), ("Responder", "en curso")]
	assert eventos.de_tipo(TipoEvento.RAZONAMIENTO)[0].texto == "Pienso en ello"
	archivos = eventos.de_tipo(TipoEvento.ARCHIVO)
	assert [(a.datos["ruta"], a.datos["tipo"]) for a in archivos] == [("a.txt", "nuevo"), ("b.txt", "modificado")]
	assert archivos[0].datos["diff"] == "+hola"
	uso = eventos.de_tipo(TipoEvento.USO)[0].datos
	assert (uso["entrada"], uso["salida"], uso["contexto_usado"], uso["contexto_total"]) == (100, 20, 60, 1000)
	esperar_estado(conversacion, Estado.INACTIVA)
	assert eventos.estados() == [Estado.TRABAJANDO, Estado.INACTIVA]
	assert not eventos.de_tipo(TipoEvento.HERRAMIENTA)  # el outputDelta se ignora

	mensajes = leer_registro(registro)
	assert all("jsonrpc" not in m for m in mensajes)
	inicio = mensajes[0]
	assert inicio["method"] == "initialize"
	assert inicio["params"]["clientInfo"] == {"name": "ctrl_ia", "title": "Ctrl+IA", "version": APP_VERSION}
	assert inicio["params"]["capabilities"] == {"experimentalApi": True}
	assert mensajes[1] == {"method": "initialized", "params": {}}
	arranque = peticiones(registro, "thread/start")[0]["params"]
	assert arranque["cwd"] == str(tmp_path)
	assert arranque["sandbox"] == "workspace-write"
	assert arranque["approvalPolicy"] == "on-request"
	assert arranque["approvalsReviewer"] == "user"
	assert peticiones(registro, "thread/name/set")[0]["params"] == {"threadId": "hilo-1", "name": "Mi prueba"}
	turno = peticiones(registro, "turn/start")[0]["params"]
	assert turno["threadId"] == "hilo-1"
	assert turno["input"] == [{"type": "text", "text": "hola"}]
	assert "sandboxPolicy" not in turno


@pytest.mark.parametrize(("opcion", "error"), [("accept", False), ("decline", True)])
def test_aprobacion_de_orden(entorno, opcion, error):
	_proveedor, conversacion, eventos, _registro = entorno("aprobacion")
	conversacion.enviar("lista los archivos")
	solicitud = eventos.esperar(TipoEvento.PERMISO)[0].datos["solicitud"]
	assert solicitud.tipo is TipoSolicitud.HERRAMIENTA
	assert solicitud.titulo == "Codex quiere ejecutar una orden"
	assert "dir" in solicitud.detalle and "Necesito ver los archivos" in solicitud.detalle
	# El «durante esta sesión» de Codex no se ofrece en órdenes: sigue valiendo aunque cambies de modo (AIChat #298).
	assert [(o.id, o.clase) for o in solicitud.opciones] == [
		("accept", ClaseOpcion.PERMITIR),
		("decline", ClaseOpcion.DENEGAR),
		("cancel", ClaseOpcion.CANCELAR),
	]
	assert "fuera del aislamiento" in solicitud.detalle
	assert solicitud.opcion_segura().id == "decline"
	herramienta = eventos.de_tipo(TipoEvento.HERRAMIENTA)[0]
	assert herramienta.datos["detalle"] == "Ejecutar: dir"
	esperar_estado(conversacion, Estado.ESPERANDO_USUARIO)

	conversacion.responder(solicitud.id, RespuestaPermiso(opcion))
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert eventos.de_tipo(TipoEvento.MENSAJE)[0].texto == "Decisión: %s" % opcion
	fin_herramienta = eventos.de_tipo(TipoEvento.HERRAMIENTA_FIN)[0]
	assert fin_herramienta.datos["error"] is error
	if not error:
		assert len(fin_herramienta.datos["salida"]) < MAX_SALIDA + 100
		assert "recortada" in fin_herramienta.datos["salida"]
	# Ya contestada: el «resolved» posterior no se anuncia como retirada.
	assert not eventos.de_tipo(TipoEvento.PERMISO_RETIRADO)
	esperar_estado(conversacion, Estado.INACTIVA)
	assert eventos.estados() == [Estado.TRABAJANDO, Estado.ESPERANDO_USUARIO, Estado.TRABAJANDO, Estado.INACTIVA]


def test_pregunta_al_usuario(entorno):
	_proveedor, conversacion, eventos, _registro = entorno("pregunta")
	conversacion.enviar("elige")
	solicitud = eventos.esperar(TipoEvento.PERMISO)[0].datos["solicitud"]
	assert solicitud.tipo is TipoSolicitud.PREGUNTA
	pregunta = solicitud.preguntas[0]
	assert pregunta.id == "color" and pregunta.encabezado == "Color"
	assert pregunta.texto == "¿Qué color prefieres?"
	assert pregunta.opciones == [("Rojo", "Cálido"), ("Azul", "Frío")]
	assert pregunta.admite_otra is True
	conversacion.responder(solicitud.id, RespuestaPermiso("responder", respuestas={"color": ["Azul"]}))
	eventos.esperar(TipoEvento.FIN_TURNO)
	texto = eventos.de_tipo(TipoEvento.MENSAJE)[0].texto
	assert texto == 'Respuestas: {"answers": {"color": {"answers": ["Azul"]}}}'


def test_interrupcion_conserva_la_cola(entorno):
	_proveedor, conversacion, eventos, registro = entorno("interrupcion")
	conversacion.enviar("uno")
	conversacion.enviar("dos")
	assert eventos.de_tipo(TipoEvento.AVISO)[0].datos["en_cola"] == 1
	esperar_registro(registro, "turn/start")
	time.sleep(0.2)
	conversacion.detener()
	fin = eventos.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["interrumpido"] is True and fin.datos["correcto"] is False
	assert peticiones(registro, "turn/interrupt")[0]["params"] == {"threadId": "hilo-1", "turnId": "turno-1"}
	time.sleep(0.2)
	assert len(peticiones(registro, "turn/start")) == 1  # lo encolado NO se envía solo
	assert conversacion.pendientes() == ["dos"]
	assert conversacion.pendientes() == []
	esperar_estado(conversacion, Estado.INACTIVA)


def test_detener_antes_de_conocer_el_turno(entorno):
	_proveedor, conversacion, eventos, registro = entorno("interrupcion")
	conversacion.enviar("uno")
	conversacion.detener()  # aún sin id de turno: se interrumpe en cuanto se conozca
	fin = eventos.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["interrumpido"] is True
	esperar_estado(conversacion, Estado.INACTIVA)


def test_detener_retira_el_permiso_pendiente(entorno):
	_proveedor, conversacion, eventos, _registro = entorno("aprobacion_detener")
	conversacion.enviar("lista")
	solicitud = eventos.esperar(TipoEvento.PERMISO)[0].datos["solicitud"]
	conversacion.detener()
	retirado = eventos.esperar(TipoEvento.PERMISO_RETIRADO)[0]
	assert retirado.datos["id"] == solicitud.id
	assert eventos.esperar(TipoEvento.FIN_TURNO)[0].datos["interrumpido"] is True


def test_cola_de_mensajes_en_orden(entorno):
	_proveedor, conversacion, eventos, registro = entorno("lento")
	for texto in ("uno", "dos", "tres"):
		conversacion.enviar(texto)
	eventos.esperar(TipoEvento.FIN_TURNO, 3)
	assert [e.texto for e in eventos.de_tipo(TipoEvento.MENSAJE)] == ["Eco: uno", "Eco: dos", "Eco: tres"]
	assert [p["params"]["input"][0]["text"] for p in peticiones(registro, "turn/start")] == ["uno", "dos", "tres"]
	esperar_estado(conversacion, Estado.INACTIVA)
	assert eventos.estados() == [Estado.TRABAJANDO, Estado.INACTIVA]
	assert len(peticiones(registro, "thread/start")) == 1


def test_cola_limitada(entorno):
	_proveedor, conversacion, eventos, _registro = entorno("interrupcion")
	conversacion.enviar("primero")
	for numero in range(MAX_COLA + 1):
		conversacion.enviar("m%d" % numero)
	assert "llena" in eventos.de_tipo(TipoEvento.AVISO)[-1].texto
	assert conversacion.pendientes() == ["m%d" % n for n in range(MAX_COLA)]
	conversacion.detener()
	eventos.esperar(TipoEvento.FIN_TURNO)


def test_anadir_al_turno_usa_steer_con_el_turno_exacto(entorno):
	proveedor, conversacion, eventos, registro = entorno("interrupcion")
	assert proveedor.capacidades.anadir_al_turno is True
	conversacion.enviar("uno")
	esperar_registro(registro, "turn/start")
	time.sleep(0.2)
	conversacion.anadir_al_turno("y esto también")
	mensaje = eventos.esperar(TipoEvento.MENSAJE)[0]
	assert mensaje.texto == "Corrección: y esto también"
	corregir = peticiones(registro, "turn/steer")[0]["params"]
	assert corregir == {"threadId": "hilo-1", "input": [{"type": "text", "text": "y esto también"}], "expectedTurnId": "turno-1"}
	aceptado = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("anadido")]
	limite = time.monotonic() + ESPERA
	while not aceptado and time.monotonic() < limite:
		time.sleep(0.02)
		aceptado = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("anadido")]
	assert aceptado and conversacion.anadidos_pendientes == 0
	assert not eventos.de_tipo(TipoEvento.FIN_TURNO)  # el turno sigue: no se ha detenido
	conversacion.detener()
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert len(peticiones(registro, "turn/start")) == 1


def test_anadir_al_turno_sin_turno_devuelve_el_texto(entorno):
	_proveedor, conversacion, eventos, registro = entorno("normal")
	conversacion.anadir_al_turno("corrige esto")
	aviso = eventos.esperar(TipoEvento.AVISO)[0]
	assert aviso.datos["devolver_texto"] == "corrige esto"
	assert "no se ha enviado" in aviso.texto
	time.sleep(0.2)
	assert not peticiones(registro, "turn/start")  # nunca se convierte en un turno nuevo
	assert not peticiones(registro, "turn/steer")


def test_anadir_al_turno_rechazado_devuelve_el_texto(entorno):
	_proveedor, conversacion, eventos, registro = entorno("interrupcion")
	conversacion.enviar("uno")
	esperar_registro(registro, "turn/start")
	time.sleep(0.2)
	# El turno acaba justo antes de que llegue la corrección: el servidor la rechaza.
	conversacion._turno_id = "turno-viejo"  # noqa: SLF001 - se simula la carrera con el fin del turno
	conversacion.anadir_al_turno("demasiado tarde")
	limite = time.monotonic() + ESPERA
	devueltos: list = []
	while not devueltos and time.monotonic() < limite:
		time.sleep(0.02)
		devueltos = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("devolver_texto")]
	assert devueltos and devueltos[0].datos["devolver_texto"] == "demasiado tarde"
	assert conversacion.anadidos_pendientes == 0
	assert len(peticiones(registro, "turn/start")) == 1
	assert conversacion.pendientes() == []  # tampoco se cuela en la cola
	conversacion._turno_id = "turno-1"  # noqa: SLF001
	conversacion.detener()
	eventos.esperar(TipoEvento.FIN_TURNO)


def test_enviar_ahora_detiene_y_envia(entorno):
	_proveedor, conversacion, eventos, registro = entorno("interrupcion")
	conversacion.enviar("uno")
	conversacion.enviar("en cola")
	esperar_registro(registro, "turn/start")
	time.sleep(0.2)
	conversacion.enviar_ahora("para y haz esto")
	esperar_registro(registro, "turn/interrupt")
	arranques = esperar_registro(registro, "turn/start", 2)
	assert arranques[1]["params"]["input"][0]["text"] == "para y haz esto"
	assert not peticiones(registro, "turn/steer")
	assert conversacion.pendientes() == ["en cola"]  # lo que ya estaba en cola sigue esperando
	conversacion.detener()
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	time.sleep(0.2)
	assert len(peticiones(registro, "turn/start")) == 2


def test_permitir_orden_durante_la_sesion_lo_recuerda_ctrl_ia(entorno, tmp_path):
	(tmp_path / ".git").mkdir()  # las reglas de git solo valen en la raíz de un repositorio (AIChat #333)
	_proveedor, conversacion, eventos, registro = entorno("regla")
	conversacion.enviar("git commit -m primero")
	solicitud = eventos.esperar(TipoEvento.PERMISO)[0].datos["solicitud"]
	ids = [o.id for o in solicitud.opciones]
	assert "acceptForSession" not in ids  # el de Codex no se puede revocar: no se ofrece
	propia = next(o for o in solicitud.opciones if o.id == "ctrlia_permitir_sesion")
	assert propia.clase is ClaseOpcion.PERMITIR_SESION
	assert "«git commit» con cualquier argumento" in propia.etiqueta
	assert solicitud.opcion_segura().id == "decline"
	conversacion.responder(solicitud.id, RespuestaPermiso("ctrlia_permitir_sesion"))
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto == "Decisión: accept"  # a Codex, «una vez»
	assert conversacion.ajustes.reglas_permitidas == ["Orden(git commit)"]
	esperar_estado(conversacion, Estado.INACTIVA)

	# Segunda vez: Ctrl+IA contesta solo y lo dice.
	conversacion.enviar("git commit -m segundo")
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	assert len(eventos.de_tipo(TipoEvento.PERMISO)) == 1
	assert eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto == "Decisión: accept"
	aviso = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("permitido_por_regla")]
	assert aviso and "git commit -m segundo" in aviso[0].texto
	esperar_estado(conversacion, Estado.INACTIVA)

	# Algo compuesto con el mismo prefijo vuelve a preguntar.
	conversacion.enviar("git commit -m x && del todo")
	assert len(eventos.esperar(TipoEvento.PERMISO, 2)) == 2
	compuesta = eventos.de_tipo(TipoEvento.PERMISO)[-1].datos["solicitud"]
	assert "ctrlia_permitir_sesion" not in [o.id for o in compuesta.opciones]
	conversacion.responder(compuesta.id, RespuestaPermiso("decline"))
	eventos.esperar(TipoEvento.FIN_TURNO, 3)
	esperar_estado(conversacion, Estado.INACTIVA)

	# Quitar la regla surte efecto en la petición siguiente.
	conversacion.ajustes.reglas_permitidas.clear()
	conversacion.enviar("git commit -m tercero")
	assert len(eventos.esperar(TipoEvento.PERMISO, 3)) == 3
	ultima = eventos.de_tipo(TipoEvento.PERMISO)[-1].datos["solicitud"]
	conversacion.responder(ultima.id, RespuestaPermiso("decline"))
	eventos.esperar(TipoEvento.FIN_TURNO, 4)
	assert not (Path.home() / ".codex" / "ctrlia").exists()


def test_reglas_de_orden_rechazan_lo_dudoso(tmp_path):
	from app.services.agentes.codex import palabras_orden, prefijo_de_regla, prefijo_permitible, regla_de_orden

	assert palabras_orden("git status --short") == ["git", "status", "--short"]
	for compuesta in ("git add . && git commit", "git log | more", "git log > a.txt", "echo $(rm x)", "git a; del b",
			"git push `x`", "dir %USERPROFILE%", "git a\ngit b", "git a || b", "echo $HOME"):
		assert palabras_orden(compuesta) is None, compuesta
	for interprete in (["bash"], ["python", "x.py"], ["pwsh"], ["C:\\Windows\\System32\\cmd.exe"], ["node"], ["sudo", "git"],
			["python3.12"], ["env"], ["powershell.exe", "-c"]):
		assert not prefijo_permitible(interprete), interprete
	assert prefijo_permitible(["git", "switch"])
	assert prefijo_de_regla(regla_de_orden(["git", "switch"])) == ["git", "switch"]
	assert prefijo_de_regla("Bash(git push)") is None


def test_regla_no_cubre_otra_carpeta_ni_necesidades_extra(entorno, tmp_path):
	_proveedor, conversacion, _eventos, _registro = entorno("normal")
	conversacion.ajustes.reglas_permitidas.append("Orden(git commit)")
	(tmp_path / ".git").mkdir()
	base = {"command": "git commit -m x", "cwd": str(tmp_path)}
	assert conversacion._regla_que_permite(base) == "Orden(git commit)"  # noqa: SLF001
	# Solo en la carpeta raíz de la sesión, no en una subcarpeta (AIChat #333).
	assert not conversacion._regla_que_permite(dict(base, cwd=str(tmp_path / "sub")))  # noqa: SLF001
	assert not conversacion._regla_que_permite(dict(base, cwd=str(tmp_path.parent)))  # noqa: SLF001
	assert not conversacion._regla_que_permite({"command": "git commit -m x"})  # noqa: SLF001 - sin carpeta, se pregunta
	assert not conversacion._regla_que_permite(dict(base, networkApprovalContext={"host": "x.com"}))  # noqa: SLF001
	assert not conversacion._regla_que_permite(dict(base, additionalPermissions={"network": {}}))  # noqa: SLF001
	assert not conversacion._regla_que_permite(dict(base, kind="writeStdin"))  # noqa: SLF001
	assert not conversacion._regla_que_permite(dict(base, availableDecisions=["decline"]))  # noqa: SLF001
	assert not conversacion._regla_que_permite(dict(base, command="git commitx"))  # noqa: SLF001


def test_proceso_que_muere_y_se_relanza(entorno):
	_proveedor, conversacion, eventos, registro = entorno("muere")
	conversacion.enviar("uno")
	error = eventos.esperar(TipoEvento.ERROR)[0]
	assert "cerrado de forma inesperada" in error.texto and "código 3" in error.texto
	assert "fallo simulado" in error.texto
	fin = eventos.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["correcto"] is False
	time.sleep(0.1)
	esperar_estado(conversacion, Estado.ERROR)
	assert len(peticiones(registro, "turn/start")) == 1  # no se reintenta solo

	conversacion.enviar("dos")
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	assert eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto == "Eco: dos"
	assert len(peticiones(registro, "initialize")) == 2
	assert peticiones(registro, "thread/resume")[0]["params"]["threadId"] == "hilo-1"
	assert eventos.de_tipo(TipoEvento.INICIADO)[-1].datos["retomada"] is True
	assert len(eventos.de_tipo(TipoEvento.ERROR)) == 1


def test_respuestas_desordenadas(entorno):
	proveedor, _conversacion, _eventos, _registro = entorno("desordenado")
	servidor = proveedor.servidor()
	resultados: dict[str, object] = {}

	def pedir(metodo: str) -> None:
		resultados[metodo] = servidor.peticion(metodo, {})

	hilos = [threading.Thread(target=pedir, args=(m,)) for m in ("model/list", "account/read")]
	for hilo in hilos:
		hilo.start()
	for hilo in hilos:
		hilo.join(ESPERA)
	assert resultados["model/list"]["data"][0]["model"] == "gpt-falso"
	assert resultados["account/read"]["account"]["email"] == "persona@example.com"


def test_entorno_sin_claves_de_api(entorno, monkeypatch):
	for variable in ("OPENAI_API_KEY", "CODEX_API_KEY", "AZURE_OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_API_KEY"):
		monkeypatch.setenv(variable, "secreto")
	monkeypatch.setenv("CTRLIA_PRUEBA", "1")
	proveedor, _conversacion, _eventos, _registro = entorno("normal")
	modelos = proveedor.listar_modelos()
	assert modelos[0].id == ""
	assert [m.id for m in modelos[1:]] == ["gpt-falso"]  # el oculto no aparece
	assert modelos[1].ayuda == "fugas=;prueba=1"


def test_turno_fallido_en_espanol(entorno):
	_proveedor, conversacion, eventos, _registro = entorno("fallo")
	conversacion.enviar("hola")
	fin = eventos.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["correcto"] is False
	errores = eventos.de_tipo(TipoEvento.ERROR)
	assert len(errores) == 1
	assert "límite de uso" in errores[0].texto
	assert all("xxxxxxxxxx" not in e.texto for e in eventos.eventos)
	esperar_estado(conversacion, Estado.INACTIVA)


def test_bifurcar_y_cambiar_modo(entorno):
	_proveedor, conversacion, eventos, registro = entorno("normal", bifurcar_de="hilo-1", modo_permiso="sin_preguntas")
	conversacion.enviar("uno")
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert eventos.de_tipo(TipoEvento.INICIADO)[0].datos["id_externo"] == "hilo-bifurcado"
	bifurcar = peticiones(registro, "thread/fork")[0]["params"]
	assert bifurcar["threadId"] == "hilo-1" and bifurcar["approvalPolicy"] == "never"
	conversacion.cambiar_ajustes(modo_permiso="solo_lectura", modelo="gpt-otro", esfuerzo="high")
	conversacion.enviar("dos")
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	segundo = peticiones(registro, "turn/start")[1]["params"]
	assert segundo["sandboxPolicy"] == {"type": "readOnly"}
	assert segundo["approvalPolicy"] == "on-request"
	assert segundo["model"] == "gpt-otro" and segundo["effort"] == "high"


def test_historial(entorno):
	proveedor, _conversacion, _eventos, _registro = entorno("normal")
	hilos = proveedor.listar_hilos()
	assert [h["id"] for h in hilos] == ["hilo-1", "hilo-viejo"]
	assert hilos[0]["titulo"] == "Primera línea"
	assert hilos[0]["carpeta"] == "C:\\proyecto"
	assert (hilos[0]["creado"], hilos[0]["actualizado"]) == (100, 200)
	assert hilos[1]["titulo"] == "Viejo" and hilos[1]["origen"] == "subAgent" and hilos[1]["estado"] == "guardada"
	mensajes = proveedor.leer_hilo("hilo-1")
	assert [m["rol"] for m in mensajes] == ["usuario", "herramienta", "asistente"]
	assert mensajes[0]["texto"] == "Hola\n[Imagen: C:\\i.png]" and mensajes[0]["fecha"] == 10
	assert mensajes[1]["texto"].startswith("Ejecutar: dir")
	assert mensajes[2] == {"rol": "asistente", "texto": "Adiós", "fecha": 20}


def test_detectar(entorno):
	proveedor, _conversacion, _eventos, _registro = entorno("normal")
	estado = proveedor.detectar()
	assert estado.disponible is True
	assert estado.version == "0.0.0-falso"
	assert estado.sesion_iniciada is True
	assert estado.cuenta == "persona@example.com (plan plus)"


def test_listas_del_proveedor():
	proveedor = ProveedorCodex(orden=["inexistente"])
	assert [m.id for m in proveedor.modos_permiso()] == ["solo_lectura", "espacio_trabajo", "sin_preguntas", "acceso_total"]
	assert "peligro" in proveedor.modos_permiso()[3].etiqueta.lower()
	assert [(e.id, e.etiqueta) for e in proveedor.esfuerzos()[1:]] == [
		("low", "Bajo"), ("medium", "Medio"), ("high", "Alto"), ("xhigh", "Muy alto"),
	]
	assert proveedor.modelos()[0].id == ""
	assert proveedor.capacidades.leer_historial_externo is True
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=Path(".")), Recolector())
	assert conversacion.ajustes.modo_permiso == "espacio_trabajo"


def test_programa_inexistente_da_error_claro(tmp_path):
	proveedor = ProveedorCodex(orden=[str(tmp_path / "no-existe.exe")])
	eventos = Recolector()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos)
	conversacion.enviar("hola")
	error = eventos.esperar(TipoEvento.ERROR)[0]
	assert "No se pudo" in error.texto
	eventos.esperar(TipoEvento.FIN_TURNO)
	esperar_estado(conversacion, Estado.ERROR)
	conversacion.cerrar()


def test_respuestas_de_otras_peticiones():
	"""Formas exactas de las respuestas que exige el esquema de Codex."""
	from app.services.agentes import codex

	permisos = {"fileSystem": {"write": ["C:\\otra"]}, "network": {"enabled": True}}
	params = {"threadId": "h", "turnId": "t", "itemId": "i", "cwd": "C:\\p", "permissions": permisos, "startedAtMs": 1}
	solicitud = codex._solicitud_permisos(7, params)
	assert "Escribir: C:\\otra" in solicitud.detalle and "Usar internet" in solicitud.detalle
	pendiente = codex._Pendiente(7, "item/permissions/requestApproval", params, solicitud)
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("session")) == {"permissions": permisos, "scope": "session"}
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("deny")) == {"permissions": {}}
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("inventada")) == {"permissions": {}}

	legado = {"conversationId": "h", "callId": "c", "command": ["dir"], "cwd": "C:\\p", "parsedCmd": []}
	solicitud = codex._solicitud_legado(8, "execCommandApproval", legado)
	pendiente = codex._Pendiente(8, "execCommandApproval", legado, solicitud)
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("accept")) == {"decision": "approved"}
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("cancel")) == {"decision": "abort"}
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("decline"))["decision"]["denied"]["rejection"]

	formulario = {
		"threadId": "h", "serverName": "github", "message": "Datos", "mode": "form",
		"requestedSchema": {"type": "object", "properties": {
			"nombre": {"type": "string", "title": "Nombre"},
			"edad": {"type": "integer"},
			"acepto": {"type": "boolean"},
			"color": {"type": "string", "oneOf": [{"const": "r", "title": "Rojo"}, {"const": "a", "title": "Azul"}]},
		}},
	}
	solicitud = codex._solicitud_mcp(9, formulario)
	assert solicitud.tipo is TipoSolicitud.PREGUNTA
	assert [p.id for p in solicitud.preguntas] == ["nombre", "edad", "acepto", "color"]
	pendiente = codex._Pendiente(9, "mcpServer/elicitation/request", formulario, solicitud)
	respuesta = RespuestaPermiso("accept", respuestas={"nombre": ["Ana"], "edad": ["30"], "acepto": ["Sí"], "color": ["Azul"]})
	assert codex._resultado_peticion(pendiente, respuesta) == {
		"action": "accept", "content": {"nombre": "Ana", "edad": 30, "acepto": True, "color": "a"},
	}
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("decline")) == {"action": "decline"}
	# Los formularios «openai/form» de Codex se rellenan igual.
	assert codex._solicitud_mcp(10, dict(formulario, mode="openai/form")).tipo is TipoSolicitud.PREGUNTA
