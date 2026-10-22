"""Internacionalización con gettext. El idioma base del código es el español.

Uso en el arranque (``main.py``), ANTES de importar las ventanas::

	from app.core import i18n
	idioma = i18n.inicializar(configuracion.get("idioma"))  # None o "auto" = el del sistema
	app = wx.App(False)
	i18n.configurar_wx(idioma)  # botones estándar de wx en ese idioma

Después, en cualquier módulo, sin importar nada::

	etiqueta = _("&Guardar")
	resumen = ngettext("Se descargó {n} archivo", "Se descargaron {n} archivos", n).format(n=n)
	abrir = pgettext("verbo", "Abrir")

Qué instala en ``builtins``: ``_``, ``gettext``, ``ngettext`` (y su alias
``n_``), ``pgettext``, ``npgettext`` y ``N_`` (marca una cadena para extraerla
sin traducirla todavía, útil en constantes de módulo).

Dónde busca los catálogos: ``data/locales/<idioma>/LC_MESSAGES/<DOMINIO>.mo``
junto a ``main.py`` o al ``.exe``. ``APP_DATA_DIR`` redirige ``data`` (pruebas).

Orden para elegir el idioma:

1. La variable de entorno ``APP_IDIOMA`` (para probar una traducción sin
   tocar la configuración).
2. El valor guardado en la configuración (argumento ``idioma``).
3. El idioma de la interfaz de Windows (``GetUserDefaultUILanguage``) o, en
   otros sistemas, el de ``locale``.
4. Español: el idioma del código, que no necesita catálogo.

Si el idioma elegido no tiene catálogo o el catálogo está dañado, se usa el
español (``NullTranslations``) y se registra un aviso: la aplicación arranca
siempre.

Cambio de idioma en caliente (``cambiar_idioma``):

- SÍ cambia al momento: todo texto que se pida con ``_()`` a partir de ese
  instante (mensajes, anuncios de voz, diálogos que se abran después).
- NO cambia solo: lo que ya está pintado (menús, etiquetas, cabeceras de
  columnas, barra de estado). Registre funciones con ``al_cambiar_idioma``
  para reconstruirlo, o pida reiniciar.
- REQUIERE REINICIAR: los botones y diálogos estándar de wxWidgets
  (``wx.Locale`` no se puede sustituir de forma segura con ventanas abiertas)
  y las constantes calculadas al importar un módulo con ``_()``.

PLANTILLA del agente «traducciones».
"""

from __future__ import annotations

import builtins
import gettext
import locale
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# Nombre de los catálogos (<DOMINIO>.po / .mo / .pot). Las herramientas de
# tools/ lo leen de aquí: cámbielo solo en este archivo.
DOMINIO = "ctrlia"
# Idioma en el que está escrito el código fuente.
IDIOMA_BASE = "es"
VARIABLE_DATA = "APP_DATA_DIR"
VARIABLE_IDIOMA = "APP_IDIOMA"
# Valores de configuración que significan «el del sistema».
AUTOMATICO = {"", "auto", "automatico", "automático", "sistema"}

# Nombres de cada idioma EN ESE IDIOMA: quien no entiende español encuentra
# el suyo en el selector.
NOMBRES_IDIOMA = {
	"es": "Español",
	"en": "English",
	"fr": "Français",
	"de": "Deutsch",
	"it": "Italiano",
	"pt": "Português",
	"pt_BR": "Português do Brasil",
	"ca": "Català",
	"gl": "Galego",
	"eu": "Euskara",
	"nl": "Nederlands",
	"pl": "Polski",
	"ru": "Русский",
	"uk": "Українська",
	"tr": "Türkçe",
	"ar": "العربية",
	"zh": "中文",
	"ja": "日本語",
}

_traduccion: gettext.NullTranslations = gettext.NullTranslations()
_idioma: str = IDIOMA_BASE
# Se conserva la referencia: si el recolector destruye el wx.Locale, los
# botones estándar vuelven al inglés sin avisar.
_locale_wx = None
_oyentes: list[Callable[[str], None]] = []


# ── Rutas ────────────────────────────────────────────────────────────────────

def _ruta_base() -> Path:
	"""Carpeta del .exe (compilada) o de main.py (desde código: <raíz>/app/core/i18n.py)."""
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent
	return Path(__file__).resolve().parent.parent.parent


