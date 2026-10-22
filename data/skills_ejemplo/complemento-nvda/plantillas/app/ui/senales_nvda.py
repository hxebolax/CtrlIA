"""Señales de la aplicación para su complemento de NVDA (propiedades de ventana).

La aplicación y el complemento no se importan el uno al otro: se entienden por
una **propiedad de ventana** (SetPropW/GetPropW), que NVDA puede leer desde su
propio proceso. Si el complemento no está instalado, la propiedad no molesta a
nadie; si la aplicación no la pone, el complemento se comporta como NVDA.

Movimiento silencioso: antes de mover el foco por su cuenta (entran filas
nuevas en una lista virtual y hay que recolocar a la persona sobre su
elemento), la aplicación llama a ``marcar_movimiento_silencioso(lista)``. El
valor de la propiedad es GetTickCount(): el complemento ignora ese foco solo
durante unos cientos de milisegundos, así que una marca olvidada caduca sola y
nunca deja muda la navegación normal.

Uso con wxPython::

	from app.ui import senales_nvda

	senales_nvda.marcar_movimiento_silencioso(lista.GetHandle())
	lista.Focus(indice)
	lista.Select(indice)
	# Al destruir la ventana (EVT_WINDOW_DESTROY), limpiar:
	senales_nvda.quitar_marca(lista.GetHandle())

Solo depende de ctypes: funciona sin wx y se puede probar sin NVDA.

PLANTILLA del agente «complemento-nvda».
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

#: Debe coincidir con PROPIEDAD_SILENCIO del módulo del complemento.
PROPIEDAD_SILENCIO = "MiAplicacion.MovimientoSilencioso"

if sys.platform == "win32":
	# Instancias propias: no se cambian los argtypes de ctypes.windll compartido.
	_user32 = ctypes.WinDLL("user32", use_last_error=True)
	_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	_SetPropW = _user32.SetPropW
	_SetPropW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE)
	_SetPropW.restype = wintypes.BOOL
	_GetPropW = _user32.GetPropW
	_GetPropW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
	_GetPropW.restype = wintypes.HANDLE
	_RemovePropW = _user32.RemovePropW
	_RemovePropW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
	_RemovePropW.restype = wintypes.HANDLE
	_GetTickCount = _kernel32.GetTickCount
	_GetTickCount.argtypes = ()
	_GetTickCount.restype = wintypes.DWORD
else:  # pragma: no cover - la aplicación es para Windows
	_user32 = _kernel32 = None


def marcar_movimiento_silencioso(hwnd: int, propiedad: str = PROPIEDAD_SILENCIO) -> bool:
	"""Marca la ventana con la hora actual. Devuelve True si se pudo marcar.

	Llamar JUSTO ANTES de mover el foco: NVDA procesa el evento de foco poco
	después y consulta la marca en ese momento.
	"""
	if _user32 is None or not hwnd:
		return False
	marca = int(_GetTickCount()) or 1  # 0 significaría «sin marca»
	if not _SetPropW(hwnd, propiedad, marca):
		log.debug("SetPropW falló en la ventana %s (error %s)", hwnd, ctypes.get_last_error())
		return False
	return True


def leer_marca(hwnd: int, propiedad: str = PROPIEDAD_SILENCIO) -> int:
	"""Valor actual de la propiedad (0 si no existe). Útil en pruebas y diagnóstico."""
	if _user32 is None or not hwnd:
		return 0
	return int(_GetPropW(hwnd, propiedad) or 0)


def quitar_marca(hwnd: int, propiedad: str = PROPIEDAD_SILENCIO) -> None:
	"""Quita la propiedad. Windows pide quitar las propiedades antes de destruir la ventana."""
	if _user32 is None or not hwnd:
		return
	_RemovePropW(hwnd, propiedad)
