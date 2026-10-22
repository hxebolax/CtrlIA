"""Diálogo «Permisos de la sesión»: lo que el agente puede hacer sin preguntar.

Enseña el modo de permisos de la sesión y la lista de lo que has permitido
«durante esta sesión», con la regla exacta y cuándo lo permitiste, y deja
quitar una regla (tras confirmarlo) o todas.

Las reglas son solo de esta sesión y solo las guarda Ctrl+IA: nunca se
escriben en los archivos de configuración de Claude Code ni de Codex. Con
Claude Code, «Permitir durante esta sesión» guarda también la carpeta que
sugiere (``Carpeta(ruta)``). Debajo, solo para leer, las reglas de la propia
configuración del agente para esa carpeta (AIChat #336).

- Claude Code las recibe al empezar cada turno: quitar una surte efecto desde
  el próximo mensaje (el turno en marcha conserva lo que tenía).
- Con Codex es Ctrl+IA quien contesta por ti: quitar una surte efecto en la
  siguiente petición.
"""

from __future__ import annotations

import logging
import re
import time

import wx

from app.ui.accesibilidad.dialogos import confirmar, enfocar, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Código con el que se cierra el diálogo para abrir «Opciones de la sesión».
ID_CAMBIAR_MODO = wx.NewIdRef()

_RE_REGLA = re.compile(r"^([A-Za-z][A-Za-z0-9_.:-]*)(?:\((.*)\))?$", re.DOTALL)

#: Nombre en español de las herramientas de Claude Code más habituales.
_HERRAMIENTAS = {
	"Bash": "Ejecutar órdenes",
	"PowerShell": "Ejecutar órdenes de PowerShell",
	"Read": "Leer archivos",
	"Write": "Escribir archivos",
	"Edit": "Editar archivos",
	"MultiEdit": "Editar archivos",
	"NotebookEdit": "Editar cuadernos",
	"Glob": "Buscar archivos",
	"Grep": "Buscar texto",
	"WebFetch": "Leer páginas web",
	"WebSearch": "Buscar en internet",
	"Task": "Lanzar subagentes",
	"Agent": "Lanzar subagentes",
}


def describir_regla(regla: str) -> str:
	"""Qué permite una regla, en palabras: «Ejecutar «git push» con cualquier argumento»."""
	regla = (regla or "").strip()
	if regla.startswith("Orden(") and regla.endswith(")"):
		return _("Ejecutar «{orden}» con cualquier argumento").format(orden=regla[6:-1])
	if regla.startswith("Carpeta(") and regla.endswith(")"):
		# Carpeta conservada con «Permitir durante esta sesión» (Claude Code, --add-dir).
		return _("Trabajar en la carpeta «{carpeta}» como en la de la sesión").format(carpeta=regla[8:-1])
	coincidencia = _RE_REGLA.match(regla)
	if not coincidencia:
		return _("Regla «{regla}»").format(regla=regla)
	herramienta, contenido = coincidencia.group(1), coincidencia.group(2)
	if herramienta.startswith("mcp__"):
		partes = [p for p in herramienta.split("__") if p]
		nombre = _("Herramienta {h} del servidor MCP {s}").format(h=partes[-1], s=partes[1]) if len(partes) >= 3 \
			else _("Herramientas del servidor MCP {s}").format(s=partes[-1])
	else:
		nombre = _(_HERRAMIENTAS[herramienta]) if herramienta in _HERRAMIENTAS else _("Usar la herramienta {h}").format(h=herramienta)
	if contenido is None or not contenido.strip():
		return _("{n}: cualquiera").format(n=nombre)
	contenido = contenido.strip()
	if herramienta in ("Bash", "PowerShell"):
		if contenido.endswith(":*"):
			return _("Ejecutar «{orden}» con cualquier argumento").format(orden=contenido[:-2])
		if contenido.endswith(" *"):
			return _("Ejecutar «{orden}» con cualquier argumento").format(orden=contenido[:-2])
		return _("Ejecutar la orden «{orden}»").format(orden=contenido)
	if herramienta == "WebFetch" and contenido.startswith("domain:"):
		return _("Leer páginas web de {dominio}").format(dominio=contenido[7:])
	return _("{n}: {c}").format(n=nombre, c=contenido)


def texto_fecha(fecha: float | None) -> str:
	if not fecha:
		return ""
	return time.strftime("%d/%m/%Y %H:%M", time.localtime(fecha))


