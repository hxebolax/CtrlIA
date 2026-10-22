"""Agente Codex (OpenAI) a través de ``codex app-server``.

Ctrl+IA no llama a la API de OpenAI: lanza el programa oficial ``codex`` en
modo servidor (``codex app-server --listen stdio://``) y conversa con él por
líneas JSON, con la sesión de ChatGPT que el usuario ya tiene iniciada.

Decisiones de diseño (y su porqué):

- **Un único proceso compartido** por el proveedor. Cada conversación es un
  *thread* de Codex y todas viajan multiplexadas por ``threadId``. Arrancar
  Codex cuesta segundos y memoria; uno basta. Se lanza perezosamente y, si
  muere, se relanza en la SIGUIENTE operación (la que falló no se reintenta:
  un mensaje cuyo envío quedó en duda nunca se reenvía solo).
- **Respuestas emparejadas por ``id``**, nunca por orden de llegada: Codex
  puede contestar peticiones concurrentes en cualquier orden.
- **Entorno filtrado**: se quitan las claves de API de OpenAI/Azure/Codex
  para que Codex use la suscripción de ChatGPT y jamás cobre por uso.
- El hilo lector del proceso nunca espera a una respuesta de Codex (se
  bloquearía a sí mismo): todo lo que necesita preguntar algo a Codex desde
  una notificación se lanza en un hilo aparte.
- Los permisos esperan al usuario sin límite de tiempo; si el turno termina o
  se interrumpe antes, se retiran con ``PERMISO_RETIRADO``.

Este módulo no importa wx; los eventos salen de hilos de fondo.
"""

from __future__ import annotations

import collections
import itertools
import json
import logging
import os
import posixpath
import re
import shlex
import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.version import APP_VERSION
from app.services.agentes import elicitacion, remoto
from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	Capacidades,
	ClaseOpcion,
	Conversacion,
	Estado,
	EstadoProveedor,
	Evento,
	OpcionLista,
	OpcionPermiso,
	Pregunta,
	Proveedor,
	ReceptorEventos,
	RespuestaPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.agentes.procesos import (
	ES_WINDOWS,
	ProcesoLineas,
	buscar_ejecutable,
	entorno_hijo,
	json_o_nada,
	version_de,
)

log = logging.getLogger(__name__)

#: Espera máxima por una respuesta de Codex (salvo ``turn/start``, que no tiene).
TIEMPO_PETICION = 30.0
#: Espera de las consultas de ``detectar`` (no debe colgar el panel de agentes).
TIEMPO_DETECCION = 15.0
#: Mensajes que se pueden encolar mientras Codex trabaja.
MAX_COLA = 20
#: Caracteres de la salida de una orden que se pasan a la interfaz.
MAX_SALIDA = 4000
#: Páginas máximas al listar (evita bucles si el servidor repite cursores).
MAX_PAGINAS = 50

#: Variables que harían que Codex usase una clave de API (cobro por uso).
VARIABLES_PROHIBIDAS = (
	"CODEX_API_KEY",
	"CODEX_BASE_URL",
	"CODEX_CHATGPT_BASE_URL",
	"CODEX_AUTH_TOKEN",
	"CHATGPT_BASE_URL",
	"AZURE_API_KEY",
)
PREFIJOS_PROHIBIDOS = ("OPENAI_", "AZURE_OPENAI_")

#: Código JSON-RPC «método no encontrado»: lo que respondemos a lo que no sabemos tratar.
ERROR_NO_ADMITIDO = -32601
TEXTO_NO_ADMITIDO = "Ctrl+IA no admite esta petición"
#: Peticiones del servidor que no son para la persona: se rechazan sin avisar.
PETICIONES_SIN_PERSONA = frozenset({"item/tool/call", "account/chatgptAuthTokens/refresh", "attestation/generate"})


class ErrorCodex(Exception):
	"""Fallo al hablar con Codex.

	:param muerto: el proceso no está vivo (la conversación lo anuncia aparte).
	:param tiempo_agotado: no hubo respuesta a tiempo; la petición queda en duda.
	"""

	def __init__(self, mensaje: str, codigo: int | None = None, *, muerto: bool = False, tiempo_agotado: bool = False) -> None:
		super().__init__(mensaje)
		self.mensaje = mensaje
		self.codigo = codigo
		self.muerto = muerto
		self.tiempo_agotado = tiempo_agotado


# --------------------------------------------------------------------------
# Modos de permiso y opciones
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Modo:
	id: str
	etiqueta: str
	ayuda: str
	sandbox: str
	aprobacion: str
	politica: dict[str, Any]


_MODOS: tuple[_Modo, ...] = (
	_Modo(
		"solo_lectura",
		"Solo lectura",
		"Codex puede leer tus archivos, pero te pedirá permiso antes de cambiar nada o ejecutar órdenes que escriban.",
		"read-only",
		"on-request",
		{"type": "readOnly"},
	),
	_Modo(
		"espacio_trabajo",
		"Trabajar en la carpeta del proyecto",
		"Codex puede leer y modificar archivos dentro de la carpeta del proyecto. Te preguntará antes de salir de ella o de usar internet.",
		"workspace-write",
		"on-request",
		{"type": "workspaceWrite"},
	),
	_Modo(
		"sin_preguntas",
		"Carpeta del proyecto sin preguntar",
		"Como el anterior, pero Codex nunca te pide permiso: si algo no se puede hacer dentro de la carpeta, simplemente no lo hace.",
		"workspace-write",
		"never",
		{"type": "workspaceWrite"},
	),
	_Modo(
		"acceso_total",
		"Acceso total al equipo (peligroso)",
		"Codex puede leer, cambiar y borrar cualquier archivo del equipo y usar internet sin restricciones. Úsalo solo si confías plenamente en lo que le pides.",
		"danger-full-access",
		"on-request",
		{"type": "dangerFullAccess"},
	),
)
MODO_PREDETERMINADO = "espacio_trabajo"


def _modo(identificador: str) -> _Modo:
	for modo in _MODOS:
		if modo.id == identificador:
			return modo
	return next(modo for modo in _MODOS if modo.id == MODO_PREDETERMINADO)


_ESFUERZOS = (
	OpcionLista("", "Predeterminado del modelo"),
	OpcionLista("low", "Bajo", "Responde antes pensando menos."),
	OpcionLista("medium", "Medio", "Equilibrio entre rapidez y calidad."),
	OpcionLista("high", "Alto", "Piensa más; tarda más y gasta más cuota."),
	OpcionLista("xhigh", "Muy alto", "El máximo razonamiento; lento y con mucho gasto de cuota."),
)

#: Decisiones de aprobación v2: id del protocolo → (etiqueta, clase).
_DECISIONES: dict[str, tuple[str, ClaseOpcion]] = {
	"accept": ("Permitir una vez", ClaseOpcion.PERMITIR),
	"acceptForSession": ("Permitir durante esta sesión", ClaseOpcion.PERMITIR_SESION),
	"decline": ("Denegar", ClaseOpcion.DENEGAR),
	"cancel": ("Denegar y detener el turno", ClaseOpcion.CANCELAR),
}
_DECISIONES_POR_DEFECTO = ("accept", "acceptForSession", "decline", "cancel")

#: Peticiones antiguas (v1): opción de Ctrl+IA → decisión ``ReviewDecision``.
_DECISIONES_LEGADO: dict[str, Any] = {
	"accept": "approved",
	"acceptForSession": "approved_for_session",
	"decline": None,  # se construye con el motivo
	"cancel": "abort",
}

#: Opción propia de Ctrl+IA: recuerda el prefijo de la orden y contesta «Permitir una vez» por ti.
OPCION_PERMITIR_SESION = "ctrlia_permitir_sesion"
#: Programas para los que nunca se ofrece permitir «con cualquier argumento»: con
#: ellos, cualquier argumento puede ser cualquier cosa.
INTERPRETES = frozenset({
	"bash", "sh", "zsh", "fish", "dash", "ksh", "pwsh", "powershell", "cmd", "wsl", "python", "python3", "py",
	"pythonw", "node", "deno", "bun", "ruby", "perl", "php", "lua", "env", "sudo", "doas", "su", "runas", "xargs",
	"exec", "eval", "nohup", "time", "start", "call", "npx", "uvx", "pipx", "dotnet", "java", "osascript", "cscript",
	"wscript", "mshta", "rundll32", "regsvr32",
})
#: Lo que hace que una orden no sea «una sola orden sencilla».
_RE_ORDEN_COMPUESTA = re.compile(r"&&|\|\||[;|&<>`\r\n$^]|%[A-Za-z_]+%")
#: Opciones de git que una regla «con cualquier argumento» nunca cubre (AIChat #333 y #338): fuerzan,
#: borran o reflejan ramas, o cambian el repositorio, la configuración o el programa que se ejecuta.
_GIT_OPCIONES_PELIGROSAS = (
	"--force", "--force-with-lease", "--force-if-includes", "--delete", "--mirror", "--prune", "--prune-tags",
	"--hard", "--git-dir", "--work-tree", "--exec-path", "--config-env", "--upload-pack", "--receive-pack",
	"--exec", "--namespace", "--repo", "--remote", "--output", "--extcmd", "--tool",
)
#: Subórdenes de git que cambian la configuración o ejecutan otros programas: nunca «con cualquier argumento».
_GIT_SUBORDENES_PELIGROSAS = frozenset({
	"config", "submodule", "bisect", "filter-branch", "difftool", "mergetool", "daemon", "instaweb", "credential",
	"hook", "update-ref", "reflog", "replace", "remote", "gc", "prune",
})
#: Subórdenes de git que hablan con otro repositorio (su ``+refspec`` fuerza; su destino puede ser una ruta).
_GIT_SUBORDENES_REMOTAS = frozenset({"push", "fetch", "pull"})
#: Subórdenes de git en las que ``-d``/``-D`` borran.
_GIT_BORRAN_CON_D = frozenset({"push", "branch", "tag"})
#: Entradas que se revisan como mucho al buscar repositorios git anidados (más: se pregunta).
LIMITE_EXPLORACION_GIT = 20000
#: Ayuda de «Permitir durante esta sesión» de Codex, que Codex recuerda él mismo (AIChat #298).
AYUDA_SESION_CODEX = (
	"Codex lo recordará hasta que cierres esta sesión o se reinicie Codex, aunque mientras tanto cambies a un "
	"modo de permisos más restrictivo: cambiar el modo no lo retira."
)
#: Restricción de cada sandbox de Codex: cuanto más bajo, más restrictivo.
_NIVEL_SANDBOX = {"readOnly": 0, "workspaceWrite": 1, "dangerFullAccess": 2}
_NOMBRE_SANDBOX = {
	"readOnly": "solo lectura",
	"workspaceWrite": "escritura en la carpeta del proyecto",
	"dangerFullAccess": "acceso total",
}

_ESTADOS_PLAN = {"pending": "pendiente", "inProgress": "en curso", "completed": "hecho"}
_TIPOS_CAMBIO = {"add": "nuevo", "delete": "borrado", "update": "modificado"}

_MENSAJES_ERROR = {
	"contextWindowExceeded": "La conversación ya no cabe en el contexto del modelo. Empieza una nueva o pide a Codex que la resuma.",
	"sessionBudgetExceeded": "Se ha agotado el presupuesto de esta sesión de Codex.",
	"usageLimitExceeded": "Has alcanzado el límite de uso de tu plan de ChatGPT. Espera a que se reinicie o revisa tu plan.",
	"rateLimitExceeded": "Demasiadas peticiones seguidas a OpenAI. Espera un poco y vuelve a intentarlo.",
	"serverOverloaded": "Los servidores de OpenAI están saturados. Prueba de nuevo dentro de un rato.",
	"unauthorized": "Codex no tiene la sesión iniciada o ha caducado. Inicia sesión de nuevo con tu cuenta de ChatGPT.",
	"internalServerError": "OpenAI ha tenido un error interno. Vuelve a intentarlo.",
	"sandboxError": "Ha fallado el entorno aislado (sandbox) de Codex.",
	"cyberPolicy": "OpenAI ha bloqueado la petición por su política de seguridad.",
	"misalignmentPolicyViolation": "OpenAI ha bloqueado la petición por su política de uso.",
	"tooManyDenials": "Codex se ha detenido porque se le han denegado demasiadas acciones.",
	"httpConnectionFailed": "No se pudo conectar con OpenAI. Comprueba tu conexión a internet.",
	"responseStreamConnectionFailed": "No se pudo conectar con OpenAI. Comprueba tu conexión a internet.",
	"responseStreamDisconnected": "Se ha cortado la conexión con OpenAI a mitad de la respuesta.",
	"responseTooManyFailedAttempts": "OpenAI no ha respondido tras varios intentos.",
	"activeTurnNotSteerable": "Codex no admite mensajes nuevos en este momento del turno.",
}


#: Error de un modelo retirado o que la cuenta ya no puede usar (AIChat #302).
MENSAJE_MODELO_NO_DISPONIBLE = (
	"El modelo de esta conversación ya no está disponible en Codex para tu cuenta. Elige otro modelo en "
	"Opciones de la sesión (Ctrl+Mayús+O) y vuelve a enviar el mensaje."
)
#: Fallo al resumir el historial antes del turno («remote compact task»), sin más pistas.
MENSAJE_RESUMEN_FALLIDO = (
	"Codex no ha podido resumir el historial de esta conversación antes de responder. Suele pasar cuando el "
	"modelo original de la conversación ya no está disponible o los servidores de OpenAI están saturados. "
	"Vuelve a intentarlo; si se repite, elige otro modelo en Opciones de la sesión (Ctrl+Mayús+O) o empieza "
	"una sesión nueva."
)
_RE_MODELO_NO_DISPONIBLE = re.compile(
	r"model[^.]{0,80}\b(?:is )?not supported|unknown model|model[^.]{0,40}(?:does not exist|not found|deprecated|retired)",
	re.IGNORECASE,
)


def _texto_error_modelo(mensaje: str) -> str:
	"""Explicación de un error de modelo retirado o de resumen fallido; ``""`` si no es eso."""
	if _RE_MODELO_NO_DISPONIBLE.search(mensaje):
		return MENSAJE_MODELO_NO_DISPONIBLE
	if "compact task" in mensaje.lower():
		return MENSAJE_RESUMEN_FALLIDO
	return ""


def _texto_error(error: Any) -> str:
	"""Mensaje en español para un ``TurnError`` sin volcar cuerpos crudos largos."""
	if not isinstance(error, dict):
		return "Codex ha devuelto un error."
	explicado = _texto_error_modelo(str(error.get("message") or "") + " " + str(error.get("additionalDetails") or ""))
	if explicado:
		return explicado
	info = error.get("codexErrorInfo")
	clave = info if isinstance(info, str) else (next(iter(info), "") if isinstance(info, dict) and info else "")
	if clave in _MENSAJES_ERROR:
		return _MENSAJES_ERROR[clave]
	mensaje = str(error.get("message") or "").strip()
	if not mensaje:
		return "Codex ha devuelto un error."
	return "Codex ha devuelto un error: " + _recortar_linea(mensaje, 300)


def _recortar_linea(texto: str, maximo: int) -> str:
	texto = " ".join(texto.split())
	return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def _recortar(texto: str, maximo: int = MAX_SALIDA) -> str:
	"""Recorta salidas largas: la interfaz y el lector de pantalla no necesitan megas."""
	if len(texto) <= maximo:
		return texto
	return texto[:maximo] + "\n[Salida recortada: faltan %d caracteres]" % (len(texto) - maximo)


