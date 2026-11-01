"""Conexión de los servidores MCP de Claude Code (1.5.3).

Lo comprobado con Claude Code 2.1.308 y servidores de prueba lentos: con
``MCP_CONNECTION_NONBLOCKING=0`` la espera tiene un tope de 5 s, lo que tarda más
empieza «pending» y fuera del turno, y lo que falla no se vuelve a intentar ni se
cuenta. Las pruebas usan el CLI falso (``FAKE_CLAUDE_MODO``) y nunca muestran ventanas.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import inventario, servidores_mcp
from app.services.agentes import claude
from app.services.agentes.base import AjustesSesion, TipoEvento

FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"


class Recolector:
	def __init__(self) -> None:
		self.eventos = []
		self.fin = threading.Event()

	def __call__(self, evento) -> None:
		self.eventos.append(evento)
		if evento.tipo is TipoEvento.FIN_TURNO:
			self.fin.set()

	def avisos(self) -> list[str]:
		return [e.texto for e in list(self.eventos) if e.tipo is TipoEvento.AVISO]

	def esperar_aviso(self, parte: str, segundos: float = 15.0) -> str:
		limite = time.monotonic() + segundos
		while time.monotonic() < limite:
			for texto in self.avisos():
				if parte in texto:
					return texto
			time.sleep(0.05)
		raise AssertionError(f"No llegó el aviso «{parte}»; llegaron: {self.avisos()}")


@pytest.fixture
def crear(tmp_path, monkeypatch):
	conversaciones = []

	def _crear(modo: str, **opciones):
		monkeypatch.setenv("FAKE_CLAUDE_MODO", modo)
		monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(tmp_path / "registro.jsonl"))
		recolector = Recolector()
		proveedor = claude.ProveedorClaude(orden_base=[sys.executable, str(FAKE)], **opciones)
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), recolector)
		conversaciones.append(conversacion)
		return conversacion, recolector

	yield _crear
	for conversacion in conversaciones:
		conversacion.cerrar()


# -- Variables de entorno --------------------------------------------------------


def _sin_variables_mcp(monkeypatch) -> None:
	for nombre in (claude.VARIABLE_MCP_SIN_ESPERA, claude.VARIABLE_MCP_EN_PARALELO, *claude.VARIABLES_ESPERA_MCP):
		monkeypatch.delenv(nombre, raising=False)


def test_la_espera_cubre_los_tres_tiempos_y_el_paralelismo(monkeypatch):
	_sin_variables_mcp(monkeypatch)
	variables = claude.variables_anadidas(esperar_mcp=True, segundos_mcp=60)
	assert variables["MCP_CONNECTION_NONBLOCKING"] == "0"
	assert variables["MCP_TIMEOUT"] == "60000"
	assert variables["MCP_CONNECT_TIMEOUT_MS"] == "60000"
	assert variables["CLAUDE_CODE_MCP_STARTUP_WAIT_MS"] == "60000"
	assert variables["MCP_SERVER_CONNECTION_BATCH_SIZE"] == "8"


def test_sin_la_opcion_no_se_toca_nada(monkeypatch):
	_sin_variables_mcp(monkeypatch)
	variables = claude.variables_anadidas(esperar_mcp=False)
	assert not any("MCP" in nombre for nombre in variables)


def test_los_tiempos_de_la_persona_mandan_pero_no_el_true_heredado(monkeypatch):
	_sin_variables_mcp(monkeypatch)
	monkeypatch.setenv("MCP_TIMEOUT", "90000")
	monkeypatch.setenv("MCP_SERVER_CONNECTION_BATCH_SIZE", "2")
	# Lo abierto desde la app de escritorio de Claude hereda «true»: dejaba el arranque sin esperar.
	monkeypatch.setenv("MCP_CONNECTION_NONBLOCKING", "true")
	entorno = claude.entorno_claude(esperar_mcp=True, segundos_mcp=30)
	assert entorno["MCP_TIMEOUT"] == "90000"
	assert entorno["MCP_SERVER_CONNECTION_BATCH_SIZE"] == "2"
	assert entorno["MCP_CONNECTION_NONBLOCKING"] == "0"
	assert entorno["MCP_CONNECT_TIMEOUT_MS"] == "30000"


def test_el_proveedor_lee_los_segundos_en_cada_turno():
	opcion = [60]
	proveedor = claude.ProveedorClaude(orden_base=["claude"], segundos_mcp=lambda: opcion[0])
	assert proveedor.segundos_mcp() == 60
	opcion[0] = "basura"
	assert proveedor.segundos_mcp() == claude.SEGUNDOS_ESPERA_MCP


def test_cambiar_los_segundos_relanza_claude(crear):
	conversacion, _recolector = crear("normal")
	assert conversacion._firma(True, True, 30) != conversacion._firma(True, True, 60)  # noqa: SLF001


# -- Estado en español --------------------------------------------------------


def test_motivos_conocidos_y_desconocidos():
	assert "se cierra al arrancar" in servidores_mcp.motivo_legible("Connection closed")
	assert "no ha contestado a tiempo" in servidores_mcp.motivo_legible("Request timed out")
	assert servidores_mcp.motivo_legible("  algo   raro ") == "algo raro"
	assert "claude.ai" in servidores_mcp.motivo_legible("x", "CLAUDEAI_BEARER_REJECTED")


def test_fila_de_mcp_status_completa():
	servidor = servidores_mcp.desde_claude({"name": "npm", "status": "connected", "scope": "project", "source": "project",
		"tools": [{"name": "a"}, {"name": "b"}], "config": {"type": "stdio", "command": "cmd", "args": ["/c", "npx", "x"]}})
	assert servidor.estado_legible == "conectado"
	assert servidor.origen_legible == "del proyecto (.mcp.json)"
	assert servidor.herramientas == 2
	assert servidor.destino == "cmd /c npx x"
	assert servidores_mcp.detalle(servidor) == "conectado, 2 herramientas"
	assert servidores_mcp.desde_claude({"status": "failed"}) is None


def test_aviso_de_inicio_de_sesion_segun_el_tipo():
	propio = servidores_mcp.ServidorMcp("sentry", "needs-auth", origen="user")
	conector = servidores_mcp.ServidorMcp("claude.ai Gmail", "needs-auth", origen="claudeai")
	assert "claude mcp login sentry" in servidores_mcp.aviso_de_problema(propio)
	assert "Conectores" in servidores_mcp.aviso_de_problema(conector)


def test_inventario_prefiere_el_estado_de_ahora():
	datos = {"mcp_servers": [{"name": "roto", "status": "pending"}]}
	assert inventario.desde_init_claude(datos).servidores_mcp == [("roto", "conectándose")]
	ahora = inventario.desde_init_claude(datos, [{"name": "roto", "status": "failed", "error": "Boom"}])
	assert ahora.servidores_mcp == [("roto", "no ha podido arrancar, motivo: Boom")]


# -- Con la conversación ---------------------------------------------------------


def test_avisa_de_lo_que_no_conecta_una_sola_vez(crear):
	conversacion, recolector = crear("mcp_estado")
	conversacion.enviar("Hola")
	assert recolector.fin.wait(20)
	aviso = recolector.esperar_aviso("«roto»")
	assert "se cierra al arrancar" in aviso
	assert "claude mcp login entrar" in recolector.esperar_aviso("«entrar»")
	recolector.fin.clear()
	conversacion.enviar("Otra vez")
	assert recolector.fin.wait(20)
	time.sleep(0.5)
	assert sum("«roto»" in texto for texto in recolector.avisos()) == 1


def test_reintenta_solo_en_reposo_y_avisa_cuando_conecta(crear, tmp_path):
	conversacion, recolector = crear("mcp_recupera")
	conversacion.enviar("Hola")
	assert recolector.fin.wait(20)
	recolector.esperar_aviso("«roto» de Claude no se ha podido conectar")
	recolector.esperar_aviso("«roto» de Claude ya está conectada")
	reintentos = (tmp_path / "registro.jsonl").read_text(encoding="utf-8").count('"mcp_reconnect"')
	assert reintentos == 1


def test_no_reintenta_con_un_turno_en_marcha(crear):
	conversacion, recolector = crear("mcp_estado")
	conversacion.enviar("Hola")
	assert recolector.fin.wait(20)
	recolector.esperar_aviso("«roto»")
	vivo = conversacion._vivo  # noqa: SLF001
	vivo.mcp_reintentados.clear()
	conversacion._turno = SimpleNamespace(terminado=False)  # noqa: SLF001
	conversacion._reintentar_mcp_en_reposo(vivo)  # noqa: SLF001
	assert not vivo.mcp_reintentados
	with pytest.raises(RuntimeError, match="trabajando"):
		conversacion.reconectar_mcp("roto")
	conversacion._turno = None  # noqa: SLF001


def test_lista_reconecta_y_desactiva(crear):
	conversacion, recolector = crear("mcp_recupera")
	conversacion.enviar("Hola")
	assert recolector.fin.wait(20)
	recolector.esperar_aviso("«roto» de Claude ya está conectada")
	assert conversacion.mcp_en_vivo()
	estados = {s.nombre: s.estado for s in conversacion.servidores_mcp()}
	assert estados == {"roto": "connected", "entrar": "needs-auth", "bien": "connected"}
	conversacion.activar_mcp("bien", False)
	assert {s.nombre: s.estado for s in conversacion.servidores_mcp()}["bien"] == "disabled"
	assert "conectado, 1 herramienta" in dict(conversacion.inventario().servidores_mcp)["roto"]


def test_reconectar_dice_por_que_falla(crear):
	conversacion, recolector = crear("mcp_estado")
	conversacion.enviar("Hola")
	assert recolector.fin.wait(20)
	recolector.esperar_aviso("«roto»")
	with pytest.raises(RuntimeError, match="Connection closed"):
		conversacion.reconectar_mcp("roto")


def test_sin_proceso_abierto_vale_lo_de_la_ultima_vez(crear):
	conversacion, _recolector = crear("normal")
	assert conversacion.servidores_mcp() == []
	assert not conversacion.mcp_en_vivo()
	with pytest.raises(RuntimeError, match="no está abierto"):
		conversacion.reconectar_mcp("x")


def test_avisa_mientras_conecta_y_el_mensaje_sale_igual(crear, monkeypatch):
	monkeypatch.setattr(claude, "ESPERA_AVISO_MCP", 0.2)
	monkeypatch.setenv("FAKE_CLAUDE_TARDA", "1.5")
	conversacion, recolector = crear("mcp_tarda")
	conversacion.enviar("Hola")
	recolector.esperar_aviso("está conectando")
	assert recolector.fin.wait(20)


def test_orden_para_iniciar_sesion(crear):
	conversacion, _recolector = crear("normal")
	assert conversacion.orden_inicio_sesion_mcp("sentry")[-3:] == ["mcp", "login", "sentry"]


# -- Diálogo -----------------------------------------------------------------------


def _ejecutar_ya(funcion, *argumentos, al_terminar=None, al_fallar=None, clave=None):
	try:
		resultado = funcion(*argumentos)
	except (RuntimeError, TimeoutError) as error:
		al_fallar(error)
		return
	al_terminar(resultado)


class ConversacionFalsa:
	def __init__(self, servidores) -> None:
		self.servidores = servidores
		self.ajustes = SimpleNamespace(carpeta=None)
		self.reconectados: list[str] = []
		self.activados: list[tuple[str, bool]] = []
		self.orden: list[str] | None = ["claude", "mcp", "login", "x"]

	def servidores_mcp(self):
		return list(self.servidores)

	def mcp_en_vivo(self):
		return True

	def reconectar_mcp(self, nombre):
		if nombre == "roto":
			raise RuntimeError("Connection closed")
		self.reconectados.append(nombre)

	def activar_mcp(self, nombre, activo):
		self.activados.append((nombre, activo))

	def orden_inicio_sesion_mcp(self, nombre):
		return self.orden


@pytest.fixture
def dialogo(app_wx, monkeypatch):
	wx = pytest.importorskip("wx")  # noqa: F841
	from app.ui.dialogos import servidores_mcp as modulo

	anuncios: list[str] = []
	monkeypatch.setattr(modulo.avisos, "anunciar", lambda texto, **_opciones: anuncios.append(texto))
	lista = [
		servidores_mcp.ServidorMcp("roto", "failed", motivo="se cierra", origen="project", destino="npx algo"),
		servidores_mcp.ServidorMcp("entrar", "needs-auth", origen="user"),
		servidores_mcp.ServidorMcp("apagado", "disabled", origen="project"),
		servidores_mcp.ServidorMcp("bien", "connected", herramientas=2, origen="project"),
	]
	conversacion = ConversacionFalsa(lista)
	ventana = modulo.DialogoServidoresMcp(None, conversacion, lista, _ejecutar_ya)
	yield SimpleNamespace(ventana=ventana, conversacion=conversacion, anuncios=anuncios, modulo=modulo)
	ventana.Destroy()


def test_dialogo_muestra_estado_y_detalle(dialogo):
	ventana = dialogo.ventana
	assert ventana.lista.numero_filas() == 4
	assert "con problema 2" in ventana.lista.GetName()
	assert "Motivo: se cierra" in ventana.detalle.GetValue()
	assert "Orden o dirección: npx algo" in ventana.detalle.GetValue()


def test_dialogo_reconecta_el_elegido(dialogo):
	dialogo.ventana.lista.enfocar_fila(3, anunciar=False)
	assert dialogo.ventana.reconectar()
	assert dialogo.conversacion.reconectados == ["bien"]
	assert "«bien» reconectado." in dialogo.anuncios


def test_dialogo_dice_por_que_no_reconecta(dialogo):
	dialogo.ventana.lista.enfocar_fila(0, anunciar=False)
	dialogo.ventana.reconectar()
	assert any("Connection closed" in texto for texto in dialogo.anuncios)
	assert not dialogo.ventana._ocupado  # noqa: SLF001


def test_dialogo_activa_y_desactiva(dialogo):
	dialogo.ventana.lista.enfocar_fila(2, anunciar=False)
	dialogo.ventana.alternar()
	dialogo.ventana.lista.enfocar_fila(3, anunciar=False)
	dialogo.ventana.alternar()
	assert dialogo.conversacion.activados == [("apagado", True), ("bien", False)]


def test_dialogo_intro_inicia_sesion_si_hace_falta(dialogo, monkeypatch):
	ordenes: list[list[str]] = []
	monkeypatch.setattr(dialogo.modulo.procesos, "abrir_en_consola",
		lambda orden, carpeta=None, al_terminar=None: ordenes.append(list(orden)) or True)
	dialogo.ventana.lista.enfocar_fila(1, anunciar=False)
	assert dialogo.ventana.accion_principal()
	assert ordenes == [["claude", "mcp", "login", "x"]]


def test_dialogo_reconecta_todos_los_que_fallan(dialogo):
	dialogo.ventana.reconectar_con_problema()
	assert any("Siguen fallando: 1" in texto for texto in dialogo.anuncios)


def test_dialogo_no_lanza_dos_acciones_a_la_vez(app_wx, dialogo):
	lanzadas = []
	dialogo.ventana._en_fondo = lambda *a, **k: lanzadas.append(a)  # noqa: SLF001
	dialogo.ventana.lista.enfocar_fila(3, anunciar=False)
	assert dialogo.ventana.reconectar()
	assert not dialogo.ventana.reconectar()
	assert len(lanzadas) == 1


def test_menu_lleva_a_los_servidores():
	wx = pytest.importorskip("wx")  # noqa: F841
	from app.ui.ventana_principal import MENUS

	entradas = [e for _titulo, lista in MENUS for e in lista if e and e[-1] == "accion_servidores_mcp"]
	assert entradas and entradas[0][1].endswith("\tCtrl+Alt+M")
