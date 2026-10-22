"""Los archivos de reglas propios de Codex (``~/.codex/rules/*.rules``), para «Permisos de la sesión» (AIChat #307).

Codex guarda ahí lo que permites «siempre» para sus órdenes (su política de
ejecución). Ctrl+IA solo dice cuántos hay y dónde: no los lee por dentro ni
los cambia. Sin wx.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def carpeta_codex() -> Path:
	otra = os.environ.get("CODEX_HOME")
	return Path(otra) if otra else Path.home() / ".codex"


def archivos_de_reglas() -> list[Path]:
	"""Los ``.rules`` de la carpeta de reglas de Codex, ordenados; vacío si no hay o no se puede leer."""
	carpeta = carpeta_codex() / "rules"
	try:
		return sorted(ruta for ruta in carpeta.iterdir() if ruta.is_file() and ruta.suffix == ".rules")
	except OSError:
		log.debug("No se pudo mirar la carpeta de reglas de Codex %s", carpeta, exc_info=True)
		return []


def describir(archivos: list[Path]) -> str:
	if not archivos:
		return ("Codex no tiene archivos de reglas propios: cada orden que necesita permiso te la pregunta, salvo lo "
			"que hayas permitido aquí durante la sesión o lo que permita su modo.")
	lista = "\n".join(str(ruta) for ruta in archivos)
	return (f"Codex tiene {len(archivos)} archivos de reglas propios, con lo que le permitiste «siempre». Valen en "
		"todas sus sesiones, también fuera de Ctrl+IA, y se cambian en Codex. Ctrl+IA no los modifica:\n" + lista)
