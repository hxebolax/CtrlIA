"""Novedades de Codex en Ctrl+IA 1.6.0 (paquete CODEX).

Contra el ``codex app-server`` falso (``tests/fakes/fake_codex.py``) y bases SQLite de
prueba: sin Codex real, sin red, sin ventanas y sin tocar ``~/.codex`` de quien las pasa.

1. Peticiones de subagentes (AIChat #521).
2. Historial de Codex en SQLite (BlindPilot).
3. Mensajes largos en un archivo (AIChat #625).
4. Hilo que Codex ya no tiene (AIChat codex_owned).
5. «Permitir con cualquier argumento» con PowerShell envolviendo la orden (AIChat #478).
6. Comandos de barra y skills de Codex (AIChat #600 y #467).
7. Actividad en vivo: salida de órdenes, subagentes y terminales en segundo plano.
8. Instrucciones y carpetas adicionales (AIChat #527).
9. Servidores MCP de Codex (AIChat #554 y #626).
10. Comandos de sesión (ccd_session_mgmt) también para Codex (AIChat #557).
11. Bifurcar o reintentar desde un mensaje.
12. Preguntar aparte (AIChat codex_aside).
13. Preguntas de Codex fuera del modo Plan (BlindPilot).
"""

from __future__ import annotations

import http.client
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from app.services import herramientas_propias as hp
from app.services import mensajes_largos
from app.services.agentes import codex
from app.services.agentes.base import CAMPOS_ACTIVIDAD, MAX_INSTRUCCIONES, AjustesSesion, RespuestaPermiso, TipoEvento
from app.services.agentes.codex import (
	COMANDOS_CODEX,
	MENSAJE_AJUSTES_PENDIENTES,
	MENSAJE_HILO_DESAPARECIDO,
	NADA_TERMINADO,
	ErrorCodex,
	ProveedorCodex,
	comando_de,
	contexto_de_regla,
	orden_y_contexto,
	palabras_orden,
	prefijo_de_regla,
	regla_de_orden,
)
from app.services.historial import codex as historial_codex
from tests.test_agente_codex import ESPERA, FALSO, Recolector, entorno, esperar_registro, peticiones  # noqa: F401

# ── Ayudas ───────────────────────────────────────────────────────────────────


def esperar_que(recolector: Recolector, condicion, tiempo: float = ESPERA) -> list:
	"""Espera a que algún evento cumpla ``condicion`` y devuelve los que la cumplen."""
	limite = time.monotonic() + tiempo
	while time.monotonic() < limite:
		encontrados = [e for e in list(recolector.eventos) if condicion(e)]
		if encontrados:
			return encontrados
		time.sleep(0.02)
	raise AssertionError("No llegó el evento esperado; eventos: %s" % [(e.tipo.value, e.texto[:60]) for e in recolector.eventos])


def avisos(recolector: Recolector) -> list[str]:
	return [e.texto for e in recolector.de_tipo(TipoEvento.AVISO)]


def respuestas_cliente(registro: Path) -> list[dict]:
	"""Respuestas que el cliente (Ctrl+IA) mandó al servidor falso."""
	from tests.test_agente_codex import leer_registro

	return [m for m in leer_registro(registro) if "method" not in m and "id" in m]


# ── 1. Peticiones de subagentes (AIChat #521) ────────────────────────────────


def test_la_peticion_de_un_subagente_llega_a_la_conversacion_que_lo_lanzo(entorno):
	_proveedor, conversacion, eventos, registro = entorno("subagente")
	conversacion.enviar("hola")
	permiso = eventos.esperar(TipoEvento.PERMISO)[0]
	solicitud = permiso.datos["solicitud"]
	assert solicitud.titulo == "Petición de un subagente de Codex: quiere modificar archivos"
	assert "subagente" in solicitud.detalle and "hijo.txt" in solicitud.detalle and "+del hijo" in solicitud.detalle
	assert solicitud.datos["subagente"] == "hilo-hijo"
	# Subió por la cadena de padres con thread/read sin turnos (hijo → intermedio → la nuestra).
	lecturas = [m["params"] for m in peticiones(registro, "thread/read")]
	assert {"threadId": "hilo-hijo", "includeTurns": False} in lecturas
	assert {"threadId": "hilo-intermedio", "includeTurns": False} in lecturas
	conversacion.responder(solicitud.id, RespuestaPermiso("accept"))
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert eventos.de_tipo(TipoEvento.MENSAJE)[0].texto == "Subagente: accept"


def test_la_de_un_hilo_que_no_desciende_de_ninguna_conversacion_se_rechaza(entorno):
	_proveedor, conversacion, eventos, registro = entorno("subagente_huerfano")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert not eventos.de_tipo(TipoEvento.PERMISO)
	assert eventos.de_tipo(TipoEvento.MENSAJE)[0].texto == "Subagente: error"
	rechazo = next(m for m in respuestas_cliente(registro) if m.get("id") == 901)
	assert rechazo["error"]["code"] == codex.ERROR_NO_ADMITIDO


def test_la_peticion_de_un_subagente_que_ya_termino_no_se_ensena_ni_se_contesta(entorno):
	_proveedor, conversacion, eventos, registro = entorno("subagente_retirado")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	time.sleep(0.3)
	permisos = {e.datos["solicitud"].id for e in eventos.de_tipo(TipoEvento.PERMISO)}
	retirados = {e.datos["id"] for e in eventos.de_tipo(TipoEvento.PERMISO_RETIRADO)}
	assert permisos <= retirados
	assert not [m for m in respuestas_cliente(registro) if m.get("id") == 901]


def test_sin_turno_en_marcha_no_se_acepta_la_de_un_subagente(tmp_path):
	proveedor = ProveedorCodex(orden=["codex-que-no-existe"])
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda _e: None)
	assert conversacion._al_peticion_hija(5, "item/fileChange/requestApproval", {}, "hijo", []) is False  # noqa: SLF001


