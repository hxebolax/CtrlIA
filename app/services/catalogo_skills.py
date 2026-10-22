"""Catálogo de skills: las habilidades de la persona, cada una con su SKILL.md.

Una skill es una carpeta con un ``SKILL.md`` (o ``skill.md``) que empieza
con un bloque YAML entre dos líneas ``---`` (*frontmatter*) con, al menos,
``name`` y ``description``; a veces ``version`` y un mapa ``metadata`` con
``version`` o ``category``. Junto a él puede haber plantillas, guiones o
referencias.

Qué hace este módulo (sin wx, para probarlo sin ventanas):

- ``leer_frontmatter`` / ``parsear_yaml_simple``: lector tolerante de YAML
  sencillo (claves, textos entre comillas, bloques ``|`` y ``>``, mapas
  anidados y listas). El proyecto no depende de PyYAML y no hace falta: un
  frontmatter roto nunca impide añadir la skill (se usa el nombre de la
  carpeta y el primer párrafo).
- ``leer_skill``: la ficha de una skill (nombre, descripción, versión,
  categoría) a partir de su carpeta o de su SKILL.md.
- ``buscar_skills``: recorre una carpeta (con límite de profundidad y de
  entradas, sin seguir enlaces ni uniones) y devuelve las carpetas que tienen
  SKILL.md. Una carpeta de skill no se recorre por dentro.
- ``CatalogoSkills``: importar (sin duplicar por carpeta), listar con filtro,
  editar, eliminar (y borrar la carpeta solo de las creadas aquí), crear una
  nueva, refrescar (qué cambió en los SKILL.md y qué carpetas ya no existen),
  exportar e importar el catálogo en JSON y ``datos_para_sesion``.

Los datos viven en la tabla ``skills`` de la base de datos (``Almacen``).
"""

from __future__ import annotations

import builtins
import json
import logging
import os
import re
import shutil
import time
import unicodedata
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.almacen import Almacen, SkillCatalogo

log = logging.getLogger(__name__)

#: Nombres del archivo de instrucciones de una skill (el primero es el que se crea).
NOMBRES_SKILL = ("SKILL.md", "skill.md")
#: Un SKILL.md mayor que esto no se lee (no es un SKILL.md normal).
MAX_TAMANO_SKILL = 1024 * 1024
#: Límites de la búsqueda en una carpeta.
PROFUNDIDAD_MAXIMA = 8
MAX_ENTRADAS = 20000
MAX_SKILLS = 1000
#: Carpetas que nunca se recorren al buscar (sin distinguir mayúsculas).
CARPETAS_IGNORADAS = frozenset({
	".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
	".pytest_cache", ".ruff_cache", "site-packages", "$recycle.bin", "system volume information",
	"windows", "program files", "program files (x86)", "programdata",
})
#: Carpetas padre cuyo nombre no sirve como categoría.
CARPETAS_GENERICAS = frozenset({
	"agentes", "agents", "agent", "agente", "skills", "skill", "habilidades", "habilidad", ".agents", ".agent",
	".claude", ".codex", ".gemini", ".github", "plugins", "plugin", "plantillas", "templates", "compartido",
	"shared", "src", "docs", "desktop", "escritorio", "documents", "documentos", "downloads", "descargas", "data",
	"users", "usuarios",
})
#: Estados de una skill del catálogo.
ESTADO_DISPONIBLE = "disponible"
ESTADO_NO_ENCONTRADO = "no_encontrada"
#: Origen de una skill.
ORIGEN_IMPORTADO = "importado"
ORIGEN_CREADO = "creado"
#: Formato del archivo JSON de exportación del catálogo.
FORMATO_JSON = "ctrlia-catalogo-skills"
VERSION_JSON = 2
#: Una skill creada aquí no se borra si su carpeta tiene más entradas que esto (no parece una skill).
MAX_ENTRADAS_BORRAR = 500
#: Longitud máxima del nombre de la carpeta de una skill nueva.
MAX_NOMBRE_CARPETA = 60
_RESERVADOS_WINDOWS = frozenset(
	{"con", "prn", "aux", "nul"} | {f"com{n}" for n in range(1, 10)} | {f"lpt{n}" for n in range(1, 10)}
)


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto (este módulo no usa wx)."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def _motivo(error: OSError) -> str:
	return error.strerror or str(error)


#: Categoría de las skills de ejemplo que trae Ctrl+IA.
CATEGORIA_EJEMPLOS = "Ejemplos de Ctrl+IA"
#: Lo que se añade al nombre de cada skill de ejemplo, para que se vea que lo es.
SUFIJO_EJEMPLO = " (ejemplo)"
#: Nota de cada skill de ejemplo en el catálogo.
NOTA_EJEMPLO = ("Skill de ejemplo que trae Ctrl+IA, para ver cómo se usan, se editan y se quitan las skills. Puedes "
	"usarla en tus sesiones, cambiarla o quitarla; «Restaurar las skills de ejemplo» la recupera. Si quieres "
	"cambiar su SKILL.md, copia antes su carpeta a otro sitio: una actualización de Ctrl+IA puede sustituirla.")


class ErrorCatalogo(Exception):
	"""Un problema que se explica a la persona (mensaje en español, listo para mostrar)."""


# ════════════════════════════════════════════════════════════════════════════
# YAML sencillo (frontmatter)
# ════════════════════════════════════════════════════════════════════════════

_CLAVE = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s#'"\-?:][^:#]*?|-[^\s:#][^:#]*?)\s*:(?:\s+(.*))?$""")
_INDICADOR_BLOQUE = re.compile(r"^([|>])([+-]?)(\d?)([+-]?)\s*(#.*)?$")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "0": "\0", " ": " ", "e": "\x1b"}


def _sangria(linea: str) -> int:
	return len(linea) - len(linea.lstrip(" "))


def _vacia(linea: str) -> bool:
	limpia = linea.strip()
	return not limpia or limpia.startswith("#")


def _sin_comentario(texto: str) -> str:
	"""Quita un comentario ``  # ...`` de un valor sin comillas."""
	posicion = texto.find(" #")
	return (texto[:posicion] if posicion >= 0 else texto).strip()


