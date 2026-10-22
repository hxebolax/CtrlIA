"""Extrae de una conversación lo que el usuario suele querer recuperar.

Sin interfaz (no importa wx): recibe la lista de ``MensajeVista`` de una
sesión y devuelve listas sencillas para el diálogo «Listas de la sesión» y
para la ventana principal:

- ``bloques_codigo``: bloques Markdown delimitados con ```` ``` ```` (o ``~~~``),
  con su lenguaje si lo indican; también el último si quedó sin cerrar.
- ``enlaces``: direcciones http/https sueltas y enlaces ``[texto](url)``, sin
  duplicados y sin la puntuación final pegada; también las rutas de archivos
  locales (``C:\\...``, ``\\\\servidor\\...``, ``file:///...``, código en línea
  y destinos de enlaces Markdown), con su número de línea si lo traen.
- ``archivos_cambiados``: archivos que el agente ha creado o modificado, con
  su diff (si lo hay) acumulado. ``diff_legible`` escribe un diff con palabras
  («Línea 12, añadida: …») para oírlo con cualquier nivel de puntuación.
- ``ordenes`` y ``ordenes_detalladas``: órdenes de consola que ha ejecutado el
  agente, con su salida y si fallaron.
- ``tareas``: la lista de tareas del agente (TodoWrite, TaskCreate y
  TaskUpdate de Claude, el plan de Codex y de los agentes ACP) en su último
  estado.
- ``subagentes``: qué encargó el agente a cada subagente y qué le devolvió;
  ``completar_subagentes`` añade los pasos y las ediciones de sus transcripts.
- ``hallazgos``: los hallazgos de una revisión de código (ReportFindings).
- ``decisiones``: las preguntas que hizo el agente y los planes que propuso,
  con lo que respondiste (de lo que el gestor guarda en las sesiones propias).
- ``errores``: herramientas que fallaron (también las órdenes y las que
  rechazaste) y los avisos de error, con su detalle.
- ``commits``: órdenes ``git commit`` con su mensaje y su resultado, y los
  commits y subidas que aparecen en la salida de otras órdenes de git.
- ``incidencias``: incidencias y PR de GitHub citadas (enlaces ``/issues/N`` y
  ``/pull/N``, ``dueño/repo#N``, ``#N`` y órdenes ``gh issue``/``gh pr``).
- ``archivos_leidos``: archivos que leyó el agente (Read, read_file, Leer y
  órdenes como Get-Content, cat o sed -n).
- ``mensajes_usuario``: tus propios mensajes.
- ``rango_turno``: dónde empieza y acaba el turno de un mensaje (para ver solo
  lo de ese mensaje o turno).

En todos los casos ``indice`` es la posición del mensaje en la lista
recibida, para que la ventana principal pueda saltar a él. Las formas que no
se reconocen se saltan sin fallar.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from collections.abc import Iterable, Sequence
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from app.services.gestor import MensajeVista

log = logging.getLogger(__name__)

#: Roles en los que se buscan bloques de código.
ROLES_CON_CODIGO = frozenset({"asistente", "usuario"})

#: Roles en los que se buscan rutas de archivos locales (en los de
#: herramienta hay rutas a cada paso y no son algo que el agente te señale).
ROLES_CON_RUTAS = ROLES_CON_CODIGO

#: Prefijos de los mensajes de herramienta que ejecutan una orden de consola.
_PREFIJOS_ORDEN = ("Ejecutar orden", "Ejecutar:")

#: Prefijos de los mensajes de herramienta que crean o editan un archivo
#: (Claude en vivo dice «Editar archivo: ruta»; el historial, «Editar: ruta»).
_PREFIJOS_EDICION = ("Editar archivo:", "Escribir archivo:", "Editar:", "Escribir:", "Editar cuaderno:")
_HERRAMIENTAS_EDICION = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})

#: Herramientas que lanzan un subagente (Claude) o lo describen (Codex).
_HERRAMIENTAS_SUBAGENTE = frozenset({"Task", "Agent", "Subagente"})
#: Cómo empieza el resultado de un subagente lanzado en segundo plano.
_LANZADO_EN_FONDO = "Async agent launched"

#: Separadores que el gestor y el historial ponen entre la entrada de una
#: herramienta y su resultado.
_SEPARADORES_RESULTADO = (("\n\nResultado:\n", False), ("\n\nError:\n", True))

_RE_VALLA = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_RE_ENLACE_MD = re.compile(r"(?<!!)\[([^\]\n]*)\]\(\s*<?(https?://[^\s<>]+?)>?(?:\s+\"[^\"\n]*\")?\s*\)")
_RE_URL = re.compile(r"https?://[^\s<>\"'`\u00ab\u00bb\u201c\u201d]+", re.IGNORECASE)
_PUNTUACION_FINAL = ".,;:!?*_~'\"\u2019\u2026"
_PAREJAS = {")": "(", "]": "[", "}": "{"}

# Rutas locales.
_CAR_RUTA = r"[^\s<>\"'`|?*\u00ab\u00bb\u201c\u201d]"
_RE_RUTA_WINDOWS = re.compile(r"(?<![\w/\\:])[A-Za-z]:[\\/]" + _CAR_RUTA + "*")
_RE_RUTA_UNC = re.compile(r"(?<![\w\\/:])\\\\[A-Za-z0-9._$-]+\\" + _CAR_RUTA + "+")
_RE_URL_ARCHIVO = re.compile(r"(?<![\w])file:(?://)?/?[^\s<>\"'`]+", re.IGNORECASE)
_RE_CODIGO_EN_LINEA = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_RE_ENLACE_MD_GENERICO = re.compile(
	r"(?<!!)\[([^\]\n]*)\]\(\s*(?:<([^>\n]+)>|([^\s()<>]+(?:\([^\s()]*\)[^\s()<>]*)*))(?:\s+\"[^\"\n]*\")?\s*\)"
)
_RE_RUTA_ABSOLUTA = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+\\)[^<>\"|?*\n]*$")
_RE_RUTA_RELATIVA = re.compile(r"^(?:\.{1,2}[\\/])?(?:[\w.@+-]+[\\/])+[\w.@+-]*\.[A-Za-z0-9]{1,8}(?::\d+(?::\d+)?)?$")
_RE_NOMBRE_ARCHIVO = re.compile(r"^[\w.@+-]+\.[A-Za-z0-9]{1,8}(?::\d+(?::\d+)?)?$")
_RE_LINEA_FINAL = re.compile(r"^(.*?)(?:#L(\d+)(?:-L?\d+)?|:(\d+)(?::\d+)?)$")

# Tareas.
_RE_PASO = re.compile(r"^\s*(?:[-*]\s*)?\[(hecho|hecha|en curso|pendiente|x|X| )\]\s*(.+?)\s*$")
_RE_ID_TAREA = re.compile(r"#\s*([A-Za-z0-9_-]+)")
_RE_CLAVE_VIVA = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?: (.*))?$")


@dataclass(slots=True)
class BloqueCodigo:
	"""Un bloque de código de un mensaje."""

	lenguaje: str
	codigo: str
	indice: int

	@property
	def num_lineas(self) -> int:
		"""Número de líneas del código (0 si está vacío)."""
		return len(self.codigo.splitlines())


@dataclass(slots=True)
class Enlace:
	"""Una dirección web o la ruta de un archivo local citada en la conversación.

	``tipo`` es ``"web"`` o ``"archivo"``. En un archivo, ``url`` es la ruta tal
	como se escribió (sin el número de línea, que va en ``linea``); puede ser
	relativa a la carpeta de la sesión.
	"""

	url: str
	texto: str
	indice: int
	tipo: str = "web"
	linea: int = 0

	@property
	def es_archivo(self) -> bool:
		return self.tipo == "archivo"


@dataclass(slots=True)
class Cambio:
	"""Un archivo creado o modificado, con el diff acumulado de todos sus cambios."""

	ruta: str
	diff: str
	indice: int
	#: Descripción del subagente que lo cambió (vacío si fue el agente principal).
	subagente: str = ""


@dataclass(slots=True)
class Orden:
	"""Una orden de consola ejecutada por el agente."""

	orden: str
	indice: int
	salida: str = ""
	#: ``True`` si falló, ``False`` si terminó bien y ``None`` si no hay resultado.
	error: bool | None = None


@dataclass(slots=True)
class Tarea:
	"""Una tarea de la lista del agente."""

	texto: str
	#: ``pendiente``, ``en_curso`` o ``hecha``.
	estado: str
	indice: int
	id: str = ""
	#: Lo que el agente dice mientras la hace («Ejecutando las pruebas»), si lo da.
	en_curso_texto: str = ""


@dataclass(slots=True)
class Subagente:
	"""Un trabajo encargado a un subagente."""

	descripcion: str
	tipo: str
	instrucciones: str
	resultado: str
	#: ``terminado``, ``error``, ``en_marcha`` (aún sin resultado) o
	#: ``en_segundo_plano`` (lanzado en segundo plano; su respuesta llega después).
	estado: str
	indice: int
	id: str = ""
	#: Pasos (herramientas) y ediciones: ``None`` si no se pudo leer su transcript.
	pasos: int | None = None
	ediciones: int | None = None
	acciones: list[str] = field(default_factory=list)
	archivos: list[Cambio] = field(default_factory=list)
	ordenes: list[Orden] = field(default_factory=list)


@dataclass(slots=True)
class Hallazgo:
	"""Un hallazgo de una revisión de código."""

	resumen: str
	archivo: str
	linea: int
	categoria: str
	#: ``confirmado``, ``posible`` o vacío.
	veredicto: str
	escenario: str
	#: Tras un pase de correcciones: ``corregido``, ``omitido``, ``sin_cambios`` o vacío.
	resultado: str
	corto: str
	indice: int


# ── Entrada y resultado de las herramientas ─────────────────────────────────────


def separar_resultado(detalle: str) -> tuple[str, str, bool | None]:
	"""Divide el detalle de una herramienta en ``(entrada, salida, error)``.

	``error`` es ``None`` si la herramienta aún no tiene resultado.
	"""
	detalle = detalle or ""
	for separador, es_error in _SEPARADORES_RESULTADO:
		cabecera = separador.lstrip("\n")
		if detalle.startswith(cabecera):
			return "", detalle[len(cabecera):], es_error
	encontrados = [(detalle.find(separador), separador, es_error) for separador, es_error in _SEPARADORES_RESULTADO]
	encontrados = [elemento for elemento in encontrados if elemento[0] >= 0]
	if not encontrados:
		return detalle, "", None
	posicion, separador, es_error = min(encontrados)
	return detalle[:posicion], detalle[posicion + len(separador):], es_error


def _entrada_viva(texto: str) -> dict[str, Any]:
	"""Lee la entrada tal como la escribe el adaptador de Claude en vivo.

	Cada clave en su línea: ``clave: valor``; los textos de varias líneas
	van tras ``clave:`` y los valores que no son texto, en JSON.
	"""
	resultado: dict[str, Any] = {}
	decodificador = json.JSONDecoder()
	clave_larga = ""
	trozos: list[str] = []
	posicion = 0
	total = len(texto)
	while posicion <= total:
		fin = texto.find("\n", posicion)
		fin = total if fin < 0 else fin
		linea = texto[posicion:fin]
		coincidencia = _RE_CLAVE_VIVA.match(linea)
		if coincidencia is None:
			if clave_larga:
				trozos.append(linea)
			posicion = fin + 1
			continue
		if clave_larga:
			resultado[clave_larga] = "\n".join(trozos).strip("\n")
			clave_larga, trozos = "", []
		clave, valor = coincidencia.group(1), coincidencia.group(2)
		if valor is None:
			clave_larga = clave
			posicion = fin + 1
			continue
		if valor[:1] in "[{":
			try:
				dato, final = decodificador.raw_decode(texto, posicion + len(clave) + 2)
			except ValueError:
				resultado[clave] = valor
			else:
				resultado[clave] = dato
				siguiente = texto.find("\n", final)
				posicion = total + 1 if siguiente < 0 else siguiente + 1
				continue
		else:
			resultado[clave] = valor
		posicion = fin + 1
	if clave_larga:
		resultado[clave_larga] = "\n".join(trozos).strip("\n")
	return resultado


def entrada_herramienta(detalle: str) -> dict[str, Any]:
	"""La entrada de una herramienta como diccionario (vacío si no se entiende).

	Admite las dos formas que se guardan: la del historial de Claude Code
	(``Entrada:`` seguido de JSON) y la del adaptador en vivo (``clave: valor``).
	"""
	entrada = separar_resultado(detalle)[0].strip("\n")
	if not entrada:
		return {}
	if entrada.startswith("Entrada:\n"):
		try:
			valor, _final = json.JSONDecoder().raw_decode(entrada[len("Entrada:\n"):].lstrip())
		except ValueError:
			log.debug("Entrada de herramienta en JSON que no se entiende", exc_info=True)
			return {}
		return valor if isinstance(valor, dict) else {}
	try:
		return _entrada_viva(entrada)
	except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
		log.debug("Entrada de herramienta que no se entiende", exc_info=True)
		return {}


def _texto(valor: Any) -> str:
	return valor.strip() if isinstance(valor, str) else ("" if valor is None else str(valor).strip())


# ── Bloques de código ───────────────────────────────────────────────────────────


def _bloques_de_texto(texto: str) -> list[tuple[str, str]]:
	"""Devuelve ``[(lenguaje, código)]`` de un texto Markdown."""
	resultado: list[tuple[str, str]] = []
	lineas = (texto or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
	valla = ""
	lenguaje = ""
	contenido: list[str] = []
	for linea in lineas:
		if not valla:
			apertura = _RE_VALLA.match(linea)
			if apertura is None:
				continue
			marca, resto = apertura.group(1), apertura.group(2).strip()
			if marca[0] == "`" and "`" in resto:
				continue  # «```código```» en una línea: es código en línea, no un bloque
			valla = marca
			lenguaje = resto.split()[0].strip("{}.") if resto else ""
			contenido = []
			continue
		cierre = linea.strip()
		if cierre and cierre[0] == valla[0] and set(cierre) == {valla[0]} and len(cierre) >= len(valla):
			resultado.append((lenguaje, "\n".join(contenido)))
			valla = ""
			continue
		contenido.append(linea)
	if valla and any(linea.strip() for linea in contenido):
		# Bloque sin cerrar al final (el agente aún escribía o se cortó).
		resultado.append((lenguaje, "\n".join(contenido).rstrip("\n")))
	return resultado


def _sin_bloques(texto: str) -> str:
	"""El texto con el contenido de los bloques de código cambiado por espacios.

	Conserva la longitud (y por tanto las posiciones) de todo lo demás.
	"""
	lineas = (texto or "").split("\n")
	valla = ""
	for numero, linea in enumerate(lineas):
		if not valla:
			apertura = _RE_VALLA.match(linea.rstrip("\r"))
			if apertura is not None and not (apertura.group(1)[0] == "`" and "`" in apertura.group(2)):
				valla = apertura.group(1)
				lineas[numero] = " " * len(linea)
			continue
		cierre = linea.strip()
		if cierre and cierre[0] == valla[0] and set(cierre) == {valla[0]} and len(cierre) >= len(valla):
			valla = ""
		lineas[numero] = " " * len(linea)
	return "\n".join(lineas)


def bloques_codigo(mensajes: Sequence[MensajeVista]) -> list[BloqueCodigo]:
	"""Bloques de código de los mensajes del asistente y del usuario, en orden."""
	bloques: list[BloqueCodigo] = []
	for indice, mensaje in enumerate(mensajes):
		texto = mensaje.texto or ""
		if mensaje.rol not in ROLES_CON_CODIGO or ("```" not in texto and "~~~" not in texto):
			continue
		for lenguaje, codigo in _bloques_de_texto(mensaje.texto):
			if codigo.strip():
				bloques.append(BloqueCodigo(lenguaje, codigo, indice))
	return bloques


def ultimo_bloque(mensajes: Sequence[MensajeVista]) -> BloqueCodigo | None:
	"""El último bloque de código de la conversación, o ``None`` si no hay."""
	bloques = bloques_codigo(mensajes)
	return bloques[-1] if bloques else None


# ── Enlaces ─────────────────────────────────────────────────────────────────────


def limpiar_url(url: str) -> str:
	"""Quita la puntuación final pegada y los cierres sin pareja (``)``, ``]``, ``}``)."""
	url = url.strip()
	while url:
		final = url[-1]
		if final in _PUNTUACION_FINAL:
			url = url[:-1]
		elif final in _PAREJAS and url.count(final) > url.count(_PAREJAS[final]):
			url = url[:-1]
		else:
			break
	return url


def _enlaces_web(texto: str) -> list[tuple[int, int, str, str]]:
	"""``[(inicio, fin, url, texto)]`` de las direcciones web de un texto."""
	encontrados: list[tuple[int, int, str, str]] = []
	ocupados: list[tuple[int, int]] = []
	for coincidencia in _RE_ENLACE_MD.finditer(texto):
		url = limpiar_url(coincidencia.group(2))
		if url:
			encontrados.append((coincidencia.start(), coincidencia.end(), url, " ".join(coincidencia.group(1).split())))
			ocupados.append(coincidencia.span())
	for coincidencia in _RE_URL.finditer(texto):
		inicio = coincidencia.start()
		if any(desde <= inicio < hasta for desde, hasta in ocupados):
			continue
		url = limpiar_url(coincidencia.group(0))
		if len(url) > len("https://"):
			encontrados.append((inicio, coincidencia.end(), url, ""))
	return encontrados


def _enlaces_de_texto(texto: str) -> list[tuple[str, str]]:
	"""Devuelve ``[(url, texto)]`` de un texto en el orden en que aparecen."""
	encontrados = sorted(_enlaces_web(texto), key=lambda elemento: elemento[0])
	return [(url, etiqueta) for _inicio, _fin, url, etiqueta in encontrados]


def separar_linea(ruta: str) -> tuple[str, int]:
	"""``app/x.py:42`` → ``("app/x.py", 42)``; ``C:\\a.md`` → ``("C:\\a.md", 0)``.

	También entiende ``#L42`` y ``:42:7`` (línea y columna).
	"""
	coincidencia = _RE_LINEA_FINAL.match(ruta)
	if coincidencia is None:
		return ruta, 0
	base = coincidencia.group(1)
	if not base or re.fullmatch(r"[A-Za-z]", base):
		return ruta, 0  # «C:» es la unidad, no una línea
	return base, int(coincidencia.group(2) or coincidencia.group(3))


def ruta_de_url_archivo(url: str) -> str:
	"""``file:///C:/a%20b.txt`` → ``C:\\a b.txt``; ``file://servidor/x`` → ``\\\\servidor\\x``."""
	resto = unquote(url[len("file:"):])
	if resto.startswith("///"):
		resto = resto[3:]
	elif resto.startswith("//"):
		resto = resto[2:]
		if resto.lower().startswith("localhost/"):
			resto = resto[len("localhost/"):]
		elif not re.match(r"[A-Za-z]:", resto):
			return "\\\\" + resto.replace("/", "\\")
	elif resto.startswith("/"):
		resto = resto[1:]
	if re.match(r"[A-Za-z]:", resto):
		return resto.replace("/", "\\")
	return resto


