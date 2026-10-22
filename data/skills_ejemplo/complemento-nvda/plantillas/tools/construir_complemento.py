"""Construye el complemento de NVDA (.nvda-addon) desde la carpeta fuente.

Un ``.nvda-addon`` es un ZIP con ``manifest.ini`` en la raíz y las carpetas del
complemento (``appModules``, ``globalPlugins``, ``locale``, ``doc``...). Este
script:

1. Sustituye en ``nvda_addon/manifest.ini`` (y en los manifiestos traducidos
   ``addon/locale/<idioma>/manifest.ini``) los marcadores ``@NOMBRE@``,
   ``@VERSION@``, ``@NVDA_MINIMA@``, ``@NVDA_PROBADA@``, ``@AUTOR@``, ``@URL@`` y
   ``@CAMBIOS@``. Si un marcador opcional queda vacío, quita la línea.
2. Valida el manifiesto con las reglas de NVDA y de la tienda de complementos.
3. Comprueba que cada ``.py`` compila (sin generar ``.pyc``).
4. Compila cada ``nvda.po`` a ``nvda.mo`` con un compilador propio, sin
   dependencias (el ``.po`` no viaja en el paquete).
5. Convierte ``addon/doc/<idioma>/readme.md`` en ``readme.html``: con el paquete
   ``markdown`` si está instalado y, si no, con un conversor mínimo propio.
6. Empaqueta en ``data/bin/<nombre>.nvda-addon`` sin ``__pycache__``, ``.pyc``
   ni restos de editores, y vuelve a validar el ZIP resultante.

La versión, si no se indica, es ``APP_VERSION`` de ``app/core/version.py``: el
complemento y la aplicación se publican juntos.

Uso::

	python tools/construir_complemento.py [--version 1.2.0] [--nvda-minima 2026.1]
		[--nvda-probada 2026.2] [--nombre miAplicacionNVDA] [--destino data/bin]

También se puede importar y llamar a ``construir()``. Nunca instala nada: solo
genera el archivo.

PLANTILLA del agente «complemento-nvda».
"""

from __future__ import annotations

import argparse
import ast
import html
import os
import re
import struct
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# ── CONFIGURACIÓN ────────────────────────────────────────────────────────────
ORIGEN = RAIZ / "nvda_addon"
DESTINO = RAIZ / "data" / "bin"
NOMBRE = "miAplicacionNVDA"  # letras, números, «_» y «-»; único en la tienda
AUTOR = "Tu nombre <correo@ejemplo.es>"
URL = ""  # https://... o vacío (se quita la línea)
CAMBIOS = ""  # novedades de esta versión o vacío
VERSION_POR_DEFECTO = "1.0.0"  # solo si no hay app/core/version.py
NVDA_MINIMA = "2026.1"  # primera versión de NVDA de 64 bits y Python 3.13
NVDA_PROBADA = "2026.2"  # la última con la que se ha probado DE VERDAD
# ─────────────────────────────────────────────────────────────────────────────

#: Campos que NVDA exige (string() sin valor por defecto) y los de versión, que
#: la tienda exige y que sin ellos NVDA toma como 0.0.0 (incompatible).
CAMPOS_OBLIGATORIOS = ("name", "summary", "author", "version", "minimumNVDAVersion", "lastTestedNVDAVersion")
#: Campos que conoce NVDA 2026 más updateChannel, que usa la plantilla oficial.
CAMPOS_CONOCIDOS = set(CAMPOS_OBLIGATORIOS) | {"description", "changelog", "url", "docFileName", "updateChannel"}
#: Marcadores que pueden quedar vacíos (se elimina la línea).
MARCADORES_OPCIONALES = {"URL", "CAMBIOS"}

PATRON_NOMBRE = re.compile(r"^[A-Za-z0-9_-]+$")
PATRON_VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
#: La misma expresión que usa NVDA (addonAPIVersion.ADDON_API_VERSION_REGEX).
PATRON_VERSION_NVDA = re.compile(r"^(0|\d{4})\.(\d)(?:\.(\d))?$")
PATRON_MARCADOR = re.compile(r"@([A-Z_]+)@")

