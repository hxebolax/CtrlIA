"""Imitación mínima de ``claude -p`` en modo stream-json para las pruebas.

Modo por la variable ``FAKE_CLAUDE_MODO``:

- ``normal``: responde con texto.
- ``permiso``: pide permiso para ``Bash`` y responde según la decisión.
- ``pregunta``: hace una pregunta con AskUserQuestion.
- ``clave_api``: informa de que usa una clave de API.
- ``sin_estados``: no emite ``session_state_changed`` (CLI antiguo).
- ``cuelga``: no termina nunca (para probar «detener»).
- ``mcp_paralelo``: pide a la vez tres permisos para la misma herramienta MCP
  (y vuelve a entregar el primero, como hace Claude Code al reconectar).
- ``mcp_interaccion``: pide permiso para una herramienta MCP que exige a la persona.
- ``carpeta``: pide permiso para un mkdir fuera de la carpeta, con las sugerencias
  que da Claude Code 2.1.238 (regla, carpeta y modo).
- ``anadir``: a mitad de turno espera otro mensaje del usuario y lo atiende en el mismo turno.
- ``sin_transcripcion``: falla como Claude Code 2.1.308 con ``--resume`` de una sesión borrada: un ``result``
  con ``errors`` y código 1, sin ``init``. ``sin_transcripcion_stderr``: solo por la salida de errores.
- ``anadir_sin_eco``: como ``anadir``, pero se queda con el mensaje sin recogerlo (sin eco) y sigue esperando.
- ``anadir_tarde``: ese mensaje llega cuando ya dio su resultado: lo responde aparte, como Claude Code 2.1.308.
- ``anadir_muere``: se cierra de golpe nada más recibir ese mensaje, sin recogerlo.
- ``mcp``: habla con el servidor MCP «en proceso» de Ctrl+IA (``sdkMcpServers``) con
  ``control_request`` ``mcp_message`` (initialize, notificación, tools/list y tools/call
  ``avisar``) y responde con lo que contestó la herramienta.
- ``mcp_estado``: un servidor que falla y no vuelve, otro que pide iniciar sesión y otro
  conectado; atiende ``mcp_status``, ``mcp_reconnect`` y ``mcp_toggle`` como Claude Code 2.1.308.
- ``mcp_recupera``: como ``mcp_estado``, pero el que falla conecta al reintentarlo.
- ``mcp_tarda``: no contesta a ``initialize`` hasta pasado ``FAKE_CLAUDE_TARDA`` (segundos).
- ``mcp_sesiones``: habla con ``ccd_session_mgmt`` (y con un servidor que no existe): hace las
  llamadas de ``FAKE_CLAUDE_LLAMADAS`` (JSON: ``[["list_sessions", {}], ...]``) y anota las respuestas.

- ``tareas``: lanza un subagente en segundo plano (``task_started``, ``background_tasks_changed``, pasos con
  ``parent_tool_use_id``, ``task_progress``), da su resultado y el subagente acaba ``FAKE_CLAUDE_FONDO`` segundos
  después (``task_updated``, ``task_notification``) o al recibir ``stop_task``; entonces queda inactivo.
- ``fondo_muere``: como ``tareas``, pero el proceso muere tras el resultado con el subagente vivo.
- ``permiso_subagente``: el subagente pide permiso para una orden (``can_use_tool`` con ``agent_id``).
- ``sistema``: compactación (``status``, ``compact_boundary`` con ``compact_metadata``, fallo), cambio de modo por su
  cuenta, ``api_retry``, ``model_fallback``, ``memory_saved``, ``rate_limit_event`` con uso adicional y rechazo, y
  razonamiento (``thinking``).
- ``orden_fondo``: una orden que pasa a segundo plano (``tool_use_result.backgroundTaskId``).
- ``error_resultado``: un ``result`` con ``error_max_turns`` y un secreto en la salida de errores.

Un mensaje «/compact» (salvo el primero) compacta como 2.1.308. Con ``--resume-session-at`` y
``--no-session-persistence`` (preguntar aparte) responde «aparte:<pregunta>» y termina.

Atiende ``set_model`` (``FAKE_CLAUDE_SET_MODEL=error`` lo rechaza), ``set_permission_mode`` (rechaza
``bypassPermissions`` si no arrancó con él), ``stop_task``, ``get_context_usage``, ``mcp_authenticate`` y
``remote_control`` con los formatos de 2.1.308.

En cualquier modo atiende ``rewind_files`` como Claude Code 2.1.238: conoce el uuid
de cada mensaje recibido (y los de ``FAKE_CLAUDE_PUNTOS``, separados por comas,
para un proceso nuevo con ``--resume``); con otro, contesta el error «No file
checkpoint found for this message.»; sin ``CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING``,
``canRewind`` falso.

``FAKE_CLAUDE_REGISTRO``: archivo donde se anotan los argumentos y el entorno.

Como el de verdad, atiende varios mensajes seguidos en el mismo proceso: tras
quedar inactivo sigue leyendo y cada mensaje nuevo es otro turno («Hola mundo 2»,
«Hola mundo 3»...). Anota las peticiones de control (``set_permission_mode``) y
termina al cerrarse stdin.

Con ``--replay-user-messages``, cada mensaje se devuelve con su uuid al recogerlo
(``{"type": "user", "isReplay": true, ...}``), como Claude Code 2.1.308.
"""

