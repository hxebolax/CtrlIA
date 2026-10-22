"""Contrato común de los agentes de IA (Claude Code, Codex, Antigravity, ACP...).

Ctrl+IA no habla con ningún modelo directamente: lanza en local el programa
oficial de cada agente con la sesión que el usuario ya tiene iniciada y
conversa con él por su protocolo de línea de comandos. Cada agente se adapta
a este módulo, de modo que la interfaz solo conoce tres cosas:

- :class:`Proveedor`: un agente instalable (detectarlo, sus modelos, sus
  modos de permiso) y la fábrica de conversaciones.
- :class:`Conversacion`: una sesión viva con ese agente (enviar, detener,
  responder a un permiso, cerrar).
- :class:`Evento`: todo lo que la conversación cuenta mientras trabaja.

Reglas que cumplen TODAS las implementaciones:

- Los eventos se emiten desde hilos de fondo; quien los recibe (la interfaz)
  es responsable de volver al hilo principal con ``wx.CallAfter``.
- Una petición de permiso nunca se responde sola por tiempo agotado: espera
  al usuario. Si la conversación se detiene, se retira con
  ``PERMISO_RETIRADO``.
- Un mensaje cuyo envío quedó en duda nunca se reenvía automáticamente.
- Ningún módulo de este paquete importa wx.
"""

from __future__ import annotations

import enum
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class TipoEvento(enum.StrEnum):
	"""Clases de evento que una conversación puede emitir."""

	#: El agente ha arrancado; ``datos``: ``id_externo``, ``modelo``, ``modo``.
	INICIADO = "iniciado"
	#: Trozo de texto de la respuesta que se está escribiendo; ``datos["id"]``.
	TEXTO_PARCIAL = "texto_parcial"
	#: Mensaje completo del asistente (fuente autorizada); ``datos["id"]``.
	MENSAJE = "mensaje"
	#: Razonamiento visible del modelo (solo si el agente lo publica).
	RAZONAMIENTO = "razonamiento"
	#: Empieza una herramienta; ``datos``: ``herramienta``, ``detalle``, ``id``.
	HERRAMIENTA = "herramienta"
	#: Termina una herramienta; ``datos``: ``herramienta``, ``salida``, ``error``, ``id``.
	HERRAMIENTA_FIN = "herramienta_fin"
	#: Cambio de un archivo; ``datos``: ``ruta``, ``tipo``, ``diff``.
	ARCHIVO = "archivo"
	#: El agente pide permiso o pregunta algo; ``datos["solicitud"]``.
	PERMISO = "permiso"
	#: Una solicitud ya no necesita respuesta; ``datos["id"]``.
	PERMISO_RETIRADO = "permiso_retirado"
	#: Plan o lista de tareas actualizada; ``datos["pasos"]`` = [(texto, estado)].
	PLAN = "plan"
	#: Consumo; ``datos``: ``entrada``, ``salida``, ``contexto_usado``, ``contexto_total``, ``coste``.
	USO = "uso"
	#: Límites de la cuenta; ``datos``: ``porcentaje``, ``ventana``, ``reinicio``.
	LIMITES = "limites"
	#: Comandos de barra disponibles; ``datos["comandos"]`` = [(nombre, descripción)].
	COMANDOS = "comandos"
	#: Aviso informativo (acción denegada, contexto compactado, reintento...).
	AVISO = "aviso"
	#: Error que el usuario debe conocer.
	ERROR = "error"
	#: Cambio de estado (``Estado``) en ``datos["estado"]``.
	ESTADO = "estado"
	#: Fin del turno; ``datos``: ``correcto``, ``interrumpido``, ``resumen``.
	FIN_TURNO = "fin_turno"


class Estado(enum.StrEnum):
	"""Estado de una conversación tal como lo ve el usuario."""

	INACTIVA = "inactiva"
	TRABAJANDO = "trabajando"
	ESPERANDO_USUARIO = "esperando"
	SEGUNDO_PLANO = "segundo_plano"
	ERROR = "error"
	CERRADA = "cerrada"


#: Texto en español de cada estado (para listas y anuncios).
NOMBRES_ESTADO: dict[Estado, str] = {
	Estado.INACTIVA: "Inactiva",
	Estado.TRABAJANDO: "Trabajando",
	Estado.ESPERANDO_USUARIO: "Te necesita",
	Estado.SEGUNDO_PLANO: "Trabajo en segundo plano",
	Estado.ERROR: "Error",
	Estado.CERRADA: "Cerrada",
}


