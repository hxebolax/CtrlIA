"""Configuración persistente mínima: JSON legible en ``data/config/config.json``.

Decisiones:

- Los valores por defecto viven EN EL CÓDIGO (``VALORES_POR_DEFECTO``), no en
  un archivo distribuido. Si falta ``config.json`` se usan estos valores; si
  una versión nueva añade claves, una configuración antigua sigue valiendo
  porque se fusiona sobre ellos.
- Escritura atómica: temporal + ``fsync`` + ``os.replace``. Un corte de luz
  nunca deja un ``config.json`` a medias: o queda el viejo o el nuevo.
- Un archivo dañado NO impide arrancar: se aparta con otro nombre (para poder
  estudiarlo), se avisa en el registro y se sigue con los valores por defecto.
- Un valor de tipo equivocado (``"ancho": "grande"``) se ignora y se usa el
  predeterminado: más vale una ventana de tamaño estándar que una excepción.
- Acceso seguro entre hilos con un ``RLock``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.core import paths

log = logging.getLogger(__name__)

NOMBRE_ARCHIVO = "config.json"

# Todas las claves que la aplicación conoce, con su valor inicial.
VALORES_POR_DEFECTO: dict[str, Any] = {
	"general": {
		# Voz al arrancar (solo si hay lector de pantalla o SAPI disponibles).
		"anunciar_arranque": True,
		# Opciones de bandeja desactivadas de serie: hay usuarios cuya bandeja
		# oculta los iconos y perderían el acceso a la ventana.
		"minimizar_a_bandeja": False,
		"cerrar_a_bandeja": False,
		"iniciar_en_bandeja": False,
	},
	"ventana": {
		# -1 = sin posición guardada: se centra en la pantalla.
		"x": -1,
		"y": -1,
		"ancho": 800,
		"alto": 600,
		"maximizada": False,
		"barra_estado": True,
	},
	"registro": {
		"nivel": "INFO",
	},
}


def _tipo_compatible(valor: Any, referencia: Any) -> bool:
	"""Indica si ``valor`` tiene un tipo aceptable para la clave de ``referencia``.

	``bool`` es subclase de ``int`` en Python; se trata aparte para que
	``"maximizada": 1`` no cuele como booleano ni ``True`` como tamaño.
	"""
	if referencia is None:
		return True
	if isinstance(referencia, bool):
		return isinstance(valor, bool)
	if isinstance(referencia, (int, float)):
		return isinstance(valor, (int, float)) and not isinstance(valor, bool)
	return isinstance(valor, type(referencia))


class Configuracion:
	"""Almacén de configuración con claves en notación de puntos (``"ventana.ancho"``)."""

	def __init__(self, ruta: Path | None = None) -> None:
		"""Carga la configuración del disco o, si no existe, los valores por defecto.

		:param ruta: archivo alternativo (pruebas); por defecto ``data/config/config.json``.
		"""
		self._candado = threading.RLock()
		self._ruta = ruta or (paths.ruta_configuracion() / NOMBRE_ARCHIVO)
		self._datos: dict[str, Any] = copy.deepcopy(VALORES_POR_DEFECTO)
		self._cargar()

	@property
	def ruta(self) -> Path:
		"""Archivo donde se guarda la configuración."""
		return self._ruta

	def _cargar(self) -> None:
		"""Lee el JSON y lo fusiona sobre los valores por defecto."""
		if not self._ruta.is_file():
			log.info("No hay configuración guardada; se usan los valores por defecto.")
			return
		try:
			guardado = json.loads(self._ruta.read_text(encoding="utf-8"))
		except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
			self._apartar_danado(f"{type(error).__name__}: {error}")
			return
		if not isinstance(guardado, dict):
			self._apartar_danado("el contenido no es un objeto JSON")
			return
		self._fusionar(self._datos, guardado, VALORES_POR_DEFECTO, "")

	def _apartar_danado(self, motivo: str) -> None:
		"""Renombra un archivo ilegible y sigue con los valores por defecto."""
		copia = self._ruta.with_name(f"{self._ruta.stem}.danado-{time.strftime('%Y%m%d-%H%M%S')}.json")
		log.warning(
			"La configuración %s está dañada (%s); se aparta como %s y se usan los valores por defecto.",
			self._ruta,
			motivo,
			copia.name,
		)
		try:
			os.replace(self._ruta, copia)
		except OSError:
			log.exception("No se pudo apartar la configuración dañada %s", self._ruta)

	def _fusionar(self, destino: dict[str, Any], origen: dict[str, Any], referencia: dict[str, Any], prefijo: str) -> None:
		"""Copia ``origen`` sobre ``destino`` validando el tipo de las claves conocidas.

		Las claves desconocidas se conservan (pueden ser de una versión más nueva
		o de un complemento); las conocidas con un tipo erróneo se descartan.
		"""
		for clave, valor in origen.items():
			ruta_clave = f"{prefijo}{clave}"
			if clave not in referencia:
				destino[clave] = valor
				continue
			esperado = referencia[clave]
			if isinstance(esperado, dict):
				if isinstance(valor, dict):
					self._fusionar(destino[clave], valor, esperado, ruta_clave + ".")
				else:
					log.warning("Configuración: «%s» debería ser una sección; se ignora el valor guardado.", ruta_clave)
			elif _tipo_compatible(valor, esperado):
				destino[clave] = valor
			else:
				log.warning("Configuración: «%s» tiene un valor no válido (%r); se usa %r.", ruta_clave, valor, esperado)

	def obtener(self, clave: str, defecto: Any = None) -> Any:
		"""Devuelve una COPIA del valor de ``clave`` o ``defecto`` si no existe.

		Se devuelve una copia para que nadie modifique la configuración en
		memoria sin pasar por ``establecer`` (y sin candado).
		"""
		with self._candado:
			actual: Any = self._datos
			for parte in clave.split("."):
				if not isinstance(actual, dict) or parte not in actual:
					return defecto
				actual = actual[parte]
			return copy.deepcopy(actual)

	def establecer(self, clave: str, valor: Any) -> None:
		"""Asigna ``valor`` a ``clave`` en memoria (se escribe al llamar a ``guardar``)."""
		partes = clave.split(".")
		with self._candado:
			actual = self._datos
			for parte in partes[:-1]:
				siguiente = actual.get(parte)
				if not isinstance(siguiente, dict):
					siguiente = {}
					actual[parte] = siguiente
				actual = siguiente
			actual[partes[-1]] = copy.deepcopy(valor)

	def guardar(self) -> bool:
		"""Escribe la configuración de forma atómica. Devuelve ``True`` si lo consiguió.

		No lanza excepciones: se llama al cerrar la aplicación y un disco lleno
		no debe impedir que la ventana se cierre. El fallo queda en el registro.
		"""
		with self._candado:
			texto = json.dumps(self._datos, ensure_ascii=False, indent="\t")
			temporal = self._ruta.with_name(self._ruta.name + ".tmp")
			try:
				self._ruta.parent.mkdir(parents=True, exist_ok=True)
				with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
					archivo.write(texto)
					archivo.flush()
					os.fsync(archivo.fileno())
				os.replace(temporal, self._ruta)
			except OSError:
				log.exception("No se pudo guardar la configuración en %s", self._ruta)
				try:
					temporal.unlink(missing_ok=True)
				except OSError:
					log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
				return False
		log.debug("Configuración guardada en %s", self._ruta)
		return True
