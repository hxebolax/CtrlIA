"""Compara los atajos de teclado del código con los documentados en ``docs/teclas.md``.

Uso (desde la raíz del proyecto)::

	python tools/comprobar_teclas.py
	python tools/comprobar_teclas.py --teclas docs/teclas.md --contexto "Ventana principal=VentanaPrincipal"

Informa, en texto plano que se lee bien con un lector de pantalla, de:

1. Atajos del código que no aparecen en la documentación.
2. Atajos documentados que ya no existen en el código.
3. Atajos duplicados en la misma ventana (el segundo nunca se ejecutará o
   se ejecutará el que no se espera), en el código o en la documentación.

Termina con código 1 si hay algún problema (útil en un .bat o antes de publicar).

Qué atajos encuentra en el código (``app/`` y ``main.py``):

- Textos con tabulador y combinación, como en los menús: ``"&Guardar\\tCtrl+S"``.
- ``wx.AcceleratorEntry(wx.ACCEL_CTRL, ord("S"), id)`` y tuplas
  ``(wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_F5, id)`` de ``wx.AcceleratorTable``.
- Comentarios ``# atajo: Ctrl+Mayús+L`` para las teclas que se atienden a
  mano en ``EVT_CHAR_HOOK`` o ``EVT_KEY_DOWN`` (el análisis no puede
  deducirlas). Admiten rangos: ``# atajo: Alt+1 a Alt+9``.

La «ventana» de un atajo es la clase donde aparece (o el módulo si está
fuera de una clase). Cada sección de ``teclas.md`` se relaciona con clases
mediante ``CONTEXTOS``; una sección sin relacionar se compara con todo el
código.

Solo biblioteca estándar. PLANTILLA del agente «documentacion-usuario».
"""

from __future__ import annotations

import argparse
import ast
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
ORIGENES = ["app", "main.py"]
EXCLUIDAS = {"__pycache__", "tests", "venv", ".venv", "build", "data"}
TECLAS_POR_DEFECTO = ["docs/teclas.md", "data/docs/teclas.md"]

# Sección de teclas.md → clases o módulos del código que documenta.
# Ejemplo: {"Ventana principal": ["VentanaPrincipal"], "Editor": ["DialogoEditor"]}
CONTEXTOS: dict[str, list[str]] = {}

# Secciones cuyo contenido no se compara con el código: teclas del sistema,
# del lector de pantalla o de los controles estándar. Se busca el texto en
# el título, sin distinguir mayúsculas ni tildes.
SECCIONES_SIN_COMPROBAR = ["cualquier dialogo", "todos los dialogos", "lector de pantalla", "controles estandar"]

# Teclas que proporcionan Windows o los controles estándar y que se pueden
# documentar aunque la aplicación no las defina.
TECLAS_DEL_SISTEMA = {
	"Tab", "Mayús+Tab", "Ctrl+Tab", "Ctrl+Mayús+Tab", "Intro", "Escape", "Espacio", "Alt+F4", "F10",
	"Aplicaciones", "Mayús+F10", "Arriba", "Abajo", "Izquierda", "Derecha", "Inicio", "Fin", "RePág",
	"AvPág", "Ctrl+Inicio", "Ctrl+Fin", "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+A", "Ctrl+Z", "Supr", "Retroceso",
}

