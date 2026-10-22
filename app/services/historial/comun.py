"""Ayudas comunes de los lectores de historial: SQLite de solo lectura y fechas."""

from __future__ import annotations

import contextlib
import datetime as _dt
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@contextlib.contextmanager
def solo_lectura(ruta: Path) -> Iterator[sqlite3.Connection]:
	"""Abre una base de datos de otro programa sin poder escribir en ella.

	Lanza ``sqlite3.Error`` si no se puede abrir. La conexión se cierra al salir.
	"""
	conexion = sqlite3.connect(f"{Path(ruta).resolve().as_uri()}?mode=ro", uri=True, timeout=2)
	try:
		conexion.row_factory = sqlite3.Row
		yield conexion
	finally:
		conexion.close()


def fecha(valor: Any) -> float:
	"""Segundos desde 1970 de una fecha de SQLite o ISO 8601 (``0.0`` si no se entiende)."""
	if isinstance(valor, int | float):
		return float(valor)
	if not isinstance(valor, str) or not valor.strip():
		return 0.0
	texto = valor.strip().replace("Z", "+00:00")
	try:
		momento = _dt.datetime.fromisoformat(texto)
	except ValueError:
		# Formato de Go: «2026-10-18 11:22:33.123456789 +0200 CEST».
		partes = texto.split(" ")
		try:
			momento = _dt.datetime.fromisoformat(" ".join(partes[:2]) + (partes[2] if len(partes) > 2 else ""))
		except ValueError:
			log.debug("Fecha no reconocida: %r", valor)
			return 0.0
	if momento.tzinfo is None:
		momento = momento.replace(tzinfo=_dt.UTC)
	# Antes de 1970 es una fecha sin poner: Antigravity guarda la vacía de Go, «0001-01-01», que Windows no admite.
	return max(momento.timestamp(), 0.0)
