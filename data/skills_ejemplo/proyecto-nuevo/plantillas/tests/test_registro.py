"""Pruebas del registro básico: archivo de sesión, ganchos y fallo de arranque."""

from __future__ import annotations

import logging
import sys
import threading

import pytest

from app.core import registro


@pytest.fixture
def carpeta_logs(tmp_path):
	excepthook, threadhook = sys.excepthook, threading.excepthook
	nivel = logging.getLogger().level
	carpeta = tmp_path / "logs"
	yield carpeta
	registro.establecer_aviso_usuario(None)
	registro.detener_registro()
	sys.excepthook, threading.excepthook = excepthook, threadhook
	logging.getLogger().setLevel(nivel)


def _texto(carpeta):
	for manejador in logging.getLogger().handlers:
		manejador.flush()
	return "\n".join(p.read_text(encoding="utf-8") for p in sorted(carpeta.glob("sesion_*.log")))


def test_crea_sesion_con_cabecera(carpeta_logs):
	archivo = registro.configurar_registro("Prueba", "9.9.9", carpeta=carpeta_logs)
	assert archivo.is_file()
	logging.getLogger("app.prueba").info("Mensaje con %s", "formato diferido")
	texto = _texto(carpeta_logs)
	assert "Prueba 9.9.9" in texto
	assert "Mensaje con formato diferido" in texto
	assert "test_registro.py" in texto, "cada línea indica archivo, línea y función"
	assert (carpeta_logs / "fallo_nativo.log").exists()


def test_excepcion_no_controlada_se_registra_y_avisa(carpeta_logs):
	avisos = []
	registro.configurar_registro("Prueba", "1.0", carpeta=carpeta_logs)
	registro.establecer_aviso_usuario(avisos.append)
	try:
		{}["clave"]
	except KeyError:
		sys.excepthook(*sys.exc_info())
	texto = _texto(carpeta_logs)
	assert "Excepción no controlada" in texto
	assert "KeyError" in texto and "Traceback" in texto
	assert avisos and "KeyError" in avisos[0]


def test_excepcion_en_hilo_se_registra(carpeta_logs):
	registro.configurar_registro("Prueba", "1.0", carpeta=carpeta_logs)

	def falla():
		raise ValueError("fallo en hilo")

	hilo = threading.Thread(target=falla, name="hilo-de-prueba")
	hilo.start()
	hilo.join()
	texto = _texto(carpeta_logs)
	assert "hilo-de-prueba" in texto
	assert "fallo en hilo" in texto


def test_salida_estandar_ausente_va_al_registro(carpeta_logs, monkeypatch):
	monkeypatch.setattr(sys, "stdout", None)
	registro.configurar_registro("Prueba", "1.0", carpeta=carpeta_logs)
	print("texto impreso sin consola")
	assert "texto impreso sin consola" in _texto(carpeta_logs)
	registro.detener_registro()
	assert sys.stdout is None


def test_conserva_solo_las_ultimas_sesiones(carpeta_logs):
	carpeta_logs.mkdir(parents=True)
	for numero in range(15):
		(carpeta_logs / f"sesion_20000101_0000{numero:02d}.log").write_text("x", encoding="utf-8")
	registro.configurar_registro("Prueba", "1.0", carpeta=carpeta_logs)
	assert len(list(carpeta_logs.glob("sesion_*.log"))) == registro.SESIONES_A_CONSERVAR


def test_detener_restaura_los_ganchos(carpeta_logs):
	original = sys.excepthook
	registro.configurar_registro("Prueba", "1.0", carpeta=carpeta_logs)
	assert sys.excepthook is not original
	registro.detener_registro()
	assert sys.excepthook is original


def test_registrar_fallo_de_arranque(datos_temporales):
	try:
		raise RuntimeError("no arranca")
	except RuntimeError as error:
		ruta = registro.registrar_fallo_de_arranque(error)
	assert ruta == datos_temporales / "logs" / "arranque_fallido.log"
	texto = ruta.read_text(encoding="utf-8")
	assert "RuntimeError: no arranca" in texto
	assert "Fallo de arranque" in texto