def _es_ruta_de_destino(destino: str) -> bool:
	"""Si el destino de un enlace Markdown es un archivo local."""
	if not destino or destino.startswith("#") or "://" in destino or destino.lower().startswith(("mailto:", "javascript:", "data:")):
		return False
	if _RE_RUTA_ABSOLUTA.match(destino):
		return True
	return bool(_RE_RUTA_RELATIVA.match(destino) or _RE_NOMBRE_ARCHIVO.match(destino))


def _rutas_de_texto(texto: str) -> list[tuple[int, str, str, int]]:
	"""``[(posición, ruta, texto, línea)]`` de las rutas locales de un texto.

	No mira dentro de los bloques de código (allí las rutas son parte del
	código, no algo que el agente te señale).
	"""
	limpio = _sin_bloques(texto)
	encontrados: list[tuple[int, str, str, int]] = []
	ocupados: list[tuple[int, int]] = [(inicio, fin) for inicio, fin, _url, _texto in _enlaces_web(limpio)]

	def libre(inicio: int) -> bool:
		return not any(desde <= inicio < hasta for desde, hasta in ocupados)

	def anadir(posicion: int, bruta: str, etiqueta: str, recortar: bool = True) -> None:
		bruta = bruta.strip()
		if bruta.lower().startswith("file:"):
			bruta = ruta_de_url_archivo(limpiar_url(bruta) if recortar else bruta)
		elif recortar:
			bruta = limpiar_url(bruta)
		ruta, linea = separar_linea(bruta)
		ruta = ruta.rstrip(".,;:")
		if len(ruta) > 3:
			encontrados.append((posicion, ruta, etiqueta, linea))

	for coincidencia in _RE_ENLACE_MD_GENERICO.finditer(limpio):
		destino = (coincidencia.group(2) or coincidencia.group(3) or "").strip()
		if not libre(coincidencia.start()):
			continue
		if destino.lower().startswith("file:") or _es_ruta_de_destino(destino):
			anadir(coincidencia.start(), destino, " ".join(coincidencia.group(1).split()), recortar=False)
			ocupados.append(coincidencia.span())
	for coincidencia in _RE_CODIGO_EN_LINEA.finditer(limpio):
		if not libre(coincidencia.start()):
			continue
		contenido = coincidencia.group(1).strip()
		if contenido.lower().startswith("file:") or _RE_RUTA_ABSOLUTA.match(contenido) or _RE_RUTA_RELATIVA.match(contenido):
			anadir(coincidencia.start(), contenido, "", recortar=not _RE_RUTA_ABSOLUTA.match(contenido))
		ocupados.append(coincidencia.span())  # nada de lo que hay en el código en línea es una ruta suelta
	for expresion in (_RE_URL_ARCHIVO, _RE_RUTA_WINDOWS, _RE_RUTA_UNC):
		for coincidencia in expresion.finditer(limpio):
			if libre(coincidencia.start()):
				anadir(coincidencia.start(), coincidencia.group(0), "")
				ocupados.append(coincidencia.span())
	return encontrados


