"""Ollama: conversar con modelos locales (solo chat, sin herramientas).

Ollama (https://ollama.com) ejecuta modelos de lenguaje en el propio equipo y
los sirve por HTTP en ``http://127.0.0.1:11434``. A diferencia de Claude Code,
Codex o Antigravity, Ollama NO es un agente de programación: solo contesta
mensajes. No lee ni cambia archivos, no ejecuta órdenes y no pide permisos.
Por eso este adaptador es un modo «solo chat», y así se dice en la interfaz.

Lo que usa de la API de Ollama:

- ``GET /api/version``: comprobar que el servidor está en marcha.
- ``GET /api/tags``: modelos instalados (``ollama pull <modelo>``).
- ``GET /api/ps``: modelos ya cargados en memoria (para avisar de que el
  primero tardará mientras se carga).
- ``POST /api/chat`` con ``stream: true``: una línea JSON por trozo
  (``message.content``, ``message.thinking``) y una final con ``done: true``
  y el consumo (``prompt_eval_count``, ``eval_count``).

Ollama no guarda las conversaciones que recibe por su API: cada petición
lleva el historial entero. Ctrl+IA lo guarda en ``data/ollama/<id>.json``
(solo texto; las imágenes adjuntas se envían en su turno y no se guardan),
así una sesión se puede retomar al volver a abrir el programa y bifurcar.

Reglas que se cumplen aquí:

- Nunca se envía una clave ni una cabecera de autorización: Ollama local no
  la necesita y Ctrl+IA no usa claves de API.
- Una petición de chat (POST) nunca se reintenta sola. La única repetición es
  cuando Ollama rechaza el parámetro de razonamiento con un 400 (no ha hecho
  nada) y se vuelve a pedir sin él.
- Detener corta la conexión (Ollama deja de generar al perder al cliente).
- Ningún módulo de este paquete importa wx.
"""

from __future__ import annotations

import base64
import collections
import itertools
import json
import logging
import mimetypes
import os
import re
import socket
import tempfile
import threading
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	Capacidades,
	Conversacion,
	Estado,
	EstadoProveedor,
	OpcionLista,
	Proveedor,
	ReceptorEventos,
	RespuestaPermiso,
	TipoEvento,
)

log = logging.getLogger(__name__)

#: Dirección del servidor si no se indica otra (ni en Preferencias ni en ``OLLAMA_HOST``).
SERVIDOR_PREDETERMINADO = "http://127.0.0.1:11434"
PUERTO_PREDETERMINADO = 11434
#: Segundos para conectar y para esperar cada trozo de la respuesta (cargar un
#: modelo grande en memoria puede tardar minutos antes del primer trozo).
TIEMPO_CONEXION = 5.0
TIEMPO_LECTURA = 600.0
#: Tiempos de las consultas rápidas (versión, modelos).
TIEMPO_CONSULTA = (3.0, 10.0)
#: Tamaño máximo del texto de un archivo adjunto que se copia en el mensaje.
MAX_TEXTO_ADJUNTO = 200_000
MAX_IMAGEN = 20 * 1024 * 1024
#: Mensajes del historial que se guardan como mucho (los más antiguos se descartan).
MAX_HISTORIAL = 400

_ID_VALIDO = re.compile(r"[0-9a-f]{32}")

#: Niveles de razonamiento (``think``) que se ofrecen en «Esfuerzo».
_ESFUERZOS = (
	OpcionLista("", "Predeterminado del modelo"),
	OpcionLista("no", "Sin razonar", "Pide al modelo que responda directamente, sin fase de razonamiento."),
	OpcionLista("si", "Razonar", "Pide al modelo que razone antes de responder (solo modelos que lo admiten)."),
	OpcionLista("low", "Razonamiento bajo", "Solo para modelos con niveles de razonamiento, como gpt-oss."),
	OpcionLista("medium", "Razonamiento medio", "Solo para modelos con niveles de razonamiento, como gpt-oss."),
	OpcionLista("high", "Razonamiento alto", "Solo para modelos con niveles de razonamiento, como gpt-oss."),
)

MODO_CHAT = "chat"
_MODOS = (
	OpcionLista(
		MODO_CHAT,
		"Solo conversación (sin herramientas)",
		"El modelo solo responde con texto: no puede leer ni cambiar archivos ni ejecutar órdenes. "
		"Si adjuntas un archivo de texto, su contenido se copia en tu mensaje.",
	),
)

INSTRUCCIONES = (
	"Para usar Ollama: 1) descárgalo de ollama.com e instálalo (o, en una consola, escribe: "
	"winget install --id Ollama.Ollama -e); 2) ábrelo: queda en marcha junto al reloj; "
	"3) descarga al menos un modelo escribiendo en una consola, por ejemplo: ollama pull llama3.2. "
	"Todo se queda en tu equipo y no cuesta nada."
)


