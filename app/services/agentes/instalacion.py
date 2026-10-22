"""Diagnóstico de cómo está instalado un agente (sin interfaz).

Ctrl+IA solo lanza binarios nativos (ver ``procesos.buscar_ejecutable``): los
lanzadores ``.cmd`` y ``.ps1`` que deja npm pasan por ``cmd.exe`` o PowerShell,
que reinterpretan los argumentos. Por eso, si Claude Code solo está instalado
con npm (la forma antigua), no se puede usar, y hay que explicar a la persona
por qué y qué hacer, en vez de decir sin más que no está instalado.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from app.services.agentes import procesos

log = logging.getLogger(__name__)

#: Nombres de los lanzadores que crea ``npm install -g @anthropic-ai/claude-code``.
LANZADORES_CLAUDE = ("claude.cmd", "claude.ps1")

AVISO_CLAUDE_NPM = (
	"Claude Code está instalado con npm, la forma antigua de instalarlo, y Ctrl+IA solo puede usar la versión "
	"nativa (claude.exe), que es la segura. Para pasar a la versión nativa, abre una consola (por ejemplo, "
	"Símbolo del sistema) y ejecuta: claude install. Si se niega porque ya existe la instalación de npm, "
	"ejecuta: claude install --force. Otra forma: desinstala la versión de npm con la orden "
	"npm uninstall -g @anthropic-ai/claude-code y después instala Claude Code desde «Agentes instalados». "
	"Tu inicio de sesión en Claude se conserva. Cuando termines, vuelve a comprobar los agentes."
)


def _carpetas_busqueda() -> list[Path]:
	"""Carpetas del PATH y la carpeta global de npm (``%APPDATA%\\npm``), sin repetir."""
	carpetas: list[Path] = []
	for parte in (os.environ.get("PATH") or "").split(os.pathsep):
		parte = parte.strip().strip('"')
		if parte:
			carpetas.append(Path(parte))
	appdata = os.environ.get("APPDATA")  # auditoria: ignorar ruta-no-portable - localiza programas instalados por npm; no guarda datos
	if appdata:
		carpetas.append(Path(appdata) / "npm")
	vistas: set[str] = set()
	unicas: list[Path] = []
	for carpeta in carpetas:
		clave = os.path.normcase(str(carpeta))
		if clave not in vistas:
			vistas.add(clave)
			unicas.append(carpeta)
	return unicas


def lanzadores_npm_claude() -> list[Path]:
	"""Lanzadores de npm de Claude Code (``claude.cmd``, ``claude.ps1``) en el PATH o en ``%APPDATA%\\npm``."""
	encontrados: list[Path] = []
	for carpeta in _carpetas_busqueda():
		for nombre in LANZADORES_CLAUDE:
			ruta = carpeta / nombre
			try:
				if ruta.is_file():
					encontrados.append(ruta)
			except OSError:
				log.debug("No se pudo comprobar %s", ruta, exc_info=True)
	return encontrados


def _buscar_claude_nativo() -> Path | None:
	from app.services.agentes.claude import buscar_claude  # aquí: claude.py puede importar este módulo

	return buscar_claude()


def aviso_claude_npm(buscar_nativo: Callable[[], Path | None] | None = None) -> str:
	"""Explicación en español si Claude Code solo está instalado con npm; ``""`` si no es el caso.

	Es el caso cuando hay un ``claude.cmd`` o ``claude.ps1`` de npm (en el PATH
	o en ``%APPDATA%\\npm``) y no hay ningún ``claude.exe`` nativo: ni en las
	rutas donde lo busca Ctrl+IA (``buscar_nativo``, de fábrica ``buscar_claude``)
	ni detrás del propio lanzador. Nunca ejecuta nada: solo mira archivos.
	"""
	lanzadores = lanzadores_npm_claude()
	if not lanzadores:
		return ""
	try:
		nativo = (buscar_nativo or _buscar_claude_nativo)()
	except Exception:  # noqa: BLE001 - el diagnóstico nunca debe tumbar la detección del agente
		log.debug("No se pudo buscar el Claude Code nativo", exc_info=True)
		nativo = None
	if nativo is not None:
		return ""
	if any(lanzador.suffix.lower() == ".cmd" and procesos._exe_tras_lanzador(lanzador) for lanzador in lanzadores):  # noqa: SLF001 - misma regla que buscar_ejecutable
		return ""
	log.info("Claude Code solo está instalado con npm: %s", ", ".join(str(r) for r in lanzadores))
	return AVISO_CLAUDE_NPM
