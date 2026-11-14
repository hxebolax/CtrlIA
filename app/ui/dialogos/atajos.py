"""Herramientas, «Atajos de teclado...»: dar una tecla a cualquier orden, moverla, quitarla o volver a las de fábrica.

La lista tiene todas las órdenes de la barra de menús cuya tecla se puede
cambiar (``MapaTeclas.personalizables``), en el orden de los menús. Cada fila dice
la acción, su tecla (o «Sin tecla»), si está cambiada o es la de fábrica, y en qué
menú está. Las órdenes de tecla fija (Enviar con Ctrl+Intro, la ayuda de Ctrl+H)
no salen: su tecla no se puede cambiar.

La tecla se elige en un cuadro propio (``DialogoCapturaTecla``) pulsándola: se
dice al momento qué se ha pulsado y qué pasaría.

Los cambios se guardan y se aplican al momento (``aplicar``: la ventana pone la
tecla nueva en el texto del menú y wx actualiza el acelerador), sin reiniciar.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import wx

from app.core.teclas import MapaTeclas, normalizar, para_leer, sin_tildes
from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Teclas con nombre por su código de wx (las letras, números, signos y teclas F se calculan).
_NOMBRADAS = {
	wx.WXK_RETURN: "Enter", wx.WXK_NUMPAD_ENTER: "Enter",
	wx.WXK_DELETE: "Delete", wx.WXK_NUMPAD_DELETE: "Delete",
	wx.WXK_INSERT: "Insert", wx.WXK_NUMPAD_INSERT: "Insert",
	wx.WXK_HOME: "Home", wx.WXK_NUMPAD_HOME: "Home", wx.WXK_END: "End", wx.WXK_NUMPAD_END: "End",
	wx.WXK_PAGEUP: "PageUp", wx.WXK_NUMPAD_PAGEUP: "PageUp", wx.WXK_PAGEDOWN: "PageDown", wx.WXK_NUMPAD_PAGEDOWN: "PageDown",
	wx.WXK_UP: "Up", wx.WXK_NUMPAD_UP: "Up", wx.WXK_DOWN: "Down", wx.WXK_NUMPAD_DOWN: "Down",
	wx.WXK_LEFT: "Left", wx.WXK_NUMPAD_LEFT: "Left", wx.WXK_RIGHT: "Right", wx.WXK_NUMPAD_RIGHT: "Right",
	wx.WXK_SPACE: "Space", wx.WXK_TAB: "Tab", wx.WXK_ESCAPE: "Escape", wx.WXK_BACK: "Backspace",
	wx.WXK_CAPITAL: "CapsLock", wx.WXK_WINDOWS_MENU: "Apps",
}
#: Teclas que solo son parte de un atajo: pulsadas solas no eligen nada.
_MODIFICADORES = {
	wx.WXK_SHIFT, wx.WXK_CONTROL, wx.WXK_ALT, wx.WXK_RAW_CONTROL, wx.WXK_WINDOWS_LEFT, wx.WXK_WINDOWS_RIGHT,
	wx.WXK_MENU, wx.WXK_NUMLOCK, wx.WXK_SCROLL,
}
COLUMNAS = [(_("Acción"), 320), (_("Tecla"), 170), (_("Estado"), 110), (_("Menú"), 130)]


def tecla_del_evento(evento: wx.KeyEvent) -> str:
	"""La tecla que es ``evento``, normalizada («Ctrl+Shift+Q»); ``""`` si es solo un modificador o una que no tiene nombre."""
	codigo = evento.GetKeyCode()
	if codigo in _MODIFICADORES:
		return ""
	if wx.WXK_F1 <= codigo <= wx.WXK_F24:
		nombre = f"F{codigo - wx.WXK_F1 + 1}"
	elif codigo in _NOMBRADAS:
		nombre = _NOMBRADAS[codigo]
	elif wx.WXK_NUMPAD0 <= codigo <= wx.WXK_NUMPAD9:
		nombre = str(codigo - wx.WXK_NUMPAD0)
	elif 32 < codigo < 127:
		nombre = chr(codigo)
	else:
		return ""
	modificadores = [m for m, pulsado in (("Ctrl", evento.ControlDown()), ("Alt", evento.AltDown()),
		("Shift", evento.ShiftDown())) if pulsado]
	try:
		return normalizar("+".join([*modificadores, nombre]))
	except ValueError:
		return ""


class DialogoCapturaTecla(wx.Dialog):
	"""«Cambiar tecla: X»: pulsa la tecla que quieres.

	Lo pulsado se dice y se ve al momento, con lo que pasaría: «Nadie la tiene»,
	«La tiene X; se le quitará» o por qué no se puede. Asignar (o Intro) la da;
	Probar otra la borra; Escape o Cancelar no cambian nada.

	En el cuadro «Tecla» todas las teclas se toman como la que quieres, menos
	Tab y Mayús+Tab (para moverse), Escape (cancelar), Intro (asignar), Alt+F4,
	Ctrl+H (la ayuda) y Alt con una letra (los botones de este cuadro; ninguna
	orden puede tener una).
	"""

	def __init__(self, padre: wx.Window | None, mapa: MapaTeclas, clave: str) -> None:
		self.mapa = mapa
		self.clave = clave
		nombre = mapa.nombre(clave)
		titulo = _("Cambiar tecla: {accion}").format(accion=nombre)
		super().__init__(padre, title=titulo, name=titulo)
		#: La tecla pulsada, normalizada, o «».
		self.tecla = ""
		actual = mapa.tecla(clave)
		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.explicacion = wx.StaticText(panel, label=_(
			"Pulsa las teclas que quieres para «{accion}». Su tecla ahora: {tecla}."
		).format(accion=nombre, tecla=para_leer(actual) or _("ninguna")))
		self.explicacion.Wrap(self.FromDIP(460))
		sizer.Add(self.explicacion, 0, wx.ALL, 8)
		sizer.Add(wx.StaticText(panel, label=_("&Tecla:")), 0, wx.LEFT | wx.RIGHT, 8)
		# No es de solo lectura: wx deja fuera del tabulador un cuadro de una línea de
		# solo lectura. Todas las teclas pasan antes por _al_pulsar y se quedan allí, así
		# que no se escribe nada; lo que entre de otra forma (pegar) se deshace.
		self.cuadro = wx.TextCtrl(panel, name=_("Tecla, pulsa las teclas que quieres"))
		self.cuadro.SetHelpText(_(
			"Pulsa aquí la combinación que quieres, por ejemplo Control+Mayúsculas+Q o F11: se dice al momento y debajo "
			"se explica qué pasaría. Intro la asigna; Escape cancela; Tabulador pasa a los botones. Control+H, que estás "
			"usando, no se puede asignar: aquí explica este cuadro."
		))
		sizer.Add(self.cuadro, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
		self.estado = wx.StaticText(panel, label=_("Todavía no has pulsado nada."))
		sizer.Add(self.estado, 0, wx.EXPAND | wx.ALL, 8)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_asignar = wx.Button(panel, wx.ID_OK, _("&Asignar"))
		self.boton_asignar.SetHelpText(_("Da a la acción la tecla pulsada. Si otra acción la tenía, se queda sin tecla."))
		self.boton_asignar.SetDefault()
		self.boton_otra = wx.Button(panel, label=_("&Probar otra"))
		self.boton_otra.SetHelpText(_("Borra la tecla pulsada y vuelve al cuadro para pulsar otra."))
		self.boton_cancelar = wx.Button(panel, wx.ID_CANCEL, _("&Cancelar"))
		self.boton_cancelar.SetHelpText(_("Cierra sin cambiar nada."))
		for boton in (self.boton_asignar, self.boton_otra):
			fila.Add(boton, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		fila.Add(self.boton_cancelar, 0)
		sizer.Add(fila, 0, wx.EXPAND | wx.ALL, 8)
		panel.SetSizer(sizer)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		self.boton_asignar.Bind(wx.EVT_BUTTON, lambda _e: self.asignar())
		self.boton_otra.Bind(wx.EVT_BUTTON, lambda _e: self.probar_otra())
		self.cuadro.Bind(wx.EVT_TEXT, self._al_cambiar_texto)
		preparar_dialogo(self, foco_inicial=self.cuadro)
		# Después de preparar_dialogo: este manejador va primero y deja pasar Escape y Ctrl+H.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		self.CentreOnParent()

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		if wx.Window.FindFocus() is not self.cuadro:
			evento.Skip()
			return
		codigo, mods = evento.GetKeyCode(), evento.GetModifiers()
		if codigo == wx.WXK_TAB and mods in (wx.MOD_NONE, wx.MOD_SHIFT):
			evento.Skip()
			return
		if codigo == wx.WXK_ESCAPE and mods == wx.MOD_NONE:
			evento.Skip()
			return
		if codigo in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and mods == wx.MOD_NONE:
			self.asignar()
			return
		if (mods == wx.MOD_ALT and codigo == wx.WXK_F4) or (mods == wx.MOD_CONTROL and codigo == ord("H")):
			evento.Skip()
			return
		if mods == wx.MOD_ALT and 32 < codigo < 127 and chr(codigo).isalpha():
			evento.Skip()  # las letras de los botones de este cuadro
			return
		if codigo in _MODIFICADORES:
			return
		tecla = tecla_del_evento(evento)
		if tecla:
			self.capturar(tecla)
		else:
			self.tecla = ""
			self.cuadro.ChangeValue("")
			self._decir(_("Esa tecla no se puede usar en un atajo."))

	def _al_cambiar_texto(self, _evento: wx.CommandEvent) -> None:
		mostrado = para_leer(self.tecla)
		if self.cuadro.GetValue() != mostrado:
			self.cuadro.ChangeValue(mostrado)

	def capturar(self, tecla: str) -> str:
		"""Se ha pulsado ``tecla``: se enseña y se dice qué haría Asignar. Devuelve lo dicho."""
		self.tecla = tecla
		nombre = self.mapa.nombre(self.clave)
		motivo = self.mapa.por_que_no(self.clave, tecla)
		duena = self.mapa.comando_de(tecla)
		if motivo:
			estado = motivo
		elif duena == self.clave:
			estado = _("Ya es la tecla de «{accion}».").format(accion=nombre)
		elif duena:
			estado = _("La tiene «{otra}»; si la asignas, se le quitará.").format(otra=self.mapa.nombre(duena))
		else:
			estado = _("Nadie la tiene. Asignar se la da a «{accion}».").format(accion=nombre)
		self.cuadro.ChangeValue(para_leer(tecla))
		dicho = f"{para_leer(tecla)}. {estado}"
		self._decir(estado, dicho)
		return dicho

	def _decir(self, estado: str, dicho: str = "") -> None:
		self.estado.SetLabel(estado)
		self.estado.Wrap(max(self.GetClientSize().width - self.FromDIP(16), 200))
		self.Layout()
		avisos.anunciar(dicho or estado, interrumpir=True, siempre=True)

	def usable(self) -> bool:
		return bool(self.tecla) and not self.mapa.por_que_no(self.clave, self.tecla)

	def asignar(self) -> bool:
		if not self.tecla:
			avisos.anunciar(_("Pulsa primero las teclas que quieres."), interrumpir=True, siempre=True)
			self.cuadro.SetFocus()
			return False
		if not self.usable():
			avisos.anunciar(self.mapa.por_que_no(self.clave, self.tecla), interrumpir=True, siempre=True)
			self.cuadro.SetFocus()
			return False
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
		return True

	def probar_otra(self) -> None:
		self.tecla = ""
		self.cuadro.ChangeValue("")
		self.estado.SetLabel(_("Todavía no has pulsado nada."))
		self.cuadro.SetFocus()


class DialogoAtajos(wx.Dialog):
	"""Herramientas, «Atajos de teclado...».

	:param mapa: el ``MapaTeclas`` de la ventana.
	:param aplicar: enseña y usa al momento las teclas del mapa en la ventana.
	"""

	def __init__(self, padre: wx.Window | None, mapa: MapaTeclas, aplicar: Callable[[], None]) -> None:
		titulo = _("Atajos de teclado")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.mapa = mapa
		self._aplicar = aplicar
		self._avisado_guardar = False
		#: Las claves mostradas, por fila.
		self._mostradas: list[str] = []
		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(panel, label=_("&Filtrar:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.filtro = wx.TextCtrl(panel, name=_("Filtrar"))
		self.filtro.SetHelpText(_(
			"Escribe palabras para dejar en la lista solo las acciones que las contienen, en el nombre, la tecla o el "
			"menú, en cualquier orden y sin importar las tildes. Por ejemplo «sesión» o «ctrl+mayús»."
		))
		sizer.Add(self.filtro, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
		self.etiqueta = wx.StaticText(panel, label=_("&Acciones:"))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.lista = ListaAccesible(panel, _("Acciones"), COLUMNAS)
		self.lista.SetHelpText(_(
			"Todas las acciones de los menús cuya tecla puedes cambiar, con su tecla, si la has cambiado y su menú. "
			"Intro cambia la tecla; Suprimir se la quita; en el menú contextual también puedes volver a la de fábrica. "
			"Los cambios se guardan y valen al momento."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(760, 320)))
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_cambiar = wx.Button(panel, label=_("Cam&biar la tecla..."))
		self.boton_cambiar.SetHelpText(_("Abre un cuadro donde pulsas la tecla nueva para la acción elegida (Intro en la lista)."))
		self.boton_quitar = wx.Button(panel, label=_("&Quitar la tecla"))
		self.boton_quitar.SetHelpText(_("Deja la acción elegida sin tecla; sigue en su menú (Suprimir en la lista)."))
		self.boton_restablecer = wx.Button(panel, label=_("&Restablecer"))
		self.boton_restablecer.SetHelpText(_("Devuelve a la acción elegida su tecla de fábrica."))
		self.boton_todo = wx.Button(panel, label=_("Restablecer &todas..."))
		self.boton_todo.SetHelpText(_("Devuelve a todas las acciones su tecla de fábrica; pregunta antes."))
		for boton in (self.boton_cambiar, self.boton_quitar, self.boton_restablecer, self.boton_todo):
			fila.Add(boton, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra el cuadro. Los cambios ya están guardados."))
		fila.Add(self.boton_cerrar, 0)
		sizer.Add(fila, 0, wx.EXPAND | wx.ALL, 6)
		panel.SetSizer(sizer)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)

		self.boton_cambiar.Bind(wx.EVT_BUTTON, lambda _e: self.cambiar())
		self.boton_quitar.Bind(wx.EVT_BUTTON, lambda _e: self.quitar())
		self.boton_restablecer.Bind(wx.EVT_BUTTON, lambda _e: self.restablecer())
		self.boton_todo.Bind(wx.EVT_BUTTON, lambda _e: self.restablecer_todo())
		self.filtro.Bind(wx.EVT_TEXT, lambda _e: self._rellenar(self.clave_actual()))
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.cambiar())
		vincular_menu_contextual(self.lista, self._crear_menu)
		preparar_dialogo(self, foco_inicial=self.lista)
		# Después de preparar_dialogo, para ir antes que él: en un cuadro de Windows sin
		# botón predeterminado, Intro no llega a la lista.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		self._rellenar(None)
		self.Layout()
		self.Fit()
		self.CentreOnParent()

	# ── La lista ──────────────────────────────────────────────────────────────

	def valores(self, clave: str) -> list[str]:
		"""Las columnas de una fila: acción, tecla, estado y menú."""
		tecla = self.mapa.tecla(clave)
		return [
			self.mapa.nombre(clave),
			para_leer(tecla) or _("Sin tecla"),
			_("Cambiada") if self.mapa.cambiada(clave) else _("De fábrica"),
			self.mapa.comando(clave).menu,
		]

	def _rellenar(self, elegir: str | None) -> None:
		palabras = sin_tildes(self.filtro.GetValue()).split()
		filas: list[list[str]] = []
		self._mostradas = []
		for clave in self.mapa.personalizables():
			valores = self.valores(clave)
			texto = sin_tildes(" ".join([*valores, self.mapa.tecla(clave)]))
			if all(p in texto for p in palabras):
				self._mostradas.append(clave)
				filas.append(valores)
		self.lista.reemplazar_filas(filas, conservar_foco=False)
		total = len(self._mostradas)
		if total:
			self.etiqueta.SetLabel(_("&Acciones ({n}):").format(n=total))
			self.lista.SetName(_("Acciones ({n})").format(n=total))
			fila = self._mostradas.index(elegir) if elegir in self._mostradas else 0
			self.lista.enfocar_fila(fila, anunciar=False)
		else:
			self.etiqueta.SetLabel(_("&Acciones: ninguna coincide con el filtro."))
			self.lista.SetName(_("Acciones: ninguna coincide con el filtro"))

	def clave_actual(self) -> str | None:
		fila = self.lista.fila_actual()
		return self._mostradas[fila] if 0 <= fila < len(self._mostradas) else None

	def _elegida(self) -> str | None:
		clave = self.clave_actual()
		if clave is None:
			avisos.anunciar(_("No hay ninguna acción elegida. Borra el filtro para verlas todas."), interrumpir=True)
			self.lista.SetFocus()
		return clave

	def _crear_menu(self) -> wx.Menu | None:
		if self.clave_actual() is None:
			return None
		return menu_de_acciones(self.lista, [
			(_("Cam&biar la tecla...\tIntro"), self.cambiar),
			(_("&Quitar la tecla\tSupr"), self.quitar),
			(_("&Restablecer la de fábrica"), self.restablecer),
			None,
			(_("Restablecer &todas..."), self.restablecer_todo),
		])

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		"""Intro cambia la tecla de la acción elegida y Suprimir se la quita (solo en la lista)."""
		if wx.Window.FindFocus() is self.lista and evento.GetModifiers() == wx.MOD_NONE:
			codigo = evento.GetKeyCode()
			if codigo in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
				self.cambiar()
				return
			if codigo in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
				self.quitar()
				return
		evento.Skip()

	# ── Cambios ───────────────────────────────────────────────────────────────

	def _guardado(self, clave: str | None, dicho: str) -> None:
		"""Guarda, lo enseña en la ventana y en la lista y lo dice."""
		if not self.mapa.guardar() and not self._avisado_guardar:
			self._avisado_guardar = True
			mostrar_error(self, self.mapa.aviso + "\n\n" + _("Los cambios valen hasta que cierres Ctrl+IA."), self.GetTitle())
		try:
			self._aplicar()
		except Exception:  # noqa: BLE001 - si la ventana no puede enseñarlo, el cambio sigue guardado
			log.exception("No se pudieron aplicar los atajos a la ventana")
		self._rellenar(clave)
		if clave is not None and self.clave_actual() != clave:
			# Ya no coincide con el filtro: se enseñan todas, sobre ella.
			self.filtro.ChangeValue("")
			self._rellenar(clave)
		avisos.anunciar(dicho, interrumpir=True)
		self.lista.SetFocus()

	def _perdida(self, perdedora: str | None) -> str:
		return " " + _("«{otra}» se queda sin tecla.").format(otra=self.mapa.nombre(perdedora)) if perdedora else ""

	def pedir_tecla(self, clave: str) -> str | None:
		"""Abre «Cambiar tecla» y devuelve la tecla elegida, o ``None`` si se cancela."""
		dialogo = DialogoCapturaTecla(self, self.mapa, clave)
		try:
			return dialogo.tecla if dialogo.ShowModal() == wx.ID_OK and dialogo.tecla else None
		finally:
			dialogo.Destroy()

	def cambiar(self) -> bool:
		clave = self._elegida()
		if clave is None:
			return False
		tecla = self.pedir_tecla(clave)
		if tecla is None:
			self.lista.SetFocus()
			return False
		try:
			perdedora = self.mapa.asignar(clave, tecla)
		except ValueError as error:
			avisos.anunciar(str(error), interrumpir=True)
			self.lista.SetFocus()
			return False
		self._guardado(clave, f"{self.mapa.nombre(clave)}: {para_leer(tecla)}.{self._perdida(perdedora)}")
		return True

	def quitar(self) -> bool:
		clave = self._elegida()
		if clave is None:
			return False
		if not self.mapa.tecla(clave):
			avisos.anunciar(_("«{accion}» no tiene tecla.").format(accion=self.mapa.nombre(clave)), interrumpir=True)
			return False
		self.mapa.quitar(clave)
		self._guardado(clave, _("{accion}: sin tecla.").format(accion=self.mapa.nombre(clave)))
		return True

	def restablecer(self) -> bool:
		clave = self._elegida()
		if clave is None:
			return False
		if not self.mapa.cambiada(clave):
			avisos.anunciar(_("«{accion}» ya tiene su tecla de fábrica.").format(accion=self.mapa.nombre(clave)), interrumpir=True)
			return False
		perdedora = self.mapa.restablecer(clave)
		tecla = para_leer(self.mapa.tecla(clave)) or _("sin tecla")
		self._guardado(clave, _("{accion}: {tecla}, la de fábrica.").format(accion=self.mapa.nombre(clave), tecla=tecla)
			+ self._perdida(perdedora))
		return True

	def restablecer_todo(self) -> bool:
		cambiadas = self.mapa.cambiadas()
		if not cambiadas:
			avisos.anunciar(_("Todas las acciones tienen su tecla de fábrica."), interrumpir=True)
			return False
		pregunta = _("¿Volver a las teclas de fábrica en las {n} acciones que has cambiado?").format(n=len(cambiadas))
		if confirmar(self, pregunta, _("Restablecer todas"), peligroso=True) != wx.ID_YES:
			self.lista.SetFocus()
			return False
		self.mapa.restablecer_todo()
		self._guardado(self.clave_actual(), _("Todas las acciones vuelven a tener su tecla de fábrica."))
		return True
