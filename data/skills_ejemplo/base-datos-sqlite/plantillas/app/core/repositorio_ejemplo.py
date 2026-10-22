"""Repositorio de EJEMPLO: tabla ``notas`` con índices, búsqueda y paginación.

Un repositorio agrupa TODO el SQL de una tabla: la interfaz y los servicios
llaman a métodos con nombre (``crear``, ``pagina``...) y nunca escriben SQL.
Así las consultas se revisan, se indexan y se prueban en un único sitio.

Paginación para listas virtuales grandes (``wx.ListCtrl`` con ``LC_VIRTUAL``):

- ``pagina(desplazamiento, limite)`` usa ``LIMIT/OFFSET``: acceso a cualquier
  posición, pero SQLite recorre y descarta las ``OFFSET`` filas anteriores; va
  bien hasta decenas de miles de filas.
- ``pagina_despues(cursor, limite)`` usa *keyset* (``WHERE (creada_en, id) <
  (?, ?)``): coste constante aunque haya millones, ideal para «cargar más» o
  recorrer en orden; no permite saltar a la fila 500 000 directamente.
- ``CacheBloques`` sirve ``OnGetItemText`` pidiendo bloques de filas y
  guardando los últimos en memoria.

Renómbralo y adáptalo (tabla, columnas, orden) a cada entidad del proyecto.

PLANTILLA del agente «base-datos-sqlite».
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, TypeVar

from app.core.base_datos import BaseDatos, marcadores

log = logging.getLogger(__name__)

T = TypeVar("T")


def normalizar_busqueda(texto: str) -> str:
	"""Minúsculas y sin acentos: «Camión Ñandú» → «camion nandu».

	``LOWER()``, ``LIKE`` y ``COLLATE NOCASE`` de SQLite solo entienden ASCII:
	no igualan «Á» con «á» ni «camión» con «camion». Por eso se guarda una
	columna normalizada en Python y se busca sobre ella.
	"""
	descompuesto = unicodedata.normalize("NFKD", texto.casefold())
	return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _escapar_like(texto: str) -> str:
	"""Escapa los comodines de LIKE para buscar el texto literal."""
	return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ahora_iso() -> str:
	"""Fecha y hora UTC en ISO 8601 con microsegundos: se ordena bien como texto."""
	return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class Nota:
	"""Fila de ``notas`` como objeto inmutable (seguro de pasar entre hilos)."""

	id: int
	titulo: str
	texto: str
	creada_en: str
	favorita: bool

	@property
	def cursor(self) -> tuple[str, int]:
		"""Clave de orden para ``pagina_despues``."""
		return (self.creada_en, self.id)


_COLUMNAS = "id, titulo, texto, creada_en, favorita"
# Orden estable: la fecha puede repetirse; el id desempata.
_ORDEN = "ORDER BY creada_en DESC, id DESC"


def _a_nota(fila) -> Nota:
	return Nota(fila["id"], fila["titulo"], fila["texto"], fila["creada_en"], bool(fila["favorita"]))


class RepositorioNotas:
	"""Acceso a la tabla ``notas`` (ver ``app/core/migraciones.py``)."""

	def __init__(self, bd: BaseDatos) -> None:
		self._bd = bd

	# ── Escritura ────────────────────────────────────────────────────────────

	def crear(self, titulo: str, texto: str = "", favorita: bool = False) -> int:
		"""Crea una nota y devuelve su id."""
		momento = ahora_iso()
		return self._bd.insertar(
			"INSERT INTO notas (titulo, titulo_busqueda, texto, creada_en, actualizada_en, favorita) "
			"VALUES (?, ?, ?, ?, ?, ?)",
			(titulo, normalizar_busqueda(titulo), texto, momento, momento, int(favorita)),
		)

	def crear_varias(self, notas: Iterable[tuple[str, str]]) -> int:
		"""Inserta muchas notas en UNA transacción (``executemany``)."""
		momento = ahora_iso()
		filas = [(t, normalizar_busqueda(t), x, momento, momento) for t, x in notas]
		self._bd.ejecutar_lote(
			"INSERT INTO notas (titulo, titulo_busqueda, texto, creada_en, actualizada_en) VALUES (?, ?, ?, ?, ?)",
			filas,
		)
		return len(filas)

	def actualizar(self, id_nota: int, *, titulo: str | None = None, texto: str | None = None,
			favorita: bool | None = None) -> bool:
		"""Cambia los campos indicados. ``False`` si la nota no existe."""
		cambios: dict[str, object] = {}
		if titulo is not None:
			cambios["titulo"] = titulo
			cambios["titulo_busqueda"] = normalizar_busqueda(titulo)
		if texto is not None:
			cambios["texto"] = texto
		if favorita is not None:
			cambios["favorita"] = int(favorita)
		if not cambios:
			return False
		cambios["actualizada_en"] = ahora_iso()
		# Los NOMBRES de columna salen de este código (lista blanca), nunca del usuario.
		asignaciones = ", ".join(f"{columna} = :{columna}" for columna in cambios)
		return self._bd.ejecutar(
			f"UPDATE notas SET {asignaciones} WHERE id = :id", {**cambios, "id": id_nota}
		) == 1

	def borrar(self, id_nota: int) -> bool:
		return self._bd.ejecutar("DELETE FROM notas WHERE id = ?", (id_nota,)) == 1

	def borrar_varias(self, ids: list[int]) -> int:
		if not ids:
			return 0
		return self._bd.ejecutar(f"DELETE FROM notas WHERE id IN ({marcadores(len(ids))})", ids)

	# ── Lectura ──────────────────────────────────────────────────────────────

	def obtener(self, id_nota: int) -> Nota | None:
		fila = self._bd.consultar_uno(f"SELECT {_COLUMNAS} FROM notas WHERE id = ?", (id_nota,))
		return _a_nota(fila) if fila is not None else None

	@staticmethod
	def _filtro(texto: str) -> tuple[str, tuple]:
		if not texto:
			return "", ()
		return "WHERE titulo_busqueda LIKE ? ESCAPE '\\'", (f"%{_escapar_like(normalizar_busqueda(texto))}%",)

	def contar(self, filtro: str = "") -> int:
		donde, parametros = self._filtro(filtro)
		return int(self._bd.valor(f"SELECT COUNT(*) FROM notas {donde}", parametros, 0))

	def pagina(self, desplazamiento: int, limite: int, filtro: str = "") -> list[Nota]:
		"""Filas ``[desplazamiento, desplazamiento + limite)`` en orden (LIMIT/OFFSET)."""
		donde, parametros = self._filtro(filtro)
		filas = self._bd.consultar(
			f"SELECT {_COLUMNAS} FROM notas {donde} {_ORDEN} LIMIT ? OFFSET ?",
			(*parametros, int(limite), int(desplazamiento)),
		)
		return [_a_nota(f) for f in filas]

	def pagina_despues(self, cursor: tuple[str, int] | None, limite: int, filtro: str = "") -> list[Nota]:
		"""Las ``limite`` notas siguientes a ``cursor`` (keyset). ``None`` = desde el principio."""
		condiciones, parametros = [], []
		donde, extra = self._filtro(filtro)
		if donde:
			condiciones.append(donde.removeprefix("WHERE "))
			parametros.extend(extra)
		if cursor is not None:
			# Comparación de filas (SQLite 3.15+): usa el índice (creada_en DESC, id DESC).
			condiciones.append("(creada_en, id) < (?, ?)")
			parametros.extend(cursor)
		donde_total = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
		filas = self._bd.consultar(
			f"SELECT {_COLUMNAS} FROM notas {donde_total} {_ORDEN} LIMIT ?", (*parametros, int(limite))
		)
		return [_a_nota(f) for f in filas]

	def plan_de(self, sql: str, parametros: tuple = ()) -> list[str]:
		"""``EXPLAIN QUERY PLAN`` legible (para pruebas y depuración de índices)."""
		return [fila["detail"] for fila in self._bd.consultar(f"EXPLAIN QUERY PLAN {sql}", parametros)]


class CacheBloques(Generic[T]):
	"""Caché por bloques para ``wx.ListCtrl`` virtual.

	``OnGetItemText(fila, columna)`` se llama muchas veces por fila y en el hilo
	de la interfaz: cada llamada no puede ir a la base. Se carga el bloque que
	contiene la fila (``tamano_bloque`` filas en una consulta indexada, del
	orden de un milisegundo) y se guardan los ``max_bloques`` más recientes.

	Tras un cambio en los datos: ``invalidar()`` y ``lista.SetItemCount(cache.total)``.
	"""

	def __init__(self, cargar: Callable[[int, int], list[T]], contar: Callable[[], int],
			tamano_bloque: int = 200, max_bloques: int = 8) -> None:
		self._cargar = cargar
		self._contar = contar
		self._tamano = tamano_bloque
		self._maximo = max_bloques
		self._bloques: OrderedDict[int, list[T]] = OrderedDict()
		self._total: int | None = None
		self._candado = threading.Lock()
		self.cargas = 0  # útil en pruebas y para medir

	@property
	def total(self) -> int:
		with self._candado:
			if self._total is None:
				self._total = self._contar()
			return self._total

	def fila(self, indice: int) -> T | None:
		"""Elemento en ``indice`` o ``None`` si está fuera de rango."""
		if indice < 0 or indice >= self.total:
			return None
		numero, posicion = divmod(indice, self._tamano)
		with self._candado:
			bloque = self._bloques.get(numero)
			if bloque is None:
				bloque = self._cargar(numero * self._tamano, self._tamano)
				self.cargas += 1
				self._bloques[numero] = bloque
				while len(self._bloques) > self._maximo:
					self._bloques.popitem(last=False)
			else:
				self._bloques.move_to_end(numero)
		return bloque[posicion] if posicion < len(bloque) else None

	def invalidar(self) -> None:
		with self._candado:
			self._bloques.clear()
			self._total = None