class DialogoPermisosSesion(wx.Dialog):
	"""Lista y quita lo permitido en una sesión. Al cerrar, ``self.reglas`` tiene la lista final."""

	def __init__(self, padre: wx.Window | None, titulo_sesion: str, nombre_agente: str, modo: str, ayuda_modo: str,
			reglas: list[str], fechas: dict[str, float] | None = None, trabajando: bool = False,
			cuando: str = "", reglas_agente: str = "") -> None:
		super().__init__(padre, title=_("Permisos de la sesión"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.reglas = list(reglas)
		self.fechas = dict(fechas or {})
		self.cambiado = False
		self._agente = nombre_agente
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)

		texto_modo = _("{modo}. {ayuda}").format(modo=modo, ayuda=ayuda_modo) if ayuda_modo else modo
		_e, self.modo = crear_campo(panel, _("Modo de &permisos de «{s}»:").format(s=titulo_sesion), wx.TextCtrl,
			value=texto_modo, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			ayuda=_("Qué puede hacer {a} sin preguntarte en esta sesión. Se cambia con «Cambiar el modo».").format(a=nombre_agente),
			sizer=raiz)
		self.modo.SetMinSize(self.FromDIP(wx.Size(520, 50)))
		self.boton_modo = wx.Button(panel, label=_("Cambiar el &modo..."))
		self.boton_modo.SetHelpText(_("Cierra este diálogo y abre «Opciones de la sesión» para elegir otro modo de permisos."))
		self.boton_modo.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(ID_CAMBIAR_MODO))
		raiz.Add(self.boton_modo, 0, wx.ALL, 5)

		_e, self.lista = crear_campo(panel, _("Permitido &en esta sesión:"), wx.ListBox, style=wx.LB_SINGLE,
			ayuda=_("Lo que permitiste con «Permitir durante esta sesión»: qué permite, la regla exacta y cuándo. "
				"Suprimir quita la regla elegida, tras confirmarlo."), sizer=raiz)
		self.lista.SetMinSize(self.FromDIP(wx.Size(520, 160)))
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla)

		explicacion = cuando or _("Lo que quites se aplica desde el próximo mensaje.")
		if trabajando:
			explicacion += " " + _("{a} está trabajando: el turno en marcha conserva lo que tenía permitido al empezar.").format(
				a=nombre_agente)
		explicacion += " " + _("Estas reglas son solo de esta sesión; Ctrl+IA nunca las escribe en la configuración del agente.")
		_e, self.nota = crear_campo(panel, _("Cuándo se aplica:"), wx.TextCtrl, value=explicacion,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, ayuda=_("Desde cuándo cuentan los cambios."), sizer=raiz)
		self.nota.SetMinSize(self.FromDIP(wx.Size(520, 60)))

		self.reglas_agente: wx.TextCtrl | None = None
		if reglas_agente:
			# Solo lectura (AIChat #336): lo que el propio agente permite o deniega en esta carpeta.
			_e, self.reglas_agente = crear_campo(panel, _("&Reglas de la configuración de {a}:").format(a=nombre_agente),
				wx.TextCtrl, value=reglas_agente, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
				ayuda=_("Lo que la propia configuración de {a} permite, pregunta siempre o no permite nunca en esta "
					"carpeta, una regla por línea con su archivo. Valen también fuera de Ctrl+IA. Solo se pueden leer "
					"aquí.").format(a=nombre_agente), sizer=raiz)
			self.reglas_agente.SetMinSize(self.FromDIP(wx.Size(520, 120)))

		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_quitar = wx.Button(panel, label=_("&Quitar"))
		self.boton_quitar.SetHelpText(_("Quita la regla elegida (tecla Suprimir en la lista). Se te pide confirmación."))
		self.boton_quitar.Bind(wx.EVT_BUTTON, lambda _e: self.quitar_elegida())
		self.boton_todas = wx.Button(panel, label=_("Quitar &todas"))
		self.boton_todas.SetHelpText(_("Quita todas las reglas de esta sesión, tras confirmarlo."))
		self.boton_todas.Bind(wx.EVT_BUTTON, lambda _e: self.quitar_todas())
		cerrar = wx.Button(panel, wx.ID_CLOSE, _("&Cerrar"))
		cerrar.SetHelpText(_("Cierra el diálogo. Lo que hayas quitado ya está guardado."))
		cerrar.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_CLOSE))
		for boton in (self.boton_quitar, self.boton_todas, cerrar):
			botones.Add(boton, 0, wx.RIGHT, 6)
		raiz.Add(botones, 0, wx.ALL, 5)

		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self._rellenar(0)
		self.CentreOnParent()
		preparar_dialogo(self, id_escape=wx.ID_CLOSE, foco_inicial=self.lista)

	# -- Lista ----------------------------------------------------------------------

	def texto_fila(self, regla: str) -> str:
		partes = [describir_regla(regla) + ".", _("Regla: {r}.").format(r=regla)]
		fecha = texto_fecha(self.fechas.get(regla))
		if fecha:
			partes.append(_("Permitido el {f}.").format(f=fecha))
		return " ".join(partes)

	def _rellenar(self, seleccion: int) -> None:
		hay = bool(self.reglas)
		if hay:
			self.lista.Set([self.texto_fila(r) for r in self.reglas])
			self.lista.SetSelection(min(max(seleccion, 0), len(self.reglas) - 1))
		else:
			self.lista.Set([_("No hay nada permitido más allá del modo de permisos.")])
			self.lista.SetSelection(0)
		self.boton_quitar.Enable(hay)
		self.boton_todas.Enable(hay)

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) and evento.GetModifiers() == wx.MOD_NONE:
			self.quitar_elegida()
			return
		evento.Skip()

	def quitar_elegida(self) -> bool:
		indice = self.lista.GetSelection()
		if not self.reglas or not 0 <= indice < len(self.reglas):
			return False
		regla = self.reglas[indice]
		if confirmar(self, _("¿Quitar «{d}» ({r})? {a} volverá a pedirte permiso para esto.").format(
				d=describir_regla(regla), r=regla, a=self._agente), _("Quitar permiso"), peligroso=True) != wx.ID_YES:
			return False
		del self.reglas[indice]
		self.fechas.pop(regla, None)
		self.cambiado = True
		self._rellenar(indice)
		enfocar(self.lista)
		return True

	def quitar_todas(self) -> bool:
		if not self.reglas:
			return False
		if confirmar(self, _("¿Quitar las {n} reglas de esta sesión? {a} volverá a pedirte permiso para todo.").format(
				n=len(self.reglas), a=self._agente), _("Quitar todos los permisos"), peligroso=True) != wx.ID_YES:
			return False
		self.reglas.clear()
		self.fechas.clear()
		self.cambiado = True
		self._rellenar(0)
		enfocar(self.lista)
		return True