@dataclass(slots=True)
class Evento:
	"""Algo que ha pasado en una conversación."""

	tipo: TipoEvento
	texto: str = ""
	datos: dict[str, Any] = field(default_factory=dict)


class ClaseOpcion(enum.StrEnum):
	"""Qué significa cada botón de una petición de permiso."""

	PERMITIR = "permitir"
	PERMITIR_SESION = "permitir_sesion"
	PERMITIR_SIEMPRE = "permitir_siempre"
	DENEGAR = "denegar"
	DENEGAR_SIEMPRE = "denegar_siempre"
	CANCELAR = "cancelar"


@dataclass(slots=True)
class OpcionPermiso:
	"""Una respuesta posible a una petición de permiso."""

	id: str
	etiqueta: str
	clase: ClaseOpcion
	ayuda: str = ""


@dataclass(slots=True)
class Pregunta:
	"""Pregunta del agente al usuario (con opciones o de respuesta libre)."""

	id: str
	texto: str
	encabezado: str = ""
	opciones: list[tuple[str, str]] = field(default_factory=list)
	multiple: bool = False
	admite_otra: bool = True
	secreta: bool = False
	#: Vista previa de cada opción (un borrador, código...), en el mismo orden; «» si no tiene (AIChat #276).
	vistas_previas: list[str] = field(default_factory=list)


class TipoSolicitud(enum.StrEnum):
	HERRAMIENTA = "herramienta"
	PREGUNTA = "pregunta"
	PLAN = "plan"


@dataclass(slots=True)
class SolicitudPermiso:
	"""Petición del agente que espera una decisión del usuario."""

	id: str
	tipo: TipoSolicitud
	titulo: str
	detalle: str = ""
	herramienta: str = ""
	opciones: list[OpcionPermiso] = field(default_factory=list)
	preguntas: list[Pregunta] = field(default_factory=list)
	plan: str = ""
	datos: dict[str, Any] = field(default_factory=dict)
	#: Lo que el agente escribió justo antes de preguntar (un borrador, un plan...).
	contexto: str = ""

	def opcion_segura(self) -> OpcionPermiso | None:
		"""La opción que no hace nada peligroso (botón por defecto del diálogo)."""
		for clase in (ClaseOpcion.DENEGAR, ClaseOpcion.CANCELAR, ClaseOpcion.DENEGAR_SIEMPRE):
			for opcion in self.opciones:
				if opcion.clase is clase:
					return opcion
		return None


@dataclass(slots=True)
class RespuestaPermiso:
	"""Lo que el usuario decidió ante una :class:`SolicitudPermiso`."""

	opcion: str
	mensaje: str = ""
	respuestas: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class OpcionLista:
	"""Elemento de un desplegable (modelo, esfuerzo, modo de permisos)."""

	id: str
	etiqueta: str
	ayuda: str = ""


@dataclass(slots=True)
class AjustesSesion:
	"""Cómo se abre o se continúa una conversación."""

	carpeta: Path
	modelo: str = ""
	esfuerzo: str = ""
	modo_permiso: str = ""
	titulo: str = ""
	#: Identificador de la conversación en el propio agente (para retomarla).
	id_externo: str = ""
	#: Si no está vacío, se crea una conversación nueva copiando esta.
	bifurcar_de: str = ""
	#: Reglas «permitir durante esta sesión» ya concedidas.
	reglas_permitidas: list[str] = field(default_factory=list)
	#: Opciones propias de cada agente.
	extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Adjunto:
	"""Archivo que acompaña a un mensaje."""

	ruta: Path
	tipo_mime: str = ""

	@property
	def es_imagen(self) -> bool:
		return self.tipo_mime.startswith("image/")


@dataclass(slots=True)
class EstadoProveedor:
	"""Resultado de detectar un agente en el equipo."""

	disponible: bool
	ruta: str = ""
	version: str = ""
	mensaje: str = ""
	sesion_iniciada: bool | None = None
	cuenta: str = ""


@dataclass(slots=True)
class Capacidades:
	"""Qué sabe hacer un agente (la interfaz oculta lo que no aplica)."""

	adjuntar_imagenes: bool = False
	adjuntar_archivos: bool = True
	bifurcar: bool = False
	permisos_interactivos: bool = True
	cambiar_modelo_en_marcha: bool = True
	comandos_barra: bool = False
	enviar_ahora: bool = False
	#: Puede recibir un mensaje dentro del turno en marcha sin detenerlo.
	anadir_al_turno: bool = False
	#: Carga los agentes del catálogo como habilidades propias (si no, se le presentan en el mensaje).
	agentes_nativos: bool = False
	leer_historial_externo: bool = False