def clave_ruta(ruta: str) -> str:
	"""Clave para no duplicar la misma ruta escrita con otras barras o mayúsculas."""
	return ruta.replace("\\", "/").rstrip("/").casefold()


def enlaces(mensajes: Sequence[MensajeVista], incluir_archivos: bool = True) -> list[Enlace]:
	"""Enlaces de la conversación sin duplicados, en el orden de su primera aparición.

	Si una dirección aparece primero suelta y luego con texto
	(``[texto](url)``), se conserva la primera aparición con ese texto. Con
	``incluir_archivos`` se añaden las rutas de archivos locales de los
	mensajes del asistente y del usuario.
	"""
	resultado: list[Enlace] = []
	vistos: dict[str, Enlace] = {}
	for indice, mensaje in enumerate(mensajes):
		texto = mensaje.texto or ""
		encontrados: list[tuple[int, str, str, str, int]] = []
		if "http" in texto.lower():
			encontrados.extend((inicio, "web", url, etiqueta, 0) for inicio, _fin, url, etiqueta in _enlaces_web(texto))
		if incluir_archivos and mensaje.rol in ROLES_CON_RUTAS and any(marca in texto for marca in (":\\", ":/", "\\\\", "`", "](", "file:")):
			try:
				encontrados.extend((posicion, "archivo", ruta, etiqueta, linea) for posicion, ruta, etiqueta, linea in _rutas_de_texto(texto))
			except Exception:  # noqa: BLE001 - un texto raro nunca debe romper las listas
				log.debug("No se pudieron buscar rutas en un mensaje", exc_info=True)
		encontrados.sort(key=lambda elemento: elemento[0])
		for _posicion, tipo, url, etiqueta, linea in encontrados:
			clave = url if tipo == "web" else "archivo:" + clave_ruta(url)
			existente = vistos.get(clave)
			if existente is not None:
				if etiqueta and not existente.texto:
					existente.texto = etiqueta
				if linea and not existente.linea:
					existente.linea = linea
				continue
			enlace = Enlace(url, etiqueta, indice, tipo, linea)
			vistos[clave] = enlace
			resultado.append(enlace)
	return resultado


# ── Archivos cambiados ──────────────────────────────────────────────────────────


def contar_lineas_diff(diff: str) -> tuple[int, int]:
	"""Líneas añadidas y quitadas de un diff unificado (sin contar ``+++``/``---``)."""
	anadidas = quitadas = 0
	for linea in (diff or "").splitlines():
		if linea.startswith("+") and not linea.startswith("+++"):
			anadidas += 1
		elif linea.startswith("-") and not linea.startswith("---"):
			quitadas += 1
	return anadidas, quitadas


#: Estilos de ``diff_legible``: palabras (de fábrica), palabras cortas o el diff tal cual.
ESTILOS_CAMBIOS = ("palabras", "cortas", "simbolos")

_RE_CABECERA_TROZO = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")

#: Cómo se dice cada clase de línea: (palabras, cortas). Las cortas son «Más»,
#: «Menos» e «Igual»: el sintetizador las pronuncia como palabras (una
#: abreviatura como «Añ» o «Qu» se deletrearía o sonaría rara), se oyen con
#: cualquier nivel de puntuación y recuerdan a los signos + y − del diff.
_MARCAS_LINEA = {
	"+": ("añadida", "Más"),
	"-": ("quitada", "Menos"),
	" ": ("sin cambios", "Igual"),
}


def _texto_linea(marca: str, contenido: str, numero: int | None, cortas: bool) -> str:
	palabra, corta = _MARCAS_LINEA[marca]
	if not contenido.strip():
		contenido = "en blanco" if cortas else "línea en blanco"
	if cortas:
		return f"{corta} {numero}: {contenido}" if numero is not None else f"{corta}: {contenido}"
	if numero is not None:
		return f"Línea {numero}, {palabra}: {contenido}"
	return f"{palabra.capitalize()}: {contenido}"


def _texto_cabecera_trozo(inicio: int, cuantas: int, contexto: str, cortas: bool) -> str:
	inicio = max(inicio, 1)
	if cuantas > 1:
		rango = f"líneas {inicio} a {inicio + cuantas - 1}"
		texto = rango.capitalize() if cortas else "Cambio en las " + rango
	else:
		rango = f"línea {inicio}"
		texto = rango.capitalize() if cortas else "Cambio en la " + rango
	contexto = contexto.strip()
	return texto + (f", en {contexto}:" if contexto else ":")


def _texto_cabecera_archivo(linea: str) -> str:
	ruta = linea[4:].strip()
	if linea.startswith("---"):
		return "Antes: " + ("no existía" if ruta == "/dev/null" else ruta)
	return "Después: " + ("borrado" if ruta == "/dev/null" else ruta)


def diff_legible(diff: str, estilo: str = "palabras") -> str:
	"""Un diff unificado escrito con palabras, para oírlo con un lector de pantalla.

	Con el nivel de puntuación bajo, NVDA y JAWS no dicen «+» ni «-», y todas
	las líneas sonarían iguales. Estilos (``ESTILOS_CAMBIOS``):

	- ``palabras`` (de fábrica): «Línea 12, añadida: …», «Línea 12, quitada: …»,
	  «Línea 13, sin cambios: …», con los números de línea del archivo nuevo
	  sacados de las cabeceras ``@@``; cada cabecera se dice «Cambio en las
	  líneas 12 a 19:». Sin cabeceras, sin número: «Añadida: …».
	- ``cortas``: lo mismo con palabras breves: «Más 12: …», «Menos 12: …»,
	  «Igual 13: …» y «Líneas 12 a 19:».
	- ``simbolos``: el diff tal cual.

	Una línea quitada lleva el número de la posición que ocupaba, así que una
	línea sustituida se oye «Línea 12, quitada» y luego «Línea 12, añadida».
	Lo que no es una línea del cambio (las cabeceras ``diff --git``, ``index``...)
	se deja como está. Función pura: no guarda ni lee nada.
	"""
	diff = diff or ""
	if estilo == "simbolos" or not diff.strip():
		return diff
	cortas = estilo == "cortas"
	lineas = diff.splitlines()
	hay_trozos = any(_RE_CABECERA_TROZO.match(linea) for linea in lineas)
	salida: list[str] = []
	quedan_viejas = quedan_nuevas = 0
	numero = 0
	for linea in lineas:
		trozo = _RE_CABECERA_TROZO.match(linea)
		if trozo:
			quedan_viejas = int(trozo.group(2)) if trozo.group(2) is not None else 1
			quedan_nuevas = int(trozo.group(4)) if trozo.group(4) is not None else 1
			numero = int(trozo.group(3))
			# «+0,0» (archivo vaciado) o «+12,0» (solo quitadas): las quitadas ocupaban la línea siguiente.
			if quedan_nuevas == 0:
				numero += 1
			salida.append(_texto_cabecera_trozo(numero, quedan_nuevas, trozo.group(5), cortas))
			continue
		if linea.startswith("\\"):
			nota = linea.lstrip("\\ ").strip()
			if nota.lower() == "no newline at end of file":
				nota = "sin salto de línea al final del archivo"
			salida.append("Nota: " + nota)
			continue
		if hay_trozos:
			if quedan_viejas <= 0 and quedan_nuevas <= 0:
				# Fuera de un trozo: cabeceras del archivo o separación entre cambios.
				if linea.startswith(("--- ", "+++ ")):
					salida.append(_texto_cabecera_archivo(linea))
				else:
					salida.append(linea)
				continue
			marca = linea[:1] if linea else " "
			if marca not in _MARCAS_LINEA:
				salida.append(linea)
				continue
			salida.append(_texto_linea(marca, linea[1:], numero, cortas))
			if marca == "+":
				numero += 1
				quedan_nuevas -= 1
			elif marca == "-":
				quedan_viejas -= 1
			else:
				numero += 1
				quedan_nuevas -= 1
				quedan_viejas -= 1
			continue
		# Sin cabeceras «@@»: solo se sabe la clase de cada línea.
		marca = linea[:1]
		if marca in ("+", "-") and not linea.startswith(("+++ ", "--- ")):
			salida.append(_texto_linea(marca, linea[1:], None, cortas))
		elif marca == " ":
			salida.append(_texto_linea(marca, linea[1:], None, cortas))
		elif linea.startswith(("--- ", "+++ ")):
			salida.append(_texto_cabecera_archivo(linea))
		else:
			salida.append(linea)
	return "\n".join(salida)


def _ruta_tras_dos_puntos(texto: str) -> str:
	"""«Archivo modificado: C:\\x.py» → «C:\\x.py» (la primera línea, sin comillas)."""
	primera = (texto or "").strip().split("\n", 1)[0]
	_, separador, ruta = primera.partition(": ")
	return ruta.strip().strip("\"'`") if separador else ""


def _clave_ruta(ruta: str) -> str:
	return clave_ruta(ruta)


def _cambios_de_varias_cabeceras(texto: str, detalle: str) -> list[tuple[str, str]]:
	"""Un mensaje «cambio» con varias líneas «Archivo X: ruta» (Codex).

	El detalle repite cada línea como cabecera de su diff.
	"""
	cabeceras = [linea.strip() for linea in (texto or "").splitlines() if linea.strip().startswith("Archivo ") and ": " in linea]
	if len(cabeceras) < 2:
		return []
	por_cabecera: dict[str, list[str]] = {cabecera: [] for cabecera in cabeceras}
	actual = ""
	for linea in (detalle or "").split("\n"):
		if linea.strip() in por_cabecera:
			actual = linea.strip()
			continue
		if actual:
			por_cabecera[actual].append(linea)
	resultado = []
	for cabecera in cabeceras:
		ruta = _ruta_tras_dos_puntos(cabecera).split(" (movido a ", 1)[0]
		if ruta:
			resultado.append((ruta, "\n".join(por_cabecera[cabecera]).strip("\n")))
	return resultado


