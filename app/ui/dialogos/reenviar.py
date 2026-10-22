"""Diálogo «Enviar la respuesta a otra sesión»: a qué sesión y el mensaje, para leerlo y cambiarlo antes.

Intro en la lista envía; en el mensaje, Intro empieza una línea nueva y
Ctrl+Intro envía, como en el cuadro de respuesta. Si el mensaje no puede ir a
esa sesión, el diálogo sigue abierto con el motivo: nada de lo escrito se pierde.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import wx

from app.ui.accesibilidad.dialogos import enfocar, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.comun import _


class DialogoReenviar(wx.Dialog):
	"""Deja en ``elegido`` ``(índice del destino, texto)`` al enviar."""

	def __init__(self, padre: wx.Window | None, titulo_origen: str, destinos: Sequence[str], texto: str,
			comprobar: Callable[[int, str], str], trabajando: bool = False) -> None:
		titulo = _("Enviar la respuesta a otra sesión: {t}").format(t=titulo_origen)
		if trabajando:
			# Se dice lo primero: la sesión sigue trabajando y su última respuesta puede ser un paso intermedio.
			titulo += _(" (sigue trabajando; es su última respuesta hasta ahora)")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._comprobar = comprobar
		self.elegido: tuple[int, str] | None = None
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_etiqueta, self.destinos = crear_campo(
			panel, _("Enviar &a:"), wx.ListBox, choices=list(destinos), style=wx.LB_SINGLE,
			ayuda=_("Las sesiones de Ctrl+IA a las que puedes enviar la respuesta, de cualquier agente. La primera es la "
				"que te envió la última respuesta, para contestarle con Intro. Intro envía."),
			sizer=raiz,
		)
		self.destinos.SetMinSize(self.FromDIP(wx.Size(560, 120)))
		if destinos:
			self.destinos.SetSelection(0)
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
		enviar.SetHelpText(_("Va a esa sesión y le envía el mensaje, como si lo hubieras escrito allí."))
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
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		preparar_dialogo(self, foco_inicial=self.destinos)
		self.CentreOnParent()

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
		self.elegido = (indice, texto)
		self.EndModal(wx.ID_OK)
		return True
