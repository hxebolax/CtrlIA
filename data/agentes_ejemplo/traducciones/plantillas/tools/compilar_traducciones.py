"""Compila los catálogos .po a .mo (formato binario de GNU gettext) sin dependencias.

Uso (desde la raíz del proyecto)::

	python tools/compilar_traducciones.py            # todos los .po de data/locales
	python tools/compilar_traducciones.py ruta.po    # uno concreto

Qué hace además de compilar:

- Omite las entradas «fuzzy» (aproximadas) y las que están sin traducir: la
  aplicación mostrará el texto original en español, que es mejor que una
  traducción dudosa.
- Comprueba que cada traducción conserva los marcadores del original
  (``{nombre}``, ``{}``, ``%s``, ``%(n)d``). Una traducción con un marcador
  que no existe haría fallar ``.format()`` en tiempo de ejecución; por eso
  esa entrada NO se compila y se informa como error.
- Avisa si cambia el número de aceleradores ``&`` o el atajo que sigue al
  tabulador de un menú (``&Guardar\\tCtrl+S``).
- Comprueba que ``Plural-Forms`` existe y es válido si hay plurales, y que
  cada plural tiene tantas formas como ``nplurals``.

Formato del .mo: manual de GNU gettext, «The Format of GNU MO Files»
(https://www.gnu.org/software/gettext/manual/html_node/MO-Files.html).

Código de salida 1 si hay errores (el .mo se genera igualmente sin las
entradas erróneas, para no bloquear el resto del trabajo).

Solo biblioteca estándar. PLANTILLA del agente «traducciones».
"""

from __future__ import annotations

import argparse
import array
import gettext
import logging
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
	from tools import catalogo_po  # importado como paquete (pruebas)
except ImportError:
	import catalogo_po  # ejecutado como script

log = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
NUMERO_MAGICO = 0x950412DE
_RE_NPLURALS = re.compile(r"nplurals\s*=\s*(\d+)")
_RE_PLURAL = re.compile(r"plural\s*=\s*([^;]+);?")


@dataclass
class Informe:
	"""Resultado de compilar un .po."""

	origen: Path
	destino: Path | None = None
	compiladas: int = 0
	aproximadas: int = 0
	sin_traducir: int = 0
	errores: list[str] = field(default_factory=list)
	avisos: list[str] = field(default_factory=list)


def _aceleradores(texto: str) -> int:
	"""Número de ``&`` que marcan acelerador (``&&`` es un ampersand literal)."""
	return len(re.findall(r"(?<!&)&(?!&)", texto.replace("&&", "")))


def _atajo_de_menu(texto: str) -> str:
	return texto.split("\t", 1)[1] if "\t" in texto else ""


def validar_entrada(entrada: catalogo_po.Entrada, nplurals: int | None) -> tuple[list[str], list[str]]:
	"""Errores (impiden compilar la entrada) y avisos de una entrada traducida."""
	errores: list[str] = []
	avisos: list[str] = []
	donde = f"línea {entrada.linea} («{catalogo_po.escapar(entrada.msgid[:50])}»)"
	if entrada.es_plural:
		permitidos = set(catalogo_po.marcadores(entrada.msgid)) | set(catalogo_po.marcadores(entrada.msgid_plural or ""))
		traducciones = [entrada.msgstr_plural[i] for i in sorted(entrada.msgstr_plural)]
		if nplurals is not None and len(traducciones) != nplurals:
			errores.append(f"{donde}: tiene {len(traducciones)} formas de plural y el idioma declara {nplurals}.")
		usados: set[str] = set()
		for indice, traduccion in enumerate(traducciones):
			propios = set(catalogo_po.marcadores(traduccion))
			usados |= propios
			sobrantes = propios - permitidos
			if sobrantes:
				errores.append(f"{donde}: msgstr[{indice}] usa marcadores que no existen en el original: {', '.join(sorted(sobrantes))}.")
		faltan = set(catalogo_po.marcadores(entrada.msgid_plural or "")) - usados
		if faltan:
			avisos.append(f"{donde}: ninguna forma plural usa {', '.join(sorted(faltan))}.")
		originales, traducidos = entrada.msgid, traducciones[0] if traducciones else ""
	else:
		original = sorted(catalogo_po.marcadores(entrada.msgid))
		traducido = sorted(catalogo_po.marcadores(entrada.msgstr))
		if original != traducido:
			errores.append(
				f"{donde}: los marcadores no coinciden (original: {', '.join(original) or 'ninguno'}; "
				f"traducción: {', '.join(traducido) or 'ninguno'})."
			)
		originales, traducidos = entrada.msgid, entrada.msgstr
	if _aceleradores(originales) != _aceleradores(traducidos):
		avisos.append(f"{donde}: el original tiene {_aceleradores(originales)} acelerador(es) & y la traducción {_aceleradores(traducidos)}.")
	if _atajo_de_menu(originales) != _atajo_de_menu(traducidos):
		avisos.append(f"{donde}: el atajo tras el tabulador cambió de «{_atajo_de_menu(originales)}» a «{_atajo_de_menu(traducidos)}».")
	return errores, avisos


