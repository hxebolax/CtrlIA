"""Sistema de registro (logging) profesional para aplicaciones wxPython portables.

Objetivo: que CUALQUIER problema quede escrito con el lugar exacto donde se
produjo (archivo, línea, función, hilo y traza completa) para poder corregirlo
rápido, sin frenar la interfaz ni exponer datos privados.

Qué hace ``configurar_registro()``:

- Escribe en ``data/logs`` (portable, junto a la aplicación):
	- ``sesion_AAAAMMDD_HHMMSS.log``: registro legible de esta sesión.
	- ``sesion_AAAAMMDD_HHMMSS.jsonl``: el mismo registro, un JSON por línea,
	  para filtrarlo o analizarlo con herramientas.
	- ``incidencias.log``: SOLO avisos y errores (WARNING o superior) de todas
	  las sesiones, con rotación por tamaño. Es el primer sitio donde mirar.
	- ``fallo_nativo.log``: volcado de ``faulthandler`` si el proceso muere por
	  un fallo de C (wxWidgets, una DLL...), que el logging normal no ve.
	- ``arranque_fallido.log``: si falla la propia configuración del registro.
	- ``no_controladas/fallo_*.log``: cada excepción no controlada, escrita al
	  instante y sin cola (sobrevive aunque el proceso muera justo después).
	- ``sesion.en_curso``: marca que se borra al cerrar bien; si al arrancar
	  sigue ahí, la sesión anterior terminó de forma anómala y se avisa.
	- ``*.errores_registro``: fallos del propio sistema de registro (disco
	  lleno, archivo bloqueado...), que no pueden escribirse en el registro.
- Conserva las últimas ``sesiones_conservadas`` sesiones y borra las antiguas.
- Escribe en un HILO APARTE (``QueueHandler`` + ``QueueListener``): el hilo de
  la interfaz nunca espera al disco, así el lector de pantalla no se queda mudo.
- Captura lo no controlado: ``sys.excepthook`` (incluidas las excepciones de
  los manejadores de eventos de wx), ``threading.excepthook``,
  ``sys.unraisablehook``, ``warnings`` y, en la versión compilada sin consola,
  todo lo que se escriba en ``sys.stdout``/``sys.stderr``.
- Añade a cada excepción su ORIGEN real (el marco donde se lanzó y el último
  marco del código propio), no solo la línea que la registró.
- Redacta secretos (tokens, contraseñas, cabeceras Authorization/Cookie,
  credenciales en URLs...) en mensajes y trazas.
- Resume las repeticiones: un mismo mensaje en bucle no llena el disco.
- Añade contexto de operación (``with operacion("descargar"):``) que viaja con
  cada línea y mide duraciones.

Uso mínimo en ``main.py`` (ANTES de importar wx y el resto de la aplicación):

	from app.core import registro
	registro.configurar_registro(nombre_app="MiAplicacion", version="1.0.0")

PLANTILLA del agente «logging-profesional». Solo usa la biblioteca estándar.
"""

from __future__ import annotations

import atexit
import contextlib
import contextvars
import copy
import faulthandler
import functools
import io
import itertools
import json
import logging
import logging.handlers
import os
import platform
import queue
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

# ════════════════════════════════════════════════════════════════════════════
# Constantes y estado del módulo
# ════════════════════════════════════════════════════════════════════════════

# Variable de entorno que fuerza el modo depuración (la pone entorno.bat, opción
# «Ejecutar con consola»): nivel DEBUG y copia de los mensajes en la consola.
VARIABLE_DEPURACION = "APP_DEPURACION"

# Nombre del hilo que escribe en disco (se usa para evitar bucles, ver _FlujoARegistro).
_NOMBRE_HILO_ESCRITOR = "registro-escritor"

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# Bibliotecas muy verbosas: se dejan en WARNING salvo que se pida lo contrario.
BIBLIOTECAS_RUIDOSAS = (
	"urllib3", "requests", "httpx", "httpcore", "asyncio", "charset_normalizer",
	"PIL", "chardet", "comtypes", "hpack", "websockets", "peewee", "bs4", "lxml",
)

# Atributos estándar de LogRecord: todo lo demás es «extra» y va al JSON.
_ATRIBUTOS_ESTANDAR = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
	"message", "asctime", "ubicacion", "ruta_rel", "origen", "origen_app", "operacion",
	"sesion", "exc_tipo", "exc_mensaje", "taskName",
}

_operacion_actual: contextvars.ContextVar[str] = contextvars.ContextVar("operacion", default="")
_contador_operaciones = itertools.count(1)

_estado: dict[str, Any] = {
	"configurado": False,
	"oyente": None,           # QueueListener
	"manejador_cola": None,   # QueueHandler instalado en la raíz
	"manejadores": [],        # manejadores de archivo (para cambiar nivel/cerrar)
	"carpeta": None,          # Path de data/logs
	"archivo_sesion": None,   # Path del .log de esta sesión
	"archivo_fallo_nativo": None,  # archivo abierto para faulthandler
	"raiz_proyecto": None,    # para calcular rutas relativas
	"sesion": "",
	"avisador": None,         # función de la interfaz para mostrar errores graves
	"stdout_original": None,
	"stderr_original": None,
}


# ════════════════════════════════════════════════════════════════════════════
# Redacción de datos sensibles
# ════════════════════════════════════════════════════════════════════════════

_MARCA = "***"
_secretos: set[str] = set()
_candado_secretos = threading.Lock()

