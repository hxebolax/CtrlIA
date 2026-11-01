"""Copias de seguridad: crear, comprobar, cifrar, programar y restaurar (``app/services/copias.py``)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services import copias
from app.services.almacen import Almacen, SesionPropia


def _linea(**datos) -> str:
	return json.dumps(datos, ensure_ascii=False) + "\n"


def _transcripcion(carpeta: str, texto: str = "Hola, agente") -> str:
	return (_linea(type="user", entrypoint="cli", cwd=carpeta, timestamp="2026-10-01T10:00:00Z", uuid="u1",
			message={"role": "user", "content": texto})
		+ _linea(type="assistant", timestamp="2026-10-01T10:00:05Z", uuid="a1", cwd=carpeta,
			message={"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "Hola, persona"}]}))


@pytest.fixture
def casa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
	"""Un equipo de mentira: ni la carpeta personal ni %APPDATA% son los de quien pasa las pruebas."""
	raiz = tmp_path / "casa"
	raiz.mkdir()
	monkeypatch.setenv("USERPROFILE", str(raiz))
	monkeypatch.setenv("HOME", str(raiz))
	monkeypatch.setenv("APPDATA", str(raiz / "AppData" / "Roaming"))
	monkeypatch.setenv("LOCALAPPDATA", str(raiz / "AppData" / "Local"))
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(raiz / ".claude"))
	monkeypatch.setenv("CODEX_HOME", str(raiz / ".codex"))
	monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
	monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
	monkeypatch.setenv("COMPUTERNAME", "PORTATIL")
	return raiz


def _escribir(ruta: Path, texto: str = "contenido") -> Path:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	ruta.write_text(texto, encoding="utf-8")
	return ruta


@pytest.fixture
def agentes(casa: Path) -> Path:
	"""Archivos de los cuatro agentes, con sus credenciales al lado."""
	proyecto = casa / ".claude" / "projects" / "C--trabajo"
	_escribir(proyecto / "aaaa-1111.jsonl", _transcripcion("C:\\trabajo"))
	_escribir(casa / ".claude" / "settings.json", '{"model": "opus"}')
	_escribir(casa / ".claude" / "CLAUDE.md", "memoria")
	_escribir(casa / ".claude" / ".credentials.json", "TOKEN-SECRETO")
	_escribir(casa / ".claude" / "backups" / ".claude.json.backup", "TOKEN-SECRETO")
	_escribir(casa / ".claude" / "cache" / "basura.txt")
	_escribir(casa / ".claude" / "mcp-oauth-config" / "tokens.json", "TOKEN-SECRETO")
	_escribir(casa / ".claude" / "mcp-secrets" / "x.json", "TOKEN-SECRETO")

	codex = casa / ".codex"
	identificador = "0190a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"
	rollout = codex / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T10-00-00-{identificador}.jsonl"
	_escribir(rollout, _linea(timestamp="2026-10-01T10:00:00Z", type="session_meta", payload={"id": identificador, "cwd": "C:\\codex", "timestamp": "2026-10-01T10:00:00Z"})
		+ _linea(timestamp="2026-10-01T10:00:01Z", type="event_msg", payload={"type": "user_message", "message": "Arregla el fallo"})
		+ _linea(timestamp="2026-10-01T10:00:02Z", type="event_msg", payload={"type": "agent_message", "message": "Arreglado"}))
	_escribir(codex / "session_index.jsonl", _linea(id=identificador, thread_name="El fallo de Codex", updated_at="2026-10-02T10:00:00Z"))
	_escribir(codex / "config.toml", 'model = "gpt"\n')
	_escribir(codex / "auth.json", "TOKEN-SECRETO")
	_escribir(codex / "auth.json.bak", "TOKEN-SECRETO")
	_escribir(codex / "cap_sid", "TOKEN-SECRETO")
	_escribir(codex / ".codex-provisioning-abc.guard", "bloqueo")
	_escribir(codex / ".sandbox-secrets" / "clave", "TOKEN-SECRETO")

	gemini = casa / ".gemini"
	_escribir(gemini / "GEMINI.md", "memoria de gemini")
	_escribir(gemini / "settings.json", "{}")
	_escribir(gemini / "oauth_creds.json", "TOKEN-SECRETO")
	_escribir(gemini / "google_accounts.json", "TOKEN-SECRETO")
	_escribir(gemini / "mcp-oauth-tokens-v2.json", "TOKEN-SECRETO")
	_escribir(gemini / "config" / "mcp_config.json", "TOKEN-SECRETO")
	chat = {"sessionId": "gem-1", "startTime": "2026-10-01T09:00:00Z", "lastUpdated": "2026-10-01T09:05:00Z",
		"messages": [{"type": "user", "content": "Hola, Gemini", "timestamp": "2026-10-01T09:00:00Z"},
			{"type": "gemini", "content": "Hola", "timestamp": "2026-10-01T09:00:01Z"}]}
	_escribir(gemini / "tmp" / "abc" / "chats" / "session-1.json", json.dumps(chat))
	_escribir(gemini / "antigravity" / "conversations" / "c1.pb", "pb")
	_escribir(gemini / "antigravity" / "mcp_config.json", "TOKEN-SECRETO")
	_escribir(gemini / "antigravity" / "installation_id", "TOKEN-SECRETO")
	_escribir(gemini / "antigravity-browser-profile" / "Cookies", "TOKEN-SECRETO")
	return casa


@pytest.fixture
def destino(tmp_path: Path) -> Path:
	carpeta = tmp_path / "destino"
	carpeta.mkdir()
	return carpeta


def _almacen_con_sesiones() -> Almacen:
	almacen = Almacen()
	almacen.guardar_sesion(SesionPropia(clave="ctrlia:propia", proveedor="claude", titulo="Mi sesión", carpeta="C:\\p",
		id_externo="id-de-claude", creada=100.0, actividad=200.0))
	almacen.anadir_mensaje("ctrlia:propia", "usuario", "Mensaje propio")
	almacen.fijar_metadato("ctrlia:propia", oculta=True, favorita=True)
	almacen.anadir_a_grupo("ctrlia:propia", almacen.crear_grupo("Investigación"))
	almacen.fijar_metadato("claude:aaaa-1111", titulo="Título puesto en Ctrl+IA")
	almacen.anadir_a_grupo("claude:aaaa-1111", almacen.crear_grupo("Terminal"))
	return almacen


# ── Crear ─────────────────────────────────────────────────────────────────────

def test_la_copia_lleva_lo_de_los_agentes_y_jamas_credenciales(agentes: Path, destino: Path) -> None:
	almacen = _almacen_con_sesiones()
	try:
		resultado = copias.crear(destino)
	finally:
		almacen.cerrar()
	assert resultado.completa and resultado.ruta is not None and resultado.ruta.parent == destino
	assert resultado.ruta.name.startswith("CtrlIA-copia-PORTATIL-") and resultado.ruta.suffix == ".zip"
	with zipfile.ZipFile(resultado.ruta) as archivo:
		nombres = set(archivo.namelist())
		contenido = b"".join(archivo.read(n) for n in nombres)
	assert {"claude/casa/projects/C--trabajo/aaaa-1111.jsonl", "claude/casa/settings.json", "claude/casa/CLAUDE.md",
		"codex/casa/config.toml", "codex/casa/session_index.jsonl", "gemini/casa/GEMINI.md", "gemini/casa/settings.json",
		"gemini/casa/tmp/abc/chats/session-1.json", "antigravity/antigravity/conversations/c1.pb",
		"ctrlia/datos/db/ctrlia.db", copias.MANIFIESTO} <= nombres
	assert not any("rollout" not in n and ".guard" in n for n in nombres)
	assert not any(".guard" in n or ".credentials" in n or "auth.json" in n or "oauth" in n or "cap_sid" in n
		or "mcp_config" in n or "sandbox" in n or "Cookies" in n or "installation_id" in n or "cache/" in n for n in nombres)
	assert b"TOKEN-SECRETO" not in contenido


def test_el_manifiesto_tiene_tamano_y_suma_de_cada_archivo(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)
	with zipfile.ZipFile(resultado.ruta) as archivo:
		manifiesto = json.loads(archivo.read(copias.MANIFIESTO))
		assert manifiesto["formato"] == copias.FORMATO and manifiesto["version"] == copias.VERSION_FORMATO
		assert manifiesto["equipo"]["nombre"] == "PORTATIL" and manifiesto["equipo"]["id"]
		for entrada in manifiesto["archivos"]:
			datos = archivo.read(entrada["ruta"])
			assert len(datos) == entrada["tamano"]
			assert hashlib.sha256(datos).hexdigest() == entrada["sha256"]
	assert {a["id"] for a in manifiesto["agentes"]} == {"ctrlia", "claude", "codex", "gemini", "antigravity"}
	assert any("credentials" in o for a in manifiesto["agentes"] for o in a["omitido"])


def test_no_deja_nada_a_medias_al_acabar(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)
	assert [p.name for p in destino.iterdir()] == [resultado.ruta.name]


def test_sqlite_abierta_por_su_programa_sale_entera_y_no_se_toca_su_carpeta(agentes: Path, destino: Path) -> None:
	base = agentes / ".codex" / "state_5.sqlite"
	conexion = sqlite3.connect(base)
	conexion.execute("PRAGMA journal_mode=WAL")
	conexion.execute("CREATE TABLE t (v TEXT)")
	conexion.execute("INSERT INTO t VALUES ('hola')")
	conexion.commit()
	try:
		antes = sorted(p.name for p in base.parent.iterdir())
		resultado = copias.crear(destino)
		assert sorted(p.name for p in base.parent.iterdir()) == antes
	finally:
		conexion.close()
	with zipfile.ZipFile(resultado.ruta) as archivo:
		nombres = archivo.namelist()
		assert "codex/casa/state_5.sqlite" in nombres
		assert not any(n.endswith(("-wal", "-shm")) for n in nombres)
		extraida = destino / "extraida.sqlite"
		extraida.write_bytes(archivo.read("codex/casa/state_5.sqlite"))
	copia = sqlite3.connect(extraida)
	try:
		assert copia.execute("SELECT v FROM t").fetchall() == [("hola",)]
	finally:
		copia.close()
	extraida.unlink()


def test_sqlite_inmutable_sin_anexos_no_crea_nada_junto_a_ella(agentes: Path, destino: Path) -> None:
	base = agentes / ".codex" / "goals_1.sqlite"
	conexion = sqlite3.connect(base)
	conexion.execute("PRAGMA journal_mode=WAL")
	conexion.execute("CREATE TABLE t (v TEXT)")
	conexion.commit()
	conexion.close()
	assert not Path(str(base) + "-wal").exists()
	copias.crear(destino)
	assert not Path(str(base) + "-wal").exists() and not Path(str(base) + "-shm").exists()


def test_no_se_siguen_enlaces_fuera_de_la_carpeta(agentes: Path, destino: Path, tmp_path: Path) -> None:
	fuera = tmp_path / "fuera"
	_escribir(fuera / "privado.txt", "TOKEN-SECRETO")
	import subprocess

	subprocess.run(["cmd", "/c", "mklink", "/J", str(agentes / ".claude" / "enlace"), str(fuera)], check=True, capture_output=True)
	inventario = copias.inventario_claude()
	assert not any("privado" in e.nombre for e in inventario.elementos)
	assert any("enlace" in n for n in inventario.notas)


def test_un_archivo_que_desaparece_deja_la_copia_incompleta_y_dicho(agentes: Path, destino: Path) -> None:
	def colector() -> copias.Inventario:
		inventario = copias.Inventario("prueba", "Prueba")
		inventario.elementos = [copias.Elemento(agentes / "no-existe.txt", "prueba/no-existe.txt"),
			copias.Elemento(agentes / ".claude" / "CLAUDE.md", "prueba/CLAUDE.md")]
		return inventario

	resultado = copias.crear(destino, colectores=[colector])
	assert not resultado.completa and resultado.desaparecidos == ["prueba/no-existe.txt"]
	assert "incompleta" in copias.resumen(resultado)
	assert copias.comprobar(resultado.ruta).correcta


def test_un_colector_roto_no_impide_copiar_lo_demas(agentes: Path, destino: Path) -> None:
	def roto() -> copias.Inventario:
		raise RuntimeError("no se puede")

	resultado = copias.crear(destino, colectores=[roto, copias.inventario_claude])
	assert resultado.archivos and not resultado.completa


def test_sin_nada_que_copiar_no_crea_un_zip_vacio(casa: Path, destino: Path) -> None:
	with pytest.raises(ValueError, match="nada que copiar"):
		copias.crear(destino, colectores=[lambda: copias.Inventario("x", "X")])
	assert not list(destino.iterdir())


def test_un_destino_dentro_de_lo_copiado_se_rechaza(agentes: Path) -> None:
	with pytest.raises(ValueError, match="dentro de"):
		copias.crear(agentes / ".claude" / "projects")
	assert copias.problema_carpeta_programa(str(agentes / ".codex")) is not None


def test_cancelar_no_deja_ningun_archivo(agentes: Path, destino: Path) -> None:
	pasos = []

	def cancelado() -> bool:
		pasos.append(1)
		return len(pasos) > 8

	with pytest.raises(InterruptedError):
		copias.crear(destino, cancelado=cancelado)
	assert not list(destino.iterdir())


def test_nunca_sustituye_una_copia_existente(agentes: Path, destino: Path) -> None:
	ahora = datetime(2026, 1, 2, 3, 4, 5)
	primera = copias.crear(destino, ahora=ahora)
	segunda = copias.crear(destino, ahora=ahora)
	assert primera.ruta != segunda.ruta and segunda.ruta.name.endswith(" (2).zip")
	assert copias.comprobar(primera.ruta).correcta and copias.comprobar(segunda.ruta).correcta


def test_el_progreso_llega_hasta_el_final(agentes: Path, destino: Path) -> None:
	vistos: list[tuple[int, int]] = []
	copias.crear(destino, progreso=lambda hechos, total, _nombre: vistos.append((hechos, total)))
	assert vistos[-1][0] == vistos[-1][1] > 0


def test_las_conservadas_repetidas_no_ocupan_el_doble(agentes: Path, destino: Path, data_temporal: Path) -> None:
	repetida = data_temporal / "conservadas" / "claude" / "aaaa-1111"
	_escribir(repetida / "registro.json", "{}")
	_escribir(repetida / "transcripcion.jsonl", "copia")
	unica = data_temporal / "conservadas" / "claude" / "borrada"
	_escribir(unica / "registro.json", "{}")
	_escribir(unica / "transcripcion.jsonl", "copia")
	nombres = {e.nombre for e in copias.inventario_ctrlia().elementos}
	assert "ctrlia/datos/conservadas/claude/borrada/transcripcion.jsonl" in nombres
	assert "ctrlia/datos/conservadas/claude/aaaa-1111/transcripcion.jsonl" not in nombres


def test_la_frase_y_el_estado_de_este_equipo_no_entran(agentes: Path, data_temporal: Path) -> None:
	_escribir(data_temporal / "config" / "settings.json", "{}")
	_escribir(data_temporal / "config" / copias.ARCHIVO_FRASE, "protegida")
	_escribir(data_temporal / "config" / copias.ARCHIVO_ESTADO, "{}")
	_escribir(data_temporal / "config" / "secretos.dat", "TOKEN-SECRETO")
	nombres = {e.nombre for e in copias.inventario_ctrlia().elementos}
	assert nombres == {"ctrlia/datos/config/settings.json"}


# ── Comprobar ─────────────────────────────────────────────────────────────────

def _reescribir(origen: Path, destino: Path, cambio) -> None:
	with zipfile.ZipFile(origen) as entrada, zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as salida:
		for info in entrada.infolist():
			cambio(info.filename, entrada.read(info.filename), salida)


def test_comprobar_detecta_un_archivo_danado(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)

	def estropear(nombre, datos, salida):
		salida.writestr(nombre, b"otro contenido" if nombre.endswith("CLAUDE.md") else datos)

	mala = destino / "mala.zip"
	_reescribir(resultado.ruta, mala, estropear)
	comprobacion = copias.comprobar(mala)
	assert not comprobacion.correcta and any("Dañado" in p for p in comprobacion.problemas)
	assert "tiene problemas" in copias.texto_comprobacion(mala, comprobacion)


def test_comprobar_detecta_lo_que_falta_y_lo_que_sobra(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)

	def cambiar(nombre, datos, salida):
		if not nombre.endswith("CLAUDE.md"):
			salida.writestr(nombre, datos)
		if nombre == copias.MANIFIESTO:
			salida.writestr("colado.txt", b"x")

	mala = destino / "mala.zip"
	_reescribir(resultado.ruta, mala, cambiar)
	problemas = " ".join(copias.comprobar(mala).problemas)
	assert "Falta:" in problemas and "No está en el manifiesto: colado.txt" in problemas


def test_comprobar_un_archivo_cualquiera_no_lanza(tmp_path: Path) -> None:
	raro = _escribir(tmp_path / "raro.zip", "no soy un zip")
	assert not copias.comprobar(raro).correcta
	otro = tmp_path / "otro.zip"
	with zipfile.ZipFile(otro, "w") as archivo:
		archivo.writestr("hola.txt", "hola")
	assert "manifiesto" in copias.comprobar(otro).problemas[0]


def test_una_copia_de_una_version_posterior_no_se_da_por_buena(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)

	def subir(nombre, datos, salida):
		if nombre == copias.MANIFIESTO:
			manifiesto = json.loads(datos)
			manifiesto["version"] = copias.VERSION_FORMATO + 1
			datos = json.dumps(manifiesto)
		salida.writestr(nombre, datos)

	futura = destino / "futura.zip"
	_reescribir(resultado.ruta, futura, subir)
	assert "posterior" in copias.comprobar(futura).problemas[0]


def test_texto_de_una_copia_correcta(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)
	comprobacion = copias.comprobar(resultado.ruta)
	assert comprobacion.correcta and comprobacion.archivos == resultado.archivos
	assert "está entera" in copias.texto_comprobacion(resultado.ruta, comprobacion)


def test_nombres_peligrosos() -> None:
	assert copias.nombre_seguro("a/b.txt")
	for malo in ("", "/a", "\\a", "..\\a", "a/../b", "C:/a"):
		assert not copias.nombre_seguro(malo)


# ── Cifrado ───────────────────────────────────────────────────────────────────

FRASE = "una frase bien larga"


def test_cifrada_no_deja_ver_ni_los_nombres(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino, frase=FRASE)
	assert resultado.cifrada and copias.esta_cifrada(resultado.ruta)
	with zipfile.ZipFile(resultado.ruta) as archivo:
		assert archivo.namelist() == [copias.INTERIOR]
	datos = resultado.ruta.read_bytes()
	assert b"projects" not in datos and b"aaaa-1111" not in datos and b"manifiesto" not in datos
	assert [p.name for p in destino.iterdir()] == [resultado.ruta.name]


def test_cifrada_usa_aes_256_que_entienden_otros_programas(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino, frase=FRASE)
	with zipfile.ZipFile(resultado.ruta) as archivo:
		info = archivo.getinfo(copias.INTERIOR)
	assert info.compress_type == 99  # método WinZip AES
	assert b"\x01\x99" in info.extra and b"AE" in info.extra and info.extra[info.extra.index(b"AE") + 2] == 3  # 3 = AES-256


def test_comprobar_una_cifrada_pide_la_frase(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino, frase=FRASE)
	sin_frase = copias.comprobar(resultado.ruta)
	assert sin_frase.pide_frase and sin_frase.cifrada and not sin_frase.correcta
	mala = copias.comprobar(resultado.ruta, frase="otra frase")
	assert mala.pide_frase
	buena = copias.comprobar(resultado.ruta, frase=FRASE)
	assert buena.correcta and buena.cifrada and buena.archivos == resultado.archivos


def test_lo_descifrado_para_comprobar_no_se_queda_en_temp(agentes: Path, destino: Path, data_temporal: Path) -> None:
	resultado = copias.crear(destino, frase=FRASE)
	copias.comprobar(resultado.ruta, frase=FRASE)
	assert not list((data_temporal / "temp").iterdir())


def test_una_cifrada_estropeada_se_detecta(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino, frase=FRASE)
	datos = bytearray(resultado.ruta.read_bytes())
	datos[len(datos) // 2] ^= 0xFF
	resultado.ruta.write_bytes(bytes(datos))
	comprobacion = copias.comprobar(resultado.ruta, frase=FRASE)
	assert not comprobacion.correcta


# ── Frase protegida (DPAPI) ───────────────────────────────────────────────────

def test_la_frase_se_guarda_cifrada_fuera_de_settings(data_temporal: Path) -> None:
	assert not copias.hay_frase() and copias.cargar_frase() is None
	copias.guardar_frase("frase ñandú")
	ruta = data_temporal / "config" / copias.ARCHIVO_FRASE
	assert ruta.is_file() and "frase".encode("utf-16-le") not in ruta.read_bytes() and b"frase" not in ruta.read_bytes()
	assert copias.hay_frase() and copias.cargar_frase() == "frase ñandú"
	copias.borrar_frase()
	assert not copias.hay_frase()


def test_una_frase_de_otra_cuenta_no_se_puede_leer(data_temporal: Path) -> None:
	_escribir(data_temporal / "config" / copias.ARCHIVO_FRASE, "no es de DPAPI")
	assert copias.hay_frase() and copias.cargar_frase() is None


# ── Programadas ───────────────────────────────────────────────────────────────

AHORA = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _hace(**tiempo) -> str:
	return (AHORA - timedelta(**tiempo)).isoformat()


def _programa(frecuencia: str = "diaria", carpeta: str = "C:\\copias", conservar: int = 5, cifrar: bool = False) -> dict:
	return {"programa": {"frecuencia": frecuencia, "carpeta": carpeta, "conservar": conservar, "cifrar": cifrar}}


def test_copia_pendiente_segun_el_programa() -> None:
	assert not copias.copia_pendiente({}, AHORA)
	assert not copias.copia_pendiente(_programa("no"), AHORA)
	assert copias.copia_pendiente(_programa(), AHORA)  # nunca se ha hecho
	assert not copias.copia_pendiente({**_programa(), "ultima_fecha": _hace(hours=2)}, AHORA)
	assert copias.copia_pendiente({**_programa(), "ultima_fecha": _hace(days=2)}, AHORA)
	assert not copias.copia_pendiente({**_programa("semanal"), "ultima_fecha": _hace(days=2)}, AHORA)
	assert copias.copia_pendiente({**_programa("semanal"), "ultima_fecha": _hace(days=8)}, AHORA)


def test_tras_un_fallo_se_espera_una_hora() -> None:
	assert not copias.copia_pendiente({**_programa(), "programa_intento": _hace(minutes=20)}, AHORA)
	assert copias.copia_pendiente({**_programa(), "programa_intento": _hace(hours=2)}, AHORA)


def test_una_fecha_futura_no_para_las_copias() -> None:
	assert copias.copia_pendiente({**_programa(), "ultima_fecha": (AHORA + timedelta(days=3)).isoformat()}, AHORA)


def test_un_programa_raro_se_lee_como_desactivado() -> None:
	assert not copias.cargar_programa({"programa": {"frecuencia": "cada rato", "carpeta": 3, "conservar": "x"}}).activo
	assert copias.cargar_programa({"programa": {"frecuencia": "diaria", "carpeta": "c", "conservar": 9999}}).conservar == copias.MAX_CONSERVAR


def test_el_programa_se_guarda_fuera_de_settings_y_sin_frase(data_temporal: Path) -> None:
	programa = copias.Programa("semanal", "D:\\copias", 7, True)
	copias.guardar_programa(programa)
	assert copias.cargar_programa() == programa
	assert (data_temporal / "config" / copias.ARCHIVO_ESTADO).is_file()


def test_guardar_el_programa_olvida_el_intento_fallido() -> None:
	copias.anotar_intento(AHORA)
	copias.guardar_programa(copias.Programa("diaria", "C:\\x"))
	assert "programa_intento" not in copias.cargar_estado()


def test_un_fallo_se_dice_una_vez_al_dia() -> None:
	assert copias.anotar_fallo("sin disco", AHORA)
	assert not copias.anotar_fallo("sin disco", AHORA + timedelta(hours=3))
	assert copias.anotar_fallo("sin disco", AHORA + timedelta(hours=25))
	assert "sin disco" in copias.texto_fallo()
	copias.borrar_fallo()
	assert copias.texto_fallo() == ""


def _copia_falsa(carpeta: Path, nombre: str, equipo: str, fecha: float) -> Path:
	ruta = carpeta / nombre
	with zipfile.ZipFile(ruta, "w") as archivo:
		archivo.writestr(copias.MANIFIESTO, json.dumps({"formato": copias.FORMATO, "equipo": {"id": equipo}}))
	os.utime(ruta, (fecha, fecha))
	return ruta


def test_recortar_borra_solo_las_antiguas_de_este_equipo(destino: Path) -> None:
	viejas = [_copia_falsa(destino, f"CtrlIA-copia-A-2026-01-0{i}-120000.zip", "yo", 1000.0 + i) for i in range(1, 5)]
	ajena = _copia_falsa(destino, "CtrlIA-copia-A-2025-01-01-120000.zip", "otro", 10.0)
	ilegible = _escribir(destino / "CtrlIA-copia-A-2025-01-02-120000.zip", "no es un zip")
	os.utime(ilegible, (20.0, 20.0))
	nueva = _copia_falsa(destino, "CtrlIA-copia-A-2026-02-01-120000.zip", "yo", 2000.0)
	otro_nombre = _escribir(destino / "mis-notas.zip", "notas")
	borradas = copias.recortar(destino, 3, "yo", nueva)
	assert sorted(borradas) == sorted(viejas[:2])
	assert all(r.exists() for r in [ajena, ilegible, nueva, otro_nombre, viejas[2], viejas[3]])


def test_recortar_reconoce_una_cifrada_apuntada_y_no_una_cambiada(destino: Path) -> None:
	apuntada = _escribir(destino / "CtrlIA-copia-A-2026-01-01-120000.zip", "cifrada")
	cambiada = _escribir(destino / "CtrlIA-copia-A-2026-01-02-120000.zip", "cifrada")
	os.utime(apuntada, (10.0, 10.0))
	os.utime(cambiada, (20.0, 20.0))
	copias.anotar_hecha(apuntada)
	copias.anotar_hecha(cambiada)
	cambiada.write_text("otra cosa, otro tamaño", encoding="utf-8")
	nueva = _copia_falsa(destino, "CtrlIA-copia-A-2026-02-01-120000.zip", "yo", 2000.0)
	assert copias.recortar(destino, 1, "yo", nueva) == [apuntada]
	assert cambiada.exists()


def test_ejecutar_programada_hace_recuerda_y_recorta(agentes: Path, destino: Path) -> None:
	copias.guardar_programa(copias.Programa("diaria", str(destino), 2))
	equipo = copias.identidad_equipo()["id"]
	vieja = _copia_falsa(destino, "CtrlIA-copia-A-2020-01-01-120000.zip", equipo, 10.0)
	mas_vieja = _copia_falsa(destino, "CtrlIA-copia-A-2019-01-01-120000.zip", equipo, 5.0)
	resultado = copias.ejecutar_programada()
	assert resultado.completa and vieja.exists() and not mas_vieja.exists()
	estado = copias.cargar_estado()
	assert estado["ultima_ruta"] == str(resultado.ruta) and not copias.copia_pendiente(estado)
	assert "Última copia" in copias.texto_ultima_copia(estado)


def test_una_programada_incompleta_no_borra_las_buenas(agentes: Path, destino: Path) -> None:
	copias.guardar_programa(copias.Programa("diaria", str(destino), 1))
	vieja = _copia_falsa(destino, "CtrlIA-copia-A-2020-01-01-120000.zip", copias.identidad_equipo()["id"], 10.0)

	def colector() -> copias.Inventario:
		inventario = copias.Inventario("prueba", "Prueba")
		inventario.elementos = [copias.Elemento(agentes / "no-existe.txt", "prueba/a.txt"),
			copias.Elemento(agentes / ".claude" / "CLAUDE.md", "prueba/CLAUDE.md")]
		return inventario

	resultado = copias.ejecutar_programada(colectores=[colector])
	assert not resultado.completa and vieja.exists()


def test_una_programada_cifrada_sin_frase_no_se_hace_sin_cifrar(agentes: Path, destino: Path) -> None:
	copias.guardar_programa(copias.Programa("diaria", str(destino), 2, cifrar=True))
	with pytest.raises(ValueError, match="frase"):
		copias.ejecutar_programada()
	assert not list(destino.iterdir())


def test_una_programada_cifrada_usa_la_frase_guardada(agentes: Path, destino: Path) -> None:
	copias.guardar_programa(copias.Programa("diaria", str(destino), 2, cifrar=True))
	copias.guardar_frase(FRASE)
	resultado = copias.ejecutar_programada()
	assert resultado.cifrada and copias.comprobar(resultado.ruta, frase=FRASE).correcta


def test_una_carpeta_que_ya_no_existe_es_un_fallo_claro(agentes: Path, tmp_path: Path) -> None:
	copias.guardar_programa(copias.Programa("diaria", str(tmp_path / "desconectado"), 2))
	with pytest.raises(ValueError, match="no es una carpeta"):
		copias.ejecutar_programada()


def test_mismo_disco_se_dice_sin_impedirlo(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)
	assert any("mismo disco" in n for n in resultado.notas)
	assert copias.aviso_carpeta_programa(str(destino)) is not None


# ── Restaurar ─────────────────────────────────────────────────────────────────

@pytest.fixture
def copia_hecha(agentes: Path, destino: Path) -> Path:
	almacen = _almacen_con_sesiones()
	try:
		return copias.crear(destino).ruta
	finally:
		almacen.cerrar()


def _instantanea(carpeta: Path) -> dict[str, tuple[int, int]]:
	return {str(r): (r.stat().st_size, r.stat().st_mtime_ns) for r in carpeta.rglob("*") if r.is_file()}


def test_lista_las_sesiones_de_todos_los_agentes(copia_hecha: Path) -> None:
	with copias.CopiaAbierta(copia_hecha) as copia:
		por_clave = {s.clave: s for s in copia.sesiones}
		assert set(por_clave) == {"claude:aaaa-1111", "codex:0190a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b", "gemini:gem-1", "ctrlia:propia"}
		assert por_clave["claude:aaaa-1111"].titulo == "Título puesto en Ctrl+IA"
		assert por_clave["claude:aaaa-1111"].carpeta == "C:\\trabajo"
		assert por_clave["claude:aaaa-1111"].grupos == ["Terminal"]
		assert por_clave["codex:0190a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"].titulo == "El fallo de Codex"
		assert por_clave["codex:0190a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"].carpeta == "C:\\codex"
		assert por_clave["gemini:gem-1"].titulo == "Hola, Gemini"
		assert por_clave["ctrlia:propia"].oculta and por_clave["ctrlia:propia"].favorita
		assert por_clave["ctrlia:propia"].grupos == ["Investigación"]
		assert copia.equipo == "PORTATIL"


def test_la_transcripcion_de_una_sesion_propia_no_sale_dos_veces(agentes: Path, destino: Path) -> None:
	_escribir(agentes / ".claude" / "projects" / "C--p" / "id-de-claude.jsonl", _transcripcion("C:\\p"))
	almacen = _almacen_con_sesiones()
	try:
		ruta = copias.crear(destino).ruta
	finally:
		almacen.cerrar()
	with copias.CopiaAbierta(ruta) as copia:
		assert "claude:id-de-claude" not in {s.clave for s in copia.sesiones}


def test_restaura_como_importadas_de_solo_lectura_con_grupos_y_ocultas(copia_hecha: Path, agentes: Path, data_temporal: Path) -> None:
	(data_temporal / "db" / "ctrlia.db").unlink(missing_ok=True)
	antes = _instantanea(agentes / ".claude") | _instantanea(agentes / ".codex")
	almacen = Almacen(data_temporal / "otra.db")
	try:
		with copias.CopiaAbierta(copia_hecha) as copia:
			resultado = copia.restaurar(copia.sesiones, almacen)
		assert resultado.nuevas == 4 and not resultado.fallidas and resultado.ocultas == 1
		importadas = almacen.sesiones_importadas()
		assert set(importadas) == {f"PORTATIL|{c}" for c in ("claude:aaaa-1111", "gemini:gem-1", "ctrlia:propia",
			"codex:0190a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b")}
		clave = importadas["PORTATIL|ctrlia:propia"]
		assert clave.startswith("importada:")
		sesion = almacen.sesion(clave)
		assert sesion.titulo == "Mi sesión" and sesion.extra["importada"]["equipo"] == "PORTATIL"
		assert sesion.extra["importada"]["copia"] == copia_hecha.name
		assert [m.texto for m in almacen.mensajes(clave)] == ["Mensaje propio"]
		metadatos = almacen.metadatos()[clave]
		assert metadatos.oculta and metadatos.favorita
		assert almacen.grupos_por_sesion()[clave] == ["Investigación"]
		claude = importadas["PORTATIL|claude:aaaa-1111"]
		assert almacen.sesion(claude).titulo == "Título puesto en Ctrl+IA"
		assert [m.texto for m in almacen.mensajes(claude)] == ["Hola, agente", "Hola, persona"]
		assert almacen.grupos_por_sesion()[claude] == ["Terminal"]
		codex = almacen.mensajes(importadas["PORTATIL|codex:0190a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"])
		assert [(m.rol, m.texto) for m in codex] == [("usuario", "Arregla el fallo"), ("asistente", "Arreglado")]
		gemini = almacen.mensajes(importadas["PORTATIL|gemini:gem-1"])
		assert [(m.rol, m.texto) for m in gemini] == [("usuario", "Hola, Gemini"), ("asistente", "Hola")]
	finally:
		almacen.cerrar()
	assert _instantanea(agentes / ".claude") | _instantanea(agentes / ".codex") == antes


def test_restaurar_otra_vez_actualiza_y_no_duplica(copia_hecha: Path, data_temporal: Path) -> None:
	almacen = Almacen(data_temporal / "otra.db")
	try:
		with copias.CopiaAbierta(copia_hecha) as copia:
			primera = copia.restaurar(copia.sesiones, almacen)
			# Lo que se haga aquí después manda sobre la copia.
			clave = almacen.sesiones_importadas()["PORTATIL|ctrlia:propia"]
			almacen.fijar_metadato(clave, oculta=False)
			segunda = copia.restaurar(copia.sesiones, almacen)
		assert primera.nuevas == 4 and segunda.nuevas == 0 and segunda.sin_cambios == 4
		assert len(almacen.sesiones_importadas()) == 4
		assert not almacen.metadatos()[clave].oculta
	finally:
		almacen.cerrar()


def test_una_copia_nueva_actualiza_la_sesion_que_ha_crecido(agentes: Path, destino: Path, data_temporal: Path) -> None:
	transcripcion = agentes / ".claude" / "projects" / "C--trabajo" / "aaaa-1111.jsonl"
	primera = copias.crear(destino).ruta
	almacen = Almacen(data_temporal / "otra.db")
	try:
		with copias.CopiaAbierta(primera) as copia:
			copia.restaurar([s for s in copia.sesiones if s.proveedor == "claude"], almacen)
		with open(transcripcion, "a", encoding="utf-8") as archivo:
			archivo.write(_linea(type="user", timestamp="2026-10-03T10:00:00Z", uuid="u2", message={"role": "user", "content": "Más"}))
		segunda = copias.crear(destino).ruta
		with copias.CopiaAbierta(segunda) as copia:
			resultado = copia.restaurar([s for s in copia.sesiones if s.proveedor == "claude"], almacen)
		assert resultado.actualizadas == 1 and len(almacen.sesiones_importadas()) == 1
		clave = next(iter(almacen.sesiones_importadas().values()))
		assert almacen.mensajes(clave)[-1].texto == "Más"
	finally:
		almacen.cerrar()


def test_situar_dice_que_falta_y_que_sigue_aqui(copia_hecha: Path, data_temporal: Path) -> None:
	almacen = Almacen(data_temporal / "otra.db")
	try:
		with copias.CopiaAbierta(copia_hecha) as copia:
			copia.situar(almacen, ["claude:aaaa-1111"])
			situacion = {s.clave: s.situacion for s in copia.sesiones}
			assert situacion["claude:aaaa-1111"] == copias.EN_EL_EQUIPO and situacion["gemini:gem-1"] == copias.FALTA
			copia.restaurar([s for s in copia.sesiones if s.clave == "gemini:gem-1"], almacen)
			copia.situar(almacen, [])
			assert {s.clave: s.situacion for s in copia.sesiones}["gemini:gem-1"] == copias.YA_RESTAURADA
	finally:
		almacen.cerrar()


def test_cerrar_borra_lo_extraido(copia_hecha: Path, data_temporal: Path) -> None:
	copia = copias.CopiaAbierta(copia_hecha)
	assert list((data_temporal / "temp").iterdir())
	copia.cerrar()
	copia.cerrar()
	assert not list((data_temporal / "temp").iterdir())


def test_restaurar_una_cifrada_pide_la_frase(agentes: Path, destino: Path, data_temporal: Path) -> None:
	ruta = copias.crear(destino, frase=FRASE).ruta
	with pytest.raises(copias.ErrorFrase) as error:
		copias.CopiaAbierta(ruta)
	assert str(error.value) == "falta"
	with pytest.raises(copias.ErrorFrase) as error:
		copias.CopiaAbierta(ruta, "otra frase")
	assert str(error.value) == "mal"
	assert not list((data_temporal / "temp").iterdir())
	with copias.CopiaAbierta(ruta, FRASE) as copia:
		assert len(copia.sesiones) == 3
		assert not list(Path(copia._temporal.name).glob(copias.INTERIOR))  # lo sin cifrar no se queda


def test_un_archivo_danado_se_salta_y_lo_demas_se_restaura(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)

	def estropear(nombre, datos, salida):
		salida.writestr(nombre, b"otro" if nombre.endswith("session-1.json") else datos)

	mala = destino / "mala.zip"
	_reescribir(resultado.ruta, mala, estropear)
	with copias.CopiaAbierta(mala) as copia:
		assert "gemini:gem-1" not in {s.clave for s in copia.sesiones}
		assert "claude:aaaa-1111" in {s.clave for s in copia.sesiones}
		assert any("dañado" in n.lower() or "no válida" in n for n in copia.notas)


def test_un_nombre_peligroso_en_el_manifiesto_no_sale_de_su_carpeta(agentes: Path, destino: Path) -> None:
	resultado = copias.crear(destino)

	def colar(nombre, datos, salida):
		if nombre == copias.MANIFIESTO:
			manifiesto = json.loads(datos)
			manifiesto["archivos"].append({"ruta": "claude/casa/projects/../../../fuera.jsonl", "tamano": 1, "sha256": "0" * 64})
			datos = json.dumps(manifiesto)
		salida.writestr(nombre, datos)

	mala = destino / "mala.zip"
	_reescribir(resultado.ruta, mala, colar)
	with copias.CopiaAbierta(mala) as copia:
		assert copia.sesiones


def test_abrir_algo_que_no_es_una_copia_es_un_error_claro(tmp_path: Path) -> None:
	raro = _escribir(tmp_path / "raro.zip", "no soy un zip")
	with pytest.raises(ValueError, match="no es una copia"):
		copias.CopiaAbierta(raro)
	otro = tmp_path / "otro.zip"
	with zipfile.ZipFile(otro, "w") as archivo:
		archivo.writestr(copias.MANIFIESTO, json.dumps({"formato": "otra cosa"}))
	with pytest.raises(ValueError, match="no es una copia"):
		copias.CopiaAbierta(otro)


def test_los_chats_conservados_se_restauran(casa: Path, destino: Path, data_temporal: Path) -> None:
	carpeta = data_temporal / "conservadas" / "claude" / "borrada-1"
	_escribir(carpeta / "transcripcion.jsonl", _transcripcion("C:\\vieja", "Sesión borrada"))
	_escribir(carpeta / "registro.json", json.dumps({"id": "borrada-1", "metadatos": {"titulo": "La vieja", "carpeta": "C:\\vieja",
		"origen": "terminal", "creada": 5.0, "actividad": 6.0}, "sello": [1, 1, 1]}))
	ruta = copias.crear(destino).ruta
	with copias.CopiaAbierta(ruta) as copia:
		sesion = next(s for s in copia.sesiones if s.clave == "claude:borrada-1")
		assert sesion.titulo == "La vieja" and sesion.origen == "terminal"
		assert copia.mensajes(sesion)[0]["texto"] == "Sesión borrada"


def test_sin_sesiones_lo_dice(casa: Path, destino: Path) -> None:
	_escribir(casa / ".gemini" / "GEMINI.md", "solo memoria")
	ruta = copias.crear(destino, colectores=[copias.inventario_gemini]).ruta
	with copias.CopiaAbierta(ruta) as copia:
		assert copia.sesiones == []


def test_resumen_de_la_restauracion() -> None:
	texto = copias.resumen_restauracion(copias.ResultadoRestauracion(nuevas=2, actualizadas=1, sin_cambios=3, ocultas=1,
		fallidas=["x"], notas=["Algo pasó."]))
	assert "2 sesiones restauradas" in texto and "1 sesión actualizada" in texto and "3 sin cambios" in texto
	assert "1 no se han podido leer" in texto and "Algo pasó." in texto
	assert copias.resumen_restauracion(copias.ResultadoRestauracion()) == "No se ha restaurado nada."


def test_gestor_lee_la_sesion_restaurada_y_no_deja_escribir(copia_hecha: Path, data_temporal: Path) -> None:
	from app.services.gestor import GestorSesiones

	almacen = Almacen(data_temporal / "otra.db")
	try:
		with copias.CopiaAbierta(copia_hecha) as copia:
			copia.restaurar(copia.sesiones, almacen)
		gestor = GestorSesiones(almacen, {}, lambda clave, evento: None)
		clave = almacen.sesiones_importadas()["PORTATIL|claude:aaaa-1111"]
		fila = next(f for f in gestor.listar() if f.clave == clave)
		assert fila.importada and fila.nombre_origen == "Importada de PORTATIL"
		assert [m.texto for m in gestor.mensajes(clave)] == ["Hola, agente", "Hola, persona"]
		with pytest.raises(ValueError):
			gestor.enviar(clave, "hola")
	finally:
		almacen.cerrar()


# ── Interfaz ──────────────────────────────────────────────────────────────────

def _sesion(clave: str, situacion: str = copias.FALTA) -> copias.SesionEnCopia:
	return copias.SesionEnCopia(clave=clave, proveedor="claude", titulo=clave, tipo="claude", situacion=situacion, actividad=1000.0)


def _aceleradores(ventana) -> list[str]:
	import wx

	letras: list[str] = []
	for control in ventana.GetChildren():
		etiqueta = control.GetLabel() if isinstance(control, wx.StaticText | wx.Button | wx.CheckBox) else ""
		if "&" in etiqueta:
			letras.append(etiqueta[etiqueta.index("&") + 1].lower())
		letras += _aceleradores(control)
	return letras


def test_dialogo_hacer_copia(marco, tmp_path: Path, dialogo_modal) -> None:
	import wx

	from app.ui.dialogos.copias import DialogoHacerCopia

	dialogo = DialogoHacerCopia(marco, str(tmp_path), "Última copia: ayer.")
	assert len(set(_aceleradores(dialogo))) == len(_aceleradores(dialogo))
	assert "Última copia: ayer." in dialogo.campo_aviso.GetValue()
	assert not dialogo.campo_frase.IsEnabled()
	dialogo.casilla_cifrar.SetValue(True)
	dialogo.actualizar()
	assert dialogo.campo_frase.IsEnabled() and dialogo.campo_repetir.IsEnabled()
	for control in (dialogo.campo_carpeta, dialogo.campo_frase, dialogo.campo_repetir, dialogo.casilla_cifrar, dialogo.boton_copiar):
		assert control.GetHelpText()
	dialogo.campo_frase.SetValue("corta")
	dialogo.campo_repetir.SetValue("corta")
	dialogo_modal.responder(wx.ID_OK)
	assert not dialogo.aceptar() and "al menos" in " ".join(dialogo_modal.ultimo.textos)
	dialogo.campo_frase.SetValue("una frase larga")
	dialogo.campo_repetir.SetValue("otra frase larga")
	dialogo_modal.responder(wx.ID_OK)
	assert not dialogo.aceptar() and "no coinciden" in " ".join(dialogo_modal.ultimo.textos)
	dialogo.campo_repetir.SetValue("una frase larga")
	assert dialogo.aceptar() and dialogo.frase == "una frase larga" and dialogo.carpeta == str(tmp_path)


def test_dialogo_hacer_copia_necesita_una_carpeta_que_exista(marco, tmp_path: Path, dialogo_modal) -> None:
	import wx

	from app.ui.dialogos.copias import DialogoHacerCopia

	dialogo = DialogoHacerCopia(marco, str(tmp_path / "no-existe"))
	dialogo_modal.responder(wx.ID_OK)
	assert not dialogo.aceptar() and "no es una carpeta" in " ".join(dialogo_modal.ultimo.textos)


def test_dialogo_programar(marco, tmp_path: Path, dialogo_modal, monkeypatch: pytest.MonkeyPatch) -> None:
	import wx

	from app.ui.dialogos.copias import DialogoProgramarCopias

	monkeypatch.setattr(copias, "mismo_disco_que_casa", lambda _ruta: False)
	dialogo = DialogoProgramarCopias(marco, copias.Programa(), estado="Sin copias.")
	assert len(set(_aceleradores(dialogo))) == len(_aceleradores(dialogo))
	assert not dialogo.campo_carpeta.IsEnabled()
	assert dialogo.aceptar() and dialogo.programa.frecuencia == "no"
	dialogo = DialogoProgramarCopias(marco, copias.Programa())
	dialogo.campo_frecuencia.SetSelection(2)
	dialogo.actualizar()
	assert dialogo.campo_carpeta.IsEnabled()
	dialogo.campo_carpeta.SetValue(str(tmp_path))
	dialogo.campo_conservar.SetValue(9)
	dialogo.casilla_cifrar.SetValue(True)
	dialogo.actualizar()
	dialogo_modal.responder(wx.ID_OK)
	assert not dialogo.aceptar()  # cifrar sin frase
	dialogo.campo_frase.SetValue("una frase larga")
	dialogo.campo_repetir.SetValue("una frase larga")
	assert dialogo.aceptar()
	assert dialogo.programa == copias.Programa("semanal", str(tmp_path), 9, True) and dialogo.frase_nueva == "una frase larga"


def test_dialogo_programar_conserva_la_frase_guardada(marco, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui.dialogos.copias import DialogoProgramarCopias

	monkeypatch.setattr(copias, "mismo_disco_que_casa", lambda _ruta: False)
	dialogo = DialogoProgramarCopias(marco, copias.Programa("diaria", str(tmp_path), 5, True), hay_frase=True)
	assert "déjalo vacío" in dialogo.campo_frase.GetHelpText()
	assert dialogo.aceptar() and dialogo.frase_nueva is None and dialogo.programa.cifrar


def test_dialogo_programar_avisa_del_mismo_disco(marco, tmp_path: Path, dialogo_modal, monkeypatch: pytest.MonkeyPatch) -> None:
	import wx

	from app.ui.dialogos.copias import DialogoProgramarCopias

	monkeypatch.setattr(copias, "mismo_disco_que_casa", lambda _ruta: True)
	dialogo = DialogoProgramarCopias(marco, copias.Programa("diaria", str(tmp_path)))
	dialogo_modal.responder(wx.ID_NO)
	assert not dialogo.aceptar()
	dialogo_modal.responder(wx.ID_YES)
	assert dialogo.aceptar()


def test_dialogo_restaurar_marca_las_que_faltan(marco, dialogo_modal) -> None:
	import wx

	from app.ui.dialogos.copias import DialogoRestaurarCopia

	sesiones = [_sesion("a"), _sesion("b", copias.EN_EL_EQUIPO), _sesion("c", copias.YA_RESTAURADA)]
	dialogo = DialogoRestaurarCopia(marco, sesiones, "Copia de PORTATIL.")
	assert len(set(_aceleradores(dialogo))) == len(_aceleradores(dialogo))
	assert dialogo.lista.filas_marcadas() == [0, 2]
	assert dialogo.lista.GetHelpText() and dialogo.campo_informacion.GetValue() == "Copia de PORTATIL."
	assert dialogo.lista.texto_celda(1, 2) == "Sigue en este equipo"
	assert dialogo._crear_menu() is not None
	dialogo.lista.marcar_todas(False)
	dialogo_modal.responder(wx.ID_OK)
	assert not dialogo.aceptar() and not dialogo.elegidas
	dialogo.lista.marcar_todas(True)
	assert dialogo.aceptar() and [s.clave for s in dialogo.elegidas] == ["a", "b", "c"]
	assert DialogoRestaurarCopia(marco, [], "Nada.")._crear_menu() is None


def test_las_listas_de_copias_tienen_menu_contextual() -> None:
	texto = (Path(__file__).resolve().parent.parent / "app" / "ui" / "dialogos" / "copias.py").read_text(encoding="utf-8")
	assert texto.count("ListaAccesible(") == texto.count("vincular_menu_contextual(self.lista")


def test_las_nuevas_carpetas_de_datos_nunca_se_distribuyen() -> None:
	raiz = Path(__file__).resolve().parent.parent
	assert '"conservadas"' in (raiz / "setup_cx.py").read_text(encoding="utf-8")
	assert '"conservadas"' in (raiz / "tools" / "verificar_build.py").read_text(encoding="utf-8")


def test_pyzipper_va_en_la_compilacion_y_en_los_requisitos() -> None:
	raiz = Path(__file__).resolve().parent.parent
	assert "pyzipper" in (raiz / "requirements.txt").read_text(encoding="utf-8")
	setup = (raiz / "setup_cx.py").read_text(encoding="utf-8")
	assert '"pyzipper"' in setup and '"Cryptodome"' in setup


# ── Ventana principal ─────────────────────────────────────────────────────────

@pytest.fixture
def ventana(app_wx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	from app.core.configuracion import Configuracion
	from app.ui import avisos
	from app.ui.ventana_principal import VentanaPrincipal
	from tests.ayudas_wx import procesar_eventos

	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"historial.ollama": False, "general.confirmar_salida": False, "anuncios.sonidos": False,
		"general.abrir_ultima_sesion": False})
	marco = VentanaPrincipal(config, None, almacen=Almacen(tmp_path / "prueba.db"), proveedores={})
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()
		procesar_eventos()


def _dialogo_falso(monkeypatch: pytest.MonkeyPatch, clase: str, respuesta, **atributos) -> None:
	"""Sustituye un diálogo de copias por uno que responde solo."""
	import app.ui.dialogos.copias as modulo

	class Falso:
		def __init__(self, *_a, **_k) -> None:
			for nombre, valor in atributos.items():
				setattr(self, nombre, valor)

		def ShowModal(self):  # noqa: N802 - API de wx
			return respuesta

		def Destroy(self):  # noqa: N802 - API de wx
			return None

	monkeypatch.setattr(modulo, clase, Falso)


def test_el_menu_tiene_todas_las_ordenes_y_aceleradores_unicos(ventana) -> None:
	menu = ventana.crear_menu_copias()
	etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems()]
	assert len(etiquetas) == 5 and all(item.GetHelp() for item in menu.GetMenuItems())
	letras = [e[e.index("&") + 1].lower() for e in etiquetas]
	assert len(set(letras)) == len(letras)
	assert "\tCtrl+Alt+B" in etiquetas[0]


def test_hacer_una_copia_desde_la_ventana(ventana, agentes: Path, destino: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	import wx

	from tests.ayudas_wx import esperar_hasta

	_dialogo_falso(monkeypatch, "DialogoHacerCopia", wx.ID_OK, carpeta=str(destino), frase=None)
	dichos: list[str] = []
	from app.ui import avisos

	monkeypatch.setattr(avisos, "notificar", lambda texto, *_a, **_k: dichos.append(texto))
	ventana.accion_hacer_copia()
	esperar_hasta(lambda: bool(dichos), 20, "la copia no ha terminado")
	assert "Copiados" in dichos[0] and list(destino.glob("CtrlIA-copia-*.zip"))
	assert copias.cargar_estado()["carpeta"] == str(destino)


def test_una_copia_cancelada_lo_dice_y_no_deja_nada(ventana, agentes: Path, destino: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	import threading

	import wx

	from tests.ayudas_wx import esperar_hasta

	_dialogo_falso(monkeypatch, "DialogoHacerCopia", wx.ID_OK, carpeta=str(destino), frase=None)
	dichos: list[str] = []
	from app.ui import avisos

	monkeypatch.setattr(avisos, "anunciar", lambda texto, *_a, **_k: dichos.append(texto))
	empezada, seguir = threading.Event(), threading.Event()
	crear = copias.crear

	def retenida(*argumentos, **opciones):
		def progreso(*_a):
			empezada.set()
			seguir.wait(10)

		return crear(*argumentos, **{**opciones, "progreso": progreso})

	monkeypatch.setattr(copias, "crear", retenida)
	ventana.accion_hacer_copia()
	assert empezada.wait(10)
	ventana.accion_detener_copia()
	seguir.set()
	esperar_hasta(lambda: any("detenida" in d for d in dichos), 20, "no se ha dicho que se detuvo")
	assert not list(destino.iterdir())


def test_no_hay_dos_copias_a_la_vez(ventana, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui import avisos, vp_copias

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *_a, **_k: dichos.append(texto))
	monkeypatch.setattr(ventana.tareas, "activa", lambda clave: clave == vp_copias.CLAVE_COPIA)
	ventana.accion_hacer_copia()
	assert dichos == ["Ya hay una copia en marcha."]
	ventana.comprobar_copia_programada()  # con una en marcha, ni se mira


def test_detener_sin_copia_lo_dice(ventana, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui import avisos

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *_a, **_k: dichos.append(texto))
	ventana.accion_detener_copia()
	assert dichos == ["No hay ninguna copia en marcha."]


def test_la_copia_programada_se_hace_sola_y_calla(ventana, agentes: Path, destino: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui import avisos
	from tests.ayudas_wx import esperar_hasta

	monkeypatch.setattr(copias, "mismo_disco_que_casa", lambda _ruta: False)
	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *_a, **_k: dichos.append(texto))
	monkeypatch.setattr(avisos, "notificar", lambda texto, *_a, **_k: dichos.append(texto))
	copias.guardar_programa(copias.Programa("diaria", str(destino), 3))
	ventana.comprobar_copia_programada()
	esperar_hasta(lambda: bool(list(destino.glob("CtrlIA-copia-*.zip"))) and not ventana.tareas.activa("copia-seguridad"), 20,
		"la copia programada no se ha hecho")
	assert not dichos and not copias.copia_pendiente()


def test_una_programada_que_falla_avisa_una_vez(ventana, tmp_path: Path, agentes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui import avisos
	from tests.ayudas_wx import esperar_hasta

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "notificar", lambda texto, *_a, **_k: dichos.append(texto))
	copias.guardar_programa(copias.Programa("diaria", str(tmp_path / "desconectado"), 3))
	ventana.comprobar_copia_programada()
	esperar_hasta(lambda: bool(dichos), 20, "no se ha avisado del fallo")
	assert "no es una carpeta" in dichos[0] and "desconectado" in copias.texto_fallo()
	assert not copias.copia_pendiente()  # se reintenta dentro de una hora


def test_programar_guarda_el_programa_y_la_frase(ventana, destino: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	import wx

	from tests.ayudas_wx import esperar_hasta

	programa = copias.Programa("semanal", str(destino), 4, True)
	_dialogo_falso(monkeypatch, "DialogoProgramarCopias", wx.ID_OK, programa=programa, frase_nueva=FRASE)
	ventana.accion_programar_copias()
	esperar_hasta(lambda: copias.cargar_programa() == programa, 10, "no se ha guardado")
	esperar_hasta(copias.hay_frase, 10, "no se ha guardado la frase")
	assert copias.cargar_frase() == FRASE
	assert FRASE not in json.dumps(copias.cargar_estado())
	assert FRASE not in (ventana.config.ruta.read_text(encoding="utf-8") if ventana.config.ruta.exists() else "")


def test_comprobar_pide_la_frase_si_hace_falta(ventana, agentes: Path, destino: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui import vp_copias
	from tests.ayudas_wx import esperar_hasta

	ruta = copias.crear(destino, frase=FRASE).ruta
	pedidas: list[str] = []
	mensajes: list[str] = []
	monkeypatch.setattr(vp_copias, "pedir_texto", lambda *_a, **_k: pedidas.append("frase") or FRASE)
	monkeypatch.setattr(vp_copias, "mostrar_mensaje", lambda _p, texto, *_a, **_k: mensajes.append(texto))
	monkeypatch.setattr(ventana, "_elegir_copia", lambda _titulo: ruta)
	ventana.accion_comprobar_copia()
	esperar_hasta(lambda: bool(mensajes), 20, "no ha terminado de comprobar")
	assert pedidas == ["frase"] and "está entera" in mensajes[0]


def test_restaurar_desde_la_ventana(ventana, copia_hecha: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	import wx

	import app.ui.dialogos.copias as modulo
	from app.ui import vp_copias
	from tests.ayudas_wx import esperar_hasta

	class Falso:
		def __init__(self, _padre, sesiones, _informacion, **_k) -> None:
			self.elegidas = [s for s in sesiones if s.proveedor == "claude"]

		def ShowModal(self):  # noqa: N802 - API de wx
			return wx.ID_OK

		def Destroy(self):  # noqa: N802 - API de wx
			return None

	monkeypatch.setattr(modulo, "DialogoRestaurarCopia", Falso)
	mensajes: list[str] = []
	monkeypatch.setattr(vp_copias, "mostrar_mensaje", lambda _p, texto, *_a, **_k: mensajes.append(texto))
	monkeypatch.setattr(ventana, "_elegir_copia", lambda _titulo: copia_hecha)
	ventana.accion_restaurar_copia()
	esperar_hasta(lambda: bool(mensajes), 20, "no ha terminado de restaurar")
	assert "restaurada" in mensajes[0]
	assert len(ventana.almacen.sesiones_importadas()) == 2
	assert any(f.importada for f in ventana.gestor.listar())


def test_restaurar_una_cifrada_pregunta_la_frase_y_la_repite_si_es_mala(ventana, agentes: Path, destino: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from app.ui import vp_copias
	from tests.ayudas_wx import esperar_hasta

	ruta = copias.crear(destino, frase=FRASE).ruta
	frases = iter(["no es esta", None])
	pedidas: list[str] = []

	def pedir(*_a, **_k):
		pedidas.append("frase")
		return next(frases)

	monkeypatch.setattr(vp_copias, "pedir_texto", pedir)
	monkeypatch.setattr(ventana, "_elegir_copia", lambda _titulo: ruta)
	ventana.accion_restaurar_copia()
	esperar_hasta(lambda: len(pedidas) == 2, 20, "no ha vuelto a pedir la frase")
