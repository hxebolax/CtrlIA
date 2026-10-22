"""Incidencias de AIChat sobre Codex (#333, #338, #298, #302, #297, #305 y #173).

- Reglas «Permitir … con cualquier argumento durante esta sesión»: solo en la carpeta
  raíz, nunca con opciones destructivas de git ni órdenes encadenadas, ni con un
  repositorio anidado; la ayuda dice que la orden puede ir fuera del aislamiento.
- El «Permitir durante esta sesión» propio de Codex no se ofrece en las órdenes.
- Errores de un modelo retirado y «Continuar aquí» con un modelo que ya no existe.
- Aviso cuando el sandbox efectivo de Codex es más restrictivo que el pedido.
- «Lo que el agente sabe de ti»: habilidades, prompts y AGENTS.override.md de Codex,
  columna «Modificado» y «Ordenar por» recordado en la configuración.

Sin procesos de Codex (un servidor de mentira en memoria), sin red, sin ventanas
visibles y sin tocar los archivos reales del usuario.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from app.services import memoria
from app.services.agentes import codex
from app.services.agentes.base import AjustesSesion, ClaseOpcion, Evento, TipoEvento
from app.services.agentes.codex import (
	AYUDA_SESION_CODEX,
	MENSAJE_MODELO_NO_DISPONIBLE,
	MENSAJE_RESUMEN_FALLIDO,
	OPCION_PERMITIR_SESION,
	ErrorCodex,
	ProveedorCodex,
	orden_git_peligrosa,
	palabras_orden,
	prefijo_permitible,
	repositorio_sin_anidados,
)

# ── Ayudas ───────────────────────────────────────────────────────────────────


class ServidorFalso:
	"""Lo justo de ``_ServidorCodex`` para ``_asegurar_hilo``: respuestas fijas y registro."""

	def __init__(self, respuestas: dict[str, Any] | None = None, fallan: tuple[str, ...] = ()) -> None:
		self.respuestas = respuestas or {}
		self.fallan = fallan
		self.llamadas: list[tuple[str, Any]] = []
		self.muerto = False
		self.registrados: dict[str, Any] = {}

	def peticion(self, metodo: str, params: Any = None, tiempo: Any = None) -> Any:
		self.llamadas.append((metodo, params))
		if metodo in self.fallan:
			raise ErrorCodex("no disponible")
		respuesta = self.respuestas.get(metodo, {})
		return respuesta(params) if callable(respuesta) else respuesta

	def registrar(self, id_hilo: str, conversacion: Any) -> None:
		self.registrados[id_hilo] = conversacion

	def metodos(self) -> list[str]:
		return [metodo for metodo, _params in self.llamadas]

	def params(self, metodo: str) -> Any:
		return next(params for nombre, params in self.llamadas if nombre == metodo)


def _conversacion(carpeta: Path | PurePosixPath, servidor: ServidorFalso | None = None, **ajustes: Any):
	proveedor = ProveedorCodex(orden=["codex-que-no-existe"])
	if servidor is not None:
		proveedor.servidor = lambda extra=None: servidor  # type: ignore[method-assign]
	eventos: list[Evento] = []
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=carpeta, **ajustes), eventos.append)
	return conversacion, eventos


def _avisos(eventos: list[Evento]) -> list[Evento]:
	return [evento for evento in eventos if evento.tipo is TipoEvento.AVISO]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
	"""Carpeta de sesión que es la raíz de un repositorio git (vacío)."""
	carpeta = tmp_path / "proyecto"
	(carpeta / ".git").mkdir(parents=True)
	(carpeta / "src").mkdir()
	(carpeta / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
	return carpeta


# ── 1. Reglas de Ctrl+IA para Codex (#333, #338) ─────────────────────────────


def test_regla_solo_en_la_carpeta_raiz(repo):
	conversacion, _eventos = _conversacion(repo)
	conversacion.ajustes.reglas_permitidas.append("Orden(git commit)")
	peticion = {"command": "git commit -m x", "cwd": str(repo)}
	assert conversacion._regla_que_permite(peticion) == "Orden(git commit)"  # noqa: SLF001
	assert conversacion._regla_que_permite(dict(peticion, cwd=str(repo / "src"))) == ""  # noqa: SLF001
	assert conversacion._regla_que_permite(dict(peticion, cwd=str(repo.parent))) == ""  # noqa: SLF001
	# Una orden que no es git no necesita repositorio, pero sigue valiendo solo en la raíz.
	conversacion.ajustes.reglas_permitidas.append("Orden(npm test)")
	npm = {"command": "npm test -- --watch=false", "cwd": str(repo)}
	assert conversacion._regla_que_permite(npm) == "Orden(npm test)"  # noqa: SLF001
	assert conversacion._regla_que_permite(dict(npm, cwd=str(repo / "src"))) == ""  # noqa: SLF001


@pytest.mark.parametrize("orden", [
	"git push --force", "git push -f origin main", "git push -fu origin main", "git push origin +main",
	"git push --force-with-lease", "git push --force-with-lease=main:abc", "git push --delete origin rama",
	"git push -d origin rama", "git push origin :rama", "git push --mirror", "git push --prune origin",
	"git push ./otro HEAD", "git push C:\\otro HEAD", "git push https://ejemplo.invalid/r.git", "git fetch ./malo",
	"git fetch origin +refs/heads/*:refs/remotes/origin/*", "git -c core.hooksPath=x push", "git -C otra push",
	"git --git-dir=x push", "git push --repo=./x", "git push --receive-pack=malo origin",
])
def test_opciones_destructivas_de_git_siempre_preguntan(repo, orden):
	conversacion, _eventos = _conversacion(repo)
	conversacion.ajustes.reglas_permitidas.append("Orden(git push)")
	conversacion.ajustes.reglas_permitidas.append("Orden(git fetch)")
	palabras = palabras_orden(orden)
	assert palabras is None or orden_git_peligrosa(palabras), orden
	assert conversacion._regla_que_permite({"command": orden, "cwd": str(repo)}) == "", orden  # noqa: SLF001


@pytest.mark.parametrize("orden", [
	"git push; del x", "git push & calc", "git push | more", "git push `x`", "git push $(x)", "git push > a.txt",
	"git push < a.txt", "git push\ncalc", "git push\r\ncalc", "git push && calc", "git push || calc",
])
def test_ordenes_encadenadas_siempre_preguntan(repo, orden):
	conversacion, _eventos = _conversacion(repo)
	conversacion.ajustes.reglas_permitidas.append("Orden(git push)")
	assert palabras_orden(orden) is None, orden
	assert conversacion._regla_que_permite({"command": orden, "cwd": str(repo)}) == "", orden  # noqa: SLF001


@pytest.mark.parametrize("orden", ["git push", "git push origin main", "git push -u origin feature/x",
	"git push --set-upstream origin main", 'git commit -m "arreglo del menú"', "git branch nueva"])
def test_ordenes_git_normales_siguen_valiendo(repo, orden):
	conversacion, _eventos = _conversacion(repo)
	conversacion.ajustes.reglas_permitidas.extend(["Orden(git push)", "Orden(git commit)", "Orden(git branch)"])
	assert not orden_git_peligrosa(palabras_orden(orden)), orden
	assert conversacion._regla_que_permite({"command": orden, "cwd": str(repo)}), orden  # noqa: SLF001


def test_git_borrar_rama_y_subordenes_peligrosas():
	for orden in ("git branch -d vieja", "git branch -D vieja", "git tag -d v1", "git config core.fsmonitor x",
			"git submodule foreach calc", "git rebase -x calc main", "git rebase --exec calc main", "git remote add x y",
			"git reset --hard", "git clean -fd", "git diff --output=C:\\x.txt"):
		assert orden_git_peligrosa(palabras_orden(orden)), orden
	# Prefijos: «git» a secas, opciones globales o subórdenes peligrosas nunca se ofrecen.
	for prefijo in (["git"], ["git", "-C"], ["git", "config"], ["git.exe", "submodule"], ["git", "push", "--force"]):
		assert not prefijo_permitible(prefijo), prefijo
	assert prefijo_permitible(["git", "push"]) and prefijo_permitible(["npm", "test"])


def test_repositorio_anidado_o_sin_git_no_casa(repo, tmp_path):
	conversacion, _eventos = _conversacion(repo)
	conversacion.ajustes.reglas_permitidas.append("Orden(git status)")
	peticion = {"command": "git status", "cwd": str(repo)}
	assert conversacion._regla_que_permite(peticion)  # noqa: SLF001
	# Otro repositorio dentro (carpeta .git o archivo .git de un submódulo): se pregunta.
	(repo / "vendor" / "lib" / ".git").mkdir(parents=True)
	assert not repositorio_sin_anidados(repo)
	assert conversacion._regla_que_permite(peticion) == ""  # noqa: SLF001
	(repo / "vendor" / "lib" / ".git").rmdir()
	(repo / "vendor" / "lib" / ".GIT").write_text("gitdir: ../../.git/modules/lib\n", encoding="utf-8")
	assert not repositorio_sin_anidados(repo)
	(repo / "vendor" / "lib" / ".GIT").unlink()
	assert repositorio_sin_anidados(repo)
	# Demasiados archivos para revisarlos: ante la duda, se pregunta.
	assert not repositorio_sin_anidados(repo, limite=2)
	# La carpeta de la sesión no es la raíz de un repositorio (git usaría el de una carpeta superior).
	sin_git = tmp_path / "sin_git"
	sin_git.mkdir()
	otra, _eventos = _conversacion(sin_git)
	otra.ajustes.reglas_permitidas.append("Orden(git status)")
	assert otra._regla_que_permite({"command": "git status", "cwd": str(sin_git)}) == ""  # noqa: SLF001
	(sin_git / ".git").write_text("gitdir: C:/otro/.git\n", encoding="utf-8")  # un .git que es archivo: tampoco
	assert otra._regla_que_permite({"command": "git status", "cwd": str(sin_git)}) == ""  # noqa: SLF001


def test_en_otro_equipo_git_siempre_pregunta():
	carpeta = "/home/ana/web"
	extra = {"remoto": {"equipo": "mac", "nombre": "Mac", "carpeta": carpeta}}
	conversacion, _eventos = _conversacion(PurePosixPath(carpeta), extra=extra)
	conversacion.ajustes.reglas_permitidas.extend(["Orden(git status)", "Orden(npm test)"])
	assert conversacion._regla_que_permite({"command": "git status", "cwd": carpeta}) == ""  # noqa: SLF001
	assert conversacion._regla_que_permite({"command": "npm test", "cwd": carpeta}) == "Orden(npm test)"  # noqa: SLF001
	assert conversacion._regla_que_permite({"command": "npm test", "cwd": carpeta + "/sub"}) == ""  # noqa: SLF001


def test_no_se_ofrece_la_regla_fuera_de_la_raiz_ni_con_opciones_destructivas(repo):
	conversacion, _eventos = _conversacion(repo)
	base = {"command": "git push origin main", "cwd": str(repo), "proposedExecpolicyAmendment": ["git", "push"]}
	ids = [o.id for o in conversacion._solicitud_orden(1, base).opciones]  # noqa: SLF001
	assert OPCION_PERMITIR_SESION in ids
	for cambio in ({"cwd": str(repo / "src")}, {"command": "git push --force origin main"},
			{"command": "git push origin +main"}):
		solicitud = conversacion._solicitud_orden(2, dict(base, **cambio))  # noqa: SLF001
		assert OPCION_PERMITIR_SESION not in [o.id for o in solicitud.opciones], cambio
		assert "regla" not in solicitud.datos


def test_la_ayuda_dice_que_la_orden_va_fuera_del_aislamiento(repo):
	peticion = {"command": "git push origin main", "cwd": str(repo), "proposedExecpolicyAmendment": ["git", "push"]}
	conversacion, _eventos = _conversacion(repo)
	solicitud = conversacion._solicitud_orden(1, peticion)  # noqa: SLF001
	propia = next(o for o in solicitud.opciones if o.id == OPCION_PERMITIR_SESION)
	assert "fuera de su aislamiento (sandbox)" in propia.ayuda
	assert "carpeta raíz" in propia.ayuda and "--force" in propia.ayuda
	assert "sigue la configuración del repositorio" in propia.ayuda
	assert "fuera del aislamiento" in solicitud.detalle
	# En «Acceso total», Codex no aísla nada: se dice tal cual.
	total, _eventos = _conversacion(repo, modo_permiso="acceso_total")
	solicitud = total._solicitud_orden(1, peticion)  # noqa: SLF001
	propia = next(o for o in solicitud.opciones if o.id == OPCION_PERMITIR_SESION)
	assert "ejecuta todas las órdenes sin aislamiento" in propia.ayuda
	assert "sin aislamiento" in solicitud.detalle


# ── 1 bis. «Permitir durante esta sesión» de Codex (#298) ────────────────────


def test_permitir_durante_la_sesion_de_codex_no_se_ofrece_en_ordenes(repo):
	conversacion, _eventos = _conversacion(repo)
	red = {"command": "curl ejemplo.invalid", "cwd": str(repo), "networkApprovalContext": {"host": "ejemplo.invalid"},
		"availableDecisions": ["accept", "acceptForSession", "decline", "cancel"]}
	ids = [o.id for o in conversacion._solicitud_orden(1, red).opciones]  # noqa: SLF001
	assert ids == ["accept", "decline", "cancel"]
	legado = codex._solicitud_legado(2, "execCommandApproval", {"command": ["dir"], "cwd": str(repo)})  # noqa: SLF001
	assert "acceptForSession" not in [o.id for o in legado.opciones]


def test_permitir_durante_la_sesion_en_archivos_avisa_de_que_dura_aunque_cambies_de_modo(repo):
	conversacion, _eventos = _conversacion(repo)
	archivos = conversacion._solicitud_archivos(1, {"itemId": "x", "availableDecisions": ["accept", "acceptForSession", "decline"]})  # noqa: SLF001
	sesion = next(o for o in archivos.opciones if o.id == "acceptForSession")
	assert sesion.clase is ClaseOpcion.PERMITIR_SESION and sesion.ayuda == AYUDA_SESION_CODEX
	assert "aunque mientras tanto cambies a un modo de permisos más restrictivo" in sesion.ayuda
	parche = codex._solicitud_legado(2, "applyPatchApproval", {"fileChanges": {"a.txt": {}}})  # noqa: SLF001
	assert next(o for o in parche.opciones if o.id == "acceptForSession").ayuda == AYUDA_SESION_CODEX
	permisos = codex._solicitud_permisos(3, {"permissions": {"network": {"enabled": True}}})  # noqa: SLF001
	assert next(o for o in permisos.opciones if o.id == "session").ayuda == AYUDA_SESION_CODEX


# ── 2. Modelo retirado (#302) ────────────────────────────────────────────────


def test_error_de_modelo_retirado_en_espanol():
	original = ('Error running remote compact task: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
		'"message":"The \'gpt-5.5\' model is not supported when using Codex with a ChatGPT account."}}')
	assert codex._texto_error({"message": original}) == MENSAJE_MODELO_NO_DISPONIBLE  # noqa: SLF001
	assert "Ctrl+Mayús+O" in MENSAJE_MODELO_NO_DISPONIBLE and "Opciones de la sesión" in MENSAJE_MODELO_NO_DISPONIBLE
	assert codex._texto_error({"message": "Unknown model gpt-5.5 is used"}) == MENSAJE_MODELO_NO_DISPONIBLE  # noqa: SLF001
	resumen = codex._texto_error({"message": "Error running remote compact task: stream closed before completion"})  # noqa: SLF001
	assert resumen == MENSAJE_RESUMEN_FALLIDO and "Ctrl+Mayús+O" in resumen
	assert codex._texto_error({"message": "x", "additionalDetails": "model is not supported"}) == MENSAJE_MODELO_NO_DISPONIBLE  # noqa: SLF001
	# Lo demás no cambia.
	assert "límite de uso" in codex._texto_error({"message": "x", "codexErrorInfo": "usageLimitExceeded"})  # noqa: SLF001
	assert codex._texto_error({"message": "otra cosa"}) == "Codex ha devuelto un error: otra cosa"  # noqa: SLF001


_MODELOS = {"data": [
	{"model": "gpt-6-mini", "isDefault": False, "hidden": False},
	{"model": "gpt-6", "isDefault": True, "hidden": False},
	{"model": "oculto", "isDefault": False, "hidden": True},
]}


def _respuestas(modelo_hilo: str = "gpt-6", sandbox: str = "workspaceWrite") -> dict[str, Any]:
	hilo = {"thread": {"id": "hilo-nuevo"}, "model": modelo_hilo, "sandbox": {"type": sandbox}}
	return {"model/list": _MODELOS, "thread/fork": hilo, "thread/resume": hilo, "thread/start": hilo}


def test_continuar_aqui_no_propone_un_modelo_retirado(tmp_path):
	servidor = ServidorFalso(_respuestas())
	conversacion, eventos = _conversacion(tmp_path, servidor, bifurcar_de="hilo-viejo", modelo="gpt-5.5")
	conversacion._asegurar_hilo()  # noqa: SLF001
	assert servidor.metodos()[:2] == ["model/list", "thread/fork"]
	assert servidor.params("model/list") == {"includeHidden": True}
	assert servidor.params("thread/fork")["model"] == "gpt-6"  # el predeterminado de la lista, no el retirado
	assert conversacion.ajustes.modelo == "gpt-6"
	aviso = [e for e in _avisos(eventos) if e.datos.get("modelo_sustituido")]
	assert aviso and "gpt-5.5" in aviso[0].texto and "gpt-6" in aviso[0].texto and "Ctrl+Mayús+O" in aviso[0].texto


def test_continuar_aqui_sin_modelo_cuando_la_original_usaba_uno_retirado(tmp_path):
	servidor = ServidorFalso(_respuestas(modelo_hilo="gpt-5.5"))
	conversacion, eventos = _conversacion(tmp_path, servidor, bifurcar_de="hilo-viejo")
	conversacion._asegurar_hilo()  # noqa: SLF001
	assert "model" not in servidor.params("thread/fork")
	assert conversacion.ajustes.modelo == "gpt-6"  # el siguiente turn/start lo pedirá
	assert next(e for e in eventos if e.tipo is TipoEvento.INICIADO).datos["modelo"] == "gpt-6"
	assert any("resumirá el historial" in e.texto for e in _avisos(eventos))


def test_continuar_aqui_con_modelo_vigente_o_sin_lista_no_cambia_nada(tmp_path):
	for modelo, fallan in (("gpt-6-mini", ()), ("oculto", ()), ("gpt-5.5", ("model/list",))):
		servidor = ServidorFalso(_respuestas(modelo_hilo=modelo or "gpt-6"), fallan=fallan)
		conversacion, eventos = _conversacion(tmp_path, servidor, bifurcar_de="hilo-viejo", modelo=modelo)
		conversacion._asegurar_hilo()  # noqa: SLF001
		assert servidor.params("thread/fork")["model"] == modelo
		assert conversacion.ajustes.modelo == modelo
		assert not [e for e in _avisos(eventos) if e.datos.get("modelo_sustituido")]


def test_una_sesion_nueva_no_consulta_la_lista_de_modelos(tmp_path):
	servidor = ServidorFalso(_respuestas())
	conversacion, _eventos = _conversacion(tmp_path, servidor, modelo="lo-que-sea")
	conversacion._asegurar_hilo()  # noqa: SLF001
	assert "model/list" not in servidor.metodos()
	assert servidor.params("thread/start")["model"] == "lo-que-sea"


# ── 3. Sandbox efectivo más restrictivo que el pedido (#297) ─────────────────


def test_aviso_si_codex_rebaja_a_solo_lectura(tmp_path, monkeypatch):
	monkeypatch.setattr(codex, "ES_WINDOWS", True)
	respuestas = _respuestas(sandbox="readOnly")
	respuestas["windowsSandbox/readiness"] = {"status": "notConfigured"}
	servidor = ServidorFalso(respuestas)
	conversacion, eventos = _conversacion(tmp_path, servidor, modo_permiso="espacio_trabajo")
	conversacion._asegurar_hilo()  # noqa: SLF001
	avisos = [e for e in _avisos(eventos) if e.datos.get("sandbox_efectivo")]
	assert len(avisos) == 1
	assert "solo lectura" in avisos[0].texto and "Trabajar en la carpeta del proyecto" in avisos[0].texto
	assert "no está configurado" in avisos[0].texto
	assert avisos[0].datos["sandbox_pedido"] == "workspaceWrite"
	assert servidor.metodos().count("windowsSandbox/readiness") == 1
	# Una vez por sesión: ni otra notificación ni otro arranque lo repiten.
	conversacion._al_notificacion("thread/settings/updated", {"threadSettings": {"sandboxPolicy": {"type": "readOnly"}}})  # noqa: SLF001
	conversacion._comprobar_sandbox({"type": "readOnly"}, codex._modo("espacio_trabajo"), servidor)  # noqa: SLF001
	assert len([e for e in _avisos(eventos) if e.datos.get("sandbox_efectivo")]) == 1


def test_sin_aviso_si_el_sandbox_es_el_pedido_o_mas_amplio(tmp_path, monkeypatch):
	monkeypatch.setattr(codex, "ES_WINDOWS", True)
	for modo, sandbox in (("espacio_trabajo", "workspaceWrite"), ("solo_lectura", "readOnly"),
			("solo_lectura", "workspaceWrite"), ("acceso_total", "dangerFullAccess"), ("espacio_trabajo", "externalSandbox")):
		servidor = ServidorFalso(_respuestas(sandbox=sandbox))
		conversacion, eventos = _conversacion(tmp_path, servidor, modo_permiso=modo)
		conversacion._asegurar_hilo()  # noqa: SLF001
		assert not [e for e in _avisos(eventos) if e.datos.get("sandbox_efectivo")], (modo, sandbox)
		assert "windowsSandbox/readiness" not in servidor.metodos()


def test_aviso_por_la_notificacion_sin_preguntar_desde_el_hilo_lector(tmp_path):
	conversacion, eventos = _conversacion(tmp_path, modo_permiso="acceso_total")
	conversacion._al_notificacion("thread/settings/updated", {"threadSettings": {"sandboxPolicy": {"type": "workspaceWrite"}}})  # noqa: SLF001
	avisos = [e for e in _avisos(eventos) if e.datos.get("sandbox_efectivo")]
	assert len(avisos) == 1 and "escritura en la carpeta del proyecto" in avisos[0].texto
	assert avisos[0].datos["aislamiento_windows"] == ""
	# Lo que dice Codex manda para la ayuda de las reglas: ya no es «sin aislamiento».
	assert not conversacion._sin_aislamiento()  # noqa: SLF001


# ── 4. «Lo que el agente sabe de ti» (#305 y #173) ───────────────────────────


@pytest.fixture
def casa(tmp_path, monkeypatch):
	casa = tmp_path / "casa"
	casa.mkdir()
	monkeypatch.setenv("HOME", str(casa))
	monkeypatch.setenv("USERPROFILE", str(casa))
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config_claude"))
	monkeypatch.delenv("CODEX_HOME", raising=False)
	proyecto = tmp_path / "proyecto"
	proyecto.mkdir()
	return {"casa": casa, "proyecto": proyecto, "tmp": tmp_path}


def _escribir(ruta: Path, texto: str = "contenido\n", fecha: float | None = None) -> Path:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	ruta.write_text(texto, encoding="utf-8")
	if fecha is not None:
		os.utime(ruta, (fecha, fecha))
	return ruta


def test_codex_habilidades_prompts_y_override(casa, monkeypatch):
	raiz, proyecto = casa["casa"] / ".codex", casa["proyecto"]
	_escribir(raiz / "skills" / "pdf" / "SKILL.md")
	_escribir(casa["casa"] / ".agents" / "skills" / "web" / "SKILL.md")
	_escribir(proyecto / ".agents" / "skills" / "pruebas" / "SKILL.md")
	_escribir(raiz / "prompts" / "revisar.md")
	_escribir(raiz / "AGENTS.override.md")
	_escribir(proyecto / "AGENTS.override.md")
	archivos = [a for a in memoria.archivos_memoria(proyecto, primero="codex") if a.agente == memoria.CODEX]
	por_ruta = {a.ruta: a for a in archivos}
	assert por_ruta[raiz / "skills" / "pdf" / "SKILL.md"].nombre == "Habilidad: pdf"
	assert por_ruta[raiz / "skills" / "pdf" / "SKILL.md"].tipo == memoria.HABILIDAD
	assert por_ruta[casa["casa"] / ".agents" / "skills" / "web" / "SKILL.md"].ambito == memoria.GLOBAL
	assert por_ruta[proyecto / ".agents" / "skills" / "pruebas" / "SKILL.md"].ambito == memoria.PROYECTO
	prompt = por_ruta[raiz / "prompts" / "revisar.md"]
	assert prompt.nombre == "Comando: /prompts:revisar" and prompt.tipo == memoria.COMANDO
	assert "AGENTS.override.md" in por_ruta[raiz / "AGENTS.override.md"].nombre
	assert por_ruta[proyecto / "AGENTS.override.md"].ambito == memoria.PROYECTO
	assert all(a.existe and a.modificado for a in archivos if a.ruta.name != "config.toml" and a.ruta.name != "AGENTS.md")
	# Sin esos archivos, no aparecen (no son «archivos que faltan»); y se respeta CODEX_HOME.
	otra = casa["tmp"] / "otra_codex"
	_escribir(otra / "prompts" / "otro.md")
	monkeypatch.setenv("CODEX_HOME", str(otra))
	archivos = [a for a in memoria.archivos_memoria(None, primero="codex") if a.agente == memoria.CODEX]
	nombres = [a.nombre for a in archivos]
	assert "Comando: /prompts:otro" in nombres and "Comando: /prompts:revisar" not in nombres
	assert not any("override" in n for n in nombres)
	assert next(a for a in archivos if a.ruta.name == "AGENTS.md").ruta == otra / "AGENTS.md"
	assert "Habilidad: web" in nombres  # ~/.agents/skills no depende de CODEX_HOME


def _archivo(nombre: str, modificado: float | None, agente: str = memoria.CODEX) -> memoria.ArchivoMemoria:
	return memoria.ArchivoMemoria(nombre, Path(nombre), memoria.GLOBAL, agente, modificado is not None, False,
		memoria.INSTRUCCIONES, modificado)


def test_ordenar_por_fecha_y_nombre_con_empates_y_sin_fecha():
	archivos = [_archivo("b", 200), _archivo("Ámbito", None), _archivo("a", 100), _archivo("c", 200), _archivo("zeta", 300)]
	nombres = lambda lista: [a.nombre for a in lista]  # noqa: E731
	assert nombres(memoria.ordenar(archivos, memoria.ORDEN_RECIENTES)) == ["zeta", "b", "c", "a", "Ámbito"]
	assert nombres(memoria.ordenar(archivos, memoria.ORDEN_ANTIGUOS)) == ["a", "b", "c", "zeta", "Ámbito"]
	assert nombres(memoria.ordenar(archivos, memoria.ORDEN_NOMBRE)) == ["a", "Ámbito", "b", "c", "zeta"]
	assert nombres(memoria.ordenar(archivos, memoria.ORDEN_AGENTE)) == nombres(archivos)
	assert nombres(memoria.ordenar(archivos, "otro")) == nombres(archivos)
	assert memoria.fecha_modificacion(Path("no-existe-en-ningun-sitio.md")) is None


def test_fecha_de_modificacion_de_los_archivos(casa):
	ruta = _escribir(casa["casa"] / ".codex" / "AGENTS.md", fecha=1_700_000_000)
	archivos = memoria.archivos_memoria(None, primero="codex")
	agents = next(a for a in archivos if a.ruta == ruta)
	assert agents.modificado == pytest.approx(1_700_000_000)
	assert next(a for a in archivos if a.ruta.name == "config.toml").modificado is None  # no existe


@pytest.fixture
def dialogo_memoria(app_wx, casa, monkeypatch, tmp_path):
	"""Fábrica del diálogo sin mostrarlo, con la voz y los avisos registrados."""
	from app.core.configuracion import Configuracion
	from app.ui import avisos
	from app.ui.accesibilidad import voz
	from app.ui.dialogos import memoria as mod_memoria

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, interrumpir=False: dichos.append(texto))
	monkeypatch.setattr(voz, "hablar", lambda texto, *a, **k: dichos.append(texto))
	configuracion = Configuracion(ruta=tmp_path / "config" / "settings.json")
	creados = []

	def crear():
		dialogo = mod_memoria.DialogoMemoria(None, casa["proyecto"], "codex", configuracion=configuracion)
		creados.append(dialogo)
		return dialogo

	yield crear, configuracion, dichos
	for dialogo in creados:
		dialogo.Destroy()


def test_dialogo_columna_modificado_y_ordenar_conserva_la_seleccion(dialogo_memoria, casa):
	crear, configuracion, dichos = dialogo_memoria
	viejo = _escribir(casa["casa"] / ".codex" / "skills" / "vieja" / "SKILL.md", fecha=1_600_000_000)
	nuevo = _escribir(casa["casa"] / ".codex" / "prompts" / "nuevo.md", fecha=1_900_000_000)
	dialogo = crear()
	assert dialogo.orden == "agente"
	columnas = [dialogo.lista.GetColumn(i).GetText() for i in range(dialogo.lista.GetColumnCount())]
	assert columnas == ["Archivo", "Agente", "Ámbito", "Existe", "Modificado", "Ruta"]
	fila_vieja = next(i for i, a in enumerate(dialogo.archivos) if a.ruta == viejo)
	assert dialogo.lista.texto_celda(fila_vieja, 4)  # con fecha
	inexistente = next(i for i, a in enumerate(dialogo.archivos) if not a.existe)
	assert dialogo.lista.texto_celda(inexistente, 4) == ""
	assert "ordenar la lista" in dialogo.lista.GetHelpText() and "última modificación" in dialogo.lista.GetHelpText()

	dialogo.lista.enfocar_fila(fila_vieja, anunciar=False)
	dialogo.ordenar_por("recientes")
	assert dialogo.archivos[0].ruta == nuevo
	assert dialogo.seleccionado().ruta == viejo  # sigue en el mismo archivo
	assert dichos[-1] == "Ordenado: más recientes primero"
	assert configuracion.obtener("memoria.orden") == "recientes"
	existen = [a.existe for a in dialogo.archivos]
	assert existen == sorted(existen, reverse=True)  # los que no existen, al final

	dialogo.ordenar_por("antiguos")
	assert dialogo.archivos[0].ruta == viejo and dialogo.seleccionado().ruta == viejo
	dialogo.ordenar_por("nombre")
	nombres = [a.nombre for a in dialogo.archivos]
	assert nombres == sorted(nombres, key=memoria._clave_nombre)  # noqa: SLF001
	dialogo.ordenar_por("no-vale")  # se ignora
	assert dialogo.orden == "nombre"
	# Se recuerda: el diálogo siguiente empieza con el mismo orden.
	assert crear().orden == "nombre"


def test_menu_contextual_ordenar_por(dialogo_memoria):
	import wx

	crear, configuracion, _dichos = dialogo_memoria
	configuracion.establecer("memoria.orden", "antiguos")
	dialogo = crear()
	menu = dialogo._crear_menu()  # noqa: SLF001
	try:
		elementos = list(menu.GetMenuItems())
		ordenar = elementos[-1]
		assert ordenar.GetItemLabel() == "&Ordenar por" and ordenar.GetSubMenu() is not None
		submenu = ordenar.GetSubMenu()
		opciones = [(e.GetItemLabel(), e.IsChecked()) for e in submenu.GetMenuItems()]
		assert opciones == [("Por &agente", False), ("Más &recientes primero", False), ("Más an&tiguos primero", True),
			("Por &nombre", False)]
		# Teclas Alt sin repetir en el menú y en el submenú.
		for lista in ([e.GetItemLabel() for e in elementos if not e.IsSeparator()], [o[0] for o in opciones]):
			letras = [texto[texto.index("&") + 1].lower() for texto in lista]
			assert len(letras) == len(set(letras)), lista
		# Elegir una opción la aplica y la recuerda.
		elegida = submenu.GetMenuItems()[3]
		evento = wx.CommandEvent(wx.wxEVT_MENU, elegida.GetId())
		submenu.ProcessEvent(evento)
		assert dialogo.orden == "nombre" and configuracion.obtener("memoria.orden") == "nombre"
	finally:
		menu.Destroy()
