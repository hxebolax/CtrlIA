"""Pruebas de la internacionalización (PLANTILLA del agente «traducciones»).

Trabajan sobre proyectos de ejemplo en ``tmp_path`` y redirigen ``data`` con
``APP_DATA_DIR``: nunca tocan los catálogos reales del proyecto. Las pruebas
de wx se omiten si wxPython no está instalado.
"""

from __future__ import annotations

import builtins
import gettext
import struct
import textwrap
from pathlib import Path

import pytest

from app.core import i18n
from tools import actualizar_po, catalogo_po, compilar_traducciones, extraer_cadenas

DOMINIO = "prueba"

CODIGO_VENTANA = '''\
from __future__ import annotations

TITULO = N_("Mi aplicación")


class Ventana:
	def crear(self, n, nombre, variable):
		# Traductores: botón que guarda el documento actual.
		# Máximo 15 caracteres.
		guardar = _("&Guardar")
		largo = _(
			"Primera parte "
			"y segunda parte"
		)
		sumado = _("Uno " + "dos")
		archivos = ngettext("Se descargó {n} archivo", "Se descargaron {n} archivos", n).format(n=n)
		abrir_verbo = pgettext("verbo", "Abrir")
		abrir_estado = pgettext("estado", "Abierto")
		elementos = npgettext("lista", "{n} elemento", "{n} elementos", n)
		otra_vez = _("&Guardar")
		# Un comentario normal que NO es para traductores.
		sin_nota = _("Cerrar")
		return (guardar, largo, sumado, archivos, abrir_verbo, abrir_estado, elementos, otra_vez, sin_nota)

	def mal(self, nombre, variable, n):
		a = _(f"Hola {nombre}")
		b = _(variable)
		c = _("Hola %s" % nombre)
		d = _("Hola {}".format(nombre))
		e = _("")
		f = _("De {} a {}")
		return a, b, c, d, e, f
'''


def _proyecto(tmp_path: Path, codigo: str = CODIGO_VENTANA) -> Path:
	raiz = tmp_path / "proyecto"
	(raiz / "app" / "ui").mkdir(parents=True)
	(raiz / "app" / "core").mkdir(parents=True)
	(raiz / "app" / "ui" / "ventana.py").write_text(codigo, encoding="utf-8")
	(raiz / "app" / "core" / "i18n.py").write_text(f'DOMINIO = "{DOMINIO}"\n', encoding="utf-8")
	(raiz / "app" / "core" / "version.py").write_text('APP_VERSION = "2.1.0"\n', encoding="utf-8")
	(raiz / "main.py").write_text('print(_("Arrancando"))\n', encoding="utf-8")
	return raiz


@pytest.fixture(autouse=True)
def espanol_al_terminar():
	"""Cada prueba deja instalado el español para no contaminar a las demás."""
	yield
	i18n._oyentes.clear()
	i18n._instalar(gettext.NullTranslations())
	i18n._idioma = i18n.IDIOMA_BASE


# ── Extracción ───────────────────────────────────────────────────────────────

def test_extrae_singular_plural_contexto_comentarios_y_concatenacion(tmp_path):
	raiz = _proyecto(tmp_path)
	entradas, _problemas = extraer_cadenas.extraer(raiz)
	por_clave = {e.clave: e for e in entradas}

	guardar = por_clave[(None, "&Guardar")]
	assert guardar.referencias == ["app/ui/ventana.py:10", "app/ui/ventana.py:20"]
	assert guardar.comentarios_extraidos == ["Traductores: botón que guarda el documento actual. Máximo 15 caracteres."]
	assert por_clave[(None, "Cerrar")].comentarios_extraidos == []
	assert (None, "Primera parte y segunda parte") in por_clave
	assert (None, "Uno dos") in por_clave
	assert (None, "Mi aplicación") in por_clave  # N_()
	assert (None, "Arrancando") in por_clave  # main.py

	plural = por_clave[(None, "Se descargó {n} archivo")]
	assert plural.msgid_plural == "Se descargaron {n} archivos"
	assert "python-brace-format" in plural.banderas
	assert por_clave[("verbo", "Abrir")].msgctxt == "verbo"
	assert por_clave[("lista", "{n} elemento")].msgid_plural == "{n} elementos"
	# El orden es el de aparición: el .pot se lee como el código.
	assert entradas[0].msgid == "Mi aplicación"


