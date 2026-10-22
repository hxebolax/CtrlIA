"""Cliente genérico del Agent Client Protocol (ACP v1).

ACP (https://agentclientprotocol.com) es un protocolo abierto para hablar con
agentes de programación: JSON-RPC 2.0 por la entrada y la salida estándar del
agente, un mensaje JSON por línea. Ctrl+IA hace de «cliente» (el papel de un
editor): abre una sesión en la carpeta del usuario, envía mensajes, muestra lo
que el agente va haciendo y contesta a sus peticiones (permisos y lectura o
escritura de archivos).

Se implementa a mano con hilos porque el SDK oficial de Python no admite la
versión de Python de la aplicación. Piezas:

- :class:`ConversacionAcp`: una sesión viva con un agente ACP.
- :class:`ProveedorAcp`: un agente ACP configurable (orden para lanzarlo,
  textos, variables de entorno que no debe heredar).
- Fábricas de agentes conocidos: :func:`proveedor_gemini_cli`,
  :func:`proveedor_claude_acp`, :func:`proveedor_codex_acp`,
  :func:`proveedor_antigravity_acp` y :func:`proveedores_personalizados`.

Hilos: el lector de :class:`ProcesoLineas` atiende respuestas, notificaciones
y peticiones del agente; un hilo «de turnos» arranca el agente y envía los
mensajes de la cola de uno en uno (``session/prompt`` queda abierta todo el
turno). Nunca se emite un evento con el candado interno cogido.
"""

from __future__ import annotations

import base64
import collections
import difflib
import itertools
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from app.core.version import APP_VERSION
from app.services.agentes import remoto
from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	Capacidades,
	ClaseOpcion,
	Conversacion,
	Estado,
	EstadoProveedor,
	Evento,
	OpcionLista,
	OpcionPermiso,
	Proveedor,
	ReceptorEventos,
	RespuestaPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.agentes.procesos import (
	ProcesoLineas,
	buscar_ejecutable,
	buscar_programa_node,
	entorno_hijo,
	json_o_nada,
	version_de,
)

log = logging.getLogger(__name__)

#: Versión del protocolo que habla este cliente.
VERSION_PROTOCOLO = 1

#: Espera máxima para el arranque (``initialize``, ``session/new``...).
TIEMPO_ARRANQUE = 120.0
#: Espera máxima para retomar una sesión (el agente reenvía todo el historial).
TIEMPO_CARGA = 300.0
#: Espera máxima para iniciar sesión (puede abrir el navegador).
TIEMPO_AUTENTICACION = 300.0
#: Espera máxima para cambios de modo o de modelo.
TIEMPO_AJUSTE = 30.0
#: Tras pedir que se detenga, cuánto se espera la confirmación del agente.
TIEMPO_TRAS_CANCELAR = 15.0
#: Longitud máxima de la salida de una herramienta que se muestra.
MAX_SALIDA = 4000
#: Tamaño máximo de una imagen adjunta (se envía en base64).
MAX_IMAGEN = 20 * 1024 * 1024

# Códigos de error de JSON-RPC (y de ACP).
ERROR_METODO = -32601
ERROR_PARAMETROS = -32602
ERROR_INTERNO = -32603
ERROR_AUTENTICACION = -32000
ERROR_NO_ENCONTRADO = -32002

_EXTENSIONES_SCRIPT = {".js", ".mjs", ".cjs"}
_LANZADORES = {".cmd", ".bat", ".ps1"}

#: Nombre en español de cada clase de herramienta de ACP (``kind``).
NOMBRES_TIPO_HERRAMIENTA: dict[str, str] = {
	"read": "Leer",
	"edit": "Editar",
	"delete": "Borrar",
	"move": "Mover",
	"search": "Buscar",
	"execute": "Ejecutar orden",
	"think": "Pensar",
	"fetch": "Consultar internet",
	"switch_mode": "Cambiar de modo",
	"other": "Herramienta",
}

#: Etiqueta y clase de cada tipo de opción de permiso de ACP.
_OPCIONES_PERMISO: dict[str, tuple[str, ClaseOpcion]] = {
	"allow_once": ("Permitir una vez", ClaseOpcion.PERMITIR),
	"allow_always": ("Permitir siempre", ClaseOpcion.PERMITIR_SIEMPRE),
	"reject_once": ("Denegar", ClaseOpcion.DENEGAR),
	"reject_always": ("Denegar siempre", ClaseOpcion.DENEGAR_SIEMPRE),
}

#: Nombres de opción que no aportan nada a la etiqueta en español.
_NOMBRES_GENERICOS = {
	"allow", "allow once", "always allow", "allow always", "yes", "ok", "accept", "approve",
	"reject", "reject once", "deny", "deny once", "no", "decline", "always reject", "reject always",
	"always deny", "deny always", "permitir", "permitir una vez", "permitir siempre", "denegar",
	"denegar siempre", "rechazar",
}

_AVISOS_FIN: dict[str, str] = {
	"max_tokens": "El agente ha llegado al límite de longitud de la respuesta y la ha cortado.",
	"max_turn_requests": "El agente ha llegado al máximo de pasos permitidos en un turno y se ha parado.",
	"refusal": "El agente se ha negado a continuar con esta petición.",
}

_RESUMEN_FIN: dict[str, str] = {
	"end_turn": "El agente ha terminado.",
	"max_tokens": "El agente ha terminado: respuesta cortada por longitud.",
	"max_turn_requests": "El agente se ha parado: demasiados pasos en un turno.",
	"refusal": "El agente se ha negado a continuar.",
	"cancelled": "Turno detenido.",
}


class ErrorAcp(Exception):
	"""Fallo al hablar con un agente ACP (el mensaje está en español)."""

	def __init__(self, mensaje: str, codigo: int | None = None, muerto: bool = False) -> None:
		super().__init__(mensaje)
		self.mensaje = mensaje
		self.codigo = codigo
		#: ``True`` si el proceso del agente ha terminado.
		self.muerto = muerto


class _Peticion:
	"""Petición JSON-RPC enviada al agente que espera respuesta."""

	def __init__(self, metodo: str, al_responder: Callable[[_Peticion], None] | None = None) -> None:
		self.metodo = metodo
		self.evento = threading.Event()
		self.resultado: Any = None
		self.error: ErrorAcp | None = None
		self.al_responder = al_responder
		#: Momento (``time.monotonic``) en que se pidió detenerla; 0 si no.
		self.cancelada_en = 0.0
		self._candado = threading.Lock()

	def resolver(self, resultado: Any = None, error: ErrorAcp | None = None) -> None:
		with self._candado:
			if self.evento.is_set():
				return
			self.resultado = resultado
			self.error = error
			self.evento.set()
		if self.al_responder is not None:
			try:
				self.al_responder(self)
			except Exception:  # noqa: BLE001 - un aviso roto no puede tumbar el lector
				log.exception("Fallo al atender la respuesta de %s", self.metodo)

	def esperar(self, tiempo: float | None) -> Any:
		if not self.evento.wait(tiempo):
			raise ErrorAcp("El agente no ha respondido a tiempo (%s)." % self.metodo)
		if self.error is not None:
			raise self.error
		return self.resultado


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------


def diff_unificado(ruta: str, antes: str | None, despues: str) -> str:
	"""Diff unificado (texto) entre dos versiones de un archivo."""

	def lineas(texto: str | None) -> list[str]:
		resultado = (texto or "").splitlines(keepends=True)
		if resultado and not resultado[-1].endswith(("\n", "\r")):
			resultado[-1] += "\n"
		return resultado

	nombre = ruta.replace("\\", "/")
	origen = "/dev/null" if antes is None else "a/" + nombre
	return "".join(difflib.unified_diff(lineas(antes), lineas(despues), fromfile=origen, tofile="b/" + nombre))


def _recortar(texto: str, maximo: int = MAX_SALIDA) -> str:
	if len(texto) <= maximo:
		return texto
	return texto[:maximo].rstrip() + "\n[…salida recortada: %d caracteres más]" % (len(texto) - maximo)


def _texto_de_contenido(contenido: Any) -> str:
	"""Texto de un bloque de contenido de ACP (o de una lista de ellos)."""
	if isinstance(contenido, list):
		return "".join(_texto_de_contenido(bloque) for bloque in contenido)
	if not isinstance(contenido, dict):
		return ""
	tipo = contenido.get("type")
	if tipo == "text":
		return str(contenido.get("text") or "")
	if tipo == "resource_link":
		return "[%s]" % (contenido.get("name") or contenido.get("uri") or "recurso")
	if tipo == "resource":
		recurso = contenido.get("resource") or {}
		return str(recurso.get("text") or "") if isinstance(recurso, dict) else ""
	return ""


def _mensaje_error_rpc(error: Any) -> tuple[str, int | None]:
	"""Texto legible y código de un objeto de error JSON-RPC."""
	if not isinstance(error, dict):
		return str(error), None
	mensaje = str(error.get("message") or "error desconocido")
	datos = error.get("data")
	if isinstance(datos, str) and datos and datos not in mensaje:
		mensaje = "%s (%s)" % (mensaje, datos)
	elif isinstance(datos, dict) and isinstance(datos.get("details"), str):
		mensaje = "%s (%s)" % (mensaje, datos["details"])
	codigo = error.get("code")
	return mensaje, codigo if isinstance(codigo, int) else None


def _normalizar_nombre(nombre: str) -> str:
	return re.sub(r"[^\w ]+", "", nombre.casefold()).strip()


def describir_herramienta(info: dict[str, Any]) -> tuple[str, str]:
	"""Nombre en español y detalle legible de una llamada a herramienta de ACP."""
	tipo = str(info.get("kind") or "other")
	nombre = NOMBRES_TIPO_HERRAMIENTA.get(tipo, "Herramienta")
	partes: list[str] = []
	titulo = str(info.get("title") or "").strip()
	if titulo:
		partes.append(titulo)
	rutas: list[str] = []
	for lugar in info.get("locations") or []:
		if not isinstance(lugar, dict) or not lugar.get("path"):
			continue
		ruta = str(lugar["path"])
		if isinstance(lugar.get("line"), int):
			ruta = "%s (línea %d)" % (ruta, lugar["line"])
		if ruta not in rutas:
			rutas.append(ruta)
	if rutas:
		visibles = ", ".join(rutas[:5])
		if len(rutas) > 5:
			visibles += " y %d más" % (len(rutas) - 5)
		partes.append(("Archivo: " if len(rutas) == 1 else "Archivos: ") + visibles)
	return nombre, ". ".join(partes) if partes else nombre


