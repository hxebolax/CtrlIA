"""Antigravity de Google: la CLI ``agy``, el IDE y la app de escritorio.

Conversación
	Se habla con la Antigravity CLI (``agy``), que en junio de 2026 sustituyó
	a Gemini CLI para los usuarios con suscripción. Se lanza en modo sin
	interfaz con entrada y salida ``stream-json`` (una línea JSON por mensaje)
	y la carpeta del proyecto como carpeta de trabajo::

		agy --input-format stream-json --output-format stream-json
			[--model <slug>] [--effort low|medium|high] [--conversation <id>]
			[--sandbox] [--dangerously-skip-permissions]

	Cada mensaje del usuario produce un evento ``result``. ``agy`` no tiene
	``control_request`` (responde con un error y sale con código 2) ni admite
	comandos de barra en este modo, así que nunca se envían. Para cambiar el
	modelo, el esfuerzo o el modo de permisos se cierra la entrada y se
	relanza con ``--conversation <id>`` antes del siguiente envío.

Permisos
	En modo no interactivo ``agy`` NO pregunta: deniega en silencio lo que no
	esté permitido (sale con 0, lo cuenta en ``denied_actions`` y en el error
	estándar). Por eso aquí no hay peticiones de permiso: cada denegación se
	convierte en un ``AVISO`` con lo necesario para que la interfaz ofrezca
	«Permitir y reintentar», que añade una regla a
	``~/.gemini/antigravity-cli/settings.json`` y vuelve a pedir el trabajo.

IDE y app de escritorio
	Utilidades para abrir una carpeta en el IDE (derivado de VS Code) o en la
	app de escritorio Antigravity 2.0 y para listar las conversaciones de la
	app (solo metadatos: los ``.pb`` y los blobs de los ``.db`` son protobuf
	y no se decodifican).

	Nota: el servidor de la app (``resources\\bin\\language_server.exe``)
	tiene un subcomando oculto ``agentapi`` (``new-conversation``,
	``send-message``, ``get-conversation-metadata``), pero solo funciona
	dentro de Antigravity (necesita ``ANTIGRAVITY_LS_ADDRESS`` y un token
	CSRF), así que NO se usa.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services.agentes import remoto
from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	Capacidades,
	ClaseOpcion,
	Conversacion,
	Estado,
	EstadoProveedor,
	OpcionLista,
	Proveedor,
	ReceptorEventos,
	RespuestaPermiso,
	TipoEvento,
)
from app.services.agentes.procesos import (
	CREATE_NEW_PROCESS_GROUP,
	CREATE_NO_WINDOW,
	ES_WINDOWS,
	ProcesoLineas,
	buscar_ejecutable,
	ejecutar_y_leer,
	entorno_hijo,
	json_o_nada,
	version_de,
)

log = logging.getLogger(__name__)

#: Variables que no hereda ``agy``: con ellas cobraría por uso de la API en
#: lugar de usar la suscripción del usuario.
VARIABLES_QUITADAS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")

URL_INSTALADOR = "https://antigravity.google/cli/install.ps1"

#: Longitud máxima de la salida de una herramienta que se pasa a la interfaz.
MAX_SALIDA_HERRAMIENTA = 4000

MODO_REVISAR = "revisar"
MODO_REGLAS = "reglas"
MODO_SIN_PREGUNTAS = "sin_preguntas"
MODO_SANDBOX = "sandbox"

_ESFUERZOS_VALIDOS = ("low", "medium", "high")
_ESTADOS_INTERRUMPIDO = ("CANCELED", "CANCELLED", "INTERRUPTED")
_RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_RE_DENEGACION = re.compile(
	r"\b(denied|deny|denegad[ao]s?|not permitted|not allowed|permission|permiso|blocked)\b", re.IGNORECASE
)
_RE_REGLA = re.compile(r"\b([a-z_]+\([^()\n]*\))", re.IGNORECASE)
#: Error de un paso denegado: «permission check failed for mcp "prueba/hora": user denied permission
#: for mcp(prueba/hora)». Es lo único que dice la regla de una herramienta MCP: ``denied_actions`` solo
#: trae ``{"action": "mcp", "display_name": "CallMcpTool"}``.
_RE_REGLA_DENEGADA = re.compile(r"denied permission for ([a-z_]+\([^()\n]*\))", re.IGNORECASE)
#: Error de ``agy`` con un modelo que no conoce: «invalid model selection (...): model flash is not recognized...».
_RE_MODELO_DESCONOCIDO = re.compile(r"invalid model selection|is not recognized as a known model", re.IGNORECASE)
_RE_PREFIJO_TITULO = re.compile(
	r"^(walkthrough|implementation plan|plan de implementaci[oó]n|tareas|tasks?|plan)\s*[-—–:]\s*", re.IGNORECASE
)


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------


def _programas() -> Path | None:
	"""Carpeta ``%LOCALAPPDATA%\\Programs`` (donde se instalan el IDE y la app)."""
	local = os.environ.get("LOCALAPPDATA")  # auditoria: ignorar ruta-no-portable - localiza Antigravity instalado; no guarda datos
	return Path(local) / "Programs" if local else None


def candidatos_agy() -> list[Path]:
	"""Sitios donde suele instalarse ``agy`` además del PATH."""
	candidatos = []
	local = os.environ.get("LOCALAPPDATA")  # auditoria: ignorar ruta-no-portable - localiza Antigravity instalado; no guarda datos
	if local:
		candidatos.append(Path(local) / "agy" / "bin" / "agy.exe")
	candidatos.append(Path.home() / ".local" / "bin" / "agy.exe")
	candidatos.append(Path.home() / ".local" / "bin" / "agy")
	return candidatos


def buscar_agy() -> Path | None:
	"""Ruta del ejecutable nativo de la Antigravity CLI, o ``None``."""
	return buscar_ejecutable(["agy"], candidatos_agy())


#: Primera versión de agy con ``/usage`` en modo JSON (antes, «/usage» se enviaba como mensaje).
VERSION_MINIMA_USO = (1, 1, 11)
#: Segundos que se espera a ``agy -p /usage``.
TIEMPO_USO = 75.0


def _version_numerica(texto: str) -> tuple[int, ...] | None:
	"""«1.4.2» o «agy 1.4.2 (abc)» → (1, 4, 2)."""
	coincidencia = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", texto or "")
	if not coincidencia:
		return None
	return tuple(int(parte or 0) for parte in coincidencia.groups())


def ruta_ajustes_cli() -> Path:
	"""``~/.gemini/antigravity-cli/settings.json`` (reglas de permisos de ``agy``)."""
	return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def instalar_cli_orden() -> list[str]:
	"""Orden que instala ``agy`` con el instalador oficial en una consola nueva."""
	return [
		"powershell.exe",
		"-NoProfile",
		"-ExecutionPolicy",
		"Bypass",
		"-Command",
		f"irm {URL_INSTALADOR} | iex; Read-Host 'Pulsa Intro para cerrar'",
	]


# --------------------------------------------------------------------------
# IDE y app de escritorio
# --------------------------------------------------------------------------


def _cli_js_de(exe: Path) -> Path:
	return exe.parent / "resources" / "app" / "out" / "cli.js"


def buscar_antigravity_ide() -> Path | None:
	"""Ejecutable del IDE Antigravity (derivado de VS Code), o ``None``.

	Se reconoce porque junto al ``.exe`` está ``resources\\app\\out\\cli.js``;
	así no se confunde la versión antigua del IDE (``Programs\\Antigravity``)
	con la app de escritorio 2.0, que vive en la misma carpeta.
	"""
	programas = _programas()
	if programas is None:
		return None
	for exe in (
		programas / "Antigravity IDE" / "Antigravity IDE.exe",
		programas / "Antigravity" / "Antigravity.exe",
	):
		try:
			if exe.is_file() and _cli_js_de(exe).is_file():
				return exe
		except OSError:
			log.debug("No se pudo comprobar %s", exe, exc_info=True)
	return None


def buscar_antigravity_app() -> Path | None:
	"""Ejecutable de la app de escritorio Antigravity 2.0 (Electron), o ``None``."""
	programas = _programas()
	if programas is None:
		return None
	exe = programas / "antigravity" / "Antigravity.exe"
	try:
		if exe.is_file() and not _cli_js_de(exe).is_file():
			return exe
	except OSError:
		log.debug("No se pudo comprobar %s", exe, exc_info=True)
	return None


def orden_abrir_carpeta(carpeta: Path, preferir: str = "ide", nueva_ventana: bool = False) -> tuple[list[str], dict[str, str] | None] | None:
	"""Orden (y entorno) para abrir ``carpeta`` en Antigravity, sin lanzarla.

	- IDE: ``[exe, cli.js, --new-window|--reuse-window, carpeta]`` con
	  ``ELECTRON_RUN_AS_NODE=1``. Es lo mismo que hace ``antigravity-ide.cmd``
	  pero sin pasar por ``cmd.exe``, que reinterpretaría caracteres de la ruta.
	- App: ``[Antigravity.exe, carpeta]`` (la app elige el espacio de trabajo
	  en su propia ventana si no entiende el argumento).

	Si el preferido no está instalado se usa el otro. ``None`` si no hay ninguno.
	"""
	carpeta = Path(carpeta)

	def del_ide() -> tuple[list[str], dict[str, str]] | None:
		exe = buscar_antigravity_ide()
		if exe is None:
			return None
		orden = [str(exe), str(_cli_js_de(exe)), "--new-window" if nueva_ventana else "--reuse-window", str(carpeta)]
		return orden, entorno_hijo(quitar=["VSCODE_DEV"], anadir={"ELECTRON_RUN_AS_NODE": "1"})

	def de_la_app() -> tuple[list[str], None] | None:
		exe = buscar_antigravity_app()
		return ([str(exe), str(carpeta)], None) if exe is not None else None

	orden_busqueda = (de_la_app, del_ide) if preferir == "app" else (del_ide, de_la_app)
	for buscar in orden_busqueda:
		resultado = buscar()
		if resultado is not None:
			return resultado
	return None


def _lanzar(orden: Sequence[str], carpeta: Path | None, entorno: dict[str, str] | None) -> bool:
	"""Como ``procesos.lanzar_independiente`` pero con entorno propio.

	``lanzar_independiente`` no admite ``env`` y el IDE necesita
	``ELECTRON_RUN_AS_NODE=1`` para que su ``.exe`` ejecute ``cli.js``.
	"""
	try:
		subprocess.Popen(  # noqa: S603 - orden construida por la aplicación, sin shell
			list(orden),
			cwd=str(carpeta) if carpeta else None,
			env=entorno,
			creationflags=(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if ES_WINDOWS else 0,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			close_fds=True,
		)
	except OSError:
		log.exception("No se pudo lanzar %s", Path(orden[0]).name if orden else "")
		return False
	return True


def abrir_carpeta_en_antigravity(carpeta: Path, preferir: str = "ide", nueva_ventana: bool = False) -> bool:
	"""Abre ``carpeta`` en el IDE o en la app de Antigravity. ``False`` si no se pudo."""
	resultado = orden_abrir_carpeta(carpeta, preferir, nueva_ventana)
	if resultado is None:
		log.info("Antigravity no está instalado: no se puede abrir la carpeta")
		return False
	orden, entorno = resultado
	carpeta = Path(carpeta)
	return _lanzar(orden, carpeta if carpeta.is_dir() else None, entorno)


# --------------------------------------------------------------------------
# Conversaciones de la app de escritorio (solo metadatos)
# --------------------------------------------------------------------------


def _primer_titulo(archivo: Path, admitir_linea: bool = False) -> str:
	"""Primer encabezado Markdown de ``archivo`` (lee solo los primeros 4 KB)."""
	try:
		with archivo.open("rb") as entrada:
			texto = entrada.read(4096).decode("utf-8", "replace")
	except OSError:
		return ""
	primera = ""
	for linea in texto.splitlines():
		linea = linea.strip()
		if not linea:
			continue
		if linea.startswith("#"):
			titulo = _RE_PREFIJO_TITULO.sub("", linea.lstrip("#").strip())
			return titulo[:120].strip()
		if not primera:
			primera = re.sub(r"^[-*]\s*(\[[ xX/]\]\s*)?", "", linea).replace("`", "")
	return primera[:120].strip() if admitir_linea else ""


def _titulo_de_brain(carpeta: Path) -> str:
	"""Título de una conversación a partir de sus artefactos Markdown."""
	if not carpeta.is_dir():
		return ""
	sitios = [carpeta, carpeta / "artifacts", carpeta / ".artifacts"]
	preferidos = ("implementation_plan.md", "walkthrough.md")
	for sitio in sitios:
		for nombre in preferidos:
			titulo = _primer_titulo(sitio / nombre)
			if titulo:
				return titulo
	for sitio in sitios:
		try:
			otros = sorted(p for p in sitio.glob("*.md") if p.name not in preferidos and p.name != "task.md")
		except OSError:
			log.debug("No se pudo recorrer %s", sitio, exc_info=True)
			continue
		for archivo in otros:
			titulo = _primer_titulo(archivo)
			if titulo:
				return titulo
	for sitio in sitios:
		titulo = _primer_titulo(sitio / "task.md", admitir_linea=True)
		if titulo:
			return titulo
	return ""


def _mtime(ruta: Path) -> float:
	try:
		return ruta.stat().st_mtime
	except OSError:
		return 0.0


def listar_conversaciones_escritorio(raiz: Path | None = None) -> list[dict[str, Any]]:
	"""Conversaciones de la app de escritorio, de la más reciente a la más antigua.

	Solo lee metadatos de ``~/.gemini/antigravity`` (``conversations`` y
	``brain``): nunca decodifica los ``.pb`` ni los blobs de los ``.db``.
	Cada elemento: ``id``, ``fecha`` (``datetime`` local de la última
	modificación), ``titulo`` y ``ruta_brain`` (``""`` si no hay artefactos).
	"""
	raiz = Path(raiz) if raiz is not None else Path.home() / ".gemini" / "antigravity"
	fechas: dict[str, float] = {}
	carpeta_conversaciones = raiz / "conversations"
	carpeta_brain = raiz / "brain"
	try:
		archivos = list(carpeta_conversaciones.iterdir()) if carpeta_conversaciones.is_dir() else []
	except OSError:
		log.debug("No se pudo leer %s", carpeta_conversaciones, exc_info=True)
		archivos = []
	for archivo in archivos:
		if archivo.suffix.lower() not in (".pb", ".db") or not _RE_UUID.match(archivo.stem):
			continue
		identificador = archivo.stem.lower()
		fechas[identificador] = max(fechas.get(identificador, 0.0), _mtime(archivo))
	try:
		carpetas = list(carpeta_brain.iterdir()) if carpeta_brain.is_dir() else []
	except OSError:
		log.debug("No se pudo leer %s", carpeta_brain, exc_info=True)
		carpetas = []
	brains: dict[str, Path] = {}
	for carpeta in carpetas:
		if not _RE_UUID.match(carpeta.name) or not carpeta.is_dir():
			continue
		identificador = carpeta.name.lower()
		brains[identificador] = carpeta
		fechas[identificador] = max(fechas.get(identificador, 0.0), _mtime(carpeta))
	resultado = []
	for identificador, marca in fechas.items():
		fecha = _dt.datetime.fromtimestamp(marca) if marca else _dt.datetime.fromtimestamp(0)
		brain = brains.get(identificador)
		titulo = _titulo_de_brain(brain) if brain is not None else ""
		if not titulo:
			titulo = "Conversación de Antigravity del " + fecha.strftime("%d/%m/%Y %H:%M")
		resultado.append({
			"id": identificador,
			"fecha": fecha,
			"titulo": titulo,
			"ruta_brain": str(brain) if brain is not None else "",
		})
	resultado.sort(key=lambda elemento: elemento["fecha"], reverse=True)
	return resultado


# --------------------------------------------------------------------------
# Reglas de permisos (settings.json de agy)
# --------------------------------------------------------------------------


class ErrorAjustesAntigravity(Exception):
	"""El archivo de ajustes de Antigravity no se puede leer o escribir con seguridad."""


_candado_ajustes = threading.Lock()


def _leer_ajustes(ruta: Path) -> dict[str, Any]:
	"""Contenido de ``settings.json``; ``{}`` si no existe. Lanza si no se entiende."""
	try:
		texto = ruta.read_text(encoding="utf-8-sig")
	except FileNotFoundError:
		return {}
	except OSError as exc:
		raise ErrorAjustesAntigravity(f"No se pudo leer el archivo de ajustes de Antigravity ({ruta}): {exc}") from exc
	if not texto.strip():
		return {}
	try:
		datos = json.loads(texto)
	except ValueError as exc:
		raise ErrorAjustesAntigravity(
			f"El archivo de ajustes de Antigravity ({ruta}) está dañado o no es JSON válido. "
			"No lo toco para no perder lo que contiene; corrígelo o bórralo y vuelve a intentarlo."
		) from exc
	if not isinstance(datos, dict):
		raise ErrorAjustesAntigravity(
			f"El archivo de ajustes de Antigravity ({ruta}) no tiene el formato esperado. No lo toco."
		)
	permisos = datos.get("permissions")
	if permisos is not None and not isinstance(permisos, dict):
		raise ErrorAjustesAntigravity(
			f"La sección de permisos del archivo de ajustes de Antigravity ({ruta}) no tiene el formato esperado. No la toco."
		)
	if isinstance(permisos, dict):
		permitidas = permisos.get("allow")
		if permitidas is not None and not (isinstance(permitidas, list) and all(isinstance(r, str) for r in permitidas)):
			raise ErrorAjustesAntigravity(
				f"La lista de reglas permitidas de Antigravity ({ruta}) no tiene el formato esperado. No la toco."
			)
	return datos


def _escribir_ajustes(ruta: Path, datos: dict[str, Any]) -> None:
	"""Escribe ``datos`` de forma atómica (temporal en la misma carpeta + ``os.replace``)."""
	try:
		ruta.parent.mkdir(parents=True, exist_ok=True)
		descriptor, temporal = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=str(ruta.parent))
	except OSError as exc:
		raise ErrorAjustesAntigravity(f"No se pudo guardar el archivo de ajustes de Antigravity ({ruta}): {exc}") from exc
	try:
		with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as salida:
			json.dump(datos, salida, ensure_ascii=False, indent=2)
			salida.write("\n")
			salida.flush()
			os.fsync(salida.fileno())
		os.replace(temporal, ruta)
	except OSError as exc:
		try:
			os.unlink(temporal)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
		raise ErrorAjustesAntigravity(f"No se pudo guardar el archivo de ajustes de Antigravity ({ruta}): {exc}") from exc


def _validar_regla(regla: str) -> str:
	regla = (regla or "").strip()
	if not regla or "\n" in regla or "\r" in regla:
		raise ErrorAjustesAntigravity("La regla de permiso está vacía o no es válida.")
	return regla


def leer_reglas(ruta: Path | None = None) -> list[str]:
	"""Reglas permitidas (``permissions.allow``) de la Antigravity CLI."""
	ruta = Path(ruta) if ruta is not None else ruta_ajustes_cli()
	with _candado_ajustes:
		datos = _leer_ajustes(ruta)
	return list((datos.get("permissions") or {}).get("allow") or [])


RUTA_AJUSTES_REMOTA = "~/.gemini/antigravity-cli/settings.json"


def anadir_regla_remota(equipo: remoto.EquipoSSH, regla: str) -> bool:
	"""Añade una regla permitida en el settings.json de ``agy`` del otro equipo (por SSH).

	Se lee el archivo, se cambia con las mismas funciones que en local (conservan el
	resto del archivo) y se escribe de forma atómica. Lanza ``ErrorRemoto`` o
	:class:`ErrorAjustesAntigravity`.
	"""
	import tempfile

	from app.core.paths import ruta_temporales

	contenido = remoto.leer_archivo(equipo, RUTA_AJUSTES_REMOTA)
	with tempfile.TemporaryDirectory(prefix="ctrlia-agy-", dir=ruta_temporales()) as carpeta:
		ruta = Path(carpeta) / "settings.json"
		if contenido is not None:
			ruta.write_text(contenido, encoding="utf-8")
		nueva = anadir_regla(regla, ruta)
		if nueva:
			remoto.escribir_archivo(equipo, RUTA_AJUSTES_REMOTA, ruta.read_text(encoding="utf-8"))
	return nueva


def anadir_regla(regla: str, ruta: Path | None = None) -> bool:
	"""Añade una regla permitida. ``False`` si ya estaba. Conserva el resto del archivo."""
	regla = _validar_regla(regla)
	ruta = Path(ruta) if ruta is not None else ruta_ajustes_cli()
	with _candado_ajustes:
		datos = _leer_ajustes(ruta)
		permisos = datos.setdefault("permissions", {})
		permitidas = permisos.setdefault("allow", [])
		if regla in permitidas:
			return False
		permitidas.append(regla)
		_escribir_ajustes(ruta, datos)
	log.info("Regla de Antigravity añadida: %s", regla)
	return True


def quitar_regla(regla: str, ruta: Path | None = None) -> bool:
	"""Quita una regla permitida. ``False`` si no estaba."""
	regla = _validar_regla(regla)
	ruta = Path(ruta) if ruta is not None else ruta_ajustes_cli()
	with _candado_ajustes:
		datos = _leer_ajustes(ruta)
		permitidas = (datos.get("permissions") or {}).get("allow") or []
		if regla not in permitidas:
			return False
		datos["permissions"]["allow"] = [r for r in permitidas if r != regla]
		_escribir_ajustes(ruta, datos)
	log.info("Regla de Antigravity quitada: %s", regla)
	return True


# --------------------------------------------------------------------------
# Descripción legible de herramientas y denegaciones
# --------------------------------------------------------------------------


def _texto_corto(valor: Any, maximo: int = 200) -> str:
	if valor is None:
		return ""
	texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
	texto = " ".join(texto.split())
	return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def _parametro(parametros: dict[str, Any], *claves: str) -> str:
	for clave in claves:
		valor = parametros.get(clave)
		if isinstance(valor, str) and valor.strip():
			return valor.strip()
	return ""


def describir_herramienta(nombre: str, parametros: Any) -> str:
	"""Frase en español de lo que hace una herramienta (para el lector de pantalla)."""
	nombre = nombre or "herramienta"
	parametros = parametros if isinstance(parametros, dict) else {}
	clave = nombre.lower()
	orden = _parametro(parametros, "command", "CommandLine", "cmd", "command_line")
	ruta = _parametro(parametros, "path", "file_path", "AbsolutePath", "TargetFile", "filename", "directory", "DirectoryPath")
	consulta = _parametro(parametros, "query", "pattern", "Query", "search", "url", "Url")
	if "mcp" in clave and _parametro(parametros, "ToolName", "tool_name"):
		servidor = _parametro(parametros, "ServerName", "server_name")
		herramienta = _parametro(parametros, "ToolName", "tool_name")
		return f"Usa la herramienta «{herramienta}» del servidor MCP «{servidor}»" if servidor \
			else f"Usa la herramienta MCP «{herramienta}»"
	if orden or any(p in clave for p in ("command", "shell", "terminal", "bash")):
		return f"Ejecuta la orden «{_texto_corto(orden, 300)}»" if orden else "Ejecuta una orden"
	if any(p in clave for p in ("write", "edit", "replace", "create_file", "patch")):
		return f"Modifica «{ruta}»" if ruta else "Modifica un archivo"
	if any(p in clave for p in ("read", "view_file", "open_file")):
		return f"Lee «{ruta}»" if ruta else "Lee un archivo"
	if any(p in clave for p in ("list_dir", "list_directory", "ls")):
		return f"Mira la carpeta «{ruta}»" if ruta else "Mira una carpeta"
	if any(p in clave for p in ("search", "grep", "find", "glob")):
		return f"Busca «{_texto_corto(consulta or ruta, 200)}»" if (consulta or ruta) else "Busca en el proyecto"
	if any(p in clave for p in ("fetch", "browser", "web")):
		return f"Consulta «{_texto_corto(consulta, 200)}»" if consulta else "Consulta la web"
	extras = ", ".join(
		f"{k}: {_texto_corto(v, 80)}" for k, v in list(parametros.items())[:3] if isinstance(v, (str, int, float, bool))
	)
	return f"Usa {nombre}" + (f" ({extras})" if extras else "")


def describir_denegacion(accion: Any) -> tuple[str, str]:
	"""``(frase, regla_sugerida)`` para una acción denegada por ``agy``.

	``denied_actions`` puede traer textos («command(npm test)») u objetos con
	herramienta y parámetros; se aceptan ambos. La regla sugerida es la que
	habría que permitir (``command(npm)``, ``write_file(ruta)``...), o ``""``.
	"""
	if isinstance(accion, str):
		texto = accion.strip()
		coincidencia = re.fullmatch(r"([a-z_]+)\((.*)\)", texto, re.IGNORECASE)
		if coincidencia and "<" in coincidencia.group(2):
			# Ejemplo genérico del aviso de agy («mcp(<target>)»), no una regla de verdad.
			return "una acción", ""
		if coincidencia:
			tipo, argumento = coincidencia.group(1).lower(), coincidencia.group(2).strip()
			if tipo == "command":
				return f"ejecutar la orden «{argumento}»", _regla_orden(argumento)
			if tipo in ("write_file", "edit_file"):
				return f"modificar «{argumento}»", texto
			if tipo == "mcp":
				servidor, _barra, herramienta = argumento.partition("/")
				if herramienta and herramienta != "*":
					return f"usar la herramienta «{herramienta}» del servidor MCP «{servidor}»", texto
				return f"usar las herramientas del servidor MCP «{servidor}»", texto
			return f"usar {tipo} con «{argumento}»", texto
		return texto or "una acción", ""
	if isinstance(accion, dict):
		nombre = str(accion.get("tool_name") or accion.get("tool") or accion.get("name") or accion.get("type") or "")
		parametros = accion.get("parameters") or accion.get("args") or accion.get("input") or {}
		if not isinstance(parametros, dict):
			parametros = {}
		parametros = {**{k: v for k, v in accion.items() if isinstance(v, str)}, **parametros}
		orden = _parametro(parametros, "command", "CommandLine", "cmd")
		ruta = _parametro(parametros, "path", "file_path", "TargetFile", "AbsolutePath")
		regla_dada = _parametro(accion, "rule", "permission")
		if orden:
			return f"ejecutar la orden «{_texto_corto(orden, 300)}»", regla_dada or _regla_orden(orden)
		if ruta and any(p in nombre.lower() for p in ("write", "edit", "replace", "create")):
			return f"modificar «{ruta}»", regla_dada or f"write_file({ruta})"
		descripcion = _parametro(accion, "description", "message", "reason")
		if descripcion:
			return descripcion, regla_dada
		return (f"usar la herramienta {nombre}" if nombre else "una acción"), regla_dada
	return _texto_corto(accion) or "una acción", ""


#: Opción de «Permitir y reintentar» que permite todas las herramientas del servidor MCP.
OPCION_SERVIDOR_REINTENTAR = "permitir_servidor_reintentar"


def regla_servidor_mcp(regla: str) -> str:
	"""``mcp(servidor/*)`` para una regla ``mcp(servidor/herramienta)``; ``""`` si no es de MCP."""
	coincidencia = re.fullmatch(r"mcp\(([^/()]+)/[^()]*\)", (regla or "").strip(), re.IGNORECASE)
	return f"mcp({coincidencia.group(1)}/*)" if coincidencia else ""


def _regla_orden(orden: str) -> str:
	"""``command(programa)`` para la orden dada (``git status`` → ``command(git)``)."""
	partes = orden.strip().split()
	if not partes:
		return ""
	programa = Path(partes[0].strip("\"'")).name
	if programa.lower().endswith(".exe"):
		programa = programa[:-4]
	return f"command({programa})" if programa else ""


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


class ConversacionAntigravity(Conversacion):
	"""Sesión con ``agy`` en modo ``stream-json``.

	Un proceso vive mientras dure la conversación y recibe un mensaje por
	turno. Si el usuario cambia modelo, esfuerzo o modo de permisos (o detiene
	un turno), el proceso se cierra y el siguiente envío lo relanza con
	``--conversation <id>``.
	"""

	def __init__(
		self,
		ajustes: AjustesSesion,
		al_evento: ReceptorEventos,
		orden_base: Sequence[str] | None,
		ruta_ajustes: Path | None = None,
	) -> None:
		super().__init__(ajustes, al_evento)
		self._orden_base = list(orden_base) if orden_base else None
		self._ruta_ajustes = ruta_ajustes
		self._candado = threading.RLock()
		self._proceso: ProcesoLineas | None = None
		self._relanzar = False
		self._cerrada = False
		self._turno_activo = False
		self._turno = 0
		self._cola: list[str] = []
		self._ultimo_envio = ""
		self._iniciado_emitido = False
		# Estado del turno en curso.
		self._textos: dict[Any, list[str]] = {}
		self._herramientas_vistas: set[Any] = set()
		self._mensaje_en_turno = False
		# Denegaciones.
		self._lineas_error_vistas: set[str] = set()
		self._denegaciones: dict[str, dict[str, str]] = {}
		self._contador_denegaciones = 0
		#: Reglas que los pasos de este turno dicen que faltaban (``mcp(servidor/herramienta)``...).
		self._reglas_turno: list[str] = []
		#: Ya se comprobó (en esta conversación) la regla que permite las herramientas de Ctrl+IA.
		self._regla_ctrlia_puesta = False
		if ajustes.bifurcar_de:
			log.info("Antigravity no sabe bifurcar conversaciones: se abre una nueva")

	# -- Orden y proceso ------------------------------------------------------

	def construir_orden(self) -> list[str]:
		"""Programa y argumentos con los que se lanzaría ``agy`` ahora mismo."""
		base = ["agy"] if remoto.es_remota(self.ajustes.extra) else self._orden_base
		if not base:
			return []
		orden = [*base, "--input-format", "stream-json", "--output-format", "stream-json"]
		ajustes = self.ajustes
		if ajustes.modelo:
			orden += ["--model", ajustes.modelo]
		if ajustes.esfuerzo in _ESFUERZOS_VALIDOS:
			orden += ["--effort", ajustes.esfuerzo]
		if ajustes.id_externo:
			orden += ["--conversation", ajustes.id_externo]
		if ajustes.modo_permiso == MODO_SANDBOX:
			orden.append("--sandbox")
		elif ajustes.modo_permiso == MODO_SIN_PREGUNTAS:
			orden.append("--dangerously-skip-permissions")
		return orden

	def _crear_proceso(self) -> ProcesoLineas:
		"""Prepara (sin arrancar) un proceso nuevo. Lanza ``OSError`` si no hay ``agy``."""
		orden = self.construir_orden()
		if not orden:
			raise FileNotFoundError("agy")
		orden += self._args_herramientas_ctrlia()
		if self.ajustes.modo_permiso == MODO_REGLAS and self.ajustes.reglas_permitidas:
			for regla in self.ajustes.reglas_permitidas:
				try:
					self._anadir_regla(regla)
				except (ErrorAjustesAntigravity, remoto.ErrorRemoto):
					log.debug("No se pudo añadir la regla de sesión %s", regla, exc_info=True)
		carpeta, entorno = self.ajustes.carpeta, entorno_hijo(quitar=VARIABLES_QUITADAS)
		try:
			equipo = remoto.equipo_de(self.ajustes.extra)
			if equipo is not None:
				orden = remoto.orden_remota(equipo, "antigravity", orden[1:], remoto.carpeta_remota(self.ajustes.extra),
					quitar=VARIABLES_QUITADAS)
				carpeta, entorno = None, remoto.entorno_ssh()
		except remoto.ErrorRemoto as error:
			raise OSError(str(error)) from error
		caja: dict[str, ProcesoLineas] = {}
		proceso = ProcesoLineas(
			orden,
			lambda linea: self._procesar_linea(caja["p"], linea),
			lambda codigo: self._al_terminar_proceso(caja["p"], codigo),
			carpeta=carpeta,
			entorno=entorno,
			nombre="antigravity",
		)
		caja["p"] = proceso
		return proceso

	def _quitar_modelo_desconocido(self, detalle: str) -> None:
		"""Si ``agy`` ya no reconoce el modelo de la sesión, sigue con el predeterminado y lo dice (y el gestor lo guarda).

		``agy`` 1.4.4 dejó de aceptar los nombres genéricos («flash», «pro»...) que
		aceptaban las versiones anteriores: sin esto, la sesión fallaría en cada mensaje.
		"""
		modelo = self.ajustes.modelo
		if not modelo or not _RE_MODELO_DESCONOCIDO.search(detalle):
			return
		with self._candado:
			self.ajustes.modelo = ""
			self._relanzar = True
		log.info("Antigravity no reconoce el modelo %s; se usa el predeterminado", modelo)
		self._emitir(
			TipoEvento.AVISO,
			"Antigravity ya no reconoce el modelo «%s»: esta sesión usará su modelo predeterminado. Vuelve a enviar el "
			"mensaje; para elegir otro modelo, abre Opciones de la sesión (Ctrl+Mayús+O)." % modelo,
			modelo_sustituido="",
		)

	def _args_herramientas_ctrlia(self) -> list[str]:
		"""``--add-dir <carpeta>`` con el complemento ``ctrlia`` y su servidor MCP (solo en este equipo).

		NUNCA ``--agent``: un agente propio de ``agy`` se queda sin herramientas de
		edición. Sin la regla ``mcp(ctrlia_ctrlia/*)``, ``agy`` deniega las
		herramientas en silencio: se añade con el mecanismo de siempre.
		"""
		if remoto.es_remota(self.ajustes.extra):
			return []
		herramientas = self._preparar_herramientas_ctrlia("Antigravity")
		if herramientas is None:
			return []
		from app.services import herramientas_propias

		herramientas.conversacion_externa = self.ajustes.id_externo or herramientas.conversacion_externa
		try:
			carpeta = herramientas_propias.preparar_agy()
		except OSError:
			log.warning("No se pudo preparar el agente de Ctrl+IA para Antigravity", exc_info=True)
			return []
		if not self._regla_ctrlia_puesta:
			try:
				self._anadir_regla(herramientas_propias.REGLA_AGY)
				self._regla_ctrlia_puesta = True
			except (ErrorAjustesAntigravity, remoto.ErrorRemoto):
				log.warning("No se pudo permitir las herramientas de Ctrl+IA en Antigravity", exc_info=True)
		return ["--add-dir", str(carpeta)]

	def inventario(self) -> Any:
		"""Solo lo que le da Ctrl+IA: ``agy`` dice sus herramientas propias al empezar, pero no sus servidores MCP."""
		if self.herramientas_ctrlia is None:
			return None
		from app.services import herramientas_propias

		return herramientas_propias.inventario_basico("Antigravity")

	@staticmethod
	def _retirar(proceso: ProcesoLineas | None, suave: bool) -> None:
		"""Cierra un proceso que ya no es el actual (suave: cierra la entrada y espera)."""
		if proceso is None:
			return
		try:
			if suave:
				proceso.cerrar_entrada()
				proceso.esperar_fin(5.0)
			proceso.terminar(espera=2.0)
		except Exception:  # noqa: BLE001 - retirar un proceso viejo nunca debe propagar
			log.debug("Fallo al retirar el proceso de Antigravity", exc_info=True)

	# -- API pública ----------------------------------------------------------

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		contenido = self._componer(texto, adjuntos, remota=remoto.es_remota(self.ajustes.extra))
		if not contenido.strip():
			return
		with self._candado:
			if self._cerrada:
				log.debug("Envío ignorado: la conversación de Antigravity está cerrada")
				return
			if self._turno_activo:
				self._cola.append(contenido)
				return
			if self._cola:
				self._cola.append(contenido)
				contenido = self._cola.pop(0)
			self._turno_activo = True
		self._empezar_turno(contenido)

	def detener(self) -> None:
		with self._candado:
			if not self._turno_activo:
				return
			proceso = self._proceso
			self._proceso = None
			self._turno_activo = False
			self._limpiar_turno()
		threading.Thread(target=self._retirar, args=(proceso, False), name="antigravity-detener", daemon=True).start()
		self._emitir(TipoEvento.FIN_TURNO, "Has detenido a Antigravity.", correcto=False, interrumpido=True, resumen="")
		self._cambiar_estado(Estado.INACTIVA)

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		"""Contesta a un «Permitir y reintentar» ofrecido tras una denegación.

		``agy`` no hace preguntas en este modo; las únicas «solicitudes» son
		las denegaciones avisadas con ``datos["solicitud_id"]``.
		"""
		with self._candado:
			denegacion = self._denegaciones.pop(solicitud_id, None)
		if denegacion is None:
			log.debug("Respuesta a una solicitud desconocida de Antigravity: %s", solicitud_id)
			return
		permitir = respuesta.opcion in (ClaseOpcion.PERMITIR, ClaseOpcion.PERMITIR_SESION, ClaseOpcion.PERMITIR_SIEMPRE,
			"permitir_reintentar", OPCION_SERVIDOR_REINTENTAR)
		regla = denegacion.get("regla") or ""
		if respuesta.opcion == OPCION_SERVIDOR_REINTENTAR:
			regla = regla_servidor_mcp(regla) or regla
		if permitir and regla:
			self.permitir_y_reintentar(regla, respuesta.mensaje)

	def _anadir_regla(self, regla: str) -> None:
		"""Añade la regla en el settings.json de ``agy``: el de Windows o, en otro equipo, el de ese equipo."""
		equipo = remoto.equipo_de(self.ajustes.extra)
		if equipo is None:
			anadir_regla(regla, self._ruta_ajustes)
			return
		anadir_regla_remota(equipo, regla)

	def permitir_y_reintentar(self, regla: str, texto: str = "") -> None:
		"""Añade ``regla`` a los ajustes de ``agy``, relanza y pide repetir el trabajo.

		Lanza :class:`ErrorAjustesAntigravity` si el archivo no se puede tocar.
		"""
		self._anadir_regla(regla)
		with self._candado:
			self._relanzar = True
		self.enviar(texto or f"Ya tienes permiso para {regla}. Vuelve a intentar lo que no pudiste hacer por falta de permiso.")

	def cambiar_ajustes(self, **cambios: Any) -> None:
		antes = (self.ajustes.modelo, self.ajustes.esfuerzo, self.ajustes.modo_permiso)
		super().cambiar_ajustes(**cambios)
		despues = (self.ajustes.modelo, self.ajustes.esfuerzo, self.ajustes.modo_permiso)
		if antes != despues:
			with self._candado:
				self._relanzar = True

	def pendientes(self) -> list[str]:
		with self._candado:
			return list(self._cola)

	def vaciar_pendientes(self) -> list[str]:
		"""Saca de la cola los mensajes no enviados (para devolverlos al cuadro)."""
		with self._candado:
			cola, self._cola = self._cola, []
		return cola

	def cerrar(self) -> None:
		with self._candado:
			if self._cerrada:
				return
			self._cerrada = True
			proceso = self._proceso
			self._proceso = None
			self._turno_activo = False
			self._limpiar_turno()
		self._retirar_herramientas_ctrlia()
		if proceso is not None:
			try:
				proceso.cerrar_entrada()
				proceso.esperar_fin(1.0)
				proceso.terminar(espera=2.0)
			except Exception:  # noqa: BLE001 - cerrar debe ser siempre seguro
				log.debug("Fallo al cerrar Antigravity", exc_info=True)
		self._cambiar_estado(Estado.CERRADA)

	# -- Envío ----------------------------------------------------------------

	@staticmethod
	def _componer(texto: str, adjuntos: Sequence[Adjunto], remota: bool = False) -> str:
		"""Texto del mensaje; los adjuntos van como rutas (``agy`` solo admite texto).

		En otro equipo (``remota``) las rutas de Windows no le sirven: se dice que no se han enviado.
		"""
		texto = texto or ""
		if not adjuntos:
			return texto
		if remota:
			nombres = ", ".join(Path(adjunto.ruta).name for adjunto in adjuntos)
			return (texto.rstrip() + "\n\n" if texto.strip() else "") + f"(Adjuntos que no se han enviado porque están en Windows: {nombres})"
		lineas = [f"- {Path(adjunto.ruta)}" for adjunto in adjuntos]
		return (texto.rstrip() + "\n\n" if texto.strip() else "") + "Archivos adjuntos (léelos desde su ruta):\n" + "\n".join(lineas)

	def _empezar_turno(self, contenido: str) -> None:
		"""Arranca un turno con ``contenido`` (``_turno_activo`` ya está a ``True``)."""
		with self._candado:
			self._turno += 1
			self._limpiar_turno()
			self._ultimo_envio = contenido
			proceso = self._proceso
			hay_que_lanzar = proceso is None or not proceso.vivo or self._relanzar
		self._cambiar_estado(Estado.TRABAJANDO)
		if hay_que_lanzar:
			threading.Thread(target=self._relanzar_y_escribir, args=(contenido,), name="antigravity-envio", daemon=True).start()
		else:
			self._escribir(proceso, contenido)

	def _relanzar_y_escribir(self, contenido: str) -> None:
		with self._candado:
			viejo = self._proceso
			self._proceso = None
			self._relanzar = False
		self._retirar(viejo, suave=True)
		proceso: ProcesoLineas | None = None
		try:
			proceso = self._crear_proceso()
			# Se fija como actual ANTES de arrancarlo: la primera línea
			# (``init``) puede llegar en cuanto el proceso empieza.
			with self._candado:
				if self._cerrada or not self._turno_activo:
					return
				self._proceso = proceso
			proceso.iniciar()
		except OSError as exc:
			log.debug("No se pudo arrancar agy", exc_info=True)
			with self._candado:
				if proceso is not None and self._proceso is proceso:
					self._proceso = None
			if self._orden_base:
				mensaje = f"No se pudo arrancar Antigravity CLI: {exc}"
			else:
				mensaje = "No encuentro Antigravity CLI (agy). Puedes instalarlo desde «Agentes instalados»."
			self._fallar_turno(mensaje)
			return
		self._escribir(proceso, contenido)

	def _escribir(self, proceso: ProcesoLineas, contenido: str) -> None:
		if proceso.escribir_json({"event": "user", "message": {"content": contenido}}):
			return
		# El mensaje queda en duda: nunca se reenvía solo.
		with self._candado:
			if self._proceso is proceso:
				self._proceso = None
		self._retirar(proceso, suave=False)
		self._fallar_turno(
			"No se pudo entregar el mensaje a Antigravity. No lo reenvío para no duplicarlo: revisa la conversación y vuelve a enviarlo."
		)

	def _fallar_turno(self, mensaje: str) -> None:
		with self._candado:
			if not self._turno_activo:
				return
			self._turno_activo = False
			self._limpiar_turno()
		self._emitir(TipoEvento.ERROR, mensaje)
		self._emitir(TipoEvento.FIN_TURNO, mensaje, correcto=False, interrumpido=False, resumen="")
		self._cambiar_estado(Estado.ERROR)

	def _limpiar_turno(self) -> None:
		self._textos = {}
		self._reglas_turno = []
		self._herramientas_vistas = set()
		self._mensaje_en_turno = False

	def _siguiente_de_la_cola(self) -> None:
		with self._candado:
			if self._cerrada or self._turno_activo or not self._cola:
				return
			contenido = self._cola.pop(0)
			self._turno_activo = True
		self._empezar_turno(contenido)

	# -- Lectura --------------------------------------------------------------

	def _procesar_linea(self, proceso: ProcesoLineas, linea: str) -> None:
		if proceso is not self._proceso:
			return
		objeto = json_o_nada(linea)
		if objeto is None:
			log.debug("Línea de agy que no es JSON: %s", linea[:300])
			return
		evento = objeto.get("event") or objeto.get("type")
		if evento == "init":
			self._al_init(objeto)
		elif evento == "step_update":
			self._al_paso(objeto)
		elif evento == "result":
			self._al_resultado(proceso, objeto)
		elif evento == "error":
			detalle = objeto.get("error") if isinstance(objeto.get("error"), dict) else {}
			mensaje = detalle.get("message") or objeto.get("message") or "error desconocido"
			self._emitir(TipoEvento.ERROR, f"Antigravity informa de un error: {mensaje}")
		else:
			log.debug("Evento de agy sin tratar: %s", evento)

	def _fijar_id(self, identificador: Any) -> None:
		if isinstance(identificador, str) and identificador and self.herramientas_ctrlia is not None:
			# Con este identificador reconoce Ctrl+IA a qué sesión van las herramientas que llame agy.
			self.herramientas_ctrlia.conversacion_externa = identificador
		if isinstance(identificador, str) and identificador and identificador != self.ajustes.id_externo:
			anterior = self.ajustes.id_externo
			self.ajustes.id_externo = identificador
			if anterior:
				# agy no encuentra la conversación pedida con --conversation y, en vez de fallar, empieza otra.
				log.warning("agy no encontró la conversación %s y ha empezado la %s", anterior, identificador)
				self._emitir(TipoEvento.AVISO, "Antigravity no ha encontrado la conversación que se quería retomar "
					"y ha empezado una nueva. Lo anterior no lo recuerda.")

	def _al_init(self, objeto: dict[str, Any]) -> None:
		datos = objeto.get("init") if isinstance(objeto.get("init"), dict) else {}
		self._fijar_id(objeto.get("conversation_id") or datos.get("conversation_id"))
		if self._iniciado_emitido:
			log.debug("agy relanzado en la conversación %s", self.ajustes.id_externo)
			return
		self._iniciado_emitido = True
		self._emitir(
			TipoEvento.INICIADO,
			"Antigravity está listo.",
			id_externo=self.ajustes.id_externo,
			modelo=self.ajustes.modelo,
			modo=self.ajustes.modo_permiso or MODO_REVISAR,
			modo_agente=datos.get("permission_mode", ""),
			herramientas=list(datos.get("tools") or []),
			carpeta=datos.get("cwd", ""),
		)

	def _al_paso(self, objeto: dict[str, Any]) -> None:
		paso = objeto.get("step_update")
		if not isinstance(paso, dict):
			return
		self._fijar_id(paso.get("conversation_id"))
		tipo = paso.get("step_type")
		estado = str(paso.get("state") or "").upper()
		clave = f"{self._turno}-{paso.get('step_index')}"
		if tipo == "agent_response":
			delta = paso.get("text_delta")
			if isinstance(delta, str) and delta:
				self._textos.setdefault(clave, []).append(delta)
				self._emitir(TipoEvento.TEXTO_PARCIAL, delta, id=clave)
			if estado == "DONE":
				texto = "".join(self._textos.pop(clave, []))
				if texto.strip():
					self._mensaje_en_turno = True
					self._emitir(TipoEvento.MENSAJE, texto, id=clave)
		elif tipo == "tool":
			self._al_herramienta(paso, estado, clave)
		if isinstance(paso.get("usage"), dict):
			self._emitir_uso(paso["usage"])

	def _al_herramienta(self, paso: dict[str, Any], estado: str, clave: str) -> None:
		info = paso.get("tool_info") if isinstance(paso.get("tool_info"), dict) else {}
		nombre = str(paso.get("tool_name") or info.get("name") or "herramienta")
		parametros = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
		detalle = describir_herramienta(nombre, parametros)
		if clave not in self._herramientas_vistas:
			self._herramientas_vistas.add(clave)
			self._emitir(TipoEvento.HERRAMIENTA, detalle, herramienta=nombre, detalle=detalle, id=clave, parametros=parametros)
		if estado not in ("DONE", "ERROR"):
			# ERROR también cierra el paso: una herramienta denegada o que falló (con su motivo en «error»).
			return
		salida = info.get("output")
		if salida is not None and not isinstance(salida, str):
			salida = json.dumps(salida, ensure_ascii=False)
		salida = salida or ""
		if len(salida) > MAX_SALIDA_HERRAMIENTA:
			salida = salida[:MAX_SALIDA_HERRAMIENTA] + "\n[…salida recortada…]"
		error = info.get("error")
		mensaje_error = ""
		if isinstance(error, dict):
			mensaje_error = str(error.get("message") or error.get("type") or "")
		elif error:
			mensaje_error = str(error)
		denegada = _RE_REGLA_DENEGADA.search(mensaje_error) if mensaje_error else None
		if denegada and denegada.group(1) not in self._reglas_turno:
			self._reglas_turno.append(denegada.group(1))
		texto = f"Error en «{detalle}»: {mensaje_error}" if mensaje_error else f"Hecho: {detalle}"
		self._emitir(TipoEvento.HERRAMIENTA_FIN, texto, herramienta=nombre, salida=salida, error=mensaje_error, id=clave)
		ruta = _parametro(parametros, "path", "file_path", "TargetFile", "AbsolutePath")
		if not mensaje_error and ruta and any(p in nombre.lower() for p in ("write", "edit", "replace", "create_file")):
			self._emitir(TipoEvento.ARCHIVO, f"Archivo modificado: {ruta}", ruta=ruta, tipo="modificado", diff="")

	def _emitir_uso(self, uso: dict[str, Any]) -> None:
		def entero(clave: str) -> int | None:
			valor = uso.get(clave)
			return int(valor) if isinstance(valor, (int, float)) else None

		entrada = entero("input_tokens")
		salida = entero("output_tokens")
		self._emitir(
			TipoEvento.USO,
			"",
			entrada=entrada,
			salida=salida,
			razonamiento=entero("thinking_tokens"),
			cache=entero("cache_read_tokens"),
			total=entero("total_tokens"),
			contexto_usado=entrada,
			contexto_total=None,
			coste=None,
		)

	def _al_resultado(self, proceso: ProcesoLineas, objeto: dict[str, Any]) -> None:
		resultado = objeto.get("result") if isinstance(objeto.get("result"), dict) else {}
		self._fijar_id(resultado.get("conversation_id"))
		estado = str(resultado.get("status") or "").upper()
		respuesta = resultado.get("response") if isinstance(resultado.get("response"), str) else ""
		# Texto que no llegó a cerrarse con DONE.
		for clave, trozos in list(self._textos.items()):
			texto = "".join(trozos)
			if texto.strip():
				self._mensaje_en_turno = True
				self._emitir(TipoEvento.MENSAJE, texto, id=clave)
		self._textos = {}
		if not self._mensaje_en_turno and respuesta.strip():
			self._emitir(TipoEvento.MENSAJE, respuesta, id=f"{self._turno}-respuesta")
		if isinstance(resultado.get("usage"), dict):
			self._emitir_uso(resultado["usage"])
		denegadas = resultado.get("denied_actions") or []
		reglas_pasos = bool(self._reglas_turno)
		if not isinstance(denegadas, list):
			denegadas = [denegadas]
		self._avisar_denegaciones(denegadas)
		correcto = estado == "SUCCESS"
		interrumpido = estado in _ESTADOS_INTERRUMPIDO
		if not correcto and not interrumpido:
			error = resultado.get("error")
			detalle = (error.get("message") if isinstance(error, dict) else error) or respuesta
			if not detalle:
				errores = proceso.errores_recientes(3)
				detalle = errores[-1] if errores else ""
			self._emitir(
				TipoEvento.ERROR,
				"Antigravity no pudo completar la tarea" + (f": {_texto_corto(detalle, 500)}" if detalle else "."),
				estado_agy=estado,
			)
			self._quitar_modelo_desconocido(str(detalle or ""))
		if correcto:
			texto_fin = "Antigravity ha terminado."
		elif interrumpido:
			texto_fin = "Antigravity ha interrumpido la tarea."
		else:
			texto_fin = "Antigravity ha terminado con un error."
		with self._candado:
			self._turno_activo = False
			self._limpiar_turno()
		self._emitir(
			TipoEvento.FIN_TURNO,
			texto_fin,
			correcto=correcto,
			interrumpido=interrumpido,
			resumen=_texto_corto(respuesta, 300),
			estado_agy=estado,
			turnos=resultado.get("num_turns"),
			denegadas=len(denegadas),
		)
		self._cambiar_estado(Estado.INACTIVA)
		# El error estándar se lee en otro hilo: se revisa ahora y un poco después.
		avisar = not denegadas and not reglas_pasos
		self._revisar_errores(proceso, avisar=avisar)
		temporizador = threading.Timer(0.4, self._revisar_errores, args=(proceso,), kwargs={"avisar": avisar})
		temporizador.daemon = True
		temporizador.start()
		self._siguiente_de_la_cola()

	def _avisar_denegaciones(self, denegadas: list[Any]) -> None:
		"""Una petición por cada acción denegada, con la regla que falta.

		Si ``denied_actions`` no trae la regla (las herramientas MCP), se toma del
		error del paso denegado; las reglas de los pasos que no casan con ninguna
		acción también se ofrecen. Cada regla, una sola vez.
		"""
		pendientes = list(self._reglas_turno)
		avisadas: set[str] = set()
		for accion in denegadas:
			frase, regla = describir_denegacion(accion)
			if not regla and pendientes:
				tipo = str(accion.get("action") or "").lower() if isinstance(accion, dict) else ""
				regla = next((r for r in pendientes if tipo and r.lower().startswith(tipo + "(")), pendientes[0])
				frase = describir_denegacion(regla)[0]
			if regla in pendientes:
				pendientes.remove(regla)
			if regla and regla in avisadas:
				continue
			avisadas.add(regla)
			self._avisar_denegacion(frase, regla)
		for regla in pendientes:
			if regla not in avisadas:
				avisadas.add(regla)
				self._avisar_denegacion(*describir_denegacion(regla))

	def _avisar_denegacion(self, frase: str, regla: str) -> None:
		with self._candado:
			self._contador_denegaciones += 1
			solicitud_id = f"denegada-{self._contador_denegaciones}"
			if regla:
				self._denegaciones[solicitud_id] = {"regla": regla, "accion": frase}
		texto = f"Antigravity no pudo {frase} porque necesita permiso."
		if regla:
			texto += " Puedes permitirlo y reintentar."
		self._emitir(
			TipoEvento.AVISO,
			texto,
			denegada=True,
			accion=frase,
			regla=regla,
			solicitud_id=solicitud_id if regla else "",
			reintentable=bool(regla),
			reintento=self._ultimo_envio,
		)

	def _revisar_errores(self, proceso: ProcesoLineas, avisar: bool = True) -> None:
		"""Busca denegaciones en el error estándar y las avisa (una vez cada línea)."""
		nuevas = []
		with self._candado:
			for linea in proceso.errores_recientes(50):
				if linea in self._lineas_error_vistas:
					continue
				self._lineas_error_vistas.add(linea)
				if _RE_DENEGACION.search(linea):
					nuevas.append(linea)
		if not avisar:
			return
		for linea in nuevas[:5]:
			regla_en_linea = _RE_REGLA.search(linea)
			if regla_en_linea:
				frase, regla = describir_denegacion(regla_en_linea.group(1))
			else:
				frase, regla = f"hacer algo ({_texto_corto(linea, 300)})", ""
			self._avisar_denegacion(frase, regla)

	def _al_terminar_proceso(self, proceso: ProcesoLineas, codigo: int | None) -> None:
		with self._candado:
			if proceso is not self._proceso:
				return
			self._proceso = None
			activo = self._turno_activo
			cerrada = self._cerrada
		if cerrada:
			return
		self._revisar_errores(proceso)
		errores = proceso.errores_recientes(3)
		detalle = f" Último mensaje: {_texto_corto(errores[-1], 300)}" if errores else ""
		if activo:
			self._fallar_turno(f"Antigravity se ha cerrado de forma inesperada (código {codigo}).{detalle}")
		elif codigo not in (0, None):
			self._emitir(TipoEvento.AVISO, f"Antigravity se ha cerrado (código {codigo}). Se volverá a abrir al enviar otro mensaje.{detalle}")


# --------------------------------------------------------------------------
# Proveedor
# --------------------------------------------------------------------------


class ProveedorAntigravity(Proveedor):
	"""Antigravity CLI (``agy``) de Google.

	:param ejecutable: orden base para lanzar ``agy`` (ruta o lista, p. ej.
		``[sys.executable, "fake_agy.py"]`` en las pruebas). Por defecto se busca.
		También se puede dar por conversación en ``AjustesSesion.extra["ejecutable"]``.
	:param ruta_ajustes: ``settings.json`` de las reglas (por defecto el del usuario).
	"""

	id = "antigravity"
	nombre = "Antigravity (Google)"
	descripcion = (
		"El agente de Google con los modelos Gemini, a través de Antigravity CLI. "
		"Usa tu suscripción de Google; también puede abrir tus proyectos en el IDE o en la app de Antigravity."
	)
	web = "https://antigravity.google/docs/cli"
	capacidades = Capacidades(
		adjuntar_imagenes=False,
		adjuntar_archivos=True,
		bifurcar=False,
		permisos_interactivos=False,
		cambiar_modelo_en_marcha=True,
		comandos_barra=False,
		enviar_ahora=False,
		leer_historial_externo=False,
	)

	def __init__(self, ejecutable: str | Path | Sequence[str] | None = None, ruta_ajustes: Path | None = None) -> None:
		if ejecutable is None:
			self._ejecutable: list[str] | None = None
		elif isinstance(ejecutable, (str, Path)):
			self._ejecutable = [str(ejecutable)]
		else:
			self._ejecutable = [str(parte) for parte in ejecutable]
		self._ruta_ajustes = Path(ruta_ajustes) if ruta_ajustes is not None else None

	# -- Programa -------------------------------------------------------------

	def orden_base(self) -> list[str] | None:
		"""Orden que lanza ``agy`` (sin argumentos), o ``None`` si no está instalado."""
		if self._ejecutable:
			return list(self._ejecutable)
		ruta = buscar_agy()
		return [str(ruta)] if ruta else None

	def detectar(self) -> EstadoProveedor:
		orden = self.orden_base()
		extras = []
		if buscar_antigravity_ide() is not None:
			extras.append("el IDE de Antigravity")
		if buscar_antigravity_app() is not None:
			extras.append("la app de escritorio de Antigravity")
		instalados = (" Tienes instalado " + " y ".join(extras) + ".") if extras else ""
		if orden is None:
			return EstadoProveedor(
				disponible=False,
				mensaje=(
					"Antigravity CLI (agy) no está instalado. Puedes instalarlo desde «Agentes instalados»; "
					"después inicia sesión una vez con tu cuenta de Google." + instalados
				),
			)
		version = version_de([*orden, "--version"])
		return EstadoProveedor(
			disponible=True,
			ruta=orden[-1],
			version=version,
			mensaje=("Antigravity CLI está listo." + instalados).strip(),
		)

	def leer_limites(self) -> Any:
		"""Límites de 5 horas y semanales con ``agy -p /usage --output-format json`` (no gasta cuota).

		Una versión de agy sin ``/usage`` lo tomaría por un mensaje y gastaría
		cuota: por eso solo se pregunta desde la versión que lo tiene, y se
		descarta cualquier respuesta que no sea la del comando.
		"""
		from app.services import limites

		orden = self.orden_base()
		if orden is None:
			return None
		version = _version_numerica(version_de([*orden, "--version"]))
		if version is None or version < VERSION_MINIMA_USO:
			raise limites.ErrorLimites("Para ver los límites hace falta Antigravity CLI {m} o posterior. Actualízalo con "
				"«agy update» en una consola.".format(m=".".join(map(str, VERSION_MINIMA_USO))))
		try:
			codigo, salida, errores = ejecutar_y_leer([*orden, "-p", "/usage", "--output-format", "json",
				"--print-timeout", "60s"], TIEMPO_USO, entorno_hijo(quitar=VARIABLES_QUITADAS))
		except (OSError, subprocess.SubprocessError) as error:
			raise limites.ErrorLimites("Antigravity CLI no ha contestado a tiempo.") from error
		try:
			datos = json.loads(salida.strip().splitlines()[-1]) if salida.strip() else None
		except ValueError:
			datos = None
		if not isinstance(datos, dict) or datos.get("num_turns") not in (0, None) or not datos.get("command"):
			log.info("agy /usage devolvió algo inesperado (código %s): %s", codigo, (errores or salida)[:300])
			raise limites.ErrorLimites("Antigravity CLI no ha dado los límites. Comprueba que has iniciado sesión con tu "
				"cuenta de Google (Herramientas, Agentes instalados).")
		resultado = limites.desde_antigravity(datos)
		if resultado is None:
			raise limites.ErrorLimites("Antigravity no ha informado de límites para tu cuenta.")
		return resultado

	def crear_conversacion(self, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> ConversacionAntigravity:
		extra = ajustes.extra.get("ejecutable") if isinstance(ajustes.extra, dict) else None
		if extra:
			orden = [str(extra)] if isinstance(extra, (str, Path)) else [str(parte) for parte in extra]
		else:
			orden = self.orden_base()
		return ConversacionAntigravity(ajustes, al_evento, orden, self._ruta_ajustes)

	# -- Listas ---------------------------------------------------------------

	def modelos(self) -> list[OpcionLista]:
		return [
			OpcionLista("", "Predeterminado de Antigravity"),
			OpcionLista("flash", "Gemini Flash (rápido)"),
			OpcionLista("pro", "Gemini Pro (el más capaz, más lento)"),
			OpcionLista("flash_lite", "Gemini Flash Lite (el más ligero)"),
		]

	def listar_modelos(self) -> list[OpcionLista]:
		"""Modelos que anuncia ``agy models`` (si existe); sin esa lista, los genéricos de :meth:`modelos`.

		Si ``agy`` da su lista, los genéricos («flash», «pro»...) no se ofrecen:
		``agy`` 1.4.4 ya no los acepta. Ejecuta un proceso: llamar desde un hilo.
		"""
		modelos = self.modelos()
		orden = self.orden_base()
		if orden is None:
			return modelos
		try:
			codigo, salida, _error = ejecutar_y_leer([*orden, "models"], tiempo=20.0)
		except (OSError, subprocess.SubprocessError):
			log.debug("No se pudo ejecutar agy models", exc_info=True)
			return modelos
		if codigo != 0:
			return modelos
		genericos = modelos
		modelos = [genericos[0]]
		conocidos = {genericos[0].id}
		for linea in salida.splitlines():
			linea = linea.strip().lstrip("*-• ").strip()
			if not linea or linea.endswith(":"):
				continue
			partes = re.split(r"\s+[-—–:]?\s*", linea, maxsplit=1)
			slug = partes[0]
			if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug) or slug in conocidos:
				continue
			conocidos.add(slug)
			descripcion = partes[1].strip(" -—–:()") if len(partes) > 1 else ""
			modelos.append(OpcionLista(slug, slug + (f" ({descripcion})" if descripcion else "")))
		return modelos if len(modelos) > 1 else genericos

	def esfuerzos(self) -> list[OpcionLista]:
		return [
			OpcionLista("", "Predeterminado"),
			OpcionLista("low", "Bajo", "Responde antes y piensa menos."),
			OpcionLista("medium", "Medio"),
			OpcionLista("high", "Alto", "Piensa más antes de responder; tarda más."),
		]

	def modo_predeterminado(self, carpeta: Path | None = None) -> str:
		"""«Revisar», salvo que Antigravity CLI tenga «Hacerlo dentro del sandbox» o «Hacerlo siempre»."""
		from app.services.config_agentes import modo_inicial

		return modo_inicial.elegir(modo_inicial.modo_antigravity(), self.modos_permiso(), self.modo_de_serie())

	def modos_permiso(self) -> list[OpcionLista]:
		return [
			OpcionLista(
				MODO_REVISAR,
				"Revisar (recomendado)",
				"Antigravity puede leer y editar los archivos del proyecto. Las órdenes de terminal se deniegan "
				"salvo las que hayas permitido; te avisaré de cada cosa denegada para que decidas.",
			),
			OpcionLista(
				MODO_REGLAS,
				"Con mis reglas",
				"Como «Revisar», pero aplicando también las reglas que hayas permitido en esta sesión "
				"(por ejemplo, ejecutar git o npm run test).",
			),
			OpcionLista(
				MODO_SANDBOX,
				"Aislado (sandbox)",
				"Antigravity trabaja dentro de un entorno aislado que limita lo que puede tocar en tu equipo.",
			),
			OpcionLista(
				MODO_SIN_PREGUNTAS,
				"Sin restricciones (peligroso)",
				"ATENCIÓN: Antigravity podrá ejecutar cualquier orden y modificar o borrar cualquier archivo "
				"sin pedirte permiso. Úsalo solo en proyectos con copia de seguridad.",
			),
		]

	# -- Reglas ---------------------------------------------------------------

	def leer_reglas(self) -> list[str]:
		return leer_reglas(self._ruta_ajustes)

	def anadir_regla(self, regla: str) -> bool:
		return anadir_regla(regla, self._ruta_ajustes)

	def quitar_regla(self, regla: str) -> bool:
		return quitar_regla(regla, self._ruta_ajustes)

	# -- Instalación ----------------------------------------------------------

	def instrucciones_instalacion(self) -> str:
		return (
			"Para usar Antigravity necesitas Antigravity CLI (agy).\n"
			"1. Pulsa «Instalar»: se abrirá una consola que descarga e instala agy con el instalador oficial de Google.\n"
			"2. Cuando termine, pulsa «Iniciar sesión»: se abrirá agy en una consola; sigue los pasos para entrar con tu "
			"cuenta de Google y después cierra la consola.\n"
			"3. Vuelve aquí y pulsa «Comprobar de nuevo»."
		)

	def orden_instalacion(self) -> list[str] | None:
		return instalar_cli_orden()

	def orden_inicio_sesion(self) -> list[str] | None:
		return self.orden_base()
