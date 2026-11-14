"""Diálogo «Opciones de la sesión»: título, modelo, esfuerzo, modo de permisos y, en Claude Code, estilo de las respuestas.

Con los agentes que tienen ``Capacidades.instrucciones_sesion`` (1.6.0), también las instrucciones de la sesión y
más carpetas de trabajo (``extra["instrucciones"]`` y ``extra["carpetas_adicionales"]``), que valen desde el
mensaje siguiente. En ``cambios`` solo va lo que cambió.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import wx

from app.services.agentes.base import OpcionLista
from app.ui.accesibilidad.dialogos import crear_botonera, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo, habilitar
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
			habilitar(control, len(opciones) > 1)
		self.modo.Bind(wx.EVT_CHOICE, lambda _e: self._ayuda_modo())
		self._ayuda_modo()
		from app.ui.dialogos.elegir_skills import PanelSkills

		self._skills_antes = [int(i) for i in (getattr(sesion, "extra", None) or {}).get("skills") or [] if isinstance(i, int)]
		self.panel_skills = PanelSkills(self, seleccionados=self._skills_antes,
			etiqueta_filtro=_("Categoría &de skills:"), etiqueta=_("&Skills de la sesión:"),
			etiqueta_boton=_("&Ver y gestionar el catálogo de skills..."))
		raiz.Add(self.panel_skills, 0, wx.EXPAND)
		extra = getattr(sesion, "extra", None) or {}
		self._instrucciones_antes = str(extra.get("instrucciones") or "")
		carpetas = extra.get("carpetas_adicionales")
		self._carpetas_antes = [str(c) for c in carpetas] if isinstance(carpetas, list) else []
		self.panel_instrucciones = None
		capacidades = getattr(proveedor, "capacidades", None)
		if getattr(capacidades, "instrucciones_sesion", False):
			from app.services.agentes import remoto
			from app.ui.dialogos.nueva_sesion import PanelInstrucciones

			self.panel_instrucciones = PanelInstrucciones(self, self._instrucciones_antes, self._carpetas_antes)
			# Las carpetas de una sesión en otro equipo no se eligen desde Windows: se conservan como están.
			self.panel_instrucciones.activar(True, carpetas=not remoto.es_remota(extra))
			raiz.Add(self.panel_instrucciones, 0, wx.EXPAND)
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
		habilitar(self.modelo, len(modelos) > 1)

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
		if self.panel_instrucciones is not None:
			problema = self.panel_instrucciones.comprobar()
			if problema:
				from app.ui.accesibilidad.dialogos import mostrar_error

				mostrar_error(self, problema, _("Opciones de la sesión"))
				return
		nuevos = {
			"titulo": self.titulo.GetValue().strip() or self.sesion.titulo,
			"modelo": self._modelos[self.modelo.GetSelection()].id,
			"esfuerzo": self._esfuerzos[self.esfuerzo.GetSelection()].id,
			"modo_permiso": self._modos[self.modo.GetSelection()].id,
		}
		self.cambios = {k: v for k, v in nuevos.items() if v != getattr(self.sesion, k)}
		skills = self.panel_skills.seleccionados()
		if skills != self._skills_antes:
			self.cambios["skills"] = skills
		if self.estilo is not None:
			estilo = self._estilos[self.estilo.GetSelection()].id
			if estilo != str((getattr(self.sesion, "extra", None) or {}).get("estilo") or ""):
				self.cambios["estilo"] = estilo
		if self.panel_instrucciones is not None:
			instrucciones, carpetas = self.panel_instrucciones.valores()
			if not self.panel_instrucciones._carpetas_activas:  # noqa: SLF001 - sesión en otro equipo: no se tocan
				carpetas = list(self._carpetas_antes)
			if instrucciones != self._instrucciones_antes.strip():
				self.cambios["instrucciones"] = instrucciones
			if carpetas != self._carpetas_antes:
				self.cambios["carpetas_adicionales"] = carpetas
		self.EndModal(wx.ID_OK)
