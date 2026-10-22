"""Servidor HTTP local para las pruebas de red (PLANTILLA del agente «red-http»).

Las pruebas NUNCA salen a Internet: este servidor escucha en 127.0.0.1 en un
puerto libre (0) dentro de un hilo y simula los casos difíciles: errores 503 y
429 con Retry-After, respuestas lentas, rangos, cortes, Content-Disposition...

Cada ruta lleva un contador por URL completa (``/503?id=a``), así cada prueba
usa su propio ``id`` y no depende del orden.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

CONTENIDO = bytes(range(256)) * 1200  # 300 KB deterministas
ETAG = '"v1-contenido"'


class Estado:
	"""Lo que el servidor ha visto, para que las pruebas lo comprueben."""

	def __init__(self) -> None:
		self.contador: Counter[str] = Counter()
		self.cabeceras: list[tuple[str, dict[str, str]]] = []
		self.cerrojo = threading.Lock()


class Manejador(BaseHTTPRequestHandler):
	protocol_version = "HTTP/1.1"
	estado: Estado

	def log_message(self, formato, *args):  # noqa: ANN001, ANN002 - silencio en las pruebas
		return

	def _responder(self, codigo: int, cuerpo: bytes = b"", cabeceras: dict[str, str] | None = None) -> None:
		self.send_response(codigo)
		for nombre, valor in (cabeceras or {}).items():
			self.send_header(nombre, valor)
		self.send_header("Content-Length", str(len(cuerpo)))
		self.end_headers()
		if cuerpo and self.command != "HEAD":
			self.wfile.write(cuerpo)

	def _con_rangos(self, admite_rangos: bool = True) -> None:
		"""Sirve CONTENIDO respetando Range e If-Range como un servidor real."""
		rango = self.headers.get("Range")
		si_rango = self.headers.get("If-Range")
		base = {"ETag": ETAG, "Accept-Ranges": "bytes" if admite_rangos else "none"}
		if admite_rangos and rango and (si_rango is None or si_rango == ETAG):
			inicio = int(rango.split("=", 1)[1].split("-", 1)[0])
			if inicio >= len(CONTENIDO):
				self._responder(416, cabeceras={**base, "Content-Range": f"bytes */{len(CONTENIDO)}"})
				return
			parte = CONTENIDO[inicio:]
			self._responder(206, parte, {**base, "Content-Range": f"bytes {inicio}-{len(CONTENIDO) - 1}/{len(CONTENIDO)}"})
			return
		self._responder(200, CONTENIDO, base)

	def do_GET(self) -> None:  # noqa: N802 - nombre de http.server
		partes = urlsplit(self.path)
		ruta = partes.path
		consulta = parse_qs(partes.query)
		with self.estado.cerrojo:
			self.estado.contador[self.path] += 1
			veces = self.estado.contador[self.path]
			self.estado.cabeceras.append((self.path, dict(self.headers.items())))

		if ruta == "/ua":
			self._responder(200, self.headers.get("User-Agent", "").encode("utf-8"))
		elif ruta == "/503":
			if veces <= 2:
				self._responder(503, b"saturado")
			else:
				self._responder(200, b"ok")
		elif ruta == "/503-siempre":
			self._responder(503, b"saturado")
		elif ruta == "/429":
			if veces == 1:
				self._responder(429, b"calma", {"Retry-After": "1"})
			else:
				self._responder(200, b"ok")
		elif ruta == "/429-largo":
			self._responder(429, b"vuelve en una hora", {"Retry-After": "3600"})
		elif ruta == "/lento":
			time.sleep(1.5)
			self._responder(200, b"por fin")
		elif ruta == "/404":
			self._responder(404, b"no existe")
		elif ruta == "/archivo.bin":
			self._con_rangos()
		elif ruta == "/sinrango.bin":
			self._con_rangos(admite_rangos=False)
		elif ruta == "/disposicion":
			self._responder(200, b"contenido", {"Content-Disposition": consulta["cd"][0]})
		elif ruta == "/goteo.bin":
			self.send_response(200)
			self.send_header("Content-Length", str(len(CONTENIDO)))
			self.end_headers()
			for inicio in range(0, len(CONTENIDO), 8192):
				try:
					self.wfile.write(CONTENIDO[inicio:inicio + 8192])
					self.wfile.flush()
				except OSError:
					return  # el cliente canceló y cerró
				time.sleep(0.02)
		elif ruta == "/cortado.bin":
			if self.headers.get("Range"):
				self._con_rangos()
				return
			# Anuncia el tamaño completo, envía la mitad y se queda mudo: el cliente
			# agota su tiempo de lectura a media descarga. (Cerrar el socket enseguida
			# no sirve en Windows: el cierre con datos pendientes en el bucle local a
			# veces deja al cliente esperando sin recibir el resto ni el fin.)
			self.send_response(200)
			self.send_header("Content-Length", str(len(CONTENIDO)))
			self.send_header("ETag", ETAG)
			self.send_header("Accept-Ranges", "bytes")
			self.end_headers()
			self.wfile.write(CONTENIDO[: len(CONTENIDO) // 2])
			self.wfile.flush()
			time.sleep(1.5)
			self.close_connection = True
		else:
			self._responder(404, b"ruta desconocida")

	# Las pruebas de POST usan las mismas rutas (sin cuerpo).
	do_POST = do_GET  # noqa: N815


class _ServidorSilencioso(ThreadingHTTPServer):
	"""No imprime trazas cuando el cliente corta a propósito (cancelación, tiempo agotado)."""

	daemon_threads = True

	def handle_error(self, request, client_address):  # noqa: ANN001
		import sys

		if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
			return
		super().handle_error(request, client_address)


class ServidorPrueba:
	"""Servidor en marcha: ``url(ruta)`` y ``estado`` para las comprobaciones."""

	def __init__(self) -> None:
		self.estado = Estado()
		manejador = type("ManejadorPrueba", (Manejador,), {"estado": self.estado})
		self._servidor = _ServidorSilencioso(("127.0.0.1", 0), manejador)
		self._hilo = threading.Thread(target=self._servidor.serve_forever, name="servidor-prueba", daemon=True)
		self._hilo.start()

	def url(self, ruta: str) -> str:
		host, puerto = self._servidor.server_address[:2]
		return f"http://{host}:{puerto}{ruta}"

	def veces(self, ruta: str) -> int:
		with self.estado.cerrojo:
			return self.estado.contador[ruta]

	def cabeceras_de(self, ruta: str) -> list[dict[str, str]]:
		with self.estado.cerrojo:
			return [c for r, c in self.estado.cabeceras if r == ruta]

	def detener(self) -> None:
		self._servidor.shutdown()
		self._servidor.server_close()


@pytest.fixture(scope="module")
def servidor():
	"""Un servidor por módulo de pruebas."""
	instancia = ServidorPrueba()
	yield instancia
	instancia.detener()
