"""Informar de un fallo (AIChat #465): qué se incluye, qué no sale nunca y la dirección de GitHub.

Sin red ni navegador: ``webbrowser.open`` se sustituye y nunca se muestra el diálogo.
"""

from __future__ import annotations

import urllib.parse
from types import SimpleNamespace

import pytest

from app.core import diagnostico
from app.core.diagnostico import DatosEntorno, FilaAgente
from app.services import informe_fallo as modulo
from app.services.agentes import instalacion
from app.services.agentes.base import EstadoProveedor
from app.services.gestor import FilaSesion
from app.services.servidores_mcp import ServidorMcp


def _estado(version="2.1.308 (Claude Code)", sesion=True, ruta=r"C:\Users\ana\.local\bin\claude.exe", cuenta="ana@example.com"):
	return EstadoProveedor(True, ruta=ruta, version=version, mensaje=r"Instalado en C:\Users\ana", sesion_iniciada=sesion, cuenta=cuenta)


# -- Cómo está instalado ------------------------------------------------------------------


@pytest.mark.parametrize(("ruta", "disponible", "esperado"), [
	(r"C:\Users\ana\.local\bin\claude.exe", True, "con su instalador oficial"),
	(r"C:\Users\ana\AppData\Roaming\npm\node_modules\x\codex.exe", True, "con npm"),
	(r"C:\Program Files\WindowsApps\x\agy.exe", True, "de la Microsoft Store"),
	(r"C:\Herramientas\claude.exe", True, "binario nativo"),
	("http://127.0.0.1:11434", True, "servidor"),
	("", True, "desconocida"),
	(r"C:\x", False, ""),
])
def test_como_instalado_sin_decir_la_ruta(ruta, disponible, esperado):
	assert instalacion.como_instalado(ruta, disponible) == esperado


# -- Agentes ---------------------------------------------------------------------------------


def test_agente_sin_cuenta_ni_ruta():
	fila = modulo.fila_agente("claude", "Claude Code", _estado())
	assert fila == FilaAgente("Claude Code", "versión 2.1.308", "sesión iniciada", "con su instalador oficial")


@pytest.mark.parametrize(("sesion", "esperado"), [(True, "sesión iniciada"), (False, "sesión no iniciada"), (None, "sesión desconocida")])
def test_estados_de_la_sesion(sesion, esperado):
	assert modulo.fila_agente("codex", "Codex", _estado(sesion=sesion)).sesion == esperado


def test_una_version_rara_no_sale():
	"""Un error en lugar de versión podría llevar una ruta."""
	fila = modulo.fila_agente("codex", "Codex", _estado(version=r"Error en C:\Users\ana\x"))
	assert fila.version == "versión desconocida"


def test_agente_no_instalado_o_sin_respuesta(monkeypatch):
	monkeypatch.setattr(instalacion, "aviso_claude_npm", lambda: "")
	assert modulo.fila_agente("codex", "Codex", EstadoProveedor(False)) == FilaAgente("Codex", "no instalado")
	assert modulo.fila_agente("codex", "Codex", None) == FilaAgente("Codex", "estado desconocido")


def test_claude_solo_con_npm(monkeypatch):
	monkeypatch.setattr(instalacion, "aviso_claude_npm", lambda: "aviso")
	assert modulo.fila_agente("claude", "Claude", EstadoProveedor(False)).instalacion == "solo con npm"


def test_un_agente_personalizado_no_se_nombra():
	"""Su nombre lo eligió la persona: puede ser el de un cliente o un proyecto."""
	assert modulo.fila_agente("acp-mi-cliente", "Agente de Acme", _estado()).nombre == modulo.AGENTE_PERSONALIZADO


