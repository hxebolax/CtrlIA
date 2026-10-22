"""Diálogo «Agentes ACP personalizados»: añadir otros agentes compatibles con ACP.

Cualquier programa que hable el protocolo ACP (Agent Client Protocol) se puede
usar desde Ctrl+IA indicando su nombre y la orden que lo arranca, por ejemplo
``C:\\Programas\\agente\\agente.exe --acp``. Los cambios se aplican al
reiniciar Ctrl+IA.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import wx

from app.ui.accesibilidad.dialogos import confirmar, crear_botonera, enfocar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _


def separar_orden(texto: str) -> list[str]:
	"""Parte una línea de órdenes respetando las comillas («"C:\\Mis programas\\x.exe" --acp»)."""
	partes = shlex.split(texto, posix=False)
	return [parte[1:-1] if len(parte) >= 2 and parte[0] == parte[-1] == '"' else parte for parte in partes]


def identificador_de(nombre: str) -> str:
	limpio = re.sub(r"[^a-z0-9]+", "-", nombre.casefold()).strip("-")
	return "acp-" + (limpio or "agente")


class DialogoEditarAgenteAcp(wx.Dialog):
	"""Nombre, orden y carpeta de un agente ACP."""

	def __init__(self, padre: wx.Window | None, agente: dict[str, Any] | None = None) -> None:
		super().__init__(padre, title=_("Agente ACP"), style=wx.DEFAULT_DIALOG_STYLE)
		agente = agente or {}
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.nombre = crear_campo(self, _("&Nombre:"), wx.TextCtrl, value=str(agente.get("nombre") or ""), sizer=raiz,
			ayuda=_("Cómo aparecerá el agente en la lista de agentes de «Nueva sesión»."))
		orden = agente.get("orden") or []
		texto_orden = " ".join(f'"{p}"' if " " in p else p for p in orden)
		_e, self.orden = crear_campo(self, _("&Orden que lo arranca:"), wx.TextCtrl, value=texto_orden, sizer=raiz,
			ayuda=_("El programa y sus argumentos, por ejemplo: \"C:\\Programas\\agente.exe\" --acp. "
				"Las rutas con espacios van entre comillas. No se admiten archivos .cmd, .bat ni .ps1."))
		_e, self.carpeta = crear_campo(self, _("Carpeta &desde la que se lanza (opcional):"), wx.TextCtrl,
			value=str(agente.get("carpeta") or ""), sizer=raiz,
			ayuda=_("Normalmente se deja vacío: el agente se lanza en la carpeta de cada sesión."))
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		self.agente: dict[str, Any] | None = None
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.nombre)

	def _aceptar(self) -> None:
		nombre = self.nombre.GetValue().strip()
		try:
			orden = separar_orden(self.orden.GetValue().strip())
		except ValueError:
			orden = []
		if not nombre:
			mostrar_error(self, _("Escribe un nombre para el agente."), _("Agente ACP"))
			enfocar(self.nombre)
			return
		if not orden:
			mostrar_error(self, _("Escribe la orden que arranca el agente."), _("Agente ACP"))
			enfocar(self.orden)
			return
		if Path(orden[0]).suffix.lower() in (".cmd", ".bat", ".ps1"):
			mostrar_error(self, _("Por seguridad no se admiten archivos .cmd, .bat ni .ps1. Indica el programa .exe, o node.exe seguido del archivo .js."), _("Agente ACP"))
			enfocar(self.orden)
			return
		self.agente = {"id": identificador_de(nombre), "nombre": nombre, "orden": orden}
		if self.carpeta.GetValue().strip():
			self.agente["carpeta"] = self.carpeta.GetValue().strip()
		self.EndModal(wx.ID_OK)


class DialogoAcpPersonalizados(wx.Dialog):
	"""Lista de agentes ACP propios; el resultado queda en ``self.agentes``."""

	def __init__(self, padre: wx.Window | None, agentes: list[dict[str, Any]]) -> None:
		super().__init__(padre, title=_("Agentes ACP personalizados"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.agentes = [dict(a) for a in agentes if isinstance(a, dict)]
		raiz = wx.BoxSizer(wx.VERTICAL)
		explicacion = wx.StaticText(self, label=_(
			"Aquí puedes añadir otros agentes de IA compatibles con el protocolo ACP. "
			"Aparecerán en «Nueva sesión» la próxima vez que abras Ctrl+IA."))
		explicacion.Wrap(self.FromDIP(520))
		raiz.Add(explicacion, 0, wx.ALL, 6)
		raiz.Add(wx.StaticText(self, label=_("&Lista de agentes:")), 0, wx.LEFT | wx.RIGHT, 6)
		self.lista = ListaAccesible(self, _("Agentes"), [(_("Nombre"), 180), (_("Orden"), 360)])
		self.lista.SetHelpText(_("Agentes ACP añadidos. Intro o F2 los edita; Suprimir los quita."))
		self.lista.SetMinSize(self.FromDIP(wx.Size(560, 180)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_anadir = wx.Button(self, label=_("Añadir &nuevo..."))
		self.boton_anadir.SetHelpText(_("Añade un agente indicando su nombre y la orden que lo arranca."))
		self.boton_quitar = wx.Button(self, label=_("&Quitar"))
		self.boton_quitar.SetHelpText(_("Quita de la lista el agente elegido."))
		fila.Add(self.boton_anadir, 0, wx.RIGHT, 6)
		fila.Add(self.boton_quitar, 0)
		raiz.Add(fila, 0, wx.ALL, 6)
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.boton_anadir.Bind(wx.EVT_BUTTON, lambda _e: self.anadir())
		self.boton_quitar.Bind(wx.EVT_BUTTON, lambda _e: self.quitar())
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.editar())
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		vincular_menu_contextual(self.lista, self._menu)
		self._rellenar()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.lista if self.agentes else self.boton_anadir)

	def _rellenar(self) -> None:
		self.lista.reemplazar_filas([(a.get("nombre", ""), " ".join(a.get("orden") or [])) for a in self.agentes], conservar_foco=False)
		if self.agentes:
			self.lista.enfocar_fila(0, anunciar=False)
		self.boton_quitar.Enable(bool(self.agentes))

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() == wx.WXK_DELETE and not evento.HasAnyModifiers():  # atajo: Suprimir
			self.quitar()
			return
		if evento.GetKeyCode() == wx.WXK_F2 and not evento.HasAnyModifiers():  # atajo: F2
			self.editar()
			return
		evento.Skip()

	def _menu(self) -> wx.Menu:
		menu = wx.Menu()
		for etiqueta, accion in ((_("&Editar"), self.editar), (_("&Quitar"), self.quitar), (_("Añadir &nuevo..."), self.anadir)):
			item = menu.Append(wx.ID_ANY, etiqueta)
			self.lista.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), item)
		return menu

	def anadir(self) -> None:
		dialogo = DialogoEditarAgenteAcp(self)
		try:
			if dialogo.ShowModal() == wx.ID_OK and dialogo.agente:
				self.agentes.append(dialogo.agente)
		finally:
			dialogo.Destroy()
		self._rellenar()
		if self.agentes:
			self.lista.enfocar_fila(len(self.agentes) - 1, anunciar=False)

	def editar(self) -> None:
		indice = self.lista.GetFocusedItem()
		if not 0 <= indice < len(self.agentes):
			return
		dialogo = DialogoEditarAgenteAcp(self, self.agentes[indice])
		try:
			if dialogo.ShowModal() == wx.ID_OK and dialogo.agente:
				self.agentes[indice] = dialogo.agente
		finally:
			dialogo.Destroy()
		self._rellenar()

	def quitar(self) -> None:
		indice = self.lista.GetFocusedItem()
		if not 0 <= indice < len(self.agentes):
			return
		nombre = self.agentes[indice].get("nombre", "")
		if confirmar(self, _("¿Quitar el agente «{n}»?").format(n=nombre), _("Quitar agente")) != wx.ID_YES:
			return
		del self.agentes[indice]
		self._rellenar()
