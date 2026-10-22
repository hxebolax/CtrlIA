"""Pruebas del agente Antigravity (``agy`` falso, reglas, IDE y conversaciones)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services.agentes import antigravity
from app.services.agentes.antigravity import (
	ConversacionAntigravity,
	ErrorAjustesAntigravity,
	ProveedorAntigravity,
	abrir_carpeta_en_antigravity,
	anadir_regla,
	buscar_antigravity_app,
	buscar_antigravity_ide,
	describir_denegacion,
	instalar_cli_orden,
	leer_reglas,
	listar_conversaciones_escritorio,
	orden_abrir_carpeta,
	quitar_regla,
)
from app.services.agentes.base import Adjunto, AjustesSesion, Estado, RespuestaPermiso, TipoEvento

#: La función original, antes de que el conftest la sustituya en cada prueba.
_RUTA_AJUSTES_CLI_REAL = antigravity.ruta_ajustes_cli

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_agy.py"


class Recolector:
	"""Recoge eventos desde los hilos del agente y permite esperarlos."""

	def __init__(self) -> None:
		self.eventos = []
		self._condicion = threading.Condition()

	def __call__(self, evento) -> None:
		with self._condicion:
			self.eventos.append(evento)
			self._condicion.notify_all()

	def de_tipo(self, tipo):
		with self._condicion:
			return [e for e in self.eventos if e.tipo is tipo]

	def esperar(self, condicion, segundos: float = 15.0) -> bool:
		limite = time.monotonic() + segundos
		with self._condicion:
			while not condicion():
				restante = limite - time.monotonic()
				if restante <= 0:
					return False
				self._condicion.wait(restante)
		return True

	def esperar_fines(self, cuantos: int, segundos: float = 15.0) -> bool:
		return self.esperar(lambda: sum(e.tipo is TipoEvento.FIN_TURNO for e in self.eventos) >= cuantos, segundos)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_AGY_REGISTRO", str(registro))
	proyecto = tmp_path / "proyecto"
	proyecto.mkdir()
	return {"registro": registro, "proyecto": proyecto, "ajustes": tmp_path / "settings.json"}


@pytest.fixture
def conversar(entorno):
	creadas = []

	def crear(**ajustes):
		proveedor = ProveedorAntigravity(ejecutable=[sys.executable, str(FAKE)], ruta_ajustes=entorno["ajustes"])
		recolector = Recolector()
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=entorno["proyecto"], **ajustes), recolector)
		creadas.append(conversacion)
		return conversacion, recolector

	yield crear
	for conversacion in creadas:
		conversacion.cerrar()


def arranques(registro: Path) -> list[list[str]]:
	if not registro.exists():
		return []
	return [json.loads(linea)["argv"] for linea in registro.read_text(encoding="utf-8").splitlines() if linea.strip()]


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


def test_turno_normal_con_trozos(conversar, entorno):
	conversacion, eventos = conversar()
	conversacion.enviar("Hola")
	assert eventos.esperar_fines(1)
	iniciado = eventos.de_tipo(TipoEvento.INICIADO)
	assert len(iniciado) == 1 and iniciado[0].datos["id_externo"] == "conv-fake-1"
	assert conversacion.id_externo == "conv-fake-1"
	parciales = [e.texto for e in eventos.de_tipo(TipoEvento.TEXTO_PARCIAL)]
	assert parciales == ["Has dicho: ", "Hola", "."]
	mensajes = eventos.de_tipo(TipoEvento.MENSAJE)
	assert [m.texto for m in mensajes] == ["Has dicho: Hola."]
	assert mensajes[0].datos["id"] == eventos.de_tipo(TipoEvento.TEXTO_PARCIAL)[0].datos["id"]
	usos = eventos.de_tipo(TipoEvento.USO)
	assert usos and usos[-1].datos["entrada"] == 100 and usos[-1].datos["salida"] == 20
	fin = eventos.de_tipo(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["correcto"] is True and fin.datos["interrumpido"] is False
	assert eventos.esperar(lambda: conversacion.estado is Estado.INACTIVA)
	estados = [e.datos["estado"] for e in eventos.de_tipo(TipoEvento.ESTADO)]
	assert estados[:2] == [Estado.TRABAJANDO, Estado.INACTIVA]
	argv = arranques(entorno["registro"])[0]
	assert argv[:4] == ["--input-format", "stream-json", "--output-format", "stream-json"]
	assert "--conversation" not in argv and "--model" not in argv


def test_retomar_una_conversacion_de_la_cli(conversar, entorno):
	conversacion, eventos = conversar(id_externo="conv-de-la-cli")
	conversacion.enviar("Seguimos")
	assert eventos.esperar_fines(1)
	argv = arranques(entorno["registro"])[0]
	assert argv[argv.index("--conversation") + 1] == "conv-de-la-cli"
	assert conversacion.id_externo == "conv-de-la-cli"
	assert not [e for e in eventos.de_tipo(TipoEvento.AVISO) if "no ha encontrado" in e.texto]


def test_conversacion_que_agy_no_encuentra_avisa(conversar, monkeypatch):
	monkeypatch.setenv("FAKE_AGY_PERDIDA", "1")
	conversacion, eventos = conversar(id_externo="conv-borrada")
	conversacion.enviar("Seguimos")
	assert eventos.esperar_fines(1)
	assert conversacion.id_externo == "conv-fake-1"
	assert any("no ha encontrado la conversación" in e.texto for e in eventos.de_tipo(TipoEvento.AVISO))


def test_herramienta_con_detalle_y_salida_recortada(conversar):
	conversacion, eventos = conversar()
	conversacion.enviar("[herramienta] revisa")
	assert eventos.esperar_fines(1)
	inicio = eventos.de_tipo(TipoEvento.HERRAMIENTA)
	assert len(inicio) == 1
	assert inicio[0].datos["herramienta"] == "run_command"
	assert "git status" in inicio[0].texto and "orden" in inicio[0].texto
	fin = eventos.de_tipo(TipoEvento.HERRAMIENTA_FIN)
	assert len(fin) == 1 and fin[0].datos["id"] == inicio[0].datos["id"]
	assert fin[0].datos["salida"].startswith("On branch main")
	assert len(fin[0].datos["salida"]) < 4100 and fin[0].datos["error"] == ""


def test_acciones_denegadas_avisan_y_permiten_reintentar(conversar, entorno):
	conversacion, eventos = conversar()
	conversacion.enviar("[denegado] pasa las pruebas")
	assert eventos.esperar_fines(1)
	avisos = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("denegada")]
	assert len(avisos) == 2
	assert "npm test" in avisos[0].texto and "necesita permiso" in avisos[0].texto
	assert avisos[0].datos["regla"] == "command(npm)"
	assert avisos[1].datos["regla"] == "write_file(config/secreto.json)"
	assert avisos[0].datos["reintento"] == "[denegado] pasa las pruebas"
	# La línea equivalente del error estándar no duplica el aviso.
	time.sleep(0.6)
	assert len([e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("denegada")]) == 2
	# «Permitir y reintentar»: añade la regla y relanza agy en la misma conversación.
	conversacion.responder(avisos[0].datos["solicitud_id"], RespuestaPermiso("permitir"))
	assert eventos.esperar_fines(2)
	assert leer_reglas(entorno["ajustes"]) == ["command(npm)"]
	lanzados = arranques(entorno["registro"])
	assert len(lanzados) == 2
	assert lanzados[1][lanzados[1].index("--conversation") + 1] == "conv-fake-1"
	assert "command(npm)" in eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto


def test_denegacion_solo_en_error_estandar(conversar):
	conversacion, eventos = conversar()
	conversacion.enviar("[stderr_denegado] limpia")
	assert eventos.esperar_fines(1)
	assert eventos.esperar(lambda: any(e.datos.get("denegada") for e in eventos.de_tipo(TipoEvento.AVISO)), 5)
	aviso = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("denegada")][0]
	assert aviso.datos["regla"] == "command(rm)"
	assert "rm build" in aviso.texto


def test_resultado_con_error(conversar):
	conversacion, eventos = conversar()
	conversacion.enviar("[error] haz algo")
	assert eventos.esperar_fines(1)
	fin = eventos.de_tipo(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["correcto"] is False and fin.datos["estado_agy"] == "ERROR"
	errores = eventos.de_tipo(TipoEvento.ERROR)
	assert errores and "no pudo completar" in errores[0].texto
	assert eventos.esperar(lambda: conversacion.estado is Estado.INACTIVA)


def test_respuesta_sin_trozos_usa_result(conversar):
	conversacion, eventos = conversar()
	conversacion.enviar("[sin_deltas]")
	assert eventos.esperar_fines(1)
	assert [m.texto for m in eventos.de_tipo(TipoEvento.MENSAJE)] == ["Respuesta completa sin trozos."]


def test_varios_mensajes_en_la_misma_sesion_y_cola(conversar, entorno):
	conversacion, eventos = conversar()
	conversacion.enviar("uno")
	conversacion.enviar("dos")
	conversacion.enviar("tres")
	assert conversacion.pendientes()[:1] == ["dos"] or eventos.de_tipo(TipoEvento.FIN_TURNO)
	assert eventos.esperar_fines(3)
	assert [m.texto for m in eventos.de_tipo(TipoEvento.MENSAJE)] == ["Has dicho: uno.", "Has dicho: dos.", "Has dicho: tres."]
	assert len(arranques(entorno["registro"])) == 1
	assert len(eventos.de_tipo(TipoEvento.INICIADO)) == 1
	assert conversacion.pendientes() == []


def test_banderas_de_modelo_esfuerzo_y_modo(conversar, entorno):
	conversacion, eventos = conversar(modelo="pro", esfuerzo="high", modo_permiso="sandbox")
	conversacion.enviar("hola")
	assert eventos.esperar_fines(1)
	argv = eventos.de_tipo(TipoEvento.INICIADO)[0].datos
	lanzado = arranques(entorno["registro"])[0]
	assert lanzado[lanzado.index("--model") + 1] == "pro"
	assert lanzado[lanzado.index("--effort") + 1] == "high"
	assert "--sandbox" in lanzado and "--dangerously-skip-permissions" not in lanzado
	assert argv["modelo"] == "pro" and argv["modo"] == "sandbox"


def test_construir_orden_sin_preguntas_y_esfuerzo_invalido(tmp_path):
	conversacion = ConversacionAntigravity(
		AjustesSesion(carpeta=tmp_path, modo_permiso="sin_preguntas", esfuerzo="extremo", id_externo="abc"),
		lambda evento: None,
		["agy.exe"],
	)
	assert conversacion.construir_orden() == [
		"agy.exe", "--input-format", "stream-json", "--output-format", "stream-json",
		"--conversation", "abc", "--dangerously-skip-permissions",
	]
	revisar = ConversacionAntigravity(AjustesSesion(carpeta=tmp_path, modo_permiso="revisar"), lambda e: None, ["agy.exe"])
	assert revisar.construir_orden()[-1] == "stream-json"


def test_cambio_de_modelo_relanza_con_la_conversacion(conversar, entorno):
	conversacion, eventos = conversar()
	conversacion.enviar("primero")
	assert eventos.esperar_fines(1)
	conversacion.cambiar_ajustes(modelo="flash", esfuerzo="low")
	conversacion.enviar("segundo")
	assert eventos.esperar_fines(2)
	lanzados = arranques(entorno["registro"])
	assert len(lanzados) == 2
	segundo = lanzados[1]
	assert segundo[segundo.index("--conversation") + 1] == "conv-fake-1"
	assert segundo[segundo.index("--model") + 1] == "flash"
	assert segundo[segundo.index("--effort") + 1] == "low"
	assert len(eventos.de_tipo(TipoEvento.INICIADO)) == 1
	assert eventos.de_tipo(TipoEvento.FIN_TURNO)[1].datos["correcto"] is True


def test_detener_y_seguir(conversar, entorno):
	conversacion, eventos = conversar()
	conversacion.enviar("[lento] tarda")
	assert eventos.esperar(lambda: eventos.de_tipo(TipoEvento.TEXTO_PARCIAL))
	conversacion.detener()
	fin = eventos.de_tipo(TipoEvento.FIN_TURNO)
	assert len(fin) == 1 and fin[0].datos["interrumpido"] is True
	assert conversacion.estado is Estado.INACTIVA
	conversacion.enviar("otra")
	assert eventos.esperar_fines(2)
	assert eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto == "Has dicho: otra."
	lanzados = arranques(entorno["registro"])
	assert len(lanzados) == 2 and "--conversation" in lanzados[1]


def test_proceso_que_muere_a_mitad_de_turno(conversar):
	conversacion, eventos = conversar()
	conversacion.enviar("[muere]")
	assert eventos.esperar_fines(1)
	assert eventos.de_tipo(TipoEvento.FIN_TURNO)[0].datos["correcto"] is False
	errores = eventos.de_tipo(TipoEvento.ERROR)
	assert errores and "código 3" in errores[0].texto and "fallo grave" in errores[0].texto
	assert conversacion.estado is Estado.ERROR


def test_adjuntos_van_como_rutas(conversar, tmp_path):
	conversacion, eventos = conversar()
	imagen = tmp_path / "captura.png"
	conversacion.enviar("Mira esto", [Adjunto(imagen, "image/png")])
	assert eventos.esperar_fines(1)
	assert str(imagen) in eventos.de_tipo(TipoEvento.MENSAJE)[0].texto


def test_sin_agy_avisa_al_enviar(tmp_path):
	eventos = Recolector()
	conversacion = ConversacionAntigravity(AjustesSesion(carpeta=tmp_path), eventos, None)
	conversacion.enviar("hola")
	assert eventos.esperar_fines(1, 5)
	assert "Agentes instalados" in eventos.de_tipo(TipoEvento.ERROR)[0].texto
	conversacion.cerrar()
	conversacion.cerrar()
	assert conversacion.estado is Estado.CERRADA


# --------------------------------------------------------------------------
# Proveedor
# --------------------------------------------------------------------------


def test_detectar_con_ejecutable_inyectado():
	estado = ProveedorAntigravity(ejecutable=[sys.executable, str(FAKE)]).detectar()
	assert estado.disponible is True
	assert estado.version == "agy 1.2.3 (falso)"


def test_detectar_sin_agy(monkeypatch, tmp_path):
	monkeypatch.setattr(antigravity, "buscar_agy", lambda: None)
	programas = tmp_path / "Programs" / "Antigravity IDE"
	(programas / "resources" / "app" / "out").mkdir(parents=True)
	(programas / "Antigravity IDE.exe").write_bytes(b"MZ")
	(programas / "resources" / "app" / "out" / "cli.js").write_text("", encoding="utf-8")
	monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
	estado = ProveedorAntigravity().detectar()
	assert estado.disponible is False
	assert "Agentes instalados" in estado.mensaje
	assert "IDE de Antigravity" in estado.mensaje


def test_listas_y_capacidades():
	proveedor = ProveedorAntigravity()
	assert proveedor.id == "antigravity"
	assert [m.id for m in proveedor.modelos()] == ["", "flash", "pro", "flash_lite"]
	assert proveedor.modelos()[1].etiqueta == "Gemini Flash (rápido)"
	assert [(e.id, e.etiqueta) for e in proveedor.esfuerzos()][1:] == [("low", "Bajo"), ("medium", "Medio"), ("high", "Alto")]
	assert [m.id for m in proveedor.modos_permiso()] == ["revisar", "reglas", "sandbox", "sin_preguntas"]
	assert proveedor.capacidades.adjuntar_imagenes is False
	assert proveedor.capacidades.permisos_interactivos is False
	assert proveedor.orden_instalacion() == instalar_cli_orden()


def test_listar_modelos_de_agy():
	modelos = ProveedorAntigravity(ejecutable=[sys.executable, str(FAKE)]).listar_modelos()
	ids = [m.id for m in modelos]
	# Con la lista de agy, solo sus modelos: agy 1.4.4 ya no acepta los genéricos que no anuncia.
	assert ids == ["", "flash", "pro", "gemini-3-ultra"]


def test_listar_modelos_sin_agy_da_los_genericos():
	modelos = ProveedorAntigravity(ejecutable=[sys.executable, "-c", "import sys; sys.exit(1)"]).listar_modelos()
	assert [m.id for m in modelos] == ["", "flash", "pro", "flash_lite"]


def test_modelo_que_agy_ya_no_conoce_pasa_al_predeterminado(conversar, entorno):
	conversacion, eventos = conversar(modelo="flash")
	conversacion.enviar("[modelo_desconocido] hola")
	assert eventos.esperar_fines(1)
	aviso = next(e for e in eventos.de_tipo(TipoEvento.AVISO) if "modelo_sustituido" in e.datos)
	assert aviso.datos["modelo_sustituido"] == "" and "«flash»" in aviso.texto
	assert conversacion.ajustes.modelo == ""
	conversacion.enviar("otra vez")
	assert eventos.esperar_fines(2)
	assert "--model" not in arranques(entorno["registro"])[-1]


def test_orden_de_instalacion():
	assert instalar_cli_orden() == [
		"powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
		"irm https://antigravity.google/cli/install.ps1 | iex; Read-Host 'Pulsa Intro para cerrar'",
	]


def test_describir_denegaciones():
	assert describir_denegacion("command(git push)") == ("ejecutar la orden «git push»", "command(git)")
	assert describir_denegacion({"tool": "write_file", "parameters": {"path": "a/b.txt"}})[1] == "write_file(a/b.txt)"
	assert describir_denegacion("algo raro") == ("algo raro", "")


# --------------------------------------------------------------------------
# Reglas
# --------------------------------------------------------------------------


def test_reglas_conservan_el_resto(tmp_path):
	ruta = tmp_path / "antigravity-cli" / "settings.json"
	assert leer_reglas(ruta) == []
	assert anadir_regla("command(git)", ruta) is True
	assert anadir_regla("command(git)", ruta) is False
	datos = json.loads(ruta.read_text(encoding="utf-8"))
	datos["tema"] = "oscuro"
	datos["permissions"]["deny"] = ["command(rm)"]
	ruta.write_text(json.dumps(datos), encoding="utf-8")
	assert anadir_regla("command(regex:npm run (build|test))", ruta) is True
	assert leer_reglas(ruta) == ["command(git)", "command(regex:npm run (build|test))"]
	assert quitar_regla("command(git)", ruta) is True
	assert quitar_regla("command(git)", ruta) is False
	final = json.loads(ruta.read_text(encoding="utf-8"))
	assert final["tema"] == "oscuro" and final["permissions"]["deny"] == ["command(rm)"]
	assert final["permissions"]["allow"] == ["command(regex:npm run (build|test))"]
	assert [p.name for p in ruta.parent.iterdir()] == ["settings.json"]


@pytest.mark.parametrize("contenido", ["{no es json", "[1, 2]", '{"permissions": []}', '{"permissions": {"allow": "x"}}'])
def test_reglas_no_tocan_un_archivo_ilegible(tmp_path, contenido):
	ruta = tmp_path / "settings.json"
	ruta.write_text(contenido, encoding="utf-8")
	with pytest.raises(ErrorAjustesAntigravity):
		anadir_regla("command(git)", ruta)
	with pytest.raises(ErrorAjustesAntigravity):
		quitar_regla("command(git)", ruta)
	assert ruta.read_text(encoding="utf-8") == contenido


def test_reglas_ruta_por_defecto_en_la_carpeta_personal(tmp_path, monkeypatch):
	# El conftest aísla la ruta de los ajustes de agy; aquí se prueba la de verdad.
	monkeypatch.setattr(antigravity, "ruta_ajustes_cli", _RUTA_AJUSTES_CLI_REAL)
	monkeypatch.setenv("USERPROFILE", str(tmp_path))
	monkeypatch.setenv("HOME", str(tmp_path))
	proveedor = ProveedorAntigravity()
	proveedor.anadir_regla("write_file(src/)")
	assert proveedor.leer_reglas() == ["write_file(src/)"]
	assert (tmp_path / ".gemini" / "antigravity-cli" / "settings.json").is_file()
	with pytest.raises(ErrorAjustesAntigravity):
		proveedor.anadir_regla("  ")


# --------------------------------------------------------------------------
# IDE, app y conversaciones de escritorio
# --------------------------------------------------------------------------


def _instalar_falsos(raiz: Path, ide: bool = True, app: bool = True) -> None:
	programas = raiz / "Programs"
	if ide:
		carpeta = programas / "Antigravity IDE"
		(carpeta / "resources" / "app" / "out").mkdir(parents=True)
		(carpeta / "Antigravity IDE.exe").write_bytes(b"MZ")
		(carpeta / "resources" / "app" / "out" / "cli.js").write_text("", encoding="utf-8")
	if app:
		carpeta = programas / "antigravity"
		(carpeta / "resources" / "bin").mkdir(parents=True)
		(carpeta / "Antigravity.exe").write_bytes(b"MZ")


def test_orden_para_abrir_el_ide(tmp_path, monkeypatch):
	_instalar_falsos(tmp_path)
	monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
	monkeypatch.setenv("VSCODE_DEV", "1")
	proyecto = tmp_path / "mi proyecto & más"
	proyecto.mkdir()
	exe = buscar_antigravity_ide()
	assert exe is not None and exe.name == "Antigravity IDE.exe"
	assert buscar_antigravity_app().name == "Antigravity.exe"
	orden, entorno = orden_abrir_carpeta(proyecto)
	assert orden == [str(exe), str(exe.parent / "resources" / "app" / "out" / "cli.js"), "--reuse-window", str(proyecto)]
	assert entorno["ELECTRON_RUN_AS_NODE"] == "1" and "VSCODE_DEV" not in entorno
	assert orden_abrir_carpeta(proyecto, nueva_ventana=True)[0][2] == "--new-window"
	assert orden_abrir_carpeta(proyecto, preferir="app") == ([str(buscar_antigravity_app()), str(proyecto)], None)

	lanzadas = []
	monkeypatch.setattr(antigravity, "_lanzar", lambda orden, carpeta, entorno: lanzadas.append((orden, carpeta, entorno)) or True)
	assert abrir_carpeta_en_antigravity(proyecto) is True
	assert lanzadas[0][0][0] == str(exe) and lanzadas[0][1] == proyecto
	assert lanzadas[0][2]["ELECTRON_RUN_AS_NODE"] == "1"


def test_ide_antiguo_y_preferencias(tmp_path, monkeypatch):
	monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
	assert buscar_antigravity_ide() is None and buscar_antigravity_app() is None
	assert orden_abrir_carpeta(tmp_path) is None
	assert abrir_carpeta_en_antigravity(tmp_path) is False
	# Solo la app: el IDE no se confunde con ella y se usa la app como alternativa.
	_instalar_falsos(tmp_path, ide=False, app=True)
	assert buscar_antigravity_ide() is None
	assert orden_abrir_carpeta(tmp_path, preferir="ide")[0][0].endswith("Antigravity.exe")
	# IDE antiguo en Programs\Antigravity (con cli.js): es IDE, no app.
	(tmp_path / "Programs" / "antigravity" / "resources" / "app" / "out").mkdir(parents=True)
	(tmp_path / "Programs" / "antigravity" / "resources" / "app" / "out" / "cli.js").write_text("", encoding="utf-8")
	assert buscar_antigravity_ide() is not None and buscar_antigravity_app() is None


def test_listar_conversaciones_escritorio(tmp_path):
	raiz = tmp_path / "antigravity"
	conversaciones = raiz / "conversations"
	conversaciones.mkdir(parents=True)
	ids = [
		"11111111-1111-1111-1111-111111111111",
		"22222222-2222-2222-2222-222222222222",
		"33333333-3333-3333-3333-333333333333",
		"44444444-4444-4444-4444-444444444444",
	]
	(conversaciones / f"{ids[0]}.pb").write_bytes(b"\x00\x01cifrado")
	(conversaciones / f"{ids[1]}.db").write_bytes(b"SQLite")
	(conversaciones / f"{ids[1]}.db-wal").write_bytes(b"")
	(conversaciones / f"{ids[2]}.pb").write_bytes(b"\x00")
	(conversaciones / "basura.txt").write_text("x", encoding="utf-8")
	brain = raiz / "brain"
	(brain / ids[0]).mkdir(parents=True)
	(brain / ids[0] / "implementation_plan.md").write_text("\n# Implementation Plan - Arreglar el lector\n\ntexto", encoding="utf-8")
	(brain / ids[0] / "task.md").write_text("- [x] otra cosa\n", encoding="utf-8")
	(brain / ids[1]).mkdir()
	(brain / ids[1] / "task.md").write_text("- [ ] Revisar `menu.py`\n", encoding="utf-8")
	(brain / ids[3] / "artifacts").mkdir(parents=True)
	(brain / ids[3] / "artifacts" / "walkthrough.md").write_text("# Walkthrough — Voz nueva\n", encoding="utf-8")
	for posicion, identificador in enumerate(ids):
		marca = 1_700_000_000 + posicion * 100
		for ruta in [*conversaciones.glob(identificador + "*"), brain / identificador]:
			if ruta.exists():
				os.utime(ruta, (marca, marca))

	lista = listar_conversaciones_escritorio(raiz)
	assert [c["id"] for c in lista] == list(reversed(ids))
	titulos = {c["id"]: c["titulo"] for c in lista}
	assert titulos[ids[0]] == "Arreglar el lector"
	assert titulos[ids[1]] == "Revisar menu.py"
	assert titulos[ids[2]].startswith("Conversación de Antigravity del ")
	assert titulos[ids[3]] == "Voz nueva"
	assert lista[-1]["ruta_brain"] == str(brain / ids[0])
	assert listar_conversaciones_escritorio(tmp_path / "no-existe") == []
