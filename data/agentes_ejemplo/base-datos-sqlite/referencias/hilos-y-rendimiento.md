# Hilos y rendimiento

## Conexiones: por qué una de escritura y una de lectura por hilo

| Opción | Ventaja | Inconveniente |
|---|---|---|
| Una conexión con candado | Muy simple | Todo se serializa: la interfaz espera detrás de cada consulta de fondo |
| Una conexión por hilo para todo | Lecturas en paralelo | Varias conexiones escribiendo compiten por el bloqueo: «database is locked», reintentos |
| **Escritura con candado + lectura por hilo (plantilla)** | Escrituras ordenadas sin errores de bloqueo; lecturas en paralelo (WAL) | Algo más de código |

- Un objeto `sqlite3.Connection` no debe usarse a la vez desde dos hilos. La plantilla abre con `check_same_thread=False` porque el candado garantiza que la conexión de escritura la usa un hilo cada vez, y cada conexión de lectura pertenece a un solo hilo.
- En modo WAL, los lectores ven la última versión confirmada y no bloquean al escritor, ni el escritor a los lectores.
- Dentro de `bd.transaccion()`, las consultas del mismo hilo usan la conexión de escritura: así ven lo que aún no se ha confirmado.
- Las lecturas llevan `PRAGMA query_only=ON`: un `UPDATE` por la conexión equivocada falla en vez de saltarse el candado.
- Hilos efímeros (un `ThreadPoolExecutor` que se crea y se destruye): llama a `bd.cerrar_lectura_del_hilo()` al terminar la tarea para no acumular conexiones.
- **Varios procesos** (la aplicación y un proceso auxiliar): el candado de Python no los coordina. `busy_timeout` hace que esperen en vez de fallar, y `BEGIN IMMEDIATE` evita el error a mitad de transacción. Aun así, lo mejor es que un solo proceso escriba.

## «database is locked»: causas y soluciones

1. **Falta `busy_timeout`**: la conexión falla al instante en vez de esperar. La plantilla lo fija en todas.
2. **Transacción larga abierta**: un bucle que descarga de internet dentro de `bd.transaccion()` bloquea las escrituras de todos durante segundos. Descarga primero y escribe después, en una transacción corta.
3. **`BEGIN` diferido**: dos conexiones leen y luego intentan escribir; SQLite no puede subir el bloqueo de una sin romper la otra y devuelve «locked» aunque haya `busy_timeout`. `BEGIN IMMEDIATE` (la plantilla) toma el bloqueo de escritura al empezar.
4. **Un lector eterno**: un cursor sin consumir (`iterar` a medias) mantiene una instantánea y el WAL no se puede vaciar; el archivo `-wal` crece. Consume o cierra los cursores.
5. **Otro programa** (antivirus, sincronización en la nube, una copia de seguridad) tiene el archivo: al abrir, la plantilla reintenta y, si persiste, lanza `ErrorBaseDatosBloqueada` sin tocar nada. Aconseja no poner la carpeta de la aplicación dentro de OneDrive o Dropbox con la aplicación abierta.

## No bloquear la interfaz

- Regla: si una consulta puede tardar más de unos 50 ms (búsquedas sin índice, importaciones, recuentos sobre tablas enormes, `integrity_check`, `VACUUM`, copias de bases grandes), va en un hilo.

```python
def _al_buscar(self, _evento):
	texto = self.campo.GetValue()
	self.estado.SetLabel(_("Buscando..."))

	def trabajo():
		try:
			resultado = self.repo.pagina(0, 200, filtro=texto)
		except Exception:
			log.exception("Falló la búsqueda")
			wx.CallAfter(self._mostrar_error_busqueda)
			return
		wx.CallAfter(self._mostrar_resultados, resultado)

	threading.Thread(target=trabajo, name="busqueda", daemon=True).start()
```

- Los controles solo se tocan en el hilo principal (`wx.CallAfter`). En el callback, comprueba que la ventana sigue viva (`if not self: return`).
- Si el usuario lanza otra búsqueda antes de que acabe la anterior, descarta el resultado viejo (un contador de peticiones).
- Las consultas rápidas e indexadas (obtener una fila por id, un bloque de 200 filas por índice) sí pueden ir en el hilo principal: suelen tardar menos de un milisegundo.

## Índices

