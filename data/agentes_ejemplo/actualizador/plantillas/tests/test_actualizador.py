"""Servicio de actualización: comprobación, anti-rollback, descarga y extracción segura.

PLANTILLA del agente «actualizador». Usa un servidor HTTP local (127.0.0.1) y
carpetas temporales: nunca sale a Internet ni toca el data/ real.
"""

from __future__ import annotations

import json
import logging
import stat
import sys
import zipfile

import pytest

pytest.importorskip("cryptography")

from apoyo_actualizador import Servidor, cargar_firmador, crear_zip, publicar  # noqa: E402

from app.services import actualizador  # noqa: E402

ARCHIVOS_V2 = {
	"MiAplicacion.exe": b"EXE 1.1.0",
	"lib/modulo.txt": b"modulo 1.1.0" * 2000,
	"data/sounds/aviso.wav": b"sonido 1.1.0",
	"data/config/ajustes.json": b'{"sobrescrito": true}',
	"data/db/base.db": b"base falsa",
	"data/leeme.txt": b"suelto en data",
}


@pytest.fixture
def servidor(tmp_path):
	instancia = Servidor(tmp_path / "servidor")
	yield instancia
	instancia.cerrar()


@pytest.fixture
def clave(tmp_path, monkeypatch):
	firmador = cargar_firmador()
	monkeypatch.delenv(firmador.VARIABLE_CONTRASENA, raising=False)
	ruta = tmp_path / "claves" / "firma.key"
	return ruta, firmador.generar(ruta)


@pytest.fixture
def config(tmp_path, servidor, clave):
	instalacion = tmp_path / "Mi App"
	(instalacion / "data" / "config").mkdir(parents=True)
	(instalacion / "MiAplicacion.exe").write_bytes(b"EXE 1.0.0")
	return actualizador.Configuracion(
		version_actual="1.0.0",
		carpeta_app=instalacion,
		url_manifiesto=servidor.url("version.json"),
		claves_publicas=(clave[1],),
		comando=[sys.executable, str(instalacion / "main.py")],
		permitir_desde_codigo=True,
	)


def _publicar(servidor, clave, version="1.1.0", archivos=None, **opciones):
	return publicar(servidor.carpeta, version, archivos or ARCHIVOS_V2, clave[0], **opciones)


# ── Comprobación ─────────────────────────────────────────────────────────────


def test_detecta_version_nueva_con_novedades(config, servidor, clave):
	_publicar(servidor, clave, novedades="- Arreglos\n- Mejoras de accesibilidad")
	info = actualizador.comprobar(config)
	assert info is not None and info.version == "1.1.0"
	assert info.novedades.startswith("- Arreglos")
	assert info.url_zip == servidor.url("MiAplicacion_v1.1.0.zip")
	assert "ultima_comprobacion" in actualizador.leer_estado(config)


def test_sin_version_nueva(config, servidor, clave):
	_publicar(servidor, clave, version="1.0.0")
	assert actualizador.comprobar(config) is None


def test_manifiesto_sin_firmar_se_rechaza(config, servidor):
	publicar(servidor.carpeta, "1.1.0", ARCHIVOS_V2, None)
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.comprobar(config)


def test_manifiesto_manipulado_en_el_servidor(config, servidor, clave):
	_publicar(servidor, clave)
	ruta = servidor.carpeta / "version.json"
	datos = json.loads(ruta.read_text(encoding="utf-8"))
	datos["version"] = "9.0.0"
	ruta.write_text(json.dumps(datos), encoding="utf-8")
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.comprobar(config)


def test_manifiesto_de_otra_aplicacion(config, servidor, clave):
	_publicar(servidor, clave, extra={"app": "OtraAplicacion"})
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.comprobar(config)


def test_http_fuera_de_localhost_se_rechaza(config):
	with pytest.raises(actualizador.ErrorRed):
		actualizador.comprobar(config, "http://ejemplo.invalid/version.json")


