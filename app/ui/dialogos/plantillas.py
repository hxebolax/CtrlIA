"""Plantillas de mensajes: elegir, crear, editar y borrar.

``DialogoPlantillas`` muestra la lista (Título, Texto, Usos). Intro o «Usar»
deja el texto en ``texto_elegido`` (y suma un uso) y cierra con
``wx.ID_OK``. F2 edita y Supr borra (con confirmación; Intro en la
confirmación responde «No»).

``DialogoEditarPlantilla`` pide título y texto (multilínea) de una plantilla.
"""

from __future__ import annotations

import logging

import wx

from app.services.almacen import Almacen, Plantilla
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import confirmar, crear_botonera, mostrar_error, pedir_texto, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _, primera_linea

log = logging.getLogger(__name__)

#: Longitud máxima del título que se propone a partir del texto.
LARGO_TITULO_PROPUESTO = 60


def _ayudar_botonera(ventana: wx.Window) -> None:
	"""Ayuda (Ctrl+H) de los botones Aceptar y Cancelar de ``crear_botonera``."""
	for identificador, ayuda in (
		(wx.ID_OK, _("Guarda la plantilla y cierra este cuadro.")),
		(wx.ID_CANCEL, _("Cierra este cuadro sin guardar.")),
	):
		boton = ventana.FindWindowById(identificador, ventana)
		if boton is not None:
			boton.SetHelpText(ayuda)


class DialogoEditarPlantilla(wx.Dialog):
	"""Título y texto de una plantilla. ``valores()`` devuelve lo escrito."""

	def __init__(self, padre: wx.Window | None, titulo: str = "", texto: str = "") -> None:
		nombre = _("Editar plantilla") if titulo else _("Nueva plantilla")
		super().__init__(padre, title=nombre, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=nombre)
		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		_etiqueta, self.campo_titulo = crear_campo(
			panel, _("&Título:"), wx.TextCtrl, value=titulo, size=self.FromDIP(wx.Size(420, -1)),
			ayuda=_("Nombre corto con el que encontrarás la plantilla en la lista."), sizer=sizer,
		)
		_etiqueta, self.campo_texto = crear_campo(
			panel, _("Te&xto:"), wx.TextCtrl, value=texto, style=wx.TE_MULTILINE | wx.TE_RICH2,
			size=self.FromDIP(wx.Size(520, 220)),
			ayuda=_("Texto de la plantilla. Puede tener varias líneas; Intro añade una línea nueva."), sizer=sizer,
		)
		sizer.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		_ayudar_botonera(panel)
		panel.SetSizer(sizer)
		self.Bind(wx.EVT_BUTTON, self._al_aceptar, id=wx.ID_OK)
		preparar_dialogo(self, foco_inicial=self.campo_titulo)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()

	def valores(self) -> tuple[str, str]:
		"""``(título, texto)`` escritos, sin espacios sobrantes en el título."""
		return self.campo_titulo.GetValue().strip(), self.campo_texto.GetValue().rstrip()

	def validar(self) -> str | None:
		"""Mensaje de error si falta algo; ``None`` si está todo bien."""
		titulo, texto = self.valores()
		if not titulo:
			return _("Escribe un título para la plantilla.")
		if not texto.strip():
			return _("Escribe el texto de la plantilla.")
		return None

	def _al_aceptar(self, evento: wx.CommandEvent) -> None:
		error = self.validar()
		if error is None:
			evento.Skip()  # el botón Aceptar cierra con wx.ID_OK
			return
		mostrar_error(self, error)
		campo = self.campo_titulo if not self.valores()[0] else self.campo_texto
		if campo.IsShownOnScreen():
			campo.SetFocus()


