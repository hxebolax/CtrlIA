"""Configuración común de pytest para una aplicación wxPython.

Qué garantiza:

- **Nunca se toca el ``data/`` real.** ``APP_DATA_DIR`` apunta a una carpeta
  temporal ANTES de que las pruebas importen la aplicación (hay módulos que
  calculan rutas al importarse) y, además, cada prueba recibe su propia carpeta
  (``data_temporal``). Al final se avisa si el ``data/`` real cambió.
- **Sin red por defecto.** Cualquier conexión a un host no local falla con un
  mensaje claro, salvo en las pruebas marcadas ``@pytest.mark.red``.
- **wx sin ventanas visibles.** Una única ``wx.App`` para toda la sesión; los
  marcos se crean sin mostrar y se destruyen al terminar cada prueba.
- **Voz y diálogos modales simulados**, para probar lo que se anuncia o se
  pregunta sin bloquear la ejecución.

Ajusta las constantes de «Configuración del proyecto» a tu aplicación.

PLANTILLA del agente «pruebas».
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

log = logging.getLogger(__name__)

# La raíz del proyecto en el path: así ``import app`` funciona aunque pytest
# se lance desde otra carpeta o sin ``pythonpath`` en la configuración.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
	sys.path.insert(0, str(RAIZ))


# ════════════════════════════════════════════════════════════════════════════
# Configuración del proyecto (ajústala)
# ════════════════════════════════════════════════════════════════════════════

# Variable de entorno que el módulo de rutas usa para redirigir data/.
VARIABLE_DATA = "APP_DATA_DIR"

# Si True, TODAS las pruebas reciben su propia carpeta data vacía.
AISLAR_DATA_SIEMPRE = True

# Atributos de módulo que guardan la ruta de data calculada al importar (por
# ejemplo, [("app.core.paths", "DATA_DIR")]). Se sustituyen en cada prueba.
ATRIBUTOS_RUTA_DATA: list[tuple[str, str]] = []

# Si True, las pruebas sin @pytest.mark.red no pueden salir a Internet.
BLOQUEAR_RED_SIEMPRE = True

# Funciones de voz que se sustituyen en la fixture voz_simulada, como
# «paquete.modulo.funcion». Parchea donde se USA la función: si un módulo hace
# «from app.core.voz import hablar», hay que parchear «app.ui.ventana.hablar».
FUNCIONES_VOZ: list[str] = ["app.core.voz.hablar", "app.ui.accesibilidad.voz.hablar"]

MARCADORES = {
	"lento": "prueba lenta (más de un segundo); se excluye con -m 'not lento'",
	"red": "necesita Internet; no se ejecuta por defecto (usa -m red)",
	"interfaz": "muestra ventanas o envía teclas reales; no se ejecuta por defecto (usa -m interfaz)",
}

HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


# ════════════════════════════════════════════════════════════════════════════
# Carpeta data de la sesión (antes de importar la aplicación)
# ════════════════════════════════════════════════════════════════════════════

# Se fija al importar conftest, que pytest carga antes que los módulos de
# prueba: así ni siquiera una importación que calcule rutas ve el data real.
_DATA_SESION = Path(tempfile.mkdtemp(prefix="pruebas_data_"))
os.environ[VARIABLE_DATA] = str(_DATA_SESION)


def _instantanea(carpeta: Path, limite: int = 20000) -> dict[str, tuple[int, int]]:
	"""Tamaño y fecha de cada archivo de ``carpeta`` (para detectar cambios)."""
	datos: dict[str, tuple[int, int]] = {}
	if not carpeta.is_dir():
		return datos
	for numero, archivo in enumerate(carpeta.rglob("*")):
		if numero >= limite:
			break
		try:
			if archivo.is_file():
				estado = archivo.stat()
				datos[str(archivo.relative_to(carpeta))] = (estado.st_size, estado.st_mtime_ns)
		except OSError:
			log.debug("No se pudo leer %s", archivo, exc_info=True)
	return datos


def pytest_configure(config: pytest.Config) -> None:
	"""Registra los marcadores y toma una instantánea del data real."""
	for nombre, descripcion in MARCADORES.items():
		config.addinivalue_line("markers", f"{nombre}: {descripcion}")
	config._instantanea_data_real = _instantanea(RAIZ / "data")  # type: ignore[attr-defined]


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
	"""Avisa si el data real cambió durante las pruebas (fuga de aislamiento).

	Es un aviso y no un fallo: si la aplicación estaba abierta a la vez, sus
	registros también cambian.
	"""
	antes = getattr(config, "_instantanea_data_real", {})
	despues = _instantanea(RAIZ / "data")
	cambios = sorted(set(antes.items()) ^ set(despues.items()))
	if cambios:
		nombres = sorted({nombre for nombre, _ in cambios})
		terminalreporter.write_line(
			f"AVISO: el data real cambió durante las pruebas ({len(nombres)} archivos), "
			f"por ejemplo: {', '.join(nombres[:5])}. Revisa el aislamiento.",
			yellow=True,
		)


def pytest_unconfigure(config: pytest.Config) -> None:
	"""Borra la carpeta data de la sesión."""
	shutil.rmtree(_DATA_SESION, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# data temporal por prueba
# ════════════════════════════════════════════════════════════════════════════

def redirigir_data(carpeta: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
	"""Hace que la aplicación use ``carpeta`` como data mientras dure la prueba.

	Redirige ``APP_DATA_DIR`` y los atributos de ``ATRIBUTOS_RUTA_DATA`` (rutas
	que algún módulo calculó al importarse).
	"""
	monkeypatch.setenv(VARIABLE_DATA, str(carpeta))
	for modulo, atributo in ATRIBUTOS_RUTA_DATA:
		objeto = importlib.import_module(modulo)
		valor = str(carpeta) if isinstance(getattr(objeto, atributo), str) else carpeta
		monkeypatch.setattr(objeto, atributo, valor)
	return carpeta


@pytest.fixture
def data_temporal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
	"""Carpeta data vacía y exclusiva de esta prueba.

	El módulo de rutas debe leer la variable al pedir la ruta, no solo al
	importarse; si no, añade sus atributos a ``ATRIBUTOS_RUTA_DATA``.
	"""
	carpeta = tmp_path / "data"
	carpeta.mkdir()
	return redirigir_data(carpeta, monkeypatch)


@pytest.fixture(autouse=True)
def _aislar_data(request: pytest.FixtureRequest) -> None:
	"""Aplica ``data_temporal`` a todas las pruebas si ``AISLAR_DATA_SIEMPRE``."""
	if AISLAR_DATA_SIEMPRE:
		request.getfixturevalue("data_temporal")


@pytest.fixture(autouse=True)
def _config_agentes_aislada(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Los settings.json y config.toml de los agentes de quien pasa las pruebas no cuentan.

	Ctrl+IA lee de ellos, por ejemplo, el modo de permisos inicial: sin esto,
	el resultado dependería del equipo. Las pruebas que necesitan otra carpeta
	cambian estas variables con su propio ``monkeypatch``.
	"""
	casa = tmp_path_factory.mktemp("config_agentes")
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(casa / ".claude"))
	monkeypatch.setenv("CODEX_HOME", str(casa / ".codex"))
	monkeypatch.setattr("app.services.agentes.antigravity.ruta_ajustes_cli",
		lambda: casa / ".gemini" / "antigravity-cli" / "settings.json")


