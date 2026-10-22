# API de NVDA para módulos de aplicación y complementos globales

Todo lo de esta página se comprobó en el código de NVDA 2026.3 beta 2 (bytecode de su `library.zip`) y en el código fuente de la rama `master` de [nvaccess/nvda](https://github.com/nvaccess/nvda), salvo lo marcado como no verificado.

## El entorno donde corre el código

- El complemento se ejecuta **dentro de NVDA**, con su Python: **3.13 de 64 bits desde NVDA 2026.1** (antes, 3.11 de 32 bits). Fuentes: [novedades de NVDA 2026.1](https://download.nvaccess.org/releases/2026.1beta4/documentation/changes.html), [PR #18689](https://github.com/nvaccess/nvda/pull/18689) y la [guía para preparar complementos para NVDA de 64 bits](https://groups.google.com/a/nvaccess.org/g/nvda-users/c/z_TOySDuatE). En el equipo de pruebas: `nvda.exe` x86-64 y `python313.dll`.
- NVDA está congelado: su `library.zip` solo trae la parte de la biblioteca estándar que usa. En 2026.3 **faltan**, entre otros, `sqlite3`, `tkinter`, `zoneinfo`, `smtplib`, `imaplib`, `sched`, `shelve`, `dbm`, `optparse`, `timeit`, `graphlib`. Sí están `ctypes`, `json`, `re`, `threading`, `urllib`, `ssl`, `zipfile`, `wx`, `comtypes` y `configobj`.
- Un `.pyd` o una DLL que lleve el complemento deben ser de 64 bits y para Python 3.13. Si el complemento tiene que funcionar también con NVDA de 32 bits (2025.x), hay que llevar ambas y cargar la adecuada.
- Todo corre en el hilo principal de NVDA salvo que crees hilos: una operación lenta en un evento o un gesto deja a NVDA mudo. Usa `watchdog.cancellableSendMessage` en vez de `SendMessage` hacia otra aplicación.

## Módulo de aplicación (appModule)

Archivo `appModules/<ejecutable>.py`. El nombre es el del ejecutable **en minúsculas, sin extensión y con los puntos cambiados por guiones bajos** (`appModuleHandler.getAppNameFromProcessID` y `_getPossibleAppModuleNamesForExecutable`). Para un nombre raro o varios ejecutables, un complemento global puede llamar a `appModuleHandler.registerExecutableWithAppModule("ejecutable", "modulo")` (y `unregisterExecutable` al terminar).

```python
import addonHandler
import appModuleHandler
import ui
from scriptHandler import script

addonHandler.initTranslation()


class AppModule(appModuleHandler.AppModule):
	scriptCategory = "MiAplicacion"  # categoría en «Gestos de entrada»

	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		"""Elige clases propias para ciertos objetos (antes de crearlos)."""

	def event_NVDAObject_init(self, obj):
		"""Retoca un objeto recién creado (p. ej. obj.name = ...). Debe ser rápido."""

	def event_gainFocus(self, obj, nextHandler):
		"""Se llama al recibir el foco; SIEMPRE termina con nextHandler() salvo que quieras callarlo."""
		nextHandler()

	@script(
		# Translators: description in the Input gestures dialog.
		description=_("Reports something useful"),
		gesture="kb:NVDA+shift+j",
	)
	def script_informar(self, gesture):
		ui.message(_("Something useful"))
```

`addonHandler.initTranslation()` lanza `addonHandler.AddonError` si el módulo no pertenece a un complemento instalado (por ejemplo, copiado en la carpeta scratchpad); lo dice su propia docstring en NVDA 2026.3. La plantilla lo envuelve en `try/except addonHandler.AddonError` y sigue con el `_` de NVDA (textos en inglés), para poder probar desde el scratchpad.

### Clases superpuestas (overlay classes)

Es la forma limpia de cambiar el comportamiento de un tipo de control en una sola aplicación: `chooseNVDAObjectOverlayClasses` inserta tu clase al principio de `clsList` y NVDA crea el objeto con todas ellas. Al desactivar el complemento no queda nada cambiado (a diferencia de sustituir funciones de NVDA en caliente, que afecta a todos los programas y hay que deshacer en `terminate`).

```python
from NVDAObjects.IAccessible import sysListView32

class ElementoLista(sysListView32.ListItem):
	def _get_shouldAllowIAccessibleFocusEvent(self):
		if condicion_propia(self):
			return False
		return super().shouldAllowIAccessibleFocusEvent

# en AppModule:
	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		if obj.windowClassName == "SysListView32" and sysListView32.ListItem in clsList:
			clsList.insert(0, ElementoLista)
```

- Los métodos `_get_x` se convierten en la propiedad `x` (`baseObject.AutoPropertyObject`). Para llamar a la del padre: `super().x`. NVDA hace lo mismo en `appModules/kindle.py` (`PageTurnFocusIgnorer`).
- Filtra por `obj.windowClassName`, `obj.role` (`controlTypes.Role.LISTITEM`...), `obj.windowControlID` (el id de wx del control) o `obj.name`. Evita consultas lentas aquí: se llama para cada objeto.

### Gestos y scripts

- `@script(description=..., gesture="kb:NVDA+shift+j", gestures=[...], category=..., speakOnDemand=...)` en `scriptHandler`. Sin `description` el gesto no aparece en «Gestos de entrada» y la persona no puede reasignarlo.
- `scriptHandler.getLastScriptRepeatCount()` devuelve 0 en la primera pulsación y 1 en la segunda seguida: patrón «una vez lee, dos veces muestra».
- Antes de elegir tecla, comprueba que NVDA no la usa. En NVDA 2026.3 están libres `NVDA+Mayús+J` y `NVDA+Mayús+K`; ocupadas, entre otras, `NVDA+Mayús+B, D, F, I, L, S, W`. Los gestos de un módulo de aplicación solo están activos con esa aplicación en primer plano y tienen prioridad sobre los globales.
- Nombres de teclas en inglés y minúsculas: `kb:NVDA+shift+j`, `kb:NVDA+control+f3`, `kb(laptop):...`.

### Decir cosas

- `ui.message(texto)`: voz y braille. `ui.message(texto, brailleText=...)` si el braille debe ser distinto.
- `ui.browseableMessage(texto, titulo)`: ventana que se recorre con las flechas; para textos largos. En 2026.3 admite además `isHtml`, `closeButton` y `copyButton`; si el complemento declara una versión mínima anterior, usa solo los dos primeros argumentos (no se verificó desde qué versión existen los botones).
- `speech.cancelSpeech()` para cortar la voz; úsalo con mucho cuidado.

### Leer el objeto enfocado

- `api.getFocusObject()` y `api.getNavigatorObject()`.
- En una lista SysListView32 multicolumna: `obj.childCount` es el número de columnas; `obj._getColumnContent(n)` y `obj._getColumnHeader(n)` (n base 1, en el orden en que se ven) dan contenido y cabecera. Son métodos «privados» de NVDA: estables desde hace años, pero revísalos al cambiar de versión mayor.

## Complemento global (globalPlugin)

```python
import globalPluginHandler
from scriptHandler import script
import ui


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# preparar

	def terminate(self, *args, **kwargs):
		# deshacer TODO lo que se cambió en __init__
		super().terminate(*args, **kwargs)
```

Úsalo solo si la función tiene sentido en cualquier programa. Para mejorar una aplicación propia, un módulo de aplicación es más seguro: no afecta al resto del sistema.

## Límite de lectura de listas (SysListView32)

- `NVDAObjects/IAccessible/sysListView32.py` define `CBEMAXSTRLEN = 512`. La lectura dentro del proceso (`nvdaHelper/remote/sysListView32.cpp`) usa un búfer fijo de 512 caracteres compilado en C, y la de fuera del proceso (`_getColumnContentRawOutProc`) reserva `CBEMAXSTRLEN * 2` bytes en la aplicación. Resultado: cada celda se corta en **511 caracteres**. NVDA usa la ruta dentro del proceso cuando puede inyectarse (lo normal).
- Cambiar `CBEMAXSTRLEN` no basta (la ruta dentro del proceso no lo mira) y afecta a todos los programas.
- La plantilla sustituye `_getColumnContentRaw` en una clase superpuesta de la aplicación por una lectura fuera de proceso con un búfer de 16 384 caracteres: `VirtualAllocEx` en el proceso de la app, `LVM_GETITEMTEXTW` con `watchdog.cancellableSendMessage`, relectura de la estructura (el control puede cambiar `pszText`) y `ReadProcessMemory`. Usa `self.LVITEM`, que NVDA ajusta a 32 o 64 bits según la aplicación. Si falla, vuelve a la lectura de NVDA y lo anota en el registro.
- Comprobado en el equipo de pruebas contra un `wx.ListCtrl` virtual real (wxPython 4.2.5): con 512 caracteres llegan 511; con 16 384, los 3000 completos. En una lista virtual, wx copia `OnGetItemText` en el búfer que se le da.
- En listas de una sola columna que no son de tipo informe, el nombre lo da el proxy MSAA de Windows (`oleacc`), no esta ruta; su límite no se ha verificado.

## Propiedades de ventana desde el complemento

```python
import ctypes
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)  # instancia PROPIA
_GetPropW = _user32.GetPropW
_GetPropW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
_GetPropW.restype = wintypes.HANDLE
valor = _GetPropW(obj.windowHandle, "MiAplicacion.MovimientoSilencioso") or 0
```

- **No** pongas `argtypes` en `ctypes.windll.user32.GetPropW`: ese objeto lo comparte todo NVDA y otro código puede llamarlo con otros tipos.
- En 64 bits los identificadores de ventana y los punteros no caben en un `int` de C: declara siempre `argtypes` y `restype` (HWND, HANDLE, `c_void_p`, `c_size_t`). Sin ellos, ctypes los trunca a 32 bits («overflows or flipped bits», avisa la guía de 64 bits).
- NVDA 2026.1 movió sus envolturas de la API de Windows a `winBindings.*` y dejó `winUser`/`winKernel` como obsoletos en parte. `winBindings.user32` de 2026.3 no envuelve `GetPropW`, así que la plantilla usa ctypes directamente.

## Registro

`from logHandler import log`; `log.debug`, `log.info`, `log.warning`, `log.error`, `log.debugWarning(..., exc_info=True)` (aviso visible solo con el nivel de depuración). Nunca un `except: pass` mudo: si algo falla, que quede en el registro y que el complemento vuelva al comportamiento de NVDA.

## Fuentes

- [NVDA Developer Guide](https://download.nvaccess.org/documentation/developerGuide.html) (appModules, globalPlugins, scripts, consola, scratchpad).
- [Guía comunitaria de desarrollo de complementos](https://github.com/nvdaaddons/devguide/wiki/NVDA%20Add-on%20Development%20Guide).
- [sysListView32.py](https://github.com/nvaccess/nvda/blob/master/source/NVDAObjects/IAccessible/sysListView32.py) y [sysListView32.cpp](https://github.com/nvaccess/nvda/blob/master/nvdaHelper/remote/sysListView32.cpp).
- [Anuncios de cambios de API para complementos](https://groups.google.com/a/nvaccess.org/g/nvda-api).
