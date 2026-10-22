"""Pruebas de la configuración: valores por defecto, lectura, escritura y archivos dañados."""

from __future__ import annotations

import json

from app.core.config import VALORES_POR_DEFECTO, Configuracion


def test_valores_por_defecto_sin_archivo(tmp_path):
	config = Configuracion(tmp_path / "config.json")
	assert config.obtener("ventana.ancho") == VALORES_POR_DEFECTO["ventana"]["ancho"]
	assert config.obtener("general.cerrar_a_bandeja") is False
	assert config.obtener("no.existe", "x") == "x"
	# Leer no crea el archivo: solo se escribe al guardar.
	assert not (tmp_path / "config.json").exists()


def test_ruta_por_defecto_en_data_redirigida(datos_temporales):
	assert Configuracion().ruta == datos_temporales / "config" / "config.json"


def test_guardar_y_releer(tmp_path):
	ruta = tmp_path / "config.json"
	config = Configuracion(ruta)
	config.establecer("ventana.ancho", 1024)
	config.establecer("nueva.seccion.clave", "valor")
	assert config.guardar() is True
	assert not list(tmp_path.glob("*.tmp")), "no debe quedar el temporal de la escritura atómica"
	texto = ruta.read_text(encoding="utf-8")
	assert "\t" in texto, "el JSON se escribe sangrado con tabulaciones"
	otra = Configuracion(ruta)
	assert otra.obtener("ventana.ancho") == 1024
	assert otra.obtener("nueva.seccion.clave") == "valor"
	# Las claves que no estaban en el archivo conservan su valor por defecto.
	assert otra.obtener("ventana.alto") == VALORES_POR_DEFECTO["ventana"]["alto"]


def test_archivo_danado_se_aparta(tmp_path):
	ruta = tmp_path / "config.json"
	ruta.write_text("{esto no es json", encoding="utf-8")
	config = Configuracion(ruta)
	assert config.obtener("ventana.ancho") == VALORES_POR_DEFECTO["ventana"]["ancho"]
	assert not ruta.exists()
	apartados = list(tmp_path.glob("config.danado-*.json"))
	assert len(apartados) == 1
	assert apartados[0].read_text(encoding="utf-8") == "{esto no es json"
	# Y se puede volver a guardar con normalidad.
	assert config.guardar() is True
	assert json.loads(ruta.read_text(encoding="utf-8"))["ventana"]["ancho"] == VALORES_POR_DEFECTO["ventana"]["ancho"]


def test_json_que_no_es_objeto(tmp_path):
	ruta = tmp_path / "config.json"
	ruta.write_text("[1, 2, 3]", encoding="utf-8")
	config = Configuracion(ruta)
	assert config.obtener("general.anunciar_arranque") is True
	assert list(tmp_path.glob("config.danado-*.json"))


def test_tipos_erroneos_usan_el_valor_por_defecto(tmp_path):
	ruta = tmp_path / "config.json"
	ruta.write_text(
		json.dumps({"ventana": {"ancho": "grande", "maximizada": 1, "alto": 700}, "general": "no", "extra": {"a": 1}}),
		encoding="utf-8",
	)
	config = Configuracion(ruta)
	assert config.obtener("ventana.ancho") == VALORES_POR_DEFECTO["ventana"]["ancho"]
	assert config.obtener("ventana.maximizada") is False
	assert config.obtener("ventana.alto") == 700
	assert config.obtener("general.minimizar_a_bandeja") is False
	# Las claves desconocidas (versión más nueva, complementos) se conservan.
	assert config.obtener("extra.a") == 1


def test_obtener_devuelve_copia(tmp_path):
	config = Configuracion(tmp_path / "config.json")
	ventana = config.obtener("ventana")
	ventana["ancho"] = 1
	assert config.obtener("ventana.ancho") == VALORES_POR_DEFECTO["ventana"]["ancho"]


def test_guardar_fallido_no_lanza(tmp_path):
	bloqueo = tmp_path / "soy_un_archivo"
	bloqueo.write_text("x", encoding="utf-8")
	config = Configuracion(bloqueo / "config.json")
	assert config.guardar() is False
