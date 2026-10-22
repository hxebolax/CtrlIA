"""Pruebas de lo nuevo en la versión 1.4.1 (peticiones de un probador).

- Peticiones de los servidores MCP de Codex y Claude Code (permiso para usar
  una herramienta, formularios, enlaces), que antes se rechazaban con «Codex ha
  pedido algo que Ctrl+IA aún no sabe mostrar».
- El visor de mensajes atiende Escape y Alt+C también dentro de la página, y
  tiene teclas para ir a la respuesta, citar y empezar otra sesión.
- Citar en la respuesta y «Nueva sesión a partir del mensaje».
- «Nueva sesión» recuerda el último modelo y esfuerzo de cada agente.

Nada se muestra ni habla: el equipo de desarrollo usa un lector de pantalla.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from app.core.configuracion import Configuracion  # noqa: E402
from app.services.agentes import codex, elicitacion  # noqa: E402
from app.services.agentes.base import (  # noqa: E402
	AjustesSesion,
	ClaseOpcion,
	OpcionLista,
	RespuestaPermiso,
	TipoSolicitud,
)
from app.services.agentes.claude import HERRAMIENTA_ELICITACION, ConversacionClaude, ProveedorClaude  # noqa: E402
from app.services.almacen import Almacen  # noqa: E402
from app.services.gestor import GestorSesiones, MensajeVista  # noqa: E402
from app.ui import avisos, visor_ayuda  # noqa: E402
from tests.ayudas_wx import esperar_hasta, procesar_eventos, simular_tecla  # noqa: E402

FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"

#: Petición real de Codex 0.162 para usar una herramienta de un servidor MCP.
PERMISO_HERRAMIENTA = {
	"threadId": "h1", "turnId": "t1", "serverName": "github", "mode": "form",
	"message": "Allow the github MCP server to run tool \"create_issue\"?",
	"requestedSchema": {"type": "object", "properties": {}},
	"_meta": {
		"codex_approval_kind": "mcp_tool_call", "persist": ["session", "always"],
		"tool_title": "Crear incidencia", "tool_description": "Abre una incidencia en el repositorio.",
		"tool_params": {"titulo": "Fallo"},
		"tool_params_display": [{"name": "titulo", "display_name": "Título", "value": "Fallo"}],
	},
}


# -- Peticiones de los servidores MCP ------------------------------------------


def test_permiso_de_herramienta_mcp_de_codex():
	solicitud = codex._solicitud_mcp(5, PERMISO_HERRAMIENTA)
	assert solicitud.tipo is TipoSolicitud.HERRAMIENTA
	assert solicitud.titulo == "Codex quiere usar «Crear incidencia» del servidor MCP «github»"
	assert "¿Permites que el servidor MCP «github» use su herramienta «create_issue»?" in solicitud.detalle
	assert "Título: Fallo" in solicitud.detalle and "Abre una incidencia" in solicitud.detalle
	assert [o.id for o in solicitud.opciones] == ["accept", "accept_session", "accept_always", "decline", "cancel"]
	assert solicitud.opcion_segura().id == "decline"
	pendiente = codex._Pendiente(5, "mcpServer/elicitation/request", PERMISO_HERRAMIENTA, solicitud)
	resultado = codex._resultado_peticion
	assert resultado(pendiente, RespuestaPermiso("accept")) == {"action": "accept", "content": {}}
	assert resultado(pendiente, RespuestaPermiso("accept_session")) == {
		"action": "accept", "content": {}, "_meta": {"persist": "session"}}
	assert resultado(pendiente, RespuestaPermiso("accept_always"))["_meta"] == {"persist": "always"}
	assert resultado(pendiente, RespuestaPermiso("decline")) == {"action": "decline"}
	assert resultado(pendiente, RespuestaPermiso("inventada")) == {"action": "decline"}


def test_permiso_sin_recordar_y_confirmacion_simple():
	sin_recordar = dict(PERMISO_HERRAMIENTA, _meta={"codex_approval_kind": "mcp_tool_call"})
	assert [o.id for o in codex._solicitud_mcp(6, sin_recordar).opciones] == ["accept", "decline", "cancel"]
	# Un formulario sin campos de cualquier servidor es una confirmación.
	solicitud = elicitacion.solicitud_elicitacion("7", "notas", "¿Borrar la nota?", "form",
		esquema={"type": "object", "properties": {}})
	assert solicitud.tipo is TipoSolicitud.HERRAMIENTA and "¿Borrar la nota?" in solicitud.detalle
	assert elicitacion.resultado_elicitacion(solicitud, RespuestaPermiso("accept")) == {"action": "accept", "content": {}}


def test_formularios_enlaces_y_lo_desconocido():
	esquema = {"type": "object", "required": ["color"], "properties": {
		"color": {"type": "string", "enum": ["r", "a"], "enumNames": ["Rojo", "Azul"]},
		"clave": {"type": "string", "format": "password"},
	}}
	for modo in ("form", "openai/form", "openaiForm", None):
		solicitud = elicitacion.solicitud_elicitacion("1", "base", "Datos", modo, esquema=esquema)
		assert solicitud.tipo is TipoSolicitud.PREGUNTA, modo
	assert solicitud.preguntas[0].opciones == [("Rojo", ""), ("Azul", "")]
	assert "obligatorio" in solicitud.preguntas[0].texto and solicitud.preguntas[1].secreta
	respuesta = RespuestaPermiso("accept", respuestas={"color": ["Azul"], "clave": ["x"]})
	assert elicitacion.resultado_elicitacion(solicitud, respuesta) == {"action": "accept", "content": {"color": "a", "clave": "x"}}

	enlace = elicitacion.solicitud_elicitacion("2", "drive", "Entra", "url", url="https://ejemplo.org/x")
	assert "https://ejemplo.org/x" in enlace.detalle
	assert elicitacion.resultado_elicitacion(enlace, RespuestaPermiso("accept")) == {"action": "accept"}

	# Un campo que no se sabe pedir o un modo nuevo: se enseña y solo se puede rechazar o cancelar.
	raro = elicitacion.solicitud_elicitacion("3", "x", "Sube un archivo", "form",
		esquema={"type": "object", "properties": {"f": {"type": "object"}}})
	assert [o.clase for o in raro.opciones] == [ClaseOpcion.DENEGAR, ClaseOpcion.CANCELAR]
	assert "Sube un archivo" in raro.detalle
	nuevo = elicitacion.solicitud_elicitacion("4", "x", "Verifica", "openai/userVerification")
	assert {o.id for o in nuevo.opciones} == {"decline", "cancel"}


def test_codex_aprobacion_hecha_como_pregunta():
	"""Codex también puede pedir el permiso como pregunta con respuestas en inglés."""
	params = {"threadId": "h", "turnId": "t", "itemId": "i", "questions": [{
		"id": "mcp_tool_call_approval_abc", "header": "¿Usar la herramienta?", "question": "Run create_issue?",
		"options": [{"label": "Allow", "description": ""}, {"label": "Allow for this session", "description": ""},
			{"label": "Cancel", "description": ""}],
	}]}
	solicitud = codex._solicitud_pregunta(8, params)
	assert solicitud.tipo is TipoSolicitud.HERRAMIENTA
	assert [o.etiqueta for o in solicitud.opciones] == ["Permitir una vez", "Permitir durante esta sesión", "Cancelar"]
	pendiente = codex._Pendiente(8, "item/tool/requestUserInput", params, solicitud)
	assert codex._resultado_peticion(pendiente, RespuestaPermiso("opcion1")) == {
		"answers": {"mcp_tool_call_approval_abc": {"answers": ["Allow for this session"]}}}
	assert codex._resultado_peticion(pendiente, RespuestaPermiso(solicitud.opcion_segura().id)) == {
		"answers": {"mcp_tool_call_approval_abc": {"answers": ["Cancel"]}}}


class _ProcesoFalso:
	def __init__(self) -> None:
		self.escrito: list[dict] = []
		self.vivo = True

	def escribir_json(self, datos: dict) -> None:
		self.escrito.append(datos)


def test_claude_elicitacion_mcp(tmp_path):
	eventos = []
	conversacion = ConversacionClaude(ProveedorClaude(orden_base=["claude"]), AjustesSesion(carpeta=tmp_path),
		lambda evento: eventos.append(evento))
	from app.services.agentes.claude import _Turno

	turno = _Turno()
	turno.proceso = _ProcesoFalso()
	conversacion._turno = turno
	conversacion._al_peticion_control(turno, {"type": "control_request", "request_id": "e1", "request": {
		"subtype": "elicitation", "mcp_server_name": "github", "message": "¿Qué repositorio?", "mode": "form",
		"requested_schema": {"type": "object", "properties": {"repo": {"type": "string", "title": "Repositorio"}}},
	}})
	solicitud = next(e.datos["solicitud"] for e in eventos if e.datos.get("solicitud"))
	assert solicitud.tipo is TipoSolicitud.PREGUNTA and solicitud.titulo == "github te pide datos"
	assert conversacion._solicitudes["e1"]["herramienta"] == HERRAMIENTA_ELICITACION
	conversacion.responder("e1", RespuestaPermiso("accept", respuestas={"repo": ["ctrl-ia"]}))
	assert turno.proceso.escrito[-1] == {"type": "control_response", "response": {
		"subtype": "success", "request_id": "e1", "response": {"action": "accept", "content": {"repo": "ctrl-ia"}}}}


# -- Visor de mensajes ---------------------------------------------------------


def test_parrafo_y_combinaciones():
	texto = "Primero.\n\nSegundo párrafo\ncon dos líneas.\n\nTercero."
	assert visor_ayuda.parrafo_en(texto, texto.index("dos")) == "Segundo párrafo\ncon dos líneas."
	assert visor_ayuda.parrafo_en(texto, 0) == "Primero."
	assert visor_ayuda.parrafo_en(texto, len(texto)) == "Tercero."
	assert visor_ayuda._combinacion(wx.WXK_ESCAPE, wx.MOD_NONE) == "Escape"
	assert visor_ayuda._combinacion(ord("N"), wx.MOD_CONTROL | wx.MOD_ALT) == "Ctrl+Alt+n"
	pagina = visor_ayuda.markdown_a_html("# Hola", "Hola", teclas={"Escape": "cerrar"})
	assert "postMessage" in pagina and '"Escape": "cerrar"' in pagina and visor_ayuda.ESQUEMA_ORDEN in pagina
	assert "<script>" not in visor_ayuda.markdown_a_html("# Hola", "Hola")


def test_visor_atiende_las_teclas_de_la_pagina(app_wx, monkeypatch):
	monkeypatch.setattr(visor_ayuda, "avisar", lambda padre, mensaje: None)
	llamadas: list[tuple[str, str]] = []
	acciones = {"citar": lambda texto: llamadas.append(("citar", texto)),
		"respuesta": lambda texto: llamadas.append(("respuesta", texto))}
	visor = visor_ayuda.mostrar_documento(None, "Mensaje de prueba", "Uno.\n\nDos.", forzar_texto=True, mostrar=False,
		acciones=acciones, teclas={"Ctrl+r": "citar", "Alt+3": "respuesta", "Ctrl+x": "inexistente"})
	try:
		assert "Ctrl+x" not in visor.teclas and visor.teclas["Escape"] == "cerrar"
		# Lo que manda la página (modo HTML) llega como JSON y se ejecuta después del evento.
		diferidas = []
		monkeypatch.setattr(wx, "CallAfter", lambda funcion, *argumentos: diferidas.append((funcion, argumentos)))
		visor._orden_de_pagina(json.dumps({"orden": "citar", "seleccion": " Dos. "}))
		monkeypatch.undo()
		funcion, argumentos = diferidas[-1]
		funcion(*argumentos)
		assert llamadas[-1] == ("citar", "Dos.")
		visor._orden_de_pagina("esto no es json")
		# Modo texto: sin selección, Ctrl+R cita el párrafo del cursor.
		visor.contenido.SetInsertionPoint(visor.contenido.GetValue().index("Dos"))
		assert simular_tecla(visor, "r", ctrl=True).gestionado
		assert llamadas[-1] == ("citar", "Dos.")
		assert simular_tecla(visor, "3", alt=True).gestionado
		assert llamadas[-1] == ("respuesta", "")
		assert simular_tecla(visor, "a").saltado
		# Escape cierra (una sola vez, aunque llegue también de la página).
		assert simular_tecla(visor, wx.WXK_ESCAPE).gestionado
		assert visor._cerrado and "Mensaje de prueba" not in visor_ayuda._abiertos
		assert not visor.ejecutar_orden("cerrar")
	finally:
		if visor:
			visor.Destroy()
		procesar_eventos()


# -- Ventana principal: citar y nueva sesión a partir de un mensaje -------------


@pytest.fixture
def ventana(app_wx, tmp_path, monkeypatch):
	from app.ui.ventana_principal import VentanaPrincipal

	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	almacen = Almacen(tmp_path / "prueba.db")
	proveedores = {"claude": ProveedorClaude(orden_base=[sys.executable, str(FAKE)])}
	marco = VentanaPrincipal(config, None, almacen=almacen, proveedores=proveedores)
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()
		procesar_eventos()


@pytest.fixture
def anuncios(monkeypatch):
	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	return dichos


def _abrir(ventana, tmp_path, titulo="Diseño"):
	clave = ventana.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo=titulo))
	# Si ya había una lectura de la lista en marcha, la nueva se une a ella: se pide hasta que aparezca.
	esperar_hasta(lambda: ventana.fila_de(clave) is not None or ventana.refrescar_sesiones(True), 30)
	ventana.abrir_sesion(clave, mover_foco=False)
	procesar_eventos()
	return clave


def test_citar_en_la_respuesta(ventana, tmp_path, anuncios):
	clave = _abrir(ventana, tmp_path)
	ventana.respuesta.SetValue("Gracias.")
	assert ventana.citar_en_respuesta("Punto uno\n\nPunto dos", clave)
	assert ventana.respuesta.GetValue() == "Gracias.\n\n> Punto uno\n>\n> Punto dos\n\n"
	assert "Citadas 3 líneas" in anuncios[-1]
	assert not ventana.citar_en_respuesta("Otro", "ctrlia:otra-sesion")
	assert not ventana.citar_en_respuesta("   ", clave)
	# Desde el panel de lectura, sin selección, se cita el párrafo del cursor.
	ventana._mensajes = [MensajeVista("asistente", "Idea A.\n\nIdea B.", 0.0, id="m1")]
	ventana._pintar_mensajes()
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	ventana.panel_lectura.ChangeValue("Idea A.\n\nIdea B.")
	ventana.panel_lectura.SetInsertionPoint(10)
	assert ventana._texto_de_lectura(citar=True) == "Idea B."
	assert ventana._texto_de_lectura(citar=False) == ""


def test_nueva_sesion_a_partir_de_un_mensaje(ventana, tmp_path, monkeypatch):
	clave = _abrir(ventana, tmp_path, "Arquitectura")
	pedidas = []
	monkeypatch.setattr(ventana, "accion_nueva_sesion", lambda **datos: pedidas.append(datos) or True)
	mensaje = MensajeVista("asistente", "Podrías usar colas.\nO eventos.", 0.0, id="m1")
	assert ventana.nueva_sesion_desde_mensaje(mensaje, "", clave)
	datos = pedidas[-1]
	assert datos["agente"] == "claude" and datos["carpeta"] == tmp_path
	assert datos["titulo"] == "Sobre: Podrías usar colas. O eventos."
	assert "«Arquitectura»" in datos["mensaje"] and "> Podrías usar colas.\n> O eventos.\n\n" in datos["mensaje"]
	# Con un fragmento elegido, solo se cita ese fragmento.
	ventana.nueva_sesion_desde_mensaje(mensaje, "O eventos.", clave)
	assert "> O eventos." in pedidas[-1]["mensaje"] and "colas" not in pedidas[-1]["mensaje"]


def test_volver_al_visor_sin_visor(ventana, anuncios):
	ventana.accion_ir_al_visor()
	assert "No hay ningún mensaje abierto" in anuncios[-1]


# -- Último modelo y esfuerzo de cada agente -----------------------------------


class _ClaudeConLista(ProveedorClaude):
	def listar_modelos(self) -> list[OpcionLista]:
		return [OpcionLista("sonnet", "Sonnet 5.5"), OpcionLista("fable", "Fable 5.1")]


def test_nueva_sesion_recuerda_modelo_y_esfuerzo(app_wx, tmp_path, monkeypatch):
	from app.ui.dialogos import nueva_sesion

	almacen = Almacen(tmp_path / "n.db")
	config = Configuracion(tmp_path / "s.json")
	gestor = GestorSesiones(almacen, {"claude": ProveedorClaude(orden_base=["claude"])}, lambda c, e: None)
	try:
		dialogo = nueva_sesion.DialogoNuevaSesion(None, gestor, almacen, config, carpeta=tmp_path)
		try:
			assert dialogo._modelos[dialogo.modelo.GetSelection()].id == ""
			dialogo.modelo.SetSelection(next(i for i, m in enumerate(dialogo._modelos) if m.id == "opus"))
			dialogo.esfuerzo.SetSelection(next(i for i, e in enumerate(dialogo._esfuerzos) if e.id == "high"))
			monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
			dialogo._aceptar()
		finally:
			dialogo.Destroy()
		assert config.obtener("agentes.ultimas_opciones") == {"claude": {"modelo": "opus", "esfuerzo": "high"}}
		dialogo = nueva_sesion.DialogoNuevaSesion(None, gestor, almacen, config, carpeta=tmp_path,
			mensaje="> cita\n\n", titulo="Sobre: cita")
		try:
			assert dialogo._modelos[dialogo.modelo.GetSelection()].id == "opus"
			assert dialogo._esfuerzos[dialogo.esfuerzo.GetSelection()].id == "high"
			assert dialogo.titulo.GetValue() == "Sobre: cita"
		finally:
			dialogo.Destroy()
		# Un modelo que solo está en la lista real del agente se ofrece antes de que llegue y se conserva después.
		nueva_sesion.recordar_opciones(config, "claude", modelo="claude-fable-5-1")
		gestor.proveedores["claude"] = _ClaudeConLista(orden_base=["claude"])
		dialogo = nueva_sesion.DialogoNuevaSesion(None, gestor, almacen, config, carpeta=tmp_path)
		try:
			assert dialogo._modelos[dialogo.modelo.GetSelection()].id == "claude-fable-5-1"
			dialogo._poner_modelos("claude", gestor.proveedores["claude"].listar_modelos())
			assert dialogo._modelos[dialogo.modelo.GetSelection()].id == "claude-fable-5-1"
			assert [m.id for m in dialogo._modelos][:3] == ["", "sonnet", "fable"]
		finally:
			dialogo.Destroy()
	finally:
		almacen.cerrar()


def test_opciones_de_la_sesion_recuerdan_el_modelo(tmp_path):
	from app.ui.dialogos.nueva_sesion import opciones_recordadas, recordar_opciones

	config = Configuracion(tmp_path / "s.json")
	assert opciones_recordadas(config, "codex") == {}
	recordar_opciones(config, "codex", modelo="gpt-6-astra")
	recordar_opciones(config, "codex", esfuerzo="high")
	recordar_opciones(config, "", modelo="nada")
	assert opciones_recordadas(config, "codex") == {"modelo": "gpt-6-astra", "esfuerzo": "high"}
	assert list(config.obtener("agentes.ultimas_opciones")) == ["codex"]
