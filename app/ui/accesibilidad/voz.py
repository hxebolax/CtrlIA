"""Salida de voz y braille directa al lector de pantalla.

La voz es un COMPLEMENTO de la semántica accesible, no su sustituto: el
lector ya anuncia por su cuenta el foco, los nombres, los roles y los estados
de los controles nativos. Solo se habla desde la aplicación cuando el lector
no puede saberlo (lectura lógica por columnas, resultado de una acción,
cambios en segundo plano).

Características:

- ``accessible_output2`` (NVDA, JAWS, Narrador/SAPI...) inicializado de forma
  perezosa; sin él, los anuncios van al registro en DEBUG.
- Un único hilo trabajador con cola (no un hilo por anuncio).
- COALESCENCIA: si llegan varios anuncios interrumpibles seguidos (navegación
  rápida), solo se dice el último. Los aditivos nunca se descartan.
- Retardo configurable (0-600 ms) antes de los interrumpibles, para que el
  mensaje llegue DESPUÉS del anuncio automático de foco del lector y lo gane.
- CANALES con interruptor propio: ``"navegacion"`` (lo que el usuario pide
  explícitamente: columnas, Ctrl+H) y ``"avisos"`` (eventos del sistema,
  progreso). Desactivar los avisos no deja muda la lectura de listas.
- Historial de los últimos 200 anuncios (repetir el último, consultarlos).
- Braille opcional (mismo texto a la línea braille). Con
  ``braille_sin_voz``, lo que se calla (voz silenciada, canal apagado) sigue
  llegando a la línea braille: nada existe solo como voz.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)

NAVEGACION = "navegacion"
AVISOS = "avisos"

_salida = None
_salida_intentada = False
_candado_salida = threading.Lock()

_cola: queue.Queue[_Anuncio | None] = queue.Queue()
_hilo: threading.Thread | None = None
_candado_hilo = threading.Lock()

_config = {
	"activada": True,      # interruptor general
	"silencio": False,     # modo «No molestar» temporal
	"retraso_ms": 120,
	"braille": True,
	"braille_sin_voz": True,  # si la voz se calla, el texto sigue yendo a braille
	"canales": {NAVEGACION: True, AVISOS: True},
}

_historial: deque[tuple[float, str]] = deque(maxlen=200)
_candado_historial = threading.Lock()


@dataclass
class _Anuncio:
	texto: str
	interrumpir: bool
	solo_braille: bool = False


# ── Configuración ─────────────────────────────────────────────────────────────

def configurar(
	activada: bool | None = None,
	retraso_ms: int | None = None,
	braille: bool | None = None,
	navegacion: bool | None = None,
	avisos: bool | None = None,
	braille_sin_voz: bool | None = None,
) -> None:
	"""Aplica las preferencias de voz (llamar al arrancar y al Aceptar/Aplicar Preferencias)."""
	if activada is not None:
		_config["activada"] = bool(activada)
	if retraso_ms is not None:
		_config["retraso_ms"] = max(0, min(600, int(retraso_ms)))
	if braille is not None:
		_config["braille"] = bool(braille)
	if navegacion is not None:
		_config["canales"][NAVEGACION] = bool(navegacion)
	if avisos is not None:
		_config["canales"][AVISOS] = bool(avisos)
	if braille_sin_voz is not None:
		_config["braille_sin_voz"] = bool(braille_sin_voz)


def silenciar(valor: bool) -> None:
	"""Activa o desactiva el silencio temporal («No molestar»)."""
	_config["silencio"] = bool(valor)


def esta_silenciado() -> bool:
	"""Indica si el silencio temporal está activo."""
	return bool(_config["silencio"])


def ultimo_anuncio() -> str:
	"""Texto del último anuncio (para una orden «repetir último mensaje»)."""
	with _candado_historial:
		return _historial[-1][1] if _historial else ""


def historial() -> list[tuple[float, str]]:
	"""Copia del historial (marca de tiempo, texto)."""
	with _candado_historial:
		return list(_historial)


# ── Salida ────────────────────────────────────────────────────────────────────

def _obtener_salida():
	"""Inicializa accessible_output2 una sola vez; ``None`` si no está disponible."""
	global _salida, _salida_intentada
	with _candado_salida:
		if not _salida_intentada:
			_salida_intentada = True
			try:
				from accessible_output2.outputs.auto import Auto

				_salida = Auto()
				log.info("Salida de voz inicializada (accessible_output2).")
			except Exception:
				log.warning("accessible_output2 no está disponible; los anuncios irán al registro.", exc_info=True)
				_salida = None
		return _salida


def _emitir(texto: str, interrumpir: bool, solo_braille: bool = False) -> None:
	salida = _obtener_salida()
	if salida is None:
		log.debug("[%s] %s", "braille" if solo_braille else "voz", texto)
		return
	try:
		if not solo_braille:
			salida.speak(texto, interrupt=interrumpir)
		if _config["braille"] and hasattr(salida, "braille"):
			try:
				salida.braille(texto)
			except Exception:  # noqa: BLE001 - no todos los lectores/salidas admiten braille
				log.debug("La salida activa no admite braille.", exc_info=True)
	except Exception:
		log.exception("Error al enviar un anuncio al lector de pantalla.")


def _trabajador() -> None:
	while True:
		anuncio = _cola.get()
		if anuncio is None:
			return
		# Coalescencia: entre interrumpibles seguidos gana el último. Si detrás
		# llega uno aditivo, se dice primero el interrumpible pendiente y luego
		# el aditivo: ninguno de los dos se pierde.
		while anuncio.interrumpir:
			try:
				siguiente = _cola.get_nowait()
			except queue.Empty:
				break
			if siguiente is None:
				return
			if not siguiente.interrumpir or siguiente.solo_braille != anuncio.solo_braille:
				_emitir(anuncio.texto, True, anuncio.solo_braille)
				anuncio = siguiente
				break
			anuncio = siguiente
		if anuncio.interrumpir and not anuncio.solo_braille and _config["retraso_ms"] > 0:
			time.sleep(_config["retraso_ms"] / 1000.0)
		_emitir(anuncio.texto, anuncio.interrumpir, anuncio.solo_braille)


def _asegurar_hilo() -> None:
	global _hilo
	with _candado_hilo:
		if _hilo is None or not _hilo.is_alive():
			_hilo = threading.Thread(target=_trabajador, name="voz", daemon=True)
			_hilo.start()


def motivo_silencio(canal: str = AVISOS) -> str:
	"""Por qué no se hablaría ahora en ``canal`` (vacío si se habla)."""
	if not _config["activada"]:
		return "desactivada"
	if _config["silencio"]:
		return "silencio"
	if not _config["canales"].get(canal, True):
		return "canal"
	return ""


def destino(canal: str = AVISOS, solo_braille: bool = False) -> str:
	"""Adónde iría ahora un anuncio: ``"voz"`` (voz y braille), ``"braille"`` o ``""`` (a ningún sitio)."""
	if not solo_braille and not motivo_silencio(canal):
		return "voz"
	if _config["braille"] and (solo_braille or _config["braille_sin_voz"]):
		return "braille"
	return ""


def hablar(
	texto: str,
	interrumpir: bool = True,
	canal: str = AVISOS,
	sincrono: bool = False,
	solo_braille: bool = False,
) -> None:
	"""Anuncia ``texto`` por el lector de pantalla activo.

	:param interrumpir: corta lo que el lector esté diciendo (lo habitual).
		Use ``False`` para mensajes que no deben pisar a otros (resultado de
		una tarea larga mientras el usuario lee).
	:param canal: ``NAVEGACION`` para lo que el usuario pide explícitamente
		(columnas, ayuda Ctrl+H); ``AVISOS`` para eventos del sistema.
	:param sincrono: habla en el hilo actual sin cola ni retardo. Solo para el
		aviso de arranque ANTES de las importaciones pesadas (si fuera por la
		cola, el hilo de voz no conseguiría el GIL hasta el final).
	:param solo_braille: el texto va solo a la línea braille (sin voz).

	Si la voz está callada (desactivada, «No molestar» o canal apagado) y
	``braille_sin_voz`` está activo, el texto se envía igualmente a braille.
	"""
	texto = (texto or "").strip()
	if not texto:
		return
	salida = destino(canal, solo_braille)
	if not salida:
		log.debug("[voz callada: %s] %s", motivo_silencio(canal) or "sin braille", texto)
		return
	solo_braille = salida == "braille"
	with _candado_historial:
		_historial.append((time.time(), texto))
	if sincrono:
		_emitir(texto, interrumpir, solo_braille)
		return
	_asegurar_hilo()
	_cola.put(_Anuncio(texto, interrumpir, solo_braille))


def detener() -> None:
	"""Detiene el hilo de voz al cerrar la aplicación."""
	global _hilo
	with _candado_hilo:
		if _hilo is not None and _hilo.is_alive():
			_cola.put(None)
			_hilo.join(timeout=2)
		_hilo = None
