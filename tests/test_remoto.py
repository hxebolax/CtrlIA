"""Pruebas de los agentes en otro equipo por SSH, con un ``ssh`` falso (sin red ni equipo real)."""

import json
import shlex
import sys
import threading
from pathlib import Path, PurePosixPath

import pytest

from app.services.agentes import remoto
from app.services.agentes.base import AjustesSesion, TipoEvento

FAKES = Path(__file__).parent / "fakes"
FAKE_SSH = FAKES / "fake_ssh.py"

MAC = {"id": "mac-del-estudio", "nombre": "Mac del estudio", "destino": "ana@192.168.1.40", "puerto": 22,
	"sistema": "macos", "rutas": {}, "variables": {"GEMINI_FORCE_FILE_STORAGE": "true"}}


def _equipo(**cambios) -> remoto.EquipoSSH:
	return remoto.EquipoSSH.desde_dict({**MAC, **cambios})


# ── Datos del equipo ─────────────────────────────────────────────────────────


def test_equipo_ida_y_vuelta():
	equipo = _equipo(ollama=True, rutas={"claude": "/opt/homebrew/bin/claude", "codex": " "})
	assert equipo.rutas == {"claude": "/opt/homebrew/bin/claude"}
	assert remoto.EquipoSSH.desde_dict(equipo.a_dict()) == equipo
	assert equipo.programa("claude") == "/opt/homebrew/bin/claude"
	assert equipo.programa("antigravity") == "agy"
	assert remoto.EquipoSSH.desde_dict({**MAC, "puerto": True, "ollama": "sí"}).puerto == 22
	assert remoto.EquipoSSH.desde_dict({**MAC, "ollama": "sí"}).ollama is False


@pytest.mark.parametrize("cambios, fragmento", [
	({"nombre": " "}, "nombre"),
	({"destino": "-oProxyCommand=calc"}, "destino"),
	({"destino": "ana@equipo; rm -rf /"}, "destino"),
	({"destino": ""}, "destino"),
	({"puerto": 70000}, "puerto"),
	({"sistema": "windows"}, "sistema"),
	({"variables": {"MAL NOMBRE": "x"}}, "variable"),
])
def test_validar_equipo_rechaza(cambios, fragmento):
	assert fragmento in remoto.validar_equipo(_equipo(**cambios)).lower()


def test_validar_equipo_admite_alias_y_ipv6():
	for destino in ("mac-estudio", "ana@mac-estudio.local", "ana@[fe80::1%en0]", "root@10.0.0.2"):
		assert remoto.validar_equipo(_equipo(destino=destino)) is None, destino


def test_normalizar_carpeta():
	assert remoto.normalizar_carpeta(" /Users/ana/proyectos/web/ ") == "/Users/ana/proyectos/web"
	assert remoto.normalizar_carpeta("~/web/") == "~/web"
	assert remoto.normalizar_carpeta('"~"') == "~"
	for mala in ("", r"C:\proyectos", "D:/x", "proyectos/web", r"\\servidor\x"):
		with pytest.raises(remoto.ErrorRemoto):
			remoto.normalizar_carpeta(mala)


def test_leer_variables_y_nuevo_id():
	assert remoto.leer_variables("# comentario\nA=1\n\nexport B = dos=tres\n") == {"A": "1", "B": "dos=tres"}
	with pytest.raises(remoto.ErrorRemoto):
		remoto.leer_variables("sin igual")
	assert remoto.nuevo_id("Mac del Estudio") == "mac-del-estudio"
	assert remoto.nuevo_id("Mac del Estudio", ["mac-del-estudio"]) == "mac-del-estudio-2"
	assert remoto.nuevo_id("¡¡!!") == "equipo"


def test_equipo_de_una_sesion():
	opcion = lambda _clave, _defecto: [MAC, {"id": ""}, "basura"]  # noqa: E731
	assert set(remoto.equipos(opcion)) == {"mac-del-estudio"}
	extra = {"remoto": {"equipo": "mac-del-estudio", "nombre": "Mac del estudio", "carpeta": "~/web"}}
	assert remoto.equipo_de(extra, opcion).destino == "ana@192.168.1.40"
	assert remoto.carpeta_remota(extra) == "~/web"
	assert remoto.equipo_de({}, opcion) is None and not remoto.es_remota({"remoto": {}})
	with pytest.raises(remoto.ErrorRemoto, match="ya no está"):
		remoto.equipo_de({"remoto": {"equipo": "otro", "nombre": "Portátil"}}, opcion)


