"""Pruebas del sistema de registro (PLANTILLA del agente «logging-profesional»).

Trabajan sobre una carpeta temporal: nunca tocan el data/logs real.
"""

from __future__ import annotations

import json
import logging
import sys
import threading

import pytest

from app.core import registro


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
	"""Configura el registro en una carpeta temporal y lo detiene al terminar."""
	monkeypatch.delenv(registro.VARIABLE_DEPURACION, raising=False)
	excepthook, threadhook, unraisable = sys.excepthook, threading.excepthook, sys.unraisablehook
	logs = tmp_path / "logs"
	registro.configurar_registro("Prueba", "9.9.9", nivel="DEBUG", carpeta=logs)
	yield logs
	registro.detener_registro(cerrar_fallo_nativo=True)
	sys.excepthook, threading.excepthook, sys.unraisablehook = excepthook, threadhook, unraisable
	logging.captureWarnings(False)


def _leer(carpeta, patron):
	registro.detener_registro()
	return "\n".join(p.read_text(encoding="utf-8") for p in sorted(carpeta.glob(patron)))


def _funcion_que_falla():
	return {}["clave_inexistente"]


def test_crea_sesion_con_cabecera(carpeta):
	texto = _leer(carpeta, "sesion_*.log")
	assert "Prueba 9.9.9" in texto
	assert "Python" in texto


def test_excepcion_con_ubicacion_y_origen(carpeta):
	log = logging.getLogger("app.prueba")
	try:
		_funcion_que_falla()
	except KeyError:
		log.exception("Fallo al leer")
	texto = _leer(carpeta, "sesion_*.log")
	assert "Fallo al leer" in texto
	assert "test_registro.py" in texto
	assert "Origen:" in texto and "_funcion_que_falla" in texto
	assert "KeyError" in texto


def test_incidencias_solo_avisos_y_errores(carpeta):
	log = logging.getLogger("app.prueba")
	log.info("mensaje informativo")
	log.warning("aviso importante")
	texto = _leer(carpeta, "incidencias.log")
	assert "aviso importante" in texto
	assert "mensaje informativo" not in texto


def test_jsonl_estructurado(carpeta):
	log = logging.getLogger("app.prueba")
	try:
		_funcion_que_falla()
	except KeyError:
		log.error("Con traza", exc_info=True, extra={"id_elemento": 42})
	lineas = _leer(carpeta, "sesion_*.jsonl").splitlines()
	datos = [json.loads(linea) for linea in lineas]
	error = next(d for d in datos if d["mensaje"] == "Con traza")
	assert error["nivel"] == "ERROR"
	assert error["excepcion"]["tipo"] == "KeyError"
	assert "_funcion_que_falla" in error["excepcion"]["origen"]
	assert error["extra"]["id_elemento"] == 42


