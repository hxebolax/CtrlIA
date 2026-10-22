"""Icono de la bandeja del sistema con menú accesible.

- El icono solo existe mientras la ventana está oculta en la bandeja: al
  restaurarla se quita. Así no queda un icono permanente que el usuario no
  pidió, y el estado «oculta ↔ icono visible» es fácil de razonar.
- El menú se construye en ``CreatePopupMenu`` (wx lo pide en cada clic
  derecho) con etiquetas en español y ``&``.
- Desde el teclado: Windows+B lleva a la bandeja, las flechas al icono, e
  Intro o la tecla Aplicaciones lo activan o abren el menú.
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Callable

import wx
import wx.adv

from app.core import paths

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


class IconoBandeja(wx.adv.TaskBarIcon):
	"""Icono de bandeja; las acciones se inyectan desde la ventana principal."""

	def __init__(self, texto: str, al_restaurar: Callable[[], None], al_salir: Callable[[], None]) -> None:
		super().__init__()
		self._texto = texto
		self._al_restaurar = al_restaurar
		self._al_salir = al_salir
		# Un clic o un doble clic restauran: la acción más frecuente, a un gesto.
		self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, self._restaurar)
		self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._restaurar)

	def mostrar(self) -> bool:
		"""Pone el icono en la bandeja. El texto emergente es lo que lee el lector."""
		return bool(self.SetIcon(_icono_aplicacion(), self._texto))

	def quitar(self) -> None:
		"""Quita el icono y destruye el objeto (hay que llamarlo siempre al cerrar)."""
		if self.IsIconInstalled():
			self.RemoveIcon()
		self.Destroy()

	def CreatePopupMenu(self) -> wx.Menu:  # noqa: N802 - nombre impuesto por wx
		"""Menú contextual del icono."""
		menu = wx.Menu()
		elemento_mostrar = menu.Append(wx.ID_ANY, _("&Mostrar la ventana"))
		menu.AppendSeparator()
		elemento_salir = menu.Append(wx.ID_EXIT, _("&Salir"))
		menu.Bind(wx.EVT_MENU, self._restaurar, elemento_mostrar)
		menu.Bind(wx.EVT_MENU, lambda _evento: self._al_salir(), elemento_salir)
		return menu

	def _restaurar(self, _evento: wx.Event | None = None) -> None:
		self._al_restaurar()


def _icono_aplicacion() -> wx.Icon:
	"""``data/icons/app.ico`` si existe; si no, un icono genérico de wx."""
	ruta = paths.ruta_iconos() / "app.ico"
	if ruta.is_file():
		icono = wx.Icon(str(ruta), wx.BITMAP_TYPE_ICO)
		if icono.IsOk():
			return icono
		log.warning("El icono %s no es válido; se usa el genérico", ruta)
	icono = wx.Icon()
	icono.CopyFromBitmap(wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16)))
	return icono
