"""Historial de Codex en disco, para buscar en sus conversaciones (solo lectura).

La lista y la lectura de las conversaciones de Codex se hacen con su servidor
(``thread/list`` y ``thread/read``, en :mod:`app.services.agentes.codex`). Para
«Buscar en todas las sesiones» eso sería una petición por conversación, así que
aquí se leen directamente sus archivos ``rollout-<fecha>-<id>.jsonl`` de
``~/.codex/sessions`` y ``~/.codex/archived_sessions`` (o de ``CODEX_HOME``).

Solo se sacan los mensajes tuyos y las respuestas de Codex. El formato ha
cambiado entre versiones: se entienden los eventos ``item_completed``
(``UserMessage``/``AgentMessage``) y los antiguos ``user_message``/``agent_message``.
Nunca se escribe nada en esas carpetas.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.historial.comun import fecha

log = logging.getLogger(__name__)

_RE_ROLLOUT = re.compile(r"^rollout-.*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", re.IGNORECASE)
#: Marcas que tiene una línea con un mensaje; las demás (salidas de órdenes, a veces enormes) no se analizan.
_MARCAS = ('"UserMessage"', '"AgentMessage"', '"user_message"', '"agent_message"')


def carpeta_codex() -> Path:
	return Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex"


def indice_rollouts(carpeta: Path | None = None) -> dict[str, Path]:
	"""Archivo de cada conversación: id (en minúsculas) → ruta del ``rollout``."""
	carpeta = Path(carpeta) if carpeta is not None else carpeta_codex()
	indice: dict[str, Path] = {}
	for sub in ("sessions", "archived_sessions"):
		base = carpeta / sub
		if not base.is_dir():
			continue
		try:
			for ruta in base.rglob("rollout-*.jsonl"):
				coincidencia = _RE_ROLLOUT.match(ruta.name)
				if coincidencia:
					indice.setdefault(coincidencia.group(1).lower(), ruta)
		except OSError:
			log.debug("No se pudo recorrer %s", base, exc_info=True)
	return indice


def _textos(contenido: Any, union: str) -> str:
	if isinstance(contenido, str):
		return contenido
	partes = [str(p.get("text") or "") for p in contenido or [] if isinstance(p, dict) and str(p.get("type", "")).lower() == "text"]
	return union.join(p for p in partes if p)


def leer_mensajes(ruta: Path, cancelado: Callable[[], bool] | None = None) -> list[dict[str, Any]]:
	"""Mensajes de un ``rollout``: ``rol`` («usuario» o «asistente»), ``texto`` y ``fecha``."""
	nuevos: list[dict[str, Any]] = []
	antiguos: list[dict[str, Any]] = []
	try:
		with open(ruta, encoding="utf-8", errors="replace") as archivo:
			for numero, linea in enumerate(archivo):
				if numero % 2000 == 0 and cancelado is not None and cancelado():
					break
				if not any(marca in linea for marca in _MARCAS):
					continue
				try:
					evento = json.loads(linea)
				except ValueError:
					continue
				carga = evento.get("payload") if isinstance(evento, dict) else None
				if not isinstance(carga, dict) or evento.get("type") != "event_msg":
					continue
				momento = fecha(evento.get("timestamp"))
				tipo = carga.get("type")
				if tipo == "item_completed" and isinstance(carga.get("item"), dict):
					item = carga["item"]
					if item.get("type") == "UserMessage":
						nuevos.append({"rol": "usuario", "texto": _textos(item.get("content"), "\n"), "fecha": momento})
					elif item.get("type") == "AgentMessage":
						nuevos.append({"rol": "asistente", "texto": _textos(item.get("content"), ""), "fecha": momento})
				elif tipo in ("user_message", "agent_message"):
					rol = "usuario" if tipo == "user_message" else "asistente"
					antiguos.append({"rol": rol, "texto": str(carga.get("message") or ""), "fecha": momento})
	except OSError:
		log.debug("No se pudo leer %s", ruta, exc_info=True)
	# Las versiones nuevas pueden escribir los dos tipos de evento: se usa uno solo.
	return [m for m in (nuevos or antiguos) if m["texto"].strip()]


def buscar_en_hilos(hilos: list[str], coincide: Callable[[str], bool], maximo: int = 500,
		cancelado: Callable[[], bool] | None = None, carpeta: Path | None = None) -> list[tuple[str, dict[str, Any]]]:
	"""Mensajes de las conversaciones ``hilos`` (ids) que cumplen ``coincide``: ``(id, mensaje)``."""
	indice = indice_rollouts(carpeta)
	resultados: list[tuple[str, dict[str, Any]]] = []
	for identificador in hilos:
		if (cancelado is not None and cancelado()) or len(resultados) >= maximo:
			break
		ruta = indice.get(identificador.lower())
		if ruta is None:
			continue
		for mensaje in leer_mensajes(ruta, cancelado):
			if coincide(mensaje["texto"]):
				resultados.append((identificador, mensaje))
				if len(resultados) >= maximo:
					break
	return resultados
