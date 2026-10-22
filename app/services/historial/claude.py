"""Historial de Claude Code: lector de SOLO LECTURA de las sesiones del equipo.

Reúne en una sola lista las sesiones de Claude Code que hay en este equipo:

- **Terminal**: las que alguien empezó escribiendo ``claude`` en una consola.
  Ningún archivo de metadatos las describe; se encuentran por sus transcripts
  en ``<raíz>/projects/<carpeta codificada>/<sessionId>.jsonl`` cuyo primer
  registro con ``entrypoint`` dice ``"cli"``.
- **Escritorio**: las de la aplicación de escritorio de Claude
  (``%APPDATA%\\Claude\\claude-code-sessions\\**\\local_*.json``, y la misma
  carpeta de la versión de la Microsoft Store dentro de
  ``%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming``).
- **Cowork**: ``...\\Claude\\local-agent-mode-sessions\\*\\*\\local_*.json``.
  Cada una tiene su propia raíz de Claude Code en la carpeta hermana
  ``local_<id>\\.claude`` (rutas muy largas: se usan con el prefijo ``\\\\?\\``).
- **Ctrl+IA**: las creadas por esta aplicación (``claude -p``); quien llama
  pasa sus identificadores en ``ids_propios``.
- **Otro equipo por SSH**: ``historial/equipos.py`` trae el principio y el
  final de los transcripts de un Mac o un Linux y ``sesiones_de_otro_equipo``
  los convierte en la misma lista (con ``equipo`` y ``ruta_remota``).

El estado vivo (trabajando, esperando) sale de ``<raíz>/sessions/<pid>.json``
y solo cuenta si el proceso sigue vivo y es el mismo que escribió el archivo.

NUNCA se escribe en ninguno de estos archivos: todo se abre en modo lectura.
El formato es interno de Claude Code y cambia entre versiones, así que todo
campo se lee a la defensiva: un tipo de registro desconocido se ignora y una
línea que no se entiende se cuenta (``errores``), nunca lanza una excepción.

Sin wx: todo este módulo se prueba por sí solo.
"""

from __future__ import annotations

import ctypes
import functools
import json
import logging
import ntpath
import os
import re
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valores públicos
# ---------------------------------------------------------------------------

ORIGEN_TERMINAL = "terminal"
ORIGEN_ESCRITORIO = "escritorio"
ORIGEN_COWORK = "cowork"
ORIGEN_CTRLIA = "ctrlia"
ORIGEN_OTRA = "otra"

ESTADO_INACTIVA = "inactiva"
ESTADO_TRABAJANDO = "trabajando"
ESTADO_ESPERANDO = "esperando"
ESTADO_DESCONOCIDO = "desconocido"

ROL_USUARIO = "usuario"
ROL_ASISTENTE = "asistente"
ROL_HERRAMIENTA = "herramienta"
ROL_SISTEMA = "sistema"
ROL_ERROR = "error"


@dataclass(slots=True)
class SesionClaude:
	"""Una sesión de Claude Code tal como la muestra la lista."""

	clave: str  # "claude:<sessionId>"
	id: str  # sessionId del CLI (uuid)
	titulo: str
	carpeta: str
	origen: str  # "terminal" | "escritorio" | "cowork" | "ctrlia" | "otra"
	creada: float  # epoch s
	actividad: float  # epoch s (mtime o lastActivityAt)
	estado: str  # "inactiva" | "trabajando" | "esperando" | "desconocido"
	archivada: bool
	ruta_transcript: Path | None
	id_escritorio: str = ""  # local_… si aplica
	ultimo_mensaje: str = ""  # texto corto del último mensaje del asistente
	equipo: str = ""  # id del equipo por SSH; vacío en las de este equipo
	ruta_remota: str = ""  # transcript en ese equipo (ruta POSIX)
	#: Un proceso de Claude Code (terminal, app) tiene la sesión abierta ahora, aunque esté sin hacer nada.
	abierta: bool = False


@dataclass(slots=True)
class Mensaje:
	"""Un mensaje legible de una conversación."""

	rol: str  # "usuario" | "asistente" | "herramienta" | "sistema" | "error"
	texto: str  # texto completo legible
	fecha: float  # epoch s (timestamp ISO del registro)
	id: str = ""
	herramienta: str = ""  # nombre si rol herramienta
	detalle: str = ""  # entrada/salida de la herramienta, legible
	cambios: list[dict] = field(default_factory=list)  # [{"ruta":..,"diff":..}]


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

_NO_ALFANUMERICO = re.compile(r"[^A-Za-z0-9]")
# Un id acaba en nombres de archivo y patrones de glob: nada que no sea un id
# simple (un metadato malformado no puede llevarnos a otro archivo).
_ID_SEGURO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")


def raiz_claude() -> Path:
	"""Raíz de configuración de Claude Code: ``CLAUDE_CONFIG_DIR`` o ``~/.claude``."""
	otra = os.environ.get("CLAUDE_CONFIG_DIR")
	if otra:
		return Path(otra)
	return Path.home() / ".claude"


def codificar_carpeta(carpeta: str) -> str:
	"""Nombre de carpeta que usa Claude Code para los transcripts de ``carpeta``.

	Todo carácter que no sea letra o dígito ASCII pasa a ``-``:
	``C:\\Users\\ana\\proyecto`` → ``C--Users-ana-proyecto``.
	"""
	return _NO_ALFANUMERICO.sub("-", carpeta or "")


def es_id_seguro(valor: str) -> bool:
	"""Si ``valor`` es un identificador simple, utilizable en rutas."""
	return isinstance(valor, str) and bool(_ID_SEGURO.match(valor))


def ruta_transcript(session_id: str, carpeta: str = "", proyectos: Path | None = None) -> Path | None:
	"""Transcript de una sesión, o ``None`` si no está en disco.

	Primero donde lo pone Claude Code (carpeta codificada); si no está (ruta
	muy larga que Claude Code acortó, cambio de formato), se busca el id en
	todas las carpetas de proyectos: es único por sí solo.
	"""
	if not es_id_seguro(session_id):
		return None
	proyectos = proyectos if proyectos is not None else raiz_claude() / "projects"
	if carpeta:
		directa = proyectos / codificar_carpeta(carpeta) / f"{session_id}.jsonl"
		if directa.is_file():
			return directa
	try:
		for candidata in proyectos.glob(f"*/{session_id}.jsonl"):
			if candidata.is_file():
				return candidata
	except OSError:
		log.debug("No se pudo buscar el transcript %s en %s", session_id, proyectos, exc_info=True)
	return None


def _ruta_larga(ruta: Path) -> Path:
	"""``ruta`` con el prefijo ``\\\\?\\`` en Windows: sin límite de 260 caracteres.

	Los transcripts de Cowork viven en la carpeta del paquete de la Store más
	la carpeta de trabajo codificada otra vez: pasan de largo los 260
	caracteres que Windows permite si no se activan las rutas largas.
	"""
	if sys.platform != "win32":
		return ruta
	texto = str(ruta)
	if texto.startswith(("\\\\?\\", "\\\\.\\")) or not ruta.is_absolute():
		return ruta
	texto = ntpath.normpath(texto)
	if texto.startswith("\\\\"):
		return Path("\\\\?\\UNC\\" + texto[2:])
	return Path("\\\\?\\" + texto)


def _carpeta_roaming() -> Path:
	appdata = os.environ.get("APPDATA")  # auditoria: ignorar ruta-no-portable - lee el historial de la app de Claude; solo lectura
	return Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"


def _carpetas_store(nombre: str) -> list[Path]:
	"""``nombre`` dentro de la aplicación de la Microsoft Store (``Claude_*``)."""
	local = os.environ.get("LOCALAPPDATA")  # auditoria: ignorar ruta-no-portable - lee el historial de la app de Claude; solo lectura
	paquetes = Path(local) / "Packages" if local else Path.home() / "AppData" / "Local" / "Packages"
	try:
		return sorted(p / "LocalCache" / "Roaming" / "Claude" / nombre for p in paquetes.glob("Claude_*"))
	except OSError:
		log.debug("No se pudo recorrer %s", paquetes, exc_info=True)
		return []


def carpetas_escritorio() -> list[Path]:
	"""Carpetas existentes con sesiones de la aplicación de escritorio."""
	candidatas = [_carpeta_roaming() / "Claude" / "claude-code-sessions", *_carpetas_store("claude-code-sessions")]
	return [c for c in candidatas if c.is_dir()]


def carpetas_cowork() -> list[Path]:
	"""Carpetas existentes con sesiones de Cowork (en forma larga)."""
	nombre = "local-agent-mode-sessions"
	candidatas = [_carpeta_roaming() / "Claude" / nombre, *_carpetas_store(nombre)]
	return [_ruta_larga(c) for c in candidatas if c.is_dir()]


# ---------------------------------------------------------------------------
# Procesos (estado vivo)
# ---------------------------------------------------------------------------

#: ``_inicio_proceso`` de un proceso ajeno (acceso denegado): no es un Claude
#: Code de este usuario.
_NO_ES_TUYO = -1
_FILETIME_DESDE = 125_911_584_000_000_000  # año 2000
_FILETIME_HASTA = 189_025_920_000_000_000  # año 2200
_FILETIME_EPOCA_MS = 11_644_473_600_000  # de 1601 a 1970
_MARGEN_INICIO_MS = 30_000


