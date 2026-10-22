"""Pruebas de la propia infraestructura de pruebas (PLANTILLA del agente «pruebas»).

Comprueban que ``conftest.py`` y ``ayudas_wx.py`` hacen lo que prometen: que
el data real no se toca, que no se sale a Internet, que la voz y los diálogos
se simulan y que los eventos de wx se procesan sin ventanas visibles. Si alguna
falla tras cambiar el conftest, el aislamiento ya no es fiable.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import types
import urllib.request

import pytest

import tests.conftest as configuracion

wx = pytest.importorskip("wx")

from tests.ayudas_wx import (  # noqa: E402
	enviar_orden,
	escribir_con_simulador,
	esperar_hasta,
	procesar_eventos,
	propiedades_msaa,
	simular_tecla,
)

# Valor de la variable al importar este módulo: ya debe apuntar fuera del data real.
DATA_AL_IMPORTAR = os.environ.get(configuracion.VARIABLE_DATA)


def test_conftest_importado_una_sola_vez():
	"""Si se importara dos veces, se crearía otra carpeta de sesión."""
	nombres = [nombre for nombre, modulo in sys.modules.items() if getattr(modulo, "__file__", None) == configuracion.__file__]
	assert nombres == ["tests.conftest"]


# ── data ─────────────────────────────────────────────────────────────────────

def test_data_redirigido_antes_de_importar_la_aplicacion():
	assert DATA_AL_IMPORTAR
	assert os.path.realpath(DATA_AL_IMPORTAR) != os.path.realpath(configuracion.RAIZ / "data")


def test_cada_prueba_tiene_su_data(data_temporal, tmp_path):
	assert os.environ[configuracion.VARIABLE_DATA] == str(data_temporal)
	assert data_temporal == tmp_path / "data"
	assert data_temporal.is_dir() and not any(data_temporal.iterdir())


def test_redirige_tambien_rutas_calculadas_al_importar(tmp_path, monkeypatch):
	modulo = types.ModuleType("rutas_de_prueba")
	modulo.DATA_DIR = "C:/data/real"
	monkeypatch.setitem(sys.modules, "rutas_de_prueba", modulo)
	monkeypatch.setattr(configuracion, "ATRIBUTOS_RUTA_DATA", [("rutas_de_prueba", "DATA_DIR")])
	configuracion.redirigir_data(tmp_path, monkeypatch)
	assert modulo.DATA_DIR == str(tmp_path)
	assert os.environ[configuracion.VARIABLE_DATA] == str(tmp_path)


# ── red ──────────────────────────────────────────────────────────────────────

def test_sin_red_bloquea_hosts_externos(sin_red):
	with pytest.raises(configuracion.ConexionBloqueada, match="ejemplo.invalid"):
		socket.create_connection(("ejemplo.invalid", 80), timeout=1)
	with pytest.raises(configuracion.ConexionBloqueada):
		urllib.request.urlopen("http://ejemplo.invalid/", timeout=1)
	assert "ejemplo.invalid" in sin_red.intentos


def test_sin_red_bloquea_ip_externa_aunque_no_haya_dns(sin_red):
	with pytest.raises(configuracion.ConexionBloqueada, match="192.0.2.1"):
		socket.create_connection(("192.0.2.1", 443), timeout=1)


def test_sin_red_permite_conexiones_locales(sin_red):
	servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	servidor.bind(("127.0.0.1", 0))
	servidor.listen(1)
	try:
		cliente = socket.create_connection(servidor.getsockname(), timeout=2)
		cliente.close()
	finally:
		servidor.close()
	assert sin_red.intentos == []


def test_la_red_se_bloquea_sin_pedirlo():
	"""El bloqueo es automático (autouse) en las pruebas sin @pytest.mark.red."""
	with pytest.raises(configuracion.ConexionBloqueada):
		socket.getaddrinfo("ejemplo.invalid", 80)


@pytest.mark.red
def test_marcada_red_no_bloquea():
	"""No sale a Internet: solo comprueba que el bloqueo no se aplicó (se ejecuta con -m red)."""
	assert getattr(socket.getaddrinfo, "__name__", "") != "resolver"
	assert getattr(socket.socket.connect, "__name__", "") != "conectar"


# ── voz ──────────────────────────────────────────────────────────────────────

def anunciar(texto: str) -> None:
	raise AssertionError("La voz real no debe ejecutarse en las pruebas")


def test_voz_simulada_captura_los_anuncios(voz_simulada):
	voz_simulada.parchear(f"{__name__}.anunciar")
	anunciar("Descarga terminada")
	anunciar(texto="Error")
	assert voz_simulada.textos == ["Descarga terminada", "Error"]
	assert voz_simulada.ultimo == "Error"
	assert voz_simulada.contiene("terminada")
	voz_simulada.limpiar()
	assert voz_simulada.ultimo is None


def test_voz_simulada_avisa_de_rutas_inexistentes(voz_simulada):
	with pytest.raises((ImportError, AttributeError)):
		voz_simulada.parchear("modulo_que_no_existe.hablar")


# ── wx ───────────────────────────────────────────────────────────────────────

def test_marco_no_se_muestra(marco):
	assert not marco.IsShown()
	assert wx.GetApp() is not None


def test_las_ventanas_nuevas_se_destruyen(app_wx):
	antes = {id(ventana) for ventana in wx.GetTopLevelWindows()}
	wx.Frame(None)
	wx.Dialog(None)
	configuracion._destruir_nuevas(antes)
	assert {id(ventana) for ventana in wx.GetTopLevelWindows()} <= antes


def test_procesar_eventos_entrega_callafter(app_wx):
	recibido = []
	wx.CallAfter(recibido.append, "hecho")
	assert recibido == []
	procesar_eventos()
	assert recibido == ["hecho"]


def test_esperar_hasta_hace_funcionar_los_temporizadores(app_wx):
	disparos = []
	wx.CallLater(30, disparos.append, 1)
	esperar_hasta(lambda: disparos == [1], tiempo=2)


def test_esperar_hasta_falla_con_mensaje(app_wx):
	with pytest.raises(AssertionError, match="nunca"):
		esperar_hasta(lambda: False, tiempo=0.1, mensaje="nunca")


def test_hilo_con_callafter(marco):
	etiqueta = wx.StaticText(marco, label="")
	threading.Thread(target=lambda: wx.CallAfter(etiqueta.SetLabel, "listo"), daemon=True).start()
	esperar_hasta(lambda: etiqueta.GetLabel() == "listo")


def test_simular_tecla_detecta_skip(marco):
	teclas = []

	def al_pulsar(evento):
		teclas.append((evento.GetKeyCode(), evento.ControlDown(), evento.ShiftDown()))
		if evento.GetKeyCode() != wx.WXK_ESCAPE:
			evento.Skip()

	marco.Bind(wx.EVT_CHAR_HOOK, al_pulsar)
	campo = wx.TextCtrl(marco)
	resultado = simular_tecla(campo, "s", ctrl=True, mayus=True)
	assert resultado.saltado and not resultado.gestionado
	assert teclas == [(ord("S"), True, True)]
	escape = simular_tecla(campo, wx.WXK_ESCAPE)
	assert escape.gestionado and not escape.saltado


def test_enviar_orden_pulsa_boton_y_casilla(panel):
	pulsaciones = []
	boton = wx.Button(panel, label="&Aceptar")
	boton.Bind(wx.EVT_BUTTON, lambda evento: pulsaciones.append("boton"))
	casilla = wx.CheckBox(panel, label="&Recordar")
	casilla.Bind(wx.EVT_CHECKBOX, lambda evento: pulsaciones.append(evento.IsChecked()))
	enviar_orden(boton)
	casilla.SetValue(True)
	enviar_orden(casilla, wx.wxEVT_CHECKBOX)
	assert pulsaciones == ["boton", True]


def test_dialogo_modal_devuelve_la_respuesta_y_guarda_el_contenido(marco, dialogo_modal):
	dialogo_modal.responder(wx.ID_NO)
	dialogo = wx.MessageDialog(marco, "¿Guardar los cambios?", "Salir", wx.YES_NO)
	assert dialogo.ShowModal() == wx.ID_NO
	dialogo.Destroy()
	assert dialogo_modal.ultimo.mensaje == "¿Guardar los cambios?"
	assert dialogo_modal.ultimo.titulo == "Salir"
	assert dialogo_modal.ultimo.clase == "MessageDialog"


def test_dialogo_modal_con_dialogo_propio_y_funcion(marco, dialogo_modal):
	class Propio(wx.Dialog):
		def __init__(self, padre):
			super().__init__(padre, title="Propio")
			wx.StaticText(self, label="&Clave:")
			self.campo = wx.TextCtrl(self)

	dialogo_modal.responder(lambda dialogo: (dialogo.campo.SetValue("abc"), wx.ID_OK)[1])
	dialogo = Propio(marco)
	assert dialogo.ShowModal() == wx.ID_OK
	assert dialogo.campo.GetValue() == "abc"
	dialogo.Destroy()
	assert "&Clave:" in dialogo_modal.ultimo.textos


def test_dialogo_modal_por_defecto_acepta(marco, dialogo_modal):
	dialogo = wx.TextEntryDialog(marco, "Nombre:", "Nuevo")
	assert dialogo.ShowModal() == wx.ID_OK
	dialogo.Destroy()
	assert wx.MessageBox("Hecho", "Aviso") == wx.OK


@pytest.mark.skipif(sys.platform != "win32", reason="MSAA solo existe en Windows")
def test_propiedades_msaa_leen_el_nombre_de_la_etiqueta(panel):
	wx.StaticText(panel, label="&Buscar:")
	campo = wx.TextCtrl(panel)
	assert propiedades_msaa(campo)["nombre"].rstrip(":") == "Buscar"


@pytest.mark.interfaz
def test_simulador_escribe_en_un_campo_visible(marco_visible):
	"""Teclas REALES: solo se ejecuta con -m interfaz y si la ventana queda en primer plano."""
	campo = wx.TextCtrl(marco_visible)
	marco_visible.Layout()
	escribir_con_simulador(campo, "hola")
	assert campo.GetValue() == "hola"


def test_show_modal_real_falla_al_momento_con_el_titulo(marco):
	"""Sin ``dialogo_modal``, un modal real no se abre: la prueba falla diciendo cuál era."""
	dialogo = wx.Dialog(marco, title="Cuadro olvidado")
	try:
		with pytest.raises(RuntimeError, match="«Cuadro olvidado»"):
			dialogo.ShowModal()
	finally:
		dialogo.Destroy()
	nativo = wx.MessageDialog(marco, "¿Seguro?", "Nativo olvidado")
	try:
		with pytest.raises(RuntimeError, match="«Nativo olvidado»"):
			nativo.ShowModal()
	finally:
		nativo.Destroy()
	with pytest.raises(RuntimeError, match="«Caja olvidada»"):
		wx.MessageBox("Hola", "Caja olvidada")


def test_dialogo_propio_hereda_la_proteccion(marco):
	class Propio(wx.Dialog):
		pass

	dialogo = Propio(marco, title="Propio olvidado")
	try:
		with pytest.raises(RuntimeError, match="«Propio olvidado»"):
			dialogo.ShowModal()
	finally:
		dialogo.Destroy()


def test_dialogo_modal_sustituye_a_la_proteccion(marco, dialogo_modal):
	dialogo = wx.Dialog(marco, title="Simulado")
	assert dialogo.ShowModal() == wx.ID_OK
	dialogo.Destroy()
	assert dialogo_modal.ultimo.titulo == "Simulado"
