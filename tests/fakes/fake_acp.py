"""Agente ACP falso para las pruebas de ``app/services/agentes/acp.py``.

Uso: ``python fake_acp.py ESCENARIO [argumentos...]``.

Escenarios:

- ``normal``: agente completo. Lo que hace en cada turno depende del texto
  del mensaje (``turno``, ``permiso``, ``leer RUTA``, ``escribir RUTA``,
  ``morir``, ``lento N``, ``esperar_cancelacion``, ``eco``).
- ``auth``: exige ``authenticate`` antes de ``session/new``.
- ``viejo``: muere si recibe ``--acp`` (solo entiende ``--experimental-acp``).
- ``sin_carga``: no anuncia ``loadSession``.

Comprueba que todos los mensajes del cliente llevan ``"jsonrpc": "2.0"`` y que
nunca llegan dos ``session/prompt`` a la vez; si algo falla, responde con un
error que la prueba verá como ``ERROR``.
"""

from __future__ import annotations

import itertools
import json
import queue
import sys
import threading
import time

ESCENARIO = sys.argv[1] if len(sys.argv) > 1 else "normal"
ARGUMENTOS = sys.argv[2:]

_candado = threading.Lock()
_ids = itertools.count(1000)
_respuestas: dict[int, queue.Queue] = {}
_cancelaciones: dict[str, threading.Event] = {}
_estado = {"autenticado": False, "en_turno": False, "modo": "default"}


def escribir(objeto: dict) -> None:
	objeto = {"jsonrpc": "2.0", **objeto}
	with _candado:
		sys.stdout.write(json.dumps(objeto, ensure_ascii=False) + "\n")
		sys.stdout.flush()


def responder(ident, resultado) -> None:
	escribir({"id": ident, "result": resultado})


def error(ident, codigo: int, mensaje: str) -> None:
	escribir({"id": ident, "error": {"code": codigo, "message": mensaje}})


def actualizar(sesion: str, datos: dict) -> None:
	escribir({"method": "session/update", "params": {"sessionId": sesion, "update": datos}})


