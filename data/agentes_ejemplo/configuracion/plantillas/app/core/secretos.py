"""Secretos de la aplicación (tokens, contraseñas, claves de API) cifrados.

Los secretos NUNCA van en ``settings.json``: se guardan en
``data/config/secretos.dat``, cifrados.

Protección por defecto: **DPAPI de Windows** (``CryptProtectData`` /
``CryptUnprotectData`` con ``ctypes``, sin dependencias). Contrapartida que
hay que conocer:

- DPAPI liga el cifrado al **usuario de Windows** (y a su contraseña). Otro
  usuario del mismo equipo no puede descifrarlo, ni quien copie el archivo.
- Por lo mismo, si se copia la carpeta portable a **otro equipo** (o a otro
  usuario), los secretos no se pueden leer y hay que volver a introducir las
  credenciales. Este módulo lo detecta, NO sobrescribe el archivo ilegible
  (lo aparta como ``secretos.dat.ilegible-FECHA``) y lo indica en
  ``AlmacenSecretos.ilegibles`` para que la interfaz lo explique.

Alternativa opcional: ``ProteccionFernet`` (paquete ``cryptography``) con la
clave en ``data/config/secretos.key``. Viaja con la carpeta portable, pero
entonces es **ofuscación**, no protección: quien tenga la carpeta tiene la
clave y los datos. Otra alternativa (``keyring``, Administrador de
credenciales de Windows) se explica en ``referencias/secretos.md``.

Los valores jamás se escriben en el registro (logs): solo sus nombres.

PLANTILLA del agente «configuracion».
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

VARIABLE_DATA = "APP_DATA_DIR"
NOMBRE_ARCHIVO = "secretos.dat"
FORMATO = 1

# Entropía adicional de DPAPI: no es secreta, pero impide que otro programa del
# mismo usuario descifre nuestros datos llamando a CryptUnprotectData a ciegas.
# Cámbiala por una propia de la aplicación (y no la cambies después: los
# secretos guardados dejarían de leerse).
ENTROPIA_APLICACION = b"MiAplicacion/secretos/v1"


class ErrorSecretos(Exception):
	"""Error al proteger, leer o guardar secretos."""


class Proteccion(Protocol):
	"""Interfaz de un método de cifrado."""

	nombre: str

	def proteger(self, datos: bytes) -> bytes: ...

	def desproteger(self, datos: bytes) -> bytes: ...


# ── DPAPI con ctypes ─────────────────────────────────────────────────────────

class ProteccionDPAPI:
	"""Cifrado con DPAPI en el ámbito del usuario actual de Windows."""

	nombre = "dpapi"
	# CRYPTPROTECT_UI_FORBIDDEN: nunca mostrar un diálogo del sistema (bloquearía
	# la aplicación y el lector de pantalla no sabría de dónde sale).
	_SIN_INTERFAZ = 0x1

	def __init__(self, entropia: bytes = ENTROPIA_APLICACION, descripcion: str = "secretos") -> None:
		if sys.platform != "win32":
			raise ErrorSecretos("DPAPI solo está disponible en Windows.")
		import ctypes
		from ctypes import wintypes

		class DATA_BLOB(ctypes.Structure):  # noqa: N801 - nombre de la API de Windows
			_fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

		self._ctypes = ctypes
		self._blob = DATA_BLOB
		self._entropia = entropia
		self._descripcion = descripcion
		crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		puntero = ctypes.POINTER(DATA_BLOB)
		# Firmas explícitas: sin argtypes, en 64 bits ctypes puede truncar punteros.
		self._proteger = crypt32.CryptProtectData
		self._proteger.argtypes = [puntero, wintypes.LPCWSTR, puntero, ctypes.c_void_p, ctypes.c_void_p,
			wintypes.DWORD, puntero]
		self._proteger.restype = wintypes.BOOL
		self._desproteger = crypt32.CryptUnprotectData
		self._desproteger.argtypes = [puntero, ctypes.POINTER(wintypes.LPWSTR), puntero, ctypes.c_void_p,
			ctypes.c_void_p, wintypes.DWORD, puntero]
		self._desproteger.restype = wintypes.BOOL
		self._liberar = kernel32.LocalFree
		self._liberar.argtypes = [ctypes.c_void_p]
		self._liberar.restype = ctypes.c_void_p

	def _entrada(self, datos: bytes):
		"""Crea un DATA_BLOB que apunta a una copia de ``datos`` (se conserva el búfer)."""
		bufer = self._ctypes.create_string_buffer(datos, len(datos))
		blob = self._blob(len(datos), self._ctypes.cast(bufer, self._ctypes.POINTER(self._ctypes.c_char)))
		return blob, bufer

	def _llamar(self, funcion, datos: bytes, con_descripcion: bool) -> bytes:
		ctypes = self._ctypes
		entrada, _bufer = self._entrada(datos)
		entropia, _bufer_entropia = self._entrada(self._entropia)
		salida = self._blob()
		descripcion = self._descripcion if con_descripcion else None
		if not funcion(ctypes.byref(entrada), descripcion, ctypes.byref(entropia), None, None,
				self._SIN_INTERFAZ, ctypes.byref(salida)):
			codigo = ctypes.get_last_error()
			raise ErrorSecretos(f"DPAPI falló (código {codigo}): {ctypes.FormatError(codigo)}")
		try:
			return ctypes.string_at(salida.pbData, salida.cbData)
		finally:
			# La memoria de salida la reserva Windows: hay que liberarla con LocalFree.
			self._liberar(ctypes.cast(salida.pbData, ctypes.c_void_p))

	def proteger(self, datos: bytes) -> bytes:
		return self._llamar(self._proteger, datos, con_descripcion=True)

	def desproteger(self, datos: bytes) -> bytes:
		return self._llamar(self._desproteger, datos, con_descripcion=False)


# ── Alternativa opcional: Fernet con clave en data ───────────────────────────

class ProteccionFernet:
	"""Cifrado con ``cryptography.Fernet`` y la clave en un archivo de ``data``.

	OJO: la clave viaja junto a los datos. Evita que alguien lea los secretos
	abriendo el archivo con el Bloc de notas (ofuscación), pero NO protege frente
	a quien tenga la carpeta completa. Úsala solo si la portabilidad entre
	equipos pesa más que la protección, y díselo al usuario.
	"""

	nombre = "fernet"

	def __init__(self, ruta_clave: Path) -> None:
		try:
			from cryptography.fernet import Fernet
		except ImportError as error:
			raise ErrorSecretos("Falta el paquete «cryptography» para ProteccionFernet.") from error
		self._ruta_clave = Path(ruta_clave)
		if self._ruta_clave.is_file():
			clave = self._ruta_clave.read_bytes().strip()
		else:
			clave = Fernet.generate_key()
			self._ruta_clave.parent.mkdir(parents=True, exist_ok=True)
			_escribir_atomico(self._ruta_clave, clave)
			log.info("Creada la clave de ofuscación de secretos en %s", self._ruta_clave.name)
		self._fernet = Fernet(clave)

	def proteger(self, datos: bytes) -> bytes:
		return self._fernet.encrypt(datos)

	def desproteger(self, datos: bytes) -> bytes:
		from cryptography.fernet import InvalidToken
		try:
			return self._fernet.decrypt(datos)
		except InvalidToken as error:
			raise ErrorSecretos("La clave no corresponde a estos secretos.") from error


# ── Utilidades ───────────────────────────────────────────────────────────────

def carpeta_datos() -> Path:
	"""Carpeta ``data`` portable (sustitúyela por el módulo de rutas del proyecto si existe)."""
	redireccion = os.environ.get(VARIABLE_DATA, "").strip()
	if redireccion:
		return Path(redireccion).expanduser().resolve()
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent / "data"
	return Path(__file__).resolve().parents[2] / "data"


def _escribir_atomico(ruta: Path, datos: bytes) -> None:
	"""Temporal único en la misma carpeta + fsync + os.replace."""
	temporal = ruta.with_name(f"{ruta.name}.{os.getpid()}-{threading.get_ident()}.tmp")
	try:
		with open(temporal, "wb") as archivo:
			archivo.write(datos)
			archivo.flush()
			os.fsync(archivo.fileno())
		os.replace(temporal, ruta)
	except OSError:
		try:
			temporal.unlink(missing_ok=True)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
		raise


def proteccion_por_defecto() -> Proteccion:
	"""DPAPI en Windows. Fuera de Windows no hay equivalente sin dependencias."""
	return ProteccionDPAPI()


# ── Almacén ──────────────────────────────────────────────────────────────────

class AlmacenSecretos:
	"""Diccionario nombre → secreto, cifrado entero en un único archivo.

	Uso::

		secretos = AlmacenSecretos()
		secretos.guardar("cuenta1.token", token)
		token = secretos.obtener("cuenta1.token")

	Es seguro entre hilos: cada guardado es un ciclo leer-modificar-escribir bajo
	candado (dos hilos que renuevan tokens a la vez no se pisan).
	"""

	def __init__(self, ruta: Path | str | None = None, proteccion: Proteccion | None = None) -> None:
		self.ruta = Path(ruta) if ruta is not None else carpeta_datos() / "config" / NOMBRE_ARCHIVO
		self._proteccion = proteccion if proteccion is not None else proteccion_por_defecto()
		self._candado = threading.RLock()
		#: True si el archivo existía pero no se pudo descifrar (otro equipo/usuario).
		self.ilegibles = False

	# ── Lectura ──────────────────────────────────────────────────────────────

	def _cargar(self) -> dict[str, str]:
		"""Devuelve todos los secretos. ``{}`` si no hay archivo o no se puede leer."""
		self.ilegibles = False
		if not self.ruta.is_file():
			return {}
		try:
			envoltorio = json.loads(self.ruta.read_text(encoding="utf-8"))
			cifrado = base64.b64decode(envoltorio["datos"])
			claro = self._proteccion.desproteger(cifrado)
			datos = json.loads(claro.decode("utf-8"))
			if not isinstance(datos, dict):
				raise ValueError("contenido inesperado")
			return datos
		except (ErrorSecretos, ValueError, KeyError, TypeError, UnicodeDecodeError, OSError) as error:
			# No se incluye ningún dato del archivo en el mensaje: solo el tipo de fallo.
			self.ilegibles = True
			log.warning(
				"No se pudieron leer los secretos (%s: %s). Si la carpeta viene de otro equipo o de otro "
				"usuario de Windows, hay que volver a introducir las credenciales.",
				type(error).__name__, error,
			)
			return {}

	def obtener(self, nombre: str, por_defecto: str | None = None) -> str | None:
		"""Devuelve el secreto ``nombre`` o ``por_defecto``."""
		with self._candado:
			return self._cargar().get(nombre, por_defecto)

	def nombres(self) -> list[str]:
		"""Nombres de los secretos guardados (nunca sus valores)."""
		with self._candado:
			return sorted(self._cargar())

	# ── Escritura ────────────────────────────────────────────────────────────

	def _apartar_ilegible(self) -> None:
		"""Conserva el archivo ilegible en vez de pisarlo (quizá se recupere en su equipo)."""
		marca = datetime.now().strftime("%Y%m%d_%H%M%S")
		destino = self.ruta.with_name(f"{self.ruta.name}.ilegible-{marca}")
		try:
			os.replace(self.ruta, destino)
			log.warning("Secretos ilegibles apartados en %s; se empieza un almacén nuevo", destino.name)
		except OSError as error:
			raise ErrorSecretos("No se pudo apartar el archivo de secretos ilegible.") from error

	def _escribir(self, datos: dict[str, str]) -> None:
		claro = json.dumps(datos, ensure_ascii=False).encode("utf-8")
		envoltorio = {
			"formato": FORMATO,
			"proteccion": self._proteccion.nombre,
			"datos": base64.b64encode(self._proteccion.proteger(claro)).decode("ascii"),
		}
		self.ruta.parent.mkdir(parents=True, exist_ok=True)
		_escribir_atomico(self.ruta, json.dumps(envoltorio, indent=1).encode("utf-8"))

	def guardar_varios(self, cambios: dict[str, str | None]) -> None:
		"""Guarda (o borra, con valor ``None``) varios secretos en una sola escritura.

		:raises ErrorSecretos: si no se pudo cifrar o escribir.
		"""
		with self._candado:
			datos = self._cargar()
			if self.ilegibles:
				self._apartar_ilegible()
				self.ilegibles = False
			for nombre, valor in cambios.items():
				if valor is None:
					datos.pop(nombre, None)
				else:
					datos[nombre] = str(valor)
			try:
				self._escribir(datos)
			except OSError as error:
				raise ErrorSecretos(f"No se pudieron guardar los secretos: {error}") from error
		log.info("Secretos actualizados: %s", ", ".join(sorted(cambios)))

	def guardar(self, nombre: str, valor: str) -> None:
		"""Guarda un secreto."""
		self.guardar_varios({nombre: valor})

	def borrar(self, nombre: str) -> None:
		"""Borra un secreto (no hace nada si no existe)."""
		self.guardar_varios({nombre: None})


_instancia: AlmacenSecretos | None = None
_candado_instancia = threading.Lock()


def obtener_secretos() -> AlmacenSecretos:
	"""Instancia única, creada bajo demanda."""
	global _instancia
	with _candado_instancia:
		if _instancia is None:
			_instancia = AlmacenSecretos()
		return _instancia
