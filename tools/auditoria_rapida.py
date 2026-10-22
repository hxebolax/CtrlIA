"""Auditoría rápida: detector de antipatrones en proyectos Python + wxPython.

Recorre el código con ``ast`` (sin ejecutarlo) y señala patrones que suelen
esconder fallos reales: errores silenciados, peticiones sin tiempo límite,
órdenes del sistema con ``shell=True``, rutas fuera de la carpeta portable,
controles poco accesibles, hilos que tocan la interfaz, etc.

Es un PUNTO DE PARTIDA para la revisión manual, no un veredicto: cada hallazgo
hay que confirmarlo leyendo el código. Los marcados «revisar» son heurísticas
con más probabilidad de falso positivo.

Uso:

	python tools/auditoria_rapida.py                      # proyecto actual, texto
	python tools/auditoria_rapida.py app main.py --markdown --salida informe.md
	python tools/auditoria_rapida.py --reglas except-silencioso,print-en-app
	python tools/auditoria_rapida.py --severidad-minima media
	python tools/auditoria_rapida.py --listar-reglas

Exclusiones:

- En la línea del hallazgo (o en una línea de comentario justo encima):
  ``# auditoria: ignorar REGLA`` (varias separadas por comas) o
  ``# auditoria: ignorar`` para todas. Añade el motivo después de `` - ``.
- Archivo ``.auditoria-ignorar`` en la raíz: una línea por patrón de ruta
  (estilo glob, con ``/``), opcionalmente seguido de ``:`` y las reglas.

Código de salida: 0 sin hallazgos (desde la severidad indicada en
``--fallar-desde``), 1 con hallazgos, 2 error de uso.

Solo usa la biblioteca estándar (Python 3.10 o superior).

PLANTILLA del agente «auditoria-proyecto».
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
import logging
import re
import sys
import tokenize
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

log = logging.getLogger(__name__)

SEVERIDADES = ("alta", "media", "baja")
ORDEN_SEVERIDAD = {nombre: posicion for posicion, nombre in enumerate(SEVERIDADES)}

CARPETAS_EXCLUIDAS = {
	".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "build", "dist", "data", "adjuntos",
	"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", "node_modules",
	"site-packages", "htmlcov",
}
CARPETAS_PRUEBAS = {"tests", "test"}
ARCHIVO_EXCLUSIONES = ".auditoria-ignorar"
PATRON_IGNORAR = re.compile(r"#\s*auditoria:\s*ignorar\b([^\n#]*)", re.IGNORECASE)


# ════════════════════════════════════════════════════════════════════════════
# Reglas
# ════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Regla:
	"""Descripción de una regla del detector."""

	nombre: str
	severidad: str
	area: str
	descripcion: str
	consejo: str
	solo_app: bool = False
	heuristica: bool = False


REGLAS: dict[str, Regla] = {
	regla.nombre: regla
	for regla in (
		Regla(
			"sintaxis", "alta", "corrección", "El archivo no se puede analizar (error de sintaxis).",
			"Corrige el error de sintaxis; el módulo no se puede importar.",
		),
		Regla(
			"except-silencioso", "media", "robustez", "except cuyo cuerpo es solo pass, ... o continue, sin registro.",
			"Registra al menos log.debug('motivo', exc_info=True) o captura una excepción más concreta.",
		),
		Regla(
			"except-amplio", "media", "robustez",
			"except desnudo o de Exception/BaseException que no registra, no relanza y no usa la excepción.",
			"Usa log.exception(...) o relanza; si el fallo es esperable, captura la excepción concreta.",
		),
		Regla(
			"print-en-app", "baja", "registro", "print() en el código de la aplicación.",
			"Usa log = logging.getLogger(__name__); en la versión compilada sin consola, sys.stdout puede ser None.",
			solo_app=True,
		),
		Regla(
			"log-formato-inmediato", "baja", "registro", "Mensaje de registro con f-string, .format(), % o + .",
			"Pasa los valores como argumentos: log.info('Descargados %s archivos', n).",
		),
		Regla(
			"basicconfig-en-modulo", "baja", "registro", "logging.basicConfig fuera del punto de entrada.",
			"Configura el registro una sola vez en main.py; los módulos solo usan getLogger(__name__).",
			solo_app=True,
		),
		Regla(
			"requests-sin-timeout", "media", "robustez", "Petición HTTP sin timeout.",
			"Añade timeout=(10, 60) o el valor adecuado; sin él, una conexión colgada bloquea para siempre.",
		),
		Regla(
			"subprocess-shell", "alta", "seguridad", "Orden del sistema a través del intérprete (shell=True, os.system, os.popen).",
			"Pasa una lista de argumentos sin shell: subprocess.run([exe, arg1, arg2], check=True, timeout=...).",
		),
		Regla(
			"eval-exec", "alta", "seguridad", "Uso de eval() o exec().",
			"Para datos, ast.literal_eval o json.loads; para elegir funciones, un diccionario de funciones permitidas.",
		),
		Regla(
			"pickle-inseguro", "media", "seguridad", "Deserialización con pickle (o similar), que ejecuta código.",
			"Si los datos pueden venir de fuera (descargas, importaciones), usa JSON.", heuristica=True,
		),
		Regla(
			"yaml-inseguro", "alta", "seguridad", "yaml.load sin SafeLoader o yaml.unsafe_load.",
			"Usa yaml.safe_load(...) o Loader=yaml.SafeLoader.",
		),
		Regla(
			"verify-false", "alta", "seguridad", "Verificación de certificados TLS desactivada.",
			"No desactives la verificación; si falta la CA, usa certifi o verify='ruta/ca.pem'.",
		),
		Regla(
			"secreto-en-codigo", "alta", "seguridad", "Posible secreto (token, clave, contraseña) escrito en el código.",
			"Sácalo del código (configuración del usuario, almacén cifrado) y revócalo si llegó a publicarse.",
			heuristica=True,
		),
		Regla(
			"sql-formateado", "alta", "seguridad", "Consulta SQL construida con f-string, format, % o concatenación.",
			"Usa marcadores: cursor.execute('... WHERE id = ?', (valor,)); los nombres de tabla o columna, de una lista blanca.",
			heuristica=True,
		),
		Regla(
			"extraccion-insegura", "media", "seguridad", "Extracción de un archivo tar sin filtro (salto de ruta).",
			"Python 3.12+: extractall(destino, filter='data'); antes, valida cada miembro contra la carpeta destino.",
			heuristica=True,
		),
		Regla(
			"ruta-no-portable", "media", "portabilidad", "Ruta fuera de la carpeta portable (APPDATA, TEMP, carpetas del usuario).",
			"Usa el módulo de rutas del proyecto (data/config, data/temp, data/cache...).", solo_app=True,
		),
		Regla(
			"getcwd", "media", "portabilidad", "Uso del directorio de trabajo actual (os.getcwd, Path.cwd, os.chdir).",
			"Calcula las rutas desde la carpeta del programa; el directorio de trabajo depende de cómo se lance.",
			solo_app=True,
		),
		Regla(
			"registro-windows", "baja", "portabilidad", "Escritura en el registro de Windows.",
			"La aplicación portable guarda su configuración en data/; usa el registro solo si el usuario lo pide.",
			solo_app=True, heuristica=True,
		),
		Regla(
			"open-sin-encoding", "baja", "portabilidad", "Archivo de texto abierto sin encoding.",
			"Indica encoding='utf-8': en Windows el valor por defecto es la página de códigos del sistema.",
		),
		Regla(
			"hilo-toca-wx", "media", "corrección", "Una función que se ejecuta en un hilo modifica controles wx directamente.",
			"Envía los cambios al hilo principal con wx.CallAfter(...).", heuristica=True,
		),
		Regla(
			"bloqueo-interfaz", "media", "rendimiento", "Operación bloqueante dentro de un manejador de eventos.",
			"Muévela a un hilo y devuelve el resultado con wx.CallAfter; si el hilo principal se congela, el lector enmudece.",
			heuristica=True,
		),
		Regla(
			"checklistbox", "media", "accesibilidad", "wx.CheckListBox directo: en wxPython anterior a 4.3 no expone el estado marcado.",
			"Usa una subclase con wx.Accessible que exponga rol de casilla y estado, o un ListCtrl con EnableCheckBoxes(True).",
		),
		Regla(
			"control-poco-accesible", "baja", "accesibilidad", "Control con accesibilidad limitada (Grid, RichTextCtrl, HtmlWindow, agw, GenButton...).",
			"Prefiere controles nativos (ListCtrl, TextCtrl, Button) o añade wx.Accessible y pruébalo con un lector.",
			heuristica=True,
		),
		Regla(
			"etiqueta-tardia", "media", "accesibilidad", "StaticText creado DESPUÉS de su control: el lector no lo asocia.",
			"Crea la etiqueta justo antes del control, con el mismo padre (el orden del sizer no importa).",
			heuristica=True,
		),
		Regla(
			"char-hook-sin-skip", "media", "accesibilidad", "Manejador de EVT_CHAR_HOOK que no llama a Skip() para las teclas que no gestiona.",
			"Llama a evento.Skip() en la rama de teclas no gestionadas; si no, Tab, flechas y escritura dejan de funcionar.",
		),
		Regla(
			"voz-duplicada", "baja", "accesibilidad", "Voz en un manejador de selección o foco: el lector ya anuncia ese cambio.",
			"No anuncies lo que el lector lee solo; reserva la voz para lo que no puede saber.", heuristica=True,
		),
		Regla(
			"cxfreeze-optimize", "media", "empaquetado", "optimize=2 en la compilación con cx_Freeze.",
			"Usa optimize=1: con 2 se eliminan las docstrings y fallan bibliotecas que las leen.",
		),
		Regla(
			"cxfreeze-msvcr", "media", "empaquetado", "Compilación con cx_Freeze sin include_msvcr.",
			"Añade include_msvcr=True en build_exe para que el .exe arranque en equipos sin los runtimes de Visual C++.",
		),
	)
}


@dataclass
class Hallazgo:
	"""Un hallazgo del detector."""

	regla: str
	severidad: str
	area: str
	archivo: str
	linea: int
	mensaje: str
	consejo: str
	revisar: bool = False

	@property
	def ubicacion(self) -> str:
		return f"{self.archivo}:{self.linea}"


# ════════════════════════════════════════════════════════════════════════════
# Utilidades de ast
# ════════════════════════════════════════════════════════════════════════════

METODOS_REGISTRO = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
PATRON_REGISTRADOR = re.compile(r"^_*(log|logger|logging|registro|registrador|lg)$|_(log|logger)$", re.IGNORECASE)
PATRON_SQL_FUERTE = re.compile(
	r"\bselect\b[\s\S]*\bfrom\b|\binsert\s+(or\s+\w+\s+)?into\b|\bupdate\b[\s\S]*\bset\b|\bdelete\s+from\b|"
	r"\b(create|drop|alter)\s+(table|index|view|trigger)\b|\bpragma\b|\bwhere\b",
	re.IGNORECASE,
)


def construir_alias(arbol: ast.AST) -> dict[str, str]:
	"""Nombre local → nombre completo según las importaciones del archivo."""
	alias: dict[str, str] = {}
	for nodo in ast.walk(arbol):
		if isinstance(nodo, ast.Import):
			for nombre in nodo.names:
				if nombre.asname:
					alias[nombre.asname] = nombre.name
				else:
					primero = nombre.name.split(".")[0]
					alias[primero] = primero
		elif isinstance(nodo, ast.ImportFrom) and nodo.module and not nodo.level:
			for nombre in nodo.names:
				alias[nombre.asname or nombre.name] = f"{nodo.module}.{nombre.name}"
	return alias


def nombre_completo(nodo: ast.AST, alias: dict[str, str]) -> str:
	"""``sp.run`` → ``subprocess.run`` (con ``import subprocess as sp``)."""
	partes: list[str] = []
	while isinstance(nodo, ast.Attribute):
		partes.append(nodo.attr)
		nodo = nodo.value
	if isinstance(nodo, ast.Name):
		partes.append(alias.get(nodo.id, nodo.id))
	elif isinstance(nodo, ast.Call):
		interior = nombre_completo(nodo.func, alias)
		if not interior:
			return ""
		partes.append(interior + "()")
	else:
		return ""
	return ".".join(reversed(partes))


def argumento(llamada: ast.Call, posicion: int | None, clave: str | None) -> ast.AST | None:
	"""Argumento por clave o por posición (``None`` si no está)."""
	if clave:
		for palabra in llamada.keywords:
			if palabra.arg == clave:
				return palabra.value
	if posicion is not None and len(llamada.args) > posicion:
		candidato = llamada.args[posicion]
		if not isinstance(candidato, ast.Starred):
			return candidato
	return None


def tiene_kwargs_expandidos(llamada: ast.Call) -> bool:
	"""Hay un ``**opciones``: no se puede saber qué argumentos llegan."""
	return any(palabra.arg is None for palabra in llamada.keywords)


def es_constante(nodo: ast.AST | None, valor=None) -> bool:
	return isinstance(nodo, ast.Constant) and (valor is None or nodo.value is valor or nodo.value == valor)


def texto_constante(nodo: ast.AST | None) -> str | None:
	"""Texto de una constante o de ``_("...")``; ``None`` si no se conoce."""
	if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
		return nodo.value
	if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id in {"_", "gettext", "N_"} and nodo.args:
		return texto_constante(nodo.args[0])
	return None


def partes_constantes(nodo: ast.AST) -> str:
	"""Texto literal que contiene una expresión de cadena (f-string, +, %, format)."""
	if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
		return nodo.value
	if isinstance(nodo, ast.JoinedStr):
		return "".join(partes_constantes(valor) for valor in nodo.values if isinstance(valor, ast.Constant))
	if isinstance(nodo, ast.BinOp):
		return partes_constantes(nodo.left) + " " + partes_constantes(nodo.right)
	if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "format":
		return partes_constantes(nodo.func.value)
	return ""


def es_cadena_dinamica(nodo: ast.AST) -> bool:
	"""f-string con valores, ``"...".format(...)``, ``"..." % x`` o concatenación con algo variable."""
	if isinstance(nodo, ast.JoinedStr):
		return any(isinstance(valor, ast.FormattedValue) for valor in nodo.values)
	if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "format":
		return isinstance(nodo.func.value, (ast.Constant, ast.JoinedStr))
	if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Mod):
		return _es_texto(nodo.left)
	if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
		hojas = list(_hojas_suma(nodo))
		return any(_es_texto(hoja) for hoja in hojas) and any(not isinstance(hoja, ast.Constant) for hoja in hojas)
	return False


def _es_texto(nodo: ast.AST) -> bool:
	return isinstance(nodo, ast.JoinedStr) or (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str))


def _hojas_suma(nodo: ast.AST) -> Iterator[ast.AST]:
	if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
		yield from _hojas_suma(nodo.left)
		yield from _hojas_suma(nodo.right)
	else:
		yield nodo


def recorrer_sin_anidados(nodos: Iterable[ast.AST], saltar_llamadas: Callable[[ast.Call], bool] | None = None) -> Iterator[ast.AST]:
	"""Recorre los nodos sin entrar en funciones, lambdas ni clases anidadas.

	``saltar_llamadas`` permite no entrar en los argumentos de ciertas llamadas
	(por ejemplo, lo que se pasa a ``wx.CallAfter`` se ejecuta en otro momento).
	"""
	pendientes = list(nodos)
	while pendientes:
		nodo = pendientes.pop()
		yield nodo
		if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
			continue
		if saltar_llamadas and isinstance(nodo, ast.Call) and saltar_llamadas(nodo):
			pendientes.append(nodo.func)
			continue
		pendientes.extend(ast.iter_child_nodes(nodo))


def dentro_de_main(nodo: ast.AST, padres: dict[ast.AST, ast.AST]) -> bool:
	"""Está dentro de ``if __name__ == "__main__":``."""
	actual = padres.get(nodo)
	while actual is not None:
		if isinstance(actual, ast.If) and isinstance(actual.test, ast.Compare):
			texto = ast.unparse(actual.test)
			if "__name__" in texto and "__main__" in texto:
				return True
		actual = padres.get(actual)
	return False


# ════════════════════════════════════════════════════════════════════════════
# Contexto de un archivo
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Archivo:
	"""Todo lo que las reglas necesitan saber de un archivo."""

	ruta: str
	arbol: ast.Module
	es_app: bool
	alias: dict[str, str]
	lineas: list[str] = field(default_factory=list)
	padres: dict[ast.AST, ast.AST] = field(default_factory=dict)
	funciones: dict[str, ast.AST] = field(default_factory=dict)
	metodos: dict[tuple[str, str], ast.AST] = field(default_factory=dict)
	hallazgos: list[Hallazgo] = field(default_factory=list)

	def __post_init__(self) -> None:
		for padre in ast.walk(self.arbol):
			for hijo in ast.iter_child_nodes(padre):
				self.padres[hijo] = padre
		for nodo in self.arbol.body:
			if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
				self.funciones[nodo.name] = nodo
		for nodo in ast.walk(self.arbol):
			if isinstance(nodo, ast.ClassDef):
				for miembro in nodo.body:
					if isinstance(miembro, (ast.FunctionDef, ast.AsyncFunctionDef)):
						self.metodos[(nodo.name, miembro.name)] = miembro

	def linea(self, numero: int) -> str:
		return self.lineas[numero - 1] if 0 < numero <= len(self.lineas) else ""

	def nombre(self, nodo: ast.AST) -> str:
		return nombre_completo(nodo, self.alias)

	def clase_de(self, nodo: ast.AST) -> ast.ClassDef | None:
		actual = self.padres.get(nodo)
		while actual is not None:
			if isinstance(actual, ast.ClassDef):
				return actual
			actual = self.padres.get(actual)
		return None

	def funcion_de(self, nodo: ast.AST) -> ast.AST | None:
		actual = self.padres.get(nodo)
		while actual is not None:
			if isinstance(actual, (ast.FunctionDef, ast.AsyncFunctionDef)):
				return actual
			actual = self.padres.get(actual)
		return None

	def resolver_funcion(self, referencia: ast.AST, desde: ast.AST) -> ast.AST | None:
		"""Busca la definición de ``self.metodo``, ``Clase.metodo`` o ``funcion``."""
		if isinstance(referencia, ast.Attribute) and isinstance(referencia.value, ast.Name):
			clase = self.clase_de(desde)
			if referencia.value.id in {"self", "cls"} and clase is not None:
				return self.metodos.get((clase.name, referencia.attr))
			return self.metodos.get((referencia.value.id, referencia.attr))
		if isinstance(referencia, ast.Name):
			funcion = self.funcion_de(desde)
			while funcion is not None:
				for nodo in ast.walk(funcion):
					if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == referencia.id and nodo is not funcion:
						return nodo
				funcion = self.funcion_de(funcion)
			return self.funciones.get(referencia.id)
		return None

	def anotar(self, regla: str, nodo: ast.AST | int, mensaje: str, severidad: str | None = None) -> None:
		datos = REGLAS[regla]
		linea = nodo if isinstance(nodo, int) else getattr(nodo, "lineno", 1)
		self.hallazgos.append(Hallazgo(
			regla=regla, severidad=severidad or datos.severidad, area=datos.area, archivo=self.ruta,
			linea=linea, mensaje=mensaje, consejo=datos.consejo, revisar=datos.heuristica,
		))


# ════════════════════════════════════════════════════════════════════════════
# Reglas: robustez y registro
# ════════════════════════════════════════════════════════════════════════════

def _tipos_capturados(manejador: ast.ExceptHandler) -> list[str]:
	if manejador.type is None:
		return []
	elementos = manejador.type.elts if isinstance(manejador.type, ast.Tuple) else [manejador.type]
	return [ast.unparse(elemento) for elemento in elementos]


def _es_amplio(manejador: ast.ExceptHandler) -> bool:
	tipos = _tipos_capturados(manejador)
	return manejador.type is None or any(tipo.split(".")[-1] in {"Exception", "BaseException"} for tipo in tipos)


def _registra_o_usa(manejador: ast.ExceptHandler) -> bool:
	"""El manejador relanza, registra o hace algo con la excepción."""
	for nodo in recorrer_sin_anidados(manejador.body):
		if isinstance(nodo, ast.Raise):
			return True
		if isinstance(nodo, ast.Name) and manejador.name and nodo.id == manejador.name:
			return True
		if isinstance(nodo, ast.Call):
			funcion = nodo.func
			final = funcion.attr if isinstance(funcion, ast.Attribute) else getattr(funcion, "id", "")
			if final in METODOS_REGISTRO and isinstance(funcion, ast.Attribute):
				return True
			if re.search(r"log|registr|traza|traceback|print_exc|format_exc|exc_info|report|excepthook|warn", final, re.IGNORECASE):
				return True
			if any(palabra.arg == "exc_info" for palabra in nodo.keywords):
				return True
		if isinstance(nodo, ast.Attribute) and nodo.attr == "exc_info":
			return True
	return False


PATRON_NOQA_EXCEPT = re.compile(r"#\s*noqa:?[^#]*\b(BLE001|S110|S112|E722)\b", re.IGNORECASE)


# Excepciones que suelen ignorarse a propósito con un pass (y que no esconden fallos).
EXCEPCIONES_ESPERABLES = {
	"FileNotFoundError", "FileExistsError", "NotADirectoryError", "IsADirectoryError", "ProcessLookupError",
	"KeyError", "IndexError", "ValueError", "StopIteration", "StopAsyncIteration", "ImportError",
	"ModuleNotFoundError", "Empty", "Full", "InterruptedError", "CancelledError", "TimeoutError",
}


def regla_excepciones(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if not isinstance(nodo, ast.ExceptHandler):
			continue
		if PATRON_NOQA_EXCEPT.search(archivo.linea(nodo.lineno)):
			continue  # decisión documentada con noqa de ruff (BLE001, S110, E722)
		tipos = _tipos_capturados(nodo)
		descripcion = f"except {', '.join(tipos)}" if tipos else "except"
		vacio = all(
			isinstance(sentencia, (ast.Pass, ast.Continue))
			or (isinstance(sentencia, ast.Expr) and isinstance(sentencia.value, ast.Constant))
			for sentencia in nodo.body
		)
		if vacio and not _es_amplio(nodo) and all(tipo.split(".")[-1] in EXCEPCIONES_ESPERABLES for tipo in tipos):
			continue  # «si no existe, no pasa nada»: patrón idiomático, no un error perdido
		if vacio:
			archivo.anotar(
				"except-silencioso", nodo,
				f"{descripcion}: se descarta el error sin dejar rastro.",
				severidad="media" if _es_amplio(nodo) else "baja",
			)
		elif _es_amplio(nodo) and not _registra_o_usa(nodo):
			archivo.anotar("except-amplio", nodo, f"{descripcion} sin registrar ni relanzar: el error se pierde.")


def regla_print(archivo: Archivo) -> None:
	nombre_archivo = Path(archivo.ruta).name
	if not archivo.es_app or "argparse" in archivo.alias or nombre_archivo.startswith("cli") or nombre_archivo == "__main__.py":
		return
	for nodo in ast.walk(archivo.arbol):
		if (
			isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id == "print"
			and "print" not in archivo.alias and not dentro_de_main(nodo, archivo.padres)
		):
			archivo.anotar("print-en-app", nodo, "print() en lugar del registro.")


def _es_registrador(receptor: ast.AST, archivo: Archivo) -> bool:
	nombre = archivo.nombre(receptor)
	if not nombre:
		return False
	if nombre.endswith("getLogger()"):
		return True
	return bool(PATRON_REGISTRADOR.search(nombre.split(".")[-1]))


def regla_formato_registro(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr in METODOS_REGISTRO):
			continue
		if not _es_registrador(nodo.func.value, archivo):
			continue
		mensaje = argumento(nodo, 1 if nodo.func.attr == "log" else 0, "msg")
		if mensaje is not None and es_cadena_dinamica(mensaje):
			archivo.anotar("log-formato-inmediato", nodo, f"{ast.unparse(nodo.func)}() con el mensaje ya formateado.")


def regla_basicconfig(archivo: Archivo) -> None:
	if not archivo.es_app or Path(archivo.ruta).name in {"main.py", "__main__.py"}:
		return
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Call) and archivo.nombre(nodo.func) == "logging.basicConfig" and not dentro_de_main(nodo, archivo.padres):
			archivo.anotar("basicconfig-en-modulo", nodo, "logging.basicConfig en un módulo de la aplicación.")


FUNCIONES_HTTP = {
	f"requests.{metodo}" for metodo in ("get", "post", "put", "patch", "delete", "head", "options", "request")
}
PATRON_SESION = re.compile(r"^_*(session|sesion|sess|http_session|sesion_http)$", re.IGNORECASE)


def regla_timeout(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if not isinstance(nodo, ast.Call) or tiene_kwargs_expandidos(nodo):
			continue
		nombre = archivo.nombre(nodo.func)
		if nombre in FUNCIONES_HTTP or nombre.startswith("requests.Session()."):
			if argumento(nodo, None, "timeout") is None:
				archivo.anotar("requests-sin-timeout", nodo, f"{nombre}() sin timeout.")
		elif nombre == "urllib.request.urlopen":
			if argumento(nodo, 2, "timeout") is None:
				archivo.anotar("requests-sin-timeout", nodo, "urlopen() sin timeout.")
		elif (
			isinstance(nodo.func, ast.Attribute) and nodo.func.attr in {"get", "post", "put", "patch", "delete", "head", "request"}
			and PATRON_SESION.match(nombre.split(".")[-2] if "." in nombre else "")
			and "requests" in archivo.alias and argumento(nodo, None, "timeout") is None
		):
			archivo.anotar("requests-sin-timeout", nodo, f"{nombre}() de una sesión de requests sin timeout.")


# ════════════════════════════════════════════════════════════════════════════
# Reglas: seguridad
# ════════════════════════════════════════════════════════════════════════════

FUNCIONES_SUBPROCESO = {
	"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen",
}


def regla_shell(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if not isinstance(nodo, ast.Call):
			continue
		nombre = archivo.nombre(nodo.func)
		orden = argumento(nodo, 0, "args") or argumento(nodo, 0, "cmd") or argumento(nodo, 0, "command")
		fija = isinstance(orden, ast.Constant)
		if nombre in FUNCIONES_SUBPROCESO and es_constante(argumento(nodo, None, "shell"), True):
			archivo.anotar(
				"subprocess-shell", nodo, f"{nombre}(shell=True)" + ("" if fija else " con una orden construida en tiempo de ejecución") + ".",
				severidad="media" if fija else "alta",
			)
		elif nombre in {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput", "asyncio.create_subprocess_shell"}:
			archivo.anotar("subprocess-shell", nodo, f"{nombre}() ejecuta a través del intérprete de órdenes.", severidad="media" if fija else "alta")


def regla_eval(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Call):
			nombre = archivo.nombre(nodo.func)
			if nombre in {"eval", "exec", "builtins.eval", "builtins.exec"} and nombre.split(".")[-1] not in archivo.funciones:
				archivo.anotar("eval-exec", nodo, f"{nombre.split('.')[-1]}() ejecuta código arbitrario.")


def regla_pickle(archivo: Archivo) -> None:
	peligrosas = {
		f"{modulo}.{funcion}" for modulo in ("pickle", "cPickle", "_pickle", "dill", "cloudpickle", "joblib")
		for funcion in ("load", "loads", "Unpickler")
	} | {"shelve.open", "marshal.load", "marshal.loads"}
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Call) and archivo.nombre(nodo.func) in peligrosas:
			archivo.anotar("pickle-inseguro", nodo, f"{archivo.nombre(nodo.func)}() puede ejecutar código al leer datos manipulados.")


def regla_yaml(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if not isinstance(nodo, ast.Call):
			continue
		nombre = archivo.nombre(nodo.func)
		if nombre in {"yaml.unsafe_load", "yaml.unsafe_load_all"}:
			archivo.anotar("yaml-inseguro", nodo, f"{nombre}() construye objetos arbitrarios.")
		elif nombre in {"yaml.load", "yaml.load_all"}:
			cargador = argumento(nodo, 1, "Loader")
			if cargador is None or "Safe" not in ast.unparse(cargador):
				archivo.anotar("yaml-inseguro", nodo, f"{nombre}() sin SafeLoader.")


def regla_verify(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Call):
			if any(palabra.arg == "verify" and es_constante(palabra.value, False) for palabra in nodo.keywords):
				archivo.anotar("verify-false", nodo, "verify=False desactiva la comprobación del certificado.")
			elif archivo.nombre(nodo.func) in {"ssl._create_unverified_context", "urllib3.disable_warnings"}:
				archivo.anotar(
					"verify-false", nodo, f"{archivo.nombre(nodo.func)}(): conexión sin verificar o avisos ocultos.",
					severidad="alta" if "unverified" in archivo.nombre(nodo.func) else "baja",
				)
		elif isinstance(nodo, ast.Assign) and es_constante(nodo.value, False):
			for destino in nodo.targets:
				if isinstance(destino, ast.Attribute) and destino.attr in {"verify", "check_hostname"}:
					archivo.anotar("verify-false", nodo, f"{ast.unparse(destino)} = False desactiva la verificación TLS.")
		elif isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Attribute) and nodo.value.attr == "CERT_NONE":
			archivo.anotar("verify-false", nodo, "ssl.CERT_NONE desactiva la verificación TLS.")


PATRONES_SECRETO = [
	("token de GitHub", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
	("clave de API tipo sk-", re.compile(r"\bsk-(proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
	("clave de acceso de AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
	("token de Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
	("clave de API de Google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
	("token de bot de Telegram", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
	("clave privada", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
	("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
PATRON_NOMBRE_SECRETO = re.compile(
	r"(pass(word|wd)?|contrase[nñ]a|secret|secreto|token|api_?key|apikey|clave_?api|private_?key|clave_privada)",
	re.IGNORECASE,
)
PATRON_MARCADOR = re.compile(r"^(x+|\*+|\.+|changeme|cambiame|tu_.*|your_.*|<.*>|\$\{.*\}|%.*|\{.*\}|none|null|test.*|prueba.*|ejemplo.*|example.*|dummy.*)$", re.IGNORECASE)


def _parece_secreto(valor: str) -> bool:
	"""Una cadena con pinta de credencial: larga, sin espacios, con letras y dígitos."""
	if len(valor) < 12 or any(caracter.isspace() for caracter in valor) or PATRON_MARCADOR.match(valor):
		return False
	if re.fullmatch(r"[a-z_.:/-]+", valor) or valor.startswith(("http://", "https://")) or "/" in valor or "\\" in valor:
		return False
	return bool(re.search(r"[A-Za-z]", valor) and re.search(r"\d", valor))


def regla_secretos(archivo: Archivo) -> None:
	docstrings = set()
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.body:
			primero = nodo.body[0]
			if isinstance(primero, ast.Expr) and isinstance(primero.value, ast.Constant):
				docstrings.add(id(primero.value))
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and id(nodo) not in docstrings:
			for descripcion, patron in PATRONES_SECRETO:
				if patron.search(nodo.value):
					archivo.anotar("secreto-en-codigo", nodo, f"Cadena con formato de {descripcion}.")
					break
		pares: list[tuple[str, ast.AST]] = []
		if isinstance(nodo, (ast.Assign, ast.AnnAssign)):
			destinos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
			for destino in destinos:
				nombre = destino.id if isinstance(destino, ast.Name) else getattr(destino, "attr", "")
				if nombre and nodo.value is not None:
					pares.append((nombre, nodo.value))
		elif isinstance(nodo, ast.Call):
			pares.extend((palabra.arg, palabra.value) for palabra in nodo.keywords if palabra.arg)
		elif isinstance(nodo, ast.Dict):
			pares.extend(
				(clave.value, valor) for clave, valor in zip(nodo.keys, nodo.values, strict=True)
				if isinstance(clave, ast.Constant) and isinstance(clave.value, str)
			)
		for nombre, valor in pares:
			if (
				PATRON_NOMBRE_SECRETO.search(nombre) and isinstance(valor, ast.Constant) and isinstance(valor.value, str)
				and _parece_secreto(valor.value) and not any(patron.search(valor.value) for _, patron in PATRONES_SECRETO)
			):
				archivo.anotar("secreto-en-codigo", valor, f"«{nombre}» tiene un valor literal con aspecto de credencial.", severidad="media")


def _marcadores_locales(archivo: Archivo, nodo: ast.AST) -> set[str]:
	"""Variables de la función que contienen marcadores «?» generados (``",".join("?" ...)``)."""
	funcion = archivo.funcion_de(nodo)
	nombres: set[str] = set()
	for hijo in ast.walk(funcion) if funcion is not None else []:
		if isinstance(hijo, ast.Assign) and re.search(r"""['"][?,\s]*\?[?,\s]*['"]""", ast.unparse(hijo.value)):
			nombres.update(destino.id for destino in hijo.targets if isinstance(destino, ast.Name))
	return nombres


