"""Pruebas de avisos sin voz (braille, barra de estado), sonidos, menús contextuales y baja visión.

Ninguna prueba habla ni suena: la voz está silenciada por el conftest y los
sonidos se sustituyen por un registro.
"""

from __future__ import annotations

import ast
import struct
import sys
import wave
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from app.core.configuracion import Configuracion  # noqa: E402
from app.services.agentes.base import Evento, TipoEvento  # noqa: E402
from app.services.agentes.claude import ProveedorClaude  # noqa: E402
from app.services.almacen import Almacen  # noqa: E402
from app.services.gestor import MensajeVista  # noqa: E402
from app.ui import avisos  # noqa: E402
from app.ui.accesibilidad import visual, voz  # noqa: E402
from tests.ayudas_wx import procesar_eventos  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"


@pytest.fixture
def config_voz():
	"""Deja la configuración de la voz como estaba al terminar."""
	copia = {**voz._config, "canales": dict(voz._config["canales"])}
	yield voz._config
	voz._config.clear()
	voz._config.update(copia)


@pytest.fixture
def opciones(monkeypatch):
	"""Configura ``avisos`` con un diccionario de opciones propio."""
	valores: dict = {}
	monkeypatch.setitem(avisos._estado, "opcion", lambda clave, defecto=None: valores.get(clave, defecto))
	monkeypatch.setitem(avisos._estado, "ventana", None)
	monkeypatch.setattr(avisos, "_ultimo_sonido", {})
	return valores


@pytest.fixture
def ventana(app_wx, tmp_path, monkeypatch):
	from app.ui.ventana_principal import VentanaPrincipal

	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	almacen = Almacen(tmp_path / "prueba.db")
	proveedores = {"claude": ProveedorClaude(orden_base=[sys.executable, str(FAKE)])}
	marco = VentanaPrincipal(config, None, almacen=almacen, proveedores=proveedores)
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()
		procesar_eventos()


def _mensajes(ventana, cantidad: int = 3) -> None:
	ventana.mostrar_mensajes([MensajeVista("asistente" if i % 2 else "usuario", f"Mensaje número {i}", 0.0)
		for i in range(1, cantidad + 1)])


# ── Voz y braille (#229) ─────────────────────────────────────────────────────

def test_con_voz_activa_se_habla(config_voz):
	voz.configurar(activada=True, braille=True, braille_sin_voz=True)
	voz.silenciar(False)
	assert voz.destino() == "voz"


def test_voz_silenciada_sigue_llegando_a_braille(config_voz):
	voz.configurar(activada=True, braille=True, braille_sin_voz=True)
	voz.silenciar(True)
	assert voz.destino() == "braille"
	voz.configurar(braille_sin_voz=False)
	assert voz.destino() == ""
	voz.silenciar(False)


def test_canal_apagado_va_a_braille_y_sin_braille_a_ningun_sitio(config_voz):
	voz.configurar(navegacion=False, braille=True, braille_sin_voz=True)
	assert voz.destino(voz.NAVEGACION) == "braille"
	voz.configurar(braille=False)
	assert voz.destino(voz.NAVEGACION) == ""
	assert voz.destino(voz.AVISOS, solo_braille=True) == ""


def test_solo_braille_no_habla(config_voz):
	voz.configurar(activada=True, braille=True)
	voz.silenciar(False)
	assert voz.destino(solo_braille=True) == "braille"


def test_braille_solo_no_llama_a_speak(config_voz, monkeypatch):
	llamadas = []

	class Salida:
		def speak(self, texto, interrupt=False):
			llamadas.append(("voz", texto))

		def braille(self, texto):
			llamadas.append(("braille", texto))

	monkeypatch.setattr(voz, "_obtener_salida", lambda: Salida())
	voz.configurar(braille=True)
	voz._emitir("Hola", True, solo_braille=True)
	voz._emitir("Adiós", True)
	assert llamadas == [("braille", "Hola"), ("voz", "Adiós"), ("braille", "Adiós")]


