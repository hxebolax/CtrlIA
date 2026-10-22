"""Correcciones para controles nativos con problemas de accesibilidad.

- ``SliderAccesible``: el ``wx.Slider`` nativo expone como valor MSAA el
  PORCENTAJE del rango (valor 5 de 0-10 se anuncia «50»). Esta subclase
  expone el valor real con su unidad («5 de 10», «120 milisegundos») y
  ajusta los saltos de flechas y Re Pág/Av Pág. Comprobado con MSAA.
- ``nombrar_ventana_generica``: da nombre a paneles y ventanas no nativas
  (páginas de pestañas, visores) que si no se anuncian como «panel».

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins

import wx


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


class _AccesibleSlider(wx.Accessible):
	"""Expone el valor real del deslizador en lugar del porcentaje."""

	def __init__(self, control: SliderAccesible) -> None:
		super().__init__(control)
		self._control = control

	def GetValue(self, childId: int):  # noqa: N802,N803 - API de wx
		if childId != 0:
			return (wx.ACC_NOT_IMPLEMENTED, "")
		return (wx.ACC_OK, self._control.texto_valor())


class SliderAccesible(wx.Slider):
	"""``wx.Slider`` que el lector anuncia con su valor real y su unidad.

	Cree ANTES su ``wx.StaticText``. El nombre sale de esa etiqueta; el valor,
	de ``texto_valor()``. No hable el valor desde la aplicación al moverlo: el
	lector ya lo anuncia (se oiría dos veces).
	"""

	def __init__(
		self,
		padre: wx.Window,
		valor: int = 0,
		minimo: int = 0,
		maximo: int = 100,
		unidad: str = "",
		paso: int = 1,
		paso_pagina: int = 10,
		mostrar_maximo: bool = True,
		**kwargs,
	) -> None:
		super().__init__(padre, value=valor, minValue=minimo, maxValue=maximo, **kwargs)
		self.unidad = unidad
		self.mostrar_maximo = mostrar_maximo
		self.SetLineSize(paso)
		self.SetPageSize(paso_pagina)
		self._accesible = _AccesibleSlider(self)
		self.SetAccessible(self._accesible)

	def texto_valor(self) -> str:
		"""«5 de 10», «120 milisegundos» o «5 de 10 minutos»."""
		valor = self.GetValue()
		if self.mostrar_maximo and not self.unidad:
			return _("{valor} de {maximo}").format(valor=valor, maximo=self.GetMax())
		if self.mostrar_maximo:
			return _("{valor} de {maximo} {unidad}").format(valor=valor, maximo=self.GetMax(), unidad=self.unidad)
		return f"{valor} {self.unidad}".strip()


def nombrar_ventana_generica(ventana: wx.Window, nombre: str) -> None:
	"""Nombre para una ventana genérica (panel de pestaña, visor HTML...).

	En estas ventanas el nombre que ve el lector es su texto de ventana: se
	fija con ``SetLabel`` (``SetName`` después de crear no sirve). Lo ideal es
	pasar ``name=`` ya en el constructor.
	"""
	ventana.SetLabel(nombre)
	ventana.SetName(nombre)