def generar_mo(mensajes: dict[str, str]) -> bytes:
	"""Construye el binario .mo a partir de {clave: traducción} ya codificables.

	La tabla de originales va ORDENADA por bytes: GNU gettext busca con
	búsqueda binaria cuando no hay tabla hash (tamaño 0, como aquí).
	"""
	claves = sorted(mensajes, key=lambda clave: clave.encode("utf-8"))
	originales = [clave.encode("utf-8") for clave in claves]
	traducciones = [mensajes[clave].encode("utf-8") for clave in claves]
	total = len(claves)
	inicio_originales = 7 * 4
	inicio_traducciones = inicio_originales + total * 8
	inicio_cadenas = inicio_traducciones + total * 8
	tabla_originales = array.array("I")
	tabla_traducciones = array.array("I")
	cuerpo = bytearray()
	for texto in originales:
		tabla_originales.extend((len(texto), inicio_cadenas + len(cuerpo)))
		cuerpo += texto + b"\x00"
	for texto in traducciones:
		tabla_traducciones.extend((len(texto), inicio_cadenas + len(cuerpo)))
		cuerpo += texto + b"\x00"
	if sys.byteorder != "little":
		tabla_originales.byteswap()
		tabla_traducciones.byteswap()
	cabecera = struct.pack(
		"<7I",
		NUMERO_MAGICO,
		0,  # revisión del formato
		total,
		inicio_originales,
		inicio_traducciones,
		0,  # tamaño de la tabla hash (sin tabla)
		inicio_cadenas,  # posición de la tabla hash (sin uso)
	)
	return cabecera + tabla_originales.tobytes() + tabla_traducciones.tobytes() + bytes(cuerpo)


def _validar_formas_plurales(formas: str) -> int | None:
	"""``nplurals`` si la regla ``Plural-Forms`` es válida; None si falta o no lo es."""
	coincidencia_n, coincidencia_p = _RE_NPLURALS.search(formas), _RE_PLURAL.search(formas)
	if not coincidencia_n or not coincidencia_p:
		return None
	try:
		# La misma función que usa gettext al cargar el .mo.
		gettext.c2py(coincidencia_p.group(1).strip())
	except (ValueError, SyntaxError, RecursionError):
		log.debug("Regla de plurales no válida: %s", formas, exc_info=True)
		return None
	return int(coincidencia_n.group(1))