def _cambios_de_mensaje(mensaje: MensajeVista) -> list[tuple[str, str]]:
	"""``[(ruta, diff)]`` que aporta un mensaje (vacío si no es un cambio)."""
	if mensaje.rol == "cambio":
		cambios = [
			(str(cambio.get("ruta") or "").strip(), str(cambio.get("diff") or ""))
			for cambio in (mensaje.cambios or [])
			if isinstance(cambio, dict) and str(cambio.get("ruta") or "").strip()
		]
		if cambios:
			return cambios
		varios = _cambios_de_varias_cabeceras(mensaje.texto, mensaje.detalle)
		if varios:
			return varios
		ruta = _ruta_tras_dos_puntos(mensaje.texto).split(" (movido a ", 1)[0]
		detalle = mensaje.detalle or ""
		primera = (mensaje.texto or "").strip().split("\n", 1)[0].strip()
		if detalle.startswith(primera + "\n"):
			detalle = detalle[len(primera) + 1:]
		return [(ruta, detalle)] if ruta else []
	if mensaje.rol == "herramienta":
		# El historial de Claude Code guarda el diff en la propia herramienta.
		cambios = [
			(str(cambio.get("ruta") or "").strip(), str(cambio.get("diff") or ""))
			for cambio in (mensaje.cambios or [])
			if isinstance(cambio, dict) and str(cambio.get("ruta") or "").strip()
		]
		if cambios:
			return cambios
		texto = (mensaje.texto or "").strip()
		if texto.startswith(_PREFIJOS_EDICION) or mensaje.herramienta in _HERRAMIENTAS_EDICION:
			ruta = _ruta_tras_dos_puntos(texto)
			if ruta:
				# El detalle de una herramienta no es un diff: solo se usa si
				# no llega un mensaje «cambio» con el diff real de esa ruta.
				return [(ruta, "")]
	return []


def _acumular(resultado: list[Cambio], por_ruta: dict[str, Cambio], ruta: str, diff: str, indice: int, subagente: str = "") -> None:
	clave = _clave_ruta(ruta)
	existente = por_ruta.get(clave)
	diff = diff.strip("\n")
	if existente is None:
		cambio = Cambio(ruta, diff, indice, subagente)
		por_ruta[clave] = cambio
		resultado.append(cambio)
		return
	existente.indice = max(existente.indice, indice)
	if diff and diff not in existente.diff:
		existente.diff = existente.diff + "\n\n" + diff if existente.diff else diff
	if subagente and not existente.subagente:
		existente.subagente = subagente


def archivos_cambiados(mensajes: Sequence[MensajeVista]) -> list[Cambio]:
	"""Archivos creados o modificados, uno por ruta, en el orden del primer cambio.

	Si una ruta cambia varias veces, sus diffs se acumulan (en orden) y el
	``indice`` pasa a ser el del último mensaje que la cambió: «Ir al
	mensaje» lleva al cambio más reciente.
	"""
	resultado: list[Cambio] = []
	por_ruta: dict[str, Cambio] = {}
	for indice, mensaje in enumerate(mensajes):
		for ruta, diff in _cambios_de_mensaje(mensaje):
			_acumular(resultado, por_ruta, ruta, diff, indice)
	return resultado


def combinar_cambios(principales: Sequence[Cambio], subagentes: Iterable[Subagente]) -> list[Cambio]:
	"""Los archivos cambiados del agente más los de sus subagentes.

	Los de un subagente llevan el índice del mensaje que lo lanzó y su
	descripción en ``subagente``. No modifica las listas recibidas.
	"""
	resultado: list[Cambio] = []
	por_ruta: dict[str, Cambio] = {}
	for cambio in principales:
		_acumular(resultado, por_ruta, cambio.ruta, cambio.diff, cambio.indice, cambio.subagente)
	for subagente in subagentes:
		for cambio in subagente.archivos:
			_acumular(resultado, por_ruta, cambio.ruta, cambio.diff, subagente.indice, subagente.descripcion or subagente.tipo or "subagente")
	return resultado


# ── Órdenes ejecutadas ──────────────────────────────────────────────────────────


def _orden_del_detalle(detalle: str) -> str:
	"""Orden completa del detalle de la herramienta (el texto visible se recorta)."""
	if not detalle:
		return ""
	cuerpo = ""
	if "Orden:\n" in detalle:
		cuerpo = detalle.split("Orden:\n", 1)[1]
	elif detalle.startswith("Ejecutar: "):
		cuerpo = detalle[len("Ejecutar: "):]
	elif re.match(r"command:[ \n]", detalle):
		cuerpo = detalle[len("command:"):].split("\ndescription:", 1)[0]
	for separador in ("\n\nResultado:", "\n\nError:"):
		cuerpo = cuerpo.split(separador, 1)[0]
	return cuerpo.strip()


def ordenes_detalladas(mensajes: Sequence[MensajeVista]) -> list[Orden]:
	"""Órdenes de consola ejecutadas por el agente, con su salida y si fallaron."""
	resultado: list[Orden] = []
	for indice, mensaje in enumerate(mensajes):
		if mensaje.rol != "herramienta":
			continue
		texto = (mensaje.texto or "").strip()
		if not texto.startswith(_PREFIJOS_ORDEN):
			continue
		orden = _orden_del_detalle(mensaje.detalle or "")
		if not orden:
			_, separador, resto = texto.partition(": ")
			orden = resto.strip() if separador else ""
		if orden:
			_entrada, salida, error = separar_resultado(mensaje.detalle or "")
			resultado.append(Orden(orden, indice, salida.strip("\n"), error))
	return resultado


def ordenes(mensajes: Sequence[MensajeVista]) -> list[tuple[str, int]]:
	"""Órdenes de consola ejecutadas por el agente: ``[(orden, índice del mensaje)]``."""
	return [(orden.orden, orden.indice) for orden in ordenes_detalladas(mensajes)]


# ── Tareas del agente ───────────────────────────────────────────────────────────


def normalizar_estado(estado: Any) -> str:
	"""Estado de una tarea en cualquier forma → ``pendiente``, ``en_curso`` o ``hecha``."""
	valor = _texto(estado).lower().replace(" ", "_").replace("-", "_")
	if valor in ("completed", "complete", "hecho", "hecha", "done", "completado", "completada", "x"):
		return "hecha"
	if valor in ("in_progress", "inprogress", "en_curso", "active", "running", "activa", "activo"):
		return "en_curso"
	return "pendiente"


def _tareas_de_lineas(texto: str, indice: int) -> list[Tarea]:
	"""Tareas de un texto con líneas «[hecho] tarea» (plan) o «[x] tarea» (historial)."""
	tareas_leidas = []
	for linea in (texto or "").splitlines():
		coincidencia = _RE_PASO.match(linea)
		if coincidencia:
			marca = coincidencia.group(1).lower()
			estado = {"x": "hecha", "hecho": "hecha", "hecha": "hecha", "en curso": "en_curso"}.get(marca, "pendiente")
			tareas_leidas.append(Tarea(coincidencia.group(2), estado, indice))
	return tareas_leidas


def _tareas_de_todos(entrada: dict[str, Any], indice: int) -> list[Tarea]:
	resultado = []
	for tarea in entrada.get("todos") or []:
		if isinstance(tarea, dict) and _texto(tarea.get("content")):
			resultado.append(Tarea(_texto(tarea.get("content")), normalizar_estado(tarea.get("status")), indice,
				en_curso_texto=_texto(tarea.get("activeForm"))))
	return resultado


def tareas(mensajes: Sequence[MensajeVista]) -> list[Tarea]:
	"""La lista de tareas del agente en su último estado, en el orden del agente.

	Un plan (o un TodoWrite) sustituye la lista entera; TaskCreate añade una
	tarea y TaskUpdate cambia su estado o la borra. El ``indice`` de cada
	tarea es el del último mensaje que la cambió.
	"""
	lista: list[Tarea] = []
	creadas = 0
	for indice, mensaje in enumerate(mensajes):
		try:
			if mensaje.rol == "plan":
				nuevas = _tareas_de_lineas(mensaje.texto, indice)
				if nuevas:
					lista = nuevas
				continue
			if mensaje.rol != "herramienta":
				continue
			nombre = mensaje.herramienta or ""
			if nombre == "TodoWrite":
				entrada_texto = separar_resultado(mensaje.detalle or "")[0]
				nuevas = _tareas_de_lineas(entrada_texto, indice) or _tareas_de_todos(entrada_herramienta(mensaje.detalle), indice)
				if nuevas or "todos" in entrada_herramienta(mensaje.detalle):
					lista = nuevas
			elif nombre == "TaskCreate":
				entrada = entrada_herramienta(mensaje.detalle)
				_entrada, salida, error = separar_resultado(mensaje.detalle or "")
				texto = _texto(entrada.get("subject"))
				if not texto or error:
					continue
				creadas += 1
				identificador = _RE_ID_TAREA.search(salida)
				lista.append(Tarea(texto, "pendiente", indice, identificador.group(1) if identificador else str(creadas),
					_texto(entrada.get("activeForm"))))
			elif nombre == "TaskUpdate":
				entrada = entrada_herramienta(mensaje.detalle)
				if separar_resultado(mensaje.detalle or "")[2]:
					continue  # la actualización falló
				identificador = _texto(entrada.get("taskId")).lstrip("#")
				tarea = next((t for t in lista if t.id == identificador), None)
				if tarea is None:
					continue
				estado = _texto(entrada.get("status")).lower()
				if estado == "deleted":
					lista.remove(tarea)
					continue
				if estado:
					tarea.estado = normalizar_estado(estado)
				if _texto(entrada.get("subject")):
					tarea.texto = _texto(entrada.get("subject"))
				if _texto(entrada.get("activeForm")):
					tarea.en_curso_texto = _texto(entrada.get("activeForm"))
				tarea.indice = indice
		except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
			log.debug("Mensaje de tareas que no se entiende", exc_info=True)
	return lista


def tareas_de_pasos(pasos: Iterable[Any], indice: int = -1) -> list[Tarea]:
	"""Tareas a partir de ``datos["pasos"]`` de un evento ``PLAN`` (``[(texto, estado)]``)."""
	resultado = []
	for paso in pasos or []:
		if isinstance(paso, (list, tuple)) and len(paso) >= 2 and _texto(paso[0]):
			resultado.append(Tarea(_texto(paso[0]), normalizar_estado(paso[1]), indice))
	return resultado


def nombre_estado(estado: str) -> str:
	"""Cómo se dice un estado de tarea."""
	return {"hecha": "Hecha", "en_curso": "En curso"}.get(estado, "Pendiente")


def resumen_tareas(lista: Sequence[Tarea]) -> str:
	"""«Tarea 3 de 7: ejecutar las pruebas. 2 hechas.» (vacío si no hay tareas)."""
	if not lista:
		return ""
	total = len(lista)
	hechas = sum(1 for tarea in lista if tarea.estado == "hecha")
	if hechas == total:
		return "Todas las tareas hechas ({n}).".format(n=total) if total > 1 else "La tarea está hecha."
	for posicion, tarea in enumerate(lista, start=1):
		if tarea.estado == "en_curso":
			return "Tarea {p} de {t}: {texto}. {h} hechas.".format(p=posicion, t=total, texto=tarea.texto.rstrip("."), h=hechas)
	return "{h} de {t} tareas hechas.".format(h=hechas, t=total)


