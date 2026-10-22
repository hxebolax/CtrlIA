"""Migraciones del esquema de Ctrl+IA, en orden. La posición es el número de versión.

REGLAS:

- Nunca modifiques ni reordenes una migración ya publicada: añade otra al final.
- Cada una se ejecuta en su propia transacción junto con ``PRAGMA
  user_version``; si falla, la base queda en la versión anterior.
- Fechas como segundos desde 1970 (``REAL``, UTC): se ordenan y comparan
  sin conversiones y coinciden con lo que dan los agentes.
"""

from __future__ import annotations

from app.core.base_datos import Migracion

MIGRACIONES: tuple[Migracion, ...] = (
	Migracion(
		"Sesiones propias, mensajes, metadatos, grupos, plantillas y carpetas recientes",
		sql="""
			-- Sesiones creadas o continuadas desde Ctrl+IA.
			CREATE TABLE sesiones (
				clave TEXT PRIMARY KEY,            -- «ctrlia:<uuid>»
				proveedor TEXT NOT NULL,           -- claude, codex, antigravity, gemini...
				id_externo TEXT NOT NULL DEFAULT '',
				titulo TEXT NOT NULL DEFAULT '',
				carpeta TEXT NOT NULL DEFAULT '',
				modelo TEXT NOT NULL DEFAULT '',
				esfuerzo TEXT NOT NULL DEFAULT '',
				modo_permiso TEXT NOT NULL DEFAULT '',
				creada REAL NOT NULL,
				actividad REAL NOT NULL,
				estado TEXT NOT NULL DEFAULT 'inactiva',
				no_leidas INTEGER NOT NULL DEFAULT 0,
				bifurcada_de TEXT NOT NULL DEFAULT '',
				reglas TEXT NOT NULL DEFAULT '[]', -- JSON: reglas «permitir durante esta sesión»
				extra TEXT NOT NULL DEFAULT '{}'   -- JSON: opciones propias del agente
			);
			CREATE INDEX idx_sesiones_actividad ON sesiones (actividad DESC);

			-- Lo que se ha dicho en cada sesión propia, tal como lo vio Ctrl+IA.
			CREATE TABLE mensajes (
				id INTEGER PRIMARY KEY,
				sesion TEXT NOT NULL REFERENCES sesiones (clave) ON DELETE CASCADE,
				rol TEXT NOT NULL,                 -- usuario, asistente, herramienta, sistema, error
				texto TEXT NOT NULL DEFAULT '',
				detalle TEXT NOT NULL DEFAULT '',
				herramienta TEXT NOT NULL DEFAULT '',
				fecha REAL NOT NULL,
				id_origen TEXT NOT NULL DEFAULT '', -- id del mensaje en el agente (para actualizarlo)
				datos TEXT NOT NULL DEFAULT '{}'
			);
			CREATE INDEX idx_mensajes_sesion ON mensajes (sesion, id);

			-- Título propio, oculta, favorita y nota de CUALQUIER sesión (propia o externa).
			CREATE TABLE metadatos (
				clave TEXT PRIMARY KEY,
				titulo TEXT NOT NULL DEFAULT '',
				oculta INTEGER NOT NULL DEFAULT 0,
				favorita INTEGER NOT NULL DEFAULT 0,
				nota TEXT NOT NULL DEFAULT '',
				leida_hasta REAL NOT NULL DEFAULT 0
			);

			CREATE TABLE grupos (
				id INTEGER PRIMARY KEY,
				nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
				posicion INTEGER NOT NULL DEFAULT 0
			);
			CREATE TABLE sesion_grupo (
				clave TEXT NOT NULL,
				grupo INTEGER NOT NULL REFERENCES grupos (id) ON DELETE CASCADE,
				PRIMARY KEY (clave, grupo)
			);

			-- Mensajes guardados para reutilizar.
			CREATE TABLE plantillas (
				id INTEGER PRIMARY KEY,
				titulo TEXT NOT NULL,
				texto TEXT NOT NULL,
				usos INTEGER NOT NULL DEFAULT 0,
				creada REAL NOT NULL,
				usada REAL NOT NULL DEFAULT 0
			);

			CREATE TABLE carpetas_recientes (
				ruta TEXT PRIMARY KEY COLLATE NOCASE,
				usada REAL NOT NULL
			);
		""",
	),
	Migracion(
		"Grupos con descripción, carpeta y agente predeterminados, y orden propio de sus sesiones",
		sql="""
			-- Lo que la persona apunta sobre el grupo (por ejemplo, de qué trata una investigación)
			-- y con qué se crean las sesiones nuevas dentro de él.
			ALTER TABLE grupos ADD COLUMN descripcion TEXT NOT NULL DEFAULT '';
			ALTER TABLE grupos ADD COLUMN carpeta TEXT NOT NULL DEFAULT '';
			ALTER TABLE grupos ADD COLUMN agente TEXT NOT NULL DEFAULT '';
			ALTER TABLE grupos ADD COLUMN creado REAL NOT NULL DEFAULT 0;

			-- Orden de las sesiones dentro de cada grupo (lo decide la persona) y cuándo se añadieron.
			ALTER TABLE sesion_grupo ADD COLUMN posicion INTEGER NOT NULL DEFAULT 0;
			ALTER TABLE sesion_grupo ADD COLUMN anadida REAL NOT NULL DEFAULT 0;
			UPDATE sesion_grupo SET posicion = rowid;
			CREATE INDEX idx_sesion_grupo_grupo ON sesion_grupo (grupo, posicion);
		""",
	),
	Migracion(
		"Sesiones dadas por terminadas",
		sql="""
			-- Cuándo se dio la sesión por terminada (0 = no lo está). Si después hay
			-- actividad nueva, vuelve a contar como activa sin que nadie la desmarque.
			ALTER TABLE metadatos ADD COLUMN terminada REAL NOT NULL DEFAULT 0;
		""",
	),
	Migracion(
		"Mensajes favoritos y catálogo de agentes (habilidades)",
		sql="""
			-- Mensajes favoritos de cualquier sesión (propia, de otro programa o importada).
			-- «mensaje» es el identificador estable del mensaje dentro de su sesión (el id del
			-- agente o, si no tiene, una huella de rol, fecha y texto); el resto, para listarlos
			-- sin abrir la sesión.
			CREATE TABLE mensajes_favoritos (
				sesion TEXT NOT NULL,
				mensaje TEXT NOT NULL,
				rol TEXT NOT NULL DEFAULT '',
				fecha REAL NOT NULL DEFAULT 0,
				extracto TEXT NOT NULL DEFAULT '',
				nota TEXT NOT NULL DEFAULT '',
				marcado REAL NOT NULL DEFAULT 0,
				PRIMARY KEY (sesion, mensaje)
			);
			CREATE INDEX idx_favoritos_marcado ON mensajes_favoritos (marcado);

			-- Catálogo de agentes (habilidades con su SKILL.md) que la persona usa en sus sesiones.
			CREATE TABLE agentes (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				nombre TEXT NOT NULL,
				descripcion TEXT NOT NULL DEFAULT '',
				categoria TEXT NOT NULL DEFAULT '',
				-- Carpeta del agente (la que tiene el SKILL.md).
				carpeta TEXT NOT NULL UNIQUE,
				version TEXT NOT NULL DEFAULT '',
				-- «importado» (de una carpeta de la persona) o «creado» (desde Ctrl+IA).
				origen TEXT NOT NULL DEFAULT 'importado',
				notas TEXT NOT NULL DEFAULT '',
				creado REAL NOT NULL DEFAULT 0,
				modificado REAL NOT NULL DEFAULT 0
			);
			CREATE INDEX idx_agentes_categoria ON agentes (categoria, nombre);
		""",
	),
)