def _texto_ventana(minutos: Any) -> str:
	if not isinstance(minutos, int) or minutos <= 0:
		return ""
	if minutos == 10080:
		return "semanal"
	if minutos % 1440 == 0:
		dias = minutos // 1440
		return "de 1 día" if dias == 1 else "de %d días" % dias
	if minutos % 60 == 0:
		horas = minutos // 60
		return "de 1 hora" if horas == 1 else "de %d horas" % horas
	return "de %d minutos" % minutos


def _en_hilo(funcion: Callable[..., Any], *args: Any, nombre: str = "codex-tarea") -> None:
	"""Ejecuta ``funcion`` en un hilo de fondo (nunca en el lector del proceso)."""
	threading.Thread(target=funcion, args=args, name=nombre, daemon=True).start()


# --------------------------------------------------------------------------
# Conexión con el proceso app-server
# --------------------------------------------------------------------------


class _Espera:
	__slots__ = ("evento", "resultado", "error")

	def __init__(self) -> None:
		self.evento = threading.Event()
		self.resultado: Any = None
		self.error: ErrorCodex | None = None


class _ServidorCodex:
	"""Un proceso ``codex app-server`` con su protocolo de peticiones y respuestas.

	Reparte las notificaciones y peticiones del servidor a la conversación que
	tiene registrado el ``threadId`` correspondiente.
	"""

	def __init__(self, orden: Sequence[str], al_morir: Callable[[_ServidorCodex], None] | None = None,
			entorno: dict[str, str] | None = None) -> None:
		self._ids = itertools.count(1)
		self._esperas: dict[Any, _Espera] = {}
		self._hilos: dict[str, ConversacionCodex] = {}
		self._candado = threading.Lock()
		self._al_morir = al_morir
		self.muerto = False
		self.codigo_salida: int | None = None
		self.info: dict[str, Any] = {}
		self._proceso = ProcesoLineas(
			orden,
			self._al_linea,
			self._al_terminar,
			entorno=entorno if entorno is not None else entorno_hijo(VARIABLES_PROHIBIDAS, PREFIJOS_PROHIBIDOS),
			nombre="codex",
		)

	# -- Ciclo de vida --------------------------------------------------------

	def iniciar(self, tiempo: float = TIEMPO_PETICION) -> None:
		"""Lanza el proceso y hace el saludo ``initialize``/``initialized``."""
		try:
			self._proceso.iniciar()
		except OSError as error:
			self.muerto = True
			raise ErrorCodex("No se pudo lanzar Codex: %s" % error, muerto=True) from error
		try:
			self.info = self.peticion(
				"initialize",
				{
					"clientInfo": {"name": "ctrl_ia", "title": "Ctrl+IA", "version": APP_VERSION},
					"capabilities": {"experimentalApi": True},
				},
				tiempo=tiempo,
			) or {}
			if not self.notificar("initialized", {}):
				raise ErrorCodex("Codex se cerró durante el arranque", muerto=True)
		except ErrorCodex:
			self.terminar()
			raise

	@property
	def vivo(self) -> bool:
		return not self.muerto and self._proceso.vivo

	def terminar(self) -> None:
		self._proceso.cerrar_entrada()
		self._proceso.terminar()

	def ultimo_error(self) -> str:
		"""Última línea del error estándar (recortada) para explicar un cierre."""
		lineas = self._proceso.errores_recientes(5)
		return _recortar_linea(lineas[-1], 200) if lineas else ""

	# -- Registro de conversaciones -------------------------------------------

	def registrar(self, id_hilo: str, conversacion: ConversacionCodex) -> None:
		with self._candado:
			if self.muerto:
				raise ErrorCodex("Codex se ha cerrado", muerto=True)
			self._hilos[id_hilo] = conversacion

	def quitar(self, id_hilo: str) -> None:
		with self._candado:
			self._hilos.pop(id_hilo, None)

	def _conversacion(self, id_hilo: Any) -> ConversacionCodex | None:
		with self._candado:
			return self._hilos.get(id_hilo) if isinstance(id_hilo, str) else None

	def _todas(self) -> list[ConversacionCodex]:
		with self._candado:
			return list(self._hilos.values())

	# -- Envío ----------------------------------------------------------------

	def peticion(self, metodo: str, params: dict[str, Any] | None = None, tiempo: float | None = TIEMPO_PETICION) -> Any:
		"""Envía una petición y espera SU respuesta (emparejada por ``id``).

		``tiempo=None`` espera indefinidamente (solo ``turn/start``): si el
		proceso muere, la espera se despierta igualmente con error.
		"""
		espera = _Espera()
		with self._candado:
			if self.muerto:
				raise ErrorCodex("Codex no está en marcha", muerto=True)
			identificador = next(self._ids)
			self._esperas[identificador] = espera
		mensaje = {"id": identificador, "method": metodo, "params": params if params is not None else {}}
		if not self._proceso.escribir_json(mensaje):
			with self._candado:
				self._esperas.pop(identificador, None)
			raise ErrorCodex("No se pudo escribir a Codex", muerto=True)
		if not espera.evento.wait(tiempo):
			with self._candado:
				self._esperas.pop(identificador, None)
			log.warning("Codex no respondió a %s en %s s", metodo, tiempo)
			raise ErrorCodex("Codex no ha respondido a tiempo", tiempo_agotado=True)
		if espera.error is not None:
			raise espera.error
		return espera.resultado

	def notificar(self, metodo: str, params: dict[str, Any]) -> bool:
		return self._proceso.escribir_json({"method": metodo, "params": params})

	def responder(self, identificador: Any, resultado: Any) -> bool:
		return self._proceso.escribir_json({"id": identificador, "result": resultado})

	def responder_error(self, identificador: Any, codigo: int, mensaje: str) -> bool:
		return self._proceso.escribir_json({"id": identificador, "error": {"code": codigo, "message": mensaje}})

	# -- Recepción (hilo lector) ----------------------------------------------

	def _al_linea(self, linea: str) -> None:
		mensaje = json_o_nada(linea)
		if mensaje is None:
			log.debug("Línea de Codex que no es JSON: %s", linea[:200])
			return
		metodo = mensaje.get("method")
		if "id" in mensaje and isinstance(metodo, str):
			self._peticion_servidor(mensaje["id"], metodo, mensaje.get("params") or {})
		elif "id" in mensaje:
			self._respuesta(mensaje)
		elif isinstance(metodo, str):
			self._notificacion(metodo, mensaje.get("params") or {})
		else:
			log.debug("Mensaje de Codex sin forma conocida: %s", linea[:200])

	def _respuesta(self, mensaje: dict[str, Any]) -> None:
		with self._candado:
			espera = self._esperas.pop(mensaje.get("id"), None)
		if espera is None:
			log.debug("Respuesta de Codex sin petición pendiente (id %s)", mensaje.get("id"))
			return
		error = mensaje.get("error")
		if error is not None:
			if isinstance(error, dict):
				texto = str(error.get("message") or "error desconocido")
				codigo = error.get("code") if isinstance(error.get("code"), int) else None
			else:
				texto, codigo = str(error), None
			espera.error = ErrorCodex(_recortar_linea(texto, 500), codigo)
		else:
			espera.resultado = mensaje.get("result")
		espera.evento.set()

	def _notificacion(self, metodo: str, params: dict[str, Any]) -> None:
		if not isinstance(params, dict):
			return
		id_hilo = params.get("threadId")
		if id_hilo:
			conversacion = self._conversacion(id_hilo)
			if conversacion is not None:
				conversacion._al_notificacion(metodo, params)  # noqa: SLF001 - reparto interno del módulo
			else:
				log.debug("Notificación %s de un hilo que no es nuestro", metodo)
			return
		if metodo in ("account/rateLimits/updated", "warning", "mcpServer/startupStatus/updated"):
			for conversacion in self._todas():
				conversacion._al_notificacion(metodo, params)  # noqa: SLF001
		elif metodo in ("configWarning", "deprecationNotice"):
			log.info("Aviso de Codex (%s): %s", metodo, _recortar_linea(json.dumps(params, ensure_ascii=False), 300))
		else:
			log.debug("Notificación global de Codex ignorada: %s", metodo)

	def _peticion_servidor(self, identificador: Any, metodo: str, params: dict[str, Any]) -> None:
		id_hilo = params.get("threadId") or params.get("conversationId") if isinstance(params, dict) else None
		conversacion = self._conversacion(id_hilo)
		if conversacion is None:
			log.warning("Codex pidió %s para un hilo desconocido; se rechaza", metodo)
			self.responder_error(identificador, ERROR_NO_ADMITIDO, TEXTO_NO_ADMITIDO)
			return
		conversacion._al_peticion(identificador, metodo, params)  # noqa: SLF001

	def _al_terminar(self, codigo: int | None) -> None:
		with self._candado:
			self.muerto = True
			self.codigo_salida = codigo
			esperas = list(self._esperas.values())
			self._esperas.clear()
			conversaciones = list(self._hilos.values())
			self._hilos.clear()
		for espera in esperas:
			espera.error = ErrorCodex("Codex se ha cerrado", muerto=True)
			espera.evento.set()
		for conversacion in conversaciones:
			conversacion._servidor_caido(self, codigo, self.ultimo_error())  # noqa: SLF001
		if self._al_morir is not None:
			try:
				self._al_morir(self)
			except Exception:  # noqa: BLE001 - el aviso no puede tumbar el hilo lector
				log.exception("Fallo al avisar del cierre de Codex")


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Pendiente:
	"""Petición del servidor que espera respuesta del usuario."""

	rpc_id: Any
	metodo: str
	params: dict[str, Any]
	solicitud: SolicitudPermiso


