"""Descarga de archivos robusta: reanudable, verificada, cancelable y atómica.

Por qué existe: una descarga ingenua (``open(destino, "wb").write(r.content)``)
carga el archivo entero en memoria, deja un archivo a medias con el nombre
definitivo si se corta, no se puede cancelar ni reanudar y confía en el nombre
que manda el servidor (que puede traer ``..\\..\\`` o llamarse ``CON``).

``descargar(url, destino, ...)``:

1. Escribe en streaming en ``<nombre>.parcial`` junto al destino (mismo disco:
   así ``os.replace`` es atómico) o en la carpeta temporal que se indique.
2. Si ya hay un ``.parcial`` y ``reanudar=True``, pide el resto con ``Range``
   (e ``If-Range`` con el ETag o la fecha guardados). Si el servidor contesta
   206, continúa; si contesta 200 (no admite rangos o el archivo cambió),
   empieza de cero sin mezclar datos.
3. Comprueba el tamaño anunciado y, si se indica, el SHA-256.
4. Solo entonces renombra de forma atómica (``os.replace``). Nunca queda un
   archivo incompleto con el nombre definitivo.
5. Informa del progreso como mucho N veces por segundo y se cancela con un
   ``threading.Event``, un token con ``cancelado`` o una función.

``nombre_seguro(nombre)`` y ``nombre_desde_respuesta(respuesta)`` convierten lo que
diga el servidor en un nombre válido y sin trampas para Windows.

PLANTILLA del agente «red-http».
"""

from __future__ import annotations

import builtins
import email.message
import hashlib
import json
import logging
import os
import re
import shutil
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import requests

