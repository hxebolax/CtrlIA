"""Versión disponible de cada agente y cómo actualizarlo (sin interfaz).

Lo usa «Agentes instalados» (``app/ui/dialogos/agentes.py``):

- **Cómo se actualiza** (:func:`forma_de_actualizar`), según dónde está el
  programa que se encontró al detectarlo:

  - instalado con npm (la ruta está dentro de un ``node_modules``): el paquete
    sale de su ``package.json`` y se actualiza con
    ``npm install -g <paquete>@latest`` en una consola;
  - Claude Code nativo: ``claude update`` (comprobado con 2.1.308: «Check for
    updates and install if available»; no hay forma de solo consultar);
  - Antigravity CLI: ``agy update`` (comprobado con la ``agy`` del equipo:
    subcomando «Update CLI», sin opción para solo consultar);
  - lo demás (la app de Codex, Goose, Ollama...) no se actualiza desde aquí.

- **Versión disponible** (:class:`ComprobadorVersiones`): solo para los de npm,
  con ``npm view <paquete> version --json``. Ctrl+IA no lanza lanzadores
  ``.cmd``: npm se ejecuta como ``node.exe npm-cli.js`` (:func:`orden_npm`).
  Las respuestas se guardan 5 minutos en memoria y dos preguntas a la vez por
  el mismo paquete esperan a la misma consulta.

Nada de esto instala ni cambia nada: actualizar lo hace la persona en la
consola que se abre.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.services.agentes import procesos
from app.services.agentes.base import EstadoProveedor

log = logging.getLogger(__name__)

ORIGEN_NPM = "npm"
ORIGEN_CLAUDE = "claude"
ORIGEN_AGY = "agy"

#: Segundos que se guarda la versión disponible de un paquete.
VIGENCIA = 300.0
#: Segundos que se espera a ``npm view``.
TIEMPO_NPM = 30.0

#: Nombre válido de un paquete de npm (con ámbito opcional). Va dentro de una orden de ``cmd.exe``:
#: con esta forma no puede llevar comillas, espacios ni ``&``.
_RE_PAQUETE = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]{0,213}/)?[a-z0-9][a-z0-9._~-]{0,213}$")
#: Una versión semántica dentro de un texto («codex-cli 0.163.0», «2.1.308 (Claude Code)»).
_RE_VERSION = re.compile(r"(?<![\d.])v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?")


class ErrorActualizacion(Exception):
	"""Fallo con un mensaje en español para la persona."""


@dataclass(frozen=True, slots=True)
class FormaActualizar:
	"""Cómo se actualiza un agente instalado.

	:param origen: :data:`ORIGEN_NPM`, :data:`ORIGEN_CLAUDE` u :data:`ORIGEN_AGY`.
	:param orden: orden que se abre en una consola nueva.
	:param texto_orden: la orden tal como se le enseña a la persona.
	:param paquete: paquete de npm (solo con :data:`ORIGEN_NPM`).
	"""

	origen: str
	orden: tuple[str, ...]
	texto_orden: str
	paquete: str = ""

	@property
	def se_puede_consultar(self) -> bool:
		"""Si se puede saber la versión disponible sin instalarla (solo npm)."""
		return self.origen == ORIGEN_NPM


@dataclass(frozen=True, slots=True)
class VersionDisponible:
	"""Resultado de consultar la versión disponible de un paquete."""

	version: str = ""
	error: str = ""
	consultada: float = 0.0


def paquete_valido(paquete: str) -> bool:
	"""Si ``paquete`` tiene la forma de un nombre de paquete de npm (y es seguro en una orden)."""
	return bool(_RE_PAQUETE.match(paquete or ""))


def _nombre_paquete(carpeta: Path) -> str:
	try:
		datos = json.loads((carpeta / "package.json").read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return ""
	nombre = datos.get("name") if isinstance(datos, dict) else None
	return nombre if isinstance(nombre, str) and paquete_valido(nombre) else ""


def paquete_npm_de(ruta: str | Path) -> str:
	"""Paquete de npm instalado globalmente al que pertenece ``ruta``, o ``""``.

	Se busca el paquete más externo bajo un ``node_modules`` (el que se instaló
	con ``npm install -g``): Codex, por ejemplo, trae su ``codex.exe`` dentro de
	``node_modules/@openai/codex/vendor/...`` o de un paquete opcional anidado.
	"""
	try:
		camino = Path(ruta)
	except TypeError:
		return ""
	partes = camino.parts
	for indice, parte in enumerate(partes):
		if parte.lower() != "node_modules" or indice + 1 >= len(partes):
			continue
		siguiente = partes[indice + 1]
		if siguiente.startswith("@"):
			if indice + 2 >= len(partes):
				continue
			carpeta = Path(*partes[: indice + 3])
		else:
			carpeta = Path(*partes[: indice + 2])
		nombre = _nombre_paquete(carpeta)
		if nombre:
			return nombre
	return ""


def _comillas_powershell(texto: str) -> str:
	"""``texto`` entre comillas simples de PowerShell (dentro solo cuenta la comilla simple, que se dobla)."""
	return "'" + texto.replace("'", "''") + "'"


def _consola_powershell(ruta: str, *argumentos: str) -> tuple[str, ...]:
	"""Orden que ejecuta ``ruta argumentos`` en PowerShell y espera a Intro para que se pueda leer el resultado."""
	partes = " ".join([_comillas_powershell(ruta), *(_comillas_powershell(a) for a in argumentos)])
	return (
		"powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
		f"try {{ & {partes} }} catch {{ Write-Host $_ }} finally {{ Read-Host 'Pulsa Intro para cerrar' }}",
	)


def forma_de_actualizar(identificador: str, estado: EstadoProveedor | None) -> FormaActualizar | None:
	"""Cómo se actualiza el agente ``identificador`` detectado como ``estado``; ``None`` si no se sabe.

	Nunca ejecuta nada: mira la ruta del programa y, como mucho, lee un ``package.json``.
	"""
	if estado is None or not estado.disponible or not estado.ruta or "://" in estado.ruta:
		return None
	ruta = estado.ruta
	paquete = paquete_npm_de(ruta)
	if paquete:
		texto = f"npm install -g {paquete}@latest"
		# Texto fijo y un nombre de paquete validado: nada que cmd.exe pueda reinterpretar.
		return FormaActualizar(ORIGEN_NPM, ("cmd.exe", "/k", texto), texto, paquete)
	nombre = Path(ruta).stem.lower()
	if nombre == "claude" and Path(ruta).suffix.lower() in (".exe", ""):
		return FormaActualizar(ORIGEN_CLAUDE, _consola_powershell(ruta, "update"), "claude update")
	if nombre == "agy" and Path(ruta).suffix.lower() in (".exe", ""):
		return FormaActualizar(ORIGEN_AGY, _consola_powershell(ruta, "update"), "agy update")
	log.debug("No se sabe actualizar %s desde %s", identificador, ruta)
	return None


# --------------------------------------------------------------------------
# Versiones
# --------------------------------------------------------------------------


def numeros_version(texto: str) -> tuple[int, int, int] | None:
	"""(mayor, menor, parche) de la primera versión estable de ``texto``; ``None`` si no hay o es preliminar."""
	coincidencia = _RE_VERSION.search(texto or "")
	if not coincidencia or coincidencia.group(4):
		return None
	mayor, menor, parche = coincidencia.group(1, 2, 3)
	return int(mayor), int(menor), int(parche or 0)


def hay_version_nueva(instalada: str, disponible: str) -> bool | None:
	"""``True`` si ``disponible`` es más nueva que ``instalada``; ``None`` si no se pueden comparar."""
	a, b = numeros_version(instalada), numeros_version(disponible)
	if a is None or b is None:
		return None
	return b > a


def orden_npm() -> list[str] | None:
	"""npm como binario nativo: ``[node.exe, npm-cli.js]`` (nunca ``npm.cmd``), o ``None`` si no hay."""
	from app.services.agentes import acp  # aquí: acp es grande y solo hace falta al consultar

	node = acp.buscar_node()
	if node is None:
		return None
	candidatos = [node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"]
	candidatos += [carpeta / "npm" / "bin" / "npm-cli.js" for carpeta in acp.carpetas_npm_globales()]
	for candidato in candidatos:
		try:
			if candidato.is_file():
				return [str(node), str(candidato)]
		except OSError:
			log.debug("No se pudo comprobar %s", candidato, exc_info=True)
	return None


Ejecutor = Callable[[Sequence[str], float], tuple[int, str, str]]


def _ejecutar(orden: Sequence[str], tiempo: float) -> tuple[int, str, str]:
	return procesos.ejecutar_y_leer(orden, tiempo)


def version_npm(paquete: str, ejecutar: Ejecutor | None = None, npm: Sequence[str] | None = None,
		tiempo: float = TIEMPO_NPM) -> str:
	"""Última versión publicada de ``paquete`` (``npm view <paquete> version --json``). Bloquea: llamar desde un hilo.

	Lanza :class:`ErrorActualizacion` con el motivo si no se puede saber.
	"""
	if not paquete_valido(paquete):
		raise ErrorActualizacion("El nombre del paquete de npm no es válido.")
	base = list(npm) if npm else orden_npm()
	if not base:
		raise ErrorActualizacion("No se ha encontrado npm (Node.js) en este equipo.")
	try:
		codigo, salida, error = (ejecutar or _ejecutar)([*base, "view", paquete, "version", "--json"], tiempo)
	except (OSError, procesos.subprocess.SubprocessError) as fallo:
		raise ErrorActualizacion("npm no respondió a tiempo.") from fallo
	if codigo != 0:
		ultima = (error.strip().splitlines() or ["error desconocido"])[-1]
		raise ErrorActualizacion("npm no pudo consultar la versión: " + ultima[:200])
	texto = (salida or "").strip()
	try:
		valor = json.loads(texto)
	except ValueError:
		valor = texto.strip('"')
	if isinstance(valor, list):  # con varias etiquetas, npm puede devolver una lista: la última es la más nueva
		valor = valor[-1] if valor else ""
	if not isinstance(valor, str) or not _RE_VERSION.search(valor):
		raise ErrorActualizacion("npm no devolvió una versión que se entienda.")
	return valor.strip()


class ComprobadorVersiones:
	"""Versiones disponibles de los paquetes de npm, guardadas :data:`VIGENCIA` segundos.

	:param consultar: ``f(paquete) -> versión`` (de fábrica, :func:`version_npm`); lanza
		:class:`ErrorActualizacion` si no se puede saber.
	"""

	def __init__(self, consultar: Callable[[str], str] | None = None, vigencia: float = VIGENCIA,
			reloj: Callable[[], float] = time.monotonic) -> None:
		self._consultar = consultar or version_npm
		self.vigencia = vigencia
		self._reloj = reloj
		self._candado = threading.Lock()
		self._guardadas: dict[str, VersionDisponible] = {}
		self._en_curso: dict[str, threading.Event] = {}

	def guardada(self, paquete: str) -> VersionDisponible | None:
		"""La respuesta guardada y vigente para ``paquete``, sin consultar nada."""
		with self._candado:
			resultado = self._guardadas.get(paquete)
		if resultado is not None and self._reloj() - resultado.consultada < self.vigencia:
			return resultado
		return None

	def consultar(self, paquete: str, forzar: bool = False) -> VersionDisponible:
		"""Versión disponible de ``paquete`` (de la caché si es reciente). Bloquea: llamar desde un hilo."""
		if not forzar:
			guardada = self.guardada(paquete)
			if guardada is not None:
				return guardada
		with self._candado:
			evento = self._en_curso.get(paquete)
			propia = evento is None
			if propia:
				evento = threading.Event()
				self._en_curso[paquete] = evento
		if not propia:
			evento.wait(TIEMPO_NPM + 5)
			with self._candado:
				return self._guardadas.get(paquete) or VersionDisponible(error="La consulta no terminó a tiempo.")
		try:
			try:
				resultado = VersionDisponible(version=self._consultar(paquete), consultada=self._reloj())
			except ErrorActualizacion as error:
				resultado = VersionDisponible(error=str(error), consultada=self._reloj())
			except Exception as error:  # noqa: BLE001 - una consulta rota no debe tumbar la lista de agentes
				log.warning("No se pudo consultar la versión de %s", paquete, exc_info=True)
				resultado = VersionDisponible(error=f"Error inesperado: {error}", consultada=self._reloj())
			with self._candado:
				self._guardadas[paquete] = resultado
			return resultado
		finally:
			with self._candado:
				self._en_curso.pop(paquete, None)
			evento.set()

	def olvidar(self, paquete: str | None = None) -> None:
		"""Quita de la caché ``paquete`` (o todos)."""
		with self._candado:
			if paquete is None:
				self._guardadas.clear()
			else:
				self._guardadas.pop(paquete, None)


#: El comprobador que comparte toda la aplicación (la caché sobrevive a cerrar y abrir el diálogo).
COMPROBADOR = ComprobadorVersiones()
