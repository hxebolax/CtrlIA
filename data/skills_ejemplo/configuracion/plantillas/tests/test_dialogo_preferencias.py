"""Pruebas del diálogo de preferencias (PLANTILLA del agente «configuracion»).

El diálogo se crea SIN mostrarlo y se destruye al terminar. Los avisos modales
(``mostrar_aviso``, ``confirmar``, ``mostrar_ayuda``) se sustituyen: nunca se
llama a ShowModal de verdad.
"""

from __future__ import annotations

import json

import pytest

wx = pytest.importorskip("wx")

from app.core import configuracion as mod_config  # noqa: E402
from app.core.configuracion import Configuracion  # noqa: E402
from app.ui import dialogo_preferencias as mod  # noqa: E402


# La wx.App de la sesión. Esta referencia de módulo la mantiene viva hasta el final:
# sin ella, Python destruiría la aplicación recién creada al salir de la fixture.
_APLICACION_WX = None


@pytest.fixture(scope="module")
def app():
	"""La wx.App de la sesión: se reutiliza la que exista y NUNCA se destruye.

	Crear y destruir una wx.App por módulo deja sin aplicación (o con una ya
	cerrada) a los módulos que se ejecutan después cuando estas pruebas se juntan
	con las del proyecto, y crear otra wx.App tras destruir la primera llegó a
	corromper la memoria (0xc0000374). Si el proyecto tiene una fixture
	``app_wx`` de sesión en su ``conftest.py``, devuelva esa en lugar de esto.
	"""
	global _APLICACION_WX
	_APLICACION_WX = wx.GetApp() or wx.App(False)
	return _APLICACION_WX


class SecretosFalsos:
	"""Almacén de secretos en memoria (las pruebas de DPAPI están en test_secretos)."""

	def __init__(self):
		self.datos = {"red.contrasena_proxy": "vieja"}

	def obtener(self, nombre, por_defecto=None):
		return self.datos.get(nombre, por_defecto)

	def guardar_varios(self, cambios):
		for nombre, valor in cambios.items():
			if valor is None:
				self.datos.pop(nombre, None)
			else:
				self.datos[nombre] = valor


@pytest.fixture
def entorno(app, tmp_path, monkeypatch):
	monkeypatch.setenv(mod_config.VARIABLE_DATA, str(tmp_path / "data"))
	avisos, preguntas, ayudas = [], [], []
	monkeypatch.setattr(mod, "mostrar_aviso", lambda padre, texto, titulo: avisos.append(texto))
	monkeypatch.setattr(mod, "confirmar", lambda padre, texto, titulo: (preguntas.append(texto), True)[1])
	monkeypatch.setattr(mod, "mostrar_ayuda", lambda padre, titulo, texto: ayudas.append(texto))
	config = Configuracion()
	config.establecer("apariencia.tamano_fuente", 14)
	secretos = SecretosFalsos()
	dialogo = mod.DialogoPreferencias(None, config, secretos=secretos)
	yield {"config": config, "dialogo": dialogo, "avisos": avisos, "preguntas": preguntas,
		"ayudas": ayudas, "secretos": secretos, "archivo": config.ruta}
	dialogo.Destroy()
	wx.Yield()


def _pulsar(boton):
	evento = wx.CommandEvent(wx.wxEVT_BUTTON, boton.GetId())
	evento.SetEventObject(boton)
	boton.GetEventHandler().ProcessEvent(evento)


def test_no_se_muestra_y_carga_valores(entorno):
	dialogo, config = entorno["dialogo"], entorno["config"]
	assert not dialogo.IsShown()
	assert dialogo.control_de("apariencia.tamano_fuente").GetValue() == 14
	assert dialogo.control_de("general.confirmar_salida").GetValue() is True
	assert dialogo.control_de("red.contrasena_proxy").GetValue() == "vieja"
	valores = dialogo.valores()
	for clave, valor in valores.items():
		assert config.obtener(clave) == valor, clave


def test_paginas_con_nombre(entorno):
	libro = entorno["dialogo"].libro
	titulos = [libro.GetPageText(i) for i in range(libro.GetPageCount())]
	assert titulos == ["General", "Apariencia", "Accesibilidad", "Red"]


def test_botones_en_espanol_y_escape(entorno):
	dialogo = entorno["dialogo"]
	assert dialogo.boton_aceptar.GetLabel() == "&Aceptar"
	assert dialogo.boton_cancelar.GetLabel() == "&Cancelar"
	assert dialogo.boton_aplicar.GetLabel() == "A&plicar"
	assert dialogo.GetEscapeId() == wx.ID_CANCEL


def test_foco_inicial_en_la_primera_opcion(entorno):
	dialogo = entorno["dialogo"]
	assert dialogo.control_inicial() is dialogo.control_de("general.idioma")


def test_etiqueta_justo_antes_de_cada_control(entorno):
	dialogo = entorno["dialogo"]
	for pagina in mod.PAGINAS:
		for opcion in pagina.opciones:
			control = dialogo.control_de(opcion.clave)
			if opcion.tipo == mod.BOOL:
				assert control.GetLabel() == opcion.etiqueta
				continue
			hermanos = list(control.GetParent().GetChildren())
			anterior = hermanos[hermanos.index(control) - 1]
			assert isinstance(anterior, wx.StaticText), opcion.clave
			assert anterior.GetLabel() == opcion.etiqueta, opcion.clave


def test_cada_control_tiene_ayuda_y_ctrl_h_la_muestra(entorno):
	dialogo = entorno["dialogo"]
	for pagina in mod.PAGINAS:
		for opcion in pagina.opciones:
			assert dialogo.control_de(opcion.clave).GetHelpText() == opcion.ayuda
	dialogo.mostrar_ayuda_de(dialogo.control_de("apariencia.tamano_fuente"))
	assert entorno["ayudas"] and "Tamaño de la letra" in entorno["ayudas"][0]
	assert "Se aplica al momento" in entorno["ayudas"][0]


