"""Red HTTP robusta: sesiones con reintentos, tiempos de espera y errores comprensibles.

Por qué existe: una petición sin tiempo de espera puede colgar un hilo para
siempre; un reintento mal puesto duplica una publicación; un error como
«HTTPSConnectionPool(host=...): Max retries exceeded» no le dice nada a quien
usa un lector de pantalla. Este módulo concentra las decisiones:

- ``crear_sesion(ConfiguracionRed(...))``: ``requests.Session`` con reintentos
  (backoff exponencial con tope, 429/500/502/503/504, ``Retry-After`` respetado
  y limitado, solo métodos idempotentes), tiempos de espera SIEMPRE (un
  adaptador los pone si la llamada no los indica), User-Agent propio, proxy de
  la configuración o del sistema, verificación TLS con certifi, límite de
  peticiones por servidor y registro de cada respuesta con la URL redactada.
- ``sesion_de_hilo()``: una sesión por hilo (requests no garantiza compartirla).
- ``clasificar_error(error)``: categoría (red, servidor, cliente, cancelado,
  seguridad, local, desconocido), mensaje en español para el usuario, si
  conviene reintentar y cuánto esperar.
- ``redactar_url(url)``: quita tokens, claves y contraseñas antes de registrar.
- ``instalar_tiempo_global(segundos)``: red de seguridad para bibliotecas que
  abren sockets sin tiempo de espera (urllib, ftplib, feedparser...).

PLANTILLA del agente «red-http».
"""

from __future__ import annotations

import builtins
import email.utils
import errno
import inspect
import logging
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# urllib3 anota en DEBUG la URL completa, con tokens incluidos: se sube a INFO
# para que nunca llegue al registro sin redactar (este módulo ya la registra redactada).
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


