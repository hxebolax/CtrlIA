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
  si existen, ``AGENTS.override.md``, ``skills/*/SKILL.md``, ``prompts/*.md`` y la
  memoria de ``memories/**/*.md`` (sin ``memories/rollout_summaries``, que es
  historial); las habilidades de ``~/.agents/skills``; en el proyecto ``AGENTS.md``
  y, si existen, ``AGENTS.override.md`` y ``.agents/skills/*/SKILL.md``.
- **Antigravity y Gemini** (``~/.gemini``): ``GEMINI.md`` y
  ``antigravity-cli/settings.json``; en el proyecto ``GEMINI.md``,
  ``AGENTS.md`` y ``.agents/skills/*/SKILL.md``.

Escrituras (todas a petición de la persona, nunca solas):

- :func:`crear_desde_plantilla` crea un archivo de instrucciones NUEVO.
- :func:`guardar_texto` guarda lo editado en «Lo que el agente sabe de ti»:
  copia previa en ``data/backups/agentes``, escritura atómica, sin escribir
  si el archivo cambió desde que se abrió y sin guardar un JSON o TOML roto.
- :func:`anadir_memoria_claude` y :func:`borrar_memoria_claude`: notas de la
  memoria automática de Claude Code de la carpeta (y su línea de ``MEMORY.md``).
- :func:`crear_subagente`: subagentes globales de Claude Code y Codex.

Nada sustituye un archivo que ya exista sin que la persona lo pida (los nuevos
se crean con ``open(..., "x")``).

Sin wx: todo este módulo se prueba por sí solo.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
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


def _memorias_codex(raiz: Path) -> list[ArchivoMemoria]:
	"""Lo que Codex recuerda de ti, en ``memories``, el índice ``MEMORY.md`` primero.

	``memories/rollout_summaries`` guarda un resumen de cada conversación pasada: eso es
	historial, no memoria que quieras revisar, y ni se recorre (puede haber miles).
	"""
	carpeta = raiz / "memories"
	notas: list[Path] = []
	for actual, subcarpetas, nombres in os.walk(carpeta):
		subcarpetas[:] = [nombre for nombre in subcarpetas if nombre != "rollout_summaries"]
		notas += [Path(actual) / nombre for nombre in nombres if nombre.lower().endswith(".md")]
	relativas = sorted(((ruta.relative_to(carpeta), ruta) for ruta in notas if _es_archivo(ruta)),
		key=lambda par: (par[0].as_posix().upper() != "MEMORY.MD", par[0].as_posix().lower()))
	archivos = []
	for relativa, ruta in relativas:
		nombre = "Memoria de Codex"
		if relativa.as_posix().upper() != "MEMORY.MD":
			nombre += f": {relativa.with_suffix('').as_posix()}"
		archivos.append(_encontrado(nombre, ruta, GLOBAL, CODEX, MEMORIA))
	return archivos


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
	archivos += _memorias_codex(raiz)
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


# --------------------------------------------------------------------------
# Edición (1.6.0): guardar lo editado sin perder nada
# --------------------------------------------------------------------------

#: Copias de seguridad que se conservan de cada archivo editado.
MAX_COPIAS = 20


class ErrorMemoria(Exception):
	"""Fallo con un mensaje en español para la persona."""


class ArchivoCambiado(ErrorMemoria):
	"""El archivo cambió en el disco desde que se abrió para editarlo."""


@dataclass(slots=True)
class TextoEditable:
	"""Lo que se carga para editar un archivo: su texto y cómo estaba en el disco."""

	texto: str
	#: ``st_mtime_ns`` y tamaño al leerlo (``None`` si no existía): así se sabe si otro lo cambió.
	firma: tuple[int, int] | None
	#: Fin de línea del archivo («\r\n» o «\n») y si llevaba BOM: se conservan al guardar.
	fin_linea: str = "\n"
	bom: bool = False


def _firma(ruta: Path) -> tuple[int, int] | None:
	try:
		estado = Path(ruta).stat()
	except OSError:
		return None
	return estado.st_mtime_ns, estado.st_size


def es_editable(archivo: ArchivoMemoria) -> bool:
	"""Si se puede editar en Ctrl+IA: un archivo de texto que existe y no pasa de :data:`LIMITE_VER`."""
	return archivo.existe and archivo.ruta.suffix.lower() in (".md", ".json", ".toml", ".txt") and not demasiado_grande(archivo.ruta)