# Nombres de campos cuyo VALOR se oculta (en key=valor, key: valor, JSON y query strings).
_CAMPOS_SENSIBLES = (
	r"password|passwd|pwd|contrase(?:ñ|n)a|clave|secret|secreto|token|access_token|refresh_token"
	r"|id_token|api[_-]?key|apikey|client_secret|private[_-]?key|session(?:id)?|cookie|auth|signature|sig"
)
_REDACCIONES: list[tuple[re.Pattern[str], str]] = [
	# Cabeceras completas hasta fin de línea (Bearer <token>, varias cookies con ;).
	(re.compile(r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie)(\s*[:=]\s*)[^\r\n]+"), r"\1\2" + _MARCA),
	# Credenciales incrustadas en una URL: esquema://usuario:contraseña@host
	(re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + _MARCA + ":" + _MARCA + "@"),
	# Parámetros sensibles en query strings: ?token=abc&x=1
	(re.compile(r"(?i)([?&;](?:" + _CAMPOS_SENSIBLES + r")=)[^&#\s\"']+"), r"\1" + _MARCA),
	# JSON / diccionarios: "password": "valor"  o  'token': 'valor'
	(re.compile(r"(?i)([\"'](?:" + _CAMPOS_SENSIBLES + r")[\"']\s*:\s*)([\"'])(?:(?!\2).)*\2"), r"\1\2" + _MARCA + r"\2"),
	# Tokens JWT sueltos (eyJ...): van en cabeceras, URLs y mensajes de error.
	(re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), _MARCA),
	# Texto libre: password=valor, token: valor, clave = "valor"
	(re.compile(r"(?i)\b((?:" + _CAMPOS_SENSIBLES + r")\s*[:=]\s*)([\"']?)[^\s\"',;&]+\2"), r"\1\2" + _MARCA + r"\2"),
]
# Comprobación barata antes de lanzar las expresiones regulares (casi ninguna
# línea contiene nada sensible; así no se paga el coste en el caso común).
_PISTAS_SENSIBLES = (
	"://", "pass", "contrase", "clave", "secret", "token", "key", "auth", "cookie", "session", "sig", "eyj",
)


def registrar_secreto(valor: str | None) -> None:
	"""Registra un secreto concreto (token, contraseña) para ocultarlo SIEMPRE.

	Llamarlo en cuanto se lee o se recibe el secreto, antes de que ningún
	mensaje pueda contenerlo. Los valores muy cortos se ignoran para no
	destrozar mensajes normales.
	"""
	if valor and len(valor) >= 4:
		with _candado_secretos:
			_secretos.add(valor)


def redactar(texto: str) -> str:
	"""Devuelve ``texto`` con los secretos registrados y los patrones sensibles ocultos."""
	if not texto:
		return texto
	with _candado_secretos:
		secretos = sorted(_secretos, key=len, reverse=True)
	for secreto in secretos:
		if secreto in texto:
			texto = texto.replace(secreto, _MARCA)
	bajo = texto.lower()
	if any(pista in bajo for pista in _PISTAS_SENSIBLES):
		for patron, reemplazo in _REDACCIONES:
			texto = patron.sub(reemplazo, texto)
	return texto


def _neutralizar_saltos(texto: str) -> str:
	"""Evita la inyección de líneas falsas en el registro (CRLF de datos externos).

	Solo se aplica al mensaje; las trazas conservan sus saltos de línea.
	"""
	return texto.replace("\r\n", "\n").replace("\r", "\\r")


# ════════════════════════════════════════════════════════════════════════════
# Filtros: añaden contexto y origen ANTES de pasar el registro a la cola
# ════════════════════════════════════════════════════════════════════════════

_MARCAS_AJENAS = ("/site-packages/", "/.venv/", "/venv/", "/lib/python", "/Lib/", "<frozen", "<string>")


def _relativa_a_paquete_propio(normal: str) -> str | None:
	"""Si la ruta contiene un paquete propio (``app/...``), la devuelve desde ahí.

	En la versión compilada, las rutas de las trazas son las de la máquina
	donde se compiló (o relativas si setup_cx.py usa ``replace_paths``), así
	que no cuelgan de la carpeta del ejecutable: se reconocen por el paquete.
	"""
	partes = normal.split("/")
	for indice, parte in enumerate(partes[:-1]):
		if parte in _estado.get("paquetes_propios", ("app",)):
			return "/".join(partes[indice:])
	if partes and partes[-1] in ("main.py", "__main__.py") and len(partes) <= 2:
		return partes[-1]
	return None


def _ruta_relativa(ruta: str) -> str:
	"""Ruta del archivo relativa al proyecto (o abreviada si es de una biblioteca)."""
	if not ruta:
		return "?"
	raiz = _estado.get("raiz_proyecto")
	if raiz is not None and Path(ruta).is_absolute():
		try:
			return Path(ruta).resolve().relative_to(raiz).as_posix()
		except (ValueError, OSError):
			pass
	normal = ruta.replace("\\", "/")
	if not any(marca in normal for marca in _MARCAS_AJENAS):
		propia = _relativa_a_paquete_propio(normal)
		if propia:
			return propia
	# Biblioteca externa: se abrevia desde site-packages o lib.
	for marca in ("/site-packages/", "/lib/", "/Lib/"):
		if marca in normal:
			return normal.split(marca, 1)[1]
	return normal.rsplit("/", 1)[-1]


def _es_codigo_propio(ruta: str) -> bool:
	"""Indica si un archivo pertenece al proyecto (no a Python ni a bibliotecas)."""
	if not ruta:
		return False
	normal = ruta.replace("\\", "/")
	if any(marca in normal for marca in _MARCAS_AJENAS):
		return False
	raiz = _estado.get("raiz_proyecto")
	if raiz is not None and Path(ruta).is_absolute():
		try:
			Path(ruta).resolve().relative_to(raiz)
			return True
		except (ValueError, OSError):
			pass
	return _relativa_a_paquete_propio(normal) is not None


def origen_de_excepcion(exc: BaseException | None) -> tuple[str, str]:
	"""Devuelve (origen, origen_app) de una excepción.

	- ``origen``: el marco más profundo, donde realmente se lanzó.
	- ``origen_app``: el marco más profundo que pertenece al código propio. Si
	  el fallo ocurre dentro de una biblioteca, dice qué línea NUESTRA la llamó.

	Recorre también la cadena de causas (``raise ... from``) para señalar el
	fallo original.
	"""
	if exc is None:
		return "", ""
	raiz_causa = exc
	vistos: set[int] = set()
	while (siguiente := raiz_causa.__cause__ or raiz_causa.__context__) is not None and id(siguiente) not in vistos:
		vistos.add(id(raiz_causa))
		if raiz_causa.__suppress_context__ and raiz_causa.__cause__ is None:
			break
		raiz_causa = siguiente
	marcos = traceback.extract_tb(raiz_causa.__traceback__) if raiz_causa.__traceback__ else []
	if not marcos:
		return "", ""
	ultimo = marcos[-1]
	origen = f"{_ruta_relativa(ultimo.filename)}:{ultimo.lineno} en {ultimo.name}"
	origen_app = ""
	for marco in reversed(marcos):
		if _es_codigo_propio(marco.filename):
			origen_app = f"{_ruta_relativa(marco.filename)}:{marco.lineno} en {marco.name}"
			break
	return origen, origen_app