@functools.cache
def _kernel32() -> Any:
	"""kernel32 con los prototipos que usamos (solo Windows)."""
	from ctypes import wintypes

	k32 = ctypes.WinDLL("kernel32", use_last_error=True)
	k32.OpenProcess.restype = wintypes.HANDLE
	k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
	k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
	k32.GetExitCodeProcess.restype = wintypes.BOOL
	k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
	k32.GetProcessTimes.restype = wintypes.BOOL
	k32.CloseHandle.argtypes = [wintypes.HANDLE]
	return k32


_CONSULTA_LIMITADA = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
_ACCESO_DENEGADO = 5
_SIGUE_ACTIVO = 259  # STILL_ACTIVE


def _proceso_vivo(pid: int) -> bool:
	"""Si hay un proceso vivo con ese pid.

	En Windows no se usa ``os.kill(pid, 0)``: la señal 0 es CTRL_C_EVENT y
	interrumpiría el proceso en vez de consultarlo.
	"""
	if not isinstance(pid, int) or pid <= 0:
		return False
	if sys.platform != "win32":
		try:
			os.kill(pid, 0)
		except PermissionError:
			return True
		except OSError:
			return False
		return True
	from ctypes import wintypes

	k32 = _kernel32()
	manejador = k32.OpenProcess(_CONSULTA_LIMITADA, False, pid)
	if not manejador:
		# Acceso denegado también quiere decir que existe.
		return ctypes.get_last_error() == _ACCESO_DENEGADO
	try:
		codigo = wintypes.DWORD()
		if not k32.GetExitCodeProcess(manejador, ctypes.byref(codigo)):
			return False
		return codigo.value == _SIGUE_ACTIVO
	finally:
		k32.CloseHandle(manejador)


def _inicio_proceso(pid: int) -> int | None:
	"""Cuándo empezó el proceso, como FILETIME (lo que Claude Code anota en
	``procStart``); ``_NO_ES_TUYO`` si Windows no lo dice; ``None`` si no se sabe."""
	if sys.platform != "win32" or not isinstance(pid, int) or pid <= 0:
		return None
	from ctypes import wintypes

	k32 = _kernel32()
	manejador = k32.OpenProcess(_CONSULTA_LIMITADA, False, pid)
	if not manejador:
		return _NO_ES_TUYO if ctypes.get_last_error() == _ACCESO_DENEGADO else None
	try:
		tiempos = [wintypes.FILETIME() for _ in range(4)]
		if not k32.GetProcessTimes(manejador, *[ctypes.byref(t) for t in tiempos]):
			return None
		return (tiempos[0].dwHighDateTime << 32) | tiempos[0].dwLowDateTime
	finally:
		k32.CloseHandle(manejador)


def _entero(valor: Any) -> int | None:
	try:
		return int(float(str(valor)))
	except (TypeError, ValueError, OverflowError):
		return None


def _mismo_proceso(anotado: Any, real: int | None, iniciado: Any) -> bool:
	"""Si el ``procStart`` del archivo (o su ``startedAt``) es el del proceso
	que corre ahora con ese pid: Windows reutiliza los pid."""
	anotado_n, iniciado_ms = _entero(anotado), _entero(iniciado)
	if (anotado_n is None and iniciado_ms is None) or real is None:
		return True
	if real == _NO_ES_TUYO:
		return False
	if anotado_n is not None and _FILETIME_DESDE <= anotado_n <= _FILETIME_HASTA:
		return abs(anotado_n - real) < 10_000_000  # menos de un segundo
	if iniciado_ms is None:
		return True
	real_ms = real // 10_000 - _FILETIME_EPOCA_MS
	return abs(iniciado_ms - real_ms) < _MARGEN_INICIO_MS


@dataclass(slots=True)
class _EstadoVivo:
	estado: str  # busy | idle | waiting | otro
	pid: int
	esperando: str = ""


def _cargar_estado_vivo(carpeta: Path) -> dict[str, _EstadoVivo]:
	"""``sessionId`` → estado, de ``<raíz>/sessions/<pid>.json`` con proceso vivo."""
	resultado: dict[str, _EstadoVivo] = {}
	try:
		archivos = list(carpeta.glob("*.json"))
	except OSError:
		log.debug("No se pudo leer %s", carpeta, exc_info=True)
		return resultado
	for ruta in archivos:
		datos = _leer_json(ruta)
		if datos is None:
			continue
		pid = datos.get("pid")
		if not isinstance(pid, int):
			pid = _entero(ruta.stem)
			if pid is None:
				continue
		if not _proceso_vivo(pid):
			continue
		if not _mismo_proceso(datos.get("procStart"), _inicio_proceso(pid), datos.get("startedAt")):
			continue
		estado = _EstadoVivo(
			estado=str(datos.get("status") or ""),
			pid=pid,
			esperando=" ".join(str(datos.get("waitingFor") or "").split()),
		)
		for campo in ("sessionId", "hostSessionId"):
			valor = datos.get(campo)
			if isinstance(valor, str) and valor:
				resultado[valor] = estado
	return resultado


def _estado_de_vivo(vivo: _EstadoVivo | None) -> str | None:
	"""Estado de la lista según el archivo vivo; ``None`` si no dice nada."""
	if vivo is None:
		return None
	if vivo.estado == "busy":
		return ESTADO_TRABAJANDO
	if vivo.estado == "waiting":
		return ESTADO_ESPERANDO
	if vivo.estado == "idle":
		return ESTADO_INACTIVA
	return ESTADO_DESCONOCIDO


# ---------------------------------------------------------------------------
# Utilidades de texto y fechas
# ---------------------------------------------------------------------------

_RECORDATORIO = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_ETIQUETA_INICIAL = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9_-]*)>")
_CUALQUIER_ETIQUETA = re.compile(r"</?[A-Za-z][A-Za-z0-9_-]*>")
_SALIDA_ORDEN = re.compile(r"\s*<local-command-(stdout|stderr)>(.*?)</local-command-\1>\s*", re.DOTALL)
_INTERRUPCION = re.compile(r"^\[Request interrupted by user[^\]]*\]", re.IGNORECASE)
_NOMBRE_ORDEN = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_ARGS_ORDEN = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CODIGOS_TERMINAL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Máximo de la salida de una herramienta que se guarda en ``detalle``.
MAX_DETALLE = 20_000


def _sin_recordatorios(texto: str) -> str:
	return _RECORDATORIO.sub("", texto or "")


def _limpiar_salida(texto: str) -> str:
	"""Salida de terminal sin códigos de color ni caracteres de control."""
	texto = _CODIGOS_TERMINAL.sub("", texto or "").replace("\r\n", "\n").replace("\r", "\n")
	return _CONTROL.sub("", texto).strip()


def _recortar(texto: str, maximo: int) -> str:
	if len(texto) <= maximo:
		return texto
	return texto[:maximo].rstrip() + "\n[… recortado]"


def _una_linea(texto: Any, maximo: int = 0) -> str:
	linea = " ".join(str(texto or "").split())
	if maximo and len(linea) > maximo:
		linea = linea[: maximo - 1].rstrip() + "…"
	return linea


def _primera_linea(texto: str, maximo: int) -> str:
	for linea in (texto or "").splitlines():
		if linea.strip():
			return _una_linea(linea, maximo)
	return ""


def _fecha(registro: dict) -> float:
	"""``timestamp`` ISO 8601 del registro en segundos desde 1970 (0 si no hay)."""
	sello = registro.get("timestamp")
	if not isinstance(sello, str) or not sello:
		return 0.0
	try:
		momento = datetime.fromisoformat(sello.replace("Z", "+00:00"))
	except ValueError:
		return 0.0
	if momento.tzinfo is None:
		momento = momento.replace(tzinfo=timezone.utc)
	return momento.timestamp()


def _segundos(valor: Any) -> float:
	"""Un instante de los metadatos (ms, s o ISO) en segundos desde 1970."""
	if isinstance(valor, bool):
		return 0.0
	if isinstance(valor, (int, float)):
		return float(valor) / 1000.0 if valor > 100_000_000_000 else float(valor)
	if isinstance(valor, str) and valor:
		numero = _entero(valor)
		if numero is not None and valor.strip().isdigit():
			return _segundos(numero)
		return _fecha({"timestamp": valor})
	return 0.0


def _texto_plano(contenido: Any) -> str:
	"""Texto de una cadena, de una lista de bloques o de cualquier otra cosa."""
	if contenido is None:
		return ""
	if isinstance(contenido, str):
		return contenido
	if isinstance(contenido, list):
		partes: list[str] = []
		for bloque in contenido:
			if isinstance(bloque, str):
				partes.append(bloque)
			elif isinstance(bloque, dict):
				tipo = bloque.get("type")
				if tipo == "text":
					partes.append(str(bloque.get("text") or ""))
				elif tipo == "image":
					partes.append("(imagen adjunta)")
				elif tipo == "document":
					partes.append("(documento adjunto)")
				elif tipo == "tool_reference":
					partes.append("[herramienta %s]" % bloque.get("tool_name", ""))
		return "\n".join(p for p in partes if p)
	return str(contenido)


