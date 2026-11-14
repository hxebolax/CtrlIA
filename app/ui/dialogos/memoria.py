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
- «Editar aquí» (1.6.0, menú contextual o F2): el archivo en un cuadro de
  texto; Ctrl+S guarda con copia previa en ``data/backups/agentes`` y
  escritura atómica, sin pisar lo que el agente haya escrito entre tanto sin
  preguntarlo.
- «Añadir una memoria» (Claude Code, de la carpeta de la sesión) y «Borrar
  la memoria» (Supr, con confirmación y copia previa; quita su línea de
  ``MEMORY.md``).
- «Crear un subagente» de Claude Code o de Codex (global; nunca sustituye uno).

El diálogo solo escribe cuando la persona lo pide con una de esas acciones.
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
from app.ui.accesibilidad.dialogos import (
	confirmar,
	crear_botonera,
	enfocar,
	mostrar_error,
	mostrar_mensaje,
	preparar_dialogo,
)
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo, habilitar
from app.ui.accesibilidad.teclado import instalar_atajos, vincular_menu_contextual
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
			"entre ellas la fecha de la última modificación. Intro lo muestra; F2 lo edita aquí mismo; Suprimir borra "
			"una nota de la memoria de Claude Code. La tecla Aplicaciones abre las demás acciones: editar con tu "
			"editor, abrir la carpeta, crear el archivo si no existe, añadir una memoria de Claude Code, crear un "
			"subagente u ordenar la lista por agente, por fecha (más recientes o más antiguos primero) o por nombre. "
			"El orden se recuerda para la próxima vez."
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
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla_lista)

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

	def _al_tecla_lista(self, evento: wx.KeyEvent) -> None:
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		if modificadores == wx.MOD_NONE and tecla == wx.WXK_F2:
			self.editar_aqui()
			return
		if modificadores == wx.MOD_NONE and tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.borrar_memoria()
			return
		evento.Skip()

	def editar_aqui(self) -> None:
		"""Abre el archivo elegido en el editor de Ctrl+IA (F2); Ctrl+S guarda con copia previa."""
		archivo = self.seleccionado()
		if archivo is None:
			return
		if not archivo.ruta.is_file():
			self._no_existe(archivo)
			return
		if not memoria.es_editable(archivo):
			mostrar_mensaje(self, _("«{nombre}» no se edita aquí (es muy grande o no es un archivo de texto). Usa "
				"«Editar» para abrirlo con tu editor.").format(nombre=_(archivo.nombre)), _("Editar aquí"))
			return
		try:
			dialogo = DialogoEditarArchivo(self, archivo)
		except (OSError, memoria.ErrorMemoria) as error:
			log.warning("No se pudo abrir %s para editarlo", archivo.ruta, exc_info=True)
			mostrar_error(self, _("No se pudo abrir «{ruta}»: {error}").format(ruta=archivo.ruta, error=error))
			return
		try:
			dialogo.ShowModal()
			guardado = dialogo.guardado
		finally:
			dialogo.Destroy()
		if guardado:
			self.cargar(archivo.ruta)
		enfocar(self.lista)

	def puede_borrar(self, archivo: memoria.ArchivoMemoria | None) -> bool:
		"""Si el archivo es una nota de la memoria de Claude Code que se puede borrar (no el índice)."""
		return (archivo is not None and archivo.existe and archivo.tipo == memoria.MEMORIA
			and archivo.agente == memoria.CLAUDE and archivo.ruta.name.upper() != "MEMORY.MD")

	def anadir_memoria(self) -> None:
		"""Añade una nota a la memoria de Claude Code de la carpeta de la sesión."""
		if self.carpeta is None:
			mostrar_mensaje(self, _("Para añadir una memoria, abre esta ventana desde una sesión con su carpeta: la "
				"memoria de Claude Code es de cada proyecto."), _("Añadir una memoria"))
			return
		dialogo = DialogoNuevaMemoria(self, self.carpeta)
		try:
			ruta = dialogo.ruta if dialogo.ShowModal() == wx.ID_OK else None
		finally:
			dialogo.Destroy()
		if ruta is not None:
			self.cargar(ruta)
			avisos.anunciar(_("Memoria añadida"), interrumpir=True)
		enfocar(self.lista)

	def borrar_memoria(self) -> None:
		"""Borra la nota de la memoria de Claude Code elegida (Supr), tras confirmar y con copia previa."""
		archivo = self.seleccionado()
		if not self.puede_borrar(archivo):
			voz.hablar(_("Solo se pueden borrar desde aquí las notas de la memoria automática de Claude Code."))
			return
		pregunta = _("¿Borrar la memoria «{nombre}»? Claude Code dejará de recordarla en este proyecto. Antes se "
			"guarda una copia en la carpeta data, backups, agentes de Ctrl+IA.").format(nombre=archivo.ruta.stem)
		if confirmar(self, pregunta, _("Borrar la memoria"), peligroso=True) != wx.ID_YES:
			return
		indice = self.lista.fila_actual()
		try:
			memoria.borrar_memoria_claude(archivo.ruta, carpeta_copias())
		except (ValueError, memoria.ErrorMemoria) as error:
			mostrar_error(self, str(error), _("Borrar la memoria"))
			return
		self.cargar()
		if self.archivos:
			self.lista.enfocar_fila(min(indice, len(self.archivos) - 1), anunciar=False)
		avisos.anunciar(_("Memoria borrada"), interrumpir=True)

	def crear_subagente(self) -> None:
		"""Crea un subagente de Claude Code o de Codex."""
		dialogo = DialogoNuevoSubagente(self, self.proveedor)
		try:
			ruta = dialogo.ruta if dialogo.ShowModal() == wx.ID_OK else None
		finally:
			dialogo.Destroy()
		if ruta is not None:
			self.cargar(ruta)
			avisos.anunciar(_("Subagente creado"), interrumpir=True)
		enfocar(self.lista)

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
			(_("&Ver\tIntro"), self.ver, existe),
			(_("Editar a&quí\tF2"), self.editar_aqui, existe),
			(_("&Editar con tu editor"), self.editar, existe),
			(_("Abrir la &carpeta"), self.abrir_carpeta, archivo is not None),
			(_("C&rear"), self.crear, archivo is not None and archivo.creable and not existe),
			(_("Co&piar la ruta"), self.copiar_ruta, archivo is not None),
			(_("Añadir una &memoria de Claude Code..."), self.anadir_memoria, self.carpeta is not None),
			(_("&Borrar esta memoria\tSupr"), self.borrar_memoria, self.puede_borrar(archivo)),
			(_("Crear un &subagente..."), self.crear_subagente, True),
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