def test_avisa_de_cadenas_no_extraibles(tmp_path):
	raiz = _proyecto(tmp_path)
	_entradas, problemas = extraer_cadenas.extraer(raiz)
	errores = [str(p) for p in problemas if p.grave]
	avisos = [str(p) for p in problemas if not p.grave]
	assert len(errores) == 5
	assert any("f-string" in e and "ventana.py:26" in e for e in errores)
	assert any("variable «variable»" in e for e in errores)
	assert any("formatea con %" in e for e in errores)
	assert any(".format()" in e for e in errores)
	assert any("cabecera del catálogo" in e for e in errores)
	assert len(avisos) == 1 and "marcadores sin nombre" in avisos[0]
	assert extraer_cadenas.main(["--raiz", str(raiz)]) == 1


def test_pot_con_cabecera_correcta_y_estable(tmp_path):
	raiz = _proyecto(tmp_path, 'x = _("Hola")\ny = ngettext("{n} día", "{n} días", 2)\n')
	assert extraer_cadenas.main(["--raiz", str(raiz)]) == 0
	ruta = raiz / "data" / "locales" / f"{DOMINIO}.pot"
	pot = catalogo_po.leer_po(ruta)
	metadatos = pot.metadatos()
	assert metadatos["Content-Type"] == "text/plain; charset=UTF-8"
	assert metadatos["Plural-Forms"].startswith("nplurals=")
	assert metadatos["Project-Id-Version"] == f"{DOMINIO} 2.1.0"
	assert [e.msgid for e in pot.entradas] == ["Hola", "{n} día", "Arrancando"]
	# Sin cambios en el código, el archivo no se reescribe (ni cambia la fecha).
	antes = ruta.read_bytes()
	ruta.write_bytes(antes.replace(b"POT-Creation-Date: 2", b"POT-Creation-Date: 1"))
	marcado = ruta.read_bytes()
	extraer_cadenas.generar_pot(raiz)
	assert ruta.read_bytes() == marcado


# ── Lectura y escritura de .po ───────────────────────────────────────────────

PO_INGLES = textwrap.dedent(
	'''\
	# Traducción de prueba.
	msgid ""
	msgstr ""
	"Language: en\\n"
	"Content-Type: text/plain; charset=UTF-8\\n"
	"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

	#: app/ui/ventana.py:3
	msgid "Abrir"
	msgstr "Open"

	msgctxt "verbo"
	msgid "Abrir"
	msgstr "Open (verb)"

	msgid "Se descargó {n} archivo"
	msgid_plural "Se descargaron {n} archivos"
	msgstr[0] "{n} file downloaded"
	msgstr[1] "{n} files downloaded"

	msgctxt "lista"
	msgid "{n} elemento"
	msgid_plural "{n} elementos"
	msgstr[0] "{n} item"
	msgstr[1] "{n} items"

	msgid ""
	"Línea uno\\n"
	"Línea \\"dos\\""
	msgstr ""
	"Line one\\n"
	"Line \\"two\\""

	#, fuzzy
	msgid "Dudoso"
	msgstr "Doubtful"

	msgid "Sin traducir"
	msgstr ""

	#, python-brace-format
	msgid "Hola, {nombre}"
	msgstr "Hello, {name}"

	msgid "&Guardar"
	msgstr "Save"

	#~ msgid "Viejo"
	#~ msgstr "Old"
	'''
)


def test_po_ida_y_vuelta(tmp_path):
	catalogo = catalogo_po.leer_po_texto(PO_INGLES)
	assert catalogo.metadatos()["Language"] == "en"
	assert len(catalogo.entradas) == 10
	multilinea = catalogo.entradas[4]
	assert multilinea.msgid == 'Línea uno\nLínea "dos"'
	assert catalogo.entradas[-1].obsoleta
	ruta = tmp_path / "copia.po"
	catalogo_po.escribir_po(catalogo, ruta)
	releido = catalogo_po.leer_po(ruta)
	assert [(e.clave, e.msgstr, e.msgstr_plural, e.obsoleta) for e in releido.entradas] == [
		(e.clave, e.msgstr, e.msgstr_plural, e.obsoleta) for e in catalogo.entradas
	]


