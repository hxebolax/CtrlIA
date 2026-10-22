"""Diálogo «Acerca de»: nombre, versión y datos técnicos útiles para soporte.

No se usa ``wx.adv.AboutBox``: su ventana nativa mezcla etiquetas estáticas
que el lector no siempre recorre y no deja copiar el texto. Aquí va todo en
un campo de solo lectura con «Copiar al portapapeles», que es lo que el
usuario necesita para pegar en un correo de soporte.
"""

from __future__ import annotations

import builtins
import platform
import sys

import wx

from app.core import paths
from app.core.version import APP_VERSION, AUTOR, NOMBRE_APLICACION
from app.ui.dialogo_texto import mostrar_texto


def _(texto: str) -> str:
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def texto_acerca_de() -> str:
	"""Compone el texto del diálogo (sin wx en la lógica, para poder probarlo)."""
	lineas = [
		f"{NOMBRE_APLICACION} {APP_VERSION}",
		_("Autor: {autor}").format(autor=AUTOR),
		"",
		_("Versión de Python: {version} ({bits})").format(version=platform.python_version(), bits=platform.architecture()[0]),
		_("Versión de wxPython: {version}").format(version=wx.version()),
		_("Sistema: {sistema}").format(sistema=platform.platform()),
		_("Versión compilada: {valor}").format(valor=_("sí") if paths.CONGELADA else _("no")),
		_("Carpeta de datos: {ruta}").format(ruta=paths.ruta_datos()),
		_("Ejecutable: {ruta}").format(ruta=sys.executable),
	]
	return "\n".join(lineas)


def mostrar_acerca_de(padre: wx.Window | None) -> None:
	"""Abre el diálogo «Acerca de» de forma modal."""
	mostrar_texto(
		padre,
		_("Acerca de {nombre}").format(nombre=NOMBRE_APLICACION),
		_("&Información:"),
		texto_acerca_de(),
		copiable=True,
	)
