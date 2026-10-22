# Módulo de aplicación de NVDA para «MiAplicacion.exe».
# PLANTILLA del agente «complemento-nvda».
"""Mejoras de NVDA para una aplicación propia.

El nombre del archivo decide a qué programa se aplica: es el nombre del
ejecutable en minúsculas, sin «.exe» y con los puntos cambiados por guiones
bajos (``MiAplicacion.exe`` → ``miaplicacion.py``; ``mi.app.exe`` →
``mi_app.py``). Renómbralo al adaptarlo.

Este código se ejecuta DENTRO de NVDA, con el Python de NVDA (3.13 de 64 bits
desde NVDA 2026.1), no con el de la aplicación. Solo puede importar módulos de
NVDA y la parte de la biblioteca estándar que NVDA incluye (no trae, por
ejemplo, ``sqlite3``, ``tkinter`` ni ``zoneinfo``).

Qué hace:

1. **Celdas completas.** NVDA lee cada celda de una lista SysListView32 con un
   búfer de 512 caracteres (``CBEMAXSTRLEN``), así que corta a 511 los textos
   largos. Aquí se sustituye la lectura por otra con un búfer mayor, SOLO en
   esta aplicación y sin tocar el código de NVDA.
2. **Movimiento silencioso.** Si la aplicación mueve el foco por su cuenta (por
   ejemplo, para que la persona siga sobre su elemento cuando entran filas
   nuevas), pone en la ventana de la lista una propiedad con la hora del
   movimiento. Mientras esa marca sea reciente, NVDA no anuncia ese cambio de
   foco.
3. **NVDA+Mayúsculas+J** lee la fila enfocada completa, columna a columna;
   pulsado dos veces, la muestra en una ventana navegable.

Al desactivar o desinstalar el complemento no queda nada modificado: todo se
hace con clases superpuestas (overlay classes) propias de esta aplicación.
"""

import ctypes
from ctypes import wintypes

import addonHandler
import api
import appModuleHandler
import ui
import watchdog
from logHandler import log
from NVDAObjects.IAccessible import sysListView32
from scriptHandler import getLastScriptRepeatCount, script

# Instala «_» en este módulo con las traducciones de locale/<idioma>/LC_MESSAGES/nvda.mo.
# Desde la carpeta scratchpad (pruebas de desarrollo) el módulo no pertenece a un
# complemento y NVDA lanza AddonError: se sigue con el «_» de NVDA (textos en inglés).
try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.debug("Módulo cargado fuera de un complemento (scratchpad): sin traducciones propias.")

# ── CONFIGURACIÓN (debe coincidir con la aplicación) ─────────────────────────
#: Propiedad de ventana que la aplicación pone en la lista antes de mover el foco
#: por su cuenta. Su valor es GetTickCount() en el momento del movimiento.
PROPIEDAD_SILENCIO = "MiAplicacion.MovimientoSilencioso"
#: Durante cuántos milisegundos tras la marca se ignora el foco. Pasado ese
#: tiempo, NVDA vuelve a anunciar aunque la aplicación olvide quitar la marca:
#: así un fallo nunca deja muda la navegación.
VENTANA_SILENCIO_MS = 500
#: Máximo de caracteres por celda. 16 384 cubre textos muy largos sin reservar
#: memoria excesiva en el proceso de la aplicación (se reservan 2 bytes por carácter).
MAXIMO_CARACTERES = 16384
# ─────────────────────────────────────────────────────────────────────────────

# Constantes de la API de listas de Windows (commctrl.h). Se toman de NVDA si
# existen para seguir su criterio, con el valor documentado como respaldo.
LVIF_TEXT = getattr(sysListView32, "LVIF_TEXT", 0x0001)
LVIF_COLUMNS = getattr(sysListView32, "LVIF_COLUMNS", 0x0200)
LVM_GETITEMTEXTW = getattr(sysListView32, "LVM_GETITEMTEXTW", 0x1000 + 115)
MEM_COMMIT = 0x1000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04

# Instancias PROPIAS de user32 y kernel32. No se tocan los argtypes de
# ctypes.windll.user32, que comparte todo NVDA: cambiarlos podría romper a otro
# código que llama a la misma función con otros tipos.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_GetPropW = _user32.GetPropW
_GetPropW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
_GetPropW.restype = wintypes.HANDLE

