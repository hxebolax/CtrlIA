"""Crea un proyecto nuevo copiando este esqueleto y poniéndole nombre.

Uso::

	python tools/crear_proyecto.py --destino "D:/Proyectos/Lector" --nombre "Lector accesible"
	python tools/crear_proyecto.py --destino ... --nombre ... --python 3.13 --id LectorAccesible --simular

Qué hace:

- Copia ``main.py``, ``app/``, ``data/`` (solo las carpetas distribuibles),
  ``tests/``, ``setup_cx.py``, ``entorno.bat``, ``pyproject.toml``,
  ``requirements*.txt`` y ``gitignore.txt`` (como ``.gitignore``).
- Sustituye ``MiAplicacion`` por el nombre visible (en ``NOMBRE_APLICACION``
  y la documentación de usuario) o por el identificador sin espacios (en el
  ejecutable, la instancia única, la compilación y ``entorno.bat``), y
  ``miaplicacion`` por el nombre del paquete en ``pyproject.toml``.
- Con ``--paquete`` distinto de ``app``, renombra la carpeta del paquete y
  sus importaciones.
- Con ``--python X.Y`` fija la versión de Python del proyecto: ``VERSION_PY``
  en ``entorno.bat`` y ``requires-python``/``target-version`` en
  ``pyproject.toml``. Sin ella, ``entorno.bat`` usa la predeterminada del
  lanzador ``py``.
- NUNCA sobrescribe: un archivo que ya existe en el destino se deja como está
  y se informa de ello. Conserva los finales de línea (CRLF en los ``.bat``).
- No copia este generador ni su prueba: no pintan nada en el proyecto nuevo.
"""

from __future__ import annotations

import argparse
import json
import keyword
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

MARCADOR = "MiAplicacion"
MARCADOR_PAQUETE_PYPROJECT = "miaplicacion"
PAQUETE_ORIGEN = "app"
EXTENSIONES_TEXTO = {".py", ".md", ".toml", ".txt", ".bat", ".json", ".cfg", ".ini"}
# Carpetas que nunca se copian (cachés, entornos, compilaciones).
CARPETAS_EXCLUIDAS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv", "build", "dist", ".git"}
# Subcarpetas de data/ generadas al usar la aplicación: son personales.
DATA_EXCLUIDA = {"config", "logs", "db", "cache", "temp", "sessions", "backups", "downloads"}
# Archivos del propio generador (rutas relativas al esqueleto).
ARCHIVOS_EXCLUIDOS = {"tools/crear_proyecto.py", "tests/test_crear_proyecto.py"}
RENOMBRADOS = {"gitignore.txt": ".gitignore"}
PAQUETES_RESERVADOS = {"wx", "test", "tests", "data", "tools", "main", "build"}


@dataclass
class Informe:
	"""Resultado de la copia: qué se creó y qué se dejó porque ya existía."""

	creados: list[Path] = field(default_factory=list)
	omitidos: list[Path] = field(default_factory=list)


def _sin_acentos(texto: str) -> str:
	descompuesto = unicodedata.normalize("NFKD", texto)
	return "".join(c for c in descompuesto if not unicodedata.combining(c))


def identificador_de(nombre: str) -> str:
	"""«Lector accesible» → ``LectorAccesible`` (ejecutable, mutex, carpeta de compilación)."""
	palabras = re.findall(r"[A-Za-z0-9]+", _sin_acentos(nombre))
	identificador = "".join(p[:1].upper() + p[1:] for p in palabras)
	if not identificador:
		raise ValueError("El nombre debe contener al menos una letra o un número.")
	return identificador if identificador[0].isalpha() else "App" + identificador


def nombre_distribucion_de(nombre: str) -> str:
	"""«Lector accesible» → ``lector-accesible`` (``name`` de ``pyproject.toml``)."""
	return "-".join(p.lower() for p in re.findall(r"[A-Za-z0-9]+", _sin_acentos(nombre))) or "aplicacion"