# ════════════════════════════════════════════════════════════════════════════
# Red
# ════════════════════════════════════════════════════════════════════════════

class ConexionBloqueada(RuntimeError):
	"""Una prueba sin ``@pytest.mark.red`` intentó salir a Internet.

	Hereda de ``RuntimeError`` y no de ``OSError`` a propósito: así no la
	absorbe un ``except requests.RequestException`` ni un ``except OSError``
	del código probado, y la prueba falla con este mensaje.
	"""


def _host_de(direccion) -> str:
	if isinstance(direccion, tuple) and direccion:
		return str(direccion[0])
	return str(direccion)


def _es_local(host: str) -> bool:
	return host in HOSTS_LOCALES or host.startswith("127.") or host.lower() == "localhost"


@dataclass
class RegistroRed:
	"""Intentos de conexión bloqueados (para comprobarlos en la prueba)."""

	intentos: list[str] = field(default_factory=list)


@pytest.fixture
def sin_red(monkeypatch: pytest.MonkeyPatch) -> RegistroRed:
	"""Bloquea las conexiones y resoluciones DNS a hosts no locales."""
	registro = RegistroRed()
	conectar_original = socket.socket.connect
	conectar_ex_original = socket.socket.connect_ex
	resolver_original = socket.getaddrinfo

	def _rechazar(host: str) -> None:
		registro.intentos.append(host)
		raise ConexionBloqueada(
			f"Conexión a «{host}» bloqueada: las pruebas no salen a Internet. "
			"Simula la respuesta o marca la prueba con @pytest.mark.red."
		)

	def conectar(sock, direccion):
		if not _es_local(_host_de(direccion)):
			_rechazar(_host_de(direccion))
		return conectar_original(sock, direccion)

	def conectar_ex(sock, direccion):
		if not _es_local(_host_de(direccion)):
			_rechazar(_host_de(direccion))
		return conectar_ex_original(sock, direccion)

	def resolver(host, *args, **kwargs):
		nombre = host.decode() if isinstance(host, bytes) else str(host or "")
		if not _es_local(nombre):
			_rechazar(nombre)
		return resolver_original(host, *args, **kwargs)

	monkeypatch.setattr(socket.socket, "connect", conectar)
	monkeypatch.setattr(socket.socket, "connect_ex", conectar_ex)
	monkeypatch.setattr(socket, "getaddrinfo", resolver)
	return registro


