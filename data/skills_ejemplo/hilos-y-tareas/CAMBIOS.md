# Cambios del agente hilos-y-tareas

## Cómo ampliarlo

- **Nueva capacidad del gestor** (prioridades, reintentos automáticos, límites por clave...): en `plantillas/app/core/tareas.py`, con su prueba en `plantillas/tests/test_tareas.py` y su explicación en `referencias/`. Mantén la regla de que cada envío termina en una sola respuesta.
- **Nuevo tipo de error transitorio**: añade el nombre de la clase a `_NOMBRES_TRANSITORIOS` (o el atributo `transitorio = True` en la excepción propia) y un caso en `test_clasificacion_de_errores_transitorios`.
- **Nueva técnica de rendimiento o diagnóstico**: en `referencias/rendimiento.md`; si es una regla corta y general, también en `SKILL.md`.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- `GestorTareas.apagar` vacía la cola (`shutdown(cancel_futures=True)`) ANTES de cancelar lo que está en marcha y espera también a las tareas que siguen en `_activas` (con `wait(min(restante, 0.05))`); `_GrupoHilosDaemon._trabajar` cancela, en vez de ejecutar, lo que saca de la cola con el grupo ya cerrado. Síntoma: el apagado ordenado con hilos daemon fallaba alrededor de 1 de cada 12 veces.
- Pruebas: una determinista del grupo daemon cerrado (falla con el código anterior), otra que repite 40 apagados con cada tipo de grupo; las dos de wx real ya no usan `MainLoop` (al terminar, wx da la aplicación por cerrada) ni `ProcessPendingEvents` sin bucle activo: usan `esperar_con_bucle`. Las pruebas con wx ya no crean ni destruyen su propia `wx.App`: reutilizan la de la sesión (`wx.GetApp() or wx.App(False)`, guardada en una variable de módulo para que Python no la destruya) y nunca la destruyen. Crearla y destruirla por módulo dejaba sin aplicación a los módulos siguientes de la batería del proyecto.
- Probado: 42 pruebas con pytest, seis ejecuciones seguidas sin fallos.

## 1.0.0 - 2026-09-25

- Primera versión: `GestorTareas` (grupo de hilos daemon propio o `ThreadPoolExecutor`, respuestas en el hilo de la interfaz con ejecutor inyectable, una sola respuesta por envío, `TokenCancelacion`, claves únicas con unificación o rechazo, tiempo máximo con un único hilo vigilante, progreso agrupado, registro de errores con traza y de fallos transitorios en una línea deduplicada, nombre de la tarea en el del hilo, prioridad baja en Windows, apagado ordenado con espera limitada, estadísticas), `HitosProgreso`, `ejecutar_en_interfaz` y `@en_interfaz`; `MedicionArranque` con tiempo previo del proceso (`GetProcessTimes`) e `importar_perezoso`.
- Probado de verdad: 39 pruebas con pytest en Python 3.13 y wxPython 4.2.5 (las del gestor se ejecutan con los dos tipos de grupo de hilos; dos usan `wx.App` real: una atendiendo `wx.CallAfter` con `ProcessPendingEvents` y otra con `MainLoop` y `wx.CallLater`). Doce ejecuciones seguidas y cuatro rondas de dos ejecuciones simultáneas por suite sin fallos. Hallazgo: crear una segunda `wx.App` en el mismo proceso tras destruir la primera provocó una vez una corrupción de memoria de Windows (0xc0000374); las pruebas con wx comparten ahora una sola `wx.App` de módulo. Las comprobaciones de tiempo del progreso son proporcionales a lo que tarda la tarea, para no fallar en un equipo cargado. En Python 3.14 sin wx: 37 pasan y las 2 de wx se omiten. Sintaxis comprobada para Python 3.10.
- Sin verificar: comportamiento con un lector de pantalla real, versión compilada con cx_Freeze (`importar_perezoso` en el zip de bibliotecas) y Python 3.10 ejecutándose de verdad (no estaba instalado).