class ErrorOllama(Exception):
	"""Error con un mensaje apto para el usuario.

	:param enviado: ``True`` si Ollama llegó a recibir el mensaje (entonces se
		guarda en el historial aunque la respuesta falle).
	"""

	def __init__(self, mensaje: str, enviado: bool = False, parcial: str = "") -> None:
		super().__init__(mensaje)
		self.mensaje = mensaje
		self.enviado = enviado
		#: Lo que el modelo llegó a escribir antes del fallo.
		self.parcial = parcial
		#: No se pudo ni conectar con el servidor.
		self.sin_conexion = False


# --------------------------------------------------------------------------
# Dirección del servidor
# --------------------------------------------------------------------------


def normalizar_direccion(texto: str | None) -> str:
	"""Convierte lo que escribe el usuario (o ``OLLAMA_HOST``) en una URL base.

	``""`` → la predeterminada; ``localhost`` → ``http://localhost:11434``;
	``0.0.0.0:11434`` (dirección de escucha) → ``http://127.0.0.1:11434``;
	se quitan la barra final y un ``/api`` sobrante.
	"""
	texto = (texto or "").strip().strip('"').strip()
	if not texto:
		return SERVIDOR_PREDETERMINADO
	if "://" not in texto:
		texto = "http://" + texto
	partes = urlsplit(texto)
	esquema = partes.scheme.lower() if partes.scheme.lower() in ("http", "https") else "http"
	host = partes.hostname or "127.0.0.1"
	if host in ("0.0.0.0", "::", ""):  # noqa: S104 - dirección de escucha, no de conexión
		host = "127.0.0.1"
	try:
		puerto = partes.port
	except ValueError:
		puerto = None
	if puerto is None and not partes.netloc.rstrip("]").endswith(":") and esquema == "http":
		puerto = PUERTO_PREDETERMINADO
	host_url = "[%s]" % host if ":" in host else host
	netloc = "%s:%d" % (host_url, puerto) if puerto else host_url
	ruta = partes.path.rstrip("/")
	if ruta.endswith("/api"):
		ruta = ruta[: -len("/api")]
	return urlunsplit((esquema, netloc, ruta, "", ""))


def es_local(direccion: str) -> bool:
	"""``True`` si la dirección apunta a este mismo equipo."""
	host = (urlsplit(direccion).hostname or "").lower()
	return host in ("localhost", "::1") or host.startswith("127.") or host.endswith(".localhost")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _sesion_http(direccion: str, tiempo_lectura: float, reintentos: int = 0):  # noqa: ANN202 - requests.Session
	"""Sesión de requests con tiempos de espera; sin proxy si el servidor es local."""
	from app.core.red import ConfiguracionRed, crear_sesion
	from app.core.version import APP_VERSION

	configuracion = ConfiguracionRed(
		version_app=APP_VERSION,
		tiempo_conexion=TIEMPO_CONEXION if tiempo_lectura > TIEMPO_CONSULTA[1] else TIEMPO_CONSULTA[0],
		tiempo_lectura=tiempo_lectura,
		reintentos=reintentos,
		proxy="ninguno" if es_local(direccion) else "sistema",
	)
	return crear_sesion(configuracion)


def _mensaje_conexion(direccion: str) -> str:
	if es_local(direccion):
		return (
			"No se puede conectar con Ollama en %s. Comprueba que Ollama está abierto (su icono aparece junto al reloj) "
			"o escribe «ollama serve» en una consola." % direccion
		)
	return "No se puede conectar con el servidor de Ollama en %s. Comprueba la dirección en Preferencias, Agentes." % direccion


def _error_de_respuesta(respuesta: Any, modelo: str = "") -> str:
	"""Texto para el usuario a partir de una respuesta de error de Ollama."""
	detalle = ""
	try:
		datos = respuesta.json()
		if isinstance(datos, dict):
			detalle = str(datos.get("error") or "")
	except ValueError:
		detalle = (respuesta.text or "")[:300]
	codigo = respuesta.status_code
	if codigo == 404 and ("not found" in detalle.lower() or not detalle):
		if modelo:
			return (
				"El modelo «%s» no está instalado en Ollama. Descárgalo escribiendo en una consola: ollama pull %s"
				% (modelo, modelo)
			)
		return "Este servidor no parece ser Ollama (error 404). Comprueba la dirección en Preferencias, Agentes."
	if codigo in (401, 403):
		return (
			"Ollama ha pedido iniciar sesión (error %d). Los modelos «en la nube» de Ollama necesitan una cuenta: "
			"escribe «ollama signin» en una consola o elige un modelo local." % codigo
		)
	from app.core.red import clasificar_error

	base = clasificar_error(respuesta=respuesta).mensaje
	return ("%s Ollama dice: %s" % (base, detalle)).strip() if detalle else base


def _formato_tamano(octetos: Any) -> str:
	try:
		valor = float(octetos)
	except (TypeError, ValueError):
		return ""
	if valor <= 0:
		return ""
	for unidad in ("bytes", "KB", "MB", "GB", "TB"):
		if valor < 1024 or unidad == "TB":
			texto = ("%d" % valor) if unidad == "bytes" else ("%.1f" % valor).replace(".", ",")
			return "%s %s" % (texto, unidad)
		valor /= 1024
	return ""


