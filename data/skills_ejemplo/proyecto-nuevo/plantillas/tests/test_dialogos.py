"""Pruebas de los diálogos y la bandeja (se crean sin mostrarse; nunca ShowModal)."""

from __future__ import annotations

import sys
import types

import wx

from app.core.config import Configuracion
from app.core.version import APP_VERSION, NOMBRE_APLICACION
from app.ui.acerca_de import texto_acerca_de
from app.ui.bandeja import IconoBandeja
from app.ui.dialogo_opciones import DialogoOpciones
from app.ui import dialogo_texto
from app.ui.dialogo_texto import DialogoTexto


def _botones(ventana: wx.Window) -> dict[int, str]:
	return {hijo.GetId(): hijo.GetLabel() for hijo in ventana.GetChildren() if isinstance(hijo, wx.Button)}


def test_dialogo_texto_accesible(app_wx):
	dialogo = DialogoTexto(None, "Título", "&Contenido:", "línea 1\nlínea 2", copiable=True)
	try:
		campo = dialogo.campo
		assert campo.IsMultiLine() and not campo.IsEditable(), "texto largo: multilínea de solo lectura"
		hijos = list(dialogo.GetChildren())
		anterior = hijos[hijos.index(campo) - 1]
		assert isinstance(anterior, wx.StaticText) and anterior.GetLabel() == "&Contenido:"
		botones = _botones(dialogo)
		assert botones[wx.ID_CANCEL] == "C&errar"
		assert "&Copiar al portapapeles" in botones.values()
		assert dialogo.GetEscapeId() == wx.ID_CANCEL
		assert campo.GetInsertionPoint() == 0
	finally:
		dialogo.Destroy()


def test_dialogo_opciones_guarda(app_wx, datos_temporales):
	config = Configuracion()
	dialogo = DialogoOpciones(None, config)
	try:
		for casilla in dialogo.casillas.values():
			assert "&" in casilla.GetLabel()
			assert casilla.GetHelpText()
		botones = _botones(dialogo)
		assert botones[wx.ID_OK] == "&Aceptar"
		assert botones[wx.ID_CANCEL] == "&Cancelar"
		dialogo.casillas["general.minimizar_a_bandeja"].SetValue(True)
		dialogo.aplicar()
	finally:
		dialogo.Destroy()
	assert Configuracion().obtener("general.minimizar_a_bandeja") is True


def test_texto_acerca_de():
	texto = texto_acerca_de()
	assert NOMBRE_APLICACION in texto and APP_VERSION in texto
	assert "Carpeta de datos" in texto


def test_menu_de_bandeja_en_espanol(app_wx):
	llamadas = []
	icono = IconoBandeja("Prueba", lambda: llamadas.append("restaurar"), lambda: llamadas.append("salir"))
	try:
		menu = icono.CreatePopupMenu()
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		assert etiquetas == ["&Mostrar la ventana", "&Salir"]
		menu.Destroy()
		icono._restaurar()
		assert llamadas == ["restaurar"]
	finally:
		icono.quitar()


def _capturar_message_dialog(monkeypatch) -> list[tuple[str, str, str]]:
	"""Sustituye ``ShowModal`` de ``wx.MessageDialog``: anota título, mensaje y botón."""
	vistos: list[tuple[str, str, str]] = []

	def mostrar(dialogo):
		vistos.append((dialogo.GetTitle(), dialogo.GetMessage(), dialogo.GetOKLabel()))
		return wx.ID_OK

	monkeypatch.setattr(wx.MessageDialog, "ShowModal", mostrar)
	return vistos


def test_mensajes_con_boton_en_espanol(app_wx, monkeypatch):
	"""Sin ``app/ui/accesibilidad/dialogos.py``, un MessageDialog con «Aceptar» (nunca wx.MessageBox)."""
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad.dialogos", None)  # import → ImportError
	vistos = _capturar_message_dialog(monkeypatch)
	dialogo_texto.mostrar_mensaje(None, "Hola", "Ayuda")
	dialogo_texto.mostrar_error(None, "Fallo")
	assert vistos == [("Ayuda", "Hola", "&Aceptar"), ("Error", "Fallo", "&Aceptar")]


def test_mensajes_usan_los_dialogos_del_proyecto_si_existen(app_wx, monkeypatch):
	recibidos = []
	falso = types.SimpleNamespace(
		mostrar_mensaje=lambda padre, mensaje, titulo, icono=None: recibidos.append(("mensaje", mensaje, titulo)),
		mostrar_error=lambda padre, mensaje, titulo: recibidos.append(("error", mensaje, titulo)),
	)
	paquete = types.ModuleType("app.ui.accesibilidad")
	paquete.dialogos = falso
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad", paquete)
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad.dialogos", falso)
	dialogo_texto.mostrar_mensaje(None, "Hola", "Ayuda")
	dialogo_texto.mostrar_error(None, "Fallo", "Abrir")
	assert recibidos == [("mensaje", "Hola", "Ayuda"), ("error", "Fallo", "Abrir")]


def test_documento_inexistente_avisa_en_espanol(app_wx, monkeypatch):
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad.dialogos", None)
	vistos = _capturar_message_dialog(monkeypatch)
	assert dialogo_texto.mostrar_documento(None, "no_existe_nunca", "Manual") is False
	assert len(vistos) == 1 and vistos[0][0] == "Manual" and vistos[0][2] == "&Aceptar"
	assert "no_existe_nunca" in vistos[0][1]
