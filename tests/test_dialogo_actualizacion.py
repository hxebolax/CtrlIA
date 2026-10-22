"""Diálogo accesible de actualización (PLANTILLA del agente «actualizador»).

Crea controles wx reales (sin mostrarlos de forma modal) y recorre el flujo de
descarga en su hilo contra un servidor HTTP local, sin lanzar el script real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

wx = pytest.importorskip("wx")
pytest.importorskip("cryptography")

from tests.apoyo_actualizador import Servidor, cargar_firmador, publicar  # noqa: E402

from app.services import actualizador  # noqa: E402
from app.ui import dialogo_actualizacion as dialogo_mod  # noqa: E402


@pytest.fixture(scope="module")
def aplicacion(app_wx):
	# La wx.App es única para toda la sesión (conftest): crear y destruir otra
	# deja sin aplicación a los módulos que se ejecutan después.
	return app_wx


@pytest.fixture
def config(tmp_path):
	instalacion = tmp_path / "Mi App"
	instalacion.mkdir()
	(instalacion / "CtrlIA.exe").write_bytes(b"EXE 1.0.0")
	return actualizador.Configuracion(
		version_actual="1.0.0", carpeta_app=instalacion, comando=[], permitir_desde_codigo=True
	)


INFO = actualizador.InfoActualizacion(
	version="1.1.0",
	archivo="CtrlIA_v1.1.0.zip",
	url_zip="https://ejemplo.invalid/CtrlIA_v1.1.0.zip",
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


def _textos(ventana) -> list[str]:
	"""Etiquetas y valores de todos los controles (lo que puede oír la persona)."""
	textos, pendientes = [], list(ventana.GetChildren())
	while pendientes:
		control = pendientes.pop(0)
		textos.append(control.GetValue() if isinstance(control, wx.TextCtrl) else control.GetLabel())
		textos.append(control.GetName())
		pendientes.extend(control.GetChildren())
	return textos


def test_sin_novedades_no_hay_campo_ni_se_menciona(aplicacion, config):
	info = actualizador.InfoActualizacion(**{**INFO.__dict__, "novedades": ""})
	dialogo = dialogo_mod.DialogoActualizacion(None, config, info)
	try:
		assert dialogo.novedades is None
		assert not any("ovedades" in texto for texto in _textos(dialogo))
		assert "Ctrl+IA" in dialogo.resumen.GetLabel()  # el nombre que oye la persona
		assert "CtrlIA" not in dialogo.resumen.GetLabel()
	finally:
		dialogo.Destroy()


def test_con_novedades_hay_campo_de_solo_lectura(aplicacion, config):
	dialogo = dialogo_mod.DialogoActualizacion(None, config, INFO)
	try:
		assert dialogo.novedades is not None and dialogo.novedades.GetValue() == INFO.novedades
		assert dialogo.novedades.GetHelpText()
	finally:
		dialogo.Destroy()


def test_textos_tratan_de_tu():
	"""Ni el diálogo ni el servicio hablan de usted a la persona."""
	import re
	from pathlib import Path

	raiz = Path(__file__).resolve().parents[1]
	for ruta in ("app/services/actualizador.py", "app/ui/dialogo_actualizacion.py"):
		codigo = (raiz / ruta).read_text(encoding="utf-8")
		for patron in (r"Compruebe", r"Vuelva a", r"Podrá ", r"Consulte", r"\bSus datos", r"Puede volver", r"actualícela"):
			assert not re.search(patron, codigo), f"{ruta}: «{patron}»"
		assert "no ha publicado novedades" not in codigo


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


def test_error_ofrece_reintentar_y_lleva_el_foco(aplicacion, config, monkeypatch):
	# Sin foco real: el evento de foco pendiente lo procesaría otra prueba y el
	# lector de pantalla del equipo consultaría una ventana ya destruida.
	enfocados = []
	monkeypatch.setattr(wx.Window, "SetFocus", lambda control: enfocados.append(control))
	anuncios: list[str] = []
	dialogo = dialogo_mod.DialogoActualizacion(None, config, INFO, anunciar=anuncios.append)
	try:
		dialogo._mostrar_progreso(True)
		dialogo._al_fallar("No se pudo conectar con el servidor de actualizaciones.")
		assert dialogo.boton_actualizar.GetLabel() == "&Reintentar" and dialogo.boton_actualizar.IsEnabled()
		assert dialogo.estado.GetValue().startswith("No se pudo conectar")
		assert anuncios[-1].startswith("No se pudo conectar")
		assert enfocados and enfocados[-1] is dialogo.estado  # el error se puede releer
	finally:
		dialogo.Destroy()


@pytest.fixture
def publicado(config, tmp_path, monkeypatch):
	"""Servidor local con la 1.1.0 publicada y firmada; devuelve (info, servidor)."""
	firmador = cargar_firmador()
	monkeypatch.delenv(firmador.VARIABLE_CONTRASENA, raising=False)
	clave = tmp_path / "k.key"
	config.claves_publicas = (firmador.generar(clave),)
	servidor = Servidor(tmp_path / "servidor")
	publicar(
		servidor.carpeta, "1.1.0", {"CtrlIA.exe": b"EXE 1.1.0", "lib/a.txt": b"a" * 5000}, clave, novedades="- Nueva"
	)
	config.url_manifiesto = servidor.url("version.json")
	yield actualizador.comprobar(config), servidor
	servidor.cerrar()


def _flujo(config, info, monkeypatch, dialogo_modal, respuesta):
	"""Descarga en su hilo, contesta la pregunta de reinicio y devuelve lo ocurrido."""
	from tests.ayudas_wx import esperar_hasta

	lanzados, cierres, anuncios = [], [], []
	monkeypatch.setattr(actualizador, "lanzar_sustitucion", lanzados.append)
	monkeypatch.setattr(dialogo_mod, "_sesiones_ocupadas", lambda: 0)
	# Nada de foco real: el lector de pantalla del equipo reaccionaría a ventanas ocultas.
	monkeypatch.setattr(wx.Window, "SetFocus", lambda self: None)
	dialogo_modal.responder(respuesta)
	dialogo = dialogo_mod.DialogoActualizacion(
		None, config, info, anunciar=anuncios.append, al_cerrar_app=lambda: cierres.append(1)
	)
	try:
		dialogo._al_actualizar(None)
		assert not dialogo.boton_actualizar.IsEnabled()
		assert dialogo.boton_mas_tarde.GetLabel() == "&Cancelar"
		if respuesta == wx.ID_OK:
			esperar_hasta(lambda: bool(cierres), 20, "no se cerró la aplicación tras «Reiniciar ahora»")
		else:
			esperar_hasta(
				lambda: any(d.titulo == "Actualización pendiente" for d in dialogo_modal.mostrados),
				20,
				"no se explicó cómo instalar después",
			)
		return dialogo.resultado, lanzados, cierres, anuncios
	finally:
		dialogo.Destroy()


def test_flujo_reiniciar_ahora(aplicacion, config, publicado, monkeypatch, dialogo_modal):
	info, _servidor = publicado
	resultado, lanzados, cierres, anuncios = _flujo(config, info, monkeypatch, dialogo_modal, wx.ID_OK)
	pregunta = dialogo_modal.mostrados[0]
	assert pregunta.titulo == "Actualización lista"
	assert any("¿Reiniciar ahora para aplicar la actualización?" in t for t in pregunta.textos)
	assert "&Reiniciar ahora" in pregunta.textos and "&Más tarde" in pregunta.textos
	assert len(dialogo_modal.mostrados) == 1  # se pregunta UNA sola vez
	assert lanzados and lanzados[0].name == "actualizar.bat"
	assert cierres == [1]
	assert resultado == dialogo_mod.DialogoActualizacion.RESULTADO_ACTUALIZAR
	assert any("Se volverá a abrir sola" in texto for texto in anuncios)
	assert "Reiniciar ahora" in config.registro_script.read_text(encoding="utf-8")


def test_flujo_mas_tarde(aplicacion, config, publicado, monkeypatch, dialogo_modal):
	info, servidor = publicado
	resultado, lanzados, cierres, _anuncios = _flujo(config, info, monkeypatch, dialogo_modal, wx.ID_CANCEL)
	assert not lanzados and not cierres
	assert resultado == dialogo_mod.DialogoActualizacion.RESULTADO_MAS_TARDE
	aviso = dialogo_modal.mostrados[-1]
	assert any("Buscar actualizaciones..." in t and "Actualizar ahora" in t for t in aviso.textos)
	# No queda nada que se instale al cerrar, pero la descarga se conserva...
	assert not (config.carpeta_trabajo / "actualizar.bat").exists()
	assert not (config.carpeta_trabajo / "nueva").exists()
	assert (config.carpeta_trabajo / "descarga" / info.archivo).is_file()
	# ...y la próxima vez se reutiliza sin volver a bajarla.
	bajadas = sum(1 for nombre, _r in servidor.peticiones if nombre == info.archivo)
	actualizador.descargar_y_preparar(config, info)
	assert sum(1 for nombre, _r in servidor.peticiones if nombre == info.archivo) == bajadas


# ── Pregunta de reinicio ─────────────────────────────────────────────────────


def test_dialogo_reinicio_botones(aplicacion):
	dialogo = dialogo_mod.DialogoReinicio(None, "1.5.0")
	try:
		assert dialogo.GetTitle() == "Actualización lista"
		assert dialogo.boton_reiniciar.GetLabel() == "&Reiniciar ahora"
		assert dialogo.boton_mas_tarde.GetLabel() == "&Más tarde"
		assert dialogo.GetDefaultItem() is dialogo.boton_reiniciar
		assert dialogo.GetEscapeId() == wx.ID_CANCEL
		assert dialogo.boton_reiniciar.GetHelpText() and dialogo.boton_mas_tarde.GetHelpText()
		assert " ".join(dialogo.mensaje.GetLabel().split()).startswith(
			"La versión 1.5.0 está descargada y lista. Para instalarla, Ctrl+IA tiene que cerrarse y volver a abrirse."
		)
	finally:
		dialogo.Destroy()


def test_preguntar_reinicio_con_modales_simulados(aplicacion, dialogo_modal):
	dialogo_modal.responder(wx.ID_OK, wx.ID_CANCEL)
	assert dialogo_mod.preguntar_reinicio(None, "1.5.0") is True
	assert dialogo_mod.preguntar_reinicio(None, "1.5.0") is False
	assert [d.titulo for d in dialogo_modal.mostrados] == ["Actualización lista", "Actualización lista"]


def test_texto_reinicio_con_agentes_trabajando():
	assert "agente" not in dialogo_mod.texto_reinicio("1.5.0", 0)
	uno = dialogo_mod.texto_reinicio("1.5.0", 1)
	assert "Hay 1 sesión con el agente trabajando: se detendrá, pero su conversación se conserva." in uno
	varias = dialogo_mod.texto_reinicio("1.5.0", 3)
	assert "Hay 3 sesiones con el agente trabajando: se detendrán, pero sus conversaciones se conservan." in varias
	assert varias.endswith("¿Reiniciar ahora para aplicar la actualización?")


def test_al_preparada_cuenta_las_sesiones_ocupadas(aplicacion, config, monkeypatch):
	preguntas = []
	monkeypatch.setattr(actualizador, "descartar_preparada", lambda _config: None)
	monkeypatch.setattr(dialogo_mod, "_avisar_mas_tarde", lambda *a: None)
	dialogo = dialogo_mod.DialogoActualizacion(
		None, config, INFO, preguntar=lambda padre, v, n: preguntas.append((v, n)) or False, contar_ocupadas=lambda: 2
	)
	try:
		dialogo._al_preparada(config.carpeta_trabajo / "actualizar.bat")
		assert preguntas == [("1.1.0", 2)]
		assert dialogo.resultado == dialogo.RESULTADO_MAS_TARDE
	finally:
		dialogo.Destroy()


def test_cerrar_aplicacion_usa_salir_para_actualizar(aplicacion, monkeypatch):
	llamadas = []

	class Ventana:
		def salir_para_actualizar(self):
			llamadas.append("sin preguntar")

		def Close(self):  # noqa: N802 - nombre de wx
			llamadas.append("normal")

	monkeypatch.setattr(dialogo_mod, "_ventana_principal", Ventana)
	dialogo_mod._cerrar_aplicacion()
	assert llamadas == ["sin preguntar"]


def test_comprobador_periodico_pasa_anunciar(aplicacion, config, monkeypatch):
	"""El diálogo abierto por la comprobación automática recibe la función de voz."""
	recibidos = []
	monkeypatch.setattr(dialogo_mod, "mostrar_dialogo", lambda v, c, i, anunciar=None: recibidos.append(anunciar))
	anuncios: list[str] = []
	marco = wx.Frame(None)
	try:
		comprobador = dialogo_mod.ComprobadorPeriodico(marco, config, anunciar=anuncios.append)
		comprobador._mostrar(INFO)
		assert recibidos == [anuncios.append]
		assert anuncios == ["Hay una versión nueva de Ctrl+IA: la 1.1.0."]
		comprobador.detener()
	finally:
		marco.Destroy()


def test_main_crea_el_comprobador_con_anunciar():
	from pathlib import Path

	codigo = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
	assert "ComprobadorPeriodico(" in codigo and "anunciar=avisos.anunciar" in codigo


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
