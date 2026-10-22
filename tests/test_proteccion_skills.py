"""Paso de los «agentes» del catálogo (1.5.0 y anteriores) a skills.

Carpetas al arrancar (``proteccion_skills``), migración 5 de la base de datos,
migración 2 → 3 de la configuración, catálogos JSON antiguos y la protección de
índices de las listas de casillas de las skills.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from app.core.base_datos import BaseDatos
from app.core.migraciones import MIGRACIONES, ruta_skill_renombrada
from app.services import proteccion_skills
from app.services.almacen import Almacen
from app.services.catalogo_skills import CatalogoSkills, ErrorCatalogo


def _skill(carpeta: Path, nombre: str | None = None, extra: str = "") -> Path:
	carpeta.mkdir(parents=True, exist_ok=True)
	(carpeta / "SKILL.md").write_text(f"---\nname: {nombre or carpeta.name}\ndescription: Hace {extra or carpeta.name}.\n---\n",
		encoding="utf-8")
	return carpeta


def _union(destino: Path, enlace: Path) -> None:
	"""Crea una junction de Windows (o se salta la prueba si no se puede)."""
	_winapi = pytest.importorskip("_winapi")
	try:
		_winapi.CreateJunction(str(destino), str(enlace))
	except OSError:
		pytest.skip("No se pueden crear uniones aquí")


@pytest.fixture
def carpetas(tmp_path):
	"""``datos`` (la carpeta data de la persona) y ``programa`` (la data del programa), por separado."""
	datos, programa = tmp_path / "datos", tmp_path / "programa"
	datos.mkdir()
	programa.mkdir()
	return datos, programa


# ════════════════════════════════════════════════════════════════════════════
# Carpetas al arrancar
# ════════════════════════════════════════════════════════════════════════════

def test_sin_nada_que_mover_no_hace_nada(carpetas):
	datos, programa = carpetas
	resultado = proteccion_skills.proteger_carpetas(datos, programa)
	assert resultado.acciones == [] and resultado.avisos == []
	assert sorted(os.listdir(datos)) == [] and sorted(os.listdir(programa)) == []


def test_ejemplos_solo_la_vieja_se_renombra(carpetas, caplog):
	datos, programa = carpetas
	_skill(programa / "agentes_ejemplo" / "traducciones")
	with caplog.at_level(logging.INFO, logger="app.services.proteccion_skills"):
		resultado = proteccion_skills.proteger_carpetas(datos, programa)
	assert not (programa / "agentes_ejemplo").exists()
	assert (programa / "skills_ejemplo" / "traducciones" / "SKILL.md").is_file()
	assert len(resultado.acciones) == 1 and "renombrada" in caplog.text


def test_ejemplos_las_dos_se_borra_la_vieja(carpetas):
	datos, programa = carpetas
	_skill(programa / "agentes_ejemplo" / "vieja")
	_skill(programa / "skills_ejemplo" / "nueva")
	proteccion_skills.proteger_carpetas(datos, programa)
	assert not (programa / "agentes_ejemplo").exists()
	assert sorted(os.listdir(programa / "skills_ejemplo")) == ["nueva"]


def test_ejemplos_al_borrar_la_vieja_no_se_entra_en_uniones(carpetas, tmp_path):
	datos, programa = carpetas
	original = _skill(tmp_path / "de-la-persona" / "importante")
	(original / "plantilla.txt").write_text("no se borra", encoding="utf-8")
	(programa / "agentes_ejemplo").mkdir()
	_union(original, programa / "agentes_ejemplo" / "enlace")
	_skill(programa / "skills_ejemplo" / "nueva")
	proteccion_skills.proteger_carpetas(datos, programa)
	assert not (programa / "agentes_ejemplo").exists()
	assert sorted(p.name for p in original.iterdir()) == ["SKILL.md", "plantilla.txt"]


def test_propias_solo_la_vieja_se_renombra(carpetas):
	datos, programa = carpetas
	_skill(datos / "agentes" / "mia")
	proteccion_skills.proteger_carpetas(datos, programa)
	assert not (datos / "agentes").exists()
	assert (datos / "skills" / "mia" / "SKILL.md").is_file()


def test_propias_las_dos_mueve_las_que_no_chocan_y_nunca_borra(carpetas, caplog):
	datos, programa = carpetas
	_skill(datos / "agentes" / "solo-en-la-vieja")
	_skill(datos / "agentes" / "repetida", extra="lo viejo")
	_skill(datos / "skills" / "repetida", extra="lo nuevo")
	with caplog.at_level(logging.INFO, logger="app.services.proteccion_skills"):
		resultado = proteccion_skills.proteger_carpetas(datos, programa)
	assert (datos / "skills" / "solo-en-la-vieja" / "SKILL.md").is_file()
	# La que choca se queda en su sitio, intacta, y se avisa en el registro.
	assert "lo viejo" in (datos / "agentes" / "repetida" / "SKILL.md").read_text(encoding="utf-8")
	assert "lo nuevo" in (datos / "skills" / "repetida" / "SKILL.md").read_text(encoding="utf-8")
	assert len(resultado.avisos) == 1 and "repetida" in resultado.avisos[0]
	assert "repetida" in caplog.text
	# Al volver a arrancar no se pierde nada ni se mueve dos veces.
	proteccion_skills.proteger_carpetas(datos, programa)
	assert (datos / "agentes" / "repetida" / "SKILL.md").is_file()


def test_propias_las_dos_sin_choques_quita_la_vieja_vacia(carpetas):
	datos, programa = carpetas
	_skill(datos / "agentes" / "a")
	_skill(datos / "skills" / "b")
	resultado = proteccion_skills.proteger_carpetas(datos, programa)
	assert not (datos / "agentes").exists() and resultado.avisos == []
	assert sorted(os.listdir(datos / "skills")) == ["a", "b"]


def test_cache_antigua_se_quita_sin_tocar_el_destino_de_las_uniones(carpetas, tmp_path):
	datos, programa = carpetas
	original = _skill(tmp_path / "de-la-persona" / "pruebas")
	(original / "plantilla.txt").write_text("importante", encoding="utf-8")
	habilidades = datos / "cache" / "agentes_sesion" / "0123456789abcdef" / "skills"
	habilidades.mkdir(parents=True)
	(habilidades.parent / ".claude-plugin").mkdir()
	(habilidades.parent / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
	_union(original, habilidades / "pruebas")
	resultado = proteccion_skills.proteger_carpetas(datos, programa)
	assert not (datos / "cache" / "agentes_sesion").exists()
	assert sorted(p.name for p in original.iterdir()) == ["SKILL.md", "plantilla.txt"]
	assert len(resultado.acciones) == 1


def test_un_paso_que_falla_no_impide_los_demas(carpetas, monkeypatch):
	datos, programa = carpetas
	_skill(programa / "agentes_ejemplo" / "x")
	_skill(datos / "agentes" / "mia")

	def falla(*_argumentos):
		raise OSError("bloqueada")

	monkeypatch.setattr(proteccion_skills, "_ejemplos", falla)
	resultado = proteccion_skills.proteger_carpetas(datos, programa)
	assert (datos / "skills" / "mia").is_dir()
	assert any("_ejemplos" in aviso or "falla" in aviso for aviso in resultado.avisos)


def test_por_defecto_usa_las_carpetas_de_la_aplicacion(data_temporal):
	_skill(data_temporal / "agentes" / "mia")
	proteccion_skills.proteger_carpetas()
	assert (data_temporal / "skills" / "mia" / "SKILL.md").is_file()


# ════════════════════════════════════════════════════════════════════════════
# Rutas del catálogo y migración 5
# ════════════════════════════════════════════════════════════════════════════

def test_ruta_skill_renombrada(tmp_path):
	datos = tmp_path / "data"
	_skill(datos / "skills" / "movida")
	_skill(datos / "agentes" / "repetida")
	_skill(datos / "skills" / "repetida")
	_skill(datos / "skills_ejemplo" / "traducciones")
	vieja = str(datos / "agentes" / "movida")
	assert ruta_skill_renombrada(vieja) == str(datos / "skills" / "movida")
	assert ruta_skill_renombrada(str(datos / "agentes_ejemplo" / "traducciones")) == str(datos / "skills_ejemplo" / "traducciones")
	# La vieja sigue existiendo (chocaba): no se cambia.
	assert ruta_skill_renombrada(str(datos / "agentes" / "repetida")) is None
	# Ninguna existe: solo si la carpeta data es de Ctrl+IA.
	perdida = str(datos / "agentes" / "perdida")
	assert ruta_skill_renombrada(perdida) is None
	assert ruta_skill_renombrada(perdida, [datos]) == str(datos / "skills" / "perdida")
	# Lo que no está en data\agentes no se toca.
	assert ruta_skill_renombrada(str(tmp_path / "proyectos" / "agentes" / "x"), [datos]) is None
	assert ruta_skill_renombrada("") is None


def _base_version_4(ruta: Path, copias: Path) -> BaseDatos:
	"""Una base como la de la 1.5.0: solo las cuatro primeras migraciones."""
	return BaseDatos(ruta, MIGRACIONES[:4], carpeta_copias=copias)


def test_migracion_5_renombra_tabla_rutas_y_claves_de_las_sesiones(data_temporal, tmp_path):
	_skill(data_temporal / "skills" / "movida")
	_skill(data_temporal / "agentes" / "repetida")
	_skill(data_temporal / "skills" / "repetida")
	_skill(data_temporal / "skills_ejemplo" / "traducciones")
	ajena = tmp_path / "proyectos" / "data" / "agentes" / "ajena"
	ruta = tmp_path / "ctrlia.db"
	bd = _base_version_4(ruta, tmp_path / "copias")
	try:
		filas = {
			"movida": str(data_temporal / "agentes" / "movida"),
			"repetida": str(data_temporal / "agentes" / "repetida"),
			"ejemplo": str(data_temporal / "agentes_ejemplo" / "traducciones"),
			"ajena": str(ajena),
			"otra": str(tmp_path / "otra" / "skill"),
		}
		for nombre, carpeta in filas.items():
			bd.ejecutar("INSERT INTO agentes (nombre, carpeta, creado, modificado) VALUES (?, ?, 0, 0)", (nombre, carpeta))
		extra = {"agentes": [1, 2], "agentes_presentados": "abc", "estilo": "conciso"}
		bd.ejecutar("INSERT INTO sesiones (clave, proveedor, creada, actividad, extra) VALUES ('ctrlia:1', 'claude', 0, 0, ?)",
			(json.dumps(extra),))
		bd.ejecutar("INSERT INTO sesiones (clave, proveedor, creada, actividad, extra) VALUES ('ctrlia:2', 'codex', 0, 0, 'roto')")
	finally:
		bd.cerrar()

	almacen = Almacen(ruta)
	try:
		assert almacen.bd.version() == len(MIGRACIONES) == 5
		tablas = {f["name"] for f in almacen.bd.consultar("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
		assert "skills" in tablas and "idx_skills_categoria" in tablas
		assert "agentes" not in tablas and "idx_agentes_categoria" not in tablas
		carpetas = {s.nombre: s.carpeta for s in almacen.skills_catalogo()}
		assert carpetas["movida"] == str(data_temporal / "skills" / "movida")
		assert carpetas["ejemplo"] == str(data_temporal / "skills_ejemplo" / "traducciones")
		assert carpetas["repetida"] == str(data_temporal / "agentes" / "repetida")  # chocaba: se queda
		assert carpetas["ajena"] == str(ajena) and carpetas["otra"] == filas["otra"]
		sesion = almacen.sesion("ctrlia:1")
		assert sesion.extra == {"skills": [1, 2], "skills_presentadas": "abc", "estilo": "conciso"}
		# Las altas siguen funcionando (AUTOINCREMENT tras renombrar la tabla).
		nueva = almacen.crear_skill_catalogo("nueva", str(tmp_path / "nueva"))
		assert nueva > 5
	finally:
		almacen.cerrar()
	# Antes de migrar se hizo una copia de seguridad.
	assert list((data_temporal / "backups").glob("*antes-de-migrar-v4*"))


def test_reparar_rutas_catalogo_tras_mover_las_carpetas(data_temporal, tmp_path):
	almacen = Almacen(tmp_path / "ctrlia.db")
	try:
		vieja = _skill(data_temporal / "agentes" / "mia")
		id_skill = almacen.crear_skill_catalogo("mia", str(vieja))
		# La base se abrió antes de mover las carpetas: la ruta no ha cambiado.
		assert proteccion_skills.reparar_rutas_catalogo(almacen) == 0
		proteccion_skills.proteger_carpetas(data_temporal, tmp_path / "programa")
		assert proteccion_skills.reparar_rutas_catalogo(almacen) == 1
		assert almacen.skill_catalogo(id_skill).carpeta == str(data_temporal / "skills" / "mia")
		assert proteccion_skills.reparar_rutas_catalogo(almacen) == 0
	finally:
		almacen.cerrar()


def test_reparar_rutas_nunca_duplica_una_carpeta(data_temporal, tmp_path):
	almacen = Almacen(tmp_path / "ctrlia.db")
	try:
		_skill(data_temporal / "skills" / "mia")
		almacen.crear_skill_catalogo("nueva", str(data_temporal / "skills" / "mia"))
		vieja = almacen.crear_skill_catalogo("vieja", str(data_temporal / "agentes" / "mia"))
		assert proteccion_skills.reparar_rutas_catalogo(almacen) == 0
		assert almacen.skill_catalogo(vieja).carpeta == str(data_temporal / "agentes" / "mia")
	finally:
		almacen.cerrar()


# ════════════════════════════════════════════════════════════════════════════
# Configuración y catálogos exportados
# ════════════════════════════════════════════════════════════════════════════

def test_configuracion_2_a_3_copia_ejemplos_cargados(tmp_path):
	from app.core import configuracion as mod

	ruta = tmp_path / "settings.json"
	ruta.write_text(json.dumps({mod.CLAVE_VERSION: 2, "agentes": {"ejemplos_cargados": True, "editor_externo": "x.exe"}}),
		encoding="utf-8")
	config = mod.Configuracion(ruta)
	assert config.obtener("skills.ejemplos_cargados") is True
	assert config.obtener("agentes.editor_externo") == "x.exe"
	guardado = json.loads(ruta.read_text(encoding="utf-8"))
	assert guardado[mod.CLAVE_VERSION] == mod.VERSION_ESQUEMA == 3
	assert "ejemplos_cargados" not in guardado["agentes"]
	# De fábrica: sin cargar.
	assert mod.VALORES_POR_DEFECTO["skills"]["ejemplos_cargados"] is False


def test_importa_catalogos_exportados_con_la_1_5_0(tmp_path):
	almacen = Almacen(tmp_path / "ctrlia.db")
	try:
		catalogo = CatalogoSkills(almacen, tmp_path / "creadas")
		antiguo = tmp_path / "agentes-ctrlia.json"
		antiguo.write_text(json.dumps({"formato": proteccion_skills.FORMATO_JSON_ANTIGUO, "version": 1,
			"agentes": [{"nombre": "Revisor", "carpeta": str(tmp_path / "revisor"), "categoria": "Texto"}]}), encoding="utf-8")
		assert catalogo.importar_json(antiguo).nuevos == 1
		exportado = tmp_path / "skills-ctrlia.json"
		assert catalogo.exportar_json(exportado) == 1
		datos = json.loads(exportado.read_text(encoding="utf-8"))
		assert datos["formato"] == "ctrlia-catalogo-skills" and [s["nombre"] for s in datos["skills"]] == ["Revisor"]
		# Un formato antiguo con la lista nueva (o al revés) no es válido.
		mezclado = tmp_path / "mezclado.json"
		mezclado.write_text(json.dumps({"formato": proteccion_skills.FORMATO_JSON_ANTIGUO, "skills": []}), encoding="utf-8")
		with pytest.raises(ErrorCatalogo):
			catalogo.importar_json(mezclado)
	finally:
		almacen.cerrar()


# ════════════════════════════════════════════════════════════════════════════
# Índices fuera de la lista de casillas (wxAssertionError «IsValid(uiIndex)»)
# ════════════════════════════════════════════════════════════════════════════

wx = pytest.importorskip("wx")


@pytest.fixture
def almacen_con_skills(tmp_path):
	almacen = Almacen(tmp_path / "ctrlia.db")
	catalogo = CatalogoSkills(almacen, tmp_path / "creadas")
	ids = [catalogo.importar_carpeta(_skill(tmp_path / "s" / nombre)).id for nombre in ("a", "b", "c")]
	yield almacen, ids
	almacen.cerrar()


def test_panel_seleccionar_con_la_lista_mas_corta_que_visibles(marco, almacen_con_skills):
	from app.ui.dialogos.elegir_skills import PanelSkills

	almacen, ids = almacen_con_skills
	panel = PanelSkills(marco, almacen)
	assert panel.lista.GetCount() == 3
	# A medio recargar: la lista tiene menos elementos que ``visibles``.
	panel.lista.Set(["solo uno"])
	panel.seleccionar(ids)
	assert panel.lista.IsChecked(0)
	panel._sincronizar()
	evento = wx.CommandEvent(wx.wxEVT_CHECKLISTBOX)
	evento.SetInt(2)
	panel._al_marcar(evento)  # índice fuera de la lista: se ignora sin error
	panel.cargar()
	assert panel.lista.GetCount() == 3 and set(panel.seleccionados()) == set(ids)


def test_skills_encontradas_con_indices_fuera_de_la_lista(app_wx, almacen_con_skills, tmp_path):
	from app.services.catalogo_skills import buscar_skills
	from app.ui.dialogos.gestor_skills import DialogoSkillsEncontradas

	almacen, _ids = almacen_con_skills
	_skill(tmp_path / "busca" / "x")
	_skill(tmp_path / "busca" / "y")
	dialogo = DialogoSkillsEncontradas(None, CatalogoSkills(almacen), buscar_skills(tmp_path / "busca"), tmp_path / "busca")
	try:
		assert dialogo.lista.indices_marcados() == [0, 1]
		dialogo.lista.Set(["solo uno"])
		dialogo.poner_categoria("Web", 1)  # fuera de la lista: no hace nada
		dialogo.poner_categoria("Web", 0)
		assert dialogo.categorias == ["Web", ""]
		dialogo.lista.Check(0, True)
		assert dialogo.aceptar() is True
		assert [f.nombre for f, _c in dialogo.elegidos] == ["x"]
	finally:
		dialogo.Destroy()
