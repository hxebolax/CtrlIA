"""Historial de Codex en disco, para buscar en sus conversaciones (solo lectura).

La lista y la lectura de las conversaciones de Codex se hacen con su servidor
(``thread/list`` y ``thread/read``, en :mod:`app.services.agentes.codex`). Para
«Buscar en todas las sesiones», el informe de sesiones y restaurar desde una
copia eso sería una petición por conversación, así que aquí se lee lo que Codex
guarda en disco (en ``~/.codex`` o ``CODEX_HOME``):

- Sus bases SQLite (codex-cli 0.16x y la app de escritorio): ``state_<N>.sqlite``
  con la lista de hilos (tabla ``threads``) y ``thread_history_<N>.sqlite`` con
  cada elemento de cada turno (tabla ``thread_items``). Codex pasa ahí sus
  conversaciones y los ``rollout`` recientes ya no existen. El número cambia con
  las versiones: se buscan por patrón y se usa el más alto. Si ``sqlite_home`` o
  ``CODEX_SQLITE_HOME`` las llevan a otra carpeta, se buscan allí.
- Los ``rollout-<fecha>-<id>.jsonl`` antiguos de ``sessions`` y ``archived_sessions``.

Los hilos de subagentes (``source`` en JSON) no son conversaciones de la persona y
se dejan fuera. Solo se sacan los mensajes de la persona y las respuestas de
Codex. Las bases se abren con ``mode=ro`` y los archivos solo para leer: nunca se
escribe nada en esas carpetas.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.historial.comun import fecha, solo_lectura

log = logging.getLogger(__name__)

_RE_ROLLOUT = re.compile(r"^rollout-.*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", re.IGNORECASE)
#: Marcas que tiene una línea con un mensaje; las demás (salidas de órdenes, a veces enormes) no se analizan.
_MARCAS = ('"UserMessage"', '"AgentMessage"', '"user_message"', '"agent_message"')
#: Bases de Codex: el número es la versión de su formato y cambia entre versiones.
_RE_ESTADO = re.compile(r"^state_(\d+)\.sqlite$")
_RE_HISTORIA = re.compile(r"^thread_history_(\d+)\.sqlite$")


def carpeta_codex() -> Path:
	return Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex"


def carpeta_sqlite(casa: Path | None = None) -> Path:
	"""Dónde guarda Codex sus bases: ``sqlite_home`` de su ``config.toml``, ``CODEX_SQLITE_HOME`` o su carpeta."""
	casa = Path(casa) if casa is not None else carpeta_codex()
	try:
		ajustes = tomllib.loads((casa / "config.toml").read_text(encoding="utf-8"))
	except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
		ajustes = {}
	valor = ajustes.get("sqlite_home") if isinstance(ajustes, dict) else None
	base = casa
	if not valor:
		valor = os.environ.get("CODEX_SQLITE_HOME")
		base = Path.cwd()  # auditoria: ignorar getcwd - la documentación de Codex define así las rutas relativas de esa variable
	if not isinstance(valor, str) or not valor or "\x00" in valor:
		return casa
	ruta = Path(valor).expanduser()
	return ruta if ruta.is_absolute() else base / ruta


def _la_mas_nueva(carpeta: Path, patron: re.Pattern[str]) -> Path | None:
	mejor: tuple[int, Path] | None = None
	try:
		for ruta in carpeta.glob("*.sqlite"):
			coincidencia = patron.match(ruta.name)
			if coincidencia and ruta.is_file():
				numero = int(coincidencia.group(1))
				if mejor is None or numero > mejor[0]:
					mejor = (numero, ruta)
	except OSError:
		log.debug("No se pudo recorrer %s", carpeta, exc_info=True)
	return mejor[1] if mejor else None


def bases_codex(carpeta: Path | None = None) -> tuple[Path | None, Path | None]:
	"""``(state_N.sqlite, thread_history_N.sqlite)`` de Codex, o ``None`` en lo que no exista.

	``carpeta`` es la carpeta de Codex (de fábrica, ``~/.codex`` o ``CODEX_HOME``); si su
	``config.toml`` las lleva a otra, se buscan allí.
	"""
	donde = carpeta_sqlite(Path(carpeta) if carpeta is not None else None)
	return _la_mas_nueva(donde, _RE_ESTADO), _la_mas_nueva(donde, _RE_HISTORIA)


def _sin_prefijo_largo(ruta: str) -> str:
	"""Las carpetas de Codex en Windows llevan a veces ``\\\\?\\`` delante."""
	ruta = str(ruta or "")
	if ruta.startswith("\\\\?\\UNC\\"):
		return "\\\\" + ruta[8:]
	return ruta.removeprefix("\\\\?\\")


def _segundos(valor: Any) -> float:
	"""Segundos de una marca de Codex: segundos, milisegundos o texto ISO."""
	if isinstance(valor, int | float) and valor > 1e11:
		return float(valor) / 1000
	return fecha(valor)


def hilos_sqlite(base: Path | None = None, archivados: bool = False) -> list[dict[str, Any]]:
	"""Hilos de la persona según la base ``state`` de Codex, de más reciente a más antiguo.

	Cada uno: ``id``, ``titulo``, ``primer_mensaje``, ``carpeta``, ``actualizado``, ``creado``,
	``ruta`` (su ``rollout``, que puede no existir ya) y ``origen``. Sin subagentes.
	"""
	if base is None:
		base = bases_codex()[0]
	if base is None or not Path(base).is_file():
		return []
	try:
		with solo_lectura(Path(base)) as conexion:
			filas = conexion.execute(
				"SELECT id, title, first_user_message, cwd, updated_at, created_at, rollout_path, source FROM threads "
				"WHERE archived = ? AND source NOT LIKE '{%' ORDER BY updated_at DESC",
				(1 if archivados else 0,),
			).fetchall()
	except sqlite3.Error:
		log.info("No se pudo leer la lista de hilos de Codex en %s", base, exc_info=True)
		return []
	hilos = []
	for fila in filas:
		identificador = str(fila["id"] or "")
		if not identificador:
			continue
		primer = str(fila["first_user_message"] or "")
		hilos.append({
			"id": identificador,
			"titulo": " ".join(str(fila["title"] or "").split()) or " ".join(primer.split())[:120],
			"primer_mensaje": primer,
			"carpeta": _sin_prefijo_largo(fila["cwd"]),
			"actualizado": _segundos(fila["updated_at"]),
			"creado": _segundos(fila["created_at"]),
			"ruta": str(fila["rollout_path"] or ""),
			"origen": str(fila["source"] or ""),
		})
	return hilos


def _texto_usuario(item: dict[str, Any]) -> str:
	partes = item.get("content") if isinstance(item.get("content"), list) else []
	return "\n".join(str(p.get("text") or "") for p in partes if isinstance(p, dict) and p.get("type", "text") == "text" and p.get("text"))


def mensajes_sqlite(identificador: str, base: Path | None = None,
		cancelado: Callable[[], bool] | None = None) -> list[dict[str, Any]]:
	"""Mensajes de un hilo según la base ``thread_history`` de Codex: ``rol``, ``texto``, ``fecha`` y ``ancla``.

	``ancla`` es ``"<turnId>:<itemId>"``, la misma que da el adaptador en vivo.
	"""
	from app.services import mensajes_largos

	if base is None:
		base = bases_codex()[1]
	if base is None or not Path(base).is_file():
		return []
	mensajes: list[dict[str, Any]] = []
	try:
		with solo_lectura(Path(base)) as conexion:
			cursor = conexion.execute(
				"SELECT turn_id, item_id, item_type, item_json, created_at_ms FROM thread_items "
				"WHERE thread_id = ? AND item_type IN ('userMessage', 'agentMessage') ORDER BY rollout_ordinal",
				(identificador,),
			)
			for numero, fila in enumerate(cursor):
				if numero % 500 == 0 and cancelado is not None and cancelado():
					break
				try:
					item = json.loads(fila["item_json"])
				except (TypeError, ValueError):
					log.debug("Elemento ilegible en el historial de Codex (%s)", fila["item_id"])
					continue
				if not isinstance(item, dict):
					continue
				if fila["item_type"] == "userMessage":
					rol, texto = "usuario", mensajes_largos.texto_visible(_texto_usuario(item))
				else:
					rol, texto = "asistente", str(item.get("text") or "")
				if not texto.strip():
					continue
				momento = fila["created_at_ms"]
				mensajes.append({
					"rol": rol, "texto": texto, "fecha": momento / 1000 if isinstance(momento, int | float) else 0.0,
					"ancla": "%s:%s" % (fila["turn_id"], fila["item_id"]),
				})
	except sqlite3.Error:
		log.info("No se pudo leer el historial de Codex en %s", base, exc_info=True)
		return []
	return mensajes


def tamano_sqlite(identificador: str, base: Path | None = None) -> int | None:
	"""Bytes de un hilo en la base ``thread_history`` (suma de sus elementos); ``None`` si no está."""
	if base is None:
		base = bases_codex()[1]
	if base is None or not Path(base).is_file():
		return None
	try:
		with solo_lectura(Path(base)) as conexion:
			fila = conexion.execute(
				"SELECT COUNT(*) AS cuantos, COALESCE(SUM(LENGTH(item_json)), 0) AS bytes FROM thread_items WHERE thread_id = ?",
				(identificador,),
			).fetchone()
	except sqlite3.Error:
		log.debug("No se pudo medir el hilo %s de Codex", identificador, exc_info=True)
		return None
	return int(fila["bytes"]) if fila is not None and fila["cuantos"] else None


def ids_sqlite(base: Path | None = None) -> set[str]:
	"""Los hilos (en minúsculas) que tienen algún elemento en la base ``thread_history``."""
	if base is None:
		base = bases_codex()[1]
	if base is None or not Path(base).is_file():
		return set()
	try:
		with solo_lectura(Path(base)) as conexion:
			return {str(f[0]).lower() for f in conexion.execute("SELECT DISTINCT thread_id FROM thread_items")}
	except sqlite3.Error:
		log.debug("No se pudo leer la lista de hilos con historial de Codex", exc_info=True)
		return set()


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
	from app.services import mensajes_largos

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
						texto = mensajes_largos.texto_visible(_textos(item.get("content"), "\n"))
						nuevos.append({"rol": "usuario", "texto": texto, "fecha": momento})
					elif item.get("type") == "AgentMessage":
						nuevos.append({"rol": "asistente", "texto": _textos(item.get("content"), ""), "fecha": momento})
				elif tipo in ("user_message", "agent_message"):
					rol = "usuario" if tipo == "user_message" else "asistente"
					texto = str(carga.get("message") or "")
					antiguos.append({"rol": rol, "texto": mensajes_largos.texto_visible(texto) if rol == "usuario" else texto,
						"fecha": momento})
	except OSError:
		log.debug("No se pudo leer %s", ruta, exc_info=True)
	# Las versiones nuevas pueden escribir los dos tipos de evento: se usa uno solo.
	return [m for m in (nuevos or antiguos) if m["texto"].strip()]


def mensajes_hilo(identificador: str, cancelado: Callable[[], bool] | None = None, carpeta: Path | None = None,
		indice: dict[str, Path] | None = None) -> list[dict[str, Any]]:
	"""Mensajes de un hilo: primero de las bases SQLite y, si no está en ellas, de su ``rollout``."""
	_estado, historia = bases_codex(carpeta)
	mensajes = mensajes_sqlite(identificador, historia, cancelado) if historia is not None else []
	if mensajes:
		return mensajes
	if indice is None:
		indice = indice_rollouts(carpeta)
	ruta = indice.get(identificador.lower())
	return leer_mensajes(ruta, cancelado) if ruta is not None else []


def buscar_en_hilos(hilos: list[str], coincide: Callable[[str], bool], maximo: int = 500,
		cancelado: Callable[[], bool] | None = None, carpeta: Path | None = None) -> list[tuple[str, dict[str, Any]]]:
	"""Mensajes de las conversaciones ``hilos`` (ids) que cumplen ``coincide``: ``(id, mensaje)``.

	Mira las bases SQLite de Codex y, para lo que no esté en ellas, los ``rollout``.
	"""
	_estado, historia = bases_codex(carpeta)
	en_base = ids_sqlite(historia) if historia is not None else set()
	indice: dict[str, Path] | None = None
	resultados: list[tuple[str, dict[str, Any]]] = []
	for identificador in hilos:
		if (cancelado is not None and cancelado()) or len(resultados) >= maximo:
			break
		if identificador.lower() in en_base:
			mensajes = mensajes_sqlite(identificador, historia, cancelado)
		else:
			if indice is None:
				indice = indice_rollouts(carpeta)
			ruta = indice.get(identificador.lower())
			mensajes = leer_mensajes(ruta, cancelado) if ruta is not None else []
		for mensaje in mensajes:
			if coincide(mensaje["texto"]):
				resultados.append((identificador, mensaje))
				if len(resultados) >= maximo:
					break
	return resultados
