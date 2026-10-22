"""Medición de las fases del arranque e importaciones diferidas.

Por qué: «arranca lento» no se arregla a ciegas. Antes de tocar nada hay que
saber QUÉ fase tarda: importar wx, leer la configuración, abrir la base de datos,
construir la ventana, cargar los datos... Esta medición cuesta microsegundos
(``time.perf_counter``) y deja UNA línea en el registro al terminar:

	Arranque en 1.84 s (antes de main: 0.62 s) | configuracion 0.05 · wx 0.40 · ventana 0.90 · datos 0.49 | más lenta: ventana

Uso típico en ``main.py``::

	from app.core.medicion_arranque import MedicionArranque
	medicion = MedicionArranque()
	configurar_registro(...)
	medicion.fase("registro")
	import wx
	medicion.fase("wx")
	...
	ventana.Show()
	medicion.fase("ventana")
	wx.CallAfter(medicion.terminar, umbral_aviso=3.0)  # cuando la ventana ya responde

Guía rápida de importaciones diferidas (detalles en referencias/rendimiento.md):

- Importa lo pesado (SDK de redes, PIL, numpy, bibliotecas de documentos) DENTRO
  de la función que lo usa, no al principio del módulo: Python solo lo importa
  la primera vez y después es un acceso a ``sys.modules``.
- Para anotaciones de tipos usa ``from __future__ import annotations`` y
  ``if TYPE_CHECKING:``: así el tipo no obliga a importar el módulo al arrancar.
- ``importar_perezoso("modulo")`` devuelve el módulo sin ejecutarlo hasta que se
  usa uno de sus atributos (``importlib.util.LazyLoader``).
- Mide con ``python -X importtime main.py 2> importaciones.txt``.

PLANTILLA del agente «hilos-y-tareas».
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType

log = logging.getLogger(__name__)


def segundos_desde_inicio_proceso() -> float | None:
	"""Segundos transcurridos desde que Windows creó el proceso.

	Incluye lo que pasa ANTES de que se ejecute la primera línea de ``main.py``:
	carga del intérprete, de la versión compilada y de las importaciones iniciales.
	Devuelve ``None`` fuera de Windows o si no se puede consultar.
	"""
	if sys.platform != "win32":
		return None
	import ctypes
	from ctypes import wintypes

	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	kernel32.GetCurrentProcess.restype = wintypes.HANDLE
	kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
	kernel32.GetProcessTimes.restype = wintypes.BOOL
	creacion, salida, nucleo, usuario, ahora = (wintypes.FILETIME() for _ in range(5))
	if not kernel32.GetProcessTimes(
		kernel32.GetCurrentProcess(), ctypes.byref(creacion), ctypes.byref(salida),
		ctypes.byref(nucleo), ctypes.byref(usuario),
	):
		log.debug("GetProcessTimes falló (error %d).", ctypes.get_last_error())
		return None
	kernel32.GetSystemTimePreciseAsFileTime(ctypes.byref(ahora))

	def a_entero(tiempo: wintypes.FILETIME) -> int:
		return (tiempo.dwHighDateTime << 32) | tiempo.dwLowDateTime

	# FILETIME cuenta intervalos de 100 ns.
	return max(0.0, (a_entero(ahora) - a_entero(creacion)) / 10_000_000)


class MedicionArranque:
	"""Cronómetro de fases del arranque con resumen en una línea.

	:param nombre: rótulo del resumen («Arranque», «Apertura del documento»...).
	:param reloj: función de tiempo; se inyecta en las pruebas.
	:param incluir_proceso: añade el tiempo transcurrido antes de crear la medición.
	"""

	def __init__(
		self,
		nombre: str = "Arranque",
		reloj: Callable[[], float] = time.perf_counter,
		incluir_proceso: bool = True,
	) -> None:
		self.nombre = nombre
		self._reloj = reloj
		self._inicio = reloj()
		self._ultima = self._inicio
		self._fases: list[tuple[str, float]] = []
		self._terminada: float | None = None
		self._previo = segundos_desde_inicio_proceso() if incluir_proceso else None

	def fase(self, nombre: str) -> float:
		"""Cierra una fase: el tiempo desde la marca anterior. Devuelve su duración."""
		ahora = self._reloj()
		duracion = ahora - self._ultima
		self._fases.append((nombre, duracion))
		self._ultima = ahora
		return duracion

	@contextmanager
	def medir(self, nombre: str) -> Iterator[None]:
		"""Mide un bloque concreto: ``with medicion.medir("base de datos"): ...``."""
		inicio = self._reloj()
		try:
			yield
		finally:
			fin = self._reloj()
			self._fases.append((nombre, fin - inicio))
			self._ultima = fin

	@property
	def fases(self) -> list[tuple[str, float]]:
		"""Copia de las fases medidas (nombre, segundos)."""
		return list(self._fases)

	@property
	def total(self) -> float:
		"""Segundos desde la creación de la medición hasta ``terminar`` (o hasta ahora)."""
		return (self._terminada if self._terminada is not None else self._reloj()) - self._inicio

	def mas_lenta(self) -> tuple[str, float] | None:
		"""La fase que más tardó, o ``None`` si no hay fases."""
		return max(self._fases, key=lambda fase: fase[1]) if self._fases else None

	def resumen(self) -> str:
		"""Texto de una línea con el total, el tiempo previo y cada fase."""
		partes = [f"{self.nombre} en {self.total:.2f} s"]
		if self._previo is not None:
			partes[0] += f" (antes de main: {self._previo:.2f} s)"
		if self._fases:
			partes.append(" · ".join(f"{nombre} {segundos:.2f}" for nombre, segundos in self._fases))
			lenta = self.mas_lenta()
			if lenta is not None:
				partes.append(f"más lenta: {lenta[0]}")
		return " | ".join(partes)

	def terminar(self, umbral_aviso: float | None = None, registrador: logging.Logger | None = None) -> str:
		"""Detiene el cronómetro y escribe el resumen en el registro (una sola vez).

		Con ``umbral_aviso`` el resumen sale como WARNING si el total lo supera, para
		que un arranque lento aparezca en el archivo de incidencias.
		"""
		destino = registrador or log
		if self._terminada is not None:
			return self.resumen()
		self._terminada = self._reloj()
		texto = self.resumen()
		if umbral_aviso is not None and self.total > umbral_aviso:
			destino.warning("%s (supera el umbral de %.1f s)", texto, umbral_aviso)
		else:
			destino.info("%s", texto)
		return texto


def importar_perezoso(nombre: str) -> ModuleType:
	"""Devuelve el módulo ``nombre`` sin ejecutarlo hasta el primer acceso a un atributo.

	Receta de la documentación de ``importlib`` (``LazyLoader``). Úsalo para
	módulos pesados que se referencian al arrancar pero se usan tarde. Límites:
	los errores de importación aparecen en el primer uso, no aquí; no sirve para
	``from modulo import nombre`` (eso lo ejecuta); y en la versión compilada hay
	que comprobar que el módulo sigue incluido (cx_Freeze no ve importaciones por
	cadena: añádelo a ``packages``/``includes``).
	"""
	if nombre in sys.modules:
		return sys.modules[nombre]
	especificacion = importlib.util.find_spec(nombre)
	if especificacion is None or especificacion.loader is None:
		raise ModuleNotFoundError(f"No se encontró el módulo {nombre!r}", name=nombre)
	cargador = importlib.util.LazyLoader(especificacion.loader)
	especificacion.loader = cargador
	modulo = importlib.util.module_from_spec(especificacion)
	sys.modules[nombre] = modulo
	cargador.exec_module(modulo)
	return modulo
