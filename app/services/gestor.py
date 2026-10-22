"""Gestor de sesiones: el centro de Ctrl+IA, sin interfaz.

Une tres fuentes en una sola lista:

- Las sesiones propias (creadas o continuadas desde Ctrl+IA), guardadas en la
  base de datos con todo lo que se ha dicho.
- Las sesiones que ya existen en los agentes (historial de Claude Code, hilos
  de Codex, conversaciones de la app Antigravity), que solo se leen. También
  las de Claude Code y Codex de los equipos por SSH (``historial/equipos.py``),
  leídas en segundo plano: la lista nunca espera a otro equipo.
- Las conversaciones vivas con cada agente.

Los eventos de las conversaciones llegan desde hilos de fondo: el gestor los
guarda y los reenvía tal cual a ``al_evento(clave, evento)``. La interfaz es la
responsable de volver al hilo principal.
"""

from __future__ import annotations

import logging
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.agentes import remoto
from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	ClaseOpcion,
	Conversacion,
	Estado,
	Evento,
	OpcionPermiso,
	Proveedor,
	RespuestaPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.almacen import LEIDA_NUNCA, PREFIJO_IMPORTADA, Almacen, SesionPropia, nueva_clave
from app.services.puntos import MixinPuntos

log = logging.getLogger(__name__)

#: De dónde viene cada sesión (texto para la columna «Origen»).
NOMBRES_ORIGEN = {
	"propia": "Ctrl+IA",
	"terminal": "Terminal",
	"escritorio": "App de Claude",
	"cowork": "Cowork",
	"ctrlia": "Ctrl+IA",
	"codex": "Codex",
	"antigravity": "App Antigravity",
	"antigravity_cli": "Antigravity CLI",
	"ollama_app": "App de Ollama",
	"importada": "Importada",
	"otra": "Otra",
}

#: Margen (segundos) al comparar la actividad con «leída hasta» o «terminada»:
#: abrir una sesión justo cuando el agente escribe no debe dejarla como nueva.
MARGEN_ACTIVIDAD = 2.0
#: Si la sesión original tuvo actividad hace menos de esto (segundos), se da por «en uso» y no se sigue.
RECIENTE_EN_USO = 120.0
#: Agentes cuya sesión original se puede seguir (``--resume`` sin copia, ``thread/resume``).
AGENTES_CON_ORIGINAL = ("claude", "codex")
#: Por qué no se pueden leer los mensajes de una sesión de otro programa (``FilaSesion.sin_conversacion``).
TEXTO_SIN_CONVERSACION = (
	"El otro programa ya no guarda los mensajes de esta sesión: Claude Code borra las "
	"conversaciones antiguas pasado un tiempo (30 días, si no se ha cambiado), o la sesión se creó y no llegó "
	"a tener mensajes. Se conservan su título, su carpeta y sus fechas."
)


class SesionEnUso(ValueError):
	"""La sesión original está abierta o se ha usado hace muy poco en otro programa: seguirla a la vez las mezclaría."""

ROLES = {
	"usuario": "Tú",
	"asistente": "Agente",
	"herramienta": "Herramienta",
	"sistema": "Aviso",
	"error": "Error",
	"cambio": "Cambio",
	"plan": "Plan",
	"razonamiento": "Razonamiento",
}


@dataclass(slots=True)
class FilaSesion:
	"""Una sesión tal como aparece en la lista principal."""

	clave: str
	proveedor: str
	titulo: str
	carpeta: str = ""
	origen: str = "propia"
	estado: str = Estado.INACTIVA
	actividad: float = 0.0
	creada: float = 0.0
	no_leidas: int = 0
	oculta: bool = False
	favorita: bool = False
	archivada: bool = False
	grupos: list[str] = field(default_factory=list)
	ultimo_mensaje: str = ""
	id_externo: str = ""
	modelo: str = ""
	#: Dada por terminada: ya no sale en «Te necesitan» ni cuenta como respuesta nueva.
	terminada: bool = False
	#: Equipo del que viene una sesión importada o, si no, equipo por SSH al que pertenece (vacío: este equipo).
	equipo: str = ""
	#: Sistema de ese equipo por SSH: «macOS» o «Linux».
	sistema: str = ""
	#: Sesión de otro programa cuya conversación ya no está en el disco (solo quedan sus datos).
	sin_conversacion: bool = False

	@property
	def propia(self) -> bool:
		return self.clave.startswith("ctrlia:")

	@property
	def importada(self) -> bool:
		return self.clave.startswith(PREFIJO_IMPORTADA)

	@property
	def remota(self) -> bool:
		"""Sesión de otro equipo por SSH: propia que trabaja allí o que ya existía allí."""
		return bool(self.equipo) and self.origen != "importada"

	@property
	def nombre_origen(self) -> str:
		if self.origen == "importada" and self.equipo:
			return f"Importada de {self.equipo}"
		if self.remota:
			return f"{NOMBRES_ORIGEN.get(self.origen, self.origen)} en {self.equipo} (SSH)"
		return NOMBRES_ORIGEN.get(self.origen, self.origen)

	@property
	def nombre_equipo(self) -> str:
		"""A qué equipo pertenece: «Este equipo (Windows)», «Mac del estudio (macOS)»..."""
		if not self.equipo:
			return "Este equipo (Windows)"
		return f"{self.equipo} ({self.sistema})" if self.sistema else self.equipo


@dataclass(slots=True)
class MensajeVista:
	"""Un mensaje listo para mostrar."""

	rol: str
	texto: str
	fecha: float
	detalle: str = ""
	herramienta: str = ""
	id: int | str = 0
	cambios: list[dict[str, str]] = field(default_factory=list)
	#: Datos guardados con el mensaje (solo en las sesiones de Ctrl+IA): ``solicitudes``
	#: (preguntas, permisos y planes que pidió el agente y lo que respondiste), ``adjuntos``,
	#: ``detenido``... No cuenta al comparar mensajes.
	datos: dict[str, Any] = field(default_factory=dict, compare=False)

	@property
	def nombre_rol(self) -> str:
		if self.rol == "herramienta" and self.herramienta:
			return "Herramienta"
		return ROLES.get(self.rol, self.rol)


def normalizar(texto: str) -> str:
	"""Minúsculas y sin acentos (para buscar «canción» con «cancion»)."""
	descompuesto = unicodedata.normalize("NFD", texto.casefold())
	return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def coincide(texto: str, consulta: str) -> bool:
	"""Todas las palabras de ``consulta`` aparecen en ``texto``."""
	texto_n = normalizar(texto)
	return all(palabra in texto_n for palabra in normalizar(consulta).split())


