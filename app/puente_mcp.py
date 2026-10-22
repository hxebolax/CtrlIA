"""Puente MCP por stdio hacia Ctrl+IA (``ctrlia-mcp.exe`` en la versión compilada).

Algunos agentes solo saben lanzar servidores MCP como programas que hablan
JSON-RPC por su entrada y salida estándar (los agentes ACP sin HTTP y
Antigravity). Este puente lee cada mensaje (uno por línea), lo reenvía por HTTP
al servidor local de Ctrl+IA y escribe la respuesta, también en una línea.

De dónde saca la dirección y el token:

- ``CTRLIA_URL`` y ``CTRLIA_TOKEN`` en el entorno, o
- ``CTRLIA_CONEXION``: ruta de un JSON ``{"url": ..., "token": ...}`` que
  Ctrl+IA reescribe en cada arranque (se vuelve a leer si el token deja de valer).

Solo se conecta a este equipo (``127.0.0.1`` o ``localhost``). Nunca escribe
en la salida estándar nada que no sea JSON-RPC: los avisos van a la de errores.
Solo usa la biblioteca estándar: se ejecuta con ``python -I``.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
from urllib.parse import urlsplit

#: Segundos de espera a Ctrl+IA en cada mensaje.
TIEMPO = 30.0
#: Tamaño máximo de un mensaje que se reenvía.
MAX_MENSAJE = 64 * 1024


def avisar(texto: str) -> None:
	"""Aviso para quien lanzó el puente (el agente lo apunta en su registro): siempre al error estándar."""
	if sys.stderr is not None:
		sys.stderr.write("[ctrlia-mcp] " + texto + "\n")
		sys.stderr.flush()


def leer_conexion() -> tuple[str, str]:
	"""Dirección y token, del entorno o del archivo de conexión."""
	url = os.environ.get("CTRLIA_URL", "")
	token = os.environ.get("CTRLIA_TOKEN", "")
	ruta = os.environ.get("CTRLIA_CONEXION", "")
	if (not url or not token) and ruta:
		try:
			with open(ruta, encoding="utf-8") as archivo:
				datos = json.load(archivo)
			url = str(datos.get("url") or "")
			token = str(datos.get("token") or "")
		except (OSError, ValueError, AttributeError) as error:
			avisar("No se pudo leer el archivo de conexión: %s" % error)
	return url, token


class Cliente:
	"""Conexión HTTP/1.1 persistente con el servidor local de Ctrl+IA."""

	def __init__(self) -> None:
		self._conexion: http.client.HTTPConnection | None = None
		self._url = ""
		self._token = ""
		self._ruta = "/mcp"

	def _preparar(self) -> None:
		url, token = leer_conexion()
		partes = urlsplit(url)
		if partes.scheme != "http" or partes.hostname not in ("127.0.0.1", "localhost") or not partes.port:
			raise ConnectionError("Ctrl+IA no ha dado una dirección local válida")
		if not token:
			raise ConnectionError("Ctrl+IA no ha dado el token de la sesión")
		if self._conexion is None or url != self._url:
			self.cerrar()
			self._conexion = http.client.HTTPConnection(partes.hostname, partes.port, timeout=TIEMPO)
		self._url, self._token, self._ruta = url, token, partes.path or "/mcp"

	def cerrar(self) -> None:
		if self._conexion is not None:
			self._conexion.close()
			self._conexion = None

	def enviar(self, cuerpo: bytes) -> tuple[int, bytes]:
		"""``(estado, cuerpo)`` de la respuesta; reintenta una vez si la conexión se había cerrado o el token cambió."""
		for intento in range(2):
			self._preparar()
			assert self._conexion is not None
			try:
				self._conexion.request("POST", self._ruta, body=cuerpo, headers={
					"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
					"Authorization": "Bearer " + self._token,
				})
				respuesta = self._conexion.getresponse()
				datos = respuesta.read()
			except (OSError, http.client.HTTPException):
				self.cerrar()
				if intento:
					raise
				continue
			if respuesta.status == 401 and not intento:
				self.cerrar()  # Ctrl+IA se reinició: el archivo de conexión tiene el token nuevo
				continue
			return respuesta.status, datos
		raise ConnectionError("Ctrl+IA no responde")


def escribir(mensaje: dict) -> None:
	datos = json.dumps(mensaje, ensure_ascii=False, separators=(",", ":")) + "\n"
	sys.stdout.buffer.write(datos.encode("utf-8"))
	sys.stdout.buffer.flush()


def error(ident: object, codigo: int, texto: str) -> dict:
	return {"jsonrpc": "2.0", "id": ident, "error": {"code": codigo, "message": texto}}


def atender(cliente: Cliente, linea: bytes) -> None:
	try:
		mensaje = json.loads(linea)
	except (ValueError, RecursionError):
		escribir(error(None, -32700, "JSON no válido"))
		return
	ident = mensaje.get("id") if isinstance(mensaje, dict) else None
	pide_respuesta = isinstance(mensaje, dict) and isinstance(mensaje.get("method"), str) and ident is not None
	if len(linea) > MAX_MENSAJE:
		if pide_respuesta:
			escribir(error(ident, -32600, "Mensaje demasiado grande"))
		return
	try:
		estado, cuerpo = cliente.enviar(linea)
	except (OSError, ConnectionError, http.client.HTTPException) as fallo:
		avisar("No se pudo hablar con Ctrl+IA: %s" % fallo)
		if pide_respuesta:
			escribir(error(ident, -32000, "Ctrl+IA no está abierto o no responde; sus herramientas no están disponibles."))
		return
	if estado == 200 and cuerpo.strip():
		try:
			escribir(json.loads(cuerpo))
		except ValueError:
			if pide_respuesta:
				escribir(error(ident, -32603, "Respuesta no válida de Ctrl+IA"))
		return
	if estado in (200, 202):
		return
	avisar("Ctrl+IA respondió con el estado HTTP %d" % estado)
	if pide_respuesta:
		texto = ("Ctrl+IA ya no reconoce esta sesión: vuelve a abrirla en Ctrl+IA." if estado == 401
			else "Ctrl+IA ha rechazado la petición (HTTP %d)." % estado)
		escribir(error(ident, -32000, texto))


def main() -> int:
	cliente = Cliente()
	try:
		for linea in sys.stdin.buffer:
			linea = linea.strip()
			if linea:
				atender(cliente, linea)
	finally:
		cliente.cerrar()
	return 0


if __name__ == "__main__":
	sys.exit(main())
