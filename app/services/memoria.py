"""Archivos de instrucciones y memoria que leen los agentes («Lo que el agente sabe de ti»).

Cada agente de programación lee, al empezar a trabajar, unos archivos de texto
con instrucciones del usuario y del proyecto, y algunos guardan además su
propia memoria. Este módulo los descubre (existan o no) para que la interfaz
los liste, los muestre y permita crear los que faltan:

- **Claude Code** (raíz ``CLAUDE_CONFIG_DIR`` o ``~/.claude``): ``CLAUDE.md``,
  ``settings.json``, ``agents/*.md``, ``skills/*/SKILL.md`` y
  ``commands/*.md``; en el proyecto ``CLAUDE.md``, ``CLAUDE.local.md``,
  ``.claude/CLAUDE.md``, ``.claude/settings.json`` y las mismas carpetas de
  agentes, habilidades y comandos; y la memoria automática en
  ``<raíz>/projects/<carpeta codificada>/memory/*.md``.
- **Codex** (``CODEX_HOME`` o ``~/.codex``): ``AGENTS.md``, ``config.toml`` y,
  si existen, ``AGENTS.override.md``, ``skills/*/SKILL.md`` y ``prompts/*.md``;
  las habilidades de ``~/.agents/skills``; en el proyecto ``AGENTS.md`` y, si
  existen, ``AGENTS.override.md`` y ``.agents/skills/*/SKILL.md``.
- **Antigravity y Gemini** (``~/.gemini``): ``GEMINI.md`` y
  ``antigravity-cli/settings.json``; en el proyecto ``GEMINI.md``,
  ``AGENTS.md`` y ``.agents/skills/*/SKILL.md``.

Solo lee el disco. La única escritura es :func:`crear_desde_plantilla`, que
crea un archivo de instrucciones NUEVO (nunca sobrescribe uno existente).

Sin wx: todo este módulo se prueba por sí solo.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.services.historial.claude import codificar_carpeta, raiz_claude

log = logging.getLogger(__name__)

#: Ámbitos (texto visible de la columna «Ámbito»).
GLOBAL = "Global"
PROYECTO = "Proyecto"

#: Grupos de agentes, en el orden por defecto.
CLAUDE = "claude"
CODEX = "codex"
ANTIGRAVITY = "antigravity"
GRUPOS = (CLAUDE, CODEX, ANTIGRAVITY)

#: Nombre visible de cada grupo.
NOMBRES_GRUPO = {
	CLAUDE: "Claude",
	CODEX: "Codex",
	ANTIGRAVITY: "Antigravity y Gemini",
}

#: Clases de archivo.
INSTRUCCIONES = "instrucciones"
AJUSTES = "ajustes"
AGENTE = "agente"
HABILIDAD = "habilidad"
COMANDO = "comando"
MEMORIA = "memoria"

#: Tamaño máximo (bytes) de un archivo que se abre en el visor.
LIMITE_VER = 1024 * 1024

#: Órdenes de la lista (``memoria.orden`` en la configuración).
ORDEN_AGENTE = "agente"
ORDEN_RECIENTES = "recientes"
ORDEN_ANTIGUOS = "antiguos"
ORDEN_NOMBRE = "nombre"
ORDENES = (ORDEN_AGENTE, ORDEN_RECIENTES, ORDEN_ANTIGUOS, ORDEN_NOMBRE)


@dataclass(slots=True)
class ArchivoMemoria:
	"""Un archivo que un agente lee (o leería si existiera).

	:param nombre: nombre amigable («Tus instrucciones generales para Claude»).
	:param ruta: ruta completa del archivo.
	:param ambito: :data:`GLOBAL` o :data:`PROYECTO`.
	:param agente: grupo de agentes que lo lee (:data:`CLAUDE`, :data:`CODEX`, :data:`ANTIGRAVITY`).
	:param existe: si el archivo está en disco.
	:param creable: se puede crear desde Ctrl+IA (archivo ``.md`` de instrucciones que aún no existe).
	:param tipo: clase de archivo (:data:`INSTRUCCIONES`, :data:`AJUSTES`...).
	:param modificado: fecha de la última modificación (``st_mtime``); ``None`` si no existe o no se puede leer.
	"""

	nombre: str
	ruta: Path
	ambito: str
	agente: str
	existe: bool
	creable: bool
	tipo: str = INSTRUCCIONES
	modificado: float | None = None

	@property
	def nombre_agente(self) -> str:
		"""Nombre visible del grupo de agentes («Claude», «Codex»...)."""
		return NOMBRES_GRUPO.get(self.agente, self.agente)


# --------------------------------------------------------------------------
# Rutas raíz
# --------------------------------------------------------------------------


def raiz_codex() -> Path:
	"""Raíz de configuración de Codex: ``CODEX_HOME`` o ``~/.codex``."""
	otra = os.environ.get("CODEX_HOME")
	return Path(otra) if otra else Path.home() / ".codex"


def raiz_gemini() -> Path:
	"""Carpeta ``~/.gemini`` (Gemini CLI y Antigravity CLI)."""
	return Path.home() / ".gemini"


def carpeta_memoria_claude(carpeta: Path) -> Path:
	"""Carpeta de la memoria automática de Claude Code para el proyecto ``carpeta``."""
	absoluta = os.path.abspath(str(carpeta))
	return raiz_claude() / "projects" / codificar_carpeta(absoluta) / "memory"


def grupo_de(proveedor: str) -> str:
	"""Grupo de archivos de un identificador de agente (``claude_acp`` → ``claude``)."""
	proveedor = (proveedor or "").lower()
	if proveedor.startswith("claude"):
		return CLAUDE
	if proveedor.startswith("codex"):
		return CODEX
	if proveedor.startswith(("antigravity", "gemini", "agy")):
		return ANTIGRAVITY
	return ""


# --------------------------------------------------------------------------
# Descubrimiento
# --------------------------------------------------------------------------


def _es_archivo(ruta: Path) -> bool:
	try:
		return ruta.is_file()
	except OSError:
		log.debug("No se pudo comprobar %s", ruta, exc_info=True)
		return False


def _listar(carpeta: Path, patron: str, recursivo: bool = False) -> list[Path]:
	"""Archivos de ``carpeta`` que cumplen ``patron``, ordenados; ``[]`` si no se puede leer."""
	try:
		if not carpeta.is_dir():
			return []
		encontrados = carpeta.rglob(patron) if recursivo else carpeta.glob(patron)
		return sorted((ruta for ruta in encontrados if ruta.is_file()), key=lambda r: str(r).lower())
	except OSError:
		log.debug("No se pudo listar %s", carpeta, exc_info=True)
		return []


def fecha_modificacion(ruta: Path) -> float | None:
	"""``st_mtime`` del archivo; ``None`` si no existe o no se puede leer."""
	try:
		return Path(ruta).stat().st_mtime
	except (OSError, ValueError):
		return None


def _fijo(nombre: str, ruta: Path, ambito: str, agente: str, tipo: str) -> ArchivoMemoria:
	"""Archivo de ruta conocida: se lista aunque no exista."""
	existe = _es_archivo(ruta)
	creable = not existe and tipo == INSTRUCCIONES and ruta.suffix.lower() == ".md"
	return ArchivoMemoria(nombre, ruta, ambito, agente, existe, creable, tipo, fecha_modificacion(ruta) if existe else None)


def _encontrado(nombre: str, ruta: Path, ambito: str, agente: str, tipo: str) -> ArchivoMemoria:
	"""Archivo hallado al recorrer una carpeta (siempre existe)."""
	return ArchivoMemoria(nombre, ruta, ambito, agente, True, False, tipo, fecha_modificacion(ruta))


def _extensiones_claude(base: Path, ambito: str) -> list[ArchivoMemoria]:
	"""Agentes personalizados, habilidades y comandos de una carpeta de Claude Code."""
	archivos = []
	for ruta in _listar(base / "agents", "*.md"):
		archivos.append(_encontrado(f"Agente personalizado: {ruta.stem}", ruta, ambito, CLAUDE, AGENTE))
	for ruta in _listar(base / "skills", "*/SKILL.md"):
		archivos.append(_encontrado(f"Habilidad: {ruta.parent.name}", ruta, ambito, CLAUDE, HABILIDAD))
	for ruta in _listar(base / "commands", "*.md", recursivo=True):
		archivos.append(_encontrado(f"Comando: /{ruta.stem}", ruta, ambito, CLAUDE, COMANDO))
	return archivos


def _archivos_claude(carpeta: Path | None) -> list[ArchivoMemoria]:
	raiz = raiz_claude()
	archivos = [
		_fijo("Tus instrucciones generales para Claude", raiz / "CLAUDE.md", GLOBAL, CLAUDE, INSTRUCCIONES),
		_fijo("Ajustes de Claude Code", raiz / "settings.json", GLOBAL, CLAUDE, AJUSTES),
	]
	archivos += _extensiones_claude(raiz, GLOBAL)
	if carpeta is None:
		return archivos
	archivos += [
		_fijo("Instrucciones de este proyecto", carpeta / "CLAUDE.md", PROYECTO, CLAUDE, INSTRUCCIONES),
		_fijo("Tus instrucciones privadas de este proyecto", carpeta / "CLAUDE.local.md", PROYECTO, CLAUDE, INSTRUCCIONES),
		_fijo(
			"Instrucciones de este proyecto (carpeta .claude)", carpeta / ".claude" / "CLAUDE.md", PROYECTO, CLAUDE,
			INSTRUCCIONES,
		),
		_fijo("Ajustes de Claude Code de este proyecto", carpeta / ".claude" / "settings.json", PROYECTO, CLAUDE, AJUSTES),
	]
	privados = carpeta / ".claude" / "settings.local.json"
	if _es_archivo(privados):
		archivos.append(_encontrado("Tus ajustes privados de Claude Code en este proyecto", privados, PROYECTO, CLAUDE, AJUSTES))
	archivos += _extensiones_claude(carpeta / ".claude", PROYECTO)
	memoria = carpeta_memoria_claude(carpeta)
	notas = _listar(memoria, "*.md")
	if not notas:
		archivos.append(_fijo("Memoria automática del proyecto", memoria / "MEMORY.md", PROYECTO, CLAUDE, MEMORIA))
	else:
		notas.sort(key=lambda r: (r.name.upper() != "MEMORY.MD", r.name.lower()))
		for ruta in notas:
			nombre = "Memoria automática del proyecto"
			if ruta.name.upper() != "MEMORY.MD":
				nombre += f": {ruta.stem}"
			archivos.append(_encontrado(nombre, ruta, PROYECTO, CLAUDE, MEMORIA))
	return archivos


def _si_existe(nombre: str, ruta: Path, ambito: str, agente: str, tipo: str) -> list[ArchivoMemoria]:
	"""El archivo, solo si está en disco (los opcionales no se listan como «no existe»)."""
	return [_encontrado(nombre, ruta, ambito, agente, tipo)] if _es_archivo(ruta) else []


def _habilidades(carpeta: Path, ambito: str, agente: str) -> list[ArchivoMemoria]:
	"""Habilidades de una carpeta ``skills``: cada una es ``<nombre>/SKILL.md``."""
	return [
		_encontrado(f"Habilidad: {ruta.parent.name}", ruta, ambito, agente, HABILIDAD)
		for ruta in _listar(carpeta, "*/SKILL.md")
	]


def _archivos_codex(carpeta: Path | None) -> list[ArchivoMemoria]:
	raiz = raiz_codex()
	archivos = [
		_fijo("Tus instrucciones generales para Codex", raiz / "AGENTS.md", GLOBAL, CODEX, INSTRUCCIONES),
		*_si_existe(
			"Tus instrucciones generales para Codex que sustituyen a AGENTS.md (AGENTS.override.md)",
			raiz / "AGENTS.override.md", GLOBAL, CODEX, INSTRUCCIONES,
		),
		_fijo("Ajustes de Codex", raiz / "config.toml", GLOBAL, CODEX, AJUSTES),
	]
	archivos += _habilidades(raiz / "skills", GLOBAL, CODEX)
	archivos += _habilidades(Path.home() / ".agents" / "skills", GLOBAL, CODEX)
	for ruta in _listar(raiz / "prompts", "*.md"):
		archivos.append(_encontrado(f"Comando: /prompts:{ruta.stem}", ruta, GLOBAL, CODEX, COMANDO))
	if carpeta is not None:
		archivos.append(_agents_md_proyecto(carpeta, CODEX))
		archivos += _si_existe(
			"Instrucciones de este proyecto para Codex que sustituyen a AGENTS.md (AGENTS.override.md)",
			carpeta / "AGENTS.override.md", PROYECTO, CODEX, INSTRUCCIONES,
		)
		archivos += _habilidades(carpeta / ".agents" / "skills", PROYECTO, CODEX)
	return archivos


def _agents_md_proyecto(carpeta: Path, agente: str) -> ArchivoMemoria:
	return _fijo(
		"Instrucciones de este proyecto para varios agentes (AGENTS.md)", carpeta / "AGENTS.md", PROYECTO, agente,
		INSTRUCCIONES,
	)


def _archivos_antigravity(carpeta: Path | None) -> list[ArchivoMemoria]:
	raiz = raiz_gemini()
	archivos = [
		_fijo("Tus instrucciones generales para Gemini y Antigravity", raiz / "GEMINI.md", GLOBAL, ANTIGRAVITY, INSTRUCCIONES),
		_fijo(
			"Ajustes y reglas de permisos de Antigravity CLI", raiz / "antigravity-cli" / "settings.json", GLOBAL,
			ANTIGRAVITY, AJUSTES,
		),
	]
	if carpeta is None:
		return archivos
	archivos += [
		_fijo("Instrucciones de este proyecto para Gemini y Antigravity", carpeta / "GEMINI.md", PROYECTO, ANTIGRAVITY, INSTRUCCIONES),
		_agents_md_proyecto(carpeta, ANTIGRAVITY),
	]
	for ruta in _listar(carpeta / ".agents" / "skills", "*/SKILL.md"):
		archivos.append(_encontrado(f"Habilidad: {ruta.parent.name}", ruta, PROYECTO, ANTIGRAVITY, HABILIDAD))
	return archivos


_POR_GRUPO = {
	CLAUDE: _archivos_claude,
	CODEX: _archivos_codex,
	ANTIGRAVITY: _archivos_antigravity,
}


def orden_grupos(primero: str = "") -> list[str]:
	"""Grupos en el orden en que se listan: el del agente indicado, el primero."""
	grupo = grupo_de(primero) or (primero if primero in GRUPOS else "")
	return ([grupo] if grupo else []) + [g for g in GRUPOS if g != grupo]


def _sin_repetidos(archivos: Iterable[ArchivoMemoria]) -> list[ArchivoMemoria]:
	"""Quita los archivos repetidos: queda el primero.

	``AGENTS.md`` lo leen varios agentes, y en una sesión de tu carpeta personal
	su carpeta ``.claude`` es la tuya: sus ajustes, habilidades y agentes se
	verían dos veces. Se compara la ruta real (mayúsculas, enlaces y uniones de
	carpetas incluidos).
	"""
	vistos: set[str] = set()
	resultado = []
	for archivo in archivos:
		clave = os.path.normcase(os.path.realpath(str(archivo.ruta)))
		if clave in vistos:
			continue
		vistos.add(clave)
		resultado.append(archivo)
	return resultado


def archivos_memoria(carpeta: Path | None, primero: str = "") -> list[ArchivoMemoria]:
	"""Archivos de instrucciones y memoria de todos los agentes, agrupados por agente.

	:param carpeta: carpeta del proyecto; ``None`` = solo los archivos globales.
	:param primero: identificador del agente cuyo grupo va primero (``"claude"``,
		``"codex"``, ``"antigravity"``, ``"gemini"``, ``"claude_acp"``...).
	"""
	carpeta = Path(carpeta) if carpeta is not None else None
	archivos: list[ArchivoMemoria] = []
	for grupo in orden_grupos(primero):
		try:
			archivos += _POR_GRUPO[grupo](carpeta)
		except OSError:
			log.warning("No se pudieron revisar los archivos de %s", grupo, exc_info=True)
	return _sin_repetidos(archivos)


def ordenar(archivos: Iterable[ArchivoMemoria], orden: str) -> list[ArchivoMemoria]:
	"""Los archivos en el orden pedido (:data:`ORDENES`); los empates conservan el orden por agente.

	- :data:`ORDEN_AGENTE`: el de :func:`archivos_memoria` (agrupados por agente).
	- :data:`ORDEN_RECIENTES` / :data:`ORDEN_ANTIGUOS`: por fecha de modificación; los
	  que no existen o cuya fecha no se puede leer van siempre al final.
	- :data:`ORDEN_NOMBRE`: por nombre visible, de la A a la Z, sin distinguir mayúsculas.

	Un orden desconocido se trata como :data:`ORDEN_AGENTE`.
	"""
	lista = list(archivos)
	if orden in (ORDEN_RECIENTES, ORDEN_ANTIGUOS):
		signo = -1 if orden == ORDEN_RECIENTES else 1
		# ``sorted`` es estable: con la misma fecha queda el orden por agente.
		return sorted(lista, key=lambda a: (a.modificado is None, signo * (a.modificado or 0.0)))
	if orden == ORDEN_NOMBRE:
		return sorted(lista, key=lambda a: _clave_nombre(a.nombre))
	return lista


def _clave_nombre(nombre: str) -> str:
	"""Clave para ordenar por nombre en español: sin mayúsculas ni tildes («Ámbito» junto a «ambiente»)."""
	descompuesto = unicodedata.normalize("NFKD", nombre.casefold())
	return "".join(letra for letra in descompuesto if not unicodedata.combining(letra))


# --------------------------------------------------------------------------
# Lectura y creación
# --------------------------------------------------------------------------


def tamano(ruta: Path) -> int:
	"""Tamaño en bytes; ``-1`` si no se puede saber."""
	try:
		return Path(ruta).stat().st_size
	except OSError:
		return -1


def demasiado_grande(ruta: Path, limite: int = LIMITE_VER) -> bool:
	"""Si el archivo supera ``limite`` bytes (no se abre en el visor)."""
	return tamano(ruta) > limite


def leer_texto(ruta: Path) -> str:
	"""Contenido del archivo como texto (UTF-8; los bytes inválidos se sustituyen)."""
	return Path(ruta).read_text(encoding="utf-8-sig", errors="replace")


def plantilla_para(archivo: ArchivoMemoria) -> str:
	"""Texto inicial, breve y en español, de un archivo de instrucciones nuevo."""
	nombre = archivo.ruta.name
	if archivo.agente == CODEX or nombre.upper() == "AGENTS.MD":
		quien = "Codex y otros agentes que leen AGENTS.md" if archivo.ambito == PROYECTO else "Codex"
	elif archivo.agente == ANTIGRAVITY:
		quien = "Gemini y Antigravity"
	else:
		quien = "Claude"
	if nombre.lower() == "claude.local.md":
		titulo = "Mis instrucciones privadas para este proyecto"
		cuando = (
			f"{quien} lee este archivo cada vez que trabaja en este proyecto. Es solo para ti: "
			"no lo compartas con el resto del equipo (añádelo a .gitignore si usas git)."
		)
	elif archivo.ambito == PROYECTO:
		titulo = "Instrucciones de este proyecto"
		cuando = (
			f"{quien} lee este archivo cada vez que trabaja en este proyecto. Explica de qué trata el proyecto, "
			"cómo se prueba y qué normas debe seguir."
		)
	else:
		titulo = f"Mis instrucciones para {quien}"
		cuando = f"{quien} lee este archivo al empezar cada sesión, en todos tus proyectos."
	return (
		f"# {titulo}\n\n"
		f"{cuando} Escribe debajo, en frases sencillas, cómo quieres que trabaje: una instrucción por línea. "
		"Puedes borrar o cambiar todo lo que hay aquí.\n\n"
		"## Instrucciones\n\n"
		"- Respóndeme en español de España.\n"
	)


def crear_desde_plantilla(archivo: ArchivoMemoria) -> Path:
	"""Crea ``archivo`` con :func:`plantilla_para`. Nunca sobrescribe.

	Lanza ``FileExistsError`` si ya existe, ``ValueError`` si no es un archivo
	de instrucciones ``.md`` y ``OSError`` si no se puede escribir.
	"""
	if archivo.tipo != INSTRUCCIONES or archivo.ruta.suffix.lower() != ".md":
		raise ValueError("Solo se pueden crear archivos de instrucciones .md")
	ruta = Path(archivo.ruta)
	ruta.parent.mkdir(parents=True, exist_ok=True)
	# «x»: falla si el archivo apareció entre tanto; nunca se pisa nada.
	with ruta.open("x", encoding="utf-8", newline="\n") as salida:
		salida.write(plantilla_para(archivo))
	log.info("Archivo de instrucciones creado: %s", ruta)
	return ruta
