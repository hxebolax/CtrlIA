"""Procesos hijo de los agentes: lanzarlos, hablar por líneas y matarlos enteros.

- En Windows cada agente va dentro de un *Job Object* con
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``: al cerrar el job (o al cerrarse
  Ctrl+IA, aunque sea de golpe) muere el árbol completo, nietos incluidos.
  Sin esto, un ``npm`` o un servidor que lance el agente quedaría huérfano.
- Sin ventana de consola (``CREATE_NO_WINDOW``).
- Tuberías binarias: se parte SOLO por ``\\n`` (``str.splitlines`` cortaría
  también en U+2028, que los modelos escriben a veces dentro del JSON).
- El error estándar se drena en un hilo propio (las últimas 200 líneas) para
  que el hijo nunca se bloquee por tener la tubería llena.
- Los hilos lectores bajan su prioridad de Windows: la interfaz y el lector
  de pantalla siempre ganan el reparto de CPU.

Búsqueda de ejecutables: solo binarios nativos. Los lanzadores ``.cmd``,
``.bat`` y ``.ps1`` se rechazan porque ``cmd.exe`` reinterpreta caracteres
como ``&`` o ``"`` de los argumentos (un título de sesión podría ejecutar
órdenes). Si el único rastro es un lanzador de npm, se busca el ``.exe`` real
al que apunta.
"""

from __future__ import annotations

import collections
import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from ctypes import wintypes
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ES_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200

_MAX_LINEAS_ERROR = 200


# --------------------------------------------------------------------------
# Entorno del hijo
# --------------------------------------------------------------------------


def entorno_hijo(
	quitar: Iterable[str] = (),
	prefijos: Iterable[str] = (),
	anadir: dict[str, str] | None = None,
) -> dict[str, str]:
	"""Copia del entorno sin las variables indicadas (sin distinguir mayúsculas).

	Se usa para que un agente no herede, por ejemplo, una clave de API que
	haría que cobrase por uso en lugar de usar la suscripción del usuario.
	"""
	quitar_mayus = {nombre.upper() for nombre in quitar}
	prefijos_mayus = tuple(prefijo.upper() for prefijo in prefijos)
	resultado = {
		clave: valor
		for clave, valor in os.environ.items()
		if clave.upper() not in quitar_mayus and not (prefijos_mayus and clave.upper().startswith(prefijos_mayus))
	}
	if anadir:
		resultado.update(anadir)
	return resultado


# --------------------------------------------------------------------------
# Búsqueda de ejecutables
# --------------------------------------------------------------------------

_LANZADORES = {".cmd", ".bat", ".ps1"}
_RE_SHIM_NPM = re.compile(r'"%~?dp0%?\\([^"]*?\.exe)"', re.IGNORECASE)


def es_binario_nativo(ruta: Path) -> bool:
	"""``True`` si el archivo empieza por la firma de un ejecutable (MZ, ELF o Mach-O)."""
	try:
		with ruta.open("rb") as archivo:
			cabecera = archivo.read(4)
	except OSError:
		log.debug("No se pudo leer la cabecera de %s", ruta, exc_info=True)
		return False
	return cabecera[:2] == b"MZ" or cabecera == b"\x7fELF" or cabecera in (
		b"\xcf\xfa\xed\xfe",
		b"\xce\xfa\xed\xfe",
		b"\xca\xfe\xba\xbe",
	)


def _exe_tras_lanzador(lanzador: Path) -> Path | None:
	"""Si ``lanzador`` es un ``.cmd`` de npm, devuelve el ``.exe`` al que llama."""
	try:
		texto = lanzador.read_text(encoding="utf-8", errors="replace")
	except OSError:
		return None
	coincidencia = _RE_SHIM_NPM.search(texto)
	if not coincidencia:
		return None
	candidato = (lanzador.parent / coincidencia.group(1)).resolve()
	return candidato if candidato.is_file() and es_binario_nativo(candidato) else None


def buscar_ejecutable(nombres: Sequence[str], candidatos: Iterable[Path] = ()) -> Path | None:
	"""Busca un ejecutable nativo: primero en el PATH, después en ``candidatos``.

	Nunca devuelve un ``.cmd``/``.bat``/``.ps1``; de un lanzador de npm se
	extrae el ``.exe`` real si lo hay.
	"""
	for nombre in nombres:
		encontrado = shutil.which(nombre)
		if not encontrado:
			continue
		ruta = Path(encontrado)
		if ruta.suffix.lower() in _LANZADORES:
			real = _exe_tras_lanzador(ruta)
			if real:
				return real
			log.debug("Se descarta el lanzador %s (no es un binario nativo)", ruta)
			continue
		if es_binario_nativo(ruta):
			return ruta
	for candidato in candidatos:
		try:
			if candidato.is_file() and es_binario_nativo(candidato):
				return candidato
		except OSError:
			continue
	return None


