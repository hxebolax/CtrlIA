"""Herramientas propias de Ctrl+IA para los agentes (servidor MCP «ctrlia»).

Ctrl+IA da a cada sesión tres herramientas que solo afectan a ESA sesión:

- ``avisar``: un aviso breve para la persona usuaria (voz y braille; con
  sonido y notificación de Windows si la ventana no está activa). Como mucho
  :data:`MAX_AVISO` caracteres y uno cada :data:`INTERVALO_AVISOS` segundos.
- ``dar_por_terminada``: da la sesión por terminada, como Control+D.
- ``renombrar_sesion``: le pone un título nuevo (como mucho :data:`MAX_TITULO`).

Cómo llegan a cada agente
=========================

- Claude Code: servidor MCP «en proceso» (``sdkMcpServers`` en ``initialize``);
  las peticiones llegan como ``control_request`` ``mcp_message`` por su misma
  entrada y salida (también por SSH). Lo atiende :meth:`SesionHerramientas.atender`.
- Codex (``thread/start``, ``thread/resume`` y ``thread/fork`` con ``config``)
  y los agentes ACP que admiten HTTP: el servidor HTTP local de este módulo
  (:func:`url_servidor`), con un token por sesión en ``Authorization: Bearer``.
- Los agentes ACP sin HTTP y Antigravity (``agy``): el puente stdio
  ``app/puente_mcp.py`` (``ctrlia-mcp.exe`` en la versión compilada), que
  reenvía cada mensaje al servidor HTTP.

A ``agy`` se le da un complemento ``ctrlia`` (``.agents/plugins/ctrlia`` con su
``mcp_config.json``) en una carpeta que se añade con ``--add-dir``: así conserva
TODAS sus herramientas. NUNCA con un agente propio (``--agent``): un agente
propio de ``agy`` 1.4.4 arranca solo con herramientas de lectura (sin escribir,
editar ni ejecutar órdenes), comprobado. ``agy`` llama al servidor
``ctrlia_ctrlia`` (complemento y servidor) y su regla es ``mcp(ctrlia_ctrlia/*)``.
El complemento apunta al archivo fijo :func:`ruta_conexion` (dirección y un token
de enrutado que se renuevan en cada arranque) y la sesión se reconoce por el
``_meta`` ``antigravity.google/conversation_id`` de cada ``tools/call``.

Nada de este módulo importa wx: cada uso llega a la conversación por
``al_usar(accion, datos)``, que lo convierte en un ``AVISO`` (queda como mensaje
de sistema) y la interfaz hace el resto con ``wx.CallAfter``.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Nombre del servidor MCP (las herramientas de Claude son ``mcp__ctrlia__avisar``...).
NOMBRE_SERVIDOR = "ctrlia"
#: Clave de configuración de la opción de Preferencias, Agentes.
CLAVE_OPCION = "agentes.herramientas_ctrlia"
#: Ejecutable del puente en la versión compilada (junto a ``CtrlIA.exe``).
EJECUTABLE_PUENTE = "ctrlia-mcp.exe"
#: Variables de entorno del puente.
VARIABLE_URL = "CTRLIA_URL"
VARIABLE_TOKEN = "CTRLIA_TOKEN"
VARIABLE_CONEXION = "CTRLIA_CONEXION"

MAX_AVISO = 300
MAX_TITULO = 80
MAX_MOTIVO = 300
#: Segundos mínimos entre dos avisos (y entre dos cambios de título) de la misma sesión.
INTERVALO_AVISOS = 10.0
INTERVALO_TITULOS = 10.0
#: Tamaño máximo de una petición HTTP (un mensaje JSON-RPC).
MAX_CUERPO = 64 * 1024
#: Segundos que el servidor HTTP espera a que el cliente termine de enviar.
TIEMPO_SOCKET = 30.0

VERSIONES_PROTOCOLO = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
VERSION_PREDETERMINADA = "2025-06-18"

#: Claves del ``_meta`` de ``tools/call`` con las que Antigravity dice su conversación.
META_CONVERSACION_AGY = ("antigravity.google/root_conversation_id", "antigravity.google/conversation_id")

INSTRUCCIONES = (
	"Herramientas de Ctrl+IA, el programa accesible desde el que la persona usuaria conversa contigo. Usa un lector "
	"de pantalla: avísale solo de lo importante. Estas herramientas solo afectan a esta sesión."
)

_ANOTACIONES = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
#: Claude Code difiere las herramientas MCP (las busca bajo demanda) salvo con esta marca.
_META_CLAUDE = {"anthropic/alwaysLoad": True}

HERRAMIENTAS: list[dict[str, Any]] = [
	{
		"name": "avisar",
		"title": "Avisar a la persona usuaria",
		"description": (
			"Avisa a la persona usuaria con un mensaje breve: Ctrl+IA se lo lee con su lector de pantalla (voz y "
			"braille) y, si no está mirando la ventana de Ctrl+IA, suena y le sale una notificación de Windows. Úsala "
			"solo para lo que deba saber enseguida: que has terminado una tarea larga, que necesitas que decida algo o "
			"un problema grave. No la uses para contar cada paso ni para repetir tu respuesta. Como mucho %d caracteres "
			"y un aviso cada %d segundos." % (MAX_AVISO, INTERVALO_AVISOS)
		),
		"inputSchema": {
			"type": "object",
			"properties": {
				"texto": {"type": "string", "maxLength": MAX_AVISO,
					"description": "El aviso, en el idioma de la persona y en una o dos frases."},
				"importante": {"type": "boolean", "default": False,
					"description": "true si es urgente: se avisa aunque la persona tenga los anuncios de las respuestas en silencio."},
			},
			"required": ["texto"],
			"additionalProperties": False,
		},
		"annotations": _ANOTACIONES,
		"_meta": _META_CLAUDE,
	},
	{
		"name": "dar_por_terminada",
		"title": "Dar la sesión por terminada",
		"description": (
			"Da por terminada esta sesión en Ctrl+IA, igual que cuando la persona pulsa Control+D: deja de salir entre "
			"las sesiones que la necesitan o que tienen respuesta nueva, y la persona ve que lo has hecho tú. Úsala solo "
			"cuando hayas acabado del todo lo que te pidió y no quede nada pendiente por su parte. No cierra la "
			"conversación: si vuelve a escribirte, la sesión se activa sola."
		),
		"inputSchema": {
			"type": "object",
			"properties": {
				"motivo": {"type": "string", "maxLength": MAX_MOTIVO,
					"description": "Opcional: en una frase, qué has terminado."},
			},
			"additionalProperties": False,
		},
		"annotations": _ANOTACIONES,
		"_meta": _META_CLAUDE,
	},
	{
		"name": "renombrar_sesion",
		"title": "Renombrar la sesión",
		"description": (
			"Cambia el título de esta sesión en la lista de Ctrl+IA por uno breve y claro que diga de qué trata el "
			"trabajo (como mucho %d caracteres). Úsala cuando el título actual no lo describa bien; no lo cambies a "
			"menudo." % MAX_TITULO
		),
		"inputSchema": {
			"type": "object",
			"properties": {
				"titulo": {"type": "string", "maxLength": MAX_TITULO, "description": "El título nuevo."},
			},
			"required": ["titulo"],
			"additionalProperties": False,
		},
		"annotations": _ANOTACIONES,
		"_meta": _META_CLAUDE,
	},
]

NOMBRES_HERRAMIENTAS = tuple(h["name"] for h in HERRAMIENTAS)

#: Lo que se dice de cada herramienta en «Herramientas del agente» (Ctrl+Alt+H).
DESCRIPCION_INVENTARIO = "herramientas de Ctrl+IA: avisarte, dar la sesión por terminada y renombrarla"

ReceptorUso = Callable[[str, dict[str, Any]], None]


class ErrorHerramienta(Exception):
	"""Uso no válido de una herramienta: el texto vuelve al modelo como error de la herramienta."""


# ---------------------------------------------------------------------------
# Preferencia
# ---------------------------------------------------------------------------


def activadas() -> bool:
	"""Preferencias, Agentes: «Dar a los agentes las herramientas de Ctrl+IA».

	Activada de fábrica en el programa. Sin configuración cargada (pruebas,
	herramientas de línea de órdenes) no se declara nada.
	"""
	from app.core import configuracion

	instancia = getattr(configuracion, "_instancia", None)
	if instancia is None:
		return False
	try:
		return bool(instancia.obtener(CLAVE_OPCION, True))
	except Exception:  # noqa: BLE001 - una clave ilegible deja las herramientas como de fábrica
		log.debug("No se pudo leer %s", CLAVE_OPCION, exc_info=True)
		return True


# ---------------------------------------------------------------------------
# JSON-RPC (MCP) común
# ---------------------------------------------------------------------------


def _error(ident: Any, codigo: int, mensaje: str) -> dict[str, Any]:
	return {"jsonrpc": "2.0", "id": ident, "error": {"code": codigo, "message": mensaje}}


def _resultado(ident: Any, resultado: dict[str, Any]) -> dict[str, Any]:
	return {"jsonrpc": "2.0", "id": ident, "result": resultado}


def resultado_herramienta(texto: str, error: bool = False) -> dict[str, Any]:
	"""Resultado de ``tools/call`` con un texto."""
	return {"content": [{"type": "text", "text": texto}], "isError": error}


def atender_mcp(mensaje: Any, llamar: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]] | None,
		con_herramientas: bool = True) -> dict[str, Any] | None:
	"""Respuesta JSON-RPC a un mensaje MCP; ``None`` para las notificaciones y respuestas.

	:param llamar: ``llamar(nombre, argumentos, meta)`` atiende ``tools/call``.
	:param con_herramientas: ``False``: ``tools/list`` vacío (opción desactivada).
	"""
	if not isinstance(mensaje, dict):
		return _error(None, -32600, "Petición no válida")
	metodo = mensaje.get("method")
	if "id" not in mensaje or mensaje.get("id") is None:
		return None  # notificación (notifications/initialized...) o respuesta: nada que contestar
	ident = mensaje["id"]
	if not isinstance(metodo, str):
		return None if ("result" in mensaje or "error" in mensaje) else _error(ident, -32600, "Petición no válida")
	params = mensaje.get("params") if isinstance(mensaje.get("params"), dict) else {}
	if metodo == "initialize":
		pedida = params.get("protocolVersion")
		version = pedida if pedida in VERSIONES_PROTOCOLO else VERSION_PREDETERMINADA
		from app.core.version import APP_VERSION

		return _resultado(ident, {
			"protocolVersion": version,
			"capabilities": {"tools": {"listChanged": False}},
			"serverInfo": {"name": NOMBRE_SERVIDOR, "title": "Ctrl+IA", "version": APP_VERSION},
			"instructions": INSTRUCCIONES,
		})
	if metodo == "ping":
		return _resultado(ident, {})
	if metodo == "tools/list":
		return _resultado(ident, {"tools": HERRAMIENTAS if con_herramientas else []})
	if metodo == "tools/call":
		nombre = params.get("name")
		argumentos = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
		meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
		if not isinstance(nombre, str) or nombre not in NOMBRES_HERRAMIENTAS or llamar is None:
			return _error(ident, -32602, "Herramienta desconocida: %s" % nombre)
		try:
			return _resultado(ident, llamar(nombre, argumentos, meta))
		except ErrorHerramienta as error:
			return _resultado(ident, resultado_herramienta(str(error), error=True))
		except Exception:  # noqa: BLE001 - un fallo de Ctrl+IA vuelve al modelo como error de la herramienta
			log.exception("Fallo al atender la herramienta %s", nombre)
			return _resultado(ident, resultado_herramienta("Ctrl+IA no ha podido hacerlo por un error interno.", error=True))
	return _error(ident, -32601, "Método no admitido: %s" % metodo)


def _texto_limpio(valor: Any, una_linea: bool) -> str:
	"""Texto sin caracteres de control; en una línea si ``una_linea``."""
	if not isinstance(valor, str):
		return ""
	if una_linea:
		return " ".join(valor.split())
	lineas = (" ".join(linea.split()) for linea in valor.splitlines())
	return "\n".join(linea for linea in lineas if linea).strip()


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------


class SesionHerramientas:
	"""Las herramientas de UNA sesión: su token y lo que pasa cuando el agente las usa.

	:param al_usar: ``al_usar(accion, datos)``; la conversación lo convierte en un
		aviso. Si lanza, el modelo recibe un error de la herramienta.
	"""

	def __init__(self, al_usar: ReceptorUso, agente: str = "", reloj: Callable[[], float] = time.monotonic) -> None:
		self.token = secrets.token_urlsafe(32)
		self.agente = agente
		#: Conversación de Antigravity (``conversation_id``) con la que se reconoce la sesión en el enrutado.
		self.conversacion_externa = ""
		self._al_usar = al_usar
		self._reloj = reloj
		self._candado = threading.Lock()
		self._ultimo: dict[str, float] = {}
		self.retirada = False

	def atender(self, mensaje: Any) -> dict[str, Any] | None:
		"""Respuesta JSON-RPC a un mensaje MCP de esta sesión (``None`` si no hay que contestar)."""
		return atender_mcp(mensaje, self.llamar)

	def llamar(self, nombre: str, argumentos: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
		"""Ejecuta una herramienta. Lanza :class:`ErrorHerramienta` si no se puede."""
		if self.retirada:
			raise ErrorHerramienta("Esta sesión ya no está abierta en Ctrl+IA.")
		if nombre == "avisar":
			return self._avisar(argumentos)
		if nombre == "dar_por_terminada":
			motivo = _texto_limpio(argumentos.get("motivo"), una_linea=True)
			if len(motivo) > MAX_MOTIVO:
				raise ErrorHerramienta("El motivo pasa de %d caracteres: resúmelo." % MAX_MOTIVO)
			self._usar("dar_por_terminada", {"motivo": motivo})
			return resultado_herramienta("Hecho: Ctrl+IA da esta sesión por terminada en cuanto acabes este turno.")
		if nombre == "renombrar_sesion":
			titulo = _texto_limpio(argumentos.get("titulo"), una_linea=True)
			if not titulo:
				raise ErrorHerramienta("Falta el título.")
			if len(titulo) > MAX_TITULO:
				raise ErrorHerramienta("El título pasa de %d caracteres: acórtalo." % MAX_TITULO)
			self._limitar("renombrar_sesion", INTERVALO_TITULOS, "cambiar el título")
			self._usar("renombrar_sesion", {"titulo": titulo})
			return resultado_herramienta("Hecho: la sesión se llama ahora «%s»." % titulo)
		raise ErrorHerramienta("Herramienta desconocida: %s" % nombre)

	def _avisar(self, argumentos: dict[str, Any]) -> dict[str, Any]:
		texto = _texto_limpio(argumentos.get("texto"), una_linea=False)
		if not texto:
			raise ErrorHerramienta("Falta el texto del aviso.")
		if len(texto) > MAX_AVISO:
			raise ErrorHerramienta("El aviso pasa de %d caracteres: resúmelo en una o dos frases." % MAX_AVISO)
		importante = argumentos.get("importante") is True
		self._limitar("avisar", INTERVALO_AVISOS, "avisar otra vez")
		self._usar("avisar", {"texto": texto, "importante": importante})
		return resultado_herramienta("Hecho: Ctrl+IA ha avisado a la persona.")

	def _limitar(self, accion: str, intervalo: float, que: str) -> None:
		with self._candado:
			ahora = self._reloj()
			anterior = self._ultimo.get(accion)
			if anterior is not None and ahora - anterior < intervalo:
				espera = max(1, round(intervalo - (ahora - anterior)))
				raise ErrorHerramienta("Demasiado seguido: espera %d segundos antes de %s." % (espera, que))
			self._ultimo[accion] = ahora

	def _usar(self, accion: str, datos: dict[str, Any]) -> None:
		log.info("El agente usa la herramienta de Ctrl+IA «%s»", accion)
		self._al_usar(accion, datos)


_candado = threading.Lock()
_sesiones: list[SesionHerramientas] = []


def registrar(al_usar: ReceptorUso, agente: str = "") -> SesionHerramientas:
	"""Da de alta las herramientas de una sesión (con su token) y las devuelve."""
	sesion = SesionHerramientas(al_usar, agente)
	with _candado:
		_sesiones.append(sesion)
	return sesion


def retirar(sesion: SesionHerramientas | None) -> None:
	"""Da de baja una sesión (al cerrar su conversación): su token deja de valer. Idempotente."""
	if sesion is None:
		return
	sesion.retirada = True
	with _candado:
		if sesion in _sesiones:
			_sesiones.remove(sesion)


def sesion_de_token(token: str) -> SesionHerramientas | None:
	"""La sesión de ese token, o ``None`` (comparación en tiempo constante)."""
	if not token:
		return None
	with _candado:
		candidatas = list(_sesiones)
	encontrada = None
	for sesion in candidatas:
		if hmac.compare_digest(sesion.token.encode(), token.encode()):
			encontrada = sesion
	return encontrada


def _sesion_de_conversacion(conversacion: str) -> SesionHerramientas | None:
	if not conversacion:
		return None
	with _candado:
		return next((s for s in _sesiones if s.conversacion_externa == conversacion), None)


# ---------------------------------------------------------------------------
# Enrutado de Antigravity (un token por arranque de Ctrl+IA)
# ---------------------------------------------------------------------------

_token_enrutado = secrets.token_urlsafe(32)


def _atender_enrutado(mensaje: Any) -> dict[str, Any] | None:
	"""Mensaje del puente de ``agy``: la sesión sale del ``_meta`` de cada ``tools/call``."""

	def llamar(nombre: str, argumentos: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
		sesion = None
		for clave in META_CONVERSACION_AGY:
			valor = meta.get(clave)
			sesion = _sesion_de_conversacion(valor) if isinstance(valor, str) else None
			if sesion is not None:
				break
		if sesion is None:
			raise ErrorHerramienta("Esta conversación no está abierta en Ctrl+IA o tiene sus herramientas desactivadas.")
		return sesion.llamar(nombre, argumentos, meta)

	return atender_mcp(mensaje, llamar, con_herramientas=activadas())


# ---------------------------------------------------------------------------
# Servidor HTTP local (MCP «Streamable HTTP» sin SSE)
# ---------------------------------------------------------------------------


class _Manejador(BaseHTTPRequestHandler):
	protocol_version = "HTTP/1.1"
	timeout = TIEMPO_SOCKET
	server_version = "CtrlIA-MCP"
	sys_version = ""

	def log_message(self, formato: str, *args: Any) -> None:
		"""Sin ruido en la consola: al registro, en depuración."""
		log.debug("MCP HTTP: %s", formato % args)

	def _vacia(self, codigo: int, cabeceras: dict[str, str] | None = None, cerrar: bool = False) -> None:
		"""Respuesta sin cuerpo (``Content-Length: 0``: Codex no entiende un 401 sin él).

		La conexión se deja abierta salvo con ``cerrar``: con Python 3.15 en Windows, cerrarla
		justo después de responder hace que la mitad de las veces el cliente reciba un corte
		(RST) en lugar de la respuesta. Por eso el cuerpo de la petición se lee siempre antes.
		"""
		self.send_response(codigo)
		for nombre, valor in (cabeceras or {}).items():
			self.send_header(nombre, valor)
		self.send_header("Content-Length", "0")
		if cerrar:
			self.send_header("Connection", "close")
		self.end_headers()

	def _json(self, codigo: int, datos: Any) -> None:
		cuerpo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
		self.send_response(codigo)
		self.send_header("Content-Type", "application/json; charset=utf-8")
		self.send_header("Content-Length", str(len(cuerpo)))
		self.send_header("Cache-Control", "no-store")
		self.end_headers()
		self.wfile.write(cuerpo)

	def _origen_valido(self) -> bool:
		"""Solo clientes locales que no son un navegador (sin ``Origin``) y con un ``Host`` de este equipo."""
		if self.headers.get("Origin"):
			return False
		anfitrion = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
		return anfitrion in ("127.0.0.1", "localhost", "")

	def _descartar(self, longitud: int) -> bool:
		"""Lee y tira un cuerpo demasiado grande (hasta 1 MB). ``True`` si se ha leído entero."""
		if longitud > 1024 * 1024:
			return False
		restante = longitud
		while restante > 0:
			trozo = self.rfile.read(min(restante, 65536))
			if not trozo:
				return False
			restante -= len(trozo)
		return True

	def do_GET(self) -> None:  # noqa: N802 - nombre de http.server
		self._vacia(405, {"Allow": "POST"})

	def do_DELETE(self) -> None:  # noqa: N802
		self._vacia(405, {"Allow": "POST"})

	def do_POST(self) -> None:  # noqa: N802
		# El cuerpo se lee siempre antes de contestar: en Windows, cerrar con datos sin leer corta la
		# conexión (RST) y el cliente no llegaría a ver la respuesta (un 401, por ejemplo).
		try:
			longitud = int(self.headers.get("Content-Length") or "-1")
		except ValueError:
			longitud = -1
		if longitud < 0:
			self._vacia(411, cerrar=True)  # sin longitud no se sabe dónde acaba el cuerpo
			return
		if longitud > MAX_CUERPO:
			self._vacia(413, cerrar=not self._descartar(longitud))
			return
		datos = self.rfile.read(longitud)
		if self.path.split("?", 1)[0].rstrip("/") != "/mcp":
			self._vacia(404)
			return
		if not self._origen_valido():
			self._vacia(403)
			return
		autorizacion = self.headers.get("Authorization") or ""
		token = autorizacion[7:].strip() if autorizacion[:7].lower() == "bearer " else ""
		sesion = sesion_de_token(token)
		enrutado = sesion is None and bool(token) and hmac.compare_digest(token.encode(), _token_enrutado.encode())
		if sesion is None and not enrutado:
			self._vacia(401, {"WWW-Authenticate": "Bearer"})
			return
		try:
			mensaje = json.loads(datos.decode("utf-8"))
		except (UnicodeDecodeError, ValueError, RecursionError):
			self._json(400, _error(None, -32700, "JSON no válido"))
			return
		respuesta = _atender_enrutado(mensaje) if enrutado else sesion.atender(mensaje)  # type: ignore[union-attr]
		if respuesta is None:
			self._vacia(202)
		else:
			self._json(200, respuesta)


class _Servidor(ThreadingHTTPServer):
	daemon_threads = True
	allow_reuse_address = False

	def handle_error(self, request: Any, client_address: Any) -> None:
		"""Una conexión rota no se escribe en la consola: al registro, en depuración."""
		error = sys.exception()
		log.debug("Conexión con el servidor MCP de Ctrl+IA cortada (%s): %r", client_address, error)


_servidor: _Servidor | None = None


def url_servidor() -> str:
	"""Dirección del servidor HTTP local (lo arranca la primera vez): ``http://127.0.0.1:<puerto>/mcp``."""
	global _servidor
	with _candado:
		if _servidor is None:
			servidor = _Servidor(("127.0.0.1", 0), _Manejador)
			threading.Thread(target=servidor.serve_forever, name="ctrlia-mcp-http", daemon=True).start()
			_servidor = servidor
			log.info("Servidor MCP de Ctrl+IA en 127.0.0.1:%d", servidor.server_address[1])
		return "http://127.0.0.1:%d/mcp" % _servidor.server_address[1]


