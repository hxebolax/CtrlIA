"""Pruebas de la capa de red (PLANTILLA del agente «red-http»).

Todas contra un servidor local (servidor_http_prueba.py): nunca salen a Internet.
"""

from __future__ import annotations

import errno
import logging
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from app.core import red
from app.core.red import ConfiguracionRed, ErrorRed, clasificar_error, crear_sesion, redactar_url
from servidor_http_prueba import servidor  # noqa: F401 - fixture


def _sesion(**opciones):
	"""Sesión de prueba: sin proxy del sistema (el equipo podría tener uno) y esperas cortas."""
	base = {"nombre_app": "Prueba", "version_app": "1.2.3", "proxy": "ninguno", "factor_espera": 0.01}
	base.update(opciones)
	return crear_sesion(ConfiguracionRed(**base))


# ── Cabeceras, proxy y TLS ─────────────────────────────────────────────────

def test_user_agent_propio(servidor):
	respuesta = _sesion().get(servidor.url("/ua"))
	assert respuesta.text.startswith("Prueba/1.2.3 (Windows")
	assert "python-requests/" in respuesta.text


def test_proxy_desde_configuracion():
	con_proxy = crear_sesion(ConfiguracionRed(proxy="http://usuario:clave@proxy.local:8080"))
	assert con_proxy.proxies["https"] == "http://usuario:clave@proxy.local:8080"
	assert crear_sesion(ConfiguracionRed(proxy="ninguno")).trust_env is False
	assert crear_sesion(ConfiguracionRed(proxy="sistema")).trust_env is True


def test_verificacion_tls_con_certifi():
	sesion = crear_sesion(ConfiguracionRed())
	assert isinstance(sesion.verify, str) and os.path.isfile(sesion.verify)
	assert sesion.verify.endswith("cacert.pem")


# ── Tiempos de espera ──────────────────────────────────────────────────────

def test_tiempo_de_espera_por_defecto_sin_indicarlo(servidor):
	sesion = _sesion(tiempo_lectura=0.3, reintentos=0)
	inicio = time.monotonic()
	# Con un Retry en el adaptador, requests lo entrega como ConnectionError (no ReadTimeout):
	# clasificar_error mira dentro de la cadena de causas para dar el mensaje correcto.
	with pytest.raises(requests.RequestException) as info:
		sesion.get(servidor.url("/lento"))  # sin timeout=: lo pone el adaptador
	assert time.monotonic() - inicio < 1.45  # no esperó a la respuesta, que tarda 1,5 s
	clasificado = clasificar_error(info.value)
	assert clasificado.categoria == "red" and clasificado.reintentar
	assert "dejado de responder" in clasificado.mensaje


def test_conexion_rechazada():
	with socket.socket() as libre:
		libre.bind(("127.0.0.1", 0))
		puerto = libre.getsockname()[1]
	with pytest.raises(requests.exceptions.ConnectionError) as info:
		_sesion(reintentos=0, tiempo_conexion=5).get(f"http://127.0.0.1:{puerto}/")
	clasificado = clasificar_error(info.value)
	assert clasificado.categoria == "red" and clasificado.reintentar
	assert "No se ha podido conectar" in clasificado.mensaje


# ── Reintentos ─────────────────────────────────────────────────────────────

def test_reintenta_ante_503(servidor):
	respuesta = _sesion(reintentos=3).get(servidor.url("/503?id=reintento"))
	assert respuesta.status_code == 200 and respuesta.text == "ok"
	assert servidor.veces("/503?id=reintento") == 3


def test_no_reintenta_post(servidor):
	# Un POST no se repite solo: publicar dos veces sería peor que fallar una.
	respuesta = _sesion(reintentos=3).post(servidor.url("/503-siempre?id=post"))
	assert respuesta.status_code == 503
	assert servidor.veces("/503-siempre?id=post") == 1


def test_503_agotado_devuelve_respuesta_clasificable(servidor):
	respuesta = _sesion(reintentos=1).get(servidor.url("/503-siempre?id=agotado"))
	assert respuesta.status_code == 503
	assert servidor.veces("/503-siempre?id=agotado") == 2
	clasificado = clasificar_error(respuesta=respuesta)
	assert clasificado.categoria == "servidor" and clasificado.reintentar
	with pytest.raises(ErrorRed) as info:
		red.comprobar_respuesta(respuesta)
	assert "503" in info.value.mensaje_usuario and info.value.transitorio


def test_429_respeta_retry_after(servidor):
	inicio = time.monotonic()
	respuesta = _sesion(reintentos=2).get(servidor.url("/429?id=espera"))
	transcurrido = time.monotonic() - inicio
	assert respuesta.status_code == 200
	assert servidor.veces("/429?id=espera") == 2
	assert transcurrido >= 0.9, transcurrido


def test_retry_after_largo_no_duerme_el_hilo(servidor):
	sesion = _sesion(reintentos=1, espera_maxima_servidor=1)
	inicio = time.monotonic()
	primera = sesion.get(servidor.url("/429-largo?id=hora"))
	assert primera.status_code == 429
	# urllib3 esperó como mucho el tope (1 s por intento), nunca una hora.
	assert time.monotonic() - inicio < 3
	antes = servidor.veces("/429-largo?id=hora")
	with pytest.raises(ErrorRed) as info:
		sesion.get(servidor.url("/429-largo?id=hora"))
	# El limitador falla enseguida sin molestar otra vez al servidor.
	assert servidor.veces("/429-largo?id=hora") == antes
	assert "minutos" in info.value.mensaje_usuario and info.value.reintentar