@pytest.fixture(autouse=True)
def _red_controlada(request: pytest.FixtureRequest) -> None:
	"""Aplica ``sin_red`` salvo en las pruebas marcadas ``red``."""
	if BLOQUEAR_RED_SIEMPRE and request.node.get_closest_marker("red") is None:
		request.getfixturevalue("sin_red")


# ════════════════════════════════════════════════════════════════════════════
# Voz simulada
# ════════════════════════════════════════════════════════════════════════════

class VozSimulada:
	"""Sustituye funciones de voz y guarda lo que se habría anunciado."""

	def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
		self._monkeypatch = monkeypatch
		self.llamadas: list[tuple[tuple, dict]] = []

	def parchear(self, ruta: str, obligatorio: bool = True) -> None:
		"""Sustituye la función ``ruta`` («paquete.modulo.funcion»).

		Con ``obligatorio=False`` ignora las rutas que no existen (útil para la
		lista por defecto de ``FUNCIONES_VOZ``).
		"""
		def falsa(*args, **kwargs):
			self.llamadas.append((args, kwargs))

		try:
			self._monkeypatch.setattr(ruta, falsa)
		except (ImportError, AttributeError):
			if obligatorio:
				raise
			log.debug("Función de voz no encontrada: %s", ruta)

	@property
	def textos(self) -> list[str]:
		"""Primer argumento de cada anuncio (el texto)."""
		return [str(args[0]) if args else str(kwargs.get("texto", "")) for args, kwargs in self.llamadas]

	@property
	def ultimo(self) -> str | None:
		return self.textos[-1] if self.llamadas else None

	def contiene(self, fragmento: str) -> bool:
		return any(fragmento in texto for texto in self.textos)

	def limpiar(self) -> None:
		self.llamadas.clear()


@pytest.fixture
def voz_simulada(monkeypatch: pytest.MonkeyPatch) -> VozSimulada:
	"""Captura los anuncios en una lista en lugar de hablar.

	Parchea las funciones de ``FUNCIONES_VOZ`` que existan; para otras, llama a
	``voz_simulada.parchear("app.ui.ventana.hablar")``.
	"""
	voz = VozSimulada(monkeypatch)
	for ruta in FUNCIONES_VOZ:
		voz.parchear(ruta, obligatorio=False)
	return voz


