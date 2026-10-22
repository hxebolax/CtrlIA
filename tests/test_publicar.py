"""Publicación de versiones y subida a GitHub (tools/publicar.py, tools/subir_github.py, tools/empaquetar.py).

git y gh están simulados: ninguna prueba toca el repositorio, GitHub ni la
carpeta real del servidor. Todo ocurre en carpetas temporales.
"""

from __future__ import annotations

import json
import sys
import types
import zipfile
from datetime import date
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ / "tools") not in sys.path:
	sys.path.insert(0, str(RAIZ / "tools"))

import empaquetar  # noqa: E402
import firmar_version  # noqa: E402
import publicar  # noqa: E402
import subir_github  # noqa: E402
from subir_github import Cambio, ErrorPublicacion, Resultado  # noqa: E402

pytest.importorskip("cryptography")

VERSION = "2.3.0"
CHANGELOG = """# Novedades de Ctrl+IA

## Versión 2.3.0 - 2026-10-22

### Novedades

- **Deshacer** los cambios con `Ctrl+Alt+Z`.
* Ver el [manual](https://ejemplo.invalid/manual) con F1.


### Correcciones

- Arreglo de \\*asteriscos\\* y *cursiva*.

## Versión 2.2.0 - 2026-10-01

- Algo antiguo.
"""


# ── Simulación de git y gh ───────────────────────────────────────────────────

class Simulado:
	"""Ejecutor falso: responde según el prefijo más largo de la orden y apunta cada llamada."""

	def __init__(self, respuestas: dict[tuple[str, ...], Resultado] | None = None):
		self.respuestas = dict(respuestas or {})
		self.registro: list[list[str]] = []
		self.entradas: list[str | None] = []

	def __call__(self, orden, *, entrada=None, capturar=True):
		orden = [str(parte) for parte in orden]
		self.registro.append(orden)
		self.entradas.append(entrada)
		mejor: tuple[str, ...] | None = None
		for clave in self.respuestas:
			if tuple(orden[: len(clave)]) == clave and (mejor is None or len(clave) > len(mejor)):
				mejor = clave
		return self.respuestas[mejor] if mejor is not None else Resultado(0)

	def ordenes(self, *prefijo: str) -> list[list[str]]:
		return [orden for orden in self.registro if tuple(orden[: len(prefijo)]) == prefijo]


def repo_git(raiz: Path, estado: str = "", seguidos: str = "", **extra: Resultado) -> dict:
	"""Respuestas de un repositorio sano en main con origin."""
	respuestas = {
		("git", "rev-parse", "--show-toplevel"): Resultado(0, str(raiz) + "\n"),
		("git", "branch", "--show-current"): Resultado(0, "main\n"),
		("git", "remote", "get-url", "origin"): Resultado(0, "https://github.com/hxebolax/CtrlIA.git\n"),
		("git", "status"): Resultado(0, estado),
		("git", "ls-files"): Resultado(0, seguidos),
		("git", "rev-parse", "--verify", "--quiet", "HEAD"): Resultado(0, "abc\n"),
		("git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"): Resultado(0, "abc\n"),
		("git", "rev-list", "--count"): Resultado(0, "0\n"),
		("git", "rev-parse", "--verify", "--quiet", f"refs/tags/v{VERSION}"): Resultado(1),
		("git", "ls-remote"): Resultado(0, ""),
		("gh", "release", "view"): Resultado(1, "", "release not found"),
	}
	respuestas.update({tuple(clave.split("|")): valor for clave, valor in extra.items()})
	return respuestas


def mudo(_texto: str = "") -> None:
	pass


# ── Novedades del manifiesto (empaquetar) ────────────────────────────────────

