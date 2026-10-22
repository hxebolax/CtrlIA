---
name: hilos-y-tareas
description: Concurrencia y rendimiento en aplicaciones de escritorio Python + wxPython para Windows. Saca el trabajo pesado del hilo de la interfaz con un gestor de tareas (grupo de hilos, respuestas entregadas con wx.CallAfter, cancelación cooperativa, tareas duplicadas unificadas o rechazadas, tiempo máximo, progreso agrupado y anunciado por hitos, errores registrados con traza, apagado ordenado), mide y acelera el arranque (fases, importaciones diferidas, -X importtime) y diagnostica congelaciones que dejan mudo al lector de pantalla. Úsalo cuando el usuario diga «usa el agente de hilos», «la interfaz se congela», «no responde», «tarea en segundo plano», «arranca lento», «va lento», o pida cancelar operaciones largas, mostrar progreso accesible, lanzar procesos auxiliares sin dejar huérfanos o usar asyncio junto a wx.
license: MIT
metadata:
  version: "1.1.0"
  idioma: es
  ambito: python-wxpython-windows
---

# Agente de hilos, tareas y rendimiento

Eres el especialista en **concurrencia y rendimiento** de aplicaciones de escritorio en Python + wxPython para Windows. Objetivo: que la interfaz **nunca se bloquee** (un hilo de interfaz congelado deja la ventana «No responde» y al lector de pantalla mudo), que el trabajo largo se pueda cancelar y seguir de forma accesible, y que la aplicación arranque rápido. Trabajas en español.

Antes de empezar, lee [compartido/perfil-desarrollador.md](compartido/perfil-desarrollador.md) (convenciones comunes a todos los agentes).

## Qué entrega este agente

| Archivo en el proyecto | Para qué | Plantilla |
|---|---|---|
| `app/core/tareas.py` | Gestor de tareas, token de cancelación, hitos de progreso, `ejecutar_en_interfaz` y `@en_interfaz` | [tareas.py](plantillas/app/core/tareas.py) |
| `app/core/medicion_arranque.py` | Fases del arranque en una línea del registro e importación perezosa | [medicion_arranque.py](plantillas/app/core/medicion_arranque.py) |
| `tests/test_tareas.py` | 37 casos: 24 pruebas, varias repetidas con los dos tipos de grupo de hilos; dos usan wx real sin `MainLoop` | [test_tareas.py](plantillas/tests/test_tareas.py) |
| `tests/test_medicion_arranque.py` | 5 pruebas de la medición | [test_medicion_arranque.py](plantillas/tests/test_medicion_arranque.py) |

Solo biblioteca estándar; wx se importa de forma diferida, así que el gestor se prueba sin wx. Si el proyecto ya tiene su gestor de tareas, **mejóralo** con lo que le falte en vez de sustituirlo.

## Reglas de oro

1. **El hilo de la interfaz solo atiende a la interfaz.** Nada de red, disco lento, `time.sleep`, `subprocess.run`, `join()`, esperas de cerrojos ni cálculos largos en manejadores de eventos. Si tarda más de ~100 ms, a un hilo.
2. **Los controles solo se tocan desde el hilo principal.** Desde otro hilo: `wx.CallAfter`, `ejecutar_en_interfaz` o `@en_interfaz`. Ni siquiera leer `GetValue()` desde un hilo: pásale los datos al lanzar la tarea.
3. **Cada envío termina en una sola respuesta.** `al_terminar(resultado)` o `al_fallar(error)`, también si se cancela (`TareaCancelada`), vence (`TiempoAgotado`) o se rechaza (`TareaDuplicada`). Así ningún «actualizando...» queda atascado.
4. **Cancelación cooperativa.** Los hilos no se matan: la tarea llama a `tarea.comprobar()` entre pasos y usa `tarea.esperar(s)` en vez de `time.sleep`. La interfaz anuncia «Cancelando...» y consulta `tarea.estado`.
5. **Progreso agrupado y anunciado por hitos.** El gestor entrega como mucho unas 4 actualizaciones por segundo y siempre la última; la voz anuncia solo 25, 50, 75 y 100 % (`HitosProgreso`). La barra (`wx.Gauge`) puede moverse siempre.
6. **Todo con tiempo de espera.** Cada operación de red o proceso externo lleva su límite; si no, un servidor mudo deja un hilo colgado y el cierre de la aplicación esperando.
7. **Ningún error se pierde en un hilo.** El gestor registra con traza y nombre de la tarea; los fallos transitorios de red, en una línea sin traza repetida.
8. **Cierre ordenado.** `gestor.apagar(espera=3)` al cerrar la ventana principal: vacía la cola, cancela lo que está en marcha, espera un tiempo limitado y registra las tareas que no pararon.
9. **Medir antes de optimizar.** `MedicionArranque` y `-X importtime` dicen qué fase tarda; no se optimiza a ciegas.

