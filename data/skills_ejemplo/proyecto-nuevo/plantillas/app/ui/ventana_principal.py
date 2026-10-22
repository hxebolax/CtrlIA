"""Ventana principal accesible.

Qué resuelve este esqueleto (y por qué):

- Barra de menús completa en español (Archivo, Editar, Ver, Herramientas,
  Ayuda) con ``&`` y atajos en la etiqueta (``"&Abrir...\\tCtrl+O"``): el
  lector anuncia el atajo al recorrer el menú y así se descubre.
- Un control principal (``ListCtrl`` en modo informe) con su etiqueta creada
  JUSTO ANTES y el foco inicial en él.
- F1 manual, Ctrl+F1 teclas, Ctrl+H ayuda del control enfocado.
- Menú contextual con la tecla Aplicaciones o Mayús+F10 (``EVT_CONTEXT_MENU``),
  colocado sobre el elemento seleccionado y no donde esté el ratón.
- Escape NO cierra la ventana principal (solo los diálogos); Alt+F4 sí.
- Tamaño, posición y estado maximizado recordados en la configuración, sin
  restaurar la ventana fuera de pantalla si cambió el monitor.
- Bandeja opcional (minimizar o cerrar a la bandeja) y respuesta a una segunda
  instancia (buzón revisado con un ``wx.Timer``).
- Trabajo pesado en hilos; los controles solo se tocan en el hilo principal
  (``wx.CallAfter``), y al cerrar se detienen las tareas y se guarda todo.
"""

from __future__ import annotations

import builtins
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import wx

from app.core import paths, voz
from app.core.config import Configuracion
from app.core.instancia_unica import ORDEN_ABRIR, ORDEN_MOSTRAR, InstanciaUnica, traer_al_frente
from app.core.version import NOMBRE_APLICACION
from app.ui.bandeja import IconoBandeja
from app.ui.dialogo_texto import copiar_al_portapapeles, mostrar_documento, mostrar_error, mostrar_mensaje

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


# Cada cuánto mira la ventana si una segunda instancia le ha dejado órdenes.
INTERVALO_BUZON_MS = 500
# Tamaño mínimo aceptable al restaurar (evita ventanas de 0x0 por datos raros).
TAMANO_MINIMO = (400, 300)

# Definición de los menús: (título, [(nombre, etiqueta, ayuda, tipo) | None]).
# ``nombre`` es el sufijo del manejador ``_al_<nombre>``; ``None`` es un separador.
# Los atajos van en inglés (Ctrl, Shift, Alt) porque así los interpreta wx; en
# pantalla, con el idioma español activo, wx los muestra traducidos.
MENUS: list[tuple[str, list[tuple[str, str, str, int] | None]]] = [
	(
		"&Archivo",
		[
			("abrir", "&Abrir...\tCtrl+O", "Añade uno o varios archivos a la lista.", wx.ITEM_NORMAL),
			None,
			("salir", "&Salir", "Cierra la aplicación aunque esté activada la opción de cerrar a la bandeja.", wx.ITEM_NORMAL),
		],
	),
	(
		"&Editar",
		[
			("copiar", "&Copiar\tCtrl+C", "Copia el texto seleccionado o los elementos seleccionados de la lista.", wx.ITEM_NORMAL),
			(
				"seleccionar_todo",
				"Seleccionar &todo\tCtrl+A",
				"Selecciona todo el texto o todos los elementos de la lista.",
				wx.ITEM_NORMAL,
			),
			None,
			(
				"quitar",
				"&Quitar de la lista",
				"Quita de la lista los elementos seleccionados (también con la tecla Suprimir).",
				wx.ITEM_NORMAL,
			),
		],
	),
	(
		"&Ver",
		[
			("barra_estado", "&Barra de estado", "Muestra u oculta la barra de estado.", wx.ITEM_CHECK),
			None,
			("actualizar", "&Actualizar\tF5", "Vuelve a cargar la lista.", wx.ITEM_NORMAL),
		],
	),
	(
		"&Herramientas",
		[
			("opciones", "&Opciones...\tCtrl+,", "Abre las opciones de la aplicación.", wx.ITEM_NORMAL),
			None,
			("carpeta_datos", "Abrir la carpeta de &datos", "Abre en el Explorador la carpeta data de la aplicación.", wx.ITEM_NORMAL),
			("carpeta_registros", "Abrir la carpeta de &registros", "Abre en el Explorador la carpeta de registros.", wx.ITEM_NORMAL),
		],
	),
	(
		"Ay&uda",
		[
			("manual", "&Manual de usuario\tF1", "Abre el manual de usuario.", wx.ITEM_NORMAL),
			("teclas", "&Lista de teclas\tCtrl+F1", "Muestra todos los atajos de teclado.", wx.ITEM_NORMAL),
			("novedades", "&Novedades de la versión", "Muestra los cambios de cada versión.", wx.ITEM_NORMAL),
			(
				"ayuda_contextual",
				"Ayuda del control &enfocado\tCtrl+H",
				"Explica para qué sirve el control que tiene el foco.",
				wx.ITEM_NORMAL,
			),
			None,
			("acerca_de", "&Acerca de {nombre}...", "Muestra la versión y los datos técnicos de la aplicación.", wx.ITEM_NORMAL),
		],
	),
]