def test_markdown_a_texto_deja_texto_plano_legible():
	texto = empaquetar.markdown_a_texto(
		"### Novedades\n\n- **Negrita** y `código`.\n* [Enlace](https://x.invalid) y *cursiva*.\n\n\n\n### Correcciones\n- Un \\*escape\\*."
	)
	assert texto == (
		"Novedades:\n\n- Negrita y código.\n- Enlace y cursiva.\n\nCorrecciones:\n- Un *escape*."
	)


def test_novedades_de_version_sale_del_changelog(tmp_path):
	ruta = tmp_path / "changelog.md"
	ruta.write_text(CHANGELOG, encoding="utf-8")
	novedades = empaquetar.novedades_de_version(VERSION, ruta)
	assert novedades.startswith("Novedades:\n\n- Deshacer los cambios con Ctrl+Alt+Z.")
	assert "- Ver el manual con F1." in novedades
	assert "Arreglo de *asteriscos* y cursiva." in novedades
	assert "Algo antiguo" not in novedades
	assert "**" not in novedades and "##" not in novedades
	assert empaquetar.novedades_de_version("9.9.9", ruta) is None


def test_empaquetar_mete_las_novedades_antes_de_firmar(tmp_path, monkeypatch):
	"""El manifiesto lleva «novedades» (la clave que lee el actualizador) y la firma sigue siendo válida."""
	(tmp_path / "app" / "core").mkdir(parents=True)
	(tmp_path / "app" / "core" / "version.py").write_text(f'APP_VERSION = "{VERSION}"\n', encoding="utf-8")
	(tmp_path / "docs").mkdir()
	(tmp_path / "docs" / "changelog.md").write_text(CHANGELOG, encoding="utf-8")
	(tmp_path / "tools").mkdir()
	(tmp_path / "tools" / "firmar_version.py").write_bytes((RAIZ / "tools" / "firmar_version.py").read_bytes())
	origen = tmp_path / "build" / "CtrlIA"
	origen.mkdir(parents=True)
	(origen / "CtrlIA.exe").write_bytes(b"MZ")
	clave = tmp_path / "claves" / "firma.key"
	publica = firmar_version.generar(clave)
	monkeypatch.setattr(empaquetar, "RAIZ", tmp_path)
	monkeypatch.setattr(empaquetar, "ORIGEN", origen)
	monkeypatch.setattr(empaquetar, "CLAVE_FIRMA", clave)
	monkeypatch.setitem(sys.modules, "verificar_build", types.SimpleNamespace(verificar=lambda *_: []))
	assert empaquetar.main() == 0
	manifiesto = json.loads((tmp_path / "build" / "version.json").read_text(encoding="utf-8"))
	assert manifiesto["version"] == VERSION
	assert manifiesto[empaquetar.CAMPO_NOVEDADES].startswith("Novedades:")
	assert firmar_version.verificar(tmp_path / "build" / "version.json", [publica]) == []
	# Cambiar las novedades invalida la firma: también están protegidas.
	manifiesto["novedades"] += "\n- Añadido por un intruso."
	(tmp_path / "build" / "version.json").write_text(json.dumps(manifiesto), encoding="utf-8")
	assert firmar_version.verificar(tmp_path / "build" / "version.json", [publica], comprobar_zip=False)


def test_la_clave_de_novedades_es_la_que_lee_el_actualizador():
	ruta = RAIZ / "app" / "services" / "actualizador.py"
	texto = ruta.read_text(encoding="utf-8")
	constante = publicar._asignacion_en(ruta, "CAMPO_NOVEDADES")
	assert constante == empaquetar.CAMPO_NOVEDADES or f'manifiesto.get("{empaquetar.CAMPO_NOVEDADES}")' in texto


# ── Archivos prohibidos y mensajes de commit (subir_github) ──────────────────