def test_servidor_caido(config, servidor, clave):
	with pytest.raises(actualizador.ErrorRed):
		actualizador.comprobar(config)  # no hay version.json: 404


def test_anti_rollback(config, servidor, clave, caplog):
	actualizador.confirmar_version(config, "1.3.0")
	_publicar(servidor, clave, version="1.2.0")
	with caplog.at_level(logging.WARNING), pytest.raises(actualizador.ErrorRollback):
		actualizador.comprobar(config)
	assert "Anti-rollback" in caplog.text
	actualizador.confirmar_version(config, "1.2.0")  # solo sube: sigue en 1.3.0
	assert actualizador.leer_estado(config)["version_confirmada"] == "1.3.0"
	_publicar(servidor, clave, version="1.3.0")
	assert actualizador.comprobar(config).version == "1.3.0"


def test_estado_danado_no_bloquea(config, servidor, clave):
	config.archivo_estado.parent.mkdir(parents=True, exist_ok=True)
	config.archivo_estado.write_text("{no es json", encoding="utf-8")
	_publicar(servidor, clave)
	assert actualizador.comprobar(config).version == "1.1.0"


def test_version_omitida_solo_en_la_periodica(config, servidor, clave):
	_publicar(servidor, clave)
	actualizador.omitir_version(config, "1.1.0")
	assert actualizador.comprobar(config, respetar_omitida=True) is None
	assert actualizador.comprobar(config, respetar_omitida=False).version == "1.1.0"


def test_confirmar_arranque_escribe_marcador(config):
	actualizador.confirmar_arranque(config, tras_actualizar=False)
	assert not config.marcador_salud.exists()
	assert actualizador.leer_estado(config)["version_confirmada"] == "1.0.0"
	actualizador.confirmar_arranque(config, tras_actualizar=True)
	assert config.marcador_salud.read_text(encoding="utf-8") == "ok 1.0.0\n"


# ── Descarga y verificación ──────────────────────────────────────────────────