def _comillas_dobles(texto: str) -> str:
	def sustituir(coincidencia: re.Match[str]) -> str:
		secuencia = coincidencia.group(1)
		if secuencia[0] == "u" and len(secuencia) == 5:
			try:
				return chr(int(secuencia[1:], 16))
			except ValueError:
				return secuencia
		return _ESCAPES.get(secuencia, secuencia)

	return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", sustituir, texto)


def _escalar(texto: str) -> Any:
	"""Valor de una línea: texto entre comillas, lista en línea o texto simple (``null`` = ``""``)."""
	texto = texto.strip()
	if not texto:
		return ""
	if texto[0] == '"':
		fin = re.match(r'"((?:[^"\\]|\\.)*)"', texto)
		return _comillas_dobles(fin.group(1)) if fin else _comillas_dobles(texto[1:])
	if texto[0] == "'":
		fin = re.match(r"'((?:[^']|'')*)'", texto)
		return (fin.group(1) if fin else texto[1:]).replace("''", "'")
	texto = _sin_comentario(texto)
	if texto.startswith("[") and texto.endswith("]"):
		interior = texto[1:-1].strip()
		return [_escalar(parte) for parte in interior.split(",")] if interior else []
	if texto in ("~", "null", "Null", "NULL"):
		return ""
	return texto


def _comilla_abierta(texto: str) -> bool:
	"""El valor empieza con comillas que no se cierran en esta línea."""
	texto = texto.strip()
	if texto[:1] == '"':
		return re.match(r'"(?:[^"\\]|\\.)*"', texto) is None
	if texto[:1] == "'":
		return re.match(r"'(?:[^']|'')*'", texto) is None
	return False


def _bloque(lineas: list[str], indice: int, sangria_padre: int, indicador: str) -> tuple[str, int]:
	"""Bloque ``|`` (literal) o ``>`` (plegado) que sigue a una clave."""
	partes = _INDICADOR_BLOQUE.match(indicador)
	estilo = partes.group(1) if partes else "|"
	recorte = (partes.group(2) or partes.group(4)) if partes else ""
	recogidas: list[str] = []
	sangria_bloque = 0
	while indice < len(lineas):
		linea = lineas[indice]
		if linea.strip():
			if _sangria(linea) <= sangria_padre:
				break
			if not sangria_bloque:
				sangria_bloque = _sangria(linea)
			recogidas.append(linea[min(sangria_bloque, _sangria(linea)):])
		else:
			recogidas.append("")
		indice += 1
	if estilo == "|":
		texto = "\n".join(recogidas)
	else:
		trozos: list[str] = []
		for linea in recogidas:
			if not linea:
				trozos.append("\n")
			elif trozos and trozos[-1] not in ("\n",) and not linea.startswith(" "):
				trozos.append(" " + linea)
			else:
				trozos.append(linea)
		texto = "".join(trozos)
	if recorte == "-":
		texto = texto.rstrip("\n")
	elif recorte != "+":
		texto = texto.rstrip("\n") + ("\n" if texto.strip() else "")
	return texto, indice


def _continuar(lineas: list[str], indice: int, sangria_clave: int, inicio: str) -> tuple[str, int]:
	"""Valor que sigue en las líneas siguientes (texto simple largo o comillas sin cerrar)."""
	trozos = [inicio.strip()]
	abierta = _comilla_abierta(inicio)
	while indice < len(lineas):
		linea = lineas[indice]
		if not abierta:
			if not linea.strip():
				# Una línea en blanco dentro de un texto simple solo sigue si después hay más texto sangrado.
				siguiente = indice + 1
				while siguiente < len(lineas) and not lineas[siguiente].strip():
					siguiente += 1
				if siguiente < len(lineas) and _sangria(lineas[siguiente]) > sangria_clave and not lineas[siguiente].strip().startswith("#"):
					trozos.append("\n")
					indice = siguiente
					continue
				break
			if _sangria(linea) <= sangria_clave or linea.strip().startswith("#"):
				break
		trozos.append(linea.strip())
		indice += 1
		if abierta and not _comilla_abierta(" ".join(t for t in trozos if t != "\n")):
			break
	texto = ""
	for trozo in trozos:
		if trozo == "\n":
			texto = texto.rstrip(" ") + "\n"
		elif texto and not texto.endswith("\n"):
			texto += " " + trozo
		else:
			texto += trozo
	return texto, indice


def _clave(contenido: str) -> tuple[str, str] | None:
	coincidencia = _CLAVE.match(contenido)
	if not coincidencia:
		return None
	clave = coincidencia.group(1).strip()
	if clave[:1] in ('"', "'"):
		clave = str(_escalar(clave))
	return clave, (coincidencia.group(2) or "")


def _siguiente_util(lineas: list[str], indice: int) -> int:
	while indice < len(lineas) and _vacia(lineas[indice]):
		indice += 1
	return indice


def _es_elemento_lista(contenido: str) -> bool:
	return contenido == "-" or contenido.startswith("- ")


def _mapa(lineas: list[str], indice: int, sangria: int) -> tuple[dict[str, Any], int]:
	resultado: dict[str, Any] = {}
	while indice < len(lineas):
		linea = lineas[indice]
		if _vacia(linea):
			indice += 1
			continue
		propia = _sangria(linea)
		if propia < sangria:
			break
		contenido = linea.strip()
		if propia > sangria or _es_elemento_lista(contenido):
			if _es_elemento_lista(contenido) and propia <= sangria:
				break
			indice += 1  # línea que no encaja: se ignora (tolerante)
			continue
		par = _clave(contenido)
		indice += 1
		if par is None:
			continue
		clave, resto = par
		resto_limpio = resto.strip()
		if _INDICADOR_BLOQUE.match(resto_limpio):
			valor, indice = _bloque(lineas, indice, propia, resto_limpio)
		elif not resto_limpio or resto_limpio.startswith("#"):
			siguiente = _siguiente_util(lineas, indice)
			if siguiente < len(lineas) and _es_elemento_lista(lineas[siguiente].strip()) and _sangria(lineas[siguiente]) >= propia:
				valor, indice = _lista(lineas, siguiente, _sangria(lineas[siguiente]))
			elif siguiente < len(lineas) and _sangria(lineas[siguiente]) > propia:
				if _clave(lineas[siguiente].strip()) is not None:
					valor, indice = _mapa(lineas, siguiente, _sangria(lineas[siguiente]))
				else:
					texto, indice = _continuar(lineas, siguiente, propia, "")
					valor = texto.strip()
			else:
				valor = ""
		else:
			texto, indice = _continuar(lineas, indice, propia, resto_limpio)
			valor = _escalar(texto)
		resultado[clave] = valor
	return resultado, indice