class VentanaPrincipal(wx.Frame):
	"""Ventana principal con menús, lista, barra de estado y bandeja opcional."""

	def __init__(self, configuracion: Configuracion, instancia: InstanciaUnica | None = None) -> None:
		super().__init__(None, title=NOMBRE_APLICACION)
		self._config = configuracion
		self._instancia = instancia
		self._bandeja: IconoBandeja | None = None
		self._saliendo = False
		self._forzar_salida = False
		self._detener = threading.Event()
		self._hilos: list[threading.Thread] = []
		self._rect_normal: wx.Rect | None = None
		# Elementos de menú por nombre (para activarlos o probarlos).
		self.elementos_menu: dict[str, wx.MenuItem] = {}

		self._crear_menus()
		self._crear_contenido()
		self.CreateStatusBar()
		self.SetStatusText(_("Listo"))
		self._aplicar_barra_estado(bool(self._config.obtener("ventana.barra_estado", True)))
		self._aplicar_icono()
		self._restaurar_geometria()

		self.Bind(wx.EVT_CLOSE, self._al_cerrar)
		self.Bind(wx.EVT_ICONIZE, self._al_minimizar)
		self.Bind(wx.EVT_SIZE, self._al_cambiar_geometria)
		self.Bind(wx.EVT_MOVE, self._al_cambiar_geometria)

		self._temporizador_buzon = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self._al_revisar_buzon, self._temporizador_buzon)
		if self._instancia is not None:
			self._temporizador_buzon.Start(INTERVALO_BUZON_MS)

	# ── Construcción ─────────────────────────────────────────────────────────

	def _crear_menus(self) -> None:
		"""Crea la barra de menús a partir de ``MENUS``."""
		barra = wx.MenuBar()
		for titulo, elementos in MENUS:
			menu = wx.Menu()
			for elemento in elementos:
				if elemento is None:
					menu.AppendSeparator()
					continue
				nombre, etiqueta, ayuda, tipo = elemento
				item = menu.Append(wx.ID_ANY, _(etiqueta).format(nombre=NOMBRE_APLICACION), _(ayuda), tipo)
				self.Bind(wx.EVT_MENU, getattr(self, f"_al_{nombre}"), item)
				self.elementos_menu[nombre] = item
			barra.Append(menu, _(titulo))
		self.SetMenuBar(barra)

	def _crear_contenido(self) -> None:
		"""Panel con la lista principal.

		La ``StaticText`` se crea justo ANTES de la lista y con el mismo padre:
		el lector toma de ella el nombre de la lista.
		"""
		self.panel = wx.Panel(self)
		etiqueta = wx.StaticText(self.panel, label=_("&Elementos:"))
		self.lista = wx.ListCtrl(self.panel, style=wx.LC_REPORT)
		self.lista.InsertColumn(0, _("Nombre"), width=self.FromDIP(300))
		self.lista.InsertColumn(1, _("Carpeta"), width=self.FromDIP(400))
		self.lista.SetHelpText(
			_("Lista de elementos. Intro abre el elemento, Suprimir lo quita, la tecla Aplicaciones abre el menú contextual.")
		)
		self.lista.Bind(wx.EVT_CONTEXT_MENU, self._al_menu_contextual)
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla_lista)
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._al_activar_elemento)

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
		self.panel.SetSizer(sizer)

	def _aplicar_icono(self) -> None:
		"""Usa ``data/icons/app.ico`` si existe (barra de tareas y Alt+Tab)."""
		ruta = paths.ruta_iconos() / "app.ico"
		if ruta.is_file():
			icono = wx.Icon(str(ruta), wx.BITMAP_TYPE_ICO)
			if icono.IsOk():
				self.SetIcon(icono)

	# ── Mostrar, ocultar, bandeja ────────────────────────────────────────────

	def mostrar(self) -> None:
		"""Muestra la ventana con el foco en la lista (la llama ``main.py``)."""
		self.Show()
		self.Raise()
		wx.CallAfter(self._enfocar_lista)

	def _enfocar_lista(self) -> None:
		"""Foco en la lista y, si tiene elementos, en uno concreto.

		Una lista con foco pero sin elemento enfocado deja al lector callado.
		"""
		if not self:
			return
		self.lista.SetFocus()
		if self.lista.GetItemCount() and self.lista.GetFocusedItem() < 0:
			self.lista.Focus(0)
			self.lista.Select(0)

	def ocultar_en_bandeja(self) -> bool:
		"""Oculta la ventana dejando un icono en la bandeja. ``False`` si no hay bandeja.

		Si no se pudo poner el icono, NO se oculta: el usuario perdería el
		acceso a la ventana.
		"""
		if self._bandeja is None:
			self._bandeja = IconoBandeja(NOMBRE_APLICACION, self.restaurar, self.salir)
		if not self._bandeja.mostrar():
			log.warning("No se pudo poner el icono en la bandeja; la ventana no se oculta.")
			self._quitar_bandeja()
			return False
		self.Hide()
		voz.hablar(_("{nombre} sigue en la bandeja del sistema.").format(nombre=NOMBRE_APLICACION))
		return True

	def _quitar_bandeja(self) -> None:
		if self._bandeja is not None:
			self._bandeja.quitar()
			self._bandeja = None

	def restaurar(self) -> None:
		"""Muestra la ventana desde la bandeja, minimizada o detrás de otras.

		Si hay un diálogo modal abierto, el foco va a él: la ventana principal
		está desactivada detrás.
		"""
		if not self:
			return
		self.Show()
		if self.IsIconized():
			self.Iconize(False)
		self.Raise()
		# El icono se destruye después: quizá estamos dentro de su propio evento.
		wx.CallAfter(self._quitar_bandeja)
		dialogo = next(
			(v for v in wx.GetTopLevelWindows() if isinstance(v, wx.Dialog) and v.IsShown() and v.IsEnabled()),
			None,
		)
		if dialogo is not None:
			dialogo.Raise()
			dialogo.SetFocus()
		else:
			wx.CallAfter(self._enfocar_lista)
		if not traer_al_frente(self.GetHandle()):
			# Windows no dejó ponerla delante: al menos parpadea en la barra de tareas.
			self.RequestUserAttention()

	def salir(self) -> None:
		"""Sale de verdad, aunque esté activado «cerrar a la bandeja»."""
		self._forzar_salida = True
		self.Close()

	# ── Geometría ────────────────────────────────────────────────────────────

	def _restaurar_geometria(self) -> None:
		"""Aplica el tamaño y la posición guardados si siguen siendo visibles."""
		ancho = int(self._config.obtener("ventana.ancho", 800))
		alto = int(self._config.obtener("ventana.alto", 600))
		if ancho < TAMANO_MINIMO[0] or alto < TAMANO_MINIMO[1]:
			ancho, alto = 800, 600
		self.SetSize(wx.Size(ancho, alto))
		x = int(self._config.obtener("ventana.x", -1))
		y = int(self._config.obtener("ventana.y", -1))
		# Se comprueba un punto de la barra de título: si ese monitor ya no
		# existe, la ventana se centraría en una pantalla que nadie ve.
		if x >= -50 and y >= -50 and wx.Display.GetFromPoint(wx.Point(x + 60, y + 15)) != wx.NOT_FOUND:
			self.SetPosition(wx.Point(x, y))
		else:
			self.Centre()
		if self._config.obtener("ventana.maximizada", False):
			self.Maximize(True)

	def _al_cambiar_geometria(self, evento: wx.Event) -> None:
		"""Recuerda el rectángulo «normal» (ni maximizada ni minimizada)."""
		if self.IsShown() and not self.IsMaximized() and not self.IsIconized():
			self._rect_normal = self.GetRect()
		evento.Skip()

	def _guardar_geometria(self) -> None:
		rect = self._rect_normal or self.GetRect()
		self._config.establecer("ventana.x", rect.x)
		self._config.establecer("ventana.y", rect.y)
		self._config.establecer("ventana.ancho", rect.width)
		self._config.establecer("ventana.alto", rect.height)
		self._config.establecer("ventana.maximizada", bool(self.IsMaximized()))

	# ── Cierre ───────────────────────────────────────────────────────────────

	def _al_minimizar(self, evento: wx.IconizeEvent) -> None:
		if evento.IsIconized() and self._config.obtener("general.minimizar_a_bandeja", False):
			wx.CallAfter(self.ocultar_en_bandeja)
		evento.Skip()

	def _al_cerrar(self, evento: wx.CloseEvent) -> None:
		"""Cierre ordenado: bandeja si procede; si no, detener, guardar y destruir."""
		if (
			not self._forzar_salida
			and evento.CanVeto()
			and self._config.obtener("general.cerrar_a_bandeja", False)
			and self.ocultar_en_bandeja()
		):
			evento.Veto()
			return
		self._saliendo = True
		self._temporizador_buzon.Stop()
		self._detener_tareas()
		self._guardar_geometria()
		self._config.guardar()
		self._quitar_bandeja()
		log.info("Ventana principal cerrada.")
		self.Destroy()

	def _detener_tareas(self, espera: float = 3.0) -> None:
		"""Pide a los hilos que paren y los espera un tiempo razonable."""
		self._detener.set()
		for hilo in self._hilos:
			hilo.join(timeout=espera)
			if hilo.is_alive():
				log.warning("La tarea %s no terminó a tiempo; se abandona.", hilo.name)

	# ── Tareas en segundo plano ──────────────────────────────────────────────

	def ejecutar_en_segundo_plano(
		self,
		descripcion: str,
		trabajo: Callable[[threading.Event], Any],
		al_terminar: Callable[[Any], None],
	) -> threading.Thread:
		"""Ejecuta ``trabajo(detener)`` en un hilo y entrega el resultado en el hilo principal.

		``trabajo`` NO puede tocar controles; debe consultar ``detener.is_set()``
		en sus bucles para terminar pronto al cerrar la aplicación.
		"""

		def envoltorio() -> None:
			try:
				resultado = trabajo(self._detener)
			except Exception:  # noqa: BLE001 - se registra y se avisa en la interfaz
				log.exception("Falló la tarea «%s»", descripcion)
				wx.CallAfter(self._en_hilo_principal, self._avisar_fallo_tarea, descripcion)
				return
			wx.CallAfter(self._en_hilo_principal, al_terminar, resultado)

		self._hilos = [h for h in self._hilos if h.is_alive()]
		hilo = threading.Thread(target=envoltorio, name=f"tarea-{descripcion}", daemon=True)
		self._hilos.append(hilo)
		hilo.start()
		return hilo

	def _en_hilo_principal(self, funcion: Callable[..., None], *argumentos: Any) -> None:
		"""Ejecuta ``funcion`` solo si la ventana sigue viva (un resultado puede llegar tarde)."""
		if self and not self._saliendo:
			funcion(*argumentos)

	def _avisar_fallo_tarea(self, descripcion: str) -> None:
		self.SetStatusText(_("Error en «{tarea}». Detalles en el registro.").format(tarea=descripcion))
		mostrar_error(
			self,
			_("No se pudo completar «{tarea}». Los detalles están en la carpeta de registros.").format(tarea=descripcion),
			_("Error"),
		)

	# ── Segunda instancia ────────────────────────────────────────────────────

	def _al_revisar_buzon(self, _evento: wx.TimerEvent) -> None:
		"""Atiende las órdenes de una segunda instancia (mostrar, abrir archivos)."""
		try:
			ordenes = self._instancia.recoger_ordenes() if self._instancia else []
			for orden in ordenes:
				if orden.orden == ORDEN_ABRIR:
					self.abrir_archivos([Path(a) for a in orden.argumentos])
				if orden.orden in (ORDEN_MOSTRAR, ORDEN_ABRIR):
					self.restaurar()
		except Exception:  # noqa: BLE001 - el temporizador late toda la sesión; no debe romperla
			log.exception("Error al atender las órdenes de otra instancia")

	# ── Lista ────────────────────────────────────────────────────────────────

	def abrir_archivos(self, rutas: list[Path]) -> None:
		"""Añade archivos a la lista (desde Archivo, Abrir o desde otra instancia)."""
		for ruta in rutas:
			indice = self.lista.InsertItem(self.lista.GetItemCount(), ruta.name)
			self.lista.SetItem(indice, 1, str(ruta.parent))
		if rutas:
			self.SetStatusText(_("{n} elementos en la lista").format(n=self.lista.GetItemCount()))

	def seleccionados(self) -> list[int]:
		"""Índices seleccionados de la lista, en orden."""
		indices = []
		indice = self.lista.GetFirstSelected()
		while indice != -1:
			indices.append(indice)
			indice = self.lista.GetNextSelected(indice)
		return indices

	def _texto_fila(self, indice: int) -> str:
		return "\t".join(self.lista.GetItemText(indice, columna) for columna in range(self.lista.GetColumnCount()))

	def _al_tecla_lista(self, evento: wx.KeyEvent) -> None:
		# Suprimir se atiende aquí y NO como atajo del menú: un atajo de menú
		# le robaría la tecla a cualquier campo de texto de la ventana.
		if evento.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) and not evento.HasAnyModifiers():
			self._al_quitar(None)
			return
		evento.Skip()

	def _al_activar_elemento(self, evento: wx.ListEvent) -> None:
		"""Intro o doble clic: abre el archivo con su programa predeterminado."""
		indice = evento.GetIndex()
		ruta = Path(self.lista.GetItemText(indice, 1)) / self.lista.GetItemText(indice, 0)
		if ruta.exists() and hasattr(os, "startfile"):
			try:
				os.startfile(ruta)  # noqa: S606 - archivo elegido por el usuario
			except OSError:
				log.exception("No se pudo abrir %s", ruta)
				mostrar_error(self, _("No se pudo abrir {ruta}.").format(ruta=ruta), _("Abrir"))

	def _al_menu_contextual(self, evento: wx.ContextMenuEvent) -> None:
		"""Menú contextual con ratón, tecla Aplicaciones o Mayús+F10.

		Desde el teclado la posición llega como ``wx.DefaultPosition``: se abre
		junto al elemento enfocado para que el lector no lo anuncie «en el aire».
		"""
		menu = wx.Menu()
		for nombre, etiqueta in (
			("copiar", _("&Copiar")),
			("quitar", _("&Quitar de la lista")),
			(None, None),
			("actualizar", _("&Actualizar")),
		):
			if nombre is None:
				menu.AppendSeparator()
				continue
			item = menu.Append(wx.ID_ANY, etiqueta)
			menu.Bind(wx.EVT_MENU, getattr(self, f"_al_{nombre}"), item)
		posicion = evento.GetPosition()
		if posicion == wx.DefaultPosition:
			enfocado = max(self.lista.GetFocusedItem(), 0)
			rect = self.lista.GetItemRect(enfocado) if self.lista.GetItemCount() else wx.Rect(0, 0, 0, 0)
			posicion = wx.Point(rect.x + 20, rect.y + rect.height)
		else:
			posicion = self.lista.ScreenToClient(posicion)
		try:
			self.lista.PopupMenu(menu, posicion)
		finally:
			menu.Destroy()

	# ── Manejadores de menú ──────────────────────────────────────────────────

	def _al_abrir(self, _evento: wx.CommandEvent | None) -> None:
		with wx.FileDialog(
			self,
			_("Abrir archivos"),
			wildcard=_("Todos los archivos (*.*)|*.*"),
			style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST,
		) as dialogo:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			self.abrir_archivos([Path(r) for r in dialogo.GetPaths()])
		self._enfocar_lista()

	def _al_salir(self, _evento: wx.CommandEvent | None) -> None:
		self.salir()

	def _al_copiar(self, _evento: wx.CommandEvent | None) -> None:
		"""Copia del campo de texto enfocado o de la lista.

		Un atajo de menú (Ctrl+C) tiene prioridad sobre el control enfocado: si
		no se reenvía, dejaría de funcionar copiar en los campos de texto.
		"""
		foco = wx.Window.FindFocus()
		if isinstance(foco, (wx.TextCtrl, wx.ComboBox)):
			foco.Copy()
			return
		indices = self.seleccionados()
		if not indices:
			self.SetStatusText(_("No hay nada seleccionado"))
			return
		if copiar_al_portapapeles("\n".join(self._texto_fila(i) for i in indices)):
			mensaje = _("Copiados {n} elementos").format(n=len(indices))
			self.SetStatusText(mensaje)
			voz.hablar(mensaje)

	def _al_seleccionar_todo(self, _evento: wx.CommandEvent | None) -> None:
		foco = wx.Window.FindFocus()
		if isinstance(foco, (wx.TextCtrl, wx.ComboBox)):
			foco.SelectAll()
			return
		for indice in range(self.lista.GetItemCount()):
			self.lista.Select(indice)

	def _al_quitar(self, _evento: wx.CommandEvent | None) -> None:
		indices = self.seleccionados()
		if not indices:
			return
		for indice in reversed(indices):
			self.lista.DeleteItem(indice)
		# El foco queda en el elemento que ocupa el lugar del primero borrado.
		restantes = self.lista.GetItemCount()
		if restantes:
			nuevo = min(indices[0], restantes - 1)
			self.lista.Focus(nuevo)
			self.lista.Select(nuevo)
		mensaje = _("Quitados {n} elementos").format(n=len(indices))
		self.SetStatusText(mensaje)
		voz.hablar(mensaje)

	def _al_barra_estado(self, evento: wx.CommandEvent) -> None:
		visible = evento.IsChecked()
		self._aplicar_barra_estado(visible)
		self._config.establecer("ventana.barra_estado", visible)

	def _aplicar_barra_estado(self, visible: bool) -> None:
		self.GetStatusBar().Show(visible)
		self.elementos_menu["barra_estado"].Check(visible)
		self.SendSizeEvent()

	def _al_actualizar(self, _evento: wx.CommandEvent | None) -> None:
		"""Ejemplo del patrón de tarea en segundo plano: sustituir por el servicio real."""
		self.SetStatusText(_("Actualizando..."))
		# Lo que el hilo necesite de los controles se lee AQUÍ, en el hilo
		# principal: el hilo de fondo no puede ni leer ni escribir controles.
		actuales = self.lista.GetItemCount()

		def trabajo(detener: threading.Event) -> int:
			# Aquí iría la llamada a app/services (red, disco...). Sin wx.
			return 0 if detener.is_set() else actuales

		def al_terminar(total: int) -> None:
			mensaje = _("Lista actualizada: {n} elementos").format(n=total)
			self.SetStatusText(mensaje)
			voz.hablar(mensaje)

		self.ejecutar_en_segundo_plano(_("actualizar"), trabajo, al_terminar)

	def _al_opciones(self, _evento: wx.CommandEvent | None) -> None:
		from app.ui.dialogo_opciones import DialogoOpciones

		with DialogoOpciones(self, self._config) as dialogo:
			if dialogo.ShowModal() == wx.ID_OK:
				dialogo.aplicar()

	def _abrir_carpeta(self, carpeta: Path) -> None:
		if hasattr(os, "startfile"):
			try:
				os.startfile(carpeta)  # noqa: S606 - carpeta propia de la aplicación
			except OSError:
				log.exception("No se pudo abrir la carpeta %s", carpeta)

	def _al_carpeta_datos(self, _evento: wx.CommandEvent | None) -> None:
		self._abrir_carpeta(paths.ruta_datos())

	def _al_carpeta_registros(self, _evento: wx.CommandEvent | None) -> None:
		self._abrir_carpeta(paths.ruta_registros())

	def _al_manual(self, _evento: wx.CommandEvent | None) -> None:
		mostrar_documento(self, "manual", _("Manual de usuario"))

	def _al_teclas(self, _evento: wx.CommandEvent | None) -> None:
		mostrar_documento(self, "teclas", _("Lista de teclas"))

	def _al_novedades(self, _evento: wx.CommandEvent | None) -> None:
		mostrar_documento(self, "novedades", _("Novedades de la versión"))

	def _al_ayuda_contextual(self, _evento: wx.CommandEvent | None) -> None:
		"""Ctrl+H: la ayuda (``SetHelpText``) del control enfocado.

		Requiere ``wx.HelpProvider.Set(wx.SimpleHelpProvider())`` al arrancar;
		sin proveedor, ``SetHelpText`` no guarda nada.
		"""
		foco = wx.Window.FindFocus()
		texto = foco.GetHelpText() if foco else ""
		if not texto:
			texto = _("No hay ayuda para este control. Pulse F1 para abrir el manual.")
		mostrar_mensaje(self, texto, _("Ayuda"))

	def _al_acerca_de(self, _evento: wx.CommandEvent | None) -> None:
		from app.ui.acerca_de import mostrar_acerca_de

		mostrar_acerca_de(self)