def _normalizar(texto: str) -> str:
	"""Minúsculas y sin acentos, para buscar."""
	descompuesto = unicodedata.normalize("NFD", (texto or "").casefold())
	return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _leer_json(ruta: Path) -> dict | None:
	try:
		with open(ruta, "rb") as archivo:
			datos = json.loads(archivo.read().decode("utf-8", errors="replace"))
	except (OSError, ValueError):
		log.debug("No se pudo leer %s", ruta, exc_info=True)
		return None
	return datos if isinstance(datos, dict) else None


def _json_linea(linea: bytes) -> dict | None:
	try:
		registro = json.loads(linea.decode("utf-8", errors="replace"))
	except ValueError:
		return None
	return registro if isinstance(registro, dict) else None


# ---------------------------------------------------------------------------
# Herramientas: cómo se dicen en español
# ---------------------------------------------------------------------------

#: Nombre de herramienta → (verbo, campos de la entrada que la resumen).
_HERRAMIENTAS: dict[str, tuple[str, tuple[str, ...]]] = {
	"Bash": ("Ejecutar", ("command",)),
	"PowerShell": ("Ejecutar", ("command",)),
	"Read": ("Leer", ("file_path", "path")),
	"Edit": ("Editar", ("file_path",)),
	"MultiEdit": ("Editar", ("file_path",)),
	"Write": ("Escribir", ("file_path",)),
	"NotebookEdit": ("Editar cuaderno", ("notebook_path",)),
	"Grep": ("Buscar", ("pattern",)),
	"Glob": ("Buscar archivos", ("pattern",)),
	"WebFetch": ("Consultar web", ("url",)),
	"WebSearch": ("Buscar en internet", ("query",)),
	"Task": ("Delegar en agente", ("description", "prompt")),
	"Agent": ("Delegar en agente", ("description", "prompt")),
	"TodoWrite": ("Actualizar lista de tareas", ()),
	"TaskCreate": ("Crear tarea", ("subject",)),
	"TaskUpdate": ("Actualizar tarea", ("taskId",)),
	"ReportFindings": ("Informar de los hallazgos de la revisión", ()),
	"Skill":("Usar habilidad", ("skill",)),
	"ToolSearch": ("Cargar herramientas", ("query",)),
}

_HERRAMIENTAS_DE_CAMBIO = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})


def describir_herramienta(nombre: str, entrada: dict) -> str:
	"""Una línea en español con lo que hace una llamada: «Ejecutar: git status»."""
	conocida = _HERRAMIENTAS.get(nombre)
	if conocida is None:
		return "Herramienta %s" % nombre
	verbo, campos = conocida
	for campo in campos:
		valor = entrada.get(campo)
		if isinstance(valor, str) and valor.strip():
			resumen = _una_linea(valor, 200)
			if nombre == "Grep" and isinstance(entrada.get("path"), str) and entrada["path"]:
				resumen += " en " + _una_linea(entrada["path"], 120)
			return "%s: %s" % (verbo, resumen)
	return verbo


def _detalle_entrada(nombre: str, entrada: dict) -> str:
	"""La entrada de una herramienta, legible."""
	if not entrada:
		return ""
	if nombre in ("Bash", "PowerShell"):
		partes = []
		if entrada.get("description"):
			partes.append("Descripción: %s" % _una_linea(entrada["description"]))
		partes.append("Orden:\n%s" % str(entrada.get("command") or "").strip())
		return "\n".join(partes)
	if nombre == "TodoWrite" and isinstance(entrada.get("todos"), list):
		marcas = {"completed": "[x]", "in_progress": "[en curso]"}
		lineas = []
		for tarea in entrada["todos"]:
			if isinstance(tarea, dict):
				marca = marcas.get(str(tarea.get("status")), "[ ]")
				lineas.append("%s %s" % (marca, _una_linea(tarea.get("content"))))
		return "Tareas:\n" + "\n".join(lineas)
	if nombre in ("Edit", "MultiEdit", "Write", "Read"):
		return "Archivo: %s" % (entrada.get("file_path") or entrada.get("path") or "")
	try:
		texto = json.dumps(entrada, ensure_ascii=False, indent=1)
	except (TypeError, ValueError):
		texto = str(entrada)
	return "Entrada:\n" + _recortar(texto, MAX_DETALLE)


def _cambios(resultado: Any, entrada: dict) -> list[dict]:
	"""Cambios de archivos a partir de ``toolUseResult`` (``structuredPatch``)."""
	if not isinstance(resultado, dict):
		return []
	ruta = str(resultado.get("filePath") or entrada.get("file_path") or entrada.get("notebook_path") or "")
	if not ruta:
		return []
	parche = resultado.get("structuredPatch")
	lineas: list[str] = []
	if isinstance(parche, list) and parche:
		for trozo in parche:
			if not isinstance(trozo, dict) or not isinstance(trozo.get("lines"), list):
				continue
			lineas.append(
				"@@ -%s,%s +%s,%s @@"
				% (trozo.get("oldStart", 0), trozo.get("oldLines", 0), trozo.get("newStart", 0), trozo.get("newLines", 0))
			)
			lineas.extend(str(linea) for linea in trozo["lines"])
	elif resultado.get("type") == "create" and isinstance(resultado.get("content"), str):
		lineas = ["+" + linea for linea in resultado["content"].split("\n")]
	if not lineas:
		return []
	return [{"ruta": ruta, "diff": "\n".join(lineas)}]


def _formatear_preguntas(entrada: dict) -> str:
	preguntas = entrada.get("questions")
	if not isinstance(preguntas, list) or not preguntas:
		return "Claude pregunta (no se pudo leer la pregunta)."
	partes = []
	for pregunta in preguntas:
		if not isinstance(pregunta, dict):
			continue
		texto = str(pregunta.get("question") or pregunta.get("header") or "").strip()
		opciones = []
		for opcion in pregunta.get("options") or []:
			if isinstance(opcion, dict):
				etiqueta = str(opcion.get("label") or "").strip()
				descripcion = str(opcion.get("description") or "").strip()
				if etiqueta:
					opciones.append("%s (%s)" % (etiqueta, descripcion) if descripcion else etiqueta)
			elif isinstance(opcion, str):
				opciones.append(opcion)
		if opciones:
			texto += "\nOpciones: %s." % "; ".join(opciones)
		partes.append(texto)
	return "Claude pregunta:\n" + "\n\n".join(partes)


def _formatear_respuestas(resultado: Any, texto: str) -> str:
	if isinstance(resultado, dict):
		respuestas = resultado.get("answers")
		if isinstance(respuestas, dict) and respuestas:
			return "Respuesta:\n" + "\n".join("%s — %s" % (p, r) for p, r in respuestas.items())
	texto = texto.strip()
	prefijo = "Your questions have been answered:"
	if texto.startswith(prefijo):
		texto = texto[len(prefijo) :].strip()
	return "Respuesta:\n" + (texto or "(respondida)")


# ---------------------------------------------------------------------------
# Análisis de transcripts
# ---------------------------------------------------------------------------


