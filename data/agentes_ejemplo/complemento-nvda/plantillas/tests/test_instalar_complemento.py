"""Pruebas del ofrecimiento de instalación (PLANTILLA del agente «complemento-nvda»).

``os.startfile`` se sustituye SIEMPRE: estas pruebas nunca abren el
.nvda-addon ni tocan la configuración de NVDA. La enumeración de procesos sí
es real (solo lectura).
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nvda_falso  # noqa: E402

RAIZ = nvda_falso.RAIZ
instalar = nvda_falso.cargar_modulo("instalar_complemento_pruebas", RAIZ / "app" / "ui" / "instalar_complemento.py")


@pytest.fixture(autouse=True)
def sin_abrir_nada(monkeypatch):
	"""Cualquier llamada no prevista a os.startfile hace fallar la prueba."""

	def prohibido(*_argumentos, **_opciones):
		raise AssertionError("Una prueba ha intentado abrir un archivo con os.startfile")

	monkeypatch.setattr(os, "startfile", prohibido, raising=False)


@pytest.fixture
def paquete(tmp_path):
	ruta = tmp_path / "data" / "bin" / "miAplicacionNVDA.nvda-addon"
	ruta.parent.mkdir(parents=True)
	with zipfile.ZipFile(ruta, "w") as zip_:
		zip_.writestr("manifest.ini", 'name = miAplicacionNVDA\nsummary = "x"\nversion = 1.4.0\n')
	return ruta


def test_ruta_en_data_bin(tmp_path):
	assert instalar.ruta_complemento("abc", tmp_path) == tmp_path / "data" / "bin" / "abc.nvda-addon"


def test_hay_proceso():
	assert instalar.hay_proceso(["explorer.exe", "NVDA.EXE"])
	assert not instalar.hay_proceso(["explorer.exe", "nvda_slave.exe"])


@pytest.mark.skipif(sys.platform != "win32", reason="Enumeración de procesos de Windows")
def test_enumeracion_real_de_procesos():
	nombres = [n.lower() for n in instalar.nombres_de_procesos()]
	assert Path(sys.executable).name.lower() in nombres


def test_version_instalada(tmp_path):
	carpeta = tmp_path / "addons" / "miAplicacionNVDA"
	carpeta.mkdir(parents=True)
	(carpeta / "manifest.ini").write_text('name = miAplicacionNVDA\nversion = "1.3.0"\n', encoding="utf-8")
	assert instalar.version_instalada("miAplicacionNVDA", tmp_path) == "1.3.0"
	assert instalar.version_instalada("otro", tmp_path) is None


def test_version_del_paquete(paquete, tmp_path):
	assert instalar.version_del_paquete(paquete) == "1.4.0"
	roto = tmp_path / "roto.nvda-addon"
	roto.write_bytes(b"no es un zip")
	assert instalar.version_del_paquete(roto) is None


def test_explicacion_avisa_de_confirmacion_y_reinicio():
	texto = instalar.explicacion(en_ejecucion=True, instalada="1.3.0", nueva="1.4.0")
	assert "preguntarte si quieres instalarlo" in texto
	assert "reiniciarse" in texto
	assert "1.3.0" in texto and "1.4.0" in texto
	assert "no parece estar en ejecución" not in texto


def test_explicacion_si_nvda_no_esta_en_marcha():
	assert "no parece estar en ejecución" in instalar.explicacion(False, None, "1.0")


def test_abrir_usa_startfile_con_la_ruta(paquete, monkeypatch):
	abiertos = []
	monkeypatch.setattr(os, "startfile", abiertos.append, raising=False)
	correcto, _mensaje = instalar.abrir_instalador(paquete)
	assert correcto
	assert abiertos == [str(paquete)]


def test_abrir_sin_archivo_no_llama_a_nada(tmp_path):
	correcto, mensaje = instalar.abrir_instalador(tmp_path / "no-existe.nvda-addon")
	assert not correcto
	assert "No se encontró" in mensaje


def test_abrir_sin_asociacion(paquete):
	def sin_asociacion(_ruta):
		raise OSError(1155, "No hay ninguna aplicación asociada")

	correcto, mensaje = instalar.abrir_instalador(paquete, abrir=sin_asociacion)
	assert not correcto
	assert str(paquete) in mensaje
	assert "Tienda de complementos" in mensaje


# ════════════════════════════════════════════════════════════════════════════
# Parte wx (diálogo simulado: nunca se muestra)
# ════════════════════════════════════════════════════════════════════════════

class _DialogoFalso:
	respuesta = None
	creados: list = []

	def __init__(self, padre, texto, titulo, estilo):
		self.texto, self.titulo, self.estilo = texto, titulo, estilo
		self.etiquetas = None
		self.destruido = False
		_DialogoFalso.creados.append(self)

	def SetYesNoLabels(self, si, no):  # noqa: N802 - API de wx
		self.etiquetas = (si, no)
		return True

	def SetOKLabel(self, aceptar):  # noqa: N802 - API de wx
		self.etiquetas = (aceptar,)
		return True

	def ShowModal(self):  # noqa: N802 - API de wx
		return self.respuesta

	def Destroy(self):  # noqa: N802 - API de wx
		self.destruido = True


@pytest.fixture
def wx_simulado(monkeypatch):
	wx = pytest.importorskip("wx")
	_DialogoFalso.creados = []
	monkeypatch.setattr(wx, "MessageDialog", _DialogoFalso)
	mensajes = []
	monkeypatch.setattr(instalar, "_mostrar_error", lambda padre, texto, titulo: mensajes.append(texto))
	monkeypatch.setattr(instalar, "nvda_en_ejecucion", lambda: True)
	monkeypatch.setattr(instalar, "version_instalada", lambda *a, **k: None)
	return wx, mensajes


def test_cancelar_no_abre(paquete, wx_simulado):
	wx, _mensajes = wx_simulado
	_DialogoFalso.respuesta = wx.ID_NO
	assert instalar.ofrecer_instalacion(None, paquete) is False
	dialogo = _DialogoFalso.creados[0]
	assert dialogo.etiquetas == ("&Abrir el instalador", "&Cancelar")
	assert dialogo.estilo & wx.NO_DEFAULT
	assert dialogo.destruido


def test_aceptar_abre(paquete, wx_simulado, monkeypatch):
	wx, _mensajes = wx_simulado
	_DialogoFalso.respuesta = wx.ID_YES
	abiertos = []
	monkeypatch.setattr(os, "startfile", abiertos.append, raising=False)
	assert instalar.ofrecer_instalacion(None, paquete) is True
	assert abiertos == [str(paquete)]
	assert "reiniciarse" in _DialogoFalso.creados[0].texto


def test_sin_paquete_lo_genera(tmp_path, paquete, wx_simulado, monkeypatch):
	wx, _mensajes = wx_simulado
	_DialogoFalso.respuesta = wx.ID_NO
	llamadas = []

	def construir():
		llamadas.append(True)
		return paquete

	instalar.ofrecer_instalacion(None, tmp_path / "falta.nvda-addon", construir=construir)
	assert llamadas and _DialogoFalso.creados


def test_si_no_se_puede_generar_avisa(tmp_path, wx_simulado):
	_wx, mensajes = wx_simulado

	def construir():
		raise RuntimeError("manifiesto roto")

	assert instalar.ofrecer_instalacion(None, tmp_path / "falta.nvda-addon", construir=construir) is False
	assert "manifiesto roto" in mensajes[0]
	assert not _DialogoFalso.creados


def test_error_con_boton_en_espanol_sin_dialogos_del_proyecto(monkeypatch):
	"""Sin ``app.ui.accesibilidad.dialogos``, el error sale en un MessageDialog con «Aceptar»."""
	wx = pytest.importorskip("wx")
	_DialogoFalso.creados = []
	monkeypatch.setattr(wx, "MessageDialog", _DialogoFalso)
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad.dialogos", None)  # import → ImportError
	instalar._mostrar_error(None, "fallo", "Complemento de NVDA")
	dialogo = _DialogoFalso.creados[0]
	assert dialogo.etiquetas == ("&Aceptar",)
	assert dialogo.estilo & wx.ICON_ERROR
	assert dialogo.destruido


def test_error_usa_los_dialogos_del_proyecto_si_existen(monkeypatch):
	import types

	pytest.importorskip("wx")
	recibidos = []
	falso = types.SimpleNamespace(mostrar_error=lambda padre, mensaje, titulo: recibidos.append((mensaje, titulo)))
	paquete = types.ModuleType("app.ui.accesibilidad")
	paquete.dialogos = falso
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad", paquete)
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad.dialogos", falso)
	instalar._mostrar_error(None, "fallo", "Complemento de NVDA")
	assert recibidos == [("fallo", "Complemento de NVDA")]


# La wx.App de la sesión. Esta referencia de módulo la mantiene viva hasta el final:
# sin ella, Python destruiría la aplicación recién creada al salir de la fixture.
_APLICACION_WX = None


def test_boton_con_etiqueta_y_ayuda():
	wx = pytest.importorskip("wx")
	# La wx.App de la sesión si ya hay una; nunca se destruye (otra wx.App tras
	# destruir la primera deja sin aplicación al resto de la batería).
	global _APLICACION_WX
	_APLICACION_WX = wx.GetApp() or wx.App(False)
	wx.HelpProvider.Set(wx.SimpleHelpProvider())  # sin proveedor, wx descarta la ayuda
	marco = wx.Frame(None)
	try:
		boton = instalar.crear_boton(marco)
		assert boton.GetLabel() == "&Instalar el complemento de NVDA..."
		assert boton.GetHelpText()
	finally:
		marco.Destroy()
