"""Accesibilidad visual: escalado (DPI), alto contraste y colores del sistema.

La accesibilidad no es solo el lector de pantalla: personas con baja visión
usan ampliación, escalado de Windows al 150-200 % y temas de alto contraste.

- ``activar_conciencia_dpi()``: llamar ANTES de crear ``wx.App``. Sin ella,
  Windows estira la ventana y el texto sale borroso al escalar.
- ``alto_contraste_activo()``: para no pintar colores propios en ese modo.
- ``color_sistema()``: colores del tema en lugar de colores fijos.
- ``aplicar_tema()``: claro, oscuro o como Windows (modo oscuro de wx 3.3),
  nunca por encima de un tema de alto contraste.
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
		log.debug("SetProcessDpiAwarenessContext no está disponible; se prueba la API anterior.", exc_info=True)
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


#: Tema elegido al arrancar (``claro``, ``oscuro`` o ``sistema``) y si se activó el modo oscuro.
_tema = {"elegido": "claro", "oscuro": False}


def windows_en_modo_oscuro() -> bool:
	"""Indica si Windows usa el modo oscuro para las aplicaciones."""
	try:
		import winreg

		with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
				r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as clave:
			valor, _tipo = winreg.QueryValueEx(clave, "AppsUseLightTheme")
		return int(valor) == 0
	except (ImportError, OSError, ValueError):
		return False


def decidir_tema_oscuro(tema: str, alto_contraste: bool, windows_oscuro: bool) -> bool:
	"""Si hay que pintar en oscuro. Con alto contraste, nunca: mandan los colores de Windows."""
	if alto_contraste:
		return False
	if tema == "oscuro":
		return True
	return tema == "sistema" and windows_oscuro


def aplicar_tema(aplicacion, tema: str) -> bool:
	"""Activa el modo oscuro de wx si procede. Llamar tras crear ``wx.App`` y ANTES de la primera ventana.

	Devuelve ``True`` si la aplicación queda en oscuro. Los nombres, roles y
	estados que lee el lector no cambian: solo los colores.
	"""
	_tema["elegido"] = tema
	oscuro = decidir_tema_oscuro(tema, alto_contraste_activo(), windows_en_modo_oscuro())
	if oscuro:
		try:
			# 1 = DarkMode_Always: «Como Windows» ya se ha resuelto arriba.
			oscuro = bool(aplicacion.MSWEnableDarkMode(1))
		except (AttributeError, TypeError):
			log.info("Esta versión de wx no admite el modo oscuro.", exc_info=True)
			oscuro = False
	_tema["oscuro"] = oscuro
	log.info("Tema de colores: %s (oscuro: %s)", tema, oscuro)
	return oscuro


def tema_oscuro_activo() -> bool:
	"""Si la aplicación se está pintando en oscuro (para las páginas HTML del visor)."""
	return bool(_tema["oscuro"])


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