def _lista(lineas: list[str], indice: int, sangria: int) -> tuple[list[Any], int]:
	resultado: list[Any] = []
	lineas = list(lineas)
	while indice < len(lineas):
		linea = lineas[indice]
		if _vacia(linea):
			indice += 1
			continue
		contenido = linea.strip()
		if _sangria(linea) != sangria or not _es_elemento_lista(contenido):
			break
		elemento = contenido[1:].lstrip()
		columna = sangria + (len(contenido) - len(elemento))
		if elemento and _clave(elemento) is not None and elemento[:1] not in "\"'[":
			lineas[indice] = " " * columna + elemento
			valor, indice = _mapa(lineas, indice, columna)
			resultado.append(valor)
			continue
		indice += 1
		if not elemento:
			siguiente = _siguiente_util(lineas, indice)
			if siguiente < len(lineas) and _sangria(lineas[siguiente]) > sangria:
				valor, indice = _mapa(lineas, siguiente, _sangria(lineas[siguiente]))
				resultado.append(valor)
			else:
				resultado.append("")
			continue
		texto, indice = _continuar(lineas, indice, sangria, elemento)
		resultado.append(_escalar(texto))
	return resultado, indice


def parsear_yaml_simple(texto: str) -> dict[str, Any]:
	"""Lee un YAML sencillo (el de un frontmatter) a un diccionario. Nunca lanza: lo que no entiende lo salta.

	Admite claves con valor simple o entre comillas (con escapes), comentarios,
	bloques ``|`` y ``>`` (con ``-`` y ``+``), textos de varias líneas, mapas
	anidados por sangría, listas con guiones y listas en línea ``[a, b]``. Los
	valores son siempre textos (``true`` o ``1.0`` se quedan como texto).
	"""
	lineas = texto.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "  ").split("\n")
	try:
		inicio = _siguiente_util(lineas, 0)
		if inicio >= len(lineas):
			return {}
		resultado, _indice = _mapa(lineas, inicio, _sangria(lineas[inicio]))
		return resultado
	except (RecursionError, ValueError, IndexError):
		log.warning("No se pudo interpretar el frontmatter", exc_info=True)
		return {}


def separar_frontmatter(texto: str) -> tuple[str | None, str]:
	"""Devuelve ``(yaml, cuerpo)``; ``yaml`` es ``None`` si el texto no empieza con ``---``."""
	texto = texto.lstrip("﻿")
	lineas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
	primera = 0
	while primera < len(lineas) and not lineas[primera].strip():
		primera += 1
	if primera >= len(lineas) or lineas[primera].strip() != "---":
		return None, texto
	for indice in range(primera + 1, len(lineas)):
		if lineas[indice].strip() in ("---", "..."):
			return "\n".join(lineas[primera + 1:indice]), "\n".join(lineas[indice + 1:])
	# Sin cierre: no es un frontmatter válido; se trata todo como cuerpo.
	return None, texto


def leer_frontmatter(texto: str) -> dict[str, Any]:
	"""El frontmatter de un SKILL.md como diccionario (vacío si no tiene)."""
	yaml, _cuerpo = separar_frontmatter(texto)
	return parsear_yaml_simple(yaml) if yaml is not None else {}


# ════════════════════════════════════════════════════════════════════════════
# Lectura de una skill
# ════════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class FichaSkill:
	"""Lo que dice el SKILL.md de una skill."""

	nombre: str
	descripcion: str
	carpeta: Path
	archivo: Path
	version: str = ""
	categoria: str = ""
	#: Frontmatter completo (por si se necesita algún otro campo).
	datos: dict[str, Any] = field(default_factory=dict)
	#: Problema leve al leerlo (por ejemplo, sin frontmatter): se puede añadir igualmente.
	aviso: str = ""


def una_linea(texto: Any) -> str:
	"""Texto en una sola línea, sin espacios repetidos (las listas se unen con comas)."""
	if isinstance(texto, list):
		texto = ", ".join(str(t) for t in texto if t)
	elif isinstance(texto, dict):
		return ""
	return " ".join(str(texto or "").split())


def ruta_skill(carpeta: str | Path) -> Path | None:
	"""El SKILL.md (o skill.md) de ``carpeta`` si existe; ``None`` si no."""
	carpeta = Path(carpeta)
	for nombre in NOMBRES_SKILL:
		candidato = carpeta / nombre
		try:
			if candidato.is_file():
				return candidato
		except OSError:
			log.debug("No se pudo mirar %s", candidato, exc_info=True)
			return None
	return None


def _primer_parrafo(cuerpo: str, maximo: int = 400) -> str:
	"""Primer párrafo que no es un título (descripción de respaldo de un SKILL.md sin frontmatter)."""
	parrafo: list[str] = []
	for linea in cuerpo.splitlines():
		limpia = linea.strip()
		if not limpia:
			if parrafo:
				break
			continue
		if limpia.startswith(("#", "```", "|", "---", "<")):
			if parrafo:
				break
			continue
		parrafo.append(limpia)
	texto = " ".join(parrafo)
	return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def _campo(datos: dict[str, Any], *claves: str) -> Any:
	"""Primer valor no vacío de ``claves`` (``"metadata.version"`` mira dentro de ``metadata``)."""
	for clave in claves:
		valor: Any = datos
		for parte in clave.split("."):
			valor = valor.get(parte) if isinstance(valor, dict) else None
		if valor not in (None, "", [], {}):
			return valor
	return ""