def anuncio_cambio_tareas(antes: Sequence[Tarea], despues: Sequence[Tarea]) -> str:
	"""Lo que se diría cuando el agente marca tareas como hechas (vacío si ninguna).

	«Hecha: ejecutar las pruebas. 4 de 7.» Las tareas se reconocen por su id
	o, si no lo tienen, por su texto.
	"""
	def clave(tarea: Tarea) -> str:
		return "id:" + tarea.id if tarea.id else "texto:" + tarea.texto.casefold()

	hechas_antes = {clave(tarea) for tarea in antes if tarea.estado == "hecha"}
	nuevas = [tarea for tarea in despues if tarea.estado == "hecha" and clave(tarea) not in hechas_antes]
	if not nuevas:
		return ""
	total = len(despues)
	hechas = sum(1 for tarea in despues if tarea.estado == "hecha")
	textos = "; ".join(tarea.texto.rstrip(".") for tarea in nuevas)
	prefijo = "Hecha" if len(nuevas) == 1 else "Hechas"
	return "{p}: {t}. {h} de {n}.".format(p=prefijo, t=textos, h=hechas, n=total)


# ── Subagentes ──────────────────────────────────────────────────────────────────


def subagentes(mensajes: Sequence[MensajeVista]) -> list[Subagente]:
	"""Trabajos encargados a subagentes (Task o Agent de Claude, subagentes de Codex)."""
	resultado: list[Subagente] = []
	for indice, mensaje in enumerate(mensajes):
		if mensaje.rol != "herramienta" or mensaje.herramienta not in _HERRAMIENTAS_SUBAGENTE:
			continue
		try:
			entrada_texto, salida, error = separar_resultado(mensaje.detalle or "")
			if mensaje.herramienta == "Subagente":
				instrucciones = _texto((entrada_texto or mensaje.texto or "").removeprefix("Subagente:"))
				descripcion = " ".join(instrucciones.split())[:120]
				tipo = ""
			else:
				entrada = entrada_herramienta(mensaje.detalle)
				instrucciones = _texto(entrada.get("prompt"))
				descripcion = _texto(entrada.get("description")) or _texto(mensaje.texto.partition(": ")[2])
				tipo = _texto(entrada.get("subagent_type"))
			estado = "en_marcha" if error is None else ("error" if error else "terminado")
			salida = salida.strip("\n")
			if salida.startswith(_LANZADO_EN_FONDO):
				# Subagente en segundo plano: su respuesta llega después y está en su transcript.
				estado, salida = "en_segundo_plano", ""
			resultado.append(Subagente(descripcion or "Subagente", tipo, instrucciones, salida, estado, indice,
				str(mensaje.id or "")))
		except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
			log.debug("Mensaje de subagente que no se entiende", exc_info=True)
	return resultado


def completar_subagentes(lista: Sequence[Subagente], transcritos: Iterable[Any]) -> None:
	"""Añade a cada subagente los pasos y ediciones de su transcript.

	``transcritos`` son objetos con ``id_herramienta``, ``descripcion`` y
	``mensajes`` (con ``rol``, ``texto``, ``detalle``, ``herramienta`` y
	``cambios``), como los de ``historial.claude.leer_subagentes``. Se
	emparejan por el id de la llamada y, si no, por la descripción en orden.
	"""
	pendientes = list(transcritos)
	for subagente in lista:
		elegido = next((t for t in pendientes if t.id_herramienta and t.id_herramienta == subagente.id), None)
		if elegido is None:
			elegido = next((t for t in pendientes if not (t.id_herramienta and any(s.id == t.id_herramienta for s in lista))
				and _texto(t.descripcion) == subagente.descripcion), None)
		if elegido is None:
			continue
		pendientes.remove(elegido)
		vista = [_como_vista(mensaje) for mensaje in elegido.mensajes]
		herramientas = [mensaje for mensaje in vista if mensaje.rol == "herramienta"]
		subagente.pasos = len(herramientas)
		subagente.ediciones = sum(1 for mensaje in herramientas if mensaje.herramienta in _HERRAMIENTAS_EDICION
			or (mensaje.texto or "").startswith(_PREFIJOS_EDICION))
		subagente.acciones = [" ".join((mensaje.texto or "").split())[:300] for mensaje in herramientas]
		subagente.archivos = archivos_cambiados(vista)
		subagente.ordenes = ordenes_detalladas(vista)
		if not subagente.tipo:
			subagente.tipo = _texto(getattr(elegido, "tipo", ""))
		ultimo = vista[-1] if vista else None
		if ultimo is not None and ultimo.rol == "asistente" and ultimo.texto.strip():
			if not subagente.resultado:
				subagente.resultado = ultimo.texto.strip()
			if subagente.estado == "en_segundo_plano":
				subagente.estado = "terminado"


def _como_vista(mensaje: Any) -> MensajeVista:
	if isinstance(mensaje, MensajeVista):
		return mensaje
	return MensajeVista(str(getattr(mensaje, "rol", "")), str(getattr(mensaje, "texto", "")), float(getattr(mensaje, "fecha", 0) or 0),
		str(getattr(mensaje, "detalle", "")), str(getattr(mensaje, "herramienta", "")), getattr(mensaje, "id", 0) or 0,
		list(getattr(mensaje, "cambios", []) or []))


# ── Hallazgos de una revisión de código ─────────────────────────────────────────


def _es_informe_hallazgos(nombre: str) -> bool:
	return nombre == "ReportFindings" or nombre.endswith(("__ReportFindings", ".ReportFindings"))


def _normalizar_veredicto(valor: Any) -> str:
	texto = _texto(valor).lower()
	return {"confirmed": "confirmado", "confirmado": "confirmado", "plausible": "posible", "posible": "posible"}.get(texto, texto)


def _normalizar_resultado(valor: Any) -> str:
	texto = _texto(valor).lower()
	return {"fixed": "corregido", "skipped": "omitido", "no_change_needed": "sin_cambios"}.get(texto, texto)


def _linea_entera(valor: Any) -> int:
	try:
		return max(0, int(valor))
	except (TypeError, ValueError):
		return 0


def hallazgos(mensajes: Sequence[MensajeVista]) -> list[Hallazgo]:
	"""Hallazgos de las revisiones de código de la sesión, en el orden en que se dieron.

	Si se vuelven a informar tras un pase de correcciones, el mismo hallazgo
	(archivo, línea y resumen) se actualiza con su resultado.
	"""
	resultado: list[Hallazgo] = []
	por_clave: dict[tuple[str, int, str], Hallazgo] = {}
	for indice, mensaje in enumerate(mensajes):
		if mensaje.rol != "herramienta" or not _es_informe_hallazgos(mensaje.herramienta or ""):
			continue
		entrada = entrada_herramienta(mensaje.detalle)
		lista = entrada.get("findings")
		if not isinstance(lista, list):
			continue
		for elemento in lista:
			if not isinstance(elemento, dict):
				continue
			resumen = _texto(elemento.get("summary"))
			corto = _texto(elemento.get("short_summary")) or resumen
			if not resumen and not corto:
				continue
			archivo = _texto(elemento.get("file"))
			linea = _linea_entera(elemento.get("line"))
			clave = (clave_ruta(archivo), linea, (corto or resumen).casefold())
			existente = por_clave.get(clave)
			if existente is not None:
				existente.indice = indice
				existente.veredicto = _normalizar_veredicto(elemento.get("verdict")) or existente.veredicto
				existente.resultado = _normalizar_resultado(elemento.get("outcome")) or existente.resultado
				continue
			hallazgo = Hallazgo(
				resumen or corto, archivo, linea, _texto(elemento.get("category")),
				_normalizar_veredicto(elemento.get("verdict")), _texto(elemento.get("failure_scenario")),
				_normalizar_resultado(elemento.get("outcome")), corto, indice,
			)
			por_clave[clave] = hallazgo
			resultado.append(hallazgo)
	return resultado


def nombre_veredicto(veredicto: str) -> str:
	return {"confirmado": "Confirmado", "posible": "Posible"}.get(veredicto, veredicto.capitalize() if veredicto else "")


def nombre_resultado(resultado: str) -> str:
	return {"corregido": "Corregido", "omitido": "Omitido", "sin_cambios": "No hacía falta"}.get(
		resultado, resultado.capitalize() if resultado else "")


def resumen_hallazgos(lista: Sequence[Hallazgo]) -> str:
	"""«Revisión: 5 hallazgos, 2 confirmados.» (vacío si no hay)."""
	if not lista:
		return ""
	confirmados = sum(1 for hallazgo in lista if hallazgo.veredicto == "confirmado")
	corregidos = sum(1 for hallazgo in lista if hallazgo.resultado == "corregido")
	texto = "Revisión: {n} hallazgo{s}".format(n=len(lista), s="" if len(lista) == 1 else "s")
	if confirmados:
		texto += ", {c} confirmado{s}".format(c=confirmados, s="" if confirmados == 1 else "s")
	if corregidos:
		texto += ", {c} corregido{s}".format(c=corregidos, s="" if corregidos == 1 else "s")
	return texto + "."


# ── Decisiones: preguntas y planes del agente con lo que respondiste ────────────


@dataclass(slots=True)
class Decision:
	"""Una pregunta que te hizo el agente (o un plan que te propuso) y lo que respondiste.

	``estado`` es «pendiente», «respondida», «retirada» o «sin_respuesta»;
	``documento`` es el texto completo (Markdown) para leerlo con Intro.
	"""

	tipo: str
	pregunta: str
	respuesta: str
	estado: str
	indice: int
	documento: str
	fecha: float = 0.0


#: Clases de respuesta que dicen «sí» (responder la pregunta, aprobar el plan).
_CLASES_SI = frozenset({"permitir", "permitir_sesion", "permitir_siempre"})


def _primera_linea_util(texto: str) -> str:
	for linea in (texto or "").splitlines():
		limpia = linea.strip().lstrip("#*-> ").strip()
		if limpia:
			return limpia
	return ""


def _texto_estado_decision(estado: str, tipo: str) -> str:
	if estado == "pendiente":
		return "Por aprobar" if tipo == "plan" else "Por responder"
	if estado == "retirada":
		return "Retirada por el agente"
	return "Sin respuesta"


