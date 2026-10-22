"""Ofrece instalar el complemento de NVDA de la aplicación, siempre con permiso.

La instalación se hace ABRIENDO el ``.nvda-addon`` con ``os.startfile``: NVDA
tiene asociada esa extensión, muestra su propio diálogo («¿Instalar este
complemento?», con el aviso de que solo se instalen complementos de fuentes de
confianza) y, al aceptar, pide reiniciarse. Es la vía estándar: no hace falta
localizar ``nvda.exe`` ni copiar archivos en la configuración de NVDA, y la
persona decide en cada paso. **Nunca se instala en silencio.**

Piezas:

- ``nvda_en_ejecucion()``: busca el proceso ``nvda.exe`` (solo lectura).
- ``version_instalada(nombre)``: lee, si puede, el manifiesto del complemento
  ya instalado en la configuración de NVDA (solo lectura, sin garantías: una
  copia portátil de NVDA guarda su configuración en otro sitio).
- ``explicacion(...)``: el texto que se muestra antes de abrir nada.
- ``abrir_instalador(ruta)``: llama a ``os.startfile`` y traduce los errores.
- ``ofrecer_instalacion(padre)`` y ``crear_boton(padre)``: la parte wx.

La lógica no depende de wx: se prueba sin ventanas y con ``os.startfile``
sustituido.

PLANTILLA del agente «complemento-nvda».
"""

from __future__ import annotations

import builtins
import ctypes
import logging
import os
import re
import sys
from collections.abc import Callable, Iterable
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

# ── CONFIGURACIÓN ────────────────────────────────────────────────────────────
#: Igual que NOMBRE en tools/construir_complemento.py (y «name» del manifiesto).
NOMBRE_COMPLEMENTO = "miAplicacionNVDA"
NOMBRE_APLICACION = "MiAplicacion"
# ─────────────────────────────────────────────────────────────────────────────


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) and traducir is not _ else texto


def raiz_aplicacion() -> Path:
	"""Carpeta de la aplicación: la del .exe compilado o la de main.py.

	Si el proyecto tiene un módulo de rutas (app/core/paths.py), úsalo en su lugar.
	"""
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent
	return Path(__file__).resolve().parent.parent.parent  # <raíz>/app/ui/este_archivo.py


def ruta_complemento(nombre: str = NOMBRE_COMPLEMENTO, raiz: Path | None = None) -> Path:
	"""Ruta del paquete que genera tools/construir_complemento.py (data/bin)."""
	return (raiz or raiz_aplicacion()) / "data" / "bin" / f"{nombre}.nvda-addon"


# ════════════════════════════════════════════════════════════════════════════
# ¿Está NVDA en marcha? (enumeración de procesos, solo lectura)
# ════════════════════════════════════════════════════════════════════════════

class _PROCESSENTRY32W(ctypes.Structure):
	_fields_ = [
		("dwSize", wintypes.DWORD),
		("cntUsage", wintypes.DWORD),
		("th32ProcessID", wintypes.DWORD),
		("th32DefaultHeapID", ctypes.c_size_t),
		("th32ModuleID", wintypes.DWORD),
		("cntThreads", wintypes.DWORD),
		("th32ParentProcessID", wintypes.DWORD),
		("pcPriClassBase", ctypes.c_long),
		("dwFlags", wintypes.DWORD),
		("szExeFile", ctypes.c_wchar * 260),
	]


def nombres_de_procesos() -> list[str]:
	"""Nombres de los ejecutables en marcha (vacío si no se pueden enumerar)."""
	if sys.platform != "win32":
		return []
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	crear = kernel32.CreateToolhelp32Snapshot
	crear.argtypes = (wintypes.DWORD, wintypes.DWORD)
	crear.restype = wintypes.HANDLE
	primero = kernel32.Process32FirstW
	primero.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
	primero.restype = wintypes.BOOL
	siguiente = kernel32.Process32NextW
	siguiente.argtypes = primero.argtypes
	siguiente.restype = wintypes.BOOL
	cerrar = kernel32.CloseHandle
	cerrar.argtypes = (wintypes.HANDLE,)
	instantanea = crear(0x00000002, 0)  # TH32CS_SNAPPROCESS
	if not instantanea or instantanea == wintypes.HANDLE(-1).value:
		log.debug("No se pudieron enumerar los procesos (error %s)", ctypes.get_last_error())
		return []
	nombres: list[str] = []
	try:
		entrada = _PROCESSENTRY32W()
		entrada.dwSize = ctypes.sizeof(entrada)
		correcto = primero(instantanea, ctypes.byref(entrada))
		while correcto:
			nombres.append(entrada.szExeFile)
			correcto = siguiente(instantanea, ctypes.byref(entrada))
	finally:
		cerrar(instantanea)
	return nombres


