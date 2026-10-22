"""Actualización automática y segura de la aplicación portable (compilada con cx_Freeze).

Flujo completo (el porqué de cada paso está en ``referencias/flujo-de-actualizacion.md``
del agente «actualizador»):

1. :func:`comprobar` descarga ``version.json`` con tiempo límite, VERIFICA su firma
   Ed25519 con la clave pública incrustada en este módulo, compara versiones y aplica
   la protección anti-rollback (nunca se acepta una versión menor que la última
   instalada y confirmada en este equipo).
2. :func:`descargar` baja el ZIP a ``data/temp/actualizacion/descarga`` (reanudable),
   sin pasar nunca del tamaño anunciado en el manifiesto firmado.
3. :func:`verificar_paquete` comprueba tamaño y SHA-256.
4. :func:`extraer_seguro` rechaza rutas absolutas, ``..``, enlaces, nombres
   reservados de Windows y paquetes demasiado grandes; separa el programa de los
   recursos de ``data`` y DESCARTA cualquier dato de usuario que viniera en el ZIP.
5. :func:`preparar_sustitucion` genera el ``.bat`` desde la plantilla de este módulo.
6. :func:`lanzar_sustitucion` lo lanza desacoplado y la interfaz cierra la aplicación.
7. El ``.bat`` espera al cierre, hace copia de seguridad (sin ``data``), copia la
   versión nueva (sin tocar los datos del usuario), la relanza con
   ``--tras-actualizar`` y espera el marcador de salud. Si no llega, vuelve atrás.
8. La versión nueva llama a :func:`confirmar_arranque` unos segundos después de
   abrir su ventana: escribe el marcador y anota la versión (anti-rollback).

Por qué un ``.bat`` y no un ``.py``: la aplicación compilada con cx_Freeze no trae un
``python.exe`` utilizable (el intérprete va dentro del propio ejecutable, que es
justo el archivo que hay que sustituir), mientras que ``cmd.exe``, ``robocopy``,
``tasklist`` y ``powershell`` están en todos los Windows admitidos. El script vive
en ``data/temp`` para no sobrescribirse a sí mismo durante la copia.

Solo biblioteca estándar, salvo ``cryptography`` para verificar la firma (si falta,
se rechaza la actualización: la verificación nunca «se abre» ante un fallo).

PLANTILLA del agente «actualizador».
"""

from __future__ import annotations

import base64
import binascii
import builtins
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	"""Traduce con el ``_`` que gettext instala en ``builtins``, si existe.

	Se busca en cada llamada (y no al importar) porque el módulo puede importarse
	antes de que la aplicación instale su traducción.
	"""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


# ── CONFIGURACIÓN (ajústela en cada proyecto) ────────────────────────────────
NOMBRE_APP = "MiAplicacion"
EJECUTABLE = "MiAplicacion.exe"
# Dirección del manifiesto firmado. Siempre https (http solo se admite en localhost
# para las pruebas).
URL_MANIFIESTO = "https://ejemplo.invalid/miaplicacion/version.json"
# Claves PÚBLICAS Ed25519 en hexadecimal (64 caracteres). Se acepta la firma de
# CUALQUIERA de ellas: así se puede rotar la clave publicando una versión que ya
# conozca la nueva antes de retirar la antigua. Vacía = se rechaza todo.
CLAVES_PUBLICAS_ED25519: tuple[str, ...] = ()
# Subcarpetas de data que son RECURSOS del programa y que una versión nueva puede
# refrescar (añadir o reemplazar archivos, nunca borrar). Todo lo demás de data
# (config, db, logs, cache, sessions, backups...) es del usuario y no se toca.
# Déjela vacía para que la actualización no escriba nada en data.
DATA_ACTUALIZABLE: tuple[str, ...] = ("bin", "sounds", "locales", "docs", "icons")
# ─────────────────────────────────────────────────────────────────────────────

ARGUMENTO_TRAS_ACTUALIZAR = "--tras-actualizar"
CAMPO_FIRMA = "firma_ed25519"
# Segundos que la versión nueva tiene para escribir el marcador de salud.
ESPERA_SALUD = 60
# Segundos que el script espera a que la aplicación se cierre. Si no se cierra
# (el usuario canceló el cierre para guardar algo), la actualización se CANCELA:
# nunca se mata una aplicación que puede tener trabajo sin guardar.
ESPERA_CIERRE = 90
# Segundos tras mostrar la ventana principal antes de confirmar el arranque.
RETRASO_CONFIRMACION = 5
TIEMPO_MANIFIESTO = 15
TIEMPO_DESCARGA = 30
MAX_MANIFIESTO = 256 * 1024
MAX_DESCARGA = 2 * 1024**3
MAX_EXPANDIDO = 4 * 1024**3
MAX_MIEMBROS = 50_000
_BLOQUE = 64 * 1024
_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1"}
_NOMBRES_RESERVADOS = (
	{"con", "prn", "aux", "nul"} | {f"com{n}" for n in range(1, 10)} | {f"lpt{n}" for n in range(1, 10)}
)

Progreso = Callable[[str, int, int], None]
"""``progreso(fase, hecho, total)``; fases: descarga, verificacion, extraccion, preparacion."""


# ════════════════════════════════════════════════════════════════════════════
# Errores: el mensaje de cada uno está pensado para leérselo al usuario
# ════════════════════════════════════════════════════════════════════════════


class ErrorActualizacion(Exception):
	"""Error de actualización con un mensaje claro para el usuario."""


class ErrorRed(ErrorActualizacion):
	"""No se pudo hablar con el servidor o la descarga se interrumpió."""


class ErrorFirma(ErrorActualizacion):
	"""El manifiesto no está firmado o la firma no es válida."""


class ErrorVerificacion(ErrorActualizacion):
	"""El paquete descargado no coincide con el manifiesto (tamaño o SHA-256)."""


class ErrorPaquete(ErrorActualizacion):
	"""El ZIP es peligroso, inválido o no parece de esta aplicación."""


class ErrorRollback(ErrorActualizacion):
	"""El servidor ofrece una versión inferior a la última confirmada en este equipo."""


class ErrorCancelado(ErrorActualizacion):
	"""El usuario canceló la descarga (la parte descargada se conserva)."""


class VersionInvalida(ValueError):
	"""La cadena no es una versión reconocible."""