def carpeta_locales() -> Path:
	"""``data/locales`` (o ``$APP_DATA_DIR/locales``). No la crea: solo se lee."""
	redireccion = os.environ.get(VARIABLE_DATA, "").strip()
	datos = Path(redireccion).expanduser() if redireccion else _ruta_base() / "data"
	return datos / "locales"


# ── Detección ────────────────────────────────────────────────────────────────

def normalizar(codigo: str | None) -> str:
	"""``es-ES``, ``es_ES.UTF-8`` o ``Spanish_Spain`` → ``es_ES``; ``EN`` → ``en``."""
	if not codigo:
		return ""
	codigo = codigo.strip().split(".")[0].split("@")[0].replace("-", "_")
	if "_" in codigo and len(codigo.split("_")[0]) > 3:
		# Nombre largo de Windows («Spanish_Spain»): se traduce con la tabla de locale.
		codigo = locale.normalize(codigo).split(".")[0]
	partes = codigo.split("_")
	if len(partes) >= 2 and partes[1]:
		return f"{partes[0].lower()}_{partes[1].upper()}"
	return partes[0].lower()


def idioma_sistema() -> str:
	"""Idioma de la interfaz del sistema (``es_ES``, ``en_US``...) o ``""`` si no se sabe.

	En Windows se pregunta el idioma de la INTERFAZ del usuario, no el formato
	regional: alguien puede tener Windows en inglés con fechas españolas.
	"""
	if sys.platform == "win32":
		try:
			import ctypes

			identificador = ctypes.windll.kernel32.GetUserDefaultUILanguage()
			codigo = locale.windows_locale.get(identificador, "")
			if codigo:
				return normalizar(codigo)
		except (AttributeError, OSError):
			log.debug("No se pudo consultar GetUserDefaultUILanguage", exc_info=True)
	for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
		valor = os.environ.get(variable, "")
		if valor:
			return normalizar(valor.split(":")[0])
	try:
		return normalizar(locale.getlocale()[0])
	except (ValueError, TypeError):
		log.debug("locale.getlocale() no devolvió un idioma", exc_info=True)
		return ""


def es_idioma_base(codigo: str) -> bool:
	return normalizar(codigo).split("_")[0] == IDIOMA_BASE


def idiomas_disponibles(dominio: str = DOMINIO) -> list[str]:
	"""Idiomas que se pueden elegir: el base y los que tienen ``.mo``."""
	encontrados: set[str] = set()
	carpeta = carpeta_locales()
	if carpeta.is_dir():
		for subcarpeta in carpeta.iterdir():
			if (subcarpeta / "LC_MESSAGES" / f"{dominio}.mo").is_file():
				encontrados.add(subcarpeta.name)
	encontrados.discard(IDIOMA_BASE)
	return [IDIOMA_BASE, *sorted(encontrados, key=lambda c: nombre_idioma(c).casefold())]


def nombre_idioma(codigo: str) -> str:
	return NOMBRES_IDIOMA.get(codigo) or NOMBRES_IDIOMA.get(codigo.split("_")[0]) or codigo


def resolver_idioma(configurado: str | None = None) -> str:
	"""Idioma que se intentará cargar, según el orden descrito en el módulo."""
	forzado = os.environ.get(VARIABLE_IDIOMA, "").strip()
	if forzado:
		return normalizar(forzado)
	if configurado and configurado.strip().casefold() not in AUTOMATICO:
		return normalizar(configurado)
	return idioma_sistema() or IDIOMA_BASE


# ── Instalación ──────────────────────────────────────────────────────────────

def _instalar(traduccion: gettext.NullTranslations) -> None:
	"""Publica las funciones en builtins para que todos los módulos las vean."""
	traduccion.install(names=["gettext", "ngettext", "pgettext", "npgettext"])
	builtins.n_ = traduccion.ngettext
	builtins.N_ = lambda texto: texto


