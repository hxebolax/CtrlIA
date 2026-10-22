"""Agentes en otro equipo por SSH.

Ctrl+IA habla con los agentes por su entrada y su salida de texto. En lugar de
arrancar el agente en Windows, se arranca ``ssh.exe`` (incluido en Windows 10
y 11) y se le pide que arranque el agente en el otro equipo (macOS o Linux):

	ssh -T -o BatchMode=yes ... -- destino 'exec "$SHELL" -l -c "..."'

Reglas de seguridad:

- Solo claves SSH (``BatchMode=yes``): Ctrl+IA nunca pide, escribe ni guarda
  contraseñas, y nunca acepta por su cuenta la huella de un equipo nuevo.
- Cada parte de la orden remota va entre comillas POSIX (``shlex.quote``): un
  título o un modelo con comillas o «;» no puede ejecutar nada en el otro
  equipo. Lo que la persona escribe al agente nunca va en la orden: va por la
  entrada estándar.
- Las claves de API se quitan también en el otro equipo (``env -u``).
- Nada de tokens en la orden (se verían en la lista de procesos del otro equipo).

Estabilidad: Claude Code y Codex están probados y documentados por sus
fabricantes para este uso; Gemini CLI, Antigravity CLI, los agentes ACP
personalizados y el túnel de Ollama son experimentales (ver ``EXPERIMENTALES``).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.agentes import procesos

log = logging.getLogger(__name__)

#: Clave de la configuración con la lista de equipos.
CLAVE_CONFIG = "agentes.equipos_ssh"

#: Agentes que funcionan en otro equipo: id → programa por defecto en ese equipo.
PROGRAMAS = {"claude": "claude", "codex": "codex", "gemini": "gemini", "antigravity": "agy", "ollama": "ollama"}
#: Agentes estables por SSH (documentados por su fabricante para este uso).
ESTABLES = frozenset({"claude", "codex"})
#: Agentes que funcionan por SSH, pero de forma experimental.
EXPERIMENTALES = frozenset({"gemini", "antigravity", "acp", "ollama"})

#: Carpetas habituales de los agentes que el shell de SSH no siempre tiene en el PATH.
PATH_EXTRA = "$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/.bun/bin:/opt/homebrew/bin:/usr/local/bin"

_RE_DESTINO = re.compile(r"^[A-Za-z0-9_.@\-\[\]:%]+$")
_RE_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TIEMPO_CONEXION = 15
TIEMPO_COMPROBACION = 40

#: Sistemas admitidos en el otro equipo.
SISTEMAS = (("macos", "macOS"), ("linux", "Linux"))


class ErrorRemoto(Exception):
	"""Problema con el equipo remoto; el mensaje es para la persona usuaria."""


@dataclass(frozen=True)
class EquipoSSH:
	"""Un equipo al que se entra por SSH."""

	id: str
	nombre: str
	destino: str
	puerto: int = 22
	sistema: str = "macos"
	#: Ruta del programa de cada agente en ese equipo, si no está en el PATH.
	rutas: dict[str, str] = field(default_factory=dict)
	#: Variables que se ponen al arrancar los agentes en ese equipo.
	variables: dict[str, str] = field(default_factory=dict)
	#: Usar el Ollama de este equipo por un túnel SSH (experimental).
	ollama: bool = False
	#: Mostrar en la lista las sesiones que ya tiene este equipo (``historial/equipos.py``).
	historial: bool = True

	@classmethod
	def desde_dict(cls, datos: dict[str, Any]) -> EquipoSSH:
		puerto = datos.get("puerto", 22)
		return cls(
			id=str(datos.get("id") or ""),
			nombre=str(datos.get("nombre") or datos.get("destino") or ""),
			destino=str(datos.get("destino") or ""),
			puerto=puerto if isinstance(puerto, int) and not isinstance(puerto, bool) else 22,
			sistema=str(datos.get("sistema") or "macos"),
			rutas={str(k): str(v) for k, v in (datos.get("rutas") or {}).items() if str(v).strip()},
			variables={str(k): str(v) for k, v in (datos.get("variables") or {}).items()},
			ollama=datos.get("ollama") is True,
			historial=datos.get("historial") is not False,
		)

	def a_dict(self) -> dict[str, Any]:
		return {"id": self.id, "nombre": self.nombre, "destino": self.destino, "puerto": self.puerto,
			"sistema": self.sistema, "rutas": dict(self.rutas), "variables": dict(self.variables), "ollama": self.ollama,
			"historial": self.historial}

	def programa(self, agente: str) -> str:
		return self.rutas.get(agente) or PROGRAMAS.get(agente, agente)

	@property
	def nombre_sistema(self) -> str:
		"""«macOS» o «Linux»."""
		return dict(SISTEMAS).get(self.sistema, self.sistema)


def validar_equipo(equipo: EquipoSSH) -> str | None:
	"""Mensaje de error para la persona, o ``None`` si el equipo vale."""
	if not equipo.nombre.strip():
		return "Escribe un nombre para el equipo."
	if not equipo.destino or equipo.destino.startswith("-") or not _RE_DESTINO.match(equipo.destino):
		return ("Escribe el destino como un nombre de tu archivo .ssh/config (por ejemplo «mac-estudio») o como "
			"usuario@equipo (por ejemplo «ana@192.168.1.40»), sin espacios.")
	if not 1 <= equipo.puerto <= 65535:
		return "El puerto debe estar entre 1 y 65535 (normalmente 22)."
	if equipo.sistema not in dict(SISTEMAS):
		return "Elige el sistema del otro equipo: macOS o Linux."
	for nombre in equipo.variables:
		if not _RE_VARIABLE.match(nombre):
			return f"«{nombre}» no es un nombre de variable válido (letras, números y guion bajo)."
	return None


# ── Equipos guardados ────────────────────────────────────────────────────────


def _opcion(clave: str, defecto: Any) -> Any:
	from app.core.configuracion import obtener_configuracion

	return obtener_configuracion().obtener(clave, defecto)


def equipos(opcion=None) -> dict[str, EquipoSSH]:
	"""Equipos guardados en Herramientas, «Equipos por SSH», por id."""
	datos = (opcion or _opcion)(CLAVE_CONFIG, []) or []
	resultado = {}
	for elemento in datos if isinstance(datos, list) else []:
		if isinstance(elemento, dict):
			equipo = EquipoSSH.desde_dict(elemento)
			if equipo.id:
				resultado[equipo.id] = equipo
	return resultado


def datos_remotos(extra: dict[str, Any] | None) -> dict[str, Any] | None:
	"""Los datos de equipo remoto de una sesión (``extra["remoto"]``), o ``None`` si es local."""
	datos = (extra or {}).get("remoto")
	return datos if isinstance(datos, dict) and datos.get("equipo") else None


def es_remota(extra: dict[str, Any] | None) -> bool:
	return datos_remotos(extra) is not None


def equipo_de(extra: dict[str, Any] | None, opcion=None) -> EquipoSSH | None:
	"""El equipo de una sesión, o ``None`` si es local. Lanza ``ErrorRemoto`` si ya no existe."""
	datos = datos_remotos(extra)
	if datos is None:
		return None
	equipo = equipos(opcion).get(str(datos["equipo"]))
	if equipo is None:
		raise ErrorRemoto(
			f"El equipo «{datos.get('nombre') or datos['equipo']}» de esta sesión ya no está en Herramientas, "
			"«Equipos por SSH». Vuelve a añadirlo para seguir con la sesión."
		)
	return equipo


def carpeta_remota(extra: dict[str, Any] | None) -> str:
	"""Carpeta de trabajo en el otro equipo (ruta POSIX), o ``""``."""
	datos = datos_remotos(extra)
	return str(datos.get("carpeta") or "") if datos else ""


def normalizar_carpeta(texto: str) -> str:
	"""Ruta POSIX limpia («~» y rutas absolutas). Lanza ``ErrorRemoto`` si no lo es."""
	texto = texto.strip().strip('"').strip("'")
	if not texto:
		raise ErrorRemoto("Escribe la carpeta de trabajo del otro equipo, por ejemplo /Users/ana/proyectos/web.")
	if "\\" in texto or re.match(r"^[A-Za-z]:", texto):
		raise ErrorRemoto("Esa ruta es de Windows. En el otro equipo las carpetas son como /Users/ana/proyectos/web "
			"(macOS) o /home/ana/proyectos/web (Linux).")
	if not (texto.startswith("/") or texto == "~" or texto.startswith("~/")):
		raise ErrorRemoto("La carpeta debe empezar por / o por ~/, por ejemplo ~/proyectos/web.")
	return str(PurePosixPath(texto)) if texto.startswith("/") else texto.rstrip("/") or "~"


def _citar_carpeta(carpeta: str) -> str:
	"""Comillas para ``cd``: «~» se deja fuera de las comillas para que el shell lo expanda."""
	if carpeta == "~":
		return '"$HOME"'
	if carpeta.startswith("~/"):
		return '"$HOME"/' + shlex.quote(carpeta[2:])
	return shlex.quote(carpeta)


# ── La orden ─────────────────────────────────────────────────────────────────


def buscar_ssh() -> Path | None:
	"""``ssh.exe`` de Windows (OpenSSH), o ``None``."""
	sistema = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
	if sistema.is_file():
		return sistema
	encontrado = shutil.which("ssh")
	return Path(encontrado) if encontrado else None


def opciones_ssh(equipo: EquipoSSH, terminal: bool = False) -> list[str]:
	"""Opciones de ``ssh``: sin terminal (``-T``) y sin preguntar nunca nada (``BatchMode``)."""
	return [
		"-t" if terminal else "-T",
		*([] if terminal else ["-e", "none", "-o", "BatchMode=yes"]),
		"-o", f"ConnectTimeout={TIEMPO_CONEXION}",
		"-o", "ServerAliveInterval=15",
		"-o", "ServerAliveCountMax=3",
		"-o", "LogLevel=ERROR",
		"-p", str(equipo.puerto),
	]


def guion_remoto(equipo: EquipoSSH, agente: str, argumentos: Iterable[str], carpeta: str = "",
		quitar: Iterable[str] = (), anadir: dict[str, str] | None = None, programa: str | None = None) -> str:
	"""El guion que ejecuta el shell del otro equipo, con cada parte entre comillas."""
	partes = [f'export PATH="{PATH_EXTRA}:$PATH";']
	if carpeta:
		partes.append(f"cd {_citar_carpeta(carpeta)} &&")
	partes.append("exec env")
	for nombre in dict.fromkeys(quitar):
		if _RE_VARIABLE.match(nombre):
			partes += ["-u", nombre]
	for nombre, valor in {**equipo.variables, **(anadir or {})}.items():
		if _RE_VARIABLE.match(nombre):
			partes.append(shlex.quote(f"{nombre}={valor}"))
	partes.append(shlex.quote(equipo.rutas.get(agente) or programa or equipo.programa(agente)))
	partes += [shlex.quote(str(argumento)) for argumento in argumentos]
	return " ".join(partes)


def orden_remota(equipo: EquipoSSH, agente: str, argumentos: Iterable[str], carpeta: str = "",
		quitar: Iterable[str] = (), anadir: dict[str, str] | None = None, terminal: bool = False,
		programa: str | None = None) -> list[str]:
	"""La orden de ``ssh.exe`` que arranca ``agente`` en ``carpeta`` del otro equipo.

	Se usa el shell de inicio de sesión del otro equipo (``$SHELL -l``), como la
	app oficial de Codex, y además se añaden las carpetas habituales de los
	agentes al PATH: por SSH, zsh no lee ~/.zshrc.
	"""
	ssh = buscar_ssh()
	if ssh is None:
		raise ErrorRemoto("No se encuentra ssh.exe. Instala «Cliente OpenSSH» en Configuración de Windows, "
			"Sistema, Características opcionales.")
	error = validar_equipo(equipo)
	if error is not None:
		raise ErrorRemoto(error)
	guion = guion_remoto(equipo, agente, argumentos, carpeta, quitar, anadir, programa)
	return [str(ssh), *opciones_ssh(equipo, terminal), "--", equipo.destino, 'exec "$SHELL" -l -c ' + shlex.quote(guion)]


def entorno_ssh() -> dict[str, str]:
	"""Entorno de ``ssh.exe`` en Windows (sin claves de API, que no le hacen falta)."""
	return procesos.entorno_hijo(
		quitar=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
	)


# ── Comprobar un equipo ──────────────────────────────────────────────────────


@dataclass
class Resultado:
	"""Resultado de una prueba de «Comprobar el equipo»."""

	nombre: str
	correcto: bool
	detalle: str


def _ejecutar(orden: list[str], tiempo: float) -> tuple[int, str, str]:
	banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0)
	try:
		resultado = subprocess.run(  # noqa: S603 - orden construida por la aplicación, sin shell
			orden, capture_output=True, timeout=tiempo, env=entorno_ssh(), creationflags=banderas, check=False,
			stdin=subprocess.DEVNULL,
		)
	except subprocess.TimeoutExpired:
		return -1, "", "tiempo agotado"
	except OSError as error:
		return -1, "", str(error)
	salida = resultado.stdout.decode("utf-8", "replace")
	errores = resultado.stderr.decode("utf-8", "replace")
	return resultado.returncode, salida, errores


def explicar_error_ssh(errores: str) -> str:
	"""Traduce los errores habituales de SSH a una explicación con la solución."""
	texto = errores.lower()
	if "permission denied" in texto:
		return ("El otro equipo no acepta tu clave SSH. Crea una clave con «ssh-keygen» y copia el contenido de "
			"tu archivo .ssh\\id_ed25519.pub al final de ~/.ssh/authorized_keys del otro equipo. Si la clave "
			"tiene contraseña, añádela al agente de claves de Windows con «ssh-add».")
	if "host key verification failed" in texto or "no matching host key" in texto:
		return ("Es la primera conexión (o la huella del equipo ha cambiado). Abre una terminal, escribe "
			"«ssh destino» y acepta la huella escribiendo «yes». Ctrl+IA no la acepta por su cuenta.")
	if "could not resolve hostname" in texto or "name or service not known" in texto:
		return "No se encuentra el equipo. Revisa el nombre o la dirección IP del destino."
	if "connection refused" in texto:
		return ("El otro equipo rechaza la conexión. En el Mac, activa «Sesión remota» en Ajustes del Sistema, "
			"General, Compartir; en Linux, instala y arranca el servidor OpenSSH.")
	if "timed out" in texto or "tiempo agotado" in texto:
		return "El otro equipo no responde. Comprueba que está encendido y en la misma red (o el puerto)."
	return "No se pudo conectar: " + (errores.strip().splitlines()[-1] if errores.strip() else "error desconocido")


def comprobar(equipo: EquipoSSH, agentes: Iterable[str] = ("claude", "codex", "gemini", "antigravity", "ollama")) -> list[Resultado]:
	"""Prueba la conexión, qué agentes encuentra con su versión y si tienen la sesión iniciada."""
	try:
		orden = orden_remota(equipo, "sh", ["-c", "echo ctrlia-conexion-ok"])
	except ErrorRemoto as error:
		return [Resultado("Conexión", False, str(error))]
	codigo, salida, errores = _ejecutar(orden, TIEMPO_COMPROBACION)
	if "ctrlia-conexion-ok" not in salida:
		return [Resultado("Conexión", False, explicar_error_ssh(errores or salida))]
	resultados = [Resultado("Conexión", True, f"Conectado con {equipo.destino} sin contraseña.")]
	lista = list(agentes)
	pruebas = []
	for agente in lista:
		programa = shlex.quote(equipo.programa(agente))
		pruebas.append(f"printf '@@%s\\n' {shlex.quote(agente)}; command -v {programa} && {programa} --version 2>&1 | head -n 1")
	codigo, salida, errores = _ejecutar(orden_remota(equipo, "sh", ["-c", "; ".join(pruebas)]), TIEMPO_COMPROBACION)
	bloques = {}
	for trozo in salida.split("@@")[1:]:
		nombre, _, resto = trozo.partition("\n")
		bloques[nombre.strip()] = [linea for linea in resto.strip().splitlines() if linea.strip()]
	encontrados = []
	for agente in lista:
		lineas = bloques.get(agente, [])
		nombre = _NOMBRES.get(agente, agente)
		if not lineas:
			resultados.append(Resultado(nombre, False,
				f"No se encuentra «{equipo.programa(agente)}» en el otro equipo. Si está instalado, escribe su ruta completa "
				"en el equipo (por ejemplo /Users/ana/.local/bin/claude) o añade su carpeta al PATH en ~/.zshenv."))
			continue
		version = lineas[1] if len(lineas) > 1 else ""
		encontrados.append(agente)
		resultados.append(Resultado(nombre, True, f"Encontrado en {lineas[0]}" + (f", versión {version}." if version else ".")))
	resultados.extend(_comprobar_sesiones(equipo, encontrados))
	return resultados


_NOMBRES = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "antigravity": "Antigravity CLI", "ollama": "Ollama"}


def _comprobar_sesiones(equipo: EquipoSSH, agentes: list[str]) -> list[Resultado]:
	resultados = []
	if "codex" in agentes:
		codigo, salida, errores = _ejecutar(orden_remota(equipo, "codex", ["login", "status"]), TIEMPO_COMPROBACION)
		if codigo == 0:
			resultados.append(Resultado("Sesión de Codex", True, "Codex tiene la sesión iniciada."))
		else:
			resultados.append(Resultado("Sesión de Codex", False,
				"Codex no tiene la sesión iniciada en el otro equipo. Pulsa «Abrir una consola en el equipo» y escribe "
				"«codex login --device-auth»: te da una dirección y un código para entrar desde el navegador de Windows. "
				"Conviene poner cli_auth_credentials_store = \"file\" en ~/.codex/config.toml del otro equipo."))
	if "claude" in agentes:
		argumentos = ["-p", "Responde solo con la palabra ok.", "--output-format", "json", "--max-turns", "1"]
		codigo, salida, errores = _ejecutar(orden_remota(equipo, "claude", argumentos,
			quitar=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")), 90)
		texto = (salida + errores).lower()
		if codigo == 0 and '"is_error":false' in texto.replace(" ", ""):
			resultados.append(Resultado("Sesión de Claude Code", True, "Claude Code tiene la sesión iniciada."))
		elif "not logged in" in texto or "login" in texto or "api key" in texto or "keychain" in texto:
			resultados.append(Resultado("Sesión de Claude Code", False, EXPLICACION_LLAVERO_CLAUDE))
		else:
			ultima = (errores or salida).strip().splitlines()[-1:] or ["sin respuesta"]
			resultados.append(Resultado("Sesión de Claude Code", False, "Claude Code no respondió bien: " + ultima[0][:300]))
	return resultados


EXPLICACION_LLAVERO_CLAUDE = (
	"Claude Code no tiene la sesión iniciada por SSH. En un Mac es lo normal: guarda la sesión en el llavero, que "
	"por SSH está bloqueado. Soluciones: en una terminal de Windows, «ssh -t destino claude» y dentro escribe /login "
	"(la sesión queda en ~/.claude/.credentials.json); o en el Mac, «claude setup-token» y pon el token en "
	"~/.zshenv como export CLAUDE_CODE_OAUTH_TOKEN=...; o desbloquea el llavero con «security unlock-keychain»."
)


# ── Túnel para Ollama (experimental) ─────────────────────────────────────────

#: Túneles abiertos: id del equipo → (proceso de ssh, puerto local).
_tuneles: dict[str, tuple[procesos.ProcesoLineas, int]] = {}


def _puerto_libre() -> int:
	import socket

	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as enchufe:
		enchufe.bind(("127.0.0.1", 0))
		return int(enchufe.getsockname()[1])


def equipo_ollama(opcion=None) -> EquipoSSH | None:
	"""El equipo marcado para usar su Ollama, o ``None``."""
	return next((e for e in equipos(opcion).values() if e.ollama), None)


def orden_tunel(equipo: EquipoSSH, puerto_local: int, puerto_remoto: int = 11434) -> list[str]:
	"""``ssh -N -L``: reenvía un puerto de Windows al Ollama del otro equipo, sin abrirlo a la red."""
	ssh = buscar_ssh()
	if ssh is None:
		raise ErrorRemoto("No se encuentra ssh.exe.")
	error = validar_equipo(equipo)
	if error is not None:
		raise ErrorRemoto(error)
	return [str(ssh), "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", f"ConnectTimeout={TIEMPO_CONEXION}",
		"-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR", "-p", str(equipo.puerto),
		"-L", f"127.0.0.1:{puerto_local}:127.0.0.1:{puerto_remoto}", "--", equipo.destino]


def direccion_ollama(opcion=None) -> str:
	"""Dirección local del túnel al Ollama del equipo marcado (lo abre si hace falta), o ``""``."""
	try:
		equipo = equipo_ollama(opcion)
	except Exception:  # noqa: BLE001 - sin configuración legible no hay túnel
		log.debug("No se pudieron leer los equipos SSH", exc_info=True)
		return ""
	if equipo is None:
		return ""
	existente = _tuneles.get(equipo.id)
	if existente is not None and existente[0].vivo:
		return f"http://127.0.0.1:{existente[1]}"
	try:
		puerto = _puerto_libre()
		proceso = procesos.ProcesoLineas(orden_tunel(equipo, puerto), lambda _linea: None, nombre="tunel-ollama",
			entorno=entorno_ssh())
		proceso.iniciar()
	except (ErrorRemoto, OSError):
		log.warning("No se pudo abrir el túnel SSH a Ollama en %s", equipo.nombre, exc_info=True)
		return ""
	_tuneles[equipo.id] = (proceso, puerto)
	import time

	time.sleep(1.5)  # el reenvío tarda un momento en estar listo
	if not proceso.vivo:
		# Se devuelve igualmente la dirección: mejor un error de conexión que usar en silencio otro Ollama.
		log.warning("El túnel SSH a Ollama en %s se cerró: %s", equipo.nombre, " | ".join(proceso.errores_recientes(5)))
		return f"http://127.0.0.1:{puerto}"
	log.info("Túnel SSH a Ollama abierto: %s → puerto local %d", equipo.nombre, puerto)
	return f"http://127.0.0.1:{puerto}"


def cerrar_tuneles() -> None:
	"""Cierra los túneles abiertos (al salir de Ctrl+IA; el Job Object lo haría igualmente)."""
	for proceso, _puerto in list(_tuneles.values()):
		proceso.terminar(espera=1)
	_tuneles.clear()


# ── Archivos del otro equipo ─────────────────────────────────────────────────


def _citar_ruta(ruta: str) -> str:
	return _citar_carpeta(ruta) if ruta == "~" or ruta.startswith("~/") else shlex.quote(ruta)


def leer_archivo(equipo: EquipoSSH, ruta: str) -> str | None:
	"""Contenido de un archivo de texto del otro equipo, o ``None`` si no existe. Lanza ``ErrorRemoto``."""
	guion = f"if [ -f {_citar_ruta(ruta)} ]; then cat {_citar_ruta(ruta)}; else echo ctrlia-no-existe >&2; exit 3; fi"
	codigo, salida, errores = _ejecutar(orden_remota(equipo, "sh", ["-c", guion]), TIEMPO_COMPROBACION)
	if codigo == 3 and "ctrlia-no-existe" in errores:
		return None
	if codigo != 0:
		raise ErrorRemoto(f"No se pudo leer {ruta} en «{equipo.nombre}»: {explicar_error_ssh(errores)}")
	return salida


def escribir_archivo(equipo: EquipoSSH, ruta: str, contenido: str) -> None:
	"""Escribe un archivo de texto en el otro equipo de forma atómica (temporal y ``mv``). Lanza ``ErrorRemoto``."""
	destino = _citar_ruta(ruta)
	temporal = _citar_ruta(ruta + ".ctrlia-tmp")
	guion = f'mkdir -p "$(dirname {destino})" && cat > {temporal} && mv -f {temporal} {destino}'
	orden = orden_remota(equipo, "sh", ["-c", guion])
	banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0)
	try:
		resultado = subprocess.run(  # noqa: S603 - orden construida por la aplicación, sin shell
			orden, input=contenido.encode("utf-8"), capture_output=True, timeout=TIEMPO_COMPROBACION,
			env=entorno_ssh(), creationflags=banderas, check=False,
		)
	except (subprocess.TimeoutExpired, OSError) as error:
		raise ErrorRemoto(f"No se pudo escribir {ruta} en «{equipo.nombre}»: {error}") from error
	if resultado.returncode != 0:
		raise ErrorRemoto(f"No se pudo escribir {ruta} en «{equipo.nombre}»: "
			+ explicar_error_ssh(resultado.stderr.decode("utf-8", "replace")))


def orden_consola(equipo: EquipoSSH, agente: str, argumentos: Iterable[str] = ()) -> list[str]:
	"""Orden para abrir el agente en una consola de Windows, con terminal (para iniciar sesión)."""
	return orden_remota(equipo, agente, argumentos, terminal=True)


def orden_terminal(equipo: EquipoSSH) -> list[str]:
	"""``ssh -t destino``: una consola normal en el otro equipo.

	Sin ``BatchMode``: ahí es la persona quien escribe, y puede aceptar la huella
	del equipo la primera vez o iniciar sesión en los agentes.
	"""
	ssh = buscar_ssh()
	if ssh is None:
		raise ErrorRemoto("No se encuentra ssh.exe. Instala «Cliente OpenSSH» en Configuración de Windows, "
			"Sistema, Características opcionales.")
	error = validar_equipo(equipo)
	if error is not None:
		raise ErrorRemoto(error)
	return [str(ssh), "-t", "-o", f"ConnectTimeout={TIEMPO_CONEXION}", "-p", str(equipo.puerto), "--", equipo.destino]


# ── Ayudas para la interfaz ──────────────────────────────────────────────────


def nuevo_id(nombre: str, existentes: Iterable[str] = ()) -> str:
	"""Identificador estable a partir del nombre («Mac del estudio» → «mac-del-estudio»)."""
	base = re.sub(r"[^a-z0-9]+", "-", nombre.casefold()).strip("-") or "equipo"
	usados = set(existentes)
	ident, numero = base, 2
	while ident in usados:
		ident, numero = f"{base}-{numero}", numero + 1
	return ident


def leer_variables(texto: str) -> dict[str, str]:
	"""Variables escritas una por línea como ``NOMBRE=valor``. Lanza ``ErrorRemoto`` si una está mal."""
	variables: dict[str, str] = {}
	for linea in texto.splitlines():
		linea = linea.strip()
		if not linea or linea.startswith("#"):
			continue
		nombre, igual, valor = linea.partition("=")
		nombre = nombre.strip().removeprefix("export ").strip()
		if not igual or not _RE_VARIABLE.match(nombre):
			raise ErrorRemoto(f"«{linea}» no es una variable válida. Escribe una por línea, como NOMBRE=valor.")
		variables[nombre] = valor.strip()
	return variables


def admite_agente(proveedor: Any) -> bool:
	"""``True`` si el agente se puede usar en otro equipo por SSH (Ollama va aparte, por túnel)."""
	ident = getattr(proveedor, "id", "")
	if ident in ("claude", "codex", "antigravity"):
		return True
	orden = getattr(proveedor, "orden_para_remoto", None)
	if not callable(orden):
		return False
	try:
		return bool(orden())
	except Exception:  # noqa: BLE001 - un agente raro no debe romper la lista
		log.debug("orden_para_remoto falló en %s", ident, exc_info=True)
		return False


def es_experimental(agente: str) -> bool:
	"""Los agentes que no son Claude Code ni Codex funcionan por SSH de forma experimental."""
	return agente not in ESTABLES