def test_po_mal_formado_indica_la_linea():
	with pytest.raises(catalogo_po.ErrorPO, match=r":3:"):
		catalogo_po.leer_po_texto('msgid "a"\nmsgstr "b"\nbasura\n', "roto.po")


# ── Compilación ──────────────────────────────────────────────────────────────

def _compilar(tmp_path: Path, texto: str) -> tuple[compilar_traducciones.Informe, Path]:
	carpeta = tmp_path / "locales" / "en" / "LC_MESSAGES"
	carpeta.mkdir(parents=True, exist_ok=True)
	po = carpeta / f"{DOMINIO}.po"
	po.write_text(texto, encoding="utf-8")
	return compilar_traducciones.compilar(po), carpeta / f"{DOMINIO}.mo"


def test_compila_y_gettext_lo_carga(tmp_path):
	informe, mo = _compilar(tmp_path, PO_INGLES)
	assert informe.destino == mo
	assert informe.aproximadas == 1 and informe.sin_traducir == 1
	# «Hola, {nombre}» → «{name}» no coincide: error y NO se compila esa entrada.
	assert any("{nombre}" in e for e in informe.errores)
	assert any("acelerador" in a for a in informe.avisos)

	datos = mo.read_bytes()
	assert struct.unpack("<I", datos[:4])[0] == 0x950412DE
	with mo.open("rb") as archivo:
		traduccion = gettext.GNUTranslations(archivo)
	assert traduccion.gettext("Abrir") == "Open"
	assert traduccion.pgettext("verbo", "Abrir") == "Open (verb)"
	assert traduccion.ngettext("Se descargó {n} archivo", "Se descargaron {n} archivos", 1) == "{n} file downloaded"
	assert traduccion.ngettext("Se descargó {n} archivo", "Se descargaron {n} archivos", 5) == "{n} files downloaded"
	assert traduccion.npgettext("lista", "{n} elemento", "{n} elementos", 2) == "{n} items"
	assert traduccion.gettext('Línea uno\nLínea "dos"') == 'Line one\nLine "two"'
	assert traduccion.gettext("Dudoso") == "Dudoso"  # fuzzy: no se compila
	assert traduccion.gettext("Sin traducir") == "Sin traducir"
	assert traduccion.gettext("Hola, {nombre}") == "Hola, {nombre}"  # marcador erróneo
	assert traduccion.gettext("Viejo") == "Viejo"  # obsoleta
	assert traduccion.info()["language"] == "en"


def test_mo_tabla_ordenada(tmp_path):
	_informe, mo = _compilar(tmp_path, PO_INGLES)
	datos = mo.read_bytes()
	_magico, _revision, total, inicio, *_resto = struct.unpack("<7I", datos[:28])
	claves = []
	for indice in range(total):
		longitud, posicion = struct.unpack("<2I", datos[inicio + indice * 8 : inicio + indice * 8 + 8])
		claves.append(datos[posicion : posicion + longitud])
	assert claves == sorted(claves)
	assert claves[0] == b""  # la cabecera
	assert b"verbo\x04Abrir" in claves
	assert b"Se descarg\xc3\xb3 {n} archivo\x00Se descargaron {n} archivos" in claves


def test_regla_de_plural_invalida(tmp_path):
	sin_regla = 'msgid ""\nmsgstr ""\n"Plural-Forms: nplurals=INTEGER; plural=EXPRESSION;\\n"\n\n'
	plural = 'msgid "{n} día"\nmsgid_plural "{n} días"\nmsgstr[0] "{n} day"\nmsgstr[1] "{n} days"\n'
	informe, _mo = _compilar(tmp_path, sin_regla + plural)
	assert any("Plural-Forms" in e for e in informe.errores)
	# Sin plurales, la regla de la plantilla se quita del .mo y el catálogo carga.
	informe, mo = _compilar(tmp_path, sin_regla + 'msgid "Hola"\nmsgstr "Hello"\n')
	assert not informe.errores
	with mo.open("rb") as archivo:
		assert gettext.GNUTranslations(archivo).gettext("Hola") == "Hello"


