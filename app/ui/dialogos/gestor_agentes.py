"""Gestor de agentes: el catálogo de las habilidades (*skills*) de la persona.

Cada agente es una carpeta con un SKILL.md (ver ``app.services.catalogo_agentes``).
La lista tiene Nombre, Categoría, Descripción, Carpeta y Estado («Disponible»
o «No encontrado»), un filtro por categoría y un cuadro de búsqueda. Todas las
acciones están en el menú contextual (tecla Aplicaciones o botón «Acciones») y
con teclas:

- Intro: ver el SKILL.md en el visor de texto.
- F2: editar nombre, descripción, categoría y notas (solo en el catálogo).
- Ctrl+E: abrir el SKILL.md en el editor externo (Preferencias).
- Ctrl+Mayús+E: abrir la carpeta del agente en el Explorador.
- Ctrl+C: copiar la ruta del SKILL.md.
- Supr: quitarlo del catálogo (Intro en la confirmación es «No»); si se creó
  aquí, se pregunta aparte si se borra también su carpeta.
- Ctrl+O: añadir desde una carpeta (busca dentro y deja elegir con casillas).
- Ctrl+I: añadir uno (elegir la carpeta que tiene su SKILL.md).
- Ctrl+N: crear uno nuevo (carpeta y SKILL.md de plantilla).
- F5: refrescar (qué cambió en los SKILL.md y qué carpetas ya no están).
- Ctrl+F: ir al cuadro de búsqueda.
- Exportar e importar el catálogo en JSON: en el menú contextual.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import wx

from app.services import catalogo_agentes as servicio
from app.services.almacen import AgenteCatalogo, Almacen
from app.services.catalogo_agentes import CATEGORIA_EJEMPLOS, CatalogoAgentes, ErrorCatalogo, FichaAgente, ResultadoBusqueda
from app.ui import avisos, visor_ayuda
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.casillas import CheckListBoxAccesible
from app.ui.accesibilidad.dialogos import (
	confirmar,
	crear_botonera,
	enfocar,
	mostrar_error,
	mostrar_mensaje,
	preparar_dialogo,
)
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import copiar_texto, vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Longitud máxima de los campos del catálogo.
MAX_NOMBRE = 120
MAX_DESCRIPCION = 4000
MAX_CATEGORIA = 80
MAX_NOTAS = 8000
#: Caracteres de la descripción que se muestran en una lista de casillas.
RESUMEN_DESCRIPCION = 160


def texto_estado(agente: AgenteCatalogo) -> str:
	"""«Disponible» o «No encontrado» (su carpeta o su SKILL.md ya no están)."""
	return _("Disponible") if servicio.disponible(agente) else _("No encontrado")


def resumen(texto: str, maximo: int = RESUMEN_DESCRIPCION) -> str:
	"""Texto en una línea recortado a ``maximo`` caracteres (con «…»)."""
	texto = servicio.una_linea(texto)
	return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def mostrar_skill(padre: wx.Window | None, nombre: str, archivo: Path | None) -> bool:
	"""Muestra un SKILL.md en el visor de texto. Avisa si no existe o es demasiado grande."""
	if archivo is None or not archivo.is_file():
		mostrar_mensaje(padre, _("No se encuentra el SKILL.md de «{nombre}». Puede que su carpeta se haya movido o borrado; "
			"pulsa F5 en el gestor de agentes para revisar el catálogo.").format(nombre=nombre), _("No encontrado"))
		return False
	try:
		if archivo.stat().st_size > servicio.MAX_TAMANO_SKILL:
			mostrar_mensaje(padre, _("«{archivo}» ocupa más de 1 MB y no se abre aquí. Ábrelo en el editor externo (Control+E).").format(
				archivo=archivo), _("Archivo demasiado grande"))
			return False
		texto = archivo.read_text(encoding="utf-8-sig", errors="replace")
	except OSError as error:
		log.warning("No se pudo leer %s", archivo, exc_info=True)
		mostrar_error(padre, _("No se pudo leer «{ruta}»: {error}").format(ruta=archivo, error=error.strerror or error))
		return False
	visor_ayuda.mostrar_documento(padre, _("{nombre} (SKILL.md)").format(nombre=nombre), texto, forzar_texto=True)
	return True


# ════════════════════════════════════════════════════════════════════════════
# Editar, añadir o crear un agente
# ════════════════════════════════════════════════════════════════════════════

MODO_EDITAR = "editar"
MODO_ANADIR = "anadir"
MODO_CREAR = "crear"


class DialogoEditarAgente(wx.Dialog):
	"""Nombre, descripción, categoría y notas de un agente. Tras ``wx.ID_OK``, ``valores`` tiene lo escrito.

	:param modo: ``MODO_EDITAR`` (uno del catálogo), ``MODO_ANADIR`` (uno leído de
		su SKILL.md, antes de añadirlo) o ``MODO_CREAR`` (uno nuevo: pide además
		la carpeta donde se crea).
	"""

	def __init__(self, padre: wx.Window | None, categorias: list[str], *, modo: str = MODO_EDITAR,
			nombre: str = "", descripcion: str = "", categoria: str = "", notas: str = "", carpeta: str = "") -> None:
		titulos = {MODO_EDITAR: _("Editar el agente «{n}»").format(n=nombre), MODO_ANADIR: _("Añadir el agente «{n}»").format(n=nombre),
			MODO_CREAR: _("Crear un agente nuevo")}
		titulo = titulos.get(modo, titulos[MODO_EDITAR])
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.modo = modo
		self.valores: dict[str, str] = {}

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_nombre = crear_campo(panel, _("&Nombre:"), wx.TextCtrl, sizer=raiz, value=nombre,
			ayuda=_("Nombre con el que ves el agente en Ctrl+IA. Cambiarlo no cambia su SKILL.md."))
		self.campo_nombre.SetMaxLength(MAX_NOMBRE)
		_e, self.campo_descripcion = crear_campo(panel, _("&Descripción:"), wx.TextCtrl, sizer=raiz, value=descripcion,
			style=wx.TE_MULTILINE,
			ayuda=_("Qué hace el agente y cuándo usarlo. Se guarda en una sola línea. En un agente nuevo, va también a su SKILL.md."))
		self.campo_descripcion.SetMaxLength(MAX_DESCRIPCION)
		self.campo_descripcion.SetMinSize(self.FromDIP(wx.Size(520, 110)))
		_e, self.campo_categoria = crear_campo(panel, _("Ca&tegoría:"), wx.ComboBox, sizer=raiz, value=categoria,
			choices=list(categorias), style=wx.CB_DROPDOWN,
			ayuda=_("Para clasificar los agentes. Elige una de las que ya usas con las flechas o escribe una nueva. Vacía: sin categoría."))
		_e, self.campo_notas = crear_campo(panel, _("N&otas:"), wx.TextCtrl, sizer=raiz, value=notas, style=wx.TE_MULTILINE,
			ayuda=_("Lo que quieras apuntar del agente. Solo se guarda en Ctrl+IA."))
		self.campo_notas.SetMaxLength(MAX_NOTAS)
		self.campo_notas.SetMinSize(self.FromDIP(wx.Size(520, 80)))
		self.boton_examinar: wx.Button | None = None
		if modo == MODO_CREAR:
			fila = wx.BoxSizer(wx.HORIZONTAL)
			_e, self.campo_carpeta = crear_campo(panel, _("Ca&rpeta donde se crea:"), wx.TextCtrl, sizer=fila, horizontal=True,
				value=carpeta, ayuda=_("Dentro de esta carpeta se crea otra con el nombre del agente y su SKILL.md. "
					"Por defecto, la carpeta agentes de los datos de Ctrl+IA."))
			self.boton_examinar = wx.Button(panel, label=_("E&xaminar..."))
			self.boton_examinar.SetHelpText(_("Elige la carpeta en una ventana de selección."))
			self.boton_examinar.Bind(wx.EVT_BUTTON, lambda _evento: self._examinar())
			fila.Add(self.boton_examinar, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 6)
			raiz.Add(fila, 0, wx.EXPAND)
		else:
			_e, self.campo_carpeta = crear_campo(panel, _("Ca&rpeta:"), wx.TextCtrl, sizer=raiz, value=carpeta,
				style=wx.TE_READONLY, ayuda=_("Carpeta del agente, donde está su SKILL.md. Solo lectura."))
		raiz.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		ayudas_botones = {
			MODO_EDITAR: _("Guarda los cambios en el catálogo y cierra."),
			MODO_ANADIR: _("Añade el agente al catálogo con estos datos."),
			MODO_CREAR: _("Crea la carpeta del agente con su SKILL.md y lo añade al catálogo."),
		}
		for identificador, ayuda in ((wx.ID_OK, ayudas_botones.get(modo, "")), (wx.ID_CANCEL, _("Cierra sin cambiar nada."))):
			boton = panel.FindWindowById(identificador, panel)
			if boton is not None:
				boton.SetHelpText(ayuda)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		preparar_dialogo(self, foco_inicial=self.campo_nombre)
		self.CentreOnParent()

	def _examinar(self) -> None:
		with wx.DirDialog(self, _("Carpeta donde se crea el agente"), defaultPath=self.campo_carpeta.GetValue()) as dialogo:
			if dialogo.ShowModal() == wx.ID_OK:
				self.campo_carpeta.SetValue(dialogo.GetPath())
		enfocar(self.campo_carpeta)

	def validar(self) -> str | None:
		"""Mensaje de error o ``None`` si todo vale."""
		if not servicio.una_linea(self.campo_nombre.GetValue()):
			return _("Escribe un nombre para el agente.")
		if self.modo == MODO_CREAR:
			if not servicio.una_linea(self.campo_descripcion.GetValue()):
				return _("Escribe una descripción: es lo que lee el agente de IA para saber cuándo usarlo.")
			if not self.campo_carpeta.GetValue().strip().strip('"'):
				return _("Escribe la carpeta donde se crea el agente o pulsa Examinar.")
		return None

	def aceptar(self) -> bool:
		error = self.validar()
		if error is not None:
			mostrar_error(self, error, _("Datos del agente"))
			return False
		self.valores = {
			"nombre": servicio.una_linea(self.campo_nombre.GetValue()),
			"descripcion": servicio.una_linea(self.campo_descripcion.GetValue()),
			"categoria": servicio.una_linea(self.campo_categoria.GetValue()),
			"notas": self.campo_notas.GetValue().strip(),
			"carpeta": self.campo_carpeta.GetValue().strip().strip('"'),
		}
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True


def pedir_datos_agente(padre: wx.Window | None, categorias: list[str], **datos: str) -> dict[str, str] | None:
	"""Abre ``DialogoEditarAgente`` y devuelve ``valores`` o ``None`` si se cancela."""
	dialogo = DialogoEditarAgente(padre, categorias, **datos)
	try:
		if dialogo.ShowModal() != wx.ID_OK or not dialogo.valores:
			return None
		return dict(dialogo.valores)
	finally:
		dialogo.Destroy()


# ════════════════════════════════════════════════════════════════════════════
# Elegir qué agentes encontrados se añaden
# ════════════════════════════════════════════════════════════════════════════

class DialogoAgentesEncontrados(wx.Dialog):
	"""Lista de casillas con los agentes encontrados en una carpeta y la categoría de cada uno.

	Tras ``wx.ID_OK``: ``elegidos`` (``[(ficha, categoría), ...]``) y ``actualizar``.
	"""

	def __init__(self, padre: wx.Window | None, catalogo: CatalogoAgentes, resultado: ResultadoBusqueda,
			raiz: str | Path) -> None:
		titulo = _("Agentes encontrados")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.catalogo = catalogo
		self.fichas: list[FichaAgente] = list(resultado.encontrados)
		self.categorias = [servicio.categoria_propuesta(f, raiz) for f in self.fichas]
		self.ya_estan = [catalogo.en_catalogo(f.carpeta) is not None for f in self.fichas]
		self.elegidos: list[tuple[FichaAgente, str]] = []
		self.actualizar = False

		panel = wx.Panel(self)
		raiz_sizer = wx.BoxSizer(wx.VERTICAL)
		nuevos = self.ya_estan.count(False)
		partes = [_("En «{carpeta}» hay {n} agentes; {nuevos} todavía no están en el catálogo.").format(
			carpeta=raiz, n=len(self.fichas), nuevos=nuevos)]
		if resultado.truncado:
			partes.append(_("La búsqueda se paró antes de terminar porque la carpeta es muy grande: elige una más concreta si falta alguno."))
		if resultado.errores:
			partes.append(_("Hubo {n} carpetas o archivos que no se pudieron leer.").format(n=len(resultado.errores)))
		partes.append(_("Marca con la barra espaciadora los que quieres añadir y revisa su categoría."))
		explicacion = wx.StaticText(panel, label=" ".join(partes))
		explicacion.Wrap(self.FromDIP(620))
		raiz_sizer.Add(explicacion, 0, wx.ALL, 8)

		raiz_sizer.Add(wx.StaticText(panel, label=_("Agentes &encontrados:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = CheckListBoxAccesible(panel, _("Agentes encontrados"), [self._texto(i) for i in range(len(self.fichas))],
			recordar_posicion=False)
		self.lista.SetHelpText(_(
			"Agentes encontrados en la carpeta. La barra espaciadora marca o desmarca; Control+A los marca todos y "
			"Control+Mayúsculas+A los desmarca. Los que ya están en el catálogo salen sin marcar. Debajo puedes cambiar "
			"la categoría del elegido y leer su descripción completa. Tecla Aplicaciones: más acciones."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(620, 220)))
		for indice, ya in enumerate(self.ya_estan):
			if not ya:
				self.lista.Check(indice, True)
		raiz_sizer.Add(self.lista, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
		vincular_menu_contextual(self.lista, self._crear_menu)

		opciones = sorted({*catalogo.categorias(), *(c for c in self.categorias if c)}, key=str.casefold)
		_e, self.campo_categoria = crear_campo(panel, _("Ca&tegoría del agente elegido:"), wx.ComboBox, sizer=raiz_sizer,
			choices=opciones, style=wx.CB_DROPDOWN,
			ayuda=_("Categoría con la que se añade el agente elegido en la lista. Se propone la de su SKILL.md o la de su "
				"carpeta; puedes elegir otra con las flechas o escribirla. Vacía: sin categoría."))
		self.casilla_actualizar = wx.CheckBox(panel, label=_("Act&ualizar la descripción de los que ya están en el catálogo"))
		self.casilla_actualizar.SetHelpText(_(
			"Si marcas un agente que ya está en el catálogo, se pone la descripción de su SKILL.md en lugar de la que tiene."
		))
		raiz_sizer.Add(self.casilla_actualizar, 0, wx.ALL, 8)
		_e, self.detalles = crear_campo(panel, _("Deta&lles:"), wx.TextCtrl, sizer=raiz_sizer,
			style=wx.TE_MULTILINE | wx.TE_READONLY,
			ayuda=_("Descripción completa, carpeta y versión del agente elegido. Solo lectura."))
		self.detalles.SetMinSize(self.FromDIP(wx.Size(620, 100)))
		botonera = crear_botonera(panel, wx.OK | wx.CANCEL)
		raiz_sizer.Add(botonera, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		boton_ok = panel.FindWindowById(wx.ID_OK, panel)
		if boton_ok is not None:
			boton_ok.SetLabel(_("&Añadir los marcados"))
			boton_ok.SetHelpText(_("Añade al catálogo los agentes marcados, con su categoría."))
		boton_cancelar = panel.FindWindowById(wx.ID_CANCEL, panel)
		if boton_cancelar is not None:
			boton_cancelar.SetHelpText(_("Cierra sin añadir nada."))
		panel.SetSizer(raiz_sizer)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		self.lista.Bind(wx.EVT_LISTBOX, self._al_elegir)
		self.campo_categoria.Bind(wx.EVT_TEXT, self._al_cambiar_categoria)
		self.campo_categoria.Bind(wx.EVT_COMBOBOX, self._al_cambiar_categoria)
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		if self.fichas:
			self.lista.SetSelection(0)
			self.mostrar_elegido()
		preparar_dialogo(self, foco_inicial=self.lista)
		self.CentreOnParent()

	def _texto(self, indice: int) -> str:
		ficha = self.fichas[indice]
		texto = f"{ficha.nombre} — {resumen(ficha.descripcion)}"
		categoria = self.categorias[indice]
		if categoria:
			texto += " " + _("(categoría: {c})").format(c=categoria)
		if self.ya_estan[indice]:
			texto += " " + _("(ya en el catálogo)")
		return texto

	def elegido(self) -> int:
		return self.lista.GetSelection()

	def mostrar_elegido(self) -> None:
		"""Pone en «Categoría» y «Detalles» los datos del agente elegido."""
		indice = self.elegido()
		if not 0 <= indice < len(self.fichas):
			self.detalles.SetValue("")
			return
		ficha = self.fichas[indice]
		self.campo_categoria.ChangeValue(self.categorias[indice])
		lineas = [ficha.nombre, "", ficha.descripcion or _("(sin descripción)"), "",
			_("Carpeta: {c}").format(c=ficha.carpeta)]
		if ficha.version:
			lineas.append(_("Versión: {v}").format(v=ficha.version))
		if self.ya_estan[indice]:
			lineas.append(_("Ya está en el catálogo."))
		if ficha.aviso:
			lineas.append(_("Aviso: {a}").format(a=ficha.aviso))
		self.detalles.SetValue("\n".join(lineas))

	def _al_elegir(self, evento: wx.CommandEvent) -> None:
		self.mostrar_elegido()
		evento.Skip()

	def _al_cambiar_categoria(self, evento: wx.CommandEvent) -> None:
		self.poner_categoria(self.campo_categoria.GetValue())
		evento.Skip()

	def poner_categoria(self, categoria: str, indice: int | None = None) -> None:
		"""Cambia la categoría con que se añadirá un agente (por defecto, el elegido)."""
		indice = self.elegido() if indice is None else indice
		if not 0 <= indice < len(self.fichas):
			return
		self.categorias[indice] = servicio.una_linea(categoria)[:MAX_CATEGORIA]
		texto = self._texto(indice)
		if self.lista.GetString(indice) != texto:
			marcado = self.lista.IsChecked(indice)
			self.lista.SetString(indice, texto)
			self.lista.Check(indice, marcado)

	def categoria_a_marcados(self) -> int:
		"""Pone la categoría del elegido a todos los marcados. Devuelve a cuántos."""
		categoria = servicio.una_linea(self.campo_categoria.GetValue())
		marcados = self.lista.indices_marcados()
		for indice in marcados:
			self.poner_categoria(categoria, indice)
		voz.hablar(_("Categoría puesta a {n} agentes").format(n=len(marcados)))
		return len(marcados)

	def ver_skill(self) -> None:
		indice = self.elegido()
		if 0 <= indice < len(self.fichas):
			mostrar_skill(self, self.fichas[indice].nombre, self.fichas[indice].archivo)

	def _crear_menu(self) -> wx.Menu | None:
		menu = wx.Menu()
		hay = 0 <= self.elegido() < len(self.fichas)
		for etiqueta, accion, activa in (
			(_("&Marcar o desmarcar\tEspacio"), lambda: self.lista.alternar(), hay),
			(_("Marcar &todos\tCtrl+A"), lambda: self.lista.marcar_todas(True), bool(self.fichas)),
			(_("&Desmarcar todos\tCtrl+Shift+A"), lambda: self.lista.marcar_todas(False), bool(self.fichas)),
			(_("&Poner su categoría a todos los marcados"), self.categoria_a_marcados, hay),
			(_("&Ver el SKILL.md"), self.ver_skill, hay),
		):
			elemento = menu.Append(wx.ID_ANY, etiqueta)
			elemento.Enable(activa)
			menu.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), elemento)
		return menu

	def aceptar(self) -> bool:
		marcados = self.lista.indices_marcados()
		if not marcados:
			mostrar_mensaje(self, _("No has marcado ningún agente. Márcalos con la barra espaciadora o pulsa Cancelar."),
				_("Ningún agente marcado"))
			return False
		self.elegidos = [(self.fichas[i], self.categorias[i]) for i in marcados]
		self.actualizar = self.casilla_actualizar.GetValue()
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True


# ════════════════════════════════════════════════════════════════════════════
# Gestor
# ════════════════════════════════════════════════════════════════════════════

class DialogoGestorAgentes(wx.Dialog):
	"""El catálogo de agentes: añadir, crear, editar, ver, refrescar y quitar.

	:param editor: programa para editar el SKILL.md (vacío: el de Windows).
	:param carpeta_creados: dónde se crean los agentes nuevos (por defecto ``data/agentes``).
	"""

	def __init__(self, padre: wx.Window | None, almacen: Almacen, editor: str = "",
			carpeta_creados: str | Path | None = None) -> None:
		titulo = _("Gestor de agentes")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.catalogo = CatalogoAgentes(almacen, carpeta_creados)
		self.editor = editor or ""
		self.agentes: list[AgenteCatalogo] = []
		self.cambiado = False

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		explicacion = wx.StaticText(panel, label=_(
			"Tus agentes: cada uno es una carpeta con su archivo SKILL.md. Añádelos desde sus carpetas o crea uno nuevo, "
			"clasifícalos por categorías y elige después cuáles usa cada sesión. La tecla Aplicaciones abre todas las acciones."
		))
		explicacion.Wrap(self.FromDIP(720))
		raiz.Add(explicacion, 0, wx.ALL, 8)

		filtros = wx.BoxSizer(wx.HORIZONTAL)
		_e, self.campo_categoria = crear_campo(panel, _("Ca&tegoría:"), wx.Choice, sizer=filtros, horizontal=True,
			expandir=False, choices=[_("Todas")],
			ayuda=_("Muestra solo los agentes de una categoría. «Todas» los muestra todos."))
		_e, self.campo_buscar = crear_campo(panel, _("&Buscar:"), wx.TextCtrl, sizer=filtros, horizontal=True,
			ayuda=_("Escribe para ver solo los agentes que contienen ese texto en el nombre, la descripción, la categoría, "
				"las notas o la carpeta. No distingue mayúsculas ni acentos. Control+F trae aquí el foco."))
		raiz.Add(filtros, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

		self.etiqueta = wx.StaticText(panel, label=_("&Agentes:"))
		self.lista = ListaAccesible(panel, _("Agentes"), [
			(_("Nombre"), 200), (_("Categoría"), 130), (_("Descripción"), 380), (_("Carpeta"), 300), (_("Estado"), 110),
		])
		self.lista.SetHelpText(_(
			"Agentes del catálogo. Arriba y abajo cambian de agente; izquierda y derecha leen sus columnas. Intro muestra "
			"su SKILL.md; F2 edita sus datos; Control+E abre el SKILL.md en el editor; Control+Mayúsculas+E abre su carpeta; "
			"Supr lo quita del catálogo; Control+O añade los de una carpeta; Control+I añade uno; Control+N crea uno nuevo; "
			"F5 revisa si han cambiado o desaparecido. Tecla Aplicaciones: todas las acciones."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(760, 300)))
		raiz.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_acciones = wx.Button(panel, label=_("Acci&ones..."))
		self.boton_acciones.SetHelpText(_("Abre el menú con todas las acciones del gestor (el mismo que la tecla Aplicaciones en la lista)."))
		self.boton_acciones.Bind(wx.EVT_BUTTON, lambda _evento: self._menu_desde_boton())
		fila.Add(self.boton_acciones, 0)
		fila.AddStretchSpacer()
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra el gestor de agentes. También con Escape."))
		fila.Add(self.boton_cerrar, 0)
		raiz.Add(fila, 0, wx.EXPAND | wx.ALL, 8)
		panel.SetSizer(raiz)

		vincular_menu_contextual(self.lista, self._crear_menu)
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.ver_skill())
		self.campo_categoria.Bind(wx.EVT_CHOICE, lambda _evento: self.recargar())
		self.campo_buscar.Bind(wx.EVT_TEXT, lambda _evento: self.recargar())
		self.recargar()
		preparar_dialogo(self, foco_inicial=self.lista)
		# Después de preparar_dialogo: este manejador se atiende antes que el suyo.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)

		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		if not self.agentes:
			wx.CallAfter(voz.hablar, _("El catálogo está vacío. Pulsa Control+O para añadir los agentes de una carpeta o Control+N para crear uno."))

	# ── Datos ─────────────────────────────────────────────────────────────────

	def categoria_filtro(self) -> str | None:
		"""Categoría elegida en el filtro: ``None`` = todas; ``""`` = sin categoría."""
		indice = self.campo_categoria.GetSelection()
		if indice <= 0:
			return None
		if indice == 1:
			return ""
		return self.campo_categoria.GetString(indice)

	def _rellenar_categorias(self) -> None:
		anterior = self.categoria_filtro()
		opciones = [_("Todas"), _("Sin categoría"), *self.catalogo.categorias()]
		if list(self.campo_categoria.GetItems()) != opciones:
			self.campo_categoria.SetItems(opciones)
		if anterior is None:
			self.campo_categoria.SetSelection(0)
		elif anterior == "":
			self.campo_categoria.SetSelection(1)
		else:
			posicion = next((i for i, o in enumerate(opciones) if i > 1 and o.casefold() == anterior.casefold()), 0)
			self.campo_categoria.SetSelection(posicion)

	def recargar(self, seleccionar_id: int | None = None) -> None:
		"""Vuelve a leer el catálogo con los filtros; deja elegido ``seleccionar_id`` o la fila de antes."""
		previo = self.actual()
		fila_previa = self.lista.fila_actual()
		self._rellenar_categorias()
		self.agentes = self.catalogo.listar(self.categoria_filtro(), self.campo_buscar.GetValue())
		filas = [
			[a.nombre, a.categoria, a.descripcion, a.carpeta, texto_estado(a)]
			for a in self.agentes
		]
		self.lista.reemplazar_filas(filas, conservar_foco=False)
		total = len(self.agentes)
		self.etiqueta.SetLabel(_("&Agentes ({n}):").format(n=total))
		self.lista.SetName(_("Agentes ({n})").format(n=total))
		buscado = seleccionar_id if seleccionar_id is not None else (previo.id if previo is not None else None)
		destino = next((i for i, a in enumerate(self.agentes) if a.id == buscado), None) if buscado is not None else None
		if destino is None and fila_previa != wx.NOT_FOUND and self.agentes:
			destino = min(fila_previa, total - 1)
		if destino is None and self.agentes:
			destino = 0
		if destino is not None:
			self.lista.enfocar_fila(destino, anunciar=False)

	def actual(self) -> AgenteCatalogo | None:
		fila = self.lista.fila_actual()
		return self.agentes[fila] if 0 <= fila < len(self.agentes) else None

	def _sin_seleccion(self) -> None:
		voz.hablar(_("No hay ningún agente elegido") if self.agentes else _("No hay agentes en la lista"))

	def _enfocar_lista(self) -> None:
		if self.lista.IsShownOnScreen():
			self.lista.SetFocus()

	def _elegir_carpeta(self, titulo: str) -> str | None:
		with wx.DirDialog(self, titulo, style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dialogo:
			if dialogo.ShowModal() != wx.ID_OK:
				return None
			return dialogo.GetPath()

	# ── Acciones sobre el agente elegido ─────────────────────────────────────────

	def ver_skill(self) -> bool:
		"""Muestra el SKILL.md del agente elegido en el visor de texto (Intro)."""
		agente = self.actual()
		if agente is None:
			self._sin_seleccion()
			return False
		return mostrar_skill(self, agente.nombre, servicio.ruta_skill(agente.carpeta))

	def editar(self) -> bool:
		"""Edita nombre, descripción, categoría y notas del agente elegido (F2)."""
		agente = self.actual()
		if agente is None:
			self._sin_seleccion()
			return False
		valores = pedir_datos_agente(self, self.catalogo.categorias(), modo=MODO_EDITAR, nombre=agente.nombre,
			descripcion=agente.descripcion, categoria=agente.categoria, notas=agente.notas, carpeta=agente.carpeta)
		if valores is None:
			return False
		try:
			self.catalogo.editar(agente.id, nombre=valores["nombre"], descripcion=valores["descripcion"],
				categoria=valores["categoria"], notas=valores["notas"])
		except ErrorCatalogo as error:
			mostrar_error(self, str(error))
			return False
		self.cambiado = True
		self.recargar(agente.id)
		self._enfocar_lista()
		avisos.anunciar(_("Agente guardado"), interrumpir=True)
		return True

	def editar_skill(self) -> bool:
		"""Abre el SKILL.md del agente elegido en el editor externo (Ctrl+E)."""
		from app.ui.dialogos.memoria import abrir_en_editor

		agente = self.actual()
		if agente is None:
			self._sin_seleccion()
			return False
		archivo = servicio.ruta_skill(agente.carpeta)
		if archivo is None:
			return mostrar_skill(self, agente.nombre, None)
		if abrir_en_editor(archivo, self.editor):
			voz.hablar(_("Abriendo el editor"))
			return True
		mostrar_error(self, _("No se pudo abrir el editor con «{ruta}».").format(ruta=archivo))
		return False

	def abrir_carpeta(self) -> bool:
		"""Abre la carpeta del agente en el Explorador (Ctrl+Mayús+E)."""
		from app.ui.dialogos.memoria import abrir_carpeta_de

		agente = self.actual()
		if agente is None:
			self._sin_seleccion()
			return False
		archivo = servicio.ruta_skill(agente.carpeta)
		if abrir_carpeta_de(archivo or Path(agente.carpeta)):
			return True
		mostrar_error(self, _("No se pudo abrir la carpeta «{ruta}».").format(ruta=agente.carpeta))
		return False

	def copiar_ruta(self) -> bool:
		"""Copia la ruta del SKILL.md (o de la carpeta, si no está) al portapapeles (Ctrl+C)."""
		agente = self.actual()
		if agente is None:
			self._sin_seleccion()
			return False
		archivo = servicio.ruta_skill(agente.carpeta)
		if copiar_texto(str(archivo or agente.carpeta)):
			voz.hablar(_("Ruta copiada"))
			return True
		voz.hablar(_("No se pudo usar el portapapeles."))
		return False

	def eliminar(self) -> bool:
		"""Quita el agente del catálogo tras confirmarlo (Supr); si se creó aquí, ofrece borrar su carpeta."""
		agente = self.actual()
		if agente is None:
			self._sin_seleccion()
			return False
		pregunta = _("¿Quitar «{nombre}» del catálogo? Su carpeta y su SKILL.md no se borran.").format(nombre=agente.nombre)
		if agente.origen == servicio.ORIGEN_CREADO:
			pregunta = _("¿Quitar «{nombre}» del catálogo? Después se te preguntará si quieres borrar también su carpeta.").format(
				nombre=agente.nombre)
		if confirmar(self, pregunta, _("Quitar agente"), peligroso=True) != wx.ID_YES:
			return False
		borrar_carpeta = False
		if agente.origen == servicio.ORIGEN_CREADO and not self.catalogo.se_puede_borrar_carpeta(agente):
			borrar_carpeta = confirmar(
				self,
				_("«{nombre}» se creó desde Ctrl+IA. ¿Borrar también su carpeta «{carpeta}» con su SKILL.md y todo lo que "
					"contenga? No se puede deshacer.").format(nombre=agente.nombre, carpeta=agente.carpeta),
				_("Borrar la carpeta del agente"), peligroso=True,
			) == wx.ID_YES
		try:
			borrada = self.catalogo.eliminar(agente.id, borrar_carpeta=borrar_carpeta)
		except ErrorCatalogo as error:
			mostrar_error(self, _("No se pudo borrar la carpeta: {error} El agente sigue en el catálogo.").format(error=error))
			return False
		self.cambiado = True
		self.recargar()
		self._enfocar_lista()
		avisos.anunciar(_("Agente quitado y carpeta borrada") if borrada else _("Agente quitado del catálogo"), interrumpir=True)
		return True

	# ── Añadir y crear ───────────────────────────────────────────────────────────

	def buscar(self, carpeta: str | Path) -> ResultadoBusqueda:
		"""Busca agentes en ``carpeta`` (con límites, así que tarda poco) mostrando el cursor de espera."""
		with wx.BusyCursor():
			return servicio.buscar_agentes(carpeta)

	def anadir_desde_carpeta(self, carpeta: str | Path | None = None) -> int:
		"""Busca agentes dentro de una carpeta y deja elegir cuáles añadir (Ctrl+O). Devuelve cuántos se añadieron."""
		if carpeta is None:
			carpeta = self._elegir_carpeta(_("Carpeta donde buscar agentes"))
			if carpeta is None:
				return 0
		voz.hablar(_("Buscando agentes..."))
		resultado = self.buscar(carpeta)
		if not resultado.encontrados:
			mensaje = _("No se ha encontrado ninguna carpeta con un archivo SKILL.md dentro de «{carpeta}».").format(carpeta=carpeta)
			if resultado.errores:
				mensaje += "\n\n" + _("Problemas al buscar:") + "\n" + "\n".join(resultado.errores[:10])
			mostrar_mensaje(self, mensaje, _("Sin agentes"))
			return 0
		dialogo = DialogoAgentesEncontrados(self, self.catalogo, resultado, carpeta)
		try:
			if dialogo.ShowModal() != wx.ID_OK or not dialogo.elegidos:
				return 0
			elegidos, actualizar = list(dialogo.elegidos), dialogo.actualizar
		finally:
			dialogo.Destroy()
		resultados = self.catalogo.importar_varios(elegidos, actualizar_descripcion=actualizar)
		nuevos = sum(1 for r in resultados if r.nuevo)
		actualizados = sum(1 for r in resultados if r.actualizado)
		self.cambiado = self.cambiado or bool(nuevos or actualizados)
		self.recargar(next((r.id for r in resultados if r.nuevo), None))
		self._enfocar_lista()
		texto = _("{n} agentes añadidos").format(n=nuevos)
		if actualizados:
			texto += ", " + _("{n} actualizados").format(n=actualizados)
		avisos.anunciar(texto, interrumpir=True)
		return nuevos

	def anadir_uno(self, carpeta: str | Path | None = None) -> int | None:
		"""Añade el agente de una carpeta con SKILL.md (Ctrl+I). Devuelve su id o ``None``."""
		if carpeta is None:
			carpeta = self._elegir_carpeta(_("Carpeta del agente (la que tiene su SKILL.md)"))
			if carpeta is None:
				return None
		try:
			ficha = servicio.leer_skill(carpeta)
		except ErrorCatalogo as error:
			mostrar_error(self, str(error), _("Añadir agente"))
			return None
		existente = self.catalogo.en_catalogo(ficha.carpeta)
		if existente is not None:
			if ficha.descripcion and ficha.descripcion != existente.descripcion and confirmar(
				self,
				_("«{nombre}» ya está en el catálogo. Su SKILL.md tiene otra descripción. ¿Poner la del SKILL.md?").format(
					nombre=existente.nombre),
				_("Agente repetido"),
			) == wx.ID_YES:
				self.catalogo.importar(ficha, actualizar_descripcion=True)
				self.cambiado = True
				avisos.anunciar(_("Descripción actualizada"), interrumpir=True)
			else:
				voz.hablar(_("Ese agente ya está en el catálogo"))
			self._quitar_filtros()
			self.recargar(existente.id)
			self._enfocar_lista()
			return existente.id
		valores = pedir_datos_agente(self, self.catalogo.categorias(), modo=MODO_ANADIR, nombre=ficha.nombre,
			descripcion=ficha.descripcion, categoria=servicio.categoria_propuesta(ficha), carpeta=str(ficha.carpeta))
		if valores is None:
			return None
		resultado = self.catalogo.importar(ficha, valores["categoria"])
		self.catalogo.editar(resultado.id, nombre=valores["nombre"], descripcion=valores["descripcion"], notas=valores["notas"])
		self.cambiado = True
		self._quitar_filtros()
		self.recargar(resultado.id)
		self._enfocar_lista()
		avisos.anunciar(_("Agente «{nombre}» añadido").format(nombre=valores["nombre"]), interrumpir=True)
		return resultado.id

	def crear_nuevo(self) -> int | None:
		"""Crea un agente nuevo con su carpeta y un SKILL.md de plantilla (Ctrl+N). Devuelve su id o ``None``."""
		valores = pedir_datos_agente(self, self.catalogo.categorias(), modo=MODO_CREAR,
			categoria=self.categoria_filtro() or "", carpeta=str(self.catalogo.carpeta_creados))
		if valores is None:
			return None
		try:
			identificador = self.catalogo.crear(valores["nombre"], valores["descripcion"], valores["categoria"],
				valores["notas"], valores["carpeta"])
		except ErrorCatalogo as error:
			mostrar_error(self, str(error), _("Crear agente"))
			return None
		self.cambiado = True
		self._quitar_filtros()
		self.recargar(identificador)
		self._enfocar_lista()
		agente = self.catalogo.obtener(identificador)
		avisos.anunciar(_("Agente «{nombre}» creado").format(nombre=valores["nombre"]), interrumpir=True)
		if agente is not None and confirmar(
			self,
			_("Se ha creado «{carpeta}» con un SKILL.md de plantilla. ¿Abrirlo ahora en el editor para escribir sus instrucciones?").format(
				carpeta=agente.carpeta),
			_("Agente creado"),
		) == wx.ID_YES:
			self.editar_skill()
		return identificador

	def _quitar_filtros(self) -> None:
		"""Quita el filtro y la búsqueda para que se vea el agente recién añadido."""
		if self.campo_buscar.GetValue():
			self.campo_buscar.ChangeValue("")
		self.campo_categoria.SetSelection(0)

	# ── Refrescar, exportar, importar ───────────────────────────────────────────

	def refrescar(self) -> servicio.ResultadoRefresco:
		"""Vuelve a leer los SKILL.md (F5): marca los no encontrados y ofrece las descripciones nuevas."""
		with wx.BusyCursor():
			resultado = self.catalogo.refrescar()
		if resultado.cambiados:
			nombres = "\n".join(f"- {c.nombre}" for c in resultado.cambiados[:20])
			if len(resultado.cambiados) > 20:
				nombres += "\n" + _("y {n} más").format(n=len(resultado.cambiados) - 20)
			respuesta = confirmar(
				self,
				_("Estos agentes tienen en su SKILL.md una descripción distinta de la del catálogo:\n{nombres}\n\n"
					"¿Poner en el catálogo la descripción de su SKILL.md? Si dices que no, se queda la que tienen y "
					"no se vuelve a preguntar hasta que el SKILL.md cambie otra vez.").format(nombres=nombres),
				_("Descripciones cambiadas"),
			)
			if respuesta == wx.ID_YES:
				self.catalogo.aplicar_cambios(resultado.cambiados)
			else:
				self.catalogo.descartar_cambios(resultado.cambiados)
			self.cambiado = True
		if resultado.versiones:
			self.cambiado = True
		self.recargar()
		texto = _("Catálogo revisado: {n} agentes").format(n=resultado.revisados)
		if resultado.no_encontrados:
			texto += ", " + _("{n} no encontrados").format(n=len(resultado.no_encontrados))
		if resultado.cambiados:
			texto += ", " + _("{n} con descripción nueva").format(n=len(resultado.cambiados))
		avisos.anunciar(texto, interrumpir=True)
		return resultado

	def exportar(self, ruta: str | Path | None = None) -> int | None:
		"""Guarda el catálogo en un archivo JSON (sin los SKILL.md). Devuelve cuántos agentes."""
		if ruta is None:
			with wx.FileDialog(self, _("Exportar el catálogo de agentes"), defaultFile="agentes-ctrlia.json",
					wildcard=_("Catálogo de agentes (*.json)|*.json"), style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialogo:
				if dialogo.ShowModal() != wx.ID_OK:
					return None
				ruta = dialogo.GetPath()
		try:
			total = self.catalogo.exportar_json(ruta)
		except ErrorCatalogo as error:
			mostrar_error(self, str(error), _("Exportar el catálogo"))
			return None
		avisos.anunciar(_("{n} agentes exportados").format(n=total), interrumpir=True)
		return total

	def importar_catalogo(self, ruta: str | Path | None = None, actualizar: bool | None = None) -> servicio.ResultadoJson | None:
		"""Añade los agentes de un catálogo exportado (JSON)."""
		if ruta is None:
			with wx.FileDialog(self, _("Importar un catálogo de agentes"), wildcard=_("Catálogo de agentes (*.json)|*.json"),
					style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialogo:
				if dialogo.ShowModal() != wx.ID_OK:
					return None
				ruta = dialogo.GetPath()
		if actualizar is None:
			respuesta = confirmar(
				self, _("Si un agente del archivo ya está en el catálogo (misma carpeta), ¿se cambian sus datos por los del archivo?"),
				_("Importar un catálogo"), con_cancelar=True, peligroso=True,
			)
			if respuesta == wx.ID_CANCEL:
				return None
			actualizar = respuesta == wx.ID_YES
		try:
			resultado = self.catalogo.importar_json(ruta, actualizar=actualizar)
		except ErrorCatalogo as error:
			mostrar_error(self, str(error), _("Importar un catálogo"))
			return None
		self.cambiado = True
		self._quitar_filtros()
		self.recargar()
		texto = _("{n} agentes añadidos").format(n=resultado.nuevos)
		if resultado.actualizados:
			texto += ", " + _("{n} actualizados").format(n=resultado.actualizados)
		if resultado.omitidos:
			texto += ", " + _("{n} ya estaban").format(n=resultado.omitidos)
		avisos.anunciar(texto, interrumpir=True)
		return resultado

	# ── Menú y teclas ─────────────────────────────────────────────────────────

	def _crear_menu(self) -> wx.Menu:
		menu = wx.Menu()
		hay = self.actual() is not None

		def anadir(etiqueta: str, accion: Callable[[], object], activa: bool = True) -> None:
			elemento = menu.Append(wx.ID_ANY, etiqueta)
			elemento.Enable(activa)
			menu.Bind(wx.EVT_MENU, lambda _e: accion(), elemento)

		anadir(_("&Ver el SKILL.md\tIntro"), self.ver_skill, hay)
		anadir(_("&Editar...\tF2"), self.editar, hay)
		anadir(_("Editar el SKILL.md en el edi&tor externo\tCtrl+E"), self.editar_skill, hay)
		anadir(_("Abrir la &carpeta\tCtrl+Shift+E"), self.abrir_carpeta, hay)
		anadir(_("Co&piar la ruta del SKILL.md\tCtrl+C"), self.copiar_ruta, hay)
		anadir(_("Qu&itar del catálogo...\tSupr"), self.eliminar, hay)
		menu.AppendSeparator()
		anadir(_("Añadir &desde una carpeta...\tCtrl+O"), self.anadir_desde_carpeta)
		anadir(_("&Añadir uno (elegir su carpeta)...\tCtrl+I"), self.anadir_uno)
		anadir(_("C&rear uno nuevo...\tCtrl+N"), self.crear_nuevo)
		anadir(_("Restaurar los agentes de e&jemplo"), self.restaurar_ejemplos)
		menu.AppendSeparator()
		anadir(_("Re&frescar\tF5"), self.refrescar)
		anadir(_("E&xportar el catálogo..."), self.exportar, bool(self.catalogo.almacen.agentes_catalogo()))
		anadir(_("I&mportar un catálogo..."), self.importar_catalogo)
		return menu

	def restaurar_ejemplos(self) -> int:
		"""Vuelve a añadir los agentes de ejemplo de Ctrl+IA que se hayan quitado del catálogo."""
		anadidos = self.catalogo.cargar_ejemplos()
		self.recargar()
		if anadidos:
			avisos.anunciar(_("Se han vuelto a añadir {n} agentes de ejemplo, en la categoría «{c}».").format(
				n=anadidos, c=CATEGORIA_EJEMPLOS), interrumpir=True)
		else:
			avisos.anunciar(_("Ya tienes todos los agentes de ejemplo en el catálogo."), interrumpir=True)
		return anadidos

	def _menu_desde_boton(self) -> None:
		menu = self._crear_menu()
		self.boton_acciones.PopupMenu(menu, wx.Point(0, self.boton_acciones.GetSize().height))
		menu.Destroy()

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		# atajo: Ctrl+F ir al cuadro de búsqueda
		# Las demás teclas son las del menú contextual (``_crear_menu``).
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		en_lista = wx.Window.FindFocus() is self.lista
		acciones: dict[tuple[int, int], Callable[[], object]] = {
			(wx.MOD_NONE, wx.WXK_F2): self.editar,
			(wx.MOD_NONE, wx.WXK_F5): self.refrescar,
			(wx.MOD_CONTROL, ord("N")): self.crear_nuevo,
			(wx.MOD_CONTROL, ord("O")): self.anadir_desde_carpeta,
			(wx.MOD_CONTROL, ord("I")): self.anadir_uno,
			(wx.MOD_CONTROL, ord("E")): self.editar_skill,
			(wx.MOD_CONTROL | wx.MOD_SHIFT, ord("E")): self.abrir_carpeta,
			(wx.MOD_CONTROL, ord("F")): lambda: enfocar(self.campo_buscar),
		}
		if en_lista:
			acciones[(wx.MOD_NONE, wx.WXK_DELETE)] = self.eliminar
			acciones[(wx.MOD_NONE, wx.WXK_NUMPAD_DELETE)] = self.eliminar
			acciones[(wx.MOD_CONTROL, ord("C"))] = self.copiar_ruta
		accion = acciones.get((mods, tecla))
		if accion is not None:
			accion()
			return
		evento.Skip()


def abrir_gestor(padre: wx.Window | None, almacen: Almacen, editor: str = "") -> bool:
	"""Abre el gestor de agentes (modal). Devuelve ``True`` si cambió algo del catálogo."""
	dialogo = DialogoGestorAgentes(padre, almacen, editor)
	try:
		dialogo.ShowModal()
		return dialogo.cambiado
	finally:
		dialogo.Destroy()