class FiltroContexto(logging.Filter):
	"""Añade al registro: ruta relativa, ubicación, operación, sesión y origen de la excepción."""

	def filter(self, record: logging.LogRecord) -> bool:
		record.ruta_rel = _ruta_relativa(record.pathname)
		record.ubicacion = f"{record.ruta_rel}:{record.lineno} en {record.funcName}"
		record.operacion = _operacion_actual.get()
		record.sesion = _estado.get("sesion", "")
		record.origen = ""
		record.origen_app = ""
		record.exc_tipo = ""
		record.exc_mensaje = ""
		if record.exc_info and record.exc_info[1] is not None:
			exc = record.exc_info[1]
			record.origen, record.origen_app = origen_de_excepcion(exc)
			record.exc_tipo = type(exc).__qualname__
			record.exc_mensaje = str(exc)
		return True


class FiltroRepeticiones(logging.Filter):
	"""Resume los mensajes idénticos que se repiten en ráfaga.

	Deja pasar las ``maximo`` primeras apariciones de un mismo mensaje (mismo
	logger, nivel, línea y texto) dentro de ``ventana`` segundos; el resto se
	cuentan y, cuando vuelve a aparecer pasada la ventana, se indica cuántas
	se omitieron. Evita que un error en un bucle llene el disco y entierre los
	demás. Los CRITICAL nunca se omiten.
	"""

	def __init__(self, maximo: int = 5, ventana: float = 60.0) -> None:
		super().__init__()
		self.maximo = maximo
		self.ventana = ventana
		self._vistos: dict[tuple, list[float | int]] = {}
		self._candado = threading.Lock()

	def filter(self, record: logging.LogRecord) -> bool:
		if record.levelno >= logging.CRITICAL:
			return True
		clave = (record.name, record.levelno, record.pathname, record.lineno, str(record.msg))
		ahora = time.monotonic()
		with self._candado:
			inicio, cuenta, omitidos = self._vistos.get(clave, (ahora, 0, 0))
			if ahora - inicio > self.ventana:
				if omitidos:
					record.msg = f"{record.msg} [se omitieron {omitidos} repeticiones en {self.ventana:.0f} s]"
				self._vistos[clave] = [ahora, 1, 0]
				return True
			cuenta += 1
			if cuenta <= self.maximo:
				self._vistos[clave] = [inicio, cuenta, omitidos]
				if cuenta == self.maximo:
					record.msg = f"{record.msg} [se repite; se omitirán más copias durante {self.ventana:.0f} s]"
				return True
			self._vistos[clave] = [inicio, cuenta, omitidos + 1]
			if len(self._vistos) > 5000:
				self._vistos.clear()
			return False


# ════════════════════════════════════════════════════════════════════════════
# Cola: el hilo que registra no espera al disco
# ════════════════════════════════════════════════════════════════════════════

class ManejadorCola(logging.handlers.QueueHandler):
	"""QueueHandler que conserva la traza como texto y redacta antes de encolar.

	El ``prepare`` estándar mezcla la traza dentro del mensaje; aquí se deja
	en ``exc_text`` para que el formato JSON la guarde en su propio campo. La
	cola es del mismo proceso, así que no hace falta que el registro sea
	serializable, pero sí se sueltan ``exc_info`` y los argumentos para no
	retener marcos de pila (memoria) ni objetos mutables.
	"""

	_formateador_trazas = logging.Formatter()

	def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
		registro = copy.copy(record)
		try:
			mensaje = record.getMessage()
		except Exception as error:  # noqa: BLE001 - un mensaje mal formado no debe perderse
			mensaje = f"{record.msg!r} (argumentos {record.args!r}: {error})"
		registro.msg = _neutralizar_saltos(redactar(mensaje))
		registro.args = None
		if record.exc_info:
			registro.exc_text = redactar(self._formateador_trazas.formatException(record.exc_info))
		elif record.exc_text:
			registro.exc_text = redactar(record.exc_text)
		registro.exc_info = None
		if record.stack_info:
			registro.stack_info = redactar(record.stack_info)
		if getattr(registro, "exc_mensaje", ""):
			registro.exc_mensaje = redactar(registro.exc_mensaje)
		return registro


# ════════════════════════════════════════════════════════════════════════════
# Formatos
# ════════════════════════════════════════════════════════════════════════════

class FormatoLegible(logging.Formatter):
	"""Formato para personas:

	2026-09-25 14:03:22.517 ERROR    [MainThread] app.ui.ventana (app/ui/ventana.py:123 en al_guardar) {guardar#4} | Mensaje
	    Origen: app/core/datos.py:88 en escribir  ·  Código propio: app/ui/ventana.py:130 en al_guardar
	    Traceback (most recent call last): ...
	"""

	def format(self, record: logging.LogRecord) -> str:
		fecha = datetime.fromtimestamp(record.created).strftime(FORMATO_FECHA)
		ubicacion = getattr(record, "ubicacion", f"{record.filename}:{record.lineno} en {record.funcName}")
		operacion = getattr(record, "operacion", "")
		partes = [
			f"{fecha}.{int(record.msecs):03d} {record.levelname:<8} [{record.threadName}] {record.name}",
			f"({ubicacion})",
		]
		if operacion:
			partes.append(f"{{{operacion}}}")
		mensaje = record.getMessage()
		if "\n" in mensaje:
			# Las líneas siguientes se sangran: ninguna línea de un mensaje puede
			# hacerse pasar por una entrada nueva (esas empiezan siempre por la fecha).
			primera, resto = mensaje.split("\n", 1)
			mensaje = primera + "\n" + _sangrar(resto)
		linea = " ".join(partes) + " | " + mensaje
		origen = getattr(record, "origen", "")
		origen_app = getattr(record, "origen_app", "")
		if origen:
			detalle = f"    Origen: {origen}"
			if origen_app and origen_app != origen:
				detalle += f"  ·  Código propio: {origen_app}"
			linea += "\n" + detalle
		if record.exc_text:
			linea += "\n" + _sangrar(record.exc_text)
		if record.stack_info:
			linea += "\n" + _sangrar(record.stack_info)
		return linea


