"""Aviso ACCESIBLE de errores inesperados.

Cuando ocurre una excepción no controlada, ``registro`` llama al avisador
registrado aquí. El aviso:

- Se muestra en el hilo de la interfaz (``wx.CallAfter``), venga de donde venga.
- Explica qué pasó en lenguaje claro, sin traza, en un campo de SOLO LECTURA
  (el lector de pantalla puede recorrerlo línea a línea y copiarlo).
- Ofrece «Copiar detalles» (tipo, mensaje y origen, ya redactados), «Abrir la
  carpeta de registros» y «Aceptar», con aceleradores y en español.
- Nunca apila diálogos: si llegan diez errores seguidos, se muestra uno y se
  indica cuántos más hubo.

Registro en ``main.py`` tras crear ``wx.App``::

	from app.ui import dialogo_error
	dialogo_error.instalar(nombre_app="MiAplicacion")

PLANTILLA del agente «logging-profesional».
"""

from __future__ import annotations

import builtins
import logging
import os
import threading
from pathlib import Path

import wx

from app.core import registro

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


_candado = threading.Lock()
_estado: dict = {"abierto": False, "pendientes": 0, "nombre_app": "la aplicación", "anunciar": None}


class DialogoError(wx.Dialog):
	"""Diálogo accesible que informa de un error inesperado."""

	def __init__(self, padre: wx.Window | None, resumen: str, detalles: str, ruta_registro: Path | None) -> None:
		super().__init__(padre, title=_("Error inesperado"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self._detalles = detalles
		self._ruta_registro = ruta_registro
		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)

		# Etiqueta ANTES del campo: el lector la usa como nombre del campo.
		etiqueta = wx.StaticText(panel, label=_("&Qué ha pasado:"))
		self.texto = wx.TextCtrl(
			panel, value=resumen, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
			size=(480, 170), name=_("Qué ha pasado"),
		)
		sizer.Add(etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		sizer.Add(self.texto, 1, wx.EXPAND | wx.ALL, 10)

		botones = wx.BoxSizer(wx.HORIZONTAL)
		boton_copiar = wx.Button(panel, label=_("&Copiar detalles"))
		boton_carpeta = wx.Button(panel, label=_("Abrir la carpeta de &registros"))
		boton_aceptar = wx.Button(panel, wx.ID_OK, _("&Aceptar"))
		boton_copiar.SetHelpText(_("Copia al portapapeles el tipo de error y dónde se produjo, para enviarlo al desarrollador."))
		boton_carpeta.SetHelpText(_("Abre en el Explorador la carpeta con los archivos de registro."))
		for boton in (boton_copiar, boton_carpeta, boton_aceptar):
			botones.Add(boton, 0, wx.ALL, 5)
		sizer.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
		panel.SetSizer(sizer)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		boton_aceptar.SetDefault()
		self.SetAffirmativeId(wx.ID_OK)
		self.SetEscapeId(wx.ID_OK)
		boton_copiar.Bind(wx.EVT_BUTTON, self._al_copiar)
		boton_carpeta.Bind(wx.EVT_BUTTON, self._al_abrir_carpeta)
		boton_carpeta.Enable(ruta_registro is not None)
		# El foco inicial va al texto, al principio, para que se lea desde arriba.
		self.Bind(wx.EVT_INIT_DIALOG, self._al_iniciar)
		self.CentreOnParent()

	def _al_iniciar(self, evento: wx.InitDialogEvent) -> None:
		self.texto.SetFocus()
		self.texto.SetInsertionPoint(0)
		evento.Skip()

	def _al_copiar(self, _evento: wx.CommandEvent) -> None:
		if wx.TheClipboard.Open():
			try:
				wx.TheClipboard.SetData(wx.TextDataObject(self._detalles))
			finally:
				wx.TheClipboard.Close()
			_anunciar(_("Detalles copiados al portapapeles."))
		else:
			_anunciar(_("No se pudo acceder al portapapeles."))

	def _al_abrir_carpeta(self, _evento: wx.CommandEvent) -> None:
		if self._ruta_registro is not None:
			try:
				os.startfile(self._ruta_registro.parent)  # noqa: S606 - carpeta propia de la aplicación
			except OSError:
				log.warning("No se pudo abrir la carpeta de registros.", exc_info=True)


def _anunciar(texto: str) -> None:
	"""Anuncia por voz con la función que indicó ``instalar`` (si la hay)."""
	anunciador = _estado.get("anunciar")
	if anunciador is None:
		return
	try:
		anunciador(texto)
	except Exception:  # noqa: BLE001 - un fallo de voz no debe provocar otro error
		log.debug("No se pudo anunciar por voz.", exc_info=True)


def _mostrar(tipo, valor, _rastro, ruta_registro: Path | None) -> None:
	"""Muestra el diálogo (hilo de la interfaz). Nunca apila más de uno."""
	with _candado:
		if _estado["abierto"]:
			_estado["pendientes"] += 1
			return
		_estado["abierto"] = True
	try:
		origen, origen_app = registro.origen_de_excepcion(valor)
		resumen = _(
			"{app} ha encontrado un error inesperado y lo ha guardado en el registro.\n"
			"Puede seguir trabajando; si el problema se repite, envíe al desarrollador "
			"el botón «Copiar detalles» o la carpeta de registros.\n\n"
			"Error: {tipo}: {mensaje}"
		).format(app=_estado["nombre_app"], tipo=tipo.__name__, mensaje=registro.redactar(str(valor)))
		detalles = registro.redactar(
			f"{tipo.__name__}: {valor}\nOrigen: {origen}\nCódigo propio: {origen_app}\n"
			f"Registro: {ruta_registro.name if ruta_registro else '-'}"
		)
		app = wx.GetApp()
		padre = app.GetTopWindow() if app else None
		foco_previo = wx.Window.FindFocus()
		dialogo = DialogoError(padre, resumen, detalles, ruta_registro)
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()
		# Devolver el foco a donde estaba el usuario.
		if foco_previo:
			foco_previo.SetFocus()
	finally:
		with _candado:
			_estado["abierto"] = False
			pendientes, _estado["pendientes"] = _estado["pendientes"], 0
		if pendientes:
			_anunciar(_("Se produjeron {n} errores más; están en el registro.").format(n=pendientes))


def _avisador(tipo, valor, rastro, ruta_registro) -> None:
	"""Avisador para ``registro.establecer_avisador``: seguro desde cualquier hilo."""
	if wx.GetApp() is None:
		return
	wx.CallAfter(_mostrar, tipo, valor, rastro, ruta_registro)


def instalar(nombre_app: str = "", anunciar=None) -> None:
	"""Activa el aviso accesible de errores no controlados (llamar tras crear wx.App).

	:param nombre_app: nombre que aparece en el mensaje.
	:param anunciar: función opcional ``anunciar(texto)`` para confirmar por voz
		acciones como «Detalles copiados» (la de voz del proyecto, si tiene).
	"""
	if nombre_app:
		_estado["nombre_app"] = nombre_app
	_estado["anunciar"] = anunciar
	registro.establecer_avisador(_avisador)