def test_experimentales():
	assert not remoto.es_experimental("claude") and not remoto.es_experimental("codex")
	for agente in ("gemini", "antigravity", "acp-mio", "ollama"):
		assert remoto.es_experimental(agente)


def test_admite_agente():
	from app.services.agentes.acp import ProveedorAcp, proveedor_gemini_cli, proveedores_personalizados

	class Falso:
		id = "ollama"

	assert remoto.admite_agente(type("P", (), {"id": "claude"})())
	assert not remoto.admite_agente(Falso())
	assert remoto.admite_agente(proveedor_gemini_cli())
	assert remoto.admite_agente(proveedores_personalizados([{"nombre": "Mío", "orden": [r"C:\x\mio.exe", "--acp"]}])[0])
	assert not remoto.admite_agente(ProveedorAcp("raro", "Raro", "", lambda: ["x"]))


# ── La orden ─────────────────────────────────────────────────────────────────


def test_orden_remota_sin_contrasenas_y_con_comillas(monkeypatch):
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path(r"C:\Windows\System32\OpenSSH\ssh.exe"))
	malicioso = "título'; rm -rf ~; echo \"$(whoami)\" `id`"
	orden = remoto.orden_remota(_equipo(), "claude", ["--name", malicioso], "~/mis proyectos/web",
		quitar=("ANTHROPIC_API_KEY", "MAL;NOMBRE"), anadir={"X": "un valor"})
	assert orden[0].endswith("ssh.exe")
	assert "BatchMode=yes" in orden and "-T" in orden and "-t" not in orden
	assert orden[orden.index("--") + 1] == "ana@192.168.1.40"
	# El shell del otro equipo deshace dos niveles de comillas: el exterior y el guion.
	exterior = shlex.split(orden[-1])
	assert exterior[:4] == ["exec", "$SHELL", "-l", "-c"]
	partes = shlex.split(exterior[4])
	assert partes[partes.index("--name") + 1] == malicioso
	assert partes[partes.index("cd") + 1] == "$HOME/mis proyectos/web"
	assert "ANTHROPIC_API_KEY" in partes and "MAL;NOMBRE" not in exterior[4]
	assert "X=un valor" in partes and "GEMINI_FORCE_FILE_STORAGE=true" in partes
	assert partes[partes.index("X=un valor") + 1] == "claude"


def test_orden_terminal_y_consola(monkeypatch):
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path("ssh.exe"))
	terminal = remoto.orden_terminal(_equipo(puerto=2222))
	assert terminal == ["ssh.exe", "-t", "-o", "ConnectTimeout=15", "-p", "2222", "--", "ana@192.168.1.40"]
	consola = remoto.orden_consola(_equipo(), "codex", ["login", "--device-auth"])
	assert "-t" in consola and "BatchMode=yes" not in consola


def test_sin_ssh_explica_como_instalarlo(monkeypatch):
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: None)
	with pytest.raises(remoto.ErrorRemoto, match="Cliente OpenSSH"):
		remoto.orden_remota(_equipo(), "claude", [])


def test_errores_de_ssh_traducidos():
	assert "clave SSH" in remoto.explicar_error_ssh("ana@mac: Permission denied (publickey).")
	assert "huella" in remoto.explicar_error_ssh("Host key verification failed.")
	assert "No se encuentra el equipo" in remoto.explicar_error_ssh("ssh: Could not resolve hostname mac")
	assert "Sesión remota" in remoto.explicar_error_ssh("connect to host mac port 22: Connection refused")
	assert "no responde" in remoto.explicar_error_ssh("Connection timed out")


# ── Comprobar el equipo ──────────────────────────────────────────────────────