def opcion_de_modelo(modelo: dict[str, Any]) -> OpcionLista | None:
	"""Elemento del desplegable «Modelo» a partir de una entrada de ``/api/tags``."""
	nombre = str(modelo.get("model") or modelo.get("name") or "").strip()
	if not nombre:
		return None
	detalles = modelo.get("details") if isinstance(modelo.get("details"), dict) else {}
	partes = [nombre]
	if detalles.get("parameter_size"):
		partes.append(str(detalles["parameter_size"]))
	tamano = _formato_tamano(modelo.get("size"))
	nube = bool(modelo.get("remote_host") or modelo.get("remote_model")) or nombre.endswith("-cloud") or ":cloud" in nombre
	if nube:
		partes.append("en la nube de Ollama: tus mensajes salen del equipo")
	elif tamano:
		partes.append(tamano)
	ayuda = []
	if detalles.get("family"):
		ayuda.append("Familia: %s." % detalles["family"])
	if detalles.get("quantization_level"):
		ayuda.append("Cuantización: %s." % detalles["quantization_level"])
	if nube:
		ayuda.append("Se ejecuta en los servidores de Ollama, no en tu equipo, y necesita una cuenta de Ollama.")
	return OpcionLista(nombre, ", ".join(partes), " ".join(ayuda))