def texto(sesion: str, contenido: str) -> None:
	actualizar(sesion, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": contenido}})


def pedir(metodo: str, parametros: dict) -> dict:
	"""Petición al cliente; espera su respuesta (``result`` o ``error``)."""
	ident = next(_ids)
	cola: queue.Queue = queue.Queue()
	with _candado:
		_respuestas[ident] = cola
	escribir({"id": ident, "method": metodo, "params": parametros})
	return cola.get(timeout=30)


def sesion_nueva_resultado(sesion: str) -> dict:
	return {
		"sessionId": sesion,
		"modes": {
			"currentModeId": _estado["modo"],
			"availableModes": [
				{"id": "default", "name": "Preguntar siempre", "description": "Pide permiso"},
				{"id": "plan", "name": "Planificar", "description": "Solo planifica"},
			],
		},
		"models": {
			"currentModelId": "rapido",
			"availableModels": [{"modelId": "rapido", "name": "Rápido"}, {"modelId": "listo", "name": "Listo"}],
		},
	}


def turno(ident, parametros: dict) -> None:
	sesion = parametros["sessionId"]
	bloques = parametros.get("prompt") or []
	mensaje = "".join(b.get("text", "") for b in bloques if b.get("type") == "text")
	motivo = "end_turn"
	try:
		if mensaje == "turno":
			texto(sesion, "Hola, ")
			actualizar(sesion, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "pensando"}})
			texto(sesion, "voy a editar.")
			actualizar(sesion, {
				"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Editar config.txt", "kind": "edit",
				"status": "pending", "locations": [{"path": "/proyecto/config.txt", "line": 3}],
			})
			actualizar(sesion, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress"})
			actualizar(sesion, {
				"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
				"content": [
					{"type": "diff", "path": "/proyecto/config.txt", "oldText": "a\nviejo\n", "newText": "a\nnuevo\n"},
					{"type": "content", "content": {"type": "text", "text": "Hecho"}},
				],
			})
			actualizar(sesion, {"sessionUpdate": "plan", "entries": [
				{"content": "Leer", "priority": "high", "status": "completed"},
				{"content": "Editar", "priority": "medium", "status": "in_progress"},
			]})
			actualizar(sesion, {"sessionUpdate": "available_commands_update", "availableCommands": [
				{"name": "web", "description": "Busca en internet"},
			]})
			actualizar(sesion, {"sessionUpdate": "usage_update", "used": 500, "size": 1000, "cost": {"amount": 0.5, "currency": "USD"}})
			texto(sesion, "Listo.")
		elif mensaje == "permiso":
			actualizar(sesion, {"sessionUpdate": "tool_call", "toolCallId": "p1", "title": "Ejecutar ls", "kind": "execute", "status": "pending"})
			respuesta = pedir("session/request_permission", {
				"sessionId": sesion,
				"toolCall": {"toolCallId": "p1"},
				"options": [
					{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
					{"optionId": "always", "name": "Always allow `ls` commands", "kind": "allow_always"},
					{"optionId": "reject", "name": "Reject", "kind": "reject_once"},
				],
			})
			resultado = (respuesta.get("result") or {}).get("outcome") or {}
			if resultado.get("outcome") == "selected":
				texto(sesion, "elegido: %s" % resultado.get("optionId"))
			else:
				texto(sesion, "elegido: %s" % resultado.get("outcome"))
				motivo = "cancelled"
		elif mensaje.startswith("leer "):
			partes = mensaje.split(" ")
			parametros_leer = {"sessionId": sesion, "path": partes[1]}
			if len(partes) > 2:
				parametros_leer["line"] = int(partes[2])
				parametros_leer["limit"] = int(partes[3])
			respuesta = pedir("fs/read_text_file", parametros_leer)
			if "error" in respuesta:
				texto(sesion, "error %s" % respuesta["error"]["code"])
			else:
				texto(sesion, "contenido: %s" % respuesta["result"]["content"])
		elif mensaje.startswith("escribir "):
			ruta = mensaje.split(" ", 1)[1]
			respuesta = pedir("fs/write_text_file", {"sessionId": sesion, "path": ruta, "content": "línea 1\nlínea 2\n"})
			if "error" in respuesta:
				texto(sesion, "error %s: %s" % (respuesta["error"]["code"], respuesta["error"]["message"]))
			else:
				texto(sesion, "escrito")
		elif mensaje == "terminal":
			respuesta = pedir("terminal/create", {"sessionId": sesion, "command": "ls"})
			texto(sesion, "error %s" % respuesta.get("error", {}).get("code"))
		elif mensaje == "morir":
			sys.stderr.write("fallo catastrófico del agente\n")
			sys.stderr.flush()
			time.sleep(0.2)
			sys.stdout.flush()
			import os

			os._exit(3)
		elif mensaje.startswith("lento"):
			time.sleep(0.4)
			texto(sesion, "respuesta %s" % mensaje)
		elif mensaje == "esperar_cancelacion":
			texto(sesion, "trabajando")
			evento = _cancelaciones.setdefault(sesion, threading.Event())
			if evento.wait(20):
				motivo = "cancelled"
		elif mensaje == "eco_adjuntos":
			tipos = ",".join(b.get("type", "") for b in bloques)
			texto(sesion, "tipos: %s" % tipos)
		else:
			texto(sesion, "eco: %s" % mensaje)
	finally:
		with _candado:
			_estado["en_turno"] = False
			_cancelaciones.pop(sesion, None)
	responder(ident, {"stopReason": motivo})


def atender(mensaje: dict) -> None:
	if mensaje.get("jsonrpc") != "2.0":
		sys.stderr.write("falta jsonrpc 2.0\n")
		if "id" in mensaje and "method" in mensaje:
			error(mensaje["id"], -32600, "falta jsonrpc")
		return
	metodo = mensaje.get("method")
	ident = mensaje.get("id")
	parametros = mensaje.get("params") or {}
	if metodo is None:
		with _candado:
			cola = _respuestas.pop(ident, None)
		if cola is not None:
			cola.put(mensaje)
		return
	if metodo == "initialize":
		capacidades = parametros.get("clientCapabilities") or {}
		if parametros.get("protocolVersion") != 1 or not capacidades.get("fs", {}).get("writeTextFile") or capacidades.get("terminal"):
			error(ident, -32602, "initialize incorrecto: %s" % json.dumps(parametros))
			return
		if (parametros.get("clientInfo") or {}).get("name") != "ctrl-ia":
			error(ident, -32602, "clientInfo incorrecto")
			return
		metodos = [{"id": "clave", "name": "Clave de API", "description": "Usa una clave"}] if ESCENARIO == "auth" else []
		responder(ident, {
			"protocolVersion": 1,
			"agentCapabilities": {
				"loadSession": ESCENARIO != "sin_carga",
				"promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
			},
			"authMethods": metodos,
		})
	elif metodo == "authenticate":
		_estado["autenticado"] = parametros.get("methodId") == "clave"
		responder(ident, {})
	elif metodo == "session/new":
		if ESCENARIO == "auth" and not _estado["autenticado"]:
			error(ident, -32000, "Authentication required")
			return
		if not parametros.get("cwd") or parametros.get("mcpServers") != []:
			error(ident, -32602, "session/new incorrecto")
			return
		responder(ident, sesion_nueva_resultado("ses-1"))
	elif metodo == "session/load":
		sesion = parametros["sessionId"]
		actualizar(sesion, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "hola "}})
		actualizar(sesion, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "agente"}})
		actualizar(sesion, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hola humano"}})
		responder(ident, {k: v for k, v in sesion_nueva_resultado(sesion).items() if k != "sessionId"})
	elif metodo == "session/set_mode":
		_estado["modo"] = parametros.get("modeId")
		actualizar(parametros["sessionId"], {"sessionUpdate": "current_mode_update", "currentModeId": parametros.get("modeId")})
		responder(ident, {})
	elif metodo == "session/set_model":
		responder(ident, {})
	elif metodo == "session/cancel":
		with _candado:
			evento = _cancelaciones.setdefault(parametros.get("sessionId", ""), threading.Event())
		evento.set()
	elif metodo == "session/prompt":
		with _candado:
			if _estado["en_turno"]:
				error(ident, -32603, "dos turnos a la vez")
				return
			_estado["en_turno"] = True
			_cancelaciones.pop(parametros.get("sessionId", ""), None)
		threading.Thread(target=turno, args=(ident, parametros), daemon=True).start()
	elif ident is not None:
		error(ident, -32601, "método desconocido")


def main() -> None:
	sys.stdout.reconfigure(encoding="utf-8", newline="\n")
	sys.stderr.reconfigure(encoding="utf-8")
	if ESCENARIO == "viejo" and "--acp" in ARGUMENTOS:
		sys.stderr.write("Unknown argument: acp\n")
		sys.stderr.flush()
		sys.exit(1)
	entrada = sys.stdin.buffer
	for linea in iter(entrada.readline, b""):
		linea = linea.strip()
		if not linea:
			continue
		try:
			mensaje = json.loads(linea.decode("utf-8"))
		except ValueError:
			sys.stderr.write("línea no JSON\n")
			continue
		atender(mensaje)


if __name__ == "__main__":
	main()
