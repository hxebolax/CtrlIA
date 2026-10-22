"""«Conversación automática entre sesiones»: el diálogo para prepararla y lo que la une a la ventana principal.

El ciclo vive en ``app.services.dialogo_agentes`` (sin wx). Aquí están el
diálogo (qué sesión contesta, la tarea, los turnos y el papel de cada una) y
las órdenes de la ventana: empezar, detener, ver la conversación intercalada y
pasar al relevo los eventos de los agentes.

En el diálogo, Control+Intro empieza desde cualquier campo; en la tarea, Intro
escribe una línea nueva. Si algo falta, el diálogo sigue abierto con el motivo.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

import wx

from app.services import dialogo_agentes, reenviar
from app.services.agentes.base import Evento
from app.services.dialogo_agentes import ConversacionEntreSesiones, Participante, Preparacion
from app.services.gestor import MensajeVista
from app.ui import avisos
from app.ui.accesibilidad.dialogos import enfocar, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.comun import _, nombre_agente

log = logging.getLogger(__name__)

TITULO = _("Conversación automática entre sesiones")


class DialogoConversacionSesiones(wx.Dialog):
	"""Deja en ``elegido`` una ``Preparacion`` al pulsar «Empezar»."""

	def __init__(self, padre: wx.Window | None, origen: str, destinos: Sequence[str],
			comprobar: Callable[[int], str] | None = None) -> None:
		super().__init__(padre, title=TITULO, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=TITULO)
		self._comprobar = comprobar or (lambda _indice: "")
		self.elegido: Preparacion | None = None
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.origen = crear_campo(
			panel, _("&Primera sesión, la que empieza:"), wx.TextCtrl, value=origen, style=wx.TE_READONLY,
			ayuda=_("La sesión abierta. Recibe la tarea y trabaja primero; después, cada respuesta pasa a la otra sesión."),
			sizer=raiz,
		)
		_e, self.destinos = crear_campo(
			panel, _("&Segunda sesión:"), wx.ListBox, choices=list(destinos), style=wx.LB_SINGLE,
			ayuda=_("La sesión de Ctrl+IA que contesta a la primera, de cualquier agente: por ejemplo, Codex para revisar "
				"lo que escribe Claude. Tiene que estar parada."),
			sizer=raiz,
		)
		self.destinos.SetMinSize(self.FromDIP(wx.Size(560, 110)))
		if destinos:
			self.destinos.SetSelection(0)
		_e, self.tarea = crear_campo(
			panel, _("&Tarea para la primera sesión:"), wx.TextCtrl, style=wx.TE_MULTILINE | wx.TE_RICH2,
			ayuda=_("Lo que le pides a la primera sesión. Es el primer mensaje de la conversación. Intro empieza una línea "
				"nueva y Control+Intro empieza la conversación."),
			sizer=raiz,
		)
		self.tarea.SetMinSize(self.FromDIP(wx.Size(560, 140)))
		_e, self.turnos = crear_campo(
			panel, _("T&urnos como máximo:"), wx.SpinCtrl, min=dialogo_agentes.TURNOS_MINIMOS,
			max=dialogo_agentes.TURNOS_MAXIMOS, initial=dialogo_agentes.TURNOS_DE_FABRICA,
			ayuda=_("Cuántas respuestas, contando las de las dos sesiones, habrá como mucho. Termina antes si una de ellas "
				"escribe {m} para dar el trabajo por terminado. De 2 a 20.").format(m=dialogo_agentes.MARCA_FIN),
			sizer=raiz, horizontal=True, expandir=False,
		)
		_e, self.instruccion_a = crear_campo(
			panel, _("Instrucción para la p&rimera sesión (opcional):"), wx.TextCtrl,
			ayuda=_("Su papel en la conversación, por ejemplo: «escribe el código y corrige lo que te señalen»."),
			sizer=raiz,
		)
		_e, self.instruccion_b = crear_campo(
			panel, _("Instrucción para la segu&nda sesión (opcional):"), wx.TextCtrl,
			ayuda=_("Su papel en la conversación, por ejemplo: «revisa y señala fallos, sin cambiar archivos»."),
			sizer=raiz,
		)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		empezar = wx.Button(panel, wx.ID_OK, _("&Empezar"))
		empezar.SetHelpText(_("Envía la tarea a la primera sesión y empieza la conversación. Los permisos y las preguntas "
			"de los agentes te siguen llegando a ti: la conversación se pausa hasta que respondas."))
		empezar.SetDefault()
		cancelar = wx.Button(panel, wx.ID_CANCEL, _("Cancelar"))
		cancelar.SetHelpText(_("Cierra sin empezar nada. También con Escape."))
		fila.Add(empezar, 0, wx.RIGHT, 6)
		fila.Add(cancelar, 0)
		raiz.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		empezar.Bind(wx.EVT_BUTTON, lambda _e: self.empezar())
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		preparar_dialogo(self, foco_inicial=self.destinos)
		self.CentreOnParent()

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		intro = evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
		if intro and evento.ControlDown() and not evento.AltDown() and not evento.ShiftDown():
			self.empezar()
			return
		evento.Skip()

	def empezar(self) -> bool:
		indice = self.destinos.GetSelection()
		if not 0 <= indice < self.destinos.GetCount():
			mostrar_mensaje(self, _("Elige primero en la lista la segunda sesión."), self.GetTitle())
			enfocar(self.destinos)
			return False
		problema = self._comprobar(indice)
		if problema:
			mostrar_mensaje(self, problema, self.GetTitle())
			enfocar(self.destinos)
			return False
		tarea = self.tarea.GetValue().strip()
		if not tarea:
			mostrar_mensaje(self, _("Escribe la tarea para la primera sesión: es el mensaje con el que empieza todo."),
				self.GetTitle())
			enfocar(self.tarea)
			return False
		self.elegido = Preparacion(indice, tarea, dialogo_agentes.limitar_turnos(self.turnos.GetValue()),
			self.instruccion_a.GetValue().strip(), self.instruccion_b.GetValue().strip())
		self.EndModal(wx.ID_OK)
		return True


# ----------------------------------------------------------------------
# Órdenes de la ventana principal
# ----------------------------------------------------------------------


def actual(ventana: Any) -> ConversacionEntreSesiones | None:
	"""La conversación en marcha o la última que hubo (para verla)."""
	return ventana.__dict__.get("_conversacion_sesiones")


def empezar(ventana: Any) -> ConversacionEntreSesiones | None:
	"""Prepara y empieza una conversación con la sesión abierta como primera."""
	gestor = ventana.gestor
	fila = ventana.fila_de(ventana.sesion_actual) if ventana.sesion_actual else None
	if fila is None or not fila.propia or gestor.proveedor(fila.proveedor) is None:
		avisos.anunciar(_("Abre primero una sesión de Ctrl+IA: será la que empiece la conversación."), interrumpir=True)
		return None
	anterior = actual(ventana)
	if anterior is not None and anterior.activa:
		mostrar_mensaje(ventana, _("Ya hay una conversación entre sesiones en marcha. {r} Detenla antes con Sesión, "
			"Detener la conversación entre sesiones.").format(r=anterior.resumen()), TITULO)
		return None
	if gestor.ocupada(fila.clave):
		mostrar_mensaje(ventana, _("«{t}» está trabajando. Espera a que termine su turno, o detenla, y vuelve a "
			"empezar la conversación.").format(t=fila.titulo), TITULO)
		return None
	destinos = reenviar.destinos(ventana._todas, fila.clave, lambda f: gestor.proveedor(f.proveedor) is not None)
	if not destinos:
		mostrar_mensaje(ventana, _("No hay otra sesión con la que conversar. Puede ser cualquier sesión de Ctrl+IA, de "
			"cualquier agente (no las de otros programas, ni las ocultas o archivadas). Crea una con Archivo, Nueva "
			"sesión."), TITULO)
		return None
	nombres = [_("{t}, {a}, {c}").format(t=d.titulo, a=nombre_agente(d.proveedor), c=d.carpeta or _("sin carpeta"))
		for d in destinos]

	def comprobar(indice: int) -> str:
		if gestor.ocupada(destinos[indice].clave):
			return _("«{t}» está trabajando. Elige otra sesión o espera a que termine su turno.").format(
				t=destinos[indice].titulo)
		return ""

	origen = _("{t}, {a}").format(t=fila.titulo, a=nombre_agente(fila.proveedor))
	dialogo = DialogoConversacionSesiones(ventana, origen, nombres, comprobar)
	try:
		if dialogo.ShowModal() != wx.ID_OK or dialogo.elegido is None:
			return None
		eleccion = dialogo.elegido
	finally:
		dialogo.Destroy()
	destino = destinos[eleccion.destino]
	a = Participante(fila.clave, nombre_agente(fila.proveedor), fila.titulo, fila.proveedor, eleccion.instruccion_a)
	b = Participante(destino.clave, nombre_agente(destino.proveedor), destino.titulo, destino.proveedor,
		eleccion.instruccion_b)
	conversacion = crear(ventana, a, b, eleccion.tarea, eleccion.max_turnos)
	conversacion.empezar()
	ventana.actualizar_barra_estado()
	return conversacion


def crear(ventana: Any, a: Participante, b: Participante, tarea: str, max_turnos: int) -> ConversacionEntreSesiones:
	"""El relevo unido a la ventana: envía como un mensaje normal y lee lo nuevo de cada sesión."""
	almacen = ventana.almacen
	#: Último mensaje ya visto de cada sesión: la respuesta de un turno es lo que llega después.
	desde: dict[str, int] = {}
	for clave in (a.clave, b.clave):
		guardados = almacen.mensajes(clave)
		desde[clave] = int(guardados[-1].id) if guardados else 0

	def enviar(clave: str, texto: str) -> bool:
		return bool(ventana.enviar_mensaje(clave, texto, anunciar=False))

	def ultima_respuesta(clave: str) -> MensajeVista | None:
		nuevos = almacen.mensajes(clave, desde.get(clave, 0))
		if nuevos:
			desde[clave] = int(nuevos[-1].id)
		return reenviar.mensaje_a_reenviar([MensajeVista(m.rol, m.texto, m.fecha, id=m.id) for m in nuevos])

	def avisar(texto: str, importante: bool) -> None:
		if importante:
			avisos.notificar(texto, TITULO, padre=ventana)
		else:
			avisos.anunciar(texto)

	def al_terminar(_conversacion: ConversacionEntreSesiones) -> None:
		if ventana and not getattr(ventana, "_saliendo", False):
			ventana.actualizar_barra_estado()

	conversacion = ConversacionEntreSesiones(a, b, tarea, max_turnos, enviar, ultima_respuesta, avisar, al_terminar)
	ventana.__dict__["_conversacion_sesiones"] = conversacion
	return conversacion


def detener(ventana: Any) -> bool:
	conversacion = actual(ventana)
	if conversacion is None or not conversacion.activa:
		avisos.anunciar(_("No hay ninguna conversación entre sesiones en marcha."), interrumpir=True)
		return False
	conversacion.detener()
	return True


def detener_si_participa(ventana: Any, clave: str | None) -> bool:
	"""Control+punto en una de sus sesiones, aunque esté parada esperando a la otra, la detiene."""
	conversacion = actual(ventana)
	if conversacion is None or not conversacion.activa or not conversacion.participa(clave):
		return False
	conversacion.detener()
	return True


def ver(ventana: Any) -> bool:
	from app.ui.dialogo_texto import mostrar_texto

	conversacion = actual(ventana)
	if conversacion is None:
		avisos.anunciar(_("Todavía no ha habido ninguna conversación entre sesiones. Empieza una con "
			"Control+Alt+Mayúsculas+R."), interrumpir=True)
		return False
	mostrar_texto(ventana, _("Conversación entre sesiones"), _("&Lo que se han dicho:"), conversacion.texto_combinado(),
		copiable=True)
	return True


def al_evento(ventana: Any, clave: str, evento: Evento) -> None:
	"""Pasa al relevo los eventos de los agentes (ya en el hilo de la interfaz)."""
	conversacion = actual(ventana)
	if conversacion is None or not conversacion.activa:
		return
	try:
		conversacion.al_evento(clave, evento)
	except Exception:  # noqa: BLE001 - un fallo del relevo nunca debe cerrar la ventana
		log.exception("Falló la conversación entre sesiones")
		conversacion.terminar(_("ha fallado algo dentro de Ctrl+IA; mira el registro"), error=True)
