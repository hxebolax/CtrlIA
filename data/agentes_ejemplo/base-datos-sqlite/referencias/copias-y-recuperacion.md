# Copias de seguridad, integridad y recuperación

## Copias en caliente

```python
ruta = bd.copia_seguridad("diaria")   # data/backups/app-AAAAMMDD_HHMMSS_ffffff-diaria.db
```

- Usa la **API `backup`** de SQLite (`Connection.backup`): copia coherente aunque la aplicación esté abierta, incluidos los cambios que aún están en el `-wal`. Copiar el `.db` con `shutil` o el Explorador con la base abierta puede dar una copia a la que le faltan datos o que está dañada.
- La copia se escribe como `.tmp` y se renombra al terminar: una copia a medias (disco lleno, cierre) nunca parece válida.
- Mientras dura, las escrituras esperan (el candado); las lecturas siguen.
- **Rotación**: se conservan las `copias_conservadas` más recientes de esa base (10 por defecto).
- **Copias automáticas**: antes de cada migración (`antes-de-migrar-vN`) y antes de restaurar (`antes-de-restaurar`).
- **Copias periódicas**: la aplicación decide cuándo (al arrancar si la última tiene más de un día, al cerrar...). Hazlas en un hilo si la base es grande.
- Para exportar a otra carpeta que elija el usuario, copia el archivo de `data/backups` resultante.
- `data/backups` no se distribuye y contiene datos del usuario.

## Restaurar

```python
bd.restaurar(ruta_copia)
```

1. Comprueba la copia con `quick_check` (abierta en solo lectura); si no está sana, error y no se toca nada.
2. Copia de seguridad del estado actual (`antes-de-restaurar`).
3. Cierra las conexiones de lectura (cada hilo abrirá una nueva al volver a leer).
4. Copia el contenido con la API `backup` sobre la conexión de escritura y reactiva WAL.
5. Aplica las migraciones que le falten a la copia (una copia antigua se pone al día).

Detén antes los hilos de fondo que escriban, y refresca todas las vistas después (sus cachés tienen datos de antes).

En la interfaz: un diálogo con la lista de copias (fecha legible y motivo, no el nombre de archivo), confirmación «¿Restaurar la copia del 25 de septiembre a las 10:03? Los cambios posteriores se perderán» con «No» por defecto, y aviso final del resultado.

## Integridad al arrancar

- `quick_check` al abrir (la plantilla lo hace por defecto): recorre la base una vez, detecta la mayoría de los daños y en bases de escritorio tarda poco.
- `integrity_check` (`bd.comprobar_integridad(completa=True)`) revisa además los índices; más lento: en un hilo, bajo demanda o de forma periódica.
- Resultado `[]` = sana.

## Plan de recuperación

Qué hace la plantilla al abrir, en orden:

1. **Bloqueo** («database is locked» o «busy»): reintenta con espera creciente. Si persiste, `ErrorBaseDatosBloqueada` y **no toca nada**. La aplicación debe decir que otro programa tiene la base abierta (otra instancia, antivirus, sincronización en la nube).
2. **Error que no es de corrupción** («unable to open database file»: permisos, carpeta inexistente, ruta demasiado larga): `ErrorBaseDatos`, sin tocar nada.
3. **Corrupción** («database disk image is malformed», «file is not a database» o `quick_check` distinto de «ok»):
   1. Aparta `app.db`, `app.db-wal` y `app.db-shm` como `*.danada-FECHA`, **todo o nada** (un `-wal` huérfano junto a una base nueva la estropearía).
   2. Busca la copia más reciente de `data/backups` que supere `quick_check` y la restaura.
   3. Si no hay ninguna sana, empieza con una base vacía.
   4. Aplica las migraciones.
   5. Deja `bd.recuperacion` con el motivo, los archivos apartados y la copia usada. `bd.recuperacion.mensaje()` da un texto para el usuario.
4. La aplicación **debe avisar** al usuario (diálogo accesible con texto de solo lectura) y registrar el incidente. Nunca en silencio: podría seguir trabajando creyendo que tiene todos sus datos.