def _(texto: str) -> str:
	"""Traduce con el ``_`` de gettext si la aplicación lo instaló en builtins."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


# Métodos que se pueden repetir sin efectos duplicados. PUT y DELETE también son
# idempotentes según RFC 9110, pero muchas API no lo cumplen: añádelos solo si la
# del proyecto lo garantiza. POST y PATCH NUNCA se reintentan solos.
METODOS_REINTENTABLES = frozenset({"GET", "HEAD", "OPTIONS"})
ESTADOS_REINTENTABLES = frozenset({429, 500, 502, 503, 504})

# Parámetros de consulta cuyo valor nunca debe llegar al registro.
_PARAMETROS_SECRETOS = frozenset({
	"token", "access_token", "refresh_token", "id_token", "auth", "authorization", "key", "api_key",
	"apikey", "password", "passwd", "pass", "pwd", "secret", "client_secret", "signature", "sig",
	"session", "sessionid", "sid", "code", "x-amz-signature", "x-amz-credential", "x-amz-security-token",
})


# ════════════════════════════════════════════════════════════════════════════
# Configuración y sesión
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ConfiguracionRed:
	"""Todo lo que decide cómo se conecta la aplicación.

	:param proxy: ``"sistema"`` (variables de entorno y configuración de Windows),
		``"ninguno"`` o una URL (``http://usuario:clave@servidor:8080``,
		``socks5h://...`` con ``requests[socks]``).
	:param verificar: ``True`` (certifi), o la ruta de un paquete de certificados
		propio. Nunca ``False`` en producción.
	:param espera_maxima_servidor: si un servidor pide esperar más que esto
		(``Retry-After``), la petición falla enseguida con un mensaje claro en vez
		de dejar el hilo dormido.
	"""

	nombre_app: str = "MiAplicacion"
	version_app: str = "0.0.0"
	contacto: str = ""
	tiempo_conexion: float = 10.0
	tiempo_lectura: float = 30.0
	reintentos: int = 3
	factor_espera: float = 0.5
	espera_maxima: float = 30.0
	espera_maxima_servidor: float = 120.0
	proxy: str = "sistema"
	verificar: bool | str = True
	max_redirecciones: int = 10
	tamano_grupo: int = 10
	intervalo_minimo_por_servidor: float = 0.0
	cabeceras: dict[str, str] = field(default_factory=dict)

	@property
	def user_agent(self) -> str:
		"""User-Agent propio: identifica la aplicación ante el servidor (y su registro)."""
		contacto = f"; +{self.contacto}" if self.contacto else ""
		return f"{self.nombre_app}/{self.version_app} (Windows{contacto}) python-requests/{requests.__version__}"


class ErrorRed(Exception):
	"""Error de red ya clasificado, con un mensaje apto para el usuario.

	Los módulos del proyecto pueden heredar de ella: ``clasificar_error`` respeta
	sus atributos ``categoria``, ``mensaje_usuario``, ``reintentar`` y ``codigo_http``.
	"""

	def __init__(
		self,
		mensaje_usuario: str,
		categoria: str = "desconocido",
		reintentar: bool = False,
		codigo_http: int | None = None,
		espera: float | None = None,
		detalle: str = "",
	) -> None:
		super().__init__(detalle or mensaje_usuario)
		self.mensaje_usuario = mensaje_usuario
		self.categoria = categoria
		self.reintentar = reintentar
		self.codigo_http = codigo_http
		self.espera = espera
		# Lo lee el gestor de tareas para no volcar trazas de fallos pasajeros.
		self.transitorio = reintentar and categoria in ("red", "servidor")


class LimitadorServidores:
	"""Evita saturar a un servidor que pidió calma, desde CUALQUIER hilo.

	urllib3 respeta ``Retry-After`` dentro de una petición; esto lo recuerda para
	las siguientes: si un servidor contestó 429/503 con ``Retry-After: 60``, las
	peticiones a ese servidor esperan (si es poco) o fallan enseguida (si es mucho)
	en vez de insistir. También admite un intervalo mínimo entre peticiones.
	"""

	def __init__(self, intervalo_minimo: float = 0.0, espera_maxima: float = 120.0) -> None:
		self.intervalo_minimo = intervalo_minimo
		self.espera_maxima = espera_maxima
		self._cerrojo = threading.Lock()
		self._libre_desde: dict[str, float] = {}

	def bloquear(self, servidor: str, segundos: float) -> None:
		"""Anota que ``servidor`` no debe recibir peticiones durante ``segundos``."""
		with self._cerrojo:
			hasta = time.monotonic() + max(0.0, segundos)
			self._libre_desde[servidor] = max(self._libre_desde.get(servidor, 0.0), hasta)

	def espera_pendiente(self, servidor: str) -> float:
		"""Segundos que faltan para poder llamar a ``servidor``."""
		with self._cerrojo:
			return max(0.0, self._libre_desde.get(servidor, 0.0) - time.monotonic())

	def antes_de_pedir(self, servidor: str) -> None:
		"""Espera el turno o lanza ``ErrorRed`` si la espera supera el máximo."""
		with self._cerrojo:
			ahora = time.monotonic()
			libre = self._libre_desde.get(servidor, 0.0)
			espera = max(0.0, libre - ahora)
			if espera > self.espera_maxima:
				raise ErrorRed(
					_("El servidor ha pedido que esperemos unos {minutos} minutos antes de volver a "
						"intentarlo.").format(minutos=max(1, round(espera / 60))),
					categoria="servidor", reintentar=True, codigo_http=429, espera=espera,
					detalle=f"{servidor} bloqueado {espera:.0f} s por Retry-After",
				)
			# Reserva el turno siguiente para que dos hilos no salgan a la vez.
			self._libre_desde[servidor] = max(libre, ahora) + self.intervalo_minimo
		if espera > 0:
			log.debug("Esperando %.1f s antes de llamar a %s.", espera, servidor)
			time.sleep(espera)


class _AdaptadorRobusto(HTTPAdapter):
	"""Adaptador que pone tiempos de espera por defecto y respeta el limitador.

	requests pasa ``timeout=None`` cuando la llamada no lo indica, y ``None``
	significa ESPERAR PARA SIEMPRE: aquí se sustituye por (conexión, lectura).
	"""

	def __init__(self, tiempos: tuple[float, float], limitador: LimitadorServidores, **kwargs) -> None:
		self._tiempos = tiempos
		self._limitador = limitador
		super().__init__(**kwargs)

	def send(self, request, **kwargs):  # noqa: ANN001, ANN201 - firma de requests
		if kwargs.get("timeout") is None:
			kwargs["timeout"] = self._tiempos
		servidor = urlsplit(request.url).netloc.lower()
		self._limitador.antes_de_pedir(servidor)
		respuesta = super().send(request, **kwargs)
		if respuesta.status_code in (429, 503):
			segundos = segundos_retry_after(respuesta.headers.get("Retry-After"))
			if segundos:
				self._limitador.bloquear(servidor, segundos)
		return respuesta


def _crear_reintentos(configuracion: ConfiguracionRed) -> Retry:
	"""Política de reintentos compatible con urllib3 1.26 y 2.x.

	Se usa una subclase para poner tope a ``Retry-After`` en todas las versiones:
	hasta urllib3 2.6 no había límite, y desde entonces el máximo por defecto es de
	6 horas. Un servidor que pide «vuelve dentro de una hora» no debe dormir un
	hilo una hora: se espera como mucho ``espera_maxima_servidor`` y el limitador
	se encarga de las peticiones siguientes.
	"""
	tope = float(configuracion.espera_maxima_servidor)

	class ReintentosConTope(Retry):
		# urllib3 1.26 lee el tope del backoff de estos atributos de clase.
		DEFAULT_BACKOFF_MAX = configuracion.espera_maxima
		BACKOFF_MAX = configuracion.espera_maxima

		def parse_retry_after(self, retry_after: str) -> float:  # noqa: D102
			return min(super().parse_retry_after(retry_after), tope)

	opciones: dict = {
		"total": configuracion.reintentos,
		"connect": configuracion.reintentos,
		"read": configuracion.reintentos,
		"status": configuracion.reintentos,
		"backoff_factor": configuracion.factor_espera,
		"status_forcelist": ESTADOS_REINTENTABLES,
		"allowed_methods": METODOS_REINTENTABLES,
		"respect_retry_after_header": True,
		# Tras agotar reintentos se devuelve la última respuesta (503, 429...): así
		# el código ve el estado real y clasificar_error da un mensaje preciso.
		"raise_on_status": False,
	}
	if "backoff_max" in inspect.signature(Retry.__init__).parameters:  # urllib3 2.x
		opciones["backoff_max"] = configuracion.espera_maxima
	return ReintentosConTope(**opciones)


def ruta_certificados() -> str:
	"""Ruta del paquete de certificados de confianza (certifi).

	En la versión compilada con cx_Freeze, ``certifi`` debe quedar FUERA del zip de
	bibliotecas (en ``packages``, no en ``zip_include_packages``) para que
	``cacert.pem`` sea un archivo real: si no, certifi lo extrae a una carpeta
	temporal del sistema o falla. Se busca primero junto al ejecutable.
	"""
	if getattr(sys, "frozen", False):
		candidato = Path(sys.executable).resolve().parent / "lib" / "certifi" / "cacert.pem"
		if candidato.is_file():
			return str(candidato)
	import certifi

	return certifi.where()


def crear_sesion(configuracion: ConfiguracionRed | None = None) -> requests.Session:
	"""Crea una ``requests.Session`` lista para producción. Ver ``ConfiguracionRed``."""
	configuracion = configuracion or ConfiguracionRed()
	sesion = requests.Session()
	sesion.headers["User-Agent"] = configuracion.user_agent
	sesion.headers.update(configuracion.cabeceras)
	sesion.max_redirects = configuracion.max_redirecciones
	limitador = LimitadorServidores(configuracion.intervalo_minimo_por_servidor, configuracion.espera_maxima_servidor)
	adaptador = _AdaptadorRobusto(
		(configuracion.tiempo_conexion, configuracion.tiempo_lectura),
		limitador,
		max_retries=_crear_reintentos(configuracion),
		pool_connections=configuracion.tamano_grupo,
		pool_maxsize=configuracion.tamano_grupo,
	)
	sesion.mount("https://", adaptador)
	sesion.mount("http://", adaptador)
	sesion.limitador = limitador  # type: ignore[attr-defined]  # accesible para pruebas y diagnóstico

	proxy = (configuracion.proxy or "sistema").strip()
	if proxy.lower() == "ninguno":
		# trust_env=False ignora HTTP(S)_PROXY y la configuración de Windows (y también
		# .netrc y REQUESTS_CA_BUNDLE, que aquí no se quieren).
		sesion.trust_env = False
	elif proxy.lower() != "sistema":
		sesion.proxies = {"http": proxy, "https": proxy}

	if configuracion.verificar is False:
		log.warning("La verificación de certificados TLS está DESACTIVADA: solo es aceptable en pruebas.")
		sesion.verify = False
	elif isinstance(configuracion.verificar, str):
		sesion.verify = configuracion.verificar
	else:
		sesion.verify = ruta_certificados()

	sesion.hooks["response"].append(_registrar_respuesta)
	log.debug(
		"Sesión HTTP creada: %s, tiempos (%.0f s, %.0f s), %d reintentos, proxy %s.",
		configuracion.user_agent, configuracion.tiempo_conexion, configuracion.tiempo_lectura,
		configuracion.reintentos, "del sistema" if proxy.lower() == "sistema" else ("ninguno" if proxy.lower() == "ninguno" else redactar_url(proxy)),
	)
	return sesion


def _registrar_respuesta(respuesta: requests.Response, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
	"""Gancho de requests: una línea DEBUG por respuesta, con la URL redactada."""
	if log.isEnabledFor(logging.DEBUG):
		log.debug(
			"%s %s → %d en %.2f s", respuesta.request.method, redactar_url(respuesta.url),
			respuesta.status_code, respuesta.elapsed.total_seconds(),
		)


_local = threading.local()
_configuracion_global = ConfiguracionRed()


def configurar(configuracion: ConfiguracionRed) -> None:
	"""Fija la configuración de las sesiones por hilo (al arrancar y al guardar preferencias).

	Las sesiones ya creadas se sustituyen en su siguiente uso.
	"""
	global _configuracion_global
	_configuracion_global = configuracion


def sesion_de_hilo() -> requests.Session:
	"""Sesión propia del hilo actual; reutiliza conexiones (keep-alive) entre peticiones."""
	sesion = getattr(_local, "sesion", None)
	if sesion is None or getattr(_local, "configuracion", None) is not _configuracion_global:
		if sesion is not None:
			sesion.close()
		sesion = crear_sesion(_configuracion_global)
		_local.sesion, _local.configuracion = sesion, _configuracion_global
	return sesion


def instalar_tiempo_global(segundos: float = 60.0) -> None:
	"""Pone un tiempo de espera por defecto a TODOS los sockets que se creen sin él.

	Red de seguridad para bibliotecas que no permiten indicarlo (urllib, ftplib,
	feedparser...): sin esto, un servidor mudo deja un hilo colgado para siempre.
	requests no se ve afectado porque siempre recibe el suyo. Llámala una vez al
	principio de ``main``. Cuidado: afecta también a sockets que escuchan
	(``accept`` fallará con ``TimeoutError``); ponles ``settimeout(None)``.
	"""
	socket.setdefaulttimeout(segundos)
	log.debug("Tiempo de espera global de sockets: %.0f s.", segundos)


# ════════════════════════════════════════════════════════════════════════════
# Utilidades
# ════════════════════════════════════════════════════════════════════════════

def segundos_retry_after(valor: str | None, ahora: datetime | None = None) -> float | None:
	"""Interpreta la cabecera ``Retry-After`` (segundos o fecha HTTP). ``None`` si no vale."""
	if not valor:
		return None
	valor = valor.strip()
	if valor.isdigit():
		return float(valor)
	try:
		fecha = email.utils.parsedate_to_datetime(valor)
	except (TypeError, ValueError, IndexError):
		return None
	if fecha is None:
		return None
	if fecha.tzinfo is None:
		fecha = fecha.replace(tzinfo=timezone.utc)
	return max(0.0, (fecha - (ahora or datetime.now(timezone.utc))).total_seconds())


def redactar_url(url: str) -> str:
	"""Versión de ``url`` apta para el registro: sin contraseñas ni tokens.

	Sustituye por ``***`` la contraseña de ``usuario:clave@``, los valores de los
	parámetros sensibles (token, key, signature, code...) y un fragmento con
	``access_token`` (flujo implícito de OAuth).
	"""
	try:
		partes = urlsplit(url)
	except ValueError:
		return "<url no válida>"
	netloc = partes.netloc
	if "@" in netloc:
		credenciales, _arroba, servidor = netloc.rpartition("@")
		usuario = credenciales.split(":", 1)[0]
		netloc = f"{usuario}:***@{servidor}" if ":" in credenciales else f"{usuario}@{servidor}"

	def limpiar(consulta: str) -> str:
		if not consulta:
			return consulta
		pares = parse_qsl(consulta, keep_blank_values=True)
		return urlencode(
			[(nombre, "***" if nombre.lower() in _PARAMETROS_SECRETOS else valor) for nombre, valor in pares],
			safe="*/:", quote_via=quote,
		)

	fragmento = partes.fragment
	if "=" in fragmento:
		fragmento = limpiar(fragmento)
	return urlunsplit((partes.scheme, netloc, partes.path, limpiar(partes.query), fragmento))


# ════════════════════════════════════════════════════════════════════════════
# Clasificación de errores
# ════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ErrorClasificado:
	"""Resultado de ``clasificar_error``.

	- ``categoria``: red (pasajero), servidor, cliente, cancelado, seguridad, local, desconocido.
	- ``mensaje``: frase en español para mostrar o anunciar al usuario.
	- ``reintentar``: si tiene sentido volver a intentarlo (automáticamente o con un botón).
	- ``detalle``: texto técnico y redactado para el registro.
	"""

	categoria: str
	mensaje: str
	reintentar: bool
	codigo_http: int | None = None
	espera: float | None = None
	detalle: str = ""

	@property
	def transitorio(self) -> bool:
		"""Se registra como aviso de una línea, no como error con traza."""
		return self.reintentar and self.categoria in ("red", "servidor")


_MENSAJES_HTTP = {
	400: ("cliente", False, "El servidor no ha entendido la petición (error 400)."),
	401: ("cliente", False, "Hace falta iniciar sesión o la sesión ha caducado (error 401)."),
	403: ("cliente", False, "El servidor ha denegado el acceso (error 403)."),
	404: ("cliente", False, "No se ha encontrado lo que se buscaba en el servidor (error 404)."),
	408: ("red", True, "El servidor tardó demasiado en recibir la petición (error 408)."),
	410: ("cliente", False, "El recurso ya no existe en el servidor (error 410)."),
	413: ("cliente", False, "El envío es demasiado grande para el servidor (error 413)."),
	416: ("cliente", False, "El servidor no admite continuar desde ese punto (error 416)."),
	429: ("servidor", True, "El servidor está recibiendo demasiadas peticiones; se volverá a intentar más tarde (error 429)."),
	500: ("servidor", True, "El servidor ha tenido un error interno (error 500)."),
	502: ("servidor", True, "El servidor no está disponible en este momento (error 502)."),
	503: ("servidor", True, "El servidor está saturado o en mantenimiento (error 503)."),
	504: ("servidor", True, "El servidor tardó demasiado en responder (error 504)."),
}


def _mensaje_http(codigo: int, respuesta: requests.Response | None) -> ErrorClasificado:
	espera = segundos_retry_after(respuesta.headers.get("Retry-After")) if respuesta is not None else None
	url = redactar_url(respuesta.url) if respuesta is not None and respuesta.url else ""
	if codigo in _MENSAJES_HTTP:
		categoria, reintentar, texto = _MENSAJES_HTTP[codigo]
	elif 400 <= codigo < 500:
		categoria, reintentar, texto = "cliente", False, "El servidor ha rechazado la petición (error {codigo})."
	elif codigo >= 500:
		categoria, reintentar, texto = "servidor", False, "El servidor ha fallado (error {codigo})."
	else:
		categoria, reintentar, texto = "desconocido", False, "Respuesta inesperada del servidor (código {codigo})."
	return ErrorClasificado(
		categoria, _(texto).format(codigo=codigo), reintentar, codigo, espera, f"HTTP {codigo} en {url}".strip()
	)


def _cadena(error: BaseException) -> list[BaseException]:
	"""El error y todas sus causas.

	requests y urllib3 envuelven unas excepciones en otras (``ConnectionError`` →
	``MaxRetryError`` → ``ReadTimeoutError``), por ``__cause__``/``__context__``, por
	``args[0]`` o por ``.reason``. Con un ``Retry`` en el adaptador, un tiempo de
	lectura agotado llega como ``requests.ConnectionError``, NO como ``ReadTimeout``:
	hay que mirar dentro para dar el mensaje correcto.
	"""
	vistos: list[BaseException] = []
	pendientes: list[BaseException | None] = [error]
	while pendientes and len(vistos) < 16:
		actual = pendientes.pop(0)
		if actual is None or any(actual is visto for visto in vistos):
			continue
		vistos.append(actual)
		razon = getattr(actual, "reason", None)
		pendientes.extend([actual.__cause__, actual.__context__, razon if isinstance(razon, BaseException) else None])
		if actual.args and isinstance(actual.args[0], BaseException):
			pendientes.append(actual.args[0])
	return vistos


def clasificar_error(error: BaseException | None = None, respuesta: requests.Response | None = None) -> ErrorClasificado:
	"""Traduce un error (o una respuesta con estado de error) a categoría + mensaje.

	Úsalo en ``al_fallar`` para anunciar ``mensaje`` y decidir si ofrecer «Reintentar»,
	y en el registro para escribir ``detalle`` (ya redactado).
	"""
	if error is None and respuesta is not None:
		return _mensaje_http(respuesta.status_code, respuesta)
	if error is None:
		return ErrorClasificado("desconocido", _("Error desconocido."), False)
	detalle = redactar_url(f"{type(error).__name__}: {error}")[:300]

	def resultado(categoria: str, mensaje: str, reintentar: bool) -> ErrorClasificado:
		return ErrorClasificado(categoria, mensaje, reintentar, detalle=detalle)

	mensaje_propio = getattr(error, "mensaje_usuario", None)
	if isinstance(mensaje_propio, str) and mensaje_propio:
		return ErrorClasificado(
			getattr(error, "categoria", "desconocido"), mensaje_propio, bool(getattr(error, "reintentar", False)),
			getattr(error, "codigo_http", None), getattr(error, "espera", None), detalle,
		)
	cadena = _cadena(error)
	# Nombre exacto de cada eslabón (no toda la jerarquía: en urllib3, NewConnectionError
	# hereda de ConnectTimeoutError aunque sea una conexión rechazada al instante).
	tipos = {type(eslabon).__name__ for eslabon in cadena}
	jerarquia = {clase.__name__ for eslabon in cadena for clase in type(eslabon).__mro__}

	if jerarquia & {"TareaCancelada", "DescargaCancelada", "CancelledError"}:
		return resultado("cancelado", _("Operación cancelada."), False)
	if isinstance(error, requests.HTTPError) and error.response is not None:
		http = _mensaje_http(error.response.status_code, error.response)
		return ErrorClasificado(http.categoria, http.mensaje, http.reintentar, http.codigo_http, http.espera, detalle)
	if jerarquia & {"SSLError", "SSLCertVerificationError", "CertificateError"}:
		return resultado("seguridad",
			_("No se ha podido comprobar la identidad del servidor (certificado no válido). Por seguridad no se ha "
			"conectado. Revisa la fecha y la hora del equipo o avisa al administrador del servidor."), False)
	if tipos & {"ProxyError"}:
		return resultado("red",
			_("No se ha podido conectar a través del proxy. Revisa la configuración del proxy en las preferencias."), False)
	if tipos & {"ConnectTimeout", "ConnectTimeoutError"}:
		return resultado("red",
			_("El servidor no ha respondido a tiempo al conectar. Comprueba la conexión e inténtalo de nuevo."), True)
	if tipos & {"ReadTimeout", "ReadTimeoutError"} or any(isinstance(e, TimeoutError) for e in cadena):
		return resultado("red", _("El servidor ha dejado de responder. Inténtalo de nuevo dentro de un momento."), True)
	if isinstance(error, requests.exceptions.TooManyRedirects):
		return resultado("servidor", _("El servidor redirige una y otra vez sin llegar a ningún sitio."), False)
	if isinstance(error, (requests.exceptions.MissingSchema, requests.exceptions.InvalidURL,
			requests.exceptions.InvalidSchema)):
		return resultado("cliente", _("La dirección no es válida."), False)
	if any(isinstance(e, socket.gaierror) for e in cadena) or "NameResolutionError" in tipos or any(
		marca in str(e).lower() for e in cadena for marca in ("getaddrinfo failed", "name or service not known",
			"failed to resolve")
	):
		return resultado("red",
			_("No se ha encontrado el servidor. Comprueba la dirección y que haya conexión a Internet."), True)
	if tipos & {"ChunkedEncodingError", "ProtocolError", "IncompleteRead", "RemoteDisconnected"} or any(
		isinstance(e, (ConnectionResetError, ConnectionAbortedError)) for e in cadena
	):
		return resultado("red", _("La conexión se ha cortado mientras se recibían los datos. Inténtalo de nuevo."), True)
	if isinstance(error, (requests.exceptions.ConnectionError, ConnectionError)):
		return resultado("red",
			_("No se ha podido conectar con el servidor. Comprueba la conexión a Internet e inténtalo de nuevo."), True)
	if isinstance(error, PermissionError):
		return resultado("local",
			_("No hay permiso para escribir en la carpeta de destino, o el archivo está abierto en otro programa."), False)
	if isinstance(error, OSError) and error.errno == errno.ENOSPC:
		return resultado("local", _("No queda espacio libre en el disco."), False)
	if isinstance(error, requests.RequestException):
		return resultado("red", _("Ha fallado la comunicación con el servidor."), False)
	return resultado("desconocido", _("Se ha producido un error inesperado."), False)


def comprobar_respuesta(respuesta: requests.Response) -> requests.Response:
	"""Como ``raise_for_status`` pero lanzando ``ErrorRed`` con el mensaje en español."""
	if respuesta.status_code < 400:
		return respuesta
	clasificado = _mensaje_http(respuesta.status_code, respuesta)
	raise ErrorRed(clasificado.mensaje, clasificado.categoria, clasificado.reintentar, clasificado.codigo_http,
		clasificado.espera, clasificado.detalle)
