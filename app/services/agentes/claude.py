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

Subagentes y trabajo en segundo plano (1.6.0)
=============================================

Los ``system`` ``task_started``, ``task_progress``, ``task_updated`` y
``task_notification`` se convierten en ``ACTIVIDAD``; lo que llega con
``parent_tool_use_id`` (con ``--forward-subagent-text`` también su texto) son los
pasos del subagente que lo lanzó y nunca entra en la conversación principal.
Mientras hay tareas en segundo plano no hay tope tras el resultado (AIChat #485);
si el proceso muere con ellas vivas, el turno acaba como fallo. Detener el turno
pide ``stop_task`` de cada subagente antes del ``interrupt``.

Preguntar aparte, bifurcar desde un mensaje y el prompt de sistema
==================================================================

Una pregunta aparte es otro ``claude -p`` con una copia de solo lectura que no se
guarda (``--resume --resume-session-at --fork-session --no-session-persistence``,
sin herramientas que cambien nada ni MCP ni hooks). ``extra["cortar_en"]`` corta
la sesión de origen con ``--resume-session-at``. Claude Code guarda el prompt de
sistema del primer turno: si las instrucciones, el estilo o las skills cambian
después, se pasa ``--system-prompt-snapshot off`` desde entonces.

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
import difflib
import hashlib
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
	MAX_INSTRUCCIONES,
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
#: Si tras el resultado nunca llega «inactiva» (y nada más), se da el turno por terminado. No cuenta mientras hay
#: tareas en segundo plano (AIChat #485: cerrar la entrada solo les deja 600 s más, y los subagentes morían a los
#: 10 o 20 minutos), y lo que llega después del resultado lo vuelve a empezar.
ESPERA_TRAS_RESULTADO = 600.0
#: Cuando la última tarea en segundo plano acaba con Claude Code ya inactivo, se espera esto antes de cerrar el turno
#: (el aviso de la tarea puede abrir enseguida otra respuesta).
ESPERA_TRAS_FONDO = 5.0
#: Tras pedir que se detenga, cuánto se espera antes de matar el proceso.
ESPERA_DETENER = 5.0
#: Segundos que se espera a ``stop_task`` de cada subagente al detener el turno.
ESPERA_PARAR_TAREA = 3.0
#: Segundos que se espera el desglose del contexto (``get_context_usage``).
ESPERA_CONTEXTO = 1.5
#: Segundos que se espera la respuesta a ``mcp_authenticate`` y a ``remote_control``.
ESPERA_CONTROL = 15.0
#: Como mucho un evento de progreso por tarea en este tiempo (segundos): los subagentes dan muchos.
INTERVALO_PROGRESO = 1.0
#: Largo máximo de un paso de un subagente («Dice: ...», «Resultado: ...»).
MAX_PASO = 300
#: Largo máximo de la línea ``result`` y del final de la salida de errores en ``detalle_tecnico``.
MAX_DETALLE_RESULTADO = 3000
MAX_DETALLE_ERRORES = 4000
#: Mensaje con el que se pide compactar (y que marca el turno como de compactación).
ORDEN_COMPACTAR = "/compact"
#: ``extra`` de la sesión: el prompt de sistema cambió tras el primer turno (``--system-prompt-snapshot off``).
CLAVE_PROMPT_FRESCO = "prompt_fresco"
#: ``extra`` de la sesión: huella de lo que se añadió al prompt de sistema en el primer proceso.
CLAVE_HUELLA_PROMPT = "huella_prompt"
#: ``extra`` de la sesión (opción que pone la interfaz): pedir a Claude que pregunte con AskUserQuestion.
CLAVE_PREGUNTAS_HERRAMIENTA = "pedir_preguntas_con_herramienta"
#: Lo que se añade al prompt de sistema con esa opción (BlindPilot claude_session.py).
TEXTO_PREGUNTAS_HERRAMIENTA = (
	"Esta persona te usa desde Ctrl+IA, una aplicación pensada para manejarse con lector de pantalla. Cuando necesites "
	"que te responda algo, pregúntalo con la herramienta AskUserQuestion en lugar de escribir la pregunta en tu "
	"respuesta: Ctrl+IA anuncia esa pregunta y la abre en un diálogo, mientras que una pregunta escrita en la respuesta "
	"no se anuncia como pregunta y el turno acaba sin que nada indique que esperas respuesta. Vale para todas las "
	"preguntas, también cuando preguntas varias cosas una a una."
)
#: Texto que añade Claude Code al resultado de una orden que pasa a segundo plano (2.1.308).
_RE_SALIDA_FONDO = re.compile(r"Output is being written to: (.+?)\.?\s*$", re.MULTILINE)

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


#: Herramientas de archivos cuyo aviso dice solo el nombre del archivo (la ruta entera va en el detalle).
_HERRAMIENTAS_ARCHIVO = frozenset({"Read", "Edit", "MultiEdit", "Write", "NotebookEdit"})
#: Más allá de esto no se cuentan las líneas cambiadas (sería lento y no aporta).
_MAX_COMPARAR = 200_000


def _ruta_de(entrada: dict[str, Any]) -> str:
	for clave in ("file_path", "notebook_path", "path"):
		valor = entrada.get(clave)
		if isinstance(valor, str) and valor.strip():
			return valor.strip()
	return ""


def nombre_archivo(ruta: str) -> str:
	"""Solo el nombre del archivo de una ruta de Windows o POSIX («C:\\\\a\\\\b.py» → «b.py»)."""
	return ntpath.basename(ruta.replace("/", "\\").rstrip("\\")) or ruta


def lineas_cambiadas(antes: str, despues: str) -> tuple[int, int]:
	"""``(añadidas, quitadas)`` entre dos versiones de un trozo, con un diff de verdad (BlindPilot)."""
	if len(antes) + len(despues) > _MAX_COMPARAR:
		return 0, 0
	anadidas = quitadas = 0
	comparador = difflib.SequenceMatcher(None, antes.splitlines(), despues.splitlines(), autojunk=False)
	for etiqueta, inicio_a, fin_a, inicio_b, fin_b in comparador.get_opcodes():
		if etiqueta in ("replace", "delete"):
			quitadas += fin_a - inicio_a
		if etiqueta in ("replace", "insert"):
			anadidas += fin_b - inicio_b
	return anadidas, quitadas


def _frase_lineas(anadidas: int, quitadas: int) -> str:
	"""« (3 líneas añadidas, 1 quitada)», sin las mitades a cero; «» si no hay nada."""
	partes = []
	if anadidas:
		partes.append("1 línea añadida" if anadidas == 1 else f"{anadidas} líneas añadidas")
	if quitadas:
		if anadidas:
			partes.append("1 quitada" if quitadas == 1 else f"{quitadas} quitadas")
		else:
			partes.append("1 línea quitada" if quitadas == 1 else f"{quitadas} líneas quitadas")
	return " (" + ", ".join(partes) + ")" if partes else ""


def _cambio_de_edicion(nombre: str, entrada: dict[str, Any]) -> str:
	"""Lo que cambia una edición o una escritura, para añadirlo al nombre del archivo."""
	def texto(valor: Any) -> str:
		return valor if isinstance(valor, str) else ""

	if nombre == "Write":
		lineas = len(texto(entrada.get("content")).splitlines())
		return "" if not lineas else (" (1 línea)" if lineas == 1 else f" ({lineas} líneas)")
	if nombre == "MultiEdit" and isinstance(entrada.get("edits"), list):
		anadidas = quitadas = 0
		for edicion in entrada["edits"]:
			if isinstance(edicion, dict):
				mas, menos = lineas_cambiadas(texto(edicion.get("old_string")), texto(edicion.get("new_string")))
				anadidas += mas
				quitadas += menos
		return _frase_lineas(anadidas, quitadas)
	if nombre == "Edit":
		return _frase_lineas(*lineas_cambiadas(texto(entrada.get("old_string")), texto(entrada.get("new_string"))))
	return ""


def describir_herramienta(nombre: str, entrada: dict[str, Any] | None) -> str:
	"""Frase corta y legible: «Ejecutar orden: git status», «Editar archivo: app.py (2 líneas añadidas)».

	De los archivos solo va el nombre (BlindPilot): la ruta entera sigue en el detalle.
	"""
	entrada = entrada or {}
	titulo = NOMBRES_HERRAMIENTAS.get(nombre)
	if titulo is None:
		titulo = nombre.replace("mcp__", "MCP ").replace("__", ": ") if nombre.startswith("mcp__") else f"Herramienta {nombre}"
	ruta = _ruta_de(entrada) if nombre in _HERRAMIENTAS_ARCHIVO else ""
	if ruta:
		return f"{titulo}: {nombre_archivo(ruta)}{_cambio_de_edicion(nombre, entrada)}"
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


def _una_linea(texto: Any, maximo: int = MAX_PASO) -> str:
	limpio = " ".join(str(texto or "").split())
	return limpio if len(limpio) <= maximo else limpio[:maximo - 1].rstrip() + "…"


def paso_de_herramienta(nombre: str, entrada: dict[str, Any] | None) -> str:
	"""Un paso de un subagente, como acción: «Lee app.py», «Ejecuta: git status», «Edita x.py (1 línea añadida)»."""
	entrada = entrada if isinstance(entrada, dict) else {}
	ruta = _ruta_de(entrada)
	corto = nombre_archivo(ruta) if ruta else ""

	def primero(*claves: str) -> str:
		for clave in claves:
			valor = entrada.get(clave)
			if isinstance(valor, str) and valor.strip():
				return " ".join(valor.split())
		return ""

	if nombre == "Read":
		texto = f"Lee {corto}" if corto else "Lee un archivo"
	elif nombre in ("Edit", "MultiEdit", "NotebookEdit"):
		texto = (f"Edita {corto}" if corto else "Edita un archivo") + _cambio_de_edicion(nombre, entrada)
	elif nombre == "Write":
		texto = (f"Escribe {corto}" if corto else "Escribe un archivo") + _cambio_de_edicion(nombre, entrada)
	elif nombre in ("Bash", "PowerShell"):
		orden = primero("command")
		texto = f"Ejecuta: {orden}" if orden else "Ejecuta una orden"
	elif nombre in ("Grep", "Glob"):
		patron = primero("pattern")
		texto = f"Busca «{patron}»" if patron else "Busca"
	elif nombre == "WebFetch":
		texto = "Lee la página " + primero("url")
	elif nombre == "WebSearch":
		texto = "Busca en internet: " + primero("query")
	elif nombre in ("Task", "Agent"):
		texto = "Lanza un subagente: " + (primero("description") or "una tarea")
	elif nombre in ("TodoWrite", "TaskCreate", "TaskUpdate"):
		texto = "Actualiza su lista de tareas"
	else:
		texto = "Usa " + describir_herramienta(nombre, entrada)
	return _una_linea(texto)


def _uso_de_tarea(uso: Any) -> dict[str, int]:
	"""``usage`` de una tarea (``total_tokens``, ``tool_uses``, ``duration_ms``) con los nombres de ``ACTIVIDAD``."""
	if not isinstance(uso, dict):
		return {}
	resultado = {}
	for origen, destino in (("total_tokens", "tokens"), ("tool_uses", "usos_herramientas"), ("duration_ms", "duracion_ms")):
		valor = uso.get(origen)
		if isinstance(valor, (int, float)) and not isinstance(valor, bool) and valor >= 0:
			resultado[destino] = int(valor)
	return resultado


def _texto_resultado(contenido: Any) -> str:
	"""Texto de un ``tool_result`` (cadena o lista de bloques)."""
	if isinstance(contenido, list):
		return "\n".join(str(parte.get("text") or "") for parte in contenido if isinstance(parte, dict))
	return str(contenido or "")


#: Clase de ``ACTIVIDAD`` de cada ``task_type`` de Claude Code.
CLASES_TAREA = {"local_agent": "subagente", "remote_agent": "subagente", "local_bash": "orden"}
#: Estado de ``ACTIVIDAD`` de cada estado de una tarea de Claude Code (``task_updated`` y ``task_notification``).
ESTADOS_TAREA = {
	"pending": "en_marcha", "running": "en_marcha", "paused": "en_marcha",
	"completed": "completada", "failed": "fallida", "killed": "detenida", "stopped": "detenida",
}
#: Errores de ``api_retry`` (Claude Code 2.1.308), en palabras.
ERRORES_SERVICIO = {
	"authentication_failed": "la sesión de Claude no es válida",
	"oauth_org_not_allowed": "tu organización no permite este inicio de sesión",
	"account_on_hold": "tu cuenta está en espera",
	"verification_required": "hace falta verificar tu cuenta",
	"billing_error": "hay un problema de facturación",
	"rate_limit": "has llegado a un límite de uso",
	"overloaded": "el servicio de Claude está sobrecargado",
	"invalid_request": "Claude no ha aceptado la petición",
	"model_not_found": "el modelo no existe o no está disponible",
	"server_error": "el servidor de Claude ha fallado",
	"unknown": "un error desconocido",
	"max_output_tokens": "la respuesta pasó de su tamaño máximo",
	"cloud_credential_error": "fallan las credenciales de la nube",
}
#: Por qué Claude Code cambia de modelo a mitad de turno (``model_fallback``).
MOTIVOS_CAMBIO_MODELO = {
	"model_not_found": "el modelo ya no existe o no se conoce",
	"permission_denied": "tu cuenta no tiene acceso a ese modelo",
	"overloaded": "el modelo está sobrecargado",
	"server_error": "el servidor de ese modelo ha fallado",
	"last_resort": "el modelo ha dado un error que no se puede reintentar",
	"model_blocked": "ese modelo está bloqueado",
}
#: Ventanas de límite de uso de Claude Code (``rate_limit_info``), en palabras.
VENTANAS_LIMITE = {
	"five_hour": "las próximas horas",
	"seven_day": "la semana",
	"seven_day_opus": "la semana de Opus",
	"seven_day_sonnet": "la semana de Sonnet",
	"seven_day_overage_included": "la semana con el uso adicional",
	"overage": "el uso adicional",
}
#: Partes del contexto (``get_context_usage``, ``categories[].id``), en palabras.
PARTES_CONTEXTO = {
	"system_prompt": "Instrucciones del sistema",
	"system_tools": "Herramientas de Claude Code",
	"system_tools_deferred": "Herramientas de Claude Code que se cargan cuando hacen falta",
	"mcp_tools": "Herramientas MCP",
	"mcp_tools_deferred": "Herramientas MCP que se cargan cuando hacen falta",
	"mcp_instructions": "Instrucciones de los servidores MCP",
	"custom_agents": "Subagentes",
	"memory_files": "Archivos de memoria (CLAUDE.md)",
	"skills": "Skills",
	"messages": "Mensajes",
	"autocompact_buffer": "Reserva para compactar",
	"compact_buffer": "Reserva para compactar",
	"free_space": "Libre",
}
NOTA_DIFERIDA = "se carga cuando hace falta"
NOTA_RESERVA = "reservado para compactar"


def desglose_contexto(cuerpo: dict[str, Any]) -> list[tuple[str, int, str]] | None:
	"""La respuesta de ``get_context_usage`` como ``[(parte, tokens, nota)]``, en español.

	Se clasifica por ``kind`` y ``id``, nunca por el nombre en inglés (lo dice el propio esquema de 2.1.308).
	"""
	categorias = cuerpo.get("categories")
	if not isinstance(categorias, list):
		return None
	filas: list[tuple[str, int, str]] = []
	for categoria in categorias:
		if not isinstance(categoria, dict):
			continue
		tokens = categoria.get("tokens")
		if not isinstance(tokens, (int, float)) or isinstance(tokens, bool):
			continue
		identificador = str(categoria.get("id") or "")
		tipo = str(categoria.get("kind") or "")
		nombre = PARTES_CONTEXTO.get(identificador) or " ".join(str(categoria.get("name") or identificador or "Otra").split())
		nota = ""
		if categoria.get("isDeferred") or tipo == "deferred":
			nota = NOTA_DIFERIDA
		elif tipo == "buffer" or identificador in ("autocompact_buffer", "compact_buffer"):
			nota = NOTA_RESERVA
		filas.append((nombre, int(tokens), nota))
	return filas


def numero_legible(valor: Any) -> str:
	"""«150000» → «150.000»."""
	try:
		return f"{int(valor):,}".replace(",", ".")
	except (TypeError, ValueError):
		return str(valor)


def _hora_legible(epoca: Any) -> str:
	"""Una hora en segundos desde 1970 como «a las 18:30» (o «el 14/11 a las 18:30» si no es hoy)."""
	try:
		momento = time.localtime(float(epoca))
	except (TypeError, ValueError, OverflowError, OSError):
		return ""
	hoy = time.localtime()
	if (momento.tm_year, momento.tm_yday) == (hoy.tm_year, hoy.tm_yday):
		return time.strftime("a las %H:%M", momento)
	return time.strftime("el %d/%m a las %H:%M", momento)


def detalle_tecnico(resultado: dict[str, Any] | None, errores: Sequence[str] = ()) -> str:
	"""La línea ``result`` en bruto (recortada) y el final de la salida de errores, sin secretos.

	Para leerla con Intro en un ``ERROR``: ``subtype`` (``error_max_turns``...), ``errors``, ``stop_reason``.
	"""
	from app.core.registro import redactar

	partes = []
	if resultado:
		resumen = {clave: resultado[clave] for clave in ("subtype", "is_error", "stop_reason", "terminal_reason", "errors",
			"result", "num_turns", "duration_ms", "api_error_status") if clave in resultado}
		texto = json.dumps(resumen or resultado, ensure_ascii=False)
		partes.append("Resultado de Claude Code:\n" + texto[:MAX_DETALLE_RESULTADO])
	salida = "\n".join(str(linea) for linea in errores if str(linea).strip())
	if salida:
		partes.append("Salida de errores de Claude Code:\n" + salida[-MAX_DETALLE_ERRORES:])
	return redactar("\n\n".join(partes))


def _nombre_modo(modo: str) -> str:
	"""Nombre en español de un modo de permisos de Claude Code."""
	modo = "default" if modo == "manual" else modo
	for opcion in ProveedorClaude.MODOS:
		if opcion.id == modo:
			return opcion.etiqueta
	return modo


def _modo_normalizado(modo: Any) -> str:
	"""El modo tal como se compara: «manual» es el nombre nuevo de «default» (2.1.308)."""
	modo = str(modo or "")
	return "default" if modo == "manual" else modo


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
		# «/compact» como mensaje normal; «stop_task», «get_context_usage» y «--resume-session-at»: 2.1.308.
		compactar=True,
		detener_tareas=True,
		preguntar_aparte=True,
		bifurcar_en_mensaje=True,
		instrucciones_sesion=True,
		desglose_contexto=True,
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

	MODOS = (
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
	)

	def modos_permiso(self) -> list[OpcionLista]:
		return [OpcionLista(o.id, o.etiqueta, o.ayuda) for o in self.MODOS]

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

	# -- Preguntar aparte (AIChat aside.py) --------------------------------------

	def orden_aparte(self, ajustes: AjustesSesion, origen: str, corte: str) -> list[str]:
		"""La orden de una pregunta aparte: una copia de solo lectura de ``origen`` hasta ``corte``, que no se guarda.

		Todas las opciones comprobadas en Claude Code 2.1.308 (``--permission-mode manual`` y ``--permission-prompts
		none``: nadie contesta permisos y lo que preguntaría se deniega; ``--tools`` solo de lectura; sin servidores
		MCP ni conectores; sin hooks; un «/» al principio es solo texto; nunca un ``--resume`` sin ``--fork-session``).
		"""
		base = self.orden_base()
		if not base:
			raise RuntimeError("No se encuentra Claude Code en este equipo.")
		if not _RE_ID.match(origen or "") or origen.startswith("local_"):
			raise RuntimeError("Esta sesión no se puede copiar para preguntar aparte.")
		if not _RE_UUID.match(corte or ""):
			raise RuntimeError("No se sabe hasta dónde copiar la conversación.")
		orden = [*base, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
			"--include-partial-messages", "--permission-mode", "manual", "--permission-prompts", "none",
			"--tools", HERRAMIENTAS_APARTE, *SIN_SERVIDORES_MCP, "--settings", json.dumps({"disableAllHooks": True}),
			"--disable-slash-commands"]
		if ajustes.modelo and _RE_MODELO.match(ajustes.modelo):
			orden += ["--model", ajustes.modelo]
		carpetas = [str(c) for c in ajustes.extra.get("carpetas_adicionales", []) or [] if carpeta_concreta(str(c))]
		for carpeta in carpetas:
			orden += ["--add-dir", carpeta]
		return [*orden, "--resume", origen, "--resume-session-at", corte, "--fork-session", "--no-session-persistence"]

	def preguntar_aparte(self, ajustes: AjustesSesion, pregunta: str, al_texto: Callable[[str], None] | None = None,
			cancelar: threading.Event | None = None, anteriores: Sequence[tuple[str, str]] = ()) -> str:
		"""Pregunta rápida sobre la sesión que no se añade a ella (``Proveedor.preguntar_aparte``).

		:param anteriores: preguntas aparte anteriores con sus respuestas, que van delante marcadas como contexto
			(``pregunta_con_contexto``, como mucho ``MAX_CONTEXTO_APARTE`` caracteres).
		"""
		from app.services.historial import claude as historial_claude

		pregunta = (pregunta or "").strip()
		if not pregunta:
			raise RuntimeError("Escribe la pregunta.")
		if remoto.es_remota(ajustes.extra):
			raise RuntimeError("Preguntar aparte no está disponible en las sesiones de otro equipo por SSH: su "
				"conversación está guardada allí. Pregunta en la propia sesión.")
		ruta = None
		origen = ""
		for candidato in (ajustes.id_externo, ajustes.bifurcar_de):
			if candidato and _RE_ID.match(candidato):
				ruta = historial_claude.ruta_transcript(candidato, str(ajustes.carpeta or ""))
				if ruta is not None:
					origen = candidato
					break
		if ruta is None:
			raise RuntimeError("Claude Code no tiene guardada la conversación de esta sesión (todavía no ha respondido, o "
				"ya la borró): no hay nada sobre lo que preguntar aparte.")
		try:
			corte = corte_terminado_de_archivo(ruta)
		except OSError as error:
			raise RuntimeError("No se ha podido leer la conversación de Claude Code: %s" % error) from error
		if corte == CORTE_COMPACTADA:
			raise RuntimeError("Esta conversación se ha compactado después de su última respuesta terminada: vuelve a "
				"preguntar cuando Claude haya respondido otra vez.")
		if not corte:
			raise RuntimeError(SIN_RESPUESTA_TERMINADA)
		orden = self.orden_aparte(ajustes, origen, corte)
		return _ejecutar_aparte(orden, ajustes.carpeta, pregunta_con_contexto(anteriores, pregunta), al_texto, cancelar,
			self.permitir_clave_api)


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
		#: Último ``total_cost_usd`` de este proceso: el coste de cada respuesta es lo que crece.
		self.coste_anterior: float | None = None
		#: Tareas de este proceso (subagentes, órdenes en segundo plano): task_id → lo que se sabe de ella.
		self.tareas: dict[str, dict[str, Any]] = {}
		#: Llamada (Task/Agent, Bash) que lanzó cada tarea: tool_use_id → task_id.
		self.tarea_de_llamada: dict[str, str] = {}
		#: Llamadas hechas DENTRO de un subagente: tool_use_id → (task_id, herramienta).
		self.llamadas_de_subagente: dict[str, tuple[str, str]] = {}
		#: Pasos de subagentes que llegaron antes de su ``task_started``: tool_use_id de la llamada → pasos.
		self.pasos_sin_tarea: dict[str, list[str]] = {}
		#: Hay que relanzarlo antes del próximo mensaje aunque la firma no cambie (``bypassPermissions`` en marcha).
		self.relanzar = False

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
		#: Último ``session_state_changed`` («idle», «running», «requires_action»).
		self.ultimo_estado = ""
		#: Descripción de cada tarea en segundo plano (``background_tasks_changed``): task_id → descripción.
		self.tareas_descritas: dict[str, str] = {}
		#: Ya llegó el resultado y se espera «inactiva» (lo que llegue después vuelve a empezar la espera).
		self.tras_resultado = False
		#: Es el turno de «/compact»: no admite «Añadir al turno» (iría a las instrucciones del resumen).
		self.compactar = False
		#: Ya se ha dicho que Claude está pensando en este bloque (solo barra de estado).
		self.pensando = False
		#: Hora (monotónica) de la última línea recibida en este turno.
		self.ultima_actividad = time.monotonic()
		#: Orden de cada llamada a Bash o PowerShell: tool_use_id → orden (para las que pasan a segundo plano).
		self.ordenes: dict[str, str] = {}


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
		#: Peticiones de control propias sin respuesta todavía: request_id → (subtipo, valor anterior).
		self._peticiones: dict[str, tuple[str, Any]] = {}
		#: Modo de permisos que Claude Code dice tener (``init``, ``status``); «» si aún no lo ha dicho.
		self._modo_real = ""
		#: Claude Code está compactando (``status`` «compacting»): se avisa una vez por compactación.
		self._compactando = False
		#: Ya se avisó de que Claude está gastando uso adicional de pago (una vez por conversación).
		self._avisado_uso_extra = False
		#: Límites alcanzados de los que ya se avisó: (ventana, reinicio).
		self._limites_avisados: set[tuple[str, Any]] = set()
		#: Hora (monotónica) del último progreso emitido de cada tarea.
		self._ultimo_progreso: dict[str, float] = {}
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
		return servidores_mcp.con_denegados_de_sesion(servidores_mcp.lista_desde_claude(filas),
			self.ajustes.extra.get("mcp_denegados") or [])

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

	def activar_mcp(self, nombre: str, activo: bool, solo_sesion: bool = False) -> None:
		"""Activa o desactiva un servidor MCP.

		Sin ``solo_sesion`` es ``mcp_toggle``, que Claude Code GUARDA en su configuración (vale para todas sus
		sesiones). Con ``solo_sesion`` va a ``extra["mcp_denegados"]`` y a ``--settings {"deniedMcpServers": ...}``
		(comprobado en 2.1.308: se suma a la lista de la organización y no se escribe en ningún archivo); como va en
		la línea de órdenes, vale desde el próximo mensaje, que relanza Claude Code.
		"""
		if solo_sesion:
			if not nombre or not isinstance(nombre, str) or len(nombre) > 200 or any(c in nombre for c in "\r\n\0"):
				raise RuntimeError("Nombre de servidor MCP no válido.")
			with self._candado:
				denegados = [n for n in self.ajustes.extra.get("mcp_denegados", []) or [] if isinstance(n, str)]
				if activo:
					denegados = [n for n in denegados if n != nombre]
				elif nombre not in denegados:
					denegados.append(nombre)
				if denegados:
					self.ajustes.extra["mcp_denegados"] = denegados
				else:
					self.ajustes.extra.pop("mcp_denegados", None)
				self._mcp_avisados.pop(nombre, None)
			return
		self._control_mcp({"subtype": "mcp_toggle", "serverName": nombre, "enabled": bool(activo)}, ESPERA_ESTADO_MCP)
		with self._candado:
			self._mcp_avisados.pop(nombre, None)

	def url_inicio_sesion_mcp(self, nombre: str) -> str | None:
		"""Dirección para iniciar sesión en un servidor MCP (``mcp_authenticate``, 2.1.308), o ``None``.

		Solo con Claude Code abierto y sin turno en marcha: la vuelta del navegador la recoge ese mismo proceso
		(``callbackExpected``, un puerto de este equipo), así que no vale en otro equipo por SSH salvo los
		conectores de claude.ai, que no esperan vuelta.
		"""
		if self._turno_en_marcha():
			return None
		vivo = self._proceso_listo()
		if vivo is None or vivo.proceso is None:
			return None
		if vivo.temporizador is not None:
			self._programar_cierre(vivo)
		respuesta = self._control_y_esperar(vivo.proceso, {"subtype": "mcp_authenticate", "serverName": nombre},
			ESPERA_CONTROL).get("response") or {}
		if respuesta.get("subtype") == "error":
			raise RuntimeError("Claude Code no puede iniciar sesión en «%s»: %s" % (nombre, respuesta.get("error") or "sin motivo"))
		cuerpo = respuesta.get("response") or {}
		direccion = str(cuerpo.get("authUrl") or "")
		if not direccion:
			raise RuntimeError("Claude Code dice que «%s» no necesita que inicies sesión ahora." % nombre)
		if not direccion.startswith(("https://", "http://")):
			raise RuntimeError("Claude Code ha dado una dirección de inicio de sesión que no es web.")
		if cuerpo.get("callbackExpected") and remoto.es_remota(self.ajustes.extra):
			raise RuntimeError("Este inicio de sesión vuelve a un puerto del otro equipo y tu navegador está en este: hazlo "
				"allí con «claude mcp login %s»." % nombre)
		return direccion

	def compactar(self) -> None:
		"""Pide a Claude Code que compacte la conversación: «/compact» como un mensaje normal.

		El siguiente mensaje va siempre aparte (la cola manda uno por turno) y durante ese turno no se admite
		«Añadir al turno»: lo tomaría por instrucciones para el resumen (FastChat 689da57).
		"""
		self.enviar(ORDEN_COMPACTAR)

	def tareas_fondo_descritas(self) -> list[tuple[str, str]]:
		"""``[(id, descripción)]`` de lo que sigue en segundo plano en el turno en marcha (para «Estado del turno»)."""
		with self._candado:
			turno = self._turno
			if turno is None or turno.terminado:
				return []
			return [(tarea, turno.tareas_descritas.get(tarea, "")) for tarea in sorted(turno.tareas_fondo)]

	def detener_tarea(self, id_tarea: str) -> None:
		"""Para una tarea en segundo plano (``stop_task``). Claude Code responde que sí aunque no la conozca: el
		final de verdad llega con su ``task_notification`` o al salir de ``background_tasks_changed``."""
		if not isinstance(id_tarea, str) or not _RE_ID.match(id_tarea):
			raise RuntimeError("Identificador de tarea no válido.")
		vivo = self._proceso_listo()
		if vivo is None or vivo.proceso is None:
			raise RuntimeError("Claude Code no está abierto: esa tarea ya no está en marcha.")
		respuesta = self._control_y_esperar(vivo.proceso, {"subtype": "stop_task", "task_id": id_tarea},
			ESPERA_PARAR_TAREA).get("response") or {}
		if respuesta.get("subtype") == "error":
			raise RuntimeError("Claude Code no ha podido parar la tarea: %s" % (respuesta.get("error") or "sin motivo"))

	def uso_contexto(self) -> list[tuple[str, int, str]] | None:
		"""Desglose del contexto con ``get_context_usage`` (``detail: summary``, sin llamar a la API), o ``None``."""
		vivo = self._proceso_listo()
		if vivo is None or vivo.proceso is None:
			return None
		try:
			datos = self._control_y_esperar(vivo.proceso, {"subtype": "get_context_usage", "detail": "summary"}, ESPERA_CONTEXTO)
		except (RuntimeError, TimeoutError):
			log.debug("Claude Code no ha dado el desglose del contexto", exc_info=True)
			return None
		respuesta = datos.get("response") or {}
		if respuesta.get("subtype") == "error":
			log.info("Claude Code no da el desglose del contexto: %s", respuesta.get("error"))
			return None
		return desglose_contexto(respuesta.get("response") or {})

	def control_remoto(self, activar: bool) -> str:
		"""Activa o quita Claude Remote Control (``remote_control``, 2.1.308) y devuelve la dirección para seguirla.

		Se guarda ``bridge_session_id`` en ``extra["control_remoto"]`` y cada proceso nuevo vuelve a unirse a la
		misma sesión remota (``reattach_session_id``).
		"""
		vivo = self._proceso_listo()
		if vivo is None or vivo.proceso is None:
			raise RuntimeError("Claude Code no está abierto: envía un mensaje en esta sesión y vuelve a intentarlo.")
		return self._pedir_control_remoto(vivo, activar)

	def _pedir_control_remoto(self, vivo: _ProcesoClaude, activar: bool) -> str:
		assert vivo.proceso is not None
		guardado = self.ajustes.extra.get("control_remoto") if isinstance(self.ajustes.extra.get("control_remoto"), dict) else {}
		peticion: dict[str, Any] = {"subtype": "remote_control", "enabled": bool(activar)}
		if activar:
			peticion["name"] = (self.ajustes.titulo or "Ctrl+IA")[:120]
			peticion["keep_session_on_exit"] = True
			anterior = str(guardado.get("bridge_session_id") or "")
			if anterior and _RE_ID.match(anterior):
				peticion["reattach_session_id"] = anterior
		respuesta = self._control_y_esperar(vivo.proceso, peticion, ESPERA_CONTROL).get("response") or {}
		if respuesta.get("subtype") == "error":
			raise RuntimeError("Claude Code no ha podido %s el control remoto: %s" % ("activar" if activar else "quitar",
				respuesta.get("error") or "sin motivo"))
		cuerpo = respuesta.get("response") or {}
		with self._candado:
			if not activar:
				self.ajustes.extra.pop("control_remoto", None)
				return ""
			puente = str(cuerpo.get("bridge_session_id") or guardado.get("bridge_session_id") or "")
			direccion = str(cuerpo.get("session_url") or "")
			self.ajustes.extra["control_remoto"] = {"activo": True, "bridge_session_id": puente, "url": direccion}
		return direccion

	def _volver_a_control_remoto(self, vivo: _ProcesoClaude) -> None:
		"""Un proceso nuevo con el control remoto activado en la sesión: se vuelve a unir (en otro hilo)."""
		guardado = self.ajustes.extra.get("control_remoto")
		if not isinstance(guardado, dict) or not guardado.get("activo"):
			return

		def unir() -> None:
			try:
				self._pedir_control_remoto(vivo, True)
				log.info("Control remoto de Claude Code activado de nuevo en el proceso nuevo")
			except (RuntimeError, TimeoutError) as error:
				self._emitir(TipoEvento.AVISO, "No se ha podido volver a activar el control remoto de Claude: %s" % error,
					detalle_tecnico=str(error))

		threading.Thread(target=unir, name="claude-control-remoto", daemon=True).start()

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
			if listo and turno is not None and turno.compactar:
				# Lo escrito durante «/compact» lo tomaría por instrucciones para el resumen (FastChat 689da57).
				self._emitir(TipoEvento.AVISO, "Claude está compactando la conversación: tu mensaje no se ha enviado para "
					"que no se mezcle con el resumen. El texto vuelve al cuadro de respuesta; envíalo cuando termine.",
					devolver_texto=texto)
				return
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
			vivo = turno.vivo
			# Un «interrupt» acaba el turno pero deja trabajando a sus subagentes (BlindPilot claude_session.py):
			# detener tiene que pararlos también. Las órdenes en segundo plano siguen, como en Claude Code.
			subagentes = [identificador for identificador, tarea in (vivo.tareas.items() if vivo is not None else ())
				if tarea.get("clase") == "subagente" and tarea.get("estado") == "en_marcha"]
		if proceso is not None and proceso.vivo:
			for identificador in subagentes:
				self._pedir(proceso, {"subtype": "stop_task", "task_id": identificador}, "stop_task", identificador)
			proceso.escribir_json({"type": "control_request", "request_id": "ctrlia-detener", "request": {"subtype": "interrupt"}})
			temporizador = threading.Timer(ESPERA_DETENER, self._forzar_fin, args=(turno, True))
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

	def _pedir(self, proceso: procesos.ProcesoLineas, peticion: dict[str, Any], subtipo: str, anterior: Any = None) -> bool:
		"""Petición de control sin esperar: su respuesta la mira ``_al_respuesta_control`` (los errores se avisan)."""
		identificador = f"ctrlia-{subtipo.replace('_', '-')}-{uuid.uuid4().hex[:8]}"
		with self._candado:
			self._peticiones[identificador] = (subtipo, anterior)
		if proceso.escribir_json({"type": "control_request", "request_id": identificador, "request": peticion}):
			return True
		with self._candado:
			self._peticiones.pop(identificador, None)
		return False

	def cambiar_ajustes(self, **cambios: Any) -> None:
		with self._candado:
			modo_anterior, modelo_anterior = self.ajustes.modo_permiso, self.ajustes.modelo
			super().cambiar_ajustes(**cambios)
			turno = self._turno
			vivo = self._vivo
		modo = cambios.get("modo_permiso")
		proceso = vivo.proceso if vivo is not None and vivo.vivo() else (turno.proceso if turno is not None else None)
		if proceso is None or not proceso.vivo:
			return
		if modo and modo != modo_anterior:
			self._pedir(proceso, {"subtype": "set_permission_mode", "mode": modo}, "set_permission_mode", modo_anterior)
		elif modo:
			proceso.escribir_json({"type": "control_request", "request_id": f"ctrlia-modo-{uuid.uuid4().hex[:6]}",
				"request": {"subtype": "set_permission_mode", "mode": modo}})
		if "modelo" in cambios and (cambios["modelo"] or "") != (modelo_anterior or ""):
			modelo = str(cambios["modelo"] or "")
			if modelo and not _RE_MODELO.match(modelo):
				return
			# En caliente (2.1.308): «default» vuelve al predeterminado. Si Claude Code lo rechaza, el modelo nuevo
			# se queda para el turno siguiente, que relanza el proceso (va en la firma).
			self._pedir(proceso, {"subtype": "set_model", "model": modelo or "default"}, "set_model", (vivo, modelo))

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
		posix = remoto.es_remota(ajustes.extra)
		if not posix:
			# Texto y razonamiento de los subagentes con su ``parent_tool_use_id`` (2.1.308): pasos de su ACTIVIDAD.
			# Solo aquí: en otro equipo puede haber un Claude Code que no conozca la opción.
			orden.append("--forward-subagent-text")
		# Sin modo elegido no se pasa nada: así manda el «defaultMode» de los settings.json de Claude Code.
		if ajustes.modo_permiso:
			orden += ["--permission-mode", ajustes.modo_permiso]
		if ajustes.modelo and _RE_MODELO.match(ajustes.modelo):
			orden += ["--model", ajustes.modelo]
		if ajustes.esfuerzo:
			orden += ["--effort", ajustes.esfuerzo]
		ajustes_cli = self._ajustes_cli()
		if ajustes_cli:
			# Solo para esta sesión: nunca se cambia el settings.json de la persona. Claude Code se queda solo con el
			# ÚLTIMO --settings, así que todo va en uno.
			orden += ["--settings", json.dumps(ajustes_cli, ensure_ascii=False)]
		if self._proveedor.usar_chrome() and not remoto.es_remota(ajustes.extra):
			# Solo aquí: el Chrome de la persona está en este equipo, no en el del otro lado del SSH.
			orden.append("--chrome")
		reglas = [regla for regla in ajustes.reglas_permitidas if _RE_REGLA.match(regla) and not regla.startswith(PREFIJO_CARPETA)]
		# «ctrlia» solo afecta a esta sesión: no se pregunta por él. De los comandos de sesión, solo por lo que no lee
		# ni cambia otra sesión (``herramientas_sesiones.SIN_PREGUNTAR``).
		reglas += self._reglas_en_proceso()
		if reglas:
			orden += ["--allowedTools", *reglas]
		for carpeta in self._carpetas_adicionales():
			orden += ["--add-dir", carpeta]
		for regla in ajustes.reglas_permitidas:
			carpeta = carpeta_de_regla(regla)
			if carpeta and carpeta_concreta(carpeta, posix):
				orden += ["--add-dir", carpeta]
		corte = self._corte_bifurcacion() if ajustes.bifurcar_de else None
		if ajustes.bifurcar_de and corte == "":
			# «Reintentar» el primer mensaje de la conversación: no hay nada que recordar antes de él.
			log.info("Reintentar desde el primer mensaje: la sesión nueva empieza sin historial")
			ajustes.bifurcar_de = ""
			ajustes.id_externo = ajustes.id_externo or str(uuid.uuid4())
			orden += ["--session-id", ajustes.id_externo]
		elif ajustes.bifurcar_de:
			if not _RE_ID.match(ajustes.bifurcar_de) or ajustes.bifurcar_de.startswith("local_"):
				raise ValueError("No se puede continuar esa sesión")
			nuevo = ajustes.id_externo or str(uuid.uuid4())
			ajustes.id_externo = nuevo
			orden += ["--resume", ajustes.bifurcar_de]
			if corte:
				# Comprobado en 2.1.308 (opción oculta): conserva hasta ese registro, incluido.
				orden += ["--resume-session-at", corte]
			orden += ["--fork-session", "--session-id", nuevo]
		elif self._sesion_creada and ajustes.id_externo:
			orden += ["--resume", ajustes.id_externo]
		else:
			if not ajustes.id_externo:
				ajustes.id_externo = str(uuid.uuid4())
			orden += ["--session-id", ajustes.id_externo]
		if ajustes.titulo:
			orden += ["--name", ajustes.titulo[:120]]
		complemento: Path | None = None
		textos_sistema: list[str] = []
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
				textos_sistema.append(
					"La persona ha elegido para esta sesión estas skills, que tienes como habilidades: %s. Cuando la "
					"tarea encaje con una o te pida usarla, ábrela con la herramienta Skill y sigue sus instrucciones; "
					"si te pregunta por ellas, ábrelas para responder. Sus archivos SKILL.md están en: %s." % (nombres, rutas))
				# También la del propio complemento: Claude Code abre las skills por esa ruta (enlaces a las mismas carpetas).
				carpetas = [*(s.get("carpeta", "") for s in self.skills), str(complemento)]
				lecturas = [regla for regla in (skills_sesion.regla_lectura(c) for c in carpetas) if regla]
				if lecturas:
					orden += ["--allowedTools", *lecturas]
		instrucciones = self._instrucciones()
		if instrucciones:
			textos_sistema.append("Instrucciones de la persona para esta sesión:\n" + instrucciones)
		if ajustes.extra.get(CLAVE_PREGUNTAS_HERRAMIENTA):
			textos_sistema.append(TEXTO_PREGUNTAS_HERRAMIENTA)
		texto_sistema = "\n\n".join(textos_sistema)
		if texto_sistema:
			# Con «=»: un texto que empieza por «-» no se puede leer como otra opción (AIChat chat_options.py).
			orden.append("--append-system-prompt=" + texto_sistema)
		if self._prompt_fresco(texto_sistema + "\0" + json.dumps(self._ajustes_cli().get("outputStyle") or "")):
			# Claude Code guarda el prompt de sistema del primer turno y lo reutiliza tal cual (2.1.308): sin esto,
			# cambiar las instrucciones, el estilo o las skills a mitad de sesión no surtía efecto.
			orden += ["--system-prompt-snapshot", "off"]
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

	def _ajustes_cli(self) -> dict[str, Any]:
		"""Lo que va en el único ``--settings`` de la sesión: estilo y servidores MCP desactivados solo aquí."""
		extra = self.ajustes.extra
		resultado: dict[str, Any] = {}
		estilo = str(extra.get("estilo") or "")
		if estilo and estilo.lower() != "default" and _RE_ESTILO.match(estilo):
			resultado["outputStyle"] = estilo
		denegados = [n for n in extra.get("mcp_denegados", []) or []
			if isinstance(n, str) and n and len(n) <= 200 and not any(c in n for c in "\r\n\0")]
		if denegados:
			# Forma comprobada en 2.1.308: ``deniedMcpServers: [{serverName}]``, nombre tal cual (sin normalizar).
			resultado["deniedMcpServers"] = [{"serverName": nombre} for nombre in denegados]
		return resultado

	def _carpetas_adicionales(self) -> list[str]:
		"""``extra["carpetas_adicionales"]`` que son carpetas concretas: nunca una unidad entera ni una raíz."""
		posix = remoto.es_remota(self.ajustes.extra)
		carpetas = []
		for carpeta in self.ajustes.extra.get("carpetas_adicionales", []) or []:
			texto = str(carpeta)
			if carpeta_concreta(texto, posix) and texto not in carpetas:
				carpetas.append(texto)
			elif texto:
				log.warning("Carpeta adicional rechazada (no es una carpeta concreta): %s", texto)
		return carpetas

	def _instrucciones(self) -> str:
		"""Instrucciones de la sesión (``extra["instrucciones"]``), recortadas a ``MAX_INSTRUCCIONES``."""
		texto = self.ajustes.extra.get("instrucciones")
		if not isinstance(texto, str):
			return ""
		return texto.replace("\0", "").strip()[:MAX_INSTRUCCIONES]

	def _prompt_fresco(self, lo_que_cambia: str) -> bool:
		"""Si hay que pasar ``--system-prompt-snapshot off`` porque el prompt de sistema cambió tras el primer turno.

		La huella del primer proceso se guarda en ``extra["huella_prompt"]``; si después cambia (o la sesión ya existía
		sin huella y lleva algo propio), ``extra["prompt_fresco"]`` queda a ``True`` para siempre (AIChat #468).
		"""
		extra = self.ajustes.extra
		if extra.get(CLAVE_PROMPT_FRESCO):
			return True
		huella = hashlib.sha256(lo_que_cambia.encode("utf-8")).hexdigest()[:16]
		anterior = extra.get(CLAVE_HUELLA_PROMPT)
		vacio = lo_que_cambia == "\0" + json.dumps("")
		existe = bool(self.ajustes.bifurcar_de) or (self._sesion_creada and bool(self.ajustes.id_externo))
		if not existe or (anterior is None and vacio) or anterior == huella:
			extra[CLAVE_HUELLA_PROMPT] = huella
			return False
		extra[CLAVE_PROMPT_FRESCO] = True
		log.info("El prompt de sistema de la sesión ha cambiado tras empezar: se renueva en cada petición")
		return True

	def _corte_bifurcacion(self) -> str | None:
		"""Dónde corta la sesión de origen al bifurcar (``extra["cortar_en"]``): un uuid, «» (nada antes) o ``None``.

		«bifurcar» corta en el registro elegido (incluido); «reintentar», que se hace sobre un mensaje tuyo, en el
		anterior a él (su ``parentUuid``), que se lee del transcript del origen en este equipo: solo se lee.
		"""
		cortar = self.ajustes.extra.get("cortar_en")
		if not cortar:
			return None
		if not isinstance(cortar, dict):
			raise ValueError("No se sabe en qué mensaje cortar la conversación.")
		modo, ancla = str(cortar.get("modo") or "bifurcar"), str(cortar.get("ancla") or "")
		if not _RE_UUID.match(ancla):
			raise ValueError("Ese mensaje no se puede usar para cortar la conversación de Claude Code.")
		if modo == "bifurcar":
			return ancla
		if modo != "reintentar":
			raise ValueError("Forma de cortar la conversación desconocida: %s" % modo)
		if remoto.es_remota(self.ajustes.extra):
			raise RuntimeError("No se puede reintentar desde un mensaje en una sesión de otro equipo por SSH: su conversación "
				"está guardada allí. Usa «Bifurcar» desde la respuesta anterior.")
		from app.services.historial import claude as historial_claude

		ruta = historial_claude.ruta_transcript(self.ajustes.bifurcar_de, str(self.ajustes.carpeta or ""))
		if ruta is None:
			raise RuntimeError("Claude Code ya no tiene guardada la conversación de origen: no se puede reintentar desde ese mensaje.")
		try:
			padre = registro_padre(ruta, ancla)
		except OSError as error:
			raise RuntimeError("No se ha podido leer la conversación de origen: %s" % error) from error
		if padre is None:
			raise RuntimeError("Ese mensaje no está en la conversación de Claude Code: no se puede reintentar desde él.")
		if padre and not _RE_UUID.match(padre):
			raise RuntimeError("La conversación de Claude Code tiene un formato que no se esperaba.")
		return padre

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
			# Instrucciones de la sesión, servidores MCP desactivados solo aquí y lo demás que va en la orden.
			self._instrucciones(), tuple(str(n) for n in ajustes.extra.get("mcp_denegados", []) or []),
			bool(ajustes.extra.get(CLAVE_PREGUNTAS_HERRAMIENTA)),
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
		compactar = texto.strip() == ORDEN_COMPACTAR
		if (anterior is not None and anterior.vivo() and self._proveedor.minutos_abierto() > 0
				and anterior.firma == firma and anterior.reglas == reglas and not anterior.relanzar):
			# El mismo proceso sigue abierto y nada obliga a relanzarlo: el mensaje va directo.
			anterior.cancelar_temporizador()
			turno = _Turno(anterior)
			turno.compactar = compactar
			self._turno = turno
			self._cambiar_estado(Estado.TRABAJANDO)
			mensaje = self._mensaje_usuario(texto, adjuntos, uuid_mensaje)
			threading.Thread(target=self._enviar_tras_inicializar, args=(turno, mensaje), name="claude-envio", daemon=True).start()
			return
		if anterior is not None:
			self._retirar_proceso(anterior)
		vivo = _ProcesoClaude(firma, reglas)
		turno = _Turno(vivo)
		turno.compactar = compactar
		self._turno = turno
		self._modo_real = ""
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
		except (FileNotFoundError, ValueError, RuntimeError, remoto.ErrorRemoto) as error:
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
		# Lo que llega tras el resultado vuelve a empezar la espera de «inactiva» (no es un turno colgado).
		turno.ultima_actividad = time.monotonic()
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
		identificador = str(respuesta.get("request_id") or "")
		with self._candado:
			propia = self._peticiones.pop(identificador, None)
		if propia is not None:
			self._al_respuesta_propia(turno, propia[0], propia[1], respuesta)
			return
		if identificador != "ctrlia-inicio":
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
		if turno.vivo is not None and turno.vivo.proceso is not None:
			self._volver_a_control_remoto(turno.vivo)

	def _al_respuesta_propia(self, turno: _Turno, subtipo: str, anterior: Any, respuesta: dict[str, Any]) -> None:
		"""Respuesta a una petición de control que no espera nadie: los errores se dicen y se deshace lo que no valió."""
		error = respuesta.get("subtype") == "error"
		motivo = " ".join(str(respuesta.get("error") or "sin motivo").split())
		if subtipo == "set_permission_mode":
			if not error:
				cuerpo = respuesta.get("response") or {}
				self._modo_real = _modo_normalizado(cuerpo.get("mode") or self.ajustes.modo_permiso)
				return
			pedido = self.ajustes.modo_permiso
			log.warning("Claude Code no ha cambiado su modo de permisos a %s: %s", pedido, motivo)
			if pedido == "bypassPermissions":
				# Claude Code solo admite «Sin preguntas» si arrancó con él (``allowDangerouslySkipPermissions``, 2.1.308).
				# No se lanza siempre con --allow-dangerously-skip-permissions: ese modo solo existe si la persona lo
				# eligió, y entonces se relanza con --permission-mode bypassPermissions en su próximo mensaje.
				vivo = turno.vivo or self._vivo
				if vivo is not None:
					vivo.relanzar = True
				self._emitir(TipoEvento.AVISO, "Claude Code solo admite el modo «%s» desde el arranque: se aplicará con tu "
					"próximo mensaje, que reinicia Claude Code. Hasta entonces sigue en «%s»." % (_nombre_modo(pedido),
					_nombre_modo(anterior or self._modo_real or "default")), detalle_tecnico=motivo)
				return
			self.ajustes.modo_permiso = anterior or ""
			self._emitir(TipoEvento.AVISO, "Claude Code no ha aceptado el modo «%s» y sigue en «%s»." % (
				_nombre_modo(pedido), _nombre_modo(anterior or self._modo_real or "default")),
				modo_cambiado=anterior or "", detalle_tecnico=motivo)
		elif subtipo == "set_model":
			vivo, modelo = anterior if isinstance(anterior, tuple) else (None, "")
			if not error:
				# El proceso abierto ya usa el modelo nuevo: no hace falta relanzarlo por él.
				if vivo is not None and vivo.firma:
					vivo.firma = (*vivo.firma[:1], modelo if _RE_MODELO.match(modelo or "x") else "", *vivo.firma[2:])
				log.info("Modelo de Claude Code cambiado en marcha a %s", modelo or "el predeterminado")
				return
			log.info("Claude Code no ha cambiado el modelo en marcha (%s); se aplicará en el próximo mensaje", motivo)
			self._emitir(TipoEvento.AVISO, "Claude Code no ha podido cambiar de modelo ahora mismo: el modelo nuevo se "
				"usará desde tu próximo mensaje.", detalle_tecnico=motivo)
		elif subtipo == "stop_task" and error:
			log.info("Claude Code no ha parado la tarea %s: %s", anterior, motivo)

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
			self.ajustes.extra.pop("cortar_en", None)  # ya se cortó: los procesos siguientes retoman la sesión nueva
			self._modo_real = _modo_normalizado(datos.get("permissionMode"))
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
			turno.ultimo_estado = str(estado or "")
			if estado == "running":
				turno.pensando = False
				self._cambiar_estado(Estado.TRABAJANDO)
			elif estado == "requires_action":
				self._cambiar_estado(Estado.ESPERANDO_USUARIO)
			elif estado == "idle":
				self._comprobar_fin(turno)
		elif subtipo == "background_tasks_changed":
			self._al_tareas_fondo(turno, datos)
		elif subtipo == "compact_boundary":
			self._compactando = False
			meta = datos.get("compact_metadata") or datos.get("compactMetadata") or {}
			antes = meta.get("pre_tokens", meta.get("preTokens"))
			despues = meta.get("post_tokens", meta.get("postTokens"))
			texto = "Claude ha compactado la conversación para liberar contexto."
			if antes and despues is not None:
				texto += f" Pasó de {numero_legible(antes)} a {numero_legible(despues)} unidades de texto (tokens)."
			self._emitir(TipoEvento.AVISO, texto, compactado=True)
		elif subtipo == "status":
			self._al_estado_sistema(turno, datos)
		elif subtipo in ("task_started", "task_progress", "task_updated", "task_notification"):
			self._al_tarea(turno, subtipo, datos)
		elif subtipo == "permission_denied":
			herramienta = str(datos.get("tool_name") or "")
			self._emitir(TipoEvento.AVISO, f"Acción denegada: {herramienta}. {datos.get('message') or ''}".strip())
		elif subtipo == "api_retry":
			self._al_reintento(datos)
		elif subtipo == "model_fallback":
			original, nuevo = str(datos.get("original_model") or ""), str(datos.get("fallback_model") or "")
			motivo = MOTIVOS_CAMBIO_MODELO.get(str(datos.get("trigger") or ""), "el modelo ha fallado")
			self._emitir(TipoEvento.AVISO, "Claude sigue este turno con el modelo %s en lugar de %s porque %s." % (
				nuevo or "de reserva", original or "el elegido", motivo), modelo_cambiado=nuevo,
				detalle_tecnico=str(datos.get("content") or ""))
		elif subtipo == "memory_saved":
			rutas = [str(r) for r in datos.get("written_paths") or [] if isinstance(r, str)]
			verbo = str(datos.get("verb") or "").lower()
			hecho = "actualizado" if verbo.startswith("updat") else "guardado"
			cuantos = "1 recuerdo" if len(rutas) == 1 else f"{len(rutas)} recuerdos"
			self._emitir(TipoEvento.AVISO, f"Claude ha {hecho} {cuantos} en su memoria.", memoria_guardada=True,
				detalle_tecnico="\n".join(rutas))

	def _al_estado_sistema(self, turno: _Turno, datos: dict[str, Any]) -> None:
		"""``system/status``: compactación en marcha o fallida, y cambios de modo que hace Claude Code solo."""
		estado = datos.get("status")
		if estado == "compacting":
			if not self._compactando:
				self._compactando = True
				self._emitir(TipoEvento.AVISO, "Claude está compactando la conversación para liberar contexto. Puede "
					"tardar un poco.", compactando=True)
		else:
			self._compactando = False
		if datos.get("compact_result") == "failed":
			error = " ".join(str(datos.get("compact_error") or "").split())
			self._emitir(TipoEvento.AVISO, "Claude no ha podido compactar la conversación." + (f" Motivo: {error}" if error else ""),
				servicio=True, detalle_tecnico=error)
		modo = datos.get("permissionMode")
		if isinstance(modo, str) and modo:
			nuevo = _modo_normalizado(modo)
			anterior, self._modo_real = self._modo_real, nuevo
			elegido = _modo_normalizado(self.ajustes.modo_permiso)
			if anterior and nuevo != anterior and nuevo != elegido:
				# Claude ha cambiado de modo por su cuenta (al aprobar un plan, EnterPlanMode...).
				self.ajustes.modo_permiso = nuevo
				self._emitir(TipoEvento.AVISO, "Claude ha pasado al modo «%s»." % _nombre_modo(nuevo), modo_cambiado=nuevo)
			elif nuevo == elegido:
				self.ajustes.modo_permiso = self.ajustes.modo_permiso or nuevo

	def _al_reintento(self, datos: dict[str, Any]) -> None:
		"""``api_retry``: el servicio ha fallado y Claude Code lo reintenta (AIChat live_turn.py)."""
		intento, maximo = datos.get("attempt"), datos.get("max_retries")
		motivo = ERRORES_SERVICIO.get(str(datos.get("error") or ""), "")
		if not motivo and isinstance(datos.get("error_status"), int):
			motivo = "el servicio ha respondido con el código %d" % datos["error_status"]
		elif not motivo:
			motivo = "no se ha podido conectar"
		if isinstance(datos.get("no_response"), dict):
			motivo = "el servicio no ha respondido a tiempo"
		texto = "Problema con el servicio de Claude: %s." % motivo
		if intento:
			texto += " Reintento %s%s" % (intento, f" de {maximo}" if maximo else "")
			try:
				segundos = round(float(datos.get("retry_delay_ms") or 0) / 1000)
			except (TypeError, ValueError):
				segundos = 0
			texto += (" dentro de %d segundos." % segundos) if segundos > 1 else "."
		self._emitir(TipoEvento.AVISO, texto, servicio=True, detalle_tecnico=json.dumps(
			{c: datos.get(c) for c in ("attempt", "max_retries", "retry_delay_ms", "error_status", "error") if c in datos}))

	def _al_tareas_fondo(self, turno: _Turno, datos: dict[str, Any]) -> None:
		"""``background_tasks_changed`` (el conjunto entero, no los cambios): lo que sigue en segundo plano."""
		tareas: set[str] = set()
		descritas: dict[str, str] = {}
		for tarea in datos.get("tasks") or []:
			if isinstance(tarea, dict) and not tarea.get("ambient") and tarea.get("task_id"):
				identificador = str(tarea["task_id"])
				tareas.add(identificador)
				descritas[identificador] = " ".join(str(tarea.get("description") or "").split())
		vivo = turno.vivo
		if vivo is not None:
			# Las que ya no están han terminado aunque no llegara su aviso (``stop_task`` de una que no conocía...).
			for identificador, tarea in list(vivo.tareas.items()):
				if tarea.get("estado") == "en_marcha" and tarea.get("segundo_plano") and identificador not in tareas \
						and identificador in turno.tareas_fondo:
					tarea["estado"] = "detenida" if tarea.get("parando") else "completada"
					self._emitir(TipoEvento.ACTIVIDAD, id=identificador, estado=tarea["estado"])
		turno.tareas_fondo = tareas
		turno.tareas_descritas = descritas
		if tareas:
			if turno.temporizador is not None:
				# Sin tope mientras hay trabajo en segundo plano (AIChat #485).
				turno.temporizador.cancel()
				turno.temporizador = None
			self._cambiar_estado(Estado.SEGUNDO_PLANO)
		elif turno.tras_resultado and not turno.terminado:
			if turno.ultimo_estado == "idle":
				self._armar_espera(turno, corta=True)
			else:
				self._armar_espera(turno)

	def _al_tarea(self, turno: _Turno, subtipo: str, datos: dict[str, Any]) -> None:
		"""``task_started``, ``task_progress``, ``task_updated`` y ``task_notification`` → ``ACTIVIDAD``."""
		identificador = str(datos.get("task_id") or "")
		vivo = turno.vivo
		if not identificador or vivo is None:
			return
		tarea = vivo.tareas.setdefault(identificador, {"estado": "en_marcha"})
		evento: dict[str, Any] = {"id": identificador}
		llamada = str(datos.get("tool_use_id") or "")
		if llamada:
			vivo.tarea_de_llamada[llamada] = identificador
			tarea["llamada"] = llamada
		if subtipo == "task_started":
			clase = CLASES_TAREA.get(str(datos.get("task_type") or ""), "otra")
			tarea.update(clase=clase, estado="en_marcha", titulo=" ".join(str(datos.get("description") or "").split()),
				segundo_plano=bool(datos.get("is_backgrounded")), tipo_agente=str(datos.get("subagent_type") or ""))
			evento.update(clase=clase, estado="en_marcha", titulo=tarea["titulo"], segundo_plano=tarea["segundo_plano"],
				detenible=True)
			if tarea["tipo_agente"]:
				evento["tipo_agente"] = tarea["tipo_agente"]
			if isinstance(datos.get("prompt"), str) and datos["prompt"].strip():
				if clase == "orden":
					evento["orden"] = datos["prompt"].strip()
				else:
					evento["encargo"] = datos["prompt"].strip()
			if tarea.get("orden"):
				evento["orden"] = tarea["orden"]
			self._emitir(TipoEvento.ACTIVIDAD, **evento)
			for paso in vivo.pasos_sin_tarea.pop(llamada, []) if llamada else []:
				self._emitir(TipoEvento.ACTIVIDAD, id=identificador, paso_nuevo=paso)
			return
		if subtipo == "task_progress":
			ahora = time.monotonic()
			if ahora - self._ultimo_progreso.get(identificador, 0.0) < INTERVALO_PROGRESO:
				return  # los subagentes dan muchos: como mucho uno por segundo
			self._ultimo_progreso[identificador] = ahora
			resumen = " ".join(str(datos.get("summary") or "").split())
			descripcion = " ".join(str(datos.get("description") or "").split())
			ultima = str(datos.get("last_tool_name") or "")
			paso = resumen or (descripcion if descripcion and descripcion != tarea.get("titulo") else "")
			if not paso and ultima:
				paso = "Usa " + NOMBRES_HERRAMIENTAS.get(ultima, ultima).lower()
			if paso:
				evento["paso"] = _una_linea(paso)
			evento.update(_uso_de_tarea(datos.get("usage")))
			if len(evento) > 1:
				self._emitir(TipoEvento.ACTIVIDAD, **evento)
			return
		if subtipo == "task_updated":
			parche = datos.get("patch") if isinstance(datos.get("patch"), dict) else {}
			estado = ESTADOS_TAREA.get(str(parche.get("status") or ""))
			if estado:
				tarea["estado"] = evento["estado"] = estado
			if isinstance(parche.get("description"), str) and parche["description"].strip():
				evento["titulo"] = " ".join(parche["description"].split())
			if isinstance(parche.get("is_backgrounded"), bool):
				tarea["segundo_plano"] = evento["segundo_plano"] = parche["is_backgrounded"]
			if isinstance(parche.get("error"), str) and parche["error"].strip():
				evento["resumen"] = _una_linea(parche["error"], 2000)
			if len(evento) > 1:
				self._emitir(TipoEvento.ACTIVIDAD, **evento)
			return
		estado = ESTADOS_TAREA.get(str(datos.get("status") or ""), "completada")
		tarea["estado"] = evento["estado"] = estado
		if str(datos.get("summary") or "").strip():
			evento["resumen"] = str(datos["summary"]).strip()
		if str(datos.get("output_file") or "").strip():
			evento["archivo_salida"] = str(datos["output_file"]).strip()
		evento.update(_uso_de_tarea(datos.get("usage")))
		self._ultimo_progreso.pop(identificador, None)
		self._emitir(TipoEvento.ACTIVIDAD, **evento)

	def _paso_subagente(self, turno: _Turno, padre: str, paso: str) -> None:
		"""Un paso de un subagente (lo que llega con ``parent_tool_use_id``): a su ACTIVIDAD, nunca a la conversación."""
		vivo = turno.vivo
		if vivo is None or not paso:
			return
		tarea = vivo.tarea_de_llamada.get(padre)
		if tarea is None:
			pendientes = vivo.pasos_sin_tarea.setdefault(padre, [])
			if len(pendientes) < 20:
				pendientes.append(paso)
			return
		self._emitir(TipoEvento.ACTIVIDAD, id=tarea, paso_nuevo=paso)

	def _orden_en_fondo(self, turno: _Turno, tarea: str, llamada: str, salida: str) -> None:
		"""Una orden que ha pasado a segundo plano (``tool_use_result.backgroundTaskId``): su orden y su archivo de salida."""
		vivo = turno.vivo
		if vivo is not None:
			registro = vivo.tareas.setdefault(tarea, {"estado": "en_marcha", "clase": "orden"})
			registro.setdefault("clase", "orden")
			registro["segundo_plano"] = True
			if llamada:
				vivo.tarea_de_llamada.setdefault(llamada, tarea)
			if turno.ordenes.get(llamada):
				registro["orden"] = turno.ordenes[llamada]
		evento: dict[str, Any] = {"id": tarea, "clase": "orden", "segundo_plano": True, "detenible": True}
		if turno.ordenes.get(llamada):
			evento["orden"] = turno.ordenes[llamada]
		archivo = _RE_SALIDA_FONDO.search(salida or "")
		if archivo:
			evento["archivo_salida"] = archivo.group(1).strip()
		self._emitir(TipoEvento.ACTIVIDAD, **evento)

	def _de_subagente(self, turno: _Turno, datos: dict[str, Any]) -> None:
		"""Mensaje de un subagente (``assistant`` o ``user`` con ``parent_tool_use_id``) convertido en pasos."""
		padre = str(datos.get("parent_tool_use_id") or "")
		vivo = turno.vivo
		contenido = (datos.get("message") or {}).get("content")
		if isinstance(contenido, str):
			contenido = [{"type": "text", "text": contenido}] if datos.get("type") == "assistant" else []
		for bloque in contenido or []:
			if not isinstance(bloque, dict):
				continue
			tipo = bloque.get("type")
			if datos.get("type") == "assistant" and tipo == "tool_use":
				nombre = str(bloque.get("name") or "")
				if vivo is not None and bloque.get("id"):
					vivo.llamadas_de_subagente[str(bloque["id"])] = (padre, nombre)
				self._paso_subagente(turno, padre, paso_de_herramienta(nombre, bloque.get("input")))
			elif datos.get("type") == "assistant" and tipo == "text" and str(bloque.get("text") or "").strip():
				self._paso_subagente(turno, padre, _una_linea("Dice: " + str(bloque["text"])))
			elif datos.get("type") == "user" and tipo == "tool_result":
				texto = _texto_resultado(bloque.get("content")).strip()
				prefijo = "Falló: " if bloque.get("is_error") else "Resultado: "
				self._paso_subagente(turno, padre, _una_linea(prefijo + (texto or "sin salida")))

	def _al_parcial(self, turno: _Turno, datos: dict[str, Any]) -> None:
		if datos.get("parent_tool_use_id"):
			return  # los subagentes se siguen por sus mensajes completos (pasos de su ACTIVIDAD)
		evento = datos.get("event") or {}
		if evento.get("type") == "content_block_start" and (evento.get("content_block") or {}).get("type") == "thinking":
			if not turno.pensando:
				turno.pensando = True
				self._emitir(TipoEvento.AVISO, "Claude está pensando...", solo_estado=True)
			return
		if evento.get("type") == "message_start":
			turno.textos.setdefault("__actual__", "")
			turno.textos["__id__"] = str((evento.get("message") or {}).get("id") or "")
		elif evento.get("type") == "content_block_delta":
			delta = evento.get("delta") or {}
			if delta.get("type") == "thinking_delta" and not turno.pensando:
				turno.pensando = True
				self._emitir(TipoEvento.AVISO, "Claude está pensando...", solo_estado=True)
			if delta.get("type") == "text_delta" and delta.get("text"):
				turno.pensando = False
				identificador = turno.textos.get("__id__", "")
				turno.parciales[identificador] = turno.parciales.get(identificador, "") + str(delta["text"])
				self._emitir(TipoEvento.TEXTO_PARCIAL, str(delta["text"]), id=identificador)

	def _al_asistente(self, turno: _Turno, datos: dict[str, Any]) -> None:
		if datos.get("parent_tool_use_id"):
			self._de_subagente(turno, datos)
			return
		mensaje = datos.get("message") or {}
		identificador = str(mensaje.get("id") or uuid.uuid4().hex)
		# Uuid del registro en el transcript: con él se bifurca o se reintenta desde este mensaje (``cortar_en``).
		ancla = str(datos.get("uuid") or "")
		ancla = ancla if _RE_UUID.match(ancla) else ""
		for bloque in mensaje.get("content") or []:
			if not isinstance(bloque, dict):
				continue
			tipo = bloque.get("type")
			if tipo == "thinking":
				# Los firmados sin texto (razonamiento oculto) no dicen nada.
				pensado = str(bloque.get("thinking") or "").strip()
				if pensado:
					self._emitir(TipoEvento.RAZONAMIENTO, pensado, id=identificador)
				continue
			if tipo == "text" and bloque.get("text"):
				if turno.rescatados.get(identificador, "").strip() == str(bloque["text"]).strip():
					# Ya se rescató de los trozos al llegar una pregunta: no se repite.
					turno.rescatados.pop(identificador, None)
					continue
				texto = turno.textos.get(identificador, "")
				texto = (texto + "\n\n" if texto else "") + str(bloque["text"])
				turno.textos[identificador] = texto
				turno.texto_reciente = texto
				if ancla:
					self._emitir(TipoEvento.MENSAJE, texto, id=identificador, ancla=ancla)
				else:
					self._emitir(TipoEvento.MENSAJE, texto, id=identificador)
			elif tipo == "tool_use":
				nombre = str(bloque.get("name") or "")
				entrada = bloque.get("input") if isinstance(bloque.get("input"), dict) else {}
				id_herramienta = str(bloque.get("id") or "")
				turno.herramientas[id_herramienta] = nombre
				if nombre in ("Bash", "PowerShell") and isinstance(entrada.get("command"), str):
					turno.ordenes[id_herramienta] = entrada["command"]
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
			self._de_subagente(turno, datos)
			return
		mensaje = datos.get("message") or {}
		contenido = mensaje.get("content")
		if not isinstance(contenido, list):
			return
		resultado = datos.get("tool_use_result")
		for bloque in contenido:
			if not isinstance(bloque, dict) or bloque.get("type") != "tool_result":
				continue
			turno.texto_reciente = ""
			id_herramienta = str(bloque.get("tool_use_id") or "")
			nombre = turno.herramientas.get(id_herramienta, "")
			salida = _texto_resultado(bloque.get("content"))
			self._emitir(TipoEvento.HERRAMIENTA_FIN, herramienta=nombre, salida=salida[:4000],
				error=bool(bloque.get("is_error")), id=id_herramienta)
			if isinstance(resultado, dict) and resultado.get("backgroundTaskId"):
				self._orden_en_fondo(turno, str(resultado["backgroundTaskId"]), id_herramienta, salida)
		if isinstance(resultado, dict) and resultado.get("structuredPatch"):
			ruta = str(resultado.get("filePath") or "")
			lineas = []
			for trozo in resultado.get("structuredPatch") or []:
				if isinstance(trozo, dict):
					lineas.append(f"@@ -{trozo.get('oldStart')},{trozo.get('oldLines')} +{trozo.get('newStart')},{trozo.get('newLines')} @@")
					lineas.extend(str(linea) for linea in trozo.get("lines") or [])
			tipo = "nuevo" if resultado.get("type") == "create" else "modificado"
			self._emitir(TipoEvento.ARCHIVO, ruta, ruta=ruta, tipo=tipo, diff="\n".join(lineas))

	@staticmethod
	def _coste_de(vivo: _ProcesoClaude | None, datos: dict[str, Any]) -> tuple[float | None, float | str]:
		"""(acumulado de la sesión, coste de esta respuesta) de un ``result``.

		``total_cost_usd`` es la suma de TODA la sesión, no lo de la respuesta: dentro
		de un proceso, cada resultado suma al anterior, y el primero de un proceso que
		reanuda una sesión trae además el coste guardado de antes. Lo de la respuesta
		es lo que crece desde el resultado anterior del mismo proceso; en el primero,
		todo, salvo que traiga coste restaurado (los tokens de ``modelUsage``, que
		acumulan la sesión, pasan de los de ``usage``, que son solo de esta respuesta).
		``""`` si no se puede saber.
		"""
		total = datos.get("total_cost_usd")
		if not isinstance(total, (int, float)) or isinstance(total, bool):
			return None, ""
		total = float(total)
		anterior = vivo.coste_anterior if vivo is not None else None
		if vivo is not None:
			vivo.coste_anterior = total
		if anterior is not None:
			return total, max(0.0, total - anterior) if total >= anterior else ""
		uso = datos.get("usage") or {}
		claves = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
		propios = sum(int(uso.get(c) or 0) for c in claves if isinstance(uso, dict))
		claves_modelo = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
		acumulados = sum(
			int(info.get(c) or 0) for info in (datos.get("modelUsage") or {}).values() if isinstance(info, dict)
			for c in claves_modelo)
		if acumulados > propios:
			return total, ""
		return total, total

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
		coste, coste_turno = self._coste_de(turno.vivo, datos)
		self._emitir(TipoEvento.USO, entrada=int(uso.get("input_tokens") or 0), salida=int(uso.get("output_tokens") or 0),
			contexto_total=turno.contexto_total or None, coste=coste, coste_turno=coste_turno)
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
				self._emitir(TipoEvento.ERROR, texto, sin_sesion="/login" in str(datos.get("result") or ""),
					detalle_tecnico=detalle_tecnico(datos, turno.proceso.errores_recientes(40) if turno.proceso else ()))
		turno.tras_resultado = True
		if not turno.vio_estados:
			# CLI sin eventos de estado: el resultado marca el final del turno.
			self._comprobar_fin(turno, forzar=True)
		elif turno.temporizador is None:
			self._armar_espera(turno)

	def _armar_espera(self, turno: _Turno, corta: bool = False) -> None:
		"""Tope tras el resultado si nunca llega «inactiva». Nunca con tareas en segundo plano (AIChat #485).

		:param corta: la última tarea en segundo plano acaba de terminar con Claude Code inactivo (``ESPERA_TRAS_FONDO``).
		"""
		with self._candado:
			if turno.temporizador is not None:
				turno.temporizador.cancel()
				turno.temporizador = None
			if turno.terminado or turno.tareas_fondo:
				return
			if corta:
				turno.temporizador = threading.Timer(ESPERA_TRAS_FONDO, self._fin_tras_fondo, args=(turno,))
			else:
				turno.temporizador = threading.Timer(ESPERA_TRAS_RESULTADO, self._forzar_fin, args=(turno,))
			turno.temporizador.daemon = True
			turno.temporizador.start()

	def _fin_tras_fondo(self, turno: _Turno) -> None:
		"""La última tarea en segundo plano acabó con Claude Code inactivo y no ha empezado nada más: fin del turno."""
		with self._candado:
			turno.temporizador = None
			listo = (not turno.terminado and not turno.tareas_fondo and turno.ultimo_estado == "idle"
				and turno.resultados_pendientes == 0)
		if listo:
			self._comprobar_fin(turno)
		elif not turno.terminado:
			self._armar_espera(turno)

	def _al_limites(self, datos: dict[str, Any]) -> None:
		"""``rate_limit_event``: todas las ventanas que vengan, el uso adicional de pago y los límites alcanzados."""
		info = datos.get("rate_limit_info") or {}
		ventanas = dict(info.get("unifiedWindows") or {})
		tipo = str(info.get("rateLimitType") or "")
		if not ventanas and tipo and info.get("utilization") is not None:
			ventanas[tipo] = {"utilization": info.get("utilization"), "resetsAt": info.get("resetsAt")}
		for clave, ventana in ventanas.items():
			if not isinstance(ventana, dict):
				continue
			uso = ventana.get("utilization")
			try:
				porcentaje = round(float(uso) * 100)
			except (TypeError, ValueError):
				log.debug("Uso de la ventana %s que no es un número: %r", clave, uso)
				continue
			nombre = VENTANAS_LIMITE.get(str(clave), str(clave).replace("_", " "))
			self._emitir(TipoEvento.LIMITES, f"Has usado el {porcentaje} % del límite de {nombre}.",
				porcentaje=porcentaje, ventana=clave, reinicio=ventana.get("resetsAt"))
		if (info.get("isUsingOverage") or info.get("overageInUse")) and not self._avisado_uso_extra:
			self._avisado_uso_extra = True
			self._emitir(TipoEvento.AVISO, "Claude está usando uso adicional de pago: has pasado el límite de tu "
				"suscripción y lo que sigue se cobra aparte. Puedes detenerlo con Control+punto.", uso_extra=True)
		if info.get("status") == "rejected":
			clave = (tipo, info.get("resetsAt"))
			if clave not in self._limites_avisados:
				self._limites_avisados.add(clave)
				nombre = VENTANAS_LIMITE.get(tipo, "tu suscripción")
				cuando = _hora_legible(info.get("resetsAt"))
				self._emitir(TipoEvento.AVISO, "Has llegado al límite de uso de %s: Claude no puede seguir hasta que se "
					"reinicie%s." % (nombre, " " + cuando if cuando else ""), servicio=True, limite_alcanzado=True,
					detalle_tecnico=json.dumps(info, ensure_ascii=False)[:MAX_DETALLE_RESULTADO])

	def _comprobar_fin(self, turno: _Turno, forzar: bool = False) -> None:
		if turno.terminado:
			return
		# Detenido con Control+punto: el turno acaba aunque sigan órdenes en segundo plano (los subagentes ya se pararon).
		fondo = turno.tareas_fondo and not turno.interrumpido
		if not forzar and (turno.resultados_pendientes > 0 or fondo):
			if fondo:
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

	def _forzar_fin(self, turno: _Turno, detener: bool = False) -> None:
		"""Cierra el proceso de un turno que no termina. Tras el resultado, nunca con tareas en segundo plano, y si ha
		llegado algo hace poco se vuelve a esperar (no está colgado). ``detener``: lo pidió la persona (Control+punto)."""
		if turno.terminado or turno.proceso is None:
			return
		if not detener:
			with self._candado:
				turno.temporizador = None
				if turno.tareas_fondo:
					return
				restante = ESPERA_TRAS_RESULTADO - (time.monotonic() - turno.ultima_actividad)
				if restante > 1:
					turno.temporizador = threading.Timer(restante, self._forzar_fin, args=(turno,))
					turno.temporizador.daemon = True
					turno.temporizador.start()
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

	def _tareas_perdidas(self, turno: _Turno) -> list[str]:
		"""Al morir el proceso: las tareas en segundo plano que seguían vivas (su descripción) pasan a «fallida»."""
		vivo = turno.vivo
		perdidas = [turno.tareas_descritas.get(t, "") for t in sorted(turno.tareas_fondo)]
		if vivo is not None:
			for identificador, tarea in vivo.tareas.items():
				if tarea.get("estado") == "en_marcha":
					tarea["estado"] = "fallida"
					self._emitir(TipoEvento.ACTIVIDAD, id=identificador, estado="fallida",
						resumen="Claude Code se cerró antes de que terminara.")
		return perdidas

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
		tecnico = detalle_tecnico(resultado, turno.proceso.errores_recientes(40) if turno.proceso else ())
		vivas = self._tareas_perdidas(turno) if salio and not turno.interrumpido and not self._cerrada else []
		if vivas:
			# El proceso ha muerto con trabajo en segundo plano: ese trabajo se ha perdido, no es un turno correcto.
			correcto = False
			nombres = ", ".join("«%s»" % t for t in vivas if t) or ""
			self._emitir(TipoEvento.ERROR, "Claude Code se ha cerrado con %s en segundo plano sin terminar%s: se ha%s perdido." % (
				"1 tarea" if len(vivas) == 1 else f"{len(vivas)} tareas", " (" + nombres + ")" if nombres else "",
				"" if len(vivas) == 1 else "n"), detalle_tecnico=tecnico)
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
				self._emitir(TipoEvento.ERROR, texto, detalle_tecnico=tecnico)
		self._emitir(TipoEvento.FIN_TURNO, correcto=correcto, interrumpido=turno.interrumpido,
			resumen=str(resultado.get("result") or ""))
		self._cambiar_estado(Estado.ERROR if (not correcto and not turno.interrumpido and (not resultado or vivas))
			else Estado.INACTIVA)
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
		subagente = self._subagente_de_peticion(turno, peticion)
		if subagente and solicitud.titulo.startswith("Claude "):
			# Lo pide un subagente, no la conversación principal (``agent_id`` o la llamada, 2.1.308).
			solicitud.titulo = "El subagente «%s» %s" % (subagente, solicitud.titulo[len("Claude "):])
			solicitud.datos["subagente"] = subagente
		solicitud.contexto = self._texto_antes_de_preguntar(turno)
		self._cambiar_estado(Estado.ESPERANDO_USUARIO)
		self._emitir(TipoEvento.PERMISO, solicitud.titulo, solicitud=solicitud)

	def _subagente_de_peticion(self, turno: _Turno, peticion: dict[str, Any]) -> str:
		"""Nombre del subagente que hace la petición («» si es la conversación principal)."""
		vivo = turno.vivo
		if vivo is None:
			return ""
		tarea = ""
		agente = str(peticion.get("agent_id") or "")
		if agente and agente in vivo.tareas:
			tarea = agente
		llamada = str(peticion.get("tool_use_id") or "")
		if not tarea and llamada in vivo.llamadas_de_subagente:
			padre = vivo.llamadas_de_subagente[llamada][0]
			tarea = vivo.tarea_de_llamada.get(padre, "")
			if not tarea:
				return "sin nombre"
		if not tarea:
			return "sin nombre" if agente else ""
		datos = vivo.tareas.get(tarea, {})
		return str(datos.get("titulo") or datos.get("tipo_agente") or "sin nombre")

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
		brutas = entrada.get("questions") or []
		if isinstance(brutas, str):
			# Algún modelo manda la lista como texto JSON en vez de como lista.
			try:
				brutas = json.loads(brutas)
			except ValueError:
				brutas = []
		if isinstance(brutas, dict):
			brutas = [brutas]
		for numero, pregunta in enumerate(brutas if isinstance(brutas, list) else []):
			if not isinstance(pregunta, dict):
				continue
			validas = [o if isinstance(o, dict) else {"label": o} for o in pregunta.get("options") or []
				if isinstance(o, dict) or (isinstance(o, str) and o.strip())]
			opciones = [(str(o.get("label") or ""), str(o.get("description") or "")) for o in validas]
			# «preview»: lo que Claude quiere que veas con esa opción (un borrador, código, un esquema).
			previas = [str(o.get("preview") or "") for o in validas]
			preguntas.append(Pregunta(
				id=str(pregunta.get("question") or numero), texto=str(pregunta.get("question") or ""),
				encabezado=str(pregunta.get("header") or ""), opciones=opciones,
				multiple=bool(pregunta.get("multiSelect")),
				# Claude Code admite siempre una respuesta escrita además de sus opciones.
				admite_otra=True,
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


# ---------------------------------------------------------------------------
# Preguntar aparte y cortar una conversación en un mensaje
# ---------------------------------------------------------------------------

#: Herramientas de una pregunta aparte: puede mirar archivos, nunca cambiar nada.
HERRAMIENTAS_APARTE = "Read,Grep,Glob"
#: Lo que se dice si la conversación aún no tiene ninguna respuesta terminada.
SIN_RESPUESTA_TERMINADA = ("Todavía no hay ninguna respuesta terminada en esta conversación: espera a que Claude "
	"responda y vuelve a preguntar.")
#: ``corte_terminado`` cuando la conversación se compactó tras su última respuesta terminada (nunca es un uuid).
CORTE_COMPACTADA = "<compactada>"
#: Caracteres como mucho de las preguntas aparte anteriores que van delante de una nueva.
MAX_CONTEXTO_APARTE = 20000
#: Segundos sin recibir nada de una pregunta aparte tras los que se da por perdida.
ESPERA_APARTE = 300.0
#: Cuánto se lee del final del transcript para buscar el corte (AIChat ``CUT_STEPS``).
_PASOS_CORTE = (64 * 1024, 1024 * 1024, 16 * 1024 * 1024)
_RE_ORDEN_LOCAL = re.compile(r"\s*<(command-name|local-command-(stdout|stderr|caveat))>")


def es_respuesta_terminada(registro: Any) -> bool:
	"""Si un registro del transcript es texto de Claude que cerró un turno (AIChat #559).

	Del asistente, no de un subagente (``isSidechain``), no el texto sintético con el que Claude Code cierra
	un turno fallido (``isApiErrorMessage``, modelo ``<synthetic>``), sin llamar a ninguna herramienta y con texto.
	"""
	if not isinstance(registro, dict) or registro.get("type") != "assistant":
		return False
	if registro.get("isSidechain") or registro.get("isApiErrorMessage"):
		return False
	mensaje = registro.get("message")
	if not isinstance(mensaje, dict) or mensaje.get("model") == "<synthetic>" or mensaje.get("stop_reason") == "tool_use":
		return False
	contenido = mensaje.get("content")
	if isinstance(contenido, str):
		return bool(contenido.strip())
	if not isinstance(contenido, list):
		return False
	bloques = [b for b in contenido if isinstance(b, dict)]
	if any(b.get("type") == "tool_use" for b in bloques):
		return False
	return any(b.get("type") == "text" and str(b.get("text") or "").strip() for b in bloques)


def _parte_de_un_turno(registro: dict[str, Any]) -> bool:
	if registro.get("type") == "assistant":
		return True
	if registro.get("type") != "user" or registro.get("isMeta"):
		return False
	mensaje = registro.get("message")
	contenido = mensaje.get("content") if isinstance(mensaje, dict) else None
	if isinstance(contenido, list):
		if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in contenido):
			return True
		contenido = "\n".join(str(b.get("text") or "") for b in contenido if isinstance(b, dict) and b.get("type") == "text")
	return not (isinstance(contenido, str) and _RE_ORDEN_LOCAL.match(contenido))


def _conservado_por(limite: Any, identificador: str) -> bool:
	"""Si una compactación manual conservó el registro (``compactMetadata.preservedMessages``)."""
	if not isinstance(limite, dict):
		return False
	meta = limite.get("compactMetadata") or limite.get("compact_metadata")
	if not isinstance(meta, dict) or meta.get("trigger") != "manual":
		return False
	conservados = meta.get("preservedMessages")
	if not isinstance(conservados, dict):
		return False
	return any(isinstance(ids, list) and identificador in ids for ids in (conservados.get("uuids"), conservados.get("allUuids")))


def corte_terminado(lineas: Sequence[str]) -> str:
	"""Dónde acaba la copia de una pregunta aparte: el uuid de la última respuesta terminada.

	«» si no hay ninguna; ``CORTE_COMPACTADA`` si la conversación se compactó después y la compactación no la
	conservó. Siempre se corta, aunque la conversación acabe ahí: un turno en marcha o detenido a medias haría
	que Claude respondiera también a ese mensaje (AIChat aside.py ``finished_cut``).
	"""
	compactacion: Any = None
	turno_despues = False
	for linea in reversed(list(lineas)):
		if '"assistant"' not in linea and "ompact" not in linea and not (compactacion is not None and '"user"' in linea):
			continue
		try:
			registro = json.loads(linea)
		except ValueError:
			continue
		if not isinstance(registro, dict) or registro.get("isSidechain"):
			continue
		if registro.get("type") == "system" and registro.get("subtype") == "compact_boundary":
			if not isinstance(compactacion, dict):
				compactacion = registro
			continue
		if registro.get("isCompactSummary"):
			compactacion = compactacion or True
			continue
		if es_respuesta_terminada(registro):
			identificador = registro.get("uuid")
			if not isinstance(identificador, str) or not _RE_UUID.match(identificador):
				return ""
			if compactacion is not None and (turno_despues or not _conservado_por(compactacion, identificador)):
				return CORTE_COMPACTADA
			return identificador
		if isinstance(compactacion, dict) and _parte_de_un_turno(registro):
			turno_despues = True
	return ""


def _lineas_finales(ruta: Path, limite: int) -> tuple[list[str], bool]:
	"""Las líneas completas de los últimos ``limite`` bytes de ``ruta`` y si se ha leído desde el principio."""
	with open(ruta, "rb") as archivo:
		tamano = os.fstat(archivo.fileno()).st_size
		inicio = max(0, tamano - limite)
		archivo.seek(inicio)
		datos = archivo.read(tamano - inicio)
		if inicio > 0:
			archivo.seek(inicio - 1)
			if archivo.read(1) != b"\n":
				salto = datos.find(b"\n")
				datos = datos[salto + 1:] if salto >= 0 else b""
	return datos.decode("utf-8", errors="replace").splitlines(), inicio == 0


def corte_terminado_de_archivo(ruta: Path) -> str:
	"""``corte_terminado`` de un transcript en disco, leyendo solo el final que haga falta. ``OSError`` si no se puede."""
	for limite in (*_PASOS_CORTE, 1 << 62):
		lineas, entero = _lineas_finales(ruta, limite)
		corte = corte_terminado(lineas)
		if corte or entero:
			return corte
	return ""


def registro_padre(ruta: Path, identificador: str) -> str | None:
	"""El ``parentUuid`` del registro ``identificador`` de un transcript: «» si es el primero, ``None`` si no está."""
	with open(ruta, "rb") as archivo:
		for linea in archivo:
			if identificador.encode("ascii") not in linea:
				continue
			try:
				registro = json.loads(linea)
			except ValueError:
				continue
			if isinstance(registro, dict) and registro.get("uuid") == identificador:
				padre = registro.get("parentUuid")
				return padre if isinstance(padre, str) else ""
	return None


def pregunta_con_contexto(anteriores: Sequence[tuple[str, str]], pregunta: str, maximo: int = MAX_CONTEXTO_APARTE) -> str:
	"""La pregunta aparte con las anteriores y sus respuestas delante, marcadas como contexto.

	Se conservan las más recientes que quepan en ``maximo`` caracteres (sin contar la pregunta nueva).
	"""
	bloques: list[str] = []
	usado = 0
	for anterior, respuesta in reversed(list(anteriores or ())):
		bloque = "Pregunta: %s\nRespuesta: %s" % (str(anterior).strip(), str(respuesta).strip())
		if usado + len(bloque) > maximo:
			break
		bloques.insert(0, bloque)
		usado += len(bloque) + 2
	if not bloques:
		return pregunta
	return ("[Contexto: preguntas aparte que ya te hice sobre esta conversación, con tus respuestas. No forman parte "
		"de la conversación.]\n\n" + "\n\n".join(bloques) + "\n\n[Fin del contexto. Mi pregunta nueva es:]\n" + pregunta)


def _ejecutar_aparte(orden: list[str], carpeta: Path | None, texto: str, al_texto: Callable[[str], None] | None,
		cancelar: threading.Event | None, permitir_clave_api: bool) -> str:
	"""Lanza la copia de una pregunta aparte, le pasa la pregunta y devuelve la respuesta completa."""
	inicializado = threading.Event()
	fin = threading.Event()
	estado: dict[str, Any] = {"textos": {}, "resultado": None, "problema": "", "actividad": time.monotonic()}
	caja: list[procesos.ProcesoLineas] = []

	def al_linea(linea: str) -> None:
		estado["actividad"] = time.monotonic()
		datos = procesos.json_o_nada(linea)
		if not datos or datos.get("parent_tool_use_id"):
			return
		tipo = datos.get("type")
		if tipo == "control_response":
			if (datos.get("response") or {}).get("request_id") == "ctrlia-aparte-inicio":
				inicializado.set()
		elif tipo == "control_request" and caja:
			# Nadie contesta permisos en una pregunta aparte (``--permission-prompts none``): por si acaso, se rechaza.
			caja[0].escribir_json({"type": "control_response", "response": {"subtype": "error",
				"request_id": str(datos.get("request_id") or ""), "error": "Una pregunta aparte no puede pedir nada"}})
		elif tipo == "system" and datos.get("subtype") == "init":
			if str(datos.get("apiKeySource") or "none") not in ("none", "") and not permitir_clave_api:
				estado["problema"] = ("Claude Code iba a usar una clave de API y podría cobrarte por uso: no se ha preguntado. "
					"Si de verdad quieres usarla, actívalo en Preferencias, Agentes.")
				fin.set()
		elif tipo == "stream_event":
			evento = datos.get("event") or {}
			delta = evento.get("delta") or {}
			if evento.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text") and al_texto:
				try:
					al_texto(str(delta["text"]))
				except Exception:  # noqa: BLE001 - quien escucha no debe cortar la respuesta
					log.exception("Falló quien recibía la respuesta aparte")
		elif tipo == "assistant":
			mensaje = datos.get("message") or {}
			partes = [str(b.get("text") or "") for b in mensaje.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
			if any(p.strip() for p in partes):
				identificador = str(mensaje.get("id") or len(estado["textos"]))
				anterior = estado["textos"].get(identificador, "")
				estado["textos"][identificador] = (anterior + "\n\n" if anterior else "") + "\n\n".join(p for p in partes if p.strip())
		elif tipo == "result":
			estado["resultado"] = datos
			fin.set()

	proceso = procesos.ProcesoLineas(orden, al_linea, lambda _codigo: fin.set(), carpeta=carpeta, entorno=entorno_claude(),
		nombre="claude-aparte")
	caja.append(proceso)
	try:
		proceso.iniciar()
	except OSError as error:
		raise RuntimeError(f"No se pudo iniciar Claude Code: {error}") from error
	try:
		proceso.escribir_json({"type": "control_request", "request_id": "ctrlia-aparte-inicio",
			"request": {"subtype": "initialize", "hooks": None}})
		limite = time.monotonic() + ESPERA_INICIALIZAR
		while not inicializado.wait(0.1) and not fin.is_set() and time.monotonic() < limite:
			if cancelar is not None and cancelar.is_set():
				raise RuntimeError("Pregunta cancelada.")
		if cancelar is not None and cancelar.is_set():
			raise RuntimeError("Pregunta cancelada.")
		if not fin.is_set():
			proceso.escribir_json({"type": "user", "session_id": "", "parent_tool_use_id": None, "uuid": str(uuid.uuid4()),
				"message": {"role": "user", "content": texto}})
		while not fin.wait(0.2):
			if cancelar is not None and cancelar.is_set():
				raise RuntimeError("Pregunta cancelada.")
			if time.monotonic() - estado["actividad"] > ESPERA_APARTE:
				raise RuntimeError("Claude Code no ha contestado a tiempo.")
	finally:
		proceso.cerrar_entrada()
		if not proceso.esperar_fin(2):
			proceso.terminar(espera=2)
	if estado["problema"]:
		raise RuntimeError(estado["problema"])
	resultado = estado["resultado"] or {}
	errores = " ".join(proceso.errores_recientes(5))
	if not resultado or resultado.get("is_error"):
		motivo = str(resultado.get("result") or "") or "; ".join(str(e) for e in resultado.get("errors") or []) or errores
		if "No message found" in motivo:
			raise RuntimeError("Claude Code no encuentra la última respuesta terminada de esta conversación: vuelve a "
				"preguntar cuando Claude haya respondido otra vez.")
		if _RE_SIN_TRANSCRIPCION.search(motivo):
			raise RuntimeError("Claude Code ya no tiene guardada esta conversación: no se puede preguntar sobre ella.")
		raise RuntimeError("Claude no ha podido responder a la pregunta." + (f" Detalle: {motivo[-300:]}" if motivo else ""))
	respuesta = "\n\n".join(estado["textos"].values()).strip() or str(resultado.get("result") or "").strip()
	return respuesta
