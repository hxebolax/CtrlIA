"""Pruebas de la composición de anuncios (PLANTILLA del agente «accesibilidad-wx»).

Reproducen la matriz de referencias/pruebas.md: índice interno de fila 2 (se anuncia 3),
cabecera «Estado», valor «Terminado». No prueban que NVDA lo diga: prueban
qué texto se le envía.
"""

from __future__ import annotations

import pytest

from app.ui.accesibilidad.composicion import (
	PreferenciasLectura,
	componer_anuncio_celda,
	componer_estado_casilla,
	limpiar_para_voz,
)


@pytest.mark.parametrize(
	("fila", "cabecera", "contenido", "esperado"),
	[
		(True, True, True, "Fila 3, Estado, Terminado"),
		(True, True, False, "Fila 3, Estado"),
		(True, False, True, "Fila 3, Terminado"),
		(True, False, False, "Fila 3"),
		(False, True, True, "Estado, Terminado"),
		(False, True, False, "Estado"),
		(False, False, True, "Terminado"),
		(False, False, False, "Terminado"),  # respaldo: nunca silencio
	],
)
def test_matriz_de_preferencias(fila, cabecera, contenido, esperado):
	preferencias = PreferenciasLectura(numero_fila=fila, cabecera=cabecera, contenido=contenido)
	assert componer_anuncio_celda(2, "Estado", "Terminado", globales=preferencias) == esperado


def test_respaldo_despues_de_combinar_con_las_locales():
	"""El caso del hallazgo A02: globales solo contenido y la lista suprime el contenido."""
	globales = PreferenciasLectura(numero_fila=False, cabecera=False, contenido=True)
	locales = PreferenciasLectura(contenido=False)
	assert componer_anuncio_celda(0, "Estado", "Terminado", globales, locales) == "Terminado"


@pytest.mark.parametrize("valor", ["", "   ", None, "\n\t"])
def test_celda_vacia_dice_sin_datos(valor):
	assert componer_anuncio_celda(0, "Autor", valor) == "Fila 1, Autor, Sin datos"


def test_columna_protegida_no_revela_el_valor_ni_por_respaldo():
	nada = PreferenciasLectura(False, False, False)
	texto = componer_anuncio_celda(0, "Contraseña", "hunter22", globales=nada, protegido=True)
	assert "hunter22" not in texto
	assert texto == "Contenido no disponible"


def test_cabecera_vacia_se_omite():
	assert componer_anuncio_celda(4, "", "Valor") == "Fila 5, Valor"


def test_saltos_de_linea_y_texto_largo():
	largo = "palabra " * 300
	texto = componer_anuncio_celda(0, "Texto", "línea 1\nlínea 2\r\n\vlínea 3" + largo)
	assert "\n" not in texto and "\v" not in texto
	assert texto.startswith("Fila 1, Texto, línea 1 línea 2 línea 3")
	assert len(texto) > 2000  # nunca se recorta el contenido


def test_unicode():
	assert componer_anuncio_celda(0, "Título", "Ñandú 🎧 café") == "Fila 1, Título, Ñandú 🎧 café"


def test_estado_casilla():
	assert componer_estado_casilla("Descargar subtítulos", True) == "Descargar subtítulos, marcada"
	assert componer_estado_casilla("", False) == "Elemento, no marcada"


def test_limpiar_para_voz():
	assert limpiar_para_voz("  a\n\nb\tc  ") == "a b c"
	assert limpiar_para_voz(None) == ""
