"""Gestión centralizada de rutas de la aplicación (modo portable).

TODO lo que la aplicación genera o necesita vive en la carpeta ``data``
situada junto al programa:

- Desde código fuente: junto a ``main.py`` (la raíz del proyecto).
- Compilada con cx_Freeze: junto al ``.exe`` (``sys.executable``).

Nunca se escribe en %APPDATA%, %LOCALAPPDATA%, %TEMP% ni en el registro de
Windows. Así la aplicación se puede llevar en un pendrive, se desinstala
borrando su carpeta y las copias de seguridad son triviales.

La variable de entorno ``APP_DATA_DIR`` redirige ``data`` a otra carpeta: la
usan las PRUEBAS para trabajar sobre un directorio temporal sin tocar la
configuración real del desarrollador.

PLANTILLA del agente «entorno-virtual».
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Indica si se ejecuta la versión compilada con cx_Freeze.
CONGELADA: bool = bool(getattr(sys, "frozen", False))

# Variable de entorno que redirige la carpeta data (pruebas, perfiles aparte).
VARIABLE_DATA = "APP_DATA_DIR"


def ruta_base() -> Path:
	"""Devuelve la carpeta raíz del programa.

	Congelada: la carpeta del ejecutable. Desde código: la carpeta que contiene
	``main.py``, que está dos niveles por encima de este archivo
	(``<raíz>/app/core/paths.py``).
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

	Las carpetas se crean bajo demanda, justo antes de usarse, para no llenar
	``data`` de carpetas vacías.
	"""
	ruta = ruta_datos() / nombre
	ruta.mkdir(parents=True, exist_ok=True)
	return ruta


# ── Carpetas generadas en tiempo de ejecución (NO se distribuyen) ─────────────

def ruta_configuracion() -> Path:
	"""Carpeta de configuración (settings.json y similares)."""
	return subcarpeta("config")


def ruta_registros() -> Path:
	"""Carpeta de registros (logs)."""
	return subcarpeta("logs")


def ruta_base_datos(nombre: str = "app.db") -> Path:
	"""Ruta del archivo SQLite principal."""
	return subcarpeta("db") / nombre


def ruta_cache() -> Path:
	"""Carpeta de caché (se puede borrar sin perder nada importante)."""
	return subcarpeta("cache")


def ruta_temporales() -> Path:
	"""Carpeta de temporales de la aplicación (se vacía al cerrar)."""
	return subcarpeta("temp")


def ruta_copias() -> Path:
	"""Carpeta de copias de seguridad."""
	return subcarpeta("backups")


def ruta_skills() -> Path:
	"""Carpeta donde se crean las skills nuevas del gestor de skills."""
	return subcarpeta("skills")


# ── Recursos que viajan con la distribución ───────────────────────────────────

def ruta_skills_ejemplo() -> Path:
	"""Skills de ejemplo que trae el programa (``data/skills_ejemplo`` junto al ejecutable).

	Es un recurso de la instalación, no de los datos de la persona: se lee de la
	carpeta del programa aunque ``APP_DATA_DIR`` lleve los datos a otro sitio, y no se crea.
	"""
	return ruta_base() / "data" / "skills_ejemplo"


def ruta_copia_skills_ejemplo() -> Path:
	"""Copia de las skills de ejemplo FUERA de ``data`` (``copia_skills_ejemplo`` junto al ejecutable).

	El actualizador de cada versión solo copia las carpetas de ``data`` que conoce, y hasta
	la 1.5.0 no conocía ``skills_ejemplo``; el programa, en cambio, se copia siempre. Al
	arrancar, ``proteccion_skills`` pone desde aquí ``data/skills_ejemplo`` si falta. Solo
	existe en la versión compilada (``setup_cx.py``).
	"""
	return ruta_base() / "copia_skills_ejemplo"


def ruta_binarios() -> Path:
	"""Carpeta de binarios auxiliares (ffmpeg, aria2c...)."""
	return subcarpeta("bin")


def ruta_sonidos() -> Path:
	"""Carpeta de sonidos."""
	return subcarpeta("sounds")


def ruta_traducciones() -> Path:
	"""Carpeta de catálogos gettext: ``data/locales/<idioma>/LC_MESSAGES``."""
	return subcarpeta("locales")


def ruta_documentacion() -> Path:
	"""Carpeta del manual de usuario y la lista de teclas."""
	return subcarpeta("docs")


def ruta_iconos() -> Path:
	"""Carpeta de iconos."""
	return subcarpeta("icons")


# ── Utilidades ───────────────────────────────────────────────────────────────

def buscar_binario(nombre: str) -> str | None:
	"""Localiza un ejecutable auxiliar: primero en ``data/bin``, luego en el PATH.

	:param nombre: nombre sin extensión, por ejemplo ``ffmpeg``.
	:return: ruta completa o ``None`` si no se encuentra.
	"""
	carpeta = ruta_binarios()
	for candidato in (carpeta / f"{nombre}.exe", carpeta / nombre / f"{nombre}.exe"):
		if candidato.is_file():
			return str(candidato)
	return shutil.which(nombre)


def anadir_binarios_al_path() -> None:
	"""Antepone ``data/bin`` al PATH del proceso (no al del sistema).

	Para bibliotecas que buscan ffmpeg u otros binarios por su cuenta.
	"""
	carpeta = str(ruta_binarios())
	actual = os.environ.get("PATH", "")
	if carpeta not in actual.split(os.pathsep):
		os.environ["PATH"] = carpeta + os.pathsep + actual


def vaciar_temporales() -> None:
	"""Vacía ``data/temp``. Se llama al cerrar; ignora archivos en uso."""
	carpeta = ruta_datos() / "temp"
	if not carpeta.is_dir():
		return
	for elemento in carpeta.iterdir():
		try:
			if elemento.is_dir():
				shutil.rmtree(elemento, ignore_errors=True)
			else:
				elemento.unlink()
		except OSError:
			# Archivo en uso por otro proceso: se borrará en el próximo cierre.
			continue
