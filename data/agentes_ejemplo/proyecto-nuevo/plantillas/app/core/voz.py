"""Anuncios por voz mínimos (opcionales) a través del lector de pantalla.

Usa ``accessible_output2`` si está instalado: habla con NVDA, JAWS o, si no
hay lector, con SAPI. Si no está instalado, los anuncios se omiten sin error:
la voz directa es un COMPLEMENTO; lo esencial es que cada control tenga su
nombre, rol y valor correctos para que el lector los lea por sí solo.

Se usa para dos cosas en el esqueleto: el aviso de arranque (antes de las
importaciones pesadas, cuando todavía no hay ventana) y avisos que no tienen
un control donde leerse (por ejemplo, «sigue en la bandeja del sistema»).
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_salida = None
_iniciada = False
_candado = threading.Lock()


def _obtener_salida():
	"""Crea la salida de voz la primera vez que se necesita (importación perezosa)."""
	global _salida, _iniciada
	with _candado:
		if not _iniciada:
			_iniciada = True
			try:
				from accessible_output2.outputs.auto import Auto

				_salida = Auto()
			except Exception:  # noqa: BLE001 - sin voz la aplicación sigue funcionando
				log.info("Voz directa no disponible (accessible_output2 no está instalado o falló).", exc_info=True)
				_salida = None
	return _salida


def hablar(texto: str, interrumpir: bool = False) -> bool:
	"""Envía ``texto`` al lector de pantalla. Devuelve ``True`` si se pudo.

	Se puede llamar desde cualquier hilo: no toca controles wx.
	"""
	salida = _obtener_salida()
	if salida is None or not texto:
		return False
	try:
		salida.output(texto, interrupt=interrumpir)
		return True
	except Exception:  # noqa: BLE001 - un fallo de voz no debe romper nada
		log.debug("No se pudo anunciar por voz: %s", texto, exc_info=True)
		return False
