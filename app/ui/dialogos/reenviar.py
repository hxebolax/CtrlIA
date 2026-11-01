"""Diálogo «Enviar la respuesta a otra sesión»: a qué sesión y el mensaje, para leerlo y cambiarlo antes.

Intro en la lista envía; en el mensaje, Intro empieza una línea nueva y
Ctrl+Intro envía, como en el cuadro de respuesta. Si el mensaje no puede ir a
esa sesión, el diálogo sigue abierto con el motivo: nada de lo escrito se pierde.

La última entrada de la lista es «Nueva sesión...» (AIChat #379): la abre con el
mensaje puesto. Una casilla ofrece además arrancar con la respuesta una
conversación automática entre las dos sesiones; si no se puede con la sesión
elegida, su etiqueta dice por qué en lugar de desaparecer o quedar inaccesible.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import wx

from app.services import dialogo_agentes
from app.services.reenviar import Problema
from app.ui.accesibilidad.dialogos import enfocar, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.comun import _


class DialogoReenviar(wx.Dialog):
	"""Deja en ``elegido`` ``(índice del destino, texto)`` al enviar.

	:param comprobar: ``comprobar(índice, texto)`` dice por qué el mensaje no puede ir allí, o ``""``.
	:param problema_conversacion: ``problema_conversacion(índice)`` dice por qué no se puede arrancar una
		conversación automática con esa sesión, o ``None``. Lo elegido queda en ``conversacion``,
		``max_turnos`` y ``aprobar``.
	"""

	def __init__(self, padre: wx.Window | None, titulo_origen: str, destinos: Sequence[str], texto: str,
			comprobar: Callable[[int, str], str], trabajando: bool = False,
			problema_conversacion: Callable[[int], Problema | None] | None = None) -> None:
		titulo = _("Enviar la respuesta a otra sesión: {t}").format(t=titulo_origen)
		if trabajando:
			# Se dice lo primero: la sesión sigue trabajando y su última respuesta puede ser un paso intermedio.
			titulo += _(" (sigue trabajando; es su última respuesta hasta ahora)")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._comprobar = comprobar
		self._problema_conversacion = problema_conversacion or (lambda _indice: None)
		self.elegido: tuple[int, str] | None = None
		self.conversacion = False
		self.max_turnos = dialogo_agentes.TURNOS_DE_FABRICA
		self.aprobar = False
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_etiqueta, self.destinos = crear_campo(
			panel, _("Enviar &a:"), wx.ListBox, choices=list(destinos), style=wx.LB_SINGLE,
			ayuda=_("Las sesiones de Ctrl+IA a las que puedes enviar la respuesta, de cualquier agente. La primera es la "
				"que te envió la última respuesta, para contestarle con Intro. La última, «Nueva sesión...», abre ese "
				"cuadro con el mensaje puesto, para elegir el agente y la carpeta. Intro envía."),
			sizer=raiz,
		)
		self.destinos.SetMinSize(self.FromDIP(wx.Size(560, 120)))
		if destinos:
			self.destinos.SetSelection(0)
		self._etiqueta_conversar = _("Empezar también una &conversación automática entre las dos sesiones")
		self.conversar = wx.CheckBox(panel, label=self._etiqueta_conversar)
		self.conversar.SetHelpText(_("Además de enviar la respuesta, las dos sesiones siguen hablando solas unos turnos, "
			"empezando por la que la recibe. Los permisos y las preguntas te siguen llegando a ti."))
		raiz.Add(self.conversar, 0, wx.ALL, 5)
		self._etiqueta_turnos, self.turnos = crear_campo(
			panel, _("T&urnos como máximo:"), wx.SpinCtrl, min=dialogo_agentes.TURNOS_MINIMOS,
			max=dialogo_agentes.TURNOS_MAXIMOS, initial=dialogo_agentes.TURNOS_DE_FABRICA,
			ayuda=_("Cuántas respuestas, contando las de las dos sesiones, habrá como mucho en la conversación automática."),
			sizer=raiz, horizontal=True, expandir=False,
		)
		self.casilla_aprobar = wx.CheckBox(panel, label=_("A&probar cada relevo a mano"))
		self.casilla_aprobar.SetHelpText(_("Antes de pasar cada respuesta, Ctrl+IA te avisa y te la enseña: puedes seguir, "
			"cambiarla o parar la conversación."))
		raiz.Add(self.casilla_aprobar, 0, wx.ALL, 5)
		_etiqueta, self.texto = crear_campo(
			panel, _("&Mensaje:"), wx.TextCtrl, value=texto, style=wx.TE_MULTILINE | wx.TE_RICH2,
			ayuda=_("Lo que se enviará. Empieza con una línea que dice qué agente lo escribió, para que el otro agente "
				"no lo tome como tus instrucciones. Puedes cambiarlo o añadir lo tuyo. Intro empieza una línea nueva y "
				"Control+Intro envía."),
			sizer=raiz,
		)
		self.texto.SetMinSize(self.FromDIP(wx.Size(560, 220)))
		fila = wx.BoxSizer(wx.HORIZONTAL)
		enviar = wx.Button(panel, wx.ID_OK, _("&Enviar"))
		enviar.SetHelpText(_("Va a esa sesión y le envía el mensaje, como si lo hubieras escrito allí. Con «Nueva sesión...», "
			"abre ese cuadro con el mensaje puesto."))
		enviar.SetDefault()
		cancelar = wx.Button(panel, wx.ID_CANCEL, _("Cancelar"))
		cancelar.SetHelpText(_("Cierra sin enviar nada. También con Escape."))
		fila.Add(enviar, 0, wx.RIGHT, 6)
		fila.Add(cancelar, 0)
		raiz.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		enviar.Bind(wx.EVT_BUTTON, lambda _e: self.enviar())
		self.destinos.Bind(wx.EVT_LISTBOX_DCLICK, lambda _e: self.enviar())
		self.destinos.Bind(wx.EVT_LISTBOX, lambda _e: self.sincronizar())
		self.conversar.Bind(wx.EVT_CHECKBOX, lambda _e: self.sincronizar())
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		self.sincronizar()
		preparar_dialogo(self, foco_inicial=self.destinos)
		self.CentreOnParent()

	def sincronizar(self) -> None:
		"""La casilla dice por qué no se puede con esa sesión; turnos y aprobación solo cuentan si está marcada."""
		indice = self.destinos.GetSelection()
		problema = self._problema_conversacion(indice) if indice != wx.NOT_FOUND else None
		etiqueta = self._etiqueta_conversar
		if problema is not None:
			etiqueta = _("{e} (no disponible: {m})").format(e=etiqueta, m=problema.corto)
		if self.conversar.GetLabel() != etiqueta:
			self.conversar.SetLabel(etiqueta)
		marcada = self.conversar.GetValue()
		for control in (self._etiqueta_turnos, self.turnos, self.casilla_aprobar):
			control.Enable(marcada)

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		intro = evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
		if intro and evento.ControlDown() and not evento.AltDown() and not evento.ShiftDown():
			self.enviar()
			return
		if intro and not evento.HasAnyModifiers() and wx.Window.FindFocus() is self.destinos:
			self.enviar()
			return
		evento.Skip()

	def enviar(self) -> bool:
		indice = self.destinos.GetSelection()
		if not 0 <= indice < self.destinos.GetCount():
			mostrar_mensaje(self, _("Elige primero en la lista la sesión a la que enviarlo."), self.GetTitle())
			enfocar(self.destinos)
			return False
		texto = self.texto.GetValue().strip()
		problema = _("No hay nada que enviar: el mensaje está vacío.") if not texto else self._comprobar(indice, texto)
		if problema:
			mostrar_mensaje(self, problema, self.GetTitle())
			enfocar(self.texto)
			return False
		if self.conversar.GetValue():
			motivo = self._problema_conversacion(indice)
			if motivo is not None:
				mostrar_mensaje(self, _("No se puede empezar la conversación automática: {m}. Desmarca la casilla para enviar "
					"solo la respuesta, o elige otra sesión.").format(m=motivo.completo), self.GetTitle())
				enfocar(self.conversar)
				return False
			self.conversacion = True
			self.max_turnos = dialogo_agentes.limitar_turnos(self.turnos.GetValue())
			self.aprobar = self.casilla_aprobar.GetValue()
		self.elegido = (indice, texto)
		self.EndModal(wx.ID_OK)
		return True
