"""Servidor de Ollama falso para las pruebas (escucha solo en 127.0.0.1).

Imita las rutas que usa Ctrl+IA (``/api/version``, ``/api/tags``, ``/api/ps``
y ``/api/chat`` en streaming NDJSON). El comportamiento del chat depende del
nombre del modelo pedido:

- ``normal`` (o cualquier otro instalado): «Hola» + « mundo» y la línea final.
- ``piensa``: razona («Pensando...») y después contesta.
- ``goteo``: escribe un trozo cada 50 ms hasta que el cliente se va.
- ``carga``: no contesta hasta que se activa ``liberar`` (modelo cargándose).
- ``rompe``: un trozo y después una línea ``{"error": ...}``.
- ``sin-think``: rechaza con 400 cualquier petición que lleve ``think``.
- ``corta``: un trozo y cierra sin línea final.
- un modelo que no está en ``modelos``: 404 «model not found».
"""

from __future__ import annotations

import json
import select
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _ServidorSilencioso(ThreadingHTTPServer):
	"""No imprime trazas cuando el cliente cierra la conexión (detener, fin de la prueba)."""

	daemon_threads = True

	def handle_error(self, request, client_address):  # noqa: ANN001
		if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
			return
		super().handle_error(request, client_address)


class ServidorOllamaFalso:
	"""Servidor en un hilo; ``direccion`` es su URL base."""

	def __init__(self) -> None:
		self.modelos: list[dict[str, Any]] = [
			{"name": "normal:latest", "model": "normal:latest", "size": 2_000_000_000, "modified_at": "2026-01-01T10:00:00Z",
				"details": {"family": "llama", "parameter_size": "3.2B", "quantization_level": "Q4_K_M"}},
			{"name": "reciente:latest", "model": "reciente:latest", "size": 500_000_000, "modified_at": "2026-09-01T10:00:00Z",
				"details": {"family": "gemma", "parameter_size": "1B"}},
			{"name": "nube:cloud", "model": "nube:cloud", "remote_host": "https://ollama.com:443", "remote_model": "nube",
				"modified_at": "2025-01-01T10:00:00Z", "details": {}},
		]
		for nombre in ("piensa", "goteo", "carga", "rompe", "sin-think", "corta"):
			self.modelos.append({"name": nombre, "model": nombre, "size": 1024, "modified_at": "2020-01-01T00:00:00Z"})
		self.cargados: list[str] = ["normal:latest", "reciente:latest", "piensa", "goteo", "carga", "rompe", "sin-think", "corta"]
		self.peticiones: list[dict[str, Any]] = []
		self.cabeceras: list[dict[str, str]] = []
		self.liberar = threading.Event()
		self.desconectado = threading.Event()
		self.terminar = threading.Event()
		servidor = self

		class Manejador(BaseHTTPRequestHandler):
			# HTTP/1.1 con respuestas troceadas, como el Ollama real (en Windows, cerrar
			# cada conexión al estilo HTTP/1.0 provoca reinicios esporádicos).
			protocol_version = "HTTP/1.1"

			def log_message(self, formato, *args):  # noqa: ANN001, ANN002 - silencio en las pruebas
				return

			def _json(self, codigo: int, datos: Any) -> None:
				cuerpo = json.dumps(datos).encode("utf-8")
				self.send_response(codigo)
				self.send_header("Content-Type", "application/json")
				self.send_header("Content-Length", str(len(cuerpo)))
				self.end_headers()
				self.wfile.write(cuerpo)

			def do_GET(self) -> None:  # noqa: N802 - nombre de http.server
				servidor.cabeceras.append(dict(self.headers.items()))
				if self.path == "/api/version":
					self._json(200, {"version": "0.12.3"})
				elif self.path == "/api/tags":
					self._json(200, {"models": servidor.modelos})
				elif self.path == "/api/ps":
					self._json(200, {"models": [{"name": n, "model": n} for n in servidor.cargados]})
				else:
					self._json(404, {"error": "404 page not found"})

			def _linea(self, datos: dict[str, Any]) -> None:
				linea = json.dumps(datos).encode("utf-8") + b"\n"
				self.wfile.write(b"%x\r\n%s\r\n" % (len(linea), linea))
				self.wfile.flush()

			def _fin_stream(self) -> None:
				self.wfile.write(b"0\r\n\r\n")
				self.wfile.flush()

			def _trozo(self, modelo: str, texto: str = "", pensado: str = "") -> None:
				mensaje: dict[str, Any] = {"role": "assistant", "content": texto}
				if pensado:
					mensaje["thinking"] = pensado
				self._linea({"model": modelo, "created_at": "2026-10-17T10:00:00Z", "message": mensaje, "done": False})

			def _final(self, modelo: str) -> None:
				self._linea({"model": modelo, "message": {"role": "assistant", "content": ""}, "done": True,
					"done_reason": "stop", "prompt_eval_count": 12, "eval_count": 5})

			def _cliente_se_fue(self) -> bool:
				"""``True`` si el cliente cerró o cortó la conexión.

				No basta con esperar a que falle un ``send``: en Windows, si el cliente
				cierra de forma ordenada, los envíos pueden seguir «funcionando» un
				rato. El cierre (FIN o RST) sí deja el socket legible al instante.
				"""
				try:
					legibles, _e, _x = select.select([self.connection], [], [], 0)
					if not legibles:
						return False
					return self.connection.recv(1, socket.MSG_PEEK) == b""
				except OSError:
					return True

			def _cabeceras_stream(self) -> None:
				self.send_response(200)
				self.send_header("Content-Type", "application/x-ndjson")
				self.send_header("Transfer-Encoding", "chunked")
				self.end_headers()

			def do_POST(self) -> None:  # noqa: N802 - nombre de http.server
				servidor.cabeceras.append(dict(self.headers.items()))
				longitud = int(self.headers.get("Content-Length") or 0)
				cuerpo = json.loads(self.rfile.read(longitud) or b"{}")
				servidor.peticiones.append(cuerpo)
				if self.path != "/api/chat":
					self._json(404, {"error": "404 page not found"})
					return
				modelo = str(cuerpo.get("model") or "")
				if not any(m["name"] == modelo or m["model"] == modelo for m in servidor.modelos):
					self._json(404, {"error": "model '%s' not found" % modelo})
					return
				try:
					if self._chat(modelo, cuerpo):
						self._fin_stream()
				except OSError:
					servidor.desconectado.set()
					self.close_connection = True

			def _chat(self, modelo: str, cuerpo: dict[str, Any]) -> bool:
				"""Contesta al chat; ``True`` si abrió un streaming que hay que cerrar."""
				if modelo == "sin-think" and "think" in cuerpo:
					self._json(400, {"error": "\"sin-think\" does not support thinking"})
					return False
				if modelo == "carga":
					servidor.liberar.wait(10)
				self._cabeceras_stream()
				if modelo == "goteo":
					limite = time.monotonic() + 30
					while time.monotonic() < limite and not servidor.terminar.is_set():
						if self._cliente_se_fue():
							servidor.desconectado.set()
							self.close_connection = True
							return False
						self._trozo(modelo, "bla ")
						time.sleep(0.05)
					return True
				if modelo == "piensa":
					self._trozo(modelo, pensado="Pensando...")
				if modelo == "rompe":
					self._trozo(modelo, "Empiezo")
					self._linea({"error": "se acabó la memoria"})
					return True
				if modelo == "corta":
					self._trozo(modelo, "A medias")
					return True
				self._trozo(modelo, "Hola")
				self._trozo(modelo, " mundo")
				self._final(modelo)
				return True

		self._http = _ServidorSilencioso(("127.0.0.1", 0), Manejador)
		self.direccion = "http://127.0.0.1:%d" % self._http.server_address[1]
		self._hilo = threading.Thread(target=self._http.serve_forever, args=(0.05,), name="ollama-falso", daemon=True)

	def __enter__(self) -> ServidorOllamaFalso:
		self._hilo.start()
		return self

	def __exit__(self, *_exc: object) -> None:
		self.terminar.set()
		self.liberar.set()
		self._http.shutdown()
		self._http.server_close()

	def mensajes(self, indice: int = -1) -> list[dict[str, Any]]:
		"""Mensajes que llegaron en la petición de chat ``indice``."""
		return self.peticiones[indice]["messages"]
