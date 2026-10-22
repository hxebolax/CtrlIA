"""Rutas de la aplicación (modo portable): TODO vive en ``data``.

La carpeta ``data`` está junto al programa:

- Desde el código: junto a ``main.py`` (la raíz del proyecto).
- Compilada con cx_Freeze: junto al ``.exe`` (``Path(sys.executable).parent``).

Nunca se escribe en %APPDATA%, %LOCALAPPDATA%, %TEMP% ni en el registro de
Windows: así la aplicación va en un pendrive, se desinstala borrando su
carpeta y la copia de seguridad es copiar una carpeta.

La variable de entorno ``APP_DATA_DIR`` redirige ``data`` a otra carpeta. La
usan las pruebas para no tocar nunca la configuración real del desarrollador.

Este módulo solo usa la biblioteca estándar: lo importa ``main.py`` antes que
nada (incluso antes que wx) para poder registrar un fallo de arranque.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ¿Se ejecuta la versión compilada con cx_Freeze?
CONGELADA: bool = bool(getattr(sys, "frozen", False))

# Variable de entorno que redirige la carpeta data (pruebas, perfiles aparte).
VARIABLE_DATA = "APP_DATA_DIR"


def ruta_base() -> Path:
	"""Carpeta raíz del programa.

	Compilada: la carpeta del ejecutable. Desde código: la que contiene
	``main.py``, dos niveles por encima de este archivo (``<raíz>/app/core``).
	No se usa ``os.getcwd()``: un acceso directo o el Explorador pueden
	arrancar el programa con otra carpeta de trabajo.
	"""
	if CONGELADA:
		return Path(sys.executable).resolve().parent
	return Path(__file__).resolve().parent.parent.parent


def ruta_datos() -> Path:
	"""Devuelve (y crea si falta) la carpeta ``data``."""
	redireccion = os.environ.get(VARIABLE_DATA, "").strip()
	ruta = Path(redireccion).expanduser().resolve() if redireccion else ruta_base() / "data"
	ruta.mkdir(parents=True, exist_ok=True)
	return ruta


def subcarpeta(nombre: str) -> Path:
	"""Devuelve (y crea si falta) una subcarpeta de ``data``.

	Las carpetas se crean justo antes de usarse para no llenar ``data`` de
	carpetas vacías que luego alguien podría distribuir por error.
	"""
	ruta = ruta_datos() / nombre
	ruta.mkdir(parents=True, exist_ok=True)
	return ruta


# ── Generadas al usar la aplicación (NUNCA se distribuyen) ───────────────────


def ruta_configuracion() -> Path:
	"""Carpeta de configuración (``config.json``)."""
	return subcarpeta("config")


def ruta_registros() -> Path:
	"""Carpeta de registros (logs)."""
	return subcarpeta("logs")


def ruta_temporales() -> Path:
	"""Temporales de la aplicación (por ejemplo, el buzón de la instancia única)."""
	return subcarpeta("temp")


# ── Recursos que viajan con la distribución ──────────────────────────────────


def ruta_documentacion() -> Path:
	"""Manual de usuario, lista de teclas y novedades."""
	return subcarpeta("docs")


def ruta_iconos() -> Path:
	"""Iconos de la aplicación (``app.ico``)."""
	return subcarpeta("icons")


def ruta_sonidos() -> Path:
	"""Sonidos de la interfaz."""
	return subcarpeta("sounds")


def ruta_binarios() -> Path:
	"""Binarios auxiliares (ffmpeg y similares)."""
	return subcarpeta("bin")


def ruta_traducciones() -> Path:
	"""Catálogos gettext: ``data/locales/<idioma>/LC_MESSAGES``."""
	return subcarpeta("locales")