_GetTickCount = _kernel32.GetTickCount
_GetTickCount.argtypes = ()
_GetTickCount.restype = wintypes.DWORD

_VirtualAllocEx = _kernel32.VirtualAllocEx
_VirtualAllocEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD)
_VirtualAllocEx.restype = ctypes.c_void_p

_VirtualFreeEx = _kernel32.VirtualFreeEx
_VirtualFreeEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD)
_VirtualFreeEx.restype = wintypes.BOOL

_WriteProcessMemory = _kernel32.WriteProcessMemory
_WriteProcessMemory.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
_WriteProcessMemory.restype = wintypes.BOOL

_ReadProcessMemory = _kernel32.ReadProcessMemory
_ReadProcessMemory.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
_ReadProcessMemory.restype = wintypes.BOOL


def marca_reciente(valor: int, ahora: int, ventana_ms: int = VENTANA_SILENCIO_MS) -> bool:
	"""Dice si una marca de GetTickCount() está dentro de la ventana de silencio.

	GetTickCount() da la vuelta cada 49,7 días; la resta módulo 2**32 lo tiene en
	cuenta. Un valor 0 significa «sin marca».
	"""
	if not valor:
		return False
	transcurrido = (ahora - valor) & 0xFFFFFFFF
	return transcurrido <= ventana_ms


def movimiento_silencioso_activo(hwnd: int) -> bool:
	"""True si la aplicación marcó la ventana ``hwnd`` hace menos de VENTANA_SILENCIO_MS."""
	if not hwnd:
		return False
	valor = _GetPropW(hwnd, PROPIEDAD_SILENCIO) or 0
	return marca_reciente(int(valor), int(_GetTickCount()))


def _comprobar(correcto, que: str) -> None:
	"""Convierte un fallo de la API de Windows en excepción con su código."""
	if not correcto:
		raise ctypes.WinError(ctypes.get_last_error(), f"{que} ha fallado")


def leer_celda_completa(elemento, indice: int, maximo: int = MAXIMO_CARACTERES) -> str | None:
	"""Lee la celda ``indice`` (subelemento, base 0) de un elemento de lista, sin cortarla a 511.

	Hace lo mismo que la lectura «fuera de proceso» de NVDA
	(``ListItem._getColumnContentRawOutProc``) pero con un búfer de ``maximo``
	caracteres: reserva memoria en el proceso de la aplicación, le pide el texto
	con LVM_GETITEMTEXTW y lo copia. En una lista virtual de wxPython, el control
	pide el texto a ``OnGetItemText`` y lo copia en ese búfer, así que un búfer
	mayor devuelve el texto completo.

	Devuelve None si la celda está vacía (como NVDA). Lanza una excepción si algo
	falla, para que quien llama vuelva a la lectura original de NVDA.
	"""
	proceso = elemento.processHandle
	estructura = elemento.LVITEM  # NVDA elige LVITEM o LVITEM64 según el proceso
	tam_estructura = ctypes.sizeof(estructura)
	memoria_elemento = _VirtualAllocEx(proceso, None, tam_estructura, MEM_COMMIT, PAGE_READWRITE)
	_comprobar(memoria_elemento, "VirtualAllocEx (elemento)")
	try:
		memoria_texto = _VirtualAllocEx(proceso, None, maximo * 2, MEM_COMMIT, PAGE_READWRITE)
		_comprobar(memoria_texto, "VirtualAllocEx (texto)")
		try:
			fila = elemento.IAccessibleChildID - 1
			item = estructura(
				iItem=fila,
				mask=LVIF_TEXT | LVIF_COLUMNS,
				iSubItem=indice,
				pszText=memoria_texto,
				cchTextMax=maximo,
			)
			_comprobar(
				_WriteProcessMemory(proceso, memoria_elemento, ctypes.byref(item), tam_estructura, None),
				"WriteProcessMemory",
			)
			# cancellableSendMessage: si la aplicación está colgada, NVDA puede
			# cancelar la espera en lugar de quedarse mudo.
			longitud = watchdog.cancellableSendMessage(
				elemento.windowHandle, LVM_GETITEMTEXTW, fila, memoria_elemento
			)
			if not longitud:
				return None
			# El control puede cambiar pszText para apuntar a su propio texto:
			# se relee la estructura y se usa el puntero que devuelva.
			_comprobar(
				_ReadProcessMemory(proceso, memoria_elemento, ctypes.byref(item), tam_estructura, None),
				"ReadProcessMemory (elemento)",
			)
			texto = ctypes.create_unicode_buffer(longitud + 1)
			_comprobar(
				_ReadProcessMemory(proceso, item.pszText, texto, longitud * 2, None),
				"ReadProcessMemory (texto)",
			)
			if longitud >= maximo - 1:
				log.debug("Celda de %d caracteres o más: puede estar cortada; sube MAXIMO_CARACTERES.", maximo - 1)
			return texto.value
		finally:
			_VirtualFreeEx(proceso, memoria_texto, 0, MEM_RELEASE)
	finally:
		_VirtualFreeEx(proceso, memoria_elemento, 0, MEM_RELEASE)