import json
import os
import queue
import sys
import threading
import time

MODO = os.environ.get("FAKE_CLAUDE_MODO", "normal")
#: uuid de los mensajes con «punto» de archivos (los recibidos y los de FAKE_CLAUDE_PUNTOS).
PUNTOS = {u for u in os.environ.get("FAKE_CLAUDE_PUNTOS", "").split(",") if u}
#: Servidores MCP de ``mcp_estado`` y ``mcp_recupera``, como los cuenta ``mcp_status``.
SERVIDORES_MCP = {
	"roto": {"name": "roto", "status": "failed", "error": "Connection closed", "scope": "project", "source": "project",
		"config": {"type": "stdio", "command": "no-existe", "args": []}},
	"entrar": {"name": "entrar", "status": "needs-auth", "scope": "user", "source": "user",
		"config": {"type": "http", "url": "https://mcp.example.com/mcp"}},
	"bien": {"name": "bien", "status": "connected", "scope": "project", "source": "project", "tools": [{"name": "eco"}]},
}


def escribir(objeto):
	sys.stdout.write(json.dumps(objeto, ensure_ascii=False) + "\n")
	sys.stdout.flush()


#: Lo que llega por la entrada, leído por un hilo (así se puede esperar con tiempo límite).
_ENTRADA = queue.Queue()


def _lector():
	while True:
		linea = sys.stdin.readline()
		if not linea:
			_ENTRADA.put(None)
			return
		_ENTRADA.put(json.loads(linea))


def leer():
	return _ENTRADA.get()


def leer_con_espera(segundos):
	"""La siguiente línea, o ``FALTA`` si no llega nada en ``segundos``."""
	try:
		return _ENTRADA.get(timeout=segundos)
	except queue.Empty:
		return FALTA


FALTA = object()


def anotar(datos):
	ruta = os.environ.get("FAKE_CLAUDE_REGISTRO")
	if ruta:
		with open(ruta, "a", encoding="utf-8") as archivo:
			archivo.write(json.dumps(datos, ensure_ascii=False) + "\n")


def estado(valor):
	if MODO != "sin_estados":
		escribir({"type": "system", "subtype": "session_state_changed", "state": valor})


def esperar_mensaje():
	"""Lee hasta el siguiente mensaje del usuario; contesta a ``initialize`` y anota lo demás."""
	while True:
		datos = leer()
		if datos is None:
			return None
		if datos.get("type") == "control_request" and datos["request"]["subtype"] == "initialize":
			if MODO in ("mcp", "mcp_sesiones"):
				anotar({"inicio": datos["request"]})
			if MODO == "mcp_tarda":
				time.sleep(float(os.environ.get("FAKE_CLAUDE_TARDA", "0")))
			escribir({"type": "control_response", "response": {"subtype": "success", "request_id": datos["request_id"],
				"response": {"commands": [{"name": "compact", "description": "Compacta", "argumentHint": ""}], "models": []}}})
			continue
		if datos.get("type") == "control_request" and datos["request"]["subtype"] == "rewind_files":
			anotar({"control": datos["request"]})
			deshacer(datos)
			continue
		if datos.get("type") == "control_request" and datos["request"]["subtype"].startswith("mcp_") \
				and datos["request"]["subtype"] != "mcp_authenticate":
			anotar({"control": datos["request"]})
			control_mcp(datos)
			continue
		if datos.get("type") == "control_request":
			anotar({"control": datos["request"]})
			control_general(datos)
			continue
		if datos.get("type") == "user":
			if datos.get("uuid"):
				PUNTOS.add(datos["uuid"])
			return datos


