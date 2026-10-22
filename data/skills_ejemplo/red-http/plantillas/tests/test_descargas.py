"""Pruebas de las descargas (PLANTILLA del agente «red-http»).

Todas contra el servidor local de servidor_http_prueba.py y en carpetas temporales.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest
import requests

from app.core.descargas import (
	SUFIJO_METADATOS,
	SUFIJO_PARCIAL,
	DescargaCancelada,
	ErrorDescarga,
	ErrorIntegridad,
	descargar,
	nombre_seguro,
)
from app.core.red import ConfiguracionRed, ErrorRed, clasificar_error, crear_sesion
from servidor_http_prueba import CONTENIDO, ETAG, servidor  # noqa: F401 - fixture

SHA_CONTENIDO = hashlib.sha256(CONTENIDO).hexdigest()


@pytest.fixture
def sesion():
	return crear_sesion(ConfiguracionRed(proxy="ninguno", reintentos=0, tiempo_lectura=5))


def _parciales(carpeta):
	return sorted(p.name for p in carpeta.iterdir() if p.name.endswith((SUFIJO_PARCIAL, SUFIJO_METADATOS)))


def test_descarga_completa_con_sha256(servidor, sesion, tmp_path):
	avances = []
	destino = tmp_path / "archivo.bin"
	resultado = descargar(servidor.url("/archivo.bin"), destino, sha256=SHA_CONTENIDO, sesion=sesion,
		al_progresar=lambda hecho, total: avances.append((hecho, total)))
	assert destino.read_bytes() == CONTENIDO
	assert resultado.tamano == len(CONTENIDO) and resultado.sha256 == SHA_CONTENIDO
	assert not resultado.reanudada
	assert avances[-1] == (len(CONTENIDO), len(CONTENIDO))  # la llamada final siempre llega
	assert _parciales(tmp_path) == []
	# Pidió los bytes tal cual: sin compresión, tamaños y rangos cuadran.
	assert servidor.cabeceras_de("/archivo.bin")[-1].get("Accept-Encoding") == "identity"


def test_sha256_incorrecto(servidor, sesion, tmp_path):
	destino = tmp_path / "archivo.bin"
	with pytest.raises(ErrorIntegridad) as info:
		descargar(servidor.url("/archivo.bin"), destino, sha256="0" * 64, sesion=sesion)
	assert not destino.exists()
	assert _parciales(tmp_path) == []  # un parcial corrupto no sirve para reanudar
	assert "dañado" in info.value.mensaje_usuario and info.value.reintentar


def test_reanuda_con_range(servidor, sesion, tmp_path):
	destino = tmp_path / "archivo.bin"
	(tmp_path / ("archivo.bin" + SUFIJO_PARCIAL)).write_bytes(CONTENIDO[:100_000])
	(tmp_path / ("archivo.bin" + SUFIJO_METADATOS)).write_text(json.dumps({"etag": ETAG}), encoding="utf-8")
	ruta = "/archivo.bin?id=reanudar"
	resultado = descargar(servidor.url(ruta), destino, sha256=SHA_CONTENIDO, sesion=sesion)
	cabeceras = servidor.cabeceras_de(ruta)[-1]
	assert cabeceras["Range"] == "bytes=100000-" and cabeceras["If-Range"] == ETAG
	assert resultado.reanudada and destino.read_bytes() == CONTENIDO
	assert _parciales(tmp_path) == []


def test_archivo_cambiado_empieza_de_cero(servidor, sesion, tmp_path):
	destino = tmp_path / "archivo.bin"
	(tmp_path / ("archivo.bin" + SUFIJO_PARCIAL)).write_bytes(b"x" * 5000)  # restos de otra versión
	(tmp_path / ("archivo.bin" + SUFIJO_METADATOS)).write_text(json.dumps({"etag": '"viejo"'}), encoding="utf-8")
	resultado = descargar(servidor.url("/archivo.bin?id=cambiado"), destino, sha256=SHA_CONTENIDO, sesion=sesion)
	assert not resultado.reanudada and destino.read_bytes() == CONTENIDO


def test_servidor_sin_range(servidor, sesion, tmp_path):
	destino = tmp_path / "sinrango.bin"
	(tmp_path / ("sinrango.bin" + SUFIJO_PARCIAL)).write_bytes(CONTENIDO[:50_000])
	resultado = descargar(servidor.url("/sinrango.bin"), destino, sha256=SHA_CONTENIDO, sesion=sesion)
	assert not resultado.reanudada  # contestó 200: se reinició sin mezclar datos
	assert destino.read_bytes() == CONTENIDO


def test_parcial_ya_completo_416(servidor, sesion, tmp_path):
	destino = tmp_path / "archivo.bin"
	(tmp_path / ("archivo.bin" + SUFIJO_PARCIAL)).write_bytes(CONTENIDO)
	resultado = descargar(servidor.url("/archivo.bin?id=completo"), destino, sha256=SHA_CONTENIDO, sesion=sesion)
	assert resultado.reanudada and destino.read_bytes() == CONTENIDO


def test_corte_y_reanudacion(servidor, tmp_path):
	sesion = crear_sesion(ConfiguracionRed(proxy="ninguno", reintentos=0, tiempo_lectura=0.5))
	destino = tmp_path / "cortado.bin"
	with pytest.raises((ErrorRed, requests.RequestException)) as info:
		descargar(servidor.url("/cortado.bin"), destino, sesion=sesion, tamano_bloque=8192)
	clasificado = clasificar_error(info.value)
	assert clasificado.categoria == "red" and clasificado.reintentar
	parcial = tmp_path / ("cortado.bin" + SUFIJO_PARCIAL)
	assert 0 < parcial.stat().st_size < len(CONTENIDO) and not destino.exists()
	resultado = descargar(servidor.url("/cortado.bin"), destino, sha256=SHA_CONTENIDO, sesion=sesion)
	assert resultado.reanudada and destino.read_bytes() == CONTENIDO


@pytest.mark.parametrize("conservar", [True, False])
def test_cancelacion(servidor, sesion, tmp_path, conservar):
	cancelar = threading.Event()
	destino = tmp_path / "goteo.bin"
	with pytest.raises(DescargaCancelada) as info:
		descargar(servidor.url(f"/goteo.bin?id={conservar}"), destino, sesion=sesion, cancelacion=cancelar,
			conservar_parcial=conservar, progresos_por_segundo=0, tamano_bloque=4096,
			al_progresar=lambda hecho, total: hecho > 20_000 and cancelar.set())
	assert info.value.parcial_conservado is conservar
	assert not destino.exists()
	parcial = tmp_path / ("goteo.bin" + SUFIJO_PARCIAL)
	assert parcial.exists() is conservar
	assert clasificar_error(info.value).categoria == "cancelado"


def test_progreso_limitado(servidor, sesion, tmp_path):
	llamadas = []
	inicio = time.monotonic()
	descargar(servidor.url("/goteo.bin?id=progreso"), tmp_path / "g.bin", sesion=sesion, tamano_bloque=1024,
		progresos_por_segundo=4, al_progresar=lambda hecho, total: llamadas.append(hecho))
	segundos = time.monotonic() - inicio
	# ~300 bloques en ~0,75 s: como mucho 4 por segundo, más la primera y la final.
	assert 2 <= len(llamadas) <= segundos * 4 + 2, (len(llamadas), segundos)
	assert llamadas[-1] == len(CONTENIDO)


def test_tamano_maximo(servidor, sesion, tmp_path):
	with pytest.raises(ErrorDescarga) as info:
		descargar(servidor.url("/archivo.bin?id=grande"), tmp_path / "a.bin", sesion=sesion, tamano_maximo=1000)
	assert info.value.categoria == "cliente"
	assert _parciales(tmp_path) == []


def test_no_sobrescribe_sin_permiso(servidor, sesion, tmp_path):
	(tmp_path / "archivo.bin").write_bytes(b"del usuario")
	resultado = descargar(servidor.url("/archivo.bin?id=nombre"), tmp_path / "archivo.bin", sesion=sesion)
	assert resultado.ruta.name == "archivo (1).bin"
	assert (tmp_path / "archivo.bin").read_bytes() == b"del usuario"


def test_error_404_con_mensaje(servidor, sesion, tmp_path):
	with pytest.raises(ErrorRed) as info:
		descargar(servidor.url("/404"), tmp_path / "x.bin", sesion=sesion)
	assert info.value.codigo_http == 404 and "404" in info.value.mensaje_usuario
	assert not info.value.reintentar


def test_carpeta_temporal_aparte(servidor, sesion, tmp_path):
	temporal = tmp_path / "data" / "temp"
	destino = tmp_path / "descargas" / "archivo.bin"
	destino.parent.mkdir()
	descargar(servidor.url("/archivo.bin?id=temp"), destino, sesion=sesion, carpeta_temporal=temporal)
	assert destino.read_bytes() == CONTENIDO and list(temporal.iterdir()) == []


# ── Nombres de archivo ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("cabecera", "esperado"), [
	('attachment; filename="..\\..\\CON.txt"', "_CON.txt"),
	('attachment; filename="../../etc/passwd"', "passwd"),
	("attachment; filename*=UTF-8''%E2%82%AC%20informe.pdf", "€ informe.pdf"),
	('attachment; filename="a<b>c:d|e?.txt"', "a_b_c_d_e_.txt"),
])
def test_nombre_desde_content_disposition(servidor, sesion, tmp_path, cabecera, esperado):
	from urllib.parse import quote

	resultado = descargar(servidor.url("/disposicion?cd=" + quote(cabecera)), tmp_path, sesion=sesion)
	assert resultado.ruta.name == esperado
	assert resultado.ruta.parent == tmp_path  # nunca fuera de la carpeta elegida


def test_nombre_seguro():
	assert nombre_seguro("..\\..\\Windows\\system32\\evil.dll") == "evil.dll"
	assert nombre_seguro("/etc/passwd") == "passwd"
	assert nombre_seguro("NUL.txt") == "_NUL.txt"
	assert nombre_seguro("com1") == "_com1"
	assert nombre_seguro("LPT¹.log") == "_LPT¹.log"
	assert nombre_seguro("CONSOLA.txt") == "CONSOLA.txt"
	assert nombre_seguro("informe final. . .") == "informe final"
	assert nombre_seguro("línea\x00nula\x1f.txt") == "líneanula.txt"
	assert nombre_seguro("") == "descarga"
	assert nombre_seguro("..") == "descarga"
	assert nombre_seguro("   ", respaldo="sin_nombre") == "sin_nombre"
	largo = nombre_seguro("a" * 400 + ".mp4")
	assert largo.endswith(".mp4") and len(largo) == 150
	emojis = nombre_seguro("😀" * 100 + ".txt", max_longitud=50)
	assert len(emojis.encode("utf-16-le")) // 2 <= 50 and emojis.endswith(".txt")