def validar_paquete(paquete: str) -> str:
	"""Comprueba que ``paquete`` es un nombre de paquete Python razonable."""
	if not re.fullmatch(r"[a-z_][a-z0-9_]*", paquete) or keyword.iskeyword(paquete) or paquete in PAQUETES_RESERVADOS:
		raise ValueError(f"«{paquete}» no sirve como nombre de paquete: use minúsculas, números y guiones bajos.")
	return paquete


def validar_python(version: str | None) -> str | None:
	"""Comprueba que ``version`` tiene la forma ``3.X`` (3.10 o superior)."""
	if version is None:
		return None
	coincidencia = re.fullmatch(r"3\.(\d{2,})", version.strip())
	if not coincidencia or int(coincidencia.group(1)) < 10:
		raise ValueError(f"«{version}» no es una versión válida: use 3.10 o superior, por ejemplo 3.13.")
	return version.strip()


def _sustituir(
	texto: str, ruta_relativa: str, nombre: str, identificador: str, distribucion: str, paquete: str, python: str | None = None
) -> str:
	"""Aplica las sustituciones a un archivo de texto."""
	texto = texto.replace(f'NOMBRE_APLICACION = "{MARCADOR}"', f"NOMBRE_APLICACION = {json.dumps(nombre, ensure_ascii=False)}")
	texto = texto.replace(f"{MARCADOR}.exe", f"{identificador}.exe")
	# La documentación de usuario lleva el nombre visible; el resto, el identificador.
	texto = texto.replace(MARCADOR, nombre if ruta_relativa.startswith("data/docs/") else identificador)
	texto = texto.replace(f'"{MARCADOR_PAQUETE_PYPROJECT}"', f'"{distribucion}"')
	if python:
		texto = texto.replace('set "VERSION_PY="', f'set "VERSION_PY={python}"')
		texto = texto.replace('requires-python = ">=3.10"', f'requires-python = ">={python}"')
		texto = texto.replace('target-version = "py310"', f'target-version = "py{python.replace(".", "")}"')
	if paquete != PAQUETE_ORIGEN and ruta_relativa.endswith((".py", ".md", ".toml")):
		texto = re.sub(r"(?<![\w.\-])app(?=[./\\](?:core|ui|services|main)\b)", paquete, texto)
		texto = re.sub(r"\bfrom app import\b", f"from {paquete} import", texto)
		texto = re.sub(r"\bimport app\b", f"import {paquete}", texto)
		texto = texto.replace('/ "app" /', f'/ "{paquete}" /')
		texto = texto.replace('PAQUETES = ["wx", "app"]', f'PAQUETES = ["wx", "{paquete}"]')
	return texto


def _archivos_del_esqueleto(origen: Path) -> list[Path]:
	"""Archivos que se copian, en orden estable."""
	resultado = []
	for ruta in sorted(origen.rglob("*")):
		if not ruta.is_file():
			continue
		relativa = ruta.relative_to(origen)
		partes = relativa.parts
		if any(parte in CARPETAS_EXCLUIDAS for parte in partes[:-1]):
			continue
		if relativa.as_posix() in ARCHIVOS_EXCLUIDOS or ruta.suffix in {".pyc", ".log", ".tmp"}:
			continue
		if partes[0] == "data" and len(partes) > 2 and partes[1] in DATA_EXCLUIDA:
			continue
		resultado.append(ruta)
	return resultado