_MODIFICADORES = {
	"ctrl": "Ctrl", "control": "Ctrl", "rawctrl": "Ctrl", "cmd": "Ctrl", "strg": "Ctrl",
	"alt": "Alt", "shift": "Mayús", "mayús": "Mayús", "mayus": "Mayús", "mayúsculas": "Mayús", "mayusculas": "Mayús",
	"win": "Win", "windows": "Win",
}
_ORDEN_MODIFICADORES = ["Ctrl", "Alt", "Mayús", "Win"]
_TECLAS = {
	"del": "Supr", "delete": "Supr", "supr": "Supr", "suprimir": "Supr",
	"enter": "Intro", "return": "Intro", "intro": "Intro", "entrar": "Intro",
	"esc": "Escape", "escape": "Escape",
	"back": "Retroceso", "backspace": "Retroceso", "retroceso": "Retroceso",
	"space": "Espacio", "espacio": "Espacio",
	"tab": "Tab", "tabulador": "Tab",
	"home": "Inicio", "inicio": "Inicio", "end": "Fin", "fin": "Fin",
	"pgup": "RePág", "pageup": "RePág", "prior": "RePág", "repág": "RePág", "repag": "RePág",
	"pgdn": "AvPág", "pagedown": "AvPág", "next": "AvPág", "avpág": "AvPág", "avpag": "AvPág",
	"ins": "Insertar", "insert": "Insertar", "insertar": "Insertar",
	"up": "Arriba", "arriba": "Arriba", "down": "Abajo", "abajo": "Abajo",
	"left": "Izquierda", "izquierda": "Izquierda", "right": "Derecha", "derecha": "Derecha",
	"apps": "Aplicaciones", "aplicaciones": "Aplicaciones", "menu": "Aplicaciones",
	"coma": ",", "comma": ",", "punto": ".", "period": ".", "plus": "+", "más": "+", "minus": "-", "menos": "-",
}
# Constantes wx.WXK_* → nombre de tecla.
_WXK = {
	"WXK_DELETE": "Supr", "WXK_RETURN": "Intro", "WXK_NUMPAD_ENTER": "Intro", "WXK_ESCAPE": "Escape",
	"WXK_BACK": "Retroceso", "WXK_SPACE": "Espacio", "WXK_TAB": "Tab", "WXK_HOME": "Inicio", "WXK_END": "Fin",
	"WXK_PAGEUP": "RePág", "WXK_PAGEDOWN": "AvPág", "WXK_INSERT": "Insertar", "WXK_UP": "Arriba",
	"WXK_DOWN": "Abajo", "WXK_LEFT": "Izquierda", "WXK_RIGHT": "Derecha", "WXK_WINDOWS_MENU": "Aplicaciones",
}
_FLAGS = {"ACCEL_CTRL": "Ctrl", "ACCEL_RAW_CTRL": "Ctrl", "ACCEL_CMD": "Ctrl", "ACCEL_ALT": "Alt", "ACCEL_SHIFT": "Mayús"}
_METODOS_MENU = {"Append": 0, "Prepend": 0, "AppendCheckItem": 0, "AppendRadioItem": 0, "Insert": 1, "MenuItem": 1}
_IDS_ANONIMOS = {"-1", "wx.ID_ANY", "ID_ANY", "wx.NewIdRef()", "wx.NewId()"}