# ════════════════════════════════════════════════════════════════════════════
# wx
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def app_wx():
	"""Una única ``wx.App`` para toda la sesión (crear varias da problemas)."""
	wx = pytest.importorskip("wx")
	app = wx.App.Get() or wx.App(False)
	# Como en main.py: ayuda contextual (Ctrl+H) y botones estándar en español.
	wx.HelpProvider.Set(wx.SimpleHelpProvider())
	# Con la misma función que main.py: el objeto queda guardado en i18n y no se
	# destruye a mano (los wx.Locale deben morir en orden inverso al de creación).
	from app.core import i18n

	i18n.configurar_wx("es")
	yield app
	for ventana in list(wx.GetTopLevelWindows()):
		ventana.Destroy()
	from tests.ayudas_wx import procesar_eventos

	procesar_eventos()
	app.Destroy()


def _destruir_nuevas(antes: set[int]) -> None:
	"""Destruye las ventanas de nivel superior creadas durante la prueba."""
	import wx

	from tests.ayudas_wx import procesar_eventos

	for ventana in list(wx.GetTopLevelWindows()):
		if id(ventana) not in antes and ventana:
			ventana.Destroy()
	procesar_eventos()


@pytest.fixture
def marco(app_wx):
	"""Marco SIN mostrar; al terminar se destruye junto con los diálogos que queden."""
	import wx

	antes = {id(ventana) for ventana in wx.GetTopLevelWindows()}
	ventana = wx.Frame(None, title="Pruebas")
	yield ventana
	_destruir_nuevas(antes)


@pytest.fixture
def panel(marco):
	"""Panel dentro de ``marco`` para crear controles."""
	import wx

	return wx.Panel(marco)


@pytest.fixture
def marco_visible(app_wx, request: pytest.FixtureRequest):
	"""Marco visible: solo para pruebas marcadas ``interfaz`` (foco real, teclas reales)."""
	import wx

	if request.node.get_closest_marker("interfaz") is None:
		pytest.fail("marco_visible solo se usa en pruebas marcadas @pytest.mark.interfaz")
	antes = {id(ventana) for ventana in wx.GetTopLevelWindows()}
	ventana = wx.Frame(None, title="Pruebas (se cerrará sola)")
	ventana.Show()
	yield ventana
	_destruir_nuevas(antes)


@dataclass
class DialogoMostrado:
	"""Lo que tenía un diálogo cuando se «mostró» (sigue disponible tras Destroy)."""

	clase: str
	titulo: str
	mensaje: str
	textos: list[str]


class DialogosSimulados:
	"""Sustituye ``ShowModal`` y ``wx.MessageBox`` por respuestas programadas."""

	CLASES = (
		"Dialog", "MessageDialog", "RichMessageDialog", "GenericMessageDialog", "FileDialog",
		"DirDialog", "TextEntryDialog", "PasswordEntryDialog", "SingleChoiceDialog",
		"MultiChoiceDialog", "ColourDialog", "FontDialog",
	)

	def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
		import wx

		self._wx = wx
		self.respuestas: list = []
		self.respuesta_por_defecto = wx.ID_OK
		self.mostrados: list[DialogoMostrado] = []
		for nombre in self.CLASES:
			clase = getattr(wx, nombre, None)
			if clase is not None and "ShowModal" in vars(clase):
				monkeypatch.setattr(clase, "ShowModal", self._sustituto_show_modal())
		monkeypatch.setattr(wx, "MessageBox", self._caja_mensaje)

	def _sustituto_show_modal(self):
		"""Función (no método ligado) para que Python le pase el diálogo como ``self``."""
		def show_modal(dialogo):
			return self._mostrar(dialogo)

		return show_modal

	def responder(self, *respuestas) -> None:
		"""Encola respuestas: un id (``wx.ID_CANCEL``) o una función ``f(dialogo) -> id``.

		La función sirve para rellenar campos antes de «pulsar» Aceptar.
		"""
		self.respuestas.extend(respuestas)

	def _siguiente(self, dialogo):
		respuesta = self.respuestas.pop(0) if self.respuestas else self.respuesta_por_defecto
		return respuesta(dialogo) if callable(respuesta) else respuesta

	def _foto(self, dialogo) -> DialogoMostrado:
		wx = self._wx
		textos: list[str] = []
		for hijo in dialogo.GetChildren():
			pendientes = [hijo]
			while pendientes:
				control = pendientes.pop(0)
				if isinstance(control, wx.TextCtrl):
					textos.append(control.GetValue())
				elif control.GetLabel():
					textos.append(control.GetLabel())
				pendientes.extend(control.GetChildren())
		mensaje = dialogo.GetMessage() if hasattr(dialogo, "GetMessage") else ""
		return DialogoMostrado(type(dialogo).__name__, dialogo.GetTitle(), mensaje, textos)

	def _mostrar(self, dialogo):
		self.mostrados.append(self._foto(dialogo))
		return self._siguiente(dialogo)

	def _caja_mensaje(self, mensaje, titulo="", estilo=0, padre=None, *args, **kwargs):
		self.mostrados.append(DialogoMostrado("MessageBox", str(titulo), str(mensaje), [str(mensaje)]))
		respuesta = self._siguiente(None)
		# wx.MessageBox devuelve wx.OK/wx.YES/wx.NO/wx.CANCEL, no los ID_*.
		equivalencias = {
			self._wx.ID_OK: self._wx.OK, self._wx.ID_YES: self._wx.YES,
			self._wx.ID_NO: self._wx.NO, self._wx.ID_CANCEL: self._wx.CANCEL,
		}
		return equivalencias.get(respuesta, respuesta)

	@property
	def ultimo(self) -> DialogoMostrado | None:
		return self.mostrados[-1] if self.mostrados else None


