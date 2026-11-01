"""Chats conservados: copias propias de las conversaciones de Claude Code antes de que se borren.

Claude Code borra sus transcripciones pasados ``cleanupPeriodDays`` días (30 de
fábrica) y una sesión que otro programa deja de listar desaparecería de Ctrl+IA.
Este servicio copia esos JSONL a ``data/conservadas/claude/<id>/``, nunca al
revés: jamás se escribe en la carpeta del agente.

- ``transcripcion.jsonl``: la copia.
- ``registro.json``: de qué sesión es (título, carpeta, fechas...) y cómo estaba
  el original al copiarlo (tamaño, fecha e inodo).
- ``versiones/``: lo que había antes si el original se reescribió en vez de
  crecer (al compactar o al retroceder). Nada de lo copiado se pierde.

Quien lee el historial pide las copias con ``anotar``; un único hilo de prioridad
baja las hace. Una transcripción casi siempre solo crece, así que se añaden los
bytes nuevos y no se vuelve a copiar entera cada pocos segundos. Si algo no
cuadra (más corta, otro archivo, los últimos bytes no coinciden), se copia
entera a un temporal y solo al acabar sustituye a la anterior: una copia fallida
nunca estropea la última buena.

Es conservar en el mismo disco, no una copia de seguridad: las copias no
caducan ni van cifradas, y «Hacer una copia ahora» las incluye.

Sin wx. No conoce el formato de Claude Code: para este módulo la transcripción
es un archivo opaco; ``app/services/historial/claude.py`` decide qué se conserva
y cómo se muestra.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.core.paths import ruta_conservadas
from app.core.tareas import _bajar_prioridad_hilo

log = logging.getLogger(__name__)

#: Un identificador acaba en un nombre de carpeta: nada que no sea un id simple.
_ID_SEGURO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
TRANSCRIPCION = "transcripcion.jsonl"
REGISTRO = "registro.json"
VERSIONES = "versiones"
_TROZO = 1024 * 1024
#: Últimos bytes de la copia que deben coincidir con el original para fiarse de que solo ha crecido.
_COMPROBAR_COLA = 64 * 1024
#: Respiro entre dos copias, para no acaparar el disco la primera vez, con miles de sesiones.
_PAUSA = 0.02
#: Lo que se espera a la copia en curso al cerrar.
_ESPERA_CIERRE = 2.0


class _Cerrando(Exception):
	"""El conservador se ha cerrado a mitad de una copia."""


def _escribir_json(ruta: Path, datos: dict[str, Any]) -> None:
	"""Escritura atómica: un corte deja el archivo viejo o el nuevo, nunca uno a medias."""
	temporal = ruta.with_name(f"{ruta.name}.{uuid.uuid4().hex[:8]}.tmp")
	try:
		with open(temporal, "w", encoding="utf-8") as archivo:
			json.dump(datos, archivo, ensure_ascii=False, indent=1)
			archivo.flush()
			os.fsync(archivo.fileno())
		os.replace(temporal, ruta)
	finally:
		temporal.unlink(missing_ok=True)


class Conservador:
	"""Copias de una carpeta raíz, con su único hilo de trabajo. Seguro desde varios hilos."""

	def __init__(self, raiz: Path) -> None:
		self.raiz = Path(raiz)
		self._condicion = threading.Condition(threading.RLock())
		self._pendientes: OrderedDict[str, tuple[Path, dict[str, Any]]] = OrderedDict()
		self._activa = False
		self._cerrojo_copia = threading.Lock()
		self._cerrado = False
		self._hilo: threading.Thread | None = None
		self._registros: dict[str, dict[str, Any]] = {}
		self._olvidadas: set[str] = set()
		self._por_borrar: set[str] = set()
		#: Lo que falló, por identificador: se cuenta en el texto de Preferencias y va al registro.
		self.problemas: dict[str, str] = {}
		self._cargar()

	def _cargar(self) -> None:
		"""Lee los registros y quita lo que dejó a medias un cierre brusco."""
		try:
			for sobrante in self.raiz.glob("*/copia-*.tmp"):
				sobrante.unlink(missing_ok=True)
			rutas = sorted(self.raiz.glob(f"*/{REGISTRO}"))
		except OSError:
			log.warning("No se pudo leer la carpeta de chats conservados %s", self.raiz, exc_info=True)
			self.problemas["catalogo"] = "No se pudo leer la carpeta de chats conservados."
			return
		for ruta in rutas:
			try:
				registro = json.loads(ruta.read_text(encoding="utf-8"))
				identificador = registro["id"]
				if identificador != ruta.parent.name or not isinstance(registro["metadatos"], dict):
					raise ValueError("registro de otra carpeta")
				if self._ruta(identificador).is_file():
					self._registros[identificador] = registro
			except (OSError, ValueError, KeyError, TypeError):
				log.warning("Registro de chat conservado ilegible: %s", ruta, exc_info=True)
				self.problemas[str(ruta)] = "No se pudo leer el registro de un chat conservado."

	def _ruta(self, identificador: str) -> Path:
		# El identificador se comprueba aquí: un registro no puede elegir un archivo cualquiera.
		if not _ID_SEGURO.match(identificador or ""):
			raise ValueError("Identificador de sesión no válido")
		return self.raiz / identificador / TRANSCRIPCION

	# ── Consultas ─────────────────────────────────────────────────────────────

	def registros(self) -> dict[str, dict[str, Any]]:
		"""``{id: metadatos}`` de todo lo conservado."""
		with self._condicion:
			return {identificador: dict(registro["metadatos"]) for identificador, registro in self._registros.items()}

	def ruta_copia(self, identificador: str) -> Path | None:
		"""La copia de una sesión, o ``None`` si no hay."""
		if not _ID_SEGURO.match(identificador or ""):
			return None
		with self._condicion:
			if identificador not in self._registros:
				return None
		ruta = self._ruta(identificador)
		return ruta if ruta.is_file() else None

	def al_dia(self, identificador: str, metadatos: dict[str, Any]) -> bool:
		"""Si no hay nada nuevo que copiar, sin tocar el disco: la lista lo pregunta cada pocos segundos."""
		with self._condicion:
			registro = self._registros.get(identificador)
			return registro is not None and registro.get("metadatos") == metadatos

	def resumen(self) -> tuple[int, int]:
		"""``(conversaciones conservadas, bytes)``, según los registros (sin recorrer el disco)."""
		with self._condicion:
			total = 0
			for registro in self._registros.values():
				sello = registro.get("sello")
				if isinstance(sello, list) and sello and isinstance(sello[0], int):
					total += sello[0]
			return len(self._registros), total

	# ── Encargar copias ───────────────────────────────────────────────────────

	def anotar(self, identificador: str, origen: Path, metadatos: dict[str, Any]) -> None:
		"""Encarga copiar ``origen``; una petición nueva de la misma sesión sustituye a la anterior."""
		if not _ID_SEGURO.match(identificador or ""):
			return
		with self._condicion:
			if self._cerrado or identificador in self._olvidadas:
				return
			self._pendientes[identificador] = (Path(origen), dict(metadatos))
			if self._hilo is None:
				# El hilo acaba al vaciarse la cola: no queda ninguno esperando mientras no haya trabajo.
				self._hilo = threading.Thread(target=self._trabajar, name="conservar-chats", daemon=True)
				self._hilo.start()
			self._condicion.notify_all()

	def _trabajar(self) -> None:
		_bajar_prioridad_hilo()
		while True:
			with self._condicion:
				if self._cerrado or not self._pendientes:
					self._hilo = None
					self._condicion.notify_all()
					return
				identificador, (origen, metadatos) = self._pendientes.popitem(last=False)
				self._activa = True
			try:
				self.capturar(identificador, origen, metadatos)
			except _Cerrando:
				log.debug("Copia de %s interrumpida al cerrar", identificador)
			except (OSError, ValueError, TypeError) as error:
				log.warning("No se pudo conservar la sesión %s: %s", identificador, error)
				with self._condicion:
					self.problemas[identificador] = f"No se pudo copiar la conversación ({type(error).__name__})."
			finally:
				self._borrar_olvidadas()
				with self._condicion:
					self._activa = False
					self._condicion.notify_all()
					cerrado = self._cerrado
			if not cerrado:
				with self._condicion:
					self._condicion.wait(_PAUSA)

	def esperar_inactivo(self, tiempo: float = 10.0) -> bool:
		"""Espera a que no quede nada por copiar (bloquea; para las pruebas y para antes de cerrar)."""
		with self._condicion:
			return self._condicion.wait_for(lambda: not self._pendientes and not self._activa, tiempo)

	def cerrar(self) -> None:
		"""Se detiene: lo encargado se abandona (se volverá a pedir al abrir) y la copia en curso acaba al momento."""
		with self._condicion:
			self._cerrado = True
			self._pendientes.clear()
			hilo = self._hilo
			self._condicion.notify_all()
		if hilo is not None and hilo is not threading.current_thread():
			hilo.join(timeout=_ESPERA_CIERRE)

	def olvidar(self, identificador: str) -> None:
		"""Borra una copia para siempre. Si se está copiando, la borra el hilo al acabar: nunca se espera."""
		carpeta = self._ruta(identificador).parent
		with self._condicion:
			self._olvidadas.add(identificador)
			self._pendientes.pop(identificador, None)
			self._registros.pop(identificador, None)
			self.problemas.pop(identificador, None)
			self._por_borrar.add(identificador)
		if self._cerrojo_copia.acquire(blocking=False):
			try:
				self._borrar_olvidadas(con_cerrojo=True)
			finally:
				self._cerrojo_copia.release()
		log.info("Chat conservado olvidado: %s", carpeta.name)

	def _borrar_olvidadas(self, con_cerrojo: bool = False) -> None:
		if not con_cerrojo:
			with self._cerrojo_copia:
				self._borrar_olvidadas(con_cerrojo=True)
			return
		with self._condicion:
			borrar, self._por_borrar = self._por_borrar, set()
		for identificador in borrar:
			carpeta = self._ruta(identificador).parent
			try:
				if carpeta.exists():
					shutil.rmtree(carpeta)
			except OSError:
				log.debug("No se pudo borrar %s; se reintentará", carpeta, exc_info=True)
				with self._condicion:
					self._por_borrar.add(identificador)

	# ── Copiar ────────────────────────────────────────────────────────────────

	def capturar(self, identificador: str, origen: Path, metadatos: dict[str, Any]) -> bool:
		"""Copia lo nuevo de ``origen``. Devuelve si cambió la copia."""
		with self._cerrojo_copia:
			return self._capturar(identificador, Path(origen), metadatos)

	def _guardar_registro(self, identificador: str, registro: dict[str, Any], destino: Path) -> None:
		_escribir_json(destino.parent / REGISTRO, registro)
		with self._condicion:
			self._registros[identificador] = registro
			self.problemas.pop(identificador, None)

	def _capturar(self, identificador: str, origen: Path, metadatos: dict[str, Any]) -> bool:
		destino = self._ruta(identificador)
		with self._condicion:
			if identificador in self._olvidadas:
				return False
			anterior = self._registros.get(identificador)
		try:
			estado = origen.stat()
		except FileNotFoundError:
			if anterior is None:
				with self._condicion:
					self.problemas[identificador] = "El original desapareció antes de la primera copia."
			return False
		sello = [estado.st_size, estado.st_mtime_ns, estado.st_ino]
		registro = {"id": identificador, "metadatos": dict(metadatos), "sello": sello}
		if anterior is not None and anterior.get("sello") == sello and destino.is_file():
			if registro != anterior:
				self._guardar_registro(identificador, registro, destino)
			return False
		if anterior is not None and self._anadir_lo_nuevo(origen, destino, anterior.get("sello"), estado):
			self._guardar_registro(identificador, registro, destino)
			return True
		destino.parent.mkdir(parents=True, exist_ok=True)
		descriptor, temporal = tempfile.mkstemp(prefix="copia-", suffix=".tmp", dir=destino.parent)
		try:
			restante = estado.st_size
			with os.fdopen(descriptor, "wb") as salida, origen.open("rb") as entrada:
				abierto = os.fstat(entrada.fileno())
				if abierto.st_ino != estado.st_ino or abierto.st_size < restante:
					raise OSError("La conversación cambió antes de copiarla")
				while restante:
					self._comprobar_cierre()
					trozo = entrada.read(min(_TROZO, restante))
					if not trozo:
						raise OSError("La conversación se acortó mientras se copiaba")
					salida.write(trozo)
					restante -= len(trozo)
				salida.flush()
				os.fsync(salida.fileno())
			if destino.is_file() and not _es_principio(destino, Path(temporal)):
				# El original se reescribió (compactar, retroceder): lo que ya no tiene se guarda aparte.
				versiones = destino.parent / VERSIONES
				versiones.mkdir(exist_ok=True)
				shutil.copyfile(destino, versiones / f"{uuid.uuid4().hex}.jsonl")
			os.replace(temporal, destino)
			self._guardar_registro(identificador, registro, destino)
			return True
		finally:
			Path(temporal).unlink(missing_ok=True)

	def _comprobar_cierre(self) -> None:
		with self._condicion:
			if self._cerrado:
				raise _Cerrando

	def _anadir_lo_nuevo(self, origen: Path, destino: Path, sello: Any, estado: os.stat_result) -> bool:
		"""Añade solo lo que ha crecido. ``False``, sin cambiar nada, si no se puede fiar de que solo creció."""
		try:
			tamano_antes, _fecha, inodo = sello
			if (inodo != estado.st_ino or estado.st_size < tamano_antes or not destino.is_file()
					or destino.stat().st_size != tamano_antes):
				return False
			return self._copiar_cola(origen, destino, tamano_antes, estado.st_size)
		except (TypeError, ValueError, OSError):
			log.debug("No se pudo añadir lo nuevo a %s", destino, exc_info=True)
			return False

	def _copiar_cola(self, origen: Path, destino: Path, tamano_antes: int, tamano_nuevo: int) -> bool:
		with origen.open("rb") as entrada, destino.open("rb+") as salida:
			inicio = max(0, tamano_antes - _COMPROBAR_COLA)
			entrada.seek(inicio)
			salida.seek(inicio)
			if entrada.read(tamano_antes - inicio) != salida.read(tamano_antes - inicio):
				return False
			salida.seek(tamano_antes)
			salida.truncate()
			entrada.seek(tamano_antes)
			restante = tamano_nuevo - tamano_antes
			while restante:
				self._comprobar_cierre()
				trozo = entrada.read(min(_TROZO, restante))
				if not trozo:
					raise OSError("La conversación se acortó mientras se copiaba")
				salida.write(trozo)
				restante -= len(trozo)
			salida.flush()
			os.fsync(salida.fileno())
		return True


def _es_principio(viejo: Path, nuevo: Path) -> bool:
	"""Si ``nuevo`` empieza por todo ``viejo`` (solo ha crecido)."""
	if viejo.stat().st_size > nuevo.stat().st_size:
		return False
	with viejo.open("rb") as antes, nuevo.open("rb") as despues:
		while trozo := antes.read(_TROZO):
			if despues.read(len(trozo)) != trozo:
				return False
	return True


_conservadores: dict[Path, Conservador] = {}
_cerrojo = threading.Lock()


def conservador() -> Conservador:
	"""El conservador de la carpeta ``data`` actual (uno por carpeta, nunca dos sobre la misma)."""
	raiz = ruta_conservadas() / "claude"
	with _cerrojo:
		if raiz not in _conservadores:
			_conservadores[raiz] = Conservador(raiz)
		return _conservadores[raiz]


def cerrar_todos() -> None:
	"""Al salir: detiene todos los hilos."""
	with _cerrojo:
		instancias = list(_conservadores.values())
		_conservadores.clear()
	for instancia in instancias:
		instancia.cerrar()


# ── Cuánto conserva cada agente ──────────────────────────────────────────────

#: Un ajuste de días de Claude Code más grande que esto no es un archivo de ajustes.
_MAX_AJUSTES = 1024 * 1024


def _ajustes_administrador() -> list[Path]:
	"""Dónde puede estar la política de empresa de Claude Code en Windows."""
	archivos = os.environ.get("ProgramFiles", r"C:\Program Files")
	datos = os.environ.get("ProgramData", r"C:\ProgramData")  # auditoria: ignorar ruta-no-portable - política de Claude Code; solo lectura
	return [Path(archivos) / "ClaudeCode" / "managed-settings.json", Path(datos) / "ClaudeCode" / "managed-settings.json"]


def _texto_claude() -> str:
	from app.services.historial.claude import raiz_claude

	valores: dict[str, int] = {}
	problema = False
	# Solo los ajustes globales: los de cada carpeta y las políticas remotas no se pueden saber aquí, y se dice.
	for ruta in (raiz_claude() / "settings.json", *reversed(_ajustes_administrador())):
		try:
			if ruta.stat().st_size > _MAX_AJUSTES:
				raise ValueError("demasiado grande")
			datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
			if not isinstance(datos, dict):
				raise ValueError("no son ajustes")
			for clave, minimo in (("cleanupPeriodDays", 1), ("desktopSessionCleanupPeriodDays", 0)):
				if clave in datos:
					valor = datos[clave]
					if type(valor) is not int or valor < minimo:
						raise ValueError("valor no válido")
					valores[clave] = valor
		except FileNotFoundError:
			continue
		except (OSError, ValueError):
			log.debug("Ajustes de Claude Code ilegibles: %s", ruta, exc_info=True)
			problema = True
	if problema:
		return "Claude Code: no se ha podido saber cuánto guarda, porque uno de sus archivos de ajustes no se puede leer."
	dias = valores.get("cleanupPeriodDays", 30)
	escritorio = valores.get("desktopSessionCleanupPeriodDays", 0)
	texto = (f"Claude Code: guarda las conversaciones {dias} días (cleanupPeriodDays; 30 de fábrica). Los ajustes de una "
		"carpeta o la política de una empresa pueden cambiarlo. ")
	if escritorio:
		return texto + f"Las de la app de escritorio, al menos {max(dias, escritorio)} días."
	return texto + "Las versiones recientes no aplican ese límite a las de la app de escritorio."


def describir_retencion() -> str:
	"""Cuánto guarda cada agente sus conversaciones y qué conserva Ctrl+IA (para Preferencias, Historial)."""
	try:
		claude = _texto_claude()
	except Exception:  # noqa: BLE001 - es un texto de ayuda: nunca debe impedir abrir Preferencias
		log.warning("No se pudo averiguar cuánto guarda Claude Code", exc_info=True)
		claude = "Claude Code: no se ha podido saber cuánto guarda."
	cuantas, octetos = conservador().resumen()
	problemas = len(conservador().problemas)
	lineas = [
		claude,
		"Codex: no tiene un límite automático documentado para sus conversaciones.",
		"Gemini CLI: 30 días de fábrica en sus versiones recientes (general.sessionRetention.maxAge).",
		"Antigravity: no tiene un límite automático documentado.",
		"Ctrl+IA guarda sus propias sesiones en su base de datos y no las borra nunca.",
		f"Conversaciones de Claude Code conservadas aquí: {cuantas} ({tamano_legible(octetos)}). Las copias no caducan, no "
		"van cifradas y, cuando el original desaparece, se leen sin poder cambiarlas. «Hacer una copia ahora» las incluye.",
	]
	if problemas:
		lineas.append(f"Copias con problemas: {problemas} (más detalles en el registro).")
	return "\n".join(lineas)


def tamano_legible(octetos: float) -> str:
	"""«12 bytes», «3,4 MB»: con coma decimal, como lo lee una voz española."""
	valor = float(octetos)
	for unidad in ("bytes", "KB", "MB", "GB"):
		if valor < 1024 or unidad == "GB":
			return f"{int(valor)} bytes" if unidad == "bytes" else f"{valor:.1f} {unidad}".replace(".", ",")
		valor /= 1024
	return f"{int(octetos)} bytes"
