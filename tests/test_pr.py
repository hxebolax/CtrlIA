"""Estado de la PR y de las comprobaciones (CI) de una sesión (AIChat #236).

gh nunca se ejecuta de verdad: se simula con el formato real de ``gh pr view
--json`` y de ``gh run view --log-failed`` (gh 2.100, PR de TheIdeaPlace/AIChat
y de cli/cli). Nada de red ni de ventanas a la vista.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from app.services import pr

# ── JSON reales (recortados) ─────────────────────────────────────────────────

#: TheIdeaPlace/AIChat PR 340 (fusionada, todo correcto; CodeQL sin /actions/ y un trabajo omitido).
PR_CORRECTA = {
	"headRefName": "relay-waiting-on-exit", "isDraft": False, "mergeable": "UNKNOWN", "number": 340,
	"reviewDecision": "", "state": "MERGED",
	"statusCheckRollup": [
		{"__typename": "CheckRun", "completedAt": "2026-10-21T13:16:04Z", "conclusion": "SUCCESS",
			"detailsUrl": "https://github.com/TheIdeaPlace/AIChat/actions/runs/39606383244/job/119243535343",
			"name": "Analyze (actions)", "startedAt": "2026-10-21T13:15:23Z", "status": "COMPLETED", "workflowName": "CodeQL"},
		{"__typename": "CheckRun", "completedAt": "2026-10-21T13:18:28Z", "conclusion": "SUCCESS",
			"detailsUrl": "https://github.com/TheIdeaPlace/AIChat/actions/runs/39606392393/job/119243564699",
			"name": "pytest (Windows)", "startedAt": "2026-10-21T13:15:27Z", "status": "COMPLETED",
			"workflowName": "The Chat Place tests"},
		{"__typename": "CheckRun", "completedAt": "2026-10-21T13:26:34Z", "conclusion": "SKIPPED",
			"detailsUrl": "https://github.com/TheIdeaPlace/AIChat/actions/runs/39606392429/job/119249216287",
			"name": "Publish the release", "startedAt": "2026-10-21T13:26:34Z", "status": "COMPLETED",
			"workflowName": "Release The Chat Place"},
		{"__typename": "CheckRun", "completedAt": "2026-10-21T13:15:57Z", "conclusion": "SUCCESS",
			"detailsUrl": "https://github.com/TheIdeaPlace/AIChat/runs/119243823798", "name": "CodeQL",
			"startedAt": "2026-10-21T13:15:55Z", "status": "COMPLETED", "workflowName": ""},
	],
	"title": "Quitting or updating counts a reply still waiting for a Codex chat to be ready (#248)",
	"url": "https://github.com/TheIdeaPlace/AIChat/pull/340",
}

#: cli/cli PR 14035 (borrador con conflictos; falla «lint»).
PR_FALLIDA = {
	"headRefName": "maxbeizer-fix-project-scope-error", "isDraft": True, "mergeable": "CONFLICTING", "number": 14035,
	"reviewDecision": "REVIEW_REQUIRED", "state": "OPEN",
	"statusCheckRollup": [
		{"__typename": "CheckRun", "completedAt": "2026-08-30T16:04:06Z", "conclusion": "FAILURE",
			"detailsUrl": "https://github.com/cli/cli/actions/runs/33321337572/job/99283726990", "name": "lint",
			"startedAt": "2026-08-30T16:02:57Z", "status": "COMPLETED", "workflowName": "Lint"},
		{"__typename": "CheckRun", "completedAt": "2026-08-30T16:05:10Z", "conclusion": "SUCCESS",
			"detailsUrl": "https://github.com/cli/cli/actions/runs/33321337580/job/99283727001",
			"name": "build (windows-latest)", "startedAt": "2026-08-30T16:02:57Z", "status": "COMPLETED",
			"workflowName": "Unit and Integration Tests"},
	],
	"title": "Fix project scope errors during issue creation",
	"url": "https://github.com/cli/cli/pull/14035",
}

#: Registro real de ``gh run view --job 99283726990 --repo cli/cli --log-failed`` (recortado).
REGISTRO_LINT = "\n".join([
	"lint\tUNKNOWN STEP\t﻿2026-08-30T16:02:57.7614610Z Current runner version: '2.336.0'",
	"lint\tUNKNOWN STEP\t2026-08-30T16:02:57.7646047Z ##[group]Runner Image Provisioner",
	"lint\tUNKNOWN STEP\t2026-08-30T16:02:57.7651762Z ##[endgroup]",
	"lint\tUNKNOWN STEP\t2026-08-30T16:03:23.5614363Z --- /home/runner/work/cli/cli/api/client.go (old)",
	"lint\tUNKNOWN STEP\t2026-08-30T16:03:23.5636271Z +++ /home/runner/work/cli/cli/api/client.go (new)",
	"lint\tUNKNOWN STEP\t2026-08-30T16:03:23.5691923Z -\t\t\tif requirement.contains(scope) {",
	"lint\tUNKNOWN STEP\t2026-08-30T16:03:23.5692683Z +\t\tif slices.ContainsFunc(scopes, requirement.contains) {",
	"lint\tUNKNOWN STEP\t2026-08-30T16:04:05.9128999Z ##[error]Process completed with exit code 1.",
	"lint\tUNKNOWN STEP\t2026-08-30T16:04:05.9267937Z Post job cleanup.",
	"lint\tUNKNOWN STEP\t2026-08-30T16:04:06.1127260Z Cleaning up orphan processes",
])


def en_curso() -> dict:
	"""Una PR con una comprobación aún en marcha (gh pone ``completedAt`` a 0001-01-01 y sin conclusión)."""
	datos = dict(PR_FALLIDA, state="OPEN", isDraft=False, mergeable="MERGEABLE", reviewDecision="")
	datos["statusCheckRollup"] = [
		dict(PR_FALLIDA["statusCheckRollup"][1]),
		{"__typename": "CheckRun", "completedAt": "0001-01-01T00:00:00Z", "conclusion": "",
			"detailsUrl": "https://github.com/cli/cli/actions/runs/33321337599/job/99283727999", "name": "lint",
			"startedAt": "2026-08-30T16:10:00Z", "status": "IN_PROGRESS", "workflowName": "Lint"},
	]
	return datos


class GhSimulado:
	"""Sustituye a ``pr.ejecutar_gh``: guarda cada orden y devuelve lo programado."""

	def __init__(self, *respuestas) -> None:
		self.respuestas = list(respuestas)
		self.ordenes: list[tuple[list[str], Path | None, float]] = []

	def __call__(self, orden, carpeta, tiempo):
		self.ordenes.append((list(orden), carpeta, tiempo))
		respuesta = self.respuestas.pop(0)
		if isinstance(respuesta, BaseException):
			raise respuesta
		return respuesta


@pytest.fixture
def repositorio(tmp_path: Path) -> Path:
	carpeta = tmp_path / "proyecto"
	(carpeta / ".git").mkdir(parents=True)
	(carpeta / "src").mkdir()
	return carpeta


# ── Lectura del JSON ─────────────────────────────────────────────────────────


def test_pr_correcta_con_el_json_real():
	estado = pr.desde_json(PR_CORRECTA)
	assert estado.numero == 340 and estado.estado == "MERGED" and estado.nombre_estado == "fusionada"
	assert estado.resultado == pr.RESULTADO_CORRECTAS
	analizar = estado.comprobaciones[0]
	assert (analizar.repositorio, analizar.id_ejecucion, analizar.id_trabajo) == (
		"TheIdeaPlace/AIChat", "39606383244", "119243535343")
	codeql = estado.comprobaciones[-1]
	assert codeql.id_trabajo == "" and codeql.flujo == ""
	assert [c.estado for c in estado.comprobaciones].count(pr.OMITIDA) == 1
	assert estado.resumen() == "PR 340 (fusionada): todas las comprobaciones han pasado."


def test_pr_fallida_con_el_json_real():
	estado = pr.desde_json(PR_FALLIDA)
	assert estado.resultado == pr.RESULTADO_FALLIDAS
	assert estado.nombres(pr.FALLIDA) == "lint"
	assert estado.borrador and estado.nombre_estado == "borrador"
	texto = estado.texto_completo()
	assert "Se puede fusionar: no, tiene conflictos" in texto
	assert "Fallidas:\n- lint (Lint)" in texto
	assert "Correctas:\n- build (windows-latest) (Unit and Integration Tests)" in texto
	assert "Comprobaciones: 2 en total: 1 fallida, 1 correcta." in texto
	assert estado.resumen() == "PR 14035 (borrador): las comprobaciones han fallado: lint."


def test_pr_en_curso_no_usa_la_fecha_vacia_de_gh():
	estado = pr.desde_json(en_curso())
	assert estado.resultado == pr.RESULTADO_EN_CURSO
	assert "1 de 2 terminadas" in estado.resumen()
	assert estado.ultima_terminada == "2026-08-30T16:05:10Z"


def test_status_context_clasico():
	comprobacion = pr.comprobacion_desde_json({"__typename": "StatusContext", "context": "ci/circleci",
		"state": "FAILURE", "targetUrl": "https://circleci.com/gh/x/y/1", "startedAt": "2026-10-01T10:00:00Z"})
	assert (comprobacion.nombre, comprobacion.estado, comprobacion.id_trabajo) == ("ci/circleci", pr.FALLIDA, "")
	pendiente = pr.comprobacion_desde_json({"__typename": "StatusContext", "context": "x", "state": "PENDING"})
	assert pendiente.estado == pr.EN_CURSO


def test_nombres_sin_repetir_y_resumidos():
	datos = dict(PR_FALLIDA)
	base = PR_FALLIDA["statusCheckRollup"][0]
	datos["statusCheckRollup"] = [dict(base, name=n, detailsUrl=f"{base['detailsUrl']}{i}")
		for i, n in enumerate(["a", "a", "b", "c", "d", "e"])]
	assert pr.desde_json(datos).nombres(pr.FALLIDA) == "a, b, c y 2 más"


def test_firma_cambia_con_trabajos_nuevos():
	uno = pr.desde_json(PR_FALLIDA)
	datos = dict(PR_FALLIDA)
	datos["statusCheckRollup"] = [dict(c, detailsUrl=c["detailsUrl"] + "9") for c in PR_FALLIDA["statusCheckRollup"]]
	assert uno.firma != pr.desde_json(datos).firma
	assert uno.firma == pr.desde_json(PR_FALLIDA).firma


# ── gh simulado ──────────────────────────────────────────────────────────────


def test_consultar_pr_pide_los_campos_en_la_carpeta(repositorio):
	import json

	gh = GhSimulado((0, json.dumps(PR_FALLIDA), ""))
	estado = pr.consultar_pr(repositorio / "src", ejecutar=gh)
	assert estado.numero == 14035
	orden, carpeta, tiempo = gh.ordenes[0]
	assert orden[1:] == ["pr", "view", "--json", pr.CAMPOS_PR]
	assert carpeta == repositorio / "src" and tiempo == pr.TIEMPO_PR


def test_sin_repositorio_no_llama_a_gh(tmp_path):
	gh = GhSimulado()
	with pytest.raises(pr.ErrorPR) as error:
		pr.consultar_pr(tmp_path, ejecutar=gh)
	assert error.value.codigo == "sin_repositorio" and not gh.ordenes


def test_sin_gh(repositorio, monkeypatch):
	monkeypatch.setattr(pr, "buscar_gh", lambda: None)
	with pytest.raises(pr.ErrorPR) as error:
		pr.consultar_pr(repositorio)
	assert error.value.codigo == "sin_gh"
	assert "cli.github.com" in str(error.value)


@pytest.mark.parametrize(("codigo", "salida_error", "esperado", "frase"), [
	(1, 'no pull requests found for branch "rama-sin-pr"\n', "sin_pr", "«rama-sin-pr»"),
	(4, "To get started with GitHub CLI, please run:  gh auth login\nAlternatively, populate the GH_TOKEN environment "
		"variable with a GitHub API authentication token.\n", "sin_sesion", "gh auth login"),
	(1, "HTTP 401: Bad credentials (https://api.github.com/graphql)\nTry authenticating with:  gh auth login -h "
		"github.com\n", "sin_sesion", "No has iniciado sesión"),
	(1, 'Post "https://api.github.com/graphql": proxyconnect tcp: dial tcp 127.0.0.1:9: connectex: No se puede '
		"establecer una conexión\n", "sin_red", "conexión a Internet"),
	(1, "error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com\n",
		"sin_red", "conexión a Internet"),
	(1, "could not determine current branch: failed to run git: not on any branch\n", "sin_rama", "commit suelto"),
	(1, "no git remotes found\n", "sin_github", "remoto de GitHub"),
	(1, "failed to get run log: HTTP 410: Server Error (https://api.github.com/repos/cli/cli/actions/runs/1/logs)\n",
		"caducado", "ya no está en GitHub"),
	(1, "algo raro\núltima línea\n", "otro", "última línea"),
])
def test_errores_reales_de_gh_en_espanol(codigo, salida_error, esperado, frase):
	error = pr.explicar_error(codigo, salida_error)
	assert error.codigo == esperado
	assert frase in str(error)


def test_tiempo_agotado(repositorio):
	gh = GhSimulado(subprocess.TimeoutExpired(["gh"], 30))
	with pytest.raises(pr.ErrorPR) as error:
		pr.consultar_pr(repositorio, ejecutar=gh)
	assert error.value.codigo == "tiempo" and "30 segundos" in str(error.value)


def test_respuesta_ilegible(repositorio):
	with pytest.raises(pr.ErrorPR, match="no se pudo leer"):
		pr.consultar_pr(repositorio, ejecutar=GhSimulado((0, "no es json", "")))


# ── Registro del fallo ───────────────────────────────────────────────────────


def test_leer_fallo_con_el_registro_real():
	gh = GhSimulado((0, REGISTRO_LINT, ""))
	texto = pr.leer_fallo(pr.desde_json(PR_FALLIDA), ejecutar=gh)
	orden = gh.ordenes[0][0]
	assert orden[1:] == ["run", "view", "--job", "99283726990", "--repo", "cli/cli", "--log-failed"]
	assert texto.startswith("Comprobación «lint (Lint)»:")
	assert "Error: Process completed with exit code 1." in texto
	assert "+\t\tif slices.ContainsFunc" in texto
	assert "2026-08-30T" not in texto and "UNKNOWN STEP" not in texto and "##[" not in texto
	# Lo de después del error (limpieza del trabajo) sobra.
	assert "Post job cleanup" not in texto


def test_leer_fallo_caducado_por_trabajo():
	gh = GhSimulado((1, "", "failed to get run log: HTTP 410: Server Error (https://api.github.com/x)"))
	texto = pr.leer_fallo(pr.desde_json(PR_FALLIDA), ejecutar=gh)
	assert "ya no está en GitHub" in texto


def test_leer_fallo_sin_sesion_se_explica():
	gh = GhSimulado((4, "", "To get started with GitHub CLI, please run:  gh auth login"))
	with pytest.raises(pr.ErrorPR) as error:
		pr.leer_fallo(pr.desde_json(PR_FALLIDA), ejecutar=gh)
	assert error.value.codigo == "sin_sesion"


def test_leer_fallo_sin_fallos():
	with pytest.raises(pr.ErrorPR) as error:
		pr.leer_fallo(pr.desde_json(PR_CORRECTA), ejecutar=GhSimulado())
	assert error.value.codigo == "sin_fallos"


def test_recortar_sin_error_toma_las_ultimas_lineas_y_corta_las_enormes():
	registro = "\n".join(f"job\tPaso uno\t2026-01-01T00:00:00.0Z línea {i}" for i in range(200))
	registro += "\njob\tPaso uno\t2026-01-01T00:00:00.0Z " + "x" * 5000
	texto = pr.recortar_registro(registro)
	assert texto.startswith("Paso: Paso uno")
	assert "línea 199" in texto and "línea 100" not in texto
	assert max(len(linea) for linea in texto.splitlines()) <= pr.MAX_CARACTERES_LINEA + 6


def test_mensaje_para_arreglar():
	estado = pr.desde_json(PR_FALLIDA)
	mensaje = pr.mensaje_para_arreglar(estado, "Error: falló\n")
	assert "PR 14035" in mensaje and "han fallado: lint" in mensaje
	assert "```\nError: falló\n```" in mensaje
	assert "rama maxbeizer-fix-project-scope-error" in mensaje


# ── Interfaz: avisos, menús, Propiedades ─────────────────────────────────────

wx = pytest.importorskip("wx")


@pytest.fixture
def ventana(app_wx, tmp_path, monkeypatch):
	import sys

	from app.core.configuracion import Configuracion
	from app.services.agentes.claude import ProveedorClaude
	from app.services.almacen import Almacen
	from app.ui import avisos
	from app.ui.ventana_principal import VentanaPrincipal
	from tests.ayudas_wx import procesar_eventos

	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	fake = Path(__file__).parent / "fakes" / "fake_claude.py"
	marco = VentanaPrincipal(config, None, almacen=Almacen(tmp_path / "prueba.db"),
		proveedores={"claude": ProveedorClaude(orden_base=[sys.executable, str(fake)])})
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()


@pytest.fixture
def avisados(monkeypatch):
	from app.ui import avisos

	dichos: list[str] = []
	sonidos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, interrumpir=False: dichos.append(texto))
	monkeypatch.setattr(avisos, "sonar", lambda nombre, forzar=False: sonidos.append(nombre))
	return dichos, sonidos


def _fila(clave: str, carpeta: Path | str, titulo: str = "Mi sesión", actividad: float | None = None, **otros):
	from app.services.gestor import FilaSesion

	return FilaSesion(clave, "claude", titulo, carpeta=str(carpeta),
		actividad=time.time() if actividad is None else actividad, **otros)


def test_avisa_una_vez_cuando_fallan_y_cuando_pasan(ventana, avisados, repositorio):
	dichos, sonidos = avisados
	ventana._todas = [_fila("ctrlia:a", repositorio)]
	carpeta = str(repositorio)
	assert ventana.al_consultar_pr(carpeta, pr.desde_json(en_curso())) == ""
	texto = ventana.al_consultar_pr(carpeta, pr.desde_json(PR_FALLIDA | {"state": "OPEN", "isDraft": False}))
	assert texto == "Las comprobaciones de la PR 14035 de «Mi sesión» han fallado: lint."
	assert dichos == [texto] and sonidos == ["error"]
	# Lo mismo otra vez: nada.
	assert ventana.al_consultar_pr(carpeta, pr.desde_json(PR_FALLIDA | {"state": "OPEN", "isDraft": False})) == ""
	# Un envío nuevo a la rama: trabajos nuevos, todo correcto.
	corregida = dict(PR_FALLIDA, state="OPEN", isDraft=False)
	corregida["statusCheckRollup"] = [dict(c, conclusion="SUCCESS", detailsUrl=c["detailsUrl"] + "1")
		for c in PR_FALLIDA["statusCheckRollup"]]
	assert ventana.al_consultar_pr(carpeta, pr.desde_json(corregida)) == (
		"Las comprobaciones de la PR 14035 de «Mi sesión» han pasado.")
	assert sonidos[-1] == "respuesta"


def test_al_primer_vistazo_solo_avisa_si_termino_despues_de_empezar(ventana, avisados, repositorio):
	dichos, _sonidos = avisados
	ventana._todas = [_fila("ctrlia:a", repositorio)]
	ventana._datos_pr()["inicio"] = time.time()
	assert ventana.al_consultar_pr(str(repositorio), pr.desde_json(PR_FALLIDA)) == ""
	assert dichos == []
	ventana._datos_pr()["registros"].clear()
	ventana._datos_pr()["inicio"] = 0.0
	assert "han fallado" in ventana.al_consultar_pr(str(repositorio), pr.desde_json(PR_FALLIDA))


def test_fusionada_y_cambios_pedidos(ventana, avisados, repositorio):
	ventana._todas = [_fila("ctrlia:a", repositorio)]
	abierta = dict(PR_CORRECTA, state="OPEN")
	ventana.al_consultar_pr(str(repositorio), pr.desde_json(abierta))
	cambios = ventana.al_consultar_pr(str(repositorio), pr.desde_json(abierta | {"reviewDecision": "CHANGES_REQUESTED"}))
	assert cambios == "En la revisión de la PR 340 de «Mi sesión» piden cambios."
	assert ventana.al_consultar_pr(str(repositorio), pr.desde_json(PR_CORRECTA)) == "La PR 340 de «Mi sesión» se ha fusionado."


def test_siguiente_consulta_segun_el_estado(ventana, repositorio):
	from app.ui import vp_pr

	ventana._todas = [_fila("ctrlia:a", repositorio)]
	clave = vp_pr.clave_carpeta(repositorio)
	siguiente = ventana._datos_pr()["siguiente"]
	ventana.al_consultar_pr(str(repositorio), pr.desde_json(en_curso()), ahora=1000.0)
	assert siguiente[clave] == 1000.0 + vp_pr.CADA_EN_CURSO
	ventana.al_consultar_pr(str(repositorio), pr.desde_json(PR_FALLIDA), ahora=1000.0)
	assert siguiente[clave] == 1000.0 + vp_pr.CADA_NORMAL
	ventana.al_consultar_pr(str(repositorio), None, "sin_pr", ahora=1000.0)
	assert siguiente[clave] == 1000.0 + vp_pr.CADA_SIN_PR
	assert ventana.registro_pr(repositorio).codigo_error == "sin_pr"


def test_solo_se_vigilan_las_sesiones_locales_abiertas_o_recientes(ventana, tmp_path, monkeypatch):
	from app.ui import vp_pr

	ahora = time.time()
	ventana._todas = [
		_fila("ctrlia:reciente", tmp_path / "a"),
		_fila("ctrlia:vieja", tmp_path / "b", actividad=ahora - vp_pr.SEGUNDOS_RECIENTE - 10),
		_fila("ctrlia:remota", tmp_path / "c", equipo="mac", origen="propia"),
		_fila("importada:x", tmp_path / "d"),
		_fila("ctrlia:sin_carpeta", ""),
		_fila("ctrlia:otra", tmp_path / "a", titulo="Otra"),
	]
	vigiladas = ventana.carpetas_vigiladas(ahora)
	assert list(vigiladas) == [vp_pr.clave_carpeta(tmp_path / "a")]
	assert [f.clave for f in vigiladas[vp_pr.clave_carpeta(tmp_path / "a")]] == ["ctrlia:reciente", "ctrlia:otra"]
	ventana.sesion_actual = "ctrlia:vieja"
	assert vp_pr.clave_carpeta(tmp_path / "b") in ventana.carpetas_vigiladas(ahora)

	lanzadas: list[str] = []
	monkeypatch.setattr(ventana, "_consultar_para_vigilancia", lambda carpeta: lanzadas.append(carpeta))
	assert ventana.repasar_prs(ahora) == 2
	ventana._datos_pr()["siguiente"] = {clave: ahora + 100 for clave in ventana.carpetas_vigiladas(ahora)}
	assert ventana.repasar_prs(ahora) == 0
	ventana.config.establecer("anuncios.comprobaciones_pr", False)
	ventana._datos_pr()["siguiente"].clear()
	assert ventana.repasar_prs(ahora) == 0


def test_propiedades_y_estado_del_turno_dicen_lo_sabido(ventana, repositorio, monkeypatch):
	fila = _fila("ctrlia:a", repositorio)
	pedidas: list[str] = []
	monkeypatch.setattr(ventana, "_consultar_para_vigilancia", lambda carpeta: pedidas.append(carpeta))
	assert ventana.lineas_pr(fila) == [] and ventana.frase_pr(fila) == ""
	assert set(pedidas) == {str(repositorio)}  # se pregunta en segundo plano para la próxima vez
	ventana._guardar_registro_pr(repositorio, pr.desde_json(PR_FALLIDA))
	lineas = ventana.lineas_pr(fila)
	assert lineas[0] == "PR: PR 14035 (borrador): las comprobaciones han fallado: lint."
	assert "Enlace de la PR: https://github.com/cli/cli/pull/14035" in lineas
	assert ventana.frase_pr(fila) == "PR 14035 (borrador): las comprobaciones han fallado: lint."
	ventana._guardar_registro_pr(repositorio, None, "sin_pr")
	assert ventana.lineas_pr(fila)[0].startswith("PR: ninguna para la rama actual")


def test_menu_de_la_sesion_tiene_pr_y_comprobaciones(ventana):
	import re

	from app.ui.ventana_principal import MENUS

	sesion = next(elementos for titulo, elementos in MENUS if titulo == "&Sesión")
	assert ("submenu", "PR y co&mprobaciones", "crear_menu_pr") in sesion
	menu = ventana.crear_menu_pr()
	etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems()]
	assert etiquetas[0] == "&Estado de la PR...\tCtrl+Alt+P"
	letras = [re.search(r"&(\w)", e).group(1).lower() for e in etiquetas]
	assert len(set(letras)) == len(letras) == 5
	assert all(item.GetHelp() for item in menu.GetMenuItems())
	menu.Destroy()


def test_menu_contextual_lleva_el_submenu_solo_en_sesiones_locales(ventana, repositorio, monkeypatch):
	elegida = [_fila("ctrlia:a", repositorio)]
	monkeypatch.setattr(ventana, "fila_seleccionada", lambda: elegida[0])
	menu = ventana._menu_contextual_sesiones()
	assert any(item.GetItemLabel() == "&PR y comprobaciones" for item in menu.GetMenuItems())
	menu.Destroy()
	elegida[0] = _fila("importada:x", repositorio)
	menu = ventana._menu_contextual_sesiones()
	assert not any("PR" in item.GetItemLabel() for item in menu.GetMenuItems())
	menu.Destroy()


def _sincrono(ventana, monkeypatch):
	def en_segundo_plano(funcion, *argumentos, al_terminar=None, al_fallar=None, clave=None):
		resultado = funcion(*argumentos)
		if al_terminar is not None:
			al_terminar(resultado)

	monkeypatch.setattr(ventana, "en_segundo_plano", en_segundo_plano)


def test_estado_de_la_pr_en_el_visor(ventana, repositorio, monkeypatch, avisados):
	from app.ui import dialogo_texto

	_sincrono(ventana, monkeypatch)
	monkeypatch.setattr(pr, "consultar_pr", lambda carpeta: pr.desde_json(PR_FALLIDA))
	mostrados: list[tuple] = []
	monkeypatch.setattr(dialogo_texto, "mostrar_texto", lambda *a, **k: mostrados.append(a))
	ventana.accion_estado_pr(_fila("ctrlia:a", repositorio))
	_padre, titulo, etiqueta, texto = mostrados[0]
	assert titulo == "Estado de la PR 14035" and etiqueta == "Estado de la &PR:"
	assert "Fallidas:\n- lint (Lint)" in texto
	assert ventana.registro_pr(repositorio).estado.numero == 14035


def test_error_claro_si_no_hay_pr(ventana, repositorio, monkeypatch, avisados):
	from app.ui import vp_pr

	_sincrono(ventana, monkeypatch)

	def sin_pr(carpeta):
		raise pr.ErrorPR("No hay ninguna PR para la rama «x».", "sin_pr")

	monkeypatch.setattr(pr, "consultar_pr", sin_pr)
	mensajes: list[tuple] = []
	monkeypatch.setattr(vp_pr, "mostrar_mensaje", lambda padre, texto, titulo="": mensajes.append((texto, titulo)))
	ventana.accion_leer_fallo_pr(_fila("ctrlia:a", repositorio))
	assert mensajes == [("No hay ninguna PR para la rama «x».", "Fallo de las comprobaciones")]
	assert ventana.registro_pr(repositorio).codigo_error == "sin_pr"


def test_copiar_el_enlace_usa_lo_sabido(ventana, repositorio, monkeypatch, avisados):
	_sincrono(ventana, monkeypatch)
	ventana._guardar_registro_pr(repositorio, pr.desde_json(PR_FALLIDA))
	monkeypatch.setattr(pr, "consultar_pr", lambda carpeta: pytest.fail("no debía preguntar a GitHub"))
	copiados: list[str] = []
	monkeypatch.setattr(ventana, "copiar", lambda texto: copiados.append(texto))
	ventana.accion_copiar_enlace_pr(_fila("ctrlia:a", repositorio))
	assert copiados == ["https://github.com/cli/cli/pull/14035"]


def test_pedir_al_agente_que_lo_arregle_no_envia(ventana, repositorio, monkeypatch, avisados):
	dichos, _sonidos = avisados
	_sincrono(ventana, monkeypatch)
	monkeypatch.setattr(pr, "consultar_pr", lambda carpeta: pr.desde_json(PR_FALLIDA))
	monkeypatch.setattr(pr, "leer_fallo", lambda estado: "Error: Process completed with exit code 1.")
	abiertas: list[str] = []
	monkeypatch.setattr(ventana, "abrir_sesion", lambda clave, mover_foco=True: abiertas.append(clave))
	monkeypatch.setattr(ventana, "ir_a_respuesta", lambda: None)
	monkeypatch.setattr(ventana, "accion_enviar", lambda: pytest.fail("no debe enviarse solo"))
	ventana.respuesta.SetValue("Borrador mío")
	ventana.accion_arreglar_pr(_fila("ctrlia:a", repositorio))
	texto = ventana.respuesta.GetValue()
	assert abiertas == ["ctrlia:a"]
	assert texto.startswith("Borrador mío\n\nLas comprobaciones automáticas (CI) de la PR 14035")
	assert "```\nError: Process completed with exit code 1.\n```" in texto
	assert dichos[-1].startswith("Mensaje con el fallo preparado")


def test_sesion_remota_no_consulta(ventana, tmp_path, monkeypatch):
	monkeypatch.setattr(pr, "consultar_pr", lambda carpeta: pytest.fail("no debía preguntar a GitHub"))
	avisos_remota: list[str] = []
	monkeypatch.setattr(ventana, "_avisar_remota", lambda fila, titulo: avisos_remota.append(titulo))
	ventana.accion_estado_pr(_fila("ctrlia:r", "/home/yo/proyecto", equipo="mac", origen="propia"))
	assert avisos_remota == ["Estado de la PR"]


def test_preferencia_activada_de_fabrica():
	from app.core.configuracion import VALORES_POR_DEFECTO
	from app.ui.dialogo_preferencias import PAGINAS

	assert VALORES_POR_DEFECTO["anuncios"]["comprobaciones_pr"] is True
	opciones = {o.clave: o for p in PAGINAS for o in p.opciones}
	assert opciones["anuncios.comprobaciones_pr"].ayuda