def detener_servidor() -> None:
	"""Para el servidor HTTP (al salir y en las pruebas)."""
	global _servidor
	with _candado:
		servidor, _servidor = _servidor, None
	if servidor is not None:
		servidor.shutdown()
		servidor.server_close()


# ---------------------------------------------------------------------------
# Lo que se da a cada agente
# ---------------------------------------------------------------------------


def orden_puente() -> list[str]:
	"""Programa (y argumentos) del puente stdio: ``ctrlia-mcp.exe`` compilado o Python con ``app/puente_mcp.py``."""
	if getattr(sys, "frozen", False):
		return [str(Path(sys.executable).resolve().with_name(EJECUTABLE_PUENTE))]
	script = Path(__file__).resolve().parent.parent / "puente_mcp.py"
	return [str(Path(sys.executable).resolve()), "-I", str(script)]


def config_codex(sesion: SesionHerramientas) -> dict[str, Any]:
	"""Claves ``config`` de ``thread/start``, ``thread/resume`` y ``thread/fork`` de Codex (servidor HTTP)."""
	prefijo = "mcp_servers.%s." % NOMBRE_SERVIDOR
	return {
		prefijo + "url": url_servidor(),
		prefijo + "http_headers": {"Authorization": "Bearer " + sesion.token},
		# Son inofensivas y solo tocan Ctrl+IA: Codex no pregunta por ellas.
		prefijo + "default_tools_approval_mode": "approve",
	}


