"""Etiquetas, nombres accesibles y orden de tabulación.

Cómo obtiene un lector de pantalla el nombre de un control Win32 que no
tiene texto propio (TextCtrl, Choice, ComboBox, SpinCtrl, ListCtrl...): usa
la etiqueta estática (``wx.StaticText``) que está INMEDIATAMENTE ANTES en el
orden Z de las ventanas hermanas, que en wxPython es el ORDEN DE CREACIÓN. Por
eso la regla de oro es: **crear la etiqueta justo antes que su control, con
el mismo padre**. El orden en el sizer no influye. Comprobado con MSAA:

- ``name=`` y ``SetName()`` NO dan nombre accesible a un control nativo (sirven
  para Ctrl+H y para depurar, pero el lector no los ve).
- ``MoveBeforeInTabOrder``/``MoveAfterInTabOrder`` cambian el orden de Tab de
  wx pero NO el orden Z: no arreglan una etiqueta creada tarde.
- Una etiqueta oculta (``Hide()``) sigue dando nombre; ``SetLabel`` lo cambia.
- Un control creado justo después de un ``wx.StaticBox`` HERMANO sin etiqueta
  propia recibe como nombre el título del grupo (trampa). Use el estilo
  moderno: controles hijos de ``sizer.GetStaticBox()``, cada uno con etiqueta.
- Las ventanas genéricas (``wx.Panel``, páginas de Notebook, HtmlWindow...)
  se anuncian con su ``name=`` del CONSTRUCTOR o con ``SetLabel()`` posterior.

Este módulo ofrece:

- ``crear_campo``: crea etiqueta + control en el orden correcto, con nombre,
  ayuda (Ctrl+H) y acelerador (``&Nombre:`` → Alt+N lleva al campo).
- ``asignar_nombre_accesible``: fuerza el nombre MSAA con ``wx.Accessible``
  cuando ``SetName`` no llega al lector (campo interno de los selectores).
- ``etiquetar_selector``: nombre para el campo interno de ``DirPickerCtrl`` /
  ``FilePickerCtrl`` y botón «Seleccionar...» en español.
- ``nombre_limpio``: quita ``&`` y los dos puntos finales de una etiqueta.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import TypeVar

import wx

C = TypeVar("C", bound=wx.Window)


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def nombre_limpio(etiqueta: str) -> str:
	"""«&Carpeta de destino:» → «Carpeta de destino»."""
	return etiqueta.replace("&&", "\0").replace("&", "").replace("\0", "&").strip().rstrip(":").strip()


class _AccesibleConNombre(wx.Accessible):
	"""Objeto MSAA que expone un nombre (y opcionalmente una descripción) fijos."""

	def __init__(self, ventana: wx.Window, nombre: str, descripcion: str = "") -> None:
		super().__init__(ventana)
		self._nombre = nombre
		self._descripcion = descripcion

	def GetName(self, childId: int):  # noqa: N802,N803 - API de wx
		if childId == 0:
			return (wx.ACC_OK, self._nombre)
		return (wx.ACC_NOT_IMPLEMENTED, "")

	def GetDescription(self, childId: int):  # noqa: N802,N803 - API de wx
		if childId == 0 and self._descripcion:
			return (wx.ACC_OK, self._descripcion)
		return (wx.ACC_NOT_IMPLEMENTED, "")


def asignar_nombre_accesible(
	control: wx.Window, nombre: str, descripcion: str = "", ref_persistente: wx.Window | None = None
) -> None:
	"""Fija el nombre que anunciará el lector, aunque ``SetName`` no le llegue.

	La referencia al objeto ``wx.Accessible`` se guarda en ``ref_persistente``
	(o en el propio control): si la recogiera el recolector de basura, el
	nombre volvería a quedar vacío. Use ``ref_persistente`` cuando ``control``
	sea una envoltura temporal (``selector.GetTextCtrl()``).
	"""
	control.SetName(nombre)
	accesible = _AccesibleConNombre(control, nombre, descripcion)
	destino = ref_persistente or control
	referencias = getattr(destino, "_referencias_accesibles", None)
	if referencias is None:
		referencias = []
		destino._referencias_accesibles = referencias
	referencias.append(accesible)
	control.SetAccessible(accesible)


def crear_campo(
	padre: wx.Window,
	etiqueta: str,
	fabrica: Callable[..., C],
	*args,
	ayuda: str = "",
	sizer: wx.Sizer | None = None,
	horizontal: bool = False,
	expandir: bool = True,
	**kwargs,
) -> tuple[wx.StaticText, C]:
	"""Crea una etiqueta y, JUSTO DESPUÉS, su control, con nombre y ayuda.

	::

		_, self.campo_nombre = crear_campo(panel, _("&Nombre:"), wx.TextCtrl, ayuda=_("Nombre del perfil."), sizer=sizer)
		_, self.lista_formato = crear_campo(panel, _("&Formato:"), wx.Choice, choices=["MP3", "OGG"], sizer=sizer)

	:param etiqueta: texto visible; con ``&`` para el acelerador (Alt+letra lleva al control).
	:param fabrica: clase del control (``wx.TextCtrl``, ``wx.Choice``...) o función que lo crea.
	:param ayuda: texto para Ctrl+H (requiere ``wx.HelpProvider`` instalado).
	:param sizer: si se indica, añade etiqueta y control en el orden correcto.
	:param horizontal: etiqueta a la izquierda (True) o encima (False) del control.
	"""
	texto_etiqueta = wx.StaticText(padre, label=etiqueta)
	kwargs.setdefault("name", nombre_limpio(etiqueta))
	control = fabrica(padre, *args, **kwargs)
	if ayuda:
		control.SetHelpText(ayuda)
	if sizer is not None:
		if horizontal:
			fila = wx.BoxSizer(wx.HORIZONTAL)
			fila.Add(texto_etiqueta, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
			fila.Add(control, 1 if expandir else 0, wx.EXPAND if expandir else 0)
			sizer.Add(fila, 0, wx.EXPAND | wx.ALL, 5)
		else:
			sizer.Add(texto_etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
			sizer.Add(control, 0, (wx.EXPAND if expandir else 0) | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
	return texto_etiqueta, control


def etiquetar_selector(selector: wx.Window, nombre: str, etiqueta_boton: str = "") -> None:
	"""Nombre accesible para el campo interno de un selector y botón en español.

	Al tabular, el foco cae en el campo de texto interno de
	``DirPickerCtrl``/``FilePickerCtrl``, que no hereda el nombre del
	selector: sin esto el lector dice solo «editar».

	AVISO: esta técnica funciona en algunas versiones, pero una prueba MSAA con
	wxPython 4.2.3 no vio el nombre en el campo interno. Compruébelo con Inspect o NVDA; si no
	funciona en su versión, use ``crear_selector_ruta`` (campo + botón propios,
	como hace NVDA en su interfaz).
	"""
	campo = selector.GetTextCtrl() if hasattr(selector, "GetTextCtrl") else None
	if campo is not None:
		asignar_nombre_accesible(campo, nombre, ref_persistente=selector)
	texto = etiqueta_boton or _("&Seleccionar...")
	for hijo in selector.GetChildren():
		if isinstance(hijo, wx.Button):
			hijo.SetLabel(texto)
			break


def crear_selector_ruta(
	padre: wx.Window,
	etiqueta: str,
	carpeta: bool = True,
	valor: str = "",
	comodin: str = "*.*",
	ayuda: str = "",
	sizer: wx.Sizer | None = None,
) -> tuple[wx.StaticText, wx.TextCtrl, wx.Button]:
	"""Selector de carpeta o archivo accesible: etiqueta + campo + «Examinar...».

	Alternativa a ``DirPickerCtrl``/``FilePickerCtrl`` (su campo interno queda
	sin nombre en algunas versiones). El campo toma la etiqueta por estar
	creado justo después; el botón abre el diálogo nativo de Windows.
	"""
	texto, campo = crear_campo(padre, etiqueta, wx.TextCtrl, value=valor, ayuda=ayuda)
	boton = wx.Button(padre, label=_("E&xaminar..."))
	boton.SetHelpText(_("Abre un cuadro para elegir la carpeta.") if carpeta else _("Abre un cuadro para elegir el archivo."))

	def al_examinar(_evento: wx.CommandEvent) -> None:
		titulo = nombre_limpio(etiqueta)
		if carpeta:
			dialogo = wx.DirDialog(padre, titulo, campo.GetValue(), style=wx.DD_DEFAULT_STYLE)
		else:
			dialogo = wx.FileDialog(padre, titulo, wildcard=comodin, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
		try:
			if dialogo.ShowModal() == wx.ID_OK:
				campo.SetValue(dialogo.GetPath())
		finally:
			dialogo.Destroy()
		campo.SetFocus()

	boton.Bind(wx.EVT_BUTTON, al_examinar)
	if sizer is not None:
		sizer.Add(texto, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		fila.Add(campo, 1, wx.EXPAND | wx.RIGHT, 5)
		fila.Add(boton, 0)
		sizer.Add(fila, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
	return texto, campo, boton


def nombrar_campo_interno(control: wx.Window, nombre: str) -> None:
	"""Da nombre al campo de texto interno de controles compuestos genéricos.

	``wx.SpinCtrlDouble`` y ``wx.SearchCtrl`` son genéricos en Windows: su
	campo interno (el que recibe el foco) queda sin nombre aunque haya
	etiqueta delante. Una prueba MSAA confirmó que ``SetAccessible`` sobre ese
	campo sí funciona.
	"""
	for hijo in control.GetChildren():
		if isinstance(hijo, wx.TextCtrl):
			asignar_nombre_accesible(hijo, nombre, ref_persistente=control)


def reordenar_tabulacion(controles: list[wx.Window]) -> None:
	"""Fuerza el orden de tabulación de una lista de controles hermanos.

	OJO: solo cambia la navegación con Tab de wx, no el orden Z nativo, así
	que NO cambia qué etiqueta nombra a cada control. Si el orden de creación
	está mal, lo correcto es crear de nuevo etiqueta y control en orden.
	"""
	for anterior, siguiente in zip(controles, controles[1:], strict=False):
		siguiente.MoveAfterInTabOrder(anterior)