@pytest.mark.parametrize(
	("texto", "secreto"),
	[
		("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
		("https://usuario:hunter22@servidor.com/ruta", "hunter22"),
		("GET https://api.x.com/v1?access_token=ZZZ999&x=1", "ZZZ999"),
		('{"password": "hunter22", "otro": 1}', "hunter22"),
		("password=hunter22 continuar", "hunter22"),
		("Cookie: sesion=AAA; otra=BBB", "BBB"),
	],
)
def test_redaccion(texto, secreto):
	assert secreto not in registro.redactar(texto)


def test_secreto_registrado_se_oculta_tambien_en_trazas(carpeta):
	registro.registrar_secreto("MI-TOKEN-SUPERSECRETO")
	log = logging.getLogger("app.prueba")
	try:
		raise ValueError("token rechazado: MI-TOKEN-SUPERSECRETO")
	except ValueError:
		log.exception("Error de autenticación con MI-TOKEN-SUPERSECRETO")
	texto = _leer(carpeta, "sesion_*.log")
	assert "MI-TOKEN-SUPERSECRETO" not in texto
	assert "***" in texto


def test_hilo_no_controlado_queda_registrado(carpeta):
	def trabajo():
		raise RuntimeError("fallo en segundo plano")

	hilo = threading.Thread(target=trabajo, name="hilo-de-prueba")
	hilo.start()
	hilo.join()
	texto = _leer(carpeta, "sesion_*.log")
	assert "hilo-de-prueba" in texto
	assert "fallo en segundo plano" in texto


def test_excepthook_registra(carpeta):
	try:
		raise LookupError("no controlada")
	except LookupError:
		sys.excepthook(*sys.exc_info())
	texto = _leer(carpeta, "incidencias.log")
	assert "CRITICAL" in texto and "no controlada" in texto


def test_repeticiones_resumidas(carpeta):
	log = logging.getLogger("app.prueba")
	for _ in range(100):
		log.warning("mensaje en bucle")
	texto = _leer(carpeta, "sesion_*.log")
	assert texto.count("mensaje en bucle") <= 6


def test_operacion_anidada_no_duplica_traza(carpeta):
	with pytest.raises(ZeroDivisionError):
		with registro.operacion("exterior"):
			with registro.operacion("interior"):
				_ = 1 / 0
	texto = _leer(carpeta, "sesion_*.log")
	assert texto.count("Traceback") == 1
	assert "exterior#" in texto and ">interior#" in texto


def test_decorador_devuelve_valor_por_defecto(carpeta):
	@registro.registrar_excepciones("No se pudo calcular", valor_por_defecto=-1)
	def calcular():
		return 1 / 0

	assert calcular() == -1
	texto = _leer(carpeta, "sesion_*.log")
	assert "No se pudo calcular" in texto and "ZeroDivisionError" in texto


def test_conserva_solo_las_ultimas_sesiones(tmp_path):
	logs = tmp_path / "logs"
	logs.mkdir()
	for i in range(15):
		(logs / f"sesion_20200101_0000{i:02d}.log").write_text("x", encoding="utf-8")
	registro.configurar_registro("Prueba", carpeta=logs, sesiones_conservadas=5, capturar_no_controladas=False)
	registro.detener_registro()
	claves = {p.name.split(".", 1)[0] for p in logs.glob("sesion_*")}
	# 4 antiguas + la actual (que aún puede no tener archivo si no escribió nada).
	assert len(claves - {f"sesion_{registro._estado['sesion']}"}) == 4


def test_detecta_cierre_anomalo(tmp_path):
	logs = tmp_path / "logs"
	logs.mkdir()
	(logs / "sesion.en_curso").write_text("20260101_000000", encoding="utf-8")
	registro.configurar_registro("Prueba", carpeta=logs, capturar_no_controladas=False)
	registro.detener_registro()
	texto = "\n".join(p.read_text(encoding="utf-8") for p in logs.glob("sesion_*.log"))
	assert "no terminó limpiamente" in texto
	# Tras un cierre ordenado la marca desaparece.
	assert not (logs / "sesion.en_curso").exists()


def test_fallo_no_controlado_se_escribe_al_instante(carpeta):
	try:
		raise OSError("disco desconectado")
	except OSError:
		sys.excepthook(*sys.exc_info())
	archivos = list((carpeta / "no_controladas").glob("fallo_*.log"))
	assert len(archivos) == 1
	texto = archivos[0].read_text(encoding="utf-8")
	assert "disco desconectado" in texto and "Origen:" in texto


def test_codigo_propio_en_version_compilada(carpeta):
	# Rutas como las de una traza congelada: de la máquina de compilación o relativas.
	assert registro._es_codigo_propio(r"D:\compilacion\MiApp\app\ui\ventana.py")
	assert registro._es_codigo_propio("app/core/datos.py")
	assert not registro._es_codigo_propio(r"C:\Python313\Lib\site-packages\wx\core.py")
	assert registro._ruta_relativa(r"D:\compilacion\MiApp\app\ui\ventana.py") == "app/ui/ventana.py"


def test_ejecutar_proceso_registra_codigo_y_orden_redactada(carpeta):
	resultado = registro.ejecutar_proceso([sys.executable, "-c", "import sys; sys.exit(3)", "password=abc123"])
	assert resultado.returncode == 3
	texto = _leer(carpeta, "sesion_*.log")
	assert "Código 3" in texto
	assert "abc123" not in texto


# La wx.App de la sesión. Esta referencia de módulo la mantiene viva hasta el final:
# sin ella, Python destruiría la aplicación recién creada al salir de la fixture.
_APLICACION_WX = None


def test_puente_wx(carpeta):
	wx = pytest.importorskip("wx")
	# wx.Log necesita una aplicación: la de la sesión si ya hay una (nunca una segunda).
	global _APLICACION_WX
	_APLICACION_WX = wx.GetApp() or wx.App(False)
	puente = registro.instalar_puente_wx()  # noqa: F841 - debe seguir vivo
	wx.LogError("fallo interno de wxWidgets")
	wx.Log.FlushActive()
	texto = _leer(carpeta, "incidencias.log")
	assert "fallo interno de wxWidgets" in texto
	wx.Log.SetActiveTarget(None)


def test_paquete_de_diagnostico_anonimiza(carpeta):
	import zipfile

	from app.core import diagnostico

	logging.getLogger("app.prueba").error(r"No se encontró C:\Users\ana.garcia\Documentos\lista.txt")
	registro.detener_registro()
	assert "<usuario>" in diagnostico.anonimizar(r"C:\Users\ana.garcia\x") and "ana.garcia" not in diagnostico.anonimizar(r"C:\Users\ana.garcia\x")
	ruta = diagnostico.generar_paquete("Prueba", "9.9.9")
	with zipfile.ZipFile(ruta) as zip_:
		nombres = zip_.namelist()
		contenido = "".join(zip_.read(n).decode("utf-8") for n in nombres)
	assert "sistema.txt" in nombres
	assert any(n.startswith("logs/sesion_") for n in nombres)
	assert "ana.garcia" not in contenido
