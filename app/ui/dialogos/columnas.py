"""Elegir y ordenar las columnas visibles de una lista.

Una lista de casillas (``CheckListBoxAccesible``: en wxPython 4.3 el lector
anuncia «marcada / no marcada» de serie, y al marcar desde el código se
notifica el cambio) con todas las columnas: primero las activas en su
orden y después las demás. Alt+Arriba y Alt+Abajo (o «Subir» y «Bajar»)
mueven la columna seleccionada y se anuncia la nueva posición.

Al aceptar, ``activas`` tiene los ids de las columnas marcadas en el orden
de la lista (al menos una).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import wx

from app.ui.accesibilidad import voz
from app.ui.accesibilidad.casillas import CheckListBoxAccesible
from app.ui.accesibilidad.dialogos import crear_botonera, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.teclado import menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)


class DialogoColumnas(wx.Dialog):
	"""Columnas visibles y su orden. Tras ``wx.ID_OK``, ``activas`` tiene los ids marcados en orden."""

	def __init__(
		self,
		padre: wx.Window | None,
		disponibles: list[tuple[str, str]],
		activas: list[str],
		predeterminadas: Sequence[str] | None = None,
		titulo: str = "",
		ejemplo: Callable[[list[str]], str] | None = None,
	) -> None:
		"""
		:param disponibles: ``[(id, nombre visible)]`` de todas las columnas, en su orden natural.
		:param activas: ids de las columnas visibles ahora, en su orden.
		:param predeterminadas: columnas que deja marcadas «Restablecer» (por defecto, todas).
		:param titulo: título del cuadro (por defecto, «Columnas de la lista»).
		:param ejemplo: recibe las columnas marcadas en orden y devuelve cómo sonará
			una fila; si se da, se muestra en un campo de solo lectura que se
			actualiza con cada cambio.
		"""
		titulo = titulo or _("Columnas de la lista")
		self._ejemplo = ejemplo
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.nombres: dict[str, str] = {identificador: nombre for identificador, nombre in disponibles}
		self._orden_natural = [identificador for identificador, _nombre in disponibles]
		self._predeterminadas = list(predeterminadas) if predeterminadas is not None else list(self._orden_natural)
		conocidas = [identificador for identificador in activas if identificador in self.nombres]
		self.orden: list[str] = list(dict.fromkeys(conocidas)) + [
			identificador for identificador in self._orden_natural if identificador not in conocidas
		]
		marcadas = set(conocidas)
		self.activas: list[str] = list(dict.fromkeys(conocidas))

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		# «&Columnas» chocaría con «&Cancelar»: se usa la V.
		sizer.Add(wx.StaticText(panel, label=_("Columnas &visibles:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = CheckListBoxAccesible(
			panel, _("Columnas visibles"), [self.nombres[i] for i in self.orden], recordar_posicion=False,
		)
		self.lista.SetHelpText(_(
			"Columnas de la lista, en el orden en que se muestran. La barra espaciadora marca o desmarca la "
			"columna; Alt+flecha arriba y Alt+flecha abajo la mueven."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(320, 220)))
		for posicion, identificador in enumerate(self.orden):
			if identificador in marcadas:
				self.lista.Check(posicion, True)
		if self.orden:
			self.lista.SetSelection(0)

		fila = wx.BoxSizer(wx.HORIZONTAL)
		fila.Add(self.lista, 1, wx.EXPAND | wx.RIGHT, 8)
		columna_botones = wx.BoxSizer(wx.VERTICAL)
		self.boton_subir = self._boton(panel, columna_botones, _("&Subir"),
			_("Sube la columna seleccionada una posición (Alt+flecha arriba)."), lambda: self.mover(-1))
		self.boton_bajar = self._boton(panel, columna_botones, _("&Bajar"),
			_("Baja la columna seleccionada una posición (Alt+flecha abajo)."), lambda: self.mover(1))
		self.boton_restablecer = self._boton(panel, columna_botones, _("&Restablecer"),
			_("Vuelve al orden y a las columnas predeterminados."), self.restablecer)
		fila.Add(columna_botones, 0)
		sizer.Add(fila, 1, wx.EXPAND | wx.ALL, 8)
		self.campo_ejemplo: wx.TextCtrl | None = None
		if ejemplo is not None:
			sizer.Add(wx.StaticText(panel, label=_("&Ejemplo de lo que se leerá:")), 0, wx.LEFT | wx.RIGHT, 8)
			self.campo_ejemplo = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
			self.campo_ejemplo.SetHelpText(_(
				"Cómo sonará una fila de ejemplo con las columnas marcadas y en este orden. Se actualiza al marcar o mover."
			))
			self.campo_ejemplo.SetMinSize(self.FromDIP(wx.Size(320, 60)))
			sizer.Add(self.campo_ejemplo, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
			self.lista.Bind(wx.EVT_CHECKLISTBOX, lambda evento: (self.actualizar_ejemplo(), evento.Skip()))
			self.actualizar_ejemplo()
		sizer.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		for identificador, ayuda in (
			(wx.ID_OK, _("Aplica las columnas marcadas en este orden y cierra.")),
			(wx.ID_CANCEL, _("Cierra sin cambiar las columnas.")),
		):
			boton = panel.FindWindowById(identificador, panel)
			if boton is not None:
				boton.SetHelpText(ayuda)
		panel.SetSizer(sizer)

		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		vincular_menu_contextual(self.lista, self._crear_menu)
		preparar_dialogo(self, foco_inicial=self.lista)
		# Después de preparar_dialogo: este manejador se atiende antes que el suyo.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()

	@staticmethod
	def _boton(panel: wx.Window, sizer: wx.Sizer, etiqueta: str, ayuda: str, accion) -> wx.Button:
		boton = wx.Button(panel, label=etiqueta)
		boton.SetHelpText(ayuda)
		boton.Bind(wx.EVT_BUTTON, lambda _evento: accion())
		sizer.Add(boton, 0, wx.EXPAND | wx.BOTTOM, 4)
		return boton

	# ── Estado ────────────────────────────────────────────────────────────────

	def marcadas(self) -> list[str]:
		"""Ids marcados en el orden actual de la lista."""
		return [identificador for posicion, identificador in enumerate(self.orden) if self.lista.IsChecked(posicion)]

	def actualizar_ejemplo(self) -> None:
		"""Rehace la vista previa de la lectura (si el cuadro la tiene)."""
		if self.campo_ejemplo is not None and self._ejemplo is not None:
			self.campo_ejemplo.ChangeValue(self._ejemplo(self.marcadas()))

	def _rellenar(self, marcadas: set[str], seleccion: int) -> None:
		for posicion, identificador in enumerate(self.orden):
			self.lista.SetString(posicion, self.nombres[identificador])
			if self.lista.IsChecked(posicion) != (identificador in marcadas):
				self.lista.Check(posicion, identificador in marcadas)
		if self.orden:
			self.lista.SetSelection(max(0, min(seleccion, len(self.orden) - 1)))
		self.actualizar_ejemplo()

	# ── Acciones ──────────────────────────────────────────────────────────────

	def mover(self, desplazamiento: int) -> bool:
		"""Mueve la columna seleccionada ``desplazamiento`` posiciones (−1 sube, +1 baja)."""
		posicion = self.lista.GetSelection()
		if posicion == wx.NOT_FOUND:
			voz.hablar(_("No hay ninguna columna seleccionada"))
			return False
		destino = posicion + desplazamiento
		if not 0 <= destino < len(self.orden):
			voz.hablar(_("Ya está la primera") if destino < 0 else _("Ya está la última"))
			return False
		marcadas = set(self.marcadas())
		self.orden[posicion], self.orden[destino] = self.orden[destino], self.orden[posicion]
		self._rellenar(marcadas, destino)
		voz.hablar(_("Movida a la posición {n}").format(n=destino + 1))
		return True

	def alternar(self) -> None:
		"""Marca o desmarca la columna seleccionada."""
		posicion = self.lista.GetSelection()
		if posicion != wx.NOT_FOUND:
			self.lista.Check(posicion, not self.lista.IsChecked(posicion))
			self.actualizar_ejemplo()

	def _crear_menu(self) -> wx.Menu | None:
		"""Menú contextual con las mismas órdenes que las teclas y los botones."""
		if self.lista.GetSelection() == wx.NOT_FOUND:
			return None
		return menu_de_acciones(self.lista, [
			(_("&Marcar o desmarcar\tEspacio"), self.alternar),
			# El atajo va entre paréntesis: Alt+Arriba y Alt+Abajo ya los atiende _al_pulsar.
			(_("&Subir (Alt+Arriba)"), lambda: self.mover(-1)),
			(_("&Bajar (Alt+Abajo)"), lambda: self.mover(1)),
			None,
			(_("&Restablecer"), self.restablecer),
		])

	def restablecer(self) -> None:
		"""Orden natural y columnas predeterminadas marcadas."""
		predeterminadas = [i for i in self._predeterminadas if i in self.nombres]
		self.orden = list(dict.fromkeys(predeterminadas)) + [i for i in self._orden_natural if i not in predeterminadas]
		self._rellenar(set(predeterminadas), 0)
		voz.hablar(_("Columnas restablecidas"))

	def aceptar(self) -> bool:
		"""Guarda en ``activas`` las marcadas y cierra; exige al menos una."""
		marcadas = self.marcadas()
		if not marcadas:
			mostrar_error(self, _("Marca al menos una columna."))
			if self.lista.IsShownOnScreen():
				self.lista.SetFocus()
			return False
		self.activas = marcadas
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		# atajo: Alt+Arriba subir la columna
		# atajo: Alt+Abajo bajar la columna
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		if modificadores == wx.MOD_ALT and tecla in (wx.WXK_UP, wx.WXK_NUMPAD_UP):
			self.mover(-1)
			return
		if modificadores == wx.MOD_ALT and tecla in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
			self.mover(1)
			return
		evento.Skip()