_NOMBRES_TECLA = "|".join(
	sorted({re.escape(k) for k in _TECLAS if len(k) > 1} | {r"F\d{1,2}"}, key=len, reverse=True)
)
_NOMBRES_MOD = "|".join(sorted((re.escape(k) for k in _MODIFICADORES), key=len, reverse=True))
# Una combinación dentro de un texto de la documentación.
_RE_COMBINACION = re.compile(
	rf"(?<![\w+])((?:(?:{_NOMBRES_MOD})\s*\+\s*)+(?:{_NOMBRES_TECLA}|[^\s+|;]|\+)|(?:{_NOMBRES_TECLA}))(?![\w])",
	re.IGNORECASE,
)
_RE_RANGO = re.compile(
	rf"((?:(?:{_NOMBRES_MOD})\s*\+\s*)*)(F?\w)\s+a\s+((?:(?:{_NOMBRES_MOD})\s*\+\s*)*)(F?\w)\b", re.IGNORECASE
)
_RE_MARCA_ATAJO = re.compile(r"#\s*atajo:\s*(.+)$", re.IGNORECASE)
_RE_ENCABEZADO = re.compile(r"^(#{2,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Atajo:
	combinacion: str
	contexto: str
	archivo: str
	linea: int
	descripcion: str = ""
	# Identificadores de la acción (id del menú, texto): dos atajos con alguno
	# en común son la MISMA acción declarada dos veces (menú + acelerador).
	destinos: set[str] = field(default_factory=set)

	def donde(self) -> str:
		return f"{self.archivo}:{self.linea}"


@dataclass
class Resultado:
	en_codigo: list[Atajo] = field(default_factory=list)
	documentados: list[Atajo] = field(default_factory=list)
	no_documentados: list[Atajo] = field(default_factory=list)
	obsoletos: list[Atajo] = field(default_factory=list)
	duplicados: list[list[Atajo]] = field(default_factory=list)
	ruta_teclas: Path | None = None

	@property
	def problemas(self) -> int:
		return len(self.no_documentados) + len(self.obsoletos) + len(self.duplicados)


# ── Normalización ────────────────────────────────────────────────────────────

def _sin_tildes(texto: str) -> str:
	return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").casefold()


def normalizar(combinacion: str) -> str | None:
	"""``ctrl+shift+s``, ``Ctrl-Mayús-S`` o ``Mayús+Ctrl+S`` → ``Ctrl+Mayús+S``. None si no es válida."""
	texto = combinacion.strip()
	if not texto:
		return None
	if texto.endswith("++"):
		partes = [p for p in texto[:-2].split("+") if p] + ["+"]
	elif "+" in texto:
		partes = texto.split("+")
	elif "-" in texto[1:]:
		partes = texto.split("-")
	else:
		partes = [texto]
	partes = [p.strip() for p in partes]
	*modificadores, tecla = partes
	nombres_mod: set[str] = set()
	for modificador in modificadores:
		nombre = _MODIFICADORES.get(modificador.casefold())
		if nombre is None:
			return None
		nombres_mod.add(nombre)
	clave = tecla.casefold()
	if clave in _TECLAS:
		tecla = _TECLAS[clave]
	elif re.fullmatch(r"f([1-9]|1\d|2[0-4])", clave):
		tecla = clave.upper()
	elif len(tecla) == 1 and not tecla.isspace():
		tecla = tecla.upper()
	else:
		return None
	return "+".join([m for m in _ORDEN_MODIFICADORES if m in nombres_mod] + [tecla])


def combinaciones_en_texto(texto: str, permitir_sueltas: bool = False) -> list[str]:
	"""Combinaciones de un texto libre (expande «Alt+1 a Alt+9»)."""
	encontradas: list[str] = []
	for rango in _RE_RANGO.finditer(texto):
		mods_ini, ini, mods_fin, fin = rango.groups()
		if normalizar(mods_ini + "x") and _sin_tildes(mods_ini.replace(" ", "")) == _sin_tildes(mods_fin.replace(" ", "")):
			if len(ini) == 1 and len(fin) == 1 and ini.isalnum() and fin.isalnum() and ord(ini) <= ord(fin):
				for codigo in range(ord(ini.upper()), ord(fin.upper()) + 1):
					combinacion = normalizar(mods_ini + chr(codigo))
					if combinacion:
						encontradas.append(combinacion)
				texto = texto.replace(rango.group(0), " ")
	for coincidencia in _RE_COMBINACION.finditer(texto):
		bruto = coincidencia.group(1)
		# Una tecla sin modificadores solo cuenta escrita con mayúscula inicial
		# («Inicio», «F5»): así «ir al inicio» en una descripción no es un atajo.
		if "+" not in bruto and not bruto[0].isupper():
			continue
		combinacion = normalizar(re.sub(r"\s*\+\s*", "+", bruto))
		if combinacion:
			encontradas.append(combinacion)
	if not encontradas and permitir_sueltas:
		limpio = texto.strip().strip("*`").strip()
		combinacion = normalizar(limpio) if len(limpio) == 1 else None
		if combinacion:
			encontradas.append(combinacion)
	return list(dict.fromkeys(encontradas))


# ── Código ───────────────────────────────────────────────────────────────────

def _contexto_por_linea(arbol: ast.AST, modulo: str):
	clases = [
		(n.lineno, n.end_lineno or n.lineno, n.name) for n in ast.walk(arbol) if isinstance(n, ast.ClassDef)
	]

	def contexto(linea: int) -> str:
		# La clase más interior que contiene la línea.
		candidatas = [c for c in clases if c[0] <= linea <= c[1]]
		return max(candidatas, key=lambda c: c[0])[2] if candidatas else modulo

	return contexto


def _tecla_de_nodo(nodo: ast.AST) -> str | None:
	if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id == "ord" and nodo.args:
		argumento = nodo.args[0]
		if isinstance(argumento, ast.Constant) and isinstance(argumento.value, str) and len(argumento.value) == 1:
			return argumento.value.upper()
	if isinstance(nodo, ast.Constant) and isinstance(nodo.value, int) and 32 < nodo.value < 127:
		return chr(nodo.value).upper()
	nombre = nodo.attr if isinstance(nodo, ast.Attribute) else getattr(nodo, "id", "")
	if nombre in _WXK:
		return _WXK[nombre]
	coincidencia = re.fullmatch(r"WXK_F(\d{1,2})", nombre or "")
	if coincidencia:
		return f"F{coincidencia.group(1)}"
	return None


def _modificadores_de_nodo(nodo: ast.AST) -> list[str] | None:
	"""Modificadores de una expresión ``wx.ACCEL_CTRL | wx.ACCEL_SHIFT``; None si no es de ese tipo."""
	nombres = [n.attr if isinstance(n, ast.Attribute) else n.id for n in ast.walk(nodo) if isinstance(n, (ast.Attribute, ast.Name))]
	accel = [n for n in nombres if n.startswith("ACCEL_")]
	if not accel:
		return None
	return [_FLAGS[n] for n in accel if n in _FLAGS]


def _entrada_acelerador(argumentos: list[ast.AST], contexto: str, archivo: str, linea: int) -> Atajo | None:
	if len(argumentos) < 2:
		return None
	modificadores = _modificadores_de_nodo(argumentos[0])
	tecla = _tecla_de_nodo(argumentos[1])
	if modificadores is None or tecla is None:
		return None
	combinacion = normalizar("+".join([*modificadores, tecla]))
	if combinacion is None:
		return None
	destinos = {ast.unparse(argumentos[2])} if len(argumentos) > 2 else set()
	return Atajo(combinacion, contexto, archivo, linea, destinos=destinos - _IDS_ANONIMOS)


def _id_de_menu(llamada: ast.Call) -> str | None:
	nombre = llamada.func.attr if isinstance(llamada.func, ast.Attribute) else getattr(llamada.func, "id", "")
	for palabra in llamada.keywords:
		if palabra.arg == "id":
			return ast.unparse(palabra.value)
	posicion = _METODOS_MENU.get(nombre)
	if posicion is not None and len(llamada.args) > posicion:
		return ast.unparse(llamada.args[posicion])
	return None


def atajos_de_codigo(codigo: str, archivo: str, modulo: str) -> list[Atajo]:
	try:
		arbol = ast.parse(codigo, filename=archivo)
	except SyntaxError:
		log.warning("No se pudo analizar %s", archivo, exc_info=True)
		return []
	contexto = _contexto_por_linea(arbol, modulo)
	padres = {hijo: padre for padre in ast.walk(arbol) for hijo in ast.iter_child_nodes(padre)}
	atajos: list[Atajo] = []

	def id_de_menu_cercano(nodo: ast.AST) -> str | None:
		"""Id de la llamada de menú más cercana que contiene el texto (atraviesa _() y Bind)."""
		actual = padres.get(nodo)
		while actual is not None and not isinstance(actual, ast.stmt):
			if isinstance(actual, ast.Call):
				identificador = _id_de_menu(actual)
				if identificador is not None:
					return identificador
			actual = padres.get(actual)
		return None

	for nodo in ast.walk(arbol):
		if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and "\t" in nodo.value:
			etiqueta, _, resto = nodo.value.rpartition("\t")
			combinacion = normalizar(resto)
			# Una sola letra tras un tabulador suele ser texto en columnas, no un atajo.
			if combinacion is None or ("+" not in combinacion and len(combinacion) == 1):
				continue
			etiqueta = etiqueta.replace("&&", "\0").replace("&", "").replace("\0", "&").strip()
			id_menu = id_de_menu_cercano(nodo)
			destinos = {etiqueta} | ({id_menu} if id_menu else set())
			atajos.append(Atajo(combinacion, contexto(nodo.lineno), archivo, nodo.lineno, etiqueta, destinos - _IDS_ANONIMOS))
		elif isinstance(nodo, ast.Call):
			nombre = nodo.func.attr if isinstance(nodo.func, ast.Attribute) else getattr(nodo.func, "id", "")
			if nombre in ("AcceleratorEntry", "Set"):
				argumentos = list(nodo.args) + [k.value for k in nodo.keywords]
				atajo = _entrada_acelerador(argumentos, contexto(nodo.lineno), archivo, nodo.lineno)
				if atajo:
					atajos.append(atajo)
		elif isinstance(nodo, ast.Tuple) and len(nodo.elts) == 3:
			atajo = _entrada_acelerador(nodo.elts, contexto(nodo.lineno), archivo, nodo.lineno)
			if atajo:
				atajos.append(atajo)
	for numero, linea in enumerate(codigo.splitlines(), start=1):
		marca = _RE_MARCA_ATAJO.search(linea)
		if marca:
			descripcion = _RE_COMBINACION.sub("", _RE_RANGO.sub("", marca.group(1))).strip(" :-=—()")
			for combinacion in combinaciones_en_texto(marca.group(1)):
				atajos.append(Atajo(combinacion, contexto(numero), archivo, numero, descripcion, {f"marca:{numero}"}))
	atajos.sort(key=lambda a: a.linea)
	return atajos


def atajos_del_proyecto(raiz: Path, origenes: list[str] | None = None) -> list[Atajo]:
	todos: list[Atajo] = []
	for origen in origenes or ORIGENES:
		ruta = raiz / origen
		archivos = [ruta] if ruta.is_file() else sorted(ruta.rglob("*.py")) if ruta.is_dir() else []
		for archivo in archivos:
			relativo = archivo.relative_to(raiz)
			if EXCLUIDAS.intersection(relativo.parts):
				continue
			try:
				codigo = archivo.read_text(encoding="utf-8")
			except (OSError, UnicodeDecodeError):
				log.warning("No se pudo leer %s", archivo, exc_info=True)
				continue
			todos.extend(atajos_de_codigo(codigo, relativo.as_posix(), relativo.with_suffix("").as_posix()))
	return todos


# ── Documentación ────────────────────────────────────────────────────────────

def _celdas(linea: str) -> list[str]:
	return [c.strip() for c in linea.strip().strip("|").split("|")]


def atajos_documentados(texto: str, archivo: str = "teclas.md") -> list[Atajo]:
	"""Atajos de ``teclas.md``: primera columna de las tablas o inicio de los elementos de lista."""
	atajos: list[Atajo] = []
	seccion = "(sin sección)"
	en_tabla_de_teclas = False
	cabecera_vista = False
	for numero, linea in enumerate(texto.splitlines(), start=1):
		encabezado = _RE_ENCABEZADO.match(linea)
		if encabezado:
			seccion = re.sub(r"^\d+(\.\d+)*\.?\s+", "", encabezado.group(2).replace("*", "")).strip()
			en_tabla_de_teclas = cabecera_vista = False
			continue
		limpia = linea.strip()
		if limpia.startswith("|"):
			celdas = _celdas(limpia)
			if not cabecera_vista:
				cabecera_vista = True
				en_tabla_de_teclas = bool(re.search(r"atajo|tecla|combinaci", _sin_tildes(celdas[0])))
				continue
			if not en_tabla_de_teclas or re.fullmatch(r":?-{2,}:?", celdas[0].replace(" ", "")):
				continue
			descripcion = celdas[1] if len(celdas) > 1 else ""
			for combinacion in combinaciones_en_texto(celdas[0].replace("**", "").replace("`", ""), permitir_sueltas=True):
				atajos.append(Atajo(combinacion, seccion, archivo, numero, descripcion))
			continue
		cabecera_vista = en_tabla_de_teclas = False
		elemento = re.match(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$", linea)
		if not elemento:
			continue
		contenido = elemento.group(1)
		negritas = re.findall(r"\*\*(.+?)\*\*", contenido)
		if negritas:
			parte_teclas = " ".join(negritas)
		elif ":" in contenido:
			parte_teclas = contenido.split(":", 1)[0]
		else:
			continue
		descripcion = re.sub(r"\*\*.+?\*\*", "", contenido).strip(" :-—")
		for combinacion in combinaciones_en_texto(parte_teclas.replace("`", "")):
			atajos.append(Atajo(combinacion, seccion, archivo, numero, descripcion))
	return atajos


# ── Comparación ──────────────────────────────────────────────────────────────

def _grupos_por_accion(atajos: list[Atajo]) -> list[list[Atajo]]:
	"""Agrupa los atajos que comparten algún destino (son la misma acción)."""
	grupos: list[tuple[set[str], list[Atajo]]] = []
	for atajo in atajos:
		destinos = atajo.destinos or {f"{atajo.archivo}:{atajo.linea}"}
		unidos = [g for g in grupos if g[0] & destinos]
		nuevo = (set(destinos), [atajo])
		for grupo in unidos:
			nuevo[0].update(grupo[0])
			nuevo[1].extend(grupo[1])
			grupos.remove(grupo)
		grupos.append(nuevo)
	return [sorted(g[1], key=lambda a: (a.archivo, a.linea)) for g in grupos]


def _clave_seccion(titulo: str) -> str:
	return _sin_tildes(titulo).strip()


def comprobar(raiz: Path, ruta_teclas: Path | None = None, contextos: dict[str, list[str]] | None = None) -> Resultado:
	resultado = Resultado()
	if ruta_teclas is None:
		ruta_teclas = next((raiz / r for r in TECLAS_POR_DEFECTO if (raiz / r).is_file()), raiz / TECLAS_POR_DEFECTO[0])
	resultado.ruta_teclas = ruta_teclas
	mapa = {_clave_seccion(k): v for k, v in (CONTEXTOS if contextos is None else contextos).items()}
	resultado.en_codigo = atajos_del_proyecto(raiz)
	if ruta_teclas.is_file():
		relativo = ruta_teclas.relative_to(raiz).as_posix() if ruta_teclas.is_relative_to(raiz) else str(ruta_teclas)
		resultado.documentados = atajos_documentados(ruta_teclas.read_text(encoding="utf-8"), relativo)

	def exenta(seccion: str) -> bool:
		clave = _clave_seccion(seccion)
		return any(_sin_tildes(texto) in clave for texto in SECCIONES_SIN_COMPROBAR)

	# Contextos de código relacionados con cada sección y al revés.
	secciones_de_contexto: dict[str, set[str]] = {}
	for seccion, clases in mapa.items():
		for clase in clases:
			secciones_de_contexto.setdefault(clase, set()).add(seccion)
	doc_por_seccion: dict[str, set[str]] = {}
	for atajo in resultado.documentados:
		doc_por_seccion.setdefault(_clave_seccion(atajo.contexto), set()).add(atajo.combinacion)
	todas_doc = {a.combinacion for a in resultado.documentados}
	codigo_por_contexto: dict[str, set[str]] = {}
	for atajo in resultado.en_codigo:
		codigo_por_contexto.setdefault(atajo.contexto, set()).add(atajo.combinacion)
	todo_codigo = {a.combinacion for a in resultado.en_codigo}

	vistos: set[tuple[str, str]] = set()
	for atajo in resultado.en_codigo:
		if (atajo.contexto, atajo.combinacion) in vistos:
			continue
		vistos.add((atajo.contexto, atajo.combinacion))
		secciones = secciones_de_contexto.get(atajo.contexto)
		documentadas = set().union(*(doc_por_seccion.get(s, set()) for s in secciones)) if secciones else todas_doc
		if atajo.combinacion not in documentadas:
			resultado.no_documentados.append(atajo)

	for atajo in resultado.documentados:
		if exenta(atajo.contexto) or atajo.combinacion in TECLAS_DEL_SISTEMA:
			continue
		clases = mapa.get(_clave_seccion(atajo.contexto))
		existentes = set().union(*(codigo_por_contexto.get(c, set()) for c in clases)) if clases else todo_codigo
		if atajo.combinacion not in existentes:
			resultado.obsoletos.append(atajo)

	for grupo_contexto in _agrupar(resultado.en_codigo, lambda a: (a.contexto, a.combinacion)):
		acciones = _grupos_por_accion(grupo_contexto)
		if len(acciones) > 1:
			resultado.duplicados.append([accion[0] for accion in acciones])
	for grupo_seccion in _agrupar(resultado.documentados, lambda a: (_clave_seccion(a.contexto), a.combinacion)):
		descripciones = {_sin_tildes(a.descripcion) for a in grupo_seccion}
		if len(grupo_seccion) > 1 and len(descripciones) > 1:
			resultado.duplicados.append(grupo_seccion)
	return resultado


def _agrupar(atajos: list[Atajo], clave) -> list[list[Atajo]]:
	grupos: dict[object, list[Atajo]] = {}
	for atajo in atajos:
		grupos.setdefault(clave(atajo), []).append(atajo)
	return list(grupos.values())


# ── Informe ──────────────────────────────────────────────────────────────────

def _descripcion(atajo: Atajo) -> str:
	return f": {atajo.descripcion}" if atajo.descripcion else ""


def formatear(resultado: Resultado) -> str:
	"""Informe en texto plano: frases completas, una por línea, sin tablas."""
	lineas = ["Comprobación de atajos de teclado", ""]
	if resultado.ruta_teclas is None or not resultado.ruta_teclas.is_file():
		lineas.append(f"No se encontró la lista de teclas ({resultado.ruta_teclas}). Todos los atajos figuran como no documentados.")
	lineas.append(f"Atajos en el código: {len(resultado.en_codigo)}. Atajos documentados: {len(resultado.documentados)}.")
	lineas.append("")
	lineas.append(f"Atajos sin documentar: {len(resultado.no_documentados)}.")
	for atajo in resultado.no_documentados:
		lineas.append(f"- {atajo.combinacion} en {atajo.contexto} ({atajo.donde()}){_descripcion(atajo)}")
	lineas.append("")
	lineas.append(f"Atajos documentados que ya no existen: {len(resultado.obsoletos)}.")
	for atajo in resultado.obsoletos:
		lineas.append(f"- {atajo.combinacion} en la sección «{atajo.contexto}» ({atajo.donde()}){_descripcion(atajo)}")
	lineas.append("")
	lineas.append(f"Atajos duplicados en la misma ventana o sección: {len(resultado.duplicados)}.")
	for grupo in resultado.duplicados:
		detalles = "; ".join(f"«{a.descripcion or 'sin descripción'}» ({a.donde()})" for a in grupo)
		lineas.append(f"- {grupo[0].combinacion} en {grupo[0].contexto}: {detalles}")
	lineas.append("")
	if resultado.problemas:
		lineas.append(f"Resultado: {resultado.problemas} problemas.")
	else:
		lineas.append("Resultado: sin problemas. El código y la lista de teclas coinciden.")
	return "\n".join(lineas)


def main(argumentos: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Compara los atajos del código con la lista de teclas documentada.")
	analizador.add_argument("--raiz", type=Path, default=RAIZ, help="Raíz del proyecto.")
	analizador.add_argument("--teclas", type=Path, help="Lista de teclas (por defecto, docs/teclas.md).")
	analizador.add_argument(
		"--contexto", action="append", default=[], metavar="SECCIÓN=Clase1,Clase2",
		help="Relaciona una sección de la lista de teclas con clases del código (se puede repetir).",
	)
	opciones = analizador.parse_args(argumentos)
	for flujo in (sys.stdout, sys.stderr):
		if hasattr(flujo, "reconfigure"):
			flujo.reconfigure(errors="replace")
	raiz = opciones.raiz.resolve()
	contextos = dict(CONTEXTOS)
	for relacion in opciones.contexto:
		seccion, _, clases = relacion.partition("=")
		contextos[seccion.strip()] = [c.strip() for c in clases.split(",") if c.strip()]
	teclas = opciones.teclas if opciones.teclas is None or opciones.teclas.is_absolute() else raiz / opciones.teclas
	resultado = comprobar(raiz, teclas, contextos)
	print(formatear(resultado))
	return 1 if resultado.problemas else 0


if __name__ == "__main__":
	sys.exit(main())
