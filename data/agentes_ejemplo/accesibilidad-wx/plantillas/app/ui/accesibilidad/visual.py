"""Accesibilidad visual: escalado (DPI), alto contraste y colores del sistema.

La accesibilidad no es solo el lector de pantalla: personas con baja visión
usan ampliación, escalado de Windows al 150-200 % y temas de alto contraste.

- ``activar_conciencia_dpi()``: llamar ANTES de crear ``wx.App``. Sin ella,
  Windows estira la ventana y el texto sale borroso al escalar.
- ``alto_contraste_activo()``: para no pintar colores propios en ese modo.
- ``color_sistema()``: colores del tema en lugar de colores fijos.
- Medidas con ``ventana.FromDIP(n)``, fuentes del sistema y sizers
  (``SetSizerAndFit``); nunca tamaños fijos en píxeles.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)


def activar_conciencia_dpi() -> None:
	"""Declara el proceso consciente del DPI por monitor (antes de crear wx.App)."""
	try:
		# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4 (Windows 10 1703+).
		if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
			return
	except (AttributeError, OSError):
		pass
	try:
		ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
	except (AttributeError, OSError):
		try:
			ctypes.windll.user32.SetProcessDPIAware()
		except (AttributeError, OSError):
			log.debug("No se pudo declarar la conciencia de DPI.", exc_info=True)


class _ALTO_CONTRASTE(ctypes.Structure):
	_fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint), ("lpszDefaultScheme", ctypes.c_wchar_p)]


def alto_contraste_activo() -> bool:
	"""Indica si Windows tiene activado un tema de alto contraste."""
	try:
		datos = _ALTO_CONTRASTE(ctypes.sizeof(_ALTO_CONTRASTE), 0, None)
		ctypes.windll.user32.SystemParametersInfoW(0x0042, datos.cbSize, ctypes.byref(datos), 0)  # SPI_GETHIGHCONTRAST
		return bool(datos.dwFlags & 0x1)  # HCF_HIGHCONTRASTON
	except (AttributeError, OSError):
		return False


def color_sistema(nombre: str = "texto"):
	"""Color del tema actual: ``texto``, ``fondo``, ``seleccion``, ``texto_seleccion``, ``desactivado``."""
	import wx

	indices = {
		"texto": wx.SYS_COLOUR_WINDOWTEXT,
		"fondo": wx.SYS_COLOUR_WINDOW,
		"seleccion": wx.SYS_COLOUR_HIGHLIGHT,
		"texto_seleccion": wx.SYS_COLOUR_HIGHLIGHTTEXT,
		"desactivado": wx.SYS_COLOUR_GRAYTEXT,
		"boton": wx.SYS_COLOUR_BTNFACE,
	}
	return wx.SystemSettings.GetColour(indices.get(nombre, wx.SYS_COLOUR_WINDOWTEXT))
