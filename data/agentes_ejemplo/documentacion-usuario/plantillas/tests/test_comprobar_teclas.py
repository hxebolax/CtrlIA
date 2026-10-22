"""Pruebas del comprobador de atajos (PLANTILLA del agente «documentacion-usuario»).

Crean un proyecto de ejemplo en ``tmp_path`` con atajos sin documentar,
documentados que ya no existen y duplicados, y comprueban el informe.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools import comprobar_teclas as ct

CODIGO = '''\
import wx


class VentanaPrincipal(wx.Frame):
	def crear_menus(self):
		archivo = wx.Menu()
		self.Bind(wx.EVT_MENU, self.al_nuevo, archivo.Append(wx.ID_NEW, _("&Nuevo\\tCtrl+N")))
		archivo.Append(wx.ID_SAVE, _("&Guardar\\tCtrl+S"))
		archivo.Append(ID_SINCRONIZAR, _("S&incronizar\\tCtrl+S"))
		archivo.Append(wx.ID_EXIT, "&Salir\\tCtrl+Q")
		exportar = wx.MenuItem(archivo, ID_EXPORTAR, _("&Exportar\\tCtrl+Shift+E"))
		tabla = wx.AcceleratorTable([
			(wx.ACCEL_CTRL, ord("S"), wx.ID_SAVE),
			(wx.ACCEL_NORMAL, wx.WXK_F5, ID_ACTUALIZAR),
			wx.AcceleratorEntry(wx.ACCEL_CTRL | wx.ACCEL_ALT, ord("p"), ID_PREFERENCIAS),
		])
		cabeceras = "Nombre\\tTamaño"
		columnas = "Nombre\\tA"
		return exportar, tabla, cabeceras, columnas

	def al_pulsar(self, evento):
		# atajo: Alt+1 a Alt+3 ir a la pestaña
		evento.Skip()


class DialogoEditor(wx.Dialog):
	def crear(self, menu):
		menu.Append(wx.ID_ANY, _("&Guardar\\tCtrl+S"))
'''

TECLAS = """\
# Teclas

## Ventana principal

| Atajo | Acción |
|---|---|
| Ctrl+N | Nuevo |
| **Ctrl+S** | Guardar |
| Ctrl+Q | Salir |
| F5 | Actualizar |
| Ctrl+Alt+P | Preferencias |
| Alt+1 a Alt+3 | Ir a la pestaña |
| Ctrl+J | Función retirada |
| Ctrl+N | Otra cosa distinta |

## Editor

- **Ctrl+S**: guardar el texto
- **Ctrl+G**: ir a la línea

## En cualquier diálogo

- **Escape**: cerrar sin guardar
- **Ctrl+B**: no se comprueba

