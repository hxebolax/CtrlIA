"""Gestor de tareas en segundo plano para aplicaciones wxPython.

Por qué existe: todo lo que tarda (red, disco, análisis, importaciones pesadas)
debe salir del hilo de la interfaz. Si ese hilo se bloquea, Windows marca la
ventana como «No responde» y el lector de pantalla se queda MUDO, porque sus
consultas a la ventana se atienden en ese mismo hilo.

Qué ofrece:

- ``GestorTareas.enviar(funcion, *args, al_terminar=..., al_fallar=...,
  al_progresar=..., clave_unica=...)``: ejecuta ``funcion(tarea, *args)`` en un
  hilo del grupo y entrega las respuestas EN EL HILO DE LA INTERFAZ mediante un
  «ejecutor de interfaz» inyectable (``wx.CallAfter`` por defecto; en pruebas,
  ``ejecutor_directo`` o ``EjecutorManual``).
- Cada envío termina SIEMPRE en exactamente una llamada a ``al_terminar`` o a
  ``al_fallar`` (también si se cancela, vence o se rechaza por duplicada). Así los
  indicadores de la interfaz («actualizando...») nunca se quedan atascados.
- Cancelación cooperativa (``TokenCancelacion``), tareas duplicadas unificadas o
  rechazadas por clave, tiempo máximo de ejecución, progreso limitado y agrupado
  (la interfaz nunca recibe cientos de ``CallAfter``), registro de errores con
  traza y nombre de tarea (los fallos transitorios de red van en una línea, sin
  traza repetida), apagado ordenado con espera limitada y estadísticas.
- ``ejecutar_en_interfaz(fn)`` y el decorador ``@en_interfaz`` para tocar
  controles desde cualquier hilo.

wx NO se importa a nivel de módulo: el gestor se prueba sin wx y el arranque no
paga su importación hasta que hace falta.

PLANTILLA del agente «hilos-y-tareas».
"""

from __future__ import annotations

