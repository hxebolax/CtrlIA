"""Quien ve la pantalla y no usa lector puede apagar la voz de Windows (1.5.3).

Sin lector en marcha, ``accessible_output2`` acaba en SAPI5, que siempre está
activa: Ctrl+IA leía las respuestas en voz alta. Aquí nunca suena nada: la salida
es una imitación.
"""

from __future__ import annotations

import pytest

from app.ui.accesibilidad import voz


class SalidaFalsa:
	"""``Auto`` con un lector en marcha o solo con SAPI5."""

	def __init__(self, de_windows: bool) -> None:
		self.de_windows = de_windows
		self.dicho: list[str] = []
		self.braille_dicho: list[str] = []

	def is_system_output(self) -> bool:
		return self.de_windows

	def speak(self, texto, interrupt=False) -> None:
		self.dicho.append(texto)

	def braille(self, texto) -> None:
		self.braille_dicho.append(texto)


@pytest.fixture
def salida(monkeypatch):
	def _crear(de_windows: bool, activada: bool) -> SalidaFalsa:
		falsa = SalidaFalsa(de_windows)
		monkeypatch.setattr(voz, "_obtener_salida", lambda: falsa)
		monkeypatch.setitem(voz._config, "voz_windows", activada)  # noqa: SLF001
		return falsa

	return _crear


def test_de_fabrica_habla_con_la_voz_de_windows(salida):
	falsa = salida(True, True)
	voz._emitir("Hola", True)  # noqa: SLF001
	assert falsa.dicho == ["Hola"]
	assert not voz.sin_voz_por_no_haber_lector()


def test_apagada_y_sin_lector_no_habla_pero_sigue_el_braille(salida):
	falsa = salida(True, False)
	voz._emitir("Hola", True)  # noqa: SLF001
	assert falsa.dicho == []
	assert falsa.braille_dicho == ["Hola"]
	assert voz.sin_voz_por_no_haber_lector()


def test_apagada_no_calla_al_lector(salida):
	falsa = salida(False, False)
	voz._emitir("Hola", True)  # noqa: SLF001
	assert falsa.dicho == ["Hola"]
	assert not voz.sin_voz_por_no_haber_lector()


def test_lo_que_pide_la_persona_suena_igual(salida):
	falsa = salida(True, False)
	voz._emitir("Léelo", True, siempre=True)  # noqa: SLF001
	assert falsa.dicho == ["Léelo"]


def test_ante_la_duda_se_habla(salida, monkeypatch):
	falsa = salida(True, False)

	def falla() -> bool:
		raise OSError("sin acceso")

	monkeypatch.setattr(falsa, "is_system_output", falla)
	voz._emitir("Hola", True)  # noqa: SLF001
	assert falsa.dicho == ["Hola"]


def test_la_biblioteca_marca_sapi_como_voz_del_sistema():
	sapi5 = pytest.importorskip("accessible_output2.outputs.sapi5")
	base = pytest.importorskip("accessible_output2.outputs.auto")
	assert sapi5.SAPI5.system_output is True
	assert hasattr(base.Auto, "is_system_output")


def test_los_avisos_importantes_salen_en_un_cuadro_si_nadie_los_oiria(app_wx, monkeypatch):
	from app.ui import avisos
	from app.ui.accesibilidad import dialogos

	mostrados: list[str] = []
	monkeypatch.setattr(dialogos, "mostrar_mensaje", lambda padre, texto, titulo="": mostrados.append(texto))
	monkeypatch.setattr(voz, "sin_voz_por_no_haber_lector", lambda: True)
	avisos.notificar("Ha fallado")
	assert mostrados == ["Ha fallado"]


def test_ctrl_h_lo_ensena_si_nadie_lo_oiria(app_wx, monkeypatch):
	from app.ui.accesibilidad import ayuda, dialogos

	mostrados: list[str] = []
	monkeypatch.setattr(dialogos, "mostrar_mensaje", lambda padre, texto, titulo="": mostrados.append(texto))
	monkeypatch.setattr(voz, "sin_voz_por_no_haber_lector", lambda: True)
	ayuda._decir("Explicación", None)  # noqa: SLF001
	assert mostrados == ["Explicación"]


def test_de_fabrica_sigue_activada_para_quien_no_ve():
	from app.core.configuracion import VALORES_POR_DEFECTO

	assert VALORES_POR_DEFECTO["anuncios"]["voz_windows"] is True