class _Analizador:
	"""Convierte registros JSONL en mensajes, conservando el estado entre
	llamadas para poder procesar solo lo añadido a un archivo que crece.

	Con ``laterales`` se leen también los registros de subagente
	(``isSidechain``): los transcripts de ``subagents/`` solo tienen esos.
	"""

	def __init__(self, laterales: bool = False) -> None:
		self.laterales = laterales
		self.mensajes: list[Mensaje] = []
		self.errores = 0
		self.uso_contexto: int | None = None
		self._herramientas: dict[str, tuple[str, dict, Mensaje | None]] = {}
		self._asistente: Mensaje | None = None  # el último, para fusionar por message.id
		self._en_cola: list[str] = []  # mensajes del usuario encolados (adjuntos)
		self._nuevos: list[Mensaje] = []
		self._modificados: list[Mensaje] = []

	# -- público -------------------------------------------------------------

	def procesar(self, lineas: Iterable[bytes]) -> list[Mensaje]:
		"""Procesa líneas completas; devuelve los mensajes añadidos."""
		self._nuevos = []
		self._modificados = []
		for linea in lineas:
			if not linea.strip():
				continue
			registro = _json_linea(linea)
			if registro is None:
				self.errores += 1
				continue
			try:
				self._registro(registro)
			except Exception:  # noqa: BLE001 - un cambio de formato nunca debe tumbar la aplicación
				self.errores += 1
				log.debug("Registro de transcript no reconocido", exc_info=True)
		return self._nuevos

	def modificados(self) -> list[Mensaje]:
		"""Mensajes anteriores a la última llamada que esta ha cambiado."""
		nuevos = {id(m) for m in self._nuevos}
		vistos: set[int] = set()
		resultado = []
		for mensaje in self._modificados:
			if id(mensaje) not in nuevos and id(mensaje) not in vistos:
				vistos.add(id(mensaje))
				resultado.append(mensaje)
		return resultado

	# -- registros -------------------------------------------------------------

	def _anadir(self, rol: str, texto: str, registro: dict, ident: str = "", **extra: Any) -> Mensaje:
		mensaje = Mensaje(
			rol=rol, texto=texto.strip(), fecha=_fecha(registro), id=ident or str(registro.get("uuid") or ""), **extra
		)
		self.mensajes.append(mensaje)
		self._nuevos.append(mensaje)
		return mensaje

	def _registro(self, registro: dict) -> None:
		if registro.get("isSidechain") and not self.laterales:
			return
		tipo = registro.get("type")
		if tipo == "system":
			self._sistema(registro)
			return
		if tipo == "attachment":
			self._adjunto(registro)
			return
		if tipo not in ("user", "assistant"):
			return  # summary, custom-title, ai-title y demás: no son conversación
		mensaje = registro.get("message")
		if not isinstance(mensaje, dict):
			self.errores += 1
			return
		contenido = mensaje.get("content")
		if tipo == "user":
			self._asistente = None
			self._usuario(registro, contenido)
		else:
			self._anotar_uso(mensaje)
			self._de_asistente(registro, mensaje, contenido)

	def _sistema(self, registro: dict) -> None:
		subtipo = registro.get("subtype")
		if subtipo == "compact_boundary":
			self._asistente = None
			meta = registro.get("compactMetadata")
			despues = meta.get("postTokens") if isinstance(meta, dict) else None
			self.uso_contexto = despues if isinstance(despues, int) and despues >= 0 else None
			self._anadir(ROL_SISTEMA, "Conversación compactada para liberar contexto.", registro)
		elif subtipo == "local_command":
			contenido = registro.get("content")
			if isinstance(contenido, str):
				self._salida_orden(contenido, registro)

	def _adjunto(self, registro: dict) -> None:
		"""De los adjuntos solo es conversación un mensaje encolado: lo que el
		usuario escribió mientras Claude trabajaba, o el de otra sesión."""
		adjunto = registro.get("attachment")
		if not isinstance(adjunto, dict) or adjunto.get("type") != "queued_command":
			return
		origen = adjunto.get("origin")
		tipo_origen = origen.get("kind") if isinstance(origen, dict) else None
		texto = _sin_recordatorios(_texto_plano(adjunto.get("prompt"))).strip()
		if not texto or adjunto.get("senderTaskId"):
			return
		if tipo_origen == "peer":
			if isinstance(origen, dict) and (origen.get("handback") or origen.get("senderTaskId")):
				return
			nombre = _una_linea(origen.get("name") if isinstance(origen, dict) else "") or "otra sesión"
			cuerpo = origen.get("body") if isinstance(origen, dict) else None
			cuerpo = cuerpo if isinstance(cuerpo, str) and cuerpo.strip() else texto
			self._anadir(ROL_SISTEMA, "Mensaje de %s:\n%s" % (nombre, _sin_recordatorios(cuerpo).strip()), registro)
			return
		if tipo_origen not in (None, "human") or _ETIQUETA_INICIAL.match(texto):
			return  # avisos de tareas y demás eventos del arnés
		self._en_cola.append(_normalizar(texto))
		del self._en_cola[:-20]
		self._anadir(ROL_USUARIO, texto, registro)

	def _anotar_uso(self, mensaje: dict) -> None:
		uso = mensaje.get("usage")
		if not isinstance(uso, dict):
			return
		total = 0
		for clave in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
			valor = uso.get(clave)
			if isinstance(valor, (int, float)) and not isinstance(valor, bool):
				total += int(valor)
		if total:
			self.uso_contexto = total

	# usuario -------------------------------------------------------------------

	def _usuario(self, registro: dict, contenido: Any) -> None:
		if registro.get("isCompactSummary"):
			texto = _sin_recordatorios(_texto_plano(contenido)).strip()
			self._anadir(ROL_SISTEMA, "Resumen de la conversación anterior:\n" + texto, registro)
			return
		if registro.get("isMeta"):
			texto = _sin_recordatorios(_texto_plano(contenido)).strip()
			if texto:
				self._anadir(ROL_SISTEMA, "Contexto: " + texto, registro)
			return
		if isinstance(contenido, str):
			self._texto_usuario(contenido, registro)
			return
		if not isinstance(contenido, list):
			self.errores += 1
			return
		textos: list[str] = []
		for bloque in contenido:
			if not isinstance(bloque, dict):
				continue
			tipo = bloque.get("type")
			if tipo == "text":
				textos.append(str(bloque.get("text") or ""))
			elif tipo == "image":
				textos.append("(imagen adjunta)")
			elif tipo == "document":
				textos.append("(documento adjunto)")
			elif tipo == "tool_result":
				self._resultado(bloque, registro)
		if textos:
			self._texto_usuario("\n".join(textos), registro)

	def _salida_orden(self, texto: str, registro: dict) -> bool:
		salida = _SALIDA_ORDEN.fullmatch(texto)
		if not salida:
			return False
		cuerpo = _limpiar_salida(_sin_recordatorios(salida.group(2)))
		if cuerpo:
			if salida.group(1) == "stderr":
				self._anadir(ROL_ERROR, "Error de comando:\n" + cuerpo, registro)
			else:
				self._anadir(ROL_SISTEMA, "Salida de comando:\n" + cuerpo, registro)
		return True

	def _texto_usuario(self, texto: str, registro: dict) -> None:
		if self._salida_orden(texto, registro):
			return
		limpio = _sin_recordatorios(texto).strip()
		if not limpio:
			return
		etiqueta = _ETIQUETA_INICIAL.match(limpio)
		if etiqueta:
			self._evento(etiqueta.group(1), limpio, registro)
			return
		if _INTERRUPCION.match(limpio):
			self._anadir(ROL_SISTEMA, "Interrumpido por el usuario", registro)
			return
		normal = _normalizar(limpio)
		if normal in self._en_cola:
			# Ya se mostró al llegar encolado.
			self._en_cola.remove(normal)
			return
		self._anadir(ROL_USUARIO, limpio, registro)

	def _evento(self, etiqueta: str, texto: str, registro: dict) -> None:
		"""Texto que escribe el arnés de Claude Code, no la persona."""
		if etiqueta in ("command-name", "command-message", "command-args"):
			nombre = _NOMBRE_ORDEN.search(texto)
			argumentos = _ARGS_ORDEN.search(texto)
			orden = (nombre.group(1).strip() if nombre else "").strip()
			if argumentos and argumentos.group(1).strip():
				orden += " " + argumentos.group(1).strip()
			if orden:
				self._anadir(ROL_USUARIO, "Orden: " + orden, registro)
			return
		cuerpo = _CUALQUIER_ETIQUETA.sub(" ", texto)
		cuerpo = re.sub(r"[ \t]+", " ", cuerpo).strip()
		if etiqueta == "bash-input":
			self._anadir(ROL_USUARIO, "Orden de terminal: " + cuerpo, registro)
		elif etiqueta in ("bash-stdout", "bash-stderr"):
			if cuerpo:
				self._anadir(ROL_SISTEMA, "Salida de comando:\n" + _limpiar_salida(cuerpo), registro)
		else:
			nombre = etiqueta.replace("-", " ")
			self._anadir(ROL_SISTEMA, "%s: %s" % (nombre, cuerpo) if cuerpo else nombre, registro)

	def _resultado(self, bloque: dict, registro: dict) -> None:
		ident = str(bloque.get("tool_use_id") or "")
		nombre, entrada, mensaje = self._herramientas.pop(ident, ("", {}, None))
		cuerpo = _sin_recordatorios(_texto_plano(bloque.get("content"))).strip()
		es_error = bool(bloque.get("is_error"))
		resultado = registro.get("toolUseResult")
		if nombre == "AskUserQuestion":
			self._anadir(ROL_USUARIO, _formatear_respuestas(resultado, cuerpo), registro, ident + ":respuesta")
			return
		if nombre == "ExitPlanMode":
			if es_error:
				self._anadir(ROL_SISTEMA, "Plan no aprobado: " + _primera_linea(cuerpo, 300), registro)
			else:
				self._anadir(ROL_SISTEMA, "Plan aprobado.", registro)
			return
		salida = _recortar(_limpiar_salida(cuerpo), MAX_DETALLE)
		if mensaje is None:
			# Resultado sin su llamada (transcript cortado): se muestra suelto.
			mensaje = self._anadir(
				ROL_HERRAMIENTA, "Resultado de herramienta", registro, ident, herramienta=nombre
			)
		else:
			self._modificados.append(mensaje)
		if es_error:
			mensaje.texto += " (permiso denegado)" if registro.get("toolDenialKind") else " (error)"
		titulo = "Error" if es_error else "Resultado"
		mensaje.detalle = ("%s\n\n%s:\n%s" % (mensaje.detalle, titulo, salida)).strip()
		if not es_error and nombre in _HERRAMIENTAS_DE_CAMBIO:
			mensaje.cambios.extend(_cambios(resultado, entrada))

	# asistente -----------------------------------------------------------------

	def _de_asistente(self, registro: dict, mensaje: dict, contenido: Any) -> None:
		ident = str(mensaje.get("id") or registro.get("uuid") or "")
		if registro.get("isApiErrorMessage"):
			self._asistente = None
			texto = _sin_recordatorios(_texto_plano(contenido)).strip()
			self._anadir(ROL_ERROR, texto or "Claude informó de un error.", registro)
			return
		if isinstance(contenido, str):
			bloques: list = [{"type": "text", "text": contenido}]
		elif isinstance(contenido, list):
			bloques = contenido
		else:
			self.errores += 1
			return
		for bloque in bloques:
			if not isinstance(bloque, dict):
				continue
			tipo = bloque.get("type")
			if tipo == "text":
				texto = _sin_recordatorios(str(bloque.get("text") or "")).strip()
				if not texto:
					continue
				ultimo = self._asistente
				if (
					ultimo is not None
					and ident
					and ultimo.id == ident
					and self.mensajes
					and self.mensajes[-1] is ultimo
				):
					ultimo.texto = "%s\n\n%s" % (ultimo.texto, texto)
					self._modificados.append(ultimo)
				else:
					self._asistente = self._anadir(ROL_ASISTENTE, texto, registro, ident)
			elif tipo == "tool_use":
				self._llamada(bloque, registro)
				self._asistente = None
			# thinking, redacted_thinking y bloques desconocidos: no se muestran

	def _llamada(self, bloque: dict, registro: dict) -> None:
		nombre = str(bloque.get("name") or "herramienta")
		ident = str(bloque.get("id") or "")
		entrada = bloque.get("input") if isinstance(bloque.get("input"), dict) else {}
		mensaje: Mensaje | None
		if nombre == "AskUserQuestion":
			mensaje = self._anadir(
				ROL_ASISTENTE, _formatear_preguntas(entrada), registro, ident, herramienta=nombre
			)
		elif nombre == "ExitPlanMode":
			plan = str(entrada.get("plan") or "").strip()
			mensaje = self._anadir(
				ROL_ASISTENTE, "Plan propuesto:\n" + (plan or "(sin texto)"), registro, ident, herramienta=nombre
			)
		else:
			mensaje = self._anadir(
				ROL_HERRAMIENTA,
				describir_herramienta(nombre, entrada),
				registro,
				ident,
				herramienta=nombre,
				detalle=_detalle_entrada(nombre, entrada),
			)
		if ident:
			self._herramientas[ident] = (nombre, entrada, mensaje)


