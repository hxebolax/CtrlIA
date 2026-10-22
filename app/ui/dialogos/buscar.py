"""Diálogo «Buscar en todas las sesiones».

- Campo «Buscar» (Intro busca) y botón que busca o, durante la búsqueda, la
  detiene. Escape durante la búsqueda la detiene sin cerrar el diálogo.
- La búsqueda corre en un hilo aparte (``threading.Thread`` daemon) y vuelve
  con ``wx.CallAfter``: la interfaz nunca se queda bloqueada.
- Se anuncia «Buscando…» al empezar y «N resultados» (o «No hay
  resultados») al terminar.
- Intro en un resultado deja ``self.resultado = (clave, mensaje)`` y cierra
  con ``wx.ID_OK``.
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from collections.abc import Callable

import wx

from app.services.gestor import MensajeVista
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import mostrar_error, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import copiar_texto, menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _, fecha_relativa

log = logging.getLogger(__name__)

#: Máximo de resultados que se muestran.
MAX_RESULTADOS = 500
#: Longitud aproximada del fragmento que se muestra de cada mensaje.
ANCHO_FRAGMENTO = 200

FuncionBuscar = Callable[[str, Callable[[], bool]], list[tuple[str, str, MensajeVista]]]


def _normalizado_con_posiciones(texto: str) -> tuple[str, list[int]]:
	"""Texto en minúsculas y sin acentos, y la posición original de cada carácter."""
	caracteres: list[str] = []
	posiciones: list[int] = []
	for indice, caracter in enumerate(texto):
		descompuesto = unicodedata.normalize("NFD", caracter.casefold())
		for parte in descompuesto:
			if unicodedata.category(parte) != "Mn":
				caracteres.append(parte)
				posiciones.append(indice)
	return "".join(caracteres), posiciones


def _primera_coincidencia(texto: str, consulta: str) -> int:
	"""Posición (en ``texto``) de la primera palabra de ``consulta`` que aparece; -1 si ninguna."""
	normalizado, posiciones = _normalizado_con_posiciones(texto)
	consulta_n, _posiciones = _normalizado_con_posiciones(consulta)
	encontradas = [normalizado.find(palabra) for palabra in consulta_n.split()]
	encontradas = [p for p in encontradas if p >= 0]
	return posiciones[min(encontradas)] if encontradas else -1


def fragmento(texto: str, consulta: str, ancho: int = ANCHO_FRAGMENTO) -> str:
	"""Unos ``ancho`` caracteres de ``texto`` alrededor de la primera coincidencia.

	Sin saltos de línea ni espacios repetidos; con «…» si se recorta por
	delante o por detrás. Sin coincidencia, el principio del texto.
	"""
	limpio = " ".join((texto or "").split())
	if len(limpio) <= ancho:
		return limpio
	posicion = max(0, _primera_coincidencia(limpio, consulta))
	inicio = max(0, posicion - ancho // 3)
	fin = min(len(limpio), inicio + ancho)
	inicio = max(0, fin - ancho)
	if inicio > 0:
		espacio = limpio.find(" ", inicio, posicion if posicion > inicio else fin)
		if espacio != -1:
			inicio = espacio + 1
	if fin < len(limpio):
		espacio = limpio.rfind(" ", max(inicio, posicion), fin)
		if espacio > inicio:
			fin = espacio
	resultado = limpio[inicio:fin].strip()
	return ("…" if inicio > 0 else "") + resultado + ("…" if fin < len(limpio) else "")


def fragmento_mensaje(mensaje: MensajeVista, consulta: str, ancho: int = ANCHO_FRAGMENTO) -> str:
	"""Fragmento del texto del mensaje (o de su detalle si la coincidencia está ahí)."""
	texto = mensaje.texto or ""
	if _primera_coincidencia(texto, consulta) < 0 and _primera_coincidencia(mensaje.detalle or "", consulta) >= 0:
		texto = mensaje.detalle
	return fragmento(texto, consulta, ancho)


class DialogoBuscarTodo(wx.Dialog):
	"""Busca un texto en todas las sesiones y permite saltar al mensaje encontrado."""

	def __init__(self, padre: wx.Window | None, buscar: FuncionBuscar, consulta_inicial: str = "") -> None:
		titulo = _("Buscar en todas las sesiones")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._buscar = buscar
		self.resultado: tuple[str, MensajeVista] | None = None
		self.resultados: list[tuple[str, str, MensajeVista]] = []
		self.buscando = False
		self.consulta = ""
		self._generacion = 0
		self._cancelacion: threading.Event | None = None
		self._hilo: threading.Thread | None = None

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		_etiqueta, self.campo = crear_campo(
			panel, _("&Buscar:"), wx.TextCtrl, value=consulta_inicial, style=wx.TE_PROCESS_ENTER,
			ayuda=_(
				"Texto que se busca en todas las sesiones, sin distinguir mayúsculas ni acentos; deben aparecer "
				"todas las palabras. Intro busca."
			),
			sizer=fila, horizontal=True,
		)
		# «B&uscar» y no «&Buscar»: Alt+B ya lleva al campo.
		self.boton_buscar = wx.Button(panel, label=_("B&uscar"))
		self.boton_buscar.SetHelpText(_("Busca el texto escrito. Durante la búsqueda, la detiene."))
		fila.Add(self.boton_buscar, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		sizer.Add(fila, 0, wx.EXPAND)

		self.etiqueta_resultados = wx.StaticText(panel, label=_("&Resultados:"))
		self.lista = ListaAccesible(
			panel, _("Resultados"), [(_("Sesión"), 200), (_("Quién"), 90), (_("Fragmento"), 460), (_("Cuándo"), 140)],
		)
		self.lista.SetHelpText(_(
			"Mensajes que contienen el texto buscado. Intro cierra el diálogo y lleva a ese mensaje en su sesión."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(760, 320)))
		sizer.Add(self.etiqueta_resultados, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra la búsqueda sin ir a ningún mensaje."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
		panel.SetSizer(sizer)

		self.campo.Bind(wx.EVT_TEXT_ENTER, lambda _evento: self.buscar())
		self.boton_buscar.Bind(wx.EVT_BUTTON, self._al_pulsar_boton)
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.abrir_resultado())
		vincular_menu_contextual(self.lista, self._crear_menu)
		self.Bind(wx.EVT_WINDOW_DESTROY, self._al_destruir)

		preparar_dialogo(self, foco_inicial=self.campo)
		# Después de preparar_dialogo: este manejador se atiende antes que el suyo.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)

		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		if consulta_inicial.strip():
			wx.CallAfter(self.buscar)

	# ── Búsqueda ──────────────────────────────────────────────────────────────

	def buscar(self) -> bool:
		"""Empieza a buscar el texto del campo (cancela la búsqueda anterior si la hay)."""
		try:
			if not self:
				return False
		except RuntimeError:
			return False
		consulta = self.campo.GetValue().strip()
		if not consulta:
			voz.hablar(_("Escribe el texto que quieres buscar"))
			self._enfocar(self.campo)
			return False
		self._cancelar_hilo()
		self._generacion += 1
		generacion = self._generacion
		cancelacion = threading.Event()
		self._cancelacion = cancelacion
		self.consulta = consulta
		self._marcar_buscando(True)
		voz.hablar(_("Buscando…"))
		self._hilo = threading.Thread(
			target=self._trabajar, args=(consulta, cancelacion, generacion), name="buscar-todo", daemon=True,
		)
		self._hilo.start()
		return True

	def _trabajar(self, consulta: str, cancelacion: threading.Event, generacion: int) -> None:
		"""Hilo de búsqueda: no toca la interfaz; vuelve con ``wx.CallAfter``."""
		error = ""
		try:
			resultados = list(self._buscar(consulta, cancelacion.is_set) or [])
		except Exception as excepcion:
			log.exception("Error al buscar %r en todas las sesiones", consulta)
			resultados, error = [], str(excepcion) or excepcion.__class__.__name__
		wx.CallAfter(self._al_terminar, generacion, resultados, cancelacion.is_set(), error)

	def _al_terminar(self, generacion: int, resultados: list[tuple[str, str, MensajeVista]], cancelada: bool,
			error: str) -> None:
		try:
			if not self or generacion != self._generacion:
				return  # diálogo cerrado o búsqueda sustituida por otra más nueva
		except RuntimeError:
			return
		self._marcar_buscando(False)
		self._hilo = None
		if error:
			mostrar_error(self, _("No se pudo completar la búsqueda:\n{error}").format(error=error))
			return
		self.mostrar_resultados(resultados[:MAX_RESULTADOS])
		total = len(self.resultados)
		if cancelada:
			mensaje = _("Búsqueda detenida. {n} resultados").format(n=total) if total != 1 else _("Búsqueda detenida. 1 resultado")
		elif not total:
			mensaje = _("No hay resultados")
		elif total == 1:
			mensaje = _("1 resultado")
		elif len(resultados) > MAX_RESULTADOS:
			mensaje = _("Más de {n} resultados; se muestran los {n} primeros").format(n=MAX_RESULTADOS)
		else:
			mensaje = _("{n} resultados").format(n=total)
		voz.hablar(mensaje)

	def mostrar_resultados(self, resultados: list[tuple[str, str, MensajeVista]]) -> None:
		"""Rellena la lista con ``(clave, título de la sesión, mensaje)``."""
		self.resultados = list(resultados)
		filas = [
			[titulo, _(mensaje.nombre_rol), fragmento_mensaje(mensaje, self.consulta), fecha_relativa(mensaje.fecha)]
			for _clave, titulo, mensaje in self.resultados
		]
		self.lista.reemplazar_filas(filas, conservar_foco=False)
		self.etiqueta_resultados.SetLabel(_("&Resultados ({n}):").format(n=len(filas)))

	def cancelar(self) -> bool:
		"""Detiene la búsqueda en curso. Devuelve si había una."""
		if not self.buscando:
			return False
		self._cancelar_hilo()
		voz.hablar(_("Deteniendo la búsqueda"))
		return True

	def _cancelar_hilo(self) -> None:
		if self._cancelacion is not None:
			self._cancelacion.set()

	def _marcar_buscando(self, valor: bool) -> None:
		self.buscando = valor
		self.boton_buscar.SetLabel(_("&Detener") if valor else _("B&uscar"))

	# ── Acciones ──────────────────────────────────────────────────────────────

	def abrir_resultado(self) -> bool:
		"""Elige el resultado seleccionado: ``self.resultado`` y cierra con ``wx.ID_OK``."""
		fila = self.lista.fila_actual()
		if not 0 <= fila < len(self.resultados):
			voz.hablar(_("No hay ningún resultado seleccionado"))
			return False
		clave, _titulo, mensaje = self.resultados[fila]
		self.resultado = (clave, mensaje)
		self._cancelar_hilo()
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True

	def _crear_menu(self) -> wx.Menu | None:
		"""Menú contextual del resultado: ir al mensaje o copiar."""
		fila = self.lista.fila_actual()
		if not 0 <= fila < len(self.resultados):
			return None
		_clave, titulo, mensaje = self.resultados[fila]
		return menu_de_acciones(self.lista, [
			(_("&Ir al mensaje\tIntro"), self.abrir_resultado),
			(_("&Copiar el mensaje"), lambda: copiar_texto(mensaje.texto)),
			(_("Copiar el &título de la sesión"), lambda: copiar_texto(titulo)),
		])

	@staticmethod
	def _enfocar(control: wx.Window) -> None:
		"""Foco solo si se ve: un control oculto no debe robar el foco a nadie."""
		if control.IsShownOnScreen():
			control.SetFocus()

	# ── Eventos ───────────────────────────────────────────────────────────────

	def _al_pulsar_boton(self, _evento: wx.CommandEvent) -> None:
		if self.buscando:
			self.cancelar()
		else:
			self.buscar()

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		# Escape con una búsqueda en marcha la detiene; si no, cierra (preparar_dialogo).
		if evento.GetKeyCode() == wx.WXK_ESCAPE and evento.GetModifiers() == wx.MOD_NONE and self.buscando:
			self.cancelar()
			return
		evento.Skip()

	def _al_destruir(self, evento: wx.WindowDestroyEvent) -> None:
		if evento.GetEventObject() is self:
			self._cancelar_hilo()
		evento.Skip()
