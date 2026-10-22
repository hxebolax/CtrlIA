"""Sesiones que ya existen en otros equipos por SSH (sin red ni equipo real).

El ``ssh`` se sustituye por respuestas preparadas; los guiones POSIX se
ejecutan de verdad solo si hay un ``sh`` en este equipo (el de Git para Windows).
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path, PurePosixPath

import pytest

from app.services.agentes import remoto
from app.services.historial import claude as historial_claude
from app.services.historial import equipos

MAC = {"id": "mac-de-jose", "nombre": "Mac de José", "destino": "jose@192.168.1.40", "sistema": "macos"}
RAIZ = "/Users/jose/.claude/projects/-Users-jose-web"
ID_TERMINAL = "11111111-2222-3333-4444-555555555555"
ID_ESCRITORIO = "66666666-7777-8888-9999-000000000000"
ID_SDK = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _equipo(**cambios) -> remoto.EquipoSSH:
	return remoto.EquipoSSH.desde_dict({**MAC, **cambios})


def _transcript(entrada: str, pregunta: str, respuesta: str, titulo: str = "", relleno: int = 0) -> bytes:
	lineas = [
		{"type": "user", "entrypoint": entrada, "cwd": "/Users/jose/web", "timestamp": "2026-10-20T10:00:00Z",
			"message": {"role": "user", "content": pregunta}},
		{"type": "assistant", "timestamp": "2026-10-20T10:00:05Z",
			"message": {"role": "assistant", "content": [{"type": "text", "text": "Mirando..."}]}},
	]
	texto = "\n".join(json.dumps(linea, ensure_ascii=False) for linea in lineas) + "\n"
	if relleno:
		texto += json.dumps({"type": "progress", "relleno": "x" * relleno}) + "\n"
	final = [{"type": "assistant", "timestamp": "2026-10-20T10:01:00Z",
		"message": {"role": "assistant", "content": [{"type": "text", "text": respuesta}]}}]
	if titulo:
		final.append({"type": "ai-title", "aiTitle": titulo})
	texto += "\n".join(json.dumps(linea, ensure_ascii=False) for linea in final) + "\n"
	return texto.encode("utf-8")


def _b64gz(datos: bytes) -> str:
	return base64.b64encode(gzip.compress(datos)).decode("ascii")


# ── El equipo ────────────────────────────────────────────────────────────────


def test_el_equipo_muestra_sus_sesiones_salvo_que_se_desmarque():
	assert _equipo().historial is True  # equipos guardados antes de esta opción
	assert _equipo(historial=False).historial is False
	assert remoto.EquipoSSH.desde_dict(_equipo(historial=False).a_dict()).historial is False
	assert _equipo().nombre_sistema == "macOS" and _equipo(sistema="linux").nombre_sistema == "Linux"


# ── Lo que devuelve el otro equipo ───────────────────────────────────────────


def test_leer_inventario_tolerante():
	vivo = base64.b64encode(json.dumps({"pid": 812, "sessionId": ID_TERMINAL, "status": "busy"}).encode()).decode()
	salida = "\n".join([
		"Último inicio de sesión: ayer",  # lo que imprima el shell de inicio se ignora
		"@@proyectos",
		f"300554 1792479911 {RAIZ}/{ID_TERMINAL}.jsonl",
		"stat: no such file",
		"@@escritorio",
		"812 1792479000 /Users/jose/Library/Application Support/Claude/claude-code-sessions/a/b/local_1.json",
		"@@vivos",
		f"@@v {vivo}",
		"@@v no-es-base64",
		"@@codex",
		"si",
		"@@fin",
	])
	inventario = equipos.leer_inventario(salida)
	assert inventario.transcripts == {f"{RAIZ}/{ID_TERMINAL}.jsonl": (300554, 1792479911)}
	assert list(inventario.escritorio.values()) == [(812, 1792479000)]
	assert next(iter(inventario.escritorio)).endswith("Application Support/Claude/claude-code-sessions/a/b/local_1.json")
	assert inventario.vivos == [{"pid": 812, "sessionId": ID_TERMINAL, "status": "busy"}]
	assert inventario.codex is True


def test_datos_de_un_transcript_grande_por_trozos():
	contenido = _transcript("cli", "Arregla el menú", "Hecho, el menú ya funciona", titulo="Menú roto", relleno=300_000)
	cabeza, cola = contenido[:equipos.TAM_CABEZA], contenido[-equipos.TAM_COLA:]
	salida = f"@@t {len(contenido)} {RAIZ}/{ID_TERMINAL}.jsonl\n{_b64gz(cabeza)}\n{_b64gz(cola)}\n@@x /ya/no/esta.jsonl\n"
	trozos, archivos = equipos.leer_trozos(salida)
	assert archivos == {}
	tamano, cabeza_leida, cola_leida = trozos[f"{RAIZ}/{ID_TERMINAL}.jsonl"]
	datos = historial_claude.datos_de_trozos(cabeza_leida, cola_leida, tamano)
	assert (datos.entrada, datos.carpeta, datos.primer_mensaje) == ("cli", "/Users/jose/web", "Arregla el menú")
	assert datos.titulo == "Menú roto"
	assert datos.ultimo_mensaje == "Hecho, el menú ya funciona"


def test_datos_de_un_transcript_pequeno_y_trozo_danado():
	contenido = _transcript("cli", "Hola", "Adiós")
	datos = historial_claude.datos_de_trozos(contenido, contenido, len(contenido))
	assert datos.primer_mensaje == "Hola" and datos.ultimo_mensaje == "Adiós"
	trozos, _ = equipos.leer_trozos(f"@@t 10 /a.jsonl\nno-es-base64!!\n{_b64gz(b'')}\n")
	assert trozos["/a.jsonl"] == (10, b"", b"")


def test_sesiones_de_otro_equipo():
	grande = _transcript("cli", "Arregla el menú", "Listo", titulo="Menú roto")
	escritorio = _transcript("claude-desktop", "Revisa las pruebas", "Pasan todas")
	sdk = _transcript("sdk-cli", "Responde ok", "ok")
	transcripts = {
		f"{RAIZ}/{ID_TERMINAL}.jsonl": (historial_claude.datos_de_trozos(grande, grande, len(grande)), 1792479911.0),
		f"{RAIZ}/{ID_ESCRITORIO}.jsonl": (historial_claude.datos_de_trozos(escritorio, escritorio, len(escritorio)), 1792470000.0),
		f"{RAIZ}/{ID_SDK}.jsonl": (historial_claude.datos_de_trozos(sdk, sdk, len(sdk)), 1792400000.0),
	}
	metadatos = [{"sessionId": "local_abc", "cliSessionId": ID_ESCRITORIO, "title": "Pruebas del Mac",
		"cwd": "/Users/jose/web", "createdAt": 1792460000000, "lastActivityAt": 1792465000000}]
	vivos = historial_claude.estado_vivo_de_datos([{"pid": 9, "sessionId": ID_TERMINAL, "status": "busy"}])
	sesiones = historial_claude.sesiones_de_otro_equipo("mac-de-jose", transcripts, metadatos, vivos)
	por_id = {s.id: s for s in sesiones}
	assert set(por_id) == {ID_TERMINAL, ID_ESCRITORIO}  # el claude -p de otro programa no sale
	terminal = por_id[ID_TERMINAL]
	assert (terminal.origen, terminal.titulo, terminal.estado) == ("terminal", "Menú roto", "trabajando")
	assert terminal.equipo == "mac-de-jose" and terminal.ruta_remota == f"{RAIZ}/{ID_TERMINAL}.jsonl"
	assert terminal.ruta_transcript is None
	app = por_id[ID_ESCRITORIO]
	assert (app.origen, app.titulo, app.ultimo_mensaje) == ("escritorio", "Pruebas del Mac", "Pasan todas")
	assert historial_claude.url_abrir_en_claude(app) is None  # la app de Windows no la tiene
	assert sesiones[0].id == ID_TERMINAL  # la más reciente primero


# ── Leer un equipo: solo se trae lo que ha cambiado ──────────────────────────


def test_leer_claude_trae_solo_lo_cambiado(monkeypatch):
	contenido = _transcript("cli", "Arregla el menú", "Listo", titulo="Menú roto")
	ruta = f"{RAIZ}/{ID_TERMINAL}.jsonl"
	inventario = f"@@proyectos\n{len(contenido)} 1792479911 {ruta}\n@@escritorio\n@@vivos\n@@codex\n@@fin\n"
	pedidos = []

	def ejecutar(equipo, guion, entrada, tiempo):
		assert equipo.destino == "jose@192.168.1.40"
		if entrada:
			pedidos.append(entrada.decode("utf-8"))
			assert ruta not in guion  # las rutas van por la entrada estándar, nunca en la orden
			return 0, f"@@t {len(contenido)} {ruta}\n{_b64gz(contenido)}\n{_b64gz(contenido)}\n".encode(), ""
		assert "stat -f '%z %m %N'" in guion  # macOS: stat de BSD
		return 0, inventario.encode(), ""

	monkeypatch.setattr(equipos, "_ejecutar", ejecutar)
	lectura = equipos.Lectura()
	assert equipos.leer_claude(_equipo(), lectura) is False
	assert pedidos == [f"t {ruta}\n"]
	assert [s.titulo for s in lectura.claude] == ["Menú roto"]
	equipos.leer_claude(_equipo(), lectura)
	assert len(pedidos) == 1  # sin cambios en el otro equipo, no se vuelve a traer nada
	inventario = "@@proyectos\n@@escritorio\n@@vivos\n@@codex\nsi\n@@fin\n"
	assert equipos.leer_claude(_equipo(), lectura) is True
	assert lectura.claude == [] and lectura.transcripts == {}


def test_leer_claude_sin_respuesta_valida(monkeypatch):
	monkeypatch.setattr(equipos, "_ejecutar", lambda *a: (127, b"", "sh: find: not found"))
	with pytest.raises(remoto.ErrorRemoto, match="no devolvió"):
		equipos.leer_claude(_equipo(), equipos.Lectura())


def test_linux_usa_stat_de_gnu():
	assert "stat -c '%s %Y %n'" in equipos.guion_inventario("linux")
	assert "Library/Application Support/Claude" in equipos.guion_inventario("macos")


# ── Copia local para leer una sesión ─────────────────────────────────────────


def test_copiar_transcript_incremental(monkeypatch, tmp_path):
	remoto_archivo = bytearray(b"linea 1\n")
	llamadas = []

	def ejecutar(equipo, guion, entrada, tiempo):
		desde, ruta = entrada.decode().strip().split(" ", 1)
		llamadas.append(int(desde))
		if ruta == "/no/existe.jsonl":
			return 3, b"", ""
		if int(desde) > len(remoto_archivo):
			return 4, b"", ""
		return 0, gzip.compress(bytes(remoto_archivo[int(desde):])), ""

	monkeypatch.setattr(equipos, "_ejecutar", ejecutar)
	destino = tmp_path / "copia" / f"{ID_TERMINAL}.jsonl"
	equipos.copiar_transcript(_equipo(), "/r.jsonl", destino)
	assert destino.read_bytes() == b"linea 1\n"
	remoto_archivo += b"linea 2\n"
	equipos.copiar_transcript(_equipo(), "/r.jsonl", destino)
	assert destino.read_bytes() == b"linea 1\nlinea 2\n" and llamadas[-1] == 8  # solo lo añadido
	equipos.copiar_transcript(_equipo(), "/r.jsonl", destino, minimo=60)
	assert len(llamadas) == 2  # se acaba de traer: no se vuelve a conectar
	remoto_archivo[:] = b"otra\n"  # reescrito: más corto que la copia
	equipos.copiar_transcript(_equipo(), "/r.jsonl", destino)
	assert destino.read_bytes() == b"otra\n" and llamadas[-2:] == [16, 0]
	with pytest.raises(remoto.ErrorRemoto, match="ya no está"):
		equipos.copiar_transcript(_equipo(), "/no/existe.jsonl", tmp_path / "otra.jsonl")
	with pytest.raises(remoto.ErrorRemoto):
		equipos.copiar_transcript(_equipo(), "relativa\n/etc/passwd", tmp_path / "x.jsonl")


# ── En segundo plano ─────────────────────────────────────────────────────────


def test_historial_equipos_en_segundo_plano(monkeypatch):
	listo = threading.Event()
	sesion = historial_claude.SesionClaude(clave=f"claude:{ID_TERMINAL}", id=ID_TERMINAL, titulo="Menú roto",
		carpeta="/Users/jose/web", origen="terminal", creada=1.0, actividad=2.0, estado="inactiva", archivada=False,
		ruta_transcript=None, equipo="mac-de-jose", ruta_remota="/r.jsonl")

	def leer(equipo, lectura, incluir_archivadas=True):
		lectura.claude = [sesion]
		return True

	monkeypatch.setattr(equipos, "leer_claude", leer)
	opciones = {"agentes.equipos_ssh": [MAC, {**MAC, "id": "apagado", "nombre": "Apagado", "historial": False}]}
	historial = equipos.HistorialEquipos(lambda clave, defecto: opciones.get(clave, defecto),
		listar_codex=lambda equipo: [{"id": "hilo-1", "titulo": "Hilo del Mac"}], al_cambiar=listo.set)
	assert set(historial.lecturas()) == {"mac-de-jose"}  # el desmarcado no se lee
	assert listo.wait(10)
	lectura = historial.lecturas()["mac-de-jose"][1]
	assert lectura.claude == [sesion] and lectura.codex == [{"id": "hilo-1", "titulo": "Hilo del Mac", "equipo": "mac-de-jose"}]
	assert historial.estado() == []


def test_historial_equipos_avisa_una_vez_si_no_responde(monkeypatch):
	avisos = []

	def falla(equipo, lectura, incluir_archivadas=True):
		raise remoto.ErrorRemoto("El otro equipo no responde.")

	monkeypatch.setattr(equipos, "leer_claude", falla)
	historial = equipos.HistorialEquipos(lambda clave, defecto: [MAC] if clave == "agentes.equipos_ssh" else defecto,
		al_cambiar=lambda: avisos.append(1))
	historial.lecturas()
	for _ in range(200):
		if not historial.lecturas()["mac-de-jose"][1].leyendo:
			break
		threading.Event().wait(0.05)
	historial._leer(_equipo())  # el mismo fallo otra vez no vuelve a avisar
	assert historial.estado() == [("Mac de José", "El otro equipo no responde.")]
	assert len(avisos) == 1


# ── El gestor: lista, mensajes y «Continuar aquí» ────────────────────────────


@pytest.fixture
def gestor_con_mac(tmp_path, monkeypatch, data_temporal):
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones
	from tests.test_gestor import ProveedorFalso

	class ProveedorClaudeFalso(ProveedorFalso):
		id = "claude"

	almacen = Almacen(tmp_path / "g.db")
	opciones = {"agentes.equipos_ssh": [MAC], "historial.antigravity": False, "historial.ollama": False,
		"historial.codex": False}
	monkeypatch.setattr(remoto, "_opcion", lambda clave, defecto: opciones.get(clave, defecto))
	gestor = GestorSesiones(almacen, {"claude": ProveedorClaudeFalso()}, lambda c, e: None,
		lambda clave, defecto: opciones.get(clave, defecto))
	contenido = _transcript("cli", "Arregla el menú", "Hecho, el menú ya funciona", titulo="Menú roto")
	ruta = f"{RAIZ}/{ID_TERMINAL}.jsonl"
	lectura = equipos.Lectura(claude=historial_claude.sesiones_de_otro_equipo("mac-de-jose", {
		ruta: (historial_claude.datos_de_trozos(contenido, contenido, len(contenido)), 1792479911.0)}, [], {}))
	monkeypatch.setattr(gestor.historial_equipos, "lecturas", lambda forzar=False: {"mac-de-jose": (_equipo(), lectura)})

	def copiar(equipo, ruta_remota, destino, minimo=0.0):
		assert ruta_remota == ruta and equipo.id == "mac-de-jose"
		destino.parent.mkdir(parents=True, exist_ok=True)
		destino.write_bytes(contenido)
		return destino

	monkeypatch.setattr(equipos, "copiar_transcript", copiar)
	yield gestor, almacen
	almacen.cerrar()


def test_gestor_lista_las_sesiones_del_mac(gestor_con_mac):
	gestor, _almacen = gestor_con_mac
	fila = next(f for f in gestor.listar() if f.id_externo == ID_TERMINAL)
	assert (fila.titulo, fila.carpeta, fila.equipo, fila.sistema) == ("Menú roto", "/Users/jose/web", "Mac de José", "macOS")
	assert fila.remota and not fila.propia
	assert fila.nombre_origen == "Terminal en Mac de José (SSH)"
	assert fila.nombre_equipo == "Mac de José (macOS)"
	textos = [m.texto for m in gestor.mensajes(fila.clave)]
	assert "Arregla el menú" in textos and "Hecho, el menú ya funciona" in textos


def test_gestor_continua_en_el_mac(gestor_con_mac):
	gestor, almacen = gestor_con_mac
	fila = next(f for f in gestor.listar() if f.id_externo == ID_TERMINAL)
	clave = gestor.continuar_aqui(fila.clave)
	sesion = almacen.sesion(clave)
	assert sesion.bifurcada_de == ID_TERMINAL and sesion.carpeta == "/Users/jose/web"
	assert sesion.extra["remoto"] == {"equipo": "mac-de-jose", "nombre": "Mac de José", "carpeta": "/Users/jose/web"}
	assert PurePosixPath(sesion.carpeta).is_absolute()
	propia = next(f for f in gestor.listar() if f.clave == clave)
	assert propia.nombre_origen == "Ctrl+IA en Mac de José (SSH)" and propia.sistema == "macOS"


def test_sesiones_de_windows_dicen_este_equipo():
	from app.services.gestor import FilaSesion

	fila = FilaSesion(clave="claude:x", proveedor="claude", titulo="t", origen="terminal")
	assert not fila.remota and fila.nombre_equipo == "Este equipo (Windows)" and fila.nombre_origen == "Terminal"
	importada = FilaSesion(clave="importada:x", proveedor="claude", titulo="t", origen="importada", equipo="PC")
	assert not importada.remota and importada.nombre_origen == "Importada de PC"


def test_columna_y_vistas_de_equipo(app_wx):
	from app.services.gestor import FilaSesion
	from app.ui.vp_sesiones import COLUMNAS, VISTAS, nombre_equipo, valores_sesion

	assert "equipo" in COLUMNAS and {"equipo:este", "equipo:otros"} <= {v for v, _e in VISTAS}
	mac = FilaSesion(clave="claude:m", proveedor="claude", titulo="t", origen="terminal", equipo="Mac de José", sistema="macOS")
	local = FilaSesion(clave="claude:w", proveedor="claude", titulo="t", origen="terminal")
	assert valores_sesion(mac, ["equipo"], lambda c, d: d) == ["Mac de José (macOS)"]
	assert nombre_equipo(local) == "Este equipo (Windows)"


# ── Los guiones de verdad, con un sh de este equipo ──────────────────────────


def _buscar_sh() -> str | None:
	for candidato in (shutil.which("sh"), r"C:\Program Files\Git\usr\bin\sh.exe", r"C:\Program Files\Git\bin\sh.exe"):
		if candidato and Path(candidato).is_file():
			return candidato
	return None


SH = _buscar_sh()


def _posix(ruta: Path) -> str:
	texto = str(ruta).replace("\\", "/")
	return f"/{texto[0].lower()}{texto[2:]}" if len(texto) > 1 and texto[1] == ":" else texto


@pytest.mark.skipif(SH is None, reason="no hay un sh en este equipo")
def test_guiones_en_un_sh_real(tmp_path):
	casa = tmp_path / "casa"
	proyectos = casa / ".claude" / "projects" / "-Users-jose-web"
	proyectos.mkdir(parents=True)
	contenido = _transcript("cli", "Arregla el menú", "Hecho", titulo="Menú roto", relleno=200_000)
	(proyectos / f"{ID_TERMINAL}.jsonl").write_bytes(contenido)
	entorno = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
	entorno["HOME"] = _posix(casa)

	def ejecutar(guion: str, entrada: bytes = b"") -> subprocess.CompletedProcess:
		return subprocess.run([SH, "-c", guion], input=entrada, capture_output=True, env=entorno, timeout=60, check=False)

	resultado = ejecutar(equipos.guion_inventario("linux"))
	inventario = equipos.leer_inventario(resultado.stdout.decode("utf-8", "replace"))
	if not inventario.transcripts:
		pytest.skip("este sh no tiene stat de GNU: " + resultado.stderr.decode("utf-8", "replace")[:200])
	ruta = next(iter(inventario.transcripts))
	assert inventario.transcripts[ruta][0] == len(contenido)
	resultado = ejecutar(equipos.GUION_TROZOS, f"t {ruta}\n".encode())
	trozos, _ = equipos.leer_trozos(resultado.stdout.decode("utf-8", "replace"))
	tamano, cabeza, cola = trozos[ruta]
	datos = historial_claude.datos_de_trozos(cabeza, cola, tamano)
	assert (datos.titulo, datos.primer_mensaje, datos.ultimo_mensaje) == ("Menú roto", "Arregla el menú", "Hecho")
	resultado = ejecutar(equipos.GUION_COPIA, f"0 {ruta}\n".encode())
	assert resultado.returncode == 0 and gzip.decompress(resultado.stdout) == contenido
	assert ejecutar(equipos.GUION_COPIA, f"{len(contenido) + 5} {ruta}\n".encode()).returncode == 4
	assert ejecutar(equipos.GUION_COPIA, b"0 /no/existe.jsonl\n").returncode == 3
