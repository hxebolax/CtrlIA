"""Extrae las cadenas traducibles del código y genera ``data/locales/<dominio>.pot``.

Uso (desde la raíz del proyecto)::

	python tools/extraer_cadenas.py
	python tools/extraer_cadenas.py --dominio ctrlia --salida data/locales/ctrlia.pot

Qué reconoce (analizando el código con ``ast``, no con expresiones regulares,
para no confundirse con comillas, paréntesis anidados o llamadas en varias
líneas):

- ``_("texto")``, ``gettext("texto")`` y ``N_("texto")`` (marca sin traducir,
  para constantes que se traducen más tarde con ``_(CONSTANTE)``).
- ``ngettext("singular", "plural", n)`` y ``n_(...)``.
- ``pgettext("contexto", "texto")`` y ``npgettext("contexto", "singular", "plural", n)``.
- Cadenas concatenadas implícitamente (``_("Primera parte " "segunda")``) o
  con ``+`` entre literales.
- Comentarios para traductores: una o varias líneas ``# Traductores: ...``
  justo encima de la llamada.

Es un ERROR (código de salida 1) pasar a ``_()`` algo que no es un literal:
una f-string, una variable, ``"..." % x`` o ``"...".format(x)``. En esos casos
la cadena no se puede extraer, o el texto que se busca en el catálogo ya
lleva los datos dentro y nunca coincidirá con ninguna traducción.

Solo biblioteca estándar. PLANTILLA del agente «traducciones».
"""

from __future__ import annotations

import argparse
import ast
import io
import logging
import re
import sys
import tokenize
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
	from tools import catalogo_po  # importado como paquete (pruebas)
except ImportError:
	import catalogo_po  # ejecutado como script: python tools/extraer_cadenas.py

log = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent

# Carpetas y archivos (relativos a la raíz) donde se buscan cadenas.
ORIGENES = ["app", "main.py"]
# Carpetas que nunca se recorren.
EXCLUIDAS = {"__pycache__", "tests", "venv", ".venv", "build", "data"}

# Función → posición de los argumentos (contexto, singular, plural). None = no aplica.
FUNCIONES: dict[str, tuple[int | None, int, int | None]] = {
	"_": (None, 0, None),
	"gettext": (None, 0, None),
	"N_": (None, 0, None),
	"ngettext": (None, 0, 1),
	"n_": (None, 0, 1),
	"pgettext": (0, 1, None),
	"npgettext": (0, 1, 2),
}
PREFIJO_COMENTARIO = "Traductores:"

_RE_VERSION = re.compile(r"^APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


@dataclass
class Aparicion:
	"""Un mensaje encontrado en el código."""

	msgid: str
	msgctxt: str | None
	msgid_plural: str | None
	archivo: str
	linea: int
	comentarios: list[str] = field(default_factory=list)


@dataclass
class Problema:
	archivo: str
	linea: int
	mensaje: str
	grave: bool = True

	def __str__(self) -> str:
		tipo = "Error" if self.grave else "Aviso"
		return f"{tipo} en {self.archivo}:{self.linea}: {self.mensaje}"


def _literal(nodo: ast.AST) -> str | None:
	"""Texto de un literal de cadena (también ``"a" + "b"``); None si no es literal."""
	if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
		return nodo.value
	if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
		izquierda, derecha = _literal(nodo.left), _literal(nodo.right)
		if izquierda is not None and derecha is not None:
			return izquierda + derecha
	return None


def _explicar(nodo: ast.AST) -> str:
	"""Por qué un argumento no es extraíble, en palabras de quien lo corrige."""
	if isinstance(nodo, ast.JoinedStr):
		return "es una f-string; use un marcador con nombre: _(\"Hola, {nombre}\").format(nombre=nombre)"
	if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Mod):
		return "formatea con % DENTRO de _(); formatee fuera: _(\"%s archivos\") % n"
	if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "format":
		return "llama a .format() DENTRO de _(); hágalo fuera: _(\"{n} archivos\").format(n=n)"
	if isinstance(nodo, ast.Name):
		return f"recibe la variable «{nodo.id}»; márquela donde se define con N_(\"...\")"
	return "no es un literal de cadena y no se puede extraer"


def _nombre_funcion(llamada: ast.Call) -> str | None:
	if isinstance(llamada.func, ast.Name):
		return llamada.func.id
	# gettext.ngettext(...), traduccion.pgettext(...): solo las funciones con nombre claro.
	if isinstance(llamada.func, ast.Attribute) and llamada.func.attr in FUNCIONES and llamada.func.attr != "_":
		return llamada.func.attr
	return None


def _comentarios_solos(codigo: str) -> dict[int, str]:
	"""Líneas que solo contienen un comentario → texto del comentario."""
	comentarios: dict[int, str] = {}
	try:
		for token in tokenize.generate_tokens(io.StringIO(codigo).readline):
			if token.type == tokenize.COMMENT and token.line.strip().startswith("#"):
				comentarios[token.start[0]] = token.string[1:].strip()
	except (tokenize.TokenError, SyntaxError):
		log.debug("No se pudieron leer los comentarios", exc_info=True)
	return comentarios


