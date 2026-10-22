"""Pruebas del lector de historial de Claude Code (``app.services.historial.claude``).

Todo con datos SINTÉTICOS en ``tmp_path``: ``CLAUDE_CONFIG_DIR``, ``APPDATA``
y ``LOCALAPPDATA`` se redirigen, así que nunca se leen los datos reales.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.services.historial import claude as hc

CARPETA = "C:\\Proyectos\\demo"


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------


@pytest.fixture
def entorno(tmp_path, monkeypatch):
	"""Raíces falsas de Claude Code y de la aplicación de escritorio."""
	raiz = tmp_path / "claude"
	(raiz / "projects").mkdir(parents=True)
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(raiz))
	monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
	monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
	# Ningún pid de las pruebas está vivo salvo que la prueba lo diga.
	monkeypatch.setattr(hc, "_proceso_vivo", lambda pid: False)
	monkeypatch.setattr(hc, "_inicio_proceso", lambda pid: None)
	hc._cache_datos.clear()
	return tmp_path


def sello(segundos: int = 0) -> str:
	return "2026-10-01T10:00:%02d.000Z" % segundos


def usuario(texto, uuid="u1", segundos=0, **extra) -> dict:
	registro = {
		"type": "user",
		"uuid": uuid,
		"timestamp": sello(segundos),
		"message": {"role": "user", "content": texto},
	}
	registro.update(extra)
	return registro


def asistente(bloques, msg_id="m1", uuid="a1", segundos=1, uso=None, **extra) -> dict:
	mensaje = {"role": "assistant", "id": msg_id, "content": bloques}
	if uso is not None:
		mensaje["usage"] = uso
	registro = {"type": "assistant", "uuid": uuid, "timestamp": sello(segundos), "message": mensaje}
	registro.update(extra)
	return registro


def texto(t: str) -> dict:
	return {"type": "text", "text": t}


def llamada(nombre: str, ident: str, entrada: dict) -> dict:
	return {"type": "tool_use", "id": ident, "name": nombre, "input": entrada}


def resultado(ident: str, contenido, uuid="r1", es_error=False, tool_use_result=None, **extra) -> dict:
	bloque = {"type": "tool_result", "tool_use_id": ident, "content": contenido}
	if es_error:
		bloque["is_error"] = True
	registro = usuario([bloque], uuid=uuid, segundos=2, **extra)
	if tool_use_result is not None:
		registro["toolUseResult"] = tool_use_result
	return registro


def linea(registro) -> bytes:
	return (json.dumps(registro, ensure_ascii=False) + "\n").encode("utf-8")


def escribir(ruta: Path, registros, modo="wb") -> Path:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	with open(ruta, modo) as archivo:
		for registro in registros:
			archivo.write(registro if isinstance(registro, bytes) else linea(registro))
	return ruta


def transcript(raiz: Path, session_id: str, registros, carpeta: str = CARPETA) -> Path:
	ruta = raiz / "claude" / "projects" / hc.codificar_carpeta(carpeta) / ("%s.jsonl" % session_id)
	return escribir(ruta, registros)


def con_entrada(registro: dict, entrada: str, carpeta: str = CARPETA) -> dict:
	registro = dict(registro)
	registro["entrypoint"] = entrada
	registro["cwd"] = carpeta
	registro["sessionId"] = "x"
	return registro


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------


def test_fusiona_respuestas_por_message_id_y_uso_de_contexto(tmp_path):
	ruta = escribir(
		tmp_path / "t.jsonl",
		[
			usuario("Hola"),
			asistente([texto("Primera parte.")], msg_id="m1", uuid="a1", uso={"input_tokens": 5}),
			asistente(
				[texto("Segunda parte.")],
				msg_id="m1",
				uuid="a2",
				uso={"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20, "output_tokens": 999},
			),
			asistente([texto("Otra respuesta.")], msg_id="m2", uuid="a3"),
		],
	)
	lector = hc.LectorTranscript(ruta)
	mensajes = lector.leer_todo()
	assert [m.rol for m in mensajes] == ["usuario", "asistente", "asistente"]
	assert mensajes[1].texto == "Primera parte.\n\nSegunda parte."
	assert mensajes[1].id == "m1"
	assert mensajes[0].fecha == pytest.approx(1790848800.0)
	# Entrada + caché leída + caché creada, sin los tokens de salida.
	assert lector.uso_contexto == 130
	assert lector.errores == 0


def test_herramientas_emparejadas_con_su_resultado_y_cambios(tmp_path):
	parche = [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": ["-viejo", "+nuevo"]}]
	ruta = escribir(
		tmp_path / "t.jsonl",
		[
			usuario("Arregla"),
			asistente(
				[
					texto("Miro el estado."),
					llamada("Bash", "t1", {"command": "git status", "description": "Ver estado"}),
				]
			),
			resultado("t1", "En la rama main", uuid="r1"),
			asistente([llamada("Edit", "t2", {"file_path": "C:\\a.py", "old_string": "viejo", "new_string": "nuevo"})], msg_id="m2", uuid="a2"),
			resultado("t2", "ok", uuid="r2", tool_use_result={"filePath": "C:\\a.py", "structuredPatch": parche}),
			asistente([llamada("Read", "t3", {"file_path": "C:\\b.py"})], msg_id="m3", uuid="a3"),
			resultado("t3", "no existe", uuid="r3", es_error=True),
			asistente([llamada("mcp__x__hacer", "t4", {})], msg_id="m4", uuid="a4"),
		],
	)
	mensajes = hc.leer_mensajes(ruta)
	herramientas = [m for m in mensajes if m.rol == "herramienta"]
	assert [m.texto for m in herramientas] == [
		"Ejecutar: git status",
		"Editar: C:\\a.py",
		"Leer: C:\\b.py (error)",
		"Herramienta mcp__x__hacer",
	]
	bash = herramientas[0]
	assert bash.herramienta == "Bash"
	assert bash.id == "t1"
	assert "git status" in bash.detalle and "En la rama main" in bash.detalle
	assert herramientas[1].cambios == [{"ruta": "C:\\a.py", "diff": "@@ -1,1 +1,1 @@\n-viejo\n+nuevo"}]
	assert "no existe" in herramientas[2].detalle
	# Ningún resultado de herramienta se cuela como mensaje del usuario.
	assert [m.texto for m in mensajes if m.rol == "usuario"] == ["Arregla"]


def test_pregunta_y_respuesta_salida_de_comando_e_interrupcion(tmp_path):
	pregunta = {"questions": [{"question": "¿Qué base de datos?", "options": [{"label": "SQLite"}, {"label": "Postgres"}]}]}
	ruta = escribir(
		tmp_path / "t.jsonl",
		[
			asistente([llamada("AskUserQuestion", "q1", pregunta)]),
			resultado("q1", "respuesta", tool_use_result={"answers": {"¿Qué base de datos?": "SQLite"}}),
			usuario("<local-command-stdout>\x1b[1mModelo cambiado\x1b[0m</local-command-stdout>", uuid="u2"),
			usuario([texto("[Request interrupted by user for tool use]")], uuid="u3"),
			usuario("<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args>opus</command-args>", uuid="u4"),
		],
	)
	mensajes = hc.leer_mensajes(ruta)
	assert mensajes[0].rol == "asistente"
	assert "¿Qué base de datos?" in mensajes[0].texto and "SQLite; Postgres" in mensajes[0].texto
	assert mensajes[1].rol == "usuario" and "SQLite" in mensajes[1].texto
	assert (mensajes[2].rol, mensajes[2].texto) == ("sistema", "Salida de comando:\nModelo cambiado")
	assert (mensajes[3].rol, mensajes[3].texto) == ("sistema", "Interrumpido por el usuario")
	assert (mensajes[4].rol, mensajes[4].texto) == ("usuario", "Orden: /model opus")


def test_elimina_system_reminder_y_trata_meta_como_contexto(tmp_path):
	ruta = escribir(
		tmp_path / "t.jsonl",
		[
			usuario("Antes <system-reminder>secreto\ninterno</system-reminder> después", uuid="u1"),
			usuario("<system-reminder>solo recordatorio</system-reminder>", uuid="u2"),
			usuario("Instrucciones de una habilidad", uuid="u3", isMeta=True),
			usuario("Resumen previo", uuid="u4", isCompactSummary=True),
			asistente([texto("Vale <system-reminder>x</system-reminder>")]),
			{"type": "user", "isSidechain": True, "uuid": "s", "message": {"role": "user", "content": "de un subagente"}},
		],
	)
	mensajes = hc.leer_mensajes(ruta)
	assert [(m.rol, m.texto) for m in mensajes] == [
		("usuario", "Antes  después"),
		("sistema", "Contexto: Instrucciones de una habilidad"),
		("sistema", "Resumen de la conversación anterior:\nResumen previo"),
		("asistente", "Vale"),
	]
	assert not any("secreto" in m.texto for m in mensajes)


def test_u2028_dentro_de_un_texto_no_parte_el_registro(tmp_path):
	contenido = "línea uno\u2028línea dos\u2029fin"
	ruta = escribir(tmp_path / "t.jsonl", [usuario(contenido), asistente([texto("ok")])])
	assert "\u2028".encode("utf-8") in ruta.read_bytes()
	lector = hc.LectorTranscript(ruta)
	mensajes = lector.leer_todo()
	assert lector.errores == 0
	assert mensajes[0].texto == contenido
	assert len(mensajes) == 2


def test_linea_corrupta_se_cuenta_y_se_salta(tmp_path):
	ruta = escribir(
		tmp_path / "t.jsonl",
		[usuario("uno"), b'{"type": "user", "mess\n', b"[1, 2]\n", b"\xff\xfe basura\n", asistente([texto("dos")])],
	)
	lector = hc.LectorTranscript(ruta)
	mensajes = lector.leer_todo()
	assert [m.texto for m in mensajes] == ["uno", "dos"]
	assert lector.errores == 3


def test_compactacion_reinicia_el_uso_de_contexto(tmp_path):
	ruta = escribir(
		tmp_path / "t.jsonl",
		[
			asistente([texto("a")], uso={"input_tokens": 50_000}),
			{"type": "system", "subtype": "compact_boundary", "timestamp": sello(3), "compactMetadata": {"postTokens": 1200}},
		],
	)
	lector = hc.LectorTranscript(ruta)
	mensajes = lector.leer_todo()
	assert mensajes[-1].rol == "sistema" and "compactada" in mensajes[-1].texto
	assert lector.uso_contexto == 1200


# ---------------------------------------------------------------------------
# Lector incremental
# ---------------------------------------------------------------------------


def test_lector_incremental_solo_procesa_lo_anadido(tmp_path):
	ruta = escribir(
		tmp_path / "t.jsonl",
		[usuario("pregunta"), asistente([texto("Voy."), llamada("Bash", "t1", {"command": "dir"})])],
	)
	lector = hc.LectorTranscript(ruta)
	primeros = lector.leer_nuevo()
	assert [m.rol for m in primeros] == ["usuario", "asistente", "herramienta"]
	assert lector.leer_nuevo() == []

	# Llega el resultado y media línea más (aún escribiéndose).
	with open(ruta, "ab") as archivo:
		archivo.write(linea(resultado("t1", "listado")))
		archivo.write(b'{"type": "assistant", "uuid": "a9", ')
	nuevos = lector.leer_nuevo()
	assert nuevos == []  # el resultado completa una herramienta ya devuelta
	assert len(lector.modificados) == 1 and lector.modificados[0] is primeros[2]
	assert "listado" in primeros[2].detalle

	# Se completa la línea a medias y llega otra.
	with open(ruta, "ab") as archivo:
		archivo.write(b'"timestamp": "2026-10-01T10:00:05Z", "message": {"role": "assistant", "id": "m9", "content": [{"type": "text", "text": "Hecho."}]}}\n')
	nuevos = lector.leer_nuevo()
	assert [(m.rol, m.texto) for m in nuevos] == [("asistente", "Hecho.")]
	assert lector.errores == 0
	assert len(lector.leer_todo()) == 4
	assert not lector.reiniciado


def test_lector_continua_la_fusion_entre_lecturas(tmp_path):
	ruta = escribir(tmp_path / "t.jsonl", [asistente([texto("Uno.")], msg_id="m1", uuid="a1")])
	lector = hc.LectorTranscript(ruta)
	(primero,) = lector.leer_nuevo()
	escribir(ruta, [asistente([texto("Dos.")], msg_id="m1", uuid="a2")], modo="ab")
	assert lector.leer_nuevo() == []
	assert lector.modificados == [primero]
	assert primero.texto == "Uno.\n\nDos."


def test_archivo_que_encoge_reinicia_la_lectura(tmp_path):
	ruta = escribir(tmp_path / "t.jsonl", [usuario("a", uuid="1"), usuario("b", uuid="2"), usuario("c", uuid="3")])
	lector = hc.LectorTranscript(ruta)
	assert len(lector.leer_nuevo()) == 3
	escribir(ruta, [usuario("z", uuid="9")])  # reescrito, más corto
	nuevos = lector.leer_nuevo()
	assert lector.reiniciado
	assert [m.texto for m in nuevos] == ["z"]
	assert [m.texto for m in lector.leer_todo()] == ["z"]


def test_archivo_reescrito_del_mismo_tamano_se_detecta(tmp_path):
	ruta = escribir(tmp_path / "t.jsonl", [usuario("aaaa", uuid="1")])
	lector = hc.LectorTranscript(ruta)
	lector.leer_nuevo()
	escribir(ruta, [usuario("bbbb", uuid="1"), usuario("nuevo", uuid="2")])
	nuevos = lector.leer_nuevo()
	assert lector.reiniciado
	assert [m.texto for m in nuevos] == ["bbbb", "nuevo"]


def test_lector_de_archivo_inexistente_no_falla(tmp_path):
	lector = hc.LectorTranscript(tmp_path / "no-existe.jsonl")
	assert lector.leer_nuevo() == []
	assert lector.uso_contexto is None


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def test_ruta_transcript_directa_y_por_busqueda(entorno):
	assert hc.codificar_carpeta("C:\\Users\\ana\\mi_proy.v2") == "C--Users-ana-mi-proy-v2"
	directa = transcript(entorno, "abc-1", [usuario("x")])
	assert hc.ruta_transcript("abc-1", CARPETA) == directa
	otra = transcript(entorno, "abc-2", [usuario("x")], carpeta="D:\\otra")
	assert hc.ruta_transcript("abc-2", CARPETA) == otra  # no está donde se espera: se busca
	assert hc.ruta_transcript("no-hay") is None
	assert hc.ruta_transcript("../malo") is None
	assert hc.raiz_claude() == entorno / "claude"


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------


def test_titulos_de_sesiones_de_terminal(entorno):
	inicio = con_entrada(usuario("Primer mensaje " + "largo " * 30, uuid="u0"), "cli")
	transcript(
		entorno,
		"s-propio",
		[
			inicio,
			{"type": "ai-title", "aiTitle": "Título de la IA"},
			{"type": "custom-title", "customTitle": "Viejo"},
			asistente([texto("Respuesta final\nsegunda línea")]),
			{"type": "custom-title", "customTitle": "Mi título"},
		],
	)
	transcript(entorno, "s-ia", [inicio, {"type": "ai-title", "aiTitle": "Título de la IA"}])
	transcript(entorno, "s-primero", [inicio])
	sesiones = {s.id: s for s in hc.listar_sesiones()}
	assert set(sesiones) == {"s-propio", "s-ia", "s-primero"}
	assert sesiones["s-propio"].titulo == "Mi título"
	assert sesiones["s-ia"].titulo == "Título de la IA"
	primero = sesiones["s-primero"].titulo
	assert primero.startswith("Primer mensaje largo") and len(primero) <= 80
	propio = sesiones["s-propio"]
	assert propio.origen == "terminal"
	assert propio.clave == "claude:s-propio"
	assert propio.carpeta == CARPETA
	assert propio.ultimo_mensaje == "Respuesta final"
	assert propio.estado == "inactiva"
	assert propio.creada == pytest.approx(1790848800.0)
	assert propio.ruta_transcript is not None and propio.ruta_transcript.name == "s-propio.jsonl"
	assert hc.url_abrir_en_claude(propio) is None


def test_origenes_sdk_excluido_y_ctrlia_incluido(entorno):
	transcript(entorno, "sdk-ajeno", [con_entrada(usuario("script"), "sdk-cli")])
	transcript(entorno, "sdk-propio", [con_entrada(usuario("desde Ctrl+IA"), "sdk-cli")])
	transcript(entorno, "vscode", [con_entrada(usuario("desde el editor"), "claude-vscode")])
	transcript(entorno, "huerfana", [con_entrada(usuario("escritorio borrado"), "claude-desktop")])
	sesiones = {s.id: s.origen for s in hc.listar_sesiones(ids_propios={"sdk-propio"})}
	assert sesiones == {"sdk-propio": "ctrlia", "vscode": "otra"}


def test_estado_vivo_solo_si_el_proceso_vive(entorno, monkeypatch):
	transcript(entorno, "viva", [con_entrada(usuario("a"), "cli")])
	transcript(entorno, "espera", [con_entrada(usuario("b"), "cli")])
	transcript(entorno, "muerta", [con_entrada(usuario("c"), "cli")])
	vivos = entorno / "claude" / "sessions"
	vivos.mkdir()
	(vivos / "100.json").write_text(json.dumps({"pid": 100, "status": "busy", "sessionId": "viva"}), encoding="utf-8")
	(vivos / "200.json").write_text(
		json.dumps({"pid": 200, "status": "waiting", "waitingFor": "permiso", "sessionId": "espera"}), encoding="utf-8"
	)
	(vivos / "300.json").write_text(json.dumps({"pid": 300, "status": "busy", "sessionId": "muerta"}), encoding="utf-8")
	(vivos / "roto.json").write_text("{no es json", encoding="utf-8")
	monkeypatch.setattr(hc, "_proceso_vivo", lambda pid: pid in (100, 200))
	estados = {s.id: s.estado for s in hc.listar_sesiones()}
	assert estados == {"viva": "trabajando", "espera": "esperando", "muerta": "inactiva"}


def test_pid_reutilizado_no_cuenta():
	filetime = 133_000_000_000_000_000
	assert hc._mismo_proceso(filetime, filetime + 5, None)
	assert not hc._mismo_proceso(filetime, filetime + 50_000_000_000, None)
	assert not hc._mismo_proceso(filetime, hc._NO_ES_TUYO, None)
	assert hc._mismo_proceso(None, filetime, None)


def metadatos_escritorio(carpeta: Path, id_local: str, **datos) -> Path:
	ruta = carpeta / "usuario" / "org" / ("%s.json" % id_local)
	ruta.parent.mkdir(parents=True, exist_ok=True)
	contenido = {"sessionId": id_local, "cwd": CARPETA, "createdAt": 1_790_000_000_000, "lastActivityAt": 1_790_000_100_000}
	contenido.update(datos)
	ruta.write_text(json.dumps(contenido), encoding="utf-8")
	return ruta


def test_sesion_de_escritorio_con_su_transcript(entorno):
	escritorio = entorno / "roaming" / "Claude" / "claude-code-sessions"
	metadatos_escritorio(
		escritorio,
		"local_aaa",
		cliSessionId="cli-aaa",
		title="Sesión del escritorio",
		permissionMode="default",
		postTurnSummary={"needs_action": "Revisa el cambio", "status_category": "review_ready"},
	)
	metadatos_escritorio(escritorio, "local_arch", cliSessionId="cli-arch", title="Vieja", isArchived=True)
	transcript(
		entorno,
		"cli-aaa",
		[con_entrada(usuario("hola"), "claude-desktop"), asistente([texto("Último del asistente")])],
	)
	transcript(entorno, "cli-arch", [con_entrada(usuario("viejo"), "claude-desktop")])
	sesiones = hc.listar_sesiones()
	assert [s.id for s in sesiones] == ["cli-aaa"]  # la archivada no sale, ni como de terminal
	sesion = sesiones[0]
	assert sesion.origen == "escritorio"
	assert sesion.titulo == "Sesión del escritorio"
	assert sesion.id_escritorio == "local_aaa"
	assert sesion.clave == "claude:cli-aaa"
	assert sesion.estado == "esperando"
	assert sesion.creada == pytest.approx(1_790_000_000.0)
	assert sesion.actividad >= 1_790_000_100.0
	assert sesion.ultimo_mensaje == "Último del asistente"
	assert sesion.ruta_transcript is not None
	assert [m.texto for m in hc.leer_mensajes(sesion.ruta_transcript)] == ["hola", "Último del asistente"]
	assert hc.url_abrir_en_claude(sesion) == "claude://claude.ai/epitaxy/local_aaa"

	todas = {s.id: s for s in hc.listar_sesiones(incluir_archivadas=True)}
	assert todas["cli-arch"].archivada and todas["cli-arch"].origen == "escritorio"


def test_escritorio_duplicado_gana_el_mas_reciente(entorno):
	normal = entorno / "roaming" / "Claude" / "claude-code-sessions"
	store = entorno / "local" / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude" / "claude-code-sessions"
	viejo = metadatos_escritorio(normal, "local_dup", cliSessionId="cli-dup", title="Título viejo")
	nuevo = metadatos_escritorio(store, "local_dup", cliSessionId="cli-dup", title="Título nuevo")
	ahora = time.time()
	os.utime(viejo, (ahora - 100, ahora - 100))
	os.utime(nuevo, (ahora, ahora))
	sesiones = hc.listar_sesiones()
	assert [s.titulo for s in sesiones] == ["Título nuevo"]


def test_sesion_de_cowork(entorno):
	cowork = entorno / "roaming" / "Claude" / "local-agent-mode-sessions"
	meta = metadatos_escritorio(
		cowork,
		"local_cw",
		cliSessionId="cli-cw",
		title="Tarea de Cowork",
		cwd="C:\\cowork\\outputs",
		userSelectedFolders=["C:\\Users\\ana\\Documentos"],
	)
	casa = meta.with_suffix("") / ".claude" / "projects" / "C--cowork-outputs"
	escribir(casa / "cli-cw.jsonl", [usuario("organiza"), asistente([texto("Organizado.")])])
	(sesion,) = hc.listar_sesiones()
	assert sesion.origen == "cowork"
	assert sesion.carpeta == "C:\\Users\\ana\\Documentos"
	assert sesion.ultimo_mensaje == "Organizado."
	assert sesion.ruta_transcript is not None
	assert [m.texto for m in hc.leer_mensajes(sesion.ruta_transcript)] == ["organiza", "Organizado."]
	assert hc.url_abrir_en_claude(sesion) == "claude://claude.ai/cowork/local_cw"


def test_metadatos_rotos_no_rompen_la_lista(entorno):
	escritorio = entorno / "roaming" / "Claude" / "claude-code-sessions" / "x" / "y"
	escritorio.mkdir(parents=True)
	(escritorio / "local_roto.json").write_text("{roto", encoding="utf-8")
	(escritorio / "local_lista.json").write_text("[1]", encoding="utf-8")
	transcript(entorno, "t1", [con_entrada(usuario("hola"), "cli"), b"{corrupta\n"])
	assert [s.id for s in hc.listar_sesiones()] == ["t1"]


def test_la_lista_no_escribe_en_los_datos_de_claude(entorno):
	transcript(entorno, "t1", [con_entrada(usuario("hola"), "cli")])
	metadatos_escritorio(entorno / "roaming" / "Claude" / "claude-code-sessions", "local_z", cliSessionId="cli-z")

	def instantanea():
		return sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in entorno.rglob("*") if p.is_file())

	antes = instantanea()
	sesiones = hc.listar_sesiones()
	for sesion in sesiones:
		if sesion.ruta_transcript:
			hc.leer_mensajes(sesion.ruta_transcript)
	assert instantanea() == antes


# ---------------------------------------------------------------------------
# Búsqueda
# ---------------------------------------------------------------------------


def test_busqueda_sin_acentos_ni_mayusculas_y_todas_las_palabras(entorno):
	transcript(
		entorno,
		"b1",
		[
			con_entrada(usuario("Necesito un CAMIÓN rojo"), "cli"),
			asistente([texto("El camion azul está listo")]),
			usuario("nada que ver", uuid="u9"),
		],
	)
	transcript(entorno, "b2", [con_entrada(usuario("Otro camión, pero rojo también"), "cli")])
	sesiones = hc.listar_sesiones()
	encontrados = hc.buscar_en_sesiones(sesiones, "camion")
	assert sorted(m.texto for _, m in encontrados) == sorted(
		["Necesito un CAMIÓN rojo", "El camion azul está listo", "Otro camión, pero rojo también"]
	)
	ambas = hc.buscar_en_sesiones(sesiones, "Rojo  camión")
	assert sorted(s.id for s, _ in ambas) == ["b1", "b2"]
	assert len(hc.buscar_en_sesiones(sesiones, "camion", maximo=1)) == 1
	assert hc.buscar_en_sesiones(sesiones, "   ") == []
	assert hc.buscar_en_sesiones(sesiones, "camion", cancelado=lambda: True) == []
	assert hc.buscar_en_sesiones(sesiones, "camion inexistente") == []


def test_filtro_previo_pliega_mayusculas_y_tildes(tmp_path):
	ruta = tmp_path / "t.jsonl"
	ruta.write_bytes(json.dumps({"t": "ÁRBOL Pingüino CAÑÓN — ok"}, ensure_ascii=False).encode("utf-8"))
	assert hc._puede_contener(ruta, [b"arbol", b"pinguino", b"canon"])
	assert not hc._puede_contener(ruta, [b"arbol", b"perro"])
	descompuesto = tmp_path / "d.jsonl"
	descompuesto.write_bytes(json.dumps({"t": "Café y CAÑON"}, ensure_ascii=False).encode("utf-8"))
	assert hc._puede_contener(descompuesto, [b"cafe", b"canon"])
	assert not hc._puede_contener(descompuesto, [b"te"])
	assert hc._palabra_filtrable("ejecutar") is None  # la pone el lector
	assert hc._palabra_filtrable("strasse") is None  # podría venir de «ß»


def test_busqueda_encuentra_textos_puestos_por_el_lector(entorno):
	"""«Ejecutar» no está en el archivo (lo pone el lector): el filtro previo
	no debe descartar el transcript."""
	transcript(
		entorno,
		"h1",
		[con_entrada(usuario("hola"), "cli"), asistente([llamada("Bash", "t1", {"command": "git status"})])],
	)
	encontrados = hc.buscar_en_sesiones(hc.listar_sesiones(), "EJECUTAR git")
	assert [m.texto for _, m in encontrados] == ["Ejecutar: git status"]