@pytest.mark.parametrize(
	"ruta",
	[
		"tools/claves/firma_ed25519.key",
		"tools/claves/.gitignore",
		"otra/carpeta/privada.key",
		"certificado.PEM",
		"adjuntos/investigacion/x.py",
		".agents/skills/a/SKILL.md",
		".claude/settings.json",
		"app/.claude/x.json",
		"AGENTS.md",
		"CLAUDE.md",
		"data/config/settings.json",
		"data/db/ctrlia.sqlite3",
		"data/logs/sesion.log",
		"data/cache/x.bin",
		"build/CtrlIA/CtrlIA.exe",
		".venv/Scripts/python.exe",
		"tools/publicar.local.json",
		".agentes-instalados.json",
		"docs/auditorias/auditoria_1.md",
		"requirements-lock.txt",
		"data/temp/x.tmp",
		"data/backups/copia.zip",
		"data/skills/catalogo.json",
		"data/diagnostico/paquete.zip",
		"data/ollama/modelos.json",
		"data/sessions/s.json",
		"data/downloads/a.zip",
		"data/sounds/propios/mio.wav",
		"data/skills_ejemplo/proyecto-nuevo/plantillas/.ruff_cache/0.17.1/123",
		"app/.pytest_cache/v/cache/lastfailed",
	],
)
def test_detecta_rutas_prohibidas(ruta, tmp_path):
	assert subir_github.motivo_prohibido(ruta, tmp_path)


@pytest.mark.parametrize(
	"ruta",
	[
		"app/main.py",
		"tools/publicar.py",
		"data/sounds/aviso.wav",
		"data/configuracion.txt",
		"docs/manual.md",
		"data/skills_ejemplo/accesibilidad-wx/SKILL.md",
		"requirements.txt",
	],
)
def test_rutas_permitidas(ruta, tmp_path):
	assert subir_github.motivo_prohibido(ruta, tmp_path) is None


def test_detecta_archivos_demasiado_grandes(tmp_path):
	(tmp_path / "grande.bin").write_bytes(b"x" * 2048)
	assert "MB" in subir_github.motivo_prohibido("grande.bin", tmp_path, limite=1024)
	assert subir_github.motivo_prohibido("grande.bin", tmp_path, limite=4096) is None


def test_analiza_el_estado_de_git_con_renombrados():
	salida = " M app/a.py\0?? nuevo.txt\0R  nuevo_nombre.py\0viejo.py\0 D borrado.py\0"
	cambios = subir_github.analizar_estado(salida)
	assert [(c.ruta, c.tipo) for c in cambios] == [
		("app/a.py", "modificado"),
		("nuevo.txt", "nuevo"),
		("nuevo_nombre.py", "renombrado"),
		("borrado.py", "borrado"),
	]


def test_mensaje_automatico_con_fecha_y_resumen():
	cambios = [Cambio(" M", f"a{i}.py") for i in range(3)] + [Cambio("??", "n.py"), Cambio(" D", "b.py")]
	mensaje = subir_github.mensaje_automatico(cambios, date(2026, 10, 22))
	assert mensaje.splitlines()[0] == "Actualización del 22/10/2026: 5 archivos cambiados"
	assert "1 nuevo, 3 modificados y 1 borrado" in mensaje
	uno = subir_github.mensaje_automatico([Cambio(" M", "a.py")], date(2026, 1, 2))
	assert uno.splitlines()[0] == "Actualización del 02/01/2026: 1 archivo cambiado"


def test_coautoria_solo_si_lo_prepara_un_agente():
	cambios = [Cambio(" M", "a.py")]
	assert "Co-Authored-By" not in subir_github.componer_mensaje(None, cambios)
	assert "Co-Authored-By" not in subir_github.componer_mensaje("Arreglo del menú", cambios)
	con_agente = subir_github.componer_mensaje("Arreglo del menú", cambios, agente=True)
	assert con_agente.startswith("Arreglo del menú\n\n")
	assert con_agente.rstrip("\n").endswith(subir_github.COAUTOR)


# ── Flujo de subir_github ────────────────────────────────────────────────────

