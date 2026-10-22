"""Pruebas de las señales de la app al complemento (PLANTILLA del agente «complemento-nvda»).

Usan una ventana real de wxPython: comprueban que la propiedad se pone, se lee
y se quita en el identificador de ventana (HWND) que verá NVDA.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nvda_falso  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Propiedades de ventana de Windows")

senales = nvda_falso.cargar_modulo("senales_nvda_pruebas", nvda_falso.RAIZ / "app" / "ui" / "senales_nvda.py")


# La wx.App de la sesión. Esta referencia de módulo la mantiene viva hasta el final:
# sin ella, Python destruiría la aplicación recién creada al salir de la fixture.
_APLICACION_WX = None


@pytest.fixture
def lista():
	wx = pytest.importorskip("wx")
	# La wx.App de la sesión si ya hay una; nunca se destruye (otra wx.App tras
	# destruir la primera deja sin aplicación al resto de la batería).
	global _APLICACION_WX
	_APLICACION_WX = wx.GetApp() or wx.App(False)
	marco = wx.Frame(None)
	control = wx.ListCtrl(marco, style=wx.LC_REPORT)
	yield control
	marco.Destroy()


def test_marcar_leer_y_quitar(lista):
	hwnd = lista.GetHandle()
	assert senales.leer_marca(hwnd) == 0
	assert senales.marcar_movimiento_silencioso(hwnd)
	assert senales.leer_marca(hwnd) != 0
	senales.quitar_marca(hwnd)
	assert senales.leer_marca(hwnd) == 0


def test_sin_ventana_no_hace_nada():
	assert senales.marcar_movimiento_silencioso(0) is False
	assert senales.leer_marca(0) == 0
	senales.quitar_marca(0)