def test_comprobar_equipo(monkeypatch):
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path("ssh.exe"))
	llamadas = []

	def ejecutar(orden, _tiempo):
		guion = shlex.split(orden[-1])[4]
		llamadas.append(guion)
		if "ctrlia-conexion-ok" in guion:
			return 0, "ctrlia-conexion-ok\n", ""
		if "@@" in guion:
			return 0, ("@@claude\n/Users/ana/.local/bin/claude\n2.3.0 (Claude Code)\n@@codex\n/opt/homebrew/bin/codex\n"
				"codex-cli 0.130.0\n@@gemini\n@@antigravity\n"), ""
		if "'login' 'status'" in guion or "login status" in guion:
			return 1, "", "Not logged in"
		return 1, "", "Error: keychain locked, please run /login"

	monkeypatch.setattr(remoto, "_ejecutar", ejecutar)
	resultados = {r.nombre: r for r in remoto.comprobar(_equipo(), ["claude", "codex", "gemini", "antigravity"])}
	assert resultados["Conexión"].correcto
	assert resultados["Claude Code"].correcto and "2.3.0" in resultados["Claude Code"].detalle
	assert not resultados["Gemini CLI"].correcto and "ruta completa" in resultados["Gemini CLI"].detalle
	assert not resultados["Sesión de Codex"].correcto and "--device-auth" in resultados["Sesión de Codex"].detalle
	assert not resultados["Sesión de Claude Code"].correcto and "llavero" in resultados["Sesión de Claude Code"].detalle


def test_comprobar_sin_conexion(monkeypatch):
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path("ssh.exe"))
	monkeypatch.setattr(remoto, "_ejecutar", lambda orden, tiempo: (255, "", "Permission denied (publickey)."))
	resultados = remoto.comprobar(_equipo())
	assert len(resultados) == 1 and not resultados[0].correcto and "ssh-keygen" in resultados[0].detalle


def test_leer_archivo_que_no_existe(monkeypatch):
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path("ssh.exe"))
	monkeypatch.setattr(remoto, "_ejecutar", lambda orden, tiempo: (3, "", "ctrlia-no-existe\n"))
	assert remoto.leer_archivo(_equipo(), "~/.gemini/antigravity-cli/settings.json") is None
	monkeypatch.setattr(remoto, "_ejecutar", lambda orden, tiempo: (0, '{"a": 1}', ""))
	assert remoto.leer_archivo(_equipo(), "~/x.json") == '{"a": 1}'
	monkeypatch.setattr(remoto, "_ejecutar", lambda orden, tiempo: (255, "", "Connection refused"))
	with pytest.raises(remoto.ErrorRemoto):
		remoto.leer_archivo(_equipo(), "~/x.json")


# ── Túnel de Ollama ──────────────────────────────────────────────────────────


def test_tunel_de_ollama(monkeypatch):
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path("ssh.exe"))
	orden = remoto.orden_tunel(_equipo(), 50123)
	assert orden[:2] == ["ssh.exe", "-N"] and "ExitOnForwardFailure=yes" in orden and "BatchMode=yes" in orden
	assert orden[orden.index("-L") + 1] == "127.0.0.1:50123:127.0.0.1:11434"
	assert remoto.direccion_ollama(lambda _c, _d: [MAC]) == ""
	assert remoto.equipo_ollama(lambda _c, _d: [MAC, {**MAC, "id": "linux", "ollama": True}]).id == "linux"


# ── Agentes de verdad (falsos) a través del ssh falso ────────────────────────


@pytest.fixture
def ssh_falso(tmp_path, monkeypatch):
	"""``ssh.exe`` sustituido por ``fake_ssh.py``; devuelve el archivo donde anota cada llamada."""
	registro = tmp_path / "ssh.jsonl"
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path(sys.executable))
	monkeypatch.setattr(remoto, "opciones_ssh", lambda equipo, terminal=False: [str(FAKE_SSH)])
	monkeypatch.setattr(remoto, "_opcion", lambda clave, defecto: [MAC])
	monkeypatch.setenv("FAKE_SSH_REGISTRO", str(registro))
	return registro


def _llamadas(registro: Path) -> list[dict]:
	return [json.loads(linea) for linea in registro.read_text(encoding="utf-8").splitlines()]


EXTRA = {"remoto": {"equipo": "mac-del-estudio", "nombre": "Mac del estudio", "carpeta": "/Users/ana/proyectos/web"}}


