"""Cuadro «Sonidos»: probar cada sonido, activarlo o desactivarlo, abrir la carpeta propia o importar un paquete.

Una lista de casillas con un elemento por evento («Mensaje enviado», «El
agente te necesita»...). La barra espaciadora activa o desactiva el sonido;
Intro lo reproduce (aunque esté desactivado, para probarlo). Cada elemento
dice de dónde sale su sonido: del esquema de Ctrl+IA o de tu carpeta propia.

Los cambios se guardan al pulsar Aceptar. El esquema y el volumen están en
Preferencias, pestaña Sonidos.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import wx

from app.core.configuracion import Configuracion
from app.ui import avisos
from app.ui.accesibilidad.casillas import CheckListBoxAccesible
from app.ui.accesibilidad.dialogos import confirmar, crear_botonera, mostrar_error, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)


class DialogoSonidos(wx.Dialog):
	"""Lista de sonidos de Ctrl+IA con casillas; ``aceptar`` guarda los marcados."""

	def __init__(self, padre: wx.Window | None, config: Configuracion) -> None:
		super().__init__(padre, title=_("Sonidos"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Sonidos"))
		self._config = config
		self.eventos = [nombre for nombre, _descripcion in avisos.EVENTOS_SONIDO]
		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.texto_esquema = wx.StaticText(panel, label=self._describir_esquema())
		sizer.Add(self.texto_esquema, 0, wx.ALL, 8)

		sizer.Add(wx.StaticText(panel, label=_("&Sonidos:")), 0, wx.LEFT | wx.RIGHT, 8)
		self.lista = CheckListBoxAccesible(panel, _("Sonidos"), self._textos(), recordar_posicion=False,
			atajos_marcar_todo=True)
		self.lista.SetHelpText(_(
			"Un sonido por cada cosa que pasa. La barra espaciadora lo activa o lo desactiva; Intro lo reproduce para "
			"oírlo. Control+A los activa todos y Control+Mayúsculas+A los desactiva. Tecla Aplicaciones: más acciones."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(460, 220)))
		for posicion, nombre in enumerate(self.eventos):
			if bool(config.obtener(f"sonidos.{nombre}", True)):
				self.lista.Check(posicion, True)
		if self.eventos:
			self.lista.SetSelection(0)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
		vincular_menu_contextual(self.lista, self._crear_menu)

		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_reproducir = wx.Button(panel, label=_("&Reproducir"))
		self.boton_reproducir.SetHelpText(_("Reproduce el sonido elegido (también con Intro en la lista)."))
		self.boton_reproducir.Bind(wx.EVT_BUTTON, lambda _evento: self.reproducir())
		fila.Add(self.boton_reproducir, 0, wx.RIGHT, 8)
		self.boton_carpeta = wx.Button(panel, label=_("Abrir la carpeta de sonidos &propios"))
		self.boton_carpeta.SetHelpText(_(
			"Abre data\\sounds\\propios. Copia allí tus archivos WAV con el nombre de cada evento (por ejemplo "
			"respuesta.wav) y elige el esquema «Propio» en Preferencias, pestaña Sonidos."
		))
		self.boton_carpeta.Bind(wx.EVT_BUTTON, lambda _evento: self.abrir_carpeta_propia())
		fila.Add(self.boton_carpeta, 0, wx.RIGHT, 8)
		self.boton_importar = wx.Button(panel, label=_("&Importar un paquete de sonidos..."))
		self.boton_importar.SetHelpText(_(
			"Elige un archivo ZIP con sonidos WAV. Se instalan en tu carpeta propia los que se llamen como un sonido de "
			"Ctrl+IA (por ejemplo respuesta.wav) y se te dice cuáles no se han usado."
		))
		self.boton_importar.Bind(wx.EVT_BUTTON, lambda _evento: self.importar_paquete())
		fila.Add(self.boton_importar, 0)
		sizer.Add(fila, 0, wx.LEFT | wx.RIGHT, 8)

		sizer.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(sizer)
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		preparar_dialogo(self, foco_inicial=self.lista)
		# Después de preparar_dialogo: este manejador se atiende antes que el suyo.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.CentreOnParent()

	# ── Textos ────────────────────────────────────────────────────────────────

	def _describir_esquema(self) -> str:
		if not self._config.obtener("anuncios.sonidos", True):
			estado = _("Los sonidos están desactivados (F9 en la ventana principal los activa).")
		else:
			estado = _("Los sonidos están activados.")
		esquema = self._config.obtener("sonidos.esquema", "predeterminado")
		nombre = _("Propio") if esquema == "propio" else _("Ctrl+IA")
		return _("{estado} Esquema: {esquema}. Volumen: {volumen} %.").format(
			estado=estado, esquema=nombre, volumen=self._config.obtener("sonidos.volumen", 100))

	def _origen(self, nombre: str) -> str:
		ruta = avisos.ruta_sonido(nombre)
		if ruta is None:
			return _("sin archivo")
		if ruta.parent == avisos.carpeta_sonidos_propios():
			return _("propio")
		return _("de Ctrl+IA")

	def _textos(self) -> list[str]:
		descripciones = dict(avisos.EVENTOS_SONIDO)
		return [f"{_(descripciones[nombre])} ({self._origen(nombre)})" for nombre in self.eventos]

	# ── Acciones ──────────────────────────────────────────────────────────────

	def elegido(self) -> str | None:
		indice = self.lista.GetSelection()
		return self.eventos[indice] if 0 <= indice < len(self.eventos) else None

	def reproducir(self) -> None:
		nombre = self.elegido()
		if nombre is None:
			wx.Bell()
			return
		if avisos.ruta_sonido(nombre) is None:
			avisos.anunciar(_("Este sonido no tiene archivo."), interrumpir=True)
			return
		avisos.sonar(nombre, forzar=True)

	def alternar(self) -> None:
		indice = self.lista.GetSelection()
		if 0 <= indice < len(self.eventos):
			self.lista.Check(indice, not self.lista.IsChecked(indice))

	def abrir_carpeta_propia(self) -> None:
		carpeta = avisos.carpeta_sonidos_propios()
		try:
			carpeta.mkdir(parents=True, exist_ok=True)
			os.startfile(carpeta)  # noqa: S606 - carpeta propia de la aplicación
		except OSError as error:
			log.warning("No se pudo abrir la carpeta de sonidos propios", exc_info=True)
			mostrar_error(self, _("No se pudo abrir la carpeta {carpeta}: {error}").format(carpeta=carpeta, error=error))

	def importar_paquete(self, archivo: Path | None = None) -> bool:
		"""Instala los sonidos de un ZIP en la carpeta propia y ofrece usar el esquema «Propio»."""
		if archivo is None:
			with wx.FileDialog(self, _("Importar un paquete de sonidos"), wildcard=_("Archivos ZIP (*.zip)|*.zip"),
					style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialogo:
				if dialogo.ShowModal() != wx.ID_OK:
					return False
				archivo = Path(dialogo.GetPath())
		try:
			instalados, descartados = avisos.importar_paquete_sonidos(archivo)
		except ValueError as error:
			mostrar_error(self, str(error), _("No se pudo importar el paquete"))
			return False
		except OSError as error:
			log.exception("No se pudo importar el paquete de sonidos %s", archivo)
			mostrar_error(self, _("No se pudo copiar los sonidos: {error}").format(error=error),
				_("No se pudo importar el paquete"))
			return False
		descripciones = dict(avisos.EVENTOS_SONIDO)
		nombres = [_(descripciones[n]) if n in descripciones else n for n in instalados]
		mensaje = _("Se han instalado {n} sonidos: {lista}.").format(n=len(instalados), lista=", ".join(nombres))
		if descartados:
			mensaje += "\n\n" + _("No se han usado {n} archivos porque no se llaman como un sonido de Ctrl+IA o no son "
				"WAV: {lista}.").format(n=len(descartados), lista=", ".join(descartados[:30]))
		if self._config.obtener("sonidos.esquema", "predeterminado") != "propio":
			mensaje += "\n\n" + _("¿Usar ya tus sonidos propios (esquema «Propio»)?")
			if confirmar(self, mensaje, _("Paquete de sonidos importado")) == wx.ID_YES:
				self._config.establecer("sonidos.esquema", "propio")
		else:
			mostrar_mensaje(self, mensaje, _("Paquete de sonidos importado"))
		self._refrescar_textos()
		return True

	def _refrescar_textos(self) -> None:
		"""Vuelve a decir de dónde sale cada sonido sin perder las casillas ni el sitio."""
		marcados = [self.lista.IsChecked(i) for i in range(len(self.eventos))]
		seleccion = self.lista.GetSelection()
		self.lista.Set(self._textos())
		for indice, marcado in enumerate(marcados):
			self.lista.Check(indice, marcado)
		if 0 <= seleccion < len(self.eventos):
			self.lista.SetSelection(seleccion)
		self.texto_esquema.SetLabel(self._describir_esquema())

	def activos(self) -> dict[str, bool]:
		"""Estado de cada sonido según las casillas."""
		return {f"sonidos.{nombre}": self.lista.IsChecked(posicion) for posicion, nombre in enumerate(self.eventos)}

	def aceptar(self) -> None:
		try:
			self._config.establecer_varias(self.activos())
		except (ValueError, KeyError, OSError) as error:
			log.exception("No se pudieron guardar los sonidos")
			mostrar_error(self, _("No se pudieron guardar los sonidos: {error}").format(error=error))
			return
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()

	def _crear_menu(self) -> wx.Menu | None:
		if self.elegido() is None:
			return None
		menu = wx.Menu()
		for etiqueta, accion in (
			(_("&Reproducir\tIntro"), self.reproducir),
			(_("&Activar o desactivar\tEspacio"), self.alternar),
			(_("Abrir la carpeta de sonidos &propios"), self.abrir_carpeta_propia),
			(_("&Importar un paquete de sonidos..."), self.importar_paquete),
		):
			item = menu.Append(wx.ID_ANY, etiqueta)
			self.lista.Bind(wx.EVT_MENU, lambda _evento, a=accion: a(), item)
		return menu

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		if (evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not evento.HasAnyModifiers()
				and wx.Window.FindFocus() is self.lista):
			self.reproducir()
			return
		evento.Skip()
