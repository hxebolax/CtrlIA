"""Pruebas de las correcciones de la versión 1.5.2.

- Las reglas de permisos que nombran las skills como en la 1.5.0 (``data\\agentes_ejemplo``,
  ``ctrlia-agentes:``) o con la carpeta del complemento de otra sesión siguen valiendo:
  Claude Code recibe también su versión con los nombres de ahora.
- El error de la API «Claude Code X does not support this model» se explica en español.
- Una fecha vacía de Antigravity («0001-01-01») ya no deja sin la lista de sus conversaciones.

Nada se muestra ni habla.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import skills_sesion
from app.services.agentes.base import AjustesSesion, TipoEvento
from app.services.agentes.claude import ConversacionClaude, ProveedorClaude, _Turno
from app.services.config_agentes import reglas_claude
from app.services.historial import comun

UUID = "50ea5ef5-ac4c-4c46-a628-a108693840ca"
DATOS = Path(r"F:\MisApps\CtrlIA\data")
COMPLEMENTO = DATOS / "cache" / "skills_sesion" / "0123456789abcdef"


# -- Traducir las reglas ----------------------------------------------------------------------


@pytest.mark.parametrize(("vieja", "nueva"), [
	("Skill(ctrlia-agentes:pruebas)", "Skill(ctrlia-skills:pruebas)"),
	(r"Bash(python F:\MisApps\CtrlIA\data\agentes_ejemplo\auditoria-proyecto\scripts\auditar.py:*)",
		r"Bash(python F:\MisApps\CtrlIA\data\skills_ejemplo\auditoria-proyecto\scripts\auditar.py:*)"),
	("Read(//f/MisApps/CtrlIA/data/agentes_ejemplo/pruebas/**)", "Read(//f/MisApps/CtrlIA/data/skills_ejemplo/pruebas/**)"),
	("Bash(python /f/MisApps/CtrlIA/data/agentes/mia/x.py)", "Bash(python /f/MisApps/CtrlIA/data/skills/mia/x.py)"),
	(r"Bash(python f:\misapps\ctrlia\DATA\Agentes\mia\x.py)", r"Bash(python f:\misapps\ctrlia\DATA\skills\mia\x.py)"),
	# El complemento de otra sesión (de antes o de ahora) pasa a ser el de esta.
	(r"Bash(python F:\MisApps\CtrlIA\data\cache\agentes_sesion\fedcba9876543210\skills\pruebas\x.py:*)",
		r"Bash(python F:\MisApps\CtrlIA\data\cache\skills_sesion\0123456789abcdef\skills\pruebas\x.py:*)"),
	("Read(//f/MisApps/CtrlIA/data/cache/skills_sesion/fedcba9876543210/skills/pruebas/**)",
		"Read(//f/MisApps/CtrlIA/data/cache/skills_sesion/0123456789abcdef/skills/pruebas/**)"),
])
def test_traduce_los_nombres_de_antes(vieja, nueva):
	assert skills_sesion.traducir_regla(vieja, COMPLEMENTO, [DATOS]) == nueva


@pytest.mark.parametrize("regla", [
	"Bash(npm test)",
	# Carpetas ajenas que se llaman igual: no son de Ctrl+IA.
	r"Bash(python E:\Proyecto\data\agentes\x.py)",
	"Read(//e/Proyecto/data/agentes_ejemplo/**)",
	r"Bash(python F:\MisApps\CtrlIA\data\agentesX\x.py)",
	"Skill(mi-ctrlia-agentes:x)",
	# Ya con los nombres de ahora.
	r"Read(//f/MisApps/CtrlIA/data/cache/skills_sesion/0123456789abcdef/**)",
])
def test_no_toca_lo_que_no_es_de_antes(regla):
	assert skills_sesion.traducir_regla(regla, COMPLEMENTO, [DATOS]) == regla


def test_sin_complemento_no_se_cambia_su_carpeta():
	regla = r"Bash(python F:\MisApps\CtrlIA\data\cache\agentes_sesion\fedcba9876543210\skills\x.py)"
	assert skills_sesion.traducir_regla(regla, None, [DATOS]) == regla


def test_reglas_traducidas_solo_las_nuevas_y_sin_repetir():
	reglas = ["Bash(ls)", "Skill(ctrlia-agentes:a)", "Skill(ctrlia-agentes:a)", "Skill(ctrlia-skills:b)", "Skill(ctrlia-agentes:b)"]
	assert skills_sesion.reglas_traducidas(reglas) == ["Skill(ctrlia-skills:a)"]


# -- En la orden de Claude Code ---------------------------------------------------------------


@pytest.fixture
def claude_aislado(tmp_path, monkeypatch):
	"""La configuración de Claude Code (usuario y equipo) en una carpeta temporal; devuelve la carpeta del proyecto."""
	raiz = tmp_path / "claude"
	raiz.mkdir()
	monkeypatch.setattr(reglas_claude, "_raiz_claude", lambda: raiz)
	monkeypatch.setattr(reglas_claude, "archivo_config_claude", lambda: tmp_path / "claude.json")
	monkeypatch.setattr(reglas_claude, "archivos_gestionados", lambda: [])
	datos = tmp_path / "data"
	monkeypatch.setenv("APP_DATA_DIR", str(datos))
	from app.core import migraciones

	monkeypatch.setattr(migraciones, "carpetas_datos_propias", lambda: [datos])
	proyecto = tmp_path / "proyecto"
	(proyecto / ".claude").mkdir(parents=True)
	return proyecto, datos


def _ruta(datos: Path, *partes: str) -> str:
	return str(datos.joinpath(*partes))


def test_claude_recibe_las_reglas_de_antes_con_los_nombres_de_ahora(claude_aislado):
	proyecto, datos = claude_aislado
	permitida = "Bash(python %s:*)" % _ruta(datos, "agentes_ejemplo", "pruebas", "x.py")
	prohibida = "Bash(rm %s)" % _ruta(datos, "agentes", "mia")
	(proyecto / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {
		"allow": [permitida, "Skill(ctrlia-agentes:pruebas)", "Bash(npm test)"], "deny": [prohibida]}}), encoding="utf-8")
	ajustes = AjustesSesion(carpeta=proyecto, id_externo=UUID, reglas_permitidas=["Skill(ctrlia-agentes:otra)"])
	orden = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None).construir_orden()
	permitidas = [orden[i + 1] for i, x in enumerate(orden) if x == "--allowedTools"]
	todas = [r for i, x in enumerate(orden) if x == "--allowedTools" for r in orden[i + 1:]]
	assert "Bash(python %s:*)" % _ruta(datos, "skills_ejemplo", "pruebas", "x.py") in todas
	assert "Skill(ctrlia-skills:pruebas)" in todas and "Skill(ctrlia-skills:otra)" in todas
	# Lo que ya vale tal cual lo aplica Claude Code de su archivo: no se repite en la orden.
	assert "Bash(npm test)" not in orden and permitidas
	assert orden[orden.index("--disallowedTools") + 1] == "Bash(rm %s)" % _ruta(datos, "skills", "mia")


def test_sin_reglas_de_antes_la_orden_no_cambia(claude_aislado):
	proyecto, _datos = claude_aislado
	(proyecto / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"allow": ["Bash(npm test)"]}}),
		encoding="utf-8")
	orden = ProveedorClaude(orden_base=["claude"]).crear_conversacion(
		AjustesSesion(carpeta=proyecto, id_externo=UUID), lambda e: None).construir_orden()
	assert "--allowedTools" not in orden and "--disallowedTools" not in orden


def test_skill_de_otra_sesion_y_lectura_del_complemento(claude_aislado):
	proyecto, datos = claude_aislado
	carpeta = datos / "skills" / "pruebas"
	carpeta.mkdir(parents=True)
	(carpeta / "SKILL.md").write_text("---\nname: pruebas\ndescription: x\n---\n", encoding="utf-8")
	otra = "Bash(python %s:*)" % _ruta(datos, "cache", "skills_sesion", "fedcba9876543210", "skills", "pruebas", "x.py")
	(proyecto / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"allow": [otra]}}), encoding="utf-8")
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(
		AjustesSesion(carpeta=proyecto, id_externo=UUID), lambda e: None)
	conversacion.skills = [{"nombre": "Pruebas", "carpeta": str(carpeta)}]
	orden = conversacion.construir_orden()
	complemento = Path(orden[orden.index("--plugin-dir") + 1])
	assert "Bash(python %s:*)" % (complemento / "skills" / "pruebas" / "x.py") in orden
	assert skills_sesion.regla_lectura(str(complemento)) in orden
	skills_sesion.limpiar_complemento("claude:" + UUID)


# -- Error de versión de Claude Code ---------------------------------------------------------


def test_error_de_version_antigua_en_espanol(tmp_path):
	eventos = []
	conversacion = ConversacionClaude(ProveedorClaude(orden_base=["claude"]), AjustesSesion(carpeta=tmp_path),
		lambda evento: eventos.append(evento))
	turno = _Turno()
	conversacion._turno = turno
	conversacion._al_resultado(turno, {"type": "result", "subtype": "success", "is_error": True, "result": (
		"API Error: 400 Claude Code 2.1.238 does not support this model; version 2.1.280 or newer is required. "
		"Run 'claude update', or update the Claude desktop app, then try again.")})
	error = next(e for e in eventos if e.tipo is TipoEvento.ERROR)
	assert "versión 2.1.238" in error.texto and "2.1.280 o posterior" in error.texto and "claude update" in error.texto
	assert "does not support" not in error.texto


# -- Skills de ejemplo que no llegaron al actualizar --------------------------------------------


def _ventana_falsa(almacen, opciones):
	from types import SimpleNamespace

	return SimpleNamespace(almacen=almacen, opcion=lambda c, d=None: opciones.get(c, d),
		poner_opcion=lambda c, v: opciones.__setitem__(c, v))


def test_sin_carpeta_de_ejemplos_no_se_marcan_como_cargados(tmp_path, monkeypatch):
	from app.core import paths
	from app.services.almacen import Almacen
	from app.ui.ventana_principal import VentanaPrincipal

	reales = paths.ruta_skills_ejemplo()
	monkeypatch.setattr(paths, "ruta_skills_ejemplo", lambda: tmp_path / "no_hay")
	almacen = Almacen(tmp_path / "prueba.db")
	opciones: dict = {}
	VentanaPrincipal._preparar_skills_ejemplo(_ventana_falsa(almacen, opciones))
	assert opciones == {} and almacen.skills_catalogo() == []
	# Una actualización trae la carpeta: en el siguiente arranque se cargan.
	monkeypatch.setattr(paths, "ruta_skills_ejemplo", lambda: reales)
	VentanaPrincipal._preparar_skills_ejemplo(_ventana_falsa(almacen, opciones))
	assert len(almacen.skills_catalogo()) >= 10 and opciones["skills.ejemplos_cargados"] is True
	almacen.cerrar()


def _skill(carpeta: Path) -> Path:
	carpeta.mkdir(parents=True, exist_ok=True)
	(carpeta / "SKILL.md").write_text("---\nname: %s\ndescription: x\n---\n" % carpeta.name, encoding="utf-8")
	return carpeta


def test_ejemplos_desde_la_copia_del_programa(tmp_path):
	"""Al actualizar con el actualizador de la 1.5.0 o anterior, data/skills_ejemplo no llega: se pone desde la copia."""
	from app.services import proteccion_skills

	datos, programa, copia = tmp_path / "datos", tmp_path / "programa" / "data", tmp_path / "programa" / "copia_skills_ejemplo"
	datos.mkdir()
	programa.mkdir(parents=True)
	_skill(copia / "traducciones")
	# La vieja de la 1.4.4 no se renombra: la copia es la de esta versión, y la vieja (del programa) se borra.
	_skill(programa / "agentes_ejemplo" / "vieja")
	(programa / "skills_ejemplo.parcial").mkdir()  # de un arranque que no terminó
	resultado = proteccion_skills.proteger_carpetas(datos, programa, copia)
	assert sorted(p.name for p in (programa / "skills_ejemplo").iterdir()) == ["traducciones"]
	assert not (programa / "agentes_ejemplo").exists() and not (programa / "skills_ejemplo.parcial").exists()
	assert (copia / "traducciones" / "SKILL.md").is_file() and resultado.avisos == []
	# Si ya está (la trajo el actualizador), no se toca.
	_skill(programa / "skills_ejemplo" / "nueva")
	proteccion_skills.proteger_carpetas(datos, programa, copia)
	assert sorted(p.name for p in (programa / "skills_ejemplo").iterdir()) == ["nueva", "traducciones"]


def test_sin_copia_se_renombra_la_vieja_como_antes(tmp_path):
	from app.services import proteccion_skills

	datos, programa = tmp_path / "datos", tmp_path / "data"
	datos.mkdir()
	_skill(programa / "agentes_ejemplo" / "vieja")
	proteccion_skills.proteger_carpetas(datos, programa, tmp_path / "no_hay")
	assert (programa / "skills_ejemplo" / "vieja" / "SKILL.md").is_file()


def test_la_compilacion_lleva_la_copia_de_los_ejemplos():
	import importlib.util

	pytest.importorskip("cx_Freeze")
	especificacion = importlib.util.spec_from_file_location("setup_cx_prueba", Path(__file__).parents[1] / "setup_cx.py")
	modulo = importlib.util.module_from_spec(especificacion)
	especificacion.loader.exec_module(modulo)
	from app.core.paths import ruta_copia_skills_ejemplo

	copia = modulo.archivos_copia_ejemplos()
	en_data = [d for _o, d in modulo.archivos_de_data() if d.startswith("data/skills_ejemplo/")]
	assert len(copia) == len(en_data) > 100
	assert all(d.startswith(ruta_copia_skills_ejemplo().name + "/") for _o, d in copia)
	assert any(d.endswith("traducciones/SKILL.md") for _o, d in copia)


def test_marcados_sin_tenerlos_se_cargan_una_vez(tmp_path):
	"""Hasta la 1.5.1 se marcaban como cargados aunque la carpeta no hubiera llegado con la actualización."""
	from app.services.almacen import Almacen
	from app.ui.ventana_principal import VentanaPrincipal

	almacen = Almacen(tmp_path / "prueba.db")
	opciones = {"skills.ejemplos_cargados": True}
	VentanaPrincipal._preparar_skills_ejemplo(_ventana_falsa(almacen, opciones))
	assert len(almacen.skills_catalogo()) >= 10 and opciones["skills.ejemplos_revisados"] is True
	# Desde entonces, si la persona los quita, no vuelven solos.
	for skill in almacen.skills_catalogo():
		almacen.borrar_skill_catalogo(skill.id)
	VentanaPrincipal._preparar_skills_ejemplo(_ventana_falsa(almacen, opciones))
	assert almacen.skills_catalogo() == []
	almacen.cerrar()


# -- Fechas de Antigravity ------------------------------------------------------------------


def test_fecha_vacia_de_go():
	assert comun.fecha("0001-01-01 00:00:00+00:00") == 0.0
	assert comun.fecha("1969-12-31T23:59:59Z") == 0.0
	assert comun.fecha("2026-10-21 21:09:37.4655682+00:00") > 0
