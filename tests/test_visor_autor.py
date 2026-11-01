"""El visor de un mensaje dice quién lo escribió: el autor es siempre el encabezado 1 (AIChat #346).

Antes solo se añadía un encabezado 1 si el mensaje no traía ninguno, y entonces era el título de
la ventana: con encabezados propios no se decía quién hablaba. Nada se muestra ni habla.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

pytest.importorskip("markdown")
wx = pytest.importorskip("wx")

from app.services.gestor import MensajeVista  # noqa: E402
from app.ui import visor_ayuda  # noqa: E402
from app.ui.comun import markdown_seguro  # noqa: E402
from app.ui.vp_mensajes import MixinMensajes  # noqa: E402


def _encabezados(pagina: str) -> list[tuple[str, str]]:
	return [(n, re.sub("<[^>]+>", "", t)) for n, t in re.findall(r"<h([1-6])[^>]*>(.*?)</h\1>", pagina, re.DOTALL)]


def test_con_encabezados_propios_el_autor_va_delante_y_los_suyos_bajan():
	pagina = visor_ayuda.markdown_a_html("# Plan\n\n## Pasos\n\nTexto.", "Mensaje 3", autor="Claude Code")
	assert _encabezados(pagina) == [("1", "Claude Code"), ("2", "Plan"), ("3", "Pasos")]
	assert pagina.count("<h1") == 1 and "</h1>\n<h2" in pagina


def test_sin_encabezados_propios_el_encabezado_es_el_autor_y_no_el_titulo():
	pagina = visor_ayuda.markdown_a_html("Hola.", "Mensaje 3 de Tú (10:15)", autor="Tú")
	assert _encabezados(pagina) == [("1", "Tú")]
	assert "<title>Mensaje 3 de Tú (10:15)</title>" in pagina


def test_el_nivel_6_no_pasa_de_ahi():
	assert visor_ayuda.bajar_encabezados('<h5 id="a">x</h5><h6>y</h6>') == '<h6 id="a">x</h6><h6>y</h6>'


def test_el_autor_se_escapa_y_lo_escrito_en_el_mensaje_no_cuenta_como_encabezado():
	pagina = visor_ayuda.markdown_a_html(markdown_seguro("Un <h1>falso</h1>\n\n`<h2>` y más"), "T", autor="A <b>")
	assert _encabezados(pagina) == [("1", "A &lt;b&gt;")]
	assert "&lt;h1&gt;falso" in pagina


def test_el_codigo_con_encabezados_no_se_toca():
	pagina = visor_ayuda.markdown_a_html("```html\n<h1>hola</h1>\n```", "T", autor="Codex")
	assert _encabezados(pagina) == [("1", "Codex")]
	assert "&lt;h1&gt;hola&lt;/h1&gt;" in pagina


def test_sin_autor_el_manual_sigue_igual():
	manual = visor_ayuda.markdown_a_html("# Manual\n\n## Uno", "Ayuda")
	assert _encabezados(manual) == [("1", "Manual"), ("2", "Uno")]
	assert _encabezados(visor_ayuda.markdown_a_html("Sin título.", "Ayuda")) == [("1", "Ayuda")]


def test_el_guion_de_las_teclas_y_main_siguen_en_la_pagina():
	pagina = visor_ayuda.markdown_a_html("# Plan", "T", teclas={"Alt+3": "respuesta"}, autor="Claude Code")
	assert '"Alt+3": "respuesta"' in pagina and "<main>" in pagina


def test_ver_mensaje_pasa_quien_lo_escribio(monkeypatch):
	abiertos: list[dict] = []
	monkeypatch.setattr(visor_ayuda, "mostrar_documento", lambda *a, **k: abiertos.append(k))
	ventana = SimpleNamespace(_mensajes=[], sesion_actual="c", _quien=lambda m: "Codex", opcion=lambda c, d=None: d,
		numero_mensaje=lambda i: 1)
	MixinMensajes.ver_mensaje(ventana, MensajeVista("asistente", "# Plan", 1.0))
	assert abiertos[0]["autor"] == "Codex"
	assert set(abiertos[0]["teclas"]) == {"Alt+3", "Ctrl+r", "Ctrl+Alt+n"}


def test_el_visor_guarda_el_autor_y_el_modo_texto_no_cambia(app_wx):
	visor = visor_ayuda.mostrar_documento(None, "Mensaje con autor", "# Plan\n\nHecho.", forzar_texto=True, mostrar=False, autor="Claude Code")
	try:
		assert visor.autor == "Claude Code"
		assert visor.contenido.GetValue().startswith("Plan")
		assert visor.secciones.GetCount() == 1
	finally:
		visor.Destroy()
		visor_ayuda._abiertos.pop("Mensaje con autor", None)  # noqa: SLF001
