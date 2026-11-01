"""Las reglas guardadas de Codex (``~/.codex/rules/*.rules``), para «Permisos de la sesión» (AIChat #307, #343).

Cuando le dices a Codex que permita siempre una orden, añade a un archivo
``.rules`` una línea como ``prefix_rule(pattern=["git", "commit"],
decision="allow")``, y desde entonces esa orden se ejecuta sin preguntar y fuera
del aislamiento, en todas sus sesiones. Con solo decir cuántos archivos hay, una
sesión podía hacer commit sin preguntar y nada explicaba por qué.

Los archivos son Starlark (la política de ejecución de Codex):
``prefix_rule(pattern, decision, justification, match, not_match)`` y
``host_executable(name, paths)``. Un patrón es una lista de palabras, y una palabra
puede ser una lista de alternativas; ``decision`` es allow (si no se dice), prompt o
forbidden. Para estas llamadas su sintaxis es la de Python, así que se analizan con
``ast``: NUNCA se ejecutan. Lo demás que haya en un archivo se cuenta, no se adivina.

Solo se leen: Ctrl+IA no los cambia nunca. Sin wx.
"""

from __future__ import annotations

import ast
import logging
import os
import stat
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Cada decisión y cómo se dice, de menos a más estricta (Codex: forbidden gana a prompt, y prompt a allow).
CLASES = (("allow", "Permitido sin preguntar"), ("prompt", "Pregunta siempre"), ("forbidden", "Nunca se permite"))
#: Codex rechaza lo que una regla dice preguntar si la sesión nunca pregunta (``exec_policy.rs``).
PROMPT_SIN_PREGUNTAS = "Se rechaza en esta sesión, que nunca pregunta"
#: Un archivo de reglas mayor que esto no se lee: los de Codex ocupan unos kilobytes.
MAX_TAMANO = 1024 * 1024
#: La lista termina aquí, para que un archivo enorme no entierre el resto de la nota.
MAX_REGLAS = 500
#: Una regla más larga se corta: cada una se queda en una línea o dos que leer.
MAX_ORDEN = 300

INTRODUCCION = ("Reglas guardadas de Codex. Valen en todas sus sesiones, aquí, en la app de Codex o en una consola. Cada "
	"regla cubre cualquier orden que empiece por esas palabras, y una orden permitida se ejecuta fuera de los límites de "
	"esta carpeta: puede cambiar archivos en otros sitios y usar la red. Las de la carpeta .codex de un proyecto solo "
	"valen ahí, si es un proyecto de confianza. Ctrl+IA solo las lee: se cambian en Codex o editando el archivo.")
SIN_REGLAS = "Las reglas guardadas de Codex no permiten nada más."


@dataclass(frozen=True, slots=True)
class ReglaCodex:
	"""Una regla leída: su decisión (``allow``, ``prompt`` o ``forbidden``), la orden en una línea y su archivo."""

	decision: str
	orden: str
	ruta: str


def carpeta_codex() -> Path:
	otra = os.environ.get("CODEX_HOME")
	return Path(otra) if otra else Path.home() / ".codex"


def _archivos_en(carpeta: Path) -> list[Path]:
	try:
		return sorted(ruta for ruta in carpeta.iterdir() if ruta.is_file() and ruta.suffix == ".rules")
	except OSError:
		log.debug("No se pudo mirar la carpeta de reglas de Codex %s", carpeta, exc_info=True)
		return []


def archivos_de_reglas(carpeta_sesion: Path | str | None = None) -> list[Path]:
	"""Los ``.rules`` de Codex y, con ``carpeta_sesion``, los de su ``.codex/rules``; vacío si no hay."""
	archivos = _archivos_en(carpeta_codex() / "rules")
	if carpeta_sesion:
		archivos += [ruta for ruta in _archivos_en(Path(carpeta_sesion) / ".codex" / "rules") if ruta not in archivos]
	return archivos


def _visible(texto: str) -> str:
	"""``texto`` en una línea y con los caracteres de control o de formato (NUL, ESC, cambios de sentido) escapados.

	Así nada de un archivo puede esconder una línea de la nota, reordenarla ni hacerse pasar por otra regla.
	"""
	texto = " ".join(texto.split())
	return "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co") else c for c in texto)


def _palabra(valor: Any) -> str | None:
	"""Una palabra del patrón, o sus alternativas («(push o pull)»), en una línea; ``None`` si no se entiende."""
	if isinstance(valor, str):
		texto = _visible(valor)
		if texto and texto == valor and not any(c in texto for c in ' "'):
			return texto
		# Con espacios o comillas (un guion de PowerShell entero), entre comillas para que se lea como una palabra.
		return '"' + _visible(" ".join(valor.split()).replace("\\", "\\\\").replace('"', '\\"')) + '"'
	if isinstance(valor, list) and valor and all(isinstance(v, str) for v in valor):
		return "(" + " o ".join(str(_palabra(v)) for v in valor) + ")"
	return None