def test_filas_agentes_a_la_vez_y_uno_que_no_contesta():
	import threading

	suelta = threading.Event()

	class Gestor:
		proveedores = {"codex": SimpleNamespace(nombre="Codex"), "gemini": SimpleNamespace(nombre="Gemini")}

		def detectar(self, identificador):
			if identificador == "gemini":
				suelta.wait(5)
			return _estado()

		def estado_proveedor(self, _identificador):
			return EstadoProveedor(True, version="1.0.0", ruta="x")

	try:
		filas = modulo.filas_agentes(Gestor(), tiempo=0.3)
	finally:
		suelta.set()
	assert [f.nombre for f in filas] == ["Codex", "Gemini"]
	assert filas[0].version == "versión 2.1.308" and filas[1].version == "versión 1.0.0"


# -- Sesiones ----------------------------------------------------------------------------------


def test_recuentos_sin_nada_de_las_sesiones():
	filas = [
		FilaSesion("ctrlia:a", "claude", "Título secreto", carpeta=r"C:\cliente"),
		FilaSesion("ctrlia:b", "claude", "Otro", origen="propia"),
		FilaSesion("claude:c", "claude", "Terminal", origen="terminal"),
		FilaSesion("codex:d", "codex", "Hilo", origen="codex"),
		FilaSesion("ctrlia:e", "claude", "Oculta", oculta=True),
		FilaSesion("claude:f", "claude", "Archivada", origen="escritorio", archivada=True),
		FilaSesion("ctrlia:g", "acp-acme", "Personalizado"),
		FilaSesion("x:h", "claude", "Rara", origen="algo-privado"),
	]
	recuentos, ocultas = modulo.recuentos_sesiones(filas)
	assert ocultas == 2
	assert recuentos["Claude"] == {"Ctrl+IA": 2, "Terminal": 1, modulo.OTRO_ORIGEN: 1}
	assert recuentos["Codex"] == {"Codex": 1}
	assert recuentos[modulo.AGENTE_PERSONALIZADO] == {"Ctrl+IA": 1}
	assert "algo-privado" not in str(recuentos) and "secreto" not in str(recuentos)


# -- Servidores MCP -------------------------------------------------------------------------------


def test_servidores_mcp_solo_nombre_estado_y_motivo():
	servidor = ServidorMcp("sentry", "failed", motivo="se cierra", destino="npx -y sentry --token=abc123", origen="project", herramientas=3)
	conversacion = SimpleNamespace(servidores_mcp=lambda: [servidor])
	assert modulo.filas_mcp(conversacion) == [("sentry", "no ha podido arrancar", "se cierra")]


@pytest.mark.parametrize(("motivo", "destino", "no_sale"), [
	("spawn npx --token=abc123 ENOENT", "npx --token=abc123", "abc123"),
	("Fallo al conectar con https://interno.empresa.com/mcp?clave=1", "", "interno.empresa.com"),
	(r"No existe C:\proyectos\cliente-acme\server.exe", "", "cliente-acme"),
	("No existe /home/ana/cliente-acme/server", "", "cliente-acme"),
	("Rechazado para ana@example.com", "", "ana@example.com"),
])
def test_el_motivo_no_repite_la_orden_ni_la_direccion(motivo, destino, no_sale):
	limpio = modulo.motivo_sin_secretos(ServidorMcp("x", "failed", motivo=motivo, destino=destino))
	assert no_sale not in limpio and limpio


def test_sin_conversacion_o_si_el_agente_no_informa():
	assert modulo.filas_mcp(None) is None
	assert modulo.filas_mcp(SimpleNamespace(servidores_mcp=lambda: None)) == []


def test_si_falla_al_preguntar_el_informe_sigue():
	def falla():
		raise TimeoutError("no contesta")

	assert modulo.filas_mcp(SimpleNamespace(servidores_mcp=falla)) == []


def test_recoger_entorno_de_la_sesion_abierta():
	conversacion = SimpleNamespace(servidores_mcp=lambda: [ServidorMcp("bien", "connected")])
	pedidas: list[tuple[str, bool]] = []

	def buscar(clave, crear=True):
		pedidas.append((clave, crear))
		return conversacion

	datos = modulo.recoger_entorno(SimpleNamespace(conversacion=buscar), [FilaSesion("ctrlia:a", "claude", "x")], "ctrlia:a",
		agentes=lambda _g: [FilaAgente("Claude", "versión 1.0")])
	assert pedidas == [("ctrlia:a", False)]  # nunca abre una conversación para esto
	assert datos.servidores_mcp == [("bien", "conectado", "")] and datos.agentes[0].nombre == "Claude"
	assert modulo.recoger_entorno(SimpleNamespace(), [], "", agentes=lambda _g: []).servidores_mcp is None


