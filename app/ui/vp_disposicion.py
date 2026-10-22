"""Disposición de la ventana principal y ayudas de baja visión.

- **Panel de lectura** (F8, Alt+5): el mensaje elegido, completo, debajo de la
  lista de mensajes. Desactivado de fábrica. Con lector de pantalla no se dice
  nada al moverse por la lista (sería ruido): el panel es una parada más de F6.
- **Ocultar la lista de sesiones** (F7): la conversación ocupa toda la altura.
  Alt+1 (o Escape) la vuelve a mostrar; F6 la salta mientras está oculta.
- **Reparto del espacio**: equilibrado, más para los mensajes o más para la
  respuesta. El cuadro de respuesta tiene como mínimo tres líneas, nunca más
  alto que los mensajes en el reparto equilibrado.
- **Divisor** (#242): una franja fina entre los mensajes (o el panel de
  lectura) y el cuadro de respuesta que se arrastra con el ratón; con el
  teclado, Ver, Distribución de la ventana, «Un poco más de espacio para los
  mensajes» (Ctrl+Alt+Mayús+Abajo) y «... para la respuesta»
  (Ctrl+Alt+Mayús+Arriba) lo mueven de 5 en 5 y lo dicen. Se guarda como
  ``ventana.porcentaje_respuesta`` (parte de la conversación para la
  respuesta; 0 = la del reparto elegido, que lo deshace). No es un
  ``wx.SplitterWindow``: este cambiaría el padre de las listas y del cuadro, su
  orden de tabulación y el de F6; el divisor es una ventana que nunca recibe el
  foco (``AcceptsFocus`` falso) y solo cambia las proporciones del sizer.
- **Zona con el foco resaltada**: su título se ve en negrita y con los colores
  de selección de Windows (también con alto contraste).

Nada de esto cambia los nombres ni el orden que oye el lector: solo lo que se
ve y lo que se salta cuando está oculto.
"""

from __future__ import annotations

import logging
from typing import Any

import wx

from app.ui import avisos
from app.ui.accesibilidad.visual import color_sistema
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Proporciones de altura (sesiones, mensajes, lectura, respuesta) de cada reparto.
REPARTOS: dict[str, tuple[int, int, int, int]] = {
	"equilibrado": (3, 4, 4, 2),
	"mensajes": (2, 6, 6, 1),
	"respuesta": (3, 3, 3, 4),
}
#: Cada cuánto se revisa si el mensaje del panel de lectura ha cambiado (respuestas que llegan).
INTERVALO_LECTURA_MS = 700
#: Divisor: paso de las órdenes de teclado y límites de la parte (por ciento) del cuadro de respuesta.
PASO_DIVISOR = 5
MINIMO_RESPUESTA = 10
MAXIMO_RESPUESTA = 80


def porcentaje_de_reparto(reparto: str) -> int:
	"""Parte de la conversación (mensajes y respuesta, sin el panel de lectura) que da al cuadro de respuesta un reparto."""
	_sesiones, mensajes, _lectura, respuesta = REPARTOS.get(reparto, REPARTOS["equilibrado"])
	return round(100 * respuesta / (mensajes + respuesta))