def _valor_formateado_seguro(expresion: ast.AST, marcadores: set[str]) -> bool:
	"""Constantes en mayúsculas, marcadores «?», números o aritmética: no son inyección."""
	texto = ast.unparse(expresion)
	if isinstance(expresion, ast.Name) and expresion.id in marcadores:
		return True
	if isinstance(expresion, ast.BinOp) and all(
		isinstance(hoja, (ast.Name, ast.Constant)) for hoja in (expresion.left, expresion.right)
	) and any(isinstance(hoja, ast.Constant) and isinstance(hoja.value, int) for hoja in (expresion.left, expresion.right)):
		return True
	return bool(
		re.fullmatch(r"[A-Z][A-Z0-9_]*(\.[A-Z][A-Z0-9_]*)*", texto)
		or re.search(r"""['"][?,\s]*\?[?,\s]*['"]""", texto)
		or re.fullmatch(r"(int|len|float)\(.*\)", texto)
	)


PATRON_POSICION_VALOR = re.compile(r"(=|<|>|\blike|\blimit|\boffset|\bin\s*\(|\bvalues\s*\(|,)\s*$", re.IGNORECASE)


def _valores_interpolados(consulta: ast.AST) -> list[ast.AST]:
	"""Expresiones que se insertan en el texto SQL (f-string, %, format o +)."""
	if isinstance(consulta, ast.JoinedStr):
		return [valor.value for valor in consulta.values if isinstance(valor, ast.FormattedValue)]
	if isinstance(consulta, ast.BinOp) and isinstance(consulta.op, ast.Mod):
		derecha = consulta.right
		return list(derecha.elts) if isinstance(derecha, ast.Tuple) else [derecha]
	if isinstance(consulta, ast.Call):
		return list(consulta.args) + [palabra.value for palabra in consulta.keywords]
	if isinstance(consulta, ast.BinOp):
		return [hoja for hoja in _hojas_suma(consulta) if not isinstance(hoja, ast.Constant)]
	return []


