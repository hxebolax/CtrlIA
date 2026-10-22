"""Dos fuentes de actualización (servidor propio y GitHub) con servidores LOCALES simulados.

Cada «fuente» es un servidor HTTP local (``::1`` o 127.0.0.1) de
``tests.apoyo_actualizador``: las pruebas nunca salen a Internet. La primera
hace de servidor propio y la segunda de GitHub.
"""

from __future__ import annotations

import shutil
import sys

import pytest

pytest.importorskip("cryptography")

from tests.apoyo_actualizador import Servidor, cargar_firmador, publicar  # noqa: E402

from app.services import actualizador  # noqa: E402

ARCHIVOS = {"CtrlIA.exe": b"EXE nueva", "lib/modulo.txt": b"modulo nuevo" * 3000}


@pytest.fixture
def clave(tmp_path, monkeypatch):
	firmador = cargar_firmador()
	monkeypatch.delenv(firmador.VARIABLE_CONTRASENA, raising=False)
	ruta = tmp_path / "claves" / "firma.key"
	return ruta, firmador.generar(ruta)


@pytest.fixture
def fuentes(tmp_path):
	"""(servidor propio, «GitHub»), dos servidores locales independientes."""
	propio, github = Servidor(tmp_path / "propio"), Servidor(tmp_path / "github")
	yield propio, github
	propio.cerrar()
	github.cerrar()


@pytest.fixture
def config(tmp_path, fuentes, clave):
	propio, github = fuentes
	instalacion = tmp_path / "Mi App"
	(instalacion / "data" / "config").mkdir(parents=True)
	(instalacion / "CtrlIA.exe").write_bytes(b"EXE 1.0.0")
	return actualizador.Configuracion(
		version_actual="1.0.0",
		carpeta_app=instalacion,
		url_manifiesto=[propio.url("version.json"), github.url("version.json")],
		claves_publicas=(clave[1],),
		comando=[sys.executable, str(instalacion / "main.py")],
		permitir_desde_codigo=True,
	)


def _publicar(servidor, clave, version="1.1.0", **opciones):
	return publicar(servidor.carpeta, version, ARCHIVOS, clave[0], **opciones)


def _copiar_publicacion(origen, destino) -> None:
	"""Misma publicación (mismo version.json firmado y mismo ZIP) en las dos fuentes."""
	for archivo in origen.carpeta.iterdir():
		shutil.copy2(archivo, destino.carpeta / archivo.name)


def _registro(config) -> str:
	return config.registro_script.read_text(encoding="utf-8")


# ── Configuración de las fuentes ─────────────────────────────────────────────


def test_fuentes_por_defecto_servidor_y_github():
	assert actualizador.URLS_MANIFIESTO[0] == actualizador.URL_MANIFIESTO
	assert actualizador.URLS_MANIFIESTO[1] == "https://github.com/hxebolax/CtrlIA/releases/latest/download/version.json"
	for url in actualizador.URLS_MANIFIESTO:
		actualizador._comprobar_url(url)  # https: no lanza


def test_url_manifiesto_admite_texto_y_lista(tmp_path):
	base = {"version_actual": "1.0.0", "carpeta_app": tmp_path, "comando": []}
	una = actualizador.Configuracion(**base, url_manifiesto="https://a.example/version.json")
	assert una.urls_manifiesto == ("https://a.example/version.json",)
	varias = actualizador.Configuracion(**base, url_manifiesto="https://a.example/v.json; https://b.example/v.json\n")
	assert varias.urls_manifiesto == ("https://a.example/v.json", "https://b.example/v.json")
	lista = actualizador.Configuracion(**base, url_manifiesto=["https://a.example/v.json", "", "https://a.example/v.json"])
	assert lista.urls_manifiesto == ("https://a.example/v.json",)
	assert actualizador.Configuracion(**base).urls_manifiesto == actualizador.URLS_MANIFIESTO


def test_nombre_de_fuente():
	assert actualizador.nombre_fuente(actualizador.URL_MANIFIESTO_GITHUB) == "GitHub"
	assert actualizador.nombre_fuente("https://objects.githubusercontent.com/x") == "GitHub"
	assert actualizador.nombre_fuente(actualizador.URL_MANIFIESTO) == "xebolax.synology.me"