# ════════════════════════════════════════════════════════════════════════════
# Configuración y datos
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class Configuracion:
	"""Todo lo que el actualizador necesita saber de la aplicación.

	Se pasa explícitamente (en lugar de leer variables globales) para que las
	pruebas trabajen sobre instalaciones falsas en carpetas temporales.
	"""

	version_actual: str
	carpeta_app: Path
	carpeta_datos: Path | None = None
	nombre_app: str = NOMBRE_APP
	ejecutable: str = EJECUTABLE
	url_manifiesto: str = URL_MANIFIESTO
	claves_publicas: tuple[str, ...] = CLAVES_PUBLICAS_ED25519
	comando: list[str] | None = None
	data_actualizable: tuple[str, ...] = DATA_ACTUALIZABLE
	espera_salud: int = ESPERA_SALUD
	espera_cierre: int = ESPERA_CIERRE
	pid_a_esperar: int | None = None
	max_expandido: int = MAX_EXPANDIDO
	permitir_desde_codigo: bool = False
	extra: dict = field(default_factory=dict)

	def __post_init__(self) -> None:
		self.carpeta_app = Path(self.carpeta_app).resolve()
		self.carpeta_datos = Path(self.carpeta_datos).resolve() if self.carpeta_datos else self.carpeta_app / "data"
		if self.comando is None:
			self.comando = _comando_por_defecto()

	@property
	def carpeta_trabajo(self) -> Path:
		return self.carpeta_datos / "temp" / "actualizacion"

	@property
	def carpeta_respaldo(self) -> Path:
		return self.carpeta_datos / "temp" / "version_anterior"

	@property
	def archivo_estado(self) -> Path:
		return self.carpeta_datos / "config" / "actualizaciones.json"

	@property
	def marcador_salud(self) -> Path:
		return self.carpeta_trabajo / "salud.txt"

	@property
	def archivo_resultado(self) -> Path:
		return self.carpeta_trabajo / "resultado.txt"

	@property
	def registro_script(self) -> Path:
		return self.carpeta_datos / "logs" / "actualizacion.log"


def _comando_por_defecto() -> list[str]:
	"""Orden para relanzar la aplicación: el .exe compilado o ``python main.py``."""
	if getattr(sys, "frozen", False):
		return [sys.executable]
	return [sys.executable, str(Path(sys.argv[0]).resolve())]


def configuracion_por_defecto(version_actual: str) -> Configuracion:
	"""Configuración de la aplicación en ejecución (portable: todo junto al programa).

	Respeta ``APP_DATA_DIR`` (la usan las pruebas y los perfiles aparte).
	"""
	if getattr(sys, "frozen", False):
		base = Path(sys.executable).resolve().parent
	else:
		base = Path(__file__).resolve().parent.parent.parent
	redireccion = os.environ.get("APP_DATA_DIR", "").strip()
	datos = Path(redireccion).expanduser() if redireccion else None
	return Configuracion(version_actual=version_actual, carpeta_app=base, carpeta_datos=datos)


@dataclass(frozen=True)
class InfoActualizacion:
	"""Versión disponible, tal como la describe el manifiesto firmado."""

	version: str
	archivo: str
	url_zip: str
	sha256: str
	tamano: int
	fecha: str = ""
	novedades: str = ""


def puede_instalar(config: Configuracion) -> bool:
	"""La sustitución solo se hace en la versión compilada.

	Desde código fuente se puede comprobar y avisar, pero sustituir los archivos
	del proyecto del desarrollador con un ZIP compilado sería destructivo.
	"""
	return bool(getattr(sys, "frozen", False)) or config.permitir_desde_codigo


# ════════════════════════════════════════════════════════════════════════════
# Versiones (semver sencillo + PEP 440 básico, sin dependencias)
# ════════════════════════════════════════════════════════════════════════════

_PATRON_VERSION = re.compile(
	r"""
	^\s*v?
	(?P<release>\d+(?:\.\d+)*)
	(?:[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>\d+)?)?
	(?:(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n>\d+)?)|(?:-(?P<post_implicito>\d+)))?
	(?:[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>\d+)?)?
	(?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
	\s*$
	""",
	re.VERBOSE | re.IGNORECASE,
)
_RANGO_PRE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}


def clave_version(texto: str) -> tuple:
	"""Convierte una versión en una tupla ordenable según PEP 440.

	Orden: ``1.0.dev1 < 1.0a1 < 1.0b1 < 1.0rc1 < 1.0 == 1.0.0 < 1.0.post1``.
	Admite el semver habitual (``1.2.3-beta.1``). La parte local (``+abc``) no
	cuenta para el orden. ``1.0-1`` es una posterior (post), como en PEP 440.
	"""
	coincidencia = _PATRON_VERSION.match(str(texto or ""))
	if not coincidencia:
		raise VersionInvalida(f"Versión no válida: {texto!r}")
	release = tuple(int(parte) for parte in coincidencia["release"].split("."))
	while len(release) > 1 and release[-1] == 0:
		release = release[:-1]
	pre_l = coincidencia["pre_l"]
	post_presente = bool(coincidencia["post_l"] or coincidencia["post_implicito"])
	dev_presente = bool(coincidencia["dev_l"])
	if pre_l:
		pre = (0, _RANGO_PRE[pre_l.lower()], int(coincidencia["pre_n"] or 0))
	elif dev_presente and not post_presente:
		pre = (-1, 0, 0)  # 1.0.dev1 va antes que cualquier pre-versión de 1.0
	else:
		pre = (1, 0, 0)
	if post_presente:
		post = (1, int(coincidencia["post_n"] or coincidencia["post_implicito"] or 0))
	else:
		post = (0, 0)
	dev = (0, int(coincidencia["dev_n"] or 0)) if dev_presente else (1, 0)
	return (release, pre, post, dev)


def comparar_versiones(a: str, b: str) -> int:
	"""Devuelve -1, 0 o 1 según ``a`` sea menor, igual o mayor que ``b``."""
	clave_a, clave_b = clave_version(a), clave_version(b)
	return (clave_a > clave_b) - (clave_a < clave_b)


def es_mas_nueva(a: str, b: str) -> bool:
	"""Indica si ``a`` es estrictamente más nueva que ``b``."""
	return comparar_versiones(a, b) > 0


# ════════════════════════════════════════════════════════════════════════════
# Firma
# ════════════════════════════════════════════════════════════════════════════


def serializar_canonico(manifiesto: dict) -> bytes:
	"""Bytes exactos que se firman: JSON ordenado, separadores fijos, sin la firma.

	Debe ser IDÉNTICA a la de ``tools/firmar_version.py`` (una prueba lo
	comprueba). Firmar el manifiesto completo, y no solo «versión + sha», impide
	que alguien cambie las novedades, la URL o el tamaño sin invalidar la firma.
	"""
	datos = {clave: valor for clave, valor in manifiesto.items() if clave != CAMPO_FIRMA}
	return json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def verificar_firma(manifiesto: dict, claves_publicas: tuple[str, ...] | list[str]) -> None:
	"""Comprueba la firma Ed25519 del manifiesto con alguna de las claves públicas.

	:raises ErrorFirma: sin claves, sin firma, firma mal formada o no válida.
	"""
	if not claves_publicas:
		raise ErrorFirma(
			_(
				"La aplicación no tiene configurada ninguna clave de verificación; por seguridad no se aceptan actualizaciones."
			)
		)
	firma_b64 = manifiesto.get(CAMPO_FIRMA)
	if not isinstance(firma_b64, str) or not firma_b64.strip():
		raise ErrorFirma(_("La información de la versión nueva no está firmada; no se instalará."))
	try:
		firma = base64.b64decode(firma_b64, validate=True)
	except (binascii.Error, ValueError) as error:
		raise ErrorFirma(_("La firma de la versión nueva está dañada; no se instalará.")) from error
	try:
		from cryptography.exceptions import InvalidSignature
		from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
	except ImportError as error:
		log.exception("Falta el paquete cryptography: no se puede verificar la firma")
		raise ErrorFirma(
			_("Falta el componente que verifica las firmas; no se puede actualizar de forma segura.")
		) from error
	mensaje = serializar_canonico(manifiesto)
	for clave_hex in claves_publicas:
		try:
			clave = Ed25519PublicKey.from_public_bytes(bytes.fromhex(clave_hex))
		except ValueError:
			log.error("Clave pública mal formada en la configuración: %s…", str(clave_hex)[:16])
			continue
		try:
			clave.verify(firma, mensaje)
		except InvalidSignature:
			log.debug("La firma no corresponde a la clave %s…", clave_hex[:16])
			continue
		log.info("Firma del manifiesto verificada con la clave %s…", clave_hex[:16])
		return
	raise ErrorFirma(_("La firma de la versión nueva no es válida; no se instalará."))


