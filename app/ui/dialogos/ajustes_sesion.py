"""Diálogo «Opciones de la sesión»: título, modelo, esfuerzo, modo de permisos y, en Claude Code, estilo de las respuestas."""

from __future__ import annotations

import logging
import threading
from typing import Any

import wx

from app.services.agentes.base import OpcionLista
from app.ui.accesibilidad.dialogos import crear_botonera, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.comun import _

log = logging.getLogger(__name__)


class DialogoAjustesSesion(wx.Dialog):
	"""Los cambios elegidos quedan en ``self.cambios`` (solo lo que cambió)."""

	def __init__(self, padre: wx.Window | None, proveedor, sesion) -> None:
		super().__init__(padre, title=_("Opciones de la sesión"), style=wx.DEFAULT_DIALOG_STYLE)
		self.sesion = sesion
		self.cambios: dict[str, Any] = {}
		self._modelos = proveedor.modelos() or [OpcionLista("", _("Predeterminado"))]
		if sesion.modelo and sesion.modelo not in [m.id for m in self._modelos]:
			self._modelos.append(OpcionLista(sesion.modelo, sesion.modelo))
		self._esfuerzos = proveedor.esfuerzos() or [OpcionLista("", _("Predeterminado"))]
		self._modos = proveedor.modos_permiso() or [OpcionLista("", _("Predeterminado"))]
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.titulo = crear_campo(self, _("&Título:"), wx.TextCtrl, value=sesion.titulo, sizer=raiz,
			ayuda=_("Nombre de la sesión en la lista."))
		_e, self.modelo = crear_campo(self, _("&Modelo:"), wx.Choice, choices=[m.etiqueta for m in self._modelos], sizer=raiz,
			ayuda=_("Modelo de IA para los próximos mensajes."))
		_e, self.esfuerzo = crear_campo(self, _("&Esfuerzo:"), wx.Choice, choices=[e.etiqueta for e in self._esfuerzos], sizer=raiz,
			ayuda=_("Cuánto piensa el agente antes de responder."))
		_e, self.modo = crear_campo(self, _("Modo de &permisos:"), wx.Choice, choices=[m.etiqueta for m in self._modos], sizer=raiz,
			ayuda=_("Qué puede hacer el agente sin preguntarte."))
		self._estilos = proveedor.estilos() if hasattr(proveedor, "estilos") else []
		self.estilo: wx.Choice | None = None
		if self._estilos:
			estilo_actual = str((getattr(sesion, "extra", None) or {}).get("estilo") or "")
			if estilo_actual and estilo_actual not in [e.id for e in self._estilos]:
				self._estilos.append(OpcionLista(estilo_actual, _("{e} (ya no está en Claude Code)").format(e=estilo_actual)))
			_e, self.estilo = crear_campo(self, _("Estilo de las &respuestas:"), wx.Choice,
				choices=[e.etiqueta for e in self._estilos], sizer=raiz,
				ayuda=_("Cómo escribe el agente sus respuestas en esta sesión: conciso, explicativo... Vale desde el "
					"próximo mensaje y no cambia la configuración del agente."))
			self.estilo.SetSelection(next((i for i, e in enumerate(self._estilos) if e.id == estilo_actual), 0))
			self.estilo.Bind(wx.EVT_CHOICE, lambda _e: self._ayuda_estilo())
			self._ayuda_estilo()
		for control, opciones, actual in ((self.modelo, self._modelos, sesion.modelo), (self.esfuerzo, self._esfuerzos, sesion.esfuerzo),
				(self.modo, self._modos, sesion.modo_permiso)):
			control.SetSelection(next((i for i, o in enumerate(opciones) if o.id == actual), 0))
			control.Enable(len(opciones) > 1)
		self.modo.Bind(wx.EVT_CHOICE, lambda _e: self._ayuda_modo())
		self._ayuda_modo()
		from app.ui.dialogos.elegir_agentes import PanelAgentes

		self._agentes_antes = [int(i) for i in (getattr(sesion, "extra", None) or {}).get("agentes") or [] if isinstance(i, int)]
		self.panel_agentes = PanelAgentes(self, seleccionados=self._agentes_antes,
			etiqueta_filtro=_("Categoría &de agentes:"), etiqueta=_("A&gentes de la sesión:"),
			etiqueta_boton=_("&Ver y gestionar el catálogo de agentes..."))
		raiz.Add(self.panel_agentes, 0, wx.EXPAND)
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		self.Layout()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.titulo)
		self._cargar_modelos_en_vivo(proveedor)

	def _cargar_modelos_en_vivo(self, proveedor) -> None:
		"""Pide al agente su lista real de modelos sin bloquear (Codex y Antigravity la dan)."""
		listar = getattr(proveedor, "listar_modelos", None)
		if not callable(listar):
			return

		def trabajo() -> None:
			try:
				modelos = listar()
			except Exception:  # noqa: BLE001 - sin lista en vivo se quedan los modelos conocidos
				log.debug("No se pudo obtener la lista de modelos de %s", proveedor.id, exc_info=True)
				return
			wx.CallAfter(self._poner_modelos, modelos)

		threading.Thread(target=trabajo, name="modelos-sesion", daemon=True).start()

	def _poner_modelos(self, modelos: list[OpcionLista]) -> None:
		if not self or not modelos:
			return
		actual = self._modelos[self.modelo.GetSelection()] if self.modelo.GetSelection() >= 0 else self._modelos[0]
		if not any(m.id == "" for m in modelos):
			modelos = [OpcionLista("", _("Predeterminado del agente")), *modelos]
		if not any(m.id == actual.id for m in modelos):
			modelos = [*modelos, actual]
		self._modelos = modelos
		self.modelo.Set([m.etiqueta for m in modelos])
		self.modelo.SetSelection(next(i for i, m in enumerate(modelos) if m.id == actual.id))
		self.modelo.Enable(len(modelos) > 1)

	def _ayuda_modo(self) -> None:
		indice = self.modo.GetSelection()
		if 0 <= indice < len(self._modos) and self._modos[indice].ayuda:
			self.modo.SetHelpText(self._modos[indice].ayuda)

	def _ayuda_estilo(self) -> None:
		if self.estilo is None:
			return
		indice = self.estilo.GetSelection()
		if 0 <= indice < len(self._estilos) and self._estilos[indice].ayuda:
			self.estilo.SetHelpText(self._estilos[indice].ayuda)

	def _aceptar(self) -> None:
		nuevos = {
			"titulo": self.titulo.GetValue().strip() or self.sesion.titulo,
			"modelo": self._modelos[self.modelo.GetSelection()].id,
			"esfuerzo": self._esfuerzos[self.esfuerzo.GetSelection()].id,
			"modo_permiso": self._modos[self.modo.GetSelection()].id,
		}
		self.cambios = {k: v for k, v in nuevos.items() if v != getattr(self.sesion, k)}
		agentes = self.panel_agentes.seleccionados()
		if agentes != self._agentes_antes:
			self.cambios["agentes"] = agentes
		if self.estilo is not None:
			estilo = self._estilos[self.estilo.GetSelection()].id
			if estilo != str((getattr(self.sesion, "extra", None) or {}).get("estilo") or ""):
				self.cambios["estilo"] = estilo
		self.EndModal(wx.ID_OK)