def _severidad_sql(consulta: ast.AST, peligrosos: list[ast.AST]) -> str:
	"""alta: el valor va entre comillas; media: en posición de valor; baja: identificador (tabla, columna)."""
	if not isinstance(consulta, ast.JoinedStr):
		return "media"
	severidad = "baja"
	anterior = ""
	for parte in consulta.values:
		if isinstance(parte, ast.Constant):
			anterior = str(parte.value)
			continue
		if isinstance(parte, ast.FormattedValue) and parte.value in peligrosos:
			previo = anterior.rstrip()
			if previo.endswith(("'", '"')):
				return "alta"
			if PATRON_POSICION_VALOR.search(previo):
				severidad = "media"
		anterior = ""
	return severidad


def regla_sql(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr in {"execute", "executemany", "executescript"}):
			continue
		consulta = argumento(nodo, 0, "sql")
		if consulta is None or not es_cadena_dinamica(consulta):
			continue
		if not PATRON_SQL_FUERTE.search(partes_constantes(consulta)):
			continue
		marcadores = _marcadores_locales(archivo, nodo)
		peligrosos = [valor for valor in _valores_interpolados(consulta) if not _valor_formateado_seguro(valor, marcadores)]
		if not peligrosos:
			continue
		severidad = _severidad_sql(consulta, peligrosos)
		detalle = {
			"alta": "un valor dentro de comillas",
			"media": "valores",
			"baja": "nombres de tabla o columna interpolados",
		}[severidad]
		archivo.anotar("sql-formateado", nodo, f"{ast.unparse(nodo.func)}() con SQL construido a partir de {detalle}.", severidad=severidad)