# ════════════════════════════════════════════════════════════════════════════
# Estado persistente (data/config/actualizaciones.json)
# ════════════════════════════════════════════════════════════════════════════


def leer_estado(config: Configuracion) -> dict:
	"""Lee la memoria del actualizador; si falta o está dañada, empieza vacía."""
	try:
		datos = json.loads(config.archivo_estado.read_text(encoding="utf-8"))
		return datos if isinstance(datos, dict) else {}
	except FileNotFoundError:
		return {}
	except (OSError, ValueError):
		# Dañada: se sigue sin memoria (la regla «más nueva que la instalada»
		# sigue protegiendo) en lugar de bloquear las actualizaciones para siempre.
		log.warning("No se pudo leer %s; se empieza con el estado vacío", config.archivo_estado, exc_info=True)
		return {}


def guardar_estado(config: Configuracion, estado: dict) -> None:
	"""Guarda el estado de forma atómica (temporal + ``os.replace``)."""
	ruta = config.archivo_estado
	ruta.parent.mkdir(parents=True, exist_ok=True)
	temporal = ruta.with_name(ruta.name + ".tmp")
	temporal.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
	os.replace(temporal, ruta)


def _actualizar_estado(config: Configuracion, **cambios: str) -> None:
	try:
		estado = leer_estado(config)
		estado.update(cambios)
		guardar_estado(config, estado)
	except OSError:
		log.warning("No se pudo guardar el estado del actualizador", exc_info=True)


def omitir_version(config: Configuracion, version: str) -> None:
	"""Recuerda que el usuario no quiere que se le ofrezca esta versión."""
	log.info("El usuario omite la versión %s", version)
	_actualizar_estado(config, version_omitida=version)


def confirmar_version(config: Configuracion, version: str | None = None) -> None:
	"""Anota la versión como instalada y confirmada (memoria anti-rollback).

	Solo SUBE: guarda la mayor entre la anotada y la actual. Se alimenta únicamente
	de versiones que han arrancado de verdad en este equipo, nunca de lo que diga
	el servidor; así una errata publicada en ``version.json`` no puede bloquear las
	actualizaciones para siempre.
	"""
	version = version or config.version_actual
	estado = leer_estado(config)
	anterior = str(estado.get("version_confirmada") or "")
	try:
		if anterior and comparar_versiones(anterior, version) >= 0:
			return
	except VersionInvalida:
		log.warning("Versión confirmada no válida en el estado (%r); se sustituye", anterior)
	estado["version_confirmada"] = version
	try:
		guardar_estado(config, estado)
		log.info("Versión %s confirmada (anti-rollback)", version)
	except OSError:
		log.warning("No se pudo anotar la versión confirmada", exc_info=True)


# ════════════════════════════════════════════════════════════════════════════
# Red
# ════════════════════════════════════════════════════════════════════════════


def _comprobar_url(url: str) -> None:
	"""Solo https; http únicamente contra la propia máquina (pruebas)."""
	partes = urllib.parse.urlsplit(url)
	if partes.scheme == "https":
		return
	if partes.scheme == "http" and (partes.hostname or "") in _HOSTS_LOCALES:
		return
	raise ErrorRed(_("Dirección de actualización no segura: solo se admite https."))


def _abrir(config: Configuracion, url: str, tiempo: float, cabeceras: dict | None = None):
	_comprobar_url(url)
	todas = {"User-Agent": f"{config.nombre_app}-Actualizador/{config.version_actual}", "Cache-Control": "no-cache"}
	todas.update(cabeceras or {})
	peticion = urllib.request.Request(url, headers=todas)
	try:
		respuesta = urllib.request.urlopen(peticion, timeout=tiempo)  # noqa: S310 (esquema comprobado)
	except urllib.error.HTTPError as error:
		log.warning("El servidor respondió %s a %s", error.code, url)
		raise
	except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
		log.warning("No se pudo conectar con %s: %s", url, error)
		raise ErrorRed(
			_("No se pudo conectar con el servidor de actualizaciones. Compruebe la conexión a Internet.")
		) from error
	try:
		_comprobar_url(respuesta.geturl())  # una redirección no puede bajar a http
	except ErrorRed:
		respuesta.close()
		raise
	return respuesta


def _leer_manifiesto(config: Configuracion, url: str) -> dict:
	try:
		with _abrir(config, url, TIEMPO_MANIFIESTO) as respuesta:
			datos = respuesta.read(MAX_MANIFIESTO + 1)
	except urllib.error.HTTPError as error:
		raise ErrorRed(_("El servidor de actualizaciones respondió con el error %s.") % error.code) from error
	except (http.client.HTTPException, TimeoutError, OSError) as error:
		raise ErrorRed(_("El servidor de actualizaciones no respondió a tiempo.")) from error
	if len(datos) > MAX_MANIFIESTO:
		raise ErrorFirma(_("La información de versión del servidor es demasiado grande; se ignora."))
	try:
		manifiesto = json.loads(datos.decode("utf-8"))
	except (UnicodeDecodeError, ValueError) as error:
		raise ErrorFirma(_("La información de versión del servidor está dañada.")) from error
	if not isinstance(manifiesto, dict):
		raise ErrorFirma(_("La información de versión del servidor está dañada."))
	return manifiesto