def _respuestas_a_preguntas(entrada: dict[str, Any], respuesta: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
	"""``[(pregunta, lo que respondiste)]`` de una petición de tipo pregunta."""
	elegidas = respuesta.get("respuestas") if isinstance(respuesta.get("respuestas"), dict) else {}
	resultado = []
	for numero, pregunta in enumerate(p for p in entrada.get("preguntas") or [] if isinstance(p, dict)):
		valores = elegidas.get(str(pregunta.get("id"))) or elegidas.get(str(numero)) or []
		resultado.append((pregunta, ", ".join(str(v) for v in valores) if isinstance(valores, list) else str(valores)))
	return resultado


def _decision_de(entrada: dict[str, Any], indice: int) -> Decision | None:
	tipo = _texto(entrada.get("tipo"))
	if tipo not in ("pregunta", "plan"):
		return None
	estado = _texto(entrada.get("estado")) or "sin_respuesta"
	respuesta = entrada.get("respuesta") if isinstance(entrada.get("respuesta"), dict) else {}
	dijo_si = _texto(respuesta.get("clase")) in _CLASES_SI
	etiqueta = _texto(respuesta.get("etiqueta"))
	nota = _texto(respuesta.get("mensaje"))
	partes: list[str] = []
	if tipo == "plan":
		plan = _texto(entrada.get("plan")) or _texto(entrada.get("detalle"))
		pregunta = _primera_linea_util(plan) or _texto(entrada.get("titulo")) or "Plan"
		if estado == "respondida":
			resumen = etiqueta or ("Aprobado" if dijo_si else "Rechazado")
		else:
			resumen = _texto_estado_decision(estado, tipo)
		partes += ["# " + (_texto(entrada.get("titulo")) or "Plan que propuso el agente"), "", "## El plan", "", plan or "(sin texto)"]
		partes += ["", "## Tu respuesta", "", resumen + "."]
	else:
		pares = _respuestas_a_preguntas(entrada, respuesta)
		textos = [_texto(p.get("texto")) or _texto(p.get("encabezado")) for p, _r in pares]
		pregunta = " ".join(t for t in textos if t) or _texto(entrada.get("titulo")) or "Pregunta"
		if estado == "respondida" and dijo_si:
			elegidas = [r for _p, r in pares if r]
			resumen = "; ".join(elegidas) or nota or etiqueta or "Respondida"
		elif estado == "respondida":
			resumen = "No la respondiste" + (f" ({etiqueta})" if etiqueta else "")
		else:
			resumen = _texto_estado_decision(estado, tipo)
		partes += ["# " + (_texto(entrada.get("titulo")) or "Pregunta del agente"), ""]
		for numero, (datos_pregunta, elegida) in enumerate(pares, start=1):
			encabezado = _texto(datos_pregunta.get("encabezado"))
			partes.append("## " + (f"Pregunta {numero}: {encabezado}" if encabezado else f"Pregunta {numero}"))
			partes += ["", _texto(datos_pregunta.get("texto")), ""]
			opciones = [str(o) for o in datos_pregunta.get("opciones") or [] if str(o).strip()]
			if opciones:
				partes += ["Opciones: " + "; ".join(opciones) + ".", ""]
			if estado == "respondida" and dijo_si:
				partes += ["Tu respuesta: " + (elegida or "(ninguna)") + ".", ""]
		if not pares:
			partes += [_texto(entrada.get("detalle")) or pregunta, ""]
		if not (estado == "respondida" and dijo_si and pares):
			partes += ["## Tu respuesta", "", resumen + ".", ""]
	if nota and estado == "respondida":
		partes += ["", "## Lo que le escribiste", "", nota]
	contexto = _texto(entrada.get("contexto"))
	if contexto:
		partes += ["", "## Lo que el agente escribió antes", "", contexto]
	fecha = entrada.get("fecha")
	return Decision(tipo, pregunta, resumen, estado, indice, "\n".join(partes).strip() + "\n",
		float(fecha) if isinstance(fecha, (int, float)) else 0.0)


def decisiones(mensajes: Sequence[MensajeVista]) -> list[Decision]:
	"""Las preguntas que hizo el agente y los planes que propuso, con lo que respondiste.

	Salen de ``datos["solicitudes"]`` de cada mensaje, que el gestor guarda en
	las sesiones de Ctrl+IA desde que se apunta cada petición (las sesiones de
	otros programas no las tienen). Los permisos para herramientas no son
	decisiones de este tipo y no se incluyen.
	"""
	resultado: list[Decision] = []
	for indice, mensaje in enumerate(mensajes):
		datos = getattr(mensaje, "datos", None)
		solicitudes = datos.get("solicitudes") if isinstance(datos, dict) else None
		if not isinstance(solicitudes, list):
			continue
		for entrada in solicitudes:
			if not isinstance(entrada, dict):
				continue
			try:
				decision = _decision_de(entrada, indice)
			except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
				log.debug("Decisión guardada que no se entiende", exc_info=True)
				continue
			if decision is not None:
				resultado.append(decision)
	return resultado


# ── Ámbito: un mensaje o su turno ───────────────────────────────────────────────


def rango_turno(mensajes: Sequence[MensajeVista], indice: int) -> tuple[int, int]:
	"""``(inicio, fin)`` del turno del mensaje ``indice``: desde tu mensaje hasta el siguiente tuyo, sin incluirlo.

	Si antes no hay ningún mensaje tuyo, el turno empieza en el primero. Con un
	índice fuera de la lista, la conversación entera.
	"""
	total = len(mensajes)
	if not 0 <= indice < total:
		return 0, total
	inicio = indice
	while inicio > 0 and mensajes[inicio].rol != "usuario":
		inicio -= 1
	fin = inicio + 1
	while fin < total and mensajes[fin].rol != "usuario":
		fin += 1
	return inicio, fin


def desplazar(elementos: list[Any], inicio: int) -> list[Any]:
	"""Suma ``inicio`` al ``indice`` de cada elemento sacado de un trozo de la conversación."""
	if inicio:
		for elemento in elementos:
			elemento.indice += inicio
	return elementos


def _linea_con_texto(texto: str, ultima: bool = False) -> str:
	lineas = [" ".join(linea.split()) for linea in (texto or "").splitlines() if linea.strip()]
	if not lineas:
		return ""
	return lineas[-1] if ultima else lineas[0]


def _linea_en(texto: str, posicion: int) -> str:
	"""La línea de ``texto`` donde está ``posicion``, en una sola línea y recortada."""
	inicio = texto.rfind("\n", 0, posicion) + 1
	fin = texto.find("\n", posicion)
	linea = " ".join(texto[inicio:fin if fin >= 0 else len(texto)].split())
	return linea[:200]


# ── Errores ─────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Fallo:
	"""Algo que salió mal: una herramienta que falló (o que rechazaste) o un aviso de error."""

	titulo: str
	#: ``herramienta`` o ``aviso``.
	tipo: str
	#: Lo que se pidió a la herramienta (vacío en un aviso).
	entrada: str
	#: El error completo (la salida de la herramienta o el texto del aviso).
	detalle: str
	indice: int
	herramienta: str = ""

	@property
	def resumen(self) -> str:
		"""La línea que mejor dice el error: la última con texto (en una traza o en pytest, la que importa)."""
		ultima = _linea_con_texto(self.detalle, ultima=True)
		return "" if ultima == self.titulo else ultima[:300]


def errores(mensajes: Sequence[MensajeVista]) -> list[Fallo]:
	"""Herramientas que terminaron con error (también las órdenes y las que rechazaste) y avisos de error."""
	resultado: list[Fallo] = []
	for indice, mensaje in enumerate(mensajes):
		try:
			if mensaje.rol == "error":
				texto = "\n\n".join(parte for parte in ((mensaje.texto or "").strip(), (mensaje.detalle or "").strip()) if parte)
				if texto:
					resultado.append(Fallo(_linea_con_texto(texto)[:300], "aviso", "", texto, indice))
				continue
			if mensaje.rol != "herramienta":
				continue
			entrada, salida, error = separar_resultado(mensaje.detalle or "")
			if error is not True:
				continue
			titulo = _linea_con_texto(mensaje.texto) or mensaje.herramienta or "Herramienta"
			resultado.append(Fallo(titulo[:300], "herramienta", entrada.strip("\n"), salida.strip("\n"), indice,
				mensaje.herramienta or ""))
		except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
			log.debug("Mensaje de error que no se entiende", exc_info=True)
	return resultado


# ── Commits y subidas ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class Commit:
	"""Un commit (o una subida con git push) que hizo el agente."""

	mensaje: str
	hash: str
	rama: str
	#: ``commit`` (una orden git commit), ``subida`` (git push) u ``otro`` (un commit que
	#: aparece en la salida de otra orden de git: merge, cherry-pick, revert...).
	tipo: str
	#: ``True`` si la orden falló, ``False`` si terminó bien y ``None`` si no hay resultado.
	error: bool | None
	indice: int
	orden: str = ""
	salida: str = ""


_GIT = r"(?<![\w.-])git(?:\.exe)?(?:\s+-[cC]\s+(?:\"[^\"]*\"|'[^']*'|\S+)|\s+--?[A-Za-z][\w-]*(?:=(?:\"[^\"]*\"|'[^']*'|\S+))?)*\s+"
_RE_GIT = re.compile(r"(?<![\w.-])git(?:\.exe)?\s")
_RE_GIT_COMMIT = re.compile(_GIT + r"commit(?![\w-])")
_RE_GIT_PUSH = re.compile(_GIT + r"push(?![\w-])")
#: «[main 1a2b3c4] Mensaje», «[main (root-commit) 1a2b3c4] …», «[detached HEAD 1a2b3c4] …».
_RE_RESUMEN_COMMIT = re.compile(r"^\[(?P<rama>[^\]\n]+?)(?: \(root-commit\))? (?P<hash>[0-9a-f]{7,40})\] ?(?P<asunto>.*)$", re.M)
#: «   1a2b3c4..5d6e7f8  main -> main» y « * [new branch]      rama -> rama» de git push.
_RE_SUBIDA = re.compile(
	r"^\s*[+*!= -]?\s*(?:(?P<desde>[0-9a-f]{7,40})\.{2,3}(?P<hasta>[0-9a-f]{7,40})|\[new branch\]|\[new tag\])"
	r"\s+(?P<local>\S+)\s+->\s+(?P<remota>\S+)", re.M,
)
_RE_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?:\n|$)", re.S)
_RE_HERE_STRING = re.compile(r"@(['\"])\r?\n(.*?)\r?\n\1@", re.S)
_RE_OPCION_MENSAJE = re.compile(r"(?:^|\s)(?:-[A-Za-z]*m|--message)(?:=|\s+)")
_RE_OPCION_MENSAJE_O_ARCHIVO = re.compile(r"(?:^|\s)(?:-[A-Za-z]*m|--message|-F|--file)(?:=|\s+)")


def _partir_orden(orden: str) -> list[str]:
	"""Las órdenes sueltas de una línea de consola: corta en ``;``, ``|``, ``&&``, ``||`` y saltos de línea fuera de comillas."""
	partes: list[str] = []
	actual: list[str] = []
	comilla = ""
	posicion = 0
	total = len(orden)
	while posicion < total:
		caracter = orden[posicion]
		if comilla:
			if comilla == '"' and caracter in "\\`" and posicion + 1 < total:
				actual.append(orden[posicion:posicion + 2])
				posicion += 2
				continue
			if caracter == comilla:
				comilla = ""
		elif caracter in "\"'":
			comilla = caracter
		elif caracter in ";|\n&":
			if caracter == "&" and not orden.startswith("&&", posicion):
				actual.append(caracter)
				posicion += 1
				continue
			partes.append("".join(actual))
			actual = []
			posicion += 2 if orden.startswith(("&&", "||"), posicion) else 1
			continue
		actual.append(caracter)
		posicion += 1
	partes.append("".join(actual))
	return [parte.strip() for parte in partes if parte.strip()]


