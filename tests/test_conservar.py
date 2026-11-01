"""Chats conservados: copias de las conversaciones de Claude Code y qué pasa cuando el original ya no está."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services import conservar
from app.services.agentes.base import AjustesSesion, Evento, TipoEvento
from app.services.agentes.claude import ProveedorClaude
from app.services.almacen import Almacen, SesionPropia
from app.services.conservar import Conservador
from app.services.gestor import ID_AVISO_CONSERVADA, TEXTO_CONSERVADA, FilaSesion, GestorSesiones
from app.services.historial import claude as hc

FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"


def _linea(**datos) -> str:
	return json.dumps(datos, ensure_ascii=False) + "\n"


def _transcripcion(texto: str = "Hola, agente", carpeta: str = "C:\\trabajo") -> str:
	return (_linea(type="user", entrypoint="cli", cwd=carpeta, timestamp="2026-10-01T10:00:00Z", uuid="u1",
			message={"role": "user", "content": texto})
		+ _linea(type="assistant", timestamp="2026-10-01T10:00:05Z", uuid="a1",
			message={"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "Hola, persona"}]}))


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
	return tmp_path / "conservadas"


@pytest.fixture
def origen(tmp_path: Path) -> Path:
	ruta = tmp_path / "original.jsonl"
	ruta.write_text("línea 1\n", encoding="utf-8")
	return ruta


@pytest.fixture
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
	"""Una carpeta de Claude Code de mentira."""
	carpeta = tmp_path / ".claude"
	(carpeta / "projects" / "C--trabajo").mkdir(parents=True)
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(carpeta))
	monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
	monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
	monkeypatch.setattr(hc, "_proceso_vivo", lambda pid: False)
	hc._cache_datos.clear()
	yield carpeta
	conservar.cerrar_todos()


def _crear(claude: Path, identificador: str = "abc-1", texto: str = "Hola, agente") -> Path:
	ruta = claude / "projects" / "C--trabajo" / f"{identificador}.jsonl"
	ruta.write_text(_transcripcion(texto), encoding="utf-8")
	return ruta


class Proveedor:
	id = "claude"
	nombre = "Claude Code"


def _gestor(tmp_path: Path, opciones: dict | None = None) -> GestorSesiones:
	valores = {"historial.claude": True, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False, **(opciones or {})}
	return GestorSesiones(Almacen(tmp_path / "a.db"), {"claude": Proveedor()}, lambda c, e: None, lambda clave, defecto: valores.get(clave, defecto))


# ── Conservador ───────────────────────────────────────────────────────────────

def test_copia_y_recuerda_tras_reiniciar(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	assert almacen.capturar("id-1", origen, {"titulo": "Uno"})
	copia = almacen.ruta_copia("id-1")
	assert copia.read_text(encoding="utf-8") == "línea 1\n"
	assert almacen.registros() == {"id-1": {"titulo": "Uno"}}
	otro = Conservador(raiz)
	assert otro.registros() == {"id-1": {"titulo": "Uno"}} and otro.ruta_copia("id-1") == copia


def test_solo_anade_lo_nuevo_si_el_original_crece(raiz: Path, origen: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	almacen = Conservador(raiz)
	almacen.capturar("id-1", origen, {})
	with open(origen, "a", encoding="utf-8") as archivo:
		archivo.write("línea 2\n")

	def sin_copia_entera(*_a, **_k):
		raise AssertionError("no debe copiarse entero")

	monkeypatch.setattr(conservar.tempfile, "mkstemp", sin_copia_entera)
	assert almacen.capturar("id-1", origen, {})
	assert almacen.ruta_copia("id-1").read_text(encoding="utf-8") == "línea 1\nlínea 2\n"


def test_si_el_original_se_reescribe_lo_anterior_se_guarda_aparte(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	almacen.capturar("id-1", origen, {})
	origen.write_text("otra cosa\n", encoding="utf-8")
	assert almacen.capturar("id-1", origen, {})
	copia = almacen.ruta_copia("id-1")
	assert copia.read_text(encoding="utf-8") == "otra cosa\n"
	versiones = list((copia.parent / "versiones").iterdir())
	assert len(versiones) == 1 and versiones[0].read_text(encoding="utf-8") == "línea 1\n"


def test_sin_cambios_no_se_copia_otra_vez_pero_se_guardan_los_datos_nuevos(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	almacen.capturar("id-1", origen, {"titulo": "Uno"})
	assert not almacen.capturar("id-1", origen, {"titulo": "Uno"})
	assert not almacen.capturar("id-1", origen, {"titulo": "Otro"})
	assert Conservador(raiz).registros()["id-1"]["titulo"] == "Otro"


def test_una_copia_fallida_no_estropea_la_anterior(raiz: Path, origen: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	almacen = Conservador(raiz)
	almacen.capturar("id-1", origen, {})
	origen.write_text("distinto\n", encoding="utf-8")
	monkeypatch.setattr(conservar, "_es_principio", lambda *_a: (_ for _ in ()).throw(OSError("disco lleno")))
	with pytest.raises(OSError):
		almacen.capturar("id-1", origen, {})
	assert almacen.ruta_copia("id-1").read_text(encoding="utf-8") == "línea 1\n"
	assert not list((raiz / "id-1").glob("copia-*.tmp"))


def test_el_original_desaparecido_antes_de_la_primera_copia_se_anota(raiz: Path, tmp_path: Path) -> None:
	almacen = Conservador(raiz)
	assert not almacen.capturar("id-1", tmp_path / "no-existe.jsonl", {})
	assert almacen.problemas["id-1"] and almacen.ruta_copia("id-1") is None


def test_el_hilo_copia_y_acaba_sin_quedarse_esperando(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	almacen.anotar("id-1", origen, {"a": 1})
	almacen.anotar("id-2", origen, {"a": 2})
	assert almacen.esperar_inactivo(10)
	assert set(almacen.registros()) == {"id-1", "id-2"}
	for _intento in range(200):
		if almacen._hilo is None:
			break
		time.sleep(0.01)
	assert almacen._hilo is None
	almacen.anotar("id-3", origen, {})
	assert almacen.esperar_inactivo(10) and "id-3" in almacen.registros()


def test_un_id_raro_no_puede_elegir_otro_archivo(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	for malo in ("..", "../fuera", "a/b", "a\\b", "", "C:"):
		almacen.anotar(malo, origen, {})
		assert almacen.ruta_copia(malo) is None
	assert almacen.esperar_inactivo(5) and not raiz.exists()


def test_olvidar_borra_y_no_resucita(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	almacen.capturar("id-1", origen, {})
	almacen.olvidar("id-1")
	assert almacen.ruta_copia("id-1") is None and not (raiz / "id-1").exists()
	almacen.anotar("id-1", origen, {})
	assert almacen.esperar_inactivo(5) and "id-1" not in almacen.registros()


def test_cerrar_abandona_lo_pendiente_y_lo_dejado_a_medias_se_limpia(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	almacen.capturar("id-1", origen, {})
	(raiz / "id-1" / "copia-resto.tmp").write_text("a medias", encoding="utf-8")
	almacen.cerrar()
	almacen.anotar("id-2", origen, {})
	assert "id-2" not in almacen.registros()
	assert not list(Conservador(raiz).raiz.glob("*/copia-*.tmp"))


def test_un_registro_roto_no_impide_leer_los_demas(raiz: Path, origen: Path) -> None:
	Conservador(raiz).capturar("id-1", origen, {})
	(raiz / "roto").mkdir()
	(raiz / "roto" / "registro.json").write_text("no es json", encoding="utf-8")
	(raiz / "ajeno").mkdir()
	(raiz / "ajeno" / "registro.json").write_text(json.dumps({"id": "otro", "metadatos": {}}), encoding="utf-8")
	almacen = Conservador(raiz)
	assert set(almacen.registros()) == {"id-1"} and len(almacen.problemas) == 2


def test_resumen_cuenta_conversaciones_y_tamano(raiz: Path, origen: Path) -> None:
	almacen = Conservador(raiz)
	almacen.capturar("id-1", origen, {})
	assert almacen.resumen() == (1, origen.stat().st_size)


def test_tamano_legible() -> None:
	assert conservar.tamano_legible(12) == "12 bytes"
	assert conservar.tamano_legible(1536) == "1,5 KB"
	assert conservar.tamano_legible(3 * 1024 ** 3) == "3,0 GB"


# ── Historial de Claude Code ──────────────────────────────────────────────────

def test_la_lista_encarga_copias_y_no_toca_la_carpeta_de_claude(claude: Path) -> None:
	ruta = _crear(claude)
	antes = ruta.read_bytes(), ruta.stat().st_mtime_ns
	sesiones = hc.con_conservadas(hc.listar_sesiones())
	assert sesiones[0].ruta_transcript == ruta and not sesiones[0].conservada
	almacen = conservar.conservador()
	assert almacen.esperar_inactivo(10)
	assert almacen.ruta_copia("abc-1").read_bytes() == antes[0]
	assert (ruta.read_bytes(), ruta.stat().st_mtime_ns) == antes
	assert sorted(p.name for p in claude.rglob("*") if p.is_file()) == ["abc-1.jsonl"]


def test_una_sesion_al_dia_no_se_vuelve_a_encargar(claude: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_crear(claude)
	hc.con_conservadas(hc.listar_sesiones())
	almacen = conservar.conservador()
	assert almacen.esperar_inactivo(10)
	monkeypatch.setattr(almacen, "anotar", lambda *_a: pytest.fail("no debe encargar nada"))
	hc.con_conservadas(hc.listar_sesiones())


def test_sin_la_opcion_no_se_copia_pero_lo_conservado_sigue(claude: Path) -> None:
	ruta = _crear(claude)
	hc.con_conservadas(hc.listar_sesiones(), copiar=False)
	assert not conservar.conservador().registros()
	hc.con_conservadas(hc.listar_sesiones())
	assert conservar.conservador().esperar_inactivo(10)
	ruta.unlink()
	sesiones = hc.con_conservadas(hc.listar_sesiones(), copiar=False)
	assert [s.id for s in sesiones] == ["abc-1"] and sesiones[0].conservada


def test_si_falta_el_original_se_lee_la_copia_marcada(claude: Path) -> None:
	ruta = _crear(claude, texto="Conversación que se borrará")
	hc.con_conservadas(hc.listar_sesiones())
	assert conservar.conservador().esperar_inactivo(10)
	ruta.unlink()
	sesion = hc.con_conservadas(hc.listar_sesiones())[0]
	assert sesion.conservada and sesion.ruta_transcript == conservar.conservador().ruta_copia("abc-1")
	assert sesion.titulo == "Conversación que se borrará" and sesion.carpeta == "C:\\trabajo" and sesion.origen == "terminal"
	assert [m.texto for m in hc.leer_mensajes(sesion.ruta_transcript)] == ["Conversación que se borrará", "Hola, persona"]


def test_una_sesion_de_la_app_sin_transcripcion_recupera_la_copia(claude: Path, tmp_path: Path) -> None:
	ruta = _crear(claude)
	carpeta = tmp_path / "roaming" / "Claude" / "claude-code-sessions" / "x"
	carpeta.mkdir(parents=True)
	(carpeta / "local_1.json").write_text(json.dumps({"sessionId": "local_1", "cliSessionId": "abc-1", "title": "De la app",
		"cwd": "C:\\trabajo"}), encoding="utf-8")
	hc.con_conservadas(hc.listar_sesiones())
	assert conservar.conservador().esperar_inactivo(10)
	ruta.unlink()
	sesion = next(s for s in hc.con_conservadas(hc.listar_sesiones()) if s.id == "abc-1")
	assert sesion.conservada and sesion.titulo == "De la app" and sesion.id_escritorio == "local_1"


def test_las_sesiones_de_ctrlia_no_se_conservan_aparte(claude: Path) -> None:
	_crear(claude)
	hc.con_conservadas(hc.listar_sesiones({"abc-1"}), {"abc-1"})
	assert not conservar.conservador().registros()


def test_una_copia_conservada_de_una_sesion_ahora_propia_no_sale_repetida(claude: Path) -> None:
	ruta = _crear(claude)
	hc.con_conservadas(hc.listar_sesiones())
	assert conservar.conservador().esperar_inactivo(10)
	ruta.unlink()
	assert hc.con_conservadas(hc.listar_sesiones({"abc-1"}), {"abc-1"}) == []


def test_un_fallo_al_conservar_no_vacia_la_lista(claude: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_crear(claude)
	sesiones = hc.listar_sesiones()
	monkeypatch.setattr(conservar, "conservador", lambda: (_ for _ in ()).throw(RuntimeError("roto")))
	assert hc.con_conservadas(sesiones) == sesiones


# ── Gestor ────────────────────────────────────────────────────────────────────

def _conservada(tmp_path: Path, claude: Path, opciones: dict | None = None) -> GestorSesiones:
	"""Un gestor con una sesión de la terminal que Claude Code ya ha borrado."""
	ruta = _crear(claude, texto="La tarea original")
	gestor = _gestor(tmp_path, opciones)
	gestor._listar_externas()
	assert conservar.conservador().esperar_inactivo(10)
	ruta.unlink()
	gestor._listar_externas()
	return gestor


def test_la_lista_dice_que_es_una_copia(tmp_path: Path, claude: Path) -> None:
	gestor = _conservada(tmp_path, claude)
	fila = gestor._listar_externas()[0]
	assert fila.conservada and not fila.sin_conversacion and fila.nombre_origen == "Terminal, copia conservada"
	gestor.almacen.cerrar()


def test_abrirla_avisa_y_se_lee_entera(tmp_path: Path, claude: Path) -> None:
	gestor = _conservada(tmp_path, claude)
	mensajes = gestor.mensajes("claude:abc-1")
	assert mensajes[0].texto == TEXTO_CONSERVADA and mensajes[0].id == ID_AVISO_CONSERVADA
	assert [m.texto for m in mensajes[1:]] == ["La tarea original", "Hola, persona"]
	gestor.almacen.cerrar()


def test_se_encuentra_al_buscar_en_todas_las_sesiones(tmp_path: Path, claude: Path) -> None:
	gestor = _conservada(tmp_path, claude)
	assert [r[0] for r in gestor.buscar("tarea original")] == ["claude:abc-1"]
	gestor.almacen.cerrar()


def test_abrirla_antes_de_listar_tambien_encuentra_la_copia(tmp_path: Path, claude: Path) -> None:
	gestor = _conservada(tmp_path, claude)
	gestor._externas.clear()
	assert gestor.mensajes("claude:abc-1")[0].id == ID_AVISO_CONSERVADA
	gestor.almacen.cerrar()


def test_no_se_puede_seguir_la_original_ni_bifurcar(tmp_path: Path, claude: Path) -> None:
	gestor = _conservada(tmp_path, claude)
	assert not gestor.puede_seguir_original("claude:abc-1")
	with pytest.raises(ValueError, match="Ctrl\\+IA crea una sesión nueva"):
		gestor.continuar_aqui("claude:abc-1")
	gestor.almacen.cerrar()


def test_continuarla_le_cuenta_la_conversacion_al_agente(tmp_path: Path, claude: Path) -> None:
	gestor = _conservada(tmp_path, claude)
	gestor.almacen.guardar_sesion(SesionPropia(clave="ctrlia:nueva", proveedor="claude", creada=1.0, actividad=1.0))
	contexto = gestor.preparar_desde_conservada("ctrlia:nueva", "claude:abc-1", gestor.mensajes("claude:abc-1"))
	assert not contexto.recortada and gestor.contexto_pendiente("ctrlia:nueva")
	assert "La tarea original" in contexto.texto and TEXTO_CONSERVADA not in contexto.texto
	assert "Claude Code ya borró" in contexto.texto and "otro equipo" not in contexto.texto
	textos = [m.texto for m in gestor.almacen.mensajes("ctrlia:nueva")]
	assert textos[0] == "La tarea original" and "continúa" in textos[-1]
	assert gestor.fuente_externa("claude:abc-1").conservada  # la copia no se toca
	gestor.almacen.cerrar()


def test_borrar_la_copia_de_una_conservada(tmp_path: Path, claude: Path) -> None:
	gestor = _conservada(tmp_path, claude)
	gestor.borrar("claude:abc-1")
	assert conservar.conservador().ruta_copia("abc-1") is None
	assert gestor._listar_externas() == []
	gestor.almacen.cerrar()


def test_una_sesion_con_original_no_se_borra(tmp_path: Path, claude: Path) -> None:
	_crear(claude)
	gestor = _gestor(tmp_path)
	gestor._listar_externas()
	with pytest.raises(ValueError):
		gestor.borrar("claude:abc-1")
	gestor.almacen.cerrar()


def test_la_opcion_de_preferencias_para_las_copias(tmp_path: Path, claude: Path) -> None:
	_crear(claude)
	gestor = _gestor(tmp_path, {"historial.conservar": False})
	gestor._listar_externas()
	assert not conservar.conservador().registros()
	gestor.almacen.cerrar()


def test_fila_conservada_en_las_columnas(app_wx) -> None:
	from app.ui.vp_sesiones import valores_sesion

	fila = FilaSesion("claude:x", "claude", "Vieja", origen="terminal", conservada=True)
	valores = valores_sesion(fila, ["estado", "origen"], lambda clave, defecto=None: defecto)
	assert "el original ya no está" in valores[0] and valores[1] == "Terminal, copia conservada"


def test_cerrar_el_gestor_detiene_el_conservador(tmp_path: Path, claude: Path) -> None:
	_crear(claude)
	gestor = _gestor(tmp_path)
	gestor._listar_externas()
	gestor.cerrar_todo()
	assert not any(h.name == "conservar-chats" and h.is_alive() for h in threading.enumerate())
	gestor.almacen.cerrar()


# ── Cuánto guarda cada agente ─────────────────────────────────────────────────

def test_el_texto_lee_los_dias_de_claude_code(claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
	monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
	texto = conservar.describir_retencion()
	assert "30 días" in texto and "Codex" in texto and "Gemini CLI" in texto and "Antigravity" in texto
	assert "conservadas aquí: 0" in texto
	(claude / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 90}), encoding="utf-8")
	assert "guarda las conversaciones 90 días" in conservar.describir_retencion()


def test_la_politica_de_empresa_manda_y_un_ajuste_roto_se_dice(claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
	monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
	(claude / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 90}), encoding="utf-8")
	politica = tmp_path / "pd" / "ClaudeCode" / "managed-settings.json"
	politica.parent.mkdir(parents=True)
	politica.write_text(json.dumps({"cleanupPeriodDays": 7}), encoding="utf-8")
	assert "7 días" in conservar.describir_retencion()
	(claude / "settings.json").write_text("{roto", encoding="utf-8")
	assert "no se ha podido saber" in conservar.describir_retencion()


def test_preferencias_muestra_la_opcion_y_el_texto(app_wx, marco, tmp_path: Path, claude: Path) -> None:
	import wx

	from app.core.configuracion import Configuracion
	from app.ui.dialogo_preferencias import DialogoPreferencias

	configuracion = Configuracion(tmp_path / "ajustes.json")
	assert configuracion.obtener("historial.conservar") is True
	dialogo = DialogoPreferencias(marco, configuracion)
	casilla = dialogo.control_de("historial.conservar")
	assert isinstance(casilla, wx.CheckBox) and casilla.GetValue()
	texto = dialogo.control_de("historial.retencion")
	assert not texto.IsEditable() and "Claude Code" in texto.GetValue() and texto.GetHelpText()
	assert "historial.retencion" not in dialogo.valores()
	casilla.SetValue(False)
	assert dialogo.aplicar() and configuracion.obtener("historial.conservar") is False
	dialogo.restablecer_controles()
	assert "Claude Code" in texto.GetValue()


# ── Sesión propia cuya transcripción ya no está (--resume) ────────────────────

class Recolector:
	def __init__(self) -> None:
		self.eventos: list[Evento] = []
		self.fin = threading.Event()

	def __call__(self, evento: Evento) -> None:
		self.eventos.append(evento)
		if evento.tipo is TipoEvento.FIN_TURNO:
			self.fin.set()


@pytest.mark.parametrize("modo", ["sin_transcripcion", "sin_transcripcion_stderr"])
def test_resume_sin_transcripcion_se_detecta_y_se_explica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modo: str) -> None:
	monkeypatch.setenv("FAKE_CLAUDE_MODO", modo)
	recolector = Recolector()
	proveedor = ProveedorClaude(orden_base=[sys.executable, str(FAKE)])
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, id_externo="3f0e7a10-0000-4000-8000-1234567890ab"), recolector)
	conversacion.enviar("Sigue con lo de ayer")
	assert recolector.fin.wait(20)
	conversacion.cerrar()
	errores = [e for e in recolector.eventos if e.tipo is TipoEvento.ERROR]
	assert len(errores) == 1 and errores[0].datos.get("sin_transcripcion") is True
	assert "ya no tiene la conversación" in errores[0].texto and "No conversation found" not in errores[0].texto
	assert not next(e for e in recolector.eventos if e.tipo is TipoEvento.FIN_TURNO).datos["correcto"]


def _sesion_propia(gestor: GestorSesiones) -> None:
	gestor.almacen.guardar_sesion(SesionPropia(clave="ctrlia:vieja", proveedor="claude", titulo="Vieja", carpeta="C:\\p",
		id_externo="3f0e7a10-0000-4000-8000-1234567890ab", creada=1.0, actividad=1.0))
	gestor.almacen.anadir_mensaje("ctrlia:vieja", "usuario", "Primera pregunta")
	gestor.almacen.anadir_mensaje("ctrlia:vieja", "asistente", "Primera respuesta")
	gestor.almacen.anadir_mensaje("ctrlia:vieja", "usuario", "Mensaje que no llegó")
	gestor.almacen.anadir_mensaje("ctrlia:vieja", "error", "Claude Code ya no tiene la conversación")


def test_seguir_con_contexto_empieza_de_nuevo_y_no_reenvia_nada(tmp_path: Path, claude: Path) -> None:
	gestor = _gestor(tmp_path)
	_sesion_propia(gestor)
	pendiente = gestor.seguir_con_contexto("ctrlia:vieja")
	assert pendiente == "Mensaje que no llegó"
	sesion = gestor.almacen.sesion("ctrlia:vieja")
	assert sesion.id_externo == "" and sesion.bifurcada_de == ""
	contexto = sesion.extra["contexto_importada"]
	assert "Primera pregunta" in contexto and "Primera respuesta" in contexto and "Mensaje que no llegó" not in contexto
	assert "ya borró su conversación" in contexto and "otro equipo" not in contexto
	mensajes = gestor.almacen.mensajes("ctrlia:vieja")
	assert mensajes[-1].rol == "sistema" and "conversación nueva" in mensajes[-1].texto
	gestor.almacen.cerrar()


def test_el_proximo_mensaje_lleva_el_contexto_y_no_usa_resume(tmp_path: Path, claude: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro))
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	eventos: list[str] = []
	fin = threading.Event()

	def al_evento(_clave: str, evento: Evento) -> None:
		eventos.append(evento.texto)
		if evento.tipo is TipoEvento.FIN_TURNO:
			fin.set()

	almacen = Almacen(tmp_path / "a.db")
	gestor = GestorSesiones(almacen, {"claude": ProveedorClaude(orden_base=[sys.executable, str(FAKE)])}, al_evento)
	_sesion_propia(gestor)
	almacen.actualizar_sesion("ctrlia:vieja", carpeta=str(tmp_path))
	gestor.seguir_con_contexto("ctrlia:vieja")
	gestor.enviar("ctrlia:vieja", "Sigamos")
	assert fin.wait(20)
	gestor.cerrar_todo()
	lineas = [json.loads(linea) for linea in registro.read_text(encoding="utf-8").splitlines()]
	argumentos = next(linea["argv"] for linea in lineas if "argv" in linea)
	assert "--resume" not in argumentos and "--session-id" in argumentos
	enviado = next(linea["mensaje"]["message"]["content"] for linea in lineas if "mensaje" in linea)
	assert "Primera pregunta" in enviado and enviado.endswith("Sigamos")
	assert "contexto_importada" not in almacen.sesion("ctrlia:vieja").extra
	assert almacen.sesion("ctrlia:vieja").id_externo  # ya tiene una conversación nueva
	almacen.cerrar()


def test_una_continuacion_sin_mensajes_coge_los_de_la_sesion_de_origen(tmp_path: Path, claude: Path) -> None:
	gestor = _gestor(tmp_path)
	_sesion_propia(gestor)
	gestor.almacen.guardar_sesion(SesionPropia(clave="ctrlia:hija", proveedor="claude", titulo="Hija", carpeta="C:\\p", creada=2.0,
		actividad=2.0, bifurcada_de="3f0e7a10-0000-4000-8000-1234567890ab"))
	gestor.almacen.anadir_mensaje("ctrlia:hija", "usuario", "Sigue por ahí")
	assert gestor.seguir_con_contexto("ctrlia:hija") == "Sigue por ahí"
	assert "Primera respuesta" in gestor.almacen.sesion("ctrlia:hija").extra["contexto_importada"]
	gestor.almacen.cerrar()


def test_la_ventana_ofrece_seguir_y_devuelve_el_mensaje_al_cuadro(app_wx, marco, tmp_path: Path, claude: Path, dialogo_modal, monkeypatch) -> None:
	import wx

	from app.ui import vp_conversacion

	class Ventana(vp_conversacion.MixinConversacion):
		def __init__(self, gestor) -> None:
			self.gestor = gestor
			self.sesion_actual = "ctrlia:vieja"
			self._ofreciendo_contexto = set()
			self.respuesta = wx.TextCtrl(marco)
			self.recargadas = 0

		def recargar_mensajes(self) -> None:
			self.recargadas += 1

	gestor = _gestor(tmp_path)
	_sesion_propia(gestor)
	ventana = Ventana(gestor)
	monkeypatch.setattr(vp_conversacion, "confirmar", lambda *_a, **_k: wx.ID_NO)
	assert not ventana.ofrecer_seguir_con_contexto("ctrlia:vieja")
	assert gestor.almacen.sesion("ctrlia:vieja").id_externo
	monkeypatch.setattr(vp_conversacion, "confirmar", lambda *_a, **_k: wx.ID_YES)
	assert ventana.ofrecer_seguir_con_contexto("ctrlia:vieja")
	assert ventana.respuesta.GetValue() == "Mensaje que no llegó" and ventana.recargadas == 1
	gestor.almacen.cerrar()


def test_el_contexto_explica_otro_motivo_sin_tocar_el_de_las_importadas() -> None:
	from app.services import intercambio

	normal = intercambio.contexto_para_continuar([], "T", "PORTATIL", "claude")
	assert "otro equipo (PORTATIL)" in normal.texto and "exportó a un archivo" in normal.texto
	propio = intercambio.contexto_para_continuar([], "T", "", "claude", motivo="Motivo propio.")
	assert "Motivo propio." in propio.texto and "exportó" not in propio.texto and "«T»" in propio.texto