def _info_desde_manifiesto(config: Configuracion, manifiesto: dict, url_manifiesto: str) -> InfoActualizacion:
	"""Valida los campos (ya con la firma comprobada) y construye la información."""
	version = manifiesto.get("version")
	archivo = manifiesto.get("file")
	sha = str(manifiesto.get("sha256") or "").strip().lower()
	tamano = manifiesto.get("size")
	aplicacion = manifiesto.get("app")
	if aplicacion is not None and aplicacion != config.nombre_app:
		raise ErrorFirma(_("La versión publicada es de otra aplicación; se ignora."))
	try:
		clave_version(str(version))
	except VersionInvalida as error:
		raise ErrorFirma(_("El servidor publica una versión con formato no válido.")) from error
	if not isinstance(archivo, str) or not re.fullmatch(r"[\w.\-]+\.zip", archivo) or ".." in archivo:
		raise ErrorFirma(_("El nombre del paquete publicado no es válido."))
	if not re.fullmatch(r"[0-9a-f]{64}", sha):
		raise ErrorFirma(_("El paquete publicado no trae una huella SHA-256 válida."))
	if not isinstance(tamano, int) or isinstance(tamano, bool) or not 0 < tamano <= MAX_DESCARGA:
		raise ErrorFirma(_("El tamaño del paquete publicado no es válido."))
	url_zip = manifiesto.get("url") or urllib.parse.urljoin(url_manifiesto, archivo)
	_comprobar_url(str(url_zip))
	return InfoActualizacion(
		version=str(version),
		archivo=archivo,
		url_zip=str(url_zip),
		sha256=sha,
		tamano=tamano,
		fecha=str(manifiesto.get("date") or ""),
		novedades=str(manifiesto.get("novedades") or "").strip(),
	)


def comprobar(
	config: Configuracion, url_manifiesto: str | None = None, *, respetar_omitida: bool = False
) -> InfoActualizacion | None:
	"""Consulta el servidor y devuelve la versión nueva, o ``None`` si no la hay.

	:param respetar_omitida: ``True`` en la comprobación periódica (no molesta con la
		versión que el usuario pidió omitir); ``False`` en la manual.
	:raises ErrorActualizacion: red, firma, formato o anti-rollback. La firma se
		verifica ANTES de mirar ningún otro campo.
	"""
	url = url_manifiesto or config.url_manifiesto
	log.info("Comprobando actualizaciones en %s (versión instalada %s)", url, config.version_actual)
	manifiesto = _leer_manifiesto(config, url)
	verificar_firma(manifiesto, config.claves_publicas)
	info = _info_desde_manifiesto(config, manifiesto, url)
	_actualizar_estado(config, ultima_comprobacion=datetime.now(timezone.utc).isoformat(timespec="seconds"))
	if not es_mas_nueva(info.version, config.version_actual):
		log.info("No hay versión nueva: el servidor ofrece %s", info.version)
		return None
	estado = leer_estado(config)
	confirmada = str(estado.get("version_confirmada") or "")
	try:
		if confirmada and comparar_versiones(info.version, confirmada) < 0:
			log.warning("Anti-rollback: el servidor ofrece %s, inferior a la confirmada %s", info.version, confirmada)
			raise ErrorRollback(
				_(
					"El servidor ofrece la versión %(nueva)s, anterior a la %(confirmada)s que ya se instaló en este equipo. Por seguridad se rechaza."
				)
				% {"nueva": info.version, "confirmada": confirmada}
			)
	except VersionInvalida:
		log.warning("Versión confirmada no válida en el estado (%r); se ignora", confirmada)
	if respetar_omitida and estado.get("version_omitida") == info.version:
		log.info("La versión %s está omitida por el usuario", info.version)
		return None
	log.info("Versión nueva disponible: %s (%d bytes)", info.version, info.tamano)
	return info


# ════════════════════════════════════════════════════════════════════════════
# Descarga y verificación
# ════════════════════════════════════════════════════════════════════════════


def _avisar(progreso: Progreso | None, fase: str, hecho: int, total: int) -> None:
	if progreso is None:
		return
	try:
		progreso(fase, hecho, total)
	except Exception:  # noqa: BLE001 (función ajena: cualquier fallo se registra y se sigue)
		# Un fallo al pintar el progreso no debe estropear la actualización.
		log.debug("Fallo en la función de progreso", exc_info=True)


def sha256_de(ruta: Path) -> str:
	"""SHA-256 de un archivo leyendo por bloques de 1 MB."""
	resumen = hashlib.sha256()
	with ruta.open("rb") as archivo:
		for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
			resumen.update(bloque)
	return resumen.hexdigest()


def descargar(
	config: Configuracion,
	info: InfoActualizacion,
	progreso: Progreso | None = None,
	cancelado: Callable[[], bool] | None = None,
	*,
	reanudar: bool = True,
) -> Path:
	"""Descarga el ZIP a ``data/temp/actualizacion/descarga`` y devuelve su ruta.

	La parte ya descargada se guarda como ``.parcial`` y, si el servidor admite
	``Range``, se reanuda desde ahí. Nunca se escriben más bytes de los anunciados
	en el manifiesto firmado (defensa ante un servidor que envíe un flujo infinito).
	"""
	carpeta = config.carpeta_trabajo / "descarga"
	carpeta.mkdir(parents=True, exist_ok=True)
	final = carpeta / info.archivo
	parcial = final.with_name(final.name + ".parcial")
	if final.exists():
		final.unlink()
	inicio = parcial.stat().st_size if (reanudar and parcial.is_file()) else 0
	if inicio > info.tamano:
		parcial.unlink()
		inicio = 0
	if inicio < info.tamano:
		cabeceras = {"Range": f"bytes={inicio}-"} if inicio else {}
		try:
			respuesta = _abrir(config, info.url_zip, TIEMPO_DESCARGA, cabeceras)
		except urllib.error.HTTPError as error:
			if error.code == 416 and inicio:
				log.info("El servidor no acepta reanudar desde %d; se empieza de cero", inicio)
				parcial.unlink(missing_ok=True)
				return descargar(config, info, progreso, cancelado, reanudar=False)
			raise ErrorRed(
				_("El servidor respondió con el error %s al descargar la actualización.") % error.code
			) from error
		with respuesta:
			reanudada = (
				bool(inicio)
				and respuesta.status == 206
				and str(respuesta.headers.get("Content-Range", "")).startswith(f"bytes {inicio}-")
			)
			if inicio and not reanudada:
				log.info("El servidor no admite reanudar; se descarga completo")
				inicio = 0
			hecho = inicio
			log.info("Descargando %s desde el byte %d de %d", info.url_zip, inicio, info.tamano)
			with parcial.open("ab" if reanudada else "wb") as salida:
				while True:
					if cancelado is not None and cancelado():
						raise ErrorCancelado(_("Descarga cancelada. Podrá continuarla más tarde."))
					try:
						bloque = respuesta.read(_BLOQUE)
					except (http.client.HTTPException, TimeoutError, OSError) as error:
						# IncompleteRead (conexión cortada) es HTTPException, no OSError.
						raise ErrorRed(
							_("La descarga se interrumpió. Vuelva a intentarlo: continuará donde se quedó.")
						) from error
					if not bloque:
						break
					hecho += len(bloque)
					if hecho > info.tamano:
						salida.close()
						parcial.unlink(missing_ok=True)
						raise ErrorVerificacion(
							_("El paquete descargado es mayor de lo anunciado; se descarta por seguridad.")
						)
					salida.write(bloque)
					_avisar(progreso, "descarga", hecho, info.tamano)
		if hecho < info.tamano:
			raise ErrorRed(_("La descarga se interrumpió. Vuelva a intentarlo: continuará donde se quedó."))
	os.replace(parcial, final)
	return final