def servidor_acp(sesion: SesionHerramientas, http: bool) -> dict[str, Any]:
	"""Entrada de ``mcpServers`` de ``session/new``, ``session/load`` y ``session/resume`` (ACP)."""
	url = url_servidor()
	if http:
		return {"type": "http", "name": NOMBRE_SERVIDOR, "url": url,
			"headers": [{"name": "Authorization", "value": "Bearer " + sesion.token}]}
	orden = orden_puente()
	return {"name": NOMBRE_SERVIDOR, "command": orden[0], "args": orden[1:],
		"env": [{"name": VARIABLE_URL, "value": url}, {"name": VARIABLE_TOKEN, "value": sesion.token}]}


def ruta_conexion() -> Path:
	"""Archivo con la dirección y el token de enrutado actuales (lo lee el puente de ``agy``)."""
	from app.core.paths import ruta_cache

	return ruta_cache() / "herramientas_ctrlia" / "conexion.json"


def carpeta_agente_agy() -> Path:
	"""Carpeta que se añade a ``agy`` con ``--add-dir``: tiene el complemento ``.agents/plugins/ctrlia``."""
	from app.core.paths import ruta_cache

	return ruta_cache() / "herramientas_ctrlia" / "agy"


def _escribir_atomico(ruta: Path, texto: str) -> None:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	temporal = ruta.with_name(ruta.name + ".tmp")
	temporal.write_text(texto, encoding="utf-8", newline="\n")
	os.replace(temporal, ruta)


