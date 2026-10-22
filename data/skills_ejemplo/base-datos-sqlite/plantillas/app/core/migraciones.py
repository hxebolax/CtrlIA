"""Migraciones del esquema, en orden. La posición es el número de versión.

REGLAS (lee referencias/diseno-y-migraciones.md):

- Nunca modifiques ni reordenes una migración ya publicada: añade otra al final.
- Cada una se ejecuta en su propia transacción junto con ``PRAGMA
  user_version``; si falla, la base queda en la versión anterior.
- Las funciones reciben la conexión dentro de la transacción: no llames a
  ``commit`` ni a ``executescript``.
- Reconstruir una tabla (cambiar tipo, quitar ``NOT NULL``, añadir una clave
  foránea): ``desactivar_claves_foraneas=True`` y el procedimiento de 12 pasos.

EJEMPLO para la tabla ``notas`` del repositorio de ejemplo.

PLANTILLA del agente «base-datos-sqlite».
"""

from __future__ import annotations

import sqlite3

from app.core.base_datos import Migracion
from app.core.repositorio_ejemplo import normalizar_busqueda


def _v2_busqueda_y_favoritas(conexion: sqlite3.Connection) -> None:
	"""Añade columnas y RELLENA la de búsqueda para las filas que ya existían."""
	conexion.execute("ALTER TABLE notas ADD COLUMN favorita INTEGER NOT NULL DEFAULT 0")
	conexion.execute("ALTER TABLE notas ADD COLUMN titulo_busqueda TEXT NOT NULL DEFAULT ''")
	filas = conexion.execute("SELECT id, titulo FROM notas").fetchall()
	conexion.executemany(
		"UPDATE notas SET titulo_busqueda = ? WHERE id = ?",
		[(normalizar_busqueda(fila["titulo"]), fila["id"]) for fila in filas],
	)
	conexion.execute("CREATE INDEX idx_notas_busqueda ON notas (titulo_busqueda)")


MIGRACIONES: tuple[Migracion, ...] = (
	Migracion(
		"Tabla de notas",
		sql="""
			CREATE TABLE notas (
				id INTEGER PRIMARY KEY,
				titulo TEXT NOT NULL,
				texto TEXT NOT NULL DEFAULT '',
				creada_en TEXT NOT NULL,        -- ISO 8601 en UTC; se ordena como texto
				actualizada_en TEXT NOT NULL
			);
			-- Índice que casa con ORDER BY creada_en DESC, id DESC (paginación keyset)
			CREATE INDEX idx_notas_creada ON notas (creada_en DESC, id DESC);
		""",
	),
	Migracion("Búsqueda sin acentos y favoritas", funcion=_v2_busqueda_y_favoritas),
)