def inicializar(idioma: str | None = None, dominio: str = DOMINIO) -> str:
	"""Carga el catálogo del idioma e instala ``_`` y compañía en builtins.

	:param idioma: el guardado en la configuración; ``None`` o ``"auto"``
		para usar el del sistema.
	:return: el idioma realmente activo (``"es"`` si se recurrió al español).
	"""
	global _traduccion, _idioma
	solicitado = resolver_idioma(idioma)
	traduccion: gettext.NullTranslations = gettext.NullTranslations()
	efectivo = IDIOMA_BASE
	if not es_idioma_base(solicitado):
		carpeta = carpeta_locales()
		try:
			# gettext prueba solo «pt_BR» y luego «pt»: no hace falta repetirlo.
			traduccion = gettext.translation(dominio, localedir=str(carpeta), languages=[solicitado])
			efectivo = solicitado
		except FileNotFoundError:
			log.warning(
				"No hay traducción «%s» (%s.mo) en %s; se usa el español.", solicitado, dominio, carpeta
			)
		except (OSError, ValueError, UnicodeDecodeError):
			# .mo dañado, a medio copiar o con una regla de plurales inválida.
			log.error("El catálogo «%s» no se pudo cargar; se usa el español.", solicitado, exc_info=True)
	_instalar(traduccion)
	_traduccion, _idioma = traduccion, efectivo
	log.info("Idioma de la interfaz: %s (solicitado: %s)", efectivo, solicitado)
	return efectivo


def idioma_actual() -> str:
	return _idioma


def traduccion_actual() -> gettext.NullTranslations:
	"""El catálogo activo, para quien necesite llamarlo sin pasar por builtins."""
	return _traduccion


# ── Cambio en caliente ───────────────────────────────────────────────────────

def al_cambiar_idioma(funcion: Callable[[str], None]) -> None:
	"""Registra una función que reconstruye textos ya pintados (menús, barra de estado)."""
	if funcion not in _oyentes:
		_oyentes.append(funcion)


def cambiar_idioma(idioma: str | None) -> tuple[str, bool]:
	"""Cambia el catálogo sin reiniciar y avisa a los oyentes.

	:return: (idioma activo, ¿hace falta reiniciar para que wx cambie también?).
		Guarde la preferencia y ofrezca reiniciar si el segundo valor es True.
	"""
	anterior = _idioma
	efectivo = inicializar(idioma)
	for funcion in list(_oyentes):
		try:
			funcion(efectivo)
		except Exception:
			log.exception("Falló al refrescar textos tras cambiar el idioma (%r)", funcion)
	necesita_reinicio = _locale_wx is not None and efectivo != anterior
	return efectivo, necesita_reinicio


# ── wxWidgets ────────────────────────────────────────────────────────────────

def configurar_wx(idioma: str | None = None):
	"""Crea el ``wx.Locale`` del idioma activo (botones estándar: Aceptar, Cancelar...).

	Llamar DESPUÉS de crear ``wx.App`` (``App.InitLocale`` reinicia la
	configuración regional al arrancar) y UNA sola vez por sesión. Devuelve el
	objeto, que este módulo también conserva.
	"""
	global _locale_wx
	import wx

	codigo = normalizar(idioma or _idioma) or IDIOMA_BASE
	carpeta_wx = Path(wx.__file__).resolve().parent / "locale"
	if carpeta_wx.is_dir():
		# En la versión compilada el catálogo wxstd.mo está en lib/wx/locale.
		wx.Locale.AddCatalogLookupPathPrefix(str(carpeta_wx))
	informacion = wx.Locale.FindLanguageInfo(codigo) or wx.Locale.FindLanguageInfo(codigo.split("_")[0])
	identificador = informacion.Language if informacion else wx.LANGUAGE_SPANISH
	# wx.LogNull: si el idioma no está instalado en Windows, wx mostraría un
	# cuadro de error en inglés; se registra aquí en su lugar.
	sin_avisos = wx.LogNull()
	try:
		nuevo = wx.Locale(identificador)
	except Exception:
		log.warning("No se pudo crear wx.Locale para «%s»; los botones estándar pueden salir en inglés.", codigo, exc_info=True)
		return _locale_wx
	finally:
		del sin_avisos
	if not nuevo.IsOk():
		log.warning("wx.Locale(«%s») no está disponible en este sistema.", codigo)
	_locale_wx = nuevo
	log.debug("wx.Locale activo: %s", nuevo.GetCanonicalName())
	return nuevo


# ``_`` debe existir aunque un módulo se importe antes de inicializar (por
# ejemplo, en las pruebas): se instala el español sin traducción. Se mira
# ``ngettext`` porque la consola interactiva ya define ``_`` (último resultado).
if not hasattr(builtins, "ngettext"):
	_instalar(gettext.NullTranslations())