class DialogoPlantillas(wx.Dialog):
	"""Lista de plantillas. Tras ``wx.ID_OK``, ``texto_elegido`` tiene el texto de la elegida."""

	def __init__(self, padre: wx.Window | None, almacen: Almacen, texto_actual: str = "") -> None:
		titulo = _("Plantillas de mensajes")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.almacen = almacen
		self.texto_actual = texto_actual or ""
		self.texto_elegido: str | None = None
		self.plantillas: list[Plantilla] = []

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.etiqueta = wx.StaticText(panel, label=_("&Plantillas:"))
		self.lista = ListaAccesible(panel, _("Plantillas"), [(_("Título"), 200), (_("Texto"), 380), (_("Usos"), 60)])
		self.lista.SetHelpText(_(
			"Plantillas guardadas. Intro usa la plantilla; F2 la edita; Supr la borra. "
			"Tecla Aplicaciones: más acciones."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(660, 280)))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_usar = self._boton(panel, botones, _("&Usar"), _("Pone el texto de la plantilla en el mensaje y cierra."), self.usar)
		self.boton_nueva = self._boton(panel, botones, _("&Nueva..."), _("Crea una plantilla nueva con título y texto."), self.nueva)
		self.boton_editar = self._boton(panel, botones, _("&Editar..."), _("Cambia el título o el texto de la plantilla seleccionada (F2)."), self.editar)
		self.boton_borrar = self._boton(panel, botones, _("&Borrar"), _("Borra la plantilla seleccionada; pide confirmación (Supr)."), self.borrar)
		self.boton_guardar_actual: wx.Button | None = None
		if self.texto_actual.strip():
			self.boton_guardar_actual = self._boton(
				panel, botones, _("Guardar el texto &actual como plantilla"),
				_("Guarda como plantilla el texto que tienes escrito en el mensaje; solo pide el título."),
				self.guardar_actual,
			)
		sizer.Add(botones, 0, wx.LEFT | wx.RIGHT, 4)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra las plantillas sin usar ninguna."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(sizer)

		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.usar())
		vincular_menu_contextual(self.lista, self._crear_menu)
		self.recargar()
		foco = self.lista if self.plantillas else (self.boton_guardar_actual or self.boton_nueva)
		preparar_dialogo(self, foco_inicial=foco)
		# Después de preparar_dialogo: este manejador se atiende antes que el suyo.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)

		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		if not self.plantillas:
			wx.CallAfter(voz.hablar, _("No hay plantillas. Pulsa Alt+N para crear una."))

	# ── Construcción ──────────────────────────────────────────────────────────

	@staticmethod
	def _boton(panel: wx.Window, sizer: wx.Sizer, etiqueta: str, ayuda: str, accion) -> wx.Button:
		boton = wx.Button(panel, label=etiqueta)
		boton.SetHelpText(ayuda)
		boton.Bind(wx.EVT_BUTTON, lambda _evento: accion())
		sizer.Add(boton, 0, wx.ALL, 4)
		return boton

	def _crear_menu(self) -> wx.Menu | None:
		menu = wx.Menu()
		hay = self.actual() is not None
		if hay:
			menu.Bind(wx.EVT_MENU, lambda _e: self.usar(), menu.Append(wx.ID_ANY, _("&Usar\tIntro")))
			menu.Bind(wx.EVT_MENU, lambda _e: self.editar(), menu.Append(wx.ID_ANY, _("&Editar...\tF2")))
			menu.Bind(wx.EVT_MENU, lambda _e: self.borrar(), menu.Append(wx.ID_ANY, _("&Borrar\tSupr")))
			menu.AppendSeparator()
		menu.Bind(wx.EVT_MENU, lambda _e: self.nueva(), menu.Append(wx.ID_ANY, _("&Nueva...")))
		if self.texto_actual.strip():
			menu.Bind(wx.EVT_MENU, lambda _e: self.guardar_actual(),
				menu.Append(wx.ID_ANY, _("Guardar el texto &actual como plantilla")))
		return menu

	# ── Datos ─────────────────────────────────────────────────────────────────

	def recargar(self, seleccionar_id: int | None = None) -> None:
		"""Vuelve a leer las plantillas; selecciona ``seleccionar_id`` si se indica."""
		fila_previa = self.lista.fila_actual()
		self.plantillas = self.almacen.plantillas()
		filas = [[p.titulo, primera_linea(p.texto, 200), str(p.usos)] for p in self.plantillas]
		self.lista.reemplazar_filas(filas, conservar_foco=False)
		self.etiqueta.SetLabel(_("&Plantillas ({n}):").format(n=len(filas)))
		destino = None
		if seleccionar_id is not None:
			destino = next((i for i, p in enumerate(self.plantillas) if p.id == seleccionar_id), None)
		if destino is None and fila_previa != wx.NOT_FOUND and self.plantillas:
			destino = min(fila_previa, len(self.plantillas) - 1)
		if destino is not None:
			self.lista.enfocar_fila(destino, anunciar=False)
		hay = bool(self.plantillas)
		for boton in (self.boton_usar, self.boton_editar, self.boton_borrar):
			boton.Enable(hay)

	def actual(self) -> Plantilla | None:
		"""Plantilla seleccionada o ``None``."""
		fila = self.lista.fila_actual()
		return self.plantillas[fila] if 0 <= fila < len(self.plantillas) else None

	def _editar_en_dialogo(self, titulo: str, texto: str) -> tuple[str, str] | None:
		"""Abre ``DialogoEditarPlantilla``; ``(título, texto)`` o ``None`` si se cancela."""
		dialogo = DialogoEditarPlantilla(self, titulo, texto)
		try:
			return dialogo.valores() if dialogo.ShowModal() == wx.ID_OK else None
		finally:
			dialogo.Destroy()

	def _enfocar_lista(self) -> None:
		if self.lista.IsShownOnScreen():
			self.lista.SetFocus()

	def _sin_seleccion(self) -> None:
		voz.hablar(_("No hay ninguna plantilla seleccionada") if self.plantillas else _("No hay plantillas"))

	# ── Acciones ──────────────────────────────────────────────────────────────

	def usar(self) -> bool:
		"""Elige la plantilla seleccionada y cierra con ``wx.ID_OK``."""
		plantilla = self.actual()
		if plantilla is None:
			self._sin_seleccion()
			return False
		try:
			self.almacen.usar_plantilla(plantilla.id)
		except Exception:
			log.warning("No se pudo anotar el uso de la plantilla %s", plantilla.id, exc_info=True)
		self.texto_elegido = plantilla.texto
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True

	def nueva(self) -> int | None:
		"""Crea una plantilla (propone el texto actual si lo hay). Devuelve su id."""
		titulo = primera_linea(self.texto_actual, LARGO_TITULO_PROPUESTO) if self.texto_actual.strip() else ""
		valores = self._editar_en_dialogo(titulo, self.texto_actual)
		if valores is None:
			return None
		identificador = self.almacen.guardar_plantilla(valores[0], valores[1])
		self.recargar(identificador)
		self._enfocar_lista()
		voz.hablar(_("Plantilla creada"))
		return identificador

	def editar(self) -> bool:
		"""Edita la plantilla seleccionada (F2)."""
		plantilla = self.actual()
		if plantilla is None:
			self._sin_seleccion()
			return False
		valores = self._editar_en_dialogo(plantilla.titulo, plantilla.texto)
		if valores is None:
			return False
		self.almacen.guardar_plantilla(valores[0], valores[1], plantilla.id)
		self.recargar(plantilla.id)
		self._enfocar_lista()
		voz.hablar(_("Plantilla guardada"))
		return True

	def borrar(self) -> bool:
		"""Borra la plantilla seleccionada tras confirmar (Supr)."""
		plantilla = self.actual()
		if plantilla is None:
			self._sin_seleccion()
			return False
		respuesta = confirmar(
			self, _("¿Borrar la plantilla «{titulo}»? No se puede deshacer.").format(titulo=plantilla.titulo),
			_("Borrar plantilla"), peligroso=True,
		)
		if respuesta != wx.ID_YES:
			return False
		self.almacen.borrar_plantilla(plantilla.id)
		self.recargar()
		self._enfocar_lista()
		voz.hablar(_("Plantilla borrada"))
		return True

	def guardar_actual(self) -> int | None:
		"""Guarda el texto actual del mensaje como plantilla (solo pide el título)."""
		if not self.texto_actual.strip():
			return None
		titulo = pedir_texto(
			self, _("&Título de la plantilla:"), _("Guardar el texto actual como plantilla"),
			primera_linea(self.texto_actual, LARGO_TITULO_PROPUESTO),
			ayuda=_("Nombre corto con el que encontrarás la plantilla en la lista."),
		)
		if titulo is None:
			return None
		titulo = titulo.strip()
		if not titulo:
			mostrar_error(self, _("Escribe un título para la plantilla."))
			return None
		identificador = self.almacen.guardar_plantilla(titulo, self.texto_actual.rstrip())
		self.recargar(identificador)
		self._enfocar_lista()
		voz.hablar(_("Plantilla creada"))
		return identificador

	# ── Eventos ───────────────────────────────────────────────────────────────

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		if modificadores == wx.MOD_NONE:
			if tecla == wx.WXK_F2:
				self.editar()
				return
			if tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
				self.borrar()
				return
		evento.Skip()