def leer_skill(ruta: str | Path) -> FichaSkill:
	"""Lee la carpeta de una skill o su SKILL.md. Lanza ``ErrorCatalogo`` si no hay o no se puede leer."""
	ruta = Path(ruta)
	if ruta.is_dir():
		archivo = ruta_skill(ruta)
		if archivo is None:
			raise ErrorCatalogo(_("La carpeta «{ruta}» no tiene ningún archivo SKILL.md.").format(ruta=ruta))
	elif ruta.is_file():
		archivo = ruta
	else:
		raise ErrorCatalogo(_("No existe «{ruta}».").format(ruta=ruta))
	carpeta = archivo.parent
	try:
		tamano = archivo.stat().st_size
		if tamano > MAX_TAMANO_SKILL:
			raise ErrorCatalogo(_("«{archivo}» ocupa más de 1 MB: no parece un SKILL.md.").format(archivo=archivo))
		texto = archivo.read_text(encoding="utf-8-sig", errors="replace")
	except OSError as error:
		raise ErrorCatalogo(_("No se pudo leer «{archivo}»: {error}").format(archivo=archivo, error=_motivo(error))) from error
	yaml, cuerpo = separar_frontmatter(texto)
	datos = parsear_yaml_simple(yaml) if yaml is not None else {}
	aviso = "" if yaml is not None else _("El SKILL.md no empieza con un bloque --- (frontmatter).")
	nombre = una_linea(_campo(datos, "name", "nombre", "title", "titulo")) or carpeta.name
	descripcion = una_linea(_campo(datos, "description", "descripcion", "descripción"))
	if not descripcion:
		descripcion = _primer_parrafo(cuerpo)
		if yaml is not None:
			aviso = _("El SKILL.md no tiene descripción (description).")
	version = una_linea(_campo(datos, "version", "metadata.version"))
	categoria = una_linea(_campo(
		datos, "metadata.category", "metadata.categoria", "metadata.categoría", "category", "categoria", "categoría",
	))
	return FichaSkill(nombre, descripcion, carpeta, archivo, version, categoria, datos, aviso)


def es_plantilla(ficha: FichaSkill) -> bool:
	"""El SKILL.md es una plantilla para crear skills (``{{NOMBRE}}``), no una skill."""
	return "{{" in ficha.nombre or ficha.descripcion.strip().startswith("{{")


def categoria_propuesta(ficha: FichaSkill, raiz: str | Path | None = None) -> str:
	"""Categoría que se propone al añadir una skill.

	La de su ``metadata`` si tiene; si no, el nombre de la carpeta que lo
	contiene cuando la persona los ha ordenado en subcarpetas (con ``raiz``,
	solo si esa carpeta está dentro de la buscada) y no es un nombre genérico
	(«skills», «agentes», «Escritorio»...); si no, vacía.
	"""
	if ficha.categoria:
		return ficha.categoria
	padre = ficha.carpeta.parent
	if padre == padre.parent:
		return ""
	if raiz is not None:
		raiz = Path(raiz)
		if padre == raiz:
			return ""
		try:
			padre.relative_to(raiz)
		except ValueError:
			return ""
	nombre = padre.name.strip()
	if not nombre or nombre.casefold() in CARPETAS_GENERICAS or nombre.startswith("."):
		return ""
	return nombre


# ════════════════════════════════════════════════════════════════════════════
# Búsqueda en una carpeta
# ════════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class ResultadoBusqueda:
	encontrados: list[FichaSkill] = field(default_factory=list)
	#: Carpetas que no se pudieron leer o SKILL.md con problemas (texto para la persona).
	errores: list[str] = field(default_factory=list)
	#: Se paró antes de terminar por algún límite (entradas, skills o cancelación).
	truncado: bool = False
	#: Entradas (archivos y carpetas) revisadas.
	revisadas: int = 0
	#: Plantillas de skill (``{{NOMBRE}}``) que se han saltado.
	plantillas: int = 0


def _es_enlace(entrada: os.DirEntry) -> bool:
	try:
		if entrada.is_symlink():
			return True
		es_union = getattr(entrada, "is_junction", None)
		return bool(es_union()) if callable(es_union) else False
	except OSError:
		return True


def buscar_skills(
	raiz: str | Path,
	profundidad: int = PROFUNDIDAD_MAXIMA,
	max_entradas: int = MAX_ENTRADAS,
	max_skills: int = MAX_SKILLS,
	cancelado: Callable[[], bool] | None = None,
) -> ResultadoBusqueda:
	"""Busca carpetas con SKILL.md dentro de ``raiz`` (incluida ella misma).

	Recorre por niveles, sin seguir enlaces simbólicos ni uniones, sin entrar en
	carpetas de herramientas (``.git``, ``node_modules``...) ni dentro de una
	carpeta que ya es una skill. Se detiene al pasar ``max_entradas`` entradas
	revisadas o ``max_skills`` encontradas (``truncado``).
	"""
	raiz = Path(raiz)
	resultado = ResultadoBusqueda()
	if not raiz.is_dir():
		resultado.errores.append(_("No existe la carpeta «{ruta}».").format(ruta=raiz))
		return resultado
	pendientes: deque[tuple[Path, int]] = deque([(raiz, 0)])
	while pendientes:
		if cancelado is not None and cancelado():
			resultado.truncado = True
			break
		carpeta, nivel = pendientes.popleft()
		try:
			with os.scandir(carpeta) as iterador:
				entradas = sorted(iterador, key=lambda e: e.name.casefold())
		except OSError as error:
			resultado.errores.append(_("No se pudo leer «{archivo}»: {error}").format(archivo=carpeta, error=_motivo(error)))
			continue
		resultado.revisadas += len(entradas)
		skill = next(
			(e for e in entradas if e.name.lower() == "skill.md" and not _es_enlace(e) and e.is_file(follow_symlinks=False)),
			None,
		)
		if skill is not None:
			try:
				ficha = leer_skill(Path(skill.path))
			except ErrorCatalogo as error:
				resultado.errores.append(str(error))
			else:
				if es_plantilla(ficha):
					resultado.plantillas += 1
				else:
					resultado.encontrados.append(ficha)
					if len(resultado.encontrados) >= max_skills:
						resultado.truncado = bool(pendientes)
						break
			continue  # una carpeta de skill no se recorre por dentro
		if resultado.revisadas > max_entradas:
			resultado.truncado = True
			break
		if nivel >= profundidad:
			continue
		for entrada in entradas:
			if entrada.name.casefold() in CARPETAS_IGNORADAS or _es_enlace(entrada):
				continue
			try:
				if entrada.is_dir(follow_symlinks=False):
					pendientes.append((Path(entrada.path), nivel + 1))
			except OSError:
				log.debug("No se pudo mirar %s", entrada.path, exc_info=True)
				continue
	resultado.encontrados.sort(key=lambda f: (str(f.carpeta.parent).casefold(), f.nombre.casefold()))
	return resultado


