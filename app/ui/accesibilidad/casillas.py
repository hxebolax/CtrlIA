"""Lista de casillas accesible (``wx.CheckListBox``).

Problema: en Windows, ``wx.CheckListBox`` es un ListBox dibujado por la
aplicación y los lectores de pantalla NO saben que cada elemento es una
casilla ni si está marcada: dicen solo el texto.

Solución (la misma que usa NVDA en su propia interfaz, ``CustomCheckListBox``
de ``gui/nvdaControls.py``, y la de SocialRSS, ``AccessibleCheckListBox``):

1. Un ``wx.Accessible`` que expone cada elemento hijo con el rol de casilla
   (``ROLE_SYSTEM_CHECKBUTTON``) y el estado marcado (``STATE_SYSTEM_CHECKED``).
   Se instala SIEMPRE (como SocialRSS): así el lector dice «casilla de
   verificación, marcada» al moverse con las flechas, sea cual sea la versión
   de wxPython. ``forzar_accesible=False`` lo desactiva.
2. Al marcar o desmarcar, ``NotifyWinEvent(EVENT_OBJECT_STATECHANGE)`` para
   que el lector anuncie el nuevo estado en el momento.
3. Anuncio por voz del cambio («Subtítulos, marcada»), interrumpiendo, como
   SocialRSS: es lo que garantiza que se oiga aunque el lector no atienda el
   paso 2. Activado por defecto (``ANUNCIAR_ESTADO``); ``anunciar_estado=False``
   lo quita en una lista concreta. ``lectura`` elige la forma: nombre y
   estado, estado y nombre, o solo el estado.

Extras:

- También se notifica el cambio al marcar DESDE CÓDIGO (``Check``):
  ``EVT_CHECKLISTBOX`` solo salta con acciones del usuario.
- Espacio marca o desmarca (nativo); si no hay ningún elemento elegido, elige
  el primero antes, para que la tecla nunca «no haga nada».
- Ctrl+A marca todas y Ctrl+Mayús+A las desmarca (con un solo anuncio).
- ``alternar`` (para menús contextuales) cambia el elegido, lo anuncia y
  envía ``EVT_CHECKLISTBOX`` como si se hubiera pulsado Espacio.
- Recuerda la posición entre aperturas del mismo diálogo (por nombre).

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins
import ctypes
import logging
from collections.abc import Iterable, Sequence

import wx

from app.ui.accesibilidad import voz
from app.ui.accesibilidad.composicion import LECTURA_NOMBRE_ESTADO, LECTURAS_CASILLA, componer_estado_casilla

log = logging.getLogger(__name__)

_EVENT_OBJECT_STATECHANGE = 0x800A
_OBJID_CLIENT = -4
_POSICIONES: dict[str, int] = {}

#: Anunciar por voz cada cambio de casilla en las listas que no digan otra cosa.
ANUNCIAR_ESTADO = True
#: Forma del anuncio en las listas que no digan otra cosa (``composicion.LECTURAS_CASILLA``).
LECTURA_ESTADO = LECTURA_NOMBRE_ESTADO


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def configurar(anunciar_estado: bool | None = None, lectura: str | None = None) -> None:
	"""Cambia los valores por defecto de las listas de casillas que se creen después."""
	global ANUNCIAR_ESTADO, LECTURA_ESTADO
	if anunciar_estado is not None:
		ANUNCIAR_ESTADO = bool(anunciar_estado)
	if lectura in LECTURAS_CASILLA:
		LECTURA_ESTADO = str(lectura)


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
			# El lector puede preguntar por un elemento que ya no existe (la lista se acaba de vaciar o
			# filtrar): IsChecked con un índice malo es una aserción de wx que salía como «Error inesperado».
			if not 0 <= indice < self._control.GetCount():
				return (wx.ACC_OK, estados)
			if self._control.IsChecked(indice):
				estados |= wx.ACC_STATE_SYSTEM_CHECKED
			if self._control.IsSelected(indice):
				estados |= wx.ACC_STATE_SYSTEM_SELECTED
				if self._control.HasFocus():
					estados |= wx.ACC_STATE_SYSTEM_FOCUSED
		except (RuntimeError, wx.PyAssertionError):
			log.debug("Estado de la casilla %s no disponible", indice, exc_info=True)  # el control se está destruyendo
		return (wx.ACC_OK, estados)


class CheckListBoxAccesible(wx.CheckListBox):
	"""``wx.CheckListBox`` cuyo estado marcado anuncian correctamente los lectores."""

	def __init__(
		self,
		padre: wx.Window,
		nombre: str,
		opciones: Sequence[str] = (),
		anunciar_estado: bool | None = None,
		atajos_marcar_todo: bool = True,
		recordar_posicion: bool = True,
		forzar_accesible: bool | None = None,
		lectura: str | None = None,
		**kwargs,
	) -> None:
		"""Cree ANTES su ``wx.StaticText`` con el mismo padre.

		:param nombre: nombre accesible de la lista.
		:param anunciar_estado: decir por voz «Nombre, marcada» al cambiar una casilla.
			``None``: el valor de ``ANUNCIAR_ESTADO`` (activado).
		:param atajos_marcar_todo: Ctrl+A / Ctrl+Mayús+A.
		:param recordar_posicion: volver al último elemento visitado al reabrir.
		:param forzar_accesible: instalar el ``wx.Accessible`` propio (``None`` o
			``True``: sí, como SocialRSS y NVDA; ``False``: el de wx).
		:param lectura: forma del anuncio (``composicion.LECTURAS_CASILLA``);
			``None``: ``LECTURA_ESTADO``.
		"""
		super().__init__(padre, choices=list(opciones), name=nombre, **kwargs)
		self._anunciar_estado = ANUNCIAR_ESTADO if anunciar_estado is None else bool(anunciar_estado)
		self._lectura = lectura if lectura in LECTURAS_CASILLA else LECTURA_ESTADO
		self._recordar = recordar_posicion
		self._atajos_marcar_todo = atajos_marcar_todo
		if forzar_accesible is None or forzar_accesible:
			try:
				self._accesible = _AccesibleCasillas(self)  # referencia viva por si acaso
				self.SetAccessible(self._accesible)
			except (AttributeError, NotImplementedError, RuntimeError):
				log.debug("No se pudo instalar el accesible de la lista de casillas", exc_info=True)
		self.Bind(wx.EVT_CHECKLISTBOX, self._al_marcar)
		self.Bind(wx.EVT_KEY_DOWN, self._al_pulsar)
		if recordar_posicion:
			self.Bind(wx.EVT_LISTBOX, self._al_seleccionar)
		ayuda = _("Lista de casillas. Muévase con las flechas y marque o desmarque con la barra espaciadora.")
		if atajos_marcar_todo:
			ayuda += " " + _("Control+A marca todas; Control+Mayúsculas+A las desmarca.")
		self.SetHelpText(ayuda)
		if recordar_posicion and self.GetCount():
			self.SetSelection(min(_POSICIONES.get(nombre, 0), self.GetCount() - 1))

	# -- Anuncios -------------------------------------------------------------------

	@property
	def anunciar_estado(self) -> bool:
		return self._anunciar_estado

	@anunciar_estado.setter
	def anunciar_estado(self, valor: bool) -> None:
		self._anunciar_estado = bool(valor)

	@property
	def lectura(self) -> str:
		return self._lectura

	@lectura.setter
	def lectura(self, valor: str) -> None:
		if valor in LECTURAS_CASILLA:
			self._lectura = valor

	def texto_estado(self, indice: int) -> str:
		"""Lo que se anuncia al cambiar la casilla ``indice`` (según ``lectura``)."""
		return componer_estado_casilla(self.GetString(indice), self.IsChecked(indice), self._lectura)

	def _anunciar(self, indice: int) -> None:
		if self._anunciar_estado and 0 <= indice < self.GetCount():
			voz.hablar(self.texto_estado(indice), interrumpir=True, canal=voz.NAVEGACION)

	# -- Eventos --------------------------------------------------------------------

	def _al_marcar(self, evento: wx.CommandEvent) -> None:
		indice = evento.GetInt()
		notificar_cambio_estado(self, indice + 1)
		self._anunciar(indice)
		evento.Skip()

	def _al_seleccionar(self, evento: wx.CommandEvent) -> None:
		if evento.GetSelection() >= 0:
			_POSICIONES[self.GetName()] = evento.GetSelection()
		evento.Skip()

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		if (self._atajos_marcar_todo and tecla == ord("A")
				and modificadores in (wx.MOD_CONTROL, wx.MOD_CONTROL | wx.MOD_SHIFT)):
			self.marcar_todas(not evento.ShiftDown())
			return
		if tecla == wx.WXK_SPACE and modificadores == wx.MOD_NONE and self.GetSelection() == wx.NOT_FOUND and self.GetCount():
			# Sin elemento elegido, Espacio no haría nada: se elige el primero y se marca.
			self.SetSelection(0)
			self.alternar(0)
			return
		evento.Skip()

	# -- Acciones -------------------------------------------------------------------

	def Check(self, item: int, check: bool = True) -> None:  # noqa: N802 - API de wx
		"""Marca desde código y avisa al lector (wx no genera evento en este caso)."""
		super().Check(item, check)
		notificar_cambio_estado(self, item + 1)

	def alternar(self, indice: int | None = None) -> bool:
		"""Marca o desmarca ``indice`` (o el elegido) como Espacio: anuncia y envía ``EVT_CHECKLISTBOX``.

		Devuelve el nuevo estado (``False`` también si no hay elemento).
		"""
		if indice is None:
			indice = self.GetSelection()
		if not 0 <= indice < self.GetCount():
			voz.hablar(_("Lista vacía") if not self.GetCount() else _("Ningún elemento seleccionado"), canal=voz.NAVEGACION)
			return False
		nuevo = not self.IsChecked(indice)
		super().Check(indice, nuevo)
		evento = wx.CommandEvent(wx.wxEVT_CHECKLISTBOX, self.GetId())
		evento.SetEventObject(self)
		evento.SetInt(indice)
		# Se procesa en el momento: notifica al lector, anuncia y avisa a quien escuche el evento.
		self.GetEventHandler().ProcessEvent(evento)
		return nuevo

	def marcar_todas(self, marcar: bool = True) -> None:
		"""Marca o desmarca todo y lo anuncia UNA vez (no una por elemento)."""
		for indice in range(self.GetCount()):
			if self.IsChecked(indice) != marcar:
				self.Check(indice, marcar)
		voz.hablar(_("Todas marcadas") if marcar else _("Todas desmarcadas"), canal=voz.NAVEGACION)

	def indices_marcados(self) -> list[int]:
		"""Posiciones de los elementos marcados."""
		return list(self.GetCheckedItems())

	def marcar_indices(self, indices: Iterable[int]) -> None:
		"""Deja marcadas exactamente esas posiciones (sin anunciar nada)."""
		elegidos = set(indices)
		for indice in range(self.GetCount()):
			marcar = indice in elegidos
			if self.IsChecked(indice) != marcar:
				self.Check(indice, marcar)

	def textos_marcados(self) -> list[str]:
		"""Textos de los elementos marcados."""
		return [self.GetString(indice) for indice in self.GetCheckedItems()]