def regla_extraccion(archivo: Archivo) -> None:
	usa_tar = any(nombre in {"tarfile"} or nombre.startswith("tarfile.") for nombre in archivo.alias.values())
	for nodo in ast.walk(archivo.arbol):
		if not isinstance(nodo, ast.Call):
			continue
		nombre = archivo.nombre(nodo.func)
		if nombre == "shutil.unpack_archive" and argumento(nodo, 3, "filter") is None:
			archivo.anotar("extraccion-insegura", nodo, "shutil.unpack_archive() sin filter: un tar puede escribir fuera del destino.")
		elif (
			usa_tar and isinstance(nodo.func, ast.Attribute) and nodo.func.attr in {"extractall", "extract"}
			and argumento(nodo, None, "filter") is None and "zip" not in ast.unparse(nodo.func.value).lower()
		):
			archivo.anotar("extraccion-insegura", nodo, f"{ast.unparse(nodo.func)}() sin filter en un módulo que usa tarfile.")


# ════════════════════════════════════════════════════════════════════════════
# Reglas: portabilidad
# ════════════════════════════════════════════════════════════════════════════

VARIABLES_NO_PORTABLES = {"APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMDATA", "ALLUSERSPROFILE"}
TEMPORALES_SIN_DIR = {
	"tempfile.mkdtemp": 2, "tempfile.mkstemp": 2, "tempfile.NamedTemporaryFile": None, "tempfile.TemporaryDirectory": 2,
	"tempfile.TemporaryFile": None, "tempfile.SpooledTemporaryFile": None,
}
METODOS_CARPETAS_USUARIO = {"GetUserDataDir", "GetUserLocalDataDir", "GetUserConfigDir", "GetTempDir", "GetConfigDir"}