def test_anunciar_en_silencio_va_a_braille_e_historial(opciones, monkeypatch):
	dichos = []
	monkeypatch.setattr(voz, "hablar", lambda texto, **k: dichos.append((texto, k.get("solo_braille", False))))
	opciones["anuncios.nivel"] = "silencio"
	avisos.anunciar("Ya está actualizado")
	assert dichos == [("Ya está actualizado", True)]
	assert avisos.ultimo_aviso() == "Ya está actualizado"
	opciones["anuncios.braille_sin_voz"] = False
	avisos.anunciar("Nada")
	assert len(dichos) == 1
	assert avisos.historial()[-1][1] == "Nada"


def test_anunciar_escribe_en_la_barra_de_estado(marco, monkeypatch):
	marco.CreateStatusBar(4)
	valores = {"anuncios.barra_estado": True}
	monkeypatch.setitem(avisos._estado, "opcion", lambda clave, defecto=None: valores.get(clave, defecto))
	monkeypatch.setitem(avisos._estado, "ventana", marco)
	avisos.anunciar("Hay una versión nueva:\n2.0")
	assert marco.GetStatusBar().GetStatusText(avisos.CAMPO_AVISO) == "Hay una versión nueva: 2.0"
	valores["anuncios.barra_estado"] = False
	avisos.anunciar("Otro aviso")
	assert marco.GetStatusBar().GetStatusText(avisos.CAMPO_AVISO) == "Hay una versión nueva: 2.0"


# ── Sonidos (#250) ───────────────────────────────────────────────────────────

class _SonidoFalso:
	def __init__(self, registro, ruta):
		self.registro, self.ruta = registro, ruta

	def IsOk(self):  # noqa: N802 - API de wx
		return True

	def Play(self, _modo):  # noqa: N802 - API de wx
		self.registro.append(self.ruta.stem)


@pytest.fixture
def sonidos(opciones, monkeypatch, data_temporal):
	carpeta = data_temporal / "sounds"
	carpeta.mkdir(exist_ok=True)
	for evento, _descripcion in avisos.EVENTOS_SONIDO:
		_wav(carpeta / f"{evento}.wav")
	registro: list[str] = []
	monkeypatch.setattr(avisos, "_cargar", lambda ruta, volumen: _SonidoFalso(registro, ruta))
	return registro


def _wav(ruta: Path, muestras=(1000, -1000, 20000, -20000)) -> None:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	with wave.open(str(ruta), "wb") as archivo:
		archivo.setnchannels(1)
		archivo.setsampwidth(2)
		archivo.setframerate(8000)
		archivo.writeframes(b"".join(struct.pack("<h", m) for m in muestras))


def test_todos_los_eventos_tienen_sonido_y_opcion():
	from app.core.configuracion import VALORES_POR_DEFECTO

	for evento, descripcion in avisos.EVENTOS_SONIDO:
		assert (RAIZ / "data" / "sounds" / f"{evento}.wav").is_file(), evento
		assert evento in VALORES_POR_DEFECTO["sonidos"], evento
		assert descripcion


def test_sonar_respeta_interruptor_general_y_por_evento(sonidos, opciones):
	avisos.sonar("enviado")
	assert sonidos == ["enviado"]
	opciones["sonidos.respuesta"] = False
	avisos.sonar("respuesta")
	assert sonidos == ["enviado"]
	avisos.sonar("respuesta", forzar=True)
	assert sonidos == ["enviado", "respuesta"]
	opciones["anuncios.sonidos"] = False
	avisos.sonar("error")
	assert sonidos == ["enviado", "respuesta"]


def test_rafaga_de_herramientas_no_atruena(sonidos):
	for _vez in range(10):
		avisos.sonar("herramienta")
	assert sonidos == ["herramienta"]


def test_volumen_cero_calla(sonidos, opciones):
	opciones["sonidos.volumen"] = 0
	avisos.sonar("error")
	assert sonidos == []


def test_esquema_propio_con_respaldo(opciones, data_temporal):
	opciones["sonidos.esquema"] = "propio"
	_wav(data_temporal / "sounds" / "error.wav")
	_wav(data_temporal / "sounds" / "propios" / "respuesta.wav")
	assert avisos.ruta_sonido("respuesta").parent.name == "propios"
	assert avisos.ruta_sonido("error").parent.name == "sounds"
	assert avisos.ruta_sonido("no_existe") is None
	opciones["sonidos.esquema"] = "predeterminado"
	assert avisos.ruta_sonido("respuesta") is None


