"""Módulos de NVDA simulados para probar el complemento fuera de NVDA.

PLANTILLA del agente «complemento-nvda». NVDA no se puede importar desde un
Python normal, así que estas pruebas sustituyen sus módulos por versiones
mínimas con la misma forma (nombres, firmas y el mecanismo de propiedades
``_get_x`` → ``x`` de ``baseObject.AutoPropertyObject``). Sirven para probar la
LÓGICA del complemento (qué clase superpuesta elige, cuándo calla, qué lee el
gesto) y la lectura real de celdas contra una lista de wxPython. NO prueban
que NVDA lo anuncie: eso es una prueba manual (referencias/pruebas-manuales.md).
"""

from __future__ import annotations

import builtins
import ctypes
import importlib.util
import sys
import types
from ctypes import wintypes
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CARPETA_COMPLEMENTO = RAIZ / "nvda_addon" / "addon"


def cargar_modulo(nombre: str, ruta: Path):
	"""Importa un archivo .py por su ruta, sin depender de paquetes ni de sys.path."""
	especificacion = importlib.util.spec_from_file_location(nombre, ruta)
	modulo = importlib.util.module_from_spec(especificacion)
	sys.modules[nombre] = modulo
	especificacion.loader.exec_module(modulo)
	return modulo


class AutoPropertyType(type):
	"""Como en NVDA: cada método ``_get_x`` de la clase crea la propiedad ``x``."""

	def __init__(cls, nombre, bases, diccionario):
		super().__init__(nombre, bases, diccionario)
		for clave, valor in diccionario.items():
			if clave.startswith("_get_") and callable(valor):
				setattr(cls, clave[5:], property(valor))


class LVITEM64(ctypes.Structure):
	"""Estructura LVITEMW para procesos de 64 bits (misma disposición que en NVDA)."""

	_fields_ = [
		("mask", ctypes.c_uint),
		("iItem", ctypes.c_int),
		("iSubItem", ctypes.c_int),
		("state", ctypes.c_uint),
		("stateMask", ctypes.c_uint),
		("pszText", ctypes.c_uint64),
		("cchTextMax", ctypes.c_int),
		("iImage", ctypes.c_int),
		("lParam", ctypes.c_uint64),
		("iIndent", ctypes.c_int),
		("iGroupId", ctypes.c_int),
		("cColumns", ctypes.c_uint),
		("puColumns", ctypes.c_uint64),
		("piColFmt", ctypes.c_uint64),
		("iGroup", ctypes.c_int),
	]


class Registro:
	"""Sustituto de logHandler.log que guarda los mensajes."""

	def __init__(self):
		self.mensajes: list[tuple[str, str]] = []

	def _anotar(self, nivel, mensaje, *argumentos, **_opciones):
		self.mensajes.append((nivel, mensaje % argumentos if argumentos else mensaje))

	def debug(self, *a, **k):
		self._anotar("debug", *a, **k)

	def debugWarning(self, *a, **k):  # noqa: N802 - nombre de la API de NVDA
		self._anotar("debugWarning", *a, **k)

	def info(self, *a, **k):
		self._anotar("info", *a, **k)

	def warning(self, *a, **k):
		self._anotar("warning", *a, **k)

	def error(self, *a, **k):
		self._anotar("error", *a, **k)


class Estado:
	"""Lo que las pruebas observan o controlan de la NVDA simulada."""

	def __init__(self):
		self.dichos: list[str] = []
		self.ventanas: list[tuple[str, str | None]] = []
		self.foco = None
		self.repeticiones = 0
		self.registro = Registro()
		self.fuera_de_complemento = False


