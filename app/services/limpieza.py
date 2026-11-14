"""Restos que una sesión anterior de Ctrl+IA pudo dejar en ``data/temp`` (AIChat #494).

Restaurar o comprobar una copia de seguridad saca archivos a ``data/temp``
(``copia-abierta-*``, ``copia-*``, ``restaurar-*``) y los borra al terminar;
si Ctrl+IA se cerró a medias, se quedaban ahí, y pueden ser chats descifrados.
Lo mismo con los mensajes largos de Codex (``ctrlia-mensaje-largo-*``). Se
borran al arrancar, en un hilo de prioridad baja, sin retrasar la ventana.

Solo dentro de ``data/temp``, solo lo que empieza por esos prefijos y sin
seguir enlaces ni junctions: nunca se borra nada fuera de la carpeta.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Prefijos de lo que Ctrl+IA crea en ``data/temp`` y borra él mismo al terminar.
PREFIJOS = ("copia-abierta-", "copia-", "restaurar-", "ctrlia-mensaje-largo-")
#: Lo más reciente se deja (podría estar usándolo otra copia que arranca a la vez): minutos.
MINUTOS_MINIMOS = 10


def _ruta_larga(ruta: Path) -> str:
	texto = str(ruta)
	if os.name == "nt" and not texto.startswith("\\\\?\\"):
		return "\\\\?\\" + texto
	return texto


def _quitar_solo_lectura(funcion, ruta, _excepcion) -> None:
	"""Un archivo de solo lectura (los de ``.git``) se deja escribir y se reintenta."""
	try:
		os.chmod(ruta, stat.S_IWRITE)
		funcion(ruta)
	except OSError:
		log.debug("No se pudo borrar %s", ruta, exc_info=True)


def _borrar(ruta: Path) -> bool:
	if ruta.is_symlink():
		ruta.unlink(missing_ok=True)  # el enlace, nunca lo que señala
		return True
	if _es_junction(ruta):
		os.rmdir(ruta)  # una junction se quita con rmdir, sin entrar
		return True
	if ruta.is_dir():
		for _intento in range(3):
			shutil.rmtree(_ruta_larga(ruta), onexc=_quitar_solo_lectura)
			if not ruta.exists():
				return True
			time.sleep(0.2)  # Windows: «la carpeta no está vacía» mientras un antivirus la mira
		return False
	ruta.unlink(missing_ok=True)
	return True


def _es_junction(ruta: Path) -> bool:
	try:
		return bool(getattr(ruta, "is_junction", lambda: False)())
	except OSError:
		return False


def restos(carpeta: Path, ahora: float | None = None) -> list[Path]:
	"""Lo que se puede borrar de ``carpeta`` (``data/temp``)."""
	if not carpeta.is_dir():
		return []
	ahora = time.time() if ahora is None else ahora
	encontrados = []
	for entrada in carpeta.iterdir():
		if not entrada.name.startswith(PREFIJOS):
			continue
		try:
			if ahora - entrada.lstat().st_mtime < MINUTOS_MINIMOS * 60:
				continue
		except OSError:
			log.debug("No se pudo mirar la fecha de %s; se deja", entrada, exc_info=True)
			continue
		encontrados.append(entrada)
	return encontrados


def limpiar(carpeta: Path | None = None) -> int:
	"""Borra los restos y devuelve cuántos ha quitado. Nunca lanza."""
	try:
		if carpeta is None:
			from app.core.paths import ruta_temporales

			carpeta = ruta_temporales()
		quitados = 0
		for ruta in restos(carpeta):
			try:
				if _borrar(ruta):
					quitados += 1
			except OSError:
				log.info("No se pudo borrar el resto %s", ruta, exc_info=True)
		if quitados:
			log.info("Borrados %d restos de sesiones anteriores en %s", quitados, carpeta)
		return quitados
	except Exception:  # noqa: BLE001 - limpiar nunca debe impedir arrancar
		log.warning("No se pudieron limpiar los restos de data/temp", exc_info=True)
		return 0


def limpiar_en_segundo_plano() -> threading.Thread:
	hilo = threading.Thread(target=limpiar, name="limpieza-temp", daemon=True)
	hilo.start()
	return hilo
