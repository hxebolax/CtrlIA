"""Ayuda, «Informar de un fallo...»: prepara un informe para copiarlo o abrir una incidencia.

Campos para lo que pasó y, debajo, de solo lectura, el informe tal como saldrá: lo
que se escribe se ve ahí al momento, con los datos del programa ya anonimizados.
Nada se envía solo: «Copiar» lo deja en el portapapeles y «Abrir una incidencia en
GitHub» abre el navegador con el informe puesto, y es la persona quien lo publica.

Los datos del equipo tardan (se pregunta a cada agente), así que van en segundo
plano (``en_fondo``, normalmente ``en_segundo_plano`` de la ventana principal): el
diálogo se abre al momento y la interfaz nunca espera.
"""

from __future__ import annotations

import logging
import webbrowser
from collections.abc import Callable
from typing import Any

import wx

from app.services import informe_fallo
from app.ui import avisos
from app.ui.accesibilidad.dialogos import enfocar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import copiar_texto
from app.ui.comun import _

log = logging.getLogger(__name__)

ESTILO_LARGO = wx.TE_MULTILINE | wx.TE_WORDWRAP


class DialogoInformarFallo(wx.Dialog):
	"""Formulario del informe, con lo que se incluirá a la vista.

	:param recoger: devuelve el texto del entorno (bloquea: se llama con ``en_fondo``).
	:param en_fondo: ``en_fondo(funcion, al_terminar=..., al_fallar=...)``.
	"""

	def __init__(self, padre: wx.Window | None, recoger: Callable[[], str], en_fondo: Callable[..., None]) -> None:
		titulo = _("Informar de un fallo")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		#: El entorno, cuando esté; hasta entonces no se puede copiar ni abrir.
		self.entorno: str | None = None

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_resumen = crear_campo(panel, _("&Resumen del fallo:"), wx.TextCtrl, sizer=raiz,
			ayuda=_("Una frase que diga qué falla. Es el título de la incidencia."))
		self.campo_resumen.SetMaxLength(informe_fallo.MAX_RESUMEN)
		_e, self.campo_que_paso = crear_campo(panel, _("&Qué ha pasado:"), wx.TextCtrl, sizer=raiz, style=ESTILO_LARGO,
			ayuda=_("Cuenta lo que ha pasado, con el texto exacto del error si lo hay."))
		_e, self.campo_esperado = crear_campo(panel, _("Qué e&sperabas que pasara:"), wx.TextCtrl, sizer=raiz, style=ESTILO_LARGO,
			ayuda=_("Opcional: qué creías que iba a hacer el programa."))
		_e, self.campo_pasos = crear_campo(panel, _("&Pasos para que vuelva a pasar:"), wx.TextCtrl, sizer=raiz, style=ESTILO_LARGO,
			ayuda=_("Opcional: qué hay que hacer, paso a paso, para que vuelva a pasar."))
		self.campos = (self.campo_resumen, self.campo_que_paso, self.campo_esperado, self.campo_pasos)
		for campo in self.campos[1:]:
			campo.SetMinSize(self.FromDIP(wx.Size(620, 64)))
		_e, self.vista = crear_campo(panel, _("Lo que se &incluirá:"), wx.TextCtrl, sizer=raiz,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			ayuda=_("El informe tal como saldrá: tus palabras y los datos del programa. Es público en GitHub: no lleva tu nombre "
				"de cuenta, tus rutas, los títulos de tus sesiones ni tus mensajes, ni la orden o la dirección de los servidores "
				"MCP, pero repasa lo que hayas escrito tú."))
		self.vista.SetMinSize(self.FromDIP(wx.Size(620, 170)))

		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_copiar = wx.Button(panel, label=_("&Copiar"))
		self.boton_copiar.SetHelpText(_("Copia el informe entero al portapapeles."))
		self.boton_github = wx.Button(panel, label=_("&Abrir una incidencia en GitHub"))
		self.boton_github.SetHelpText(_(
			"Copia el informe y abre en el navegador la página de una incidencia nueva con el informe puesto. No se publica "
			"nada hasta que lo envíes tú allí; necesitas una cuenta de GitHub. Si el informe es muy largo, en la página sale "
			"recortado: pega encima el que está en el portapapeles."
		))
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("C&errar"))
		self.boton_cerrar.SetHelpText(_("Cierra sin enviar nada. También con Escape."))
		fila.Add(self.boton_copiar, 0, wx.RIGHT, 6)
		fila.Add(self.boton_github, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		fila.Add(self.boton_cerrar, 0)
		raiz.Add(fila, 0, wx.EXPAND | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		for campo in self.campos:
			campo.Bind(wx.EVT_TEXT, lambda _evento: self.actualizar_vista())
		self.boton_copiar.Bind(wx.EVT_BUTTON, lambda _evento: self.copiar())
		self.boton_github.Bind(wx.EVT_BUTTON, lambda _evento: self.abrir_incidencia())
		self.actualizar_vista()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.campo_resumen)
		en_fondo(recoger, al_terminar=self._poner_entorno, al_fallar=self._sin_entorno)

	# ── Entorno ───────────────────────────────────────────────────────────────

	def _poner_entorno(self, entorno: str) -> None:
		if not self:
			return  # se cerró antes de que acabara
		self.entorno = entorno
		self.actualizar_vista()
		avisos.anunciar(_("Datos del programa recogidos."))

	def _sin_entorno(self, error: BaseException) -> None:
		"""Sin los datos del equipo también se puede informar: mejor poco que nada."""
		log.warning("No se han podido recoger los datos para el informe: %s", error)
		if self:
			self._poner_entorno(_("No se han podido recoger los datos del programa."))

	def informe(self) -> informe_fallo.InformeFallo:
		"""Lo escrito hasta ahora, con el entorno si ya está."""
		return informe_fallo.InformeFallo(self.campo_resumen.GetValue(), self.campo_que_paso.GetValue(),
			self.campo_esperado.GetValue(), self.campo_pasos.GetValue(), self.entorno or "")

	def actualizar_vista(self) -> None:
		"""Pone en «Lo que se incluirá» el texto final (sin mover el foco ni generar eventos)."""
		texto = informe_fallo.texto_informe(self.informe())
		if self.entorno is None:
			texto += "\n\n" + _("(Recogiendo los datos del programa...)")
		self.vista.ChangeValue(texto)

	# ── Acciones ──────────────────────────────────────────────────────────────

	def _listo(self) -> bool:
		"""Que haya lo mínimo: un resumen, qué ha pasado y los datos del programa."""
		for campo, falta in ((self.campo_resumen, _("Escribe un resumen del fallo.")),
				(self.campo_que_paso, _("Cuenta qué ha pasado."))):
			if not campo.GetValue().strip():
				mostrar_error(self, falta, _("Informar de un fallo"))
				enfocar(campo)
				return False
		if self.entorno is None:
			avisos.anunciar(_("Espera: todavía se están recogiendo los datos del programa."), interrumpir=True)
			return False
		return True

	def _al_portapapeles(self) -> bool:
		return copiar_texto(informe_fallo.texto_informe(self.informe()))

	def copiar(self) -> bool:
		"""Deja el informe en el portapapeles."""
		if not self._listo():
			return False
		copiado = self._al_portapapeles()
		avisos.anunciar(_("Informe copiado.") if copiado else _("No se pudo usar el portapapeles."), interrumpir=True)
		return copiado

	def abrir_incidencia(self) -> bool:
		"""Copia el informe y abre la incidencia en el navegador; nunca publica nada."""
		if not self._listo():
			return False
		copiado = self._al_portapapeles()
		abierto = self._abrir(informe_fallo.url_incidencia(self.informe()))
		if not abierto:
			mostrar_error(self, _("No se pudo abrir el navegador. La dirección es {u}").format(
				u=informe_fallo.URL_NUEVA_INCIDENCIA), _("Informar de un fallo"))
			return False
		avisos.anunciar(_("Se ha abierto la incidencia en el navegador. Repásala y envíala allí.") + (
			" " + _("El informe entero está copiado.") if copiado else ""), interrumpir=True)
		return True

	@staticmethod
	def _abrir(url: str) -> Any:
		try:
			return webbrowser.open(url)
		except Exception:  # noqa: BLE001 - el navegador puede fallar de muchas formas
			log.warning("No se pudo abrir el navegador", exc_info=True)
			return False