def test_el_lector_nunca_espera_y_los_trabajadores_estan_acotados(monkeypatch):
	servidor = codex._ServidorCodex(["no-se-lanza"])  # noqa: SLF001
	rechazadas: list[Any] = []
	monkeypatch.setattr(servidor, "responder_error", lambda ident, *_a: rechazadas.append(ident))
	lanzados: list[tuple] = []
	monkeypatch.setattr(codex, "_en_hilo", lambda funcion, *args, nombre="": lanzados.append(args))
	for _ in range(codex.MAX_TRABAJADORES_SUBAGENTES):
		servidor._peticion_servidor(len(lanzados) + 1, "item/fileChange/requestApproval", {"threadId": "hijo"})  # noqa: SLF001
	assert len(lanzados) == codex.MAX_TRABAJADORES_SUBAGENTES and not rechazadas
	servidor._peticion_servidor(99, "item/fileChange/requestApproval", {"threadId": "hijo"})  # noqa: SLF001
	assert rechazadas == [99]  # sin trabajador libre: se rechaza, nunca se espera
	servidor._peticion_servidor(100, "item/tool/call", {"threadId": "hijo"})  # noqa: SLF001
	assert rechazadas == [99, 100]  # lo que no es para la persona, ni se intenta


# ── 2. Historial en SQLite ───────────────────────────────────────────────────

ID_SQL = "01a1baef-3e62-73f2-bf7c-b8beb884f65b"
ID_SUB = "01a1baef-0000-7000-8000-000000000001"