def buscar_programa_node(nombre: str, paquete_js: str) -> list[str] | None:
	"""Orden para un agente publicado solo como paquete de npm (sin ``.exe``).

	Devuelve ``[node.exe, ruta/al/script.js]`` leyendo el lanzador de npm, de
	modo que nunca se pasa por ``cmd.exe``.
	"""
	node = buscar_ejecutable(["node"])
	if node is None:
		return None
	encontrado = shutil.which(nombre)
	carpetas = []
	if encontrado:
		carpetas.append(Path(encontrado).parent)
	appdata = os.environ.get("APPDATA")  # auditoria: ignorar ruta-no-portable - localiza programas instalados por npm; no guarda datos
	if appdata:
		carpetas.append(Path(appdata) / "npm")
	for carpeta in carpetas:
		script = carpeta / "node_modules" / paquete_js
		if script.is_file():
			return [str(node), str(script)]
	return None


def version_de(orden: Sequence[str], tiempo: float = 20.0) -> str:
	"""Ejecuta ``orden`` (normalmente ``X --version``) y devuelve la primera línea."""
	try:
		resultado = subprocess.run(  # noqa: S603 - orden construida por la aplicación, sin shell
			list(orden),
			capture_output=True,
			timeout=tiempo,
			creationflags=CREATE_NO_WINDOW if ES_WINDOWS else 0,
			stdin=subprocess.DEVNULL,
			check=False,
		)
	except (OSError, subprocess.SubprocessError):
		log.debug("No se pudo obtener la versión con %s", orden, exc_info=True)
		return ""
	texto = (resultado.stdout or resultado.stderr or b"").decode("utf-8", "replace").strip()
	return texto.splitlines()[0].strip() if texto else ""


def ejecutar_y_leer(orden: Sequence[str], tiempo: float = 30.0, entorno: dict[str, str] | None = None) -> tuple[int, str, str]:
	"""Ejecuta una orden corta y devuelve ``(código, salida, error)``."""
	resultado = subprocess.run(  # noqa: S603 - orden construida por la aplicación, sin shell
		list(orden),
		capture_output=True,
		timeout=tiempo,
		creationflags=CREATE_NO_WINDOW if ES_WINDOWS else 0,
		stdin=subprocess.DEVNULL,
		env=entorno,
		check=False,
	)
	return (
		resultado.returncode,
		(resultado.stdout or b"").decode("utf-8", "replace"),
		(resultado.stderr or b"").decode("utf-8", "replace"),
	)


def abrir_en_consola(orden: Sequence[str], carpeta: Path | None = None,
		al_terminar: Callable[[int], None] | None = None) -> bool:
	"""Lanza una orden interactiva en una consola NUEVA (instalar, iniciar sesión...).

	:param al_terminar: se llama (desde otro hilo) con el código de salida cuando
		la consola se cierra; por ejemplo, para volver a comprobar el agente.
	"""
	try:
		proceso = subprocess.Popen(  # noqa: S603 - orden fija de la aplicación, sin shell
			list(orden),
			cwd=str(carpeta) if carpeta else None,
			creationflags=CREATE_NEW_CONSOLE if ES_WINDOWS else 0,
		)
	except OSError:
		log.exception("No se pudo abrir la consola con %s", orden[:1])
		return False
	if al_terminar is not None:
		def esperar() -> None:
			codigo = proceso.wait()
			try:
				al_terminar(codigo)
			except Exception:  # noqa: BLE001 - el aviso de fin nunca debe tumbar el hilo sin dejar rastro
				log.exception("Falló el aviso de fin de la consola de %s", orden[:1])

		threading.Thread(target=esperar, name="consola", daemon=True).start()
	return True


