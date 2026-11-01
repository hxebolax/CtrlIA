"""Diálogo «Servidores MCP de la sesión»: su estado de ahora y qué hacer con ellos.

Una lista (Servidor, Estado, Herramientas, Origen, Motivo) y un detalle de solo
lectura. Intro hace lo que el servidor necesita: reconectarlo o, si pide iniciar
sesión, abrir la consola para ello. Ctrl+R reconecta, Ctrl+Mayús+R reconecta los
que tienen algún problema, Ctrl+D lo activa o desactiva, Ctrl+L inicia sesión y
F5 vuelve a preguntar. Todo está también en el menú contextual.

Lo que habla con el agente va por ``en_fondo`` (``en_segundo_plano`` de la ventana
principal): reconectar un servidor puede tardar lo que tarde en arrancar, y la
interfaz nunca espera. El diálogo no sabe de Claude Code: usa los métodos MCP de
``Conversacion``, así se prueba sin ventana principal ni agente.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import wx

from app.services.agentes import procesos
from app.services.servidores_mcp import ServidorMcp
from app.ui import avisos
from app.ui.accesibilidad.dialogos import mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Columnas de la lista: (cabecera, ancho).
COLUMNAS = [(_("Servidor"), 220), (_("Estado"), 190), (_("Herramientas"), 90), (_("Origen"), 200), (_("Motivo"), 340)]


def valores_de(servidor: ServidorMcp) -> list[str]:
	"""Las celdas de un servidor en la lista."""
	herramientas = "" if servidor.herramientas is None else str(servidor.herramientas)
	return [servidor.nombre, servidor.estado_legible, herramientas, servidor.origen_legible, servidor.motivo]


def detalle_de(servidor: ServidorMcp) -> str:
	"""Todo lo que se sabe de un servidor, una cosa por línea."""
	lineas = [_("Servidor: {v}").format(v=servidor.nombre), _("Estado: {v}").format(v=servidor.estado_legible)]
	if servidor.herramientas is not None:
		lineas.append(_("Herramientas: {v}").format(v=servidor.herramientas))
	if servidor.origen_legible:
		lineas.append(_("Origen: {v}").format(v=servidor.origen_legible))
	if servidor.destino:
		lineas.append(_("Orden o dirección: {v}").format(v=servidor.destino))
	if servidor.motivo:
		lineas.append(_("Motivo: {v}").format(v=servidor.motivo))
	return "\n".join(lineas)


class DialogoServidoresMcp(wx.Dialog):
	"""Servidores MCP de una sesión, con reconectar, activar y desactivar e iniciar sesión."""

	def __init__(self, padre: wx.Window | None, conversacion: Any, servidores: list[ServidorMcp],
			en_fondo: Callable[..., None]) -> None:
		titulo = _("Servidores MCP de la sesión")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._conversacion = conversacion
		self._en_fondo = en_fondo
		self._servidores = list(servidores)
		#: Hay una acción en marcha: las demás esperan a que acabe.
		self._ocupado = False

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.situacion = wx.StaticText(panel, label="")
		sizer.Add(self.situacion, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.etiqueta = wx.StaticText(panel, label=_("&Servidores:"))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.lista = ListaAccesible(panel, _("Servidores"), COLUMNAS)
		self.lista.SetHelpText(_(
			"Los servidores MCP y conectores de esta sesión, con su estado. Intro hace lo que el servidor necesita: "
			"reconectarlo o, si pide iniciar sesión, abrir una consola para ello. Control+R lo reconecta; Control+Mayús+R "
			"reconecta todos los que tienen algún problema; Control+D lo activa o lo desactiva; Control+L inicia sesión en "
			"él; F5 vuelve a preguntar el estado. La tecla Aplicaciones abre el menú con todo."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(900, 240)))
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		sizer.Add(wx.StaticText(panel, label=_("&Detalle:")), 0, wx.LEFT | wx.RIGHT, 6)
		self.detalle = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, name=_("Detalle"))
		self.detalle.SetHelpText(_("Todo lo que se sabe del servidor elegido: la orden o dirección y el motivo completo si falla."))
		self.detalle.SetMinSize(self.FromDIP(wx.Size(900, 110)))
		sizer.Add(self.detalle, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra esta ventana. Lo que hayas cambiado sigue aplicado."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
		panel.SetSizer(sizer)

		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.accion_principal())
		self.lista.Bind(wx.EVT_LIST_ITEM_FOCUSED, lambda _evento: self._poner_detalle())
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		vincular_menu_contextual(self.lista, self._crear_menu)
		preparar_dialogo(self, foco_inicial=self.lista)

		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self._rellenar()
		self.Layout()
		self.Fit()
		self.CentreOnParent()

	# ── Contenido ─────────────────────────────────────────────────────────────

	def _texto_situacion(self) -> str:
		if self._conversacion.mcp_en_vivo():
			return _("Estado de ahora mismo.")
		if self._servidores:
			return _("Claude Code está cerrado: esto es lo que dijo la última vez. Vuelve a conectarlo todo con tu próximo mensaje.")
		return _("Claude Code todavía no ha dicho nada: lo cuenta al empezar a responder. Envía un mensaje en esta sesión y vuelve aquí.")

	def _rellenar(self, fila: int | None = None) -> None:
		actual = self.lista.fila_actual() if fila is None else fila
		self.situacion.SetLabel(self._texto_situacion())
		self.lista.reemplazar_filas([valores_de(s) for s in self._servidores], conservar_foco=False)
		total = len(self._servidores)
		problemas = sum(1 for s in self._servidores if s.con_problema)
		if total:
			self.etiqueta.SetLabel(_("&Servidores ({n}, con problema {p}):").format(n=total, p=problemas))
			self.lista.SetName(_("Servidores ({n}, con problema {p})").format(n=total, p=problemas))
			self.lista.enfocar_fila(min(max(actual, 0), total - 1), anunciar=False)
		else:
			self.etiqueta.SetLabel(_("&Servidores: ninguno."))
			self.lista.SetName(_("Servidores: ninguno"))
		self._poner_detalle()

	def _poner_detalle(self) -> None:
		servidor = self.servidor_actual()
		self.detalle.ChangeValue(detalle_de(servidor) if servidor is not None else "")

	def servidor_actual(self) -> ServidorMcp | None:
		fila = self.lista.fila_actual()
		return self._servidores[fila] if 0 <= fila < len(self._servidores) else None

	# ── Acciones ──────────────────────────────────────────────────────────────

	def _elegido(self) -> ServidorMcp | None:
		servidor = self.servidor_actual()
		if servidor is None:
			avisos.anunciar(_("No hay ningún servidor elegido."), interrumpir=True)
		return servidor

	def _lanzar(self, funcion: Callable[[], Any], al_terminar: Callable[[Any], None]) -> bool:
		"""Ejecuta ``funcion`` en segundo plano, de una en una; el resultado solo se usa si el diálogo sigue abierto."""
		if self._ocupado:
			avisos.anunciar(_("Espera: la acción anterior todavía no ha terminado."), interrumpir=True)
			return False
		self._ocupado = True

		def bien(resultado: Any) -> None:
			self._ocupado = False
			if self:
				al_terminar(resultado)

		def mal(error: BaseException) -> None:
			self._ocupado = False
			log.info("Acción de un servidor MCP sin hacer: %s", error)
			if self:
				avisos.anunciar(_("No se ha podido: {e}").format(e=error), interrumpir=True)
				self.actualizar(anunciar=False)

		self._en_fondo(funcion, al_terminar=bien, al_fallar=mal, clave="servidores_mcp")
		return True

	def actualizar(self, anunciar: bool = True) -> bool:
		"""F5: vuelve a preguntar el estado."""

		def poner(servidores: Any) -> None:
			self._servidores = list(servidores or [])
			self._rellenar()
			if anunciar:
				avisos.anunciar(_("Estado actualizado."), interrumpir=True)

		return self._lanzar(self._conversacion.servidores_mcp, poner)

	def reconectar(self) -> bool:
		"""Ctrl+R: reconecta el servidor elegido."""
		servidor = self._elegido()
		if servidor is None:
			return False
		nombre = servidor.nombre

		def hecho(_resultado: Any) -> None:
			avisos.anunciar(_("«{n}» reconectado.").format(n=nombre), interrumpir=True)
			self.actualizar(anunciar=False)

		if self._lanzar(lambda: self._conversacion.reconectar_mcp(nombre), hecho):
			avisos.anunciar(_("Reconectando «{n}»...").format(n=nombre), interrumpir=True)
			return True
		return False

	def reconectar_con_problema(self) -> bool:
		"""Ctrl+Mayús+R: reconecta, uno tras otro, los que tienen algún problema."""
		nombres = [s.nombre for s in self._servidores if s.estado == "failed"]
		if not nombres:
			avisos.anunciar(_("Ningún servidor tiene un fallo que se pueda reintentar."), interrumpir=True)
			return False

		def todos() -> int:
			fallan = 0
			for nombre in nombres:
				try:
					self._conversacion.reconectar_mcp(nombre)
				except (RuntimeError, TimeoutError) as error:
					log.info("El servidor MCP %s sigue sin conectar: %s", nombre, error)
					fallan += 1
			return fallan

		def hecho(fallan: int) -> None:
			avisos.anunciar(_("Reconectados: {b}. Siguen fallando: {m}.").format(b=len(nombres) - fallan, m=fallan), interrumpir=True)
			self.actualizar(anunciar=False)

		if self._lanzar(todos, hecho):
			avisos.anunciar(_("Reconectando {n} servidores...").format(n=len(nombres)), interrumpir=True)
			return True
		return False

	def alternar(self) -> bool:
		"""Ctrl+D: desactiva el servidor o lo vuelve a activar."""
		servidor = self._elegido()
		if servidor is None:
			return False
		nombre, activar = servidor.nombre, servidor.estado == "disabled"

		def hecho(_resultado: Any) -> None:
			avisos.anunciar((_("«{n}» activado.") if activar else _("«{n}» desactivado.")).format(n=nombre), interrumpir=True)
			self.actualizar(anunciar=False)

		return self._lanzar(lambda: self._conversacion.activar_mcp(nombre, activar), hecho)

	def iniciar_sesion(self) -> bool:
		"""Ctrl+L: abre una consola con ``claude mcp login`` y reconecta el servidor al cerrarla."""
		servidor = self._elegido()
		if servidor is None:
			return False
		if servidor.conector:
			mostrar_mensaje(self, _("Los conectores de claude.ai se conectan en claude.ai, en Ajustes, Conectores. Cuando "
				"lo hayas hecho, pulsa Control+R aquí para reconectarlo."), _("Iniciar sesión"))
			return False
		orden = self._conversacion.orden_inicio_sesion_mcp(servidor.nombre)
		if not orden:
			mostrar_mensaje(self, _("Desde aquí no se puede: inicia sesión en el equipo donde está Claude Code, con "
				"«claude mcp login {n}» en una consola.").format(n=servidor.nombre), _("Iniciar sesión"))
			return False
		nombre = servidor.nombre
		carpeta = getattr(self._conversacion.ajustes, "carpeta", None)
		if not procesos.abrir_en_consola(orden, carpeta, al_terminar=lambda _codigo: wx.CallAfter(self._tras_iniciar_sesion, nombre)):
			avisos.anunciar(_("No se ha podido abrir la consola."), interrumpir=True)
			return False
		avisos.anunciar(_("Se ha abierto una consola para iniciar sesión en «{n}». Al cerrarla, se reconecta.").format(n=nombre),
			interrumpir=True)
		return True

	def _tras_iniciar_sesion(self, nombre: str) -> None:
		if not self:
			return
		for fila, servidor in enumerate(self._servidores):
			if servidor.nombre == nombre:
				self.lista.enfocar_fila(fila, anunciar=False)
				self.reconectar()
				return

	def accion_principal(self) -> bool:
		"""Intro: lo que el servidor necesita."""
		servidor = self._elegido()
		if servidor is None:
			return False
		return self.iniciar_sesion() if servidor.estado == "needs-auth" else self.reconectar()

	def _crear_menu(self) -> wx.Menu | None:
		servidor = self.servidor_actual()
		if servidor is None:
			return None
		acciones: list[tuple[str, Callable[[], object]] | None] = [
			(_("&Reconectar\tCtrl+R"), self.reconectar),
			(_("Reconectar &los que fallan\tCtrl+Shift+R"), self.reconectar_con_problema),
			(_("&Iniciar sesión...\tCtrl+L"), self.iniciar_sesion),
			(_("&Activar o desactivar\tCtrl+D"), self.alternar),
			None,
			(_("Act&ualizar\tF5"), self.actualizar),
		]
		return menu_de_acciones(self.lista, acciones)

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if mods == wx.MOD_NONE and tecla == wx.WXK_F5:
			self.actualizar()
			return
		if mods == wx.MOD_CONTROL and tecla == ord("R"):
			self.reconectar()
			return
		if mods == wx.MOD_CONTROL | wx.MOD_SHIFT and tecla == ord("R"):
			self.reconectar_con_problema()
			return
		if mods == wx.MOD_CONTROL and tecla == ord("D"):
			self.alternar()
			return
		if mods == wx.MOD_CONTROL and tecla == ord("L"):
			self.iniciar_sesion()
			return
		evento.Skip()