ReceptorEventos = Callable[[Evento], None]


class Conversacion(ABC):
	"""Sesión viva con un agente. Todos los métodos son seguros desde la interfaz."""

	def __init__(self, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> None:
		self.ajustes = ajustes
		self._al_evento = al_evento
		#: Agentes del catálogo elegidos para la sesión (``catalogo_agentes.datos_para_sesion``); los pone el gestor.
		self.agentes: list[dict[str, Any]] = []
		#: Identificador (uuid) del último mensaje del usuario enviado; solo lo dan los agentes que saben deshacer
		#: los cambios de archivos de un mensaje (Claude Code, ``deshacer_archivos``).
		self.ultimo_uuid_usuario = ""
		#: Herramientas propias de Ctrl+IA de esta sesión (``herramientas_propias.SesionHerramientas``), si se dieron.
		self.herramientas_ctrlia: Any = None
		self._estado = Estado.INACTIVA
		self._candado_estado = threading.Lock()

	# -- Lo que implementa cada agente ---------------------------------------

	@abstractmethod
	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		"""Envía un mensaje del usuario. Si hay un turno en marcha, lo pone en cola."""

	@abstractmethod
	def detener(self) -> None:
		"""Interrumpe el turno en curso (no reenvía nada)."""

	@abstractmethod
	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		"""Contesta a una :class:`SolicitudPermiso` pendiente."""

	@abstractmethod
	def cerrar(self) -> None:
		"""Libera procesos y conexiones. Idempotente."""

	def enviar_ahora(self, texto: str) -> None:
		"""Interrumpe y envía de inmediato (solo si ``Capacidades.enviar_ahora``)."""
		self.detener()
		self.enviar(texto)

	def inventario(self) -> Any:
		"""Herramientas, servidores MCP, complementos y habilidades cargados (``inventario.Inventario``), o ``None``.

		Puede bloquear (Codex pregunta a su servidor): llamar desde un hilo.
		"""
		return None

	def anadir_al_turno(self, texto: str) -> None:
		"""Mete el mensaje en el turno en marcha sin detenerlo (solo si ``Capacidades.anadir_al_turno``).

		Si no se puede entregar, NUNCA se envía como mensaje normal: se emite un
		``AVISO`` con ``devolver_texto`` para que la interfaz lo devuelva al
		cuadro de respuesta y el usuario decida.
		"""
		raise NotImplementedError("Este agente no admite añadir mensajes al turno en marcha")

	def cambiar_ajustes(self, **cambios: Any) -> None:
		"""Cambia modelo, esfuerzo o modo de permisos para los turnos siguientes."""
		for nombre, valor in cambios.items():
			if hasattr(self.ajustes, nombre):
				setattr(self.ajustes, nombre, valor)

	def pendientes(self) -> list[str]:
		"""Mensajes en cola que aún no se han enviado (para devolverlos al cuadro)."""
		return []

	def deshacer_archivos(self, uuid_mensaje: str, prueba: bool = False) -> dict[str, Any] | None:
		"""Devuelve los archivos al estado de antes del mensaje ``uuid_mensaje`` (Claude Code).

		Con ``prueba`` solo dice qué cambiaría. Devuelve ``{"canRewind", "error",
		"filesChanged", "insertions", "deletions"}``, o ``None`` si este agente no
		sabe deshacer. Bloquea: llamar desde un hilo y con el agente parado.
		"""
		return None

	# -- Herramientas propias de Ctrl+IA (servidor MCP «ctrlia») ---------------

	def _preparar_herramientas_ctrlia(self, agente: str) -> Any:
		"""Las herramientas de Ctrl+IA de esta sesión (las crea la primera vez); ``None`` si están desactivadas."""
		from app.services import herramientas_propias

		if not herramientas_propias.activadas():
			if self.herramientas_ctrlia is not None:
				# Se han desactivado en Preferencias: su token deja de valer.
				herramientas_propias.retirar(self.herramientas_ctrlia)
				self.herramientas_ctrlia = None
			return None
		if self.herramientas_ctrlia is None or self.herramientas_ctrlia.retirada:
			self.herramientas_ctrlia = herramientas_propias.registrar(self._uso_herramienta_ctrlia, agente)
		return self.herramientas_ctrlia

	def _retirar_herramientas_ctrlia(self) -> None:
		from app.services import herramientas_propias

		herramientas_propias.retirar(self.herramientas_ctrlia)

	def _uso_herramienta_ctrlia(self, accion: str, datos: dict[str, Any]) -> None:
		"""El agente ha usado una herramienta de Ctrl+IA: queda como aviso (mensaje de sistema) y la interfaz actúa."""
		agente = getattr(self.herramientas_ctrlia, "agente", "") or "El agente"
		if accion == "avisar":
			texto = "%s te avisa: %s" % (agente, datos.get("texto", ""))
		elif accion == "dar_por_terminada":
			motivo = str(datos.get("motivo") or "")
			texto = "%s ha dado la sesión por terminada%s." % (agente, ": " + motivo.rstrip(".") if motivo else "")
		elif accion == "renombrar_sesion":
			texto = "%s ha renombrado la sesión: «%s»." % (agente, datos.get("titulo", ""))
		else:
			return
		self._emitir(TipoEvento.AVISO, texto, herramienta_ctrlia=accion, **datos)

	# -- Comunes --------------------------------------------------------------

	@property
	def id_externo(self) -> str:
		return self.ajustes.id_externo

	@property
	def estado(self) -> Estado:
		return self._estado

	@property
	def ocupada(self) -> bool:
		return self._estado in (Estado.TRABAJANDO, Estado.ESPERANDO_USUARIO, Estado.SEGUNDO_PLANO)

	def _emitir(self, tipo_evento: TipoEvento, texto_evento: str = "", /, **datos: Any) -> None:
		# Solo posicionales: así ``datos`` puede llevar claves «tipo» o «texto».
		try:
			self._al_evento(Evento(tipo_evento, texto_evento, datos))
		except Exception:  # noqa: BLE001 - un receptor roto no debe matar el lector del agente
			log.exception("El receptor de eventos falló con %s", tipo_evento)

	def _cambiar_estado(self, estado: Estado) -> None:
		with self._candado_estado:
			if estado is self._estado:
				return
			self._estado = estado
		self._emitir(TipoEvento.ESTADO, NOMBRES_ESTADO[estado], estado=estado)


class Proveedor(ABC):
	"""Un agente de IA que Ctrl+IA sabe manejar."""

	#: Identificador estable (se guarda en la base de datos).
	id: str = ""
	#: Nombre visible.
	nombre: str = ""
	#: Frase que explica qué es (se muestra en «Agentes instalados»).
	descripcion: str = ""
	#: Página oficial con instrucciones de instalación.
	web: str = ""
	capacidades: Capacidades = Capacidades()

	@abstractmethod
	def detectar(self) -> EstadoProveedor:
		"""Busca el programa del agente. Puede tardar: llamar desde un hilo."""

	@abstractmethod
	def crear_conversacion(self, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> Conversacion:
		"""Crea una conversación (no envía nada hasta ``enviar``)."""

	def modelos(self) -> list[OpcionLista]:
		"""Modelos conocidos. El primero, vacío, significa «el predeterminado del agente»."""
		return [OpcionLista("", "Predeterminado del agente")]

	def esfuerzos(self) -> list[OpcionLista]:
		return []

	def modos_permiso(self) -> list[OpcionLista]:
		return []

	def estilos(self) -> list[OpcionLista]:
		"""Estilos de respuesta que se pueden elegir por sesión (``extra["estilo"]``); vacío si el agente no tiene."""
		return []

	def modo_de_serie(self) -> str:
		"""Modo de permisos que se propone si la configuración del agente no dice otro (el primero)."""
		modos = self.modos_permiso()
		return modos[0].id if modos else ""

	def modo_predeterminado(self, carpeta: Path | None = None) -> str:
		"""Modo de permisos que se propone al crear una sesión en ``carpeta``.

		Los agentes con archivo de configuración proponen su modo de permisos
		inicial; los demás, ``modo_de_serie``.
		"""
		return self.modo_de_serie()

	def leer_limites(self) -> Any:
		"""Límites de uso de la cuenta (5 horas y semanal) sin enviar mensajes ni gastar cuota.

		Devuelve un ``app.services.limites.LimitesCuenta`` o ``None`` si este agente
		no sabe decirlos. Lanza ``app.services.limites.ErrorLimites`` con un mensaje
		para la persona si debería saberlos y no puede. Bloquea: llamar desde un hilo.
		"""
		return None

	def instrucciones_instalacion(self) -> str:
		"""Texto para el usuario con los pasos para instalar el agente."""
		return ""

	def orden_instalacion(self) -> list[str] | None:
		"""Orden que instala el agente en una consola nueva, si existe."""
		return None

	def orden_inicio_sesion(self) -> list[str] | None:
		"""Orden que abre el inicio de sesión del agente en una consola nueva."""
		return None
