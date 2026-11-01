"""Copias de seguridad: un ZIP con el historial, la memoria y los ajustes de los agentes y los datos de Ctrl+IA.

Sin interfaz (no importa wx). Portado de AIChat (#433 a #452) y ampliado a los
agentes de Ctrl+IA. Si se pierde o se cambia el equipo, lo que los agentes guardan
en local se perdería con él: iniciar sesión en otro equipo no lo trae de vuelta.

Formato del archivo (``CtrlIA-copia-<equipo>-<fecha>.zip``), versión 1
======================================================================

- ``manifiesto.json``::

	{
	  "formato": "ctrlia-copia", "version": 1,
	  "aplicacion": {"nombre": "Ctrl+IA", "version": "1.5.3"},
	  "creada": "2026-11-01T10:00:00+00:00", "terminada": "...",
	  "equipo": {"id": "<uuid estable>", "nombre": "PORTATIL", "sistema": "win32", "version_sistema": "...", "casa": "C:\\\\Users\\\\ana"},
	  "completa": true,            # falso si algo no se pudo copiar (ver «fallos»)
	  "cifrada": false,
	  "agentes": [{"id": "claude", "etiqueta": "Claude Code", "version": null,
	               "zonas": [{"nombre": "claude/casa", "raiz": "C:\\\\...", "descripcion": "..."}],
	               "archivos": 120, "bytes": 123456, "omitido": ["..."], "notas": ["..."]}],
	  "fallos": ["..."], "cambiados": ["..."], "desaparecidos": ["..."], "inservibles": ["..."],
	  "archivos": [{"ruta": "claude/casa/projects/x/y.jsonl", "agente": "claude", "tipo": "archivo",
	                "tamano": 10, "sha256": "...", "modificado": "2026-10-01T10:00:00+00:00"}]
	}

- ``<zona>/<ruta relativa>``: cada archivo, comprimido. Las zonas son ``ctrlia/datos``,
  ``claude/casa``, ``claude/escritorio-N``, ``claude/cowork-N``, ``codex/casa``,
  ``codex/sqlite``, ``codex/skills``, ``gemini/casa`` y ``antigravity/<carpeta>``.

Cifrado (AIChat #435): la copia se hace igual, en ``data/temp``, y entra entera, como
``copia.zip``, en un ZIP cuya única entrada va cifrada con AES-256 al estilo WinZip
(``pyzipper``). Cifrar las entradas de la propia copia dejaría a la vista sus nombres,
y las carpetas de proyectos de Claude llevan la ruta entera en el nombre. Así no se ve
más que ese nombre y 7-Zip la abre (comprobado con frases ASCII: la frase va en UTF-8 y
7-Zip no acepta una con ñ, aunque Ctrl+IA sí). El sin cifrar nunca pasa por la carpeta
de destino (podría estar sincronizándose con una nube). La frase no se guarda en la
copia: olvidada, la copia se pierde. Ese formato deriva la clave con solo 1.000 vueltas
de PBKDF2, así que la seguridad depende de que la frase sea larga.

Reglas:

- Jamás se copian credenciales ni tokens, los liste quien los liste (``NOMBRES_CREDENCIAL``,
  ``CARPETAS_CREDENCIAL``). Lo que no se entiende de las carpetas de Antigravity y Gemini
  no se copia: de esas se copia solo una lista blanca.
- Nunca se escribe en las carpetas de origen. Una base SQLite se copia con su API
  ``backup`` (una copia coherente de esa base aunque su programa la tenga abierta).
- Se escribe a ``.parcial`` en el destino y solo tras releerlo y comprobar cada suma
  recibe su nombre definitivo: una copia a medias nunca parece buena ni sustituye a otra.
- Un archivo que no se puede leer deja la copia incompleta y lo dice, pero no la detiene.
- Restaurar nunca toca los archivos de los agentes: las sesiones vuelven como importadas
  de solo lectura (``importada:<uuid>``) y restaurar otra vez las actualiza sin duplicarlas.
  Las de Antigravity van en la copia, pero sus mensajes no se saben leer.

Los hilos: todo esto bloquea; se llama desde ``app.core.tareas`` y nunca desde la interfaz.
"""

from __future__ import annotations

import contextlib
import ctypes
import fnmatch
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import stat as modos_stat
import sys
import tempfile
import threading
import time
import tomllib
import uuid
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.paths import ruta_configuracion, ruta_datos, ruta_temporales
from app.core.version import APP_VERSION, NOMBRE_APLICACION
from app.services.conservar import tamano_legible
from app.services.historial import claude as historial_claude
from app.services.historial import codex as historial_codex

log = logging.getLogger(__name__)

FORMATO = "ctrlia-copia"
VERSION_FORMATO = 1
MANIFIESTO = "manifiesto.json"
PREFIJO_ARCHIVO = "CtrlIA-copia-"
SUFIJO_PARCIAL = ".parcial"
#: La única entrada de una copia cifrada: la copia entera.
INTERIOR = "copia.zip"
#: Largo mínimo de la frase: el cifrado de los ZIP deriva la clave con pocas vueltas.
MIN_FRASE = 8
_TROZO = 1024 * 1024
#: Un manifiesto lista cada archivo; incluso con 100.000 son decenas de MB. Más grande no es nuestro.
MAX_MANIFIESTO = 256 * 1024 * 1024

#: Nunca se copian, los liste quien los liste: inicios de sesión y claves.
NOMBRES_CREDENCIAL = frozenset({
	".credentials.json", "auth.json", "credentials.json", ".netrc", "id_rsa", "id_ed25519", "token.json", "oauth.json",
	"oauth_creds.json", "google_accounts.json", ".env", "secretos.dat", "cap_sid", "installation_id", "user_id",
})
PATRONES_CREDENCIAL = ("auth.json*", "mcp-oauth-tokens*", "app-server-token*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*")
CARPETAS_CREDENCIAL = frozenset({"mcp-oauth-config", "mcp-secrets", ".sandbox-secrets", ".ssh"})
#: El registro de escritura y la memoria compartida entran con su base (los lee la API backup): copiados tal cual, saldrían rotos.
_ANEXOS_SQLITE = ("-wal", "-shm", "-journal")
_SUFIJOS_SQLITE = (".sqlite", ".sqlite3", ".db")
#: Archivos a medio escribir que los programas renombran después: nunca son historial.
_TEMPORALES = ("*.tmp", "*.tmp.*", "*.partial", "*.parcial", "*~")

# ── Claude Code (AIChat, providers/claude_backup.py) ─────────────────────────
# Lo reconstruye Claude Code, o solo vale mientras se ejecuta. «.claude.json» lleva la cuenta iniciada
# y puede llevar claves de servidores MCP; lo útil que tiene (carpetas de confianza) se rehace enseguida.
CLAUDE_SIN_CARPETAS = (
	"cache", "debug", "telemetry", "statsig", "shell-snapshots", "session-env", "paste-cache", "downloads", "daemon",
	"sessions", "ide", "scratch", "chrome", "spares", "plugins/marketplaces", "plugins/cache", "plugins/repos",
)
CLAUDE_SIN_ARCHIVOS = (
	"*.lock", "daemon.status.json", "stats-cache.json", "*-cache.json", "plugins/plugin-directory-cache*.json",
	".claude.json", ".claude.json.*", "backups/.claude.json*",
)
CLAUDE_OMITIDO = [
	"cachés, registros y telemetría (los reconstruye Claude Code)",
	"sessions/, session-env/, shell-snapshots/ y daemon/ (solo valen mientras Claude Code se ejecuta)",
	"scratch/ (archivos de trabajo temporales)",
	"copias de los complementos de los catálogos (se vuelven a instalar; las listas de lo instalado sí van)",
	".credentials.json, .claude.json y sus copias en backups/ (inicio de sesión y cuenta: vuelve a iniciar sesión)",
]

# ── Codex (AIChat, providers/codex_backup.py) ────────────────────────────────
CODEX_SIN_CARPETAS = (
	"cache", ".cache", "tmp", ".tmp", "packages", "app-server-control", "app-server-daemon", "thread-writer-locks",
	"tui-thread-reference-capabilities", "log", "logs", "plugins/cache", "plugins/openai-curated*", "skills/.system",
	".sandbox*", "browser", "process_manager", "project-metadata-locks", "node_repl", "vendor_imports",
)
CODEX_SIN_ARCHIVOS = (
	"auth.json*", "cap_sid*", "app-server-token*", "installation_id", "*.lock",
	# Un Codex en marcha lo tiene abierto y Windows no deja leerlo: es un bloqueo, no datos (#457).
	".codex-provisioning-*.guard",
	"logs_*.sqlite*", "models_cache.json", "version.json", ".personality_migration", ".sandbox_migration",
	"app-server-control", "app-server-daemon", "tui-thread-reference-capabilities",
)
CODEX_OMITIDO = [
	"auth.json y sus copias, testigos del servidor e identidad de la instalación (inicio de sesión: vuelve a iniciar sesión)",
	"logs_*.sqlite, cache/, tmp/ y la lista de modelos (los reconstruye Codex)",
	"packages/ y copias de complementos de catálogos (se vuelven a instalar)",
	"archivos de control, bloqueos y seguros de aprovisionamiento (solo valen mientras Codex se ejecuta)",
	"skills/.system (las habilidades que trae Codex)",
]
#: Una carpeta reubicada puede ser la carpeta personal entera: una base SQLite cualquiera no es de Codex.
_RE_BASE_CODEX = re.compile(r"(?:state|thread_history|memories|goals|queue)_[0-9]+\.sqlite")

# ── Gemini CLI y Antigravity: carpetas con credenciales y perfiles de navegador sin documentar ──────
# Solo se copia lo que se sabe que es historial, memoria o ajustes.
GEMINI_CARPETAS = ("tmp", "history", "commands", "skills", "policies")
GEMINI_ARCHIVOS = ("GEMINI.md", "AGENTS.md", "settings.json")
ANTIGRAVITY_CARPETAS = ("antigravity", "antigravity-cli", "antigravity-ide")
ANTIGRAVITY_CONTENIDO = ("conversations", "brain", "knowledge", "annotations", "implicit", "code_tracker", "skills")
ANTIGRAVITY_ARCHIVOS = ("conversation_summaries.db", "agyhub_summaries_proto.pb", "jetbox_summaries_proto.pb", "settings.json")
GEMINI_OMITIDO = ["oauth_creds.json, google_accounts.json y mcp-oauth-tokens (inicio de sesión: vuelve a iniciar sesión)",
	"mcp_config.json (puede llevar claves de servidores MCP)", "registros, binarios y el perfil de navegador de Antigravity"]

Colector = Callable[[], "Inventario"]
Cancelado = Callable[[], bool]
Progreso = Callable[[int, int, str], None]


@dataclass(slots=True)
class Elemento:
	"""Un archivo que va en la copia."""

	origen: Path
	nombre: str  # con barras normales, relativo a la raíz del ZIP
	sqlite: bool = False  # se copia con la API backup de SQLite


@dataclass(slots=True)
class Zona:
	"""Una carpeta de este equipo y lo que sale de ella."""

	nombre: str  # «claude/casa»
	raiz: str
	descripcion: str


@dataclass(slots=True)
class Inventario:
	"""Lo que un agente (o Ctrl+IA) quiere copiar."""

	agente: str
	etiqueta: str
	zonas: list[Zona] = field(default_factory=list)
	elementos: list[Elemento] = field(default_factory=list)
	omitido: list[str] = field(default_factory=list)
	notas: list[str] = field(default_factory=list)
	#: Lo que no se pudo leer al listar: basta uno para que la copia quede incompleta.
	fallos: list[str] = field(default_factory=list)
	version: str | None = None


@dataclass(slots=True)
class ResultadoCopia:
	ruta: Path | None = None
	archivos: int = 0
	bytes: int = 0
	bytes_archivo: int = 0
	agentes: list[str] = field(default_factory=list)
	fallos: list[str] = field(default_factory=list)
	cambiados: list[str] = field(default_factory=list)
	desaparecidos: list[str] = field(default_factory=list)
	#: Entradas escritas a medias porque su origen falló: están en el ZIP, pero no son copia de nada.
	inservibles: list[str] = field(default_factory=list)
	notas: list[str] = field(default_factory=list)
	cifrada: bool = False
	completa: bool = False


@dataclass(slots=True)
class Comprobacion:
	correcta: bool
	archivos: int = 0
	problemas: list[str] = field(default_factory=list)
	manifiesto: dict[str, Any] | None = None
	#: Va cifrada y falta la frase o no es esa: hay que pedirla y volver a comprobar.
	pide_frase: bool = False
	cifrada: bool = False


class ErrorFrase(ValueError):
	"""Copia cifrada sin frase («falta»), con la frase mal («mal») o dudosa («mal_o_danada»)."""