# --------------------------------------------------------------------------
# Búsqueda de node y de paquetes de npm
# --------------------------------------------------------------------------


def _carpetas_scoop() -> list[Path]:
	carpetas = []
	for variable in ("SCOOP", "SCOOP_GLOBAL"):
		valor = os.environ.get(variable)
		if valor:
			carpetas.append(Path(valor))
	perfil = os.environ.get("USERPROFILE")
	if perfil:
		carpetas.append(Path(perfil) / "scoop")
	return carpetas


def buscar_node() -> Path | None:
	"""``node.exe`` del PATH o de las instalaciones habituales (oficial, scoop)."""
	candidatos: list[Path] = []
	for raiz in _carpetas_scoop():
		for app in ("nodejs-lts", "nodejs"):
			candidatos.append(raiz / "apps" / app / "current" / "node.exe")
			candidatos.append(raiz / "apps" / app / "current" / "bin" / "node.exe")
	for variable in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
		valor = os.environ.get(variable)
		if valor:
			candidatos.append(Path(valor) / "nodejs" / "node.exe")
			candidatos.append(Path(valor) / "Programs" / "nodejs" / "node.exe")
	return buscar_ejecutable(["node"], candidatos)


def carpetas_npm_globales(nombre_bin: str = "") -> list[Path]:
	"""Carpetas ``node_modules`` donde ``npm install -g`` deja los paquetes."""
	carpetas: list[Path] = []
	if nombre_bin:
		encontrado = shutil.which(nombre_bin)
		if encontrado:
			carpetas.append(Path(encontrado).parent / "node_modules")
	prefijo = os.environ.get("NPM_CONFIG_PREFIX") or os.environ.get("npm_config_prefix")
	if prefijo:
		carpetas.append(Path(prefijo) / "node_modules")
		carpetas.append(Path(prefijo) / "lib" / "node_modules")
	appdata = os.environ.get("APPDATA")  # auditoria: ignorar ruta-no-portable - localiza programas instalados por npm; no guarda datos
	if appdata:
		carpetas.append(Path(appdata) / "npm" / "node_modules")
	for raiz in _carpetas_scoop():
		for app in ("nodejs-lts", "nodejs"):
			carpetas.append(raiz / "persist" / app / "bin" / "node_modules")
			carpetas.append(raiz / "apps" / app / "current" / "bin" / "node_modules")
	node = buscar_node()
	if node is not None:
		carpetas.append(node.parent / "node_modules")
	unicas: list[Path] = []
	for carpeta in carpetas:
		if carpeta not in unicas:
			unicas.append(carpeta)
	return unicas


