"""Utilidades comunes de las pruebas del actualizador (PLANTILLA del agente «actualizador»).

- Servidor HTTP LOCAL (``::1`` o 127.0.0.1) con soporte de ``Range`` y cortes simulados:
  las pruebas nunca salen a Internet.
- Carga de ``tools/firmar_version.py`` sin que ``tools`` sea un paquete.
- Construcción de paquetes ZIP y manifiestos firmados como los de ``empaquetar.py``.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import logging
import socket
import threading
import urllib.parse
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def cargar_firmador():
	"""Importa ``tools/firmar_version.py`` como módulo."""
	especificacion = importlib.util.spec_from_file_location("firmar_version", RAIZ / "tools" / "firmar_version.py")
	modulo = importlib.util.module_from_spec(especificacion)
	especificacion.loader.exec_module(modulo)
	return modulo


class _Manejador(http.server.BaseHTTPRequestHandler):
	"""Sirve archivos de una carpeta; admite Range y puede cortar una respuesta."""

	def log_message(self, *argumentos) -> None:  # silencio: no ensuciar la salida de pytest
		return

	def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por http.server)
		prueba: Servidor = self.server.prueba  # type: ignore[attr-defined]
		nombre = urllib.parse.unquote(self.path.lstrip("/").split("?", 1)[0])
		rango = self.headers.get("Range")
		prueba.peticiones.append((nombre, rango))
		ruta = prueba.carpeta / nombre
		if not ruta.is_file():
			self.send_error(404)
			return
		datos = ruta.read_bytes()
		inicio = 0
		if rango and prueba.admite_rangos:
			inicio = int(rango.split("=", 1)[1].split("-", 1)[0])
			if inicio >= len(datos):
				self.send_response(416)
				self.send_header("Content-Range", f"bytes */{len(datos)}")
				self.send_header("Content-Length", "0")
				self.end_headers()
				return
			self.send_response(206)
			self.send_header("Content-Range", f"bytes {inicio}-{len(datos) - 1}/{len(datos)}")
		else:
			self.send_response(200)
		cuerpo = datos[inicio:]
		self.send_header("Content-Type", "application/octet-stream")
		self.send_header("Content-Length", str(len(cuerpo)))
		self.end_headers()
		if prueba.cortar_en is not None:
			# Simula una conexión que se cae: anuncia todo y envía solo una parte.
			self.wfile.write(cuerpo[: prueba.cortar_en])
			self.wfile.flush()
			prueba.cortar_en = None
			self.close_connection = True
			return
		self.wfile.write(cuerpo)


class _ServidorIPv6(http.server.ThreadingHTTPServer):
	address_family = socket.AF_INET6


def _crear_http() -> http.server.ThreadingHTTPServer:
	"""Prefiere ``::1``: en algunos equipos un filtro de red corta (RST) las
	transferencias de más de unos KB por 127.0.0.1 y las pruebas fallan al azar.
	Si no hay IPv6, usa 127.0.0.1."""
	if socket.has_ipv6:
		try:
			return _ServidorIPv6(("::1", 0), _Manejador)
		except OSError:
			logging.getLogger(__name__).debug("Sin IPv6 en bucle local; se usa 127.0.0.1", exc_info=True)
	return http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Manejador)


class Servidor:
	"""Servidor HTTP local (``::1`` o ``127.0.0.1``) en un puerto libre, en un hilo."""

	def __init__(self, carpeta: Path) -> None:
		self.carpeta = carpeta
		self.carpeta.mkdir(parents=True, exist_ok=True)
		self.admite_rangos = True
		self.cortar_en: int | None = None
		self.peticiones: list[tuple[str, str | None]] = []
		self._http = _crear_http()
		self._http.prueba = self  # type: ignore[attr-defined]
		self._hilo = threading.Thread(target=self._http.serve_forever, daemon=True)
		self._hilo.start()

	def url(self, nombre: str) -> str:
		anfitrion = "[::1]" if self._http.address_family == socket.AF_INET6 else "127.0.0.1"
		return f"http://{anfitrion}:{self._http.server_address[1]}/{urllib.parse.quote(nombre)}"

	def cerrar(self) -> None:
		self._http.shutdown()
		self._http.server_close()


def crear_zip(ruta: Path, archivos: dict[str, bytes], carpeta_raiz: str | None = "MiAplicacion") -> Path:
	"""Crea un ZIP como el de ``empaquetar.py`` (todo bajo una carpeta raíz)."""
	with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as paquete:
		for nombre, contenido in archivos.items():
			paquete.writestr(f"{carpeta_raiz}/{nombre}" if carpeta_raiz else nombre, contenido)
	return ruta


def publicar(
	carpeta: Path,
	version: str,
	archivos: dict[str, bytes],
	ruta_clave: Path | None,
	*,
	nombre: str = "MiAplicacion",
	novedades: str | None = None,
	extra: dict | None = None,
) -> dict:
	"""Deja en ``carpeta`` el ZIP y el ``version.json`` (firmado si hay clave)."""
	zip_ = crear_zip(carpeta / f"{nombre}_v{version}.zip", archivos, nombre)
	datos = zip_.read_bytes()
	manifiesto = {
		"version": version,
		"file": zip_.name,
		"sha256": hashlib.sha256(datos).hexdigest(),
		"size": len(datos),
		"date": "2026-09-25",
	}
	manifiesto.update(extra or {})
	ruta_json = carpeta / "version.json"
	ruta_json.write_text(json.dumps(manifiesto, indent=4, ensure_ascii=False), encoding="utf-8")
	if ruta_clave is not None:
		ruta_novedades = None
		if novedades is not None:
			ruta_novedades = carpeta / "novedades.txt"
			ruta_novedades.write_text(novedades, encoding="utf-8")
		cargar_firmador().firmar(ruta_clave, ruta_json, ruta_novedades)
	return json.loads(ruta_json.read_text(encoding="utf-8"))