PROBLEMAS_FRASE = {
	"falta": "Va cifrada: hace falta su frase.",
	"mal": "La frase no es correcta.",
	"mal_o_danada": "La frase no es correcta, o la copia está dañada.",
}


class ErrorOrigen(OSError):
	"""Falló la lectura de un archivo que se estaba copiando: ese archivo se pierde, la copia sigue.

	Un fallo al escribir la copia es un ``OSError`` normal, y sí la detiene.
	"""


# ── Rutas de los agentes ─────────────────────────────────────────────────────

def raiz_gemini() -> Path:
	"""``~/.gemini``, o la que diga ``GEMINI_CLI_HOME`` (Gemini CLI y Antigravity la comparten)."""
	otra = os.environ.get("GEMINI_CLI_HOME")
	return (Path(otra) if otra else Path.home()) / ".gemini"


def carpeta_skills_compartidas() -> Path:
	return Path.home() / ".agents" / "skills"


def _nombre_arc(*partes: str) -> str:
	return "/".join(p.strip("/").replace("\\", "/") for p in partes if p)


def _es_enlace(ruta: Path) -> bool:
	"""Enlace simbólico o junction: nunca se siguen, para no salir de la carpeta ni dar vueltas."""
	try:
		return ruta.is_symlink() or ruta.is_junction()
	except OSError:
		return True


def es_sqlite(ruta: Path) -> bool:
	"""Si es una base SQLite por su cabecera (no solo por su nombre)."""
	if not str(ruta).lower().endswith(_SUFIJOS_SQLITE):
		return False
	try:
		with open(ruta, "rb") as archivo:
			return archivo.read(16) == b"SQLite format 3\x00"
	except OSError:
		return False


def _es_credencial(nombre: str) -> bool:
	minusculas = nombre.casefold()
	return minusculas in NOMBRES_CREDENCIAL or any(fnmatch.fnmatchcase(minusculas, p) for p in PATRONES_CREDENCIAL)


def recorrer(raiz: Path, zona: str, *, excluir_carpetas: Iterable[str] = (), excluir_archivos: Iterable[str] = (),
		sqlite: bool = True, inventario: Inventario | None = None) -> list[Elemento]:
	"""Cada archivo bajo ``raiz`` como un ``Elemento`` llamado ``zona/<ruta relativa>``.

	Los patrones se comparan con la ruta relativa a ``raiz`` (con barras normales; ``*``
	cruza carpetas): ``"cache"`` deja fuera solo la carpeta de arriba y ``"*.log"`` todos los
	registros. Las credenciales quedan fuera siempre. No se siguen enlaces (se anota en
	``inventario``); si ``raiz`` es uno, se lee donde apunta (quien mudó ``~/.claude`` quiere
	copiar eso). Una carpeta ilegible es un fallo de ``inventario``.
	"""
	raiz = Path(raiz)
	carpetas_fuera, archivos_fuera = tuple(excluir_carpetas), tuple(excluir_archivos)
	elementos: list[Elemento] = []
	if not raiz.is_dir():
		return elementos

	def coincide(relativa: str, patrones: tuple[str, ...]) -> bool:
		return any(fnmatch.fnmatchcase(relativa, p) for p in patrones)

	lectura = raiz
	if _es_enlace(raiz):
		lectura = raiz.resolve()
		if inventario is not None:
			inventario.notas.append(f"{raiz} es un enlace a {lectura}: se copia de allí.")
	# Las carpetas de proyectos de Claude llevan rutas enteras en el nombre y pueden pasar de 260 caracteres.
	lectura = historial_claude._ruta_larga(Path(os.path.abspath(lectura)))

	def ilegible(error: OSError) -> None:
		if inventario is not None:
			inventario.fallos.append(f"No se pudo leer {error.filename}: {error.strerror or error}")

	for actual, carpetas, archivos in os.walk(lectura, onerror=ilegible):
		aqui = Path(actual)
		relativa_aqui = aqui.relative_to(lectura).as_posix()
		relativa_aqui = "" if relativa_aqui == "." else relativa_aqui
		seguir = []
		for nombre in sorted(carpetas):
			relativa = f"{relativa_aqui}/{nombre}" if relativa_aqui else nombre
			if nombre.casefold() in CARPETAS_CREDENCIAL or coincide(relativa, carpetas_fuera):
				continue
			if _es_enlace(aqui / nombre):
				if inventario is not None:
					inventario.notas.append(f"No se sigue un enlace: {zona}/{relativa}")
				continue
			seguir.append(nombre)
		carpetas[:] = seguir
		nombres = set(archivos)
		for nombre in sorted(archivos):
			relativa = f"{relativa_aqui}/{nombre}" if relativa_aqui else nombre
			if (_es_credencial(nombre) or coincide(relativa, archivos_fuera)
					or any(fnmatch.fnmatchcase(nombre, p) for p in _TEMPORALES)):
				continue
			ruta = aqui / nombre
			if _es_enlace(ruta):
				if inventario is not None:
					inventario.notas.append(f"No se sigue un enlace: {zona}/{relativa}")
				continue
			if sqlite and nombre.endswith(_ANEXOS_SQLITE):
				base = nombre[: nombre.rindex("-")]
				if base in nombres and es_sqlite(aqui / base):
					continue
			elementos.append(Elemento(ruta, _nombre_arc(zona, relativa), sqlite=sqlite and es_sqlite(ruta)))
	return elementos


def _un_archivo(ruta: Path, nombre: str) -> list[Elemento]:
	try:
		if ruta.is_file() and not _es_enlace(ruta) and not _es_credencial(ruta.name):
			return [Elemento(ruta, nombre, sqlite=es_sqlite(ruta))]
	except OSError:
		log.debug("No se pudo mirar %s", ruta, exc_info=True)
	return []


# ── Inventarios ──────────────────────────────────────────────────────────────

ZONA_CTRLIA = "ctrlia/datos"
#: Subcarpetas de ``data`` que van en la copia. Lo demás se regenera o es de este equipo.
CTRLIA_CARPETAS = ("db", "config", "skills", "conservadas", "ollama", "sounds/propios")


def _conservadas_repetidas(raiz: Path) -> set[str]:
	"""Copias conservadas cuyo original sigue en la carpeta de Claude Code, que se copia igualmente (AIChat #451).

	Se dejan fuera para que la copia no ocupe el doble. Las que guardan versiones
	anteriores tienen historial que el original ya no tiene: esas van.
	"""
	carpeta = raiz / "conservadas" / "claude"
	if not carpeta.is_dir():
		return set()
	try:
		presentes = {r.stem for r in (historial_claude.raiz_claude() / "projects").glob("*/*.jsonl")}
		return {r.parent.name for r in carpeta.glob("*/registro.json")
			if r.parent.name in presentes and not (r.parent / "versiones").exists()}
	except OSError:
		log.debug("No se pudieron comparar las conservadas", exc_info=True)
		return set()


def inventario_ctrlia() -> Inventario:
	"""Datos de Ctrl+IA: sesiones, grupos, ocultas, ajustes, skills y chats conservados."""
	raiz = ruta_datos()
	inventario = Inventario("ctrlia", NOMBRE_APLICACION, version=APP_VERSION)
	inventario.zonas.append(Zona(ZONA_CTRLIA, str(raiz), "Sesiones, grupos, ajustes, skills y chats conservados de Ctrl+IA"))
	for carpeta in CTRLIA_CARPETAS:
		inventario.elementos += recorrer(raiz / carpeta, f"{ZONA_CTRLIA}/{carpeta}", excluir_archivos=(
			"*.log", ARCHIVO_ESTADO, ARCHIVO_FRASE + "*", "*.bak"), inventario=inventario)
	repetidas = _conservadas_repetidas(raiz)
	if repetidas:
		prefijo = f"{ZONA_CTRLIA}/conservadas/claude/"
		inventario.elementos = [e for e in inventario.elementos
			if not (e.nombre.startswith(prefijo) and e.nombre[len(prefijo):].split("/", 1)[0] in repetidas)]
		inventario.omitido.append(f"{len(repetidas)} chats conservados cuyo original también va en la copia")
	inventario.omitido += ["registros, caché y temporales (se regeneran)",
		"la frase protegida de las copias programadas (solo vale en este equipo)"]
	return inventario


def inventario_claude() -> Inventario:
	"""Claude Code: transcripciones, memoria, instrucciones, ajustes, y las sesiones de su app y de Cowork."""
	inventario = Inventario("claude", "Claude Code")
	casa = historial_claude.raiz_claude()
	if casa.is_dir():
		inventario.zonas.append(Zona("claude/casa", str(casa), "Conversaciones, memoria, instrucciones, ajustes y habilidades"))
		inventario.elementos += recorrer(casa, "claude/casa", excluir_carpetas=CLAUDE_SIN_CARPETAS,
			excluir_archivos=CLAUDE_SIN_ARCHIVOS, inventario=inventario)
		inventario.omitido += CLAUDE_OMITIDO
	else:
		inventario.notas.append(f"No hay carpeta de Claude Code en {casa}.")
	# Pueden existir a la vez la de la instalación normal y la de la Microsoft Store: cada una con su zona.
	for tipo, carpetas in (("escritorio", historial_claude.carpetas_escritorio()), ("cowork", historial_claude.carpetas_cowork())):
		for numero, carpeta in enumerate(carpetas):
			zona = f"claude/{tipo}-{numero}"
			inventario.zonas.append(Zona(zona, str(carpeta), f"Sesiones de la app de Claude ({tipo})"))
			inventario.elementos += recorrer(Path(carpeta), zona, excluir_archivos=("*.lock",), inventario=inventario)
	return inventario