def lanzar_independiente(orden: Sequence[str], carpeta: Path | None = None, entorno: dict[str, str] | None = None) -> bool:
	"""Lanza un programa externo (un editor, un IDE) sin atarlo a Ctrl+IA."""
	try:
		subprocess.Popen(  # noqa: S603 - orden fija de la aplicación, sin shell
			list(orden),
			cwd=str(carpeta) if carpeta else None,
			env=entorno,
			creationflags=(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if ES_WINDOWS else 0,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			close_fds=True,
		)
	except OSError:
		log.exception("No se pudo lanzar %s", orden[:1])
		return False
	return True


# --------------------------------------------------------------------------
# Job Object (Windows)
# --------------------------------------------------------------------------


class _JobObject:
	"""Grupo de procesos que muere entero al cerrarse."""

	JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
	_CLASE_LIMITES_EXTENDIDOS = 9

	def __init__(self) -> None:
		self._manejador = None
		if not ES_WINDOWS:
			return
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		kernel32.CreateJobObjectW.restype = wintypes.HANDLE
		kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
		manejador = kernel32.CreateJobObjectW(None, None)
		if not manejador:
			log.warning("No se pudo crear el Job Object (error %s)", ctypes.get_last_error())
			return

		class _Basicos(ctypes.Structure):
			_fields_ = [
				("PerProcessUserTimeLimit", ctypes.c_int64),
				("PerJobUserTimeLimit", ctypes.c_int64),
				("LimitFlags", wintypes.DWORD),
				("MinimumWorkingSetSize", ctypes.c_size_t),
				("MaximumWorkingSetSize", ctypes.c_size_t),
				("ActiveProcessLimit", wintypes.DWORD),
				("Affinity", ctypes.c_size_t),
				("PriorityClass", wintypes.DWORD),
				("SchedulingClass", wintypes.DWORD),
			]

		class _ContadoresES(ctypes.Structure):
			_fields_ = [(nombre, ctypes.c_uint64) for nombre in (
				"ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
				"ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
			)]

		class _Extendidos(ctypes.Structure):
			_fields_ = [
				("BasicLimitInformation", _Basicos),
				("IoInfo", _ContadoresES),
				("ProcessMemoryLimit", ctypes.c_size_t),
				("JobMemoryLimit", ctypes.c_size_t),
				("PeakProcessMemoryUsed", ctypes.c_size_t),
				("PeakJobMemoryUsed", ctypes.c_size_t),
			]

		info = _Extendidos()
		info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
		kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
		if not kernel32.SetInformationJobObject(
			manejador, self._CLASE_LIMITES_EXTENDIDOS, ctypes.byref(info), ctypes.sizeof(info)
		):
			log.warning("No se pudo configurar el Job Object (error %s)", ctypes.get_last_error())
			kernel32.CloseHandle(manejador)
			return
		self._kernel32 = kernel32
		self._manejador = manejador

	def asignar(self, proceso: subprocess.Popen) -> bool:
		if not self._manejador:
			return False
		kernel32 = self._kernel32
		kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
		manejador_proceso = wintypes.HANDLE(int(proceso._handle))  # noqa: SLF001 - único acceso al HANDLE en Windows
		if not kernel32.AssignProcessToJobObject(self._manejador, manejador_proceso):
			log.warning("No se pudo meter el proceso %s en el Job Object (error %s)", proceso.pid, ctypes.get_last_error())
			return False
		return True

	def terminar(self) -> None:
		if self._manejador:
			self._kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
			self._kernel32.TerminateJobObject(self._manejador, 1)

	def cerrar(self) -> None:
		if self._manejador:
			self._kernel32.CloseHandle(self._manejador)
			self._manejador = None


def _reanudar(proceso: subprocess.Popen) -> None:
	"""Reanuda un proceso creado con ``CREATE_SUSPENDED`` (ya dentro del job)."""
	ntdll = ctypes.WinDLL("ntdll")
	ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
	ntdll.NtResumeProcess(wintypes.HANDLE(int(proceso._handle)))  # noqa: SLF001


def bajar_prioridad_hilo_actual() -> None:
	"""Prioridad de Windows inferior a la normal para el hilo que llama."""
	if not ES_WINDOWS:
		return
	try:
		kernel32 = ctypes.windll.kernel32
		kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1)
	except (AttributeError, OSError):
		log.debug("No se pudo bajar la prioridad del hilo", exc_info=True)


# --------------------------------------------------------------------------
# Proceso hijo con protocolo de líneas
# --------------------------------------------------------------------------


