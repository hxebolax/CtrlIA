# Reglas de hilos en wxPython

## Qué se puede hacer fuera del hilo principal

Sí, desde cualquier hilo:

- Red, disco, bases de datos (cada hilo con su propia conexión SQLite, o una sola conexión protegida con cerrojo), análisis de texto, compresión, llamadas a procesos externos.
- `wx.CallAfter(funcion, *args)`: encola la llamada para que la ejecute el hilo principal. Es la puerta segura hacia la interfaz.
- `wx.PostEvent` / `wx.QueueEvent` con eventos propios. Para eventos con cadenas, `wx.QueueEvent` (la documentación advierte de que `PostEvent` no es seguro con campos de texto).
- Registrar con `logging` (es seguro entre hilos).

No, nunca desde otro hilo:

- Crear, cambiar, leer o destruir controles, ventanas, diálogos, menús o temporizadores (`SetValue`, `GetValue`, `Append`, `Show`, `SetFocus`, `wx.Timer`, `wx.CallLater`...). Puede funcionar «casi siempre» y fallar de forma aleatoria o colgar la aplicación.
- Llamar a la voz del lector de pantalla si la biblioteca no es segura entre hilos: pásala por `wx.CallAfter`.
- `wx.Yield`, `wx.SafeYield` o bucles de eventos propios.

Regla práctica: la tarea recibe **datos** (no controles) y devuelve **datos**; la respuesta en el hilo principal es la única que toca la interfaz.

## Herramientas de wx para pasar trabajo entre hilos

| Herramienta | Cuándo |
|---|---|
| `wx.CallAfter(f, *a)` | Desde un hilo, para ejecutar `f` en el hilo principal en cuanto quede libre |
| `wx.CallLater(ms, f, *a)` | Solo desde el hilo principal: ejecuta `f` pasado un tiempo (un temporizador de una vez). Guarda el objeto para poder hacer `Stop()` al cerrar |
| `wx.lib.delayedresult.startWorker(consumidor, productor)` | Alternativa incluida en wx: ejecuta `productor` en un hilo y `consumidor` en el principal. Sin cancelación ni control del número de hilos: para casos sencillos |
| `wx.lib.pubsub` / `pypubsub` | Mensajes entre partes de la aplicación. **No es seguro entre hilos por sí mismo**: publica desde el hilo principal (`wx.CallAfter(pub.sendMessage, "tema", dato=x)`) |
| `GestorTareas` de este agente | Grupo de hilos con cancelación, claves únicas, tiempos máximos, progreso agrupado y apagado |

`wx.CallAfter` necesita una `wx.App` viva; durante el cierre puede lanzar una excepción o no ejecutarse. Por eso el gestor deja de entregar respuestas en cuanto se llama a `apagar()`.

## El GIL y por qué la interfaz se atasca aunque el trabajo esté en un hilo

Python solo ejecuta código Python en un hilo a la vez (el GIL). Las esperas de red y disco lo liberan, así que para E/S los hilos funcionan muy bien. Pero un hilo que **calcula** (analizar cientos de documentos, la primera importación de un SDK grande) retiene el GIL y el hilo de la interfaz tiene que esperar su turno: el lector de pantalla se oye a tirones o se calla.

- `sys.setswitchinterval(0.001)` al principio de `main.py` (el valor por defecto es 0,005 s): el GIL cambia de hilo más a menudo y la interfaz recupera el turno antes. El coste es despreciable en una aplicación de escritorio.
- Trocear el cálculo: procesar por lotes y llamar a `tarea.comprobar()` entre lotes (cada llamada a una función de C que libera el GIL también ayuda).
- Si el cálculo es largo de verdad, un **proceso aparte** (ver más abajo). Más hilos no ayudan con CPU.
- La **prioridad de hilo** de Windows (`SetThreadPriority`, que el gestor aplica con `prioridad_baja=True`) solo decide quién usa la CPU cuando el GIL está libre; no reparte el GIL. Es un complemento, no la solución.
- En las compilaciones de Python sin GIL (3.13t, 3.14t) los hilos de cálculo sí corren en paralelo, pero wxPython y muchas extensiones aún pueden reactivar el GIL: no lo des por hecho sin medir.

## Cuántos hilos