def test_zip_de_github_junto_al_manifiesto():
	"""El ZIP de GitHub se resuelve con urljoin junto a releases/latest/download/version.json."""
	import urllib.parse

	assert (
		urllib.parse.urljoin(actualizador.URL_MANIFIESTO_GITHUB, "CtrlIA_v1.5.0.zip")
		== "https://github.com/hxebolax/CtrlIA/releases/latest/download/CtrlIA_v1.5.0.zip"
	)


# ── Comprobación con dos fuentes ─────────────────────────────────────────────


def test_servidor_bien(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave)
	_copiar_publicacion(propio, github)
	info = actualizador.comprobar(config)
	assert info.version == "1.1.0"
	assert info.fuente == propio.url("version.json")  # en empate gana la primera
	assert info.url_zip == propio.url("CtrlIA_v1.1.0.zip")
	assert info.urls_alternativas == (github.url("CtrlIA_v1.1.0.zip"),)
	assert "Elegida la version 1.1.0" in _registro(config)


def test_servidor_caido_y_github_bien(config, fuentes, clave):
	propio, github = fuentes
	_publicar(github, clave)  # el propio no tiene version.json: 404
	info = actualizador.comprobar(config)
	assert info.version == "1.1.0"
	assert info.fuente == github.url("version.json")
	assert info.urls_alternativas == ()
	registro = _registro(config)
	assert f"({propio.url('version.json')}): fallo (ErrorRed)" in registro
	assert f"de ::1 ({github.url('version.json')})" in registro or github.url("version.json") in registro


def test_servidor_sin_conexion_y_github_bien(config, fuentes, clave, tmp_path):
	propio, github = fuentes
	_publicar(github, clave)
	cerrado = Servidor(tmp_path / "cerrado")
	url_cerrada = cerrado.url("version.json")
	cerrado.cerrar()  # conexión rechazada
	config.url_manifiesto = (url_cerrada, github.url("version.json"))
	assert actualizador.comprobar(config).fuente == github.url("version.json")


def test_github_con_version_mayor(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave, version="1.1.0")
	_publicar(github, clave, version="1.2.0", novedades="- Lo nuevo de la 1.2.0")
	info = actualizador.comprobar(config)
	assert info.version == "1.2.0"
	assert info.url_zip == github.url("CtrlIA_v1.2.0.zip")
	assert info.novedades == "- Lo nuevo de la 1.2.0"
	assert info.urls_alternativas == ()  # el propio anuncia otro paquete


def test_servidor_con_version_mayor_que_github(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave, version="1.3.0")
	_publicar(github, clave, version="1.2.0")
	assert actualizador.comprobar(config).fuente == propio.url("version.json")


def test_firma_mala_en_el_servidor_usa_github(config, fuentes, clave, tmp_path):
	propio, github = fuentes
	otra_clave = tmp_path / "otra" / "firma.key"
	cargar_firmador().generar(otra_clave)
	# El servidor propio publica una versión MAYOR pero firmada con otra clave: no cuenta.
	publicar(propio.carpeta, "9.0.0", ARCHIVOS, otra_clave)
	_publicar(github, clave, version="1.1.0")
	info = actualizador.comprobar(config)
	assert info.version == "1.1.0" and info.fuente == github.url("version.json")
	assert "fallo (ErrorFirma)" in _registro(config)


def test_firma_mala_en_github_usa_el_servidor(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave, version="1.1.0")
	publicar(github.carpeta, "2.0.0", ARCHIVOS, None)  # sin firmar
	info = actualizador.comprobar(config)
	assert info.version == "1.1.0" and info.fuente == propio.url("version.json")


def test_manifiesto_danado_en_una_fuente(config, fuentes, clave):
	propio, github = fuentes
	(propio.carpeta / "version.json").write_text("{esto no es json", encoding="utf-8")
	_publicar(github, clave)
	assert actualizador.comprobar(config).fuente == github.url("version.json")


