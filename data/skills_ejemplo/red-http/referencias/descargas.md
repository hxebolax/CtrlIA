# Descargas de archivos

## Qué hace `descargar(...)`

```python
resultado = descargar(url, destino, sha256=None, reanudar=True, al_progresar=None, cancelacion=None,
	sesion=None, carpeta_temporal=None, tamano_maximo=None, conservar_parcial=True, sobrescribir=False,
	progresos_por_segundo=4.0)
```

1. **Streaming**: `stream=True` e `iter_content` en bloques de 64 KB. Nunca `r.content` para archivos: carga todo en memoria.
2. **Parcial**: escribe en `<nombre>.parcial` junto al destino (mismo disco, así `os.replace` es atómico) o en `carpeta_temporal` (por ejemplo, `data/temp`). Si están en discos distintos, copia y renombra en el destino. Guarda al lado `<nombre>.parcial.json` con el ETag, la fecha y el tamaño.
3. **Reanudación**: si hay parcial y `reanudar=True`, pide `Range: bytes=<tamaño>-` con `If-Range: <ETag o Last-Modified>`.
   - **206** con `Content-Range` que empieza donde toca: continúa añadiendo.
   - **200**: el servidor no admite rangos o el archivo cambió (el `If-Range` no coincide): empieza de cero, sin mezclar datos viejos y nuevos.
   - **416** con `Content-Range: bytes */<total>` igual al parcial: ya estaba completo.
   - Otro rango o un 416 distinto: borra el parcial y empieza de cero.
   - Pide `Accept-Encoding: identity`: los rangos y `Content-Length` se refieren a los bytes reales del archivo, no a una versión comprimida.
4. **Verificación**: el tamaño final debe coincidir con el anunciado (`Content-Length` o el total de `Content-Range`); si no, `ErrorDescarga` «se ha cortado» con `reintentar=True` y el parcial se conserva para continuar. Con `sha256`, se calcula leyendo el archivo por bloques; si no coincide, el parcial se borra (reanudar un archivo corrupto no sirve) y se lanza `ErrorIntegridad`.
5. **Movimiento atómico** con `os.replace`. Si el destino existe y `sobrescribir=False`, se usa «nombre (1).ext».
6. **Resultado**: `ResultadoDescarga(ruta, tamano, sha256, reanudada, segundos)`; `sha256` sirve para guardarlo y comprobar duplicados.

## Progreso

- `al_progresar(bytes_hechos, bytes_totales_o_None)` se llama como mucho `progresos_por_segundo` veces (4 por defecto) y siempre una última vez al terminar.
- Se llama **en el hilo de la descarga**: pásalo a la interfaz con el gestor de tareas del proyecto (`tarea.progresar(hecho, total)`, que agrupa las entregas) o con `wx.CallAfter`.
- Para la voz, anuncia por hitos (25, 50, 75, 100 %), no cada llamada. Con total desconocido, anuncia megas descargados cada cierto tiempo.
- Para mostrar velocidad y tiempo restante, calcula una media móvil de los últimos segundos; los valores instantáneos saltan demasiado para leerlos.

## Cancelación

- `cancelacion` admite un `threading.Event`, cualquier objeto con la propiedad `cancelado` (como un token de cancelación de tareas) o una función que devuelva `True`.
- Se comprueba en cada bloque. Al cancelar se lanza `DescargaCancelada` (categoría «cancelado»); con `conservar_parcial=True` el `.parcial` queda para reanudar, con `False` se borra.
- Si el servidor deja de enviar datos, la cancelación llega cuando vence el tiempo de lectura. Para que sea inmediata, cierra la respuesta desde el token de cancelación, o usa un tiempo de lectura corto en las descargas.
- En la interfaz: «Cancelando...» al pulsar y «Descarga cancelada» al confirmarse; ofrece «Reanudar» si el parcial se conservó.

## Nombres de archivo seguros

`nombre_desde_respuesta(respuesta)` usa `Content-Disposition` (`filename*=UTF-8''...` tiene prioridad sobre `filename=`, según RFC 6266) y si no, el último tramo de la URL. Siempre pasa por `nombre_seguro(nombre)`, que:

- se queda solo con la última parte (`..\..\Windows\evil.dll` → `evil.dll`; nunca se escribe fuera de la carpeta elegida);
- quita caracteres de control y de formato invisibles y sustituye `< > : " / \ | ? *` por `_`;
- quita puntos y espacios finales (Windows los elimina al crear el archivo y el nombre ya no coincide);
- antepone `_` a los nombres de dispositivo `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9` (también `COM¹`, `COM²`, `COM³`, `LPT¹`, `LPT²`, `LPT³`), con o sin extensión: `NUL.txt` apunta al dispositivo, no a un archivo;
- recorta a 150 unidades UTF-16 (NTFS admite 255 por componente y las rutas clásicas 260 en total) conservando la extensión; un emoji cuenta como dos;
- si no queda nada, usa el nombre de respaldo («descarga»).

Aunque el nombre sea seguro, pregunta antes de sobrescribir y muestra el nombre final al usuario (o anúncialo al terminar).

## Varias descargas

- Una tarea por descarga con `clave_unica` = URL (o el identificador del elemento): pedirla dos veces no la duplica.
- Limita las simultáneas (3-4) y por servidor; el resto espera en cola.
- Persistir la cola (URL, destino, SHA-256 esperado) en la base de datos permite reanudar todo tras cerrar la aplicación: los parciales ya están en disco.
- Para descargas masivas o de protocolos que requests no cubre (BitTorrent, segmentadas), un motor externo (aria2c) en un proceso auxiliar con tiempo de espera y sin consola.

## Fuentes

- RFC 9110, peticiones de rango (`Range`, `If-Range`, 206, 416): <https://www.rfc-editor.org/rfc/rfc9110#name-range-requests>
- RFC 6266, `Content-Disposition`: <https://www.rfc-editor.org/rfc/rfc6266>
- Nombres de archivo en Windows: <https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file>
- requests, contenido en streaming: <https://requests.readthedocs.io/en/latest/user/advanced/#body-content-workflow>
- `os.replace`: <https://docs.python.org/3/library/os.html#os.replace>
