"""Pruebas de los otros agentes ACP conocidos (``app/services/agentes/acp_conocidos.py``) y del catálogo."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.agentes import ORDEN, acp, acp_conocidos, crear_proveedores
from app.ui.comun import NOMBRES_AGENTE

IDS = ["opencode", "goose", "copilot", "qwen", "kilo", "cline"]


def test_fichas_completas() -> None:
	assert [agente.id for agente in acp_conocidos.AGENTES] == IDS
	for agente in acp_conocidos.AGENTES:
		assert agente.id not in ORDEN
		assert agente.nombre and agente.descripcion and agente.web.startswith("https://")
		assert agente.argumentos
		assert agente.nativos or agente.paquetes
		assert agente.instrucciones.startswith("Para usar %s" % agente.nombre.split()[0])
		assert NOMBRES_AGENTE[agente.id]


def test_no_aparecen_por_omision() -> None:
	proveedores = crear_proveedores({"otros_acp": False})
	assert not set(IDS) & set(proveedores)


def test_aparecen_al_activarlos_detras_de_los_de_serie() -> None:
	proveedores = crear_proveedores({"otros_acp": True})
	ids = list(proveedores)
	assert ids[-len(IDS):] == IDS
	for identificador in IDS:
		proveedor = proveedores[identificador]
		assert "ANTHROPIC_API_KEY" in proveedor.entorno_quitar
		assert "OPENAI_API_KEY" in proveedor.entorno_quitar
		assert proveedor.capacidades.permisos_interactivos


def test_un_personalizado_con_el_mismo_id_gana() -> None:
	proveedores = crear_proveedores({
		"otros_acp": True,
		"acp_personalizados": [{"id": "goose", "nombre": "Mi Goose", "orden": ["C:/no/existe/goose.exe", "acp"]}],
	})
	assert proveedores["goose"].nombre == "Mi Goose"


def test_orden_con_ejecutable_nativo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	programa = tmp_path / "opencode.exe"
	programa.write_bytes(b"MZ falso")
	monkeypatch.setattr(acp, "buscar_ejecutable", lambda nombres: programa if "opencode" in nombres else None)
	monkeypatch.setattr(acp, "buscar_paquete_npm", lambda *_a, **_k: None)
	proveedor = acp_conocidos.proveedores_conocidos()[0]
	assert proveedor.resolver_orden() == [str(programa), "acp"]
	assert proveedor.orden_instalacion() == ["cmd.exe", "/k", "npm install -g opencode-ai"]


def test_orden_con_paquete_npm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	node = tmp_path / "node.exe"
	node.write_bytes(b"MZ falso")
	script = tmp_path / "cli.js"
	script.write_text("// falso", encoding="utf-8")
	monkeypatch.setattr(acp, "buscar_ejecutable", lambda nombres: None)
	monkeypatch.setattr(acp, "buscar_paquete_npm", lambda paquete, nombre="": ([str(node), str(script)], "1.0.0")
		if paquete == "@github/copilot" else None)
	proveedor = next(p for p in acp_conocidos.proveedores_conocidos() if p.id == "copilot")
	assert proveedor.resolver_orden() == [str(node), str(script), "--acp"]


def test_no_instalado(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(acp, "buscar_ejecutable", lambda nombres: None)
	monkeypatch.setattr(acp, "buscar_paquete_npm", lambda *_a, **_k: None)
	estado = next(p for p in acp_conocidos.proveedores_conocidos() if p.id == "qwen").detectar()
	assert not estado.disponible
	assert "npm install -g @qwen-code/qwen-code" in estado.mensaje


def test_excluir() -> None:
	assert [p.id for p in acp_conocidos.proveedores_conocidos(excluir=["goose", "cline"])] == ["opencode", "copilot", "qwen", "kilo"]