def _leer_paquete(carpeta: Path) -> dict[str, Any] | None:
	try:
		datos = json.loads((carpeta / "package.json").read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return None
	return datos if isinstance(datos, dict) else None


def buscar_paquete_npm(paquete: str, nombre_bin: str = "") -> tuple[list[str], str] | None:
	"""Busca un paquete de npm instalado globalmente.

	Devuelve ``([node.exe, script.js], versión)`` leyendo el campo ``bin`` de
	su ``package.json`` (así nunca se pasa por el lanzador ``.cmd``), o
	``None`` si no está instalado o no hay node.
	"""
	node = buscar_node()
	if node is None:
		return None
	for carpeta in carpetas_npm_globales(nombre_bin):
		raiz = carpeta.joinpath(*paquete.split("/"))
		datos = _leer_paquete(raiz)
		if datos is None:
			continue
		binarios = datos.get("bin")
		script = ""
		if isinstance(binarios, str):
			script = binarios
		elif isinstance(binarios, dict) and binarios:
			script = str(binarios.get(nombre_bin) or next(iter(binarios.values())))
		if not script:
			continue
		ruta = (raiz / script).resolve()
		if ruta.is_file():
			return [str(node), str(ruta)], str(datos.get("version") or "")
	return None


def version_por_paquete(orden: Sequence[str]) -> str:
	"""Versión de un agente lanzado como ``node script.js`` (de su ``package.json``)."""
	if len(orden) < 2 or Path(orden[0]).stem.lower() != "node":
		return ""
	carpeta = Path(orden[1]).parent
	for _ in range(6):
		datos = _leer_paquete(carpeta)
		if datos is not None and datos.get("version"):
			return str(datos["version"])
		if carpeta.parent == carpeta:
			break
		carpeta = carpeta.parent
	return ""


def preparar_orden(orden: Sequence[str]) -> list[str] | None:
	"""Convierte una orden escrita por el usuario en una lanzable sin shell.

	- Un script ``.js`` se lanza con node.
	- Un nombre suelto se busca en el PATH (solo binarios nativos).
	- Los lanzadores ``.cmd``, ``.bat`` y ``.ps1`` se rechazan (``cmd.exe``
	  reinterpretaría los argumentos).
	"""
	orden = [str(parte) for parte in orden if str(parte)]
	if not orden:
		return None
	programa = orden[0]
	ruta = Path(programa)
	sufijo = ruta.suffix.lower()
	if sufijo in _LANZADORES:
		log.warning("Se rechaza el lanzador %s: indica el programa real", ruta.name)
		return None
	if sufijo in _EXTENSIONES_SCRIPT:
		node = buscar_node()
		if node is None or not ruta.is_file():
			return None
		return [str(node), str(ruta), *orden[1:]]
	if ruta.is_absolute() or "/" in programa or "\\" in programa:
		return orden if ruta.is_file() else None
	encontrado = buscar_ejecutable([programa])
	return [str(encontrado), *orden[1:]] if encontrado else None


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


class ConversacionAcp(Conversacion):
	"""Sesión viva con un agente ACP.

	El agente se lanza con el primer :meth:`enviar`. Si el proceso muere, el
	siguiente :meth:`enviar` lo vuelve a lanzar y retoma la sesión (si el
	agente sabe hacerlo).
	"""

	def __init__(self, ajustes: AjustesSesion, al_evento: ReceptorEventos, proveedor: ProveedorAcp) -> None:
		super().__init__(ajustes, al_evento)
		self.proveedor = proveedor
		self._candado = threading.RLock()
		self._proceso: ProcesoLineas | None = None
		self._generacion = 0
		self._ids = itertools.count(1)
		self._peticiones: dict[int, _Peticion] = {}
		self._sesion = ""
		self._capacidades_agente: dict[str, Any] = {}
		self._metodos_auth: list[OpcionLista] = []
		self._modos: list[OpcionLista] = []
		self._modelos: list[OpcionLista] = []
		self._modo_actual = ""
		self._modelo_actual = ""
		self._comandos: list[tuple[str, str]] = []
		self._cola: collections.deque[tuple[str, tuple[Adjunto, ...]]] = collections.deque()
		self._devueltos: list[str] = []
		self._trabajador_activo = False
		self._cancelado = False
		self._arrancando = False
		self._cargando = False
		self._cerrada = False
		self._prompt: _Peticion | None = None
		self._permisos: dict[str, tuple[Any, SolicitudPermiso]] = {}
		self._contador_permisos = itertools.count(1)
		self._herramientas: dict[str, dict[str, Any]] = {}
		self._herramientas_terminadas: set[str] = set()
		self._texto: list[str] = []
		self._id_mensaje = ""
		self._contador_mensajes = itertools.count(1)
		self._historial: list[tuple[str, str]] = []

	# -- API pública -----------------------------------------------------------

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		"""Envía un mensaje; si hay un turno en marcha, lo deja en cola."""
		with self._candado:
			if self._cerrada:
				log.debug("Se ignora un envío a una conversación cerrada")
				return
			self._cola.append((texto, tuple(adjuntos)))
			self._cancelado = False
			ocupada = self._trabajador_activo
			en_cola = len(self._cola)
			if not ocupada:
				self._trabajador_activo = True
		if ocupada:
			self._emitir(
				TipoEvento.AVISO,
				"Mensaje en cola: se enviará cuando el agente termine (%d pendiente%s)." % (en_cola, "" if en_cola == 1 else "s"),
				en_cola=en_cola,
			)
			return
		threading.Thread(target=self._trabajar, name="%s-turnos" % self.proveedor.id, daemon=True).start()

	def detener(self) -> None:
		"""Pide al agente que pare el turno y retira los permisos pendientes.

		Los mensajes en cola NO se envían: quedan disponibles en :meth:`pendientes`.
		"""
		with self._candado:
			self._devueltos.extend(texto for texto, _adjuntos in self._cola)
			self._cola.clear()
			if self._trabajador_activo:
				self._cancelado = True
			prompt = self._prompt
			sesion = self._sesion
			proceso = self._proceso
		if prompt is not None and not prompt.evento.is_set():
			if not prompt.cancelada_en:
				prompt.cancelada_en = time.monotonic()
			if proceso is not None and sesion:
				proceso.escribir_json({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sesion}})
		self._retirar_permisos()

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		"""Contesta a una petición de permiso con la opción elegida."""
		with self._candado:
			par = self._permisos.pop(solicitud_id, None)
			quedan = bool(self._permisos)
			trabajando = self._trabajador_activo
		if par is None:
			log.debug("Respuesta a una solicitud que ya no está pendiente: %s", solicitud_id)
			return
		ident, solicitud = par
		if any(opcion.id == respuesta.opcion for opcion in solicitud.opciones):
			resultado = {"outcome": {"outcome": "selected", "optionId": respuesta.opcion}}
		else:
			resultado = {"outcome": {"outcome": "cancelled"}}
		self._responder(ident, resultado)
		if not quedan and trabajando:
			self._cambiar_estado(Estado.TRABAJANDO)

	def cerrar(self) -> None:
		"""Retira lo pendiente y termina el proceso del agente. Idempotente."""
		with self._candado:
			if self._cerrada:
				return
			self._cerrada = True
			proceso = self._proceso
			self._proceso = None
			pendientes = list(self._peticiones.values())
			self._peticiones.clear()
			self._devueltos.extend(texto for texto, _adjuntos in self._cola)
			self._cola.clear()
		self._retirar_herramientas_ctrlia()
		permisos = self._sacar_permisos()
		if proceso is not None:
			for ident, _solicitud in permisos:
				proceso.escribir_json({"jsonrpc": "2.0", "id": ident, "result": {"outcome": {"outcome": "cancelled"}}})
		error = ErrorAcp("La conversación se ha cerrado.")
		for peticion in pendientes:
			peticion.resolver(error=error)
		if proceso is not None:
			proceso.cerrar_entrada()
			proceso.terminar(espera=2.0)
		self._cambiar_estado(Estado.CERRADA)

	def cambiar_ajustes(self, **cambios: Any) -> None:
		"""Cambia modelo o modo; si la sesión está abierta, se aplica al momento."""
		super().cambiar_ajustes(**cambios)
		with self._candado:
			sesion = self._sesion if self._proceso is not None else ""
			modos = {modo.id for modo in self._modos}
			modelos = {modelo.id for modelo in self._modelos}
		if not sesion:
			return
		modo = cambios.get("modo_permiso")
		if modo and modo in modos and modo != self._modo_actual:
			self._enviar_peticion(
				"session/set_mode",
				{"sessionId": sesion, "modeId": modo},
				al_responder=lambda peticion, modo=modo: self._tras_ajuste(peticion, "modo", modo),
			)
		modelo = cambios.get("modelo")
		if modelo and modelo in modelos and modelo != self._modelo_actual:
			self._enviar_peticion(
				"session/set_model",
				{"sessionId": sesion, "modelId": modelo},
				al_responder=lambda peticion, modelo=modelo: self._tras_ajuste(peticion, "modelo", modelo),
			)

	def pendientes(self) -> list[str]:
		"""Retira y devuelve los mensajes que no se llegaron a enviar."""
		with self._candado:
			textos = self._devueltos + [texto for texto, _adjuntos in self._cola]
			self._devueltos = []
			self._cola.clear()
		return textos

	def modos_disponibles(self) -> list[OpcionLista]:
		"""Modos de trabajo que anuncia el agente en esta sesión (vacío si ninguno)."""
		with self._candado:
			return list(self._modos)

	def modelos_disponibles(self) -> list[OpcionLista]:
		"""Modelos que anuncia el agente en esta sesión (vacío si ninguno)."""
		with self._candado:
			return list(self._modelos)

	def metodos_autenticacion(self) -> list[OpcionLista]:
		"""Formas de iniciar sesión que anuncia el agente."""
		with self._candado:
			return list(self._metodos_auth)

	def comandos(self) -> list[tuple[str, str]]:
		"""Comandos de barra que anuncia el agente: ``[(nombre, descripción)]``."""
		with self._candado:
			return list(self._comandos)

	def historial(self) -> list[tuple[str, str]]:
		"""Mensajes reenviados por el agente al retomar la sesión: ``[(rol, texto)]``.

		``rol`` es ``"usuario"`` o ``"asistente"``.
		"""
		with self._candado:
			return list(self._historial)

	@property
	def modo_actual(self) -> str:
		return self._modo_actual

	@property
	def modelo_actual(self) -> str:
		return self._modelo_actual

	# -- Eventos ----------------------------------------------------------------

	def _emitir(self, tipo: TipoEvento, texto: str = "", **datos: Any) -> None:
		if self._cerrada and tipo is not TipoEvento.ESTADO:
			return
		super()._emitir(tipo, texto, **datos)

	def _emitir_con_datos(self, tipo: TipoEvento, texto: str, datos: dict[str, Any]) -> None:
		"""Como ``_emitir``, para datos con claves que chocan con sus parámetros (``tipo``, ``texto``)."""
		if self._cerrada:
			return
		try:
			self._al_evento(Evento(tipo, texto, datos))
		except Exception:  # noqa: BLE001 - un receptor roto no debe matar el lector del agente
			log.exception("El receptor de eventos falló con %s", tipo)

	# -- Hilo de turnos -----------------------------------------------------------

	def _trabajar(self) -> None:
		fallo = False
		while True:
			with self._candado:
				if self._cerrada or not self._cola:
					self._trabajador_activo = False
					break
				texto, adjuntos = self._cola.popleft()
			self._cambiar_estado(Estado.TRABAJANDO)
			if not self._listo():
				try:
					self._arrancar()
				except ErrorAcp as exc:
					fallo = True
					self._fallo_arranque(texto, exc)
					continue
			if not self._turno(texto, adjuntos):
				fallo = True
		if not self._cerrada:
			if fallo and self._proceso is None:
				self._cambiar_estado(Estado.ERROR)
			else:
				self._cambiar_estado(Estado.INACTIVA)

	def _listo(self) -> bool:
		with self._candado:
			return self._proceso is not None and self._proceso.vivo and bool(self._sesion)

	def _fallo_arranque(self, texto: str, exc: ErrorAcp) -> None:
		log.info("No se pudo iniciar %s: %s", self.proveedor.id, exc.mensaje)
		with self._candado:
			self._devueltos.append(texto)
			self._devueltos.extend(otro for otro, _adjuntos in self._cola)
			self._cola.clear()
			proceso = self._proceso
			self._proceso = None
			self._sesion = ""
			self._generacion += 1  # su fin ya no es una «muerte inesperada»
		if proceso is not None:
			proceso.terminar(espera=2.0)
		if self._cerrada:
			return
		self._emitir(TipoEvento.ERROR, exc.mensaje, codigo=exc.codigo)
		self._emitir(TipoEvento.FIN_TURNO, "No se ha podido iniciar el agente.", correcto=False, interrumpido=False,
			resumen="No se ha podido iniciar el agente.")

	def _turno(self, texto: str, adjuntos: Sequence[Adjunto]) -> bool:
		"""Envía un mensaje y espera al final del turno. ``False`` si algo falla."""
		with self._candado:
			if self._cancelado:
				self._devueltos.append(texto)
				cancelado = True
			else:
				cancelado = False
				sesion = self._sesion
				self._herramientas.clear()
				self._herramientas_terminadas.clear()
		if cancelado:
			self._emitir(TipoEvento.FIN_TURNO, "Turno detenido.", correcto=False, interrumpido=True, resumen="Turno detenido.")
			return True
		contenido = self._contenido(texto, adjuntos)
		prompt = self._enviar_peticion("session/prompt", {"sessionId": sesion, "prompt": contenido}, registrar_prompt=True)
		abandonado = False
		while not prompt.evento.wait(0.25):
			if self._cerrada:
				break
			if prompt.cancelada_en and time.monotonic() - prompt.cancelada_en > TIEMPO_TRAS_CANCELAR:
				log.warning("%s no confirmó la cancelación; se da el turno por terminado", self.proveedor.id)
				abandonado = True
				break
		with self._candado:
			self._prompt = None
		self._volcar_mensaje()
		self._retirar_permisos()
		if self._cerrada:
			return True
		if abandonado:
			self._emitir(TipoEvento.FIN_TURNO, "Turno detenido.", correcto=False, interrumpido=True, resumen="Turno detenido.",
				motivo="cancelled")
			return True
		if prompt.error is not None:
			if not prompt.error.muerto:
				self._emitir(TipoEvento.ERROR, "El agente ha devuelto un error: %s" % prompt.error.mensaje, codigo=prompt.error.codigo)
			self._emitir(TipoEvento.FIN_TURNO, "El turno ha terminado con un error.", correcto=False,
				interrumpido=False, resumen="El turno ha terminado con un error.")
			return False
		resultado = prompt.resultado if isinstance(prompt.resultado, dict) else {}
		motivo = str(resultado.get("stopReason") or "end_turn")
		if prompt.cancelada_en and motivo != "cancelled":
			log.debug("El agente terminó con %s tras pedir que se detuviera", motivo)
		self._emitir_uso_turno(resultado.get("usage"))
		if motivo in _AVISOS_FIN:
			self._emitir(TipoEvento.AVISO, _AVISOS_FIN[motivo], motivo=motivo)
		resumen = _RESUMEN_FIN.get(motivo, "El agente ha terminado.")
		self._emitir(TipoEvento.FIN_TURNO, resumen, correcto=motivo == "end_turn", interrumpido=motivo == "cancelled",
			resumen=resumen, motivo=motivo)
		return True

	def _contenido(self, texto: str, adjuntos: Sequence[Adjunto]) -> list[dict[str, Any]]:
		"""Bloques de ``session/prompt`` para el texto y los adjuntos."""
		bloques: list[dict[str, Any]] = []
		if texto:
			bloques.append({"type": "text", "text": texto})
		capacidades = self._capacidades_agente.get("promptCapabilities") or {}
		admite_imagen = bool(capacidades.get("image")) if isinstance(capacidades, dict) else False
		remota = remoto.es_remota(self.ajustes.extra)
		for adjunto in adjuntos:
			ruta = Path(adjunto.ruta)
			try:
				ruta = ruta.resolve()
			except OSError:
				log.debug("No se pudo resolver %s", ruta, exc_info=True)
			tipo_mime = adjunto.tipo_mime or mimetypes.guess_type(ruta.name)[0] or ""
			if remota and not (admite_imagen and tipo_mime.startswith("image/")):
				bloques.append({"type": "text", "text": "(Archivo adjunto que no se ha enviado porque está en Windows: %s)" % ruta.name})
				continue
			if admite_imagen and tipo_mime.startswith("image/"):
				try:
					if ruta.stat().st_size <= MAX_IMAGEN:
						datos = base64.b64encode(ruta.read_bytes()).decode("ascii")
						bloques.append({"type": "image", "mimeType": tipo_mime, "data": datos, "uri": ruta.as_uri()})
						continue
					self._emitir(TipoEvento.AVISO, "La imagen %s es demasiado grande; se envía solo su ubicación." % ruta.name)
				except OSError:
					log.debug("No se pudo leer la imagen %s", ruta, exc_info=True)
					self._emitir(TipoEvento.AVISO, "No se ha podido leer la imagen %s; se envía solo su ubicación." % ruta.name)
			enlace: dict[str, Any] = {"type": "resource_link", "uri": ruta.as_uri(), "name": ruta.name}
			if tipo_mime:
				enlace["mimeType"] = tipo_mime
			bloques.append(enlace)
		if not bloques:
			bloques.append({"type": "text", "text": ""})
		return bloques

	# -- Arranque ------------------------------------------------------------------

	def _arrancar(self) -> None:
		"""Lanza el agente, negocia el protocolo y abre o retoma la sesión."""
		with self._candado:
			self._arrancando = True
		try:
			try:
				equipo = remoto.equipo_de(self.ajustes.extra)
			except remoto.ErrorRemoto as exc:
				raise ErrorAcp(str(exc)) from exc
			self._remota = equipo is not None
			if equipo is not None:
				orden = self.proveedor.orden_para_remoto()
				if not orden:
					raise ErrorAcp("%s no se puede usar en otro equipo por SSH." % self.proveedor.nombre)
			else:
				orden = self.proveedor.resolver_orden()
			if not orden:
				raise ErrorAcp(("No se encuentra %s en este equipo. %s" % (self.proveedor.nombre, self.proveedor.instrucciones)).strip())

			def preparar(base: list[str]) -> list[str]:
				if equipo is None:
					return base
				try:
					return remoto.orden_remota(equipo, self.proveedor.id, base[1:], remoto.carpeta_remota(self.ajustes.extra),
						quitar=self.proveedor.entorno_quitar, programa=base[0])
				except remoto.ErrorRemoto as exc:
					raise ErrorAcp(str(exc)) from exc

			try:
				inicio = self._lanzar_e_iniciar(preparar(orden))
			except ErrorAcp as exc:
				alternativa = self.proveedor.orden_alternativa(orden)
				if not exc.muerto or alternativa is None:
					raise
				log.info("%s no arrancó con %s; se prueba la orden alternativa", self.proveedor.id, orden[1:])
				inicio = self._lanzar_e_iniciar(preparar(alternativa))
				self.proveedor.recordar_alternativa()
			self._tras_iniciar(inicio)
			retomada = self._abrir_sesion()
			self._aplicar_ajustes_iniciales()
		finally:
			with self._candado:
				self._arrancando = False
				self._cargando = False
		self._emitir(
			TipoEvento.INICIADO,
			"Conversación retomada con %s." % self.proveedor.nombre if retomada else "%s preparado." % self.proveedor.nombre,
			id_externo=self._sesion,
			modelo=self._modelo_actual or self.ajustes.modelo,
			modo=self._modo_actual or self.ajustes.modo_permiso,
			retomada=retomada,
		)

	def _lanzar_e_iniciar(self, orden: list[str]) -> dict[str, Any]:
		with self._candado:
			self._generacion += 1
			generacion = self._generacion
			anterior = self._proceso
			self._proceso = None
			self._sesion = ""
		if anterior is not None:
			anterior.terminar(espera=2.0)
		remota = getattr(self, "_remota", False)
		carpeta = None if remota else (self.proveedor.carpeta or self.ajustes.carpeta)
		proceso = ProcesoLineas(
			orden,
			al_linea=lambda linea: self._al_linea(generacion, linea),
			al_terminar=lambda codigo: self._al_terminar(generacion, codigo),
			carpeta=Path(carpeta) if carpeta else None,
			entorno=remoto.entorno_ssh() if remota else entorno_hijo(self.proveedor.entorno_quitar),
			nombre=self.proveedor.id or "acp",
		)
		with self._candado:
			self._proceso = proceso
		try:
			proceso.iniciar()
		except OSError as exc:
			log.debug("No se pudo lanzar %s", orden[:1], exc_info=True)
			with self._candado:
				self._proceso = None
			raise ErrorAcp("No se ha podido iniciar %s: %s" % (self.proveedor.nombre, exc.strerror or exc)) from exc
		# En otro equipo, Ctrl+IA no lee ni escribe archivos por el agente: le llegarían rutas que Windows no tiene.
		archivos = not remota
		parametros = {
			"protocolVersion": VERSION_PROTOCOLO,
			"clientCapabilities": {"fs": {"readTextFile": archivos, "writeTextFile": archivos}, "terminal": False},
			"clientInfo": {"name": "ctrl-ia", "title": "Ctrl+IA", "version": APP_VERSION},
		}
		resultado = self._llamar("initialize", parametros, TIEMPO_ARRANQUE)
		return resultado if isinstance(resultado, dict) else {}

	def _tras_iniciar(self, inicio: dict[str, Any]) -> None:
		version = inicio.get("protocolVersion")
		if version != VERSION_PROTOCOLO:
			raise ErrorAcp(
				"%s usa una versión del protocolo ACP que Ctrl+IA no conoce (%s). Actualiza Ctrl+IA o el agente."
				% (self.proveedor.nombre, version)
			)
		capacidades = inicio.get("agentCapabilities")
		metodos = []
		for metodo in inicio.get("authMethods") or []:
			if isinstance(metodo, dict) and metodo.get("id"):
				metodos.append(OpcionLista(str(metodo["id"]), str(metodo.get("name") or metodo["id"]), str(metodo.get("description") or "")))
		with self._candado:
			self._capacidades_agente = capacidades if isinstance(capacidades, dict) else {}
			self._metodos_auth = metodos
		self.proveedor.recordar(metodos_auth=metodos)

	def _abrir_sesion(self) -> bool:
		"""Abre la sesión; ``True`` si se ha retomado una anterior."""
		carpeta_remota = remoto.carpeta_remota(self.ajustes.extra)
		base = {"cwd": carpeta_remota or str(Path(self.ajustes.carpeta).resolve()), "mcpServers": self._servidores_mcp()}
		capacidades = self._capacidades_agente
		sesiones = capacidades.get("sessionCapabilities") or {}
		previa = self.ajustes.id_externo
		if previa:
			metodo = ""
			if capacidades.get("loadSession"):
				metodo = "session/load"
			elif isinstance(sesiones, dict) and sesiones.get("resume") is not None:
				metodo = "session/resume"
			if metodo:
				with self._candado:
					self._sesion = previa
					self._cargando = metodo == "session/load"
					self._historial = []
				try:
					resultado = self._llamar_con_auth(metodo, {"sessionId": previa, **base}, TIEMPO_CARGA)
				except ErrorAcp as exc:
					if exc.muerto:
						raise
					log.info("No se pudo retomar la sesión %s: %s", previa, exc.mensaje)
					self._emitir(TipoEvento.AVISO, "No se ha podido retomar la conversación anterior; se empieza una nueva.")
				else:
					with self._candado:
						self._cargando = False
					self._aplicar_sesion(resultado, previa)
					return True
				finally:
					with self._candado:
						self._cargando = False
			else:
				self._emitir(TipoEvento.AVISO, "Este agente no sabe retomar conversaciones; se empieza una nueva.")
		with self._candado:
			self._sesion = ""
		resultado = self._llamar_con_auth("session/new", base, TIEMPO_ARRANQUE)
		sesion = resultado.get("sessionId") if isinstance(resultado, dict) else None
		if not sesion:
			raise ErrorAcp("El agente no ha abierto la conversación (falta el identificador de sesión).")
		self._aplicar_sesion(resultado, str(sesion), nueva=True)
		return False

	def _servidores_mcp(self) -> list[dict[str, Any]]:
		"""``mcpServers`` de la sesión: las herramientas de Ctrl+IA (no en otro equipo por SSH).

		Por HTTP si el agente lo anuncia (``mcpCapabilities.http``, como Gemini CLI, que con
		un servidor stdio falla si la carpeta no es de confianza); si no, el puente stdio.
		"""
		if getattr(self, "_remota", False) or remoto.es_remota(self.ajustes.extra):
			return []
		# El nombre sin la empresa («Gemini CLI (Google)» → «Gemini CLI»): es quien avisa.
		herramientas = self._preparar_herramientas_ctrlia(self.proveedor.nombre.split(" (")[0])
		if herramientas is None:
			return []
		from app.services import herramientas_propias

		mcp = self._capacidades_agente.get("mcpCapabilities")
		http = isinstance(mcp, dict) and mcp.get("http") is True
		try:
			return [herramientas_propias.servidor_acp(herramientas, http)]
		except OSError:
			log.warning("No se pudo arrancar el servidor de las herramientas de Ctrl+IA", exc_info=True)
			return []

	def inventario(self) -> Any:
		"""Solo lo que le da Ctrl+IA: ACP no tiene forma de preguntar al agente qué tiene cargado."""
		if self.herramientas_ctrlia is None:
			return None
		from app.services import herramientas_propias

		return herramientas_propias.inventario_basico(self.proveedor.nombre.split(" (")[0])

	def _llamar_con_auth(self, metodo: str, parametros: dict[str, Any], tiempo: float) -> Any:
		"""Llama a ``metodo``; si el agente exige iniciar sesión, lo intenta una vez."""
		try:
			return self._llamar(metodo, parametros, tiempo)
		except ErrorAcp as exc:
			if exc.muerto or exc.codigo != ERROR_AUTENTICACION:
				raise
			metodos = self.metodos_autenticacion()
			elegido = self.proveedor.elegir_metodo_auth(metodos, self.ajustes)
			if not elegido:
				nombres = ", ".join(metodo_auth.etiqueta for metodo_auth in metodos) or "ninguna"
				raise ErrorAcp(
					"%s necesita que inicies sesión. Ábrelo una vez en una consola para hacerlo. Formas disponibles: %s."
					% (self.proveedor.nombre, nombres),
					codigo=exc.codigo,
				) from exc
			log.info("Iniciando sesión en %s con el método %s", self.proveedor.id, elegido)
			try:
				self._llamar("authenticate", {"methodId": elegido}, TIEMPO_AUTENTICACION)
			except ErrorAcp as error_auth:
				if error_auth.muerto:
					raise
				raise ErrorAcp(
					"No se ha podido iniciar sesión en %s: %s" % (self.proveedor.nombre, error_auth.mensaje), codigo=error_auth.codigo
				) from error_auth
			return self._llamar(metodo, parametros, tiempo)

	def _aplicar_sesion(self, resultado: Any, sesion: str, nueva: bool = False) -> None:
		resultado = resultado if isinstance(resultado, dict) else {}
		modos: list[OpcionLista] = []
		modo_actual = ""
		datos_modos = resultado.get("modes")
		if isinstance(datos_modos, dict):
			modo_actual = str(datos_modos.get("currentModeId") or "")
			for modo in datos_modos.get("availableModes") or []:
				if isinstance(modo, dict) and modo.get("id"):
					modos.append(OpcionLista(str(modo["id"]), str(modo.get("name") or modo["id"]), str(modo.get("description") or "")))
		modelos: list[OpcionLista] = []
		modelo_actual = ""
		datos_modelos = resultado.get("models")
		if isinstance(datos_modelos, dict):
			modelo_actual = str(datos_modelos.get("currentModelId") or "")
			for modelo in datos_modelos.get("availableModels") or []:
				if isinstance(modelo, dict) and modelo.get("modelId"):
					modelos.append(
						OpcionLista(str(modelo["modelId"]), str(modelo.get("name") or modelo["modelId"]), str(modelo.get("description") or ""))
					)
		with self._candado:
			self._sesion = sesion
			self._modos = modos
			self._modelos = modelos
			self._modo_actual = modo_actual
			self._modelo_actual = modelo_actual
		self.ajustes.id_externo = sesion
		# Una conversación nueva empieza en el modo de la configuración del agente: es el que se propone después.
		self.proveedor.recordar(modos=modos, modelos=modelos, modo_inicial=modo_actual if nueva else None)

	def _aplicar_ajustes_iniciales(self) -> None:
		modo = self.ajustes.modo_permiso
		if modo and modo != self._modo_actual and any(opcion.id == modo for opcion in self._modos):
			try:
				self._llamar("session/set_mode", {"sessionId": self._sesion, "modeId": modo}, TIEMPO_AJUSTE)
				self._modo_actual = modo
			except ErrorAcp as exc:
				if exc.muerto:
					raise
				self._emitir(TipoEvento.AVISO, "No se ha podido poner el modo elegido: %s" % exc.mensaje)
		modelo = self.ajustes.modelo
		if modelo and modelo != self._modelo_actual and any(opcion.id == modelo for opcion in self._modelos):
			try:
				self._llamar("session/set_model", {"sessionId": self._sesion, "modelId": modelo}, TIEMPO_AJUSTE)
				self._modelo_actual = modelo
			except ErrorAcp as exc:
				if exc.muerto:
					raise
				self._emitir(TipoEvento.AVISO, "No se ha podido poner el modelo elegido: %s" % exc.mensaje)

	def _tras_ajuste(self, peticion: _Peticion, que: str, valor: str) -> None:
		if peticion.error is not None:
			self._emitir(TipoEvento.AVISO, "No se ha podido cambiar el %s: %s" % (que, peticion.error.mensaje))
			return
		if que == "modo":
			self._fijar_modo(valor)
		else:
			nombre = next((opcion.etiqueta for opcion in self._modelos if opcion.id == valor), valor)
			with self._candado:
				cambia = self._modelo_actual != valor
				self._modelo_actual = valor
			if cambia:
				self._emitir(TipoEvento.AVISO, "Modelo cambiado a %s." % nombre, modelo=valor)

	def _fijar_modo(self, modo: str) -> None:
		with self._candado:
			cambia = modo != self._modo_actual
			self._modo_actual = modo
			cargando = self._cargando
		self.ajustes.modo_permiso = modo
		if cambia and not cargando:
			nombre = next((opcion.etiqueta for opcion in self._modos if opcion.id == modo), modo)
			self._emitir(TipoEvento.AVISO, "Modo de trabajo: %s." % nombre, modo=modo)

	# -- JSON-RPC -------------------------------------------------------------------

	def _enviar_peticion(
		self,
		metodo: str,
		parametros: dict[str, Any],
		al_responder: Callable[[_Peticion], None] | None = None,
		registrar_prompt: bool = False,
	) -> _Peticion:
		peticion = _Peticion(metodo, al_responder)
		with self._candado:
			proceso = self._proceso
			ident = next(self._ids)
			if proceso is not None:
				self._peticiones[ident] = peticion
			if registrar_prompt:
				self._prompt = peticion
		if proceso is None or not proceso.escribir_json({"jsonrpc": "2.0", "id": ident, "method": metodo, "params": parametros}):
			with self._candado:
				self._peticiones.pop(ident, None)
			peticion.resolver(error=ErrorAcp("No se ha podido comunicar con el agente.", muerto=True))
		return peticion

	def _llamar(self, metodo: str, parametros: dict[str, Any], tiempo: float | None) -> Any:
		peticion = self._enviar_peticion(metodo, parametros)
		try:
			return peticion.esperar(tiempo)
		finally:
			if not peticion.evento.is_set():
				with self._candado:
					for ident, otra in list(self._peticiones.items()):
						if otra is peticion:
							del self._peticiones[ident]

	def _responder(self, ident: Any, resultado: Any) -> None:
		proceso = self._proceso
		if proceso is not None:
			proceso.escribir_json({"jsonrpc": "2.0", "id": ident, "result": resultado})

	def _responder_error(self, ident: Any, codigo: int, mensaje: str) -> None:
		proceso = self._proceso
		if proceso is not None:
			proceso.escribir_json({"jsonrpc": "2.0", "id": ident, "error": {"code": codigo, "message": mensaje}})

	def _al_linea(self, generacion: int, linea: str) -> None:
		if generacion != self._generacion:
			return
		mensaje = json_o_nada(linea)
		if mensaje is None:
			log.debug("%s escribió algo que no es JSON: %s", self.proveedor.id, linea[:200])
			return
		metodo = mensaje.get("method")
		if isinstance(metodo, str):
			parametros = mensaje.get("params")
			parametros = parametros if isinstance(parametros, dict) else {}
			if mensaje.get("id") is not None:
				self._atender_peticion(mensaje["id"], metodo, parametros)
			else:
				self._atender_notificacion(metodo, parametros)
			return
		if "id" in mensaje:
			self._atender_respuesta(mensaje)

	def _atender_respuesta(self, mensaje: dict[str, Any]) -> None:
		with self._candado:
			peticion = self._peticiones.pop(mensaje.get("id"), None)  # type: ignore[arg-type]
		if peticion is None:
			log.debug("Respuesta a una petición desconocida: %s", mensaje.get("id"))
			return
		if "error" in mensaje and mensaje["error"] is not None:
			texto, codigo = _mensaje_error_rpc(mensaje["error"])
			peticion.resolver(error=ErrorAcp(texto, codigo=codigo))
		else:
			peticion.resolver(resultado=mensaje.get("result"))

	def _al_terminar(self, generacion: int, codigo: int | None) -> None:
		if generacion != self._generacion:
			return
		with self._candado:
			proceso = self._proceso
			self._proceso = None
			self._sesion = ""
			pendientes = list(self._peticiones.values())
			self._peticiones.clear()
			cerrada = self._cerrada
			arrancando = self._arrancando
			if not cerrada and not arrancando:
				self._devueltos.extend(texto for texto, _adjuntos in self._cola)
				self._cola.clear()
		lineas: list[str] = []
		if proceso is not None:
			if not proceso.errores_recientes(1):
				time.sleep(0.3)  # el hilo del error estándar puede ir un poco por detrás
			lineas = [linea[:300] for linea in proceso.errores_recientes(5)]
		mensaje = "%s se ha cerrado inesperadamente (código %s)." % (self.proveedor.nombre, codigo)
		if lineas:
			mensaje += " Últimos mensajes del agente: " + " | ".join(lineas)
		self._sacar_permisos_y_avisar()
		error = ErrorAcp(mensaje, codigo=codigo, muerto=True)
		for peticion in pendientes:
			peticion.resolver(error=error)
		if cerrada or arrancando:
			return
		self._volcar_mensaje()
		self._emitir(TipoEvento.ERROR, mensaje, codigo=codigo, errores=lineas)
		self._cambiar_estado(Estado.ERROR)

	# -- Peticiones del agente --------------------------------------------------------

	def _atender_peticion(self, ident: Any, metodo: str, parametros: dict[str, Any]) -> None:
		try:
			if metodo == "session/request_permission":
				self._pedir_permiso(ident, parametros)
			elif metodo == "fs/read_text_file":
				self._leer_archivo(ident, parametros)
			elif metodo == "fs/write_text_file":
				self._escribir_archivo(ident, parametros)
			elif metodo.startswith("terminal/"):
				self._responder_error(ident, ERROR_METODO, "Ctrl+IA no ofrece terminal al agente.")
			else:
				log.debug("Petición no admitida de %s: %s", self.proveedor.id, metodo)
				self._responder_error(ident, ERROR_METODO, "Método no admitido por Ctrl+IA: %s" % metodo)
		except Exception:  # noqa: BLE001 - el agente siempre debe recibir respuesta
			log.exception("Fallo al atender %s", metodo)
			self._responder_error(ident, ERROR_INTERNO, "Error interno de Ctrl+IA al atender %s." % metodo)

	def _pedir_permiso(self, ident: Any, parametros: dict[str, Any]) -> None:
		llamada = parametros.get("toolCall") if isinstance(parametros.get("toolCall"), dict) else {}
		id_herramienta = str(llamada.get("toolCallId") or "")
		with self._candado:
			info = dict(self._herramientas.get(id_herramienta, {}))
			cancelado = self._prompt is not None and bool(self._prompt.cancelada_en)
		info.update({clave: valor for clave, valor in llamada.items() if valor is not None})
		if cancelado or self._cerrada:
			self._responder(ident, {"outcome": {"outcome": "cancelled"}})
			return
		opciones: list[OpcionPermiso] = []
		for opcion in parametros.get("options") or []:
			if not isinstance(opcion, dict) or not opcion.get("optionId"):
				continue
			base, clase = _OPCIONES_PERMISO.get(str(opcion.get("kind")), ("Elegir", ClaseOpcion.PERMITIR))
			nombre = str(opcion.get("name") or "").strip()
			etiqueta, ayuda = base, ""
			if nombre and _normalizar_nombre(nombre) not in _NOMBRES_GENERICOS:
				etiqueta, ayuda = "%s (%s)" % (base, nombre), nombre
			opciones.append(OpcionPermiso(str(opcion["optionId"]), etiqueta, clase, ayuda))
		if not opciones:
			log.warning("Petición de permiso sin opciones; se responde como cancelada")
			self._responder(ident, {"outcome": {"outcome": "cancelled"}})
			return
		nombre_herramienta, detalle = describir_herramienta(info)
		cambios = [
			diff_unificado(str(bloque.get("path") or ""), bloque.get("oldText"), str(bloque.get("newText") or ""))
			for bloque in info.get("content") or []
			if isinstance(bloque, dict) and bloque.get("type") == "diff"
		]
		if cambios:
			detalle += "\n\nCambios propuestos:\n" + _recortar("\n".join(cambios))
		titulo_herramienta = str(info.get("title") or "").strip() or nombre_herramienta
		solicitud = SolicitudPermiso(
			id="acp-%d" % next(self._contador_permisos),
			tipo=TipoSolicitud.HERRAMIENTA,
			titulo="El agente pide permiso: %s" % titulo_herramienta,
			detalle=detalle,
			herramienta=nombre_herramienta,
			opciones=opciones,
			datos={"toolCallId": id_herramienta, "kind": info.get("kind") or "other"},
		)
		with self._candado:
			self._permisos[solicitud.id] = (ident, solicitud)
		self._volcar_mensaje()
		self._cambiar_estado(Estado.ESPERANDO_USUARIO)
		self._emitir(TipoEvento.PERMISO, solicitud.titulo, solicitud=solicitud)

	def _sacar_permisos(self) -> list[tuple[Any, SolicitudPermiso]]:
		with self._candado:
			permisos = list(self._permisos.values())
			self._permisos.clear()
		return permisos

	def _sacar_permisos_y_avisar(self) -> None:
		for _ident, solicitud in self._sacar_permisos():
			self._emitir(TipoEvento.PERMISO_RETIRADO, "Ya no hace falta responder a esta petición.", id=solicitud.id)

	def _retirar_permisos(self) -> None:
		"""Responde «cancelado» a todo lo pendiente y lo retira de la interfaz."""
		permisos = self._sacar_permisos()
		for ident, solicitud in permisos:
			self._responder(ident, {"outcome": {"outcome": "cancelled"}})
			self._emitir(TipoEvento.PERMISO_RETIRADO, "Ya no hace falta responder a esta petición.", id=solicitud.id)
		if permisos and self._trabajador_activo and not self._cerrada:
			self._cambiar_estado(Estado.TRABAJANDO)

	def _ruta_de(self, valor: Any) -> Path:
		ruta = Path(str(valor))
		if not ruta.is_absolute():
			ruta = Path(self.ajustes.carpeta) / ruta
		return ruta

	def _leer_archivo(self, ident: Any, parametros: dict[str, Any]) -> None:
		if not parametros.get("path"):
			self._responder_error(ident, ERROR_PARAMETROS, "Falta la ruta del archivo.")
			return
		ruta = self._ruta_de(parametros["path"])
		try:
			with ruta.open("r", encoding="utf-8", errors="replace", newline="") as archivo:
				texto = archivo.read()
		except FileNotFoundError:
			self._responder_error(ident, ERROR_NO_ENCONTRADO, "No existe el archivo %s." % ruta)
			return
		except OSError as exc:
			log.debug("No se pudo leer %s", ruta, exc_info=True)
			self._responder_error(ident, ERROR_INTERNO, "No se ha podido leer %s: %s" % (ruta, exc.strerror or exc))
			return
		linea = parametros.get("line")
		limite = parametros.get("limit")
		if isinstance(linea, int) or isinstance(limite, int):
			lineas = texto.splitlines(keepends=True)
			inicio = max(linea - 1, 0) if isinstance(linea, int) else 0
			fin = inicio + max(limite, 0) if isinstance(limite, int) else None
			texto = "".join(lineas[inicio:fin])
		self._responder(ident, {"content": texto})

	def _escribir_archivo(self, ident: Any, parametros: dict[str, Any]) -> None:
		contenido = parametros.get("content")
		if not parametros.get("path") or not isinstance(contenido, str):
			self._responder_error(ident, ERROR_PARAMETROS, "Faltan la ruta o el contenido del archivo.")
			return
		ruta = self._ruta_de(parametros["path"])
		try:
			destino = ruta.resolve()
			carpeta = Path(self.ajustes.carpeta).resolve()
		except OSError:
			log.debug("No se pudo resolver %s", ruta, exc_info=True)
			self._responder_error(ident, ERROR_PARAMETROS, "La ruta %s no es válida." % ruta)
			return
		if not destino.is_relative_to(carpeta):
			log.info("Se impide escribir fuera de la carpeta de trabajo: %s", destino)
			self._responder_error(
				ident,
				ERROR_PARAMETROS,
				"Ctrl+IA solo deja escribir dentro de la carpeta de la conversación (%s); %s queda fuera." % (carpeta, destino),
			)
			return
		try:
			escribir_atomico(destino, contenido)
		except OSError as exc:
			log.debug("No se pudo escribir %s", destino, exc_info=True)
			self._responder_error(ident, ERROR_INTERNO, "No se ha podido escribir %s: %s" % (destino, exc.strerror or exc))
			return
		self._responder(ident, {})

	# -- Notificaciones ----------------------------------------------------------------

	def _atender_notificacion(self, metodo: str, parametros: dict[str, Any]) -> None:
		if metodo != "session/update":
			log.debug("Notificación no admitida de %s: %s", self.proveedor.id, metodo)
			return
		sesion = parametros.get("sessionId")
		if sesion and self._sesion and sesion != self._sesion:
			log.debug("Notificación de otra sesión: %s", sesion)
			return
		actualizacion = parametros.get("update")
		if isinstance(actualizacion, dict):
			self._actualizar(actualizacion)

	def _actualizar(self, datos: dict[str, Any]) -> None:
		tipo = datos.get("sessionUpdate")
		cargando = self._cargando
		if tipo == "agent_message_chunk":
			texto = _texto_de_contenido(datos.get("content"))
			if cargando:
				self._anadir_historial("asistente", texto)
			elif texto:
				with self._candado:
					if not self._id_mensaje:
						self._id_mensaje = "acp-msg-%d" % next(self._contador_mensajes)
					self._texto.append(texto)
					ident = self._id_mensaje
				self._emitir(TipoEvento.TEXTO_PARCIAL, texto, id=ident)
		elif tipo == "user_message_chunk":
			if cargando:
				self._anadir_historial("usuario", _texto_de_contenido(datos.get("content")))
		elif tipo == "agent_thought_chunk":
			texto = _texto_de_contenido(datos.get("content"))
			if texto and not cargando:
				self._emitir(TipoEvento.RAZONAMIENTO, texto, id=self._id_mensaje)
		elif tipo in ("tool_call", "tool_call_update"):
			if not cargando:
				self._herramienta(datos, nueva=tipo == "tool_call")
		elif tipo == "plan":
			pasos = [
				(str(paso.get("content") or ""), str(paso.get("status") or "pending"))
				for paso in datos.get("entries") or []
				if isinstance(paso, dict)
			]
			hechos = sum(1 for _texto, estado in pasos if estado == "completed")
			self._emitir(TipoEvento.PLAN, "Plan: %d de %d pasos completados." % (hechos, len(pasos)), pasos=pasos)
		elif tipo == "available_commands_update":
			comandos = [
				(str(comando.get("name") or ""), str(comando.get("description") or ""))
				for comando in datos.get("availableCommands") or []
				if isinstance(comando, dict) and comando.get("name")
			]
			with self._candado:
				self._comandos = comandos
			self._emitir(TipoEvento.COMANDOS, "%d comandos disponibles." % len(comandos), comandos=comandos)
		elif tipo == "current_mode_update":
			modo = datos.get("currentModeId") or datos.get("modeId")
			if modo:
				self._fijar_modo(str(modo))
		elif tipo == "usage_update":
			self._emitir_uso_contexto(datos)
		else:
			log.debug("Actualización de sesión no tratada: %s", tipo)

	def _anadir_historial(self, rol: str, texto: str) -> None:
		if not texto:
			return
		with self._candado:
			if self._historial and self._historial[-1][0] == rol:
				self._historial[-1] = (rol, self._historial[-1][1] + texto)
			else:
				self._historial.append((rol, texto))

	def _volcar_mensaje(self) -> None:
		"""Emite como ``MENSAJE`` el texto acumulado del asistente (si lo hay)."""
		with self._candado:
			texto = "".join(self._texto)
			ident = self._id_mensaje
			self._texto = []
			self._id_mensaje = ""
		if texto.strip():
			self._emitir(TipoEvento.MENSAJE, texto, id=ident)

	def _herramienta(self, datos: dict[str, Any], nueva: bool) -> None:
		ident = str(datos.get("toolCallId") or "")
		cambios = {clave: valor for clave, valor in datos.items() if clave not in ("sessionUpdate",) and valor is not None}
		with self._candado:
			conocida = ident in self._herramientas
			info = self._herramientas.setdefault(ident, {})
			info.update(cambios)
			info = dict(info)
			terminada = ident in self._herramientas_terminadas
		if nueva or not conocida:
			self._volcar_mensaje()
			nombre, detalle = describir_herramienta(info)
			titulo = str(info.get("title") or "").strip()
			self._emitir(
				TipoEvento.HERRAMIENTA,
				"%s: %s" % (nombre, titulo) if titulo and titulo != nombre else nombre,
				herramienta=nombre,
				detalle=detalle,
				id=ident,
				clase=info.get("kind") or "other",
			)
		if info.get("status") in ("completed", "failed") and not terminada:
			with self._candado:
				self._herramientas_terminadas.add(ident)
			self._fin_herramienta(ident, info)

	def _fin_herramienta(self, ident: str, info: dict[str, Any]) -> None:
		nombre, _detalle = describir_herramienta(info)
		fallida = info.get("status") == "failed"
		textos: list[str] = []
		for bloque in info.get("content") or []:
			if not isinstance(bloque, dict):
				continue
			if bloque.get("type") == "content":
				texto = _texto_de_contenido(bloque.get("content"))
				if texto:
					textos.append(texto)
			elif bloque.get("type") == "diff" and not fallida:
				self._emitir_archivo(bloque, info)
		if not textos and info.get("rawOutput") is not None:
			salida_cruda = info["rawOutput"]
			textos.append(salida_cruda if isinstance(salida_cruda, str) else json.dumps(salida_cruda, ensure_ascii=False))
		salida = _recortar("\n".join(textos))
		titulo = str(info.get("title") or "").strip() or nombre
		self._emitir(
			TipoEvento.HERRAMIENTA_FIN,
			("Ha fallado: %s" if fallida else "Terminado: %s") % titulo,
			herramienta=nombre,
			salida=salida,
			error=fallida,
			id=ident,
		)

	def _emitir_archivo(self, bloque: dict[str, Any], info: dict[str, Any]) -> None:
		ruta = str(bloque.get("path") or "")
		antes = bloque.get("oldText")
		despues = str(bloque.get("newText") or "")
		if antes is None:
			tipo = "creado"
		elif not despues and info.get("kind") == "delete":
			tipo = "borrado"
		else:
			tipo = "modificado"
		mostrada = ruta
		try:
			mostrada = str(Path(ruta).resolve().relative_to(Path(self.ajustes.carpeta).resolve()))
		except (OSError, ValueError):
			log.debug("La ruta %s está fuera de la carpeta de trabajo", ruta)
		diff = diff_unificado(mostrada, antes if isinstance(antes, str) else None, despues)
		# «tipo» choca con el primer parámetro de _emitir: se pasan los datos en un diccionario.
		self._emitir_con_datos(TipoEvento.ARCHIVO, "Archivo %s: %s" % (tipo, mostrada), {"ruta": ruta, "tipo": tipo, "diff": diff})

	def _emitir_uso_contexto(self, datos: dict[str, Any]) -> None:
		usado = datos.get("used")
		total = datos.get("size")
		coste = datos.get("cost")
		importe = None
		moneda = ""
		if isinstance(coste, dict):
			importe = coste.get("amount")
			moneda = str(coste.get("currency") or "")
		texto = "Uso del contexto actualizado."
		if isinstance(usado, int | float) and isinstance(total, int | float) and total:
			texto = "Contexto usado: %d %%." % round(usado * 100 / total)
		self._emitir(
			TipoEvento.USO, texto, entrada=None, salida=None, contexto_usado=usado, contexto_total=total, coste=importe, moneda=moneda
		)

	def _emitir_uso_turno(self, uso: Any) -> None:
		if not isinstance(uso, dict):
			return
		entrada = uso.get("inputTokens")
		salida = uso.get("outputTokens")
		self._emitir(
			TipoEvento.USO,
			"Uso del turno: %s de entrada y %s de salida." % (entrada, salida),
			entrada=entrada,
			salida=salida,
			contexto_usado=None,
			contexto_total=None,
			coste=None,
		)