class PartidorLineas:
	"""Parte en líneas (por ``\\n``) los bloques de bytes que van llegando, en tiempo lineal.

	El salto de línea solo se busca en el bloque nuevo: lo pendiente de antes
	no puede contenerlo. Lo pendiente vive en un ``bytearray`` que crece sin
	copiarse entero en cada bloque, así que un mensaje de varios megas en una
	sola línea no congela el hilo lector. Se trabaja con bytes: un carácter
	UTF-8 partido entre dos bloques se une antes de decodificar la línea.
	"""

	def __init__(self) -> None:
		self._pendiente = bytearray()

	def anadir(self, bloque: bytes) -> list[bytes]:
		"""Añade un bloque y devuelve las líneas completas (sin el ``\\n``), en orden."""
		posicion = bloque.find(b"\n")
		if posicion < 0:
			self._pendiente += bloque
			return []
		self._pendiente += bloque[:posicion]
		lineas = [bytes(self._pendiente)]
		self._pendiente.clear()
		inicio = posicion + 1
		while (posicion := bloque.find(b"\n", inicio)) >= 0:
			lineas.append(bloque[inicio:posicion])
			inicio = posicion + 1
		self._pendiente += bloque[inicio:]
		return lineas

	def resto(self) -> bytes | None:
		"""La última línea sin salto al cerrarse la salida, o ``None`` si solo quedan espacios."""
		if not self._pendiente.strip():
			return None
		resto = bytes(self._pendiente)
		self._pendiente.clear()
		return resto


