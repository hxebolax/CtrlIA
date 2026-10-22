"""Pruebas del generador de proyectos (tools/crear_proyecto.py).

Este archivo NO se copia a los proyectos generados: solo tiene sentido junto
al esqueleto.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

from .conftest import RAIZ

_spec = importlib.util.spec_from_file_location("crear_proyecto", RAIZ / "tools" / "crear_proyecto.py")
crear = importlib.util.module_from_spec(_spec)
# dataclasses busca el módulo en sys.modules al procesar las anotaciones.
sys.modules["crear_proyecto"] = crear
_spec.loader.exec_module(crear)

EXTENSIONES_TEXTO = {".py", ".md", ".toml", ".txt", ".bat", ""}


def _textos(carpeta):
	for ruta in carpeta.rglob("*"):
		if ruta.is_file() and ruta.suffix in EXTENSIONES_TEXTO:
			yield ruta, ruta.read_bytes().decode("utf-8")


def test_derivacion_de_nombres():
	assert crear.identificador_de("Lector accesible") == "LectorAccesible"
	assert crear.identificador_de("Árbol de 3 niveles") == "ArbolDe3Niveles"
	assert crear.identificador_de("3D visor") == "App3DVisor"
	assert crear.nombre_distribucion_de("Lector accesible") == "lector-accesible"
	with pytest.raises(ValueError):
		crear.identificador_de("¿?")


def test_crea_el_proyecto_completo(tmp_path):
	destino = tmp_path / "Lector"
	informe = crear.crear_proyecto(destino, "Lector accesible")
	assert informe.creados and not informe.omitidos
	for relativa in (
		"main.py",
		"app/__init__.py",
		"app/core/paths.py",
		"app/core/version.py",
		"app/core/config.py",
		"app/core/instancia_unica.py",
		"app/core/registro.py",
		"app/ui/ventana_principal.py",
		"app/ui/bandeja.py",
		"app/ui/acerca_de.py",
		"app/services/__init__.py",
		"data/docs/manual.md",
		"data/icons/.gitkeep",
		"data/sounds/.gitkeep",
		"tests/conftest.py",
		"tests/test_ventana.py",
		".gitignore",
		"entorno.bat",
		"setup_cx.py",
		"pyproject.toml",
		"requirements.txt",
	):
		assert (destino / relativa).is_file(), relativa
	assert not (destino / "tools" / "crear_proyecto.py").exists()
	assert not (destino / "tests" / "test_crear_proyecto.py").exists()
	assert not (destino / "gitignore.txt").exists()

	for ruta, texto in _textos(destino):
		assert "MiAplicacion" not in texto, ruta
		assert "miaplicacion" not in texto, ruta
	version = (destino / "app/core/version.py").read_text(encoding="utf-8")
	assert 'NOMBRE_APLICACION = "Lector accesible"' in version
	assert 'ID_APLICACION = "LectorAccesible"' in version
	assert 'name = "lector-accesible"' in (destino / "pyproject.toml").read_text(encoding="utf-8")
	manual = (destino / "data/docs/manual.md").read_text(encoding="utf-8")
	assert "# Manual de Lector accesible" in manual and "LectorAccesible.exe" in manual
	bat = (destino / "entorno.bat").read_bytes()
	assert b'set "NOMBRE_APP=LectorAccesible"' in bat
	assert b"\n" not in bat.replace(b"\r\n", b""), "el .bat conserva CRLF"
	assert not bat.startswith(b"\xef\xbb\xbf")


def test_no_sobrescribe_nada(tmp_path):
	destino = tmp_path / "Existente"
	destino.mkdir()
	(destino / "main.py").write_text("# mío\n", encoding="utf-8")
	informe = crear.crear_proyecto(destino, "Otra")
	assert destino / "main.py" in informe.omitidos
	assert (destino / "main.py").read_text(encoding="utf-8") == "# mío\n"
	segunda = crear.crear_proyecto(destino, "Otra")
	assert segunda.creados == []
	assert len(segunda.omitidos) == len(informe.creados) + 1


def test_simular_no_escribe(tmp_path):
	informe = crear.crear_proyecto(tmp_path / "Simulado", "Simulada", simular=True)
	assert informe.creados
	assert not (tmp_path / "Simulado").exists()


def test_valida_los_argumentos(tmp_path):
	with pytest.raises(ValueError):
		crear.crear_proyecto(tmp_path / "x", "")
	with pytest.raises(ValueError):
		crear.crear_proyecto(tmp_path / "x", 'Con "comillas"')
	with pytest.raises(ValueError):
		crear.crear_proyecto(tmp_path / "x", "Bien", paquete="class")
	with pytest.raises(ValueError):
		crear.crear_proyecto(RAIZ / "dentro", "Bien")


def test_fija_la_version_de_python(tmp_path):
	destino = tmp_path / "ConPython"
	crear.crear_proyecto(destino, "Con Python", python="3.13")
	assert b'set "VERSION_PY=3.13"' in (destino / "entorno.bat").read_bytes()
	pyproject = (destino / "pyproject.toml").read_text(encoding="utf-8")
	assert 'requires-python = ">=3.13"' in pyproject
	assert 'target-version = "py313"' in pyproject
	for invalida in ("3.9", "2.7", "tres", "3"):
		with pytest.raises(ValueError):
			crear.crear_proyecto(tmp_path / "Mal", "Mal", python=invalida)


def test_linea_de_ordenes(tmp_path, capsys):
	assert crear.principal(["--destino", str(tmp_path / "Cli"), "--nombre", "Por consola"]) == 0
	assert "Creados" in capsys.readouterr().out
	assert (tmp_path / "Cli" / "main.py").is_file()
	assert crear.principal(["--destino", str(tmp_path / "Cli2"), "--nombre", "Mal", "--paquete", "import"]) == 1


def test_proyecto_generado_pasa_sus_pruebas(tmp_path):
	"""Prueba de verdad: genera con otro nombre de paquete y ejecuta SUS pruebas."""
	destino = tmp_path / "Generado"
	crear.crear_proyecto(destino, "Gestor de notas", paquete="notas")
	assert (destino / "notas" / "core" / "paths.py").is_file()
	assert not (destino / "app").exists()
	for ruta, texto in _textos(destino):
		if ruta.suffix == ".py":
			assert "from app." not in texto and "import app" not in texto, ruta
	entorno = {k: v for k, v in os.environ.items() if k not in ("APP_DATA_DIR", "PYTEST_ADDOPTS")}
	resultado = subprocess.run(
		[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
		cwd=str(destino),
		capture_output=True,
		text=True,
		timeout=600,
		env=entorno,
	)
	assert resultado.returncode == 0, resultado.stdout[-4000:] + resultado.stderr[-2000:]
	assert not (destino / "data" / "config").exists(), "las pruebas no tocan la data real"
	assert not (destino / "data" / "logs").exists()