class GestorSesiones(MixinPuntos):
	"""Orquesta sesiones, agentes y almacenamiento. Seguro desde varios hilos.

	Los puntos de restauración («Deshacer los cambios de archivos desde este
	mensaje») están en ``MixinPuntos`` (``app/services/puntos.py``).
	"""

	def __init__(
		self,
		almacen: Almacen,
		proveedores: dict[str, Proveedor],
		al_evento: Callable[[str, Evento], None],
		opciones: Callable[[str, Any], Any] | None = None,
	) -> None:
		self.almacen = almacen
		self.proveedores = proveedores
		self._al_evento = al_evento
		self._opcion = opciones or (lambda clave, defecto: defecto)
		self._candado = threading.RLock()
		self._conversaciones: dict[str, Conversacion] = {}
		self._solicitudes: dict[str, dict[str, SolicitudPermiso]] = {}
		self._ids_mensaje: dict[tuple[str, str], int] = {}
		#: Mensaje en el que se apuntó cada petición del agente: (sesión, id de la petición) → id del mensaje.
		self._mensaje_de_solicitud: dict[tuple[str, str], int] = {}
		#: Lo que se lee y reescribe en ``datos["solicitudes"]`` no se pisa entre hilos.
		self._candado_solicitudes = threading.Lock()
		self._parciales: dict[str, str] = {}
		self._uso: dict[str, dict[str, Any]] = {}
		self._limites: dict[str, dict[str, Any]] = {}
		#: Lo último preguntado con «Límites de uso de tus cuentas» (``LimitesCuenta`` por agente).
		self._limites_cuenta: dict[str, Any] = {}
		self._comandos: dict[str, list[tuple[str, str]]] = {}
		self._lectores: dict[str, Any] = {}
		self._externas: dict[str, Any] = {}
		self._estados_proveedor: dict[str, Any] = {}
		#: Sesión que el usuario tiene abierta (no suma «sin leer»).
		self.sesion_abierta = ""
		from app.services.historial.equipos import HistorialEquipos

		#: Sesiones que ya existen en los equipos por SSH, leídas en segundo plano.
		self.historial_equipos = HistorialEquipos(self._opcion, listar_codex=self._listar_codex_equipo,
			al_cambiar=lambda: self.al_cambiar_equipos() if self.al_cambiar_equipos is not None else None)
		#: La interfaz lo fija para volver a pintar la lista cuando llegan sesiones de otro equipo (desde un hilo).
		self.al_cambiar_equipos: Callable[[], None] | None = None
		self._forzar_equipos = False
		self._iniciar_puntos()

	# ------------------------------------------------------------------
	# Agentes
	# ------------------------------------------------------------------

	def proveedor(self, identificador: str) -> Proveedor | None:
		return self.proveedores.get(identificador)

	def detectar(self, identificador: str) -> Any:
		"""Detecta un agente (bloquea): llamar desde un hilo."""
		proveedor = self.proveedores.get(identificador)
		if proveedor is None:
			return None
		try:
			estado = proveedor.detectar()
		except Exception:  # noqa: BLE001 - un detector roto no debe impedir ver los demás
			log.exception("Falló la detección de %s", identificador)
			return None
		self._estados_proveedor[identificador] = estado
		return estado

	def estado_proveedor(self, identificador: str) -> Any:
		return self._estados_proveedor.get(identificador)

	def comandos(self, proveedor: str) -> list[tuple[str, str]]:
		return list(self._comandos.get(proveedor, []))

	def uso(self, clave: str) -> dict[str, Any]:
		return dict(self._uso.get(clave, {}))

	def limites(self, proveedor: str) -> dict[str, Any]:
		return dict(self._limites.get(proveedor, {}))

	def limites_cuenta(self, proveedor: str) -> Any:
		"""Lo último que se preguntó con :meth:`leer_limites_todos` (un ``LimitesCuenta``) o ``None``."""
		return self._limites_cuenta.get(proveedor)

	def leer_limites_todos(self, tiempo: float = 90.0) -> list[Any]:
		"""Pregunta a la vez los límites de todos los agentes que saben darlos (bloquea: llamar desde un hilo).

		Devuelve un ``app.services.limites.ResultadoAgente`` por agente, en el
		orden de la lista de agentes. Un agente que no informa (``None``) no sale.
		"""
		from concurrent.futures import ThreadPoolExecutor, wait

		from app.services import limites

		def leer(proveedor: Proveedor) -> Any:
			try:
				return proveedor.leer_limites()
			except limites.ErrorLimites as error:
				return error
			except Exception as error:  # noqa: BLE001 - un agente roto no debe impedir ver los demás
				log.exception("No se pudieron leer los límites de %s", proveedor.id)
				return limites.ErrorLimites(f"No se han podido leer: {error}")

		proveedores = list(self.proveedores.values())
		grupo = ThreadPoolExecutor(max_workers=max(1, len(proveedores)), thread_name_prefix="limites")
		try:
			futuros = {proveedor.id: grupo.submit(leer, proveedor) for proveedor in proveedores}
			wait(futuros.values(), timeout=tiempo)
		finally:
			grupo.shutdown(wait=False, cancel_futures=True)
		resultados = []
		for proveedor in proveedores:
			futuro = futuros[proveedor.id]
			if not futuro.done():
				valor: Any = limites.ErrorLimites("No ha contestado a tiempo.")
			else:
				valor = futuro.result()
			if valor is None:
				continue
			pasivos = [str(datos.get("texto") or "") for datos in self.limites(proveedor.id).values() if datos.get("texto")]
			if isinstance(valor, limites.ErrorLimites):
				resultados.append(limites.ResultadoAgente(proveedor.nombre, error=str(valor), durante_sesion=pasivos))
			else:
				self._limites_cuenta[proveedor.id] = valor
				resultados.append(limites.ResultadoAgente(proveedor.nombre, valor))
		return resultados

	def texto_parcial(self, clave: str) -> str:
		return self._parciales.get(clave, "")

	def solicitudes(self, clave: str | None = None) -> list[tuple[str, SolicitudPermiso]]:
		"""Peticiones de permiso pendientes (de una sesión o de todas)."""
		with self._candado:
			if clave is not None:
				return [(clave, s) for s in self._solicitudes.get(clave, {}).values()]
			return [(c, s) for c, pendientes in self._solicitudes.items() for s in pendientes.values()]

	# ------------------------------------------------------------------
	# Lista de sesiones
	# ------------------------------------------------------------------

	def leer_equipos_ya(self) -> None:
		"""La próxima ``listar`` vuelve a leer los equipos por SSH sin esperar su intervalo (F5)."""
		self._forzar_equipos = True

	def listar(self) -> list[FilaSesion]:
		"""Todas las sesiones conocidas (bloquea: lee disco y agentes)."""
		metadatos = self.almacen.metadatos()
		equipos = remoto.equipos(self._opcion)
		grupos = self.almacen.grupos_por_sesion()
		filas: list[FilaSesion] = []
		with self._candado:
			vivas = {clave: conv.estado for clave, conv in self._conversaciones.items()}
			con_solicitudes = {clave for clave, pendientes in self._solicitudes.items() if pendientes}
		for sesion in self.almacen.sesiones():
			estado = vivas.get(sesion.clave, Estado.INACTIVA)
			if sesion.clave in con_solicitudes:
				estado = Estado.ESPERANDO_USUARIO
			importada = sesion.clave.startswith(PREFIJO_IMPORTADA)
			datos_importada = sesion.extra.get("importada") if importada else None
			if not isinstance(datos_importada, dict):
				datos_importada = {}
			filas.append(FilaSesion(
				clave=sesion.clave, proveedor=sesion.proveedor, titulo=sesion.titulo or "Sesión sin título",
				carpeta=sesion.carpeta, origen="importada" if importada else "propia", estado=estado,
				actividad=sesion.actividad, creada=sesion.creada, no_leidas=0 if importada else sesion.no_leidas,
				id_externo=sesion.id_externo, modelo=sesion.modelo,
				ultimo_mensaje=_recortar(self.almacen.ultimo_mensaje(sesion.clave)),
				equipo=str(datos_importada.get("equipo") or (remoto.datos_remotos(sesion.extra) or {}).get("nombre") or ""),
				sistema=_sistema_de(equipos, sesion.extra),
			))
		filas.extend(self._listar_externas())
		leidas_nuevas: dict[str, float] = {}
		for fila in filas:
			meta = metadatos.get(fila.clave)
			if meta is not None:
				if meta.titulo:
					fila.titulo = meta.titulo
				fila.oculta = meta.oculta
				fila.favorita = meta.favorita
				fila.terminada = bool(meta.terminada) and fila.actividad <= meta.terminada + MARGEN_ACTIVIDAD
			fila.grupos = grupos.get(fila.clave, [])
			if not fila.propia and not fila.importada:
				self._calcular_sin_leer(fila, meta, leidas_nuevas)
			if fila.terminada:
				fila.no_leidas = 0
		if leidas_nuevas:
			try:
				self.almacen.fijar_leida_hasta(leidas_nuevas)
			except Exception:  # noqa: BLE001 - no guardar la marca no debe vaciar la lista
				log.warning("No se pudo guardar hasta cuándo están leídas las sesiones", exc_info=True)
		return filas

	def _calcular_sin_leer(self, fila: FilaSesion, meta: Any, leidas_nuevas: dict[str, float]) -> None:
		"""«Respuesta nueva» de una sesión de otro programa: actividad posterior a la última vez que se leyó aquí.

		Ctrl+IA no puede saber si leíste la respuesta en el otro programa; solo
		compara con la última vez que abriste la sesión aquí (``leida_hasta``).
		La primera vez que ve una sesión la da por leída, para no marcar como
		nuevo todo el historial. Lo apunta en sus propios datos, nunca en los del agente.
		"""
		leida_hasta = meta.leida_hasta if meta is not None else 0.0
		if leida_hasta == LEIDA_NUNCA:
			fila.no_leidas = 1
		elif not leida_hasta or fila.clave == self.sesion_abierta:
			if fila.actividad and fila.actividad > leida_hasta:
				leidas_nuevas[fila.clave] = fila.actividad
		elif fila.actividad > leida_hasta + MARGEN_ACTIVIDAD and fila.estado not in (Estado.TRABAJANDO, Estado.SEGUNDO_PLANO):
			fila.no_leidas = 1

	def _listar_externas(self) -> list[FilaSesion]:
		filas: list[FilaSesion] = []
		externas: dict[str, Any] = {}
		if self._opcion("historial.claude", True) and "claude" in self.proveedores:
			try:
				from app.services.historial import claude as historial_claude

				propios = self.almacen.ids_externos("claude")
				for sesion in historial_claude.listar_sesiones(propios, incluir_archivadas=True):
					if sesion.id in propios:
						continue
					externas[sesion.clave] = sesion
					filas.append(FilaSesion(
						clave=sesion.clave, proveedor="claude", titulo=sesion.titulo or "Sesión de Claude",
						carpeta=sesion.carpeta, origen=sesion.origen, estado=sesion.estado, actividad=sesion.actividad,
						creada=sesion.creada, archivada=sesion.archivada, id_externo=sesion.id,
						ultimo_mensaje=_recortar(sesion.ultimo_mensaje), sin_conversacion=sesion.ruta_transcript is None,
					))
			except Exception:  # noqa: BLE001 - un historial ilegible no debe vaciar la lista
				log.exception("No se pudo leer el historial de Claude Code")
		proveedor_codex = self.proveedores.get("codex")
		if self._opcion("historial.codex", True) and proveedor_codex is not None and hasattr(proveedor_codex, "listar_hilos"):
			estado = self._estados_proveedor.get("codex")
			if estado is not None and getattr(estado, "disponible", False):
				try:
					propios = self.almacen.ids_externos("codex")
					for hilo in proveedor_codex.listar_hilos():
						identificador = str(hilo.get("id") or "")
						if not identificador or identificador in propios:
							continue
						clave = f"codex:{identificador}"
						externas[clave] = hilo
						filas.append(FilaSesion(
							clave=clave, proveedor="codex", titulo=str(hilo.get("titulo") or "Hilo de Codex"),
							carpeta=str(hilo.get("carpeta") or ""), origen="codex",
							estado=str(hilo.get("estado") or Estado.INACTIVA), actividad=float(hilo.get("actualizado") or 0),
							creada=float(hilo.get("creado") or 0), id_externo=identificador,
						))
				except Exception:  # noqa: BLE001 - Codex puede no estar disponible o no tener sesión
					log.warning("No se pudieron leer los hilos de Codex", exc_info=True)
		if self._opcion("historial.antigravity", True):
			try:
				from app.services.historial import antigravity as historial_antigravity

				propios = self.almacen.ids_externos("antigravity")
				for conversacion in historial_antigravity.listar_conversaciones(propios=propios):
					identificador = str(conversacion.get("id") or "")
					if not identificador:
						continue
					clave = f"antigravity:{identificador}"
					externas[clave] = conversacion
					filas.append(FilaSesion(
						clave=clave, proveedor="antigravity",
						titulo=str(conversacion.get("titulo") or "Conversación de Antigravity"),
						carpeta=str(conversacion.get("carpeta") or ""), origen=str(conversacion.get("origen") or "antigravity"),
						actividad=_segundos(conversacion.get("fecha")), creada=_segundos(conversacion.get("fecha")),
						id_externo=identificador,
					))
			except Exception:  # noqa: BLE001 - los datos de Antigravity no tienen formato documentado
				log.warning("No se pudieron leer las conversaciones de Antigravity", exc_info=True)
		if self._opcion("historial.ollama", True) and "ollama" in self.proveedores:
			try:
				from app.services.historial import ollama as historial_ollama

				for chat in historial_ollama.listar_chats():
					clave = f"ollama:{historial_ollama.PREFIJO}{chat['id']}"
					externas[clave] = chat
					filas.append(FilaSesion(
						clave=clave, proveedor="ollama", titulo=str(chat["titulo"]), origen=historial_ollama.ORIGEN,
						actividad=float(chat["actividad"]), creada=float(chat["creada"]), modelo=str(chat["modelo"]),
						id_externo=historial_ollama.PREFIJO + chat["id"], ultimo_mensaje=_recortar(str(chat["ultimo"])),
					))
			except Exception:  # noqa: BLE001 - una base de la app de Ollama ilegible no debe vaciar la lista
				log.warning("No se pudieron leer los chats de la app de Ollama", exc_info=True)
		filas.extend(self._listar_equipos(externas))
		with self._candado:
			self._externas = externas
		return filas

	def _listar_equipos(self, externas: dict[str, Any]) -> list[FilaSesion]:
		"""Lo último leído de los equipos por SSH (nunca espera: la lectura va en segundo plano)."""
		forzar, self._forzar_equipos = self._forzar_equipos, False
		filas: list[FilaSesion] = []
		propios_claude = self.almacen.ids_externos("claude")
		propios_codex = self.almacen.ids_externos("codex")
		for equipo, lectura in self.historial_equipos.lecturas(forzar).values():
			if "claude" in self.proveedores and self._opcion("historial.claude", True):
				for sesion in lectura.claude:
					if sesion.id in propios_claude or sesion.clave in externas:
						continue
					externas[sesion.clave] = sesion
					filas.append(FilaSesion(
						clave=sesion.clave, proveedor="claude", titulo=sesion.titulo or "Sesión de Claude",
						carpeta=sesion.carpeta, origen=sesion.origen, estado=sesion.estado, actividad=sesion.actividad,
						creada=sesion.creada, archivada=sesion.archivada, id_externo=sesion.id,
						ultimo_mensaje=_recortar(sesion.ultimo_mensaje), equipo=equipo.nombre, sistema=equipo.nombre_sistema,
						sin_conversacion=not sesion.ruta_remota,
					))
			if "codex" in self.proveedores and self._opcion("historial.codex", True):
				for hilo in lectura.codex:
					identificador = str(hilo.get("id") or "")
					clave = f"codex:{identificador}"
					if not identificador or identificador in propios_codex or clave in externas:
						continue
					externas[clave] = hilo
					filas.append(FilaSesion(
						clave=clave, proveedor="codex", titulo=str(hilo.get("titulo") or "Hilo de Codex"),
						carpeta=str(hilo.get("carpeta") or ""), origen="codex",
						estado=str(hilo.get("estado") or Estado.INACTIVA), actividad=float(hilo.get("actualizado") or 0),
						creada=float(hilo.get("creado") or 0), id_externo=identificador,
						equipo=equipo.nombre, sistema=equipo.nombre_sistema,
					))
		return filas

	def _listar_codex_equipo(self, equipo: remoto.EquipoSSH) -> list[dict[str, Any]]:
		"""Hilos de Codex de un equipo por SSH (por su servidor en ese equipo). Llamar desde un hilo."""
		proveedor = self.proveedores.get("codex")
		if proveedor is None or not hasattr(proveedor, "listar_hilos"):
			return []
		return proveedor.listar_hilos(extra=_extra_equipo(equipo))

	def estado_equipos(self) -> list[tuple[str, str]]:
		"""``(equipo, explicación)`` de los equipos por SSH cuyas sesiones no se han podido leer."""
		return self.historial_equipos.estado()

	# ------------------------------------------------------------------
	# Mensajes
	# ------------------------------------------------------------------

	def mensajes(self, clave: str) -> list[MensajeVista]:
		"""Todo lo dicho en una sesión (bloquea: puede leer un historial grande)."""
		if clave.startswith(("ctrlia:", PREFIJO_IMPORTADA)):
			mostrar_herramientas = self._opcion("mensajes.herramientas", True)
			return [
				MensajeVista(m.rol, m.texto, m.fecha, m.detalle, m.herramienta, m.id, m.datos.get("cambios", []),
					self.datos_para_vista(clave, m.datos))
				for m in self.almacen.mensajes(clave)
				if mostrar_herramientas or m.rol not in ("herramienta", "razonamiento")
			]
		with self._candado:
			fuente = self._externas.get(clave)
		if clave.startswith("claude:"):
			from app.services.historial import claude as historial_claude

			if getattr(fuente, "equipo", ""):
				ruta = self._copia_remota(fuente)
			else:
				ruta = getattr(fuente, "ruta_transcript", None) or historial_claude.ruta_transcript(clave.split(":", 1)[1])
			if ruta is None:
				return [MensajeVista("sistema", "Conversación no disponible. " + TEXTO_SIN_CONVERSACION, time.time())]
			lector = historial_claude.LectorTranscript(Path(ruta))
			with self._candado:
				self._lectores[clave] = lector
			return [self._de_claude(m) for m in lector.leer_todo()]
		if clave.startswith("codex:"):
			proveedor = self.proveedores.get("codex")
			if proveedor is None or not hasattr(proveedor, "leer_hilo"):
				return []
			equipo = self._equipo_de_fuente(fuente)
			hilo = clave.split(":", 1)[1]
			return [
				MensajeVista(str(m.get("rol") or "asistente"), str(m.get("texto") or ""), float(m.get("fecha") or 0),
					str(m.get("detalle") or ""), str(m.get("herramienta") or ""))
				for m in (proveedor.leer_hilo(hilo, extra=_extra_equipo(equipo)) if equipo else proveedor.leer_hilo(hilo))
			]
		if clave.startswith("antigravity:"):
			from app.services.historial import antigravity as historial_antigravity

			datos = fuente if isinstance(fuente, dict) else {}
			return [MensajeVista("sistema", historial_antigravity.texto_de_aviso(datos), _segundos(datos.get("fecha")) or time.time())]
		if clave.startswith("ollama:"):
			from app.services.historial import ollama as historial_ollama

			identificador = clave.split(":", 1)[1].removeprefix(historial_ollama.PREFIJO)
			mostrar_razonamiento = self._opcion("mensajes.razonamiento", False)
			vista: list[MensajeVista] = []
			for mensaje in historial_ollama.leer_mensajes(identificador):
				if mostrar_razonamiento and mensaje["razonamiento"].strip():
					vista.append(MensajeVista("razonamiento", mensaje["razonamiento"], mensaje["fecha"]))
				vista.append(MensajeVista(mensaje["rol"], mensaje["texto"], mensaje["fecha"]))
			return vista
		return []

	def _equipo_de_fuente(self, fuente: Any) -> remoto.EquipoSSH | None:
		"""El equipo por SSH de una sesión externa, o ``None`` si es de Windows. Lanza ``ValueError`` si ya no existe."""
		ident = str(getattr(fuente, "equipo", "") or (fuente.get("equipo") if isinstance(fuente, dict) else "") or "")
		if not ident:
			return None
		equipo = remoto.equipos(self._opcion).get(ident)
		if equipo is None:
			raise ValueError("El equipo de esta sesión ya no está en Herramientas, «Equipos por SSH».")
		return equipo

	def _copia_remota(self, fuente: Any, minimo: float = 0.0) -> Path | None:
		"""Trae (o pone al día) la copia en Windows del transcript de una sesión de otro equipo."""
		from app.services.historial import equipos as historial_equipos

		equipo = self._equipo_de_fuente(fuente)
		if equipo is None or not fuente.ruta_remota or not fuente.id:
			return None
		destino = historial_equipos.ruta_copia(equipo.id, fuente.id)
		try:
			return historial_equipos.copiar_transcript(equipo, fuente.ruta_remota, destino, minimo)
		except remoto.ErrorRemoto as error:
			if minimo and destino.is_file():
				log.debug("No se pudo poner al día la sesión de %s: %s", equipo.nombre, error)
				return destino
			raise ValueError(str(error)) from error

	def mensajes_nuevos(self, clave: str) -> dict[str, Any]:
		"""Cambios de una sesión externa desde la última lectura (para seguirla en vivo).

		Devuelve ``nuevos`` (mensajes añadidos), ``modificados`` (mensajes ya
		mostrados que han cambiado, como una herramienta que recibe su resultado)
		y ``reiniciado`` (el historial se reescribió: hay que recargarlo entero).
		"""
		vacio: dict[str, Any] = {"nuevos": [], "modificados": [], "reiniciado": False}
		with self._candado:
			lector = self._lectores.get(clave)
		if lector is None:
			return vacio
		fuente = self.fuente_externa(clave)
		if getattr(fuente, "equipo", ""):
			from app.services.historial import equipos as historial_equipos

			try:
				self._copia_remota(fuente, historial_equipos.INTERVALO_SEGUIMIENTO)
			except ValueError:
				log.debug("No se pudo seguir la sesión del otro equipo", exc_info=True)
				return vacio
		try:
			nuevos = [self._de_claude(m) for m in lector.leer_nuevo()]
		except OSError:
			log.debug("No se pudo releer el historial", exc_info=True)
			return vacio
		modificados = getattr(lector, "modificados", []) or []
		if callable(modificados):
			modificados = modificados()
		return {
			"nuevos": nuevos,
			"modificados": [self._de_claude(m) for m in modificados],
			"reiniciado": bool(getattr(lector, "reiniciado", False)),
		}

	@staticmethod
	def _de_claude(mensaje: Any) -> MensajeVista:
		return MensajeVista(mensaje.rol, mensaje.texto, mensaje.fecha, mensaje.detalle, mensaje.herramienta,
			mensaje.id, list(getattr(mensaje, "cambios", []) or []))

	def buscar(self, consulta: str, cancelado: Callable[[], bool] | None = None,
			maximo: int = 500) -> list[tuple[str, str, MensajeVista]]:
		"""Busca en todas las sesiones: propias, importadas, de Claude Code, de Codex y de la app de Ollama.

		Sin distinguir mayúsculas ni acentos; deben aparecer todas las palabras.
		Devuelve ``(clave, título de la sesión, mensaje)``. Bloquea: llamar desde un hilo.
		"""
		cancelado = cancelado or (lambda: False)
		palabras = normalizar(consulta).split()
		if not palabras:
			return []
		titulos = {sesion.clave: sesion.titulo or "Sesión sin título" for sesion in self.almacen.sesiones()}
		titulos.update({clave: meta.titulo for clave, meta in self.almacen.metadatos().items() if meta.titulo})
		resultados: list[tuple[str, str, MensajeVista]] = []
		# Se recorre todo en Python (no con LIKE) para que «cancion» encuentre «canción».
		for fila in self.almacen.bd.iterar(
			"SELECT id, sesion, rol, texto, detalle, herramienta, fecha FROM mensajes ORDER BY fecha DESC",
		):
			if cancelado() or len(resultados) >= maximo:
				return resultados
			if coincide(fila["texto"] + "\n" + fila["detalle"], consulta):
				resultados.append((fila["sesion"], titulos.get(fila["sesion"], "Sesión"),
					MensajeVista(fila["rol"], fila["texto"], fila["fecha"], fila["detalle"], fila["herramienta"], fila["id"])))
		with self._candado:
			externas = [fuente for clave, fuente in self._externas.items() if clave.startswith("claude:")]
		if externas:
			try:
				from app.services.historial import claude as historial_claude

				for sesion, mensaje in historial_claude.buscar_en_sesiones(externas, consulta, maximo - len(resultados), cancelado):
					resultados.append((sesion.clave, titulos.get(sesion.clave, sesion.titulo), self._de_claude(mensaje)))
			except Exception:  # noqa: BLE001 - un historial ilegible no debe anular el resto de resultados
				log.exception("Falló la búsqueda en el historial de Claude Code")
		with self._candado:
			# Las de otros equipos no se buscan: habría que traer sus archivos enteros por SSH.
			hilos = [clave for clave, fuente in self._externas.items()
				if clave.startswith("codex:") and not (isinstance(fuente, dict) and fuente.get("equipo"))]
			chats = [clave for clave in self._externas if clave.startswith("ollama:")]
		if hilos and len(resultados) < maximo and not cancelado():
			try:
				from app.services.historial import codex as historial_codex

				ids = [clave.split(":", 1)[1] for clave in hilos]
				for identificador, mensaje in historial_codex.buscar_en_hilos(
						ids, lambda texto: coincide(texto, consulta), maximo - len(resultados), cancelado):
					clave = f"codex:{identificador}"
					resultados.append((clave, titulos.get(clave) or self._titulo_externa(clave),
						MensajeVista(mensaje["rol"], mensaje["texto"], mensaje["fecha"])))
			except Exception:  # noqa: BLE001 - un historial ilegible no debe anular el resto de resultados
				log.exception("Falló la búsqueda en el historial de Codex")
		if chats and len(resultados) < maximo and not cancelado():
			try:
				from app.services.historial import ollama as historial_ollama

				ids = [clave.split(":", 1)[1].removeprefix(historial_ollama.PREFIJO) for clave in chats]
				for identificador, mensaje in historial_ollama.buscar(
						dict.fromkeys(ids, ""), lambda texto: coincide(texto, consulta), maximo - len(resultados), cancelado):
					clave = f"ollama:{historial_ollama.PREFIJO}{identificador}"
					resultados.append((clave, titulos.get(clave) or self._titulo_externa(clave),
						MensajeVista(mensaje["rol"], mensaje["texto"], mensaje["fecha"])))
			except Exception:  # noqa: BLE001 - un historial ilegible no debe anular el resto de resultados
				log.exception("Falló la búsqueda en los chats de la app de Ollama")
		return resultados

	def _titulo_externa(self, clave: str) -> str:
		fuente = self.fuente_externa(clave)
		if isinstance(fuente, dict):
			return str(fuente.get("titulo") or "Sesión")
		return str(getattr(fuente, "titulo", "") or "Sesión")

	def fuente_externa(self, clave: str) -> Any:
		with self._candado:
			return self._externas.get(clave)

	# ------------------------------------------------------------------
	# Conversaciones
	# ------------------------------------------------------------------

	def nueva_sesion(self, proveedor_id: str, ajustes: AjustesSesion) -> str:
		"""Registra una sesión propia nueva (todavía sin enviar nada) y devuelve su clave."""
		if proveedor_id not in self.proveedores:
			raise ValueError("Agente desconocido")
		clave = nueva_clave()
		ahora = time.time()
		self.almacen.guardar_sesion(SesionPropia(
			clave=clave, proveedor=proveedor_id, titulo=ajustes.titulo, carpeta=str(ajustes.carpeta),
			id_externo=ajustes.id_externo, modelo=ajustes.modelo, esfuerzo=ajustes.esfuerzo,
			modo_permiso=ajustes.modo_permiso, creada=ahora, actividad=ahora, bifurcada_de=ajustes.bifurcar_de,
			reglas=list(ajustes.reglas_permitidas), extra=dict(ajustes.extra),
		))
		if not remoto.es_remota(ajustes.extra):  # las carpetas de otro equipo no son recientes de Windows
			self.almacen.usar_carpeta(str(ajustes.carpeta))
		return clave

	def puede_seguir_original(self, clave: str) -> bool:
		"""Si una sesión de otro programa se puede seguir tal cual (sin copia): Claude Code y Codex."""
		if clave.startswith(("ctrlia:", PREFIJO_IMPORTADA)) or ":" not in clave:
			return False
		proveedor_id, identificador = clave.split(":", 1)
		return proveedor_id in AGENTES_CON_ORIGINAL and proveedor_id in self.proveedores and not identificador.startswith("local_")

	def _comprobar_libre(self, fuente: Any, ahora: float | None = None) -> None:
		"""Lanza ``SesionEnUso`` si la original está abierta en otro programa o se ha usado hace muy poco."""
		ahora = time.time() if ahora is None else ahora
		abierta = bool(getattr(fuente, "abierta", False))
		estado = str(getattr(fuente, "estado", "") or (fuente.get("estado") if isinstance(fuente, dict) else "") or "")
		actividad = getattr(fuente, "actividad", None)
		if actividad is None and isinstance(fuente, dict):
			actividad = fuente.get("actualizado")
		try:
			hace = ahora - float(actividad or 0)
		except (TypeError, ValueError):
			hace = RECIENTE_EN_USO + 1
		if abierta or estado in (Estado.TRABAJANDO, Estado.ESPERANDO_USUARIO, Estado.SEGUNDO_PLANO) or 0 <= hace < RECIENTE_EN_USO:
			raise SesionEnUso(
				"La sesión original está abierta o se ha usado hace menos de dos minutos en el otro programa. "
				"Si la siguieras aquí a la vez, las dos conversaciones se mezclarían."
			)

	def continuar_con_historial(self, clave_origen: str, titulo: str = "", original: bool = False) -> str:
		"""«Continuar aquí» desde la interfaz (bloquea: lee el historial, quizá por SSH).

		Crea la sesión (copia o, con ``original``, la misma sesión) y le pone delante
		los mensajes que ya tenía, para leerla entera en Ctrl+IA. Si no se pueden
		leer, la sesión se crea igual y lo dice un aviso.
		"""
		proveedor_id = clave_origen.split(":", 1)[0]
		copiar = not clave_origen.startswith(("ctrlia:", PREFIJO_IMPORTADA)) and proveedor_id in ("claude", "codex", "ollama")
		anteriores: list[MensajeVista] = []
		fallo = ""
		if copiar:
			try:
				anteriores = [m for m in self.mensajes(clave_origen) if m.rol != "anteriores"]
			except Exception as error:  # noqa: BLE001 - sin historial se continúa igual: el agente sí lo recuerda
				log.warning("No se pudo copiar el historial de %s", clave_origen, exc_info=True)
				fallo = str(error)
		clave = self.continuar_aqui(clave_origen, titulo, original=original)
		if anteriores:
			self.almacen.reemplazar_mensajes(clave, [
				{"rol": m.rol, "texto": m.texto, "detalle": m.detalle, "herramienta": m.herramienta,
					"fecha": m.fecha, "datos": {"cambios": list(m.cambios or []), "anterior": True}}
				for m in anteriores
			])
		elif fallo:
			self.almacen.anadir_mensaje(clave, "sistema", "No se ha podido traer aquí la conversación anterior (%s), "
				"pero el agente la recuerda entera." % fallo)
		return clave

	def continuar_aqui(self, clave_origen: str, titulo: str = "", original: bool = False) -> str:
		"""Crea una sesión propia que continúa una sesión de un agente.

		De fábrica la bifurca (una copia: la original no cambia). Con ``original``,
		en Claude Code y Codex la sesión de Ctrl+IA ES la original (``--resume`` sin
		copia, ``thread/resume``): lo que hables aquí se verá después en la terminal,
		la app o el otro equipo. Solo si la original no está abierta ni se ha usado
		hace muy poco (``SesionEnUso``).
		"""
		if clave_origen.startswith(PREFIJO_IMPORTADA):
			# Una importada no se sigue: se empieza otra sesión con ``preparar_desde_importada``.
			raise ValueError(
				"Esta sesión se importó de otro equipo y es de solo lectura: el agente de este equipo no la tiene. "
				"«Continuar aquí» crea una sesión nueva en este equipo que le cuenta la conversación al agente."
			)
		proveedor_id, identificador = clave_origen.split(":", 1)
		if clave_origen.startswith("ctrlia:"):
			original = self.almacen.sesion(clave_origen)
			if original is None or not original.id_externo:
				raise ValueError("Esa sesión todavía no tiene nada que continuar")
			proveedor_id, identificador = original.proveedor, original.id_externo
			carpeta = original.carpeta
			titulo = titulo or f"{original.titulo} (continuación)"
			modelo, esfuerzo, modo = original.modelo, original.esfuerzo, original.modo_permiso
			datos_remotos = remoto.datos_remotos(original.extra)
			if datos_remotos is not None:
				# En otro equipo: la carpeta no se comprueba desde Windows y la continuación sigue allí.
				if proveedor_id not in self.proveedores:
					raise ValueError("Ese agente no está disponible")
				ajustes = AjustesSesion(carpeta=PurePosixPath(carpeta), titulo=titulo, bifurcar_de=identificador,
					modelo=modelo, esfuerzo=esfuerzo, modo_permiso=modo, extra={"remoto": dict(datos_remotos)})
				return self.nueva_sesion(proveedor_id, ajustes)
		else:
			fuente = self.fuente_externa(clave_origen)
			carpeta = str(getattr(fuente, "carpeta", "") or (fuente.get("carpeta") if isinstance(fuente, dict) else "") or "")
			titulo_original = str(getattr(fuente, "titulo", "") or (fuente.get("titulo") if isinstance(fuente, dict) else "") or "")
			titulo = titulo or f"{titulo_original or 'Sesión'} (continuación)"
			modelo = esfuerzo = modo = ""
			if original and self.puede_seguir_original(clave_origen):
				self._comprobar_libre(fuente)
				if titulo.endswith(" (continuación)"):
					titulo = titulo_original or titulo.removesuffix(" (continuación)")
			else:
				original = False
			equipo = self._equipo_de_fuente(fuente)
			if equipo is not None:
				return self._continuar_en_equipo(equipo, proveedor_id, identificador, carpeta, titulo, original, clave_origen)
			if proveedor_id == "ollama":
				# Un chat de la app de Ollama no tiene carpeta: el modelo no entra en ninguna.
				carpeta = carpeta or str(Path.home())
				modelo = str(fuente.get("modelo") or "") if isinstance(fuente, dict) else ""
		if proveedor_id not in self.proveedores:
			raise ValueError("Ese agente no está disponible")
		if proveedor_id == "antigravity" and not clave_origen.startswith("ctrlia:"):
			return self._retomar_antigravity(identificador, carpeta, titulo)
		if not carpeta or not Path(carpeta).is_dir():
			raise ValueError("La carpeta de esa sesión ya no existe")
		if not modo:
			# Una sesión de otro programa no guarda su modo: se usa el inicial de la configuración del agente.
			try:
				modo = self.proveedores[proveedor_id].modo_predeterminado(Path(carpeta))
			except Exception:  # noqa: BLE001 - sin modo, el agente usa el suyo
				log.warning("No se pudo leer el modo de permisos inicial de %s", proveedor_id, exc_info=True)
		if original and not clave_origen.startswith("ctrlia:"):
			ajustes = AjustesSesion(carpeta=Path(carpeta), titulo=titulo, id_externo=identificador,
				modelo=modelo, esfuerzo=esfuerzo, modo_permiso=modo, extra={"original": clave_origen})
		else:
			ajustes = AjustesSesion(carpeta=Path(carpeta), titulo=titulo, bifurcar_de=identificador,
				modelo=modelo, esfuerzo=esfuerzo, modo_permiso=modo)
		return self.nueva_sesion(proveedor_id, ajustes)

	def preparar_desde_importada(self, clave: str, clave_importada: str) -> Any:
		"""Convierte la sesión propia recién creada ``clave`` en la continuación de una importada (#228, fase 2).

		Le copia los mensajes de la importada (para leerlos aquí; la importada no se
		toca), añade un aviso que lo explica y deja en ``extra["contexto_importada"]``
		el resumen que irá delante del primer mensaje al agente (``_con_importada``).
		Devuelve el ``ContextoImportada`` (dice si el resumen va recortado).
		"""
		from app.services import intercambio

		if not clave.startswith("ctrlia:") or not clave_importada.startswith(PREFIJO_IMPORTADA):
			raise ValueError("Solo una sesión de Ctrl+IA puede continuar una sesión importada")
		sesion = self.almacen.sesion(clave)
		importada = self.almacen.sesion(clave_importada)
		if sesion is None or importada is None:
			raise ValueError("La sesión importada ya no existe")
		datos_importada = importada.extra.get("importada") if isinstance(importada.extra.get("importada"), dict) else {}
		equipo = str(datos_importada.get("equipo") or "")
		guardados = self.almacen.mensajes(clave_importada)
		contexto = intercambio.contexto_para_continuar(guardados, importada.titulo, equipo, importada.proveedor)
		ya_tenia = self.almacen.mensajes(clave)
		copia = [
			{"rol": m.rol, "texto": m.texto, "detalle": m.detalle, "herramienta": m.herramienta, "fecha": m.fecha,
				"datos": {"cambios": list(m.datos.get("cambios") or []), "anterior": True}}
			for m in guardados
		]
		if contexto.recortada:
			resumen = "recortada a lo más importante (el primer mensaje y los más recientes)"
		else:
			resumen = "entera"
		aviso = (
			f"Esta sesión continúa «{importada.titulo}», importada de {equipo or 'otro equipo'}. Arriba están sus "
			f"mensajes para leerlos; el agente de este equipo no los tiene, así que con tu primer mensaje le llegará la "
			f"conversación anterior, {resumen}. La sesión importada no cambia."
		)
		copia.append({"rol": "sistema", "texto": aviso, "fecha": time.time()})
		copia += [{"rol": m.rol, "texto": m.texto, "detalle": m.detalle, "herramienta": m.herramienta, "fecha": m.fecha,
			"id_origen": m.id_origen, "datos": m.datos} for m in ya_tenia]
		self.almacen.reemplazar_mensajes(clave, copia)
		extra = dict(sesion.extra or {})
		extra["desde_importada"] = clave_importada
		extra["contexto_importada"] = contexto.texto
		self.almacen.actualizar_sesion(clave, extra=extra)
		with self._candado:
			viva = self._conversaciones.get(clave)
		if viva is not None:
			viva.ajustes.extra.update(desde_importada=clave_importada, contexto_importada=contexto.texto)
		return contexto

	def contexto_pendiente(self, clave: str) -> bool:
		"""Si la sesión aún tiene que contarle al agente la conversación importada que continúa."""
		sesion = self.almacen.sesion(clave)
		return sesion is not None and bool(sesion.extra.get("contexto_importada"))

	def _con_importada(self, clave: str, conversacion: Conversacion, texto: str) -> str:
		"""Pone delante del primer mensaje al agente el resumen de la sesión importada que se continúa (una vez)."""
		contexto = str(conversacion.ajustes.extra.pop("contexto_importada", "") or "")
		sesion = self.almacen.sesion(clave)
		if sesion is not None and "contexto_importada" in sesion.extra:
			contexto = contexto or str(sesion.extra.get("contexto_importada") or "")
			guardado = dict(sesion.extra)
			guardado.pop("contexto_importada", None)
			guardado["contexto_importada_enviado"] = time.time()
			self.almacen.actualizar_sesion(clave, extra=guardado)
		return contexto + "\n\n" + texto if contexto else texto

	def _continuar_en_equipo(self, equipo: remoto.EquipoSSH, proveedor_id: str, identificador: str, carpeta: str,
			titulo: str, original: bool = False, clave_origen: str = "") -> str:
		"""Sesión propia que continúa en su mismo equipo por SSH una sesión que ya existía allí (copia u original)."""
		if proveedor_id not in self.proveedores:
			raise ValueError("Ese agente no está disponible")
		if not identificador or identificador.startswith("local_"):
			raise ValueError("Esta sesión de la app de Claude todavía no tiene conversación que continuar")
		if not carpeta:
			raise ValueError("Esa sesión no dice en qué carpeta trabajaba")
		# La carpeta es del otro equipo: no se comprueba desde Windows. Sin modo elegido, el agente usa el suyo.
		extra: dict[str, Any] = {"remoto": {"equipo": equipo.id, "nombre": equipo.nombre, "carpeta": carpeta}}
		if original:
			# La misma sesión del otro equipo: lo que se hable aquí queda allí, en su terminal o su app.
			extra["original"] = clave_origen
			ajustes = AjustesSesion(carpeta=PurePosixPath(carpeta), titulo=titulo, id_externo=identificador, extra=extra)
		else:
			ajustes = AjustesSesion(carpeta=PurePosixPath(carpeta), titulo=titulo, bifurcar_de=identificador, extra=extra)
		return self.nueva_sesion(proveedor_id, ajustes)

	def _retomar_antigravity(self, identificador: str, carpeta: str, titulo: str) -> str:
		"""Sesión propia que sigue una conversación de Antigravity CLI con ``agy --conversation``.

		Antigravity no sabe copiar conversaciones: la sesión sigue la misma, que deja de
		salir aparte en la lista porque ya es de Ctrl+IA.
		"""
		from app.services.historial import antigravity as historial_antigravity

		if not historial_antigravity.se_puede_retomar(identificador):
			raise ValueError(
				"Esta conversación es de la app de escritorio de Antigravity, y Antigravity CLI no puede retomarla. "
				"Pulsa Control+O para abrir su carpeta en la app y seguirla allí."
			)
		if not carpeta or not Path(carpeta).is_dir():
			raise ValueError("La carpeta de esa sesión ya no existe")
		ajustes = AjustesSesion(carpeta=Path(carpeta), titulo=titulo, id_externo=identificador)
		return self.nueva_sesion("antigravity", ajustes)

	def conversacion(self, clave: str, crear: bool = True) -> Conversacion | None:
		"""Conversación viva de una sesión propia (la crea si hace falta)."""
		if not clave.startswith("ctrlia:"):
			return None  # las importadas viven en la misma tabla, pero son de solo lectura
		with self._candado:
			existente = self._conversaciones.get(clave)
			if existente is not None or not crear:
				return existente
			sesion = self.almacen.sesion(clave)
			if sesion is None:
				return None
			proveedor = self.proveedores.get(sesion.proveedor)
			if proveedor is None:
				raise ValueError("El agente de esta sesión no está disponible")
			ajustes = AjustesSesion(
				carpeta=Path(sesion.carpeta), modelo=sesion.modelo, esfuerzo=sesion.esfuerzo,
				modo_permiso=sesion.modo_permiso, titulo=sesion.titulo, id_externo=sesion.id_externo,
				# Mientras la copia no exista en el agente (sin id propio), hay que bifurcar;
				# después se retoma la copia como cualquier otra sesión.
				bifurcar_de=sesion.bifurcada_de if not sesion.id_externo else "",
				reglas_permitidas=list(sesion.reglas), extra=dict(sesion.extra),
			)
			conversacion = proveedor.crear_conversacion(ajustes, lambda evento, c=clave: self._recibir(c, evento))
			self._conversaciones[clave] = conversacion
			return conversacion

	def enviar(self, clave: str, texto: str, adjuntos: Sequence[Adjunto] = (), ahora: bool = False) -> None:
		conversacion = self.conversacion(clave)
		if conversacion is None:
			raise ValueError("Solo se puede escribir en las sesiones de Ctrl+IA")
		datos = {"adjuntos": [str(a.ruta) for a in adjuntos]} if adjuntos else None
		id_mensaje = self.almacen.anadir_mensaje(clave, "usuario", texto, datos=datos)
		self.almacen.actualizar_sesion(clave, actividad=time.time())
		# Lo que se guarda y se ve es lo que escribió la persona; al agente le puede llegar con la
		# presentación de los agentes elegidos delante.
		texto = self._con_agentes(clave, conversacion, texto)
		# La continuación de una importada le cuenta antes al agente la conversación anterior (#228).
		texto = self._con_importada(clave, conversacion, texto)
		# Tras deshacer cambios de archivos, el agente lo sabe con este mensaje.
		texto = self._con_deshacer(clave, conversacion, texto)
		# Punto de restauración antes de que el agente toque nada: el mensaje sale tras él, desde un hilo.
		if self._enviar_con_punto(clave, conversacion, id_mensaje, texto, adjuntos, ahora):
			return
		if ahora:
			conversacion.enviar_ahora(texto)
		else:
			conversacion.enviar(texto, adjuntos)
		self._apuntar_uuid_claude(clave, conversacion, id_mensaje)

	def _con_agentes(self, clave: str, conversacion: Conversacion, texto: str) -> str:
		"""Da a la conversación los agentes del catálogo elegidos para la sesión (``extra["agentes"]``).

		Claude Code en este equipo los recibe de forma nativa (``conversacion.agentes``,
		que monta su complemento). A los demás se les presentan delante del mensaje
		la primera vez y cada vez que cambia la lista (``extra["agentes_presentados"]``).
		"""
		from app.services import agentes_sesion

		extra = conversacion.ajustes.extra
		ids = [i for i in extra.get("agentes") or [] if isinstance(i, int)]
		try:
			from app.services import catalogo_agentes

			datos = catalogo_agentes.datos_para_sesion(self.almacen, ids) if ids else []
		except Exception:  # noqa: BLE001 - un catálogo ilegible no impide enviar el mensaje
			log.warning("No se pudieron leer los agentes elegidos para la sesión", exc_info=True)
			datos = []
		datos = [d for d in datos if d.get("existe", True)]
		conversacion.agentes = datos
		sesion = self.almacen.sesion(clave)
		proveedor = self.proveedor(sesion.proveedor) if sesion is not None else None
		remota = remoto.es_remota(extra)
		nativo = proveedor is not None and getattr(proveedor.capacidades, "agentes_nativos", False) and not remota
		firma = agentes_sesion.firma(datos)
		anterior = str(extra.get("agentes_presentados") or "")
		if nativo or firma == anterior:
			return texto
		extra["agentes_presentados"] = firma
		if sesion is not None:
			guardado = dict(sesion.extra or {})
			guardado["agentes_presentados"] = firma
			self.almacen.actualizar_sesion(clave, extra=guardado)
		if not datos:
			return "[Ctrl+IA] Ya no hay agentes elegidos para esta sesión: deja de usar los de antes.\n\n" + texto
		con_contenido = remota or (proveedor is not None and proveedor.id == "ollama")
		return agentes_sesion.presentacion(datos, con_contenido) + "\n\n" + texto

	def detener(self, clave: str) -> list[str]:
		"""Detiene el turno y devuelve los mensajes que estaban en cola."""
		# Los que esperaban su punto de restauración aún no habían salido: son los más recientes.
		retenidos = self._retirar_envios(clave)
		conversacion = self.conversacion(clave, crear=False)
		if conversacion is None:
			return retenidos
		pendientes = conversacion.pendientes()
		conversacion.detener()
		if pendientes:
			self._sin_punto_en_cola(clave)
		return pendientes + retenidos

	def responder(self, clave: str, solicitud_id: str, respuesta: RespuestaPermiso) -> int | None:
		"""Pasa la respuesta al agente y la apunta junto a la petición.

		Devuelve el identificador del mensaje en el que quedó apuntada (para
		refrescar su fila) o ``None`` si no se apuntó en ninguno.
		"""
		conversacion = self.conversacion(clave, crear=False)
		with self._candado:
			solicitud = self._solicitudes.get(clave, {}).pop(solicitud_id, None)
		if conversacion is not None:
			conversacion.responder(solicitud_id, respuesta)
			reglas = list(conversacion.ajustes.reglas_permitidas)
			self.almacen.actualizar_sesion(clave, reglas=reglas, modo_permiso=conversacion.ajustes.modo_permiso)
		try:
			return self._apuntar_respuesta(clave, solicitud_id, solicitud, respuesta)
		except Exception:  # noqa: BLE001 - la respuesta ya ha llegado al agente; apuntarla es secundario
			log.exception("No se pudo guardar la respuesta a una petición del agente")
			return None

	# ------------------------------------------------------------------
	# Peticiones del agente guardadas con los mensajes (indicadores y «Decisiones»)
	# ------------------------------------------------------------------

	def datos_para_vista(self, clave: str, datos: dict[str, Any]) -> dict[str, Any]:
		"""Los datos de un mensaje guardado para mostrarlo.

		Una petición apuntada como pendiente que el agente ya no espera (Ctrl+IA
		se cerró sin responderla, o el turno acabó) se muestra «sin respuesta»:
		el indicador nunca dice que hay algo que contestar si no lo hay.
		"""
		solicitudes = datos.get("solicitudes") if isinstance(datos, dict) else None
		if not isinstance(solicitudes, list) or not solicitudes:
			return datos if isinstance(datos, dict) else {}
		with self._candado:
			vivas = set(self._solicitudes.get(clave, {}))
		copia = []
		for entrada in solicitudes:
			if isinstance(entrada, dict) and entrada.get("estado") == "pendiente" and str(entrada.get("id")) not in vivas:
				entrada = dict(entrada, estado="sin_respuesta")
			copia.append(entrada)
		return dict(datos, solicitudes=copia)

	def _mensaje_para_apuntar(self, clave: str) -> int | None:
		"""El mensaje al que pertenece lo que pasa ahora: la respuesta del agente en este turno.

		Si el agente no ha escrito nada desde tu último mensaje, el último mensaje
		que no es una herramienta (para que se vea aunque las acciones estén
		ocultas); si no hay ninguno, el último mensaje de la sesión.
		"""
		ultimos = self.almacen.ultimos_mensajes(clave, 200)
		for mensaje in ultimos:
			if mensaje.rol == "asistente":
				return mensaje.id
			if mensaje.rol == "usuario":
				break
		for mensaje in ultimos:
			if mensaje.rol not in ("herramienta", "razonamiento"):
				return mensaje.id
		return ultimos[0].id if ultimos else None

	def _apuntar_solicitud(self, clave: str, solicitud: SolicitudPermiso) -> int | None:
		"""Guarda una petición del agente en los datos del mensaje al que pertenece."""
		if not clave.startswith("ctrlia:"):
			return None
		with self._candado_solicitudes:
			if (clave, solicitud.id) in self._mensaje_de_solicitud:
				return None  # la misma petición repetida: se apunta una vez
			id_mensaje = self._mensaje_para_apuntar(clave)
			guardado = self.almacen.mensaje(id_mensaje) if id_mensaje else None
			if guardado is None:
				log.debug("No hay ningún mensaje en el que apuntar la petición %s", solicitud.id)
				return None
			datos = dict(guardado.datos)
			solicitudes = [s for s in datos.get("solicitudes") or [] if isinstance(s, dict)]
			solicitudes.append(resumen_solicitud(solicitud))
			datos["solicitudes"] = solicitudes
			self.almacen.actualizar_mensaje(guardado.id, datos=datos)
			self._mensaje_de_solicitud[(clave, solicitud.id)] = guardado.id
			return guardado.id

	def _cambiar_solicitud(self, clave: str, solicitud_id: str, **cambios: Any) -> int | None:
		"""Actualiza la petición apuntada (``estado``, ``respuesta``...). Devuelve el id del mensaje."""
		with self._candado_solicitudes:
			id_mensaje = self._mensaje_de_solicitud.get((clave, solicitud_id))
			guardado = self.almacen.mensaje(id_mensaje) if id_mensaje else None
			if guardado is None:
				return None
			datos = dict(guardado.datos)
			solicitudes = [dict(s) for s in datos.get("solicitudes") or [] if isinstance(s, dict)]
			cambiada = False
			for entrada in solicitudes:
				if str(entrada.get("id")) == solicitud_id and entrada.get("estado") == "pendiente":
					entrada.update(cambios)
					cambiada = True
			if not cambiada:
				return None
			datos["solicitudes"] = solicitudes
			self.almacen.actualizar_mensaje(guardado.id, datos=datos)
			return guardado.id

	def _apuntar_respuesta(self, clave: str, solicitud_id: str, solicitud: SolicitudPermiso | None,
			respuesta: RespuestaPermiso) -> int | None:
		opcion = None
		if solicitud is not None:
			opcion = next((o for o in solicitud.opciones if o.id == respuesta.opcion), None)
		apuntada = {
			"opcion": respuesta.opcion,
			"etiqueta": opcion.etiqueta if opcion is not None else respuesta.opcion,
			"clase": str(opcion.clase) if opcion is not None else "",
			"mensaje": respuesta.mensaje,
			"respuestas": {str(k): [str(v) for v in valores] for k, valores in (respuesta.respuestas or {}).items()},
		}
		return self._cambiar_solicitud(clave, solicitud_id, estado="respondida", respuesta=apuntada,
			fecha_respuesta=time.time())

	def _apuntar_detenido(self, clave: str) -> int | None:
		"""Marca como «detenido» el mensaje del turno que no llegó a terminar."""
		if not clave.startswith("ctrlia:"):
			return None
		with self._candado_solicitudes:
			id_mensaje = self._mensaje_para_apuntar(clave)
			guardado = self.almacen.mensaje(id_mensaje) if id_mensaje else None
			if guardado is None or guardado.rol not in ("asistente", "usuario"):
				return None
			self.almacen.actualizar_mensaje(guardado.id, datos=dict(guardado.datos, detenido=True))
			return guardado.id

	def cambiar_ajustes(self, clave: str, **cambios: Any) -> None:
		campos = {k: v for k, v in cambios.items() if k in ("modelo", "esfuerzo", "modo_permiso", "titulo")}
		self.almacen.actualizar_sesion(clave, **campos)
		conversacion = self.conversacion(clave, crear=False)
		if conversacion is not None:
			conversacion.cambiar_ajustes(**campos)
		for nombre in ("estilo", "agentes"):
			if nombre not in cambios:
				continue
			# El estilo (Claude Code) y los agentes del catálogo van en ``extra``: no hace falta tocar el esquema.
			# Los agentes se le dicen (o se le cargan) al agente desde el próximo mensaje.
			sesion = self.almacen.sesion(clave)
			extra = dict(sesion.extra or {}) if sesion is not None else {}
			valor = [int(i) for i in cambios[nombre] or []] if nombre == "agentes" else str(cambios[nombre] or "")
			extra[nombre] = valor
			self.almacen.actualizar_sesion(clave, extra=extra)
			if conversacion is not None:
				conversacion.ajustes.extra[nombre] = valor

	def ocupada(self, clave: str) -> bool:
		if self._envio_pendiente(clave):
			return True  # un mensaje espera su punto de restauración para salir
		conversacion = self.conversacion(clave, crear=False)
		return conversacion is not None and conversacion.ocupada

	def sesiones_ocupadas(self) -> list[str]:
		with self._candado:
			ocupadas = [clave for clave, conv in self._conversaciones.items() if conv.ocupada]
		return ocupadas + [clave for clave in self._sesiones_con_envio() if clave not in ocupadas]

	def cerrar_conversacion(self, clave: str) -> None:
		with self._candado:
			conversacion = self._conversaciones.pop(clave, None)
		if conversacion is not None:
			conversacion.cerrar()

	def cerrar_todo(self) -> None:
		with self._candado:
			conversaciones = list(self._conversaciones.values())
			self._conversaciones.clear()
		for conversacion in conversaciones:
			try:
				conversacion.cerrar()
			except Exception:  # noqa: BLE001 - al salir se cierra todo lo que se pueda
				log.exception("No se pudo cerrar una conversación")
		for proveedor in self.proveedores.values():
			cerrar = getattr(proveedor, "cerrar", None)
			if callable(cerrar):
				try:
					cerrar()
				except Exception:  # noqa: BLE001
					log.exception("No se pudo cerrar el agente %s", proveedor.id)
		from app.services.agentes import remoto

		remoto.cerrar_tuneles()

	# ------------------------------------------------------------------
	# Organización
	# ------------------------------------------------------------------

	def renombrar(self, clave: str, titulo: str) -> None:
		if clave.startswith("ctrlia:"):
			self.almacen.actualizar_sesion(clave, titulo=titulo)
			conversacion = self.conversacion(clave, crear=False)
			if conversacion is not None:
				conversacion.ajustes.titulo = titulo
		else:
			self.almacen.fijar_metadato(clave, titulo=titulo)

	def ocultar(self, clave: str, oculta: bool = True) -> None:
		self.almacen.fijar_metadato(clave, oculta=oculta)

	def marcar_favorita(self, clave: str, favorita: bool) -> None:
		self.almacen.fijar_metadato(clave, favorita=favorita)

	def borrar(self, clave: str) -> None:
		"""Borra para siempre una sesión propia o importada de Ctrl+IA (no toca el historial del agente)."""
		if not clave.startswith(("ctrlia:", PREFIJO_IMPORTADA)):
			raise ValueError("Solo se pueden borrar las sesiones de Ctrl+IA; las demás se pueden ocultar.")
		sesion = self.almacen.sesion(clave)
		self.cerrar_conversacion(clave)
		self.almacen.borrar_sesion(clave)
		self._borrar_puntos(sesion)  # sus puntos de restauración, en segundo plano
		if sesion is not None and sesion.proveedor == "claude" and sesion.id_externo:
			# El complemento con los agentes de la sesión (enlaces); los originales no se tocan.
			from app.services import agentes_sesion

			agentes_sesion.limpiar_complemento("claude:" + sesion.id_externo)

	def borrar_importadas(self) -> int:
		"""Borra la copia de todas las sesiones importadas. Devuelve cuántas había."""
		claves = [s.clave for s in self.almacen.sesiones() if s.clave.startswith(PREFIJO_IMPORTADA)]
		for clave in claves:
			self.almacen.borrar_sesion(clave)
		return len(claves)

	def marcar_leida(self, clave: str) -> None:
		"""Quita la marca de respuesta nueva (al abrir la sesión o con «Marcar como leída»)."""
		if clave.startswith("ctrlia:"):
			self.almacen.actualizar_sesion(clave, no_leidas=0)
		elif not clave.startswith(PREFIJO_IMPORTADA):
			self.almacen.fijar_metadato(clave, leida_hasta=time.time())

	def marcar_no_leida(self, clave: str) -> None:
		"""Vuelve a marcar la sesión con respuesta nueva, para acordarte de leerla."""
		if clave.startswith("ctrlia:"):
			sesion = self.almacen.sesion(clave)
			if sesion is not None and not sesion.no_leidas:
				self.almacen.actualizar_sesion(clave, no_leidas=1)
		elif not clave.startswith(PREFIJO_IMPORTADA):
			self.almacen.fijar_metadato(clave, leida_hasta=LEIDA_NUNCA)

	def marcar_todas_leidas(self, claves: Sequence[str]) -> None:
		"""Quita la marca de respuesta nueva de todas esas sesiones de una vez."""
		ahora = time.time()
		externas = {c: ahora for c in claves if not c.startswith(("ctrlia:", PREFIJO_IMPORTADA))}
		self.almacen.fijar_leida_hasta(externas)
		for clave in claves:
			if clave.startswith("ctrlia:"):
				self.almacen.actualizar_sesion(clave, no_leidas=0)

	def marcar_terminada(self, clave: str, terminada: bool = True) -> None:
		"""Da la sesión por terminada (o la vuelve a activar).

		Una sesión terminada no sale en «Te necesitan» ni en «Trabajando» y no
		cuenta como respuesta nueva. Si después tiene actividad nueva (le
		escribes, o sigue en otro programa), vuelve a contar como activa sola.
		"""
		self.almacen.fijar_metadato(clave, terminada=time.time() if terminada else 0.0)
		if terminada:
			self.marcar_leida(clave)

	# ------------------------------------------------------------------
	# Eventos de las conversaciones (hilos de fondo)
	# ------------------------------------------------------------------

	def _recibir(self, clave: str, evento: Evento) -> None:
		try:
			self._guardar(clave, evento)
		except Exception:  # noqa: BLE001 - un fallo al guardar no debe perder el evento en pantalla
			log.exception("No se pudo guardar un evento de %s", evento.tipo)
		self._al_evento(clave, evento)

	def _guardar(self, clave: str, evento: Evento) -> None:
		tipo = evento.tipo
		datos = evento.datos
		almacen = self.almacen
		if tipo is TipoEvento.INICIADO:
			cambios: dict[str, Any] = {"actividad": time.time()}
			if datos.get("id_externo"):
				cambios["id_externo"] = datos["id_externo"]
			almacen.actualizar_sesion(clave, **cambios)
		elif tipo is TipoEvento.TEXTO_PARCIAL:
			self._parciales[clave] = self._parciales.get(clave, "") + evento.texto
		elif tipo is TipoEvento.MENSAJE:
			self._parciales.pop(clave, None)
			id_origen = str(datos.get("id") or "")
			existente = self._ids_mensaje.get((clave, id_origen)) if id_origen else None
			if existente:
				almacen.actualizar_mensaje(existente, texto=evento.texto)
			else:
				nuevo = almacen.anadir_mensaje(clave, "asistente", evento.texto, id_origen=id_origen)
				if id_origen:
					self._ids_mensaje[(clave, id_origen)] = nuevo
				datos["id_mensaje"] = nuevo
			if existente:
				datos["id_mensaje"] = existente
		elif tipo is TipoEvento.RAZONAMIENTO:
			if self._opcion("mensajes.razonamiento", False):
				almacen.anadir_mensaje(clave, "razonamiento", evento.texto)
		elif tipo is TipoEvento.HERRAMIENTA:
			id_origen = str(datos.get("id") or "")
			nuevo = almacen.anadir_mensaje(clave, "herramienta", evento.texto, detalle=str(datos.get("detalle") or ""),
				herramienta=str(datos.get("herramienta") or ""), id_origen=id_origen)
			if id_origen:
				self._ids_mensaje[(clave, "herramienta:" + id_origen)] = nuevo
			datos["id_mensaje"] = nuevo
		elif tipo is TipoEvento.HERRAMIENTA_FIN:
			id_origen = str(datos.get("id") or "")
			existente = self._ids_mensaje.get((clave, "herramienta:" + id_origen)) if id_origen else None
			salida = str(datos.get("salida") or "")
			if existente and salida:
				filas = [m for m in almacen.mensajes(clave, existente - 1) if m.id == existente]
				detalle = filas[0].detalle if filas else ""
				encabezado = "Error" if datos.get("error") else "Resultado"
				almacen.actualizar_mensaje(existente, detalle=(detalle + "\n\n" if detalle else "") + f"{encabezado}:\n{salida}")
				datos["id_mensaje"] = existente
		elif tipo is TipoEvento.ARCHIVO:
			ruta = str(datos.get("ruta") or evento.texto)
			nombre_tipo = {"nuevo": "Archivo nuevo", "borrado": "Archivo borrado"}.get(str(datos.get("tipo")), "Archivo modificado")
			datos["id_mensaje"] = almacen.anadir_mensaje(clave, "cambio", f"{nombre_tipo}: {ruta}",
				detalle=str(datos.get("diff") or ""), datos={"cambios": [{"ruta": ruta, "diff": str(datos.get("diff") or "")}]})
		elif tipo is TipoEvento.PLAN:
			pasos = datos.get("pasos") or []
			texto = "\n".join(f"{_marca_paso(estado)} {paso}" for paso, estado in pasos)
			if texto:
				datos["id_mensaje"] = almacen.anadir_mensaje(clave, "plan", texto)
		elif tipo is TipoEvento.ERROR:
			datos["id_mensaje"] = almacen.anadir_mensaje(clave, "error", evento.texto)
		elif tipo is TipoEvento.AVISO:
			if not datos.get("en_cola"):
				datos["id_mensaje"] = almacen.anadir_mensaje(clave, "sistema", evento.texto)
			if "modelo_sustituido" in datos and clave.startswith("ctrlia:"):
				# Codex cambió un modelo retirado por otro, o Antigravity ya no conoce el suyo (vacío: el
				# predeterminado): se guarda, para no volver a fallar al abrir Ctrl+IA.
				almacen.actualizar_sesion(clave, modelo=str(datos["modelo_sustituido"] or ""))
			if datos.get("reintentable") and datos.get("solicitud_id"):
				# Antigravity no pregunta: deniega y avisa. Se ofrece como una petición más.
				from app.services.agentes.antigravity import OPCION_SERVIDOR_REINTENTAR, regla_servidor_mcp

				regla = str(datos.get("regla") or "")
				opciones = [
					OpcionPermiso("permitir_reintentar", "Permitir y reintentar", ClaseOpcion.PERMITIR,
						"Añade la regla de permiso y pide a Antigravity que lo vuelva a intentar."),
				]
				servidor = regla_servidor_mcp(regla)
				if servidor and servidor != regla:
					opciones.append(OpcionPermiso(OPCION_SERVIDOR_REINTENTAR, "Permitir todo el servidor y reintentar",
						ClaseOpcion.PERMITIR, "Añade la regla %s, que permite todas las herramientas de ese servidor MCP, "
						"y pide a Antigravity que lo vuelva a intentar." % servidor))
				opciones.append(OpcionPermiso("no_permitir", "No permitir", ClaseOpcion.DENEGAR))
				solicitud = SolicitudPermiso(
					str(datos["solicitud_id"]), TipoSolicitud.HERRAMIENTA, evento.texto,
					detalle=evento.texto + "\n\nRegla que se añadiría: " + regla,
					herramienta=regla, opciones=opciones,
				)
				with self._candado:
					self._solicitudes.setdefault(clave, {})[solicitud.id] = solicitud
				datos["solicitud"] = solicitud
				# Sin «id_mensaje»: el aviso ya refresca su propia fila.
				self._apuntar_solicitud(clave, solicitud)
		elif tipo is TipoEvento.PERMISO:
			solicitud: SolicitudPermiso = datos["solicitud"]
			with self._candado:
				self._solicitudes.setdefault(clave, {})[solicitud.id] = solicitud
			id_mensaje = self._apuntar_solicitud(clave, solicitud)
			if id_mensaje:
				datos["id_mensaje"] = id_mensaje  # la interfaz refresca la fila (su indicador)
		elif tipo is TipoEvento.PERMISO_RETIRADO:
			identificador = str(datos.get("id") or "")
			with self._candado:
				self._solicitudes.get(clave, {}).pop(identificador, None)
			id_mensaje = self._cambiar_solicitud(clave, identificador, estado="retirada")
			if id_mensaje:
				datos["id_mensaje"] = id_mensaje
		elif tipo is TipoEvento.ESTADO:
			almacen.actualizar_sesion(clave, estado=str(datos.get("estado") or ""), actividad=time.time())
		elif tipo is TipoEvento.USO:
			actual = self._uso.setdefault(clave, {})
			actual.update({k: v for k, v in datos.items() if v is not None})
		elif tipo is TipoEvento.LIMITES:
			sesion = almacen.sesion(clave)
			if sesion is not None:
				self._limites.setdefault(sesion.proveedor, {})[str(datos.get("ventana") or "")] = dict(datos, texto=evento.texto)
		elif tipo is TipoEvento.COMANDOS:
			sesion = almacen.sesion(clave)
			if sesion is not None:
				self._comandos[sesion.proveedor] = list(datos.get("comandos") or [])
		elif tipo is TipoEvento.FIN_TURNO:
			self._parciales.pop(clave, None)
			if datos.get("interrumpido"):
				id_mensaje = self._apuntar_detenido(clave)
				if id_mensaje and not datos.get("id_mensaje"):
					datos["id_mensaje"] = id_mensaje
			sesion = almacen.sesion(clave)
			if sesion is not None:
				no_leidas = sesion.no_leidas + (0 if self.sesion_abierta == clave else 1)
				almacen.actualizar_sesion(clave, actividad=time.time(), no_leidas=no_leidas)
			# Foto de cómo dejó el agente la carpeta (y punto del mensaje que esperaba en cola).
			self._punto_fin_turno(clave)


