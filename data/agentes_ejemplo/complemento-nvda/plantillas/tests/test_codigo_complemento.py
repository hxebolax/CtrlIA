"""Pruebas del código del complemento de NVDA (PLANTILLA del agente «complemento-nvda»).

Comprueban, sin NVDA:

- que cada .py del complemento compila y solo importa módulos que existen
  dentro de NVDA (los de NVDA y la parte de la biblioteca estándar que trae);
- que cada ``_("...")`` tiene traducción en ``locale/es/LC_MESSAGES/nvda.po``;
- la lógica del módulo de aplicación con una NVDA simulada (tests/nvda_falso.py):
  clase superpuesta, movimiento silencioso con una propiedad de ventana REAL,
  lectura de celdas largas contra una lista virtual REAL de wxPython y el gesto.

Que NVDA lo anuncie de verdad se comprueba a mano (referencias/pruebas-manuales.md).
"""

from __future__ import annotations

import ast
import ctypes
import gettext
import py_compile
import sys
import zipfile
from ctypes import wintypes
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nvda_falso  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="NVDA y su complemento son de Windows")

RAIZ = nvda_falso.RAIZ
CARPETA = nvda_falso.CARPETA_COMPLEMENTO
MODULOS = sorted(p for p in CARPETA.rglob("*.py") if "__pycache__" not in p.parts)

#: Módulos de NVDA que un complemento suele importar (respaldo si NVDA no está instalado).
MODULOS_NVDA = {
	"addonAPIVersion", "addonHandler", "api", "appModuleHandler", "appModules", "baseObject", "braille",
	"brailleInput", "browseMode", "buildVersion", "characterProcessing", "comtypes", "config", "controlTypes",
	"core", "eventHandler", "extensionPoints", "globalCommands", "globalPluginHandler", "globalPlugins",
	"globalVars", "gui", "IAccessibleHandler", "inputCore", "keyboardHandler", "languageHandler",
	"locationHelper", "logHandler", "mouseHandler", "NVDAHelper", "NVDAObjects", "nvwave", "oleacc",
	"queueHandler", "scriptHandler", "speech", "synthDriverHandler", "textInfos", "tones",
	"treeInterceptorHandler", "UIAHandler", "ui", "utils", "watchdog", "winBindings", "winKernel",
	"winUser", "winVersion", "wx",
}
#: Biblioteca estándar que NVDA 2026.3 NO incluye (comprobado en su library.zip).
AUSENTES_EN_NVDA = {
	"cProfile", "compileall", "curses", "dbm", "ensurepip", "filecmp", "fileinput", "graphlib", "idlelib",
	"imaplib", "mailbox", "modulefinder", "optparse", "pickletools", "poplib", "profile", "pstats", "pyclbr",
	"readline", "sched", "shelve", "smtplib", "sqlite3", "symtable", "tabnanny", "timeit", "tkinter", "trace",
	"turtle", "turtledemo", "venv", "wsgiref", "zipapp", "zoneinfo",
}
CARPETAS_NVDA = [Path(r"C:\Program Files\NVDA"), Path(r"C:\Program Files (x86)\NVDA")]


def _importaciones(ruta: Path) -> set[str]:
	"""Módulos de primer nivel que importa un archivo (sin las relativas)."""
	arbol = ast.parse(ruta.read_text(encoding="utf-8"))
	nombres: set[str] = set()
	for nodo in ast.walk(arbol):
		if isinstance(nodo, ast.Import):
			nombres.update(alias.name.split(".")[0] for alias in nodo.names)
		elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
			nombres.add(nodo.module.split(".")[0])
	return nombres


def _cadenas_traducibles(ruta: Path) -> set[str]:
	arbol = ast.parse(ruta.read_text(encoding="utf-8"))
	return {
		nodo.args[0].value
		for nodo in ast.walk(arbol)
		if isinstance(nodo, ast.Call)
		and isinstance(nodo.func, ast.Name)
		and nodo.func.id == "_"
		and nodo.args
		and isinstance(nodo.args[0], ast.Constant)
		and isinstance(nodo.args[0].value, str)
	}


def _traduccion_es():
	constructor = nvda_falso.cargar_modulo("construir_complemento_pruebas", RAIZ / "tools" / "construir_complemento.py")
	catalogo = constructor.leer_po(CARPETA / "locale" / "es" / "LC_MESSAGES" / "nvda.po")
	return catalogo, constructor