class FormatoJSON(logging.Formatter):
	"""Un objeto JSON por línea (JSON Lines), fácil de filtrar y analizar."""

	def format(self, record: logging.LogRecord) -> str:
		datos: dict[str, Any] = {
			"ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
			"nivel": record.levelname,
			"logger": record.name,
			"mensaje": record.getMessage(),
			"archivo": getattr(record, "ruta_rel", record.filename),
			"linea": record.lineno,
			"funcion": record.funcName,
			"hilo": record.threadName,
			"proceso": record.process,
			"sesion": getattr(record, "sesion", ""),
		}
		if getattr(record, "operacion", ""):
			datos["operacion"] = record.operacion
		if record.exc_text:
			datos["excepcion"] = {
				"tipo": getattr(record, "exc_tipo", ""),
				"mensaje": getattr(record, "exc_mensaje", ""),
				"origen": getattr(record, "origen", ""),
				"origen_app": getattr(record, "origen_app", ""),
				"traza": record.exc_text,
			}
		if record.stack_info:
			datos["pila"] = record.stack_info
		extra = {
			clave: valor for clave, valor in vars(record).items()
			if clave not in _ATRIBUTOS_ESTANDAR and not clave.startswith("_")
		}
		if extra:
			datos["extra"] = extra
		return json.dumps(datos, ensure_ascii=False, default=_serializar)


def _serializar(valor: Any) -> str:
	"""Convierte a texto (redactado) lo que json no sabe serializar."""
	return redactar(repr(valor))


def _sangrar(texto: str, sangria: str = "    ") -> str:
	"""Sangra cada línea para que las trazas se distingan del resto al leer."""
	return "\n".join(sangria + linea for linea in texto.rstrip("\n").splitlines())


# ════════════════════════════════════════════════════════════════════════════
# Captura de lo no controlado
# ════════════════════════════════════════════════════════════════════════════

_log_capturas = logging.getLogger("app.no_controlado")


def _hook_excepcion(tipo, valor, rastro) -> None:
	"""sys.excepthook: excepciones no capturadas del hilo principal y de wx.

	En wxPython, una excepción dentro de un manejador de eventos no cierra la
	aplicación: wx la imprime con este mismo gancho y sigue. Sin él, en la
	versión compilada (sin consola) el error desaparecería sin dejar rastro.
	"""
	if issubclass(tipo, KeyboardInterrupt):
		sys.__excepthook__(tipo, valor, rastro)
		return
	_log_capturas.critical("Excepción no controlada: %s: %s", tipo.__name__, valor, exc_info=(tipo, valor, rastro))
	_escribir_fallo_inmediato("Excepción no controlada", tipo, valor, rastro)
	_avisar_interfaz(tipo, valor, rastro)


def _hook_hilo(argumentos: threading.ExceptHookArgs) -> None:
	"""threading.excepthook: excepciones que matan un hilo secundario."""
	if argumentos.exc_type is SystemExit:
		return
	nombre = argumentos.thread.name if argumentos.thread else "desconocido"
	_log_capturas.error(
		"Excepción no controlada en el hilo %r: %s: %s", nombre,
		argumentos.exc_type.__name__, argumentos.exc_value,
		exc_info=(argumentos.exc_type, argumentos.exc_value, argumentos.exc_traceback),
	)
	_escribir_fallo_inmediato(
		f"Excepción no controlada en el hilo {nombre!r}",
		argumentos.exc_type, argumentos.exc_value, argumentos.exc_traceback,
	)


def _hook_no_lanzable(argumentos: Any) -> None:
	"""sys.unraisablehook: errores en __del__, recolector de basura, finalizadores..."""
	exc = argumentos.exc_value
	objeto = getattr(argumentos, "object", None)
	_log_capturas.error(
		"Excepción imposible de lanzar (%s) en %r",
		getattr(argumentos, "err_msg", None) or "error ignorado", objeto,
		exc_info=(argumentos.exc_type, exc, argumentos.exc_traceback) if exc is not None else None,
	)


def _escribir_fallo_inmediato(titulo: str, tipo, valor, rastro, conservar: int = 20) -> None:
	"""Escribe la excepción en ``no_controladas/`` SIN pasar por la cola.

	Si el proceso muere justo después (fallo en el hilo principal fuera del
	bucle de wx), la cola podría no llegar a vaciarse; este archivo sí queda.
	"""
	carpeta = _estado.get("carpeta")
	if carpeta is None:
		return
	try:
		destino = carpeta / "no_controladas"
		destino.mkdir(exist_ok=True)
		ruta = destino / f"fallo_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
		origen, origen_app = origen_de_excepcion(valor)
		cuerpo = "".join(traceback.format_exception(tipo, valor, rastro))
		ruta.write_text(
			redactar(
				f"{titulo}\nSesión: {_estado.get('sesion')} · Hilo: {threading.current_thread().name}\n"
				f"Origen: {origen}\nCódigo propio: {origen_app}\n\n{cuerpo}"
			),
			encoding="utf-8",
		)
		for antiguo in sorted(destino.glob("fallo_*.log"))[:-conservar]:
			antiguo.unlink(missing_ok=True)
	except OSError:
		pass


def manejador_excepciones_asyncio(bucle: Any, contexto: dict[str, Any]) -> None:
	"""Úselo con ``bucle.set_exception_handler(manejador_excepciones_asyncio)``."""
	exc = contexto.get("exception")
	mensaje = contexto.get("message", "Error en el bucle asyncio")
	logging.getLogger("app.asyncio").error(
		"%s (tarea: %r)", mensaje, contexto.get("task") or contexto.get("future"),
		exc_info=(type(exc), exc, exc.__traceback__) if exc else None,
	)