#: Hasta cuántos caracteres de cada texto de una petición se guardan (un plan puede ser largo).
MAX_TEXTO_SOLICITUD = 20000


def resumen_solicitud(solicitud: SolicitudPermiso) -> dict[str, Any]:
	"""Lo que se guarda de una petición del agente en ``datos["solicitudes"]`` del mensaje.

	Solo texto (se guarda como JSON). ``estado`` empieza en «pendiente» y pasa a
	«respondida» (con ``respuesta``) o «retirada».
	"""
	def corto(texto: Any) -> str:
		return str(texto or "")[:MAX_TEXTO_SOLICITUD]

	return {
		"id": str(solicitud.id),
		"tipo": str(solicitud.tipo),
		"titulo": corto(solicitud.titulo),
		"herramienta": corto(solicitud.herramienta),
		"detalle": corto(solicitud.detalle),
		"plan": corto(solicitud.plan),
		"contexto": corto(solicitud.contexto),
		"preguntas": [
			{
				"id": str(pregunta.id), "texto": corto(pregunta.texto), "encabezado": corto(pregunta.encabezado),
				"opciones": [corto(opcion[0] if isinstance(opcion, (tuple, list)) else opcion) for opcion in pregunta.opciones],
				"multiple": bool(pregunta.multiple),
			}
			for pregunta in solicitud.preguntas
		],
		"opciones": [{"id": o.id, "etiqueta": o.etiqueta, "clase": str(o.clase)} for o in solicitud.opciones],
		"fecha": time.time(),
		"estado": "pendiente",
	}


