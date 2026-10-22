"""Verifica que una compilación de cx_Freeze está completa y LIMPIA.

Comprueba, sin ejecutar la aplicación:

1. Que existe el ejecutable.
2. Que viajan los runtimes de Visual C++ (``include_msvcr``).
3. Que ``data/`` no contiene nada personal ni generado durante las pruebas
   (configuración, registros, bases de datos, sesiones, claves...).
4. Que no se han colado cachés de Python ni herramientas de desarrollo.

Uso:
    python tools/verificar_build.py build/MiAplicacion MiAplicacion.exe

Devuelve 0 si todo está bien y 1 si hay algún problema (lo usa setup_cx.py
y entorno.bat para detener el empaquetado).

PLANTILLA del agente «entorno-virtual».
"""

from __future__ import annotations

import sys
from pathlib import Path

# Carpetas de data/ que nunca deben distribuirse (mismo criterio que setup_cx.py).
DATA_PROHIBIDO = {
	"config", "logs", "db", "database", "cache", "temp", "tmp", "sessions",
	"backups", "downloads", "descargas", "claves", "keys", "crash", "diagnostico",
}
# Extensiones que delatan datos generados en tiempo de ejecución.
EXTENSIONES_SOSPECHOSAS = {".log", ".jsonl", ".db", ".db-wal", ".db-shm", ".sqlite", ".key", ".enc", ".bak", ".tmp"}
# Runtimes de Visual C++ que include_msvcr copia junto al ejecutable. En x64,
# Python y wxPython de 64 bits necesitan además vcruntime140_1.dll.
RUNTIMES_VC = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
# Recursos que deben viajar si el paquete correspondiente está en la compilación
# (ruta del paquete dentro de lib/ → archivo que tiene que existir).
RECURSOS_DE_PAQUETES = {
	"wx": "wx/locale/es/LC_MESSAGES/wxstd.mo",               # botones estándar en español
	"accessible_output2": "accessible_output2/lib/nvdaControllerClient64.dll",  # voz con NVDA
	"certifi": "certifi/cacert.pem",                          # certificados HTTPS de requests
}
# Paquetes de desarrollo que no deberían viajar.
PAQUETES_DESARROLLO = ("pytest", "_pytest", "pip", "ruff", "cx_Freeze")


def _tamano_legible(bytes_: int) -> str:
	"""Formatea un tamaño en MB con un decimal."""
	return f"{bytes_ / (1024 * 1024):.1f} MB"


def verificar(destino: Path, ejecutable: str) -> list[str]:
	"""Devuelve la lista de problemas encontrados (vacía si todo está bien)."""
	problemas: list[str] = []
	if not destino.is_dir():
		return [f"No existe la carpeta de compilación: {destino}"]

	if not (destino / ejecutable).is_file():
		problemas.append(f"Falta el ejecutable {ejecutable}.")

	presentes = {p.name.lower() for p in destino.glob("*.dll")}
	presentes |= {p.name.lower() for p in (destino / "lib").glob("*.dll")} if (destino / "lib").is_dir() else set()
	faltan = [dll for dll in RUNTIMES_VC if dll not in presentes]
	if faltan:
		problemas.append(
			"Faltan runtimes de Visual C++ (" + ", ".join(faltan) + "). "
			"Compruebe que setup_cx.py tiene include_msvcr=True."
		)

	data = destino / "data"
	if data.is_dir():
		for carpeta in data.iterdir():
			if carpeta.is_dir() and carpeta.name.lower() in DATA_PROHIBIDO:
				problemas.append(f"data/{carpeta.name} NO debe distribuirse (datos personales o generados).")
		for archivo in data.rglob("*"):
			if archivo.is_file() and archivo.suffix.lower() in EXTENSIONES_SOSPECHOSAS:
				problemas.append(f"Archivo sospechoso en la distribución: {archivo.relative_to(destino)}")
			if archivo.is_dir() and archivo.name == "__pycache__":
				problemas.append(f"Caché de Python en la distribución: {archivo.relative_to(destino)}")

	lib = destino / "lib"
	if lib.is_dir():
		for paquete, recurso in RECURSOS_DE_PAQUETES.items():
			if (lib / paquete).is_dir() and not (lib / recurso).is_file():
				problemas.append(
					f"Falta lib/{recurso}: el paquete {paquete} no funcionará bien "
					"(¿está en zip_include_packages? Los paquetes con DLL o datos deben ir fuera del zip)."
				)
		for paquete in PAQUETES_DESARROLLO:
			if (lib / paquete).exists():
				problemas.append(f"Herramienta de desarrollo incluida: lib/{paquete} (añádala a EXCLUDES).")
	return problemas


def main(argumentos: list[str]) -> int:
	"""Punto de entrada de línea de comandos."""
	if len(argumentos) < 2:
		print("Uso: python tools/verificar_build.py <carpeta_build> <ejecutable.exe>")
		return 2
	destino = Path(argumentos[0]).resolve()
	ejecutable = argumentos[1]
	print(f"Verificando la compilación en {destino}...")
	problemas = verificar(destino, ejecutable)
	if problemas:
		print(f"Se encontraron {len(problemas)} problemas:")
		for problema in problemas:
			print(f"  - {problema}")
		return 1
	total = sum(p.stat().st_size for p in destino.rglob("*") if p.is_file())
	archivos = sum(1 for p in destino.rglob("*") if p.is_file())
	print(f"Compilación correcta y limpia: {archivos} archivos, {_tamano_legible(total)}.")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