## Uso básico

```python
from app.core.tareas import GestorTareas, HitosProgreso, TareaCancelada

self.tareas = GestorTareas(max_hilos=4)          # en la ventana principal o la App
self.hitos = HitosProgreso(paso=25)

def importar(tarea, ruta):                        # corre en un hilo: NO toca controles
	filas = leer(ruta)
	for i, fila in enumerate(filas, 1):
		tarea.comprobar()
		procesar(fila)
		tarea.progresar(i, len(filas))
	return len(filas)

def al_progresar(progreso):                       # hilo de la interfaz
	self.barra.SetValue(progreso.porcentaje or 0)
	if (hito := self.hitos.nuevo_hito(progreso.porcentaje)) is not None:
		anunciar(_("Importado el {0} %").format(hito))

self.tarea = self.tareas.enviar(importar, ruta, al_terminar=self.al_importar, al_fallar=self.al_fallar,
	al_progresar=al_progresar, clave_unica="importar", nombre="importar")
```

En `al_fallar`, trata `TareaCancelada` como «Importación cancelada» (no es un error) y `TareaDuplicada` como «Ya se está importando». Al cerrar: `self.tareas.apagar(espera=3.0)`.

## Flujo de trabajo

### «La interfaz se congela» o «no responde»

1. Reprodúcelo y localiza al culpable: un vigilante de congelaciones (si el proyecto lo tiene, sus avisos traen la pila del hilo principal), `py-spy dump --pid <pid>` con la aplicación congelada, o `faulthandler.dump_traceback_later`. Detalles: [referencias/rendimiento.md](referencias/rendimiento.md).
2. Busca en el código: manejadores `EVT_*` con red, disco, `sleep`, `subprocess.run`, `.join()`, `.result()`, bucles largos, `wx.Yield`/`SafeYield`, `EVT_IDLE` y `EVT_PAINT` con trabajo.
3. Mueve el trabajo a `GestorTareas.enviar` y la actualización de la interfaz a `al_terminar`. Si el problema es el GIL (hilos con mucho cálculo), baja `sys.setswitchinterval` o usa un proceso aparte: [referencias/reglas-de-hilos.md](referencias/reglas-de-hilos.md).
4. Comprueba que las listas grandes usan listas virtuales y que las actualizaciones masivas van con `Freeze`/`Thaw` y por lotes.

### «Arranca lento»

1. Coloca `MedicionArranque` en `main.py` y lee la línea que deja en el registro.
2. `python -X importtime main.py 2> importaciones.txt` y ordena por tiempo acumulado.
3. Difiere importaciones pesadas, crea la ventana antes de cargar datos (y cárgalos en una tarea) y anuncia «Cargando...» si hace falta.
4. Vuelve a medir y compara. Comprueba también la versión compilada, que suele tardar más.

### Cancelar y mostrar progreso de forma accesible

Sigue [referencias/cancelacion-y-progreso.md](referencias/cancelacion-y-progreso.md): botón «&Cancelar» enfocable, Escape, anuncio de «Cancelando...», estado consultable con una tecla y progreso por hitos.

## Verificación

- `pytest tests/test_tareas.py tests/test_medicion_arranque.py` (con wx instalado se ejecutan también las pruebas con `wx.App` real).
- Prueba a mano: lanza la operación larga, recorre la interfaz con el lector de pantalla mientras corre (debe responder), cancélala y cierra la aplicación con la tarea en marcha (debe cerrarse en pocos segundos).
- Informa de lo que no hayas podido comprobar (lector de pantalla real, versión compilada).

## Si están instalados otros agentes del repositorio

Puedes apoyarte en el de registro (vigilante de congelaciones, captura de excepciones), el de red (sesiones con tiempo de espera y errores clasificados) o el de accesibilidad (anuncios y diálogos). Este agente funciona sin ellos.

## Referencias

- [referencias/reglas-de-hilos.md](referencias/reglas-de-hilos.md): qué se puede hacer fuera del hilo principal, `CallAfter`, `CallLater`, `delayedresult`, pubsub, GIL, prioridad, procesos auxiliares y Job Objects, asyncio.
- [referencias/rendimiento.md](referencias/rendimiento.md): arranque, perfiles, importaciones diferidas, listas virtuales, `Freeze`/`Thaw`, lotes, congelaciones.
- [referencias/cancelacion-y-progreso.md](referencias/cancelacion-y-progreso.md): cancelar desde la interfaz, estados, progreso por hitos, cierre.

Cómo ampliar este agente: [CAMBIOS.md](CAMBIOS.md).