class _FlujoARegistro(io.TextIOBase):
	"""Sustituye a sys.stdout/sys.stderr cuando no hay consola (compilada con base gui).

	Con cx_Freeze y ``base="gui"`` ambos valen ``None`` y cualquier ``print`` o
	biblioteca que escriba en ellos fallaría o se perdería. Aquí cada línea
	completa se convierte en un mensaje de registro.
	"""

	def __init__(self, nombre: str, nivel: int) -> None:
		super().__init__()
		self._logger = logging.getLogger(nombre)
		self._nivel = nivel
		self._pendiente = ""
		self._candado = threading.Lock()

	def writable(self) -> bool:
		return True

	def write(self, texto: str) -> int:
		# Lo que escriba el propio hilo escritor (errores del logging) se descarta
		# para no crear un bucle infinito disco lleno → error → registro → disco lleno.
		if threading.current_thread().name == _NOMBRE_HILO_ESCRITOR:
			return len(texto)
		with self._candado:
			self._pendiente += texto
			*lineas, self._pendiente = self._pendiente.split("\n")
		for linea in lineas:
			if linea.strip():
				self._logger.log(self._nivel, linea.rstrip())
		return len(texto)

	def flush(self) -> None:
		with self._candado:
			resto, self._pendiente = self._pendiente, ""
		if resto.strip():
			self._logger.log(self._nivel, resto.rstrip())

	@property
	def encoding(self) -> str:  # type: ignore[override]
		return "utf-8"

	def isatty(self) -> bool:
		return False


def _avisar_interfaz(tipo, valor, rastro) -> None:
	"""Pide a la interfaz (si registró un avisador) que informe de forma accesible."""
	avisador = _estado.get("avisador")
	if avisador is None:
		return
	try:
		avisador(tipo, valor, rastro, _estado.get("archivo_sesion"))
	except Exception:  # noqa: BLE001 - el aviso nunca debe provocar otro error no controlado
		_log_capturas.debug("El avisador de la interfaz falló.", exc_info=True)


def establecer_avisador(funcion: Callable[..., None] | None) -> None:
	"""Registra la función de la interfaz que muestra los errores graves.

	Recibe ``(tipo, valor, rastro, ruta_registro)``; puede llamarse desde
	cualquier hilo, así que debe usar ``wx.CallAfter``. Ver
	``app/ui/dialogo_error.py``.
	"""
	_estado["avisador"] = funcion


def _instalar_capturas() -> None:
	sys.excepthook = _hook_excepcion
	threading.excepthook = _hook_hilo
	sys.unraisablehook = _hook_no_lanzable
	logging.captureWarnings(True)


def _redirigir_flujos_sin_consola() -> None:
	"""Redirige stdout/stderr al registro si la aplicación no tiene consola."""
	# En una reconfiguración, sys.stderr ya es nuestro flujo: no se toma como
	# «original» (la consola escribiría en el registro y este en la consola...).
	if not isinstance(sys.stdout, _FlujoARegistro):
		_estado["stdout_original"] = sys.stdout
	if not isinstance(sys.stderr, _FlujoARegistro):
		_estado["stderr_original"] = sys.stderr
	if sys.stdout is None:
		sys.stdout = _FlujoARegistro("app.stdout", logging.INFO)
	if sys.stderr is None:
		sys.stderr = _FlujoARegistro("app.stderr", logging.WARNING)
		# Sin consola real, un fallo del propio logging no tiene dónde mostrarse.
		logging.raiseExceptions = False


def _activar_faulthandler(carpeta: Path) -> None:
	"""Vuelca la pila de todos los hilos si el proceso muere por un fallo nativo.

	El archivo debe quedar ABIERTO toda la vida del proceso: faulthandler
	escribe en su descriptor en el momento del fallo, sin pasar por Python.
	"""
	try:
		archivo = open(carpeta / "fallo_nativo.log", "a", encoding="utf-8")  # noqa: SIM115 - debe seguir abierto
		archivo.write(f"\n=== Sesión {_estado['sesion']} (pid {os.getpid()}) ===\n")
		archivo.flush()
		faulthandler.enable(file=archivo, all_threads=True)
		_estado["archivo_fallo_nativo"] = archivo
	except OSError:
		logging.getLogger(__name__).warning("No se pudo activar faulthandler.", exc_info=True)


# ════════════════════════════════════════════════════════════════════════════
# Configuración
# ════════════════════════════════════════════════════════════════════════════

def _raiz_proyecto() -> Path:
	"""Raíz del programa: carpeta del .exe (compilada) o de main.py (código)."""
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent
	principal = sys.modules.get("__main__")
	archivo = getattr(principal, "__file__", None)
	if archivo:
		return Path(archivo).resolve().parent
	# <raíz>/app/core/registro.py
	return Path(__file__).resolve().parent.parent.parent


def _carpeta_predeterminada() -> Path:
	"""``data/logs`` junto al programa, respetando APP_DATA_DIR (pruebas)."""
	redireccion = os.environ.get("APP_DATA_DIR", "").strip()
	data = Path(redireccion).expanduser().resolve() if redireccion else _raiz_proyecto() / "data"
	return data / "logs"


def _limpiar_sesiones_antiguas(carpeta: Path, prefijo: str, actual: str, conservar: int) -> None:
	"""Deja solo las ``conservar`` sesiones más recientes (.log, .jsonl y sus rotaciones).

	La sesión actual cuenta entre las conservadas aunque sus archivos aún no
	existan (se crean al escribir la primera línea).
	"""
	if conservar <= 0:
		return
	sesiones: dict[str, list[Path]] = {actual: []}
	for archivo in carpeta.glob(f"{prefijo}_*"):
		clave = archivo.name.split(".", 1)[0]
		sesiones.setdefault(clave, []).append(archivo)
	antiguas = sorted(sesiones)[:-conservar]
	for clave in antiguas:
		if clave == actual:
			continue
		for archivo in sesiones[clave]:
			with contextlib.suppress(OSError):
				archivo.unlink()


class ManejadorArchivoSeguro(logging.handlers.RotatingFileHandler):
	"""RotatingFileHandler que sobrevive a los problemas típicos de Windows.

	- Si la rotación falla (``PermissionError: [WinError 32]`` porque otro
	  programa, un antivirus o un visor tiene el archivo abierto), sigue
	  escribiendo en el archivo actual y lo reintenta pasado un minuto.
	- Los errores del propio manejador no se escriben en ``sys.stderr`` (que en
	  la versión compilada es el propio registro: se formaría un bucle), sino
	  en ``<archivo>.errores_registro``.
	"""

	_reintentar_desde = 0.0

	def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802 - API de logging
		return time.monotonic() >= self._reintentar_desde and bool(super().shouldRollover(record))

	def doRollover(self) -> None:  # noqa: N802 - API de logging
		try:
			super().doRollover()
		except OSError as error:
			self._reintentar_desde = time.monotonic() + 60
			if self.stream is None:
				self.stream = self._open()
			self._anotar(f"Rotación aplazada 60 s: {error!r}")

	def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - API de logging
		self._anotar(f"Error al escribir un registro de {record.name}:{record.lineno}: {sys.exc_info()[1]!r}")

	def _anotar(self, texto: str) -> None:
		with contextlib.suppress(OSError):
			with open(self.baseFilename + ".errores_registro", "a", encoding="utf-8") as archivo:
				archivo.write(f"{datetime.now().strftime(FORMATO_FECHA)} {texto}\n")


