"""Puntos de restauración de Ctrl+IA: deshacer los cambios de archivos hasta un mensaje (sin interfaz).

Vale para todos los agentes que trabajan en una carpeta de este equipo (Claude
Code, Codex, Antigravity, Gemini y los ACP): antes de cada mensaje de la persona
se guarda una foto de la carpeta en un repositorio git «sombra» propio de
Ctrl+IA, que NUNCA es el de la persona:

- Vive en ``data/cache/puntos/<sha256(carpeta)[:16]>/repo.git`` (``--git-dir``
  aparte y ``--work-tree`` la carpeta de la sesión). El ``.git`` de la persona no
  se lee ni se escribe (salvo ``rev-parse HEAD`` para saber si cambió).
- Entorno aislado: sin variables ``GIT_*`` heredadas, sin configuración global
  ni de sistema (``GIT_CONFIG_GLOBAL``/``SYSTEM`` a un archivo vacío),
  ``GIT_OPTIONAL_LOCKS=0`` y opciones fijas (``CONFIG_FIJA``): bytes exactos (sin
  autocrlf, sin filtros ni LFS: ``info/attributes``), sin ganchos ni firmas.
- Qué entra: lo que git no ignora con el ``.gitignore`` del proyecto, menos
  ``EXCLUSIONES`` (``info/exclude``) y los archivos de más de ``MAX_TAMANO``
  (``info/grandes``, recalculado en cada punto sin contarse a sí mismo).
- Cada punto: ``add -A`` + ``write-tree`` + ``commit-tree`` huérfano +
  ``update-ref refs/puntos/<sesión>/<nombre>``. El mensaje del commit lleva en
  JSON la fecha, el HEAD del repositorio de la persona y los archivos que no se
  pudieron leer («unable to index file»: abiertos en exclusiva), que al
  restaurar no se tocan nunca.
- Vista previa: ``diff --cached --raw`` contra el punto. A = se borrará (lo
  creó el agente), M = se devolverá, D = se volverá a crear.
- Restaurar: antes, un punto «seguridad» (para «Rehacer»); después ``restore``
  de los M y D con rutas literales y las A se borran en Python sin salir NUNCA
  de la carpeta ni seguir enlaces o uniones.

Limitaciones: un repositorio git anidado o un submódulo entra como «gitlink»,
sin su contenido: sus cambios no se deshacen (se avisa). Los archivos de más de
5 MB y los que estaban abiertos en exclusiva tampoco.

No se crean puntos en la carpeta personal, el Escritorio, Documentos,
Descargas, la raíz de un disco, carpetas de red ni con más de ``MAX_ARCHIVOS``
archivos (``motivo_carpeta``). La parte de las sesiones (cuándo se crea cada
punto, la nota al agente, «Rehacer») está en ``MixinPuntos``, que hereda
``GestorSesiones``.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

#: Segundos que puede esperar un mensaje a que se guarde su punto (si no, sale sin punto).
TIEMPO_PUNTO = 5.0
#: Segundos para la vista previa y para restaurar.
TIEMPO_ORDEN = 120.0
#: Segundos para preparar en segundo plano una carpeta grande (el primer punto).
TIEMPO_PREPARAR = 600.0
#: Con más archivos que estos no se guardan puntos.
MAX_ARCHIVOS = 50_000
#: Los archivos más grandes que esto (bytes) no se guardan en los puntos.
MAX_TAMANO = 5 * 1024 * 1024
#: Días que se conservan los puntos.
DIAS_PUNTOS = 30
#: Puntos de mensajes que se conservan por sesión (los más recientes).
MAX_PUNTOS_SESION = 50
#: Versión mínima de git (``restore --pathspec-from-file``).
VERSION_MINIMA = (2, 26)
#: Tras ver una carpeta con demasiados archivos, no se vuelve a contar en este tiempo (segundos).
PAUSA_DEMASIADOS = 6 * 3600
#: Cuántas rutas se nombran como mucho en un aviso.
MAX_NOMBRES = 10

#: Lo que nunca entra en un punto (además del .gitignore del proyecto).
EXCLUSIONES = (
	".git/", ".ctrlia/", "node_modules/", ".venv/", "venv/", "__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/",
	".ruff_cache/", ".tox/", ".gradle/", ".next/", ".nuxt/", ".cache/", "build/", "dist/", "target/",
)

#: Opciones fijas de cada orden: el resultado no depende de la configuración de nadie.
CONFIG_FIJA = (
	"-c", "core.autocrlf=false",
	"-c", "core.safecrlf=false",
	"-c", "core.eol=lf",
	"-c", "core.quotepath=false",
	"-c", "core.longpaths=true",
	"-c", "core.fscache=true",
	"-c", "core.fsmonitor=false",
	"-c", "core.hooksPath=NUL",
	"-c", "core.ignorecase=true",
	"-c", "core.precomposeunicode=false",
	"-c", "commit.gpgSign=false",
	"-c", "tag.gpgSign=false",
	"-c", "gc.auto=0",
	"-c", "maintenance.auto=false",
	"-c", "core.logAllRefUpdates=false",
	"-c", "diff.renames=false",
	"-c", "status.showUntrackedFiles=all",
	"-c", "safe.directory=*",
	"-c", "user.name=Ctrl+IA",
	"-c", "user.email=puntos@ctrl-ia.invalid",
)

ACCION_DEVOLVER = "devolver"
ACCION_BORRAR = "borrar"
ACCION_RECREAR = "recrear"
#: Columna «Qué pasará» del diálogo.
TEXTOS_ACCION = {
	ACCION_DEVOLVER: "Se devolverá como estaba",
	ACCION_BORRAR: "Se borrará (lo creó el agente)",
	ACCION_RECREAR: "Se volverá a crear",
}
#: Al rehacer, lo que se borra no lo creó el agente: lo había recreado el deshacer.
TEXTOS_ACCION_REHACER = {
	ACCION_DEVOLVER: "Volverá a como lo dejó el agente",
	ACCION_BORRAR: "Se borrará (como lo dejó el agente)",
	ACCION_RECREAR: "Se volverá a crear",
}

#: Nombres reservados de los puntos de una sesión (los de los mensajes son «m<id>»).
PUNTO_FIN = "fin"
PUNTO_SEGURIDAD = "seguridad"

_RE_NO_INDEXADO = re.compile(r"^error: unable to index file '(.+)'$", re.M)
_RE_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_RE_VERSION = re.compile(rb"(\d+)\.(\d+)")
_CREAR_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErrorPuntos(Exception):
	"""Fallo con un mensaje en español listo para la persona."""


class PuntosNoDisponibles(ErrorPuntos):
	"""En esta carpeta o en este equipo no se pueden guardar puntos (el motivo va en el mensaje)."""


class TiempoAgotado(ErrorPuntos):
	"""Git no terminó a tiempo (la carpeta es grande o el disco va lento)."""


# ----------------------------------------------------------------------
# Git: dónde está y con qué entorno se ejecuta
# ----------------------------------------------------------------------

_deteccion: tuple[str | None, str] | None = None
_candado_deteccion = threading.Lock()


def _buscar_git() -> str | None:
	from app.services.git import buscar_git

	ruta = buscar_git()
	return str(ruta) if ruta else None


def reiniciar_deteccion() -> None:
	"""Olvida si hay git (vuelve a buscarlo en la próxima llamada)."""
	global _deteccion
	with _candado_deteccion:
		_deteccion = None


def disponible() -> tuple[bool, str]:
	"""``(True, "")`` si hay un git que vale; si no, ``(False, motivo para la persona)``."""
	global _deteccion
	with _candado_deteccion:
		if _deteccion is None:
			_deteccion = _comprobar_git()
		ruta, motivo = _deteccion
	return ruta is not None, motivo


def _ruta_git() -> str:
	ok, motivo = disponible()
	if not ok or _deteccion is None or _deteccion[0] is None:
		raise PuntosNoDisponibles(motivo)
	return _deteccion[0]


def _comprobar_git() -> tuple[str | None, str]:
	ruta = _buscar_git()
	if not ruta:
		return None, ("No está instalado Git para Windows, que Ctrl+IA necesita para guardar los puntos de restauración. "
			"Instálalo desde git-scm.com y vuelve a abrir Ctrl+IA.")
	try:
		resultado = subprocess.run([ruta, "--version"], capture_output=True, timeout=20, env=entorno(),
			creationflags=_CREAR_SIN_VENTANA, check=False)
	except (OSError, subprocess.SubprocessError) as error:
		return None, f"No se ha podido ejecutar Git ({error}), que Ctrl+IA necesita para los puntos de restauración."
	coincidencia = _RE_VERSION.search(resultado.stdout or b"")
	version = (int(coincidencia.group(1)), int(coincidencia.group(2))) if coincidencia else (0, 0)
	if version < VERSION_MINIMA:
		texto = "%d.%d" % version if coincidencia else "desconocida"
		return None, (f"El Git instalado es demasiado antiguo (versión {texto}): los puntos de restauración necesitan la "
			f"{VERSION_MINIMA[0]}.{VERSION_MINIMA[1]} o posterior. Actualiza Git para Windows.")
	return ruta, ""


def carpeta_puntos() -> Path:
	"""``data/cache/puntos`` (se crea si falta)."""
	from app.core.paths import ruta_cache

	carpeta = ruta_cache() / "puntos"
	carpeta.mkdir(parents=True, exist_ok=True)
	return carpeta


def entorno() -> dict[str, str]:
	"""Entorno de git sin nada heredado: ni variables ``GIT_*`` ni configuración global o de sistema."""
	vacio = carpeta_puntos() / "gitconfig-vacio"
	if not vacio.exists():
		vacio.write_bytes(b"")
	variables = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
	variables.update(
		GIT_CONFIG_NOSYSTEM="1",
		GIT_CONFIG_SYSTEM=str(vacio),
		GIT_CONFIG_GLOBAL=str(vacio),
		GIT_TERMINAL_PROMPT="0",
		GIT_OPTIONAL_LOCKS="0",
		LC_ALL="C",
		LANG="C",
		LANGUAGE="C",
	)
	return variables


# ----------------------------------------------------------------------
# Carpetas donde no se guardan puntos
# ----------------------------------------------------------------------

#: Carpetas conocidas de Windows (pueden estar en OneDrive): nombre para el aviso → (FOLDERID, carpeta de reserva).
_CARPETAS_CONOCIDAS = {
	"el Escritorio": ("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "Desktop"),
	"la carpeta Documentos": ("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", "Documents"),
	"la carpeta Descargas": ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
}


def _carpeta_conocida(guid: str) -> Path | None:
	"""Ruta real de una carpeta conocida de Windows (``SHGetKnownFolderPath``) o ``None``."""
	try:
		datos = uuid.UUID(guid).bytes_le
		identificador = (ctypes.c_ubyte * 16).from_buffer_copy(datos)
		puntero = ctypes.c_wchar_p()
		resultado = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(identificador), 0, None, ctypes.byref(puntero))
		if resultado != 0:
			return None
		valor = puntero.value
		ctypes.windll.ole32.CoTaskMemFree(puntero)
		return Path(valor) if valor else None
	except (AttributeError, OSError, ValueError):
		return None


def _normal(ruta: Path) -> str:
	try:
		return os.path.normcase(os.path.realpath(str(ruta))).rstrip("\\/")
	except (OSError, ValueError):
		return os.path.normcase(os.path.abspath(str(ruta))).rstrip("\\/")


def _es_unidad_de_red(ruta: Path) -> bool:
	try:
		return ctypes.windll.kernel32.GetDriveTypeW(ruta.anchor) == 4  # DRIVE_REMOTE
	except (AttributeError, OSError):
		return False


def motivo_carpeta(carpeta: str | Path) -> str:
	"""Por qué no se guardan puntos en ``carpeta`` (``""`` si se pueden guardar)."""
	ruta = Path(str(carpeta or ""))
	texto = str(ruta)
	if texto.startswith(("\\\\", "//")):
		return "La carpeta de la sesión es una carpeta de red: ahí no se guardan puntos de restauración."
	if not texto or not ruta.is_absolute():
		return "La sesión no tiene una carpeta de trabajo completa."
	if not ruta.is_dir():
		return "La carpeta de la sesión ya no existe."
	real = _normal(ruta)
	if ruta.parent == ruta or real == _normal(Path(ruta.anchor)):
		return "La carpeta de la sesión es la raíz de un disco: ahí no se guardan puntos de restauración."
	if _es_unidad_de_red(ruta):
		return "La carpeta de la sesión está en una unidad de red: ahí no se guardan puntos de restauración."
	especiales: list[tuple[str, Path | None]] = [("tu carpeta personal", Path.home())]
	for nombre, (guid, reserva) in _CARPETAS_CONOCIDAS.items():
		especiales.append((nombre, _carpeta_conocida(guid)))
		especiales.append((nombre, Path.home() / reserva))
	for nombre, especial in especiales:
		if especial is not None and _normal(especial) == real:
			return (f"La carpeta de la sesión es {nombre}, que suele tener muchos archivos que no son del proyecto: ahí no se "
				"guardan puntos de restauración. Trabaja en la carpeta del proyecto.")
	return ""


# ----------------------------------------------------------------------
# Rutas seguras dentro de la carpeta
# ----------------------------------------------------------------------


def _es_enlace(ruta: Path) -> bool:
	"""Enlace simbólico o unión de Windows (junction): nunca se sigue."""
	try:
		if ruta.is_symlink():
			return True
		es_union = getattr(ruta, "is_junction", None)
		return bool(es_union()) if es_union is not None else False
	except OSError:
		return True


def ruta_dentro(raiz: Path, relativa: str) -> Path | None:
	"""``raiz/relativa`` si está DENTRO de ``raiz`` sin pasar por enlaces ni uniones; si no, ``None``.

	Rechaza rutas absolutas, ``..``, ``:`` (flujos alternativos o unidades) y
	todo lo que esté dentro de una carpeta ``.git``.
	"""
	if not relativa or "\\" in relativa or ":" in relativa or relativa.startswith("/"):
		return None
	partes = PurePosixPath(relativa).parts
	if not partes or any(p in ("", ".", "..") or p.casefold() == ".git" for p in partes):
		return None
	actual = raiz
	for parte in partes[:-1]:
		actual = actual / parte
		if _es_enlace(actual) or not actual.is_dir():
			return None
	destino = actual / partes[-1]
	base = _normal(raiz)
	padre = _normal(destino.parent)
	if padre != base and not padre.startswith(base + os.sep):
		return None
	return destino


def borrar_seguro(raiz: Path, relativa: str) -> str | None:
	"""Borra un archivo creado por el agente sin salir de ``raiz``. Devuelve el motivo si no se pudo."""
	destino = ruta_dentro(raiz, relativa)
	if destino is None:
		return "Está fuera de la carpeta de la sesión o detrás de un enlace: no se toca."
	try:
		if _es_enlace(destino):
			# El enlace en sí, nunca lo que hay detrás.
			if destino.is_dir() and not destino.is_symlink():
				os.rmdir(destino)
			else:
				os.unlink(destino)
		elif destino.is_dir():
			return "Ahora es una carpeta: no se toca."
		elif os.path.lexists(destino):
			try:
				destino.unlink()
			except PermissionError:
				os.chmod(destino, stat.S_IWRITE)
				destino.unlink()
	except OSError as error:
		return f"No se pudo borrar ({error.strerror or error})."
	# Carpetas que se han quedado vacías, hasta la raíz (ya comprobadas: sin enlaces).
	carpeta = destino.parent
	base = _normal(raiz)
	while _normal(carpeta) != base and _normal(carpeta).startswith(base + os.sep):
		try:
			if any(carpeta.iterdir()):
				break
			carpeta.rmdir()
		except OSError:
			break
		carpeta = carpeta.parent
	return None


# ----------------------------------------------------------------------
# El repositorio sombra
# ----------------------------------------------------------------------

_candados: dict[str, threading.RLock] = {}
_candado_registro = threading.Lock()


def _candado_de(git_dir: Path) -> threading.RLock:
	with _candado_registro:
		return _candados.setdefault(os.path.normcase(str(git_dir)), threading.RLock())


def ruta_repositorio(carpeta: str | Path) -> Path:
	"""``data/cache/puntos/<sha256 de la carpeta>[:16]/repo.git``."""
	clave = os.path.normcase(os.path.abspath(str(carpeta))).rstrip("\\/")
	return carpeta_puntos() / sha256(clave.encode("utf-8")).hexdigest()[:16] / "repo.git"


def _componente(texto: str) -> str:
	limpio = re.sub(r"[^A-Za-z0-9_-]", "_", str(texto)).strip("_")
	return limpio or "x"


def nombre_ref(sesion: str, nombre: str) -> str:
	"""``refs/puntos/<sesión>/<nombre>`` con solo caracteres seguros."""
	return f"refs/puntos/{_componente(sesion)}/{_componente(nombre)}"


def _patron_literal(ruta: str) -> str:
	"""Patrón de exclusión que solo casa con ``ruta`` (anclado y con los comodines escapados)."""
	escapado = re.sub(r"([\\*?\[\]!#])", r"\\\1", ruta)
	if escapado.endswith(" "):
		escapado = escapado[:-1] + "\\ "
	return "/" + escapado


def _validar_commit(commit: str) -> str:
	commit = str(commit or "").strip().lower()
	if not _RE_COMMIT.fullmatch(commit):
		raise ErrorPuntos("El punto de restauración guardado no es válido.")
	return commit


def _lista(rutas: Sequence[str]) -> str:
	"""«a, b y c» con como mucho ``MAX_NOMBRES`` nombres."""
	nombres = list(rutas[:MAX_NOMBRES])
	resto = len(rutas) - len(nombres)
	if resto > 0:
		return ", ".join(nombres) + f" y {resto} más"
	if len(nombres) > 1:
		return ", ".join(nombres[:-1]) + " y " + nombres[-1]
	return nombres[0] if nombres else ""


def archivos(n: int) -> str:
	return "1 archivo" if n == 1 else f"{n} archivos"


@dataclass(slots=True)
class Punto:
	commit: str
	#: Archivos que no se pudieron leer (abiertos en exclusiva): no están en el punto y no se tocan al restaurar.
	no_capturados: list[str] = field(default_factory=list)
	#: Archivos de más de ``MAX_TAMANO`` que no se guardan.
	grandes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Cambio:
	ruta: str
	accion: str

	def texto(self, rehacer: bool = False) -> str:
		return (TEXTOS_ACCION_REHACER if rehacer else TEXTOS_ACCION).get(self.accion, self.accion)


@dataclass(slots=True)
class VistaPrevia:
	commit: str
	fecha: float
	cambios: list[Cambio]
	#: Archivos no protegidos: no se pudieron leer al guardar el punto o ahora.
	no_protegidos: list[str] = field(default_factory=list)
	#: Repositorios git anidados o submódulos (sus cambios no se deshacen).
	anidados: list[str] = field(default_factory=list)
	#: Archivos que cambiaron después del último turno del agente (a mano o con otro programa).
	a_mano: list[str] = field(default_factory=list)
	head_antes: str = ""
	head_ahora: str = ""
	#: Archivos de más de 5 MB nuevos o cambiados desde el punto (no se tocan).
	grandes: list[str] = field(default_factory=list)

	@property
	def head_cambiado(self) -> bool:
		return bool(self.head_antes) and self.head_antes != self.head_ahora


@dataclass(slots=True)
class Resultado:
	devueltos: list[str] = field(default_factory=list)
	borrados: list[str] = field(default_factory=list)
	fallidos: list[tuple[str, str]] = field(default_factory=list)
	#: Punto con el estado de antes de restaurar (para «Rehacer»).
	seguridad: str = ""

	@property
	def total(self) -> int:
		return len(self.devueltos) + len(self.borrados)


class Sombra:
	"""El repositorio sombra de una carpeta. Cada orden toma el candado del repositorio."""

	def __init__(self, carpeta: str | Path, git_dir: Path | None = None) -> None:
		self.git = _ruta_git()
		self.arbol = Path(str(carpeta))
		self.git_dir = git_dir or ruta_repositorio(carpeta)
		self.candado = _candado_de(self.git_dir)
		self.env = entorno()
		self._limite: float | None = None

	# -- Ejecutar git -----------------------------------------------------

	def _restante(self) -> float | None:
		if self._limite is None:
			return None
		restante = self._limite - time.monotonic()
		if restante <= 0:
			raise TiempoAgotado("Git no ha terminado a tiempo de guardar el punto de restauración.")
		return restante

	def orden(self, *argumentos: str, entrada: bytes | None = None, comprobar: bool = True,
			tiempo: float | None = None) -> subprocess.CompletedProcess:
		restante = self._restante()
		if tiempo is None or (restante is not None and restante < tiempo):
			tiempo = restante
		grandes = (self.git_dir / "info" / "grandes").as_posix()
		orden = [self.git, f"--git-dir={self.git_dir}", f"--work-tree={self.arbol}", *CONFIG_FIJA,
			"-c", f"core.excludesFile={grandes}", *argumentos]
		try:
			resultado = subprocess.run(orden, cwd=str(self.arbol), env=self.env, input=entrada, capture_output=True,
				timeout=tiempo, creationflags=_CREAR_SIN_VENTANA, check=False)
		except subprocess.TimeoutExpired as error:
			raise TiempoAgotado("Git no ha terminado a tiempo: la carpeta es muy grande o el disco va lento.") from error
		except OSError as error:
			raise ErrorPuntos(f"No se ha podido ejecutar Git: {error}") from error
		if comprobar and resultado.returncode != 0:
			detalle = resultado.stderr.decode("utf-8", "replace").strip()
			log.warning("git %s terminó con %d: %s", " ".join(a for a in argumentos[:3]), resultado.returncode, detalle[:500])
			raise ErrorPuntos(f"Git ha fallado: {detalle.splitlines()[-1] if detalle else resultado.returncode}")
		return resultado

	@contextmanager
	def _bloqueo(self, tiempo: float | None) -> Iterator[None]:
		inicio = time.monotonic()
		if not self.candado.acquire(timeout=tiempo if tiempo else -1):
			raise TiempoAgotado("Otra operación con los puntos de restauración de esta carpeta no ha terminado a tiempo.")
		anterior = self._limite
		self._limite = inicio + tiempo if tiempo else None
		try:
			yield
		finally:
			self._limite = anterior
			self.candado.release()

	# -- Preparación ------------------------------------------------------

	def existe(self) -> bool:
		return (self.git_dir / "HEAD").is_file()

	def _asegurar(self) -> None:
		if not self.existe():
			self.git_dir.mkdir(parents=True, exist_ok=True)
			try:
				subprocess.run([self.git, "init", "--bare", "--template=", "-q", str(self.git_dir)], env=self.env,
					capture_output=True, timeout=30, creationflags=_CREAR_SIN_VENTANA, check=True)
			except (OSError, subprocess.SubprocessError) as error:
				raise ErrorPuntos(f"No se ha podido preparar el repositorio de los puntos de restauración: {error}") from error
		info = self.git_dir / "info"
		info.mkdir(exist_ok=True)
		_escribir_si_cambia(info / "exclude", "\n".join(self._exclusiones()) + "\n")
		# Bytes exactos: sin conversión de finales de línea, sin filtros (LFS) ni codificaciones.
		_escribir_si_cambia(info / "attributes", "* -text -eol -filter -ident -working-tree-encoding -diff\n")
		if not (info / "grandes").exists():
			(info / "grandes").write_text("", encoding="utf-8")
		estado = self._estado()
		if estado.get("carpeta") != str(self.arbol):
			self._guardar_estado(carpeta=str(self.arbol))
		# Un candado que dejó un git que se paró a la fuerza (tiempo agotado): el candado propio lo protege.
		for candado in (self.git_dir / "index.lock",):
			try:
				candado.unlink(missing_ok=True)
			except OSError:
				log.debug("No se pudo quitar %s", candado, exc_info=True)

	def _exclusiones(self) -> list[str]:
		lineas = list(EXCLUSIONES)
		try:
			from app.core.paths import ruta_datos

			datos = ruta_datos().resolve()
			relativa = datos.relative_to(self.arbol.resolve())
			if relativa.parts:
				lineas.append(_patron_literal(relativa.as_posix()) + "/")
		except (ValueError, OSError):
			log.debug("Los datos de Ctrl+IA no están dentro de %s", self.arbol)
		return lineas

	def _ruta_estado(self) -> Path:
		return self.git_dir.parent / "ctrlia.json"

	def _estado(self) -> dict[str, Any]:
		try:
			datos = json.loads(self._ruta_estado().read_text(encoding="utf-8"))
			return datos if isinstance(datos, dict) else {}
		except (OSError, ValueError):
			return {}

	def _guardar_estado(self, **cambios: Any) -> None:
		datos = self._estado()
		datos.update(cambios)
		try:
			self._ruta_estado().write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
		except OSError:
			log.debug("No se pudo guardar el estado de los puntos", exc_info=True)

	def _comprobar_demasiados(self) -> None:
		cuando = float(self._estado().get("demasiados") or 0)
		if cuando and time.time() - cuando < PAUSA_DEMASIADOS:
			raise PuntosNoDisponibles(self._texto_demasiados())

	@staticmethod
	def _texto_demasiados() -> str:
		return (f"La carpeta de la sesión tiene más de {MAX_ARCHIVOS:,} archivos (sin contar los que ignora git): ahí no se "
			"guardan puntos de restauración.").replace(",", ".")

	def _candidatos(self) -> list[str]:
		"""Lo que entraría en un punto sin contar el límite de tamaño (seguido o nuevo y no ignorado)."""
		resultado = self.orden("-c", "core.excludesFile=", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
		return [r for r in resultado.stdout.decode("utf-8", "replace").split("\0") if r]

	def _actualizar_grandes(self) -> list[str]:
		candidatos = self._candidatos()
		if len(candidatos) > MAX_ARCHIVOS:
			self._guardar_estado(demasiados=time.time())
			raise PuntosNoDisponibles(self._texto_demasiados())
		grandes: list[str] = []
		for indice, relativa in enumerate(candidatos):
			if indice % 2000 == 1999:
				self._restante()
			if relativa.endswith("/"):
				continue
			datos = _lstat(self.arbol / relativa)
			if datos is not None and stat.S_ISREG(datos.st_mode) and datos.st_size > MAX_TAMANO:
				grandes.append(relativa)
		_escribir_si_cambia(self.git_dir / "info" / "grandes", "".join(_patron_literal(g) + "\n" for g in grandes))
		if grandes and (self.git_dir / "index").exists():
			# Si ya estaban (crecieron), salen del índice de la sombra; el archivo del disco no se toca.
			self.orden("update-index", "-z", "--force-remove", "--stdin", entrada=("\0".join(grandes) + "\0").encode("utf-8"))
		return grandes

	def _anadir(self) -> list[str]:
		"""``add -A`` de toda la carpeta. Devuelve lo que no se pudo leer."""
		if not (self.git_dir / "index").exists():
			# Primer punto: un solo paquete en vez de miles de objetos sueltos.
			resultado = self.orden("-c", "core.bigFileThreshold=1", "add", "-A", "--", ":/", comprobar=False)
			if resultado.returncode != 0:
				log.info("El primer punto en paquete falló (%d); se repite con objetos sueltos", resultado.returncode)
				resultado = self.orden("add", "-A", "--ignore-errors", "--", ":/", comprobar=False)
		else:
			resultado = self.orden("add", "-A", "--ignore-errors", "--", ":/", comprobar=False)
		error = resultado.stderr.decode("utf-8", "replace")
		if resultado.returncode not in (0, 1):
			log.warning("git add terminó con %d: %s", resultado.returncode, error[:500])
			raise ErrorPuntos("Git no ha podido leer la carpeta: " + (error.strip().splitlines() or ["error desconocido"])[-1])
		return sorted({ruta.rstrip("/") for ruta in _RE_NO_INDEXADO.findall(error)})

	def _head_usuario(self) -> str:
		"""El commit al que apunta el HEAD del repositorio de la persona (``""`` si no hay). Solo lee."""
		restante = self._restante()
		try:
			resultado = subprocess.run([self.git, "-c", "safe.directory=*", "-C", str(self.arbol), "rev-parse", "--verify", "-q",
				"HEAD"], env=self.env, capture_output=True, timeout=min(15.0, restante or 15.0),
				creationflags=_CREAR_SIN_VENTANA, check=False)
		except (OSError, subprocess.SubprocessError):
			return ""
		return resultado.stdout.decode("ascii", "replace").strip() if resultado.returncode == 0 else ""

	# -- Puntos -----------------------------------------------------------

	def crear_punto(self, sesion: str, nombre: str, tiempo: float | None = TIEMPO_PUNTO,
			datos: dict[str, Any] | None = None) -> Punto:
		with self._bloqueo(tiempo):
			return self._crear_punto(sesion, nombre, datos)

	def _crear_punto(self, sesion: str, nombre: str, datos: dict[str, Any] | None = None) -> Punto:
		self._asegurar()
		self._comprobar_demasiados()
		grandes = self._actualizar_grandes()
		no_capturados = self._anadir()
		arbol = self.orden("write-tree").stdout.decode("ascii").strip()
		meta = {"v": 1, "sesion": sesion, "nombre": nombre, "fecha": time.time(), "head": self._head_usuario(),
			"no_capturados": no_capturados[:500], "grandes": grandes[:500], **(datos or {})}
		mensaje = f"Ctrl+IA: punto {nombre}\n\n" + json.dumps(meta, ensure_ascii=False) + "\n"
		commit = self.orden("commit-tree", arbol, "-F", "-", entrada=mensaje.encode("utf-8")).stdout.decode("ascii").strip()
		self.orden("update-ref", nombre_ref(sesion, nombre), commit)
		return Punto(commit, no_capturados, grandes)

	def apuntar(self, sesion: str, nombre: str, commit: str) -> None:
		"""Otro nombre para un punto que ya existe (el de fin de turno es el de un mensaje que esperaba en cola)."""
		commit = _validar_commit(commit)
		with self._bloqueo(TIEMPO_PUNTO):
			self.orden("update-ref", nombre_ref(sesion, nombre), commit)

	def resolver(self, sesion: str, nombre: str) -> str:
		"""El commit de un punto con nombre, o ``""``."""
		if not self.existe():
			return ""
		resultado = self.orden("rev-parse", "--verify", "-q", nombre_ref(sesion, nombre) + "^{commit}", comprobar=False)
		return resultado.stdout.decode("ascii", "replace").strip() if resultado.returncode == 0 else ""

	def meta(self, commit: str) -> dict[str, Any]:
		resultado = self.orden("cat-file", "commit", commit, comprobar=False)
		if resultado.returncode != 0:
			raise ErrorPuntos(f"El punto de restauración ya no existe: se borran pasados {DIAS_PUNTOS} días o cuando una "
				f"sesión tiene más de {MAX_PUNTOS_SESION}.")
		cuerpo = resultado.stdout.decode("utf-8", "replace").split("\n\n", 1)[-1]
		for linea in reversed(cuerpo.splitlines()):
			if linea.startswith("{"):
				try:
					datos = json.loads(linea)
				except ValueError:
					continue
				if isinstance(datos, dict):
					return datos
		return {}

	def _diferencias(self, commit: str) -> list[tuple[str, str, str, str]]:
		"""``(estado, ruta, modo en el punto, modo ahora)`` de lo que cambió entre el punto y el índice."""
		resultado = self.orden("diff", "--cached", "--raw", "-z", "--no-renames", "--no-ext-diff", commit, "--")
		partes = resultado.stdout.split(b"\0")
		diferencias = []
		indice = 0
		while indice < len(partes) - 1:
			cabecera = partes[indice].decode("ascii", "replace")
			if not cabecera.startswith(":"):
				indice += 1
				continue
			campos = cabecera[1:].split()
			ruta = partes[indice + 1].decode("utf-8", "replace")
			if len(campos) >= 5:
				diferencias.append((campos[4][:1], ruta, campos[0], campos[1]))
			indice += 2
		return diferencias

	def _cambios(self, commit: str, excluir: set[str]) -> tuple[list[Cambio], list[str]]:
		cambios: list[Cambio] = []
		anidados: list[str] = []
		for estado, ruta, modo_antes, modo_ahora in self._diferencias(commit):
			if "160000" in (modo_antes, modo_ahora):
				anidados.append(ruta)
				continue
			if ruta in excluir:
				continue
			if estado == "A":
				accion = ACCION_BORRAR
			elif estado == "D":
				accion = ACCION_DEVOLVER if os.path.lexists(self.arbol / ruta) else ACCION_RECREAR
			else:
				accion = ACCION_DEVOLVER
			cambios.append(Cambio(ruta, accion))
		return cambios, anidados

	def _anidados(self, commit: str) -> list[str]:
		"""Repositorios git anidados y submódulos que hay en el punto (entran sin su contenido)."""
		resultado = self.orden("ls-tree", "-r", "-z", commit, comprobar=False)
		anidados = []
		for entrada in resultado.stdout.split(b"\0"):
			if entrada.startswith(b"160000 "):
				anidados.append(entrada.split(b"\t", 1)[-1].decode("utf-8", "replace"))
		return anidados

	def vista_previa(self, sesion: str, commit: str, a_mano: bool = True, tiempo: float | None = TIEMPO_ORDEN) -> VistaPrevia:
		"""Qué pasará con cada archivo si se vuelve al punto ``commit``."""
		commit = _validar_commit(commit)
		with self._bloqueo(tiempo):
			self._asegurar()
			meta = self.meta(commit)
			grandes = self._actualizar_grandes()
			no_capturados = self._anadir()
			excluir = set(meta.get("no_capturados") or []) | set(no_capturados)
			cambios, anidados = self._cambios(commit, excluir)
			for ruta in self._anidados(commit):
				if ruta not in anidados:
					anidados.append(ruta)
			fecha = float(meta.get("fecha") or 0)
			cambiados_a_mano: list[str] = []
			fin = self.resolver(sesion, PUNTO_FIN) if a_mano else ""
			if fin and fin != commit:
				try:
					fecha_fin = float(self.meta(fin).get("fecha") or 0)
				except ErrorPuntos:
					fecha_fin = 0.0
				if fecha_fin > fecha:
					rutas = {c.ruta for c in cambios}
					cambiados_a_mano = [ruta for _e, ruta, _a, _b in self._diferencias(fin) if ruta in rutas]
			grandes_nuevos = []
			for ruta in grandes:
				datos = _lstat(self.arbol / ruta)
				if datos is not None and datos.st_mtime > fecha:
					grandes_nuevos.append(ruta)
			return VistaPrevia(commit, fecha, cambios, sorted(excluir), anidados, cambiados_a_mano,
				str(meta.get("head") or ""), self._head_usuario(), grandes_nuevos)

	def restaurar(self, sesion: str, commit: str, seguridad: str = PUNTO_SEGURIDAD,
			tiempo: float | None = TIEMPO_ORDEN) -> Resultado:
		"""Deja la carpeta como en el punto ``commit`` (salvo lo no protegido).

		Antes guarda el estado actual en el punto ``seguridad`` (vacío: no se guarda).
		Al terminar guarda un punto de fin de turno con lo que queda, para que lo
		restaurado no parezca después un cambio hecho a mano.
		"""
		commit = _validar_commit(commit)
		with self._bloqueo(tiempo):
			self._asegurar()
			meta = self.meta(commit)
			resultado = Resultado()
			if seguridad:
				punto = self._crear_punto(sesion, seguridad, {"tipo": "seguridad", "desde": commit})
				resultado.seguridad = punto.commit
				no_capturados = punto.no_capturados
			else:
				self._actualizar_grandes()
				no_capturados = self._anadir()
			excluir = set(meta.get("no_capturados") or []) | set(no_capturados)
			cambios, _anidados = self._cambios(commit, excluir)
			a_recuperar = [c.ruta for c in cambios if c.accion != ACCION_BORRAR]
			a_borrar = [c.ruta for c in cambios if c.accion == ACCION_BORRAR]
			if a_recuperar:
				lote = self.orden("--literal-pathspecs", "restore", f"--source={commit}", "--staged", "--worktree",
					"--pathspec-from-file=-", "--pathspec-file-nul", entrada=("\0".join(a_recuperar) + "\0").encode("utf-8"),
					comprobar=False)
				if lote.returncode == 0:
					resultado.devueltos = list(a_recuperar)
				else:
					# Uno a uno: así se sabe cuál falló (abierto en otro programa) y el resto se devuelve igual.
					for ruta in a_recuperar:
						uno = self.orden("--literal-pathspecs", "restore", f"--source={commit}", "--staged", "--worktree",
							"--", ruta, comprobar=False)
						if uno.returncode == 0:
							resultado.devueltos.append(ruta)
						else:
							detalle = uno.stderr.decode("utf-8", "replace").strip().splitlines()
							resultado.fallidos.append((ruta, detalle[-1] if detalle else "Git no ha podido devolverlo."))
			for ruta in a_borrar:
				motivo = borrar_seguro(self.arbol, ruta)
				if motivo is None:
					resultado.borrados.append(ruta)
				else:
					resultado.fallidos.append((ruta, motivo))
			if resultado.borrados:
				self.orden("update-index", "-z", "--force-remove", "--stdin",
					entrada=("\0".join(resultado.borrados) + "\0").encode("utf-8"))
			try:
				self._crear_punto(sesion, PUNTO_FIN, {"tipo": "tras_restaurar"})
			except ErrorPuntos:
				log.debug("No se pudo guardar el punto tras restaurar", exc_info=True)
			return resultado

	# -- Limpieza ---------------------------------------------------------

	def _refs(self, prefijo: str = "refs/puntos/") -> list[tuple[str, float]]:
		resultado = self.orden("for-each-ref", "--format=%(refname)%00%(committerdate:unix)", prefijo, comprobar=False)
		refs = []
		for linea in resultado.stdout.decode("utf-8", "replace").splitlines():
			nombre, _sep, fecha = linea.partition("\0")
			try:
				refs.append((nombre, float(fecha or 0)))
			except ValueError:
				refs.append((nombre, 0.0))
		return refs

	def _borrar_refs(self, refs: Sequence[str]) -> None:
		if refs:
			self.orden("update-ref", "--stdin", entrada="".join(f"delete {r}\n" for r in refs).encode("utf-8"))

	def _podar(self) -> None:
		self.orden("gc", "--prune=now", "--quiet", comprobar=False, tiempo=TIEMPO_PREPARAR)

	def limpiar(self, sesion: str | None = None, ahora: float | None = None) -> int:
		"""Borra los puntos de más de ``DIAS_PUNTOS`` días y los que pasan de ``MAX_PUNTOS_SESION`` por sesión."""
		ahora = time.time() if ahora is None else ahora
		if not self.existe():
			return 0
		with self._bloqueo(TIEMPO_PREPARAR):
			prefijo = f"refs/puntos/{_componente(sesion)}/" if sesion else "refs/puntos/"
			refs = self._refs(prefijo)
			viejos = {r for r, fecha in refs if ahora - fecha > DIAS_PUNTOS * 86400}
			por_sesion: dict[str, list[tuple[str, float]]] = {}
			for ref, fecha in refs:
				partes = ref.split("/")
				if len(partes) >= 4 and partes[-1] not in (PUNTO_FIN, PUNTO_SEGURIDAD) and ref not in viejos:
					por_sesion.setdefault(partes[2], []).append((ref, fecha))
			for lista in por_sesion.values():
				# Del más reciente al más antiguo; con la misma fecha (al segundo), el mensaje de número mayor.
				lista.sort(key=lambda par: (par[1], int(re.sub(r"\D", "", par[0].rsplit("/", 1)[-1]) or 0)), reverse=True)
				viejos.update(ref for ref, _fecha in lista[MAX_PUNTOS_SESION:])
			self._borrar_refs(sorted(viejos))
			if viejos:
				self._podar()
			return len(viejos)

	def borrar_sesion(self, sesion: str) -> None:
		"""Borra todos los puntos de una sesión; si el repositorio se queda sin puntos, lo borra entero."""
		if not self.existe():
			return
		with self._bloqueo(TIEMPO_PREPARAR):
			self._borrar_refs([r for r, _f in self._refs(f"refs/puntos/{_componente(sesion)}/")])
			if self._refs():
				self._podar()
			else:
				_borrar_carpeta_repositorio(self.git_dir)


def _lstat(ruta: Path) -> os.stat_result | None:
	"""Datos del archivo sin seguir enlaces, o ``None`` si ya no está (el agente lo borró entretanto)."""
	try:
		return os.lstat(ruta)
	except OSError:
		log.debug("No se puede leer %s", ruta, exc_info=True)
		return None


def _escribir_si_cambia(ruta: Path, contenido: str) -> None:
	try:
		if ruta.is_file() and ruta.read_text(encoding="utf-8") == contenido:
			return
	except (OSError, UnicodeDecodeError):
		log.debug("Se reescribe %s", ruta)
	ruta.write_text(contenido, encoding="utf-8", newline="\n")


def _borrar_carpeta_repositorio(git_dir: Path) -> None:
	"""Borra ``data/cache/puntos/<hash>`` (solo carpetas de la caché de puntos)."""
	carpeta = git_dir.parent
	try:
		if _normal(carpeta.parent) != _normal(carpeta_puntos()) or _es_enlace(carpeta):
			return

		def quitar_solo_lectura(funcion: Callable[..., Any], ruta: str, _error: BaseException) -> None:
			os.chmod(ruta, stat.S_IWRITE)
			funcion(ruta)

		shutil.rmtree(carpeta, onexc=quitar_solo_lectura)
	except OSError:
		log.warning("No se pudo borrar %s", carpeta, exc_info=True)


# ----------------------------------------------------------------------
# Atajos por carpeta (bloquean: llamar desde un hilo)
# ----------------------------------------------------------------------


def _sombra(carpeta: str | Path) -> Sombra:
	motivo = motivo_carpeta(carpeta)
	if motivo:
		raise PuntosNoDisponibles(motivo)
	return Sombra(carpeta)


def crear_punto(carpeta: str | Path, sesion: str, nombre: str, tiempo: float | None = TIEMPO_PUNTO,
		datos: dict[str, Any] | None = None) -> Punto:
	return _sombra(carpeta).crear_punto(sesion, nombre, tiempo, datos)


def vista_previa(carpeta: str | Path, sesion: str, commit: str, a_mano: bool = True) -> VistaPrevia:
	return _sombra(carpeta).vista_previa(sesion, commit, a_mano)


def restaurar(carpeta: str | Path, sesion: str, commit: str, seguridad: str = PUNTO_SEGURIDAD) -> Resultado:
	return _sombra(carpeta).restaurar(sesion, commit, seguridad)


def apuntar(carpeta: str | Path, sesion: str, nombre: str, commit: str) -> None:
	_sombra(carpeta).apuntar(sesion, nombre, commit)


def limpiar(carpeta: str | Path, sesion: str | None = None) -> int:
	if not ruta_repositorio(carpeta).is_dir():
		return 0
	return Sombra(carpeta).limpiar(sesion)


def borrar_sesion(carpeta: str | Path, sesion: str) -> None:
	if ruta_repositorio(carpeta).is_dir():
		Sombra(carpeta).borrar_sesion(sesion)


def limpiar_todo(ahora: float | None = None) -> None:
	"""Recorre todos los repositorios sombra: borra los puntos viejos y los repositorios sin puntos."""
	if not disponible()[0]:
		return
	for git_dir in sorted(carpeta_puntos().glob("*/repo.git")):
		try:
			sombra = Sombra(git_dir.parent, git_dir)
			sombra.limpiar(None, ahora)
			with sombra._bloqueo(TIEMPO_PREPARAR):
				if sombra.existe() and not sombra._refs():
					_borrar_carpeta_repositorio(git_dir)
		except ErrorPuntos:
			log.warning("No se pudieron limpiar los puntos de %s", git_dir, exc_info=True)


def preparar(carpeta: str | Path) -> None:
	"""Llena el índice de la sombra sin tiempo corto (para que los puntos siguientes lleguen a tiempo)."""
	sombra = _sombra(carpeta)
	with sombra._bloqueo(TIEMPO_PREPARAR):
		sombra._asegurar()
		sombra._comprobar_demasiados()
		sombra._actualizar_grandes()
		sombra._anadir()


def en_segundo_plano(funcion: Callable[..., Any], *argumentos: Any) -> threading.Thread:
	"""Ejecuta ``funcion`` en un hilo aparte; un fallo se registra (nunca llega a la interfaz)."""

	def trabajo() -> None:
		try:
			funcion(*argumentos)
		except ErrorPuntos as error:
			log.info("Puntos de restauración: %s", error)
		except Exception:  # noqa: BLE001 - un hilo de fondo no debe morir en silencio
			log.exception("Falló una tarea de los puntos de restauración")

	hilo = threading.Thread(target=trabajo, name="puntos", daemon=True)
	hilo.start()
	return hilo


# ----------------------------------------------------------------------
# Textos para la persona
# ----------------------------------------------------------------------


def avisos_de(vista: VistaPrevia, rehacer: bool = False) -> list[str]:
	"""Avisos de la vista previa, en frases completas."""
	avisos: list[str] = []
	if vista.a_mano and not rehacer:
		avisos.append(f"{archivos(len(vista.a_mano)).capitalize()} cambiaron después de que el agente terminara su último "
			f"turno (los cambiaste tú o lo hizo otro programa): {_lista(vista.a_mano)}. También esos cambios se desharán; "
			"«Rehacer los cambios deshechos» los recupera.")
	if vista.no_protegidos:
		avisos.append(f"Estos archivos no están protegidos porque estaban abiertos en otro programa cuando se guardó el "
			f"punto, o lo están ahora, y no se tocarán: {_lista(vista.no_protegidos)}.")
	if vista.anidados:
		avisos.append(f"Estas carpetas son repositorios git aparte (o submódulos) y lo que cambió dentro no se deshace: "
			f"{_lista(vista.anidados)}.")
	if vista.grandes:
		avisos.append(f"Los archivos de más de {MAX_TAMANO // (1024 * 1024)} MB no se guardan en los puntos y no se tocarán: "
			f"{_lista(vista.grandes)}.")
	if vista.head_cambiado:
		avisos.append("El repositorio git de la carpeta tiene commits nuevos (o cambió de rama) desde ese mensaje. Ctrl+IA solo "
			"devuelve los archivos: los commits que hiciera el agente no se deshacen y seguirán en el historial de git, que "
			"verá lo devuelto como cambios sin confirmar.")
	return avisos


def resumen_mensaje(texto: str, maximo: int = 80) -> str:
	texto = " ".join((texto or "").split())
	return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def nota_deshacer(resumen: str, rutas: Sequence[str]) -> str:
	"""Lo que se le dice al agente delante del siguiente mensaje tras deshacer."""
	return (f"[Ctrl+IA] La persona ha deshecho los cambios de archivos hasta su mensaje «{resumen}»: estos archivos han "
		f"vuelto a como estaban antes de ese mensaje: {_lista(list(rutas)) or 'ninguno'}. Lo que hiciste en ellos después ya "
		"no está en el disco; vuelve a leerlos antes de cambiarlos.")


def nota_rehacer(resumen: str, rutas: Sequence[str]) -> str:
	return (f"[Ctrl+IA] La persona ha vuelto a poner los cambios de archivos que había deshecho (hasta su mensaje "
		f"«{resumen}»): estos archivos vuelven a estar como los dejaste: {_lista(list(rutas)) or 'ninguno'}.")


# ----------------------------------------------------------------------
# Integración con las sesiones (la hereda ``GestorSesiones``)
# ----------------------------------------------------------------------


@dataclass(slots=True)
class PlanDeshacer:
	"""Lo que se va a deshacer (o rehacer), listo para enseñarlo y confirmarlo."""

	clave: str
	id_mensaje: int
	resumen: str
	cambios: list[Cambio]
	avisos: list[str] = field(default_factory=list)
	rehacer: bool = False
	#: Sesión de Claude Code por SSH: lo deshace Claude Code (``deshacer_archivos``), no la sombra.
	remoto: bool = False
	#: Punto al que se vuelve (o identificador del mensaje en Claude Code si es remoto).
	destino: str = ""
	carpeta: str = ""


class MixinPuntos:
	"""Puntos de restauración de las sesiones: cuándo se guardan y cómo se deshacen. Lo hereda ``GestorSesiones``.

	- Al enviar (``_enviar_con_punto``): si la sesión admite puntos y el agente no
	  trabaja, el mensaje espera en un hilo como mucho ``TIEMPO_PUNTO`` segundos a
	  su punto y sale después (en orden). Si el agente trabaja, el mensaje queda
	  marcado ``punto_en_cola`` y recibe el punto de fin de turno del turno en marcha.
	- Al terminar cada turno: punto ``fin`` (para saber qué cambió después a mano).
	- Datos del mensaje de la persona: ``punto`` (commit), ``sin_punto`` (motivo),
	  ``punto_en_cola`` y ``claude_uuid`` (sesiones de Claude Code por SSH).
	- ``extra`` de la sesión: ``nota_deshacer`` (va delante del siguiente mensaje)
	  y ``deshacer_ultimo`` (para «Rehacer»).
	"""

	almacen: Any
	_opcion: Callable[[str, Any], Any]
	_candado_solicitudes: Any

	def _iniciar_puntos(self) -> None:
		#: Mensajes que esperan su punto antes de salir, por sesión, en orden.
		self._envios: dict[str, list[dict[str, Any]]] = {}
		self._candado_envios = threading.Lock()
		self._uuid_apuntado: dict[str, str] = {}
		self._limpieza_lanzada = False

	# -- Si la sesión admite puntos ---------------------------------------

	def _sesion_con_puntos(self, clave: str) -> Any:
		"""La sesión si se le guardan puntos (opción activa, de Ctrl+IA, de este equipo, no Ollama y con git); si no, ``None``."""
		from app.services.agentes import remoto

		if not clave.startswith("ctrlia:") or not self._opcion("agentes.puntos_restauracion", False):
			return None
		sesion = self.almacen.sesion(clave)
		if sesion is None or sesion.proveedor == "ollama" or remoto.es_remota(sesion.extra):
			return None
		return sesion if disponible()[0] else None

	def _cambiar_datos_mensaje(self, id_mensaje: int, quitar: Sequence[str] = (), **cambios: Any) -> None:
		with self._candado_solicitudes:
			guardado = self.almacen.mensaje(id_mensaje)
			if guardado is None:
				return
			datos = dict(guardado.datos)
			for nombre in quitar:
				datos.pop(nombre, None)
			datos.update(cambios)
			self.almacen.actualizar_mensaje(id_mensaje, datos=datos)

	# -- Enviar -----------------------------------------------------------

	def _enviar_con_punto(self, clave: str, conversacion: Any, id_mensaje: int, texto: str, adjuntos: Sequence[Any],
			ahora: bool) -> bool:
		"""Si hace falta, se encarga del envío (tras el punto, en un hilo). Devuelve ``True`` si lo ha hecho."""
		with self._candado_envios:
			pendiente = bool(self._envios.get(clave))
		sesion = self._sesion_con_puntos(clave)
		if sesion is None and not pendiente:
			return False
		if not pendiente and conversacion.ocupada:
			# El agente trabaja: este mensaje sale cuando acabe, con el punto del final de ese turno.
			self._cambiar_datos_mensaje(id_mensaje, punto_en_cola=True)
			return False
		envio = {"conversacion": conversacion, "id": id_mensaje, "texto": texto, "adjuntos": list(adjuntos),
			"ahora": ahora, "puntos": sesion is not None}
		with self._candado_envios:
			cola = self._envios.setdefault(clave, [])
			cola.append(envio)
			if len(cola) > 1:
				return True  # ya hay un hilo vaciando la cola de esta sesión
		threading.Thread(target=self._vaciar_envios, args=(clave,), name="puntos-envio", daemon=True).start()
		return True

	def _vaciar_envios(self, clave: str) -> None:
		"""Hilo de una sesión: guarda el punto de cada mensaje y lo envía, en orden. Sale cuando la cola se vacía."""
		while True:
			with self._candado_envios:
				cola = self._envios.get(clave)
				if not cola:
					self._envios.pop(clave, None)
					return
				envio = cola[0]
			conversacion = envio["conversacion"]
			if envio["puntos"]:
				try:
					self._punto_antes_de_enviar(clave, conversacion, int(envio["id"]))
				except Exception:  # noqa: BLE001 - sin punto, el mensaje sale igual
					log.exception("No se pudo guardar el punto de restauración del mensaje")
			with self._candado_envios:
				cola = self._envios.get(clave) or []
				retirado = not cola or cola[0] is not envio
				if not retirado:
					# Mientras sale sigue en la cola: la sesión cuenta como ocupada y «Detener» ya no lo retira.
					envio["enviando"] = True
			if retirado:
				# Lo retiró «Detener» mientras se guardaba su punto: no ha salido.
				try:
					self._cambiar_datos_mensaje(int(envio["id"]), quitar=("punto", "punto_en_cola"),
						sin_punto="El mensaje no llegó a enviarse.")
				except Exception:  # noqa: BLE001 - apuntarlo es secundario (p. ej., Ctrl+IA se está cerrando)
					log.warning("No se pudo apuntar que el mensaje no salió", exc_info=True)
				continue
			try:
				if envio["ahora"] and conversacion.ocupada:
					conversacion.enviar_ahora(envio["texto"])
				else:
					conversacion.enviar(envio["texto"], envio["adjuntos"])
				self._apuntar_uuid_claude(clave, conversacion, int(envio["id"]))
			except Exception as error:  # noqa: BLE001 - se explica en la sesión como cualquier error del agente
				log.exception("No se pudo enviar el mensaje tras guardar su punto")
				from app.services.agentes.base import Evento, TipoEvento

				self._recibir(clave, Evento(TipoEvento.ERROR, f"No se pudo enviar el mensaje: {error}"))
			with self._candado_envios:
				cola = self._envios.get(clave) or []
				if cola and cola[0] is envio:
					cola.pop(0)
				if not cola:
					self._envios.pop(clave, None)
					return

	def _punto_antes_de_enviar(self, clave: str, conversacion: Any, id_mensaje: int) -> None:
		if conversacion.ocupada:
			self._cambiar_datos_mensaje(id_mensaje, punto_en_cola=True)
			return
		carpeta = str(conversacion.ajustes.carpeta)
		try:
			punto = crear_punto(carpeta, clave, f"m{id_mensaje}", TIEMPO_PUNTO, {"tipo": "mensaje", "mensaje": id_mensaje})
		except TiempoAgotado:
			self._cambiar_datos_mensaje(id_mensaje, sin_punto=(
				"No dio tiempo a guardar el punto de restauración antes de enviar el mensaje (la carpeta es grande o el "
				"disco iba lento). Ctrl+IA ya está preparando la carpeta para que los siguientes lleguen a tiempo."))
			en_segundo_plano(preparar, carpeta)
			return
		except ErrorPuntos as error:
			self._cambiar_datos_mensaje(id_mensaje, sin_punto=str(error))
			return
		self._cambiar_datos_mensaje(id_mensaje, quitar=("sin_punto", "punto_en_cola"), punto=punto.commit)
		if not self._limpieza_lanzada:
			self._limpieza_lanzada = True
			en_segundo_plano(limpiar_todo)
		en_segundo_plano(limpiar, carpeta, clave)

	def _apuntar_uuid_claude(self, clave: str, conversacion: Any, id_mensaje: int) -> None:
		"""Claude Code (por SSH) deshace con el identificador de tu mensaje en su conversación."""
		identificador = getattr(conversacion, "ultimo_uuid_usuario", None)
		if not identificador or not isinstance(identificador, str) or self._uuid_apuntado.get(clave) == identificador:
			return
		self._uuid_apuntado[clave] = identificador
		self._cambiar_datos_mensaje(id_mensaje, claude_uuid=identificador)

	def _con_deshacer(self, clave: str, conversacion: Any, texto: str) -> str:
		"""Pone delante del mensaje la nota de que la persona deshizo (o rehízo) cambios de archivos (una vez)."""
		nota = str(conversacion.ajustes.extra.pop("nota_deshacer", "") or "")
		sesion = self.almacen.sesion(clave)
		if sesion is not None and "nota_deshacer" in sesion.extra:
			nota = nota or str(sesion.extra.get("nota_deshacer") or "")
			guardado = dict(sesion.extra)
			guardado.pop("nota_deshacer", None)
			self.almacen.actualizar_sesion(clave, extra=guardado)
		return nota + "\n\n" + texto if nota else texto

	def _envio_pendiente(self, clave: str) -> bool:
		with self._candado_envios:
			return bool(self._envios.get(clave))

	def _sesiones_con_envio(self) -> list[str]:
		with self._candado_envios:
			return [clave for clave, cola in self._envios.items() if cola]

	def _retirar_envios(self, clave: str) -> list[str]:
		"""«Detener»: los mensajes que aún esperaban su punto no salen (vuelven al cuadro de respuesta)."""
		with self._candado_envios:
			todos = self._envios.get(clave) or []
			cola = [envio for envio in todos if not envio.get("enviando")]
			quedan = [envio for envio in todos if envio.get("enviando")]
			if quedan:
				self._envios[clave] = quedan
			else:
				self._envios.pop(clave, None)
		for envio in cola:
			self._cambiar_datos_mensaje(int(envio["id"]), quitar=("punto_en_cola",), sin_punto="El mensaje no llegó a enviarse.")
		return [self._texto_original(int(envio["id"]), envio["texto"]) for envio in cola]

	def _texto_original(self, id_mensaje: int, texto: str) -> str:
		guardado = self.almacen.mensaje(id_mensaje)
		return guardado.texto if guardado is not None else texto

	def _sin_punto_en_cola(self, clave: str) -> None:
		"""Tras «Detener», los mensajes en cola del agente no recibirán el punto de un turno que no es el suyo."""
		for mensaje in self.almacen.ultimos_mensajes(clave, 50):
			if mensaje.rol == "usuario" and mensaje.datos.get("punto_en_cola") and not mensaje.datos.get("punto"):
				self._cambiar_datos_mensaje(mensaje.id, quitar=("punto_en_cola",), sin_punto="El mensaje se quedó en cola al detener al agente.")

	# -- Fin de turno ------------------------------------------------------

	def _punto_fin_turno(self, clave: str) -> None:
		sesion = self._sesion_con_puntos(clave)
		if sesion is None:
			return
		en_cola = None
		for mensaje in self.almacen.ultimos_mensajes(clave, 50):
			if mensaje.rol == "usuario" and mensaje.datos.get("punto_en_cola") and not mensaje.datos.get("punto"):
				en_cola = mensaje.id  # el más antiguo (la lista va del más nuevo al más antiguo)
		if en_cola is None:
			en_segundo_plano(self._crear_fin, clave, sesion.carpeta, None)
		else:
			# Aquí mismo: el agente no saca el siguiente mensaje de su cola hasta que esto vuelva.
			self._crear_fin(clave, sesion.carpeta, en_cola)

	def _crear_fin(self, clave: str, carpeta: str, id_mensaje: int | None) -> None:
		try:
			punto = crear_punto(carpeta, clave, PUNTO_FIN, TIEMPO_PUNTO, {"tipo": "fin_turno"})
			if id_mensaje is not None:
				apuntar(carpeta, clave, f"m{id_mensaje}", punto.commit)
		except ErrorPuntos as error:
			if id_mensaje is not None:
				self._cambiar_datos_mensaje(id_mensaje, quitar=("punto_en_cola",), sin_punto=str(error))
			log.info("No se guardó el punto de fin de turno: %s", error)
			return
		if id_mensaje is not None:
			self._cambiar_datos_mensaje(id_mensaje, quitar=("punto_en_cola", "sin_punto"), punto=punto.commit)

	def _borrar_puntos(self, sesion: Any) -> None:
		"""Al borrar una sesión de Ctrl+IA, sus puntos (en segundo plano)."""
		from app.services.agentes import remoto

		if sesion is None or not sesion.clave.startswith("ctrlia:") or remoto.es_remota(sesion.extra) or not sesion.carpeta:
			return
		if ruta_repositorio(sesion.carpeta).is_dir() and disponible()[0]:
			en_segundo_plano(borrar_sesion, sesion.carpeta, sesion.clave)

	# -- Deshacer y rehacer (bloquean: llamar desde un hilo) ---------------

	def _comprobar_parada(self, clave: str) -> Any:
		if not clave.startswith("ctrlia:"):
			raise ErrorPuntos("Solo se pueden deshacer los cambios de archivos en las sesiones de Ctrl+IA.")
		sesion = self.almacen.sesion(clave)
		if sesion is None:
			raise ErrorPuntos("La sesión ya no existe.")
		if sesion.proveedor == "ollama":
			raise ErrorPuntos("Ollama solo conversa: no cambia archivos, así que no hay nada que deshacer.")
		if self.ocupada(clave):
			raise ErrorPuntos("El agente está trabajando. Detenlo con Control+punto o espera a que termine y vuelve a "
				"intentarlo: mientras trabaja podría volver a cambiar los archivos.")
		return sesion

	def plan_deshacer(self, clave: str, id_mensaje: int) -> PlanDeshacer:
		"""Qué pasará con cada archivo al deshacer los cambios desde el mensaje ``id_mensaje`` (tuyo)."""
		from app.services.agentes import remoto

		sesion = self._comprobar_parada(clave)
		mensaje = self.almacen.mensaje(int(id_mensaje))
		if mensaje is None or mensaje.sesion != clave or mensaje.rol != "usuario":
			raise ErrorPuntos("Elige uno de tus mensajes de esta sesión.")
		resumen = resumen_mensaje(mensaje.texto)
		if remoto.es_remota(sesion.extra):
			return self._plan_remoto(clave, sesion, mensaje, resumen)
		commit = str(mensaje.datos.get("punto") or "")
		if not commit:
			motivo = str(mensaje.datos.get("sin_punto") or "")
			if not motivo:
				ok, motivo_git = disponible()
				if not ok:
					motivo = motivo_git
				elif mensaje.datos.get("punto_en_cola"):
					motivo = "El mensaje esperaba en cola y su punto todavía no se ha guardado."
				else:
					motivo = ("Cuando se envió, los puntos de restauración estaban desactivados (Preferencias, Agentes) o "
						"esta versión de Ctrl+IA todavía no los guardaba.")
			raise ErrorPuntos("Ese mensaje no tiene punto de restauración. " + motivo)
		vista = vista_previa(sesion.carpeta, clave, commit)
		return PlanDeshacer(clave, int(id_mensaje), resumen, vista.cambios, avisos_de(vista), destino=commit,
			carpeta=sesion.carpeta)

	def _plan_remoto(self, clave: str, sesion: Any, mensaje: Any, resumen: str) -> PlanDeshacer:
		conversacion = self.conversacion(clave)
		deshacer = getattr(conversacion, "deshacer_archivos", None)
		identificador = str(mensaje.datos.get("claude_uuid") or "")
		if deshacer is None or sesion.proveedor != "claude":
			raise ErrorPuntos("En las sesiones de otro equipo por SSH solo se pueden deshacer los cambios de archivos con "
				"Claude Code.")
		if not identificador:
			raise ErrorPuntos("Claude Code no tiene apuntado ese mensaje, así que no puede deshacer sus cambios.")
		respuesta = self._llamar_deshacer_remoto(deshacer, identificador, True)
		rutas = [str(r) for r in respuesta.get("filesChanged") or []]
		avisos = ["En esta sesión de otro equipo, los cambios los deshace el propio Claude Code: solo los archivos que "
			"cambió con sus herramientas de edición, no lo que hicieran las órdenes que ejecutó."]
		mas, menos = int(respuesta.get("insertions") or 0), int(respuesta.get("deletions") or 0)
		if mas or menos:
			avisos.append(f"Se quitarán {mas} líneas añadidas y volverán {menos} líneas quitadas.")
		return PlanDeshacer(clave, mensaje.id, resumen, [Cambio(r, ACCION_DEVOLVER) for r in rutas], avisos, remoto=True,
			destino=identificador, carpeta=sesion.carpeta)

	@staticmethod
	def _llamar_deshacer_remoto(deshacer: Callable[[str, bool], Any], identificador: str, prueba: bool) -> dict[str, Any]:
		try:
			respuesta = deshacer(identificador, prueba)
		except ValueError as error:
			raise ErrorPuntos(f"Claude Code no puede deshacer los cambios de ese mensaje: {error}") from error
		if not isinstance(respuesta, dict):
			raise ErrorPuntos("Claude Code no ha contestado a la petición de deshacer los cambios.")
		if not respuesta.get("canRewind", False) or respuesta.get("error"):
			motivo = str(respuesta.get("error") or "no tiene guardado cómo estaban esos archivos")
			raise ErrorPuntos(f"Claude Code no puede deshacer los cambios de ese mensaje: {motivo}")
		return respuesta

	def deshacer(self, plan: PlanDeshacer) -> Resultado:
		"""Deshace lo que dice ``plan`` (de ``plan_deshacer``), lo apunta en la sesión y prepara la nota al agente."""
		sesion = self._comprobar_parada(plan.clave)
		if plan.remoto:
			conversacion = self.conversacion(plan.clave)
			deshacer = getattr(conversacion, "deshacer_archivos", None)
			if deshacer is None:
				raise ErrorPuntos("Claude Code ya no puede deshacer los cambios de esta sesión.")
			respuesta = self._llamar_deshacer_remoto(deshacer, plan.destino, False)
			resultado = Resultado(devueltos=[str(r) for r in respuesta.get("filesChanged") or [c.ruta for c in plan.cambios]])
		else:
			resultado = restaurar(plan.carpeta, plan.clave, plan.destino)
		rutas = resultado.devueltos + resultado.borrados
		texto = f"Se deshicieron los cambios de {archivos(resultado.total)} hasta el mensaje «{plan.resumen}»."
		if resultado.fallidos:
			texto += f" No se pudieron deshacer {archivos(len(resultado.fallidos))}: {_lista([r for r, _m in resultado.fallidos])}."
		self.almacen.anadir_mensaje(plan.clave, "sistema", texto)
		extra: dict[str, Any] = {"nota_deshacer": nota_deshacer(plan.resumen, rutas)}
		if resultado.seguridad:
			extra["deshacer_ultimo"] = {"seguridad": resultado.seguridad, "mensaje": plan.id_mensaje, "resumen": plan.resumen,
				"fecha": time.time()}
		self._cambiar_extra(plan.clave, sesion, extra, quitar=() if resultado.seguridad else ("deshacer_ultimo",))
		return resultado

	def puede_rehacer(self, clave: str) -> bool:
		sesion = self.almacen.sesion(clave) if clave.startswith("ctrlia:") else None
		datos = sesion.extra.get("deshacer_ultimo") if sesion is not None else None
		return isinstance(datos, dict) and bool(datos.get("seguridad"))

	def plan_rehacer(self, clave: str) -> PlanDeshacer:
		sesion = self._comprobar_parada(clave)
		datos = sesion.extra.get("deshacer_ultimo")
		if not isinstance(datos, dict) or not datos.get("seguridad"):
			raise ErrorPuntos("No hay cambios deshechos que rehacer en esta sesión: solo se puede rehacer el último deshacer.")
		commit = str(datos["seguridad"])
		vista = vista_previa(sesion.carpeta, clave, commit, a_mano=False)
		vista.head_antes = ""  # el HEAD del punto de seguridad es el de ahora mismo: no hace falta avisar
		return PlanDeshacer(clave, int(datos.get("mensaje") or 0), str(datos.get("resumen") or ""), vista.cambios,
			avisos_de(vista, rehacer=True), rehacer=True, destino=commit, carpeta=sesion.carpeta)

	def rehacer(self, plan: PlanDeshacer) -> Resultado:
		sesion = self._comprobar_parada(plan.clave)
		resultado = restaurar(plan.carpeta, plan.clave, plan.destino, seguridad="")
		rutas = resultado.devueltos + resultado.borrados
		texto = (f"Se rehicieron los cambios de {archivos(resultado.total)} que se habían deshecho hasta el mensaje "
			f"«{plan.resumen}».")
		if resultado.fallidos:
			texto += f" No se pudieron rehacer {archivos(len(resultado.fallidos))}: {_lista([r for r, _m in resultado.fallidos])}."
		self.almacen.anadir_mensaje(plan.clave, "sistema", texto)
		if "nota_deshacer" in sesion.extra:
			# El agente aún no sabía que se había deshecho: ahora todo está como lo dejó, no hace falta decirle nada.
			self._cambiar_extra(plan.clave, sesion, {}, quitar=("deshacer_ultimo", "nota_deshacer"))
		else:
			self._cambiar_extra(plan.clave, sesion, {"nota_deshacer": nota_rehacer(plan.resumen, rutas)}, quitar=("deshacer_ultimo",))
		return resultado

	def _cambiar_extra(self, clave: str, sesion: Any, cambios: dict[str, Any], quitar: Sequence[str] = ()) -> None:
		extra = dict(sesion.extra or {})
		for nombre in quitar:
			extra.pop(nombre, None)
		extra.update(cambios)
		self.almacen.actualizar_sesion(clave, extra=extra)
		viva = self.conversacion(clave, crear=False)
		if viva is not None:
			for nombre in quitar:
				viva.ajustes.extra.pop(nombre, None)
			viva.ajustes.extra.update(cambios)
