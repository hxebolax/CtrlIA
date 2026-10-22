"""Compilación con cx_Freeze: ``python setup_cx.py build`` (``--debug`` = con consola).

Genera ``build/MiAplicacion`` con:

- El ejecutable sin consola (``base="gui"``).
- Los runtimes de Visual C++ (``include_msvcr``), para que arranque en un
  Windows recién instalado sin el redistribuible.
- Una carpeta ``data`` LIMPIA por lista blanca: solo lo que hace falta para
  arrancar. Nunca configuración, registros ni nada generado al probar.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from cx_Freeze import Executable, setup

RAIZ = Path(__file__).resolve().parent

# ── CONFIGURACIÓN DEL PROYECTO ───────────────────────────────────────────────

NOMBRE_APP = "MiAplicacion"
EJECUTABLE = "MiAplicacion.exe"
DESCRIPCION = "Aplicación accesible para Windows"
ICONO = RAIZ / "data" / "icons" / "app.ico"

# Subcarpetas de data/ que SÍ se distribuyen (lista blanca).
DATA_DISTRIBUIBLE = ["bin", "sounds", "locales", "docs", "icons", "plugins"]
# Subcarpetas que JAMÁS pueden distribuirse: si alguien las añade arriba, se para.
DATA_PROHIBIDO = {"config", "logs", "db", "cache", "temp", "sessions", "backups", "downloads"}
# Archivos que nunca se copian aunque estén en una carpeta distribuible.
PATRONES_EXCLUIDOS = ("__pycache__", "*.pyc", "*.log", "*.tmp", "*.bak", "*.db", "*.key", ".gitkeep", "desktop.ini", "Thumbs.db")

PAQUETES = ["wx", "app"]
PAQUETES_OPCIONALES = ["accessible_output2"]
EXCLUDES = ["tkinter", "unittest", "pydoc_data", "pip", "pytest", "_pytest", "ruff", "cx_Freeze"]

# ─────────────────────────────────────────────────────────────────────────────

DESTINO = RAIZ / "build" / NOMBRE_APP


def leer_version() -> str:
	"""Lee APP_VERSION con una expresión regular: importar la app arrastraría wx."""
	texto = (RAIZ / "app" / "core" / "version.py").read_text(encoding="utf-8")
	coincidencia = re.search(r"APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", texto)
	return coincidencia.group(1) if coincidencia else "0.0.0"


def _excluido(ruta: Path) -> bool:
	return any(Path(parte).match(patron) for parte in ruta.parts for patron in PATRONES_EXCLUIDOS)


def archivos_de_data() -> list[tuple[str, str]]:
	"""Lista ``include_files`` de data/ archivo a archivo, filtrando lo excluido."""
	prohibidas = DATA_PROHIBIDO.intersection(DATA_DISTRIBUIBLE)
	if prohibidas:
		raise SystemExit("ERROR: DATA_DISTRIBUIBLE incluye carpetas personales: " + ", ".join(sorted(prohibidas)))
	resultado = []
	for nombre in DATA_DISTRIBUIBLE:
		carpeta = RAIZ / "data" / nombre
		for archivo in sorted(carpeta.rglob("*")) if carpeta.is_dir() else []:
			relativo = archivo.relative_to(RAIZ)
			if archivo.is_file() and not _excluido(relativo):
				resultado.append((str(archivo), relativo.as_posix()))
	return resultado


def _disponible(modulo: str) -> bool:
	import importlib.util

	return importlib.util.find_spec(modulo) is not None


if __name__ == "__main__":
	MODO_DEBUG = "--debug" in sys.argv
	if MODO_DEBUG:
		sys.argv.remove("--debug")
	if any(orden.startswith("build") for orden in sys.argv[1:]) and DESTINO.exists():
		# Si se probó la copia compilada, su data/ tiene configuración y registros.
		print(f"Limpiando la compilación anterior: {DESTINO}")
		shutil.rmtree(DESTINO)

	setup(
		name=NOMBRE_APP,
		version=leer_version(),
		description=DESCRIPCION,
		options={
			"build_exe": {
				"build_exe": str(DESTINO),
				"packages": PAQUETES + [p for p in PAQUETES_OPCIONALES if _disponible(p)],
				"excludes": EXCLUDES,
				"include_files": archivos_de_data(),
				"include_msvcr": True,
				# Trazas con rutas relativas, sin las carpetas del equipo donde se compiló.
				"replace_paths": [("*", "")],
				# optimize=2 quita las docstrings y rompe bibliotecas que las leen.
				"optimize": 1,
			}
		},
		executables=[
			Executable(
				script=str(RAIZ / "main.py"),
				base=None if MODO_DEBUG else "gui",
				target_name=EJECUTABLE,
				icon=str(ICONO) if ICONO.is_file() else None,
			)
		],
	)
