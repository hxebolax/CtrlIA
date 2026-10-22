"""Pruebas de app/core/secretos.py (PLANTILLA del agente «configuracion»).

La prueba de DPAPI llama de verdad a CryptProtectData/CryptUnprotectData, así
que solo se ejecuta en Windows.
"""

from __future__ import annotations

import logging
import sys
import threading

import pytest

from app.core import secretos as mod
from app.core.secretos import AlmacenSecretos, ErrorSecretos

solo_windows = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI solo existe en Windows")

SECRETO = "tóken-súper-secreto-ñ-🔑-1234567890"


@pytest.fixture
def ruta(tmp_path, monkeypatch):
	monkeypatch.setenv(mod.VARIABLE_DATA, str(tmp_path / "data"))
	return tmp_path / "data" / "config" / "secretos.dat"


@solo_windows
def test_dpapi_ida_y_vuelta():
	dpapi = mod.ProteccionDPAPI()
	datos = SECRETO.encode("utf-8")
	cifrado = dpapi.proteger(datos)
	assert cifrado != datos and datos not in cifrado
	assert dpapi.desproteger(cifrado) == datos


@solo_windows
def test_dpapi_con_otra_entropia_no_descifra():
	"""Simula «otro equipo/usuario»: con otra entropía DPAPI rechaza el blob."""
	cifrado = mod.ProteccionDPAPI(entropia=b"A").proteger(b"hola")
	with pytest.raises(ErrorSecretos):
		mod.ProteccionDPAPI(entropia=b"B").desproteger(cifrado)


@solo_windows
def test_almacen_por_defecto_en_data_config(ruta, caplog):
	caplog.set_level(logging.DEBUG)
	almacen = AlmacenSecretos()
	assert almacen.ruta == ruta.resolve()
	almacen.guardar("cuenta.token", SECRETO)
	assert ruta.is_file()
	contenido = ruta.read_bytes()
	assert SECRETO.encode("utf-8") not in contenido
	assert b"cuenta.token" not in contenido, "ni siquiera los nombres van en claro"
	assert AlmacenSecretos().obtener("cuenta.token") == SECRETO
	assert SECRETO not in caplog.text, "los valores nunca van al registro"


@solo_windows
def test_guardar_varios_borrar_y_nombres(ruta):
	almacen = AlmacenSecretos()
	almacen.guardar_varios({"a": "1", "b": "2"})
	almacen.borrar("a")
	assert almacen.nombres() == ["b"]
	assert almacen.obtener("a", "nada") == "nada"


@solo_windows
def test_ilegible_se_aparta_y_no_se_pisa(ruta):
	AlmacenSecretos(proteccion=mod.ProteccionDPAPI(entropia=b"equipo-1")).guardar("t", "x")
	otro = AlmacenSecretos(proteccion=mod.ProteccionDPAPI(entropia=b"equipo-2"))
	assert otro.obtener("t") is None
	assert otro.ilegibles is True
	otro.guardar("t", "nuevo")
	apartados = list(ruta.parent.glob("secretos.dat.ilegible-*"))
	assert len(apartados) == 1
	# El apartado sigue siendo legible con la protección original.
	original = AlmacenSecretos(ruta=apartados[0], proteccion=mod.ProteccionDPAPI(entropia=b"equipo-1"))
	assert original.obtener("t") == "x"
	assert otro.obtener("t") == "nuevo"


@solo_windows
def test_concurrencia_sin_actualizaciones_perdidas(ruta):
	almacen = AlmacenSecretos()
	hilos = [threading.Thread(target=almacen.guardar, args=(f"s{i}", str(i))) for i in range(10)]
	for hilo in hilos:
		hilo.start()
	for hilo in hilos:
		hilo.join()
	assert almacen.nombres() == sorted(f"s{i}" for i in range(10))


def test_fernet_opcional(tmp_path):
	pytest.importorskip("cryptography")
	clave = tmp_path / "secretos.key"
	almacen = AlmacenSecretos(ruta=tmp_path / "secretos.dat", proteccion=mod.ProteccionFernet(clave))
	almacen.guardar("api", SECRETO)
	assert clave.is_file()
	assert SECRETO.encode("utf-8") not in (tmp_path / "secretos.dat").read_bytes()
	otra = AlmacenSecretos(ruta=tmp_path / "secretos.dat", proteccion=mod.ProteccionFernet(clave))
	assert otra.obtener("api") == SECRETO


def test_archivo_basura_se_trata_como_ilegible(tmp_path):
	class SinCifrado:
		nombre = "prueba"

		def proteger(self, datos):
			return datos

		def desproteger(self, datos):
			return datos

	ruta = tmp_path / "secretos.dat"
	ruta.write_text("no es json", encoding="utf-8")
	almacen = AlmacenSecretos(ruta=ruta, proteccion=SinCifrado())
	assert almacen.obtener("x") is None
	assert almacen.ilegibles is True
