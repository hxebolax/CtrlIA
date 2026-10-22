"""Historial de Antigravity: conversaciones de la app de escritorio y de Antigravity CLI (solo lectura).

Antigravity guarda los mensajes en protobuf sin documentar (``conversations/*.pb``),
que no se decodifican. Lo que sí se puede leer:

- ``~/.gemini/antigravity``: la app de escritorio (``conversations`` y ``brain``),
  leída por :func:`app.services.agentes.antigravity.listar_conversaciones_escritorio`.
- ``~/.gemini/antigravity-cli``: Antigravity CLI (``agy``), con la misma estructura.
- ``conversation_summaries.db`` (SQLite, en las dos carpetas si existe): un resumen
  por conversación con su título, su carpeta de trabajo, el número de pasos y la
  última modificación. La columna ``app_data_dir`` dice de qué programa es.

Nunca se escribe nada en estas carpetas.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.services.historial.comun import fecha, solo_lectura

log = logging.getLogger(__name__)

ORIGEN_APP = "antigravity"
ORIGEN_CLI = "antigravity_cli"
_RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
#: Títulos que inventa la lectura de la app cuando no encuentra uno.
_TITULO_INVENTADO = "Conversación de Antigravity del "


def raiz_gemini() -> Path:
	return Path.home() / ".gemini"


def carpeta_de_uris(texto: Any) -> str:
	"""Primera carpeta local de ``workspace_uris`` (lista JSON de ``file:///...``), o ``""``."""
	try:
		uris = json.loads(texto) if isinstance(texto, str) else texto
	except ValueError:
		return ""
	for uri in uris if isinstance(uris, list) else []:
		if isinstance(uri, str) and uri.lower().startswith("file:"):
			ruta = urllib.parse.urlparse(uri).path
			carpeta = urllib.request.url2pathname(urllib.parse.unquote(ruta))
			return carpeta[:1].upper() + carpeta[1:] if carpeta[1:2] == ":" else carpeta
	return ""


def _varint(datos: bytes, posicion: int) -> tuple[int, int]:
	valor = desplazamiento = 0
	while True:
		octeto = datos[posicion]
		posicion += 1
		valor |= (octeto & 0x7F) << desplazamiento
		desplazamiento += 7
		if octeto < 0x80:
			return valor, posicion


def _campos(datos: bytes) -> dict[int, list[Any]]:
	"""Campos de un mensaje protobuf: número → valores (enteros o bytes). Lanza ``ValueError`` o ``IndexError``."""
	campos: dict[int, list[Any]] = {}
	posicion = 0
	while posicion < len(datos):
		clave, posicion = _varint(datos, posicion)
		numero, tipo = clave >> 3, clave & 7
		if tipo == 0:
			valor, posicion = _varint(datos, posicion)
		elif tipo == 2:
			largo, posicion = _varint(datos, posicion)
			valor, posicion = datos[posicion:posicion + largo], posicion + largo
		elif tipo in (1, 5):
			ancho = 8 if tipo == 1 else 4
			valor, posicion = datos[posicion:posicion + ancho], posicion + ancho
		else:
			raise ValueError("tipo de campo protobuf desconocido: %d" % tipo)
		campos.setdefault(numero, []).append(valor)
	return campos


def _texto(campos: dict[int, list[Any]], numero: int) -> str:
	valor = (campos.get(numero) or [b""])[0]
	return valor.decode("utf-8", "replace").strip() if isinstance(valor, bytes) else ""


def _entero(campos: dict[int, list[Any]], numero: int) -> int:
	valor = (campos.get(numero) or [0])[0]
	return valor if isinstance(valor, int) else 0


def leer_resumenes_app(ruta: Path) -> dict[str, dict[str, Any]]:
	"""Resúmenes de ``agyhub_summaries_proto.pb`` de la app (protobuf sin documentar).

	Campos observados de cada resumen: 1 título, 2 pasos, 3 última modificación
	(``Timestamp``) y 9 carpeta (``file:///...``). Si el formato cambia y no se
	entiende, se usa lo que se haya podido leer, sin fallar.
	"""
	try:
		datos = ruta.read_bytes()
	except OSError:
		return {}
	resumenes: dict[str, dict[str, Any]] = {}
	try:
		for entrada in _campos(datos).get(1, []):
			if not isinstance(entrada, bytes):
				continue
			campos = _campos(entrada)
			identificador = _texto(campos, 1).lower()
			resumen = (campos.get(2) or [b""])[0]
			if not _RE_UUID.match(identificador) or not isinstance(resumen, bytes):
				continue
			datos_resumen = _campos(resumen)
			marca = (datos_resumen.get(3) or [b""])[0]
			carpeta = (datos_resumen.get(9) or [b""])[0]
			resumenes[identificador] = {
				"titulo": _texto(datos_resumen, 1),
				"carpeta": carpeta_de_uris([_texto(_campos(carpeta), 1)]) if isinstance(carpeta, bytes) and carpeta else "",
				"pasos": _entero(datos_resumen, 2),
				"fecha": float(_entero(_campos(marca), 1)) if isinstance(marca, bytes) and marca else 0.0,
				"programa": "antigravity",
			}
	except (ValueError, IndexError):
		log.warning("No se entiende del todo %s; se usa lo leído", ruta, exc_info=True)
	return resumenes


def leer_resumenes(raiz: Path | None = None) -> dict[str, dict[str, Any]]:
	"""Resúmenes de las conversaciones: id → ``titulo``, ``carpeta``, ``pasos``, ``fecha`` y ``programa``.

	Salen de ``agyhub_summaries_proto.pb`` (app) y de ``conversation_summaries.db``
	(app y CLI); si una conversación está en varios, gana el más reciente.
	"""
	raiz = Path(raiz) if raiz is not None else raiz_gemini()
	resumenes = leer_resumenes_app(raiz / "antigravity" / "agyhub_summaries_proto.pb")
	for subcarpeta in ("antigravity", "antigravity-cli"):
		ruta = raiz / subcarpeta / "conversation_summaries.db"
		if not ruta.is_file():
			continue
		try:
			with solo_lectura(ruta) as conexion:
				filas = conexion.execute(
					"SELECT conversation_id, title, preview, step_count, last_modified_time, workspace_uris, app_data_dir "
					"FROM conversation_summaries").fetchall()
		except sqlite3.Error:
			log.warning("No se pudo leer %s", ruta, exc_info=True)
			continue
		for fila in filas:
			identificador = str(fila["conversation_id"] or "").lower()
			if not _RE_UUID.match(identificador):
				continue
			datos = {
				"titulo": str(fila["title"] or fila["preview"] or "").strip(),
				"carpeta": carpeta_de_uris(fila["workspace_uris"]),
				"pasos": int(fila["step_count"] or 0),
				"fecha": fecha(fila["last_modified_time"]),
				"programa": str(fila["app_data_dir"] or subcarpeta),
			}
			anterior = resumenes.get(identificador)
			if anterior is None or datos["fecha"] >= anterior["fecha"]:
				resumenes[identificador] = datos
	return resumenes


def listar_conversaciones(raiz: Path | None = None, propios: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
	"""Conversaciones de la app y de Antigravity CLI, de la más reciente a la más antigua.

	``propios`` son los ids de las conversaciones que abrió Ctrl+IA (ya están en
	la lista como sesiones propias). Cada elemento: ``id``, ``titulo``, ``carpeta``,
	``fecha`` (``datetime``), ``origen`` (:data:`ORIGEN_APP` o :data:`ORIGEN_CLI`),
	``pasos`` y ``ruta_brain``.
	"""
	from app.services.agentes.antigravity import listar_conversaciones_escritorio

	raiz = Path(raiz) if raiz is not None else raiz_gemini()
	resumenes = leer_resumenes(raiz)
	resultado: dict[str, dict[str, Any]] = {}
	for origen, subcarpeta in ((ORIGEN_APP, "antigravity"), (ORIGEN_CLI, "antigravity-cli")):
		for conversacion in listar_conversaciones_escritorio(raiz / subcarpeta):
			identificador = conversacion["id"]
			if identificador in propios or identificador in resultado:
				continue
			resumen = resumenes.get(identificador, {})
			titulo = conversacion.get("titulo") or ""
			if resumen.get("titulo") and (not titulo or titulo.startswith(_TITULO_INVENTADO)):
				titulo = resumen["titulo"]
			# La fecha del resumen es la de verdad: copiar la carpeta (al migrar o restaurar) cambia la de los archivos.
			marca = float(resumen.get("fecha") or 0) or conversacion["fecha"].timestamp()
			resultado[identificador] = {
				**conversacion,
				"titulo": titulo or _TITULO_INVENTADO.rstrip(),
				"carpeta": resumen.get("carpeta", ""),
				"pasos": resumen.get("pasos", 0),
				"fecha": conversacion["fecha"].fromtimestamp(marca),
				"origen": origen,
			}
	return sorted(resultado.values(), key=lambda c: c["fecha"], reverse=True)


def se_puede_retomar(identificador: str, raiz: Path | None = None) -> bool:
	"""``True`` si Antigravity CLI tiene la conversación (``agy --conversation`` la retoma).

	Si no la tiene, agy no falla: empieza otra conversación en silencio. Por eso solo
	se ofrece retomar las que están en su carpeta.
	"""
	if not _RE_UUID.match(identificador or ""):
		return False
	carpeta = (Path(raiz) if raiz is not None else raiz_gemini()) / "antigravity-cli" / "conversations"
	return any((carpeta / f"{identificador}{extension}").is_file() for extension in (".db", ".pb"))


def texto_de_aviso(conversacion: dict[str, Any]) -> str:
	"""Explicación para la lista de mensajes de una conversación que no se puede leer."""
	if conversacion.get("origen") == ORIGEN_CLI:
		texto = ("Esta conversación es de Antigravity CLI, que guarda los mensajes en un formato que otros programas "
			"no pueden leer. Para seguirla en Ctrl+IA, pulsa Control+Mayúsculas+N (Continuar aquí): Antigravity "
			"recuerda toda la conversación.")
	else:
		texto = ("Esta conversación pertenece a la app de escritorio de Antigravity, que no permite leerla desde "
			"otros programas. Pulsa Control+O para abrir la carpeta en Antigravity y continuarla allí.")
	datos = []
	if conversacion.get("carpeta"):
		datos.append("Carpeta: %s." % conversacion["carpeta"])
	if conversacion.get("pasos"):
		datos.append("Pasos del agente: %d." % conversacion["pasos"])
	return texto + (" " + " ".join(datos) if datos else "")