def _regla(llamada: ast.Call) -> tuple[str, str] | None:
	"""La decisión y la orden de un ``prefix_rule``, o ``None`` si no es sencillo."""
	if llamada.args:
		return None
	try:
		argumentos = {k.arg: ast.literal_eval(k.value) for k in llamada.keywords if k.arg}
	except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
		return None
	if len(argumentos) != len(llamada.keywords):
		return None
	patron, decision = argumentos.get("pattern"), argumentos.get("decision", "allow")
	if decision not in dict(CLASES) or not isinstance(patron, list) or not patron:
		return None
	palabras = [_palabra(p) for p in patron]
	if None in palabras:
		return None
	orden = " ".join(str(p) for p in palabras)
	return decision, orden[:MAX_ORDEN] + "..." if len(orden) > MAX_ORDEN else orden


def _texto(ruta: Path) -> str:
	"""El texto de un archivo normal, nunca más de ``MAX_TAMANO``, diga lo que diga su tamaño (una tubería dice 0)."""
	with open(ruta, "rb") as archivo:
		if not stat.S_ISREG(os.fstat(archivo.fileno()).st_mode):
			raise ValueError("No es un archivo normal")
		datos = archivo.read(MAX_TAMANO + 1)
	if len(datos) > MAX_TAMANO:
		raise ValueError("Demasiado grande")
	return datos.decode("utf-8-sig")


def _leer(ruta: Path) -> tuple[list[ReglaCodex], int] | None:
	"""Las reglas de un archivo y cuántas entradas no se pueden enseñar; ``None`` si no se puede leer."""
	try:
		if not ruta.is_file():  # antes de abrirlo: una tubería no debe dejar esperando a nadie
			return None
		arbol = ast.parse(_texto(ruta), str(ruta))
	except (OSError, ValueError, SyntaxError, RecursionError, MemoryError):
		log.debug("No se pudo leer el archivo de reglas de Codex %s", ruta, exc_info=True)
		return None
	reglas: list[ReglaCodex] = []
	otras = 0
	for nodo in arbol.body:
		llamada = nodo.value if isinstance(nodo, ast.Expr) else None
		nombre = llamada.func.id if isinstance(llamada, ast.Call) and isinstance(llamada.func, ast.Name) else None
		if nombre == "host_executable":
			continue  # a qué programas se refiere la primera palabra de una regla; no es un permiso
		encontrada = _regla(llamada) if nombre == "prefix_rule" else None
		if encontrada is None:
			otras += 1
		else:
			reglas.append(ReglaCodex(encontrada[0], encontrada[1], str(ruta)))
	return reglas, otras


def reglas_de(rutas: Sequence[Path]) -> tuple[list[ReglaCodex], list[str], int]:
	"""Las reglas de ``rutas``, los archivos que no se pudieron leer y cuántas entradas no se pueden enseñar."""
	reglas: list[ReglaCodex] = []
	ilegibles: list[str] = []
	otras = 0
	for ruta in rutas:
		leido = _leer(Path(ruta))
		if leido is None:
			ilegibles.append(str(ruta))
		else:
			reglas += leido[0]
			otras += leido[1]
	return reglas, ilegibles, otras


def describir(reglas: list[ReglaCodex], ilegibles: Sequence[str] = (), otras: int = 0, nunca_pregunta: bool = False) -> str:
	"""La nota de «Permisos de la sesión», una regla por línea.

	:param nunca_pregunta: la sesión es de las que nunca preguntan: Codex rechaza lo que una regla dice preguntar.
	"""
	etiquetas = dict(CLASES)
	if nunca_pregunta:
		etiquetas["prompt"] = PROMPT_SIN_PREGUNTAS
	lineas = [INTRODUCCION]
	if not reglas and not ilegibles and not otras:
		lineas.append(SIN_REGLAS)
	mostradas = 0
	for ruta in dict.fromkeys(regla.ruta for regla in reglas):
		if mostradas == MAX_REGLAS:
			break
		lineas.append(f"En {_visible(ruta)}:")
		for clase, _etiqueta in CLASES:
			for regla in (r for r in reglas if r.ruta == ruta and r.decision == clase):
				if mostradas == MAX_REGLAS:
					break
				lineas.append(f"{etiquetas[clase]}: órdenes que empiezan por {regla.orden}.")
				mostradas += 1
	faltan = len(reglas) - mostradas
	if faltan:
		lineas.append(f"{faltan} reglas más no caben aquí: abre el archivo para verlas." if faltan != 1
			else "1 regla más no cabe aquí: abre el archivo para verla.")
	if otras:
		lineas.append(f"{otras} entradas de los archivos de reglas no se pueden enseñar aquí: abre el archivo para verlas." if otras != 1
			else "1 entrada de los archivos de reglas no se puede enseñar aquí: abre el archivo para verla.")
	lineas += [f"No se ha podido leer {_visible(ruta)}, así que sus reglas no salen aquí." for ruta in ilegibles]
	return "\n".join(lineas)
