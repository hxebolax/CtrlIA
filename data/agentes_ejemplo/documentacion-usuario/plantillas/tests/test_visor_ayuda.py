"""Pruebas del visor de ayuda con wxPython real (PLANTILLA del agente «documentacion-usuario»).

Las ventanas se crean SIN mostrarlas (``mostrar=False``) y los avisos
modales se sustituyen por una lista. Comprueban el modo texto siempre y el
modo HTML solo si ``markdown`` y el motor Edge están disponibles. Que NVDA
lea bien el visor es una prueba manual.
"""

from __future__ import annotations

import sys
import time
import types

import pytest

wx = pytest.importorskip("wx")

from app.core import documentacion  # noqa: E402
from app.ui import visor_ayuda  # noqa: E402

MANUAL = """\
# Manual de prueba

Introducción con **negrita**, `código` y un [enlace](https://example.org).

## Instalación

1. Extraiga el ZIP.
2. Pulse Intro sobre el programa.

## Teclas

| Atajo | Acción |
|---|---|
| Ctrl+N | Nuevo documento |
| F1 | Manual |

### Detalle

- Elemento de lista
  - Elemento anidado

<!-- comentario que no se lee -->

```
código tal cual
```
"""


# La wx.App de la sesión. Esta referencia de módulo la mantiene viva hasta el final:
# sin ella, Python destruiría la aplicación recién creada al salir de la fixture.
_APLICACION_WX = None


@pytest.fixture(scope="module")
def app():
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
def avisos(monkeypatch):
	mensajes: list[str] = []
	monkeypatch.setattr(visor_ayuda, "avisar", lambda padre, mensaje: mensajes.append(mensaje))
	return mensajes


@pytest.fixture(autouse=True)
def cerrar_visores(app):
	yield
	for visor in list(visor_ayuda._abiertos.values()):
		if visor:
			visor.Destroy()
	visor_ayuda._abiertos.clear()
	wx.Yield()


def _pulsar(visor, codigo, modificadores=wx.MOD_NONE):
	evento = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
	evento.SetKeyCode(codigo)
	evento.SetControlDown(bool(modificadores & wx.MOD_CONTROL))
	visor._al_pulsar(evento)


def test_markdown_a_texto():
	texto, encabezados = visor_ayuda.markdown_a_texto(MANUAL)
	assert [(n, t) for n, t, _p in encabezados] == [(1, "Manual de prueba"), (2, "Instalación"), (2, "Teclas"), (3, "Detalle")]
	for _nivel, titulo, posicion in encabezados:
		assert texto[posicion:].startswith(titulo)
	assert "Introducción con negrita, código y un enlace (https://example.org)." in texto
	assert "Ctrl+N: Nuevo documento" in texto
	assert "• Elemento de lista\n  • Elemento anidado" in texto
	assert "1. Extraiga el ZIP." in texto
	assert "código tal cual" in texto
	for marca in ("#", "**", "|", "comentario que no se lee", "```"):
		assert marca not in texto


def test_visor_en_modo_texto(app, avisos):
	visor = visor_ayuda.mostrar_documento(None, "Manual de prueba", MANUAL, forzar_texto=True, mostrar=False)
	assert visor.modo == "texto" and visor.web is None
	assert not visor.IsShown()
	assert visor.contenido.IsEditable() is False
	assert visor.secciones.GetStrings() == ["Manual de prueba", "Instalación", "Teclas", "Detalle"]

	visor.ir_a_seccion(2)
	punto = visor.contenido.GetInsertionPoint()
	assert visor.contenido.GetRange(punto, punto + len("Teclas")) == "Teclas"

	assert visor.buscar("nuevo documento")
	assert visor.contenido.GetStringSelection() == "Nuevo documento"
	assert visor.buscar("manual")  # da la vuelta si hace falta
	assert not visor.buscar("inexistente")
	assert avisos == ["No se encontró «inexistente»."]

	# Si ya está abierto, se reutiliza.
	assert visor_ayuda.mostrar_documento(None, "Manual de prueba", MANUAL, forzar_texto=True, mostrar=False) is visor


