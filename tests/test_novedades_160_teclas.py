"""Ctrl+IA 1.6.0, paquete TECLAS: atajos de teclado propios y «Buscar una acción».

- El mapa de teclas sin wx (``app/core/teclas.py``): normalizar, rechazar con
  motivo, mover una tecla, conservar desconocidos, JSON roto, escritura atómica.
- Los diálogos «Atajos de teclado», «Cambiar tecla» y «Buscar una acción» con la
  ``wx.App`` única del conftest y sin mostrar ventanas.
- La ventana principal aplica ``teclas.json`` al arrancar y al cambiar una tecla.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.core import teclas as t
from app.core.teclas import Accion, Comando, MapaTeclas, buscar_acciones, motivo_rechazo, normalizar, para_leer


def _sin_altgr(_tecla: str) -> str:
	return ""


def _comandos() -> list[Comando]:
	return [
		Comando("nueva", "Nueva sesión", "Archivo", "Ctrl+N"),
		Comando("exportar", "Exportar la sesión", "Archivo", "Ctrl+E"),
		Comando("clonar", "Clonar un repositorio de GitHub", "Archivo", ""),
		Comando("reenviar", "Enviar la respuesta a otra sesión", "Sesión", "Ctrl+Alt+R"),
		Comando("enviar", "Enviar", "Sesión", "Ctrl+Enter", fija=t.FIJAS["Ctrl+Enter"]),
	]


def _mapa(tmp_path: Path, **opciones) -> MapaTeclas:
	opciones.setdefault("altgr", _sin_altgr)
	return MapaTeclas(_comandos(), ruta=tmp_path / "teclas.json", **opciones)


# ── Nombres de las teclas ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("texto", "esperado"), [
	("ctrl+shift+q", "Ctrl+Shift+Q"),
	("Mayús+Control+n", "Ctrl+Shift+N"),
	("Mayúsculas+Alt+Ctrl+z", "Ctrl+Alt+Shift+Z"),
	("Control+RePág", "Ctrl+PageUp"),
	("ctrl+av pág", "Ctrl+PageDown"),
	("Alt+Intro", "Alt+Enter"),
	("Ctrl+Supr", "Ctrl+Delete"),
	("Ctrl + /", "Ctrl+/"),
	("ctrl+coma", "Ctrl+,"),
	("Ctrl+Alt+Shift+Down", "Ctrl+Alt+Shift+Down"),
	("f11", "F11"),
	("Bloq Mayús", "CapsLock"),
])
def test_normalizar(texto, esperado):
	assert normalizar(texto) == esperado


@pytest.mark.parametrize("texto", ["", "Ctrl+", "Hiper+Q", "Ctrl+Mayús", "Ctrl+ñ", "Ctrl+F25", "Ctrl++"])
def test_normalizar_rechaza_lo_que_no_es_tecla(texto):
	with pytest.raises(ValueError):
		normalizar(texto)


def test_para_leer_en_espanol():
	assert para_leer("Ctrl+Alt+Shift+N") == "Ctrl+Alt+Mayús+N"
	assert para_leer("Alt+Enter") == "Alt+Intro"
	assert para_leer("Ctrl+PageUp") == "Ctrl+RePág"
	assert para_leer("") == ""


def test_etiquetas_de_menu():
	assert t.tecla_de_etiqueta("&Nueva sesión...\tCtrl+N") == "Ctrl+N"
	assert t.tecla_de_etiqueta("Clonar un repositorio de &GitHub...") == ""
	assert t.etiqueta_con_tecla("&Nueva sesión...\tCtrl+N", "Ctrl+Alt+Shift+N") == "&Nueva sesión...\tCtrl+Alt+Shift+N"
	assert t.etiqueta_con_tecla("&Nueva sesión...\tCtrl+N", "") == "&Nueva sesión..."
	assert t.nombre_de_etiqueta("Ad&juntar archivos...\tCtrl+Shift+F") == "Adjuntar archivos"
	assert t.nombre_de_etiqueta("Copiar && pegar") == "Copiar & pegar"


# ── Teclas que se rechazan ───────────────────────────────────────────────────

@pytest.mark.parametrize(("tecla", "fragmento"), [
	("Enter", "no se puede usar"),
	("Ctrl+Enter", "envía"),
	("Ctrl+Shift+Enter", "envía ahora"),
	("Ctrl+Alt+Enter", "turno"),
	("Delete", "no se puede usar"),
	("Shift+Delete", "para siempre"),
	("Ctrl+C", "copia"),
	("F6", "zona"),
	("Shift+F6", "zona"),
	("F10", "barra de menús"),
	("Shift+F10", "menú contextual"),
	("Alt+F4", "cierra la ventana"),
	("Escape", "no se puede usar"),
	("Tab", "no se puede usar"),
	("Ctrl+H", "control enfocado"),
	("Ctrl+3", "columna"),
	("Insert", "lectores de pantalla"),
	("Ctrl+Insert", "lectores de pantalla"),
	("CapsLock", "Bloq Mayús"),
	("Up", "mueve"),
	("Ctrl+Home", "mueve"),
	("Shift+PageDown", "mueve"),
	("Ctrl+Shift+End", "mueve"),
	("Ctrl+Space", "espacio"),
	("Ctrl+Backspace", "borra"),
	("Q", "Una tecla sola"),
	("Shift+Q", "Mayús con esa tecla"),
	("Alt+Q", "Alt con una letra"),
	("Alt+5", "Alt con una letra"),
	("Alt+Shift+Q", "Alt con una letra"),
	("Ctrl+X", "edita texto"),
	("Ctrl+V", "edita texto"),
	("Ctrl+A", "edita texto"),
	("Ctrl+Z", "edita texto"),
	("Ctrl+Y", "edita texto"),
])
def test_teclas_rechazadas_con_motivo(tecla, fragmento):
	motivo = motivo_rechazo(tecla, altgr=_sin_altgr)
	assert fragmento in motivo, motivo


@pytest.mark.parametrize("tecla", ["F11", "Shift+F11", "Ctrl+Shift+F10", "Ctrl+Shift+Q", "Ctrl+Q", "Alt+F11", "Alt+Enter",
	"Ctrl+Alt+N", "Ctrl+Alt+Shift+N", "Ctrl+F2", "Ctrl+/"])
def test_teclas_que_valen(tecla):
	assert motivo_rechazo(tecla, altgr=_sin_altgr) == ""


def test_altgr_con_la_distribucion_activa():
	assert "€" in motivo_rechazo("Ctrl+Alt+E", altgr=lambda tecla: "€")
	assert motivo_rechazo("Ctrl+Alt+E", altgr=lambda tecla: "") == ""
	# Sin poder comprobarlo: por prudencia no, salvo que sea una tecla de fábrica del programa.
	assert "prudencia" in motivo_rechazo("Ctrl+Alt+K", altgr=lambda tecla: None)
	assert motivo_rechazo("Ctrl+Alt+N", {"Ctrl+Alt+N"}, altgr=lambda tecla: None) == ""
	# Las teclas F nunca escriben con AltGr: no se pregunta.
	assert motivo_rechazo("Ctrl+Alt+F5", altgr=lambda tecla: pytest.fail("no debía preguntar")) == ""


@pytest.mark.skipif(sys.platform != "win32", reason="La API de teclado es de Windows")
def test_caracter_altgr_responde_en_windows():
	assert t.caracter_altgr("Ctrl+Alt+F5") == ""
	resultado = t.caracter_altgr("Ctrl+Alt+E")
	assert resultado is None or isinstance(resultado, str)


def test_comandos_de_menus_reales():
	from app.ui.ventana_principal import MENUS

	comandos = {c.clave: c for c in t.comandos_de_menus(MENUS)}
	# Ninguna tecla de fábrica se pierde al leerla.
	for _titulo, elementos in MENUS:
		for elemento in elementos:
			if elemento[0] not in ("-", "submenu") and "\t" in elemento[1]:
				assert comandos[elemento[0]].tecla_fabrica, elemento
	assert {c.clave for c in comandos.values() if c.fija} == {"enviar", "enviar_ahora", "anadir_turno", "ayuda_control"}
	assert comandos["nueva"].tecla_fabrica == "Ctrl+N"
	assert comandos["nueva"].menu == "Archivo"
	assert comandos["atajos_teclado"].menu == "Herramientas"
	assert comandos["buscar_accion"].tecla_fabrica == "Ctrl+Shift+F10"
	# Los Ctrl+Alt de fábrica valen siempre, aunque no se pueda comprobar AltGr.
	de_fabrica = {c.tecla_fabrica for c in comandos.values()}
	for comando in comandos.values():
		if comando.tecla_fabrica and not comando.fija and comando.tecla_fabrica.startswith("Ctrl+Alt+"):
			assert motivo_rechazo(comando.tecla_fabrica, de_fabrica, altgr=lambda tecla: None) == "", comando


# ── El mapa ──────────────────────────────────────────────────────────────────

def test_sin_archivo_todo_de_fabrica(tmp_path):
	mapa = _mapa(tmp_path)
	assert mapa.tecla("nueva") == "Ctrl+N"
	assert mapa.tecla("clonar") == ""
	assert mapa.personalizables() == ["nueva", "exportar", "clonar", "reenviar"]
	assert not mapa.es_personalizable("enviar")
	assert mapa.tecla("enviar") == "Ctrl+Enter"
	assert not mapa.aviso and not mapa.cambiadas()


def test_asignar_mueve_la_tecla_y_dice_quien_la_perdio(tmp_path):
	mapa = _mapa(tmp_path)
	assert mapa.asignar("clonar", "Ctrl+N") == "nueva"
	assert mapa.tecla("clonar") == "Ctrl+N"
	assert mapa.tecla("nueva") == ""
	assert mapa.comando_de("ctrl+n") == "clonar"
	assert mapa.asignar("exportar", "control+mayús+e") is None
	assert mapa.tecla("exportar") == "Ctrl+Shift+E"
	assert mapa.cambiadas() == ["nueva", "exportar", "clonar"]


def test_asignar_rechaza_con_el_motivo(tmp_path):
	mapa = _mapa(tmp_path, ocupadas={"F7": "Mostrar la lista de sesiones"})
	with pytest.raises(ValueError, match="edita texto"):
		mapa.asignar("nueva", "Ctrl+V")
	with pytest.raises(ValueError, match="Mostrar la lista de sesiones"):
		mapa.asignar("nueva", "F7")
	with pytest.raises(ValueError, match="no se puede usar"):
		mapa.asignar("nueva", "Ctrl+Enter")  # la tecla fija de Enviar
	with pytest.raises(ValueError, match="no se puede cambiar"):
		mapa.asignar("enviar", "F11")
	assert mapa.tecla("nueva") == "Ctrl+N"


def test_su_propia_tecla_de_fabrica_siempre_vale(tmp_path):
	comandos = [Comando("ir", "Ir a la lista", "Ver", "Alt+1"), Comando("otra", "Otra", "Ver", "")]
	mapa = MapaTeclas(comandos, ruta=tmp_path / "teclas.json", altgr=_sin_altgr)
	mapa.quitar("ir")
	assert mapa.tecla("ir") == ""
	assert mapa.asignar("ir", "Alt+1") is None
	assert not mapa.cambiada("ir")
	with pytest.raises(ValueError, match="Alt con una letra"):
		mapa.asignar("otra", "Alt+1")


def test_guarda_solo_lo_cambiado(tmp_path):
	mapa = _mapa(tmp_path)
	mapa.asignar("clonar", "F11")
	mapa.quitar("exportar")
	mapa.asignar("nueva", "Ctrl+N")  # la de fábrica: no es un cambio
	assert mapa.guardar()
	datos = json.loads((tmp_path / "teclas.json").read_text(encoding="utf-8"))
	assert datos == {"version": 1, "teclas": {"clonar": "F11", "exportar": ""}}
	otra = _mapa(tmp_path)
	assert otra.tecla("clonar") == "F11"
	assert otra.tecla("exportar") == ""
	assert otra.tecla("nueva") == "Ctrl+N"


def test_restablecer_y_restablecer_todo(tmp_path):
	mapa = _mapa(tmp_path)
	mapa.asignar("clonar", "Ctrl+N")
	assert mapa.restablecer("nueva") == "clonar"
	assert mapa.tecla("nueva") == "Ctrl+N"
	assert mapa.tecla("clonar") == ""
	mapa.asignar("exportar", "F11")
	mapa.restablecer_todo()
	assert not mapa.cambiadas()


def test_conserva_los_identificadores_desconocidos(tmp_path):
	ruta = tmp_path / "teclas.json"
	ruta.write_text(json.dumps({"version": 1, "teclas": {"futura": "Ctrl+Shift+J", "enviar": "F2", "nueva": "F11"}}),
		encoding="utf-8")
	mapa = _mapa(tmp_path)
	assert mapa.tecla("nueva") == "F11"
	assert mapa.tecla("enviar") == "Ctrl+Enter"
	mapa.asignar("exportar", "F12")
	mapa.guardar()
	teclas = json.loads(ruta.read_text(encoding="utf-8"))["teclas"]
	assert teclas == {"futura": "Ctrl+Shift+J", "enviar": "F2", "nueva": "F11", "exportar": "F12"}


def test_dos_ordenes_nunca_con_la_misma_tecla_al_leer(tmp_path):
	ruta = tmp_path / "teclas.json"
	# La tuya gana a la de fábrica; entre dos tuyas, la primera.
	ruta.write_text(json.dumps({"teclas": {"clonar": "Ctrl+E", "nueva": "F11", "reenviar": "F11"}}), encoding="utf-8")
	mapa = _mapa(tmp_path)
	assert mapa.tecla("clonar") == "Ctrl+E"
	assert mapa.tecla("exportar") == ""
	assert mapa.tecla("nueva") == "F11"
	assert mapa.tecla("reenviar") == "Ctrl+Alt+R"
	assert "1 de tus atajos" in mapa.aviso


def test_teclas_no_validas_en_el_archivo_se_ignoran(tmp_path):
	ruta = tmp_path / "teclas.json"
	ruta.write_text(json.dumps({"teclas": {"nueva": "Ctrl+V", "exportar": 7, "clonar": "Hiper+Q", "reenviar": ""}}),
		encoding="utf-8")
	mapa = _mapa(tmp_path)
	assert mapa.tecla("nueva") == "Ctrl+N"
	assert mapa.tecla("exportar") == "Ctrl+E"
	assert mapa.tecla("reenviar") == ""
	assert "3 de tus atajos" in mapa.aviso


@pytest.mark.parametrize("contenido", ["{roto", "[1, 2]", '{"teclas": []}'])
def test_json_roto_se_aparta_y_se_usan_las_de_fabrica(tmp_path, contenido):
	ruta = tmp_path / "teclas.json"
	ruta.write_text(contenido, encoding="utf-8")
	mapa = _mapa(tmp_path)
	assert mapa.tecla("nueva") == "Ctrl+N"
	apartados = list(tmp_path.glob("teclas.json.roto-*"))
	assert len(apartados) == 1 and apartados[0].read_text(encoding="utf-8") == contenido
	assert not ruta.exists()
	assert apartados[0].name in mapa.aviso
	# Se puede seguir guardando.
	mapa.asignar("clonar", "F11")
	assert mapa.guardar() and json.loads(ruta.read_text(encoding="utf-8"))["teclas"] == {"clonar": "F11"}


def test_escritura_atomica(tmp_path, monkeypatch):
	mapa = _mapa(tmp_path)
	mapa.asignar("clonar", "F11")
	assert mapa.guardar()
	ruta = tmp_path / "teclas.json"
	antes = ruta.read_text(encoding="utf-8")
	assert not list(tmp_path.glob("*.tmp"))

	def falla(*_args, **_kwargs):
		raise OSError("disco lleno")

	monkeypatch.setattr(t.os, "replace", falla)
	mapa.asignar("clonar", "F12")
	assert not mapa.guardar()
	assert "disco lleno" in mapa.aviso
	# El archivo anterior sigue entero y no queda ningún temporal.
	assert ruta.read_text(encoding="utf-8") == antes
	assert not list(tmp_path.glob("*.tmp"))


def test_reemplazo_reintenta_si_el_archivo_esta_bloqueado(tmp_path, monkeypatch):
	intentos = []
	original = os.replace

	def bloqueado_una_vez(origen, destino):
		intentos.append(1)
		if len(intentos) == 1:
			raise PermissionError("en uso")
		original(origen, destino)

	monkeypatch.setattr(t.os, "replace", bloqueado_una_vez)
	t.escribir_json_atomico(tmp_path / "a.json", {"x": 1})
	assert len(intentos) == 2 and json.loads((tmp_path / "a.json").read_text(encoding="utf-8")) == {"x": 1}


def test_recientes(tmp_path):
	mapa = _mapa(tmp_path)
	for numero in range(15):
		mapa.anotar_reciente(f"a{numero}")
	mapa.anotar_reciente("a3")
	assert mapa.recientes[0] == "a3" and len(mapa.recientes) == t.MAX_RECIENTES
	assert mapa.recientes.count("a3") == 1
	mapa.guardar()
	assert _mapa(tmp_path).recientes == mapa.recientes


def test_lineas_cambiadas(tmp_path):
	mapa = _mapa(tmp_path)
	mapa.asignar("nueva", "Ctrl+Alt+Shift+N")
	mapa.quitar("exportar")
	mapa.asignar("clonar", "F11")
	assert mapa.lineas_cambiadas() == [
		"Nueva sesión: Ctrl+Alt+Mayús+N, en vez de Ctrl+N",
		"Exportar la sesión: sin tecla, en vez de Ctrl+E",
		"Clonar un repositorio de GitHub: F11 (de fábrica no tenía)",
	]


# ── Buscar una acción (sin wx) ───────────────────────────────────────────────

def _acciones() -> list[Accion]:
	return [
		Accion("nueva", "Nueva sesión", "Ctrl+N", "Archivo", "Empieza una conversación nueva", orden=0),
		Accion("comando", "Insertar un comando o habilidad", "Ctrl+/", "Sesión", "Elige un comando de barra", orden=1),
		Accion("exportar", "Exportar la sesión", "Ctrl+E", "Archivo", "Guarda la conversación en Markdown", orden=2),
		Accion("columnas", "Columnas de la lista", "", "Ver", "Elige qué columnas tiene", orden=3),
	]


def test_buscar_por_palabras_en_cualquier_orden_y_sin_tildes():
	assert [a.id for a in buscar_acciones(_acciones(), "SESION nueva")] == ["nueva"]
	assert [a.id for a in buscar_acciones(_acciones(), "sesión")][:2] == ["nueva", "exportar"]


def test_buscar_por_la_tecla_y_por_la_ayuda():
	assert [a.id for a in buscar_acciones(_acciones(), "ctrl+/")] == ["comando"]
	assert [a.id for a in buscar_acciones(_acciones(), "control+e")] == ["exportar"]
	assert [a.id for a in buscar_acciones(_acciones(), "markdown")] == ["exportar"]


def test_buscar_las_recientes_primero():
	assert [a.id for a in buscar_acciones(_acciones(), "", ["columnas", "exportar"])] == ["columnas", "exportar", "nueva", "comando"]
	# Con texto, primero las que lo tienen en el nombre.
	assert [a.id for a in buscar_acciones(_acciones(), "sesion", ["comando"])] == ["nueva", "exportar", "comando"]


# ── Diálogos ─────────────────────────────────────────────────────────────────

wx = pytest.importorskip("wx")

from tests.ayudas_wx import esperar_hasta, procesar_eventos, simular_tecla  # noqa: E402


@pytest.fixture
def anuncios(monkeypatch):
	from app.ui import avisos

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	return dichos


@pytest.fixture
def foco(monkeypatch):
	"""Simula dónde está el foco sin mostrar ventanas."""
	estado = {"control": None}
	monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: estado["control"]))

	def poner(control):
		estado["control"] = control

	return poner


def test_captura_dice_lo_que_pasaria(app_wx, tmp_path, anuncios, foco, monkeypatch):
	from app.ui.dialogos.atajos import DialogoCapturaTecla

	mapa = _mapa(tmp_path, ocupadas={"F7": "Mostrar la lista de sesiones"})
	dialogo = DialogoCapturaTecla(None, mapa, "clonar")
	try:
		assert "Clonar un repositorio de GitHub" in dialogo.GetTitle()
		# Un cuadro normal, no de solo lectura (wx dejaría fuera del tabulador uno de solo lectura).
		assert dialogo.cuadro.GetHelpText() and dialogo.cuadro.IsEditable()
		assert "Nadie la tiene" in dialogo.capturar("F11")
		assert dialogo.cuadro.GetValue() == "F11"
		assert "La tiene «Nueva sesión»; si la asignas, se le quitará" in dialogo.capturar("Ctrl+N")
		assert "edita texto" in dialogo.capturar("Ctrl+V")
		assert not dialogo.asignar()
		assert "Mostrar la lista de sesiones" in dialogo.capturar("F7")
		# Con el foco en el cuadro, las teclas se toman: Ctrl+Mayús+Q se captura y no se escribe nada.
		foco(dialogo.cuadro)
		resultado = simular_tecla(dialogo, "q", ctrl=True, mayus=True)
		assert resultado.gestionado and not resultado.saltado
		assert dialogo.tecla == "Ctrl+Shift+Q" and dialogo.cuadro.GetValue() == "Ctrl+Mayús+Q"
		assert anuncios[-1].startswith("Ctrl+Mayús+Q. Nadie la tiene")
		# Tab y Alt+letra siguen su camino; Escape (cerrar) y Ctrl+H (ayuda) los atiende el cuadro, sin capturarlos.
		for tecla, mods in ((wx.WXK_TAB, {}), ("a", {"alt": True})):
			assert simular_tecla(dialogo, tecla, **mods).saltado
		cerrados = []
		monkeypatch.setattr(dialogo, "Close", lambda *a: cerrados.append(1))
		from app.ui.accesibilidad import dialogos as modulo_dialogos

		ayudas = []
		monkeypatch.setattr(modulo_dialogos, "describir_control_enfocado", lambda ventana: ayudas.append(ventana))
		simular_tecla(dialogo, wx.WXK_ESCAPE)
		simular_tecla(dialogo, "h", ctrl=True)
		assert cerrados and ayudas
		assert dialogo.tecla == "Ctrl+Shift+Q"
		# Pegar no deja texto suelto en el cuadro.
		dialogo.cuadro.SetValue("basura")
		assert dialogo.cuadro.GetValue() == "Ctrl+Mayús+Q"
		dialogo.probar_otra()
		assert dialogo.tecla == "" and dialogo.cuadro.GetValue() == ""
		simular_tecla(dialogo, wx.WXK_F11)
		assert dialogo.tecla == "F11"
		# Intro asigna.
		simular_tecla(dialogo, wx.WXK_RETURN)
		assert dialogo.GetReturnCode() == wx.ID_OK
	finally:
		dialogo.Destroy()


def test_captura_sin_letras_alt_repetidas(app_wx, tmp_path):
	import re

	from app.ui.dialogos.atajos import DialogoAtajos, DialogoCapturaTecla

	mapa = _mapa(tmp_path)
	for dialogo in (DialogoCapturaTecla(None, mapa, "nueva"), DialogoAtajos(None, mapa, lambda: None)):
		try:
			letras = []
			pendientes = list(dialogo.GetChildren())
			while pendientes:
				control = pendientes.pop()
				pendientes.extend(control.GetChildren())
				if isinstance(control, wx.StaticText | wx.Button):
					coincidencia = re.search(r"&(\w)", control.GetLabel())
					if coincidencia:
						letras.append(coincidencia.group(1).lower())
				if isinstance(control, wx.TextCtrl | wx.ListCtrl | wx.Button):
					assert control.GetHelpText(), control
			assert len(letras) == len(set(letras)), letras
		finally:
			dialogo.Destroy()


def test_dialogo_atajos_cambia_quita_y_restablece(app_wx, tmp_path, anuncios, foco, monkeypatch):
	from app.ui.dialogos import atajos
	from app.ui.dialogos.atajos import DialogoAtajos

	aplicados = []
	mapa = _mapa(tmp_path)
	dialogo = DialogoAtajos(None, mapa, lambda: aplicados.append(1))
	try:
		assert dialogo.lista.GetItemCount() == 4  # Enviar (tecla fija) no sale
		assert dialogo.lista.valores_fila(0) == ["Nueva sesión", "Ctrl+N", "De fábrica", "Archivo"]
		assert dialogo.lista.valores_fila(2)[1] == "Sin tecla"
		assert dialogo.lista.crear_menu_contextual() is not None
		# Filtrar por palabras, sin tildes.
		dialogo.filtro.SetValue("sesion exportar")
		assert dialogo.lista.GetItemCount() == 1 and dialogo.clave_actual() == "exportar"
		dialogo.filtro.SetValue("")
		# Cambiar (Intro en la lista): mueve la tecla y lo dice.
		dialogo.lista.enfocar_fila(2, anunciar=False)
		monkeypatch.setattr(dialogo, "pedir_tecla", lambda clave: "Ctrl+N")
		foco(dialogo.lista)
		assert simular_tecla(dialogo, wx.WXK_RETURN).gestionado
		assert mapa.tecla("clonar") == "Ctrl+N" and mapa.tecla("nueva") == ""
		assert anuncios[-1] == "Clonar un repositorio de GitHub: Ctrl+N. «Nueva sesión» se queda sin tecla."
		assert aplicados and json.loads((tmp_path / "teclas.json").read_text(encoding="utf-8"))["teclas"] == {
			"clonar": "Ctrl+N", "nueva": ""}
		assert dialogo.lista.valores_fila(2) == ["Clonar un repositorio de GitHub", "Ctrl+N", "Cambiada", "Archivo"]
		# Quitar (Supr).
		dialogo.lista.enfocar_fila(1, anunciar=False)
		assert simular_tecla(dialogo, wx.WXK_DELETE).gestionado
		assert mapa.tecla("exportar") == "" and anuncios[-1] == "Exportar la sesión: sin tecla."
		# Restablecer.
		dialogo.lista.enfocar_fila(0, anunciar=False)
		assert dialogo.restablecer()
		assert mapa.tecla("nueva") == "Ctrl+N" and mapa.tecla("clonar") == ""
		assert "la de fábrica" in anuncios[-1] and "«Clonar un repositorio de GitHub» se queda sin tecla" in anuncios[-1]
		# Restablecer todas pregunta antes.
		respuestas = [wx.ID_NO, wx.ID_YES]
		preguntas = []
		monkeypatch.setattr(atajos, "confirmar", lambda padre, texto, *a, **k: (preguntas.append(texto), respuestas.pop(0))[1])
		assert not dialogo.restablecer_todo()
		assert mapa.cambiadas() == ["exportar"]
		assert dialogo.restablecer_todo()
		assert not mapa.cambiadas() and "1 acciones" in preguntas[0]
		# Fuera de la lista, Intro sigue su camino.
		foco(dialogo.filtro)
		assert simular_tecla(dialogo, wx.WXK_RETURN).saltado
	finally:
		dialogo.Destroy()


def test_dialogo_atajos_cancelar_no_cambia_nada(app_wx, tmp_path, dialogo_modal, anuncios):
	from app.ui.dialogos.atajos import DialogoAtajos

	mapa = _mapa(tmp_path)
	dialogo = DialogoAtajos(None, mapa, lambda: None)
	try:
		dialogo_modal.responder(wx.ID_CANCEL)
		assert not dialogo.cambiar()
		assert not mapa.cambiadas() and not (tmp_path / "teclas.json").exists()

		def pulsar_f11(captura):
			captura.capturar("F11")
			return wx.ID_OK

		dialogo_modal.responder(pulsar_f11)
		assert dialogo.cambiar()
		assert mapa.tecla("nueva") == "F11"
		assert dialogo_modal.ultimo.titulo == "Cambiar tecla: Nueva sesión"
	finally:
		dialogo.Destroy()


def test_buscar_accion_con_foco_real(app_wx, anuncios, foco):
	from app.ui.dialogos.buscar_accion import DialogoBuscarAccion

	acciones = [*_acciones(), Accion("x", "Detener la copia en curso", "", "Herramientas, Copias", disponible=False, orden=9),
		Accion("h", "Mostrar las herramientas", "Ctrl+T", "Ver", marcada=True, orden=10)]
	dialogo = DialogoBuscarAccion(None, acciones, ["exportar"])
	try:
		assert dialogo.lista.GetItemCount() == 6
		assert dialogo.lista.valores_fila(0) == ["Exportar la sesión", "Ctrl+E", "Archivo"]
		assert dialogo.lista.valores_fila(4)[0] == "Detener la copia en curso (no disponible ahora)"
		assert dialogo.lista.valores_fila(5)[0] == "Mostrar las herramientas (marcada)"
		assert dialogo.lista.crear_menu_contextual() is not None
		dialogo.cuadro.SetValue("ctrl+/")
		assert dialogo.lista.GetItemCount() == 1 and dialogo.etiqueta.GetLabel() == "&Acciones (1):"
		assert dialogo.texto_recuento() == "Una acción."
		# Flecha abajo en el cuadro lleva el foco a la lista.
		foco(dialogo.cuadro)
		llevados = []
		dialogo.lista.SetFocus = lambda: llevados.append("lista")
		assert simular_tecla(dialogo, wx.WXK_DOWN).gestionado and llevados == ["lista"]
		# Una letra escrita en la lista vuelve al cuadro con ella.
		dialogo.cuadro.SetValue("export")
		foco(dialogo.lista)
		letra = wx.KeyEvent(wx.wxEVT_CHAR)
		letra.SetUnicodeKey(ord("a"))
		letra.SetEventObject(dialogo.lista)
		dialogo._al_escribir_en_lista(letra)
		assert dialogo.cuadro.GetValue() == "exporta"
		# Retroceso en la lista vuelve y borra la última.
		foco(dialogo.lista)
		assert simular_tecla(dialogo, wx.WXK_BACK).gestionado
		assert dialogo.cuadro.GetValue() == "export"
		# Una desactivada no se ejecuta: se explica.
		dialogo.cuadro.SetValue("detener copia")
		assert not dialogo.elegir() and "no está disponible ahora" in anuncios[-1]
		# Intro en la lista elige y cierra.
		dialogo.cuadro.SetValue("nueva")
		foco(dialogo.lista)
		assert simular_tecla(dialogo, wx.WXK_RETURN).gestionado
		assert dialogo.resultado is not None and dialogo.resultado.id == "nueva"
		assert dialogo.GetReturnCode() == wx.ID_OK
	finally:
		dialogo.Destroy()


def test_buscar_accion_sin_resultados(app_wx, anuncios):
	from app.ui.dialogos.buscar_accion import DialogoBuscarAccion

	dialogo = DialogoBuscarAccion(None, _acciones())
	try:
		dialogo.cuadro.SetValue("zzzz")
		assert dialogo.lista.GetItemCount() == 0
		assert not dialogo.elegir() and not dialogo.ir_a_lista()
		assert dialogo.resultado is None
	finally:
		dialogo.Destroy()


# ── La lista de teclas (F1) ──────────────────────────────────────────────────

def test_lista_de_teclas_con_tus_atajos_cambiados():
	from app.ui.visor_ayuda import con_atajos_cambiados

	texto = "# Teclas\n\nIntroducción.\n\n## Ventana\n\n| Atajo | Acción |\n"
	assert con_atajos_cambiados(texto, []) == texto
	nuevo = con_atajos_cambiados(texto, ["Nueva sesión: Ctrl+Alt+Mayús+N, en vez de Ctrl+N"])
	assert nuevo.index("## Tus atajos cambiados") < nuevo.index("## Ventana")
	assert "- Nueva sesión: Ctrl+Alt+Mayús+N, en vez de Ctrl+N" in nuevo
	assert nuevo.startswith("# Teclas\n\nIntroducción.")


# ── La ventana principal ─────────────────────────────────────────────────────

@pytest.fixture
def ventana_con_teclas(app_wx, tmp_path, monkeypatch):
	"""Crea la ventana principal (sin mostrarla) después de escribir ``teclas.json``."""
	from app.core.configuracion import Configuracion
	from app.services.agentes.claude import ProveedorClaude
	from app.services.almacen import Almacen
	from app.ui import avisos
	from app.ui.ventana_principal import VentanaPrincipal

	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	creadas = []

	def crear(teclas: dict | None = None, texto: str | None = None):
		ruta = t.ruta_por_defecto()
		if teclas is not None:
			ruta.write_text(json.dumps({"version": 1, "teclas": teclas}), encoding="utf-8")
		elif texto is not None:
			ruta.write_text(texto, encoding="utf-8")
		config = Configuracion(tmp_path / f"settings{len(creadas)}.json")
		config.establecer_varias({
			"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False,
			"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
		})
		almacen = Almacen(tmp_path / f"ventana{len(creadas)}.db")
		fake = Path(__file__).parent / "fakes" / "fake_claude.py"
		marco = VentanaPrincipal(config, None, almacen=almacen,
			proveedores={"claude": ProveedorClaude(orden_base=[sys.executable, str(fake)])})
		creadas.append(marco)
		procesar_eventos()
		return marco

	yield crear
	for marco in creadas:
		if marco:
			marco.salir()
	procesar_eventos()


def _acelerador(item) -> tuple[int, int] | None:
	acelerador = item.GetAccel()
	return None if acelerador is None else (acelerador.GetFlags(), acelerador.GetKeyCode())


def test_la_ventana_aplica_teclas_json_al_arrancar(ventana_con_teclas):
	ventana = ventana_con_teclas({"nueva": "Ctrl+Alt+Shift+K", "exportar": "", "clonar": "F4"})
	elementos = ventana._elementos_menu
	assert not ventana.IsShown()
	assert elementos["nueva"].GetItemLabel() == "&Nueva sesión...\tCtrl+Alt+Shift+K"
	assert _acelerador(elementos["nueva"]) == (wx.ACCEL_CTRL | wx.ACCEL_ALT | wx.ACCEL_SHIFT, ord("K"))
	assert elementos["exportar"].GetItemLabel() == "&Exportar la sesión..."
	assert elementos["exportar"].GetAccel() is None
	assert _acelerador(elementos["clonar"]) == (wx.ACCEL_NORMAL, wx.WXK_F4)
	# Lo no cambiado queda tal cual, también los marcables.
	assert elementos["conversacion_sesiones"].GetItemLabel().endswith("\tCtrl+Alt+Shift+R")
	assert elementos["herramientas"].IsChecked()


def test_cambiar_una_tecla_se_aplica_al_momento(ventana_con_teclas):
	ventana = ventana_con_teclas()
	mapa = ventana.mapa_teclas
	# Las teclas de los submenús están ocupadas: nadie más puede cogerlas.
	assert "Mostrar la lista de sesiones" in mapa.por_que_no("nueva", "F7")
	assert "Hacer una copia ahora" in mapa.por_que_no("nueva", "Ctrl+Alt+B")
	assert mapa.asignar("clonar", "Ctrl+N") == "nueva"
	ventana.aplicar_teclas()
	assert _acelerador(ventana._elementos_menu["clonar"]) == (wx.ACCEL_CTRL, ord("N"))
	assert ventana._elementos_menu["nueva"].GetAccel() is None
	mapa.restablecer_todo()
	ventana.aplicar_teclas()
	assert ventana._elementos_menu["nueva"].GetItemLabel() == "&Nueva sesión...\tCtrl+N"
	assert ventana._elementos_menu["clonar"].GetAccel() is None


def test_archivo_roto_al_arrancar_se_avisa(ventana_con_teclas, monkeypatch):
	from app.ui import avisos

	avisados = []
	monkeypatch.setattr(avisos, "notificar", lambda texto, *a, **k: avisados.append(texto))
	ventana = ventana_con_teclas(texto="{roto")
	assert ventana._elementos_menu["nueva"].GetItemLabel() == "&Nueva sesión...\tCtrl+N"
	esperar_hasta(lambda: bool(avisados), 4)
	assert avisados and "teclas.json.roto-" in avisados[0]


def test_acciones_para_buscar_incluye_los_submenus(ventana_con_teclas, monkeypatch):
	ventana = ventana_con_teclas({"nueva": "F4"})
	acciones, ejecutores = ventana.acciones_para_buscar()
	por_nombre = {a.nombre: a for a in acciones}
	assert "Buscar una acción" not in por_nombre
	assert por_nombre["Nueva sesión"].tecla == "F4" and por_nombre["Nueva sesión"].menu == "Archivo"
	copia = por_nombre["Hacer una copia ahora"]
	assert copia.tecla == "Ctrl+Alt+B" and copia.menu == "Herramientas, Copias de seguridad"
	assert por_nombre["Panel de lectura"].marcada is False
	assert por_nombre["Enviar"].tecla == "Ctrl+Enter"
	assert set(ejecutores) == {a.id for a in acciones}
	# Un elemento de submenú se ejecuta como al elegirlo en el menú; uno de MENUS, con _ejecutar_accion.
	llamadas = []
	monkeypatch.setattr(ventana, "accion_hacer_copia", lambda: llamadas.append("copia"))
	monkeypatch.setattr(ventana, "accion_columnas", lambda: llamadas.append("columnas"))
	ejecutores[copia.id]()
	ejecutores["columnas"]()
	assert llamadas == ["copia", "columnas"]


def test_buscar_accion_ejecuta_la_elegida_tras_cerrar(ventana_con_teclas, dialogo_modal, monkeypatch):
	ventana = ventana_con_teclas()
	llamadas = []
	monkeypatch.setattr(ventana, "accion_columnas", lambda: llamadas.append("columnas"))

	def elegir_columnas(dialogo):
		dialogo.cuadro.SetValue("columnas lista")
		assert dialogo.elegir()
		assert not llamadas  # nada se ejecuta con el cuadro abierto
		return wx.ID_OK

	dialogo_modal.responder(elegir_columnas)
	ventana.accion_buscar_accion()
	esperar_hasta(lambda: bool(llamadas), 3)
	assert llamadas == ["columnas"]
	assert ventana.mapa_teclas.recientes[0] == "columnas"
	assert json.loads(t.ruta_por_defecto().read_text(encoding="utf-8"))["recientes"] == ["columnas"]


def test_lista_de_teclas_recibe_los_cambios(ventana_con_teclas, monkeypatch):
	from app.ui import visor_ayuda

	recibido = []
	monkeypatch.setattr(visor_ayuda, "abrir_teclas", lambda padre, cambiadas=None, **k: recibido.append(cambiadas))
	ventana = ventana_con_teclas({"nueva": "Ctrl+Alt+Shift+K"})
	ventana.accion_teclas()
	assert recibido == [["Nueva sesión: Ctrl+Alt+Mayús+K, en vez de Ctrl+N"]]
