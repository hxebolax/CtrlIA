# Arquitectura del registro

## Esquema

```
módulos (getLogger(__name__))
        │  propagan al raíz
        ▼
logger raíz ── ManejadorCola (QueueHandler)
                 ├─ FiltroRepeticiones   (resume ráfagas)
                 ├─ FiltroContexto       (ubicación, operación, sesión, origen de la excepción)
                 └─ prepare()            (mensaje final + traza como texto, redactados)
        │  cola en memoria (queue.SimpleQueue)
        ▼
QueueListener (hilo «registro-escritor», respect_handler_level=True)
        ├─ sesion_*.log     DEBUG+   FormatoLegible  ManejadorArchivoSeguro (20 MB × 2)
        ├─ sesion_*.jsonl   DEBUG+   FormatoJSON     ManejadorArchivoSeguro (20 MB × 2)
        ├─ incidencias.log  WARNING+ FormatoLegible  ManejadorArchivoSeguro (5 MB × 5)
        └─ consola          (solo con APP_DEPURACION=1 y si hay consola)
```

## Por qué así

- **Filtros en el manejador, no en el logger raíz.** Los filtros de un logger no ven los registros que llegan propagados desde loggers hijos; los del manejador, sí. Además se ejecutan en el hilo que registra, así que ven la operación (`contextvars`) correcta.
- **Cola.** Escribir, rotar o esperar a un antivirus en el hilo de la interfaz la congela, y con ella el lector de pantalla. El `QueueHandler` solo encola; el hilo escritor hace el resto.
- **`respect_handler_level=True`.** Sin él, el oyente entrega todo a todos los manejadores e `incidencias.log` recibiría DEBUG.
- **`prepare()` propio.** El estándar mezcla la traza en el mensaje (pensado para colas entre procesos). Aquí la cola es del mismo proceso: el mensaje se fija en el hilo que registra (evita leer objetos que otro hilo modifica después), la traza queda en `exc_text` (el JSON la guarda aparte) y se sueltan `exc_info` y los argumentos para no retener marcos de pila en memoria. La redacción se aplica al mensaje **y a la traza**.
- **Rotación por tamaño**, no por tiempo: una aplicación de escritorio no está abierta a medianoche, y así el espacio en disco está acotado.
- **`encoding="utf-8"` y `errors="backslashreplace"`.** Sin encoding, Windows usa la página de códigos local y un carácter no representable hace perder la línea.
- **`ManejadorArchivoSeguro`.** Si la rotación falla con `WinError 32` (otro programa tiene el archivo abierto), sigue escribiendo y lo reintenta al minuto. Sus propios errores van a `<archivo>.errores_registro`, no a `sys.stderr` (que en la versión compilada es el propio registro: habría un bucle).
- **Un archivo por sesión** + `incidencias.log` común: la sesión cuenta la historia completa de un uso («envíame el registro de cuando falló»); `incidencias.log` junta los problemas de todas. Se conservan las últimas `sesiones_conservadas` (10 por defecto).
- **Marca de sesión en curso.** Un cierre limpio la borra (`atexit`); si el proceso muere, se cuelga o lo matan, al siguiente arranque se avisa.

## Configuración

```python
registro.configurar_registro(
	nombre_app="MiAplicacion",
	version=APP_VERSION,
	nivel="INFO",                # o el de las preferencias; APP_DEPURACION=1 fuerza DEBUG
	sesiones_conservadas=10,
	max_mb_sesion=20,
	json_lines=True,
	paquetes_propios=("app",),   # para señalar el «código propio» en las trazas
)
```

- **Proceso auxiliar** (un worker del mismo ejecutable): `prefijo="worker", archivo_incidencias=False`, o una carpeta aparte. Otra opción es que envíe sus registros al proceso principal por una `multiprocessing.Queue` (receta del «Logging Cookbook» de Python).
- **Bibliotecas ruidosas** (`urllib3`, `httpx`, `asyncio`, `PIL`, `comtypes`...) quedan en WARNING salvo en depuración. Añade las del proyecto a `bibliotecas_ruidosas`.
- **Cambio de nivel en caliente:** `registro.cambiar_nivel("DEBUG")` desde las preferencias.
- **Cierre:** `detener_registro()` se registra con `atexit` y vacía la cola. Si la aplicación termina con `os._exit()` (para no colgarse con hilos no daemon), llama antes a `registro.detener_registro()`, o se perderán las últimas líneas.

## Contexto de operación

```python
with registro.operacion("importar-lista", avisar_si_supera=2.0):
	...
```

- Cada línea registrada dentro lleva `{importar-lista#12}`; las anidadas, `{exterior#3>interior#4}`.
- Registra inicio y fin con duración (DEBUG por defecto; WARNING si supera el umbral).
- Si se escapa una excepción, la registra una vez con traza y la relanza. Las operaciones exteriores dicen «error ya registrado» sin repetir la traza.
- Los `contextvars` no viajan solos a otros hilos ni a `wx.CallAfter`: usa `registro.con_contexto(funcion)`.

## Utilidades

- `@registro.registrar_excepciones("No se pudo guardar", valor_por_defecto=None)` para manejadores y tareas: registra con traza y la aplicación sigue.
- `registro.ejecutar_proceso([ffmpeg, "-i", ...])`: sin consola, sin entrada estándar, orden redactada, código de salida, duración y final de la salida de error.
- `registro.instalar_puente_wx()`: los mensajes internos de wxWidgets (`wxLogError`...) van al registro en lugar de abrir cuadros de wx.
- `registro.archivo_sesion_actual()`, `carpeta_registros()`, `ultimas_incidencias(n)` para un visor o un menú «Abrir registros».
- `registro.registrar_fallo_de_arranque(error)` como último recurso.