def verificar_paquete(ruta: Path, info: InfoActualizacion) -> None:
	"""Comprueba tamaño y SHA-256; si no coinciden, BORRA el archivo y lo rechaza."""
	tamano = ruta.stat().st_size
	if tamano != info.tamano:
		ruta.unlink(missing_ok=True)
		raise ErrorVerificacion(_("El paquete descargado no tiene el tamaño anunciado; no se instalará."))
	real = sha256_de(ruta)
	if not hmac.compare_digest(real, info.sha256):
		log.error("SHA-256 incorrecto: esperado %s, obtenido %s", info.sha256, real)
		ruta.unlink(missing_ok=True)
		raise ErrorVerificacion(_("El paquete descargado no supera la verificación de integridad; no se instalará."))
	log.info("Paquete verificado: %d bytes, SHA-256 correcto", tamano)


# ════════════════════════════════════════════════════════════════════════════
# Extracción segura
# ════════════════════════════════════════════════════════════════════════════


def _partes_seguras(nombre: str) -> list[str]:
	"""Partes de una ruta del ZIP, o ``ErrorPaquete`` si es peligrosa."""
	if "\x00" in nombre:
		raise ErrorPaquete(_("El paquete contiene un nombre de archivo no válido."))
	normal = nombre.replace("\\", "/")
	if normal.startswith("/") or ":" in normal:
		raise ErrorPaquete(_("El paquete contiene una ruta absoluta (%s); se rechaza.") % nombre)
	partes = [parte for parte in normal.split("/") if parte not in ("", ".")]
	for parte in partes:
		if parte == "..":
			raise ErrorPaquete(_("El paquete intenta escribir fuera de su carpeta (%s); se rechaza.") % nombre)
		if parte != parte.rstrip(" ."):
			raise ErrorPaquete(_("El paquete contiene un nombre no válido en Windows (%s).") % nombre)
		if parte.split(".")[0].lower() in _NOMBRES_RESERVADOS:
			raise ErrorPaquete(_("El paquete contiene un nombre reservado de Windows (%s).") % nombre)
	return partes


def extraer_seguro(ruta_zip: Path, destino: Path, config: Configuracion, progreso: Progreso | None = None) -> Path:
	"""Extrae el ZIP en ``destino/programa`` y ``destino/recursos``.

	Primero valida TODAS las entradas y solo después escribe: un paquete con una
	sola entrada peligrosa se rechaza entero. Quita la carpeta raíz común
	(``MiAplicacion/``) que pone ``empaquetar.py``. De ``data/`` solo acepta las
	subcarpetas de ``data_actualizable``; lo demás (config, db...) se descarta.
	"""
	if destino.exists():
		shutil.rmtree(destino)
	programa, recursos = destino / "programa", destino / "recursos"
	programa.mkdir(parents=True)
	raiz_segura = destino.resolve()
	permitidas = {nombre.lower() for nombre in config.data_actualizable}
	try:
		with zipfile.ZipFile(ruta_zip) as paquete:
			infos = paquete.infolist()
			if len(infos) > MAX_MIEMBROS:
				raise ErrorPaquete(_("El paquete contiene demasiados archivos; se rechaza."))
			entradas: list[tuple[zipfile.ZipInfo, list[str]]] = []
			vistos: set[str] = set()
			declarado = 0
			for info in infos:
				partes = _partes_seguras(info.filename)
				if stat.S_ISLNK(info.external_attr >> 16):
					raise ErrorPaquete(_("El paquete contiene enlaces simbólicos (%s); se rechaza.") % info.filename)
				if info.flag_bits & 0x1:
					raise ErrorPaquete(_("El paquete contiene archivos cifrados; se rechaza."))
				if info.is_dir() or not partes:
					continue
				clave = "/".join(partes).lower()
				if clave in vistos:
					raise ErrorPaquete(_("El paquete repite el archivo %s; se rechaza.") % info.filename)
				vistos.add(clave)
				declarado += info.file_size
				entradas.append((info, partes))
			if declarado > config.max_expandido:
				raise ErrorPaquete(_("El paquete ocupa demasiado al descomprimirse; se rechaza."))
			prefijo = 0
			primeras = {partes[0] for _info, partes in entradas}
			if len(primeras) == 1 and all(len(partes) > 1 for _info, partes in entradas):
				prefijo = 1
			escrito_total = 0
			for indice, (info, partes) in enumerate(entradas, start=1):
				relativas = partes[prefijo:]
				if relativas[0].lower() == "data":
					if len(relativas) >= 3 and relativas[1].lower() in permitidas:
						destino_archivo = recursos.joinpath(*relativas[1:])
					else:
						log.warning(
							"Se descarta %s del paquete: los datos del usuario nunca se sustituyen", info.filename
						)
						continue
				else:
					destino_archivo = programa.joinpath(*relativas)
				if not destino_archivo.resolve().is_relative_to(raiz_segura):
					raise ErrorPaquete(
						_("El paquete intenta escribir fuera de su carpeta (%s); se rechaza.") % info.filename
					)
				destino_archivo.parent.mkdir(parents=True, exist_ok=True)
				with paquete.open(info) as origen, destino_archivo.open("wb") as salida:
					for bloque in iter(lambda: origen.read(1024 * 1024), b""):
						escrito_total += len(bloque)
						if escrito_total > config.max_expandido:
							raise ErrorPaquete(_("El paquete ocupa demasiado al descomprimirse; se rechaza."))
						salida.write(bloque)
				_avisar(progreso, "extraccion", indice, len(entradas))
	except zipfile.BadZipFile as error:
		shutil.rmtree(destino, ignore_errors=True)
		raise ErrorPaquete(_("El paquete descargado está dañado.")) from error
	except BaseException:
		shutil.rmtree(destino, ignore_errors=True)
		raise
	if not (programa / config.ejecutable).is_file():
		shutil.rmtree(destino, ignore_errors=True)
		raise ErrorPaquete(_("El paquete no parece una versión de %s; no se instalará.") % config.nombre_app)
	log.info("Paquete extraído en %s (%d archivos)", destino, len(entradas))
	return destino


# ════════════════════════════════════════════════════════════════════════════
# Script de sustitución
# ════════════════════════════════════════════════════════════════════════════

