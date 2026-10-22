"""Lectura de la información MSAA de una ventana: lo mismo que consulta un lector de pantalla.

Permite escribir pruebas automáticas de accesibilidad («¿este campo tiene
nombre?», «¿esta casilla expone el estado marcado?») sin NVDA. Requiere
``comtypes`` (dependencia de desarrollo) y Windows.

Una prueba MSAA correcta NO demuestra que un lector concreto lo anuncie bien:
es una red de seguridad contra regresiones, no una prueba con usuarios.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, wintypes

import comtypes.client

_oleacc = ctypes.oledll.oleacc
_IAccessible = comtypes.client.GetModule("oleacc.dll").IAccessible
_OBJID_CLIENT = -4

# Roles MSAA de Windows (oleacc), no las constantes wx.ROLE_SYSTEM_*.
ROL_CASILLA = 0x2C
ROL_ELEMENTO_LISTA = 0x22
ROL_TEXTO_EDITABLE = 0x2A
ROL_BOTON = 0x2B
ROL_LISTA = 0x21
ESTADO_MARCADO = 0x10
ESTADO_SOLO_LECTURA = 0x40


def objeto_accesible(ventana):
	"""IAccessible de la zona cliente de una ventana wx."""
	puntero = POINTER(_IAccessible)()
	_oleacc.AccessibleObjectFromWindow(
		wintypes.HWND(int(ventana.GetHandle())), wintypes.DWORD(_OBJID_CLIENT & 0xFFFFFFFF),
		byref(_IAccessible._iid_), byref(puntero),
	)
	return puntero


def propiedades(ventana, hijo: int = 0) -> dict:
	"""Nombre, rol, estado, valor y atajo tal como los ve un lector de pantalla."""
	acc = objeto_accesible(ventana)
	datos = {}
	for clave, propiedad in (
		("nombre", "accName"), ("rol", "accRole"), ("estado", "accState"),
		("valor", "accValue"), ("atajo", "accKeyboardShortcut"), ("descripcion", "accDescription"),
	):
		try:
			datos[clave] = getattr(acc, propiedad)(hijo)
		except Exception:  # noqa: BLE001 - propiedad no implementada por el control
			datos[clave] = None
	return datos