def test_subir_falla_claro_si_no_es_repositorio(tmp_path):
	simulado = Simulado({("git", "rev-parse", "--show-toplevel"): Resultado(128, "", "fatal: not a git repository")})
	with pytest.raises(ErrorPublicacion, match="todavía no es un repositorio git"):
		subir_github.subir(simulado, raiz=tmp_path, salida=mudo)


def test_subir_exige_la_rama_main(tmp_path):
	respuestas = repo_git(tmp_path)
	respuestas[("git", "branch", "--show-current")] = Resultado(0, "otra\n")
	with pytest.raises(ErrorPublicacion, match="rama «otra»"):
		subir_github.subir(Simulado(respuestas), raiz=tmp_path, salida=mudo)


def test_subir_sin_cambios_no_hace_nada(tmp_path):
	simulado = Simulado(repo_git(tmp_path))
	lineas: list[str] = []
	assert subir_github.subir(simulado, raiz=tmp_path, salida=lineas.append) is False
	assert not simulado.ordenes("git", "add") and not simulado.ordenes("git", "commit")
	assert not simulado.ordenes("git", "push")
	assert any("No hay cambios" in linea for linea in lineas)


def test_subir_con_cambios_hace_commit_y_push(tmp_path):
	simulado = Simulado(repo_git(tmp_path, estado=" M app/a.py\0?? docs/nuevo.md\0", seguidos="app/a.py\0"))
	assert subir_github.subir(simulado, raiz=tmp_path, salida=mudo, dia=date(2026, 10, 22)) is True
	assert simulado.ordenes("git", "add", "-A")
	indice = simulado.registro.index(simulado.ordenes("git", "commit")[0])
	mensaje = simulado.entradas[indice]
	assert mensaje.startswith("Actualización del 22/10/2026: 2 archivos cambiados")
	assert "Co-Authored-By" not in mensaje
	assert simulado.ordenes("git", "push", "--set-upstream", "origin", "main")


def test_subir_se_para_con_archivos_prohibidos(tmp_path):
	simulado = Simulado(repo_git(tmp_path, estado="?? tools/claves/firma_ed25519.key\0?? app/a.py\0"))
	with pytest.raises(ErrorPublicacion, match="tools/claves/firma_ed25519.key"):
		subir_github.subir(simulado, raiz=tmp_path, salida=mudo)
	assert not simulado.ordenes("git", "add") and not simulado.ordenes("git", "push")


def test_subir_detecta_prohibidos_ya_guardados_pero_no_los_que_se_borran(tmp_path):
	simulado = Simulado(repo_git(tmp_path, estado=" D AGENTS.md\0", seguidos="AGENTS.md\0app/a.py\0CLAUDE.md\0"))
	assert subir_github.buscar_prohibidos(simulado, tmp_path) == ["CLAUDE.md: instrucciones para agentes (CLAUDE.md)"]


def test_subir_en_ensayo_no_cambia_nada(tmp_path):
	simulado = Simulado(repo_git(tmp_path, estado=" M app/a.py\0"))
	lineas: list[str] = []
	assert subir_github.subir(simulado, raiz=tmp_path, ensayo=True, salida=lineas.append) is True
	assert not simulado.ordenes("git", "add") and not simulado.ordenes("git", "commit")
	assert not simulado.ordenes("git", "push")
	assert any(linea.startswith("[ensayo]") for linea in lineas)


def test_subir_sube_commits_pendientes_sin_cambios_nuevos(tmp_path):
	respuestas = repo_git(tmp_path)
	respuestas[("git", "rev-list", "--count")] = Resultado(0, "2\n")
	simulado = Simulado(respuestas)
	assert subir_github.subir(simulado, raiz=tmp_path, salida=mudo) is True
	assert not simulado.ordenes("git", "commit")
	assert simulado.ordenes("git", "push")


# ── Publicar ─────────────────────────────────────────────────────────────────