def _crear_manejador(ruta: Path, nivel: int, formato: logging.Formatter, max_mb: float, copias: int) -> logging.Handler:
	manejador = ManejadorArchivoSeguro(
		ruta, maxBytes=int(max_mb * 1024 * 1024), backupCount=copias,
		encoding="utf-8", errors="backslashreplace", delay=True,
	)
	manejador.setLevel(nivel)
	manejador.setFormatter(formato)
	return manejador


def configurar_registro(
	nombre_app: str = "Aplicacion",
	version: str = "",
	nivel: str | int = "INFO",
	carpeta: str | Path | None = None,
	sesiones_conservadas: int = 10,
	max_mb_sesion: float = 20,
	json_lines: bool = True,
	consola: bool | None = None,
	bibliotecas_ruidosas: tuple[str, ...] = BIBLIOTECAS_RUIDOSAS,
	capturar_no_controladas: bool = True,
	prefijo: str = "sesion",
	archivo_incidencias: bool = True,
	paquetes_propios: tuple[str, ...] = ("app",),
) -> Path:
	"""Configura el registro de toda la aplicación. Devuelve el archivo de esta sesión.

	Llamar UNA vez, lo antes posible en ``main.py`` (antes de importar wx y los
	módulos pesados). Llamadas posteriores reconfiguran sin duplicar salidas.

	:param nombre_app: nombre que aparece en la cabecera de la sesión.
	:param version: versión de la aplicación (``APP_VERSION``).
	:param nivel: nivel mínimo (``"DEBUG"``, ``"INFO"``...). ``APP_DEPURACION=1`` fuerza DEBUG.
	:param carpeta: carpeta de registros; por defecto ``data/logs`` junto al programa.
	:param sesiones_conservadas: cuántas sesiones se guardan antes de borrar las antiguas.
	:param max_mb_sesion: tamaño máximo del .log de una sesión antes de rotar (se guardan 2 trozos).
	:param json_lines: si se escribe también el .jsonl estructurado.
	:param consola: copiar los mensajes a la consola. Por defecto, solo en depuración y si hay consola.
	:param bibliotecas_ruidosas: loggers que se limitan a WARNING (salvo en depuración).
	:param capturar_no_controladas: instalar los ganchos de excepciones no controladas.
	:param prefijo: prefijo de los archivos de sesión. Un proceso auxiliar (worker) del
		mismo programa debe usar otro (``"worker"``) para no pisar los de la interfaz.
	:param paquetes_propios: paquetes del proyecto; sirven para señalar el «código propio» de
		cada traza también en la versión compilada.
	:param archivo_incidencias: escribir ``incidencias.log``. Póngalo a ``False`` en procesos
		auxiliares: en Windows, dos procesos rotando el mismo archivo fallan (WinError 32).
	"""
	if _estado["configurado"]:
		detener_registro(cerrar_fallo_nativo=True)

	depuracion = os.environ.get(VARIABLE_DEPURACION, "").strip() not in ("", "0")
	nivel_num = logging.DEBUG if depuracion else (
		nivel if isinstance(nivel, int) else getattr(logging, str(nivel).upper(), logging.INFO)
	)
	carpeta_logs = Path(carpeta) if carpeta else _carpeta_predeterminada()
	carpeta_logs.mkdir(parents=True, exist_ok=True)

	_estado["raiz_proyecto"] = _raiz_proyecto()
	_estado["paquetes_propios"] = tuple(paquetes_propios)
	_estado["sesion"] = datetime.now().strftime("%Y%m%d_%H%M%S")
	_estado["carpeta"] = carpeta_logs
	base = f"{prefijo}_{_estado['sesion']}"
	archivo_sesion = carpeta_logs / f"{base}.log"
	_estado["archivo_sesion"] = archivo_sesion

	if capturar_no_controladas:
		_redirigir_flujos_sin_consola()

	manejadores: list[logging.Handler] = [
		_crear_manejador(archivo_sesion, logging.DEBUG, FormatoLegible(), max_mb_sesion, 2),
	]
	if archivo_incidencias:
		manejadores.append(_crear_manejador(carpeta_logs / "incidencias.log", logging.WARNING, FormatoLegible(), 5, 5))
	if json_lines:
		manejadores.append(_crear_manejador(carpeta_logs / f"{base}.jsonl", logging.DEBUG, FormatoJSON(), max_mb_sesion, 2))
	if consola is None:
		consola = depuracion
	flujo_consola = _estado.get("stderr_original") or sys.__stderr__
	if consola and flujo_consola is not None:
		manejador_consola = logging.StreamHandler(flujo_consola)
		manejador_consola.setFormatter(FormatoLegible())
		manejadores.append(manejador_consola)

	cola: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
	manejador_cola = ManejadorCola(cola)
	manejador_cola.addFilter(FiltroRepeticiones())
	manejador_cola.addFilter(FiltroContexto())
	oyente = logging.handlers.QueueListener(cola, *manejadores, respect_handler_level=True)

	raiz = logging.getLogger()
	for previo in list(raiz.handlers):
		raiz.removeHandler(previo)
	raiz.addHandler(manejador_cola)
	raiz.setLevel(nivel_num)

	if not depuracion:
		for nombre in bibliotecas_ruidosas:
			logging.getLogger(nombre).setLevel(logging.WARNING)

	oyente.start()
	# El hilo del oyente se llama "Thread-N"; se renombra para identificarlo.
	hilo = getattr(oyente, "_thread", None)
	if hilo is not None:
		hilo.name = _NOMBRE_HILO_ESCRITOR

	_estado.update(oyente=oyente, manejador_cola=manejador_cola, manejadores=manejadores, configurado=True)
	_limpiar_sesiones_antiguas(carpeta_logs, prefijo, base, sesiones_conservadas)
	cierre_anomalo = _marcar_sesion_en_curso(carpeta_logs, prefijo)
	if capturar_no_controladas:
		_instalar_capturas()
		_activar_faulthandler(carpeta_logs)
	if not _estado.get("atexit"):
		atexit.register(detener_registro)
		_estado["atexit"] = True

	registrar_entorno(nombre_app, version, nivel_num, depuracion)
	if cierre_anomalo:
		logging.getLogger("app.registro").warning(
			"La sesión anterior (%s) no terminó limpiamente: pudo cerrarse de golpe, colgarse o "
			"morir por un fallo nativo. Revise fallo_nativo.log y la carpeta no_controladas.", cierre_anomalo,
		)
	return archivo_sesion