def test_limitador_intervalo_minimo():
	limitador = red.LimitadorServidores(intervalo_minimo=0.2)
	inicio = time.monotonic()
	for _ in range(3):
		limitador.antes_de_pedir("ejemplo:80")
	assert time.monotonic() - inicio >= 0.35


def test_segundos_retry_after():
	assert red.segundos_retry_after("120") == 120
	ahora = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
	fecha = format_datetime(ahora + timedelta(seconds=90), usegmt=True)
	assert red.segundos_retry_after(fecha, ahora=ahora) == pytest.approx(90)
	assert red.segundos_retry_after("mañana") is None
	assert red.segundos_retry_after(None) is None


# ── Clasificación de errores ───────────────────────────────────────────────

def _respuesta(codigo, url="https://api.ejemplo.org/datos?token=abc"):
	respuesta = requests.Response()
	respuesta.status_code = codigo
	respuesta.url = url
	return respuesta


def test_clasificacion_de_errores():
	casos = {
		"seguridad": requests.exceptions.SSLError("certificate verify failed"),
		"red": requests.exceptions.ConnectTimeout("connect timeout"),
		"local": PermissionError(13, "Acceso denegado"),
		"cancelado": type("TareaCancelada", (Exception,), {})(),
		"desconocido": ValueError("otra cosa"),
	}
	for categoria, error in casos.items():
		assert clasificar_error(error).categoria == categoria, categoria
	sin_espacio = clasificar_error(OSError(errno.ENOSPC, "No space left on device"))
	assert sin_espacio.categoria == "local" and "espacio" in sin_espacio.mensaje

	no_encontrado = clasificar_error(requests.HTTPError(response=_respuesta(404)))
	assert no_encontrado.categoria == "cliente" and not no_encontrado.reintentar
	assert "404" in no_encontrado.mensaje
	assert "abc" not in no_encontrado.detalle  # la URL del detalle va redactada

	try:
		try:
			raise socket.gaierror(11001, "getaddrinfo failed")
		except socket.gaierror:
			raise requests.exceptions.ConnectionError("Max retries exceeded")  # noqa: B904 - como requests
	except requests.exceptions.ConnectionError as error:
		dns = clasificar_error(error)
	assert dns.categoria == "red" and "No se ha encontrado el servidor" in dns.mensaje

	propio = clasificar_error(ErrorRed("Mensaje propio", categoria="cliente"))
	assert propio.mensaje == "Mensaje propio" and propio.categoria == "cliente"


# ── Privacidad en el registro ──────────────────────────────────────────────

def test_redactar_url():
	url = "https://ana:secreta@api.ejemplo.org/v1/cb?token=abc123&pagina=2&api_key=K#access_token=zzz&estado=1"
	limpia = redactar_url(url)
	for secreto in ("secreta", "abc123", "=K", "zzz"):
		assert secreto not in limpia
	assert "ana:***@" in limpia and "pagina=2" in limpia and "estado=1" in limpia
	assert redactar_url("https://ejemplo.org/sin/secretos?q=gato") == "https://ejemplo.org/sin/secretos?q=gato"


def test_registro_con_url_redactada(servidor, caplog):
	caplog.set_level(logging.DEBUG, logger="app.core.red")
	_sesion().get(servidor.url("/ua?token=supersecreto&x=1"))
	assert "token=***" in caplog.text
	assert "supersecreto" not in caplog.text


def test_urllib3_no_registra_la_url_con_tokens(servidor, caplog):
	"""Con el registro en DEBUG, urllib3 escribiría la URL completa sin redactar."""
	assert logging.getLogger("urllib3.connectionpool").getEffectiveLevel() >= logging.INFO
	caplog.set_level(logging.DEBUG)  # registro raíz en DEBUG, como en una sesión de depuración
	_sesion().get(servidor.url("/ua?token=supersecreto&x=1"))
	assert "supersecreto" not in caplog.text
	assert not [r for r in caplog.records if r.name.startswith("urllib3") and r.levelno < logging.INFO]


# ── Sesiones por hilo y red de seguridad global ────────────────────────────

def test_sesion_de_hilo():
	red.configurar(ConfiguracionRed(nombre_app="HiloA", proxy="ninguno"))
	principal = red.sesion_de_hilo()
	assert red.sesion_de_hilo() is principal
	otras = []
	hilo = threading.Thread(target=lambda: otras.append(red.sesion_de_hilo()))
	hilo.start()
	hilo.join()
	assert otras[0] is not principal
	red.configurar(ConfiguracionRed(nombre_app="HiloB", proxy="ninguno"))
	nueva = red.sesion_de_hilo()
	assert nueva is not principal and nueva.headers["User-Agent"].startswith("HiloB/")


def test_tiempo_global_de_sockets():
	anterior = socket.getdefaulttimeout()
	try:
		red.instalar_tiempo_global(42)
		assert socket.getdefaulttimeout() == 42
	finally:
		socket.setdefaulttimeout(anterior)
