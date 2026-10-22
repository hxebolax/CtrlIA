"""Selector genérico: un campo para filtrar y una lista para elegir.

Sirve para los comandos de barra, los repositorios de GitHub y cualquier
lista larga. Escribir en «Filtrar» reduce la lista al momento; flecha abajo
desde el filtro baja a la lista; Intro elige.
"""

from __future__ import annotations

from collections.abc import Sequence

import wx

from app.services.gestor import coincide
from app.ui.accesibilidad.dialogos import crear_botonera, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import copiar_texto, menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _


class DialogoSelector(wx.Dialog):
	"""Elige una fila de ``filas``; el índice queda en ``self.indice`` (sobre la lista original).

	Las últimas ``fijas`` filas se ven siempre, aunque no coincidan con el
	filtro (por ejemplo, «Escribir el nombre a mano...»).
	"""

	def __init__(self, padre: wx.Window | None, titulo: str, etiqueta: str, filas: Sequence[Sequence[str]],
			cabeceras: Sequence[str], ayuda: str = "", fijas: int = 0) -> None:
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self._filas = [list(f) for f in filas]
		self._primera_fija = len(self._filas) - max(0, min(int(fijas), len(self._filas)))
		self._visibles: list[int] = list(range(len(self._filas)))
		self.indice: int | None = None
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.filtro = crear_campo(self, _("&Filtrar:"), wx.TextCtrl, sizer=raiz,
			ayuda=_("Escribe para reducir la lista. Flecha abajo pasa a la lista."))
		raiz.Add(wx.StaticText(self, label=etiqueta), 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
		anchos = [220] + [420] * (len(cabeceras) - 1)
		self.lista = ListaAccesible(self, etiqueta.replace("&", "").rstrip(":"), list(zip(cabeceras, anchos, strict=False)))
		self.lista.SetHelpText(ayuda or _("Elige con las flechas y pulsa Intro."))
		self.lista.SetMinSize(self.FromDIP(wx.Size(640, 300)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.Layout()
		self.CentreOnParent()
		self._rellenar()
		self.filtro.Bind(wx.EVT_TEXT, lambda _e: self._rellenar())
		self.filtro.Bind(wx.EVT_KEY_DOWN, self._al_tecla_filtro)
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self._aceptar())
		vincular_menu_contextual(self.lista, self._crear_menu)
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		preparar_dialogo(self, foco_inicial=self.lista if self._filas else self.filtro)

	def _rellenar(self) -> None:
		consulta = self.filtro.GetValue().strip()
		self._visibles = [i for i, f in enumerate(self._filas)
			if not consulta or i >= self._primera_fija or coincide(" ".join(f), consulta)]
		self.lista.reemplazar_filas([self._filas[i] for i in self._visibles], conservar_foco=False)
		if self._visibles:
			self.lista.enfocar_fila(0, anunciar=False)

	def _al_tecla_filtro(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() == wx.WXK_DOWN and self._visibles:
			self.lista.SetFocus()
			return
		if evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self._aceptar()
			return
		evento.Skip()

	def _crear_menu(self) -> wx.Menu | None:
		"""Menú contextual: elegir la fila, copiar su primera columna o la fila entera."""
		fila = self.lista.GetFocusedItem()
		if not 0 <= fila < len(self._visibles):
			return None
		valores = self._filas[self._visibles[fila]]
		return menu_de_acciones(self.lista, [
			(_("&Elegir\tIntro"), self._aceptar),
			(_("&Copiar el nombre"), lambda: copiar_texto(valores[0] if valores else "")),
			(_("Copiar la &fila entera"), lambda: copiar_texto("\t".join(valores))),
		])

	def _aceptar(self) -> None:
		fila = self.lista.GetFocusedItem()
		if 0 <= fila < len(self._visibles):
			self.indice = self._visibles[fila]
			self.EndModal(wx.ID_OK)
		else:
			wx.Bell()


def elegir(padre: wx.Window | None, titulo: str, etiqueta: str, filas: Sequence[Sequence[str]],
		cabeceras: Sequence[str], ayuda: str = "", fijas: int = 0) -> int | None:
	"""Muestra el selector y devuelve el índice elegido o ``None``."""
	dialogo = DialogoSelector(padre, titulo, etiqueta, filas, cabeceras, ayuda, fijas)
	try:
		dialogo.ShowModal()
		return dialogo.indice
	finally:
		dialogo.Destroy()
