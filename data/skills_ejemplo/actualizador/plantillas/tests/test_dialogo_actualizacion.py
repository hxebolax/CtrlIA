"""Diálogo accesible de actualización (PLANTILLA del agente «actualizador»).

Crea controles wx reales (sin mostrarlos de forma modal) y recorre el flujo de
descarga en su hilo contra un servidor HTTP local, sin lanzar el script real.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

wx = pytest.importorskip("wx")
pytest.importorskip("cryptography")

from apoyo_actualizador import Servidor, cargar_firmador, publicar  # noqa: E402

from app.services import actualizador  # noqa: E402
from app.ui import dialogo_actualizacion as dialogo_mod  # noqa: E402


# La wx.App de la sesión. Esta referencia de módulo la mantiene viva hasta el final:
# sin ella, Python destruiría la aplicación recién creada al salir de la fixture.
_APLICACION_WX = None


@pytest.fixture(scope="module")
def aplicacion():
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


@pytest.fixture
def config(tmp_path):
	instalacion = tmp_path / "Mi App"
	instalacion.mkdir()
	(instalacion / "MiAplicacion.exe").write_bytes(b"EXE 1.0.0")
	return actualizador.Configuracion(
		version_actual="1.0.0", carpeta_app=instalacion, comando=[], permitir_desde_codigo=True
	)


INFO = actualizador.InfoActualizacion(
	version="1.1.0",
	archivo="MiAplicacion_v1.1.0.zip",
	url_zip="https://ejemplo.invalid/MiAplicacion_v1.1.0.zip",
	sha256="0" * 64,
	tamano=10,
	fecha="2026-09-25",
	novedades="- Mejora A\n- Arreglo B",
)


def test_etiquetas_novedades_y_teclas(aplicacion, config):
	dialogo = dialogo_mod.DialogoActualizacion(None, config, INFO)
	try:
		assert dialogo.GetTitle() == "Actualización disponible"
		assert dialogo.boton_actualizar.GetLabel() == "&Actualizar ahora"
		assert dialogo.boton_omitir.GetLabel() == "Omitir esta &versión"
		assert dialogo.boton_mas_tarde.GetLabel() == "&Más tarde"
		assert dialogo.boton_mas_tarde.GetId() == wx.ID_CANCEL and dialogo.GetEscapeId() == wx.ID_CANCEL
		assert dialogo.GetDefaultItem() is dialogo.boton_actualizar
		assert "1.0.0" in dialogo.resumen.GetLabel() and "1.1.0" in dialogo.resumen.GetLabel()
		assert dialogo.novedades.GetValue() == "- Mejora A\n- Arreglo B"
		assert not dialogo.novedades.IsEditable()
		assert dialogo.novedades.GetWindowStyle() & wx.TE_MULTILINE
		# La etiqueta con acelerador va justo antes del cuadro (nombre accesible).
		hijos = list(dialogo.GetChildren())
		anterior = hijos[hijos.index(dialogo.novedades) - 1]
		assert isinstance(anterior, wx.StaticText) and anterior.GetLabel() == "&Novedades:"
		assert not dialogo.estado.IsShown()  # el progreso aparece al actualizar
	finally:
		dialogo.Destroy()


def test_sin_novedades_lo_dice(aplicacion, config):
	info = actualizador.InfoActualizacion(**{**INFO.__dict__, "novedades": ""})
	dialogo = dialogo_mod.DialogoActualizacion(None, config, info)
	try:
		assert "no ha publicado novedades" in dialogo.novedades.GetValue()
	finally:
		dialogo.Destroy()


def test_desde_codigo_no_ofrece_instalar(aplicacion, config):
	config.permitir_desde_codigo = False
	dialogo = dialogo_mod.DialogoActualizacion(None, config, INFO)
	try:
		assert not dialogo.boton_actualizar.IsEnabled()
		assert "código fuente" in dialogo.estado.GetValue()
	finally:
		dialogo.Destroy()


def test_progreso_por_hitos_y_fases(aplicacion, config):
	anuncios: list[str] = []
	dialogo = dialogo_mod.DialogoActualizacion(None, config, INFO, anunciar=anuncios.append)
	try:
		for hecho in range(0, 1001, 7):
			dialogo.actualizar_progreso("descarga", hecho, 1000)
		dialogo.actualizar_progreso("descarga", 1000, 1000)
		dialogo.actualizar_progreso("verificacion", 0, 1)
		assert dialogo.barra.GetValue() == 0
		descargas = [texto for texto in anuncios if texto.startswith("Descargando:")]
		assert [texto.split(":")[1].split("%")[0].strip() for texto in descargas] == ["25", "50", "75", "100"]
		assert anuncios[-1] == "Verificando el paquete..."
		assert dialogo.estado.GetValue() == "Verificando el paquete..."
	finally:
		dialogo.Destroy()


def test_omitir_guarda_la_version(aplicacion, config):
	dialogo = dialogo_mod.DialogoActualizacion(None, config, INFO)
	try:
		dialogo._al_omitir(None)
		assert dialogo.resultado == dialogo.RESULTADO_OMITIR
		assert actualizador.leer_estado(config)["version_omitida"] == "1.1.0"
	finally:
		dialogo.Destroy()


def test_error_ofrece_reintentar_y_lleva_el_foco(aplicacion, config):
	anuncios: list[str] = []
	dialogo = dialogo_mod.DialogoActualizacion(None, config, INFO, anunciar=anuncios.append)
	try:
		dialogo._mostrar_progreso(True)
		dialogo._al_fallar("No se pudo conectar con el servidor de actualizaciones.")
		assert dialogo.boton_actualizar.GetLabel() == "&Reintentar" and dialogo.boton_actualizar.IsEnabled()
		assert dialogo.estado.GetValue().startswith("No se pudo conectar")
		assert anuncios[-1].startswith("No se pudo conectar")
	finally:
		dialogo.Destroy()


def test_flujo_completo_en_hilo(aplicacion, config, tmp_path, monkeypatch):
	firmador = cargar_firmador()
	monkeypatch.delenv(firmador.VARIABLE_CONTRASENA, raising=False)
	clave = tmp_path / "k.key"
	config.claves_publicas = (firmador.generar(clave),)
	servidor = Servidor(tmp_path / "servidor")
	try:
		publicar(
			servidor.carpeta,
			"1.1.0",
			{"MiAplicacion.exe": b"EXE 1.1.0", "lib/a.txt": b"a" * 5000},
			clave,
			novedades="- Nueva",
		)
		config.url_manifiesto = servidor.url("version.json")
		info = actualizador.comprobar(config)
		lanzados, cierres, anuncios = [], [], []
		monkeypatch.setattr(actualizador, "lanzar_sustitucion", lanzados.append)

		def cerrar_app():
			cierres.append(1)
			aplicacion.ExitMainLoop()

		dialogo = dialogo_mod.DialogoActualizacion(
			None, config, info, anunciar=anuncios.append, al_cerrar_app=cerrar_app
		)
		try:
			dialogo._al_actualizar(None)
			assert not dialogo.boton_actualizar.IsEnabled()
			assert dialogo.boton_mas_tarde.GetLabel() == "&Cancelar"
			# Bucle de eventos real: CallAfter desde el hilo y CallLater del cierre.
			seguridad = wx.CallLater(20000, aplicacion.ExitMainLoop)
			aplicacion.MainLoop()
			seguridad.Stop()
			assert cierres, anuncios
			assert lanzados and lanzados[0].name == "actualizar.bat"
			assert dialogo.resultado == dialogo.RESULTADO_ACTUALIZAR
			assert any("volverá a abrirse" in texto for texto in anuncios)
		finally:
			dialogo.Destroy()
	finally:
		servidor.cerrar()


def test_menu_ayuda(aplicacion, config):
	marco = wx.Frame(None)
	try:
		menu = wx.Menu()
		elemento = dialogo_mod.anadir_menu_ayuda(marco, menu, config)
		assert elemento.GetItemLabel() == "Buscar &actualizaciones..."
		assert elemento.GetHelp()
	finally:
		marco.Destroy()


def test_comprobador_periodico_respeta_el_intervalo(aplicacion, config):
	marco = wx.Frame(None)
	try:
		comprobador = dialogo_mod.ComprobadorPeriodico(marco, config, intervalo_horas=24)
		assert comprobador.toca_comprobar()  # nunca se comprobó
		ahora = datetime.now(timezone.utc)
		actualizador.guardar_estado(config, {"ultima_comprobacion": ahora.isoformat()})
		assert not comprobador.toca_comprobar()
		actualizador.guardar_estado(config, {"ultima_comprobacion": (ahora - timedelta(hours=25)).isoformat()})
		assert comprobador.toca_comprobar()
		actualizador.guardar_estado(config, {"ultima_comprobacion": "no es una fecha"})
		assert comprobador.toca_comprobar()
		comprobador.detener()
	finally:
		marco.Destroy()


# ── Diálogos con botones en español ─────────────────────────────────────────

def _sin_dialogos_del_proyecto(monkeypatch):
	"""Simula un proyecto sin ``app/ui/accesibilidad/dialogos.py`` (el import falla)."""
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad.dialogos", None)


def _con_dialogos_del_proyecto(monkeypatch, **funciones):
	"""Simula ``app/ui/accesibilidad/dialogos.py`` con las funciones indicadas."""
	falso = types.SimpleNamespace(**funciones)
	paquete = types.ModuleType("app.ui.accesibilidad")
	paquete.dialogos = falso
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad", paquete)
	monkeypatch.setitem(sys.modules, "app.ui.accesibilidad.dialogos", falso)


def _capturar_message_dialog(monkeypatch) -> list[tuple[str, str, str]]:
	"""Sustituye ``ShowModal`` de ``wx.MessageDialog``: anota título, mensaje y botón."""
	vistos: list[tuple[str, str, str]] = []

	def mostrar(dialogo):
		vistos.append((dialogo.GetTitle(), dialogo.GetMessage(), dialogo.GetOKLabel()))
		return wx.ID_OK

	monkeypatch.setattr(wx.MessageDialog, "ShowModal", mostrar)
	return vistos


def test_informar_manual_con_boton_en_espanol(aplicacion, config, monkeypatch):
	"""Ni «ya tiene la última versión» ni los errores usan wx.MessageBox (botón en inglés)."""
	_sin_dialogos_del_proyecto(monkeypatch)
	vistos = _capturar_message_dialog(monkeypatch)
	dialogo_mod._informar_manual(None, config, None, None, None)
	dialogo_mod._informar_manual(None, config, None, "Sin conexión con el servidor.", None)
	assert [(titulo, boton) for titulo, _mensaje, boton in vistos] == [
		("Buscar actualizaciones", "&Aceptar"), ("Buscar actualizaciones", "&Aceptar"),
	]
	assert config.version_actual in vistos[0][1]
	assert vistos[1][1] == "Sin conexión con el servidor."


def test_informar_manual_usa_los_dialogos_del_proyecto(aplicacion, config, monkeypatch):
	recibidos = []
	_con_dialogos_del_proyecto(
		monkeypatch,
		mostrar_mensaje=lambda padre, mensaje, titulo, icono=None: recibidos.append((mensaje, titulo)),
		mostrar_error=lambda padre, mensaje, titulo: recibidos.append((mensaje, titulo)),
	)
	dialogo_mod._informar_manual(None, config, None, "Sin conexión.", None)
	assert recibidos == [("Sin conexión.", "Buscar actualizaciones")]


def test_aviso_tras_actualizacion_fallida_en_espanol(aplicacion, config, monkeypatch):
	"""El aviso de al_arrancar llega por wx.CallAfter a ``mostrar_mensaje``, no a wx.MessageBox."""
	pendientes = []
	monkeypatch.setattr(wx, "CallAfter", lambda funcion, *argumentos: pendientes.append((funcion, argumentos)))
	monkeypatch.setattr(wx, "CallLater", lambda *a, **k: None)  # la confirmación del arranque no interesa aquí
	monkeypatch.setattr(actualizador, "leer_resultado_anterior", lambda configuracion: "No se pudo actualizar.")
	dialogo_mod.al_arrancar(None, config)
	assert pendientes and pendientes[0][0] is dialogo_mod.mostrar_mensaje
	assert pendientes[0][1][1:3] == ("No se pudo actualizar.", "Actualización")