def crear_bases(carpeta: Path, numero_estado: int = 5, numero_historia: int = 1) -> tuple[Path, Path]:
	"""Bases de Codex de prueba con el esquema que tiene codex-cli 0.167 (solo las columnas que se leen)."""
	carpeta.mkdir(parents=True, exist_ok=True)
	estado = carpeta / f"state_{numero_estado}.sqlite"
	historia = carpeta / f"thread_history_{numero_historia}.sqlite"
	with sqlite3.connect(estado) as base:
		base.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL, "
			"updated_at INTEGER NOT NULL, source TEXT NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL, archived INTEGER NOT NULL "
			"DEFAULT 0, first_user_message TEXT NOT NULL DEFAULT '')")
		base.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)", [
			(ID_SQL, "C:\\no-existe.jsonl", 1772990000, 1772994330, "vscode", "\\\\?\\C:\\proyecto", "Arreglar el fallo", 0, "Arregla"),
			(ID_SUB, "x", 1772990000, 1772994000, '{"subagent":{"thread_spawn":{"parent_thread_id":"x"}}}', "C:\\p", "Sub", 0, ""),
			("01a1baef-0000-7000-8000-000000000002", "x", 1, 2, "cli", "C:\\viejo", "", 1, "Archivado"),
		])
	largo = mensajes_largos.mensaje_para("x" * 40000, mensajes_largos.carpeta() / "ctrlia-mensaje-largo-abc.txt")
	with sqlite3.connect(historia) as base:
		base.execute("CREATE TABLE thread_items (thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, item_id TEXT NOT NULL, "
			"rollout_ordinal INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, item_json TEXT NOT NULL, item_type TEXT NOT NULL DEFAULT '')")
		filas = [
			(ID_SQL, "t1", "u1", 1, 1772994000000, {"type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "Busca la canción"}]}),
			(ID_SQL, "t1", "r1", 2, 1772994001000, {"type": "reasoning", "id": "r1"}),
			(ID_SQL, "t1", "a1", 3, 1772994002000, {"type": "agentMessage", "id": "a1", "text": "Encontrada"}),
			(ID_SQL, "t2", "u2", 4, 1772994003000, {"type": "userMessage", "id": "u2", "content": [{"type": "text", "text": largo}]}),
		]
		base.executemany("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?)",
			[(a, b, c, d, e, json.dumps(f), f["type"]) for a, b, c, d, e, f in filas])
	return estado, historia


def test_lee_hilos_y_mensajes_de_las_bases_sqlite_de_codex(tmp_path, monkeypatch):
	casa = tmp_path / ".codex"
	monkeypatch.setenv("CODEX_HOME", str(casa))
	monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
	crear_bases(casa, 2, 1)  # una versión antigua del formato...
	estado, historia = crear_bases(casa, 5, 3)  # ...y la de ahora, que es la que se usa
	antes = {r.name: r.read_bytes() for r in casa.iterdir()}
	assert historial_codex.bases_codex() == (estado, historia)
	hilos = historial_codex.hilos_sqlite()
	assert [h["id"] for h in hilos] == [ID_SQL]  # ni el subagente ni el archivado
	assert hilos[0]["carpeta"] == "C:\\proyecto" and hilos[0]["titulo"] == "Arreglar el fallo"
	assert hilos[0]["actualizado"] == 1772994330
	assert [h["titulo"] for h in historial_codex.hilos_sqlite(archivados=True)] == ["Archivado"]
	mensajes = historial_codex.mensajes_sqlite(ID_SQL)
	assert [(m["rol"], m["texto"], m["ancla"]) for m in mensajes] == [
		("usuario", "Busca la canción", "t1:u1"), ("asistente", "Encontrada", "t1:a1"),
		("usuario", "(enviado como archivo, 40.000 caracteres)", "t2:u2")]
	assert mensajes[0]["fecha"] == 1772994000
	assert historial_codex.tamano_sqlite(ID_SQL) > 0 and historial_codex.tamano_sqlite("otro") is None
	resultados = historial_codex.buscar_en_hilos([ID_SQL, "no-existe"], lambda t: "canción" in t)
	assert [(i, m["texto"]) for i, m in resultados] == [(ID_SQL, "Busca la canción")]
	# Nunca se escribe en la carpeta de Codex.
	assert {r.name: r.read_bytes() for r in casa.iterdir()} == antes


def test_las_bases_movidas_con_sqlite_home_tambien_se_leen(tmp_path, monkeypatch):
	casa = tmp_path / ".codex"
	casa.mkdir()
	(casa / "config.toml").write_text('sqlite_home = "bases"\n', encoding="utf-8")
	monkeypatch.setenv("CODEX_HOME", str(casa))
	crear_bases(casa / "bases")
	assert [h["id"] for h in historial_codex.hilos_sqlite()] == [ID_SQL]


def test_sin_bases_ni_rollouts_no_falla(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path / "vacia"))
	assert historial_codex.hilos_sqlite() == [] and historial_codex.mensajes_sqlite(ID_SQL) == []
	assert historial_codex.buscar_en_hilos([ID_SQL], lambda t: True) == []
	(tmp_path / "rota").mkdir()
	(tmp_path / "rota" / "state_5.sqlite").write_text("no es una base", encoding="utf-8")
	assert historial_codex.hilos_sqlite(tmp_path / "rota" / "state_5.sqlite") == []


def test_restaurar_codex_de_una_copia_con_sus_bases_sqlite(tmp_path, monkeypatch, data_temporal):
	from app.services import copias
	from app.services.almacen import Almacen

	casa = tmp_path / "casa"
	for variable, valor in (("USERPROFILE", casa), ("HOME", casa), ("APPDATA", casa / "AppData" / "Roaming"),
			("LOCALAPPDATA", casa / "AppData" / "Local"), ("CLAUDE_CONFIG_DIR", casa / ".claude"), ("CODEX_HOME", casa / ".codex")):
		monkeypatch.setenv(variable, str(valor))
	monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
	monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
	monkeypatch.setenv("COMPUTERNAME", "PORTATIL")
	crear_bases(casa / ".codex")
	destino = tmp_path / "destino"
	destino.mkdir()
	assert copias._nombre_restaurable("codex/casa/state_5.sqlite")  # noqa: SLF001
	assert copias._nombre_restaurable("codex/sqlite/thread_history_12.sqlite")  # noqa: SLF001
	assert not copias._nombre_restaurable("codex/casa/logs_2.sqlite")  # noqa: SLF001
	ruta = copias.crear(destino).ruta
	almacen = Almacen(data_temporal / "otra.db")
	try:
		with copias.CopiaAbierta(ruta) as copia:
			por_clave = {s.clave: s for s in copia.sesiones}
			assert set(por_clave) == {f"codex:{ID_SQL}"}
			sesion = por_clave[f"codex:{ID_SQL}"]
			assert sesion.titulo == "Arreglar el fallo" and sesion.carpeta == "C:\\proyecto"
			resultado = copia.restaurar(copia.sesiones, almacen)
		assert resultado.nuevas == 1 and not resultado.fallidas
		clave = almacen.sesiones_importadas()[f"PORTATIL|codex:{ID_SQL}"]
		mensajes = almacen.mensajes(clave)
		assert [(m.rol, m.texto) for m in mensajes][:2] == [("usuario", "Busca la canción"), ("asistente", "Encontrada")]
	finally:
		almacen.cerrar()


# ── 3. Mensajes largos (AIChat #625) ─────────────────────────────────────────


def test_modulo_de_mensajes_largos(data_temporal):
	texto = "ñ" * 33000
	ruta = mensajes_largos.guardar(texto)
	assert ruta.parent == mensajes_largos.carpeta() == data_temporal / "temp"
	assert ruta.name.startswith("ctrlia-mensaje-largo-") and ruta.suffix == ".txt"
	assert ruta.read_bytes().startswith(b"\xef\xbb\xbf") and ruta.read_text(encoding="utf-8-sig") == texto
	mensaje = mensajes_largos.mensaje_para(texto, ruta)
	assert mensaje.startswith(mensajes_largos.APERTURA) and ("\nruta: %s\n" % ruta) in mensaje and "33.000 caracteres" in mensaje
	assert mensajes_largos.ruta_de(mensaje, en_carpeta=True) == str(ruta)
	assert mensajes_largos.texto_visible(mensaje) == "(enviado como archivo, 33.000 caracteres)"
	# Solo se reconoce al principio y con un archivo de los suyos.
	assert mensajes_largos.texto_visible("Mira: " + mensaje) == "Mira: " + mensaje
	assert mensajes_largos.ruta_de(mensaje.replace(ruta.name, "otro.txt")) is None
	assert not mensajes_largos.hace_falta("x" * mensajes_largos.LIMITE) and mensajes_largos.hace_falta("x" * (mensajes_largos.LIMITE + 1))
	ajeno = data_temporal / "temp" / "ajeno.txt"
	ajeno.write_text("no tocar", encoding="utf-8")
	mensajes_largos.borrar([ruta, ajeno])
	assert not ruta.exists() and ajeno.exists()


def test_limpiar_restos_solo_borra_lo_de_antes_de_arrancar(data_temporal):
	viejo = mensajes_largos.guardar("viejo")
	nuevo = mensajes_largos.guardar("nuevo")
	ajeno = data_temporal / "temp" / "ctrlia-mensaje-largo-otro.log"
	ajeno.write_text("x", encoding="utf-8")
	import os

	pasado = time.time() - 3600
	os.utime(viejo, (pasado, pasado))
	assert mensajes_largos.limpiar_restos(antes=time.time() - 60) == 1
	assert not viejo.exists() and nuevo.exists() and ajeno.exists()


def test_un_mensaje_largo_va_en_un_archivo_que_se_borra_al_acabar_el_turno(entorno, data_temporal):
	_proveedor, conversacion, eventos, registro = entorno("normal")
	texto = "línea larga " * 4000  # 48.000 caracteres
	conversacion.enviar(texto)
	eventos.esperar(TipoEvento.FIN_TURNO)
	enviado = peticiones(registro, "turn/start")[0]["params"]["input"][0]["text"]
	assert enviado.startswith(mensajes_largos.APERTURA) and len(enviado) < 2000
	# El falso lee el archivo como haría Codex: estaba entero y con BOM.
	assert eventos.de_tipo(TipoEvento.MENSAJE)[0].texto == "Leído: %d caracteres, bom=True" % len(texto)
	assert not list((data_temporal / "temp").glob("ctrlia-mensaje-largo-*"))
	assert codex.item_a_mensaje({"type": "userMessage", "id": "u", "content": [{"type": "text", "text": enviado}]})["texto"] \
		== "(enviado como archivo, 48.000 caracteres)"


# ── 4. Hilo que Codex ya no tiene ────────────────────────────────────────────


def test_un_hilo_que_codex_ya_no_tiene_se_dice_claro(entorno):
	proveedor, conversacion, eventos, registro = entorno("desaparecido", id_externo="hilo-perdido")
	conversacion.enviar("hola")
	error = eventos.esperar(TipoEvento.ERROR)[0]
	assert error.texto == MENSAJE_HILO_DESAPARECIDO and error.datos["hilo_desaparecido"] is True
	listas = [m["params"] for m in peticiones(registro, "thread/list")]
	assert {lista["archived"] for lista in listas} == {False, True}
	assert all(lista["modelProviders"] == [] and "exec" in lista["sourceKinds"] for lista in listas)
	with pytest.raises(ErrorCodex) as excepcion:
		proveedor.leer_hilo("hilo-perdido")
	assert excepcion.value.hilo_desaparecido and excepcion.value.mensaje == MENSAJE_HILO_DESAPARECIDO


def test_un_fallo_sin_confirmar_no_dice_que_el_hilo_desaparecio(tmp_path):
	class Lista:
		def peticion(self, metodo, params=None, tiempo=None):
			if params.get("archived"):
				raise ErrorCodex("roto")
			return {"data": [], "nextCursor": None}

	assert codex.hilo_desaparecido(Lista(), "x") is False  # una lista que falla no prueba nada


def test_listar_hilos_pide_todos_los_origenes_y_proveedores(entorno):
	proveedor, _conversacion, _eventos, registro = entorno("normal")
	proveedor.listar_hilos()
	params = peticiones(registro, "thread/list")[0]["params"]
	assert params["modelProviders"] == [] and params["sourceKinds"] == ["cli", "vscode", "exec", "appServer", "unknown"]


# ── 5. PowerShell envolviendo la orden (AIChat #478) ─────────────────────────

POWERSHELL = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


@pytest.fixture
def windows(monkeypatch, tmp_path):
	monkeypatch.setattr(codex, "ES_WINDOWS", True)
	monkeypatch.setenv("SystemRoot", "C:\\Windows")
	monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
	monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
	return tmp_path


def test_desenvuelve_solo_interpretes_de_confianza(windows):
	contexto = POWERSHELL + " -command"
	assert orden_y_contexto('"%s" -Command "git status --short"' % POWERSHELL) == (["git", "status", "--short"], contexto)
	assert orden_y_contexto('"%s" -NoProfile -Command "git commit -m \'Arreglo\'"' % POWERSHELL.upper()) == (
		["git", "commit", "-m", "Arreglo"], POWERSHELL.upper() + " -noprofile -command")
	pwsh7 = "C:\\Program Files\\PowerShell\\7\\pwsh.exe"
	assert orden_y_contexto(['%s' % pwsh7, "-Command", "npm test"]) == (["npm", "test"], pwsh7 + " -command")
	assert orden_y_contexto(["/bin/bash", "-lc", "npm run build"]) == (["npm", "run", "build"], "/bin/bash -lc")
	# Nada que el intérprete pueda interpretar dentro del guion.
	for guion in ("git log (Get-Date)", "git status @args", "rm *.py", "git status; rm x", "echo $HOME", "git {a,b}", "echo ñ"):
		assert orden_y_contexto('"%s" -Command "%s"' % (POWERSHELL, guion)) is None, guion
	# Otras opciones del intérprete no se desenvuelven.
	assert orden_y_contexto('"%s" -ExecutionPolicy Bypass -Command "git status"' % POWERSHELL) is None
	assert orden_y_contexto('"%s" -EncodedCommand ZwBpAHQA' % POWERSHELL) is None
	# Un intérprete que no es de confianza (otra ruta, o el nombre suelto) se queda como orden normal: no permitible.
	for orden in ('"C:\\Temp\\powershell.exe" -Command "git status"', 'pwsh -Command "git status"', 'bash -c "npm test"'):
		palabras, contexto = orden_y_contexto(orden)
		assert contexto == "" and not codex.prefijo_permitible(palabras[:2]), orden
	# Una orden directa, como siempre.
	assert palabras_orden("git status --short") == ["git", "status", "--short"]


def test_el_alias_de_la_tienda_solo_si_de_verdad_lo_es(windows, monkeypatch):
	tienda = windows / "local" / "Microsoft" / "WindowsApps" / "pwsh.exe"
	tienda.parent.mkdir(parents=True)
	tienda.write_bytes(b"MZ")  # un archivo normal puesto en su lugar
	orden = '"%s" -Command "git status"' % tienda
	assert orden_y_contexto(orden)[1] == ""
	monkeypatch.setattr(codex, "_es_alias_tienda", lambda ruta: True)
	assert orden_y_contexto(orden) == (["git", "status"], "%s -command" % tienda)


def test_la_regla_queda_atada_al_interprete_exacto(windows, monkeypatch):
	contexto = POWERSHELL + " -command"
	regla = regla_de_orden(["git", "status"], contexto)
	assert regla == "Orden(git status; %s)" % contexto
	assert prefijo_de_regla(regla) == ["git", "status"] and contexto_de_regla(regla) == contexto
	assert contexto_de_regla("Orden(git status)") == "" and prefijo_de_regla("Orden(git status)") == ["git", "status"]
	carpeta = windows / "proyecto"
	(carpeta / ".git").mkdir(parents=True)
	proveedor = ProveedorCodex(orden=["codex-que-no-existe"])
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=carpeta), lambda _e: None)
	conversacion.ajustes.reglas_permitidas.append(regla)
	envuelta = {"command": '"%s" -Command "git status --short"' % POWERSHELL, "cwd": str(carpeta)}
	assert conversacion._regla_que_permite(envuelta) == regla  # noqa: SLF001
	# La misma orden sin PowerShell, u otro PowerShell, no la cubre.
	assert conversacion._regla_que_permite({"command": "git status", "cwd": str(carpeta)}) == ""  # noqa: SLF001
	otro = '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "git status"'
	assert conversacion._regla_que_permite({"command": otro, "cwd": str(carpeta)}) == ""  # noqa: SLF001
	# Lo de git que nunca se permite sigue sin permitirse dentro de PowerShell.
	forzar = {"command": '"%s" -Command "git push --force"' % POWERSHELL, "cwd": str(carpeta)}
	conversacion.ajustes.reglas_permitidas.append(regla_de_orden(["git", "push"], contexto))
	assert conversacion._regla_que_permite(forzar) == ""  # noqa: SLF001
	# Y solo en la carpeta raíz de la sesión.
	(carpeta / "sub").mkdir()
	assert conversacion._regla_que_permite(dict(envuelta, cwd=str(carpeta / "sub"))) == ""  # noqa: SLF001