# ════════════════════════════════════════════════════════════════════════════
# Utilidades de texto y carpetas
# ════════════════════════════════════════════════════════════════════════════

def sin_acentos(texto: str) -> str:
	"""Texto en minúsculas sin acentos (para buscar sin distinguir «Programación» de «programacion»)."""
	descompuesto = unicodedata.normalize("NFKD", texto.casefold())
	return "".join(c for c in descompuesto if not unicodedata.combining(c))


def nombre_seguro(nombre: str) -> str:
	"""Nombre de carpeta válido en Windows y como nombre de habilidad: «Revisión de código» → «revision-de-codigo»."""
	texto = sin_acentos(nombre)
	texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")
	texto = re.sub(r"-{2,}", "-", texto)[:MAX_NOMBRE_CARPETA].strip("-")
	if not texto:
		texto = "skill"
	if texto in _RESERVADOS_WINDOWS:
		texto = f"{texto}-skill"
	return texto


def normalizar_carpeta(carpeta: str | Path) -> str:
	"""Ruta absoluta de una carpeta tal como se guarda en el catálogo (sin resolver enlaces ni unidades de red)."""
	ruta = Path(str(carpeta).strip().strip('"')).expanduser()
	return os.path.normpath(os.path.abspath(ruta))


def _yaml_texto(texto: str) -> str:
	"""Texto entre comillas dobles válido en YAML (una línea)."""
	texto = una_linea(texto)
	return '"' + texto.replace("\\", "\\\\").replace('"', '\\"') + '"'


def contenido_skill_nuevo(nombre: str, descripcion: str, categoria: str = "", identificador: str = "") -> str:
	"""SKILL.md de una skill nueva: frontmatter con nombre y descripción y un cuerpo de plantilla en español."""
	identificador = identificador or nombre_seguro(nombre)
	lineas = [
		"---",
		f"name: {identificador}",
		f"description: {_yaml_texto(descripcion or nombre)}",
		"metadata:",
		'  version: "0.1.0"',
		"  idioma: es",
	]
	if categoria.strip():
		lineas.append(f"  category: {_yaml_texto(categoria)}")
	lineas.append("---")
	titulo = una_linea(nombre) or identificador
	cuerpo = f"""
# {titulo}

{una_linea(descripcion) or "Describe aquí en una o dos frases qué hace esta skill."}

## Cuándo usarlo

- Escribe aquí en qué situaciones debe usarse esta skill.
- Por ejemplo: «cuando la persona pida revisar un texto».

## Instrucciones

1. Explica, paso a paso, lo que debe hacer el agente de IA con esta skill.
2. Di qué debe comprobar antes de dar el trabajo por terminado.
3. Indica cómo debe entregar el resultado.

## Reglas

- Trabaja en español de España.
- Pregunta antes de hacer cambios que no se pueden deshacer.

## Archivos de apoyo

Si esta skill necesita plantillas, guiones o referencias, guárdalos en esta
misma carpeta y menciónalos aquí con su nombre.
"""
	return "\n".join(lineas) + "\n" + cuerpo


def _escribir_atomico(ruta: Path, texto: str) -> None:
	temporal = ruta.with_name(ruta.name + ".tmp")
	temporal.write_text(texto, encoding="utf-8", newline="\n")
	os.replace(temporal, ruta)


def coincide(skill: SkillCatalogo, texto: str) -> bool:
	"""La skill contiene ``texto`` (sin distinguir mayúsculas ni acentos) en nombre, descripción, categoría, notas o carpeta."""
	buscado = sin_acentos(texto.strip())
	if not buscado:
		return True
	pajar = sin_acentos(" ".join((skill.nombre, skill.descripcion, skill.categoria, skill.notas, skill.carpeta)))
	return all(palabra in pajar for palabra in buscado.split())


def disponible(skill: SkillCatalogo) -> bool:
	"""Su carpeta sigue existiendo y tiene SKILL.md."""
	return ruta_skill(skill.carpeta) is not None


def estado(skill: SkillCatalogo) -> str:
	"""``ESTADO_DISPONIBLE`` o ``ESTADO_NO_ENCONTRADO``."""
	return ESTADO_DISPONIBLE if disponible(skill) else ESTADO_NO_ENCONTRADO


# ════════════════════════════════════════════════════════════════════════════
# Catálogo
# ════════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class ResultadoImportar:
	id: int
	#: Se ha añadido ahora (``False``: ya estaba en el catálogo).
	nuevo: bool
	#: Ya estaba y se ha actualizado su descripción.
	actualizado: bool = False


@dataclass(slots=True)
class CambioSkill:
	"""Una skill cuyo SKILL.md dice ahora otra cosa que el catálogo."""

	id: int
	nombre: str
	descripcion_catalogo: str
	descripcion_archivo: str


@dataclass(slots=True)
class ResultadoRefresco:
	revisados: int = 0
	#: Ids de las skills cuya carpeta o SKILL.md ya no existe.
	no_encontrados: list[int] = field(default_factory=list)
	#: Descripciones que han cambiado en el SKILL.md desde la última vez (pendientes de decidir).
	cambiados: list[CambioSkill] = field(default_factory=list)
	#: Skills a las que se ha actualizado la versión (eso no lo edita la persona).
	versiones: int = 0
	errores: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResultadoJson:
	nuevos: int = 0
	actualizados: int = 0
	omitidos: int = 0
	errores: list[str] = field(default_factory=list)


