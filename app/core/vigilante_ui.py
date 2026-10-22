"""Vigilante de congelaciones del hilo de la interfaz.

Un hilo de fondo envía cada pocos segundos un «ping» al hilo de la interfaz
(``wx.CallAfter``) y mide cuánto tarda en atenderse. Si supera el umbral, el
hilo de la interfaz estuvo BLOQUEADO (Windows marca la ventana como «No
responde» a partir de unos 5 s y el lector de pantalla deja de hablar):

- Mientras sigue bloqueado, vuelca en el registro la PILA del hilo principal
  (la última línea dice en qué función está atascado) y una línea por cada
  otro hilo con lo que estaba haciendo.
- Al recuperarse, anota cuánto duró el bloqueo.
- Los retrasos pequeños (lag) se anotan en DEBUG para ver su distribución.

Coste despreciable: un ``CallAfter`` cada ``intervalo`` segundos.

PLANTILLA del agente «logging-profesional».
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback

log = logging.getLogger(__name__)

_hilo: threading.Thread | None = None
_parar = threading.Event()


def iniciar(intervalo: float = 2.0, umbral: float = 3.0, umbral_lag: float = 0.75) -> None:
	"""Arranca el vigilante (llamadas repetidas no hacen nada). Llamar tras crear wx.App."""
	global _hilo
	if _hilo is not None and _hilo.is_alive():
		return
	_parar.clear()
	_hilo = threading.Thread(
		target=_bucle, args=(intervalo, umbral, umbral_lag), name="vigilante-ui", daemon=True
	)
	_hilo.start()


def detener() -> None:
	"""Detiene el vigilante (al cerrar la aplicación)."""
	_parar.set()


def volcar_pilas(motivo: str) -> None:
	"""Escribe en el registro la pila del hilo principal y el estado de los demás hilos."""
	marcos = sys._current_frames()
	principal = threading.main_thread().ident
	marco = marcos.get(principal)
	if marco is not None:
		pila = "".join(traceback.format_stack(marco))
		log.warning("%s. Pila del hilo de la interfaz (el culpable está al final):\n%s", motivo, pila)
	nombres = {hilo.ident: hilo.name for hilo in threading.enumerate()}
	lineas = []
	for ident, otro in marcos.items():
		if ident == principal:
			continue
		ultimo = traceback.extract_stack(otro)[-1:] or []
		for m in ultimo:
			archivo = m.filename.replace("\\", "/").rsplit("/", 1)[-1]
			lineas.append(f"  {nombres.get(ident, ident)}: {archivo}:{m.lineno} en {m.name}")
	if lineas:
		log.warning("Qué hacían los demás hilos en ese instante:\n%s", "\n".join(lineas))


def _bucle(intervalo: float, umbral: float, umbral_lag: float) -> None:
	import wx

	ultimo_volcado = 0.0
	while not _parar.wait(intervalo):
		if wx.GetApp() is None:
			continue
		atendido = threading.Event()
		enviado = time.monotonic()
		try:
			wx.CallAfter(atendido.set)
		except Exception:  # noqa: BLE001 - la aplicación se está cerrando
			continue
		if not atendido.wait(umbral):
			# Sigue bloqueado AHORA: es el momento de capturar la pila culpable.
			if time.monotonic() - ultimo_volcado > 10.0:
				ultimo_volcado = time.monotonic()
				try:
					volcar_pilas(f"El hilo de la interfaz lleva más de {umbral:.0f} s sin responder")
				except Exception:  # noqa: BLE001 - el diagnóstico no debe romper nada
					log.debug("No se pudo volcar la pila del bloqueo.", exc_info=True)
			atendido.wait(60.0)
		duracion = time.monotonic() - enviado
		if duracion >= umbral:
			log.warning(
				"El hilo de la interfaz estuvo bloqueado %.1f s (la ventana pudo aparecer como "
				"«No responde» y el lector de pantalla quedarse mudo).", duracion,
			)
		elif duracion >= umbral_lag:
			log.debug("Retraso del hilo de la interfaz: %.2f s.", duracion)