- E/S (red, disco): entre 4 y 10. Más hilos solo sirven si de verdad hay esperas simultáneas y el servidor lo tolera.
- CPU: 1 o 2 hilos, y mejor un proceso.
- Operaciones que no deben solaparse (escribir la configuración, sincronizar una cuenta): una `clave_unica` o un gestor con `max_hilos=1` dedicado.

## Hilos daemon y cierre

Desde Python 3.9 los hilos de `ThreadPoolExecutor` no son daemon y el intérprete los espera al salir: una tarea atascada impide que el proceso termine (la ventana desaparece pero el proceso sigue vivo, bloquea archivos y la instancia única no deja volver a abrir). Por eso `GestorTareas(daemon=True)` usa un grupo propio con hilos daemon y `apagar()` da antes a las tareas la oportunidad de terminar limpias. Con `daemon=False` se usa `ThreadPoolExecutor` estándar.

Un hilo daemon se corta en seco al salir: no lo uses para escribir archivos importantes sin terminar. Guarda la configuración y la base de datos en el hilo principal durante el cierre, o espera esa tarea concreta antes de salir.

## Procesos auxiliares

Para trabajo que puede colgarse, consumir mucha memoria o bloquear el GIL mucho tiempo (bibliotecas de extracción, conversores, `ffmpeg`...):

- `subprocess.Popen`/`run` con **tiempo de espera** (`timeout=`), `creationflags=subprocess.CREATE_NO_WINDOW` (sin ventana de consola que robe el foco al lector) y la salida capturada o redirigida a un archivo del registro.
- Nunca `shell=True` con datos del usuario; pasa la orden como lista.
- Datos grandes o sensibles, por archivos temporales en `data/temp` y no por la línea de órdenes (queda visible para otros procesos).
- En la versión compilada no hay `python.exe`: el propio ejecutable se relanza con un argumento propio (`--trabajador`) o se usa `multiprocessing` con `multiprocessing.freeze_support()` al principio de `main`.
- **Sin huérfanos**: asigna el proceso principal a un **Job Object** de Windows con `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Todos los hijos entran en el mismo Job y Windows los mata cuando muere la aplicación, aunque sea de forma abrupta. El único que debe sobrevivir (un actualizador) se lanza con `CREATE_BREAKAWAY_FROM_JOB` y el Job con `JOB_OBJECT_LIMIT_BREAKAWAY_OK`. Con ctypes, fija `argtypes` y `restype` de cada función de `kernel32` (en 64 bits los descriptores se truncarían) y guarda el descriptor del Job durante toda la vida del proceso.
- Cancelar un proceso: `proceso.terminate()` desde `token.al_cancelar(...)`, luego `wait(timeout=5)` y, si no, `kill()`.

## asyncio junto a wx

wx tiene su propio bucle de eventos. Si una biblioteca solo ofrece API asíncrona:

- Un **hilo aparte** con su bucle: `bucle = asyncio.new_event_loop()`, `threading.Thread(target=bucle.run_forever, daemon=True).start()`.
- Desde la interfaz: `futuro = asyncio.run_coroutine_threadsafe(corrutina(), bucle)` y `futuro.add_done_callback(lambda f: wx.CallAfter(al_terminar, f))`. Nunca `futuro.result()` en el hilo principal.
- Cancelar: `futuro.cancel()` (cancela la corrutina en su bucle).
- Al cerrar: `bucle.call_soon_threadsafe(bucle.stop)`.
- Alternativa: `wxasync` integra ambos bucles, pero añade una dependencia y cambia el arranque de la aplicación; el hilo aparte es más simple y aislado.
- Instala un manejador de excepciones del bucle (`bucle.set_exception_handler`) que registre los errores de tareas olvidadas.

## Fuentes

- Documentación de wxPython, funciones `wx.CallAfter`, `wx.PostEvent`, `wx.QueueEvent`: <https://docs.wxpython.org/wx.functions.html>
- Wiki de wxPython, «LongRunningTasks»: <https://wiki.wxpython.org/LongRunningTasks>
- `sys.setswitchinterval`: <https://docs.python.org/3/library/sys.html#sys.setswitchinterval>
- `concurrent.futures` (hilos no daemon desde 3.9, `cancel_futures`): <https://docs.python.org/3/library/concurrent.futures.html>
- `asyncio.run_coroutine_threadsafe`: <https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe>
- Job Objects de Windows: <https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects>
