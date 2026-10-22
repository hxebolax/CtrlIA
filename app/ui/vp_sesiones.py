"""Lista de sesiones de la ventana principal: vistas, orden, filtro y organización.

Mientras la lista tiene el foco, las filas NO se reordenan solas: se actualizan
en su sitio y las sesiones nuevas se añaden al final. Así el lector no salta a
otra sesión mientras la recorres. F5 vuelve a ordenar.
"""

from __future__ import annotations

import logging
import os
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import wx

from app.services.gestor import TEXTO_SIN_CONVERSACION, FilaSesion, coincide
from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, elegir_opcion, mostrar_error, mostrar_mensaje, pedir_texto
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _, fecha_relativa, nombre_agente, nombre_estado

if TYPE_CHECKING:
	from app.services.gestor import GestorSesiones

log = logging.getLogger(__name__)

#: Columnas posibles de la lista de sesiones: id → (cabecera, ancho).
COLUMNAS: dict[str, tuple[str, int]] = {
	"titulo": (_("Título"), 300),
	"estado": (_("Estado"), 120),
	"agente": (_("Agente"), 100),
	"carpeta": (_("Carpeta"), 220),
	"actividad": (_("Última actividad"), 160),
	"origen": (_("Origen"), 110),
	"sin_leer": (_("Sin leer"), 80),
	"grupos": (_("Grupos"), 130),
	"ultimo": (_("Último mensaje"), 320),
	"creada": (_("Creada"), 160),
	"modelo": (_("Modelo"), 120),
	"equipo": (_("Equipo"), 170),
}

#: Vistas fijas del menú Ver, Mostrar: (id, etiqueta).
VISTAS: list[tuple[str, str]] = [
	("todas", _("&Todas")),
	("actividad", _("Te necesitan o &trabajando")),
	("te_necesitan", _("Te &necesitan")),
	("trabajando", _("T&rabajando")),
	("sin_leer", _("Con respuestas &sin leer")),
	("propias", _("Creadas en &Ctrl+IA")),
	("favoritas", _("&Favoritas")),
	("sin_grupo", _("Sin &grupo")),
	("agente:claude", _("Solo &Claude")),
	("agente:codex", _("Solo C&odex")),
	("agente:antigravity", _("Solo &Antigravity")),
	("agente:gemini", _("Solo G&emini")),
	("origen:terminal", _("Abiertas en la t&erminal")),
	("origen:escritorio", _("De la app de Claude (escritorio y Cowork)")),
	("importadas", _("&Importadas de otro equipo")),
	("equipo:este", _("Solo de este equipo (&Windows)")),
	("equipo:otros", _("De otros e&quipos por SSH")),
	("terminadas", _("Ter&minadas")),
	("ocultas", _("&Ocultas")),
	("archivadas", _("Arc&hivadas")),
]

ORDENES: list[tuple[str, str]] = [
	("recientes", _("Más &recientes primero")),
	("antiguas", _("Más &antiguas primero")),
	("estado", _("Por &estado (las que te necesitan primero)")),
	("titulo", _("Por &título")),
	("carpeta", _("Por &carpeta")),
	("agente", _("Por a&gente")),
]

_PRIORIDAD_ESTADO = {"esperando": 0, "trabajando": 1, "segundo_plano": 2, "error": 3}


def _clave_orden(orden: str):
	if orden == "antiguas":
		return lambda f: f.actividad, False
	if orden == "estado":
		return lambda f: (10 if f.terminada else _PRIORIDAD_ESTADO.get(str(f.estado), 9), -f.actividad), False
	if orden == "titulo":
		return lambda f: f.titulo.casefold(), False
	if orden == "carpeta":
		return lambda f: (f.carpeta.casefold(), -f.actividad), False
	if orden == "agente":
		return lambda f: (f.proveedor, -f.actividad), False
	return lambda f: f.actividad, True


def nombre_equipo(fila: FilaSesion) -> str:
	"""A qué equipo pertenece una sesión: «Este equipo (Windows)», «Mac del estudio (macOS)»..."""
	if not fila.equipo:
		return _("Este equipo (Windows)")
	if fila.importada:
		return _("Importada de {e}").format(e=fila.equipo)
	return f"{fila.equipo} ({fila.sistema})" if fila.sistema else fila.equipo


def valores_sesion(fila: FilaSesion, columnas: list[str], opcion) -> list[str]:
	"""Texto de cada columna de una sesión, según las preferencias de lectura.

	Lo usan la lista principal y la lista de sesiones de un grupo abierto.
	"""
	from app.ui.lectura_listas import escribir_fecha

	valores = []
	for columna in columnas:
		if columna == "titulo":
			texto = fila.titulo
			if fila.favorita and opcion("lectura.favorita_en_titulo", True):
				texto = _("Favorita: {t}").format(t=texto)
			valores.append(texto)
		elif columna == "estado":
			texto = _("Terminada") if fila.terminada else nombre_estado(fila.estado)
			if fila.sin_conversacion:
				texto += _(", conversación no disponible")
			if fila.no_leidas and opcion("lectura.sin_leer_en_estado", True):
				texto += _(", respuesta nueva")
			valores.append(texto)
		elif columna == "agente":
			valores.append(nombre_agente(fila.proveedor))
		elif columna == "carpeta":
			valores.append(fila.carpeta or _("Sin carpeta"))
		elif columna == "actividad":
			valores.append(escribir_fecha(fila.actividad, opcion))
		elif columna == "origen":
			valores.append(fila.nombre_origen)
		elif columna == "sin_leer":
			valores.append(str(fila.no_leidas) if fila.no_leidas else _("No"))
		elif columna == "grupos":
			valores.append(", ".join(fila.grupos) or _("Ninguno"))
		elif columna == "ultimo":
			if fila.sin_conversacion:
				valores.append(_("Conversación no disponible"))
			else:
				valores.append(fila.ultimo_mensaje or _("Sin mensajes"))
		elif columna == "creada":
			valores.append(escribir_fecha(fila.creada, opcion))
		elif columna == "modelo":
			valores.append(fila.modelo or _("Predeterminado"))
		elif columna == "equipo":
			valores.append(nombre_equipo(fila))
	return valores


