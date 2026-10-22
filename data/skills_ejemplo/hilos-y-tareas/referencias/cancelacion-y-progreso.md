# Cancelación y progreso accesibles

## Cancelar desde la interfaz

- Mientras dura una operación larga, un botón **«&Cancelar»** visible y enfocable (o la tecla Escape en el diálogo de progreso). Si la operación corre en la ventana principal, un atajo documentado en el menú (por ejemplo, «Cancelar la descarga actual»).
- Al pulsarlo: `tarea.cancelar()` (o `gestor.cancelar("clave")`), **anunciar «Cancelando...»** y deshabilitar el botón para no pedirlo dos veces. La tarea tardará lo que tarde en llegar a su siguiente `comprobar()`.
- Cuando llega `al_fallar(TareaCancelada)`: anunciar «Operación cancelada», restaurar los controles y devolver el foco a donde estaba el usuario. No es un error: no se muestra diálogo de error ni sonido de fallo.
- Si la cancelación tarda (más de 2-3 s), vuelve a anunciar que se está esperando a que termine el paso en curso.

## Escribir tareas cancelables

```python
def descargar_lista(tarea, urls):
	resultados = []
	for i, url in enumerate(urls, 1):
		tarea.comprobar()                     # lanza TareaCancelada si se pidió
		resultados.append(bajar(url, cancelacion=tarea.token))
		tarea.progresar(i, len(urls), url)
		if tarea.esperar(0.5):                 # pausa interrumpible entre peticiones
			tarea.comprobar()
	return resultados
```

- `tarea.esperar(s)` en vez de `time.sleep(s)`: se despierta al instante si se cancela.
- Operaciones que bloquean mucho (una respuesta HTTP en streaming, un proceso externo): registra con `tarea.token.al_cancelar(respuesta.close)` o `al_cancelar(proceso.terminate)` una acción que las desbloquee.
- El token tiene `is_set()`, así que sirve donde una biblioteca espera un `threading.Event`, y `cancelado`, que es lo que miran otras funciones de descarga.
- Deja las cosas coherentes al cancelar: borra temporales o conserva lo necesario para reanudar, cierra archivos (usa `with`) y no dejes registros a medias en la base de datos (transacciones).
- Si la función termina con éxito después de pedir la cancelación, el gestor entrega `TareaCancelada` igualmente: el usuario ya oyó «Cancelando...» y espera eso.

## Estado consultable

`tarea.estado` vale `pendiente`, `en_curso`, `cancelando`, `completada`, `fallida`, `cancelada` o `vencida`. `gestor.tareas_activas()` da la lista de lo que está en marcha.

Ofrece una forma de preguntar sin buscar con la vista: una tecla (por ejemplo, F12 o Ctrl+Mayús+P) que anuncie «Importando: 45 %, 120 de 260 archivos» o «No hay operaciones en curso», o una barra de estado cuyo texto se actualiza (el lector la lee con su orden de barra de estado).

## Progreso por hitos

- El gestor agrupa el progreso: una sola entrega en vuelo, como mucho una cada `intervalo_progreso` (0,25 s) y siempre la última. La interfaz no recibe una avalancha de `CallAfter` aunque la tarea llame a `progresar` miles de veces.
- **Visual**: `wx.Gauge` o `wx.ProgressDialog` se pueden actualizar en cada entrega.
- **Voz**: `HitosProgreso(paso=25)` decide cuándo anunciar (25, 50, 75, 100 %). Un anuncio por cada porcentaje interrumpe todo lo que el usuario intenta leer. Para operaciones muy largas, `paso=10` o un anuncio por tiempo (cada 30 s) según prefiera el usuario; hazlo configurable.
- Si el total es desconocido (`porcentaje` es `None`), anuncia cantidades («150 elementos procesados») cada cierto tiempo, no en cada elemento.
- `wx.ProgressDialog`: con `wx.PD_CAN_ABORT` añade el botón de cancelar; `Update(valor, mensaje)` devuelve si se pulsó. Los lectores leen el mensaje cuando cambia; evita cambiarlo en cada llamada o no pararán de hablar.
- Al terminar, un único anuncio final con el resultado («Importados 260 archivos, 3 con errores») y, si procede, un sonido.

## Tiempo máximo y operaciones que no responden

- `enviar(..., tiempo_maximo=60)`: al vencer, la interfaz recibe `TiempoAgotado` enseguida (el usuario no espera más) y se cancela el token para que la tarea pare cuando pueda. El resultado tardío se descarta.
- El tiempo máximo **no mata el hilo**: si la función está bloqueada sin tiempo de espera propio, seguirá ahí. Por eso las operaciones de red y procesos llevan siempre su propio límite.

## Cierre de la aplicación con tareas en marcha

1. En `EVT_CLOSE` de la ventana principal, si hay tareas importantes en curso, pregunta con un diálogo en español («Hay una descarga en curso. ¿Salir de todos modos?», botones «&Salir» y «&Cancelar»).
2. `gestor.apagar(espera=3.0)`: rechaza tareas nuevas, vacía la cola de pendientes, cancela las que están en curso, espera como mucho 3 s y registra las que no pararon.
   El orden importa: si se cancelara primero lo que está en marcha, el hilo que queda libre podría coger una pendiente y ejecutarla durante el cierre. La espera incluye también las tareas que un hilo acaba de sacar de la cola sin haberlas marcado aún como «en ejecución».
3. Tras `apagar`, el gestor ya no entrega respuestas: no intentes actualizar ventanas que se están destruyendo.
4. Si `apagar` devuelve `False`, el registro dice qué tareas no respetan la cancelación: corrígelas.

## Fuentes

- `wx.ProgressDialog`: <https://docs.wxpython.org/wx.ProgressDialog.html>
- `concurrent.futures.Future.cancel`: <https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel>