class CatalogoSkills:
	"""Operaciones del catálogo sobre la tabla ``skills`` del almacén.

	:param carpeta_creados: dónde se crean las skills nuevas (por defecto ``data/skills``).
	"""

	def __init__(self, almacen: Almacen, carpeta_creados: str | Path | None = None) -> None:
		self.almacen = almacen
		self._carpeta_creados = Path(carpeta_creados) if carpeta_creados is not None else None

	@property
	def carpeta_creados(self) -> Path:
		if self._carpeta_creados is not None:
			return self._carpeta_creados
		from app.core.paths import ruta_skills

		return ruta_skills()

	# -- Consultas ---------------------------------------------------------------

	def listar(self, categoria: str | None = None, texto: str = "") -> list[SkillCatalogo]:
		"""Skills ordenadas por categoría y nombre. ``categoria=None``: todas; ``""``: las que no tienen."""
		return [skill for skill in self.almacen.skills_catalogo(categoria) if coincide(skill, texto)]

	def obtener(self, id_skill: int) -> SkillCatalogo | None:
		return self.almacen.skill_catalogo(id_skill)

	def categorias(self) -> list[str]:
		return self.almacen.categorias_skills()

	def en_catalogo(self, carpeta: str | Path) -> SkillCatalogo | None:
		"""La skill de esa carpeta, si ya está en el catálogo."""
		return self.almacen.skill_por_carpeta(normalizar_carpeta(carpeta))

	# -- Añadir --------------------------------------------------------------------

	def importar(self, ficha: FichaSkill, categoria: str | None = None, actualizar_descripcion: bool = False) -> ResultadoImportar:
		"""Añade una skill leída de su SKILL.md. Si su carpeta ya estaba, no la duplica.

		:param categoria: la elegida por la persona (``None``: la propuesta por ``categoria_propuesta``).
		:param actualizar_descripcion: si ya estaba, poner la descripción (y la versión) del SKILL.md.
		"""
		carpeta = normalizar_carpeta(ficha.carpeta)
		existente = self.almacen.skill_por_carpeta(carpeta)
		if existente is not None:
			if actualizar_descripcion and (existente.descripcion != ficha.descripcion or existente.version != ficha.version):
				self.almacen.actualizar_skill_catalogo(existente.id, descripcion=ficha.descripcion, version=ficha.version)
				return ResultadoImportar(existente.id, False, True)
			return ResultadoImportar(existente.id, False, False)
		if categoria is None:
			categoria = categoria_propuesta(ficha)
		identificador = self.almacen.crear_skill_catalogo(
			ficha.nombre or Path(carpeta).name, carpeta, descripcion=ficha.descripcion, categoria=una_linea(categoria),
			version=ficha.version, origen=ORIGEN_IMPORTADO,
		)
		log.info("Skill añadida al catálogo: %s (%s)", ficha.nombre, carpeta)
		return ResultadoImportar(identificador, True)

	def cargar_ejemplos(self, carpeta: str | Path | None = None) -> int:
		"""Añade las skills de ejemplo que trae Ctrl+IA (las que falten). Devuelve cuántas se han añadido.

		Van con la categoría ``CATEGORIA_EJEMPLOS``: la persona puede editarlas, quitarlas o
		volver a cargarlas desde el gestor («Restaurar las skills de ejemplo»).
		"""
		from app.core.paths import ruta_skills_ejemplo

		raiz = Path(carpeta) if carpeta is not None else ruta_skills_ejemplo()
		if not raiz.is_dir():
			log.info("No hay skills de ejemplo en %s", raiz)
			return 0
		anadidos = 0
		for subcarpeta in sorted(p for p in raiz.iterdir() if p.is_dir()):
			if ruta_skill(subcarpeta) is None:
				continue
			try:
				ficha = leer_skill(subcarpeta)
				resultado = self.importar(ficha, CATEGORIA_EJEMPLOS)
				if resultado.nuevo:
					# Que se vea en la lista, en «Nueva sesión» y en lo que se le dice al agente de IA que es de ejemplo.
					nombre = (ficha.nombre or subcarpeta.name) + SUFIJO_EJEMPLO
					self.almacen.actualizar_skill_catalogo(resultado.id, nombre=nombre, notas=NOTA_EJEMPLO)
					anadidos += 1
			except Exception:  # noqa: BLE001 - un ejemplo roto no impide cargar los demás
				log.exception("No se pudo cargar la skill de ejemplo %s", subcarpeta)
		log.info("Skills de ejemplo cargadas en el catálogo: %d", anadidos)
		return anadidos

	def reparar_ejemplos(self, carpeta: str | Path | None = None) -> int:
		"""Si el programa portable se movió de carpeta, apunta los ejemplos a su sitio nuevo. Devuelve cuántos."""
		from app.core.paths import ruta_skills_ejemplo

		raiz = Path(carpeta) if carpeta is not None else ruta_skills_ejemplo()
		reparados = 0
		for skill in self.almacen.skills_catalogo():
			antigua = Path(skill.carpeta)
			if "skills_ejemplo" not in {parte.lower() for parte in antigua.parts} or antigua.is_dir():
				continue
			nueva = raiz / antigua.name
			if ruta_skill(nueva) is None or self.almacen.skill_por_carpeta(normalizar_carpeta(nueva)) is not None:
				continue
			self.almacen.actualizar_skill_catalogo(skill.id, carpeta=normalizar_carpeta(nueva))
			reparados += 1
		if reparados:
			log.info("Skills de ejemplo apuntadas a su carpeta nueva: %d", reparados)
		return reparados

	def importar_varios(self, elegidos: Iterable[tuple[FichaSkill, str]], actualizar_descripcion: bool = False) -> list[ResultadoImportar]:
		"""Importa ``[(ficha, categoría), ...]``; un fallo en uno no impide los demás (se registra)."""
		resultados: list[ResultadoImportar] = []
		for ficha, categoria in elegidos:
			try:
				resultados.append(self.importar(ficha, categoria, actualizar_descripcion))
			except Exception:  # noqa: BLE001 - se registra y se sigue con los demás
				log.exception("No se pudo añadir la skill %s", ficha.carpeta)
		return resultados

	def importar_carpeta(self, carpeta: str | Path, categoria: str | None = None,
			actualizar_descripcion: bool = False) -> ResultadoImportar:
		"""Añade la skill de una carpeta con SKILL.md (o del propio archivo). ``ErrorCatalogo`` si no lo es."""
		return self.importar(leer_skill(carpeta), categoria, actualizar_descripcion)

	def crear(self, nombre: str, descripcion: str, categoria: str = "", notas: str = "",
			carpeta_base: str | Path | None = None) -> int:
		"""Crea una skill nueva: su carpeta (``<carpeta_base>/<nombre-seguro>``) con un SKILL.md de plantilla.

		Si la carpeta ya existe se añade un número («revisor-2»). Devuelve su id.
		"""
		nombre = una_linea(nombre)
		if not nombre:
			raise ErrorCatalogo(_("Escribe un nombre para la skill."))
		base = Path(carpeta_base) if carpeta_base else self.carpeta_creados
		try:
			base.mkdir(parents=True, exist_ok=True)
		except OSError as error:
			raise ErrorCatalogo(_("No se pudo crear la carpeta «{ruta}»: {error}").format(ruta=base, error=_motivo(error))) from error
		identificador = nombre_seguro(nombre)
		destino = base / identificador
		numero = 2
		while destino.exists():
			destino = base / f"{identificador}-{numero}"
			numero += 1
		try:
			destino.mkdir()
			_escribir_atomico(destino / NOMBRES_SKILL[0],
				contenido_skill_nuevo(nombre, descripcion, categoria, identificador=destino.name))
		except OSError as error:
			shutil.rmtree(destino, ignore_errors=True)
			raise ErrorCatalogo(_("No se pudo crear la skill en «{ruta}»: {error}").format(
				ruta=destino, error=_motivo(error))) from error
		id_skill = self.almacen.crear_skill_catalogo(
			nombre, normalizar_carpeta(destino), descripcion=una_linea(descripcion), categoria=una_linea(categoria),
			version="0.1.0", origen=ORIGEN_CREADO, notas=notas.strip(),
		)
		log.info("Skill creada: %s en %s", nombre, destino)
		return id_skill

	# -- Cambiar y quitar ------------------------------------------------------------

	def editar(self, id_skill: int, *, nombre: str | None = None, descripcion: str | None = None,
			categoria: str | None = None, notas: str | None = None) -> None:
		"""Cambia los datos del catálogo (nunca el SKILL.md)."""
		cambios: dict[str, str] = {}
		if nombre is not None:
			nombre = una_linea(nombre)
			if not nombre:
				raise ErrorCatalogo(_("El nombre de la skill no puede quedar vacío."))
			cambios["nombre"] = nombre
		if descripcion is not None:
			cambios["descripcion"] = una_linea(descripcion)
		if categoria is not None:
			cambios["categoria"] = una_linea(categoria)
		if notas is not None:
			cambios["notas"] = notas.strip()
		if cambios:
			self.almacen.actualizar_skill_catalogo(id_skill, **cambios)

	def se_puede_borrar_carpeta(self, skill: SkillCatalogo) -> str:
		"""Motivo por el que NO se borraría su carpeta (vacío si se puede)."""
		if skill.origen != ORIGEN_CREADO:
			return _("Solo se borra la carpeta de las skills creadas desde Ctrl+IA.")
		carpeta = Path(skill.carpeta)
		if not carpeta.is_dir():
			return _("La carpeta ya no existe.")
		if carpeta == carpeta.parent or carpeta.parent == carpeta.parent.parent:
			return _("La carpeta es la raíz de una unidad o está justo debajo: no se borra.")
		if ruta_skill(carpeta) is None:
			return _("La carpeta ya no tiene SKILL.md: no parece la de la skill.")
		try:
			from app.core.paths import ruta_datos

			if carpeta.resolve() == ruta_datos().resolve():
				return _("Es la carpeta de datos de Ctrl+IA.")
		except OSError:
			log.debug("No se pudo comparar %s con la carpeta de datos", carpeta, exc_info=True)
		entradas = 0
		for _raiz, carpetas, archivos in os.walk(carpeta):
			entradas += len(carpetas) + len(archivos)
			if entradas > MAX_ENTRADAS_BORRAR:
				return _("La carpeta tiene más de {n} archivos: bórrala tú si de verdad quieres.").format(n=MAX_ENTRADAS_BORRAR)
		return ""

	def eliminar(self, id_skill: int, borrar_carpeta: bool = False) -> bool:
		"""Quita la skill del catálogo; con ``borrar_carpeta`` (solo las creadas aquí) borra también su carpeta.

		Devuelve ``True`` si se ha borrado la carpeta. Si no se puede borrar,
		lanza ``ErrorCatalogo`` y NO la quita del catálogo.
		"""
		skill = self.almacen.skill_catalogo(id_skill)
		if skill is None:
			return False
		borrada = False
		if borrar_carpeta:
			motivo = self.se_puede_borrar_carpeta(skill)
			if motivo:
				raise ErrorCatalogo(motivo)
			try:
				shutil.rmtree(skill.carpeta)
			except OSError as error:
				raise ErrorCatalogo(_("No se pudo borrar «{ruta}»: {error}").format(
					ruta=skill.carpeta, error=_motivo(error))) from error
			borrada = True
			log.info("Carpeta de la skill borrada: %s", skill.carpeta)
		self.almacen.borrar_skill_catalogo(id_skill)
		return borrada

	# -- Refrescar ------------------------------------------------------------------

	def refrescar(self, ids: Sequence[int] | None = None) -> ResultadoRefresco:
		"""Vuelve a leer los SKILL.md: qué skills ya no se encuentran y qué descripciones cambiaron.

		La versión se actualiza sola (la persona no la edita). Una descripción solo
		cuenta como cambiada si el SKILL.md se modificó DESPUÉS de la última vez
		que se tocó la skill en el catálogo: así no se proponen una y otra vez
		las descripciones que la persona ha reescrito a su gusto.
		"""
		resultado = ResultadoRefresco()
		skills = self.almacen.skills_catalogo()
		if ids is not None:
			buscados = set(ids)
			skills = [s for s in skills if s.id in buscados]
		for skill in skills:
			resultado.revisados += 1
			archivo = ruta_skill(skill.carpeta)
			if archivo is None:
				resultado.no_encontrados.append(skill.id)
				continue
			try:
				ficha = leer_skill(archivo)
				modificado_archivo = archivo.stat().st_mtime
			except (ErrorCatalogo, OSError) as error:
				resultado.errores.append(str(error))
				continue
			if ficha.version and ficha.version != skill.version:
				self.almacen.actualizar_skill_catalogo(skill.id, version=ficha.version, modificado=skill.modificado)
				resultado.versiones += 1
			if ficha.descripcion and ficha.descripcion != skill.descripcion and modificado_archivo > skill.modificado:
				resultado.cambiados.append(CambioSkill(skill.id, skill.nombre, skill.descripcion, ficha.descripcion))
		return resultado

	def aplicar_cambios(self, cambios: Iterable[CambioSkill]) -> int:
		"""Pone en el catálogo la descripción nueva de cada cambio. Devuelve cuántos."""
		total = 0
		for cambio in cambios:
			self.almacen.actualizar_skill_catalogo(cambio.id, descripcion=cambio.descripcion_archivo)
			total += 1
		return total

	def descartar_cambios(self, cambios: Iterable[CambioSkill]) -> None:
		"""Deja la descripción del catálogo y no vuelve a proponer estos cambios (hasta que el archivo cambie otra vez)."""
		ahora = time.time()
		for cambio in cambios:
			self.almacen.actualizar_skill_catalogo(cambio.id, modificado=ahora)

	# -- Para las sesiones ------------------------------------------------------------

	def datos_para_sesion(self, ids: Iterable[int]) -> list[dict[str, Any]]:
		"""Lo que necesita una sesión de cada skill elegida, en el mismo orden (los ids que ya no existen se saltan).

		Cada diccionario: ``id``, ``nombre``, ``descripcion``, ``categoria``,
		``carpeta``, ``skill`` (ruta del SKILL.md, aunque no exista), ``existe``
		(si el SKILL.md está) y ``version``.
		"""
		datos: list[dict[str, Any]] = []
		for id_skill in dict.fromkeys(ids):
			skill = self.almacen.skill_catalogo(int(id_skill))
			if skill is None:
				continue
			archivo = ruta_skill(skill.carpeta)
			datos.append({
				"id": skill.id,
				"nombre": skill.nombre,
				"descripcion": skill.descripcion,
				"categoria": skill.categoria,
				"carpeta": skill.carpeta,
				"skill": str(archivo or Path(skill.carpeta) / NOMBRES_SKILL[0]),
				"existe": archivo is not None,
				"version": skill.version,
			})
		return datos

	# -- Exportar e importar (JSON) ------------------------------------------------------

	def exportar_json(self, ruta: str | Path) -> int:
		"""Guarda el catálogo en un JSON (sin el contenido de los SKILL.md). Devuelve cuántas skills."""
		skills = self.almacen.skills_catalogo()
		datos = {
			"formato": FORMATO_JSON,
			"version": VERSION_JSON,
			"exportado": time.time(),
			"skills": [
				{
					"nombre": s.nombre, "descripcion": s.descripcion, "categoria": s.categoria, "carpeta": s.carpeta,
					"version": s.version, "origen": s.origen, "notas": s.notas,
				}
				for s in skills
			],
		}
		ruta = Path(ruta)
		try:
			_escribir_atomico(ruta, json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
		except OSError as error:
			raise ErrorCatalogo(_("No se pudo guardar «{ruta}»: {error}").format(ruta=ruta, error=_motivo(error))) from error
		return len(skills)

	def importar_json(self, ruta: str | Path, actualizar: bool = False) -> ResultadoJson:
		"""Añade las skills de un JSON exportado. Las que ya están (misma carpeta) se omiten o, con ``actualizar``, se actualizan.

		Acepta también los catálogos exportados con la 1.5.0 y anteriores (``proteccion_skills.FORMATO_JSON_ANTIGUO``).
		"""
		from app.services.proteccion_skills import CLAVE_JSON_ANTIGUA, FORMATO_JSON_ANTIGUO

		ruta = Path(ruta)
		try:
			if ruta.stat().st_size > 20 * 1024 * 1024:
				raise ErrorCatalogo(_("«{ruta}» es demasiado grande para ser un catálogo de skills.").format(ruta=ruta))
			datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
		except OSError as error:
			raise ErrorCatalogo(_("No se pudo leer «{archivo}»: {error}").format(archivo=ruta, error=_motivo(error))) from error
		except ValueError as error:
			raise ErrorCatalogo(_("«{ruta}» no es un JSON válido: {error}").format(ruta=ruta, error=error)) from error
		formato = datos.get("formato") if isinstance(datos, dict) else None
		clave = "skills" if formato == FORMATO_JSON else CLAVE_JSON_ANTIGUA if formato == FORMATO_JSON_ANTIGUO else ""
		if not clave or not isinstance(datos.get(clave), list):
			raise ErrorCatalogo(_("«{ruta}» no es un catálogo de skills exportado desde Ctrl+IA.").format(ruta=ruta))
		resultado = ResultadoJson()
		for numero, elemento in enumerate(datos[clave], start=1):
			if not isinstance(elemento, dict) or not str(elemento.get("carpeta") or "").strip():
				resultado.errores.append(_("La skill número {n} no tiene carpeta.").format(n=numero))
				continue
			carpeta = normalizar_carpeta(str(elemento["carpeta"]))
			nombre = una_linea(elemento.get("nombre")) or Path(carpeta).name
			campos = {
				"nombre": nombre,
				"descripcion": una_linea(elemento.get("descripcion")),
				"categoria": una_linea(elemento.get("categoria")),
				"version": una_linea(elemento.get("version")),
				"notas": str(elemento.get("notas") or "").strip(),
			}
			existente = self.almacen.skill_por_carpeta(carpeta)
			if existente is not None:
				if actualizar:
					self.almacen.actualizar_skill_catalogo(existente.id, **campos)
					resultado.actualizados += 1
				else:
					resultado.omitidos += 1
				continue
			origen = elemento.get("origen") if elemento.get("origen") in (ORIGEN_IMPORTADO, ORIGEN_CREADO) else ORIGEN_IMPORTADO
			self.almacen.crear_skill_catalogo(nombre, carpeta, descripcion=campos["descripcion"],
				categoria=campos["categoria"], version=campos["version"], origen=origen, notas=campos["notas"])
			resultado.nuevos += 1
		return resultado


def datos_para_sesion(almacen: Almacen, ids: Iterable[int]) -> list[dict[str, Any]]:
	"""Atajo de ``CatalogoSkills(almacen).datos_para_sesion(ids)``."""
	return CatalogoSkills(almacen).datos_para_sesion(ids)
