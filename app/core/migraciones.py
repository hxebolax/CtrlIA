"""Migraciones del esquema de Ctrl+IA, en orden. La posición es el número de versión.

REGLAS:

- Nunca modifiques ni reordenes una migración ya publicada: añade otra al final.
- Cada una se ejecuta en su propia transacción junto con ``PRAGMA
  user_version``; si falla, la base queda en la versión anterior.
- Fechas como segundos desde 1970 (``REAL``, UTC): se ordenan y comparan
  sin conversiones y coinciden con lo que dan los agentes.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from app.core.base_datos import Migracion

log = logging.getLogger(__name__)


# ── Migración 5: los «agentes» del catálogo pasan a llamarse skills ───────────────
# Nombra a propósito lo de la 1.5.0 y anteriores (la tabla ``agentes``, ``data\agentes`` y
# ``data\agentes_ejemplo``, las claves ``agentes`` y ``agentes_presentados`` de ``sesiones.extra``).
# Las carpetas las mueve antes ``app.services.proteccion_skills`` al arrancar.

#: Carpetas de ``data`` de la 1.5.0 y anteriores → sus nombres nuevos.
CARPETAS_RENOMBRADAS = {"agentes_ejemplo": "skills_ejemplo", "agentes": "skills"}
#: Claves de ``sesiones.extra`` de la 1.5.0 y anteriores → sus nombres nuevos.
CLAVES_EXTRA_RENOMBRADAS = {"agentes": "skills", "agentes_presentados": "skills_presentadas"}


def _misma_ruta(una: str | Path, otra: str | Path) -> bool:
	return os.path.normcase(os.path.normpath(str(una))) == os.path.normcase(os.path.normpath(str(otra)))


def ruta_skill_renombrada(carpeta: str, carpetas_datos: Iterable[str | Path] = ()) -> str | None:
	"""Ruta nueva de una skill del catálogo que estaba en ``data\\agentes`` o ``data\\agentes_ejemplo``.

	Devuelve ``None`` si no hay que cambiarla:

	- no está dentro de una de esas carpetas;
	- su carpeta vieja sigue existiendo: es una skill de la persona que chocaba con
	  otra del mismo nombre en ``data\\skills`` y se quedó en su sitio, una carpeta
	  ajena que también se llama así, o una carpeta que aún no se ha movido;
	- no existe ni la vieja ni la nueva y su ``data`` no es una de ``carpetas_datos``
	  (las de Ctrl+IA): no es nuestra y no se toca.
	"""
	texto = str(carpeta or "").strip()
	if not texto:
		return None
	ruta = Path(texto)
	partes = ruta.parts
	indice = next((i for i in range(len(partes) - 2, -1, -1)
		if partes[i].lower() == "data" and partes[i + 1].lower() in CARPETAS_RENOMBRADAS), None)
	if indice is None:
		return None
	nueva = Path(*partes[: indice + 1], CARPETAS_RENOMBRADAS[partes[indice + 1].lower()], *partes[indice + 2:])
	try:
		if ruta.is_dir():
			return None
		if not nueva.is_dir() and not any(_misma_ruta(Path(*partes[: indice + 1]), propia) for propia in carpetas_datos):
			return None
	except OSError:
		return None
	return os.path.normpath(str(nueva))


def carpetas_datos_propias() -> list[Path]:
	"""Las carpetas ``data`` de Ctrl+IA: la de los datos de la persona y la del programa."""
	try:
		from app.core.paths import ruta_base, ruta_datos

		return [ruta_datos(), ruta_base() / "data"]
	except Exception:  # noqa: BLE001 - sin rutas solo se cambian las que ya existen en su sitio nuevo
		log.debug("No se pudieron calcular las carpetas de datos", exc_info=True)
		return []


def renombrar_rutas_skills(conexion: sqlite3.Connection, carpetas_datos: Iterable[str | Path] | None = None) -> int:
	"""Apunta a ``data\\skills`` y ``data\\skills_ejemplo`` las skills de la tabla ``skills`` que estaban en las viejas.

	Nunca deja dos filas con la misma carpeta: si la nueva ya está en el catálogo, la fila se queda como estaba.
	Devuelve cuántas se han cambiado.
	"""
	propias = list(carpetas_datos) if carpetas_datos is not None else carpetas_datos_propias()
	cambiadas = 0
	for fila in conexion.execute("SELECT id, carpeta FROM skills").fetchall():
		identificador, carpeta = fila[0], fila[1]
		nueva = ruta_skill_renombrada(carpeta, propias)
		if nueva is None or _misma_ruta(nueva, carpeta):
			continue
		if conexion.execute("SELECT 1 FROM skills WHERE carpeta = ? COLLATE NOCASE AND id != ?", (nueva, identificador)).fetchone():
			log.warning("La skill %s no se ha apuntado a %s: esa carpeta ya está en el catálogo", carpeta, nueva)
			continue
		conexion.execute("UPDATE skills SET carpeta = ? WHERE id = ?", (nueva, identificador))
		log.info("Skill del catálogo apuntada a su carpeta nueva: %s → %s", carpeta, nueva)
		cambiadas += 1
	return cambiadas


def _renombrar_claves_extra(conexion: sqlite3.Connection) -> int:
	"""``sesiones.extra``: ``agentes`` → ``skills`` y ``agentes_presentados`` → ``skills_presentadas``."""
	cambiadas = 0
	for fila in conexion.execute("SELECT clave, extra FROM sesiones").fetchall():
		clave, texto = fila[0], fila[1]
		try:
			extra = json.loads(texto or "{}")
		except ValueError:
			log.warning("El extra de la sesión %s no es JSON válido; se deja como está", clave)
			continue
		if not isinstance(extra, dict) or not any(vieja in extra for vieja in CLAVES_EXTRA_RENOMBRADAS):
			continue
		for vieja, nueva in CLAVES_EXTRA_RENOMBRADAS.items():
			if vieja in extra:
				valor = extra.pop(vieja)
				extra.setdefault(nueva, valor)
		conexion.execute("UPDATE sesiones SET extra = ? WHERE clave = ?", (json.dumps(extra, ensure_ascii=False), clave))
		cambiadas += 1
	return cambiadas


def _migrar_catalogo_a_skills(conexion: sqlite3.Connection) -> None:
	"""Migración 5 (tras renombrar la tabla): rutas de las skills y claves de ``sesiones.extra``."""
	rutas = renombrar_rutas_skills(conexion)
	sesiones = _renombrar_claves_extra(conexion)
	log.info("Catálogo de skills migrado: %d rutas y %d sesiones actualizadas", rutas, sesiones)


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
	Migracion(
		"Catálogo de skills (en la 1.5.0 y anteriores, «agentes»)",
		sql="""
			-- Los «agentes» del catálogo de la 1.5.0 y anteriores son skills (carpetas con su SKILL.md).
			ALTER TABLE agentes RENAME TO skills;
			DROP INDEX IF EXISTS idx_agentes_categoria;
			CREATE INDEX idx_skills_categoria ON skills (categoria, nombre);
		""",
		funcion=_migrar_catalogo_a_skills,
	),
)
