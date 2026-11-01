"""Claude Code (Anthropic) mediante su CLI oficial ``claude``.

Cómo se conversa
================

Un proceso ``claude -p`` habla por stdin/stdout en *stream-json* (el mismo
protocolo que usa el Agent SDK):

1. Se envía ``control_request/initialize`` y se espera su respuesta (trae los
   comandos de barra y los modelos disponibles).
2. Se envía el mensaje del usuario como línea JSON (nunca en la línea de
   órdenes: así un texto con comillas o ``&`` no se puede reinterpretar).
3. Se leen los eventos hasta que el CLI declara la sesión inactiva
   (``session_state_changed`` → ``idle``) sin trabajo en segundo plano: ahí
   termina el turno.

El proceso sigue abierto para el mensaje siguiente («Mantener Claude Code
abierto entre mensajes», Preferencias, Agentes): así los servidores MCP y los
conectores no se vuelven a conectar en cada mensaje y la respuesta empieza
antes. Se cierra tras unos minutos sin uso, y se relanza entre dos turnos si
cambia algo que va en la línea de órdenes (modelo, esfuerzo, reglas quitadas,
carpetas, variables): eso es la «firma» del proceso. El modo de permisos se
cambia en caliente. Con la opción en «No», cada turno es un proceso nuevo y al
terminar se cierra stdin, como antes. Si Claude Code se pone a trabajar por su
cuenta entre mensajes (una tarea en segundo plano que acaba), sus eventos abren
un turno «espontáneo».

El primer proceso usa ``--session-id <uuid>`` y los siguientes ``--resume``, de
modo que la conversación queda guardada en el historial de Claude Code y se
puede abrir también desde la terminal o la app de escritorio. Un mensaje que no
se pudo escribir porque el proceso acababa de morir no se reenvía nunca.

Permisos
========

Con ``--permission-prompt-tool stdio`` cada acción que necesita permiso llega
como ``control_request/can_use_tool``; se convierte en una
:class:`SolicitudPermiso` y la respuesta del usuario vuelve como
``control_response``. Lo concedido «durante esta sesión» se guarda en
``ajustes.reglas_permitidas`` y se pasa como ``--allowedTools`` en cada turno
(Ctrl+IA nunca escribe en los archivos de configuración de Claude).

«Permitir siempre en esta carpeta» devuelve la sugerencia de Claude Code con su
destino (``localSettings``): es Claude Code quien la apunta en
``.claude/settings.local.json``, igual que su «Sí, y no volver a preguntar» de
la terminal, y vale para todas las conversaciones de esa carpeta. Con las
herramientas MCP se ofrece además permitir el servidor entero (regla
``mcp__servidor``), en la sesión o siempre.

Claude Code pregunta a la vez por cada llamada en paralelo, aunque sea la misma
herramienta, y no retira las demás preguntas cuando una respuesta añade una
regla. Por eso, al conceder una regla, las peticiones pendientes que esa regla
cubre se permiten con ella (la decisión ya es de la persona) y se retiran.

Herramientas de Ctrl+IA y deshacer
==================================

Con «Dar a los agentes las herramientas de Ctrl+IA» (Preferencias, Agentes), el
``initialize`` declara el servidor MCP «en proceso» ``ctrlia``
(``sdkMcpServers``): Claude Code le habla con ``control_request`` ``mcp_message``
por esta misma conexión (también por SSH) y lo atiende
``herramientas_propias``; sus herramientas van en ``--allowedTools`` porque son
inofensivas. Esas peticiones nunca abren un turno. Junto a él va
``ccd_session_mgmt`` (``herramientas_sesiones``), el servidor de la app de
escritorio al que llaman comandos como ``/wrap``.

Cada mensaje lleva su ``uuid`` (``ultimo_uuid_usuario``) y Claude Code guarda el
estado de los archivos antes de cada uno (``CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING``):
``deshacer_archivos`` pide ``rewind_files`` al proceso abierto o a uno nuevo con
``--resume`` que solo hace eso.

Añadir al turno
===============

Va sin ``interrupt``, por la entrada del proceso abierto. Con
``--replay-user-messages`` Claude Code devuelve cada mensaje, con su ``uuid`` y
``isReplay``, en el momento en que lo recoge (comprobado con 2.1.308): a mitad de
turno, tras la herramienta en la que estaba; si lo pilló escribiendo la respuesta
final, después de ese resultado, y entonces lo responde aparte con otro resultado.
Si el proceso se cierra sin ese eco, el mensaje queda sin confirmar: nunca se
dice que no se envió, porque ya estaba escrito.

Facturación
===========

Ctrl+IA usa la suscripción del usuario. Se borran del entorno del hijo las
variables que harían cobrar por uso (``ANTHROPIC_API_KEY``...), y si aun así el
CLI informa de que usa una clave de API (``apiKeySource`` distinto de
``none``), el turno se aborta antes de enviar nada, salvo que el usuario lo
haya permitido en Preferencias.
"""

from __future__ import annotations

import base64
import json
import logging
import ntpath
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.agentes import elicitacion, procesos, remoto
from app.services.agentes.base import (
	NOTA_SIN_CONFIRMAR,
	Adjunto,
	AjustesSesion,
	Capacidades,
	ClaseOpcion,
	Conversacion,
	Estado,
	EstadoProveedor,
	OpcionLista,
	OpcionPermiso,
	Pregunta,
	Proveedor,
	ReceptorEventos,
	RespuestaPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)

log = logging.getLogger(__name__)

#: Variables que inyecta una sesión de Claude Code en sus hijos: si Ctrl+IA se
#: abrió desde una terminal de Claude, el agente creería ser un subagente.
VARIABLES_DE_SESION = (
	"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION",
	"CLAUDE_PREVIEW_CLASSIFIER_FLOOR", "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_STARTUP_FAILURE_RESULTS",
	"CLAUDE_AGENT_SDK_MCP_NO_PREFIX",
)
PREFIJOS_DE_SESION = (
	"CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_",
	"CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SDK_",
	"CLAUDE_CODE_OAUTH_", "CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_ORGANIZATION_UUID",
	"CLAUDE_CODE_USER_EMAIL", "CLAUDE_CODE_MESSAGING_", "CLAUDE_CODE_DESKTOP_APP_VERSION",
	"CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES",
	"CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING",
	"CLAUDE_CODE_REPORT_FINDINGS", "CLAUDE_CODE_EAGER_FLUSH", "CLAUDE_CODE_DISABLE_CRON",
	"CLAUDE_CODE_DISABLE_TERMINAL_TITLE", "CLAUDE_CODE_TERMINAL_MCP_TOOLS",
)
#: Variables que harían que Claude Code cobrase por uso en lugar de la suscripción.
VARIABLES_DE_FACTURACION = (
	"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
	"ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID",
	"ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_FOUNDRY_RESOURCE",
	"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
)

_RE_MODELO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,99}$")
#: Nombre de un estilo de respuesta de Claude Code («Concise», o uno propio de ~/.claude/output-styles).
_RE_ESTILO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$")
#: Estilos que trae Claude Code 2.1.238 (``available_output_styles`` de ``initialize``), en español.
ESTILOS_CONOCIDOS = {
	"Concise": ("Conciso", "Respuestas breves y directas, sin explicaciones de más."),
	"Proactive": ("Proactivo", "Claude se adelanta: propone y hace los siguientes pasos que ve claros."),
	"Explanatory": ("Explicativo", "Claude explica por qué hace cada cosa y lo que va aprendiendo del proyecto."),
	"Learning": ("Aprendizaje", "Claude te enseña mientras trabaja y te deja partes para que las hagas tú."),
}
_RE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
#: Los nombres de las herramientas MCP llegan a 128 caracteres («mcp__servidor__herramienta»).
_RE_REGLA = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,199}(\([^\r\n]{0,500}\))?$")
#: «Claude Code 2.1.238 does not support this model; version 2.1.280 or newer is required» (la API, con 2.1.238).
_RE_VERSION_INSUFICIENTE = re.compile(r"Claude Code (\d[\w.-]*) does not support this model; version (\d[\w.-]*) or newer")
#: «No conversation found with session ID: <id>»: ``--resume`` de una sesión cuya transcripción ya no existe
#: (Claude Code 2.1.308: va en ``errors`` del ``result`` y en su salida de errores, y sale con código 1).
_RE_SIN_TRANSCRIPCION = re.compile(r"No conversation found", re.IGNORECASE)
#: El ``ERROR`` de ese caso lleva ``sin_transcripcion``: la interfaz ofrece ``GestorSesiones.seguir_con_contexto``.
TEXTO_SIN_TRANSCRIPCION = (
	"Claude Code ya no tiene la conversación de esta sesión: borra las antiguas pasado un tiempo (cleanupPeriodDays, 30 "
	"días de fábrica). Tus mensajes siguen en Ctrl+IA, pero el agente no los recuerda y no ha recibido el último."
)

#: Nombre interno de las peticiones de los servidores MCP en ``_solicitudes``.
HERRAMIENTA_ELICITACION = "__elicitacion_mcp__"
#: Herramientas cuyas peticiones no son permisos (preguntas y planes): nunca se responden con una regla.
HERRAMIENTAS_SIN_REGLA = frozenset({HERRAMIENTA_ELICITACION, "AskUserQuestion", "ExitPlanMode"})
#: Destinos de una sugerencia de Claude Code que él mismo guarda en un archivo (no solo en la sesión).
DESTINOS_PERSISTENTES = frozenset({"localSettings", "projectSettings", "userSettings"})
#: Opciones de una petición que conceden reglas (y con las que se resuelven las peticiones iguales pendientes).
OPCIONES_CON_REGLA = frozenset({"permitir_sesion", "permitir_servidor_sesion", "permitir_siempre", "permitir_servidor_siempre"})
#: Carpeta conservada con «Permitir durante esta sesión» (AIChat #334): va en ``reglas_permitidas``
#: como ``Carpeta(ruta)``, igual que las reglas (se ve y se quita en «Permisos de la sesión»), y a
#: Claude Code como ``--add-dir``, no como regla.
PREFIJO_CARPETA = "Carpeta("
#: Caracteres con los que una orden deja de ser «la misma» que la de una regla con prefijo (``Bash(git:*)``).
_CARACTERES_DE_SHELL = frozenset(";&|`$<>\n\r(){}")
#: Tamaño máximo de una imagen que se envía dentro del mensaje.
MAX_IMAGEN = 5 * 1024 * 1024
#: Espera máxima a la respuesta de ``initialize`` antes de enviar el mensaje.
ESPERA_INICIALIZAR = 10.0
#: Minutos que ``claude -p`` sigue abierto sin uso entre mensajes; 0 = un proceso por mensaje.
MINUTOS_ABIERTO_POR_DEFECTO = 15
#: Segundos de un minuto del tiempo sin uso (las pruebas lo acortan).
SEGUNDOS_POR_MINUTO = 60.0
#: Segundos que se espera a que un proceso retirado (cambio de ajustes, sin uso) termine solo.
ESPERA_RETIRAR = 5.0
#: Margen tras la hora de una comprobación programada antes de poder cerrar el proceso (AIChat #233).
MARGEN_PROGRAMADO = 120.0
#: Si tras el resultado nunca llega «inactiva», se da el turno por terminado.
ESPERA_TRAS_RESULTADO = 600.0
#: Tras pedir que se detenga, cuánto se espera antes de matar el proceso.
ESPERA_DETENER = 5.0

DENEGACION_POR_DEFECTO = "El usuario lo ha rechazado. No lo intentes de otra forma sin preguntar."

#: Cómo se describe cada herramienta conocida (para el diálogo y la voz).
NOMBRES_HERRAMIENTAS = {
	"Bash": "Ejecutar orden",
	"PowerShell": "Ejecutar orden de PowerShell",
	"Read": "Leer archivo",
	"Write": "Escribir archivo",
	"Edit": "Editar archivo",
	"MultiEdit": "Editar archivo",
	"NotebookEdit": "Editar cuaderno",
	"Glob": "Buscar archivos",
	"Grep": "Buscar texto",
	"WebFetch": "Leer página web",
	"WebSearch": "Buscar en internet",
	"Task": "Lanzar subagente",
	"Agent": "Lanzar subagente",
	"TodoWrite": "Actualizar la lista de tareas",
	"TaskCreate": "Crear tarea",
	"TaskUpdate": "Actualizar tarea",
	"ReportFindings": "Informar de los hallazgos de la revisión",
	"AskUserQuestion": "Preguntar al usuario",
	"ExitPlanMode": "Presentar el plan",
}


def describir_herramienta(nombre: str, entrada: dict[str, Any] | None) -> str:
	"""Frase corta y legible: «Ejecutar orden: git status»."""
	entrada = entrada or {}
	titulo = NOMBRES_HERRAMIENTAS.get(nombre)
	if titulo is None:
		titulo = nombre.replace("mcp__", "MCP ").replace("__", ": ") if nombre.startswith("mcp__") else f"Herramienta {nombre}"
	for clave in ("command", "file_path", "path", "notebook_path", "pattern", "url", "query", "subject", "description", "prompt"):
		valor = entrada.get(clave)
		if isinstance(valor, str) and valor.strip():
			valor = " ".join(valor.split())
			return f"{titulo}: {valor[:300]}"
	return titulo


def _detalle_entrada(entrada: dict[str, Any]) -> str:
	"""Entrada completa de la herramienta en texto legible (para el diálogo de permiso)."""
	lineas = []
	for clave, valor in entrada.items():
		if isinstance(valor, str):
			lineas.append(f"{clave}:\n{valor}" if "\n" in valor else f"{clave}: {valor}")
		else:
			lineas.append(f"{clave}: {json.dumps(valor, ensure_ascii=False, indent=2)}")
	return "\n".join(lineas)


#: Segundos que se espera a ``claude -p /usage``.
TIEMPO_USO = 60.0

#: Da a Claude su lista de tareas (TaskCreate y TaskUpdate) con todos los modelos.
#: Sin ella, Claude Code solo la ofrece con algunos; la app de escritorio la tiene
#: siempre. Documentada en las variables de entorno de Claude Code (2.1.233 o posterior).
VARIABLE_LISTA_TAREAS = "CLAUDE_CODE_ENABLE_TODO_TOOLS"
#: Con «0», ``claude -p`` conecta sus servidores MCP (y los conectores de claude.ai) ANTES de
#: empezar el turno. De fábrica los conecta en segundo plano: cada proceso nuevo empezaba con los
#: MCP «desconectados» o «aún conectándose», Claude Code lo anotaba en la conversación («The
#: following MCP servers have disconnected...») y el modelo lo contaba. Comprobado en las cadenas
#: de Claude Code 2.1.238 (``!vf(MCP_CONNECTION_NONBLOCKING)``).
VARIABLE_MCP_SIN_ESPERA = "MCP_CONNECTION_NONBLOCKING"
#: Esa espera tiene un tope de 5 s (``MCP_CONNECT_TIMEOUT_MS``) y, con ``--input-format stream-json``,
#: Claude Code no espera nada más: lo que tarda más (lo normal con ``npx``, ``uvx`` o Docker) empezaba
#: «pending» y fuera del turno (comprobado con 2.1.308 y servidores de prueba lentos). Las tres
#: variables valen el tiempo de Preferencias: sin ``MCP_TIMEOUT`` el servidor se daría por fallido a
#: los 30 s y sin ``CLAUDE_CODE_MCP_STARTUP_WAIT_MS`` (2.1.274) el primer turno solo espera 2 s.
VARIABLES_ESPERA_MCP = ("MCP_TIMEOUT", "MCP_CONNECT_TIMEOUT_MS", "CLAUDE_CODE_MCP_STARTUP_WAIT_MS")
#: Servidores stdio que se conectan a la vez: de fábrica 3, así que el cuarto empieza cuando acaba uno
#: de los tres primeros. 8 es lo que usa la app de escritorio de Claude.
VARIABLE_MCP_EN_PARALELO = "MCP_SERVER_CONNECTION_BATCH_SIZE"
MCP_EN_PARALELO = "8"
#: Segundos de fábrica de esa espera (Preferencias, Agentes).
SEGUNDOS_ESPERA_MCP = 30
#: Segundos sin contestar a ``initialize`` tras los que se avisa de que se están conectando los MCP.
ESPERA_AVISO_MCP = 4.0
#: Segundos que se espera a que Claude Code conteste a ``mcp_status`` y ``mcp_toggle``.
ESPERA_ESTADO_MCP = 10.0
#: Segundos entre comprobaciones mientras algún servidor sigue conectándose.
PAUSA_REVISAR_MCP = 3.0
#: Para las consultas que no conversan (``/usage``, la lista de comandos): sin ``--mcp-config``,
#: ``--strict-mcp-config`` no carga ningún servidor MCP ni conector. Si no, cada consulta arrancaba
#: todos los servidores de la persona (y los que abren un navegador o piden iniciar sesión, lo hacían).
SIN_SERVIDORES_MCP = ("--strict-mcp-config",)
#: Claude Code guarda el estado de los archivos antes de cada mensaje (``rewind_files``; comprobado en 2.1.238).
#: Solo cubre lo que cambian Write, Edit y NotebookEdit, no lo que hacen las órdenes.
VARIABLE_PUNTOS_ARCHIVOS = "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"
#: Segundos que se espera la respuesta a «rewind_files» (y a ``initialize`` en un proceso nuevo para ello).
ESPERA_DESHACER = 60.0
_RE_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: Lo que Claude Code contesta a una notificación MCP del servidor «en proceso» (no esperan respuesta).
_ACUSE_MCP = {"jsonrpc": "2.0", "result": {}, "id": 0}


