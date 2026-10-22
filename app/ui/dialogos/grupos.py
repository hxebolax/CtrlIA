"""Grupos de sesiones: la lista de grupos, sus datos y elegir uno.

- ``DialogoGrupos``: la lista de grupos con cuántas sesiones tiene cada uno.
  Intro ABRE el grupo (``DialogoGrupo``, en ``grupo.py``) para ver y trabajar
  sus sesiones. F2 renombra, Supr borra (con confirmación; las sesiones no se
  borran), Control+N crea, Alt+Intro edita sus datos, Alt+flecha arriba y
  abajo cambian el orden y Control+E exporta el grupo entero.
- ``DialogoEditarGrupo``: nombre, descripción (por ejemplo, de qué trata una
  investigación) y el agente y la carpeta con que se crean sus sesiones nuevas.
- ``elegir_grupo``: cuadro con los grupos y, al final, «Nuevo grupo…» (que
  pide el nombre). Devuelve el id del grupo elegido o ``None``.

Cuando un grupo pide abrir una sesión o crear una nueva, el diálogo se cierra
y deja la petición en ``accion`` para que la ventana principal la haga.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import wx

from app.services.almacen import Almacen, Grupo
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import (
	confirmar,
	crear_botonera,
	enfocar,
	mostrar_error,
	pedir_texto,
	preparar_dialogo,
)
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import menu_de_acciones, vincular_menu_contextual
from app.ui.comun import NOMBRES_AGENTE, _, nombre_agente

log = logging.getLogger(__name__)

#: Longitud máxima del nombre de un grupo.
MAX_NOMBRE = 80
#: Longitud máxima de la descripción de un grupo.
MAX_DESCRIPCION = 4000

#: Agentes que se pueden elegir como predeterminados de un grupo ("" = el de siempre).
AGENTES_GRUPO = ("", "claude", "codex", "antigravity", "gemini")


def _nombre_repetido(almacen: Almacen, nombre: str, excepto: int | None = None) -> int | None:
	"""Id del grupo que ya se llama ``nombre`` (sin distinguir mayúsculas), salvo ``excepto``."""
	buscado = nombre.strip().casefold()
	for identificador, existente in almacen.grupos():
		if identificador != excepto and existente.strip().casefold() == buscado:
			return identificador
	return None


def pedir_nombre_grupo(padre: wx.Window | None, almacen: Almacen, titulo: str, valor: str = "",
		excepto: int | None = None) -> str | None:
	"""Pide el nombre de un grupo hasta que sea válido. ``None`` si se cancela."""
	while True:
		nombre = pedir_texto(
			padre, _("&Nombre del grupo:"), titulo, valor, max_caracteres=MAX_NOMBRE,
			ayuda=_("Nombre del grupo, por ejemplo el proyecto o el tema de sus sesiones."),
		)
		if nombre is None:
			return None
		nombre = " ".join(nombre.split())
		if not nombre:
			mostrar_error(padre, _("Escribe un nombre para el grupo."))
			continue
		if _nombre_repetido(almacen, nombre, excepto) is not None:
			mostrar_error(padre, _("Ya hay un grupo llamado «{nombre}». Elige otro nombre.").format(nombre=nombre))
			valor = nombre
			continue
		return nombre


class DialogoEditarGrupo(wx.Dialog):
	"""Datos de un grupo. Tras ``wx.ID_OK``, ``valores`` tiene nombre, descripción, agente y carpeta."""

	def __init__(self, padre: wx.Window | None, almacen: Almacen, grupo: Grupo | None = None) -> None:
		titulo = _("Editar el grupo «{n}»").format(n=grupo.nombre) if grupo else _("Nuevo grupo")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.almacen = almacen
		self.grupo = grupo
		self.valores: dict[str, str] = {}

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_nombre = crear_campo(panel, _("&Nombre:"), wx.TextCtrl, sizer=raiz,
			value=grupo.nombre if grupo else "",
			ayuda=_("Nombre del grupo, por ejemplo el proyecto o el tema de la investigación."))
		self.campo_nombre.SetMaxLength(MAX_NOMBRE)
		_e, self.campo_descripcion = crear_campo(panel, _("&Descripción o notas:"), wx.TextCtrl, sizer=raiz,
			value=grupo.descripcion if grupo else "", style=wx.TE_MULTILINE,
			ayuda=_("Lo que quieras apuntar del grupo: de qué trata, qué falta por consultar, conclusiones... "
				"Se muestra al abrir el grupo."))
		self.campo_descripcion.SetMaxLength(MAX_DESCRIPCION)
		self.campo_descripcion.SetMinSize(self.FromDIP(wx.Size(480, 120)))
		nombres = [_("El de siempre (Preferencias)")] + [NOMBRES_AGENTE.get(a, a) for a in AGENTES_GRUPO[1:]]
		_e, self.campo_agente = crear_campo(panel, _("A&gente para las sesiones nuevas:"), wx.Choice, sizer=raiz,
			choices=nombres,
			ayuda=_("Agente que aparece elegido al crear una sesión nueva desde este grupo."))
		agente = grupo.agente if grupo else ""
		self.campo_agente.SetSelection(AGENTES_GRUPO.index(agente) if agente in AGENTES_GRUPO else 0)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		_e, self.campo_carpeta = crear_campo(panel, _("Carpeta de &trabajo para las sesiones nuevas:"), wx.TextCtrl,
			sizer=fila, horizontal=True, value=grupo.carpeta if grupo else "",
			ayuda=_("Carpeta que se propone al crear una sesión nueva desde este grupo. Vacía: la más reciente."))
		self.boton_examinar = wx.Button(panel, label=_("E&xaminar..."))
		self.boton_examinar.SetHelpText(_("Elige la carpeta en una ventana de selección."))
		self.boton_examinar.Bind(wx.EVT_BUTTON, lambda _evento: self._examinar())
		fila.Add(self.boton_examinar, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 6)
		raiz.Add(fila, 0, wx.EXPAND)
		raiz.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		for identificador, ayuda in (
			(wx.ID_OK, _("Guarda los datos del grupo y cierra.")),
			(wx.ID_CANCEL, _("Cierra sin cambiar nada.")),
		):
			boton = panel.FindWindowById(identificador, panel)
			if boton is not None:
				boton.SetHelpText(ayuda)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.campo_nombre)

	def _examinar(self) -> None:
		with wx.DirDialog(self, _("Carpeta de trabajo del grupo"), defaultPath=self.campo_carpeta.GetValue()) as dialogo:
			if dialogo.ShowModal() == wx.ID_OK:
				self.campo_carpeta.SetValue(dialogo.GetPath())
		enfocar(self.campo_carpeta)

	def validar(self) -> str | None:
		"""Mensaje de error o ``None`` si todo vale."""
		nombre = " ".join(self.campo_nombre.GetValue().split())
		if not nombre:
			return _("Escribe un nombre para el grupo.")
		if _nombre_repetido(self.almacen, nombre, self.grupo.id if self.grupo else None) is not None:
			return _("Ya hay un grupo llamado «{nombre}». Elige otro nombre.").format(nombre=nombre)
		carpeta = self.campo_carpeta.GetValue().strip().strip('"')
		if carpeta and not Path(carpeta).is_dir():
			return _("La carpeta de trabajo no existe. Escribe una carpeta válida, déjala vacía o pulsa Examinar.")
		return None

	def aceptar(self) -> bool:
		error = self.validar()
		if error is not None:
			mostrar_error(self, error, _("Datos del grupo"))
			return False
		indice = self.campo_agente.GetSelection()
		self.valores = {
			"nombre": " ".join(self.campo_nombre.GetValue().split()),
			"descripcion": self.campo_descripcion.GetValue().strip(),
			"agente": AGENTES_GRUPO[indice] if 0 <= indice < len(AGENTES_GRUPO) else "",
			"carpeta": self.campo_carpeta.GetValue().strip().strip('"'),
		}
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True


def editar_grupo(padre: wx.Window | None, almacen: Almacen, grupo: Grupo | None) -> int | None:
	"""Edita (o crea, si ``grupo`` es ``None``) un grupo. Devuelve su id o ``None`` si se cancela."""
	dialogo = DialogoEditarGrupo(padre, almacen, grupo)
	try:
		if dialogo.ShowModal() != wx.ID_OK:
			return None
		valores = dict(dialogo.valores)
	finally:
		dialogo.Destroy()
	if grupo is None:
		return almacen.crear_grupo(**valores)
	almacen.actualizar_grupo(grupo.id, **valores)
	return grupo.id


class DialogoGrupos(wx.Dialog):
	"""La lista de grupos: abrirlos, crearlos, editarlos, ordenarlos y borrarlos.

	:param contexto: lo que necesita un grupo abierto de la ventana principal
		(sesiones, opciones, exportar...). Sin él, Intro renombra (modo sencillo).
	"""

	def __init__(self, padre: wx.Window | None, almacen: Almacen, contexto: Any | None = None) -> None:
		titulo = _("Grupos de sesiones")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.almacen = almacen
		self.contexto = contexto
		self.grupos: list[tuple[int, str]] = []
		self._detalle: list[Grupo] = []
		self.cambiado = False
		#: Nombre anterior → nombre nuevo de los grupos renombrados aquí.
		self.renombrados: dict[str, str] = {}
		#: Petición para la ventana principal al cerrar: ("abrir", clave), ("nueva", id) o ("mostrar", id).
		self.accion: tuple[str, Any] | None = None

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.etiqueta = wx.StaticText(panel, label=_("&Grupos:"))
		self.lista = ListaAccesible(panel, _("Grupos"), [self._columnas_def()[c] for c in self._columnas()],
			leer_preferencias=getattr(contexto, "preferencias_lectura", None))
		self.lista.SetHelpText(_(
			"Grupos de sesiones. Intro abre el grupo para ver y trabajar sus sesiones; F2 cambia el nombre; "
			"Alt+Intro edita la descripción, el agente y la carpeta; Supr borra el grupo (sus sesiones no se borran); "
			"Control+N crea uno; Alt+flecha arriba y abajo cambian el orden; Control+E exporta el grupo entero. "
			"Tecla Aplicaciones: todas las acciones."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(560, 260)))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_abrir = self._boton(panel, botones, _("&Abrir"), _("Abre el grupo seleccionado para ver sus sesiones (Intro)."), self.abrir)
		self.boton_nuevo = self._boton(panel, botones, _("&Nuevo..."), _("Crea un grupo nuevo; pide su nombre (Control+N)."), self.nuevo)
		self.boton_renombrar = self._boton(panel, botones, _("&Renombrar..."), _("Cambia el nombre del grupo seleccionado (F2)."), self.renombrar)
		self.boton_editar = self._boton(panel, botones, _("&Editar..."),
			_("Cambia el nombre, la descripción y el agente y la carpeta de las sesiones nuevas del grupo (Alt+Intro)."), self.editar)
		self.boton_borrar = self._boton(panel, botones, _("&Borrar"), _("Borra el grupo seleccionado tras confirmarlo (Supr). Sus sesiones no se borran."), self.borrar)
		sizer.Add(botones, 0, wx.LEFT | wx.RIGHT, 4)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra la lista de grupos."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(sizer)

		vincular_menu_contextual(self.lista, self._crear_menu)
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.abrir())
		self.recargar()
		preparar_dialogo(self, foco_inicial=self.lista if self.grupos else self.boton_nuevo)
		# Después de preparar_dialogo: este manejador se atiende antes que el suyo.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)

		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		if not self.grupos:
			wx.CallAfter(voz.hablar, _("No hay grupos. Pulsa Alt+N para crear uno."))

	# ── Columnas ──────────────────────────────────────────────────────────────

	@staticmethod
	def _columnas_def() -> dict[str, tuple[str, int]]:
		from app.ui.lectura_listas import COLUMNAS_GRUPOS

		return COLUMNAS_GRUPOS

	def _columnas(self) -> list[str]:
		from app.ui.lectura_listas import COLUMNAS_GRUPOS, GRUPOS_PREDETERMINADAS, columnas_validas

		opcion = getattr(self.contexto, "opcion", None)
		elegidas = opcion("grupos.columnas", None) if callable(opcion) else None
		columnas = columnas_validas(elegidas, COLUMNAS_GRUPOS, GRUPOS_PREDETERMINADAS)
		# El nombre siempre va: sin él no se sabe qué grupo es.
		return columnas if "nombre" in columnas else ["nombre", *columnas]

	@staticmethod
	def _boton(panel: wx.Window, sizer: wx.Sizer, etiqueta: str, ayuda: str, accion: Callable[[], object]) -> wx.Button:
		boton = wx.Button(panel, label=etiqueta)
		boton.SetHelpText(ayuda)
		boton.Bind(wx.EVT_BUTTON, lambda _evento: accion())
		sizer.Add(boton, 0, wx.ALL, 4)
		return boton

	def _crear_menu(self) -> wx.Menu:
		menu = wx.Menu()

		def anadir(etiqueta: str, accion: Callable[[], object]) -> None:
			menu.Bind(wx.EVT_MENU, lambda _e: accion(), menu.Append(wx.ID_ANY, etiqueta))

		if self.actual() is not None:
			anadir(_("&Abrir\tIntro"), self.abrir)
			if self.contexto is not None:
				anadir(_("&Mostrar en la lista principal"), self.mostrar_en_lista)
				anadir(_("E&xportar el grupo entero...\tCtrl+E"), self.exportar)
			menu.AppendSeparator()
			anadir(_("&Renombrar...\tF2"), self.renombrar)
			anadir(_("&Editar los datos del grupo...\tAlt+Intro"), self.editar)
			anadir(_("&Subir\tAlt+Arriba"), lambda: self.mover(-1))
			anadir(_("Ba&jar\tAlt+Abajo"), lambda: self.mover(1))
			anadir(_("&Borrar\tSupr"), self.borrar)
			menu.AppendSeparator()
		anadir(_("&Nuevo grupo...\tCtrl+N"), self.nuevo)
		return menu

	# ── Datos ─────────────────────────────────────────────────────────────────

	def recargar(self, seleccionar_id: int | None = None) -> None:
		"""Vuelve a leer los grupos; selecciona ``seleccionar_id`` si se indica."""
		from app.ui.lectura_listas import valores_grupo

		fila_previa = self.lista.fila_actual()
		self._detalle = self.almacen.grupos_detalle()
		self.grupos = [(g.id, g.nombre) for g in self._detalle]
		columnas = self._columnas()
		self.lista.reemplazar_filas([valores_grupo(g, columnas, nombre_agente) for g in self._detalle], conservar_foco=False)
		self.etiqueta.SetLabel(_("&Grupos ({n}):").format(n=len(self.grupos)))
		self.lista.SetName(_("Grupos ({n})").format(n=len(self.grupos)))
		destino = None
		if seleccionar_id is not None:
			destino = next((i for i, (identificador, _n) in enumerate(self.grupos) if identificador == seleccionar_id), None)
		if destino is None and fila_previa != wx.NOT_FOUND and self.grupos:
			destino = min(fila_previa, len(self.grupos) - 1)
		if destino is not None:
			self.lista.enfocar_fila(destino, anunciar=False)
		for boton in (self.boton_abrir, self.boton_renombrar, self.boton_editar, self.boton_borrar):
			boton.Enable(bool(self.grupos))

	def actual(self) -> tuple[int, str] | None:
		"""``(id, nombre)`` del grupo seleccionado o ``None``."""
		fila = self.lista.fila_actual()
		return self.grupos[fila] if 0 <= fila < len(self.grupos) else None

	def grupo_actual(self) -> Grupo | None:
		fila = self.lista.fila_actual()
		return self._detalle[fila] if 0 <= fila < len(self._detalle) else None

	def _enfocar_lista(self) -> None:
		if self.lista.IsShownOnScreen():
			self.lista.SetFocus()

	def _sin_seleccion(self) -> None:
		voz.hablar(_("No hay ningún grupo seleccionado") if self.grupos else _("No hay grupos"))

	def _cerrar_con(self, accion: tuple[str, Any]) -> None:
		self.accion = accion
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()

	# ── Acciones ──────────────────────────────────────────────────────────────

	def abrir(self) -> bool:
		"""Abre el grupo seleccionado (Intro). Sin contexto de ventana principal, renombra."""
		grupo = self.grupo_actual()
		if grupo is None:
			self._sin_seleccion()
			return False
		if self.contexto is None:
			return self.renombrar()
		from app.ui.dialogos.grupo import abrir_grupo

		accion = abrir_grupo(self, self.almacen, grupo.id, self.contexto)
		self.cambiado = True
		if accion is not None:
			self._cerrar_con(accion)
			return True
		self.recargar(grupo.id)
		self._enfocar_lista()
		return True

	def mostrar_en_lista(self) -> bool:
		"""Cierra y deja la lista principal mostrando solo este grupo."""
		grupo = self.actual()
		if grupo is None:
			self._sin_seleccion()
			return False
		self._cerrar_con(("mostrar", grupo[0]))
		return True

	def exportar(self) -> bool:
		grupo = self.actual()
		if grupo is None:
			self._sin_seleccion()
			return False
		exportar = getattr(self.contexto, "exportar_grupo", None)
		if callable(exportar):
			exportar(grupo[0], self)
		return True

	def nuevo(self) -> int | None:
		"""Crea un grupo. Devuelve su id o ``None`` si se cancela."""
		nombre = pedir_nombre_grupo(self, self.almacen, _("Nuevo grupo"))
		if nombre is None:
			return None
		identificador = self.almacen.crear_grupo(nombre)
		self.cambiado = True
		self.recargar(identificador)
		self._enfocar_lista()
		voz.hablar(_("Grupo «{nombre}» creado").format(nombre=nombre))
		return identificador

	def renombrar(self) -> bool:
		"""Cambia el nombre del grupo seleccionado (F2)."""
		grupo = self.actual()
		if grupo is None:
			self._sin_seleccion()
			return False
		identificador, anterior = grupo
		nombre = pedir_nombre_grupo(self, self.almacen, _("Renombrar grupo"), anterior, excepto=identificador)
		if nombre is None or nombre == anterior:
			return False
		self.almacen.renombrar_grupo(identificador, nombre)
		self._anotar_renombre(anterior, nombre)
		self.cambiado = True
		self.recargar(identificador)
		self._enfocar_lista()
		voz.hablar(_("Grupo renombrado"))
		return True

	def _anotar_renombre(self, anterior: str, nuevo: str) -> None:
		original = next((viejo for viejo, actual in self.renombrados.items() if actual == anterior), anterior)
		self.renombrados[original] = nuevo

	def editar(self) -> bool:
		"""Edita nombre, descripción, agente y carpeta del grupo (Alt+Intro)."""
		grupo = self.grupo_actual()
		if grupo is None:
			self._sin_seleccion()
			return False
		if editar_grupo(self, self.almacen, grupo) is None:
			return False
		nuevo = self.almacen.grupo(grupo.id)
		if nuevo is not None and nuevo.nombre != grupo.nombre:
			self._anotar_renombre(grupo.nombre, nuevo.nombre)
		self.cambiado = True
		self.recargar(grupo.id)
		self._enfocar_lista()
		voz.hablar(_("Grupo guardado"))
		return True

	def mover(self, paso: int) -> bool:
		"""Sube o baja el grupo seleccionado (Alt+flecha arriba / abajo)."""
		grupo = self.actual()
		if grupo is None:
			self._sin_seleccion()
			return False
		fila = self.lista.fila_actual()
		destino = fila + paso
		if not 0 <= destino < len(self.grupos):
			voz.hablar(_("Ya es el primero") if destino < 0 else _("Ya es el último"))
			return False
		self.almacen.mover_grupo(grupo[0], paso)
		self.cambiado = True
		self.recargar(grupo[0])
		voz.hablar(_("Movido a la posición {n} de {t}").format(n=destino + 1, t=len(self.grupos)))
		return True

	def borrar(self) -> bool:
		"""Borra el grupo seleccionado tras confirmar (Supr)."""
		grupo = self.actual()
		if grupo is None:
			self._sin_seleccion()
			return False
		identificador, nombre = grupo
		respuesta = confirmar(
			self,
			_("¿Borrar el grupo «{nombre}»? Sus sesiones no se borran: solo dejan de estar en este grupo.").format(nombre=nombre),
			_("Borrar grupo"), peligroso=True,
		)
		if respuesta != wx.ID_YES:
			return False
		self.almacen.borrar_grupo(identificador)
		self.cambiado = True
		self.recargar()
		self._enfocar_lista()
		voz.hablar(_("Grupo borrado"))
		return True

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		en_lista = wx.Window.FindFocus() is self.lista
		if modificadores == wx.MOD_NONE and tecla == wx.WXK_F2:
			self.renombrar()
			return
		if modificadores == wx.MOD_NONE and tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.borrar()
			return
		if modificadores == wx.MOD_CONTROL and tecla == ord("N"):
			self.nuevo()
			return
		if modificadores == wx.MOD_CONTROL and tecla == ord("E") and self.contexto is not None:
			self.exportar()
			return
		if modificadores == wx.MOD_ALT and tecla in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.editar()
			return
		if en_lista and modificadores == wx.MOD_ALT and tecla in (wx.WXK_UP, wx.WXK_NUMPAD_UP):
			self.mover(-1)
			return
		if en_lista and modificadores == wx.MOD_ALT and tecla in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
			self.mover(1)
			return
		evento.Skip()


class DialogoElegirGrupo(wx.Dialog):
	"""Lista de grupos más «Nuevo grupo…». Tras ``wx.ID_OK``, ``id_elegido`` tiene el grupo."""

	def __init__(self, padre: wx.Window | None, almacen: Almacen, titulo: str, excluir: tuple[int, ...] = ()) -> None:
		titulo = titulo or _("Elegir grupo")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.almacen = almacen
		self.grupos = [g for g in almacen.grupos() if g[0] not in excluir]
		self.id_elegido: int | None = None

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(panel, label=_("&Grupo:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = ListaAccesible(panel, _("Grupo"), [(_("Nombre"), 360)])
		self.lista.SetHelpText(_(
			"Elige un grupo y pulsa Intro. La última opción, «Nuevo grupo», crea uno pidiendo su nombre."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(400, 220)))
		filas = [[nombre] for _id, nombre in self.grupos] + [[_("Nuevo grupo...")]]
		self.lista.reemplazar_filas(filas, conservar_foco=False)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
		vincular_menu_contextual(self.lista, self._crear_menu)
		sizer.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		for identificador, ayuda in (
			(wx.ID_OK, _("Usa el grupo seleccionado (o crea uno nuevo si eliges «Nuevo grupo»).")),
			(wx.ID_CANCEL, _("Cierra sin elegir ningún grupo.")),
		):
			boton = panel.FindWindowById(identificador, panel)
			if boton is not None:
				boton.SetHelpText(ayuda)
		panel.SetSizer(sizer)

		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.aceptar())
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		preparar_dialogo(self, foco_inicial=self.lista)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()

	def es_nuevo(self, fila: int) -> bool:
		"""La fila es la opción «Nuevo grupo…»."""
		return fila == len(self.grupos)

	def _crear_menu(self) -> wx.Menu:
		return menu_de_acciones(self.lista, [
			(_("&Elegir este grupo\tIntro"), self.aceptar),
			(_("&Nuevo grupo..."), self.nuevo),
		])

	def nuevo(self) -> int | None:
		"""Crea un grupo pidiendo su nombre y lo elige (como la opción «Nuevo grupo…» de la lista)."""
		self.lista.enfocar_fila(len(self.grupos), anunciar=False)
		return self.aceptar()

	def aceptar(self) -> int | None:
		"""Resuelve la elección; si hay grupo, cierra con ``wx.ID_OK``."""
		fila = self.lista.fila_actual()
		if not 0 <= fila <= len(self.grupos):
			voz.hablar(_("Elige un grupo de la lista"))
			return None
		if self.es_nuevo(fila):
			nombre = pedir_nombre_grupo(self, self.almacen, _("Nuevo grupo"))
			if nombre is None:
				return None
			self.id_elegido = self.almacen.crear_grupo(nombre)
		else:
			self.id_elegido = self.grupos[fila][0]
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return self.id_elegido


def elegir_grupo(padre: wx.Window | None, almacen: Almacen, titulo: str, excluir: tuple[int, ...] = ()) -> int | None:
	"""Muestra los grupos (más «Nuevo grupo…») y devuelve el id elegido o ``None``.

	:param excluir: grupos que no se ofrecen (por ejemplo, el de origen al mover).
	"""
	dialogo = DialogoElegirGrupo(padre, almacen, titulo, excluir)
	foco_previo = wx.Window.FindFocus()
	try:
		return dialogo.id_elegido if dialogo.ShowModal() == wx.ID_OK else None
	finally:
		dialogo.Destroy()
		if foco_previo:
			try:
				foco_previo.SetFocus()
			except RuntimeError:
				log.debug("El control que tenía el foco ya no existe", exc_info=True)