def regla_rutas(archivo: Archivo) -> None:
	if not archivo.es_app:
		return
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Call):
			nombre = archivo.nombre(nodo.func)
			primero = argumento(nodo, 0, None)
			if nombre in {"os.getenv", "os.environ.get", "os.environ.setdefault"} and isinstance(primero, ast.Constant) and str(primero.value).upper() in VARIABLES_NO_PORTABLES:
				leve = str(primero.value).upper() in {"LOCALAPPDATA", "PROGRAMDATA", "ALLUSERSPROFILE"}
				archivo.anotar(
					"ruta-no-portable", nodo, f"{nombre}('{primero.value}'): carpeta del sistema, no de la aplicación.",
					severidad="baja" if leve else None,
				)
			elif nombre in {"tempfile.gettempdir", "tempfile.gettempdirb"}:
				archivo.anotar("ruta-no-portable", nodo, f"{nombre}() apunta a %TEMP%, fuera de la carpeta portable.")
			elif nombre in TEMPORALES_SIN_DIR and argumento(nodo, TEMPORALES_SIN_DIR[nombre], "dir") is None and not tiene_kwargs_expandidos(nodo):
				archivo.anotar("ruta-no-portable", nodo, f"{nombre}() sin dir=: crea el temporal en %TEMP%.", severidad="baja")
			elif nombre == "os.path.expandvars" and isinstance(primero, ast.Constant) and re.search(r"%(APPDATA|LOCALAPPDATA|TEMP|TMP)%", str(primero.value), re.IGNORECASE):
				archivo.anotar("ruta-no-portable", nodo, f"expandvars('{primero.value}'): carpeta del sistema.")
			elif nombre.split(".")[-1] in {"user_data_dir", "user_config_dir", "user_cache_dir", "user_log_dir"} and nombre.split(".")[0] in {"platformdirs", "appdirs"}:
				archivo.anotar("ruta-no-portable", nodo, f"{nombre}() apunta a la carpeta del perfil del usuario.")
			elif isinstance(nodo.func, ast.Attribute) and nodo.func.attr in METODOS_CARPETAS_USUARIO and "StandardPaths" in nombre:
				archivo.anotar("ruta-no-portable", nodo, f"wx.StandardPaths.{nodo.func.attr}() apunta al perfil del usuario.")
		elif (
			isinstance(nodo, ast.Subscript) and archivo.nombre(nodo.value) == "os.environ"
			and isinstance(nodo.slice, ast.Constant) and str(nodo.slice.value).upper() in VARIABLES_NO_PORTABLES
		):
			archivo.anotar("ruta-no-portable", nodo, f"os.environ['{nodo.slice.value}']: carpeta del sistema.")


def regla_getcwd(archivo: Archivo) -> None:
	if not archivo.es_app:
		return
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Call):
			nombre = archivo.nombre(nodo.func)
			if nombre in {"os.getcwd", "os.getcwdb", "pathlib.Path.cwd", "os.chdir"}:
				archivo.anotar("getcwd", nodo, f"{nombre}(): depende de la carpeta desde la que se lanzó el programa.")


def regla_registro_windows(archivo: Archivo) -> None:
	if not archivo.es_app:
		return
	escrituras = {f"{modulo}.{funcion}" for modulo in ("winreg", "_winreg") for funcion in ("CreateKey", "CreateKeyEx", "SetValue", "SetValueEx")}
	llamadas = [
		nodo for nodo in ast.walk(archivo.arbol) if isinstance(nodo, ast.Call) and archivo.nombre(nodo.func) in escrituras
	]
	if llamadas:
		primera = min(llamadas, key=lambda nodo: nodo.lineno)
		archivo.anotar(
			"registro-windows", primera,
			f"{len(llamadas)} escrituras en el registro de Windows en este archivo (la primera, {archivo.nombre(primera.func)}()).",
		)


def regla_encoding(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		if not isinstance(nodo, ast.Call) or tiene_kwargs_expandidos(nodo):
			continue
		nombre = archivo.nombre(nodo.func)
		if nombre in {"open", "io.open"} and "open" not in archivo.funciones:
			modo = argumento(nodo, 1, "mode")
			if modo is not None and not (isinstance(modo, ast.Constant) and isinstance(modo.value, str)):
				continue
			if modo is not None and "b" in modo.value:
				continue
			if argumento(nodo, 3, "encoding") is None:
				archivo.anotar("open-sin-encoding", nodo, "open() en modo texto sin encoding.")
		elif isinstance(nodo.func, ast.Attribute) and nodo.func.attr in {"read_text", "write_text"}:
			posicion = 0 if nodo.func.attr == "read_text" else 1
			tiene_datos = nodo.func.attr == "read_text" or bool(nodo.args)
			if tiene_datos and argumento(nodo, posicion, "encoding") is None:
				archivo.anotar("open-sin-encoding", nodo, f"{nodo.func.attr}() sin encoding.")


# ════════════════════════════════════════════════════════════════════════════
# Reglas: wx (hilos, interfaz, accesibilidad)
# ════════════════════════════════════════════════════════════════════════════

METODOS_UI = {
	"SetValue", "ChangeValue", "SetLabel", "SetLabelText", "SetTitle", "SetStatusText", "SetItem", "SetItemText",
	"SetStringItem", "InsertItem", "InsertStringItem", "DeleteItem", "DeleteAllItems", "Append", "AppendItems",
	"AppendText", "WriteText", "SetItems", "SetSelection", "SetStringSelection", "Select", "Focus", "SetFocus",
	"Enable", "Disable", "Show", "Hide", "Refresh", "Layout", "Fit", "SetRange", "SetItemCount", "RefreshItem",
	"RefreshItems", "EnsureVisible", "Destroy", "SetBackgroundColour", "SetForegroundColour", "SetToolTip",
	"SetFont", "Pulse", "SetItemState", "Check", "SetInsertionPoint", "SetMenuBar", "SetStatusBar",
}
LLAMADAS_DIFERIDAS = {"wx.CallAfter", "wx.CallLater", "wx.PostEvent", "wx.QueueEvent", "wx.lib.pubsub.pub.sendMessage"}


def _llamada_diferida(archivo: Archivo) -> Callable[[ast.Call], bool]:
	def comprobar(llamada: ast.Call) -> bool:
		nombre = archivo.nombre(llamada.func)
		return nombre in LLAMADAS_DIFERIDAS or nombre.endswith((".CallAfter", ".QueueEvent", ".AddPendingEvent"))
	return comprobar


def _toques_wx(archivo: Archivo, funcion: ast.AST, profundidad: int = 1) -> list[ast.Call]:
	"""Llamadas directas a métodos de controles wx (sin wx.CallAfter) dentro de ``funcion``."""
	toques: list[ast.Call] = []
	for nodo in recorrer_sin_anidados(getattr(funcion, "body", []), _llamada_diferida(archivo)):
		if not isinstance(nodo, ast.Call):
			continue
		nombre = archivo.nombre(nodo.func)
		if isinstance(nodo.func, ast.Attribute) and nodo.func.attr in METODOS_UI:
			toques.append(nodo)
		elif nombre in {"wx.MessageBox", "wx.MessageDialog", "wx.LogError", "wx.LogMessage"}:
			toques.append(nodo)
		elif profundidad > 0 and isinstance(nodo.func, ast.Attribute) and isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == "self":
			otro = archivo.resolver_funcion(nodo.func, nodo)
			if otro is not None and otro is not funcion and _toques_wx(archivo, otro, profundidad - 1):
				toques.append(nodo)
	return toques


def regla_hilos(archivo: Archivo) -> None:
	if "wx" not in archivo.alias:
		return
	objetivos: list[tuple[ast.AST, ast.AST]] = []
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Call):
			nombre = archivo.nombre(nodo.func)
			objetivo = None
			if nombre == "threading.Thread":
				objetivo = argumento(nodo, 1, "target")
			elif nombre == "threading.Timer":
				objetivo = argumento(nodo, 1, "function")
			elif isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "submit" and nodo.args:
				objetivo = nodo.args[0]
			elif nombre in {"_thread.start_new_thread", "thread.start_new_thread"} and nodo.args:
				objetivo = nodo.args[0]
			if objetivo is not None:
				definicion = archivo.resolver_funcion(objetivo, nodo)
				if definicion is not None:
					objetivos.append((nodo, definicion))
		elif isinstance(nodo, ast.ClassDef) and any(archivo.nombre(base) == "threading.Thread" for base in nodo.bases):
			ejecutar = archivo.metodos.get((nodo.name, "run"))
			if ejecutar is not None:
				objetivos.append((ejecutar, ejecutar))
	vistos = set()
	for origen, definicion in objetivos:
		for toque in _toques_wx(archivo, definicion):
			if id(toque) in vistos:
				continue
			vistos.add(id(toque))
			archivo.anotar(
				"hilo-toca-wx", toque,
				f"{ast.unparse(toque.func)}() en {definicion.name}(), que se ejecuta en un hilo (línea {origen.lineno}).",
			)


