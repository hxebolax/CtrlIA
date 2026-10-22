"""Un grupo abierto: sus sesiones y todo lo que se puede hacer con ellas.

Pensado para reunir, por ejemplo, todas las consultas de una investigación:

- Arriba, la descripción del grupo (lo que hayas apuntado), de solo lectura.
- La lista de sesiones del grupo, en el orden que tú decidas (Alt+flecha
  arriba y abajo) y con las columnas de Preferencias, «Lectura de listas».
- Intro abre la sesión en la ventana principal; Control+N crea una sesión
  nueva ya dentro del grupo; Insertar añade sesiones (varias a la vez); Supr
  la quita del grupo (la sesión no se borra); Control+M la mueve a otro grupo
  y Control+G la añade también a otro; F2 la renombra; Control+F filtra;
  Alt+Intro edita el grupo; Control+E exporta el grupo entero.

``contexto`` es la ventana principal (o un objeto con lo mismo en las
pruebas): ``opcion``, ``preferencias_lectura``, ``filas_sesiones``,
``renombrar_sesion`` y ``exportar_grupo``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import wx

from app.services.almacen import Almacen
from app.services.gestor import FilaSesion, coincide
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.casillas import CheckListBoxAccesible
from app.ui.accesibilidad.dialogos import crear_botonera, mostrar_error, pedir_texto, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _, fecha_relativa, nombre_agente

log = logging.getLogger(__name__)

#: Criterios de «Ordenar el grupo por»: (id, etiqueta).
ORDENES_GRUPO: list[tuple[str, str]] = [
	("recientes", _("Más &recientes primero")),
	("antiguas", _("Más &antiguas primero")),
	("titulo", _("Por &título")),
	("agente", _("Por a&gente")),
	("estado", _("Por &estado")),
]


def _no_disponible(clave: str) -> FilaSesion:
	"""Fila para una sesión del grupo que ya no aparece (borrada en el agente o historial desactivado)."""
	return FilaSesion(clave=clave, proveedor=clave.split(":", 1)[0], titulo=_("Sesión no disponible ({c})").format(c=clave),
		estado=_("No disponible"))


def _texto_eleccion(fila: FilaSesion) -> str:
	return _("{t}, {a}, {f}").format(t=fila.titulo, a=nombre_agente(fila.proveedor), f=fecha_relativa(fila.actividad))


class DialogoElegirSesiones(wx.Dialog):
	"""Casillas con sesiones (con filtro). Tras ``wx.ID_OK``, ``elegidas`` tiene sus claves."""

	def __init__(self, padre: wx.Window | None, titulo: str, filas: list[FilaSesion], ayuda: str) -> None:
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.filas = list(filas)
		self.marcadas: set[str] = set()
		self.elegidas: list[str] = []
		self._visibles: list[FilaSesion] = list(self.filas)

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		raiz.Add(wx.StaticText(panel, label=_("&Filtrar:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.campo_filtro = wx.TextCtrl(panel)
		self.campo_filtro.SetHelpText(_(
			"Escribe para ver solo las sesiones que contienen esas palabras (en el título, la carpeta o el agente). "
			"Lo que ya hayas marcado se conserva."
		))
		raiz.Add(self.campo_filtro, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
		self.etiqueta_lista = wx.StaticText(panel, label=_("&Sesiones:"))
		raiz.Add(self.etiqueta_lista, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = CheckListBoxAccesible(panel, _("Sesiones"), [], recordar_posicion=False)
		self.lista.SetHelpText(ayuda + " " + _("Barra espaciadora: marcar o desmarcar. Control+A marca todas las que se ven."))
		self.lista.SetMinSize(self.FromDIP(wx.Size(520, 300)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
		vincular_menu_contextual(self.lista, self._crear_menu)
		raiz.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		for identificador, texto in (
			(wx.ID_OK, _("Usa las sesiones marcadas y cierra.")),
			(wx.ID_CANCEL, _("Cierra sin cambiar nada.")),
		):
			boton = panel.FindWindowById(identificador, panel)
			if boton is not None:
				boton.SetHelpText(texto)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		self.campo_filtro.Bind(wx.EVT_TEXT, lambda _evento: self.filtrar(self.campo_filtro.GetValue()))
		self.lista.Bind(wx.EVT_CHECKLISTBOX, self._al_marcar)
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		self.filtrar("")
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.lista if self.filas else self.campo_filtro)

	def filtrar(self, texto: str) -> None:
		"""Muestra solo las sesiones que coinciden, conservando lo marcado."""
		self._recoger_marcas()
		texto = texto.strip()
		self._visibles = [f for f in self.filas if not texto or coincide(
			" ".join((f.titulo, f.carpeta, nombre_agente(f.proveedor))), texto)]
		self.lista.Set([_texto_eleccion(f) for f in self._visibles])
		for posicion, fila in enumerate(self._visibles):
			if fila.clave in self.marcadas:
				self.lista.Check(posicion, True)
		if self._visibles:
			self.lista.SetSelection(0)
		self.etiqueta_lista.SetLabel(_("&Sesiones ({n} de {t}):").format(n=len(self._visibles), t=len(self.filas)))

	def _recoger_marcas(self) -> None:
		for posicion, fila in enumerate(getattr(self, "_visibles", [])):
			if posicion < self.lista.GetCount():
				if self.lista.IsChecked(posicion):
					self.marcadas.add(fila.clave)
				else:
					self.marcadas.discard(fila.clave)

	def _al_marcar(self, evento: wx.CommandEvent) -> None:
		self._recoger_marcas()
		evento.Skip()

	def _crear_menu(self) -> wx.Menu | None:
		if not self._visibles:
			return None
		return menu_de_acciones(self.lista, [
			(_("Marcar o &desmarcar esta sesión\tEspacio"), self.alternar_actual),
			(_("Marcar &todas las que se ven\tCtrl+A"), lambda: self.marcar_visibles(True)),
			(_("&Quitar la marca a todas las que se ven\tCtrl+Mayús+A"), lambda: self.marcar_visibles(False)),
		])

	def alternar_actual(self) -> None:
		posicion = self.lista.GetSelection()
		if 0 <= posicion < self.lista.GetCount():
			self.lista.Check(posicion, not self.lista.IsChecked(posicion))
			self._recoger_marcas()

	def marcar_visibles(self, marcar: bool) -> None:
		self.lista.marcar_todas(marcar)
		self._recoger_marcas()

	def aceptar(self) -> bool:
		self._recoger_marcas()
		self.elegidas = [f.clave for f in self.filas if f.clave in self.marcadas]
		if not self.elegidas:
			mostrar_error(self, _("Marca al menos una sesión con la barra espaciadora."))
			return False
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True


def elegir_sesiones(padre: wx.Window | None, titulo: str, filas: list[FilaSesion], ayuda: str) -> list[str]:
	"""Devuelve las claves marcadas (lista vacía si se cancela)."""
	dialogo = DialogoElegirSesiones(padre, titulo, filas, ayuda)
	try:
		return list(dialogo.elegidas) if dialogo.ShowModal() == wx.ID_OK else []
	finally:
		dialogo.Destroy()


class DialogoGrupo(wx.Dialog):
	"""Las sesiones de un grupo. Tras cerrar, ``accion`` dice si hay que abrir o crear una sesión."""

	def __init__(self, padre: wx.Window | None, almacen: Almacen, id_grupo: int, contexto: Any) -> None:
		self.almacen = almacen
		self.id_grupo = id_grupo
		self.contexto = contexto
		self.grupo = almacen.grupo(id_grupo)
		nombre = self.grupo.nombre if self.grupo else ""
		titulo = _("Grupo «{n}»").format(n=nombre)
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		#: ("abrir", clave) o ("nueva", id del grupo) para la ventana principal.
		self.accion: tuple[str, Any] | None = None
		self.cambiado = False
		self._filtro = ""
		self._filas: list[FilaSesion] = []

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		raiz.Add(wx.StaticText(panel, label=_("Descripción del &grupo:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.campo_descripcion = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
		self.campo_descripcion.SetHelpText(_(
			"Lo que has apuntado sobre este grupo, de solo lectura. Para cambiarlo, Alt+Intro o «Editar el grupo»."
		))
		self.campo_descripcion.SetMinSize(self.FromDIP(wx.Size(560, 70)))
		raiz.Add(self.campo_descripcion, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
		self.etiqueta_lista = wx.StaticText(panel, label=_("&Sesiones:"))
		raiz.Add(self.etiqueta_lista, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = ListaAccesible(panel, _("Sesiones del grupo"), [self._columnas_def()[c] for c in self._columnas()],
			leer_preferencias=getattr(contexto, "preferencias_lectura", None))
		self.lista.SetHelpText(_(
			"Sesiones del grupo, en el orden que tú decides. Intro abre la sesión; Control+N crea una nueva dentro del "
			"grupo; Insertar añade sesiones; Supr la quita del grupo (no se borra); Control+M la mueve a otro grupo; "
			"Control+G la añade también a otro; Alt+flecha arriba y abajo cambian el orden; F2 la renombra; "
			"Control+F filtra; Control+E exporta el grupo entero; Alt+Intro edita el grupo. Tecla Aplicaciones: todo lo demás."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(560, 260)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_abrir = self._boton(panel, botones, _("&Abrir la sesión"), _("Abre la sesión seleccionada en la ventana principal (Intro)."), self.abrir)
		self.boton_nueva = self._boton(panel, botones, _("&Nueva sesión en el grupo..."),
			_("Crea una sesión nueva que queda dentro de este grupo, con su agente y su carpeta (Control+N)."), self.nueva)
		self.boton_anadir = self._boton(panel, botones, _("Aña&dir sesiones..."),
			_("Elige una o varias sesiones para meterlas en el grupo (Insertar)."), self.anadir_sesiones)
		self.boton_quitar = self._boton(panel, botones, _("&Quitar del grupo"),
			_("Saca la sesión seleccionada del grupo. La sesión no se borra (Supr)."), self.quitar)
		sizer_botones2 = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_editar = self._boton(panel, sizer_botones2, _("&Editar el grupo..."),
			_("Cambia el nombre, la descripción, el agente y la carpeta del grupo (Alt+Intro)."), self.editar)
		self.boton_exportar = self._boton(panel, sizer_botones2, _("E&xportar el grupo..."),
			_("Guarda todas las sesiones del grupo en un solo archivo Markdown, HTML o de texto (Control+E)."), self.exportar)
		raiz.Add(botones, 0, wx.LEFT | wx.RIGHT, 4)
		raiz.Add(sizer_botones2, 0, wx.LEFT | wx.RIGHT, 4)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra el grupo y vuelve a la lista de grupos."))
		raiz.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)

		vincular_menu_contextual(self.lista, self._crear_menu)
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.abrir())
		self.recargar()
		preparar_dialogo(self, foco_inicial=self.lista if self._filas else self.boton_anadir)
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		if not self._filas and self._opcion("lectura.anunciar_vista_vacia", True):
			wx.CallAfter(voz.hablar, _("Este grupo está vacío. Pulsa Insertar para añadir sesiones o Control+N para crear una."))

	# ── Utilidades ────────────────────────────────────────────────────────────

	def _opcion(self, clave: str, defecto: Any = None) -> Any:
		opcion = getattr(self.contexto, "opcion", None)
		return opcion(clave, defecto) if callable(opcion) else defecto

	@staticmethod
	def _columnas_def() -> dict[str, tuple[str, int]]:
		from app.ui.vp_sesiones import COLUMNAS

		return COLUMNAS

	def _columnas(self) -> list[str]:
		from app.ui.lectura_listas import columnas_validas
		from app.ui.vp_sesiones import COLUMNAS

		return columnas_validas(self._opcion("grupos.columnas_sesiones", None), COLUMNAS,
			["titulo", "estado", "agente", "actividad"])

	@staticmethod
	def _boton(panel: wx.Window, sizer: wx.Sizer, etiqueta: str, ayuda: str, accion: Callable[[], object]) -> wx.Button:
		boton = wx.Button(panel, label=etiqueta)
		boton.SetHelpText(ayuda)
		boton.Bind(wx.EVT_BUTTON, lambda _evento: accion())
		sizer.Add(boton, 0, wx.ALL, 4)
		return boton

	def _todas(self) -> list[FilaSesion]:
		filas = getattr(self.contexto, "filas_sesiones", None)
		return list(filas()) if callable(filas) else []

	def _cerrar_con(self, accion: tuple[str, Any]) -> None:
		self.accion = accion
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()

	def _anunciar(self, texto: str) -> None:
		voz.hablar(texto, interrumpir=True)

	# ── Datos ─────────────────────────────────────────────────────────────────

	def recargar(self, seleccionar: str | None = None) -> None:
		"""Vuelve a leer el grupo; deja seleccionada la sesión ``seleccionar`` (o la misma posición)."""
		from app.ui.vp_sesiones import valores_sesion

		self.grupo = self.almacen.grupo(self.id_grupo)
		if self.grupo is None:
			self._filas = []
			self.lista.limpiar()
			return
		fila_previa = self.lista.fila_actual()
		por_clave = {f.clave: f for f in self._todas()}
		filas = [por_clave.get(clave) or _no_disponible(clave) for clave in self.almacen.sesiones_de_grupo(self.id_grupo)]
		if self._filtro:
			filas = [f for f in filas if coincide(" ".join((f.titulo, f.carpeta, f.ultimo_mensaje, nombre_agente(f.proveedor))), self._filtro)]
		self._filas = filas
		columnas = self._columnas()
		opcion = getattr(self.contexto, "opcion", None) or (lambda _clave, defecto=None: defecto)
		self.lista.reemplazar_filas([valores_sesion(f, columnas, opcion) for f in filas], conservar_foco=False)
		self.campo_descripcion.ChangeValue(self.grupo.descripcion or _("Sin descripción. Pulsa Alt+Intro para escribir de qué trata el grupo."))
		texto = _("Sesiones del grupo «{g}» ({n})").format(g=self.grupo.nombre, n=len(filas))
		if self._filtro:
			texto += _(", filtro: {f}").format(f=self._filtro)
		self.etiqueta_lista.SetLabel("&" + texto + ":")
		self.lista.SetName(texto)
		self.SetTitle(_("Grupo «{n}»").format(n=self.grupo.nombre))
		destino = None
		if seleccionar is not None:
			destino = next((i for i, f in enumerate(filas) if f.clave == seleccionar), None)
		if destino is None and fila_previa != wx.NOT_FOUND and filas:
			destino = min(fila_previa, len(filas) - 1)
		if destino is not None:
			self.lista.enfocar_fila(destino, anunciar=False)
		for boton in (self.boton_abrir, self.boton_quitar):
			boton.Enable(bool(filas))

	def actual(self) -> FilaSesion | None:
		fila = self.lista.fila_actual()
		return self._filas[fila] if 0 <= fila < len(self._filas) else None

	def _sin_seleccion(self) -> None:
		self._anunciar(_("Ninguna sesión seleccionada") if self._filas else _("El grupo está vacío"))

	# ── Menú contextual ──────────────────────────────────────────────────────

	def _crear_menu(self) -> wx.Menu:
		menu = wx.Menu()

		def anadir(destino: wx.Menu, etiqueta: str, accion: Callable[[], object]) -> None:
			destino.Bind(wx.EVT_MENU, lambda _e: accion(), destino.Append(wx.ID_ANY, etiqueta))

		if self.actual() is not None:
			anadir(menu, _("&Abrir la sesión\tIntro"), self.abrir)
			anadir(menu, _("&Renombrar la sesión...\tF2"), self.renombrar)
			anadir(menu, _("&Copiar el título\tCtrl+C"), self.copiar_titulo)
			menu.AppendSeparator()
			anadir(menu, _("&Mover a otro grupo...\tCtrl+M"), self.mover_a_otro)
			anadir(menu, _("Añadir &también a otro grupo...\tCtrl+G"), self.anadir_a_otro)
			anadir(menu, _("&Quitar del grupo\tSupr"), self.quitar)
			anadir(menu, _("Su&bir\tAlt+Arriba"), lambda: self.mover(-1))
			anadir(menu, _("Ba&jar\tAlt+Abajo"), lambda: self.mover(1))
			menu.AppendSeparator()
		anadir(menu, _("&Nueva sesión en el grupo...\tCtrl+N"), self.nueva)
		anadir(menu, _("Aña&dir sesiones...\tInsertar"), self.anadir_sesiones)
		if self._filas:
			anadir(menu, _("Quitar &varias sesiones..."), self.quitar_varias)
		ordenar = wx.Menu()
		for criterio, etiqueta in ORDENES_GRUPO:
			anadir(ordenar, etiqueta, lambda c=criterio: self.ordenar_por(c))
		menu.AppendSubMenu(ordenar, _("&Ordenar el grupo por"))
		anadir(menu, _("&Filtrar...\tCtrl+F"), self.filtrar)
		menu.AppendSeparator()
		anadir(menu, _("&Editar el grupo...\tAlt+Intro"), self.editar)
		anadir(menu, _("E&xportar el grupo entero...\tCtrl+E"), self.exportar)
		return menu

	# ── Acciones ──────────────────────────────────────────────────────────────

	def abrir(self) -> bool:
		"""Cierra y pide a la ventana principal que abra la sesión (Intro)."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		if fila.estado == _("No disponible"):
			mostrar_error(self, _("Esta sesión ya no está disponible: puede que se borrara en el agente o que su "
				"historial esté desactivado en Preferencias. Puedes quitarla del grupo con Supr."), _("Abrir la sesión"))
			return False
		self._cerrar_con(("abrir", fila.clave))
		return True

	def nueva(self) -> bool:
		"""Cierra y pide crear una sesión nueva dentro del grupo (Control+N)."""
		self._cerrar_con(("nueva", self.id_grupo))
		return True

	def anadir_sesiones(self) -> int:
		"""Mete en el grupo una o varias sesiones elegidas con casillas (Insertar)."""
		dentro = set(self.almacen.sesiones_de_grupo(self.id_grupo))
		candidatas = [f for f in self._todas() if f.clave not in dentro and not f.oculta]
		if not candidatas:
			self._anunciar(_("Todas las sesiones de la lista ya están en este grupo."))
			return 0
		claves = elegir_sesiones(self, _("Añadir sesiones al grupo «{g}»").format(g=self.grupo.nombre), candidatas,
			_("Marca las sesiones que quieres meter en el grupo."))
		for clave in claves:
			self.almacen.anadir_a_grupo(clave, self.id_grupo)
		if claves:
			self.cambiado = True
			self._filtro = ""
			self.recargar(claves[-1])
			self._anunciar(_("{n} sesiones añadidas al grupo.").format(n=len(claves)) if len(claves) != 1
				else _("Sesión añadida al grupo."))
		return len(claves)

	def quitar(self) -> bool:
		"""Saca la sesión seleccionada del grupo (Supr). La sesión no se borra."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		self.almacen.quitar_de_grupo(fila.clave, self.id_grupo)
		self.cambiado = True
		self.recargar()
		self._anunciar(_("Quitada del grupo. Quedan {n}.").format(n=len(self._filas)))
		return True

	def quitar_varias(self) -> int:
		"""Saca del grupo varias sesiones marcadas con casillas."""
		if not self._filas:
			self._sin_seleccion()
			return 0
		claves = elegir_sesiones(self, _("Quitar sesiones del grupo «{g}»").format(g=self.grupo.nombre), list(self._filas),
			_("Marca las sesiones que quieres sacar del grupo. No se borran."))
		for clave in claves:
			self.almacen.quitar_de_grupo(clave, self.id_grupo)
		if claves:
			self.cambiado = True
			self.recargar()
			self._anunciar(_("{n} sesiones quitadas del grupo.").format(n=len(claves)))
		return len(claves)

	def _elegir_otro_grupo(self, titulo: str) -> int | None:
		from app.ui.dialogos.grupos import elegir_grupo

		return elegir_grupo(self, self.almacen, titulo, excluir=(self.id_grupo,))

	def mover_a_otro(self) -> bool:
		"""Saca la sesión de este grupo y la mete en otro en un solo paso (Control+M)."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		destino = self._elegir_otro_grupo(_("Mover «{t}» a otro grupo").format(t=fila.titulo))
		if destino is None:
			return False
		self.almacen.mover_a_grupo(fila.clave, self.id_grupo, destino)
		self.cambiado = True
		self.recargar()
		nombre = dict(self.almacen.grupos()).get(destino, "")
		self._anunciar(_("Movida al grupo {g}.").format(g=nombre))
		return True

	def anadir_a_otro(self) -> bool:
		"""Añade la sesión también a otro grupo sin sacarla de este (Control+G)."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		destino = self._elegir_otro_grupo(_("Añadir «{t}» también a otro grupo").format(t=fila.titulo))
		if destino is None:
			return False
		self.almacen.anadir_a_grupo(fila.clave, destino)
		self.cambiado = True
		nombre = dict(self.almacen.grupos()).get(destino, "")
		if nombre and nombre not in fila.grupos:
			fila.grupos.append(nombre)
		self.recargar(fila.clave)
		self._anunciar(_("Añadida también al grupo {g}.").format(g=nombre))
		return True

	def mover(self, paso: int) -> bool:
		"""Sube o baja la sesión dentro del grupo (Alt+flecha arriba / abajo)."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		if self._filtro:
			self._anunciar(_("Quita el filtro (Escape) para cambiar el orden."))
			return False
		posicion = self.lista.fila_actual() + paso
		if not 0 <= posicion < len(self._filas):
			self._anunciar(_("Ya es la primera") if posicion < 0 else _("Ya es la última"))
			return False
		self.almacen.mover_en_grupo(fila.clave, self.id_grupo, paso)
		self.cambiado = True
		self.recargar(fila.clave)
		self._anunciar(_("Movida a la posición {n} de {t}").format(n=posicion + 1, t=len(self._filas)))
		return True

	def ordenar_por(self, criterio: str) -> None:
		"""Reordena todo el grupo una vez por el criterio (después se puede retocar a mano)."""
		from app.ui.vp_sesiones import _clave_orden

		if self._filtro:
			self._anunciar(_("Quita el filtro (Escape) para cambiar el orden."))
			return
		funcion, inverso = _clave_orden(criterio)
		ordenadas = sorted(self._filas, key=funcion, reverse=inverso)
		self.almacen.ordenar_grupo(self.id_grupo, [f.clave for f in ordenadas])
		self.cambiado = True
		self.recargar()
		self._anunciar(_("Grupo ordenado."))

	def renombrar(self) -> bool:
		"""Cambia el título de la sesión seleccionada (F2)."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		titulo = pedir_texto(self, _("&Nuevo título:"), _("Renombrar sesión"), fila.titulo)
		if titulo is None or not titulo.strip():
			return False
		renombrar = getattr(self.contexto, "renombrar_sesion", None)
		if callable(renombrar):
			renombrar(fila.clave, titulo.strip())
		fila.titulo = titulo.strip()
		self.cambiado = True
		self.recargar(fila.clave)
		self._anunciar(_("Renombrada."))
		return True

	def copiar_titulo(self) -> bool:
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		from app.ui.accesibilidad.teclado import copiar_texto

		if copiar_texto(fila.titulo):
			self._anunciar(_("Copiado."))
			return True
		self._anunciar(_("No se pudo usar el portapapeles."))
		return False

	def filtrar(self) -> None:
		texto = pedir_texto(self, _("&Mostrar solo las sesiones que contengan:"), _("Filtrar el grupo"), self._filtro)
		if texto is None:
			return
		self._filtro = texto.strip()
		self.recargar()
		if self._filtro:
			self._anunciar(_("{n} sesiones coinciden. Escape quita el filtro.").format(n=len(self._filas)))

	def quitar_filtro(self) -> None:
		self._filtro = ""
		self.recargar()
		self._anunciar(_("Filtro quitado. {n} sesiones.").format(n=len(self._filas)))

	def editar(self) -> bool:
		"""Edita los datos del grupo (Alt+Intro)."""
		from app.ui.dialogos.grupos import editar_grupo

		if self.grupo is None or editar_grupo(self, self.almacen, self.grupo) is None:
			return False
		self.cambiado = True
		actual = self.actual()
		self.recargar(actual.clave if actual else None)
		self._anunciar(_("Grupo guardado"))
		return True

	def exportar(self) -> bool:
		exportar = getattr(self.contexto, "exportar_grupo", None)
		if not callable(exportar):
			return False
		if not self._filas and not self.almacen.sesiones_de_grupo(self.id_grupo):
			self._anunciar(_("El grupo está vacío: no hay nada que exportar."))
			return False
		exportar(self.id_grupo, self)
		return True

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		en_lista = wx.Window.FindFocus() is self.lista
		if mods == wx.MOD_NONE and tecla == wx.WXK_ESCAPE and self._filtro:
			self.quitar_filtro()
			return
		if mods == wx.MOD_NONE and tecla in (wx.WXK_INSERT, wx.WXK_NUMPAD_INSERT):
			self.anadir_sesiones()
			return
		if mods == wx.MOD_CONTROL and tecla == ord("N"):
			self.nueva()
			return
		if mods == wx.MOD_CONTROL and tecla == ord("E"):
			self.exportar()
			return
		if mods == wx.MOD_CONTROL and tecla == ord("F"):
			self.filtrar()
			return
		if mods == wx.MOD_ALT and tecla in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.editar()
			return
		if en_lista:
			if mods == wx.MOD_NONE and tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
				self.quitar()
				return
			if mods == wx.MOD_NONE and tecla == wx.WXK_F2:
				self.renombrar()
				return
			if mods == wx.MOD_CONTROL and tecla == ord("M"):
				self.mover_a_otro()
				return
			if mods == wx.MOD_CONTROL and tecla == ord("G"):
				self.anadir_a_otro()
				return
			if mods == wx.MOD_CONTROL and tecla == ord("C"):
				self.copiar_titulo()
				return
			if mods == wx.MOD_ALT and tecla in (wx.WXK_UP, wx.WXK_NUMPAD_UP):
				self.mover(-1)
				return
			if mods == wx.MOD_ALT and tecla in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
				self.mover(1)
				return
		evento.Skip()


def abrir_grupo(padre: wx.Window | None, almacen: Almacen, id_grupo: int, contexto: Any) -> tuple[str, Any] | None:
	"""Muestra un grupo. Devuelve la petición para la ventana principal o ``None``."""
	dialogo = DialogoGrupo(padre, almacen, id_grupo, contexto)
	try:
		dialogo.ShowModal()
		return dialogo.accion
	finally:
		dialogo.Destroy()

