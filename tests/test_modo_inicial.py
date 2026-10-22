"""Modo de permisos inicial: «Nueva sesión» respeta la configuración de cada agente.

Fallo de la 1.3.0: con «Modo de permisos inicial: Automático» en la
configuración de Claude Code, las sesiones nuevas empezaban en «Preguntar»
porque el diálogo proponía siempre el primer modo y Claude se lanzaba con
``--permission-mode default``, que manda sobre su settings.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.services.agentes.acp import ProveedorAcp
from app.services.agentes.antigravity import MODO_REVISAR, MODO_SANDBOX, MODO_SIN_PREGUNTAS, ProveedorAntigravity
from app.services.agentes.base import AjustesSesion, OpcionLista
from app.services.agentes.claude import ProveedorClaude
from app.services.agentes.codex import ProveedorCodex
from app.services.config_agentes import modo_inicial


def _escribir_json(ruta: Path, datos: dict) -> None:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	ruta.write_text(json.dumps(datos), encoding="utf-8")


def _claude_usuario(datos: dict) -> None:
	_escribir_json(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json", datos)


def _codex_usuario(texto: str) -> None:
	ruta = Path(os.environ["CODEX_HOME"]) / "config.toml"
	ruta.parent.mkdir(parents=True, exist_ok=True)
	ruta.write_text(texto, encoding="utf-8")


# ── Claude Code ──────────────────────────────────────────────────────────────


def test_claude_sin_configuracion_propone_preguntar(tmp_path):
	assert ProveedorClaude(orden_base=["claude"]).modo_predeterminado(tmp_path) == "default"


def test_claude_propone_el_modo_automatico_del_usuario(tmp_path):
	_claude_usuario({"permissions": {"defaultMode": "auto"}})
	proveedor = ProveedorClaude(orden_base=["claude"])
	assert proveedor.modo_predeterminado(tmp_path) == "auto"
	assert proveedor.modo_predeterminado() == "auto"


def test_claude_el_proyecto_manda_sobre_el_usuario(tmp_path):
	_claude_usuario({"permissions": {"defaultMode": "auto"}})
	_escribir_json(tmp_path / ".claude" / "settings.json", {"permissions": {"defaultMode": "acceptEdits"}})
	assert modo_inicial.modo_claude(tmp_path) == "acceptEdits"
	_escribir_json(tmp_path / ".claude" / "settings.local.json", {"permissions": {"defaultMode": "plan"}})
	assert modo_inicial.modo_claude(tmp_path) == "plan"


def test_claude_auto_y_saltarse_no_valen_desde_un_proyecto(tmp_path):
	_claude_usuario({"permissions": {"defaultMode": "acceptEdits"}})
	_escribir_json(tmp_path / ".claude" / "settings.json", {"permissions": {"defaultMode": "auto"}})
	# Como Claude Code: el modo de serie, sin mirar el del usuario.
	assert ProveedorClaude(orden_base=["claude"]).modo_predeterminado(tmp_path) == "default"
	_escribir_json(tmp_path / ".claude" / "settings.json", {"permissions": {"defaultMode": "bypassPermissions"}})
	assert modo_inicial.modo_claude(tmp_path) == "default"


def test_claude_alias_manual_y_valores_raros(tmp_path):
	_claude_usuario({"permissions": {"defaultMode": "manual"}})
	assert modo_inicial.modo_claude(tmp_path) == "default"
	_claude_usuario({"permissions": {"defaultMode": "dontAsk"}})
	assert ProveedorClaude(orden_base=["claude"]).modo_predeterminado(tmp_path) == "dontAsk"
	_claude_usuario({"permissions": {"defaultMode": "inventado"}})
	assert ProveedorClaude(orden_base=["claude"]).modo_predeterminado(tmp_path) == "default"


def test_claude_settings_ilegible_no_rompe(tmp_path):
	ruta = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
	ruta.parent.mkdir(parents=True, exist_ok=True)
	ruta.write_text("{ esto no es json", encoding="utf-8")
	assert ProveedorClaude(orden_base=["claude"]).modo_predeterminado(tmp_path) == "default"


def test_claude_sin_modo_no_fuerza_permission_mode(tmp_path):
	proveedor = ProveedorClaude(orden_base=["claude"])
	sin_modo = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda e: None).construir_orden()
	assert "--permission-mode" not in sin_modo
	automatico = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, modo_permiso="auto"), lambda e: None)
	orden = automatico.construir_orden()
	assert orden[orden.index("--permission-mode") + 1] == "auto"


# ── Codex ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("toml", "esperado"), [
	("", "espacio_trabajo"),
	('sandbox_mode = "read-only"\n', "solo_lectura"),
	('sandbox_mode = "workspace-write"\napproval_policy = "never"\n', "sin_preguntas"),
	('sandbox_mode = "danger-full-access"\n', "acceso_total"),
	('approval_policy = "on-request"\n', "espacio_trabajo"),
	('profile = "seguro"\nsandbox_mode = "danger-full-access"\n[profiles.seguro]\nsandbox_mode = "read-only"\n', "solo_lectura"),
	("esto = no es toml [", "espacio_trabajo"),
])
def test_codex_propone_su_config_toml(tmp_path, toml, esperado):
	_codex_usuario(toml)
	assert ProveedorCodex().modo_predeterminado(tmp_path) == esperado


def test_codex_el_proyecto_manda_clave_a_clave(tmp_path):
	_codex_usuario('approval_policy = "never"\n')
	proyecto = tmp_path / ".codex" / "config.toml"
	proyecto.parent.mkdir()
	proyecto.write_text('sandbox_mode = "workspace-write"\n', encoding="utf-8")
	assert ProveedorCodex().modo_predeterminado(tmp_path) == "sin_preguntas"


# ── Antigravity CLI y agentes ACP ────────────────────────────────────────────


@pytest.mark.parametrize(("valor", "esperado"), [
	(None, MODO_REVISAR), ("request-review", MODO_REVISAR), ("proceed-in-sandbox", MODO_SANDBOX),
	("always-proceed", MODO_SIN_PREGUNTAS),
])
def test_antigravity_propone_su_tool_permission(valor, esperado):
	from app.services.agentes import antigravity

	if valor is not None:
		_escribir_json(antigravity.ruta_ajustes_cli(), {"toolPermission": valor})
	assert ProveedorAntigravity().modo_predeterminado() == esperado


def test_acp_propone_el_modo_con_el_que_abrio_el_agente():
	proveedor = ProveedorAcp("prueba", "Prueba", "Agente de prueba", ["prueba.exe"])
	assert proveedor.modo_predeterminado() == ""
	modos = [OpcionLista("default", "Preguntar"), OpcionLista("autoEdit", "Aceptar ediciones"), OpcionLista("plan", "Plan")]
	proveedor.recordar(modos=modos, modo_inicial="autoEdit")
	assert proveedor.modo_predeterminado() == "autoEdit"
	# Retomar una conversación (sin modo inicial) no cambia lo aprendido.
	proveedor.recordar(modos=modos, modo_inicial=None)
	assert proveedor.modo_predeterminado() == "autoEdit"
	proveedor.recordar(modos=modos, modo_inicial="desconocido")
	assert proveedor.modo_predeterminado() == "default"


# ── Diálogo «Nueva sesión» y «Continuar aquí» ────────────────────────────────


@pytest.fixture
def dialogo_nueva(app_wx, tmp_path):
	from app.core.configuracion import Configuracion
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones
	from app.ui.dialogos.nueva_sesion import DialogoNuevaSesion

	almacen = Almacen(tmp_path / "n.db")
	gestor = GestorSesiones(almacen, {"claude": ProveedorClaude(orden_base=["claude"]), "codex": ProveedorCodex()},
		lambda c, e: None)
	creados = []

	def crear(carpeta: Path, agente: str = "claude") -> DialogoNuevaSesion:
		dialogo = DialogoNuevaSesion(None, gestor, almacen, Configuracion(tmp_path / "s.json"), carpeta=carpeta, agente=agente)
		creados.append(dialogo)
		return dialogo

	yield crear
	for dialogo in creados:
		dialogo.Destroy()
	almacen.cerrar()


def _modo_elegido(dialogo) -> str:
	return dialogo._modos[dialogo.modo.GetSelection()].id


def test_nueva_sesion_propone_el_modo_automatico(dialogo_nueva, tmp_path):
	_claude_usuario({"permissions": {"defaultMode": "auto"}})
	dialogo = dialogo_nueva(tmp_path)
	assert _modo_elegido(dialogo) == "auto"
	assert "Configuración de los agentes" in dialogo.modo.GetHelpText()


def test_nueva_sesion_cambia_el_modo_con_la_carpeta_salvo_si_se_eligio_a_mano(dialogo_nueva, tmp_path):
	import wx

	_claude_usuario({"permissions": {"defaultMode": "auto"}})
	proyecto = tmp_path / "proyecto"
	_escribir_json(proyecto / ".claude" / "settings.local.json", {"permissions": {"defaultMode": "plan"}})
	dialogo = dialogo_nueva(tmp_path)
	dialogo.carpeta.SetValue(str(proyecto))
	dialogo._proponer_modo()
	assert _modo_elegido(dialogo) == "plan"

	dialogo.modo.SetSelection(next(i for i, m in enumerate(dialogo._modos) if m.id == "acceptEdits"))
	dialogo.modo.ProcessEvent(wx.CommandEvent(wx.wxEVT_CHOICE, dialogo.modo.GetId()))
	dialogo.carpeta.SetValue(str(tmp_path))
	dialogo._proponer_modo()
	assert _modo_elegido(dialogo) == "acceptEdits"


def test_nueva_sesion_codex_propone_su_config(dialogo_nueva, tmp_path):
	_codex_usuario('sandbox_mode = "read-only"\n')
	assert _modo_elegido(dialogo_nueva(tmp_path, "codex")) == "solo_lectura"


def test_continuar_aqui_usa_el_modo_inicial(tmp_path, monkeypatch):
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones

	_claude_usuario({"permissions": {"defaultMode": "auto"}})
	almacen = Almacen(tmp_path / "n.db")
	gestor = GestorSesiones(almacen, {"claude": ProveedorClaude(orden_base=["claude"])}, lambda c, e: None)
	monkeypatch.setattr(gestor, "fuente_externa", lambda clave: {"carpeta": str(tmp_path), "titulo": "De la terminal"})
	capturados = []
	monkeypatch.setattr(gestor, "nueva_sesion", lambda proveedor, ajustes: capturados.append(ajustes) or "ctrlia:x")
	try:
		gestor.continuar_aqui("claude:aaaa-bbbb")
	finally:
		almacen.cerrar()
	assert capturados[0].modo_permiso == "auto"
