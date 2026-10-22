"""Historial de la app de escritorio de Ollama (solo lectura).

La app de Ollama para Windows guarda sus chats en ``%LOCALAPPDATA%\\Ollama\\db.sqlite``
(tablas ``chats`` y ``messages``, en texto). Se leen sin escribir nunca en la base.
Un chat se puede continuar en Ctrl+IA: Ollama no recuerda nada entre peticiones,
así que basta con enviarle el historial (ver :func:`historial_para_ollama`).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.historial.comun import fecha, solo_lectura

log = logging.getLogger(__name__)

ORIGEN = "ollama_app"
#: Prefijo del identificador de un chat de la app (``ollama:app-<id>`` en la lista de sesiones).
PREFIJO = "app-"
_RE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_ROLES = {"user": "usuario", "assistant": "asistente", "system": "sistema", "tool": "herramienta"}


def ruta_base() -> Path:
	local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")  # auditoria: ignorar ruta-no-portable - lee los chats de la app de Ollama; no guarda datos
	return local / "Ollama" / "db.sqlite"


def id_valido(identificador: str) -> bool:
	return bool(_RE_ID.match(identificador or ""))


def listar_chats(ruta: Path | None = None) -> list[dict[str, Any]]:
	"""Chats de la app, del más reciente al más antiguo: ``id``, ``titulo``, ``creada``, ``actividad``, ``modelo``, ``ultimo``."""
	ruta = Path(ruta) if ruta is not None else ruta_base()
	if not ruta.is_file():
		return []
	try:
		with solo_lectura(ruta) as conexion:
			chats = conexion.execute("SELECT id, title, created_at FROM chats").fetchall()
			mensajes = conexion.execute(
				"SELECT chat_id, role, content, model_name, created_at, updated_at FROM messages ORDER BY id").fetchall()
	except sqlite3.Error:
		log.warning("No se pudieron leer los chats de la app de Ollama", exc_info=True)
		return []
	por_chat: dict[str, list[sqlite3.Row]] = {}
	for mensaje in mensajes:
		por_chat.setdefault(str(mensaje["chat_id"]), []).append(mensaje)
	resultado = []
	for chat in chats:
		identificador = str(chat["id"] or "")
		if not id_valido(identificador):
			continue
		suyos = por_chat.get(identificador, [])
		visibles = [m for m in suyos if m["role"] in ("user", "assistant") and (m["content"] or "").strip()]
		if not visibles:
			continue
		titulo = str(chat["title"] or "").strip()
		if not titulo:
			primero = next((m["content"] for m in visibles if m["role"] == "user"), "")
			titulo = " ".join(str(primero).split())[:80] or "Chat de Ollama"
		creada = fecha(chat["created_at"])
		resultado.append({
			"id": identificador,
			"titulo": titulo,
			"creada": creada,
			"actividad": max([creada, *(fecha(m["updated_at"]) or fecha(m["created_at"]) for m in suyos)]),
			"modelo": next((str(m["model_name"]) for m in reversed(suyos) if m["model_name"]), ""),
			"ultimo": str(visibles[-1]["content"]),
		})
	resultado.sort(key=lambda c: c["actividad"], reverse=True)
	return resultado


def leer_mensajes(identificador: str, ruta: Path | None = None) -> list[dict[str, Any]]:
	"""Mensajes de un chat: ``rol`` (como en Ctrl+IA), ``texto``, ``fecha``, ``razonamiento`` y ``modelo``."""
	ruta = Path(ruta) if ruta is not None else ruta_base()
	if not id_valido(identificador) or not ruta.is_file():
		return []
	try:
		with solo_lectura(ruta) as conexion:
			filas = conexion.execute(
				"SELECT role, content, thinking, model_name, created_at, tool_result FROM messages "
				"WHERE chat_id = ? ORDER BY id", (identificador,)).fetchall()
	except sqlite3.Error:
		log.warning("No se pudo leer el chat %s de la app de Ollama", identificador, exc_info=True)
		return []
	resultado = []
	for fila in filas:
		rol = _ROLES.get(str(fila["role"] or ""))
		texto = str(fila["content"] or "")
		if rol == "herramienta":
			texto = texto or str(fila["tool_result"] or "")
		if rol is None or not texto.strip():
			continue
		resultado.append({"rol": rol, "texto": texto, "fecha": fecha(fila["created_at"]),
			"razonamiento": str(fila["thinking"] or ""), "modelo": str(fila["model_name"] or "")})
	return resultado


def historial_para_ollama(identificador: str, ruta: Path | None = None) -> list[dict[str, str]]:
	"""El chat como historial de la API de Ollama (``role`` y ``content``), para continuarlo."""
	inverso = {"usuario": "user", "asistente": "assistant", "sistema": "system"}
	return [{"role": inverso[m["rol"]], "content": m["texto"]} for m in leer_mensajes(identificador, ruta) if m["rol"] in inverso]


def buscar(chats: dict[str, str], coincide: Callable[[str], bool], maximo: int = 500,
		cancelado: Callable[[], bool] | None = None, ruta: Path | None = None) -> list[tuple[str, dict[str, Any]]]:
	"""Mensajes de los chats ``chats`` (id → título) que cumplen ``coincide``: ``(id, mensaje)``."""
	resultados: list[tuple[str, dict[str, Any]]] = []
	for identificador in chats:
		if (cancelado is not None and cancelado()) or len(resultados) >= maximo:
			break
		for mensaje in leer_mensajes(identificador, ruta):
			if coincide(mensaje["texto"]):
				resultados.append((identificador, mensaje))
				if len(resultados) >= maximo:
					break
	return resultados
