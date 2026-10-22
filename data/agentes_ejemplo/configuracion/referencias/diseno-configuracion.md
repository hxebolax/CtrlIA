# Diseño de la configuración

Cómo está montada `app/core/configuracion.py` y por qué. Todo lo que aquí se describe está cubierto por `tests/test_configuracion.py`.

## Archivo y formato

- Ruta: `data/config/settings.json`, calculada (junto a `main.py` desde código, junto al `.exe` compilado). `APP_DATA_DIR` la redirige en las pruebas.
- JSON en UTF-8, con sangría (legible y fácil de comparar), `ensure_ascii=False` (los acentos se leen tal cual).
- Estructura: una sección por tema y dentro sus claves, más `version_esquema` en la raíz.

```json
{
  "general": {"idioma": "es", "confirmar_salida": true},
  "apariencia": {"tamano_fuente": 11, "tema": "sistema"},
  "version_esquema": 2
}
```

- Nombres de clave en español, sin acentos ni espacios (`tamano_fuente`), estables: cambiarlos exige migración.
- **Por qué JSON** y no INI o el registro: tipos nativos (bool, número, lista), legible, portable y estándar. Un INI obliga a convertir tipos a mano; el registro de Windows rompe la portabilidad.
- **Qué NO va en la configuración:** secretos (van en `secretos.dat`), datos que crecen (historiales, listas de miles de elementos: van a una base de datos) y estado muy volátil (posición de reproducción cada segundo: guárdalo aparte o con guardado diferido).

## Carga

Orden de `cargar()`:

1. Si no hay archivo: valores por defecto, `primer_arranque = True` (útil para autodetectar el idioma solo la primera vez) y se escribe el archivo.
2. Se lee el JSON. Si está dañado (JSON inválido, no es un objeto, bytes que no son UTF-8):
   - se copia a `settings.json.corrupto-AAAAMMDD_HHMMSS` (se conserva para diagnosticar o recuperar a mano);
   - se intenta `settings.json.bak` (el último guardado bueno);
   - si tampoco sirve, valores de fábrica;
   - se registra con ERROR y `recuperacion` guarda un texto para decírselo al usuario al arrancar (en un diálogo accesible o por voz). No lo ocultes: el usuario debe saber que ha perdido ajustes.
3. Si el archivo existe pero **no se puede leer** (permisos, disco): se usan valores de fábrica en memoria y **no se sobrescribe** el archivo (se perdería la configuración real).
4. Migraciones pendientes (ver abajo).
5. Fusión: valores por defecto + lo leído encima, recursivamente. Lo desconocido se conserva.
6. Saneado: cada valor que no tenga el tipo del valor por defecto o no cumpla su regla se sustituye por el de fábrica (WARNING en el registro).
7. Si algo cambió respecto a lo leído (faltaban claves, se migró, se saneó), se guarda. Así el archivo se autocorrige una vez y no se reescribe en cada arranque.

## Escritura atómica

```
serializar a texto (bajo el candado de datos)
  → escribir settings.json.<pid>-<hilo>.tmp en la MISMA carpeta
  → flush + os.fsync
  → copiar settings.json actual a settings.json.bak
  → os.replace(temporal, settings.json)   (con reintentos)
```

- **Misma carpeta**: `os.replace` solo es atómico dentro del mismo volumen.
- **`fsync` antes del reemplazo**: sin él, un corte de luz puede dejar un archivo de longitud cero tras el renombrado.
- **Temporal único** por proceso e hilo: dos guardados simultáneos no escriben el mismo temporal.
- **Reintentos**: en Windows `os.replace` falla con `PermissionError` (WinError 5 o 32) si un antivirus, el indexador o una herramienta de copias tiene el archivo abierto un instante.
- **Si falla**: se borra el temporal, se registra y se lanza `ErrorConfiguracion`. El archivo original queda intacto (hay prueba). Quien llama decide si avisar al usuario.

## Hilos

- `RLock` para los datos (reentrante: un suscriptor puede volver a leer la configuración).
- Un `Lock` aparte para el disco: la serialización se hace con el candado de datos y la escritura con el de disco, así un hilo de fondo que guarda no bloquea las lecturas de la interfaz durante el `fsync`.
- `obtener` devuelve una **copia**: si devolviera la lista interna, un `append` del llamante cambiaría la configuración sin guardar ni avisar.
- Los avisos a suscriptores se reparten **fuera** del candado: un suscriptor lento o que llame a otro hilo no provoca interbloqueos.

## Claves «seccion.clave»