def preparar_agy() -> Path:
	"""Deja listos el complemento ``ctrlia`` de ``agy`` y el archivo de conexión; devuelve la carpeta para ``--add-dir``.

	El complemento no lleva nada que cambie entre arranques: solo el puente y la
	ruta fija de :func:`ruta_conexion`.
	"""
	conexion = ruta_conexion()
	_escribir_atomico(conexion, json.dumps({"url": url_servidor(), "token": _token_enrutado}))
	orden = orden_puente()
	carpeta = carpeta_agente_agy()
	complemento = carpeta / ".agents" / "plugins" / NOMBRE_SERVIDOR
	archivos = {
		complemento / "plugin.json": json.dumps({"name": NOMBRE_SERVIDOR,
			"description": "Herramientas de Ctrl+IA: avisar a la persona, dar la sesión por terminada y renombrarla."},
			ensure_ascii=False, indent=2),
		complemento / "mcp_config.json": json.dumps({"mcpServers": {NOMBRE_SERVIDOR: {
			"command": orden[0], "args": orden[1:], "env": {VARIABLE_CONEXION: str(conexion)}}}},
			ensure_ascii=False, indent=2),
	}
	for ruta, texto in archivos.items():
		try:
			actual = ruta.read_text(encoding="utf-8")
		except OSError:
			actual = ""
		if actual != texto:
			_escribir_atomico(ruta, texto)
	# El agente propio de las pruebas de 1.5.0 dejaba a agy sin herramientas de edición: que no quede.
	antiguo = carpeta / ".agents" / "agents" / (NOMBRE_SERVIDOR + ".md")
	try:
		antiguo.unlink(missing_ok=True)
	except OSError:
		log.debug("No se pudo quitar %s", antiguo, exc_info=True)
	return carpeta


#: Nombre con el que ``agy`` ve el servidor del complemento: «complemento_servidor».
SERVIDOR_AGY = "%s_%s" % (NOMBRE_SERVIDOR, NOMBRE_SERVIDOR)
#: Regla de ``agy`` que permite todas las herramientas de Ctrl+IA (sin ella, ``agy`` las deniega en silencio).
REGLA_AGY = "mcp(%s/*)" % SERVIDOR_AGY


def inventario_basico(agente: str, nota: str = "") -> Any:
	"""Inventario mínimo (Ctrl+Alt+H) para los agentes que no dicen lo que tienen cargado: solo lo de Ctrl+IA."""
	from app.services import inventario

	resultado = inventario.Inventario(agente)
	resultado.servidores_mcp.append((NOMBRE_SERVIDOR, DESCRIPCION_INVENTARIO))
	resultado.nota = nota or ("%s no dice qué más tiene cargado; aquí solo sale lo que le da Ctrl+IA." % agente)
	return resultado
