"""Registro (logging) básico y autocontenido de la aplicación.

Objetivo: que ningún error se pierda, ni siquiera en la versión compilada sin
consola. Solo biblioteca estándar, para poder configurarlo ANTES de importar
wx y los módulos pesados (así hasta un fallo de importación queda escrito).

Qué deja en ``data/logs``:

- ``sesion_AAAAMMDD_HHMMSS.log``: un archivo por sesión. Uno por sesión y no
  uno rotativo porque en Windows rotar un archivo que otro proceso tiene
  abierto (una segunda instancia arrancando) falla con ``WinError 32``. Se
  conservan las ``SESIONES_A_CONSERVAR`` más recientes.
- ``fallo_nativo.log``: volcado de ``faulthandler`` si el proceso muere por un
  fallo de C (una DLL, wxWidgets...).
- ``arranque_fallido.log``: lo escribe ``registrar_fallo_de_arranque`` cuando
  falla el propio arranque.

Si el proyecto crece y necesita más (JSONL, incidencias, redacción de
secretos, vigilante de congelaciones), este módulo se sustituye por uno más
completo que conserve ``configurar_registro`` y ``registrar_fallo_de_arranque``.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)

FORMATO = "%(asctime)s.%(msecs)03d %(levelname)-8s [%(threadName)s] %(name)s (%(filename)s:%(lineno)d en %(funcName)s) | %(message)s"
FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"
SESIONES_A_CONSERVAR = 10
VARIABLE_DEPURACION = "APP_DEPURACION"

# Estado del módulo: lo que hay que deshacer en detener_registro().
_manejador: logging.Handler | None = None
_fallo_nativo: TextIO | None = None
_aviso_usuario: Callable[[str], None] | None = None
_ganchos_originales: dict[str, Any] = {}


class _FlujoAlRegistro:
	"""Sustituye a ``sys.stdout``/``sys.stderr`` cuando valen ``None``.

	Compilada con ``base="gui"`` no hay consola y ``sys.stdout`` es ``None``:
	un simple ``print`` de una biblioteca lanzaría ``AttributeError``. Así lo
	que se escriba acaba en el registro en lugar de romper la aplicación.
	"""

	def __init__(self, nivel: int) -> None:
		self._nivel = nivel
		self._registrador = logging.getLogger("salida_estandar")

	def write(self, texto: str) -> int:
		limpio = texto.rstrip()
		if limpio:
			self._registrador.log(self._nivel, "%s", limpio)
		return len(texto)

	def flush(self) -> None:
		"""Nada que vaciar: cada escritura va directa al registro."""

	def isatty(self) -> bool:
		return False


def _limpiar_sesiones_antiguas(carpeta: Path) -> None:
	"""Borra las sesiones más antiguas y conserva las ``SESIONES_A_CONSERVAR`` últimas."""
	sesiones = sorted(carpeta.glob("sesion_*.log"))
	for antigua in sesiones[:-SESIONES_A_CONSERVAR]:
		try:
			antigua.unlink()
		except OSError:
			# Puede tenerla abierta otra instancia; se borrará en otro arranque.
			log.debug("No se pudo borrar la sesión antigua %s", antigua, exc_info=True)


def _gancho_excepciones(tipo, valor, rastro) -> None:
	"""``sys.excepthook``: también recibe las excepciones de los manejadores de wx."""
	if issubclass(tipo, KeyboardInterrupt):
		sys.__excepthook__(tipo, valor, rastro)
		return
	logging.getLogger("no_controlada").critical("Excepción no controlada", exc_info=(tipo, valor, rastro))
	_avisar(f"{tipo.__name__}: {valor}")


def _gancho_hilos(argumentos: threading.ExceptHookArgs) -> None:
	"""``threading.excepthook``: sin él, un fallo en un hilo solo va a stderr (que puede no existir)."""
	if argumentos.exc_type is SystemExit:
		return
	nombre = argumentos.thread.name if argumentos.thread else "desconocido"
	logging.getLogger("no_controlada").critical(
		"Excepción no controlada en el hilo %s",
		nombre,
		exc_info=(argumentos.exc_type, argumentos.exc_value, argumentos.exc_traceback),
	)
	_avisar(f"{argumentos.exc_type.__name__}: {argumentos.exc_value}")


def _avisar(resumen: str) -> None:
	"""Llama al aviso visible (si la interfaz instaló uno) sin dejar que falle."""
	if _aviso_usuario is None:
		return
	try:
		_aviso_usuario(resumen)
	except Exception:  # noqa: BLE001 - el aviso nunca debe provocar otro error
		log.debug("No se pudo avisar al usuario del error", exc_info=True)


def establecer_aviso_usuario(funcion: Callable[[str], None] | None) -> None:
	"""Registra la función que enseña al usuario un error no controlado.

	La instala la interfaz cuando ya existe ``wx.App``; debe ser segura para
	llamarla desde cualquier hilo (es decir, usar ``wx.CallAfter``).
	"""
	global _aviso_usuario
	_aviso_usuario = funcion


def configurar_registro(
	nombre_app: str,
	version: str,
	nivel: str = "INFO",
	carpeta: Path | None = None,
) -> Path:
	"""Configura el registro de la sesión y los ganchos de errores. Devuelve el archivo de sesión.

	:param nombre_app: nombre visible, para la cabecera.
	:param version: versión de la aplicación, para la cabecera.
	:param nivel: nivel mínimo (``DEBUG``, ``INFO``...). ``APP_DEPURACION=1``
		lo fuerza a ``DEBUG`` y copia los mensajes a la consola.
	:param carpeta: carpeta de registros; por defecto ``data/logs``.
	"""
	global _manejador, _fallo_nativo
	from app.core import paths

	detener_registro()
	carpeta = carpeta or paths.ruta_registros()
	carpeta.mkdir(parents=True, exist_ok=True)
	depuracion = os.environ.get(VARIABLE_DEPURACION, "").strip() == "1"
	nivel_numerico = logging.DEBUG if depuracion else getattr(logging, str(nivel).upper(), logging.INFO)

	archivo = carpeta / f"sesion_{time.strftime('%Y%m%d_%H%M%S')}.log"
	# delay=False: si la carpeta no se puede escribir, se sabe YA (y lo recoge
	# la red de seguridad de main.py) en lugar de en el primer mensaje.
	_manejador = logging.FileHandler(archivo, encoding="utf-8")
	_manejador.setFormatter(logging.Formatter(FORMATO, FORMATO_FECHA))
	raiz = logging.getLogger()
	raiz.addHandler(_manejador)
	raiz.setLevel(nivel_numerico)
	if depuracion and sys.stderr is not None:
		consola = logging.StreamHandler(sys.stderr)
		consola.setFormatter(logging.Formatter(FORMATO, FORMATO_FECHA))
		consola.set_name("consola_depuracion")
		raiz.addHandler(consola)

	# Flujos estándar ausentes (compilada sin consola).
	if sys.stdout is None:
		_ganchos_originales["stdout"] = None
		sys.stdout = _FlujoAlRegistro(logging.INFO)
	if sys.stderr is None:
		_ganchos_originales["stderr"] = None
		sys.stderr = _FlujoAlRegistro(logging.ERROR)

	_ganchos_originales.setdefault("excepthook", sys.excepthook)
	_ganchos_originales.setdefault("threading", threading.excepthook)
	sys.excepthook = _gancho_excepciones
	threading.excepthook = _gancho_hilos

	try:
		_fallo_nativo = open(carpeta / "fallo_nativo.log", "a", encoding="utf-8")  # noqa: SIM115 - abierto toda la sesión
		faulthandler.enable(_fallo_nativo, all_threads=True)
	except (OSError, RuntimeError):
		log.warning("No se pudo activar el volcado de fallos nativos", exc_info=True)

	_limpiar_sesiones_antiguas(carpeta)
	log.info("===== %s %s =====", nombre_app, version)
	log.info(
		"Python %s (%s) en %s; compilada=%s; datos=%s",
		platform.python_version(),
		platform.architecture()[0],
		platform.platform(),
		paths.CONGELADA,
		paths.ruta_datos(),
	)
	return archivo


def detener_registro() -> None:
	"""Cierra los archivos y restaura los ganchos (pruebas y reconfiguración)."""
	global _manejador, _fallo_nativo
	raiz = logging.getLogger()
	for manejador in list(raiz.handlers):
		if manejador is _manejador or manejador.get_name() == "consola_depuracion":
			raiz.removeHandler(manejador)
			manejador.close()
	_manejador = None
	if _fallo_nativo is not None:
		try:
			faulthandler.disable()
			_fallo_nativo.close()
		except (OSError, RuntimeError):
			log.debug("No se pudo cerrar el volcado de fallos nativos", exc_info=True)
		_fallo_nativo = None
	if "excepthook" in _ganchos_originales:
		sys.excepthook = _ganchos_originales.pop("excepthook")
	if "threading" in _ganchos_originales:
		threading.excepthook = _ganchos_originales.pop("threading")
	if "stdout" in _ganchos_originales:
		sys.stdout = _ganchos_originales.pop("stdout")
	if "stderr" in _ganchos_originales:
		sys.stderr = _ganchos_originales.pop("stderr")


def registrar_fallo_de_arranque(error: BaseException) -> Path | None:
	"""Escribe ``arranque_fallido.log`` aunque el registro normal no llegara a funcionar.

	Solo usa la biblioteca estándar y calcula la ruta con el mínimo de piezas:
	si falla hasta ``paths``, prueba con la carpeta ``data`` junto a este
	paquete. Devuelve la ruta escrita o ``None`` si no hubo dónde escribir.
	"""
	texto = "".join(traceback.format_exception(type(error), error, error.__traceback__))
	candidatas: list[Path] = []
	try:
		from app.core import paths

		candidatas.append(paths.ruta_datos() / "logs")
	except Exception:  # noqa: BLE001 - se prueba con la ruta de respaldo
		log.debug("No se pudo calcular la carpeta data; se usa la de respaldo", exc_info=True)
	respaldo = Path(__file__).resolve().parent.parent.parent / "data" / "logs"
	if respaldo not in candidatas:
		candidatas.append(respaldo)
	for carpeta in candidatas:
		try:
			carpeta.mkdir(parents=True, exist_ok=True)
			ruta = carpeta / "arranque_fallido.log"
			with open(ruta, "a", encoding="utf-8") as archivo:
				archivo.write(f"\n===== Fallo de arranque {time.strftime(FORMATO_FECHA)} =====\n")
				archivo.write(f"Python {platform.python_version()} en {platform.platform()}\n")
				archivo.write(texto)
			return ruta
		except OSError:
			continue
	return None
