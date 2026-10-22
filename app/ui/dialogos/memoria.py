"""«Lo que el agente sabe de ti»: archivos de instrucciones y memoria de los agentes.

Lista los archivos que leen Claude, Codex, Antigravity y Gemini (existan o
no), agrupados por agente; el del agente de la sesión va primero. Acciones:

- Intro o «Ver»: muestra el archivo en el visor de texto (los de más de 1 MB
  no se abren: se avisa).
- «Editar»: lo abre con el editor configurado o con el de Windows.
- «Abrir la carpeta»: el Explorador con el archivo seleccionado.
- «Crear»: solo para un archivo de instrucciones ``.md`` que aún no existe;
  lo crea con una plantilla breve y lo abre para editarlo.
- «Ordenar por» (menú contextual): por agente, más recientes, más antiguos o
  nombre; se recuerda en ``memoria.orden`` y conserva el archivo seleccionado.

El diálogo nunca escribe en ningún archivo salvo el que se crea con «Crear».
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import wx

from app.services import memoria
from app.services.agentes import procesos
from app.ui import avisos, visor_ayuda
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import confirmar, mostrar_error, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _
from app.ui.lectura_listas import escribir_fecha

log = logging.getLogger(__name__)


def N_(texto: str) -> str:  # noqa: N802 - convención de gettext
	"""Marca un texto para traducirlo más tarde (con ``_``) sin traducirlo ahora."""
	return texto


#: Clave de la configuración donde se recuerda el orden de la lista.
CLAVE_ORDEN = "memoria.orden"
#: Cada orden: (texto del menú, texto que se anuncia al elegirlo).
ORDENES: dict[str, tuple[str, str]] = {
	memoria.ORDEN_AGENTE: (N_("Por &agente"), N_("Ordenado por agente")),
	memoria.ORDEN_RECIENTES: (N_("Más &recientes primero"), N_("Ordenado: más recientes primero")),
	memoria.ORDEN_ANTIGUOS: (N_("Más an&tiguos primero"), N_("Ordenado: más antiguos primero")),
	memoria.ORDEN_NOMBRE: (N_("Por &nombre"), N_("Ordenado por nombre")),
}


def abrir_en_editor(ruta: Path, editor: str = "") -> bool:
	"""Abre ``ruta`` con ``editor`` (si se indica) o con el editor de Windows.

	Sin editor configurado se prueba el verbo «editar» de Windows, después el
	programa asociado y, por último, el Bloc de notas.
	"""
	editor = (editor or "").strip().strip('"')
	if editor:
		return procesos.lanzar_independiente([editor, str(ruta)])
	for verbo in ("edit", None):
		try:
			if verbo:
				os.startfile(str(ruta), verbo)  # noqa: S606 - abre un archivo elegido por el usuario
			else:
				os.startfile(str(ruta))  # noqa: S606
			return True
		except OSError:
			log.debug("os.startfile(%s, %s) falló", ruta, verbo, exc_info=True)
	return procesos.lanzar_independiente(["notepad.exe", str(ruta)])


def abrir_carpeta_de(ruta: Path) -> bool:
	"""Abre el Explorador con ``ruta`` seleccionada, o su carpeta más cercana que exista."""
	ruta = Path(ruta)
	if ruta.is_file():
		return procesos.lanzar_independiente(["explorer.exe", f"/select,{ruta}"])
	carpeta = ruta.parent
	while not carpeta.is_dir() and carpeta.parent != carpeta:
		carpeta = carpeta.parent
	return procesos.lanzar_independiente(["explorer.exe", str(carpeta)])


class DialogoMemoria(wx.Dialog):
	"""Lista de los archivos de instrucciones y memoria que leen los agentes.

	:param carpeta: carpeta del proyecto de la sesión (``None``: solo los globales).
	:param proveedor: identificador del agente de la sesión (su grupo va primero).
	:param editor: programa con el que editar; vacío = el editor de Windows.
	:param configuracion: dónde se recuerda el orden de la lista (``obtener``/``establecer``);
		sin ella, la de la ventana padre (``padre.config``) y, si no tiene, no se recuerda.
	"""

	def __init__(self, padre: wx.Window | None, carpeta: Path | None, proveedor: str, editor: str = "",
			configuracion: Any = None) -> None:
		super().__init__(
			padre, title=_("Lo que el agente sabe de ti"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
			name=_("Lo que el agente sabe de ti"),
		)
		self.carpeta = Path(carpeta) if carpeta is not None else None
		self.proveedor = proveedor or ""
		self.editor = editor or ""
		self.archivos: list[memoria.ArchivoMemoria] = []
		self._config = configuracion if configuracion is not None else getattr(padre, "config", None)
		orden = self._opcion(CLAVE_ORDEN, memoria.ORDEN_AGENTE)
		self.orden = orden if orden in ORDENES else memoria.ORDEN_AGENTE

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		explicacion = wx.StaticText(panel, label=_(
			"Estos son los archivos de instrucciones y de memoria que leen los agentes al trabajar contigo. "
			"Intro muestra el seleccionado; la tecla Aplicaciones abre más acciones, también para ordenar la lista."
		))
		explicacion.Wrap(self.FromDIP(640))
		raiz.Add(explicacion, 0, wx.ALL, 8)

		etiqueta = wx.StaticText(panel, label=_("Archivos de &instrucciones y memoria:"))
		self.lista = ListaAccesible(
			panel, _("Archivos de instrucciones y memoria"),
			columnas=[(_("Archivo"), 320), (_("Agente"), 150), (_("Ámbito"), 90), (_("Existe"), 60),
				(_("Modificado"), 170), (_("Ruta"), 380)],
		)
		self.lista.SetHelpText(_(
			"Archivos que leen los agentes. Arriba y abajo cambian de archivo; izquierda y derecha leen las columnas, "
			"entre ellas la fecha de la última modificación. Intro lo muestra; la tecla Aplicaciones abre las demás "
			"acciones: editar, abrir la carpeta, crear el archivo si no existe u ordenar la lista por agente, por "
			"fecha (más recientes o más antiguos primero) o por nombre. El orden se recuerda para la próxima vez."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(760, 300)))
		raiz.Add(etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_ver = wx.Button(panel, label=_("&Ver"))
		self.boton_ver.SetHelpText(_("Muestra el contenido del archivo seleccionado en un visor de solo lectura. También con Intro."))
		self.boton_editar = wx.Button(panel, label=_("&Editar"))
		self.boton_editar.SetHelpText(_("Abre el archivo seleccionado en el editor para cambiarlo."))
		self.boton_carpeta = wx.Button(panel, label=_("Abrir la &carpeta"))
		self.boton_carpeta.SetHelpText(_("Abre la carpeta del archivo en el Explorador de Windows, con el archivo seleccionado."))
		self.boton_crear = wx.Button(panel, label=_("C&rear"))
		self.boton_crear.SetHelpText(_(
			"Crea el archivo de instrucciones seleccionado, que todavía no existe, con un texto inicial que explica "
			"para qué sirve, y lo abre para que lo edites. Pide confirmación."
		))
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("Cerr&ar"))
		self.boton_cerrar.SetHelpText(_("Cierra esta ventana. También con Escape."))
		for boton in (self.boton_ver, self.boton_editar, self.boton_carpeta, self.boton_crear):
			fila.Add(boton, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		fila.Add(self.boton_cerrar, 0)
		raiz.Add(fila, 0, wx.EXPAND | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		self.boton_ver.Bind(wx.EVT_BUTTON, lambda _e: self.ver())
		self.boton_editar.Bind(wx.EVT_BUTTON, lambda _e: self.editar())
		self.boton_carpeta.Bind(wx.EVT_BUTTON, lambda _e: self.abrir_carpeta())
		self.boton_crear.Bind(wx.EVT_BUTTON, lambda _e: self.crear())
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.ver())
		self.lista.Bind(wx.EVT_LIST_ITEM_FOCUSED, lambda e: (self.actualizar_botones(), e.Skip()))
		self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: (self.actualizar_botones(), e.Skip()))
		vincular_menu_contextual(self.lista, self._crear_menu)

		self.cargar()
		preparar_dialogo(self, foco_inicial=self.lista)
		panel.Layout()
		self.Layout()
		self.CentreOnParent()

	# -- Datos ------------------------------------------------------------------

	def _opcion(self, clave: str, defecto: Any = None) -> Any:
		"""Valor de la configuración, o ``defecto`` si no hay configuración o falla."""
		if self._config is None:
			return defecto
		try:
			return self._config.obtener(clave, defecto)
		except (KeyError, ValueError, AttributeError):
			log.debug("No se pudo leer la opción %s", clave, exc_info=True)
			return defecto

	def _fecha(self, archivo: memoria.ArchivoMemoria) -> str:
		"""Columna «Modificado»: fecha legible como en la lista de sesiones, o vacía si no existe."""
		if not archivo.existe or archivo.modificado is None:
			return ""
		return escribir_fecha(archivo.modificado, self._opcion)

	def cargar(self, ruta_a_enfocar: Path | None = None) -> None:
		"""Vuelve a buscar los archivos y rellena la lista (conserva la fila o va a ``ruta_a_enfocar``)."""
		self.archivos = memoria.ordenar(memoria.archivos_memoria(self.carpeta, primero=self.proveedor), self.orden)
		filas = [
			[
				_(archivo.nombre),
				_(archivo.nombre_agente),
				_(archivo.ambito),
				_("Sí") if archivo.existe else _("No"),
				self._fecha(archivo),
				str(archivo.ruta),
			]
			for archivo in self.archivos
		]
		self.lista.reemplazar_filas(filas)
		if ruta_a_enfocar is not None:
			for indice, archivo in enumerate(self.archivos):
				if archivo.ruta == ruta_a_enfocar:
					self.lista.enfocar_fila(indice, anunciar=False)
					break
		self.actualizar_botones()

	def seleccionado(self) -> memoria.ArchivoMemoria | None:
		"""Archivo de la fila enfocada, o ``None``."""
		fila = self.lista.fila_actual()
		return self.archivos[fila] if 0 <= fila < len(self.archivos) else None

	def actualizar_botones(self) -> None:
		"""Habilita cada botón solo si su acción tiene sentido para la fila actual."""
		archivo = self.seleccionado()
		existe = archivo is not None and archivo.existe
		self.boton_ver.Enable(existe)
		self.boton_editar.Enable(existe)
		self.boton_carpeta.Enable(archivo is not None)
		self.boton_crear.Enable(archivo is not None and archivo.creable and not archivo.existe)

	# -- Acciones ---------------------------------------------------------------

	def _no_existe(self, archivo: memoria.ArchivoMemoria) -> None:
		if archivo.creable:
			mensaje = _("«{nombre}» todavía no existe. Puedes crearlo con el botón «Crear» (Alt+R).")
		else:
			mensaje = _("«{nombre}» todavía no existe. El agente lo creará cuando lo necesite.")
		mostrar_mensaje(self, mensaje.format(nombre=_(archivo.nombre)), _("Archivo inexistente"))

	def ver(self) -> None:
		"""Muestra el archivo seleccionado en el visor de texto."""
		archivo = self.seleccionado()
		if archivo is None:
			return
		if not archivo.ruta.is_file():
			self._no_existe(archivo)
			return
		if memoria.demasiado_grande(archivo.ruta):
			mostrar_mensaje(
				self,
				_("«{nombre}» ocupa más de 1 MB y no se abre aquí para no bloquear el programa. Usa «Editar» para abrirlo con el editor.").format(
					nombre=_(archivo.nombre)
				),
				_("Archivo demasiado grande"),
			)
			return
		try:
			texto = memoria.leer_texto(archivo.ruta)
		except OSError as exc:
			log.warning("No se pudo leer %s", archivo.ruta, exc_info=True)
			mostrar_error(self, _("No se pudo leer «{ruta}»: {error}").format(ruta=archivo.ruta, error=exc))
			return
		if archivo.ruta.suffix.lower() != ".md":
			# JSON o TOML: como bloque de código, para que el visor no lo interprete como Markdown.
			texto = "```\n" + texto.rstrip("\n") + "\n```\n"
		titulo = _("{nombre} ({archivo})").format(nombre=_(archivo.nombre), archivo=archivo.ruta.name)
		visor_ayuda.mostrar_documento(self, titulo, texto, forzar_texto=True)

	def editar(self) -> None:
		"""Abre el archivo seleccionado en el editor."""
		archivo = self.seleccionado()
		if archivo is None:
			return
		if not archivo.ruta.is_file():
			self._no_existe(archivo)
			return
		if abrir_en_editor(archivo.ruta, self.editor):
			voz.hablar(_("Abriendo el editor"))
		else:
			mostrar_error(self, _("No se pudo abrir el editor con «{ruta}».").format(ruta=archivo.ruta))

	def abrir_carpeta(self) -> None:
		"""Abre el Explorador en la carpeta del archivo seleccionado."""
		archivo = self.seleccionado()
		if archivo is None:
			return
		if not abrir_carpeta_de(archivo.ruta):
			mostrar_error(self, _("No se pudo abrir la carpeta de «{ruta}».").format(ruta=archivo.ruta))

	def crear(self) -> None:
		"""Crea el archivo de instrucciones seleccionado (tras confirmar) y lo abre para editar."""
		archivo = self.seleccionado()
		if archivo is None:
			return
		if archivo.ruta.exists():
			mostrar_mensaje(self, _("«{nombre}» ya existe; no se toca.").format(nombre=_(archivo.nombre)))
			self.cargar(archivo.ruta)
			return
		if not archivo.creable:
			mostrar_mensaje(self, _("Este archivo no se crea desde aquí: lo crea el propio agente cuando lo necesita."))
			return
		pregunta = _(
			"Se creará el archivo «{ruta}» con un texto inicial que explica para qué sirve. "
			"Después se abrirá en el editor para que escribas tus instrucciones. ¿Crearlo?"
		).format(ruta=archivo.ruta)
		if confirmar(self, pregunta, _("Crear archivo de instrucciones")) != wx.ID_YES:
			return
		try:
			ruta = memoria.crear_desde_plantilla(archivo)
		except FileExistsError:
			mostrar_mensaje(self, _("«{nombre}» ya existe; no se toca.").format(nombre=_(archivo.nombre)))
			self.cargar(archivo.ruta)
			return
		except (OSError, ValueError) as exc:
			log.warning("No se pudo crear %s", archivo.ruta, exc_info=True)
			mostrar_error(self, _("No se pudo crear «{ruta}»: {error}").format(ruta=archivo.ruta, error=exc))
			return
		self.cargar(ruta)
		voz.hablar(_("Archivo creado"))
		if not abrir_en_editor(ruta, self.editor):
			mostrar_error(self, _("El archivo se ha creado, pero no se pudo abrir el editor."))

	def copiar_ruta(self) -> None:
		"""Copia la ruta del archivo seleccionado al portapapeles."""
		archivo = self.seleccionado()
		if archivo is None:
			return
		from app.ui.accesibilidad.teclado import copiar_texto

		if copiar_texto(str(archivo.ruta)):
			voz.hablar(_("Ruta copiada"))
		else:
			voz.hablar(_("No se pudo usar el portapapeles."))

	def ordenar_por(self, orden: str) -> None:
		"""Cambia el orden de la lista, lo recuerda y deja seleccionado el mismo archivo."""
		if orden not in ORDENES:
			return
		archivo = self.seleccionado()
		self.orden = orden
		if self._config is not None:
			try:
				self._config.establecer(CLAVE_ORDEN, orden)
			except (KeyError, ValueError, OSError):
				log.warning("No se pudo guardar el orden de «Lo que el agente sabe de ti»", exc_info=True)
		self.cargar(archivo.ruta if archivo is not None else None)
		avisos.anunciar(_(ORDENES[orden][1]), interrumpir=True)

	def _crear_menu(self) -> wx.Menu | None:
		archivo = self.seleccionado()
		menu = wx.Menu()
		existe = archivo is not None and archivo.existe
		for etiqueta, accion, activa in (
			(_("&Ver"), self.ver, existe),
			(_("&Editar"), self.editar, existe),
			(_("Abrir la &carpeta"), self.abrir_carpeta, archivo is not None),
			(_("C&rear"), self.crear, archivo is not None and archivo.creable and not existe),
			(_("Co&piar la ruta"), self.copiar_ruta, archivo is not None),
		):
			elemento = menu.Append(wx.ID_ANY, etiqueta)
			elemento.Enable(activa)
			menu.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), elemento)
		menu.AppendSeparator()
		submenu = wx.Menu()
		for orden, (etiqueta, _anuncio) in ORDENES.items():
			elemento = submenu.AppendRadioItem(wx.ID_ANY, _(etiqueta))
			elemento.Check(orden == self.orden)
			submenu.Bind(wx.EVT_MENU, lambda _e, o=orden: self.ordenar_por(o), elemento)
		menu.AppendSubMenu(submenu, _("&Ordenar por"), _("Cambia el orden de la lista; se recuerda para la próxima vez."))
		return menu