def cargar_para_editar(ruta: Path) -> TextoEditable:
	"""Lee ``ruta`` para editarla (sus saltos de línea pasan a «\\n», como en un cuadro de texto).

	Lanza ``OSError`` si no se puede leer y :class:`ErrorMemoria` si es demasiado grande o no es UTF-8.
	"""
	ruta = Path(ruta)
	if demasiado_grande(ruta):
		raise ErrorMemoria("El archivo ocupa más de 1 MB: ábrelo con el editor.")
	firma = _firma(ruta)
	datos = ruta.read_bytes()
	bom = datos.startswith(b"\xef\xbb\xbf")
	try:
		texto = datos.decode("utf-8-sig")
	except UnicodeDecodeError as error:
		# Con bytes que no son UTF-8, guardar lo editado los cambiaría: no se edita aquí.
		raise ErrorMemoria("El archivo no está en UTF-8: ábrelo con el editor para no estropearlo.") from error
	fin = "\r\n" if texto.count("\r\n") > texto.count("\n") - texto.count("\r\n") else "\n"
	return TextoEditable(texto.replace("\r\n", "\n"), firma, fin, bom)


def copia_de_seguridad(ruta: Path, carpeta: Path) -> Path | None:
	"""Copia ``ruta`` a ``carpeta`` como ``<nombre>-<fecha><extensión>`` (las más viejas se borran). ``None`` si no existe.

	Lanza :class:`ErrorMemoria` si no se puede copiar: entonces no se toca el original.
	"""
	ruta = Path(ruta)
	if not ruta.is_file():
		return None
	try:
		carpeta.mkdir(parents=True, exist_ok=True)
		marca = time.strftime("%Y%m%d_%H%M%S")
		base = f"{ruta.parent.name}-{ruta.stem}" if ruta.stem.upper() in ("MEMORY", "CLAUDE", "AGENTS", "GEMINI", "SKILL", "SETTINGS") else ruta.stem
		destino = carpeta / f"{base}-{marca}{ruta.suffix}"
		numero = 1
		while destino.exists():
			numero += 1
			destino = carpeta / f"{base}-{marca}-{numero}{ruta.suffix}"
		destino.write_bytes(ruta.read_bytes())
		viejas = sorted(carpeta.glob(f"{base}-*{ruta.suffix}"))
		for vieja in viejas[:-MAX_COPIAS]:
			vieja.unlink(missing_ok=True)
		return destino
	except OSError as error:
		raise ErrorMemoria(f"No se pudo hacer la copia de seguridad: {error.strerror or error}") from error


def _comprobar_formato(ruta: Path, texto: str) -> None:
	"""Un JSON o un TOML que no se entiende no se guarda: el agente dejaría de leerlo."""
	sufijo = ruta.suffix.lower()
	try:
		if sufijo == ".json" and texto.strip():
			json.loads(texto)
		elif sufijo == ".toml":
			import tomllib

			tomllib.loads(texto)
	except ValueError as error:
		raise ErrorMemoria(f"El texto no es {sufijo[1:].upper()} válido y no se ha guardado: {error}") from error


def escribir_atomico(ruta: Path, contenido: bytes) -> None:
	"""Escribe ``contenido`` en un temporal de la misma carpeta y lo pone en su sitio con ``os.replace``."""
	ruta = Path(ruta)
	ruta.parent.mkdir(parents=True, exist_ok=True)
	descriptor, temporal = tempfile.mkstemp(prefix=".ctrlia-", suffix=".tmp", dir=str(ruta.parent))
	try:
		with os.fdopen(descriptor, "wb") as salida:
			salida.write(contenido)
			salida.flush()
			os.fsync(salida.fileno())
		os.replace(temporal, ruta)
	except OSError:
		try:
			os.unlink(temporal)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
		raise


def guardar_texto(ruta: Path, texto: str, carpeta_copias: Path, cargado: TextoEditable | None = None,
		forzar: bool = False) -> Path | None:
	"""Guarda lo editado en ``ruta``: copia previa en ``carpeta_copias`` y escritura atómica. Devuelve la copia.

	Con ``cargado`` (lo que se leyó al abrir), si el archivo cambió en el disco desde entonces se lanza
	:class:`ArchivoCambiado` y no se escribe nada, salvo con ``forzar``. Se conservan el fin de línea y el
	BOM que tenía. Lanza :class:`ErrorMemoria` si no se puede.
	"""
	ruta = Path(ruta)
	if cargado is not None and not forzar and _firma(ruta) != cargado.firma:
		raise ArchivoCambiado("El archivo ha cambiado desde que lo abriste (quizá lo ha escrito el agente).")
	_comprobar_formato(ruta, texto)
	copia = copia_de_seguridad(ruta, carpeta_copias)
	fin = cargado.fin_linea if cargado is not None else "\n"
	normalizado = texto.replace("\r\n", "\n")
	contenido = (b"\xef\xbb\xbf" if cargado is not None and cargado.bom else b"") + normalizado.replace("\n", fin).encode("utf-8")
	try:
		escribir_atomico(ruta, contenido)
	except OSError as error:
		raise ErrorMemoria(f"No se pudo guardar {ruta}: {error.strerror or error}") from error
	log.info("Archivo de los agentes guardado: %s (copia: %s)", ruta, copia)
	return copia