def test_la_opcion_ofrecida_dice_el_interprete(windows):
	carpeta = windows / "proyecto"
	(carpeta / ".git").mkdir(parents=True)
	conversacion = ProveedorCodex(orden=["x"]).crear_conversacion(AjustesSesion(carpeta=carpeta), lambda _e: None)
	solicitud = conversacion._solicitud_orden(7, {"command": '"%s" -Command "git status"' % POWERSHELL, "cwd": str(carpeta),  # noqa: SLF001
		"proposedExecpolicyAmendment": ["git", "status"]})
	opcion = next(o for o in solicitud.opciones if o.id == codex.OPCION_PERMITIR_SESION)
	assert opcion.etiqueta == "Permitir «git status» con cualquier argumento dentro de PowerShell durante esta sesión"
	assert solicitud.datos["regla"] == "Orden(git status; %s -command)" % POWERSHELL
	assert "este mismo intérprete" in opcion.ayuda


# ── 6. Comandos de barra y skills ────────────────────────────────────────────


def test_reconoce_solo_los_comandos_de_codex():
	assert comando_de("/compact") == ("/compact", "")
	assert comando_de("  /PLAN ") == ("/plan", "")
	assert comando_de("/memories off") == ("/memories", "off")
	assert comando_de("/goal pause") == ("/goal", "pause") and comando_de("/goal") == ("/goal", "")
	for texto in ("/memories", "/goal arregla todo", "/review", "hola /compact", "/compact ya"):
		assert comando_de(texto) is None, texto
	assert ProveedorCodex.capacidades.comandos_barra and ProveedorCodex.capacidades.compactar
	assert [c for c, _d in COMANDOS_CODEX][:2] == ["/compact", "/plan"]


