# Rendimiento: arranque, interfaz y congelaciones

## Medir el arranque

1. **Fases** con `MedicionArranque` en `main.py`: una marca tras cada paso (registro, configuración, `import wx`, base de datos, ventana, datos iniciales) y `terminar()` cuando la ventana ya responde (`wx.CallAfter(medicion.terminar, umbral_aviso=3.0)`). Deja una línea como:
   `Arranque en 1.84 s (antes de main: 0.62 s) | registro 0.03 · wx 0.40 · ventana 0.90 · datos 0.49 | más lenta: ventana`.
   «Antes de main» es el tiempo desde que Windows creó el proceso (intérprete, versión compilada, primeras importaciones).
2. **Importaciones**: `python -X importtime main.py 2> importaciones.txt`. Cada línea da el tiempo propio y el acumulado en microsegundos; ordena por el acumulado y busca módulos pesados importados al arrancar sin necesidad. Con la variable `PYTHONPROFILEIMPORTTIME=1` también funciona.
3. **Perfil de funciones**: `python -m cProfile -o arranque.prof main.py` y `python -m pstats arranque.prof` (`sort cumulative`, `stats 30`). Para verlo en texto accesible, `pstats` es suficiente; no hace falta un visor gráfico.
4. **Muestreo sin tocar el código**: `py-spy record -o perfil.svg --pid <pid>` o `py-spy top --pid <pid>`; con la aplicación congelada, `py-spy dump --pid <pid>` enseña la pila de cada hilo. `py-spy` se instala aparte (no en el entorno de la aplicación) y puede necesitar permisos de administrador.
5. Mide también la **versión compilada**: con cx_Freeze cambian los tiempos de importación y la carga del zip de bibliotecas.

## Acelerar el arranque

- **Importaciones diferidas**: los módulos pesados (SDK de redes sociales, PIL, numpy, pandas, lectores de documentos, `requests` si no se usa al abrir) se importan dentro de la función que los usa. Python solo los carga la primera vez.
- **Tipos sin importar**: `from __future__ import annotations` y `if TYPE_CHECKING: from modulo import Clase`.
- `importar_perezoso("modulo")` (en `medicion_arranque.py`) devuelve el módulo sin ejecutarlo hasta el primer uso. En la versión compilada, cx_Freeze no ve importaciones por cadena: añade el módulo a `packages` o `includes`.
- **Ventana primero, datos después**: crea y muestra la ventana con lo mínimo, anuncia «Cargando...» si hace falta y carga los datos en una tarea. El usuario oye la ventana antes y puede empezar a moverse.
- **Nada de red al arrancar en el hilo principal**: comprobar actualizaciones, sincronizar cuentas o descargar avatares van en tareas, con clave única y después de mostrar la ventana.
- **Base de datos**: abrir una sola conexión, índices para las consultas del arranque, `PRAGMA journal_mode=WAL` y no cargar tablas enteras si solo se muestran las primeras filas.
- Evitar trabajo repetido: cachear en `data/cache` lo que se calcula en cada arranque (listas de sonidos, plantillas procesadas).

## Interfaz fluida

- **Listas grandes → lista virtual**: `wx.ListCtrl` con `wx.LC_REPORT | wx.LC_VIRTUAL`, `SetItemCount(n)` y `OnGetItemText(fila, columna)`. Solo se pintan las filas visibles; con miles de elementos, una lista normal tarda segundos en llenarse y congela la interfaz. Los lectores de pantalla leen bien las listas virtuales de informe.
- **`Freeze()`/`Thaw()`** alrededor de cambios masivos (vaciar y rellenar, mostrar u ocultar muchos controles): se repinta una sola vez. Usa `with wx.WindowUpdateLocker(ventana):` para no olvidar el `Thaw`. Devuelve después el foco y anuncia el cambio si el usuario estaba en esa lista.
- **Por lotes**: si llegan resultados continuamente (mensajes nuevos, filas de un análisis), acumúlalos y añade en bloque cada 200-500 ms con un `wx.Timer` o con el progreso agrupado del gestor, no uno a uno con cientos de `CallAfter`.
- **`EVT_IDLE`**: se dispara muy a menudo; nada de trabajo en él (si lo necesitas, `event.RequestMore()` con trozos muy pequeños, o mejor un temporizador).
- **`EVT_PAINT`**: solo pintar con lo ya calculado. Nada de E/S, consultas ni registros en bucle (un error en `EVT_PAINT` se repite en cada repintado).
- **`EVT_TEXT`**: una búsqueda al teclear se aplaza (por ejemplo, 300 ms con `wx.CallLater` reiniciado en cada tecla) y se ejecuta en una tarea con clave única, para no buscar en cada pulsación.
- **Temporizadores**: detén los `wx.Timer` y `wx.CallLater` al cerrar la ventana.

## Diagnosticar congelaciones

Síntoma: la ventana aparece como «No responde» y el lector de pantalla se calla o repite lo último.

1. **Vigilante**: un hilo envía cada 2 s un `wx.CallAfter` y mide cuánto tarda en atenderse. Si supera un umbral (3 s), vuelca con `sys._current_frames()` y `traceback.format_stack` la pila del hilo principal (**la última línea es la culpable**) y una línea por cada otro hilo. Si el proyecto ya tiene un vigilante, úsalo; si no, es un buen añadido.
2. **`faulthandler.dump_traceback_later(15, repeat=True, file=archivo)`**, rearmado periódicamente desde un temporizador de wx: vuelca todas las pilas aunque un hilo retenga el GIL.
3. **`py-spy dump --pid <pid>`** en el momento de la congelación, sin preparar nada.
4. Causas habituales: E/S en un manejador, `subprocess.run` sin hilo, `join()` o `future.result()` en el hilo principal, cerrojo compartido con un hilo que tarda, `wx.Yield` reentrante, importación pesada perezosa en el primer uso desde la interfaz, lista no virtual con miles de filas, bucle de repintado, o un hilo de cálculo que monopoliza el GIL (ver [reglas-de-hilos.md](reglas-de-hilos.md)).
5. «Lag» fino (0,3-1 s) también importa: el lector de pantalla consulta la ventana de forma síncrona y encola las teclas. Regístralo en DEBUG para ver su distribución.

## Fuentes

- `-X importtime`: <https://docs.python.org/3/using/cmdline.html#cmdoption-X>
- `cProfile` y `pstats`: <https://docs.python.org/3/library/profile.html>
- `importlib.util.LazyLoader`: <https://docs.python.org/3/library/importlib.html#importlib.util.LazyLoader>
- py-spy: <https://github.com/benfred/py-spy>
- `faulthandler`: <https://docs.python.org/3/library/faulthandler.html>
- `wx.ListCtrl` virtual y `wx.WindowUpdateLocker`: <https://docs.wxpython.org/wx.ListCtrl.html>, <https://docs.wxpython.org/wx.WindowUpdateLocker.html>
