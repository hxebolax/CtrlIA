"""Extrae las notas de una versión del registro de cambios (para publicarlas) y comprueba que existen.

Uso (desde la raíz del proyecto)::

	python tools/notas_version.py                 # notas de APP_VERSION en pantalla
	python tools/notas_version.py --salida build/notas.md
	python tools/notas_version.py --version 1.2.0

Termina con código 1 si la versión no tiene entrada en ``docs/changelog.md``
o si la entrada está vacía: así el paso de publicar se detiene antes de
distribuir una versión sin notas para las personas usuarias.

Solo biblioteca estándar. PLANTILLA del agente «documentacion-usuario».
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
	sys.path.insert(0, str(RAIZ))

from app.core import documentacion  # noqa: E402

_RE_APP_VERSION = re.compile(r"^APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def version_del_proyecto(raiz: Path) -> str | None:
	"""Lee ``APP_VERSION`` de ``app/core/version.py`` sin importar la aplicación."""
	try:
		coincidencia = _RE_APP_VERSION.search((raiz / "app" / "core" / "version.py").read_text(encoding="utf-8"))
	except OSError:
		return None
	return coincidencia.group(1) if coincidencia else None


def main(argumentos: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Muestra o guarda las notas de una versión del registro de cambios.")
	analizador.add_argument("--raiz", type=Path, default=RAIZ, help="Raíz del proyecto.")
	analizador.add_argument("--version", help="Versión (por defecto, APP_VERSION de app/core/version.py).")
	analizador.add_argument("--changelog", type=Path, help="Registro de cambios (por defecto, docs/changelog.md).")
	analizador.add_argument("--salida", type=Path, help="Archivo donde guardar las notas en Markdown.")
	opciones = analizador.parse_args(argumentos)
	for flujo in (sys.stdout, sys.stderr):
		if hasattr(flujo, "reconfigure"):
			flujo.reconfigure(errors="replace")
	raiz = opciones.raiz.resolve()
	version = opciones.version or version_del_proyecto(raiz)
	if not version:
		print("No se pudo leer APP_VERSION de app/core/version.py. Indique --version.")
		return 1
	ruta = opciones.changelog or raiz / "docs" / documentacion.CHANGELOG
	try:
		texto = ruta.read_text(encoding="utf-8-sig")
	except OSError as error:
		print(f"No se pudo leer el registro de cambios {ruta}: {error}")
		return 1
	entrada = documentacion.notas_de_version(texto, version)
	if entrada is None:
		print(f"La versión {version} no tiene entrada en {ruta}. Añada «## Versión {version} - AAAA-MM-DD» con sus cambios.")
		return 1
	if not entrada.texto.strip():
		print(f"La entrada de la versión {version} está vacía.")
		return 1
	notas = f"# {entrada.encabezado}\n\n{entrada.texto}\n"
	if opciones.salida:
		opciones.salida.parent.mkdir(parents=True, exist_ok=True)
		opciones.salida.write_text(notas, encoding="utf-8")
		print(f"Notas de la versión {version} guardadas en {opciones.salida}.")
	else:
		print(notas)
	return 0


if __name__ == "__main__":
	sys.exit(main())