# ---------------------------------------------------------------------------
# Lectura de archivos
# ---------------------------------------------------------------------------

_TROZO_LECTURA = 8 * 1024 * 1024
_TAM_ANCLA = 256


class LectorTranscript:
	"""Lee un transcript y lo sigue mientras crece.

	Solo se procesan líneas completas: una que aún se está escribiendo espera
	a la siguiente llamada. Si el archivo encoge o cambian los últimos bytes
	ya leídos (Claude Code lo reescribió), se empieza de cero y ``reiniciado``
	queda a ``True`` para que la interfaz rehaga la lista. Se abre siempre en
	modo lectura.
	"""

	def __init__(self, ruta: Path, laterales: bool = False) -> None:
		self.ruta = Path(ruta)
		self._laterales = laterales
		self.reiniciado = False
		#: Mensajes ya devueltos que la última ``leer_nuevo`` cambió (el
		#: resultado de una herramienta, un bloque más de una respuesta).
		self.modificados: list[Mensaje] = []
		self._reiniciar()

	def _reiniciar(self) -> None:
		self._posicion = 0
		self._ancla = b""
		self._analizador = _Analizador(self._laterales)

	@property
	def errores(self) -> int:
		"""Líneas que no se pudieron entender."""
		return self._analizador.errores

	@property
	def uso_contexto(self) -> int | None:
		"""Tokens de contexto según la última respuesta del asistente."""
		return self._analizador.uso_contexto

	@property
	def mensajes(self) -> list[Mensaje]:
		return list(self._analizador.mensajes)

	def _reescrito(self, archivo: Any, tamano: int) -> bool:
		if tamano < self._posicion:
			return True
		if not self._ancla:
			return False
		archivo.seek(self._posicion - len(self._ancla))
		return archivo.read(len(self._ancla)) != self._ancla

	def leer_nuevo(self) -> list[Mensaje]:
		"""Procesa solo lo añadido desde la última llamada; devuelve los mensajes nuevos."""
		self.reiniciado = False
		self.modificados = []
		nuevos: list[Mensaje] = []
		modificados: list[Mensaje] = []
		try:
			with open(self.ruta, "rb") as archivo:
				tamano = os.fstat(archivo.fileno()).st_size
				if self._reescrito(archivo, tamano):
					log.info("El transcript %s se reescribió; se vuelve a leer", self.ruta.name)
					self._reiniciar()
					self.reiniciado = True
				archivo.seek(self._posicion)
				resto = b""
				while self._posicion + len(resto) < tamano:
					datos = archivo.read(min(_TROZO_LECTURA, tamano - self._posicion - len(resto)))
					if not datos:
						break
					datos = resto + datos
					fin = datos.rfind(b"\n")
					if fin < 0:
						resto = datos
						continue
					completos, resto = datos[: fin + 1], datos[fin + 1 :]
					self._posicion += len(completos)
					self._ancla = (self._ancla + completos[-_TAM_ANCLA:])[-_TAM_ANCLA:]
					nuevos.extend(self._analizador.procesar(completos.split(b"\n")))
					modificados.extend(self._analizador.modificados())
		except OSError:
			log.debug("No se pudo leer el transcript %s", self.ruta, exc_info=True)
		ids_nuevos = {id(m) for m in nuevos}
		vistos: set[int] = set()
		for mensaje in modificados:
			if id(mensaje) not in ids_nuevos and id(mensaje) not in vistos:
				vistos.add(id(mensaje))
				self.modificados.append(mensaje)
		return nuevos

	def leer_todo(self) -> list[Mensaje]:
		"""Lee lo que falte y devuelve todos los mensajes de la sesión."""
		self.leer_nuevo()
		return self.mensajes


def leer_mensajes(ruta: Path) -> list[Mensaje]:
	"""Todos los mensajes de un transcript (lectura única)."""
	return LectorTranscript(ruta).leer_todo()


@dataclass(slots=True)
class TranscriptSubagente:
	"""Lo que hizo un subagente, leído de ``<sesión>/subagents/agent-*.jsonl``."""

	#: Id de la llamada (``Task`` o ``Agent``) que lo lanzó, si se conoce.
	id_herramienta: str
	tipo: str
	descripcion: str
	mensajes: list[Mensaje]
	fecha: float


#: Máximo de subagentes que se leen de una sesión (los más antiguos primero).
MAX_SUBAGENTES = 200


def leer_subagentes(ruta_transcript: Path) -> list[TranscriptSubagente]:
	"""Transcripts de los subagentes de una sesión, en el orden en que empezaron.

	Claude Code guarda cada uno junto al transcript principal, en
	``<id de sesión>/subagents/``, con un ``.meta.json`` que dice qué llamada
	lo lanzó. Solo lectura; si la carpeta no existe, devuelve una lista vacía.
	"""
	carpeta = Path(ruta_transcript).with_suffix("") / "subagents"
	try:
		archivos = sorted(carpeta.glob("agent-*.jsonl"))[:MAX_SUBAGENTES] if carpeta.is_dir() else []
	except OSError:
		log.debug("No se pudo recorrer %s", carpeta, exc_info=True)
		return []
	resultado: list[TranscriptSubagente] = []
	for archivo in archivos:
		meta = _leer_json(archivo.with_suffix(".meta.json")) or {}
		mensajes = LectorTranscript(archivo, laterales=True).leer_todo()
		if not mensajes:
			continue
		resultado.append(TranscriptSubagente(
			id_herramienta=str(meta.get("toolUseId") or ""),
			tipo=_una_linea(meta.get("agentType"), 100),
			descripcion=_una_linea(meta.get("description"), 300),
			mensajes=mensajes,
			fecha=mensajes[0].fecha,
		))
	resultado.sort(key=lambda subagente: subagente.fecha)
	return resultado


# ---------------------------------------------------------------------------
# Datos de un transcript para la lista (lectura parcial)
# ---------------------------------------------------------------------------

_TAM_CABEZA = 64 * 1024
_TAM_CABEZA_MAX = 1024 * 1024
_PASOS_COLA = (128 * 1024, 1024 * 1024)
_ENTRADA_TERMINAL = "cli"


@dataclass(slots=True)
class _DatosTranscript:
	entrada: str = ""  # entrypoint: cli, sdk-cli, claude-desktop…
	carpeta: str = ""
	titulo_propio: str = ""
	titulo_ia: str = ""
	primer_mensaje: str = ""
	creada: float = 0.0
	ultimo_mensaje: str = ""
	cola_leida: bool = False

	@property
	def titulo(self) -> str:
		return self.titulo_propio or self.titulo_ia or self.primer_mensaje


def _lineas_completas(datos: bytes, desde_inicio: bool, hasta_final: bool) -> list[bytes]:
	"""Las líneas enteras de un trozo: sin la primera si está cortada por
	delante ni la última si está cortada por detrás."""
	lineas = datos.split(b"\n")
	if not desde_inicio and lineas:
		lineas = lineas[1:]
	if not hasta_final and lineas:
		lineas = lineas[:-1]
	return lineas


def _leer_cabeza(archivo: Any, tamano: int, datos: _DatosTranscript) -> None:
	"""Del principio: quién la empezó, carpeta, fecha y primer mensaje."""
	leido = b""
	limite = _TAM_CABEZA
	while True:
		archivo.seek(0)
		leido = archivo.read(min(limite, tamano))
		if _datos_cabeza(leido, tamano, datos) or len(leido) >= tamano or limite >= _TAM_CABEZA_MAX:
			return
		limite = _TAM_CABEZA_MAX


