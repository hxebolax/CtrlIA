"""Pruebas del adaptador de Claude Code con un CLI falso (sin red ni cuenta)."""

import json
import sys
import threading
from pathlib import Path

import pytest

from app.services.agentes.base import AjustesSesion, RespuestaPermiso, TipoEvento, TipoSolicitud
from app.services.agentes.claude import ProveedorClaude, _regla_para, describir_herramienta

FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"


class Recolector:
	def __init__(self):
		self.eventos = []
		self.fin = threading.Event()
		self.permiso = threading.Event()

	def __call__(self, evento):
		self.eventos.append(evento)
		if evento.tipo is TipoEvento.FIN_TURNO:
			self.fin.set()
		if evento.tipo is TipoEvento.PERMISO:
			self.permiso.set()

	def de_tipo(self, tipo):
		return [e for e in self.eventos if e.tipo is tipo]


@pytest.fixture
def entorno(tmp_path, monkeypatch):
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro))
	monkeypatch.setenv("CLAUDECODE", "1")
	monkeypatch.setenv("ANTHROPIC_API_KEY", "secreta")
	return registro


def _lineas(registro):
	return [json.loads(linea) for linea in registro.read_text(encoding="utf-8").splitlines()]


def _conversacion(tmp_path, recolector, **ajustes):
	proveedor = ProveedorClaude(orden_base=[sys.executable, str(FAKE)])
	return proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, **ajustes), recolector)


def test_turno_normal(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector, titulo="Prueba")
	conversacion.enviar("¿Qué tal?")
	assert recolector.fin.wait(20)
	mensajes = recolector.de_tipo(TipoEvento.MENSAJE)
	assert mensajes and mensajes[-1].texto == "Hola\u2028mundo"
	assert recolector.de_tipo(TipoEvento.TEXTO_PARCIAL)
	assert recolector.de_tipo(TipoEvento.HERRAMIENTA)[0].texto == "Leer archivo: C:/x.txt"
	assert recolector.de_tipo(TipoEvento.HERRAMIENTA_FIN)[0].datos["salida"] == "contenido"
	assert recolector.de_tipo(TipoEvento.COMANDOS)[0].datos["comandos"][0][0] == "/compact"
	assert recolector.de_tipo(TipoEvento.FIN_TURNO)[0].datos["correcto"]
	lineas = _lineas(entorno)
	assert lineas[0]["claudecode"] is False and lineas[0]["clave"] is False
	assert lineas[0]["estados"] == "1"
	assert "--session-id" in lineas[0]["argv"] and "--name" in lineas[0]["argv"]
	assert lineas[1]["mensaje"]["message"]["content"] == "¿Qué tal?"
	# El segundo mensaje va al mismo proceso, que sigue abierto (sin relanzar ni reconectar los MCP).
	recolector.fin.clear()
	conversacion.enviar("Otra cosa")
	assert recolector.fin.wait(20)
	lineas = _lineas(entorno)
	assert len([linea for linea in lineas if "argv" in linea]) == 1
	assert lineas[2]["mensaje"]["message"]["content"] == "Otra cosa" and lineas[2]["pid"] == lineas[1]["pid"]
	assert recolector.de_tipo(TipoEvento.MENSAJE)[-1].texto == "Hola mundo 2"
	assert len(recolector.de_tipo(TipoEvento.FIN_TURNO)) == 2
	conversacion.cerrar()


def _conversacion_con(tmp_path, recolector, minutos, **ajustes):
	proveedor = ProveedorClaude(orden_base=[sys.executable, str(FAKE)], minutos_abierto=minutos)
	return proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, **ajustes), recolector)


def _turno(conversacion, recolector, texto):
	recolector.fin.clear()
	conversacion.enviar(texto)
	assert recolector.fin.wait(20)


def test_un_proceso_por_mensaje_si_se_elige_no(tmp_path, entorno, monkeypatch):
	"""Con «No» en Preferencias, cada turno es un proceso nuevo que retoma la sesión (como antes)."""
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	recolector = Recolector()
	conversacion = _conversacion_con(tmp_path, recolector, 0, titulo="Prueba")
	_turno(conversacion, recolector, "Uno")
	id_sesion = conversacion.id_externo
	_turno(conversacion, recolector, "Dos")
	procesos_lanzados = [linea["argv"] for linea in _lineas(entorno) if "argv" in linea]
	assert len(procesos_lanzados) == 2
	assert procesos_lanzados[1][procesos_lanzados[1].index("--resume") + 1] == id_sesion
	conversacion.cerrar()


