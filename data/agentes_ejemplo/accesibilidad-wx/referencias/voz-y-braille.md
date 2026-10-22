# Voz y braille desde la aplicación

## Cuándo hablar (y cuándo no)

Habla desde la aplicación **solo** lo que el lector no puede saber por sí mismo:

- Lectura lógica por columnas de una lista.
- Resultado de una acción que no mueve el foco («Copiado al portapapeles», «Descarga terminada»).
- Cambios en segundo plano relevantes (llega un mensaje, termina una tarea, falla una conexión).
- Ayuda contextual (Ctrl+H).
- Aviso de arranque mientras la aplicación carga sin ventana.

**No** hables:

- La fila o el control al que se mueve el usuario: el lector ya lo anuncia y se oiría dos veces.
- El valor de un deslizador o de una casilla nativos al cambiarlos: el lector ya lo dice.
- Cada porcentaje de un progreso: solo fases e hitos.

## Políticas de anuncio

| Tipo | Interrumpir | Canal | Ejemplo |
|---|---|---|---|
| Respuesta a una orden del usuario | Sí | navegación | Columna leída, ayuda Ctrl+H |
| Resultado de una acción | Sí | avisos | «Guardado» |
| Evento en segundo plano | No | avisos | «Descarga terminada» |
| Error importante | No, y además consultable | avisos | «No se pudo conectar» + diálogo, estado o registro |
| Progreso | No; solo fases e hitos | avisos | «50 por ciento» |

- **Coalescencia:** al navegar rápido, solo se dice el último anuncio interrumpible (lo hace `voz.py`).
- **Retardo** (120 ms por defecto, configurable entre 0 y 600): el anuncio llega después del anuncio automático de foco del lector y lo gana.
- **Información importante también consultable:** historial de anuncios (`voz.historial()`), una orden «repetir último mensaje», una barra de estado o un diálogo. La voz se pierde si el usuario estaba escuchando otra cosa.
- **Preferencias separadas** para la voz de navegación y la de avisos (`voz.configurar(navegacion=..., avisos=...)`). Si la persona desactiva los avisos, la lectura de columnas sigue funcionando.
- Un modo de avisos configurable (voz, cuadro de mensaje o ambos) ayuda a quien no quiere perderse los mensajes importantes.

## Opciones técnicas de salida

### accessible_output2 (la de las plantillas)

`accessible_output2.outputs.auto.Auto()` prueba NVDA, JAWS, System Access, Dolphin, ZDSR... y, si no hay lector, **SAPI5** (habla aunque no haya lector activo). `speak(texto, interrupt=True)` y `braille(texto)`. **`Auto.output()` no envía a braille**: hay que llamar a `speak` y a `braille` por separado (lo hace `voz.py`). Trae sus propias DLL (`accessible_output2/lib`); en la versión compilada deben viajar en `lib/accessible_output2/lib`.

### NVDA Controller Client (solo NVDA)

`nvdaControllerClient.dll` (LGPL 2.1, una por arquitectura): `nvdaController_testIfRunning`, `speakText`, `cancelSpeech`, `brailleMessage`. Robusto y con braille, pero solo para NVDA.

```python
nvda = ctypes.WinDLL(str(ruta_dll))
nvda.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
if nvda.nvdaController_testIfRunning() == 0:
	nvda.nvdaController_cancelSpeech()
	nvda.nvdaController_speakText("Archivo guardado")
```

### Notificaciones de UI Automation (sin DLL de terceros)

`UiaRaiseNotificationEvent` (Windows 10 1709+) es el mecanismo estándar de Microsoft: lo recibe Narrador y NVDA lo anuncia si la ventana está en primer plano. Necesita un proveedor `IRawElementProviderSimple` con `comtypes`. Una prueba confirmó que el evento se emite y otro proceso lo recibe; **no se ha verificado con lectores reales**, y JAWS podría tenerlo desactivado por defecto. Úsalo como alternativa experimental, no como única vía.

```python
import ctypes
from ctypes import POINTER, byref, c_int
import comtypes, comtypes.client
from comtypes import COMObject

UIA = comtypes.client.GetModule("UIAutomationCore.dll")
_uia = ctypes.windll.uiautomationcore
_uia.UiaHostProviderFromHwnd.argtypes = [ctypes.c_void_p, POINTER(POINTER(UIA.IRawElementProviderSimple))]
_uia.UiaRaiseNotificationEvent.argtypes = [POINTER(UIA.IRawElementProviderSimple), c_int, c_int, comtypes.BSTR, comtypes.BSTR]
_uia.UiaRaiseNotificationEvent.restype = ctypes.HRESULT


class NotificadorUIA(COMObject):
	_com_interfaces_ = [UIA.IRawElementProviderSimple]

	def __init__(self, hwnd):
		super().__init__()
		self._hwnd = hwnd

	def _get_ProviderOptions(self):
		return UIA.ProviderOptions_ServerSideProvider | UIA.ProviderOptions_UseComThreading

	def GetPatternProvider(self, patternId):
		return None

	def GetPropertyValue(self, propertyId):
		return None

	def _get_HostRawElementProvider(self):
		proveedor = POINTER(UIA.IRawElementProviderSimple)()
		_uia.UiaHostProviderFromHwnd(self._hwnd, byref(proveedor))
		return proveedor

	def anunciar(self, texto, actividad="app.anuncio"):
		return _uia.UiaRaiseNotificationEvent(
			self.QueryInterface(UIA.IRawElementProviderSimple),
			UIA.NotificationKind_Other, UIA.NotificationProcessing_ImportantMostRecent, texto, actividad,
		)

# En el hilo de la interfaz: self._uia = NotificadorUIA(self.panel.GetHandle()); self._uia.anunciar("Guardado")
```

### Otras

Tolk/cytolk (multilector, sin desarrollo activo) y Prism (nuevo; enlaces de Python en desarrollo).

## Braille

- No presupongas braille porque haya voz: envía el mismo texto a braille explícitamente, y permite desactivarlo.
- Mensajes cortos y con lo importante al principio: la línea braille muestra pocos caracteres.

## Arranque

- Anuncia «Iniciando...» de forma **síncrona** (`hablar(..., sincrono=True)`) antes de las importaciones pesadas: un hilo de voz no conseguiría el GIL a tiempo.
- No muestres una ventana de «espere» con el hilo principal bloqueado: se marca como «No responde» y el lector se bloquea al intentar leerla.
- Reducir el intervalo de cambio del GIL (`sys.setswitchinterval(0.001)`) ayuda a que el hilo de la interfaz, y con él el lector, responda aunque haya hilos de fondo trabajando.

## Hilos

La voz se puede pedir desde cualquier hilo (`voz.hablar` encola). Pero los controles solo se tocan desde el hilo de la interfaz: `wx.CallAfter(funcion, ...)`, `wx.lib.delayedresult` o, con pypubsub, `wx.CallAfter(pub.sendMessage, ...)`.
