# De informe de fallo a prueba de regresión

## El flujo

1. **Entender el síntoma.** Qué hizo el usuario, qué esperaba, qué ocurrió. Busca en los registros (`data/logs`) la traza y la ubicación (`archivo:línea`). Si no hay traza, eso también es un hallazgo: el error no se registró.
2. **Reproducir con una prueba**, lo más abajo posible en la pirámide:
   - si el fallo está en la lógica, una prueba sin wx con los datos del informe;
   - si está en la interfaz (un atajo que no responde, un diálogo que no guarda), una prueba wx sin mostrar;
   - solo si depende del foco real o de la entrada real, una prueba `interfaz`.
3. **Comprobar que la prueba falla por el motivo correcto.** Una prueba que falla por un `ImportError` o por una fixture mal puesta no reproduce nada. Lee el mensaje del fallo.
4. **Corregir el código** con el cambio mínimo que explica el síntoma.
5. **Ver pasar la prueba y ejecutar la batería completa.** Una corrección que rompe otra prueba no está terminada.
6. **Dejar la prueba en el proyecto**, con un nombre que describa el comportamiento y una docstring con el origen (número de incidencia o identificador de auditoría).

## Si todavía no se puede corregir

Marca la prueba como fallo conocido, con el motivo:

```python
@pytest.mark.xfail(reason="A-07: al cancelar se pierde la selección; pendiente de rehacer el diálogo", strict=True)
def test_cancelar_conserva_la_seleccion(...):
	...
```

Con `strict=True` (o `xfail_strict = true` en la configuración), el día que la prueba empiece a pasar la batería falla con `XPASS(strict)`: es la señal para quitar la marca y cerrar la incidencia. Sin `strict`, la prueba quedaría marcada para siempre aunque el fallo ya no exista.

## Ejemplo completo

Está en [test_ejemplos.py](../plantillas/tests/test_ejemplos.py), bloque 8: la versión con el fallo cuenta una palabra de más con espacios dobles; la prueba `xfail` lo documenta; la versión corregida pasa la tabla completa de casos del informe.

En un proyecto real no hay dos versiones de la función: se escribe la prueba contra la función existente, se ve fallar, se corrige la función y la misma prueba pasa. El ejemplo conserva ambas solo para enseñar los dos momentos.

## Cómo escribir una buena prueba de regresión

- **Datos del informe**, no inventados: el texto, el archivo o la respuesta del servidor que provocó el fallo (anonimizados si son personales). Guarda archivos de ejemplo en `tests/datos/` si hace falta.
- **Una aserción que exprese el comportamiento esperado**, no el detalle interno: «la lista conserva el elemento enfocado tras insertar», no «se llamó a `SetItemState` dos veces».
- **Los casos vecinos**: si falló con dos espacios, prueba también con tabuladores, saltos de línea y cadena vacía (`parametrize`).
- **Independiente del entorno**: sin red, sin data real, sin depender de la fecha, del idioma de Windows ni de la carpeta desde la que se lanza.

## Fallos difíciles de reproducir

- **Condiciones de carrera entre hilos:** extrae la parte crítica y pruébala con hilos controlados (`threading.Event` para forzar el orden), no repitiendo la prueba mil veces.
- **Fallos que dependen del tiempo:** inyecta el reloj (`ahora=datetime.now` como parámetro) o parchea la función que da la hora.
- **Fallos de codificación:** prueba con nombres y textos con acentos, eñes y emojis, y con rutas con espacios. En Windows, abrir un archivo de texto sin `encoding="utf-8"` usa la página de códigos del sistema.
- **Fallos solo en la versión compilada:** la prueba no los reproduce; documenta el paso manual (compilar, arrancar, qué mirar en `data/logs`) y, si se puede, añade una prueba que compruebe la condición que los provoca (por ejemplo, que un recurso existe en la lista de archivos a incluir).
- **Congelaciones de la interfaz:** prueba que la operación lenta se ejecuta en un hilo (la función del manejador vuelve de inmediato y el resultado llega por `wx.CallAfter`).

## Qué contar al entregar

- El síntoma, la causa (`archivo:línea`) y la corrección.
- El nombre de la prueba que lo reproduce y que ahora pasa.
- El resultado real de la batería completa.
- Lo que no se pudo verificar automáticamente (lector de pantalla real, versión compilada).