def proporciones(reparto: str, porcentaje: int, con_lectura: bool) -> tuple[int, int, int, int]:
	"""Proporciones del sizer (sesiones, mensajes, lectura, respuesta).

	Sin ``porcentaje`` (0), las del reparto. Con él, la conversación se cuenta en
	cien partes: ``porcentaje`` para la respuesta y el resto para los mensajes
	(a medias con el panel de lectura si está visible); la lista de sesiones
	conserva su tamaño respecto a la conversación.
	"""
	base = REPARTOS.get(reparto, REPARTOS["equilibrado"])
	if not porcentaje:
		return base
	porcentaje = min(max(int(porcentaje), MINIMO_RESPUESTA), MAXIMO_RESPUESTA)
	sesiones, mensajes, _lectura, respuesta = base
	resto = 100 - porcentaje
	parte_mensajes = max(resto // 2, 1) if con_lectura else resto
	return (max(round(100 * sesiones / (mensajes + respuesta)), 1), parte_mensajes, max(resto - resto // 2, 1), porcentaje)


class _Divisor(wx.Window):
	"""Franja que se arrastra con el ratón para repartir la altura entre los mensajes y la respuesta.

	Nunca recibe el foco (ni con Tab ni con F6): con teclado se usan las órdenes del menú Ver.
	"""

	def __init__(self, padre: wx.Window, al_mover, al_soltar, al_restablecer) -> None:
		super().__init__(padre, style=wx.BORDER_NONE, name=_("Divisor entre los mensajes y la respuesta"))
		self._al_mover, self._al_soltar, self._al_restablecer = al_mover, al_soltar, al_restablecer
		self.arrastrando = False
		self.SetMinSize(wx.Size(-1, self.FromDIP(6)))
		self.SetCursor(wx.Cursor(wx.CURSOR_SIZENS))
		self.SetToolTip(_("Arrastra para dar más espacio a los mensajes o a la respuesta. Doble clic: vuelve al reparto "
			"elegido. Con el teclado: Control+Alt+Mayúsculas+flecha arriba y abajo."))
		self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
		self.Bind(wx.EVT_PAINT, self._al_pintar)
		self.Bind(wx.EVT_LEFT_DOWN, self._al_pulsar)
		self.Bind(wx.EVT_MOTION, self._al_mover_raton)
		self.Bind(wx.EVT_LEFT_UP, self._al_levantar)
		self.Bind(wx.EVT_LEFT_DCLICK, lambda _e: self._al_restablecer())
		self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, lambda _e: self._terminar())

	def AcceptsFocus(self) -> bool:  # noqa: N802 - método de wx
		return False

	def AcceptsFocusFromKeyboard(self) -> bool:  # noqa: N802 - método de wx
		return False

	def _al_pintar(self, _evento: wx.PaintEvent) -> None:
		dc = wx.AutoBufferedPaintDC(self)
		dc.SetBackground(wx.Brush(self.GetParent().GetBackgroundColour()))
		dc.Clear()
		ancho, alto = self.GetClientSize()
		dc.SetPen(wx.Pen(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNSHADOW), max(1, alto // 3)))
		dc.DrawLine(0, alto // 2, ancho, alto // 2)

	def _al_pulsar(self, evento: wx.MouseEvent) -> None:
		self.arrastrando = True
		if not self.HasCapture():
			self.CaptureMouse()
		evento.Skip()

	def _al_mover_raton(self, evento: wx.MouseEvent) -> None:
		if self.arrastrando and evento.Dragging() and evento.LeftIsDown():
			self._al_mover(self.ClientToScreen(evento.GetPosition()).y)

	def _al_levantar(self, _evento: wx.MouseEvent) -> None:
		self._terminar()

	def _terminar(self) -> None:
		if self.HasCapture():
			self.ReleaseMouse()
		if self.arrastrando:
			self.arrastrando = False
			self._al_soltar()


class MixinDisposicion:
	"""Zonas visibles, panel de lectura, reparto del espacio y resaltado (se mezcla con ``VentanaPrincipal``)."""

	panel: wx.Panel
	_sizer_raiz: wx.BoxSizer

	def crear_panel_lectura(self, padre: wx.Window, sizer: wx.Sizer) -> None:
		"""Crea la etiqueta y el campo del panel de lectura (la etiqueta justo antes)."""
		self.etiqueta_lectura = wx.StaticText(padre, label=_("&Lectura del mensaje:"))
		sizer.Add(self.etiqueta_lectura, 0, wx.LEFT, 6)
		self.panel_lectura = wx.TextCtrl(
			padre, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_WORDWRAP | wx.TE_NOHIDESEL,
			name=_("Lectura del mensaje"),
		)
		self.panel_lectura.SetHelpText(_(
			"Texto completo del mensaje elegido en la lista de mensajes. Se recorre con las flechas y se copia con "
			"Control+C. Control+R cita en tu respuesta lo seleccionado o, sin selección, el párrafo del cursor; "
			"Control+Alt+N empieza otra sesión a partir del mensaje. Control+Alt+F lo marca como favorito o lo quita; "
			"Alt+flecha arriba y Alt+flecha abajo traen aquí el favorito anterior y el siguiente. "
			"Alt+5 lleva aquí y Alt+3 al cuadro de respuesta; "
			"F8 muestra u oculta el panel; Escape vuelve a las sesiones."
		))
		sizer.Add(self.panel_lectura, 4, wx.EXPAND | wx.ALL, 6)
		self._lectura_mostrada: tuple[int, str] = (-1, "")
		# El divisor va justo después: entre los mensajes (o el panel de lectura) y la respuesta.
		self.divisor = _Divisor(padre, self._arrastrar_divisor, self._soltar_divisor, self._restablecer_divisor)
		sizer.Add(self.divisor, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
		#: Parte de la respuesta mientras se arrastra el divisor (se guarda al soltar).
		self._porcentaje_arrastre = 0

	def iniciar_disposicion(self) -> None:
		"""Aplica las preferencias de la ventana y conecta los eventos (tras crear las zonas)."""
		self._temporizador_lectura = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, lambda _e: self.actualizar_panel_lectura(), self._temporizador_lectura)
		self.panel.Bind(wx.EVT_LIST_ITEM_FOCUSED, self._al_enfocar_fila_zona)
		self.panel.Bind(wx.EVT_CHILD_FOCUS, self._al_cambiar_foco_zona)
		self.aplicar_disposicion()

	# ------------------------------------------------------------------
	# Zonas
	# ------------------------------------------------------------------

	def zonas(self) -> list[wx.Window]:
		"""Zonas visibles, en el orden de F6."""
		todas = [self.lista_sesiones, self.lista_mensajes, self.panel_lectura, self.respuesta]
		return [zona for zona in todas if zona.IsShown()]

	def _pares_zona(self) -> list[tuple[wx.StaticText, wx.Window]]:
		return [
			(self.etiqueta_sesiones, self.lista_sesiones),
			(self.etiqueta_mensajes, self.lista_mensajes),
			(self.etiqueta_lectura, self.panel_lectura),
			(self.etiqueta_respuesta, self.respuesta),
		]

	def aplicar_disposicion(self) -> None:
		"""Muestra u oculta zonas y reparte la altura según Preferencias."""
		sizer = self._sizer_raiz
		mostrar_sesiones = bool(self.opcion("ventana.mostrar_sesiones", True))
		mostrar_lectura = bool(self.opcion("ventana.panel_lectura", False))
		foco = wx.Window.FindFocus()
		if not mostrar_sesiones and foco is self.lista_sesiones:
			self.lista_mensajes.SetFocus()
		if not mostrar_lectura and foco is self.panel_lectura:
			self.lista_mensajes.SetFocus()
		for ventana, visible in (
			(self.etiqueta_sesiones, mostrar_sesiones), (self.lista_sesiones, mostrar_sesiones),
			(self.etiqueta_lectura, mostrar_lectura), (self.panel_lectura, mostrar_lectura),
		):
			sizer.Show(ventana, visible)
		self._aplicar_proporciones()
		self._ajustar_minimos()
		if mostrar_lectura:
			self._lectura_mostrada = (-1, "")
			self.actualizar_panel_lectura()
			self._temporizador_lectura.Start(INTERVALO_LECTURA_MS)
		else:
			self._temporizador_lectura.Stop()
		self._resaltar_zona(wx.Window.FindFocus())
		self.panel.Layout()

	def porcentaje_respuesta(self) -> int:
		"""Parte de la conversación (por ciento) para el cuadro de respuesta: la del divisor o la del reparto."""
		if self._porcentaje_arrastre:
			return self._porcentaje_arrastre
		try:
			guardado = int(self.opcion("ventana.porcentaje_respuesta", 0) or 0)
		except (TypeError, ValueError):
			guardado = 0
		if guardado:
			return min(max(guardado, MINIMO_RESPUESTA), MAXIMO_RESPUESTA)
		return porcentaje_de_reparto(str(self.opcion("ventana.reparto", "equilibrado")))

	def _aplicar_proporciones(self) -> None:
		"""Pone en el sizer las proporciones del reparto o, si se movió el divisor, las suyas."""
		reparto = str(self.opcion("ventana.reparto", "equilibrado"))
		try:
			porcentaje = self._porcentaje_arrastre or int(self.opcion("ventana.porcentaje_respuesta", 0) or 0)
		except (TypeError, ValueError):
			porcentaje = 0
		valores = proporciones(reparto, porcentaje, bool(self.opcion("ventana.panel_lectura", False)))
		for ventana, proporcion in zip(
			(self.lista_sesiones, self.lista_mensajes, self.panel_lectura, self.respuesta), valores, strict=True,
		):
			elemento = self._sizer_raiz.GetItem(ventana)
			if elemento is not None:
				elemento.SetProportion(proporcion)

	def _ajustar_minimos(self) -> None:
		"""Mínimos pequeños: en ventanas pequeñas manda el reparto, no el tamaño «ideal» de cada control."""
		alto_linea = max(self.respuesta.GetCharHeight(), 8)
		self.respuesta.SetMinSize(wx.Size(-1, alto_linea * 3 + self.FromDIP(12)))
		for lista in (self.lista_sesiones, self.lista_mensajes, self.panel_lectura):
			lista.SetMinSize(wx.Size(self.FromDIP(120), alto_linea * 3 + self.FromDIP(8)))

	# Las listas se vuelven a crear al cambiar sus columnas: se reaplica la disposición.
	def recrear_lista_mensajes(self) -> None:
		super().recrear_lista_mensajes()
		self.aplicar_disposicion()

	def _recrear_lista_sesiones(self, mover_foco: bool = True) -> None:
		super()._recrear_lista_sesiones(mover_foco)
		self.aplicar_disposicion()

	# ------------------------------------------------------------------
	# Acciones del menú Ver, Distribución de la ventana
	# ------------------------------------------------------------------

	def accion_mostrar_sesiones(self) -> None:
		valor = not bool(self.opcion("ventana.mostrar_sesiones", True))
		self.poner_opcion("ventana.mostrar_sesiones", valor)
		self.aplicar_disposicion()
		self.sincronizar_menu_disposicion()
		if valor:
			self.ir_a_sesiones()
			avisos.anunciar(_("Lista de sesiones visible."), interrumpir=True)
		else:
			avisos.anunciar(_("Lista de sesiones oculta. Alt+1 la vuelve a mostrar."), interrumpir=True)

	def mostrar_lista_sesiones_si_oculta(self) -> None:
		"""Alt+1 y Escape vuelven a mostrar la lista si estaba oculta."""
		if self.opcion("ventana.mostrar_sesiones", True):
			return
		self.poner_opcion("ventana.mostrar_sesiones", True)
		self.aplicar_disposicion()
		self.sincronizar_menu_disposicion()
		avisos.anunciar(_("Lista de sesiones visible."), interrumpir=True)

	def accion_panel_lectura(self) -> None:
		valor = not bool(self.opcion("ventana.panel_lectura", False))
		self.poner_opcion("ventana.panel_lectura", valor)
		self.aplicar_disposicion()
		self.sincronizar_menu_disposicion()
		avisos.anunciar(_("Panel de lectura activado. Alt+5 lleva a él.") if valor else _("Panel de lectura desactivado."),
			interrumpir=True)

	def ir_a_lectura(self) -> None:
		if not self.panel_lectura.IsShown():
			avisos.anunciar(_("El panel de lectura está desactivado. Actívalo con F8."), interrumpir=True)
			return
		self.actualizar_panel_lectura()
		self.panel_lectura.SetFocus()
		self.panel_lectura.SetInsertionPoint(0)
		self.panel_lectura.ShowPosition(0)

	def accion_reparto(self, reparto: str) -> None:
		# Elegir un reparto deshace lo que se hubiera movido el divisor.
		self.poner_opcion("ventana.porcentaje_respuesta", 0)
		self.poner_opcion("ventana.reparto", reparto)
		self.aplicar_disposicion()
		self.sincronizar_menu_disposicion()
		nombres = {"equilibrado": _("Reparto equilibrado."), "mensajes": _("Más espacio para los mensajes."),
			"respuesta": _("Más espacio para el cuadro de respuesta.")}
		avisos.anunciar(nombres.get(reparto, ""), interrumpir=True)

	# -- Divisor -----------------------------------------------------------

	def mover_divisor(self, paso: int) -> int:
		"""Mueve el divisor ``paso`` puntos (positivo: más espacio para la respuesta), lo guarda y lo dice.

		Devuelve la parte nueva de la respuesta (la misma si ya estaba en el límite).
		"""
		actual = self.porcentaje_respuesta()
		nuevo = min(max(actual + paso, MINIMO_RESPUESTA), MAXIMO_RESPUESTA)
		if nuevo == actual:
			avisos.anunciar(_("Ya no se puede dar más espacio a la respuesta.") if paso > 0
				else _("Ya no se puede dar más espacio a los mensajes."), interrumpir=True)
			return actual
		self.poner_opcion("ventana.porcentaje_respuesta", nuevo)
		self.aplicar_disposicion()
		if paso > 0:
			aviso = _("Más espacio para la respuesta: ocupa el {p} % de la conversación.")
		else:
			aviso = _("Más espacio para los mensajes: la respuesta ocupa el {p} % de la conversación.")
		avisos.anunciar(aviso.format(p=nuevo), interrumpir=True)
		return nuevo

	def accion_mas_espacio_mensajes(self) -> None:
		"""Ctrl+Alt+Mayús+Abajo: baja el divisor un paso."""
		self.mover_divisor(-PASO_DIVISOR)

	def accion_mas_espacio_respuesta(self) -> None:
		"""Ctrl+Alt+Mayús+Arriba: sube el divisor un paso."""
		self.mover_divisor(PASO_DIVISOR)

	def _arrastrar_divisor(self, y_pantalla: int) -> None:
		"""Mientras se arrastra con el ratón: reparte sin guardar (se guarda al soltar)."""
		arriba = self.lista_mensajes.GetScreenRect().GetTop()
		abajo = self.respuesta.GetScreenRect().GetBottom()
		if abajo - arriba < self.FromDIP(40):
			return
		porcentaje = min(max(round(100 * (abajo - y_pantalla) / (abajo - arriba)), MINIMO_RESPUESTA), MAXIMO_RESPUESTA)
		if porcentaje != self.porcentaje_respuesta():
			self._porcentaje_arrastre = porcentaje
			self._aplicar_proporciones()
			self.panel.Layout()

	def _soltar_divisor(self) -> None:
		porcentaje, self._porcentaje_arrastre = self._porcentaje_arrastre, 0
		if porcentaje:
			self.poner_opcion("ventana.porcentaje_respuesta", porcentaje)
			self.aplicar_disposicion()
			avisos.anunciar(_("La respuesta ocupa el {p} % de la conversación.").format(p=porcentaje))

	def _restablecer_divisor(self) -> None:
		"""Doble clic en el divisor: vuelve al reparto elegido."""
		self._porcentaje_arrastre = 0
		self.poner_opcion("ventana.porcentaje_respuesta", 0)
		self.aplicar_disposicion()
		avisos.anunciar(_("Divisor en su sitio: el del reparto elegido."))

	def accion_resaltar_foco(self) -> None:
		valor = not bool(self.opcion("ventana.resaltar_foco", True))
		self.poner_opcion("ventana.resaltar_foco", valor)
		self._resaltar_zona(wx.Window.FindFocus())
		self.sincronizar_menu_disposicion()
		avisos.anunciar(_("Se resalta la zona con el foco.") if valor else _("Ya no se resalta la zona con el foco."),
			interrumpir=True)

	def crear_menu_disposicion(self) -> wx.Menu:
		"""Submenú Ver, «Distribución de la ventana»."""
		menu = wx.Menu()
		self._menu_disposicion: dict[str, wx.MenuItem] = {}

		def anadir(clave: str, etiqueta: str, ayuda: str, accion, tipo: int = wx.ITEM_NORMAL) -> None:
			item = menu.Append(wx.ID_ANY, etiqueta, ayuda, tipo)
			self._menu_disposicion[clave] = item
			self.Bind(wx.EVT_MENU, lambda _e: self._ejecutar_accion_funcion(accion), item)

		anadir("sesiones", _("Mostrar la lista de &sesiones\tF7"),
			_("Muestra u oculta la lista de sesiones; oculta, la conversación ocupa toda la ventana."),
			self.accion_mostrar_sesiones, wx.ITEM_CHECK)
		anadir("lectura", _("Panel de &lectura\tF8"),
			_("Muestra debajo de la lista de mensajes el mensaje elegido, completo."), self.accion_panel_lectura, wx.ITEM_CHECK)
		anadir("ir_lectura", _("&Ir al panel de lectura\tAlt+5"), _("Lleva el foco al panel de lectura."), self.ir_a_lectura)
		menu.AppendSeparator()
		anadir("equilibrado", _("Reparto &equilibrado"), _("Altura repartida entre sesiones, mensajes y respuesta."),
			lambda: self.accion_reparto("equilibrado"), wx.ITEM_RADIO)
		anadir("mensajes", _("Más espacio para los &mensajes"), _("Los mensajes ocupan casi toda la altura."),
			lambda: self.accion_reparto("mensajes"), wx.ITEM_RADIO)
		anadir("respuesta", _("Más espacio para el cuadro de &respuesta"), _("Más altura para escribir mensajes largos."),
			lambda: self.accion_reparto("respuesta"), wx.ITEM_RADIO)
		menu.AppendSeparator()
		anadir("divisor_mensajes", _("Un poco más de espacio para los mensajes\tCtrl+Alt+Shift+Down"),
			_("Baja un paso el divisor entre los mensajes y la respuesta y dice cuánto ocupa la respuesta. Se recuerda; "
				"elegir uno de los repartos de arriba lo deshace."), self.accion_mas_espacio_mensajes)
		anadir("divisor_respuesta", _("Un poco más de espacio para la respuesta\tCtrl+Alt+Shift+Up"),
			_("Sube un paso el divisor entre los mensajes y la respuesta y dice cuánto ocupa la respuesta. Se recuerda; "
				"elegir uno de los repartos de arriba lo deshace."), self.accion_mas_espacio_respuesta)
		menu.AppendSeparator()
		anadir("resaltar", _("Resaltar la &zona con el foco"), _("Destaca el título de la zona donde está el foco."),
			self.accion_resaltar_foco, wx.ITEM_CHECK)
		return menu

	def sincronizar_menu_disposicion(self) -> None:
		elementos = getattr(self, "_menu_disposicion", {})
		if not elementos:
			return
		elementos["sesiones"].Check(bool(self.opcion("ventana.mostrar_sesiones", True)))
		elementos["lectura"].Check(bool(self.opcion("ventana.panel_lectura", False)))
		elementos["resaltar"].Check(bool(self.opcion("ventana.resaltar_foco", True)))
		reparto = str(self.opcion("ventana.reparto", "equilibrado"))
		if reparto in elementos:
			elementos[reparto].Check(True)

	# ------------------------------------------------------------------
	# Panel de lectura
	# ------------------------------------------------------------------

	def texto_panel_lectura(self) -> tuple[int, str, str]:
		"""(índice, título, texto) del mensaje enfocado en la lista de mensajes."""
		mensaje = self.mensaje_actual()
		if mensaje is None:
			return -1, _("&Lectura del mensaje:"), _("No hay ningún mensaje elegido.")
		indice = self.lista_mensajes.GetFocusedItem()
		titulo = _("&Lectura: mensaje {n} de {total}, {quien}:").format(
			n=indice + 1, total=len(self._mensajes), quien=self._quien(mensaje))
		texto = mensaje.texto or _("(vacío)")
		if mensaje.detalle:
			texto += "\n\n" + mensaje.detalle
		return indice, titulo, texto

	def actualizar_panel_lectura(self) -> None:
		"""Pone en el panel el mensaje enfocado, sin hablar y sin mover el cursor si es el mismo."""
		if not self or getattr(self, "_saliendo", False) or not self.panel_lectura.IsShown():
			return
		indice, titulo, texto = self.texto_panel_lectura()
		if (indice, texto) == self._lectura_mostrada:
			return
		mismo = indice == self._lectura_mostrada[0]
		posicion = self.panel_lectura.GetInsertionPoint() if mismo else 0
		self._lectura_mostrada = (indice, texto)
		if self.etiqueta_lectura.GetLabel() != titulo:
			self.etiqueta_lectura.SetLabel(titulo)
		self.panel_lectura.ChangeValue(texto)
		self.panel_lectura.SetInsertionPoint(min(posicion, self.panel_lectura.GetLastPosition()))
		if not mismo:
			self.panel_lectura.ShowPosition(0)

	def _al_enfocar_fila_zona(self, evento: wx.ListEvent) -> None:
		if evento.GetEventObject() is self.lista_mensajes and self.panel_lectura.IsShown():
			wx.CallAfter(self.actualizar_panel_lectura)
		evento.Skip()

	# ------------------------------------------------------------------
	# Zona con el foco resaltada
	# ------------------------------------------------------------------

	def _al_cambiar_foco_zona(self, evento: wx.ChildFocusEvent) -> None:
		self._resaltar_zona(evento.GetWindow())
		evento.Skip()

	def zona_de(self, ventana: wx.Window | None) -> wx.Window | None:
		"""Zona (lista o cuadro) que contiene ``ventana``, o ``None``."""
		zonas = {id(control): control for _etiqueta, control in self._pares_zona()}
		while ventana is not None and ventana is not self:
			if id(ventana) in zonas:
				return zonas[id(ventana)]
			ventana = ventana.GetParent()
		return None

	def _resaltar_zona(self, ventana: wx.Window | None) -> None:
		activa = self.zona_de(ventana) if self.opcion("ventana.resaltar_foco", True) else None
		self.zona_resaltada = activa
		for etiqueta, control in self._pares_zona():
			resaltar = control is activa
			if getattr(etiqueta, "_resaltada", None) == resaltar:
				continue
			etiqueta._resaltada = resaltar
			fuente = etiqueta.GetParent().GetFont()
			if resaltar:
				fuente = fuente.Bold()
				etiqueta.SetBackgroundColour(color_sistema("seleccion"))
				etiqueta.SetForegroundColour(color_sistema("texto_seleccion"))
			else:
				etiqueta.SetBackgroundColour(wx.NullColour)
				etiqueta.SetForegroundColour(wx.NullColour)
			etiqueta.SetFont(fuente)
			etiqueta.Refresh()

	def preferencias_disposicion_cambiadas(self, clave: str, _valor: Any) -> bool:
		"""Aplica un cambio de Preferencias de la sección ``ventana``; ``True`` si era de esta zona."""
		if clave in ("ventana.mostrar_sesiones", "ventana.panel_lectura", "ventana.reparto", "ventana.resaltar_foco",
				"ventana.porcentaje_respuesta"):
			wx.CallAfter(self.aplicar_disposicion)
			wx.CallAfter(self.sincronizar_menu_disposicion)
			return True
		return False
