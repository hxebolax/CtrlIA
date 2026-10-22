"""Elegir qué sesiones se exportan a un archivo para llevarlas a otro equipo.

``DialogoExportarSesiones`` muestra un desplegable «Qué sesiones» (la elegida,
las que se ven en la lista, todas, o las de un grupo) y debajo esas sesiones
con una casilla cada una, todas marcadas: Espacio desmarca las que no quieras.
Tras ``wx.ID_OK``, ``claves`` tiene las marcadas e ``incluir_herramientas``
dice si se exporta lo que hicieron las herramientas.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import wx

from app.services.gestor import FilaSesion
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import enfocar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _, fecha_relativa, nombre_agente

log = logging.getLogger(__name__)

AVISO_PRIVACIDAD = _(
	"El archivo lleva todo lo que se dijo en esas sesiones: tus mensajes, las respuestas y, si lo marcas, "
	"lo que hicieron las herramientas (órdenes, su salida y los cambios en archivos), donde a veces aparecen "
	"contraseñas o claves. Guárdalo en un sitio seguro y no lo compartas. Ctrl+IA no lo sube a ningún sitio: "
	"llevarlo al otro equipo es cosa tuya (un lápiz USB, una carpeta compartida...)."
)


class DialogoExportarSesiones(wx.Dialog):
	"""Qué sesiones exportar.

	:param alcances: ``[(nombre, filas)]``: cada opción del desplegable con sus sesiones.
	:param inicial: opción elegida al abrir.
	"""

	def __init__(self, padre: wx.Window | None, alcances: Sequence[tuple[str, list[FilaSesion]]], inicial: int = 0,
			leer_preferencias: Callable | None = None) -> None:
		titulo = _("Exportar sesiones a un archivo")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.alcances = list(alcances)
		self.filas: list[FilaSesion] = []
		self.claves: list[str] = []
		self.incluir_herramientas = True

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_alcance = crear_campo(panel, _("&Qué sesiones:"), wx.Choice, sizer=raiz,
			choices=[nombre for nombre, _f in self.alcances],
			ayuda=_("Elige qué sesiones se exportan: la elegida, las que se ven en la lista, todas o las de un grupo. "
				"Debajo aparecen con una casilla cada una."))
		self.campo_alcance.SetSelection(max(0, min(inicial, len(self.alcances) - 1)))
		self.campo_alcance.Bind(wx.EVT_CHOICE, lambda _e: self.rellenar())

		self.etiqueta_lista = wx.StaticText(panel, label=_("&Sesiones que se exportarán:"))
		raiz.Add(self.etiqueta_lista, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = ListaAccesible(panel, _("Sesiones que se exportarán"),
			[(_("Título"), 280), (_("Agente"), 100), (_("Origen"), 140), (_("Última actividad"), 160)],
			con_casillas=True, leer_preferencias=leer_preferencias)
		self.lista.SetHelpText(_(
			"Sesiones que se exportarán, con una casilla cada una. Espacio marca o desmarca la sesión; "
			"solo se exportan las marcadas."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(620, 240)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
		vincular_menu_contextual(self.lista, self._crear_menu)

		fila_botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_todas = wx.Button(panel, label=_("Marcar &todas"))
		self.boton_todas.SetHelpText(_("Marca todas las sesiones de la lista."))
		self.boton_todas.Bind(wx.EVT_BUTTON, lambda _e: self.marcar(True))
		self.boton_ninguna = wx.Button(panel, label=_("&Desmarcar todas"))
		self.boton_ninguna.SetHelpText(_("Quita la marca de todas las sesiones de la lista."))
		self.boton_ninguna.Bind(wx.EVT_BUTTON, lambda _e: self.marcar(False))
		fila_botones.Add(self.boton_todas, 0, wx.RIGHT, 6)
		fila_botones.Add(self.boton_ninguna, 0)
		raiz.Add(fila_botones, 0, wx.LEFT | wx.RIGHT, 8)

		self.casilla_herramientas = wx.CheckBox(panel, label=_("Incluir lo que hicieron las &herramientas (órdenes, su salida y cambios en archivos)"))
		self.casilla_herramientas.SetValue(True)
		self.casilla_herramientas.SetHelpText(_(
			"Si la desmarcas, cada acción de una herramienta se exporta solo con su descripción, sin la salida ni "
			"las diferencias de los archivos: el archivo ocupa menos y lleva menos datos privados."
		))
		raiz.Add(self.casilla_herramientas, 0, wx.ALL, 8)

		_e, self.campo_aviso = crear_campo(panel, _("A&ntes de exportar:"), wx.TextCtrl, sizer=raiz,
			value=AVISO_PRIVACIDAD, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
			ayuda=_("Aviso sobre los datos privados que puede llevar el archivo."))
		self.campo_aviso.SetMinSize(self.FromDIP(wx.Size(-1, 80)))

		botones = wx.StdDialogButtonSizer()
		self.boton_exportar = wx.Button(panel, wx.ID_OK, _("E&xportar..."))
		self.boton_exportar.SetHelpText(_("Pide dónde guardar el archivo y exporta las sesiones marcadas."))
		self.boton_exportar.SetDefault()
		self.boton_cancelar = wx.Button(panel, wx.ID_CANCEL, _("Cancelar"))
		self.boton_cancelar.SetHelpText(_("Cierra sin exportar nada."))
		botones.AddButton(self.boton_exportar)
		botones.AddButton(self.boton_cancelar)
		botones.Realize()
		raiz.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Bind(wx.EVT_BUTTON, lambda _e: self.aceptar(), id=wx.ID_OK)
		self.rellenar()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.campo_alcance)

	def rellenar(self) -> None:
		"""Pone en la lista las sesiones de la opción elegida, todas marcadas."""
		indice = self.campo_alcance.GetSelection()
		self.filas = list(self.alcances[indice][1]) if 0 <= indice < len(self.alcances) else []
		self.lista.reemplazar_filas([
			[f.titulo, nombre_agente(f.proveedor), f.nombre_origen, fecha_relativa(f.actividad)] for f in self.filas
		], conservar_foco=False)
		self.lista.marcar_todas(True)
		self.etiqueta_lista.SetLabel(_("&Sesiones que se exportarán ({n}):").format(n=len(self.filas)))
		self.lista.SetName(_("Sesiones que se exportarán ({n})").format(n=len(self.filas)))
		if self.filas:
			self.lista.enfocar_fila(0, anunciar=False)

	def _crear_menu(self) -> wx.Menu | None:
		if not self.filas:
			return None
		menu = wx.Menu()

		def anadir(etiqueta: str, accion: Callable[[], object]) -> None:
			menu.Bind(wx.EVT_MENU, lambda _e: accion(), menu.Append(wx.ID_ANY, etiqueta))

		anadir(_("Marcar o &desmarcar esta sesión\tEspacio"), self.alternar_actual)
		anadir(_("Marcar &todas"), lambda: self.marcar(True))
		anadir(_("&Quitar todas las marcas"), lambda: self.marcar(False))
		return menu

	def alternar_actual(self) -> None:
		fila = self.lista.fila_actual()
		if 0 <= fila < len(self.filas):
			self.lista.CheckItem(fila, not self.lista.IsItemChecked(fila))

	def marcar(self, marcar: bool) -> None:
		self.lista.marcar_todas(marcar)
		voz.hablar(_("Todas marcadas") if marcar else _("Todas desmarcadas"))

	def marcadas(self) -> list[FilaSesion]:
		return [self.filas[i] for i in self.lista.filas_marcadas() if 0 <= i < len(self.filas)]

	def aceptar(self) -> bool:
		elegidas = self.marcadas()
		if not elegidas:
			mostrar_error(self, _("No hay ninguna sesión marcada. Marca con Espacio las que quieras exportar."),
				_("Exportar sesiones"))
			enfocar(self.lista)
			return False
		self.claves = [f.clave for f in elegidas]
		self.incluir_herramientas = self.casilla_herramientas.GetValue()
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True
