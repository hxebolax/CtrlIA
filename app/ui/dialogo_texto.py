"""Diálogo accesible para leer textos largos (manual, teclas, novedades, «Acerca de»).

Un texto largo va en un ``TextCtrl`` multilínea de SOLO LECTURA, no en una
``StaticText``: el lector de pantalla lo recorre por líneas, palabras y
caracteres, se puede seleccionar y copiar, y no se pierde nada si no cabe.
"""

from __future__ import annotations

import builtins
import logging
import os
from pathlib import Path

import wx

from app.core import paths

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


class DialogoTexto(wx.Dialog):
	"""Muestra un texto en un campo de solo lectura con «Copiar» opcional y «Cerrar».

	- La etiqueta (``StaticText``) se crea JUSTO ANTES del campo: el lector toma
	  de ella el nombre del control. ``name=`` o ``SetName`` no nombran un
	  control nativo.
	- Escape cierra (el botón «Cerrar» tiene ``wx.ID_CANCEL``).
	- El foco inicial va al texto, al principio, para empezar a leer ya.
	"""

	def __init__(self, padre: wx.Window | None, titulo: str, etiqueta: str, texto: str, copiable: bool = False) -> None:
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self._texto = texto

		etiqueta_texto = wx.StaticText(self, label=etiqueta)
		# TE_RICH2 permite textos de más de 64 KB en Windows y lo leen bien NVDA y JAWS.
		self.campo = wx.TextCtrl(self, value=texto, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
		self.campo.SetHelpText(_("Texto de solo lectura. Muévase con las flechas; Control+A y Control+C para copiarlo."))

		botones = wx.BoxSizer(wx.HORIZONTAL)
		if copiable:
			boton_copiar = wx.Button(self, label=_("&Copiar al portapapeles"))
			boton_copiar.Bind(wx.EVT_BUTTON, self._al_copiar)
			botones.Add(boton_copiar, 0, wx.RIGHT, 8)
		boton_cerrar = wx.Button(self, wx.ID_CANCEL, label=_("C&errar"))
		botones.Add(boton_cerrar)
		self.SetEscapeId(wx.ID_CANCEL)

		principal = wx.BoxSizer(wx.VERTICAL)
		principal.Add(etiqueta_texto, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		principal.Add(self.campo, 1, wx.EXPAND | wx.ALL, 10)
		principal.Add(botones, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		self.SetSizer(principal)
		self.SetSize(self.FromDIP(wx.Size(640, 480)))
		self.CentreOnParent()

		self.campo.SetInsertionPoint(0)
		self.campo.SetFocus()

	def _al_copiar(self, _evento: wx.CommandEvent) -> None:
		"""Copia todo el texto al portapapeles y lo confirma sin mover el foco."""
		if copiar_al_portapapeles(self._texto):
			from app.core import voz

			voz.hablar(_("Copiado al portapapeles"))
		else:
			from app.ui.accesibilidad.dialogos import mostrar_mensaje

			mostrar_mensaje(self, _("No se pudo abrir el portapapeles. Inténtalo de nuevo."), _("Copiar"))


def copiar_al_portapapeles(texto: str) -> bool:
	"""Copia ``texto`` al portapapeles. ``False`` si otro programa lo tiene bloqueado."""
	from app.ui.accesibilidad.teclado import copiar_texto

	return copiar_texto(texto)


def mostrar_texto(padre: wx.Window | None, titulo: str, etiqueta: str, texto: str, copiable: bool = False) -> None:
	"""Abre ``DialogoTexto`` de forma modal y lo destruye al cerrarlo."""
	dialogo = DialogoTexto(padre, titulo, etiqueta, texto, copiable)
	try:
		dialogo.ShowModal()
	finally:
		dialogo.Destroy()


def buscar_documento(nombre: str) -> Path | None:
	"""Busca ``data/docs/<nombre>`` en HTML (preferido), Markdown o texto."""
	carpeta = paths.ruta_documentacion()
	for extension in (".html", ".md", ".txt"):
		ruta = carpeta / f"{nombre}{extension}"
		if ruta.is_file():
			return ruta
	return None


def mostrar_documento(padre: wx.Window | None, nombre: str, titulo: str) -> bool:
	"""Muestra un documento de usuario. Devuelve ``False`` si no existe.

	El HTML se abre en el navegador: el lector de pantalla permite saltar por
	encabezados (tecla H en NVDA) y es la mejor opción para un manual largo.
	Markdown o texto se muestran en ``DialogoTexto``.
	"""
	ruta = buscar_documento(nombre)
	if ruta is None:
		from app.ui.accesibilidad.dialogos import mostrar_mensaje

		mostrar_mensaje(padre, _("No se encuentra el documento «{nombre}» en la carpeta {carpeta}.").format(
			nombre=nombre, carpeta=paths.ruta_documentacion()), titulo)
		return False
	if ruta.suffix == ".html" and hasattr(os, "startfile"):
		try:
			os.startfile(ruta)  # noqa: S606 - documento propio de data/docs
			return True
		except OSError:
			log.exception("No se pudo abrir %s en el navegador; se muestra como texto", ruta)
	try:
		texto = ruta.read_text(encoding="utf-8")
	except (OSError, UnicodeDecodeError):
		log.exception("No se pudo leer %s", ruta)
		from app.ui.accesibilidad.dialogos import mostrar_error

		mostrar_error(padre, _("No se pudo leer {ruta}.").format(ruta=ruta), titulo)
		return False
	mostrar_texto(padre, titulo, _("&Contenido:"), texto)
	return True
