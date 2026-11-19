"""Git y GitHub para preparar la carpeta de una sesión (sin interfaz).

- Copias aisladas («worktrees»): el agente trabaja en una rama propia, en una
  carpeta aparte, sin tocar tu copia de trabajo. Se crean en
  ``<repositorio>/.ctrlia/worktrees/<rama>`` y esa carpeta se añade a
  ``.git/info/exclude`` para que git no la proponga en los commits. La rama
  nueva puede empezar en lo que tiene la carpeta (``HEAD``) o en la rama
  principal de ``origin`` recién traída (:func:`crear_worktree_desde_remoto`),
  y también se puede usar una rama que ya existe
  (:func:`ramas_disponibles`, :func:`crear_worktree_rama_existente`).
- Cambios sin confirmar de la carpeta (:func:`cambios_sin_confirmar` y
  :func:`diff_de`), para «Listas de la sesión».
- GitHub: se usa la herramienta oficial ``gh`` con la sesión que el usuario ya
  tenga iniciada; Ctrl+IA no guarda credenciales de GitHub.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.agentes import procesos

log = logging.getLogger(__name__)

_RE_RAMA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_RE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_RE_DUENO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


class ErrorGit(Exception):
	"""Fallo con un mensaje en español listo para el usuario."""


def buscar_git() -> Path | None:
	return procesos.buscar_ejecutable(["git"], [Path(r"C:\Program Files\Git\cmd\git.exe")])


def buscar_gh() -> Path | None:
	return procesos.buscar_ejecutable(["gh"], [Path(r"C:\Program Files\GitHub CLI\gh.exe")])


def raiz_repositorio(carpeta: Path) -> Path | None:
	"""Carpeta raíz del repositorio git que contiene ``carpeta``, o ``None``."""
	git = buscar_git()
	if git is None or not carpeta.is_dir():
		return None
	try:
		codigo, salida, _error = procesos.ejecutar_y_leer([str(git), "-C", str(carpeta), "rev-parse", "--show-toplevel"], 15)
	except (OSError, procesos.subprocess.SubprocessError):
		return None
	return Path(salida.strip()) if codigo == 0 and salida.strip() else None


def nombre_rama_valido(nombre: str) -> bool:
	return bool(_RE_RAMA.match(nombre)) and ".." not in nombre and not nombre.endswith((".lock", "/"))


def crear_worktree(carpeta: Path, rama: str, base: str = "") -> Path:
	"""Crea una copia aislada en una rama nueva y devuelve su carpeta.

	:param base: dónde empieza la rama (una referencia de git); vacío, en lo que tiene la carpeta (``HEAD``).
		Con una rama remota, la nueva no la sigue (``--no-track``).
	"""
	git = buscar_git()
	if git is None:
		raise ErrorGit("Git no está instalado en este equipo.")
	raiz = raiz_repositorio(carpeta)
	if raiz is None:
		raise ErrorGit("La carpeta no es un repositorio de git.")
	if not nombre_rama_valido(rama):
		raise ErrorGit("El nombre de la rama solo puede tener letras, números, puntos, guiones y barras.")
	destino = raiz / ".ctrlia" / "worktrees" / rama.replace("/", "-")
	if destino.exists():
		raise ErrorGit(f"Ya existe una copia aislada con ese nombre: {destino}")
	orden = [str(git), "-C", str(raiz), "worktree", "add"]
	if base:
		orden += ["--no-track", "-b", rama, str(destino), base]
	else:
		orden += ["-b", rama, str(destino)]
	codigo, _salida, error = procesos.ejecutar_y_leer(orden, 120)
	if codigo != 0:
		raise ErrorGit("Git no pudo crear la copia aislada: " + (error.strip().splitlines() or ["error desconocido"])[-1])
	_excluir(raiz, ".ctrlia/")
	return destino


def _excluir(raiz: Path, patron: str) -> None:
	git = buscar_git()
	if git is None:
		return
	codigo, salida, _error = procesos.ejecutar_y_leer([str(git), "-C", str(raiz), "rev-parse", "--git-common-dir"], 15)
	if codigo != 0:
		return
	directorio = Path(salida.strip())
	if not directorio.is_absolute():
		directorio = raiz / directorio
	exclude = directorio / "info" / "exclude"
	try:
		existente = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
		if patron not in existente.splitlines():
			exclude.parent.mkdir(parents=True, exist_ok=True)
			with exclude.open("a", encoding="utf-8") as archivo:
				archivo.write(("" if existente.endswith("\n") or not existente else "\n") + patron + "\n")
	except OSError:
		log.warning("No se pudo actualizar .git/info/exclude", exc_info=True)


def _repositorios_de(gh: Path, dueno: str, limite: int, organizacion: bool) -> list[dict[str, str]]:
	"""Repositorios de ``dueno`` (vacío: los del usuario) con ``gh repo list``. Lanza ``ErrorGit``."""
	orden = [str(gh), "repo", "list"]
	if dueno:
		orden.append(dueno)
	orden += ["--limit", str(int(limite)), "--json", "nameWithOwner,description,isPrivate,updatedAt"]
	try:
		codigo, salida, error = procesos.ejecutar_y_leer(orden, 60)
	except (OSError, procesos.subprocess.SubprocessError) as fallo:
		raise ErrorGit("GitHub no respondió a tiempo.") from fallo
	if codigo != 0:
		if "auth" in error.lower() or "login" in error.lower():
			raise ErrorGit("No has iniciado sesión en GitHub. Abre una consola y ejecuta: gh auth login")
		raise ErrorGit("GitHub no respondió: " + (error.strip().splitlines() or ["error desconocido"])[-1])
	try:
		datos = json.loads(salida or "[]")
	except ValueError as fallo:
		raise ErrorGit("La respuesta de GitHub no se pudo leer.") from fallo
	repositorios = []
	for d in datos if isinstance(datos, list) else []:
		if not isinstance(d, dict) or not _RE_REPO.match(str(d.get("nameWithOwner") or "")):
			continue
		nombre = str(d["nameWithOwner"])
		propietario = nombre.split("/", 1)[0]
		repositorios.append({
			"nombre": nombre,
			"dueno": propietario,
			"tipo_dueno": "Organización" if organizacion else "Tuyo",
			"descripcion": str(d.get("description") or ""),
			"privado": "Privado" if d.get("isPrivate") else "Público",
		})
	return repositorios


def organizaciones(gh: Path | None = None) -> list[str]:
	"""Organizaciones de GitHub a las que perteneces (``gh api user/orgs``); vacía si no se pueden leer."""
	gh = gh or buscar_gh()
	if gh is None:
		return []
	try:
		codigo, salida, error = procesos.ejecutar_y_leer(
			[str(gh), "api", "user/orgs", "--paginate", "--jq", ".[].login"], 60)
	except (OSError, procesos.subprocess.SubprocessError):
		log.warning("GitHub no respondió al pedir tus organizaciones", exc_info=True)
		return []
	if codigo != 0:
		log.warning("No se pudieron leer tus organizaciones de GitHub: %s", (error.strip().splitlines() or [""])[-1])
		return []
	return list(dict.fromkeys(linea.strip() for linea in salida.splitlines() if _RE_DUENO.match(linea.strip())))


def listar_repositorios(limite: int = 200) -> list[dict[str, str]]:
	"""Tus repositorios de GitHub y los de tus organizaciones, sin repetir.

	Cada uno trae ``nombre`` (``dueño/nombre``), ``dueno``, ``tipo_dueno``
	(«Tuyo» u «Organización»), ``descripcion`` y ``privado``. Si falla la
	lista de tus repositorios (sin sesión iniciada, por ejemplo) se lanza
	``ErrorGit``; un fallo en una organización solo la deja fuera.
	"""
	gh = buscar_gh()
	if gh is None:
		raise ErrorGit("La herramienta de GitHub (gh) no está instalada. Descárgala de cli.github.com.")
	repositorios = _repositorios_de(gh, "", limite, organizacion=False)
	for organizacion in organizaciones(gh):
		try:
			repositorios += _repositorios_de(gh, organizacion, limite, organizacion=True)
		except ErrorGit as error:
			log.warning("No se pudieron listar los repositorios de la organización %s: %s", organizacion, error)
	vistos: set[str] = set()
	unicos = []
	for repositorio in repositorios:
		clave = repositorio["nombre"].casefold()
		if clave not in vistos:
			vistos.add(clave)
			unicos.append(repositorio)
	return unicos


# --------------------------------------------------------------------------
# Rama y copia aislada de una carpeta (Propiedades de la sesión)
# --------------------------------------------------------------------------


def rama_actual(carpeta: Path) -> str | None:
	"""Rama en la que está ``carpeta`` (``git rev-parse --abbrev-ref HEAD``).

	Devuelve ``"HEAD"`` si no hay rama (commit suelto) y ``None`` si no es un
	repositorio, no hay git o no contesta. Un repositorio recién creado, sin
	commits, devuelve la rama que tendrá (``git symbolic-ref``).
	"""
	git = buscar_git()
	if git is None or not carpeta.is_dir():
		return None
	try:
		codigo, salida, _error = procesos.ejecutar_y_leer(
			[str(git), "-C", str(carpeta), "rev-parse", "--abbrev-ref", "HEAD"], 15)
		if codigo == 0 and salida.strip():
			return salida.strip().splitlines()[0]
		codigo, salida, _error = procesos.ejecutar_y_leer(
			[str(git), "-C", str(carpeta), "symbolic-ref", "--short", "-q", "HEAD"], 15)
	except (OSError, procesos.subprocess.SubprocessError):
		return None
	return salida.strip().splitlines()[0] if codigo == 0 and salida.strip() else None


def tipo_de_copia(raiz: Path) -> str:
	"""Qué es la raíz de un repositorio: ``"aislada"`` (worktree), ``"submodulo"`` o ``"normal"``.

	En una copia aislada (y en un submódulo) ``.git`` es un archivo con
	``gitdir: <ruta>``; en la copia aislada esa ruta está en ``.../worktrees/<nombre>``.
	"""
	marca = raiz / ".git"
	try:
		if not marca.is_file():
			return "normal"
		texto = marca.read_text(encoding="utf-8", errors="replace")
	except OSError:
		return "normal"
	destino = next((linea.split(":", 1)[1].strip() for linea in texto.splitlines()
		if linea.lower().startswith("gitdir:")), "")
	partes = [p.casefold() for p in destino.replace("\\", "/").split("/")]
	return "aislada" if "worktrees" in partes[:-1] else "submodulo"


def es_copia_aislada(carpeta: Path) -> bool | None:
	"""``True`` si ``carpeta`` está en una copia aislada (worktree); ``None`` si no es un repositorio."""
	raiz = raiz_repositorio(carpeta)
	return None if raiz is None else tipo_de_copia(raiz) == "aislada"


def describir_repositorio(carpeta: str | Path) -> list[str]:
	"""Líneas de Propiedades sobre el repositorio de ``carpeta``: rama y tipo de copia.

	Solo para carpetas de este equipo. Nunca lanza: si no hay git, devuelve una
	lista vacía; si la carpeta no es un repositorio, lo dice. Llamar desde un hilo.
	"""
	try:
		ruta = Path(carpeta)
		if not str(carpeta) or buscar_git() is None or not ruta.is_dir():
			return []
		raiz = raiz_repositorio(ruta)
		if raiz is None:
			return ["Repositorio de git: no, la carpeta no es un repositorio."]
		rama = rama_actual(ruta)
		tipo = tipo_de_copia(raiz)
	except Exception:  # noqa: BLE001 - Propiedades se muestra igual aunque git falle
		log.debug("No se pudo describir el repositorio de %s", carpeta, exc_info=True)
		return []
	if rama == "HEAD":
		texto_rama = "ninguna (en un commit suelto, sin rama)"
	else:
		texto_rama = rama or "desconocida"
	if tipo == "aislada":
		texto_tipo = f"copia aislada (worktree), en {raiz}"
	elif tipo == "submodulo":
		texto_tipo = f"submódulo de otro repositorio, en {raiz}"
	else:
		texto_tipo = f"repositorio normal, en {raiz}"
	return [f"Rama: {texto_rama}", f"Repositorio de git: {texto_tipo}"]


def clonar(repositorio: str, carpeta_padre: Path) -> Path:
	"""Clona ``dueño/nombre`` dentro de ``carpeta_padre`` y devuelve la carpeta nueva."""
	if not _RE_REPO.match(repositorio):
		raise ErrorGit("El repositorio debe tener la forma dueño/nombre.")
	gh = buscar_gh()
	if gh is None:
		raise ErrorGit("La herramienta de GitHub (gh) no está instalada. Descárgala de cli.github.com.")
	destino = carpeta_padre / repositorio.split("/", 1)[1]
	if destino.exists():
		raise ErrorGit(f"Ya existe la carpeta {destino}.")
	codigo, _salida, error = procesos.ejecutar_y_leer([str(gh), "repo", "clone", repositorio, str(destino)], 900)
	if codigo != 0:
		raise ErrorGit("No se pudo clonar: " + (error.strip().splitlines() or ["error desconocido"])[-1])
	return destino


# --------------------------------------------------------------------------
# Copia aislada: desde dónde empieza la rama (1.6.0)
# --------------------------------------------------------------------------

#: Segundos que se espera a ``git fetch`` (puede ir por la red).
TIEMPO_FETCH = 120
#: Árbol vacío de git: con él, un repositorio sin commits se compara contra «nada».
ARBOL_VACIO = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _entorno_git() -> dict[str, str]:
	"""El entorno de la persona, pero sin que git pida nada por la consola (no hay consola: se quedaría colgado)."""
	return dict(os.environ, GIT_TERMINAL_PROMPT="0")


def _git(raiz: Path, *argumentos: str, tiempo: float = 30, entorno: dict[str, str] | None = None) -> tuple[int, str, str]:
	"""``git -C raiz argumentos`` con el git de la persona. Lanza ``ErrorGit`` si no hay git o no contesta."""
	git = buscar_git()
	if git is None:
		raise ErrorGit("Git no está instalado en este equipo.")
	try:
		return procesos.ejecutar_y_leer([str(git), "-C", str(raiz), *argumentos], tiempo, entorno)
	except (OSError, procesos.subprocess.SubprocessError) as fallo:
		raise ErrorGit("Git no respondió a tiempo.") from fallo


def _ultima_linea(error: str) -> str:
	return (error.strip().splitlines() or ["error desconocido"])[-1]


def _raiz_o_error(carpeta: Path) -> Path:
	if buscar_git() is None:
		raise ErrorGit("Git no está instalado en este equipo.")
	raiz = raiz_repositorio(Path(carpeta))
	if raiz is None:
		raise ErrorGit("La carpeta no es un repositorio de git.")
	return raiz


def _destino_copia(raiz: Path, rama: str) -> Path:
	destino = raiz / ".ctrlia" / "worktrees" / rama.replace("/", "-")
	if destino.exists():
		raise ErrorGit(f"Ya existe una copia aislada con ese nombre: {destino}")
	return destino


def _existe_ref(raiz: Path, ref: str) -> bool:
	codigo, _salida, _error = _git(raiz, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
	return codigo == 0


def tiene_origin(raiz: Path) -> bool:
	"""Si el repositorio tiene un remoto llamado ``origin``."""
	codigo, salida, _error = _git(raiz, "remote")
	return codigo == 0 and "origin" in salida.split()


def rama_principal_remota(carpeta: Path) -> str | None:
	"""La rama principal de ``origin`` (``main``, ``master``...) sin ir a la red; ``None`` si no se sabe.

	Primero la que apunta ``refs/remotes/origin/HEAD``; si no existe, ``origin/main`` y después ``origin/master``.
	"""
	raiz = _raiz_o_error(carpeta)
	codigo, salida, _error = _git(raiz, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
	prefijo = "refs/remotes/origin/"
	if codigo == 0 and salida.strip().startswith(prefijo):
		rama = salida.strip()[len(prefijo):]
		if nombre_rama_valido(rama):
			return rama
	for rama in ("main", "master"):
		if _existe_ref(raiz, prefijo + rama):
			return rama
	return None


def traer_rama_remota(carpeta: Path, rama: str, tiempo: float = TIEMPO_FETCH) -> None:
	"""Trae de ``origin`` la última versión de ``rama`` (sin submódulos). Lanza ``ErrorGit``."""
	if not nombre_rama_valido(rama):
		raise ErrorGit("El nombre de la rama remota no es válido.")
	raiz = _raiz_o_error(carpeta)
	codigo, _salida, error = _git(raiz, "fetch", "--quiet", "--no-recurse-submodules", "origin",
		f"+refs/heads/{rama}:refs/remotes/origin/{rama}", tiempo=tiempo, entorno=_entorno_git())
	if codigo != 0:
		raise ErrorGit(f"No se pudo traer la rama {rama} de origin: " + _ultima_linea(error))


def crear_worktree_desde_remoto(carpeta: Path, rama: str, tiempo: float = TIEMPO_FETCH) -> Path:
	"""Copia aislada en la rama nueva ``rama``, empezando en la última versión de la rama principal de ``origin``.

	Antes trae esa rama con ``git fetch``; la rama nueva no sigue a la remota (``--no-track``), para que un
	``git push`` no vaya a parar a la principal.
	"""
	raiz = _raiz_o_error(carpeta)
	if not tiene_origin(raiz):
		raise ErrorGit("El repositorio no tiene un remoto llamado origin.")
	principal = rama_principal_remota(raiz)
	if principal is None:
		raise ErrorGit("No se sabe cuál es la rama principal de origin (ni origin/HEAD, ni origin/main, ni origin/master).")
	traer_rama_remota(raiz, principal, tiempo)
	return crear_worktree(raiz, rama, base=f"refs/remotes/origin/{principal}")


@dataclass(slots=True)
class RamaGit:
	"""Una rama que se puede usar para una copia aislada.

	:param nombre: nombre de la rama (``feature/x``); en las remotas, sin ``origin/``.
	:param remota: solo existe en ``origin`` (se crea una local que la sigue).
	:param sacada_en: carpeta donde ya está sacada (otra copia aislada o la principal); vacía si está libre.
	"""

	nombre: str
	remota: bool = False
	sacada_en: str = ""

	@property
	def texto(self) -> str:
		"""Cómo se enseña en la lista: «x», «x (en origin)» o «x (ya sacada en C:\\...)»."""
		if self.sacada_en:
			return f"{self.nombre} (ya sacada en {self.sacada_en}: no se puede usar en otra copia)"
		return f"{self.nombre} (en origin)" if self.remota else self.nombre


def ramas_sacadas(carpeta: Path) -> dict[str, str]:
	"""``{rama: carpeta}`` de las ramas sacadas en alguna copia del repositorio (``git worktree list --porcelain``)."""
	raiz = _raiz_o_error(carpeta)
	codigo, salida, _error = _git(raiz, "worktree", "list", "--porcelain")
	if codigo != 0:
		return {}
	sacadas: dict[str, str] = {}
	ruta = ""
	for linea in salida.splitlines():
		if linea.startswith("worktree "):
			ruta = linea[len("worktree "):].strip()
		elif linea.startswith("branch refs/heads/") and ruta:
			sacadas[linea[len("branch refs/heads/"):].strip()] = str(Path(ruta))
	return sacadas


def ramas_disponibles(carpeta: Path, traer: bool = True, tiempo: float = TIEMPO_FETCH) -> list[RamaGit]:
	"""Ramas que se pueden usar en una copia aislada: las locales, las de ``origin`` que no hay aquí y, al final,
	las que ya están sacadas en otra copia (que git no deja sacar dos veces).

	Con ``traer`` se pone al día la lista de ``origin`` antes (``git fetch --prune``); si falla (sin red), se usa
	la que hay. Bloquea: llamar desde un hilo. Lanza ``ErrorGit`` si no es un repositorio.
	"""
	raiz = _raiz_o_error(carpeta)
	if traer and tiene_origin(raiz):
		codigo, _salida, error = _git(raiz, "fetch", "--prune", "--quiet", "--no-recurse-submodules", "origin",
			tiempo=tiempo, entorno=_entorno_git())
		if codigo != 0:
			log.info("No se pudo poner al día la lista de ramas de origin: %s", _ultima_linea(error))
	codigo, salida, error = _git(raiz, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin")
	if codigo != 0:
		raise ErrorGit("Git no pudo listar las ramas: " + _ultima_linea(error))
	locales: list[str] = []
	remotas: list[str] = []
	for linea in salida.splitlines():
		ref = linea.strip()
		if ref.startswith("refs/heads/"):
			locales.append(ref[len("refs/heads/"):])
		elif ref.startswith("refs/remotes/origin/") and ref != "refs/remotes/origin/HEAD":
			remotas.append(ref[len("refs/remotes/origin/"):])
	sacadas = ramas_sacadas(raiz)
	libres = [RamaGit(nombre) for nombre in sorted(locales, key=str.casefold) if nombre not in sacadas]
	solo_remotas = [RamaGit(nombre, remota=True) for nombre in sorted(remotas, key=str.casefold)
		if nombre not in locales and nombre_rama_valido(nombre)]
	ocupadas = [RamaGit(nombre, sacada_en=sacadas[nombre]) for nombre in sorted(locales, key=str.casefold) if nombre in sacadas]
	return libres + solo_remotas + ocupadas


def crear_worktree_rama_existente(carpeta: Path, rama: RamaGit) -> Path:
	"""Copia aislada en una rama que ya existe (local o de ``origin``). Lanza ``ErrorGit``."""
	if rama.sacada_en:
		raise ErrorGit(f"La rama {rama.nombre} ya está sacada en {rama.sacada_en}, y git no deja usar la misma rama "
			"en dos copias. Elige otra o crea una rama nueva.")
	if not nombre_rama_valido(rama.nombre):
		raise ErrorGit("El nombre de la rama no es válido.")
	raiz = _raiz_o_error(carpeta)
	destino = _destino_copia(raiz, rama.nombre)
	if rama.remota:
		argumentos = ["worktree", "add", "--track", "-b", rama.nombre, str(destino), f"refs/remotes/origin/{rama.nombre}"]
	else:
		argumentos = ["worktree", "add", str(destino), rama.nombre]
	codigo, _salida, error = _git(raiz, *argumentos, tiempo=120)
	if codigo != 0:
		raise ErrorGit("Git no pudo crear la copia aislada: " + _ultima_linea(error))
	_excluir(raiz, ".ctrlia/")
	return destino


# --------------------------------------------------------------------------
# Cambios sin confirmar (Listas de la sesión)
# --------------------------------------------------------------------------

#: Bytes que se leen de un archivo nuevo para contar sus líneas y decir si es binario.
MAX_ARCHIVO_NUEVO = 2 * 1024 * 1024


@dataclass(slots=True)
class CambioGit:
	"""Un archivo con cambios sin confirmar respecto a ``HEAD``.

	:param ruta: relativa a la raíz del repositorio, con ``/``.
	:param anadidas: líneas añadidas (``None`` en un archivo binario).
	:param quitadas: líneas quitadas (``None`` en un archivo binario).
	:param nuevo: archivo que git aún no sigue.
	:param movido_desde: nombre anterior si git lo ve como renombrado.
	"""

	ruta: str
	anadidas: int | None = 0
	quitadas: int | None = 0
	nuevo: bool = False
	movido_desde: str = ""

	@property
	def nombre(self) -> str:
		return self.ruta.rsplit("/", 1)[-1]

	@property
	def carpeta(self) -> str:
		return self.ruta.rsplit("/", 1)[0] if "/" in self.ruta else ""


def _numero(texto: str) -> int | None:
	return int(texto) if texto.isdigit() else None


def analizar_numstat(salida: str) -> list[CambioGit]:
	"""Lee la salida de ``git diff --numstat -z``.

	Cada archivo es ``añadidas\\tquitadas\\truta\\0``; un renombrado llega con la ruta vacía y luego dos campos
	más, el nombre antiguo y el nuevo: ``añadidas\\tquitadas\\t\\0antiguo\\0nuevo\\0``. Un binario trae ``-``.
	"""
	partes = salida.split("\0")
	cambios: list[CambioGit] = []
	indice = 0
	while indice < len(partes):
		campo = partes[indice].lstrip("\r\n")
		indice += 1
		if not campo:
			continue
		trozos = campo.split("\t", 2)
		if len(trozos) != 3:
			log.debug("Línea de numstat que no se entiende: %r", campo)
			continue
		anadidas, quitadas, ruta = _numero(trozos[0]), _numero(trozos[1]), trozos[2]
		movido = ""
		if not ruta:
			if indice + 1 >= len(partes):
				break
			movido, ruta = partes[indice], partes[indice + 1]
			indice += 2
		cambios.append(CambioGit(ruta, anadidas, quitadas, movido_desde=movido))
	return cambios


def _contar_nuevo(ruta: Path) -> tuple[int | None, int | None]:
	"""Líneas de un archivo nuevo (todas añadidas); ``(None, None)`` si es binario o no se puede leer."""
	try:
		with ruta.open("rb") as archivo:
			datos = archivo.read(MAX_ARCHIVO_NUEVO)
	except OSError:
		return None, None
	if b"\0" in datos[:8192]:
		return None, None
	lineas = datos.count(b"\n") + (1 if datos and not datos.endswith(b"\n") else 0)
	return lineas, 0


def cambios_sin_confirmar(carpeta: Path, tiempo: float = 30) -> tuple[Path, list[CambioGit]]:
	"""``(raíz, cambios)``: los archivos de la carpeta de la sesión con cambios sin confirmar respecto a ``HEAD``.

	Lo modificado, borrado y renombrado sale de ``git diff --numstat -z HEAD`` (sin ``HEAD`` todavía, contra el
	árbol vacío); lo nuevo, de ``git ls-files -z --others --exclude-standard``. Con el git de la persona y su
	configuración. Bloquea: llamar desde un hilo. Lanza ``ErrorGit`` con el motivo.
	"""
	raiz = _raiz_o_error(carpeta)
	base = "HEAD" if _existe_ref(raiz, "HEAD") else ARBOL_VACIO
	codigo, salida, error = _git(raiz, "-c", "core.quotepath=false", "diff", "--numstat", "-z", "--no-ext-diff",
		"--no-color", base, "--", tiempo=tiempo)
	if codigo != 0:
		raise ErrorGit("Git no pudo comparar los cambios: " + _ultima_linea(error))
	cambios = analizar_numstat(salida)
	codigo, salida, error = _git(raiz, "-c", "core.quotepath=false", "ls-files", "-z", "--others", "--exclude-standard",
		tiempo=tiempo)
	if codigo != 0:
		raise ErrorGit("Git no pudo listar los archivos nuevos: " + _ultima_linea(error))
	for ruta in (parte for parte in salida.split("\0") if parte):
		anadidas, quitadas = _contar_nuevo(raiz / ruta)
		cambios.append(CambioGit(ruta, anadidas, quitadas, nuevo=True))
	cambios.sort(key=lambda c: c.ruta.casefold())
	return raiz, cambios


def diff_de(raiz: Path, cambio: CambioGit, tiempo: float = 30) -> str:
	"""El diff de un archivo respecto a ``HEAD`` (``git diff HEAD -- ruta``). De uno nuevo, su contenido.

	Lanza ``ErrorGit`` si git falla.
	"""
	if cambio.nuevo:
		ruta = raiz / cambio.ruta
		if cambio.anadidas is None:
			return ""
		try:
			with ruta.open("rb") as archivo:
				return archivo.read(MAX_ARCHIVO_NUEVO).decode("utf-8", "replace").replace("\r\n", "\n")
		except OSError as error:
			raise ErrorGit(f"No se pudo leer {cambio.ruta}: {error.strerror or error}") from error
	base = "HEAD" if _existe_ref(raiz, "HEAD") else ARBOL_VACIO
	rutas = [cambio.movido_desde, cambio.ruta] if cambio.movido_desde else [cambio.ruta]
	codigo, salida, error = _git(raiz, "-c", "core.quotepath=false", "diff", "--no-ext-diff", "--no-color", "--no-textconv",
		base, "--", *rutas, tiempo=tiempo)
	if codigo != 0:
		raise ErrorGit("Git no pudo mostrar los cambios: " + _ultima_linea(error))
	return salida