def _leer_argumento(texto: str) -> str:
	"""El primer argumento de ``texto`` sin sus comillas (``"a b" c`` → ``a b``)."""
	texto = texto.lstrip()
	if not texto:
		return ""
	if texto[0] not in "\"'":
		return texto.split(None, 1)[0]
	comilla = texto[0]
	partes: list[str] = []
	posicion = 1
	while posicion < len(texto):
		caracter = texto[posicion]
		if comilla == '"' and caracter in "\\`" and posicion + 1 < len(texto) and texto[posicion + 1] in "\"\\`$":
			partes.append(texto[posicion + 1])
			posicion += 2
			continue
		if caracter == comilla:
			break
		partes.append(caracter)
		posicion += 1
	return "".join(partes)


def mensaje_commit(orden: str) -> str:
	"""El mensaje de una orden ``git commit`` (``-m``, varios ``-m``, heredoc o here-string de PowerShell).

	Vacío si no se puede saber (``-F archivo``, el editor o una forma rara).
	"""
	coincidencia = _RE_GIT_COMMIT.search(orden or "")
	if coincidencia is None:
		return ""
	resto = orden[coincidencia.end():]
	for expresion, grupo in ((_RE_HEREDOC, 3), (_RE_HERE_STRING, 2)):
		dentro = expresion.search(resto)
		if dentro is not None and _RE_OPCION_MENSAJE_O_ARCHIVO.search(resto[:dentro.start()]):
			return dentro.group(grupo).strip()
	segmento = (_partir_orden(resto) or [""])[0] if resto.strip() else ""
	mensajes = [_leer_argumento(segmento[opcion.end():]).strip() for opcion in _RE_OPCION_MENSAJE.finditer(segmento)]
	return "\n\n".join(mensaje for mensaje in mensajes if mensaje)


def commits(mensajes: Sequence[MensajeVista]) -> list[Commit]:
	"""Commits y subidas del agente, en orden: sus órdenes ``git commit`` y ``git push`` y los commits
	que aparecen en la salida de otras órdenes de git. Un mismo hash sale una sola vez.
	"""
	resultado: list[Commit] = []
	vistos: set[str] = set()
	for orden in ordenes_detalladas(mensajes):
		try:
			texto = orden.orden
			if not _RE_GIT.search(texto):
				continue
			resumenes = list(_RE_RESUMEN_COMMIT.finditer(orden.salida))
			if _RE_GIT_COMMIT.search(texto):
				primero = resumenes.pop(0) if resumenes else None
				hash_commit = primero.group("hash") if primero else ""
				mensaje = mensaje_commit(texto) or (primero.group("asunto").strip() if primero else "")
				resultado.append(Commit(mensaje, hash_commit, primero.group("rama") if primero else "", "commit", orden.error,
					orden.indice, texto, orden.salida))
				if hash_commit:
					vistos.add(hash_commit)
			for resumen in resumenes:
				if resumen.group("hash") in vistos:
					continue
				vistos.add(resumen.group("hash"))
				resultado.append(Commit(resumen.group("asunto").strip(), resumen.group("hash"), resumen.group("rama"), "otro",
					orden.error, orden.indice, texto, orden.salida))
			if _RE_GIT_PUSH.search(texto):
				subidas = list(_RE_SUBIDA.finditer(orden.salida))
				lineas = "\n".join(" ".join(subida.group(0).split()) for subida in subidas)
				hasta = (subidas[0].group("hasta") or "") if subidas else ""
				rama = subidas[0].group("remota") if subidas else ""
				resultado.append(Commit(lineas, hasta, rama, "subida", orden.error, orden.indice, texto, orden.salida))
		except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
			log.debug("Orden de git que no se entiende", exc_info=True)
	return resultado


# ── Incidencias y PR de GitHub ──────────────────────────────────────────────────


@dataclass(slots=True)
class Referencia:
	"""Una incidencia o una PR de GitHub citada en la conversación."""

	#: «dueño/repositorio», o vacío si no se sabe.
	repositorio: str
	numero: int
	#: ``incidencia``, ``pr`` o vacío si no se sabe (en GitHub comparten numeración).
	tipo: str
	#: Dirección web (vacía si no se sabe el repositorio).
	url: str
	#: La línea donde se citó por primera vez.
	contexto: str
	indice: int

	@property
	def referencia(self) -> str:
		"""«dueño/repo#12», o «#12» si no se sabe el repositorio."""
		return f"{self.repositorio}#{self.numero}" if self.repositorio else f"#{self.numero}"


_RE_URL_GITHUB = re.compile(
	r"https?://(?:www\.)?github\.com/([A-Za-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?/(issues|pulls?)/(\d+)", re.IGNORECASE,
)
_RE_REPO_NUMERO = re.compile(r"(?<![\w/.@-])([A-Za-z0-9][\w.-]*/[\w.-]+)#(\d{1,7})(?!\w)")
_RE_NUMERO_SUELTO = re.compile(r"(?<![\w&#/])#(\d{1,6})(?![\w-])")
_RE_GH = re.compile(
	r"(?<![\w-])gh(?:\.exe)?\s+(issue|pr)\s+(?:view|close|reopen|comment|edit|merge|checkout|diff|review|ready|lock|"
	r"unlock|develop|pin|unpin|transfer|delete|checks)\s+#?(\d+)(?!\w)",
)
_RE_GH_REPO = re.compile(r"(?:--repo|-R)(?:=|\s+)['\"]?(?:https?://)?(?:github\.com/)?([A-Za-z0-9][\w.-]*/[\w.-]+?)(?:\.git)?['\"]?(?=\s|$)")
#: Un remoto de GitHub: «git@github.com:dueño/repo.git», «https://github.com/dueño/repo».
_RE_REMOTO_GITHUB = re.compile(r"github\.com[:/]([A-Za-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?(?=[\s)'\"]|$)", re.M)


def _sin_codigo_ni_enlaces(texto: str) -> str:
	"""El texto sin bloques de código, código en línea ni direcciones web (con espacios, para conservar posiciones)."""
	limpio = _sin_bloques(texto)
	for expresion in (_RE_CODIGO_EN_LINEA, _RE_URL):
		limpio = expresion.sub(lambda coincidencia: " " * len(coincidencia.group(0)), limpio)
	return limpio


def repositorio_de_sesion(mensajes: Sequence[MensajeVista], carpeta: str = "") -> str:
	"""«dueño/repo» de GitHub de la sesión; vacío si no se sabe.

	Por orden: el remoto que sale en sus órdenes de git (``git remote -v``,
	``git push``...), el de ``.git/config`` de ``carpeta`` y el más citado en
	los enlaces a incidencias y PR.
	"""
	remotos: Counter[str] = Counter()
	for orden in ordenes_detalladas(mensajes):
		if _RE_GIT.search(orden.orden):
			for coincidencia in _RE_REMOTO_GITHUB.finditer(orden.salida):
				remotos[coincidencia.group(1) + "/" + coincidencia.group(2)] += 1
	if remotos:
		return remotos.most_common(1)[0][0]
	de_carpeta = repositorio_de_carpeta(carpeta)
	if de_carpeta:
		return de_carpeta
	enlazados: Counter[str] = Counter()
	for mensaje in mensajes:
		for coincidencia in _RE_URL_GITHUB.finditer((mensaje.texto or "") + "\n" + (mensaje.detalle or "")):
			enlazados[coincidencia.group(1) + "/" + coincidencia.group(2)] += 1
	return enlazados.most_common(1)[0][0] if enlazados else ""


def repositorio_de_carpeta(carpeta: str) -> str:
	"""«dueño/repo» del remoto de GitHub de la carpeta (``origin`` si lo hay), leído de ``.git/config``; vacío si no se sabe."""
	if not carpeta or carpeta.startswith(("\\\\", "//")):
		return ""  # nunca se espera a una carpeta de red
	try:
		git = Path(carpeta) / ".git"
		if git.is_file():
			# Copia de trabajo extra (worktree) o submódulo: «gitdir: ruta».
			enlace = git.read_text(encoding="utf-8", errors="replace").strip()
			if not enlace.startswith("gitdir:"):
				return ""
			git = Path(carpeta) / enlace[len("gitdir:"):].strip()
			comun = git / "commondir"
			if comun.is_file():
				git = git / comun.read_text(encoding="utf-8", errors="replace").strip()
		config = git / "config"
		if not config.is_file() or config.stat().st_size > 256 * 1024:
			return ""
		texto = config.read_text(encoding="utf-8", errors="replace")
	except OSError:
		log.debug("No se pudo leer la configuración de git de %s", carpeta, exc_info=True)
		return ""
	remotos: list[tuple[str, str]] = []
	actual = ""
	for linea in texto.splitlines():
		limpia = linea.strip()
		cabecera = re.match(r"\[remote \"([^\"]+)\"\]", limpia)
		if cabecera:
			actual = cabecera.group(1)
			continue
		if limpia.startswith("["):
			actual = ""
			continue
		if actual and re.match(r"url\s*=", limpia):
			coincidencia = _RE_REMOTO_GITHUB.search(limpia.split("=", 1)[1].strip())
			if coincidencia:
				remotos.append((actual, coincidencia.group(1) + "/" + coincidencia.group(2)))
	elegido = next((repo for nombre, repo in remotos if nombre == "origin"), remotos[0][1] if remotos else "")
	return elegido