def test_se_relanza_si_cambia_el_modelo_y_no_por_el_modo(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	recolector = Recolector()
	conversacion = _conversacion_con(tmp_path, recolector, 15)
	_turno(conversacion, recolector, "Uno")
	# El modo de permisos se cambia en caliente, sin relanzar.
	conversacion.cambiar_ajustes(modo_permiso="acceptEdits")
	_turno(conversacion, recolector, "Dos")
	lineas = _lineas(entorno)
	assert len([linea for linea in lineas if "argv" in linea]) == 1
	assert {"subtype": "set_permission_mode", "mode": "acceptEdits"} in [linea.get("control") for linea in lineas]
	# El modelo va en la orden: el siguiente mensaje relanza Claude Code con --resume y el modelo nuevo.
	id_sesion = conversacion.id_externo
	conversacion.cambiar_ajustes(modelo="opus")
	_turno(conversacion, recolector, "Tres")
	procesos_lanzados = [linea["argv"] for linea in _lineas(entorno) if "argv" in linea]
	assert len(procesos_lanzados) == 2
	nuevo = procesos_lanzados[1]
	assert nuevo[nuevo.index("--model") + 1] == "opus" and nuevo[nuevo.index("--resume") + 1] == id_sesion
	# Quitar una regla «durante esta sesión» también obliga a relanzar; añadirla con la respuesta, no.
	conversacion.ajustes.reglas_permitidas.append("Bash(ls)")
	_turno(conversacion, recolector, "Cuatro")
	assert len([linea for linea in _lineas(entorno) if "argv" in linea]) == 3
	conversacion.cerrar()


def test_se_cierra_tras_un_rato_sin_mensajes(tmp_path, entorno, monkeypatch):
	from app.services.agentes import claude as modulo

	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	monkeypatch.setattr(modulo, "SEGUNDOS_POR_MINUTO", 0.2)
	recolector = Recolector()
	conversacion = _conversacion_con(tmp_path, recolector, 5)
	_turno(conversacion, recolector, "Uno")
	vivo = conversacion._vivo
	assert vivo is not None and vivo.vivo()
	assert vivo.proceso.esperar_fin(15), "el proceso sin uso debería cerrarse solo"
	assert conversacion._vivo is None
	# El siguiente mensaje lo vuelve a abrir, retomando la sesión.
	_turno(conversacion, recolector, "Dos")
	procesos_lanzados = [linea["argv"] for linea in _lineas(entorno) if "argv" in linea]
	assert len(procesos_lanzados) == 2 and "--resume" in procesos_lanzados[1]
	assert recolector.de_tipo(TipoEvento.FIN_TURNO)[-1].datos["correcto"]
	conversacion.cerrar()


def test_permiso_sesion_añade_regla(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "permiso")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("haz push")
	assert recolector.permiso.wait(20)
	solicitud = recolector.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"]
	assert solicitud.tipo is TipoSolicitud.HERRAMIENTA
	assert "git push" in solicitud.detalle
	assert solicitud.opcion_segura().id == "denegar"
	conversacion.responder(solicitud.id, RespuestaPermiso("permitir_sesion"))
	assert recolector.fin.wait(20)
	assert recolector.de_tipo(TipoEvento.MENSAJE)[-1].texto == "decision:allow"
	assert conversacion.ajustes.reglas_permitidas == ["Bash(git push)"]
	respuesta = [linea for linea in _lineas(entorno) if "respuesta" in linea][0]["respuesta"]
	assert respuesta["response"]["response"]["updatedPermissions"][0]["destination"] == "session"
	conversacion.cerrar()


def test_permiso_denegado(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "permiso")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("haz push")
	assert recolector.permiso.wait(20)
	solicitud = recolector.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"]
	conversacion.responder(solicitud.id, RespuestaPermiso("denegar", mensaje="no"))
	assert recolector.fin.wait(20)
	assert recolector.de_tipo(TipoEvento.MENSAJE)[-1].texto == "decision:deny"
	conversacion.cerrar()


def test_pregunta(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "pregunta")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("pregunta")
	assert recolector.permiso.wait(20)
	solicitud = recolector.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"]
	assert solicitud.tipo is TipoSolicitud.PREGUNTA
	assert solicitud.preguntas[0].opciones[1][0] == "Azul"
	conversacion.responder(solicitud.id, RespuestaPermiso("responder", respuestas={"¿Color?": ["Azul"]}))
	assert recolector.fin.wait(20)
	assert "Azul" in recolector.de_tipo(TipoEvento.MENSAJE)[-1].texto
	conversacion.cerrar()


def test_texto_antes_de_la_pregunta_se_rescata_y_acompana(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "borrador")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("prepara los posts")
	assert recolector.permiso.wait(20)
	solicitud = recolector.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"]
	borrador = "Primer post: hola.\nSegundo post: adiós."
	assert solicitud.contexto == borrador
	# El borrador queda como mensaje propio (para la lista, Copiar y Exportar).
	rescatado = recolector.de_tipo(TipoEvento.MENSAJE)[0]
	assert rescatado.texto == borrador and rescatado.datos["id"] == "m0" and rescatado.datos["rescatado"] is True
	conversacion.responder(solicitud.id, RespuestaPermiso("responder", respuestas={"¿Color?": ["Rojo"]}))
	assert recolector.fin.wait(20)
	assert [m.texto for m in recolector.de_tipo(TipoEvento.MENSAJE)].count(borrador) == 1
	conversacion.cerrar()


def test_pregunta_sin_texto_previo_no_tiene_contexto(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "pregunta")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("pregunta")
	assert recolector.permiso.wait(20)
	assert recolector.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"].contexto == ""
	conversacion.cerrar()


def test_claude_ofrece_anadir_al_turno():
	# Comprobado con Claude Code 2.1.238: lee el mensaje a mitad de turno sin interrumpir (AIChat #238).
	assert ProveedorClaude().capacidades.anadir_al_turno is True


def test_clave_api_aborta(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "clave_api")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("hola")
	assert recolector.fin.wait(20)
	assert any("clave de API" in e.texto for e in recolector.de_tipo(TipoEvento.ERROR))
	assert not recolector.de_tipo(TipoEvento.FIN_TURNO)[0].datos["correcto"]
	conversacion.cerrar()


def test_cli_sin_estados(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "sin_estados")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("hola")
	assert recolector.fin.wait(20)
	assert recolector.de_tipo(TipoEvento.FIN_TURNO)[0].datos["correcto"]
	conversacion.cerrar()


def test_detener_y_cola(tmp_path, entorno, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "cuelga")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("uno")
	conversacion.enviar("dos")
	assert conversacion.ocupada
	assert conversacion.pendientes() == ["dos"]
	conversacion.detener()
	assert recolector.fin.wait(20)
	assert recolector.de_tipo(TipoEvento.FIN_TURNO)[0].datos["interrumpido"]
	conversacion.cerrar()


def test_bifurcar_construye_orden(tmp_path):
	proveedor = ProveedorClaude(orden_base=["claude"])
	conversacion = proveedor.crear_conversacion(
		AjustesSesion(carpeta=tmp_path, bifurcar_de="aaaa-bbbb", modelo="opus", reglas_permitidas=["Bash(ls)", "mal regla;"]),
		lambda e: None,
	)
	orden = conversacion.construir_orden()
	assert orden[orden.index("--resume") + 1] == "aaaa-bbbb"
	assert "--fork-session" in orden
	assert orden[orden.index("--model") + 1] == "opus"
	assert "Bash(ls)" in orden and "mal regla;" not in orden


def test_textos_herramientas():
	assert describir_herramienta("Bash", {"command": "git  status"}) == "Ejecutar orden: git status"
	assert describir_herramienta("mcp__github__crear", {}) == "MCP github: crear"
	assert _regla_para("Bash", {"command": "npm test"}) == "Bash(npm test)"
	assert _regla_para("Edit", {"file_path": "x"}) == "Edit"
