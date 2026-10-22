"""Ayuda contextual con Ctrl+H: «¿qué es y para qué sirve el control enfocado?».

- ``describir_control_enfocado``: anuncia el nombre y la ayuda del control con
  el foco. El nombre sale del ``name=`` propio; si es genérico de wx
  («text», «button»...), de su etiqueta visible; y si no tiene, de la
  ``StaticText`` anterior entre sus hermanos (la misma heurística que usan
  los lectores de pantalla).
- ``ayuda_efectiva``: la ayuda propia (``SetHelpText``), la del padre (campo
  interno de un selector), el tooltip o, como último recurso, una ayuda
  genérica según el tipo de control (y, en botones, según su etiqueta).
- ``asegurar_ayudas``: rellena la ayuda de los controles que no la tengan,
  sin pisar las escritas a mano.

Requisito: ``wx.HelpProvider.Set(wx.SimpleHelpProvider())`` al arrancar; sin
él, ``SetHelpText`` no guarda nada.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins
import unicodedata

import wx
import wx.adv

from app.ui.accesibilidad import voz


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


# Nombres que wx pone por defecto y que no dicen nada al usuario.
_NOMBRES_GENERICOS = {
	"", "check", "checkbox", "button", "text", "textctrl", "choice", "combobox", "combo", "listctrl",
	"listbox", "list", "panel", "statictext", "slider", "radiobutton", "radiobox", "notebook", "treectrl",
	"spinctrl", "wxspinctrl", "toggle", "groupbox", "scrolledpanel", "dialog", "frame", "gauge",
	"datectrl", "timectrl", "filepicker", "dirpicker", "htmlwindow", "grid", "staticbitmap",
}

# Ayuda por palabra clave de la etiqueta de un botón (lo más específico primero).
_AYUDA_POR_ETIQUETA: tuple[tuple[tuple[str, ...], str], ...] = (
	(("seleccionar todo", "marcar todo", "marcar todas", "marcar todos"), "Marca todos los elementos de la lista."),
	(("desmarcar", "ninguno", "ninguna"), "Desmarca todos los elementos de la lista."),
	(("examinar", "seleccionar...", "elegir"), "Abre un cuadro para elegir un archivo o una carpeta."),
	(("vista previa", "previsualizar"), "Muestra una vista previa antes de aplicar los cambios."),
	(("restablecer", "restaurar"), "Vuelve a los valores de fábrica."),
	(("comprobar", "probar", "verificar", "detectar"), "Comprueba el estado y muestra el resultado, sin cambiar nada."),
	(("actualizar", "refrescar", "recargar"), "Vuelve a cargar la información más reciente."),
	(("copiar",), "Copia la información al portapapeles."),
	(("exportar",), "Guarda la información en un archivo."),
	(("importar",), "Carga información desde un archivo."),
	(("abrir carpeta", "abrir la carpeta"), "Abre la carpeta en el Explorador de Windows."),
	(("abrir",), "Abre el elemento seleccionado."),
	(("detalles", "propiedades", "ficha"), "Muestra la información completa del elemento seleccionado."),
	(("agregar", "añadir", "nuevo", "nueva"), "Crea un elemento nuevo."),
	(("editar", "modificar"), "Edita el elemento seleccionado."),
	(("eliminar", "borrar", "quitar"), "Elimina el elemento seleccionado; pide confirmación."),
	(("buscar",), "Busca con el texto y los filtros indicados."),
	(("guardar", "aplicar"), "Guarda los cambios realizados."),
	(("aceptar",), "Acepta los cambios y cierra la ventana."),
	(("cancelar",), "Cierra la ventana sin guardar los cambios."),
	(("cerrar",), "Cierra esta ventana."),
	(("ayuda",), "Muestra la ayuda."),
)


def _normalizar(texto: str) -> str:
	"""Minúsculas, sin «&», sin acentos y sin puntos suspensivos."""
	limpio = texto.replace("&", "").replace("…", "").replace("...", "").strip().lower()
	return "".join(c for c in unicodedata.normalize("NFD", limpio) if unicodedata.category(c) != "Mn")


def _ayuda_por_etiqueta(control: wx.Window) -> str:
	etiqueta = _normalizar(control.GetLabel())
	for claves, ayuda in _AYUDA_POR_ETIQUETA:
		if any(_normalizar(clave) in etiqueta for clave in claves):
			return _(ayuda)
	return ""


def ayuda_generica(control: wx.Window) -> str:
	"""Ayuda de reserva según el tipo de control (el orden importa: subclases primero)."""
	if isinstance(control, wx.Button):
		return _ayuda_por_etiqueta(control) or _("Botón. Actívelo con la barra espaciadora o Intro.")
	if isinstance(control, wx.CheckListBox):
		return _("Lista de casillas. Muévase con las flechas y marque o desmarque con la barra espaciadora.")
	if isinstance(control, wx.CheckBox):
		return _("Casilla de verificación. Márquela o desmárquela con la barra espaciadora.")
	if isinstance(control, wx.RadioButton):
		return _("Botón de opción. Elija entre las opciones del grupo con las flechas.")
	if isinstance(control, (wx.SpinCtrl, wx.SpinCtrlDouble)):
		return _("Campo numérico. Escriba un número o cámbielo con las flechas arriba y abajo.")
	if isinstance(control, (wx.Choice, wx.ComboBox)):
		return _("Lista desplegable. Ábrala con Alt+flecha abajo y elija con las flechas.")
	if isinstance(control, wx.TextCtrl):
		if not control.IsEditable():
			return _("Texto de solo lectura. Recórralo con las flechas y cópielo si lo necesita.")
		return _("Campo de texto. Escriba aquí el valor.")
	if isinstance(control, wx.ListCtrl):
		return _(
			"Lista. Arriba y abajo cambian de elemento; izquierda y derecha recorren las columnas; "
			"Control más un número lee esa columna; Intro abre el elemento."
		)
	if isinstance(control, wx.ListBox):
		return _("Lista de opciones. Elija con las flechas.")
	if isinstance(control, wx.TreeCtrl):
		return _("Árbol. Muévase con las flechas; derecha despliega una rama e izquierda la pliega.")
	if isinstance(control, (wx.DirPickerCtrl, wx.FilePickerCtrl)):
		return _("Selector de ruta. Escriba la ruta o use el botón Seleccionar.")
	if isinstance(control, wx.adv.DatePickerCtrl):
		return _("Selector de fecha. Cambie día, mes y año con las flechas.")
	if isinstance(control, wx.adv.TimePickerCtrl):
		return _("Selector de hora. Cambie horas y minutos con las flechas.")
	if isinstance(control, wx.Slider):
		return _("Control deslizante. Cambie el valor con las flechas; Re Pág y Av Pág dan saltos mayores.")
	if isinstance(control, wx.Gauge):
		return _("Barra de progreso. Indica cuánto se ha completado.")
	if isinstance(control, wx.Notebook):
		return _("Pestañas. Cambie de pestaña con Control+Tab o con las flechas.")
	return ""


def ayuda_efectiva(control: wx.Window) -> str:
	"""La ayuda más específica disponible para ``control``."""
	ayuda = (getattr(control, "ayuda_texto", "") or "").strip()
	if not ayuda:
		ayuda = (control.GetHelpText() or "").strip()
	if not ayuda:
		padre = control.GetParent()
		if padre is not None and isinstance(padre, (wx.DirPickerCtrl, wx.FilePickerCtrl, wx.SpinCtrl)):
			ayuda = (padre.GetHelpText() or "").strip()
	if not ayuda:
		consejo = control.GetToolTip()
		ayuda = (consejo.GetTip() if consejo else "").strip()
	return ayuda or ayuda_generica(control)


def nombre_para_anunciar(control: wx.Window) -> str:
	"""Nombre del control tal y como lo presentaría un lector de pantalla."""
	nombre = (control.GetName() or "").strip()
	if nombre.lower() in _NOMBRES_GENERICOS:
		nombre = (control.GetLabel() or "").replace("&", "").strip()
	if nombre.lower() in _NOMBRES_GENERICOS and control.GetParent() is not None:
		hermanos = list(control.GetParent().GetChildren())
		if control in hermanos:
			for previo in reversed(hermanos[: hermanos.index(control)]):
				if isinstance(previo, wx.StaticText):
					nombre = previo.GetLabel().replace("&", "").strip().rstrip(":")
					break
	return "" if nombre.lower() in _NOMBRES_GENERICOS else nombre


def describir_control_enfocado(ventana: wx.Window | None = None) -> None:
	"""Acción de Ctrl+H: dice el nombre y la ayuda del control con el foco."""
	control = wx.Window.FindFocus()
	if control is None:
		voz.hablar(_("No hay ningún control con el foco."), canal=voz.NAVEGACION)
		return
	nombre = nombre_para_anunciar(control) or _("Este control")
	ayuda = ayuda_efectiva(control)
	if ayuda:
		voz.hablar(_("{nombre}. {ayuda}").format(nombre=nombre, ayuda=ayuda), canal=voz.NAVEGACION)
	else:
		voz.hablar(_("{nombre}. No hay ayuda para este control.").format(nombre=nombre), canal=voz.NAVEGACION)


_INTERACTIVOS = (
	wx.TextCtrl, wx.Choice, wx.ComboBox, wx.SpinCtrl, wx.SpinCtrlDouble, wx.CheckBox, wx.RadioButton,
	wx.ListBox, wx.ListCtrl, wx.TreeCtrl, wx.Slider, wx.Button, wx.DirPickerCtrl, wx.FilePickerCtrl,
	wx.adv.DatePickerCtrl, wx.adv.TimePickerCtrl, wx.Notebook,
)


def _descendientes(ventana: wx.Window):
	for hijo in ventana.GetChildren():
		yield hijo
		yield from _descendientes(hijo)


def asegurar_ayudas(ventana: wx.Window) -> None:
	"""Pone ayuda genérica a los controles interactivos que no tengan ninguna."""
	for control in _descendientes(ventana):
		if isinstance(control, _INTERACTIVOS) and not control.GetHelpText():
			generica = ayuda_generica(control)
			if generica:
				control.SetHelpText(generica)


def habilitar_ayuda_contextual(ventana: wx.Window) -> None:
	"""Instala Ctrl+H en una ventana principal (en diálogos lo hace ``preparar_dialogo``)."""

	def al_pulsar(evento: wx.KeyEvent) -> None:
		if evento.GetModifiers() == wx.MOD_CONTROL and evento.GetKeyCode() == ord("H"):
			describir_control_enfocado(ventana)
			return
		evento.Skip()

	ventana.Bind(wx.EVT_CHAR_HOOK, al_pulsar)
