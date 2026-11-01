"""«Conversación automática entre sesiones»: el diálogo para prepararla y lo que la une a la ventana principal.

El ciclo vive en ``app.services.dialogo_agentes`` (sin wx). Aquí están los
diálogos (prepararla, revisar un relevo y ver las guardadas) y las órdenes de la
ventana: empezar, detener, ver la conversación intercalada, avisar si se está
cerca de un límite de uso y pasar al relevo los eventos de los agentes.

En el diálogo, Control+Intro empieza desde cualquier campo; en la tarea, Intro
escribe una línea nueva. Si algo falta, el diálogo sigue abierto con el motivo.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import wx

from app.services import dialogo_agentes, limites, reenviar
from app.services.agentes.base import Adjunto, Evento
from app.services.dialogo_agentes import ConversacionEntreSesiones, Intervencion, Participante, Preparacion
from app.services.gestor import FilaSesion, MensajeVista
from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, enfocar, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _, nombre_agente

log = logging.getLogger(__name__)

TITULO = _("Conversación automática entre sesiones")

#: Cuánto se espera a los límites de uso antes de empezar sin saberlos (segundos).
TIEMPO_LIMITES = 20.0


class DialogoConversacionSesiones(wx.Dialog):
	"""Deja en ``elegido`` una ``Preparacion`` al pulsar «Empezar»."""

	def __init__(self, padre: wx.Window | None, origen: str, destinos: Sequence[str],
			comprobar: Callable[[int], str] | None = None) -> None:
		super().__init__(padre, title=TITULO, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=TITULO)
		self._comprobar = comprobar or (lambda _indice: "")
		self.elegido: Preparacion | None = None
		self._de_plantilla = ("", "")
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.origen = crear_campo(
			panel, _("&Primera sesión, la que empieza:"), wx.TextCtrl, value=origen, style=wx.TE_READONLY,
			ayuda=_("La sesión abierta. Recibe la tarea y trabaja primero; después, cada respuesta pasa a la otra sesión."),
			sizer=raiz,
		)
		_e, self.destinos = crear_campo(
			panel, _("&Segunda sesión:"), wx.ListBox, choices=list(destinos), style=wx.LB_SINGLE,
			ayuda=_("La sesión de Ctrl+IA que contesta a la primera, de cualquier agente: por ejemplo, Codex para revisar "
				"lo que escribe Claude. Tiene que estar parada."),
			sizer=raiz,
		)
		self.destinos.SetMinSize(self.FromDIP(wx.Size(560, 110)))
		if destinos:
			self.destinos.SetSelection(0)
		_e, self.plantilla = crear_campo(
			panel, _("Pl&antilla:"), wx.Choice,
			choices=[_("Sin plantilla: escribes tú las instrucciones")]
				+ [f"{p.nombre}: {p.descripcion}" for p in dialogo_agentes.PLANTILLAS],
			ayuda=_("Un tipo de conversación ya pensado: rellena las instrucciones de las dos sesiones, que puedes cambiar "
				"después. No cambia los permisos de las sesiones: revisa antes qué puede hacer cada una."),
			sizer=raiz,
		)
		self.plantilla.SetSelection(0)
		_e, self.tarea = crear_campo(
			panel, _("&Tarea para la primera sesión:"), wx.TextCtrl, style=wx.TE_MULTILINE | wx.TE_RICH2,
			ayuda=_("Lo que le pides a la primera sesión. Es el primer mensaje de la conversación. Intro empieza una línea "
				"nueva y Control+Intro empieza la conversación."),
			sizer=raiz,
		)
		self.tarea.SetMinSize(self.FromDIP(wx.Size(560, 140)))
		_e, self.turnos = crear_campo(
			panel, _("T&urnos como máximo:"), wx.SpinCtrl, min=dialogo_agentes.TURNOS_MINIMOS,
			max=dialogo_agentes.TURNOS_MAXIMOS, initial=dialogo_agentes.TURNOS_DE_FABRICA,
			ayuda=_("Cuántas respuestas, contando las de las dos sesiones, habrá como mucho. Termina antes si una de ellas "
				"escribe {m} para dar el trabajo por terminado, si se repiten o si solo se dan las gracias. De 2 a 20.").format(
				m=dialogo_agentes.MARCA_FIN),
			sizer=raiz, horizontal=True, expandir=False,
		)
		_e, self.instruccion_a = crear_campo(
			panel, _("Instrucción para la p&rimera sesión (opcional):"), wx.TextCtrl,
			ayuda=_("Su papel en la conversación, por ejemplo: «escribe el código y corrige lo que te señalen»."),
			sizer=raiz,
		)
		_e, self.instruccion_b = crear_campo(
			panel, _("Instrucción para la segu&nda sesión (opcional):"), wx.TextCtrl,
			ayuda=_("Su papel en la conversación, por ejemplo: «revisa y señala fallos, sin cambiar archivos»."),
			sizer=raiz,
		)
		self.aprobar = wx.CheckBox(panel, label=_("Aprobar cada relev&o a mano"))
		self.aprobar.SetHelpText(_("Antes de pasar cada respuesta, Ctrl+IA te avisa y Control+Alt+Mayúsculas+E te la enseña: "
			"puedes seguir, cambiarla o parar la conversación. Sin marcar, las respuestas pasan solas."))
		raiz.Add(self.aprobar, 0, wx.ALL, 5)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		empezar = wx.Button(panel, wx.ID_OK, _("&Empezar"))
		empezar.SetHelpText(_("Envía la tarea a la primera sesión y empieza la conversación. Los permisos y las preguntas "
			"de los agentes te siguen llegando a ti: la conversación se pausa hasta que respondas."))
		empezar.SetDefault()
		cancelar = wx.Button(panel, wx.ID_CANCEL, _("Cancelar"))
		cancelar.SetHelpText(_("Cierra sin empezar nada. También con Escape."))
		fila.Add(empezar, 0, wx.RIGHT, 6)
		fila.Add(cancelar, 0)
		raiz.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		empezar.Bind(wx.EVT_BUTTON, lambda _e: self.empezar())
		self.plantilla.Bind(wx.EVT_CHOICE, lambda _e: self.aplicar_plantilla())
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		preparar_dialogo(self, foco_inicial=self.destinos)
		self.CentreOnParent()

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		intro = evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
		if intro and evento.ControlDown() and not evento.AltDown() and not evento.ShiftDown():
			self.empezar()
			return
		evento.Skip()

	def aplicar_plantilla(self) -> None:
		"""Rellena las instrucciones; «Sin plantilla» solo quita lo que puso una plantilla, no lo que escribió la persona."""
		indice = self.plantilla.GetSelection() - 1
		plantilla = dialogo_agentes.PLANTILLAS[indice] if 0 <= indice < len(dialogo_agentes.PLANTILLAS) else None
		nuevas = (plantilla.instruccion_a, plantilla.instruccion_b) if plantilla else ("", "")
		for campo, anterior, nueva in zip((self.instruccion_a, self.instruccion_b), self._de_plantilla, nuevas, strict=True):
			if plantilla is not None or campo.GetValue() == anterior:
				campo.SetValue(nueva)
		self._de_plantilla = nuevas

	def empezar(self) -> bool:
		indice = self.destinos.GetSelection()
		if not 0 <= indice < self.destinos.GetCount():
			mostrar_mensaje(self, _("Elige primero en la lista la segunda sesión."), self.GetTitle())
			enfocar(self.destinos)
			return False
		problema = self._comprobar(indice)
		if problema:
			mostrar_mensaje(self, problema, self.GetTitle())
			enfocar(self.destinos)
			return False
		tarea = self.tarea.GetValue().strip()
		if not tarea:
			mostrar_mensaje(self, _("Escribe la tarea para la primera sesión: es el mensaje con el que empieza todo."),
				self.GetTitle())
			enfocar(self.tarea)
			return False
		self.elegido = Preparacion(indice, tarea, dialogo_agentes.limitar_turnos(self.turnos.GetValue()),
			self.instruccion_a.GetValue().strip(), self.instruccion_b.GetValue().strip(), self.aprobar.GetValue())
		self.EndModal(wx.ID_OK)
		return True


class DialogoRelevo(wx.Dialog):
	"""Revisar un relevo: la persona ve la respuesta y decide seguir, cambiarla o parar.

	Deja la decisión en ``decision`` («seguir» o «parar»; ``None`` si lo deja para más tarde,
	y el relevo sigue esperando) y lo que se pasará en ``texto_final``.
	"""

	def __init__(self, padre: wx.Window | None, de: str, a: str, texto: str, comprobar: Callable[[str], str]) -> None:
		titulo = _("Revisar el relevo: de {d} a {a}").format(d=de, a=a)
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._comprobar = comprobar
		self.decision: str | None = None
		self.texto_final = ""
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.texto = crear_campo(
			panel, _("&Mensaje que se pasará:"), wx.TextCtrl, value=texto, style=wx.TE_MULTILINE | wx.TE_RICH2,
			ayuda=_("La respuesta tal como se pasará, con la línea que dice de quién es y los archivos que cambió. Puedes "
				"cambiarla o añadir lo tuyo. Intro empieza una línea nueva y Control+Intro sigue."),
			sizer=raiz,
		)
		self.texto.SetMinSize(self.FromDIP(wx.Size(600, 300)))
		fila = wx.BoxSizer(wx.HORIZONTAL)
		seguir = wx.Button(panel, wx.ID_OK, _("&Seguir"))
		seguir.SetHelpText(_("Pasa el mensaje, tal como está en el cuadro, a la otra sesión."))
		seguir.SetDefault()
		parar = wx.Button(panel, wx.ID_ANY, _("&Parar la conversación"))
		parar.SetHelpText(_("Termina la conversación automática: el mensaje ya no se pasa."))
		mas_tarde = wx.Button(panel, wx.ID_CANCEL, _("Más &tarde"))
		mas_tarde.SetHelpText(_("Cierra este cuadro. El relevo sigue esperando; vuelve con Control+Alt+Mayúsculas+E. También "
			"con Escape."))
		for boton in (seguir, parar, mas_tarde):
			fila.Add(boton, 0, wx.RIGHT, 6)
		raiz.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		seguir.Bind(wx.EVT_BUTTON, lambda _e: self.seguir())
		parar.Bind(wx.EVT_BUTTON, lambda _e: self.parar())
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		preparar_dialogo(self, foco_inicial=self.texto)
		self.CentreOnParent()

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		intro = evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
		if intro and evento.ControlDown() and not evento.AltDown() and not evento.ShiftDown():
			self.seguir()
			return
		evento.Skip()

	def seguir(self) -> bool:
		texto = self.texto.GetValue().strip()
		problema = self._comprobar(texto)
		if problema:
			mostrar_mensaje(self, problema, self.GetTitle())
			enfocar(self.texto)
			return False
		self.decision, self.texto_final = "seguir", texto
		self.EndModal(wx.ID_OK)
		return True

	def parar(self) -> None:
		self.decision = "parar"
		self.EndModal(wx.ID_OK)


class DialogoConversacionesGuardadas(wx.Dialog):
	"""Las conversaciones entre sesiones guardadas: Intro ve la elegida y Suprimir la borra.

	:param nombres: cómo se llama cada una en la lista.
	:param ver: ``ver(indice)`` la muestra.
	:param borrar: ``borrar(indice) -> bool`` la borra, tras confirmarlo.
	"""

	def __init__(self, padre: wx.Window | None, nombres: Sequence[str], ver: Callable[[int], None],
			borrar: Callable[[int], bool]) -> None:
		titulo = _("Conversaciones entre sesiones guardadas")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._ver = ver
		self._borrar = borrar
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.lista = crear_campo(
			panel, _("&Conversaciones:"), wx.ListBox, choices=list(nombres), style=wx.LB_SINGLE,
			ayuda=_("Las conversaciones automáticas guardadas, de la más reciente a la más antigua. Intro muestra la elegida "
				"y Suprimir la borra. Cada mensaje sigue además en su sesión."),
			sizer=raiz,
		)
		self.lista.SetMinSize(self.FromDIP(wx.Size(600, 240)))
		if nombres:
			self.lista.SetSelection(0)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		boton_ver = wx.Button(panel, wx.ID_ANY, _("&Ver"))
		boton_ver.SetHelpText(_("Muestra lo que se dijeron, intercalado."))
		boton_ver.SetDefault()
		boton_borrar = wx.Button(panel, wx.ID_ANY, _("&Borrar..."))
		boton_borrar.SetHelpText(_("Borra la vista combinada elegida, tras confirmarlo. Los mensajes de cada sesión no se tocan."))
		cerrar = wx.Button(panel, wx.ID_CANCEL, _("C&errar"))
		cerrar.SetHelpText(_("Cierra este cuadro. También con Escape."))
		for boton in (boton_ver, boton_borrar, cerrar):
			fila.Add(boton, 0, wx.RIGHT, 6)
		raiz.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		boton_ver.Bind(wx.EVT_BUTTON, lambda _e: self.ver())
		boton_borrar.Bind(wx.EVT_BUTTON, lambda _e: self.borrar())
		self.lista.Bind(wx.EVT_LISTBOX_DCLICK, lambda _e: self.ver())
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		vincular_menu_contextual(self.lista, self._menu)
		preparar_dialogo(self, foco_inicial=self.lista)
		self.CentreOnParent()

	def _menu(self) -> wx.Menu | None:
		if self.lista.GetSelection() == wx.NOT_FOUND:
			return None
		menu = wx.Menu()
		for etiqueta, accion in ((_("&Ver\tIntro"), self.ver), (_("&Borrar...\tSupr"), self.borrar)):
			item = menu.Append(wx.ID_ANY, etiqueta)
			self.lista.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), item)
		return menu

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		if wx.Window.FindFocus() is self.lista and not evento.HasAnyModifiers():
			if evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
				self.ver()
				return
			if evento.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
				self.borrar()
				return
		evento.Skip()

	def ver(self) -> None:
		indice = self.lista.GetSelection()
		if indice != wx.NOT_FOUND:
			self._ver(indice)

	def borrar(self) -> None:
		indice = self.lista.GetSelection()
		if indice == wx.NOT_FOUND or not self._borrar(indice):
			return
		self.lista.Delete(indice)
		if self.lista.GetCount():
			self.lista.SetSelection(min(indice, self.lista.GetCount() - 1))
		enfocar(self.lista)


# ----------------------------------------------------------------------
# Órdenes de la ventana principal
# ----------------------------------------------------------------------


def actual(ventana: Any) -> ConversacionEntreSesiones | None:
	"""La conversación en marcha o la última que hubo (para verla)."""
	return ventana.__dict__.get("_conversacion_sesiones")


def hay_activa(ventana: Any) -> bool:
	conversacion = actual(ventana)
	return conversacion is not None and conversacion.activa


def comprobar_limites(ventana: Any, proveedores: Sequence[str], seguir: Callable[[], None],
		si_no: Callable[[], None] | None = None) -> None:
	"""Antes de empezar: si un agente está cerca de un límite de uso, lo dice y pregunta.

	Se lee en segundo plano, sin enviar mensajes ni gastar cuota: la interfaz nunca espera.
	Sin datos, no se avisa de nada. El umbral es el del aviso de límites de Preferencias.
	"""
	umbral = int(ventana.opcion("anuncios.aviso_limite", 90))
	if umbral <= 0:
		seguir()
		return
	if ventana.__dict__.get("_comprobando_limites"):
		avisos.anunciar(_("Aún se están comprobando los límites de uso; espera un momento."), interrumpir=True)
		if si_no is not None:
			si_no()
		return
	ventana.__dict__["_comprobando_limites"] = True
	avisos.anunciar(_("Comprobando tus límites de uso antes de empezar..."))
	gestor = ventana.gestor

	def leer() -> list[str]:
		return limites.cercanos(gestor.leer_limites_todos(TIEMPO_LIMITES, proveedores), umbral)

	def hecho(cercanos: list[str]) -> None:
		ventana.__dict__.pop("_comprobando_limites", None)
		if cercanos:
			pregunta = _("{l}\n\nCada turno de la conversación automática gasta de tu cuenta. ¿Empezarla de todos modos?")
			if confirmar(ventana, pregunta.format(l="\n".join(cercanos)), TITULO, peligroso=True) != wx.ID_YES:
				if si_no is not None:
					si_no()
				return
		seguir()

	def fallo(_error: BaseException) -> None:
		ventana.__dict__.pop("_comprobando_limites", None)
		seguir()

	ventana.en_segundo_plano(leer, al_terminar=hecho, al_fallar=fallo, clave="limites_conversacion")


def conservar(ventana: Any, clave: str, texto: str, motivo: str) -> None:
	"""Lo que no ha salido se queda en el cuadro de respuesta de esa sesión: nunca se pierde."""
	if ventana.sesion_actual != clave:
		ventana.abrir_sesion(clave, mover_foco=False)
	borrador = ventana.respuesta.GetValue()
	ventana.respuesta.SetValue(f"{texto}\n\n{borrador}" if borrador.strip() else texto)
	ventana.respuesta.SetInsertionPointEnd()
	ventana.ir_a_respuesta()
	avisos.anunciar(_("{m} El mensaje está en el cuadro de respuesta.").format(m=motivo), interrumpir=True)


def problema_para_conversar(ventana: Any, origen: FilaSesion, clave_destino: str | None = None) -> reenviar.Problema | None:
	"""Por qué no se puede arrancar una conversación entre ``origen`` y esa sesión, o ``None``.

	Sin destino, solo lo que depende del origen (la sesión nueva aún no existe).
	"""
	gestor = ventana.gestor
	problema = reenviar.problema_conversacion(origen, gestor.proveedor(origen.proveedor) is not None,
		gestor.ocupada(origen.clave), hay_activa(ventana))
	if problema is None and clave_destino:
		sesion = ventana.almacen.sesion(clave_destino)
		problema = reenviar.problema_destino(sesion.titulo if sesion else "", gestor.ocupada(clave_destino))
	return problema


def empezar(ventana: Any) -> None:
	"""Prepara y empieza una conversación con la sesión abierta como primera."""
	gestor = ventana.gestor
	fila = ventana.fila_de(ventana.sesion_actual) if ventana.sesion_actual else None
	if fila is None or not fila.propia or gestor.proveedor(fila.proveedor) is None:
		avisos.anunciar(_("Abre primero una sesión de Ctrl+IA: será la que empiece la conversación."), interrumpir=True)
		return
	anterior = actual(ventana)
	if anterior is not None and anterior.activa:
		mostrar_mensaje(ventana, _("Ya hay una conversación entre sesiones en marcha. {r} Detenla antes con Sesión, "
			"Detener la conversación entre sesiones.").format(r=anterior.resumen()), TITULO)
		return
	if gestor.ocupada(fila.clave):
		mostrar_mensaje(ventana, _("«{t}» está trabajando. Espera a que termine su turno, o detenla, y vuelve a "
			"empezar la conversación.").format(t=fila.titulo), TITULO)
		return
	destinos = reenviar.destinos(ventana._todas, fila.clave, lambda f: gestor.proveedor(f.proveedor) is not None)
	if not destinos:
		mostrar_mensaje(ventana, _("No hay otra sesión con la que conversar. Puede ser cualquier sesión de Ctrl+IA, de "
			"cualquier agente (no las de otros programas, ni las ocultas o archivadas). Crea una con Archivo, Nueva "
			"sesión."), TITULO)
		return
	nombres = [_("{t}, {a}, {c}").format(t=d.titulo, a=nombre_agente(d.proveedor), c=d.carpeta or _("sin carpeta"))
		for d in destinos]

	def comprobar(indice: int) -> str:
		if gestor.ocupada(destinos[indice].clave):
			return _("«{t}» está trabajando. Elige otra sesión o espera a que termine su turno.").format(
				t=destinos[indice].titulo)
		return ""

	origen = _("{t}, {a}").format(t=fila.titulo, a=nombre_agente(fila.proveedor))
	dialogo = DialogoConversacionSesiones(ventana, origen, nombres, comprobar)
	try:
		if dialogo.ShowModal() != wx.ID_OK or dialogo.elegido is None:
			return
		eleccion = dialogo.elegido
	finally:
		dialogo.Destroy()
	destino = destinos[eleccion.destino]
	a = Participante(fila.clave, nombre_agente(fila.proveedor), fila.titulo, fila.proveedor, eleccion.instruccion_a)
	b = Participante(destino.clave, nombre_agente(destino.proveedor), destino.titulo, destino.proveedor,
		eleccion.instruccion_b)

	def seguir() -> None:
		# Los límites se leen en segundo plano: entretanto ha podido cambiar algo.
		problema = problema_para_conversar(ventana, fila, destino.clave)
		if problema is not None:
			conservar(ventana, fila.clave, eleccion.tarea, _("No se ha empezado la conversación: {m}.").format(m=problema.completo))
			return
		crear(ventana, a, b, eleccion.tarea, eleccion.max_turnos, eleccion.aprobar).empezar()
		ventana.actualizar_barra_estado()

	comprobar_limites(ventana, (fila.proveedor, destino.proveedor), seguir,
		lambda: conservar(ventana, fila.clave, eleccion.tarea, _("No se ha empezado la conversación.")))


def empezar_con_respuesta(ventana: Any, origen: FilaSesion, clave_destino: str, texto: str, respuesta: str,
		max_turnos: int, aprobar: bool = False, adjuntos: Sequence[Adjunto] = ()) -> None:
	"""Arranca una conversación con una respuesta reenviada: contesta primero la sesión que la recibe (AIChat #379).

	:param texto: lo que dejó la persona en el mensaje, con la etiqueta del reenvío.
	:param respuesta: la respuesta del agente, para separarla de lo que haya añadido la persona.
	"""
	sesion = ventana.almacen.sesion(clave_destino)
	if sesion is None:
		avisos.anunciar(_("Esa sesión ya no existe. No se ha enviado nada."), interrumpir=True)
		return
	agente = nombre_agente(origen.proveedor)

	def seguir() -> None:
		problema = problema_para_conversar(ventana, origen, clave_destino)
		if problema is not None:
			conservar(ventana, clave_destino, texto, _("No se ha empezado la conversación automática: {m}.").format(m=problema.completo))
			return
		a = Participante(clave_destino, nombre_agente(sesion.proveedor), sesion.titulo, sesion.proveedor)
		b = Participante(origen.clave, agente, origen.titulo, origen.proveedor)
		cuerpo = reenviar.mensaje_conjunto(texto, agente, origen.titulo, respuesta)
		pasada, de_la_persona = reenviar.partir_mensaje(texto, agente, origen.titulo, respuesta)
		titulo = _("Respuesta reenviada de «{t}»").format(t=origen.titulo)
		conversacion = crear(ventana, a, b, titulo, max_turnos, aprobar, adjuntos)
		enviado = conversacion.empezar_con_respuesta(cuerpo, pasada or "", de_la_persona)
		if ventana.sesion_actual != clave_destino:
			ventana.abrir_sesion(clave_destino, mover_foco=False)
		ventana.actualizar_barra_estado()
		if enviado:
			ventana.ir_a_mensajes()
		else:
			conservar(ventana, clave_destino, texto, conversacion.motivo_fin)

	comprobar_limites(ventana, (origen.proveedor, sesion.proveedor), seguir,
		lambda: conservar(ventana, clave_destino, texto, _("No se ha empezado la conversación automática.")))


def crear(ventana: Any, a: Participante, b: Participante, tarea: str, max_turnos: int, aprobar: bool = False,
		adjuntos: Sequence[Adjunto] = ()) -> ConversacionEntreSesiones:
	"""El relevo unido a la ventana: envía como un mensaje normal y lee lo nuevo de cada sesión.

	:param adjuntos: van con el primer mensaje, si los hay.
	"""
	almacen = ventana.almacen
	#: Último mensaje ya visto de cada sesión: la respuesta de un turno es lo que llega después.
	desde: dict[str, int] = {}
	#: Lo que cambió el último turno leído de cada sesión.
	cambiados: dict[str, list[str]] = {}
	por_enviar = list(adjuntos)
	for clave in (a.clave, b.clave):
		guardados = almacen.mensajes(clave)
		desde[clave] = int(guardados[-1].id) if guardados else 0

	def enviar(clave: str, texto: str) -> bool:
		# Lo de antes de este mensaje no es de este turno (ni sus archivos).
		ultimos = almacen.ultimos_mensajes(clave, 1)
		if ultimos:
			desde[clave] = max(desde.get(clave, 0), int(ultimos[0].id))
		primeros = list(por_enviar)
		por_enviar.clear()
		return bool(ventana.enviar_mensaje(clave, texto, primeros, anunciar=False, relevo=True))

	def ultima_respuesta(clave: str) -> MensajeVista | None:
		nuevos = almacen.mensajes(clave, desde.get(clave, 0))
		if nuevos:
			desde[clave] = int(nuevos[-1].id)
		vistas = [ventana._vista_de(m) for m in nuevos]
		cambiados[clave] = dialogo_agentes.rutas_cambiadas(vistas)
		return reenviar.mensaje_a_reenviar(vistas)

	def avisar(texto: str, importante: bool) -> None:
		if importante:
			avisos.notificar(texto, TITULO, padre=ventana)
			if not avisos.aplicacion_activa():
				avisos.sonar("permiso")
				avisos.notificacion_sistema(TITULO, texto[:200])
		else:
			avisos.anunciar(texto)

	def al_terminar(_conversacion: ConversacionEntreSesiones) -> None:
		if ventana and not getattr(ventana, "_saliendo", False):
			ventana.actualizar_barra_estado()

	id_guardado: int | None = None
	try:
		id_guardado = almacen.crear_conversacion_sesiones(a.clave, b.clave, a.agente, b.agente, tarea,
			dialogo_agentes.limitar_turnos(max_turnos))
	except Exception:  # noqa: BLE001 - sin la vista guardada la conversación sigue
		log.exception("No se pudo guardar la conversación entre sesiones")

	def al_apuntar(intervencion: Intervencion) -> None:
		if id_guardado is not None:
			almacen.anadir_entrada_conversacion(id_guardado, intervencion.quien, intervencion.texto, intervencion.fecha,
				intervencion.nota)

	def al_cambiar(conversacion: ConversacionEntreSesiones) -> None:
		if id_guardado is not None:
			almacen.actualizar_conversacion_sesiones(id_guardado, conversacion.turno, str(conversacion.estado),
				conversacion.motivo_fin)

	conversacion = ConversacionEntreSesiones(a, b, tarea, max_turnos, enviar, ultima_respuesta, avisar, al_terminar,
		aprobar=aprobar, archivos_del_turno=lambda clave: cambiados.get(clave, []), al_apuntar=al_apuntar, al_cambiar=al_cambiar)
	conversacion.id_guardado = id_guardado
	ventana.__dict__["_conversacion_sesiones"] = conversacion
	return conversacion


def detener(ventana: Any) -> bool:
	conversacion = actual(ventana)
	if conversacion is None or not conversacion.activa:
		avisos.anunciar(_("No hay ninguna conversación entre sesiones en marcha."), interrumpir=True)
		return False
	conversacion.detener()
	return True


def detener_si_participa(ventana: Any, clave: str | None) -> bool:
	"""Control+punto en una de sus sesiones, aunque esté parada esperando a la otra, la detiene."""
	conversacion = actual(ventana)
	if conversacion is None or not conversacion.activa or not conversacion.participa(clave):
		return False
	conversacion.detener()
	return True


def revisar_relevo(ventana: Any) -> bool:
	"""Con «Aprobar cada relevo a mano»: la persona ve la respuesta que espera y decide."""
	conversacion = actual(ventana)
	pendiente = conversacion.pendiente if conversacion is not None and conversacion.activa else None
	if conversacion is None or pendiente is None:
		avisos.anunciar(_("No hay ningún relevo esperando tu aprobación."), interrumpir=True)
		return False
	dialogo = DialogoRelevo(ventana, pendiente.de.nombre, pendiente.a.nombre, pendiente.texto, conversacion.problema_relevo)
	try:
		if dialogo.ShowModal() != wx.ID_OK or dialogo.decision is None:
			return False
		decision, texto = dialogo.decision, dialogo.texto_final
	finally:
		dialogo.Destroy()
	if conversacion.pendiente is not pendiente:
		# Se ha parado mientras el cuadro estaba abierto.
		avisos.anunciar(_("La conversación ya no espera ese relevo. No se ha pasado nada."), interrumpir=True)
		return False
	if decision == "parar":
		conversacion.detener(_("la has parado al revisar el relevo. La respuesta de {d} no se ha pasado").format(d=pendiente.de.nombre))
		return True
	return conversacion.aprobar_relevo(texto)


def a_quien_va(ventana: Any, texto: str) -> tuple[list[str], str] | None:
	"""Si lo escrito va para una de las dos sesiones por su nombre: sus claves y el mensaje.

	Solo mientras hablan y con una de las dos abierta: en otra sesión, «@Codex» es texto normal.
	"""
	conversacion = actual(ventana)
	if conversacion is None or not conversacion.activa or not conversacion.participa(ventana.sesion_actual):
		return None
	destinos, cuerpo = dialogo_agentes.direccion(texto, conversacion.a, conversacion.b)
	return None if destinos is None else ([d.clave for d in destinos], cuerpo)


def apartar(ventana: Any, clave: str, texto: str) -> bool:
	"""Un mensaje que no es del relevo va a salir hacia una de las dos sesiones: su turno se queda fuera."""
	conversacion = actual(ventana)
	return conversacion is not None and conversacion.aparte(clave, texto)


def retirar_aparte(ventana: Any, clave: str) -> None:
	conversacion = actual(ventana)
	if conversacion is not None:
		conversacion.retirar_aparte(clave)


def _texto_guardada(ventana: Any, guardada: Any) -> str:
	conversacion = actual(ventana)
	if conversacion is not None and conversacion.id_guardado == guardada.id:
		return conversacion.texto_combinado()  # está viva: sabe más que la base de datos
	resumen = dialogo_agentes.resumen_guardada(guardada.nombre_a, guardada.nombre_b, guardada.turno, guardada.max_turnos,
		guardada.estado, guardada.motivo_fin)
	entradas = ventana.almacen.entradas_conversacion(guardada.id)
	return dialogo_agentes.componer_texto(resumen, [Intervencion(e.quien, e.texto, e.fecha, e.nota) for e in entradas])


def _mostrar(ventana: Any, texto: str) -> None:
	from app.ui.dialogo_texto import mostrar_texto

	mostrar_texto(ventana, _("Conversación entre sesiones"), _("&Lo que se han dicho:"), texto, copiable=True)


def ver(ventana: Any) -> bool:
	"""La conversación en marcha o la última: también la de antes de cerrar Ctrl+IA."""
	conversacion = actual(ventana)
	if conversacion is not None:
		_mostrar(ventana, conversacion.texto_combinado())
		return True
	lista = ventana.almacen.conversaciones_sesiones()
	if not lista:
		avisos.anunciar(_("Todavía no ha habido ninguna conversación entre sesiones. Empieza una con "
			"Control+Alt+Mayúsculas+R."), interrumpir=True)
		return False
	_mostrar(ventana, _texto_guardada(ventana, lista[0]))
	return True


def nombre_guardada(guardada: Any) -> str:
	fecha = time.strftime("%d/%m/%Y %H:%M", time.localtime(guardada.creada))
	return _("{f}: {a} y {b}, {n} turnos: {t}").format(f=fecha, a=guardada.nombre_a, b=guardada.nombre_b, n=guardada.turno,
		t=" ".join(guardada.tarea.split())[:60])


def guardadas(ventana: Any) -> bool:
	"""Todas las guardadas: para volver a leerlas o borrarlas."""
	lista = ventana.almacen.conversaciones_sesiones()
	if not lista:
		avisos.anunciar(_("No hay ninguna conversación entre sesiones guardada."), interrumpir=True)
		return False

	def mostrar(indice: int) -> None:
		_mostrar(ventana, _texto_guardada(ventana, lista[indice]))

	def borrar(indice: int) -> bool:
		guardada = lista[indice]
		conversacion = actual(ventana)
		if conversacion is not None and conversacion.activa and conversacion.id_guardado == guardada.id:
			avisos.anunciar(_("Esa conversación sigue en marcha: detenla antes de borrarla."), interrumpir=True)
			return False
		pregunta = _("¿Borrar la vista combinada de esta conversación? Los mensajes de cada sesión no se tocan.")
		if confirmar(dialogo, pregunta, _("Borrar la conversación guardada"), peligroso=True) != wx.ID_YES:
			return False
		ventana.almacen.borrar_conversacion_sesiones(guardada.id)
		del lista[indice]
		if conversacion is not None and conversacion.id_guardado == guardada.id:
			ventana.__dict__.pop("_conversacion_sesiones", None)
		avisos.anunciar(_("Borrada."), interrumpir=True)
		return True

	dialogo = DialogoConversacionesGuardadas(ventana, [nombre_guardada(g) for g in lista], mostrar, borrar)
	try:
		dialogo.ShowModal()
	finally:
		dialogo.Destroy()
	return True


def al_evento(ventana: Any, clave: str, evento: Evento) -> None:
	"""Pasa al relevo los eventos de los agentes (ya en el hilo de la interfaz)."""
	conversacion = actual(ventana)
	if conversacion is None or not conversacion.activa:
		return
	try:
		conversacion.al_evento(clave, evento)
	except Exception:  # noqa: BLE001 - un fallo del relevo nunca debe cerrar la ventana
		log.exception("Falló la conversación entre sesiones")
		conversacion.terminar(_("ha fallado algo dentro de Ctrl+IA; mira el registro"), error=True)
