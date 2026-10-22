"""Descripción de las opciones de un agente y lectura y escritura de su archivo.

Reglas que protegen la configuración del usuario:

- **Nunca se pierde nada.** Solo se cambian las claves que la persona cambia;
  el resto del archivo (claves que Ctrl+IA no conoce, servidores MCP, proyectos
  de confianza...) queda igual. El TOML de Codex se edita con ``tomlkit``, que
  conserva también los comentarios y el formato.
- **Los cambios se aplican sobre el archivo recién leído.** Si el agente lo ha
  modificado mientras el editor estaba abierto, esos cambios se conservan.
- **Un archivo que no se entiende no se toca.** Se explica el error y no se
  guarda nada hasta que la persona lo arregle (o lo abra en su editor).
- **Copia de seguridad antes de escribir**, en ``data/backups/agentes``.
- **Escritura atómica**: temporal en la misma carpeta y ``os.replace``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Tipos de opción.
BOOL, TEXTO, ENTERO, DECIMAL, OPCION, LISTA, DICCIONARIO, JSON = (
	"bool", "texto", "entero", "decimal", "opcion", "lista", "diccionario", "json",
)

#: Valor especial de un cambio: quitar la clave del archivo (volver al valor por defecto del agente).
QUITAR = object()

#: Copias de seguridad que se conservan por archivo.
MAX_COPIAS = 20


class ErrorConfigAgente(Exception):
	"""El archivo no se puede leer o escribir; el mensaje es para la persona usuaria."""


@dataclass(frozen=True)
class Ajuste:
	"""Una opción documentada del archivo de configuración de un agente.

	:param clave: ruta con puntos dentro del archivo («permissions.defaultMode»).
	:param etiqueta: nombre en español que se lee en la lista.
	:param tipo: ``bool``, ``texto``, ``entero``, ``decimal``, ``opcion``,
		``lista`` (de textos), ``diccionario`` (texto → texto) o ``json``.
	:param ayuda: qué hace, en palabras de quien lo usa.
	:param categoria: grupo en el que aparece («Permisos», «Modelo»...).
	:param opciones: pares ``(valor, etiqueta)`` para ``opcion``.
	:param defecto: el valor por defecto del agente, en texto para leerlo.
	:param riesgo: si no está vacío, por qué conviene pensarlo (se confirma al guardar).
	:param ambitos: ids de ámbito donde tiene efecto; vacío = en todos.
	:param libre: una ``opcion`` que admite además escribir otro valor.
	:param minimo, maximo: límites de ``entero`` y ``decimal``.
	:param obsoleta: la opción ya no tiene efecto; se ofrece quitarla.
	"""

	clave: str
	etiqueta: str
	tipo: str
	ayuda: str
	categoria: str
	opciones: tuple[tuple[Any, str], ...] = ()
	defecto: str = ""
	riesgo: str = ""
	ambitos: tuple[str, ...] = ()
	libre: bool = False
	minimo: float | None = None
	maximo: float | None = None
	obsoleta: bool = False

	@property
	def ruta(self) -> tuple[str, ...]:
		return tuple(self.clave.split("."))


@dataclass(frozen=True)
class Ambito:
	"""Dónde vive el archivo: el del usuario, el de un proyecto...

	:param ruta: recibe la carpeta del proyecto (o ``None``) y devuelve el archivo.
	"""

	id: str
	nombre: str
	descripcion: str
	ruta: Callable[[Path | None], Path | None]
	necesita_carpeta: bool = False


@dataclass(frozen=True)
class AgenteConfigurable:
	"""Un agente cuyo archivo de configuración se puede editar."""

	id: str
	nombre: str
	formato: str  # "json" o "toml"
	ambitos: tuple[Ambito, ...]
	ajustes: tuple[Ajuste, ...]
	documentacion: str = ""
	nota: str = ""
	#: Claves de primer nivel que no se listan en «Otras claves» (las gestiona el agente).
	ocultas: tuple[str, ...] = ()

	def ambito(self, identificador: str) -> Ambito:
		return next(a for a in self.ambitos if a.id == identificador)

	def categorias(self) -> list[str]:
		return list(dict.fromkeys(a.categoria for a in self.ajustes))

	def ajuste(self, clave: str) -> Ajuste | None:
		return next((a for a in self.ajustes if a.clave == clave), None)


# ── Valores ──────────────────────────────────────────────────────────────────


def a_python(valor: Any) -> Any:
	"""Convierte los objetos de ``tomlkit`` en tipos normales de Python."""
	desenvolver = getattr(valor, "unwrap", None)
	if callable(desenvolver):
		return desenvolver()
	return copy.deepcopy(valor)


def validar(ajuste: Ajuste, valor: Any) -> str | None:
	"""Mensaje de error para la persona, o ``None`` si el valor vale."""
	if ajuste.tipo == BOOL and not isinstance(valor, bool):
		return "Elige activado o desactivado."
	if ajuste.tipo == ENTERO:
		if isinstance(valor, bool) or not isinstance(valor, int):
			return "Escribe un número entero."
	if ajuste.tipo == DECIMAL:
		if isinstance(valor, bool) or not isinstance(valor, (int, float)):
			return "Escribe un número."
	if ajuste.tipo in (ENTERO, DECIMAL):
		if ajuste.minimo is not None and valor < ajuste.minimo:
			return f"El valor mínimo es {ajuste.minimo:g}."
		if ajuste.maximo is not None and valor > ajuste.maximo:
			return f"El valor máximo es {ajuste.maximo:g}."
	if ajuste.tipo == TEXTO and not isinstance(valor, str):
		return "Escribe un texto."
	if ajuste.tipo == OPCION:
		validos = [v for v, _e in ajuste.opciones]
		if valor not in validos and not (ajuste.libre and isinstance(valor, str) and valor.strip()):
			return "Elige uno de los valores de la lista."
	if ajuste.tipo == LISTA and not (isinstance(valor, list) and all(isinstance(v, str) for v in valor)):
		return "Debe ser una lista de textos."
	if ajuste.tipo == DICCIONARIO and not (
		isinstance(valor, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in valor.items())
	):
		return "Debe ser una lista de pares nombre y valor (textos)."
	return None


def describir(ajuste: Ajuste | None, valor: Any) -> str:
	"""El valor tal como se lee en la lista («Activado», «3 reglas: Bash, Read...»)."""
	if ajuste is not None and ajuste.tipo == BOOL and isinstance(valor, bool):
		return "Activado" if valor else "Desactivado"
	if ajuste is not None and ajuste.tipo == OPCION:
		etiqueta = next((e for v, e in ajuste.opciones if v == valor), None)
		if etiqueta is not None:
			return etiqueta
	if isinstance(valor, bool):
		return "true" if valor else "false"
	if isinstance(valor, list):
		if not valor:
			return "Lista vacía"
		muestra = ", ".join(str(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False) for v in valor[:5])
		resto = f" y {len(valor) - 5} más" if len(valor) > 5 else ""
		return f"{len(valor)} elementos: {muestra}{resto}" if len(valor) != 1 else f"1 elemento: {muestra}"
	if isinstance(valor, dict):
		if not valor:
			return "Vacío"
		claves = list(valor)
		resto = f" y {len(claves) - 5} más" if len(claves) > 5 else ""
		return f"{len(claves)} entradas: {', '.join(map(str, claves[:5]))}{resto}"
	if isinstance(valor, str):
		return valor if valor else "(texto vacío)"
	return json.dumps(valor, ensure_ascii=False, default=str)


# ── Documento ────────────────────────────────────────────────────────────────


class DocumentoConfig:
	"""El archivo de configuración de un agente, cargado para leerlo y cambiarlo."""

	def __init__(self, ruta: Path, formato: str) -> None:
		self.ruta = Path(ruta)
		self.formato = formato
		self.existe = False
		self.datos: Any = None
		self.cargar()

	# -- Lectura --------------------------------------------------------------

	def _analizar(self, texto: str) -> Any:
		if self.formato == "toml":
			import tomlkit
			from tomlkit.exceptions import ParseError

			try:
				return tomlkit.parse(texto)
			except ParseError as error:
				raise ErrorConfigAgente(f"El archivo TOML tiene un error: {error}") from error
		try:
			datos = json.loads(texto) if texto.strip() else {}
		except ValueError as error:
			raise ErrorConfigAgente(
				f"El archivo JSON tiene un error en la línea {getattr(error, 'lineno', '?')}: {getattr(error, 'msg', error)}"
			) from error
		if not isinstance(datos, dict):
			raise ErrorConfigAgente("El archivo no contiene un objeto JSON (debería empezar por «{»).")
		return datos

	def _vacio(self) -> Any:
		if self.formato == "toml":
			import tomlkit

			return tomlkit.document()
		return {}

	def cargar(self) -> None:
		"""Lee el archivo. Si no existe, queda vacío; si no se entiende, lanza ``ErrorConfigAgente``."""
		try:
			texto = self.ruta.read_text(encoding="utf-8-sig")
		except FileNotFoundError:
			self.existe = False
			self.datos = self._vacio()
			return
		except OSError as error:
			raise ErrorConfigAgente(f"No se puede leer {self.ruta}: {error.strerror or error}") from error
		self.datos = self._analizar(texto)
		self.existe = True

	# -- Acceso por rutas -----------------------------------------------------

	def obtener(self, ruta: tuple[str, ...]) -> tuple[bool, Any]:
		"""``(existe, valor)`` de una clave anidada, con tipos normales de Python."""
		actual = self.datos
		for parte in ruta:
			if not isinstance(actual, dict) or parte not in actual:
				return False, None
			actual = actual[parte]
		return True, a_python(actual)

	def _tabla_nueva(self) -> Any:
		if self.formato == "toml":
			import tomlkit

			return tomlkit.table()
		return {}

	def poner(self, ruta: tuple[str, ...], valor: Any) -> None:
		"""Fija una clave anidada, creando las tablas intermedias que falten."""
		actual = self.datos
		for parte in ruta[:-1]:
			siguiente = actual.get(parte) if isinstance(actual, dict) else None
			if not isinstance(siguiente, dict):
				siguiente = self._tabla_nueva()
				actual[parte] = siguiente
				siguiente = actual[parte]
			actual = siguiente
		actual[ruta[-1]] = valor

	def quitar(self, ruta: tuple[str, ...]) -> bool:
		"""Quita una clave; las tablas que quedan vacías por ello también se quitan."""
		cadena = [self.datos]
		for parte in ruta[:-1]:
			actual = cadena[-1]
			if not isinstance(actual, dict) or not isinstance(actual.get(parte), dict):
				return False
			cadena.append(actual[parte])
		if ruta[-1] not in cadena[-1]:
			return False
		del cadena[-1][ruta[-1]]
		for nivel in range(len(ruta) - 1, 0, -1):
			if len(cadena[nivel]) == 0:
				del cadena[nivel - 1][ruta[nivel - 1]]
			else:
				break
		return True

	def aplicar(self, cambios: dict[str, Any]) -> None:
		"""Aplica ``{clave con puntos: valor o QUITAR}``."""
		for clave, valor in cambios.items():
			ruta = tuple(clave.split("."))
			if valor is QUITAR:
				self.quitar(ruta)
			else:
				self.poner(ruta, valor)

	def hojas(self, profundidad: int = 2) -> Iterator[tuple[str, Any]]:
		"""``(clave con puntos, valor)`` de las claves hasta ``profundidad`` niveles."""

		def recorrer(datos: Any, prefijo: tuple[str, ...]) -> Iterator[tuple[str, Any]]:
			for clave, valor in datos.items():
				ruta = (*prefijo, str(clave))
				if isinstance(valor, dict) and len(ruta) < profundidad and valor and "." not in str(clave):
					yield from recorrer(valor, ruta)
				else:
					yield ".".join(ruta), a_python(valor)

		yield from recorrer(self.datos, ())

	def texto(self) -> str:
		if self.formato == "toml":
			import tomlkit

			return tomlkit.dumps(self.datos)
		return json.dumps(self.datos, ensure_ascii=False, indent=2) + "\n"

	# -- Escritura ------------------------------------------------------------

	def guardar(self, cambios: dict[str, Any], carpeta_copias: Path | None = None) -> Path | None:
		"""Vuelve a leer el archivo, aplica ``cambios`` y lo escribe. Devuelve la copia de seguridad.

		Releer justo antes conserva lo que el agente haya escrito mientras tanto.
		"""
		self.cargar()
		copia = self._copia_de_seguridad(carpeta_copias) if self.existe else None
		self.aplicar(cambios)
		contenido = self.texto()
		self._analizar(contenido)  # lo que se va a escribir se vuelve a entender
		self.ruta.parent.mkdir(parents=True, exist_ok=True)
		descriptor, temporal = tempfile.mkstemp(prefix=".ctrlia-", suffix=".tmp", dir=str(self.ruta.parent))
		try:
			with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
				archivo.write(contenido)
				archivo.flush()
				os.fsync(archivo.fileno())
			os.replace(temporal, self.ruta)
		except OSError as error:
			try:
				os.unlink(temporal)
			except OSError:
				log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
			raise ErrorConfigAgente(f"No se pudo guardar {self.ruta}: {error.strerror or error}") from error
		self.existe = True
		log.info("Configuración del agente guardada en %s (%d cambios)", self.ruta, len(cambios))
		return copia

	def _copia_de_seguridad(self, carpeta: Path | None) -> Path | None:
		if carpeta is None:
			return None
		try:
			carpeta.mkdir(parents=True, exist_ok=True)
			marca = time.strftime("%Y%m%d_%H%M%S")
			destino = carpeta / f"{self.ruta.stem}-{marca}{self.ruta.suffix}"
			numero = 1
			while destino.exists():
				numero += 1
				destino = carpeta / f"{self.ruta.stem}-{marca}-{numero}{self.ruta.suffix}"
			destino.write_bytes(self.ruta.read_bytes())
			viejas = sorted(carpeta.glob(f"{self.ruta.stem}-*{self.ruta.suffix}"))
			for vieja in viejas[:-MAX_COPIAS]:
				vieja.unlink(missing_ok=True)
			return destino
		except OSError as error:
			raise ErrorConfigAgente(f"No se pudo hacer la copia de seguridad antes de guardar: {error.strerror or error}") from error


@dataclass
class Fila:
	"""Una fila del editor: una opción documentada o una clave del archivo que Ctrl+IA no conoce."""

	clave: str
	ajuste: Ajuste | None
	existe: bool
	valor: Any = None
	categoria: str = ""
	extra: dict = field(default_factory=dict)


def filas(agente: AgenteConfigurable, documento: DocumentoConfig, ambito: str) -> list[Fila]:
	"""Opciones del catálogo (con su valor actual) y, al final, las demás claves del archivo."""
	resultado: list[Fila] = []
	conocidas: set[str] = set()
	for ajuste in agente.ajustes:
		existe, valor = documento.obtener(ajuste.ruta)
		if ajuste.obsoleta and not existe:
			continue
		conocidas.add(ajuste.clave)
		resultado.append(Fila(ajuste.clave, ajuste, existe, valor, ajuste.categoria))
	prefijos = {a.clave.split(".")[0] for a in agente.ajustes}
	for clave, valor in documento.hojas():
		raiz = clave.split(".")[0]
		if clave in conocidas or raiz in agente.ocultas:
			continue
		if any(c.startswith(clave + ".") for c in conocidas):
			continue
		if raiz in prefijos and any(clave.startswith(c + ".") for c in conocidas):
			continue
		resultado.append(Fila(clave, None, True, valor, "Otras claves del archivo"))
	return resultado