# ════════════════════════════════════════════════════════════════════════════
# Estructura del código
# ════════════════════════════════════════════════════════════════════════════

def test_hay_modulos():
	assert any(p.parent.name == "appModules" for p in MODULOS), "Falta addon/appModules/<ejecutable>.py"


@pytest.mark.parametrize("ruta", MODULOS, ids=lambda p: p.relative_to(CARPETA).as_posix())
def test_compila(ruta, tmp_path):
	"""py_compile con el .pyc en tmp_path: la carpeta fuente no se ensucia con __pycache__."""
	py_compile.compile(str(ruta), cfile=str(tmp_path / "modulo.pyc"), doraise=True)


@pytest.mark.parametrize("ruta", MODULOS, ids=lambda p: p.relative_to(CARPETA).as_posix())
def test_importa_solo_lo_que_hay_en_nvda(ruta):
	for nombre in _importaciones(ruta):
		if nombre in MODULOS_NVDA:
			continue
		assert nombre in sys.stdlib_module_names, f"{ruta.name} importa «{nombre}», que NVDA no trae"
		assert nombre not in AUSENTES_EN_NVDA, f"{ruta.name} importa «{nombre}»: NVDA no lo incluye"


def _carpeta_nvda() -> Path | None:
	return next((c for c in CARPETAS_NVDA if (c / "library.zip").is_file()), None)


@pytest.mark.skipif(_carpeta_nvda() is None, reason="NVDA no está instalado en este equipo")
@pytest.mark.parametrize("ruta", MODULOS, ids=lambda p: p.relative_to(CARPETA).as_posix())
def test_importaciones_existen_en_el_nvda_instalado(ruta):
	"""Contrasta con library.zip del NVDA instalado (solo lee la lista de nombres)."""
	carpeta = _carpeta_nvda()
	with zipfile.ZipFile(carpeta / "library.zip") as biblioteca:
		disponibles = {n.split("/")[0].removesuffix(".pyc") for n in biblioteca.namelist()}
	disponibles |= {p.name.split(".")[0] for p in carpeta.glob("*.pyd")}
	disponibles |= set(sys.builtin_module_names)
	for nombre in _importaciones(ruta):
		assert nombre in disponibles, f"{ruta.name} importa «{nombre}», que no está en el NVDA instalado"


def test_cada_cadena_tiene_traduccion_al_espanol():
	catalogo, _constructor = _traduccion_es()
	for ruta in MODULOS:
		for cadena in _cadenas_traducibles(ruta):
			assert catalogo.get(cadena), f"Falta traducir en nvda.po: {cadena!r} ({ruta.name})"


# ════════════════════════════════════════════════════════════════════════════
# Lógica del módulo de aplicación (NVDA simulada)
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def modulo(monkeypatch):
	modulo_app, estado = nvda_falso.cargar_modulo_de_aplicacion(monkeypatch)
	modulo_app.estado_pruebas = estado
	return modulo_app


class _Objeto:
	def __init__(self, clase_ventana):
		self.windowClassName = clase_ventana


def test_clase_superpuesta_solo_en_listas(modulo):
	lista_nvda = sys.modules["NVDAObjects.IAccessible.sysListView32"].ListItem
	app = modulo.AppModule()
	clases = [lista_nvda]
	app.chooseNVDAObjectOverlayClasses(_Objeto("SysListView32"), clases)
	assert clases[0] is modulo.ElementoListaApp
	otras = [object]
	app.chooseNVDAObjectOverlayClasses(_Objeto("Edit"), otras)
	assert otras == [object]


def test_gesto_declarado(modulo):
	guion = modulo.AppModule.script_leerFilaCompleta
	assert guion.gestures == ["kb:NVDA+shift+j"]
	assert guion.__doc__, "El gesto necesita descripción para «Gestos de entrada»"


@pytest.mark.parametrize(
	("valor", "ahora", "esperado"),
	[
		(0, 1000, False),  # sin marca
		(1000, 1000, True),
		(1000, 1500, True),  # justo en el límite de 500 ms
		(1000, 1501, False),  # caducada: la navegación vuelve a sonar
		(0xFFFFFF00, 0x00000010, True),  # GetTickCount dio la vuelta
	],
)
def test_marca_reciente(modulo, valor, ahora, esperado):
	assert modulo.marca_reciente(valor, ahora) is esperado