# -- Informe del sistema --------------------------------------------------------------------------


def _datos() -> DatosEntorno:
	return DatosEntorno([FilaAgente("Claude Code", "versión 2.1.308", "sesión iniciada", "binario nativo"), FilaAgente("Codex", "no instalado")],
		{"Claude": {"Ctrl+IA": 2, "Terminal": 1}}, 3, [("roto", "no ha podido arrancar", "se cierra"), ("bien", "conectado", "")])


def test_informe_sistema_con_agentes_sesiones_y_mcp():
	texto = diagnostico.informe_sistema("Ctrl+IA", "1.5.3", _datos(), paquetes=False)
	assert "Claude Code: versión 2.1.308; sesión iniciada; instalación: binario nativo" in texto
	assert "Codex: no instalado" in texto
	assert "Claude: 2 de Ctrl+IA, 1 de Terminal" in texto and "Ocultas o archivadas: 3" in texto
	assert "roto: no ha podido arrancar. Motivo: se cierra" in texto and "bien: conectado\n" in texto + "\n"
	assert "Paquetes instalados" not in texto


def test_informe_sistema_sin_datos_como_siempre():
	texto = diagnostico.informe_sistema("Ctrl+IA", "1.5.3")
	assert "Paquetes instalados" in texto and "Agentes:" not in texto and "Servidores MCP" not in texto


def test_informe_sistema_sin_servidores():
	texto = diagnostico.informe_sistema("Ctrl+IA", "1.5.3", DatosEntorno(servidores_mcp=[]), paquetes=False)
	assert "Ninguno que este agente informe." in texto


def test_paquete_de_diagnostico_con_los_datos(tmp_path, monkeypatch):
	import zipfile

	from app.core import registro

	monkeypatch.setattr(registro, "carpeta_registros", lambda: tmp_path / "logs")
	(tmp_path / "logs").mkdir()
	ruta = diagnostico.generar_paquete("Ctrl+IA", "1.5.3", datos=_datos())
	with zipfile.ZipFile(ruta) as zip_:
		assert "Claude Code: versión 2.1.308" in zip_.read("sistema.txt").decode("utf-8")


@pytest.mark.parametrize(("texto", "no_sale"), [
	(r"Falla en C:\Users\ana.garcia\x", "ana.garcia"),
	(r"Falla en C:\Users\ana.garcia", "ana.garcia"),
	("Falla en C:\\Users\\ana.garcia\ny sigue", "ana.garcia"),
	(r"En C:\Users\ana.garcia pasa esto", "ana.garcia"),
	(r"En C:\Usuarios\ana.garcia\proyecto", "ana.garcia"),
])
def test_anonimizar_quita_la_cuenta_en_cualquier_sitio_de_la_frase(texto, no_sale):
	limpio = diagnostico.anonimizar(texto)
	assert no_sale not in limpio and "<usuario>" in limpio


def test_anonimizar_no_duplica_la_marca():
	assert diagnostico.anonimizar(r"C:\Users\ana\x y C:\Users\luis") == r"C:\Users\<usuario>\x y C:\Users\<usuario>"


# -- Texto del informe -------------------------------------------------------------------------------


def _informe(**cambios) -> modulo.InformeFallo:
	datos = {"resumen": "No arranca", "que_paso": "Se cierra solo", "esperado": "Que arranque", "pasos": "1. Abrirlo",
		"entorno": "Aplicación: Ctrl+IA 1.5.3"}
	return modulo.InformeFallo(**{**datos, **cambios})