def test_plural_con_formas_de_menos(tmp_path):
	texto = PO_INGLES.replace('msgstr[1] "{n} items"\n', "")
	informe, _mo = _compilar(tmp_path, texto)
	assert any("1 formas de plural" in e for e in informe.errores)


def test_main_compila_todo_data_locales(tmp_path):
	raiz = tmp_path
	carpeta = raiz / "data" / "locales" / "en" / "LC_MESSAGES"
	carpeta.mkdir(parents=True)
	(carpeta / f"{DOMINIO}.po").write_text(PO_INGLES.replace("{name}", "{nombre}"), encoding="utf-8")
	assert compilar_traducciones.main(["--raiz", str(raiz)]) == 0
	assert (carpeta / f"{DOMINIO}.mo").is_file()


# ── Fusión ───────────────────────────────────────────────────────────────────

def test_fusion_conserva_marca_obsoletas_y_propone_aproximadas():
	po = catalogo_po.leer_po_texto(
		textwrap.dedent(
			'''\
			msgid ""
			msgstr ""
			"Language: en\\n"
			"Plural-Forms: nplurals=2; plural=(n != 1);\\n"
			"POT-Creation-Date: 2020-01-01 00:00+0000\\n"

			# Nota de quien traduce.
			msgid "Abrir"
			msgstr "Open"

			msgid "Descargar el archivo seleccionado"
			msgstr "Download the selected file"

			msgid "Función retirada"
			msgstr "Removed feature"

			#~ msgid "Vuelve"
			#~ msgstr "Comes back"
			'''
		)
	)
	pot = catalogo_po.leer_po_texto(
		textwrap.dedent(
			'''\
			msgid ""
			msgstr ""
			"POT-Creation-Date: 2026-09-25 10:00+0200\\n"

			#: app/a.py:1
			msgid "Abrir"
			msgstr ""

			#: app/a.py:2
			msgid "Descargar los archivos seleccionados"
			msgstr ""

			#: app/a.py:3
			msgid "Vuelve"
			msgstr ""

			#: app/a.py:4
			msgid "{n} día"
			msgid_plural "{n} días"
			msgstr[0] ""
			msgstr[1] ""

			#: app/a.py:5
			msgid "Nuevo"
			msgstr ""
			'''
		)
	)
	resumen = actualizar_po.fusionar(po, pot)
	por_clave = po.por_clave()
	abrir = por_clave[(None, "Abrir")]
	assert abrir.msgstr == "Open" and abrir.referencias == ["app/a.py:1"]
	assert abrir.comentarios_traductor == ["Nota de quien traduce."]
	aproximada = por_clave[(None, "Descargar los archivos seleccionados")]
	assert aproximada.aproximada and aproximada.msgstr == "Download the selected file"
	assert aproximada.previos == ['msgid "Descargar el archivo seleccionado"']
	assert por_clave[(None, "Vuelve")].msgstr == "Comes back" and not por_clave[(None, "Vuelve")].obsoleta
	assert por_clave[(None, "{n} día")].msgstr_plural == {0: "", 1: ""}
	retirada = por_clave[(None, "Función retirada")]
	assert retirada.obsoleta and po.entradas[-1] is retirada
	assert po.metadatos()["POT-Creation-Date"] == "2026-09-25 10:00+0200"
	assert (resumen.traducidas, resumen.aproximadas, resumen.sin_traducir, resumen.obsoletas) == (2, 1, 2, 1)


def test_crear_po_de_idioma_nuevo():
	pot = catalogo_po.leer_po_texto('msgid ""\nmsgstr ""\n"Language: \\n"\n\nmsgid "{n} día"\nmsgid_plural "{n} días"\nmsgstr[0] ""\nmsgstr[1] ""\n')
	po, conocida = actualizar_po.crear_po(pot, "ru")
	assert conocida
	assert po.metadatos()["Language"] == "ru"
	assert po.metadatos()["Plural-Forms"].startswith("nplurals=3;")
	assert po.entradas[0].msgstr_plural == {0: "", 1: "", 2: ""}
	assert actualizar_po.regla_plural("pt_BR") == "nplurals=2; plural=(n > 1);"
	assert actualizar_po.regla_plural("es_MX") == "nplurals=2; plural=(n != 1);"


