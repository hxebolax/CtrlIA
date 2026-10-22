"""Pruebas de la conversación con el agente (fase 2): volver a enviar, permisos de la sesión,
mensajes recientes, texto previo a una pregunta, opciones largas y añadir al turno.

Sin mostrar ventanas ni hablar: la voz se sustituye por una lista.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from app.core.configuracion import Configuracion  # noqa: E402
from app.services.agentes.base import (  # noqa: E402
	AjustesSesion,
	ClaseOpcion,
	Evento,
	OpcionPermiso,
	Pregunta,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.agentes.claude import ProveedorClaude  # noqa: E402
from app.services.almacen import Almacen  # noqa: E402
from app.ui import avisos  # noqa: E402
from tests.ayudas_wx import esperar_hasta, procesar_eventos  # noqa: E402

FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"


@pytest.fixture
def anuncios(monkeypatch):
	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	monkeypatch.setattr(avisos, "notificar", lambda texto, *a, **k: dichos.append(texto))
	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	return dichos


@pytest.fixture
def ventana(app_wx, tmp_path, monkeypatch, anuncios):
	from app.ui.ventana_principal import VentanaPrincipal

	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	almacen = Almacen(tmp_path / "prueba.db")
	proveedores = {"claude": ProveedorClaude(orden_base=[sys.executable, str(FAKE)])}
	marco = VentanaPrincipal(config, None, almacen=almacen, proveedores=proveedores)
	# Las cargas en segundo plano se hacen en el acto: la prueba no depende de hilos.
	monkeypatch.setattr(type(marco), "en_segundo_plano",
		lambda self, funcion, *args, al_terminar=None, al_fallar=None, clave=None: al_terminar(funcion(*args)) if al_terminar else funcion(*args))
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()
		procesar_eventos()


def _sesion(ventana, tmp_path, titulo="Prueba", **ajustes) -> str:
	clave = ventana.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo=titulo, **ajustes))
	# La lista se lee en segundo plano y una petición se une a la que ya esté en
	# marcha (que pudo empezar antes de crear la sesión): se pide de nuevo hasta
	# que aparece. Con la batería completa el equipo va cargado.

	def esta() -> bool:
		if ventana.fila_de(clave) is None:
			ventana.refrescar_sesiones(True)
			return False
		return True

	esperar_hasta(esta, 30, intervalo=0.05)
	return clave


# ── Volver a enviar (#278) ──────────────────────────────────────────────────────


def _etiquetas(menu: wx.Menu) -> list[str]:
	return [item.GetItemLabelText() for item in menu.GetMenuItems() if not item.IsSeparator()]


def test_volver_a_enviar_pide_confirmacion_y_envia_una_vez(ventana, tmp_path, monkeypatch):
	from app.ui import vp_mensajes

	clave = _sesion(ventana, tmp_path)
	ventana.almacen.anadir_mensaje(clave, "usuario", "Hazme el informe")
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	menu = ventana._menu_contextual_mensajes()
	try:
		assert "Volver a enviar..." in _etiquetas(menu)
	finally:
		menu.Destroy()

	enviados: list[tuple[str, str]] = []
	monkeypatch.setattr(type(ventana), "enviar_mensaje", lambda self, c, texto, *a, **k: enviados.append((c, texto)) or True)
	monkeypatch.setattr(vp_mensajes, "confirmar", lambda *a, **k: wx.ID_NO)
	assert ventana.volver_a_enviar(ventana._mensajes[0]) is False
	assert enviados == []  # nunca se envía sin confirmar
	preguntas: list[str] = []
	monkeypatch.setattr(vp_mensajes, "confirmar", lambda padre, texto, *a, **k: preguntas.append(texto) or wx.ID_YES)
	assert ventana.volver_a_enviar(ventana._mensajes[0]) is True
	assert enviados == [(clave, "Hazme el informe")]
	assert "Hazme el informe" in preguntas[0]


def test_volver_a_enviar_no_aparece_en_sesiones_de_otros_programas(ventana, tmp_path):
	from app.services.gestor import FilaSesion, MensajeVista

	fila = FilaSesion("claude:externa", "claude", "Terminal", origen="terminal")
	ventana._todas = [fila]
	ventana.mostrar_sesiones(True)
	ventana.sesion_actual = "claude:externa"
	ventana.mostrar_mensajes([MensajeVista("usuario", "Hola", 1.0, id="u1")])
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	menu = ventana._menu_contextual_mensajes()
	try:
		assert "Volver a enviar..." not in _etiquetas(menu)
		assert "Copiar al cuadro de respuesta" in _etiquetas(menu)
	finally:
		menu.Destroy()
	assert ventana.volver_a_enviar(ventana._mensajes[0]) is False


# ── Mensajes recientes y «Cargar anteriores» (#240) ─────────────────────────────


def _sesion_larga(ventana, tmp_path, cuantos=130) -> str:
	clave = _sesion(ventana, tmp_path, "Larga")
	for numero in range(1, cuantos + 1):
		ventana.almacen.anadir_mensaje(clave, "usuario" if numero % 2 else "asistente", "mensaje %d" % numero)
	return clave


def test_sesion_larga_carga_solo_los_ultimos(ventana, tmp_path, anuncios):
	ventana.poner_opcion("mensajes.recientes", 50)
	clave = _sesion_larga(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=True)
	assert ventana.mensajes_cargados() == 50 and ventana.total_mensajes() == 130
	assert ventana.lista_mensajes.GetItemCount() == 51
	assert ventana._mensajes[0].rol == "anteriores"
	assert "Cargar 50 mensajes anteriores (80 sin cargar)" in ventana.lista_mensajes.valores_fila(0)[1]
	assert ventana._mensajes[1].texto == "mensaje 81"
	assert ventana.lista_mensajes.GetName() == "Mensajes (50 de 130)"
	assert any("Últimos 50 de 130" in texto for texto in anuncios)
	assert ventana.lista_mensajes.GetFocusedItem() == 50  # el último, como siempre

	# Intro sobre la fila carga el lote anterior; el foco queda justo encima de donde estaba.
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	ventana.ver_mensaje_actual()
	assert ventana.mensajes_cargados() == 100 and len(ventana._anteriores) == 30
	enfocado = ventana.mensaje_actual()
	assert enfocado.texto == "mensaje 80"
	assert ventana.numero_mensaje(ventana.lista_mensajes.GetFocusedItem()) == 80

	# Desde un mensaje concreto, el foco no se mueve de él.
	indice = next(i for i, m in enumerate(ventana._mensajes) if m.texto == "mensaje 90")
	ventana.lista_mensajes.enfocar_fila(indice, anunciar=False)
	ventana.accion_cargar_todos()
	assert not ventana._anteriores and ventana._mensajes[0].texto == "mensaje 1"
	assert ventana.mensaje_actual().texto == "mensaje 90"
	assert ventana.lista_mensajes.GetName() == "Mensajes (130)"
	ventana.accion_cargar_anteriores()
	assert "Ya están cargados todos" in anuncios[-1]


def test_buscar_encuentra_mensajes_aun_no_cargados(ventana, tmp_path, monkeypatch):
	from app.ui import vp_mensajes

	ventana.poner_opcion("mensajes.recientes", 50)
	clave = _sesion_larga(ventana, tmp_path)
	ventana.almacen.anadir_mensaje(clave, "asistente", "la palabra clave está al final")
	ventana.abrir_sesion(clave, mover_foco=False)
	monkeypatch.setattr(vp_mensajes, "pedir_texto", lambda *a, **k: "mensaje 3")
	ventana.lista_mensajes.SetFocus = lambda: None  # sin foco real en las pruebas
	ventana.accion_buscar()
	assert ventana.mensaje_actual().texto == "mensaje 3"
	assert len(ventana._anteriores) == 2  # se ha cargado lo justo para enseñarlo
	assert ventana._mensajes[0].rol == "anteriores"
	completos = ventana.mensajes_completos()
	assert len(completos) == 131 and completos[0].texto == "mensaje 1"


def test_las_listas_y_lo_nuevo_usan_la_conversacion_entera(ventana, tmp_path, monkeypatch):
	from app.services.gestor import MensajeVista

	ventana.poner_opcion("mensajes.recientes", 50)
	clave = _sesion_larga(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	recibidos = []

	class ListasFalsas:
		def __init__(self, padre, mensajes, titulo, pestana):
			recibidos.append(list(mensajes))
			self.indice_mensaje = 4

		def ShowModal(self):  # noqa: N802 - API de wx
			return wx.ID_CLOSE

		def pagina_actual(self):
			return "codigo"

		def Destroy(self):  # noqa: N802 - API de wx
			pass

	from app.ui.dialogos import listas

	monkeypatch.setattr(listas, "DialogoListas", ListasFalsas)
	ventana.lista_mensajes.SetFocus = lambda: None
	ventana.accion_enlaces()
	assert len(recibidos[0]) == 130
	assert ventana.mensaje_actual().texto == "mensaje 5"
	# Un mensaje nuevo llega al final aunque falten antiguos por cargar.
	ventana.anadir_o_actualizar_mensaje(MensajeVista("asistente", "respuesta nueva", 2.0, id=99999))
	assert ventana._mensajes[-1].texto == "respuesta nueva"
	assert ventana.numero_mensaje(len(ventana._mensajes) - 1) == 131


def test_todos_los_mensajes_si_la_preferencia_es_cero(ventana, tmp_path):
	ventana.poner_opcion("mensajes.recientes", 0)
	clave = _sesion_larga(ventana, tmp_path, 120)
	ventana.abrir_sesion(clave, mover_foco=False)
	assert ventana.lista_mensajes.GetItemCount() == 120 and not ventana._anteriores


def test_preferencia_de_mensajes_recientes(tmp_path):
	from app.ui.dialogo_preferencias import PAGINAS

	config = Configuracion(tmp_path / "settings.json")
	assert config.obtener("mensajes.recientes") == 100
	opciones = [o for pagina in PAGINAS for o in pagina.opciones if o.clave == "mensajes.recientes"]
	assert opciones and 0 in [valor for valor, _n in opciones[0].opciones]


# ── Permisos de la sesión (#307, #298) ──────────────────────────────────────────


def test_describir_reglas_en_palabras():
	from app.ui.dialogos.permisos_sesion import describir_regla

	assert describir_regla("Orden(git switch)") == "Ejecutar «git switch» con cualquier argumento"
	assert describir_regla("Bash(git push:*)") == "Ejecutar «git push» con cualquier argumento"
	assert describir_regla("Bash(git push)") == "Ejecutar la orden «git push»"
	assert describir_regla("Edit") == "Editar archivos: cualquiera"
	assert describir_regla("WebFetch(domain:example.com)") == "Leer páginas web de example.com"
	assert "servidor MCP github" in describir_regla("mcp__github__create_issue")


def test_dialogo_permisos_quita_tras_confirmar(app_wx, monkeypatch):
	from app.ui.dialogos import permisos_sesion

	dialogo = permisos_sesion.DialogoPermisosSesion(None, "Web", "Claude Code", "Preguntar antes de cada acción", "",
		["Bash(git push)", "Edit"], fechas={"Edit": 1_700_000_000.0}, trabajando=True)
	try:
		assert dialogo.lista.GetCount() == 2
		assert "Regla: Edit." in dialogo.lista.GetString(1) and "Permitido el" in dialogo.lista.GetString(1)
		assert "el turno en marcha conserva" in dialogo.nota.GetValue()
		assert all(c.GetHelpText() for c in (dialogo.lista, dialogo.boton_quitar, dialogo.boton_todas, dialogo.modo))
		monkeypatch.setattr(permisos_sesion, "enfocar", lambda control: None)
		monkeypatch.setattr(permisos_sesion, "confirmar", lambda *a, **k: wx.ID_NO)
		dialogo.lista.SetSelection(0)
		assert dialogo.quitar_elegida() is False and len(dialogo.reglas) == 2
		preguntas: list[str] = []
		monkeypatch.setattr(permisos_sesion, "confirmar", lambda padre, texto, *a, **k: preguntas.append(texto) or wx.ID_YES)
		assert dialogo.quitar_elegida() is True
		assert dialogo.reglas == ["Edit"] and dialogo.cambiado
		assert "Bash(git push)" in preguntas[0]
		assert dialogo.quitar_todas() is True and dialogo.reglas == []
		assert "No hay nada permitido" in dialogo.lista.GetString(0)
		assert not dialogo.boton_quitar.IsEnabled()
	finally:
		dialogo.Destroy()


def test_quitar_reglas_guarda_y_cambia_la_conversacion_viva(ventana, tmp_path):
	clave = _sesion(ventana, tmp_path, reglas_permitidas=["Bash(git push)", "Edit"])
	conversacion = ventana.gestor.conversacion(clave)
	lista = conversacion.ajustes.reglas_permitidas
	ventana._apuntar_fechas_reglas(clave, ["Bash(git push)", "Edit"])
	ventana.quitar_reglas(clave, ["Edit"], dict(ventana.almacen.sesion(clave).extra["reglas_fechas"]))
	sesion = ventana.almacen.sesion(clave)
	assert sesion.reglas == ["Edit"]
	assert list(sesion.extra["reglas_fechas"]) == ["Edit"]
	assert conversacion.ajustes.reglas_permitidas is lista and lista == ["Edit"]  # la misma lista: Codex la ve al momento
	# El siguiente turno de Claude ya no lleva la regla quitada.
	orden = conversacion.construir_orden()
	assert orden[orden.index("--allowedTools") + 1:] [:1] == ["Edit"]
	assert "Bash(git push)" not in orden


def test_permisos_de_la_sesion_desde_el_menu(ventana, tmp_path, monkeypatch):
	from app.ui.dialogos import permisos_sesion

	clave = _sesion(ventana, tmp_path, reglas_permitidas=["Bash(git push)"])
	ventana.abrir_sesion(clave, mover_foco=False)
	vistos = []

	class Falso:
		def __init__(self, padre, titulo, agente, modo, ayuda, reglas, fechas=None, trabajando=False, cuando="",
				reglas_agente=""):
			vistos.append((titulo, agente, modo, list(reglas)))
			# Las reglas de la propia configuración de Claude Code, para leer (AIChat #336).
			assert reglas_agente.startswith("De la propia configuración de Claude Code")
			self.reglas, self.fechas, self.cambiado = [], {}, True

		def ShowModal(self):  # noqa: N802 - API de wx
			return wx.ID_CLOSE

		def Destroy(self):  # noqa: N802 - API de wx
			pass

	monkeypatch.setattr(permisos_sesion, "DialogoPermisosSesion", Falso)
	ventana.accion_permisos_sesion()
	assert vistos == [("Prueba", "Claude", "Preguntar antes de cada acción", ["Bash(git push)"])]
	assert ventana.almacen.sesion(clave).reglas == []


def test_al_permitir_durante_la_sesion_se_apunta_la_fecha(ventana, tmp_path, monkeypatch):
	from app.services.agentes.base import RespuestaPermiso
	from app.ui.dialogos import solicitud as modulo

	clave = _sesion(ventana, tmp_path)
	conversacion = ventana.gestor.conversacion(clave)
	monkeypatch.setattr(modulo, "pedir_respuesta", lambda *a, **k: RespuestaPermiso("permitir_sesion"))
	monkeypatch.setattr(ventana.gestor, "responder",
		lambda c, i, r: conversacion.ajustes.reglas_permitidas.append("Bash(npm test)"))
	ventana._responder(clave, SolicitudPermiso("x", TipoSolicitud.HERRAMIENTA, "Claude quiere usar «Ejecutar orden»"))
	assert "Bash(npm test)" in ventana.almacen.sesion(clave).extra["reglas_fechas"]


# ── Texto antes de una pregunta (#276) y opciones largas (#203) ─────────────────


def _pregunta(contexto: str = "", descripcion: str = "") -> SolicitudPermiso:
	return SolicitudPermiso(
		"p1", TipoSolicitud.PREGUNTA, "Claude te hace una pregunta", contexto=contexto,
		preguntas=[Pregunta("q", "¿Lo publico?", "Publicar", [("Publicar", descripcion), ("Cambiarlo", "Lo reescribo")])],
		opciones=[OpcionPermiso("responder", "Responder", ClaseOpcion.PERMITIR),
			OpcionPermiso("no_responder", "No responder", ClaseOpcion.DENEGAR)],
	)


def test_dialogo_muestra_lo_que_escribio_el_agente_antes(app_wx):
	from app.ui.dialogos.solicitud import DialogoSolicitud

	dialogo = DialogoSolicitud(None, _pregunta(contexto="Primer post: hola.\nSegundo post: adiós."))
	try:
		assert dialogo.contexto is not None
		assert dialogo.contexto.GetValue() == "Primer post: hola.\nSegundo post: adiós."
		assert not dialogo.contexto.IsEditable() and dialogo.contexto.GetHelpText()
		hijos = dialogo.GetChildren()[0].GetChildren()
		# La etiqueta va justo antes de su campo, y el texto previo antes del detalle.
		indice = hijos.index(dialogo.contexto)
		assert isinstance(hijos[indice - 1], wx.StaticText) and "escribió antes" in hijos[indice - 1].GetLabel()
		assert indice < hijos.index(dialogo.detalle)
		etiquetas = [h.GetLabel() for h in hijos if isinstance(h, wx.Button)]
		assert not any("&e" in e.lower() for e in etiquetas)  # Alt+E es del texto previo
	finally:
		dialogo.Destroy()


def test_dialogo_sin_texto_previo_no_tiene_ese_campo(app_wx):
	from app.ui.dialogos.solicitud import DialogoSolicitud

	dialogo = DialogoSolicitud(None, _pregunta())
	try:
		assert dialogo.contexto is None
	finally:
		dialogo.Destroy()


def test_opcion_con_descripcion_larga_se_lee_entera(app_wx):
	from app.ui.dialogos.solicitud import DialogoSolicitud

	larga = "Publica los dos posts en Mastodon tal cual, con sus etiquetas y su enlace, " * 6
	dialogo = DialogoSolicitud(None, _pregunta(descripcion=larga.strip()))
	try:
		assert len(dialogo.descripciones) == 1
		campo = dialogo.descripciones[0]
		assert campo.GetValue() == "Publicar — " + larga.strip()
		assert campo.IsMultiLine() and not campo.IsEditable()
		_pregunta_, lista, _otra = dialogo._preguntas[0]
		lista.SetSelection(1)
		evento = wx.CommandEvent(wx.wxEVT_LISTBOX, lista.GetId())
		evento.SetEventObject(lista)
		evento.SetInt(1)
		lista.GetEventHandler().ProcessEvent(evento)
		assert campo.GetValue() == "Cambiarlo — Lo reescribo"
	finally:
		dialogo.Destroy()


def test_la_ventana_completa_el_contexto_con_el_ultimo_texto(ventana, tmp_path):
	clave = _sesion(ventana, tmp_path)
	ventana._al_evento(clave, Evento(TipoEvento.MENSAJE, "Este es el borrador del commit.", {"id": "m1"}))
	solicitud = _pregunta()
	ventana._al_evento(clave, Evento(TipoEvento.PERMISO, solicitud.titulo, {"solicitud": solicitud}))
	assert solicitud.contexto == "Este es el borrador del commit."
	# Tras el resultado de una acción, lo escrito antes ya no acompaña a la siguiente pregunta.
	ventana._al_evento(clave, Evento(TipoEvento.HERRAMIENTA_FIN, "", {"salida": "ok"}))
	otra = _pregunta()
	ventana._al_evento(clave, Evento(TipoEvento.PERMISO, otra.titulo, {"solicitud": otra}))
	assert otra.contexto == ""


# ── Añadir al turno en marcha (#238, #239) ──────────────────────────────────────


def test_claude_sin_turno_en_marcha_no_anade_y_el_texto_se_queda(ventana, tmp_path, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.respuesta.SetValue("usa el otro archivo")
	ventana.accion_anadir_al_turno()
	assert "no está trabajando" in anuncios[-1]
	assert ventana.respuesta.GetValue() == "usa el otro archivo"


def test_anadir_al_turno_entrega_sin_detener_y_devuelve_si_falla(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	entregados: list[str] = []
	conversacion = SimpleNamespace(anadir_al_turno=entregados.append, anadidos_pendientes=1, _cola=[])
	proveedor = SimpleNamespace(capacidades=SimpleNamespace(anadir_al_turno=True))
	monkeypatch.setattr(ventana.gestor, "proveedor", lambda _id: proveedor)
	monkeypatch.setattr(ventana.gestor, "conversacion", lambda c, crear=True: conversacion)
	monkeypatch.setattr(ventana.gestor, "ocupada", lambda c: True)
	monkeypatch.setattr(ventana.gestor, "solicitudes", lambda c=None: [])

	ventana.respuesta.SetValue("también el README")
	ventana.accion_anadir_al_turno()
	assert entregados == ["también el README"]
	assert ventana.respuesta.GetValue() == ""
	assert "sin detenerlo" in anuncios[-1]
	guardados = ventana.almacen.mensajes(clave)
	assert guardados[-1].rol == "usuario" and guardados[-1].datos.get("anadido_al_turno") is True
	assert ventana._mensajes[-1].texto == "también el README"
	ventana.accion_estado_turno()
	assert "esperando a que el agente confirme" in anuncios[-1]

	# Si no llega, el texto vuelve al cuadro (delante de lo que hubiera) y nunca se reenvía solo.
	ventana.respuesta.SetValue("otra cosa")
	ventana._al_evento(clave, Evento(TipoEvento.AVISO, "El turno ya había terminado.", {"devolver_texto": "también el README"}))
	assert ventana.respuesta.GetValue() == "también el README\n\notra cosa"
	assert anuncios[-1] == "El turno ya había terminado."
	assert entregados == ["también el README"]


def test_texto_devuelto_de_otra_sesion_espera_a_que_la_abras(ventana, tmp_path, anuncios):
	abierta = _sesion(ventana, tmp_path, "Abierta")
	otra = _sesion(ventana, tmp_path, "Otra")
	ventana.abrir_sesion(abierta, mover_foco=False)
	ventana._al_evento(otra, Evento(TipoEvento.AVISO, "Codex no lo ha aceptado.", {"devolver_texto": "corrige"}))
	assert ventana.respuesta.GetValue() == ""
	assert any("Al abrir la sesión" in texto for texto in anuncios)
	ventana.abrir_sesion(otra, mover_foco=False)
	assert ventana.respuesta.GetValue() == "corrige"


def test_anadir_al_turno_sin_texto_ni_trabajo(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.accion_anadir_al_turno()
	assert "Escribe primero" in anuncios[-1]
	proveedor = SimpleNamespace(capacidades=SimpleNamespace(anadir_al_turno=True))
	monkeypatch.setattr(ventana.gestor, "proveedor", lambda _id: proveedor)
	ventana.respuesta.SetValue("hola")
	ventana.accion_anadir_al_turno()
	assert "no está trabajando" in anuncios[-1]
	assert ventana.respuesta.GetValue() == "hola"


# ── Lista de tareas (#231) y hallazgos de revisión (#232) ───────────────────────


def test_anuncia_las_tareas_hechas_solo_si_se_activa(ventana, tmp_path, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.almacen.anadir_mensaje(clave, "plan", "[en curso] Leer el código\n[pendiente] Ejecutar las pruebas")
	ventana._al_evento(clave, Evento(TipoEvento.PLAN, "", {"pasos": [("Leer el código", "in_progress"), ("Ejecutar las pruebas", "pending")]}))
	ventana.almacen.anadir_mensaje(clave, "plan", "[hecho] Leer el código\n[en curso] Ejecutar las pruebas")
	ventana._al_evento(clave, Evento(TipoEvento.PLAN, "", {"pasos": [("Leer el código", "completed"), ("Ejecutar las pruebas", "in_progress")]}))
	assert not any("Hecha" in texto for texto in anuncios)  # desactivado de fábrica

	ventana.poner_opcion("anuncios.tareas", True)
	ventana.almacen.anadir_mensaje(clave, "plan", "[hecho] Leer el código\n[hecho] Ejecutar las pruebas")
	ventana._al_evento(clave, Evento(TipoEvento.PLAN, "", {"pasos": [("Leer el código", "completed"), ("Ejecutar las pruebas", "completed")]}))
	assert anuncios[-1] == "Hecha: Ejecutar las pruebas. 2 de 2."
	ventana.accion_estado_turno()
	assert "Todas las tareas hechas (2)." in anuncios[-1]


def test_estado_del_turno_dice_por_que_tarea_va(ventana, tmp_path, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.almacen.anadir_mensaje(clave, "plan", "[hecho] Leer\n[en curso] Probar\n[pendiente] Publicar")
	ventana.accion_estado_turno()
	assert "Tarea 2 de 3: Probar. 1 hechas." in anuncios[-1]


def test_fin_de_turno_resume_los_hallazgos_de_la_revision(ventana, tmp_path, anuncios):
	from app.services.agentes.claude import _detalle_entrada

	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.almacen.anadir_mensaje(clave, "usuario", "revisa el código")
	detalle = _detalle_entrada({"findings": [
		{"file": "a.py", "line": 3, "summary": "Falla con None", "verdict": "CONFIRMED"},
		{"file": "b.py", "line": 9, "summary": "Puede colgarse", "verdict": "PLAUSIBLE"},
	]})
	ventana.almacen.anadir_mensaje(clave, "herramienta", "Informar de los hallazgos", detalle=detalle, herramienta="ReportFindings")
	ventana._al_evento(clave, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	assert "Revisión: 2 hallazgos, 1 confirmado." in anuncios
	# Un turno posterior sin revisión no lo repite.
	anuncios.clear()
	ventana.almacen.anadir_mensaje(clave, "usuario", "gracias")
	ventana._al_evento(clave, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	assert not any("Revisión" in texto for texto in anuncios)


# ── Enviar la respuesta a otra sesión (AIChat #248) ─────────────────────────────


def test_enviar_la_respuesta_a_otra_sesion(ventana, tmp_path, monkeypatch, anuncios):
	from app.ui.dialogos import reenviar as modulo

	origen = _sesion(ventana, tmp_path, "Autora")
	destino = _sesion(ventana, tmp_path, "Revisora")
	ventana.almacen.anadir_mensaje(origen, "asistente", "Propongo usar colas.")
	ventana.abrir_sesion(origen, mover_foco=False)
	esperar_hasta(lambda: any(m.texto == "Propongo usar colas." for m in ventana._mensajes), 10)
	vistos = {}

	class Falso:
		def __init__(self, padre, titulo, destinos, texto, comprobar, trabajando=False):
			vistos.update(titulo=titulo, destinos=list(destinos), texto=texto)
			self.elegido = (0, texto + "\n\n¿Qué te parece?")

		def ShowModal(self):  # noqa: N802 - API de wx
			return wx.ID_OK

		def Destroy(self):  # noqa: N802 - API de wx
			pass

	enviados = []
	monkeypatch.setattr(modulo, "DialogoReenviar", Falso)
	def enviar(clave, texto, *_a, **_k):
		enviados.append((clave, texto))
		ventana.almacen.anadir_mensaje(clave, "usuario", texto)
		return True

	monkeypatch.setattr(ventana, "enviar_mensaje", enviar)
	ventana.reenviar_a_otra_sesion()
	assert vistos["titulo"] == "Autora"
	assert vistos["destinos"][0].startswith("Revisora, Claude")
	assert vistos["texto"].startswith("De Claude, otro agente de IA; te lo reenvía la persona desde su sesión «Autora»:")
	assert enviados == [(destino, vistos["texto"] + "\n\n¿Qué te parece?")]
	assert ventana.sesion_actual == destino
	assert any("Enviado a «Revisora»" in texto for texto in anuncios)
	# Al contestar, la sesión de origen sale la primera: Intro la devuelve.
	ventana.almacen.anadir_mensaje(destino, "asistente", "Me parece bien.")
	ventana.abrir_sesion(destino, mover_foco=False)
	esperar_hasta(lambda: any(m.texto == "Me parece bien." for m in ventana._mensajes), 10)
	ventana.reenviar_a_otra_sesion()
	assert vistos["destinos"][0].startswith("Autora, Claude")
