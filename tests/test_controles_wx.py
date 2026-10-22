"""Pruebas de los controles con wxPython real (PLANTILLA del agente «accesibilidad-wx»).

Crean ventanas SIN mostrarlas y sustituyen la voz por una lista, así que no
necesitan lector de pantalla. Comprueban la lógica (qué se anunciaría, qué
nombre accesible se expone, qué etiquetas llevan los botones), NO que NVDA
lo lea: eso es una prueba manual (ver referencias/pruebas.md).
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")

from app.ui.accesibilidad import voz  # noqa: E402
from app.ui.accesibilidad.casillas import CheckListBoxAccesible  # noqa: E402
from app.ui.accesibilidad.composicion import PreferenciasLectura  # noqa: E402
from app.ui.accesibilidad.dialogos import DialogoMensaje, DialogoTexto, crear_botonera  # noqa: E402
from app.ui.accesibilidad.lista import ListaAccesible  # noqa: E402
from app.ui.accesibilidad.nombres import asignar_nombre_accesible, crear_campo, nombre_limpio  # noqa: E402


@pytest.fixture(scope="module")
def app(app_wx):
	# La wx.App es única para toda la sesión (conftest): crear y destruir otra
	# deja sin aplicación a los módulos que se ejecutan después.
	return app_wx


@pytest.fixture
def marco(app):
	ventana = wx.Frame(None)
	yield ventana
	ventana.Destroy()


@pytest.fixture
def dichos(monkeypatch):
	"""Sustituye la salida de voz por una lista de textos."""
	textos: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *a, **k: textos.append(texto))
	return textos


def _tecla(control, codigo, modificadores=wx.MOD_NONE):
	evento = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
	evento.SetKeyCode(codigo)
	evento.SetControlDown(bool(modificadores & wx.MOD_CONTROL))
	evento.SetShiftDown(bool(modificadores & wx.MOD_SHIFT))
	evento.SetAltDown(bool(modificadores & wx.MOD_ALT))
	evento.SetEventObject(control)
	control._al_pulsar(evento)


@pytest.mark.parametrize("virtual", [False, True])
def test_lista_navegacion_por_columnas(marco, dichos, virtual):
	lista = ListaAccesible(marco, "Descargas", [("Nombre", 200), ("Estado", 100), ("Tamaño", 80)], virtual=virtual)
	lista.agregar_fila(["video.mp4", "Terminado", "1 GB"])
	lista.agregar_fila(["audio.mp3", "En curso", "5 MB"])
	lista.enfocar_fila(1)
	_tecla(lista, wx.WXK_RIGHT)
	_tecla(lista, wx.WXK_RIGHT)
	_tecla(lista, wx.WXK_RIGHT)  # ya en la última: se queda
	_tecla(lista, ord("1"), wx.MOD_CONTROL)
	_tecla(lista, ord("7"), wx.MOD_CONTROL)
	assert dichos == [
		"Fila 2, Estado, En curso",
		"Fila 2, Tamaño, 5 MB",
		"Fila 2, Tamaño, 5 MB",
		"Fila 2, Nombre, audio.mp3",
		"No existe la columna 7",
	]
	# Consultar columnas no cambia la selección.
	assert lista.GetFirstSelected() == 1


def test_lista_preferencias_se_leen_en_cada_anuncio(marco, dichos):
	preferencias = {"actual": PreferenciasLectura()}
	lista = ListaAccesible(marco, "Lista", [("A", 50), ("B", 50)], leer_preferencias=lambda: preferencias["actual"])
	lista.agregar_fila(["uno", "dos"])
	lista.enfocar_fila(0)
	_tecla(lista, wx.WXK_RIGHT)
	preferencias["actual"] = PreferenciasLectura(numero_fila=False, cabecera=False)  # «Aceptar» en Preferencias
	_tecla(lista, wx.WXK_LEFT)
	assert dichos == ["Fila 1, B, dos", "uno"]


def test_lista_texto_largo_integro(marco, dichos):
	largo = "x" * 3000
	lista = ListaAccesible(marco, "Lista", [("Texto", 100)])
	lista.agregar_fila([largo])
	assert lista.texto_celda(0, 0) == largo
	lista.enfocar_fila(0)
	_tecla(lista, ord("1"), wx.MOD_CONTROL)
	assert dichos[-1].endswith(largo)


def test_lista_vacia(marco, dichos):
	lista = ListaAccesible(marco, "Lista", [("A", 50)])
	_tecla(lista, ord("1"), wx.MOD_CONTROL)
	assert dichos == ["Lista vacía"]


@pytest.mark.parametrize("virtual", [False, True])
def test_lista_insertar_conserva_el_elemento_del_usuario(marco, virtual):
	lista = ListaAccesible(marco, "Lista", [("A", 50)], virtual=virtual)
	for texto in ("a", "b", "c"):
		lista.agregar_fila([texto])
	lista.enfocar_fila(1)  # «b»
	lista.insertar_fila(0, ["nuevo"])
	assert lista.texto_celda(lista.GetFocusedItem(), 0) == "b"
	lista.eliminar_fila(0)
	assert lista.texto_celda(lista.GetFocusedItem(), 0) == "b"


def test_lista_columna_protegida(marco, dichos):
	lista = ListaAccesible(marco, "Cuentas", [("Usuario", 80), ("Clave", 80)], columnas_protegidas=[1])
	lista.agregar_fila(["ana", "secreta"])
	lista.enfocar_fila(0)
	_tecla(lista, ord("2"), wx.MOD_CONTROL)
	assert "secreta" not in dichos[-1]


def test_lista_intro_activa(marco):
	lista = ListaAccesible(marco, "Lista", [("A", 50)])
	lista.agregar_fila(["uno"])
	lista.enfocar_fila(0)
	activadas = []
	lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: activadas.append(e.GetIndex()))
	_tecla(lista, wx.WXK_RETURN)
	wx.GetApp().ProcessPendingEvents()
	assert activadas == [0]


def test_lista_casillas(marco, dichos):
	lista = ListaAccesible(marco, "Tareas", [("Tarea", 100)], con_casillas=True)
	lista.agregar_fila(["Copiar"])
	lista.agregar_fila(["Mover"])
	lista.marcar_todas(True)
	assert lista.filas_marcadas() == [0, 1]
	assert dichos[-1] == "Todas marcadas"


def test_checklistbox_expone_rol_y_estado(marco, dichos):
	casillas = CheckListBoxAccesible(marco, "Opciones", ["Subtítulos", "Miniatura"], forzar_accesible=True)
	casillas.Check(1, True)
	accesible = casillas.GetAccessible()
	assert accesible.GetRole(1) == (wx.ACC_OK, wx.ROLE_SYSTEM_CHECKBUTTON)
	assert accesible.GetState(2)[1] & wx.ACC_STATE_SYSTEM_CHECKED
	assert not accesible.GetState(1)[1] & wx.ACC_STATE_SYSTEM_CHECKED
	casillas.marcar_todas(False)
	assert casillas.textos_marcados() == []
	assert dichos[-1] == "Todas desmarcadas"


def test_crear_campo_etiqueta_antes_y_nombre(marco):
	panel = wx.Panel(marco)
	etiqueta, campo = crear_campo(panel, "&Carpeta de destino:", wx.TextCtrl, ayuda="Dónde se guarda.")
	hijos = list(panel.GetChildren())
	assert hijos.index(etiqueta) == hijos.index(campo) - 1
	assert campo.GetName() == "Carpeta de destino"
	assert nombre_limpio("&Guardar && salir:") == "Guardar & salir"


def test_nombre_accesible_forzado(marco):
	campo = wx.TextCtrl(marco)
	asignar_nombre_accesible(campo, "Buscar")
	assert campo.GetAccessible().GetName(0) == (wx.ACC_OK, "Buscar")


def test_botones_en_espanol_y_predeterminado(marco):
	panel = wx.Panel(marco)
	crear_botonera(panel, wx.YES | wx.NO | wx.CANCEL, predeterminado=wx.ID_NO)
	etiquetas = {b.GetId(): b.GetLabel() for b in panel.GetChildren() if isinstance(b, wx.Button)}
	assert etiquetas == {wx.ID_YES: "&Sí", wx.ID_NO: "&No", wx.ID_CANCEL: "&Cancelar"}
	assert panel.FindWindow(wx.ID_NO).IsDefault() if hasattr(wx.Button, "IsDefault") else True


def test_mensaje_largo_en_campo_de_solo_lectura(marco):
	dialogo = DialogoMensaje(marco, "Primera línea\nSegunda línea", "Aviso")
	assert isinstance(dialogo.cuerpo, wx.TextCtrl) and not dialogo.cuerpo.IsEditable()
	dialogo.Destroy()


def test_dialogo_texto(marco):
	dialogo = DialogoTexto(marco, "&Nombre:", "Renombrar", valor="viejo", max_caracteres=20)
	assert dialogo.GetValue() == "viejo"
	assert dialogo.campo.GetName() == "Nombre"
	dialogo.Destroy()