def _marca_paso(estado: str) -> str:
	estado = (estado or "").lower()
	if estado in ("completed", "hecho", "done", "completado"):
		return "[hecho]"
	if estado in ("in_progress", "inprogress", "en_curso", "active"):
		return "[en curso]"
	return "[pendiente]"


def _extra_equipo(equipo: remoto.EquipoSSH) -> dict[str, Any]:
	"""Los datos de sesión que llevan a un agente al equipo por SSH (``remoto.equipo_de``)."""
	return {"remoto": {"equipo": equipo.id, "nombre": equipo.nombre}}


def _sistema_de(equipos: dict[str, remoto.EquipoSSH], extra: dict[str, Any] | None) -> str:
	"""«macOS» o «Linux» si la sesión trabaja en un equipo por SSH que sigue guardado; si no, ``""``."""
	datos = remoto.datos_remotos(extra)
	equipo = equipos.get(str(datos["equipo"])) if datos else None
	return equipo.nombre_sistema if equipo is not None else ""


def _segundos(valor: Any) -> float:
	"""Fecha como segundos desde 1970, venga como número o como ``datetime``."""
	if valor is None:
		return 0.0
	if hasattr(valor, "timestamp"):
		return float(valor.timestamp())
	try:
		return float(valor)
	except (TypeError, ValueError):
		return 0.0


def _recortar(texto: str, maximo: int = 150) -> str:
	texto = " ".join((texto or "").split())
	return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"