def control_mcp(datos):
	"""Respuesta a ``mcp_status``, ``mcp_reconnect`` y ``mcp_toggle`` con los formatos de Claude Code 2.1.308."""
	peticion = datos["request"]
	subtipo, nombre = peticion["subtype"], peticion.get("serverName")
	cuerpo = {"subtype": "success", "request_id": datos["request_id"], "response": {}}
	if subtipo == "mcp_status":
		cuerpo["response"] = {"mcpServers": list(SERVIDORES_MCP.values())}
	elif subtipo == "mcp_reconnect" and nombre == "roto" and MODO == "mcp_recupera":
		SERVIDORES_MCP["roto"] = {"name": "roto", "status": "connected", "source": "project", "tools": [{"name": "eco"}]}
	elif subtipo == "mcp_reconnect" and nombre == "roto":
		cuerpo = {"subtype": "error", "request_id": datos["request_id"], "error": "Connection closed"}
	elif subtipo == "mcp_toggle":
		SERVIDORES_MCP[nombre]["status"] = "connected" if peticion.get("enabled") else "disabled"
	escribir({"type": "control_response", "response": cuerpo})


#: Tareas en marcha de ``tareas`` (task_id → descripción) y las paradas con ``stop_task``.
TAREAS = {}
PARADAS = []


def responder_control(datos, cuerpo=None, error=""):
	if error:
		escribir({"type": "control_response", "response": {"subtype": "error", "request_id": datos["request_id"], "error": error}})
	else:
		escribir({"type": "control_response", "response": {"subtype": "success", "request_id": datos["request_id"],
			"response": cuerpo or {}}})


