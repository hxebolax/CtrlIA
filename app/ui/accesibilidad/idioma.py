"""Botones y textos estándar de wxWidgets en español.

``wx.Locale(wx.LANGUAGE_SPANISH)`` carga el catálogo ``wxstd.mo`` que trae
wxPython (``wx/locale/es/LC_MESSAGES``) y traduce los botones y diálogos
estándar (Aceptar, Cancelar, Sí, No, Guardar, Abrir...) SIN depender del
idioma de Windows. wxPython añade él solo esa carpeta a la ruta de catálogos
al importarse; se vuelve a añadir por si la versión compilada la mueve.

Importante:

- Crear el objeto DESPUÉS de ``wx.App`` (``App.InitLocale`` reinicia la
  configuración regional al arrancar).
- GUARDAR la referencia durante toda la sesión: si el recolector de basura
  destruye el objeto, se pierde la traducción.
- Los cuadros nativos de Windows (``wx.MessageBox`` sin TaskDialog,
  ``wx.FileDialog``, ``wx.DirDialog``) usan el idioma de Windows, no el de wx.
  Para forzar el español en un ``wx.MessageDialog`` use ``SetYesNoLabels`` /
  ``SetOKCancelLabels`` (ver ``dialogos.py``).
- ``fijar_idioma_espanol`` también fija, si existe, el catálogo gettext de la
  propia aplicación en ``data/locales``.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import logging
from pathlib import Path

import wx

log = logging.getLogger(__name__)


def fijar_idioma_espanol(catalogo_app: str | None = None, carpeta_locales: Path | None = None) -> wx.Locale | None:
	"""Activa el español para wxWidgets (y opcionalmente para la aplicación).

	:param catalogo_app: nombre del catálogo .mo propio (``miaplicacion``), si lo hay.
	:param carpeta_locales: carpeta ``data/locales`` con ``es/LC_MESSAGES/<catalogo>.mo``.
	:return: el ``wx.Locale`` creado (hay que conservarlo) o ``None`` si falla.
	"""
	carpeta_wx = Path(wx.__file__).resolve().parent / "locale"
	if carpeta_wx.is_dir():
		wx.Locale.AddCatalogLookupPathPrefix(str(carpeta_wx))
	if carpeta_locales is not None and carpeta_locales.is_dir():
		wx.Locale.AddCatalogLookupPathPrefix(str(carpeta_locales))
	try:
		idioma = wx.Locale(wx.LANGUAGE_SPANISH)
	except Exception:
		log.warning("No se pudo activar el idioma español en wx; los botones estándar pueden salir en inglés.", exc_info=True)
		return None
	if not idioma.IsOk():
		log.warning("wx.Locale(español) no está disponible en este sistema; se usará el idioma por defecto.")
	if catalogo_app:
		if not idioma.AddCatalog(catalogo_app):
			log.info("No se encontró el catálogo %s.mo en español.", catalogo_app)
	log.debug("Idioma de wx: %s (%s)", idioma.GetName(), idioma.GetCanonicalName())
	return idioma