def _datos_cabeza(leido: bytes, tamano: int, datos: _DatosTranscript) -> bool:
	"""Analiza el principio de un transcript; ``True`` si ya se sabe su entrada."""
	analizador = _Analizador()
	for linea in _lineas_completas(leido, True, len(leido) >= tamano):
		if not linea.strip():
			continue
		registro = _json_linea(linea)
		if registro is None:
			continue
		tipo = registro.get("type")
		if tipo == "custom-title":
			datos.titulo_propio = _una_linea(registro.get("customTitle"), 200) or datos.titulo_propio
			continue
		if tipo == "ai-title":
			datos.titulo_ia = _una_linea(registro.get("aiTitle"), 200) or datos.titulo_ia
			continue
		if registro.get("isSidechain"):
			continue
		if not datos.creada:
			datos.creada = _fecha(registro)
		if not datos.entrada and isinstance(registro.get("entrypoint"), str):
			datos.entrada = registro["entrypoint"]
		if not datos.carpeta and isinstance(registro.get("cwd"), str):
			datos.carpeta = registro["cwd"]
		if not datos.primer_mensaje and tipo in ("user", "attachment"):
			try:
				for mensaje in analizador.procesar([linea]):
					if mensaje.rol == ROL_USUARIO and mensaje.texto:
						datos.primer_mensaje = _primera_linea(mensaje.texto, 80)
						break
			except Exception:  # noqa: BLE001 - formato desconocido: sin primer mensaje
				log.debug("Primer mensaje ilegible", exc_info=True)
	return bool(datos.entrada)


def _leer_cola(archivo: Any, tamano: int, datos: _DatosTranscript) -> None:
	"""Del final: el título más reciente y el último mensaje del asistente."""
	for paso in _PASOS_COLA:
		inicio = max(0, tamano - paso)
		archivo.seek(inicio)
		leido = archivo.read(tamano - inicio)
		lineas = _lineas_completas(leido, inicio == 0, True)
		titulo_propio = titulo_ia = ultimo = ""
		for linea in lineas:
			if b'"custom-title"' in linea:
				registro = _json_linea(linea)
				if registro and registro.get("type") == "custom-title":
					titulo_propio = _una_linea(registro.get("customTitle"), 200) or titulo_propio
			elif b'"ai-title"' in linea:
				registro = _json_linea(linea)
				if registro and registro.get("type") == "ai-title":
					titulo_ia = _una_linea(registro.get("aiTitle"), 200) or titulo_ia
		for linea in reversed(lineas):
			if b'"assistant"' not in linea or b'"text"' not in linea:
				continue
			registro = _json_linea(linea)
			if not registro or registro.get("type") != "assistant" or registro.get("isSidechain"):
				continue
			if registro.get("isApiErrorMessage"):
				continue
			mensaje = registro.get("message")
			contenido = mensaje.get("content") if isinstance(mensaje, dict) else None
			bloques = [{"type": "text", "text": contenido}] if isinstance(contenido, str) else contenido
			if not isinstance(bloques, list):
				continue
			for bloque in reversed(bloques):
				if isinstance(bloque, dict) and bloque.get("type") == "text":
					ultimo = _primera_linea(_sin_recordatorios(str(bloque.get("text") or "")), 200)
					if ultimo:
						break
			if ultimo:
				break
		datos.titulo_propio = titulo_propio or datos.titulo_propio
		datos.titulo_ia = titulo_ia or datos.titulo_ia
		datos.ultimo_mensaje = ultimo or datos.ultimo_mensaje
		if inicio == 0 or ((titulo_propio or titulo_ia) and ultimo):
			break
	datos.cola_leida = True


class _ArchivoParcial:
	"""Un transcript del que solo se tienen el principio y el final (leídos en otro equipo).

	Imita lo que ``_leer_cabeza`` y ``_leer_cola`` usan de un archivo: una
	lectura desde 0 devuelve el principio; cualquier otra, la parte del final
	que se tiene. Si la lectura pedía más de lo que hay, la primera línea sale
	cortada y no se entiende como JSON: se ignora, como en un archivo real.
	"""

	def __init__(self, cabeza: bytes, cola: bytes, tamano: int) -> None:
		self._cabeza = cabeza
		self._cola = cola
		self._tamano = tamano
		self._posicion = 0

	def seek(self, posicion: int) -> None:
		self._posicion = posicion

	def read(self, cantidad: int) -> bytes:
		if self._tamano <= len(self._cabeza) or self._posicion == 0 and not self._cola:
			return self._cabeza[self._posicion:self._posicion + cantidad]
		if self._posicion == 0 and cantidad < self._tamano:
			return self._cabeza[:cantidad]
		inicio_cola = self._tamano - len(self._cola)
		desde = max(0, self._posicion - inicio_cola)
		return self._cola[desde:desde + cantidad]


def datos_de_trozos(cabeza: bytes, cola: bytes, tamano: int) -> _DatosTranscript:
	"""Datos para la lista de un transcript del que solo se tienen el principio y el final.

	Es lo que se trae de otro equipo por SSH: los mismos datos que se leen de
	un archivo de este equipo (quién la empezó, carpeta, título, último mensaje).
	"""
	datos = _DatosTranscript()
	archivo = _ArchivoParcial(cabeza, cola, max(tamano, len(cabeza)))
	_leer_cabeza(archivo, max(tamano, len(cabeza)), datos)
	_leer_cola(archivo, max(tamano, len(cabeza)), datos)
	return datos


_cache_datos: dict[str, tuple[tuple[int, int], _DatosTranscript]] = {}
_cerrojo_cache = threading.Lock()


def _datos_transcript(ruta: Path, con_cola: bool) -> tuple[_DatosTranscript | None, float]:
	"""Datos de un transcript y su mtime, recordados por tamaño y fecha.

	La cola (título reciente, último mensaje) solo se lee si ``con_cola``:
	casi ningún transcript que no se lista la necesita.
	"""
	clave = str(ruta)
	try:
		info = os.stat(ruta)
	except OSError:
		log.debug("No se pudo consultar %s", ruta, exc_info=True)
		return None, 0.0
	sello = (info.st_size, info.st_mtime_ns)
	with _cerrojo_cache:
		guardado = _cache_datos.get(clave)
	if guardado is not None and guardado[0] == sello and (guardado[1].cola_leida or not con_cola):
		return guardado[1], info.st_mtime
	datos = guardado[1] if guardado is not None and guardado[0] == sello else _DatosTranscript()
	try:
		with open(ruta, "rb") as archivo:
			if guardado is None or guardado[0] != sello:
				_leer_cabeza(archivo, info.st_size, datos)
			if con_cola:
				_leer_cola(archivo, info.st_size, datos)
	except OSError:
		log.debug("No se pudo leer %s", ruta, exc_info=True)
		return (guardado[1] if guardado is not None else None), info.st_mtime
	with _cerrojo_cache:
		_cache_datos[clave] = (sello, datos)
	return datos, info.st_mtime


def _olvidar_cache_salvo(claves: set[str]) -> None:
	with _cerrojo_cache:
		for clave in [c for c in _cache_datos if c not in claves]:
			del _cache_datos[clave]


# ---------------------------------------------------------------------------
# La lista de sesiones
# ---------------------------------------------------------------------------

#: postTurnSummary.status_category que significan «te espera a ti».
_CATEGORIAS_ESPERA = frozenset({"blocked", "review_ready", "needs_input", "waiting", "needs_action", "needs_you"})


def _archivos_recientes(carpetas: Iterable[Path], patron: str) -> list[Path]:
	"""Archivos que cumplen ``patron``; si uno está en dos carpetas (las dos
	instalaciones de la aplicación de escritorio), gana el de mtime más reciente."""
	elegidos: dict[str, tuple[float, Path]] = {}
	for carpeta in carpetas:
		try:
			encontrados = list(carpeta.glob(patron))
		except OSError:
			log.debug("No se pudo recorrer %s", carpeta, exc_info=True)
			continue
		for ruta in encontrados:
			try:
				mtime = ruta.stat().st_mtime
			except OSError:
				mtime = 0.0
			conocido = elegidos.get(ruta.name)
			if conocido is None or mtime > conocido[0]:
				elegidos[ruta.name] = (mtime, ruta)
	return [ruta for _, ruta in elegidos.values()]


def _resumen_actual(datos: dict, resumen: dict) -> bool:
	"""Un resumen de un turno anterior no dice nada del último."""
	ultimo = datos.get("lastAssistantUuid")
	resumido = resumen.get("summarizes_uuid") or datos.get("postTurnSummaryFor")
	if isinstance(ultimo, str) and ultimo and isinstance(resumido, str) and resumido:
		return ultimo == resumido
	return True


def _estado_escritorio(datos: dict, vivo: _EstadoVivo | None) -> str:
	estado = _estado_de_vivo(vivo)
	if estado in (ESTADO_TRABAJANDO, ESTADO_ESPERANDO):
		return estado
	resumen = datos.get("postTurnSummary")
	if isinstance(resumen, dict) and _resumen_actual(datos, resumen):
		if str(resumen.get("needs_action") or "").strip():
			return ESTADO_ESPERANDO
		if str(resumen.get("status_category") or "").strip().lower() in _CATEGORIAS_ESPERA:
			return ESTADO_ESPERANDO
	return estado or ESTADO_INACTIVA