def _marcar_sesion_en_curso(carpeta: Path, prefijo: str) -> str:
	"""Crea la marca de sesión en curso y devuelve la sesión anterior si no cerró bien."""
	marca = carpeta / f"{prefijo}.en_curso"
	anterior = ""
	with contextlib.suppress(OSError):
		if marca.exists():
			anterior = marca.read_text(encoding="utf-8").strip() or "desconocida"
		marca.write_text(_estado["sesion"], encoding="utf-8")
	_estado["marca_sesion"] = marca
	return anterior


def registrar_entorno(nombre_app: str, version: str, nivel: int, depuracion: bool) -> None:
	"""Escribe la cabecera de la sesión: sin esto, un registro ajeno es inútil."""
	log = logging.getLogger("app.registro")
	congelada = bool(getattr(sys, "frozen", False))
	log.info("══════ %s %s · sesión %s ══════", nombre_app, version or "(sin versión)", _estado["sesion"])
	log.info(
		"Python %s (%s) · %s · %s", platform.python_version(), platform.architecture()[0],
		platform.platform(), "compilada" if congelada else "desde código",
	)
	log.info("Ejecutable: %s · Raíz: %s · Registros: %s", sys.executable, _estado["raiz_proyecto"], _estado["carpeta"])
	log.info("Nivel: %s%s · PID %s", logging.getLevelName(nivel), " (depuración)" if depuracion else "", os.getpid())
	with contextlib.suppress(Exception):
		import locale

		log.info("Configuración regional: %s · Codificación: %s", locale.getlocale(), sys.getfilesystemencoding())


def registrar_version_wx() -> None:
	"""Añade la versión de wxPython al registro (llamar tras importar wx)."""
	with contextlib.suppress(Exception):
		import wx

		logging.getLogger("app.registro").info("wxPython %s", wx.version())


def cambiar_nivel(nivel: str | int) -> None:
	"""Cambia el nivel en caliente (por ejemplo, desde Preferencias)."""
	valor = nivel if isinstance(nivel, int) else getattr(logging, str(nivel).upper(), logging.INFO)
	logging.getLogger().setLevel(valor)
	logging.getLogger("app.registro").info("Nivel de registro cambiado a %s.", logging.getLevelName(valor))


def detener_registro(cerrar_fallo_nativo: bool = False) -> None:
	"""Vacía la cola y cierra los archivos. Se llama sola al salir (atexit).

	Al salir NO se desactiva faulthandler: un fallo nativo durante el cierre
	del intérprete (frecuente con wx) también debe quedar registrado.
	"""
	if cerrar_fallo_nativo and _estado.get("archivo_fallo_nativo") is not None:
		with contextlib.suppress(Exception):
			faulthandler.disable()
			_estado["archivo_fallo_nativo"].close()
		_estado["archivo_fallo_nativo"] = None
	oyente = _estado.get("oyente")
	if oyente is not None:
		logging.getLogger("app.registro").info("Fin de la sesión %s.", _estado.get("sesion"))
		with contextlib.suppress(Exception):
			oyente.stop()
	marca = _estado.pop("marca_sesion", None)
	if marca is not None:
		with contextlib.suppress(OSError):
			marca.unlink(missing_ok=True)
	for manejador in _estado.get("manejadores") or []:
		with contextlib.suppress(Exception):
			manejador.close()
	raiz = logging.getLogger()
	manejador_cola = _estado.get("manejador_cola")
	if manejador_cola is not None and manejador_cola in raiz.handlers:
		raiz.removeHandler(manejador_cola)
	_estado.update(oyente=None, manejador_cola=None, manejadores=[], configurado=False)


def archivo_sesion_actual() -> Path | None:
	"""Ruta del registro legible de esta sesión (para «Abrir registro»)."""
	return _estado.get("archivo_sesion")


def carpeta_registros() -> Path:
	"""Carpeta de registros activa."""
	return _estado.get("carpeta") or _carpeta_predeterminada()


# ════════════════════════════════════════════════════════════════════════════
# Contexto de operación y utilidades para el código de la aplicación
# ════════════════════════════════════════════════════════════════════════════

@contextlib.contextmanager
def operacion(
	nombre: str,
	logger: logging.Logger | None = None,
	nivel_fin: int = logging.DEBUG,
	avisar_si_supera: float | None = None,
) -> Iterator[str]:
	"""Marca un bloque como operación: cada línea registrada dentro lleva su id.

	Registra el inicio y el final con la duración. Si se escapa una excepción,
	la registra UNA vez con su traza y la relanza (no la oculta)::

		with registro.operacion("importar-feeds"):
			...

	Las operaciones anidadas se muestran como ``padre>hija``. Con
	``avisar_si_supera`` (segundos), una operación lenta se anota como WARNING.
	"""
	log = logger or logging.getLogger("app.operacion")
	padre = _operacion_actual.get()
	identificador = f"{nombre}#{next(_contador_operaciones)}"
	token = _operacion_actual.set(f"{padre}>{identificador}" if padre else identificador)
	inicio = time.perf_counter()
	log.log(nivel_fin, "Inicio de %s", nombre)
	try:
		yield identificador
	except Exception as error:
		duracion = (time.perf_counter() - inicio) * 1000
		if getattr(error, "_ya_registrada", False):
			# Una operación interior ya volcó la traza: aquí basta una línea.
			log.error("Falló %s tras %.0f ms (error ya registrado arriba)", nombre, duracion)
		else:
			log.exception("Falló %s tras %.0f ms", nombre, duracion)
			_marcar_registrada(error)
		raise
	else:
		segundos = time.perf_counter() - inicio
		lenta = avisar_si_supera is not None and segundos > avisar_si_supera
		log.log(
			logging.WARNING if lenta else nivel_fin, "Fin de %s en %.0f ms%s", nombre, segundos * 1000,
			" (más lenta de lo esperado)" if lenta else "",
		)
	finally:
		_operacion_actual.reset(token)