def incidencias(mensajes: Sequence[MensajeVista], repositorio: str = "") -> list[Referencia]:
	"""Incidencias y PR de GitHub citadas, una vez cada una, en el orden en que aparecen.

	Salen de los enlaces ``github.com/dueño/repo/issues/N`` y ``/pull/N`` (en
	cualquier mensaje, también en la salida de las órdenes), de ``dueño/repo#N``
	y ``#N`` en tus mensajes y en los del agente (fuera del código) y de las
	órdenes ``gh issue …`` y ``gh pr …``. Un ``#N`` suelto es del repositorio
	``repositorio`` o, si no se da, del que se deduce de la sesión.
	"""
	repo_sesion = repositorio or repositorio_de_sesion(mensajes)
	resultado: list[Referencia] = []
	por_clave: dict[tuple[str, int], Referencia] = {}

	def anadir(repo: str, numero: str, tipo: str, contexto: str, indice: int) -> None:
		repo = repo.strip().strip("/")
		repo = repo[:-4] if repo.lower().endswith(".git") else repo
		valor = int(numero)
		if valor <= 0:
			return
		clave = (repo.casefold(), valor)
		existente = por_clave.get(clave)
		if existente is not None:
			if tipo and not existente.tipo:
				existente.tipo = tipo
				existente.url = _url_github(repo, valor, tipo)
			return
		referencia = Referencia(repo, valor, tipo, _url_github(repo, valor, tipo), contexto, indice)
		por_clave[clave] = referencia
		resultado.append(referencia)

	for indice, mensaje in enumerate(mensajes):
		try:
			texto = mensaje.texto or ""
			encontrados: list[tuple[int, str, str, str, str]] = []
			fuentes = [texto] + ([mensaje.detalle] if mensaje.detalle else [])
			for numero_fuente, fuente in enumerate(fuentes):
				for coincidencia in _RE_URL_GITHUB.finditer(fuente):
					tipo = "incidencia" if coincidencia.group(3).lower() == "issues" else "pr"
					encontrados.append((numero_fuente * 10**9 + coincidencia.start(), coincidencia.group(1) + "/" + coincidencia.group(2),
						coincidencia.group(4), tipo, _linea_en(fuente, coincidencia.start())))
			if mensaje.rol in ROLES_CON_CODIGO and "#" in texto:
				limpio = _sin_codigo_ni_enlaces(texto)
				for coincidencia in _RE_REPO_NUMERO.finditer(limpio):
					encontrados.append((coincidencia.start(), coincidencia.group(1), coincidencia.group(2), "",
						_linea_en(texto, coincidencia.start())))
				for coincidencia in _RE_NUMERO_SUELTO.finditer(limpio):
					encontrados.append((coincidencia.start(), repo_sesion, coincidencia.group(1), "", _linea_en(texto, coincidencia.start())))
			if mensaje.rol == "herramienta" and "gh" in texto:
				orden = _orden_del_detalle(mensaje.detalle or "") or texto
				for parte in _partir_orden(orden):
					for coincidencia in _RE_GH.finditer(parte):
						repo = _RE_GH_REPO.search(parte)
						encontrados.append((coincidencia.start(), repo.group(1) if repo else repo_sesion, coincidencia.group(2),
							"pr" if coincidencia.group(1) == "pr" else "incidencia", " ".join(parte.split())[:200]))
			encontrados.sort(key=lambda elemento: elemento[0])
			for _posicion, repo, numero, tipo, contexto in encontrados:
				anadir(repo, numero, tipo, contexto, indice)
		except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
			log.debug("Mensaje con incidencias que no se entiende", exc_info=True)
	return resultado


def _url_github(repositorio: str, numero: int, tipo: str) -> str:
	if not repositorio:
		return ""
	# GitHub lleva de /issues/N a /pull/N si en realidad es una PR.
	return "https://github.com/{r}/{c}/{n}".format(r=repositorio, c="pull" if tipo == "pr" else "issues", n=numero)


# ── Archivos leídos ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Lectura:
	"""Un archivo que leyó el agente (una vez por ruta)."""

	ruta: str
	#: El último mensaje que lo leyó.
	indice: int
	veces: int = 1
	#: Con qué lo leyó: la herramienta (Read, read_file...) o la orden (Get-Content, cat...).
	como: str = ""
	#: Línea desde la que leyó (si la herramienta lo dice).
	linea: int = 0


_HERRAMIENTAS_LECTURA = frozenset({"Read", "read_file", "ReadFile", "read", "view_file", "open_file", "NotebookRead", "Leer"})
_PREFIJOS_LECTURA = ("Leer archivo:", "Leer:")
_CLAVES_RUTA_LECTURA = ("file_path", "path", "notebook_path", "absolute_path", "AbsolutePath", "target_file", "TargetFile",
	"filename")
_RE_ARCHIVOS_ACP = re.compile(r"(?:^|\. )Archivos?: (.+)$", re.M)
#: Órdenes que leen archivos, con sus opciones que llevan un valor detrás.
_LECTORES: dict[str, tuple[str, frozenset[str]]] = {
	"get-content": ("Get-Content", frozenset({"-totalcount", "-tail", "-head", "-first", "-last", "-encoding", "-delimiter",
		"-readcount", "-stream"})),
	"gc": ("Get-Content", frozenset({"-totalcount", "-tail", "-head", "-first", "-last", "-encoding", "-delimiter",
		"-readcount", "-stream"})),
	"cat": ("cat", frozenset()),
	"type": ("type", frozenset()),
	"nl": ("nl", frozenset({"-w", "-s", "-b", "-v", "-i"})),
	"head": ("head", frozenset({"-n", "-c"})),
	"tail": ("tail", frozenset({"-n", "-c"})),
	"less": ("less", frozenset()),
	"more": ("more", frozenset()),
	"sed": ("sed", frozenset({"-e"})),
}
#: Intérpretes que reciben la orden de verdad como argumento (Codex en Windows usa PowerShell).
_INTERPRETES = frozenset({"powershell", "pwsh", "bash", "sh", "zsh", "cmd"})


def _tokens(orden: str) -> list[str]:
	try:
		tokens = shlex.split(orden, posix=False)
	except ValueError:
		tokens = orden.split()
	resultado = []
	for token in tokens:
		if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
			token = token[1:-1]
		resultado.append(token)
	return resultado


def _parece_archivo(token: str) -> bool:
	if not token or token.startswith(("-", "$", "%", "<", ">", "@", "(", "{", "~")) or any(c in token for c in "*?|"):
		return False
	return any(c in token for c in "./\\")


def _lecturas_de_orden(orden: str, profundidad: int = 0) -> list[tuple[str, str]]:
	"""``[(ruta, programa)]`` de los archivos que lee una orden de consola (Get-Content, cat, sed -n...)."""
	resultado: list[tuple[str, str]] = []
	for parte in _partir_orden(orden):
		tokens = _tokens(parte)
		if not tokens:
			continue
		programa = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
		if programa in _INTERPRETES and profundidad < 2:
			for posicion, token in enumerate(tokens[1:-1], start=1):
				if token.lower() in ("-command", "-c", "-lc", "/c", "/k"):
					resultado += _lecturas_de_orden(" ".join(tokens[posicion + 1:]), profundidad + 1)
					break
			continue
		if programa not in _LECTORES or any(t.startswith(">") or t == "1>" for t in tokens[1:]):
			continue  # «cat > archivo» escribe, no lee
		nombre, con_valor = _LECTORES[programa]
		posicionales: list[str] = []
		saltar = False
		for token in tokens[1:]:
			if saltar:
				saltar = False
				continue
			minuscula = token.lower()
			if programa in ("get-content", "gc") and minuscula in ("-path", "-literalpath", "-lp"):
				saltar = True  # lo que va detrás lo recoge _rutas_tras_path
				continue
			if minuscula in con_valor:
				saltar = True
				continue
			if token.startswith("-") or token.startswith("2>"):
				continue
			posicionales.append(token)
		if programa in ("get-content", "gc"):
			posicionales = _rutas_tras_path(tokens[1:]) + posicionales
		if programa == "sed":
			if not any(t in ("-n", "--quiet", "--silent") for t in tokens[1:]):
				continue
			posicionales = posicionales[1:] if "-e" not in (t.lower() for t in tokens[1:]) else posicionales
		for token in posicionales:
			ruta = token.strip().strip("\"'").rstrip(";,)")
			if _parece_archivo(ruta):
				resultado.append((ruta, nombre))
	return resultado


def _rutas_tras_path(tokens: list[str]) -> list[str]:
	rutas = []
	for posicion, token in enumerate(tokens[:-1]):
		if token.lower() in ("-path", "-literalpath", "-lp"):
			rutas += [ruta.strip() for ruta in tokens[posicion + 1].split(",") if ruta.strip()]
	return rutas


def _rutas_de_herramienta_lectura(mensaje: MensajeVista) -> tuple[list[str], int]:
	"""Rutas que lee una herramienta de lectura y la línea desde la que lee (0 si no se sabe)."""
	entrada = entrada_herramienta(mensaje.detalle)
	for clave in _CLAVES_RUTA_LECTURA:
		valor = entrada.get(clave)
		if isinstance(valor, str) and valor.strip():
			return [valor.strip()], _linea_entera(entrada.get("offset"))
	if mensaje.herramienta == "Leer":
		# ACP: «Título. Archivo: ruta (línea 3)» o «Archivos: a, b y 2 más».
		coincidencia = _RE_ARCHIVOS_ACP.search(separar_resultado(mensaje.detalle or "")[0])
		if coincidencia is None:
			return [], 0
		rutas = []
		for trozo in re.sub(r" y \d+ más$", "", coincidencia.group(1)).split(", "):
			ruta = trozo.partition(" (línea ")[0]
			if ruta.strip():
				rutas.append(ruta.strip())
		return rutas, 0
	ruta = _ruta_tras_dos_puntos(mensaje.texto)
	return ([ruta] if ruta else []), 0


def archivos_leidos(mensajes: Sequence[MensajeVista]) -> list[Lectura]:
	"""Archivos que leyó el agente, uno por ruta, en el orden de la primera lectura.

	De sus herramientas de lectura (Read de Claude, read_file, Leer de los
	agentes ACP) y de las órdenes de consola que leen archivos (Get-Content,
	cat, type, head, tail, sed -n...). ``indice`` es el de la última lectura.
	"""
	resultado: list[Lectura] = []
	por_ruta: dict[str, Lectura] = {}

	def anadir(ruta: str, como: str, indice: int, linea: int = 0) -> None:
		clave = clave_ruta(ruta)
		existente = por_ruta.get(clave)
		if existente is None:
			lectura = Lectura(ruta, indice, 1, como, linea)
			por_ruta[clave] = lectura
			resultado.append(lectura)
			return
		existente.veces += 1
		existente.indice = max(existente.indice, indice)
		if como and como not in existente.como.split(", "):
			existente.como = existente.como + ", " + como if existente.como else como

	for indice, mensaje in enumerate(mensajes):
		if mensaje.rol != "herramienta":
			continue
		try:
			texto = (mensaje.texto or "").strip()
			if mensaje.herramienta in _HERRAMIENTAS_LECTURA or texto.startswith(_PREFIJOS_LECTURA):
				rutas, linea = _rutas_de_herramienta_lectura(mensaje)
				for ruta in rutas:
					anadir(ruta, mensaje.herramienta or "Leer", indice, linea)
				continue
			if texto.startswith(_PREFIJOS_ORDEN):
				orden = _orden_del_detalle(mensaje.detalle or "") or texto.partition(": ")[2].strip()
				for ruta, programa in _lecturas_de_orden(orden):
					anadir(ruta, programa, indice)
		except Exception:  # noqa: BLE001 - una forma rara nunca debe romper las listas
			log.debug("Lectura de archivo que no se entiende", exc_info=True)
	return resultado


# ── Tus mensajes ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class MensajeUsuario:
	"""Uno de tus mensajes."""

	texto: str
	indice: int
	fecha: float = 0.0
	#: Su número entre tus mensajes (1, 2, 3...).
	numero: int = 0


def mensajes_usuario(mensajes: Sequence[MensajeVista]) -> list[MensajeUsuario]:
	"""Tus mensajes con texto, en orden."""
	resultado: list[MensajeUsuario] = []
	for indice, mensaje in enumerate(mensajes):
		if mensaje.rol == "usuario" and (mensaje.texto or "").strip():
			resultado.append(MensajeUsuario(mensaje.texto, indice, float(mensaje.fecha or 0), len(resultado) + 1))
	return resultado