def _ordenar_por_fecha(modelos: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Más recientes primero (``modified_at`` es ISO 8601: se ordena como texto)."""
	return sorted(modelos, key=lambda m: str(m.get("modified_at") or ""), reverse=True)


def buscar_ollama() -> Path | None:
	"""Programa ``ollama.exe`` (PATH o la carpeta en la que lo deja su instalador)."""
	from app.services.agentes.procesos import buscar_ejecutable

	candidatos = []
	local = os.environ.get("LOCALAPPDATA")  # auditoria: ignorar ruta-no-portable - localiza Ollama instalado; no guarda datos
	if local:
		candidatos.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
	return buscar_ejecutable(["ollama"], candidatos)


def _cortar(respuesta: Any) -> None:
	"""Corta una respuesta en streaming desde otro hilo (despierta al lector bloqueado)."""
	crudo = getattr(respuesta, "raw", None)
	candidatos = []
	conexion = getattr(crudo, "_connection", None) or getattr(crudo, "connection", None)
	candidatos.append(getattr(conexion, "sock", None))
	fp = getattr(getattr(crudo, "_fp", None), "fp", None)
	candidatos.append(getattr(getattr(fp, "raw", None), "_sock", None))
	for sock in candidatos:
		if isinstance(sock, socket.socket):
			try:
				sock.shutdown(socket.SHUT_RDWR)
			except OSError:
				log.debug("No se pudo cerrar el socket de Ollama", exc_info=True)
			break
	try:
		respuesta.close()
	except Exception:  # noqa: BLE001 - cerrar una respuesta ya rota no debe fallar
		log.debug("No se pudo cerrar la respuesta de Ollama", exc_info=True)


# --------------------------------------------------------------------------
# Historial guardado por Ctrl+IA
# --------------------------------------------------------------------------


def carpeta_historiales() -> Path:
	from app.core.paths import subcarpeta

	return subcarpeta("ollama")


def ruta_historial(identificador: str) -> Path | None:
	"""Archivo del historial de una conversación (``None`` si el id no es válido)."""
	if not _ID_VALIDO.fullmatch(identificador or ""):
		return None
	return carpeta_historiales() / ("%s.json" % identificador)


def leer_historial(identificador: str) -> list[dict[str, str]]:
	"""Mensajes ``{"role", "content"}`` guardados; lista vacía si no hay o está dañado.

	Un identificador ``app-<id>`` es un chat de la app de Ollama (para «Continuar aquí»).
	"""
	from app.services.historial import ollama as historial_app

	if (identificador or "").startswith(historial_app.PREFIJO):
		return historial_app.historial_para_ollama(identificador.removeprefix(historial_app.PREFIJO))
	if not _ID_VALIDO.fullmatch(identificador or ""):
		return []
	ruta = carpeta_historiales() / ("%s.json" % identificador)
	if not ruta.is_file():
		return []
	try:
		datos = json.loads(ruta.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		log.warning("No se pudo leer el historial de Ollama %s", ruta.name, exc_info=True)
		return []
	mensajes = datos.get("mensajes") if isinstance(datos, dict) else None
	resultado: list[dict[str, str]] = []
	for mensaje in mensajes or []:
		if isinstance(mensaje, dict) and mensaje.get("role") in ("user", "assistant", "system") and isinstance(mensaje.get("content"), str):
			resultado.append({"role": mensaje["role"], "content": mensaje["content"]})
	return resultado


def guardar_historial(identificador: str, mensajes: Sequence[dict[str, Any]], modelo: str = "") -> None:
	"""Guarda el historial (solo texto) de forma atómica."""
	ruta = ruta_historial(identificador)
	if ruta is None:
		return
	limpios = [{"role": m["role"], "content": m.get("content", "")} for m in mensajes][-MAX_HISTORIAL:]
	contenido = json.dumps({"version": 1, "modelo": modelo, "mensajes": limpios}, ensure_ascii=False, indent=1)
	descriptor, temporal = tempfile.mkstemp(prefix=".%s." % ruta.stem, suffix=".tmp", dir=str(ruta.parent))
	try:
		with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
			archivo.write(contenido)
			archivo.flush()
			os.fsync(archivo.fileno())
		os.replace(temporal, ruta)
	except OSError:
		log.exception("No se pudo guardar el historial de Ollama %s", ruta.name)
		try:
			os.unlink(temporal)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


class ConversacionOllama(Conversacion):
	"""Chat con un modelo de Ollama. Un hilo envía los mensajes de la cola de uno en uno."""

	def __init__(self, ajustes: AjustesSesion, al_evento: ReceptorEventos, proveedor: ProveedorOllama) -> None:
		super().__init__(ajustes, al_evento)
		self.proveedor = proveedor
		self._candado = threading.RLock()
		self._cola: collections.deque[tuple[str, tuple[Adjunto, ...]]] = collections.deque()
		self._devueltos: list[str] = []
		self._trabajador_activo = False
		#: Señal de «parar» del turno en curso (una nueva por turno: así detener y
		#: enviar seguidos, como hace «Enviar ahora», no se pisan).
		self._parar: threading.Event | None = None
		self._cerrada = False
		self._respuesta: Any = None
		self._historial: list[dict[str, Any]] | None = None
		self._iniciada = False
		self._modelo_omision = ""
		self._contador = itertools.count(1)

	# -- Contrato ---------------------------------------------------------------

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		with self._candado:
			if self._cerrada:
				log.debug("Se ignora un envío a una conversación de Ollama cerrada")
				return
			self._cola.append((texto, tuple(adjuntos)))
			ocupada = self._trabajador_activo
			en_cola = len(self._cola)
			if not ocupada:
				self._trabajador_activo = True
		if ocupada:
			self._emitir(
				TipoEvento.AVISO,
				"Mensaje en cola: se enviará cuando el modelo termine (%d pendiente%s)." % (en_cola, "" if en_cola == 1 else "s"),
				en_cola=en_cola,
			)
			return
		threading.Thread(target=self._trabajar, name="ollama-turnos", daemon=True).start()

	def detener(self) -> None:
		"""Corta la respuesta en curso. Los mensajes en cola quedan en :meth:`pendientes`."""
		with self._candado:
			self._devueltos.extend(texto for texto, _adjuntos in self._cola)
			self._cola.clear()
			if self._parar is not None:
				self._parar.set()
			respuesta = self._respuesta
		if respuesta is not None:
			_cortar(respuesta)

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		# Ollama nunca pide permisos: no hay nada que contestar.
		log.debug("Ollama no tiene solicitudes pendientes (%s)", solicitud_id)

	def cerrar(self) -> None:
		with self._candado:
			if self._cerrada:
				return
			self._cerrada = True
			if self._parar is not None:
				self._parar.set()
			self._devueltos.extend(texto for texto, _adjuntos in self._cola)
			self._cola.clear()
			respuesta = self._respuesta
		if respuesta is not None:
			_cortar(respuesta)
		self._cambiar_estado(Estado.CERRADA)

	def pendientes(self) -> list[str]:
		with self._candado:
			textos = self._devueltos + [texto for texto, _adjuntos in self._cola]
			self._devueltos = []
			self._cola.clear()
		return textos

	def historial(self) -> list[tuple[str, str]]:
		"""Mensajes de la conversación: ``[(rol, texto)]`` con rol ``usuario`` o ``asistente``."""
		with self._candado:
			mensajes = list(self._historial or [])
		roles = {"user": "usuario", "assistant": "asistente"}
		return [(roles[m["role"]], m.get("content", "")) for m in mensajes if m.get("role") in roles]

	# -- Eventos ------------------------------------------------------------------

	def _emitir(self, tipo: TipoEvento, texto: str = "", /, **datos: Any) -> None:
		if self._cerrada and tipo is not TipoEvento.ESTADO:
			return
		super()._emitir(tipo, texto, **datos)

	# -- Hilo de turnos -------------------------------------------------------------

	def _trabajar(self) -> None:
		fallo = False
		while True:
			with self._candado:
				if self._cerrada or not self._cola:
					self._trabajador_activo = False
					break
				texto, adjuntos = self._cola.popleft()
				parar = threading.Event()
				self._parar = parar
			self._cambiar_estado(Estado.TRABAJANDO)
			try:
				if not self._turno(texto, adjuntos, parar):
					fallo = True
			except Exception:  # noqa: BLE001 - un fallo inesperado no debe dejar la sesión «trabajando»
				log.exception("Fallo inesperado en un turno de Ollama")
				fallo = True
				self._emitir(TipoEvento.ERROR, "Ha fallado la conversación con Ollama. Los detalles están en el registro.")
				self._emitir(TipoEvento.FIN_TURNO, "El turno ha fallado.", correcto=False, interrumpido=False,
					resumen="El turno ha fallado.")
			finally:
				with self._candado:
					if self._parar is parar:
						self._parar = None
		if not self._cerrada:
			self._cambiar_estado(Estado.ERROR if fallo else Estado.INACTIVA)

	def _preparar(self) -> list[dict[str, Any]]:
		"""Carga (una vez) el historial guardado, el de la conversación bifurcada o ninguno."""
		with self._candado:
			if self._historial is not None:
				return self._historial
		if self.ajustes.id_externo:
			historial = leer_historial(self.ajustes.id_externo)
		elif self.ajustes.bifurcar_de:
			historial = leer_historial(self.ajustes.bifurcar_de)
		else:
			historial = []
		with self._candado:
			if self._historial is None:
				self._historial = historial
			return self._historial

	def _turno(self, texto: str, adjuntos: Sequence[Adjunto], parar: threading.Event) -> bool:
		"""Envía un mensaje y lee la respuesta. ``False`` si algo falla."""
		historial = self._preparar()
		direccion = self.proveedor.direccion()
		try:
			modelo = self.ajustes.modelo or self._modelo_omision or self.proveedor.modelo_por_omision()
		except ErrorOllama as exc:
			return self._fallar(texto, exc)
		if not self.ajustes.modelo:
			self._modelo_omision = modelo
		if parar.is_set():
			with self._candado:
				self._devueltos.append(texto)
			self._emitir(TipoEvento.FIN_TURNO, "Turno detenido.", correcto=False, interrumpido=True, resumen="Turno detenido.")
			return True
		if not modelo:
			return self._fallar(texto, ErrorOllama(
				"No hay ningún modelo instalado en Ollama. Descarga uno escribiendo en una consola, por ejemplo: ollama pull llama3.2"
			))
		try:
			cargado = self.proveedor.cargado(modelo)
		except ErrorOllama as exc:
			return self._fallar(texto, exc)
		if not self._iniciada:
			self._iniciar(modelo, bool(historial))
		mensaje_usuario, avisos = self._mensaje_usuario(texto, adjuntos)
		for aviso in avisos:
			self._emitir(TipoEvento.AVISO, aviso)
		if not cargado:
			self._emitir(TipoEvento.AVISO, "Ollama está cargando el modelo %s en memoria; la primera respuesta puede tardar un poco." % modelo)
		mensajes = [*historial, mensaje_usuario]
		id_mensaje = "ollama-%d" % next(self._contador)
		try:
			resultado = self._pedir(direccion, modelo, mensajes, id_mensaje, parar)
		except ErrorOllama as exc:
			if exc.parcial:
				self._emitir(TipoEvento.MENSAJE, exc.parcial, id=id_mensaje)
			if exc.enviado:
				self._anadir_al_historial(mensaje_usuario, exc.parcial, modelo)
			return self._fallar(texto, exc, devolver=not exc.enviado)
		contenido, razonamiento, final, interrumpido = resultado
		if razonamiento.strip():
			self._emitir(TipoEvento.RAZONAMIENTO, razonamiento.strip())
		if contenido:
			self._emitir(TipoEvento.MENSAJE, contenido, id=id_mensaje)
		self._anadir_al_historial(mensaje_usuario, contenido, modelo)
		if final:
			entrada = final.get("prompt_eval_count")
			salida = final.get("eval_count")
			usado = (entrada or 0) + (salida or 0) if (entrada is not None or salida is not None) else None
			self._emitir(TipoEvento.USO, "", entrada=entrada, salida=salida, contexto_usado=usado, contexto_total=None, coste=None)
		if interrumpido:
			self._emitir(TipoEvento.FIN_TURNO, "Turno detenido.", correcto=False, interrumpido=True, resumen="Turno detenido.")
		else:
			resumen = "Respuesta de %s terminada." % modelo
			self._emitir(TipoEvento.FIN_TURNO, resumen, correcto=True, interrumpido=False, resumen=resumen)
		return True

	def _iniciar(self, modelo: str, retomada: bool) -> None:
		with self._candado:
			if not self.ajustes.id_externo:
				self.ajustes.id_externo = uuid.uuid4().hex
			self._iniciada = True
			identificador = self.ajustes.id_externo
		texto = "Conversación %s con el modelo %s de Ollama (solo chat: no puede leer ni cambiar archivos)." % (
			"retomada" if retomada else "iniciada", modelo)
		self._emitir(TipoEvento.INICIADO, texto, id_externo=identificador, modelo=modelo, modo=MODO_CHAT, retomada=retomada)

	def _fallar(self, texto: str, exc: ErrorOllama, devolver: bool = True) -> bool:
		log.info("Turno de Ollama fallido: %s", exc.mensaje)
		if devolver:
			with self._candado:
				self._devueltos.append(texto)
		self._emitir(TipoEvento.ERROR, exc.mensaje)
		self._emitir(TipoEvento.FIN_TURNO, "Ollama no ha podido responder.", correcto=False, interrumpido=False,
			resumen="Ollama no ha podido responder.")
		return False

	def _anadir_al_historial(self, mensaje_usuario: dict[str, Any], respuesta: str, modelo: str) -> None:
		with self._candado:
			if self._historial is None:
				self._historial = []
			self._historial.append(mensaje_usuario)
			if respuesta:
				self._historial.append({"role": "assistant", "content": respuesta})
			copia = list(self._historial)
			identificador = self.ajustes.id_externo
		guardar_historial(identificador, copia, modelo)

	def _mensaje_usuario(self, texto: str, adjuntos: Sequence[Adjunto]) -> tuple[dict[str, Any], list[str]]:
		"""Mensaje ``user`` con el texto de los archivos adjuntos y las imágenes en base64."""
		partes = [texto]
		imagenes: list[str] = []
		avisos: list[str] = []
		for adjunto in adjuntos:
			ruta = Path(adjunto.ruta)
			tipo = adjunto.tipo_mime or mimetypes.guess_type(ruta.name)[0] or ""
			try:
				if tipo.startswith("image/"):
					if ruta.stat().st_size > MAX_IMAGEN:
						avisos.append("La imagen %s es demasiado grande y no se ha enviado." % ruta.name)
						continue
					imagenes.append(base64.b64encode(ruta.read_bytes()).decode("ascii"))
					partes.append("[Imagen adjunta: %s]" % ruta.name)
					continue
				crudo = ruta.read_bytes()
			except OSError as exc:
				avisos.append("No se ha podido leer %s: %s" % (ruta.name, exc.strerror or exc))
				continue
			if b"\x00" in crudo[:8192]:
				avisos.append("%s no es un archivo de texto: Ollama solo puede recibir texto e imágenes." % ruta.name)
				continue
			contenido = crudo.decode("utf-8", errors="replace")
			if len(contenido) > MAX_TEXTO_ADJUNTO:
				contenido = contenido[:MAX_TEXTO_ADJUNTO]
				avisos.append("%s es muy largo: solo se envía el principio." % ruta.name)
			partes.append("Contenido del archivo %s:\n```\n%s\n```" % (ruta.name, contenido))
		mensaje: dict[str, Any] = {"role": "user", "content": "\n\n".join(parte for parte in partes if parte)}
		if imagenes:
			mensaje["images"] = imagenes
		return mensaje, avisos

	def _cuerpo(self, modelo: str, mensajes: list[dict[str, Any]], con_razonamiento: bool) -> dict[str, Any]:
		cuerpo: dict[str, Any] = {"model": modelo, "messages": mensajes, "stream": True}
		esfuerzo = self.ajustes.esfuerzo
		if con_razonamiento and esfuerzo:
			cuerpo["think"] = {"no": False, "si": True}.get(esfuerzo, esfuerzo)
		return cuerpo

	def _pedir(
		self, direccion: str, modelo: str, mensajes: list[dict[str, Any]], id_mensaje: str, parar: threading.Event,
	) -> tuple[str, str, dict[str, Any], bool]:
		"""POST ``/api/chat`` y lectura del streaming: ``(texto, razonamiento, línea final, interrumpido)``."""
		import requests

		sesion = _sesion_http(direccion, TIEMPO_LECTURA)
		abandonada = False
		try:
			con_razonamiento = True
			while True:
				try:
					respuesta = self._post(sesion, direccion + "/api/chat", self._cuerpo(modelo, mensajes, con_razonamiento), parar)
				except requests.ConnectionError as exc:
					if parar.is_set():
						return "", "", {}, True
					# Sin conexión, Ollama no ha recibido nada (no se reintenta en ningún caso).
					raise ErrorOllama(_mensaje_conexion(direccion)) from exc
				except requests.RequestException as exc:
					from app.core.red import clasificar_error

					raise ErrorOllama(clasificar_error(exc).mensaje, enviado=True) from exc
				if respuesta is None:
					# Se detuvo mientras Ollama cargaba el modelo: el hilo de la petición
					# cerrará la conexión (y la sesión) cuando Ollama conteste.
					abandonada = True
					return "", "", {}, True
				if respuesta.status_code == 400 and con_razonamiento and self.ajustes.esfuerzo and "think" in (respuesta.text or "").lower():
					# Rechazada sin hacer nada: es seguro volver a pedirla sin el ajuste.
					respuesta.close()
					con_razonamiento = False
					self._emitir(TipoEvento.AVISO, "El modelo %s no admite el ajuste de razonamiento; se envía sin él." % modelo)
					continue
				if respuesta.status_code != 200:
					mensaje = _error_de_respuesta(respuesta, modelo)
					respuesta.close()
					raise ErrorOllama(mensaje)
				break
			with self._candado:
				self._respuesta = respuesta
			if parar.is_set():
				_cortar(respuesta)
			return self._leer(respuesta, id_mensaje, parar)
		finally:
			with self._candado:
				self._respuesta = None
			if not abandonada:
				sesion.close()

	@staticmethod
	def _post(sesion: Any, url: str, cuerpo: dict[str, Any], parar: threading.Event) -> Any:
		"""Hace el POST en otro hilo para poder dejar de esperar si se detiene el turno.

		Ollama no manda las cabeceras hasta que el modelo está cargado (puede
		tardar minutos); durante ese tiempo no hay respuesta que cortar. Si se
		detiene antes, se devuelve ``None`` y el hilo de la petición cierra la
		conexión en cuanto llega la respuesta (Ollama deja de generar).
		"""
		resultado: dict[str, Any] = {}
		listo = threading.Event()

		def pedir() -> None:
			try:
				resultado["respuesta"] = sesion.post(url, json=cuerpo, stream=True, timeout=(TIEMPO_CONEXION, TIEMPO_LECTURA))
			except BaseException as exc:  # noqa: BLE001 - se relanza en el hilo del turno
				resultado["error"] = exc
			finally:
				listo.set()
			if parar.is_set():
				respuesta = resultado.get("respuesta")
				if respuesta is not None:
					_cortar(respuesta)
				sesion.close()

		threading.Thread(target=pedir, name="ollama-peticion", daemon=True).start()
		while not listo.wait(0.2):
			if parar.is_set():
				return None
		if "error" in resultado:
			raise resultado["error"]
		return resultado["respuesta"]

	def _leer(self, respuesta: Any, id_mensaje: str, parar: threading.Event) -> tuple[str, str, dict[str, Any], bool]:
		texto: list[str] = []
		razonamiento: list[str] = []
		final: dict[str, Any] = {}
		try:
			for linea in respuesta.iter_lines(decode_unicode=False):
				if parar.is_set():
					break
				if not linea:
					continue
				try:
					datos = json.loads(linea)
				except ValueError:
					log.debug("Línea de Ollama que no es JSON: %r", linea[:200])
					continue
				if not isinstance(datos, dict):
					continue
				if datos.get("error"):
					raise ErrorOllama("Ollama ha informado de un error: %s" % datos["error"], enviado=True, parcial="".join(texto))
				mensaje = datos.get("message") if isinstance(datos.get("message"), dict) else {}
				if mensaje.get("thinking"):
					razonamiento.append(str(mensaje["thinking"]))
				trozo = str(mensaje.get("content") or "")
				if trozo:
					texto.append(trozo)
					self._emitir(TipoEvento.TEXTO_PARCIAL, trozo, id=id_mensaje)
				if datos.get("done"):
					final = datos
					break
		except ErrorOllama:
			raise
		except Exception as exc:  # noqa: BLE001 - la conexión puede romperse de muchas formas
			if not parar.is_set():
				log.info("Se cortó la respuesta de Ollama: %s", exc)
				raise ErrorOllama("Se ha cortado la conexión con Ollama antes de terminar la respuesta.", enviado=True,
					parcial="".join(texto)) from exc
		finally:
			respuesta.close()
		interrumpido = parar.is_set() and not final
		if not final and not interrumpido:
			raise ErrorOllama("Ollama ha terminado la respuesta sin avisar de que había acabado.", enviado=True,
				parcial="".join(texto))
		return "".join(texto), "".join(razonamiento), final, interrumpido


# --------------------------------------------------------------------------
# Proveedor
# --------------------------------------------------------------------------


class ProveedorOllama(Proveedor):
	"""Ollama como agente de «solo chat».

	:param servidor: dirección del servidor, o función que la devuelve (se
		consulta cada vez, así un cambio en Preferencias vale sin reiniciar).
		Vacía: ``OLLAMA_HOST`` o ``http://127.0.0.1:11434``.
	"""

	id = "ollama"
	nombre = "Ollama (modelos locales, solo chat)"
	descripcion = (
		"Conversa con los modelos de IA que tienes descargados en Ollama, dentro de tu equipo y sin coste. "
		"Es solo chat: el modelo no puede leer ni cambiar archivos ni ejecutar órdenes."
	)
	web = "https://ollama.com"

	def __init__(self, servidor: str | Callable[[], str] | None = None) -> None:
		self._servidor = servidor
		self.capacidades = Capacidades(
			adjuntar_imagenes=True,
			adjuntar_archivos=True,
			bifurcar=True,
			permisos_interactivos=False,
			cambiar_modelo_en_marcha=True,
			comandos_barra=False,
			enviar_ahora=True,
			leer_historial_externo=False,
		)
		self._candado = threading.Lock()
		self._modelos: list[dict[str, Any]] = []

	# -- Servidor ---------------------------------------------------------------------

	def direccion(self) -> str:
		"""URL base del servidor de Ollama que se usa ahora."""
		try:
			valor = self._servidor() if callable(self._servidor) else self._servidor
		except Exception:  # noqa: BLE001 - una configuración rota no debe impedir usar la dirección por omisión
			log.debug("No se pudo leer la dirección de Ollama", exc_info=True)
			valor = ""
		return normalizar_direccion(str(valor or "") or os.environ.get("OLLAMA_HOST", ""))

	def _consultar(self, ruta: str) -> Any:
		"""GET rápido a la API (lanza ``ErrorOllama`` con un mensaje claro)."""
		import requests

		direccion = self.direccion()
		sesion = _sesion_http(direccion, TIEMPO_CONSULTA[1])
		try:
			respuesta = sesion.get(direccion + ruta, timeout=TIEMPO_CONSULTA)
			if respuesta.status_code != 200:
				raise ErrorOllama(_error_de_respuesta(respuesta))
			return respuesta.json()
		except requests.ConnectionError as exc:
			error = ErrorOllama(_mensaje_conexion(direccion))
			error.sin_conexion = True
			raise error from exc
		except requests.RequestException as exc:
			from app.core.red import clasificar_error

			raise ErrorOllama(clasificar_error(exc).mensaje) from exc
		except ValueError as exc:
			raise ErrorOllama("El servidor de %s no responde como Ollama." % direccion) from exc
		finally:
			sesion.close()

	def modelos_instalados(self) -> list[dict[str, Any]]:
		"""Consulta ``/api/tags`` (bloquea) y recuerda el resultado."""
		datos = self._consultar("/api/tags")
		modelos = [m for m in (datos.get("models") or []) if isinstance(m, dict)] if isinstance(datos, dict) else []
		modelos = _ordenar_por_fecha(modelos)
		with self._candado:
			self._modelos = modelos
		return modelos

	def modelo_por_omision(self) -> str:
		"""El modelo descargado o actualizado más recientemente."""
		modelos = self.modelos_instalados()
		for modelo in modelos:
			nombre = str(modelo.get("model") or modelo.get("name") or "")
			if nombre:
				return nombre
		return ""

	def cargado(self, modelo: str) -> bool:
		"""``True`` si el modelo ya está en memoria (o si no se puede saber).

		Si no hay conexión con Ollama lanza ``ErrorOllama``: así el turno falla
		enseguida sin esperar otra vez a la conexión del chat.
		"""
		try:
			datos = self._consultar("/api/ps")
		except ErrorOllama as exc:
			if exc.sin_conexion:
				raise
			return True
		nombres = set()
		for entrada in (datos.get("models") or []) if isinstance(datos, dict) else []:
			if isinstance(entrada, dict):
				nombres.update(str(entrada.get(clave) or "") for clave in ("model", "name"))
		if ":" not in modelo:
			return modelo in nombres or modelo + ":latest" in nombres
		return modelo in nombres

	# -- Contrato ----------------------------------------------------------------------

	def detectar(self) -> EstadoProveedor:
		direccion = self.direccion()
		try:
			version = self._consultar("/api/version")
			modelos = self.modelos_instalados()
		except ErrorOllama as exc:
			programa = buscar_ollama()
			if programa is not None:
				return EstadoProveedor(
					True, ruta=str(programa),
					mensaje="Ollama está instalado pero no responde en %s. Ábrelo desde el menú Inicio (queda junto al reloj) "
						"o escribe «ollama serve» en una consola." % direccion,
				)
			return EstadoProveedor(False, mensaje="%s %s" % (exc.mensaje, INSTRUCCIONES))
		numero = str(version.get("version") or "") if isinstance(version, dict) else ""
		nombres = [str(m.get("model") or m.get("name") or "") for m in modelos]
		nombres = [n for n in nombres if n]
		if nombres:
			mensaje = "Preparado en %s. Modelos instalados: %s. Es solo chat: no usa herramientas." % (direccion, ", ".join(nombres))
		else:
			mensaje = (
				"Ollama está en marcha en %s pero no tiene ningún modelo. Descarga uno escribiendo en una consola, "
				"por ejemplo: ollama pull llama3.2" % direccion
			)
		return EstadoProveedor(True, ruta=direccion, version=numero, mensaje=mensaje)

	def crear_conversacion(self, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> ConversacionOllama:
		if not ajustes.modo_permiso:
			ajustes.modo_permiso = MODO_CHAT
		return ConversacionOllama(ajustes, al_evento, self)

	def listar_modelos(self) -> list[OpcionLista]:
		"""Lista en vivo de los modelos instalados (llamar desde un hilo)."""
		try:
			self.modelos_instalados()
		except ErrorOllama:
			log.debug("No se pudo obtener la lista de modelos de Ollama", exc_info=True)
		return self.modelos()

	def modelos(self) -> list[OpcionLista]:
		with self._candado:
			conocidos = list(self._modelos)
		opciones = [OpcionLista("", "Predeterminado: el último modelo descargado")]
		for modelo in conocidos:
			opcion = opcion_de_modelo(modelo)
			if opcion is not None:
				opciones.append(opcion)
		return opciones

	def esfuerzos(self) -> list[OpcionLista]:
		return list(_ESFUERZOS)

	def modos_permiso(self) -> list[OpcionLista]:
		return list(_MODOS)

	def instrucciones_instalacion(self) -> str:
		return INSTRUCCIONES

	def orden_instalacion(self) -> list[str] | None:
		return ["cmd.exe", "/k", "winget install --id Ollama.Ollama -e"]