def crear_proyecto(
	destino: Path,
	nombre: str,
	identificador: str | None = None,
	paquete: str = PAQUETE_ORIGEN,
	origen: Path | None = None,
	simular: bool = False,
	python: str | None = None,
) -> Informe:
	"""Copia el esqueleto a ``destino`` con el nombre dado. No sobrescribe nada.

	:param destino: carpeta del proyecto nuevo (puede existir ya).
	:param nombre: nombre visible, con espacios y acentos si se quiere.
	:param identificador: nombre sin espacios; por defecto se deriva de ``nombre``.
	:param paquete: nombre del paquete Python (``app`` por convención).
	:param origen: carpeta del esqueleto; por defecto, la que contiene ``tools/``.
	:param simular: no escribe nada; solo informa de lo que haría.
	:param python: versión de Python del proyecto (``3.13``); ``None`` = sin fijar.
	"""
	nombre = nombre.strip()
	if not nombre or any(c in nombre for c in '"\\/:*?<>|'):
		raise ValueError("El nombre no puede estar vacío ni contener comillas ni caracteres no válidos en Windows.")
	identificador = identificador or identificador_de(nombre)
	if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", identificador):
		raise ValueError(f"«{identificador}» no sirve como identificador: solo letras, números y guiones bajos.")
	paquete = validar_paquete(paquete)
	python = validar_python(python)
	distribucion = nombre_distribucion_de(nombre)
	origen = (origen or Path(__file__).resolve().parent.parent).resolve()
	destino = Path(destino).resolve()
	if destino == origen or origen in destino.parents:
		raise ValueError("El destino no puede estar dentro del esqueleto de origen.")

	informe = Informe()
	for ruta in _archivos_del_esqueleto(origen):
		relativa = ruta.relative_to(origen).as_posix()
		relativa_destino = RENOMBRADOS.get(relativa, relativa)
		if paquete != PAQUETE_ORIGEN and relativa_destino.startswith(PAQUETE_ORIGEN + "/"):
			relativa_destino = paquete + relativa_destino[len(PAQUETE_ORIGEN) :]
		final = destino / relativa_destino
		if final.exists():
			informe.omitidos.append(final)
			continue
		informe.creados.append(final)
		if simular:
			continue
		final.parent.mkdir(parents=True, exist_ok=True)
		if ruta.suffix in EXTENSIONES_TEXTO or relativa in RENOMBRADOS:
			# Se trabaja en binario → texto → binario para conservar CRLF/LF tal cual.
			texto = ruta.read_bytes().decode("utf-8")
			texto = _sustituir(texto, relativa, nombre, identificador, distribucion, paquete, python)
			final.write_bytes(texto.encode("utf-8"))
		else:
			shutil.copy2(ruta, final)
	return informe


def principal(argumentos: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Crea un proyecto wxPython accesible y portable a partir del esqueleto.")
	analizador.add_argument("--destino", required=True, type=Path, help="Carpeta del proyecto nuevo.")
	analizador.add_argument("--nombre", required=True, help="Nombre visible de la aplicación.")
	analizador.add_argument("--id", dest="identificador", help="Identificador sin espacios (por defecto, derivado del nombre).")
	analizador.add_argument("--paquete", default=PAQUETE_ORIGEN, help="Nombre del paquete Python (por defecto, app).")
	analizador.add_argument("--python", help="Versión de Python del proyecto, por ejemplo 3.13 (por defecto, sin fijar).")
	analizador.add_argument("--origen", type=Path, help="Carpeta del esqueleto (por defecto, la de este script).")
	analizador.add_argument("--simular", action="store_true", help="Muestra lo que haría sin escribir nada.")
	opciones = analizador.parse_args(argumentos)
	try:
		informe = crear_proyecto(
			opciones.destino,
			opciones.nombre,
			opciones.identificador,
			opciones.paquete,
			opciones.origen,
			opciones.simular,
			opciones.python,
		)
	except (ValueError, OSError) as error:
		print(f"ERROR: {error}", file=sys.stderr)
		return 1
	verbo = "Se crearían" if opciones.simular else "Creados"
	print(f"{verbo} {len(informe.creados)} archivos en {opciones.destino}.")
	if informe.omitidos:
		print(f"Omitidos {len(informe.omitidos)} archivos que ya existían (no se han tocado):")
		for ruta in informe.omitidos:
			print(f"- {ruta}")
	if not opciones.simular:
		print("Siguientes pasos: entorno.bat crear, y después entorno.bat pruebas.")
	return 0


if __name__ == "__main__":
	sys.exit(principal())
