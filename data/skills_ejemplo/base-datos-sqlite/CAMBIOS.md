# Cambios del agente base-datos-sqlite

## Cómo ampliarlo

- **Nueva capacidad de `BaseDatos`** (cifrado, varias bases, exportación): en `plantillas/app/core/base_datos.py`, con su prueba en `plantillas/tests/test_base_datos.py` y su explicación en la referencia que corresponda.
- **Nuevo patrón de repositorio** (upsert, FTS5, relaciones): en `plantillas/app/core/repositorio_ejemplo.py` o en un repositorio de ejemplo nuevo, con su migración en `plantillas/app/core/migraciones.py` y sus pruebas.
- **Nuevo error frecuente o regla**: en `SKILL.md` si es corta y general; si no, en `referencias/`.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.0.0 - 2026-09-25

- Primera versión: `BaseDatos` (archivo en `data/db`, escritura con `RLock` y lectura por hilo con `query_only`, WAL, `foreign_keys`, `busy_timeout`, `synchronous=NORMAL`, `optimize=0x10002` en SQLite 3.46+, transacciones `BEGIN IMMEDIATE` con SAVEPOINT anidado y registro del error, migraciones con `user_version` en transacción con copia previa, rechazo de versión futura y reconstrucción de tablas sin claves foráneas, parámetros con notas de SQL sin valores, copias en caliente con la API `backup` y rotación, restauración, `quick_check` al abrir con distinción entre bloqueo y corrupción y recuperación desde la última copia sana, `VACUUM` condicionado, cierre con checkpoint); `migraciones.py` de ejemplo; `RepositorioNotas` con búsqueda sin acentos, paginación OFFSET y keyset y `CacheBloques` para listas virtuales.
- Probado en este equipo (Windows 11, SQLite 3.50.4): **26 pruebas superadas** con Python 3.13 y también con Python 3.14 en un entorno propio de pruebas. `ruff check` (objetivo Python 3.10) sin avisos salvo la convención N818.
- Sin verificar: ejecución real con Python 3.10, 3.11 y 3.12 (solo se comprobó la sintaxis con `ruff --target-version py310`), varios procesos escribiendo en la misma base, bases de varios GB, unidades de red y la aplicación compilada con cx_Freeze.