Un párrafo que menciona Ctrl+K no es un atajo documentado.
"""


def _proyecto(tmp_path: Path, teclas: str = TECLAS, codigo: str = CODIGO) -> Path:
	(tmp_path / "app" / "ui").mkdir(parents=True)
	(tmp_path / "app" / "ui" / "ventana.py").write_text(codigo, encoding="utf-8")
	(tmp_path / "docs").mkdir()
	(tmp_path / "docs" / "teclas.md").write_text(teclas, encoding="utf-8")
	return tmp_path


@pytest.mark.parametrize(
	("texto", "esperado"),
	[
		("ctrl+shift+s", "Ctrl+Mayús+S"),
		("Mayús+Ctrl+s", "Ctrl+Mayús+S"),
		("Ctrl-Alt-Del", "Ctrl+Alt+Supr"),
		("Ctrl++", "Ctrl++"),
		("Ctrl+,", "Ctrl+,"),
		("f12", "F12"),
		("Return", "Intro"),
		("Alt+PgDn", "Alt+AvPág"),
		("Hiper+S", None),
		("Ctrl+Nada", None),
	],
)
def test_normalizar(texto, esperado):
	assert ct.normalizar(texto) == esperado


def test_combinaciones_en_texto():
	assert ct.combinaciones_en_texto("Alt+1 a Alt+3") == ["Alt+1", "Alt+2", "Alt+3"]
	assert ct.combinaciones_en_texto("Aplicaciones o Mayús+F10") == ["Aplicaciones", "Mayús+F10"]
	assert ct.combinaciones_en_texto("Ctrl+, (coma)") == ["Ctrl+,"]
	# Una palabra en minúscula no es una tecla aunque coincida con un nombre.
	assert ct.combinaciones_en_texto("ir al inicio de la lista") == []


def test_atajos_del_codigo(tmp_path):
	raiz = _proyecto(tmp_path)
	atajos = ct.atajos_del_proyecto(raiz)
	principal = sorted({a.combinacion for a in atajos if a.contexto == "VentanaPrincipal"})
	assert principal == sorted(
		["Ctrl+N", "Ctrl+S", "Ctrl+Q", "Ctrl+Mayús+E", "F5", "Ctrl+Alt+P", "Alt+1", "Alt+2", "Alt+3"]
	)
	editor = [a for a in atajos if a.contexto == "DialogoEditor"]
	assert [a.combinacion for a in editor] == ["Ctrl+S"]
	guardar = next(a for a in atajos if a.descripcion == "Guardar" and a.contexto == "VentanaPrincipal")
	assert guardar.destinos == {"Guardar", "wx.ID_SAVE"}
	nuevo = next(a for a in atajos if a.descripcion == "Nuevo")
	assert "wx.ID_NEW" in nuevo.destinos  # el id se encuentra aunque vaya dentro de Bind()


def test_detecta_no_documentados_obsoletos_y_duplicados(tmp_path):
	raiz = _proyecto(tmp_path)
	resultado = ct.comprobar(raiz, contextos={})
	assert [a.combinacion for a in resultado.no_documentados] == ["Ctrl+Mayús+E"]
	assert sorted(a.combinacion for a in resultado.obsoletos) == ["Ctrl+G", "Ctrl+J"]
	duplicados = {(g[0].contexto, g[0].combinacion): g for g in resultado.duplicados}
	# Guardar (menú + acelerador con el mismo id) es UNA acción; Sincronizar es otra.
	codigo = duplicados[("VentanaPrincipal", "Ctrl+S")]
	assert sorted(a.descripcion for a in codigo) == ["Guardar", "Sincronizar"]
	assert ("Ventana principal", "Ctrl+N") in duplicados  # repetido en la documentación
	assert len(resultado.duplicados) == 2
	assert resultado.problemas == 5

	informe = ct.formatear(resultado)
	assert "Atajos sin documentar: 1." in informe
	assert "- Ctrl+Mayús+E en VentanaPrincipal (app/ui/ventana.py:11): Exportar" in informe
	assert "Ctrl+J en la sección «Ventana principal» (docs/teclas.md:13)" in informe
	assert "Resultado: 5 problemas." in informe
	assert "|" not in informe  # sin tablas: se lee de corrido


def test_contextos_separan_ventanas(tmp_path):
	sin_editor = TECLAS.replace("- **Ctrl+S**: guardar el texto\n", "")
	raiz = _proyecto(tmp_path, teclas=sin_editor)
	# Sin relacionar secciones y clases, Ctrl+S cuenta como documentado (está en otra sección).
	assert "DialogoEditor" not in {a.contexto for a in ct.comprobar(raiz, contextos={}).no_documentados}
	relacion = {"Ventana principal": ["VentanaPrincipal"], "Editor": ["DialogoEditor"]}
	resultado = ct.comprobar(raiz, contextos=relacion)
	assert ("DialogoEditor", "Ctrl+S") in {(a.contexto, a.combinacion) for a in resultado.no_documentados}


def test_main_codigos_de_salida(tmp_path, capsys):
	raiz = _proyecto(tmp_path)
	assert ct.main(["--raiz", str(raiz)]) == 1
	assert "Resultado: 5 problemas." in capsys.readouterr().out

	limpio = tmp_path / "limpio"
	limpio.mkdir()
	_proyecto(
		limpio,
		teclas="# Teclas\n\n## Principal\n\n| Tecla | Acción |\n|---|---|\n| Ctrl+N | Nuevo |\n| F1 | Manual |\n",
		codigo=textwrap.dedent(
			"""\
			class Principal:
				def menus(self, menu):
					menu.Append(1, "&Nuevo\\tCtrl+N")
					menu.Append(2, "&Manual\\tF1")
			"""
		),
	)
	assert ct.main(["--raiz", str(limpio), "--contexto", "Principal=Principal"]) == 0
	assert "sin problemas" in capsys.readouterr().out


def test_sin_lista_de_teclas(tmp_path):
	raiz = _proyecto(tmp_path)
	(raiz / "docs" / "teclas.md").unlink()
	resultado = ct.comprobar(raiz)
	assert len(resultado.no_documentados) == 10
	assert "No se encontró la lista de teclas" in ct.formatear(resultado)