# --------------------------------------------------------------------------
# Memoria automática de Claude Code: añadir y borrar
# --------------------------------------------------------------------------

#: Tipos de memoria de Claude Code (``metadata.type``) con su nombre en español.
TIPOS_MEMORIA = {
	"user": "Sobre ti",
	"feedback": "Cómo quieres que trabaje",
	"project": "Sobre el proyecto",
	"reference": "Dónde encontrar información",
}

#: Nombres de Windows que no pueden ser un archivo.
_RESERVADOS_WINDOWS = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})


def nombre_seguro(texto: str) -> str:
	"""«Mis gustos de café» → «mis-gustos-de-cafe»: minúsculas ASCII, dígitos y guiones."""
	descompuesto = unicodedata.normalize("NFKD", (texto or "").casefold())
	sin_tildes = "".join(letra for letra in descompuesto if not unicodedata.combining(letra))
	return re.sub(r"[^a-z0-9]+", "-", sin_tildes).strip("-")[:60].strip("-")


def _una_linea(texto: str) -> str:
	return " ".join((texto or "").split())


def es_memoria_claude(ruta: Path) -> bool:
	"""Si ``ruta`` es una nota de la memoria automática de Claude Code (``projects/<carpeta>/memory/*.md``)."""
	ruta = Path(ruta)
	try:
		proyectos = (raiz_claude() / "projects").resolve()
		padre = ruta.parent.resolve()
	except OSError:
		return False
	return (ruta.suffix.lower() == ".md" and padre.name == "memory" and padre.parent.parent == proyectos)


def anadir_memoria_claude(carpeta_proyecto: Path, titulo: str, descripcion: str, tipo: str, texto: str) -> Path:
	"""Añade una nota a la memoria de Claude Code de ``carpeta_proyecto`` y la apunta en su ``MEMORY.md``.

	Como lo hace el propio Claude Code: ``<nombre>.md`` con cabecera ``name``, ``description`` y
	``metadata.type``, y una línea ``- [título](nombre.md) — descripción`` en el índice. Nunca sustituye una
	nota que ya exista (``FileExistsError``). ``ValueError`` si falta algo.
	"""
	nombre = nombre_seguro(titulo)
	descripcion = _una_linea(descripcion)
	texto = (texto or "").strip()
	if not nombre:
		raise ValueError("Escribe un nombre para la memoria (con alguna letra o número).")
	if not descripcion or not texto:
		raise ValueError("La memoria necesita una descripción y un texto.")
	if tipo not in TIPOS_MEMORIA:
		raise ValueError("Tipo de memoria desconocido.")
	if nombre == "memory":
		raise ValueError("Ese nombre lo usa el índice de la memoria: elige otro.")
	carpeta = carpeta_memoria_claude(Path(carpeta_proyecto))
	carpeta.mkdir(parents=True, exist_ok=True)
	ruta = carpeta / f"{nombre}.md"
	marca = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
	contenido = (
		f"---\nname: {nombre}\ndescription: {json.dumps(descripcion, ensure_ascii=False)}\n"
		f"metadata:\n  node_type: memory\n  type: {tipo}\n  modified: {marca}\n---\n\n{texto}\n"
	)
	with ruta.open("x", encoding="utf-8", newline="\n") as salida:
		salida.write(contenido)
	indice = carpeta / "MEMORY.md"
	linea = f"- [{_una_linea(titulo).replace(']', ')')}]({nombre}.md) — {descripcion}\n"
	try:
		anterior = indice.read_bytes().decode("utf-8-sig") if indice.is_file() else ""
		nuevo = (anterior.rstrip("\r\n") + "\n" if anterior.strip() else "") + linea
		escribir_atomico(indice, nuevo.encode("utf-8"))
	except (OSError, UnicodeDecodeError):
		log.warning("No se pudo apuntar la memoria %s en MEMORY.md", nombre, exc_info=True)
		raise ErrorMemoria("La memoria se ha creado, pero no se pudo apuntar en MEMORY.md.") from None
	log.info("Memoria de Claude Code añadida: %s", ruta)
	return ruta