def test_teclas_del_visor(app, avisos, monkeypatch):
	pedidas: list[bool] = []
	monkeypatch.setattr(visor_ayuda.VisorAyuda, "pedir_busqueda", lambda self: pedidas.append(True))
	visor = visor_ayuda.mostrar_documento(None, "Teclas", MANUAL, forzar_texto=True, mostrar=False)
	_pulsar(visor, ord("F"), wx.MOD_CONTROL)
	_pulsar(visor, wx.WXK_F3)  # sin búsqueda previa, pide el texto
	assert pedidas == [True, True]
	_pulsar(visor, wx.WXK_ESCAPE)
	assert "Teclas" not in visor_ayuda._abiertos
	wx.Yield()  # wx destruye las ventanas de nivel superior al quedar ocioso
	assert not visor


def test_abrir_manual_desde_data_docs(app, avisos, tmp_path, monkeypatch):
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
	(tmp_path / "docs").mkdir()
	(tmp_path / "docs" / "manual.md").write_text(MANUAL, encoding="utf-8")
	visor = visor_ayuda.abrir_manual(None, forzar_texto=True, mostrar=False)
	assert visor is not None and visor.GetTitle() == "Manual de prueba"
	assert visor_ayuda.abrir_documento(None, "no_existe_nunca.md", "Nada", mostrar=False) is None
	assert "no_existe_nunca.md" in avisos[0]


def test_menu_ayuda(app):
	marco = wx.Frame(None)
	try:
		menu = visor_ayuda.crear_menu_ayuda(marco)
		etiquetas = [elemento.GetItemLabel() for elemento in menu.GetMenuItems()]
		assert etiquetas[0] == "&Manual de uso\tF1"
		assert all("&" in etiqueta for etiqueta in etiquetas)
	finally:
		marco.Destroy()


def test_novedades_solo_una_vez(app, tmp_path, monkeypatch):
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
	changelog = "# Novedades\n\n## Versión 1.1.0 - 2026-10-15\n\n### Mejoras\n\n- Más rápido.\n\n## Versión 1.0.0 - 2026-09-25\n\n- Primera.\n"
	# Primera ejecución (instalación nueva): nada.
	assert visor_ayuda.mostrar_novedades_si_procede(None, "1.0.0", changelog, forzar_texto=True, mostrar=False) is None
	# Tras actualizar: se muestran las de 1.1.0.
	visor = visor_ayuda.mostrar_novedades_si_procede(None, "1.1.0", changelog, forzar_texto=True, mostrar=False)
	assert visor is not None
	assert visor.GetTitle() == "Novedades de la versión 1.1.0"
	texto = visor.contenido.GetValue()
	assert "Más rápido." in texto and "Primera." not in texto
	assert documentacion.leer_ultima_vista() == "1.1.0"
	# Segunda vez con la misma versión: nada.
	assert visor_ayuda.mostrar_novedades_si_procede(None, "1.1.0", changelog, forzar_texto=True, mostrar=False) is None


def test_markdown_a_html_estructurado():
	pytest.importorskip("markdown")
	pagina = visor_ayuda.markdown_a_html(MANUAL, "Manual de prueba")
	assert '<html lang="es">' in pagina
	assert "<title>Manual de prueba</title>" in pagina
	assert pagina.count("<h1") == 1 and pagina.count("<h2") == 2 and "<h3" in pagina
	assert "<th>Atajo</th>" in pagina and "<ol>" in pagina and "<ul>" in pagina
	assert "<main>" in pagina


