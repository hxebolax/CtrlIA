"""Pruebas de app/core/base_datos.py y del repositorio de ejemplo.

PLANTILLA del agente «base-datos-sqlite». Trabajan en una carpeta temporal
(APP_DATA_DIR): nunca tocan data/db real.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.core import base_datos as mod
from app.core.base_datos import (
	BaseDatos,
	ErrorBaseDatos,
	ErrorBaseDatosBloqueada,
	ErrorMigracion,
	ErrorVersionFutura,
	Migracion,
	dividir_sentencias,
	identificador,
	marcadores,
)
from app.core.migraciones import MIGRACIONES
from app.core.repositorio_ejemplo import CacheBloques, RepositorioNotas, normalizar_busqueda


@pytest.fixture
def datos(tmp_path, monkeypatch):
	monkeypatch.setenv(mod.VARIABLE_DATA, str(tmp_path / "data"))
	return tmp_path / "data"


@pytest.fixture
def bd(datos):
	base = BaseDatos(migraciones=MIGRACIONES)
	yield base
	base.cerrar()


@pytest.fixture
def repo(bd):
	return RepositorioNotas(bd)


# ── Apertura y PRAGMAs ───────────────────────────────────────────────────────

def test_archivo_en_data_db_y_pragmas(bd, datos):
	assert bd.ruta == (datos / "db" / "app.db").resolve()
	assert bd.ruta.is_file()
	assert bd.valor("PRAGMA journal_mode") == "wal"
	assert bd.valor("PRAGMA foreign_keys") == 1
	assert bd.valor("PRAGMA busy_timeout") == 5000
	with bd.transaccion() as conexion:
		assert conexion.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_row_factory_por_nombre(bd, repo):
	repo.crear("Hola")
	fila = bd.consultar_uno("SELECT titulo FROM notas")
	assert fila["titulo"] == "Hola"


# ── Migraciones ──────────────────────────────────────────────────────────────

def test_migraciones_desde_cero(bd):
	assert bd.version() == len(MIGRACIONES) == 2
	columnas = {f["name"] for f in bd.consultar("PRAGMA table_info(notas)")}
	assert {"id", "titulo", "texto", "creada_en", "favorita", "titulo_busqueda"} <= columnas
	indices = {f["name"] for f in bd.consultar("PRAGMA index_list(notas)")}
	assert {"idx_notas_creada", "idx_notas_busqueda"} <= indices
	assert bd.copias() == [], "una base nueva no necesita copia previa"


def test_migracion_desde_version_intermedia(datos):
	v1 = BaseDatos(migraciones=MIGRACIONES[:1])
	v1.ejecutar_lote(
		"INSERT INTO notas (titulo, creada_en, actualizada_en) VALUES (?, ?, ?)",
		[("Camión", "2026-01-01", "2026-01-01"), ("Ñandú", "2026-01-02", "2026-01-02")],
	)
	assert v1.version() == 1
	v1.cerrar()

	v2 = BaseDatos(migraciones=MIGRACIONES)
	try:
		assert v2.version() == 2
		busqueda = [f[0] for f in v2.consultar("SELECT titulo_busqueda FROM notas ORDER BY id")]
		assert busqueda == ["camion", "nandu"], "la migración rellenó las filas existentes"
		copias = v2.copias()
		assert len(copias) == 1 and "antes-de-migrar-v1" in copias[0].name
		# La copia previa conserva la versión 1 intacta.
		conexion = sqlite3.connect(copias[0])
		assert conexion.execute("PRAGMA user_version").fetchone()[0] == 1
		conexion.close()
	finally:
		v2.cerrar()


def test_migracion_fallida_se_deshace_entera(datos):
	rota = (
		MIGRACIONES[0],
		Migracion("rota", sql="CREATE TABLE a_medias (x INTEGER); INSERT INTO no_existe VALUES (1);"),
	)
	with pytest.raises(ErrorMigracion) as info:
		BaseDatos(migraciones=rota)
	assert "migración 2" in str(info.value)
	conexion = sqlite3.connect(datos / "db" / "app.db")
	try:
		assert conexion.execute("PRAGMA user_version").fetchone()[0] == 1
		tablas = {f[0] for f in conexion.execute("SELECT name FROM sqlite_master WHERE type='table'")}
		assert "notas" in tablas and "a_medias" not in tablas
	finally:
		conexion.close()


def test_version_futura_se_rechaza_sin_tocar(datos):
	BaseDatos(migraciones=MIGRACIONES).cerrar()
	with pytest.raises(ErrorVersionFutura):
		BaseDatos(migraciones=MIGRACIONES[:1])


def test_migracion_con_reconstruccion_de_tabla(datos):
	"""Procedimiento de 12 pasos con claves foráneas desactivadas."""
	migraciones = (
		"CREATE TABLE padre (id INTEGER PRIMARY KEY);"
		"CREATE TABLE hijo (id INTEGER PRIMARY KEY, padre_id INTEGER REFERENCES padre(id), nota TEXT NOT NULL);"
		"INSERT INTO padre VALUES (1); INSERT INTO hijo VALUES (1, 1, 'a');",
		Migracion(
			"hijo.nota admite NULL",
			sql="""
				CREATE TABLE hijo_nueva (id INTEGER PRIMARY KEY, padre_id INTEGER REFERENCES padre(id), nota TEXT);
				INSERT INTO hijo_nueva (id, padre_id, nota) SELECT id, padre_id, nota FROM hijo;
				DROP TABLE hijo;
				ALTER TABLE hijo_nueva RENAME TO hijo;
			""",
			desactivar_claves_foraneas=True,
		),
	)
	base = BaseDatos(migraciones=migraciones)
	try:
		assert base.version() == 2
		base.ejecutar("INSERT INTO hijo (padre_id, nota) VALUES (1, NULL)")
		assert base.valor("PRAGMA foreign_keys") == 1
		with pytest.raises(sqlite3.IntegrityError):
			base.ejecutar("INSERT INTO hijo (padre_id) VALUES (99)")
	finally:
		base.cerrar()


def test_dividir_sentencias_respeta_cadenas_y_disparadores():
	script = """
		CREATE TABLE t (x TEXT);
		INSERT INTO t VALUES ('uno; dos');
		CREATE TRIGGER tr AFTER INSERT ON t BEGIN UPDATE t SET x = x || ';'; END;
		SELECT 1
	"""
	sentencias = dividir_sentencias(script)
	assert len(sentencias) == 4
	assert "'uno; dos'" in sentencias[1]
	assert sentencias[2].endswith("END;")


# ── Transacciones ────────────────────────────────────────────────────────────

def test_rollback_ante_error(bd, repo, caplog):
	repo.crear("antes")
	with pytest.raises(RuntimeError):
		with bd.transaccion() as conexion:
			conexion.execute(
				"INSERT INTO notas (titulo, creada_en, actualizada_en) VALUES ('dentro', 'x', 'x')")
			raise RuntimeError("fallo a mitad")
	assert [n.titulo for n in repo.pagina(0, 10)] == ["antes"]
	assert any("Transacción deshecha" in r.getMessage() for r in caplog.records)


def test_transaccion_anidada_con_savepoint(bd, repo):
	with bd.transaccion():
		repo.crear("exterior")
		with pytest.raises(ValueError):
			with bd.transaccion():
				repo.crear("interior")
				raise ValueError("solo se deshace lo interior")
		assert repo.contar() == 1  # dentro de la transacción se ve lo no confirmado
	assert [n.titulo for n in repo.pagina(0, 10)] == ["exterior"]


def test_error_sql_lleva_nota_sin_valores(bd):
	with pytest.raises(sqlite3.OperationalError) as info:
		bd.ejecutar("INSERT INTO no_existe VALUES (?)", ("dato privado",))
	notas = "".join(getattr(info.value, "__notes__", []))
	if hasattr(info.value, "add_note"):
		assert "no_existe" in notas and "str[12]" in notas
	assert "dato privado" not in notas


def test_parametros_evitan_inyeccion(repo, bd):
	malicioso = "x'); DROP TABLE notas; --"
	id_nota = repo.crear(malicioso)
	assert repo.obtener(id_nota).titulo == malicioso
	assert bd.valor("SELECT COUNT(*) FROM sqlite_master WHERE name = 'notas'") == 1
	assert marcadores(3) == "?, ?, ?"
	assert identificador("creada_en") == '"creada_en"'
	with pytest.raises(ValueError):
		identificador("id; DROP TABLE notas")


# ── Copias y recuperación ────────────────────────────────────────────────────

def test_copia_y_restauracion(bd, repo):
	for i in range(5):
		repo.crear(f"nota {i}")
	copia = bd.copia_seguridad("prueba")
	assert copia.parent == bd.carpeta_copias and copia.is_file()
	assert BaseDatos.integridad_de_archivo(copia) == []
	bd.ejecutar("DELETE FROM notas")
	assert repo.contar() == 0
	bd.restaurar(copia)
	assert repo.contar() == 5
	assert bd.valor("PRAGMA journal_mode") == "wal"
	assert any("antes-de-restaurar" in c.name for c in bd.copias())


def test_rotacion_de_copias(datos):
	base = BaseDatos(migraciones=MIGRACIONES, copias_conservadas=3)
	try:
		creadas = [base.copia_seguridad(f"n{i}") for i in range(5)]
		assert base.copias() == creadas[-3:]
		assert not list(base.carpeta_copias.glob("*.tmp"))
	finally:
		base.cerrar()


def test_integridad_sana(bd):
	assert bd.comprobar_integridad() == []
	assert bd.comprobar_integridad(completa=True) == []


def test_base_danada_se_aparta_y_se_restaura_la_ultima_copia(datos):
	base = BaseDatos(migraciones=MIGRACIONES)
	RepositorioNotas(base).crear("a salvo")
	base.copia_seguridad("diaria")
	ruta = base.ruta
	base.cerrar()
	ruta.write_bytes(b"esto no es una base de datos SQLite" * 200)

	recuperada = BaseDatos(migraciones=MIGRACIONES)
	try:
		assert recuperada.recuperacion is not None
		assert recuperada.recuperacion.restaurada_desde is not None
		assert "restaurado" in recuperada.recuperacion.mensaje()
		assert [n.titulo for n in RepositorioNotas(recuperada).pagina(0, 10)] == ["a salvo"]
		apartados = list(ruta.parent.glob("app.db.danada-*"))
		assert apartados, "el archivo dañado se conserva"
	finally:
		recuperada.cerrar()


def test_base_danada_sin_copias_empieza_vacia(datos):
	ruta = datos / "db" / "app.db"
	ruta.parent.mkdir(parents=True)
	ruta.write_bytes(b"\x00basura" * 1000)
	base = BaseDatos(migraciones=MIGRACIONES)
	try:
		assert base.recuperacion is not None and base.recuperacion.restaurada_desde is None
		assert base.version() == 2
	finally:
		base.cerrar()


def test_bloqueo_no_es_corrupcion(datos):
	BaseDatos(migraciones=MIGRACIONES).cerrar()
	ruta = datos / "db" / "app.db"
	otro = sqlite3.connect(ruta, isolation_level=None)
	otro.execute("PRAGMA journal_mode = DELETE")
	otro.execute("BEGIN EXCLUSIVE")
	try:
		with pytest.raises(ErrorBaseDatosBloqueada):
			BaseDatos(migraciones=MIGRACIONES, espera_bloqueo_ms=50, reintentos_bloqueo=2)
		assert not list(ruta.parent.glob("*.danada-*")), "un bloqueo nunca aparta la base"
	finally:
		otro.execute("ROLLBACK")
		otro.close()


# ── Hilos ────────────────────────────────────────────────────────────────────

def test_acceso_desde_varios_hilos(bd, repo):
	errores = []
	lecturas = []

	def escritor(n):
		try:
			for i in range(50):
				repo.crear(f"hilo {n} nota {i}")
		except Exception as error:  # noqa: BLE001 - se comprueba abajo
			errores.append(error)

	def lector():
		try:
			for _ in range(50):
				lecturas.append(repo.contar())
				repo.pagina(0, 20)
			bd.cerrar_lectura_del_hilo()
		except Exception as error:  # noqa: BLE001 - se comprueba abajo
			errores.append(error)

	hilos = [threading.Thread(target=escritor, args=(n,)) for n in range(6)]
	hilos += [threading.Thread(target=lector) for _ in range(4)]
	for hilo in hilos:
		hilo.start()
	for hilo in hilos:
		hilo.join()
	assert errores == []
	assert repo.contar() == 300
	assert len(lecturas) == 200 and all(0 <= n <= 300 for n in lecturas)


def test_cerrar_es_idempotente_y_rechaza_uso_posterior(datos, caplog):
	base = BaseDatos(migraciones=MIGRACIONES)
	RepositorioNotas(base).crear("x")
	wal = base.ruta.with_name("app.db-wal")
	base.cerrar()
	base.cerrar()
	assert not wal.exists() or wal.stat().st_size == 0, "checkpoint(TRUNCATE) al cerrar"
	with pytest.raises(ErrorBaseDatos):
		base.consultar("SELECT 1")
	assert any("después de cerrarla" in r.getMessage() for r in caplog.records)


# ── Paginación y Unicode ─────────────────────────────────────────────────────

def test_paginacion_offset_y_keyset_coinciden(repo):
	repo.crear_varias((f"nota {i:04d}", "") for i in range(1000))
	assert repo.contar() == 1000
	por_offset = [n.id for d in range(0, 1000, 150) for n in repo.pagina(d, 150)]
	por_keyset, cursor = [], None
	while True:
		pagina = repo.pagina_despues(cursor, 150)
		if not pagina:
			break
		por_keyset.extend(n.id for n in pagina)
		cursor = pagina[-1].cursor
	assert por_offset == por_keyset
	assert len(set(por_keyset)) == 1000


def test_keyset_usa_el_indice(repo):
	plan = " ".join(repo.plan_de(
		"SELECT id FROM notas WHERE (creada_en, id) < (?, ?) ORDER BY creada_en DESC, id DESC LIMIT 10",
		("2030", 1),
	))
	assert "idx_notas_creada" in plan
	assert "TEMP B-TREE" not in plan, "sin ordenación en memoria"


def test_cache_de_bloques_para_lista_virtual(repo):
	repo.crear_varias((f"n{i}", "") for i in range(500))
	cache = CacheBloques(repo.pagina, repo.contar, tamano_bloque=100, max_bloques=2)
	assert cache.total == 500
	primera = cache.fila(0)
	assert cache.fila(99).id == repo.pagina(99, 1)[0].id
	assert cache.cargas == 1
	cache.fila(250)
	cache.fila(450)
	assert cache.cargas == 3
	assert cache.fila(0) == primera and cache.cargas == 4  # el bloque 0 salió de la caché
	assert cache.fila(500) is None
	repo.crear("nueva")
	cache.invalidar()
	assert cache.total == 501


def test_datos_unicode_y_busqueda_sin_acentos(repo):
	textos = ["Camión azul", "ÑANDÚ", "Café ☕ con emoji 🎉", "日本語", "Ärger"]
	ids = [repo.crear(t, texto=t * 3) for t in textos]
	for id_nota, texto in zip(ids, textos, strict=True):
		nota = repo.obtener(id_nota)
		assert nota.titulo == texto and nota.texto == texto * 3
	assert [n.titulo for n in repo.pagina(0, 10, filtro="camion")] == ["Camión azul"]
	assert [n.titulo for n in repo.pagina(0, 10, filtro="ñandu")] == ["ÑANDÚ"]
	assert repo.contar("🎉") == 1
	assert repo.contar("100%") == 0, "los comodines de LIKE se escapan"
	assert normalizar_busqueda("Ärger") == "arger"


def test_actualizar_y_borrar(repo):
	id_nota = repo.crear("original")
	assert repo.actualizar(id_nota, titulo="Título nuevo", favorita=True)
	nota = repo.obtener(id_nota)
	assert nota.titulo == "Título nuevo" and nota.favorita is True
	assert repo.contar("titulo nuevo") == 1
	assert repo.actualizar(9999, titulo="x") is False
	otro = repo.crear("otro")
	assert repo.borrar_varias([id_nota, otro]) == 2
	assert repo.borrar(id_nota) is False


def test_compactar_si_procede(datos):
	base = BaseDatos(migraciones=MIGRACIONES)
	try:
		repo = RepositorioNotas(base)
		repo.crear_varias(("x" * 50, "y" * 2000) for _ in range(3000))
		assert base.compactar_si_procede(minimo_mb=0) is False, "sin espacio libre no compacta"
		base.ejecutar("DELETE FROM notas")
		recuperable, _total = base.espacio_libre()
		assert recuperable > 0
		antes = base.ruta.stat().st_size
		assert base.compactar_si_procede(minimo_mb=0, proporcion=0.1) is True
		assert base.ruta.stat().st_size < antes
		assert base.comprobar_integridad() == []
	finally:
		base.cerrar()