def hay_proceso(nombres: Iterable[str], buscado: str = "nvda.exe") -> bool:
	"""True si ``buscado`` está entre ``nombres`` (sin distinguir mayúsculas)."""
	buscado = buscado.lower()
	return any(nombre.lower() == buscado for nombre in nombres)


def nvda_en_ejecucion() -> bool:
	return hay_proceso(nombres_de_procesos())


# ════════════════════════════════════════════════════════════════════════════
# Versión ya instalada (lectura, sin garantías)
# ════════════════════════════════════════════════════════════════════════════

def version_instalada(nombre: str = NOMBRE_COMPLEMENTO, config_nvda: Path | None = None) -> str | None:
	"""Versión del complemento instalado en NVDA, o None si no se sabe.

	Mira ``%APPDATA%\\nvda\\addons\\<nombre>\\manifest.ini`` (NVDA instalado).
	Solo LEE: la aplicación nunca escribe en la configuración de NVDA.
	"""
	if config_nvda is None:
		appdata = os.environ.get("APPDATA")
		if not appdata:
			return None
		config_nvda = Path(appdata) / "nvda"
	manifiesto = config_nvda / "addons" / nombre / "manifest.ini"
	try:
		texto = manifiesto.read_text(encoding="utf-8-sig")
	except OSError:
		return None
	coincidencia = re.search(r"(?m)^\s*version\s*=\s*[\"']?([^\"'\s#]+)", texto)
	return coincidencia.group(1) if coincidencia else None


def version_del_paquete(ruta: Path) -> str | None:
	"""Versión que declara el manifest.ini de dentro del .nvda-addon."""
	import zipfile

	try:
		with zipfile.ZipFile(ruta) as paquete:
			texto = paquete.read("manifest.ini").decode("utf-8-sig")
	except (OSError, KeyError, zipfile.BadZipFile):
		log.debug("No se pudo leer el manifiesto de %s", ruta, exc_info=True)
		return None
	coincidencia = re.search(r"(?m)^\s*version\s*=\s*[\"']?([^\"'\s#]+)", texto)
	return coincidencia.group(1) if coincidencia else None


# ════════════════════════════════════════════════════════════════════════════
# Mensajes y apertura
# ════════════════════════════════════════════════════════════════════════════

def explicacion(en_ejecucion: bool, instalada: str | None, nueva: str | None) -> str:
	"""Texto que se muestra ANTES de abrir el paquete: qué pasará y qué hará NVDA."""
	partes = [
		_("Se va a abrir el complemento de NVDA para {app}. Sirve para que NVDA lea completos los "
		  "elementos largos de las listas y no repita el anuncio cuando {app} mueve el foco por su "
		  "cuenta.").format(app=NOMBRE_APLICACION),
	]
	if instalada and nueva:
		partes.append(_("Versión instalada: {0}. Versión que se ofrece: {1}.").format(instalada, nueva))
	elif nueva:
		partes.append(_("Versión que se ofrece: {0}.").format(nueva))
	partes.append(
		_("NVDA mostrará su propia ventana para preguntarte si quieres instalarlo. Si aceptas, NVDA "
		  "pedirá reiniciarse: la voz se detendrá unos segundos y después volverá.")
	)
	if not en_ejecucion:
		partes.append(
			_("NVDA no parece estar en ejecución. Inícialo antes: si no, puede que el archivo no se "
			  "abra con nada que lo instale.")
		)
	partes.append(_("Puedes desactivarlo o quitarlo cuando quieras desde la Tienda de complementos de NVDA."))
	return "\n\n".join(partes)