BLOQUEANTES = {
	"time.sleep", "urllib.request.urlopen", "subprocess.run", "subprocess.call", "subprocess.check_call",
	"subprocess.check_output", "os.system",
} | FUNCIONES_HTTP


def _manejadores(archivo: Archivo, eventos: set[str] | None) -> Iterator[tuple[ast.Call, str, ast.AST]]:
	"""Pares (llamada a Bind, evento, definición del manejador) resueltos en el archivo."""
	for nodo in ast.walk(archivo.arbol):
		if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "Bind" and len(nodo.args) >= 2):
			continue
		evento = archivo.nombre(nodo.args[0]).split(".")[-1]
		if eventos is not None and evento not in eventos:
			continue
		definicion = archivo.resolver_funcion(nodo.args[1], nodo)
		if definicion is not None:
			yield nodo, evento, definicion


def regla_bloqueo_interfaz(archivo: Archivo) -> None:
	if "wx" not in archivo.alias:
		return
	vistos = set()
	for _, evento, definicion in _manejadores(archivo, None):
		for nodo in recorrer_sin_anidados(definicion.body, _llamada_diferida(archivo)):
			if isinstance(nodo, ast.Call) and archivo.nombre(nodo.func) in BLOQUEANTES and id(nodo) not in vistos:
				vistos.add(id(nodo))
				archivo.anotar("bloqueo-interfaz", nodo, f"{archivo.nombre(nodo.func)}() en {definicion.name}(), manejador de {evento}.")


EVENTOS_LECTOR = {"EVT_LIST_ITEM_SELECTED", "EVT_LIST_ITEM_FOCUSED", "EVT_SET_FOCUS", "EVT_LISTBOX", "EVT_TREE_SEL_CHANGED", "EVT_CHILD_FOCUS"}
PATRON_VOZ = re.compile(r"^(speak|hablar|anunciar|decir|locutar|output|braille|leer_en_voz)", re.IGNORECASE)


def regla_voz_duplicada(archivo: Archivo) -> None:
	for _, evento, definicion in _manejadores(archivo, EVENTOS_LECTOR):
		for nodo in recorrer_sin_anidados(definicion.body):
			if isinstance(nodo, ast.Call):
				final = archivo.nombre(nodo.func).split(".")[-1]
				if PATRON_VOZ.match(final):
					archivo.anotar("voz-duplicada", nodo, f"{final}() en {definicion.name}(), manejador de {evento}.")


CONTROLES_POCO_ACCESIBLES = {
	"wx.grid.Grid", "wx.richtext.RichTextCtrl", "wx.html.HtmlWindow", "wx.stc.StyledTextCtrl",
	"wx.lib.mixins.listctrl.CheckListCtrlMixin", "wx.dataview.DataViewCtrl", "wx.dataview.TreeListCtrl",
}


def regla_controles(archivo: Archivo) -> None:
	for nodo in ast.walk(archivo.arbol):
		nombres: list[tuple[ast.AST, str]] = []
		if isinstance(nodo, ast.Call):
			nombres.append((nodo, archivo.nombre(nodo.func)))
		elif isinstance(nodo, ast.ClassDef):
			nombres.extend((base, archivo.nombre(base)) for base in nodo.bases)
		for origen, nombre in nombres:
			if nombre == "wx.CheckListBox" and isinstance(nodo, ast.Call):
				archivo.anotar("checklistbox", nodo, "wx.CheckListBox sin subclase accesible.")
			elif (
				nombre in CONTROLES_POCO_ACCESIBLES or nombre.startswith(("wx.lib.agw.", "wx.lib.buttons.Gen"))
			):
				archivo.anotar("control-poco-accesible", origen, f"{nombre}: accesibilidad limitada con lectores de pantalla.")


CONTROLES_CON_ETIQUETA = {
	"TextCtrl", "Choice", "ComboBox", "BitmapComboBox", "OwnerDrawnComboBox", "ListBox", "CheckListBox", "ListCtrl",
	"ListView", "TreeCtrl", "Slider", "SpinCtrl", "SpinCtrlDouble", "DatePickerCtrl", "TimePickerCtrl",
	"DirPickerCtrl", "FilePickerCtrl", "Gauge", "SearchCtrl", "EditableListBox", "SimpleHtmlListBox",
}
OTRAS_VENTANAS = {
	"Button", "BitmapButton", "ToggleButton", "CheckBox", "RadioButton", "RadioBox", "StaticBox", "StaticLine",
	"StaticBitmap", "Panel", "ScrolledWindow", "Notebook", "Listbook", "Choicebook", "Treebook",
	"SplitterWindow", "CollapsiblePane", "HyperlinkCtrl", "CommandLinkButton", "Window",
}


def _clase_wx(archivo: Archivo, llamada: ast.Call, locales: dict[str, str]) -> str | None:
	nombre = archivo.nombre(llamada.func)
	partes = nombre.split(".")
	if len(partes) >= 2 and partes[0] == "wx":
		return partes[-1]
	if nombre in locales:
		return locales[nombre]
	# Clase del proyecto definida en otro archivo (AccesibleLista(panel, ...)):
	# se cuenta como «otra ventana» para no atribuirle etiquetas ajenas.
	final = partes[-1]
	if final[:1].isupper() and not final.isupper() and final not in {"BoxSizer", "StaticBoxSizer", "GridBagSizer", "FlexGridSizer", "GridSizer"}:
		return "_" + final
	return None


def _padre(llamada: ast.Call) -> str | None:
	padre = argumento(llamada, 0, "parent")
	return ast.unparse(padre) if padre is not None else None


def _bloques(arbol: ast.AST) -> Iterator[list[ast.stmt]]:
	for nodo in ast.walk(arbol):
		for campo in ("body", "orelse", "finalbody"):
			bloque = getattr(nodo, campo, None)
			if isinstance(bloque, list) and bloque and isinstance(bloque[0], ast.stmt):
				yield bloque


def regla_etiquetas(archivo: Archivo) -> None:
	if "wx" not in archivo.alias:
		return
	# Clases del propio archivo que heredan de un control wx: se tratan como él.
	locales: dict[str, str] = {}
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.ClassDef):
			for base in nodo.bases:
				partes = archivo.nombre(base).split(".")
				if partes[0] == "wx":
					locales[nodo.name] = partes[-1]
	compuestas = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
	if hasattr(ast, "Match"):
		compuestas += (ast.Match,)
	for bloque in _bloques(archivo.arbol):
		# Ventanas creadas en este bloque, en orden de creación. Las sentencias
		# compuestas tienen sus propios bloques y no se mezclan con este.
		secuencia: list[tuple[str, str, ast.Call]] = []
		for sentencia in bloque:
			if isinstance(sentencia, compuestas):
				continue
			llamadas = [nodo for nodo in recorrer_sin_anidados([sentencia]) if isinstance(nodo, ast.Call)]
			for llamada in sorted(llamadas, key=lambda nodo: (nodo.lineno, nodo.col_offset)):
				clase = _clase_wx(archivo, llamada, locales)
				padre = _padre(llamada)
				if clase is None or padre is None:
					continue
				if clase in {"StaticText", "GenStaticText"}:
					secuencia.append(("etiqueta", padre, llamada))
				elif clase in CONTROLES_CON_ETIQUETA:
					secuencia.append(("control", padre, llamada))
				elif clase in OTRAS_VENTANAS or clase.startswith("_"):
					secuencia.append(("otro", padre, llamada))
		for tipo, padre, llamada in secuencia:
			if tipo != "control":
				continue
			hermanos = [elemento for elemento in secuencia if elemento[1] == padre]
			indice = next(i for i, elemento in enumerate(hermanos) if elemento[2] is llamada)
			anterior = hermanos[indice - 1] if indice > 0 else None
			siguiente = hermanos[indice + 1] if indice + 1 < len(hermanos) else None
			tras_siguiente = hermanos[indice + 2] if indice + 2 < len(hermanos) else None
			if anterior is not None and anterior[0] == "etiqueta":
				continue
			if siguiente is None or siguiente[0] != "etiqueta":
				continue
			if tras_siguiente is not None and tras_siguiente[0] != "etiqueta":
				continue  # esa etiqueta es de la ventana que viene después
			texto = texto_constante(argumento(siguiente[2], 2, "label"))
			if texto is None or not (texto.rstrip().endswith(":") or "&" in texto):
				continue
			archivo.anotar(
				"etiqueta-tardia", llamada,
				f"{_clase_wx(archivo, llamada, locales)} creado antes que su etiqueta «{texto.strip()}» (línea {siguiente[2].lineno}).",
			)


def _llama_a_skip(nodo: ast.AST, evento: str) -> bool:
	"""La sentencia salta el evento o lo pasa a otra función (que decidirá)."""
	for hijo in recorrer_sin_anidados([nodo]):
		if not isinstance(hijo, ast.Call):
			continue
		if isinstance(hijo.func, ast.Attribute) and hijo.func.attr == "Skip":
			if not (hijo.args and es_constante(hijo.args[0], False)):
				return True
		if any(isinstance(arg, ast.Name) and arg.id == evento for arg in hijo.args):
			return True
		if any(isinstance(palabra.value, ast.Name) and palabra.value.id == evento for palabra in hijo.keywords):
			return True
	return False


