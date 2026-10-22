"""Pruebas del gestor de tareas (PLANTILLA del agente «hilos-y-tareas»).

La mayoría usan un ejecutor de interfaz falso (directo o manual) para no
depender de wx; la última usa wx de verdad, sin ventanas.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from app.core import tareas
from app.core.tareas import (
	EjecutorManual,
	GestorTareas,
	HitosProgreso,
	Progreso,
	TareaCancelada,
	TareaDuplicada,
	TiempoAgotado,
	TokenCancelacion,
	ejecutor_directo,
	es_error_transitorio,
)


@pytest.fixture(params=[True, False], ids=["daemon", "threadpool"])
def gestor(request):
	"""Gestor con entrega directa, probado con los dos tipos de grupo de hilos."""
	g = GestorTareas(max_hilos=2, ejecutor_interfaz=ejecutor_directo, daemon=request.param)
	yield g
	g.apagar(espera=2.0)


@pytest.fixture
def manual():
	"""Gestor cuya «interfaz» es una cola que la prueba procesa en su propio hilo."""
	cola = EjecutorManual()
	g = GestorTareas(max_hilos=2, ejecutor_interfaz=cola, intervalo_progreso=0.2)
	yield g, cola
	g.apagar(espera=2.0)


class Recogedor:
	"""Anota las respuestas recibidas, con el hilo en que llegaron."""

	def __init__(self):
		self.eventos: list[tuple[str, object, str]] = []
		self.fin = threading.Event()

	def terminar(self, resultado):
		self.eventos.append(("terminar", resultado, threading.current_thread().name))
		self.fin.set()

	def fallar(self, error):
		self.eventos.append(("fallar", error, threading.current_thread().name))
		self.fin.set()

	def progresar(self, progreso):
		self.eventos.append(("progresar", progreso, threading.current_thread().name))


def _esperar_cola(cola: EjecutorManual, condicion, limite: float = 5.0) -> None:
	"""Procesa la cola (como haría el bucle de wx) hasta que se cumpla la condición."""
	final = time.monotonic() + limite
	while time.monotonic() < final:
		cola.procesar_pendientes()
		if condicion():
			return
		time.sleep(0.01)
	raise AssertionError("La condición no se cumplió a tiempo.")


# ── Ejecución y orden ──────────────────────────────────────────────────────

def test_ejecuta_y_entrega_resultado(gestor):
	r = Recogedor()
	tarea = gestor.enviar(lambda t, a, b: a + b, 2, 3, al_terminar=r.terminar, al_fallar=r.fallar, nombre="sumar")
	assert tarea.esperar_fin(5)
	assert r.eventos[0][:2] == ("terminar", 5)
	assert tarea.estado == "completada" and tarea.resultado == 5
	assert tarea.duracion is not None


def test_respuestas_en_hilo_de_interfaz_y_en_orden(manual):
	g, cola = manual
	r = Recogedor()

	def trabajo(t):
		for i in range(1, 4):
			t.progresar(i, 3, f"paso {i}")
			time.sleep(0.25)  # más que el intervalo: cada paso se entrega
		return "hecho"

	g.enviar(trabajo, al_terminar=r.terminar, al_progresar=r.progresar)
	_esperar_cola(cola, r.fin.is_set)
	tipos = [e[0] for e in r.eventos]
	assert tipos[-1] == "terminar" and tipos.count("terminar") == 1
	assert all(tipo == "progresar" for tipo in tipos[:-1]) and len(tipos) >= 3
	# Todas las respuestas llegaron en el hilo que procesa la cola (el «de la interfaz»).
	assert {e[2] for e in r.eventos} == {threading.current_thread().name}
	ultimo_progreso = [e[1] for e in r.eventos if e[0] == "progresar"][-1]
	assert ultimo_progreso.porcentaje == 100


def test_progreso_limitado_y_agrupado(manual):
	g, cola = manual
	r = Recogedor()

	def trabajo(t):
		inicio = time.monotonic()
		for i in range(1, 2001):
			t.progresar(i, 2000)
		return time.monotonic() - inicio

	tarea = g.enviar(trabajo, al_terminar=r.terminar, al_progresar=r.progresar)
	tarea.esperar_fin(5)
	_esperar_cola(cola, r.fin.is_set)
	progresos = [e[1] for e in r.eventos if e[0] == "progresar"]
	# Como mucho una entrega por intervalo (0,2 s) más la final, aunque hubo 2000 llamadas.
	assert 1 <= len(progresos) <= tarea.resultado / 0.2 + 2, (len(progresos), tarea.resultado)
	assert progresos[-1].hecho == 2000  # llega el ÚLTIMO valor, no uno viejo


# ── Cancelación ────────────────────────────────────────────────────────────

def test_cancelacion_cooperativa(gestor):
	r = Recogedor()
	empezada = threading.Event()

	def trabajo(t):
		empezada.set()
		while not t.esperar(0.02):
			pass
		t.comprobar()

	tarea = gestor.enviar(trabajo, al_terminar=r.terminar, al_fallar=r.fallar)
	assert empezada.wait(5)
	assert tarea.cancelar("prueba")
	assert tarea.esperar_fin(5)
	assert r.eventos[0][0] == "fallar" and isinstance(r.eventos[0][1], TareaCancelada)
	assert tarea.estado == "cancelada"
	assert gestor.estadisticas()["canceladas"] == 1


def test_estado_cancelando_mientras_se_detiene(gestor):
	liberar = threading.Event()
	empezada = threading.Event()

	def trabajo(t):
		empezada.set()
		liberar.wait(5)  # tarda en mirar el token
		t.comprobar()

	tarea = gestor.enviar(trabajo)
	assert empezada.wait(5)
	tarea.cancelar()
	assert tarea.estado == "cancelando"
	liberar.set()
	assert tarea.esperar_fin(5)
	assert tarea.estado == "cancelada"


def test_cancelar_pendiente_no_llega_a_ejecutarse():
	g = GestorTareas(max_hilos=1, ejecutor_interfaz=ejecutor_directo)
	bloqueo = threading.Event()
	ejecutada = threading.Event()
	r = Recogedor()
	try:
		g.enviar(lambda t: bloqueo.wait(5))
		pendiente = g.enviar(lambda t: ejecutada.set(), al_fallar=r.fallar)
		assert pendiente.estado == "pendiente"
		assert g.cancelar(pendiente)
		assert isinstance(r.eventos[0][1], TareaCancelada)  # respuesta inmediata
		bloqueo.set()
		time.sleep(0.2)
		assert not ejecutada.is_set()
	finally:
		bloqueo.set()
		g.apagar(2)


def test_token_al_cancelar_y_compatibilidad_event():
	token = TokenCancelacion()
	llamadas = []
	token.al_cancelar(lambda: llamadas.append("cerrar conexión"))
	assert not token.is_set()
	assert token.cancelar("motivo")
	assert not token.cancelar("otra vez")
	assert token.is_set() and token.motivo == "motivo" and llamadas == ["cerrar conexión"]
	token.al_cancelar(lambda: llamadas.append("tarde"))
	assert llamadas[-1] == "tarde"
	with pytest.raises(TareaCancelada):
		token.comprobar()


# ── Claves únicas ──────────────────────────────────────────────────────────

def test_clave_unica_unifica(gestor):
	liberar = threading.Event()
	ejecuciones = []
	resultados = []

	def trabajo(t):
		ejecuciones.append(1)
		liberar.wait(5)
		return "datos"

	primera = gestor.enviar(trabajo, clave_unica="feed:7", al_terminar=resultados.append)
	segunda = gestor.enviar(trabajo, clave_unica="feed:7", al_terminar=resultados.append)
	assert segunda is primera and gestor.activa("feed:7")
	liberar.set()
	assert primera.esperar_fin(5)
	assert resultados == ["datos", "datos"] and len(ejecuciones) == 1
	assert not gestor.activa("feed:7")
	assert gestor.estadisticas()["unificadas"] == 1


def test_clave_unica_rechaza_y_avisa(gestor):
	liberar = threading.Event()
	errores = []
	primera = gestor.enviar(lambda t: liberar.wait(5), clave_unica="cuenta:1")
	segunda = gestor.enviar(lambda t: None, clave_unica="cuenta:1", duplicadas="rechazar", al_fallar=errores.append)
	assert segunda is None
	assert isinstance(errores[0], TareaDuplicada)  # la interfaz puede limpiar su estado
	liberar.set()
	assert primera.esperar_fin(5)
	tercera = gestor.enviar(lambda t: "otra vez", clave_unica="cuenta:1", duplicadas="rechazar")
	assert tercera is not None and tercera.esperar_fin(5)
	assert gestor.estadisticas()["rechazadas"] == 1


# ── Errores ────────────────────────────────────────────────────────────────

def _falla_de_verdad(t):
	raise ValueError("dato corrupto en la fila 7")


def test_error_registrado_con_traza_y_respuesta(gestor, caplog):
	errores = []
	caplog.set_level(logging.DEBUG, logger="app.core.tareas")
	tarea = gestor.enviar(_falla_de_verdad, al_fallar=errores.append, nombre="importar")
	assert tarea.esperar_fin(5)
	assert isinstance(errores[0], ValueError)
	registro = next(r for r in caplog.records if r.levelno == logging.ERROR)
	assert "importar" in registro.getMessage() and "ValueError" in registro.getMessage()
	assert registro.exc_info and registro.exc_info[0] is ValueError
	assert "_falla_de_verdad" in caplog.text  # la traza señala la función culpable
	assert tarea.estado == "fallida"


def test_error_transitorio_una_linea_sin_traza_repetida(gestor, caplog):
	caplog.set_level(logging.INFO, logger="app.core.tareas")

	def corta(t):
		raise ConnectionResetError("conexión restablecida por el servidor")

	for _ in range(3):
		gestor.enviar(corta, nombre="sondeo").esperar_fin(5)
	avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
	assert len(avisos) == 1
	assert avisos[0].exc_info is None and "sondeo" in avisos[0].getMessage()
	assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_fallo_en_la_respuesta_se_registra(gestor, caplog):
	def respuesta_rota(resultado):
		raise RuntimeError("control destruido")

	gestor.enviar(lambda t: 1, al_terminar=respuesta_rota).esperar_fin(5)
	assert any("control destruido" in r.getMessage() or (r.exc_info and "control destruido" in str(r.exc_info[1]))
		for r in caplog.records if r.levelno == logging.ERROR)


def test_clasificacion_de_errores_transitorios():
	class Respuesta:
		def __init__(self, codigo):
			self.status_code = codigo

	class ErrorHttp(Exception):
		def __init__(self, codigo):
			super().__init__(f"HTTP {codigo}")
			self.response = Respuesta(codigo)

	class SSLError(ConnectionError):
		pass

	class ReadTimeout(OSError):
		pass

	class Marcado(Exception):
		transitorio = False

	assert es_error_transitorio(TimeoutError())
	assert es_error_transitorio(ConnectionRefusedError())
	assert es_error_transitorio(ReadTimeout("lectura"))
	assert es_error_transitorio(ErrorHttp(503))
	assert not es_error_transitorio(ErrorHttp(404))
	assert not es_error_transitorio(SSLError("certificado"))
	assert not es_error_transitorio(ValueError("el valor 500 no es válido"))
	assert not es_error_transitorio(Marcado())
	try:
		try:
			raise TimeoutError("socket")
		except TimeoutError as causa:
			raise RuntimeError("no se pudo leer el feed") from causa
	except RuntimeError as envuelto:
		assert es_error_transitorio(envuelto)


# ── Tiempo máximo y apagado ────────────────────────────────────────────────

def test_tiempo_maximo(gestor):
	r = Recogedor()
	salio = threading.Event()

	def lenta(t):
		t.esperar(10)  # se despierta al vencer: el gestor cancela su token
		salio.set()
		return "tarde"

	inicio = time.monotonic()
	tarea = gestor.enviar(lenta, al_terminar=r.terminar, al_fallar=r.fallar, tiempo_maximo=0.3, nombre="lenta")
	assert r.fin.wait(5)
	assert time.monotonic() - inicio < 2
	assert r.eventos[0][0] == "fallar" and isinstance(r.eventos[0][1], TiempoAgotado)
	assert tarea.estado == "vencida"
	assert salio.wait(2)
	time.sleep(0.05)
	assert len(r.eventos) == 1  # el resultado tardío se descarta: una sola respuesta
	assert gestor.estadisticas()["vencidas"] == 1


def test_apagado_ordenado(gestor):
	empezada = threading.Event()
	ejecutadas = []

	def larga(t):
		empezada.set()
		while not t.esperar(0.02):
			pass

	gestor.enviar(larga)
	gestor.enviar(larga)
	for _ in range(3):
		gestor.enviar(lambda t: ejecutadas.append(1))
	assert empezada.wait(5)
	inicio = time.monotonic()
	assert gestor.apagar(espera=3.0) is True
	assert time.monotonic() - inicio < 2
	assert ejecutadas == []  # las pendientes se descartaron
	assert gestor.enviar(lambda t: None) is None
	assert gestor.estadisticas()["activas"] == 0


def test_apagado_con_tarea_atascada(caplog):
	g = GestorTareas(max_hilos=1, ejecutor_interfaz=ejecutor_directo)
	liberar = threading.Event()
	empezada = threading.Event()

	def atascada(t):
		empezada.set()
		liberar.wait(5)  # ignora la cancelación

	g.enviar(atascada, nombre="lectura_sin_timeout")
	assert empezada.wait(5)
	inicio = time.monotonic()
	assert g.apagar(espera=0.3) is False
	assert time.monotonic() - inicio < 1.5
	assert "lectura_sin_timeout" in caplog.text
	liberar.set()


def test_estadisticas(gestor):
	gestor.enviar(lambda t: 1).esperar_fin(5)
	gestor.enviar(_falla_de_verdad).esperar_fin(5)
	datos = gestor.estadisticas()
	assert datos["enviadas"] == 2 and datos["completadas"] == 1 and datos["fallidas"] == 1
	assert datos["activas"] == 0 and datos["duracion_maxima"] >= 0


def test_nombre_de_la_tarea_en_el_hilo(gestor):
	nombres = []
	gestor.enviar(lambda t: nombres.append(threading.current_thread().name), nombre="descargar_avatar").esperar_fin(5)
	assert "descargar_avatar" in nombres[0]


# ── Progreso por hitos ─────────────────────────────────────────────────────

def test_hitos_progreso():
	hitos = HitosProgreso(paso=25)
	anunciados = [h for p in range(0, 101) if (h := hitos.nuevo_hito(p)) is not None]
	assert anunciados == [25, 50, 75, 100]
	assert hitos.nuevo_hito(None) is None
	assert Progreso(3, 4).porcentaje == 75 and Progreso(3).porcentaje is None


# ── ejecutar_en_interfaz y @en_interfaz ────────────────────────────────────

def test_ejecutar_en_interfaz_desde_otro_hilo():
	cola = EjecutorManual()
	anterior = tareas.establecer_ejecutor_interfaz(cola)
	try:
		llamadas = []

		@tareas.en_interfaz
		def actualizar(texto):
			llamadas.append((texto, threading.current_thread().name))

		hilo = threading.Thread(target=actualizar, args=("desde hilo",), name="trabajador")
		hilo.start()
		hilo.join()
		assert llamadas == []  # encolada, no ejecutada en el hilo de trabajo
		actualizar("desde principal")  # en el hilo principal se ejecuta ya
		assert llamadas[0][0] == "desde principal"
		cola.procesar_pendientes()
		assert llamadas[1] == ("desde hilo", threading.current_thread().name)
	finally:
		tareas.establecer_ejecutor_interfaz(anterior)


# ── Con wx real ────────────────────────────────────────────────────────────

# La wx.App es la del conftest (única para toda la sesión).


def test_con_wx_real_sin_ventanas(app_wx):
	g = GestorTareas(max_hilos=2)  # ejecutor por defecto: wx.CallAfter
	r = Recogedor()

	def trabajo(t):
		t.progresar(1, 1)
		return threading.current_thread().name

	g.enviar(trabajo, al_terminar=r.terminar, al_progresar=r.progresar, nombre="con_wx")
	# esperar_hasta activa un bucle de eventos: ProcessPendingEvents sin bucle
	# activo llegó a bloquearse dentro de wx en la batería completa.
	from tests.ayudas_wx import esperar_hasta

	esperar_hasta(r.fin.is_set, 5)
	assert r.fin.is_set()
	assert r.eventos[-1][0] == "terminar" and "con_wx" in r.eventos[-1][1]
	assert {e[2] for e in r.eventos} == {threading.main_thread().name}
	assert g.apagar(2)


def test_con_wx_mainloop_y_calllater(app_wx):
	"""Los resultados llegan en el hilo principal también con wx.CallLater.

	Sin ``MainLoop`` real: al terminar, wx da la aplicación por cerrada y deja
	sin ella al resto de la sesión; además, en este equipo hay un lector de
	pantalla y no debe correr un bucle principal de pruebas. Se usa un bucle
	de eventos activo a mano (como hace ``esperar_hasta``).
	"""
	import wx

	from tests.ayudas_wx import esperar_hasta

	g = GestorTareas(max_hilos=1)
	recibido = []

	def al_terminar(resultado):
		recibido.append((resultado, wx.IsMainThread()))

	def arrancar():
		g.enviar(lambda t: "desde el hilo", al_terminar=al_terminar)

	wx.CallLater(10, arrancar)
	esperar_hasta(lambda: bool(recibido), 5)
	g.apagar(2)
	assert recibido == [("desde el hilo", True)]