def _version_codex(casa: Path) -> str | None:
	"""La de ``version.json``; ``latest_version`` es la disponible, no la instalada."""
	try:
		datos = json.loads((casa / "version.json").read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return None
	for clave in ("version", "current_version"):
		if isinstance(datos, dict) and isinstance(datos.get(clave), str) and datos[clave]:
			return datos[clave]
	return None


def _sqlite_codex(casa: Path, inventario: Inventario) -> Path | None:
	"""Dónde están las bases de Codex si las movió ``sqlite_home`` o ``CODEX_SQLITE_HOME``."""
	configuracion = casa / "config.toml"
	try:
		ajustes = tomllib.loads(configuracion.read_text(encoding="utf-8"))
	except FileNotFoundError:
		ajustes = {}
	except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
		inventario.fallos.append(f"No se pudo leer {configuracion} ({error}): puede que falten bases de datos de Codex.")
		return None
	if "sqlite_home" in ajustes:
		valor, base = ajustes["sqlite_home"], casa
	else:
		valor = os.environ.get("CODEX_SQLITE_HOME")
		if not valor:
			return None
		base = Path.cwd()  # auditoria: ignorar getcwd - la documentación de Codex define así las rutas relativas de esa variable
	if not isinstance(valor, str) or not valor or "\x00" in valor:
		inventario.fallos.append("El ajuste sqlite_home de Codex no es una carpeta válida.")
		return None
	ruta = Path(valor).expanduser()
	return ruta if ruta.is_absolute() else base / ruta


def _misma(ruta: Path) -> Path:
	try:
		return ruta.resolve()
	except (OSError, RuntimeError):
		return ruta.absolute()


def inventario_codex() -> Inventario:
	"""Codex: conversaciones, bases de hilos, memorias, reglas, habilidades y ajustes."""
	inventario = Inventario("codex", "Codex")
	casa = historial_codex.carpeta_codex()
	if casa.is_dir():
		inventario.version = _version_codex(casa)
		inventario.zonas.append(Zona("codex/casa", str(casa), "Conversaciones, memorias, reglas, habilidades y ajustes"))
		inventario.elementos += recorrer(casa, "codex/casa", excluir_carpetas=CODEX_SIN_CARPETAS,
			excluir_archivos=CODEX_SIN_ARCHIVOS, inventario=inventario)
		inventario.omitido += CODEX_OMITIDO
	else:
		inventario.notas.append(f"No hay carpeta de Codex en {casa}.")
	sqlite_casa = _sqlite_codex(casa, inventario)
	vistos = {_misma(e.origen) for e in inventario.elementos}
	if sqlite_casa is not None and _misma(sqlite_casa) != _misma(casa) and sqlite_casa.is_dir():
		# Solo la parte de arriba y con nombres de Codex: un «sqlite_home = ~» no debe llevarse las bases de un navegador.
		inventario.zonas.append(Zona("codex/sqlite", str(sqlite_casa), "Bases de datos de Codex en otra carpeta"))
		for elemento in recorrer(sqlite_casa, "codex/sqlite", excluir_carpetas=("*",), excluir_archivos=CODEX_SIN_ARCHIVOS,
				inventario=inventario):
			if _RE_BASE_CODEX.fullmatch(elemento.origen.name) and _misma(elemento.origen) not in vistos:
				inventario.elementos.append(elemento)
				vistos.add(_misma(elemento.origen))
	compartidas = carpeta_skills_compartidas()
	if compartidas.is_dir():
		inventario.zonas.append(Zona("codex/skills", str(compartidas), "Tus habilidades compartidas entre agentes"))
		for elemento in recorrer(compartidas, "codex/skills", inventario=inventario):
			if _misma(elemento.origen) not in vistos:
				inventario.elementos.append(elemento)
				vistos.add(_misma(elemento.origen))
	return inventario


def inventario_gemini() -> Inventario:
	"""Gemini CLI: sus chats, comandos, memoria y ajustes (solo lo de la lista blanca)."""
	inventario = Inventario("gemini", "Gemini CLI")
	casa = raiz_gemini()
	if not casa.is_dir():
		inventario.notas.append(f"No hay carpeta de Gemini en {casa}.")
		return inventario
	inventario.zonas.append(Zona("gemini/casa", str(casa), "Chats, comandos, memoria y ajustes de Gemini CLI"))
	for carpeta in GEMINI_CARPETAS:
		inventario.elementos += recorrer(casa / carpeta, f"gemini/casa/{carpeta}", inventario=inventario)
	for archivo in GEMINI_ARCHIVOS:
		inventario.elementos += _un_archivo(casa / archivo, f"gemini/casa/{archivo}")
	inventario.omitido += GEMINI_OMITIDO
	return inventario


def inventario_antigravity() -> Inventario:
	"""Antigravity (app, IDE y CLI): conversaciones y resúmenes (solo lo de la lista blanca)."""
	inventario = Inventario("antigravity", "Antigravity")
	casa = raiz_gemini()
	for carpeta in ANTIGRAVITY_CARPETAS:
		raiz = casa / carpeta
		if not raiz.is_dir():
			continue
		zona = f"antigravity/{carpeta}"
		inventario.zonas.append(Zona(zona, str(raiz), "Conversaciones y resúmenes de Antigravity"))
		for parte in ANTIGRAVITY_CONTENIDO:
			inventario.elementos += recorrer(raiz / parte, f"{zona}/{parte}", inventario=inventario)
		for archivo in ANTIGRAVITY_ARCHIVOS:
			inventario.elementos += _un_archivo(raiz / archivo, f"{zona}/{archivo}")
	if inventario.zonas:
		inventario.omitido += GEMINI_OMITIDO
		inventario.notas.append("Los mensajes de Antigravity van en formato propio: se guardan, pero no se pueden restaurar como sesiones.")
	return inventario


def colectores_de_fabrica() -> list[Colector]:
	return [inventario_ctrlia, inventario_claude, inventario_codex, inventario_gemini, inventario_antigravity]


def raices_copiadas() -> list[Path]:
	"""Las carpetas que copia una copia, sin listarlas: una carpeta de destino no puede estar dentro."""
	casa_gemini = raiz_gemini()
	return [ruta_datos(), historial_claude.raiz_claude(), historial_codex.carpeta_codex(),
		*(casa_gemini / c for c in ANTIGRAVITY_CARPETAS), casa_gemini,
		*historial_claude.carpetas_escritorio(), *historial_claude.carpetas_cowork()]


# ── Identidad del equipo ─────────────────────────────────────────────────────

def identidad_equipo() -> dict[str, Any]:
	"""Un identificador estable de este equipo (creado una vez) y su nombre para las personas.

	El nombre de un equipo puede cambiar; el identificador dice que dos copias salen
	del mismo. Solo sirve para limpiar las copias propias de una carpeta compartida.
	"""
	from app.services.intercambio import nombre_equipo

	identificador: list[str] = []

	def cambio(estado: dict[str, Any]) -> None:
		if not isinstance(estado.get("equipo"), str) or not estado["equipo"]:
			estado["equipo"] = str(uuid.uuid4())
		identificador.append(estado["equipo"])

	_actualizar_estado(cambio)
	return {"id": identificador[0], "nombre": nombre_equipo(), "sistema": sys.platform,
		"version_sistema": platform.platform(), "casa": str(Path.home())}


def _equipo_seguro(nombre: str) -> str:
	limpio = "".join(c if c.isalnum() or c in "-_" else "-" for c in nombre).strip("-")
	return limpio or "equipo"


def nombre_de_archivo(equipo: str, ahora: datetime | None = None) -> str:
	return f"{PREFIJO_ARCHIVO}{_equipo_seguro(equipo)}-{(ahora or datetime.now()):%Y-%m-%d-%H%M%S}.zip"


# ── Destino ──────────────────────────────────────────────────────────────────

def _dentro(hija: Path, padre: Path) -> bool:
	try:
		hija.resolve().relative_to(padre.resolve())
		return True
	except (ValueError, OSError):
		return False


def problema_destino(destino: Path, inventarios: Sequence[Inventario]) -> str | None:
	"""Por qué una carpeta no vale para la copia: dentro de lo que se copia, se copiaría a sí misma."""
	destino = Path(destino)
	if not destino.is_dir():
		return f"{destino} no es una carpeta."
	for inventario in inventarios:
		for zona in inventario.zonas:
			if zona.raiz and _dentro(destino, Path(zona.raiz)):
				return (f"{destino} está dentro de {zona.raiz}, que entra en la copia. "
					"Elige una carpeta fuera, mejor en otro disco.")
	return None


def carpetas_en_la_nube() -> list[Path]:
	"""Carpetas que un servicio sincroniza fuera de este equipo."""
	encontradas = [Path(os.environ[n]) for n in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer") if os.environ.get(n)]
	casa = Path.home()
	return encontradas + [casa / "Dropbox", casa / "Google Drive", casa / "iCloudDrive"]


def mismo_disco_que_casa(destino: Path) -> bool:
	"""Si la copia está en el disco que protege y no se sincroniza: no sobreviviría a perder el equipo."""
	if any(_dentro(Path(destino), c) for c in carpetas_en_la_nube() if c.is_dir()):
		return False
	try:
		return os.stat(destino).st_dev == os.stat(Path.home()).st_dev
	except OSError:
		return False


# ── Escribir ─────────────────────────────────────────────────────────────────

def _cancelada(cancelado: Cancelado | None) -> None:
	if cancelado is not None and cancelado():
		raise InterruptedError("Copia de seguridad cancelada.")


def _volcar(entrada: Any, salida: Any, suma: Any, cancelado: Cancelado | None = None) -> int:
	total = 0
	while True:
		_cancelada(cancelado)
		try:
			trozo = entrada.read(_TROZO)
		except OSError as error:
			raise ErrorOrigen(error.errno, error.strerror or str(error), error.filename) from error
		if not trozo:
			return total
		suma.update(trozo)
		salida.write(trozo)
		total += len(trozo)


def _instantanea_sqlite(origen: Path, carpeta: Path, cancelado: Cancelado | None = None) -> Path:
	"""Copia coherente de una base con la API ``backup``, sin escribir nunca junto al archivo original.

	Abrir una base WAL, aunque sea de solo lectura, crea los ``-wal`` y ``-shm`` que falten,
	y eso sería escribir en la carpeta de otro programa. Según lo que haya al lado:

	- nada: no hay cambios pendientes y se abre como inmutable;
	- ``-wal`` y ``-shm``: su programa la tiene abierta y se lee a través de ellos;
	- ``-wal`` sin ``-shm`` o un ``-journal``: quedaron cambios sin programa que los termine.
	  Se copian base y anexo a la carpeta temporal y se abren allí.

	La copia se comprueba con ``quick_check``: así se detecta una base que cambió bajo la
	apertura inmutable, en vez de guardarla rota.
	"""
	destino = carpeta / f"{uuid.uuid4().hex}.sqlite"
	texto = str(origen)
	if texto.startswith("\\\\?\\UNC\\"):
		texto = "\\\\" + texto[8:]
	elif texto.startswith("\\\\?\\"):
		texto = texto[4:]  # la forma larga: SQLite quiere una dirección normal
	llana = Path(texto)
	al_lado = {sufijo: Path(str(origen) + sufijo).exists() for sufijo in _ANEXOS_SQLITE}
	if al_lado["-journal"] or (al_lado["-wal"] and not al_lado["-shm"]):
		anexo = "-journal" if al_lado["-journal"] else "-wal"
		trabajo = carpeta / uuid.uuid4().hex
		trabajo.mkdir()
		shutil.copyfile(origen, trabajo / "base.sqlite")
		shutil.copyfile(str(origen) + anexo, str(trabajo / "base.sqlite") + anexo)
		direccion = (trabajo / "base.sqlite").as_uri()
	else:
		direccion = llana.resolve().as_uri() + ("?mode=ro" if al_lado["-wal"] else "?mode=ro&immutable=1")
	fuente = sqlite3.connect(direccion, uri=True, timeout=10)
	try:
		copia = sqlite3.connect(destino)
		try:
			def paso(_estado: int, _quedan: int, _total: int) -> None:
				_cancelada(cancelado)

			fuente.backup(copia, pages=256, progress=paso)
			resultado = copia.execute("PRAGMA quick_check").fetchone()
			if not resultado or resultado[0] != "ok":
				raise sqlite3.DatabaseError(f"la copia no pasó la comprobación ({resultado and resultado[0]})")
		finally:
			copia.close()
	finally:
		fuente.close()
	return destino


def _anadir(archivo: zipfile.ZipFile, inventario: Inventario, elemento: Elemento, carpeta: Path, resultado: ResultadoCopia,
		cancelado: Cancelado | None = None) -> dict[str, Any] | None:
	origen = Path(elemento.origen)
	copia: Path | None = None
	try:
		if elemento.sqlite:
			try:
				copia = _instantanea_sqlite(origen, carpeta, cancelado)
			except sqlite3.Error as error:
				resultado.fallos.append(f"{elemento.nombre}: no se pudo copiar la base de datos ({error})")
				return None
		leer = copia or origen
		try:
			antes = os.stat(leer)
			entrada = open(leer, "rb")  # noqa: SIM115 - se cierra en el «with» de abajo
		except OSError as error:
			raise ErrorOrigen(error.errno, error.strerror or str(error), error.filename) from error
		suma = hashlib.sha256()
		info = zipfile.ZipInfo(elemento.nombre, date_time=time.localtime(max(antes.st_mtime, 315532800))[:6])
		info.compress_type = zipfile.ZIP_DEFLATED
		with entrada, archivo.open(info, "w", force_zip64=True) as salida:
			try:
				tamano = _volcar(entrada, salida, suma, cancelado)
			except ErrorOrigen:
				# zipfile ya escribió parte de la entrada y no puede volver atrás: queda apuntada como inservible.
				resultado.inservibles.append(elemento.nombre)
				raise
		if copia is None:
			try:
				despues = os.stat(leer)
				cambio = despues.st_size != antes.st_size or despues.st_mtime_ns != antes.st_mtime_ns
			except OSError:
				cambio = True
			if cambio:
				resultado.cambiados.append(elemento.nombre)
		try:
			modificado = datetime.fromtimestamp(os.stat(origen).st_mtime, UTC).isoformat(timespec="seconds")
		except (OSError, ValueError, OverflowError):
			modificado = None
		return {"ruta": elemento.nombre, "agente": inventario.agente, "tipo": "sqlite" if copia is not None else "archivo",
			"tamano": tamano, "sha256": suma.hexdigest(), "modificado": modificado}
	except ErrorOrigen as error:
		if error.errno == 2 and elemento.nombre not in resultado.inservibles:
			resultado.desaparecidos.append(elemento.nombre)
			resultado.fallos.append(f"{elemento.nombre}: se borró antes de poder copiarlo")
		else:
			resultado.fallos.append(f"{elemento.nombre}: {error.strerror or error}")
		return None
	except FileNotFoundError:
		# Desapareció entre listarlo y leerlo: justo la pérdida que una copia quiere evitar, y se dice.
		resultado.desaparecidos.append(elemento.nombre)
		resultado.fallos.append(f"{elemento.nombre}: se borró antes de poder copiarlo")
		return None
	except UnicodeError as error:
		resultado.fallos.append(f"{elemento.nombre!r}: nombre de archivo no válido ({error})")
		return None
	finally:
		if copia is not None:
			copia.unlink(missing_ok=True)


def _manifiesto(inventarios: Sequence[Inventario], archivos: list[dict[str, Any]], resultado: ResultadoCopia,
		equipo: dict[str, Any], empezada: datetime, cifrada: bool) -> dict[str, Any]:
	por_agente: dict[str, list[int]] = {}
	for entrada in archivos:
		cuenta = por_agente.setdefault(entrada["agente"], [0, 0])
		cuenta[0] += 1
		cuenta[1] += entrada["tamano"]
	return {
		"formato": FORMATO, "version": VERSION_FORMATO,
		"aplicacion": {"nombre": NOMBRE_APLICACION, "version": APP_VERSION},
		"creada": empezada.isoformat(timespec="seconds"),
		"terminada": datetime.now(UTC).isoformat(timespec="seconds"),
		"equipo": equipo, "completa": not resultado.fallos, "cifrada": cifrada,
		"coherencia": "Cada base de datos es una copia coherente de sí misma. Los archivos se copiaron uno tras otro con los "
			"programas quizá en marcha, así que la copia entera no es un solo instante.",
		"agentes": [{
			"id": i.agente, "etiqueta": i.etiqueta, "version": i.version,
			"zonas": [{"nombre": z.nombre, "raiz": z.raiz, "descripcion": z.descripcion} for z in i.zonas],
			"archivos": por_agente.get(i.agente, [0, 0])[0], "bytes": por_agente.get(i.agente, [0, 0])[1],
			"omitido": i.omitido, "notas": i.notas,
		} for i in inventarios],
		"fallos": resultado.fallos, "cambiados": resultado.cambiados, "desaparecidos": resultado.desaparecidos,
		"inservibles": resultado.inservibles, "archivos": archivos,
	}


def _pyzipper() -> Any:
	try:
		import pyzipper
	except ImportError as error:
		raise ValueError("Falta el componente de cifrado (pyzipper): esta copia no se puede cifrar ni descifrar.") from error
	return pyzipper


def _cifrar(interior: Path, exterior: Path, frase: str, cancelado: Cancelado | None = None) -> str:
	"""Mete ``interior`` entero en ``exterior`` como su única entrada, con AES-256. Devuelve el SHA-256 de lo que entró."""
	pyzipper = _pyzipper()
	suma = hashlib.sha256()
	with pyzipper.AESZipFile(exterior, "w", compression=pyzipper.ZIP_STORED, allowZip64=True, encryption=pyzipper.WZ_AES) as archivo:
		archivo.setpassword(frase.encode("utf-8"))
		archivo.setencryption(pyzipper.WZ_AES, nbits=256)
		info = archivo.zipinfo_cls(INTERIOR, date_time=time.localtime()[:6])
		info.compress_type = pyzipper.ZIP_STORED
		with open(interior, "rb") as entrada, archivo.open(info, "w", force_zip64=True) as salida:
			_volcar(entrada, salida, suma, cancelado)
	return suma.hexdigest()


def _descifrar(ruta: Path, frase: str | None, salida: Any, cancelado: Cancelado | None = None) -> str:
	"""Escribe en ``salida`` (o en ningún sitio, con ``None``) la copia de dentro y devuelve su SHA-256."""
	if not frase:
		raise ErrorFrase("falta")
	pyzipper = _pyzipper()
	suma = hashlib.sha256()
	try:
		with pyzipper.AESZipFile(ruta) as archivo:
			archivo.setpassword(frase.encode("utf-8"))
			with archivo.open(INTERIOR) as entrada:
				while True:
					_cancelada(cancelado)
					trozo = entrada.read(_TROZO)
					if not trozo:
						break
					suma.update(trozo)
					if salida is not None:
						salida.write(trozo)
	except RuntimeError as error:
		raise ErrorFrase("mal") from error  # falló la comprobación de 2 bytes de la frase
	except pyzipper.BadZipFile as error:
		# Falló el HMAC final: llega hasta aquí 1 de cada 65.536 frases malas, y también el daño.
		if "hmac" in str(error).lower():
			raise ErrorFrase("mal_o_danada") from error
		raise ValueError(f"La copia cifrada está dañada: {error}") from error
	return suma.hexdigest()


def esta_cifrada(ruta: Path) -> bool:
	"""Si es una copia cifrada: una sola entrada cifrada, ``copia.zip``."""
	try:
		with zipfile.ZipFile(ruta) as archivo:
			entradas = archivo.infolist()
	except (OSError, zipfile.BadZipFile, ValueError, EOFError):
		return False
	return len(entradas) == 1 and entradas[0].filename == INTERIOR and bool(entradas[0].flag_bits & 0x1)


@contextlib.contextmanager
def abierta(ruta: Path, frase: str | None = None, cancelado: Cancelado | None = None) -> Iterator[Path]:
	"""La copia como ZIP normal para leerla: el propio archivo o, si va cifrada, su interior en ``data/temp``, que se borra al salir."""
	if not esta_cifrada(ruta):
		yield Path(ruta)
		return
	with tempfile.TemporaryDirectory(prefix="copia-abierta-", dir=ruta_temporales()) as carpeta:
		destino = Path(carpeta) / INTERIOR
		with open(destino, "wb") as salida:
			_descifrar(ruta, frase, salida, cancelado)
		yield destino


def _publicar(parcial: Path, final: Path, resultado: ResultadoCopia | None = None) -> Path:
	"""Da su nombre a la copia comprobada sin sustituir nunca nada: «(2)», «(3)»... si ya existe.

	Un enlace duro falla si el nombre existe, cosa que renombrar no promete en todas partes.
	"""
	raiz, sufijo = final.stem, final.suffix
	for intento in range(1, 100):
		destino = final if intento == 1 else final.with_name(f"{raiz} ({intento}){sufijo}")
		try:
			os.link(parcial, destino)
		except FileExistsError:
			continue
		except OSError:
			# Disco sin enlaces duros (FAT, algunas carpetas de red): se reserva el nombre y se sustituye el vacío.
			try:
				with open(destino, "xb"):
					pass
			except FileExistsError:
				continue
			try:
				os.replace(parcial, destino)
			except OSError:
				destino.unlink(missing_ok=True)  # nunca un archivo vacío con nombre de copia
				raise
			return destino
		try:
			parcial.unlink()
		except OSError:
			# La copia ya está en su sitio: un antivirus o una nube con el temporal abierto no debe deshacerlo.
			log.debug("No se pudo borrar %s", parcial, exc_info=True)
			if resultado is not None:
				resultado.notas.append(f"{parcial.name} no se ha podido borrar: es una copia repetida y se puede eliminar.")
		return destino
	raise OSError(f"No hay un nombre libre para la copia en {final.parent}.")


def crear(destino: Path, *, colectores: Sequence[Colector] | None = None, progreso: Progreso | None = None,
		cancelado: Cancelado | None = None, nombre: str | None = None, ahora: datetime | None = None,
		frase: str | None = None) -> ResultadoCopia:
	"""Escribe una copia en la carpeta ``destino`` y dice qué ha pasado (bloquea).

	``progreso(hechos, total, nombre)`` se llama desde este mismo hilo. Un archivo ilegible
	se anota y la copia queda incompleta; uno que cambia mientras se lee se conserva y se
	anota. Lanza ``OSError``, ``ValueError`` (destino malo) o ``InterruptedError`` (cancelada)
	si no se ha podido escribir: entonces el destino no tiene ningún archivo nuevo.
	"""
	destino = Path(destino)
	inventarios: list[Inventario] = []
	resultado = ResultadoCopia()
	for colector in list(colectores or colectores_de_fabrica()):
		_cancelada(cancelado)
		try:
			inventarios.append(colector())
		except Exception as error:  # noqa: BLE001 - un agente no debe impedir copiar los demás
			log.warning("No se pudieron listar los archivos de %s", colector, exc_info=True)
			resultado.fallos.append(f"No se pudieron listar los archivos de {getattr(colector, '__name__', 'un agente')}: {error}")
	for inventario in inventarios:
		resultado.fallos.extend(f"{inventario.etiqueta}: {fallo}" for fallo in inventario.fallos)
	problema = problema_destino(destino, inventarios)
	if problema:
		raise ValueError(problema)
	if not any(i.elementos for i in inventarios):
		motivo = f" ({resultado.fallos[0]})" if resultado.fallos else ""
		raise ValueError(f"No hay nada que copiar: no se han encontrado archivos de los agentes ni de Ctrl+IA{motivo}.")
	equipo = identidad_equipo()
	ahora = ahora or datetime.now()
	final = destino / (nombre or nombre_de_archivo(equipo["nombre"], ahora))
	parcial_destino = final.with_name(f"{final.name}.{uuid.uuid4().hex[:8]}{SUFIJO_PARCIAL}")
	elementos = [(i, e) for i in inventarios for e in i.elementos]
	total = len(elementos)
	archivos: list[dict[str, Any]] = []
	empezada = datetime.now(UTC)
	vistos: set[str] = set()
	try:
		with tempfile.TemporaryDirectory(prefix="copia-", dir=ruta_temporales()) as temporal:
			carpeta = Path(temporal)
			# Con frase, lo sin cifrar se queda en data/temp: el destino puede estar sincronizándose con una nube.
			parcial = carpeta / INTERIOR if frase else parcial_destino
			with zipfile.ZipFile(parcial, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archivo:
				for hechos, (inventario, elemento) in enumerate(elementos):
					_cancelada(cancelado)
					if elemento.nombre in vistos or elemento.nombre == MANIFIESTO:
						continue
					vistos.add(elemento.nombre)
					if progreso is not None:
						progreso(hechos, total, elemento.nombre)
					entrada = _anadir(archivo, inventario, elemento, carpeta, resultado, cancelado)
					if entrada is not None:
						archivos.append(entrada)
				manifiesto = _manifiesto(inventarios, archivos, resultado, equipo, empezada, bool(frase))
				archivo.writestr(MANIFIESTO, json.dumps(manifiesto, indent=1, ensure_ascii=False))
			# En el disco, no solo en su caché: un lápiz USB sacado tras «terminada» no debe tener media copia.
			with open(parcial, "rb+") as sincronizar:
				os.fsync(sincronizar.fileno())
			comprobacion = comprobar(parcial, cancelado)
			if not comprobacion.correcta:
				raise OSError("La copia no pasó la comprobación: " + "; ".join(comprobacion.problemas[:3]))
			if frase:
				escrito = _cifrar(parcial, parcial_destino, frase, cancelado)
				with open(parcial_destino, "rb+") as sincronizar:
					os.fsync(sincronizar.fileno())
				if _descifrar(parcial_destino, frase, None, cancelado) != escrito:
					raise OSError("La copia cifrada no se ha podido releer igual.")
			final = _publicar(parcial_destino, final, resultado)
	except BaseException:
		parcial_destino.unlink(missing_ok=True)
		raise
	resultado.ruta = final
	resultado.archivos = len(archivos)
	resultado.bytes = sum(a["tamano"] for a in archivos)
	resultado.bytes_archivo = final.stat().st_size
	resultado.agentes = [i.etiqueta for i in inventarios if i.elementos]
	resultado.completa = not resultado.fallos
	resultado.cifrada = bool(frase)
	if mismo_disco_que_casa(destino):
		resultado.notas.append("Esta copia está en el mismo disco que tus datos: no sobreviviría a perder el equipo. "
			"Llévala a otro disco o a una carpeta en la nube.")
	if progreso is not None:
		progreso(total, total, "")
	log.info("Copia de seguridad en %s: %s archivos, %s bytes, %s fallos", final, resultado.archivos, resultado.bytes, len(resultado.fallos))
	return resultado


# ── Comprobar ────────────────────────────────────────────────────────────────

def nombre_seguro(nombre: str) -> bool:
	"""Sin rutas absolutas, unidades ni «..»: un nombre de la copia nunca sale de donde se le dice."""
	if not nombre or nombre.startswith(("/", "\\")) or ":" in nombre:
		return False
	return ".." not in nombre.replace("\\", "/").split("/")


def leer_manifiesto(ruta: Path, frase: str | None = None) -> dict[str, Any]:
	"""El manifiesto de una copia. Lanza ``ErrorFrase`` si va cifrada y no vale la frase."""
	with abierta(ruta, frase) as llana, zipfile.ZipFile(llana) as archivo:
		manifiesto = json.loads(archivo.read(MANIFIESTO).decode("utf-8"))
	if not isinstance(manifiesto, dict) or manifiesto.get("formato") != FORMATO:
		raise ValueError(f"{Path(ruta).name} no es una copia de Ctrl+IA.")
	return manifiesto


def comprobar(ruta: Path, cancelado: Cancelado | None = None, frase: str | None = None) -> Comprobacion:
	"""Relee la copia entera y compara cada archivo con el manifiesto.

	Cada archivo debe estar, medir lo que dice y tener su SHA-256; nada sin listar ni con
	nombres peligrosos. Una versión de formato posterior es un problema: esta versión no
	puede responder de ella. Una cifrada necesita su frase: sin ella o con otra, ``pide_frase``.
	"""
	if not esta_cifrada(ruta):
		return _comprobar_llana(ruta, cancelado)
	try:
		with abierta(ruta, frase, cancelado) as llana:
			comprobacion = _comprobar_llana(llana, cancelado)
	except InterruptedError:
		raise
	except ErrorFrase as error:
		comprobacion = Comprobacion(False, pide_frase=True, problemas=[PROBLEMAS_FRASE.get(str(error), str(error))])
	except (OSError, zipfile.BadZipFile, zlib.error, ValueError, EOFError) as error:
		comprobacion = Comprobacion(False, problemas=[f"No se ha podido leer: {error}"])
	comprobacion.cifrada = True
	return comprobacion


def _comprobar_llana(ruta: Path, cancelado: Cancelado | None = None) -> Comprobacion:
	comprobacion = Comprobacion(False)
	try:
		with zipfile.ZipFile(ruta) as archivo:
			try:
				if archivo.getinfo(MANIFIESTO).file_size > MAX_MANIFIESTO:
					comprobacion.problemas.append("Su manifiesto es demasiado grande: no es una copia de Ctrl+IA.")
					return comprobacion
				manifiesto = json.loads(archivo.read(MANIFIESTO).decode("utf-8"))
			except KeyError:
				comprobacion.problemas.append("No tiene manifiesto: no es una copia de Ctrl+IA.")
				return comprobacion
			if (not isinstance(manifiesto, dict) or manifiesto.get("formato") != FORMATO
					or not isinstance(manifiesto.get("archivos", []), list)):
				comprobacion.problemas.append("No es una copia de Ctrl+IA.")
				return comprobacion
			comprobacion.manifiesto = manifiesto
			if int(manifiesto.get("version", 0)) > VERSION_FORMATO:
				comprobacion.problemas.append("La hizo una versión posterior de Ctrl+IA.")
				return comprobacion
			nombres = set(archivo.namelist())
			listados = {n for n in manifiesto.get("inservibles") or [] if isinstance(n, str)}
			for entrada in manifiesto.get("archivos", []):
				_cancelada(cancelado)
				if not isinstance(entrada, dict) or not isinstance(entrada.get("ruta"), str):
					comprobacion.problemas.append("Su manifiesto tiene una entrada que no es un archivo.")
					continue
				nombre = entrada["ruta"]
				listados.add(nombre)
				if not nombre_seguro(nombre):
					comprobacion.problemas.append(f"Nombre peligroso en la copia: {nombre}")
					continue
				if nombre not in nombres:
					comprobacion.problemas.append(f"Falta: {nombre}")
					continue
				suma = hashlib.sha256()
				tamano = 0
				with archivo.open(nombre) as leido:
					while trozo := leido.read(_TROZO):
						suma.update(trozo)
						tamano += len(trozo)
						_cancelada(cancelado)
				if tamano != entrada.get("tamano") or suma.hexdigest() != entrada.get("sha256"):
					comprobacion.problemas.append(f"Dañado: {nombre}")
					continue
				comprobacion.archivos += 1
			for nombre in sorted(nombres - listados - {MANIFIESTO})[:20]:
				comprobacion.problemas.append(f"No está en el manifiesto: {nombre}")
	except InterruptedError:
		raise  # una cancelación, no un daño (también es un OSError)
	except (OSError, zipfile.BadZipFile, zlib.error, ValueError, TypeError, EOFError, RuntimeError, NotImplementedError) as error:
		# zlib.error: datos comprimidos dañados. RuntimeError: una entrada cifrada. NotImplementedError: compresión desconocida.
		comprobacion.problemas.append(f"No se ha podido leer: {error}")
		return comprobacion
	comprobacion.correcta = not comprobacion.problemas
	return comprobacion


# ── Textos ───────────────────────────────────────────────────────────────────

def _plural(cuantos: int, singular: str, plural: str) -> str:
	return f"{cuantos} {singular if cuantos == 1 else plural}"


def resumen(resultado: ResultadoCopia) -> str:
	"""Lo que se dice al acabar una copia."""
	texto = (f"Copiados {_plural(resultado.archivos, 'archivo', 'archivos')} ({tamano_legible(resultado.bytes)}, "
		f"{tamano_legible(resultado.bytes_archivo)} comprimidos) de {', '.join(resultado.agentes) or 'nada'} en "
		f"{resultado.ruta.name if resultado.ruta else '?'}.")
	if resultado.fallos:
		texto += (f" No se {'ha' if len(resultado.fallos) == 1 else 'han'} podido copiar "
			f"{_plural(len(resultado.fallos), 'elemento', 'elementos')}: la copia está incompleta y su manifiesto dice cuáles.")
	if resultado.cifrada:
		texto += " Va cifrada con tu frase. Sin ella no se puede abrir: guárdala bien."
	else:
		texto += " No va cifrada y lleva tus conversaciones: guárdala en un sitio privado."
	for nota in resultado.notas:
		texto += " " + nota
	return texto


def texto_comprobacion(ruta: Path, comprobacion: Comprobacion) -> str:
	"""Lo que dice «Comprobar una copia»."""
	nombre = Path(ruta).name
	manifiesto = comprobacion.manifiesto or {}
	if comprobacion.correcta:
		equipo = (manifiesto.get("equipo") or {}).get("nombre", "un equipo desconocido")
		texto = f"{nombre} está entera: coinciden sus {_plural(comprobacion.archivos, 'archivo', 'archivos')}. La hizo {equipo}"
		momento = _instante(manifiesto.get("creada"))
		texto += f" el {momento.astimezone():%d/%m/%Y a las %H:%M}." if momento else "."
		if comprobacion.cifrada:
			texto += " Va cifrada."
		if manifiesto.get("completa") is False:
			fallos = manifiesto.get("fallos") or []
			texto += f" Está incompleta: al hacerla no se pudieron copiar {_plural(len(fallos), 'elemento', 'elementos')}."
		return texto
	vistos = comprobacion.problemas[:10]
	mas = len(comprobacion.problemas) - len(vistos)
	texto = f"{nombre} tiene problemas:\n" + "\n".join(vistos)
	return texto + f"\n... y {mas} más." if mas > 0 else texto


def texto_ultima_copia(estado: dict[str, Any] | None = None) -> str:
	"""Cuándo fue la última copia hecha aquí, de qué tamaño y dónde."""
	estado = cargar_estado() if estado is None else estado
	momento = _instante(estado.get("ultima_fecha"))
	if momento is None:
		return "Todavía no se ha hecho ninguna copia aquí."
	texto = (f"Última copia: {momento.astimezone():%d/%m/%Y a las %H:%M}, {tamano_legible(int(estado.get('ultima_bytes') or 0))}, "
		f"en {estado.get('carpeta', '?')}")
	if estado.get("ultima_completa") is False:
		texto += " (incompleta)"
	return texto + "."


# ── Lo que se recuerda ───────────────────────────────────────────────────────

ARCHIVO_ESTADO = "copias.json"
ARCHIVO_FRASE = "copias_frase.dpapi"
#: copias.json lo cambian la ventana y el hilo de cada copia: cada cambio lee, cambia y escribe bajo este candado.
_CANDADO_ESTADO = threading.RLock()


def _ruta_estado() -> Path:
	return ruta_configuracion() / ARCHIVO_ESTADO


def cargar_estado() -> dict[str, Any]:
	"""La última carpeta, la última copia y el programa (``data/config/copias.json``)."""
	try:
		estado = json.loads(_ruta_estado().read_text(encoding="utf-8"))
	except FileNotFoundError:
		return {}
	except (OSError, ValueError):
		log.warning("No se pudo leer el estado de las copias", exc_info=True)
		return {}
	return estado if isinstance(estado, dict) else {}


def _actualizar_estado(cambio: Callable[[dict[str, Any]], Any], callar: bool = True) -> None:
	"""Lee, deja que ``cambio`` altere y escribe. Con ``callar``, un fallo al guardar se registra y no se propaga."""
	with _CANDADO_ESTADO:
		estado = cargar_estado()
		cambio(estado)
		ruta = _ruta_estado()
		temporal = ruta.with_name(f"{ruta.name}.{uuid.uuid4().hex[:8]}.tmp")
		try:
			with open(temporal, "w", encoding="utf-8") as archivo:
				json.dump(estado, archivo, ensure_ascii=False, indent=1)
				archivo.flush()
				os.fsync(archivo.fileno())
			os.replace(temporal, ruta)
		except OSError:
			log.warning("No se pudo guardar el estado de las copias", exc_info=True)
			if not callar:
				raise
		finally:
			temporal.unlink(missing_ok=True)


def registrar(resultado: ResultadoCopia) -> None:
	"""Recuerda una copia terminada."""
	if resultado.ruta is None:
		return
	datos = {"carpeta": str(resultado.ruta.parent), "ultima_ruta": str(resultado.ruta),
		"ultima_fecha": datetime.now(UTC).isoformat(timespec="seconds"), "ultima_archivos": resultado.archivos,
		"ultima_bytes": resultado.bytes_archivo, "ultima_completa": resultado.completa}
	_actualizar_estado(lambda estado: estado.update(datos))


# ── Copias programadas (AIChat #437) ─────────────────────────────────────────

FRECUENCIAS = ("no", "diaria", "semanal")
NOMBRES_FRECUENCIA = {"no": "No hacer copias programadas", "diaria": "Cada día", "semanal": "Cada semana"}
_PERIODOS = {"diaria": 24 * 3600, "semanal": 7 * 24 * 3600}
CONSERVAR_DE_FABRICA = 5
MAX_CONSERVAR = 100
#: Un fallo se dice como mucho con esta frecuencia: un disco desconectado una semana no debe insistir cada diez minutos.
_AVISO_FALLO = 24 * 3600
_REINTENTO = 3600
#: Copias programadas cuyo nombre se recuerda.
_MAX_HECHAS = 200


@dataclass(slots=True)
class Programa:
	frecuencia: str = "no"
	carpeta: str = ""
	conservar: int = CONSERVAR_DE_FABRICA
	#: Cifrar con la frase protegida por Windows (nunca en copias.json).
	cifrar: bool = False

	@property
	def activo(self) -> bool:
		return self.frecuencia in _PERIODOS and bool(self.carpeta)


def cargar_programa(estado: dict[str, Any] | None = None) -> Programa:
	"""El programa guardado; lo que no se entienda se lee como desactivado."""
	estado = cargar_estado() if estado is None else estado
	datos = estado.get("programa") if isinstance(estado.get("programa"), dict) else {}
	frecuencia = datos.get("frecuencia") if datos.get("frecuencia") in FRECUENCIAS else "no"
	try:
		conservar = max(1, min(MAX_CONSERVAR, int(datos.get("conservar", CONSERVAR_DE_FABRICA))))
	except (TypeError, ValueError, OverflowError):
		conservar = CONSERVAR_DE_FABRICA
	carpeta = datos.get("carpeta") if isinstance(datos.get("carpeta"), str) else ""
	return Programa(frecuencia, carpeta, conservar, datos.get("cifrar") is True)


def guardar_programa(programa: Programa) -> None:
	"""Lanza ``OSError`` si no se puede guardar. Olvida el último intento fallido: la persona acaba de arreglarlo."""
	def cambio(estado: dict[str, Any]) -> None:
		estado["programa"] = {"frecuencia": programa.frecuencia, "carpeta": programa.carpeta,
			"conservar": programa.conservar, "cifrar": bool(programa.cifrar)}
		estado.pop("programa_intento", None)

	_actualizar_estado(cambio, callar=False)


def _instante(texto: Any) -> datetime | None:
	try:
		momento = datetime.fromisoformat(texto)
	except (TypeError, ValueError):
		return None
	return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def copia_pendiente(estado: dict[str, Any] | None = None, ahora: datetime | None = None) -> bool:
	"""Si toca una copia programada: ha pasado el periodo desde la última buena (a mano o no) o nunca hubo una.

	Tras un intento fallido se espera una hora. Una fecha futura significa que el reloj
	estaba mal al escribirla: se ignora, o las copias se pararían sin decir nada.
	"""
	estado = cargar_estado() if estado is None else estado
	programa = cargar_programa(estado)
	if not programa.activo:
		return False
	ahora = ahora or datetime.now(UTC)
	intento = _instante(estado.get("programa_intento"))
	if intento is not None and 0 <= (ahora - intento).total_seconds() < _REINTENTO:
		return False
	ultima = _instante(estado.get("ultima_fecha"))
	if ultima is None:
		return True
	pasado = (ahora - ultima).total_seconds()
	return pasado < 0 or pasado >= _PERIODOS[programa.frecuencia]


def anotar_intento(ahora: datetime | None = None) -> None:
	marca = (ahora or datetime.now(UTC)).isoformat(timespec="seconds")
	_actualizar_estado(lambda estado: estado.__setitem__("programa_intento", marca))


def anotar_fallo(motivo: str, ahora: datetime | None = None) -> bool:
	"""Recuerda un fallo de una copia programada. Devuelve si hay que decirlo ahora."""
	ahora = ahora or datetime.now(UTC)
	decir: list[bool] = []

	def cambio(estado: dict[str, Any]) -> None:
		dicho = _instante(estado.get("programa_fallo_dicho"))
		toca = dicho is None or (ahora - dicho).total_seconds() < 0 or (ahora - dicho).total_seconds() >= _AVISO_FALLO
		estado["programa_fallo"] = {"fecha": ahora.isoformat(timespec="seconds"), "motivo": motivo}
		if toca:
			estado["programa_fallo_dicho"] = ahora.isoformat(timespec="seconds")
		decir.append(toca)

	_actualizar_estado(cambio)
	return decir[0]


def borrar_fallo() -> None:
	def cambio(estado: dict[str, Any]) -> None:
		estado.pop("programa_fallo", None)
		estado.pop("programa_fallo_dicho", None)

	_actualizar_estado(cambio)


def texto_fallo(estado: dict[str, Any] | None = None) -> str:
	"""«La última copia programada falló el...: por qué», o ``""``."""
	estado = cargar_estado() if estado is None else estado
	fallo = estado.get("programa_fallo")
	if not isinstance(fallo, dict):
		return ""
	momento = _instante(fallo.get("fecha"))
	cuando = f" el {momento.astimezone():%d/%m/%Y a las %H:%M}" if momento else ""
	return f"La última copia programada falló{cuando}: {fallo.get('motivo', 'motivo desconocido')}"


def _hecha_por(ruta: Path) -> str | None:
	"""El equipo del manifiesto de una copia sin cifrar; ``None`` si no se puede saber (nunca se da por nuestra)."""
	try:
		with zipfile.ZipFile(ruta) as archivo:
			if archivo.getinfo(MANIFIESTO).file_size > MAX_MANIFIESTO:
				return None
			manifiesto = json.loads(archivo.read(MANIFIESTO).decode("utf-8"))
		identificador = manifiesto.get("equipo", {}).get("id") if manifiesto.get("formato") == FORMATO else None
	except (OSError, KeyError, ValueError, AttributeError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
		log.debug("No se pudo leer el manifiesto de %s", ruta, exc_info=True)
		return None
	return identificador if isinstance(identificador, str) else None


def anotar_hecha(ruta: Path) -> None:
	"""Recuerda una copia programada de este equipo: el manifiesto de una cifrada no se puede leer sin la frase."""
	try:
		entrada = {"ruta": str(ruta), "tamano": Path(ruta).stat().st_size}
	except OSError:
		log.debug("No se pudo mirar %s", ruta, exc_info=True)
		return

	def cambio(estado: dict[str, Any]) -> None:
		hechas = [h for h in estado.get("programa_hechas") or [] if isinstance(h, dict) and h.get("ruta") != entrada["ruta"]]
		estado["programa_hechas"] = (hechas + [entrada])[-_MAX_HECHAS:]

	_actualizar_estado(cambio)


def _misma_ruta(ruta: Any) -> str:
	return os.path.normcase(os.path.abspath(str(ruta)))


def recortar(carpeta: Path, conservar: int, equipo: str, nueva: Path) -> list[Path]:
	"""Borra las copias más antiguas de ESTE equipo en ``carpeta`` hasta dejar ``conservar``, contando ``nueva``.

	``nueva`` no se borra nunca. Una carpeta compartida puede tener copias de otro equipo
	con un nombre que se limpia igual: solo cuentan las que su manifiesto dice que son de
	aquí o, si van cifradas, las que se apuntaron al hacerlas y siguen midiendo lo mismo.
	Lo demás, incluida una copia que no se pueda leer, no se toca. Devuelve lo borrado.
	"""
	hechas = {_misma_ruta(h.get("ruta")): h.get("tamano") for h in cargar_estado().get("programa_hechas") or []
		if isinstance(h, dict) and isinstance(h.get("ruta"), str)}

	def es_nuestra(ruta: Path) -> bool:
		tamano = hechas.get(_misma_ruta(ruta))
		try:
			if tamano is not None and ruta.stat().st_size == tamano:
				return True
		except OSError:
			return False
		return _hecha_por(ruta) == equipo

	nuestras = []
	for ruta in Path(carpeta).glob(f"{PREFIJO_ARCHIVO}*.zip"):
		# Un nombre y luego la fecha: AAAA-MM-DD-HHMMSS, quizá con « (n)».
		sello = ruta.name[:-4].split(" (")[0][-17:]
		if len(sello) == 17 and sello.replace("-", "").isdigit() and ruta.is_file() and ruta != Path(nueva) and es_nuestra(ruta):
			nuestras.append(ruta)
	nuestras.sort(key=lambda r: (r.stat().st_mtime, r.name), reverse=True)
	borradas = []
	for ruta in nuestras[max(1, conservar) - 1:]:
		try:
			ruta.unlink()
			borradas.append(ruta)
		except OSError:
			log.warning("No se pudo borrar la copia antigua %s", ruta, exc_info=True)
	fuera = {_misma_ruta(r) for r in borradas}
	if fuera:
		_actualizar_estado(lambda estado: estado.__setitem__("programa_hechas", [h for h in estado.get("programa_hechas") or []
			if isinstance(h, dict) and _misma_ruta(h.get("ruta", "")) not in fuera]))
	return borradas


def problema_carpeta_programa(carpeta: str) -> str | None:
	"""Por qué una carpeta no vale para las copias programadas, o ``None``."""
	if not carpeta:
		return "Elige una carpeta para las copias."
	ruta = Path(carpeta)
	if not ruta.is_dir():
		return f"{carpeta} no es una carpeta que exista."
	for raiz in raices_copiadas():
		if _dentro(ruta, raiz):
			return f"{carpeta} está dentro de {raiz}, que entra en la copia. Elige una carpeta fuera, mejor en otro disco."
	return None


def aviso_carpeta_programa(carpeta: str) -> str | None:
	"""Algo que decir (sin impedirlo) sobre la carpeta elegida."""
	if carpeta and Path(carpeta).is_dir() and mismo_disco_que_casa(Path(carpeta)):
		return f"{carpeta} está en el mismo disco que tus datos: sus copias no sobrevivirían a perder el equipo."
	return None


def ejecutar_programada(cancelado: Cancelado | None = None, colectores: Sequence[Colector] | None = None,
		ahora: datetime | None = None, progreso: Progreso | None = None) -> ResultadoCopia:
	"""Una copia programada: se hace, se recuerda y se borran las antiguas.

	Solo se borran tras una copia completa y comprobada: una racha de fallos nunca se come
	las buenas. Lanza lo mismo que ``crear``; quien llama anota el fallo.
	"""
	programa = cargar_programa()
	anotar_intento(ahora)
	problema = problema_carpeta_programa(programa.carpeta)
	if problema:
		raise ValueError(problema)
	frase = None
	if programa.cifrar:
		frase = cargar_frase()
		if not frase:
			# Nunca sin cifrar en silencio: la persona eligió cifrar.
			raise ValueError("La frase de las copias programadas ya no está en este equipo. Vuelve a escribirla en "
				"Herramientas, Copias de seguridad, «Programar copias».")
	resultado = crear(Path(programa.carpeta), cancelado=cancelado, colectores=colectores, ahora=ahora, progreso=progreso, frase=frase)
	registrar(resultado)
	if resultado.ruta is not None:
		anotar_hecha(resultado.ruta)
	borrar_fallo()
	if resultado.completa and resultado.ruta is not None:
		quitadas = recortar(Path(programa.carpeta), programa.conservar, identidad_equipo()["id"], resultado.ruta)
		if quitadas:
			resultado.notas.append(f"Se han borrado {_plural(len(quitadas), 'copia antigua', 'copias antiguas')}; quedan {programa.conservar}.")
	return resultado


# ── Frase de las copias programadas, protegida por Windows (DPAPI) ───────────
# Solo esta cuenta de Windows puede recuperarla: eso deja hacer una copia sin nadie delante,
# y una copia que salga del equipo sigue cerrada. Nunca en settings.json ni en copias.json.

def _dpapi(datos: bytes, proteger: bool) -> bytes:
	from ctypes import wintypes

	class Blob(ctypes.Structure):
		_fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

	crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	memoria = ctypes.create_string_buffer(datos, len(datos))
	origen = Blob(len(datos), ctypes.cast(memoria, ctypes.POINTER(ctypes.c_char)))
	resultado = Blob()
	funcion = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
	funcion.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
		wintypes.DWORD, ctypes.POINTER(Blob)]
	funcion.restype = wintypes.BOOL
	kernel32.LocalFree.argtypes = [ctypes.c_void_p]
	kernel32.LocalFree.restype = ctypes.c_void_p
	sin_interfaz = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
	if not funcion(ctypes.byref(origen), None, None, None, None, sin_interfaz, ctypes.byref(resultado)):
		raise OSError(ctypes.get_last_error(), "Windows no ha podido proteger la frase")
	try:
		return ctypes.string_at(resultado.pbData, resultado.cbData)
	finally:
		kernel32.LocalFree(resultado.pbData)


def _ruta_frase() -> Path:
	return ruta_configuracion() / ARCHIVO_FRASE


def guardar_frase(frase: str) -> None:
	"""Guarda la frase protegida; lanza ``OSError`` si no se puede."""
	destino = _ruta_frase()
	temporal = destino.with_name(destino.name + ".tmp")
	try:
		temporal.write_bytes(_dpapi(frase.encode("utf-8"), proteger=True))
		os.replace(temporal, destino)
	finally:
		temporal.unlink(missing_ok=True)


def hay_frase() -> bool:
	"""Si hay una frase guardada, sin leerla."""
	return _ruta_frase().is_file()


def cargar_frase() -> str | None:
	"""La frase guardada, o ``None`` si no hay o no se puede leer (otra cuenta de Windows, otro equipo)."""
	ruta = _ruta_frase()
	try:
		if not ruta.is_file():
			return None
		frase = _dpapi(ruta.read_bytes(), proteger=False).decode("utf-8")
	except (OSError, ValueError):
		log.warning("No se pudo recuperar la frase de las copias programadas", exc_info=True)
		return None
	from app.core.registro import registrar_secreto

	registrar_secreto(frase)
	return frase


def borrar_frase() -> None:
	_ruta_frase().unlink(missing_ok=True)


# ── Restaurar ────────────────────────────────────────────────────────────────

_DISPOSITIVOS = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
#: Dónde vienen las sesiones dentro de la copia.
_RE_ROLLOUT = re.compile(r"^rollout-.*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", re.IGNORECASE)
_RE_CHAT_GEMINI = re.compile(r"^gemini/casa/tmp/[^/]+/chats/[^/]+\.json$")
BASE_CTRLIA = f"{ZONA_CTRLIA}/db/ctrlia.db"
#: Tamaño máximo de un chat de Gemini (JSON entero en memoria).
_MAX_CHAT_GEMINI = 256 * 1024 * 1024

#: Situación de una sesión de la copia respecto a este equipo.
EN_EL_EQUIPO, YA_RESTAURADA, FALTA = "en_el_equipo", "ya_restaurada", "falta"
NOMBRES_SITUACION = {EN_EL_EQUIPO: "Sigue en este equipo", YA_RESTAURADA: "Ya restaurada", FALTA: "No está en este equipo"}


@dataclass(slots=True)
class SesionEnCopia:
	"""Una sesión que se puede restaurar."""

	clave: str  # «claude:<id>», «codex:<id>», «gemini:<id>», «ctrlia:<uuid>» o «importada:<uuid>»
	proveedor: str
	titulo: str
	tipo: str  # cómo se leen sus mensajes: claude, codex, gemini o ctrlia
	carpeta: str = ""
	origen: str = "otra"
	creada: float = 0.0
	actividad: float = 0.0
	modelo: str = ""
	ruta: Path | None = None
	oculta: bool = False
	favorita: bool = False
	grupos: list[str] = field(default_factory=list)
	#: Identidad conservada de una sesión que ya estaba importada en la copia.
	identidad: str = ""
	equipo: str = ""
	situacion: str = FALTA


@dataclass(slots=True)
class ResultadoRestauracion:
	nuevas: int = 0
	actualizadas: int = 0
	sin_cambios: int = 0
	ocultas: int = 0
	fallidas: list[str] = field(default_factory=list)
	claves: list[str] = field(default_factory=list)
	notas: list[str] = field(default_factory=list)


def _nombre_restaurable(nombre: str) -> bool:
	"""Lo que hace falta sacar de la copia para listar y restaurar sus sesiones."""
	partes = PurePosixPath(nombre).parts
	if nombre == BASE_CTRLIA or nombre == "codex/casa/session_index.jsonl" or _RE_CHAT_GEMINI.match(nombre):
		return True
	if len(partes) == 5 and partes[:3] == ("claude", "casa", "projects") and partes[-1].endswith(".jsonl"):
		return True  # los de subagentes están más abajo y no son conversaciones aparte
	if len(partes) >= 3 and partes[0] == "claude" and partes[1].startswith(("escritorio-", "cowork-")):
		if partes[-1].startswith("local_") and partes[-1].endswith(".json"):
			return True
		if partes[1].startswith("cowork-") and len(partes) >= 6 and partes[-3] == "projects" and partes[-1].endswith(".jsonl"):
			return True
	if partes[:2] == ("codex", "casa") and len(partes) > 3 and partes[2] in ("sessions", "archived_sessions"):
		return _RE_ROLLOUT.match(partes[-1]) is not None
	prefijo = ("ctrlia", "datos", "conservadas", "claude")
	return len(partes) == 6 and partes[:4] == prefijo and partes[-1] in ("registro.json", "transcripcion.jsonl")


def _partes_seguras(nombre: str) -> bool:
	partes = PurePosixPath(nombre).parts
	return bool(partes) and nombre == "/".join(partes) and not any(
		p in (".", "..") or ":" in p or "\\" in p or "\x00" in p or p.rstrip(" .") != p or p.split(".", 1)[0].upper() in _DISPOSITIVOS
		for p in partes)


class CopiaAbierta:
	"""Una copia de la que se pueden sacar sesiones. Se crea desde un hilo; ``cerrar`` borra lo extraído.

	Los archivos se sacan a ``data/temp`` con un nombre numerado (no el suyo: así no hay
	rutas largas ni manera de salirse) y se vuelven a comprobar contra el manifiesto: una
	entrada dañada se salta y se dice, y el resto se puede restaurar igualmente.
	"""

	def __init__(self, ruta: Path, frase: str | None = None, cancelado: Cancelado | None = None, progreso: Progreso | None = None) -> None:
		self.ruta = Path(ruta)
		self.sesiones: list[SesionEnCopia] = []
		self.notas: list[str] = []
		self.manifiesto: dict[str, Any] = {}
		self._cancelado = cancelado
		self._extraidos: dict[str, Path] = {}
		self._modificados: dict[str, float] = {}
		self._temporal = tempfile.TemporaryDirectory(prefix="restaurar-", dir=ruta_temporales())
		self._cerrada = False
		try:
			self._abrir(frase, progreso)
		except BaseException:
			self.cerrar()
			raise

	def __enter__(self) -> CopiaAbierta:
		return self

	def __exit__(self, *_error: Any) -> None:
		self.cerrar()

	def cerrar(self) -> None:
		if not self._cerrada:
			self._cerrada = True
			self._temporal.cleanup()

	@property
	def equipo(self) -> str:
		datos = self.manifiesto.get("equipo")
		return str(datos.get("nombre") or "") if isinstance(datos, dict) else ""

	# ── Abrir ─────────────────────────────────────────────────────────────────

	def _abrir(self, frase: str | None, progreso: Progreso | None) -> None:
		carpeta = Path(self._temporal.name)
		llana = self.ruta
		if esta_cifrada(self.ruta):
			llana = carpeta / INTERIOR
			with open(llana, "wb") as salida:
				_descifrar(self.ruta, frase, salida, self._cancelado)
		try:
			with zipfile.ZipFile(llana) as archivo:
				self.manifiesto = self._leer_manifiesto(archivo)
				self._extraer(archivo, carpeta, progreso)
		except (zipfile.BadZipFile, zlib.error, EOFError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
			raise ValueError(f"{self.ruta.name} no es una copia de Ctrl+IA o está dañada.") from error
		finally:
			if llana != self.ruta:
				# Lo sin cifrar se queda en disco el mínimo tiempo posible.
				llana.unlink(missing_ok=True)
		self._listar()

	@staticmethod
	def _leer_manifiesto(archivo: zipfile.ZipFile) -> dict[str, Any]:
		if archivo.getinfo(MANIFIESTO).file_size > MAX_MANIFIESTO:
			raise ValueError("El manifiesto de esa copia es demasiado grande.")
		manifiesto = json.loads(archivo.read(MANIFIESTO).decode("utf-8"))
		if not isinstance(manifiesto, dict) or manifiesto.get("formato") != FORMATO or not isinstance(manifiesto.get("archivos"), list):
			raise ValueError("Ese archivo no es una copia de Ctrl+IA.")
		if int(manifiesto.get("version", 0)) > VERSION_FORMATO:
			raise ValueError("Esa copia la hizo una versión posterior de Ctrl+IA: actualízala para restaurarla.")
		return manifiesto

	def _extraer(self, archivo: zipfile.ZipFile, carpeta: Path, progreso: Progreso | None) -> None:
		inservibles = {n for n in self.manifiesto.get("inservibles") or [] if isinstance(n, str)}
		miembros = {info.filename: info for info in archivo.infolist()}
		entradas = [e for e in self.manifiesto["archivos"] if isinstance(e, dict) and isinstance(e.get("ruta"), str)
			and _nombre_restaurable(e["ruta"]) and e["ruta"] not in inservibles]
		for numero, entrada in enumerate(entradas):
			_cancelada(self._cancelado)
			if progreso is not None:
				progreso(numero, len(entradas), entrada["ruta"])
			nombre = entrada["ruta"]
			info = miembros.get(nombre)
			tamano, suma_esperada = entrada.get("tamano"), entrada.get("sha256")
			if (not _partes_seguras(nombre) or info is None or info.is_dir() or modos_stat.S_ISLNK(info.external_attr >> 16)
					or type(tamano) is not int or tamano < 0 or info.file_size != tamano or not isinstance(suma_esperada, str)):
				self.notas.append(f"Se ha saltado una entrada no válida: {nombre}")
				continue
			destino = carpeta / f"{numero:07d}{Path(nombre).suffix[:10]}"
			suma = hashlib.sha256()
			escritos = 0
			with archivo.open(info) as leido, open(destino, "xb") as salida:
				while trozo := leido.read(_TROZO):
					escritos += len(trozo)
					if escritos > tamano:
						break
					_cancelada(self._cancelado)
					suma.update(trozo)
					salida.write(trozo)
			if escritos != tamano or suma.hexdigest() != suma_esperada:
				destino.unlink(missing_ok=True)
				self.notas.append(f"Está dañado y se ha saltado: {nombre}")
				continue
			modificado = _instante(entrada.get("modificado"))
			if modificado is not None:
				sello = modificado.timestamp()
				os.utime(destino, (sello, sello))
				self._modificados[nombre] = sello
			self._extraidos[nombre] = destino

	# ── Listar ────────────────────────────────────────────────────────────────

	def _listar(self) -> None:
		propias, ids_propios, metadatos, grupos = self._leer_ctrlia()
		# Una sesión de Ctrl+IA ya tiene sus mensajes en la base: su transcripción no sale otra vez.
		encontradas = [*self._sesiones_claude(ids_propios), *self._sesiones_codex(ids_propios), *self._sesiones_gemini(ids_propios)]
		for sesion in [*propias, *encontradas]:
			meta = metadatos.get(sesion.clave)
			if meta is not None:
				sesion.titulo = meta.get("titulo") or sesion.titulo
				sesion.oculta = bool(meta.get("oculta"))
				sesion.favorita = bool(meta.get("favorita"))
			sesion.grupos = grupos.get(sesion.clave, [])
		self.sesiones = sorted([*propias, *encontradas], key=lambda s: -s.actividad)

	def _leer_ctrlia(self) -> tuple[list[SesionEnCopia], set[tuple[str, str]], dict[str, dict[str, Any]], dict[str, list[str]]]:
		"""Sesiones de la base de Ctrl+IA de la copia, sus ids en los agentes y los títulos, ocultas y grupos de cualquier sesión."""
		ids_propios: set[tuple[str, str]] = set()
		if BASE_CTRLIA not in self._extraidos:
			return [], ids_propios, {}, {}
		sesiones: list[SesionEnCopia] = []
		metadatos: dict[str, dict[str, Any]] = {}
		grupos: dict[str, list[str]] = {}
		try:
			with self._base() as base:
				for fila in base.execute("SELECT s.*, (SELECT COUNT(*) FROM mensajes m WHERE m.sesion = s.clave) AS total FROM sesiones s"):
					datos = dict(fila)
					clave = str(datos["clave"])
					if datos.get("id_externo"):
						ids_propios.add((str(datos.get("proveedor") or ""), str(datos["id_externo"])))
					if not datos["total"]:
						continue
					extra = _objeto_json(datos.get("extra"))
					importada = extra.get("importada") if isinstance(extra.get("importada"), dict) else {}
					sesiones.append(SesionEnCopia(
						clave=clave, proveedor=str(datos.get("proveedor") or "otro"), titulo=str(datos.get("titulo") or "Sesión sin título"),
						tipo="ctrlia", carpeta=str(datos.get("carpeta") or ""), origen=str(importada.get("origen") or "propia"),
						creada=float(datos.get("creada") or 0), actividad=float(datos.get("actividad") or 0),
						modelo=str(datos.get("modelo") or ""), identidad=str(importada.get("identidad") or ""),
						equipo=str(importada.get("equipo") or ""),
					))
				for fila in base.execute("SELECT * FROM metadatos"):
					metadatos[str(fila["clave"])] = dict(fila)
				for fila in base.execute("SELECT sg.clave AS clave, g.nombre AS nombre FROM sesion_grupo sg JOIN grupos g ON g.id = sg.grupo"):
					grupos.setdefault(str(fila["clave"]), []).append(str(fila["nombre"]))
		except sqlite3.Error:
			log.warning("No se pudo leer la base de Ctrl+IA de la copia", exc_info=True)
			self.notas.append("No se ha podido leer la base de datos de Ctrl+IA de esta copia.")
		return sesiones, ids_propios, metadatos, grupos

	@contextlib.contextmanager
	def _base(self) -> Iterator[sqlite3.Connection]:
		ruta = self._extraidos[BASE_CTRLIA]
		conexion = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
		try:
			conexion.row_factory = sqlite3.Row
			yield conexion
		finally:
			conexion.close()

	def _sesiones_claude(self, ids_propios: set[tuple[str, str]]) -> list[SesionEnCopia]:
		"""Transcripciones de Claude Code: las de la carpeta del agente y, si faltan, las conservadas."""
		escritorio: dict[str, tuple[dict[str, Any], bool]] = {}
		for nombre, ruta in self._extraidos.items():
			partes = PurePosixPath(nombre).parts
			if partes[0] == "claude" and partes[-1].startswith("local_") and partes[-1].endswith(".json"):
				datos = historial_claude._leer_json(ruta)
				id_cli = datos.get("cliSessionId") if datos else None
				if isinstance(id_cli, str) and historial_claude.es_id_seguro(id_cli):
					escritorio[id_cli] = (datos, partes[1].startswith("cowork-"))
		sesiones: dict[str, SesionEnCopia] = {}
		for nombre, ruta in self._extraidos.items():
			if not nombre.endswith(".jsonl") or not nombre.startswith("claude/"):
				continue
			identificador = PurePosixPath(nombre).stem
			if not historial_claude.es_id_seguro(identificador) or ("claude", identificador) in ids_propios:
				continue
			datos, _mtime = historial_claude._datos_transcript(ruta, con_cola=True)
			if datos is None:
				self.notas.append(f"No se ha podido leer una conversación de Claude Code: {nombre}")
				continue
			de_escritorio = escritorio.get(identificador)
			if de_escritorio is None and (datos.entrada.startswith("sdk") or datos.entrada == "claude-desktop" or not datos.primer_mensaje):
				continue  # igual que en la lista: órdenes «claude -p» de otros programas o sesiones vacías
			actividad = self._modificados.get(nombre, 0.0)
			sesion = SesionEnCopia(
				clave=f"claude:{identificador}", proveedor="claude", titulo=datos.titulo or "Sesión de Claude", tipo="claude",
				carpeta=datos.carpeta, origen="terminal" if datos.entrada == "cli" else "otra", creada=datos.creada or actividad,
				actividad=actividad, ruta=ruta,
			)
			if de_escritorio is not None:
				meta, cowork = de_escritorio
				sesion.titulo = historial_claude._una_linea(meta.get("title")) or sesion.titulo
				sesion.carpeta = str(meta.get("cwd") or "") or sesion.carpeta
				sesion.origen = "cowork" if cowork else "escritorio"
				sesion.creada = historial_claude._segundos(meta.get("createdAt")) or sesion.creada
				sesion.actividad = max(historial_claude._segundos(meta.get("lastActivityAt")), sesion.actividad)
			sesiones[identificador] = sesion
		# Las copias conservadas pueden ser lo único que queda de una conversación.
		prefijo = f"{ZONA_CTRLIA}/conservadas/claude/"
		for nombre, ruta in self._extraidos.items():
			if not (nombre.startswith(prefijo) and nombre.endswith("/registro.json")):
				continue
			identificador = nombre[len(prefijo):].split("/", 1)[0]
			transcripcion = self._extraidos.get(f"{prefijo}{identificador}/transcripcion.jsonl")
			if transcripcion is None or identificador in sesiones or ("claude", identificador) in ids_propios:
				continue
			try:
				meta = json.loads(ruta.read_text(encoding="utf-8"))["metadatos"]
				sesiones[identificador] = SesionEnCopia(
					clave=f"claude:{identificador}", proveedor="claude", titulo=str(meta.get("titulo") or "Sesión de Claude"),
					tipo="claude", carpeta=str(meta.get("carpeta") or ""), origen=str(meta.get("origen") or "otra"),
					creada=float(meta.get("creada") or 0), actividad=float(meta.get("actividad") or 0), ruta=transcripcion,
				)
			except (OSError, ValueError, KeyError, TypeError, AttributeError):
				log.debug("Registro conservado ilegible en la copia: %s", nombre, exc_info=True)
				self.notas.append(f"No se ha podido leer un chat conservado: {nombre}")
		return list(sesiones.values())

	def _sesiones_codex(self, ids_propios: set[tuple[str, str]]) -> list[SesionEnCopia]:
		titulos: dict[str, tuple[str, float]] = {}
		indice = self._extraidos.get("codex/casa/session_index.jsonl")
		if indice is not None:
			try:
				with open(indice, encoding="utf-8", errors="replace") as archivo:
					for linea in archivo:
						try:
							datos = json.loads(linea)
						except ValueError:
							continue
						if isinstance(datos, dict) and isinstance(datos.get("id"), str):
							titulos[datos["id"].lower()] = (str(datos.get("thread_name") or ""), _fecha(datos.get("updated_at")))
			except OSError:
				log.debug("No se pudo leer el índice de Codex de la copia", exc_info=True)
		sesiones: dict[str, SesionEnCopia] = {}
		for nombre, ruta in self._extraidos.items():
			coincidencia = _RE_ROLLOUT.match(PurePosixPath(nombre).name) if nombre.startswith("codex/") else None
			if coincidencia is None:
				continue
			identificador = coincidencia.group(1).lower()
			if ("codex", identificador) in ids_propios or identificador in sesiones:
				continue
			carpeta, creada = _datos_rollout(ruta)
			titulo, actualizada = titulos.get(identificador, ("", 0.0))
			actividad = actualizada or self._modificados.get(nombre, 0.0)
			if not titulo:
				mensajes = historial_codex.leer_mensajes(ruta, self._cancelado)
				if not mensajes:
					continue
				titulo = " ".join(mensajes[0]["texto"].split())[:120]
			sesiones[identificador] = SesionEnCopia(clave=f"codex:{identificador}", proveedor="codex", titulo=titulo, tipo="codex",
				carpeta=carpeta, origen="codex", creada=creada or actividad, actividad=actividad, ruta=ruta)
		return list(sesiones.values())

	def _sesiones_gemini(self, ids_propios: set[tuple[str, str]]) -> list[SesionEnCopia]:
		sesiones: dict[str, SesionEnCopia] = {}
		for nombre, ruta in self._extraidos.items():
			if not _RE_CHAT_GEMINI.match(nombre):
				continue
			datos = _chat_gemini(ruta)
			mensajes = _mensajes_gemini(datos)
			identificador = str(datos.get("sessionId") or PurePosixPath(nombre).stem)
			if not mensajes or ("gemini", identificador) in ids_propios:
				continue
			actividad = _fecha(datos.get("lastUpdated")) or self._modificados.get(nombre, 0.0)
			sesiones[identificador] = SesionEnCopia(clave=f"gemini:{identificador}", proveedor="gemini",
				titulo=" ".join(mensajes[0]["texto"].split())[:120] or "Chat de Gemini", tipo="gemini", origen="gemini",
				creada=_fecha(datos.get("startTime")) or actividad, actividad=actividad, ruta=ruta)
		return list(sesiones.values())

	def situar(self, almacen: Any, presentes: Iterable[str]) -> None:
		"""Dice de cada sesión si sigue en este equipo, si ya se restauró o si falta. ``presentes``: claves de la lista."""
		ya = almacen.sesiones_importadas()
		claves = set(presentes)
		for sesion in self.sesiones:
			if self._identidad(sesion) in ya:
				sesion.situacion = YA_RESTAURADA
			elif sesion.clave in claves:
				sesion.situacion = EN_EL_EQUIPO
			else:
				sesion.situacion = FALTA

	# ── Mensajes ──────────────────────────────────────────────────────────────

	def mensajes(self, sesion: SesionEnCopia) -> list[dict[str, Any]]:
		"""Los mensajes de una sesión tal como los guarda el almacén."""
		if sesion.tipo == "claude" and sesion.ruta is not None:
			return [{"rol": m.rol, "texto": m.texto, "fecha": m.fecha, "detalle": m.detalle, "herramienta": m.herramienta,
				"id_origen": m.id, "datos": {"cambios": list(m.cambios)} if m.cambios else {}}
				for m in historial_claude.leer_mensajes(sesion.ruta)]
		if sesion.tipo == "codex" and sesion.ruta is not None:
			return [dict(m) for m in historial_codex.leer_mensajes(sesion.ruta, self._cancelado)]
		if sesion.tipo == "gemini" and sesion.ruta is not None:
			return _mensajes_gemini(_chat_gemini(sesion.ruta))
		if sesion.tipo == "ctrlia":
			with self._base() as base:
				return [{"rol": f["rol"], "texto": f["texto"], "fecha": f["fecha"], "detalle": f["detalle"], "herramienta": f["herramienta"],
					"id_origen": f["id_origen"], "datos": _objeto_json(f["datos"])}
					for f in base.execute("SELECT * FROM mensajes WHERE sesion = ? ORDER BY id", (sesion.clave,))]
		return []

	# ── Restaurar ─────────────────────────────────────────────────────────────

	def _identidad(self, sesion: SesionEnCopia) -> str:
		"""La misma que usa ``intercambio``: una sesión exportada y restaurada no se duplica."""
		return sesion.identidad or f"{self.equipo or 'Equipo sin nombre'}|{sesion.clave}"

	def restaurar(self, elegidas: Sequence[SesionEnCopia], almacen: Any, progreso: Progreso | None = None,
			cancelado: Cancelado | None = None) -> ResultadoRestauracion:
		"""Guarda ``elegidas`` como sesiones importadas de solo lectura. Nunca toca los archivos de los agentes.

		Las que ya estaban se actualizan si han crecido y no se duplican. Los grupos, la marca
		de oculta y el título propio solo se ponen la primera vez: si ya estaba, manda lo que
		se haya hecho aquí desde entonces.
		"""
		resultado = ResultadoRestauracion(notas=list(self.notas))
		ya = almacen.sesiones_importadas()
		for numero, sesion in enumerate(elegidas):
			_cancelada(cancelado)
			if progreso is not None:
				progreso(numero, len(elegidas), sesion.titulo)
			try:
				self._restaurar_una(sesion, almacen, ya, resultado)
			except (OSError, ValueError, TypeError, sqlite3.Error):
				log.warning("No se pudo restaurar «%s»", sesion.clave, exc_info=True)
				resultado.fallidas.append(sesion.titulo)
		if progreso is not None:
			progreso(len(elegidas), len(elegidas), "")
		log.info("Restauración de %s: %s nuevas, %s actualizadas, %s sin cambios, %s fallidas", self.ruta.name,
			resultado.nuevas, resultado.actualizadas, resultado.sin_cambios, len(resultado.fallidas))
		return resultado

	def _restaurar_una(self, sesion: SesionEnCopia, almacen: Any, ya: dict[str, str], resultado: ResultadoRestauracion) -> None:
		from app.services.almacen import SesionPropia, nueva_clave_importada

		mensajes = self.mensajes(sesion)
		identidad = self._identidad(sesion)
		actividad = sesion.actividad or max((float(m.get("fecha") or 0) for m in mensajes), default=0.0) or time.time()
		equipo = sesion.equipo or self.equipo or "Equipo sin nombre"
		extra = {"importada": {"identidad": identidad, "equipo": equipo, "clave_original": sesion.clave, "origen": sesion.origen,
			"importada": time.time(), "copia": self.ruta.name}}
		existente = ya.get(identidad)
		guardada = almacen.sesion(existente) if existente is not None else None
		if guardada is not None:
			if len(almacen.mensajes(existente)) == len(mensajes) and abs(guardada.actividad - actividad) < 1:
				resultado.sin_cambios += 1
				return
			almacen.actualizar_sesion(existente, carpeta=sesion.carpeta, modelo=sesion.modelo, actividad=actividad, extra=extra)
			almacen.reemplazar_mensajes(existente, mensajes)
			resultado.actualizadas += 1
			resultado.claves.append(existente)
			return
		clave = nueva_clave_importada()
		almacen.guardar_sesion(SesionPropia(clave=clave, proveedor=sesion.proveedor, titulo=sesion.titulo[:500], carpeta=sesion.carpeta,
			modelo=sesion.modelo, creada=sesion.creada or actividad, actividad=actividad, extra=extra))
		almacen.reemplazar_mensajes(clave, mensajes)
		if sesion.favorita:
			almacen.fijar_metadato(clave, favorita=True)
		if sesion.oculta:
			almacen.fijar_metadato(clave, oculta=True)
			resultado.ocultas += 1
		for nombre in sesion.grupos:
			nombre = " ".join(nombre.split())[:80]
			if nombre:
				almacen.anadir_a_grupo(clave, almacen.crear_grupo(nombre))
		ya[identidad] = clave
		resultado.nuevas += 1
		resultado.claves.append(clave)


def _objeto_json(texto: Any) -> dict[str, Any]:
	try:
		datos = json.loads(texto) if isinstance(texto, str) else texto
	except ValueError:
		return {}
	return datos if isinstance(datos, dict) else {}


def _fecha(valor: Any) -> float:
	from app.services.historial.comun import fecha

	return fecha(valor)


def _datos_rollout(ruta: Path) -> tuple[str, float]:
	"""``(carpeta, creada)`` de la primera línea de un rollout de Codex."""
	try:
		with open(ruta, encoding="utf-8", errors="replace") as archivo:
			primera = archivo.readline(_TROZO)
		datos = json.loads(primera)
	except (OSError, ValueError):
		return "", 0.0
	carga = datos.get("payload") if isinstance(datos, dict) else None
	if not isinstance(carga, dict) or datos.get("type") != "session_meta":
		return "", 0.0
	return str(carga.get("cwd") or ""), _fecha(carga.get("timestamp"))


def _chat_gemini(ruta: Path) -> dict[str, Any]:
	try:
		if ruta.stat().st_size > _MAX_CHAT_GEMINI:
			return {}
		datos = json.loads(ruta.read_text(encoding="utf-8", errors="replace"))
	except (OSError, ValueError):
		log.debug("Chat de Gemini ilegible: %s", ruta, exc_info=True)
		return {}
	return datos if isinstance(datos, dict) else {}


def _mensajes_gemini(datos: dict[str, Any]) -> list[dict[str, Any]]:
	"""Mensajes de un chat de Gemini CLI (``tmp/<proyecto>/chats/session-*.json``): formato sin documentar, leído a la defensiva."""
	mensajes = []
	for mensaje in datos.get("messages") or []:
		if not isinstance(mensaje, dict):
			continue
		rol = {"user": "usuario", "gemini": "asistente", "model": "asistente"}.get(str(mensaje.get("type")))
		if rol is None:
			continue
		contenido = mensaje.get("content")
		if isinstance(contenido, list):
			contenido = "\n".join(str(p.get("text") or "") for p in contenido if isinstance(p, dict))
		if isinstance(contenido, str) and contenido.strip():
			mensajes.append({"rol": rol, "texto": contenido, "fecha": _fecha(mensaje.get("timestamp"))})
	return mensajes


def resumen_restauracion(resultado: ResultadoRestauracion) -> str:
	"""Lo que se dice al acabar de restaurar."""
	partes = []
	if resultado.nuevas:
		partes.append(_plural(resultado.nuevas, "sesión restaurada", "sesiones restauradas"))
		if resultado.ocultas:
			partes.append(f"{resultado.ocultas} de ellas ocultas, como estaban (Ver, Mostrar, Ocultas)")
	if resultado.actualizadas:
		partes.append(_plural(resultado.actualizadas, "sesión actualizada", "sesiones actualizadas"))
	if resultado.sin_cambios:
		partes.append(f"{resultado.sin_cambios} sin cambios")
	if resultado.fallidas:
		partes.append(f"{len(resultado.fallidas)} no se han podido leer")
	if not partes:
		texto = "No se ha restaurado nada."
	else:
		texto = ("Restauración terminada: " + ", ".join(partes) + ". Están en la lista de sesiones como importadas, solo para "
			"leer (Ver, Mostrar, Importadas de otro equipo).")
	notas = list(dict.fromkeys(resultado.notas))
	return texto + (" " + " ".join(notas[:5]) if notas else "")
