"""Mensajes demasiado largos para Codex, entregados en un archivo.

Codex no admite mensajes de más de :data:`LIMITE` caracteres. En lugar de
rechazarlos, Ctrl+IA escribe el mensaje entero en un archivo de ``data/temp``
(``ctrlia-mensaje-largo-*.txt``, UTF-8 con BOM para que lo lea bien hasta el
``Get-Content`` de Windows PowerShell 5.1) y le envía a Codex un mensaje fijo
que dice que lo puso Ctrl+IA, cuántos caracteres tiene, su ruta en una línea
``ruta: <ruta>`` y que lo lea y lo trate como el mensaje de la persona.

- Nunca se escribe en ``%TEMP%``: la carpeta es la portable de Ctrl+IA.
- El archivo se borra al acabar el turno que lo leyó; lo que quede de un cierre
  inesperado lo borra :func:`limpiar_restos`, que se llama al arrancar (solo lo
  anterior a este arranque: uno recién escrito puede estar esperando a Codex).
- Lo que se guarda y se ve en Ctrl+IA es el texto original: este módulo solo
  cambia lo que viaja a Codex. Al leer el hilo desde Codex, el mensaje fijo se
  muestra como «(enviado como archivo, N caracteres)» (:func:`texto_visible`).
- Un texto solo se reconoce como uno de estos por el mensaje fijo exacto,
  al principio, y nombrando un archivo con el nombre de los de Ctrl+IA. Nunca se
  abre un archivo para rellenar un mensaje.

Sin wx.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path, PureWindowsPath

log = logging.getLogger(__name__)

#: Caracteres a partir de los cuales el mensaje va en un archivo.
LIMITE = 32000
PREFIJO = "ctrlia-mensaje-largo-"
SUFIJO = ".txt"
#: Cómo empieza siempre el mensaje fijo: reconocerlo exige que esté al principio.
APERTURA = "Este mensaje lo ha puesto Ctrl+IA en la conversación."
_RE_RUTA = re.compile(r"^ruta: (.+)$", re.MULTILINE)
_RE_CARACTERES = re.compile(r"\(([\d.]+) caracteres\)")
_RE_NOMBRE = re.compile(r"^" + re.escape(PREFIJO) + r"[A-Za-z0-9_]+" + re.escape(SUFIJO) + r"$")
#: Cuándo empezó este arranque (más o menos: el programa importa el módulo al arrancar).
_CARGADO = time.time()


def carpeta() -> Path:
	"""Dónde van los archivos: ``data/temp`` (las pruebas la redirigen con ``APP_DATA_DIR``)."""
	from app.core.paths import ruta_temporales

	return ruta_temporales()


def hace_falta(texto: str) -> bool:
	return isinstance(texto, str) and len(texto) > LIMITE


def _miles(numero: int) -> str:
	return f"{numero:,}".replace(",", ".")


def guardar(texto: str) -> Path:
	"""Escribe el mensaje entero en un archivo nuevo y devuelve su ruta. ``OSError`` si no se puede."""
	destino = carpeta()
	destino.mkdir(parents=True, exist_ok=True)
	descriptor, ruta = tempfile.mkstemp(prefix=PREFIJO, suffix=SUFIJO, dir=destino)
	try:
		with open(descriptor, "w", encoding="utf-8-sig", newline="") as archivo:
			archivo.write(texto)
	except BaseException:
		Path(ruta).unlink(missing_ok=True)
		raise
	return Path(ruta)


def mensaje_para(texto: str, ruta: Path | str) -> str:
	"""Lo que se envía a Codex en lugar de un mensaje demasiado largo (lo dice Ctrl+IA, no la persona)."""
	return (
		f"{APERTURA} El mensaje de la persona es demasiado largo para enviarlo directamente "
		f"({_miles(len(texto))} caracteres), así que se ha guardado en un archivo temporal que se borrará al "
		f"terminar la tarea.\nruta: {ruta}\n\n"
		"Lee ese archivo ahora y trata todo su contenido como el mensaje de la persona: haz lo que pide y, si "
		"muestra salidas o errores, explícalos. Si no puedes leer el archivo, dilo claramente y detente; no "
		"adivines lo que dice."
	)


def es_propio(ruta: Path | str, en_carpeta: bool = True) -> bool:
	"""Un archivo de este módulo: con su nombre y, con ``en_carpeta``, directamente en :func:`carpeta`."""
	try:
		candidato = Path(ruta)
		if not _RE_NOMBRE.match(PureWindowsPath(str(ruta)).name if "\\" in str(ruta) else candidato.name):
			return False
		if not en_carpeta:
			return True
		return os.path.normcase(str(candidato.parent)) == os.path.normcase(str(carpeta()))
	except (TypeError, ValueError, OSError):
		return False


def ruta_de(texto: str, en_carpeta: bool = False) -> str | None:
	"""La ruta que nombra un mensaje fijo de Ctrl+IA, o ``None`` para cualquier otro texto."""
	if not isinstance(texto, str) or not texto.startswith(APERTURA):
		return None
	coincidencia = _RE_RUTA.search(texto)
	if coincidencia is None:
		return None
	ruta = coincidencia.group(1).strip()
	return ruta if es_propio(ruta, en_carpeta) else None


def caracteres_de(texto: str) -> int | None:
	"""Los caracteres del mensaje original que dice un mensaje fijo de Ctrl+IA, o ``None``."""
	if ruta_de(texto) is None:
		return None
	coincidencia = _RE_CARACTERES.search(texto)
	if coincidencia is None:
		return None
	try:
		return int(coincidencia.group(1).replace(".", ""))
	except ValueError:
		return None


def texto_visible(texto: str) -> str:
	"""Cómo se enseña un mensaje de la persona leído desde Codex: el fijo, como «(enviado como archivo, N caracteres)»."""
	if ruta_de(texto) is None:
		return texto
	numero = caracteres_de(texto)
	if numero is None:
		return "(enviado como archivo)"
	return "(enviado como archivo, %s caracteres)" % _miles(numero)


def borrar(rutas: Iterable[Path | str]) -> None:
	"""Borra archivos de este módulo (solo los suyos, en su carpeta); los que ya no están, nada."""
	for ruta in rutas:
		if not es_propio(ruta):
			log.warning("No se borra %s: no es un mensaje largo de Ctrl+IA", ruta)
			continue
		try:
			Path(ruta).unlink(missing_ok=True)
		except OSError:
			log.debug("No se pudo borrar %s (se borrará al arrancar)", ruta, exc_info=True)


def limpiar_restos(antes: float | None = None) -> int:
	"""Borra los mensajes largos de arranques anteriores (sus turnos ya acabaron). Devuelve cuántos.

	Se llama una vez al arrancar, fuera del hilo de la interfaz. Solo borra lo anterior a ``antes``
	(de fábrica, cuando se cargó este módulo): uno recién escrito puede estar esperando a que Codex lo lea.
	"""
	limite = _CARGADO if antes is None else antes
	borrados = 0
	try:
		candidatos = list(carpeta().glob(PREFIJO + "*" + SUFIJO))
	except OSError:
		log.debug("No se pudo revisar la carpeta de temporales", exc_info=True)
		return 0
	for ruta in candidatos:
		try:
			if ruta.stat().st_mtime < limite and es_propio(ruta):
				ruta.unlink()
				borrados += 1
		except OSError:
			log.debug("No se pudo borrar %s", ruta, exc_info=True)
	if borrados:
		log.info("Borrados %d mensajes largos de Codex que quedaron de un arranque anterior", borrados)
	return borrados