def instalar(monkeypatch, traducir=None, fuera_de_complemento=False) -> Estado:
	"""Registra en sys.modules los módulos de NVDA simulados y devuelve su estado.

	``traducir``: función para «_» (por defecto, identidad). Con un
	GNUTranslations se prueba el complemento en español.
	``fuera_de_complemento``: simula un módulo cargado desde el scratchpad, donde
	initTranslation falla y queda el «_» de NVDA (en builtins).
	"""
	estado = Estado()
	estado.fuera_de_complemento = fuera_de_complemento
	monkeypatch.setattr(builtins, "_", lambda texto: texto, raising=False)
	traducir = traducir or (lambda texto: texto)

	def modulo(nombre, **atributos):
		nuevo = types.ModuleType(nombre)
		nuevo.__dict__.update(atributos)
		monkeypatch.setitem(sys.modules, nombre, nuevo)
		return nuevo

	class AddonError(Exception):
		pass

	def initTranslation():  # noqa: N802 - API de NVDA
		# NVDA instala «_» en el módulo que llama; fuera de un complemento lanza AddonError.
		if estado.fuera_de_complemento:
			raise AddonError("Code does not belong to an addon package.")
		sys._getframe(1).f_globals["_"] = traducir

	modulo("addonHandler", initTranslation=initTranslation, AddonError=AddonError)
	modulo("api", getFocusObject=lambda: estado.foco)

	class AppModule(metaclass=AutoPropertyType):
		def __init__(self, processID=0, appName=""):
			self.processID = processID
			self.appName = appName

		def chooseNVDAObjectOverlayClasses(self, obj, clsList):
			pass

	modulo("appModuleHandler", AppModule=AppModule)
	modulo(
		"ui",
		message=lambda texto, speechPriority=None, brailleText=None: estado.dichos.append(texto),
		browseableMessage=lambda mensaje, titulo=None, *a, **k: estado.ventanas.append((mensaje, titulo)),
	)

	user32 = ctypes.WinDLL("user32", use_last_error=True)
	enviar = user32.SendMessageW
	enviar.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
	enviar.restype = ctypes.c_ssize_t
	# En NVDA, cancellableSendMessage permite abandonar la espera si la app se cuelga.
	modulo("watchdog", cancellableSendMessage=enviar)
	modulo("logHandler", log=estado.registro)

	def script(description="", gesture=None, gestures=None, category=None, **_opciones):
		def decorar(funcion):
			funcion.__doc__ = description
			funcion.gestures = ([gesture] if gesture else []) + list(gestures or [])
			funcion.category = category
			return funcion

		return decorar

	modulo("scriptHandler", script=script, getLastScriptRepeatCount=lambda: estado.repeticiones)

	class ListItem(metaclass=AutoPropertyType):
		"""Elemento de lista mínimo: lectura «original» cortada y foco permitido."""

		LVITEM = LVITEM64

		def _getColumnContentRaw(self, index):
			return "ORIGINAL"

		def _get_shouldAllowIAccessibleFocusEvent(self):
			return True

	paquete = modulo("NVDAObjects")
	paquete.__path__ = []
	subpaquete = modulo("NVDAObjects.IAccessible")
	subpaquete.__path__ = []
	lista = modulo(
		"NVDAObjects.IAccessible.sysListView32",
		ListItem=ListItem,
		LVITEM64=LVITEM64,
		LVIF_TEXT=0x0001,
		LVIF_COLUMNS=0x0200,
		LVM_GETITEMTEXTW=0x1000 + 115,
		CBEMAXSTRLEN=512,
	)
	paquete.IAccessible = subpaquete
	subpaquete.sysListView32 = lista
	return estado


def cargar_modulo_de_aplicacion(monkeypatch, traducir=None, fuera_de_complemento=False):
	"""Instala la NVDA simulada e importa appModules/miaplicacion.py. Devuelve (módulo, estado)."""
	estado = instalar(monkeypatch, traducir, fuera_de_complemento)
	ruta = next((CARPETA_COMPLEMENTO / "appModules").glob("*.py"))
	nombre = "appModules_" + ruta.stem
	# Se registra antes con monkeypatch para que al terminar la prueba se borre.
	monkeypatch.setitem(sys.modules, nombre, types.ModuleType(nombre))
	modulo = cargar_modulo(nombre, ruta)
	return modulo, estado