def variables_anadidas(lista_tareas: bool = False, esperar_mcp: bool = False,
		segundos_mcp: int = SEGUNDOS_ESPERA_MCP) -> dict[str, str]:
	"""Variables que Ctrl+IA pone a Claude Code, aquí o en otro equipo por SSH.

	Sin lista de tareas no se toca ``VARIABLE_LISTA_TAREAS``: si la persona la
	tiene puesta por su cuenta, sigue valiendo. Lo mismo con los tiempos de los
	servidores MCP: si ya tiene uno en su entorno, manda el suyo. La excepción es
	``VARIABLE_MCP_SIN_ESPERA``, que con la opción activada siempre vale «0»: lo
	que se abre desde la app de escritorio de Claude hereda su ``true``, y la
	preferencia ya sirve para no esperar.
	"""
	# Con la segunda, Claude Code guarda el estado de los archivos antes de cada mensaje y admite
	# «rewind_files» (deshacer los cambios de un mensaje: ``ConversacionClaude.deshacer_archivos``).
	variables = {"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1", VARIABLE_PUNTOS_ARCHIVOS: "true"}
	if lista_tareas:
		variables[VARIABLE_LISTA_TAREAS] = "1"
	if esperar_mcp:
		variables[VARIABLE_MCP_SIN_ESPERA] = "0"
		for nombre in VARIABLES_ESPERA_MCP:
			if nombre not in os.environ:
				variables[nombre] = str(int(segundos_mcp) * 1000)
		if VARIABLE_MCP_EN_PARALELO not in os.environ:
			variables[VARIABLE_MCP_EN_PARALELO] = MCP_EN_PARALELO
	return variables


def entorno_claude(lista_tareas: bool = False, esperar_mcp: bool = False,
		segundos_mcp: int = SEGUNDOS_ESPERA_MCP) -> dict[str, str]:
	return procesos.entorno_hijo(
		quitar=VARIABLES_DE_SESION + VARIABLES_DE_FACTURACION,
		prefijos=PREFIJOS_DE_SESION,
		anadir=variables_anadidas(lista_tareas, esperar_mcp, segundos_mcp),
	)


def buscar_claude() -> Path | None:
	candidatos = [Path.home() / ".local" / "bin" / "claude.exe"]
	local = os.environ.get("LOCALAPPDATA")  # auditoria: ignorar ruta-no-portable - localiza Claude Code instalado; no guarda datos
	if local:
		candidatos.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "claude.exe")
	return procesos.buscar_ejecutable(["claude"], candidatos)