import builtins
import heapq
import itertools
import logging
import queue
import socket
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import wraps
from typing import Any

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	"""Traduce con el ``_`` de gettext si la aplicación lo instaló en builtins."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


# Un ejecutor de interfaz recibe (funcion, *args) y debe llamarla en el hilo de la interfaz.
EjecutorInterfaz = Callable[..., None]


# ════════════════════════════════════════════════════════════════════════════
# Excepciones
# ════════════════════════════════════════════════════════════════════════════

class ErrorTarea(Exception):
	"""Base de los motivos por los que una tarea no devolvió resultado sin haber fallado ella."""


class TareaCancelada(ErrorTarea):
	"""La tarea se canceló (por el usuario, por el cierre de la aplicación...)."""


class TiempoAgotado(ErrorTarea):
	"""La tarea superó su tiempo máximo de ejecución."""


class TareaDuplicada(ErrorTarea):
	"""Se rechazó porque ya hay otra con la misma clave en curso. Es inofensivo:
	el trabajo se está haciendo igualmente; basta con limpiar el estado de la interfaz."""


# ════════════════════════════════════════════════════════════════════════════
# Ejecutores de interfaz
# ════════════════════════════════════════════════════════════════════════════

def _nombre_de(funcion: Any) -> str:
	"""Nombre legible de una función para los mensajes del registro."""
	return getattr(funcion, "__qualname__", None) or getattr(funcion, "__name__", None) or repr(funcion)


def ejecutor_wx(funcion: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
	"""Entrega la llamada al hilo de la interfaz con ``wx.CallAfter``.

	Si ya no hay ``wx.App`` (la aplicación se está cerrando), la llamada se descarta
	con una línea de depuración: las ventanas ya no existen y tocarlas fallaría.
	"""
	import wx  # Diferido: el módulo se puede importar y probar sin wx.

	if wx.GetApp() is None:
		log.debug("Sin wx.App: se descarta la llamada a %s.", _nombre_de(funcion))
		return
	wx.CallAfter(funcion, *args, **kwargs)


def ejecutor_directo(funcion: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
	"""Llama en el mismo hilo. Solo para pruebas y programas sin interfaz."""
	funcion(*args, **kwargs)


class EjecutorManual:
	"""Acumula las llamadas y las ejecuta cuando se llama a ``procesar_pendientes``.

	Imita al bucle de wx en las pruebas: permite comprobar que las respuestas NO se
	ejecutan en el hilo de trabajo y que llegan en orden.
	"""

	def __init__(self) -> None:
		self._cola: queue.SimpleQueue[tuple[Callable[..., Any], tuple, dict]] = queue.SimpleQueue()

	def __call__(self, funcion: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
		self._cola.put((funcion, args, kwargs))

	def procesar_pendientes(self, limite: int | None = None) -> int:
		"""Ejecuta lo acumulado en el hilo que llama. Devuelve cuántas llamadas hizo."""
		hechas = 0
		while limite is None or hechas < limite:
			try:
				funcion, args, kwargs = self._cola.get_nowait()
			except queue.Empty:
				break
			funcion(*args, **kwargs)
			hechas += 1
		return hechas


_ejecutor_global: EjecutorInterfaz = ejecutor_wx


def establecer_ejecutor_interfaz(ejecutor: EjecutorInterfaz) -> EjecutorInterfaz:
	"""Cambia el ejecutor que usan ``ejecutar_en_interfaz`` y ``@en_interfaz``.

	Devuelve el anterior para poder restaurarlo (pruebas).
	"""
	global _ejecutor_global
	anterior, _ejecutor_global = _ejecutor_global, ejecutor
	return anterior


def en_hilo_principal() -> bool:
	"""Indica si el código se ejecuta en el hilo principal (el de la interfaz en wx)."""
	return threading.current_thread() is threading.main_thread()


def ejecutar_en_interfaz(funcion: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
	"""Ejecuta ``funcion`` en el hilo de la interfaz.

	Desde el hilo principal se ejecuta YA (de forma síncrona); desde otro hilo se
	encola con el ejecutor de interfaz y vuelve al instante. Si necesitas que sea
	siempre diferido (por ejemplo, para dejar que wx termine de procesar el evento
	actual antes de mover el foco), usa ``wx.CallAfter`` directamente.
	"""
	if en_hilo_principal():
		funcion(*args, **kwargs)
	else:
		_ejecutor_global(funcion, *args, **kwargs)


def en_interfaz(funcion: Callable[..., Any]) -> Callable[..., None]:
	"""Decorador: la función decorada siempre se ejecuta en el hilo de la interfaz.

	Útil para métodos como ``anunciar_estado`` o ``actualizar_lista`` que se llaman
	desde hilos de trabajo. La llamada devuelve ``None`` (no espera el resultado).
	"""

	@wraps(funcion)
	def envoltorio(*args: Any, **kwargs: Any) -> None:
		ejecutar_en_interfaz(funcion, *args, **kwargs)

	return envoltorio


# ════════════════════════════════════════════════════════════════════════════
# Cancelación y progreso
# ════════════════════════════════════════════════════════════════════════════

class TokenCancelacion:
	"""Señal de cancelación COOPERATIVA: la tarea la consulta y se detiene limpia.

	Un hilo de Python no se puede matar desde fuera; por eso la tarea debe llamar
	a ``comprobar()`` entre pasos y usar ``esperar(segundos)`` en lugar de
	``time.sleep`` (se despierta al instante si se cancela). Con ``al_cancelar``
	se puede cerrar una conexión o un proceso para desbloquear una espera larga.
	Tiene ``is_set()`` para servir donde una biblioteca espera un ``threading.Event``.
	"""

	def __init__(self) -> None:
		self._evento = threading.Event()
		self._cerrojo = threading.Lock()
		self._motivo = ""
		self._al_cancelar: list[Callable[[], Any]] = []

	@property
	def cancelado(self) -> bool:
		"""``True`` si se pidió cancelar."""
		return self._evento.is_set()

	def is_set(self) -> bool:
		"""Compatibilidad con ``threading.Event``."""
		return self._evento.is_set()

	@property
	def motivo(self) -> str:
		"""Motivo de la cancelación (vacío si no se canceló)."""
		return self._motivo

	def cancelar(self, motivo: str = "") -> bool:
		"""Pide la cancelación. Devuelve ``True`` si ha sido esta llamada la que la pidió."""
		with self._cerrojo:
			if self._evento.is_set():
				return False
			self._motivo = motivo or _("Cancelado por el usuario")
			self._evento.set()
			funciones, self._al_cancelar = self._al_cancelar, []
		for funcion in funciones:
			try:
				funcion()
			except Exception:  # noqa: BLE001 - un fallo al liberar no debe impedir cancelar
				log.warning("Falló la acción al cancelar %s.", _nombre_de(funcion), exc_info=True)
		return True

	def al_cancelar(self, funcion: Callable[[], Any]) -> None:
		"""Registra una acción que se ejecuta al cancelar (o ya, si ya se canceló)."""
		with self._cerrojo:
			if not self._evento.is_set():
				self._al_cancelar.append(funcion)
				return
		funcion()

	def comprobar(self) -> None:
		"""Lanza ``TareaCancelada`` si se pidió cancelar. Llámala entre pasos."""
		if self._evento.is_set():
			raise TareaCancelada(self._motivo)

	def esperar(self, segundos: float) -> bool:
		"""Espera como ``time.sleep`` pero se despierta al cancelar. Devuelve ``cancelado``."""
		return self._evento.wait(segundos)


@dataclass(frozen=True)
class Progreso:
	"""Estado del progreso que recibe ``al_progresar``."""

	hecho: float
	total: float | None = None
	texto: str = ""

	@property
	def porcentaje(self) -> int | None:
		"""Porcentaje entero (0-100) o ``None`` si el total es desconocido."""
		if not self.total or self.total <= 0:
			return None
		return int(max(0.0, min(100.0, self.hecho * 100.0 / self.total)))


class HitosProgreso:
	"""Decide cuándo ANUNCIAR el progreso: por hitos, no por cada porcentaje.

	Un lector de pantalla que dice «1 %, 2 %, 3 %...» interrumpe todo lo demás y
	satura al usuario. Con ``paso=25`` solo se anuncian 25, 50, 75 y 100 %.
	La barra visual (``wx.Gauge``) sí puede actualizarse siempre.
	"""

	def __init__(self, paso: int = 25) -> None:
		if not 1 <= paso <= 100:
			raise ValueError("El paso debe estar entre 1 y 100.")
		self._paso = paso
		self._ultimo = 0

	def nuevo_hito(self, porcentaje: int | None) -> int | None:
		"""Devuelve el hito alcanzado si hay que anunciarlo, o ``None``."""
		if porcentaje is None:
			return None
		hito = (min(100, max(0, int(porcentaje))) // self._paso) * self._paso
		if porcentaje >= 100:
			hito = 100
		if hito > self._ultimo:
			self._ultimo = hito
			return hito
		return None

	def reiniciar(self) -> None:
		"""Vuelve a empezar (nueva operación)."""
		self._ultimo = 0


# ════════════════════════════════════════════════════════════════════════════
# Clasificación de errores transitorios
# ════════════════════════════════════════════════════════════════════════════

# Nombres de clase de las bibliotecas de red habituales (requests, urllib3, httpx,
# http.client) que indican un fallo pasajero. Se comparan por nombre para no
# importar esas bibliotecas aquí.
_NOMBRES_TRANSITORIOS = frozenset({
	"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError", "ChunkedEncodingError",
	"ProtocolError", "NewConnectionError", "RemoteDisconnected", "IncompleteRead",
	"TimeoutException", "ConnectError", "ReadError", "WriteError", "NetworkError",
	"RemoteProtocolError", "PoolTimeout",
})
# Aunque hereden de ConnectionError, estos NO son pasajeros: reintentar no arregla un
# certificado inválido.
_NOMBRES_NO_TRANSITORIOS = frozenset({"SSLError", "CertificateError", "SSLCertVerificationError"})
_ESTADOS_TRANSITORIOS = frozenset({408, 429, 500, 502, 503, 504})


def _codigo_http(error: BaseException) -> int | None:
	"""Código HTTP asociado a un error, si lo lleva (``codigo_http`` o ``response.status_code``)."""
	codigo = getattr(error, "codigo_http", None)
	if codigo is None:
		codigo = getattr(getattr(error, "response", None), "status_code", None)
	return codigo if isinstance(codigo, int) else None


def es_error_transitorio(error: BaseException, _profundidad: int = 0) -> bool:
	"""Indica si un error es un fallo pasajero de red (culpa del otro extremo o de la conexión).

	Criterios, en orden: atributo ``transitorio`` explícito, clases de la biblioteca
	estándar (``TimeoutError``, ``ConnectionError``, ``socket.gaierror``), nombres de
	clase de las bibliotecas de red, código HTTP 408/429/5xx y, por último, la causa
	encadenada (``raise ... from``). No busca números dentro del texto: «500» aparece
	en muchos mensajes que no son un error 500.
	"""
	marca = getattr(error, "transitorio", None)
	if isinstance(marca, bool):
		return marca
	nombres = {clase.__name__ for clase in type(error).__mro__}
	if nombres & _NOMBRES_NO_TRANSITORIOS:
		return False
	if isinstance(error, (TimeoutError, ConnectionError, socket.gaierror)):
		return True
	if nombres & _NOMBRES_TRANSITORIOS:
		return True
	if _codigo_http(error) in _ESTADOS_TRANSITORIOS:
		return True
	causa = error.__cause__ or error.__context__
	if causa is not None and _profundidad < 3:
		return es_error_transitorio(causa, _profundidad + 1)
	return False


def _resumen(error: BaseException, maximo: int = 200) -> str:
	"""Una línea corta con el tipo y el mensaje (sin volcar cabeceras o HTML enormes)."""
	texto = " ".join(str(error).split()) or type(error).__name__
	if len(texto) > maximo:
		texto = texto[:maximo] + "…"
	return f"{type(error).__name__}: {texto}"


# ════════════════════════════════════════════════════════════════════════════
# Tarea
# ════════════════════════════════════════════════════════════════════════════

class Tarea:
	"""Una unidad de trabajo enviada al gestor. La función la recibe como primer argumento.

	Dentro de la función de trabajo:
	- ``tarea.comprobar()`` entre pasos (lanza ``TareaCancelada`` si se canceló).
	- ``tarea.esperar(segundos)`` en lugar de ``time.sleep``.
	- ``tarea.progresar(hecho, total, texto)`` para informar del avance.

	Desde la interfaz:
	- ``tarea.cancelar()`` y ``tarea.estado`` (``pendiente``, ``en_curso``,
	  ``cancelando``, ``completada``, ``fallida``, ``cancelada``, ``vencida``).
	"""

	def __init__(
		self,
		gestor: GestorTareas,
		identificador: int,
		nombre: str,
		clave: str | None,
		funcion: Callable[..., Any],
		args: tuple,
		tiempo_maximo: float | None,
	) -> None:
		self.id = identificador
		self.nombre = nombre
		self.clave = clave
		self.tiempo_maximo = tiempo_maximo
		self.token = TokenCancelacion()
		self.resultado: Any = None
		self.error: BaseException | None = None
		self.creada = time.monotonic()
		self.inicio: float | None = None
		self.fin: float | None = None
		self._gestor = gestor
		self._funcion = funcion
		self._args = args
		self._estado = "pendiente"
		self._finalizada = False
		self._final = threading.Event()
		self._futuro: Future | None = None
		self._al_terminar: list[Callable[[Any], Any]] = []
		self._al_fallar: list[Callable[[BaseException], Any]] = []
		self._al_progresar: list[Callable[[Progreso], Any]] = []
		self._cerrojo_progreso = threading.Lock()
		self._progreso: Progreso | None = None
		self._progreso_en_vuelo = False
		self._ultimo_envio_progreso = 0.0

	def __repr__(self) -> str:
		return f"<Tarea {self.id} «{self.nombre}» {self.estado}>"

	# ── Consultas ──────────────────────────────────────────────────────────

	@property
	def estado(self) -> str:
		"""Estado consultable desde la interfaz; ``cancelando`` mientras la tarea se detiene."""
		if self._estado in ("pendiente", "en_curso") and self.token.cancelado:
			return "cancelando"
		return self._estado

	@property
	def terminada(self) -> bool:
		"""``True`` cuando ya se decidió su resultado (con éxito o no)."""
		return self._finalizada

	@property
	def cancelado(self) -> bool:
		"""Atajo de ``token.cancelado`` para la función de trabajo."""
		return self.token.cancelado

	@property
	def duracion(self) -> float | None:
		"""Segundos de ejecución (hasta ahora si sigue en curso)."""
		if self.inicio is None:
			return None
		return (self.fin or time.monotonic()) - self.inicio

	# ── Uso desde la función de trabajo ────────────────────────────────────

	def comprobar(self) -> None:
		"""Lanza ``TareaCancelada`` si se pidió cancelar."""
		self.token.comprobar()

	def esperar(self, segundos: float) -> bool:
		"""Espera interrumpible por la cancelación. Devuelve ``True`` si se canceló."""
		return self.token.esperar(segundos)

	def progresar(self, hecho: float, total: float | None = None, texto: str = "") -> None:
		"""Informa del avance. Se puede llamar miles de veces: el gestor limita y agrupa.

		Solo hay una entrega en vuelo a la vez y se envía como mucho una cada
		``intervalo_progreso`` segundos; si la interfaz va atrasada, recibe el ÚLTIMO
		valor, no una cola de valores viejos. El valor final (``hecho >= total``) no
		se descarta por el intervalo.
		"""
		if not self._al_progresar or self._finalizada:
			return
		ahora = time.monotonic()
		final = total is not None and hecho >= total
		with self._cerrojo_progreso:
			self._progreso = Progreso(hecho, total, texto)
			if self._progreso_en_vuelo:
				return
			if not final and ahora - self._ultimo_envio_progreso < self._gestor.intervalo_progreso:
				return
			self._progreso_en_vuelo = True
			self._ultimo_envio_progreso = ahora
		self._gestor._entregar(self._entregar_progreso)

	def _entregar_progreso(self) -> None:
		"""Se ejecuta en el hilo de la interfaz: entrega el último progreso conocido."""
		with self._cerrojo_progreso:
			progreso = self._progreso
			self._progreso_en_vuelo = False
		if progreso is None:
			return
		for funcion in list(self._al_progresar):
			funcion(progreso)

	# ── Uso desde la interfaz o las pruebas ────────────────────────────────

	def cancelar(self, motivo: str = "") -> bool:
		"""Pide cancelar la tarea. Ver ``GestorTareas.cancelar``."""
		return self._gestor.cancelar(self, motivo)

	def esperar_fin(self, tiempo: float | None = None) -> bool:
		"""Bloquea hasta que termine. NUNCA la llames desde el hilo de la interfaz."""
		return self._final.wait(tiempo)


# ════════════════════════════════════════════════════════════════════════════
# Grupo de hilos daemon
# ════════════════════════════════════════════════════════════════════════════

class _GrupoHilosDaemon(Executor):
	"""Ejecutor compatible con ``concurrent.futures`` cuyos hilos son daemon.

	Por qué: desde Python 3.9 los hilos de ``ThreadPoolExecutor`` NO son daemon y el
	intérprete los espera (``join``) al salir. Una tarea atascada en una lectura
	sin tiempo de espera impediría cerrar la aplicación: la ventana desaparece pero
	el proceso sigue vivo, bloquea sus archivos y la instancia única no deja volver
	a abrirla. Con hilos daemon el proceso termina aunque quede alguno atascado
	(``apagar`` ya dio a las tareas la oportunidad de terminar limpias).
	"""

	def __init__(self, max_hilos: int, prefijo: str, inicializador: Callable[[], None] | None = None) -> None:
		self._max = max_hilos
		self._prefijo = prefijo
		self._inicializador = inicializador
		self._cola: queue.SimpleQueue = queue.SimpleQueue()
		self._hilos: list[threading.Thread] = []
		self._ociosos = threading.Semaphore(0)
		self._cerrojo = threading.Lock()
		self._cerrado = False

	def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:  # noqa: D102
		with self._cerrojo:
			if self._cerrado:
				raise RuntimeError("El grupo de hilos está cerrado.")
			futuro: Future = Future()
			self._cola.put((futuro, fn, args, kwargs))
			if not self._ociosos.acquire(timeout=0) and len(self._hilos) < self._max:
				hilo = threading.Thread(
					target=self._trabajar, name=f"{self._prefijo}_{len(self._hilos)}", daemon=True
				)
				hilo.start()
				self._hilos.append(hilo)
			return futuro

	def _trabajar(self) -> None:
		if self._inicializador is not None:
			try:
				self._inicializador()
			except Exception:  # noqa: BLE001 - el ajuste del hilo es opcional
				log.warning("Falló la preparación del hilo de trabajo.", exc_info=True)
		while True:
			elemento = self._cola.get()
			if elemento is None:
				return
			futuro, fn, args, kwargs = elemento
			if self._cerrado:
				# Sacada de la cola justo cuando empezaba el apagado: no se ejecuta.
				futuro.cancel()
			if futuro.set_running_or_notify_cancel():
				try:
					resultado = fn(*args, **kwargs)
				except BaseException as error:  # noqa: BLE001 - se transmite al futuro
					futuro.set_exception(error)
				else:
					futuro.set_result(resultado)
			# Soltar referencias antes de esperar el siguiente trabajo.
			del elemento, futuro, fn, args, kwargs
			self._ociosos.release()

	def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:  # noqa: D102
		descartados: list[Future] = []
		with self._cerrojo:
			self._cerrado = True
			if cancel_futures:
				while True:
					try:
						elemento = self._cola.get_nowait()
					except queue.Empty:
						break
					if elemento is not None:
						descartados.append(elemento[0])
			for _hilo in self._hilos:
				self._cola.put(None)
		# Se cancelan fuera del cerrojo: cancel() ejecuta las funciones de aviso del
		# futuro, que toman el cerrojo del gestor (evita un abrazo mortal).
		for futuro in descartados:
			futuro.cancel()
		if wait:
			for hilo in self._hilos:
				hilo.join()


def _bajar_prioridad_hilo() -> None:
	"""Baja la prioridad del hilo actual a «inferior a la normal» (solo Windows).

	Así Windows prefiere al hilo de la interfaz cuando compiten por la CPU. No
	sustituye a ``sys.setswitchinterval``: la prioridad del sistema no decide
	quién se queda con el GIL. Si falla, no pasa nada.
	"""
	if sys.platform != "win32":
		return
	import ctypes

	kernel32 = ctypes.windll.kernel32
	THREAD_PRIORITY_BELOW_NORMAL = -1
	if not kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL):
		log.debug("No se pudo bajar la prioridad del hilo %s.", threading.current_thread().name)


# ════════════════════════════════════════════════════════════════════════════
# Gestor
# ════════════════════════════════════════════════════════════════════════════

class GestorTareas:
	"""Ejecuta tareas en hilos y entrega sus resultados en el hilo de la interfaz.

	:param max_hilos: hilos simultáneos. Para E/S (red, disco) entre 4 y 10; para
		CPU pura no ayuda subirlo (el GIL): usa un proceso aparte.
	:param nombre: prefijo del nombre de los hilos (aparece en el registro).
	:param ejecutor_interfaz: cómo llegar al hilo de la interfaz; ``None`` = ``wx.CallAfter``.
	:param daemon: ``True`` usa hilos daemon (ver ``_GrupoHilosDaemon``); ``False``,
		un ``ThreadPoolExecutor`` estándar.
	:param prioridad_baja: baja la prioridad de los hilos de trabajo en Windows.
	:param intervalo_progreso: segundos mínimos entre entregas de progreso.
	:param duplicadas: ``"unificar"`` (la segunda petición se suma a la primera y
		recibe el mismo resultado) o ``"rechazar"`` (recibe ``TareaDuplicada``).
	:param ventana_avisos: segundos durante los que un mismo fallo transitorio no se
		vuelve a escribir como aviso.
	"""

	def __init__(
		self,
		max_hilos: int = 4,
		nombre: str = "tareas",
		ejecutor_interfaz: EjecutorInterfaz | None = None,
		daemon: bool = True,
		prioridad_baja: bool = True,
		intervalo_progreso: float = 0.25,
		duplicadas: str = "unificar",
		ventana_avisos: float = 300.0,
	) -> None:
		if duplicadas not in ("unificar", "rechazar"):
			raise ValueError("duplicadas debe ser «unificar» o «rechazar».")
		self.nombre = nombre
		self.max_hilos = max(1, int(max_hilos))
		self.intervalo_progreso = intervalo_progreso
		self._ejecutor_interfaz = ejecutor_interfaz or ejecutor_wx
		self._duplicadas = duplicadas
		self._ventana_avisos = ventana_avisos
		inicializador = _bajar_prioridad_hilo if prioridad_baja else None
		if daemon:
			self._grupo: Executor = _GrupoHilosDaemon(self.max_hilos, nombre, inicializador)
		else:
			self._grupo = ThreadPoolExecutor(self.max_hilos, thread_name_prefix=nombre, initializer=inicializador)
		self._cerrojo = threading.RLock()
		self._condicion = threading.Condition(self._cerrojo)
		self._secuencia = itertools.count(1)
		self._activas: dict[int, Tarea] = {}
		self._por_clave: dict[str, Tarea] = {}
		self._en_ejecucion: set[Tarea] = set()
		self._plazos: list[tuple[float, int, Tarea]] = []
		self._hilo_plazos: threading.Thread | None = None
		self._cerrado = False
		self._entregar_activo = True
		self._avisos: dict[tuple[str, str], list[float]] = {}
		self._cerrojo_avisos = threading.Lock()
		self._estadisticas = {
			"enviadas": 0, "completadas": 0, "fallidas": 0, "canceladas": 0, "vencidas": 0,
			"rechazadas": 0, "unificadas": 0,
		}
		self._duraciones = [0.0, 0.0, 0]  # suma, máximo, cuántas

	# ── API pública ────────────────────────────────────────────────────────

	def enviar(
		self,
		funcion: Callable[..., Any],
		*args: Any,
		al_terminar: Callable[[Any], Any] | None = None,
		al_fallar: Callable[[BaseException], Any] | None = None,
		al_progresar: Callable[[Progreso], Any] | None = None,
		clave_unica: str | None = None,
		nombre: str | None = None,
		tiempo_maximo: float | None = None,
		duplicadas: str | None = None,
	) -> Tarea | None:
		"""Ejecuta ``funcion(tarea, *args)`` en segundo plano.

		Las respuestas se ejecutan en el hilo de la interfaz: ``al_terminar(resultado)``
		o ``al_fallar(error)`` (exactamente una de las dos) y ``al_progresar(Progreso)``.
		``error`` puede ser ``TareaCancelada``, ``TiempoAgotado`` o ``TareaDuplicada``
		además de cualquier excepción de la función.

		:param clave_unica: identifica trabajo equivalente («feed:7», «cuenta:3:inicio»).
		:param tiempo_maximo: segundos de ejecución antes de darla por vencida.
		:return: la ``Tarea`` (la existente si se unificó) o ``None`` si se rechazó o el
			gestor está cerrado.
		"""
		nombre = nombre or _nombre_de(funcion)
		with self._cerrojo:
			if self._cerrado:
				log.debug("Tarea «%s» rechazada: el gestor está cerrado.", nombre)
				return None
			existente = self._por_clave.get(clave_unica) if clave_unica is not None else None
			if existente is not None:
				if (duplicadas or self._duplicadas) == "unificar":
					self._anadir_respuestas(existente, al_terminar, al_fallar, al_progresar)
					self._estadisticas["unificadas"] += 1
					log.debug("Tarea «%s» unificada con la que ya estaba en curso (%s).", nombre, clave_unica)
					return existente
				self._estadisticas["rechazadas"] += 1
				rechazada = True
			else:
				rechazada = False
				tarea = Tarea(self, next(self._secuencia), nombre, clave_unica, funcion, args, tiempo_maximo)
				self._anadir_respuestas(tarea, al_terminar, al_fallar, al_progresar)
				self._activas[tarea.id] = tarea
				if clave_unica is not None:
					self._por_clave[clave_unica] = tarea
				self._estadisticas["enviadas"] += 1
				tarea._futuro = self._grupo.submit(self._ejecutar, tarea)
				tarea._futuro.add_done_callback(lambda futuro, t=tarea: self._si_futuro_cancelado(futuro, t))
		if rechazada:
			log.debug("Tarea «%s» rechazada: ya hay otra en curso con la clave %s.", nombre, clave_unica)
			if al_fallar is not None:
				self._entregar(al_fallar, TareaDuplicada(_("Esa operación ya está en curso.")))
			return None
		return tarea

	def cancelar(self, tarea_o_clave: Tarea | str, motivo: str = "") -> bool:
		"""Pide cancelar una tarea (o la que tenga esa clave).

		Si aún no había empezado, no llega a ejecutarse y ``al_fallar`` recibe
		``TareaCancelada`` enseguida. Si está en curso, se detiene cuando la función
		compruebe el token; mientras tanto su estado es ``cancelando`` (anúncialo).
		"""
		with self._cerrojo:
			tarea = self._por_clave.get(tarea_o_clave) if isinstance(tarea_o_clave, str) else tarea_o_clave
		if tarea is None or tarea.terminada:
			return False
		if tarea.token.cancelar(motivo):
			log.info("Cancelación solicitada para la tarea «%s».", tarea.nombre)
		if tarea._futuro is not None:
			tarea._futuro.cancel()  # Si no había empezado, _si_futuro_cancelado la finaliza.
		return True

	def cancelar_todas(self, motivo: str = "") -> int:
		"""Pide cancelar todas las tareas activas. Devuelve cuántas había."""
		with self._cerrojo:
			tareas = list(self._activas.values())
		for tarea in tareas:
			self.cancelar(tarea, motivo)
		return len(tareas)

	def activa(self, clave: str) -> bool:
		"""Indica si hay una tarea en curso con esa clave."""
		with self._cerrojo:
			return clave in self._por_clave

	def tareas_activas(self) -> list[Tarea]:
		"""Tareas pendientes o en curso (para mostrar o anunciar qué se está haciendo)."""
		with self._cerrojo:
			return list(self._activas.values())

	def estadisticas(self) -> dict[str, float]:
		"""Contadores del gestor: enviadas, completadas, fallidas, canceladas, vencidas,
		rechazadas, unificadas, activas, en_ejecucion, duracion_media y duracion_maxima."""
		with self._cerrojo:
			datos: dict[str, float] = dict(self._estadisticas)
			datos["activas"] = len(self._activas)
			datos["en_ejecucion"] = len(self._en_ejecucion)
			suma, maximo, cuantas = self._duraciones
			datos["duracion_media"] = round(suma / cuantas, 4) if cuantas else 0.0
			datos["duracion_maxima"] = round(maximo, 4)
		return datos

	def apagar(self, espera: float = 3.0) -> bool:
		"""Cierre ordenado: no acepta tareas nuevas, cancela las pendientes y en curso y
		espera como mucho ``espera`` segundos a que terminen.

		Tras apagar ya no se entregan respuestas a la interfaz: las ventanas se están
		destruyendo. Devuelve ``True`` si no quedó ninguna tarea viva; si quedó alguna,
		la registra por su nombre (es la pista para buscar un bucle sin ``comprobar()``
		o una lectura de red sin tiempo de espera).
		"""
		with self._cerrojo:
			self._cerrado = True
			self._entregar_activo = False
			tareas = list(self._activas.values())
		# Primero se vacía la cola y después se cancela lo que está en marcha: al
		# revés, un hilo que queda libre al cancelar llegaba a coger una pendiente.
		self._grupo.shutdown(wait=False, cancel_futures=True)
		for tarea in tareas:
			self.cancelar(tarea, _("La aplicación se está cerrando"))
		limite = time.monotonic() + max(0.0, espera)
		with self._condicion:
			# También se espera a las que siguen activas sin haberse marcado aún como
			# «en ejecución» (un hilo puede haberlas sacado de la cola en ese instante).
			while self._en_ejecucion or self._activas:
				restante = limite - time.monotonic()
				if restante <= 0:
					break
				self._condicion.wait(min(restante, 0.05))
			vivas = sorted(t.nombre for t in self._en_ejecucion)
			self._plazos.clear()
			self._condicion.notify_all()
		if vivas:
			log.warning(
				"Al cerrar, %d tarea(s) siguen ejecutándose tras %.1f s: %s. Revisa que comprueben "
				"la cancelación y que sus operaciones de red tengan tiempo de espera.",
				len(vivas), espera, ", ".join(vivas[:10]),
			)
			return False
		log.debug("Gestor de tareas «%s» apagado sin tareas pendientes.", self.nombre)
		return True

	# ── Interno ────────────────────────────────────────────────────────────

	@staticmethod
	def _anadir_respuestas(
		tarea: Tarea,
		al_terminar: Callable[[Any], Any] | None,
		al_fallar: Callable[[BaseException], Any] | None,
		al_progresar: Callable[[Progreso], Any] | None,
	) -> None:
		if al_terminar is not None:
			tarea._al_terminar.append(al_terminar)
		if al_fallar is not None:
			tarea._al_fallar.append(al_fallar)
		if al_progresar is not None:
			tarea._al_progresar.append(al_progresar)

	def _ejecutar(self, tarea: Tarea) -> None:
		"""Cuerpo que corre en el hilo de trabajo."""
		hilo = threading.current_thread()
		nombre_hilo = hilo.name
		with self._cerrojo:
			if tarea.terminada:
				return
			tarea._estado = "en_curso"
			tarea.inicio = time.monotonic()
			self._en_ejecucion.add(tarea)
			if tarea.tiempo_maximo:
				self._programar_plazo(tarea)
		# El nombre de la tarea en el del hilo aparece en cada línea del registro y en
		# el volcado de pilas del vigilante de congelaciones.
		hilo.name = f"{nombre_hilo}:{tarea.nombre}"[:80]
		try:
			if tarea.token.cancelado:
				raise TareaCancelada(tarea.token.motivo)
			resultado = tarea._funcion(tarea, *tarea._args)
		except TareaCancelada as error:
			self._finalizar(tarea, "cancelada", error=error)
		except BaseException as error:  # noqa: BLE001 - ningún error se pierde en un hilo
			if isinstance(error, TiempoAgotado):
				self._finalizar(tarea, "vencida", error=error)
			elif tarea.token.cancelado:
				# Falló al detenerse: la causa real es la cancelación; se anota por si acaso.
				log.debug("La tarea «%s» falló mientras se cancelaba.", tarea.nombre, exc_info=error)
				self._finalizar(tarea, "cancelada", error=TareaCancelada(tarea.token.motivo))
			else:
				self._registrar_error(tarea, error)
				self._finalizar(tarea, "fallida", error=error)
		else:
			if tarea.token.cancelado:
				# El usuario ya oyó «cancelando»: la cancelación gana al resultado.
				log.debug("La tarea «%s» terminó tras pedir su cancelación; se descarta el resultado.", tarea.nombre)
				self._finalizar(tarea, "cancelada", error=TareaCancelada(tarea.token.motivo))
			else:
				self._finalizar(tarea, "completada", resultado=resultado)
		finally:
			hilo.name = nombre_hilo
			with self._condicion:
				self._en_ejecucion.discard(tarea)
				self._condicion.notify_all()

	def _si_futuro_cancelado(self, futuro: Future, tarea: Tarea) -> None:
		"""Finaliza las tareas que se cancelaron antes de empezar (nunca llegan a ``_ejecutar``)."""
		if futuro.cancelled():
			self._finalizar(tarea, "cancelada", error=TareaCancelada(tarea.token.motivo or _("Cancelada")))

	def _finalizar(
		self, tarea: Tarea, estado: str, resultado: Any = None, error: BaseException | None = None
	) -> bool:
		"""Fija el resultado UNA sola vez y entrega la respuesta. Devuelve si fue esta llamada."""
		with self._condicion:
			if tarea._finalizada:
				if estado != "vencida":
					log.debug("La tarea «%s» terminó (%s) después de vencer; se ignora.", tarea.nombre, estado)
				return False
			tarea._finalizada = True
			tarea._estado = estado
			tarea.resultado = resultado
			tarea.error = error
			tarea.fin = time.monotonic()
			self._activas.pop(tarea.id, None)
			if tarea.clave is not None and self._por_clave.get(tarea.clave) is tarea:
				del self._por_clave[tarea.clave]
			self._estadisticas[{"completada": "completadas", "fallida": "fallidas", "cancelada": "canceladas",
				"vencida": "vencidas"}[estado]] += 1
			if tarea.inicio is not None:
				duracion = tarea.fin - tarea.inicio
				self._duraciones[0] += duracion
				self._duraciones[1] = max(self._duraciones[1], duracion)
				self._duraciones[2] += 1
			respuestas = list(tarea._al_terminar) if estado == "completada" else list(tarea._al_fallar)
			self._condicion.notify_all()
		argumento = resultado if estado == "completada" else error
		for respuesta in respuestas:
			self._entregar(respuesta, argumento)
		tarea._final.set()
		return True

	def _entregar(self, funcion: Callable[..., Any], *args: Any) -> None:
		"""Lleva una respuesta al hilo de la interfaz, registrando cualquier fallo."""
		if not self._entregar_activo:
			log.debug("Respuesta %s no entregada: el gestor se está apagando.", _nombre_de(funcion))
			return

		def segura() -> None:
			try:
				funcion(*args)
			except Exception:  # noqa: BLE001 - un fallo de la interfaz no debe perderse
				log.exception("Falló la respuesta %s de una tarea en segundo plano.", _nombre_de(funcion))

		try:
			self._ejecutor_interfaz(segura)
		except Exception:  # noqa: BLE001 - p. ej. wx ya destruido durante el cierre
			log.warning("No se pudo entregar %s al hilo de la interfaz.", _nombre_de(funcion), exc_info=True)

	def _registrar_error(self, tarea: Tarea, error: BaseException) -> None:
		"""ERROR con traza para fallos reales; una línea sin traza para los transitorios de red."""
		if not es_error_transitorio(error):
			log.error("La tarea «%s» falló: %s", tarea.nombre, _resumen(error), exc_info=error)
			return
		clave = (tarea.nombre, _resumen(error, 120))
		ahora = time.monotonic()
		with self._cerrojo_avisos:
			if len(self._avisos) > 500:
				self._avisos.clear()
			previo = self._avisos.get(clave)
			if previo is not None and ahora - previo[0] < self._ventana_avisos:
				previo[1] += 1
				escribir, repeticiones = False, 0
			else:
				repeticiones = int(previo[1]) if previo else 0
				self._avisos[clave] = [ahora, 0]
				escribir = True
		if escribir:
			sufijo = f" (se repitió {repeticiones} veces desde el aviso anterior)" if repeticiones else ""
			log.warning("La tarea «%s» tuvo un fallo transitorio de red: %s%s", tarea.nombre, _resumen(error), sufijo)
		# La traza queda solo en DEBUG: útil al depurar, sin llenar incidencias.log.
		log.debug("Detalle del fallo transitorio de «%s».", tarea.nombre, exc_info=error)

	def _programar_plazo(self, tarea: Tarea) -> None:
		"""Anota el vencimiento de la tarea (con el cerrojo tomado) y despierta al vigilante."""
		heapq.heappush(self._plazos, (time.monotonic() + float(tarea.tiempo_maximo or 0), tarea.id, tarea))
		if self._hilo_plazos is None:
			self._hilo_plazos = threading.Thread(target=self._vigilar_plazos, name=f"{self.nombre}-plazos", daemon=True)
			self._hilo_plazos.start()
		self._condicion.notify_all()

	def _vigilar_plazos(self) -> None:
		"""Un único hilo vigila todos los tiempos máximos (en vez de un temporizador por tarea)."""
		while True:
			with self._condicion:
				vencidas: list[Tarea] = []
				while not vencidas:
					if self._cerrado and not self._plazos:
						return
					ahora = time.monotonic()
					while self._plazos and self._plazos[0][0] <= ahora:
						tarea = heapq.heappop(self._plazos)[2]
						if not tarea.terminada:
							vencidas.append(tarea)
					if not vencidas:
						self._condicion.wait(self._plazos[0][0] - ahora if self._plazos else None)
			for tarea in vencidas:
				self._vencer(tarea)

	def _vencer(self, tarea: Tarea) -> None:
		"""Da la tarea por vencida: la interfaz recibe ``TiempoAgotado`` ya y se pide que pare."""
		error = TiempoAgotado(
			_("La operación «{nombre}» superó el tiempo máximo de {segundos:g} s.").format(
				nombre=tarea.nombre, segundos=tarea.tiempo_maximo or 0
			)
		)
		tarea.token.cancelar(_("Tiempo agotado"))
		if self._finalizar(tarea, "vencida", error=error):
			log.warning(
				"La tarea «%s» superó su tiempo máximo (%.1f s); se ha pedido que se detenga.",
				tarea.nombre, tarea.tiempo_maximo or 0,
			)

