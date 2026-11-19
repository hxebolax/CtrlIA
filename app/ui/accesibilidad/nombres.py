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
- ``habilitar``: activa o desactiva un control y quita el ``&`` de su etiqueta
  mientras está desactivado (si no, Alt+letra salta al control
  siguiente).

Dos arreglos para NVDA, que se aplican solos en
``asignar_nombre_accesible``:

- Al abrir un diálogo, NVDA lee de golpe todos sus textos estáticos salvo los
  que dicen lo mismo que el control siguiente (los toma por su etiqueta). Si
  un control recibe un nombre más largo que su etiqueta («Archivos (3):» y
  «Archivos, 3 de 12»), su etiqueta se leería suelta: la etiqueta anterior
  recibe un ``wx.Accessible`` cuyo nombre es el del control, solo si la
  etiqueta (sin ``&`` ni «:») es el principio de ese nombre
  (``atar_etiqueta``). Lo que se ve no cambia.
- Una ``wx.ListBox`` se leía dos veces al llegar con Tab (Windows anuncia el
  foco en la lista y después en su fila): mientras tiene el foco, su
  accesible responde en ``GetFocus`` que lo tiene la propia lista.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins
import logging
import re
from collections.abc import Callable
from typing import TypeVar

import wx

C = TypeVar("C", bound=wx.Window)

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def nombre_limpio(etiqueta: str) -> str:
	"""«&Carpeta de destino:» → «Carpeta de destino»."""
	return etiqueta.replace("&&", "\0").replace("&", "").replace("\0", "&").strip().rstrip(":").strip()


def foco_en_la_lista(accesible: wx.Accessible):
	"""Respuesta de ``GetFocus`` que evita que NVDA lea dos veces una ``wx.ListBox`` al llegar con Tab.

	Windows avisa del foco en la lista y después en su fila; NVDA, en el primero, pregunta a la lista qué
	tiene el foco y lee la fila, y la vuelve a leer con el segundo. Mientras la lista tiene el foco se
	responde «la propia lista» (``CHILDID_SELF`` con el accesible), y la fila se lee una vez. Sin el foco,
	como Windows. wxPython llama a ``GetFocus`` sin argumentos y espera ``(estado, hijo, accesible)``.
	"""
	try:
		ventana = accesible.GetWindow()
		if ventana and wx.Window.FindFocus() is ventana:
			return (wx.ACC_OK, 0, accesible)
	except RuntimeError:
		log.debug("La lista ya no existe", exc_info=True)
	return (wx.ACC_NOT_IMPLEMENTED, 0, None)


class _AccesibleConNombre(wx.Accessible):
	"""Objeto MSAA que expone un nombre (y opcionalmente una descripción) fijos."""

	def __init__(self, ventana: wx.Window, nombre: str, descripcion: str = "", vacia: str = "") -> None:
		super().__init__(ventana)
		self._nombre = nombre
		self._descripcion = descripcion
		self._es_lista = isinstance(ventana, wx.ListBox)
		#: Lo que se lee al llegar a la lista sin filas (si no, NVDA dice «desconocido»).
		self._vacia = vacia

	def _lista_vacia(self, childId: int) -> bool:  # noqa: N803 - API de wx
		"""Si se pregunta por la fila 1 de una lista vacía: Windows le da el foco aunque no exista."""
		if not (self._es_lista and self._vacia and childId == 1):
			return False
		try:
			ventana = self.GetWindow()
			return bool(ventana) and ventana.GetCount() == 0
		except RuntimeError:
			return False

	def GetName(self, childId: int):  # noqa: N802,N803 - API de wx
		if childId == 0:
			return (wx.ACC_OK, self._nombre)
		if self._lista_vacia(childId):
			return (wx.ACC_OK, self._vacia)
		return (wx.ACC_NOT_IMPLEMENTED, "")

	def GetRole(self, childId: int):  # noqa: N802,N803 - API de wx
		if self._lista_vacia(childId):
			return (wx.ACC_OK, wx.ROLE_SYSTEM_LISTITEM)
		return (wx.ACC_NOT_IMPLEMENTED, 0)

	def GetState(self, childId: int):  # noqa: N802,N803 - API de wx
		if self._lista_vacia(childId):
			estado = wx.ACC_STATE_SYSTEM_FOCUSABLE | wx.ACC_STATE_SYSTEM_READONLY
			if wx.Window.FindFocus() is self.GetWindow():
				estado |= wx.ACC_STATE_SYSTEM_FOCUSED
			return (wx.ACC_OK, estado)
		return (wx.ACC_NOT_IMPLEMENTED, 0)

	def GetLocation(self, elementId: int):  # noqa: N802,N803 - API de wx
		if self._lista_vacia(elementId):
			return (wx.ACC_OK, self.GetWindow().GetScreenRect())
		return (wx.ACC_NOT_IMPLEMENTED, wx.Rect())

	def GetDescription(self, childId: int):  # noqa: N802,N803 - API de wx
		if childId == 0 and self._descripcion:
			return (wx.ACC_OK, self._descripcion)
		return (wx.ACC_NOT_IMPLEMENTED, "")

	def GetFocus(self):  # noqa: N802 - API de wx
		if self._es_lista:
			return foco_en_la_lista(self)
		return (wx.ACC_NOT_IMPLEMENTED, 0, None)