def test_volumen_escala_las_muestras(tmp_path):
	ruta = tmp_path / "a.wav"
	_wav(ruta)
	(tmp_path / "c.wav").write_bytes(avisos._datos_con_volumen(ruta, 50))
	with wave.open(str(tmp_path / "c.wav"), "rb") as archivo:
		marcos = archivo.readframes(archivo.getnframes())
	assert struct.unpack("<4h", marcos) == (500, -500, 10000, -10000)


def test_sonido_de_cada_evento_de_agente():
	assert avisos.sonido_de_evento("herramienta", {"herramienta": "Read"}) == "herramienta"
	assert avisos.sonido_de_evento("herramienta", {"herramienta": "Edit"}) == "archivo"
	assert avisos.sonido_de_evento("herramienta", {"herramienta": "Write"}) == "archivo"
	assert avisos.sonido_de_evento("archivo", {}) == "archivo"
	assert avisos.sonido_de_evento("fin_turno", {"interrumpido": True}) == "detenido"
	assert avisos.sonido_de_evento("fin_turno", {"correcto": True}) == ""
	assert avisos.sonido_de_evento("mensaje", {}) == ""


def test_sustituir_sonido_solo_dentro_del_bloque(sonidos):
	with avisos.sustituir_sonido("respuesta", "segundo_plano"):
		avisos.sonar("respuesta")
	avisos._ultimo_sonido.clear()
	avisos.sonar("respuesta")
	assert sonidos == ["segundo_plano", "respuesta"]


def test_dialogo_de_sonidos_guarda_y_reproduce(app_wx, tmp_path, sonidos, opciones):
	from app.ui.dialogos.sonidos import DialogoSonidos

	config = Configuracion(tmp_path / "settings.json")
	dialogo = DialogoSonidos(None, config)
	try:
		eventos = dialogo.eventos
		assert dialogo.lista.GetCount() == len(avisos.EVENTOS_SONIDO)
		posicion = eventos.index("herramienta")
		assert not dialogo.lista.IsChecked(posicion)  # desactivado de fábrica
		dialogo.lista.SetSelection(posicion)
		dialogo.alternar()
		dialogo.reproducir()
		assert sonidos == ["herramienta"]
		menu = dialogo.lista.crear_menu_contextual()
		assert [i.GetItemLabelText() for i in menu.GetMenuItems()][0] == "Reproducir"
		menu.Destroy()
		dialogo.aceptar()
		assert config.obtener("sonidos.herramienta") is True
	finally:
		dialogo.Destroy()


# ── Menús contextuales (#252) ─────────────────────────────────────────────────

CONSTRUCTORES = {"ListaAccesible", "CheckListBoxAccesible", "ListCtrl", "ListBox", "CheckListBox", "TreeCtrl"}
#: Listas que no tienen menú todavía, con el motivo. Borra la entrada al añadirlo.
SIN_MENU: dict[tuple[str, str], str] = {}


def _listas_sin_menu() -> list[str]:
	faltan = []
	for archivo in sorted((RAIZ / "app" / "ui").rglob("*.py")):
		if "accesibilidad" in archivo.parts:
			continue
		relativo = archivo.relative_to(RAIZ).as_posix()
		arbol = ast.parse(archivo.read_text(encoding="utf-8"))
		ambitos = [n for n in arbol.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]
		for ambito in ambitos:
			creadas, con_menu = {}, set()
			for nodo in ast.walk(ambito):
				if isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Call):
					funcion = nodo.value.func
					nombre = funcion.attr if isinstance(funcion, ast.Attribute) else getattr(funcion, "id", "")
					if nombre in CONSTRUCTORES:
						creadas[ast.unparse(nodo.targets[0])] = nodo.lineno
				if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") == "vincular_menu_contextual" and nodo.args:
					con_menu.add(ast.unparse(nodo.args[0]))
			for destino, linea in creadas.items():
				if destino not in con_menu and (relativo, ambito.name) not in SIN_MENU:
					faltan.append(f"{relativo}:{linea} {ambito.name}.{destino}")
	return faltan


def test_todas_las_listas_tienen_menu_contextual():
	assert _listas_sin_menu() == []