def control_general(datos):
	"""``set_model``, ``set_permission_mode``, ``stop_task``, ``get_context_usage``, ``mcp_authenticate`` y
	``remote_control`` con los formatos de Claude Code 2.1.308. Los demás (``interrupt``) solo se anotan."""
	peticion = datos["request"]
	subtipo = peticion["subtype"]
	if subtipo == "set_model":
		if os.environ.get("FAKE_CLAUDE_SET_MODEL") == "error":
			responder_control(datos, error="Model not available")
		else:
			responder_control(datos, {"model": peticion.get("model")})
	elif subtipo == "set_permission_mode":
		modo = peticion.get("mode")
		arrancado = "--allow-dangerously-skip-permissions" in sys.argv or (
			"--permission-mode" in sys.argv and sys.argv[sys.argv.index("--permission-mode") + 1] == "bypassPermissions")
		if modo == "bypassPermissions" and not arrancado:
			responder_control(datos, error="Cannot set permission mode to bypassPermissions: session was not launched with "
				"--allow-dangerously-skip-permissions")
		elif modo not in ("default", "manual", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"):
			responder_control(datos, error="Cannot set permission mode: must be one of default, acceptEdits, plan")
		else:
			responder_control(datos, {"mode": modo})
	elif subtipo == "stop_task":
		# Como Claude Code: contesta que sí aunque no conozca la tarea.
		PARADAS.append(peticion.get("task_id"))
		responder_control(datos, {})
	elif subtipo == "get_context_usage":
		responder_control(datos, {"categories": [
			{"id": "system_prompt", "name": "System prompt", "tokens": 3000, "color": "a", "kind": "used"},
			{"id": "mcp_tools_deferred", "name": "MCP tools (deferred)", "tokens": 900, "color": "b", "isDeferred": True,
				"kind": "deferred"},
			{"id": "messages", "name": "Messages", "tokens": 12000, "color": "c", "kind": "used"},
			{"id": "autocompact_buffer", "name": "Autocompact buffer", "tokens": 33000, "color": "d", "kind": "buffer"},
			{"name": "Something new", "tokens": 5, "color": "e"},
		], "totalTokens": 48905, "maxTokens": 200000, "percentage": 24})
	elif subtipo == "mcp_authenticate":
		if peticion.get("serverName") == "entrar":
			responder_control(datos, {"authUrl": "https://auth.example.com/authorize?x=1", "requiresUserAction": True,
				"callbackExpected": True, "redirectScheme": "localhost", "callbackPort": 54321})
		elif peticion.get("serverName") == "bien":
			responder_control(datos, {"requiresUserAction": False, "callbackExpected": False})
		else:
			responder_control(datos, error="Server not found: %s" % peticion.get("serverName"))
	elif subtipo == "remote_control":
		if peticion.get("enabled"):
			puente = peticion.get("reattach_session_id") or "cse_puente1"
			responder_control(datos, {"session_url": "https://claude.ai/code/" + puente, "connect_url": "https://x",
				"environment_id": "env_1", "bridge_epoch": 1, "bridge_session_id": puente})
		else:
			responder_control(datos, {})


def eco(mensaje):
	"""Claude Code devuelve cada mensaje al recogerlo, con el uuid con el que llegó."""
	if mensaje and "--replay-user-messages" in sys.argv:
		escribir({"type": "user", "isReplay": True, "uuid": mensaje.get("uuid"), "session_id": "", "parent_tool_use_id": None,
			"message": mensaje.get("message")})


def deshacer(datos):
	"""Respuesta a «rewind_files» con los mismos formatos que Claude Code 2.1.238."""
	peticion = datos["request"]
	if os.environ.get("CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING") != "true":
		cuerpo = {"subtype": "success", "request_id": datos["request_id"],
			"response": {"canRewind": False, "error": "File rewinding is not enabled."}}
	elif peticion.get("user_message_id") not in PUNTOS and peticion.get("dry_run"):
		# Con «dry_run» el mismo error llega como respuesta correcta.
		cuerpo = {"subtype": "success", "request_id": datos["request_id"],
			"response": {"canRewind": False, "error": "No file checkpoint found for this message."}}
	elif peticion.get("user_message_id") not in PUNTOS:
		cuerpo = {"subtype": "error", "request_id": datos["request_id"], "error": "No file checkpoint found for this message."}
	elif peticion.get("dry_run"):
		cuerpo = {"subtype": "success", "request_id": datos["request_id"], "response": {"canRewind": True,
			"filesChanged": ["C:\\proyecto\\nota.txt", "C:\\proyecto\\otro.txt"], "insertions": 1, "deletions": 2}}
	else:
		cuerpo = {"subtype": "success", "request_id": datos["request_id"], "response": {"canRewind": True, "skippedLinks": 0}}
	escribir({"type": "control_response", "response": cuerpo})


def hablar_mcp(numero, metodo, params=None, notificacion=False, servidor="ctrlia"):
	"""Un mensaje a un servidor MCP «en proceso» de Ctrl+IA; devuelve su ``mcp_response``."""
	mensaje = {"jsonrpc": "2.0", "method": metodo}
	if params is not None:
		mensaje["params"] = params
	if not notificacion:
		mensaje["id"] = numero
	identificador = "mcp-%d" % numero
	escribir({"type": "control_request", "request_id": identificador,
		"request": {"subtype": "mcp_message", "server_name": servidor, "message": mensaje}})
	while True:
		datos = leer()
		if datos is None:
			return None
		if datos.get("type") == "control_response" and datos["response"].get("request_id") == identificador:
			return datos["response"]
		anotar({"otro": datos})


def subagente(numero):
	"""Un subagente en segundo plano (``tareas``) con sus pasos (``parent_tool_use_id``), como 2.1.308."""
	escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m-lanza-%d" % numero, "content": [
		{"type": "tool_use", "id": "t-agente", "name": "Agent", "input": {"description": "Revisar el código", "prompt": "Revisa"}}]}})
	# Un paso que llega antes que su task_started: se guarda hasta saber de qué tarea es.
	escribir({"type": "assistant", "parent_tool_use_id": "t-agente", "message": {"id": "s0", "content": [
		{"type": "text", "text": "Empiezo por el principio"}]}})
	escribir({"type": "system", "subtype": "task_started", "task_id": "agente-1", "tool_use_id": "t-agente",
		"description": "Revisar el código", "subagent_type": "general-purpose", "prompt": "Revisa", "is_backgrounded": True,
		"task_type": "local_agent", "uuid": "u", "session_id": "s"})
	TAREAS["agente-1"] = "Revisar el código"
	escribir({"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "agente-1",
		"task_type": "local_agent", "description": "Revisar el código"}], "uuid": "u", "session_id": "s"})
	escribir({"type": "assistant", "parent_tool_use_id": "t-agente", "message": {"id": "s1", "content": [
		{"type": "tool_use", "id": "sub-1", "name": "Read", "input": {"file_path": "C:\\proyecto\\app.py"}}]}})
	escribir({"type": "user", "parent_tool_use_id": "t-agente", "message": {"content": [
		{"type": "tool_result", "tool_use_id": "sub-1", "content": "x" * 1000}]}})
	escribir({"type": "assistant", "parent_tool_use_id": "t-agente", "message": {"id": "s2", "content": [
		{"type": "tool_use", "id": "sub-bash", "name": "Bash", "input": {"command": "rm -r build"}}]}})
	for vez in range(3):
		escribir({"type": "system", "subtype": "task_progress", "task_id": "agente-1", "tool_use_id": "t-agente",
			"description": "Revisar el código", "last_tool_name": "Read", "summary": "Leyendo app.py",
			"usage": {"total_tokens": 1000 + vez, "tool_uses": 1, "duration_ms": 500}, "uuid": "u", "session_id": "s"})
	escribir({"type": "user", "parent_tool_use_id": None, "message": {"content": [
		{"type": "tool_result", "tool_use_id": "t-agente", "content": "Async agent launched successfully."}]}})


