# Diseño del esquema y migraciones

## Esquema

- **Clave primaria**: `id INTEGER PRIMARY KEY` (es el `rowid`, la búsqueda más rápida). Si los elementos vienen de un servicio externo, guarda su identificador en otra columna con `UNIQUE` (o `UNIQUE(cuenta_id, id_remoto)`), no como clave primaria de texto.
- **Tipos**: SQLite es flexible con los tipos (afinidad). Usa `INTEGER`, `REAL`, `TEXT`, `BLOB` y sé coherente. Para validarlos de verdad, tablas `STRICT` (SQLite 3.37+; comprueba `sqlite3.sqlite_version` en el Python del proyecto antes de usarlas).
- **Booleanos**: `INTEGER NOT NULL DEFAULT 0` y, si quieres, `CHECK (campo IN (0, 1))`. Conviértelos a `bool` en el repositorio.
- **Fechas**: texto ISO 8601 en UTC (`2026-09-25T10:03:22.517000+00:00`) con el mismo formato siempre: se ordena bien como texto y se lee sin ambigüedad. Convierte a hora local solo al mostrar.
- **Claves foráneas** con `ON DELETE CASCADE` cuando borrar el padre debe borrar los hijos. Solo funcionan con `PRAGMA foreign_keys=ON` en cada conexión (la plantilla lo hace).
- **Nada de JSON para datos que se consultan.** Una columna `TEXT` con JSON vale para datos opacos (la respuesta de una API guardada tal cual), no para algo por lo que se filtra u ordena: eso va en columnas propias con índice.
- **Texto para buscar**: columna normalizada en Python (minúsculas, sin acentos) con índice; para búsqueda de texto completo en muchos documentos, FTS5 (tabla virtual `USING fts5(...)` con `tokenize='unicode61 remove_diacritics 2'`), que sí ignora acentos.
- **Datos grandes** (imágenes, audio): en archivos dentro de `data/` y en la base solo la ruta relativa. Un BLOB de varios MB en una tabla muy consultada infla la base y las copias.

## Migraciones con `PRAGMA user_version`

`user_version` es un entero de la cabecera de la base reservado para la aplicación. La plantilla lo usa como número de la última migración aplicada.

```python
MIGRACIONES = (
	Migracion("Tabla de notas", sql="CREATE TABLE notas (...); CREATE INDEX ...;"),
	Migracion("Búsqueda sin acentos", funcion=_v2_busqueda),
	Migracion("Etiquetas", sql="CREATE TABLE etiquetas (...);"),   # la nueva, al final
)
```

Al abrir la base:

1. Si `user_version` es mayor que el número de migraciones, la base es de una versión más nueva de la aplicación: `ErrorVersionFutura` y no se toca nada (abrirla con código antiguo podría dañarla).
2. Si hay migraciones pendientes y la base tiene datos, **copia de seguridad** `app-FECHA-antes-de-migrar-vN.db`.
3. Cada migración pendiente se ejecuta **en su propia transacción**, junto con `PRAGMA user_version = N`. Si falla, se deshace entera (también el número de versión: la prueba lo comprueba) y se lanza `ErrorMigracion`; la base queda en la versión anterior y la aplicación decide qué hacer (avisar y cerrar, normalmente).

### Reglas

- **Nunca edites ni reordenes una migración publicada.** Los usuarios ya la aplicaron. Corrige con otra nueva.
- **SQL o función**: SQL para cambios de esquema; función cuando hay que transformar datos (rellenar una columna nueva calculándola en Python).
- **Sin `executescript` ni `commit`** dentro de una migración: `executescript` confirma la transacción en curso antes de empezar y rompería la atomicidad. La plantilla divide el SQL con `sqlite3.complete_statement` (respeta los `;` dentro de cadenas y de disparadores).
- **Sin comprobaciones sueltas** del tipo «si falla `SELECT columna`, haz `ALTER TABLE`» repartidas por el arranque, ni errores de migración tragados con un `log` y seguir: dejan bases en estados intermedios imposibles de reproducir.
- **Base existente sin versión**: la primera migración reproduce el esquema actual con `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, así sirve para bases nuevas y para las que ya existían.
- **Prueba cada migración** creando una base con las migraciones anteriores (`MIGRACIONES[:n]`), metiendo datos y abriéndola con la lista completa.

### Añadir una columna

```sql
ALTER TABLE notas ADD COLUMN favorita INTEGER NOT NULL DEFAULT 0;
```

- `NOT NULL` exige `DEFAULT`.
- Si su valor depende de otras columnas, rellénala en la misma migración (`UPDATE` o `executemany` desde una función).
- `ALTER TABLE ... RENAME COLUMN` (3.25+) y `DROP COLUMN` (3.35+) existen, con limitaciones (no se puede quitar una columna indexada, con `UNIQUE`, clave foránea...).

### Reconstruir una tabla (el procedimiento de 12 pasos)

Para lo que `ALTER TABLE` no permite (cambiar tipo o restricciones, añadir una clave foránea):

```python
Migracion(
	"notas.texto admite NULL",
	sql="""
		CREATE TABLE notas_nueva (... definición nueva ...);
		INSERT INTO notas_nueva (columnas) SELECT columnas FROM notas;
		DROP TABLE notas;
		ALTER TABLE notas_nueva RENAME TO notas;
		CREATE INDEX idx_notas_creada ON notas (creada_en DESC, id DESC);
	""",
	desactivar_claves_foraneas=True,
)
```

- `PRAGMA foreign_keys` no se puede cambiar dentro de una transacción: con `desactivar_claves_foraneas=True` la plantilla lo desactiva antes de `BEGIN`, ejecuta `PRAGMA foreign_key_check` antes de confirmar (si hay filas huérfanas, la migración falla y se deshace) y lo reactiva al final.
- Vuelve a crear índices, disparadores y vistas de la tabla: `DROP TABLE` los elimina.
- La prueba `test_migracion_con_reconstruccion_de_tabla` lo cubre.

## Repositorios

- Todo el SQL de una tabla en su clase (`RepositorioNotas`). La interfaz y los servicios llaman a métodos con nombre.
- Devuelven objetos inmutables (`@dataclass(frozen=True)`), no `sqlite3.Row` ni cursores: se pueden pasar entre hilos y a `wx.CallAfter` sin sorpresas.
- Las operaciones que tocan varias tablas van en una `bd.transaccion()`; los métodos del repositorio se unen a ella (misma conexión, un solo COMMIT).
- `UPDATE` con columnas variables: nombres desde una lista blanca del código, valores como parámetros con nombre.

## Fuentes

- SQLite, PRAGMA (`user_version`, `foreign_keys`, `foreign_key_check`): <https://www.sqlite.org/pragma.html>
- SQLite, ALTER TABLE y el procedimiento para otros cambios de esquema: <https://www.sqlite.org/lang_altertable.html>
- SQLite, tablas STRICT: <https://www.sqlite.org/stricttables.html>
- SQLite, claves foráneas: <https://www.sqlite.org/foreignkeys.html>
- SQLite, FTS5: <https://www.sqlite.org/fts5.html>
- Python, `sqlite3` (incluidos `complete_statement` y el control de transacciones): <https://docs.python.org/3/library/sqlite3.html>