def test_las_dos_fuentes_caidas(config):
	with pytest.raises(actualizador.ErrorRed) as error:
		actualizador.comprobar(config)
	assert "ninguna de las fuentes" in str(error.value)
	assert "Comprueba la conexión" in str(error.value)


def test_las_dos_fallan_y_una_por_firma_da_el_error_de_firma(config, fuentes):
	propio, github = fuentes
	publicar(github.carpeta, "2.0.0", ARCHIVOS, None)  # sin firmar; el propio, caído
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.comprobar(config)


def test_sin_version_nueva_en_ninguna(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave, version="1.0.0")
	_publicar(github, clave, version="0.9.0")
	assert actualizador.comprobar(config) is None


def test_anti_rollback_y_omitida_con_dos_fuentes(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave, version="1.1.0")
	_publicar(github, clave, version="1.2.0")
	actualizador.omitir_version(config, "1.2.0")
	assert actualizador.comprobar(config, respetar_omitida=True) is None
	assert actualizador.comprobar(config, respetar_omitida=False).version == "1.2.0"
	actualizador.confirmar_version(config, "1.3.0")
	with pytest.raises(actualizador.ErrorRollback):
		actualizador.comprobar(config)


# ── Novedades ────────────────────────────────────────────────────────────────


def test_manifiesto_sin_novedades(config, fuentes, clave):
	_publicar(fuentes[0], clave)
	assert actualizador.comprobar(config).novedades == ""


def test_manifiesto_con_novedades(config, fuentes, clave):
	_publicar(fuentes[0], clave, novedades="- Arreglo A\n- Mejora B\n")
	assert actualizador.comprobar(config).novedades == "- Arreglo A\n- Mejora B"


def test_novedades_en_lista_tambien_valen(config, fuentes, clave):
	_publicar(fuentes[0], clave, extra={actualizador.CAMPO_NOVEDADES: ["- Uno", "- Dos"]})
	assert actualizador.comprobar(config).novedades == "- Uno\n- Dos"


# ── Descarga con alternativa ─────────────────────────────────────────────────


def test_descarga_falla_en_el_servidor_y_va_a_github(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave)
	_copiar_publicacion(propio, github)
	info = actualizador.comprobar(config)
	(propio.carpeta / info.archivo).unlink()  # el servidor da 404 al bajar el ZIP
	ruta = actualizador.descargar(config, info, verificar=True)
	assert ruta.is_file()
	assert any(nombre == info.archivo for nombre, _rango in github.peticiones)
	registro = _registro(config)
	assert "Fallo desde ::1 (ErrorRed)" in registro and "se intenta con" in registro
	assert "Paquete descargado desde ::1 y verificado" in registro


def test_descarga_cortada_continua_en_la_otra_fuente(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave)
	_copiar_publicacion(propio, github)
	info = actualizador.comprobar(config)
	mitad = info.tamano // 2
	propio.cortar_en = mitad
	ruta = actualizador.descargar(config, info, verificar=True)
	actualizador.verificar_paquete(ruta, info)
	# La otra fuente continúa donde se quedó (mismo paquete, mismo SHA-256).
	assert (info.archivo, f"bytes={mitad}-") in github.peticiones


def test_huella_mala_en_una_fuente_usa_la_otra(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave)
	_copiar_publicacion(propio, github)
	info = actualizador.comprobar(config)
	zip_propio = propio.carpeta / info.archivo
	datos = bytearray(zip_propio.read_bytes())
	datos[-30] ^= 0xFF  # mismo tamaño, contenido distinto
	zip_propio.write_bytes(bytes(datos))
	ruta = actualizador.descargar(config, info, verificar=True)
	assert actualizador.sha256_de(ruta) == info.sha256


def test_descarga_falla_en_todas(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave)
	_copiar_publicacion(propio, github)
	info = actualizador.comprobar(config)
	(propio.carpeta / info.archivo).unlink()
	(github.carpeta / info.archivo).unlink()
	with pytest.raises(actualizador.ErrorRed):
		actualizador.descargar(config, info)