- `obtener("apariencia.tamano_fuente")`; `obtener("x.y", por_defecto)` si puede no existir; sin `por_defecto`, una clave inexistente lanza `KeyError` (un error de escritura en el nombre se ve enseguida).
- `establecer(clave, valor)` guarda y avisa; `establecer(..., guardar=False)` solo en memoria.
- `establecer_varias({...})` valida todo antes de cambiar nada y guarda una vez.
- Con `lote()`, varios `establecer` hacen un solo guardado y los avisos llegan al cerrar el bloque.

## Migraciones

`MIGRACIONES[n]` lleva el diccionario leído de la versión `n` a la `n + 1`. Se aplican antes de la fusión, sobre los datos tal como vienen del archivo.

```python
VERSION_ESQUEMA = 3

def _migrar_a_3(datos: dict) -> None:
	renombrar_clave(datos, "red.proxy", "red.servidor_proxy")
	eliminar_clave(datos, "general.modo_antiguo")
	# Cambio de formato: segundos → milisegundos
	red = datos.get("red", {})
	if isinstance(red.get("espera"), int):
		red["espera_ms"] = red.pop("espera") * 1000

MIGRACIONES = {1: _migrar_a_2, 2: _migrar_a_3}
```

- Un archivo sin `version_esquema` se considera versión 1.
- **Idempotentes**: si se ejecutan dos veces, el resultado es el mismo (`renombrar_clave` no pisa una clave nueva ya existente).
- **Una migración que falla** se registra con traza y la carga sigue: el saneado repondrá lo que falte. Arrancar con algún valor de fábrica es mejor que no arrancar.
- **Archivo de una versión más nueva** (versión mayor que la de la aplicación): no se migra nada, se conservan sus claves y no se rebaja el número de versión.
- Cada migración lleva su prueba con un archivo antiguo real.
- Para preferencias que pasan a ser secretos, la migración lee el valor, lo guarda en `AlmacenSecretos` y lo borra del JSON (ver [secretos.md](secretos.md)).

## Validación

- El **tipo** lo fija el valor por defecto. `True` no vale donde se espera un número, ni `1` donde se espera un booleano. Un entero vale donde se espera un decimal.
- `REGLAS` añade `minimo`, `maximo`, `opciones` (lista cerrada) y `longitud_maxima`.
- Al cargar: valor inválido → valor de fábrica con aviso. Al establecer: `ErrorValidacion` (el diálogo lo muestra y lleva el foco al control).
- Valores con `None` por defecto no comprueban tipo (útil para «sin definir»), pero es mejor usar un valor vacío del tipo correcto (`""`, `0`, `[]`).

## Avisos a suscriptores

```python
cancelar = config.suscribir(funcion, prefijo="apariencia.")
# funcion(clave, anterior, nuevo)
```

- Llega en el **hilo que hizo el cambio**. En la interfaz, usa siempre `wx.CallAfter` dentro del suscriptor.
- Los métodos se guardan con `weakref.WeakMethod`: al destruir el objeto, desaparece la suscripción. Aun así, las ventanas deben cancelar en `EVT_WINDOW_DESTROY`: el objeto Python de una ventana wx puede sobrevivir a su parte nativa, y tocar un control destruido lanza `RuntimeError`.
- Un suscriptor que lanza una excepción se registra con traza y no impide que los demás reciban el aviso.
- Solo se avisa si el valor **cambia**.
- Alternativa: si el proyecto ya tiene un bus de eventos, publica en él desde un suscriptor único en lugar de suscribir cada ventana.

## Guardado diferido

- `establecer_diferido(clave, valor, retraso=0.8)` cambia el valor al instante y agrupa las escrituras: una ráfaga de flechas en un control de volumen escribe una vez.
- Usa un `threading.Timer` (hilo daemon). Al cerrar la aplicación, llama a `guardar_pendiente()`; si no, el último cambio se pierde.
- Un fallo del guardado diferido se registra con traza (en un hilo de temporizador nadie recogería la excepción).

## Exportar e importar (si se pide)

- Exportar: copia del JSON; los secretos no están en él, así que no viajan.
- Importar: leer, validar que es un objeto, pasar por migraciones y saneado igual que en `cargar()`, y guardar. Nunca copies el archivo importado encima sin validarlo.

## Fuentes

- Python, `os.replace`: <https://docs.python.org/3/library/os.html#os.replace>
- Python, `os.fsync`: <https://docs.python.org/3/library/os.html#os.fsync>
- Python, `weakref.WeakMethod`: <https://docs.python.org/3/library/weakref.html#weakref.WeakMethod>
- Python, `json`: <https://docs.python.org/3/library/json.html>