def _titulo_por_defecto(carpeta: str) -> str:
	nombre = (carpeta or "").rstrip("\\/").replace("\\", "/").split("/")[-1]
	return "Sesión sin título en %s" % nombre if nombre else "Sesión sin título"


class _Recolector:
	"""Estado de una llamada a ``listar_sesiones``."""

	def __init__(self, ids_propios: Iterable[str], incluir_archivadas: bool) -> None:
		self.raiz = raiz_claude()
		self.proyectos = self.raiz / "projects"
		self.ids_propios = set(ids_propios)
		self.incluir_archivadas = incluir_archivadas
		self.vivos = _cargar_estado_vivo(self.raiz / "sessions")
		self.sesiones: list[SesionClaude] = []
		self.ids_vistos: set[str] = set()
		self.claves_cache: set[str] = set()
		self.indice = self._indice_transcripts()

	def _indice_transcripts(self) -> dict[str, Path]:
		"""``sessionId`` → transcript, de una sola pasada por ``projects``."""
		indice: dict[str, Path] = {}
		try:
			for ruta in self.proyectos.glob("*/*.jsonl"):
				indice.setdefault(ruta.stem, ruta)
		except OSError:
			log.debug("No se pudo recorrer %s", self.proyectos, exc_info=True)
		return indice

	def escritorio(self, ruta_meta: Path, cowork: bool) -> None:
		datos = _leer_json(ruta_meta)
		if datos is None:
			return
		id_local = datos.get("sessionId")
		if not es_id_seguro(id_local):
			return
		id_cli = datos.get("cliSessionId")
		id_cli = id_cli if es_id_seguro(id_cli) else ""
		if id_cli:
			self.ids_vistos.add(id_cli)
		archivada = bool(datos.get("isArchived"))
		if archivada and not self.incluir_archivadas:
			return
		carpeta = str(datos.get("cwd") or "")
		if cowork:
			carpetas = datos.get("userSelectedFolders")
			if isinstance(carpetas, list) and carpetas and isinstance(carpetas[0], str):
				carpeta = carpetas[0]
			casa = _ruta_larga(ruta_meta.with_suffix("") / ".claude")
			transcript = ruta_transcript(id_cli, str(datos.get("cwd") or ""), casa / "projects") if id_cli else None
			vivos = {} if archivada else _cargar_estado_vivo(casa / "sessions")
		else:
			transcript = self.indice.get(id_cli) if id_cli else None
			if id_cli and transcript is None:
				transcript = ruta_transcript(id_cli, carpeta, self.proyectos)
			vivos = {} if archivada else self.vivos
		info_transcript, mtime = (None, 0.0)
		if transcript is not None:
			self.claves_cache.add(str(transcript))
			info_transcript, mtime = _datos_transcript(transcript, con_cola=True)
		titulo = _una_linea(datos.get("title"))
		if not titulo and info_transcript is not None:
			titulo = info_transcript.titulo
		creada = _segundos(datos.get("createdAt")) or (info_transcript.creada if info_transcript else 0.0)
		actividad = max(_segundos(datos.get("lastActivityAt")), mtime) or creada
		if id_cli in self.ids_propios or id_local in self.ids_propios:
			origen = ORIGEN_CTRLIA
		else:
			origen = ORIGEN_COWORK if cowork else ORIGEN_ESCRITORIO
		self.sesiones.append(
			SesionClaude(
				clave="claude:%s" % (id_cli or id_local),
				id=id_cli,
				titulo=titulo or _titulo_por_defecto(carpeta),
				carpeta=carpeta,
				origen=origen,
				creada=creada,
				actividad=actividad,
				estado=_estado_escritorio(datos, vivos.get(id_cli) or vivos.get(id_local)),
				archivada=archivada,
				ruta_transcript=transcript,
				abierta=bool(vivos.get(id_cli) or vivos.get(id_local)),
				id_escritorio=id_local,
				ultimo_mensaje=info_transcript.ultimo_mensaje if info_transcript else "",
			)
		)

	def transcripts_sueltos(self) -> None:
		"""Transcripts sin dueño de escritorio: terminal, Ctrl+IA y otros."""
		for id_cli, ruta in self.indice.items():
			if id_cli in self.ids_vistos or not es_id_seguro(id_cli):
				continue
			self.claves_cache.add(str(ruta))
			propia = id_cli in self.ids_propios
			datos, _ = _datos_transcript(ruta, con_cola=False)
			if datos is None:
				continue
			if propia:
				origen = ORIGEN_CTRLIA
			elif datos.entrada == _ENTRADA_TERMINAL:
				origen = ORIGEN_TERMINAL
			elif datos.entrada.startswith("sdk") or datos.entrada == "claude-desktop":
				continue  # claude -p de otros programas, o de una sesión de escritorio borrada
			elif datos.primer_mensaje:
				origen = ORIGEN_OTRA  # extensiones de editor, versiones antiguas…
			else:
				continue
			datos, mtime = _datos_transcript(ruta, con_cola=True)
			if datos is None:
				continue
			self.ids_vistos.add(id_cli)
			estado = _estado_de_vivo(self.vivos.get(id_cli)) or ESTADO_INACTIVA
			self.sesiones.append(
				SesionClaude(
					clave="claude:%s" % id_cli,
					id=id_cli,
					titulo=datos.titulo or _titulo_por_defecto(datos.carpeta),
					carpeta=datos.carpeta,
					origen=origen,
					creada=datos.creada or mtime,
					actividad=mtime,
					estado=estado,
					archivada=False,
					ruta_transcript=ruta,
					ultimo_mensaje=datos.ultimo_mensaje,
					abierta=id_cli in self.vivos,
				)
			)


def listar_sesiones(ids_propios: set[str] = frozenset(), incluir_archivadas: bool = False) -> list[SesionClaude]:
	"""Todas las sesiones de Claude Code del equipo, la más reciente primero.

	``ids_propios`` son los ``sessionId`` creados por Ctrl+IA (origen
	``"ctrlia"``). Las archivadas en la aplicación de escritorio solo salen con
	``incluir_archivadas``. Solo lectura; nunca lanza por un archivo dañado.
	"""
	inicio = time.perf_counter()
	recolector = _Recolector(ids_propios, incluir_archivadas)
	for ruta in _archivos_recientes(carpetas_escritorio(), "**/local_*.json"):
		try:
			recolector.escritorio(ruta, cowork=False)
		except Exception:  # noqa: BLE001 - un archivo dañado no vacía la lista
			log.debug("Metadatos de escritorio ilegibles: %s", ruta, exc_info=True)
	# Cada carpeta de Cowork es una raíz de Claude Code entera: solo cuentan
	# los metadatos a la profundidad en que los escribe la aplicación.
	for ruta in _archivos_recientes(carpetas_cowork(), "*/*/local_*.json"):
		try:
			recolector.escritorio(ruta, cowork=True)
		except Exception:  # noqa: BLE001 - un archivo dañado no vacía la lista
			log.debug("Metadatos de Cowork ilegibles: %s", ruta, exc_info=True)
	try:
		recolector.transcripts_sueltos()
	except Exception:  # noqa: BLE001 - se listan al menos las de escritorio
		log.warning("No se pudieron listar los transcripts de %s", recolector.proyectos, exc_info=True)
	_olvidar_cache_salvo(recolector.claves_cache)
	sesiones = sorted(recolector.sesiones, key=lambda s: (-s.actividad, s.clave))
	log.debug("Listadas %d sesiones de Claude en %.3f s", len(sesiones), time.perf_counter() - inicio)
	return sesiones


def estado_vivo_de_datos(archivos: Iterable[dict]) -> dict[str, _EstadoVivo]:
	"""``sessionId`` → estado, de archivos ``sessions/<pid>.json`` cuyo proceso ya se sabe vivo.

	Para otro equipo: allí se comprueba con ``kill -0`` que el proceso sigue
	vivo y solo llegan los de procesos vivos.
	"""
	resultado: dict[str, _EstadoVivo] = {}
	for datos in archivos:
		if not isinstance(datos, dict):
			continue
		pid = datos.get("pid")
		estado = _EstadoVivo(estado=str(datos.get("status") or ""), pid=pid if isinstance(pid, int) else 0,
			esperando=" ".join(str(datos.get("waitingFor") or "").split()))
		for campo in ("sessionId", "hostSessionId"):
			valor = datos.get(campo)
			if isinstance(valor, str) and valor:
				resultado[valor] = estado
	return resultado