class ProveedorClaude(Proveedor):
	"""Claude Code con la suscripción del usuario."""

	id = "claude"
	nombre = "Claude Code (Anthropic)"
	descripcion = (
		"El agente de programación de Anthropic. Lee y modifica el proyecto, ejecuta órdenes "
		"y te pide permiso antes de cada acción delicada. Usa tu suscripción de Claude."
	)
	web = "https://code.claude.com/docs/es/overview"
	capacidades = Capacidades(
		adjuntar_imagenes=True,
		adjuntar_archivos=True,
		bifurcar=True,
		permisos_interactivos=True,
		cambiar_modelo_en_marcha=True,
		comandos_barra=True,
		enviar_ahora=True,
		# Comprobado con Claude Code 2.1.238 (AIChat #238): un mensaje escrito en la entrada a mitad
		# de turno, sin «interrupt», lo recoge en ese mismo turno, que acaba con un solo resultado.
		anadir_al_turno=True,
		# Las skills del catálogo van como complemento propio de la sesión (--plugin-dir).
		skills_nativas=True,
		leer_historial_externo=True,
	)

	def __init__(self, ejecutable: Path | None = None, orden_base: Sequence[str] | None = None,
			permitir_clave_api: bool = False, lista_tareas: bool | Callable[[], bool] = True,
			esperar_mcp: bool | Callable[[], bool] = True,
			minutos_abierto: int | Callable[[], int] = MINUTOS_ABIERTO_POR_DEFECTO,
			chrome: bool | Callable[[], bool] = False,
			segundos_mcp: int | Callable[[], int] = SEGUNDOS_ESPERA_MCP) -> None:
		self._ejecutable = ejecutable
		#: Orden fija para las pruebas (``[python, fake_claude.py]``).
		self._orden_base = list(orden_base) if orden_base else None
		self.permitir_clave_api = permitir_clave_api
		#: Preferencias, «Dejar que Claude lleve una lista de tareas» (una función: se lee en cada turno).
		self._lista_tareas = lista_tareas
		#: Preferencias, «Esperar a que se conecten las herramientas MCP de Claude» (se lee en cada turno).
		self._esperar_mcp = esperar_mcp
		#: Preferencias, «Tiempo de espera de las herramientas MCP de Claude» (segundos; se lee en cada turno).
		self._segundos_mcp = segundos_mcp
		#: Preferencias, «Mantener Claude Code abierto entre mensajes» (minutos; se lee en cada turno).
		self._minutos_abierto = minutos_abierto
		#: Preferencias, «Dejar que Claude use Chrome» (``--chrome``; se lee en cada turno).
		self._chrome = chrome
		self._comandos: list[tuple[str, str]] = []
		#: Estilos de respuesta que dio Claude Code en su último ``initialize`` (vacío: los conocidos).
		self._estilos: list[str] = []

	def usar_chrome(self) -> bool:
		"""Si Claude Code arranca con «Claude en Chrome» (AIChat #319)."""
		try:
			return bool(self._chrome() if callable(self._chrome) else self._chrome)
		except Exception:  # noqa: BLE001 - una preferencia ilegible la deja desactivada, como de fábrica
			log.debug("No se pudo leer la preferencia de Claude en Chrome", exc_info=True)
			return False

	def usar_lista_tareas(self) -> bool:
		"""Si las sesiones de Ctrl+IA llevan la lista de tareas de Claude."""
		try:
			return bool(self._lista_tareas() if callable(self._lista_tareas) else self._lista_tareas)
		except Exception:  # noqa: BLE001 - una preferencia ilegible deja la lista activada, como de fábrica
			log.debug("No se pudo leer la preferencia de la lista de tareas", exc_info=True)
			return True

	def esperar_mcp(self) -> bool:
		"""Si cada turno espera a que Claude Code conecte sus servidores MCP."""
		try:
			return bool(self._esperar_mcp() if callable(self._esperar_mcp) else self._esperar_mcp)
		except Exception:  # noqa: BLE001 - una preferencia ilegible deja la espera activada, como de fábrica
			log.debug("No se pudo leer la preferencia de los servidores MCP", exc_info=True)
			return True

	def segundos_mcp(self) -> int:
		"""Segundos que Claude Code espera a cada servidor MCP al arrancar."""
		try:
			valor = self._segundos_mcp() if callable(self._segundos_mcp) else self._segundos_mcp
			return max(1, int(valor))
		except Exception:  # noqa: BLE001 - una preferencia ilegible deja el valor de fábrica
			log.debug("No se pudo leer el tiempo de espera de los servidores MCP", exc_info=True)
			return SEGUNDOS_ESPERA_MCP

	def minutos_abierto(self) -> int:
		"""Minutos que ``claude -p`` sigue abierto sin uso entre mensajes (0: un proceso por mensaje)."""
		try:
			valor = self._minutos_abierto() if callable(self._minutos_abierto) else self._minutos_abierto
			return max(0, int(valor))
		except Exception:  # noqa: BLE001 - una preferencia ilegible deja el valor de fábrica
			log.debug("No se pudo leer cuánto tiempo mantener abierto Claude Code", exc_info=True)
			return MINUTOS_ABIERTO_POR_DEFECTO

	def leer_limites(self) -> Any:
		"""Límite de 5 horas y semanales con ``claude -p /usage``: Claude Code lo contesta sin llamar al modelo.

		Se usa el ``usage_report`` si llega; si no, el texto del propio Claude.
		Si la respuesta no fuera la del comando (gastaría cuota), se descarta.
		"""
		from app.services import limites

		orden = self.orden_base()
		if not orden:
			return None
		try:
			_codigo, salida, errores = procesos.ejecutar_y_leer(
				[*orden, "-p", "/usage", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
					*SIN_SERVIDORES_MCP],
				TIEMPO_USO, entorno_claude())
		except (OSError, subprocess.SubprocessError) as error:
			raise limites.ErrorLimites("Claude Code no ha contestado a tiempo.") from error
		mensajes = []
		for linea in salida.splitlines():
			try:
				dato = json.loads(linea)
			except ValueError:
				continue
			if isinstance(dato, dict):
				mensajes.append(dato)
		resultado = next((m for m in mensajes if m.get("type") == "result"), {})
		if resultado.get("num_turns") or resultado.get("total_cost_usd"):
			log.warning("claude -p /usage no se resolvió como comando: num_turns=%s", resultado.get("num_turns"))
		informe = limites.desde_claude(limites.buscar_clave(mensajes, "usage_report"))
		if informe is not None:
			return informe
		texto = "\n".join(
			str(bloque.get("text") or "")
			for m in mensajes if m.get("type") == "assistant"
			for bloque in ((m.get("message") or {}).get("content") or []) if isinstance(bloque, dict)
		).strip()
		traducido = limites.desde_texto_claude(texto) if texto else None
		if traducido is not None:
			return traducido
		if not texto or texto.lower().startswith("total cost"):
			log.info("claude -p /usage sin informe de uso: %s", (errores or texto)[:300])
			raise limites.ErrorLimites("Claude Code no ha dado tus límites: suele ser porque no ha iniciado sesión con tu "
				"suscripción de Claude. Inicia sesión en Herramientas, Agentes instalados.")
		return limites.LimitesCuenta([], nota="Claude Code dice (en inglés): " + " ".join(texto.split()))

	def estilos(self) -> list[OpcionLista]:
		"""«Predeterminado» y los estilos de respuesta de Claude Code (AIChat #235)."""
		nombres = [e for e in (self._estilos or list(ESTILOS_CONOCIDOS)) if e and e.lower() != "default"]
		opciones = [OpcionLista("", "Predeterminado de Claude Code", "El estilo de tu configuración de Claude Code.")]
		for nombre in nombres:
			etiqueta, ayuda = ESTILOS_CONOCIDOS.get(nombre, (nombre, "Un estilo propio de tu configuración de Claude Code."))
			opciones.append(OpcionLista(nombre, etiqueta, ayuda))
		return opciones

	def orden_base(self) -> list[str] | None:
		if self._orden_base:
			return list(self._orden_base)
		ruta = self._ejecutable or buscar_claude()
		return [str(ruta)] if ruta else None

	def detectar(self) -> EstadoProveedor:
		orden = self.orden_base()
		if not orden:
			from app.services.agentes import instalacion

			# Solo con npm (claude.cmd, la instalación antigua) no se puede usar: se explica qué hacer (AIChat #309).
			return EstadoProveedor(
				False,
				mensaje=instalacion.aviso_claude_npm(lambda: None)
					or "Claude Code no está instalado. Puedes instalarlo desde «Agentes instalados».",
			)
		version = procesos.version_de([*orden, "--version"])
		estado = EstadoProveedor(True, ruta=orden[-1], version=version)
		try:
			codigo, salida, _error = procesos.ejecutar_y_leer([*orden, "auth", "status", "--json"], 20, entorno_claude())
			datos = _primer_json(salida, "loggedIn")
			if datos is not None:
				estado.sesion_iniciada = bool(datos.get("loggedIn"))
				estado.cuenta = str(datos.get("email") or datos.get("subscriptionType") or "")
				metodo = str(datos.get("authMethod") or "")
				if estado.sesion_iniciada and metodo and metodo not in ("claude.ai", "oauth_token"):
					estado.mensaje = "La sesión no usa una suscripción de Claude; podría cobrarse por uso."
			elif codigo != 0:
				log.debug("auth status devolvió %s", codigo)
		except (OSError, procesos.subprocess.SubprocessError):
			log.debug("No se pudo consultar el inicio de sesión de Claude", exc_info=True)
		if estado.sesion_iniciada is False:
			estado.mensaje = "Claude Code está instalado pero no has iniciado sesión."
		return estado

	def crear_conversacion(self, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> ConversacionClaude:
		return ConversacionClaude(self, ajustes, al_evento)

	def modelos(self) -> list[OpcionLista]:
		return [
			OpcionLista("", "Predeterminado de Claude Code"),
			OpcionLista("opus", "Opus (el más capaz)"),
			OpcionLista("sonnet", "Sonnet (equilibrado)"),
			OpcionLista("haiku", "Haiku (el más rápido)"),
			OpcionLista("fable", "Fable", "Modelo de máxima capacidad; puede consumir créditos adicionales."),
		]

	def esfuerzos(self) -> list[OpcionLista]:
		return [
			OpcionLista("", "Predeterminado"),
			OpcionLista("low", "Bajo"),
			OpcionLista("medium", "Medio"),
			OpcionLista("high", "Alto"),
			OpcionLista("xhigh", "Muy alto"),
			OpcionLista("max", "Máximo"),
		]

	def modos_permiso(self) -> list[OpcionLista]:
		return [
			OpcionLista("default", "Preguntar antes de cada acción",
				"Claude te pide permiso antes de modificar archivos o ejecutar órdenes."),
			OpcionLista("acceptEdits", "Aceptar las ediciones de archivos",
				"Claude modifica archivos del proyecto sin preguntar, pero sigue pidiendo permiso para ejecutar órdenes."),
			OpcionLista("plan", "Solo planificar",
				"Claude investiga y te propone un plan sin cambiar nada. Tú decides si lo lleva a cabo."),
			OpcionLista("auto", "Automático",
				"Claude decide por sí mismo qué acciones son seguras y solo pregunta en las dudosas."),
			OpcionLista("dontAsk", "No preguntar: denegar lo dudoso",
				"Claude solo hace lo que no necesita permiso y lo que permiten tus reglas; lo que tendría que preguntarte lo deniega sin preguntar."),
			OpcionLista("bypassPermissions", "Sin preguntas (peligroso)",
				"Claude hace todo sin pedir permiso, incluido borrar archivos o ejecutar cualquier orden. Úsalo solo en proyectos de prueba."),
		]

	def modo_predeterminado(self, carpeta: Path | None = None) -> str:
		"""El «Modo de permisos inicial» de los settings.json de Claude Code; si no hay, «Preguntar»."""
		from app.services.config_agentes import modo_inicial

		return modo_inicial.elegir(modo_inicial.modo_claude(carpeta), self.modos_permiso(), self.modo_de_serie())

	def instrucciones_instalacion(self) -> str:
		return (
			"Ctrl+IA abrirá una ventana de consola que descarga e instala Claude Code desde la página oficial "
			"de Anthropic. Cuando termine, pulsa Intro para cerrarla y después usa «Iniciar sesión» "
			"para entrar con tu cuenta de Claude."
		)

	def orden_instalacion(self) -> list[str]:
		return [
			"powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
			"try { irm https://claude.ai/install.ps1 | iex } catch { Write-Host $_ } finally { Read-Host 'Pulsa Intro para cerrar' }",
		]

	def orden_inicio_sesion(self) -> list[str] | None:
		orden = self.orden_base()
		return [*orden, "auth", "login"] if orden else None

	def obtener_comandos(self, carpeta: Path) -> list[tuple[str, str]]:
		"""Comandos de barra y habilidades disponibles en ``carpeta`` (sin coste: no envía nada).

		Bloquea ~1-2 s: llamar desde un hilo.
		"""
		orden = self.orden_base()
		if not orden:
			return []
		recibido = threading.Event()
		comandos: list[tuple[str, str]] = []

		def al_linea(linea: str) -> None:
			datos = procesos.json_o_nada(linea)
			if datos and datos.get("type") == "control_response":
				respuesta = (datos.get("response") or {}).get("response") or {}
				comandos.extend(_extraer_comandos(respuesta))
				recibido.set()

		proceso = procesos.ProcesoLineas(
			[*orden, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", *SIN_SERVIDORES_MCP],
			al_linea, carpeta=carpeta, entorno=entorno_claude(), nombre="claude-comandos",
		)
		try:
			proceso.iniciar()
			proceso.escribir_json({"type": "control_request", "request_id": "ctrlia-comandos",
				"request": {"subtype": "initialize", "hooks": None}})
			recibido.wait(ESPERA_INICIALIZAR)
		except OSError:
			log.warning("No se pudieron obtener los comandos de Claude Code", exc_info=True)
		finally:
			proceso.cerrar_entrada()
			proceso.terminar(espera=2)
		if comandos:
			self._comandos = comandos
		return comandos


def _primer_json(texto: str, clave: str) -> dict[str, Any] | None:
	"""Primer objeto JSON de ``texto`` que contiene ``clave`` (la salida puede traer avisos alrededor)."""
	for linea in texto.splitlines():
		linea = linea.strip()
		if linea.startswith("{") and clave in linea:
			try:
				datos = json.loads(linea)
			except ValueError:
				continue
			if isinstance(datos, dict):
				return datos
	inicio = texto.find("{")
	if inicio >= 0:
		try:
			datos = json.loads(texto[inicio:])
			return datos if isinstance(datos, dict) else None
		except ValueError:
			return None
	return None


def _extraer_comandos(respuesta: dict[str, Any]) -> list[tuple[str, str]]:
	comandos = []
	for comando in respuesta.get("commands") or []:
		if isinstance(comando, dict) and comando.get("name"):
			nombre = str(comando["name"])
			descripcion = str(comando.get("description") or "")
			pista = str(comando.get("argumentHint") or "")
			if pista:
				descripcion = f"{descripcion} (argumentos: {pista})" if descripcion else f"Argumentos: {pista}"
			comandos.append(("/" + nombre.lstrip("/"), descripcion))
	return comandos


@dataclass(slots=True)
class _Anadido:
	"""Un mensaje añadido al turno que Claude Code aún no ha recogido."""

	texto: str
	#: Llegó tras el resultado del turno: Claude lo responde aparte en vez de tenerlo en cuenta en lo que hacía.
	tarde: bool = False


class _ProcesoClaude:
	"""Un ``claude -p`` en marcha; con «Mantener Claude Code abierto» atiende varios turnos seguidos."""

	def __init__(self, firma: tuple[Any, ...] = (), reglas: set[str] | None = None) -> None:
		self.proceso: procesos.ProcesoLineas | None = None
		#: Lo que va en la línea de órdenes y obliga a relanzarlo si cambia.
		self.firma = firma
		#: Reglas «permitir durante esta sesión» que el proceso conoce (las de la orden y las concedidas después).
		self.reglas: set[str] = set(reglas or ())
		self.inicializado = threading.Event()
		self.vio_estados = False
		self.contexto_total = 0
		self.terminado = False
		self.temporizador: threading.Timer | None = None
		#: Hora (``time.time()``) de la última comprobación que Claude programó con ScheduleWakeup; 0 si ninguna.
		self.despertar = 0.0
		#: Tareas periódicas creadas con CronCreate y no borradas: viven dentro de este proceso.
		self.tareas_cron = 0
		#: Hay un hilo revisando sus servidores MCP (``ConversacionClaude._revisar_mcp``).
		self.revisando_mcp = False
		#: Servidores que ya se han reintentado solos en este proceso: un reintento por servidor.
		self.mcp_reintentados: set[str] = set()
		#: Mensajes añadidos al turno, escritos y sin recoger todavía: uuid → mensaje.
		self.anadidos: dict[str, _Anadido] = {}

	def tiene_programado(self, ahora: float) -> bool:
		"""Si cerrar ahora perdería algo que Claude dejó programado (AIChat #233)."""
		return self.tareas_cron > 0 or (self.despertar > 0 and ahora < self.despertar + MARGEN_PROGRAMADO)

	def vivo(self) -> bool:
		return not self.terminado and self.proceso is not None and self.proceso.vivo

	def cancelar_temporizador(self) -> None:
		if self.temporizador is not None:
			self.temporizador.cancel()
			self.temporizador = None


class _Turno:
	"""Estado de un turno: desde un mensaje hasta que Claude queda inactivo."""

	def __init__(self, vivo: _ProcesoClaude | None = None) -> None:
		#: El proceso que atiende el turno (``None`` solo en las pruebas que lo montan a mano).
		self.vivo = vivo
		self.proceso: procesos.ProcesoLineas | None = vivo.proceso if vivo is not None else None
		self.inicializado = vivo.inicializado if vivo is not None else threading.Event()
		self.sesion_iniciada = False
		self.vio_estados = vivo.vio_estados if vivo is not None else False
		self.resultados_pendientes = 1
		self.ultimo_resultado: dict[str, Any] | None = None
		self.tareas_fondo: set[str] = set()
		self.textos: dict[str, str] = {}
		#: Texto recibido en trozos (``text_delta``) por mensaje, aunque luego no llegue entero.
		self.parciales: dict[str, str] = {}
		#: Texto rescatado de los trozos (para no repetirlo si después llega el bloque completo).
		self.rescatados: dict[str, str] = {}
		#: Lo último que escribió Claude desde el último resultado de una herramienta.
		self.texto_reciente = ""
		self.herramientas: dict[str, str] = {}
		self.temporizador: threading.Timer | None = None
		self.terminado = False
		self.interrumpido = False
		#: Interrupciones pedidas con «Enviar ahora» cuyo resultado («error_during_execution») aún no ha llegado.
		self.interrupciones = 0
		#: El mensaje que abrió el turno ya se ha escrito en la entrada de Claude Code.
		self.mensaje_escrito = False
		self.abortado = ""
		self.contexto_total = vivo.contexto_total if vivo is not None else 0


class ConversacionClaude(Conversacion):
	"""Conversación con Claude Code: un proceso abierto entre mensajes (o uno por turno), retomando la sesión."""

	def __init__(self, proveedor: ProveedorClaude, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> None:
		super().__init__(ajustes, al_evento)
		self._proveedor = proveedor
		self._candado = threading.RLock()
		self._turno: _Turno | None = None
		#: El ``claude -p`` abierto (el del turno en marcha o el que espera el siguiente mensaje).
		self._vivo: _ProcesoClaude | None = None
		self._cola: list[tuple[str, list[Adjunto]]] = []
		#: El uuid de cada mensaje de ``_cola`` (en el mismo orden): se fija al llamar a ``enviar``.
		self._uuids_cola: list[str] = []
		#: Respuestas de control que alguien espera (``rewind_files``): ``request_id`` → (aviso, caja).
		self._esperas: dict[str, tuple[threading.Event, list[dict[str, Any]]]] = {}
		self._solicitudes: dict[str, dict[str, Any]] = {}
		#: Reglas que la persona ha dado «siempre» en esta conversación (las guarda Claude Code, no la sesión).
		self._reglas_siempre: set[str] = set()
		#: Nombre de cada servidor MCP como va en sus herramientas (``claude_ai_Gmail``) → como se muestra.
		self._servidores_mcp: dict[str, str] = {}
		#: Lo que Claude Code dijo que tenía cargado en su último ``system/init`` (AIChat #311).
		self._init: dict[str, Any] = {}
		#: Servidores MCP de los que ya se ha avisado, con el estado avisado (nombre → estado).
		self._mcp_avisados: dict[str, str] = {}
		self._cerrada = False
		#: La sesión ya existe en Claude Code (se puede retomar con --resume).
		self._sesion_creada = bool(ajustes.id_externo) and not ajustes.bifurcar_de
		if ajustes.id_externo and not _RE_ID.match(ajustes.id_externo):
			raise ValueError("Identificador de sesión no válido")

	# -- API pública ------------------------------------------------------------

	@property
	def anadidos_pendientes(self) -> int:
		"""Mensajes añadidos al turno en marcha que Claude Code todavía no ha recogido."""
		with self._candado:
			return len(self._vivo.anadidos) if self._vivo is not None else 0

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		with self._candado:
			if self._cerrada:
				raise RuntimeError("La conversación está cerrada")
			# El uuid va con el mensaje (Claude Code lo respeta): con él se deshacen sus cambios de archivos.
			uuid_mensaje = self.ultimo_uuid_usuario = str(uuid.uuid4())
			if self._turno is not None and not self._turno.terminado:
				self._encolar(texto, list(adjuntos), uuid_mensaje)
				self._emitir(TipoEvento.AVISO, "Mensaje en cola: se enviará cuando Claude termine.", en_cola=len(self._cola))
				return
			self._lanzar_turno(texto, list(adjuntos), uuid_mensaje)

	def _encolar(self, texto: str, adjuntos: list[Adjunto], uuid_mensaje: str) -> None:
		self._cola.append((texto, adjuntos))
		self._uuids_cola.append(uuid_mensaje)

	def _desencolar(self) -> tuple[str, list[Adjunto], str]:
		texto, adjuntos = self._cola.pop(0)
		uuid_mensaje = self._uuids_cola.pop(0) if self._uuids_cola else str(uuid.uuid4())
		return texto, adjuntos, uuid_mensaje

	def _vaciar_cola(self) -> None:
		self._cola.clear()
		self._uuids_cola.clear()

	def enviar_ahora(self, texto: str) -> None:
		"""Interrumpe lo que está haciendo Claude y le pasa el mensaje en el mismo proceso."""
		with self._candado:
			turno = self._turno
			if turno is None or turno.terminado or turno.proceso is None or not turno.proceso.vivo:
				self.enviar(texto)
				return
			uuid_mensaje = self.ultimo_uuid_usuario = str(uuid.uuid4())
			if not turno.mensaje_escrito:
				# Claude Code aún arranca y no tiene el primer mensaje: interrumpir ahora haría que este
				# llegase antes que aquel (AIChat #83). Va a la cola y sale en cuanto termine el turno.
				self._encolar(texto, [], uuid_mensaje)
				self._emitir(TipoEvento.AVISO, "Claude todavía está empezando: tu mensaje queda en cola y se enviará "
					"en cuanto termine este turno.", en_cola=len(self._cola))
				return
			turno.proceso.escribir_json({"type": "control_request", "request_id": f"ctrlia-interrupcion-{uuid.uuid4().hex[:8]}",
				"request": {"subtype": "interrupt"}})
			turno.interrupciones += 1
			turno.resultados_pendientes += 1
			turno.proceso.escribir_json(self._mensaje_usuario(texto, [], uuid_mensaje))
			self._emitir(TipoEvento.AVISO, "Mensaje enviado ahora; Claude deja lo que estaba haciendo.")

	def inventario(self) -> Any:
		from app.services import inventario

		if not self._init:
			resultado = inventario.Inventario("Claude Code")
			resultado.nota = ("Claude Code dice lo que tiene cargado al empezar a responder: envía un mensaje en esta "
				"sesión y vuelve a mirarlo.")
			return resultado
		vivo = self._proceso_listo()
		return inventario.desde_init_claude(self._init, self._estado_mcp(vivo) if vivo is not None else None)

	# -- Servidores MCP ---------------------------------------------------------

	def _proceso_listo(self) -> _ProcesoClaude | None:
		"""El proceso abierto, si ya ha contestado a ``initialize`` (antes no atiende peticiones de control)."""
		with self._candado:
			vivo = self._vivo
		if vivo is None or not vivo.vivo() or vivo.proceso is None or not vivo.inicializado.is_set():
			return None
		return vivo

	def _estado_mcp(self, vivo: _ProcesoClaude) -> list[dict[str, Any]] | None:
		"""Filas de ``mcp_status``; ``None`` si Claude Code no contesta o no lo admite."""
		assert vivo.proceso is not None
		try:
			datos = self._control_y_esperar(vivo.proceso, {"subtype": "mcp_status"}, ESPERA_ESTADO_MCP)
		except (RuntimeError, TimeoutError):
			log.debug("Claude Code no ha dicho el estado de sus servidores MCP", exc_info=True)
			return None
		respuesta = datos.get("response") or {}
		filas = (respuesta.get("response") or {}).get("mcpServers")
		return [fila for fila in filas if isinstance(fila, dict)] if isinstance(filas, list) else None

	def servidores_mcp(self) -> Any:
		from app.services import servidores_mcp

		vivo = self._proceso_listo()
		filas = self._estado_mcp(vivo) if vivo is not None else None
		if filas is None:
			filas = self._init.get("mcp_servers")
		return servidores_mcp.lista_desde_claude(filas)

	def mcp_en_vivo(self) -> bool:
		return self._proceso_listo() is not None

	def _control_mcp(self, peticion: dict[str, Any], tiempo: float) -> None:
		"""Petición que cambia un servidor MCP. Cuenta como uso del proceso abierto."""
		vivo = self._proceso_listo()
		if vivo is None or vivo.proceso is None:
			raise RuntimeError("Claude Code no está abierto: se conectará con tu próximo mensaje.")
		if vivo.temporizador is not None:
			self._programar_cierre(vivo)
		respuesta = self._control_y_esperar(vivo.proceso, peticion, tiempo).get("response") or {}
		if respuesta.get("subtype") == "error":
			raise RuntimeError(str(respuesta.get("error") or "Claude Code no ha podido hacerlo."))

	def _turno_en_marcha(self) -> bool:
		with self._candado:
			return self._turno is not None and not self._turno.terminado

	def reconectar_mcp(self, nombre: str) -> None:
		if self._turno_en_marcha():
			# Claude Code no atiende nada más mientras reconecta: un permiso concedido esperó 24 s (2.1.308).
			raise RuntimeError("Claude está trabajando. Espera a que termine o detenlo, y vuelve a intentarlo.")
		self._control_mcp({"subtype": "mcp_reconnect", "serverName": nombre}, self._proveedor.segundos_mcp() + ESPERA_ESTADO_MCP)
		with self._candado:
			self._mcp_avisados.pop(nombre, None)

	def activar_mcp(self, nombre: str, activo: bool) -> None:
		self._control_mcp({"subtype": "mcp_toggle", "serverName": nombre, "enabled": bool(activo)}, ESPERA_ESTADO_MCP)
		with self._candado:
			self._mcp_avisados.pop(nombre, None)

	def orden_inicio_sesion_mcp(self, nombre: str) -> list[str] | None:
		base = self._proveedor.orden_base()
		if not base or remoto.es_remota(self.ajustes.extra):
			return None
		return [*base, "mcp", "login", nombre]

	def _vigilar_mcp(self, vivo: _ProcesoClaude | None, filas: Any) -> None:
		"""Tras un ``system/init``: si algún servidor no está conectado, se revisa en otro hilo (el lector no puede esperar)."""
		from app.services.servidores_mcp import lista_desde_claude

		servidores = lista_desde_claude(filas)
		with self._candado:
			avisados = bool(self._mcp_avisados)
		if (vivo is None or vivo.proceso is None or vivo.revisando_mcp
				or not (avisados or any(s.estado in ("failed", "needs-auth", "pending") for s in servidores))):
			return
		vivo.revisando_mcp = True
		threading.Thread(target=self._revisar_mcp, args=(vivo,), name="claude-mcp-estado", daemon=True).start()

	def _revisar_mcp(self, vivo: _ProcesoClaude) -> None:
		"""Avisa de los servidores que se quedan sin conectar y sigue a los que aún se conectan.

		Un servidor que falla en el arranque no se vuelve a intentar en todo el proceso
		(hasta ``claude_minutos_abierto`` minutos), y Claude Code no lo cuenta: sin esto
		la persona solo veía que el agente no tenía la herramienta.
		"""
		from app.services.servidores_mcp import lista_desde_claude

		limite = time.monotonic() + self._proveedor.segundos_mcp() + ESPERA_ESTADO_MCP
		try:
			while vivo.vivo() and not self._cerrada:
				filas = self._estado_mcp(vivo)
				if filas is None:
					return
				servidores = lista_desde_claude(filas)
				self._avisar_mcp(servidores)
				self._reintentar_mcp_en_reposo(vivo)
				if not any(s.estado == "pending" for s in servidores) or time.monotonic() > limite:
					return
				time.sleep(PAUSA_REVISAR_MCP)
		except Exception:  # noqa: BLE001 - revisar los MCP nunca debe molestar a la conversación
			log.exception("No se pudo revisar el estado de los servidores MCP de Claude Code")
		finally:
			vivo.revisando_mcp = False

	def _reintentar_mcp_en_reposo(self, vivo: _ProcesoClaude | None) -> None:
		"""Sin turno en marcha, reintenta una vez cada servidor que falló en este proceso.

		Solo en reposo: Claude Code no atiende nada más mientras reconecta. Cubre sobre
		todo la primera vez de un ``npx`` o un ``uvx``, que se pasa del tiempo descargando.
		"""
		if vivo is None or vivo.proceso is None or self._turno_en_marcha():
			return
		with self._candado:
			nombres = [nombre for nombre, estado in self._mcp_avisados.items()
				if estado == "failed" and nombre not in vivo.mcp_reintentados]
			vivo.mcp_reintentados.update(nombres)
		if nombres:
			threading.Thread(target=self._reintentar_mcp, args=(vivo, nombres), name="claude-mcp-reintento", daemon=True).start()

	def _reintentar_mcp(self, vivo: _ProcesoClaude, nombres: list[str]) -> None:
		from app.services.servidores_mcp import lista_desde_claude

		assert vivo.proceso is not None
		try:
			for nombre in nombres:
				if self._turno_en_marcha() or not vivo.vivo():
					vivo.mcp_reintentados.discard(nombre)  # se reintenta en el próximo reposo
					continue
				try:
					respuesta = self._control_y_esperar(vivo.proceso, {"subtype": "mcp_reconnect", "serverName": nombre},
						self._proveedor.segundos_mcp() + ESPERA_ESTADO_MCP).get("response") or {}
				except (RuntimeError, TimeoutError):
					log.info("El servidor MCP %s no ha contestado al reintento", nombre, exc_info=True)
					continue
				log.info("Reintento del servidor MCP %s: %s", nombre, respuesta.get("subtype"))
			filas = self._estado_mcp(vivo) if vivo.vivo() else None
			if filas is not None:
				self._avisar_mcp(lista_desde_claude(filas))
		except Exception:  # noqa: BLE001 - revisar los MCP nunca debe molestar a la conversación
			log.exception("No se pudo reintentar un servidor MCP de Claude Code")

	def _avisar_mcp(self, servidores: list[Any]) -> None:
		"""Un aviso por servidor y estado: al quedarse sin conectar y, si se avisó, al volver."""
		from app.services.servidores_mcp import aviso_de_problema

		for servidor in servidores:
			with self._candado:
				avisado = self._mcp_avisados.get(servidor.nombre)
				if servidor.con_problema:
					if avisado == servidor.estado:
						continue
					self._mcp_avisados[servidor.nombre] = servidor.estado
				elif avisado is None or servidor.estado == "pending":
					continue
				else:
					self._mcp_avisados.pop(servidor.nombre, None)
			if servidor.con_problema:
				log.warning("Servidor MCP de Claude Code sin conectar: %s (%s: %s)", servidor.nombre, servidor.estado, servidor.motivo)
				self._emitir(TipoEvento.AVISO, aviso_de_problema(servidor), mcp=servidor.nombre)
			elif servidor.estado == "connected":
				self._emitir(TipoEvento.AVISO, "La herramienta externa «%s» de Claude ya está conectada." % servidor.nombre,
					mcp=servidor.nombre)

	def anadir_al_turno(self, texto: str) -> None:
		"""Mete el mensaje en el turno en marcha sin detenerlo: va a la entrada del proceso abierto.

		Claude Code lo lee en cuanto acaba lo que está haciendo (una herramienta, un
		párrafo) y lo tiene en cuenta en ese mismo turno; al recogerlo devuelve el
		eco que confirma que lo tiene (``_al_eco``). Si no hay turno en marcha, o
		Claude aún no tiene su primer mensaje, no se escribe nada y el texto vuelve
		al cuadro de respuesta: nunca se convierte por su cuenta en un mensaje nuevo.
		Si lo que falla es la escritura, ya no se puede asegurar que no llegara.
		"""
		with self._candado:
			turno = self._turno
			listo = (turno is not None and not turno.terminado and turno.mensaje_escrito and not turno.interrumpido
				and turno.proceso is not None and turno.proceso.vivo and not self._cerrada)
			escrito = False
			if listo:
				uuid_mensaje = str(uuid.uuid4())
				vivo = turno.vivo
				if vivo is not None:
					vivo.anadidos[uuid_mensaje] = _Anadido(texto, tarde=turno.resultados_pendientes == 0)
				escrito = turno.proceso.escribir_json(self._mensaje_usuario(texto, [], uuid_mensaje))
				if escrito:
					self.ultimo_uuid_usuario = uuid_mensaje
				elif vivo is not None:
					vivo.anadidos.pop(uuid_mensaje, None)
		if not listo:
			self._emitir(TipoEvento.AVISO, "Claude no está trabajando en un turno ahora mismo; tu mensaje no se ha "
				"enviado. El texto vuelve al cuadro de respuesta.", devolver_texto=texto)
		elif not escrito:
			self._emitir(TipoEvento.AVISO, "Claude Code no ha confirmado que recibiera tu mensaje. " + NOTA_SIN_CONFIRMAR,
				sin_confirmar=texto)
		else:
			log.info("Mensaje añadido al turno en marcha de Claude Code")

	def pendientes(self) -> list[str]:
		with self._candado:
			textos = [texto for texto, _ in self._cola]
			self._vaciar_cola()
			return textos

	def detener(self) -> None:
		with self._candado:
			turno = self._turno
			if turno is None or turno.terminado:
				return
			turno.interrumpido = True
			proceso = turno.proceso
		if proceso is not None and proceso.vivo:
			proceso.escribir_json({"type": "control_request", "request_id": "ctrlia-detener", "request": {"subtype": "interrupt"}})
			temporizador = threading.Timer(ESPERA_DETENER, self._forzar_fin, args=(turno,))
			temporizador.daemon = True
			temporizador.start()
		self._retirar_solicitudes()

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		with self._candado:
			pendiente = self._solicitudes.pop(solicitud_id, None)
			turno = self._turno
		if pendiente is None or turno is None or turno.proceso is None:
			log.info("Respuesta a una solicitud que ya no está pendiente")
			return
		cuerpo = self._cuerpo_respuesta(pendiente, respuesta)
		turno.proceso.escribir_json({"type": "control_response",
			"response": {"subtype": "success", "request_id": solicitud_id, "response": cuerpo}})
		if pendiente.get("concedidas"):
			self._permitir_pendientes_cubiertas(turno, pendiente["concedidas"])
		if not self._solicitudes:
			self._cambiar_estado(Estado.TRABAJANDO)

	def _permitir_pendientes_cubiertas(self, turno: _Turno, reglas: list[str]) -> None:
		"""Permite las peticiones que esperaban y que la regla recién concedida ya cubre.

		Claude Code pregunta a la vez por cada llamada en paralelo (tres «eco» de un
		servidor MCP son tres peticiones) y no retira las demás cuando una respuesta
		añade la regla: sin esto, la persona tendría que contestar lo mismo varias veces.
		"""
		with self._candado:
			cubiertas = {
				identificador: pendiente for identificador, pendiente in self._solicitudes.items()
				if pendiente["herramienta"] not in HERRAMIENTAS_SIN_REGLA and pendiente.get("admite_regla", True)
				and any(regla_cubre(regla, pendiente["herramienta"], pendiente["entrada"]) for regla in reglas)
			}
			for identificador in cubiertas:
				self._solicitudes.pop(identificador, None)
		if not cubiertas or turno.proceso is None:
			return
		for identificador, pendiente in cubiertas.items():
			turno.proceso.escribir_json({"type": "control_response", "response": {"subtype": "success",
				"request_id": identificador, "response": {"behavior": "allow", "updatedInput": pendiente["entrada"]}}})
			self._emitir(TipoEvento.PERMISO_RETIRADO, id=identificador)
		log.info("Permitidas %d peticiones pendientes que cubre la regla recién concedida", len(cubiertas))
		texto = ("También se ha permitido otra petición igual que esperaba respuesta." if len(cubiertas) == 1
			else f"También se han permitido {len(cubiertas)} peticiones iguales que esperaban respuesta.")
		self._emitir(TipoEvento.AVISO, texto, permitidas_por_regla=len(cubiertas))

	def cambiar_ajustes(self, **cambios: Any) -> None:
		with self._candado:
			super().cambiar_ajustes(**cambios)
			turno = self._turno
			vivo = self._vivo
		modo = cambios.get("modo_permiso")
		proceso = vivo.proceso if vivo is not None and vivo.vivo() else (turno.proceso if turno is not None else None)
		if modo and proceso is not None and proceso.vivo:
			proceso.escribir_json({"type": "control_request", "request_id": f"ctrlia-modo-{uuid.uuid4().hex[:6]}",
				"request": {"subtype": "set_permission_mode", "mode": modo}})

	def cerrar(self) -> None:
		with self._candado:
			self._cerrada = True
			turno = self._turno
			vivo, self._vivo = self._vivo, None
			self._vaciar_cola()
			esperas = list(self._esperas.values())
		self._retirar_herramientas_ctrlia()
		for aviso, _caja in esperas:
			aviso.set()  # quien esperaba un «rewind_files» ve que no hay respuesta
		if vivo is not None:
			vivo.cancelar_temporizador()
		proceso = vivo.proceso if vivo is not None else (turno.proceso if turno is not None else None)
		if proceso is not None:
			proceso.cerrar_entrada()
			proceso.terminar(espera=2)
		self._retirar_solicitudes()
		self._cambiar_estado(Estado.CERRADA)

	# -- Deshacer los cambios de archivos de un mensaje -----------------------------

	def deshacer_archivos(self, uuid_mensaje: str, prueba: bool = False) -> dict[str, Any]:
		"""Devuelve los archivos al estado de antes del mensaje ``uuid_mensaje`` («rewind_files» de Claude Code).

		Solo con Claude parado. Usa el proceso abierto entre mensajes o, si no lo
		hay, uno nuevo con ``--resume`` que solo hace esto y se cierra. Solo cubre lo
		que cambiaron Write, Edit y NotebookEdit (no lo que hicieron las órdenes).

		:param prueba: solo dice qué cambiaría (``dry_run``), sin tocar nada.
		:returns: ``{"canRewind", "error", "filesChanged", "insertions", "deletions"}``.
		:raises ValueError: si Claude Code no guardó el estado de ese mensaje.
		:raises RuntimeError: si Claude está trabajando, la sesión no existe o Claude Code falla.
		:raises TimeoutError: si Claude Code no contesta a tiempo.
		"""
		if not isinstance(uuid_mensaje, str) or not _RE_UUID.match(uuid_mensaje):
			raise ValueError("Identificador de mensaje no válido")
		with self._candado:
			if self._cerrada:
				raise RuntimeError("La conversación está cerrada")
			turno = self._turno
			if turno is not None and not turno.terminado:
				raise RuntimeError("Claude está trabajando: detenlo antes de deshacer los cambios.")
			vivo = self._vivo
			abierto = vivo is not None and vivo.vivo() and vivo.proceso is not None
			if not abierto and not (self._sesion_creada and self.ajustes.id_externo):
				raise RuntimeError("Esta sesión todavía no existe en Claude Code: no hay cambios que deshacer.")
		peticion = {"subtype": "rewind_files", "user_message_id": uuid_mensaje, "dry_run": bool(prueba)}
		if abierto:
			assert vivo is not None and vivo.proceso is not None
			if vivo.temporizador is not None:
				self._programar_cierre(vivo)  # cuenta como uso: no se cierra mientras tanto
			respuesta = self._control_y_esperar(vivo.proceso, peticion, ESPERA_DESHACER)
		else:
			respuesta = self._deshacer_con_proceso_nuevo(peticion)
		resultado = _resultado_deshacer(respuesta)
		log.info("Deshacer archivos de Claude (%s): %s, %d archivos", "prueba" if prueba else "real",
			"posible" if resultado["canRewind"] else "no posible", len(resultado["filesChanged"]))
		return resultado

	def _control_y_esperar(self, proceso: procesos.ProcesoLineas, peticion: dict[str, Any], tiempo: float) -> dict[str, Any]:
		"""Envía una petición de control y espera su respuesta (la recoge ``_control_propio``)."""
		identificador = f"ctrlia-control-{uuid.uuid4().hex[:12]}"
		aviso = threading.Event()
		caja: list[dict[str, Any]] = []
		with self._candado:
			self._esperas[identificador] = (aviso, caja)
		try:
			if not proceso.escribir_json({"type": "control_request", "request_id": identificador, "request": peticion}):
				raise RuntimeError("Claude Code se ha cerrado antes de recibir la petición.")
			limite = time.monotonic() + tiempo
			while not aviso.wait(0.2):
				if not proceso.vivo:
					raise RuntimeError("Claude Code se ha cerrado sin contestar.")
				if time.monotonic() > limite:
					raise TimeoutError("Claude Code no ha contestado a tiempo.")
		finally:
			with self._candado:
				self._esperas.pop(identificador, None)
		if not caja:
			raise RuntimeError("La conversación se ha cerrado sin respuesta de Claude Code.")
		return caja[0]

	def _deshacer_con_proceso_nuevo(self, peticion: dict[str, Any]) -> dict[str, Any]:
		"""Un ``claude -p --resume`` sin mensaje, solo para «rewind_files»: se cierra al contestar."""
		base = ["claude"] if remoto.es_remota(self.ajustes.extra) else self._proveedor.orden_base()
		if not base:
			raise RuntimeError("No se encuentra Claude Code")
		orden = [*base, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
			"--permission-prompt-tool", "stdio", "--resume", self.ajustes.id_externo, *SIN_SERVIDORES_MCP]
		carpeta: Path | None = self.ajustes.carpeta
		entorno = entorno_claude()
		equipo = remoto.equipo_de(self.ajustes.extra)
		if equipo is not None:
			orden = remoto.orden_remota(equipo, "claude", orden[1:], remoto.carpeta_remota(self.ajustes.extra),
				quitar=VARIABLES_DE_SESION + VARIABLES_DE_FACTURACION, anadir=variables_anadidas())
			carpeta, entorno = None, remoto.entorno_ssh()
		esperas: dict[str, tuple[threading.Event, list[dict[str, Any]]]] = {}

		def al_linea(linea: str) -> None:
			datos = procesos.json_o_nada(linea)
			if not datos or datos.get("type") != "control_response":
				return
			respuesta = datos.get("response") if isinstance(datos.get("response"), dict) else {}
			espera = esperas.get(str(respuesta.get("request_id") or ""))
			if espera is not None:
				espera[1].append(datos)
				espera[0].set()

		proceso = procesos.ProcesoLineas(orden, al_linea, carpeta=carpeta, entorno=entorno, nombre="claude-deshacer")

		def pedir(identificador: str, cuerpo: dict[str, Any]) -> dict[str, Any]:
			aviso, caja = threading.Event(), []
			esperas[identificador] = (aviso, caja)
			if not proceso.escribir_json({"type": "control_request", "request_id": identificador, "request": cuerpo}):
				raise RuntimeError("Claude Code se ha cerrado antes de recibir la petición.")
			limite = time.monotonic() + ESPERA_DESHACER
			while not aviso.wait(0.2):
				if not proceso.vivo:
					detalle = " ".join(proceso.errores_recientes(3))[-300:]
					raise RuntimeError("Claude Code se ha cerrado sin contestar." + (f" Detalle: {detalle}" if detalle else ""))
				if time.monotonic() > limite:
					raise TimeoutError("Claude Code no ha contestado a tiempo.")
			return caja[0]

		try:
			proceso.iniciar()
		except OSError as error:
			raise RuntimeError(f"No se pudo iniciar Claude Code: {error}") from error
		try:
			pedir("ctrlia-deshacer-inicio", {"subtype": "initialize", "hooks": None})
			return pedir(f"ctrlia-deshacer-{uuid.uuid4().hex[:8]}", peticion)
		finally:
			proceso.cerrar_entrada()
			if not proceso.esperar_fin(ESPERA_RETIRAR):
				proceso.terminar()

	# -- Construcción de la orden -------------------------------------------------

	def construir_orden(self) -> list[str]:
		# En otro equipo no hace falta tenerlo en Windows: el nombre se sustituye al envolver en SSH.
		base = ["claude"] if remoto.es_remota(self.ajustes.extra) else self._proveedor.orden_base()
		if not base:
			raise FileNotFoundError("No se encuentra Claude Code")
		ajustes = self.ajustes
		orden = [
			*base, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
			"--include-partial-messages", "--replay-user-messages", "--permission-prompt-tool", "stdio",
		]
		# Sin modo elegido no se pasa nada: así manda el «defaultMode» de los settings.json de Claude Code.
		if ajustes.modo_permiso:
			orden += ["--permission-mode", ajustes.modo_permiso]
		if ajustes.modelo and _RE_MODELO.match(ajustes.modelo):
			orden += ["--model", ajustes.modelo]
		if ajustes.esfuerzo:
			orden += ["--effort", ajustes.esfuerzo]
		estilo = str(ajustes.extra.get("estilo") or "")
		if estilo and estilo.lower() != "default" and _RE_ESTILO.match(estilo):
			# Solo para esta sesión: nunca se cambia el settings.json de la persona.
			orden += ["--settings", json.dumps({"outputStyle": estilo})]
		if self._proveedor.usar_chrome() and not remoto.es_remota(ajustes.extra):
			# Solo aquí: el Chrome de la persona está en este equipo, no en el del otro lado del SSH.
			orden.append("--chrome")
		reglas = [regla for regla in ajustes.reglas_permitidas if _RE_REGLA.match(regla) and not regla.startswith(PREFIJO_CARPETA)]
		# «ctrlia» solo afecta a esta sesión: no se pregunta por él. De los comandos de sesión, solo por lo que no lee
		# ni cambia otra sesión (``herramientas_sesiones.SIN_PREGUNTAR``).
		reglas += self._reglas_en_proceso()
		if reglas:
			orden += ["--allowedTools", *reglas]
		for carpeta in ajustes.extra.get("carpetas_adicionales", []) or []:
			orden += ["--add-dir", str(carpeta)]
		posix = remoto.es_remota(ajustes.extra)
		for regla in ajustes.reglas_permitidas:
			carpeta = carpeta_de_regla(regla)
			if carpeta and carpeta_concreta(carpeta, posix):
				orden += ["--add-dir", carpeta]
		if ajustes.bifurcar_de:
			if not _RE_ID.match(ajustes.bifurcar_de) or ajustes.bifurcar_de.startswith("local_"):
				raise ValueError("No se puede continuar esa sesión")
			nuevo = ajustes.id_externo or str(uuid.uuid4())
			ajustes.id_externo = nuevo
			orden += ["--resume", ajustes.bifurcar_de, "--fork-session", "--session-id", nuevo]
		elif self._sesion_creada and ajustes.id_externo:
			orden += ["--resume", ajustes.id_externo]
		else:
			if not ajustes.id_externo:
				ajustes.id_externo = str(uuid.uuid4())
			orden += ["--session-id", ajustes.id_externo]
		if ajustes.titulo:
			orden += ["--name", ajustes.titulo[:120]]
		complemento: Path | None = None
		if self.skills and not posix:
			# Con el identificador ya definitivo: el complemento de la sesión es siempre el mismo.
			from app.services import skills_sesion

			try:
				complemento = skills_sesion.preparar_complemento("claude:" + ajustes.id_externo, self.skills)
			except (OSError, ValueError):
				log.warning("No se pudo preparar el complemento con las skills de la sesión", exc_info=True)
				complemento = None
			if complemento is not None:
				orden += ["--plugin-dir", str(complemento)]
				# Sin esto, algún modelo (Haiku) ve la habilidad pero no la abre: se le dice que la persona las eligió
				# y dónde está cada SKILL.md, que puede leer sin preguntar (solo leer: nunca escribir ahí).
				nombres = ", ".join("%s:%s" % (skills_sesion.NOMBRE_COMPLEMENTO, n)
					for n in skills_sesion.nombres_en_complemento(complemento))
				rutas = "; ".join("%s: %s" % (s.get("nombre", ""), s.get("skill") or Path(str(s.get("carpeta", ""))) / "SKILL.md")
					for s in self.skills)
				orden += ["--append-system-prompt", (
					"La persona ha elegido para esta sesión estas skills, que tienes como habilidades: %s. Cuando la "
					"tarea encaje con una o te pida usarla, ábrela con la herramienta Skill y sigue sus instrucciones; "
					"si te pregunta por ellas, ábrelas para responder. Sus archivos SKILL.md están en: %s." % (nombres, rutas))]
				# También la del propio complemento: Claude Code abre las skills por esa ruta (enlaces a las mismas carpetas).
				carpetas = [*(s.get("carpeta", "") for s in self.skills), str(complemento)]
				lecturas = [regla for regla in (skills_sesion.regla_lectura(c) for c in carpetas) if regla]
				if lecturas:
					orden += ["--allowedTools", *lecturas]
		if not posix:
			permitir, prohibir = self._reglas_con_nombres_nuevos(complemento)
			for carpeta in (carpeta_de_regla(regla) for regla in permitir):
				if carpeta and carpeta_concreta(carpeta) and Path(carpeta).is_dir():
					orden += ["--add-dir", carpeta]
			permitir = [regla for regla in permitir if not regla.startswith(PREFIJO_CARPETA)]
			if permitir:
				orden += ["--allowedTools", *permitir]
			if prohibir:
				orden += ["--disallowedTools", *prohibir]
		return orden

	def _reglas_con_nombres_nuevos(self, complemento: Path | None) -> tuple[list[str], list[str]]:
		"""``(permitir, no permitir nunca)``: las reglas que nombran las skills como antes, con sus nombres de ahora.

		Las de la sesión y las de la configuración de Claude Code de la carpeta («Permitir siempre en esta
		carpeta»). Solo se añaden a esta orden: nunca se cambia ningún archivo. Sin esto, lo que la persona
		permitió con la 1.5.0 (``data\\agentes_ejemplo``, ``ctrlia-agentes:``) o en otra sesión (la carpeta de
		su complemento) se volvería a preguntar. Ver ``skills_sesion.traducir_regla``.
		"""
		from app.core.migraciones import carpetas_datos_propias
		from app.services import skills_sesion
		from app.services.config_agentes import reglas_claude

		try:
			encontradas, _ilegibles = reglas_claude.reglas_de(str(self.ajustes.carpeta or ""))
		except Exception:  # noqa: BLE001 - sin leerlas, Claude Code las aplica igual con los nombres de antes
			log.debug("No se pudieron leer las reglas de Claude Code de la carpeta", exc_info=True)
			encontradas = []
		permitidas = [*self.ajustes.reglas_permitidas,
			*(r.regla for r in encontradas if r.clase == "allow" and r.aplicada is True)]
		prohibidas = [r.regla for r in encontradas if r.clase == "deny"]
		datos = carpetas_datos_propias()
		permitir = [regla for regla in skills_sesion.reglas_traducidas(permitidas, complemento, datos)
			if _RE_REGLA.match(regla) or regla.startswith(PREFIJO_CARPETA)]
		prohibir = [regla for regla in skills_sesion.reglas_traducidas(prohibidas, complemento, datos) if _RE_REGLA.match(regla)]
		return permitir, prohibir

	def _mensaje_usuario(self, texto: str, adjuntos: list[Adjunto], uuid_mensaje: str = "") -> dict[str, Any]:
		bloques: list[dict[str, Any]] = []
		menciones = []
		for adjunto in adjuntos:
			if adjunto.es_imagen:
				try:
					if adjunto.ruta.stat().st_size <= MAX_IMAGEN:
						datos = base64.b64encode(adjunto.ruta.read_bytes()).decode("ascii")
						bloques.append({"type": "image", "source": {"type": "base64", "media_type": adjunto.tipo_mime, "data": datos}})
						continue
				except OSError:
					log.warning("No se pudo leer la imagen adjunta", exc_info=True)
			if remoto.es_remota(self.ajustes.extra):
				# El otro equipo no ve los archivos de Windows: solo viajan las imágenes.
				menciones.append(f"(Archivo adjunto que no se ha enviado porque está en Windows: {adjunto.ruta.name})")
				continue
			menciones.append(f'Archivo adjunto: @"{adjunto.ruta}"')
		if menciones:
			texto = (texto + "\n\n" if texto else "") + "\n".join(menciones)
		contenido: Any = texto
		if bloques:
			contenido = [{"type": "text", "text": texto}, *bloques] if texto else bloques
		return {"type": "user", "session_id": "", "parent_tool_use_id": None, "uuid": uuid_mensaje or str(uuid.uuid4()),
			"message": {"role": "user", "content": contenido}}

	# -- Turnos -----------------------------------------------------------------

	def _reglas_validas(self) -> set[str]:
		return {regla for regla in self.ajustes.reglas_permitidas if _RE_REGLA.match(regla)}

	def _firma(self, lista_tareas: bool, esperar_mcp: bool, segundos_mcp: int) -> tuple[Any, ...]:
		"""Lo que va en la orden o en el entorno de ``claude -p`` (sin el modo ni el título): si cambia, se relanza."""
		ajustes = self.ajustes
		datos_remotos = remoto.datos_remotos(ajustes.extra) or {}
		return (
			self.herramientas_ctrlia is not None,
			ajustes.modelo if _RE_MODELO.match(ajustes.modelo or "x") else "", ajustes.esfuerzo,
			tuple(str(c) for c in ajustes.extra.get("carpetas_adicionales", []) or []),
			str(datos_remotos.get("equipo") or ""), bool(lista_tareas), bool(esperar_mcp), int(segundos_mcp),
			self._proveedor.usar_chrome(), str(ajustes.extra.get("estilo") or ""),
			tuple(sorted(str(s.get("carpeta") or "") for s in self.skills)),
		)

	def _lanzar_turno(self, texto: str, adjuntos: list[Adjunto], uuid_mensaje: str = "") -> None:
		lista_tareas = self._proveedor.usar_lista_tareas()
		esperar_mcp = self._proveedor.esperar_mcp()
		segundos_mcp = self._proveedor.segundos_mcp()
		# Antes de la firma: activar o desactivar las herramientas de Ctrl+IA relanza Claude Code.
		herramientas = self._preparar_herramientas_ctrlia("Claude")
		firma = self._firma(lista_tareas, esperar_mcp, segundos_mcp)
		reglas = self._reglas_validas()
		anterior = self._vivo
		if (anterior is not None and anterior.vivo() and self._proveedor.minutos_abierto() > 0
				and anterior.firma == firma and anterior.reglas == reglas):
			# El mismo proceso sigue abierto y nada obliga a relanzarlo: el mensaje va directo.
			anterior.cancelar_temporizador()
			turno = _Turno(anterior)
			self._turno = turno
			self._cambiar_estado(Estado.TRABAJANDO)
			mensaje = self._mensaje_usuario(texto, adjuntos, uuid_mensaje)
			threading.Thread(target=self._enviar_tras_inicializar, args=(turno, mensaje), name="claude-envio", daemon=True).start()
			return
		if anterior is not None:
			self._retirar_proceso(anterior)
		vivo = _ProcesoClaude(firma, reglas)
		turno = _Turno(vivo)
		self._turno = turno
		try:
			orden = self.construir_orden()
			carpeta, entorno = self.ajustes.carpeta, entorno_claude(lista_tareas, esperar_mcp, segundos_mcp)
			equipo = remoto.equipo_de(self.ajustes.extra)
			if equipo is not None:
				orden = remoto.orden_remota(
					equipo, "claude", orden[1:], remoto.carpeta_remota(self.ajustes.extra),
					quitar=VARIABLES_DE_SESION + VARIABLES_DE_FACTURACION,
					anadir=variables_anadidas(lista_tareas, esperar_mcp, segundos_mcp),
				)
				carpeta, entorno = None, remoto.entorno_ssh()
		except (FileNotFoundError, ValueError, remoto.ErrorRemoto) as error:
			turno.terminado = True
			self._emitir(TipoEvento.ERROR, str(error))
			self._cambiar_estado(Estado.ERROR)
			return
		vivo.proceso = turno.proceso = procesos.ProcesoLineas(
			orden,
			lambda linea: self._al_linea_de(vivo, linea),
			lambda codigo: self._al_salir_de(vivo, codigo),
			carpeta=carpeta,
			entorno=entorno,
			nombre="claude",
		)
		self._vivo = vivo
		self._cambiar_estado(Estado.TRABAJANDO)
		try:
			turno.proceso.iniciar()
		except OSError as error:
			turno.terminado = vivo.terminado = True
			self._vivo = None
			self._emitir(TipoEvento.ERROR, f"No se pudo iniciar Claude Code: {error}")
			self._cambiar_estado(Estado.ERROR)
			return
		mensaje = self._mensaje_usuario(texto, adjuntos, uuid_mensaje)
		inicio: dict[str, Any] = {"subtype": "initialize", "hooks": None}
		if herramientas is not None:
			# Servidores MCP «en proceso»: Claude Code les habla con «mcp_message» por esta misma conexión.
			inicio["sdkMcpServers"] = self._servidores_en_proceso()
		turno.proceso.escribir_json({"type": "control_request", "request_id": "ctrlia-inicio", "request": inicio})
		threading.Thread(target=self._enviar_tras_inicializar, args=(turno, mensaje), name="claude-envio", daemon=True).start()

	def _enviar_tras_inicializar(self, turno: _Turno, mensaje: dict[str, Any]) -> None:
		esperar_mcp = self._proveedor.esperar_mcp()
		# Con la espera, Claude Code contesta a «initialize» cuando acaba con sus servidores MCP.
		maximo = ESPERA_INICIALIZAR + (self._proveedor.segundos_mcp() if esperar_mcp else 0)
		if not turno.inicializado.wait(ESPERA_AVISO_MCP) and esperar_mcp and not (turno.abortado or turno.terminado):
			self._emitir(TipoEvento.AVISO, "Claude Code está conectando tus herramientas externas (servidores MCP). "
				"Tu mensaje saldrá en cuanto estén listas.")
		turno.inicializado.wait(max(0.0, maximo - ESPERA_AVISO_MCP))
		if turno.abortado or turno.terminado or turno.proceso is None:
			return
		if not turno.proceso.escribir_json(mensaje):
			self._emitir(TipoEvento.ERROR, "Claude Code se cerró antes de recibir el mensaje.")
			return
		turno.mensaje_escrito = True

	def _al_linea_de(self, vivo: _ProcesoClaude, linea: str) -> None:
		"""Línea de un proceso: va al turno en marcha o, si Claude empieza solo, a un turno espontáneo."""
		if ('"mcp_message"' in linea or '"control_response"' in linea) and self._control_propio(vivo, linea):
			return  # herramientas de Ctrl+IA o respuesta que alguien espera: nunca abre un turno
		if '"isReplay"' in linea and self._al_eco(vivo, linea):
			return  # antes de mirar si el proceso sigue en uso: un retirado aún atiende lo que tenía en la entrada
		with self._candado:
			if vivo is not self._vivo:
				return  # un proceso retirado: lo que diga ya no cuenta
			turno = self._turno
			if turno is None or turno.terminado or turno.vivo is not vivo:
				turno = self._turno_espontaneo(vivo, linea)
		self._al_linea(turno, linea)

	def _al_eco(self, vivo: _ProcesoClaude, linea: str) -> bool:
		"""Claude Code ha recogido uno de nuestros mensajes (``--replay-user-messages``). ``True`` si la línea era un eco.

		El eco de cada mensaje llega con el uuid con el que se escribió; solo importa el
		de los añadidos al turno. Si llega tras el resultado, Claude lo responde con otro
		resultado: el turno sigue hasta ese, y deja de contar la espera del primero.
		"""
		datos = procesos.json_o_nada(linea)
		if datos is None or datos.get("type") != "user" or not datos.get("isReplay"):
			return False
		with self._candado:
			anadido = vivo.anadidos.pop(str(datos.get("uuid") or ""), None)
			turno = self._turno
			if anadido is None:
				return True
			if turno is not None and turno.vivo is vivo and not turno.terminado and turno.resultados_pendientes == 0:
				anadido.tarde = True
				turno.resultados_pendientes = 1
				if turno.temporizador is not None:
					turno.temporizador.cancel()
					turno.temporizador = None
		texto = ("Claude ya había terminado y ahora responde a tu mensaje." if anadido.tarde
			else "Claude ha recogido tu mensaje en el turno en marcha.")
		self._emitir(TipoEvento.AVISO, texto, anadido=True, texto_anadido=anadido.texto)
		return True

	def _control_propio(self, vivo: _ProcesoClaude, linea: str) -> bool:
		"""Atiende lo que no es de ningún turno: el servidor MCP de Ctrl+IA y las respuestas esperadas. ``True`` si lo era."""
		datos = procesos.json_o_nada(linea)
		if datos is None:
			return False
		if datos.get("type") == "control_response":
			respuesta = datos.get("response") if isinstance(datos.get("response"), dict) else {}
			with self._candado:
				espera = self._esperas.get(str(respuesta.get("request_id") or ""))
			if espera is None:
				return False
			espera[1].append(datos)
			espera[0].set()
			return True
		peticion = datos.get("request") if isinstance(datos.get("request"), dict) else {}
		if datos.get("type") != "control_request" or peticion.get("subtype") != "mcp_message":
			return False
		if vivo.proceso is not None:
			self._al_mcp(vivo.proceso, str(datos.get("request_id") or ""), peticion)
		return True

	def _servidores_en_proceso(self) -> list[str]:
		"""Servidores MCP «en proceso» de esta sesión: «ctrlia» y, con el gestor delante, los comandos de sesión."""
		if self.herramientas_ctrlia is None or self.herramientas_ctrlia.retirada:
			return []
		from app.services.herramientas_propias import NOMBRE_SERVIDOR

		nombres = [NOMBRE_SERVIDOR]
		if self.herramientas_sesiones is not None:
			from app.services import herramientas_sesiones

			nombres.append(herramientas_sesiones.NOMBRE_SERVIDOR)
		return nombres

	def _reglas_en_proceso(self) -> list[str]:
		"""Reglas de ``--allowedTools`` de los servidores «en proceso»."""
		from app.services import herramientas_sesiones

		reglas: list[str] = []
		for nombre in self._servidores_en_proceso():
			if nombre == herramientas_sesiones.NOMBRE_SERVIDOR:
				reglas += [f"mcp__{nombre}__{herramienta}" for herramienta in herramientas_sesiones.SIN_PREGUNTAR]
			else:
				reglas.append("mcp__" + nombre)
		return reglas

	def _atencion_mcp(self, servidor: Any, mensaje: Any) -> Callable[[], dict[str, Any] | None] | None:
		"""Quién atiende un mensaje de ese servidor «en proceso»; ``None`` si Ctrl+IA no lo tiene."""
		from app.services.herramientas_propias import NOMBRE_SERVIDOR

		ctrlia, sesiones = self.herramientas_ctrlia, self.herramientas_sesiones
		if ctrlia is None:
			return None
		if servidor == NOMBRE_SERVIDOR:
			return lambda: ctrlia.atender(mensaje)
		if sesiones is not None:
			from app.services import herramientas_sesiones

			if servidor == herramientas_sesiones.NOMBRE_SERVIDOR:
				return lambda: sesiones.atender(mensaje, self._uso_herramienta_ctrlia)
		return None

	def _al_mcp(self, proceso: procesos.ProcesoLineas, identificador: str, peticion: dict[str, Any]) -> None:
		"""Mensaje JSON-RPC para un servidor MCP «en proceso» de Ctrl+IA (``mcp_message``)."""
		mensaje = peticion.get("message")
		atender = self._atencion_mcp(peticion.get("server_name"), mensaje)
		if atender is None:
			log.info("Claude Code habla con un servidor MCP «en proceso» que Ctrl+IA no tiene: %s", peticion.get("server_name"))
			proceso.escribir_json({"type": "control_response", "response": {"subtype": "error", "request_id": identificador,
				"error": "Servidor MCP desconocido"}})
			return

		def responder() -> None:
			respuesta = atender() or _ACUSE_MCP
			proceso.escribir_json({"type": "control_response", "response": {"subtype": "success", "request_id": identificador,
				"response": {"mcp_response": respuesta}}})

		if isinstance(mensaje, dict) and mensaje.get("method") == "tools/call":
			# En otro hilo: el lector de Claude Code no puede quedarse esperando a la herramienta.
			threading.Thread(target=responder, name="claude-mcp-ctrlia", daemon=True).start()
		else:
			responder()

	def _turno_espontaneo(self, vivo: _ProcesoClaude, linea: str) -> _Turno:
		"""Turno para lo que llega entre mensajes. Solo es un turno de verdad si Claude se pone a trabajar."""
		datos = procesos.json_o_nada(linea) or {}
		tipo = datos.get("type")
		trabaja = tipo in ("assistant", "stream_event", "control_request") or (
			tipo == "system" and datos.get("subtype") == "session_state_changed" and datos.get("state") in ("running", "requires_action"))
		turno = _Turno(vivo)
		if not trabaja:
			# Una respuesta de control, «inactiva»...: se lee con un turno ya cerrado, que no emite el fin.
			turno.terminado = True
			return turno
		vivo.cancelar_temporizador()
		turno.mensaje_escrito = True  # no lo abrió un mensaje: «Enviar ahora» puede interrumpirlo
		self._turno = turno
		log.info("Claude Code se ha puesto a trabajar por su cuenta (por ejemplo, al acabar una tarea en segundo plano)")
		self._cambiar_estado(Estado.TRABAJANDO)
		return turno

	def _al_linea(self, turno: _Turno, linea: str) -> None:
		datos = procesos.json_o_nada(linea)
		if datos is None:
			if "unknown option" in linea:
				self._emitir(TipoEvento.ERROR, "Tu versión de Claude Code es demasiado antigua. Actualízala escribiendo «claude update» en una consola (en el equipo donde está Claude Code) y pulsa F5 en «Agentes instalados».")
			return
		tipo = datos.get("type")
		if tipo == "control_response":
			self._al_respuesta_control(turno, datos)
		elif tipo == "control_request":
			self._al_peticion_control(turno, datos)
		elif tipo == "control_cancel_request":
			solicitud = str(datos.get("request_id") or "")
			with self._candado:
				self._solicitudes.pop(solicitud, None)
			self._emitir(TipoEvento.PERMISO_RETIRADO, id=solicitud)
		elif tipo == "system":
			self._al_sistema(turno, datos)
		elif tipo == "stream_event":
			self._al_parcial(turno, datos)
		elif tipo == "assistant":
			self._al_asistente(turno, datos)
		elif tipo == "user":
			self._al_usuario(turno, datos)
		elif tipo == "result":
			self._al_resultado(turno, datos)
		elif tipo == "rate_limit_event":
			self._al_limites(datos)

	def _al_respuesta_control(self, turno: _Turno, datos: dict[str, Any]) -> None:
		respuesta = datos.get("response") or {}
		if respuesta.get("request_id") != "ctrlia-inicio":
			return
		cuerpo = respuesta.get("response") or {}
		estilos = cuerpo.get("available_output_styles")
		if isinstance(estilos, list) and all(isinstance(e, str) for e in estilos):
			self._proveedor._estilos = [e for e in estilos if _RE_ESTILO.match(e)]  # noqa: SLF001 - caché del proveedor
		comandos = _extraer_comandos(cuerpo)
		if comandos:
			self._proveedor._comandos = comandos  # noqa: SLF001 - caché compartida del proveedor
			self._emitir(TipoEvento.COMANDOS, comandos=comandos)
		turno.inicializado.set()

	def _al_sistema(self, turno: _Turno, datos: dict[str, Any]) -> None:
		subtipo = datos.get("subtype")
		if subtipo == "init":
			origen = str(datos.get("apiKeySource") or "none")
			if origen not in ("none", "") and not self._proveedor.permitir_clave_api:
				turno.abortado = "clave_api"
				self._emitir(TipoEvento.ERROR,
					"Claude Code iba a usar una clave de API y podría cobrarte por uso. Se ha detenido. "
					"Si de verdad quieres usarla, actívalo en Preferencias, Agentes.")
				if turno.proceso is not None:
					turno.proceso.terminar()
				return
			turno.sesion_iniciada = True
			sesion = str(datos.get("session_id") or "")
			if sesion and _RE_ID.match(sesion):
				self.ajustes.id_externo = sesion
			self._sesion_creada = True
			self.ajustes.bifurcar_de = ""
			self._init = datos
			for servidor in datos.get("mcp_servers") or []:
				if isinstance(servidor, dict) and servidor.get("name"):
					nombre_servidor = str(servidor["name"])
					self._servidores_mcp[_nombre_normalizado(nombre_servidor)] = nombre_servidor
			self._emitir(TipoEvento.INICIADO, id_externo=self.ajustes.id_externo,
				modelo=str(datos.get("model") or ""), modo=str(datos.get("permissionMode") or ""))
			self._vigilar_mcp(turno.vivo, datos.get("mcp_servers"))
		elif subtipo == "session_state_changed":
			turno.vio_estados = True
			if turno.vivo is not None:
				turno.vivo.vio_estados = True
			estado = datos.get("state")
			if estado == "running":
				self._cambiar_estado(Estado.TRABAJANDO)
			elif estado == "requires_action":
				self._cambiar_estado(Estado.ESPERANDO_USUARIO)
			elif estado == "idle":
				self._comprobar_fin(turno)
		elif subtipo == "background_tasks_changed":
			tareas = set()
			for tarea in datos.get("tasks") or []:
				if isinstance(tarea, dict) and not tarea.get("ambient"):
					tareas.add(str(tarea.get("task_id")))
			turno.tareas_fondo = tareas
			if tareas:
				self._cambiar_estado(Estado.SEGUNDO_PLANO)
		elif subtipo == "compact_boundary":
			meta = datos.get("compactMetadata") or datos.get("compact_metadata") or {}
			antes, despues = meta.get("preTokens"), meta.get("postTokens")
			texto = "Claude ha compactado la conversación para liberar contexto."
			if antes and despues:
				texto += f" Pasó de {antes} a {despues} unidades de texto (tokens)."
			self._emitir(TipoEvento.AVISO, texto, compactado=True)
		elif subtipo == "permission_denied":
			herramienta = str(datos.get("tool_name") or "")
			self._emitir(TipoEvento.AVISO, f"Acción denegada: {herramienta}. {datos.get('message') or ''}".strip())
		elif subtipo == "api_retry":
			intento = datos.get("attempt")
			self._emitir(TipoEvento.AVISO, f"Problema de conexión con el servicio; reintentando (intento {intento}).")

	def _al_parcial(self, turno: _Turno, datos: dict[str, Any]) -> None:
		if datos.get("parent_tool_use_id"):
			return
		evento = datos.get("event") or {}
		if evento.get("type") == "message_start":
			turno.textos.setdefault("__actual__", "")
			turno.textos["__id__"] = str((evento.get("message") or {}).get("id") or "")
		elif evento.get("type") == "content_block_delta":
			delta = evento.get("delta") or {}
			if delta.get("type") == "text_delta" and delta.get("text"):
				identificador = turno.textos.get("__id__", "")
				turno.parciales[identificador] = turno.parciales.get(identificador, "") + str(delta["text"])
				self._emitir(TipoEvento.TEXTO_PARCIAL, str(delta["text"]), id=identificador)

	def _al_asistente(self, turno: _Turno, datos: dict[str, Any]) -> None:
		if datos.get("parent_tool_use_id"):
			return
		mensaje = datos.get("message") or {}
		identificador = str(mensaje.get("id") or uuid.uuid4().hex)
		for bloque in mensaje.get("content") or []:
			if not isinstance(bloque, dict):
				continue
			tipo = bloque.get("type")
			if tipo == "text" and bloque.get("text"):
				if turno.rescatados.get(identificador, "").strip() == str(bloque["text"]).strip():
					# Ya se rescató de los trozos al llegar una pregunta: no se repite.
					turno.rescatados.pop(identificador, None)
					continue
				texto = turno.textos.get(identificador, "")
				texto = (texto + "\n\n" if texto else "") + str(bloque["text"])
				turno.textos[identificador] = texto
				turno.texto_reciente = texto
				self._emitir(TipoEvento.MENSAJE, texto, id=identificador)
			elif tipo == "tool_use":
				nombre = str(bloque.get("name") or "")
				entrada = bloque.get("input") if isinstance(bloque.get("input"), dict) else {}
				id_herramienta = str(bloque.get("id") or "")
				turno.herramientas[id_herramienta] = nombre
				if nombre in ("ScheduleWakeup", "CronCreate", "CronDelete"):
					self._al_programar(turno, nombre, entrada)
				if nombre == "TodoWrite":
					pasos = [(str(t.get("content") or ""), str(t.get("status") or "")) for t in entrada.get("todos") or [] if isinstance(t, dict)]
					self._emitir(TipoEvento.PLAN, pasos=pasos)
					continue
				self._emitir(TipoEvento.HERRAMIENTA, describir_herramienta(nombre, entrada),
					herramienta=nombre, detalle=_detalle_entrada(entrada), id=id_herramienta)
		uso = mensaje.get("usage")
		if isinstance(uso, dict):
			usado = sum(int(uso.get(clave) or 0) for clave in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
			if usado:
				self._emitir(TipoEvento.USO, contexto_usado=usado, contexto_total=turno.contexto_total or None)

	def _al_programar(self, turno: _Turno, nombre: str, entrada: dict[str, Any]) -> None:
		"""Claude programa trabajo para más tarde dentro de su proceso: que no se cierre antes (AIChat #233)."""
		vivo = turno.vivo
		if vivo is None:
			return
		minutos = self._proveedor.minutos_abierto()
		if nombre == "ScheduleWakeup":
			if entrada.get("stop"):
				vivo.despertar = 0.0
				return
			try:
				segundos = min(max(float(entrada.get("delaySeconds") or 0), 60.0), 3600.0)
			except (TypeError, ValueError):
				return
			vivo.despertar = max(vivo.despertar, time.time() + segundos)
			texto = f"Claude ha programado volver a esto dentro de {round(segundos / 60)} minutos."
		elif nombre == "CronCreate":
			vivo.tareas_cron += 1
			texto = "Claude ha programado una tarea que se repite."
		else:
			vivo.tareas_cron = max(0, vivo.tareas_cron - 1)
			return
		if minutos <= 0:
			texto += (" No llegará a hacerse: Claude Code se cierra al acabar cada mensaje (Preferencias, Agentes, "
				"«Mantener Claude Code abierto entre mensajes»).")
		else:
			texto += " Ctrl+IA mantiene Claude Code abierto hasta entonces."
		self._emitir(TipoEvento.AVISO, texto, programado=True)

	def _al_usuario(self, turno: _Turno, datos: dict[str, Any]) -> None:
		if datos.get("parent_tool_use_id"):
			return
		mensaje = datos.get("message") or {}
		contenido = mensaje.get("content")
		if not isinstance(contenido, list):
			return
		for bloque in contenido:
			if not isinstance(bloque, dict) or bloque.get("type") != "tool_result":
				continue
			turno.texto_reciente = ""
			id_herramienta = str(bloque.get("tool_use_id") or "")
			nombre = turno.herramientas.get(id_herramienta, "")
			salida = bloque.get("content")
			if isinstance(salida, list):
				salida = "\n".join(str(parte.get("text") or "") for parte in salida if isinstance(parte, dict))
			self._emitir(TipoEvento.HERRAMIENTA_FIN, herramienta=nombre, salida=str(salida or "")[:4000],
				error=bool(bloque.get("is_error")), id=id_herramienta)
		resultado = datos.get("tool_use_result")
		if isinstance(resultado, dict) and resultado.get("structuredPatch"):
			ruta = str(resultado.get("filePath") or "")
			lineas = []
			for trozo in resultado.get("structuredPatch") or []:
				if isinstance(trozo, dict):
					lineas.append(f"@@ -{trozo.get('oldStart')},{trozo.get('oldLines')} +{trozo.get('newStart')},{trozo.get('newLines')} @@")
					lineas.extend(str(linea) for linea in trozo.get("lines") or [])
			tipo = "nuevo" if resultado.get("type") == "create" else "modificado"
			self._emitir(TipoEvento.ARCHIVO, ruta, ruta=ruta, tipo=tipo, diff="\n".join(lineas))

	def _al_resultado(self, turno: _Turno, datos: dict[str, Any]) -> None:
		turno.resultados_pendientes = max(0, turno.resultados_pendientes - 1)
		turno.ultimo_resultado = datos
		if turno.vivo is not None:
			with self._candado:
				for anadido in turno.vivo.anadidos.values():
					anadido.tarde = True  # lo que no se recogió antes de este resultado ya no entra en este trabajo
		uso = datos.get("usage") or {}
		total_contexto = 0
		for info in (datos.get("modelUsage") or {}).values():
			if isinstance(info, dict) and info.get("contextWindow"):
				total_contexto = max(total_contexto, int(info["contextWindow"]))
		turno.contexto_total = total_contexto or turno.contexto_total
		if turno.vivo is not None:
			turno.vivo.contexto_total = turno.contexto_total
		self._emitir(TipoEvento.USO, entrada=int(uso.get("input_tokens") or 0), salida=int(uso.get("output_tokens") or 0),
			contexto_total=turno.contexto_total or None, coste=datos.get("total_cost_usd"))
		for denegacion in datos.get("permission_denials") or []:
			if isinstance(denegacion, dict):
				nombre = str(denegacion.get("tool_name") or "")
				entrada = denegacion.get("tool_input") if isinstance(denegacion.get("tool_input"), dict) else {}
				self._emitir(TipoEvento.AVISO, "No se permitió: " + describir_herramienta(nombre, entrada))
		interrumpido_a_proposito = datos.get("subtype") == "error_during_execution" and (turno.interrumpido or turno.interrupciones > 0)
		if interrumpido_a_proposito and turno.interrupciones > 0:
			turno.interrupciones -= 1
		if datos.get("is_error") and not interrumpido_a_proposito:
			errores = datos.get("errors") or []
			texto = str(datos.get("result") or "") or "; ".join(str(e) for e in errores) or "Claude terminó con un error."
			version = _RE_VERSION_INSUFICIENTE.search(texto)
			if _RE_SIN_TRANSCRIPCION.search(texto):
				self._emitir(TipoEvento.ERROR, TEXTO_SIN_TRANSCRIPCION, sin_transcripcion=True)
				texto = ""
			elif "not logged in" in texto.lower() or "/login" in texto:
				texto = ("Claude Code no tiene la sesión iniciada en este equipo. Ve a Herramientas, Agentes instalados, "
					"elige Claude Code y pulsa «Iniciar sesión».")
			elif version:
				texto = (f"Tu Claude Code (versión {version.group(1)}) es demasiado antiguo para este modelo: hace falta la "
					f"{version.group(2)} o posterior. Actualízalo escribiendo «claude update» en una consola y vuelve a "
					"enviar el mensaje, o elige otro modelo en «Opciones de la sesión» (Ctrl+Mayús+O). Si no se actualiza "
					"solo, puede que tengas desactivadas sus actualizaciones automáticas.")
			if texto:
				self._emitir(TipoEvento.ERROR, texto, sin_sesion="/login" in str(datos.get("result") or ""))
		if not turno.vio_estados:
			# CLI sin eventos de estado: el resultado marca el final del turno.
			self._comprobar_fin(turno, forzar=True)
		elif turno.temporizador is None:
			turno.temporizador = threading.Timer(ESPERA_TRAS_RESULTADO, self._forzar_fin, args=(turno,))
			turno.temporizador.daemon = True
			turno.temporizador.start()

	def _al_limites(self, datos: dict[str, Any]) -> None:
		info = datos.get("rate_limit_info") or {}
		ventanas = info.get("unifiedWindows") or {}
		for clave, nombre in (("five_hour", "las próximas horas"), ("seven_day", "la semana")):
			ventana = ventanas.get(clave) or {}
			uso = ventana.get("utilization")
			if uso is None:
				continue
			porcentaje = round(float(uso) * 100) if float(uso) <= 1 else round(float(uso))
			self._emitir(TipoEvento.LIMITES, f"Has usado el {porcentaje} % del límite de {nombre}.",
				porcentaje=porcentaje, ventana=clave, reinicio=ventana.get("resetsAt"))

	def _comprobar_fin(self, turno: _Turno, forzar: bool = False) -> None:
		if turno.terminado:
			return
		if not forzar and (turno.resultados_pendientes > 0 or turno.tareas_fondo):
			if turno.tareas_fondo:
				self._cambiar_estado(Estado.SEGUNDO_PLANO)
			return
		if turno.temporizador is not None:
			turno.temporizador.cancel()
		vivo = turno.vivo
		if (vivo is not None and vivo.vivo() and vivo is self._vivo and self._proveedor.minutos_abierto() > 0
				and not turno.abortado and not self._cerrada):
			self._cerrar_turno(turno)  # el proceso sigue abierto para el mensaje siguiente
			return
		if turno.proceso is not None:
			turno.proceso.cerrar_entrada()

	def _programar_cierre(self, vivo: _ProcesoClaude | None) -> None:
		"""Cierra el proceso si pasa el tiempo de Preferencias sin ningún mensaje."""
		minutos = self._proveedor.minutos_abierto()
		if vivo is None or minutos <= 0 or not vivo.vivo():
			return
		vivo.cancelar_temporizador()
		vivo.temporizador = threading.Timer(minutos * SEGUNDOS_POR_MINUTO, self._cerrar_sin_uso, args=(vivo,))
		vivo.temporizador.daemon = True
		vivo.temporizador.start()

	def _cerrar_sin_uso(self, vivo: _ProcesoClaude) -> None:
		with self._candado:
			turno = self._turno
			if vivo is not self._vivo or (turno is not None and not turno.terminado and turno.vivo is vivo):
				return
		ahora = time.time()
		if vivo.tiene_programado(ahora):
			# Lo programado vive en este proceso: se espera a que pase (más un margen) antes de cerrarlo.
			espera = max(vivo.despertar + MARGEN_PROGRAMADO - ahora, self._proveedor.minutos_abierto() * SEGUNDOS_POR_MINUTO)
			log.info("Claude Code tiene trabajo programado; se mantiene abierto %.0f s más", espera)
			vivo.temporizador = threading.Timer(espera, self._cerrar_sin_uso, args=(vivo,))
			vivo.temporizador.daemon = True
			vivo.temporizador.start()
			return
		log.info("Claude Code llevaba un rato sin mensajes; se cierra hasta el siguiente")
		self._retirar_proceso(vivo)

	def _retirar_proceso(self, vivo: _ProcesoClaude) -> None:
		"""Deja de usar un proceso entre turnos: se le cierra la entrada y, si no termina solo, se termina."""
		with self._candado:
			if self._vivo is vivo:
				self._vivo = None
		vivo.cancelar_temporizador()
		proceso = vivo.proceso
		if proceso is None:
			return

		def cerrar() -> None:
			proceso.cerrar_entrada()
			if not proceso.esperar_fin(ESPERA_RETIRAR):
				proceso.terminar()

		threading.Thread(target=cerrar, name="claude-retirar", daemon=True).start()

	def _forzar_fin(self, turno: _Turno) -> None:
		if turno.terminado or turno.proceso is None:
			return
		log.info("Claude no terminó a tiempo; se cierra el proceso.")
		turno.proceso.cerrar_entrada()
		if not turno.proceso.esperar_fin(3):
			turno.proceso.terminar()

	def _al_salir_de(self, vivo: _ProcesoClaude, codigo: int | None) -> None:
		with self._candado:
			vivo.terminado = True
			if self._vivo is vivo:
				self._vivo = None
			turno = self._turno
			sin_recoger = list(vivo.anadidos.values())
			vivo.anadidos.clear()
			cerrada = self._cerrada
		vivo.cancelar_temporizador()
		for anadido in sin_recoger:
			if cerrada:
				# Es la persona quien cierra la sesión o el programa: no hay a quién avisar.
				log.info("Conversación cerrada con un mensaje añadido al turno sin confirmar")
				continue
			self._emitir(TipoEvento.AVISO, "Claude Code se ha cerrado sin confirmar que recogiera tu mensaje. " + NOTA_SIN_CONFIRMAR,
				sin_confirmar=anadido.texto)
		if turno is not None and turno.vivo is vivo and not turno.terminado:
			self._cerrar_turno(turno, codigo, salio=True)
		else:
			log.debug("Claude Code se cerró entre dos mensajes (código %s)", codigo)

	def _al_salir(self, turno: _Turno, codigo: int | None) -> None:
		self._cerrar_turno(turno, codigo, salio=True)

	def _cerrar_turno(self, turno: _Turno, codigo: int | None = None, salio: bool = False) -> None:
		"""Fin del turno: avisa, deja la sesión inactiva y lanza lo que esperaba en la cola.

		:param salio: el proceso ha terminado (si no, sigue abierto para el siguiente mensaje).
		"""
		with self._candado:
			if turno.terminado:
				return
			turno.terminado = True
			if turno.temporizador is not None:
				turno.temporizador.cancel()
			cola = self._desencolar() if self._cola and not self._cerrada and not turno.abortado else None
		self._retirar_solicitudes()
		resultado = turno.ultimo_resultado or {}
		correcto = bool(resultado) and not resultado.get("is_error") and not turno.abortado
		if not resultado and not turno.abortado and not turno.interrumpido:
			errores = turno.proceso.errores_recientes(5) if turno.proceso else []
			detalle = " ".join(errores)[-500:]
			if _RE_SIN_TRANSCRIPCION.search(detalle):
				self._emitir(TipoEvento.ERROR, TEXTO_SIN_TRANSCRIPCION, sin_transcripcion=True)
				texto = ""
			elif "unknown option" in detalle:
				texto = "Tu versión de Claude Code es demasiado antigua. Actualízala escribiendo «claude update» en una consola (en el equipo donde está Claude Code) y pulsa F5 en «Agentes instalados»."
			elif "login" in detalle.lower() or "auth" in detalle.lower():
				texto = "Claude Code necesita que inicies sesión. Usa «Iniciar sesión» en «Agentes instalados»."
			else:
				texto = f"Claude Code se cerró sin responder (código {codigo})."
				if detalle:
					texto += f" Detalle: {detalle}"
			if texto:
				self._emitir(TipoEvento.ERROR, texto)
		self._emitir(TipoEvento.FIN_TURNO, correcto=correcto, interrumpido=turno.interrumpido,
			resumen=str(resultado.get("result") or ""))
		self._cambiar_estado(Estado.ERROR if (not correcto and not turno.interrumpido and not resultado) else Estado.INACTIVA)
		if cola is not None:
			with self._candado:
				if not self._cerrada:
					self._lanzar_turno(*cola)
		elif not salio:
			self._programar_cierre(turno.vivo)
			self._reintentar_mcp_en_reposo(turno.vivo)

	# -- Permisos ---------------------------------------------------------------

	def _al_peticion_control(self, turno: _Turno, datos: dict[str, Any]) -> None:
		peticion = datos.get("request") or {}
		identificador = str(datos.get("request_id") or "")
		with self._candado:
			repetida = bool(identificador) and identificador in self._solicitudes
		if repetida:
			# Claude Code puede volver a entregar una petición que sigue pendiente: se muestra una sola vez.
			log.info("Petición de Claude Code repetida (%s): ya está pendiente", identificador)
			return
		if peticion.get("subtype") == "elicitation" and identificador:
			self._al_elicitacion(turno, identificador, peticion)
			return
		if peticion.get("subtype") != "can_use_tool" or not identificador:
			log.info("Petición de control de Claude Code que Ctrl+IA no atiende: %s", peticion.get("subtype"))
			if turno.proceso is not None:
				turno.proceso.escribir_json({"type": "control_response", "response": {"subtype": "error",
					"request_id": identificador, "error": "Ctrl+IA no admite esta petición"}})
			return
		nombre = str(peticion.get("tool_name") or "")
		entrada = peticion.get("input") if isinstance(peticion.get("input"), dict) else {}
		sugerencias = peticion.get("permission_suggestions") or []
		admite_regla = _admite_regla(peticion)
		# En «Solo planificar» Claude Code pregunta adrede aunque haya regla: esa pregunta no se salta.
		if nombre not in HERRAMIENTAS_SIN_REGLA and admite_regla and self.ajustes.modo_permiso != "plan":
			regla = self._regla_que_cubre(nombre, entrada)
			if regla and turno.proceso is not None:
				# Llegó mientras la persona concedía esa regla (Claude ya había preguntado): vale su decisión.
				log.info("Petición de %s permitida por la regla «%s» que ya se había concedido", nombre, regla)
				turno.proceso.escribir_json({"type": "control_response", "response": {"subtype": "success",
					"request_id": identificador, "response": {"behavior": "allow", "updatedInput": entrada}}})
				return
		with self._candado:
			self._solicitudes[identificador] = {"herramienta": nombre, "entrada": entrada, "sugerencias": sugerencias,
				"admite_regla": admite_regla}
		if nombre == "AskUserQuestion":
			solicitud = self._solicitud_pregunta(identificador, entrada)
		elif nombre == "ExitPlanMode":
			solicitud = SolicitudPermiso(
				identificador, TipoSolicitud.PLAN, "Claude te propone un plan",
				detalle=str(entrada.get("plan") or ""), herramienta=nombre, plan=str(entrada.get("plan") or ""),
				opciones=[
					OpcionPermiso("aprobar_editar", "Aprobar y aceptar las ediciones", ClaseOpcion.PERMITIR_SESION,
						"Claude empieza a trabajar y modifica archivos sin preguntar cada vez."),
					OpcionPermiso("aprobar", "Aprobar y preguntar cada cambio", ClaseOpcion.PERMITIR,
						"Claude empieza a trabajar pero sigue pidiendo permiso para cada acción."),
					OpcionPermiso("seguir", "Seguir planificando", ClaseOpcion.DENEGAR,
						"Claude no hace nada todavía; puedes escribirle qué cambiar del plan."),
				],
			)
		else:
			solicitud = self._solicitud_herramienta(identificador, peticion, nombre, entrada, sugerencias)
		solicitud.contexto = self._texto_antes_de_preguntar(turno)
		self._cambiar_estado(Estado.ESPERANDO_USUARIO)
		self._emitir(TipoEvento.PERMISO, solicitud.titulo, solicitud=solicitud)

	def _solicitud_herramienta(self, identificador: str, peticion: dict[str, Any], nombre: str, entrada: dict[str, Any],
			sugerencias: list[Any]) -> SolicitudPermiso:
		"""Permiso para una herramienta: una vez, en la sesión, siempre en la carpeta y, si es MCP, el servidor entero."""
		descripcion = str(peticion.get("description") or "")
		detalle = describir_herramienta(nombre, entrada)
		if descripcion:
			detalle += "\n\nMotivo: " + descripcion
		mcp = _partes_mcp(nombre)
		servidor = self._servidor_visible(mcp[0]) if mcp else ""
		visible = str(peticion.get("display_name") or "") or (mcp[1] if mcp else NOMBRES_HERRAMIENTAS.get(nombre, nombre))
		if mcp:
			titulo = f"Claude quiere usar «{visible}» del servidor MCP «{servidor}»"
			detalle += f"\n\nServidor MCP: {servidor}\nHerramienta: {mcp[1]}"
		else:
			titulo = f"Claude quiere usar «{visible}»"
		detalle += "\n\nCarpeta: " + str(self.ajustes.carpeta) + "\n\n" + _detalle_entrada(entrada)
		if mcp and self.ajustes.modo_permiso == "plan":
			detalle += ("\n\nEn el modo «Solo planificar», Claude Code pregunta siempre por las herramientas MCP que "
				"pueden cambiar algo, aunque las hayas permitido antes.")
		opciones = [OpcionPermiso("permitir", "Permitir una vez", ClaseOpcion.PERMITIR)]
		if not _admite_regla(peticion):
			# Claude Code volvería a preguntar igual (una regla «preguntar» o una herramienta que exige a la persona).
			opciones.append(OpcionPermiso("denegar", "Denegar", ClaseOpcion.DENEGAR))
			return SolicitudPermiso(identificador, TipoSolicitud.HERRAMIENTA, titulo, detalle=detalle,
				herramienta=nombre, opciones=opciones, datos={"entrada": entrada, "servidor_mcp": mcp[0] if mcp else ""})
		carpetas = _carpetas_de_sugerencias(sugerencias, remoto.es_remota(self.ajustes.extra))
		ayuda_sesion = "No volverá a preguntar por esta misma acción mientras dure esta sesión."
		if carpetas:
			lista = ", ".join(carpetas)
			ayuda_sesion += f" Además, Claude podrá trabajar en {lista} como en la carpeta de la sesión."
			detalle += f"\n\nSi lo permites durante esta sesión, Claude podrá trabajar también en: {lista}"
		opciones.append(OpcionPermiso("permitir_sesion", "Permitir durante esta sesión", ClaseOpcion.PERMITIR_SESION,
			ayuda_sesion))
		if mcp:
			opciones.append(OpcionPermiso("permitir_servidor_sesion", f"Permitir todo «{servidor}» durante esta sesión",
				ClaseOpcion.PERMITIR_SESION,
				f"Todas las herramientas del servidor MCP «{servidor}», sin preguntar, mientras dure esta sesión."))
		if _destino_persistente(sugerencias) and _reglas_de_sugerencias(sugerencias):
			opciones.append(OpcionPermiso("permitir_siempre", "Permitir siempre en esta carpeta", ClaseOpcion.PERMITIR_SIEMPRE,
				"Claude Code lo apunta en el archivo .claude\\settings.local.json de la carpeta y no volverá a preguntar "
				"por esto en ninguna conversación de esa carpeta. Se quita en Herramientas, Configuración de los agentes."))
			if mcp:
				opciones.append(OpcionPermiso("permitir_servidor_siempre", f"Permitir todo «{servidor}» siempre en esta carpeta",
					ClaseOpcion.PERMITIR_SIEMPRE,
					f"Todas las herramientas del servidor MCP «{servidor}», sin preguntar, en todas las conversaciones de "
					"esta carpeta. Claude Code lo apunta en .claude\\settings.local.json."))
		if any(isinstance(s, dict) and s.get("type") == "setMode" and s.get("mode") == "acceptEdits" for s in sugerencias):
			opciones.append(OpcionPermiso("aceptar_ediciones", "Permitir y aceptar todas las ediciones",
				ClaseOpcion.PERMITIR_SESION, "Cambia la sesión al modo «Aceptar las ediciones de archivos»."))
		opciones.append(OpcionPermiso("denegar", "Denegar", ClaseOpcion.DENEGAR))
		return SolicitudPermiso(identificador, TipoSolicitud.HERRAMIENTA, titulo, detalle=detalle,
			herramienta=nombre, opciones=opciones, datos={"entrada": entrada, "servidor_mcp": mcp[0] if mcp else ""})

	def _servidor_visible(self, normalizado: str) -> str:
		"""Nombre del servidor MCP como lo muestra Claude Code («claude.ai Gmail»), o el de la herramienta."""
		return self._servidores_mcp.get(normalizado, normalizado)

	def _regla_que_cubre(self, nombre: str, entrada: dict[str, Any]) -> str:
		"""La regla concedida en esta conversación que ya permite esta acción, o ``""``."""
		with self._candado:
			reglas = [*self.ajustes.reglas_permitidas, *self._reglas_siempre]
		return next((regla for regla in reglas if regla_cubre(regla, nombre, entrada)), "")

	def _al_elicitacion(self, turno: _Turno, identificador: str, peticion: dict[str, Any]) -> None:
		"""Un servidor MCP pide datos, un enlace o una confirmación (``elicitation``)."""
		servidor = str(peticion.get("display_name") or peticion.get("mcp_server_name") or "")
		descripcion = " ".join(str(peticion.get(c) or "") for c in ("title", "description")).strip()
		solicitud = elicitacion.solicitud_elicitacion(
			identificador, servidor, str(peticion.get("message") or ""),
			peticion.get("mode") if isinstance(peticion.get("mode"), str) else None,
			url=str(peticion.get("url") or ""), esquema=peticion.get("requested_schema"),
			agente="Claude", descripcion=descripcion,
		)
		with self._candado:
			self._solicitudes[identificador] = {"herramienta": HERRAMIENTA_ELICITACION, "entrada": {}, "sugerencias": [],
				"solicitud": solicitud}
		solicitud.contexto = self._texto_antes_de_preguntar(turno)
		self._cambiar_estado(Estado.ESPERANDO_USUARIO)
		self._emitir(TipoEvento.PERMISO, solicitud.titulo, solicitud=solicitud)

	def _texto_antes_de_preguntar(self, turno: _Turno) -> str:
		"""Lo que Claude escribió justo antes de esta petición (un borrador que pide revisar...).

		A veces el bloque de texto completo no llega (ni queda en el historial de
		Claude Code) cuando a continuación hay una pregunta: si se recibió en
		trozos, se rescata de ahí y se guarda como mensaje para no perderlo.
		"""
		identificador = turno.textos.get("__id__", "")
		parcial = turno.parciales.get(identificador, "").strip()
		if identificador and parcial and not turno.textos.get(identificador):
			turno.textos[identificador] = parcial
			turno.rescatados[identificador] = parcial
			turno.texto_reciente = parcial
			self._emitir(TipoEvento.MENSAJE, parcial, id=identificador, rescatado=True)
		return turno.texto_reciente

	def _solicitud_pregunta(self, identificador: str, entrada: dict[str, Any]) -> SolicitudPermiso:
		preguntas = []
		for numero, pregunta in enumerate(entrada.get("questions") or []):
			if not isinstance(pregunta, dict):
				continue
			validas = [o for o in pregunta.get("options") or [] if isinstance(o, dict)]
			opciones = [(str(o.get("label") or ""), str(o.get("description") or "")) for o in validas]
			# «preview»: lo que Claude quiere que veas con esa opción (un borrador, código, un esquema).
			previas = [str(o.get("preview") or "") for o in validas]
			preguntas.append(Pregunta(
				id=str(pregunta.get("question") or numero), texto=str(pregunta.get("question") or ""),
				encabezado=str(pregunta.get("header") or ""), opciones=opciones,
				multiple=bool(pregunta.get("multiSelect")),
				vistas_previas=previas if any(p.strip() for p in previas) else [],
			))
		return SolicitudPermiso(
			identificador, TipoSolicitud.PREGUNTA, "Claude te hace una pregunta", herramienta="AskUserQuestion",
			preguntas=preguntas,
			opciones=[
				OpcionPermiso("responder", "Responder", ClaseOpcion.PERMITIR),
				OpcionPermiso("no_responder", "No responder", ClaseOpcion.DENEGAR),
			],
			datos={"entrada": entrada},
		)

	def _cuerpo_respuesta(self, pendiente: dict[str, Any], respuesta: RespuestaPermiso) -> dict[str, Any]:
		nombre = pendiente["herramienta"]
		entrada = pendiente["entrada"]
		opcion = respuesta.opcion
		if nombre == HERRAMIENTA_ELICITACION:
			# Claude Code solo admite ``action`` y ``content``.
			return elicitacion.resultado_elicitacion(pendiente["solicitud"], respuesta, con_meta=False)
		if nombre == "AskUserQuestion":
			if opcion != "responder":
				return {"behavior": "deny", "message": respuesta.mensaje or "El usuario no ha querido responder."}
			respuestas = {pregunta: ", ".join(valores) for pregunta, valores in respuesta.respuestas.items()}
			return {"behavior": "allow", "updatedInput": {**entrada, "answers": respuestas}}
		if nombre == "ExitPlanMode":
			if opcion == "aprobar_editar":
				self.ajustes.modo_permiso = "acceptEdits"
				return {"behavior": "allow", "updatedInput": entrada,
					"updatedPermissions": [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]}
			if opcion == "aprobar":
				self.ajustes.modo_permiso = "default"
				return {"behavior": "allow", "updatedInput": entrada,
					"updatedPermissions": [{"type": "setMode", "mode": "default", "destination": "session"}]}
			return {"behavior": "deny", "message": respuesta.mensaje or "Sigue planificando; el usuario aún no aprueba el plan."}
		if opcion == "permitir":
			return {"behavior": "allow", "updatedInput": entrada}
		if opcion == "aceptar_ediciones":
			self.ajustes.modo_permiso = "acceptEdits"
			return {"behavior": "allow", "updatedInput": entrada,
				"updatedPermissions": [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]}
		if opcion in ("permitir_sesion", "permitir_servidor_sesion"):
			carpetas: list[str] = []
			if opcion == "permitir_servidor_sesion" and _partes_mcp(nombre):
				reglas = ["mcp__" + _partes_mcp(nombre)[0]]
			else:
				reglas = _reglas_de_sugerencias(pendiente["sugerencias"]) or [_regla_para(nombre, entrada)]
				# La carpeta que sugiere con ella: sin ella, una orden que escribe allí volvería a preguntar.
				carpetas = _carpetas_de_sugerencias(pendiente["sugerencias"], remoto.es_remota(self.ajustes.extra))
			self._guardar_en_sesion([*reglas, *(PREFIJO_CARPETA + c + ")" for c in carpetas)])
			pendiente["concedidas"] = reglas
			cambios: list[dict[str, Any]] = [{
				"type": "addRules", "behavior": "allow", "destination": "session",
				"rules": [_regla_a_dict(regla) for regla in reglas],
			}]
			if carpetas:
				cambios.append({"type": "addDirectories", "directories": carpetas, "destination": "session"})
			return {"behavior": "allow", "updatedInput": entrada, "updatedPermissions": cambios}
		if opcion in ("permitir_siempre", "permitir_servidor_siempre"):
			destino = _destino_persistente(pendiente["sugerencias"]) or "localSettings"
			if opcion == "permitir_servidor_siempre" and _partes_mcp(nombre):
				reglas = ["mcp__" + _partes_mcp(nombre)[0]]
			else:
				reglas = _reglas_de_sugerencias(pendiente["sugerencias"]) or [_regla_para(nombre, entrada)]
			# Las guarda Claude Code en su archivo (no son de la sesión): se recuerdan aquí solo para las
			# peticiones de esta misma conversación que lleguen mientras tanto.
			with self._candado:
				self._reglas_siempre.update(reglas)
			pendiente["concedidas"] = reglas
			log.info("Permiso «siempre» concedido (%s): %s", destino, ", ".join(reglas))
			cambios = [{
				"type": "addRules", "behavior": "allow", "destination": destino,
				"rules": [_regla_a_dict(regla) for regla in reglas],
			}]
			carpetas = [] if opcion == "permitir_servidor_siempre" else \
				_carpetas_de_sugerencias(pendiente["sugerencias"], remoto.es_remota(self.ajustes.extra))
			if carpetas:
				# Claude Code la sugiere para la sesión (no para su archivo): se conserva en la de Ctrl+IA.
				self._guardar_en_sesion([PREFIJO_CARPETA + c + ")" for c in carpetas])
				cambios.append({"type": "addDirectories", "directories": carpetas, "destination": "session"})
			return {"behavior": "allow", "updatedInput": entrada, "updatedPermissions": cambios}
		return {"behavior": "deny", "message": respuesta.mensaje or DENEGACION_POR_DEFECTO}

	def _guardar_en_sesion(self, reglas: list[str]) -> None:
		"""Añade reglas (y ``Carpeta(...)``) a las de la sesión y al proceso abierto, que ya las tiene."""
		with self._candado:
			for regla in reglas:
				if regla not in self.ajustes.reglas_permitidas:
					self.ajustes.reglas_permitidas.append(regla)
		vivo = self._vivo
		if vivo is not None:
			# Van al proceso abierto con «updatedPermissions»: no hace falta relanzarlo por ellas.
			vivo.reglas.update(regla for regla in reglas if _RE_REGLA.match(regla))

	def _retirar_solicitudes(self) -> None:
		with self._candado:
			pendientes = list(self._solicitudes)
			self._solicitudes.clear()
		for identificador in pendientes:
			self._emitir(TipoEvento.PERMISO_RETIRADO, id=identificador)


#: Errores conocidos de «rewind_files», en español.
_ERRORES_DESHACER = {
	"file rewinding is not enabled.": "Claude Code no tiene activado guardar el estado de los archivos en esta sesión.",
}


def _resultado_deshacer(datos: dict[str, Any]) -> dict[str, Any]:
	"""El ``control_response`` de «rewind_files» en el formato de ``deshacer_archivos``."""
	respuesta = datos.get("response") if isinstance(datos.get("response"), dict) else {}
	sin_punto = "Claude Code no guardó el estado de los archivos de ese mensaje: no se puede deshacer."
	if respuesta.get("subtype") == "error":
		texto = str(respuesta.get("error") or "")
		if "no file checkpoint" in texto.lower():
			raise ValueError(sin_punto)
		raise RuntimeError("Claude Code no ha podido deshacer los cambios: " + (texto or "error sin detalle"))
	cuerpo = respuesta.get("response") if isinstance(respuesta.get("response"), dict) else {}
	error = str(cuerpo.get("error") or "")
	if "no file checkpoint" in error.lower():
		# Con «dry_run», Claude Code 2.1.238 lo dice como respuesta correcta con «canRewind» falso.
		raise ValueError(sin_punto)
	archivos = cuerpo.get("filesChanged") if isinstance(cuerpo.get("filesChanged"), list) else []

	def entero(valor: Any) -> int:
		return int(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else 0

	return {
		"canRewind": bool(cuerpo.get("canRewind")),
		"error": _ERRORES_DESHACER.get(error.lower(), error),
		"filesChanged": [str(ruta) for ruta in archivos if isinstance(ruta, str)],
		"insertions": entero(cuerpo.get("insertions")),
		"deletions": entero(cuerpo.get("deletions")),
	}


def _regla_para(herramienta: str, entrada: dict[str, Any]) -> str:
	"""Regla «permitir» para una acción concreta (``Bash(git status)``, ``Edit``...)."""
	orden = entrada.get("command")
	if herramienta in ("Bash", "PowerShell") and isinstance(orden, str) and orden.strip():
		return f"{herramienta}({orden.strip()[:500]})"
	return herramienta


def _nombre_normalizado(servidor: str) -> str:
	"""Nombre de un servidor MCP como lo pone Claude Code en sus herramientas («claude.ai Gmail» → «claude_ai_Gmail»)."""
	return re.sub(r"[^A-Za-z0-9_-]", "_", servidor)


def _partes_mcp(herramienta: str) -> tuple[str, str] | None:
	"""``("servidor", "herramienta")`` de ``mcp__servidor__herramienta``; ``None`` si no es MCP."""
	if not herramienta.startswith("mcp__"):
		return None
	servidor, _separador, nombre = herramienta[5:].partition("__")
	return (servidor, nombre) if servidor else None


def carpeta_concreta(carpeta: str, posix: bool = False) -> bool:
	"""Una ruta completa a una carpeta concreta, apta para ``--add-dir``.

	Nunca una unidad entera (``C:\\``), la raíz de un recurso compartido, la raíz
	de un Mac o Linux ni una ruta de dispositivo (``\\\\?\\``): darían mucho más que
	una carpeta (AIChat #334). Tampoco algo que se pueda leer como una opción.
	"""
	if not isinstance(carpeta, str) or not 0 < len(carpeta) <= 1024 or carpeta.startswith("-") \
			or any(c in carpeta for c in "\r\n\0"):
		return False
	if posix:
		return carpeta.startswith("/") and bool(carpeta.strip("/"))
	if carpeta.startswith(("\\\\.\\", "\\\\?\\", "//./", "//?/")):
		return False
	unidad, resto = ntpath.splitdrive(carpeta)
	return bool(unidad) and ntpath.isabs(carpeta) and bool(resto.strip("\\/"))


def carpeta_de_regla(regla: str) -> str:
	"""La ruta de una regla ``Carpeta(ruta)``, o ``""``."""
	if regla.startswith(PREFIJO_CARPETA) and regla.endswith(")"):
		return regla[len(PREFIJO_CARPETA):-1]
	return ""


def _carpetas_de_sugerencias(sugerencias: list[Any], posix: bool = False) -> list[str]:
	"""Las carpetas en las que Claude Code sugiere dejar trabajar (``addDirectories``), solo las concretas."""
	carpetas: list[str] = []
	for sugerencia in sugerencias:
		if not isinstance(sugerencia, dict) or sugerencia.get("type") != "addDirectories":
			continue
		for carpeta in sugerencia.get("directories") or []:
			if isinstance(carpeta, str) and carpeta_concreta(carpeta, posix) and carpeta not in carpetas:
				carpetas.append(carpeta)
	return carpetas


def _admite_regla(peticion: dict[str, Any]) -> bool:
	"""Si una regla «permitir» evitaría volver a preguntar por esto.

	No la admiten las herramientas que exigen a la persona en cada uso
	(``requires_user_interaction``), las que cubre una regla «preguntar»
	(``matched_ask_rule``) ni aquellas en que Claude Code no propone recordarlo
	(``suppress_always_allow_rule``).
	"""
	return not any(peticion.get(clave) for clave in ("requires_user_interaction", "matched_ask_rule", "suppress_always_allow_rule"))


def _destino_persistente(sugerencias: list[Any]) -> str:
	"""Archivo donde Claude Code guardaría su sugerencia «permitir» (``localSettings``...), o ``""``."""
	for sugerencia in sugerencias:
		if (isinstance(sugerencia, dict) and sugerencia.get("type") == "addRules" and sugerencia.get("behavior") == "allow"
				and sugerencia.get("destination") in DESTINOS_PERSISTENTES):
			return str(sugerencia["destination"])
	return ""


def regla_cubre(regla: str, herramienta: str, entrada: dict[str, Any]) -> bool:
	"""Si ``regla`` permite esta acción, con el mismo criterio que Claude Code y, en la duda, que no.

	``mcp__servidor`` y ``mcp__servidor__*`` cubren todas las herramientas de ese
	servidor; una regla sin paréntesis, la herramienta entera; ``Bash(orden)``,
	esa orden exacta, y ``Bash(prefijo:*)``, las órdenes que empiezan así sin
	encadenar otras. Cualquier otra regla con contenido (rutas, dominios) no se
	interpreta aquí: esa petición se sigue preguntando.
	"""
	if "(" in regla:
		nombre, _parentesis, contenido = regla.partition("(")
		if nombre != herramienta or not contenido.endswith(")") or herramienta not in ("Bash", "PowerShell"):
			return False
		contenido = contenido[:-1]
		orden = entrada.get("command")
		if not isinstance(orden, str):
			return False
		orden = orden.strip()
		if contenido == orden:
			return True
		if contenido.endswith(":*"):
			prefijo = contenido[:-2]
			return bool(prefijo) and (orden == prefijo or orden.startswith(prefijo + " ")) and \
				not any(caracter in orden for caracter in _CARACTERES_DE_SHELL)
		return False
	if regla == herramienta:
		return True
	partes = _partes_mcp(regla)
	if partes and partes[1] in ("", "*"):
		return herramienta.startswith(f"mcp__{partes[0]}__")
	return False


def _reglas_de_sugerencias(sugerencias: list[Any]) -> list[str]:
	reglas = []
	for sugerencia in sugerencias:
		if not isinstance(sugerencia, dict) or sugerencia.get("type") != "addRules" or sugerencia.get("behavior") != "allow":
			continue
		for regla in sugerencia.get("rules") or []:
			if not isinstance(regla, dict) or not regla.get("toolName"):
				continue
			contenido = regla.get("ruleContent")
			texto = f"{regla['toolName']}({contenido})" if contenido else str(regla["toolName"])
			if _RE_REGLA.match(texto):
				reglas.append(texto)
	return reglas


def _regla_a_dict(regla: str) -> dict[str, Any]:
	if "(" in regla and regla.endswith(")"):
		nombre, contenido = regla.split("(", 1)
		return {"toolName": nombre, "ruleContent": contenido[:-1]}
	return {"toolName": regla}