def esperar_tareas():
	"""Tras el resultado, el subagente sigue ``FAKE_CLAUDE_FONDO`` segundos (o hasta un ``stop_task``) y acaba."""
	limite = time.monotonic() + float(os.environ.get("FAKE_CLAUDE_FONDO", "1.0"))
	parada = False
	while time.monotonic() < limite:
		datos = leer_con_espera(0.05)
		if datos is FALTA:
			if PARADAS:
				parada = True
				break
			continue
		if datos is None:
			return
		if datos.get("type") == "control_request":
			anotar({"control": datos["request"]})
			control_general(datos)
	escribir({"type": "system", "subtype": "task_updated", "task_id": "agente-1",
		"patch": {"status": "killed" if parada else "completed"}, "uuid": "u", "session_id": "s"})
	escribir({"type": "system", "subtype": "task_notification", "task_id": "agente-1", "tool_use_id": "t-agente",
		"status": "stopped" if parada else "completed", "output_file": "C:\\tmp\\agente-1.output",
		"summary": "Revisión terminada", "usage": {"total_tokens": 5000, "tool_uses": 4, "duration_ms": 9000},
		"uuid": "u", "session_id": "s"})
	TAREAS.clear()
	escribir({"type": "system", "subtype": "background_tasks_changed", "tasks": [], "uuid": "u", "session_id": "s"})


def sistema():
	"""Avisos en vivo de 2.1.308: compactación, modo cambiado, reintentos, otro modelo, memoria, límites y razonamiento."""
	escribir({"type": "system", "subtype": "status", "status": "compacting", "uuid": "u", "session_id": "s"})
	escribir({"type": "system", "subtype": "status", "status": "compacting", "compact_output_chars": 40, "uuid": "u",
		"session_id": "s"})
	escribir({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "auto", "pre_tokens": 150000,
		"post_tokens": 20000}, "uuid": "u", "session_id": "s"})
	escribir({"type": "system", "subtype": "status", "status": None, "compact_result": "failed",
		"compact_error": "Prompt too long", "uuid": "u", "session_id": "s"})
	escribir({"type": "system", "subtype": "status", "status": None, "permissionMode": "plan", "uuid": "u", "session_id": "s"})
	escribir({"type": "system", "subtype": "api_retry", "attempt": 2, "max_retries": 10, "retry_delay_ms": 4000,
		"error_status": 529, "error": "overloaded", "uuid": "u", "session_id": "s"})
	escribir({"type": "system", "subtype": "model_fallback", "trigger": "overloaded", "original_model": "claude-opus",
		"fallback_model": "claude-sonnet", "content": "Switched", "uuid": "u", "session_id": "s"})
	escribir({"type": "system", "subtype": "memory_saved", "written_paths": ["C:\\m\\a.md", "C:\\m\\b.md"], "verb": "Saved",
		"uuid": "u", "session_id": "s"})
	for _vez in range(2):
		escribir({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "isUsingOverage": True,
			"unifiedWindows": {"five_hour": {"utilization": 0.5, "resetsAt": 1700000000},
				"seven_day_overage_included": {"utilization": 1.2, "resetsAt": 1700000000}}}, "uuid": "u", "session_id": "s"})
	escribir({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "seven_day_opus",
		"resetsAt": 1700000000, "utilization": 1.0}, "uuid": "u", "session_id": "s"})
	escribir({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "thinking"}},
		"parent_tool_use_id": None})
	escribir({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta",
		"thinking": "Pienso"}}, "parent_tool_use_id": None})
	escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m-pensar", "content": [
		{"type": "thinking", "thinking": "Primero miro el archivo.", "signature": "abc"},
		{"type": "thinking", "thinking": "", "signature": "firmado"}]}})


