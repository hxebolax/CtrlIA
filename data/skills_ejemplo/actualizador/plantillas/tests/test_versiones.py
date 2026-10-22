"""Comparación de versiones sin dependencias (PLANTILLA del agente «actualizador»)."""

from __future__ import annotations

import pytest

from app.services import actualizador


@pytest.mark.parametrize(
	("menor", "mayor"),
	[
		("1.0.0", "1.0.1"),
		("1.9", "1.10"),
		("1.0", "2"),
		("1.0.dev1", "1.0a1"),
		("1.0a1", "1.0a2"),
		("1.0a2", "1.0b1"),
		("1.0b1", "1.0rc1"),
		("1.0rc1.dev1", "1.0rc1"),
		("1.0rc1", "1.0"),
		("1.0", "1.0.post1"),
		("1.0.post1", "1.0.1"),
		("1.0.0-alpha", "1.0.0-alpha.1"),
		("1.0.0-alpha.1", "1.0.0-beta"),
		("1.0.0-beta.2", "1.0.0-beta.11"),
		("1.0.0-rc.1", "1.0.0"),
		("0.9.9", "v1.0.0"),
	],
)
def test_orden(menor, mayor):
	assert actualizador.comparar_versiones(menor, mayor) == -1
	assert actualizador.comparar_versiones(mayor, menor) == 1
	assert actualizador.es_mas_nueva(mayor, menor)
	assert not actualizador.es_mas_nueva(menor, mayor)


@pytest.mark.parametrize(
	("a", "b"),
	[("1.0", "1.0.0"), ("v2.1", "2.1.0"), ("1.0+local.7", "1.0"), ("1.0-beta1", "1.0b1"), ("1.0.0-RC.1", "1.0rc1")],
)
def test_iguales(a, b):
	assert actualizador.comparar_versiones(a, b) == 0
	assert not actualizador.es_mas_nueva(a, b)


@pytest.mark.parametrize("texto", ["", "abc", "1..2", "1.0-foo", "1.0.0-beta.x", None, "1.0 2.0"])
def test_invalidas(texto):
	with pytest.raises(actualizador.VersionInvalida):
		actualizador.clave_version(texto)
