"""Empaqueta la compilación en un ZIP con la versión en el nombre.

Toma ``build/<NOMBRE_APP>`` (generado por ``setup_cx.py``) y crea, dentro de
``build/``:

- ``<NOMBRE_APP>_v<versión>.zip`` con la máxima compresión. Dentro del ZIP
  todo cuelga de una carpeta ``<NOMBRE_APP>/`` para que, al descomprimir, no
  se esparzan archivos sueltos.
- ``version.json`` con la versión, el SHA-256, el tamaño y la fecha: es lo que
  necesita un actualizador para saber si hay versión nueva y VERIFICAR la
  descarga antes de instalarla.

Antes de comprimir vuelve a pasar ``verificar_build.py``: nunca se empaqueta
una compilación con datos personales.

Si existe ``tools/firmar_version.py`` con una función ``firmar(clave, json)``
y la clave privada indicada en CLAVE_FIRMA, se firma el manifiesto.

PLANTILLA del agente «entorno-virtual».
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# ── CONFIGURACIÓN ────────────────────────────────────────────────────────────
NOMBRE_APP = "MiAplicacion"
EJECUTABLE = "MiAplicacion.exe"
# Clave privada opcional para firmar version.json (NUNCA se versiona ni distribuye).
CLAVE_FIRMA = RAIZ / "tools" / "claves" / "firma_ed25519.key"
# ─────────────────────────────────────────────────────────────────────────────

ORIGEN = RAIZ / "build" / NOMBRE_APP


def leer_version() -> str:
	"""Lee APP_VERSION de app/core/version.py sin importar la aplicación."""
	try:
		texto = (RAIZ / "app" / "core" / "version.py").read_text(encoding="utf-8")
	except OSError:
		return "0.0.0"
	coincidencia = re.search(r"APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", texto)
	return coincidencia.group(1) if coincidencia else "0.0.0"


def sha256_de(ruta: Path) -> str:
	"""Calcula el SHA-256 de un archivo leyendo por bloques de 1 MB."""
	resumen = hashlib.sha256()
	with ruta.open("rb") as archivo:
		for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
			resumen.update(bloque)
	return resumen.hexdigest()


def comprimir(origen: Path, destino: Path, carpeta_interna: str) -> int:
	"""Comprime ``origen`` en ``destino`` y devuelve el número de archivos."""
	total = 0
	with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zip_:
		for archivo in sorted(origen.rglob("*")):
			if archivo.is_file():
				interno = Path(carpeta_interna) / archivo.relative_to(origen)
				zip_.write(archivo, interno.as_posix())
				total += 1
	return total


def firmar_si_procede(ruta_json: Path) -> None:
	"""Firma el manifiesto si el proyecto tiene firmador y clave; si no, avisa."""
	firmador = RAIZ / "tools" / "firmar_version.py"
	if not firmador.is_file():
		return
	if not CLAVE_FIRMA.is_file():
		raise SystemExit(
			f"ERROR: existe {firmador.name} pero falta la clave {CLAVE_FIRMA}. "
			"Sin firma, el actualizador rechazaría el paquete."
		)
	sys.path.insert(0, str(firmador.parent))
	try:
		from firmar_version import firmar  # type: ignore[import-not-found]

		firmar(str(CLAVE_FIRMA), str(ruta_json))
		print("version.json firmado.")
	finally:
		sys.path.remove(str(firmador.parent))


def main() -> int:
	"""Verifica, comprime y genera el manifiesto de la versión."""
	if not ORIGEN.is_dir():
		print(f"No existe {ORIGEN}. Compile primero con setup_cx.py.")
		return 1

	sys.path.insert(0, str(Path(__file__).resolve().parent))
	from verificar_build import verificar

	problemas = verificar(ORIGEN, EJECUTABLE)
	if problemas:
		print("No se empaqueta: la compilación no está limpia.")
		for problema in problemas:
			print(f"  - {problema}")
		return 1

	version = leer_version()
	zip_destino = RAIZ / "build" / f"{NOMBRE_APP}_v{version}.zip"
	zip_destino.unlink(missing_ok=True)
	print(f"Comprimiendo {ORIGEN.name} en {zip_destino.name} (máxima compresión)...")
	archivos = comprimir(ORIGEN, zip_destino, NOMBRE_APP)
	tamano = zip_destino.stat().st_size
	print(f"Listo: {archivos} archivos, {tamano / (1024 * 1024):.1f} MB.")

	manifiesto = {
		"version": version,
		"file": zip_destino.name,
		"sha256": sha256_de(zip_destino),
		"size": tamano,
		"date": date.today().isoformat(),
	}
	ruta_json = RAIZ / "build" / "version.json"
	ruta_json.write_text(json.dumps(manifiesto, indent=4, ensure_ascii=False), encoding="utf-8")
	firmar_si_procede(ruta_json)
	print(f"Generado {ruta_json.name}. Para publicar, suba estos dos archivos:")
	print(f"  1) {zip_destino}")
	print(f"  2) {ruta_json}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