# Plantilla del .bat. Reglas que la hacen robusta (todas comprobadas en las pruebas):
# - Todo el flujo con «goto»: dentro de bloques ( ) las %variables% se expanden al
#   leer el bloque entero y darían valores viejos.
# - Esperas con «ping -n 2 127.0.0.1» (≈1 s): «timeout» falla sin consola.
# - find/findstr/robocopy con ruta completa: un PATH con utilidades GNU tiene otro «find».
# - robocopy devuelve 0-7 si fue bien y 8 o más si falló.
# - La copia de la versión nueva NUNCA usa /MIR: no borra nada del destino.
# - «data» se salta por nombre SOLO en el primer nivel de la carpeta de la aplicación
#   (en lib/ puede haber carpetas llamadas data de otros paquetes).
# - Mensajes sin tildes: el registro se lee igual con cualquier página de códigos.
# - Los mensajes del registro nunca llevan rutas: «:anotar» las escribe sin comillas
#   y un «&» en una ruta partiría la orden.
_PLANTILLA_BAT = r"""@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
rem Script de sustitucion generado por app/services/actualizador.py. No lo edite.
set "APP={app}"
set "DATOS_APP=%APP%\data"
set "TRABAJO={trabajo}"
set "NUEVA=%TRABAJO%\nueva"
set "RESPALDO={respaldo}"
set "MARCADOR={marcador}"
set "RESULTADO={resultado}"
set "ARCHIVO_PID=%TRABAJO%\pid_nueva.txt"
set "REGISTRO={registro}"
set "EJECUTABLE={ejecutable}"
set "VERSION_NUEVA={version_nueva}"
set "VERSION_ACTUAL={version_actual}"
set "PID_APP={pid}"
set "ESPERA_CIERRE={espera_cierre}"
set "ESPERA_SALUD={espera_salud}"
set "RECURSOS={recursos}"
set "COMANDO={comando}"
set "SISTEMA=%SystemRoot%\System32"
set "ROBO=/R:2 /W:1 /NP /NFL /NDL /NJH /NJS"
set "MOTIVO=desconocido"

call :anotar "Inicio: de %VERSION_ACTUAL% a %VERSION_NUEVA%"
if not exist "%APP%\%EJECUTABLE%" goto instalacion_no_valida
if not exist "%NUEVA%\programa\%EJECUTABLE%" goto instalacion_no_valida

rem 1. Esperar a que la aplicacion se cierre (sin matarla nunca).
set /a INTENTOS=0
:esperar_cierre
"%SISTEMA%\tasklist.exe" /FI "PID eq %PID_APP%" /NH 2>nul | "%SISTEMA%\find.exe" " %PID_APP% " >nul
if errorlevel 1 goto cerrada
set /a INTENTOS+=1
if %INTENTOS% geq %ESPERA_CIERRE% goto no_se_cierra
ping -n 2 127.0.0.1 >nul
goto esperar_cierre

:cerrada
call :anotar "Aplicacion cerrada"
rem 2. Copia de seguridad de la version actual, sin data (salvo sus recursos).
if exist "%RESPALDO%" rmdir /s /q "%RESPALDO%"
if exist "%RESPALDO%" goto fallo_respaldo
"%SISTEMA%\robocopy.exe" "%APP%" "%RESPALDO%\programa" %ROBO% >nul
if errorlevel 8 goto fallo_respaldo
for /f "delims=" %%D in ('dir /b /a:d "%APP%" 2^>nul') do if /I not "%%D"=="data" (
	"%SISTEMA%\robocopy.exe" "%APP%\%%D" "%RESPALDO%\programa\%%D" /E %ROBO% >nul
	if errorlevel 8 goto fallo_respaldo
)
for %%S in (%RECURSOS%) do if exist "%DATOS_APP%\%%S" (
	"%SISTEMA%\robocopy.exe" "%DATOS_APP%\%%S" "%RESPALDO%\recursos\%%S" /E %ROBO% >nul
	if errorlevel 8 goto fallo_respaldo
)
if not exist "%RESPALDO%\programa\%EJECUTABLE%" goto fallo_respaldo
call :anotar "Copia de seguridad hecha"

rem 3. Copiar la version nueva encima (sin borrar nada; data solo recibe recursos).
del /f /q "%MARCADOR%" "%ARCHIVO_PID%" >nul 2>&1
"%SISTEMA%\robocopy.exe" "%NUEVA%\programa" "%APP%" /E /IS /IT %ROBO% >nul
if errorlevel 8 goto fallo_copia
if not exist "%NUEVA%\recursos" goto copiada
for /f "delims=" %%S in ('dir /b /a:d "%NUEVA%\recursos" 2^>nul') do (
	"%SISTEMA%\robocopy.exe" "%NUEVA%\recursos\%%S" "%DATOS_APP%\%%S" /E /IS /IT %ROBO% >nul
	if errorlevel 8 goto fallo_copia
)
:copiada
call :anotar "Version nueva copiada; se lanza con {argumento}"

rem 4. Lanzar la version nueva guardando su PID (start no lo da; PowerShell si).
{linea_lanzar}
set "NUEVO_PID="
if exist "%ARCHIVO_PID%" set /p NUEVO_PID=<"%ARCHIVO_PID%"
echo(%NUEVO_PID%| "%SISTEMA%\findstr.exe" /r /x "[0-9][0-9]*" >nul || set "NUEVO_PID="
if not defined NUEVO_PID goto sin_pid
call :anotar "Version nueva en marcha con PID %NUEVO_PID%; esperando el marcador de salud"

rem 5. Esperar el marcador de salud.
set /a SEGUNDOS=0
:esperar_salud
if exist "%MARCADOR%" goto comprobar_marcador
"%SISTEMA%\tasklist.exe" /FI "PID eq %NUEVO_PID%" /NH 2>nul | "%SISTEMA%\find.exe" " %NUEVO_PID% " >nul
if errorlevel 1 goto nueva_cerrada
set /a SEGUNDOS+=1
if %SEGUNDOS% geq %ESPERA_SALUD% goto sin_salud
ping -n 2 127.0.0.1 >nul
goto esperar_salud

:comprobar_marcador
ping -n 2 127.0.0.1 >nul
"%SISTEMA%\findstr.exe" /l /c:"ok %VERSION_NUEVA%" "%MARCADOR%" >nul && goto sana
set "MOTIVO=marcador_incorrecto"
goto matar_y_volver

:nueva_cerrada
ping -n 2 127.0.0.1 >nul
if exist "%MARCADOR%" goto comprobar_marcador
set "MOTIVO=se_cerro_sin_confirmar"
goto volver_atras

:sin_salud
set "MOTIVO=sin_marcador_de_salud"
:matar_y_volver
"%SISTEMA%\taskkill.exe" /F /T /PID %NUEVO_PID% >nul 2>&1
ping -n 3 127.0.0.1 >nul
goto volver_atras

:sin_pid
set "MOTIVO=no_se_pudo_lanzar"
goto volver_atras

:fallo_copia
set "MOTIVO=fallo_copia"
goto volver_atras

rem 6. Vuelta atras: se borra el programa (NUNCA data) y se restaura la copia.
:volver_atras
call :anotar "Vuelta atras (%MOTIVO%): se restaura %VERSION_ACTUAL%"
if not exist "%RESPALDO%\programa\%EJECUTABLE%" goto fallo_restaurar
for /f "delims=" %%F in ('dir /b /a:-d "%APP%" 2^>nul') do del /f /q /a "%APP%\%%F" >nul 2>&1
for /f "delims=" %%D in ('dir /b /a:d "%APP%" 2^>nul') do if /I not "%%D"=="data" rmdir /s /q "%APP%\%%D"
"%SISTEMA%\robocopy.exe" "%RESPALDO%\programa" "%APP%" /E /IS /IT %ROBO% >nul
if errorlevel 8 goto fallo_restaurar
if not exist "%RESPALDO%\recursos" goto restaurada
for /f "delims=" %%S in ('dir /b /a:d "%RESPALDO%\recursos" 2^>nul') do (
	"%SISTEMA%\robocopy.exe" "%RESPALDO%\recursos\%%S" "%DATOS_APP%\%%S" /E /IS /IT %ROBO% >nul
	if errorlevel 8 goto fallo_restaurar
)
:restaurada
call :anotar "Version %VERSION_ACTUAL% restaurada; se relanza"
call :limpiar
call :resultado revertida
start "" /D "%APP%" %COMANDO%
goto fin

:fallo_restaurar
call :anotar "ERROR GRAVE: no se pudo restaurar. La copia sigue en data\temp\version_anterior\programa"
call :resultado rota
goto fin

:fallo_respaldo
set "MOTIVO=fallo_copia_de_seguridad"
call :anotar "ERROR: fallo la copia de seguridad; la instalacion NO se ha tocado"
call :limpiar
call :resultado no_aplicada
start "" /D "%APP%" %COMANDO%
goto fin

:no_se_cierra
set "MOTIVO=la_aplicacion_no_se_cerro"
call :anotar "La aplicacion no se cerro en %ESPERA_CIERRE% s; se cancela sin tocar nada"
call :limpiar
call :resultado no_aplicada
goto fin

:instalacion_no_valida
set "MOTIVO=instalacion_no_valida"
call :anotar "ERROR: falta el ejecutable en la instalacion o en la version nueva; no se toca nada"
call :resultado no_aplicada
goto fin

:sana
call :anotar "Arranque confirmado: actualizacion a %VERSION_NUEVA% completada"
set "MOTIVO=ninguno"
rmdir /s /q "%RESPALDO%" >nul 2>&1
call :limpiar
call :resultado correcta
goto fin

:fin
call :anotar "Fin del script"
endlocal
(goto) 2>nul & del /f /q "%~f0"

:anotar
>>"%REGISTRO%" echo [%date% %time%] %~1
exit /b 0

:limpiar
rmdir /s /q "%NUEVA%" >nul 2>&1
rmdir /s /q "%TRABAJO%\descarga" >nul 2>&1
del /f /q "%ARCHIVO_PID%" "%MARCADOR%" >nul 2>&1
exit /b 0

:resultado
>"%RESULTADO%" echo estado=%~1
>>"%RESULTADO%" echo motivo=%MOTIVO%
>>"%RESULTADO%" echo version=%VERSION_NUEVA%
>>"%RESULTADO%" echo anterior=%VERSION_ACTUAL%
exit /b 0
"""