def test_el_informe_lleva_todo_en_apartados():
	texto = modulo.texto_informe(_informe())
	assert texto.index("### Resumen") < texto.index("### Qué ha pasado") < texto.index("### Qué esperaba")
	assert texto.index("### Qué esperaba") < texto.index("### Pasos") < texto.index("### Entorno")
	assert "```text\nAplicación: Ctrl+IA 1.5.3\n```" in texto


def test_lo_opcional_no_sale_vacio():
	texto = modulo.texto_informe(_informe(esperado=" ", pasos=""))
	assert "### Qué esperaba" not in texto and "### Pasos" not in texto


def test_lo_que_escribe_la_persona_tambien_se_anonimiza():
	texto = modulo.texto_informe(_informe(que_paso=r"Falla C:\Users\ana\x con token=abc123secreto"))
	assert "ana" not in texto.split("### Entorno")[0].replace("Qué", "") and "abc123secreto" not in texto


# -- Dirección de GitHub --------------------------------------------------------------------------------


def _consulta(url: str) -> dict[str, list[str]]:
	return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


def test_la_direccion_lleva_titulo_cuerpo_y_etiqueta():
	url = modulo.url_incidencia(_informe())
	assert url.startswith("https://github.com/hxebolax/CtrlIA/issues/new?")
	consulta = _consulta(url)
	assert consulta["title"] == ["No arranca"] and consulta["labels"] == ["bug"]
	assert "### Qué ha pasado" in consulta["body"][0] and "### Resumen" not in consulta["body"][0]


def test_la_direccion_larga_recorta_lo_escrito_y_conserva_el_entorno():
	url = modulo.url_incidencia(_informe(que_paso="ñ" * 20000, pasos="é" * 9000))
	cuerpo = _consulta(url)["body"][0]
	assert len(urllib.parse.quote(cuerpo)) <= modulo.MAX_CUERPO_URL
	assert "Aplicación: Ctrl+IA 1.5.3" in cuerpo and "portapapeles" in cuerpo


def test_la_direccion_cabe_aunque_el_entorno_sea_enorme():
	url = modulo.url_incidencia(_informe(que_paso="corto", esperado="", pasos="", entorno="línea\n" * 5000))
	cuerpo = _consulta(url)["body"][0]
	assert len(urllib.parse.quote(cuerpo)) <= modulo.MAX_CUERPO_URL and "portapapeles" in cuerpo


def test_la_direccion_tambien_va_anonimizada():
	url = modulo.url_incidencia(_informe(resumen=r"Falla C:\Users\ana.garcia\x", que_paso=r"En C:\Users\ana.garcia\y"))
	assert "ana.garcia" not in urllib.parse.unquote(url)


def test_titulo_largo_o_con_saltos():
	consulta = _consulta(modulo.url_incidencia(_informe(resumen="x\n" * 300)))
	assert "\n" not in consulta["title"][0] and len(consulta["title"][0]) <= modulo.MAX_RESUMEN


# -- Diálogo ---------------------------------------------------------------------------------------


@pytest.fixture
def entorno_dialogo(app_wx, monkeypatch):
	wx = pytest.importorskip("wx")
	from app.ui.dialogos import informar_fallo
	from tests.ayudas_wx import procesar_eventos

	estado = SimpleNamespace(modulo=informar_fallo, anuncios=[], errores=[], copiado=[], abiertas=[], pendiente=None, wx=wx)
	monkeypatch.setattr(informar_fallo.avisos, "anunciar", lambda texto, **_o: estado.anuncios.append(texto))
	monkeypatch.setattr(informar_fallo, "mostrar_error", lambda _p, texto, *_a: estado.errores.append(texto))
	monkeypatch.setattr(informar_fallo, "copiar_texto", lambda texto: estado.copiado.append(texto) or True)
	monkeypatch.setattr(informar_fallo.webbrowser, "open", lambda url: estado.abiertas.append(url) or True)

	def en_fondo(funcion, al_terminar=None, al_fallar=None):
		estado.pendiente = (funcion, al_terminar, al_fallar)

	estado.ventana = informar_fallo.DialogoInformarFallo(None, lambda: "Aplicación: Ctrl+IA 1.5.3", en_fondo)
	yield estado
	estado.ventana.Destroy()
	procesar_eventos()


