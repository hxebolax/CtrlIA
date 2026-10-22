"""Reglas de permisos de Antigravity CLI (``permissions.allow`` de su ``settings.json``).

Desde Ctrl+IA, Antigravity no pregunta antes de ejecutar una orden: lo que no
está permitido por una regla se deniega en silencio (Ctrl+IA lo avisa después).
Este diálogo lista las reglas permitidas y deja añadir y quitar reglas con
las funciones de :mod:`app.services.agentes.antigravity`, que conservan el
resto del archivo y lo escriben de forma atómica.

Si el archivo existe pero no se entiende (JSON dañado, formato inesperado),
se muestra el error y la lista queda vacía y deshabilitada: el archivo NO se
toca.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import wx

from app.services.agentes import antigravity
from app.services.agentes.antigravity import ErrorAjustesAntigravity
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
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TipoRegla:
	"""Una clase de regla que se puede añadir desde el diálogo."""

	id: str
	nombre: str
	etiqueta: str
	explicacion: str


TIPOS_REGLA: tuple[TipoRegla, ...] = (
	TipoRegla(
		"orden",
		"Ejecutar órdenes que empiezan por…",
		"&Principio de la orden:",
		"Permite ejecutar cualquier orden de terminal que empiece por lo que escribas. "
		"Ejemplo: «git» permite git status, git diff, git commit…; «npm run test» permite solo esa orden "
		"y sus variantes. Se guarda como command(git).",
	),
	TipoRegla(
		"regex",
		"Ejecutar órdenes que cumplen una expresión regular…",
		"&Expresión regular:",
		"Para usuarios avanzados: permite las órdenes que cumplan la expresión regular que escribas. "
		"Ejemplo: «^(git|npm) .*» permite cualquier orden de git o de npm. Se guarda como command(regex:^(git|npm) .*).",
	),
	TipoRegla(
		"escritura",
		"Escribir en la carpeta o archivo…",
		"Carpeta o archi&vo:",
		"Permite a Antigravity crear y modificar archivos dentro de la carpeta o en el archivo que escribas "
		"(ruta completa). Ejemplo: «C:\\Proyectos\\mi-web». Se guarda como write_file(C:\\Proyectos\\mi-web).",
	),
	TipoRegla(
		"mcp",
		"Usar las herramientas de un servidor MCP…",
		"&Servidor MCP (o servidor/herramienta):",
		"Permite a Antigravity usar las herramientas externas de un servidor MCP sin preguntar. Escribe solo el "
		"nombre del servidor para todas sus herramientas (por ejemplo «github», que se guarda como mcp(github/*)), "
		"o servidor/herramienta para una sola (por ejemplo «github/create_issue»).",
	),
	TipoRegla(
		"manual",
		"Regla escrita a mano",
		"&Regla completa:",
		"Escribe la regla exactamente como la entiende Antigravity CLI; se guarda tal cual. "
		"Ejemplo: «command(python -m pytest)». Úsalo solo si sabes qué formato espera.",
	),
)

_RE_REGLA = re.compile(r"([A-Za-z_]+)\((.*)\)", re.DOTALL)


def regla_de(tipo: str, valor: str) -> str:
	"""Regla de Antigravity para el tipo y el valor escritos. Lanza ``ValueError`` si no es válida."""
	valor = (valor or "").strip()
	if not valor:
		raise ValueError(_("Escribe el valor de la regla."))
	if "\n" in valor or "\r" in valor:
		raise ValueError(_("La regla debe ocupar una sola línea."))
	if tipo == "orden":
		return f"command({valor})"
	if tipo == "regex":
		try:
			re.compile(valor)
		except re.error as exc:
			raise ValueError(_("La expresión regular no es válida: {error}").format(error=exc)) from exc
		return f"command(regex:{valor})"
	if tipo == "escritura":
		return f"write_file({valor})"
	if tipo == "mcp":
		servidor, _barra, herramienta = valor.partition("/")
		servidor, herramienta = servidor.strip(), herramienta.strip() or "*"
		if not servidor or any(c in valor for c in "()"):
			raise ValueError(_("Escribe el nombre del servidor MCP, o servidor/herramienta."))
		return f"mcp({servidor}/{herramienta})"
	if tipo == "manual":
		return valor
	raise ValueError(_("Tipo de regla desconocido."))


def describir_regla(regla: str) -> str:
	"""Qué permite una regla, en español («Ejecutar órdenes que empiezan por «git»»)."""
	coincidencia = _RE_REGLA.fullmatch((regla or "").strip())
	if not coincidencia:
		return _("Regla escrita a mano")
	funcion, argumento = coincidencia.group(1).lower(), coincidencia.group(2).strip()
	if funcion == "command":
		if argumento.lower().startswith("regex:"):
			return _("Ejecutar órdenes que cumplen la expresión regular «{valor}»").format(valor=argumento[6:].strip())
		return _("Ejecutar órdenes que empiezan por «{valor}»").format(valor=argumento)
	if funcion in ("write_file", "edit_file"):
		return _("Escribir en «{valor}»").format(valor=argumento)
	if funcion == "mcp":
		servidor, _barra, herramienta = argumento.partition("/")
		if argumento == "*":
			return _("Usar las herramientas de todos los servidores MCP")
		if herramienta in ("", "*"):
			return _("Usar todas las herramientas del servidor MCP «{s}»").format(s=servidor)
		return _("Usar la herramienta «{h}» del servidor MCP «{s}»").format(h=herramienta, s=servidor)
	return _("Usar {herramienta} con «{valor}»").format(herramienta=funcion, valor=argumento)


class DialogoNuevaRegla(wx.Dialog):
	"""Pide el tipo y el valor de una regla nueva; el resultado queda en :meth:`regla`."""

	def __init__(self, padre: wx.Window | None) -> None:
		super().__init__(padre, title=_("Añadir regla de permiso"), name=_("Añadir regla de permiso"))
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_etiqueta, self.tipo = crear_campo(
			panel, _("&Tipo de regla:"), wx.Choice, choices=[_(t.nombre) for t in TIPOS_REGLA],
			ayuda=_(
				"Qué quieres permitir. «Ejecutar órdenes que empiezan por» es lo habitual (por ejemplo, git o npm). "
				"«Expresión regular» es para usuarios avanzados. «Escribir en la carpeta o archivo» deja modificar "
				"archivos en esa ruta. «Servidor MCP» deja usar sus herramientas externas. «Regla escrita a mano» "
				"guarda el texto tal cual."
			),
			sizer=raiz,
		)
		self.tipo.SetSelection(0)
		self.etiqueta_valor, self.valor = crear_campo(panel, _(TIPOS_REGLA[0].etiqueta), wx.TextCtrl, sizer=raiz)
		self.valor.SetMinSize(self.FromDIP(wx.Size(420, -1)))
		_etiqueta, self.explicacion = crear_campo(
			panel, _("E&xplicación y ejemplo:"), wx.TextCtrl, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
			ayuda=_("Qué permite el tipo de regla elegido, con un ejemplo. Es de solo lectura."), sizer=raiz,
		)
		self.explicacion.SetMinSize(self.FromDIP(wx.Size(420, 110)))
		raiz.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		aceptar = panel.FindWindow(wx.ID_OK)
		if aceptar is not None:
			aceptar.SetHelpText(_("Guarda la regla nueva en los ajustes de Antigravity."))
		cancelar = panel.FindWindow(wx.ID_CANCEL)
		if cancelar is not None:
			cancelar.SetHelpText(_("Cierra sin añadir ninguna regla. También con Escape."))
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.tipo.Bind(wx.EVT_CHOICE, lambda _e: self.al_cambiar_tipo())
		self.Bind(wx.EVT_BUTTON, self._al_aceptar, id=wx.ID_OK)
		self.al_cambiar_tipo()
		preparar_dialogo(self, foco_inicial=self.tipo)
		panel.Layout()
		self.CentreOnParent()

	def tipo_actual(self) -> TipoRegla:
		"""Tipo de regla elegido en el desplegable."""
		indice = self.tipo.GetSelection()
		return TIPOS_REGLA[indice if 0 <= indice < len(TIPOS_REGLA) else 0]

	def al_cambiar_tipo(self) -> None:
		"""Adapta la etiqueta, la ayuda y la explicación al tipo elegido."""
		tipo = self.tipo_actual()
		# SetLabel cambia también el nombre que el lector da al campo.
		self.etiqueta_valor.SetLabel(_(tipo.etiqueta))
		self.valor.SetHelpText(_(tipo.explicacion))
		self.explicacion.SetValue(_(tipo.explicacion))
		self.explicacion.SetInsertionPoint(0)

	def regla(self) -> str:
		"""Regla que se guardaría. Lanza ``ValueError`` si no es válida."""
		return regla_de(self.tipo_actual().id, self.valor.GetValue())

	def _al_aceptar(self, evento: wx.CommandEvent) -> None:
		try:
			self.regla()
		except ValueError as exc:
			mostrar_error(self, str(exc), _("Regla no válida"))
			enfocar(self.valor)
			return
		self.EndModal(wx.ID_OK)


class DialogoReglasAntigravity(wx.Dialog):
	"""Lista, añade y quita las reglas permitidas de Antigravity CLI.

	:param proveedor: ``ProveedorAntigravity`` cuyos métodos ``leer_reglas``,
		``anadir_regla`` y ``quitar_regla`` se usan (así se respeta su ruta de
		ajustes). Si es ``None`` se usan las funciones del módulo.
	:param ruta_ajustes: ``settings.json`` alternativo (solo sin proveedor).
	"""

	def __init__(self, padre: wx.Window | None, proveedor: Any = None, ruta_ajustes: Path | None = None) -> None:
		super().__init__(
			padre, title=_("Reglas de permisos de Antigravity"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
			name=_("Reglas de permisos de Antigravity"),
		)
		self.proveedor = proveedor if proveedor is not None and hasattr(proveedor, "leer_reglas") else None
		self.ruta_ajustes = Path(ruta_ajustes) if ruta_ajustes is not None else None
		self.reglas: list[str] = []
		self.error = ""

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		explicacion = wx.StaticText(panel, label=_(
			"Desde Ctrl+IA, Antigravity no te pregunta antes de actuar: lo que no está permitido por una de "
			"estas reglas se deniega en silencio y Ctrl+IA te lo avisa después. Añade aquí las órdenes que "
			"quieres que pueda ejecutar sin preguntar, por ejemplo git o npm."
		))
		explicacion.Wrap(self.FromDIP(560))
		raiz.Add(explicacion, 0, wx.ALL, 8)

		self.etiqueta_lista = wx.StaticText(panel, label=_("&Reglas permitidas:"))
		self.lista = ListaAccesible(panel, _("Reglas permitidas"), columnas=[(_("Qué permite"), 330), (_("Regla"), 250)])
		self.lista.SetHelpText(_(
			"Reglas que Antigravity aplica sin preguntar. Arriba y abajo cambian de regla; izquierda y derecha "
			"leen sus columnas. Suprimir quita la regla seleccionada, con confirmación."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(600, 220)))
		raiz.Add(self.etiqueta_lista, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_anadir = wx.Button(panel, label=_("&Añadir…"))
		self.boton_anadir.SetHelpText(_("Añade una regla nueva: eliges qué tipo de permiso das y escribes el valor."))
		self.boton_quitar = wx.Button(panel, label=_("&Quitar"))
		self.boton_quitar.SetHelpText(_("Quita la regla seleccionada, tras pedir confirmación. También con Suprimir."))
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra esta ventana. Los cambios ya están guardados. También con Escape."))
		fila.Add(self.boton_anadir, 0, wx.RIGHT, 6)
		fila.Add(self.boton_quitar, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		fila.Add(self.boton_cerrar, 0)
		raiz.Add(fila, 0, wx.EXPAND | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		self.boton_anadir.Bind(wx.EVT_BUTTON, lambda _e: self.anadir())
		self.boton_quitar.Bind(wx.EVT_BUTTON, lambda _e: self.quitar())
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_pulsar_lista)
		self.lista.Bind(wx.EVT_LIST_ITEM_FOCUSED, lambda e: (self.actualizar_botones(), e.Skip()))
		vincular_menu_contextual(self.lista, self._crear_menu)

		self.cargar()
		preparar_dialogo(self, foco_inicial=self.boton_cerrar if self.error else self.lista)
		# Tras preparar_dialogo: se ejecuta antes que el suyo y lo deja seguir con Skip.
		self.Bind(wx.EVT_INIT_DIALOG, self._al_iniciar)
		panel.Layout()
		self.CentreOnParent()

	# -- Acceso a las reglas -----------------------------------------------------

	def _leer(self) -> list[str]:
		if self.proveedor is not None:
			return list(self.proveedor.leer_reglas())
		return antigravity.leer_reglas(self.ruta_ajustes)

	def _anadir(self, regla: str) -> bool:
		if self.proveedor is not None:
			return bool(self.proveedor.anadir_regla(regla))
		return antigravity.anadir_regla(regla, self.ruta_ajustes)

	def _quitar(self, regla: str) -> bool:
		if self.proveedor is not None:
			return bool(self.proveedor.quitar_regla(regla))
		return antigravity.quitar_regla(regla, self.ruta_ajustes)

	# -- Datos -------------------------------------------------------------------

	def cargar(self, regla_a_enfocar: str = "") -> bool:
		"""Lee las reglas y rellena la lista. ``False`` (lista deshabilitada) si el archivo no se entiende."""
		try:
			self.reglas = self._leer()
			self.error = ""
		except ErrorAjustesAntigravity as exc:
			log.warning("No se pueden leer las reglas de Antigravity: %s", exc)
			self.reglas = []
			self.error = str(exc)
		self.lista.reemplazar_filas([[describir_regla(regla), regla] for regla in self.reglas])
		if regla_a_enfocar in self.reglas:
			self.lista.enfocar_fila(self.reglas.index(regla_a_enfocar), anunciar=False)
		disponible = not self.error
		self.lista.Enable(disponible)
		self.etiqueta_lista.SetLabel(
			_("&Reglas permitidas:") if disponible else _("&Reglas permitidas (no se pueden leer):")
		)
		self.actualizar_botones()
		return disponible

	def seleccionada(self) -> str | None:
		"""Regla de la fila actual, o ``None``."""
		fila = self.lista.fila_actual()
		return self.reglas[fila] if 0 <= fila < len(self.reglas) else None

	def actualizar_botones(self) -> None:
		"""Sin archivo legible no se puede añadir ni quitar; sin selección, no se puede quitar."""
		self.boton_anadir.Enable(not self.error)
		self.boton_quitar.Enable(not self.error and self.seleccionada() is not None)

	# -- Acciones ----------------------------------------------------------------

	def anadir(self) -> None:
		"""Pide una regla nueva y la guarda."""
		if self.error:
			mostrar_error(self, self.error)
			return
		dialogo = DialogoNuevaRegla(self)
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			regla = dialogo.regla()
		except ValueError as exc:
			mostrar_error(self, str(exc), _("Regla no válida"))
			return
		finally:
			dialogo.Destroy()
		self.guardar_regla(regla)

	def guardar_regla(self, regla: str) -> bool:
		"""Añade ``regla`` al archivo y recarga la lista. ``True`` si se añadió."""
		try:
			anadida = self._anadir(regla)
		except ErrorAjustesAntigravity as exc:
			mostrar_error(self, str(exc))
			self.cargar()
			return False
		self.cargar(regla)
		enfocar(self.lista)
		if anadida:
			voz.hablar(_("Regla añadida"))
		else:
			mostrar_mensaje(self, _("Esa regla ya estaba permitida."))
		return anadida

	def quitar(self) -> None:
		"""Quita la regla seleccionada, tras confirmar."""
		regla = self.seleccionada()
		if regla is None or self.error:
			return
		pregunta = _("¿Quitar la regla «{regla}»? Antigravity dejará de poder hacerlo sin preguntar ({que}).").format(
			regla=regla, que=describir_regla(regla)
		)
		if confirmar(self, pregunta, _("Quitar regla"), peligroso=True) != wx.ID_YES:
			return
		self.borrar_regla(regla)

	def borrar_regla(self, regla: str) -> bool:
		"""Quita ``regla`` del archivo y recarga la lista. ``True`` si se quitó."""
		fila = self.lista.fila_actual()
		try:
			quitada = self._quitar(regla)
		except ErrorAjustesAntigravity as exc:
			mostrar_error(self, str(exc))
			self.cargar()
			return False
		self.cargar()
		if self.reglas:
			self.lista.enfocar_fila(min(max(fila, 0), len(self.reglas) - 1), anunciar=False)
		self.actualizar_botones()
		voz.hablar(_("Regla quitada") if quitada else _("Esa regla ya no estaba"))
		return quitada

	# -- Eventos -----------------------------------------------------------------

	def _al_iniciar(self, evento: wx.InitDialogEvent) -> None:
		if self.error:
			wx.CallAfter(self._avisar_error)
		evento.Skip()

	def _avisar_error(self) -> None:
		if self and self.error:
			mostrar_error(self, self.error, _("No se pueden leer las reglas"))

	def _al_pulsar_lista(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) and evento.GetModifiers() == wx.MOD_NONE:
			self.quitar()
			return
		evento.Skip()

	def _crear_menu(self) -> wx.Menu | None:
		if self.error:
			return None
		menu = wx.Menu()
		anadir = menu.Append(wx.ID_ANY, _("&Añadir…"))
		quitar = menu.Append(wx.ID_ANY, _("&Quitar"))
		quitar.Enable(self.seleccionada() is not None)
		menu.Bind(wx.EVT_MENU, lambda _e: self.anadir(), anadir)
		menu.Bind(wx.EVT_MENU, lambda _e: self.quitar(), quitar)
		return menu