def _puede_terminar_sin_skip(sentencias: list[ast.stmt], evento: str) -> bool:
	"""¿Puede una tecla NO gestionada llegar al final sin ``Skip()``?

	Se sigue solo el camino de «ninguna condición se cumplió»: los ``else``
	(explícitos o implícitos) de cada ``if``. Entrar en el cuerpo de un ``if``
	significa que la tecla se gestionó, y no saltarla ahí es correcto.
	"""
	for sentencia in sentencias:
		if isinstance(sentencia, (ast.Return, ast.Raise)):
			return False
		if isinstance(sentencia, ast.If):
			if _llama_a_skip(sentencia.test, evento):
				return False
			if sentencia.orelse and not _puede_terminar_sin_skip(sentencia.orelse, evento):
				return False
			continue
		if isinstance(sentencia, ast.Try):
			if any(_llama_a_skip(final, evento) for final in sentencia.finalbody):
				return False
			if not _puede_terminar_sin_skip(sentencia.body + sentencia.orelse, evento):
				return False
			continue
		if isinstance(sentencia, (ast.With, ast.AsyncWith)):
			if not _puede_terminar_sin_skip(sentencia.body, evento):
				return False
			continue
		if hasattr(ast, "Match") and isinstance(sentencia, ast.Match):
			comodines = [caso for caso in sentencia.cases if isinstance(caso.pattern, ast.MatchAs) and caso.pattern.pattern is None]
			if comodines and not _puede_terminar_sin_skip(comodines[0].body, evento):
				return False
			continue
		if isinstance(sentencia, (ast.For, ast.AsyncFor, ast.While)):
			continue  # el cuerpo puede no ejecutarse; no se cuenta su Skip()
		if _llama_a_skip(sentencia, evento):
			return False
	return True


def regla_char_hook(archivo: Archivo) -> None:
	vistos = set()
	for _, _, definicion in _manejadores(archivo, {"EVT_CHAR_HOOK"}):
		if id(definicion) in vistos:
			continue
		vistos.add(id(definicion))
		argumentos = definicion.args
		if argumentos.vararg is not None:
			continue
		parametros = [arg.arg for arg in argumentos.args]
		if parametros and parametros[0] in {"self", "cls"}:
			parametros = parametros[1:]
		if not parametros:
			continue
		if _puede_terminar_sin_skip(definicion.body, parametros[0]):
			archivo.anotar(
				"char-hook-sin-skip", definicion,
				f"{definicion.name}() (EVT_CHAR_HOOK) puede terminar sin {parametros[0]}.Skip(): bloquea las teclas no gestionadas.",
			)


# ════════════════════════════════════════════════════════════════════════════
# Reglas: empaquetado
# ════════════════════════════════════════════════════════════════════════════

def regla_cxfreeze(archivo: Archivo) -> None:
	if not any(nombre == "cx_Freeze" or nombre.startswith("cx_Freeze.") for nombre in archivo.alias.values()):
		return
	tiene_msvcr = False
	for nodo in ast.walk(archivo.arbol):
		if isinstance(nodo, ast.Dict):
			for clave, valor in zip(nodo.keys, nodo.values, strict=True):
				if es_constante(clave, "optimize") and es_constante(valor, 2):
					archivo.anotar("cxfreeze-optimize", valor, "optimize=2 en las opciones de build_exe.")
				if es_constante(clave, "include_msvcr") and not es_constante(valor, False):
					tiene_msvcr = True
		elif isinstance(nodo, ast.Call):
			for palabra in nodo.keywords:
				if palabra.arg == "optimize" and es_constante(palabra.value, 2):
					archivo.anotar("cxfreeze-optimize", palabra.value, "optimize=2 en la compilación.")
				if palabra.arg == "include_msvcr" and not es_constante(palabra.value, False):
					tiene_msvcr = True
	if not tiene_msvcr:
		for nodo in ast.walk(archivo.arbol):
			if isinstance(nodo, ast.Call) and archivo.nombre(nodo.func) == "cx_Freeze.setup":
				archivo.anotar("cxfreeze-msvcr", nodo, "setup() de cx_Freeze sin include_msvcr=True.")
				break


FUNCIONES_REGLA: list[Callable[[Archivo], None]] = [
	regla_excepciones, regla_print, regla_formato_registro, regla_basicconfig, regla_timeout, regla_shell,
	regla_eval, regla_pickle, regla_yaml, regla_verify, regla_secretos, regla_sql, regla_extraccion,
	regla_rutas, regla_getcwd, regla_registro_windows, regla_encoding, regla_hilos, regla_bloqueo_interfaz,
	regla_voz_duplicada, regla_controles, regla_etiquetas, regla_char_hook, regla_cxfreeze,
]


# ════════════════════════════════════════════════════════════════════════════
# Exclusiones
# ════════════════════════════════════════════════════════════════════════════

def exclusiones_en_linea(fuente: str) -> dict[int, set[str] | None]:
	"""Línea → reglas ignoradas (``None`` = todas), leídas de los comentarios.

	Un comentario en una línea propia se aplica también a la línea siguiente.
	"""
	resultado: dict[int, set[str] | None] = {}
	try:
		fichas = list(tokenize.generate_tokens(io.StringIO(fuente).readline))
	except (tokenize.TokenError, SyntaxError):
		log.debug("No se pudieron leer los comentarios", exc_info=True)
		return resultado
	lineas = fuente.splitlines()
	for ficha in fichas:
		if ficha.type != tokenize.COMMENT:
			continue
		coincidencia = PATRON_IGNORAR.search(ficha.string)
		if not coincidencia:
			continue
		texto = coincidencia.group(1).split(" - ")[0]
		reglas = {parte.strip() for parte in re.split(r"[,\s]+", texto) if parte.strip()} or None
		numero = ficha.start[0]
		destinos = [numero]
		if lineas[numero - 1].lstrip().startswith("#"):
			destinos.append(numero + 1)
		for destino in destinos:
			anterior = resultado.get(destino, set())
			resultado[destino] = None if reglas is None or anterior is None else anterior | reglas
	return resultado


def leer_archivo_exclusiones(ruta: Path) -> list[tuple[str, set[str] | None]]:
	"""Lee ``.auditoria-ignorar``: ``patron`` o ``patron: regla1, regla2``."""
	exclusiones: list[tuple[str, set[str] | None]] = []
	if not ruta.is_file():
		return exclusiones
	for linea in ruta.read_text(encoding="utf-8").splitlines():
		linea = linea.split("#", 1)[0].strip()
		if not linea:
			continue
		patron, _, reglas = linea.partition(":")
		conjunto = {regla.strip() for regla in reglas.split(",") if regla.strip()} or None
		exclusiones.append((patron.strip().replace("\\", "/"), conjunto))
	return exclusiones


def excluido(hallazgo: Hallazgo, exclusiones: list[tuple[str, set[str] | None]]) -> bool:
	for patron, reglas in exclusiones:
		if fnmatch.fnmatch(hallazgo.archivo, patron) and (reglas is None or hallazgo.regla in reglas):
			return True
	return False


# ════════════════════════════════════════════════════════════════════════════
# Análisis
# ════════════════════════════════════════════════════════════════════════════

def analizar_fuente(fuente: str, ruta: str = "app/modulo.py", es_app: bool = True, reglas: set[str] | None = None) -> list[Hallazgo]:
	"""Analiza un texto de código y devuelve sus hallazgos (sin exclusiones de archivo)."""
	try:
		arbol = ast.parse(fuente, filename=ruta)
	except SyntaxError as error:
		hallazgo = Hallazgo(
			"sintaxis", "alta", REGLAS["sintaxis"].area, ruta, error.lineno or 1,
			f"Error de sintaxis: {error.msg}.", REGLAS["sintaxis"].consejo,
		)
		return [hallazgo] if reglas is None or "sintaxis" in reglas else []
	archivo = Archivo(ruta=ruta, arbol=arbol, es_app=es_app, alias=construir_alias(arbol), lineas=fuente.splitlines())
	for funcion in FUNCIONES_REGLA:
		try:
			funcion(archivo)
		except Exception:  # noqa: BLE001 - una regla rota no debe impedir las demás
			log.exception("La regla %s falló en %s", funcion.__name__, ruta)
	ignorar = exclusiones_en_linea(fuente)
	resultado = []
	vistos = set()
	for hallazgo in archivo.hallazgos:
		clave = (hallazgo.regla, hallazgo.linea, hallazgo.mensaje)
		if clave in vistos:
			continue
		vistos.add(clave)
		if reglas is not None and hallazgo.regla not in reglas:
			continue
		if hallazgo.linea in ignorar and (ignorar[hallazgo.linea] is None or hallazgo.regla in ignorar[hallazgo.linea]):
			continue
		resultado.append(hallazgo)
	resultado.sort(key=lambda h: (h.linea, h.regla))
	return resultado


def buscar_archivos(raiz: Path, rutas: list[Path], incluir_pruebas: bool, excluir: set[str]) -> Iterator[Path]:
	omitidas = CARPETAS_EXCLUIDAS | excluir | (set() if incluir_pruebas else CARPETAS_PRUEBAS)
	for ruta in rutas:
		if ruta.is_file() and ruta.suffix == ".py":
			yield ruta
			continue
		for archivo in sorted(ruta.rglob("*.py")):
			relativa = archivo.relative_to(ruta).parts[:-1]
			if any(parte in omitidas or parte.endswith(".egg-info") for parte in relativa):
				continue
			yield archivo