@pytest.fixture
def proyecto(tmp_path):
	"""Proyecto mínimo con versión, changelog, clave pública y una compilación firmada en build."""
	raiz = tmp_path / "proyecto"
	(raiz / "app" / "core").mkdir(parents=True)
	(raiz / "app" / "services").mkdir(parents=True)
	(raiz / "docs").mkdir()
	(raiz / "build").mkdir()
	(raiz / "app" / "core" / "version.py").write_text(f'APP_VERSION = "{VERSION}"\n', encoding="utf-8")
	(raiz / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{VERSION}"\n', encoding="utf-8")
	(raiz / "docs" / "changelog.md").write_text(CHANGELOG, encoding="utf-8")
	clave = tmp_path / "claves" / "firma.key"
	publica = firmar_version.generar(clave)
	(raiz / "app" / "services" / "actualizador.py").write_text(
		f'CLAVES_PUBLICAS_ED25519: tuple[str, ...] = ("{publica}",)\n', encoding="utf-8"
	)
	zip_ = raiz / "build" / f"CtrlIA_v{VERSION}.zip"
	with zipfile.ZipFile(zip_, "w") as archivo:
		archivo.writestr("CtrlIA/CtrlIA.exe", b"MZ" * 1000)
	manifiesto = {
		"version": VERSION,
		"file": zip_.name,
		"sha256": empaquetar.sha256_de(zip_),
		"size": zip_.stat().st_size,
		"date": "2026-10-22",
		"novedades": empaquetar.novedades_de_version(VERSION, raiz / "docs" / "changelog.md"),
	}
	(raiz / "build" / "version.json").write_text(json.dumps(manifiesto), encoding="utf-8")
	firmar_version.firmar(clave, raiz / "build" / "version.json")
	destino = tmp_path / "SynologyDrive" / "web" / "CtrlIA"
	destino.mkdir(parents=True)
	(destino / "CtrlIA_v2.2.0.zip").write_bytes(b"antiguo")
	(destino / "version.json").write_text(json.dumps({"version": "2.2.0"}), encoding="utf-8")
	return types.SimpleNamespace(raiz=raiz, destino=destino, zip_=zip_, publica=publica)


def respuestas_publicar(proyecto, **extra: Resultado) -> dict:
	respuestas = repo_git(proyecto.raiz)
	tamanos = [
		{"name": proyecto.zip_.name, "size": proyecto.zip_.stat().st_size},
		{"name": "version.json", "size": (proyecto.raiz / "build" / "version.json").stat().st_size},
	]
	respuestas[("gh", "release", "view", f"v{VERSION}", "--repo", "hxebolax/CtrlIA", "--json", "isDraft,isPrerelease,assets")] = (
		Resultado(0, json.dumps({"isDraft": False, "isPrerelease": False, "assets": tamanos}))
	)
	respuestas[("gh", "api")] = Resultado(0, f"v{VERSION}\n")
	respuestas.update({tuple(clave.split("|")): valor for clave, valor in extra.items()})
	return respuestas


def opciones(proyecto, **cambios) -> publicar.Opciones:
	valores = {
		"sin_pruebas": True,
		"sin_compilar": True,
		"sin_confirmar": True,
		"raiz": proyecto.raiz,
		"destino": proyecto.destino,
	}
	valores.update(cambios)
	return publicar.Opciones(**valores)


def test_lee_versiones_y_claves_sin_importar(proyecto):
	assert publicar.version_de_codigo(proyecto.raiz) == VERSION
	assert publicar.version_de_pyproject(proyecto.raiz) == VERSION
	assert publicar.claves_publicas(proyecto.raiz) == [proyecto.publica]
	assert publicar.claves_publicas(RAIZ)  # El proyecto real también tiene su clave pública.


def test_notas_de_release_son_la_seccion_de_la_version(proyecto):
	notas = publicar.notas_de_release(proyecto.raiz, VERSION)
	assert notas.startswith("### Novedades")
	assert "Algo antiguo" not in notas and "## Versión" not in notas
	assert publicar.notas_de_release(proyecto.raiz, "9.0.0") is None


def test_comprobaciones_paran_si_las_versiones_no_coinciden(proyecto):
	(proyecto.raiz / "pyproject.toml").write_text('[project]\nversion = "2.2.9"\n', encoding="utf-8")
	publicador = publicar.Publicador(opciones(proyecto), Simulado(respuestas_publicar(proyecto)), salida=mudo)
	with pytest.raises(ErrorPublicacion, match="no coincide"):
		publicador.comprobaciones_previas()


def test_comprobaciones_paran_sin_notas_en_el_changelog(proyecto):
	(proyecto.raiz / "docs" / "changelog.md").write_text("# Novedades\n\n## Versión 2.2.0\n\n- Viejo.\n", encoding="utf-8")
	publicador = publicar.Publicador(opciones(proyecto), Simulado(respuestas_publicar(proyecto)), salida=mudo)
	with pytest.raises(ErrorPublicacion, match="no tiene la sección de la versión 2.3.0"):
		publicador.comprobaciones_previas()


def test_comprobaciones_paran_si_la_etiqueta_ya_existe(proyecto):
	respuestas = respuestas_publicar(proyecto, **{"git|ls-remote": Resultado(0, "abc\trefs/tags/v2.3.0\n")})
	publicador = publicar.Publicador(opciones(proyecto), Simulado(respuestas), salida=mudo)
	with pytest.raises(ErrorPublicacion, match="--reemplazar"):
		publicador.comprobaciones_previas()
	con_reemplazar = publicar.Publicador(
		opciones(proyecto, reemplazar=True), Simulado(respuestas), salida=mudo
	)
	(proyecto.destino / "version.json").write_text(json.dumps({"version": VERSION}), encoding="utf-8")
	con_reemplazar.comprobaciones_previas()
	assert con_reemplazar.etiqueta_remota


def test_comprobaciones_paran_si_el_servidor_tiene_una_version_mas_nueva(proyecto):
	(proyecto.destino / "version.json").write_text(json.dumps({"version": "3.0.0"}), encoding="utf-8")
	publicador = publicar.Publicador(opciones(proyecto), Simulado(respuestas_publicar(proyecto)), salida=mudo)
	with pytest.raises(ErrorPublicacion, match="más nueva"):
		publicador.comprobaciones_previas()


def test_comprobaciones_paran_si_no_es_repositorio(proyecto):
	respuestas = respuestas_publicar(proyecto)
	respuestas[("git", "rev-parse", "--show-toplevel")] = Resultado(128, "", "fatal: not a git repository")
	publicador = publicar.Publicador(opciones(proyecto), Simulado(respuestas), salida=mudo)
	with pytest.raises(ErrorPublicacion, match="no es un repositorio git"):
		publicador.comprobaciones_previas()


def test_ensayo_no_copia_ni_sube_ni_crea_nada(proyecto):
	simulado = Simulado(respuestas_publicar(proyecto, **{"git|status": Resultado(0, " M app/a.py\0")}))
	lineas: list[str] = []
	publicador = publicar.Publicador(opciones(proyecto, ensayo=True), simulado, salida=lineas.append)
	assert publicador.publicar() == 0
	assert not (proyecto.destino / proyecto.zip_.name).exists()
	assert json.loads((proyecto.destino / "version.json").read_text(encoding="utf-8"))["version"] == "2.2.0"
	for prefijo in (("git", "add"), ("git", "commit"), ("git", "push"), ("git", "tag"), ("gh", "release", "create")):
		assert not simulado.ordenes(*prefijo), prefijo
	assert any("gh release create v2.3.0" in linea and "--latest" in linea for linea in lineas)
	assert any("la firma es válida" in linea for linea in lineas)
	assert any("no se ha copiado, subido ni creado nada" in linea for linea in lineas)


def test_ensayo_sigue_y_resume_los_problemas(proyecto):
	respuestas = respuestas_publicar(proyecto)
	respuestas[("git", "rev-parse", "--show-toplevel")] = Resultado(128, "", "fatal: not a git repository")
	lineas: list[str] = []
	publicador = publicar.Publicador(opciones(proyecto, ensayo=True), Simulado(respuestas), salida=lineas.append)
	assert publicador.publicar() == 1
	assert len(publicador.problemas) == 1
	assert any(linea.startswith("Paso 8 de 8") for linea in lineas)
	assert any("hay que resolver 1 problema" in linea for linea in lineas)


def test_publicacion_completa_con_git_y_gh_simulados(proyecto, monkeypatch):
	copias: list[str] = []
	original = publicar.copiar_verificando

	def copiar(origen, destino):
		copias.append(destino.name)
		return original(origen, destino)

	monkeypatch.setattr(publicar, "copiar_verificando", copiar)
	simulado = Simulado(respuestas_publicar(proyecto, **{"git|status": Resultado(0, " M docs/changelog.md\0")}))
	lineas: list[str] = []
	publicador = publicar.Publicador(opciones(proyecto, agente=True), simulado, salida=lineas.append)
	assert publicador.publicar() == 0
	# El ZIP primero y version.json al final, idénticos a los de build.
	assert copias == [proyecto.zip_.name, "version.json"]
	assert (proyecto.destino / proyecto.zip_.name).read_bytes() == proyecto.zip_.read_bytes()
	assert (proyecto.destino / "version.json").read_bytes() == (proyecto.raiz / "build" / "version.json").read_bytes()
	assert not list(proyecto.destino.glob("*.parcial"))
	# Commit (con coautoría, porque lo lanza un agente), push, etiqueta y release.
	indice = simulado.registro.index(simulado.ordenes("git", "commit")[0])
	assert simulado.entradas[indice].startswith("Ctrl+IA 2.3.0")
	assert subir_github.COAUTOR in simulado.entradas[indice]
	assert simulado.ordenes("git", "tag", "-a", "v2.3.0")
	assert simulado.ordenes("git", "push", "origin", "v2.3.0")
	creacion = simulado.ordenes("gh", "release", "create", "v2.3.0")[0]
	assert str(proyecto.zip_) in creacion and str(proyecto.raiz / "build" / "version.json") in creacion
	assert "--latest" in creacion and "--verify-tag" in creacion
	assert creacion[creacion.index("--title") + 1] == "Ctrl+IA 2.3.0"
	assert any("releases/latest/download/version.json" in linea for linea in lineas)
	# El código se sube antes de crear la release.
	assert simulado.registro.index(simulado.ordenes("git", "push", "--set-upstream")[0]) < simulado.registro.index(creacion)


def test_publicacion_reemplazando_sube_los_recursos_de_nuevo(proyecto):
	respuestas = respuestas_publicar(proyecto)
	respuestas[("gh", "release", "view", f"v{VERSION}", "--repo", "hxebolax/CtrlIA", "--json", "tagName")] = Resultado(0, "{}")
	respuestas[("git", "ls-remote")] = Resultado(0, "abc\trefs/tags/v2.3.0\n")
	simulado = Simulado(respuestas)
	publicador = publicar.Publicador(opciones(proyecto, reemplazar=True), simulado, salida=mudo)
	assert publicador.publicar() == 0
	assert simulado.ordenes("gh", "release", "upload", "v2.3.0")
	assert "--clobber" in simulado.ordenes("gh", "release", "upload")[0]
	assert "--latest" in simulado.ordenes("gh", "release", "edit")[0]
	assert not simulado.ordenes("gh", "release", "create")
	assert not simulado.ordenes("git", "tag")


def test_publicacion_para_si_la_release_no_queda_como_la_ultima(proyecto):
	simulado = Simulado(respuestas_publicar(proyecto, **{"gh|api": Resultado(0, "v2.2.0\n")}))
	publicador = publicar.Publicador(opciones(proyecto), simulado, salida=mudo)
	with pytest.raises(ErrorPublicacion, match="no ha quedado como la última"):
		publicador.publicar()


def test_la_firma_mala_detiene_la_publicacion_antes_de_copiar(proyecto):
	ruta = proyecto.raiz / "build" / "version.json"
	datos = json.loads(ruta.read_text(encoding="utf-8"))
	datos["novedades"] = "Cambiadas después de firmar."
	ruta.write_text(json.dumps(datos), encoding="utf-8")
	publicador = publicar.Publicador(opciones(proyecto), Simulado(respuestas_publicar(proyecto)), salida=mudo)
	with pytest.raises(ErrorPublicacion, match="firma NO es válida"):
		publicador.publicar()
	assert not (proyecto.destino / proyecto.zip_.name).exists()


def test_cancela_si_la_persona_no_confirma(proyecto):
	publicador = publicar.Publicador(
		opciones(proyecto, sin_confirmar=False), Simulado(respuestas_publicar(proyecto)), salida=mudo,
		preguntar=lambda _texto: "",
	)
	publicador.comprobaciones_previas()
	with pytest.raises(publicar.Cancelado):
		publicador.confirmar()


def test_destino_del_servidor_fuera_del_codigo(tmp_path, monkeypatch):
	"""La ruta personal del servidor no está en el código: variable de entorno o archivo local."""
	assert "SynologyDrive" not in (RAIZ / "tools" / "publicar.py").read_text(encoding="utf-8")
	local = tmp_path / "publicar.local.json"
	monkeypatch.delenv(publicar.VARIABLE_DESTINO, raising=False)
	destino, motivo = publicar.destino_configurado(local)
	assert destino is None and "publicar.local.json" in motivo
	local.write_text(json.dumps({"destino_servidor": str(tmp_path / "web")}), encoding="utf-8")
	assert publicar.destino_configurado(local) == (tmp_path / "web", "el archivo local publicar.local.json")
	monkeypatch.setenv(publicar.VARIABLE_DESTINO, str(tmp_path / "otra"))
	destino, motivo = publicar.destino_configurado(local)
	assert destino == tmp_path / "otra" and publicar.VARIABLE_DESTINO in motivo


def test_sin_destino_se_salta_el_servidor_y_publica_en_github(proyecto):
	simulado = Simulado(respuestas_publicar(proyecto))
	lineas: list[str] = []
	publicador = publicar.Publicador(opciones(proyecto, destino=None), simulado, salida=lineas.append)
	assert publicador.publicar() == 0
	assert not (proyecto.destino / proyecto.zip_.name).exists()
	assert any("Se salta este paso" in linea for linea in lineas)
	assert simulado.ordenes("gh", "release", "create", "v2.3.0")


def test_copiar_verificando(tmp_path, monkeypatch):
	origen = tmp_path / "a.zip"
	origen.write_bytes(b"contenido")
	destino = tmp_path / "servidor" / "a.zip"
	destino.parent.mkdir()
	assert publicar.copiar_verificando(origen, destino) is True
	assert destino.read_bytes() == b"contenido"
	assert publicar.copiar_verificando(origen, destino) is False  # Ya estaba idéntico.

	def copia_corrupta(desde, hasta):
		Path(hasta).write_bytes(b"contenidX")

	origen.write_bytes(b"contenido2")
	monkeypatch.setattr(publicar.shutil, "copyfile", copia_corrupta)
	with pytest.raises(ErrorPublicacion):
		publicar.copiar_verificando(origen, destino)
	assert destino.read_bytes() == b"contenido"  # El bueno anterior no se ha tocado.
	assert not list(destino.parent.glob("*.parcial"))