def _bat(texto: str) -> str:
	"""Escapa ``%`` para un .bat (en un .bat, ``%%`` es un ``%`` literal)."""
	return str(texto).replace("%", "%%")


def _ps(texto: str) -> str:
	"""Cadena de PowerShell entre comillas simples (la comilla se duplica)."""
	return "'" + str(texto).replace("'", "''") + "'"


def _linea_lanzar(config: Configuracion, archivo_pid: Path) -> str:
	"""Orden que lanza la versión nueva con PowerShell y guarda su PID.

	``start`` de cmd no devuelve el PID, y sin PID no se podría terminar una versión
	nueva colgada antes de restaurar. Las comillas dobles de los argumentos se
	construyen con ``[char]34`` para no romper las comillas de la línea de cmd.
	"""
	programa, *argumentos = config.comando or [str(config.carpeta_app / config.ejecutable)]
	piezas = [f"[char]34 + {_ps(argumento)} + [char]34" for argumento in argumentos]
	piezas.append(_ps(ARGUMENTO_TRAS_ACTUALIZAR))
	lista = " + ' ' + ".join(piezas)
	guion = (
		"$ErrorActionPreference = 'Stop'; "
		f"$p = Start-Process -FilePath {_ps(programa)} -ArgumentList ({lista}) "
		f"-WorkingDirectory {_ps(config.carpeta_app)} -PassThru; "
		f"[IO.File]::WriteAllText({_ps(archivo_pid)}, [string]$p.Id)"
	)
	return (
		'"%SISTEMA%\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -NonInteractive '
		f'-ExecutionPolicy Bypass -Command "{_bat(guion)}" >nul 2>&1'
	)


def _comprobar_instalacion(config: Configuracion) -> None:
	"""Defensa antes de generar un script que borra y copia archivos."""
	carpeta = config.carpeta_app
	if carpeta.parent == carpeta:
		raise ErrorActualizacion(_("La aplicación está en la raíz de una unidad; no se actualiza automáticamente."))
	if not (carpeta / config.ejecutable).is_file():
		raise ErrorActualizacion(
			_("No se encuentra %s en la carpeta de la aplicación; no se puede actualizar.") % config.ejecutable
		)
	for nombre in config.data_actualizable:
		if not re.fullmatch(r"[A-Za-z0-9_-]+", nombre):
			raise ErrorActualizacion(f"Nombre de subcarpeta de data no válido: {nombre!r}")