def test_sin_alternativa_si_el_paquete_es_distinto(config, fuentes, clave):
	propio, github = fuentes
	_publicar(propio, clave)
	publicar(github.carpeta, "1.1.0", {**ARCHIVOS, "otro.txt": b"distinto"}, clave[0])  # otra huella
	info = actualizador.comprobar(config)
	assert info.fuente == propio.url("version.json")
	assert info.urls_alternativas == ()


# ── Recursos de data que llegan con cada actualización ───────────────────────


def test_data_actualizable_coincide_con_lo_distribuido():
	"""Lo que setup_cx.py mete en data (DATA_DISTRIBUIBLE) debe poder actualizarse."""
	import ast
	from pathlib import Path

	arbol = ast.parse((Path(__file__).resolve().parents[1] / "setup_cx.py").read_text(encoding="utf-8"))
	distribuible = next(
		ast.literal_eval(nodo.value)
		for nodo in ast.walk(arbol)
		if isinstance(nodo, ast.Assign) and any(getattr(d, "id", "") == "DATA_DISTRIBUIBLE" for d in nodo.targets)
	)
	assert set(actualizador.DATA_ACTUALIZABLE) == set(distribuible)
	assert {"skills_ejemplo", "plugins"} <= set(actualizador.DATA_ACTUALIZABLE)


def test_skills_de_ejemplo_y_plugins_llegan_con_la_actualizacion(tmp_path):
	from tests.apoyo_actualizador import crear_zip

	config = actualizador.Configuracion(version_actual="1.0.0", carpeta_app=tmp_path / "app", comando=[])
	ruta = crear_zip(
		tmp_path / "p.zip",
		{
			"CtrlIA.exe": b"EXE",
			"data/skills_ejemplo/revisor.md": b"skill nueva",
			"data/plugins/extra/plugin.json": b"{}",
			"data/config/settings.json": b"{}",
		},
	)
	destino = actualizador.extraer_seguro(ruta, tmp_path / "nueva", config)
	assert (destino / "recursos" / "skills_ejemplo" / "revisor.md").read_bytes() == b"skill nueva"
	assert (destino / "recursos" / "plugins" / "extra" / "plugin.json").is_file()
	assert not (destino / "recursos" / "config").exists()  # lo del usuario nunca


# ── Reutilizar la descarga tras «Más tarde» ──────────────────────────────────


def test_mas_tarde_conserva_el_zip_y_se_reutiliza(config, fuentes, clave):
	propio, _github = fuentes
	_publicar(propio, clave)
	info = actualizador.comprobar(config)
	actualizador.descargar_y_preparar(config, info)
	assert (config.carpeta_trabajo / "nueva").is_dir()
	actualizador.descartar_preparada(config)
	assert not (config.carpeta_trabajo / "nueva").exists()
	assert not (config.carpeta_trabajo / "actualizar.bat").exists()
	assert (config.carpeta_trabajo / "descarga" / info.archivo).is_file()
	descargas_antes = sum(1 for nombre, _r in propio.peticiones if nombre == info.archivo)
	ruta_bat = actualizador.descargar_y_preparar(config, info)
	assert ruta_bat.is_file()
	assert sum(1 for nombre, _r in propio.peticiones if nombre == info.archivo) == descargas_antes
	assert "Se reutiliza el paquete" in _registro(config)


def test_zip_guardado_que_no_coincide_se_vuelve_a_bajar(config, fuentes, clave):
	propio, _github = fuentes
	_publicar(propio, clave)
	info = actualizador.comprobar(config)
	carpeta = config.carpeta_trabajo / "descarga"
	carpeta.mkdir(parents=True)
	(carpeta / info.archivo).write_bytes(b"x" * info.tamano)  # mismo tamaño, otra huella
	(carpeta / "CtrlIA_v0.9.0.zip").write_bytes(b"resto viejo")
	ruta = actualizador.descargar(config, info, verificar=True)
	assert actualizador.sha256_de(ruta) == info.sha256
	assert not (carpeta / "CtrlIA_v0.9.0.zip").exists()