from app.core.red import ErrorRed, comprobar_respuesta, redactar_url, sesion_de_hilo

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	"""Traduce con el ``_`` de gettext si la aplicación lo instaló en builtins."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


SUFIJO_PARCIAL = ".parcial"
SUFIJO_METADATOS = ".parcial.json"
TAMANO_BLOQUE = 64 * 1024


class ErrorDescarga(ErrorRed):
	"""Fallo de una descarga con mensaje para el usuario (ver ``ErrorRed``)."""


class DescargaCancelada(ErrorDescarga):
	"""El usuario (o el cierre de la aplicación) canceló la descarga."""

	def __init__(self, conservado: bool) -> None:
		super().__init__(_("Descarga cancelada."), categoria="cancelado")
		self.parcial_conservado = conservado


class ErrorIntegridad(ErrorDescarga):
	"""El archivo descargado no coincide con el tamaño o la suma SHA-256 esperados."""


class _EmpezarDeCero(Exception):
	"""Señal interna: el parcial no sirve y hay que pedir el archivo desde el principio."""


@dataclass(frozen=True)
class ResultadoDescarga:
	"""Qué se descargó y cómo."""

	ruta: Path
	tamano: int
	sha256: str
	reanudada: bool
	segundos: float


# ════════════════════════════════════════════════════════════════════════════
# Nombres de archivo seguros
# ════════════════════════════════════════════════════════════════════════════

_PROHIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Nombres de dispositivo de Windows: no se pueden usar ni con extensión («NUL.txt»).
_RESERVADOS = frozenset(
	{"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
	| {f"COM{i}" for i in "123456789¹²³"}
	| {f"LPT{i}" for i in "123456789¹²³"}
)


def _longitud_utf16(texto: str) -> int:
	"""NTFS limita cada componente a 255 unidades UTF-16 (un emoji ocupa dos)."""
	return len(texto.encode("utf-16-le")) // 2


def nombre_seguro(nombre: str | None, respaldo: str = "descarga", max_longitud: int = 150) -> str:
	"""Convierte un nombre cualquiera en un nombre de archivo válido y sin trampas en Windows.

	- Se queda solo con la última parte (nada de ``..\\..\\`` ni rutas absolutas).
	- Quita caracteres de control y sustituye ``<>:"/\\|?*`` por ``_``.
	- Quita puntos y espacios del final (Windows los elimina y confunde el nombre).
	- Antepone ``_`` a los nombres de dispositivo (``CON``, ``NUL``, ``COM1``...).
	- Recorta a ``max_longitud`` (en unidades UTF-16) conservando la extensión;
	  150 deja margen para la carpeta dentro del límite clásico de 260 de las rutas.
	"""
	texto = unicodedata.normalize("NFC", str(nombre or ""))
	texto = texto.replace("\\", "/").rsplit("/", 1)[-1]
	texto = "".join(c for c in texto if unicodedata.category(c) not in ("Cc", "Cf"))
	texto = _PROHIBIDOS.sub("_", texto)
	texto = re.sub(r"\s+", " ", texto).strip().rstrip(". ")
	if not texto or set(texto) <= {".", "_", " "}:
		texto = respaldo
	base, punto, extension = texto.rpartition(".")
	if not punto or not base or len(extension) > 16 or " " in extension:
		base, extension = texto, ""
	if base.split(".")[0].strip().upper() in _RESERVADOS:
		base = "_" + base
	sufijo = f".{extension}" if extension else ""
	while _longitud_utf16(base + sufijo) > max_longitud and len(base) > 1:
		base = base[:-1]
	return (base.rstrip(". ") or respaldo) + sufijo


def nombre_desde_respuesta(respuesta: requests.Response, respaldo: str = "descarga") -> str:
	"""Nombre de archivo según ``Content-Disposition`` (RFC 6266) o, si no, la URL.

	``filename*=UTF-8''...`` tiene prioridad sobre ``filename=``. El resultado
	siempre pasa por ``nombre_seguro``.
	"""
	cabecera = respuesta.headers.get("Content-Disposition", "")
	if cabecera:
		mensaje = email.message.Message()
		mensaje["Content-Disposition"] = cabecera
		propuesto = mensaje.get_filename()
		if propuesto:
			return nombre_seguro(propuesto, respaldo)
	ruta = urlsplit(respuesta.url or "").path
	return nombre_seguro(unquote(ruta.rsplit("/", 1)[-1]), respaldo)


def ruta_libre(ruta: Path) -> Path:
	"""``ruta`` si no existe; si existe, «nombre (1).ext», «nombre (2).ext»..."""
	if not ruta.exists():
		return ruta
	for numero in range(1, 10_000):
		candidata = ruta.with_name(f"{ruta.stem} ({numero}){ruta.suffix}")
		if not candidata.exists():
			return candidata
	raise ErrorDescarga(_("No se ha encontrado un nombre libre para el archivo."), categoria="local")


# ════════════════════════════════════════════════════════════════════════════
# Descarga
# ════════════════════════════════════════════════════════════════════════════

def _esta_cancelado(cancelacion: Any) -> bool:
	"""Admite ``threading.Event``, cualquier objeto con ``cancelado`` o una función."""
	if cancelacion is None:
		return False
	if hasattr(cancelacion, "cancelado"):
		return bool(cancelacion.cancelado)
	if hasattr(cancelacion, "is_set"):
		return bool(cancelacion.is_set())
	return bool(cancelacion())


class _Progreso:
	"""Limita las llamadas de progreso a ``por_segundo`` (la última siempre llega)."""

	def __init__(self, funcion: Callable[[int, int | None], Any] | None, por_segundo: float) -> None:
		self._funcion = funcion
		self._intervalo = 1.0 / por_segundo if por_segundo > 0 else 0.0
		self._ultima = 0.0
		self.llamadas = 0

	def __call__(self, hecho: int, total: int | None, final: bool = False) -> None:
		if self._funcion is None:
			return
		ahora = time.monotonic()
		if not final and ahora - self._ultima < self._intervalo:
			return
		self._ultima = ahora
		self.llamadas += 1
		self._funcion(hecho, total)


def _leer_metadatos(ruta: Path) -> dict:
	try:
		return json.loads(ruta.read_text(encoding="utf-8"))
	except FileNotFoundError:
		return {}
	except (OSError, ValueError):
		log.debug("Metadatos de reanudación ilegibles en %s; se ignoran.", ruta, exc_info=True)
		return {}


def _borrar(*rutas: Path) -> None:
	for ruta in rutas:
		try:
			ruta.unlink(missing_ok=True)
		except OSError:
			log.warning("No se pudo borrar %s.", ruta, exc_info=True)


def _total_desde_content_range(valor: str | None) -> tuple[int | None, int | None]:
	"""De ``bytes 100-199/1000`` devuelve (100, 1000); de ``bytes */1000``, (None, 1000)."""
	if not valor:
		return None, None
	coincidencia = re.match(r"\s*bytes\s+(?:(\d+)-\d+|\*)/(\d+|\*)", valor)
	if not coincidencia:
		return None, None
	inicio = int(coincidencia.group(1)) if coincidencia.group(1) else None
	total = int(coincidencia.group(2)) if coincidencia.group(2) != "*" else None
	return inicio, total


def _mover(origen: Path, destino: Path) -> None:
	"""Renombrado atómico; si están en discos distintos, copia y renombra en el destino."""
	try:
		os.replace(origen, destino)
	except OSError as error:
		# 18 = EXDEV en POSIX; 17 = ERROR_NOT_SAME_DEVICE en Windows.
		if getattr(error, "winerror", None) != 17 and error.errno != 18:
			raise
		temporal = destino.with_name(destino.name + SUFIJO_PARCIAL + ".mover")
		shutil.copyfile(origen, temporal)
		os.replace(temporal, destino)
		_borrar(origen)


def descargar(
	url: str,
	destino: str | os.PathLike[str],
	sha256: str | None = None,
	reanudar: bool = True,
	al_progresar: Callable[[int, int | None], Any] | None = None,
	cancelacion: Any = None,
	*,
	sesion: requests.Session | None = None,
	carpeta_temporal: str | os.PathLike[str] | None = None,
	tamano_maximo: int | None = None,
	conservar_parcial: bool = True,
	sobrescribir: bool = False,
	progresos_por_segundo: float = 4.0,
	tamano_bloque: int = TAMANO_BLOQUE,
	permitir_http_tras_https: bool = False,
) -> ResultadoDescarga:
	"""Descarga ``url`` en ``destino`` de forma segura. Llamar SIEMPRE fuera del hilo de la interfaz.

	:param destino: ruta del archivo, o una carpeta existente (el nombre sale de la
		respuesta con ``nombre_desde_respuesta``).
	:param sha256: suma esperada en hexadecimal; si no coincide, se borra el parcial
		y se lanza ``ErrorIntegridad``.
	:param al_progresar: ``funcion(bytes_hechos, bytes_totales_o_None)``, limitada a
		``progresos_por_segundo`` llamadas (en el hilo de la descarga: pásala por
		``wx.CallAfter`` o el gestor de tareas para tocar la interfaz).
	:param cancelacion: ``threading.Event``, token con ``cancelado`` o función.
	:param tamano_maximo: bytes máximos aceptados (protege de respuestas enormes).
	:param conservar_parcial: al cancelar, conservar el ``.parcial`` para reanudar.
	:param sobrescribir: si el destino existe y es ``False``, se usa «nombre (1).ext».
	:raises DescargaCancelada, ErrorIntegridad, ErrorDescarga, ErrorRed: con
		``mensaje_usuario`` en español.
	"""
	sesion = sesion or sesion_de_hilo()
	destino = Path(destino)
	inicio = time.monotonic()
	progreso = _Progreso(al_progresar, progresos_por_segundo)
	url_segura = redactar_url(url)

	# Si el destino es una carpeta, el nombre depende de la respuesta; el parcial usa
	# un nombre provisional estable derivado de la URL para poder reanudar.
	es_carpeta = destino.is_dir()
	provisional = nombre_seguro(unquote(urlsplit(url).path.rsplit("/", 1)[-1]))
	base_parcial = Path(carpeta_temporal) if carpeta_temporal else (destino if es_carpeta else destino.parent)
	base_parcial.mkdir(parents=True, exist_ok=True)
	parcial = base_parcial / ((provisional if es_carpeta else destino.name) + SUFIJO_PARCIAL)
	metadatos_ruta = parcial.with_name(parcial.name[: -len(SUFIJO_PARCIAL)] + SUFIJO_METADATOS)

	for _intento in range(2):  # el segundo solo si el parcial no sirve y hay que empezar de cero
		desplazamiento = parcial.stat().st_size if (reanudar and parcial.exists()) else 0
		metadatos = _leer_metadatos(metadatos_ruta) if desplazamiento else {}
		cabeceras = {"Accept-Encoding": "identity"}  # tamaños y rangos sobre los bytes reales
		if desplazamiento:
			cabeceras["Range"] = f"bytes={desplazamiento}-"
			validador = metadatos.get("etag") or metadatos.get("last_modified")
			if validador:
				cabeceras["If-Range"] = validador
		respuesta = sesion.get(url, headers=cabeceras, stream=True)
		try:
			if respuesta.url.startswith("http://") and url.startswith("https://") and not permitir_http_tras_https:
				raise ErrorDescarga(
					_("El servidor ha redirigido la descarga a una conexión no cifrada; se ha detenido por seguridad."),
					categoria="seguridad", detalle=f"https → http en {url_segura}",
				)
			if respuesta.status_code == 416 and desplazamiento:
				_inicio, total_servidor = _total_desde_content_range(respuesta.headers.get("Content-Range"))
				if total_servidor == desplazamiento:
					log.info("El archivo parcial de %s ya estaba completo.", url_segura)
					total, anadir, reanudada = desplazamiento, None, True
				else:
					log.info("El servidor rechazó el rango pedido para %s; se empieza de cero.", url_segura)
					_borrar(parcial, metadatos_ruta)
					continue
			else:
				comprobar_respuesta(respuesta)
				try:
					total, anadir = _preparar_escritura(respuesta, desplazamiento, url_segura)
					reanudada = anadir
				except _EmpezarDeCero:
					_borrar(parcial, metadatos_ruta)
					continue
			if tamano_maximo is not None and total is not None and total > tamano_maximo:
				raise ErrorDescarga(
					_("El archivo es demasiado grande ({megas:.0f} MB).").format(megas=total / 1_048_576),
					categoria="cliente", detalle=f"{total} bytes > máximo {tamano_maximo} en {url_segura}",
				)
			if es_carpeta:
				final = destino / nombre_desde_respuesta(respuesta, provisional)
			else:
				final = destino
			if anadir is not None:
				_guardar_metadatos(metadatos_ruta, respuesta, total)
				hecho = _escribir(
					respuesta, parcial, anadir, total, tamano_maximo, progreso, cancelacion,
					conservar_parcial, metadatos_ruta, tamano_bloque,
				)
			else:
				hecho = desplazamiento
		finally:
			respuesta.close()
		break
	else:  # pragma: no cover - solo si el servidor rechaza el parcial dos veces seguidas
		raise ErrorDescarga(_("El servidor no permite descargar este archivo."), categoria="servidor")

	if total is not None and hecho != total:
		raise ErrorDescarga(
			_("La descarga se ha cortado antes de terminar. Vuelve a intentarlo para continuar donde se quedó."),
			categoria="red", reintentar=True, detalle=f"{hecho} de {total} bytes en {url_segura}",
		)
	suma = _sha256_de(parcial)
	if sha256 and suma.lower() != sha256.strip().lower():
		_borrar(parcial, metadatos_ruta)
		raise ErrorIntegridad(
			_("El archivo descargado está dañado (su suma de comprobación no coincide) y se ha descartado. "
				"Vuelve a intentarlo."),
			categoria="servidor", reintentar=True, detalle=f"SHA-256 {suma} ≠ {sha256} en {url_segura}",
		)
	final.parent.mkdir(parents=True, exist_ok=True)
	if not sobrescribir:
		final = ruta_libre(final)
	_mover(parcial, final)
	_borrar(metadatos_ruta)
	progreso(hecho, total, final=True)
	resultado = ResultadoDescarga(final, hecho, suma, reanudada, time.monotonic() - inicio)
	log.info(
		"Descargado %s → %s (%d bytes en %.1f s%s).", url_segura, final.name, hecho, resultado.segundos,
		", reanudada" if resultado.reanudada else "",
	)
	return resultado


def _preparar_escritura(respuesta: requests.Response, desplazamiento: int, url_segura: str) -> tuple[int | None, bool]:
	"""Decide si se continúa (206) o se empieza de cero (200). Devuelve (total, añadir)."""
	longitud = respuesta.headers.get("Content-Length")
	codificado = respuesta.headers.get("Content-Encoding", "identity").lower() not in ("", "identity")
	if respuesta.status_code == 206 and desplazamiento:
		inicio, total = _total_desde_content_range(respuesta.headers.get("Content-Range"))
		if inicio == desplazamiento:
			log.info("Reanudando %s desde el byte %d.", url_segura, desplazamiento)
			return total, True
		log.warning("Rango inesperado (%s) para %s; se empieza de cero.", respuesta.headers.get("Content-Range"), url_segura)
		raise _EmpezarDeCero
	if desplazamiento:
		log.info("El servidor no admite continuar %s (o el archivo cambió): se descarga desde el principio.", url_segura)
	# Con compresión, Content-Length mide los bytes comprimidos: no sirve para comprobar.
	total = int(longitud) if longitud and longitud.isdigit() and not codificado else None
	return total, False


def _guardar_metadatos(ruta: Path, respuesta: requests.Response, total: int | None) -> None:
	"""Guarda ETag/Last-Modified para que la reanudación detecte si el archivo cambió."""
	datos = {
		"etag": respuesta.headers.get("ETag") if not (respuesta.headers.get("ETag") or "").startswith("W/") else None,
		"last_modified": respuesta.headers.get("Last-Modified"),
		"total": total,
	}
	try:
		ruta.write_text(json.dumps(datos), encoding="utf-8")
	except OSError:
		log.debug("No se pudieron guardar los metadatos de reanudación en %s.", ruta, exc_info=True)


def _escribir(
	respuesta: requests.Response,
	parcial: Path,
	anadir: bool,
	total: int | None,
	tamano_maximo: int | None,
	progreso: _Progreso,
	cancelacion: Any,
	conservar_parcial: bool,
	metadatos_ruta: Path,
	tamano_bloque: int,
) -> int:
	"""Vuelca el cuerpo al parcial comprobando cancelación y tamaño en cada bloque."""
	hecho = parcial.stat().st_size if anadir else 0
	with open(parcial, "ab" if anadir else "wb") as archivo:
		for bloque in respuesta.iter_content(chunk_size=tamano_bloque):
			if _esta_cancelado(cancelacion):
				archivo.close()
				if not conservar_parcial:
					_borrar(parcial, metadatos_ruta)
				log.info("Descarga cancelada con %d bytes; parcial %s.", hecho,
					"conservado" if conservar_parcial else "borrado")
				raise DescargaCancelada(conservar_parcial)
			if not bloque:
				continue
			archivo.write(bloque)
			hecho += len(bloque)
			if tamano_maximo is not None and hecho > tamano_maximo:
				archivo.close()
				_borrar(parcial, metadatos_ruta)
				raise ErrorDescarga(_("El archivo es más grande de lo permitido."), categoria="cliente",
					detalle=f"más de {tamano_maximo} bytes")
			progreso(hecho, total)
	return hecho


def _sha256_de(ruta: Path) -> str:
	"""SHA-256 del archivo leído por bloques (no carga el archivo en memoria)."""
	suma = hashlib.sha256()
	with open(ruta, "rb") as archivo:
		for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
			suma.update(bloque)
	return suma.hexdigest()