def _comentario_traductores(comentarios: dict[int, str], linea: int) -> list[str]:
	"""Bloque ``# Traductores: ...`` inmediatamente encima de ``linea``."""
	bloque: list[str] = []
	actual = linea - 1
	while actual in comentarios:
		bloque.insert(0, comentarios[actual])
		actual -= 1
	for indice, texto in enumerate(bloque):
		if texto.startswith(PREFIJO_COMENTARIO):
			return [" ".join(bloque[indice:])]
	return []


def extraer_de_codigo(codigo: str, archivo: str) -> tuple[list[Aparicion], list[Problema]]:
	"""Mensajes y problemas de un archivo de código."""
	apariciones: list[Aparicion] = []
	problemas: list[Problema] = []
	try:
		arbol = ast.parse(codigo, filename=archivo)
	except SyntaxError as error:
		return [], [Problema(archivo, error.lineno or 0, f"no se pudo analizar: {error.msg}")]
	comentarios = _comentarios_solos(codigo)
	for nodo in ast.walk(arbol):
		if not isinstance(nodo, ast.Call):
			continue
		nombre = _nombre_funcion(nodo)
		if nombre not in FUNCIONES:
			continue
		pos_contexto, pos_singular, pos_plural = FUNCIONES[nombre]
		necesarios = max(p for p in (pos_contexto, pos_singular, pos_plural) if p is not None) + 1
		if len(nodo.args) < necesarios:
			problemas.append(Problema(archivo, nodo.lineno, f"{nombre}() necesita {necesarios} argumentos posicionales."))
			continue
		valores: dict[str, str | None] = {}
		correcto = True
		for campo, posicion in (("msgctxt", pos_contexto), ("msgid", pos_singular), ("msgid_plural", pos_plural)):
			if posicion is None:
				valores[campo] = None
				continue
			argumento = nodo.args[posicion]
			texto = _literal(argumento)
			if texto is None:
				problemas.append(Problema(archivo, nodo.lineno, f"{nombre}() {_explicar(argumento)}."))
				correcto = False
				break
			valores[campo] = texto
		if not correcto:
			continue
		if valores["msgid"] == "":
			# _("") devuelve la CABECERA del catálogo, nunca una cadena vacía.
			problemas.append(Problema(archivo, nodo.lineno, f"{nombre}(\"\") devuelve la cabecera del catálogo; no traduzca cadenas vacías."))
			continue
		msgid = valores["msgid"] or ""
		posicionales = [m for m in catalogo_po.marcadores(msgid) if m in ("{}", "%s", "%d", "%i", "%f", "%r")]
		if len(posicionales) > 1:
			problemas.append(
				Problema(
					archivo,
					nodo.lineno,
					f"«{msgid}» tiene {len(posicionales)} marcadores sin nombre; use nombres ({{nombre}}) para que la traducción pueda cambiar el orden.",
					grave=False,
				)
			)
		apariciones.append(
			Aparicion(
				msgid=msgid,
				msgctxt=valores["msgctxt"],
				msgid_plural=valores["msgid_plural"],
				archivo=archivo,
				linea=nodo.lineno,
				comentarios=_comentario_traductores(comentarios, nodo.lineno),
			)
		)
	apariciones.sort(key=lambda a: a.linea)
	return apariciones, problemas


def archivos_de_origen(raiz: Path, origenes: list[str] | None = None) -> list[Path]:
	"""Archivos .py a analizar, en orden estable (el .pot no cambia sin motivo)."""
	encontrados: list[Path] = []
	for origen in origenes or ORIGENES:
		ruta = raiz / origen
		if ruta.is_file() and ruta.suffix == ".py":
			encontrados.append(ruta)
		elif ruta.is_dir():
			for archivo in sorted(ruta.rglob("*.py")):
				if not EXCLUIDAS.intersection(archivo.relative_to(raiz).parts):
					encontrados.append(archivo)
	return encontrados


