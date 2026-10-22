"""Lectura y escritura de catálogos gettext (.po y .pot) sin dependencias.

Lo usan ``extraer_cadenas.py``, ``actualizar_po.py`` y
``compilar_traducciones.py``. Solo biblioteca estándar: así las herramientas
funcionan en cualquier entorno virtual del proyecto sin instalar ``polib`` ni
``Babel``, y también en la máquina de quien traduce.

Formato de referencia: manual de GNU gettext, «The Format of PO Files»
(https://www.gnu.org/software/gettext/manual/html_node/PO-Files.html).

PLANTILLA del agente «traducciones».
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Separador que gettext usa entre contexto y mensaje dentro del .mo.
SEPARADOR_CONTEXTO = "\x04"

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
_RE_ESCAPE = re.compile(r"\\(.)")
_RE_CHARSET = re.compile(rb"charset=([A-Za-z0-9_.:-]+)")


class ErrorPO(ValueError):
	"""Un .po mal formado. Lleva el archivo y la línea para poder corregirlo."""


@dataclass
class Entrada:
	"""Una entrada de un catálogo: mensaje original, traducción y comentarios."""

	msgid: str
	msgstr: str = ""
	msgctxt: str | None = None
	msgid_plural: str | None = None
	# Formas plurales traducidas: índice → texto (msgstr[0], msgstr[1]...).
	msgstr_plural: dict[int, str] = field(default_factory=dict)
	comentarios_traductor: list[str] = field(default_factory=list)  # «# ...»
	comentarios_extraidos: list[str] = field(default_factory=list)  # «#. ...»
	referencias: list[str] = field(default_factory=list)  # «#: archivo:línea»
	banderas: list[str] = field(default_factory=list)  # «#, fuzzy, python-format»
	previos: list[str] = field(default_factory=list)  # «#| msgid ...» (texto anterior)
	obsoleta: bool = False
	linea: int = 0

	@property
	def clave(self) -> tuple[str | None, str]:
		"""Identidad de la entrada: el mismo texto con otro contexto es otra entrada."""
		return (self.msgctxt, self.msgid)

	@property
	def es_plural(self) -> bool:
		return self.msgid_plural is not None

	@property
	def aproximada(self) -> bool:
		"""«fuzzy»: traducción dudosa que NO se compila hasta que alguien la revise."""
		return "fuzzy" in self.banderas

	@property
	def traducida(self) -> bool:
		if self.es_plural:
			return bool(self.msgstr_plural) and all(self.msgstr_plural.values())
		return bool(self.msgstr)

	def marcar_aproximada(self, activar: bool = True) -> None:
		if activar and "fuzzy" not in self.banderas:
			self.banderas.insert(0, "fuzzy")
		elif not activar and "fuzzy" in self.banderas:
			self.banderas.remove("fuzzy")


@dataclass
class Catalogo:
	"""Un archivo .po o .pot completo."""

	cabecera: Entrada | None = None
	entradas: list[Entrada] = field(default_factory=list)

	def metadatos(self) -> dict[str, str]:
		"""Campos de la cabecera («Language», «Plural-Forms»...)."""
		datos: dict[str, str] = {}
		if self.cabecera is None:
			return datos
		for linea in self.cabecera.msgstr.splitlines():
			if ":" in linea:
				nombre, valor = linea.split(":", 1)
				datos[nombre.strip()] = valor.strip()
		return datos

	def fijar_metadato(self, nombre: str, valor: str) -> None:
		"""Cambia o añade un campo de la cabecera conservando el orden de los demás."""
		if self.cabecera is None:
			self.cabecera = Entrada(msgid="")
		lineas = [linea for linea in self.cabecera.msgstr.split("\n") if linea]
		for indice, linea in enumerate(lineas):
			if linea.split(":", 1)[0].strip() == nombre:
				lineas[indice] = f"{nombre}: {valor}"
				break
		else:
			lineas.append(f"{nombre}: {valor}")
		self.cabecera.msgstr = "\n".join(lineas) + "\n"

	def por_clave(self) -> dict[tuple[str | None, str], Entrada]:
		return {entrada.clave: entrada for entrada in self.entradas}


# ── Lectura ──────────────────────────────────────────────────────────────────

def _desescapar(texto: str, archivo: str, linea: int) -> str:
	def sustituir(coincidencia: re.Match) -> str:
		letra = coincidencia.group(1)
		if letra not in _ESCAPES:
			raise ErrorPO(f"{archivo}:{linea}: secuencia de escape desconocida «\\{letra}».")
		return _ESCAPES[letra]

	return _RE_ESCAPE.sub(sustituir, texto)


def _cadena(resto: str, archivo: str, linea: int) -> str:
	"""Interpreta ``"texto"`` (con las comillas) de una línea del .po."""
	resto = resto.strip()
	if len(resto) < 2 or not (resto.startswith('"') and resto.endswith('"')):
		raise ErrorPO(f"{archivo}:{linea}: se esperaba una cadena entre comillas y hay «{resto}».")
	return _desescapar(resto[1:-1], archivo, linea)


def detectar_codificacion(datos: bytes) -> str:
	"""Codificación declarada en la cabecera (``charset=``); UTF-8 si no hay."""
	coincidencia = _RE_CHARSET.search(datos[:8192])
	if coincidencia:
		nombre = coincidencia.group(1).decode("ascii", "replace")
		if nombre.upper() not in ("CHARSET", ""):
			return nombre
	return "utf-8"


def leer_po(ruta: Path | str) -> Catalogo:
	"""Lee un .po o .pot. Lanza :class:`ErrorPO` con archivo:línea si está mal formado."""
	ruta = Path(ruta)
	datos = ruta.read_bytes()
	if datos.startswith(b"\xef\xbb\xbf"):
		datos = datos[3:]
	texto = datos.decode(detectar_codificacion(datos))
	return leer_po_texto(texto, str(ruta))


def leer_po_texto(texto: str, archivo: str = "<texto>") -> Catalogo:
	"""Interpreta el contenido de un .po (útil en pruebas)."""
	catalogo = Catalogo()
	actual: Entrada | None = None
	# Campo al que se añaden las líneas de continuación ("...").
	campo: str | None = None
	indice_plural = 0
	comentarios_pendientes: dict[str, list[str]] = {"#": [], "#.": [], "#:": [], "#,": [], "#|": []}

	def nueva(numero: int, obsoleta: bool) -> Entrada:
		nonlocal comentarios_pendientes
		entrada = Entrada(msgid="", linea=numero, obsoleta=obsoleta)
		entrada.comentarios_traductor = comentarios_pendientes["#"]
		entrada.comentarios_extraidos = comentarios_pendientes["#."]
		entrada.referencias = [ref for linea in comentarios_pendientes["#:"] for ref in linea.split()]
		entrada.banderas = [b.strip() for linea in comentarios_pendientes["#,"] for b in linea.split(",") if b.strip()]
		entrada.previos = comentarios_pendientes["#|"]
		comentarios_pendientes = {"#": [], "#.": [], "#:": [], "#,": [], "#|": []}
		return entrada

	def cerrar() -> None:
		nonlocal actual
		if actual is None:
			return
		if actual.msgid == "" and actual.msgctxt is None and not actual.obsoleta and catalogo.cabecera is None:
			catalogo.cabecera = actual
		else:
			catalogo.entradas.append(actual)
		actual = None

	for numero, bruta in enumerate(texto.splitlines(), start=1):
		linea = bruta.strip()
		obsoleta = False
		if linea.startswith("#~"):
			obsoleta = True
			linea = linea[2:].strip()
			if linea.startswith("|"):
				# «#~| msgid ...»: texto anterior de una obsoleta; no se necesita.
				continue
		if not linea:
			campo = None
			continue
		if linea.startswith("#"):
			if actual is not None and campo is not None:
				cerrar()
				campo = None
			for prefijo in ("#.", "#:", "#,", "#|"):
				if linea.startswith(prefijo):
					comentarios_pendientes[prefijo].append(linea[len(prefijo):].strip())
					break
			else:
				contenido = linea[1:]
				comentarios_pendientes["#"].append(contenido[1:] if contenido.startswith(" ") else contenido)
			continue
		if linea.startswith('"'):
			if actual is None or campo is None:
				raise ErrorPO(f"{archivo}:{numero}: cadena suelta sin msgid ni msgstr delante.")
			valor = _cadena(linea, archivo, numero)
			if campo == "msgstr_plural":
				actual.msgstr_plural[indice_plural] += valor
			else:
				setattr(actual, campo, getattr(actual, campo) + valor)
			continue
		palabra, _, resto = linea.partition(" ")
		if palabra == "msgctxt":
			cerrar()
			actual = nueva(numero, obsoleta)
			actual.msgctxt = _cadena(resto, archivo, numero)
			campo = "msgctxt"
		elif palabra == "msgid":
			if actual is None or campo != "msgctxt":
				cerrar()
				actual = nueva(numero, obsoleta)
			actual.msgid = _cadena(resto, archivo, numero)
			campo = "msgid"
		elif palabra == "msgid_plural":
			if actual is None:
				raise ErrorPO(f"{archivo}:{numero}: msgid_plural sin msgid.")
			actual.msgid_plural = _cadena(resto, archivo, numero)
			campo = "msgid_plural"
		elif palabra == "msgstr":
			if actual is None:
				raise ErrorPO(f"{archivo}:{numero}: msgstr sin msgid.")
			actual.msgstr = _cadena(resto, archivo, numero)
			campo = "msgstr"
		elif palabra.startswith("msgstr[") and palabra.endswith("]"):
			if actual is None:
				raise ErrorPO(f"{archivo}:{numero}: {palabra} sin msgid.")
			try:
				indice_plural = int(palabra[7:-1])
			except ValueError:
				raise ErrorPO(f"{archivo}:{numero}: índice de plural no válido «{palabra}».") from None
			actual.msgstr_plural[indice_plural] = _cadena(resto, archivo, numero)
			campo = "msgstr_plural"
		else:
			raise ErrorPO(f"{archivo}:{numero}: línea no reconocida «{linea}».")
	cerrar()
	return catalogo


# ── Escritura ────────────────────────────────────────────────────────────────

def escapar(texto: str) -> str:
	return (
		texto.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
	)


def _campo(nombre: str, valor: str, prefijo: str = "") -> list[str]:
	"""Formatea un campo. Si el texto tiene saltos de línea, uno por renglón (como xgettext)."""
	partes = valor.split("\n")
	if len(partes) > 2 or (len(partes) == 2 and partes[1]):
		trozos = [parte + "\n" for parte in partes[:-1]] + ([partes[-1]] if partes[-1] else [])
		return [f'{prefijo}{nombre} ""'] + [f'{prefijo}"{escapar(trozo)}"' for trozo in trozos]
	return [f'{prefijo}{nombre} "{escapar(valor)}"']


def formatear_entrada(entrada: Entrada) -> list[str]:
	"""Líneas de texto de una entrada (sin la línea en blanco final)."""
	lineas = [f"# {c}".rstrip() for c in entrada.comentarios_traductor]
	prefijo = "#~ " if entrada.obsoleta else ""
	if not entrada.obsoleta:
		lineas += [f"#. {c}" for c in entrada.comentarios_extraidos]
		if entrada.referencias:
			lineas.append("#: " + " ".join(entrada.referencias))
	if entrada.banderas:
		lineas.append("#, " + ", ".join(entrada.banderas))
	if not entrada.obsoleta:
		lineas += [f"#| {p}" for p in entrada.previos]
	if entrada.msgctxt is not None:
		lineas += _campo("msgctxt", entrada.msgctxt, prefijo)
	lineas += _campo("msgid", entrada.msgid, prefijo)
	if entrada.es_plural:
		lineas += _campo("msgid_plural", entrada.msgid_plural or "", prefijo)
		formas = entrada.msgstr_plural or {0: "", 1: ""}
		for indice in sorted(formas):
			lineas += _campo(f"msgstr[{indice}]", formas[indice], prefijo)
	else:
		lineas += _campo("msgstr", entrada.msgstr, prefijo)
	return lineas


def formatear_catalogo(catalogo: Catalogo) -> str:
	bloques: list[list[str]] = []
	if catalogo.cabecera is not None:
		bloques.append(formatear_entrada(catalogo.cabecera))
	activas = [e for e in catalogo.entradas if not e.obsoleta]
	obsoletas = [e for e in catalogo.entradas if e.obsoleta]
	bloques += [formatear_entrada(e) for e in activas + obsoletas]
	return "\n\n".join("\n".join(bloque) for bloque in bloques) + "\n"


def escribir_texto_atomico(ruta: Path, texto: str) -> None:
	"""Escribe en un temporal y lo renombra: un corte a medias no deja un .po roto."""
	ruta.parent.mkdir(parents=True, exist_ok=True)
	descriptor, temporal = tempfile.mkstemp(prefix=ruta.name + ".", suffix=".tmp", dir=ruta.parent)
	try:
		with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
			archivo.write(texto)
		os.replace(temporal, ruta)
	except BaseException:
		Path(temporal).unlink(missing_ok=True)
		raise


def escribir_po(catalogo: Catalogo, ruta: Path | str) -> None:
	"""Guarda el catálogo en UTF-8 con finales LF (lo que esperan Poedit y msgfmt)."""
	escribir_texto_atomico(Path(ruta), formatear_catalogo(catalogo))


_RE_DOMINIO = re.compile(r"^DOMINIO\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def leer_constante(archivo: Path, patron: re.Pattern, por_defecto: str) -> str:
	"""Lee una constante de un .py sin importarlo (importarlo arrastraría wx)."""
	try:
		coincidencia = patron.search(archivo.read_text(encoding="utf-8"))
	except OSError:
		return por_defecto
	return coincidencia.group(1) if coincidencia else por_defecto


def dominio_del_proyecto(raiz: Path) -> str:
	"""El dominio es único: se lee de ``DOMINIO`` en ``app/core/i18n.py``."""
	return leer_constante(raiz / "app" / "core" / "i18n.py", _RE_DOMINIO, "miaplicacion")


def preparar_consola() -> None:
	"""Evita que un carácter no representable en la consola corte el informe.

	Con la salida redirigida (un .bat que guarda el informe), Windows usa la
	página de códigos del sistema y un «…» o un emoji lanzaría
	UnicodeEncodeError a mitad del listado.
	"""
	for flujo in (sys.stdout, sys.stderr):
		reconfigurar = getattr(flujo, "reconfigure", None)
		if reconfigurar is not None:
			try:
				reconfigurar(errors="replace")
			except (ValueError, OSError):
				continue


# ── Marcadores de formato ────────────────────────────────────────────────────

_RE_LLAVES = re.compile(r"\{\{|\}\}|\{([^{}]*)\}")
_RE_PORCENTAJE = re.compile(r"%%|%(\([^)]*\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?([diouxXeEfFgGcrsa])")


def marcadores(texto: str) -> list[str]:
	"""Marcadores de formato de un texto, en orden: ``{nombre}``, ``{}``, ``%s``, ``%(n)d``...

	Se normalizan (``{n:>3}`` → ``{n}``) porque el traductor puede cambiar el
	formato de presentación pero no el nombre del dato.
	"""
	encontrados: list[str] = []
	for coincidencia in _RE_LLAVES.finditer(texto):
		if coincidencia.group(0) in ("{{", "}}"):
			continue
		nombre = re.split(r"[!:]", coincidencia.group(1), maxsplit=1)[0]
		encontrados.append("{" + nombre + "}")
	for coincidencia in _RE_PORCENTAJE.finditer(texto):
		if coincidencia.group(0) == "%%":
			continue
		nombre = coincidencia.group(1) or ""
		encontrados.append(f"%{nombre}{coincidencia.group(2)}")
	return encontrados


def banderas_de_formato(texto: str) -> list[str]:
	"""Banderas que Poedit usa para validar marcadores al traducir."""
	banderas = []
	if any(m.startswith("%") for m in marcadores(texto)):
		banderas.append("python-format")
	if any(m.startswith("{") for m in marcadores(texto)):
		banderas.append("python-brace-format")
	return banderas
