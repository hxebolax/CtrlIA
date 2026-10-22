"""Pruebas de app/core/configuracion.py (PLANTILLA del agente «configuracion»).

Trabajan en una carpeta temporal (APP_DATA_DIR): nunca tocan el data real.
"""

from __future__ import annotations

import gc
import json
import os
import threading
import time

import pytest

from app.core import configuracion as mod
from app.core.configuracion import Configuracion, ErrorConfiguracion, ErrorValidacion


@pytest.fixture
def datos(tmp_path, monkeypatch):
	"""Redirige data a una carpeta temporal."""
	monkeypatch.setenv(mod.VARIABLE_DATA, str(tmp_path / "data"))
	return tmp_path / "data"


def _ruta(datos):
	return datos / "config" / "settings.json"


def _leer(ruta):
	return json.loads(ruta.read_text(encoding="utf-8"))


def test_ruta_portable_usa_app_data_dir(datos):
	assert mod.ruta_por_defecto() == (datos / "config" / "settings.json").resolve()


def test_valores_por_defecto_sin_archivo(datos):
	config = Configuracion()
	assert config.primer_arranque is True
	assert config.obtener("apariencia.tamano_fuente") == 11
	assert config.obtener("general.confirmar_salida") is True
	assert _ruta(datos).is_file(), "el primer arranque deja el archivo creado"
	assert _leer(_ruta(datos))[mod.CLAVE_VERSION] == mod.VERSION_ESQUEMA


def test_obtener_devuelve_copia_y_clave_desconocida(datos):
	config = Configuracion(por_defecto={"s": {"lista": [1, 2]}}, reglas={})
	copia = config.obtener("s.lista")
	copia.append(3)
	assert config.obtener("s.lista") == [1, 2]
	assert config.obtener("s.no_existe", "x") == "x"
	with pytest.raises(KeyError):
		config.obtener("s.no_existe")


def test_fusion_completa_claves_y_conserva_desconocidas(datos):
	ruta = _ruta(datos)
	ruta.parent.mkdir(parents=True)
	ruta.write_text(json.dumps({
		mod.CLAVE_VERSION: mod.VERSION_ESQUEMA,
		"apariencia": {"tamano_fuente": 20},
		"complemento": {"dato": "de otra versión"},
	}), encoding="utf-8")
	config = Configuracion()
	assert config.obtener("apariencia.tamano_fuente") == 20  # del usuario
	assert config.obtener("apariencia.tema") == "sistema"  # completado
	assert config.obtener("red.puerto_proxy") == 8080  # sección entera completada
	guardado = _leer(ruta)
	assert guardado["complemento"] == {"dato": "de otra versión"}
	assert guardado["apariencia"]["tema"] == "sistema"


def test_valor_de_tipo_o_rango_invalido_se_sustituye(datos):
	ruta = _ruta(datos)
	ruta.parent.mkdir(parents=True)
	ruta.write_text(json.dumps({
		mod.CLAVE_VERSION: mod.VERSION_ESQUEMA,
		"apariencia": {"tamano_fuente": "grande", "tema": "fucsia"},
		"general": {"confirmar_salida": 1},
	}), encoding="utf-8")
	config = Configuracion()
	assert config.obtener("apariencia.tamano_fuente") == 11
	assert config.obtener("apariencia.tema") == "sistema"
	assert config.obtener("general.confirmar_salida") is True


def test_corrupto_se_aparta_y_arranca_con_valores_por_defecto(datos, caplog):
	ruta = _ruta(datos)
	ruta.parent.mkdir(parents=True)
	ruta.write_text("{esto no es json", encoding="utf-8")
	config = Configuracion()
	assert config.obtener("apariencia.tamano_fuente") == 11
	apartados = list(ruta.parent.glob("settings.json.corrupto-*"))
	assert len(apartados) == 1
	assert apartados[0].read_text(encoding="utf-8") == "{esto no es json"
	assert "fábrica" in config.recuperacion
	assert any("dañada" in r.getMessage() for r in caplog.records)
	assert _leer(ruta)["apariencia"]["tamano_fuente"] == 11  # archivo sano regenerado