def _palabras(texto: str) -> str:
	return re.sub(r"[\W_]+", "", (texto or "").casefold())


def es_etiqueta_de(etiqueta: str, nombre: str) -> bool:
	"""Si la etiqueta visible («&Volumen:», «Archivos (3):») dice lo mismo que el nombre del control o su principio.

	No lo es una etiqueta que dice MÁS que el nombre («Rol, por ejemplo Arreglar:» para «Rol»): atada, se
	perderían sus palabras de más.
	"""
	corta, completo = _palabras(etiqueta.replace("&", "")), _palabras(nombre)
	return bool(corta) and completo.startswith(corta)


def nombre_accesible_de(control: wx.Window) -> str:
	"""El nombre que expone ``control`` a los lectores (el de ``asignar_nombre_accesible`` o su ``GetName``)."""
	accesible = control.GetAccessible() if hasattr(control, "GetAccessible") else None
	if isinstance(accesible, _AccesibleConNombre):
		return accesible._nombre  # noqa: SLF001 - mismo módulo
	return control.GetName()


class _AccesibleEtiqueta(wx.Accessible):
	"""Una etiqueta visible cuyo nombre MSAA es el de su control (se pregunta cada vez: sigue sus cambios)."""

	def __init__(self, etiqueta: wx.Window, control: wx.Window) -> None:
		super().__init__(etiqueta)
		self.control = control

	def GetName(self, childId: int):  # noqa: N802,N803 - API de wx
		try:
			nombre = nombre_accesible_de(self.control) if childId == 0 and self.control else ""
		except RuntimeError:
			nombre = ""
		if nombre:
			return (wx.ACC_OK, nombre)
		return (wx.ACC_NOT_IMPLEMENTED, "")


def atar_etiqueta(etiqueta: wx.StaticText, control: wx.Window) -> None:
	"""Hace que NVDA no lea ``etiqueta`` suelta al abrir el diálogo: su nombre MSAA será el de ``control``.

	La etiqueta debe estar creada justo antes que el control. Solo en Windows.
	"""
	if wx.Platform != "__WXMSW__":
		return
	existente = getattr(etiqueta, "_accesible_etiqueta", None)
	if isinstance(existente, _AccesibleEtiqueta):
		existente.control = control
		return
	try:
		accesible = _AccesibleEtiqueta(etiqueta, control)
		etiqueta.SetAccessible(accesible)
		etiqueta._accesible_etiqueta = accesible  # que no lo recoja el recolector de basura
	except (NotImplementedError, AttributeError):
		log.debug("No se pudo atar la etiqueta", exc_info=True)


def atar_etiqueta_anterior(control: wx.Window, nombre: str) -> bool:
	"""``atar_etiqueta`` con el texto estático justo anterior a ``control``, si es su etiqueta. ``True`` si se ató."""
	try:
		anterior = control.GetPrevSibling()
	except (AttributeError, RuntimeError):
		return False
	if isinstance(anterior, wx.StaticText) and es_etiqueta_de(anterior.GetLabel(), nombre):
		atar_etiqueta(anterior, control)
		return True
	return False


def preparar_lista(lista: wx.ListBox, nombre: str, vacia: str = "") -> None:
	"""Una ``wx.ListBox`` que lee su fila una sola vez y, sin filas, dice ``vacia`` en vez de «desconocido»."""
	accesible = _AccesibleConNombre(lista, nombre, vacia=vacia or _("Lista vacía"))
	referencias = getattr(lista, "_referencias_accesibles", None)
	if referencias is None:
		referencias = []
		lista._referencias_accesibles = referencias
	referencias.append(accesible)
	lista.SetAccessible(accesible)


