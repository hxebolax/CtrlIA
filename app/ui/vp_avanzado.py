"""Acciones de la 1.6.0 en la ventana principal (se mezcla con ``VentanaPrincipal``).

- Actividad en segundo plano (Ctrl+Alt+A), compactar (Ctrl+Alt+K), preguntar aparte (Ctrl+Alt+S),
  control remoto de Claude y detener una tarea suelta.
- Bifurcar o reintentar desde un mensaje: una sesión nueva que recuerda la
  conversación solo hasta ahí; la original no cambia.
- Vistas de la lista de mensajes: todo, la conversación, solo las respuestas, solo lo tuyo,
  las acciones o los errores y avisos (F11 y Mayús+F11).
- Leer la conversación entera como una página, ir a la respuesta anterior o siguiente del
  agente (F12 y Mayús+F12) y leer o copiar la última respuesta desde cualquier sitio.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import wx

from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, mostrar_error, mostrar_mensaje
from app.ui.comun import _, hora_corta, nombre_agente

if TYPE_CHECKING:
	from app.services.gestor import FilaSesion, MensajeVista

log = logging.getLogger(__name__)

#: Vistas de la lista de mensajes: identificador → (nombre, roles que muestra; ``None`` = todos).
VISTAS: dict[str, tuple[str, frozenset[str] | None]] = {
	"todo": (_("Todo"), None),
	"conversacion": (_("La conversación (tú y el agente)"), frozenset({"usuario", "asistente"})),
	"respuestas": (_("Solo las respuestas del agente"), frozenset({"asistente"})),
	"tuyos": (_("Solo tus mensajes"), frozenset({"usuario"})),
	"acciones": (_("Acciones y archivos cambiados"), frozenset({"herramienta", "cambio", "plan"})),
	"errores": (_("Errores y avisos"), frozenset({"error", "sistema"})),
}
ORDEN_VISTAS = list(VISTAS)

_ENCABEZADO_MD = re.compile(r"^(#{1,4})(?=[ \t])")
_VALLA = re.compile(r"^[ \t]*(```|~~~)")


def bajar_encabezados_md(texto: str, niveles: int = 2) -> str:
	"""Baja ``niveles`` los encabezados Markdown de un mensaje (no los de dentro de un bloque de código)."""
	lineas = []
	en_codigo = False
	for linea in texto.split("\n"):
		if _VALLA.match(linea):
			en_codigo = not en_codigo
		elif not en_codigo:
			linea = _ENCABEZADO_MD.sub(lambda m: "#" * min(6, len(m.group(1)) + niveles), linea)
		lineas.append(linea)
	return "\n".join(lineas)


def pagina_conversacion(titulo: str, mensajes: list[MensajeVista], quien: Any) -> str:
	"""La conversación como una página: cada mensaje es un encabezado de nivel 2 con quién lo dijo y su hora."""
	partes = [f"# {titulo}", ""]
	for numero, mensaje in enumerate(mensajes, start=1):
		partes.append(f"## {numero}. {quien(mensaje)}, {hora_corta(mensaje.fecha)}")
		partes.append("")
		partes.append(bajar_encabezados_md(mensaje.texto or _("(vacío)")))
		if mensaje.datos.get("detalle_tecnico"):
			partes += ["", "### " + _("Detalle técnico"), "", "```", str(mensaje.datos["detalle_tecnico"]), "```"]
		partes.append("")
	return "\n".join(partes)


class MixinAvanzado:
	"""Acciones nuevas de la 1.6.0 (se mezcla con ``VentanaPrincipal``)."""

	# ------------------------------------------------------------------
	# Actividad en segundo plano
	# ------------------------------------------------------------------

	def _fila_con_conversacion(self, accion: str) -> FilaSesion | None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None:
			avisos.anunciar(_("Primero abre una sesión."), interrumpir=True)
			return None
		if not fila.propia:
			avisos.anunciar(_("{a} solo se puede hacer en las sesiones de Ctrl+IA.").format(a=accion), interrumpir=True)
			return None
		return fila

	def accion_actividad(self) -> None:
		"""Sesión, «Actividad en segundo plano...» (Ctrl+Alt+A)."""
		from app.ui.dialogos.actividad import DialogoActividad

		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None:
			avisos.anunciar(_("Primero abre una sesión."), interrumpir=True)
			return
		abierto = getattr(self, "_dialogo_actividad", None)
		if abierto and abierto.clave == fila.clave:
			abierto.Raise()
			abierto.lista.SetFocus()
			return
		if abierto:
			abierto.Close()
		capacidades = self._capacidades_de(fila)
		detener = (lambda identificador, c=fila.clave: self._detener_tarea(c, identificador)) \
			if capacidades is not None and capacidades.detener_tareas else None
		dialogo = DialogoActividad(self, self.gestor.actividad, fila.clave, fila.titulo, detener)
		self._dialogo_actividad = dialogo
		dialogo.Show()

	def _capacidades_de(self, fila: FilaSesion) -> Any:
		proveedor = self.gestor.proveedor(fila.proveedor)
		return proveedor.capacidades if proveedor is not None else None

	def _detener_tarea(self, clave: str, identificador: str) -> None:
		def fallo(error: BaseException) -> None:
			mostrar_error(self, _("No se ha podido detener la tarea: {e}").format(e=error), _("Detener la tarea"))

		self.en_segundo_plano(self.gestor.detener_tarea, clave, identificador, al_fallar=fallo)

	# ------------------------------------------------------------------
	# Compactar, preguntar aparte y control remoto
	# ------------------------------------------------------------------

	def accion_compactar(self) -> None:
		"""Sesión, «Compactar la conversación» (Ctrl+Alt+K): resume lo hablado para liberar contexto."""
		fila = self._fila_con_conversacion(_("Compactar"))
		if fila is None:
			return
		capacidades = self._capacidades_de(fila)
		if capacidades is None or not capacidades.compactar:
			mostrar_mensaje(self, _("{a} no deja compactar la conversación desde Ctrl+IA. Suele hacerlo él solo cuando el "
				"contexto se llena.").format(a=nombre_agente(fila.proveedor)), _("Compactar la conversación"))
			return
		if confirmar(self, _("El agente resumirá la conversación para liberar contexto: a partir de ahora recordará el "
				"resumen en vez de cada mensaje. Los mensajes siguen aquí para leerlos. ¿Compactar ahora?"),
				_("Compactar la conversación")) != wx.ID_YES:
			return
		try:
			self.gestor.compactar(fila.clave)
		except Exception as error:  # noqa: BLE001 - se explica y se registra
			log.info("No se pudo compactar: %s", error)
			mostrar_error(self, str(error), _("Compactar la conversación"))
			return
		avisos.anunciar(_("Compactando la conversación..."), interrumpir=True)
		self.recargar_mensajes()

	def accion_preguntar_aparte(self) -> None:
		"""Sesión, «Preguntar aparte...» (Ctrl+Alt+S): una pregunta que no se añade a la sesión."""
		from app.ui.dialogos.preguntar_aparte import DialogoPreguntarAparte

		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else self.fila_seleccionada()
		if fila is None:
			avisos.anunciar(_("Primero elige o abre una sesión."), interrumpir=True)
			return
		capacidades = self._capacidades_de(fila)
		if capacidades is None or not capacidades.preguntar_aparte:
			mostrar_mensaje(self, _("{a} no admite preguntas aparte. Puedes hacerlo con las sesiones de Claude Code y de "
				"Codex.").format(a=nombre_agente(fila.proveedor)), _("Preguntar aparte"))
			return
		historial: dict[str, list[tuple[str, str]]] = self.__dict__.setdefault("_preguntas_aparte", {})
		clave = fila.clave

		def preguntar(pregunta: str, al_texto, cancelar) -> str:
			return self.gestor.preguntar_aparte(clave, pregunta, al_texto, cancelar)

		dialogo = DialogoPreguntarAparte(self, fila.titulo, preguntar, self.en_segundo_plano,
			historial.setdefault(clave, []))
		dialogo.Show()

	def accion_control_remoto(self) -> None:
		"""Sesión, «Seguir en claude.ai (control remoto)...»: la sesión de Claude Code también desde la web o el móvil."""
		fila = self._fila_con_conversacion(_("El control remoto"))
		if fila is None:
			return
		if fila.proveedor != "claude":
			mostrar_mensaje(self, _("El control remoto es de Claude Code."), _("Control remoto"))
			return
		conversacion = self.gestor.conversacion(fila.clave)
		activo = bool(conversacion is not None and conversacion.ajustes.extra.get("control_remoto"))
		if not activo and confirmar(self, _(
				"Con el control remoto, esta sesión de Claude Code se puede seguir y continuar también desde claude.ai o la "
				"app del móvil, con tu cuenta. La conversación pasa a estar también en claude.ai. ¿Activarlo?"),
				_("Control remoto")) != wx.ID_YES:
			return

		def hecho(direccion: Any) -> None:
			if activo:
				avisos.anunciar(_("Control remoto desactivado."), interrumpir=True)
				return
			from app.ui.accesibilidad.teclado import copiar_texto

			copiar_texto(str(direccion or ""))
			mostrar_mensaje(self, _("Control remoto activado. La dirección de la sesión está copiada en el portapapeles:\n{d}")
				.format(d=direccion), _("Control remoto"))

		def fallo(error: BaseException) -> None:
			mostrar_error(self, _("No se ha podido: {e}").format(e=error), _("Control remoto"))

		if conversacion is None:
			return
		self.en_segundo_plano(conversacion.control_remoto, not activo, al_terminar=hecho, al_fallar=fallo)

	# ------------------------------------------------------------------
	# Bifurcar o reintentar desde un mensaje
	# ------------------------------------------------------------------

	def accion_bifurcar_desde_mensaje(self) -> None:
		"""«Bifurcar desde este mensaje...»: sesión nueva que recuerda la conversación hasta el mensaje elegido."""
		self._cortar_en_mensaje("bifurcar")

	def accion_reintentar_desde_mensaje(self) -> None:
		"""«Reintentar desde tu mensaje...»: sesión nueva hasta antes de tu mensaje, que vuelve al cuadro para cambiarlo."""
		self._cortar_en_mensaje("reintentar")

	def _cortar_en_mensaje(self, modo: str) -> None:
		titulo = _("Bifurcar desde el mensaje") if modo == "bifurcar" else _("Reintentar desde tu mensaje")
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		mensaje = self.mensaje_actual()
		if fila is None or mensaje is None:
			avisos.anunciar(_("Elige primero un mensaje en la lista de mensajes."), interrumpir=True)
			return
		capacidades = self._capacidades_de(fila)
		if capacidades is None or not capacidades.bifurcar_en_mensaje:
			mostrar_mensaje(self, _("{a} no sabe empezar una conversación desde un mensaje. Puedes usar «Nueva sesión a "
				"partir del mensaje» (Control+Alt+N).").format(a=nombre_agente(fila.proveedor)), titulo)
			return
		if modo == "reintentar" and mensaje.rol != "usuario":
			avisos.anunciar(_("«Reintentar» se hace sobre un mensaje tuyo."), interrumpir=True)
			return
		todos = self.mensajes_completos()
		indice = next((i for i, m in enumerate(todos) if m is mensaje), -1)
		if indice < 0:
			return
		if modo == "bifurcar":
			pregunta = _("Se creará una sesión nueva que recuerda la conversación hasta este mensaje, incluido. La original no "
				"cambia. ¿Bifurcar?")
		else:
			pregunta = _("Se creará una sesión nueva que recuerda la conversación hasta justo antes de este mensaje tuyo, que "
				"volverá al cuadro de respuesta para que lo cambies y lo envíes. La original no cambia. ¿Reintentar?")
		if confirmar(self, pregunta, titulo) != wx.ID_YES:
			return
		texto = mensaje.texto

		def hecho(clave: Any) -> None:
			self.refrescar_sesiones(True)
			self.abrir_sesion(str(clave))
			if modo == "reintentar":
				self.respuesta.SetValue(texto)
				self.respuesta.SetInsertionPointEnd()
				wx.CallLater(600, self.ir_a_respuesta)
				avisos.anunciar(_("Sesión nueva lista. Tu mensaje está en el cuadro de respuesta para cambiarlo."),
					interrumpir=True)
			else:
				avisos.anunciar(_("Sesión bifurcada lista. Escribe por dónde quieres seguir."), interrumpir=True)

		def fallo(error: BaseException) -> None:
			mostrar_error(self, str(error), titulo)

		self.en_segundo_plano(self.gestor.bifurcar_desde_mensaje, fila.clave, todos, indice, modo,
			al_terminar=hecho, al_fallar=fallo)

	# ------------------------------------------------------------------
	# Vistas de la lista de mensajes
	# ------------------------------------------------------------------

	def crear_menu_vistas_mensajes(self) -> wx.Menu:
		"""Ver, «Vista de los mensajes»: qué mensajes se ven en la lista."""
		menu = wx.Menu()
		self._elementos_vista: dict[str, wx.MenuItem] = {}
		for identificador, (nombre, _roles) in VISTAS.items():
			item = menu.AppendRadioItem(wx.ID_ANY, nombre)
			self._elementos_vista[identificador] = item
			self.Bind(wx.EVT_MENU, lambda _evento, v=identificador: self._ejecutar_accion_con(self.poner_vista_mensajes, v), item)
		menu.AppendSeparator()
		for etiqueta, ayuda, paso in (
			(_("Vista siguiente\tF11"), _("Pasa a la siguiente vista de los mensajes."), 1),
			(_("Vista anterior\tShift+F11"), _("Vuelve a la vista anterior de los mensajes."), -1),
		):
			item = menu.Append(wx.ID_ANY, etiqueta, ayuda)
			self.Bind(wx.EVT_MENU, lambda _evento, p=paso: self._ejecutar_accion_con(self.cambiar_vista_mensajes, p), item)
		return menu

	def _ejecutar_accion_con(self, funcion: Any, *argumentos: Any) -> None:
		try:
			funcion(*argumentos)
		except Exception as error:  # noqa: BLE001 - una acción que falla no debe tumbar la aplicación
			log.exception("Falló la acción %s", getattr(funcion, "__name__", funcion))
			mostrar_error(self, _("No se pudo completar la acción: {error}").format(error=error))

	def cambiar_vista_mensajes(self, paso: int) -> None:
		actual = ORDEN_VISTAS.index(self.vista_mensajes) if self.vista_mensajes in ORDEN_VISTAS else 0
		self.poner_vista_mensajes(ORDEN_VISTAS[(actual + paso) % len(ORDEN_VISTAS)])

	def poner_vista_mensajes(self, vista: str) -> None:
		"""Cambia qué mensajes se ven, sin perder el sitio: el mismo mensaje si se ve, o el anterior que se vea."""
		if vista not in VISTAS:
			return
		elementos = getattr(self, "_elementos_vista", {})
		if vista in elementos:
			elementos[vista].Check(True)
		completos = self.mensajes_completos()
		actual = self.mensaje_actual()
		self.vista_mensajes = vista
		self.mostrar_mensajes(completos)
		visibles = self.mensajes_completos_visibles()
		destino = None
		if actual is not None and actual in completos:
			posicion = completos.index(actual)
			for anterior in reversed(completos[:posicion + 1]):
				if anterior in visibles:
					destino = anterior
					break
		if destino is not None:
			indice = next((i for i, m in enumerate(self._mensajes) if m is destino), -1)
			if indice < 0:
				self._mostrar_mensaje_completo(visibles.index(destino), anunciar=False, enfocar_lista=False)
			else:
				self.lista_mensajes.enfocar_fila(indice, anunciar=False)
		nombre = VISTAS[vista][0]
		self.etiqueta_mensajes.SetLabel(_("&Mensajes:") if vista == "todo" else _("&Mensajes ({v}):").format(v=nombre))
		avisos.anunciar(_("Vista: {v}. {n} mensajes.").format(v=nombre, n=len(visibles)), interrumpir=True)

	# ------------------------------------------------------------------
	# Leer la conversación, saltar entre respuestas y la última respuesta
	# ------------------------------------------------------------------

	def accion_leer_conversacion(self) -> None:
		"""Ver, «Leer la conversación entera como una página» (Ctrl+Alt+L)."""
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		mensajes = [m for m in self.mensajes_completos() if m.rol not in ("anteriores",)]
		if fila is None or not mensajes:
			avisos.anunciar(_("No hay ninguna conversación abierta."), interrumpir=True)
			return
		titulo = _("Conversación «{t}»").format(t=fila.titulo)
		avisos.anunciar(_("Preparando la página..."), interrumpir=True)
		quien = {id(m): self._quien(m) for m in mensajes}

		def preparar() -> str:
			return pagina_conversacion(titulo, mensajes, lambda m: quien.get(id(m), m.nombre_rol))

		def mostrar(texto: Any) -> None:
			from app.ui.comun import markdown_seguro
			from app.ui.visor_ayuda import _abiertos, mostrar_documento

			anterior = _abiertos.get(titulo)
			if anterior:
				anterior.Close()  # la de antes tiene la conversación de entonces
			formato = bool(self.opcion("mensajes.vista_formateada", True))
			mostrar_documento(self, titulo, markdown_seguro(str(texto)) if formato else str(texto), forzar_texto=not formato,
				ayuda=_("La conversación entera: cada mensaje es un encabezado de nivel 2 (con NVDA, la tecla 2 salta al "
					"siguiente). Escape cierra."))

		self.en_segundo_plano(preparar, al_terminar=mostrar, clave="leer_conversacion")

	def _ir_a_respuesta(self, paso: int) -> None:
		"""F12 y Mayús+F12: la respuesta siguiente o anterior del agente en la lista de mensajes."""
		if not self._mensajes:
			avisos.anunciar(_("No hay mensajes."), interrumpir=True)
			return
		actual = self.lista_mensajes.GetFocusedItem()
		if actual < 0:
			actual = len(self._mensajes) if paso < 0 else -1
		indice = actual + paso
		while 0 <= indice < len(self._mensajes):
			if self._mensajes[indice].rol == "asistente":
				self.lista_mensajes.SetFocus()
				self.lista_mensajes.enfocar_fila(indice, anunciar=True)
				return
			indice += paso
		avisos.anunciar(_("No hay más respuestas del agente hacia abajo.") if paso > 0
			else _("No hay más respuestas del agente hacia arriba. Alt+Inicio carga los mensajes anteriores."), interrumpir=True)

	def accion_respuesta_siguiente(self) -> None:
		self._ir_a_respuesta(1)

	def accion_respuesta_anterior(self) -> None:
		self._ir_a_respuesta(-1)

	def _ultima_respuesta(self) -> MensajeVista | None:
		return next((m for m in reversed(self.mensajes_completos()) if m.rol == "asistente"), None)

	def accion_leer_ultima_respuesta(self) -> None:
		"""Ctrl+Alt+U: lee la última respuesta del agente sin moverte de donde estés."""
		mensaje = self._ultima_respuesta()
		if mensaje is None:
			avisos.anunciar(_("Esta sesión todavía no tiene respuestas del agente."), interrumpir=True)
			return
		avisos.anunciar(self._para_voz(mensaje.texto)[:8000], interrumpir=True, siempre=True)

	def accion_copiar_ultima_respuesta(self) -> None:
		"""Ctrl+Alt+C: copia la última respuesta del agente."""
		mensaje = self._ultima_respuesta()
		if mensaje is None:
			avisos.anunciar(_("Esta sesión todavía no tiene respuestas del agente."), interrumpir=True)
			return
		self.copiar(mensaje.texto)