def escribir_atomico(destino: Path, contenido: str) -> None:
	"""Escribe ``contenido`` en UTF-8 de golpe: o queda el archivo nuevo o el viejo."""
	destino.parent.mkdir(parents=True, exist_ok=True)
	descriptor, temporal = tempfile.mkstemp(prefix=".ctrlia-", suffix=".tmp", dir=str(destino.parent))
	try:
		with os.fdopen(descriptor, "wb") as archivo:
			archivo.write(contenido.encode("utf-8"))
			archivo.flush()
			os.fsync(archivo.fileno())
		os.replace(temporal, destino)
	except BaseException:
		try:
			os.unlink(temporal)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
		raise


# --------------------------------------------------------------------------
# Proveedor
# --------------------------------------------------------------------------

OrdenAgente = list[str] | Callable[[], list[str] | None]


class ProveedorAcp(Proveedor):
	"""Agente que habla ACP, configurable.

	:param orden: programa y argumentos para lanzarlo en modo ACP, o una
		función que la busca (devuelve ``None`` si no está instalado).
	:param entorno_quitar: variables de entorno que el agente no debe heredar.
	:param sustituciones: argumentos que se cambian si el agente muere al
		arrancar (p. ej. ``{"--acp": "--experimental-acp"}`` para versiones
		antiguas); lo que funcione se recuerda.
	:param carpeta: carpeta desde la que se lanza el proceso (por omisión, la
		de la conversación).
	:param version: función que da la versión a partir de la orden resuelta.
	:param elegir_auth: función que elige el método de inicio de sesión entre
		los anunciados (``None``: no iniciar sesión automáticamente).
	"""

	def __init__(
		self,
		id: str,  # noqa: A002 - nombre del contrato de Proveedor
		nombre: str,
		descripcion: str,
		orden: OrdenAgente,
		web: str = "",
		instrucciones: str = "",
		entorno_quitar: Iterable[str] = (),
		*,
		sustituciones: dict[str, str] | None = None,
		carpeta: Path | str | None = None,
		version: Callable[[list[str]], str] | None = None,
		elegir_auth: Callable[[list[OpcionLista]], str | None] | None = None,
		instalacion: list[str] | None = None,
		inicio_sesion: Callable[[], list[str] | None] | None = None,
		argumentos_remotos: list[str] | None = None,
	) -> None:
		self.id = id
		self._argumentos_remotos = list(argumentos_remotos) if argumentos_remotos is not None else None
		self.nombre = nombre
		self.descripcion = descripcion
		self.web = web
		self.instrucciones = instrucciones
		self.entorno_quitar = tuple(entorno_quitar)
		self.carpeta = Path(carpeta) if carpeta else None
		self.capacidades = Capacidades(
			adjuntar_imagenes=True,
			adjuntar_archivos=True,
			bifurcar=False,
			permisos_interactivos=True,
			cambiar_modelo_en_marcha=True,
			comandos_barra=True,
			enviar_ahora=True,
			leer_historial_externo=False,
		)
		self._orden = orden
		self._sustituciones = dict(sustituciones or {})
		self._usar_sustitucion = False
		self._version = version
		self._elegir_auth = elegir_auth
		self._instalacion = instalacion
		self._inicio_sesion = inicio_sesion
		self._candado = threading.Lock()
		self._modos: list[OpcionLista] = []
		self._modo_inicial = ""
		self._modelos: list[OpcionLista] = []
		self._metodos_auth: list[OpcionLista] = []

	# -- Orden ------------------------------------------------------------------

	def resolver_orden(self) -> list[str] | None:
		"""Orden lista para lanzar (sin shell), o ``None`` si no se encuentra."""
		try:
			base = self._orden() if callable(self._orden) else list(self._orden)
		except Exception:  # noqa: BLE001 - una búsqueda rota equivale a «no instalado»
			log.debug("Falló la búsqueda de %s", self.id, exc_info=True)
			return None
		if not base:
			return None
		orden = preparar_orden(base)
		if orden and self._usar_sustitucion:
			orden = self._sustituir(orden)
		return orden

	def orden_para_remoto(self) -> list[str] | None:
		"""Programa y argumentos en el otro equipo (experimental), o ``None`` si no se puede.

		Los agentes conocidos dicen sus argumentos (Gemini CLI: ``--acp``); en los
		personalizados se usa el nombre del programa de su orden y sus argumentos.
		Si el programa no está en el PATH del otro equipo, su ruta se indica en
		«Equipos por SSH».
		"""
		if self._argumentos_remotos is not None:
			orden = [self.id, *self._argumentos_remotos]
		elif not callable(self._orden) and self._orden:
			original = list(self._orden)
			orden = [Path(original[0]).stem, *original[1:]]
		else:
			return None
		return self._sustituir(orden) if self._usar_sustitucion else orden

	def _sustituir(self, orden: list[str]) -> list[str]:
		return [orden[0], *(self._sustituciones.get(parte, parte) for parte in orden[1:])]

	def orden_alternativa(self, orden: list[str]) -> list[str] | None:
		"""Orden con los argumentos sustituidos, si cambia algo y no se ha probado ya."""
		if self._usar_sustitucion or not self._sustituciones:
			return None
		alternativa = self._sustituir(orden)
		return alternativa if alternativa != orden else None

	def recordar_alternativa(self) -> None:
		self._usar_sustitucion = True

	# -- Lo aprendido de las sesiones ---------------------------------------------

	def recordar(
		self,
		modos: list[OpcionLista] | None = None,
		modelos: list[OpcionLista] | None = None,
		metodos_auth: list[OpcionLista] | None = None,
		modo_inicial: str | None = None,
	) -> None:
		"""Guarda lo último que anunció el agente (para los desplegables)."""
		with self._candado:
			if modos is not None:
				self._modos = list(modos)
			if modo_inicial is not None:
				self._modo_inicial = modo_inicial
			if modelos is not None:
				self._modelos = list(modelos)
			if metodos_auth is not None:
				self._metodos_auth = list(metodos_auth)

	def elegir_metodo_auth(self, metodos: list[OpcionLista], ajustes: AjustesSesion) -> str | None:
		"""Método de inicio de sesión que se usa si el agente lo exige."""
		ids = [metodo.id for metodo in metodos]
		preferido = ajustes.extra.get("metodo_autenticacion") if isinstance(ajustes.extra, dict) else None
		if preferido and preferido in ids:
			return str(preferido)
		if self._elegir_auth is not None:
			elegido = self._elegir_auth(metodos)
			if elegido in ids:
				return elegido
		return ids[0] if len(ids) == 1 else None

	def metodos_autenticacion(self) -> list[OpcionLista]:
		with self._candado:
			return list(self._metodos_auth)

	# -- Contrato de Proveedor --------------------------------------------------------

	def detectar(self) -> EstadoProveedor:
		orden = self.resolver_orden()
		if not orden:
			return EstadoProveedor(False, mensaje=("No se ha encontrado %s. %s" % (self.nombre, self.instrucciones)).strip())
		version = ""
		try:
			version = self._version(orden) if self._version is not None else version_por_paquete(orden)
		except Exception:  # noqa: BLE001 - la versión es solo informativa
			log.debug("No se pudo obtener la versión de %s", self.id, exc_info=True)
		ruta = orden[1] if len(orden) > 1 and Path(orden[0]).stem.lower() == "node" else orden[0]
		mensaje = "Preparado para usar."
		metodos = self.metodos_autenticacion()
		if metodos:
			mensaje += " Formas de iniciar sesión: %s." % ", ".join(metodo.etiqueta for metodo in metodos)
		return EstadoProveedor(True, ruta=ruta, version=version, mensaje=mensaje)

	def crear_conversacion(self, ajustes: AjustesSesion, al_evento: ReceptorEventos) -> ConversacionAcp:
		return ConversacionAcp(ajustes, al_evento, self)

	def modelos(self) -> list[OpcionLista]:
		with self._candado:
			return [OpcionLista("", "Predeterminado del agente"), *self._modelos]

	def modos_permiso(self) -> list[OpcionLista]:
		with self._candado:
			return list(self._modos)

	def modo_predeterminado(self, carpeta: Path | None = None) -> str:
		"""El modo en el que el agente abrió su última conversación nueva (el de su configuración).

		Así se respeta, por ejemplo, el «Modo de aprobación inicial» de Gemini CLI.
		Antes de la primera conversación no hay lista de modos y no se fuerza ninguno.
		"""
		with self._candado:
			modos, inicial = list(self._modos), self._modo_inicial
		if inicial and any(modo.id == inicial for modo in modos):
			return inicial
		return self.modo_de_serie()

	def instrucciones_instalacion(self) -> str:
		return self.instrucciones

	def orden_instalacion(self) -> list[str] | None:
		return list(self._instalacion) if self._instalacion else None

	def orden_inicio_sesion(self) -> list[str] | None:
		if self._inicio_sesion is None:
			return None
		try:
			return self._inicio_sesion()
		except Exception:  # noqa: BLE001 - sin orden de inicio de sesión
			log.debug("No se pudo preparar el inicio de sesión de %s", self.id, exc_info=True)
			return None