def componer_fila(columnas) -> str:
	"""Une pares (cabecera, contenido) en un texto para leer: «Autor: Ana. Texto: Hola».

	Las celdas vacías se dicen como «vacío» para que la persona sepa que la
	columna existe; sin cabecera se lee solo el contenido.
	"""
	partes = []
	for cabecera, contenido in columnas:
		contenido = (contenido or "").strip() or _("blank")
		partes.append(f"{cabecera}: {contenido}" if cabecera else contenido)
	return ". ".join(partes)


class ElementoListaApp(sysListView32.ListItem):
	"""Elemento de lista de esta aplicación: celdas completas y foco silencioso."""

	def _getColumnContentRaw(self, index):
		try:
			return leer_celda_completa(self, index)
		except Exception:
			# No se pierde la lectura: se registra y se usa la de NVDA (cortada a 511).
			log.debugWarning("No se pudo leer la celda completa; se usa la lectura de NVDA.", exc_info=True)
			return super()._getColumnContentRaw(index)

	def _get_shouldAllowIAccessibleFocusEvent(self):
		# windowHandle de un elemento es la ventana de la lista, donde la app pone la marca.
		try:
			silencio = movimiento_silencioso_activo(self.windowHandle)
		except Exception:
			log.debugWarning("No se pudo leer la marca de movimiento silencioso.", exc_info=True)
			silencio = False
		if silencio:
			log.debug("Foco automático de la aplicación: no se anuncia.")
			return False
		return super().shouldAllowIAccessibleFocusEvent


class AppModule(appModuleHandler.AppModule):
	#: Categoría con la que aparecen los gestos en «Gestos de entrada» de NVDA.
	scriptCategory = "MiAplicacion"

	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		"""Pone ElementoListaApp delante en los elementos de lista multicolumna.

		Solo en listas SysListView32 (``wx.ListCtrl`` en Windows) cuyo elemento
		NVDA ya trata como ListItem. Para limitarlo a una lista concreta, filtra
		también por ``obj.windowControlID`` (el id de wx del control).
		"""
		if obj.windowClassName == "SysListView32" and sysListView32.ListItem in clsList:
			clsList.insert(0, ElementoListaApp)

	@script(
		# Translators: description of a command in the Input gestures dialog.
		description=_("Reads the whole focused row of the list; pressed twice, shows it in a window"),
		gesture="kb:NVDA+shift+j",
	)
	def script_leerFilaCompleta(self, gesture):
		foco = api.getFocusObject()
		if not isinstance(foco, sysListView32.ListItem):
			# Translators: reported when the command is used outside a list.
			ui.message(_("Not in a list"))
			return
		columnas = []
		for columna in range(1, (foco.childCount or 0) + 1):
			columnas.append((foco._getColumnHeader(columna), foco._getColumnContent(columna)))
		texto = componer_fila(columnas) if columnas else (foco.name or "")
		if not texto:
			# Translators: reported when the focused row has no text.
			ui.message(_("Empty row"))
		elif getLastScriptRepeatCount() >= 1:
			# Translators: title of the window that shows the full row.
			ui.browseableMessage(texto, _("Full row"))
		else:
			ui.message(texto)