def _marcar_registrada(error: BaseException) -> None:
	"""Marca una excepción como ya registrada para no duplicar su traza al relanzarla."""
	with contextlib.suppress(AttributeError, TypeError):
		error._ya_registrada = True  # type: ignore[attr-defined]


def con_contexto(funcion: Callable[..., Any]) -> Callable[..., Any]:
	"""Envuelve ``funcion`` para que conserve la operación actual en otro hilo.

	Los ``contextvars`` no viajan solos a ``threading.Thread``,
	``ThreadPoolExecutor`` ni ``wx.CallAfter``::

		threading.Thread(target=registro.con_contexto(trabajo)).start()
		wx.CallAfter(registro.con_contexto(actualizar_lista), datos)
	"""
	contexto = contextvars.copy_context()

	@functools.wraps(funcion)
	def envoltura(*args: Any, **kwargs: Any) -> Any:
		return contexto.run(funcion, *args, **kwargs)

	return envoltura


def registrar_excepciones(
	mensaje: str | None = None,
	relanzar: bool = False,
	logger: logging.Logger | None = None,
	valor_por_defecto: Any = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
	"""Decorador para manejadores de eventos y tareas en hilos.

	Registra cualquier excepción con su traza, el nombre de la función y los
	nombres de sus argumentos. Con ``relanzar=False`` (lo habitual en
	manejadores de wx) devuelve ``valor_por_defecto`` y la aplicación sigue::

		@registro.registrar_excepciones("No se pudo guardar el documento")
		def al_guardar(self, evento): ...
	"""

	def decorador(funcion: Callable[..., Any]) -> Callable[..., Any]:
		log = logger or logging.getLogger(funcion.__module__)

		@functools.wraps(funcion)
		def envoltura(*args: Any, **kwargs: Any) -> Any:
			try:
				return funcion(*args, **kwargs)
			except Exception as error:
				if getattr(error, "_ya_registrada", False):
					log.error("%s (en %s; error ya registrado)", mensaje or "Error inesperado", funcion.__qualname__)
				else:
					log.exception("%s (en %s)", mensaje or "Error inesperado", funcion.__qualname__)
					_marcar_registrada(error)
				if relanzar:
					raise
				return valor_por_defecto

		return envoltura

	return decorador


def instalar_puente_wx() -> Any:
	"""Envía al registro los mensajes internos de wxWidgets (wxLogError, wxLogWarning...).

	Sin esto aparecen como cuadros de diálogo de wx (que interrumpen al lector
	de pantalla) o se pierden. Llamar tras crear ``wx.App`` y GUARDAR el objeto
	devuelto mientras viva la aplicación.
	"""
	import wx

	niveles = {
		wx.LOG_FatalError: logging.CRITICAL, wx.LOG_Error: logging.ERROR, wx.LOG_Warning: logging.WARNING,
		wx.LOG_Message: logging.INFO, wx.LOG_Status: logging.INFO, wx.LOG_Info: logging.INFO,
		wx.LOG_Debug: logging.DEBUG, wx.LOG_Trace: logging.DEBUG,
	}
	destino = logging.getLogger("wx")

	class _Puente(wx.Log):
		def DoLogTextAtLevel(self, level, msg):  # noqa: N802 - API de wx
			destino.log(niveles.get(level, logging.INFO), "%s", msg)

	puente = _Puente()
	wx.Log.SetActiveTarget(puente)
	return puente


def ejecutar_proceso(orden: list[str], tiempo_maximo: float = 120, **opciones: Any):
	"""Ejecuta un programa externo (ffmpeg, aria2c...) y deja constancia en el registro.

	Sin ventana de consola, sin heredar la entrada estándar (en la versión
	compilada no es válida), con la orden REDACTADA, el código de salida, la
	duración y, si falla, el final de su salida de error.
	"""
	import subprocess

	log = logging.getLogger("app.procesos")
	texto_orden = redactar(subprocess.list2cmdline([str(parte) for parte in orden]))
	inicio = time.perf_counter()
	try:
		resultado = subprocess.run(
			orden, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
			errors="replace", timeout=tiempo_maximo, check=False,
			creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **opciones,
		)
	except (OSError, subprocess.TimeoutExpired) as error:
		if hasattr(error, "add_note"):  # Python 3.11+
			error.add_note(f"Orden: {texto_orden}")
		log.exception("No se pudo ejecutar: %s", texto_orden)
		_marcar_registrada(error)
		raise
	milisegundos = (time.perf_counter() - inicio) * 1000
	if resultado.returncode == 0:
		log.info("Código 0 en %.0f ms: %s", milisegundos, texto_orden)
	else:
		log.error(
			"Código %s en %.0f ms: %s\nFinal de su salida de error:\n%s", resultado.returncode, milisegundos,
			texto_orden, redactar((resultado.stderr or "")[-2000:]),
		)
	return resultado


def ultimas_incidencias(cantidad: int = 50) -> list[str]:
	"""Devuelve las últimas líneas de cabecera de ``incidencias.log`` (para un visor)."""
	ruta = carpeta_registros() / "incidencias.log"
	try:
		lineas = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
	except OSError:
		return []
	cabeceras = [linea for linea in lineas if linea[:4].isdigit()]
	return cabeceras[-cantidad:]


def registrar_fallo_de_arranque(error: BaseException) -> Path | None:
	"""Último recurso si el registro no llegó a configurarse o el arranque murió.

	Escribe la traza en ``data/logs/arranque_fallido.log`` sin depender de
	nada más. Devuelve la ruta escrita o ``None``.
	"""
	try:
		carpeta = _estado.get("carpeta") or _carpeta_predeterminada()
		carpeta.mkdir(parents=True, exist_ok=True)
		ruta = carpeta / "arranque_fallido.log"
		with ruta.open("a", encoding="utf-8") as archivo:
			archivo.write(f"\n=== {datetime.now().strftime(FORMATO_FECHA)} · pid {os.getpid()} ===\n")
			archivo.write(redactar("".join(traceback.format_exception(error))))
		return ruta
	except OSError:
		return None