# ── Editar aquí, añadir memorias y crear subagentes (1.6.0) ─────────────────


def carpeta_copias() -> Path:
	"""``data/backups/agentes``: copias de lo que se cambia en los archivos de los agentes."""
	from app.core.paths import ruta_copias

	return ruta_copias() / "agentes"


class DialogoEditarArchivo(wx.Dialog):
	"""Edita un archivo de los agentes en un cuadro de texto: Ctrl+S guarda (con copia previa y escritura atómica).

	Si el archivo cambió en el disco desde que se abrió (el agente lo reescribió), pregunta antes de
	guardar; al cerrar con cambios sin guardar, pregunta si guardarlos. ``self.guardado`` dice si se guardó.
	"""

	def __init__(self, padre: wx.Window | None, archivo: memoria.ArchivoMemoria, copias: Path | None = None) -> None:
		titulo = _("Editar {nombre}").format(nombre=_(archivo.nombre))
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.archivo = archivo
		self.copias = copias or carpeta_copias()
		self.guardado = False
		self.cargado = memoria.cargar_para_editar(archivo.ruta)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.texto = crear_campo(self, _("C&ontenido de {archivo}:").format(archivo=archivo.ruta.name), wx.TextCtrl,
			value=self.cargado.texto, style=wx.TE_MULTILINE | wx.TE_RICH2, sizer=raiz,
			ayuda=_("El texto del archivo. Escribe lo que quieras cambiar y pulsa Control+S para guardarlo: antes se "
				"guarda una copia del archivo en la carpeta data, backups, agentes de Ctrl+IA. Escape cierra y, si hay "
				"cambios sin guardar, pregunta qué hacer."))
		self.texto.SetMinSize(self.FromDIP(wx.Size(680, 360)))
		raiz.GetItem(self.texto).SetProportion(1)
		raiz.GetItem(self.texto).SetFlag(wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_guardar = wx.Button(self, wx.ID_SAVE, _("&Guardar (Ctrl+S)"))
		self.boton_guardar.SetHelpText(_("Guarda los cambios en el archivo, con una copia de seguridad antes."))
		self.boton_cerrar = wx.Button(self, wx.ID_CLOSE, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra el editor. Si hay cambios sin guardar, pregunta si guardarlos. También con Escape."))
		fila.Add(self.boton_guardar, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		fila.Add(self.boton_cerrar, 0)
		raiz.Add(fila, 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.boton_guardar.Bind(wx.EVT_BUTTON, lambda _e: self.guardar())
		self.boton_cerrar.Bind(wx.EVT_BUTTON, lambda _e: self.cerrar())
		self.Bind(wx.EVT_CLOSE, lambda _e: self.cerrar())
		instalar_atajos(self, [(wx.ACCEL_CTRL, ord("S"), self.guardar)])
		preparar_dialogo(self, id_escape=wx.ID_CLOSE, foco_inicial=self.texto)
		# Después de preparar_dialogo: este manejador se atiende antes y Escape pregunta si hay cambios.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		self.texto.SetInsertionPoint(0)
		self.CentreOnParent()

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() == wx.WXK_ESCAPE and evento.GetModifiers() == wx.MOD_NONE:
			self.cerrar()
			return
		evento.Skip()

	def modificado(self) -> bool:
		"""Si el texto es distinto del que se cargó (o del último guardado)."""
		return self.texto.GetValue().replace("\r\n", "\n") != self.cargado.texto

	def guardar(self) -> bool:
		"""Guarda el texto (Ctrl+S). ``True`` si se guardó."""
		texto = self.texto.GetValue()
		forzar = False
		while True:
			try:
				memoria.guardar_texto(self.archivo.ruta, texto, self.copias, self.cargado, forzar=forzar)
				break
			except memoria.ArchivoCambiado:
				pregunta = _("«{nombre}» ha cambiado desde que lo abriste: quizá lo ha escrito el agente. Si guardas, "
					"se sustituye por lo que tienes aquí (antes se guarda una copia del que hay ahora). ¿Guardar "
					"igualmente?").format(nombre=self.archivo.ruta.name)
				if confirmar(self, pregunta, _("El archivo ha cambiado"), peligroso=True) != wx.ID_YES:
					return False
				forzar = True
			except memoria.ErrorMemoria as error:
				mostrar_error(self, str(error), _("No se ha guardado"))
				return False
		self.cargado = memoria.cargar_para_editar(self.archivo.ruta)
		self.guardado = True
		avisos.anunciar(_("Guardado"), interrumpir=True)
		return True

	def cerrar(self) -> None:
		"""Cierra; con cambios sin guardar, pregunta si guardarlos (Sí, No o Cancelar)."""
		if self.modificado():
			respuesta = confirmar(self, _("Hay cambios sin guardar en «{nombre}». ¿Guardarlos?").format(
				nombre=self.archivo.ruta.name), _("Cambios sin guardar"), con_cancelar=True)
			if respuesta == wx.ID_CANCEL or respuesta == wx.ID_YES and not self.guardar():
				return
		if self.IsModal():
			self.EndModal(wx.ID_OK if self.guardado else wx.ID_CANCEL)
		else:
			self.Destroy()


class DialogoNuevaMemoria(wx.Dialog):
	"""Pide lo que hace falta para añadir una nota a la memoria de Claude Code de la carpeta."""

	def __init__(self, padre: wx.Window | None, carpeta: Path) -> None:
		titulo = _("Añadir una memoria de Claude Code")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.carpeta = carpeta
		self.ruta: Path | None = None
		self._tipos = list(memoria.TIPOS_MEMORIA)
		raiz = wx.BoxSizer(wx.VERTICAL)
		explicacion = wx.StaticText(self, label=_(
			"Claude Code lee esta memoria cada vez que trabaja en {carpeta}. Escribe una cosa por memoria.").format(carpeta=carpeta))
		explicacion.Wrap(self.FromDIP(560))
		raiz.Add(explicacion, 0, wx.ALL, 6)
		_e, self.titulo = crear_campo(self, _("&Nombre:"), wx.TextCtrl, sizer=raiz,
			ayuda=_("Un nombre corto, por ejemplo «Pruebas con pytest». Con él se forma el nombre del archivo."))
		_e, self.descripcion = crear_campo(self, _("&Descripción (una línea):"), wx.TextCtrl, sizer=raiz,
			ayuda=_("Una frase que diga de qué trata: Claude la lee en el índice de la memoria para decidir si la necesita."))
		_e, self.tipo = crear_campo(self, _("&Tipo:"), wx.Choice, choices=[_(memoria.TIPOS_MEMORIA[t]) for t in self._tipos],
			sizer=raiz, ayuda=_("De qué es la memoria: sobre ti, cómo quieres que trabaje, sobre el proyecto o dónde "
				"encontrar información."))
		self.tipo.SetSelection(self._tipos.index("feedback"))
		_e, self.texto = crear_campo(self, _("Te&xto de la memoria:"), wx.TextCtrl, style=wx.TE_MULTILINE, sizer=raiz,
			ayuda=_("Lo que Claude debe recordar, con frases sencillas."))
		self.texto.SetMinSize(self.FromDIP(wx.Size(560, 140)))
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		preparar_dialogo(self, foco_inicial=self.titulo)
		self.CentreOnParent()

	def _aceptar(self) -> None:
		try:
			self.ruta = memoria.anadir_memoria_claude(self.carpeta, self.titulo.GetValue(), self.descripcion.GetValue(),
				self._tipos[self.tipo.GetSelection()], self.texto.GetValue())
		except FileExistsError:
			mostrar_error(self, _("Ya hay una memoria con ese nombre: elige otro."), _("Memoria"))
			enfocar(self.titulo)
			return
		except ValueError as error:
			mostrar_error(self, str(error), _("Memoria"))
			return
		except (OSError, memoria.ErrorMemoria) as error:
			log.warning("No se pudo añadir la memoria", exc_info=True)
			mostrar_error(self, _("No se pudo añadir la memoria: {error}").format(error=error), _("Memoria"))
			return
		self.EndModal(wx.ID_OK)


class DialogoNuevoSubagente(wx.Dialog):
	"""Crea un subagente personalizado de Claude Code o de Codex (global, nunca sustituye uno que exista)."""

	AGENTES = ((memoria.CLAUDE, "Claude Code"), (memoria.CODEX, "Codex"))

	def __init__(self, padre: wx.Window | None, agente: str = "") -> None:
		titulo = _("Crear un subagente")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.ruta: Path | None = None
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.agente = crear_campo(self, _("A&gente:"), wx.Choice, choices=[n for _i, n in self.AGENTES], sizer=raiz,
			ayuda=_("Para qué agente es el subagente: Claude Code lo guarda en tu carpeta .claude, agents; Codex, en "
				".codex, agents. Sirve en todos tus proyectos."))
		ids = [i for i, _n in self.AGENTES]
		self.agente.SetSelection(ids.index(memoria.grupo_de(agente)) if memoria.grupo_de(agente) in ids else 0)
		_e, self.nombre = crear_campo(self, _("&Nombre (minúsculas y guiones):"), wx.TextCtrl, sizer=raiz,
			ayuda=_("Nombre del subagente, por ejemplo revisor-de-codigo: letras minúsculas sin tildes, números y guiones."))
		_e, self.descripcion = crear_campo(self, _("&Descripción (cuándo usarlo):"), wx.TextCtrl, sizer=raiz,
			ayuda=_("Una frase que diga para qué sirve: el agente la lee para decidir cuándo encargarle trabajo."))
		_e, self.herramientas = crear_campo(self, _("&Herramientas permitidas (opcional, separadas por comas):"), wx.TextCtrl,
			sizer=raiz, ayuda=_("Solo Claude Code: las herramientas que puede usar, como Read, Grep, Glob. Vacío: todas."))
		_e, self.instrucciones = crear_campo(self, _("&Instrucciones:"), wx.TextCtrl, style=wx.TE_MULTILINE, sizer=raiz,
			ayuda=_("Cómo debe trabajar el subagente, con frases sencillas."))
		self.instrucciones.SetMinSize(self.FromDIP(wx.Size(560, 140)))
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.agente.Bind(wx.EVT_CHOICE, lambda _e: self._al_cambiar_agente())
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		self._al_cambiar_agente()
		preparar_dialogo(self, foco_inicial=self.agente)
		self.CentreOnParent()

	def agente_elegido(self) -> str:
		return self.AGENTES[max(self.agente.GetSelection(), 0)][0]

	def _al_cambiar_agente(self) -> None:
		# Codex no tiene lista de herramientas por subagente: el campo se desactiva y su etiqueta pierde la «&».
		habilitar(self.herramientas, self.agente_elegido() == memoria.CLAUDE)

	def _aceptar(self) -> None:
		agente = self.agente_elegido()
		herramientas = self.herramientas.GetValue() if agente == memoria.CLAUDE else ""
		try:
			self.ruta = memoria.crear_subagente(agente, self.nombre.GetValue(), self.descripcion.GetValue(),
				self.instrucciones.GetValue(), herramientas)
		except FileExistsError:
			mostrar_error(self, _("Ya existe un subagente con ese nombre: elige otro. No se ha tocado el que hay."),
				_("Subagente"))
			enfocar(self.nombre)
			return
		except ValueError as error:
			mostrar_error(self, str(error), _("Subagente"))
			return
		except OSError as error:
			log.warning("No se pudo crear el subagente", exc_info=True)
			mostrar_error(self, _("No se pudo crear el subagente: {error}").format(error=error), _("Subagente"))
			return
		self.EndModal(wx.ID_OK)