def test_modo_plan_memorias_y_objetivos(entorno):
	_proveedor, conversacion, eventos, registro = entorno("normal")
	conversacion.enviar("/plan")
	esperar_que(eventos, lambda e: e.tipo is TipoEvento.AVISO and e.datos.get("modo_colaboracion") == "plan")
	assert peticiones(registro, "collaborationMode/list")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	turno = peticiones(registro, "turn/start")[0]["params"]
	assert turno["collaborationMode"] == {"mode": "plan", "settings": {"model": "gpt-falso", "reasoning_effort": None,
		"developer_instructions": None}}
	assert peticiones(registro, "thread/start")[0]["params"]["config"]["features.goals"] is False
	conversacion.enviar("/memories off")
	esperar_registro(registro, "thread/memoryMode/set")
	assert peticiones(registro, "thread/memoryMode/set")[0]["params"] == {"threadId": "hilo-1", "mode": "disabled"}
	conversacion.enviar("/goal")
	esperar_que(eventos, lambda e: e.tipo is TipoEvento.AVISO and "Terminar la 1.6.0" in e.texto)
	conversacion.enviar("/goal pause")
	esperar_registro(registro, "thread/goal/set")
	assert peticiones(registro, "thread/goal/set")[0]["params"] == {"threadId": "hilo-1", "status": "paused", "origin": "user"}
	conversacion.enviar("/goal clear")
	esperar_registro(registro, "thread/goal/clear")
	conversacion.enviar("/plan")
	esperar_que(eventos, lambda e: e.tipo is TipoEvento.AVISO and e.datos.get("modo_colaboracion") == "default")
	# Ningún comando se ha enviado como mensaje.
	assert len(peticiones(registro, "turn/start")) == 1


def test_sin_preajuste_plan_no_se_cambia_nada(entorno):
	_proveedor, conversacion, eventos, registro = entorno("sin_plan")
	conversacion.enviar("/plan")
	esperar_que(eventos, lambda e: e.tipo is TipoEvento.AVISO and "no tiene el modo Plan" in e.texto)
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert "collaborationMode" not in peticiones(registro, "turn/start")[0]["params"]


def test_compactar(entorno):
	_proveedor, conversacion, eventos, registro = entorno("normal")
	conversacion.enviar("/compact")
	esperar_que(eventos, lambda e: e.tipo is TipoEvento.AVISO and "nada que compactar" in e.texto)
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	conversacion.enviar("/compact")
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	assert peticiones(registro, "thread/compact/start")[0]["params"] == {"threadId": "hilo-1"}
	assert any(e.datos.get("compactando") for e in eventos.de_tipo(TipoEvento.AVISO))
	assert any(e.datos.get("compactado") for e in eventos.de_tipo(TipoEvento.AVISO))
	# Con un ajuste cambiado que Codex aún no tiene, primero un mensaje normal (AIChat).
	conversacion.cambiar_ajustes(modelo="otro")
	with pytest.raises(RuntimeError, match="envía antes un mensaje normal"):
		conversacion.compactar()
	assert MENSAJE_AJUSTES_PENDIENTES.startswith("Has cambiado")


def test_skills_de_codex_en_los_comandos_y_en_el_mensaje(entorno, tmp_path):
	proveedor, conversacion, eventos, registro = entorno("normal")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	comandos = eventos.esperar(TipoEvento.COMANDOS)[0].datos["comandos"]
	nombres = [c for c, _d in comandos]
	assert "$revisar" in nombres and "$apagada" not in nombres and "/compact" in nombres
	assert peticiones(registro, "skills/list")[0]["params"] == {"cwds": [str(tmp_path)], "forceReload": True}
	conversacion.enviar("usa $revisar ahora, no $revisarlo")
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	entrada = peticiones(registro, "turn/start")[1]["params"]["input"]
	assert entrada[1:] == [{"type": "skill", "name": "revisar", "path": "C:\\skills\\revisar\\SKILL.md"}]
	assert ("$revisar", "Revisa el código") in proveedor.obtener_comandos(tmp_path)


def test_como_mucho_21_elementos_en_un_mensaje():
	skills = [("s%d" % n, "C:\\s%d\\SKILL.md" % n) for n in range(30)]
	assert len(codex._entrada("hola", (), skills=skills)) == codex.MAX_ELEMENTOS_ENTRADA  # noqa: SLF001


# ── 7. Actividad en vivo ─────────────────────────────────────────────────────