class ProcesoLineas:
	"""Proceso hijo con el que se habla por líneas de texto (JSON, normalmente).

	:param orden: programa y argumentos (sin shell).
	:param al_linea: se llama con cada línea de la salida (``str`` sin el ``\\n``),
		desde el hilo lector.
	:param al_terminar: se llama una vez, desde el hilo lector, con el código de
		salida, cuando la salida estándar se cierra.
	"""

	def __init__(
		self,
		orden: Sequence[str],
		al_linea: Callable[[str], None],
		al_terminar: Callable[[int | None], None] | None = None,
		carpeta: Path | None = None,
		entorno: dict[str, str] | None = None,
		nombre: str = "agente",
	) -> None:
		self.orden = list(orden)
		self.nombre = nombre
		self._al_linea = al_linea
		self._al_terminar = al_terminar
		self._carpeta = carpeta
		self._entorno = entorno
		self._proceso: subprocess.Popen | None = None
		self._job: _JobObject | None = None
		self._candado_escritura = threading.Lock()
		self._errores: collections.deque[str] = collections.deque(maxlen=_MAX_LINEAS_ERROR)
		self._terminado = threading.Event()
		self.codigo: int | None = None
		#: ``True`` si lo paró Ctrl+IA (``terminar``), para no confundirlo con un fallo del agente.
		self.detenido_por_ctrlia = False

	# -- Ciclo de vida --------------------------------------------------------

	def iniciar(self) -> None:
		"""Arranca el proceso y los hilos lectores. Lanza ``OSError`` si no puede."""
		banderas = 0
		if ES_WINDOWS:
			banderas = CREATE_NO_WINDOW | CREATE_SUSPENDED
			self._job = _JobObject()
		log.info("Lanzando %s: %s", self.nombre, Path(self.orden[0]).name)
		self._proceso = subprocess.Popen(  # noqa: S603 - orden construida por la aplicación, sin shell
			self.orden,
			stdin=subprocess.PIPE,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			cwd=str(self._carpeta) if self._carpeta else None,
			env=self._entorno,
			creationflags=banderas,
			bufsize=0,
		)
		if ES_WINDOWS:
			if self._job is not None:
				self._job.asignar(self._proceso)
			_reanudar(self._proceso)
		threading.Thread(target=self._leer_salida, name=f"{self.nombre}-salida", daemon=True).start()
		threading.Thread(target=self._leer_errores, name=f"{self.nombre}-errores", daemon=True).start()

	@property
	def vivo(self) -> bool:
		return self._proceso is not None and self._proceso.poll() is None

	@property
	def pid(self) -> int | None:
		return self._proceso.pid if self._proceso else None

	def errores_recientes(self, maximo: int = 20) -> list[str]:
		return list(self._errores)[-maximo:]

	def escribir(self, texto: str) -> bool:
		"""Escribe una línea en la entrada del proceso. ``False`` si ya no acepta."""
		proceso = self._proceso
		if proceso is None or proceso.stdin is None or proceso.poll() is not None:
			return False
		datos = (texto.rstrip("\n") + "\n").encode("utf-8")
		with self._candado_escritura:
			try:
				proceso.stdin.write(datos)
				proceso.stdin.flush()
			except (OSError, ValueError):
				log.debug("No se pudo escribir en %s", self.nombre, exc_info=True)
				return False
		return True

	def escribir_json(self, objeto: Any) -> bool:
		return self.escribir(json.dumps(objeto, ensure_ascii=False, separators=(",", ":")))

	def cerrar_entrada(self) -> None:
		"""Cierra la entrada estándar: los agentes lo entienden como «terminar»."""
		proceso = self._proceso
		if proceso is None or proceso.stdin is None:
			return
		with self._candado_escritura:
			try:
				proceso.stdin.close()
			except OSError:
				log.debug("La entrada de %s ya estaba cerrada", self.nombre, exc_info=True)

	def terminar(self, espera: float = 3.0) -> None:
		"""Mata el proceso y todos sus descendientes. Idempotente."""
		proceso = self._proceso
		if proceso is None:
			return
		if proceso.poll() is None:
			# Lo detiene Ctrl+IA: su código de salida (1) no es un error del agente.
			self.detenido_por_ctrlia = True
			if self._job is not None and self._job._manejador:  # noqa: SLF001
				self._job.terminar()
			else:
				try:
					proceso.kill()
				except OSError:
					log.debug("No se pudo matar %s", self.nombre, exc_info=True)
				if ES_WINDOWS:
					subprocess.run(  # noqa: S603, S607 - herramienta del sistema con argumentos fijos
						["taskkill", "/T", "/F", "/PID", str(proceso.pid)],
						capture_output=True,
						creationflags=CREATE_NO_WINDOW,
						check=False,
					)
		try:
			proceso.wait(timeout=espera)
		except subprocess.TimeoutExpired:
			log.warning("%s no terminó en %.1f s", self.nombre, espera)
		if self._job is not None:
			self._job.cerrar()

	def esperar_fin(self, tiempo: float | None = None) -> bool:
		return self._terminado.wait(tiempo)

	# -- Hilos ----------------------------------------------------------------

	def _leer_salida(self) -> None:
		bajar_prioridad_hilo_actual()
		proceso = self._proceso
		assert proceso is not None and proceso.stdout is not None
		partidor = PartidorLineas()
		try:
			while True:
				bloque = proceso.stdout.read1(65536) if hasattr(proceso.stdout, "read1") else proceso.stdout.read(4096)
				if not bloque:
					break
				for linea in partidor.anadir(bloque):
					self._entregar(linea)
			resto = partidor.resto()
			if resto is not None:
				self._entregar(resto)
		except (OSError, ValueError):
			log.debug("Se cortó la salida de %s", self.nombre, exc_info=True)
		finally:
			try:
				self.codigo = proceso.wait(timeout=10)
			except subprocess.TimeoutExpired:
				self.codigo = None
			self._terminado.set()
			if self.detenido_por_ctrlia:
				log.info("%s detenido por Ctrl+IA (código %s)", self.nombre, self.codigo)
			else:
				log.info("%s terminó con código %s", self.nombre, self.codigo)
			if self._al_terminar is not None:
				try:
					self._al_terminar(self.codigo)
				except Exception:  # noqa: BLE001 - el aviso de fin no puede tumbar el hilo
					log.exception("Fallo en el aviso de fin de %s", self.nombre)

	def _entregar(self, linea: bytes) -> None:
		texto = linea.rstrip(b"\r").decode("utf-8", "replace")
		if not texto.strip():
			return
		try:
			self._al_linea(texto)
		except Exception:  # noqa: BLE001 - una línea mal procesada no corta la conversación
			log.exception("Fallo al procesar una línea de %s", self.nombre)

	def _leer_errores(self) -> None:
		bajar_prioridad_hilo_actual()
		proceso = self._proceso
		assert proceso is not None and proceso.stderr is not None
		try:
			for linea in iter(proceso.stderr.readline, b""):
				texto = linea.decode("utf-8", "replace").rstrip()
				if texto:
					self._errores.append(texto)
					log.debug("%s (stderr): %s", self.nombre, texto[:500])
		except (OSError, ValueError):
			log.debug("Se cortó el error estándar de %s", self.nombre, exc_info=True)


def json_o_nada(linea: str) -> dict[str, Any] | None:
	"""Interpreta una línea como objeto JSON; ``None`` si no lo es (sin excepciones)."""
	linea = linea.strip()
	if not linea.startswith("{"):
		return None
	try:
		objeto = json.loads(linea)
	except (ValueError, RecursionError):
		return None
	return objeto if isinstance(objeto, dict) else None