def test_tecla_ctrl_h_abre_la_ayuda(entorno):
	dialogo = entorno["dialogo"]
	evento = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
	evento.SetKeyCode(ord("H"))
	evento.SetControlDown(True)
	dialogo._al_tecla(evento)
	assert len(entorno["ayudas"]) == 1
	otra = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
	otra.SetKeyCode(ord("H"))  # sin Ctrl: no es la ayuda
	dialogo._al_tecla(otra)
	assert len(entorno["ayudas"]) == 1


def test_cancelar_no_guarda(entorno):
	dialogo, config = entorno["dialogo"], entorno["config"]
	antes = entorno["archivo"].read_bytes()
	avisos = []
	config.suscribir(lambda c, a, n: avisos.append(c))
	dialogo.control_de("apariencia.tamano_fuente").SetValue(30)
	dialogo.control_de("red.contrasena_proxy").SetValue("nueva")
	_pulsar(dialogo.boton_cancelar)
	assert dialogo.GetReturnCode() == wx.ID_CANCEL
	assert config.obtener("apariencia.tamano_fuente") == 14
	assert entorno["archivo"].read_bytes() == antes
	assert entorno["secretos"].datos["red.contrasena_proxy"] == "vieja"
	assert avisos == []


def test_aplicar_guarda_y_notifica(entorno):
	dialogo, config = entorno["dialogo"], entorno["config"]
	avisos = []
	config.suscribir(lambda c, a, n: avisos.append((c, a, n)))
	dialogo.control_de("apariencia.tamano_fuente").SetValue(20)
	dialogo.control_de("apariencia.tema").SetSelection(2)  # oscuro
	dialogo.control_de("general.minimizar_a_bandeja").SetValue(True)
	dialogo.control_de("red.contrasena_proxy").SetValue("nueva")
	_pulsar(dialogo.boton_aplicar)
	assert ("apariencia.tamano_fuente", 14, 20) in avisos
	assert ("apariencia.tema", "sistema", "oscuro") in avisos
	assert len(avisos) == 3
	guardado = json.loads(entorno["archivo"].read_text(encoding="utf-8"))
	assert guardado["apariencia"]["tamano_fuente"] == 20
	assert guardado["general"]["minimizar_a_bandeja"] is True
	assert "nueva" not in entorno["archivo"].read_text(encoding="utf-8"), "el secreto no va al JSON"
	assert entorno["secretos"].datos["red.contrasena_proxy"] == "nueva"
	assert dialogo.IsShown() is False  # Aplicar no cierra


def test_aceptar_guarda_y_cierra(entorno):
	dialogo, config = entorno["dialogo"], entorno["config"]
	dialogo.control_de("red.servidor_proxy").SetValue("proxy.ejemplo")
	_pulsar(dialogo.boton_aceptar)
	assert dialogo.GetReturnCode() == wx.ID_OK
	assert config.obtener("red.servidor_proxy") == "proxy.ejemplo"


def test_valor_invalido_no_guarda_nada_y_lleva_el_foco(entorno):
	dialogo, config = entorno["dialogo"], entorno["config"]
	dialogo.control_de("apariencia.tamano_fuente").SetValue(22)
	dialogo.control_de("red.servidor_proxy").SetValue("x" * 300)  # máximo 255
	assert dialogo.aplicar() is False
	assert entorno["avisos"] and "Servidor proxy" in entorno["avisos"][0]
	assert dialogo.libro.GetSelection() == 3
	assert config.obtener("apariencia.tamano_fuente") == 14, "todo o nada"


def test_restablecer_pide_confirmacion_y_no_guarda_hasta_aplicar(entorno):
	dialogo, config = entorno["dialogo"], entorno["config"]
	_pulsar(dialogo.boton_restablecer)
	assert entorno["preguntas"], "se pidió confirmación"
	assert dialogo.control_de("apariencia.tamano_fuente").GetValue() == 11
	assert config.obtener("apariencia.tamano_fuente") == 14
	assert dialogo.control_de("red.contrasena_proxy").GetValue() == "vieja", "los secretos no se borran"
	_pulsar(dialogo.boton_aplicar)
	assert config.obtener("apariencia.tamano_fuente") == 11


def test_suscriptor_de_ventana_abierta_aplica_con_callafter(entorno):
	"""Recorrido completo: una ventana abierta aplica el cambio al momento."""
	config, dialogo = entorno["config"], entorno["dialogo"]
	marco = wx.Frame(None)
	texto = wx.TextCtrl(marco)

	def al_cambiar(clave, _anterior, nuevo):
		wx.CallAfter(aplicar_fuente, nuevo)

	def aplicar_fuente(tamano):
		fuente = texto.GetFont()
		fuente.SetPointSize(tamano)
		texto.SetFont(fuente)

	config.suscribir(al_cambiar, prefijo="apariencia.tamano_fuente")
	dialogo.control_de("apariencia.tamano_fuente").SetValue(24)
	assert dialogo.aplicar()
	wx.Yield()  # procesa el CallAfter
	assert texto.GetFont().GetPointSize() == 24
	marco.Destroy()


def test_opciones_secretas_se_omiten_sin_almacen(app, tmp_path, monkeypatch):
	monkeypatch.setenv(mod_config.VARIABLE_DATA, str(tmp_path / "data"))
	dialogo = mod.DialogoPreferencias(None, Configuracion())
	try:
		with pytest.raises(KeyError):
			dialogo.control_de("red.contrasena_proxy")
	finally:
		dialogo.Destroy()
