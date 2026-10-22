"""Elegir con casillas qué agentes del catálogo usa una sesión.

- ``PanelAgentes``: panel reutilizable (para «Nueva sesión» y «Opciones de la
  sesión») con un filtro por categoría, la lista de casillas (``nombre
  (categoría) — descripción``, ordenada por categoría y nombre) y el botón
  «Gestionar agentes...», que abre el gestor y, al volver, recarga la lista
  sin perder lo marcado. ``seleccionados()`` y ``seleccionar(ids)``.
- ``DialogoElegirAgentes`` y ``elegir_agentes(padre, seleccionados)``: el
  mismo panel en un cuadro con Aceptar y Cancelar. Devuelve los ids marcados
  o ``None`` si se cancela.

Lo marcado se guarda por id, así que cambiar el filtro no lo pierde. Los
agentes cuya carpeta ya no está se ven con «(no encontrado)».
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import wx

from app.services import catalogo_agentes as servicio
from app.services.almacen import AgenteCatalogo, Almacen
from app.services.catalogo_agentes import CatalogoAgentes
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.casillas import CheckListBoxAccesible
from app.ui.accesibilidad.dialogos import crear_botonera, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo, nombre_limpio
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _
from app.ui.dialogos.gestor_agentes import RESUMEN_DESCRIPCION, abrir_gestor, mostrar_skill, resumen

log = logging.getLogger(__name__)


def _buscar_en_ventanas(ventana: wx.Window | None, nombre: str) -> Any:
	"""Primer atributo ``nombre`` de ``ventana``, sus padres o la ventana principal de la aplicación."""
	actual = ventana
	while actual is not None:
		valor = getattr(actual, nombre, None)
		if valor is not None:
			return valor
		actual = actual.GetParent()
	aplicacion = wx.GetApp()
	principal = aplicacion.GetTopWindow() if aplicacion else None
	return getattr(principal, nombre, None) if principal is not None else None


def almacen_de(ventana: wx.Window | None) -> Almacen | None:
	"""El ``Almacen`` de la ventana principal (buscándolo desde ``ventana`` hacia arriba)."""
	almacen = _buscar_en_ventanas(ventana, "almacen")
	return almacen if isinstance(almacen, Almacen) else None


def editor_de(ventana: wx.Window | None) -> str:
	"""El editor externo de Preferencias (``agentes.editor_externo``) o vacío."""
	opcion = _buscar_en_ventanas(ventana, "opcion")
	if callable(opcion):
		try:
			return str(opcion("agentes.editor_externo", "") or "")
		except (KeyError, ValueError, TypeError):
			log.debug("No se pudo leer el editor externo", exc_info=True)
	return ""


def texto_casilla(agente: AgenteCatalogo) -> str:
	"""Texto de un agente en la lista de casillas: «nombre (categoría) — descripción»."""
	texto = agente.nombre
	if agente.categoria:
		texto += f" ({agente.categoria})"
	if not servicio.disponible(agente):
		texto += " " + _("(no encontrado)")
	if agente.descripcion:
		texto += " — " + resumen(agente.descripcion, RESUMEN_DESCRIPCION)
	return texto


class PanelAgentes(wx.Panel):
	"""Filtro por categoría, lista de casillas con los agentes del catálogo y botón «Gestionar agentes...».

	:param almacen: el de la aplicación; ``None``: se busca en la ventana principal.
	:param seleccionados: ids marcados al empezar.
	:param editor: editor externo para el gestor; ``None``: el de Preferencias.
	:param con_filtro: mostrar el filtro por categoría.
	:param etiqueta, etiqueta_filtro, etiqueta_boton: textos con su ``&``; cámbielos si
		chocan con los de la ventana donde se mete el panel.
	"""

	def __init__(self, padre: wx.Window, almacen: Almacen | None = None, seleccionados: Iterable[int] = (),
			editor: str | None = None, con_filtro: bool = True, etiqueta: str = "", etiqueta_filtro: str = "",
			etiqueta_boton: str = "") -> None:
		super().__init__(padre, name=_("Agentes de la sesión"))
		self.almacen = almacen if almacen is not None else almacen_de(padre)
		self.catalogo = CatalogoAgentes(self.almacen) if self.almacen is not None else None
		self._editor = editor
		#: Ids marcados (también los que el filtro oculta), en el orden en que se marcaron.
		self._marcados: dict[int, None] = dict.fromkeys(int(i) for i in seleccionados)
		self.visibles: list[AgenteCatalogo] = []

		sizer = wx.BoxSizer(wx.VERTICAL)
		self.campo_categoria: wx.Choice | None = None
		if con_filtro:
			_e, self.campo_categoria = crear_campo(self, etiqueta_filtro or _("Ca&tegoría de agentes:"), wx.Choice,
				sizer=sizer, horizontal=True, expandir=False, choices=[_("Todas")],
				ayuda=_("Muestra en la lista solo los agentes de una categoría. Lo que ya has marcado se conserva."))
			self.campo_categoria.SetSelection(0)
			self.campo_categoria.Bind(wx.EVT_CHOICE, lambda _evento: self.cargar())
		texto_etiqueta = etiqueta or _("Age&ntes para la sesión:")
		self.etiqueta = wx.StaticText(self, label=texto_etiqueta)
		self._nombre_lista = nombre_limpio(texto_etiqueta)
		self.lista = CheckListBoxAccesible(self, self._nombre_lista, [], recordar_posicion=False)
		self.lista.SetHelpText(_(
			"Agentes del catálogo que usará la sesión. La barra espaciadora marca o desmarca; Control+A los marca todos y "
			"Control+Mayúsculas+A los desmarca. Están ordenados por categoría. Tecla Aplicaciones: ver su SKILL.md o "
			"abrir el gestor de agentes para añadir más."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(440, 140)))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
		vincular_menu_contextual(self.lista, self._crear_menu)
		self.boton_gestionar = wx.Button(self, label=etiqueta_boton or _("&Gestionar agentes..."))
		self.boton_gestionar.SetHelpText(_(
			"Abre el gestor de agentes para añadir, crear, editar o quitar agentes del catálogo. Al cerrarlo, esta lista se actualiza."
		))
		self.boton_gestionar.Bind(wx.EVT_BUTTON, lambda _evento: self.gestionar())
		self.boton_gestionar.Enable(self.almacen is not None)
		sizer.Add(self.boton_gestionar, 0, wx.ALL, 5)
		self.SetSizer(sizer)
		self.lista.Bind(wx.EVT_CHECKLISTBOX, self._al_marcar)
		self.cargar()

	# ── Datos ─────────────────────────────────────────────────────────────────

	def categoria_filtro(self) -> str | None:
		if self.campo_categoria is None:
			return None
		indice = self.campo_categoria.GetSelection()
		if indice <= 0:
			return None
		if indice == 1:
			return ""
		return self.campo_categoria.GetString(indice)

	def _rellenar_categorias(self) -> None:
		if self.campo_categoria is None or self.catalogo is None:
			return
		anterior = self.categoria_filtro()
		opciones = [_("Todas"), _("Sin categoría"), *self.catalogo.categorias()]
		if list(self.campo_categoria.GetItems()) != opciones:
			self.campo_categoria.SetItems(opciones)
		if anterior is None:
			posicion = 0
		elif anterior == "":
			posicion = 1
		else:
			posicion = next((i for i, o in enumerate(opciones) if i > 1 and o.casefold() == anterior.casefold()), 0)
		self.campo_categoria.SetSelection(posicion)

	def _sincronizar(self) -> None:
		"""Pasa a ``_marcados`` lo que hay marcado en la lista (Ctrl+A no envía eventos)."""
		for indice, agente in enumerate(self.visibles):
			if indice >= self.lista.GetCount():
				break
			if self.lista.IsChecked(indice):
				self._marcados.setdefault(agente.id, None)
			else:
				self._marcados.pop(agente.id, None)

	def cargar(self) -> None:
		"""Rellena la lista con el filtro actual, conservando lo marcado y el elemento elegido."""
		self._sincronizar()
		elegido = self.agente_elegido()
		self._rellenar_categorias()
		self.visibles = self.catalogo.listar(self.categoria_filtro()) if self.catalogo is not None else []
		self.lista.Set([texto_casilla(a) for a in self.visibles])
		for indice, agente in enumerate(self.visibles):
			if agente.id in self._marcados:
				self.lista.Check(indice, True)
		total = len(self.visibles)
		if total:
			destino = next((i for i, a in enumerate(self.visibles) if elegido is not None and a.id == elegido.id), 0)
			self.lista.SetSelection(destino)
		nombre = self._nombre_lista
		if self.catalogo is not None and not self.catalogo.almacen.agentes_catalogo():
			nombre = _("{nombre}: el catálogo está vacío, pulsa «Gestionar agentes» para añadirlos").format(nombre=nombre)
		else:
			nombre = _("{nombre} ({n})").format(nombre=nombre, n=total)
		self.lista.SetName(nombre)

	def agente_elegido(self) -> AgenteCatalogo | None:
		indice = self.lista.GetSelection()
		return self.visibles[indice] if 0 <= indice < len(self.visibles) else None

	def _al_marcar(self, evento: wx.CommandEvent) -> None:
		indice = evento.GetInt()
		if 0 <= indice < len(self.visibles):
			identificador = self.visibles[indice].id
			if self.lista.IsChecked(indice):
				self._marcados.setdefault(identificador, None)
			else:
				self._marcados.pop(identificador, None)
		evento.Skip()

	def seleccionados(self) -> list[int]:
		"""Ids marcados que siguen en el catálogo, en el orden del catálogo (categoría y nombre)."""
		self._sincronizar()
		if self.catalogo is None:
			return list(self._marcados)
		return [a.id for a in self.catalogo.listar() if a.id in self._marcados]

	def seleccionar(self, ids: Iterable[int]) -> None:
		"""Deja marcados exactamente esos ids (los que no están en el catálogo se ignoran al pedirlos)."""
		self._marcados = dict.fromkeys(int(i) for i in ids)
		for indice, agente in enumerate(self.visibles):
			marcar = agente.id in self._marcados
			if self.lista.IsChecked(indice) != marcar:
				self.lista.Check(indice, marcar)

	# ── Acciones ──────────────────────────────────────────────────────────────

	def gestionar(self) -> bool:
		"""Abre el gestor de agentes y, al volver, recarga la lista. Devuelve si cambió el catálogo."""
		if self.almacen is None:
			return False
		self._sincronizar()
		editor = self._editor if self._editor is not None else editor_de(self)
		cambiado = abrir_gestor(wx.GetTopLevelParent(self), self.almacen, editor)
		self.cargar()
		if self.lista and self.lista.IsShownOnScreen():
			self.lista.SetFocus()
		return cambiado

	def ver_skill(self) -> None:
		agente = self.agente_elegido()
		if agente is None:
			voz.hablar(_("No hay ningún agente elegido"))
			return
		mostrar_skill(wx.GetTopLevelParent(self), agente.nombre, servicio.ruta_skill(agente.carpeta))

	def _crear_menu(self) -> wx.Menu:
		menu = wx.Menu()
		hay = self.agente_elegido() is not None
		for etiqueta, accion, activa in (
			(_("&Marcar o desmarcar\tEspacio"), lambda: self.lista.alternar(), hay),
			(_("Marcar &todos\tCtrl+A"), lambda: self.lista.marcar_todas(True), bool(self.visibles)),
			(_("&Desmarcar todos\tCtrl+Shift+A"), lambda: self.lista.marcar_todas(False), bool(self.visibles)),
			(_("&Ver el SKILL.md"), self.ver_skill, hay),
			(_("&Gestionar agentes..."), self.gestionar, self.almacen is not None),
		):
			elemento = menu.Append(wx.ID_ANY, etiqueta)
			elemento.Enable(activa)
			menu.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), elemento)
		return menu


class DialogoElegirAgentes(wx.Dialog):
	"""``PanelAgentes`` con Aceptar y Cancelar. Tras ``wx.ID_OK``, ``elegidos`` tiene los ids marcados."""

	def __init__(self, padre: wx.Window | None, seleccionados: Iterable[int] = (), almacen: Almacen | None = None,
			editor: str | None = None) -> None:
		titulo = _("Agentes de la sesión")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.elegidos: list[int] = []
		raiz = wx.BoxSizer(wx.VERTICAL)
		explicacion = wx.StaticText(self, label=_(
			"Marca con la barra espaciadora los agentes que quieres que use la sesión. Para añadir o crear agentes, "
			"pulsa «Gestionar agentes»."
		))
		explicacion.Wrap(self.FromDIP(520))
		raiz.Add(explicacion, 0, wx.ALL, 8)
		self.panel = PanelAgentes(self, almacen if almacen is not None else almacen_de(padre), seleccionados, editor)
		raiz.Add(self.panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		for identificador, ayuda in (
			(wx.ID_OK, _("Usa los agentes marcados en la sesión.")),
			(wx.ID_CANCEL, _("Cierra sin cambiar los agentes de la sesión.")),
		):
			boton = self.FindWindowById(identificador, self)
			if boton is not None:
				boton.SetHelpText(ayuda)
		self.SetSizerAndFit(raiz)
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		preparar_dialogo(self, foco_inicial=self.panel.lista)
		self.CentreOnParent()

	def aceptar(self) -> None:
		self.elegidos = self.panel.seleccionados()
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()


def elegir_agentes(padre: wx.Window | None, seleccionados: Iterable[int] = (), almacen: Almacen | None = None,
		editor: str | None = None) -> list[int] | None:
	"""Cuadro para marcar los agentes de una sesión. Devuelve los ids marcados o ``None`` si se cancela.

	Sin ``almacen`` se usa el de la ventana principal (``padre`` o sus padres).
	"""
	dialogo = DialogoElegirAgentes(padre, list(seleccionados), almacen, editor)
	foco_previo = wx.Window.FindFocus()
	try:
		if dialogo.ShowModal() != wx.ID_OK:
			return None
		return list(dialogo.elegidos)
	finally:
		dialogo.Destroy()
		if foco_previo:
			try:
				foco_previo.SetFocus()
			except RuntimeError:
				log.debug("El control que tenía el foco ya no existe", exc_info=True)
