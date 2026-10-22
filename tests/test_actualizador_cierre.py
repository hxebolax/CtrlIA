"""Cierre de la ventana principal tras «Reiniciar ahora»: no vuelve a preguntar.

Usa la ventana de prueba de ``test_ventana_principal`` (Claude falso, base de
datos temporal, sin mostrarla) y los modales simulados.
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")

from tests.ayudas_wx import procesar_eventos  # noqa: E402
from tests.test_ventana_principal import ventana  # noqa: E402, F401 - fixture compartida


def _preparar(marco, monkeypatch, ocupadas: int) -> None:
	marco.config.establecer_varias({"general.confirmar_salida": True})
	monkeypatch.setattr(marco.gestor, "sesiones_ocupadas", lambda: ["s"] * ocupadas)


def test_salir_normal_sigue_preguntando(ventana, monkeypatch, dialogo_modal):  # noqa: F811
	_preparar(ventana, monkeypatch, 2)
	dialogo_modal.responder(wx.ID_NO)
	ventana.Close()
	procesar_eventos()
	assert ventana  # vetado: sigue abierta
	assert any("se detendrán" in t for t in dialogo_modal.mostrados[-1].textos)


@pytest.mark.parametrize("ocupadas", [0, 2])
def test_salir_para_actualizar_no_pregunta(ventana, monkeypatch, dialogo_modal, ocupadas):  # noqa: F811
	_preparar(ventana, monkeypatch, ocupadas)
	ventana.salir_para_actualizar()
	procesar_eventos()
	assert dialogo_modal.mostrados == []  # ni «¿Quieres salir?» ni el aviso de agentes
	assert not ventana  # destruida