def preparar_sustitucion(config: Configuracion, info: InfoActualizacion) -> Path:
	"""Genera ``data/temp/actualizacion/actualizar.bat`` (CRLF, UTF-8 sin BOM)."""
	_comprobar_instalacion(config)
	trabajo = config.carpeta_trabajo
	trabajo.mkdir(parents=True, exist_ok=True)
	config.registro_script.parent.mkdir(parents=True, exist_ok=True)
	for viejo in (config.marcador_salud, config.archivo_resultado):
		viejo.unlink(missing_ok=True)
	comando = " ".join(f'"{parte}"' for parte in (config.comando or [str(config.carpeta_app / config.ejecutable)]))
	valores = {
		"app": _bat(config.carpeta_app),
		"trabajo": _bat(trabajo),
		"respaldo": _bat(config.carpeta_respaldo),
		"marcador": _bat(config.marcador_salud),
		"resultado": _bat(config.archivo_resultado),
		"registro": _bat(config.registro_script),
		"ejecutable": _bat(config.ejecutable),
		"version_nueva": _bat(info.version),
		"version_actual": _bat(config.version_actual),
		"pid": str(int(config.pid_a_esperar or os.getpid())),
		"espera_cierre": str(int(config.espera_cierre)),
		"espera_salud": str(int(config.espera_salud)),
		"recursos": " ".join(config.data_actualizable),
		"comando": _bat(comando),
		"argumento": ARGUMENTO_TRAS_ACTUALIZAR,
		"linea_lanzar": _linea_lanzar(config, trabajo / "pid_nueva.txt"),
	}
	contenido = _PLANTILLA_BAT
	for clave, valor in valores.items():
		contenido = contenido.replace("{" + clave + "}", valor)
	ruta = trabajo / "actualizar.bat"
	ruta.write_bytes(contenido.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
	log.info("Script de sustitución preparado en %s", ruta)
	return ruta


def lanzar_sustitucion(ruta_bat: Path) -> subprocess.Popen:
	"""Lanza el .bat oculto y desacoplado, para que sobreviva al cierre de la app.

	Se ejecuta con la carpeta del script como directorio de trabajo y como
	``.\\actualizar.bat``: así ``cmd /c`` no tiene que interpretar comillas de una
	ruta que pueda contener ``&`` u otros caracteres especiales. El ``.\\`` es
	necesario: con ``NoDefaultCurrentDirectoryInExePath`` definida, cmd no busca
	en la carpeta actual y el nombre suelto «no se reconoce como un comando».
	"""
	crear_sin_ventana = 0x08000000
	nuevo_grupo = 0x00000200
	salir_del_job = 0x01000000
	cmd = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe")
	argumentos = {
		"cwd": str(ruta_bat.parent),
		"stdin": subprocess.DEVNULL,
		"stdout": subprocess.DEVNULL,
		"stderr": subprocess.DEVNULL,
		"close_fds": True,
	}
	orden = [cmd, "/d", "/c", ".\\" + ruta_bat.name]
	log.info("Lanzando el script de sustitución %s", ruta_bat)
	try:
		# Si la aplicación está dentro de un Job Object que mata a sus hijos al
		# cerrarse, hay que salir de él o el script moriría con ella.
		return subprocess.Popen(orden, creationflags=crear_sin_ventana | nuevo_grupo | salir_del_job, **argumentos)
	except OSError:
		log.debug("No se pudo salir del Job Object; se lanza sin esa bandera", exc_info=True)
		return subprocess.Popen(orden, creationflags=crear_sin_ventana | nuevo_grupo, **argumentos)


def descargar_y_preparar(
	config: Configuracion,
	info: InfoActualizacion,
	progreso: Progreso | None = None,
	cancelado: Callable[[], bool] | None = None,
) -> Path:
	"""Descarga, verifica, extrae y genera el script. Pensada para un hilo de fondo.

	Devuelve la ruta del ``.bat``; después la interfaz llama a
	:func:`lanzar_sustitucion` y cierra la aplicación.
	"""
	if not puede_instalar(config):
		raise ErrorActualizacion(_("Esta copia se ejecuta desde el código fuente: no se sustituye automáticamente."))
	_comprobar_instalacion(config)
	config.carpeta_trabajo.mkdir(parents=True, exist_ok=True)
	libre = shutil.disk_usage(config.carpeta_trabajo).free
	if libre < info.tamano * 4:
		raise ErrorActualizacion(_("No hay espacio libre suficiente en el disco para actualizar."))
	ruta_zip = descargar(config, info, progreso, cancelado)
	_avisar(progreso, "verificacion", 0, 1)
	verificar_paquete(ruta_zip, info)
	_avisar(progreso, "verificacion", 1, 1)
	extraer_seguro(ruta_zip, config.carpeta_trabajo / "nueva", config, progreso)
	ruta_zip.unlink(missing_ok=True)
	_avisar(progreso, "preparacion", 0, 1)
	ruta_bat = preparar_sustitucion(config, info)
	_avisar(progreso, "preparacion", 1, 1)
	return ruta_bat


# ════════════════════════════════════════════════════════════════════════════
# Arranque de la aplicación
# ════════════════════════════════════════════════════════════════════════════


def confirmar_arranque(config: Configuracion, tras_actualizar: bool | None = None) -> None:
	"""Llamar unos segundos después de mostrar la ventana principal.

	Anota la versión como confirmada (anti-rollback) y, si la aplicación arrancó
	con ``--tras-actualizar``, escribe el marcador de salud que espera el script.
	Esperar unos segundos demuestra que la ventana se abrió y el bucle de eventos
	funciona, no solo que ``main.py`` empezó.
	"""
	if tras_actualizar is None:
		tras_actualizar = ARGUMENTO_TRAS_ACTUALIZAR in sys.argv
	confirmar_version(config)
	if not tras_actualizar:
		return
	try:
		config.marcador_salud.parent.mkdir(parents=True, exist_ok=True)
		temporal = config.marcador_salud.with_name("salud.tmp")
		temporal.write_text(f"ok {config.version_actual}\n", encoding="utf-8")
		os.replace(temporal, config.marcador_salud)
		log.info("Marcador de salud escrito para la versión %s", config.version_actual)
	except OSError:
		log.exception("No se pudo escribir el marcador de salud: el script volverá a la versión anterior")


def limpiar_trabajo(config: Configuracion) -> None:
	"""Borra los restos de actualizaciones terminadas, salvo descargas a medias."""
	trabajo = config.carpeta_trabajo
	if not trabajo.is_dir():
		return
	for elemento in trabajo.iterdir():
		if elemento.name == "descarga" or elemento.suffix.lower() == ".bat":
			continue  # la descarga parcial se reanuda; el .bat se borra solo
		try:
			if elemento.is_dir():
				shutil.rmtree(elemento)
			else:
				elemento.unlink()
		except OSError:
			log.debug("No se pudo borrar %s", elemento, exc_info=True)


def leer_resultado_anterior(config: Configuracion) -> str | None:
	"""Lee lo que dejó el script y devuelve un mensaje para el usuario (o ``None``).

	Llamar al arrancar. Si la versión nueva no arrancó, la omite para que la
	comprobación periódica no la vuelva a ofrecer en bucle.
	"""
	ruta = config.archivo_resultado
	if not ruta.is_file():
		return None
	datos: dict[str, str] = {}
	try:
		for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
			clave, _separador, valor = linea.partition("=")
			datos[clave.strip()] = valor.strip()
		ruta.unlink()
	except OSError:
		log.warning("No se pudo leer el resultado de la actualización", exc_info=True)
		return None
	limpiar_trabajo(config)
	estado, version, motivo = datos.get("estado", ""), datos.get("version", "?"), datos.get("motivo", "")
	log.info("Resultado de la actualización anterior: %s (versión %s, motivo %s)", estado, version, motivo)
	if estado == "correcta":
		return None
	if estado == "revertida":
		omitir_version(config, version)
		return _(
			"La versión %(nueva)s no arrancó correctamente y se ha vuelto a la versión %(actual)s. "
			"Sus datos no se han tocado. Los detalles están en data/logs/actualizacion.log."
		) % {"nueva": version, "actual": datos.get("anterior", config.version_actual)}
	if estado == "no_aplicada":
		if motivo == "la_aplicacion_no_se_cerro":
			return (
				_(
					"La actualización a la versión %s no se aplicó porque la aplicación no llegó a cerrarse. Puede volver a intentarlo desde el menú Ayuda."
				)
				% version
			)
		return (
			_(
				"La actualización a la versión %s no se aplicó y la instalación no se ha tocado. Los detalles están en data/logs/actualizacion.log."
			)
			% version
		)
	return _("La actualización a la versión %s tuvo un problema. Consulte data/logs/actualizacion.log.") % version
