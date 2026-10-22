"""Pruebas de la ventana principal con wx real, SIN mostrarla en pantalla."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import wx

from app.core.config import Configuracion
from app.core.instancia_unica import ORDEN_ABRIR, Orden
from app.core.version import NOMBRE_APLICACION
from app.ui import ventana_principal as vp

from .conftest import procesar_eventos

MENUS_ESPERADOS = ["&Archivo", "&Editar", "&Ver", "&Herramientas", "Ay&uda"]
# nombre del elemento → (modificadores, tecla)
ATAJOS_ESPERADOS = {
	"abrir": (wx.ACCEL_CTRL, ord("O")),
	"copiar": (wx.ACCEL_CTRL, ord("C")),
	"seleccionar_todo": (wx.ACCEL_CTRL, ord("A")),
	"actualizar": (wx.ACCEL_NORMAL, wx.WXK_F5),
	"opciones": (wx.ACCEL_CTRL, ord(",")),
	"manual": (wx.ACCEL_NORMAL, wx.WXK_F1),
	"teclas": (wx.ACCEL_CTRL, wx.WXK_F1),
	"ayuda_contextual": (wx.ACCEL_CTRL, ord("H")),
}
PALABRAS_EN_INGLES = {"file", "edit", "view", "tools", "help", "open", "exit", "quit", "copy", "select", "about", "options", "refresh"}


@pytest.fixture
def ventana(app_wx):
	marco = vp.VentanaPrincipal(Configuracion())
	yield marco
	if marco:
		marco.salir()
		procesar_eventos(lambda: not marco, 2)


def _elementos(menu: wx.Menu):
	for item in menu.GetMenuItems():
		if not item.IsSeparator():
			yield item


def test_arranca_sin_mostrarse(ventana):
	assert ventana.GetTitle() == NOMBRE_APLICACION
	assert not ventana.IsShown()
	assert ventana.GetStatusBar() is not None


def test_menus_en_espanol_con_teclas_de_acceso(ventana):
	barra = ventana.GetMenuBar()
	assert [barra.GetMenuLabel(i) for i in range(barra.GetMenuCount())] == MENUS_ESPERADOS
	for i in range(barra.GetMenuCount()):
		letras = []
		for item in _elementos(barra.GetMenu(i)):
			etiqueta = item.GetItemLabel()
			assert "&" in etiqueta, f"«{etiqueta}» no tiene tecla de acceso"
			letras.append(etiqueta[etiqueta.index("&") + 1].lower())
			palabras = {p.lower().strip(".&") for p in item.GetItemLabelText().split()}
			assert not palabras & PALABRAS_EN_INGLES, f"«{etiqueta}» parece estar en inglés"
			assert item.GetHelp(), f"«{etiqueta}» no tiene texto de ayuda"
		assert len(letras) == len(set(letras)), f"teclas de acceso repetidas en {barra.GetMenuLabel(i)}: {letras}"


def test_atajos_de_teclado(ventana):
	for nombre, (modificadores, tecla) in ATAJOS_ESPERADOS.items():
		atajo = ventana.elementos_menu[nombre].GetAccel()
		assert atajo is not None, f"{nombre} no tiene atajo"
		assert (atajo.GetFlags(), atajo.GetKeyCode()) == (modificadores, tecla), nombre
	# Escape nunca cierra la ventana principal: ningún elemento lo usa.
	for item in ventana.elementos_menu.values():
		atajo = item.GetAccel()
		assert atajo is None or atajo.GetKeyCode() != wx.WXK_ESCAPE


def test_etiqueta_creada_justo_antes_de_la_lista(ventana):
	hijos = list(ventana.panel.GetChildren())
	posicion = hijos.index(ventana.lista)
	anterior = hijos[posicion - 1]
	assert isinstance(anterior, wx.StaticText)
	assert anterior.GetLabel() == "&Elementos:"
	assert ventana.lista.GetHelpText(), "Ctrl+H necesita la ayuda del control"


def test_cierre_guarda_la_geometria(app_wx, datos_temporales):
	config = Configuracion()
	marco = vp.VentanaPrincipal(config)
	marco.SetSize(wx.Size(640, 480))
	marco._rect_normal = marco.GetRect()
	marco.Close()
	assert procesar_eventos(lambda: not marco), "la ventana debe destruirse"
	guardada = Configuracion()
	assert guardada.obtener("ventana.ancho") == 640
	assert guardada.obtener("ventana.alto") == 480
	assert (datos_temporales / "config" / "config.json").is_file()


def test_posicion_fuera_de_pantalla_se_centra(app_wx):
	config = Configuracion()
	config.establecer("ventana.x", 30000)
	config.establecer("ventana.y", 30000)
	config.establecer("ventana.ancho", 50)  # demasiado pequeña: se usa el tamaño estándar
	marco = vp.VentanaPrincipal(config)
	try:
		assert marco.GetPosition().x != 30000
		assert marco.GetSize().width >= vp.TAMANO_MINIMO[0]
	finally:
		marco.salir()
		procesar_eventos(lambda: not marco)


def test_cerrar_a_bandeja_oculta_y_salir_cierra(app_wx, monkeypatch):
	monkeypatch.setattr(vp.IconoBandeja, "mostrar", lambda self: True)
	config = Configuracion()
	config.establecer("general.cerrar_a_bandeja", True)
	marco = vp.VentanaPrincipal(config)
	# No se muestra la ventana en pruebas: se comprueba que se pide ocultarla.
	ocultada = []
	monkeypatch.setattr(marco, "Hide", lambda: ocultada.append(True) or True)
	marco.Close()  # como Alt+F4: se puede vetar
	procesar_eventos(segundos=0.2)
	assert marco, "con «cerrar a la bandeja» la ventana sigue viva"
	assert ocultada == [True]
	marco.salir()  # Archivo, Salir: cierra de verdad
	assert procesar_eventos(lambda: not marco)


def test_lista_abrir_quitar_y_copiar(ventana, monkeypatch, tmp_path):
	copiado = []
	monkeypatch.setattr(vp, "copiar_al_portapapeles", lambda texto: copiado.append(texto) or True)
	ventana.abrir_archivos([tmp_path / "uno.txt", tmp_path / "dos.txt", tmp_path / "tres.txt"])
	assert ventana.lista.GetItemCount() == 3
	ventana.lista.Select(1)
	ventana._al_copiar(None)
	assert copiado == [f"dos.txt\t{tmp_path}"]
	ventana._al_quitar(None)
	assert ventana.lista.GetItemCount() == 2
	assert ventana.lista.GetFocusedItem() == 1, "el foco pasa al elemento siguiente"


def test_tarea_en_segundo_plano_vuelve_al_hilo_principal(ventana):
	resultado = {}

	def trabajo(detener):
		return threading.current_thread().name

	def al_terminar(nombre_hilo):
		resultado["hilo_trabajo"] = nombre_hilo
		resultado["hilo_entrega"] = threading.current_thread() is threading.main_thread()

	ventana.ejecutar_en_segundo_plano("prueba", trabajo, al_terminar)
	assert procesar_eventos(lambda: "hilo_entrega" in resultado)
	assert resultado["hilo_trabajo"] == "tarea-prueba"
	assert resultado["hilo_entrega"] is True


def test_ordenes_de_otra_instancia(app_wx, monkeypatch, tmp_path):
	restauradas = []

	class InstanciaFalsa:
		def recoger_ordenes(self):
			return [Orden(ORDEN_ABRIR, [str(tmp_path / "recibido.txt")], 1.0)]

	marco = vp.VentanaPrincipal(Configuracion(), InstanciaFalsa())
	monkeypatch.setattr(marco, "restaurar", lambda: restauradas.append(True))
	try:
		marco._al_revisar_buzon(None)
		assert marco.lista.GetItemText(0, 0) == "recibido.txt"
		assert restauradas == [True]
	finally:
		marco.salir()
		procesar_eventos(lambda: not marco)


def test_ayuda_contextual_sin_ayuda(ventana, monkeypatch):
	mensajes = []
	from app.ui import ventana_principal

	monkeypatch.setattr(ventana_principal, "mostrar_mensaje", lambda padre, texto, *a, **k: mensajes.append(texto))
	monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: None))
	ventana._al_ayuda_contextual(None)
	assert mensajes and "F1" in mensajes[0]


def test_documento_inexistente_avisa(ventana, monkeypatch):
	mensajes = []
	from app.ui import dialogo_texto

	monkeypatch.setattr(dialogo_texto, "mostrar_mensaje", lambda padre, texto, *a, **k: mensajes.append(texto))
	assert dialogo_texto.mostrar_documento(ventana, "no_existe", "Prueba") is False
	assert mensajes and "no_existe" in mensajes[0]


def test_documento_markdown_se_muestra_como_texto(ventana, monkeypatch, datos_temporales):
	mostrados = []
	from app.ui import dialogo_texto

	(datos_temporales / "docs").mkdir(parents=True)
	(datos_temporales / "docs" / "manual.md").write_text("# Manual\n\nHola", encoding="utf-8")
	monkeypatch.setattr(dialogo_texto, "mostrar_texto", lambda padre, titulo, etiqueta, texto, copiable=False: mostrados.append(texto))
	assert dialogo_texto.mostrar_documento(ventana, "manual", "Manual") is True
	assert mostrados == ["# Manual\n\nHola"]
	assert Path(dialogo_texto.buscar_documento("manual")).suffix == ".md"