def test_menu_contextual_de_resultados_de_busqueda(app_wx):
	from app.ui.dialogos.buscar import DialogoBuscarTodo

	dialogo = DialogoBuscarTodo(None, lambda consulta, cancelada: [])
	try:
		assert dialogo.lista.crear_menu_contextual() is None  # sin resultados, sin menú
		dialogo.consulta = "hola"
		dialogo.mostrar_resultados([("ctrlia:1", "Web", MensajeVista("asistente", "hola mundo", 0.0))])
		dialogo.lista.enfocar_fila(0, anunciar=False)
		menu = dialogo.lista.crear_menu_contextual()
		etiquetas = [i.GetItemLabelText() for i in menu.GetMenuItems()]
		assert etiquetas == ["Ir al mensaje", "Copiar el mensaje", "Copiar el título de la sesión"]
		menu.Destroy()
	finally:
		dialogo.Destroy()


def test_menu_contextual_del_selector_y_de_columnas(app_wx):
	from app.ui.dialogos.columnas import DialogoColumnas
	from app.ui.dialogos.selector import DialogoSelector

	selector = DialogoSelector(None, "Comandos", "&Comandos:", [["/compact", "Resume"]], ["Comando", "Descripción"])
	columnas = DialogoColumnas(None, [("a", "Uno"), ("b", "Dos")], ["a"])
	try:
		menu = selector.lista.crear_menu_contextual()
		assert [i.GetItemLabelText() for i in menu.GetMenuItems()][0] == "Elegir"
		menu.Destroy()
		menu = columnas.lista.crear_menu_contextual()
		assert "Subir (Alt+Arriba)" in [i.GetItemLabelText() for i in menu.GetMenuItems()]
		menu.Destroy()
		columnas.lista.SetSelection(1)
		columnas.alternar()
		assert columnas.marcadas() == ["a", "b"]
	finally:
		selector.Destroy()
		columnas.Destroy()


# ── Campos recortados (#178) ─────────────────────────────────────────────────

def test_campo_de_una_linea_se_ve_desde_el_principio(marco):
	from app.ui.accesibilidad.dialogos import enfocar

	campo = wx.TextCtrl(marco, value="Arreglar la prueba de subida inestable (continuación)", size=(80, -1))
	enfocar(campo)
	assert campo.GetInsertionPoint() == 0
	assert campo.GetSelection() == (0, campo.GetLastPosition())


# ── Tema (#177) ──────────────────────────────────────────────────────────────

def test_decidir_tema_oscuro():
	assert visual.decidir_tema_oscuro("oscuro", False, False)
	assert not visual.decidir_tema_oscuro("claro", False, True)
	assert visual.decidir_tema_oscuro("sistema", False, True)
	assert not visual.decidir_tema_oscuro("sistema", False, False)
	# Con alto contraste mandan siempre los colores de Windows.
	assert not visual.decidir_tema_oscuro("oscuro", True, True)


def test_la_pagina_html_sigue_al_tema_de_la_aplicacion(monkeypatch):
	pytest.importorskip("markdown")
	from app.ui import visor_ayuda

	monkeypatch.setitem(visual._tema, "oscuro", False)
	assert "#1e1e1e" not in visor_ayuda.markdown_a_html("# Hola", "Hola")
	monkeypatch.setitem(visual._tema, "oscuro", True)
	assert "#1e1e1e" in visor_ayuda.markdown_a_html("# Hola", "Hola")


# ── Ventana principal: zonas, panel de lectura y reparto (#241 #242 #243 #181) ──

def test_barra_de_estado_con_el_ultimo_aviso(ventana):
	assert ventana.GetStatusBar().GetFieldsCount() == 4


def test_ocultar_la_lista_de_sesiones(ventana):
	assert ventana.zonas() == [ventana.lista_sesiones, ventana.lista_mensajes, ventana.respuesta]
	ventana.accion_mostrar_sesiones()
	assert not ventana.lista_sesiones.IsShown()
	assert ventana.opcion("ventana.mostrar_sesiones") is False
	assert ventana.zonas() == [ventana.lista_mensajes, ventana.respuesta]
	ventana.ir_a_sesiones()
	assert ventana.lista_sesiones.IsShown()
	assert ventana.opcion("ventana.mostrar_sesiones") is True


