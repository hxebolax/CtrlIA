# Capturar todo lo no controlado

| Origen del error | Mecanismo | Qué hace la plantilla |
|---|---|---|
| Excepción que sale del hilo principal | `sys.excepthook` | CRITICAL con traza + archivo inmediato + aviso accesible |
| **Manejadores de eventos de wx**, `wx.CallAfter`, temporizadores | `sys.excepthook` | Igual. wxPython llama a `PyErr_Print()` cuando un manejador falla, y eso invoca `sys.excepthook`; la aplicación sigue viva (comprobado con wxPython real) |
| Hilo secundario (`threading.Thread`) | `threading.excepthook` | ERROR con el nombre del hilo + archivo inmediato |
| `__del__`, recolector, finalizadores | `sys.unraisablehook` | ERROR con el objeto |
| Tareas y callbacks de asyncio | `bucle.set_exception_handler(registro.manejador_excepciones_asyncio)` | ERROR con la tarea |
| `warnings` | `logging.captureWarnings(True)` | WARNING en `py.warnings` |
| Mensajes de wxWidgets (`wxLogError`...) | `wx.Log.SetActiveTarget` | `instalar_puente_wx()` |
| Fallo nativo (acceso inválido en una DLL, `abort`) | `faulthandler.enable(file=...)` | `fallo_nativo.log`, con el archivo abierto toda la sesión |
| Cuelgue del hilo de la interfaz | Vigilante con `sys._current_frames()` | `vigilante_ui.py` vuelca la pila culpable |
| `print` y bibliotecas que escriben en consola en la compilada | `sys.stdout`/`sys.stderr` son `None` con `base="gui"` | Se sustituyen por flujos que escriben en el registro |
| Fallo en el propio arranque | `try/except` en `main` | `arranque_fallido.log` + cartel de error (wx o, si no, `MessageBoxW`) |

## Detalles importantes

- **Orden en `main`:** configurar el registro, después importar wx y el resto. `multiprocessing.freeze_support()` al principio si se usa multiprocessing (imprescindible en la versión compilada).
- **`KeyboardInterrupt`** se deja al gancho original; `SystemExit` en hilos se ignora.
- **Archivo inmediato** (`no_controladas/fallo_*.log`): si el proceso muere justo después, la cola podría no vaciarse. Se conservan los 20 últimos.
- **Tormentas de excepciones:** un error en `EVT_PAINT`, `EVT_IDLE` o `EVT_TIMER` se repite sin parar. El filtro de repeticiones lo resume en el registro y el diálogo de error nunca se apila (uno abierto como máximo, con el recuento de los demás).
- **`faulthandler` en Windows** instala también un manejador de excepciones SEH. Con COM (comtypes, accesibilidad) puede escribir «Windows fatal exception: code 0x8001010d» por excepciones que después se manejan y no matan el proceso. No trates todo lo que aparezca en `fallo_nativo.log` como un cierre: crúzalo con el aviso de sesión no terminada.
- **Sin consola real, un fallo del propio logging no tiene dónde verse:** la plantilla pone `logging.raiseExceptions = False` en ese caso, y los manejadores de archivo anotan sus errores en `*.errores_registro`.
- **Trazas en la versión compilada:** llevan archivo, línea y función, pero a menudo no el texto de la línea (solo viajan los `.pyc`). Con `replace_paths=[("*", "")]` en cx_Freeze las rutas salen relativas (`app/ui/ventana.py`). Registra la versión en la cabecera y conserva el código de cada versión publicada para interpretar las líneas.

## Excepciones de la aplicación

Una jerarquía propia ayuda a decidir cómo registrar:

```python
class ErrorAplicacion(Exception):
	"""Base de los errores propios."""

	# Fallo ya diagnosticado que depende del usuario o del servidor (sesión
	# caducada, servidor que no responde con la API): se registra en una línea,
	# sin traza y sin repetir, para no enterrar los fallos de verdad.
	sin_traza = False


class ErrorRed(ErrorAplicacion):
	"""Problemas de conexión (reintentables)."""
```

- Errores **esperados y recuperables** (red caída, archivo no encontrado que el usuario eligió): WARNING o ERROR **sin traza** y mensaje claro al usuario.
- Errores **inesperados**: ERROR o CRITICAL **con traza**.
- Para distinguir fallos transitorios de red (5xx, 429, tiempos de espera) de errores reales, clasifícalos con códigos concretos, no buscando «500» dentro del texto (casaría con cualquier número que lo contenga).
