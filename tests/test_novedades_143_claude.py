"""Pruebas de las incidencias de AIChat hechas para Claude Code en la 1.4.3.

- #238: añadir un mensaje al turno en marcha sin detenerlo.
- #83: «Enviar ahora» mientras Claude Code aún no tiene el primer mensaje va a la cola.
- #233: lo que Claude programa (ScheduleWakeup, CronCreate) no se pierde al cerrar por inactividad.
- #319: «Dejar que Claude use Chrome» (--chrome).
- #235: estilo de las respuestas por sesión (--settings con outputStyle).
- #276: la vista previa de las opciones de una pregunta.

Nada se muestra ni habla.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services.agentes.base import AjustesSesion, Pregunta, RespuestaPermiso, SolicitudPermiso, TipoEvento, TipoSolicitud
from app.services.agentes.base import ClaseOpcion, OpcionPermiso
from app.services.agentes.claude import ProveedorClaude, _ProcesoClaude

FALSO = Path(__file__).parent / "fakes" / "fake_claude.py"


class Recolector:
	def __init__(self) -> None:
		self.eventos = []
		self._condicion = threading.Condition()

	def __call__(self, evento) -> None:
		with self._condicion:
			self.eventos.append(evento)
			self._condicion.notify_all()

	def de_tipo(self, tipo):
		with self._condicion:
			return [e for e in self.eventos if e.tipo is tipo]

	def esperar(self, condicion, segundos: float = 20.0) -> bool:
		limite = time.monotonic() + segundos
		with self._condicion:
			while not condicion():
				restante = limite - time.monotonic()
				if restante <= 0:
					return False
				self._condicion.wait(restante)
		return True


def _lineas(registro: Path) -> list[dict]:
	if not registro.exists():
		return []
	return [json.loads(linea) for linea in registro.read_text(encoding="utf-8").splitlines()]


def _esperar_registro(registro: Path, clave: str, segundos: float = 15.0) -> bool:
	limite = time.monotonic() + segundos
	while time.monotonic() < limite:
		if any(clave in linea for linea in _lineas(registro)):
			return True
		time.sleep(0.05)
	return False


@pytest.fixture
def claude(tmp_path, monkeypatch):
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro))
	creadas = []

	def crear(modo: str, **opciones):
		monkeypatch.setenv("FAKE_CLAUDE_MODO", modo)
		recolector = Recolector()
		proveedor = ProveedorClaude(orden_base=[sys.executable, str(FALSO)], **opciones)
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), recolector)
		creadas.append(conversacion)
		return conversacion, recolector

	yield crear, registro
	for conversacion in creadas:
		conversacion.cerrar()


def test_anadir_al_turno_en_marcha(claude):
	crear, registro = claude
	conversacion, eventos = crear("anadir")
	conversacion.enviar("haz algo largo")
	assert _esperar_registro(registro, "esperando_anadido")
	conversacion.anadir_al_turno("y mira también el README")
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	assert eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto == "recibido:y mira también el README"
	# Un solo turno: no se contó como un mensaje aparte.
	assert len(eventos.de_tipo(TipoEvento.FIN_TURNO)) == 1


def test_anadir_al_turno_sin_turno_devuelve_el_texto(claude):
	crear, _registro = claude
	conversacion, eventos = crear("normal")
	conversacion.anadir_al_turno("no hay turno")
	devueltos = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("devolver_texto")]
	assert devueltos and devueltos[0].datos["devolver_texto"] == "no hay turno"


def test_enviar_ahora_antes_del_primer_mensaje_va_a_la_cola(claude):
	crear, _registro = claude
	conversacion, eventos = crear("normal")
	with conversacion._candado:  # noqa: SLF001 - se para justo antes de escribir el primer mensaje
		conversacion.enviar("primero")
		turno = conversacion._turno
		turno.mensaje_escrito = False
		conversacion.enviar_ahora("segundo")
		assert [t for t, _a in conversacion._cola] == ["segundo"]
		assert turno.interrupciones == 0
	assert any(e.datos.get("en_cola") for e in eventos.de_tipo(TipoEvento.AVISO))


def test_lo_programado_no_se_pierde_al_cerrar_por_inactividad(tmp_path):
	proveedor = ProveedorClaude(orden_base=["claude"], minutos_abierto=15)
	eventos = Recolector()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos)
	vivo = _ProcesoClaude()
	turno = type("Turno", (), {"vivo": vivo})()
	conversacion._al_programar(turno, "ScheduleWakeup", {"delaySeconds": 600})
	assert vivo.tiene_programado(time.time())
	assert "dentro de 10 minutos" in eventos.de_tipo(TipoEvento.AVISO)[-1].texto
	assert not vivo.tiene_programado(time.time() + 600 + 130)
	conversacion._al_programar(turno, "ScheduleWakeup", {"stop": True})
	assert not vivo.tiene_programado(time.time())
	conversacion._al_programar(turno, "CronCreate", {"cron": "*/5 * * * *"})
	assert vivo.tiene_programado(time.time() + 10 ** 6)
	conversacion._al_programar(turno, "CronDelete", {"id": "x"})
	assert not vivo.tiene_programado(time.time())
	# Con «arrancarlo en cada mensaje», se avisa de que no llegará a hacerse.
	cada_mensaje = ProveedorClaude(orden_base=["claude"], minutos_abierto=0).crear_conversacion(
		AjustesSesion(carpeta=tmp_path), eventos)
	cada_mensaje._al_programar(turno, "ScheduleWakeup", {"delaySeconds": 60})
	assert "No llegará a hacerse" in eventos.de_tipo(TipoEvento.AVISO)[-1].texto


def test_chrome_y_estilo_van_en_la_orden_y_en_la_firma(tmp_path):
	sin = ProveedorClaude(orden_base=["claude"]).crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda e: None)
	assert "--chrome" not in sin.construir_orden() and "--settings" not in sin.construir_orden()
	ajustes = AjustesSesion(carpeta=tmp_path, extra={"estilo": "Concise"})
	con = ProveedorClaude(orden_base=["claude"], chrome=True).crear_conversacion(ajustes, lambda e: None)
	orden = con.construir_orden()
	assert "--chrome" in orden
	assert json.loads(orden[orden.index("--settings") + 1]) == {"outputStyle": "Concise"}
	assert con._firma(True, True) != sin._firma(True, True)
	# Un estilo con caracteres raros no llega a la orden; en otro equipo, Chrome tampoco.
	raro = ProveedorClaude(orden_base=["claude"]).crear_conversacion(
		AjustesSesion(carpeta=tmp_path, extra={"estilo": "x\"; rm"}), lambda e: None)
	assert "--settings" not in raro.construir_orden()


def test_estilos_de_claude(tmp_path):
	proveedor = ProveedorClaude(orden_base=["claude"])
	assert [e.id for e in proveedor.estilos()] == ["", "Concise", "Proactive", "Explanatory", "Learning"]
	proveedor._estilos = ["default", "Concise", "Mio"]
	estilos = proveedor.estilos()
	assert [e.id for e in estilos] == ["", "Concise", "Mio"]
	assert estilos[1].etiqueta == "Conciso"


def test_pregunta_con_vista_previa(tmp_path):
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda e: None)
	solicitud = conversacion._solicitud_pregunta("p", {"questions": [{"question": "¿Lo publico?", "header": "Publicar",
		"options": [{"label": "Sí", "description": "Tal cual", "preview": "Primer post: hola."}, {"label": "No"}]}]})
	assert solicitud.preguntas[0].vistas_previas == ["Primer post: hola.", ""]
	sin = conversacion._solicitud_pregunta("q", {"questions": [{"question": "¿Color?", "options": [{"label": "Rojo"}]}]})
	assert sin.preguntas[0].vistas_previas == []


def test_dialogo_muestra_la_vista_previa(app_wx):
	from app.ui.dialogos.solicitud import DialogoSolicitud

	solicitud = SolicitudPermiso("p1", TipoSolicitud.PREGUNTA, "Claude te hace una pregunta",
		preguntas=[Pregunta("q", "¿Lo publico?", "Publicar", [("Publicar", ""), ("Cambiarlo", "")],
			vistas_previas=["Primer post: hola.", ""])],
		opciones=[OpcionPermiso("responder", "Responder", ClaseOpcion.PERMITIR),
			OpcionPermiso("no_responder", "No responder", ClaseOpcion.DENEGAR)])
	dialogo = DialogoSolicitud(None, solicitud)
	try:
		campo = dialogo.descripciones[0]
		assert "Vista previa:" in campo.GetValue() and "Primer post: hola." in campo.GetValue()
	finally:
		dialogo.Destroy()