# ── Flujo completo e i18n ────────────────────────────────────────────────────

def _traducir_todo(po_ruta: Path) -> None:
	"""Simula a quien traduce: rellena cada entrada en «inglés»."""
	catalogo = catalogo_po.leer_po(po_ruta)
	for entrada in catalogo.entradas:
		if entrada.es_plural:
			entrada.msgstr_plural = {0: "EN1 " + entrada.msgid, 1: "EN2 " + (entrada.msgid_plural or "")}
		else:
			entrada.msgstr = "EN " + entrada.msgid
	catalogo_po.escribir_po(catalogo, po_ruta)


def test_flujo_completo_extraer_crear_compilar_cargar(tmp_path, monkeypatch):
	raiz = _proyecto(tmp_path, 'a = _("&Abrir")\nb = ngettext("{n} día", "{n} días", 1)\nc = pgettext("menú", "Ver")\n')
	assert extraer_cadenas.main(["--raiz", str(raiz)]) == 0
	assert actualizar_po.main(["--raiz", str(raiz), "--nuevo", "en"]) == 0
	assert actualizar_po.main(["--raiz", str(raiz), "--nuevo", "en"]) == 1  # no sobrescribe
	po = raiz / "data" / "locales" / "en" / "LC_MESSAGES" / f"{DOMINIO}.po"
	_traducir_todo(po)
	assert actualizar_po.main(["--raiz", str(raiz)]) == 0
	assert compilar_traducciones.main(["--raiz", str(raiz)]) == 0

	monkeypatch.setenv("APP_DATA_DIR", str(raiz / "data"))
	monkeypatch.delenv("APP_IDIOMA", raising=False)
	assert i18n.inicializar("en", dominio=DOMINIO) == "en"
	assert builtins._("&Abrir") == "EN &Abrir"
	assert builtins.ngettext("{n} día", "{n} días", 3) == "EN2 {n} días"
	assert builtins.n_("{n} día", "{n} días", 1) == "EN1 {n} día"
	assert builtins.pgettext("menú", "Ver") == "EN Ver"
	assert builtins.N_("Constante") == "Constante"
	assert i18n.idiomas_disponibles(DOMINIO) == ["es", "en"]


def test_idioma_inexistente_vuelve_al_espanol(tmp_path, monkeypatch, caplog):
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
	monkeypatch.delenv("APP_IDIOMA", raising=False)
	assert i18n.inicializar("xx") == "es"
	assert isinstance(i18n.traduccion_actual(), gettext.NullTranslations)
	assert builtins._("Guardar") == "Guardar"
	assert builtins.ngettext("{n} día", "{n} días", 2) == "{n} días"
	assert builtins.pgettext("verbo", "Abrir") == "Abrir"
	assert "se usa el español" in caplog.text


def test_catalogo_danado_no_impide_arrancar(tmp_path, monkeypatch, caplog):
	carpeta = tmp_path / "locales" / "en" / "LC_MESSAGES"
	carpeta.mkdir(parents=True)
	(carpeta / f"{i18n.DOMINIO}.mo").write_bytes(b"esto no es un .mo")
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
	monkeypatch.delenv("APP_IDIOMA", raising=False)
	assert i18n.inicializar("en") == "es"
	assert "no se pudo cargar" in caplog.text


def test_orden_de_preferencia(monkeypatch):
	monkeypatch.setattr(i18n, "idioma_sistema", lambda: "fr_FR")
	monkeypatch.delenv("APP_IDIOMA", raising=False)
	assert i18n.resolver_idioma(None) == "fr_FR"
	assert i18n.resolver_idioma("auto") == "fr_FR"
	assert i18n.resolver_idioma("en-US") == "en_US"
	monkeypatch.setenv("APP_IDIOMA", "de")
	assert i18n.resolver_idioma("en") == "de"
	monkeypatch.delenv("APP_IDIOMA")
	monkeypatch.setattr(i18n, "idioma_sistema", lambda: "")
	assert i18n.resolver_idioma(None) == "es"


