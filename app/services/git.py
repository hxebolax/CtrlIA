"""Git y GitHub para preparar la carpeta de una sesión (sin interfaz).

- Copias aisladas («worktrees»): el agente trabaja en una rama propia, en una
  carpeta aparte, sin tocar tu copia de trabajo. Se crean en
  ``<repositorio>/.ctrlia/worktrees/<rama>`` y esa carpeta se añade a
  ``.git/info/exclude`` para que git no la proponga en los commits.
- GitHub: se usa la herramienta oficial ``gh`` con la sesión que el usuario ya
  tenga iniciada; Ctrl+IA no guarda credenciales de GitHub.
"""

from __future__ import annotations

import json
import logging
import re
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


def crear_worktree(carpeta: Path, rama: str) -> Path:
	"""Crea una copia aislada en una rama nueva y devuelve su carpeta."""
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
	codigo, _salida, error = procesos.ejecutar_y_leer(
		[str(git), "-C", str(raiz), "worktree", "add", "-b", rama, str(destino)], 120)
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