CARPETAS_EXCLUIDAS = {"__pycache__", ".git", ".svn", ".vscode", ".idea", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
SUFIJOS_EXCLUIDOS = {".pyc", ".pyo", ".po", ".pot", ".bak", ".tmp", ".orig", ".swp"}
ARCHIVOS_EXCLUIDOS = {"Thumbs.db", "desktop.ini", ".DS_Store"}


class ErrorComplemento(Exception):
	"""Error de construcción con un mensaje en español para la persona."""


@dataclass
class Datos:
	"""Valores que se sustituyen en los manifiestos."""

	nombre: str = NOMBRE
	version: str = ""
	nvda_minima: str = NVDA_MINIMA
	nvda_probada: str = NVDA_PROBADA
	autor: str = AUTOR
	url: str = URL
	cambios: str = CAMBIOS

	def marcadores(self) -> dict[str, str]:
		return {
			"NOMBRE": self.nombre,
			"VERSION": self.version or leer_version_app(),
			"NVDA_MINIMA": self.nvda_minima,
			"NVDA_PROBADA": self.nvda_probada,
			"AUTOR": self.autor,
			"URL": self.url,
			"CAMBIOS": self.cambios,
		}


@dataclass
class Informe:
	"""Resultado de una construcción: ruta, archivos incluidos y avisos."""

	ruta: Path
	archivos: list[str] = field(default_factory=list)
	avisos: list[str] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════
# Versión de la aplicación
# ════════════════════════════════════════════════════════════════════════════

def leer_version_app(raiz: Path = RAIZ) -> str:
	"""Lee APP_VERSION de app/core/version.py sin importar la aplicación."""
	archivo = raiz / "app" / "core" / "version.py"
	try:
		texto = archivo.read_text(encoding="utf-8")
	except OSError:
		return VERSION_POR_DEFECTO
	coincidencia = re.search(r"APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", texto)
	return coincidencia.group(1) if coincidencia else VERSION_POR_DEFECTO


# ════════════════════════════════════════════════════════════════════════════
# Manifiesto
# ════════════════════════════════════════════════════════════════════════════

def sustituir_marcadores(texto: str, valores: dict[str, str]) -> str:
	"""Sustituye @MARCADOR@, quita los comentarios y las líneas con un marcador opcional vacío.

	Lanza ErrorComplemento si queda un marcador desconocido o uno obligatorio
	vacío: un manifiesto con «@VERSION@» literal no debe llegar a NVDA.
	"""
	lineas = []
	dentro_de_triples = False
	for linea in texto.splitlines():
		if not dentro_de_triples and linea.lstrip().startswith("#"):
			continue  # los comentarios de la plantilla no viajan en el paquete
		if linea.count('"""') % 2 == 1:
			dentro_de_triples = not dentro_de_triples
		nombres = PATRON_MARCADOR.findall(linea)
		if any(n in MARCADORES_OPCIONALES and not valores.get(n, "").strip() for n in nombres):
			continue
		for nombre in nombres:
			if nombre not in valores:
				raise ErrorComplemento(f"Marcador desconocido en el manifiesto: @{nombre}@")
			if not valores[nombre].strip():
				raise ErrorComplemento(f"El marcador @{nombre}@ es obligatorio y está vacío.")
			valor = valores[nombre]
			# CAMBIOS va entre comillas triples y puede tener varias líneas; el
			# resto va en una sola línea y a veces entre comillas dobles.
			if nombre != "CAMBIOS" and ('"' in valor or "\n" in valor):
				raise ErrorComplemento(f"El valor de @{nombre}@ no puede llevar comillas dobles ni saltos de línea.")
			if '"""' in valor:
				raise ErrorComplemento(f"El valor de @{nombre}@ no puede llevar comillas triples.")
			linea = linea.replace(f"@{nombre}@", valor)
		lineas.append(linea)
	return "\n".join(lineas) + "\n"


def _quitar_comillas(valor: str) -> str:
	"""Quita comillas simples o dobles y el comentario final de un valor sin comillas."""
	valor = valor.strip()
	if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
		return valor[1:-1]
	return valor.split("#", 1)[0].strip()


def leer_manifiesto(texto: str) -> dict[str, str]:
	"""Lee los campos de primer nivel de un manifest.ini (subconjunto de configobj).

	Admite comentarios, valores entre comillas simples o dobles, sin comillas y
	entre comillas triples en varias líneas. Se detiene en la primera sección
	``[...]`` (tablas braille, diccionarios), que aquí no se validan. Lanza
	ErrorComplemento ante líneas que configobj rechazaría o claves repetidas.
	"""
	campos: dict[str, str] = {}
	lineas = texto.splitlines()
	i = 0
	while i < len(lineas):
		linea = lineas[i].strip()
		i += 1
		if not linea or linea.startswith("#"):
			continue
		if linea.startswith("["):
			break
		if "=" not in linea:
			raise ErrorComplemento(f"Línea {i} del manifiesto sin «clave = valor»: {linea}")
		clave, valor = (parte.strip() for parte in linea.split("=", 1))
		if clave in campos:
			raise ErrorComplemento(f"Clave repetida en el manifiesto: {clave}")
		for triple in ('"""', "'''"):
			if valor.startswith(triple):
				resto = valor[3:]
				partes = [resto]
				while triple not in partes[-1]:
					if i >= len(lineas):
						raise ErrorComplemento(f"Comillas triples sin cerrar en «{clave}».")
					partes.append(lineas[i])
					i += 1
				completo = "\n".join(partes)
				valor = completo[: completo.index(triple)]
				break
		else:
			valor = _quitar_comillas(valor)
		campos[clave] = valor
	return campos


def _tupla_nvda(version: str) -> tuple[int, int, int]:
	coincidencia = PATRON_VERSION_NVDA.match(version)
	assert coincidencia is not None
	anio, mayor, menor = coincidencia.groups()
	return int(anio), int(mayor), int(menor or 0)


def validar_manifiesto(campos: dict[str, str], traducido: bool = False) -> list[str]:
	"""Devuelve avisos y lanza ErrorComplemento con todos los errores encontrados.

	Reglas de NVDA (addonHandler.AddonManifest) y de la tienda de complementos
	(nvaccess/addon-datastore): nombre con letras, números, «_» y «-»; versión
	``mayor.menor`` o ``mayor.menor.parche``; versiones de NVDA ``año.mayor`` o
	``año.mayor.menor``; mínima ≤ probada; URL con https. Un manifiesto
	traducido solo puede traer summary, description y changelog.
	"""
	errores: list[str] = []
	avisos: list[str] = []
	if traducido:
		sobrantes = set(campos) - {"summary", "description", "changelog"}
		if sobrantes:
			avisos.append(f"El manifiesto traducido trae campos que NVDA ignora: {', '.join(sorted(sobrantes))}")
		if not campos.get("summary", "").strip():
			errores.append("El manifiesto traducido no tiene «summary».")
	else:
		for clave in CAMPOS_OBLIGATORIOS:
			if not campos.get(clave, "").strip():
				errores.append(f"Falta el campo obligatorio «{clave}».")
		nombre = campos.get("name", "")
		if nombre and not PATRON_NOMBRE.match(nombre):
			errores.append(f"«name» ({nombre}) solo admite letras, números, «_» y «-».")
		version = campos.get("version", "")
		if version and not PATRON_VERSION.match(version):
			errores.append(f"«version» ({version}) debe ser mayor.menor o mayor.menor.parche (por ejemplo 1.2.0).")
		for clave in ("minimumNVDAVersion", "lastTestedNVDAVersion"):
			valor = campos.get(clave, "")
			if valor and not PATRON_VERSION_NVDA.match(valor):
				errores.append(
					f"«{clave}» ({valor}) debe ser año.mayor o año.mayor.menor (por ejemplo 2026.1 o 2026.2.0)."
				)
		minima, probada = campos.get("minimumNVDAVersion", ""), campos.get("lastTestedNVDAVersion", "")
		if PATRON_VERSION_NVDA.match(minima) and PATRON_VERSION_NVDA.match(probada):
			if _tupla_nvda(minima) > _tupla_nvda(probada):
				errores.append(
					f"minimumNVDAVersion ({minima}) no puede ser mayor que lastTestedNVDAVersion ({probada})."
				)
		desconocidos = set(campos) - CAMPOS_CONOCIDOS
		if desconocidos:
			avisos.append(f"Campos que NVDA no conoce: {', '.join(sorted(desconocidos))}")
		if "description" not in campos:
			avisos.append("Sin «description»: la tienda y el gestor de complementos no explicarán qué hace.")
	url = campos.get("url", "")
	if url and not url.startswith("https://"):
		errores.append(f"«url» ({url}) debe empezar por https:// (lo exige la tienda de complementos).")
	resto = [clave for clave, valor in campos.items() if PATRON_MARCADOR.search(valor)]
	if resto:
		errores.append(f"Quedan marcadores sin sustituir en: {', '.join(resto)}")
	if errores:
		raise ErrorComplemento("Manifiesto no válido:\n- " + "\n- ".join(errores))
	return avisos


# ════════════════════════════════════════════════════════════════════════════
# Traducciones: .po → .mo sin dependencias
# ════════════════════════════════════════════════════════════════════════════

def _cadena_po(texto: str, archivo: Path, numero: int) -> str:
	"""Convierte una cadena entre comillas de un .po («\"Hola\\n\"») en texto."""
	try:
		valor = ast.literal_eval(texto)
	except (SyntaxError, ValueError) as error:
		raise ErrorComplemento(f"{archivo.name}:{numero}: cadena mal formada: {texto}") from error
	if not isinstance(valor, str):
		raise ErrorComplemento(f"{archivo.name}:{numero}: se esperaba una cadena entre comillas.")
	return valor


def leer_po(archivo: Path) -> dict[str, str]:
	"""Lee un .po y devuelve {clave: traducción} listo para el .mo.

	Sigue el criterio de msgfmt: omite las entradas sin traducir y las marcadas
	«fuzzy» (salvo la cabecera), une contexto y msgid con «\\x04» y las formas
	de plural con «\\x00». Solo admite UTF-8, que es lo que espera NVDA.
	"""
	entradas: list[dict] = []
	actual: dict = {}
	campo = None
	difusa = False

	def completa() -> bool:
		return any(clave.startswith("msgstr") for clave in actual)

	def cerrar():
		# Las marcas «#, fuzzy» van ANTES de su entrada: por eso se cierra la
		# entrada anterior al ver un comentario o un msgid nuevo.
		nonlocal actual, difusa
		if "msgid" in actual:
			actual["fuzzy"] = difusa
			entradas.append(actual)
		actual, difusa = {}, False

	for numero, bruta in enumerate(archivo.read_text(encoding="utf-8-sig").splitlines(), start=1):
		linea = bruta.strip()
		if not linea:
			continue
		if linea.startswith("#"):
			if completa():
				cerrar()
				campo = None
			if linea.startswith("#,") and "fuzzy" in linea:
				difusa = True
			continue
		if linea.startswith('"'):
			if campo is None:
				raise ErrorComplemento(f"{archivo.name}:{numero}: continuación sin campo previo.")
			actual[campo] += _cadena_po(linea, archivo, numero)
			continue
		clave, _separador, resto = linea.partition(" ")
		if clave not in ("msgctxt", "msgid", "msgid_plural", "msgstr") and not re.match(r"^msgstr\[\d+\]$", clave):
			raise ErrorComplemento(f"{archivo.name}:{numero}: palabra clave desconocida: {clave}")
		if clave in ("msgctxt", "msgid") and completa():
			cerrar()
		campo = clave
		actual[campo] = _cadena_po(resto.strip(), archivo, numero)
	cerrar()

	catalogo: dict[str, str] = {}
	for entrada in entradas:
		msgid = entrada["msgid"]
		if entrada["fuzzy"] and msgid:
			continue
		if "msgid_plural" in entrada:
			indices = sorted(int(k[7:-1]) for k in entrada if k.startswith("msgstr["))
			formas = [entrada[f"msgstr[{i}]"] for i in indices]
			if not any(formas):
				continue
			clave = msgid + "\x00" + entrada["msgid_plural"]
			valor = "\x00".join(formas)
		else:
			valor = entrada.get("msgstr", "")
			if not valor:
				continue
			clave = msgid
		if "msgctxt" in entrada:
			clave = entrada["msgctxt"] + "\x04" + clave
		catalogo[clave] = valor
	cabecera = catalogo.get("", "")
	juego = re.search(r"charset=([\w-]+)", cabecera)
	if juego and juego.group(1).lower().replace("_", "-") not in ("utf-8", "utf8"):
		raise ErrorComplemento(f"{archivo.name}: el juego de caracteres debe ser UTF-8 (es {juego.group(1)}).")
	return catalogo


def compilar_mo(catalogo: dict[str, str]) -> bytes:
	"""Genera un .mo (formato GNU gettext, little-endian) a partir del catálogo."""
	claves = sorted(catalogo)
	ids = b""
	textos = b""
	desplazamientos = []
	for clave in claves:
		id_bytes = clave.encode("utf-8")
		texto_bytes = catalogo[clave].encode("utf-8")
		desplazamientos.append((len(ids), len(id_bytes), len(textos), len(texto_bytes)))
		ids += id_bytes + b"\x00"
		textos += texto_bytes + b"\x00"
	cantidad = len(claves)
	inicio_ids = 7 * 4 + cantidad * 8 * 2
	inicio_textos = inicio_ids + len(ids)
	tabla_ids = []
	tabla_textos = []
	for pos_id, largo_id, pos_texto, largo_texto in desplazamientos:
		tabla_ids += [largo_id, pos_id + inicio_ids]
		tabla_textos += [largo_texto, pos_texto + inicio_textos]
	cabecera = struct.pack(
		"<7I",
		0x950412DE,  # número mágico
		0,  # revisión del formato
		cantidad,
		7 * 4,  # tabla de originales
		7 * 4 + cantidad * 8,  # tabla de traducciones
		0,  # sin tabla hash (gettext no la necesita)
		0,
	)
	return (
		cabecera
		+ struct.pack(f"<{len(tabla_ids)}I", *tabla_ids)
		+ struct.pack(f"<{len(tabla_textos)}I", *tabla_textos)
		+ ids
		+ textos
	)


# ════════════════════════════════════════════════════════════════════════════
# Documentación: readme.md → readme.html
# ════════════════════════════════════════════════════════════════════════════

def _en_linea(texto: str) -> str:
	"""Escapa y aplica **negrita**, *cursiva* y `código` del Markdown básico."""
	texto = html.escape(texto, quote=False)
	texto = re.sub(r"`([^`]+)`", r"<code>\1</code>", texto)
	texto = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", texto)
	texto = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", texto)
	return texto


def markdown_minimo(texto: str) -> str:
	"""Conversor mínimo para cuando no está el paquete ``markdown``.

	Cubre lo que usa un readme de complemento: encabezados (#), listas con
	guion, asterisco o número, y párrafos. Mantiene la estructura de
	encabezados y listas, que es lo que usa un lector de pantalla para navegar.
	"""
	salida: list[str] = []
	parrafo: list[str] = []
	lista: str | None = None

	def cerrar_parrafo():
		if parrafo:
			salida.append("<p>" + _en_linea(" ".join(parrafo)) + "</p>")
			parrafo.clear()

	def cerrar_lista():
		nonlocal lista
		if lista:
			salida.append(f"</{lista}>")
			lista = None

	for bruta in texto.splitlines():
		linea = bruta.rstrip()
		encabezado = re.match(r"^(#{1,6})\s+(.*)$", linea)
		punto = re.match(r"^\s*[-*+]\s+(.*)$", linea)
		numero = re.match(r"^\s*\d+[.)]\s+(.*)$", linea)
		if not linea.strip():
			cerrar_parrafo()
			cerrar_lista()
		elif encabezado:
			cerrar_parrafo()
			cerrar_lista()
			nivel = len(encabezado.group(1))
			salida.append(f"<h{nivel}>{_en_linea(encabezado.group(2).strip())}</h{nivel}>")
		elif punto or numero:
			cerrar_parrafo()
			tipo = "ul" if punto else "ol"
			if lista != tipo:
				cerrar_lista()
				salida.append(f"<{tipo}>")
				lista = tipo
			salida.append("<li>" + _en_linea((punto or numero).group(1)) + "</li>")
		else:
			cerrar_lista()
			parrafo.append(linea.strip())
	cerrar_parrafo()
	cerrar_lista()
	return "\n".join(salida)


def convertir_documentacion(texto_md: str, idioma: str, titulo: str) -> str:
	"""Devuelve una página HTML completa a partir del Markdown del readme."""
	try:
		import markdown  # opcional: mejor conversión si está instalado
	except ImportError:
		cuerpo = markdown_minimo(texto_md)
	else:
		cuerpo = markdown.markdown(texto_md, extensions=["extra"])
	return (
		"<!DOCTYPE html>\n"
		f'<html lang="{html.escape(idioma)}">\n<head>\n<meta charset="utf-8">\n'
		f"<title>{html.escape(titulo)}</title>\n</head>\n<body>\n{cuerpo}\n</body>\n</html>\n"
	)


# ════════════════════════════════════════════════════════════════════════════
# Construcción
# ════════════════════════════════════════════════════════════════════════════

def _excluido(relativo: Path) -> bool:
	if any(parte in CARPETAS_EXCLUIDAS for parte in relativo.parts):
		return True
	nombre = relativo.name
	return relativo.suffix.lower() in SUFIJOS_EXCLUIDOS or nombre in ARCHIVOS_EXCLUIDOS or nombre.endswith("~")


def _compila(ruta: Path) -> None:
	"""Comprueba la sintaxis del módulo sin escribir .pyc en la carpeta fuente."""
	try:
		compile(ruta.read_text(encoding="utf-8"), str(ruta), "exec", dont_inherit=True)
	except SyntaxError as error:
		raise ErrorComplemento(f"{ruta.name}:{error.lineno}: error de sintaxis: {error.msg}") from error


def generar_contenido(origen: Path, datos: Datos) -> tuple[dict[str, bytes], list[str]]:
	"""Calcula {ruta dentro del ZIP: bytes} y los avisos, sin escribir nada."""
	valores = datos.marcadores()
	archivo_manifiesto = origen / "manifest.ini"
	carpeta = origen / "addon"
	if not archivo_manifiesto.is_file():
		raise ErrorComplemento(f"No existe {archivo_manifiesto}.")
	if not carpeta.is_dir():
		raise ErrorComplemento(f"No existe la carpeta del complemento {carpeta}.")

	contenido: dict[str, bytes] = {}
	manifiesto = sustituir_marcadores(archivo_manifiesto.read_text(encoding="utf-8-sig"), valores)
	campos = leer_manifiesto(manifiesto)
	avisos = validar_manifiesto(campos)
	contenido["manifest.ini"] = manifiesto.encode("utf-8")

	for ruta in sorted(carpeta.rglob("*")):
		relativo = ruta.relative_to(carpeta)
		if not ruta.is_file() or _excluido(relativo):
			continue
		interno = relativo.as_posix()
		partes = relativo.parts
		if ruta.suffix == ".py":
			_compila(ruta)
		if interno == "manifest.ini":
			raise ErrorComplemento("El manifest.ini raíz va en nvda_addon/, no dentro de addon/.")
		if len(partes) == 3 and partes[0] == "locale" and ruta.name == "manifest.ini":
			traducido = sustituir_marcadores(ruta.read_text(encoding="utf-8-sig"), valores)
			avisos += validar_manifiesto(leer_manifiesto(traducido), traducido=True)
			contenido[interno] = traducido.encode("utf-8")
		elif len(partes) == 3 and partes[0] == "doc" and ruta.suffix.lower() == ".md":
			html_doc = convertir_documentacion(ruta.read_text(encoding="utf-8-sig"), partes[1], campos["summary"])
			contenido[relativo.with_suffix(".html").as_posix()] = html_doc.encode("utf-8")
		else:
			contenido[interno] = ruta.read_bytes()

	for po in sorted(carpeta.glob("locale/*/LC_MESSAGES/*.po")):
		mo = po.relative_to(carpeta).with_suffix(".mo").as_posix()
		contenido[mo] = compilar_mo(leer_po(po))

	documento = campos.get("docFileName", "")
	if documento and not any(re.fullmatch(rf"doc/[^/]+/{re.escape(documento)}", k) for k in contenido):
		avisos.append(f"docFileName = {documento}, pero no hay doc/<idioma>/{documento} en el paquete.")
	carpetas_de_codigo = ("appModules/", "globalPlugins/", "synthDrivers/", "brailleDisplayDrivers/")
	if not any(k.startswith(carpetas_de_codigo) for k in contenido):
		avisos.append("El paquete no contiene appModules, globalPlugins ni controladores.")
	return contenido, avisos


def verificar_paquete(ruta: Path) -> list[str]:
	"""Abre el .nvda-addon generado y comprueba lo esencial. Devuelve los nombres."""
	with zipfile.ZipFile(ruta) as paquete:
		nombres = paquete.namelist()
		if "manifest.ini" not in nombres:
			raise ErrorComplemento("El paquete no tiene manifest.ini en la raíz.")
		validar_manifiesto(leer_manifiesto(paquete.read("manifest.ini").decode("utf-8")))
		prohibidos = [n for n in nombres if _excluido(Path(n))]
		if prohibidos:
			raise ErrorComplemento(f"El paquete contiene archivos excluidos: {', '.join(prohibidos)}")
		if paquete.testzip() is not None:
			raise ErrorComplemento("El ZIP generado está dañado.")
	return nombres


def construir(origen: Path = ORIGEN, destino: Path = DESTINO, datos: Datos | None = None) -> Informe:
	"""Genera ``<destino>/<nombre>.nvda-addon`` y devuelve el informe.

	Escribe primero un temporal en la misma carpeta y lo renombra al final: si
	la aplicación ofrece el archivo mientras se construye, nunca verá un ZIP a
	medias.
	"""
	datos = datos or Datos()
	contenido, avisos = generar_contenido(Path(origen), datos)
	destino = Path(destino)
	destino.mkdir(parents=True, exist_ok=True)
	nombre = leer_manifiesto(contenido["manifest.ini"].decode("utf-8"))["name"]
	ruta = destino / f"{nombre}.nvda-addon"
	descriptor, temporal = tempfile.mkstemp(prefix=f".{nombre}-", suffix=".tmp", dir=destino)
	os.close(descriptor)
	try:
		with zipfile.ZipFile(temporal, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as paquete:
			for interno in sorted(contenido):
				paquete.writestr(interno, contenido[interno])
		verificar_paquete(Path(temporal))
		os.replace(temporal, ruta)
	finally:
		if os.path.exists(temporal):
			os.remove(temporal)
	return Informe(ruta=ruta, archivos=sorted(contenido), avisos=avisos)


def main(argv: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Construye el complemento de NVDA (.nvda-addon).")
	analizador.add_argument("--origen", type=Path, default=ORIGEN, help="Carpeta fuente (con manifest.ini y addon/).")
	analizador.add_argument("--destino", type=Path, default=DESTINO, help="Carpeta de salida (por defecto data/bin).")
	analizador.add_argument("--nombre", default=NOMBRE)
	analizador.add_argument("--version", default="", help="Versión del complemento (por defecto, la de la app).")
	analizador.add_argument("--nvda-minima", default=NVDA_MINIMA)
	analizador.add_argument("--nvda-probada", default=NVDA_PROBADA)
	analizador.add_argument("--autor", default=AUTOR)
	analizador.add_argument("--url", default=URL)
	analizador.add_argument("--cambios", default=CAMBIOS)
	argumentos = analizador.parse_args(argv)
	datos = Datos(
		nombre=argumentos.nombre,
		version=argumentos.version,
		nvda_minima=argumentos.nvda_minima,
		nvda_probada=argumentos.nvda_probada,
		autor=argumentos.autor,
		url=argumentos.url,
		cambios=argumentos.cambios,
	)
	try:
		informe = construir(argumentos.origen, argumentos.destino, datos)
	except ErrorComplemento as error:
		print(f"ERROR: {error}", file=sys.stderr)
		return 1
	print(f"Complemento generado: {informe.ruta} ({len(informe.archivos)} archivos)")
	for aviso in informe.avisos:
		print(f"AVISO: {aviso}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
