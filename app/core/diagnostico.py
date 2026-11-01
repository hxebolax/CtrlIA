"""Paquete de diagnóstico para soporte.

Genera un ZIP en ``data/diagnostico`` con lo necesario para investigar un
problema que ha tenido otra persona, SIN datos privados:

- Las últimas sesiones de registro (ya redactadas al escribirse; se vuelven a
  redactar por si contienen algo anterior a la redacción).
- ``incidencias.log``, ``fallo_nativo.log`` y ``arranque_fallido.log``.
- ``sistema.txt``: versión de la aplicación, Python, wxPython, Windows,
  arquitectura, si está compilada, lectores de pantalla en ejecución y la
  lista de paquetes (sin rutas personales). Con :class:`DatosEntorno`, además
  cada agente (versión, sesión iniciada y cómo está instalado), cuántas sesiones
  hay de cada tipo y los servidores MCP de la sesión abierta.

Nunca incluye configuración, bases de datos, sesiones ni claves. Lo que se sabe
de los agentes y las sesiones lo recoge ``app/services/informe_fallo.py`` y llega
aquí ya como datos sueltos, sin títulos, carpetas ni cuentas: este módulo es del
núcleo y no conoce a los agentes.

PLANTILLA del agente «logging-profesional».
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path

from app.core import registro

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FilaAgente:
	"""Un agente en el informe: solo textos con forma conocida, nunca rutas ni cuentas."""

	nombre: str
	version: str = ""
	sesion: str = ""
	instalacion: str = ""


@dataclass(slots=True)
class DatosEntorno:
	"""Lo que se añade al informe del sistema."""

	agentes: list[FilaAgente] = field(default_factory=list)
	#: ``{agente: {origen: cuántas}}`` de las sesiones de la lista.
	sesiones: dict[str, dict[str, int]] = field(default_factory=dict)
	#: Sesiones ocultas o archivadas, que no cuentan en ``sesiones``.
	ocultas: int = 0
	#: ``(nombre, estado, motivo)`` de los servidores MCP de la sesión abierta; ``None`` si no hay ninguna abierta.
	servidores_mcp: list[tuple[str, str, str]] | None = None

# C:\Users\<nombre>\... pasa a C:\Users\<usuario>\... (el nombre de la cuenta es un dato personal).
_RUTA_USUARIO = re.compile(r"(?im)\b([a-z]:[\\/](?:users|usuarios)[\\/])[^\\/\"'\r\n]+?(?=[\\/]|$)")
#: La misma ruta sin nada detrás y con más texto en la línea («en C:\Users\nombre pasa esto»): hasta el espacio.
_RUTA_USUARIO_EN_FRASE = re.compile(r"(?i)\b([a-z]:[\\/](?:users|usuarios)[\\/])(?!<usuario>)[^\\/\"'\s]+")


def anonimizar(texto: str) -> str:
	"""Redacta secretos y quita el nombre de la cuenta de Windows de las rutas."""
	return _RUTA_USUARIO_EN_FRASE.sub(r"\1<usuario>", _RUTA_USUARIO.sub(r"\1<usuario>", registro.redactar(texto)))


_LECTORES = {"nvda.exe": "NVDA", "jfw.exe": "JAWS", "narrator.exe": "Narrador", "zt.exe": "ZoomText"}


def _lectores_en_ejecucion() -> list[str]:
	"""Detecta los lectores de pantalla abiertos (solo informativo)."""
	try:
		salida = subprocess.run(
			["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10,
			creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
		).stdout.lower()
	except (OSError, subprocess.SubprocessError):
		return []
	return [nombre for proceso, nombre in _LECTORES.items() if f'"{proceso}"' in salida]


def _lineas_entorno(datos: DatosEntorno) -> list[str]:
	"""Agentes, recuentos de sesiones y servidores MCP, un apartado detrás de otro."""
	lineas: list[str] = []
	if datos.agentes:
		lineas.extend(["", "Agentes:"])
		for agente in datos.agentes:
			partes = [agente.version, agente.sesion, "instalación: " + agente.instalacion if agente.instalacion else ""]
			lineas.append(f"  {agente.nombre}: {'; '.join(parte for parte in partes if parte)}")
	if datos.sesiones or datos.ocultas:
		lineas.extend(["", "Sesiones en la lista:"])
		for agente, origenes in datos.sesiones.items():
			lineas.append(f"  {agente}: " + ", ".join(f"{cuantas} de {origen}" for origen, cuantas in origenes.items()))
		if datos.ocultas:
			lineas.append(f"  Ocultas o archivadas: {datos.ocultas}")
	if datos.servidores_mcp is not None:
		lineas.extend(["", "Servidores MCP de la sesión abierta:"])
		for nombre, estado, motivo in datos.servidores_mcp:
			lineas.append(f"  {nombre}: {estado}" + (f". Motivo: {motivo}" if motivo else ""))
		if not datos.servidores_mcp:
			lineas.append("  Ninguno que este agente informe.")
	return lineas


def informe_sistema(nombre_app: str = "", version: str = "", datos: DatosEntorno | None = None, paquetes: bool = True) -> str:
	"""Texto con la información del entorno, sin rutas ni datos personales.

	:param datos: agentes, sesiones y servidores MCP, si se han recogido.
	:param paquetes: ``False`` omite la lista de paquetes (un informe de fallo se abre en una dirección web).
	"""
	lineas = [
		f"Aplicación: {nombre_app} {version}".strip(),
		f"Fecha: {datetime.now().isoformat(timespec='seconds')}",
		f"Compilada: {'sí' if getattr(sys, 'frozen', False) else 'no'}",
		f"Python: {platform.python_version()} ({platform.architecture()[0]})",
		f"Sistema: {platform.platform()}",
	]
	try:
		import wx

		lineas.append(f"wxPython: {wx.version()}")
	except Exception:  # noqa: BLE001 - informativo
		lineas.append("wxPython: no disponible")
	lectores = _lectores_en_ejecucion()
	lineas.append(f"Lectores de pantalla en ejecución: {', '.join(lectores) or 'ninguno detectado'}")
	if datos is not None:
		lineas.extend(_lineas_entorno(datos))
	if paquetes:
		lineas.append("")
		lineas.append("Paquetes instalados:")
		try:
			lista = sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions())
			lineas.extend(f"  {p}" for p in lista)
		except Exception:  # noqa: BLE001 - en la versión compilada puede no haber metadatos
			lineas.append("  (no disponible)")
	return anonimizar("\n".join(lineas))


def generar_paquete(nombre_app: str = "", version: str = "", sesiones: int = 3, datos: DatosEntorno | None = None) -> Path:
	"""Crea el ZIP de diagnóstico y devuelve su ruta."""
	carpeta_logs = registro.carpeta_registros()
	destino_dir = carpeta_logs.parent / "diagnostico"
	destino_dir.mkdir(parents=True, exist_ok=True)
	destino = destino_dir / f"diagnostico_{datetime.now():%Y%m%d_%H%M%S}.zip"

	sesiones_log = sorted(carpeta_logs.glob("sesion_*.log*"))[-sesiones * 3:]
	fijos = [carpeta_logs / n for n in ("incidencias.log", "fallo_nativo.log", "arranque_fallido.log")]
	with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zip_:
		for archivo in [*sesiones_log, *fijos]:
			if not archivo.is_file():
				continue
			texto = archivo.read_text(encoding="utf-8", errors="replace")
			zip_.writestr(f"logs/{archivo.name}", anonimizar(texto))
		zip_.writestr("sistema.txt", informe_sistema(nombre_app, version, datos))
	log.info("Paquete de diagnóstico generado: %s", destino.name)
	return destino