def test_salida_de_ordenes_y_subagentes_como_actividad(entorno):
	_proveedor, conversacion, eventos, _registro = entorno("actividad")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	actividad = eventos.de_tipo(TipoEvento.ACTIVIDAD)
	assert all(set(e.datos) <= set(CAMPOS_ACTIVIDAD) for e in actividad)
	orden = [e.datos for e in actividad if e.datos["id"] == "orden-viva"]
	assert orden[0]["clase"] == "orden" and orden[0]["estado"] == "en_marcha" and orden[0]["orden"] == "npm test"
	assert "".join(d.get("salida", "") for d in orden) == "uno\ndos\ntres\n"
	assert orden[-1]["estado"] == "completada"
	assert len(orden) <= 4  # la salida se agrupa: no un evento por trozo
	sub = [e.datos for e in actividad if e.datos["id"] == "hilo-sub"]
	assert sub[0]["clase"] == "subagente" and sub[0]["encargo"] == "Revisa las pruebas" and sub[0]["tipo_agente"] == "gpt-mini"
	assert sub[-1]["estado"] == "completada" and sub[-1]["resumen"] == "Todo en orden"
	otro = [e.datos for e in actividad if e.datos["id"] == "hilo-otro"]
	assert otro == [{"id": "hilo-otro", "clase": "subagente", "estado": "completada", "segundo_plano": True, "titulo": "/root/revisor"}]


def test_la_salida_viva_se_recorta(tmp_path, monkeypatch):
	recolector = Recolector()
	conversacion = ProveedorCodex(orden=["x"]).crear_conversacion(AjustesSesion(carpeta=tmp_path), recolector)
	monkeypatch.setattr(codex, "MAX_SALIDA_VIVA", 10)
	conversacion._item_empezado({"type": "commandExecution", "id": "o", "command": "x"})  # noqa: SLF001
	for _ in range(5):
		conversacion._salida_orden({"itemId": "o", "delta": "123456"})  # noqa: SLF001
		conversacion._ordenes_vivas["o"]["ultimo"] = 0.0  # noqa: SLF001
	salida = "".join(e.datos.get("salida", "") for e in recolector.de_tipo(TipoEvento.ACTIVIDAD))
	assert salida.startswith("1234561234") and "recortada" in salida


def test_terminales_en_segundo_plano_y_detenerlos(entorno):
	_proveedor, conversacion, eventos, registro = entorno("terminales")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	terminal = esperar_que(eventos, lambda e: e.tipo is TipoEvento.ACTIVIDAD and e.datos["id"] == "proc-1")[0].datos
	assert terminal["detenible"] is True and terminal["segundo_plano"] is True and terminal["orden"] == "npm run dev"
	assert peticiones(registro, "thread/backgroundTerminals/list")[0]["params"] == {"threadId": "hilo-1", "limit": 100}
	conversacion.detener_tarea("proc-1")
	assert peticiones(registro, "thread/backgroundTerminals/terminate")[0]["params"] == {"threadId": "hilo-1", "processId": "proc-1"}
	assert eventos.de_tipo(TipoEvento.ACTIVIDAD)[-1].datos == {"id": "proc-1", "estado": "detenida", "detenible": False}
	conversacion.detener_tarea("proc-acabado")
	assert "ya había terminado" in avisos(eventos)[-1]
	assert ProveedorCodex.capacidades.detener_tareas


def test_detener_sin_codex_abierto_lo_dice(tmp_path):
	conversacion = ProveedorCodex(orden=["x"]).crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda _e: None)
	with pytest.raises(RuntimeError, match="no hay tareas"):
		conversacion.detener_tarea("proc-1")


# ── 8. Instrucciones y carpetas adicionales ──────────────────────────────────


def test_instrucciones_y_carpetas_adicionales(entorno, tmp_path):
	otra = tmp_path / "otra"
	otra.mkdir()
	extra = {"instrucciones": "Responde en verso. " + "x" * MAX_INSTRUCCIONES,
		"carpetas_adicionales": [str(otra), "C:\\", "relativa", "\\\\?\\C:\\x", str(otra)]}
	_proveedor, conversacion, eventos, registro = entorno("normal", extra=extra)
	conversacion.enviar("uno")
	eventos.esperar(TipoEvento.FIN_TURNO)
	conversacion.enviar("dos")
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	inicio = peticiones(registro, "thread/start")[0]["params"]
	assert inicio["config"]["sandbox_workspace_write.writable_roots"] == [str(otra)]  # nunca una unidad entera
	for turno in peticiones(registro, "turn/start"):
		params = turno["params"]
		contexto = params["additionalContext"]["ctrlia_instrucciones"]
		assert contexto["kind"] == "application" and contexto["value"].startswith("Responde en verso.")
		assert len(contexto["value"]) == MAX_INSTRUCCIONES
		assert params["sandboxPolicy"] == {"type": "workspaceWrite", "writableRoots": [str(tmp_path), str(otra)]}
		assert params["approvalPolicy"] == "on-request"
	assert ProveedorCodex.capacidades.instrucciones_sesion


def test_en_solo_lectura_no_hay_carpetas_que_escribir(entorno, tmp_path):
	extra = {"carpetas_adicionales": [str(tmp_path)]}
	_proveedor, conversacion, eventos, registro = entorno("normal", modo_permiso="solo_lectura", extra=extra)
	conversacion.enviar("uno")
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert "sandbox_workspace_write.writable_roots" not in peticiones(registro, "thread/start")[0]["params"]["config"]
	assert "sandboxPolicy" not in peticiones(registro, "turn/start")[0]["params"]
	assert "additionalContext" not in peticiones(registro, "turn/start")[0]["params"]


# ── 9. Servidores MCP ─────────────────────────────────────────────────────────


def test_servidores_mcp_de_codex(entorno):
	_proveedor, conversacion, eventos, registro = entorno("normal")
	assert conversacion.mcp_en_vivo() is False
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	servidores = {s.nombre: s for s in conversacion.servidores_mcp()}
	assert conversacion.mcp_en_vivo() is True
	assert peticiones(registro, "mcpServerStatus/list")[0]["params"] == {"detail": "toolsAndAuthOnly", "threadId": "hilo-1"}
	assert servidores["github"].estado == "connected" and servidores["github"].herramientas == 2
	assert servidores["notion"].estado == "needs-auth"
	assert servidores["roto"].estado == "failed" and "se cierra al arrancar" in servidores["roto"].motivo
	conversacion.reconectar_mcp("roto")
	assert peticiones(registro, "config/mcpServer/reload")[0]["params"] == {}
	assert conversacion.url_inicio_sesion_mcp("notion") == "https://ejemplo.com/autorizar"
	assert peticiones(registro, "mcpServer/oauth/login")[0]["params"] == {"name": "notion", "timeoutSecs": 180, "threadId": "hilo-1"}
	esperar_que(eventos, lambda e: e.tipo is TipoEvento.AVISO and "Has iniciado sesión" in e.texto and e.datos.get("mcp") == "notion")