def extraer(raiz: Path, origenes: list[str] | None = None) -> tuple[list[catalogo_po.Entrada], list[Problema]]:
	"""Entradas del .pot (en orden de primera aparición) y problemas encontrados."""
	entradas: dict[tuple[str | None, str], catalogo_po.Entrada] = {}
	problemas: list[Problema] = []
	for archivo in archivos_de_origen(raiz, origenes):
		relativo = archivo.relative_to(raiz).as_posix()
		try:
			codigo = archivo.read_text(encoding="utf-8")
		except (OSError, UnicodeDecodeError) as error:
			problemas.append(Problema(relativo, 0, f"no se pudo leer: {error}"))
			continue
		apariciones, encontrados = extraer_de_codigo(codigo, relativo)
		problemas.extend(encontrados)
		for aparicion in apariciones:
			clave = (aparicion.msgctxt, aparicion.msgid)
			entrada = entradas.get(clave)
			if entrada is None:
				entrada = catalogo_po.Entrada(
					msgid=aparicion.msgid,
					msgctxt=aparicion.msgctxt,
					msgid_plural=aparicion.msgid_plural,
					banderas=catalogo_po.banderas_de_formato(aparicion.msgid + (aparicion.msgid_plural or "")),
				)
				entradas[clave] = entrada
			elif aparicion.msgid_plural != entrada.msgid_plural:
				if entrada.msgid_plural is None or aparicion.msgid_plural is None:
					# El mismo texto usado como simple y como plural: gana el plural.
					entrada.msgid_plural = entrada.msgid_plural or aparicion.msgid_plural
				else:
					problemas.append(
						Problema(
							aparicion.archivo,
							aparicion.linea,
							f"«{aparicion.msgid}» ya se usó con otro plural («{entrada.msgid_plural}»); use un contexto distinto.",
						)
					)
			entrada.referencias.append(f"{aparicion.archivo}:{aparicion.linea}")
			for comentario in aparicion.comentarios:
				if comentario not in entrada.comentarios_extraidos:
					entrada.comentarios_extraidos.append(comentario)
	return list(entradas.values()), problemas


def crear_cabecera(nombre: str, version: str, fecha: str) -> catalogo_po.Entrada:
	campos = [
		f"Project-Id-Version: {nombre} {version}",
		"Report-Msgid-Bugs-To: ",
		f"POT-Creation-Date: {fecha}",
		"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE",
		"Last-Translator: FULL NAME <EMAIL@ADDRESS>",
		"Language-Team: LANGUAGE <LL@li.org>",
		"Language: ",
		"MIME-Version: 1.0",
		"Content-Type: text/plain; charset=UTF-8",
		"Content-Transfer-Encoding: 8bit",
		# En la plantilla va el marcador estándar; actualizar_po.py --nuevo
		# pone la regla real de cada idioma.
		"Plural-Forms: nplurals=INTEGER; plural=EXPRESSION;",
		"X-Generator: extraer_cadenas.py",
	]
	return catalogo_po.Entrada(
		msgid="",
		msgstr="\n".join(campos) + "\n",
		comentarios_traductor=[
			f"Plantilla de traducción de {nombre}.",
			"El idioma de los textos originales (msgid) es el español.",
		],
		banderas=["fuzzy"],
	)


def generar_pot(raiz: Path, salida: Path | None = None, dominio: str | None = None) -> tuple[Path, int, list[Problema]]:
	"""Genera el .pot. Devuelve (ruta, número de mensajes, problemas).

	Si el contenido no cambió, no reescribe el archivo: así la fecha de
	creación no ensucia el control de versiones en cada ejecución.
	"""
	dominio = dominio or catalogo_po.dominio_del_proyecto(raiz)
	salida = salida or raiz / "data" / "locales" / f"{dominio}.pot"
	version = catalogo_po.leer_constante(raiz / "app" / "core" / "version.py", _RE_VERSION, "1.0")
	entradas, problemas = extraer(raiz)
	fecha = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M%z")
	catalogo = catalogo_po.Catalogo(cabecera=crear_cabecera(dominio, version, fecha), entradas=entradas)
	texto = catalogo_po.formatear_catalogo(catalogo)
	if salida.is_file():
		sin_fecha = re.compile(r'^"POT-Creation-Date:.*$', re.MULTILINE)
		anterior = salida.read_text(encoding="utf-8")
		if sin_fecha.sub("", anterior) == sin_fecha.sub("", texto):
			return salida, len(entradas), problemas
	catalogo_po.escribir_texto_atomico(salida, texto)
	return salida, len(entradas), problemas


def main(argumentos: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Extrae las cadenas traducibles y genera la plantilla .pot.")
	analizador.add_argument("--raiz", type=Path, default=RAIZ, help="Raíz del proyecto (por defecto, la carpeta superior a tools).")
	analizador.add_argument("--dominio", help="Dominio gettext (por defecto, DOMINIO de app/core/i18n.py).")
	analizador.add_argument("--salida", type=Path, help="Ruta del .pot (por defecto, data/locales/<dominio>.pot).")
	opciones = analizador.parse_args(argumentos)
	catalogo_po.preparar_consola()
	raiz = opciones.raiz.resolve()
	ruta, total, problemas = generar_pot(raiz, opciones.salida, opciones.dominio)
	errores = [p for p in problemas if p.grave]
	avisos = [p for p in problemas if not p.grave]
	print(f"Plantilla generada: {ruta}")
	print(f"Mensajes: {total}. Errores: {len(errores)}. Avisos: {len(avisos)}.")
	for problema in errores + avisos:
		print(f"- {problema}")
	if errores:
		print("Corrija los errores: esas cadenas no se pueden traducir.")
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