def compilar(origen: Path, destino: Path | None = None) -> Informe:
	"""Compila un .po. El .mo se escribe junto al .po si no se indica destino."""
	informe = Informe(origen=origen)
	try:
		catalogo = catalogo_po.leer_po(origen)
	except (OSError, UnicodeDecodeError, catalogo_po.ErrorPO) as error:
		informe.errores.append(f"No se pudo leer: {error}")
		return informe
	metadatos = catalogo.metadatos()
	formas = metadatos.get("Plural-Forms", "")
	nplurals = _validar_formas_plurales(formas)
	hay_plurales = any(e.es_plural and not e.obsoleta for e in catalogo.entradas)
	if nplurals is None and hay_plurales:
		informe.errores.append("Falta un «Plural-Forms» válido en la cabecera; los plurales no se compilan.")
	elif nplurals is None and formas:
		informe.avisos.append(f"«Plural-Forms: {formas}» no es válido; se omite del .mo (no hay plurales que lo necesiten).")
	mensajes: dict[str, str] = {}
	if catalogo.cabecera is not None:
		cabecera = re.sub(r"charset=[A-Za-z0-9_.:-]+", "charset=UTF-8", catalogo.cabecera.msgstr)
		if nplurals is None:
			# Python lanza ValueError al CARGAR un .mo con una regla inválida
			# (por ejemplo, el marcador «nplurals=INTEGER» de la plantilla).
			cabecera = "".join(linea for linea in cabecera.splitlines(True) if not linea.startswith("Plural-Forms:"))
		mensajes[""] = cabecera
	for entrada in catalogo.entradas:
		if entrada.obsoleta:
			continue
		if entrada.aproximada:
			informe.aproximadas += 1
			continue
		if not entrada.traducida:
			informe.sin_traducir += 1
			continue
		if entrada.es_plural and nplurals is None:
			continue
		errores, avisos = validar_entrada(entrada, nplurals)
		informe.avisos.extend(avisos)
		if errores:
			informe.errores.extend(errores)
			continue
		clave = entrada.msgid
		if entrada.es_plural:
			clave += "\x00" + (entrada.msgid_plural or "")
			valor = "\x00".join(entrada.msgstr_plural[i] for i in sorted(entrada.msgstr_plural))
		else:
			valor = entrada.msgstr
		if entrada.msgctxt is not None:
			clave = entrada.msgctxt + catalogo_po.SEPARADOR_CONTEXTO + clave
		mensajes[clave] = valor
		informe.compiladas += 1
	destino = destino or origen.with_suffix(".mo")
	temporal = destino.with_name(destino.name + ".tmp")
	try:
		temporal.write_bytes(generar_mo(mensajes))
		temporal.replace(destino)
	except OSError as error:
		# Típico en Windows: el .mo está abierto por la aplicación en marcha.
		temporal.unlink(missing_ok=True)
		informe.errores.append(f"No se pudo escribir {destino}: {error}")
		return informe
	informe.destino = destino
	return informe


def buscar_po(raiz: Path) -> list[Path]:
	return sorted((raiz / "data" / "locales").glob("*/LC_MESSAGES/*.po"))


def main(argumentos: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Compila los catálogos .po a .mo.")
	analizador.add_argument("archivos", nargs="*", type=Path, help="Archivos .po (por defecto, todos los de data/locales).")
	analizador.add_argument("--raiz", type=Path, default=RAIZ, help="Raíz del proyecto.")
	opciones = analizador.parse_args(argumentos)
	catalogo_po.preparar_consola()
	archivos = opciones.archivos or buscar_po(opciones.raiz.resolve())
	if not archivos:
		print("No hay archivos .po en data/locales/<idioma>/LC_MESSAGES/.")
		return 0
	hay_errores = False
	for archivo in archivos:
		informe = compilar(archivo)
		print(f"{archivo}:")
		if informe.destino:
			print(
				f"  Compilado en {informe.destino}. Traducidas: {informe.compiladas}. "
				f"Aproximadas (omitidas): {informe.aproximadas}. Sin traducir: {informe.sin_traducir}."
			)
		for error in informe.errores:
			print(f"  Error: {error}")
		for aviso in informe.avisos:
			print(f"  Aviso: {aviso}")
		hay_errores = hay_errores or bool(informe.errores)
	return 1 if hay_errores else 0


if __name__ == "__main__":
	sys.exit(main())
