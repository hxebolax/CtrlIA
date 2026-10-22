"""Leer del portapapeles lo que se puede adjuntar a un mensaje: imágenes y archivos.

Lo copiado llega en formatos distintos según el programa:

- **Archivos copiados en el Explorador** (``CF_HDROP``): se adjuntan tal cual;
  una imagen va como imagen.
- **«PNG»** (formato registrado): lo ponen los navegadores, las apps Electron y
  la herramienta Recortes. Se guarda sin tocarlo, con su transparencia. Hay
  programas que SOLO ponen este, y antes no se reconocía.
- **Mapa de bits** (``CF_BITMAP``/``CF_DIB``): capturas con Impr Pant y la
  mayoría de programas. Muchas capturas de 32 bits traen el canal alfa a cero:
  guardadas así serían una imagen transparente (vacía), así que se quita.

Si lo copiado trae también texto (una celda de Excel o un párrafo de Word
traen texto y una imagen del mismo contenido), Ctrl+V pega el texto, como en
cualquier programa; la imagen se adjunta a propósito con «Adjuntar lo copiado».
"""

from __future__ import annotations

import logging
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path

import wx

from app.services.agentes.base import Adjunto

log = logging.getLogger(__name__)

#: Firma de un archivo PNG.
FIRMA_PNG = b"\x89PNG\r\n\x1a\n"


@dataclass(slots=True)
class LoCopiado:
	"""Lo que se ha sacado del portapapeles."""

	adjuntos: list[Adjunto] = field(default_factory=list)
	#: Lo copiado trae texto (Ctrl+V lo pega en lugar de adjuntar).
	hay_texto: bool = False
	#: Lo copiado trae una imagen o archivos que no se han adjuntado porque había texto.
	hay_adjuntable: bool = False


def _tipo(ruta: Path) -> str:
	return mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"


def _nombre_png(carpeta: Path) -> Path:
	carpeta.mkdir(parents=True, exist_ok=True)
	ruta = carpeta / time.strftime("pegada_%Y%m%d_%H%M%S.png")
	numero = 2
	while ruta.exists():
		ruta = carpeta / time.strftime(f"pegada_%Y%m%d_%H%M%S_{numero}.png")
		numero += 1
	return ruta


def _archivos(portapapeles: wx.Clipboard) -> list[Adjunto]:
	if not portapapeles.IsSupported(wx.DataFormat(wx.DF_FILENAME)):
		return []
	datos = wx.FileDataObject()
	if not portapapeles.GetData(datos):
		return []
	adjuntos = []
	for nombre in datos.GetFilenames():
		ruta = Path(nombre)
		if ruta.is_file():
			adjuntos.append(Adjunto(ruta, _tipo(ruta)))
		else:
			log.info("Se ignora algo copiado que no es un archivo: %s", ruta.name)
	return adjuntos


def _png(portapapeles: wx.Clipboard, carpeta: Path) -> Adjunto | None:
	formato = wx.DataFormat("PNG")
	if not portapapeles.IsSupported(formato):
		return None
	datos = wx.CustomDataObject(formato)
	if not portapapeles.GetData(datos):
		return None
	contenido = bytes(datos.GetData())
	if not contenido.startswith(FIRMA_PNG):
		log.info("El formato PNG del portapapeles no trae un PNG (%d bytes)", len(contenido))
		return None
	ruta = _nombre_png(carpeta)
	ruta.write_bytes(contenido)
	return Adjunto(ruta, "image/png")


def _mapa_de_bits(portapapeles: wx.Clipboard, carpeta: Path) -> Adjunto | None:
	if not portapapeles.IsSupported(wx.DataFormat(wx.DF_BITMAP)):
		return None
	datos = wx.BitmapDataObject()
	if not portapapeles.GetData(datos):
		return None
	mapa = datos.GetBitmap()
	if not mapa.IsOk():
		return None
	imagen = mapa.ConvertToImage()
	if imagen.HasAlpha():
		alfa = imagen.GetAlphaBuffer()
		if alfa is not None and not any(bytes(alfa)):
			# Captura de 32 bits sin transparencia de verdad: sin esto sería invisible.
			imagen.ClearAlpha()
	ruta = _nombre_png(carpeta)
	if not imagen.SaveFile(str(ruta), wx.BITMAP_TYPE_PNG):
		return None
	return Adjunto(ruta, "image/png")


def leer_adjuntables(carpeta: Path, aunque_haya_texto: bool = False) -> LoCopiado | None:
	"""Imágenes o archivos del portapapeles, guardando las imágenes en ``carpeta``.

	Devuelve ``None`` si Windows no deja abrir el portapapeles (otro programa
	lo tiene). Con texto y sin ``aunque_haya_texto`` no adjunta nada: solo
	dice si había algo adjuntable.
	"""
	portapapeles = wx.TheClipboard
	if not portapapeles.Open():
		log.info("No se pudo abrir el portapapeles para pegar")
		return None
	try:
		resultado = LoCopiado(hay_texto=portapapeles.IsSupported(wx.DataFormat(wx.DF_UNICODETEXT))
			or portapapeles.IsSupported(wx.DataFormat(wx.DF_TEXT)))
		formatos = (wx.DataFormat(wx.DF_FILENAME), wx.DataFormat("PNG"), wx.DataFormat(wx.DF_BITMAP))
		if resultado.hay_texto and not aunque_haya_texto:
			resultado.hay_adjuntable = any(portapapeles.IsSupported(f) for f in formatos)
			return resultado
		try:
			resultado.adjuntos = _archivos(portapapeles)
			if not resultado.adjuntos:
				imagen = _png(portapapeles, carpeta) or _mapa_de_bits(portapapeles, carpeta)
				if imagen is not None:
					resultado.adjuntos = [imagen]
		except OSError:
			log.warning("No se pudo guardar la imagen del portapapeles", exc_info=True)
		return resultado
	finally:
		portapapeles.Close()