# --------------------------------------------------------------------------
# Agentes conocidos
# --------------------------------------------------------------------------

_PAQUETE_GEMINI = "@google/gemini-cli"


def _buscar_gemini() -> list[str] | None:
	"""``gemini`` (binario nativo) o ``node …/@google/gemini-cli/<bin>``, sin argumentos."""
	nativo = buscar_ejecutable(["gemini"])
	if nativo is not None:
		return [str(nativo)]
	for script in ("dist/index.js", "bundle/gemini.js"):
		orden = buscar_programa_node("gemini", "%s/%s" % (_PAQUETE_GEMINI, script))
		if orden:
			return orden
	hallado = buscar_paquete_npm(_PAQUETE_GEMINI, "gemini")
	return hallado[0] if hallado else None


def _version_gemini(orden: list[str]) -> str:
	version = version_por_paquete(orden)
	if version:
		return version
	return version_de([orden[0], "--version"]) if Path(orden[0]).stem.lower() != "node" else ""


def _auth_gemini(metodos: list[OpcionLista]) -> str | None:
	"""Con una clave en el entorno, se usa la clave (nunca se piden contraseñas)."""
	ids = {metodo.id for metodo in metodos}
	if os.environ.get("GEMINI_API_KEY") and "gemini-api-key" in ids:
		return "gemini-api-key"
	if (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")) and "vertex-ai" in ids:
		return "vertex-ai"
	return None


def proveedor_gemini_cli() -> ProveedorAcp:
	"""Gemini CLI de Google en modo ACP (``gemini --acp``)."""

	def orden() -> list[str] | None:
		base = _buscar_gemini()
		return [*base, "--acp"] if base else None

	return ProveedorAcp(
		"gemini",
		"Gemini CLI (Google)",
		"Agente de programación de Google. Desde junio de 2026 solo funciona con una clave de API de pago "
		"(Google AI Studio o Vertex AI) o con una licencia de empresa; la cuenta gratuita de Google ya no sirve.",
		orden,
		web="https://github.com/google-gemini/gemini-cli",
		instrucciones=(
			"Para usar Gemini CLI: 1) instala Node.js (versión LTS) desde nodejs.org; "
			"2) en una consola escribe: npm install -g @google/gemini-cli; "
			"3) necesitas una clave de API de pago de Google AI Studio o Vertex AI, o una licencia de empresa: "
			"guarda la clave en la variable de entorno GEMINI_API_KEY o abre gemini una vez en una consola para configurarla. "
			"Desde junio de 2026 la cuenta gratuita de Google ya no sirve."
		),
		sustituciones={"--acp": "--experimental-acp"},
		version=_version_gemini,
		elegir_auth=_auth_gemini,
		instalacion=["cmd.exe", "/k", "npm install -g @google/gemini-cli"],
		inicio_sesion=_buscar_gemini,
		argumentos_remotos=["--acp"],
	)


def _orden_paquete(paquetes: Sequence[tuple[str, str]], nativos: Sequence[str] = ()) -> Callable[[], list[str] | None]:
	"""Función que busca un adaptador: ejecutable nativo o paquete de npm."""

	def buscar() -> list[str] | None:
		if nativos:
			nativo = buscar_ejecutable(list(nativos))
			if nativo is not None:
				return [str(nativo)]
		for paquete, nombre_bin in paquetes:
			hallado = buscar_paquete_npm(paquete, nombre_bin)
			if hallado:
				return hallado[0]
		return None

	return buscar


def proveedor_claude_acp() -> ProveedorAcp:
	"""Claude Code a través del adaptador oficial de ACP (opcional)."""
	return ProveedorAcp(
		"claude-acp",
		"Claude Code (adaptador ACP)",
		"Claude Code a través del adaptador oficial del protocolo ACP. Es opcional: usa tu sesión de Claude Code.",
		_orden_paquete([("@agentclientprotocol/claude-agent-acp", "claude-agent-acp"), ("@zed-industries/claude-code-acp", "claude-code-acp")]),
		web="https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp",
		instrucciones=(
			"Para usar este adaptador: 1) instala Node.js (versión LTS) desde nodejs.org; "
			"2) en una consola escribe: npm install -g @agentclientprotocol/claude-agent-acp; "
			"3) inicia sesión una vez en Claude Code."
		),
		entorno_quitar=("ANTHROPIC_API_KEY",),
		instalacion=["cmd.exe", "/k", "npm install -g @agentclientprotocol/claude-agent-acp"],
	)


def proveedor_codex_acp() -> ProveedorAcp:
	"""Codex de OpenAI a través del adaptador oficial de ACP (opcional)."""
	return ProveedorAcp(
		"codex-acp",
		"Codex (adaptador ACP)",
		"Codex de OpenAI a través del adaptador oficial del protocolo ACP. Es opcional: usa tu sesión de Codex.",
		_orden_paquete([("@agentclientprotocol/codex-acp", "codex-acp"), ("@zed-industries/codex-acp", "codex-acp")], nativos=["codex-acp"]),
		web="https://www.npmjs.com/package/@agentclientprotocol/codex-acp",
		instrucciones=(
			"Para usar este adaptador: 1) instala Node.js (versión LTS) desde nodejs.org; "
			"2) en una consola escribe: npm install -g @agentclientprotocol/codex-acp; "
			"3) inicia sesión una vez en Codex."
		),
		instalacion=["cmd.exe", "/k", "npm install -g @agentclientprotocol/codex-acp"],
	)


def proveedor_antigravity_acp() -> ProveedorAcp:
	"""Antigravity a través del adaptador de la comunidad ``antigravity-acp`` (experimental)."""
	return ProveedorAcp(
		"antigravity-acp",
		"Antigravity (adaptador ACP de la comunidad)",
		"Antigravity a través de un adaptador ACP hecho por la comunidad. Es experimental y NO es oficial de Google: "
		"puede fallar o dejar de funcionar con cualquier actualización.",
		_orden_paquete([("antigravity-acp", "antigravity-acp")], nativos=["antigravity-acp"]),
		instrucciones=(
			"Este adaptador no es oficial. Para probarlo: 1) instala Node.js (versión LTS) desde nodejs.org; "
			"2) en una consola escribe: npm install -g antigravity-acp; 3) inicia sesión una vez en Antigravity."
		),
		instalacion=["cmd.exe", "/k", "npm install -g antigravity-acp"],
	)


def proveedores_acp() -> list[ProveedorAcp]:
	"""Los agentes ACP que Ctrl+IA conoce de serie."""
	return [proveedor_gemini_cli(), proveedor_claude_acp(), proveedor_codex_acp(), proveedor_antigravity_acp()]


def _identificador(texto: str) -> str:
	return re.sub(r"[^a-z0-9]+", "-", texto.casefold()).strip("-")


def proveedores_personalizados(lista: list[dict]) -> list[ProveedorAcp]:
	"""Agentes ACP añadidos a mano por el usuario.

	Cada elemento es un diccionario con ``id``, ``nombre``, ``orden`` (lista
	con el programa y sus argumentos) y, opcionalmente, ``carpeta``,
	``descripcion`` y ``entorno_quitar``. Los elementos incorrectos se
	descartan (se anotan en el registro).
	"""
	resultado: list[ProveedorAcp] = []
	vistos: set[str] = set()
	for elemento in lista or []:
		if not isinstance(elemento, dict):
			log.warning("Agente personalizado descartado: no es un diccionario")
			continue
		orden = elemento.get("orden")
		if not isinstance(orden, list | tuple) or not orden or not all(isinstance(parte, str) and parte for parte in orden):
			log.warning("Agente personalizado %s descartado: la orden no es válida", elemento.get("id"))
			continue
		nombre = str(elemento.get("nombre") or "").strip() or Path(orden[0]).stem
		ident = _identificador(str(elemento.get("id") or "")) or "acp-" + (_identificador(nombre) or "agente")
		if ident in vistos:
			log.warning("Agente personalizado %s descartado: identificador repetido", ident)
			continue
		vistos.add(ident)
		quitar = elemento.get("entorno_quitar") or ()
		resultado.append(
			ProveedorAcp(
				ident,
				nombre,
				str(elemento.get("descripcion") or "Agente compatible con ACP añadido por ti."),
				list(orden),
				instrucciones="Comprueba que la orden «%s» es correcta y que el programa está instalado." % " ".join(orden),
				entorno_quitar=[str(variable) for variable in quitar] if isinstance(quitar, list | tuple) else (),
				carpeta=elemento.get("carpeta") or None,
			)
		)
	return resultado
