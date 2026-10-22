"""Pruebas del constructor del complemento (PLANTILLA del agente «complemento-nvda»).

Construyen el .nvda-addon en ``tmp_path`` a partir de una COPIA de la carpeta
fuente (así se puede ensuciar con cachés sin tocar la real) y comprueban el
ZIP: manifiesto válido, traducciones compiladas, documentación en HTML y nada
de ``__pycache__``, ``.pyc`` ni ``.po``. No instalan nada en NVDA.
"""

from __future__ import annotations

import gettext
import io
import shutil
import sys
import types
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nvda_falso  # noqa: E402

RAIZ = nvda_falso.RAIZ
constructor = nvda_falso.cargar_modulo("construir_complemento_pruebas", RAIZ / "tools" / "construir_complemento.py")
Datos = constructor.Datos
ErrorComplemento = constructor.ErrorComplemento


def _datos(**cambios):
	valores = {
		"nombre": "miAplicacionNVDA",
		"version": "1.2.3",
		"nvda_minima": "2026.1",
		"nvda_probada": "2026.2",
		"autor": "Persona de prueba <prueba@ejemplo.es>",
		"url": "",
		"cambios": "",
	}
	valores.update(cambios)
	return Datos(**valores)


@pytest.fixture
def origen(tmp_path):
	"""Copia de nvda_addon/ con cachés y restos de editor que NO deben viajar."""
	copia = tmp_path / "nvda_addon"
	shutil.copytree(RAIZ / "nvda_addon", copia, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.mo"))
	cache = copia / "addon" / "appModules" / "__pycache__"
	cache.mkdir()
	(cache / "miaplicacion.cpython-313.pyc").write_bytes(b"basura")
	(copia / "addon" / "appModules" / "viejo.pyc").write_bytes(b"basura")
	(copia / "addon" / "Thumbs.db").write_bytes(b"basura")
	return copia


@pytest.fixture
def paquete(origen, tmp_path):
	informe = constructor.construir(origen, tmp_path / "data" / "bin", _datos())
	return informe


def _leer(ruta: Path, interno: str) -> str:
	with zipfile.ZipFile(ruta) as zip_:
		return zip_.read(interno).decode("utf-8")


# ════════════════════════════════════════════════════════════════════════════
# Paquete
# ════════════════════════════════════════════════════════════════════════════

def test_genera_el_paquete_en_data_bin(paquete, tmp_path):
	assert paquete.ruta == tmp_path / "data" / "bin" / "miAplicacionNVDA.nvda-addon"
	assert zipfile.is_zipfile(paquete.ruta)
	assert not list(paquete.ruta.parent.glob("*.tmp")), "Quedó un temporal a medias"


def test_contenido_esperado(paquete):
	with zipfile.ZipFile(paquete.ruta) as zip_:
		nombres = set(zip_.namelist())
	for esperado in (
		"manifest.ini",
		"appModules/miaplicacion.py",
		"installTasks.py",
		"locale/es/LC_MESSAGES/nvda.mo",
		"locale/es/manifest.ini",
		"doc/es/readme.html",
	):
		assert esperado in nombres, f"Falta {esperado}"


def test_sin_caches_ni_fuentes_de_traduccion(paquete):
	with zipfile.ZipFile(paquete.ruta) as zip_:
		nombres = zip_.namelist()
	assert not [n for n in nombres if "__pycache__" in n or n.endswith((".pyc", ".pyo"))]
	assert not [n for n in nombres if n.endswith((".po", ".pot", ".md")) or n.endswith("Thumbs.db")]


def test_construir_dos_veces_da_lo_mismo(origen, tmp_path):
	primero = constructor.construir(origen, tmp_path / "a", _datos())
	segundo = constructor.construir(origen, tmp_path / "a", _datos())
	assert primero.archivos == segundo.archivos
	assert primero.ruta == segundo.ruta


def test_verificar_paquete_detecta_cache(tmp_path):
	malo = tmp_path / "malo.nvda-addon"
	with zipfile.ZipFile(malo, "w") as zip_:
		zip_.writestr(
			"manifest.ini",
			'name = x\nsummary = "x"\nauthor = "x"\nversion = 1.0\n'
			"minimumNVDAVersion = 2026.1\nlastTestedNVDAVersion = 2026.2\n",
		)
		zip_.writestr("appModules/__pycache__/x.pyc", b"")
	with pytest.raises(ErrorComplemento, match="excluidos"):
		constructor.verificar_paquete(malo)


def test_no_escribe_en_la_fuente(origen, tmp_path):
	"""Compilar para comprobar la sintaxis no deja .pyc ni .mo en la carpeta fuente."""
	antes = sorted(p.relative_to(origen).as_posix() for p in origen.rglob("*"))
	constructor.construir(origen, tmp_path / "salida", _datos())
	despues = sorted(p.relative_to(origen).as_posix() for p in origen.rglob("*"))
	assert antes == despues


# ════════════════════════════════════════════════════════════════════════════
# Manifiesto
# ════════════════════════════════════════════════════════════════════════════

def test_manifiesto_sustituido_y_valido(paquete):
	campos = constructor.leer_manifiesto(_leer(paquete.ruta, "manifest.ini"))
	assert campos["name"] == "miAplicacionNVDA"
	assert campos["version"] == "1.2.3"
	assert campos["minimumNVDAVersion"] == "2026.1"
	assert campos["lastTestedNVDAVersion"] == "2026.2"
	assert campos["docFileName"] == "readme.html"
	for clave in constructor.CAMPOS_OBLIGATORIOS:
		assert campos[clave]
	assert "@" not in "".join(v for k, v in campos.items() if k != "author")


def test_opcionales_vacios_quitan_la_linea(paquete):
	campos = constructor.leer_manifiesto(_leer(paquete.ruta, "manifest.ini"))
	assert "url" not in campos
	assert "changelog" not in campos


def test_url_y_cambios(origen, tmp_path):
	informe = constructor.construir(
		origen, tmp_path, _datos(url="https://ejemplo.es/complemento", cambios="Primera versión.\nLee celdas largas.")
	)
	campos = constructor.leer_manifiesto(_leer(informe.ruta, "manifest.ini"))
	assert campos["url"] == "https://ejemplo.es/complemento"
	assert campos["changelog"] == "Primera versión.\nLee celdas largas."


def test_manifiesto_traducido(paquete):
	campos = constructor.leer_manifiesto(_leer(paquete.ruta, "locale/es/manifest.ini"))
	assert "mejor lectura" in campos["summary"]
	assert set(campos) <= {"summary", "description", "changelog"}


@pytest.mark.parametrize(
	("cambio", "mensaje"),
	[
		({"version": "1.2.3b1"}, "version"),
		({"version": "1"}, "version"),
		({"nvda_minima": "2026.10"}, "minimumNVDAVersion"),
		({"nvda_probada": "26.1"}, "lastTestedNVDAVersion"),
		({"nvda_minima": "2026.2", "nvda_probada": "2026.1"}, "no puede ser mayor"),
		({"nombre": "mi complemento"}, "name"),
		({"url": "http://ejemplo.es"}, "https"),
		({"autor": 'Nombre "apodo"'}, "comillas"),
	],
)
def test_manifiesto_invalido(origen, tmp_path, cambio, mensaje):
	with pytest.raises(ErrorComplemento, match=mensaje):
		constructor.construir(origen, tmp_path, _datos(**cambio))
	assert not list(tmp_path.glob("*.nvda-addon"))


def test_version_por_defecto_la_de_la_app(tmp_path):
	(tmp_path / "app" / "core").mkdir(parents=True)
	(tmp_path / "app" / "core" / "version.py").write_text('APP_VERSION = "3.4.5"\n', encoding="utf-8")
	assert constructor.leer_version_app(tmp_path) == "3.4.5"
	assert constructor.leer_version_app(tmp_path / "no-existe") == constructor.VERSION_POR_DEFECTO


def test_leer_manifiesto_formatos():
	texto = (
		"# comentario\n"
		"name = prueba\n"
		"summary = 'Resumen'\n"
		'description = """Línea uno\n'
		'línea dos"""\n'
		"version = 1.0  # comentario final\n"
		"[brailleTables]\n"
		"otra = cosa\n"
	)
	campos = constructor.leer_manifiesto(texto)
	assert campos == {"name": "prueba", "summary": "Resumen", "description": "Línea uno\nlínea dos", "version": "1.0"}


def test_clave_repetida():
	with pytest.raises(ErrorComplemento, match="repetida"):
		constructor.leer_manifiesto("name = a\nname = b\n")


def test_marcador_desconocido():
	with pytest.raises(ErrorComplemento, match="desconocido"):
		constructor.sustituir_marcadores("name = @OTRO@\n", {"NOMBRE": "x"})


def test_manifiesto_con_configobj_si_esta_disponible(paquete):
	"""NVDA lee el manifiesto con configobj: si está instalado, se comprueba con él."""
	configobj = pytest.importorskip("configobj")
	datos = configobj.ConfigObj(io.StringIO(_leer(paquete.ruta, "manifest.ini")), encoding="utf-8")
	assert datos["name"] == "miAplicacionNVDA"
	assert datos["lastTestedNVDAVersion"] == "2026.2"


# ════════════════════════════════════════════════════════════════════════════
# Traducciones
# ════════════════════════════════════════════════════════════════════════════

def test_mo_del_paquete_traduce(paquete):
	with zipfile.ZipFile(paquete.ruta) as zip_:
		datos = zip_.read("locale/es/LC_MESSAGES/nvda.mo")
	traduccion = gettext.GNUTranslations(io.BytesIO(datos))
	assert traduccion.gettext("Not in a list") == "No estás en una lista"
	assert traduccion.gettext("Texto sin traducir") == "Texto sin traducir"


def test_po_complejo(tmp_path):
	po = tmp_path / "nvda.po"
	po.write_text(
		'msgid ""\n'
		'msgstr ""\n'
		'"Content-Type: text/plain; charset=UTF-8\\n"\n'
		'"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n'
		"\n"
		"#, fuzzy\n"
		'msgid "Dudosa"\n'
		'msgstr "No debe aparecer"\n'
		"\n"
		'msgid "Sin traducir"\n'
		'msgstr ""\n'
		"\n"
		'msgid "Multi"\n'
		'"línea"\n'
		'msgstr "Varias "\n'
		'"líneas con \\"comillas\\"\\n"\n'
		"\n"
		'msgctxt "menú"\n'
		'msgid "Open"\n'
		'msgstr "Abrir"\n'
		"\n"
		'msgid "{0} item"\n'
		'msgid_plural "{0} items"\n'
		'msgstr[0] "{0} elemento"\n'
		'msgstr[1] "{0} elementos"\n',
		encoding="utf-8",
	)
	catalogo = constructor.leer_po(po)
	assert "Dudosa" not in catalogo
	assert "Sin traducir" not in catalogo
	traduccion = gettext.GNUTranslations(io.BytesIO(constructor.compilar_mo(catalogo)))
	assert traduccion.gettext("Multilínea") == 'Varias líneas con "comillas"\n'
	assert traduccion.pgettext("menú", "Open") == "Abrir"
	assert traduccion.ngettext("{0} item", "{0} items", 1) == "{0} elemento"
	assert traduccion.ngettext("{0} item", "{0} items", 3) == "{0} elementos"


def test_po_en_otra_codificacion(tmp_path):
	po = tmp_path / "nvda.po"
	po.write_text('msgid ""\nmsgstr "Content-Type: text/plain; charset=ISO-8859-1\\n"\n', encoding="utf-8")
	with pytest.raises(ErrorComplemento, match="UTF-8"):
		constructor.leer_po(po)


# ════════════════════════════════════════════════════════════════════════════
# Documentación
# ════════════════════════════════════════════════════════════════════════════

def test_readme_html_sin_markdown(paquete):
	pagina = _leer(paquete.ruta, "doc/es/readme.html")
	assert pagina.startswith("<!DOCTYPE html>")
	assert '<html lang="es">' in pagina
	assert "<h1>" in pagina and "<h2>" in pagina and "<ul>" in pagina and "<li>" in pagina
	assert "<strong>" in pagina and "<code>" in pagina


def test_readme_con_markdown_si_esta(origen, tmp_path, monkeypatch):
	falso = types.ModuleType("markdown")
	falso.markdown = lambda texto, extensions=None: "<p>CONVERTIDO CON MARKDOWN</p>"
	monkeypatch.setitem(sys.modules, "markdown", falso)
	informe = constructor.construir(origen, tmp_path, _datos())
	assert "CONVERTIDO CON MARKDOWN" in _leer(informe.ruta, "doc/es/readme.html")


def test_markdown_minimo_escapa():
	html = constructor.markdown_minimo("# Título <b>\n\n1. uno\n2. dos\n\nTexto & más")
	assert "<h1>Título &lt;b&gt;</h1>" in html
	assert "<ol>" in html and html.count("<li>") == 2
	assert "<p>Texto &amp; más</p>" in html


# ════════════════════════════════════════════════════════════════════════════
# Línea de órdenes
# ════════════════════════════════════════════════════════════════════════════

def test_main_correcto(origen, tmp_path, capsys):
	codigo = constructor.main(["--origen", str(origen), "--destino", str(tmp_path), "--version", "2.0"])
	assert codigo == 0
	assert "Complemento generado" in capsys.readouterr().out
	assert (tmp_path / "miAplicacionNVDA.nvda-addon").is_file()


def test_main_con_error(origen, tmp_path, capsys):
	codigo = constructor.main(["--origen", str(origen), "--destino", str(tmp_path), "--version", "dos"])
	assert codigo == 1
	assert "ERROR" in capsys.readouterr().err
