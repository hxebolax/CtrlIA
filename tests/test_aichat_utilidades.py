"""Incidencias de AIChat: repositorios de organizaciones (#216), rama y copia aislada en Propiedades (#331),
lectura lineal de la salida de los agentes (#258), Claude Code instalado solo con npm (#309) y sesiones
cuya conversación ya no está en el disco (#111).

Sin ventanas visibles, sin red, sin el gh ni el git reales y sin leer archivos del usuario.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import git
from app.services.agentes import instalacion, procesos
from app.services.agentes.procesos import PartidorLineas, ProcesoLineas

# ── #258: lectura lineal de la salida ─────────────────────────────────────────


def _partir(bloques: list[bytes]) -> list[bytes]:
	partidor = PartidorLineas()
	lineas: list[bytes] = []
	for bloque in bloques:
		lineas.extend(partidor.anadir(bloque))
	resto = partidor.resto()
	if resto is not None:
		lineas.append(resto)
	return lineas


def test_partidor_varias_lineas_por_bloque_y_ultima_sin_salto():
	assert _partir([b"uno\ndos\ntr", b"es\n\ncuatro"]) == [b"uno", b"dos", b"tres", b"", b"cuatro"]
	assert _partir([b"a\n", b"   "]) == [b"a"]  # al final, solo espacios: no se entrega
	assert _partir([b"", b"x\n"]) == [b"x"]


def test_partidor_une_un_caracter_utf8_partido_entre_bloques():
	texto = '{"texto": "canción ñandú €"}\n'.encode()
	for corte in range(1, len(texto)):
		lineas = _partir([texto[:corte], texto[corte:]])
		assert [linea.decode("utf-8") for linea in lineas] == ['{"texto": "canción ñandú €"}']
	# Byte a byte también.
	assert _partir([texto[i:i + 1] for i in range(len(texto))])[0].decode("utf-8") == '{"texto": "canción ñandú €"}'


def test_partidor_una_linea_de_varios_megas_no_tarda():
	linea = ("€" * 3_000_000).encode("utf-8")  # 9 MB en una sola línea
	datos = linea + b"\nfin"
	bloques = [datos[i:i + 4096] for i in range(0, len(datos), 4096)]  # cortes en mitad de los caracteres
	inicio = time.perf_counter()
	lineas = _partir(bloques)
	duracion = time.perf_counter() - inicio
	assert lineas == [linea, b"fin"]
	assert duracion < 3.0, f"tardó {duracion:.2f} s"


class _SalidaFalsa:
	def __init__(self, bloques: list[bytes]) -> None:
		self._bloques = list(bloques)

	def read1(self, _tamano: int) -> bytes:
		return self._bloques.pop(0) if self._bloques else b""


def test_proceso_lineas_entrega_lo_mismo_que_antes(monkeypatch):
	"""El hilo lector, con un proceso simulado: mismas líneas, ``\\r`` quitado, vacías fuera, última sin salto."""
	monkeypatch.setattr(procesos, "bajar_prioridad_hilo_actual", lambda: None)  # es el hilo de las pruebas
	recibidas: list[str] = []
	codigos: list[int | None] = []
	proceso = ProcesoLineas(["x"], recibidas.append, codigos.append, nombre="prueba")
	datos = '{"a": "é"}\r\n\n  \n{"b": 2}\n{"c": "ñ"}'.encode()
	corte = datos.index("é".encode()) + 1  # en mitad de la «é»
	proceso._proceso = SimpleNamespace(stdout=_SalidaFalsa([datos[:corte], datos[corte:]]), wait=lambda timeout=None: 0)
	proceso._leer_salida()
	assert recibidas == ['{"a": "é"}', '{"b": 2}', '{"c": "ñ"}']
	assert codigos == [0] and proceso.esperar_fin(0)


# ── #309: Claude Code instalado solo con npm ──────────────────────────────────


@pytest.fixture
def entorno_npm(tmp_path, monkeypatch):
	"""PATH y APPDATA apuntan a carpetas temporales vacías."""
	en_path = tmp_path / "path"
	appdata = tmp_path / "appdata"
	en_path.mkdir()
	(appdata / "npm").mkdir(parents=True)
	monkeypatch.setenv("PATH", str(en_path))
	monkeypatch.setenv("APPDATA", str(appdata))
	return SimpleNamespace(path=en_path, npm=appdata / "npm")


_SHIM_NODE = '@ECHO off\r\n"%_prog%"  "%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*\r\n'


def test_sin_lanzadores_no_hay_aviso(entorno_npm):
	assert instalacion.lanzadores_npm_claude() == []
	assert instalacion.aviso_claude_npm(lambda: None) == ""


def test_solo_npm_explica_que_hacer(entorno_npm):
	(entorno_npm.npm / "claude.cmd").write_text(_SHIM_NODE, encoding="utf-8")
	(entorno_npm.path / "claude.ps1").write_text("# lanzador", encoding="utf-8")
	assert {r.name for r in instalacion.lanzadores_npm_claude()} == {"claude.cmd", "claude.ps1"}
	aviso = instalacion.aviso_claude_npm(lambda: None)
	assert "npm" in aviso and "claude install" in aviso
	assert "npm uninstall -g @anthropic-ai/claude-code" in aviso and "Agentes instalados" in aviso


def test_con_claude_nativo_no_hay_aviso(entorno_npm, tmp_path):
	(entorno_npm.npm / "claude.cmd").write_text(_SHIM_NODE, encoding="utf-8")
	assert instalacion.aviso_claude_npm(lambda: tmp_path / "claude.exe") == ""


def test_lanzador_que_apunta_a_un_exe_nativo_no_es_la_instalacion_antigua(entorno_npm):
	carpeta = entorno_npm.npm / "node_modules" / "claude"
	carpeta.mkdir(parents=True)
	(carpeta / "claude.exe").write_bytes(b"MZ\x90\x00resto")
	(entorno_npm.npm / "claude.cmd").write_text('"%~dp0\\node_modules\\claude\\claude.exe" %*\r\n', encoding="utf-8")
	assert instalacion.aviso_claude_npm(lambda: None) == ""


def test_un_fallo_al_buscar_el_nativo_no_tumba_el_aviso(entorno_npm):
	(entorno_npm.npm / "claude.cmd").write_text(_SHIM_NODE, encoding="utf-8")

	def falla():
		raise OSError("sin permiso")

	assert instalacion.aviso_claude_npm(falla) == instalacion.AVISO_CLAUDE_NPM


# ── #216: repositorios de las organizaciones ──────────────────────────────────


def _repo(nombre: str, privado: bool = False) -> dict:
	return {"nameWithOwner": nombre, "description": f"Descripción de {nombre}", "isPrivate": privado, "updatedAt": "x"}


@pytest.fixture
def gh_simulado(monkeypatch):
	"""``gh`` falso: responde según la orden y apunta cada llamada."""
	llamadas: list[list[str]] = []
	respuestas: dict[str, tuple[int, str, str]] = {}

	def ejecutar(orden, tiempo=30.0, entorno=None):
		orden = list(orden)
		llamadas.append(orden)
		if orden[1:3] == ["api", "user/orgs"]:
			return respuestas.get("orgs", (0, "", ""))
		dueno = orden[3] if len(orden) > 3 and not orden[3].startswith("--") else ""
		return respuestas.get("repos:" + dueno, (0, "[]", ""))

	monkeypatch.setattr(git, "buscar_gh", lambda: Path("gh.exe"))
	monkeypatch.setattr(procesos, "ejecutar_y_leer", ejecutar)
	return SimpleNamespace(llamadas=llamadas, respuestas=respuestas)


def test_lista_tus_repositorios_y_los_de_tus_organizaciones_sin_repetir(gh_simulado):
	gh_simulado.respuestas.update({
		"repos:": (0, json.dumps([_repo("ana/web"), _repo("ana/notas", True)]), ""),
		"orgs": (0, "MiEmpresa\nOtraOrg\nRota\n../malo\n", ""),
		"repos:MiEmpresa": (0, json.dumps([_repo("MiEmpresa/api", True), _repo("ana/web")]), ""),
		"repos:OtraOrg": (0, json.dumps([_repo("OtraOrg/docs")]), ""),
		"repos:Rota": (1, "", "HTTP 403: Resource protected by organization SAML enforcement"),
	})
	repos = git.listar_repositorios()
	assert [r["nombre"] for r in repos] == ["ana/web", "ana/notas", "MiEmpresa/api", "OtraOrg/docs"]
	por_nombre = {r["nombre"]: r for r in repos}
	assert por_nombre["ana/web"]["tipo_dueno"] == "Tuyo" and por_nombre["ana/web"]["dueno"] == "ana"
	assert por_nombre["MiEmpresa/api"] == {"nombre": "MiEmpresa/api", "dueno": "MiEmpresa", "tipo_dueno": "Organización",
		"descripcion": "Descripción de MiEmpresa/api", "privado": "Privado"}
	# Se pidió cada organización válida (el nombre raro se descarta) y nada con shell.
	duenos = [o[3] for o in gh_simulado.llamadas if o[1:3] == ["repo", "list"] and not o[3].startswith("--")]
	assert duenos == ["MiEmpresa", "OtraOrg", "Rota"]
	assert ["gh.exe", "api", "user/orgs", "--paginate", "--jq", ".[].login"] in gh_simulado.llamadas


def test_sin_organizaciones_legibles_salen_al_menos_los_tuyos(gh_simulado):
	gh_simulado.respuestas.update({
		"repos:": (0, json.dumps([_repo("ana/web")]), ""),
		"orgs": (1, "", "error de red"),
	})
	assert [r["nombre"] for r in git.listar_repositorios()] == ["ana/web"]


def test_sin_sesion_en_github_sigue_siendo_un_error_claro(gh_simulado):
	gh_simulado.respuestas["repos:"] = (1, "", "To get started with GitHub CLI, please run:  gh auth login")
	with pytest.raises(git.ErrorGit, match="gh auth login"):
		git.listar_repositorios()


def test_filas_del_selector_con_dueno_y_la_opcion_de_escribirlo_a_mano(app_wx):
	from app.ui.dialogos import github

	repos = [
		{"nombre": "ana/web", "dueno": "ana", "tipo_dueno": "Tuyo", "descripcion": "", "privado": "Público"},
		{"nombre": "MiEmpresa/api", "dueno": "MiEmpresa", "tipo_dueno": "Organización", "descripcion": "API",
			"privado": "Privado"},
	]
	filas = github.filas_repositorios(repos)
	assert filas[0] == ("ana/web", "ana (tuyo)", "Sin descripción", "Público")
	assert filas[1] == ("MiEmpresa/api", "MiEmpresa (organización)", "API", "Privado")
	assert "a mano" in filas[-1][0] and len(filas) == 3


def test_la_fila_fija_se_ve_aunque_se_filtre(marco):
	from app.ui.dialogos.selector import DialogoSelector

	filas = [("ana/web", "ana (tuyo)"), ("MiEmpresa/api", "MiEmpresa (organización)"), ("Escribir a mano...", "")]
	dialogo = DialogoSelector(marco, "Clonar", "&Repositorios:", filas, ["Repositorio", "Dueño"], fijas=1)
	try:
		dialogo.filtro.ChangeValue("empresa")
		dialogo._rellenar()
		assert dialogo._visibles == [1, 2]
		dialogo.filtro.ChangeValue("nada-coincide")
		dialogo._rellenar()
		assert dialogo._visibles == [2]
	finally:
		dialogo.Destroy()


class _DirDialogFalso:
	def __init__(self, *args, **kwargs) -> None:
		pass

	def __enter__(self):
		return self

	def __exit__(self, *args) -> None:
		return None

	def ShowModal(self) -> int:  # noqa: N802 - API de wx
		import wx

		return wx.ID_OK

	def GetPath(self) -> str:  # noqa: N802 - API de wx
		return r"C:\copias"


def test_elegir_la_fila_a_mano_pide_dueno_y_nombre(app_wx, monkeypatch):
	import wx

	from app.ui import avisos
	from app.ui.dialogos import github, selector

	repos = [{"nombre": "ana/web", "dueno": "ana", "tipo_dueno": "Tuyo", "descripcion": "", "privado": "Público"}]
	llamadas: list[tuple] = []
	ventana = SimpleNamespace()

	def en_segundo_plano(funcion, *argumentos, al_terminar=None, al_fallar=None, clave=None):
		llamadas.append((funcion, argumentos))
		if funcion is git.listar_repositorios:
			al_terminar(repos)

	ventana.en_segundo_plano = en_segundo_plano
	elegidos: list[dict] = []

	def elegir(padre, titulo, etiqueta, filas, cabeceras, ayuda="", fijas=0):
		elegidos.append({"filas": filas, "fijas": fijas, "cabeceras": cabeceras})
		return len(filas) - 1  # la fila «escribir a mano»

	monkeypatch.setattr(selector, "elegir", elegir)
	monkeypatch.setattr(github, "pedir_texto", lambda *a, **k: " OtraOrg/privado ")
	monkeypatch.setattr(wx, "DirDialog", _DirDialogFalso)
	monkeypatch.setattr(avisos, "anunciar", lambda *a, **k: None)
	github.clonar_repositorio(ventana, lambda carpeta: None)
	assert elegidos[0]["fijas"] == 1 and "Dueño" in elegidos[0]["cabeceras"]
	assert llamadas[-1] == (git.clonar, ("OtraOrg/privado", Path(r"C:\copias")))


# ── #331: rama y copia aislada en Propiedades ─────────────────────────────────


@pytest.fixture
def git_simulado(monkeypatch):
	respuestas: dict[str, tuple[int, str, str]] = {}

	def ejecutar(orden, tiempo=30.0, entorno=None):
		clave = " ".join(list(orden)[3:])
		return respuestas.get(clave, (128, "", "fatal: not a git repository"))

	monkeypatch.setattr(git, "buscar_git", lambda: Path("git.exe"))
	monkeypatch.setattr(procesos, "ejecutar_y_leer", ejecutar)
	return respuestas


def test_tipo_de_copia_normal_aislada_y_submodulo(tmp_path):
	normal = tmp_path / "normal"
	(normal / ".git").mkdir(parents=True)
	assert git.tipo_de_copia(normal) == "normal"
	aislada = tmp_path / "aislada"
	aislada.mkdir()
	(aislada / ".git").write_text("gitdir: E:/repo/.git/worktrees/rama-nueva\n", encoding="utf-8")
	assert git.tipo_de_copia(aislada) == "aislada"
	submodulo = tmp_path / "sub"
	submodulo.mkdir()
	(submodulo / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
	assert git.tipo_de_copia(submodulo) == "submodulo"


def test_rama_actual_y_repositorio_sin_commits(git_simulado, tmp_path):
	git_simulado["rev-parse --abbrev-ref HEAD"] = (0, "main\n", "")
	assert git.rama_actual(tmp_path) == "main"
	del git_simulado["rev-parse --abbrev-ref HEAD"]
	git_simulado["symbolic-ref --short -q HEAD"] = (0, "trunk\n", "")
	assert git.rama_actual(tmp_path) == "trunk"
	del git_simulado["symbolic-ref --short -q HEAD"]
	assert git.rama_actual(tmp_path) is None
	assert git.rama_actual(tmp_path / "no-existe") is None


def test_describir_una_copia_aislada(git_simulado, tmp_path):
	(tmp_path / ".git").write_text("gitdir: E:/repo/.git/worktrees/arreglo\n", encoding="utf-8")
	git_simulado["rev-parse --show-toplevel"] = (0, tmp_path.as_posix() + "\n", "")
	git_simulado["rev-parse --abbrev-ref HEAD"] = (0, "arreglo\n", "")
	lineas = git.describir_repositorio(str(tmp_path))
	assert lineas[0] == "Rama: arreglo"
	assert lineas[1].startswith("Repositorio de git: copia aislada (worktree)")
	assert git.es_copia_aislada(tmp_path) is True


def test_describir_un_repositorio_normal_en_un_commit_suelto(git_simulado, tmp_path):
	(tmp_path / ".git").mkdir()
	git_simulado["rev-parse --show-toplevel"] = (0, str(tmp_path), "")
	git_simulado["rev-parse --abbrev-ref HEAD"] = (0, "HEAD\n", "")
	lineas = git.describir_repositorio(tmp_path)
	assert lineas[0].startswith("Rama: ninguna") and "repositorio normal" in lineas[1]
	assert git.es_copia_aislada(tmp_path) is False


def test_describir_una_carpeta_que_no_es_repositorio_o_sin_git(git_simulado, tmp_path, monkeypatch):
	assert git.describir_repositorio(tmp_path) == ["Repositorio de git: no, la carpeta no es un repositorio."]
	assert git.es_copia_aislada(tmp_path) is None
	assert git.describir_repositorio(tmp_path / "no-existe") == []
	monkeypatch.setattr(git, "buscar_git", lambda: None)
	assert git.describir_repositorio(tmp_path) == []


def _propiedades(monkeypatch, fila, descripcion):
	"""Llama a ``_mostrar_propiedades`` con una ventana falsa cuyo trabajo de fondo se hace al momento."""
	from app.ui import visor_ayuda
	from app.ui.vp_sesiones import MixinSesiones

	mostrados: list[str] = []
	en_fondo: list[tuple] = []
	monkeypatch.setattr(visor_ayuda, "mostrar_documento", lambda padre, titulo, texto, **k: mostrados.append(texto))
	monkeypatch.setattr(git, "describir_repositorio", descripcion)

	def en_segundo_plano(funcion, *argumentos, al_terminar=None, al_fallar=None, clave=None):
		en_fondo.append(argumentos)
		try:
			resultado = funcion(*argumentos)
		except Exception as error:  # noqa: BLE001 - se entrega como haría la ventana
			al_fallar(error)
		else:
			al_terminar(resultado)

	ventana = SimpleNamespace(en_segundo_plano=en_segundo_plano)
	MixinSesiones._mostrar_propiedades(ventana, fila, ["Título: x", "Carpeta: " + (fila.carpeta or "Sin carpeta"), "Modelo: y"])
	return mostrados, en_fondo


def test_propiedades_pone_la_rama_tras_la_carpeta(app_wx, monkeypatch):
	from app.services.gestor import FilaSesion

	fila = FilaSesion("ctrlia:1", "claude", "x", carpeta=r"C:\proyecto")
	mostrados, en_fondo = _propiedades(monkeypatch, fila, lambda carpeta: ["Rama: main", "Repositorio de git: normal"])
	assert en_fondo == [(r"C:\proyecto",)]
	assert mostrados[0].split("\n\n") == ["Título: x", r"Carpeta: C:\proyecto", "Rama: main", "Repositorio de git: normal",
		"Modelo: y"]


def test_propiedades_sin_git_en_remotas_y_si_git_falla(app_wx, monkeypatch):
	from app.services.gestor import FilaSesion

	def nunca(_carpeta):
		raise AssertionError("no se debe preguntar a git")

	remota = FilaSesion("claude:r", "claude", "x", carpeta="/home/ana/web", equipo="mac", origen="terminal")
	mostrados, en_fondo = _propiedades(monkeypatch, remota, nunca)
	assert en_fondo == [] and "Rama" not in mostrados[0]
	sin_carpeta = FilaSesion("ollama:1", "ollama", "x")
	mostrados, en_fondo = _propiedades(monkeypatch, sin_carpeta, nunca)
	assert en_fondo == [] and len(mostrados) == 1

	def falla(_carpeta):
		raise OSError("git colgado")

	mostrados, _en_fondo = _propiedades(monkeypatch, FilaSesion("ctrlia:2", "claude", "x", carpeta=r"C:\p"), falla)
	assert len(mostrados) == 1 and "Rama" not in mostrados[0]


# ── #111: conversación no disponible ──────────────────────────────────────────


def test_columnas_de_una_sesion_sin_conversacion(app_wx):
	from app.services.gestor import FilaSesion
	from app.ui.vp_sesiones import valores_sesion

	fila = FilaSesion("claude:x", "claude", "Vieja", origen="escritorio", sin_conversacion=True)
	estado, ultimo = valores_sesion(fila, ["estado", "ultimo"], lambda clave, defecto: defecto)
	assert estado == "Inactiva, conversación no disponible"
	assert ultimo == "Conversación no disponible"
	normal = FilaSesion("claude:y", "claude", "Buena", origen="terminal", ultimo_mensaje="Hecho.")
	assert valores_sesion(normal, ["estado", "ultimo"], lambda clave, defecto: defecto) == ["Inactiva", "Hecho."]


@pytest.fixture
def gestor_claude(tmp_path, data_temporal, monkeypatch):
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones
	from app.services.historial import claude as historial_claude
	from tests.test_gestor import ProveedorFalso

	opciones = {"historial.claude": True, "historial.codex": False, "historial.antigravity": False,
		"historial.ollama": False}
	sesiones = [
		historial_claude.SesionClaude(clave="claude:perdida", id="perdida", titulo="Perdida", carpeta="C:/p",
			origen="escritorio", creada=1.0, actividad=2.0, estado="inactiva", archivada=False, ruta_transcript=None),
		historial_claude.SesionClaude(clave="claude:viva", id="viva", titulo="Viva", carpeta="C:/p",
			origen="terminal", creada=1.0, actividad=3.0, estado="inactiva", archivada=False,
			ruta_transcript=tmp_path / "viva.jsonl"),
	]
	monkeypatch.setattr(historial_claude, "listar_sesiones", lambda propios, incluir_archivadas=False: list(sesiones))
	monkeypatch.setattr(historial_claude, "ruta_transcript", lambda *a, **k: None)  # nunca busca en el disco real
	gestor = GestorSesiones(Almacen(tmp_path / "a.db"), {"claude": ProveedorFalso()}, lambda c, e: None,
		lambda clave, defecto: opciones.get(clave, defecto))
	yield gestor
	gestor.almacen.cerrar()


def test_la_lista_marca_la_sesion_sin_conversacion_y_abrirla_lo_explica(gestor_claude):
	filas = {f.clave: f for f in gestor_claude._listar_externas()}
	assert filas["claude:perdida"].sin_conversacion is True
	assert filas["claude:viva"].sin_conversacion is False
	mensajes = gestor_claude.mensajes("claude:perdida")
	assert len(mensajes) == 1 and mensajes[0].rol == "sistema"
	assert mensajes[0].texto.startswith("Conversación no disponible.") and "30 días" in mensajes[0].texto
