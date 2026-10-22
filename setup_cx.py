"""Compilación con cx_Freeze: ``python setup_cx.py build`` (``--debug`` = con consola).

Genera ``build/CtrlIA`` con:

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

NOMBRE_APP = "CtrlIA"
EJECUTABLE = "CtrlIA.exe"
# Puente MCP de las herramientas de Ctrl+IA (app/puente_mcp.py); el nombre está también en
# app/services/herramientas_propias.py (EJECUTABLE_PUENTE).
EJECUTABLE_PUENTE = "ctrlia-mcp.exe"
DESCRIPCION = "Ctrl+IA: chat accesible con agentes de IA de programación"
ICONO = RAIZ / "data" / "icons" / "app.ico"

# Subcarpetas de data/ que SÍ se distribuyen (lista blanca).
DATA_DISTRIBUIBLE = ["bin", "sounds", "locales", "docs", "icons", "plugins", "skills_ejemplo"]
# Subcarpetas que JAMÁS pueden distribuirse: si alguien las añade arriba, se para.
DATA_PROHIBIDO = {"config", "logs", "db", "cache", "temp", "sessions", "backups", "downloads"}
# Copia de data/skills_ejemplo FUERA de data/ (el nombre está también en app/core/paths.py,
# ruta_copia_skills_ejemplo): los actualizadores de la 1.5.0 y anteriores no copian esa carpeta de data,
# pero el programa sí; al arrancar, si falta, se pone desde aquí.
COPIA_EJEMPLOS = "copia_skills_ejemplo"
# Archivos que nunca se copian aunque estén en una carpeta distribuible.
PATRONES_EXCLUIDOS = ("__pycache__", ".ruff_cache", ".pytest_cache", "*.pyc", "*.log", "*.tmp", "*.bak", "*.db", "*.key", ".gitkeep", "desktop.ini", "Thumbs.db")

PAQUETES = ["wx", "app", "markdown", "requests", "certifi", "cryptography", "tomlkit"]
# accessible_output2 se importa de forma diferida: sus dependencias hay que nombrarlas.
PAQUETES_OPCIONALES = ["accessible_output2", "libloader", "platform_utils", "comtypes", "win32com", "pythoncom"]
# Documentación de usuario: la fuente está en docs/ y viaja a data/docs/.
ARCHIVOS_EXTRA = [(f"docs/{nombre}", f"data/docs/{nombre}") for nombre in ("manual.md", "teclas.md", "changelog.md")]
EXCLUDES = ["tkinter", "unittest", "pydoc_data", "pip", "pytest", "_pytest", "ruff", "cx_Freeze", "tests"]

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


def archivos_copia_ejemplos() -> list[tuple[str, str]]:
	"""Los de ``data/skills_ejemplo`` otra vez, en ``COPIA_EJEMPLOS`` (fuera de data/)."""
	prefijo = "data/skills_ejemplo/"
	return [(origen, COPIA_EJEMPLOS + "/" + destino[len(prefijo):]) for origen, destino in archivos_de_data()
		if destino.startswith(prefijo)]


def corregir_abi_estable(destino: Path) -> list[str]:
	"""Pone junto al ejecutable las DLL del ABI estable que corresponden a ESTE Python.

	Python 3.15 trae dos ``python3t.dll``: el de su carpeta raíz reenvía a
	``python315t.dll`` (el intérprete sin GIL) y el de ``abi3t-compat`` reenvía a
	``python315.dll`` (el normal). cx_Freeze copia el de la raíz, así que las
	extensiones «abi3t», como ``cryptography`` (verifica la firma de las
	actualizaciones), no cargan en la versión compilada: «DLL load failed».
	También se añade ``python3.dll`` si falta (extensiones «abi3»).
	"""
	import sysconfig

	if sysconfig.get_config_var("Py_GIL_DISABLED"):
		return []  # en un Python sin GIL, los de la raíz son los correctos
	base = Path(sys.base_prefix)
	hechos = []
	compat = base / "abi3t-compat" / "python3t.dll"
	if compat.is_file():
		shutil.copy2(compat, destino / "python3t.dll")
		hechos.append("python3t.dll (de abi3t-compat)")
	python3 = base / "python3.dll"
	if python3.is_file() and not (destino / "python3.dll").exists():
		shutil.copy2(python3, destino / "python3.dll")
		hechos.append("python3.dll")
	return hechos


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
				"include_files": archivos_de_data() + archivos_copia_ejemplos()
					+ [(str(RAIZ / o), d) for o, d in ARCHIVOS_EXTRA if (RAIZ / o).is_file()],
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
			),
			# Puente MCP por stdio de las herramientas de Ctrl+IA (lo lanzan los agentes ACP sin HTTP y
			# Antigravity): de consola, porque habla por su entrada y su salida estándar.
			Executable(
				script=str(RAIZ / "app" / "puente_mcp.py"),
				base=None,
				target_name=EJECUTABLE_PUENTE,
				icon=str(ICONO) if ICONO.is_file() else None,
			),
		],
	)
	if any(orden.startswith("build") for orden in sys.argv[1:]) and DESTINO.is_dir():
		for hecho in corregir_abi_estable(DESTINO):
			print(f"ABI estable: copiado {hecho}")
