"""Pruebas del paquete HERRAMIENTAS de Ctrl+IA 1.6.0.

- Actualizar los agentes: versión disponible con npm (caché) y orden de actualización.
- «Lo que el agente sabe de ti»: editar con copia previa, memorias de Claude Code y subagentes.
- Cambios sin confirmar en git y página «Aprobaciones» de «Listas de la sesión».
- Nueva sesión: instrucciones y carpetas, carpeta base de proyectos y copia aislada (desde origin o una rama
  que ya existe); etiquetas sin «&» con su control desactivado.
- Accesibilidad: etiquetas atadas a su control y ListBox que no se lee dos veces (MSAA).
- Command Code entre los agentes ACP conocidos.

Los servicios se prueban sin wx con ``tmp_path`` y repositorios git de prueba; los diálogos con la wx.App única,
sin mostrarlos. Nada sale a Internet: npm y git remoto se simulan o son carpetas locales.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from app.services import extraccion, memoria
from app.services import git as servicio_git
from app.services.agentes import actualizacion, acp, acp_conocidos
from app.services.agentes.base import Capacidades, EstadoProveedor, OpcionLista
from app.services.gestor import MensajeVista

wx = pytest.importorskip("wx")


# ════════════════════════════════════════════════════════════════════════════
# Ayudas
# ════════════════════════════════════════════════════════════════════════════


def _paquete(raiz: Path, nombre: str, version: str = "1.0.0", binario: str = "") -> Path:
	carpeta = raiz.joinpath("node_modules", *nombre.split("/"))
	carpeta.mkdir(parents=True, exist_ok=True)
	datos = {"name": nombre, "version": version}
	if binario:
		datos["bin"] = {binario: "dist/index.js"}
		(carpeta / "dist").mkdir(exist_ok=True)
		(carpeta / "dist" / "index.js").write_text("// programa", encoding="utf-8")
	(carpeta / "package.json").write_text(json.dumps(datos), encoding="utf-8")
	return carpeta


_ENTORNO_GIT = {
	"GIT_AUTHOR_NAME": "Prueba", "GIT_AUTHOR_EMAIL": "prueba@ejemplo.invalid",
	"GIT_COMMITTER_NAME": "Prueba", "GIT_COMMITTER_EMAIL": "prueba@ejemplo.invalid",
	"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
}


def _git(carpeta: Path, *argumentos: str) -> str:
	resultado = subprocess.run(
		["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
			"-C", str(carpeta), *argumentos],
		capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, **_ENTORNO_GIT), check=False,
		creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
	)
	assert resultado.returncode == 0, resultado.stderr
	return resultado.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
	"""Repositorio con un commit (a.txt de tres líneas, b.txt y viejo.txt)."""
	if servicio_git.buscar_git() is None:
		pytest.skip("No hay git")
	carpeta = tmp_path / "repo"
	carpeta.mkdir()
	_git(carpeta, "init", "-q")
	(carpeta / "a.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
	(carpeta / "b.txt").write_text("borrar\n", encoding="utf-8")
	(carpeta / "sub").mkdir()
	(carpeta / "sub" / "viejo.txt").write_text("contenido que se mueve\nsegunda\n", encoding="utf-8")
	_git(carpeta, "add", "-A")
	_git(carpeta, "commit", "-q", "-m", "inicio")
	return carpeta


@pytest.fixture
def efectos(monkeypatch: pytest.MonkeyPatch) -> dict:
	"""Diálogos y consolas simulados en los módulos de la interfaz del paquete."""
	from app.services.agentes import procesos
	from app.ui.dialogos import agentes as mod_agentes
	from app.ui.dialogos import memoria as mod_memoria
	from app.ui.dialogos import nueva_sesion as mod_nueva

	datos: dict = {"confirmar": [], "mensaje": [], "error": [], "consola": [], "respuesta": wx.ID_YES}

	def confirmar(_padre, texto, titulo="", **_opciones):
		datos["confirmar"].append(texto)
		return datos["respuesta"]

	for modulo in (mod_agentes, mod_memoria, mod_nueva):
		monkeypatch.setattr(modulo, "confirmar", confirmar, raising=False)
		monkeypatch.setattr(modulo, "mostrar_error", lambda _p, texto, titulo="": datos["error"].append(texto), raising=False)
	for modulo in (mod_agentes, mod_memoria):
		monkeypatch.setattr(modulo, "mostrar_mensaje", lambda _p, texto, titulo="": datos["mensaje"].append(texto))
	monkeypatch.setattr(procesos, "abrir_en_consola", lambda orden, carpeta=None, al_terminar=None: datos["consola"].append(list(orden)) or True)
	return datos


def _letras(ventana: wx.Window) -> list[str]:
	"""Letras Alt de las etiquetas y botones visibles y activos de una ventana (sin repetir debería)."""
	letras = []
	pendientes = list(ventana.GetChildren())
	while pendientes:
		hijo = pendientes.pop(0)
		pendientes.extend(hijo.GetChildren())
		if not isinstance(hijo, (wx.StaticText, wx.Button, wx.CheckBox)) or not hijo.IsShown():
			continue
		texto = hijo.GetLabel().replace("&&", "")
		if "&" in texto:
			letras.append(texto.split("&", 1)[1][:1].lower())
	return letras


# ════════════════════════════════════════════════════════════════════════════
# 1. Actualizar los agentes
# ════════════════════════════════════════════════════════════════════════════


def test_paquete_npm_de_la_ruta(tmp_path: Path) -> None:
	global_npm = tmp_path / "npm"
	codex = _paquete(global_npm, "@openai/codex", "0.160.0")
	anidado = _paquete(codex, "@openai/codex-win32-x64", "0.160.0")
	exe = anidado / "vendor" / "codex.exe"
	exe.parent.mkdir(parents=True)
	exe.write_bytes(b"MZ")
	assert actualizacion.paquete_npm_de(exe) == "@openai/codex"  # el más externo, el que se instaló
	sencillo = _paquete(global_npm, "opencode-ai")
	assert actualizacion.paquete_npm_de(sencillo / "bin" / "opencode.js") == "opencode-ai"
	assert actualizacion.paquete_npm_de(tmp_path / "otra" / "claude.exe") == ""
	# Un package.json con un nombre raro no se usa (iría dentro de una orden de cmd.exe).
	raro = global_npm / "node_modules" / "raro"
	raro.mkdir(parents=True)
	(raro / "package.json").write_text(json.dumps({"name": "raro & del C:\\"}), encoding="utf-8")
	assert actualizacion.paquete_npm_de(raro / "x.js") == ""


def test_forma_de_actualizar(tmp_path: Path) -> None:
	paquete = _paquete(tmp_path / "npm", "@agentclientprotocol/codex-acp")
	npm = actualizacion.forma_de_actualizar("codex-acp", EstadoProveedor(True, ruta=str(paquete / "dist" / "index.js")))
	assert npm is not None and npm.origen == actualizacion.ORIGEN_NPM and npm.se_puede_consultar
	assert npm.orden == ("cmd.exe", "/k", "npm install -g @agentclientprotocol/codex-acp@latest")
	claude = actualizacion.forma_de_actualizar("claude", EstadoProveedor(True, ruta="C:\\Users\\O'Neil\\.local\\bin\\claude.exe"))
	assert claude is not None and claude.texto_orden == "claude update" and not claude.se_puede_consultar
	assert claude.orden[0] == "powershell.exe"
	assert "& 'C:\\Users\\O''Neil\\.local\\bin\\claude.exe' 'update'" in claude.orden[-1]  # la comilla se dobla
	agy = actualizacion.forma_de_actualizar("antigravity", EstadoProveedor(True, ruta="C:\\agy\\bin\\agy.exe"))
	assert agy is not None and agy.texto_orden == "agy update"
	assert actualizacion.forma_de_actualizar("goose", EstadoProveedor(True, ruta="C:\\bin\\goose.exe")) is None
	assert actualizacion.forma_de_actualizar("ollama", EstadoProveedor(True, ruta="http://127.0.0.1:11434")) is None
	assert actualizacion.forma_de_actualizar("claude", EstadoProveedor(False)) is None
	assert actualizacion.forma_de_actualizar("claude", None) is None


def test_versiones_y_npm_view() -> None:
	assert actualizacion.hay_version_nueva("codex-cli 0.163.0", "0.164.1") is True
	assert actualizacion.hay_version_nueva("2.1.308 (Claude Code)", "2.1.308") is False
	assert actualizacion.hay_version_nueva("", "1.0.0") is None
	assert actualizacion.hay_version_nueva("1.0.0", "1.1.0-beta.1") is None
	ordenes = []

	def ejecutar(respuesta, codigo=0, error=""):
		def funcion(orden, tiempo):
			ordenes.append(list(orden))
			return codigo, respuesta, error
		return funcion

	npm = ["C:\\node\\node.exe", "C:\\node\\node_modules\\npm\\bin\\npm-cli.js"]
	assert actualizacion.version_npm("command-code", ejecutar('"1.89.1"\n'), npm) == "1.89.1"
	assert ordenes[-1] == [*npm, "view", "command-code", "version", "--json"]
	assert actualizacion.version_npm("x", ejecutar('["1.0.0", "1.2.0"]'), npm) == "1.2.0"
	with pytest.raises(actualizacion.ErrorActualizacion, match="E404"):
		actualizacion.version_npm("x", ejecutar("", 1, "npm error code E404"), npm)
	with pytest.raises(actualizacion.ErrorActualizacion, match="no devolvió"):
		actualizacion.version_npm("x", ejecutar("hola"), npm)
	with pytest.raises(actualizacion.ErrorActualizacion, match="no es válido"):
		actualizacion.version_npm("x & y", ejecutar('"1.0.0"'), npm)


def test_orden_npm_es_nativa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	node = tmp_path / "nodejs" / "node.exe"
	cli = node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
	cli.parent.mkdir(parents=True)
	node.write_bytes(b"MZ")
	cli.write_text("// npm", encoding="utf-8")
	monkeypatch.setattr(acp, "buscar_node", lambda: node)
	monkeypatch.setattr(acp, "carpetas_npm_globales", lambda nombre_bin="": [])
	assert actualizacion.orden_npm() == [str(node), str(cli)]
	monkeypatch.setattr(acp, "buscar_node", lambda: None)
	assert actualizacion.orden_npm() is None


def test_comprobador_con_cache_de_cinco_minutos() -> None:
	llamadas = []
	reloj = [1000.0]

	def consultar(paquete: str) -> str:
		llamadas.append(paquete)
		if paquete == "roto":
			raise actualizacion.ErrorActualizacion("sin red")
		return "2.0.0"

	comprobador = actualizacion.ComprobadorVersiones(consultar, reloj=lambda: reloj[0])
	assert comprobador.guardada("a") is None
	assert comprobador.consultar("a").version == "2.0.0"
	assert comprobador.consultar("a").version == "2.0.0"
	assert llamadas == ["a"]
	reloj[0] += actualizacion.VIGENCIA - 1
	assert comprobador.guardada("a") is not None
	reloj[0] += 2
	assert comprobador.guardada("a") is None
	comprobador.consultar("a")
	assert llamadas == ["a", "a"]
	assert comprobador.consultar("roto").error == "sin red"
	comprobador.olvidar()
	assert comprobador.guardada("a") is None


class _ProveedorFalso:
	def __init__(self, identificador: str, nombre: str) -> None:
		self.id = identificador
		self.nombre = nombre
		self.descripcion = ""
		self.web = "https://ejemplo.invalid"

	def instrucciones_instalacion(self) -> str:
		return ""

	def orden_instalacion(self):
		return None

	def orden_inicio_sesion(self):
		return None


class _GestorAgentes:
	def __init__(self, ruta_npm: str, comprobador) -> None:
		self.proveedores = {"claude": _ProveedorFalso("claude", "Claude Code"), "copilot": _ProveedorFalso("copilot", "Copilot")}
		self.estados = {
			"claude": EstadoProveedor(True, ruta="C:\\Users\\ana\\.local\\bin\\claude.exe", version="2.1.308 (Claude Code)"),
			"copilot": EstadoProveedor(True, ruta=ruta_npm, version="1.0.0"),
		}
		self.comprobador_versiones = comprobador
		self.ocupadas: list[str] = []
		self.almacen = self

	def detectar(self, identificador):
		return self.estados.get(identificador)

	def sesiones_ocupadas(self):
		return list(self.ocupadas)

	def sesion(self, clave):
		from types import SimpleNamespace

		return SimpleNamespace(proveedor=clave.split(":")[0])


def test_dialogo_agentes_version_disponible_y_actualizar(app_wx, tmp_path: Path, efectos: dict) -> None:
	from app.ui.dialogos import agentes as mod_agentes
	from tests.ayudas_wx import esperar_hasta

	paquete = _paquete(tmp_path / "npm", "@github/copilot", "1.0.0", "copilot")
	comprobador = actualizacion.ComprobadorVersiones(lambda paquete: "1.2.0")
	gestor = _GestorAgentes(str(paquete / "dist" / "index.js"), comprobador)
	dialogo = mod_agentes.DialogoAgentes(None, gestor, comprobar_al_abrir=False)
	try:
		assert not dialogo.IsShown()
		for identificador in dialogo.ids:
			dialogo.poner_estado(identificador, gestor.detectar(identificador))
		dialogo.terminar_comprobacion()
		esperar_hasta(lambda: dialogo.lista.texto_celda(1, mod_agentes.COL_DISPONIBLE) == "1.2.0, hay una nueva", 5.0,
			"no llegó la versión disponible")
		assert dialogo.lista.texto_celda(0, mod_agentes.COL_DISPONIBLE) == ""  # Claude Code: solo la instalada
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.al_cambiar_fila()
		assert "no deja consultarla sin instalarla" in dialogo.descripcion.GetValue()
		dialogo.lista.enfocar_fila(1, anunciar=False)
		dialogo.al_cambiar_fila()
		assert "Versión disponible en npm: 1.2.0." in dialogo.descripcion.GetValue()
		menu = dialogo.crear_menu()
		etiquetas = {item.GetItemLabel(): item.IsEnabled() for item in menu.GetMenuItems() if not item.IsSeparator()}
		menu.Destroy()
		assert etiquetas["Ac&tualizar…"] is True
		# Con una sesión de Copilot trabajando, no se actualiza: se avisa.
		gestor.ocupadas = ["copilot:1", "claude:2"]
		dialogo.actualizar()
		assert "trabajando ahora en 1 sesiones" in efectos["mensaje"][-1] and not efectos["consola"]
		# Sin nadie trabajando: confirma y abre la consola con npm install -g …@latest.
		gestor.ocupadas = []
		efectos["respuesta"] = wx.ID_NO
		dialogo.actualizar()
		assert not efectos["consola"]
		efectos["respuesta"] = wx.ID_YES
		dialogo.actualizar()
		assert efectos["consola"][-1] == ["cmd.exe", "/k", "npm install -g @github/copilot@latest"]
		assert "npm install -g @github/copilot@latest" in efectos["confirmar"][-1]
		# Claude Code: claude update en PowerShell, esperando a Intro para leer el resultado.
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.actualizar()
		assert efectos["consola"][-1][0] == "powershell.exe" and "'update'" in efectos["consola"][-1][-1]
	finally:
		dialogo.Destroy()


# ════════════════════════════════════════════════════════════════════════════
# 2. Lo que el agente sabe de ti
# ════════════════════════════════════════════════════════════════════════════


def test_guardar_texto_con_copia_y_sin_perder_nada(tmp_path: Path) -> None:
	ruta = tmp_path / "CLAUDE.md"
	ruta.write_bytes(b"\xef\xbb\xbfuno\r\ndos\r\n")
	copias = tmp_path / "copias"
	cargado = memoria.cargar_para_editar(ruta)
	assert cargado.texto == "uno\ndos\n" and cargado.fin_linea == "\r\n" and cargado.bom
	copia = memoria.guardar_texto(ruta, "uno\ndos\ntres\n", copias, cargado)
	assert ruta.read_bytes() == b"\xef\xbb\xbfuno\r\ndos\r\ntres\r\n"  # mismo BOM y fin de línea
	assert copia is not None and copia.read_bytes() == b"\xef\xbb\xbfuno\r\ndos\r\n"
	assert not list(tmp_path.glob(".ctrlia-*"))  # ningún temporal suelto
	# Si el agente lo cambia mientras se edita, no se pisa sin preguntar.
	viejo = memoria.cargar_para_editar(ruta)
	ruta.write_bytes(b"lo escribio el agente\n" * 3)
	with pytest.raises(memoria.ArchivoCambiado):
		memoria.guardar_texto(ruta, "mio\n", copias, viejo)
	assert ruta.read_bytes().startswith(b"lo escribio")
	memoria.guardar_texto(ruta, "mio\n", copias, viejo, forzar=True)
	assert ruta.read_bytes().endswith(b"mio\r\n")
	assert any(c.read_bytes().startswith(b"lo escribio") for c in copias.iterdir())
	# Un JSON roto no se guarda.
	ajustes = tmp_path / "settings.json"
	ajustes.write_text('{"a": 1}', encoding="utf-8")
	with pytest.raises(memoria.ErrorMemoria, match="JSON"):
		memoria.guardar_texto(ajustes, '{"a": ', copias, memoria.cargar_para_editar(ajustes))
	assert ajustes.read_text(encoding="utf-8") == '{"a": 1}'
	# Lo que no es UTF-8 no se edita aquí.
	latin = tmp_path / "latin.md"
	latin.write_bytes("año".encode("latin-1"))
	with pytest.raises(memoria.ErrorMemoria, match="UTF-8"):
		memoria.cargar_para_editar(latin)


def test_memorias_de_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
	proyecto = tmp_path / "proyecto"
	proyecto.mkdir()
	carpeta = memoria.carpeta_memoria_claude(proyecto)
	carpeta.mkdir(parents=True)
	(carpeta / "MEMORY.md").write_text("- [Otra](otra.md) — se queda\n", encoding="utf-8")
	ruta = memoria.anadir_memoria_claude(proyecto, "Pruebas con pytest", "Cómo se prueban los cambios", "feedback",
		"Usa entorno.bat pruebas.")
	assert ruta == carpeta / "pruebas-con-pytest.md"
	texto = ruta.read_text(encoding="utf-8")
	assert texto.startswith("---\nname: pruebas-con-pytest\ndescription: \"Cómo se prueban los cambios\"\nmetadata:\n")
	assert "  type: feedback\n" in texto and texto.endswith("---\n\nUsa entorno.bat pruebas.\n")
	indice = (carpeta / "MEMORY.md").read_text(encoding="utf-8")
	assert indice == "- [Otra](otra.md) — se queda\n- [Pruebas con pytest](pruebas-con-pytest.md) — Cómo se prueban los cambios\n"
	with pytest.raises(FileExistsError):
		memoria.anadir_memoria_claude(proyecto, "Pruebas con pytest", "otra", "user", "otro texto")
	with pytest.raises(ValueError):
		memoria.anadir_memoria_claude(proyecto, "¡¿!", "d", "user", "t")
	with pytest.raises(ValueError):
		memoria.anadir_memoria_claude(proyecto, "x", "d", "inventado", "t")
	assert memoria.es_memoria_claude(ruta)
	copias = tmp_path / "copias"
	copia = memoria.borrar_memoria_claude(ruta, copias)
	assert not ruta.exists() and copia is not None and copia.read_text(encoding="utf-8") == texto
	assert (carpeta / "MEMORY.md").read_text(encoding="utf-8") == "- [Otra](otra.md) — se queda\n"
	with pytest.raises(ValueError):
		memoria.borrar_memoria_claude(carpeta / "MEMORY.md", copias)
	ajena = proyecto / "nota.md"
	ajena.write_text("x", encoding="utf-8")
	with pytest.raises(ValueError):
		memoria.borrar_memoria_claude(ajena, copias)
	assert ajena.exists()


def test_crear_subagentes_sin_sobrescribir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
	monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
	claude = memoria.crear_subagente(memoria.CLAUDE, "revisor", "Revisa el código", "Revisa con cuidado.\nSin prisa.",
		"Read, Grep,  Glob")
	assert claude == tmp_path / ".claude" / "agents" / "revisor.md"
	assert claude.read_text(encoding="utf-8") == (
		"---\nname: revisor\ndescription: \"Revisa el código\"\ntools: Read, Grep, Glob\n---\n\nRevisa con cuidado.\nSin prisa.\n")
	codex = memoria.crear_subagente(memoria.CODEX, "revisor", "Revisa \"todo\"", "Línea 1\nLínea 2 con \\ barra")
	datos = tomllib.loads(codex.read_text(encoding="utf-8"))
	assert datos == {"name": "revisor", "description": "Revisa \"todo\"", "developer_instructions": "Línea 1\nLínea 2 con \\ barra"}
	antes = claude.read_text(encoding="utf-8")
	with pytest.raises(FileExistsError):
		memoria.crear_subagente(memoria.CLAUDE, "revisor", "otra", "otras")
	assert claude.read_text(encoding="utf-8") == antes
	for malo in ("Revisor", "con espacio", "con", "-x", "", "a" * 70):
		with pytest.raises(ValueError):
			memoria.crear_subagente(memoria.CLAUDE, malo, "d", "i")
	with pytest.raises(ValueError):
		memoria.crear_subagente(memoria.CLAUDE, "otro", "d", "i", "Bash(rm: *)")
	with pytest.raises(ValueError):
		memoria.crear_subagente(memoria.CODEX, "otro", "", "i")


def test_dialogo_editar_archivo(app_wx, tmp_path: Path, efectos: dict) -> None:
	from app.ui.dialogos import memoria as mod_memoria

	ruta = tmp_path / "AGENTS.md"
	ruta.write_text("# Hola\n", encoding="utf-8")
	archivo = memoria.ArchivoMemoria("Instrucciones", ruta, memoria.PROYECTO, memoria.CODEX, True, False)
	assert memoria.es_editable(archivo)
	dialogo = mod_memoria.DialogoEditarArchivo(None, archivo, tmp_path / "copias")
	try:
		assert not dialogo.IsShown()
		assert sorted(set(_letras(dialogo))) == sorted(_letras(dialogo))
		assert dialogo.texto.GetHelpText()
		assert not dialogo.modificado()
		dialogo.texto.SetValue("# Hola\nNueva línea\n")
		assert dialogo.modificado()
		assert dialogo.guardar()
		assert ruta.read_text(encoding="utf-8") == "# Hola\nNueva línea\n"
		assert dialogo.guardado and not dialogo.modificado()
		assert len(list((tmp_path / "copias").iterdir())) == 1
	finally:
		dialogo.Destroy()


def test_dialogo_memoria_acciones_nuevas(app_wx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, efectos: dict) -> None:
	from app.ui.dialogos import memoria as mod_memoria

	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
	monkeypatch.setattr(mod_memoria, "carpeta_copias", lambda: tmp_path / "copias")
	proyecto = tmp_path / "proyecto"
	proyecto.mkdir()
	nota = memoria.anadir_memoria_claude(proyecto, "Café", "Le gusta solo", "user", "Sin azúcar.")
	dialogo = mod_memoria.DialogoMemoria(None, proyecto, "claude")
	try:
		indice = next(i for i, a in enumerate(dialogo.archivos) if a.ruta == nota)
		dialogo.lista.enfocar_fila(indice, anunciar=False)
		assert dialogo.puede_borrar(dialogo.seleccionado())
		menu = dialogo._crear_menu()
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		menu.Destroy()
		letras = [e.split("&", 1)[1][0].lower() for e in etiquetas]
		assert len(letras) == len(set(letras)), etiquetas
		efectos["respuesta"] = wx.ID_NO
		dialogo.borrar_memoria()
		assert nota.exists()
		efectos["respuesta"] = wx.ID_YES
		dialogo.borrar_memoria()
		assert not nota.exists() and "Café" not in (nota.parent / "MEMORY.md").read_text(encoding="utf-8")
		# El índice no se borra nunca desde aquí.
		indice = next(i for i, a in enumerate(dialogo.archivos) if a.ruta.name == "MEMORY.md")
		dialogo.lista.enfocar_fila(indice, anunciar=False)
		assert not dialogo.puede_borrar(dialogo.seleccionado())
	finally:
		dialogo.Destroy()


def test_dialogo_nuevo_subagente_quita_la_letra_del_campo_desactivado(app_wx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
		efectos: dict) -> None:
	from app.ui.dialogos import memoria as mod_memoria

	monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
	dialogo = mod_memoria.DialogoNuevoSubagente(None, "codex")
	try:
		etiqueta = dialogo.herramientas._etiqueta_campo
		assert dialogo.agente_elegido() == memoria.CODEX
		assert not dialogo.herramientas.IsEnabled() and "&" not in etiqueta.GetLabel()
		dialogo.agente.SetSelection(0)
		dialogo._al_cambiar_agente()
		assert dialogo.herramientas.IsEnabled() and etiqueta.GetLabel().startswith("&Herramientas")
		dialogo.agente.SetSelection(1)
		dialogo._al_cambiar_agente()
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		dialogo.nombre.SetValue("Mal Nombre")
		dialogo._aceptar()
		assert dialogo.ruta is None and "minúsculas" in efectos["error"][-1]
		dialogo.nombre.SetValue("ayudante")
		dialogo.descripcion.SetValue("Ayuda")
		dialogo.instrucciones.SetValue("Ayuda en todo.")
		dialogo._aceptar()
		assert dialogo.ruta == tmp_path / ".codex" / "agents" / "ayudante.toml"
	finally:
		dialogo.Destroy()


# ════════════════════════════════════════════════════════════════════════════
# 3. Cambios sin confirmar en git
# ════════════════════════════════════════════════════════════════════════════


def test_analizar_numstat_con_renombrados_y_binarios() -> None:
	salida = "3\t1\ta.txt\0" + "1\t0\t\0sub/viejo.txt\0sub/nuevo.txt\0" + "-\t-\timagen.png\0"
	cambios = servicio_git.analizar_numstat(salida)
	assert [(c.ruta, c.anadidas, c.quitadas, c.movido_desde) for c in cambios] == [
		("a.txt", 3, 1, ""), ("sub/nuevo.txt", 1, 0, "sub/viejo.txt"), ("imagen.png", None, None, ""),
	]
	assert cambios[1].nombre == "nuevo.txt" and cambios[1].carpeta == "sub"


def test_cambios_sin_confirmar_y_su_diff(repo: Path) -> None:
	(repo / "a.txt").write_text("uno\nDOS\ntres\ncuatro\n", encoding="utf-8")
	(repo / "b.txt").unlink()
	_git(repo, "mv", "sub/viejo.txt", "sub/nuevo.txt")
	(repo / "nuevo con espacios ñ.md").write_text("hola\nadiós\n", encoding="utf-8")
	(repo / "binario.bin").write_bytes(b"\0\1\2")
	(repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
	(repo / "ignorado.log").write_text("x\n", encoding="utf-8")
	raiz, cambios = servicio_git.cambios_sin_confirmar(repo / "sub")
	assert raiz.resolve() == repo.resolve()
	por_ruta = {c.ruta: c for c in cambios}
	assert (por_ruta["a.txt"].anadidas, por_ruta["a.txt"].quitadas) == (2, 1)
	assert (por_ruta["b.txt"].anadidas, por_ruta["b.txt"].quitadas) == (0, 1)
	assert por_ruta["sub/nuevo.txt"].movido_desde == "sub/viejo.txt"
	assert por_ruta["nuevo con espacios ñ.md"].nuevo and por_ruta["nuevo con espacios ñ.md"].anadidas == 2
	assert por_ruta["binario.bin"].nuevo and por_ruta["binario.bin"].anadidas is None
	assert "ignorado.log" not in por_ruta and ".gitignore" in por_ruta
	diff = servicio_git.diff_de(raiz, por_ruta["a.txt"])
	assert "-dos" in diff and "+DOS" in diff and "+cuatro" in diff
	assert "Línea 2, añadida: DOS" in extraccion.diff_legible(diff)
	assert servicio_git.diff_de(raiz, por_ruta["nuevo con espacios ñ.md"]) == "hola\nadiós\n"
	assert "rename" in servicio_git.diff_de(raiz, por_ruta["sub/nuevo.txt"])


def test_cambios_sin_confirmar_en_repositorio_sin_commits_y_fuera_de_git(tmp_path: Path) -> None:
	if servicio_git.buscar_git() is None:
		pytest.skip("No hay git")
	vacio = tmp_path / "vacio"
	vacio.mkdir()
	_git(vacio, "init", "-q")
	(vacio / "x.py").write_text("print(1)\n", encoding="utf-8")
	_git(vacio, "add", "x.py")
	(vacio / "y.py").write_text("a\nb\n", encoding="utf-8")
	_raiz, cambios = servicio_git.cambios_sin_confirmar(vacio)
	assert [(c.ruta, c.anadidas, c.nuevo) for c in cambios] == [("x.py", 1, False), ("y.py", 2, True)]
	suelta = tmp_path / "suelta"
	suelta.mkdir()
	with pytest.raises(servicio_git.ErrorGit, match="no es un repositorio"):
		servicio_git.cambios_sin_confirmar(suelta)


# ════════════════════════════════════════════════════════════════════════════
# 5c. Copia aislada desde origin o en una rama que ya existe
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def clon(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
	"""``(clon, otro)``: un clon de un origin local (carpeta, sin red) y otro clon que sube cambios."""
	origen = tmp_path / "origen.git"
	_git(tmp_path, "clone", "-q", "--bare", str(repo), str(origen))
	clon = tmp_path / "clon"
	_git(tmp_path, "clone", "-q", str(origen), str(clon))
	otro = tmp_path / "otro"
	_git(tmp_path, "clone", "-q", str(origen), str(otro))
	(otro / "a.txt").write_text("cambiado en origin\n", encoding="utf-8")
	_git(otro, "commit", "-q", "-am", "nuevo en origin")
	_git(otro, "push", "-q", "origin", "main")
	_git(otro, "push", "-q", "origin", "main:refs/heads/solo-remota")
	_git(clon, "branch", "local-libre")
	return clon, otro


def test_copia_aislada_desde_la_rama_principal_de_origin(clon: tuple[Path, Path]) -> None:
	carpeta, _otro = clon
	assert servicio_git.rama_principal_remota(carpeta) == "main"
	desde_carpeta = servicio_git.crear_worktree(carpeta, "ctrlia/desde-carpeta")
	assert (desde_carpeta / "a.txt").read_text(encoding="utf-8") == "uno\ndos\ntres\n"
	desde_remoto = servicio_git.crear_worktree_desde_remoto(carpeta, "ctrlia/desde-remoto")
	assert (desde_remoto / "a.txt").read_text(encoding="utf-8") == "cambiado en origin\n"
	assert desde_remoto == carpeta / ".ctrlia" / "worktrees" / "ctrlia-desde-remoto"
	# La rama nueva no sigue a origin/main: un push nunca va a la principal.
	resultado = subprocess.run(["git", "-C", str(desde_remoto), "rev-parse", "--abbrev-ref", "@{upstream}"],
		capture_output=True, text=True, check=False)
	assert resultado.returncode != 0
	assert ".ctrlia/" in (carpeta / ".git" / "info" / "exclude").read_text(encoding="utf-8")


def test_ramas_que_ya_existen(clon: tuple[Path, Path]) -> None:
	carpeta, _otro = clon
	ramas = servicio_git.ramas_disponibles(carpeta)
	nombres = [(r.nombre, r.remota, bool(r.sacada_en)) for r in ramas]
	# Primero las locales libres, luego las de origin que no hay aquí y al final las ya sacadas.
	assert nombres == [("local-libre", False, False), ("solo-remota", True, False), ("main", False, True)]
	assert ramas[1].texto == "solo-remota (en origin)"
	assert "ya sacada en" in ramas[2].texto
	with pytest.raises(servicio_git.ErrorGit, match="ya está sacada"):
		servicio_git.crear_worktree_rama_existente(carpeta, ramas[2])
	remota = servicio_git.crear_worktree_rama_existente(carpeta, ramas[1])
	assert (remota / "a.txt").read_text(encoding="utf-8") == "cambiado en origin\n"
	local = servicio_git.crear_worktree_rama_existente(carpeta, ramas[0])
	assert local.is_dir()
	sacadas = servicio_git.ramas_sacadas(carpeta)
	assert {"main", "local-libre", "solo-remota"} <= set(sacadas)
	assert [r.nombre for r in servicio_git.ramas_disponibles(carpeta, traer=False) if not r.sacada_en] == []


# ════════════════════════════════════════════════════════════════════════════
# 3 y 4. Listas de la sesión: Cambios sin confirmar en git y Aprobaciones
# ════════════════════════════════════════════════════════════════════════════


def _revision(estado: str, accion: str, motivo: str = "", fecha: float = 1.0) -> MensajeVista:
	datos = {"revision_automatica": {"id": "r", "estado": estado, "accion": accion, "riesgo": "bajo", "motivo": motivo}}
	return MensajeVista("sistema", "La revisión automática de Codex decidió. " + accion, fecha, datos=datos)


def test_aprobaciones_y_su_resumen() -> None:
	mensajes = [
		MensajeVista("usuario", "Haz algo", 0.5),
		_revision("approved", "Orden: git status", "Solo lee", 1.0),
		MensajeVista("sistema", "Otro aviso", 1.5),
		_revision("denied", "Orden: rm -rf build", "Borra", 2.0),
		_revision("approved", "Cambiar archivos: a.py", "", 3.0),
		_revision("rarisimo", "Orden: x"),
	]
	lista = extraccion.aprobaciones(mensajes)
	assert [a.accion for a in lista] == ["Orden: x", "Cambiar archivos: a.py", "Orden: rm -rf build", "Orden: git status"]
	assert lista[2].orden == "rm -rf build" and lista[2].motivo == "Borra" and lista[2].indice == 3
	assert lista[1].orden == "" and lista[1].nombre_estado == "Permitida"
	assert extraccion.resumen_aprobaciones(mensajes) == "2 permitidas automáticamente, 1 no permitida, 1 con otra decisión"
	assert extraccion.resumen_aprobaciones(lista[1:3]) == "1 permitida automáticamente, 1 no permitida"
	assert extraccion.resumen_aprobaciones([MensajeVista("usuario", "x", 1.0)]) == ""


def test_listas_paginas_aprobaciones_y_git(app_wx, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui.dialogos import listas
	from tests.ayudas_wx import esperar_hasta

	copiados = []
	monkeypatch.setattr(listas, "copiar_al_portapapeles", lambda texto: copiados.append(texto) or True)
	documentos = []
	monkeypatch.setattr(listas, "mostrar_documento", lambda _p, titulo, texto, **_o: documentos.append((titulo, texto)))
	(repo / "a.txt").write_text("uno\ndos\ntres\ncuatro\n", encoding="utf-8")
	mensajes = [MensajeVista("usuario", "Hola", 1.0), _revision("approved", "Orden: git status", "Solo lee", 2.0),
		_revision("denied", "Orden: git push --force", "Peligroso", 3.0)]
	dialogo = listas.DialogoListas(None, mensajes, "Prueba", "aprobaciones", carpeta=str(repo), editor="", propia=True,
		transcritos=[], indice_elegido=-1, ambito="sesion")
	try:
		assert not dialogo.IsShown()
		assert dialogo.etiquetas["aprobaciones"].GetLabel() == "Aprobacio&nes de la revisión automática (2):"
		assert dialogo.listas["aprobaciones"].valores_fila(0)[:3] == ["Orden: git push --force", "No permitida", "bajo"]
		assert dialogo.copiar() and copiados[-1] == "git push --force"
		dialogo.activar()
		assert "Peligroso" in documentos[-1][1]
		letras = [e.GetLabel().split("&", 1)[1][0].lower() for e in dialogo.etiquetas.values()]
		letras += [dialogo.etiqueta_ambito.GetLabel().split("&", 1)[1][0].lower(), "f", "c"]
		assert len(letras) == len(set(letras)), letras
		esperar_hasta(lambda: dialogo.listas["git"].numero_filas() == 1, 10.0, "git no contestó")
		assert dialogo.listas["git"].valores_fila(0) == ["a.txt", "1 línea añadida, 0 quitadas", "en la raíz del repositorio"]
		dialogo.ir_a_pagina(listas.PAGINAS.index("git"))
		assert dialogo.pagina_actual() == "git"
		assert dialogo.copiar() and Path(copiados[-1]).resolve() == (repo / "a.txt").resolve()
		dialogo.activar()
		esperar_hasta(lambda: documentos[-1][0] == "Cambios en a.txt", 10.0, "no se mostró el diff")
		assert "cuatro" in documentos[-1][1]
		menu = dialogo._crear_menu("git")
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		menu.Destroy()
		assert all("\t" in e for e in etiquetas) and "&Volver a preguntar a git\tF5" in etiquetas
		# F5 vuelve a preguntar: el archivo nuevo aparece.
		(repo / "z.txt").write_text("z\n", encoding="utf-8")
		dialogo.actualizar_pagina()
		esperar_hasta(lambda: dialogo.listas["git"].numero_filas() == 2, 10.0, "F5 no volvió a leer")
	finally:
		dialogo.Destroy()


def test_listas_git_sin_repositorio_lo_dice(app_wx, tmp_path: Path) -> None:
	from app.ui.dialogos import listas
	from tests.ayudas_wx import esperar_hasta

	dialogo = listas.DialogoListas(None, [MensajeVista("usuario", "Hola", 1.0)], "Prueba", "git", carpeta=str(tmp_path),
		editor="", propia=True, transcritos=[], indice_elegido=-1, ambito="sesion")
	try:
		esperar_hasta(lambda: dialogo._estado_git is not None, 10.0, "git no contestó")
		assert "no es un repositorio" in dialogo._aviso_vacio("git")
	finally:
		dialogo.Destroy()
	dialogo = listas.DialogoListas(None, [], "Prueba", "git", carpeta="/Users/ana/proyecto", editor="", propia=True,
		transcritos=[], indice_elegido=-1, ambito="sesion")
	try:
		assert "no está en este equipo" in dialogo._aviso_vacio("git")
	finally:
		dialogo.Destroy()


# ════════════════════════════════════════════════════════════════════════════
# 5. Nueva sesión y Opciones de la sesión
# ════════════════════════════════════════════════════════════════════════════


def test_resolver_carpeta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui.dialogos.nueva_sesion import ErrorCarpeta, resolver_carpeta

	base = str(tmp_path / "proyectos")
	assert resolver_carpeta("web", base) == Path(base) / "web"
	assert resolver_carpeta("  \"cliente\\api\"  ", base) == Path(base) / "cliente" / "api"
	assert resolver_carpeta("'C:\\otra\\cosa'", base) == Path("C:\\otra\\cosa")
	assert resolver_carpeta("\\\\servidor\\recurso\\x") == Path("\\\\servidor\\recurso\\x")
	monkeypatch.setenv("USERPROFILE", str(tmp_path / "casa"))
	monkeypatch.setenv("HOME", str(tmp_path / "casa"))
	assert resolver_carpeta("~\\proyectos\\x") == tmp_path / "casa" / "proyectos" / "x"
	assert resolver_carpeta("web", "~\\pro") == tmp_path / "casa" / "pro" / "web"
	for malo in ("", "  ", "web", "C:sin-barra"):
		with pytest.raises(ErrorCarpeta):
			resolver_carpeta(malo)


class _ProveedorSesion:
	"""Agente falso que admite instrucciones de la sesión."""

	id = "falso"
	nombre = "Agente falso"
	capacidades = Capacidades(instrucciones_sesion=True)

	def modelos(self):
		return [OpcionLista("", "Predeterminado")]

	def esfuerzos(self):
		return []

	def modos_permiso(self):
		return []

	def estilos(self):
		return []

	def modo_de_serie(self):
		return ""

	def modo_predeterminado(self, carpeta=None):
		return ""


class _ProveedorSinInstrucciones(_ProveedorSesion):
	id = "otro"
	nombre = "Otro agente"
	capacidades = Capacidades()


class _GestorSesion:
	def __init__(self) -> None:
		self.proveedores = {"falso": _ProveedorSesion(), "otro": _ProveedorSinInstrucciones()}

	def estado_proveedor(self, identificador):
		return EstadoProveedor(True)


@pytest.fixture
def nueva(app_wx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	from app.core.configuracion import Configuracion
	from app.services.almacen import Almacen
	from app.ui.dialogos.nueva_sesion import DialogoNuevaSesion

	config = Configuracion(tmp_path / "s.json")
	config.establecer("general.carpeta_proyectos", str(tmp_path / "proyectos"))
	almacen = Almacen(tmp_path / "n.db")
	creados = []

	def crear(**opciones) -> DialogoNuevaSesion:
		dialogo = DialogoNuevaSesion(None, _GestorSesion(), almacen, config, **opciones)
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		creados.append(dialogo)
		return dialogo

	yield crear
	for dialogo in creados:
		dialogo.Destroy()


def test_nueva_sesion_instrucciones_carpetas_y_carpeta_base(nueva, tmp_path: Path, efectos: dict) -> None:
	extra = tmp_path / "compartida"
	extra.mkdir()
	dialogo = nueva(agente="falso")
	assert not dialogo.IsShown()
	panel = dialogo.panel_instrucciones
	assert panel.IsShown() and panel.instrucciones.IsEnabled()
	letras = _letras(dialogo)
	assert len(letras) == len(set(letras)), letras
	assert panel.instrucciones.GetHelpText() and panel.carpetas.GetHelpText() and panel.boton_anadir.GetHelpText()
	# Nunca una unidad entera ni la raíz de un recurso compartido.
	assert not panel.anadir_carpeta("C:\\")
	assert not panel.anadir_carpeta("\\\\servidor\\recurso")
	assert panel.anadir_carpeta(str(extra))
	assert not panel.anadir_carpeta(str(extra))  # repetida
	panel.instrucciones.SetValue("  Responde en español.  ")
	# Un nombre se busca en la carpeta base; si no existe, se ofrece crearla.
	dialogo.carpeta.SetValue("\"nuevo-proyecto\"")
	efectos["respuesta"] = wx.ID_NO
	dialogo._aceptar()
	assert dialogo.resultado is None and "no existe" in efectos["confirmar"][-1]
	efectos["respuesta"] = wx.ID_YES
	dialogo._aceptar()
	destino = tmp_path / "proyectos" / "nuevo-proyecto"
	assert destino.is_dir()
	assert dialogo.resultado is not None and dialogo.resultado.ajustes.carpeta == destino
	assert dialogo.resultado.ajustes.extra["instrucciones"] == "Responde en español."
	assert dialogo.resultado.ajustes.extra["carpetas_adicionales"] == [str(extra)]
	# Con un agente que no las admite, el panel se oculta, sus etiquetas pierden la «&» y no va nada en extra.
	dialogo.agente.SetSelection(1)
	dialogo._al_cambiar_agente()
	assert not panel.IsShown() and "&" not in panel.etiqueta_instrucciones.GetLabel()
	dialogo.resultado = None
	dialogo._aceptar()
	assert "instrucciones" not in dialogo.resultado.ajustes.extra


def test_nueva_sesion_etiquetas_de_controles_desactivados(nueva) -> None:
	dialogo = nueva(agente="otro")
	# Un solo modelo: el desplegable se desactiva y Alt+M ya no salta al control siguiente.
	assert not dialogo.modelo.IsEnabled()
	assert dialogo.modelo._etiqueta_campo.GetLabel() == "Modelo:"
	assert not dialogo.rama.IsEnabled() and dialogo.rama._etiqueta_campo.GetLabel() == "Nombre de la rama:"
	dialogo.aislada.SetValue(True)
	dialogo._actualizar_copia()
	assert dialogo.rama._etiqueta_campo.GetLabel() == "Nombre de la &rama:"
	assert dialogo.forma_copia.IsEnabled() and not dialogo.rama_existente.IsEnabled()
	assert "&" not in dialogo.rama_existente._etiqueta_campo.GetLabel()


def test_nueva_sesion_copia_en_rama_existente_y_desde_origin(nueva, clon: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
		efectos: dict) -> None:
	from tests.ayudas_wx import esperar_hasta

	carpeta, _otro = clon
	dialogo = nueva(agente="otro", carpeta=carpeta)
	dialogo.aislada.SetValue(True)
	dialogo.forma_copia.SetSelection(2)
	dialogo._actualizar_copia()
	assert dialogo.rama_existente.IsEnabled() and not dialogo.rama.IsEnabled()
	esperar_hasta(lambda: len(dialogo._ramas) == 3, 15.0, "no llegaron las ramas")
	assert dialogo.rama_existente.GetString(1) == "solo-remota (en origin)"
	dialogo.rama_existente.SetSelection(2)  # main: ya sacada en la carpeta principal
	dialogo._aceptar()
	assert dialogo.resultado is None and "ya está sacada" in efectos["error"][-1]
	dialogo.rama_existente.SetSelection(1)
	dialogo._aceptar()
	assert dialogo.resultado is not None
	assert dialogo.resultado.ajustes.carpeta == carpeta / ".ctrlia" / "worktrees" / "solo-remota"
	# Desde origin: en segundo plano, y la sesión se crea al acabar.
	dialogo.resultado = None
	dialogo.forma_copia.SetSelection(1)
	dialogo.rama.SetValue("ctrlia/prueba-remota")
	dialogo._actualizar_copia()
	dialogo._aceptar()
	esperar_hasta(lambda: dialogo.resultado is not None, 30.0, "no se creó la copia desde origin")
	copia = dialogo.resultado.ajustes.carpeta
	assert (copia / "a.txt").read_text(encoding="utf-8") == "cambiado en origin\n"


def test_opciones_de_la_sesion_instrucciones(app_wx, monkeypatch: pytest.MonkeyPatch) -> None:
	from types import SimpleNamespace

	from app.ui.dialogos.ajustes_sesion import DialogoAjustesSesion

	sesion = SimpleNamespace(titulo="T", modelo="", esfuerzo="", modo_permiso="",
		extra={"instrucciones": "Antes", "carpetas_adicionales": ["C:\\a\\b"]})
	dialogo = DialogoAjustesSesion(None, _ProveedorSesion(), sesion)
	try:
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		panel = dialogo.panel_instrucciones
		assert panel is not None and panel.instrucciones.GetValue() == "Antes"
		assert panel.lista_carpetas() == ["C:\\a\\b"]
		letras = _letras(dialogo)
		assert len(letras) == len(set(letras)), letras
		dialogo._aceptar()
		assert "instrucciones" not in dialogo.cambios and "carpetas_adicionales" not in dialogo.cambios
		panel.instrucciones.SetValue("Después")
		panel.carpetas.SetSelection(0)
		panel.quitar_carpeta()
		dialogo._aceptar()
		assert dialogo.cambios["instrucciones"] == "Después" and dialogo.cambios["carpetas_adicionales"] == []
	finally:
		dialogo.Destroy()
	dialogo = DialogoAjustesSesion(None, _ProveedorSinInstrucciones(), SimpleNamespace(titulo="T", modelo="", esfuerzo="",
		modo_permiso="", extra={}))
	try:
		assert dialogo.panel_instrucciones is None
	finally:
		dialogo.Destroy()


# ════════════════════════════════════════════════════════════════════════════
# 6. Accesibilidad: etiquetas atadas, ListBox sin doble lectura y «&» de los desactivados
# ════════════════════════════════════════════════════════════════════════════


def test_es_etiqueta_de_y_sin_acelerador() -> None:
	from app.ui.accesibilidad import nombres

	assert nombres.es_etiqueta_de("&Archivos (3):", "Archivos (3), 3 de 12")
	assert nombres.es_etiqueta_de("&Volumen:", "Volumen")
	assert not nombres.es_etiqueta_de("Rol, por ejemplo Arreglar:", "Rol")
	assert not nombres.es_etiqueta_de("", "Algo")
	assert nombres.sin_acelerador("&Guardar && salir") == "Guardar && salir"


def test_habilitar_quita_y_devuelve_la_letra(panel) -> None:
	from app.ui.accesibilidad.nombres import crear_campo, habilitar

	etiqueta, campo = crear_campo(panel, "&Modelo:", wx.Choice, choices=["a"])
	habilitar(campo, False)
	assert not campo.IsEnabled() and etiqueta.GetLabel() == "Modelo:"
	habilitar(campo, False)
	assert etiqueta.GetLabel() == "Modelo:"
	habilitar(campo, True)
	assert campo.IsEnabled() and etiqueta.GetLabel() == "&Modelo:"
	# Si la etiqueta cambia desde fuera, manda la nueva.
	etiqueta.SetLabel("&Otro modelo:")
	habilitar(campo, False)
	assert etiqueta.GetLabel() == "Otro modelo:"
	habilitar(campo, True)
	assert etiqueta.GetLabel() == "&Otro modelo:"
	# Sin crear_campo: el texto estático justo anterior.
	suelta = wx.StaticText(panel, label="&Suelta:")
	control = wx.TextCtrl(panel)
	habilitar(control, False)
	assert suelta.GetLabel() == "Suelta:"


def test_list_box_responde_que_tiene_el_foco_ella_misma(panel, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui.accesibilidad.casillas import CheckListBoxAccesible
	from app.ui.accesibilidad.nombres import asignar_nombre_accesible

	wx.StaticText(panel, label="&Secciones:")
	lista = wx.ListBox(panel, choices=["Uno", "Dos"])
	asignar_nombre_accesible(lista, "Secciones")
	accesible = lista.GetAccessible()
	monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: None))
	assert accesible.GetFocus()[0] == wx.ACC_NOT_IMPLEMENTED
	monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: lista))
	estado, hijo, objeto = accesible.GetFocus()
	assert (estado, hijo) == (wx.ACC_OK, 0) and objeto is accesible
	casillas = CheckListBoxAccesible(panel, "Casillas", ["a"])
	monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: casillas))
	assert casillas.GetAccessible().GetFocus()[:2] == (wx.ACC_OK, 0)
	# Un control que no es una lista no cambia lo que dice Windows del foco.
	texto = wx.TextCtrl(panel)
	asignar_nombre_accesible(texto, "Texto")
	monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: texto))
	assert texto.GetAccessible().GetFocus()[0] == wx.ACC_NOT_IMPLEMENTED


@pytest.mark.skipif(os.name != "nt", reason="MSAA solo en Windows")
def test_msaa_la_etiqueta_atada_dice_el_nombre_del_control(panel) -> None:
	pytest.importorskip("comtypes")
	from app.ui.accesibilidad.nombres import asignar_nombre_accesible
	from tests import msaa

	etiqueta = wx.StaticText(panel, label="&Archivos (3):")
	lista = wx.ListBox(panel, choices=["a", "b", "c"])
	asignar_nombre_accesible(lista, "Archivos (3), 3 de 12")
	assert msaa.propiedades(etiqueta)["nombre"] == "Archivos (3), 3 de 12"
	assert msaa.propiedades(lista)["nombre"] == "Archivos (3), 3 de 12"
	# Una nota que no es su etiqueta no se toca.
	nota = wx.StaticText(panel, label="Esto es una nota larga:")
	campo = wx.TextCtrl(panel)
	asignar_nombre_accesible(campo, "Buscar")
	assert msaa.propiedades(nota)["nombre"].startswith("Esto es una nota")
	assert getattr(nota, "_accesible_etiqueta", None) is None


# ════════════════════════════════════════════════════════════════════════════
# 7. Command Code entre los agentes ACP conocidos
# ════════════════════════════════════════════════════════════════════════════


def test_command_code_se_lanza_con_node_y_nunca_con_cmd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	node = tmp_path / "node.exe"
	script = tmp_path / "command-code" / "dist" / "index.mjs"
	node.write_bytes(b"MZ falso")
	script.parent.mkdir(parents=True)
	script.write_text("// falso", encoding="utf-8")
	pedidos = []

	def buscar(paquete: str, nombre_bin: str = ""):
		pedidos.append((paquete, nombre_bin))
		return ([str(node), str(script)], "1.89.1") if paquete == "command-code" else None

	monkeypatch.setattr(acp, "buscar_paquete_npm", buscar)
	monkeypatch.setattr(acp, "buscar_ejecutable", lambda nombres, candidatos=(): None)
	ficha = next(a for a in acp_conocidos.AGENTES if a.id == "command-code")
	assert ficha.paquetes == (("command-code", "command-code"),) and "cmd" not in ficha.nativos
	proveedor = acp_conocidos.proveedor_conocido(ficha)
	assert proveedor.resolver_orden() == [str(node), str(script), "acp"]
	assert pedidos == [("command-code", "command-code")]
	assert proveedor.orden_instalacion() == ["cmd.exe", "/k", "npm install -g command-code"]
	assert "command-code login" in proveedor.instrucciones_instalacion()
