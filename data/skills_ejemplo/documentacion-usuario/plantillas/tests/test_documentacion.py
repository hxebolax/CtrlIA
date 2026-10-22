"""Pruebas del registro de cambios y del aviso de novedades (PLANTILLA del agente «documentacion-usuario»).

No necesitan wx. Redirigen ``data`` con ``APP_DATA_DIR`` para no tocar la
configuración real.
"""

from __future__ import annotations

import pytest

from app.core import documentacion as doc
from tools import notas_version

CHANGELOG = """\
# Novedades

Texto de introducción.

## Versión 1.10.0 - 2026-12-01

### Novedades

- Diez.

## 1.2.0 (2026-11-01)

### Correcciones

- Dos.

## v1.1.0

- Uno.

## Versión 1.0.0 - 2026-09-25

- Primera.

## Enlaces

- No es una versión.
"""


@pytest.fixture
def datos(tmp_path, monkeypatch):
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
	return tmp_path / "data"


def test_leer_changelog():
	versiones = doc.leer_changelog(CHANGELOG)
	assert [v.numero for v in versiones] == ["1.10.0", "1.2.0", "1.1.0", "1.0.0"]
	assert versiones[0].encabezado == "Versión 1.10.0 - 2026-12-01"
	assert versiones[0].texto == "### Novedades\n\n- Diez."
	assert "No es una versión" not in versiones[-1].texto


def test_comparacion_numerica_de_versiones():
	assert doc.clave_version("1.10.0") > doc.clave_version("1.9.9")
	assert doc.clave_version("2") == doc.clave_version("2.0.0")
	elegidas = doc.novedades_entre(doc.leer_changelog(CHANGELOG), "1.0.0", "1.2.0")
	assert [v.numero for v in elegidas] == ["1.2.0", "1.1.0"]


def test_novedades_una_sola_vez_por_version(datos):
	# Instalación nueva: no se muestra nada, pero se anota la versión.
	assert doc.novedades_pendientes("1.0.0", CHANGELOG) is None
	assert doc.leer_ultima_vista() == "1.0.0"
	# Misma versión: nada.
	assert doc.novedades_pendientes("1.0.0", CHANGELOG) is None
	# Actualización: las versiones intermedias, de la más nueva a la más antigua.
	desde, versiones = doc.novedades_pendientes("1.2.0", CHANGELOG)
	assert desde == "1.0.0"
	assert [v.numero for v in versiones] == ["1.2.0", "1.1.0"]
	doc.marcar_vista("1.2.0")
	assert doc.novedades_pendientes("1.2.0", CHANGELOG) is None
	assert (datos / "config" / "novedades.json").is_file()


def test_version_sin_entrada_y_vuelta_atras(datos):
	doc.marcar_vista("1.10.0")
	assert doc.novedades_pendientes("1.2.0", CHANGELOG) is None  # versión anterior
	assert doc.leer_ultima_vista() == "1.2.0"
	assert doc.novedades_pendientes("1.2.5", CHANGELOG) is None  # sin entrada: se anota
	assert doc.leer_ultima_vista() == "1.2.5"


def test_estado_danado_se_trata_como_instalacion_nueva(datos):
	(datos / "config").mkdir(parents=True)
	(datos / "config" / "novedades.json").write_text("{roto", encoding="utf-8")
	assert doc.novedades_pendientes("1.1.0", CHANGELOG) is None
	assert doc.leer_ultima_vista() == "1.1.0"


def test_componer_novedades():
	texto = doc.componer_novedades(doc.leer_changelog(CHANGELOG)[:2], "Novedades de la versión 1.10.0")
	assert texto.startswith("# Novedades de la versión 1.10.0\n\n## Versión 1.10.0 - 2026-12-01")
	assert "### Correcciones" in texto


def test_ruta_documento_prefiere_data_docs(datos, tmp_path):
	assert doc.ruta_documento("no_existe_nunca.md") is None
	(datos / "docs").mkdir(parents=True)
	(datos / "docs" / "prueba_ruta.md").write_text("# Hola\n", encoding="utf-8")
	assert doc.ruta_documento("prueba_ruta.md") == datos / "docs" / "prueba_ruta.md"
	assert doc.titulo_de(doc.leer_documento("prueba_ruta.md"), "Otro") == "Hola"


def test_notas_version(tmp_path, capsys):
	(tmp_path / "docs").mkdir()
	(tmp_path / "docs" / "changelog.md").write_text(CHANGELOG, encoding="utf-8")
	(tmp_path / "app" / "core").mkdir(parents=True)
	(tmp_path / "app" / "core" / "version.py").write_text('APP_VERSION = "1.2.0"\n', encoding="utf-8")
	salida = tmp_path / "build" / "notas.md"
	assert notas_version.main(["--raiz", str(tmp_path), "--salida", str(salida)]) == 0
	assert salida.read_text(encoding="utf-8") == "# 1.2.0 (2026-11-01)\n\n### Correcciones\n\n- Dos.\n"
	assert notas_version.main(["--raiz", str(tmp_path), "--version", "3.0.0"]) == 1
	assert "no tiene entrada" in capsys.readouterr().out