def test_normalizar_codigos():
	assert i18n.normalizar("es-ES") == "es_ES"
	assert i18n.normalizar("en_US.UTF-8") == "en_US"
	assert i18n.normalizar("EN") == "en"
	assert i18n.normalizar("Spanish_Spain") == "es_ES"
	assert i18n.es_idioma_base("es_MX")
	assert i18n.nombre_idioma("fr_CA") == "Français"


def test_cambiar_idioma_avisa_a_los_oyentes(tmp_path, monkeypatch):
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
	monkeypatch.delenv("APP_IDIOMA", raising=False)
	avisados: list[str] = []
	i18n.al_cambiar_idioma(avisados.append)
	i18n.al_cambiar_idioma(lambda idioma: 1 / 0)  # un oyente roto no para a los demás
	efectivo, reiniciar = i18n.cambiar_idioma("es")
	assert efectivo == "es" and avisados == ["es"]
	assert reiniciar is False


# ── wxPython ─────────────────────────────────────────────────────────────────

def _en_proceso_aparte(codigo: str, tmp_path: Path) -> None:
	"""Ejecuta ``codigo`` en un intérprete nuevo, con su propia ``wx.App``.

	``wx.Locale`` es estado GLOBAL de wxWidgets y sus objetos deben destruirse
	en orden inverso al de creación: crear y destruir idiomas dentro de la
	sesión de pruebas de un proyecto (que ya tiene uno activo y una sola
	``wx.App`` compartida) llegó a cerrar el intérprete de golpe. En un proceso
	aparte la prueba es la misma y no interfiere con el resto de la batería.
	El código sale con 3 para pedir que la prueba se omita.
	"""
	import os
	import subprocess
	import sys

	pytest.importorskip("wx")
	raiz = Path(__file__).resolve().parent.parent
	entorno = dict(os.environ, APP_DATA_DIR=str(tmp_path / "data_proceso"), PYTHONIOENCODING="utf-8")
	resultado = subprocess.run(  # noqa: S603 - intérprete propio con código de la prueba
		[sys.executable, "-c", textwrap.dedent(codigo)], cwd=raiz, env=entorno,
		capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
	)
	if resultado.returncode == 3:
		pytest.skip(resultado.stdout.strip() or "No disponible en este sistema")
	assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_configurar_wx_traduce_botones_estandar(tmp_path):
	_en_proceso_aparte(
		"""
		import wx
		from app.core import i18n

		aplicacion = wx.App(False)
		objeto = i18n.configurar_wx("es")
		assert objeto is i18n._locale_wx, "la referencia se conserva"
		assert objeto.IsOk()
		assert wx.GetStockLabel(wx.ID_CANCEL).replace("&", "") == "Cancelar"
		""",
		tmp_path,
	)


def test_wx_lee_el_mo_generado(tmp_path):
	"""Un segundo lector independiente (el de wxWidgets, en C++) acepta el .mo."""
	carpeta = tmp_path / "fr" / "LC_MESSAGES"
	carpeta.mkdir(parents=True)
	po = carpeta / f"{DOMINIO}.po"
	po.write_text(
		'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Plural-Forms: nplurals=2; plural=(n > 1);\\n"\n\n'
		'msgid "Abrir"\nmsgstr "Ouvrir"\n\nmsgid "{n} día"\nmsgid_plural "{n} días"\nmsgstr[0] "{n} jour"\nmsgstr[1] "{n} jours"\n',
		encoding="utf-8",
	)
	assert not compilar_traducciones.compilar(po).errores
	_en_proceso_aparte(
		f"""
		import sys
		import wx

		aplicacion = wx.App(False)
		sin_avisos = wx.LogNull()
		frances = wx.Locale(wx.LANGUAGE_FRENCH)
		del sin_avisos
		if not frances.IsOk():
			print("El francés no está disponible en este sistema")
			sys.exit(3)
		wx.Locale.AddCatalogLookupPathPrefix({str(tmp_path)!r})
		assert frances.AddCatalog({DOMINIO!r}, wx.LANGUAGE_SPANISH, "UTF-8")
		assert wx.GetTranslation("Abrir", {DOMINIO!r}) == "Ouvrir"
		assert wx.GetTranslation("{{n}} día", "{{n}} días", 5, {DOMINIO!r}) == "{{n}} jours"
		""",
		tmp_path,
	)
