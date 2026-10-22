"""Pruebas de la instancia única: mutex real de wx, otro proceso y buzón de órdenes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

import pytest

from app.core import instancia_unica as iu


@pytest.fixture
def id_unico():
	# Un identificador distinto en cada prueba: no choca con la aplicación
	# real abierta ni con otra prueba.
	return f"Prueba{uuid.uuid4().hex[:8]}"


def test_nombre_exclusion_seguro_y_ligado_a_data(tmp_path):
	uno = iu.nombre_exclusion("Mi App", tmp_path / "a")
	otro = iu.nombre_exclusion("Mi App", tmp_path / "b")
	assert uno != otro, "dos copias portables en carpetas distintas pueden convivir"
	assert uno == iu.nombre_exclusion("Mi App", tmp_path / "a")
	assert all(c.isalnum() or c in "-_" for c in uno)


def test_dos_comprobadores_en_el_mismo_proceso(app_wx, tmp_path, id_unico):
	primera = iu.InstanciaUnica(id_unico, carpeta=tmp_path)
	assert primera.hay_otra() is False
	segunda = iu.InstanciaUnica(id_unico, carpeta=tmp_path)
	assert segunda.hay_otra() is True
	segunda.liberar()
	primera.liberar()
	# Liberados los dos, una nueva instancia vuelve a ser la primera.
	tercera = iu.InstanciaUnica(id_unico, carpeta=tmp_path)
	assert tercera.hay_otra() is False
	tercera.liberar()


def test_otro_proceso_detecta_la_instancia(app_wx, tmp_path, id_unico):
	primera = iu.InstanciaUnica(id_unico, carpeta=tmp_path)
	codigo = (
		"import wx, sys; app = wx.App(False); "
		f"c = wx.SingleInstanceChecker({primera.nombre!r}); "
		"sys.stdout.write(str(c.IsAnotherRunning()))"
	)
	resultado = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60)
	primera.liberar()
	assert resultado.returncode == 0, resultado.stderr
	assert resultado.stdout.strip() == "True"


def test_buzon_mostrar_y_abrir(tmp_path):
	assert iu.dejar_orden(iu.ORDEN_MOSTRAR, carpeta=tmp_path)
	assert iu.dejar_orden(iu.ORDEN_ABRIR, ["C:/uno.txt", "C:/dos.txt"], carpeta=tmp_path)
	ordenes = iu.recoger_ordenes(tmp_path)
	assert [o.orden for o in ordenes] == [iu.ORDEN_MOSTRAR, iu.ORDEN_ABRIR]
	assert ordenes[1].argumentos == ["C:/uno.txt", "C:/dos.txt"]
	# Recoger las consume.
	assert iu.recoger_ordenes(tmp_path) == []
	assert not list(tmp_path.glob("*.tmp"))


def test_buzon_descarta_caducadas_desconocidas_y_danadas(tmp_path):
	assert iu.dejar_orden("formatear_disco", carpeta=tmp_path) is False
	iu.dejar_orden(iu.ORDEN_MOSTRAR, carpeta=tmp_path)
	(tmp_path / "orden_0_roto.json").write_text("{no json", encoding="utf-8")
	(tmp_path / "orden_1_ajena.json").write_text(json.dumps({"orden": "otra", "creado": time.time()}), encoding="utf-8")
	assert iu.recoger_ordenes(tmp_path, ahora=time.time() + iu.MAX_EDAD_SEGUNDOS + 5) == []
	assert not list(tmp_path.glob("orden_*.json")), "lo inservible también se borra"


def test_registrar_ventana_y_liberar(app_wx, tmp_path, id_unico):
	instancia = iu.InstanciaUnica(id_unico, carpeta=tmp_path)
	instancia.registrar_ventana(12345)
	assert instancia.leer_primera() == {"pid": os.getpid(), "hwnd": 12345}
	instancia.liberar()
	assert not (tmp_path / iu.ARCHIVO_INSTANCIA).exists()


def test_pedir_mostrar_sin_primera_deja_la_orden(app_wx, tmp_path, id_unico):
	instancia = iu.InstanciaUnica(id_unico, carpeta=tmp_path)
	assert instancia.pedir_mostrar(["C:/archivo.txt"]) is True
	assert [o.orden for o in instancia.recoger_ordenes()] == [iu.ORDEN_MOSTRAR, iu.ORDEN_ABRIR]
	instancia.liberar()


def test_primer_plano_con_valores_invalidos():
	assert iu.traer_al_frente(0) is False
	assert iu.permitir_primer_plano(0) is False
