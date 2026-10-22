"""Documentos de usuario (manual, teclas, novedades) y aviso de novedades tras actualizar.

Sin wx: lo usan el visor de ayuda (``app/ui/visor_ayuda.py``), las pruebas y
las herramientas de publicación.

Dónde se buscan los documentos, por orden:

1. ``data/docs/<nombre>``: donde están en la versión compilada (junto al
   ``.exe``) y donde los deja la compilación.
2. ``docs/<nombre>``: el original en el proyecto, al ejecutar desde código.

La última versión cuyas novedades se mostraron se guarda en
``data/config/novedades.json``. ``APP_DATA_DIR`` redirige ``data`` (pruebas).

Formato del registro de cambios que se entiende (``docs/changelog.md``)::

	## Versión 1.2.0 - 2026-09-25
	### Novedades
	- ...

También vale ``## 1.2.0 (2026-09-25)`` o ``## v1.2.0``.

PLANTILLA del agente «documentacion-usuario».
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

VARIABLE_DATA = "APP_DATA_DIR"
MANUAL = "manual.md"
TECLAS = "teclas.md"
CHANGELOG = "changelog.md"
ARCHIVO_ESTADO = "novedades.json"

_RE_VERSION = re.compile(r"^##\s+(?:versi[oó]n\s+)?v?(\d+(?:\.\d+){0,3})\b\s*(.*)$", re.IGNORECASE)
_RE_TITULO = re.compile(r"^#\s+(.+?)\s*#*\s*$")


# ── Rutas ────────────────────────────────────────────────────────────────────

def ruta_base() -> Path:
	"""Carpeta del .exe (compilada) o de main.py (desde código: <raíz>/app/core/documentacion.py)."""
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent
	return Path(__file__).resolve().parent.parent.parent


def ruta_datos() -> Path:
	redireccion = os.environ.get(VARIABLE_DATA, "").strip()
	return Path(redireccion).expanduser() if redireccion else ruta_base() / "data"


def ruta_documento(nombre: str) -> Path | None:
	"""Primer ``data/docs/<nombre>`` o ``docs/<nombre>`` que exista; None si no hay ninguno."""
	for candidata in (ruta_datos() / "docs" / nombre, ruta_base() / "docs" / nombre):
		if candidata.is_file():
			return candidata
	return None


def ruta_estado() -> Path:
	return ruta_datos() / "config" / ARCHIVO_ESTADO


def leer_documento(nombre: str) -> str | None:
	"""Texto de un documento o None (se registra el motivo)."""
	ruta = ruta_documento(nombre)
	if ruta is None:
		log.warning("No se encontró el documento %s en data/docs ni en docs", nombre)
		return None
	try:
		return ruta.read_text(encoding="utf-8-sig")
	except (OSError, UnicodeDecodeError):
		log.exception("No se pudo leer %s", ruta)
		return None


def titulo_de(markdown: str, por_defecto: str) -> str:
	"""El primer encabezado de nivel 1, o el título por defecto."""
	for linea in markdown.splitlines():
		coincidencia = _RE_TITULO.match(linea.strip())
		if coincidencia:
			return coincidencia.group(1).strip()
	return por_defecto


# ── Registro de cambios ──────────────────────────────────────────────────────

@dataclass
class Version:
	numero: str
	encabezado: str  # La línea «## ...» completa, sin las almohadillas.
	texto: str  # El cuerpo hasta la siguiente versión.


def clave_version(numero: str) -> tuple[int, ...]:
	"""``"1.10.0"`` → ``(1, 10, 0, 0)``: se compara como números, no como texto."""
	partes = [int(p) for p in re.findall(r"\d+", numero)[:4]]
	return tuple(partes + [0] * (4 - len(partes)))


def leer_changelog(texto: str) -> list[Version]:
	"""Versiones del registro de cambios, en el orden del archivo."""
	versiones: list[Version] = []
	actual: Version | None = None
	cuerpo: list[str] = []
	for linea in texto.splitlines():
		coincidencia = _RE_VERSION.match(linea.strip())
		if coincidencia:
			if actual is not None:
				actual.texto = "\n".join(cuerpo).strip()
				versiones.append(actual)
			actual = Version(coincidencia.group(1), linea.strip().lstrip("#").strip(), "")
			cuerpo = []
		elif actual is not None:
			if linea.startswith("## "):
				# Otra sección de nivel 2 que no es una versión: termina la actual.
				actual.texto = "\n".join(cuerpo).strip()
				versiones.append(actual)
				actual = None
			else:
				cuerpo.append(linea)
	if actual is not None:
		actual.texto = "\n".join(cuerpo).strip()
		versiones.append(actual)
	return versiones


def notas_de_version(texto_changelog: str, numero: str) -> Version | None:
	"""La entrada de una versión concreta (para publicarla o comprobar que existe)."""
	buscada = clave_version(numero)
	return next((v for v in leer_changelog(texto_changelog) if clave_version(v.numero) == buscada), None)


def novedades_entre(versiones: list[Version], desde: str | None, hasta: str) -> list[Version]:
	"""Versiones posteriores a ``desde`` y hasta ``hasta`` incluida, de la más nueva a la más antigua."""
	minimo = clave_version(desde) if desde else (-1,)
	maximo = clave_version(hasta)
	elegidas = [v for v in versiones if minimo < clave_version(v.numero) <= maximo]
	return sorted(elegidas, key=lambda v: clave_version(v.numero), reverse=True)


def componer_novedades(versiones: list[Version], titulo: str) -> str:
	"""Documento Markdown con las novedades: un encabezado por versión."""
	partes = [f"# {titulo}"]
	for version in versiones:
		# Los ### del registro (Novedades, Mejoras...) quedan bajo el ## de su versión.
		partes.append(f"## {version.encabezado}\n\n{version.texto}".rstrip())
	return "\n\n".join(partes) + "\n"


# ── Última versión vista ─────────────────────────────────────────────────────

def leer_ultima_vista(ruta: Path | None = None) -> str | None:
	ruta = ruta or ruta_estado()
	try:
		datos = json.loads(ruta.read_text(encoding="utf-8"))
	except FileNotFoundError:
		return None
	except (OSError, ValueError):
		log.warning("No se pudo leer %s; se considera una instalación nueva", ruta, exc_info=True)
		return None
	valor = datos.get("ultima_version_vista") if isinstance(datos, dict) else None
	return valor if isinstance(valor, str) else None


def marcar_vista(version: str, ruta: Path | None = None) -> None:
	"""Guarda la versión cuyas novedades ya se mostraron (escritura atómica)."""
	ruta = ruta or ruta_estado()
	try:
		ruta.parent.mkdir(parents=True, exist_ok=True)
		descriptor, temporal = tempfile.mkstemp(prefix=ruta.name + ".", suffix=".tmp", dir=ruta.parent)
		with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
			json.dump({"ultima_version_vista": version}, archivo, ensure_ascii=False, indent="\t")
		os.replace(temporal, ruta)
	except OSError:
		# Si no se puede guardar, las novedades volverán a salir: molesto, no grave.
		log.warning("No se pudo guardar la última versión vista en %s", ruta, exc_info=True)


def novedades_pendientes(
	version_actual: str, texto_changelog: str | None = None, ruta: Path | None = None
) -> tuple[str, list[Version]] | None:
	"""Novedades que hay que mostrar tras una actualización, o None.

	- Instalación nueva (sin estado guardado): no se muestran; se anota la
	versión para avisar en la próxima actualización.
	- Misma versión o anterior: nada que mostrar.
	- Versión nueva sin entrada en el registro: se anota y no se muestra nada.

	:return: (versión desde la que se actualiza, versiones a mostrar).
	"""
	ultima = leer_ultima_vista(ruta)
	if ultima is None:
		marcar_vista(version_actual, ruta)
		return None
	if clave_version(ultima) >= clave_version(version_actual):
		if clave_version(ultima) > clave_version(version_actual):
			# Se volvió a una versión anterior: se anota para avisar al subir de nuevo.
			marcar_vista(version_actual, ruta)
		return None
	if texto_changelog is None:
		texto_changelog = leer_documento(CHANGELOG) or ""
	versiones = novedades_entre(leer_changelog(texto_changelog), ultima, version_actual)
	if not versiones:
		log.info("Versión %s sin entrada en el registro de cambios", version_actual)
		marcar_vista(version_actual, ruta)
		return None
	return ultima, versiones