class ConversacionCodex(Conversacion):
	"""Un *thread* de Codex dentro del proceso compartido del proveedor."""

	def __init__(self, proveedor: ProveedorCodex, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> None:
		super().__init__(ajustes, al_evento)
		self._proveedor = proveedor
		self._candado = threading.RLock()
		self._srv: _ServidorCodex | None = None
		self._hilo_id = ""
		self._modo_aplicado = ""
		self._turno_activo = False
		self._turno_id: str | None = None
		self._turnos_terminados: collections.deque[str] = collections.deque(maxlen=20)
		#: Servidores MCP cuyo fallo de arranque ya se ha avisado (no se repite hasta que vuelvan a estar listos).
		self._mcp_fallidos: set[str] = set()
		self._detenido = False
		self._interrumpir_pendiente = False
		self._error_en_turno = False
		self._compactado_en: str | None = None
		self._cola: list[tuple[str, tuple[Adjunto, ...]]] = []
		self._solicitudes: dict[str, _Pendiente] = {}
		self._cambios_archivo: dict[str, list[dict[str, Any]]] = {}
		self._cerrada = False
		#: «Enviar ahora» detuvo el turno: al terminar, se envía lo primero de la cola.
		self._relanzar_tras_detener = False
		#: Mensajes añadidos al turno (``turn/steer``) que Codex aún no ha confirmado.
		self._anadidos_pendientes = 0
		#: Tipo del sandbox que Codex aplica de verdad (``readOnly``...), según su última respuesta; «» si no se sabe.
		self._sandbox_efectivo = ""
		#: Ya se avisó de que Codex aplica un sandbox más restrictivo que el pedido (una vez por sesión, AIChat #297).
		self._aviso_sandbox_dado = False

	# -- API pública ----------------------------------------------------------

	@property
	def anadidos_pendientes(self) -> int:
		"""Mensajes añadidos al turno en marcha que Codex todavía no ha aceptado."""
		with self._candado:
			return self._anadidos_pendientes

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		"""Empieza un turno o, si Codex ya trabaja, encola el mensaje."""
		with self._candado:
			if self._cerrada:
				cerrada = True
			else:
				cerrada = False
				if self._turno_activo:
					if len(self._cola) >= MAX_COLA:
						llena = True
					else:
						llena = False
						self._cola.append((texto, tuple(adjuntos)))
					encolado = True
				else:
					encolado = False
					self._turno_activo = True
					self._detenido = False
					self._interrumpir_pendiente = False
					self._error_en_turno = False
		if cerrada:
			self._emitir(TipoEvento.ERROR, "Esta conversación con Codex está cerrada.")
			return
		if encolado:
			if llena:
				self._emitir(TipoEvento.AVISO, "La cola está llena (%d mensajes). Espera a que Codex termine." % MAX_COLA)
			else:
				self._emitir(TipoEvento.AVISO, "Mensaje en cola: se enviará cuando Codex termine.", en_cola=len(self._cola))
			return
		self._cambiar_estado(Estado.TRABAJANDO)
		_en_hilo(self._arrancar_turno, texto, tuple(adjuntos), nombre="codex-turno")

	def enviar_ahora(self, texto: str) -> None:
		"""Detiene el turno en curso y, cuando Codex confirma que ha parado, envía el mensaje."""
		with self._candado:
			ocupada = self._turno_activo and not self._cerrada
			if ocupada:
				# Primero de la cola: es lo que se envía en cuanto el turno se detenga.
				self._cola.insert(0, (texto, ()))
				self._relanzar_tras_detener = True
		if not ocupada:
			self.enviar(texto)
			return
		self._emitir(TipoEvento.AVISO, "Deteniendo a Codex para pasarle tu mensaje.")
		self.detener()

	def inventario(self) -> Any:
		"""Servidores MCP, habilidades y complementos de Codex, preguntados a su servidor (bloquea)."""
		from app.services import inventario

		try:
			srv = self._proveedor.servidor(self.ajustes.extra)
		except ErrorCodex as error:
			resultado = inventario.Inventario("Codex")
			resultado.nota = "No se ha podido preguntar a Codex: %s" % error
			return resultado
		respuestas: list[Any] = []
		carpeta = str(self.ajustes.carpeta) if not remoto.es_remota(self.ajustes.extra) else ""
		with self._candado:
			hilo = self._hilo_id if self._srv is srv else ""
		# Con el hilo salen también sus servidores propios (las herramientas de Ctrl+IA van en su «config»).
		estado_mcp = {"threadId": hilo} if hilo else {}
		for metodo, params in (("mcpServerStatus/list", estado_mcp), ("skills/list", {"cwds": [carpeta]} if carpeta else {}),
				("plugin/list", {})):
			try:
				respuestas.append(srv.peticion(metodo, params))
			except Exception:  # noqa: BLE001 - una parte que falla no impide enseñar las demás
				log.info("Codex no ha contestado a %s", metodo, exc_info=True)
				respuestas.append(None)
		return inventario.desde_codex(*respuestas)

	def anadir_al_turno(self, texto: str) -> None:
		"""Mete el mensaje en el turno en marcha (``turn/steer``) sin detenerlo.

		Si el turno ya terminó o Codex lo rechaza, el texto vuelve a la interfaz
		(``devolver_texto``): nunca se convierte por su cuenta en un turno nuevo.
		"""
		with self._candado:
			turno = self._turno_id if self._turno_activo and not self._cerrada else None
			if turno:
				self._anadidos_pendientes += 1
		if not turno:
			self._devolver_texto(texto, "Codex no está trabajando en un turno ahora mismo; tu mensaje no se ha enviado.")
			return
		_en_hilo(self._anadir_al_turno, texto, turno, nombre="codex-anadir")

	def detener(self) -> None:
		"""Interrumpe el turno; lo encolado se queda para :meth:`pendientes`."""
		with self._candado:
			if not self._turno_activo:
				return
			self._detenido = True
			turno = self._turno_id
			if not turno:
				self._interrumpir_pendiente = True
				return
		_en_hilo(self._interrumpir, turno, nombre="codex-detener")

	def pendientes(self) -> list[str]:
		"""Saca de la cola los mensajes aún no enviados y los devuelve.

		Quedan fuera de la cola: la interfaz los devuelve al cuadro de texto y
		el usuario decide si los vuelve a mandar.
		"""
		with self._candado:
			textos = [texto for texto, _adjuntos in self._cola]
			self._cola.clear()
		return textos

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		with self._candado:
			pendiente = self._solicitudes.pop(solicitud_id, None)
			srv = self._srv
			quedan = bool(self._solicitudes)
			activo = self._turno_activo
		if pendiente is None:
			log.warning("Respuesta a una solicitud de Codex que ya no existe: %s", solicitud_id)
			return
		regla = str(pendiente.solicitud.datos.get("regla") or "")
		if respuesta.opcion == OPCION_PERMITIR_SESION and regla:
			# Ctrl+IA guarda la regla y a Codex le contesta «una vez»: Codex no recuerda
			# nada, así que quitar la regla surte efecto en la siguiente petición.
			with self._candado:
				if regla not in self.ajustes.reglas_permitidas:
					self.ajustes.reglas_permitidas.append(regla)
			respuesta = RespuestaPermiso(opcion="accept", mensaje=respuesta.mensaje, respuestas=respuesta.respuestas)
		resultado = _resultado_peticion(pendiente, respuesta)
		# El estado cambia ANTES de contestar: si no, el fin de turno que provoca
		# la respuesta podría llegar antes y quedaríamos en «Trabajando» sin turno.
		if not quedan and activo:
			self._cambiar_estado(Estado.TRABAJANDO)
		if srv is None or not srv.responder(pendiente.rpc_id, resultado):
			self._emitir(TipoEvento.ERROR, "No se pudo entregar tu respuesta a Codex: el programa ya no está en marcha.")

	def cerrar(self) -> None:
		with self._candado:
			if self._cerrada:
				return
			self._cerrada = True
			srv = self._srv
			self._srv = None
			hilo = self._hilo_id
			turno = self._turno_id if self._turno_activo else None
			retirar = list(self._solicitudes)
			self._solicitudes.clear()
			self._turno_activo = False
		self._retirar_herramientas_ctrlia()
		for solicitud_id in retirar:
			self._emitir(TipoEvento.PERMISO_RETIRADO, id=solicitud_id)
		if srv is not None and hilo:
			srv.quitar(hilo)
			_en_hilo(self._despedirse, srv, hilo, turno, nombre="codex-cerrar")
		self._cambiar_estado(Estado.CERRADA)

	# -- Trabajo en hilos de fondo --------------------------------------------

	def _asegurar_hilo(self) -> _ServidorCodex:
		"""Garantiza un *thread* de Codex cargado en el proceso actual.

		Si el proceso se relanzó, el hilo se retoma con ``thread/resume``.
		"""
		srv = self._proveedor.servidor(self.ajustes.extra)
		with self._candado:
			if self._srv is srv and self._hilo_id:
				return srv
		ajustes = self.ajustes
		modo = _modo(ajustes.modo_permiso)
		comunes: dict[str, Any] = {
			"cwd": remoto.carpeta_remota(ajustes.extra) or str(ajustes.carpeta),
			"approvalPolicy": modo.aprobacion,
			"sandbox": modo.sandbox,
			"approvalsReviewer": "user",
		}
		if ajustes.bifurcar_de and not ajustes.id_externo:
			metodo = "thread/fork"
		elif ajustes.id_externo:
			metodo = "thread/resume"
		else:
			metodo = "thread/start"
		# «Continuar aquí» (copia u original): nunca se propone a Codex un modelo que ya no está en su lista (AIChat #302).
		continuacion = metodo == "thread/fork" or (metodo == "thread/resume" and bool(ajustes.extra.get("original")))
		disponibles = self._proveedor.modelos_disponibles(srv) if continuacion else None
		aviso_modelo = ""
		if disponibles and ajustes.modelo and ajustes.modelo not in disponibles:
			sustituto = disponibles[0]
			aviso_modelo = ("El modelo «%s» ya no está en la lista de Codex; esta sesión usará «%s». Para elegir otro, "
				"abre Opciones de la sesión (Ctrl+Mayús+O)." % (ajustes.modelo, sustituto))
			log.info("Modelo %s retirado en Codex; se usa %s", ajustes.modelo, sustituto)
			ajustes.modelo = sustituto
		if ajustes.modelo:
			comunes["model"] = ajustes.modelo
		comunes.update(self._params_herramientas_ctrlia())
		if metodo == "thread/fork":
			params = {"threadId": ajustes.bifurcar_de, "excludeTurns": True, **comunes}
		elif metodo == "thread/resume":
			params = {"threadId": ajustes.id_externo, "excludeTurns": True, **comunes}
		else:
			params = comunes
		respuesta = srv.peticion(metodo, params) or {}
		hilo = respuesta.get("thread") if isinstance(respuesta, dict) else None
		id_hilo = hilo.get("id") if isinstance(hilo, dict) else None
		if not isinstance(id_hilo, str) or not id_hilo:
			raise ErrorCodex("Codex no ha devuelto el identificador de la conversación")
		srv.registrar(id_hilo, self)
		with self._candado:
			self._srv = srv
			self._hilo_id = id_hilo
			self._modo_aplicado = modo.id
			ajustes.id_externo = id_hilo
			ajustes.bifurcar_de = ""
		modelo = str(respuesta.get("model") or ajustes.modelo or "")
		if disponibles and not ajustes.modelo and modelo and modelo not in disponibles:
			# La copia hereda el modelo de la original, que ya no existe: se pide el primero de la lista en cada turno.
			sustituto = disponibles[0]
			aviso_modelo = ("La conversación original usaba el modelo «%s», que ya no está en la lista de Codex; esta "
				"sesión usará «%s». Antes de responder, Codex resumirá el historial con el modelo nuevo, lo que puede "
				"tardar un poco. Para elegir otro, abre Opciones de la sesión (Ctrl+Mayús+O)." % (modelo, sustituto))
			log.info("La copia de Codex heredaba el modelo retirado %s; se usa %s", modelo, sustituto)
			with self._candado:
				ajustes.modelo = sustituto
			modelo = sustituto
		log.info("Hilo de Codex listo (%s) con %s", metodo, modelo or "modelo predeterminado")
		self._emitir(
			TipoEvento.INICIADO,
			"Codex está listo" + (" con el modelo %s" % modelo if modelo else ""),
			id_externo=id_hilo,
			modelo=modelo,
			modo=modo.id,
			retomada=metodo == "thread/resume",
			bifurcada=metodo == "thread/fork",
		)
		if aviso_modelo:
			self._emitir(TipoEvento.AVISO, aviso_modelo, modelo_sustituido=modelo)
		self._comprobar_sandbox(respuesta.get("sandbox") if isinstance(respuesta, dict) else None, modo, srv)
		if metodo == "thread/start" and ajustes.titulo:
			try:
				srv.peticion("thread/name/set", {"threadId": id_hilo, "name": ajustes.titulo})
			except ErrorCodex:
				log.debug("No se pudo poner título al hilo de Codex", exc_info=True)
		return srv

	def _params_herramientas_ctrlia(self) -> dict[str, Any]:
		"""Las herramientas de Ctrl+IA del hilo: el servidor MCP HTTP local con el token de la sesión.

		Solo en este equipo: Codex en otro equipo por SSH no llega al 127.0.0.1 de Windows.
		"""
		if remoto.es_remota(self.ajustes.extra):
			return {}
		herramientas = self._preparar_herramientas_ctrlia("Codex")
		if herramientas is None:
			return {}
		from app.services import herramientas_propias

		try:
			return {"config": herramientas_propias.config_codex(herramientas)}
		except OSError:
			log.warning("No se pudo arrancar el servidor de las herramientas de Ctrl+IA", exc_info=True)
			return {}

	def _comprobar_sandbox(self, sandbox: Any, modo: _Modo, srv: _ServidorCodex | None, *, preguntar: bool = True) -> None:
		"""Apunta el sandbox que Codex aplica y avisa (una vez por sesión) si es más restrictivo que el pedido.

		Codex en Windows rebaja a solo lectura una sesión con permiso de escritura si
		su aislamiento no está preparado (AIChat #297). La respuesta de ``thread/start``,
		``thread/resume`` y ``thread/fork`` y la notificación ``thread/settings/updated``
		dicen el sandbox efectivo. Con ``preguntar`` (nunca desde el hilo lector) se
		consulta además ``windowsSandbox/readiness`` para explicar el porqué.
		"""
		tipo = sandbox.get("type") if isinstance(sandbox, dict) else None
		if not isinstance(tipo, str) or not tipo:
			return
		pedido = str(modo.politica.get("type") or "")
		with self._candado:
			self._sandbox_efectivo = tipo
			avisar = (not self._aviso_sandbox_dado and tipo in _NIVEL_SANDBOX and pedido in _NIVEL_SANDBOX
				and _NIVEL_SANDBOX[tipo] < _NIVEL_SANDBOX[pedido])
			if avisar:
				self._aviso_sandbox_dado = True
		if not avisar:
			return
		texto = ("Codex trabaja en %s aunque pediste «%s»: no podrá hacer todo lo que permite ese modo."
			% (_NOMBRE_SANDBOX[tipo], modo.etiqueta))
		listo = None
		if preguntar and srv is not None and ES_WINDOWS and not remoto.es_remota(self.ajustes.extra):
			try:
				respuesta = srv.peticion("windowsSandbox/readiness", None)
				listo = respuesta.get("status") if isinstance(respuesta, dict) else None
			except ErrorCodex:
				log.debug("Codex no ha dicho si su aislamiento de Windows está listo", exc_info=True)
		if listo == "notConfigured":
			texto += (" El aislamiento de Windows de Codex (sandbox del agente) no está configurado en este equipo. "
				"Configúralo desde la app Codex y vuelve a abrir la sesión.")
		elif listo == "updateRequired":
			texto += (" El aislamiento de Windows de Codex (sandbox del agente) necesita actualizarse. Actualízalo "
				"desde la app Codex y vuelve a abrir la sesión.")
		else:
			texto += " Revisa la configuración de Codex (sandbox_mode y el aislamiento de Windows)."
		log.warning("Codex aplica el sandbox %s aunque se pidió %s (preparación de Windows: %s)", tipo, pedido, listo)
		self._emitir(TipoEvento.AVISO, texto, sandbox_efectivo=tipo, sandbox_pedido=pedido, aislamiento_windows=listo or "")

	def _arrancar_turno(self, texto: str, adjuntos: tuple[Adjunto, ...]) -> None:
		try:
			srv = self._asegurar_hilo()
		except ErrorCodex as error:
			self._fallo_turno("No se pudo abrir la conversación con Codex. " + _explicar(error))
			return
		with self._candado:
			if self._interrumpir_pendiente or self._cerrada:
				# Detenido antes de enviar: el mensaje no salió, vuelve a la cola
				# (detrás del de «Enviar ahora», si es eso lo que lo detuvo).
				self._cola.insert(1 if self._relanzar_tras_detener and self._cola else 0, (texto, adjuntos))
				abortar = True
			else:
				abortar = False
			params: dict[str, Any] = {"threadId": self._hilo_id,
				"input": _entrada(texto, adjuntos, remota=remoto.es_remota(self.ajustes.extra))}
			if self.ajustes.modelo:
				params["model"] = self.ajustes.modelo
			if self.ajustes.esfuerzo:
				params["effort"] = self.ajustes.esfuerzo
			modo = _modo(self.ajustes.modo_permiso)
			if modo.id != self._modo_aplicado:
				params["sandboxPolicy"] = modo.politica
				params["approvalPolicy"] = modo.aprobacion
		if abortar:
			self._terminar_sin_turno(interrumpido=True)
			return
		try:
			respuesta = srv.peticion("turn/start", params, tiempo=None) or {}
		except ErrorCodex as error:
			if error.muerto:
				self._fallo_turno("Codex se ha cerrado antes de empezar a responder. Tu mensaje no se reenviará automáticamente.")
			else:
				self._fallo_turno("Codex no ha aceptado el mensaje. " + _explicar(error))
			return
		with self._candado:
			if "sandboxPolicy" in params:
				self._modo_aplicado = modo.id
			turno = respuesta.get("turn") if isinstance(respuesta, dict) else None
			id_turno = turno.get("id") if isinstance(turno, dict) else None
			interrumpir = False
			if isinstance(id_turno, str) and self._turno_activo and id_turno not in self._turnos_terminados:
				if self._turno_id is None:
					self._turno_id = id_turno
				if self._interrumpir_pendiente:
					self._interrumpir_pendiente = False
					interrumpir = True
		if interrumpir:
			self._interrumpir(id_turno)

	def _anadir_al_turno(self, texto: str, turno: str) -> None:
		with self._candado:
			srv = self._srv
			hilo = self._hilo_id
		try:
			if srv is None or not hilo:
				raise ErrorCodex("Codex no está en marcha", muerto=True)
			srv.peticion("turn/steer", {"threadId": hilo, "input": _entrada(texto, ()), "expectedTurnId": turno})
		except ErrorCodex as error:
			if error.tiempo_agotado:
				motivo = "Codex no ha confirmado si recibió tu mensaje; revisa su respuesta antes de enviarlo otra vez."
			elif error.muerto:
				log.debug("Codex cayó al añadir un mensaje al turno", exc_info=True)
				motivo = "Codex se ha cerrado y no ha recibido tu mensaje."
			else:
				log.info("turn/steer rechazado: %s", error.mensaje)
				with self._candado:
					termino = turno in self._turnos_terminados or self._turno_id != turno
				motivo = ("El turno de Codex ya había terminado; tu mensaje no se ha enviado." if termino
					else "Codex no ha aceptado el mensaje en el turno en marcha. " + _explicar(error))
			self._devolver_texto(texto, motivo)
			return
		finally:
			with self._candado:
				self._anadidos_pendientes = max(0, self._anadidos_pendientes - 1)
		self._emitir(TipoEvento.AVISO, "Codex ha recibido tu mensaje en el turno en marcha.", anadido=True)

	def _devolver_texto(self, texto: str, motivo: str) -> None:
		"""Avisa de que un mensaje no se entregó y pide a la interfaz que lo devuelva al cuadro."""
		self._emitir(TipoEvento.AVISO, motivo + " El texto vuelve al cuadro de respuesta.", devolver_texto=texto)

	def _interrumpir(self, turno: str) -> None:
		with self._candado:
			srv = self._srv
			hilo = self._hilo_id
		if srv is None:
			return
		try:
			srv.peticion("turn/interrupt", {"threadId": hilo, "turnId": turno})
		except ErrorCodex as error:
			with self._candado:
				sigue = self._turno_activo and self._turno_id == turno
			if not error.muerto and sigue:
				log.warning("No se pudo interrumpir el turno de Codex: %s", error.mensaje)
				self._emitir(TipoEvento.AVISO, "Codex no ha confirmado la detención. " + _explicar(error))

	def _despedirse(self, srv: _ServidorCodex, hilo: str, turno: str | None) -> None:
		"""Al cerrar: detiene el turno y deja de escuchar el hilo (sin borrarlo)."""
		try:
			if turno:
				srv.peticion("turn/interrupt", {"threadId": hilo, "turnId": turno})
			srv.peticion("thread/unsubscribe", {"threadId": hilo})
		except ErrorCodex:
			log.debug("Codex no confirmó el cierre del hilo %s", hilo, exc_info=True)

	# -- Fin de turno ---------------------------------------------------------

	def _fallo_turno(self, mensaje: str) -> None:
		"""El turno no pudo empezar o se perdió. Idempotente: solo avisa una vez."""
		with self._candado:
			if not self._turno_activo:
				return
			self._turno_activo = False
			self._turno_id = None
			self._interrumpir_pendiente = False
			self._relanzar_tras_detener = False
			retirar = list(self._solicitudes)
			self._solicitudes.clear()
			en_cola = len(self._cola)
		for solicitud_id in retirar:
			self._emitir(TipoEvento.PERMISO_RETIRADO, id=solicitud_id)
		self._emitir(TipoEvento.ERROR, mensaje)
		self._emitir(TipoEvento.FIN_TURNO, "El turno ha fallado", correcto=False, interrumpido=False, resumen=mensaje)
		self._cambiar_estado(Estado.ERROR)
		self._avisar_cola(en_cola)

	def _terminar_sin_turno(self, interrumpido: bool) -> None:
		siguiente: tuple[str, tuple[Adjunto, ...]] | None = None
		with self._candado:
			self._turno_activo = False
			self._turno_id = None
			self._interrumpir_pendiente = False
			self._detenido = False
			if self._relanzar_tras_detener and self._cola and not self._cerrada:
				siguiente = self._cola.pop(0)
				self._turno_activo = True
			self._relanzar_tras_detener = False
			en_cola = len(self._cola)
		self._emitir(TipoEvento.FIN_TURNO, "Detenido", correcto=False, interrumpido=interrumpido, resumen="")
		if siguiente is not None:
			_en_hilo(self._arrancar_turno, siguiente[0], siguiente[1], nombre="codex-turno")
			return
		self._cambiar_estado(Estado.INACTIVA)
		self._avisar_cola(en_cola)

	def _avisar_cola(self, en_cola: int) -> None:
		if en_cola:
			self._emitir(
				TipoEvento.AVISO,
				"Hay %d mensaje%s en cola sin enviar." % (en_cola, "" if en_cola == 1 else "s"),
				en_cola=en_cola,
			)

	def _servidor_caido(self, srv: _ServidorCodex, codigo: int | None, detalle: str) -> None:
		"""El proceso murió: se avisa si había trabajo y se retomará al próximo envío."""
		with self._candado:
			if self._srv is not srv:
				return
			self._srv = None
			activo = self._turno_activo
		if not activo:
			log.info("Codex se cerró con la conversación inactiva; se retomará al enviar")
			return
		mensaje = "Codex se ha cerrado de forma inesperada"
		if codigo is not None:
			mensaje += " (código %s)" % codigo
		mensaje += ". Tu último mensaje no se reenviará automáticamente."
		if detalle:
			mensaje += " Detalle: " + detalle
		self._fallo_turno(mensaje)

	# -- Notificaciones (hilo lector: NUNCA esperar respuestas aquí) ----------

	def _al_notificacion(self, metodo: str, p: dict[str, Any]) -> None:
		if metodo == "item/agentMessage/delta":
			delta = p.get("delta")
			if isinstance(delta, str) and delta:
				self._emitir(TipoEvento.TEXTO_PARCIAL, delta, id=p.get("itemId", ""))
		elif metodo == "item/started":
			self._item_empezado(p.get("item") or {})
		elif metodo == "item/completed":
			self._item_completado(p.get("item") or {}, p.get("turnId"))
		elif metodo == "turn/started":
			self._turno_empezado(p)
		elif metodo == "turn/completed":
			self._turno_completado(p.get("turn") or {})
		elif metodo == "mcpServer/startupStatus/updated":
			self._estado_mcp(p)
		elif metodo == "turn/plan/updated":
			pasos = [
				(str(paso.get("step", "")), _ESTADOS_PLAN.get(paso.get("status", ""), "pendiente"))
				for paso in p.get("plan") or []
				if isinstance(paso, dict)
			]
			self._emitir(TipoEvento.PLAN, str(p.get("explanation") or ""), pasos=pasos)
		elif metodo == "thread/tokenUsage/updated":
			self._uso(p.get("tokenUsage") or {})
		elif metodo == "account/rateLimits/updated":
			self._limites(p.get("rateLimits") or {})
		elif metodo == "thread/compacted":
			self._avisar_compactado(p.get("turnId"))
		elif metodo == "thread/settings/updated":
			ajustes_hilo = p.get("threadSettings")
			if isinstance(ajustes_hilo, dict):
				# Hilo lector: sin preguntar nada a Codex (no puede esperar respuestas).
				self._comprobar_sandbox(ajustes_hilo.get("sandboxPolicy"), _modo(self.ajustes.modo_permiso), None, preguntar=False)
		elif metodo == "error":
			self._error(p)
		elif metodo == "warning":
			mensaje = str(p.get("message") or "").strip()
			if mensaje:
				self._emitir(TipoEvento.AVISO, "Aviso de Codex: " + _recortar_linea(mensaje, 500))
		elif metodo == "serverRequest/resolved":
			solicitud_id = str(p.get("requestId"))
			with self._candado:
				pendiente = self._solicitudes.pop(solicitud_id, None)
				quedan = bool(self._solicitudes)
				activo = self._turno_activo
			if pendiente is not None:
				self._emitir(TipoEvento.PERMISO_RETIRADO, id=solicitud_id)
				if not quedan and activo:
					self._cambiar_estado(Estado.TRABAJANDO)
		elif metodo in (
			"item/commandExecution/outputDelta",
			"item/reasoning/summaryTextDelta",
			"item/reasoning/summaryPartAdded",
			"item/reasoning/textDelta",
			"item/plan/delta",
			"item/fileChange/outputDelta",
			"item/fileChange/patchUpdated",
			"turn/diff/updated",
			"thread/status/changed",
			"thread/started",
		):
			# Deltas que saturarían la interfaz o información que ya llega completa en otro sitio.
			return
		else:
			log.debug("Notificación de Codex no tratada: %s", metodo)

	def _estado_mcp(self, p: dict[str, Any]) -> None:
		"""Arranque de un servidor MCP: solo se avisa si falla (una vez); «iniciando» y «listo» serían ruido."""
		nombre = str(p.get("name") or "")
		estado = p.get("status")
		if not nombre:
			return
		with self._candado:
			if estado == "ready":
				self._mcp_fallidos.discard(nombre)
				return
			if estado != "failed" or nombre in self._mcp_fallidos:
				return
			self._mcp_fallidos.add(nombre)
		if p.get("failureReason") == "reauthenticationRequired":
			texto = ("La herramienta externa «%s» de Codex necesita que vuelvas a iniciar sesión en ella. "
				"Hazlo con «codex mcp login %s» en una consola." % (nombre, nombre))
		else:
			texto = "La herramienta externa «%s» de Codex no se ha podido iniciar." % nombre
			if p.get("error"):
				texto += " Motivo: %s" % _recortar_linea(str(p["error"]), 300)
		log.warning("Servidor MCP de Codex sin arrancar: %s (%s)", nombre, p.get("error") or p.get("failureReason") or "")
		self._emitir(TipoEvento.AVISO, texto, mcp=nombre)

	def _turno_empezado(self, p: dict[str, Any]) -> None:
		turno = p.get("turn") or {}
		id_turno = turno.get("id") if isinstance(turno, dict) else None
		interrumpir = False
		with self._candado:
			if isinstance(id_turno, str) and id_turno not in self._turnos_terminados:
				self._turno_id = id_turno
				if not self._turno_activo:
					# Turno que no empezamos nosotros (otro cliente o una tarea en segundo plano).
					self._turno_activo = True
				if self._interrumpir_pendiente:
					self._interrumpir_pendiente = False
					interrumpir = True
		self._cambiar_estado(Estado.TRABAJANDO)
		if interrumpir and id_turno:
			_en_hilo(self._interrumpir, id_turno, nombre="codex-detener")

	def _turno_completado(self, turno: dict[str, Any]) -> None:
		estado = turno.get("status", "")
		id_turno = turno.get("id")
		siguiente: tuple[str, tuple[Adjunto, ...]] | None = None
		with self._candado:
			if isinstance(id_turno, str):
				self._turnos_terminados.append(id_turno)
			if not self._turno_activo:
				# Ya se dio por terminado (fallo o cierre): no se anuncia dos veces.
				log.debug("turn/completed de un turno que no estaba activo (%s)", id_turno)
				return
			detenido = self._detenido
			self._turno_activo = False
			self._turno_id = None
			self._detenido = False
			self._interrumpir_pendiente = False
			retirar = list(self._solicitudes)
			self._solicitudes.clear()
			self._cambios_archivo.clear()
			error_emitido = self._error_en_turno
			self._error_en_turno = False
			correcto = estado == "completed"
			relanzar = self._relanzar_tras_detener
			self._relanzar_tras_detener = False
			if (relanzar or (correcto and not detenido)) and self._cola and not self._cerrada:
				siguiente = self._cola.pop(0)
				self._turno_activo = True
			en_cola = len(self._cola)
		for solicitud_id in retirar:
			self._emitir(TipoEvento.PERMISO_RETIRADO, id=solicitud_id)
		resumen = ""
		if estado == "failed":
			resumen = _texto_error(turno.get("error"))
			if not error_emitido:
				self._emitir(TipoEvento.ERROR, resumen)
		interrumpido = estado == "interrupted"
		texto = "Turno terminado" if correcto else ("Turno detenido" if interrumpido else "El turno ha fallado")
		self._emitir(TipoEvento.FIN_TURNO, texto, correcto=correcto, interrumpido=interrumpido, resumen=resumen, id=id_turno or "")
		if siguiente is not None:
			self._cambiar_estado(Estado.TRABAJANDO)
			_en_hilo(self._arrancar_turno, siguiente[0], siguiente[1], nombre="codex-turno")
			return
		self._cambiar_estado(Estado.INACTIVA)
		self._avisar_cola(en_cola)

	def _item_empezado(self, item: dict[str, Any]) -> None:
		tipo = item.get("type")
		id_item = item.get("id", "")
		if tipo == "commandExecution":
			orden = str(item.get("command") or "")
			self._emitir(TipoEvento.HERRAMIENTA, "Ejecutar: " + orden, herramienta="Ejecutar", detalle="Ejecutar: " + orden, id=id_item)
		elif tipo == "mcpToolCall":
			nombre = "%s.%s" % (item.get("server", ""), item.get("tool", ""))
			detalle = "Herramienta %s del servidor %s" % (item.get("tool", ""), item.get("server", ""))
			self._emitir(TipoEvento.HERRAMIENTA, detalle, herramienta=nombre, detalle=detalle, id=id_item)
		elif tipo == "dynamicToolCall":
			nombre = str(item.get("tool") or "")
			self._emitir(TipoEvento.HERRAMIENTA, "Herramienta " + nombre, herramienta=nombre, detalle="Herramienta " + nombre, id=id_item)
		elif tipo == "webSearch":
			consulta = str(item.get("query") or "")
			detalle = "Buscar en la web" + (": " + consulta if consulta else "")
			self._emitir(TipoEvento.HERRAMIENTA, detalle, herramienta="Buscar en la web", detalle=detalle, id=id_item)
		elif tipo == "imageView":
			detalle = "Ver imagen: %s" % item.get("path", "")
			self._emitir(TipoEvento.HERRAMIENTA, detalle, herramienta="Ver imagen", detalle=detalle, id=id_item)
		elif tipo == "collabAgentToolCall":
			detalle = "Subagente: %s" % _recortar_linea(str(item.get("prompt") or item.get("tool") or ""), 200)
			self._emitir(TipoEvento.HERRAMIENTA, detalle, herramienta="Subagente", detalle=detalle, id=id_item)
		elif tipo == "fileChange":
			# Se guardan para enseñar el diff si Codex pide permiso para aplicarlos.
			cambios = [cambio for cambio in item.get("changes") or [] if isinstance(cambio, dict)]
			with self._candado:
				self._cambios_archivo[id_item] = cambios

	def _item_completado(self, item: dict[str, Any], id_turno: Any) -> None:
		tipo = item.get("type")
		id_item = item.get("id", "")
		if tipo == "agentMessage":
			self._emitir(TipoEvento.MENSAJE, str(item.get("text") or ""), id=id_item, fase=item.get("phase") or "")
		elif tipo == "commandExecution":
			estado = item.get("status")
			codigo = item.get("exitCode")
			error = estado in ("failed", "declined") or (isinstance(codigo, int) and codigo != 0)
			salida = _recortar(str(item.get("aggregatedOutput") or ""))
			if estado == "declined":
				salida = salida or "Orden denegada."
			self._emitir(TipoEvento.HERRAMIENTA_FIN, salida, herramienta="Ejecutar", salida=salida, error=error, codigo=codigo, id=id_item)
		elif tipo == "mcpToolCall":
			nombre = "%s.%s" % (item.get("server", ""), item.get("tool", ""))
			fallo = item.get("error")
			if isinstance(fallo, dict):
				salida = _recortar(str(fallo.get("message") or "Error"))
			else:
				salida = _recortar(_texto_contenido_mcp(item.get("result")))
			self._emitir(TipoEvento.HERRAMIENTA_FIN, salida, herramienta=nombre, salida=salida, error=fallo is not None, id=id_item)
		elif tipo == "dynamicToolCall":
			fallo = item.get("success") is False
			self._emitir(TipoEvento.HERRAMIENTA_FIN, "", herramienta=str(item.get("tool") or ""), salida="", error=fallo, id=id_item)
		elif tipo == "webSearch":
			self._emitir(TipoEvento.HERRAMIENTA_FIN, "", herramienta="Buscar en la web", salida="", error=False, id=id_item)
		elif tipo in ("imageView", "collabAgentToolCall"):
			nombre = "Ver imagen" if tipo == "imageView" else "Subagente"
			self._emitir(TipoEvento.HERRAMIENTA_FIN, "", herramienta=nombre, salida="", error=False, id=id_item)
		elif tipo == "fileChange":
			with self._candado:
				self._cambios_archivo.pop(id_item, None)
			self._cambios_completados(item)
		elif tipo == "reasoning":
			resumen = [str(parte) for parte in item.get("summary") or [] if str(parte).strip()]
			if resumen:
				self._emitir(TipoEvento.RAZONAMIENTO, "\n\n".join(resumen), id=id_item)
		elif tipo == "contextCompaction":
			self._avisar_compactado(id_turno)
		elif tipo == "imageGeneration":
			ruta = item.get("savedPath") or ""
			self._emitir(TipoEvento.AVISO, "Codex ha generado una imagen" + (": %s" % ruta if ruta else "."))

	def _cambios_completados(self, item: dict[str, Any]) -> None:
		estado = item.get("status")
		if estado == "declined":
			self._emitir(TipoEvento.AVISO, "No se han aplicado los cambios en archivos: se denegaron.")
			return
		if estado == "failed":
			self._emitir(TipoEvento.AVISO, "Codex no ha podido aplicar los cambios en archivos.")
			return
		for cambio in item.get("changes") or []:
			if not isinstance(cambio, dict):
				continue
			ruta = str(cambio.get("path") or "")
			clase = cambio.get("kind") or {}
			tipo = _TIPOS_CAMBIO.get(clase.get("type", "") if isinstance(clase, dict) else str(clase), "modificado")
			destino = clase.get("move_path") if isinstance(clase, dict) else None
			texto = "Archivo %s: %s" % (tipo, ruta) + (" (movido a %s)" % destino if destino else "")
			self._emitir_datos(
				TipoEvento.ARCHIVO,
				texto,
				{"ruta": ruta, "tipo": tipo, "diff": str(cambio.get("diff") or ""), "destino": destino or ""},
			)

	def _emitir_datos(self, tipo: TipoEvento, texto: str, datos: dict[str, Any]) -> None:
		"""Como ``_emitir``, pero admite una clave ``tipo`` en los datos.

		``Conversacion._emitir`` recibe los datos como ``**kwargs`` y su primer
		parámetro se llama ``tipo``, así que no puede llevar ``datos["tipo"]``
		(que el contrato pide para ``ARCHIVO``).
		"""
		try:
			self._al_evento(Evento(tipo, texto, datos))
		except Exception:  # noqa: BLE001 - un receptor roto no debe matar el lector del agente
			log.exception("El receptor de eventos falló con %s", tipo)

	def _avisar_compactado(self, id_turno: Any) -> None:
		with self._candado:
			if id_turno and self._compactado_en == id_turno:
				return
			self._compactado_en = id_turno if isinstance(id_turno, str) else None
		self._emitir(TipoEvento.AVISO, "Se ha compactado el contexto")

	def _uso(self, uso: dict[str, Any]) -> None:
		total = uso.get("total") or {}
		ultimo = uso.get("last") or {}
		# El contexto ocupado es lo que usó la última petición, no la suma de toda la sesión.
		usado = ultimo.get("totalTokens") or total.get("totalTokens") or 0
		self._emitir(
			TipoEvento.USO,
			"",
			entrada=int(total.get("inputTokens") or 0),
			salida=int(total.get("outputTokens") or 0),
			contexto_usado=int(usado),
			contexto_total=int(uso.get("modelContextWindow") or 0),
			coste=None,
		)

	def _limites(self, limites: dict[str, Any]) -> None:
		primaria = limites.get("primary") or {}
		secundaria = limites.get("secondary") or {}
		if not isinstance(primaria, dict) or "usedPercent" not in primaria:
			return
		ventana = _texto_ventana(primaria.get("windowDurationMins"))
		texto = "Has usado el %s %% del límite%s" % (primaria.get("usedPercent"), " " + ventana if ventana else "")
		datos_secundaria = None
		if isinstance(secundaria, dict) and "usedPercent" in secundaria:
			ventana2 = _texto_ventana(secundaria.get("windowDurationMins"))
			texto += " y el %s %% del límite%s" % (secundaria.get("usedPercent"), " " + ventana2 if ventana2 else "")
			datos_secundaria = {
				"porcentaje": secundaria.get("usedPercent"),
				"ventana": ventana2,
				"reinicio": secundaria.get("resetsAt"),
			}
		self._emitir(
			TipoEvento.LIMITES,
			texto + ".",
			porcentaje=primaria.get("usedPercent"),
			ventana=ventana,
			reinicio=primaria.get("resetsAt"),
			secundario=datos_secundaria,
			plan=limites.get("planType") or "",
		)

	def _error(self, p: dict[str, Any]) -> None:
		texto = _texto_error(p.get("error"))
		if p.get("willRetry"):
			self._emitir(TipoEvento.AVISO, "Fallo temporal; Codex lo vuelve a intentar. " + texto)
			return
		with self._candado:
			self._error_en_turno = True
		self._emitir(TipoEvento.ERROR, texto)

	# -- Peticiones del servidor (hilo lector) --------------------------------

	def _al_peticion(self, rpc_id: Any, metodo: str, p: dict[str, Any]) -> None:
		with self._candado:
			srv = self._srv
		if srv is None:
			return
		solicitud: SolicitudPermiso | None
		if metodo == "item/commandExecution/requestApproval":
			permitida = self._regla_que_permite(p)
			if permitida:
				# El usuario lo permitió «durante esta sesión»: se contesta por él, una vez.
				srv.responder(rpc_id, {"decision": "accept"})
				self._emitir(TipoEvento.AVISO, "Permitido «%s», como dijiste para esta sesión." % _recortar_linea(str(p.get("command") or ""), 200),
					permitido_por_regla=permitida)
				return
			solicitud = self._solicitud_orden(rpc_id, p)
		elif metodo == "item/fileChange/requestApproval":
			solicitud = self._solicitud_archivos(rpc_id, p)
		elif metodo == "item/tool/requestUserInput":
			solicitud = _solicitud_pregunta(rpc_id, p)
		elif metodo == "item/permissions/requestApproval":
			solicitud = _solicitud_permisos(rpc_id, p)
		elif metodo == "mcpServer/elicitation/request":
			solicitud = _solicitud_mcp(rpc_id, p)
		elif metodo in ("execCommandApproval", "applyPatchApproval"):
			solicitud = _solicitud_legado(rpc_id, metodo, p)
		elif metodo in PETICIONES_SIN_PERSONA:
			# No son preguntas para la persona: herramientas dinámicas que Ctrl+IA no registra,
			# tokens de una cuenta gestionada por la app que llama y atestaciones del cliente.
			log.info("Petición de Codex que Ctrl+IA no atiende: %s", metodo)
			srv.responder_error(rpc_id, ERROR_NO_ADMITIDO, TEXTO_NO_ADMITIDO)
			return
		else:
			solicitud = None
		if solicitud is None:
			log.warning("Petición de Codex no admitida: %s (%s)", metodo, ", ".join(sorted(p)) if isinstance(p, dict) else "")
			srv.responder_error(rpc_id, ERROR_NO_ADMITIDO, TEXTO_NO_ADMITIDO)
			self._emitir(TipoEvento.AVISO, "Codex ha pedido algo que Ctrl+IA aún no sabe mostrar (%s); se ha rechazado. "
				"Si se repite, crea un paquete de diagnóstico en Herramientas y envíalo al autor." % metodo, metodo=metodo)
			return
		with self._candado:
			self._solicitudes[solicitud.id] = _Pendiente(rpc_id, metodo, p, solicitud)
		self._emitir(TipoEvento.PERMISO, solicitud.titulo, solicitud=solicitud)
		self._cambiar_estado(Estado.ESPERANDO_USUARIO)

	def _solicitud_orden(self, rpc_id: Any, p: dict[str, Any]) -> SolicitudPermiso:
		lineas = []
		if p.get("kind") == "writeStdin":
			lineas.append("Escribir en una orden que ya está en marcha.")
		orden = p.get("command")
		if orden:
			lineas.append("Orden: %s" % orden)
		if p.get("cwd"):
			lineas.append("Carpeta: %s" % p["cwd"])
		if p.get("reason"):
			lineas.append("Motivo: %s" % p["reason"])
		red = p.get("networkApprovalContext")
		if isinstance(red, dict) and red.get("host"):
			lineas.append("Acceso a internet: %s (%s)" % (red.get("host"), red.get("protocol", "")))
		extra = p.get("additionalPermissions")
		if isinstance(extra, dict) and extra:
			lineas.extend(_describir_permisos(extra))
		fuera = self._fuera_del_aislamiento(p)
		if fuera:
			lineas.append(fuera)
		# El «Permitir durante esta sesión» de Codex no se ofrece en las órdenes: Codex lo
		# recuerda él mismo, no se puede retirar y sigue valiendo aunque cambies a un modo
		# más restrictivo (AIChat #298). Ctrl+IA ofrece en su lugar su propia regla.
		opciones = _opciones_decision(p.get("availableDecisions"), sesion=False)
		datos: dict[str, Any] = {"orden": orden or "", "carpeta": p.get("cwd") or "", "item": p.get("itemId", "")}
		prefijo = self._prefijo_permitible(p)
		if prefijo and any(o.id == "accept" for o in opciones):
			# Ctrl+IA guarda la regla y responde «Permitir una vez» por ti: quitarla surte efecto enseguida.
			texto_prefijo = " ".join(prefijo)
			datos["regla"] = regla_de_orden(prefijo)
			opciones = [o for o in opciones if o.id != "acceptForSession"]
			posicion = next(i for i, o in enumerate(opciones) if o.id == "accept") + 1
			opciones.insert(posicion, OpcionPermiso(
				OPCION_PERMITIR_SESION, "Permitir «%s» con cualquier argumento durante esta sesión" % texto_prefijo,
				ClaseOpcion.PERMITIR_SESION, self._ayuda_regla(prefijo),
			))
		return SolicitudPermiso(
			id=str(rpc_id),
			tipo=TipoSolicitud.HERRAMIENTA,
			titulo="Codex quiere ejecutar una orden",
			detalle="\n".join(lineas),
			herramienta="Ejecutar",
			opciones=opciones,
			datos=datos,
		)

	# -- Reglas «permitir durante esta sesión» de Ctrl+IA --------------------

	def _orden_sencilla_en_carpeta(self, p: dict[str, Any]) -> list[str] | None:
		"""Palabras de la orden si es una sola orden sencilla, en la carpeta raíz de la sesión y sin más necesidades.

		Solo vale la carpeta raíz, no una subcarpeta (AIChat #333): en una subcarpeta puede
		haber otro repositorio con su propia configuración. Tampoco valen las órdenes git
		que fuerzan, borran o cambian de repositorio (:func:`orden_git_peligrosa`).
		"""
		if p.get("kind") == "writeStdin" or p.get("additionalPermissions") or p.get("networkApprovalContext"):
			return None
		carpeta = p.get("cwd")
		if not isinstance(carpeta, str) or not carpeta:
			return None
		remota = remoto.carpeta_remota(self.ajustes.extra)
		if not (_misma_carpeta_posix(carpeta, remota) if remota else _misma_carpeta(carpeta, self.ajustes.carpeta)):
			return None
		palabras = palabras_orden(p.get("command"))
		if not palabras or orden_git_peligrosa(palabras):
			return None
		return palabras

	def _repositorio_permitido(self, palabras: Sequence[str], carpeta: str) -> bool:
		"""Una orden git solo se permite por regla en la raíz de un repositorio sin repositorios anidados.

		En otro equipo no se puede revisar la carpeta desde aquí: se pregunta siempre.
		"""
		if not es_orden_git(palabras):
			return True
		if remoto.es_remota(self.ajustes.extra):
			return False
		return repositorio_sin_anidados(Path(carpeta))

	def _prefijo_permitible(self, p: dict[str, Any]) -> list[str] | None:
		"""Prefijo que Codex propone recordar, si se puede ofrecer «Permitir durante esta sesión»."""
		palabras = self._orden_sencilla_en_carpeta(p)
		prefijo = _prefijo_propuesto(p.get("proposedExecpolicyAmendment"))
		if not palabras or not prefijo or not prefijo_permitible(prefijo) or palabras[:len(prefijo)] != prefijo:
			return None
		return prefijo if self._repositorio_permitido(palabras, str(p.get("cwd"))) else None

	def _regla_que_permite(self, p: dict[str, Any]) -> str:
		"""Regla guardada que cubre esta petición de orden, o ``""`` si hay que preguntar."""
		reglas = [r for r in list(self.ajustes.reglas_permitidas) if r.startswith("Orden(")]
		if not reglas:
			return ""
		disponibles = p.get("availableDecisions")
		if isinstance(disponibles, list) and "accept" not in disponibles:
			return ""
		palabras = self._orden_sencilla_en_carpeta(p)
		if not palabras:
			return ""
		for regla in reglas:
			prefijo = prefijo_de_regla(regla)
			if prefijo and prefijo_permitible(prefijo) and palabras[:len(prefijo)] == prefijo:
				# La carpeta se revisa al final y solo si hay regla: recorrerla cuesta.
				return regla if self._repositorio_permitido(palabras, str(p.get("cwd"))) else ""
		return ""

	def _sin_aislamiento(self) -> bool:
		"""Si Codex trabaja ahora sin aislamiento (sandbox): modo «Acceso total» o lo que dijo Codex."""
		with self._candado:
			efectivo = self._sandbox_efectivo
		if efectivo:
			return efectivo == "dangerFullAccess"
		return _modo(self.ajustes.modo_permiso).politica.get("type") == "dangerFullAccess"

	def _fuera_del_aislamiento(self, p: dict[str, Any]) -> str:
		"""Línea del detalle que dice si la orden se ejecutará fuera del aislamiento de Codex.

		Con la aprobación «a petición», Codex solo pregunta por una orden sin más permisos
		(ni red ni carpetas extra) cuando quiere ejecutarla fuera de su aislamiento: si se
		permite, la orden puede cambiar cualquier archivo tuyo y usar internet.
		"""
		if self._sin_aislamiento():
			return "En el modo actual, Codex ejecuta las órdenes sin aislamiento (sandbox), con acceso a todo el equipo."
		if p.get("kind") == "writeStdin" or p.get("additionalPermissions") or p.get("networkApprovalContext"):
			return ""
		return ("Si la permites, esta orden puede ejecutarse fuera del aislamiento (sandbox) de Codex, con acceso a "
			"todos tus archivos y a internet.")

	def _ayuda_regla(self, prefijo: Sequence[str]) -> str:
		"""Ayuda (Ctrl+H) de «Permitir … con cualquier argumento durante esta sesión»."""
		texto_prefijo = " ".join(prefijo)
		partes = [
			"Ctrl+IA lo recordará y contestará «Permitir una vez» por ti cada vez que Codex quiera ejecutar «%s» en "
			"esta sesión, solo en la carpeta raíz de la sesión y nunca con órdenes compuestas." % texto_prefijo,
		]
		if self._sin_aislamiento():
			partes.append("En el modo actual, Codex ejecuta todas las órdenes sin aislamiento (sandbox), con acceso a todo "
				"el equipo.")
		else:
			partes.append("Atención: Codex suele pedir permiso para una orden cuando quiere ejecutarla fuera de su "
				"aislamiento (sandbox); con esta regla, esas órdenes se ejecutarán así, con acceso a todos tus archivos y a "
				"internet, sin volver a preguntarte.")
		if es_orden_git(prefijo):
			partes.append("Con git, siempre se pregunta si la orden fuerza o borra (por ejemplo --force, -f, --delete, "
				"--mirror o una rama con +), si va a otra carpeta o repositorio, o si hay otro repositorio dentro de la "
				"carpeta; un «git push» normal sigue la configuración del repositorio.")
		partes.append("Puedes quitarlo en Sesión, Permisos de la sesión.")
		return " ".join(partes)

	def _solicitud_archivos(self, rpc_id: Any, p: dict[str, Any]) -> SolicitudPermiso:
		lineas = []
		if p.get("reason"):
			lineas.append("Motivo: %s" % p["reason"])
		if p.get("grantRoot"):
			lineas.append("Pide poder escribir en %s durante el resto de la sesión." % p["grantRoot"])
		with self._candado:
			cambios = list(self._cambios_archivo.get(p.get("itemId", ""), []))
		diffs = []
		for cambio in cambios:
			clase = cambio.get("kind") or {}
			tipo = _TIPOS_CAMBIO.get(clase.get("type", "") if isinstance(clase, dict) else "", "modificado")
			lineas.append("Archivo %s: %s" % (tipo, cambio.get("path", "")))
			if cambio.get("diff"):
				diffs.append(str(cambio["diff"]))
		detalle = "\n".join(lineas)
		if diffs:
			detalle += "\n\n" + _recortar("\n".join(diffs), 20000)
		return SolicitudPermiso(
			id=str(rpc_id),
			tipo=TipoSolicitud.HERRAMIENTA,
			titulo="Codex quiere modificar archivos",
			detalle=detalle,
			herramienta="Modificar archivos",
			opciones=_opciones_decision(p.get("availableDecisions")),
			datos={"archivos": [cambio.get("path", "") for cambio in cambios], "item": p.get("itemId", "")},
		)


# --------------------------------------------------------------------------
# Construcción de solicitudes y respuestas (funciones puras)
# --------------------------------------------------------------------------


def _explicar(error: ErrorCodex) -> str:
	if error.tiempo_agotado:
		return "Codex no ha respondido a tiempo."
	if error.muerto:
		return "El programa Codex no está en marcha."
	return "Motivo: " + _recortar_linea(error.mensaje, 300)


def _entrada(texto: str, adjuntos: Sequence[Adjunto], remota: bool = False) -> list[dict[str, Any]]:
	"""Convierte el mensaje en ``UserInput``: texto + imágenes locales.

	Los adjuntos que no son imágenes se citan por ruta para que Codex los lea
	desde el disco (el protocolo no tiene un tipo «archivo»). En otro equipo
	(``remota``), Codex no ve los archivos de Windows: se dice que no se han enviado.
	"""
	if remota and adjuntos:
		nombres = ", ".join(adjunto.ruta.name for adjunto in adjuntos)
		texto = (texto + "\n\n" if texto else "") + f"(Adjuntos que no se han enviado porque están en Windows: {nombres})"
		return [{"type": "text", "text": texto}]
	imagenes = [{"type": "localImage", "path": str(adjunto.ruta)} for adjunto in adjuntos if adjunto.es_imagen]
	otros = [str(adjunto.ruta) for adjunto in adjuntos if not adjunto.es_imagen]
	if otros:
		texto = (texto + "\n\n" if texto else "") + "Archivos adjuntos (léelos del disco):\n" + "\n".join("- " + ruta for ruta in otros)
	return [{"type": "text", "text": texto}, *imagenes]


def palabras_orden(orden: Any) -> list[str] | None:
	"""Palabras de una orden si es UNA orden sencilla; ``None`` si encadena, redirige o expande algo."""
	if isinstance(orden, list):
		if not all(isinstance(parte, str) for parte in orden):
			return None
		orden = " ".join(orden)
	if not isinstance(orden, str) or not orden.strip() or _RE_ORDEN_COMPUESTA.search(orden):
		return None
	try:
		palabras = shlex.split(orden, posix=False)
	except ValueError:
		return None
	return palabras or None


def _prefijo_propuesto(propuesta: Any) -> list[str] | None:
	"""Prefijo de ``proposedExecpolicyAmendment`` (lista de palabras o ``{"command": [...]}``)."""
	if isinstance(propuesta, dict):
		propuesta = propuesta.get("command")
	if not isinstance(propuesta, list) or not propuesta or not all(isinstance(p, str) and p.strip() for p in propuesta):
		return None
	return [p.strip() for p in propuesta]


def _nombre_programa(palabra: str) -> str:
	"""Nombre del programa sin carpeta, comillas ni extensión, en minúsculas (``C:\\Git\\git.exe`` → ``git``)."""
	programa = palabra.replace("\\", "/").rsplit("/", 1)[-1].lower().strip("\"'")
	for extension in (".exe", ".cmd", ".bat", ".ps1", ".com"):
		if programa.endswith(extension):
			programa = programa[: -len(extension)]
	return programa


def es_orden_git(palabras: Sequence[str]) -> bool:
	return bool(palabras) and _nombre_programa(palabras[0]) == "git"


def _parece_ruta_o_direccion(texto: str) -> bool:
	"""Si el destino de ``git push``/``fetch``/``pull`` es una ruta o una dirección y no un remoto con nombre."""
	return texto.startswith((".", "~")) or any(signo in texto for signo in ("/", "\\", ":", "@"))


def orden_git_peligrosa(palabras: Sequence[str]) -> bool:
	"""Si una orden git lleva algo que una regla «con cualquier argumento» no debe cubrir nunca.

	Opciones antes de la suborden (``-c``, ``-C``, ``--git-dir``...), subórdenes que cambian
	la configuración o ejecutan programas, opciones que fuerzan o borran (``--force``,
	``-f``, ``--force-with-lease``, ``--delete``, ``-d``/``-D`` con push, branch y tag,
	``--mirror``), un *refspec* que empieza por ``+`` (o por ``:`` en push) y un destino
	que es una ruta o una dirección (sus *hooks* se ejecutarían). Las demás órdenes, falso.
	"""
	if not es_orden_git(palabras):
		return False
	resto = [palabra.strip("\"'") for palabra in palabras[1:]]
	if not resto or resto[0].startswith("-"):
		return True
	suborden = resto[0].lower()
	if suborden in _GIT_SUBORDENES_PELIGROSAS:
		return True
	posicionales = []
	for palabra in resto[1:]:
		baja = palabra.lower()
		if any(baja == opcion or baja.startswith(opcion + "=") for opcion in _GIT_OPCIONES_PELIGROSAS):
			return True
		if palabra.startswith("-") and not palabra.startswith("--") and len(palabra) > 1:
			letras = palabra[1:]
			if "f" in letras or (suborden in _GIT_BORRAN_CON_D and "d" in letras.lower()) or (suborden == "rebase" and "x" in letras):
				return True
		elif not palabra.startswith("-"):
			posicionales.append(palabra)
	if suborden in _GIT_SUBORDENES_REMOTAS:
		if any(p.startswith("+") or (suborden == "push" and p.startswith(":")) for p in posicionales):
			return True
		if posicionales and _parece_ruta_o_direccion(posicionales[0]):
			return True
	return False


def repositorio_sin_anidados(carpeta: Path, limite: int = LIMITE_EXPLORACION_GIT) -> bool:
	"""Si ``carpeta`` es la raíz de un repositorio git (con su carpeta ``.git``) sin otro ``.git`` dentro.

	Un repositorio anidado tiene su propia configuración (``core.fsmonitor``, *hooks*...)
	que git puede ejecutar desde la raíz (AIChat #333). No se siguen enlaces ni uniones
	de carpetas. Si hay más de ``limite`` entradas o algo no se puede leer, ``False``:
	ante la duda, se pregunta.
	"""
	try:
		propio = carpeta / ".git"
		if not propio.is_dir() or propio.is_symlink() or propio.is_junction():
			return False
	except OSError:
		return False
	pendientes = [carpeta]
	vistas = 0
	while pendientes:
		actual = pendientes.pop()
		try:
			with os.scandir(actual) as entradas:
				for entrada in entradas:
					vistas += 1
					if vistas > limite:
						log.info("Demasiados archivos en %s para buscar repositorios anidados; se pregunta", carpeta)
						return False
					if entrada.name.lower() == ".git":
						if actual is carpeta:
							continue  # el del propio repositorio: no se recorre
						log.info("Hay un repositorio git anidado en %s; la regla no se aplica", actual)
						return False
					if entrada.is_dir(follow_symlinks=False) and not entrada.is_junction():
						pendientes.append(Path(entrada.path))
		except OSError:
			log.debug("No se pudo revisar %s", actual, exc_info=True)
			return False
	return True


def prefijo_permitible(prefijo: Sequence[str]) -> bool:
	"""Si se puede permitir ese prefijo «con cualquier argumento».

	Nunca un intérprete, algo compuesto, ``git`` a secas ni una suborden de git peligrosa.
	"""
	if not prefijo or any(_RE_ORDEN_COMPUESTA.search(palabra) or not palabra.strip() for palabra in prefijo):
		return False
	programa = _nombre_programa(prefijo[0])
	if not programa or programa in INTERPRETES or programa.startswith("python"):
		return False
	return not orden_git_peligrosa(prefijo)


def regla_de_orden(prefijo: Sequence[str]) -> str:
	"""Regla con la que Ctrl+IA recuerda un prefijo permitido: ``Orden(git switch)``."""
	return "Orden(%s)" % " ".join(prefijo)


def prefijo_de_regla(regla: str) -> list[str] | None:
	if not regla.startswith("Orden(") or not regla.endswith(")"):
		return None
	return palabras_orden(regla[len("Orden("):-1])


def _misma_carpeta_posix(ruta: str, carpeta: str) -> bool:
	"""Como :func:`_misma_carpeta`, para rutas del otro equipo (POSIX, distingue mayúsculas)."""
	if carpeta in ("~",) or carpeta.startswith("~/"):
		return False  # sin saber la carpeta personal no se puede comparar: se pregunta
	return posixpath.normpath(ruta) == posixpath.normpath(carpeta)


def _misma_carpeta(ruta: str, carpeta: Path) -> bool:
	"""``ruta`` es exactamente ``carpeta``, no una subcarpeta (sin distinguir mayúsculas en Windows)."""
	try:
		hija = os.path.normcase(os.path.realpath(ruta))
		madre = os.path.normcase(os.path.realpath(str(carpeta)))
	except (OSError, ValueError):
		return False
	return hija == madre


def _opciones_decision(disponibles: Any, *, sesion: bool = True) -> list[OpcionPermiso]:
	"""Opciones de una aprobación de Codex.

	Con ``sesion=False`` se quita «Permitir durante esta sesión» de Codex (si queda otra
	forma de permitir): Codex lo recuerda hasta cerrar la sesión aunque cambies de modo.
	Cuando se ofrece, su ayuda lo dice (AIChat #298).
	"""
	ids = [d for d in disponibles if isinstance(d, str)] if isinstance(disponibles, list) else []
	ids = [d for d in ids if d in _DECISIONES] or list(_DECISIONES_POR_DEFECTO)
	if not sesion and "accept" in ids:
		ids = [d for d in ids if d != "acceptForSession"]
	return [
		OpcionPermiso(d, _DECISIONES[d][0], _DECISIONES[d][1], AYUDA_SESION_CODEX if d == "acceptForSession" else "")
		for d in ids
	]


def _describir_permisos(permisos: dict[str, Any]) -> list[str]:
	"""Texto legible de un perfil de permisos (sistema de archivos y red)."""
	lineas = []
	sistema = permisos.get("fileSystem")
	if isinstance(sistema, dict):
		for ruta in sistema.get("read") or []:
			lineas.append("Leer: %s" % ruta)
		for ruta in sistema.get("write") or []:
			lineas.append("Escribir: %s" % ruta)
		acceso = {"read": "Leer", "write": "Escribir", "deny": "Bloquear"}
		for entrada in sistema.get("entries") or []:
			if not isinstance(entrada, dict):
				continue
			lineas.append("%s: %s" % (acceso.get(entrada.get("access", ""), "Acceso"), _lugar_permiso(entrada.get("path"))))
	red = permisos.get("network")
	if isinstance(red, dict) and red.get("enabled"):
		lineas.append("Usar internet")
	return lineas


_LUGARES_ESPECIALES = {
	"root": "todo el disco",
	"minimal": "lo mínimo del sistema",
	"project_roots": "las carpetas del proyecto",
	"tmpdir": "la carpeta temporal",
	"slash_tmp": "la carpeta temporal del sistema",
}


def _lugar_permiso(ruta: Any) -> str:
	"""Texto de un ``FileSystemPath`` (ruta, patrón o lugar especial)."""
	if not isinstance(ruta, dict):
		return str(ruta or "")
	if ruta.get("path"):
		return str(ruta["path"])
	if ruta.get("pattern"):
		return "archivos que coincidan con %s" % ruta["pattern"]
	valor = ruta.get("value")
	if isinstance(valor, dict):
		clase = str(valor.get("kind") or "")
		texto = _LUGARES_ESPECIALES.get(clase, str(valor.get("path") or clase))
		if valor.get("subpath"):
			texto += " (%s)" % valor["subpath"]
		return texto
	return ""


#: Respuestas en inglés de la pregunta con la que Codex pide permiso para una herramienta MCP
#: (prefijo ``mcp_tool_call_approval`` en el id): etiqueta → (texto en español, clase).
_RESPUESTAS_APROBACION_MCP: dict[str, tuple[str, ClaseOpcion]] = {
	"Allow": ("Permitir una vez", ClaseOpcion.PERMITIR),
	"Run the tool and continue": ("Permitir una vez", ClaseOpcion.PERMITIR),
	"Allow this request and continue": ("Permitir una vez", ClaseOpcion.PERMITIR),
	"Allow for this session": ("Permitir durante esta sesión", ClaseOpcion.PERMITIR_SESION),
	"Run the tool and remember this choice for this session": ("Permitir durante esta sesión", ClaseOpcion.PERMITIR_SESION),
	"Allow this request and remember this choice for this session": ("Permitir durante esta sesión", ClaseOpcion.PERMITIR_SESION),
	"Allow and don't ask me again": ("Permitir siempre", ClaseOpcion.PERMITIR_SIEMPRE),
	"Decline": ("Denegar", ClaseOpcion.DENEGAR),
	"Deny": ("Denegar", ClaseOpcion.DENEGAR),
	"Cancel": ("Cancelar", ClaseOpcion.CANCELAR),
}
#: Prefijo del id de la pregunta de aprobación de herramientas MCP de Codex.
PREFIJO_APROBACION_MCP = "mcp_tool_call_approval"


def _solicitud_aprobacion_pregunta(rpc_id: Any, pregunta: dict[str, Any]) -> SolicitudPermiso | None:
	"""Permiso para una herramienta MCP que Codex hace como pregunta con opciones en inglés."""
	opciones: list[OpcionPermiso] = []
	etiquetas: dict[str, str] = {}
	for numero, bruta in enumerate(pregunta.get("options") or []):
		etiqueta = str(bruta.get("label") or "") if isinstance(bruta, dict) else ""
		if not etiqueta:
			continue
		texto, clase = _RESPUESTAS_APROBACION_MCP.get(etiqueta, (etiqueta, ClaseOpcion.PERMITIR))
		identificador = "opcion%d" % numero
		etiquetas[identificador] = etiqueta
		opciones.append(OpcionPermiso(identificador, texto, clase, str(bruta.get("description") or "")))
	if not opciones:
		return None
	if not any(o.clase in (ClaseOpcion.DENEGAR, ClaseOpcion.CANCELAR) for o in opciones):
		# Sin respuesta en la lista, no contestar equivale a cancelar: siempre hay una opción inofensiva.
		opciones.append(OpcionPermiso("ctrlia_cancelar", "Cancelar", ClaseOpcion.CANCELAR))
	return SolicitudPermiso(
		id=str(rpc_id),
		tipo=TipoSolicitud.HERRAMIENTA,
		titulo=str(pregunta.get("header") or "") or "Codex quiere usar una herramienta",
		detalle=str(pregunta.get("question") or ""),
		herramienta="Herramienta MCP",
		opciones=opciones,
		datos={"aprobacion_mcp": str(pregunta["id"]), "etiquetas": etiquetas},
	)


def _solicitud_pregunta(rpc_id: Any, p: dict[str, Any]) -> SolicitudPermiso | None:
	brutas = [b for b in p.get("questions") or [] if isinstance(b, dict) and b.get("id")]
	if len(brutas) == 1 and str(brutas[0]["id"]).startswith(PREFIJO_APROBACION_MCP):
		aprobacion = _solicitud_aprobacion_pregunta(rpc_id, brutas[0])
		if aprobacion is not None:
			return aprobacion
	preguntas = []
	for bruta in p.get("questions") or []:
		if not isinstance(bruta, dict) or not bruta.get("id"):
			continue
		opciones = [
			(str(opcion.get("label", "")), str(opcion.get("description", "")))
			for opcion in bruta.get("options") or []
			if isinstance(opcion, dict)
		]
		preguntas.append(
			Pregunta(
				id=str(bruta["id"]),
				texto=str(bruta.get("question", "")),
				encabezado=str(bruta.get("header", "")),
				opciones=opciones,
				multiple=False,
				admite_otra=bool(bruta.get("isOther")) or not opciones,
				secreta=bool(bruta.get("isSecret")),
			)
		)
	if not preguntas:
		return None
	return SolicitudPermiso(
		id=str(rpc_id),
		tipo=TipoSolicitud.PREGUNTA,
		titulo="Codex te hace una pregunta" if len(preguntas) == 1 else "Codex te hace %d preguntas" % len(preguntas),
		detalle="\n".join(pregunta.texto for pregunta in preguntas),
		opciones=[
			OpcionPermiso("responder", "Responder", ClaseOpcion.PERMITIR),
			OpcionPermiso("omitir", "No responder", ClaseOpcion.DENEGAR, "Codex seguirá sin tu respuesta."),
		],
		preguntas=preguntas,
	)


def _solicitud_permisos(rpc_id: Any, p: dict[str, Any]) -> SolicitudPermiso:
	permisos = p.get("permissions") if isinstance(p.get("permissions"), dict) else {}
	lineas = _describir_permisos(permisos)
	if p.get("cwd"):
		lineas.append("Carpeta: %s" % p["cwd"])
	if p.get("reason"):
		lineas.append("Motivo: %s" % p["reason"])
	return SolicitudPermiso(
		id=str(rpc_id),
		tipo=TipoSolicitud.HERRAMIENTA,
		titulo="Codex pide permisos adicionales",
		detalle="\n".join(lineas),
		herramienta="Permisos",
		opciones=[
			OpcionPermiso("turn", "Permitir en este turno", ClaseOpcion.PERMITIR),
			OpcionPermiso("session", "Permitir durante esta sesión", ClaseOpcion.PERMITIR_SESION, AYUDA_SESION_CODEX),
			OpcionPermiso("deny", "Denegar", ClaseOpcion.DENEGAR),
		],
	)


def _solicitud_mcp(rpc_id: Any, p: dict[str, Any]) -> SolicitudPermiso:
	"""Elicitación MCP: permiso para una herramienta, formulario, enlace o algo que solo se puede rechazar."""
	return elicitacion.solicitud_elicitacion(
		str(rpc_id),
		str(p.get("serverName") or ""),
		str(p.get("message") or ""),
		p.get("mode") if isinstance(p.get("mode"), str) else None,
		url=str(p.get("url") or ""),
		esquema=p.get("requestedSchema"),
		meta=p.get("_meta") if isinstance(p.get("_meta"), dict) else None,
		agente="Codex",
	)


def _solicitud_legado(rpc_id: Any, metodo: str, p: dict[str, Any]) -> SolicitudPermiso:
	if metodo == "execCommandApproval":
		orden = p.get("command")
		orden_texto = " ".join(orden) if isinstance(orden, list) else str(orden or "")
		titulo = "Codex quiere ejecutar una orden"
		lineas = ["Orden: %s" % orden_texto, "Carpeta: %s" % p.get("cwd", "")]
	else:
		titulo = "Codex quiere modificar archivos"
		cambios = p.get("fileChanges") if isinstance(p.get("fileChanges"), dict) else {}
		lineas = ["Archivo: %s" % ruta for ruta in cambios]
		if p.get("grantRoot"):
			lineas.append("Pide poder escribir en %s durante el resto de la sesión." % p["grantRoot"])
	if p.get("reason"):
		lineas.append("Motivo: %s" % p["reason"])
	return SolicitudPermiso(
		id=str(rpc_id),
		tipo=TipoSolicitud.HERRAMIENTA,
		titulo=titulo,
		detalle="\n".join(lineas),
		herramienta="Ejecutar" if metodo == "execCommandApproval" else "Modificar archivos",
		opciones=_opciones_decision(None, sesion=metodo != "execCommandApproval"),
	)


def _resultado_peticion(pendiente: _Pendiente, respuesta: RespuestaPermiso) -> dict[str, Any]:
	"""Construye el ``result`` que espera Codex para cada tipo de petición."""
	metodo = pendiente.metodo
	opcion = respuesta.opcion
	validas = {o.id for o in pendiente.solicitud.opciones}
	if opcion not in validas:
		segura = pendiente.solicitud.opcion_segura()
		log.warning("Opción desconocida %r para %s; se usa la segura", opcion, metodo)
		opcion = segura.id if segura else ""
	if metodo in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
		return {"decision": opcion or "decline"}
	if metodo in ("execCommandApproval", "applyPatchApproval"):
		decision = _DECISIONES_LEGADO.get(opcion)
		if decision is None:
			decision = {"denied": {"rejection": respuesta.mensaje or "El usuario lo ha denegado."}}
		return {"decision": decision}
	if metodo == "item/permissions/requestApproval":
		if opcion in ("turn", "session"):
			return {"permissions": pendiente.params.get("permissions") or {}, "scope": opcion}
		return {"permissions": {}}
	if metodo == "item/tool/requestUserInput":
		aprobacion = pendiente.solicitud.datos.get("aprobacion_mcp")
		if aprobacion:
			etiqueta = pendiente.solicitud.datos.get("etiquetas", {}).get(opcion, "")
			return {"answers": {aprobacion: {"answers": [etiqueta]}}} if etiqueta else {"answers": {}}
		if opcion != "responder":
			return {"answers": {}}
		respuestas = {}
		for pregunta in pendiente.solicitud.preguntas:
			valores = list(respuesta.respuestas.get(pregunta.id, []))
			if not valores and respuesta.mensaje and len(pendiente.solicitud.preguntas) == 1:
				valores = [respuesta.mensaje]
			respuestas[pregunta.id] = {"answers": valores}
		return {"answers": respuestas}
	if metodo == "mcpServer/elicitation/request":
		return elicitacion.resultado_elicitacion(pendiente.solicitud, respuesta)
	return {}


def _texto_contenido_mcp(resultado: Any) -> str:
	if not isinstance(resultado, dict):
		return ""
	partes = []
	for bloque in resultado.get("content") or []:
		if isinstance(bloque, dict) and bloque.get("type") == "text":
			partes.append(str(bloque.get("text") or ""))
	return "\n".join(partes)


# --------------------------------------------------------------------------
# Historial
# --------------------------------------------------------------------------

_ESTADOS_HILO = {"notLoaded": "guardada", "idle": "inactiva", "active": "activa", "systemError": "error"}


def convertir_hilo(hilo: dict[str, Any]) -> dict[str, Any]:
	"""Resumen de un ``Thread`` de Codex para la lista de historial."""
	titulo = str(hilo.get("name") or "").strip()
	if not titulo:
		vista = str(hilo.get("preview") or "").strip()
		titulo = _recortar_linea(vista.splitlines()[0], 120) if vista else "Conversación sin título"
	origen = hilo.get("source")
	if isinstance(origen, dict):
		origen = next(iter(origen), "")
	estado = hilo.get("status")
	tipo_estado = estado.get("type", "") if isinstance(estado, dict) else str(estado or "")
	return {
		"id": str(hilo.get("id") or ""),
		"titulo": titulo,
		"carpeta": str(hilo.get("cwd") or ""),
		"creado": hilo.get("createdAt"),
		"actualizado": hilo.get("updatedAt"),
		"origen": str(origen or ""),
		"estado": _ESTADOS_HILO.get(tipo_estado, tipo_estado),
	}


def item_a_mensaje(item: dict[str, Any], fecha: float | None = None) -> dict[str, Any] | None:
	"""Convierte un ``ThreadItem`` en un mensaje de historial (o ``None`` si no se muestra)."""
	tipo = item.get("type")
	if tipo == "userMessage":
		partes = []
		for entrada in item.get("content") or []:
			if not isinstance(entrada, dict):
				continue
			clase = entrada.get("type")
			if clase == "text":
				partes.append(str(entrada.get("text") or ""))
			elif clase in ("localImage", "image"):
				partes.append("[Imagen: %s]" % (entrada.get("path") or entrada.get("url") or ""))
			elif clase in ("mention", "skill"):
				partes.append("@%s" % entrada.get("name", ""))
		return {"rol": "usuario", "texto": "\n".join(p for p in partes if p), "fecha": fecha}
	if tipo == "agentMessage":
		return {"rol": "asistente", "texto": str(item.get("text") or ""), "fecha": fecha}
	if tipo == "commandExecution":
		orden = str(item.get("command") or "")
		texto = "Ejecutar: %s" % orden
		salida = str(item.get("aggregatedOutput") or "")
		if salida:
			texto += "\n" + _recortar(salida)
		# El detalle tiene la misma forma que en vivo: las listas de la sesión
		# separan la orden de su resultado y saben si falló.
		codigo = item.get("exitCode")
		fallo = item.get("status") in ("failed", "declined") or (isinstance(codigo, int) and codigo != 0)
		detalle = "Ejecutar: " + orden
		if salida or item.get("status") in ("completed", "failed", "declined"):
			detalle += "\n\n%s:\n%s" % ("Error" if fallo else "Resultado", _recortar(salida))
		return {"rol": "herramienta", "texto": texto, "fecha": fecha, "herramienta": "Ejecutar", "detalle": detalle}
	if tipo == "fileChange":
		lineas = []
		diffs = []
		for cambio in item.get("changes") or []:
			if isinstance(cambio, dict):
				clase = cambio.get("kind") or {}
				nombre = _TIPOS_CAMBIO.get(clase.get("type", "") if isinstance(clase, dict) else "", "modificado")
				linea = "Archivo %s: %s" % (nombre, cambio.get("path", ""))
				lineas.append(linea)
				diffs.append(linea + "\n" + str(cambio.get("diff") or "").strip("\n"))
		if not lineas:
			return {"rol": "herramienta", "texto": "Cambios en archivos", "fecha": fecha}
		# Un mensaje «cambio» por elemento: el detalle repite cada línea del
		# texto como cabecera de su diff (así se separan si son varios).
		return {"rol": "cambio", "texto": "\n".join(lineas), "fecha": fecha, "detalle": "\n\n".join(diffs)}
	if tipo == "collabAgentToolCall":
		instrucciones = str(item.get("prompt") or item.get("tool") or "")
		return {"rol": "herramienta", "texto": "Subagente: %s" % _recortar_linea(instrucciones, 200), "fecha": fecha,
			"herramienta": "Subagente", "detalle": "Subagente: %s" % instrucciones}
	if tipo == "mcpToolCall":
		return {"rol": "herramienta", "texto": "Herramienta %s del servidor %s" % (item.get("tool", ""), item.get("server", "")), "fecha": fecha}
	if tipo == "webSearch":
		return {"rol": "herramienta", "texto": "Buscar en la web: %s" % item.get("query", ""), "fecha": fecha}
	return None


def turnos_a_mensajes(turnos: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Mensajes de una lista de ``Turn`` (con sus ``items``) en orden."""
	mensajes = []
	for turno in turnos:
		if not isinstance(turno, dict):
			continue
		inicio = turno.get("startedAt")
		fin = turno.get("completedAt") or inicio
		for item in turno.get("items") or []:
			if not isinstance(item, dict):
				continue
			mensaje = item_a_mensaje(item, inicio if item.get("type") == "userMessage" else fin)
			if mensaje is not None and mensaje["texto"]:
				mensajes.append(mensaje)
	return mensajes


# --------------------------------------------------------------------------
# Proveedor
# --------------------------------------------------------------------------

#: Identificador de un hilo de Codex (UUID, a menudo versión 7).
_RE_ID_HILO = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def url_abrir_en_codex(id_hilo: str) -> str | None:
	"""Enlace que abre un hilo en la app de escritorio de Codex (``codex://threads/<id>``).

	Comprobado con la app de Codex 26.1016: abre también los hilos que creó
	Ctrl+IA (comparten el estado de ``~/.codex``) y los deja en sus recientes.
	"""
	if not isinstance(id_hilo, str) or not _RE_ID_HILO.match(id_hilo):
		return None
	return "codex://threads/%s" % id_hilo


def _candidatos_windows() -> list[Path]:
	"""Rutas donde suelen instalarse los ``codex.exe`` nativos en Windows."""
	casa = Path.home()
	local = Path(os.environ.get("LOCALAPPDATA") or casa / "AppData" / "Local")  # auditoria: ignorar ruta-no-portable - localiza el programa Codex instalado; no guarda datos
	roaming = Path(os.environ.get("APPDATA") or casa / "AppData" / "Roaming")  # auditoria: ignorar ruta-no-portable - localiza el programa Codex instalado; no guarda datos
	codex_home = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else casa / ".codex"
	candidatos = [
		casa / ".local" / "bin" / "codex.exe",
		local / "Microsoft" / "WinGet" / "Links" / "codex.exe",
		local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe",
		codex_home / "packages" / "standalone" / "current" / "bin" / "codex.exe",
	]
	# La aplicación de escritorio de Codex guarda cada versión en bin\<hash>\: la más reciente primero.
	escritorio = []
	try:
		for ruta in (local / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"):
			try:
				escritorio.append((ruta.stat().st_mtime, ruta))
			except OSError:
				log.debug("No se pudo consultar %s", ruta, exc_info=True)
	except OSError:
		log.debug("No se pudo recorrer la carpeta de la app de Codex", exc_info=True)
	candidatos.extend(ruta for _fecha, ruta in sorted(escritorio, reverse=True))
	npm = roaming / "npm" / "node_modules" / "@openai" / "codex" / "vendor"
	candidatos.append(npm / "x86_64-pc-windows-msvc" / "codex" / "codex.exe")
	candidatos.append(npm / "aarch64-pc-windows-msvc" / "codex" / "codex.exe")
	return candidatos


class ProveedorCodex(Proveedor):
	"""Codex de OpenAI con la suscripción de ChatGPT del usuario.

	:param ejecutable: ruta fija de ``codex`` (si no, se busca).
	:param orden: orden completa del servidor; solo para pruebas (servidor
		falso). Con ella, ``detectar`` usa ``orden + ["--version"]``.
	"""

	id = "codex"
	nombre = "Codex (OpenAI)"
	descripcion = "El agente de programación de OpenAI. Usa tu cuenta de ChatGPT (Plus, Pro, Business...), sin pagar por API."
	web = "https://developers.openai.com/codex"
	capacidades = Capacidades(
		adjuntar_imagenes=True,
		adjuntar_archivos=True,
		bifurcar=True,
		permisos_interactivos=True,
		cambiar_modelo_en_marcha=True,
		comandos_barra=False,
		enviar_ahora=True,
		anadir_al_turno=True,
		leer_historial_externo=True,
	)

	def __init__(self, ejecutable: str | Path | None = None, orden: Sequence[str] | None = None) -> None:
		self._ejecutable = Path(ejecutable) if ejecutable else None
		self._orden = list(orden) if orden else None
		self._candado = threading.Lock()
		self._servidor: _ServidorCodex | None = None
		#: Un servidor por equipo SSH (id del equipo → servidor).
		self._remotos: dict[str, _ServidorCodex] = {}
		self._modelos_vivos: list[OpcionLista] = []
		#: Modelos que admite cada proceso de Codex (id del servidor → (servidor, modelos)), para «Continuar aquí».
		self._modelos_servidor: dict[int, tuple[_ServidorCodex, list[str]]] = {}

	# -- Programa y proceso ---------------------------------------------------

	def ruta_ejecutable(self) -> Path | None:
		if self._ejecutable is not None:
			return self._ejecutable
		return buscar_ejecutable(["codex"], _candidatos_windows() if ES_WINDOWS else ())

	def orden_servidor(self) -> list[str] | None:
		if self._orden:
			return list(self._orden)
		ruta = self.ruta_ejecutable()
		return [str(ruta), "app-server", "--listen", "stdio://"] if ruta else None

	def servidor(self, extra: dict[str, Any] | None = None) -> _ServidorCodex:
		"""El proceso compartido, lanzándolo si no existe o murió. Lanza :class:`ErrorCodex`.

		Si ``extra`` es de una sesión en otro equipo, el de ese equipo (por SSH).
		"""
		try:
			equipo = remoto.equipo_de(extra)
		except remoto.ErrorRemoto as error:
			raise ErrorCodex(str(error), muerto=True) from error
		if equipo is not None:
			return self._servidor_remoto(equipo)
		with self._candado:
			if self._servidor is not None and not self._servidor.muerto:
				return self._servidor
			orden = self.orden_servidor()
			if not orden:
				raise ErrorCodex("No se encuentra Codex en este equipo. Instálalo desde «Agentes instalados».", muerto=True)
			servidor = _ServidorCodex(orden, al_morir=self._servidor_muerto)
			servidor.iniciar()
			self._servidor = servidor
			return servidor

	def _servidor_remoto(self, equipo: remoto.EquipoSSH) -> _ServidorCodex:
		with self._candado:
			existente = self._remotos.get(equipo.id)
			if existente is not None and not existente.muerto:
				return existente
			try:
				orden = remoto.orden_remota(equipo, "codex", ["app-server", "--listen", "stdio://"],
					quitar=(*VARIABLES_PROHIBIDAS, "OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY"))
			except remoto.ErrorRemoto as error:
				raise ErrorCodex(str(error), muerto=True) from error
			servidor = _ServidorCodex(orden, al_morir=self._servidor_muerto, entorno=remoto.entorno_ssh())
			try:
				servidor.iniciar()
			except ErrorCodex as error:
				raise ErrorCodex(
					f"No se pudo arrancar Codex en «{equipo.nombre}»: {error.mensaje}. Comprueba el equipo en Herramientas, "
					"«Equipos por SSH».", muerto=True) from error
			self._remotos[equipo.id] = servidor
			return servidor

	def _servidor_muerto(self, servidor: _ServidorCodex) -> None:
		with self._candado:
			if self._servidor is servidor:
				self._servidor = None
			for clave, otro in list(self._remotos.items()):
				if otro is servidor:
					del self._remotos[clave]

	def cerrar(self) -> None:
		"""Termina el proceso compartido (al salir de Ctrl+IA). Idempotente."""
		with self._candado:
			servidores = [s for s in (self._servidor, *self._remotos.values()) if s is not None]
			self._servidor = None
			self._remotos.clear()
		for servidor in servidores:
			servidor.terminar()

	# -- Contrato ---------------------------------------------------------------

	def detectar(self) -> EstadoProveedor:
		ruta = self.ruta_ejecutable() if not self._orden else Path(self._orden[0])
		if ruta is None:
			return EstadoProveedor(False, mensaje="Codex no está instalado.")
		orden_version = [*self._orden, "--version"] if self._orden else [str(ruta), "--version"]
		version = version_de(orden_version)
		if version.lower().startswith("codex-cli "):
			version = version[len("codex-cli ") :]
		estado = EstadoProveedor(True, ruta=str(ruta), version=version, mensaje="Codex está instalado.")
		try:
			respuesta = self._consultar("account/read", {"refreshToken": False})
		except ErrorCodex as error:
			log.info("No se pudo consultar la cuenta de Codex: %s", error.mensaje)
			return estado
		cuenta = respuesta.get("account") if isinstance(respuesta, dict) else None
		if isinstance(cuenta, dict):
			estado.sesion_iniciada = True
			if cuenta.get("type") == "chatgpt":
				plan = cuenta.get("planType") or ""
				estado.cuenta = "%s%s" % (cuenta.get("email") or "Cuenta de ChatGPT", " (plan %s)" % plan if plan else "")
			elif cuenta.get("type") == "apiKey":
				estado.cuenta = "Clave de API"
				estado.mensaje = "Codex usa una clave de API: cobraría por uso. Inicia sesión con tu cuenta de ChatGPT."
			else:
				estado.cuenta = str(cuenta.get("type") or "")
		else:
			estado.sesion_iniciada = not (isinstance(respuesta, dict) and respuesta.get("requiresOpenaiAuth"))
			if not estado.sesion_iniciada:
				estado.mensaje = "Codex está instalado, pero no has iniciado sesión."
		return estado

	def _consultar(self, metodo: str, params: dict[str, Any]) -> Any:
		"""Petición rápida: con el proceso compartido si ya existe, si no con uno temporal.

		Así detectar Codex no deja un servidor en marcha si el usuario no lo usa.
		"""
		with self._candado:
			servidor = self._servidor if self._servidor is not None and not self._servidor.muerto else None
		if servidor is not None:
			return servidor.peticion(metodo, params, tiempo=TIEMPO_DETECCION)
		orden = self.orden_servidor()
		if not orden:
			raise ErrorCodex("No se encuentra Codex", muerto=True)
		temporal = _ServidorCodex(orden)
		try:
			temporal.iniciar(tiempo=TIEMPO_DETECCION)
			return temporal.peticion(metodo, params, tiempo=TIEMPO_DETECCION)
		finally:
			temporal.terminar()

	def leer_limites(self) -> Any:
		"""El límite de 5 horas y el semanal con ``account/rateLimits/read`` (no gasta cuota).

		Si Codex no contesta, el último dato que guardó en sus sesiones.
		"""
		from app.services import limites
		from app.services.memoria import raiz_codex

		try:
			# Sin el detalle de los créditos de reinicio: es una consulta de fondo (lo pide el propio Codex).
			respuesta = self._consultar("account/rateLimits/read", {"excludeResetCreditDetails": True})
		except ErrorCodex as error:
			log.info("No se pudieron leer los límites de Codex: %s", error.mensaje)
			guardado = limites.ultimo_codex_en_disco(raiz_codex())
			if guardado is not None:
				return guardado
			mensaje = error.mensaje or ""
			if "401" in mensaje or "invalid_request" in mensaje or "auth" in mensaje.lower():
				raise limites.ErrorLimites("Codex no tiene la sesión iniciada con tu cuenta de ChatGPT. Inicia sesión en "
					"Herramientas, Agentes instalados.") from error
			raise limites.ErrorLimites("Codex no ha podido preguntar tus límites: " + mensaje) from error
		resultado = limites.desde_codex(respuesta)
		if resultado is None:
			raise limites.ErrorLimites("Codex no ha informado de límites para tu cuenta.")
		return resultado

	def crear_conversacion(self, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> ConversacionCodex:
		if not ajustes.modo_permiso:
			ajustes.modo_permiso = MODO_PREDETERMINADO
		return ConversacionCodex(self, ajustes, al_evento)

	def modelos(self) -> list[OpcionLista]:
		return [OpcionLista("", "Predeterminado de Codex"), *self._modelos_vivos]

	def listar_modelos(self) -> list[OpcionLista]:
		"""Consulta ``model/list`` en vivo (llamar desde un hilo) y recuerda el resultado."""
		vivos: list[OpcionLista] = []
		cursor = None
		for _pagina in range(MAX_PAGINAS):
			params: dict[str, Any] = {}
			if cursor:
				params["cursor"] = cursor
			respuesta = self.servidor().peticion("model/list", params) or {}
			for modelo in respuesta.get("data") or []:
				if not isinstance(modelo, dict) or modelo.get("hidden"):
					continue
				identificador = str(modelo.get("model") or modelo.get("id") or "")
				if identificador:
					vivos.append(OpcionLista(identificador, str(modelo.get("displayName") or identificador), str(modelo.get("description") or "")))
			cursor = respuesta.get("nextCursor")
			if not cursor:
				break
		self._modelos_vivos = vivos
		return self.modelos()

	def modelos_disponibles(self, srv: _ServidorCodex) -> list[str] | None:
		"""Modelos que ``srv`` admite ahora (``model/list`` con los ocultos), el predeterminado primero.

		Se pregunta una vez por proceso (llamar desde un hilo de fondo). ``None`` si
		Codex no contesta o no da ninguno: entonces no se puede saber y no se cambia nada.
		"""
		with self._candado:
			guardado = self._modelos_servidor.get(id(srv))
		if guardado is not None and guardado[0] is srv:
			return list(guardado[1]) or None
		ids: list[str] = []
		cursor = None
		try:
			for _pagina in range(MAX_PAGINAS):
				params: dict[str, Any] = {"includeHidden": True}
				if cursor:
					params["cursor"] = cursor
				respuesta = srv.peticion("model/list", params) or {}
				for modelo in respuesta.get("data") or []:
					if not isinstance(modelo, dict):
						continue
					identificador = str(modelo.get("model") or modelo.get("id") or "")
					if identificador and identificador not in ids:
						if modelo.get("isDefault"):
							ids.insert(0, identificador)
						else:
							ids.append(identificador)
				cursor = respuesta.get("nextCursor")
				if not cursor:
					break
		except ErrorCodex as error:
			log.info("No se pudo consultar la lista de modelos de Codex: %s", error.mensaje)
			return None
		with self._candado:
			self._modelos_servidor = {clave: valor for clave, valor in self._modelos_servidor.items() if not valor[0].muerto}
			self._modelos_servidor[id(srv)] = (srv, ids)
		return list(ids) or None

	def esfuerzos(self) -> list[OpcionLista]:
		return list(_ESFUERZOS)

	def modo_de_serie(self) -> str:
		"""Se propone trabajar en la carpeta del proyecto (con permiso para lo demás), no solo leer."""
		return MODO_PREDETERMINADO

	def modo_predeterminado(self, carpeta: Path | None = None) -> str:
		"""El que diga el config.toml de Codex (sandbox y aprobación); si no dice nada, el de serie."""
		from app.services.config_agentes import modo_inicial

		return modo_inicial.elegir(modo_inicial.modo_codex(carpeta), self.modos_permiso(), self.modo_de_serie())

	def modos_permiso(self) -> list[OpcionLista]:
		return [OpcionLista(modo.id, modo.etiqueta, modo.ayuda) for modo in _MODOS]

	def instrucciones_instalacion(self) -> str:
		return (
			"Para usar Codex necesitas una cuenta de ChatGPT (Plus, Pro, Business, Edu o Enterprise).\n"
			"1. Instala Codex: descarga la aplicación Codex para Windows desde la web de OpenAI, "
			"o instala la versión de terminal con la orden «npm install -g @openai/codex» (requiere Node.js).\n"
			"2. Inicia sesión: en Ctrl+IA, pulsa «Iniciar sesión» o escribe «codex login» en una consola y "
			"elige «Sign in with ChatGPT».\n"
			"Ctrl+IA nunca usa claves de API: Codex trabajará con tu suscripción."
		)

	def orden_instalacion(self) -> list[str] | None:
		if shutil.which("npm") is None:
			return None
		# Texto fijo sin datos del usuario: no hay riesgo de inyección en cmd.exe.
		return ["cmd.exe", "/k", "npm install -g @openai/codex"] if ES_WINDOWS else ["npm", "install", "-g", "@openai/codex"]

	def orden_inicio_sesion(self) -> list[str] | None:
		ruta = self.ruta_ejecutable()
		return [str(ruta), "login"] if ruta else None

	# -- Historial --------------------------------------------------------------

	def listar_hilos(self, archivados: bool = False, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
		"""Conversaciones guardadas por Codex, de la más reciente a la más antigua.

		Con ``extra`` de una sesión en otro equipo, las de ese equipo (por SSH).
		Lanza :class:`ErrorCodex` si Codex no responde. Llamar desde un hilo.
		"""
		hilos: list[dict[str, Any]] = []
		cursor = None
		servidor = self.servidor(extra)
		for _pagina in range(MAX_PAGINAS):
			params: dict[str, Any] = {"limit": 100, "sortKey": "updated_at", "archived": archivados}
			if cursor:
				params["cursor"] = cursor
			respuesta = servidor.peticion("thread/list", params) or {}
			for hilo in respuesta.get("data") or []:
				if isinstance(hilo, dict) and hilo.get("id"):
					hilos.append(convertir_hilo(hilo))
			cursor = respuesta.get("nextCursor")
			if not cursor:
				break
		return hilos

	def leer_hilo(self, id_hilo: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
		"""Mensajes de una conversación guardada: ``{rol, texto, fecha}``.

		Usa ``thread/read`` con los turnos; si Codex solo devuelve resúmenes
		(historial paginado), completa con ``thread/items/list``. Con ``extra``
		de una sesión en otro equipo, la lee en ese equipo (por SSH).
		"""
		servidor = self.servidor(extra)
		respuesta = servidor.peticion("thread/read", {"threadId": id_hilo, "includeTurns": True}) or {}
		hilo = respuesta.get("thread") or {}
		turnos = [t for t in hilo.get("turns") or [] if isinstance(t, dict)]
		incompleto = any(t.get("itemsView") in ("notLoaded", "summary") for t in turnos)
		if turnos and not incompleto:
			return turnos_a_mensajes(turnos)
		try:
			mensajes = self._leer_items(servidor, id_hilo, turnos)
		except ErrorCodex:
			log.debug("thread/items/list no disponible; se usa thread/read", exc_info=True)
			return turnos_a_mensajes(turnos)
		return mensajes or turnos_a_mensajes(turnos)

	def _leer_items(self, servidor: _ServidorCodex, id_hilo: str, turnos: list[dict[str, Any]]) -> list[dict[str, Any]]:
		inicio_turno = {t.get("id"): t.get("startedAt") for t in turnos}
		mensajes = []
		cursor = None
		for _pagina in range(MAX_PAGINAS):
			params: dict[str, Any] = {"threadId": id_hilo, "limit": 200}
			if cursor:
				params["cursor"] = cursor
			respuesta = servidor.peticion("thread/items/list", params) or {}
			for entrada in respuesta.get("data") or []:
				if not isinstance(entrada, dict) or not isinstance(entrada.get("item"), dict):
					continue
				milisegundos = entrada.get("completedAtMs") or entrada.get("startedAtMs")
				fecha = milisegundos / 1000 if isinstance(milisegundos, (int, float)) else inicio_turno.get(entrada.get("turnId"))
				mensaje = item_a_mensaje(entrada["item"], fecha)
				if mensaje is not None and mensaje["texto"]:
					mensajes.append(mensaje)
			cursor = respuesta.get("nextCursor")
			if not cursor:
				break
		return mensajes