def _terminar_recogida(estado) -> None:
	funcion, al_terminar, _al_fallar = estado.pendiente
	al_terminar(funcion())


def _rellenar(estado) -> None:
	estado.ventana.campo_resumen.SetValue("No arranca")
	estado.ventana.campo_que_paso.SetValue("Se cierra solo")


def test_se_abre_sin_esperar_a_los_datos(entorno_dialogo):
	assert entorno_dialogo.pendiente is not None
	assert "Recogiendo los datos" in entorno_dialogo.ventana.vista.GetValue()


def test_lo_que_se_incluira_se_ve_mientras_se_escribe(entorno_dialogo):
	_terminar_recogida(entorno_dialogo)
	assert "Datos del programa recogidos." in entorno_dialogo.anuncios
	assert "Aplicación: Ctrl+IA 1.5.3" in entorno_dialogo.ventana.vista.GetValue()
	entorno_dialogo.ventana.campo_que_paso.SetValue(r"Falla C:\Users\ana.garcia\x")
	vista = entorno_dialogo.ventana.vista.GetValue()
	assert "Falla C:\\Users\\<usuario>\\x" in vista and "ana.garcia" not in vista


def test_lo_que_se_incluira_es_de_solo_lectura(entorno_dialogo):
	assert not entorno_dialogo.ventana.vista.IsEditable()


def test_copiar_pide_lo_minimo(entorno_dialogo):
	_terminar_recogida(entorno_dialogo)
	assert not entorno_dialogo.ventana.copiar()
	assert entorno_dialogo.errores == ["Escribe un resumen del fallo."] and not entorno_dialogo.copiado
	entorno_dialogo.ventana.campo_resumen.SetValue("No arranca")
	assert not entorno_dialogo.ventana.copiar()
	assert entorno_dialogo.errores[-1] == "Cuenta qué ha pasado."


def test_copiar_espera_a_los_datos(entorno_dialogo):
	_rellenar(entorno_dialogo)
	assert not entorno_dialogo.ventana.copiar()
	assert any("Espera" in texto for texto in entorno_dialogo.anuncios) and not entorno_dialogo.copiado


def test_copiar_deja_el_informe_entero(entorno_dialogo):
	_terminar_recogida(entorno_dialogo)
	_rellenar(entorno_dialogo)
	assert entorno_dialogo.ventana.copiar()
	assert "No arranca" in entorno_dialogo.copiado[0] and "Ctrl+IA 1.5.3" in entorno_dialogo.copiado[0]
	assert "Informe copiado." in entorno_dialogo.anuncios


def test_abrir_copia_antes_y_nunca_publica_solo(entorno_dialogo):
	_terminar_recogida(entorno_dialogo)
	_rellenar(entorno_dialogo)
	assert entorno_dialogo.ventana.abrir_incidencia()
	assert entorno_dialogo.copiado and len(entorno_dialogo.abiertas) == 1
	assert entorno_dialogo.abiertas[0].startswith("https://github.com/hxebolax/CtrlIA/issues/new?")


def test_si_no_se_abre_el_navegador_lo_dice(entorno_dialogo, monkeypatch):
	_terminar_recogida(entorno_dialogo)
	_rellenar(entorno_dialogo)
	monkeypatch.setattr(entorno_dialogo.modulo.webbrowser, "open", lambda url: False)
	assert not entorno_dialogo.ventana.abrir_incidencia()
	assert "github.com/hxebolax/CtrlIA" in entorno_dialogo.errores[-1]


def test_si_fallan_los_datos_se_puede_informar_igual(entorno_dialogo):
	_funcion, _al_terminar, al_fallar = entorno_dialogo.pendiente
	al_fallar(RuntimeError("boom"))
	_rellenar(entorno_dialogo)
	assert entorno_dialogo.ventana.copiar()
	assert "No se han podido recoger" in entorno_dialogo.copiado[0]