def test_visor_en_modo_html(app, avisos):
	if not visor_ayuda.html_disponible():
		pytest.skip("Sin markdown o sin el motor Edge de wx.html2")
	visor = visor_ayuda.mostrar_documento(None, "Manual HTML", MANUAL, mostrar=False)
	assert visor.modo == "html" and visor.web is not None and visor.contenido is None
	# Edge carga la página de forma asíncrona y necesita el bucle de eventos
	# (con wx.Yield no termina): se ejecuta MainLoop hasta tener respuesta.
	resultado: dict[str, str] = {}
	inicio = time.monotonic()

	def consultar():
		correcto, valor = visor.web.RunScript(
			"document.querySelectorAll('h1,h2,h3').length + '|' + document.documentElement.lang + '|' + document.title"
		)
		if correcto and valor and not valor.startswith("0"):
			resultado["pagina"] = valor
			app.ExitMainLoop()
		elif time.monotonic() - inicio > 15:
			app.ExitMainLoop()
		else:
			wx.CallLater(300, consultar)

	wx.CallLater(300, consultar)
	app.MainLoop()
	if "pagina" not in resultado:
		pytest.skip("La vista web no terminó de cargar en 15 segundos")
	assert resultado["pagina"] == "4|es|Manual HTML"


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


def test_aviso_con_boton_en_espanol_sin_dialogos_del_proyecto(app, monkeypatch):
	_sin_dialogos_del_proyecto(monkeypatch)
	vistos = _capturar_message_dialog(monkeypatch)
	visor_ayuda.avisar(None, "No se encontró «nada».")
	assert vistos == [("Ayuda", "No se encontró «nada».", "&Aceptar")]


def test_pedir_texto_con_botones_en_espanol_sin_dialogos_del_proyecto(app, monkeypatch):
	_sin_dialogos_del_proyecto(monkeypatch)
	vistos = {}

	def mostrar(dialogo):
		hijos = list(dialogo.GetChildren())
		vistos["titulo"] = dialogo.GetTitle()
		vistos["botones"] = {dialogo.FindWindow(i).GetLabel() for i in (wx.ID_OK, wx.ID_CANCEL)}
		campo = next(h for h in hijos if isinstance(h, wx.TextCtrl))
		anterior = hijos[hijos.index(campo) - 1]
		vistos["etiqueta"] = anterior.GetLabel() if isinstance(anterior, wx.StaticText) else None
		vistos["valor"] = campo.GetValue()
		campo.SetValue("teclas")
		return wx.ID_OK

	monkeypatch.setattr(wx.Dialog, "ShowModal", mostrar)
	assert visor_ayuda.pedir_texto(None, "&Texto que se busca:", "Buscar", "previo") == "teclas"
	assert vistos == {
		"titulo": "Buscar", "botones": {"&Aceptar", "&Cancelar"},
		"etiqueta": "&Texto que se busca:", "valor": "previo",
	}
	monkeypatch.setattr(wx.Dialog, "ShowModal", lambda dialogo: wx.ID_CANCEL)
	assert visor_ayuda.pedir_texto(None, "&Texto:", "Buscar") is None


def test_usa_los_dialogos_del_proyecto_si_existen(app, monkeypatch):
	recibidos = []
	_con_dialogos_del_proyecto(
		monkeypatch,
		mostrar_mensaje=lambda padre, mensaje, titulo, icono=None: recibidos.append(("mensaje", mensaje, titulo)),
		mostrar_error=lambda padre, mensaje, titulo: recibidos.append(("error", mensaje, titulo)),
		pedir_texto=lambda padre, etiqueta, titulo, valor="": f"{etiqueta}|{valor}",
	)
	visor_ayuda.avisar(None, "Hola")
	assert recibidos == [("mensaje", "Hola", "Ayuda")]
	assert visor_ayuda.pedir_texto(None, "&Texto:", "Buscar", "previo") == "&Texto:|previo"


def test_pedir_busqueda_busca_lo_escrito(app, avisos, monkeypatch):
	visor = visor_ayuda.mostrar_documento(None, "Manual de prueba", MANUAL, forzar_texto=True, mostrar=False)
	buscados = []
	monkeypatch.setattr(visor, "buscar", buscados.append)
	monkeypatch.setattr(visor_ayuda, "pedir_texto", lambda *a, **k: "  Teclas  ")
	visor.pedir_busqueda()
	monkeypatch.setattr(visor_ayuda, "pedir_texto", lambda *a, **k: None)  # cancelado
	visor.pedir_busqueda()
	assert buscados == ["Teclas"]