def abrir_instalador(ruta: Path, abrir: Callable[[str], object] | None = None) -> tuple[bool, str]:
	"""Abre el .nvda-addon con su programa asociado (NVDA). Devuelve (éxito, mensaje).

	``abrir`` permite sustituir ``os.startfile`` en las pruebas. No se comprueba
	nada más: la decisión de instalar la toma la persona en el diálogo de NVDA.
	"""
	ruta = Path(ruta)
	if not ruta.is_file():
		return False, _("No se encontró el archivo del complemento:\n{0}").format(ruta)
	abrir = abrir or getattr(os, "startfile", None)
	if abrir is None:
		return False, _("Abrir el complemento solo es posible en Windows.")
	try:
		abrir(str(ruta))
	except OSError as error:
		log.warning("No se pudo abrir %s: %s", ruta, error, exc_info=True)
		return False, _(
			"No se pudo abrir el complemento. Comprueba que NVDA está instalado (una copia portátil "
			"no registra la extensión .nvda-addon).\n\nPuedes instalarlo a mano desde el menú de NVDA: "
			"Herramientas, Tienda de complementos, botón para instalar desde una fuente externa, y "
			"eligiendo este archivo:\n{0}\n\nDetalle: {1}"
		).format(ruta, error)
	return True, _("NVDA debería mostrar ahora su ventana de instalación.")


# ════════════════════════════════════════════════════════════════════════════
# Parte wx
# ════════════════════════════════════════════════════════════════════════════

def _mostrar_error(padre, mensaje: str, titulo: str) -> None:
	"""Mensaje de error con el botón «Aceptar» en español.

	Usa los diálogos accesibles del proyecto (``app/ui/accesibilidad/dialogos.py``)
	si existen; si no, un ``wx.MessageDialog`` con el botón puesto a mano. Nunca
	``wx.MessageBox``: no deja cambiar sus botones y, sin el catálogo de wx
	cargado, salen en inglés.
	"""
	import wx

	try:
		from app.ui.accesibilidad import dialogos
	except ImportError:
		dialogos = None
	if dialogos is not None:
		dialogos.mostrar_error(padre, mensaje, titulo)
		return
	dialogo = wx.MessageDialog(padre, mensaje, titulo, wx.OK | wx.ICON_ERROR)
	dialogo.SetOKLabel(_("&Aceptar"))
	try:
		dialogo.ShowModal()
	finally:
		dialogo.Destroy()


def ofrecer_instalacion(padre=None, ruta: Path | None = None, construir: Callable[[], Path] | None = None) -> bool:
	"""Explica, pide confirmación y abre el paquete. Devuelve True si se abrió.

	``construir``: función opcional que genera el paquete si falta (por ejemplo,
	``lambda: construir_complemento.construir().ruta`` al ejecutar desde código
	fuente). En la versión compilada el paquete ya viaja en data/bin.
	"""
	import wx

	titulo = _("Complemento de NVDA")
	ruta = Path(ruta) if ruta else ruta_complemento()
	if not ruta.is_file() and construir is not None:
		try:
			ruta = Path(construir())
		except Exception as error:  # se informa a la persona y queda en el registro
			log.exception("No se pudo generar el complemento de NVDA")
			mensaje = _("No se pudo generar el complemento.\n\nDetalle: {0}").format(error)
			_mostrar_error(padre, mensaje, titulo)
			return False
	if not ruta.is_file():
		_mostrar_error(padre, _("No se encontró el archivo del complemento:\n{0}").format(ruta), titulo)
		return False

	texto = explicacion(nvda_en_ejecucion(), version_instalada(), version_del_paquete(ruta))
	dialogo = wx.MessageDialog(padre, texto, titulo, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
	# Botones que dicen la acción; «Cancelar» por defecto porque NVDA se reiniciará.
	dialogo.SetYesNoLabels(_("&Abrir el instalador"), _("&Cancelar"))
	try:
		respuesta = dialogo.ShowModal()
	finally:
		dialogo.Destroy()
	if respuesta != wx.ID_YES:
		return False
	correcto, mensaje = abrir_instalador(ruta)
	if not correcto:
		_mostrar_error(padre, mensaje, titulo)
	return correcto


def crear_boton(padre, construir: Callable[[], Path] | None = None):
	"""Botón «Instalar el complemento de NVDA...» listo para añadir a un sizer.

	El texto del botón es su nombre accesible; los puntos suspensivos avisan de
	que abre un diálogo antes de hacer nada. La ayuda contextual solo se guarda
	si la aplicación instaló antes ``wx.HelpProvider.Set(wx.SimpleHelpProvider())``.
	"""
	import wx

	boton = wx.Button(padre, label=_("&Instalar el complemento de NVDA..."))
	boton.SetHelpText(
		_("Explica qué hace el complemento de NVDA y, si estás de acuerdo, lo abre para que NVDA lo instale.")
	)
	boton.Bind(wx.EVT_BUTTON, lambda _evento: ofrecer_instalacion(padre.GetTopLevelParent(), construir=construir))
	return boton
