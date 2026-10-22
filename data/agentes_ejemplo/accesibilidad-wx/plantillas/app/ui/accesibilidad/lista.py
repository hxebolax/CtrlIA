"""Lista multicolumna accesible (``wx.ListCtrl`` en modo informe).

- Control NATIVO de Windows (SysListView32): el lector anuncia por sí solo la
  fila al moverse con arriba/abajo. La aplicación NO vuelve a leer la fila al
  cambiar de selección (provocaría doble lectura).
- Texto COMPLETO de cada celda en un almacén paralelo: Windows recorta el
  texto de las columnas (unos 260-511 caracteres) y ``GetItemText`` no sirve
  para leerlo entero.
- Navegación LÓGICA por columnas, sin mover la selección:
	- Izquierda/Derecha: columna anterior/siguiente (en el orden visible).
	- Ctrl+1…Ctrl+9: lee directamente esa columna; Ctrl+0 la décima.
	- Inicio/Fin con Ctrl+Mayús: primera/última columna.
- Qué se anuncia (número de fila, cabecera, contenido) es configurable con
  ``leer_preferencias`` y se consulta EN CADA ANUNCIO: un cambio en
  Preferencias se aplica a las listas ya abiertas sin reiniciar.
- Columnas protegidas (datos sensibles): nunca se leen, se dice «no disponible».
- Modo VIRTUAL (``LC_VIRTUAL``) para miles de filas sin bloquear la interfaz,
  con la misma API.
- Inserciones y borrados que conservan el elemento en el que está el usuario
  (en modo virtual se recoloca el foco en silencio).
- Casillas opcionales (``EnableCheckBoxes``) con anuncio del estado.
- Intro activa el elemento (``EVT_LIST_ITEM_ACTIVATED``), también en diálogos.
- Barra espaciadora: comportamiento nativo salvo que se indique otra acción.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins
import ctypes
from collections.abc import Callable, Sequence

import wx

from app.ui.accesibilidad import voz
from app.ui.accesibilidad.composicion import PreferenciasLectura, componer_anuncio_celda, componer_estado_casilla


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


class ListaAccesible(wx.ListCtrl):
	"""``wx.ListCtrl`` accesible con navegación por columnas."""

	def __init__(
		self,
		padre: wx.Window,
		nombre: str,
		columnas: Sequence[tuple[str, int]] = (),
		estilo: int = wx.LC_REPORT | wx.LC_SINGLE_SEL,
		virtual: bool = False,
		con_casillas: bool = False,
		leer_preferencias: Callable[[], PreferenciasLectura] | None = None,
		preferencias_locales: PreferenciasLectura = PreferenciasLectura(),
		columnas_protegidas: Sequence[int] = (),
		accion_espacio: Callable[[int], None] | None = None,
		anunciar_casillas: bool = True,
		propiedad_silencio: str = "",
	) -> None:
		"""Crea la lista. Cree ANTES su ``wx.StaticText`` con el mismo padre.

		:param nombre: nombre accesible (lo anuncia el lector al llegar a la lista).
		:param columnas: ``[(cabecera, ancho), ...]``.
		:param estilo: debe incluir ``wx.LC_REPORT``.
		:param virtual: modo virtual para listas grandes (no admite casillas).
		:param con_casillas: una casilla por fila (``EnableCheckBoxes``).
		:param leer_preferencias: devuelve las preferencias globales vigentes
			(se llama en cada anuncio; normalmente lee la configuración).
		:param preferencias_locales: restricciones propias de esta lista.
		:param columnas_protegidas: índices de columnas que nunca se leen.
		:param accion_espacio: qué hacer con Espacio (recibe la fila). ``None`` =
			comportamiento nativo (en listas con casillas, marcar/desmarcar).
		:param anunciar_casillas: decir «marcada/no marcada» al cambiar una casilla.
			Compruebe con NVDA que no se oye dos veces; si el lector ya lo dice, póngalo a False.
		:param propiedad_silencio: nombre de la propiedad de ventana que un
			complemento de NVDA consulta para no leer un movimiento automático
			del foco. Vacío = no se usa.
		"""
		self._virtual = bool(virtual)
		if self._virtual:
			estilo |= wx.LC_VIRTUAL
		super().__init__(padre, style=estilo, name=nombre)
		self._celdas: dict[int, dict[int, str]] = {}
		self._num_filas = 0
		self._columna = 0
		self._leer_preferencias = leer_preferencias or PreferenciasLectura
		self.preferencias_locales = preferencias_locales
		self.columnas_protegidas = set(columnas_protegidas)
		self._accion_espacio = accion_espacio
		self._anunciar_casillas = anunciar_casillas
		self._propiedad_silencio = propiedad_silencio
		self._con_casillas = bool(con_casillas) and not self._virtual
		for indice, (cabecera, ancho) in enumerate(columnas):
			self.InsertColumn(indice, cabecera, width=ancho)
		if self._con_casillas:
			self.EnableCheckBoxes(True)
			self.Bind(wx.EVT_LIST_ITEM_CHECKED, self._al_cambiar_casilla)
			self.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._al_cambiar_casilla)
		self.Bind(wx.EVT_KEY_DOWN, self._al_pulsar)
		self.SetHelpText(_(
			"Lista. Arriba y abajo cambian de elemento; izquierda y derecha recorren las columnas; "
			"Control más un número lee esa columna; Intro abre el elemento."
		))

	# ── Modo virtual: wx pide el texto de las celdas visibles ──────────────────

	def OnGetItemText(self, item: int, column: int) -> str:  # noqa: N802 - API de wx
		return self._celdas.get(item, {}).get(column, "")

	def OnGetItemImage(self, item: int) -> int:  # noqa: N802 - API de wx
		return -1

	def OnGetItemColumnImage(self, item: int, column: int) -> int:  # noqa: N802 - API de wx
		return -1

	# ── Datos ───────────────────────────────────────────────────────────────────

	def numero_filas(self) -> int:
		"""Número de filas en cualquier modo."""
		return self._num_filas if self._virtual else self.GetItemCount()

	def texto_celda(self, fila: int, columna: int) -> str:
		"""Texto ÍNTEGRO de una celda (no el recortado por Windows).

		Si la fila se añadió con los métodos nativos (``InsertItem``/``SetItem``)
		en lugar de los de esta clase, se recurre al texto del control.
		"""
		celdas = self._celdas.get(fila)
		if celdas is not None and columna in celdas:
			return celdas[columna]
		if not self._virtual and 0 <= fila < self.GetItemCount():
			return self.GetItemText(fila, columna)
		return ""

	def valores_fila(self, fila: int) -> list[str]:
		"""Textos íntegros de todas las columnas de una fila."""
		return [self.texto_celda(fila, c) for c in range(self.GetColumnCount())]

	def limpiar(self) -> None:
		"""Vacía la lista."""
		self._celdas.clear()
		if self._virtual:
			self._num_filas = 0
			self.SetItemCount(0)
		else:
			self.DeleteAllItems()

	def agregar_fila(self, valores: Sequence[str]) -> int:
		"""Añade una fila al final y devuelve su índice.

		Si es la primera fila, recibe el foco: una lista con elementos pero sin
		elemento enfocado se anuncia solo como «lista», sin contenido.
		"""
		fila = self.insertar_fila(self.numero_filas(), valores)
		if self.numero_filas() == 1 and self.GetFocusedItem() == wx.NOT_FOUND:
			self._mover_foco_silencioso(0)
		return fila

	def reemplazar_filas(self, filas: Sequence[Sequence[str]], conservar_foco: bool = True) -> None:
		"""Sustituye todo el contenido de una vez (la forma eficiente de recargar).

		Conserva la fila enfocada por posición si sigue existiendo, o la última.
		"""
		foco = self.GetFocusedItem() if conservar_foco else wx.NOT_FOUND
		self.Freeze()
		try:
			self._celdas = {i: {c: v or "" for c, v in enumerate(fila)} for i, fila in enumerate(filas)}
			if self._virtual:
				self._num_filas = len(filas)
				self.SetItemCount(self._num_filas)
				self.Refresh()
			else:
				self.DeleteAllItems()
				for indice, fila in enumerate(filas):
					self._insertar_nativa(indice, fila)
		finally:
			self.Thaw()
		if self.numero_filas():
			# Siempre un elemento enfocado: sin él, el lector dice solo «lista».
			destino = 0 if foco == wx.NOT_FOUND else min(foco, self.numero_filas() - 1)
			self.enfocar_fila(destino, anunciar=False)

	def insertar_fila(self, posicion: int, valores: Sequence[str]) -> int:
		"""Inserta una fila en ``posicion`` sin que el usuario pierda su elemento."""
		posicion = max(0, min(posicion, self.numero_filas()))
		self._celdas = {(k + 1 if k >= posicion else k): v for k, v in self._celdas.items()}
		self._celdas[posicion] = {c: v or "" for c, v in enumerate(valores)}
		if self._virtual:
			foco = self.GetFocusedItem()
			self._num_filas += 1
			self.SetItemCount(self._num_filas)
			self.Refresh()
			# En virtual el control no desplaza el foco solo: se recoloca sobre el mismo elemento.
			if foco != wx.NOT_FOUND and posicion <= foco:
				self._mover_foco_silencioso(foco + 1)
		else:
			# El control nativo desplaza el foco con su elemento: no hay que hacer nada.
			self._insertar_nativa(posicion, valores)
		return posicion

	def _insertar_nativa(self, posicion: int, valores: Sequence[str]) -> None:
		fila = super().InsertItem(posicion, valores[0] if valores else "")
		for columna, valor in enumerate(valores[1:], start=1):
			super().SetItem(fila, columna, valor or "")

	def actualizar_celda(self, fila: int, columna: int, texto: str) -> None:
		"""Cambia una celda sin locutar nada. Solo repinta si cambió.

		Repintar la fila ENFOCADA hace que algunos lectores la relean; por eso
		no se toca si el texto es el mismo.
		"""
		if not 0 <= fila < self.numero_filas():
			return
		celdas = self._celdas.setdefault(fila, {})
		if celdas.get(columna) == texto:
			return
		celdas[columna] = texto
		if self._virtual:
			self.RefreshItem(fila)
		else:
			super().SetItem(fila, columna, texto)

	def actualizar_fila(self, fila: int, valores: Sequence[str]) -> None:
		"""Actualiza todas las columnas de una fila existente."""
		for columna, valor in enumerate(valores):
			self.actualizar_celda(fila, columna, valor or "")

	def eliminar_fila(self, fila: int) -> None:
		"""Elimina una fila. Si era la enfocada, el foco pasa a la vecina."""
		if not 0 <= fila < self.numero_filas():
			return
		foco = self.GetFocusedItem()
		self._celdas = {(k - 1 if k > fila else k): v for k, v in self._celdas.items() if k != fila}
		if self._virtual:
			self._num_filas -= 1
			self.SetItemCount(self._num_filas)
			self.Refresh()
			if foco != wx.NOT_FOUND and fila < foco:
				self._mover_foco_silencioso(foco - 1)
		else:
			self.DeleteItem(fila)
		if foco == fila and self.numero_filas():
			self.enfocar_fila(min(fila, self.numero_filas() - 1), anunciar=False)

	# ── Foco ────────────────────────────────────────────────────────────────────

	def fila_actual(self) -> int:
		"""Fila enfocada; si no hay, la seleccionada; si no, ``wx.NOT_FOUND``."""
		fila = self.GetFocusedItem()
		return fila if fila != wx.NOT_FOUND else self.GetFirstSelected()

	def enfocar_fila(self, fila: int, anunciar: bool = True) -> None:
		"""Selecciona, enfoca y hace visible una fila."""
		if not 0 <= fila < self.numero_filas():
			return
		if not anunciar:
			self._mover_foco_silencioso(fila)
			return
		if not self.GetWindowStyleFlag() & wx.LC_SINGLE_SEL:
			# En selección múltiple, «ir a una fila» deja seleccionada solo esa.
			seleccionadas, actual = [], self.GetFirstSelected()
			while actual != wx.NOT_FOUND:
				seleccionadas.append(actual)
				actual = self.GetNextSelected(actual)
			for otra in seleccionadas:
				if otra != fila:
					self.Select(otra, False)
		self.Select(fila)
		self.Focus(fila)
		self.EnsureVisible(fila)

	def _mover_foco_silencioso(self, fila: int) -> None:
		"""Recoloca foco y selección sin que el lector lo tome como un movimiento del usuario."""
		if not 0 <= fila < self.numero_filas():
			return
		self._marcar_silencio(True)
		try:
			tenia_seleccion = self.GetFirstSelected() != wx.NOT_FOUND
			super().Focus(fila)
			if tenia_seleccion or self.GetWindowStyleFlag() & wx.LC_SINGLE_SEL:
				self.Select(fila)
		finally:
			if wx.GetApp() is not None:
				wx.CallLater(150, self._marcar_silencio, False)
			else:
				self._marcar_silencio(False)

	def _marcar_silencio(self, activar: bool) -> None:
		if not self._propiedad_silencio or not self:
			return
		try:
			hwnd = ctypes.c_void_p(int(self.GetHandle()))
			nombre = ctypes.c_wchar_p(self._propiedad_silencio)
			if activar:
				ctypes.windll.user32.SetPropW(hwnd, nombre, ctypes.c_void_p(1))
			else:
				ctypes.windll.user32.RemovePropW(hwnd, nombre)
		except (AttributeError, OSError, RuntimeError):
			pass

	# ── Lectura por columnas ─────────────────────────────────────────────────────

	def _orden_columnas(self) -> list[int]:
		"""Columnas en el orden en que se ven (si el usuario las reordenó) y con ancho > 0."""
		total = self.GetColumnCount()
		orden = list(range(total))
		if self.HasColumnOrderSupport():
			try:
				orden = list(self.GetColumnsOrder()) or orden
			except (RuntimeError, wx.PyAssertionError):
				pass
		return [c for c in orden if self.GetColumnWidth(c) > 0] or orden

	def anunciar_celda(self, fila: int, columna: int) -> None:
		"""Anuncia una celda según las preferencias vigentes."""
		if self.numero_filas() == 0:
			voz.hablar(_("Lista vacía"), canal=voz.NAVEGACION)
			return
		if not 0 <= fila < self.numero_filas() or not 0 <= columna < self.GetColumnCount():
			return
		texto = componer_anuncio_celda(
			fila,
			self.GetColumn(columna).GetText(),
			self.texto_celda(fila, columna),
			globales=self._leer_preferencias(),
			locales=self.preferencias_locales,
			protegido=columna in self.columnas_protegidas,
		)
		voz.hablar(texto, canal=voz.NAVEGACION)

	def reiniciar_columna(self) -> None:
		"""Vuelve a la primera columna (por ejemplo, al recargar la lista)."""
		self._columna = 0

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		fila = self.fila_actual()
		orden = self._orden_columnas()

		if modificadores == wx.MOD_NONE and tecla in (wx.WXK_LEFT, wx.WXK_RIGHT):
			if fila == wx.NOT_FOUND or not orden:
				evento.Skip()
				return
			posicion = orden.index(self._columna) if self._columna in orden else 0
			posicion = max(0, posicion - 1) if tecla == wx.WXK_LEFT else min(len(orden) - 1, posicion + 1)
			self._columna = orden[posicion]
			self.anunciar_celda(fila, self._columna)
			return

		if modificadores == (wx.MOD_CONTROL | wx.MOD_SHIFT) and tecla in (wx.WXK_HOME, wx.WXK_END) and orden:
			if fila != wx.NOT_FOUND:
				self._columna = orden[0] if tecla == wx.WXK_HOME else orden[-1]
				self.anunciar_celda(fila, self._columna)
			return

		if modificadores == wx.MOD_CONTROL and ord("0") <= tecla <= ord("9"):
			posicion = 9 if tecla == ord("0") else tecla - ord("1")
			if fila == wx.NOT_FOUND:
				voz.hablar(_("Lista vacía") if self.numero_filas() == 0 else _("Ningún elemento seleccionado"),
						   canal=voz.NAVEGACION)
			elif posicion < len(orden):
				self.anunciar_celda(fila, orden[posicion])
			else:
				voz.hablar(_("No existe la columna {numero}").format(numero=posicion + 1), canal=voz.NAVEGACION)
			return

		if modificadores == wx.MOD_NONE and tecla in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			if fila != wx.NOT_FOUND:
				activacion = wx.ListEvent(wx.wxEVT_LIST_ITEM_ACTIVATED, self.GetId())
				activacion.SetEventObject(self)
				activacion.SetIndex(fila)
				wx.PostEvent(self.GetEventHandler(), activacion)
				return
			evento.Skip()
			return

		if modificadores == wx.MOD_NONE and tecla == wx.WXK_SPACE and self._accion_espacio is not None:
			if fila != wx.NOT_FOUND:
				self._accion_espacio(fila)
			return

		evento.Skip()

	# ── Casillas ─────────────────────────────────────────────────────────────────

	def _al_cambiar_casilla(self, evento: wx.ListEvent) -> None:
		if self._anunciar_casillas:
			fila = evento.GetIndex()
			nombre = self.texto_celda(fila, 0) or _("Fila {numero}").format(numero=fila + 1)
			voz.hablar(componer_estado_casilla(nombre, self.IsItemChecked(fila)), canal=voz.NAVEGACION)
		evento.Skip()

	def filas_marcadas(self) -> list[int]:
		"""Índices de las filas con la casilla marcada."""
		if not self._con_casillas:
			return []
		return [fila for fila in range(self.numero_filas()) if self.IsItemChecked(fila)]

	def marcar_todas(self, marcar: bool = True) -> None:
		"""Marca o desmarca todas las filas y lo anuncia una sola vez."""
		if not self._con_casillas:
			return
		anterior, self._anunciar_casillas = self._anunciar_casillas, False
		try:
			for fila in range(self.numero_filas()):
				self.CheckItem(fila, marcar)
		finally:
			self._anunciar_casillas = anterior
		voz.hablar(_("Todas marcadas") if marcar else _("Todas desmarcadas"), canal=voz.NAVEGACION)