def test_si_se_cierra_antes_no_pasa_nada(app_wx, monkeypatch):
	wx = pytest.importorskip("wx")  # noqa: F841
	from app.ui.dialogos import informar_fallo
	from tests.ayudas_wx import procesar_eventos

	monkeypatch.setattr(informar_fallo.avisos, "anunciar", lambda *a, **k: None)
	guardado = []
	ventana = informar_fallo.DialogoInformarFallo(None, lambda: "x", lambda f, al_terminar=None, al_fallar=None: guardado.append(al_terminar))
	ventana.Destroy()
	procesar_eventos()
	guardado[0]("entorno")  # no debe lanzar


def test_accesibilidad_del_dialogo(entorno_dialogo):
	wx = entorno_dialogo.wx
	ventana = entorno_dialogo.ventana
	for control in (*ventana.campos, ventana.vista, ventana.boton_copiar, ventana.boton_github, ventana.boton_cerrar):
		assert control.GetHelpText(), control.GetName()
	hijos = ventana.GetChildren()[0].GetChildren()
	for campo in (*ventana.campos, ventana.vista):
		anterior = hijos[list(hijos).index(campo) - 1]
		assert isinstance(anterior, wx.StaticText), campo.GetName()  # etiqueta justo antes de su control
	letras = [h.GetLabel()[h.GetLabel().index("&") + 1].lower() for h in hijos
		if isinstance(h, (wx.StaticText, wx.Button)) and "&" in h.GetLabel()]
	assert len(letras) == len(set(letras)), letras


def test_la_ventana_abre_el_dialogo_y_recoge_los_datos(app_wx, tmp_path, monkeypatch, data_temporal):
	wx = pytest.importorskip("wx")
	from app.core.configuracion import Configuracion
	from app.services.almacen import Almacen
	from app.ui import avisos
	from app.ui.dialogos import informar_fallo
	from app.ui.ventana_principal import VentanaPrincipal
	from tests.ayudas_wx import procesar_eventos
	from tests.test_gestor import ProveedorFalso

	vistas: list[str] = []
	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "anunciar", lambda *a, **k: None)

	def mostrar(dialogo) -> int:
		vistas.append(dialogo.vista.GetValue())
		return wx.ID_CANCEL

	monkeypatch.setattr(informar_fallo.DialogoInformarFallo, "ShowModal", mostrar)
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"historial.ollama": False, "general.confirmar_salida": False, "general.abrir_ultima_sesion": False})
	marco = VentanaPrincipal(config, None, almacen=Almacen(tmp_path / "ventana.db"), proveedores={"falso": ProveedorFalso()})

	def en_segundo_plano(funcion, *argumentos, al_terminar=None, al_fallar=None, clave=None):
		al_terminar(funcion(*argumentos))

	monkeypatch.setattr(marco, "en_segundo_plano", en_segundo_plano)
	try:
		procesar_eventos()
		marco.accion_informar_fallo()
		assert len(vistas) == 1
		assert modulo.AGENTE_PERSONALIZADO + ": versión 1.0" in vistas[0] and "Agente falso" not in vistas[0]
		assert "Lectores de pantalla" in vistas[0] and "Paquetes instalados" not in vistas[0]
	finally:
		marco.salir()
		procesar_eventos()


def test_el_menu_ayuda_lleva_al_dialogo():
	pytest.importorskip("wx")
	from app.ui.ventana_principal import MENUS, VentanaPrincipal

	ayuda = next(elementos for titulo, elementos in MENUS if titulo == "Ay&uda")
	entrada = next(e for e in ayuda if e[0] == "informar_fallo")
	assert entrada[1] == "&Informar de un fallo..." and hasattr(VentanaPrincipal, entrada[3])
	letras = [e[1][e[1].index("&") + 1].lower() for e in ayuda if e[0] != "-"]
	assert len(letras) == len(set(letras)), letras
