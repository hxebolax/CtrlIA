"""Antigravity CLI (``agy``) falsa para las pruebas.

Imita el protocolo ``stream-json`` sin interfaz. El escenario de cada turno
se elige con marcas dentro del texto del mensaje:

- (ninguna): respuesta normal en tres trozos que repite el mensaje.
- ``[herramienta]``: ejecuta ``git status`` y luego responde.
- ``[denegado]``: el resultado trae ``denied_actions``.
- ``[stderr_denegado]``: la denegación solo aparece en el error estándar.
- ``[mcp_denegado]``: una herramienta MCP denegada, como agy 1.4.2: el paso
  acaba en ``ERROR`` con la regla en su mensaje y ``denied_actions`` solo dice
  ``{"action": "mcp"}``; el error estándar trae el ejemplo genérico ``mcp(<target>)``.
- ``[error]``: resultado con ``status`` ``ERROR``.
- ``[sin_deltas]``: la respuesta solo llega en ``result.response``.
- ``[lento]``: escribe un trozo y se queda esperando (para ``detener``).
- ``[muere]``: sale con código 3 a mitad de turno.

Variables de entorno:

- ``FAKE_AGY_REGISTRO``: archivo donde se añade una línea JSON con los
  argumentos de cada arranque.
- ``FAKE_AGY_ID``: identificador de conversación nuevo (``conv-fake-1``).
- ``FAKE_AGY_PERDIDA``: no encuentra la conversación de ``--conversation`` y empieza otra.
"""

from __future__ import annotations

import json
import os
import sys
import time


def emitir(objeto: dict) -> None:
	sys.stdout.buffer.write((json.dumps(objeto, ensure_ascii=False) + "\n").encode("utf-8"))
	sys.stdout.buffer.flush()


def error(texto: str) -> None:
	sys.stderr.buffer.write((texto + "\n").encode("utf-8"))
	sys.stderr.buffer.flush()


def valor(argumentos: list[str], bandera: str) -> str | None:
	if bandera in argumentos:
		posicion = argumentos.index(bandera)
		if posicion + 1 < len(argumentos):
			return argumentos[posicion + 1]
	return None


def paso(cid: str, indice: int, estado: str, tipo: str, **resto) -> None:
	emitir({
		"event": "step_update",
		"step_update": {"conversation_id": cid, "step_index": indice, "state": estado, "step_type": tipo, **resto},
	})