def borrar_memoria_claude(ruta: Path, carpeta_copias: Path) -> Path | None:
	"""Borra una nota de la memoria de Claude Code y quita su línea de ``MEMORY.md``. Devuelve la copia hecha antes.

	Solo notas de la memoria automática (nunca el propio ``MEMORY.md``): ``ValueError`` si no lo es. Antes de
	borrar, copia la nota (y el índice) a ``carpeta_copias``.
	"""
	ruta = Path(ruta)
	if not es_memoria_claude(ruta) or ruta.name.upper() == "MEMORY.MD":
		raise ValueError("Solo se pueden borrar desde aquí las notas de la memoria automática de Claude Code.")
	copia = copia_de_seguridad(ruta, carpeta_copias)
	indice = ruta.parent / "MEMORY.md"
	try:
		ruta.unlink()
	except FileNotFoundError:
		log.debug("La memoria %s ya no estaba", ruta)
	except OSError as error:
		raise ErrorMemoria(f"No se pudo borrar {ruta.name}: {error.strerror or error}") from error
	if indice.is_file():
		try:
			texto = indice.read_bytes().decode("utf-8-sig")
		except (OSError, UnicodeDecodeError):
			log.warning("No se pudo leer %s para quitar la nota", indice, exc_info=True)
			return copia
		lineas = texto.splitlines(keepends=True)
		quedan = [linea for linea in lineas if f"({ruta.name})" not in linea and f"(./{ruta.name})" not in linea]
		if quedan != lineas:
			copia_de_seguridad(indice, carpeta_copias)
			try:
				escribir_atomico(indice, "".join(quedan).encode("utf-8"))
			except OSError as error:
				raise ErrorMemoria(f"La nota se ha borrado, pero no se pudo quitar de MEMORY.md: {error.strerror or error}") from error
	log.info("Memoria de Claude Code borrada: %s", ruta)
	return copia


# --------------------------------------------------------------------------
# Subagentes personalizados de Claude Code y de Codex
# --------------------------------------------------------------------------

_RE_NOMBRE_AGENTE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def comprobar_nombre_subagente(nombre: str) -> str:
	"""El nombre si vale (minúsculas, dígitos y guiones, sin nombres reservados de Windows); si no, ``ValueError``."""
	nombre = (nombre or "").strip()
	if not _RE_NOMBRE_AGENTE.match(nombre) or nombre in _RESERVADOS_WINDOWS:
		raise ValueError("El nombre solo puede tener letras minúsculas sin tildes, números y guiones, y empezar por "
			"una letra o un número (por ejemplo, revisor-de-codigo).")
	return nombre


def ruta_subagente(agente: str, nombre: str) -> Path:
	"""Dónde se crea el subagente: ``~/.claude/agents/<nombre>.md`` o ``~/.codex/agents/<nombre>.toml``."""
	if agente == CLAUDE:
		return raiz_claude() / "agents" / f"{nombre}.md"
	if agente == CODEX:
		return raiz_codex() / "agents" / f"{nombre}.toml"
	raise ValueError("Solo se pueden crear subagentes de Claude Code y de Codex.")


def crear_subagente(agente: str, nombre: str, descripcion: str, instrucciones: str, herramientas: str = "") -> Path:
	"""Crea un subagente personalizado global. Nunca sustituye uno que exista (``FileExistsError``).

	- Claude Code: ``~/.claude/agents/<nombre>.md`` con cabecera ``name``, ``description`` y, si se dan,
	  ``tools`` (separadas por comas), y las instrucciones debajo.
	- Codex: ``~/.codex/agents/<nombre>.toml`` con ``name``, ``description`` y ``developer_instructions``
	  (obligatoria: sin ella Codex lo descarta con un aviso).
	"""
	nombre = comprobar_nombre_subagente(nombre)
	descripcion = _una_linea(descripcion)
	instrucciones = (instrucciones or "").strip()
	if not descripcion or not instrucciones:
		raise ValueError("El subagente necesita una descripción y unas instrucciones.")
	ruta = ruta_subagente(agente, nombre)
	if agente == CLAUDE:
		lista = ", ".join(h for h in (parte.strip() for parte in (herramientas or "").replace("\n", ",").split(",")) if h)
		if any(caracter in lista for caracter in "\"'#:{}[]"):
			raise ValueError("Las herramientas se escriben separadas por comas, como Read, Grep, Bash.")
		cabecera = f"---\nname: {nombre}\ndescription: {json.dumps(descripcion, ensure_ascii=False)}\n"
		if lista:
			cabecera += f"tools: {lista}\n"
		contenido = cabecera + f"---\n\n{instrucciones}\n"
	else:
		# Cadenas de JSON: son también cadenas básicas válidas de TOML.
		contenido = (
			f"name = {json.dumps(nombre)}\n"
			f"description = {json.dumps(descripcion, ensure_ascii=False)}\n"
			f"developer_instructions = {json.dumps(instrucciones, ensure_ascii=False)}\n"
		)
	ruta.parent.mkdir(parents=True, exist_ok=True)
	with ruta.open("x", encoding="utf-8", newline="\n") as salida:
		salida.write(contenido)
	log.info("Subagente creado: %s", ruta)
	return ruta
