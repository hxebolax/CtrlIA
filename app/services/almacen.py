"""Acceso a la base de datos de Ctrl+IA (``data/db/ctrlia.db``).

Solo SQL y conversiones; nada de interfaz. Todas las claves de sesión tienen
la forma ``<origen>:<id>``:

- ``ctrlia:<uuid>``: sesión creada o continuada desde Ctrl+IA (tabla ``sesiones``).
- ``claude:<id>``, ``codex:<id>``, ``antigravity:<id>``: sesiones que existen
  en el propio agente y Ctrl+IA solo lee. Su título propio, si están ocultas o
  sus grupos viven en ``metadatos`` y ``sesion_grupo``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.base_datos import BaseDatos
from app.core.migraciones import MIGRACIONES

log = logging.getLogger(__name__)

NOMBRE_BASE = "ctrlia.db"
MAX_CARPETAS_RECIENTES = 30
#: Prefijo de las sesiones importadas de otro equipo (solo lectura).
PREFIJO_IMPORTADA = "importada:"
#: ``leida_hasta`` de una sesión que la persona ha marcado como no leída.
LEIDA_NUNCA = -1.0


@dataclass(slots=True)
class SesionPropia:
	clave: str
	proveedor: str
	titulo: str = ""
	carpeta: str = ""
	id_externo: str = ""
	modelo: str = ""
	esfuerzo: str = ""
	modo_permiso: str = ""
	creada: float = 0.0
	actividad: float = 0.0
	estado: str = "inactiva"
	no_leidas: int = 0
	bifurcada_de: str = ""
	reglas: list[str] = field(default_factory=list)
	extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MensajeGuardado:
	id: int
	sesion: str
	rol: str
	texto: str
	fecha: float
	detalle: str = ""
	herramienta: str = ""
	id_origen: str = ""
	datos: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Metadatos:
	clave: str
	titulo: str = ""
	oculta: bool = False
	favorita: bool = False
	nota: str = ""
	leida_hasta: float = 0.0
	terminada: float = 0.0


@dataclass(slots=True)
class EstadisticasMensajes:
	"""Cuánto se ha dicho en una sesión guardada en la base de datos (para el informe)."""

	total: int = 0
	usuario: int = 0
	asistente: int = 0
	herramientas: int = 0
	cambios: int = 0
	bytes: int = 0


@dataclass(slots=True)
class Grupo:
	"""Un grupo de sesiones (por ejemplo, todas las consultas de una investigación)."""

	id: int
	nombre: str
	posicion: int = 0
	descripcion: str = ""
	carpeta: str = ""
	agente: str = ""
	creado: float = 0.0
	sesiones: int = 0


@dataclass(slots=True)
class MensajeFavorito:
	"""Un mensaje marcado como favorito en cualquier sesión (propia, de otro programa o importada).

	``mensaje`` es su identificador estable dentro de la sesión (ver ``app.services.favoritos``).
	"""

	sesion: str
	mensaje: str
	rol: str = ""
	fecha: float = 0.0
	extracto: str = ""
	nota: str = ""
	marcado: float = 0.0


@dataclass(slots=True)
class Plantilla:
	id: int
	titulo: str
	texto: str
	usos: int = 0
	creada: float = 0.0
	usada: float = 0.0


@dataclass(slots=True)
class SkillCatalogo:
	"""Una skill (habilidad con su SKILL.md) del catálogo de la persona."""

	id: int
	nombre: str
	descripcion: str = ""
	categoria: str = ""
	carpeta: str = ""
	version: str = ""
	origen: str = "importado"
	notas: str = ""
	creado: float = 0.0
	modificado: float = 0.0


def nueva_clave() -> str:
	return f"ctrlia:{uuid.uuid4()}"


def nueva_clave_importada() -> str:
	return f"{PREFIJO_IMPORTADA}{uuid.uuid4()}"


def _json(texto: str, por_defecto: Any) -> Any:
	try:
		valor = json.loads(texto or "")
	except ValueError:
		return por_defecto
	return valor if isinstance(valor, type(por_defecto)) else por_defecto


class Almacen:
	"""Fachada de la base de datos. Seguro desde varios hilos (lo garantiza ``BaseDatos``)."""

	def __init__(self, ruta: Path | None = None) -> None:
		if ruta is None:
			from app.core.paths import ruta_base_datos

			ruta = ruta_base_datos(NOMBRE_BASE)
		self.bd = BaseDatos(ruta, MIGRACIONES)

	def cerrar(self) -> None:
		self.bd.cerrar()

	# -- Sesiones propias -------------------------------------------------------

	def guardar_sesion(self, sesion: SesionPropia) -> None:
		self.bd.ejecutar(
			"""INSERT INTO sesiones (clave, proveedor, id_externo, titulo, carpeta, modelo, esfuerzo, modo_permiso,
				creada, actividad, estado, no_leidas, bifurcada_de, reglas, extra)
			VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
			ON CONFLICT (clave) DO UPDATE SET proveedor=excluded.proveedor, id_externo=excluded.id_externo,
				titulo=excluded.titulo, carpeta=excluded.carpeta, modelo=excluded.modelo, esfuerzo=excluded.esfuerzo,
				modo_permiso=excluded.modo_permiso, actividad=excluded.actividad, estado=excluded.estado,
				no_leidas=excluded.no_leidas, bifurcada_de=excluded.bifurcada_de, reglas=excluded.reglas, extra=excluded.extra""",
			(
				sesion.clave, sesion.proveedor, sesion.id_externo, sesion.titulo, sesion.carpeta, sesion.modelo,
				sesion.esfuerzo, sesion.modo_permiso, sesion.creada or time.time(), sesion.actividad or time.time(),
				sesion.estado, sesion.no_leidas, sesion.bifurcada_de, json.dumps(sesion.reglas, ensure_ascii=False),
				json.dumps(sesion.extra, ensure_ascii=False),
			),
		)

	def _a_sesion(self, fila) -> SesionPropia:
		return SesionPropia(
			clave=fila["clave"], proveedor=fila["proveedor"], titulo=fila["titulo"], carpeta=fila["carpeta"],
			id_externo=fila["id_externo"], modelo=fila["modelo"], esfuerzo=fila["esfuerzo"],
			modo_permiso=fila["modo_permiso"], creada=fila["creada"], actividad=fila["actividad"], estado=fila["estado"],
			no_leidas=fila["no_leidas"], bifurcada_de=fila["bifurcada_de"], reglas=_json(fila["reglas"], []),
			extra=_json(fila["extra"], {}),
		)

	def sesion(self, clave: str) -> SesionPropia | None:
		fila = self.bd.consultar_uno("SELECT * FROM sesiones WHERE clave = ?", (clave,))
		return self._a_sesion(fila) if fila else None

	def sesiones(self) -> list[SesionPropia]:
		return [self._a_sesion(fila) for fila in self.bd.consultar("SELECT * FROM sesiones ORDER BY actividad DESC")]

	def ids_externos(self, proveedor: str) -> set[str]:
		filas = self.bd.consultar("SELECT id_externo FROM sesiones WHERE proveedor = ? AND id_externo != ''", (proveedor,))
		return {fila["id_externo"] for fila in filas}

	def actualizar_sesion(self, clave: str, **campos: Any) -> None:
		permitidos = {"titulo", "id_externo", "modelo", "esfuerzo", "modo_permiso", "actividad", "estado",
			"no_leidas", "bifurcada_de", "reglas", "extra", "carpeta"}
		cambios = {k: v for k, v in campos.items() if k in permitidos}
		if not cambios:
			return
		for clave_json in ("reglas", "extra"):
			if clave_json in cambios:
				cambios[clave_json] = json.dumps(cambios[clave_json], ensure_ascii=False)
		asignaciones = ", ".join(f"{nombre} = ?" for nombre in cambios)
		self.bd.ejecutar(f"UPDATE sesiones SET {asignaciones} WHERE clave = ?", (*cambios.values(), clave))  # noqa: S608 - nombres de columna de una lista blanca

	def borrar_sesion(self, clave: str) -> None:
		with self.bd.transaccion() as conexion:
			conexion.execute("DELETE FROM sesiones WHERE clave = ?", (clave,))
			conexion.execute("DELETE FROM metadatos WHERE clave = ?", (clave,))
			conexion.execute("DELETE FROM sesion_grupo WHERE clave = ?", (clave,))
			conexion.execute("DELETE FROM mensajes_favoritos WHERE sesion = ?", (clave,))

	# -- Mensajes -----------------------------------------------------------------

	def anadir_mensaje(self, sesion: str, rol: str, texto: str, *, detalle: str = "", herramienta: str = "",
			id_origen: str = "", datos: dict[str, Any] | None = None, fecha: float | None = None) -> int:
		return self.bd.insertar(
			"INSERT INTO mensajes (sesion, rol, texto, detalle, herramienta, fecha, id_origen, datos) VALUES (?,?,?,?,?,?,?,?)",
			(sesion, rol, texto, detalle, herramienta, fecha or time.time(), id_origen,
				json.dumps(datos or {}, ensure_ascii=False)),
		)

	def actualizar_mensaje(self, id_mensaje: int, texto: str | None = None, detalle: str | None = None,
			datos: dict[str, Any] | None = None) -> None:
		if texto is not None:
			self.bd.ejecutar("UPDATE mensajes SET texto = ? WHERE id = ?", (texto, id_mensaje))
		if detalle is not None:
			self.bd.ejecutar("UPDATE mensajes SET detalle = ? WHERE id = ?", (detalle, id_mensaje))
		if datos is not None:
			self.bd.ejecutar("UPDATE mensajes SET datos = ? WHERE id = ?", (json.dumps(datos, ensure_ascii=False), id_mensaje))

	def mensajes(self, sesion: str, desde_id: int = 0) -> list[MensajeGuardado]:
		filas = self.bd.consultar("SELECT * FROM mensajes WHERE sesion = ? AND id > ? ORDER BY id", (sesion, desde_id))
		return [
			MensajeGuardado(fila["id"], fila["sesion"], fila["rol"], fila["texto"], fila["fecha"], fila["detalle"],
				fila["herramienta"], fila["id_origen"], _json(fila["datos"], {}))
			for fila in filas
		]

	def _a_mensaje(self, fila) -> MensajeGuardado:
		return MensajeGuardado(fila["id"], fila["sesion"], fila["rol"], fila["texto"], fila["fecha"], fila["detalle"],
			fila["herramienta"], fila["id_origen"], _json(fila["datos"], {}))

	def mensaje(self, id_mensaje: int) -> MensajeGuardado | None:
		"""Un mensaje por su identificador (``None`` si no existe)."""
		fila = self.bd.consultar_uno("SELECT * FROM mensajes WHERE id = ?", (id_mensaje,))
		return self._a_mensaje(fila) if fila is not None else None

	def ultimos_mensajes(self, sesion: str, limite: int = 200) -> list[MensajeGuardado]:
		"""Los últimos ``limite`` mensajes de una sesión, del más nuevo al más antiguo."""
		filas = self.bd.consultar("SELECT * FROM mensajes WHERE sesion = ? ORDER BY id DESC LIMIT ?", (sesion, max(1, limite)))
		return [self._a_mensaje(fila) for fila in filas]

	def ultimo_mensaje(self, sesion: str, rol: str = "asistente") -> str:
		return self.bd.valor(
			"SELECT texto FROM mensajes WHERE sesion = ? AND rol = ? ORDER BY id DESC LIMIT 1", (sesion, rol), "",
		) or ""

	def buscar_mensajes(self, texto: str, limite: int = 500) -> list[MensajeGuardado]:
		"""Busca en todas las sesiones propias (sin distinguir mayúsculas)."""
		patron = f"%{texto}%"
		filas = self.bd.consultar(
			"SELECT * FROM mensajes WHERE texto LIKE ? OR detalle LIKE ? ORDER BY fecha DESC LIMIT ?", (patron, patron, limite),
		)
		return [
			MensajeGuardado(fila["id"], fila["sesion"], fila["rol"], fila["texto"], fila["fecha"], fila["detalle"],
				fila["herramienta"], fila["id_origen"], _json(fila["datos"], {}))
			for fila in filas
		]

	def estadisticas_mensajes(self) -> dict[str, EstadisticasMensajes]:
		"""Mensajes, quién los dijo, herramientas, archivos cambiados y bytes de cada sesión guardada."""
		resultado: dict[str, EstadisticasMensajes] = {}
		for fila in self.bd.consultar(
			"""SELECT sesion, rol, COUNT(*) AS n,
				SUM(LENGTH(CAST(texto AS BLOB)) + LENGTH(CAST(detalle AS BLOB)) + LENGTH(CAST(datos AS BLOB))) AS bytes
			FROM mensajes GROUP BY sesion, rol""",
		):
			datos = resultado.setdefault(fila["sesion"], EstadisticasMensajes())
			numero = int(fila["n"] or 0)
			datos.total += numero
			datos.bytes += int(fila["bytes"] or 0)
			if fila["rol"] == "usuario":
				datos.usuario += numero
			elif fila["rol"] == "asistente":
				datos.asistente += numero
			elif fila["rol"] == "herramienta":
				datos.herramientas += numero
			elif fila["rol"] == "cambio":
				datos.cambios += numero
		return resultado

	# -- Sesiones importadas de otro equipo -------------------------------------------

	def sesiones_importadas(self) -> dict[str, str]:
		"""``{identidad: clave}`` de las sesiones importadas (identidad = equipo + clave original)."""
		resultado: dict[str, str] = {}
		for fila in self.bd.consultar("SELECT clave, extra FROM sesiones WHERE clave LIKE ?", (PREFIJO_IMPORTADA + "%",)):
			datos = _json(fila["extra"], {}).get("importada")
			if isinstance(datos, dict) and datos.get("identidad"):
				resultado[str(datos["identidad"])] = fila["clave"]
		return resultado

	def reemplazar_mensajes(self, sesion: str, mensajes: list[dict[str, Any]]) -> None:
		"""Sustituye en un solo paso todos los mensajes de una sesión (al volver a importarla)."""
		with self.bd.transaccion() as conexion:
			conexion.execute("DELETE FROM mensajes WHERE sesion = ?", (sesion,))
			conexion.executemany(
				"INSERT INTO mensajes (sesion, rol, texto, detalle, herramienta, fecha, id_origen, datos) VALUES (?,?,?,?,?,?,?,?)",
				[
					(sesion, str(m.get("rol") or "asistente"), str(m.get("texto") or ""), str(m.get("detalle") or ""),
						str(m.get("herramienta") or ""), float(m.get("fecha") or 0), str(m.get("id_origen") or ""),
						json.dumps(m.get("datos") or {}, ensure_ascii=False))
					for m in mensajes
				],
			)

	# -- Metadatos de cualquier sesión ------------------------------------------------

	def metadatos(self) -> dict[str, Metadatos]:
		return {
			fila["clave"]: Metadatos(fila["clave"], fila["titulo"], bool(fila["oculta"]), bool(fila["favorita"]),
				fila["nota"], fila["leida_hasta"], fila["terminada"])
			for fila in self.bd.consultar("SELECT * FROM metadatos")
		}

	def fijar_leida_hasta(self, valores: dict[str, float]) -> None:
		"""Fija de una vez hasta cuándo está leída cada sesión (``{clave: segundos}``)."""
		if not valores:
			return
		with self.bd.transaccion() as conexion:
			conexion.executemany("INSERT OR IGNORE INTO metadatos (clave) VALUES (?)", [(c,) for c in valores])
			conexion.executemany("UPDATE metadatos SET leida_hasta = ? WHERE clave = ?", [(v, c) for c, v in valores.items()])

	def fijar_metadato(self, clave: str, **campos: Any) -> None:
		permitidos = {"titulo", "oculta", "favorita", "nota", "leida_hasta", "terminada"}
		cambios = {k: (int(v) if isinstance(v, bool) else v) for k, v in campos.items() if k in permitidos}
		if not cambios:
			return
		with self.bd.transaccion() as conexion:
			conexion.execute("INSERT OR IGNORE INTO metadatos (clave) VALUES (?)", (clave,))
			asignaciones = ", ".join(f"{nombre} = ?" for nombre in cambios)
			conexion.execute(f"UPDATE metadatos SET {asignaciones} WHERE clave = ?", (*cambios.values(), clave))  # noqa: S608 - lista blanca  # auditoria: ignorar sql-formateado - los nombres de columna salen de una lista blanca fija

	# -- Mensajes favoritos -------------------------------------------------------------

	@staticmethod
	def _a_favorito(fila) -> MensajeFavorito:
		return MensajeFavorito(fila["sesion"], fila["mensaje"], fila["rol"], fila["fecha"], fila["extracto"], fila["nota"],
			fila["marcado"])

	def marcar_favorito(self, sesion: str, mensaje: str, rol: str = "", fecha: float = 0.0, extracto: str = "",
			nota: str = "") -> None:
		"""Marca un mensaje como favorito (si ya lo era, pone al día sus datos y conserva la nota)."""
		self.bd.ejecutar(
			"""INSERT INTO mensajes_favoritos (sesion, mensaje, rol, fecha, extracto, nota, marcado) VALUES (?,?,?,?,?,?,?)
			ON CONFLICT (sesion, mensaje) DO UPDATE SET rol=excluded.rol, fecha=excluded.fecha, extracto=excluded.extracto,
				nota=CASE WHEN excluded.nota != '' THEN excluded.nota ELSE mensajes_favoritos.nota END""",
			(sesion, mensaje, rol, float(fecha or 0), extracto, nota, time.time()),
		)

	def poner_nota_favorito(self, sesion: str, mensaje: str, nota: str) -> bool:
		"""Escribe (o, vacía, quita) la nota de un favorito. Devuelve si el favorito existía."""
		return self.bd.ejecutar("UPDATE mensajes_favoritos SET nota = ? WHERE sesion = ? AND mensaje = ?",
			(nota, sesion, mensaje)) > 0

	def desmarcar_favorito(self, sesion: str, mensaje: str) -> bool:
		"""Quita un mensaje de favoritos. Devuelve si lo era."""
		return self.bd.ejecutar("DELETE FROM mensajes_favoritos WHERE sesion = ? AND mensaje = ?", (sesion, mensaje)) > 0

	def es_favorito(self, sesion: str, mensaje: str) -> bool:
		return bool(self.bd.valor(
			"SELECT 1 FROM mensajes_favoritos WHERE sesion = ? AND mensaje = ?", (sesion, mensaje), 0,
		))

	def favoritos_de_sesion(self, sesion: str) -> list[MensajeFavorito]:
		"""Los favoritos de una sesión, del mensaje más antiguo al más reciente."""
		filas = self.bd.consultar("SELECT * FROM mensajes_favoritos WHERE sesion = ? ORDER BY fecha, marcado", (sesion,))
		return [self._a_favorito(fila) for fila in filas]

	def favoritos(self) -> list[MensajeFavorito]:
		"""Todos los favoritos de todas las sesiones, del marcado más recientemente al más antiguo."""
		return [self._a_favorito(fila) for fila in self.bd.consultar("SELECT * FROM mensajes_favoritos ORDER BY marcado DESC")]

	# -- Grupos -----------------------------------------------------------------------

	def grupos(self) -> list[tuple[int, str]]:
		return [(fila["id"], fila["nombre"]) for fila in self.bd.consultar("SELECT id, nombre FROM grupos ORDER BY posicion, nombre")]

	def _a_grupo(self, fila) -> Grupo:
		return Grupo(fila["id"], fila["nombre"], fila["posicion"], fila["descripcion"], fila["carpeta"], fila["agente"],
			fila["creado"], fila["sesiones"])

	def grupos_detalle(self) -> list[Grupo]:
		"""Todos los grupos con sus datos y cuántas sesiones tiene cada uno, en su orden."""
		filas = self.bd.consultar(
			"""SELECT g.*, (SELECT COUNT(*) FROM sesion_grupo sg WHERE sg.grupo = g.id) AS sesiones
			FROM grupos g ORDER BY g.posicion, g.nombre""",
		)
		return [self._a_grupo(fila) for fila in filas]

	def grupo(self, id_grupo: int) -> Grupo | None:
		fila = self.bd.consultar_uno(
			"SELECT g.*, (SELECT COUNT(*) FROM sesion_grupo sg WHERE sg.grupo = g.id) AS sesiones FROM grupos g WHERE g.id = ?",
			(id_grupo,),
		)
		return self._a_grupo(fila) if fila else None

	def crear_grupo(self, nombre: str, descripcion: str = "", carpeta: str = "", agente: str = "") -> int:
		existente = self.bd.valor("SELECT id FROM grupos WHERE nombre = ?", (nombre,))
		if existente:
			return int(existente)
		posicion = self.bd.valor("SELECT COALESCE(MAX(posicion), 0) + 1 FROM grupos", (), 1)
		return self.bd.insertar(
			"INSERT INTO grupos (nombre, posicion, descripcion, carpeta, agente, creado) VALUES (?, ?, ?, ?, ?, ?)",
			(nombre, posicion, descripcion, carpeta, agente, time.time()),
		)

	def renombrar_grupo(self, id_grupo: int, nombre: str) -> None:
		self.bd.ejecutar("UPDATE grupos SET nombre = ? WHERE id = ?", (nombre, id_grupo))

	def actualizar_grupo(self, id_grupo: int, **campos: Any) -> None:
		"""Cambia nombre, descripción, carpeta o agente predeterminados de un grupo."""
		permitidos = {"nombre", "descripcion", "carpeta", "agente"}
		cambios = {k: str(v) for k, v in campos.items() if k in permitidos}
		if not cambios:
			return
		asignaciones = ", ".join(f"{nombre} = ?" for nombre in cambios)
		self.bd.ejecutar(f"UPDATE grupos SET {asignaciones} WHERE id = ?", (*cambios.values(), id_grupo))  # noqa: S608 - lista blanca  # auditoria: ignorar sql-formateado - los nombres de columna salen de una lista blanca fija

	def borrar_grupo(self, id_grupo: int) -> None:
		self.bd.ejecutar("DELETE FROM grupos WHERE id = ?", (id_grupo,))

	def mover_grupo(self, id_grupo: int, paso: int) -> int | None:
		"""Sube (``paso`` < 0) o baja un grupo en la lista. Devuelve su nueva posición (0 = primero) o ``None``."""
		ids = [identificador for identificador, _nombre in self.grupos()]
		if id_grupo not in ids:
			return None
		origen = ids.index(id_grupo)
		destino = max(0, min(len(ids) - 1, origen + paso))
		if destino == origen:
			return destino
		ids.insert(destino, ids.pop(origen))
		with self.bd.transaccion() as conexion:
			conexion.executemany("UPDATE grupos SET posicion = ? WHERE id = ?", [(i + 1, g) for i, g in enumerate(ids)])
		return destino

	def grupos_por_sesion(self) -> dict[str, list[str]]:
		resultado: dict[str, list[str]] = {}
		for fila in self.bd.consultar(
			"SELECT sg.clave, g.nombre FROM sesion_grupo sg JOIN grupos g ON g.id = sg.grupo ORDER BY g.posicion, g.nombre",
		):
			resultado.setdefault(fila["clave"], []).append(fila["nombre"])
		return resultado

	def sesiones_de_grupo(self, id_grupo: int) -> list[str]:
		"""Claves de las sesiones del grupo, en el orden que ha decidido la persona."""
		filas = self.bd.consultar("SELECT clave FROM sesion_grupo WHERE grupo = ? ORDER BY posicion, rowid", (id_grupo,))
		return [fila["clave"] for fila in filas]

	def anadir_a_grupo(self, clave: str, id_grupo: int) -> None:
		"""Añade la sesión al final del grupo (si ya estaba, no cambia nada)."""
		with self.bd.transaccion() as conexion:
			posicion = conexion.execute(
				"SELECT COALESCE(MAX(posicion), 0) + 1 FROM sesion_grupo WHERE grupo = ?", (id_grupo,),
			).fetchone()[0]
			conexion.execute(
				"INSERT OR IGNORE INTO sesion_grupo (clave, grupo, posicion, anadida) VALUES (?, ?, ?, ?)",
				(clave, id_grupo, posicion, time.time()),
			)

	def quitar_de_grupo(self, clave: str, id_grupo: int) -> None:
		self.bd.ejecutar("DELETE FROM sesion_grupo WHERE clave = ? AND grupo = ?", (clave, id_grupo))

	def mover_a_grupo(self, clave: str, origen: int, destino: int) -> None:
		"""Saca la sesión de ``origen`` y la pone al final de ``destino`` en un solo paso."""
		if origen == destino:
			return
		with self.bd.transaccion() as conexion:
			conexion.execute("DELETE FROM sesion_grupo WHERE clave = ? AND grupo = ?", (clave, origen))
			posicion = conexion.execute(
				"SELECT COALESCE(MAX(posicion), 0) + 1 FROM sesion_grupo WHERE grupo = ?", (destino,),
			).fetchone()[0]
			conexion.execute(
				"INSERT OR IGNORE INTO sesion_grupo (clave, grupo, posicion, anadida) VALUES (?, ?, ?, ?)",
				(clave, destino, posicion, time.time()),
			)

	def ordenar_grupo(self, id_grupo: int, claves: list[str]) -> None:
		"""Fija el orden de las sesiones del grupo; las que falten en ``claves`` quedan al final."""
		actuales = self.sesiones_de_grupo(id_grupo)
		orden = [c for c in dict.fromkeys(claves) if c in actuales] + [c for c in actuales if c not in claves]
		with self.bd.transaccion() as conexion:
			conexion.executemany(
				"UPDATE sesion_grupo SET posicion = ? WHERE grupo = ? AND clave = ?",
				[(i + 1, id_grupo, clave) for i, clave in enumerate(orden)],
			)

	def mover_en_grupo(self, clave: str, id_grupo: int, paso: int) -> int | None:
		"""Sube o baja una sesión dentro del grupo. Devuelve su nueva posición (0 = primera) o ``None``."""
		claves = self.sesiones_de_grupo(id_grupo)
		if clave not in claves:
			return None
		origen = claves.index(clave)
		destino = max(0, min(len(claves) - 1, origen + paso))
		if destino != origen:
			claves.insert(destino, claves.pop(origen))
			self.ordenar_grupo(id_grupo, claves)
		return destino

	# -- Plantillas -------------------------------------------------------------------

	def plantillas(self) -> list[Plantilla]:
		return [
			Plantilla(fila["id"], fila["titulo"], fila["texto"], fila["usos"], fila["creada"], fila["usada"])
			for fila in self.bd.consultar("SELECT * FROM plantillas ORDER BY usada DESC, titulo")
		]

	def guardar_plantilla(self, titulo: str, texto: str, id_plantilla: int | None = None) -> int:
		if id_plantilla:
			self.bd.ejecutar("UPDATE plantillas SET titulo = ?, texto = ? WHERE id = ?", (titulo, texto, id_plantilla))
			return id_plantilla
		return self.bd.insertar("INSERT INTO plantillas (titulo, texto, creada) VALUES (?, ?, ?)", (titulo, texto, time.time()))

	def usar_plantilla(self, id_plantilla: int) -> None:
		self.bd.ejecutar("UPDATE plantillas SET usos = usos + 1, usada = ? WHERE id = ?", (time.time(), id_plantilla))

	def borrar_plantilla(self, id_plantilla: int) -> None:
		self.bd.ejecutar("DELETE FROM plantillas WHERE id = ?", (id_plantilla,))

	# -- Carpetas recientes -----------------------------------------------------------

	def carpetas_recientes(self, maximo: int = 15) -> list[str]:
		filas = self.bd.consultar("SELECT ruta FROM carpetas_recientes ORDER BY usada DESC LIMIT ?", (maximo,))
		return [fila["ruta"] for fila in filas]

	def usar_carpeta(self, ruta: str) -> None:
		with self.bd.transaccion() as conexion:
			conexion.execute(
				"INSERT INTO carpetas_recientes (ruta, usada) VALUES (?, ?) ON CONFLICT (ruta) DO UPDATE SET usada = excluded.usada",
				(ruta, time.time()),
			)
			conexion.execute(
				"DELETE FROM carpetas_recientes WHERE ruta NOT IN (SELECT ruta FROM carpetas_recientes ORDER BY usada DESC LIMIT ?)",
				(MAX_CARPETAS_RECIENTES,),
			)

	def olvidar_carpeta(self, ruta: str) -> None:
		self.bd.ejecutar("DELETE FROM carpetas_recientes WHERE ruta = ?", (ruta,))

	# -- Catálogo de skills (habilidades con SKILL.md) ----------------------------------

	def _a_skill(self, fila) -> SkillCatalogo:
		return SkillCatalogo(fila["id"], fila["nombre"], fila["descripcion"], fila["categoria"], fila["carpeta"],
			fila["version"], fila["origen"], fila["notas"], fila["creado"], fila["modificado"])

	def skills_catalogo(self, categoria: str | None = None) -> list[SkillCatalogo]:
		"""Skills del catálogo ordenadas por categoría y nombre; ``categoria`` filtra (``""`` = sin categoría)."""
		if categoria is None:
			filas = self.bd.consultar(
				"SELECT * FROM skills ORDER BY categoria COLLATE NOCASE, nombre COLLATE NOCASE, id",
			)
		else:
			filas = self.bd.consultar(
				"SELECT * FROM skills WHERE categoria = ? COLLATE NOCASE ORDER BY nombre COLLATE NOCASE, id", (categoria,),
			)
		return [self._a_skill(fila) for fila in filas]

	def skill_catalogo(self, id_skill: int) -> SkillCatalogo | None:
		fila = self.bd.consultar_uno("SELECT * FROM skills WHERE id = ?", (id_skill,))
		return self._a_skill(fila) if fila else None

	def skill_por_carpeta(self, carpeta: str) -> SkillCatalogo | None:
		"""La skill de esa carpeta (sin distinguir mayúsculas, como Windows) o ``None``."""
		fila = self.bd.consultar_uno("SELECT * FROM skills WHERE carpeta = ? COLLATE NOCASE", (carpeta,))
		return self._a_skill(fila) if fila else None

	def crear_skill_catalogo(self, nombre: str, carpeta: str, *, descripcion: str = "", categoria: str = "",
			version: str = "", origen: str = "importado", notas: str = "") -> int:
		ahora = time.time()
		return self.bd.insertar(
			"""INSERT INTO skills (nombre, descripcion, categoria, carpeta, version, origen, notas, creado, modificado)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
			(nombre, descripcion, categoria, carpeta, version, origen, notas, ahora, ahora),
		)

	def actualizar_skill_catalogo(self, id_skill: int, **campos: Any) -> None:
		"""Cambia nombre, descripción, categoría, notas, versión o carpeta; anota la fecha de modificación."""
		permitidos = {"nombre", "descripcion", "categoria", "notas", "version", "carpeta", "origen", "modificado"}
		cambios = {k: v for k, v in campos.items() if k in permitidos}
		if not cambios:
			return
		cambios.setdefault("modificado", time.time())
		asignaciones = ", ".join(f"{nombre} = ?" for nombre in cambios)
		self.bd.ejecutar(f"UPDATE skills SET {asignaciones} WHERE id = ?", (*cambios.values(), id_skill))  # noqa: S608 - lista blanca  # auditoria: ignorar sql-formateado - los nombres de columna salen de una lista blanca fija

	def borrar_skill_catalogo(self, id_skill: int) -> None:
		self.bd.ejecutar("DELETE FROM skills WHERE id = ?", (id_skill,))

	def categorias_skills(self) -> list[str]:
		"""Categorías usadas en el catálogo (sin la vacía), en orden alfabético."""
		filas = self.bd.consultar(
			"SELECT DISTINCT categoria FROM skills WHERE categoria != '' ORDER BY categoria COLLATE NOCASE",
		)
		return [fila["categoria"] for fila in filas]