def resultado(cid: str, estado: str = "SUCCESS", respuesta: str = "", denegadas: list | None = None) -> None:
	emitir({
		"event": "result",
		"result": {
			"conversation_id": cid,
			"status": estado,
			"response": respuesta,
			"num_turns": 1,
			"usage": {"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 5, "cache_read_tokens": 0, "total_tokens": 125},
			"denied_actions": denegadas or [],
		},
	})


def main() -> int:
	argumentos = sys.argv[1:]
	if "--version" in argumentos:
		print("agy 1.2.3 (falso)")
		return 0
	if argumentos[:1] == ["models"]:
		print("Modelos disponibles:")
		print("  flash - Gemini Flash")
		print("  pro - Gemini Pro")
		print("  gemini-3-ultra - Gemini Ultra experimental")
		return 0
	registro = os.environ.get("FAKE_AGY_REGISTRO")
	if registro:
		with open(registro, "a", encoding="utf-8") as archivo:
			archivo.write(json.dumps({"argv": argumentos, "cwd": os.getcwd()}) + "\n")
	if valor(argumentos, "--input-format") != "stream-json" or valor(argumentos, "--output-format") != "stream-json":
		error("agy falso: faltan --input-format/--output-format stream-json")
		return 2
	cid = valor(argumentos, "--conversation") or os.environ.get("FAKE_AGY_ID", "conv-fake-1")
	if valor(argumentos, "--conversation") and os.environ.get("FAKE_AGY_PERDIDA"):
		# Como el agy real: si no encuentra la conversación, avisa y empieza otra.
		error('warning: conversation "%s" not found' % cid)
		cid = os.environ.get("FAKE_AGY_ID", "conv-fake-1")
	modo = "always-proceed" if "--dangerously-skip-permissions" in argumentos else "request-review"
	emitir({
		"event": "init",
		"conversation_id": cid,
		"init": {"cwd": os.getcwd(), "tools": ["run_command", "write_file"], "permission_mode": modo, "argv": argumentos},
	})
	indice = 0
	for linea in iter(sys.stdin.buffer.readline, b""):
		linea = linea.decode("utf-8").strip()
		if not linea:
			continue
		mensaje = json.loads(linea)
		if mensaje.get("event") != "user":
			error("agy falso: evento desconocido")
			return 2
		contenido = mensaje["message"]["content"]
		if isinstance(contenido, list):
			contenido = "".join(parte.get("text", "") for parte in contenido)
		indice += 1
		paso(cid, indice, "DONE", "user_input")
		if "[muere]" in contenido:
			error("fallo grave simulado")
			return 3
		if "[herramienta]" in contenido:
			indice += 1
			info = {"name": "run_command", "parameters": {"command": "git status", "cwd": "."}}
			paso(cid, indice, "ACTIVE", "tool", tool_name="run_command", tool_info=info)
			paso(cid, indice, "DONE", "tool", tool_name="run_command", tool_info={**info, "output": "On branch main\n" + "x" * 5000})
		if "[mcp_denegado]" in contenido:
			indice += 1
			info = {"name": "call_mcp_tool", "parameters": {"Arguments": {}, "ServerName": "prueba", "ToolName": "hora"}}
			paso(cid, indice, "ACTIVE", "tool", tool_name="call_mcp_tool", tool_info=info)
			paso(cid, indice, "ERROR", "tool", tool_name="call_mcp_tool", tool_info={**info, "error": {"type": "TOOL_ERROR",
				"message": 'permission check failed for mcp "prueba/hora": user denied permission for mcp(prueba/hora)\n'
					"Do not attempt to circumvent this denial."}})
			error('jetski: a tool required the "mcp" permission that headless mode cannot prompt for. '
				"Add an allow-rule under permissions.allow in settings.json (e.g. mcp(<target>)).")
			resultado(cid, "SUCCESS", "", [{"action": "mcp", "display_name": "CallMcpTool"}])
			continue
		if "[error]" in contenido:
			error("Error: quota exceeded")
			resultado(cid, "ERROR", "")
			continue
		if "[modelo_desconocido]" in contenido:
			modelo = valor(argumentos, "--model")
			resultado(cid, "ERROR", 'invalid model selection (--model "%s" --effort ""): model %s is not recognized as a '
				"known model or custom model in settings Available models: Gemini 3.8 Flash (Medium)" % (modelo, modelo))
			continue
		if "[sin_deltas]" in contenido:
			resultado(cid, "SUCCESS", "Respuesta completa sin trozos.")
			continue
		indice += 1
		trozos = ["Has dicho: ", contenido, "."]
		for trozo in trozos:
			paso(cid, indice, "ACTIVE", "agent_response", text_delta=trozo)
			if "[lento]" in contenido:
				time.sleep(60)
		paso(cid, indice, "DONE", "agent_response", usage={"input_tokens": 50, "output_tokens": 10})
		denegadas = []
		if "[denegado]" in contenido:
			denegadas = [{"tool": "run_command", "parameters": {"command": "npm test"}}, "write_file(config/secreto.json)"]
			error("Denied: command(npm test) (non-interactive mode)")
		if "[stderr_denegado]" in contenido:
			error("Action denied in non-interactive mode: command(rm build)")
		resultado(cid, "SUCCESS", "".join(trozos), denegadas)
	return 0


if __name__ == "__main__":
	sys.exit(main())