- Un índice por cada patrón de consulta frecuente: columnas del `WHERE` por igualdad primero, luego la de rango, y que coincida con el `ORDER BY` para evitar ordenar en memoria.
- Ejemplo: `WHERE cuenta_id = ? ORDER BY creada_en DESC, id DESC` → `CREATE INDEX ... ON tabla (cuenta_id, creada_en DESC, id DESC)`.
- Cada índice cuesta en cada escritura y en espacio. No indexes columnas que no se consultan ni dupliques el prefijo de otro índice.
- `LIKE '%texto%'` no puede usar índices (recorre la tabla). Para búsqueda de texto con muchas filas, FTS5.
- Tras crear índices o cambiar mucho los datos, `PRAGMA optimize` (la plantilla lo lanza al abrir con `0x10002` en SQLite 3.46+ y al cerrar) mantiene al día las estadísticas del planificador.

## EXPLAIN QUERY PLAN

```python
for fila in bd.consultar("EXPLAIN QUERY PLAN SELECT ... WHERE ... ORDER BY ...", parametros):
	print(fila["detail"])
```

Cómo leerlo:

- `SEARCH tabla USING INDEX idx_... (col=?)`: usa el índice. Bien.
- `SCAN tabla`: recorre toda la tabla. Aceptable en tablas pequeñas; en grandes, falta un índice.
- `USE TEMP B-TREE FOR ORDER BY`: ordena en memoria. El índice no casa con el `ORDER BY`.
- `USING COVERING INDEX`: resuelve la consulta solo con el índice. Lo mejor.

Convierte las consultas críticas en pruebas (`test_keyset_usa_el_indice`): si alguien cambia el índice o la consulta, la prueba lo detecta.

## Lotes

- `bd.ejecutar_lote(sql, filas)` hace `executemany` en una sola transacción: miles de filas en milisegundos, frente a segundos con un `ejecutar` (y un `fsync`) por fila.
- Para importaciones enormes, trocea en lotes de unos miles de filas (cada lote, una transacción) para que las lecturas de la interfaz vean progreso y la memoria no se dispare.
- `INSERT ... ON CONFLICT(clave) DO UPDATE SET ...` (upsert) evita el patrón «SELECT para ver si existe y luego INSERT o UPDATE», que además tiene condiciones de carrera.
- El problema N+1: un bucle que hace una consulta por elemento («para cada nota, busca su autor»). Sustitúyelo por un `JOIN` o por una consulta con `IN (marcadores(n))`.

## Paginación para listas grandes

Con `wx.ListCtrl` en modo `wx.LC_VIRTUAL`, la lista pide el texto de cada fila visible con `OnGetItemText(fila, columna)`; nunca cargues todas las filas.

- **LIMIT/OFFSET** (`repo.pagina(desplazamiento, limite)`): salta a cualquier posición, que es lo que necesita una lista virtual (el usuario pulsa Fin). Coste proporcional al desplazamiento: bien hasta decenas de miles de filas.
- **Keyset** (`repo.pagina_despues(cursor, limite)`): `WHERE (creada_en, id) < (?, ?)` con el mismo orden que el índice. Coste constante con millones de filas, ideal para «cargar más» o recorrer en orden. No permite saltar a la fila N.
- **`CacheBloques`**: carga bloques de 200 filas y guarda los últimos; `OnGetItemText` consulta la caché. Tras insertar o borrar: `cache.invalidar()` y `lista.SetItemCount(cache.total)`, conservando la selección y el foco por id (no por posición) para que el lector no pierda el sitio.
- El orden debe ser **estable** (añade el `id` como desempate): con un orden ambiguo, dos páginas pueden repetir o saltarse filas.

```python
class ListaNotas(wx.ListCtrl):
	def __init__(self, padre, repo):
		super().__init__(padre, style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.LC_SINGLE_SEL)
		self.InsertColumn(0, _("Título"))
		self.cache = CacheBloques(repo.pagina, repo.contar)
		self.SetItemCount(self.cache.total)

	def OnGetItemText(self, fila, columna):
		nota = self.cache.fila(fila)
		return nota.titulo if nota is not None else ""
```

## Fuentes

- SQLite, WAL: <https://www.sqlite.org/wal.html>
- SQLite, hilos: <https://www.sqlite.org/threadsafe.html>
- SQLite, `BEGIN IMMEDIATE` y transacciones: <https://www.sqlite.org/lang_transaction.html>
- SQLite, EXPLAIN QUERY PLAN: <https://www.sqlite.org/eqp.html>
- SQLite, planificador de consultas e índices: <https://www.sqlite.org/queryplanner.html>
- SQLite, comparación de filas (row values): <https://www.sqlite.org/rowvalue.html>
- SQLite, `PRAGMA optimize`: <https://www.sqlite.org/pragma.html#pragma_optimize>
- Paginación keyset frente a OFFSET: <https://use-the-index-luke.com/no-offset>
- Python, `sqlite3`: <https://docs.python.org/3/library/sqlite3.html>