def compactar(sesion):
	"""«/compact» como Claude Code 2.1.308: estado «compacting», el límite con sus cifras y un resultado sin texto."""
	estado("running")
	escribir({"type": "system", "subtype": "status", "status": "compacting", "uuid": "u", "session_id": sesion})
	time.sleep(float(os.environ.get("FAKE_CLAUDE_COMPACTAR", "0.5")))
	escribir({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "manual", "pre_tokens": 9000,
		"post_tokens": 1000}, "uuid": "u", "session_id": sesion})
	escribir({"type": "system", "subtype": "status", "status": None, "compact_result": "success", "uuid": "u", "session_id": sesion})
	escribir({"type": "result", "subtype": "success", "is_error": False, "result": "", "session_id": sesion, "usage": {}})
	estado("idle")


def aparte(mensaje):
	"""Una pregunta aparte (``--resume-session-at`` y ``--no-session-persistence``): responde y termina."""
	escribir({"type": "system", "subtype": "init", "session_id": "copia", "model": "claude-prueba", "permissionMode": "default",
		"apiKeySource": "none", "mcp_servers": []})
	contenido = str((mensaje.get("message") or {}).get("content") or "")
	escribir({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "apar"}},
		"parent_tool_use_id": None})
	escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "ap1", "content": [
		{"type": "text", "text": "aparte:" + contenido}]}})
	escribir({"type": "result", "subtype": "success", "is_error": False, "result": "aparte:" + contenido, "session_id": "copia",
		"usage": {}})