def test_descarga_y_sha_correcto(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	fases = []
	ruta = actualizador.descargar(config, info, progreso=lambda f, h, t: fases.append((f, h, t)))
	actualizador.verificar_paquete(ruta, info)
	assert ruta.parent == config.carpeta_trabajo / "descarga"
	assert fases[-1] == ("descarga", info.tamano, info.tamano)


def test_sha_incorrecto_se_rechaza_y_se_borra(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	zip_servidor = servidor.carpeta / info.archivo
	datos = bytearray(zip_servidor.read_bytes())
	datos[-30] ^= 0xFF  # mismo tamaño, contenido distinto
	zip_servidor.write_bytes(bytes(datos))
	ruta = actualizador.descargar(config, info)
	with pytest.raises(actualizador.ErrorVerificacion):
		actualizador.verificar_paquete(ruta, info)
	assert not ruta.exists()


def test_descarga_mayor_de_lo_anunciado(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	with open(servidor.carpeta / info.archivo, "ab") as archivo:
		archivo.write(b"x" * 100_000)
	with pytest.raises(actualizador.ErrorVerificacion):
		actualizador.descargar(config, info)
	assert not list((config.carpeta_trabajo / "descarga").glob("*.parcial"))


def test_descarga_reanudable(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	mitad = info.tamano // 2
	servidor.cortar_en = mitad
	with pytest.raises(actualizador.ErrorRed):
		actualizador.descargar(config, info)
	parcial = config.carpeta_trabajo / "descarga" / (info.archivo + ".parcial")
	assert parcial.stat().st_size == mitad
	ruta = actualizador.descargar(config, info)
	actualizador.verificar_paquete(ruta, info)
	assert (info.archivo, f"bytes={mitad}-") in servidor.peticiones


def test_servidor_sin_rangos_empieza_de_cero(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	servidor.admite_rangos = False
	servidor.cortar_en = info.tamano // 2
	with pytest.raises(actualizador.ErrorRed):
		actualizador.descargar(config, info)
	ruta = actualizador.descargar(config, info)
	actualizador.verificar_paquete(ruta, info)


def test_cancelar_conserva_lo_descargado(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	llamadas = []

	def cancelado():
		llamadas.append(1)
		return len(llamadas) > 1

	with pytest.raises(actualizador.ErrorCancelado):
		actualizador.descargar(config, info, cancelado=cancelado)
	assert (config.carpeta_trabajo / "descarga" / (info.archivo + ".parcial")).stat().st_size > 0


# ── Extracción segura ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
	"nombre",
	[
		"../fuera.txt",
		"MiAplicacion/../../fuera.txt",
		"/absoluta.txt",
		"C:/Windows/fuera.txt",
		"C:fuera.txt",
		"a\\..\\..\\fuera.txt",
		"\\\\servidor\\recurso\\fuera.txt",
		"MiAplicacion/lib/archivo.txt:flujo",
		"MiAplicacion/CON.txt",
		"MiAplicacion/lib/punto_final.",
	],
)
def test_zip_con_rutas_peligrosas_se_rechaza(tmp_path, config, nombre):
	ruta = tmp_path / "malo.zip"
	with zipfile.ZipFile(ruta, "w") as paquete:
		paquete.writestr("MiAplicacion/MiAplicacion.exe", b"exe")
		paquete.writestr(nombre, b"malicioso")
	destino = config.carpeta_trabajo / "nueva"
	with pytest.raises(actualizador.ErrorPaquete):
		actualizador.extraer_seguro(ruta, destino, config)
	assert not destino.exists()
	assert not (tmp_path / "fuera.txt").exists()
	assert not list(tmp_path.rglob("fuera.txt"))


def test_zip_con_enlace_simbolico_se_rechaza(tmp_path, config):
	ruta = tmp_path / "enlace.zip"
	with zipfile.ZipFile(ruta, "w") as paquete:
		paquete.writestr("MiAplicacion/MiAplicacion.exe", b"exe")
		enlace = zipfile.ZipInfo("MiAplicacion/enlace")
		enlace.create_system = 3
		enlace.external_attr = (stat.S_IFLNK | 0o777) << 16
		paquete.writestr(enlace, "C:/Windows")
	with pytest.raises(actualizador.ErrorPaquete):
		actualizador.extraer_seguro(ruta, config.carpeta_trabajo / "nueva", config)


def test_zip_demasiado_grande_se_rechaza(tmp_path, config):
	config.max_expandido = 10_000
	ruta = crear_zip(tmp_path / "bomba.zip", {"MiAplicacion.exe": b"exe", "lib/relleno.bin": b"\0" * 50_000})
	assert ruta.stat().st_size < 10_000  # comprime mucho: por eso se mide lo expandido
	with pytest.raises(actualizador.ErrorPaquete):
		actualizador.extraer_seguro(ruta, config.carpeta_trabajo / "nueva", config)


def test_nombres_duplicados_sin_distinguir_mayusculas(tmp_path, config):
	ruta = crear_zip(tmp_path / "dup.zip", {"MiAplicacion.exe": b"exe", "lib/A.txt": b"1", "lib/a.TXT": b"2"})
	with pytest.raises(actualizador.ErrorPaquete):
		actualizador.extraer_seguro(ruta, config.carpeta_trabajo / "nueva", config)


def test_paquete_sin_ejecutable_se_rechaza(tmp_path, config):
	ruta = crear_zip(tmp_path / "ajeno.zip", {"otro.exe": b"x", "lib/a.txt": b"1"})
	with pytest.raises(actualizador.ErrorPaquete):
		actualizador.extraer_seguro(ruta, config.carpeta_trabajo / "nueva", config)


def test_zip_danado(tmp_path, config):
	ruta = tmp_path / "roto.zip"
	ruta.write_bytes(b"PK\x03\x04 esto no es un zip")
	with pytest.raises(actualizador.ErrorPaquete):
		actualizador.extraer_seguro(ruta, config.carpeta_trabajo / "nueva", config)


def test_extraccion_separa_recursos_y_descarta_datos_de_usuario(tmp_path, config, caplog):
	ruta = crear_zip(tmp_path / "bueno.zip", {**ARCHIVOS_V2, "lib/paquete/data/interno.txt": b"interno"})
	with caplog.at_level(logging.WARNING):
		destino = actualizador.extraer_seguro(ruta, config.carpeta_trabajo / "nueva", config)
	assert (destino / "programa" / "MiAplicacion.exe").read_bytes() == b"EXE 1.1.0"
	assert (destino / "programa" / "lib" / "paquete" / "data" / "interno.txt").is_file()
	assert (destino / "recursos" / "sounds" / "aviso.wav").read_bytes() == b"sonido 1.1.0"
	assert not (destino / "programa" / "data").exists()
	assert not (destino / "recursos" / "config").exists()
	assert not (destino / "recursos" / "db").exists()
	assert "data/config/ajustes.json" in caplog.text


def test_zip_sin_carpeta_raiz(tmp_path, config):
	ruta = crear_zip(tmp_path / "plano.zip", {"MiAplicacion.exe": b"exe", "lib/a.txt": b"1"}, carpeta_raiz=None)
	destino = actualizador.extraer_seguro(ruta, config.carpeta_trabajo / "nueva", config)
	assert (destino / "programa" / "lib" / "a.txt").is_file()


# ── Preparación, script y resultado ──────────────────────────────────────────


def test_descargar_y_preparar_genera_bat_crlf_sin_bom(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	fases = []
	ruta_bat = actualizador.descargar_y_preparar(config, info, progreso=lambda f, h, t: fases.append(f))
	datos = ruta_bat.read_bytes()
	assert ruta_bat.parent == config.carpeta_trabajo
	assert not datos.startswith(b"\xef\xbb\xbf")
	assert b"\n" not in datos.replace(b"\r\n", b"")
	assert {"descarga", "verificacion", "extraccion", "preparacion"} <= set(fases)
	assert not (config.carpeta_trabajo / "descarga" / info.archivo).exists()  # el ZIP ya no hace falta
	texto = datos.decode("utf-8")
	assert "{" + "app}" not in texto and "--tras-actualizar" in texto


def test_desde_codigo_no_se_instala(config, servidor, clave):
	config.permitir_desde_codigo = False
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	with pytest.raises(actualizador.ErrorActualizacion):
		actualizador.descargar_y_preparar(config, info)


def test_instalacion_sin_ejecutable_no_prepara(config, servidor, clave):
	_publicar(servidor, clave)
	info = actualizador.comprobar(config)
	(config.carpeta_app / "MiAplicacion.exe").unlink()
	with pytest.raises(actualizador.ErrorActualizacion):
		actualizador.descargar_y_preparar(config, info)


def test_resultado_revertida_avisa_y_omite(config):
	config.carpeta_trabajo.mkdir(parents=True)
	config.archivo_resultado.write_text(
		"estado=revertida\nmotivo=sin_marcador_de_salud\nversion=1.1.0\nanterior=1.0.0\n", encoding="utf-8"
	)
	(config.carpeta_trabajo / "nueva").mkdir()
	mensaje = actualizador.leer_resultado_anterior(config)
	assert "1.1.0" in mensaje and "1.0.0" in mensaje
	assert actualizador.leer_estado(config)["version_omitida"] == "1.1.0"
	assert not config.archivo_resultado.exists()
	assert not (config.carpeta_trabajo / "nueva").exists()
	assert actualizador.leer_resultado_anterior(config) is None


def test_resultado_correcto_no_molesta(config):
	config.carpeta_trabajo.mkdir(parents=True)
	config.archivo_resultado.write_text("estado=correcta\nmotivo=ninguno\nversion=1.1.0\n", encoding="utf-8")
	assert actualizador.leer_resultado_anterior(config) is None


def test_traduccion_con_gettext_instalado(monkeypatch):
	import builtins

	monkeypatch.setattr(builtins, "_", lambda texto: texto.upper(), raising=False)
	assert actualizador._("hola") == "HOLA"