def test_inicio_de_sesion_mcp_que_falla(entorno):
	_proveedor, conversacion, eventos, _registro = entorno("oauth_falla")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	conversacion.url_inicio_sesion_mcp("notion")
	esperar_que(eventos, lambda e: e.tipo is TipoEvento.AVISO and "No se ha completado" in e.texto and "denegado" in e.texto)


# ── 10. Comandos de sesión también para Codex (AIChat #557) ──────────────────


class SesionesFalsas:
	def __init__(self) -> None:
		self.retirada = False
		self.recibido: list[Any] = []

	def atender(self, mensaje, al_usar):
		self.recibido.append(mensaje)
		al_usar("ocultar_sesion", {"sesion": "ctrlia:x", "titulo": "X", "motivo": ""})
		return {"jsonrpc": "2.0", "id": mensaje.get("id"), "result": {"ok": True}}


def _post(url: str, token: str, cuerpo: dict) -> tuple[int, Any]:
	"""Como Codex: POST con el token, sin cerrar la conexión hasta leer la respuesta."""
	partes = urlsplit(url)
	conexion = http.client.HTTPConnection(partes.hostname, partes.port, timeout=10)
	try:
		conexion.request("POST", partes.path, body=json.dumps(cuerpo).encode("utf-8"),
			headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
		respuesta = conexion.getresponse()
		datos = respuesta.read()
		return respuesta.status, json.loads(datos) if datos else None
	finally:
		conexion.close()


def test_los_comandos_de_sesion_van_a_codex_por_http_con_el_mismo_token():
	usos: list[tuple] = []
	sesion = hp.registrar(lambda accion, datos: usos.append((accion, datos)), "Codex")
	try:
		config = hp.config_codex(sesion, sesiones=True)
		base = hp.url_servidor()
		assert config["mcp_servers.ccd_session_mgmt.url"] == base + "/ccd_session_mgmt"
		assert config["mcp_servers.ccd_session_mgmt.http_headers"] == {"Authorization": "Bearer " + sesion.token}
		assert config["mcp_servers.ccd_session_mgmt.default_tools_approval_mode"] == "prompt"
		from app.services.herramientas_sesiones import SIN_PREGUNTAR

		for herramienta in SIN_PREGUNTAR:
			assert config["mcp_servers.ccd_session_mgmt.tools.%s.approval_mode" % herramienta] == "approve"
		assert "mcp_servers.ccd_session_mgmt.tools.archive_session.approval_mode" not in config
		assert not any("ccd_session_mgmt" in clave for clave in hp.config_codex(sesion))
		url = config["mcp_servers.ccd_session_mgmt.url"]
		# Sin comandos de sesión en esa sesión, la ruta no existe.
		assert _post(url, sesion.token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0] == 404
		sesion.sesiones = SesionesFalsas()
		codigo, respuesta = _post(url, sesion.token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
		assert codigo == 200 and respuesta["result"] == {"ok": True}
		assert usos and usos[0][0] == "ocultar_sesion"
		assert _post(url, "otro-token", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})[0] == 401
		assert _post(url, hp._token_enrutado, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})[0] == 404  # noqa: SLF001
	finally:
		hp.retirar(sesion)


def test_el_hilo_de_codex_lleva_los_comandos_de_sesion(entorno, monkeypatch):
	monkeypatch.setattr(hp, "activadas", lambda: True)
	_proveedor, conversacion, eventos, registro = entorno("normal")
	conversacion.herramientas_sesiones = SesionesFalsas()
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	config = peticiones(registro, "thread/start")[0]["params"]["config"]
	assert config["mcp_servers.ccd_session_mgmt.url"].endswith("/mcp/ccd_session_mgmt")
	assert conversacion.herramientas_ctrlia.sesiones is conversacion.herramientas_sesiones
	conversacion.cerrar()
	assert conversacion.herramientas_sesiones.retirada is True


# ── 11. Bifurcar o reintentar desde un mensaje ───────────────────────────────


def test_los_mensajes_llevan_su_ancla(entorno):
	proveedor, conversacion, eventos, _registro = entorno("normal")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert eventos.de_tipo(TipoEvento.MENSAJE)[0].datos["ancla"] == "turno-1:msg-1"
	mensajes = proveedor.leer_hilo("hilo-1")
	assert [(m["rol"], m.get("ancla")) for m in mensajes] == [("usuario", "t1:u1"), ("herramienta", None), ("asistente", "t1:a1")]
	assert ProveedorCodex.capacidades.bifurcar_en_mensaje


@pytest.mark.parametrize("modo,clave", [("bifurcar", "lastTurnId"), ("reintentar", "beforeTurnId")])
def test_bifurcar_o_reintentar_desde_un_mensaje(entorno, modo, clave):
	extra = {"cortar_en": {"modo": modo, "ancla": "turno-2:msg-2"}}
	_proveedor, conversacion, eventos, registro = entorno("normal", bifurcar_de="hilo-origen", extra=extra)
	conversacion.enviar("otra vez")
	eventos.esperar(TipoEvento.FIN_TURNO)
	lista = peticiones(registro, "thread/turns/list")[0]["params"]
	assert lista == {"threadId": "hilo-origen", "sortDirection": "desc", "limit": 100, "itemsView": "notLoaded"}
	bifurcacion = peticiones(registro, "thread/fork")[0]["params"]
	assert bifurcacion[clave] == "turno-2" and bifurcacion["threadId"] == "hilo-origen"
	assert ({"lastTurnId", "beforeTurnId"} - {clave}).isdisjoint(bifurcacion)
	assert "cortar_en" not in conversacion.ajustes.extra and conversacion.id_externo == "hilo-bifurcado"


def test_un_ancla_que_ya_no_esta_no_bifurca(entorno):
	extra = {"cortar_en": {"modo": "bifurcar", "ancla": "turno-9:msg"}}
	_proveedor, conversacion, eventos, registro = entorno("normal", bifurcar_de="hilo-origen", extra=extra)
	conversacion.enviar("otra vez")
	error = eventos.esperar(TipoEvento.ERROR)[0]
	assert "ya no corresponde" in error.texto and not peticiones(registro, "thread/fork")