def turno(sesion, numero):
	"""Un turno completo. Devuelve False si el proceso debe terminar."""
	if numero == 1:
		escribir({"type": "system", "subtype": "init", "session_id": sesion, "model": "claude-prueba",
			"permissionMode": "default", "apiKeySource": "ANTHROPIC_API_KEY" if MODO == "clave_api" else "none",
			"mcp_servers": ([{"name": s["name"], "status": s["status"]} for s in SERVIDORES_MCP.values()]
				if MODO in ("mcp_estado", "mcp_recupera") else [{"name": "claude.ai Prueba", "status": "connected"}])})
	if MODO == "clave_api":
		time.sleep(5)
		return False
	estado("running")
	if MODO == "cuelga":
		while leer() is not None:
			pass
		return False
	texto = "Hola\u2028mundo" if numero == 1 else "Hola mundo %d" % numero
	if MODO == "mcp":
		respuestas = [
			hablar_mcp(100, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
				"clientInfo": {"name": "claude-code", "version": "2.1.238"}}),
			hablar_mcp(101, "notifications/initialized", notificacion=True),
			hablar_mcp(102, "tools/list"),
			hablar_mcp(103, "tools/call", {"name": "avisar", "arguments": {"texto": "Prueba hecha", "importante": True},
				"_meta": {"claudecode/toolUseId": "toolu_1"}}),
		]
		anotar({"mcp": respuestas})
		llamada = (respuestas[-1] or {}).get("response", {}).get("mcp_response", {}).get("result", {})
		texto = "mcp:" + "".join(b.get("text", "") for b in llamada.get("content") or [])
	if MODO == "mcp_sesiones":
		servidor = "ccd_session_mgmt"
		respuestas = [
			hablar_mcp(200, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
				"clientInfo": {"name": "claude-code", "version": "2.1.308"}}, servidor=servidor),
			hablar_mcp(201, "tools/list", servidor=servidor),
			hablar_mcp(202, "tools/list", servidor="no_existe"),
		]
		for indice, (nombre, argumentos) in enumerate(json.loads(os.environ.get("FAKE_CLAUDE_LLAMADAS", "[]"))):
			respuestas.append(hablar_mcp(210 + indice, "tools/call", {"name": nombre, "arguments": argumentos}, servidor=servidor))
		anotar({"mcp": respuestas})
	if MODO == "anadir":
		# Como Claude Code 2.1.238: lo que llega a mitad de turno lo atiende en el mismo turno.
		anotar({"esperando_anadido": True})
		otro = leer()
		anotar({"anadido": otro})
		eco(otro)
		texto = "recibido:" + str(((otro or {}).get("message") or {}).get("content") or "")
	if MODO in ("anadir_sin_eco", "anadir_muere"):
		anotar({"esperando_anadido": True})
		otro = leer()
		anotar({"anadido": otro})
		if MODO == "anadir_muere":
			sys.stdout.flush()
			os._exit(1)
		while leer() is not None:
			pass
		return False
	if MODO == "carpeta":
		fuera = os.environ.get("FAKE_CLAUDE_FUERA", "C:\\fuera")
		escribir({"type": "control_request", "request_id": "req-c%d" % numero, "request": {"subtype": "can_use_tool",
			"tool_name": "Bash", "input": {"command": "mkdir nueva"}, "tool_use_id": "t1",
			"permission_suggestions": [
				{"type": "addRules", "behavior": "allow", "destination": "localSettings",
					"rules": [{"toolName": "Bash", "ruleContent": "mkdir nueva"}]},
				{"type": "addDirectories", "directories": [fuera, "C:\\"], "destination": "session"},
				{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]}})
		respuesta = leer()
		anotar({"respuesta": respuesta})
		texto = "decision:" + respuesta["response"]["response"]["behavior"]
	if MODO == "permiso":
		escribir({"type": "control_request", "request_id": "req-%d" % numero, "request": {"subtype": "can_use_tool",
			"tool_name": "Bash", "input": {"command": "git push"}, "tool_use_id": "t1",
			"permission_suggestions": [{"type": "addRules", "behavior": "allow", "destination": "localSettings",
				"rules": [{"toolName": "Bash", "ruleContent": "git push"}]}]}})
		estado("requires_action")
		respuesta = leer()
		anotar({"respuesta": respuesta})
		texto = "decision:" + respuesta["response"]["response"]["behavior"]
		estado("running")
	elif MODO in ("mcp_paralelo", "mcp_interaccion"):
		herramienta = "mcp__claude_ai_Prueba__eco"
		peticiones = [{"type": "control_request", "request_id": "mcp-%s" % letra, "request": {"subtype": "can_use_tool",
			"tool_name": herramienta, "display_name": "Eco", "input": {"texto": letra}, "tool_use_id": "t-" + letra,
			"permission_suggestions": [{"type": "addRules", "behavior": "allow", "destination": "localSettings",
				"rules": [{"toolName": herramienta}]}]}} for letra in ("a", "b", "c")]
		if MODO == "mcp_interaccion":
			peticiones = peticiones[:1]
			peticiones[0]["request"]["requires_user_interaction"] = True
			peticiones[0]["request"].pop("permission_suggestions")
		for peticion in peticiones:
			escribir(peticion)
		if MODO == "mcp_paralelo":
			escribir(peticiones[0])  # la misma petición entregada otra vez
		estado("requires_action")
		respuestas = []
		while len(respuestas) < len(peticiones):
			respuesta = leer()
			if respuesta.get("type") == "control_response":
				respuestas.append(respuesta)
				anotar({"respuesta": respuesta})
		texto = "respuestas:%d" % len(respuestas)
		estado("running")
	elif MODO in ("pregunta", "borrador"):
		if MODO == "borrador":
			# Claude escribe un borrador y pregunta en la misma respuesta; el bloque de
			# texto completo no llega (solo los trozos), como en el issue #276.
			escribir({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m0"}}, "parent_tool_use_id": None})
			for trozo in ("Primer post: hola.", "\nSegundo post: adiós."):
				escribir({"type": "stream_event", "event": {"type": "content_block_delta",
					"delta": {"type": "text_delta", "text": trozo}}, "parent_tool_use_id": None})
			escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m0", "content": [
				{"type": "tool_use", "id": "t2", "name": "AskUserQuestion", "input": {}}]}})
		escribir({"type": "control_request", "request_id": "req-2", "request": {"subtype": "can_use_tool",
			"tool_name": "AskUserQuestion", "input": {"questions": [{"question": "¿Color?", "header": "Color",
				"options": [{"label": "Rojo", "description": ""}, {"label": "Azul", "description": ""}], "multiSelect": False}]},
			"tool_use_id": "t2"}})
		respuesta = leer()
		anotar({"respuesta": respuesta})
		texto = "respuesta:" + json.dumps(respuesta["response"]["response"].get("updatedInput", {}).get("answers"), ensure_ascii=False)
	if MODO in ("tareas", "fondo_muere", "permiso_subagente"):
		subagente(numero)
	if MODO == "permiso_subagente":
		escribir({"type": "control_request", "request_id": "req-sub", "request": {"subtype": "can_use_tool",
			"tool_name": "Bash", "input": {"command": "rm -r build"}, "tool_use_id": "sub-bash", "agent_id": "agente-1"}})
		estado("requires_action")
		respuesta = leer()
		anotar({"respuesta": respuesta})
		estado("running")
	if MODO == "sistema":
		sistema()
	if MODO == "orden_fondo":
		escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m-of", "content": [
			{"type": "tool_use", "id": "t-fondo", "name": "Bash", "input": {"command": "npm run servidor", "run_in_background": True}}]}})
		escribir({"type": "user", "parent_tool_use_id": None, "tool_use_result": {"backgroundTaskId": "bash-7", "stdout": ""},
			"message": {"content": [{"type": "tool_result", "tool_use_id": "t-fondo", "content":
				"Command running in background with ID: bash-7. Output is being written to: C:\\tmp\\tareas\\bash-7.output."}]}})
	escribir({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1-%d" % numero}}, "parent_tool_use_id": None})
	escribir({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hola"}},
		"parent_tool_use_id": None})
	escribir({"type": "assistant", "parent_tool_use_id": None, "uuid": "aaaaaaaa-0000-4000-8000-%012d" % numero,
		"message": {"id": "m1-%d" % numero, "content": [{"type": "text", "text": texto}],
		"usage": {"input_tokens": 10, "cache_read_input_tokens": 90}}})
	escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m2-%d" % numero, "content": [
		{"type": "tool_use", "id": "t9-%d" % numero, "name": "Read", "input": {"file_path": "C:/x.txt"}}]}})
	escribir({"type": "user", "parent_tool_use_id": None, "message": {"content": [
		{"type": "tool_result", "tool_use_id": "t9-%d" % numero, "content": "contenido"}]}})
	if MODO == "error_resultado":
		sys.stderr.write("Error de la API: api_key=sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 rechazada\n")
		sys.stderr.flush()
		time.sleep(0.2)
		escribir({"type": "result", "subtype": "error_max_turns", "is_error": True, "stop_reason": "max_turns",
			"errors": ["Reached maximum number of turns (3)"], "session_id": sesion, "usage": {}, "num_turns": 3})
		estado("idle")
		return True
	escribir({"type": "result", "subtype": "success", "is_error": False, "result": texto, "session_id": sesion,
		"usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01,
		"modelUsage": {"claude-prueba": {"contextWindow": 200000}}})
	if MODO == "fondo_muere":
		time.sleep(0.3)
		sys.stdout.flush()
		os._exit(1)
	if MODO in ("tareas", "permiso_subagente"):
		esperar_tareas()
	if MODO == "anadir_tarde":
		# Comprobado con 2.1.308: sin «idle» de por medio, lo escrito durante la respuesta final se recoge tras el resultado.
		anotar({"esperando_anadido": True})
		otro = leer()
		eco(otro)
		respuesta = "tarde:" + str(((otro or {}).get("message") or {}).get("content") or "")
		escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m3-%d" % numero, "content": [{"type": "text", "text": respuesta}]}})
		escribir({"type": "result", "subtype": "success", "is_error": False, "result": respuesta, "session_id": sesion,
			"usage": {"input_tokens": 10, "output_tokens": 5}})
	estado("idle")
	return True