def test_corrupto_recupera_la_copia_bak(datos):
	config = Configuracion()
	config.establecer("apariencia.tamano_fuente", 15)
	config.establecer("apariencia.tamano_fuente", 16)  # el .bak queda con 15
	_ruta(datos).write_bytes(b"\x00\x00basura")
	recuperada = Configuracion()
	assert recuperada.obtener("apariencia.tamano_fuente") == 15
	assert "copia anterior" in recuperada.recuperacion


def test_escritura_atomica_ante_fallo(datos, monkeypatch):
	config = Configuracion()
	config.establecer("apariencia.tamano_fuente", 14)
	antes = _ruta(datos).read_bytes()

	def fallo(*_args, **_kwargs):
		raise OSError("disco lleno simulado")

	monkeypatch.setattr(mod.os, "replace", fallo)
	with pytest.raises(ErrorConfiguracion):
		config.establecer("apariencia.tamano_fuente", 30)
	monkeypatch.undo()
	assert _ruta(datos).read_bytes() == antes, "el archivo original sigue intacto"
	assert not list(_ruta(datos).parent.glob("*.tmp")), "no quedan temporales"


def test_guardar_deja_copia_bak(datos):
	config = Configuracion()
	config.establecer("apariencia.tamano_fuente", 12)
	config.establecer("apariencia.tamano_fuente", 13)
	bak = _ruta(datos).with_name("settings.json.bak")
	assert _leer(bak)["apariencia"]["tamano_fuente"] == 12
	assert _leer(_ruta(datos))["apariencia"]["tamano_fuente"] == 13


def test_migracion_de_clave_renombrada(datos):
	ruta = _ruta(datos)
	ruta.parent.mkdir(parents=True)
	# Archivo de la versión 1 (sin version_esquema): la fuente estaba en «general».
	ruta.write_text(json.dumps({
		"general": {"tamano_letra": 18, "usar_sonidos_viejos": True, "idioma": "en"},
	}), encoding="utf-8")
	config = Configuracion()
	assert config.obtener("apariencia.tamano_fuente") == 18
	assert config.obtener("general.idioma") == "en"
	guardado = _leer(ruta)
	assert guardado[mod.CLAVE_VERSION] == mod.VERSION_ESQUEMA
	assert "tamano_letra" not in guardado["general"]
	assert "usar_sonidos_viejos" not in guardado["general"]


def test_version_futura_no_se_rebaja(datos):
	ruta = _ruta(datos)
	ruta.parent.mkdir(parents=True)
	ruta.write_text(json.dumps({mod.CLAVE_VERSION: 99, "nueva": {"x": 1}}), encoding="utf-8")
	config = Configuracion()
	config.establecer("apariencia.tamano_fuente", 12)
	guardado = _leer(ruta)
	assert guardado[mod.CLAVE_VERSION] == 99
	assert guardado["nueva"] == {"x": 1}


def test_validacion_al_establecer(datos):
	config = Configuracion()
	with pytest.raises(ErrorValidacion):
		config.establecer("apariencia.tamano_fuente", 200)
	with pytest.raises(ErrorValidacion):
		config.establecer("apariencia.tamano_fuente", "12")
	with pytest.raises(ErrorValidacion):
		config.establecer("apariencia.tema", "fucsia")
	with pytest.raises(ErrorValidacion):
		config.establecer("general.confirmar_salida", 1)
	assert config.establecer("accesibilidad.volumen", 1) is True  # int vale como float
	assert config.obtener("apariencia.tamano_fuente") == 11


def test_suscriptores_reciben_cambios(datos):
	config = Configuracion()
	recibidos, apariencia = [], []
	config.suscribir(lambda c, a, n: recibidos.append((c, a, n)))
	cancelar = config.suscribir(lambda c, a, n: apariencia.append(c), prefijo="apariencia.")
	config.establecer("apariencia.tamano_fuente", 14)
	config.establecer("apariencia.tamano_fuente", 14)  # sin cambio: sin aviso
	config.establecer("general.idioma", "fr")
	assert recibidos == [("apariencia.tamano_fuente", 11, 14), ("general.idioma", "es", "fr")]
	assert apariencia == ["apariencia.tamano_fuente"]
	cancelar()
	config.establecer("apariencia.tamano_fuente", 15)
	assert apariencia == ["apariencia.tamano_fuente"]