def es_codigo_de_app(relativa: str, carpetas_app: list[str], hay_carpeta_app: bool) -> bool:
	"""Las reglas «solo app» se aplican al paquete de la aplicación y a main.py."""
	partes = relativa.split("/")
	if partes[0] in carpetas_app or relativa in {"main.py", "__main__.py"}:
		return True
	if hay_carpeta_app:
		return False
	# Sin carpeta de aplicación reconocible: todo salvo herramientas y compilación.
	return partes[0] not in {"tools", "scripts", "tests", "test"} and not partes[-1].startswith("setup")


def auditar(
	raiz: Path,
	rutas: list[Path] | None = None,
	reglas: set[str] | None = None,
	carpetas_app: list[str] | None = None,
	incluir_pruebas: bool = False,
	excluir_carpetas: set[str] | None = None,
	archivo_exclusiones: Path | None = None,
) -> tuple[list[Hallazgo], int]:
	"""Audita el proyecto. Devuelve (hallazgos ordenados, archivos analizados)."""
	raiz = raiz.resolve()
	carpetas_app = carpetas_app or ["app"]
	hay_carpeta_app = any((raiz / carpeta).is_dir() for carpeta in carpetas_app)
	exclusiones = leer_archivo_exclusiones(archivo_exclusiones or raiz / ARCHIVO_EXCLUSIONES)
	hallazgos: list[Hallazgo] = []
	total = 0
	for archivo in buscar_archivos(raiz, [ruta.resolve() for ruta in (rutas or [raiz])], incluir_pruebas, excluir_carpetas or set()):
		try:
			relativa = archivo.relative_to(raiz).as_posix()
		except ValueError:
			relativa = archivo.as_posix()
		try:
			fuente = archivo.read_text(encoding="utf-8-sig")
		except (OSError, UnicodeDecodeError) as error:
			log.warning("No se pudo leer %s: %s", relativa, error)
			continue
		total += 1
		for hallazgo in analizar_fuente(fuente, relativa, es_codigo_de_app(relativa, carpetas_app, hay_carpeta_app), reglas):
			if not excluido(hallazgo, exclusiones):
				hallazgos.append(hallazgo)
	hallazgos.sort(key=lambda h: (ORDEN_SEVERIDAD[h.severidad], h.archivo, h.linea, h.regla))
	return hallazgos, total


# ════════════════════════════════════════════════════════════════════════════
# Informes
# ════════════════════════════════════════════════════════════════════════════

def _resumen(hallazgos: list[Hallazgo], total: int) -> str:
	archivos = len({h.archivo for h in hallazgos})
	cuentas = ", ".join(f"{sum(h.severidad == s for h in hallazgos)} de severidad {s}" for s in SEVERIDADES)
	return f"{len(hallazgos)} hallazgos en {archivos} de {total} archivos analizados: {cuentas}."


def informe_texto(hallazgos: list[Hallazgo], total: int) -> str:
	"""Texto lineal: una línea por hallazgo, agrupado por severidad (cómodo con lector)."""
	lineas = ["Auditoría rápida.", _resumen(hallazgos, total)]
	for severidad in SEVERIDADES:
		grupo = [h for h in hallazgos if h.severidad == severidad]
		if not grupo:
			continue
		lineas.append("")
		lineas.append(f"Severidad {severidad}: {len(grupo)}.")
		for h in grupo:
			revisar = " (revisar)" if h.revisar else ""
			lineas.append(f"{h.ubicacion}: {h.regla}{revisar}. {h.mensaje} Consejo: {h.consejo}")
	if not hallazgos:
		lineas.append("Sin hallazgos. Recuerda que el detector no sustituye a la revisión manual.")
	return "\n".join(lineas) + "\n"


def informe_markdown(hallazgos: list[Hallazgo], total: int) -> str:
	"""Markdown con encabezados y listas (sin tablas grandes)."""
	lineas = ["# Auditoría rápida", "", _resumen(hallazgos, total), ""]
	por_regla: dict[str, int] = {}
	for h in hallazgos:
		por_regla[h.regla] = por_regla.get(h.regla, 0) + 1
	if por_regla:
		lineas.append("## Por regla")
		lineas.append("")
		for regla, cuenta in sorted(por_regla.items(), key=lambda par: (-par[1], par[0])):
			lineas.append(f"- `{regla}` ({REGLAS[regla].area}): {cuenta}")
		lineas.append("")
	for severidad in SEVERIDADES:
		grupo = [h for h in hallazgos if h.severidad == severidad]
		if not grupo:
			continue
		lineas.append(f"## Severidad {severidad} ({len(grupo)})")
		lineas.append("")
		for h in grupo:
			revisar = " (revisar)" if h.revisar else ""
			lineas.append(f"- `{h.ubicacion}`, `{h.regla}`{revisar}: {h.mensaje} Consejo: {h.consejo}")
		lineas.append("")
	if not hallazgos:
		lineas.append("Sin hallazgos. El detector no sustituye a la revisión manual.")
		lineas.append("")
	return "\n".join(lineas)


def informe_json(hallazgos: list[Hallazgo], total: int) -> str:
	return json.dumps({"archivos_analizados": total, "hallazgos": [asdict(h) for h in hallazgos]}, ensure_ascii=False, indent=1)


def listar_reglas() -> str:
	lineas = []
	for regla in sorted(REGLAS.values(), key=lambda r: (r.area, r.nombre)):
		extra = ", solo código de la aplicación" if regla.solo_app else ""
		extra += ", heurística" if regla.heuristica else ""
		lineas.append(f"{regla.nombre} ({regla.area}, {regla.severidad}{extra}): {regla.descripcion}")
	return "\n".join(lineas) + "\n"


# ════════════════════════════════════════════════════════════════════════════
# Línea de órdenes
# ════════════════════════════════════════════════════════════════════════════

def _lista(texto: str | None) -> set[str]:
	return {parte.strip() for parte in (texto or "").split(",") if parte.strip()}


def _escribir(texto: str) -> None:
	"""Escribe en la consola sin fallar si no admite algún carácter."""
	if hasattr(sys.stdout, "reconfigure"):
		sys.stdout.reconfigure(errors="replace")
	sys.stdout.write(texto)


def main(argumentos: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Detector de antipatrones para proyectos Python + wxPython.")
	analizador.add_argument("rutas", nargs="*", help="archivos o carpetas (por defecto, la raíz)")
	analizador.add_argument("--raiz", default=".", help="raíz del proyecto (por defecto, la carpeta actual)")
	analizador.add_argument("--markdown", action="store_true", help="informe en Markdown")
	analizador.add_argument("--json", action="store_true", help="informe en JSON")
	analizador.add_argument("--salida", help="guardar el informe en este archivo (UTF-8)")
	analizador.add_argument("--reglas", help="solo estas reglas, separadas por comas")
	analizador.add_argument("--sin-reglas", help="omitir estas reglas, separadas por comas")
	analizador.add_argument("--severidad-minima", choices=SEVERIDADES, default="baja", help="no mostrar lo menos grave")
	analizador.add_argument("--fallar-desde", choices=SEVERIDADES, default="baja", help="severidad que provoca código de salida 1")
	analizador.add_argument("--carpeta-app", action="append", help="paquete de la aplicación (por defecto, app); repetible")
	analizador.add_argument("--incluir-pruebas", action="store_true", help="analizar también tests/")
	analizador.add_argument("--excluir", help="carpetas adicionales a omitir, separadas por comas")
	analizador.add_argument("--exclusiones", help=f"archivo de exclusiones (por defecto, {ARCHIVO_EXCLUSIONES} en la raíz)")
	analizador.add_argument("--listar-reglas", action="store_true", help="mostrar las reglas y salir")
	try:
		opciones = analizador.parse_args(argumentos)
	except SystemExit as salida:
		# argparse sale con 0 tras --help y con 2 ante un error de uso.
		return 0 if not salida.code else 2
	if opciones.listar_reglas:
		_escribir(listar_reglas())
		return 0
	solicitadas = _lista(opciones.reglas) or set(REGLAS)
	desconocidas = (solicitadas | _lista(opciones.sin_reglas)) - set(REGLAS)
	if desconocidas:
		sys.stderr.write(f"Reglas desconocidas: {', '.join(sorted(desconocidas))}. Usa --listar-reglas.\n")
		return 2
	reglas = solicitadas - _lista(opciones.sin_reglas)
	raiz = Path(opciones.raiz)
	rutas = [Path(ruta) for ruta in opciones.rutas] or None
	for ruta in rutas or []:
		if not ruta.exists():
			sys.stderr.write(f"No existe: {ruta}\n")
			return 2
	hallazgos, total = auditar(
		raiz, rutas, reglas, opciones.carpeta_app, opciones.incluir_pruebas, _lista(opciones.excluir),
		Path(opciones.exclusiones) if opciones.exclusiones else None,
	)
	hallazgos = [h for h in hallazgos if ORDEN_SEVERIDAD[h.severidad] <= ORDEN_SEVERIDAD[opciones.severidad_minima]]
	if opciones.json:
		texto = informe_json(hallazgos, total)
	elif opciones.markdown:
		texto = informe_markdown(hallazgos, total)
	else:
		texto = informe_texto(hallazgos, total)
	if opciones.salida:
		Path(opciones.salida).write_text(texto, encoding="utf-8")
		sys.stdout.write(f"Informe guardado en {opciones.salida}. {_resumen(hallazgos, total)}\n")
	else:
		_escribir(texto)
	graves = [h for h in hallazgos if ORDEN_SEVERIDAD[h.severidad] <= ORDEN_SEVERIDAD[opciones.fallar_desde]]
	return 1 if graves else 0


if __name__ == "__main__":
	logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
	sys.exit(main())