def main():
	threading.Thread(target=_lector, daemon=True).start()
	sesion = "11111111-2222-3333-4444-555555555555"
	if "--session-id" in sys.argv:
		sesion = sys.argv[sys.argv.index("--session-id") + 1]
	if "--resume" in sys.argv and "--fork-session" not in sys.argv:
		sesion = sys.argv[sys.argv.index("--resume") + 1]
	anotar({"argv": sys.argv[1:], "claudecode": "CLAUDECODE" in os.environ,
		"clave": "ANTHROPIC_API_KEY" in os.environ, "estados": os.environ.get("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"),
		"mcp": os.environ.get("MCP_CONNECTION_NONBLOCKING"), "pid": os.getpid()})
	if MODO.startswith("sin_transcripcion") and "--resume" in sys.argv:
		aviso = "No conversation found with session ID: " + sys.argv[sys.argv.index("--resume") + 1]
		if MODO == "sin_transcripcion":
			escribir({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 0,
				"session_id": sesion, "total_cost_usd": 0, "usage": {}, "modelUsage": {}, "permission_denials": [],
				"errors": [aviso]})
		sys.stderr.write(aviso + "\n")
		sys.stderr.flush()
		sys.exit(1)
	numero = 0
	while True:
		mensaje = esperar_mensaje()
		if mensaje is None:
			return
		numero += 1
		anotar({"mensaje": mensaje, "turno": numero, "pid": os.getpid()})
		eco(mensaje)
		if "--resume-session-at" in sys.argv and "--no-session-persistence" in sys.argv:
			aparte(mensaje)
			return
		if (mensaje.get("message") or {}).get("content") == "/compact" and numero > 1:
			compactar(sesion)
			continue
		if not turno(sesion, numero):
			return


if __name__ == "__main__":
	main()
