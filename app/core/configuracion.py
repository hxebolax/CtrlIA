"""Configuración y preferencias de la aplicación (archivo JSON portable).

Qué resuelve este módulo y por qué:

- **Valores por defecto en código** (``VALORES_POR_DEFECTO``), agrupados por
  secciones. El archivo solo guarda lo que el usuario tiene; al cargar se
  completa con los valores por defecto, así una preferencia nueva aparece sola
  y un archivo de una versión antigua sigue funcionando.
- **Archivo en ``data/config/settings.json``**, junto al programa (portable).
- **Tolerancia a daños.** Un JSON corrupto se aparta como
  ``settings.json.corrupto-AAAAMMDD_HHMMSS`` (no se pierde), se intenta la copia
  ``settings.json.bak`` y, si tampoco sirve, se arranca con los valores por
  defecto. Todo queda registrado y ``recuperacion`` dice qué pasó para que la
  interfaz se lo cuente al usuario.
- **Escritura atómica**: temporal único en la misma carpeta + ``fsync`` +
  ``os.replace``. Un corte de luz deja el archivo viejo o el nuevo, nunca uno a
  medias. Antes se copia el archivo actual a ``.bak``.
- **Seguro entre hilos** (``RLock`` para los datos, otro candado para el disco:
  la interfaz no espera al ``fsync`` de un guardado de fondo).
- **Claves «seccion.clave»** con ``obtener``/``establecer``.
- **Versión de esquema y migraciones** (claves renombradas, movidas o
  eliminadas) que se aplican una sola vez al cargar.
- **Suscriptores**: las ventanas abiertas se enteran de cada cambio y aplican
  la preferencia al momento (sin reiniciar).
- **Validación opcional** de tipos, rangos y opciones con ``REGLAS``.
- **Guardado inmediato, diferido (agrupa cambios rápidos) o por lotes.**

Los secretos (contraseñas, tokens) NO van aquí: van cifrados en
``data/config/secretos.dat`` (módulo ``secretos``).

PLANTILLA del agente «configuracion». Adapta ``VALORES_POR_DEFECTO``, ``REGLAS``
y ``MIGRACIONES`` al proyecto.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import sys
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Variable de entorno que redirige la carpeta data (la usan las pruebas).
VARIABLE_DATA = "APP_DATA_DIR"

NOMBRE_ARCHIVO = "settings.json"

# Clave de la raíz del JSON donde se guarda la versión del esquema.
CLAVE_VERSION = "version_esquema"

# Versión actual del esquema. Súbela cuando añadas una migración.
VERSION_ESQUEMA = 2

# ── Valores por defecto ──────────────────────────────────────────────────────
# Un diccionario por sección. Cada valor fija también el TIPO esperado: si en el
# archivo aparece otro tipo (una cadena donde va un número), se descarta y se
# usa el valor por defecto.
VALORES_POR_DEFECTO: dict[str, dict[str, Any]] = {
	"general": {
		"idioma": "auto",
		"confirmar_salida": True,
		"minimizar_a_bandeja": False,
		"anunciar_arranque": True,
		"abrir_ultima_sesion": True,
		"ultima_sesion": "",
		"comprobar_actualizaciones": True,
	},
	"agentes": {
		"predeterminado": "claude",
		"carpeta_predeterminada": "",
		"permitir_clave_api": False,
		"lista_tareas_claude": True,
		# Claude Code espera a sus servidores MCP antes de cada turno (MCP_CONNECTION_NONBLOCKING=0).
		"esperar_mcp_claude": True,
		# Claude Code con «Claude en Chrome» (--chrome): sus herramientas del navegador (AIChat #319).
		# Activada desde la 1.4.4: sin ella, las sesiones de Ctrl+IA no tenían las herramientas de Chrome
		# que sí tienen las de la app de escritorio; cada acción en el navegador sigue pidiendo permiso.
		"chrome_claude": True,
		# Punto de restauración de la carpeta antes de cada mensaje, en un repositorio git aparte de Ctrl+IA
		# (app/services/puntos.py): «Deshacer los cambios de archivos desde este mensaje».
		"puntos_restauracion": True,
		# Herramientas propias de Ctrl+IA para los agentes (servidor MCP «ctrlia»: avisar, dar por terminada,
		# renombrar; app/services/herramientas_propias.py).
		"herramientas_ctrlia": True,
		# Los agentes de ejemplo de data/agentes_ejemplo ya se cargaron en el catálogo (solo la primera vez:
		# si la persona los quita, no vuelven solos; se restauran desde el gestor).
		"ejemplos_cargados": False,
		# Minutos que Claude Code sigue abierto sin uso entre mensajes (0: un proceso por mensaje).
		"claude_minutos_abierto": 15,
		"adaptadores_acp": False,
		"personalizados": [],
		"equipos_ssh": [],
		"editor_externo": "",
		# Último modelo y esfuerzo elegidos con cada agente: {"codex": {"modelo": "...", "esfuerzo": "..."}}.
		"ultimas_opciones": {},
		# Ollama (solo chat con modelos locales). Dirección vacía: OLLAMA_HOST o 127.0.0.1:11434.
		"ollama": True,
		"servidor_ollama": "",
		# OpenCode, Goose, GitHub Copilot CLI y otros agentes ACP conocidos.
		"otros_acp": False,
	},
	"historial": {
		"claude": True,
		"codex": True,
		"antigravity": True,
		"ollama": True,
		"seguir_en_vivo": True,
		# «Continuar aquí» sigue la sesión original de Claude Code o Codex en lugar de copiarla.
		"continuar_original": False,
	},
	"lista": {
		"orden": "recientes",
		"vista": "todas",
		"columnas": ["titulo", "estado", "agente", "carpeta", "actividad"],
		"mostrar_ocultas": False,
		"mostrar_archivadas": False,
	},
	"mensajes": {
		"herramientas": True,
		"razonamiento": False,
		"vista_formateada": True,
		"orden_inverso": False,
		"columnas": ["quien", "mensaje", "hora", "indicadores"],
		# Mensajes que se ponen en la lista al abrir una sesión (0 = todos).
		"recientes": 100,
		# Cómo se leen las líneas cambiadas de un archivo: «palabras», «cortas» o «simbolos» (el diff tal cual).
		"estilo_cambios": "palabras",
	},
	# «Lo que el agente sabe de ti»: orden de la lista («agente», «recientes», «antiguos» o «nombre»).
	"memoria": {
		"orden": "agente",
	},
	"listas": {
		"ambito": "sesion",
	},
	"grupos": {
		"columnas": ["nombre", "sesiones", "descripcion"],
		"columnas_sesiones": ["titulo", "estado", "agente", "actividad"],
	},
	# Qué lee el lector en las listas (Preferencias, «Lectura de listas»).
	"lectura": {
		"numero_fila": True,
		"cabecera": True,
		"contenido": True,
		"favorita_en_titulo": True,
		"sin_leer_en_estado": True,
		"fechas": "relativas",
		"indicadores": True,
		"indicador_favorito": True,
		"indicador_pregunta": True,
		"indicador_permiso": True,
		"indicador_error": True,
		"indicador_detenido": True,
		"indicador_adjuntos": True,
		"indicador_codigo": True,
		"indicador_enlaces": True,
		"indicador_archivos": True,
		"indicador_largo": True,
		"anunciar_vista_vacia": True,
	},
	"anuncios": {
		"nivel": "completo",
		"todas_las_sesiones": False,
		"herramientas": False,
		# «Hecha: ejecutar las pruebas. 4 de 7» al avanzar la lista de tareas del agente.
		"tareas": False,
		"mis_mensajes": False,
		"avisos_importantes": "voz",
		"sonidos": True,
		"retardo_voz_ms": 120,
		"aviso_contexto": 80,
		"aviso_limite": 90,
		# Vigilar la PR de las sesiones con rama en GitHub y avisar cuando sus comprobaciones fallan o pasan (AIChat #236).
		"comprobaciones_pr": True,
		# Nada solo como voz: braille, barra de estado e historial.
		"braille": True,
		"braille_sin_voz": True,
		"barra_estado": True,
		# Palabras curiosas («Cavilando…») en la barra de estado mientras el agente escribe; nunca por voz (#314).
		"palabras_curiosas": False,
	},
	# Esquema de sonidos: el interruptor general es «anuncios.sonidos».
	"sonidos": {
		"esquema": "predeterminado",
		"volumen": 100,
		"nueva": True,
		"enviado": True,
		"herramienta": False,
		"archivo": False,
		"respuesta": True,
		"segundo_plano": True,
		"permiso": True,
		"error": True,
		"detenido": True,
		"compactado": True,
		"limite": True,
	},
	"notificaciones": {
		"modo": "te_necesita",
	},
	"registro": {
		"nivel": "INFO",
	},
	"ventana": {
		"x": -1,
		"y": -1,
		"ancho": 1000,
		"alto": 700,
		"maximizada": False,
		# Disposición y baja visión.
		"mostrar_sesiones": True,
		"panel_lectura": False,
		"reparto": "equilibrado",
		# Parte de la conversación (por ciento) para el cuadro de respuesta, movida con el divisor
		# o con Ver, Distribución de la ventana; 0 = la del reparto elegido (#242).
		"porcentaje_respuesta": 0,
		"resaltar_foco": True,
		"tema": "claro",
	},
}


@dataclass(frozen=True)
class Regla:
	"""Restricciones opcionales de una clave.

	:param minimo: valor mínimo (números).
	:param maximo: valor máximo (números).
	:param opciones: valores admitidos (listas cerradas: tema, idioma...).
	:param longitud_maxima: longitud máxima (textos).
	"""

	minimo: float | None = None
	maximo: float | None = None
	opciones: tuple[Any, ...] | None = None
	longitud_maxima: int | None = None


# Reglas por clave «seccion.clave». Las claves sin regla solo comprueban el tipo.
REGLAS: dict[str, Regla] = {
	"general.idioma": Regla(opciones=("auto", "es", "en")),
	"lista.orden": Regla(opciones=("recientes", "antiguas", "estado", "titulo", "carpeta", "agente")),
	"listas.ambito": Regla(opciones=("sesion", "turno")),
	"memoria.orden": Regla(opciones=("agente", "recientes", "antiguos", "nombre")),
	"lista.vista": Regla(longitud_maxima=200),
	"lectura.fechas": Regla(opciones=("relativas", "absolutas")),
	"mensajes.recientes": Regla(opciones=(0, 50, 100, 250, 500)),
	"mensajes.estilo_cambios": Regla(opciones=("palabras", "cortas", "simbolos")),
	"anuncios.nivel": Regla(opciones=("completo", "resumen", "silencio")),
	"anuncios.avisos_importantes": Regla(opciones=("voz", "mensaje", "ambos")),
	"anuncios.retardo_voz_ms": Regla(minimo=0, maximo=600),
	"anuncios.aviso_contexto": Regla(minimo=0, maximo=100),
	"anuncios.aviso_limite": Regla(minimo=0, maximo=100),
	"notificaciones.modo": Regla(opciones=("todas", "te_necesita", "nunca")),
	"sonidos.esquema": Regla(opciones=("predeterminado", "propio")),
	"sonidos.volumen": Regla(minimo=0, maximo=100),
	"ventana.reparto": Regla(opciones=("equilibrado", "mensajes", "respuesta")),
	"ventana.porcentaje_respuesta": Regla(minimo=0, maximo=90),
	"ventana.tema": Regla(opciones=("claro", "oscuro", "sistema")),
	"registro.nivel": Regla(opciones=("DEBUG", "INFO", "WARNING", "ERROR")),
	"agentes.editor_externo": Regla(longitud_maxima=1000),
	"agentes.carpeta_predeterminada": Regla(longitud_maxima=1000),
	"agentes.servidor_ollama": Regla(longitud_maxima=500),
	"agentes.claude_minutos_abierto": Regla(opciones=(0, 5, 15, 30, 60)),
}


class ErrorConfiguracion(Exception):
	"""Error de configuración que el llamante debe conocer (no se oculta)."""


class ErrorValidacion(ErrorConfiguracion, ValueError):
	"""Valor que no cumple el tipo o la regla de su clave."""


# ── Migraciones ──────────────────────────────────────────────────────────────

def renombrar_clave(datos: dict, antigua: str, nueva: str) -> bool:
	"""Mueve el valor de ``antigua`` a ``nueva`` (ambas «seccion.clave»).

	Si la clave nueva ya existe, gana la nueva (el usuario ya la configuró con
	una versión más reciente) y la antigua se elimina igualmente.

	:return: ``True`` si había algo que mover.
	"""
	sec_antigua, _, clave_antigua = antigua.partition(".")
	sec_nueva, _, clave_nueva = nueva.partition(".")
	origen = datos.get(sec_antigua)
	if not isinstance(origen, dict) or clave_antigua not in origen:
		return False
	valor = origen.pop(clave_antigua)
	destino = datos.setdefault(sec_nueva, {})
	if isinstance(destino, dict):
		destino.setdefault(clave_nueva, valor)
	if not origen:
		# Sección vacía tras el traslado: se elimina para no dejar restos.
		datos.pop(sec_antigua, None)
	return True


def eliminar_clave(datos: dict, clave: str) -> bool:
	"""Elimina una clave obsoleta. ``True`` si existía."""
	seccion, _, nombre = clave.partition(".")
	contenedor = datos.get(seccion)
	if isinstance(contenedor, dict) and nombre in contenedor:
		del contenedor[nombre]
		return True
	return False


# MIGRACIONES[n] lleva los datos de la versión n a la n+1. Cada función recibe
# el diccionario TAL COMO SE LEYÓ del archivo (antes de completar con los
# valores por defecto) y lo modifica en el sitio. Deben ser idempotentes.
# Usa ``renombrar_clave`` y ``eliminar_clave``. Versión 1: no hay ninguna aún.
def _migrar_1_a_2(datos: dict) -> None:
	"""1.4.4: «Dejar que Claude use Chrome» pasa a estar activada.

	En la 1.4.3 (que no se publicó) venía desactivada y se guardaba así sin que
	nadie la eligiera; los probadores echaban de menos las herramientas de Chrome.
	"""
	agentes = datos.get("agentes")
	if isinstance(agentes, dict):
		agentes["chrome_claude"] = True


MIGRACIONES: dict[int, Callable[[dict], None]] = {1: _migrar_1_a_2}


# ── Rutas ────────────────────────────────────────────────────────────────────

def carpeta_datos() -> Path:
	"""Carpeta ``data`` portable: junto a ``main.py`` o junto al ``.exe``.

	Si el proyecto ya tiene un módulo de rutas (``app/core/paths.py``), usa el
	suyo en lugar de esta función.
	"""
	redireccion = os.environ.get(VARIABLE_DATA, "").strip()
	if redireccion:
		return Path(redireccion).expanduser().resolve()
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent / "data"
	# <raíz>/app/core/configuracion.py → <raíz>
	return Path(__file__).resolve().parents[2] / "data"


def ruta_por_defecto() -> Path:
	"""``data/config/settings.json``."""
	return carpeta_datos() / "config" / NOMBRE_ARCHIVO


# ── Utilidades internas ──────────────────────────────────────────────────────

_SIN_VALOR = object()


def _fusionar(base: dict, encima: dict) -> None:
	"""Pone ``encima`` sobre ``base`` de forma recursiva.

	Las secciones y claves desconocidas se CONSERVAN: pueden ser de una versión
	más nueva de la aplicación o de un complemento, y descartarlas borraría la
	configuración del usuario al abrir una versión anterior.
	"""
	for clave, valor in encima.items():
		if isinstance(valor, dict) and isinstance(base.get(clave), dict):
			_fusionar(base[clave], valor)
		else:
			base[clave] = copy.deepcopy(valor)


def _tipo_compatible(valor: Any, referencia: Any) -> bool:
	"""Comprueba que ``valor`` tiene el tipo del valor por defecto ``referencia``.

	``bool`` es subclase de ``int`` en Python: se distinguen explícitamente para
	que ``True`` no pase por un número ni ``1`` por un booleano. Un ``int`` sí vale
	donde se espera ``float`` (el JSON escribe ``1.0`` como ``1`` a veces).
	"""
	if referencia is None:
		return True
	if isinstance(referencia, bool):
		return isinstance(valor, bool)
	if isinstance(referencia, int):
		return isinstance(valor, int) and not isinstance(valor, bool)
	if isinstance(referencia, float):
		return isinstance(valor, (int, float)) and not isinstance(valor, bool)
	return isinstance(valor, type(referencia))


def validar_valor(clave: str, valor: Any, reglas: dict[str, Regla] | None = None,
		por_defecto: dict | None = None) -> None:
	"""Lanza ``ErrorValidacion`` si ``valor`` no es válido para ``clave``."""
	reglas = REGLAS if reglas is None else reglas
	por_defecto = VALORES_POR_DEFECTO if por_defecto is None else por_defecto
	seccion, _, nombre = clave.partition(".")
	referencia = por_defecto.get(seccion, {}).get(nombre, _SIN_VALOR)
	if referencia is not _SIN_VALOR and not _tipo_compatible(valor, referencia):
		raise ErrorValidacion(
			f"«{clave}» espera {type(referencia).__name__} y recibió {type(valor).__name__}."
		)
	regla = reglas.get(clave)
	if regla is None:
		return
	if regla.opciones is not None and valor not in regla.opciones:
		raise ErrorValidacion(f"«{clave}» solo admite: {', '.join(map(str, regla.opciones))}.")
	if isinstance(valor, (int, float)) and not isinstance(valor, bool):
		if regla.minimo is not None and valor < regla.minimo:
			raise ErrorValidacion(f"«{clave}» debe ser como mínimo {regla.minimo}.")
		if regla.maximo is not None and valor > regla.maximo:
			raise ErrorValidacion(f"«{clave}» debe ser como máximo {regla.maximo}.")
	if isinstance(valor, str) and regla.longitud_maxima is not None and len(valor) > regla.longitud_maxima:
		raise ErrorValidacion(f"«{clave}» admite como máximo {regla.longitud_maxima} caracteres.")


def _reemplazar(origen: Path, destino: Path, intentos: int = 5) -> None:
	"""``os.replace`` con reintentos.

	En Windows el reemplazo falla con ``PermissionError`` (WinError 5 o 32) si
	un antivirus, el indexador o una copia de seguridad tiene el destino abierto
	un instante. Se reintenta con una espera corta antes de rendirse.
	"""
	espera = 0.05
	for intento in range(1, intentos + 1):
		try:
			os.replace(origen, destino)
			return
		except PermissionError:
			if intento == intentos:
				raise
			log.debug("Reemplazo de %s bloqueado, reintento %d", destino.name, intento, exc_info=True)
			time.sleep(espera)
			espera *= 2


class _Suscriptor:
	"""Función suscrita a los cambios, con referencia débil si es un método.

	Con una referencia normal, una ventana cerrada seguiría viva en memoria (y
	recibiendo avisos sobre controles ya destruidos). Con ``WeakMethod`` se
	borra sola cuando la ventana desaparece.
	"""

	def __init__(self, funcion: Callable[[str, Any, Any], None], prefijo: str) -> None:
		self.prefijo = prefijo
		if hasattr(funcion, "__self__") and hasattr(funcion, "__func__"):
			self._ref: Callable[[], Any] = weakref.WeakMethod(funcion)
		else:
			self._ref = lambda: funcion

	def funcion(self) -> Callable[[str, Any, Any], None] | None:
		return self._ref()


# ── Clase principal ──────────────────────────────────────────────────────────

class Configuracion:
	"""Preferencias de la aplicación persistidas en JSON.

	Uso típico::

		config = obtener_configuracion()
		tamano = config.obtener("apariencia.tamano_fuente")
		config.establecer("apariencia.tamano_fuente", 14)   # guarda y avisa

	:param ruta: archivo JSON (por defecto ``data/config/settings.json``).
	:param por_defecto: valores por defecto (por defecto ``VALORES_POR_DEFECTO``).
	:param reglas: reglas de validación (por defecto ``REGLAS``).
	:param version: versión del esquema actual.
	:param migraciones: funciones de migración por versión de origen.
	"""

	def __init__(
		self,
		ruta: Path | str | None = None,
		por_defecto: dict[str, dict[str, Any]] | None = None,
		reglas: dict[str, Regla] | None = None,
		version: int = VERSION_ESQUEMA,
		migraciones: dict[int, Callable[[dict], None]] | None = None,
	) -> None:
		self.ruta = Path(ruta) if ruta is not None else ruta_por_defecto()
		self._por_defecto = copy.deepcopy(VALORES_POR_DEFECTO if por_defecto is None else por_defecto)
		self._reglas = REGLAS if reglas is None else reglas
		self._version = version
		self._migraciones = MIGRACIONES if migraciones is None else migraciones
		# Datos: RLock porque un suscriptor puede volver a leer la configuración.
		self._candado = threading.RLock()
		# Disco: separado del de datos para que obtener()/establecer() no esperen al fsync.
		self._candado_disco = threading.Lock()
		self._datos: dict[str, Any] = {}
		self._suscriptores: list[_Suscriptor] = []
		self._profundidad_lote = 0
		self._cambios_lote: list[tuple[str, Any, Any]] = []
		self._pendiente_lote = False
		self._temporizador: threading.Timer | None = None
		# No se sobrescribe un archivo que no se pudo LEER (permisos, disco): se
		# perdería la configuración real del usuario.
		self._guardado_bloqueado = False
		#: True si no existía archivo (primer arranque): útil para autodetectar idioma.
		self.primer_arranque = False
		#: Texto con lo que se recuperó al cargar (None si todo fue bien).
		self.recuperacion: str | None = None
		self.cargar()

	# ── Carga ────────────────────────────────────────────────────────────────

	def _leer_json(self, ruta: Path) -> dict:
		"""Lee y decodifica ``ruta``. Lanza ``ValueError`` si no es un objeto JSON."""
		texto = ruta.read_text(encoding="utf-8")
		datos = json.loads(texto)
		if not isinstance(datos, dict):
			raise ValueError("el archivo no contiene un objeto JSON")
		return datos

	def _apartar_corrupto(self) -> Path | None:
		"""Copia el archivo dañado a ``settings.json.corrupto-FECHA`` (se conserva)."""
		marca = datetime.now().strftime("%Y%m%d_%H%M%S")
		destino = self.ruta.with_name(f"{self.ruta.name}.corrupto-{marca}")
		try:
			shutil.copy2(self.ruta, destino)
			return destino
		except OSError:
			log.error("No se pudo apartar la configuración dañada %s", self.ruta, exc_info=True)
			return None

	def cargar(self) -> None:
		"""Carga el archivo, lo migra, lo completa y lo valida.

		Nunca lanza por un archivo dañado: la aplicación debe poder arrancar.
		"""
		with self._candado:
			self.recuperacion = None
			self._guardado_bloqueado = False
			leidos: dict | None = None
			reescribir = False
			if not self.ruta.exists():
				log.info("Sin archivo de configuración; se usan los valores por defecto (%s)", self.ruta)
				self.primer_arranque = True
				leidos = {}
				reescribir = True
			else:
				try:
					leidos = self._leer_json(self.ruta)
				except (ValueError, UnicodeDecodeError) as error:
					apartado = self._apartar_corrupto()
					log.error("Configuración dañada (%s); copia apartada en %s", error, apartado)
					leidos = self._leer_respaldo()
					if leidos is not None:
						self.recuperacion = "La configuración estaba dañada y se ha recuperado la copia anterior."
					else:
						leidos = {}
						self.recuperacion = (
							"La configuración estaba dañada y se han restablecido los valores de fábrica."
						)
					reescribir = True
				except OSError:
					log.error("No se pudo leer la configuración %s; no se sobrescribirá", self.ruta, exc_info=True)
					self._guardado_bloqueado = True
					self.recuperacion = "No se pudo leer la configuración; se usan valores de fábrica sin guardar."
					leidos = {}

			original = copy.deepcopy(leidos)
			reescribir |= self._migrar(leidos)
			datos = copy.deepcopy(self._por_defecto)
			_fusionar(datos, leidos)
			reescribir |= self._sanear(datos)
			datos[CLAVE_VERSION] = max(self._version, int(leidos.get(CLAVE_VERSION, 0) or 0))
			self._datos = datos
			# Si faltaban claves nuevas o se migró/saneó algo, se deja el archivo al día.
			if reescribir or original != datos:
				self.guardar()

	def _leer_respaldo(self) -> dict | None:
		"""Intenta ``settings.json.bak`` (último guardado correcto)."""
		respaldo = self.ruta.with_name(self.ruta.name + ".bak")
		if not respaldo.is_file():
			return None
		try:
			datos = self._leer_json(respaldo)
			log.warning("Configuración recuperada desde %s", respaldo.name)
			return datos
		except (OSError, ValueError, UnicodeDecodeError):
			log.error("La copia %s tampoco es válida", respaldo.name, exc_info=True)
			return None

	def _migrar(self, datos: dict) -> bool:
		"""Aplica las migraciones pendientes. Devuelve ``True`` si cambió algo."""
		try:
			actual = int(datos.get(CLAVE_VERSION, 1) or 1)
		except (TypeError, ValueError):
			actual = 1
		if actual > self._version:
			# Archivo escrito por una versión MÁS NUEVA: no se toca su estructura.
			log.warning(
				"La configuración es de la versión de esquema %d y esta aplicación usa la %d; "
				"se conservan las claves desconocidas.", actual, self._version,
			)
			return False
		cambiado = False
		for version in range(actual, self._version):
			migracion = self._migraciones.get(version)
			if migracion is None:
				continue
			try:
				migracion(datos)
				log.info("Configuración migrada de la versión %d a la %d", version, version + 1)
				cambiado = True
			except Exception:
				# Una migración rota no debe impedir arrancar: se registra y se sigue
				# con lo que haya; el saneado posterior repone valores por defecto.
				log.exception("Falló la migración de configuración %d → %d", version, version + 1)
		datos[CLAVE_VERSION] = self._version
		return cambiado or actual != self._version

	def _sanear(self, datos: dict) -> bool:
		"""Sustituye por su valor por defecto cada valor de tipo o rango inválido."""
		cambiado = False
		for seccion, claves in self._por_defecto.items():
			if not isinstance(datos.get(seccion), dict):
				log.warning("Sección «%s» inválida en la configuración; se restablece", seccion)
				datos[seccion] = copy.deepcopy(claves)
				cambiado = True
				continue
			for nombre, defecto in claves.items():
				clave = f"{seccion}.{nombre}"
				try:
					validar_valor(clave, datos[seccion].get(nombre), self._reglas, self._por_defecto)
				except ErrorValidacion as error:
					log.warning("Valor no válido en la configuración (%s); se usa el de fábrica", error)
					datos[seccion][nombre] = copy.deepcopy(defecto)
					cambiado = True
		return cambiado

	# ── Lectura y escritura de valores ───────────────────────────────────────

	def obtener(self, clave: str, por_defecto: Any = _SIN_VALOR) -> Any:
		"""Devuelve el valor de «seccion.clave».

		Devuelve una COPIA: modificar una lista devuelta no altera la
		configuración sin pasar por ``establecer`` (ni se salta los avisos).

		:raises KeyError: si la clave no existe y no se da ``por_defecto``.
		"""
		with self._candado:
			actual: Any = self._datos
			for parte in clave.split("."):
				if not isinstance(actual, dict) or parte not in actual:
					if por_defecto is _SIN_VALOR:
						raise KeyError(clave)
					return por_defecto
				actual = actual[parte]
			return copy.deepcopy(actual)

	def valor_por_defecto(self, clave: str) -> Any:
		"""Valor de fábrica de «seccion.clave» (``KeyError`` si no existe)."""
		seccion, _, nombre = clave.partition(".")
		return copy.deepcopy(self._por_defecto[seccion][nombre])

	def claves(self) -> list[str]:
		"""Todas las claves «seccion.clave» conocidas (las de fábrica)."""
		return [f"{s}.{c}" for s, claves in self._por_defecto.items() for c in claves]

	def regla(self, clave: str) -> Regla | None:
		"""Regla de validación de la clave (``None`` si solo se comprueba el tipo)."""
		return self._reglas.get(clave)

	def validar(self, clave: str, valor: Any) -> None:
		"""Lanza ``ErrorValidacion`` si ``valor`` no sirve para ``clave`` (no cambia nada)."""
		validar_valor(clave, valor, self._reglas, self._por_defecto)

	def establecer(self, clave: str, valor: Any, *, guardar: bool = True) -> bool:
		"""Cambia «seccion.clave», guarda (si ``guardar``) y avisa a los suscriptores.

		:return: ``True`` si el valor cambió.
		:raises ErrorValidacion: si el valor no cumple su tipo o su regla.
		"""
		return self.establecer_varias({clave: valor}, guardar=guardar)

	def establecer_varias(self, cambios: dict[str, Any], *, guardar: bool = True) -> bool:
		"""Cambia varias claves con UN solo guardado. Valida todas antes de tocar nada."""
		for clave, valor in cambios.items():
			validar_valor(clave, valor, self._reglas, self._por_defecto)
		avisos: list[tuple[str, Any, Any]] = []
		with self._candado:
			for clave, valor in cambios.items():
				seccion, punto, nombre = clave.partition(".")
				if not punto or not seccion or not nombre:
					raise KeyError(f"Clave mal formada: «{clave}» (se espera «seccion.clave»)")
				contenedor = self._datos.setdefault(seccion, {})
				anterior = copy.deepcopy(contenedor.get(nombre, None))
				if nombre in contenedor and contenedor[nombre] == valor and type(contenedor[nombre]) is type(valor):
					continue
				contenedor[nombre] = copy.deepcopy(valor)
				avisos.append((clave, anterior, copy.deepcopy(valor)))
			if not avisos:
				return False
			if self._profundidad_lote > 0:
				self._cambios_lote.extend(avisos)
				self._pendiente_lote = self._pendiente_lote or guardar
				return True
		if guardar:
			self.guardar()
		self._notificar(avisos)
		return True

	def establecer_diferido(self, clave: str, valor: Any, retraso: float = 0.8) -> bool:
		"""Como ``establecer``, pero agrupa el guardado ``retraso`` segundos.

		Para controles que cambian muchas veces por segundo (volumen, velocidad):
		cada pulsación de flecha no debe escribir el archivo con ``fsync`` en el
		hilo de la interfaz. Llama a ``guardar_pendiente()`` al cerrar.
		"""
		cambiado = self.establecer(clave, valor, guardar=False)
		if cambiado:
			with self._candado:
				if self._temporizador is not None:
					self._temporizador.cancel()
				self._temporizador = threading.Timer(max(0.05, retraso), self._al_vencer_temporizador)
				self._temporizador.daemon = True
				self._temporizador.name = "config-guardado-diferido"
				self._temporizador.start()
		return cambiado

	def _al_vencer_temporizador(self) -> None:
		with self._candado:
			self._temporizador = None
		try:
			self.guardar()
		except Exception:
			# En un hilo de temporizador nadie recogería la excepción: se registra.
			log.exception("Falló el guardado diferido de la configuración")

	def guardar_pendiente(self) -> None:
		"""Guarda YA si hay un guardado diferido en espera (al cerrar la aplicación)."""
		with self._candado:
			temporizador, self._temporizador = self._temporizador, None
		if temporizador is not None:
			temporizador.cancel()
			self.guardar()

	@contextmanager
	def lote(self) -> Iterator[Configuracion]:
		"""Agrupa varios ``establecer`` en un solo guardado y un solo reparto de avisos.

		Si el bloque lanza una excepción, lo ya cambiado se queda en memoria y se
		guarda igualmente (los cambios eran válidos uno a uno).
		"""
		with self._candado:
			self._profundidad_lote += 1
		try:
			yield self
		finally:
			with self._candado:
				self._profundidad_lote -= 1
				terminado = self._profundidad_lote == 0
				avisos, guardar = [], False
				if terminado:
					avisos, self._cambios_lote = self._cambios_lote, []
					guardar, self._pendiente_lote = self._pendiente_lote, False
			if terminado:
				if guardar:
					self.guardar()
				self._notificar(avisos)

	def restablecer(self, seccion: str | None = None, *, guardar: bool = True) -> None:
		"""Vuelve a los valores de fábrica (todo o una sección) y avisa."""
		with self.lote():
			secciones = [seccion] if seccion else list(self._por_defecto)
			cambios = {
				f"{s}.{c}": copy.deepcopy(v) for s in secciones for c, v in self._por_defecto[s].items()
			}
			self.establecer_varias(cambios, guardar=guardar)
		log.info("Configuración restablecida a valores de fábrica: %s", seccion or "todas las secciones")

	# ── Guardado ─────────────────────────────────────────────────────────────

	def guardar(self) -> None:
		"""Escribe el archivo de forma atómica y deja una copia ``.bak`` del anterior.

		:raises ErrorConfiguracion: si no se pudo escribir (disco lleno, permisos).
		"""
		with self._candado:
			if self._guardado_bloqueado:
				log.warning("Guardado omitido: el archivo original no se pudo leer y no se sobrescribe")
				return
			texto = json.dumps(self._datos, ensure_ascii=False, indent=2, sort_keys=False)
		with self._candado_disco:
			carpeta = self.ruta.parent
			# Temporal ÚNICO (proceso + hilo) en la MISMA carpeta: os.replace solo es
			# atómico dentro del mismo volumen, y dos guardados no comparten temporal.
			temporal = carpeta / f"{self.ruta.name}.{os.getpid()}-{threading.get_ident()}.tmp"
			try:
				carpeta.mkdir(parents=True, exist_ok=True)
				with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
					archivo.write(texto)
					archivo.flush()
					os.fsync(archivo.fileno())
				if self.ruta.exists():
					try:
						shutil.copy2(self.ruta, self.ruta.with_name(self.ruta.name + ".bak"))
					except OSError:
						# Sin .bak se pierde una red de seguridad, no el guardado.
						log.warning("No se pudo actualizar la copia .bak de la configuración", exc_info=True)
				_reemplazar(temporal, self.ruta)
				log.debug("Configuración guardada en %s", self.ruta)
			except OSError as error:
				try:
					temporal.unlink(missing_ok=True)
				except OSError:
					log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
				log.error("No se pudo guardar la configuración en %s", self.ruta, exc_info=True)
				raise ErrorConfiguracion(f"No se pudo guardar la configuración: {error}") from error

	# ── Suscriptores ─────────────────────────────────────────────────────────

	def suscribir(self, funcion: Callable[[str, Any, Any], None], prefijo: str = "") -> Callable[[], None]:
		"""Avisa a ``funcion(clave, anterior, nuevo)`` de cada cambio.

		El aviso llega en el hilo que hizo el cambio. Si ``funcion`` toca
		controles wx, debe pasar por ``wx.CallAfter``.

		:param prefijo: solo claves que empiecen así (``"apariencia."``).
		:return: función que cancela la suscripción.
		"""
		suscriptor = _Suscriptor(funcion, prefijo)
		with self._candado:
			self._suscriptores.append(suscriptor)

		def cancelar() -> None:
			with self._candado:
				if suscriptor in self._suscriptores:
					self._suscriptores.remove(suscriptor)

		return cancelar

	def _notificar(self, avisos: list[tuple[str, Any, Any]]) -> None:
		"""Reparte los avisos FUERA del candado (un suscriptor lento no bloquea a nadie)."""
		if not avisos:
			return
		with self._candado:
			vivos = [s for s in self._suscriptores if s.funcion() is not None]
			self._suscriptores = vivos
			suscriptores = list(vivos)
		for clave, anterior, nuevo in avisos:
			for suscriptor in suscriptores:
				if not clave.startswith(suscriptor.prefijo):
					continue
				funcion = suscriptor.funcion()
				if funcion is None:
					continue
				try:
					funcion(clave, anterior, nuevo)
				except Exception:
					# Un suscriptor roto no debe impedir que los demás se enteren.
					log.exception("Falló un suscriptor de la configuración al cambiar «%s»", clave)


# ── Instancia compartida ─────────────────────────────────────────────────────

_instancia: Configuracion | None = None
_candado_instancia = threading.Lock()


def obtener_configuracion() -> Configuracion:
	"""Instancia única, creada la primera vez que se pide (no al importar).

	Crearla al importar escribiría en ``data`` durante las pruebas o al
	importar el módulo desde herramientas.
	"""
	global _instancia
	with _candado_instancia:
		if _instancia is None:
			_instancia = Configuracion()
		return _instancia