### Recuperación manual de lo apartado

Los archivos `*.danada-*` se conservan. Con la herramienta de línea de órdenes de SQLite (`sqlite3.exe`, que no viene con Python):

```
sqlite3 app.db.danada-20260925_100322 ".recover" | sqlite3 recuperada.db
```

`.recover` rescata todo lo legible, incluso de páginas dañadas. Antes de copiarlo, junta el `.db` con su `-wal` renombrándolos a su nombre original en una carpeta aparte. Desde Python solo queda `iterdump()`, que se detiene en el primer error.

### Causas habituales de corrupción (para prevenirlas)

- Copiar o sincronizar el `.db` sin su `-wal` con la base abierta (nube, copias con el Explorador).
- Carpeta en una unidad de red: el bloqueo de archivos de red no es fiable y WAL no funciona en red.
- Dos procesos con bibliotecas SQLite distintas o sin coordinación sobre el mismo archivo.
- Borrar el `-wal` a mano «para limpiar».
- Fallos de disco o de memoria (raros, pero existen).

## VACUUM y optimize

- **VACUUM** reescribe la base entera y devuelve al disco el espacio de lo borrado. Necesita espacio libre del orden del tamaño de la base, bloquea las escrituras mientras dura y puede tardar segundos. Por eso `compactar_si_procede(minimo_mb=20, proporcion=0.25)` solo compacta si lo recuperable (`freelist_count × page_size`) supera los dos umbrales, y **se llama al arrancar, antes de lanzar hilos**. Nunca en el hilo de la interfaz durante el uso normal.
- Mide lo recuperable con `freelist_count`, no comparando el tamaño del archivo con `page_count` (que incluye las páginas libres).
- `PRAGMA auto_vacuum=INCREMENTAL` más `PRAGMA incremental_vacuum(N)` es una alternativa para bases que borran mucho, pero solo se puede activar en una base nueva o tras un VACUUM.
- **`PRAGMA optimize`**: barato; actualiza estadísticas del planificador. La plantilla lo hace al abrir (`0x10002`, SQLite 3.46+) y al cerrar. Tras crear índices, `bd.optimizar()`.

## Cierre ordenado

`bd.cerrar()`:

1. Marca la base como cerrada (cualquier operación posterior lanza `ErrorBaseDatos` y deja un aviso: una escritura tardía de un hilo no se pierde en silencio).
2. Cierra las conexiones de lectura.
3. `PRAGMA optimize` y `PRAGMA wal_checkpoint(TRUNCATE)`: pasa el `-wal` al `.db` y lo deja vacío, así el `.db` queda completo por sí solo (útil si el usuario copia la carpeta con la aplicación cerrada).
4. Cierra la conexión de escritura.

Llámalo al salir, después de detener los hilos de fondo. Si la aplicación termina con `os._exit()`, llámalo antes.

## Durabilidad con `synchronous=NORMAL`

En WAL, `NORMAL` nunca corrompe la base, pero un corte de luz o un cuelgue del sistema puede perder la última transacción confirmada. Un cierre inesperado de la aplicación no la pierde. Para datos donde eso no es aceptable (un pago, una firma), `synchronous=FULL` en esa base, a cambio de escrituras más lentas.

## Fuentes

- SQLite, API de copia en caliente: <https://www.sqlite.org/backup.html>
- Python, `Connection.backup`: <https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup>
- SQLite, cómo se corrompe una base (y cómo evitarlo): <https://www.sqlite.org/howtocorrupt.html>
- SQLite, recuperación con `.recover`: <https://www.sqlite.org/recovery.html>
- SQLite, VACUUM: <https://www.sqlite.org/lang_vacuum.html>
- SQLite, PRAGMA (`quick_check`, `integrity_check`, `wal_checkpoint`, `optimize`, `synchronous`): <https://www.sqlite.org/pragma.html>
- SQLite, WAL: <https://www.sqlite.org/wal.html>