def avisar_lista_vacia(lista: wx.ListBox) -> None:
	"""Tras quitar la última fila con el foco en la lista: que el lector diga que se ha quedado vacía."""
	if lista.GetCount() or wx.Window.FindFocus() is not lista:
		return
	try:
		wx.Accessible.NotifyEvent(wx.ACC_EVENT_OBJECT_FOCUS, lista, wx.OBJID_CLIENT, 1)
	except (AttributeError, RuntimeError):
		log.debug("No se pudo avisar de la lista vacía", exc_info=True)


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
	atar_etiqueta_anterior(control, nombre)


# ── Etiquetas de controles desactivados ─────────────────────────

def sin_acelerador(texto: str) -> str:
	"""«&Modelo:» → «Modelo:» (un «&&», que es un «&» escrito, se conserva)."""
	return texto.replace("&&", "\0").replace("&", "").replace("\0", "&&")


def etiqueta_de(control: wx.Window) -> wx.StaticText | None:
	"""La etiqueta de un control: la que guardó ``crear_campo`` o el texto estático justo anterior."""
	guardada = getattr(control, "_etiqueta_campo", None)
	if isinstance(guardada, wx.StaticText) and guardada:
		return guardada
	try:
		anterior = control.GetPrevSibling()
	except (AttributeError, RuntimeError):
		return None
	return anterior if isinstance(anterior, wx.StaticText) else None


def habilitar(control: wx.Window, activo: bool = True, etiqueta: wx.StaticText | None = None) -> None:
	"""Activa o desactiva ``control`` y, mientras está desactivado, quita el ``&`` de su etiqueta.

	Con la etiqueta «&Modelo:» y su desplegable desactivado, Alt+M llevaría al control SIGUIENTE (Windows
	salta los desactivados): sin acelerador, Alt+M no hace nada. Al activarlo, la etiqueta recupera el suyo.
	"""
	control.Enable(activo)
	etiqueta = etiqueta or etiqueta_de(control)
	if etiqueta is None:
		return
	original = getattr(etiqueta, "_etiqueta_con_acelerador", None)
	actual = etiqueta.GetLabel()
	if original is None or actual not in (original, sin_acelerador(original)):
		# La etiqueta se cambió desde fuera (con su «&» o sin él): esa es la nueva original.
		original = actual
		etiqueta._etiqueta_con_acelerador = original
	nuevo = original if activo else sin_acelerador(original)
	if actual != nuevo:
		etiqueta.SetLabel(nuevo)


def crear_campo(
	padre: wx.Window,
	etiqueta: str,
	fabrica: Callable[..., C],
	*args,
	ayuda: str = "",
	sizer: wx.Sizer | None = None,
	horizontal: bool = False,
	expandir: bool = True,
	vacia: str = "",
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
	:param vacia: en una ``wx.ListBox``, lo que se lee si no tiene filas («No hay reglas»); de fábrica, «Lista vacía».
	"""
	texto_etiqueta = wx.StaticText(padre, label=etiqueta)
	kwargs.setdefault("name", nombre_limpio(etiqueta))
	control = fabrica(padre, *args, **kwargs)
	control._etiqueta_campo = texto_etiqueta  # para ``habilitar``
	if type(control) is wx.ListBox:  # Las de casillas tienen su propio accesible (casillas.py).
		preparar_lista(control, kwargs["name"], vacia)
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
	etiqueta_boton: str = "",
) -> tuple[wx.StaticText, wx.TextCtrl, wx.Button]:
	"""Selector de carpeta o archivo accesible: etiqueta + campo + «Examinar...».

	Alternativa a ``DirPickerCtrl``/``FilePickerCtrl`` (su campo interno queda
	sin nombre en algunas versiones). El campo toma la etiqueta por estar
	creado justo después; el botón abre el diálogo nativo de Windows.

	:param etiqueta_boton: otra etiqueta para el botón, si su ``&`` choca en esa ventana.
	"""
	texto, campo = crear_campo(padre, etiqueta, wx.TextCtrl, value=valor, ayuda=ayuda)
	boton = wx.Button(padre, label=etiqueta_boton or _("E&xaminar..."))
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
