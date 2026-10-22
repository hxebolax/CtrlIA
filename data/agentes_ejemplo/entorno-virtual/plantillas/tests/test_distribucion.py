"""Pruebas de coherencia del entorno y de la distribución.

PLANTILLA del agente «entorno-virtual». No compilan nada: comprueban que la
versión es única, que la lista de data distribuible no incluye datos
personales y que las rutas son portables.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

try:
	import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - versiones anteriores
	tomllib = None

RAIZ = Path(__file__).resolve().parent.parent


def _constante_de(archivo: Path, nombre: str):
	"""Lee una constante literal de un .py sin ejecutarlo."""
	arbol = ast.parse(archivo.read_text(encoding="utf-8"))
	for nodo in arbol.body:
		if isinstance(nodo, ast.Assign) and any(getattr(t, "id", None) == nombre for t in nodo.targets):
			return ast.literal_eval(nodo.value)
	raise AssertionError(f"{nombre} no está definido en {archivo}")


def test_version_unica():
	"""pyproject.toml y app/core/version.py declaran la misma versión."""
	version_codigo = _constante_de(RAIZ / "app" / "core" / "version.py", "APP_VERSION")
	pyproject = RAIZ / "pyproject.toml"
	if tomllib is None:
		pytest.skip("tomllib necesita Python 3.11 o superior")
	if pyproject.is_file():
		datos = tomllib.loads(pyproject.read_text(encoding="utf-8"))
		assert datos["project"]["version"] == version_codigo


def test_data_distribuible_sin_datos_personales():
	"""Ninguna carpeta prohibida figura en la lista blanca de setup_cx.py."""
	setup = RAIZ / "setup_cx.py"
	distribuible = set(_constante_de(setup, "DATA_DISTRIBUIBLE"))
	prohibido = set(_constante_de(setup, "DATA_PROHIBIDO"))
	assert not distribuible & prohibido


def test_rutas_redirigibles(tmp_path, monkeypatch):
	"""APP_DATA_DIR redirige data para que las pruebas no toquen la real."""
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
	from app.core import paths

	assert paths.ruta_datos() == tmp_path.resolve()
	assert paths.ruta_registros().parent == tmp_path.resolve()


def test_sin_rutas_fuera_de_la_aplicacion():
	"""El código no usa carpetas del perfil de Windows para guardar datos."""
	prohibidas = re.compile(
		r"(environ|getenv)\W+[\"'](LOCAL)?APPDATA|tempfile\.gettempdir\(",
		re.IGNORECASE,
	)
	culpables = []
	for archivo in (RAIZ / "app").rglob("*.py"):
		texto = archivo.read_text(encoding="utf-8", errors="replace")
		if prohibidas.search(texto):
			culpables.append(str(archivo.relative_to(RAIZ)))
	assert not culpables, f"Rutas fuera de data/: {culpables}"
