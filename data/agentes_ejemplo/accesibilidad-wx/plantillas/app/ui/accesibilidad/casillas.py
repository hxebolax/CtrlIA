"""Lista de casillas accesible (``wx.CheckListBox``).

Problema: en Windows, ``wx.CheckListBox`` es un ListBox dibujado por la
aplicación y los lectores de pantalla NO saben que cada elemento es una
casilla ni si está marcada: dicen solo el texto.

Solución (la misma que usa NVDA en su propia interfaz, ``CustomCheckListBox``
de ``gui/nvdaControls.py``):

1. Un ``wx.Accessible`` que expone cada elemento hijo con el rol de casilla
   (``ROLE_SYSTEM_CHECKBUTTON``) y el estado marcado (``STATE_SYSTEM_CHECKED``).
2. Al marcar o desmarcar, ``NotifyWinEvent(EVENT_OBJECT_STATECHANGE)`` para
   que el lector anuncie el nuevo estado en el momento.

wxPython 4.3 (wxWidgets 3.3) ya lo hace de serie: el ``wx.Accessible`` propio
solo se instala en versiones anteriores (o si se fuerza).

Extras:

- También se notifica el cambio al marcar DESDE CÓDIGO (``Check``):
  ``EVT_CHECKLISTBOX`` solo salta con acciones del usuario.
- Ctrl+A marca todas y Ctrl+Mayús+A las desmarca (con un solo anuncio).
- Anuncio de respaldo del estado por voz, DESACTIVADO por defecto: con el
  paso 2 el lector ya lo dice; actívelo solo si comprueba que no lo hace.
- Recuerda la posición entre aperturas del mismo diálogo (por nombre).

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins
import ctypes
import logging
from collections.abc import Sequence

import wx

from app.ui.accesibilidad import voz
from app.ui.accesibilidad.composicion import componer_estado_casilla

log = logging.getLogger(__name__)

_EVENT_OBJECT_STATECHANGE = 0x800A
_OBJID_CLIENT = -4
_POSICIONES: dict[str, int] = {}


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def notificar_cambio_estado(ventana: wx.Window, id_hijo: int) -> None:
	"""Avisa a los lectores de que cambió el estado del hijo ``id_hijo`` (1 = primero)."""
	try:
		ctypes.windll.user32.NotifyWinEvent(
			_EVENT_OBJECT_STATECHANGE, ctypes.c_void_p(int(ventana.GetHandle())), _OBJID_CLIENT, id_hijo
		)
	except (AttributeError, OSError):
		log.debug("NotifyWinEvent no está disponible.", exc_info=True)


class _AccesibleCasillas(wx.Accessible):
	"""Expone los elementos de un CheckListBox como casillas de verificación."""

	def __init__(self, control: CheckListBoxAccesible) -> None:
		super().__init__(control)
		self._control = control

	def GetRole(self, childId: int):  # noqa: N802,N803 - API de wx
		if childId == 0:
			return super().GetRole(childId)
		return (wx.ACC_OK, wx.ROLE_SYSTEM_CHECKBUTTON)

	def GetState(self, childId: int):  # noqa: N802,N803 - API de wx
		if childId == 0:
			return super().GetState(childId)
		indice = childId - 1
		estados = wx.ACC_STATE_SYSTEM_SELECTABLE | wx.ACC_STATE_SYSTEM_FOCUSABLE
		try:
			if self._control.IsChecked(indice):
				estados |= wx.ACC_STATE_SYSTEM_CHECKED
			if self._control.IsSelected(indice):
				estados |= wx.ACC_STATE_SYSTEM_SELECTED
				if self._control.HasFocus():
					estados |= wx.ACC_STATE_SYSTEM_FOCUSED
		except RuntimeError:
			pass  # el control se está destruyendo
		return (wx.ACC_OK, estados)


class CheckListBoxAccesible(wx.CheckListBox):
	"""``wx.CheckListBox`` cuyo estado marcado anuncian correctamente los lectores."""

	def __init__(
		self,
		padre: wx.Window,
		nombre: str,
		opciones: Sequence[str] = (),
		anunciar_estado: bool = False,
		atajos_marcar_todo: bool = True,
		recordar_posicion: bool = True,
		forzar_accesible: bool | None = None,
		**kwargs,
	) -> None:
		"""Cree ANTES su ``wx.StaticText`` con el mismo padre.

		:param nombre: nombre accesible de la lista.
		:param anunciar_estado: respaldo por voz del cambio (desactivado: el lector ya lo dice).
		:param atajos_marcar_todo: Ctrl+A / Ctrl+Mayús+A.
		:param recordar_posicion: volver al último elemento visitado al reabrir.
		:param forzar_accesible: instalar el ``wx.Accessible`` propio. Por defecto
			solo en wxPython < 4.3 (4.3 ya expone el estado de serie).
		"""
		super().__init__(padre, choices=list(opciones), name=nombre, **kwargs)
		self._anunciar_estado = anunciar_estado
		self._recordar = recordar_posicion
		if forzar_accesible is None:
			forzar_accesible = wx.VERSION < (4, 3)
		if forzar_accesible:
			self._accesible = _AccesibleCasillas(self)  # referencia viva por si acaso
			self.SetAccessible(self._accesible)
		self.Bind(wx.EVT_CHECKLISTBOX, self._al_marcar)
		if atajos_marcar_todo:
			self.Bind(wx.EVT_KEY_DOWN, self._al_pulsar)
		if recordar_posicion:
			self.Bind(wx.EVT_LISTBOX, self._al_seleccionar)
		ayuda = _("Lista de casillas. Muévase con las flechas y marque o desmarque con la barra espaciadora.")
		if atajos_marcar_todo:
			ayuda += " " + _("Control+A marca todas; Control+Mayúsculas+A las desmarca.")
		self.SetHelpText(ayuda)
		if recordar_posicion and self.GetCount():
			self.SetSelection(min(_POSICIONES.get(nombre, 0), self.GetCount() - 1))

	def _al_marcar(self, evento: wx.CommandEvent) -> None:
		indice = evento.GetInt()
		notificar_cambio_estado(self, indice + 1)
		if self._anunciar_estado:
			voz.hablar(componer_estado_casilla(self.GetString(indice), self.IsChecked(indice)), canal=voz.NAVEGACION)
		evento.Skip()

	def _al_seleccionar(self, evento: wx.CommandEvent) -> None:
		if evento.GetSelection() >= 0:
			_POSICIONES[self.GetName()] = evento.GetSelection()
		evento.Skip()

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() == ord("A") and evento.GetModifiers() in (wx.MOD_CONTROL, wx.MOD_CONTROL | wx.MOD_SHIFT):
			self.marcar_todas(not evento.ShiftDown())
			return
		evento.Skip()

	def Check(self, item: int, check: bool = True) -> None:  # noqa: N802 - API de wx
		"""Marca desde código y avisa al lector (wx no genera evento en este caso)."""
		super().Check(item, check)
		notificar_cambio_estado(self, item + 1)

	def marcar_todas(self, marcar: bool = True) -> None:
		"""Marca o desmarca todo y lo anuncia UNA vez (no una por elemento)."""
		for indice in range(self.GetCount()):
			if self.IsChecked(indice) != marcar:
				self.Check(indice, marcar)
		voz.hablar(_("Todas marcadas") if marcar else _("Todas desmarcadas"), canal=voz.NAVEGACION)

	def textos_marcados(self) -> list[str]:
		"""Textos de los elementos marcados."""
		return [self.GetString(indice) for indice in self.GetCheckedItems()]