def sesiones_de_otro_equipo(
	equipo: str,
	transcripts: dict[str, tuple[_DatosTranscript, float]],
	escritorio: Iterable[dict],
	vivos: dict[str, _EstadoVivo],
	incluir_archivadas: bool = False,
) -> list[SesionClaude]:
	"""Las sesiones de Claude Code de otro equipo, con las mismas reglas que las de este.

	``transcripts`` va de la ruta POSIX del transcript a sus datos y su fecha;
	``escritorio``, los metadatos ``local_*.json`` de la aplicación de
	escritorio de ese equipo. Las sesiones no tienen ``ruta_transcript`` (el
	archivo está en el otro equipo): llevan ``equipo`` y ``ruta_remota``.
	"""
	indice: dict[str, str] = {}
	for ruta in sorted(transcripts):
		nombre = ruta.rsplit("/", 1)[-1]
		if nombre.endswith(".jsonl"):
			indice.setdefault(nombre[:-6], ruta)
	sesiones: list[SesionClaude] = []
	vistos: set[str] = set()
	for datos in escritorio:
		if not isinstance(datos, dict):
			continue
		id_local = datos.get("sessionId")
		if not es_id_seguro(id_local):
			continue
		id_cli = datos.get("cliSessionId")
		id_cli = id_cli if es_id_seguro(id_cli) else ""
		if id_cli:
			vistos.add(id_cli)
		archivada = bool(datos.get("isArchived"))
		if archivada and not incluir_archivadas:
			continue
		carpeta = str(datos.get("cwd") or "")
		ruta = indice.get(id_cli, "") if id_cli else ""
		info, mtime = transcripts.get(ruta, (None, 0.0)) if ruta else (None, 0.0)
		titulo = _una_linea(datos.get("title")) or (info.titulo if info is not None else "")
		creada = _segundos(datos.get("createdAt")) or (info.creada if info is not None else 0.0)
		sesiones.append(SesionClaude(
			clave="claude:%s" % (id_cli or id_local), id=id_cli, titulo=titulo or _titulo_por_defecto(carpeta),
			carpeta=carpeta, origen=ORIGEN_ESCRITORIO, creada=creada,
			actividad=max(_segundos(datos.get("lastActivityAt")), mtime) or creada,
			estado=_estado_escritorio(datos, None if archivada else vivos.get(id_cli) or vivos.get(id_local)),
			archivada=archivada, ruta_transcript=None, id_escritorio=id_local,
			ultimo_mensaje=info.ultimo_mensaje if info is not None else "", equipo=equipo, ruta_remota=ruta,
			abierta=not archivada and bool(vivos.get(id_cli) or vivos.get(id_local)),
		))
	for id_cli, ruta in indice.items():
		if id_cli in vistos or not es_id_seguro(id_cli):
			continue
		info, mtime = transcripts[ruta]
		if info.entrada == _ENTRADA_TERMINAL:
			origen = ORIGEN_TERMINAL
		elif info.entrada.startswith("sdk") or info.entrada == "claude-desktop":
			continue  # claude -p de otros programas (también de Ctrl+IA), o de una sesión de escritorio borrada
		elif info.primer_mensaje:
			origen = ORIGEN_OTRA
		else:
			continue
		sesiones.append(SesionClaude(
			clave="claude:%s" % id_cli, id=id_cli, titulo=info.titulo or _titulo_por_defecto(info.carpeta),
			carpeta=info.carpeta, origen=origen, creada=info.creada or mtime, actividad=mtime,
			estado=_estado_de_vivo(vivos.get(id_cli)) or ESTADO_INACTIVA, archivada=False, ruta_transcript=None,
			ultimo_mensaje=info.ultimo_mensaje, equipo=equipo, ruta_remota=ruta, abierta=id_cli in vivos,
		))
	return sorted(sesiones, key=lambda s: (-s.actividad, s.clave))


# ---------------------------------------------------------------------------
# Búsqueda y enlaces
# ---------------------------------------------------------------------------

def _tabla_pliegue() -> bytes:
	"""Tabla de ``bytes.translate`` que pasa el UTF-8 crudo a minúsculas ASCII
	sin tildes: A-Z → a-z y, borrando antes el byte inicial ``\\xc3`` del
	bloque Latin-1 (À…ÿ), su segundo byte → la letra base (``ó`` → ``o``).

	La tabla no distingue contexto, así que otros caracteres no ASCII pueden
	convertirse en letras sueltas: solo da falsos positivos, que el análisis
	completo descarta; nunca oculta un acierto.
	"""
	tabla = bytearray(range(256))
	for byte in range(ord("A"), ord("Z") + 1):
		tabla[byte] = byte + 32
	for byte in range(0x80, 0xC0):
		base = _normalizar(chr(0xC0 + byte - 0x80))
		if len(base) == 1 and base.isascii() and base.isalpha():
			tabla[byte] = ord(base)
	return bytes(tabla)


_PLIEGUE = _tabla_pliegue()
_MARCAS_COMBINANTES = re.compile(b"[\xcc\xcd][\x80-\xbf]")

#: Textos que pone este módulo y no están en el archivo: una palabra que
#: aparezca aquí no puede descartar un transcript por no estar en sus bytes.
_VOCABULARIO = _normalizar(
	" ".join(
		[verbo for verbo, _ in _HERRAMIENTAS.values()]
		+ [
			"Herramienta Resultado de herramienta Resultado Error (error) (permiso denegado)",
			"Contexto: Resumen de la conversación anterior Conversación compactada para liberar contexto.",
			"Salida de comando Error de comando Orden: Orden de terminal Interrumpido por el usuario",
			"Claude pregunta Opciones Respuesta (respondida) Plan propuesto (sin texto) Plan aprobado.",
			"Plan no aprobado Mensaje de otra sesión Claude informó de un error. Descripción Archivo Tareas",
			"Entrada [en curso] [x] (imagen adjunta) (documento adjunto) [herramienta [… recortado]",
		]
	)
)


def _palabra_filtrable(palabra: str) -> bytes | None:
	"""``palabra`` (ya normalizada) en bytes para el filtro previo, o ``None``
	si no sirve para descartar archivos: la pone el propio lector, no es
	ASCII (ß → ss, otros alfabetos) o JSON la escaparía (comillas, barras)."""
	if palabra in _VOCABULARIO or "ss" in palabra or not palabra.isascii():
		return None
	if any(c in palabra for c in '"\\') or any(ord(c) < 32 for c in palabra):
		return None
	return palabra.encode("ascii")


def _puede_contener(ruta: Path, palabras: list[bytes]) -> bool:
	"""Filtro previo barato: si alguna palabra no está en los bytes del
	archivo (sin mayúsculas ni tildes), ningún mensaje suyo puede contenerla
	y no hace falta analizarlo."""
	try:
		with open(ruta, "rb") as archivo:
			datos = archivo.read()
	except OSError:
		log.debug("No se pudo leer %s para buscar", ruta, exc_info=True)
		return False
	if b"\xcc" in datos or b"\xcd" in datos:
		# Tildes como marcas combinantes (U+0300…U+036F, «e» + «´»): se quitan.
		datos = _MARCAS_COMBINANTES.sub(b"", datos)
	plegado = datos.translate(_PLIEGUE, b"\xc3")
	return all(palabra in plegado for palabra in palabras)


def buscar_en_sesiones(
	sesiones: list[SesionClaude],
	texto: str,
	maximo: int = 500,
	cancelado: Callable[[], bool] | None = None,
) -> list[tuple[SesionClaude, Mensaje]]:
	"""Mensajes que contienen todas las palabras de ``texto``, sin distinguir
	mayúsculas ni acentos. Para en ``maximo`` resultados o cuando ``cancelado()``
	devuelve ``True`` (se consulta entre sesiones y cada pocos mensajes)."""
	palabras = _normalizar(texto).split()
	resultados: list[tuple[SesionClaude, Mensaje]] = []
	if not palabras or maximo <= 0:
		return resultados
	filtrables = [p for p in (_palabra_filtrable(palabra) for palabra in palabras) if p is not None]
	for sesion in sesiones:
		if cancelado is not None and cancelado():
			break
		if sesion.ruta_transcript is None:
			continue
		if filtrables and not _puede_contener(sesion.ruta_transcript, filtrables):
			continue
		for numero, mensaje in enumerate(leer_mensajes(sesion.ruta_transcript)):
			if numero % 200 == 0 and cancelado is not None and cancelado():
				return resultados
			normal = _normalizar(mensaje.texto)
			if all(palabra in normal for palabra in palabras):
				resultados.append((sesion, mensaje))
				if len(resultados) >= maximo:
					return resultados
	return resultados


#: Identificador de una sesión de Claude Code (``--session-id``): un UUID.
_RE_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def url_importar_en_claude(id_cli: str) -> str | None:
	"""Enlace que hace que la app de escritorio de Claude importe una sesión de Claude Code y la abra.

	Es el «resume» oficial de la app (comprobado con Claude 3.1623): importa la
	conversación de ``~/.claude/projects`` como sesión suya, ``local_<id>``, y la
	abre. Si ya la tenía importada, solo la abre. Sirve para las sesiones que
	crea Ctrl+IA, que son de Claude Code como las de la terminal.
	"""
	if not isinstance(id_cli, str) or not _RE_UUID.match(id_cli):
		return None
	return "claude://resume?session=%s" % id_cli


def url_abrir_en_claude(sesion: SesionClaude) -> str | None:
	"""Enlace ``claude://`` que abre la sesión en la aplicación de escritorio,
	o ``None`` si no es de la aplicación de escritorio."""
	if sesion.equipo or not sesion.id_escritorio or not es_id_seguro(sesion.id_escritorio):
		return None
	pagina = "cowork" if sesion.origen == ORIGEN_COWORK else "epitaxy"
	return "claude://claude.ai/%s/%s" % (pagina, sesion.id_escritorio)
