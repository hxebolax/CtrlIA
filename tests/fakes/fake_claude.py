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
- ``mcp``: habla con el servidor MCP «en proceso» de Ctrl+IA (``sdkMcpServers``) con
  ``control_request`` ``mcp_message`` (initialize, notificación, tools/list y tools/call
  ``avisar``) y responde con lo que contestó la herramienta.

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
"""

import json
import os
import sys
import time

MODO = os.environ.get("FAKE_CLAUDE_MODO", "normal")
#: uuid de los mensajes con «punto» de archivos (los recibidos y los de FAKE_CLAUDE_PUNTOS).
PUNTOS = {u for u in os.environ.get("FAKE_CLAUDE_PUNTOS", "").split(",") if u}


def escribir(objeto):
	sys.stdout.write(json.dumps(objeto, ensure_ascii=False) + "\n")
	sys.stdout.flush()


def leer():
	linea = sys.stdin.readline()
	if not linea:
		return None
	return json.loads(linea)


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
			if MODO == "mcp":
				anotar({"inicio": datos["request"]})
			escribir({"type": "control_response", "response": {"subtype": "success", "request_id": datos["request_id"],
				"response": {"commands": [{"name": "compact", "description": "Compacta", "argumentHint": ""}], "models": []}}})
			continue
		if datos.get("type") == "control_request" and datos["request"]["subtype"] == "rewind_files":
			anotar({"control": datos["request"]})
			deshacer(datos)
			continue
		if datos.get("type") == "control_request":
			anotar({"control": datos["request"]})
			continue
		if datos.get("type") == "user":
			if datos.get("uuid"):
				PUNTOS.add(datos["uuid"])
			return datos


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


def hablar_mcp(numero, metodo, params=None, notificacion=False):
	"""Un mensaje al servidor MCP «en proceso» de Ctrl+IA; devuelve su ``mcp_response``."""
	mensaje = {"jsonrpc": "2.0", "method": metodo}
	if params is not None:
		mensaje["params"] = params
	if not notificacion:
		mensaje["id"] = numero
	identificador = "mcp-%d" % numero
	escribir({"type": "control_request", "request_id": identificador,
		"request": {"subtype": "mcp_message", "server_name": "ctrlia", "message": mensaje}})
	while True:
		datos = leer()
		if datos is None:
			return None
		if datos.get("type") == "control_response" and datos["response"].get("request_id") == identificador:
			return datos["response"]
		anotar({"otro": datos})


def turno(sesion, numero):
	"""Un turno completo. Devuelve False si el proceso debe terminar."""
	if numero == 1:
		escribir({"type": "system", "subtype": "init", "session_id": sesion, "model": "claude-prueba",
			"permissionMode": "default", "apiKeySource": "ANTHROPIC_API_KEY" if MODO == "clave_api" else "none",
			"mcp_servers": [{"name": "claude.ai Prueba", "status": "connected"}]})
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
	if MODO == "anadir":
		# Como Claude Code 2.1.238: lo que llega a mitad de turno lo atiende en el mismo turno.
		anotar({"esperando_anadido": True})
		otro = leer()
		anotar({"anadido": otro})
		texto = "recibido:" + str(((otro or {}).get("message") or {}).get("content") or "")
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
	escribir({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1-%d" % numero}}, "parent_tool_use_id": None})
	escribir({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hola"}},
		"parent_tool_use_id": None})
	escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m1-%d" % numero, "content": [{"type": "text", "text": texto}],
		"usage": {"input_tokens": 10, "cache_read_input_tokens": 90}}})
	escribir({"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m2-%d" % numero, "content": [
		{"type": "tool_use", "id": "t9-%d" % numero, "name": "Read", "input": {"file_path": "C:/x.txt"}}]}})
	escribir({"type": "user", "parent_tool_use_id": None, "message": {"content": [
		{"type": "tool_result", "tool_use_id": "t9-%d" % numero, "content": "contenido"}]}})
	escribir({"type": "result", "subtype": "success", "is_error": False, "result": texto, "session_id": sesion,
		"usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01,
		"modelUsage": {"claude-prueba": {"contextWindow": 200000}}})
	estado("idle")
	return True


def main():
	sesion = "11111111-2222-3333-4444-555555555555"
	if "--session-id" in sys.argv:
		sesion = sys.argv[sys.argv.index("--session-id") + 1]
	if "--resume" in sys.argv and "--fork-session" not in sys.argv:
		sesion = sys.argv[sys.argv.index("--resume") + 1]
	anotar({"argv": sys.argv[1:], "claudecode": "CLAUDECODE" in os.environ,
		"clave": "ANTHROPIC_API_KEY" in os.environ, "estados": os.environ.get("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"),
		"mcp": os.environ.get("MCP_CONNECTION_NONBLOCKING"), "pid": os.getpid()})
	numero = 0
	while True:
		mensaje = esperar_mensaje()
		if mensaje is None:
			return
		numero += 1
		anotar({"mensaje": mensaje, "turno": numero, "pid": os.getpid()})
		if not turno(sesion, numero):
			return


if __name__ == "__main__":
	main()