def test_claude_en_otro_equipo(ssh_falso, tmp_path, monkeypatch):
	from app.services.agentes.claude import ProveedorClaude

	monkeypatch.setenv("FAKE_SSH_PROGRAMAS", json.dumps({"claude": [sys.executable, str(FAKES / "fake_claude.py")]}))
	registro_claude = tmp_path / "claude.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro_claude))
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	monkeypatch.setenv("ANTHROPIC_API_KEY", "secreta")
	fin = threading.Event()
	eventos = []

	def recibir(evento):
		eventos.append(evento)
		if evento.tipo is TipoEvento.FIN_TURNO:
			fin.set()

	proveedor = ProveedorClaude(orden_base=[r"C:\no\existe\claude.exe"])
	ajustes = AjustesSesion(carpeta=PurePosixPath("/Users/ana/proyectos/web"), titulo="Web 'del' Mac", extra=dict(EXTRA))
	conversacion = proveedor.crear_conversacion(ajustes, recibir)
	try:
		conversacion.enviar("¿Qué hay en esta carpeta?")
		assert fin.wait(20)
		assert [e for e in eventos if e.tipo is TipoEvento.MENSAJE][-1].texto == "Hola\u2028mundo"
		llamada = _llamadas(ssh_falso)[0]
		assert llamada["destino"] == "ana@192.168.1.40"
		assert llamada["carpeta"] == "/Users/ana/proyectos/web"
		assert llamada["programa"] == "claude"
		assert "ANTHROPIC_API_KEY" in llamada["quitar"]
		assert llamada["anadir"]["GEMINI_FORCE_FILE_STORAGE"] == "true"
		assert llamada["argumentos"][llamada["argumentos"].index("--name") + 1] == "Web 'del' Mac"
		primera = json.loads(registro_claude.read_text(encoding="utf-8").splitlines()[0])
		assert primera["clave"] is False  # la clave de API no llega al agente del otro equipo
	finally:
		conversacion.cerrar()


def test_codex_en_otro_equipo(ssh_falso, tmp_path, monkeypatch):
	from app.services.agentes.codex import ProveedorCodex

	registro_codex = tmp_path / "codex.jsonl"
	monkeypatch.setenv("FAKE_SSH_PROGRAMAS", json.dumps(
		{"codex": [sys.executable, str(FAKES / "fake_codex.py"), "normal", str(registro_codex)]}))
	fin = threading.Event()

	def recibir(evento):
		if evento.tipo is TipoEvento.FIN_TURNO:
			fin.set()

	proveedor = ProveedorCodex(orden=[r"C:\no\existe\codex.exe"])
	conversacion = proveedor.crear_conversacion(
		AjustesSesion(carpeta=PurePosixPath("/Users/ana/proyectos/web"), extra=dict(EXTRA)), recibir)
	try:
		conversacion.enviar("hola")
		assert fin.wait(20)
		llamada = _llamadas(ssh_falso)[0]
		assert llamada["programa"] == "codex" and llamada["argumentos"][:1] == ["app-server"]
		assert "OPENAI_API_KEY" in llamada["quitar"]
		mensajes = [json.loads(linea) for linea in registro_codex.read_text(encoding="utf-8").splitlines()]
		hilo = next(m for m in mensajes if m.get("method") == "thread/start")
		assert hilo["params"]["cwd"] == "/Users/ana/proyectos/web"
	finally:
		conversacion.cerrar()
		proveedor.cerrar()


def test_agente_que_no_esta_en_el_otro_equipo(ssh_falso, monkeypatch):
	from app.services.agentes.claude import ProveedorClaude

	monkeypatch.setenv("FAKE_SSH_PROGRAMAS", "{}")
	fin = threading.Event()
	eventos = []

	def recibir(evento):
		eventos.append(evento)
		if evento.tipo in (TipoEvento.FIN_TURNO, TipoEvento.ERROR):
			fin.set()

	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(
		AjustesSesion(carpeta=PurePosixPath("/Users/ana"), extra=dict(EXTRA)), recibir)
	try:
		conversacion.enviar("hola")
		assert fin.wait(20)
	finally:
		conversacion.cerrar()


def test_sesion_de_un_equipo_borrado(monkeypatch):
	from app.services.agentes.claude import ProveedorClaude

	monkeypatch.setattr(remoto, "_opcion", lambda clave, defecto: [])
	monkeypatch.setattr(remoto, "buscar_ssh", lambda: Path("ssh.exe"))
	fin = threading.Event()
	eventos = []

	def recibir(evento):
		eventos.append(evento)
		if evento.tipo in (TipoEvento.FIN_TURNO, TipoEvento.ERROR):
			fin.set()

	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(
		AjustesSesion(carpeta=PurePosixPath("/Users/ana"), extra=dict(EXTRA)), recibir)
	try:
		conversacion.enviar("hola")
		assert fin.wait(20)
		textos = " ".join(e.texto or "" for e in eventos if e.tipo is TipoEvento.ERROR)
		assert "Equipos por SSH" in textos
	finally:
		conversacion.cerrar()