class MixinSesiones:
	"""Comportamiento de la lista de sesiones (se mezcla con ``VentanaPrincipal``)."""

	gestor: GestorSesiones
	lista_sesiones: ListaAccesible

	# ------------------------------------------------------------------
	# Construcción
	# ------------------------------------------------------------------

	def columnas_activas(self) -> list[str]:
		columnas = [c for c in (self.opcion("lista.columnas", []) or []) if c in COLUMNAS]
		return columnas or ["titulo", "estado", "agente", "carpeta", "actividad"]

	def crear_lista_sesiones(self, padre: wx.Window) -> ListaAccesible:
		lista = ListaAccesible(padre, _("Sesiones"), [COLUMNAS[c] for c in self.columnas_activas()],
			leer_preferencias=self.preferencias_lectura)
		lista.SetHelpText(_(
			"Todas tus sesiones con agentes de IA. Intro abre la sesión; F2 la renombra; Suprimir la oculta; "
			"Control+N crea una nueva; la tecla Aplicaciones abre el menú con todas las acciones. "
			"Flecha derecha e izquierda recorren las columnas."
		))
		lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.abrir_sesion_seleccionada())
		lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla_sesiones)
		vincular_menu_contextual(lista, self._menu_contextual_sesiones)
		return lista

	def iniciar_sesiones(self) -> None:
		self.sesion_actual: str = ""
		self._todas: list[FilaSesion] = []
		self._visibles: list[FilaSesion] = []
		self._filtro = ""
		self._primera_carga = True
		self._errores_equipos: dict[str, str] = {}
		self.gestor.al_cambiar_equipos = lambda: wx.CallAfter(self._al_cambiar_equipos)
		self.refrescar_sesiones(True)

	# ------------------------------------------------------------------
	# Carga y presentación
	# ------------------------------------------------------------------

	def refrescar_sesiones(self, reordenar: bool = False) -> None:
		"""Vuelve a leer las sesiones en segundo plano y actualiza la lista."""
		self.en_segundo_plano(self.gestor.listar, al_terminar=lambda filas: self._al_listar(filas, reordenar),
			al_fallar=lambda error: log.warning("No se pudo leer la lista de sesiones: %s", error), clave="listar")

	def accion_actualizar(self) -> None:
		avisos.anunciar(_("Actualizando..."))
		self.gestor.leer_equipos_ya()
		self.refrescar_sesiones(True)

	def _al_cambiar_equipos(self) -> None:
		"""Han llegado sesiones de un equipo por SSH, o ha dejado de responder: se repinta y se avisa una vez."""
		try:
			if self.IsBeingDeleted():
				return
		except RuntimeError:  # la ventana ya no existe
			return
		errores = dict(self.gestor.estado_equipos())
		for equipo, error in errores.items():
			if self._errores_equipos.get(equipo) != error:
				avisos.anunciar(_("No se pueden leer las sesiones de {e}: {x}").format(e=equipo, x=error))
		for equipo in self._errores_equipos.keys() - errores.keys():
			avisos.anunciar(_("Ya se leen otra vez las sesiones de {e}.").format(e=equipo))
		self._errores_equipos = errores
		self.refrescar_sesiones()

	def _al_listar(self, filas: list[FilaSesion], reordenar: bool) -> None:
		self._todas = filas
		self.mostrar_sesiones(reordenar or wx.Window.FindFocus() is not self.lista_sesiones)
		if self._primera_carga:
			self._primera_carga = False
			self._abrir_ultima_sesion()

	def _abrir_ultima_sesion(self) -> None:
		ultima = self.opcion("general.ultima_sesion", "")
		if not ultima or not self.opcion("general.abrir_ultima_sesion", True):
			return
		indice = next((i for i, f in enumerate(self._visibles) if f.clave == ultima), -1)
		if indice >= 0:
			self.lista_sesiones.enfocar_fila(indice, anunciar=False)
			self.abrir_sesion(ultima, mover_foco=False)

	def _pasa_vista(self, fila: FilaSesion, vista: str) -> bool:
		if vista == "ocultas":
			return fila.oculta
		if vista == "archivadas":
			return fila.archivada
		if fila.oculta and not self.opcion("lista.mostrar_ocultas", False):
			return False
		if fila.archivada and not self.opcion("lista.mostrar_archivadas", False):
			return False
		estado = str(fila.estado)
		if vista == "terminadas":
			return fila.terminada
		if vista == "importadas":
			return fila.importada
		if vista == "equipo:este":
			return not fila.equipo
		if vista == "equipo:otros":
			return fila.remota
		# Una sesión terminada ya no te necesita ni tiene nada nuevo que leer.
		if vista in ("actividad", "te_necesitan", "trabajando", "sin_leer") and fila.terminada:
			return False
		if vista == "actividad":
			return estado in ("esperando", "trabajando", "segundo_plano")
		if vista == "te_necesitan":
			return estado == "esperando"
		if vista == "trabajando":
			return estado in ("trabajando", "segundo_plano")
		if vista == "sin_leer":
			return fila.no_leidas > 0
		if vista == "propias":
			return fila.propia
		if vista == "favoritas":
			return fila.favorita
		if vista == "sin_grupo":
			return not fila.grupos
		if vista.startswith("agente:"):
			return fila.proveedor == vista.split(":", 1)[1]
		if vista.startswith("origen:"):
			origen = vista.split(":", 1)[1]
			return fila.origen in (("escritorio", "cowork") if origen == "escritorio" else (origen,))
		if vista.startswith("grupo:"):
			return vista.split(":", 1)[1] in fila.grupos
		return True

	def _filtrar_y_ordenar(self) -> list[FilaSesion]:
		vista = self.opcion("lista.vista", "todas")
		filas = [f for f in self._todas if self._pasa_vista(f, vista)]
		if self._filtro:
			filas = [f for f in filas if coincide(" ".join((f.titulo, f.carpeta, f.ultimo_mensaje, nombre_agente(f.proveedor),
				" ".join(f.grupos), f.equipo)), self._filtro)]
		funcion, inverso = _clave_orden(self.opcion("lista.orden", "recientes"))
		filas.sort(key=funcion, reverse=inverso)
		return filas

	def valores_fila(self, fila: FilaSesion) -> list[str]:
		return valores_sesion(fila, self.columnas_activas(), self.opcion)

	def mostrar_sesiones(self, reordenar: bool = True) -> None:
		"""Pinta la lista. Sin ``reordenar``, conserva el orden actual (foco en la lista)."""
		nuevas = self._filtrar_y_ordenar()
		if not reordenar and self._visibles:
			por_clave = {f.clave: f for f in nuevas}
			conservadas = [por_clave[f.clave] for f in self._visibles if f.clave in por_clave]
			vistas = {f.clave for f in conservadas}
			nuevas = conservadas + [f for f in nuevas if f.clave not in vistas]
		lista = self.lista_sesiones
		actual = self.fila_seleccionada()
		mismas = [f.clave for f in nuevas] == [f.clave for f in self._visibles]
		self._visibles = nuevas
		if mismas:
			for indice, fila in enumerate(nuevas):
				valores = self.valores_fila(fila)
				if lista.valores_fila(indice) != valores:
					lista.actualizar_fila(indice, valores)
		else:
			lista.Freeze()
			try:
				lista.reemplazar_filas([self.valores_fila(f) for f in nuevas], conservar_foco=False)
			finally:
				lista.Thaw()
			if actual is not None:
				indice = next((i for i, f in enumerate(nuevas) if f.clave == actual.clave), 0)
				if nuevas:
					lista.enfocar_fila(min(indice, len(nuevas) - 1), anunciar=False)
			elif nuevas:
				lista.enfocar_fila(0, anunciar=False)
		self._actualizar_etiqueta_sesiones()

	def _actualizar_etiqueta_sesiones(self) -> None:
		vista = self.opcion("lista.vista", "todas")
		nombre = next((e for v, e in VISTAS if v == vista), vista.split(":", 1)[-1]).replace("&", "")
		texto = _("Sesiones ({n}, vista: {vista})").format(n=len(self._visibles), vista=nombre)
		if self._filtro:
			texto += _(", filtro: {f}").format(f=self._filtro)
		self.lista_sesiones.SetName(texto)
		self.etiqueta_sesiones.SetLabel("&" + texto)

	def fila_seleccionada(self) -> FilaSesion | None:
		indice = self.lista_sesiones.GetFocusedItem()
		if 0 <= indice < len(self._visibles):
			return self._visibles[indice]
		return None

	def fila_de(self, clave: str) -> FilaSesion | None:
		return next((f for f in self._todas if f.clave == clave), None)

	def sesion_objetivo(self) -> FilaSesion | None:
		"""La sesión sobre la que actúa un menú: la seleccionada en la lista o la abierta."""
		if wx.Window.FindFocus() is self.lista_sesiones:
			fila = self.fila_seleccionada()
			if fila is not None:
				return fila
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None:
			fila = self.fila_seleccionada()
		if fila is None:
			avisos.anunciar(_("No hay ninguna sesión elegida."), interrumpir=True)
		return fila

	# ------------------------------------------------------------------
	# Teclado y menú contextual
	# ------------------------------------------------------------------

	def _al_tecla_sesiones(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if tecla == wx.WXK_DELETE and mods == wx.MOD_NONE:
			self.accion_ocultar()
			return
		if tecla == wx.WXK_DELETE and mods == wx.MOD_SHIFT:
			self.accion_borrar()
			return
		if tecla == ord("C") and mods == wx.MOD_CONTROL:
			fila = self.fila_seleccionada()
			if fila is not None:
				self.copiar(fila.titulo)
			return
		evento.Skip()

	def _menu_contextual_sesiones(self) -> wx.Menu | None:
		fila = self.fila_seleccionada()
		if fila is None:
			return None
		menu = wx.Menu()

		def anadir(etiqueta: str, accion, activo: bool = True) -> None:
			item = menu.Append(wx.ID_ANY, etiqueta)
			item.Enable(activo)
			self.lista_sesiones.Bind(wx.EVT_MENU, lambda _e: self._ejecutar_accion_funcion(accion), item)

		anadir(_("&Abrir\tIntro"), self.abrir_sesion_seleccionada)
		if not fila.propia:
			# En una importada, «Continuar aquí» crea una sesión nueva de este equipo (#228).
			anadir(_("&Continuar aquí\tCtrl+Mayús+N"), self.accion_continuar_aqui)
		anadir(_("Abrir en la app del &agente\tCtrl+O"), self.accion_abrir_en_agente)
		anadir(_("Abrir la carpeta en Antigravity &IDE\tCtrl+Mayús+G"), self.accion_abrir_en_antigravity)
		anadir(_("Abrir la carpeta en el &Explorador\tCtrl+Mayús+E"), self.accion_abrir_explorador)
		menu.AppendSeparator()
		anadir(_("&Renombrar\tF2"), self.accion_renombrar)
		anadir(_("Quitar de favoritas") if fila.favorita else _("Marcar como &favorita"), self.accion_favorita)
		# Una sola etiqueta según el estado; el atajo se escribe una vez.
		terminar = _("Volver a ac&tivar") if fila.terminada else _("Dar por &terminada")
		anadir(terminar + "\tCtrl+D", self.accion_terminada)
		if not fila.importada:
			leer = _("Marcar como &leída") if fila.no_leidas else _("Marcar como no &leída")
			anadir(leer + "\tCtrl+Q", self.accion_marcar_leida)
		anadir(_("&Añadir a un grupo\tCtrl+G"), self.accion_anadir_a_grupo)
		if fila.grupos:
			anadir(_("&Quitar de un grupo"), self.accion_quitar_de_grupo)
			anadir(_("&Mover a otro grupo..."), self.accion_mover_a_grupo)
		if fila.oculta:
			anadir(_("&Volver a mostrar"), self.accion_mostrar_oculta)
		else:
			anadir(_("&Ocultar\tSuprimir"), self.accion_ocultar)
		if fila.propia or fila.importada:
			borrar = _("&Borrar para siempre") if fila.propia else _("&Borrar la copia importada")
			anadir(borrar + "\tMayús+Suprimir", self.accion_borrar)
		menu.AppendSeparator()
		anadir(_("E&xportar\tCtrl+E"), self.accion_exportar)
		anadir(_("&Copiar el título\tCtrl+C"), lambda: self.copiar(fila.titulo))
		if hasattr(self, "crear_menu_pr") and fila.carpeta and not fila.remota and not fila.importada:
			menu.AppendSubMenu(self.crear_menu_pr(fila, self.lista_sesiones), _("&PR y comprobaciones"))
		anadir(_("Propie&dades\tAlt+Intro"), self.accion_propiedades)
		return menu

	def _ejecutar_accion_funcion(self, funcion) -> None:
		try:
			funcion()
		except Exception as error:  # noqa: BLE001 - una acción que falla se explica, no tumba la ventana
			log.exception("Falló una acción del menú contextual")
			mostrar_error(self, _("No se pudo completar la acción: {error}").format(error=error))

	def crear_menu_vistas(self) -> wx.Menu:
		menu = wx.Menu()
		self._menu_vistas = menu
		self._rellenar_menu_vistas()
		menu.Bind(wx.EVT_MENU_OPEN, lambda e: (self._rellenar_menu_vistas(), e.Skip()))
		return menu

	def _rellenar_menu_vistas(self) -> None:
		menu = self._menu_vistas
		for item in list(menu.GetMenuItems()):
			menu.Delete(item)
		actual = self.opcion("lista.vista", "todas")
		for vista, etiqueta in VISTAS:
			item = menu.AppendRadioItem(wx.ID_ANY, etiqueta)
			item.Check(vista == actual)
			self.Bind(wx.EVT_MENU, lambda _e, v=vista: self.cambiar_vista(v), item)
		grupos = self.almacen.grupos()
		if grupos:
			menu.AppendSeparator()
			for _id, nombre in grupos:
				vista = f"grupo:{nombre}"
				item = menu.AppendRadioItem(wx.ID_ANY, _("Grupo: {g}").format(g=nombre))
				item.Check(vista == actual)
				self.Bind(wx.EVT_MENU, lambda _e, v=vista: self.cambiar_vista(v), item)

	def cambiar_vista(self, vista: str) -> None:
		self.poner_opcion("lista.vista", vista)
		self.mostrar_sesiones(True)
		if not self._visibles and self.opcion("lectura.anunciar_vista_vacia", True):
			avisos.anunciar(_("No hay sesiones en esta vista. Elige otra en Ver, Mostrar."), interrumpir=True)
		else:
			avisos.anunciar(_("{n} sesiones").format(n=len(self._visibles)), interrumpir=True)
		self.ir_a_sesiones()

	def crear_menu_orden(self) -> wx.Menu:
		menu = wx.Menu()
		actual = self.opcion("lista.orden", "recientes")
		for orden, etiqueta in ORDENES:
			item = menu.AppendRadioItem(wx.ID_ANY, etiqueta)
			item.Check(orden == actual)
			self.Bind(wx.EVT_MENU, lambda _e, o=orden: self._cambiar_orden(o), item)
		return menu

	def _cambiar_orden(self, orden: str) -> None:
		self.poner_opcion("lista.orden", orden)
		self.mostrar_sesiones(True)
		self.ir_a_sesiones()

	def accion_columnas(self) -> None:
		from app.ui.dialogos.columnas import DialogoColumnas

		dialogo = DialogoColumnas(self, [(c, n) for c, (n, _a) in COLUMNAS.items()], self.columnas_activas())
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			activas = list(dialogo.activas)
		finally:
			dialogo.Destroy()
		self.poner_opcion("lista.columnas", activas)
		self._recrear_lista_sesiones()

	def _recrear_lista_sesiones(self, mover_foco: bool = True) -> None:
		vieja = self.lista_sesiones
		tenia_foco = wx.Window.FindFocus() is vieja
		nueva = self.crear_lista_sesiones(self.panel)
		self._sizer_raiz.Replace(vieja, nueva)
		vieja.Destroy()
		self.lista_sesiones = nueva
		nueva.MoveAfterInTabOrder(self.etiqueta_sesiones)
		self.panel.Layout()
		self._visibles = []
		self.mostrar_sesiones(True)
		if mover_foco or tenia_foco:
			self.ir_a_sesiones()

	# ------------------------------------------------------------------
	# Filtro
	# ------------------------------------------------------------------

	def filtro_activo(self) -> bool:
		return bool(self._filtro)

	def quitar_filtro(self) -> None:
		self._filtro = ""
		self.mostrar_sesiones(True)
		avisos.anunciar(_("Filtro quitado. {n} sesiones.").format(n=len(self._visibles)), interrumpir=True)

	def filtrar_sesiones(self) -> None:
		texto = pedir_texto(self, _("&Mostrar solo las sesiones que contengan:"), _("Filtrar sesiones"), self._filtro)
		if texto is None:
			return
		self._filtro = texto.strip()
		self.mostrar_sesiones(True)
		self.ir_a_sesiones()
		if self._filtro:
			avisos.anunciar(_("{n} sesiones coinciden. Escape quita el filtro.").format(n=len(self._visibles)), interrumpir=True)

	# ------------------------------------------------------------------
	# Agentes al arrancar
	# ------------------------------------------------------------------

	def detectar_agentes_al_arrancar(self) -> None:
		def detectar_todos() -> dict[str, Any]:
			return {identificador: self.gestor.detectar(identificador) for identificador in self.gestor.proveedores}

		def hecho(estados: dict[str, Any]) -> None:
			disponibles = [i for i, e in estados.items() if e is not None and e.disponible]
			if not disponibles:
				avisos.notificar(_(
					"No se ha encontrado ningún agente de IA en este equipo. Abre Herramientas, Agentes instalados, "
					"para instalar Claude Code, Codex o Antigravity."
				), padre=self)
			if "codex" in disponibles:
				self.refrescar_sesiones()

		self.en_segundo_plano(detectar_todos, al_terminar=hecho, clave="detectar")

	# ------------------------------------------------------------------
	# Crear y continuar sesiones
	# ------------------------------------------------------------------

	def accion_nueva_sesion(self, carpeta: Path | None = None, mensaje: str = "", grupo: int | None = None,
			agente: str = "", titulo: str = "", equipo: str = "", carpeta_remota: str = "",
			preparar: Callable[[str], None] | None = None) -> bool:
		"""Abre «Nueva sesión»; devuelve ``True`` si se creó.

		:param grupo: grupo en el que nace la sesión; propone su agente y su carpeta.
		:param agente: agente propuesto (si no, el del grupo o el predeterminado).
		:param equipo: equipo por SSH propuesto (identificador o nombre), con ``carpeta_remota``.
		:param preparar: se llama con la clave de la sesión recién creada, antes de abrirla y de enviar
			nada (lo usa «Continuar aquí» en una importada para copiarle sus mensajes).
		"""
		from app.ui.dialogos.nueva_sesion import DialogoNuevaSesion

		datos_grupo = self.almacen.grupo(grupo) if grupo else None
		if carpeta is None and datos_grupo is not None and datos_grupo.carpeta and Path(datos_grupo.carpeta).is_dir():
			carpeta = Path(datos_grupo.carpeta)
		dialogo = DialogoNuevaSesion(self, self.gestor, self.almacen, self.config, carpeta=carpeta, mensaje=mensaje,
			grupo=grupo, agente=agente or (datos_grupo.agente if datos_grupo else ""), titulo=titulo, equipo=equipo,
			carpeta_remota=carpeta_remota)
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return False
			resultado = dialogo.resultado
		finally:
			dialogo.Destroy()
		if resultado is None:
			return False
		clave = self.gestor.nueva_sesion(resultado.proveedor, resultado.ajustes)
		if resultado.grupo:
			self.almacen.anadir_a_grupo(clave, resultado.grupo)
		if preparar is not None:
			try:
				preparar(clave)
			except Exception as error:  # noqa: BLE001 - la sesión ya existe; se explica y se sigue
				log.exception("No se pudo preparar la sesión nueva")
				mostrar_error(self, _("La sesión se ha creado, pero no se ha podido preparar: {e}").format(e=error),
					_("Nueva sesión"))
		avisos.sonar("nueva")
		self.refrescar_sesiones(True)
		self.abrir_sesion(clave, mover_foco=False)
		if resultado.mensaje.strip() or resultado.adjuntos:
			self.enviar_mensaje(clave, resultado.mensaje, resultado.adjuntos)
			self.ir_a_mensajes()
		else:
			avisos.anunciar(_("Sesión creada. Escribe tu primer mensaje."), interrumpir=True)
			self.ir_a_respuesta()
		return True

	def seguira_la_original(self, fila: FilaSesion) -> bool:
		"""Si «Continuar aquí» seguirá la sesión original (Preferencias, Historial) en lugar de copiarla."""
		return bool(self.opcion("historial.continuar_original", False)) and self.gestor.puede_seguir_original(fila.clave)

	def continuar_importada(self, fila: FilaSesion, mensaje: str = "") -> bool:
		"""«Continuar aquí» en una sesión importada (#228, fase 2): una sesión NUEVA de Ctrl+IA en este equipo.

		Abre «Nueva sesión» con el mismo agente (si está en este equipo; si no, el que
		se elija), la carpeta original si existe aquí y el título; la nueva recibe los
		mensajes de la importada para leerlos, y el primero que se envíe al agente lleva
		delante la conversación anterior (``GestorSesiones.preparar_desde_importada``).
		La importada no se toca. Devuelve si se creó.
		"""
		agente = fila.proveedor if fila.proveedor in self.gestor.proveedores else ""
		if not agente:
			avisos.anunciar(_("{a}, el agente de esa sesión, no está en este equipo: elige otro en «Nueva sesión».").format(
				a=nombre_agente(fila.proveedor)), interrumpir=True)
		carpeta: Path | None = None
		# Las rutas de red del otro equipo no se comprueban (podrían tardar); las locales, sí.
		if fila.carpeta and not fila.carpeta.startswith(("\\\\", "//")):
			try:
				if Path(fila.carpeta).is_dir():
					carpeta = Path(fila.carpeta)
			except OSError:
				carpeta = None
		clave_importada = fila.clave
		preparada: list[Any] = []

		def preparar(clave: str) -> None:
			preparada.append(self.gestor.preparar_desde_importada(clave, clave_importada))

		creada = self.accion_nueva_sesion(carpeta=carpeta, mensaje=mensaje, agente=agente,
			titulo=_("{t} (continuación)").format(t=fila.titulo), preparar=preparar)
		if creada and preparada and self.sesion_actual and self.gestor.contexto_pendiente(self.sesion_actual):
			if preparada[0].recortada:
				aviso = _("Sesión creada a partir de la importada «{t}», con sus mensajes para leerlos. Con tu primer "
					"mensaje, el agente recibirá la conversación anterior recortada (es larga). Escribe tu mensaje.")
			else:
				aviso = _("Sesión creada a partir de la importada «{t}», con sus mensajes para leerlos. Con tu primer "
					"mensaje, el agente recibirá la conversación anterior entera. Escribe tu mensaje.")
			avisos.anunciar(aviso.format(t=fila.titulo), interrumpir=True)
		return creada

	def accion_continuar_aqui(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		if fila.importada:
			self.continuar_importada(fila)
			return
		original = self.seguira_la_original(fila)
		propuesta = fila.titulo if original else _("{t} (continuación)").format(t=fila.titulo)
		titulo = pedir_texto(self, _("&Título de la sesión:") if original else _("&Título de la nueva sesión:"),
			_("Continuar aquí"), propuesta)
		if titulo is None:
			return

		def hecho(clave: str, siguio_original: bool) -> None:
			if fila.proveedor == "antigravity":
				# Antigravity no copia: la sesión sigue la misma conversación de Antigravity CLI.
				avisos.anunciar(_("«{t}» sigue ahora en Ctrl+IA. Es la misma conversación de Antigravity CLI, no una copia. "
					"Escribe tu mensaje.").format(t=fila.titulo), interrumpir=True)
			elif siguio_original:
				avisos.anunciar(_("«{t}» sigue ahora en Ctrl+IA. Es la misma sesión, no una copia: lo que hables aquí "
					"aparecerá también en {o}. Escribe tu mensaje.").format(t=fila.titulo, o=fila.nombre_origen), interrumpir=True)
			else:
				avisos.anunciar(_("Sesión creada a partir de «{t}». Escribe tu mensaje: el agente recuerda toda la conversación.").format(t=fila.titulo), interrumpir=True)
			self.ir_a_respuesta()

		self.continuar_sesion(fila, titulo.strip(), hecho, original)

	def continuar_sesion(self, fila: FilaSesion, titulo: str, al_crear, original: bool | None = None) -> None:
		"""Crea en segundo plano la sesión que continúa ``fila`` (con su historial) y llama a ``al_crear(clave, original)``.

		Si había que seguir la original y está en uso en el otro programa, ofrece hacer una copia.
		"""
		from app.services.gestor import SesionEnUso

		if original is None:
			original = self.seguira_la_original(fila)
		avisos.anunciar(_("Preparando la sesión..."), interrumpir=True)

		def creada(clave: str) -> None:
			self.refrescar_sesiones(True)
			self.abrir_sesion(clave, mover_foco=False)
			al_crear(clave, original)

		def fallo(error: BaseException) -> None:
			if isinstance(error, SesionEnUso):
				pregunta = _("{e}\n\n¿Quieres continuar en una copia? La original no cambiará.").format(e=error)
				if confirmar(self, pregunta, _("Continuar aquí")) == wx.ID_YES:
					copia = titulo if titulo.endswith(_(" (continuación)")) else _("{t} (continuación)").format(t=titulo or fila.titulo)
					self.continuar_sesion(fila, copia, al_crear, original=False)
				return
			mostrar_error(self, str(error), _("Continuar aquí"))

		self.en_segundo_plano(self.gestor.continuar_con_historial, fila.clave, titulo, original,
			al_terminar=creada, al_fallar=fallo)

	def accion_clonar_github(self) -> None:
		from app.ui.dialogos.github import clonar_repositorio

		clonar_repositorio(self, self.accion_nueva_sesion)

	# ------------------------------------------------------------------
	# Organización
	# ------------------------------------------------------------------

	def accion_renombrar(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		titulo = pedir_texto(self, _("&Nuevo título:"), _("Renombrar sesión"), fila.titulo)
		if titulo is None or not titulo.strip():
			return
		self.gestor.renombrar(fila.clave, titulo.strip())
		fila.titulo = titulo.strip()
		self.mostrar_sesiones(False)
		if fila.clave == self.sesion_actual:
			self.actualizar_titulo_ventana()
		avisos.anunciar(_("Renombrada."))

	def accion_favorita(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		fila.favorita = not fila.favorita
		self.gestor.marcar_favorita(fila.clave, fila.favorita)
		self.mostrar_sesiones(False)
		avisos.anunciar(_("Marcada como favorita.") if fila.favorita else _("Quitada de favoritas."), interrumpir=True)

	def accion_ocultar(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		indice = self.lista_sesiones.GetFocusedItem()
		self.gestor.ocultar(fila.clave, True)
		fila.oculta = True
		self.mostrar_sesiones(True)
		if self._visibles:
			self.lista_sesiones.enfocar_fila(min(max(indice, 0), len(self._visibles) - 1), anunciar=False)
		avisos.anunciar(_("Sesión oculta. Está en Ver, Mostrar, Ocultas."), interrumpir=True)

	def accion_mostrar_oculta(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		self.gestor.ocultar(fila.clave, False)
		fila.oculta = False
		self.mostrar_sesiones(True)
		avisos.anunciar(_("La sesión vuelve a estar en la lista."), interrumpir=True)

	def accion_borrar(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		if not fila.propia and not fila.importada:
			mostrar_mensaje(self, _(
				"Esta sesión pertenece al agente, no a Ctrl+IA, así que no se borra desde aquí. Puedes ocultarla con Suprimir."
			), _("Borrar sesión"))
			return
		if fila.importada:
			pregunta = _("¿Borrar la copia importada de «{t}»? Solo se borra la copia de Ctrl+IA; si la necesitas, "
				"puedes volver a importarla desde el archivo.").format(t=fila.titulo)
		else:
			pregunta = _("¿Borrar para siempre la sesión «{t}» y todos sus mensajes guardados en Ctrl+IA? "
				"No se puede deshacer.").format(t=fila.titulo)
		if confirmar(self, pregunta, _("Borrar sesión"), peligroso=True) != wx.ID_YES:
			return
		self.gestor.borrar(fila.clave)
		if fila.clave == self.sesion_actual:
			self.cerrar_sesion_abierta()
		self._todas = [f for f in self._todas if f.clave != fila.clave]
		self.mostrar_sesiones(True)
		avisos.anunciar(_("Sesión borrada."), interrumpir=True)

	def accion_terminada(self) -> None:
		"""Da la sesión por terminada (Ctrl+D) o, si ya lo estaba, la vuelve a activar."""
		fila = self.sesion_objetivo()
		if fila is None:
			return
		fila.terminada = not fila.terminada
		self.gestor.marcar_terminada(fila.clave, fila.terminada)
		if fila.terminada:
			fila.no_leidas = 0
		vista = self.opcion("lista.vista", "todas")
		indice = self.lista_sesiones.GetFocusedItem()
		self.mostrar_sesiones(True)
		if self._visibles and wx.Window.FindFocus() is self.lista_sesiones and not any(f.clave == fila.clave for f in self._visibles):
			self.lista_sesiones.enfocar_fila(min(max(indice, 0), len(self._visibles) - 1), anunciar=False)
		if fila.terminada:
			texto = _("Dada por terminada. Ya no saldrá en «Te necesitan»; está en Ver, Mostrar, Terminadas.")
			if vista == "terminadas":
				texto = _("Dada por terminada.")
		else:
			texto = _("Vuelve a estar activa.")
		avisos.anunciar(texto, interrumpir=True)

	def accion_marcar_leida(self) -> None:
		"""Ctrl+Q: quita la marca de respuesta nueva o, si no la tenía, la vuelve a poner."""
		fila = self.sesion_objetivo()
		if fila is None:
			return
		if fila.importada:
			avisos.anunciar(_("Las sesiones importadas no tienen respuestas nuevas."), interrumpir=True)
			return
		if fila.no_leidas:
			self.gestor.marcar_leida(fila.clave)
			fila.no_leidas = 0
			texto = _("Marcada como leída.")
		else:
			self.gestor.marcar_no_leida(fila.clave)
			fila.no_leidas = 1
			texto = _("Marcada como no leída.")
		self.mostrar_sesiones(False)
		avisos.anunciar(texto, interrumpir=True)

	def accion_marcar_todas_leidas(self) -> None:
		"""Ctrl+Mayús+Q: quita la marca de respuesta nueva de todas las sesiones."""
		con_nuevas = [f for f in self._todas if f.no_leidas]
		if not con_nuevas:
			avisos.anunciar(_("No hay ninguna sesión con respuestas sin leer."), interrumpir=True)
			return
		self.gestor.marcar_todas_leidas([f.clave for f in con_nuevas])
		for fila in con_nuevas:
			fila.no_leidas = 0
		self.mostrar_sesiones(self.opcion("lista.vista", "todas") == "sin_leer")
		avisos.anunciar(_("{n} sesiones marcadas como leídas.").format(n=len(con_nuevas)), interrumpir=True)

	def accion_anadir_a_grupo(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		from app.ui.dialogos.grupos import elegir_grupo

		grupo = elegir_grupo(self, self.almacen, _("Añadir «{t}» a un grupo").format(t=fila.titulo))
		if grupo is None:
			return
		self.almacen.anadir_a_grupo(fila.clave, grupo)
		nombre = dict(self.almacen.grupos()).get(grupo, "")
		if nombre and nombre not in fila.grupos:
			fila.grupos.append(nombre)
		self.mostrar_sesiones(False)
		avisos.anunciar(_("Añadida al grupo {g}.").format(g=nombre))

	def accion_quitar_de_grupo(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		if not fila.grupos:
			avisos.anunciar(_("Esta sesión no está en ningún grupo."), interrumpir=True)
			return
		indice = elegir_opcion(self, _("¿De qué grupo quieres sacarla?"), _("Quitar de un grupo"), fila.grupos)
		if indice is None:
			return
		nombre = fila.grupos[indice]
		ids = {n: i for i, n in self.almacen.grupos()}
		if nombre in ids:
			self.almacen.quitar_de_grupo(fila.clave, ids[nombre])
		fila.grupos.remove(nombre)
		self.mostrar_sesiones(True)
		avisos.anunciar(_("Quitada del grupo {g}.").format(g=nombre))

	def accion_mover_a_grupo(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		if not fila.grupos:
			avisos.anunciar(_("Esta sesión no está en ningún grupo. Usa Añadir a un grupo (Control+G)."), interrumpir=True)
			return
		ids = {n: i for i, n in self.almacen.grupos()}
		origen_nombre = fila.grupos[0]
		if len(fila.grupos) > 1:
			indice = elegir_opcion(self, _("¿De qué grupo quieres sacarla?"), _("Mover a otro grupo"), fila.grupos)
			if indice is None:
				return
			origen_nombre = fila.grupos[indice]
		origen = ids.get(origen_nombre)
		if origen is None:
			return
		from app.ui.dialogos.grupos import elegir_grupo

		destino = elegir_grupo(self, self.almacen, _("Mover «{t}» a otro grupo").format(t=fila.titulo), excluir=(origen,))
		if destino is None:
			return
		self.almacen.mover_a_grupo(fila.clave, origen, destino)
		nombre = dict(self.almacen.grupos()).get(destino, "")
		fila.grupos = [g for g in fila.grupos if g != origen_nombre] + ([nombre] if nombre and nombre not in fila.grupos else [])
		self.mostrar_sesiones(False)
		avisos.anunciar(_("Movida del grupo {o} al grupo {d}.").format(o=origen_nombre, d=nombre))

	# -- Lo que necesita un grupo abierto (ver app/ui/dialogos/grupo.py) -----------------

	def filas_sesiones(self) -> list[FilaSesion]:
		"""Todas las sesiones conocidas (también las ocultas), para la lista de un grupo."""
		return list(self._todas)

	def renombrar_sesion(self, clave: str, titulo: str) -> None:
		self.gestor.renombrar(clave, titulo)
		fila = self.fila_de(clave)
		if fila is not None:
			fila.titulo = titulo
		if clave == self.sesion_actual:
			self.actualizar_titulo_ventana()

	def exportar_grupo(self, id_grupo: int, padre: wx.Window | None = None) -> None:
		"""Guarda todas las sesiones de un grupo en un archivo (en segundo plano)."""
		grupo = self.almacen.grupo(id_grupo)
		if grupo is None:
			return
		comodines = _("Markdown (*.md)|*.md|Página web (*.html)|*.html|Texto (*.txt)|*.txt")
		nombre = "".join(c if c.isalnum() or c in " -_" else "_" for c in grupo.nombre)[:80] or "grupo"
		with wx.FileDialog(padre or self, _("Exportar el grupo «{g}»").format(g=grupo.nombre), defaultFile=nombre + ".md",
				wildcard=comodines, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialogo:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			ruta = Path(dialogo.GetPath())
			if not ruta.suffix:
				ruta = ruta.with_suffix((".md", ".html", ".txt")[dialogo.GetFilterIndex()])
		por_clave = {f.clave: f for f in self._todas}
		claves = self.almacen.sesiones_de_grupo(id_grupo)
		avisos.anunciar(_("Exportando {n} sesiones...").format(n=len(claves)))

		def exportar() -> Path:
			from app.services.exportar import Conversacion
			from app.services.exportar import exportar_grupo as exportar_conversaciones

			conversaciones = []
			for clave in claves:
				fila = por_clave.get(clave)
				if fila is None:
					continue
				try:
					mensajes = self.gestor.mensajes(clave)
				except Exception:  # noqa: BLE001 - una sesión ilegible no impide exportar las demás
					log.warning("No se pudieron leer los mensajes de %s para exportar el grupo", clave, exc_info=True)
					mensajes = []
				datos = {_("Agente"): nombre_agente(fila.proveedor), _("Carpeta"): fila.carpeta, _("Origen"): fila.nombre_origen}
				conversaciones.append(Conversacion(fila.titulo, mensajes, datos))
			return exportar_conversaciones(conversaciones, ruta, grupo.nombre, grupo.descripcion)

		self.en_segundo_plano(exportar, al_terminar=lambda r: avisos.notificar(
			_("Grupo exportado en {r}.").format(r=r), padre=self))

	def accion_gestionar_grupos(self) -> None:
		"""Lista de grupos (Control+Mayús+L): abrirlos, crearlos, editarlos, ordenarlos y exportarlos."""
		from app.ui.dialogos.grupos import DialogoGrupos

		dialogo = DialogoGrupos(self, self.almacen, contexto=self)
		try:
			dialogo.ShowModal()
			accion, renombrados = dialogo.accion, dict(dialogo.renombrados)
		finally:
			dialogo.Destroy()
		self._corregir_vista_de_grupo(renombrados)
		self.refrescar_sesiones(True)
		if accion is not None:
			self._hacer_accion_de_grupo(accion)

	def _corregir_vista_de_grupo(self, renombrados: dict[str, str]) -> None:
		"""Si la lista mostraba un grupo renombrado o borrado, sigue al nombre nuevo o vuelve a «Todas»."""
		vista = str(self.opcion("lista.vista", "todas"))
		if not vista.startswith("grupo:"):
			return
		nombre = vista.split(":", 1)[1]
		if nombre in renombrados:
			self.poner_opcion("lista.vista", f"grupo:{renombrados[nombre]}")
		elif nombre not in {n for _i, n in self.almacen.grupos()}:
			self.poner_opcion("lista.vista", "todas")
			avisos.anunciar(_("El grupo que mostraba la lista ya no existe: se muestran todas las sesiones."))

	def _hacer_accion_de_grupo(self, accion: tuple[str, Any]) -> None:
		tipo, valor = accion
		if tipo == "abrir":
			indice = next((i for i, f in enumerate(self._visibles) if f.clave == valor), -1)
			if indice >= 0:
				self.lista_sesiones.enfocar_fila(indice, anunciar=False)
			self.abrir_sesion(valor)
		elif tipo == "nueva":
			self.accion_nueva_sesion(grupo=valor)
		elif tipo == "mostrar":
			nombre = dict(self.almacen.grupos()).get(valor)
			if nombre:
				self.cambiar_vista(f"grupo:{nombre}")

	# ------------------------------------------------------------------
	# Abrir fuera de Ctrl+IA
	# ------------------------------------------------------------------

	def _carpeta_de(self, fila: FilaSesion) -> Path | None:
		if fila.remota:
			self._avisar_remota(fila, _("Abrir carpeta"))
			return None
		if fila.carpeta and Path(fila.carpeta).is_dir():
			return Path(fila.carpeta)
		mostrar_mensaje(self, _("La carpeta de esta sesión no existe o no se conoce."), _("Abrir carpeta"))
		return None

	def _avisar_remota(self, fila: FilaSesion, titulo: str) -> None:
		"""Explica por qué una acción de Windows no vale en una sesión de otro equipo."""
		mostrar_mensaje(self, _(
			"Esta sesión es de «{e}», por SSH: su carpeta ({c}) está en ese equipo, no en Windows. Para verla, "
			"abre Herramientas, «Equipos por SSH», elige el equipo y pulsa «Abrir una consola en el equipo»."
		).format(e=fila.equipo, c=fila.carpeta or _("sin carpeta")), titulo)

	def accion_abrir_explorador(self) -> None:
		fila = self.sesion_objetivo()
		carpeta = self._carpeta_de(fila) if fila else None
		if carpeta is not None:
			os.startfile(carpeta)  # noqa: S606 - carpeta de trabajo elegida por el usuario

	def accion_abrir_en_antigravity(self) -> None:
		fila = self.sesion_objetivo()
		carpeta = self._carpeta_de(fila) if fila else None
		if carpeta is None:
			return
		from app.services.agentes import antigravity

		if antigravity.abrir_carpeta_en_antigravity(carpeta, preferir="ide"):
			avisos.anunciar(_("Abriendo la carpeta en Antigravity IDE..."))
		else:
			mostrar_mensaje(self, _(
				"No se encuentra Antigravity IDE en este equipo. Puedes descargarlo de antigravity.google y "
				"comprobarlo después en Herramientas, Agentes instalados."
			), _("Antigravity IDE"))

	def accion_abrir_en_editor(self) -> None:
		fila = self.sesion_objetivo()
		carpeta = self._carpeta_de(fila) if fila else None
		if carpeta is None:
			return
		editor = str(self.opcion("agentes.editor_externo", "") or "").strip()
		from app.services.agentes import procesos

		if editor and Path(editor).is_file():
			procesos.lanzar_independiente([editor, str(carpeta)], carpeta)
		else:
			codigo = procesos.buscar_ejecutable(["code"], [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe"])  # auditoria: ignorar ruta-no-portable - localiza Visual Studio Code instalado; no guarda datos
			if codigo is not None:
				procesos.lanzar_independiente([str(codigo), str(carpeta)], carpeta)
			else:
				mostrar_mensaje(self, _(
					"No hay ningún editor configurado. Elige el programa en Preferencias, Agentes, «Editor de código»."
				), _("Abrir en el editor"))

	def accion_abrir_en_agente(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		if fila.remota:
			self._avisar_remota(fila, _("Abrir en la app del agente"))
			return
		if fila.proveedor == "antigravity":
			from app.services.agentes import antigravity

			carpeta = Path(fila.carpeta) if fila.carpeta and Path(fila.carpeta).is_dir() else None
			if carpeta is not None and antigravity.abrir_carpeta_en_antigravity(carpeta, preferir="app"):
				return
			app = antigravity.buscar_antigravity_app()
			if app is not None:
				from app.services.agentes import procesos

				procesos.lanzar_independiente([str(app)])
				return
		if fila.proveedor == "claude":
			from app.services.historial import claude as historial_claude

			fuente = self.gestor.fuente_externa(fila.clave)
			url = historial_claude.url_abrir_en_claude(fuente) if fuente is not None else None
			if not url and fila.id_externo:
				# Sesión de Ctrl+IA o de la terminal: la app de Claude la importa y la abre.
				url = historial_claude.url_importar_en_claude(fila.id_externo)
				if url and fila.propia:
					self._ceder_a_la_app(fila)
			if url:
				webbrowser.open(url)
				if fila.propia:
					avisos.anunciar(_("Abriendo «{t}» en la app de Claude.").format(t=fila.titulo), interrumpir=True)
				return
		if fila.proveedor == "codex" and fila.id_externo:
			from app.services.agentes import codex as agente_codex

			url = agente_codex.url_abrir_en_codex(fila.id_externo)
			if url:
				if fila.propia:
					self._ceder_a_la_app(fila)
				webbrowser.open(url)
				avisos.anunciar(_("Abriendo «{t}» en la app de Codex.").format(t=fila.titulo), interrumpir=True)
				return
		mostrar_mensaje(self, _(
			"Esta sesión no se puede abrir en la app de escritorio del agente. Para seguir con ella en Ctrl+IA "
			"usa «Continuar aquí»; para ver sus archivos, «Abrir la carpeta en el Explorador»."
		), _("Abrir en la app del agente"))

	def _ceder_a_la_app(self, fila: FilaSesion) -> None:
		"""Antes de abrir una sesión de Ctrl+IA en la app del agente, Ctrl+IA deja de tenerla abierta.

		Así no hay dos programas escribiendo a la vez en la misma conversación. Si
		vuelves a escribir aquí, Ctrl+IA la retoma con todo lo que se hizo en la app.
		"""
		if self.gestor.ocupada(fila.clave):
			avisos.anunciar(_("{a} sigue trabajando en esta sesión; ábrela en la app cuando termine para no "
				"mezclar las dos.").format(a=nombre_agente(fila.proveedor)), interrumpir=True)
			return
		self.gestor.cerrar_conversacion(fila.clave)

	def copiar(self, texto: str) -> None:
		from app.ui.accesibilidad.teclado import copiar_texto

		if copiar_texto(texto):
			avisos.anunciar(_("Copiado."), interrumpir=True)
		else:
			avisos.anunciar(_("No se pudo usar el portapapeles."), interrumpir=True)

	def accion_exportar(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		comodines = _("Markdown (*.md)|*.md|Página web (*.html)|*.html|Texto (*.txt)|*.txt")
		nombre = "".join(c if c.isalnum() or c in " -_" else "_" for c in fila.titulo)[:80] or "sesion"
		with wx.FileDialog(self, _("Exportar la sesión"), defaultFile=nombre + ".md", wildcard=comodines,
				style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialogo:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			ruta = Path(dialogo.GetPath())
			if not ruta.suffix:
				ruta = ruta.with_suffix((".md", ".html", ".txt")[dialogo.GetFilterIndex()])

		def exportar() -> Path:
			from app.services.exportar import exportar as exportar_mensajes

			mensajes = self.gestor.mensajes(fila.clave)
			datos = {_("Agente"): nombre_agente(fila.proveedor), _("Carpeta"): fila.carpeta, _("Origen"): fila.nombre_origen}
			return exportar_mensajes(mensajes, ruta, fila.titulo, datos=datos)

		self.en_segundo_plano(exportar, al_terminar=lambda r: avisos.notificar(_("Sesión exportada en {r}.").format(r=r), padre=self))

	# ------------------------------------------------------------------
	# Llevar sesiones a otro equipo
	# ------------------------------------------------------------------

	def _alcances_exportar(self) -> tuple[list[tuple[str, list[FilaSesion]]], int]:
		"""Opciones de «Qué sesiones» al exportar varias y cuál se propone."""
		alcances: list[tuple[str, list[FilaSesion]]] = []
		inicial = 0
		fila = self.fila_seleccionada()
		if fila is not None:
			alcances.append((_("La sesión elegida: {t}").format(t=fila.titulo), [fila]))
		if self._visibles:
			alcances.append((_("Las que se ven ahora en la lista ({n})").format(n=len(self._visibles)), list(self._visibles)))
			inicial = len(alcances) - 1
		alcances.append((_("Todas, también las ocultas y archivadas ({n})").format(n=len(self._todas)), list(self._todas)))
		por_clave = {f.clave: f for f in self._todas}
		for id_grupo, nombre in self.almacen.grupos():
			filas = [por_clave[c] for c in self.almacen.sesiones_de_grupo(id_grupo) if c in por_clave]
			if filas:
				alcances.append((_("Grupo {g} ({n})").format(g=nombre, n=len(filas)), filas))
		return alcances, inicial

	def accion_exportar_varias(self) -> None:
		"""Archivo, «Exportar varias sesiones a un archivo...»: un ZIP para llevarlas a otro equipo."""
		from app.ui.dialogos.intercambio import DialogoExportarSesiones

		if not self._todas:
			mostrar_mensaje(self, _("No hay sesiones que exportar."), _("Exportar sesiones"))
			return
		alcances, inicial = self._alcances_exportar()
		dialogo = DialogoExportarSesiones(self, alcances, inicial, leer_preferencias=self.preferencias_lectura)
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			claves, herramientas = list(dialogo.claves), dialogo.incluir_herramientas
		finally:
			dialogo.Destroy()
		por_clave = {f.clave: f for f in self._todas}
		filas = [por_clave[c] for c in claves if c in por_clave]
		nombre = _("Sesiones de {e} {f}").format(e=self._nombre_equipo(), f=time.strftime("%Y-%m-%d"))
		with wx.FileDialog(self, _("Guardar las sesiones en un archivo"), defaultFile=nombre + ".zip",
				wildcard=_("Archivo de sesiones de Ctrl+IA (*.zip)|*.zip"), style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as elegir:
			if elegir.ShowModal() != wx.ID_OK:
				return
			ruta = Path(elegir.GetPath())
		avisos.anunciar(_("Exportando {n} sesiones...").format(n=len(filas)))
		progreso = self._progreso_por_voz(_("Exportando {i} de {n}..."))

		def exportar():
			from app.core.version import APP_VERSION, NOMBRE_APLICACION
			from app.services.intercambio import exportar_sesiones

			return exportar_sesiones(filas, self.gestor.mensajes, ruta, almacen=self.almacen,
				incluir_herramientas=herramientas, version_app=f"{NOMBRE_APLICACION} {APP_VERSION}", progreso=progreso)

		def hecho(resultado) -> None:
			texto = _("{n} sesiones exportadas en {r}. Guarda el archivo en un sitio seguro: lleva todo lo que se dijo en ellas.").format(
				n=resultado.sesiones, r=resultado.ruta)
			if resultado.fallidas:
				texto += " " + _("No se pudieron leer los mensajes de: {t}.").format(t=", ".join(resultado.fallidas))
			avisos.notificar(texto, _("Exportar sesiones"), padre=self)

		self.en_segundo_plano(exportar, al_terminar=hecho)

	@staticmethod
	def _nombre_equipo() -> str:
		from app.services.intercambio import nombre_equipo

		return nombre_equipo()

	def _progreso_por_voz(self, plantilla: str, cada: float = 3.0):
		"""Función de progreso para un hilo: dice «{i} de {n}» como mucho cada ``cada`` segundos."""
		ultimo = [time.monotonic()]

		def progreso(indice: int, total: int) -> None:
			ahora = time.monotonic()
			if ahora - ultimo[0] >= cada and indice < total:
				ultimo[0] = ahora
				wx.CallAfter(avisos.anunciar, plantilla.format(i=indice, n=total))

		return progreso

	def accion_importar(self) -> None:
		"""Archivo, «Importar sesiones...»: trae las sesiones de un archivo exportado en otro equipo."""
		with wx.FileDialog(self, _("Importar sesiones de un archivo"),
				wildcard=_("Archivo de sesiones de Ctrl+IA (*.zip)|*.zip"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as elegir:
			if elegir.ShowModal() != wx.ID_OK:
				return
			ruta = Path(elegir.GetPath())
		avisos.anunciar(_("Importando sesiones..."))
		progreso = self._progreso_por_voz(_("Importando {i} de {n}..."))

		def importar():
			from app.services.intercambio import importar_sesiones

			return importar_sesiones(ruta, self.almacen, progreso=progreso)

		def hecho(resultado) -> None:
			partes = [_("Sesiones nuevas: {n}.").format(n=resultado.nuevas),
				_("Actualizadas: {n}.").format(n=resultado.actualizadas),
				_("Sin cambios: {n}.").format(n=resultado.sin_cambios)]
			if resultado.del_equipo:
				partes.append(_("Omitidas por ser de este mismo equipo: {n}.").format(n=resultado.del_equipo))
			if resultado.errores:
				partes.append(_("No se pudieron leer: {t}.").format(t=", ".join(resultado.errores)))
			if resultado.equipos:
				partes.append(_("Vienen de: {e}.").format(e=", ".join(sorted(resultado.equipos))))
			if resultado.total:
				partes.append(_("Son de solo lectura y están en Ver, Mostrar, Importadas de otro equipo; "
					"también aparecen en Buscar en todas las sesiones."))
			self.refrescar_sesiones(True)
			mostrar_mensaje(self, "\n".join(partes), _("Importar sesiones"))

		def fallo(error: BaseException) -> None:
			mostrar_error(self, _("No se pudieron importar las sesiones: {e}").format(e=error), _("Importar sesiones"))

		self.en_segundo_plano(importar, al_terminar=hecho, al_fallar=fallo)

	def accion_borrar_importadas(self) -> None:
		"""Archivo, «Borrar las sesiones importadas...»: quita las copias importadas (no toca nada más)."""
		importadas = [f for f in self._todas if f.importada]
		if not importadas:
			mostrar_mensaje(self, _("No hay sesiones importadas."), _("Borrar las sesiones importadas"))
			return
		if confirmar(self, _("¿Borrar las {n} sesiones importadas? Solo se borran las copias de Ctrl+IA; tus sesiones "
			"y las de los agentes no se tocan, y puedes volver a importarlas desde el archivo.").format(n=len(importadas)),
				_("Borrar las sesiones importadas"), peligroso=True) != wx.ID_YES:
			return
		if self.sesion_actual and any(f.clave == self.sesion_actual for f in importadas):
			self.cerrar_sesion_abierta()
		borradas = self.gestor.borrar_importadas()
		self._todas = [f for f in self._todas if not f.importada]
		self.mostrar_sesiones(True)
		avisos.anunciar(_("{n} sesiones importadas borradas.").format(n=borradas), interrumpir=True)

	# ------------------------------------------------------------------
	# Informe de sesiones
	# ------------------------------------------------------------------

	def accion_informe(self) -> None:
		"""Herramientas, «Informe de sesiones...» (Ctrl+I): tamaños, mensajes y totales de todas."""
		filas = list(self._todas)
		if not filas:
			mostrar_mensaje(self, _("Todavía no hay sesiones."), _("Informe de sesiones"))
			return
		avisos.anunciar(_("Preparando el informe de {n} sesiones...").format(n=len(filas)))
		progreso = self._progreso_por_voz(_("Leyendo {i} de {n}..."))

		def construir():
			from app.services import informe

			return informe.construir(filas, self.almacen, fuente_externa=self.gestor.fuente_externa,
				leer_mensajes=self.gestor.mensajes, progreso=progreso)

		self.en_segundo_plano(construir, al_terminar=self._mostrar_informe, clave="informe")

	def _mostrar_informe(self, filas_informe) -> None:
		from app.ui.dialogos.informe import DialogoInforme

		dialogo = DialogoInforme(self, filas_informe, contexto=self)
		try:
			dialogo.ShowModal()
			accion, cambiado = dialogo.accion, dialogo.cambiado
		finally:
			dialogo.Destroy()
		if cambiado:
			self.refrescar_sesiones(True)
		if accion is not None and accion[0] == "abrir":
			indice = next((i for i, f in enumerate(self._visibles) if f.clave == accion[1]), -1)
			if indice >= 0:
				self.lista_sesiones.enfocar_fila(indice, anunciar=False)
			self.abrir_sesion(accion[1])

	def accion_propiedades(self) -> None:
		fila = self.sesion_objetivo()
		if fila is None:
			return
		uso = self.gestor.uso(fila.clave)
		lineas = [
			_("Título: {v}").format(v=fila.titulo),
			_("Agente: {v}").format(v=nombre_agente(fila.proveedor)),
			_("Estado: {v}").format(v=nombre_estado(fila.estado)),
			_("Origen: {v}").format(v=fila.nombre_origen),
			_("Equipo: {v}").format(v=nombre_equipo(fila)),
			_("Carpeta: {v}").format(v=fila.carpeta or _("Sin carpeta")),
			_("Modelo: {v}").format(v=fila.modelo or _("Predeterminado")),
			_("Creada: {v}").format(v=fecha_relativa(fila.creada)),
			_("Última actividad: {v}").format(v=fecha_relativa(fila.actividad)),
			_("Grupos: {v}").format(v=", ".join(fila.grupos) or _("Ninguno")),
			_("Favorita: {v}").format(v=_("Sí") if fila.favorita else _("No")),
			_("Oculta: {v}").format(v=_("Sí") if fila.oculta else _("No")),
			_("Terminada: {v}").format(v=_("Sí") if fila.terminada else _("No")),
			_("Respuestas sin leer: {v}").format(v=fila.no_leidas),
			_("Identificador en Ctrl+IA: {v}").format(v=fila.clave),
			_("Identificador en el agente: {v}").format(v=fila.id_externo or _("Todavía no tiene")),
		]
		if fila.importada:
			sesion = self.almacen.sesion(fila.clave)
			datos = sesion.extra.get("importada") if sesion is not None else None
			if isinstance(datos, dict):
				lineas.append(_("Importada del equipo: {v}").format(v=datos.get("equipo") or _("Desconocido")))
				lineas.append(_("Identificador original: {v}").format(v=datos.get("clave_original") or ""))
				lineas.append(_("Importada el: {v}").format(v=fecha_relativa(float(datos.get("importada") or 0))))
				lineas.append(_("Es de solo lectura: se puede leer, buscar, agrupar y exportar, pero no continuar."))
		if fila.remota and fila.propia:
			lineas.append(_("Trabaja en otro equipo por SSH: {v}").format(v=fila.equipo))
		elif fila.remota:
			lineas.append(_("Está en otro equipo, {v}: Ctrl+IA la lee por SSH y «Continuar aquí» la sigue en ese "
				"equipo.").format(v=fila.equipo))
		if fila.propia:
			sesion = self.almacen.sesion(fila.clave)
			if sesion is not None:
				lineas.append(_("Esfuerzo: {v}").format(v=sesion.esfuerzo or _("Predeterminado")))
				proveedor = self.gestor.proveedor(fila.proveedor)
				modos = proveedor.modos_permiso() if proveedor is not None else []
				modo = next((m.etiqueta for m in modos if m.id == sesion.modo_permiso), sesion.modo_permiso)
				lineas.append(_("Modo de permisos: {v}").format(v=modo or _("El de la configuración del agente")))
				if sesion.reglas:
					lineas.append(_("Permitido durante la sesión: {v}").format(v="; ".join(sesion.reglas)))
				if sesion.bifurcada_de:
					lineas.append(_("Continúa la sesión: {v}").format(v=sesion.bifurcada_de))
		if uso.get("contexto_usado"):
			lineas.append(_("Contexto usado: {v} unidades de texto (tokens)").format(v=uso["contexto_usado"]))
		from app.services.extraccion import resumen_tareas

		progreso = resumen_tareas(self._tareas_de(fila.clave))
		if progreso:
			lineas.append(_("Lista de tareas del agente: {v}").format(v=progreso))
		if fila.sin_conversacion:
			lineas.append(_("Conversación: no disponible. {v}").format(v=TEXTO_SIN_CONVERSACION))
		if hasattr(self, "lineas_pr"):
			lineas.extend(self.lineas_pr(fila))
		self._mostrar_propiedades(fila, lineas)

	def _mostrar_propiedades(self, fila: FilaSesion, lineas: list[str]) -> None:
		"""Muestra Propiedades; en una carpeta de este equipo, antes pregunta a git (en segundo plano) la rama
		y si es una copia aislada, y lo pone tras la línea de la carpeta."""
		from app.ui.visor_ayuda import mostrar_documento

		titulo = _("Propiedades de «{t}»").format(t=fila.titulo)

		def mostrar(extra: list[str] | None = None) -> None:
			todas = list(lineas)
			if extra:
				posicion = next((i + 1 for i, linea in enumerate(todas) if linea.startswith(_("Carpeta: {v}").format(v=""))),
					len(todas))
				todas[posicion:posicion] = [str(linea) for linea in extra]
			mostrar_documento(self, titulo, "\n\n".join(todas), forzar_texto=True)

		if not fila.carpeta or fila.remota or fila.importada:
			mostrar()
			return
		from app.services import git

		self.en_segundo_plano(git.describir_repositorio, fila.carpeta, al_terminar=mostrar,
			al_fallar=lambda _error: mostrar(), clave="propiedades")
