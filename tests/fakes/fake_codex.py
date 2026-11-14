"""Servidor falso de ``codex app-server`` para las pruebas (sin red ni Codex real).

Uso: ``python fake_codex.py ESCENARIO [REGISTRO]``.

- ``ESCENARIO`` decide cómo se comporta ``turn/start`` (ver ``_turno``).
- ``REGISTRO`` (opcional): archivo JSONL donde se apunta cada mensaje
  recibido, para que la prueba compruebe lo que mandó el cliente.
- Con ``--version`` imprime una versión y termina.

Habla el mismo protocolo que el real: JSON por líneas, sin ``jsonrpc``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ESCENARIO = sys.argv[1] if len(sys.argv) > 1 else "normal"
REGISTRO = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] != "--version" else None
SOSPECHOSAS = ("OPENAI_API_KEY", "CODEX_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_API_KEY", "OPENAI_BASE_URL", "CODEX_AUTH_TOKEN")

estado = {"turno": 0, "hilo": "hilo-1", "turno_actual": None, "esperando": None, "retenida": None, "id_srv": 900,
	# Subagentes: hilo hijo → hilo padre (lo que dice thread/read en parentThreadId).
	"padres": {"hilo-hijo": "hilo-intermedio", "hilo-intermedio": "hilo-1", "hilo-huerfano": "hilo-desconocido"}}
#: Cómo empieza el mensaje fijo de Ctrl+IA para los mensajes largos (app/services/mensajes_largos.py).
APERTURA_LARGO = "Este mensaje lo ha puesto Ctrl+IA en la conversación."


def escribir(objeto: dict) -> None:
	sys.stdout.write(json.dumps(objeto, ensure_ascii=False) + "\n")
	sys.stdout.flush()


def notificar(metodo: str, params: dict) -> None:
	escribir({"method": metodo, "params": params})


def responder(identificador, resultado) -> None:
	escribir({"id": identificador, "result": resultado})


def hilo(identificador: str) -> dict:
	return {
		"id": identificador, "preview": "Hola", "name": None, "cwd": "C:\\proyecto", "createdAt": 100, "updatedAt": 200,
		"source": "appServer", "status": {"type": "idle"}, "turns": [], "cliVersion": "0", "ephemeral": False,
		"modelProvider": "openai", "sessionId": "s", "projectId": None,
	}


def apuntar(mensaje: dict) -> None:
	if REGISTRO is not None:
		with REGISTRO.open("a", encoding="utf-8") as archivo:
			archivo.write(json.dumps(mensaje, ensure_ascii=False) + "\n")


def texto_de(params: dict) -> str:
	return " ".join(parte.get("text", "") for parte in params.get("input", []) if parte.get("type") == "text")


def mensaje_agente(turno: str, item: str, texto: str, trozos: list[str] | None = None) -> None:
	h = estado["hilo"]
	notificar("item/started", {"threadId": h, "turnId": turno, "startedAtMs": 1, "item": {"type": "agentMessage", "id": item, "text": ""}})
	for trozo in trozos or [texto]:
		notificar("item/agentMessage/delta", {"threadId": h, "turnId": turno, "itemId": item, "delta": trozo})
	notificar("item/completed", {"threadId": h, "turnId": turno, "completedAtMs": 2, "item": {"type": "agentMessage", "id": item, "text": texto}})


def completar(turno: str, status: str = "completed") -> None:
	estado["turno_actual"] = None
	notificar("turn/completed", {"threadId": estado["hilo"], "turn": {"id": turno, "items": [], "status": status}})


def peticion_servidor(metodo: str, params: dict, al_responder) -> None:
	estado["id_srv"] += 1
	identificador = estado["id_srv"]
	estado["esperando"] = (identificador, al_responder)
	escribir({"id": identificador, "method": metodo, "params": params})


def _leer_largo(texto: str) -> str:
	"""Lo que haría Codex con un mensaje largo: leer el archivo que nombra (y decir cuánto ocupa)."""
	for linea in texto.splitlines():
		if linea.startswith("ruta: "):
			ruta = Path(linea[6:].strip())
			contenido = ruta.read_text(encoding="utf-8-sig")
			return "Leído: %d caracteres, bom=%s" % (len(contenido), ruta.read_bytes().startswith(b"\xef\xbb\xbf"))
	return "Sin ruta"


def _turno(identificador, params: dict) -> None:
	estado["turno"] += 1
	turno = "turno-%d" % estado["turno"]
	estado["turno_actual"] = turno
	h = estado["hilo"]
	texto = texto_de(params)
	responder(identificador, {"turn": {"id": turno, "items": [], "status": "inProgress"}})
	notificar("turn/started", {"threadId": h, "turn": {"id": turno, "items": [], "status": "inProgress"}})

	if texto.startswith(APERTURA_LARGO):
		mensaje_agente(turno, "msg-largo", _leer_largo(texto))
		completar(turno)
		return

	if h == "hilo-aparte":
		mensaje_agente(turno, "msg-aparte", "Respuesta aparte a: " + texto, ["Respuesta aparte", " a: " + texto])
		completar(turno)
		return

	if ESCENARIO in ("subagente", "subagente_huerfano", "subagente_retirado"):
		hijo = "hilo-huerfano" if ESCENARIO == "subagente_huerfano" else "hilo-hijo"
		# Antes, el subagente cambia un archivo (su diff debe salir en la petición).
		notificar("item/started", {"threadId": hijo, "turnId": "turno-hijo", "item": {"type": "fileChange", "id": "fc-hijo",
			"status": "inProgress", "changes": [{"path": "hijo.txt", "kind": {"type": "add"}, "diff": "+del hijo"}]}})

		def tras_hijo(resultado: dict) -> None:
			notificar("serverRequest/resolved", {"threadId": hijo, "requestId": estado["id_srv"]})
			respuesta = "error" if "_error" in resultado else resultado.get("decision")
			mensaje_agente(turno, "msg-sub", "Subagente: %s" % respuesta)
			completar(turno)

		peticion_servidor("item/fileChange/requestApproval", {"threadId": hijo, "turnId": "turno-hijo",
			"itemId": "fc-hijo", "reason": "El subagente quiere escribir", "startedAtMs": 1}, tras_hijo)
		if ESCENARIO == "subagente_retirado":
			# El subagente termina su turno antes de que nadie conteste: la petición ya no vale.
			time.sleep(0.5)
			notificar("turn/completed", {"threadId": hijo, "turn": {"id": "turno-hijo", "items": [], "status": "completed"}})
			estado["esperando"] = None
			mensaje_agente(turno, "msg-sub", "Retirada")
			completar(turno)
		return

	if ESCENARIO == "actividad":
		notificar("item/started", {"threadId": h, "turnId": turno, "item": {"type": "commandExecution", "id": "orden-viva",
			"command": "npm test", "cwd": "C:\\proyecto", "commandActions": [], "status": "inProgress"}})
		for trozo in ("uno\n", "dos\n", "tres\n"):
			notificar("item/commandExecution/outputDelta", {"threadId": h, "turnId": turno, "itemId": "orden-viva", "delta": trozo})
		notificar("item/completed", {"threadId": h, "turnId": turno, "item": {"type": "commandExecution", "id": "orden-viva",
			"command": "npm test", "cwd": "C:\\proyecto", "commandActions": [], "status": "completed", "exitCode": 0,
			"aggregatedOutput": "uno\ndos\ntres\n"}})
		subagente = {"type": "collabAgentToolCall", "id": "collab-1", "tool": "spawnAgent", "senderThreadId": h,
			"receiverThreadIds": ["hilo-sub"], "prompt": "Revisa las pruebas", "model": "gpt-mini", "status": "inProgress",
			"agentsStates": {"hilo-sub": {"status": "running", "message": None}}}
		notificar("item/started", {"threadId": h, "turnId": turno, "item": subagente})
		notificar("item/completed", {"threadId": h, "turnId": turno, "item": dict(subagente, status="completed",
			agentsStates={"hilo-sub": {"status": "completed", "message": "Todo en orden"}})})
		notificar("item/completed", {"threadId": h, "turnId": turno, "item": {"type": "subAgentActivity", "id": "act-1",
			"kind": "completed", "agentThreadId": "hilo-otro", "agentPath": "/root/revisor"}})
		mensaje_agente(turno, "msg-act", "Hecho")
		completar(turno)
		return

	if ESCENARIO == "muere" and REGISTRO is not None and not REGISTRO.with_suffix(".muerto").exists():
		REGISTRO.with_suffix(".muerto").write_text("x")
		sys.stderr.write("panic: fallo simulado\n")
		sys.stderr.flush()
		os._exit(3)

	if ESCENARIO == "lento":
		time.sleep(0.4)

	if ESCENARIO in ("aprobacion", "aprobacion_detener"):
		notificar("item/started", {"threadId": h, "turnId": turno, "startedAtMs": 1, "item": {
			"type": "commandExecution", "id": "orden-1", "command": "dir", "cwd": "C:\\proyecto", "commandActions": [], "status": "inProgress"}})

		def tras_aprobar(resultado: dict) -> None:
			decision = resultado.get("decision")
			notificar("serverRequest/resolved", {"threadId": h, "requestId": estado["id_srv"]})
			estado_orden = "completed" if decision in ("accept", "acceptForSession") else "declined"
			notificar("item/completed", {"threadId": h, "turnId": turno, "completedAtMs": 2, "item": {
				"type": "commandExecution", "id": "orden-1", "command": "dir", "cwd": "C:\\proyecto", "commandActions": [],
				"status": estado_orden, "exitCode": 0 if estado_orden == "completed" else None,
				"aggregatedOutput": "x" * 5000 if estado_orden == "completed" else None}})
			mensaje_agente(turno, "msg-ap", "Decisión: %s" % decision)
			completar(turno)

		peticion_servidor("item/commandExecution/requestApproval", {
			"threadId": h, "turnId": turno, "itemId": "orden-1", "command": "dir", "cwd": "C:\\proyecto",
			"reason": "Necesito ver los archivos", "startedAtMs": 1}, tras_aprobar)
		return

	if ESCENARIO == "regla":
		# La orden es el texto del mensaje; Codex propone recordar sus dos primeras palabras.
		def tras_decidir(resultado: dict) -> None:
			mensaje_agente(turno, "msg-r%d" % estado["turno"], "Decisión: %s" % resultado.get("decision"))
			completar(turno)

		peticion_servidor("item/commandExecution/requestApproval", {
			"threadId": h, "turnId": turno, "itemId": "orden-%d" % estado["turno"], "command": texto,
			"cwd": estado.get("cwd") or "C:\\proyecto", "startedAtMs": 1,
			"proposedExecpolicyAmendment": texto.split()[:2]}, tras_decidir)
		return

	if ESCENARIO == "pregunta":
		def tras_pregunta(resultado: dict) -> None:
			mensaje_agente(turno, "msg-p", "Respuestas: " + json.dumps(resultado, ensure_ascii=False, sort_keys=True))
			completar(turno)

		peticion_servidor("item/tool/requestUserInput", {
			"threadId": h, "turnId": turno, "itemId": "preg-1", "isBlocking": True,
			"questions": [{"id": "color", "header": "Color", "question": "¿Qué color prefieres?", "isOther": True,
				"options": [{"label": "Rojo", "description": "Cálido"}, {"label": "Azul", "description": "Frío"}]}]}, tras_pregunta)
		return

	if ESCENARIO in ("interrupcion", "steer_muere", "steer_interno"):
		return  # el turno sigue «trabajando» hasta turn/interrupt

	if ESCENARIO == "revision":
		# La revisión automática decide por sí misma: solo llegan notificaciones, ninguna petición.
		orden = {"type": "command", "command": "dir", "cwd": "C:\\proyecto", "source": "shell"}
		for numero, (accion, revision) in enumerate((
			(orden, {"status": "approved", "riskLevel": "low", "rationale": "Parece inofensiva"}),
			({"type": "networkAccess", "host": "ejemplo.com", "port": 443, "protocol": "https", "target": "x"},
				{"status": "denied", "riskLevel": "high"}),
			(orden, {"status": "timedOut"}),
			(orden, {"status": "aborted"}),
		)):
			comunes = {"threadId": h, "turnId": turno, "reviewId": "rev-%d" % numero, "action": accion, "startedAtMs": 1}
			notificar("item/autoApprovalReview/started", dict(comunes, review={"status": "inProgress"}))
			notificar("item/autoApprovalReview/completed", dict(comunes, review=revision, completedAtMs=2, decisionSource="agent"))

	if ESCENARIO == "fallo":
		notificar("error", {"threadId": h, "turnId": turno, "willRetry": False,
			"error": {"message": "x" * 2000, "codexErrorInfo": "usageLimitExceeded"}})
		notificar("turn/completed", {"threadId": h, "turn": {"id": turno, "items": [], "status": "failed",
			"error": {"message": "límite", "codexErrorInfo": "usageLimitExceeded"}}})
		estado["turno_actual"] = None
		return

	# normal / lento / muere (segunda vez) / entorno
	notificar("turn/plan/updated", {"threadId": h, "turnId": turno, "plan": [
		{"step": "Leer", "status": "completed"}, {"step": "Responder", "status": "inProgress"}]})
	notificar("item/completed", {"threadId": h, "turnId": turno, "completedAtMs": 2, "item": {
		"type": "reasoning", "id": "raz-1", "summary": ["Pienso en ello"], "content": []}})
	notificar("item/completed", {"threadId": h, "turnId": turno, "completedAtMs": 2, "item": {
		"type": "fileChange", "id": "fc-1", "status": "completed", "changes": [
			{"path": "a.txt", "kind": {"type": "add"}, "diff": "+hola"},
			{"path": "b.txt", "kind": {"type": "update", "move_path": None}, "diff": "-x\n+y"}]}})
	notificar("item/commandExecution/outputDelta", {"threadId": h, "turnId": turno, "itemId": "x", "delta": "ruido"})
	notificar("thread/tokenUsage/updated", {"threadId": h, "turnId": turno, "tokenUsage": {
		"total": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120, "cachedInputTokens": 0, "reasoningOutputTokens": 0},
		"last": {"inputTokens": 50, "outputTokens": 10, "totalTokens": 60, "cachedInputTokens": 0, "reasoningOutputTokens": 0},
		"modelContextWindow": 1000}})
	mensaje_agente(turno, "msg-%d" % estado["turno"], "Eco: " + texto, ["Eco", ": ", texto])
	completar(turno)


def atender(mensaje: dict) -> None:
	metodo = mensaje.get("method")
	identificador = mensaje.get("id")
	params = mensaje.get("params") or {}
	if metodo is None:
		# Respuesta del cliente a una petición nuestra (un error llega como {"_error": ...}).
		esperando = estado["esperando"]
		if esperando and esperando[0] == identificador:
			estado["esperando"] = None
			esperando[1]({"_error": mensaje["error"]} if "error" in mensaje else (mensaje.get("result") or {}))
		return
	if identificador is None:
		return  # notificación (initialized)

	if ESCENARIO == "desordenado" and metodo not in ("initialize",):
		if estado["retenida"] is None:
			estado["retenida"] = mensaje
			return
		retenida, estado["retenida"] = estado["retenida"], None
		atender_peticion(metodo, identificador, params)
		atender_peticion(retenida["method"], retenida["id"], retenida.get("params") or {})
		return
	atender_peticion(metodo, identificador, params)


def atender_peticion(metodo: str, identificador, params: dict) -> None:
	if metodo == "initialize":
		responder(identificador, {"userAgent": "fake/0", "codexHome": "C:\\codex", "platformFamily": "windows", "platformOs": "windows"})
	elif metodo == "thread/start":
		estado["hilo"] = "hilo-1"
		estado["cwd"] = params.get("cwd")
		# Como el real: devuelve el revisor con el que se queda el hilo.
		revisor = "user" if ESCENARIO == "revisor_ignorado" else params.get("approvalsReviewer") or "user"
		responder(identificador, {"thread": hilo("hilo-1"), "model": "gpt-falso", "modelProvider": "openai", "cwd": params.get("cwd"),
			"approvalPolicy": params.get("approvalPolicy"), "approvalsReviewer": revisor, "sandbox": {"type": "workspaceWrite"}})
	elif metodo == "thread/resume" and ESCENARIO == "desaparecido":
		escribir({"id": identificador, "error": {"code": -32600, "message": "no rollout found for thread id"}})
	elif metodo == "thread/resume":
		estado["hilo"] = params["threadId"]
		respuesta = {"thread": hilo(params["threadId"]), "model": "gpt-falso"}
		if ESCENARIO == "revisor_pegado":
			# Un hilo ya cargado ignora lo que se le pida al retomarlo (AIChat, codex-auto-review.md).
			respuesta["approvalsReviewer"] = "auto_review"
		responder(identificador, respuesta)
	elif metodo == "thread/fork":
		if params.get("ephemeral"):
			# «Preguntar aparte»: una copia efímera.
			estado["hilo"] = "hilo-aparte"
			responder(identificador, {"thread": dict(hilo("hilo-aparte"), ephemeral=True), "model": "gpt-falso"})
			return
		estado["hilo"] = "hilo-bifurcado"
		responder(identificador, {"thread": hilo("hilo-bifurcado"), "model": "gpt-falso"})
	elif metodo == "thread/turns/list":
		turnos = [{"id": "turno-vivo", "status": "inProgress", "items": [], "itemsView": "notLoaded"}] if ESCENARIO == "aparte_vivo" else []
		turnos += [{"id": "turno-2", "status": "completed", "items": [], "itemsView": "notLoaded"},
			{"id": "turno-1", "status": "completed", "items": [], "itemsView": "notLoaded"}]
		if ESCENARIO == "sin_turnos":
			turnos = [{"id": "turno-vivo", "status": "inProgress", "items": [], "itemsView": "notLoaded"}]
		responder(identificador, {"data": turnos, "nextCursor": None})
	elif metodo == "thread/compact/start":
		responder(identificador, {})
		turno = "turno-compactar"
		h = estado["hilo"]
		notificar("turn/started", {"threadId": h, "turn": {"id": turno, "items": [], "status": "inProgress"}})
		notificar("item/completed", {"threadId": h, "turnId": turno, "item": {"type": "contextCompaction", "id": "comp-1"}})
		notificar("thread/compacted", {"threadId": h, "turnId": turno})
		notificar("turn/completed", {"threadId": h, "turn": {"id": turno, "items": [], "status": "completed"}})
	elif metodo == "collaborationMode/list":
		modos = [{"name": "Default", "mode": "default"}]
		if ESCENARIO != "sin_plan":
			modos.append({"name": "Plan", "mode": "plan"})
		responder(identificador, {"data": modos})
	elif metodo in ("thread/memoryMode/set", "thread/goal/set", "thread/goal/clear", "config/mcpServer/reload"):
		responder(identificador, {})
	elif metodo == "thread/goal/get":
		responder(identificador, {"goal": {"objective": "Terminar la 1.6.0", "status": "paused", "tokensUsed": 1200,
			"tokenBudget": None, "timeUsedSeconds": 30, "threadId": params.get("threadId")}})
	elif metodo == "skills/list":
		responder(identificador, {"data": [{"cwd": (params.get("cwds") or [""])[0], "errors": [], "skills": [
			{"name": "revisar", "path": "C:\\skills\\revisar\\SKILL.md", "description": "Revisa el código", "enabled": True, "scope": "user"},
			{"name": "apagada", "path": "C:\\skills\\apagada\\SKILL.md", "description": "No sale", "enabled": False, "scope": "user"}]}]})
	elif metodo == "thread/backgroundTerminals/list":
		if ESCENARIO != "terminales":
			escribir({"id": identificador, "error": {"code": -32601, "message": "método desconocido: %s" % metodo}})
			return
		terminales = [] if estado.get("terminado") else [{"processId": "proc-1", "itemId": "orden-9", "command": "npm run dev",
			"cwd": "C:\\proyecto"}]
		responder(identificador, {"data": terminales, "nextCursor": None})
	elif metodo == "thread/backgroundTerminals/terminate":
		estado["terminado"] = True
		responder(identificador, {"terminated": params.get("processId") != "proc-acabado"})
	elif metodo == "mcpServerStatus/list":
		responder(identificador, {"data": [
			{"name": "github", "authStatus": "oAuth", "runtimeStatus": "connected", "tools": {"buscar": {}, "leer": {}},
				"resources": [], "resourceTemplates": []},
			{"name": "notion", "authStatus": "notLoggedIn", "runtimeStatus": None, "tools": {}, "resources": [], "resourceTemplates": []},
			{"name": "roto", "authStatus": "unsupported", "runtimeStatus": "failed", "toolsError": "connection closed",
				"tools": {}, "resources": [], "resourceTemplates": []}], "nextCursor": None})
	elif metodo == "mcpServer/oauth/login":
		responder(identificador, {"authorizationUrl": "https://ejemplo.com/autorizar", "loginId": "inicio-1"})
		notificar("mcpServer/oauthLogin/completed", {"threadId": params.get("threadId"), "name": params.get("name"),
			"loginId": "inicio-1", "success": ESCENARIO != "oauth_falla", "error": "denegado" if ESCENARIO == "oauth_falla" else None})
	elif metodo == "config/read":
		responder(identificador, {"config": {"mcp_servers": {"github": {"url": "https://x"}, "raro.nombre": {}}}, "origins": {}})
	elif metodo == "turn/start":
		_turno(identificador, params)
	elif metodo == "turn/steer":
		turno = estado["turno_actual"]
		if ESCENARIO == "steer_muere":
			# El proceso muere con la petición ya escrita: nadie sabe si la llegó a leer.
			os._exit(3)
		if ESCENARIO == "steer_interno":
			escribir({"id": identificador, "error": {"code": -32603, "message": "error interno"}})
			return
		if turno is None or params.get("expectedTurnId") != turno:
			escribir({"id": identificador, "error": {"code": -32600, "message": "no hay turno activo"}})
			return
		responder(identificador, {"turnId": turno})
		mensaje_agente(turno, "msg-steer", "Corrección: " + texto_de(params))
	elif metodo == "turn/interrupt":
		responder(identificador, {})
		if estado["esperando"] is not None:
			notificar("serverRequest/resolved", {"threadId": estado["hilo"], "requestId": estado["esperando"][0]})
			estado["esperando"] = None
		if estado["turno_actual"]:
			completar(estado["turno_actual"], "interrupted")
	elif metodo == "thread/unsubscribe" or metodo == "thread/name/set":
		responder(identificador, {})
	elif metodo == "model/list":
		fugas = [nombre for nombre in os.environ if nombre.upper() in SOSPECHOSAS or nombre.upper().startswith(("OPENAI_", "AZURE_OPENAI_"))]
		responder(identificador, {"data": [
			{"id": "gpt-falso", "model": "gpt-falso", "displayName": "GPT falso", "hidden": False, "isDefault": True,
				"description": "fugas=%s;prueba=%s" % (",".join(sorted(fugas)), os.environ.get("CTRLIA_PRUEBA", "")),
				"defaultReasoningEffort": "medium", "supportedReasoningEfforts": []},
			{"id": "oculto", "model": "oculto", "displayName": "Oculto", "hidden": True, "isDefault": False, "description": "",
				"defaultReasoningEffort": "medium", "supportedReasoningEfforts": []}]})
	elif metodo == "thread/list" and ESCENARIO == "desaparecido":
		otros = [] if params.get("archived") else [dict(hilo("hilo-que-si"))]
		responder(identificador, {"data": otros, "nextCursor": None})
	elif metodo == "thread/list":
		if params.get("cursor") == "c2":
			datos = [dict(hilo("hilo-viejo"), name="Viejo", source={"subAgent": {}}, status={"type": "notLoaded"})]
			responder(identificador, {"data": datos, "nextCursor": None})
		else:
			responder(identificador, {"data": [dict(hilo("hilo-1"), preview="Primera línea\nsegunda")], "nextCursor": "c2"})
	elif metodo == "thread/read" and params.get("threadId") in estado["padres"]:
		hijo = params["threadId"]
		responder(identificador, {"thread": dict(hilo(hijo), parentThreadId=estado["padres"][hijo], source={"subAgent": {}})})
	elif metodo == "thread/read" and params.get("threadId") == "hilo-desconocido":
		escribir({"id": identificador, "error": {"code": -32600, "message": "thread not found"}})
	elif metodo == "thread/read" and ESCENARIO == "desaparecido":
		escribir({"id": identificador, "error": {"code": -32600, "message": "thread not found"}})
	elif metodo == "thread/read":
		turno = {"id": "t1", "status": "completed", "startedAt": 10, "completedAt": 20, "itemsView": "full", "items": [
			{"type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "Hola"}, {"type": "localImage", "path": "C:\\i.png"}]},
			{"type": "reasoning", "id": "r1", "summary": ["x"]},
			{"type": "commandExecution", "id": "c1", "command": "dir", "aggregatedOutput": "a.txt", "status": "completed"},
			{"type": "agentMessage", "id": "a1", "text": "Adiós"}]}
		responder(identificador, {"thread": dict(hilo(params["threadId"]), turns=[turno])})
	elif metodo == "account/read":
		responder(identificador, {"account": {"type": "chatgpt", "email": "persona@example.com", "planType": "plus"}, "requiresOpenaiAuth": True})
	else:
		escribir({"id": identificador, "error": {"code": -32601, "message": "método desconocido: %s" % metodo}})


def main() -> None:
	sys.stdout.reconfigure(encoding="utf-8", newline="\n")
	if "--version" in sys.argv:
		print("codex-cli 0.0.0-falso")
		return
	for linea in sys.stdin.buffer:
		texto = linea.decode("utf-8").strip()
		if not texto:
			continue
		mensaje = json.loads(texto)
		apuntar(mensaje)
		atender(mensaje)


if __name__ == "__main__":
	main()