def test_componer_fila(modulo):
	texto = modulo.componer_fila([("Autor", "Ana"), ("Texto", "  "), (None, "Suelto")])
	assert texto == "Autor: Ana. Texto: blank. Suelto"


# ── Propiedad de ventana real ───────────────────────────────────────────────

@pytest.fixture
def ventana():
	"""Ventana de solo mensajes (HWND_MESSAGE): real, invisible y sin wx."""
	user32 = ctypes.WinDLL("user32", use_last_error=True)
	crear = user32.CreateWindowExW
	crear.argtypes = (
		wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
		ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
	)
	crear.restype = wintypes.HWND
	destruir = user32.DestroyWindow
	destruir.argtypes = (wintypes.HWND,)
	hwnd = crear(0, "STATIC", "prueba", 0, 0, 0, 0, 0, wintypes.HWND(-3), None, None, None)
	assert hwnd, f"CreateWindowExW falló: {ctypes.get_last_error()}"
	yield hwnd
	destruir(hwnd)


def _senales():
	return nvda_falso.cargar_modulo("senales_nvda_pruebas", RAIZ / "app" / "ui" / "senales_nvda.py")


def test_misma_propiedad_en_app_y_complemento(modulo):
	assert _senales().PROPIEDAD_SILENCIO == modulo.PROPIEDAD_SILENCIO


def test_movimiento_silencioso_con_ventana_real(modulo, ventana):
	senales = _senales()
	assert modulo.movimiento_silencioso_activo(ventana) is False
	assert senales.marcar_movimiento_silencioso(ventana)
	assert modulo.movimiento_silencioso_activo(ventana) is True
	elemento = modulo.ElementoListaApp()
	elemento.windowHandle = ventana
	assert elemento.shouldAllowIAccessibleFocusEvent is False
	senales.quitar_marca(ventana)
	assert modulo.movimiento_silencioso_activo(ventana) is False
	assert elemento.shouldAllowIAccessibleFocusEvent is True


def test_marca_antigua_no_silencia(modulo, ventana):
	"""Una marca olvidada caduca: nunca deja muda la navegación."""
	user32 = ctypes.WinDLL("user32", use_last_error=True)
	poner = user32.SetPropW
	poner.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE)
	kernel32 = ctypes.WinDLL("kernel32")
	kernel32.GetTickCount.restype = wintypes.DWORD
	antigua = (kernel32.GetTickCount() - 5000) & 0xFFFFFFFF or 1
	assert poner(ventana, modulo.PROPIEDAD_SILENCIO, antigua)
	try:
		assert modulo.movimiento_silencioso_activo(ventana) is False
	finally:
		_senales().quitar_marca(ventana)


# ── Lectura de celdas contra una lista virtual real de wxPython ─────────────

LARGO = "a" * 2990 + "FIN-CELDA0"


# La wx.App de la sesión. Esta referencia de módulo la mantiene viva hasta el final:
# sin ella, Python destruiría la aplicación recién creada al salir de la fixture.
_APLICACION_WX = None


@pytest.fixture
def lista_wx():
	wx = pytest.importorskip("wx")
	# La wx.App de la sesión si ya hay una; nunca se destruye (otra wx.App tras
	# destruir la primera deja sin aplicación al resto de la batería).
	global _APLICACION_WX
	_APLICACION_WX = wx.GetApp() or wx.App(False)

	class ListaVirtual(wx.ListCtrl):
		def __init__(self, padre):
			super().__init__(padre, style=wx.LC_REPORT | wx.LC_VIRTUAL)
			self.InsertColumn(0, "Texto")
			self.InsertColumn(1, "Autor")
			self.SetItemCount(2)

		def OnGetItemText(self, fila, columna):  # noqa: N802 - API de wx
			return LARGO if columna == 0 else f"Autor {fila}"

	marco = wx.Frame(None)
	lista = ListaVirtual(marco)
	yield lista
	marco.Destroy()