def test_suscriptor_roto_no_afecta_a_los_demas(datos, caplog):
	config = Configuracion()
	recibidos = []

	def roto(*_args):
		raise RuntimeError("fallo del suscriptor")

	config.suscribir(roto)
	config.suscribir(lambda c, a, n: recibidos.append(c))
	config.establecer("general.idioma", "pt")
	assert recibidos == ["general.idioma"]
	assert any("suscriptor" in r.getMessage() and r.exc_info for r in caplog.records)


def test_suscriptor_metodo_se_libera_con_su_objeto(datos):
	config = Configuracion()

	class Ventana:
		def __init__(self):
			self.avisos = 0

		def al_cambiar(self, *_args):
			self.avisos += 1

	ventana = Ventana()
	config.suscribir(ventana.al_cambiar)
	config.establecer("general.idioma", "en")
	assert ventana.avisos == 1
	del ventana
	gc.collect()
	config.establecer("general.idioma", "fr")  # no falla aunque la ventana ya no exista
	assert config._suscriptores == []


def test_lote_un_solo_guardado_y_avisos_al_final(datos, monkeypatch):
	config = Configuracion()
	guardados = []
	original = config.guardar
	monkeypatch.setattr(config, "guardar", lambda: (guardados.append(1), original()))
	avisos = []
	config.suscribir(lambda c, a, n: avisos.append(c))
	with config.lote():
		config.establecer("apariencia.tamano_fuente", 20)
		config.establecer("general.idioma", "en")
		assert avisos == [], "los avisos llegan al cerrar el lote"
	assert len(guardados) == 1
	assert avisos == ["apariencia.tamano_fuente", "general.idioma"]


def test_guardado_diferido(datos):
	config = Configuracion()
	config.establecer_diferido("accesibilidad.volumen", 0.5, retraso=0.05)
	config.establecer_diferido("accesibilidad.volumen", 0.4, retraso=0.05)
	limite = time.monotonic() + 3
	while time.monotonic() < limite:
		if _leer(_ruta(datos))["accesibilidad"]["volumen"] == 0.4:
			break
		time.sleep(0.02)
	assert _leer(_ruta(datos))["accesibilidad"]["volumen"] == 0.4
	config.establecer_diferido("accesibilidad.volumen", 0.3, retraso=60)
	config.guardar_pendiente()
	assert _leer(_ruta(datos))["accesibilidad"]["volumen"] == 0.3


def test_restablecer(datos):
	config = Configuracion()
	config.establecer_varias({"apariencia.tamano_fuente": 30, "general.idioma": "en"})
	config.restablecer("apariencia")
	assert config.obtener("apariencia.tamano_fuente") == 11
	assert config.obtener("general.idioma") == "en"
	config.restablecer()
	assert _leer(_ruta(datos))["general"]["idioma"] == "es"


def test_concurrencia_basica(datos):
	por_defecto = {"hilos": {f"h{i}": 0 for i in range(8)}}
	config = Configuracion(por_defecto=por_defecto, reglas={}, migraciones={})
	errores = []

	def trabajar(i):
		try:
			for n in range(1, 51):
				config.establecer(f"hilos.h{i}", n)
				config.obtener(f"hilos.h{(i + 1) % 8}")
		except Exception as error:  # noqa: BLE001 - se comprueba abajo
			errores.append(error)

	hilos = [threading.Thread(target=trabajar, args=(i,)) for i in range(8)]
	for hilo in hilos:
		hilo.start()
	for hilo in hilos:
		hilo.join()
	assert errores == []
	guardado = _leer(_ruta(datos))
	assert guardado["hilos"] == {f"h{i}": 50 for i in range(8)}
	assert not [p for p in os.listdir(_ruta(datos).parent) if p.endswith(".tmp")]


def test_archivo_ilegible_no_se_sobrescribe(datos, monkeypatch):
	ruta = _ruta(datos)
	ruta.parent.mkdir(parents=True)
	ruta.write_text(json.dumps({"apariencia": {"tamano_fuente": 22}}), encoding="utf-8")

	def sin_permiso(self, *args, **kwargs):
		raise PermissionError("acceso denegado simulado")

	monkeypatch.setattr(mod.Path, "read_text", sin_permiso)
	config = Configuracion()
	monkeypatch.undo()
	assert config.obtener("apariencia.tamano_fuente") == 11
	config.establecer("general.idioma", "en")  # no lanza, pero no escribe
	assert _leer(ruta) == {"apariencia": {"tamano_fuente": 22}}
