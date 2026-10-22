"""Indicador de progreso accesible para tareas largas.

- ``wx.Gauge`` + etiqueta de estado CONSULTABLE (el usuario puede tabular
  hasta ella o pedirla con una tecla) en lugar de solo voz.
- Voz REGULADA: se anuncian los cambios de fase y los hitos (25, 50, 75 y
  100 %), nunca cada porcentaje; el lector ya puede consultar la barra.
- Modo indeterminado con ``Pulse()`` y un temporizador que se detiene solo.
- Seguro desde hilos: ``actualizar_desde_hilo`` usa ``wx.CallAfter``.

Para una tarea que bloquea la ventana, ``wx.ProgressDialog`` (nativo) con
``wx.PD_CAN_ABORT | wx.PD_APP_MODAL | wx.PD_AUTO_HIDE`` también es accesible;
este panel sirve cuando la ventana debe seguir usable mientras trabaja.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins

import wx

from app.ui.accesibilidad import voz

_HITOS = (25, 50, 75, 100)


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


class IndicadorProgreso(wx.Panel):
	"""Etiqueta de estado + barra de progreso con anuncios regulados."""

	def __init__(self, padre: wx.Window, nombre: str = "", anunciar: bool = True) -> None:
		super().__init__(padre)
		nombre = nombre or _("Progreso")
		self._anunciar = anunciar
		self._fase = ""
		self._ultimo_hito = -1
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Campo de solo lectura en vez de StaticText: se puede enfocar y releer.
		self.estado = wx.TextCtrl(self, style=wx.TE_READONLY | wx.BORDER_NONE, name=_("Estado"))
		self.barra = wx.Gauge(self, range=100, name=nombre)
		sizer.Add(self.estado, 0, wx.EXPAND | wx.BOTTOM, 3)
		sizer.Add(self.barra, 0, wx.EXPAND)
		self.SetSizer(sizer)
		self._pulso = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, lambda _e: self.barra.Pulse(), self._pulso)
		self.Bind(wx.EVT_WINDOW_DESTROY, self._al_destruir)

	def _al_destruir(self, evento: wx.WindowDestroyEvent) -> None:
		if evento.GetEventObject() is self and self._pulso.IsRunning():
			self._pulso.Stop()
		evento.Skip()

	def _decir(self, texto: str) -> None:
		if self._anunciar and texto:
			voz.hablar(texto, interrumpir=False, canal=voz.AVISOS)

	def fase(self, texto: str, indeterminado: bool = False) -> None:
		"""Cambia de fase (siempre se anuncia)."""
		self.estado.SetValue(texto)
		if indeterminado:
			self.barra.Pulse()
			if not self._pulso.IsRunning():
				self._pulso.Start(150)
		if texto != self._fase:
			self._fase = texto
			self._ultimo_hito = -1
			self._decir(texto)

	def porcentaje(self, valor: int, texto: str = "") -> None:
		"""Actualiza el porcentaje; solo anuncia al cruzar un hito."""
		if self._pulso.IsRunning():
			self._pulso.Stop()
		valor = max(0, min(100, int(valor)))
		self.barra.SetValue(valor)
		if texto:
			self.estado.SetValue(texto)
		hito = max((h for h in _HITOS if valor >= h), default=None)
		if hito is not None and hito != self._ultimo_hito:
			self._ultimo_hito = hito
			self._decir(_("{porcentaje} por ciento").format(porcentaje=hito))

	def terminar(self, texto: str = "") -> None:
		"""Marca el final y anuncia el resultado."""
		if self._pulso.IsRunning():
			self._pulso.Stop()
		self.barra.SetValue(100)
		mensaje = texto or _("Terminado")
		self.estado.SetValue(mensaje)
		self._decir(mensaje)

	def actualizar_desde_hilo(self, valor: int | None = None, texto: str = "") -> None:
		"""Versión segura para llamar desde un hilo de trabajo."""
		def aplicar() -> None:
			if not self:
				return
			if valor is None:
				self.fase(texto, indeterminado=True)
			else:
				self.porcentaje(valor, texto)

		wx.CallAfter(aplicar)