@pytest.fixture
def dialogo_modal(app_wx, monkeypatch: pytest.MonkeyPatch) -> DialogosSimulados:
	"""Los diálogos modales no se muestran: devuelven la respuesta programada.

	Uso: ``dialogo_modal.responder(wx.ID_CANCEL)`` y, después de la acción,
	``dialogo_modal.ultimo.mensaje``. Sin respuestas encoladas, devuelve
	``wx.ID_OK``.
	"""
	return DialogosSimulados(monkeypatch)


# ════════════════════════════════════════════════════════════════════════════
# Añadidos de Ctrl+IA
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _voz_muda(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Ninguna prueba habla: el equipo de desarrollo tiene un lector de pantalla en marcha."""
	for ruta in FUNCIONES_VOZ:
		modulo, _punto, nombre = ruta.rpartition(".")
		try:
			objeto = importlib.import_module(modulo)
		except ImportError:
			continue
		if hasattr(objeto, nombre):
			monkeypatch.setattr(objeto, nombre, lambda *args, **kwargs: False)


@pytest.fixture
def datos_temporales(data_temporal: Path) -> Path:
	"""Nombre que usan las pruebas del esqueleto para la carpeta ``data`` temporal."""
	return data_temporal


def procesar_eventos(condicion=lambda: False, segundos: float = 3.0) -> bool:
	"""Procesa eventos de wx hasta que se cumple ``condicion`` (ver ``tests.ayudas_wx``)."""
	from tests.ayudas_wx import esperar_hasta

	try:
		esperar_hasta(condicion, segundos)
	except AssertionError:
		return False
	return bool(condicion())


@pytest.fixture(autouse=True)
def _sin_modales_reales(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Un ShowModal real en pruebas se vería en pantalla y dejaría la sesión colgada.

	Falla al momento diciendo qué cuadro era. Las pruebas que necesitan modales
	usan la fixture ``dialogo_modal`` (que lo sustituye después) o parchean la
	función que lo abre. Las marcadas ``interfaz`` quedan fuera.
	"""
	if request.node.get_closest_marker("interfaz"):
		return
	try:
		import wx
	except ImportError:
		return

	def prohibido(dialogo, *args, **kwargs):
		titulo = dialogo.GetTitle() if dialogo else "?"
		log.error("ShowModal real durante las pruebas: «%s» (%s)", titulo, request.node.nodeid)
		raise RuntimeError(f"ShowModal real durante las pruebas: «{titulo}»")

	monkeypatch.setattr(wx.Dialog, "ShowModal", prohibido)