def _elemento_de(modulo, lista, fila=0):
	"""Elemento como el que crearía NVDA para la fila (IAccessibleChildID es base 1)."""
	kernel32 = ctypes.WinDLL("kernel32")
	kernel32.GetCurrentProcess.restype = wintypes.HANDLE
	elemento = modulo.ElementoListaApp()
	elemento.processHandle = kernel32.GetCurrentProcess()
	elemento.windowHandle = lista.GetHandle()
	elemento.IAccessibleChildID = fila + 1
	return elemento


def test_celda_larga_se_lee_completa(modulo, lista_wx):
	elemento = _elemento_de(modulo, lista_wx)
	texto = elemento._getColumnContentRaw(0)
	assert len(texto) == 3000 and texto.endswith("FIN-CELDA0")
	assert elemento._getColumnContentRaw(1) == "Autor 0"


def test_con_el_bufer_de_nvda_se_corta(modulo, lista_wx):
	"""Demuestra el problema: con 512 caracteres (CBEMAXSTRLEN) se leen 511."""
	elemento = _elemento_de(modulo, lista_wx)
	assert len(modulo.leer_celda_completa(elemento, 0, maximo=512)) == 511


def test_si_falla_vuelve_a_la_lectura_de_nvda(modulo):
	elemento = modulo.ElementoListaApp()
	elemento.processHandle = None  # VirtualAllocEx fallará
	elemento.windowHandle = 0
	elemento.IAccessibleChildID = 1
	assert elemento._getColumnContentRaw(0) == "ORIGINAL"
	assert any(nivel == "debugWarning" for nivel, _m in modulo.estado_pruebas.registro.mensajes)


# ── Gesto NVDA+Mayús+J ──────────────────────────────────────────────────────

class _Foco:
	childCount = 2
	name = "Hola; Ana"

	def _getColumnHeader(self, columna):  # noqa: N802 - API de NVDA
		return {1: "Texto", 2: "Autor"}[columna]

	def _getColumnContent(self, columna):  # noqa: N802 - API de NVDA
		return {1: "Hola", 2: "Ana"}[columna]


def _foco_lista(modulo):
	lista_nvda = sys.modules["NVDAObjects.IAccessible.sysListView32"].ListItem
	return type("FocoLista", (_Foco, lista_nvda), {})()


def test_gesto_lee_la_fila(modulo):
	estado = modulo.estado_pruebas
	estado.foco = _foco_lista(modulo)
	modulo.AppModule().script_leerFilaCompleta(None)
	assert estado.dichos == ["Texto: Hola. Autor: Ana"]


def test_gesto_dos_veces_abre_ventana(modulo):
	estado = modulo.estado_pruebas
	estado.foco = _foco_lista(modulo)
	estado.repeticiones = 1
	modulo.AppModule().script_leerFilaCompleta(None)
	assert estado.ventanas == [("Texto: Hola. Autor: Ana", "Full row")]


def test_gesto_fuera_de_lista(modulo):
	estado = modulo.estado_pruebas
	estado.foco = object()
	modulo.AppModule().script_leerFilaCompleta(None)
	assert estado.dichos == ["Not in a list"]


def test_gesto_en_espanol(monkeypatch, tmp_path):
	"""Con el nvda.mo compilado, lo que se dice sale en español."""
	catalogo, constructor = _traduccion_es()
	ruta_mo = tmp_path / "nvda.mo"
	ruta_mo.write_bytes(constructor.compilar_mo(catalogo))
	with ruta_mo.open("rb") as archivo:
		traduccion = gettext.GNUTranslations(archivo)
	modulo_app, estado = nvda_falso.cargar_modulo_de_aplicacion(monkeypatch, traduccion.gettext)
	estado.foco = object()
	modulo_app.AppModule().script_leerFilaCompleta(None)
	assert estado.dichos == ["No estás en una lista"]


def test_se_carga_desde_el_scratchpad(monkeypatch):
	"""Fuera de un complemento, initTranslation falla; el módulo debe cargar igualmente."""
	modulo_app, estado = nvda_falso.cargar_modulo_de_aplicacion(monkeypatch, fuera_de_complemento=True)
	estado.foco = object()
	modulo_app.AppModule().script_leerFilaCompleta(None)
	assert estado.dichos == ["Not in a list"]
	assert any("scratchpad" in mensaje for _nivel, mensaje in estado.registro.mensajes)