def test_no_se_bifurca_un_turno_en_marcha():
	class Turnos:
		def peticion(self, metodo, params=None, tiempo=None):
			return {"data": [{"id": "vivo", "status": "inProgress"}], "nextCursor": None}

	with pytest.raises(ErrorCodex, match="aún está trabajando"):
		codex.verificar_turno(Turnos(), "h", "vivo")
	codex.verificar_turno(Turnos(), "h", "vivo", antes=True)  # reintentar corta antes: vale
	assert codex.turno_de_ancla("t-1:i") == "t-1" and codex.turno_de_ancla("sin-dos-puntos") == ""


# ── 12. Preguntar aparte ─────────────────────────────────────────────────────


def _proveedor_aparte(tmp_path: Path, escenario: str = "normal") -> tuple[ProveedorCodex, Path]:
	registro = tmp_path / ("aparte-%s.jsonl" % escenario)
	return ProveedorCodex(orden=[sys.executable, str(FALSO), escenario, str(registro)]), registro


def test_preguntar_aparte_en_otro_proceso_y_de_solo_lectura(tmp_path):
	proveedor, registro = _proveedor_aparte(tmp_path)
	trozos: list[str] = []
	ajustes = AjustesSesion(carpeta=tmp_path, id_externo="hilo-1", modelo="gpt-falso")
	respuesta = proveedor.preguntar_aparte(ajustes, "¿Qué has hecho?", al_texto=trozos.append)
	assert respuesta == "Respuesta aparte a: ¿Qué has hecho?"
	assert trozos and trozos[-1] == respuesta
	assert proveedor._servidor is None  # noqa: SLF001 - el proceso de las sesiones no se ha tocado
	copia = peticiones(registro, "thread/fork")[0]["params"]
	assert copia["ephemeral"] is True and copia["excludeTurns"] is True and copia["lastTurnId"] == "turno-2"
	assert copia["sandbox"] == "read-only" and copia["approvalPolicy"] == "never"
	assert all(copia["config"]["features.%s" % f] is False for f in codex.FUNCIONES_APAGADAS_APARTE)
	assert copia["config"]["mcp_servers.github.enabled"] is False
	assert not any("raro.nombre" in clave for clave in copia["config"])
	turno = peticiones(registro, "turn/start")[0]["params"]
	assert turno["threadId"] == "hilo-aparte" and turno["approvalPolicy"] == "never"
	assert turno["sandboxPolicy"] == {"type": "readOnly"}
	assert ProveedorCodex.capacidades.preguntar_aparte


def test_preguntar_aparte_usa_el_ultimo_turno_terminado(tmp_path):
	proveedor, registro = _proveedor_aparte(tmp_path, "aparte_vivo")
	proveedor.preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo="hilo-1"), "¿Y?")
	assert peticiones(registro, "thread/fork")[0]["params"]["lastTurnId"] == "turno-2"


def test_preguntar_aparte_sin_nada_terminado_o_mal_pedido(tmp_path):
	proveedor, _registro = _proveedor_aparte(tmp_path, "sin_turnos")
	with pytest.raises(RuntimeError) as error:
		proveedor.preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo="hilo-1"), "¿Y?")
	assert str(error.value) == NADA_TERMINADO
	with pytest.raises(RuntimeError, match="todavía no tiene"):
		proveedor.preguntar_aparte(AjustesSesion(carpeta=tmp_path), "¿Y?")
	with pytest.raises(RuntimeError, match="demasiado larga"):
		proveedor.preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo="hilo-1"), "x" * codex.MAX_PREGUNTA_APARTE)
	remota = AjustesSesion(carpeta=tmp_path, id_externo="hilo-1", extra={"remoto": {"equipo": "mac", "carpeta": "/Users/x"}})
	with pytest.raises(RuntimeError, match="otro equipo"):
		proveedor.preguntar_aparte(remota, "¿Y?")
	cancelar = threading.Event()
	cancelar.set()
	proveedor2, _ = _proveedor_aparte(tmp_path)
	with pytest.raises(RuntimeError, match="cancelado"):
		proveedor2.preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo="hilo-1"), "¿Y?", cancelar=cancelar)


def test_la_pregunta_aparte_rechaza_cualquier_peticion():
	rechazadas: list[Any] = []

	class Srv:
		def responder_error(self, ident, codigo, texto):
			rechazadas.append((ident, codigo))

	aparte = codex._Aparte(None)  # noqa: SLF001
	aparte.srv = Srv()
	aparte._al_peticion(3, "item/tool/requestUserInput", {})  # noqa: SLF001
	assert rechazadas == [(3, codex.ERROR_NO_ADMITIDO)] and aparte._al_peticion_hija() is False  # noqa: SLF001


# ── 13. Preguntas de Codex fuera del modo Plan ───────────────────────────────


def test_preguntas_de_codex_solo_con_la_opcion(tmp_path, monkeypatch):
	monkeypatch.setattr("app.core.configuracion._instancia", None, raising=False)
	ejecutable = tmp_path / "codex.exe"
	assert ProveedorCodex(ejecutable=ejecutable).orden_servidor() == [str(ejecutable), "app-server", "--listen", "stdio://"]
	casa = tmp_path / ".codex"
	casa.mkdir()
	(casa / "config.toml").write_text('developer_instructions = "Habla de tú."\n', encoding="utf-8")
	orden = ProveedorCodex(ejecutable=ejecutable, preguntas=lambda: True).orden_servidor()
	assert orden[:4] == [str(ejecutable), "app-server", "--listen", "stdio://"]
	argumentos = orden[4:]
	assert argumentos[:4] == ["-c", "tools.experimental_request_user_input={enabled=true}", "--enable",
		"default_mode_request_user_input"]
	assert argumentos[4] == "-c" and argumentos[5].startswith("developer_instructions=")
	instrucciones = json.loads(argumentos[5].split("=", 1)[1])
	assert "request_user_input" in instrucciones and "lector de pantalla" in instrucciones
	# Las de la persona no se pierden: van delante.
	assert json.loads(codex.argumentos_preguntas(casa)[5].split("=", 1)[1]).startswith("Habla de tú.\n\n")
	# «Preguntar aparte» nunca las lleva.
	assert ProveedorCodex(ejecutable=ejecutable, preguntas=True).orden_servidor(preguntas=False)[4:] == []
