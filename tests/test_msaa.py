"""Pruebas MSAA de los controles accesibles (PLANTILLA del agente «accesibilidad-wx»).

Comprueban lo que expone Windows a los lectores de pantalla: nombres
tomados de la etiqueta, estado de las casillas, valor del deslizador...
Se saltan si no hay ``comtypes``.
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")
pytest.importorskip("comtypes")

from app.ui.accesibilidad.casillas import CheckListBoxAccesible  # noqa: E402
from app.ui.accesibilidad.controles import SliderAccesible  # noqa: E402
from app.ui.accesibilidad.lista import ListaAccesible  # noqa: E402
from app.ui.accesibilidad.nombres import crear_campo, crear_selector_ruta, nombrar_campo_interno  # noqa: E402

from tests import msaa  # noqa: E402


@pytest.fixture(scope="module")
def app(app_wx):
	# La wx.App es única para toda la sesión (conftest): crear y destruir otra
	# deja sin aplicación a los módulos que se ejecutan después.
	return app_wx


@pytest.fixture
def panel(app):
	marco = wx.Frame(None)
	panel = wx.Panel(marco)
	yield panel
	marco.Destroy()


def test_campo_toma_el_nombre_y_el_atajo_de_su_etiqueta(panel):
	_, campo = crear_campo(panel, "&Carpeta de destino:", wx.TextCtrl)
	datos = msaa.propiedades(campo)
	assert datos["nombre"].rstrip(":") == "Carpeta de destino"
	assert datos["atajo"] == "Alt+c"


def test_choice_y_lista_toman_su_etiqueta(panel):
	_, eleccion = crear_campo(panel, "&Formato:", wx.Choice, choices=["MP3", "OGG"])
	wx.StaticText(panel, label="&Descargas:")
	lista = ListaAccesible(panel, "Descargas", [("Nombre", 100)])
	assert msaa.propiedades(eleccion)["nombre"].rstrip(":") == "Formato"
	assert msaa.propiedades(lista)["nombre"].rstrip(":") == "Descargas"


def test_control_creado_antes_que_su_etiqueta_queda_sin_nombre(panel):
	"""Documenta el error típico: el orden de creación manda (no el del sizer)."""
	campo = wx.TextCtrl(panel)
	wx.StaticText(panel, label="Etiqueta tardía:")
	assert not msaa.propiedades(campo)["nombre"]


def test_selector_de_ruta_accesible(panel):
	_, campo, boton = crear_selector_ruta(panel, "&Guardar en:")
	assert msaa.propiedades(campo)["nombre"].rstrip(":") == "Guardar en"
	assert msaa.propiedades(boton)["nombre"] == "Examinar..."


def test_checklistbox_expone_casillas_y_estado(panel):
	wx.StaticText(panel, label="&Opciones:")
	casillas = CheckListBoxAccesible(panel, "Opciones", ["Subtítulos", "Miniatura"])
	casillas.Check(1, True)
	primero, segundo = msaa.propiedades(casillas, 1), msaa.propiedades(casillas, 2)
	assert primero["rol"] == msaa.ROL_CASILLA
	assert not primero["estado"] & msaa.ESTADO_MARCADO
	assert segundo["estado"] & msaa.ESTADO_MARCADO


def test_slider_expone_el_valor_real(panel):
	wx.StaticText(panel, label="&Volumen:")
	deslizador = SliderAccesible(panel, valor=5, minimo=0, maximo=10)
	datos = msaa.propiedades(deslizador)
	assert datos["nombre"].rstrip(":") == "Volumen"
	assert datos["valor"] == "5 de 10"


def test_spinctrldouble_campo_interno_con_nombre(panel):
	wx.StaticText(panel, label="&Velocidad:")
	numero = wx.SpinCtrlDouble(panel, min=0.5, max=4, initial=1, inc=0.25)
	nombrar_campo_interno(numero, "Velocidad")
	internos = [h for h in numero.GetChildren() if isinstance(h, wx.TextCtrl)]
	if not internos:
		pytest.skip("En esta versión SpinCtrlDouble es nativo y no tiene campo interno")
	assert msaa.propiedades(internos[0])["nombre"] == "Velocidad"


def test_lista_con_casillas_nativas(panel):
	wx.StaticText(panel, label="&Tareas:")
	lista = ListaAccesible(panel, "Tareas", [("Tarea", 100)], con_casillas=True)
	lista.agregar_fila(["Copiar"])
	lista.agregar_fila(["Mover"])
	lista.CheckItem(1, True)
	assert msaa.propiedades(lista, 2)["estado"] & msaa.ESTADO_MARCADO
	assert not msaa.propiedades(lista, 1)["estado"] & msaa.ESTADO_MARCADO