def test_panel_de_lectura_muestra_el_mensaje_enfocado(ventana):
	assert not ventana.panel_lectura.IsShown()
	_mensajes(ventana, 3)
	ventana.accion_panel_lectura()
	assert ventana.panel_lectura.IsShown()
	assert ventana.zonas() == [ventana.lista_sesiones, ventana.lista_mensajes, ventana.panel_lectura, ventana.respuesta]
	assert ventana.panel_lectura.GetValue() == "Mensaje número 3"
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	ventana.actualizar_panel_lectura()
	assert ventana.panel_lectura.GetValue() == "Mensaje número 1"
	assert "mensaje 1 de 3" in ventana.etiqueta_lectura.GetLabel()
	ventana.accion_panel_lectura()
	assert not ventana.panel_lectura.IsShown()


def test_f6_salta_las_zonas_ocultas(ventana, monkeypatch):
	visitas = []
	monkeypatch.setattr(ventana, "ir_a_mensajes", lambda: visitas.append("mensajes"))
	monkeypatch.setattr(ventana, "ir_a_respuesta", lambda: visitas.append("respuesta"))
	monkeypatch.setattr(ventana, "ir_a_sesiones", lambda: visitas.append("sesiones"))
	ventana.poner_opcion("ventana.mostrar_sesiones", False)
	ventana.aplicar_disposicion()
	monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: ventana.respuesta))
	evento = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
	evento.SetKeyCode(wx.WXK_F6)
	ventana._al_tecla_global(evento)
	assert visitas == ["mensajes"]


@pytest.mark.parametrize("reparto", ["equilibrado", "mensajes"])
def test_en_ventana_pequena_los_mensajes_no_son_mas_bajos_que_la_respuesta(ventana, reparto):
	ventana.poner_opcion("ventana.reparto", reparto)
	ventana.aplicar_disposicion()
	ventana.SetSize(ventana.FromDIP(wx.Size(640, 480)))
	ventana.panel.Layout()
	mensajes = ventana.lista_mensajes.GetSize().height
	respuesta = ventana.respuesta.GetSize().height
	assert mensajes >= respuesta
	assert respuesta >= ventana.respuesta.GetCharHeight() * 3


def test_resaltar_la_zona_con_el_foco(ventana):
	ventana._resaltar_zona(ventana.lista_mensajes)
	assert ventana.zona_resaltada is ventana.lista_mensajes
	assert ventana.etiqueta_mensajes.GetFont().GetWeight() == wx.FONTWEIGHT_BOLD
	assert ventana.etiqueta_sesiones.GetFont().GetWeight() != wx.FONTWEIGHT_BOLD
	ventana.poner_opcion("ventana.resaltar_foco", False)
	ventana._resaltar_zona(ventana.lista_mensajes)
	assert ventana.zona_resaltada is None
	assert ventana.etiqueta_mensajes.GetFont().GetWeight() != wx.FONTWEIGHT_BOLD


def test_menu_distribucion_refleja_las_opciones(ventana):
	elementos = ventana._menu_disposicion
	assert elementos["sesiones"].IsChecked()
	assert not elementos["lectura"].IsChecked()
	assert elementos["equilibrado"].IsChecked()
	ventana.accion_reparto("mensajes")
	assert elementos["mensajes"].IsChecked()


def test_sonidos_de_herramientas_y_de_otra_sesion(ventana, monkeypatch):
	sonados = []
	monkeypatch.setattr(avisos, "sonar", lambda nombre, forzar=False: sonados.append(avisos._sustituciones.get(nombre, nombre)))
	ventana.sesion_actual = "ctrlia:abierta"
	ventana._al_evento("ctrlia:otra", Evento(TipoEvento.HERRAMIENTA, "Leer", {"herramienta": "Read"}))
	ventana._al_evento("ctrlia:otra", Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	ventana._al_evento("ctrlia:abierta", Evento(TipoEvento.FIN_TURNO, "", {"interrumpido": True}))
	assert sonados == ["herramienta", "segundo_plano", "detenido"]


def test_alternar_sonidos_cambia_la_opcion_y_el_menu(ventana):
	antes = bool(ventana.opcion("anuncios.sonidos"))
	ventana.accion_alternar_sonidos()
	assert ventana.opcion("anuncios.sonidos") is (not antes)
	assert ventana._elementos_menu["sonidos_activos"].IsChecked() is (not antes)
