"""Guion de compilación con cx_Freeze.

Genera una distribución portable de 64 bits en ``build/<NOMBRE_APP>`` con:

- El ejecutable principal sin consola (``base="gui"``).
- Los runtimes de Microsoft Visual C++ (``include_msvcr``), para que arranque
  en un Windows recién instalado sin el redistribuible.
- Una carpeta ``data`` LIMPIA: solo lo imprescindible para arrancar
  (binarios, sonidos por defecto, traducciones, manual, iconos, plugins).
  Nunca la configuración, los registros, la base de datos ni nada generado
  durante las pruebas del desarrollador.

Uso (lo invoca ``entorno.bat``, opción Compilar):

    python setup_cx.py build            → versión final, sin consola
    python setup_cx.py build --debug    → misma versión, pero CON consola

PLANTILLA del agente «entorno-virtual»: ajusta la sección CONFIGURACIÓN y
deja el resto igual salvo que el proyecto necesite algo especial.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from cx_Freeze import Executable, setup

RAIZ = Path(__file__).resolve().parent

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DEL PROYECTO (lo único que normalmente hay que tocar)
# ════════════════════════════════════════════════════════════════════════════

# Nombre visible y nombre de la carpeta de salida (build/<NOMBRE_APP>).
NOMBRE_APP = "MiAplicacion"
# Nombre del ejecutable que verá el usuario.
EJECUTABLE = "MiAplicacion.exe"
# Script de entrada.
SCRIPT = "main.py"
DESCRIPCION = "Aplicación accesible para Windows"
AUTOR = ""
# Icono opcional; si no existe, se compila sin icono.
ICONO = RAIZ / "data" / "icons" / "app.ico"

# Subcarpetas de data/ que SÍ viajan en la distribución. Todo lo demás se
# queda fuera por defecto (lista blanca: lo nuevo no se cuela sin querer).
DATA_DISTRIBUIBLE = [
	"bin",       # ffmpeg, aria2c... binarios auxiliares
	"sounds",    # sonidos por defecto
	"locales",   # traducciones .mo (data/locales/<idioma>/LC_MESSAGES/*.mo)
	"docs",      # manual de usuario y lista de teclas (no la doc de desarrollo)
	"icons",     # iconos de la interfaz
	"plugins",   # complementos incluidos de fábrica
]

# Carpetas de data/ que JAMÁS pueden aparecer en la distribución. Si alguien
# las añade a la lista blanca por error, la compilación se detiene.
DATA_PROHIBIDO = {
	"config", "logs", "db", "database", "cache", "temp", "tmp", "sessions",
	"backups", "downloads", "descargas", "claves", "keys", "crash", "diagnostico",
}

# Archivos que nunca se copian aunque estén dentro de una carpeta distribuible.
PATRONES_EXCLUIDOS = (
	"__pycache__", "*.pyc", "*.pyo", "*.log", "*.jsonl", "*.tmp", "*.bak",
	"*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.key", "*.enc", "desktop.ini",
	"Thumbs.db", ".DS_Store",
)

# Archivos sueltos que se copian desde fuera de data/ (origen → destino).
ARCHIVOS_EXTRA: list[tuple[str, str]] = [
	# ("docs/manual.md", "data/docs/manual.md"),
	# ("LICENSE", "LICENSE.txt"),
]

# Paquetes que cx_Freeze debe incluir completos (los que carga de forma
# dinámica y su análisis no detecta). Los opcionales se añaden solo si están
# instalados, para no romper la compilación.
PAQUETES = [
	"wx",
	"app",
]
PAQUETES_OPCIONALES = [
	"accessible_output2",
]
INCLUDES: list[str] = []
EXCLUDES = [
	"tkinter", "unittest", "test", "pydoc_data",
	# Herramientas de desarrollo que no deben viajar en el ejecutable.
	"pip", "pytest", "_pytest", "ruff", "flake8", "pyflakes", "pycodestyle",
	"cx_Freeze", "wheel", "polib",
]

# ════════════════════════════════════════════════════════════════════════════
# A PARTIR DE AQUÍ NO SUELE HACER FALTA TOCAR NADA
# ════════════════════════════════════════════════════════════════════════════

DESTINO = RAIZ / "build" / NOMBRE_APP


def leer_version() -> str:
	"""Lee APP_VERSION de app/core/version.py sin importar la aplicación.

	Importarla arrastraría wx y la configuración solo para saber un número;
	con una expresión regular basta y no hay efectos secundarios.
	"""
	ruta = RAIZ / "app" / "core" / "version.py"
	try:
		texto = ruta.read_text(encoding="utf-8")
	except OSError:
		print(f"AVISO: no se encontró {ruta}; se usa la versión 0.0.0.")
		return "0.0.0"
	coincidencia = re.search(r"APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", texto)
	return coincidencia.group(1) if coincidencia else "0.0.0"


def modulo_disponible(nombre: str) -> bool:
	"""Indica si un módulo está instalado en el entorno virtual."""
	import importlib.util

	return importlib.util.find_spec(nombre) is not None


def _excluido(ruta: Path) -> bool:
	"""Indica si un archivo o carpeta coincide con algún patrón excluido."""
	return any(ruta.match(patron) for patron in PATRONES_EXCLUIDOS)


def archivos_de_data() -> list[tuple[str, str]]:
	"""Construye la lista include_files de data/ ARCHIVO A ARCHIVO.

	cx_Freeze copiaría una carpeta entera tal cual (con __pycache__, registros
	o bases de datos que hubiera dentro); recorriéndola a mano se filtra cada
	archivo con PATRONES_EXCLUIDOS.
	"""
	prohibidas = DATA_PROHIBIDO.intersection(DATA_DISTRIBUIBLE)
	if prohibidas:
		raise SystemExit(
			"ERROR: DATA_DISTRIBUIBLE incluye carpetas con datos personales: "
			+ ", ".join(sorted(prohibidas))
		)
	data = RAIZ / "data"
	resultado: list[tuple[str, str]] = []
	for nombre in DATA_DISTRIBUIBLE:
		carpeta = data / nombre
		if not carpeta.is_dir():
			print(f"Aviso: data/{nombre} no existe; no se incluye.")
			continue
		for archivo in sorted(carpeta.rglob("*")):
			if not archivo.is_file():
				continue
			relativo = archivo.relative_to(RAIZ)
			if any(_excluido(Path(parte)) for parte in relativo.parts) or _excluido(archivo):
				continue
			resultado.append((str(archivo), relativo.as_posix()))
	return resultado


def archivos_extra() -> list[tuple[str, str]]:
	"""Valida y devuelve los archivos sueltos de ARCHIVOS_EXTRA."""
	resultado = []
	for origen, destino in ARCHIVOS_EXTRA:
		ruta = RAIZ / origen
		if ruta.exists():
			resultado.append((str(ruta), destino))
		else:
			print(f"Aviso: no se encontró {origen}; no se incluye.")
	return resultado


def limpiar_destino() -> None:
	"""Borra la compilación anterior para que no se cuelen restos.

	Si la copia compilada se ejecutó para probarla, su data/ contiene ya
	configuración, registros y base de datos: compilar encima los arrastraría
	a la distribución.
	"""
	if DESTINO.exists():
		print(f"Limpiando la compilación anterior: {DESTINO}")
		shutil.rmtree(DESTINO, ignore_errors=False)


# ── Preparación ──────────────────────────────────────────────────────────────

MODO_DEBUG = "--debug" in sys.argv
if MODO_DEBUG:
	sys.argv.remove("--debug")

COMPILANDO = any(comando.startswith("build") for comando in sys.argv[1:])
if COMPILANDO:
	limpiar_destino()

VERSION = leer_version()
INCLUIR = archivos_de_data() + archivos_extra()
PAQUETES_FINALES = PAQUETES + [p for p in PAQUETES_OPCIONALES if modulo_disponible(p)]
for opcional in PAQUETES_OPCIONALES:
	if not modulo_disponible(opcional):
		print(f"Aviso: el paquete opcional {opcional} no está instalado; no se incluye.")

OPCIONES_BUILD = {
	"build_exe": str(DESTINO),
	"packages": PAQUETES_FINALES,
	"includes": INCLUDES,
	"excludes": EXCLUDES,
	"include_files": INCLUIR,
	# Runtimes de Visual C++ junto al ejecutable. cx_Freeze descarga el
	# vc_redist oficial (versión 17), extrae sus DLL (vcruntime140.dll,
	# vcruntime140_1.dll, msvcp140*.dll...) y deja la licencia en
	# share/licenses/vc_redist. Así arranca sin instalar el redistribuible.
	"include_msvcr": True,
	# Las trazas muestran rutas relativas (app/ui/ventana.py) en lugar de las
	# de la máquina donde se compiló: más legibles y sin datos personales.
	"replace_paths": [("*", "")],
	# optimize=2 elimina las docstrings y rompe bibliotecas que las usan
	# (clientes de API que leen docstrings, argparse...). 1 es seguro.
	"optimize": 1,
}

EJECUTABLES = [
	Executable(
		script=str(RAIZ / SCRIPT),
		# "gui" = sin consola (antes "Win32GUI"). En modo --debug, consola.
		base=None if MODO_DEBUG else "gui",
		target_name=EJECUTABLE,
		icon=str(ICONO) if ICONO.is_file() else None,
		copyright=AUTOR or None,
	),
]

setup(
	name=NOMBRE_APP,
	version=VERSION,
	description=DESCRIPCION,
	author=AUTOR or None,
	options={"build_exe": OPCIONES_BUILD},
	executables=EJECUTABLES,
)

# ── Verificación posterior ───────────────────────────────────────────────────
# Solo tras un build real: comprueba que la distribución está completa y limpia.
if COMPILANDO and (RAIZ / "tools" / "verificar_build.py").is_file():
	resultado = subprocess.run(
		[sys.executable, str(RAIZ / "tools" / "verificar_build.py"), str(DESTINO), EJECUTABLE],
		check=False,
	)
	if resultado.returncode != 0:
		raise SystemExit("La verificación de la compilación ha fallado; revise los mensajes anteriores.")
