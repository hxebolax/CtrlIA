"""Pruebas de la medición del arranque (PLANTILLA del agente «hilos-y-tareas»)."""

from __future__ import annotations

import logging
import sys

import pytest

from app.core.medicion_arranque import MedicionArranque, importar_perezoso, segundos_desde_inicio_proceso


class RelojFalso:
	"""Reloj que avanza solo cuando la prueba lo pide."""

	def __init__(self):
		self.ahora = 100.0

	def __call__(self):
		return self.ahora


def test_fases_y_resumen_en_una_linea():
	reloj = RelojFalso()
	medicion = MedicionArranque(reloj=reloj, incluir_proceso=False)
	reloj.ahora += 0.05
	medicion.fase("configuracion")
	reloj.ahora += 0.40
	medicion.fase("wx")
	with medicion.medir("ventana"):
		reloj.ahora += 0.90
	texto = medicion.resumen()
	assert "\n" not in texto
	assert texto.startswith("Arranque en 1.35 s")
	assert "configuracion 0.05 · wx 0.40 · ventana 0.90" in texto
	assert texto.endswith("más lenta: ventana")
	assert medicion.mas_lenta() == ("ventana", pytest.approx(0.90))


def test_terminar_registra_una_vez(caplog):
	caplog.set_level(logging.INFO, logger="app.core.medicion_arranque")
	reloj = RelojFalso()
	medicion = MedicionArranque(reloj=reloj, incluir_proceso=False)
	reloj.ahora += 0.5
	medicion.fase("todo")
	medicion.terminar()
	medicion.terminar()
	registros = [r for r in caplog.records if r.name == "app.core.medicion_arranque"]
	assert len(registros) == 1 and registros[0].levelno == logging.INFO


def test_arranque_lento_es_aviso(caplog):
	reloj = RelojFalso()
	medicion = MedicionArranque(reloj=reloj, incluir_proceso=False)
	reloj.ahora += 4.0
	medicion.fase("base de datos")
	medicion.terminar(umbral_aviso=3.0)
	aviso = next(r for r in caplog.records if r.levelno == logging.WARNING)
	assert "supera el umbral" in aviso.getMessage() and "base de datos" in aviso.getMessage()


@pytest.mark.skipif(sys.platform != "win32", reason="GetProcessTimes solo existe en Windows")
def test_tiempo_desde_inicio_del_proceso():
	segundos = segundos_desde_inicio_proceso()
	assert segundos is not None and 0 < segundos < 24 * 3600
	medicion = MedicionArranque()
	assert "antes de main" in medicion.resumen()


def test_importacion_perezosa(tmp_path, monkeypatch):
	marca = tmp_path / "ejecutado.txt"
	(tmp_path / "modulo_pesado_prueba.py").write_text(
		f"from pathlib import Path\nPath({str(marca)!r}).write_text('si')\nVALOR = 42\n", encoding="utf-8"
	)
	monkeypatch.syspath_prepend(str(tmp_path))
	monkeypatch.delitem(sys.modules, "modulo_pesado_prueba", raising=False)
	modulo = importar_perezoso("modulo_pesado_prueba")
	assert not marca.exists()  # aún no se ha ejecutado
	assert modulo.VALOR == 42
	assert marca.exists()
	assert importar_perezoso("modulo_pesado_prueba") is modulo
	with pytest.raises(ModuleNotFoundError):
		importar_perezoso("modulo_que_no_existe_de_verdad")
