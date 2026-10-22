"""Pruebas de los lectores de historial de Antigravity (app y CLI), de la app de Ollama y de Codex en disco.

Todo con datos inventados en carpetas temporales: nunca se leen los archivos reales de los agentes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.historial import antigravity as h_agy
from app.services.historial import codex as h_codex
from app.services.historial import comun
from app.services.historial import ollama as h_ollama

ID_APP = "11111111-2222-3333-4444-555555555555"
ID_APP_SIN_RESUMEN = "aaaaaaaa-2222-3333-4444-555555555555"
ID_CLI = "66666666-7777-8888-9999-000000000000"
ID_PROPIO = "bbbbbbbb-7777-8888-9999-000000000000"


# ── Protobuf mínimo para inventar el archivo de resúmenes de la app ──────────


def _varint(valor: int) -> bytes:
	salida = bytearray()
	while True:
		octeto = valor & 0x7F
		valor >>= 7
		salida.append(octeto | (0x80 if valor else 0))
		if not valor:
			return bytes(salida)


def _campo(numero: int, valor: bytes | int) -> bytes:
	if isinstance(valor, int):
		return _varint(numero << 3) + _varint(valor)
	return _varint(numero << 3 | 2) + _varint(len(valor)) + valor


def _resumen_app(identificador: str, titulo: str, pasos: int, segundos: int, uri: str) -> bytes:
	resumen = (_campo(1, titulo.encode()) + _campo(2, pasos) + _campo(3, _campo(1, segundos) + _campo(2, 5))
		+ _campo(9, _campo(1, uri.encode()) + _campo(3, b"")) + _campo(22, 4))
	return _campo(1, _campo(1, identificador.encode()) + _campo(2, resumen))


@pytest.fixture
def gemini(tmp_path: Path) -> Path:
	raiz = tmp_path / ".gemini"
	app = raiz / "antigravity"
	cli = raiz / "antigravity-cli"
	for carpeta in (app / "conversations", app / "brain", cli / "conversations", cli / "brain"):
		carpeta.mkdir(parents=True)
	for identificador in (ID_APP, ID_APP_SIN_RESUMEN):
		(app / "conversations" / f"{identificador}.pb").write_bytes(b"\x00")
	for identificador in (ID_CLI, ID_PROPIO):
		(cli / "conversations" / f"{identificador}.pb").write_bytes(b"\x00")
	(app / "agyhub_summaries_proto.pb").write_bytes(
		_resumen_app(ID_APP, "Arreglar el chat", 42, 1_780_000_000, "file:///e:/Proyectos/Ajedrez%20NVDA"))
	base = sqlite3.connect(cli / "conversation_summaries.db")
	base.execute("CREATE TABLE conversation_summaries (conversation_id text PRIMARY KEY, title text NOT NULL DEFAULT '', "
		"preview text NOT NULL DEFAULT '', step_count integer NOT NULL DEFAULT 0, last_modified_time datetime NOT NULL, "
		"workspace_uris text NOT NULL, app_data_dir text NOT NULL DEFAULT '')")
	base.execute("INSERT INTO conversation_summaries VALUES (?, '', 'Probar la CLI', 7, '2026-07-25 09:58:09.4060498+00:00', ?, "
		"'antigravity-cli')", (ID_CLI, json.dumps(["file:///e%3A/Proyectos/Web"])))
	base.commit()
	base.close()
	return raiz


def test_fechas():
	assert comun.fecha("2026-07-25 09:58:09.4060498+00:00") == pytest.approx(1_784_973_489.406, abs=0.01)
	assert comun.fecha("2026-10-08T07:39:42.294Z") > 0
	assert comun.fecha("2026-10-18 11:22:33") == comun.fecha("2026-10-18T11:22:33Z")
	assert comun.fecha("2026-10-18 11:22:33.123456789 +0200 CEST") > 0
	assert comun.fecha("basura") == 0.0 and comun.fecha(None) == 0.0 and comun.fecha(12) == 12.0


def test_carpeta_de_uris():
	assert h_agy.carpeta_de_uris('["file:///e%3A/Proyectos/Mi%20juego"]') == r"E:\Proyectos\Mi juego"
	assert h_agy.carpeta_de_uris('["https://x", "file:///c:/a"]') == "C:\\a"
	assert h_agy.carpeta_de_uris("no es json") == "" and h_agy.carpeta_de_uris("[]") == ""


def test_antigravity_app_y_cli(gemini):
	conversaciones = {c["id"]: c for c in h_agy.listar_conversaciones(gemini, propios={ID_PROPIO})}
	assert set(conversaciones) == {ID_APP, ID_APP_SIN_RESUMEN, ID_CLI}
	app = conversaciones[ID_APP]
	assert (app["titulo"], app["carpeta"], app["pasos"], app["origen"]) == ("Arreglar el chat", r"E:\Proyectos\Ajedrez NVDA", 42, "antigravity")
	assert app["fecha"].timestamp() == 1_780_000_000  # la del resumen, no la del archivo
	cli = conversaciones[ID_CLI]
	assert (cli["titulo"], cli["carpeta"], cli["pasos"], cli["origen"]) == ("Probar la CLI", r"E:\Proyectos\Web", 7, "antigravity_cli")
	assert conversaciones[ID_APP_SIN_RESUMEN]["titulo"].startswith("Conversación de Antigravity del")
	assert "Continuar aquí" in h_agy.texto_de_aviso(cli) and "Pasos del agente: 7." in h_agy.texto_de_aviso(cli)
	assert "Control+O" in h_agy.texto_de_aviso(app)


def test_antigravity_se_puede_retomar_solo_la_cli(gemini):
	assert h_agy.se_puede_retomar(ID_CLI, gemini)
	assert not h_agy.se_puede_retomar(ID_APP, gemini)  # agy empezaría otra en silencio
	assert not h_agy.se_puede_retomar("../../etc", gemini) and not h_agy.se_puede_retomar("", gemini)


def test_gestor_continuar_aqui_antigravity(tmp_path, gemini, monkeypatch):
	from app.services.agentes.antigravity import ProveedorAntigravity
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones

	monkeypatch.setattr(h_agy, "raiz_gemini", lambda: gemini)
	almacen = Almacen(tmp_path / "a.db")
	try:
		gestor = GestorSesiones(almacen, {"antigravity": ProveedorAntigravity(ejecutable=["agy.exe"])}, lambda c, e: None,
			lambda clave, defecto: (clave == "historial.antigravity") if clave.startswith("historial.") else defecto)
		with gestor._candado:
			gestor._externas[f"antigravity:{ID_CLI}"] = {"titulo": "Probar la CLI", "carpeta": str(tmp_path), "origen": "antigravity_cli"}
			gestor._externas[f"antigravity:{ID_APP}"] = {"titulo": "Arreglar el chat", "carpeta": str(tmp_path), "origen": "antigravity"}
		clave = gestor.continuar_aqui(f"antigravity:{ID_CLI}")
		sesion = almacen.sesion(clave)
		# Retoma la misma conversación (no la copia): agy --conversation <id>.
		assert sesion.id_externo == ID_CLI and sesion.bifurcada_de == "" and sesion.carpeta == str(tmp_path)
		assert sesion.titulo == "Probar la CLI (continuación)"
		# Ya es de Ctrl+IA: deja de salir aparte en la lista.
		assert f"antigravity:{ID_CLI}" not in {f.clave for f in gestor.listar()}
		with pytest.raises(ValueError, match="app de escritorio"):
			gestor.continuar_aqui(f"antigravity:{ID_APP}")
	finally:
		almacen.cerrar()


def test_antigravity_resumenes_estropeados_no_fallan(gemini):
	(gemini / "antigravity" / "agyhub_summaries_proto.pb").write_bytes(b"\x0f\xff\xff")
	(gemini / "antigravity-cli" / "conversation_summaries.db").write_bytes(b"no es sqlite")
	conversaciones = h_agy.listar_conversaciones(gemini)
	assert len(conversaciones) == 4 and all(c["carpeta"] == "" for c in conversaciones)


def test_antigravity_sin_carpetas(tmp_path):
	assert h_agy.listar_conversaciones(tmp_path / "no-existe") == []


# ── App de Ollama ────────────────────────────────────────────────────────────


@pytest.fixture
def base_ollama(tmp_path: Path) -> Path:
	ruta = tmp_path / "Ollama" / "db.sqlite"
	ruta.parent.mkdir()
	base = sqlite3.connect(ruta)
	base.executescript("""
		CREATE TABLE chats (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, browser_state TEXT);
		CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, role TEXT NOT NULL,
			content TEXT NOT NULL DEFAULT '', thinking TEXT NOT NULL DEFAULT '', stream BOOLEAN NOT NULL DEFAULT 0,
			model_name TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, tool_result TEXT);
		INSERT INTO chats (id, title, created_at) VALUES ('chat-1', '', '2026-10-01 10:00:00'), ('chat-2', 'Recetas', '2026-10-02 10:00:00'),
			('vacio', 'Nada', '2026-10-03 10:00:00'), ('mal id;', 'X', '2026-10-03 10:00:00');
		INSERT INTO messages (chat_id, role, content, thinking, model_name, created_at, updated_at) VALUES
			('chat-1', 'user', '¿Qué es una canción protesta?', '', 'llama3.2', '2026-10-01 10:00:01', '2026-10-01 10:00:01'),
			('chat-1', 'assistant', 'Una canción con mensaje social.', 'Pienso...', 'llama3.2', '2026-10-01 10:00:05', '2026-10-05 10:00:05'),
			('chat-2', 'user', 'Una receta de tortilla', '', 'gemma3', '2026-10-02 10:00:01', '2026-10-02 10:00:01'),
			('mal id;', 'user', 'hola', '', 'x', '2026-10-03 10:00:01', '2026-10-03 10:00:01');
	""")
	base.commit()
	base.close()
	return ruta


def test_ollama_lista_y_mensajes(base_ollama):
	chats = h_ollama.listar_chats(base_ollama)
	assert [c["id"] for c in chats] == ["chat-1", "chat-2"]  # sin el vacío ni el id raro; el más reciente primero
	assert chats[0]["titulo"] == "¿Qué es una canción protesta?" and chats[0]["modelo"] == "llama3.2"
	assert chats[0]["ultimo"] == "Una canción con mensaje social."
	mensajes = h_ollama.leer_mensajes("chat-1", base_ollama)
	assert [(m["rol"], m["razonamiento"]) for m in mensajes] == [("usuario", ""), ("asistente", "Pienso...")]
	assert h_ollama.historial_para_ollama("chat-1", base_ollama) == [
		{"role": "user", "content": "¿Qué es una canción protesta?"},
		{"role": "assistant", "content": "Una canción con mensaje social."}]
	encontrados = h_ollama.buscar({"chat-1": "", "chat-2": ""}, lambda t: "tortilla" in t, ruta=base_ollama)
	assert [(i, m["texto"]) for i, m in encontrados] == [("chat-2", "Una receta de tortilla")]
	assert h_ollama.leer_mensajes("x' OR 1=1 --", base_ollama) == []
	assert h_ollama.listar_chats(base_ollama.parent / "no.sqlite") == []


def test_ollama_continuar_un_chat_de_la_app(base_ollama, monkeypatch):
	from app.services.agentes import ollama as agente_ollama

	monkeypatch.setattr(h_ollama, "ruta_base", lambda: base_ollama)
	assert agente_ollama.leer_historial("app-chat-2") == [{"role": "user", "content": "Una receta de tortilla"}]


# ── Codex en disco ───────────────────────────────────────────────────────────

ID_HILO = "01a11a74-248b-7132-acea-63f89019e613"
ID_ANTIGUO = "01a12222-248b-7132-acea-63f89019e613"


def _evento(tipo: str, carga: dict, momento: str = "2026-10-08T07:39:42.294Z") -> str:
	return json.dumps({"timestamp": momento, "type": tipo, "payload": carga}, ensure_ascii=False)


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
	casa = tmp_path / ".codex"
	dia = casa / "sessions" / "2026" / "10" / "08"
	dia.mkdir(parents=True)
	lineas = [
		_evento("session_meta", {"id": ID_HILO}),
		_evento("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>"}]}),
		_evento("event_msg", {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "Arregla la canción"}]}}),
		_evento("event_msg", {"type": "item_completed", "item": {"type": "CommandExecution", "aggregated_output": "canción " * 50000}}),
		_evento("event_msg", {"type": "item_completed", "item": {"type": "AgentMessage", "content": [{"type": "Text", "text": "He arreglado "}, {"type": "Text", "text": "la cancion."}]}}),
		_evento("event_msg", {"type": "user_message", "message": "duplicado que no se usa"}),
		"{roto",
	]
	(dia / f"rollout-2026-10-08T09-39-41-{ID_HILO}.jsonl").write_text("\n".join(lineas), encoding="utf-8")
	archivado = casa / "archived_sessions"
	archivado.mkdir()
	antiguas = [_evento("event_msg", {"type": "user_message", "message": "Hola, Codex"}),
		_evento("event_msg", {"type": "agent_message", "message": "Hola, ¿qué hacemos?"})]
	(archivado / f"rollout-2025-01-01T00-00-00-{ID_ANTIGUO}.jsonl").write_text("\n".join(antiguas), encoding="utf-8")
	(dia / "otro.jsonl").write_text("{}", encoding="utf-8")
	return casa


def test_codex_indice_y_mensajes(codex_home):
	indice = h_codex.indice_rollouts(codex_home)
	assert set(indice) == {ID_HILO, ID_ANTIGUO}
	mensajes = h_codex.leer_mensajes(indice[ID_HILO])
	assert [(m["rol"], m["texto"]) for m in mensajes] == [("usuario", "Arregla la canción"), ("asistente", "He arreglado la cancion.")]
	assert mensajes[0]["fecha"] > 0
	antiguos = h_codex.leer_mensajes(indice[ID_ANTIGUO])
	assert [m["rol"] for m in antiguos] == ["usuario", "asistente"]


def test_codex_buscar(codex_home, monkeypatch):
	from app.services.gestor import coincide

	monkeypatch.setenv("CODEX_HOME", str(codex_home))
	resultados = h_codex.buscar_en_hilos([ID_HILO, ID_ANTIGUO, "no-existe"], lambda t: coincide(t, "cancion"))
	assert [(i, m["rol"]) for i, m in resultados] == [(ID_HILO, "usuario"), (ID_HILO, "asistente")]
	assert h_codex.buscar_en_hilos([ID_HILO], lambda t: True, maximo=1) and len(h_codex.buscar_en_hilos([ID_HILO], lambda t: True, maximo=1)) == 1
	assert h_codex.buscar_en_hilos([ID_HILO], lambda t: True, cancelado=lambda: True) == []


# ── Integración con el gestor ────────────────────────────────────────────────


def test_gestor_con_ollama_antigravity_y_codex(tmp_path, base_ollama, gemini, codex_home, monkeypatch):
	from app.services.agentes.ollama import ProveedorOllama
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones

	monkeypatch.setattr(h_ollama, "ruta_base", lambda: base_ollama)
	monkeypatch.setattr(h_agy, "raiz_gemini", lambda: gemini)
	monkeypatch.setenv("CODEX_HOME", str(codex_home))
	activos = {"historial.ollama", "historial.antigravity"}
	almacen = Almacen(tmp_path / "g.db")
	try:
		gestor = GestorSesiones(almacen, {"ollama": ProveedorOllama("http://127.0.0.1:9")}, lambda c, e: None,
			lambda clave, defecto: (clave in activos) if clave.startswith("historial.") else defecto)
		filas = {f.clave: f for f in gestor.listar()}
		chat = filas["ollama:app-chat-1"]
		assert chat.nombre_origen == "App de Ollama" and chat.modelo == "llama3.2" and not chat.propia
		assert filas[f"antigravity:{ID_CLI}"].nombre_origen == "Antigravity CLI"
		assert filas[f"antigravity:{ID_APP}"].carpeta == r"E:\Proyectos\Ajedrez NVDA"
		assert [m.rol for m in gestor.mensajes("ollama:app-chat-1")] == ["usuario", "asistente"]
		assert "Continuar aquí" in gestor.mensajes(f"antigravity:{ID_CLI}")[0].texto
		# Buscar: también en los chats de la app de Ollama y en los hilos de Codex conocidos.
		with gestor._candado:
			gestor._externas[f"codex:{ID_HILO}"] = {"titulo": "Arreglos"}
		resultados = gestor.buscar("cancion")
		assert ("codex:" + ID_HILO, "Arreglos") in [(c, t) for c, t, _m in resultados]
		assert any(c == "ollama:app-chat-1" for c, _t, _m in resultados)
		# Continuar aquí un chat de la app de Ollama: sesión propia que bifurca el chat.
		clave = gestor.continuar_aqui("ollama:app-chat-1")
		sesion = almacen.sesion(clave)
		assert sesion.proveedor == "ollama" and sesion.bifurcada_de == "app-chat-1" and sesion.modelo == "llama3.2"
		assert sesion.carpeta == str(Path.home())
	finally:
		almacen.cerrar()
