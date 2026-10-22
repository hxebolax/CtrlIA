"""Base de datos SQLite local y portable (``data/db/app.db``).

Decisiones y por qué:

- **Una conexión de escritura con candado + una conexión de lectura por hilo.**
  SQLite admite un solo escritor a la vez; serializar las escrituras en Python
  (``RLock``) evita los «database is locked» entre hilos propios. En modo WAL
  los lectores no bloquean al escritor ni entre sí, así que cada hilo lee con
  su propia conexión (``threading.local``, ``query_only``) sin esperar a nadie:
  la interfaz no se queda esperando detrás de una sincronización de fondo.
  Para una aplicación pequeña bastaría una sola conexión con candado; este
  diseño cuesta poco más y no se queda corto al crecer.
- **PRAGMAs**: ``journal_mode=WAL``, ``foreign_keys=ON`` (por conexión: SQLite
  lo trae desactivado), ``busy_timeout`` (espera en vez de fallar al instante
  si otro proceso escribe) y ``synchronous=NORMAL`` (en WAL es consistente; un
  corte de luz puede perder la última transacción, nunca corromper).
- **Transacciones explícitas** (``isolation_level=None`` + ``BEGIN
  IMMEDIATE``): el control no depende de las reglas implícitas del módulo
  ``sqlite3``, que cambiaron en Python 3.12. Las anidadas son ``SAVEPOINT``.
- **Migraciones numeradas con ``PRAGMA user_version``**: cada una en su
  transacción (si falla, la base queda en la versión anterior), con copia de
  seguridad del archivo antes de migrar y rechazo de versiones futuras.
- **Parámetros siempre** (``?``), nunca formateo de cadenas con valores.
- **Copias en caliente** con la API ``backup`` a ``data/backups`` con rotación.
- **Integridad al abrir** (``quick_check``) y plan de recuperación: un bloqueo
  NO es corrupción (se reintenta y nunca se aparta la base); una base dañada
  se aparta entera (con ``-wal`` y ``-shm``) y se restaura la última copia sana.
- **Cierre ordenado**: ``PRAGMA optimize`` y ``wal_checkpoint(TRUNCATE)``.

PLANTILLA del agente «base-datos-sqlite».
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VARIABLE_DATA = "APP_DATA_DIR"
NOMBRE_POR_DEFECTO = "app.db"

Parametros = Sequence[Any] | dict[str, Any]


# ── Errores ──────────────────────────────────────────────────────────────────

class ErrorBaseDatos(Exception):
	"""Problema de la base de datos que la aplicación debe tratar (no se oculta)."""


class ErrorBaseDatosBloqueada(ErrorBaseDatos):
	"""Otro proceso (antivirus, copia de seguridad, otra instancia) la tiene bloqueada."""


class ErrorMigracion(ErrorBaseDatos):
	"""Una migración falló; la base sigue en la versión anterior."""


class ErrorVersionFutura(ErrorMigracion):
	"""La base la creó una versión MÁS NUEVA de la aplicación: no se toca."""


# ── Migraciones ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Migracion:
	"""Un paso de esquema. La posición en la lista es su número (la primera es la 1).

	:param descripcion: qué hace (va al registro).
	:param sql: una o varias sentencias separadas por ``;``.
	:param funcion: alternativa a ``sql`` para migraciones con datos; recibe la
		conexión DENTRO de la transacción. No debe llamar a ``commit``.
	:param desactivar_claves_foraneas: para reconstruir tablas (el procedimiento
		de 12 pasos de SQLite). Desactiva ``foreign_keys`` fuera de la
		transacción y comprueba ``foreign_key_check`` antes de confirmar.
	"""

	descripcion: str
	sql: str | None = None
	funcion: Callable[[sqlite3.Connection], None] | None = None
	desactivar_claves_foraneas: bool = False


def dividir_sentencias(script: str) -> list[str]:
	"""Divide un script SQL en sentencias completas.

	``executescript`` no sirve dentro de una transacción (hace COMMIT antes de
	empezar). ``sqlite3.complete_statement`` sabe si un ``;`` cierra la
	sentencia o está dentro de una cadena o de un ``CREATE TRIGGER ... END``.
	"""
	sentencias: list[str] = []
	actual = ""
	for trozo in script.split(";"):
		actual += trozo + ";"
		if sqlite3.complete_statement(actual):
			texto = actual.strip()
			if texto.strip(";").strip():
				sentencias.append(texto)
			actual = ""
	resto = actual[:-1].strip()
	if resto:
		sentencias.append(resto)
	return sentencias


# ── Utilidades de SQL seguro ─────────────────────────────────────────────────

_PATRON_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def marcadores(cantidad: int) -> str:
	"""``?, ?, ?`` para una cláusula ``IN`` con parámetros.

	``f"... IN ({marcadores(len(ids))})"`` solo inserta signos ``?``: los
	valores siguen yendo como parámetros.
	"""
	if cantidad < 1:
		raise ValueError("IN () necesita al menos un valor")
	return ", ".join("?" for _ in range(cantidad))


def identificador(nombre: str) -> str:
	"""Valida y entrecomilla un nombre de tabla o columna.

	Los nombres no pueden ir como parámetros; si vienen de una lista blanca del
	propio código no hace falta, pero si se eligen en tiempo de ejecución (una
	columna de ordenación), pásalos por aquí.
	"""
	if not _PATRON_IDENTIFICADOR.match(nombre):
		raise ValueError(f"Identificador SQL no válido: {nombre!r}")
	return f'"{nombre}"'


def _resumen_sql(sql: str) -> str:
	"""SQL en una línea y truncado (es código, no datos del usuario)."""
	texto = " ".join(sql.split())
	return texto if len(texto) <= 160 else texto[:160] + "…"


def _describir_parametros(parametros: Parametros | None) -> str:
	"""Tipos y longitudes de los parámetros, NUNCA su contenido (privacidad)."""
	if not parametros:
		return "()"
	valores = parametros.values() if isinstance(parametros, dict) else parametros
	partes = []
	for valor in valores:
		if isinstance(valor, (str, bytes, bytearray)):
			partes.append(f"{type(valor).__name__}[{len(valor)}]")
		else:
			partes.append(type(valor).__name__)
	return "(" + ", ".join(partes) + ")"


def _anotar(error: BaseException, sql: str, parametros: Parametros | None) -> None:
	"""Añade el SQL a la traza (Python 3.11+) sin exponer los valores."""
	if hasattr(error, "add_note"):
		error.add_note(f"SQL: {_resumen_sql(sql)} · parámetros: {_describir_parametros(parametros)}")


def _es_bloqueo(error: BaseException) -> bool:
	"""«database is locked/busy» es pasajero; NO es corrupción."""
	texto = str(error).lower()
	return isinstance(error, sqlite3.OperationalError) and ("locked" in texto or "busy" in texto)


def _es_corrupcion(error: BaseException) -> bool:
	"""Solo estos mensajes justifican apartar la base. «unable to open» no (permisos, carpeta)."""
	texto = str(error).lower()
	return isinstance(error, sqlite3.DatabaseError) and any(
		marca in texto for marca in ("malformed", "not a database", "corrupt", "file is encrypted")
	)


def carpeta_datos() -> Path:
	"""Carpeta ``data`` portable (sustitúyela por el módulo de rutas del proyecto si existe)."""
	redireccion = os.environ.get(VARIABLE_DATA, "").strip()
	if redireccion:
		return Path(redireccion).expanduser().resolve()
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent / "data"
	return Path(__file__).resolve().parents[2] / "data"


class _BaseDanada(Exception):
	"""Interna: ``quick_check`` no devolvió «ok»."""


@dataclass
class Recuperacion:
	"""Qué se hizo al encontrar la base dañada (para contárselo al usuario)."""

	motivo: str
	apartados: list[Path] = field(default_factory=list)
	restaurada_desde: Path | None = None

	def mensaje(self) -> str:
		if self.restaurada_desde is not None:
			return ("La base de datos estaba dañada y se ha restaurado la copia de seguridad "
				f"«{self.restaurada_desde.name}». Pueden faltar los cambios posteriores a esa copia.")
		return ("La base de datos estaba dañada y no había copias de seguridad sanas: se ha creado una "
			"nueva vacía. El archivo dañado se conserva en la carpeta de la base de datos.")


# ── Clase principal ──────────────────────────────────────────────────────────

class BaseDatos:
	"""Acceso a una base SQLite desde varios hilos, con migraciones y copias.

	:param ruta: archivo (por defecto ``data/db/app.db``).
	:param migraciones: lista de ``Migracion`` (o SQL, o funciones) en orden.
	:param carpeta_copias: por defecto ``data/backups``.
	:param copias_conservadas: copias por base que se guardan (rotación).
	:param espera_bloqueo_ms: ``busy_timeout``.
	:param reintentos_bloqueo: intentos al abrir si otro proceso la bloquea.
	:param comprobar_al_abrir: ``quick_check`` al abrir (recomendado).
	"""

	def __init__(
		self,
		ruta: Path | str | None = None,
		migraciones: Sequence[Migracion | str | Callable[[sqlite3.Connection], None]] = (),
		*,
		carpeta_copias: Path | str | None = None,
		copias_conservadas: int = 10,
		espera_bloqueo_ms: int = 5000,
		reintentos_bloqueo: int = 5,
		comprobar_al_abrir: bool = True,
	) -> None:
		self.ruta = Path(ruta) if ruta is not None else carpeta_datos() / "db" / NOMBRE_POR_DEFECTO
		self.carpeta_copias = Path(carpeta_copias) if carpeta_copias is not None else carpeta_datos() / "backups"
		self._migraciones = [self._normalizar(m, i) for i, m in enumerate(migraciones, start=1)]
		self._conservadas = max(1, copias_conservadas)
		self._espera_ms = espera_bloqueo_ms
		self._reintentos = max(1, reintentos_bloqueo)
		self._comprobar = comprobar_al_abrir
		self._candado = threading.RLock()
		self._local = threading.local()
		self._lecturas: list[sqlite3.Connection] = []
		self._candado_lecturas = threading.Lock()
		# Al restaurar o compactar se cierran las lecturas; cada hilo detecta el
		# cambio de generación y abre una conexión nueva.
		self._generacion = 0
		self._cerrada = False
		#: Información para la interfaz si hubo que recuperar la base al abrir.
		self.recuperacion: Recuperacion | None = None
		self.ruta.parent.mkdir(parents=True, exist_ok=True)
		self._escritura = self._abrir_con_recuperacion()
		self._migrar()

	@staticmethod
	def _normalizar(migracion: Migracion | str | Callable, numero: int) -> Migracion:
		if isinstance(migracion, Migracion):
			return migracion
		if isinstance(migracion, str):
			return Migracion(f"migración {numero}", sql=migracion)
		if callable(migracion):
			return Migracion(getattr(migracion, "__name__", f"migración {numero}"), funcion=migracion)
		raise TypeError(f"Migración {numero} no válida: {migracion!r}")

	# ── Conexiones ───────────────────────────────────────────────────────────

	def _conectar(self, solo_lectura: bool = False) -> sqlite3.Connection:
		conexion = sqlite3.connect(
			str(self.ruta),
			timeout=self._espera_ms / 1000,
			isolation_level=None,  # autocommit: las transacciones las abre transaccion()
			check_same_thread=False,  # se protege con candados propios
		)
		try:
			conexion.row_factory = sqlite3.Row
			conexion.execute(f"PRAGMA busy_timeout = {int(self._espera_ms)}")
			conexion.execute("PRAGMA foreign_keys = ON")
			if solo_lectura:
				conexion.execute("PRAGMA query_only = ON")
			else:
				modo = conexion.execute("PRAGMA journal_mode = WAL").fetchone()[0]
				if str(modo).lower() != "wal":
					# Unidades de red o solo lectura no admiten WAL: se sigue, pero se avisa.
					log.warning("SQLite no pudo activar WAL (modo %s) en %s", modo, self.ruta)
				conexion.execute("PRAGMA synchronous = NORMAL")
				if sqlite3.sqlite_version_info >= (3, 46, 0):
					# Recomendación de SQLite para conexiones de larga duración.
					conexion.execute("PRAGMA optimize = 0x10002")
			return conexion
		except BaseException:
			conexion.close()
			raise

	def _lectura(self) -> sqlite3.Connection:
		"""Conexión de lectura propia del hilo actual."""
		conexion, generacion = getattr(self._local, "lectura", (None, -1))
		if conexion is None or generacion != self._generacion:
			conexion = self._conectar(solo_lectura=True)
			with self._candado_lecturas:
				self._lecturas.append(conexion)
			self._local.lectura = (conexion, self._generacion)
		return conexion

	def _cerrar_lecturas(self) -> None:
		"""Cierra todas las conexiones de lectura (de todos los hilos)."""
		with self._candado_lecturas:
			lecturas, self._lecturas = self._lecturas, []
			self._generacion += 1
		for conexion in lecturas:
			try:
				conexion.close()
			except sqlite3.Error:
				log.debug("No se pudo cerrar una conexión de lectura", exc_info=True)

	def cerrar_lectura_del_hilo(self) -> None:
		"""Para hilos efímeros: cierra su conexión de lectura al terminar."""
		conexion, _generacion = getattr(self._local, "lectura", (None, -1))
		if conexion is None:
			return
		self._local.lectura = (None, -1)
		with self._candado_lecturas:
			if conexion in self._lecturas:
				self._lecturas.remove(conexion)
		conexion.close()

	def _exigir_abierta(self) -> None:
		if self._cerrada:
			log.warning("Operación sobre la base de datos después de cerrarla (se descarta)")
			raise ErrorBaseDatos("La base de datos ya está cerrada.")

	# ── Apertura, integridad y recuperación ──────────────────────────────────

	def _abrir_con_recuperacion(self) -> sqlite3.Connection:
		espera = 0.2
		for intento in range(1, self._reintentos + 1):
			try:
				conexion = self._conectar()
				if self._comprobar:
					problemas = self._quick_check(conexion)
					if problemas:
						conexion.close()
						raise _BaseDanada("; ".join(problemas[:5]))
				return conexion
			except sqlite3.Error as error:
				if _es_bloqueo(error):
					if intento == self._reintentos:
						raise ErrorBaseDatosBloqueada(
							"La base de datos está bloqueada por otro programa (antivirus, copia de "
							"seguridad u otra instancia). No se ha tocado ningún dato."
						) from error
					log.warning("Base de datos bloqueada; reintento %d de %d", intento, self._reintentos)
					time.sleep(espera)
					espera *= 2
					continue
				if not _es_corrupcion(error):
					raise ErrorBaseDatos(f"No se pudo abrir la base de datos: {error}") from error
				return self._recuperar(str(error))
			except _BaseDanada as error:
				return self._recuperar(f"quick_check: {error}")
		raise ErrorBaseDatos("No se pudo abrir la base de datos.")  # inalcanzable

	@staticmethod
	def _quick_check(conexion: sqlite3.Connection, completa: bool = False) -> list[str]:
		orden = "PRAGMA integrity_check" if completa else "PRAGMA quick_check"
		filas = [str(fila[0]) for fila in conexion.execute(orden).fetchall()]
		return [] if filas == ["ok"] else filas

	def _apartar_danada(self) -> list[Path]:
		"""Aparta la base y sus ``-wal``/``-shm``: todo o nada.

		Si se apartara solo el ``.db``, un ``-wal`` huérfano se aplicaría a la
		base nueva y la estropearía.
		"""
		marca = datetime.now().strftime("%Y%m%d_%H%M%S")
		movidos: list[tuple[Path, Path]] = []
		for sufijo in ("", "-wal", "-shm"):
			origen = Path(f"{self.ruta}{sufijo}")
			if not origen.exists():
				continue
			destino = Path(f"{origen}.danada-{marca}")
			try:
				os.replace(origen, destino)
			except OSError as error:
				for previo_origen, previo_destino in reversed(movidos):
					try:
						os.replace(previo_destino, previo_origen)
					except OSError:
						log.error("No se pudo devolver %s a su sitio", previo_destino, exc_info=True)
				raise ErrorBaseDatos(f"La base está dañada y no se pudo apartar: {error}") from error
			movidos.append((origen, destino))
		return [destino for _origen, destino in movidos]

	def _recuperar(self, motivo: str) -> sqlite3.Connection:
		"""Plan de recuperación: apartar, restaurar la última copia sana o empezar vacía."""
		log.error("Base de datos dañada (%s); se inicia la recuperación", motivo)
		apartados = self._apartar_danada()
		restaurada: Path | None = None
		for copia in reversed(self.copias()):
			if self.integridad_de_archivo(copia):
				log.warning("Copia %s descartada: tampoco está sana", copia.name)
				continue
			origen = sqlite3.connect(copia.resolve().as_uri() + "?mode=ro", uri=True)
			destino = sqlite3.connect(str(self.ruta))
			try:
				origen.backup(destino)
			finally:
				origen.close()
				destino.close()
			restaurada = copia
			break
		self.recuperacion = Recuperacion(motivo, apartados, restaurada)
		if restaurada is not None:
			log.warning("Base restaurada desde la copia %s", restaurada.name)
		else:
			log.error("Sin copias sanas: se crea una base vacía (los datos dañados se conservan)")
		return self._conectar()

	def comprobar_integridad(self, completa: bool = False) -> list[str]:
		"""``[]`` si la base está sana; si no, los problemas encontrados.

		``quick_check`` es O(N); ``integrity_check`` (completa) además revisa los
		índices y tarda más: llámala en un hilo, nunca en el de la interfaz.
		"""
		self._exigir_abierta()
		return self._quick_check(self._lectura(), completa)

	@staticmethod
	def integridad_de_archivo(ruta: Path) -> list[str]:
		"""Comprueba otro archivo (una copia) abriéndolo en solo lectura."""
		try:
			conexion = sqlite3.connect(Path(ruta).resolve().as_uri() + "?mode=ro", uri=True)
		except sqlite3.Error as error:
			return [str(error)]
		try:
			return BaseDatos._quick_check(conexion)
		except sqlite3.Error as error:
			return [str(error)]
		finally:
			conexion.close()

	# ── Migraciones ──────────────────────────────────────────────────────────

	def version(self) -> int:
		"""``PRAGMA user_version`` actual."""
		with self._candado:
			return int(self._escritura.execute("PRAGMA user_version").fetchone()[0])

	def _migrar(self) -> None:
		actual = self.version()
		total = len(self._migraciones)
		if actual > total:
			raise ErrorVersionFutura(
				f"La base de datos es de la versión {actual} y esta aplicación solo conoce hasta la {total}. "
				"Se creó con una versión más nueva de la aplicación; actualízala para abrirla."
			)
		if actual == total:
			return
		if actual > 0:
			# Copia antes de tocar el esquema de una base con datos del usuario.
			self.copia_seguridad(motivo=f"antes-de-migrar-v{actual}")
		for numero in range(actual + 1, total + 1):
			migracion = self._migraciones[numero - 1]
			with self._candado:
				conexion = self._escritura
				if migracion.desactivar_claves_foraneas:
					conexion.execute("PRAGMA foreign_keys = OFF")  # no se puede dentro de BEGIN
				try:
					with self.transaccion() as tx:
						if migracion.sql:
							for sentencia in dividir_sentencias(migracion.sql):
								tx.execute(sentencia)
						if migracion.funcion is not None:
							migracion.funcion(tx)
						if migracion.desactivar_claves_foraneas:
							rotas = tx.execute("PRAGMA foreign_key_check").fetchall()
							if rotas:
								raise ErrorMigracion(f"{len(rotas)} filas incumplen claves foráneas")
						# PRAGMA no admite parámetros; «numero» es un entero nuestro.
						tx.execute(f"PRAGMA user_version = {int(numero)}")
				except Exception as error:
					raise ErrorMigracion(
						f"Falló la migración {numero} ({migracion.descripcion}); la base sigue en la versión "
						f"{numero - 1}: {error}"
					) from error
				finally:
					if migracion.desactivar_claves_foraneas:
						conexion.execute("PRAGMA foreign_keys = ON")
			log.info("Base de datos migrada a la versión %d: %s", numero, migracion.descripcion)

	# ── Transacciones y consultas ────────────────────────────────────────────

	@contextmanager
	def transaccion(self) -> Iterator[sqlite3.Connection]:
		"""Bloque atómico: COMMIT al salir bien, ROLLBACK (y registro) si hay excepción.

		Anidable: dentro de otra transacción del mismo hilo se usa un SAVEPOINT;
		si el bloque interior falla y el exterior captura la excepción, solo se
		deshace lo interior.

		``BEGIN IMMEDIATE`` toma el bloqueo de escritura al empezar: con
		``BEGIN`` a secas, dos procesos que leen y luego escriben pueden
		fallar con «database is locked» a mitad de transacción.
		"""
		self._exigir_abierta()
		profundidad = getattr(self._local, "profundidad", 0)
		if profundidad:
			nombre = f"punto_{profundidad}"
			conexion = self._escritura
			conexion.execute(f"SAVEPOINT {nombre}")
			self._local.profundidad = profundidad + 1
			try:
				yield conexion
			except BaseException:
				conexion.execute(f"ROLLBACK TO {nombre}")
				conexion.execute(f"RELEASE {nombre}")
				raise
			else:
				conexion.execute(f"RELEASE {nombre}")
			finally:
				self._local.profundidad = profundidad
			return

		with self._candado:
			conexion = self._escritura
			conexion.execute("BEGIN IMMEDIATE")
			self._local.profundidad = 1
			try:
				yield conexion
				conexion.execute("COMMIT")
			except BaseException as error:
				if conexion.in_transaction:
					try:
						conexion.execute("ROLLBACK")
					except sqlite3.Error:
						log.error("Falló el ROLLBACK", exc_info=True)
				# Sin traza: quien maneje la excepción la registrará completa.
				log.warning("Transacción deshecha por %s: %s", type(error).__name__, error)
				raise
			finally:
				self._local.profundidad = 0

	def _en_transaccion(self) -> bool:
		return bool(getattr(self._local, "profundidad", 0))

	def ejecutar(self, sql: str, parametros: Parametros = ()) -> int:
		"""INSERT/UPDATE/DELETE con parámetros. Devuelve las filas afectadas."""
		with self.transaccion() as conexion:
			try:
				return conexion.execute(sql, parametros).rowcount
			except sqlite3.Error as error:
				_anotar(error, sql, parametros)
				raise

	def insertar(self, sql: str, parametros: Parametros = ()) -> int:
		"""INSERT que devuelve el ``rowid`` creado (en la misma transacción, sin carreras)."""
		with self.transaccion() as conexion:
			try:
				return int(conexion.execute(sql, parametros).lastrowid)
			except sqlite3.Error as error:
				_anotar(error, sql, parametros)
				raise

	def ejecutar_lote(self, sql: str, filas: Iterable[Parametros]) -> int:
		"""``executemany`` en UNA transacción: miles de filas en milisegundos."""
		with self.transaccion() as conexion:
			try:
				return conexion.executemany(sql, filas).rowcount
			except sqlite3.Error as error:
				_anotar(error, sql, None)
				raise

	def _conexion_consulta(self) -> sqlite3.Connection:
		# Dentro de una transacción de este hilo se lee por la conexión de
		# escritura: así se ven los cambios aún sin confirmar.
		return self._escritura if self._en_transaccion() else self._lectura()

	def consultar(self, sql: str, parametros: Parametros = ()) -> list[sqlite3.Row]:
		"""SELECT con parámetros. Filas ``sqlite3.Row`` (acceso por nombre: ``fila["titulo"]``)."""
		self._exigir_abierta()
		try:
			return self._conexion_consulta().execute(sql, parametros).fetchall()
		except sqlite3.Error as error:
			_anotar(error, sql, parametros)
			raise

	def consultar_uno(self, sql: str, parametros: Parametros = ()) -> sqlite3.Row | None:
		"""Primera fila o ``None``."""
		self._exigir_abierta()
		try:
			return self._conexion_consulta().execute(sql, parametros).fetchone()
		except sqlite3.Error as error:
			_anotar(error, sql, parametros)
			raise

	def valor(self, sql: str, parametros: Parametros = (), por_defecto: Any = None) -> Any:
		"""Primera columna de la primera fila (``COUNT(*)``, ``MAX(...)``)."""
		fila = self.consultar_uno(sql, parametros)
		return por_defecto if fila is None else fila[0]

	def iterar(self, sql: str, parametros: Parametros = (), tamano: int = 500) -> Iterator[sqlite3.Row]:
		"""Recorre resultados grandes por tandas sin cargarlos todos en memoria.

		Mantiene abierta una lectura mientras se recorre: consúmelo entero y
		en un hilo de fondo (exportaciones, informes).
		"""
		self._exigir_abierta()
		cursor = self._lectura().execute(sql, parametros)
		try:
			while True:
				tanda = cursor.fetchmany(tamano)
				if not tanda:
					return
				yield from tanda
		finally:
			cursor.close()

	# ── Copias de seguridad ──────────────────────────────────────────────────

	def copias(self) -> list[Path]:
		"""Copias de esta base, de la más antigua a la más reciente."""
		if not self.carpeta_copias.is_dir():
			return []
		return sorted(self.carpeta_copias.glob(f"{self.ruta.stem}-*.db"))

	def copia_seguridad(self, motivo: str = "manual") -> Path:
		"""Copia consistente EN CALIENTE con la API ``backup`` de SQLite.

		Copiar el archivo con ``shutil`` mientras la aplicación escribe puede dar
		una copia inservible (y en WAL faltarían los cambios del ``-wal``). Las
		escrituras esperan mientras dura la copia; las lecturas siguen.
		"""
		self._exigir_abierta()
		if self._en_transaccion():
			raise ErrorBaseDatos("No se puede copiar la base dentro de una transacción.")
		self.carpeta_copias.mkdir(parents=True, exist_ok=True)
		motivo_limpio = re.sub(r"[^A-Za-z0-9_-]+", "-", motivo).strip("-") or "copia"
		marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
		destino = self.carpeta_copias / f"{self.ruta.stem}-{marca}-{motivo_limpio}.db"
		temporal = destino.with_name(destino.name + ".tmp")
		with self._candado:
			conexion_destino = sqlite3.connect(str(temporal))
			try:
				self._escritura.backup(conexion_destino)
			finally:
				conexion_destino.close()
		# Solo con la copia completa aparece con su nombre definitivo.
		os.replace(temporal, destino)
		log.info("Copia de seguridad creada: %s", destino.name)
		self._rotar_copias()
		return destino

	def _rotar_copias(self) -> None:
		sobrantes = self.copias()[:-self._conservadas]
		for copia in sobrantes:
			try:
				copia.unlink()
			except OSError:
				log.warning("No se pudo borrar la copia antigua %s", copia.name, exc_info=True)

	def restaurar(self, copia: Path | str) -> None:
		"""Sustituye el contenido de la base por el de ``copia``.

		Antes comprueba la copia y guarda el estado actual como copia
		«antes-de-restaurar». Detén los hilos de fondo que escriban antes de
		llamarla. Después se aplican las migraciones que le falten a la copia.
		"""
		self._exigir_abierta()
		copia = Path(copia)
		problemas = self.integridad_de_archivo(copia)
		if problemas:
			raise ErrorBaseDatos(f"La copia {copia.name} no está sana: {'; '.join(problemas[:3])}")
		self.copia_seguridad(motivo="antes-de-restaurar")
		with self._candado:
			self._cerrar_lecturas()
			origen = sqlite3.connect(copia.resolve().as_uri() + "?mode=ro", uri=True)
			try:
				origen.backup(self._escritura)
			finally:
				origen.close()
			self._escritura.execute("PRAGMA journal_mode = WAL")
		log.warning("Base de datos restaurada desde %s", copia.name)
		self._migrar()

	# ── Mantenimiento ────────────────────────────────────────────────────────

	def espacio_libre(self) -> tuple[int, int]:
		"""(bytes recuperables por VACUUM, tamaño total de la base)."""
		with self._candado:
			libres = self._escritura.execute("PRAGMA freelist_count").fetchone()[0]
			paginas = self._escritura.execute("PRAGMA page_count").fetchone()[0]
			tamano = self._escritura.execute("PRAGMA page_size").fetchone()[0]
		return int(libres) * int(tamano), int(paginas) * int(tamano)

	def compactar_si_procede(self, minimo_mb: float = 20, proporcion: float = 0.25) -> bool:
		"""VACUUM si hay mucho espacio muerto. Solo AL ARRANCAR, antes de lanzar hilos.

		VACUUM reescribe toda la base (segundos en bases grandes), necesita
		espacio libre en disco del orden de su tamaño y bloquea las escrituras
		mientras dura: nunca en el hilo de la interfaz durante el uso.
		"""
		recuperable, total = self.espacio_libre()
		if recuperable < minimo_mb * 1024 * 1024 or (total and recuperable < total * proporcion):
			return False
		with self._candado:
			self._cerrar_lecturas()
			antes = self.ruta.stat().st_size
			self._escritura.execute("VACUUM")
			self._escritura.execute("PRAGMA wal_checkpoint(TRUNCATE)")
			despues = self.ruta.stat().st_size
		log.info("Base compactada: %.1f MB → %.1f MB", antes / 1048576, despues / 1048576)
		return True

	def optimizar(self) -> None:
		"""``PRAGMA optimize`` (actualiza estadísticas del planificador). Barato."""
		with self._candado:
			self._escritura.execute("PRAGMA optimize")

	# ── Cierre ───────────────────────────────────────────────────────────────

	def cerrar(self) -> None:
		"""Cierre ordenado: optimize, checkpoint del WAL y cierre de todas las conexiones.

		Idempotente. Tras cerrar, cualquier operación lanza ``ErrorBaseDatos``
		y deja un aviso en el registro (una escritura tardía no se pierde en
		silencio).
		"""
		with self._candado:
			if self._cerrada:
				return
			self._cerrada = True
			self._cerrar_lecturas()
			try:
				self._escritura.execute("PRAGMA optimize")
				self._escritura.execute("PRAGMA wal_checkpoint(TRUNCATE)")
			except sqlite3.Error:
				log.warning("No se pudo completar el cierre ordenado de la base", exc_info=True)
			finally:
				self._escritura.close()
		log.info("Base de datos cerrada: %s", self.ruta.name)

	def __enter__(self) -> BaseDatos:
		return self

	def __exit__(self, *_excepcion: object) -> None:
		self.cerrar()


def copiar_archivo_cerrado(origen: Path, destino: Path) -> None:
	"""Copia un archivo de base CERRADO con sus ``-wal``/``-shm`` (herramientas externas).

	Con la base abierta usa ``BaseDatos.copia_seguridad``.
	"""
	for sufijo in ("", "-wal", "-shm"):
		archivo = Path(f"{origen}{sufijo}")
		if archivo.exists():
			shutil.copy2(archivo, Path(f"{destino}{sufijo}"))
