"""Pruebas de lo nuevo en la versión 1.4.3.

- Claude Code: varias llamadas en paralelo a la misma herramienta MCP llegaban
  como varias peticiones a la vez, y «Permitir durante esta sesión» en la
  primera no retiraba las demás. Ahora se permiten con la regla recién dada.
- Claude Code: «Permitir siempre en esta carpeta» (lo guarda el propio Claude
  Code en .claude/settings.local.json) y permitir el servidor MCP entero.
- Codex y Antigravity: peticiones de herramientas MCP en español y con su
  nombre; Antigravity saca la regla ``mcp(servidor/herramienta)`` del paso
  denegado y ofrece permitir el servidor entero.
- Portapapeles: Ctrl+V adjunta imágenes «PNG» y archivos copiados en el
  Explorador, y no roba el texto de lo copiado en Excel o Word.

Nada se muestra ni habla, y el portapapeles real no se toca (se simula).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services.agentes import codex
from app.services.agentes.antigravity import (
	OPCION_SERVIDOR_REINTENTAR,
	ProveedorAntigravity,
	describir_denegacion,
	describir_herramienta,
	leer_reglas,
	regla_servidor_mcp,
)
from app.services.agentes.base import AjustesSesion, RespuestaPermiso, TipoEvento
from app.services.agentes.claude import ProveedorClaude, regla_cubre

FALSOS = Path(__file__).parent / "fakes"
HERRAMIENTA = "mcp__claude_ai_Prueba__eco"


class Recolector:
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

	def esperar(self, condicion, segundos: float = 20.0) -> bool:
		limite = time.monotonic() + segundos
		with self._condicion:
			while not condicion():
				restante = limite - time.monotonic()
				if restante <= 0:
					return False
				self._condicion.wait(restante)
		return True


# -- Claude Code: permisos de las herramientas MCP -------------------------------


@pytest.fixture
def claude_falso(tmp_path, monkeypatch):
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro))
	creadas = []

	def crear(modo: str, **ajustes):
		monkeypatch.setenv("FAKE_CLAUDE_MODO", modo)
		recolector = Recolector()
		proveedor = ProveedorClaude(orden_base=[sys.executable, str(FALSOS / "fake_claude.py")])
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, **ajustes), recolector)
		creadas.append(conversacion)
		return conversacion, recolector

	yield crear, registro
	for conversacion in creadas:
		conversacion.cerrar()


def _respuestas(registro: Path) -> list[dict]:
	lineas = [json.loads(linea) for linea in registro.read_text(encoding="utf-8").splitlines()]
	return [linea["respuesta"]["response"] for linea in lineas if "respuesta" in linea]


def test_llamadas_en_paralelo_se_contestan_una_vez(claude_falso):
	crear, registro = claude_falso
	conversacion, eventos = crear("mcp_paralelo")
	conversacion.enviar("usa eco tres veces")
	# Tres peticiones (la que Claude Code entrega dos veces se muestra una sola vez).
	assert eventos.esperar(lambda: len(eventos.de_tipo(TipoEvento.PERMISO)) >= 3)
	time.sleep(0.3)
	permisos = [e.datos["solicitud"] for e in eventos.de_tipo(TipoEvento.PERMISO)]
	assert [s.id for s in permisos] == ["mcp-a", "mcp-b", "mcp-c"]
	primera = permisos[0]
	assert primera.titulo == "Claude quiere usar «Eco» del servidor MCP «claude.ai Prueba»"
	assert [o.id for o in primera.opciones] == ["permitir", "permitir_sesion", "permitir_servidor_sesion",
		"permitir_siempre", "permitir_servidor_siempre", "denegar"]
	assert primera.opcion_segura().id == "denegar"
	conversacion.responder("mcp-a", RespuestaPermiso("permitir_sesion"))
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	assert {e.datos["id"] for e in eventos.de_tipo(TipoEvento.PERMISO_RETIRADO)} >= {"mcp-b", "mcp-c"}
	assert any(e.datos.get("permitidas_por_regla") == 2 for e in eventos.de_tipo(TipoEvento.AVISO))
	respuestas = _respuestas(registro)
	assert [r["request_id"] for r in respuestas] == ["mcp-a", "mcp-b", "mcp-c"]
	assert all(r["response"]["behavior"] == "allow" for r in respuestas)
	assert respuestas[0]["response"]["updatedPermissions"] == [{"type": "addRules", "behavior": "allow",
		"destination": "session", "rules": [{"toolName": HERRAMIENTA}]}]
	assert respuestas[1]["response"] == {"behavior": "allow", "updatedInput": {"texto": "b"}}
	assert conversacion.ajustes.reglas_permitidas == [HERRAMIENTA]
	assert eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto == "respuestas:3"


def test_permitir_el_servidor_siempre_lo_guarda_claude(claude_falso):
	crear, registro = claude_falso
	conversacion, eventos = crear("mcp_paralelo")
	conversacion.enviar("usa eco tres veces")
	assert eventos.esperar(lambda: len(eventos.de_tipo(TipoEvento.PERMISO)) >= 3)
	conversacion.responder("mcp-a", RespuestaPermiso("permitir_servidor_siempre"))
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	respuestas = _respuestas(registro)
	# Es Claude Code quien lo apunta en .claude/settings.local.json; no es una regla de la sesión.
	assert respuestas[0]["response"]["updatedPermissions"] == [{"type": "addRules", "behavior": "allow",
		"destination": "localSettings", "rules": [{"toolName": "mcp__claude_ai_Prueba"}]}]
	assert conversacion.ajustes.reglas_permitidas == []
	assert len(respuestas) == 3 and all(r["response"]["behavior"] == "allow" for r in respuestas)


def test_permitir_una_vez_no_contesta_las_demas(claude_falso):
	crear, registro = claude_falso
	conversacion, eventos = crear("mcp_paralelo")
	conversacion.enviar("usa eco tres veces")
	assert eventos.esperar(lambda: len(eventos.de_tipo(TipoEvento.PERMISO)) >= 3)
	conversacion.responder("mcp-a", RespuestaPermiso("permitir"))
	time.sleep(0.5)
	assert not eventos.de_tipo(TipoEvento.PERMISO_RETIRADO)
	assert len(_respuestas(registro)) == 1
	conversacion.responder("mcp-b", RespuestaPermiso("denegar"))
	conversacion.responder("mcp-c", RespuestaPermiso("permitir"))
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	assert [r["response"]["behavior"] for r in _respuestas(registro)] == ["allow", "deny", "allow"]


def test_peticion_que_ya_cubre_una_regla_concedida(claude_falso):
	crear, registro = claude_falso
	# Llega después de concederla (Claude ya había preguntado): vale la decisión de la persona.
	conversacion, eventos = crear("mcp_paralelo", reglas_permitidas=[HERRAMIENTA])
	conversacion.enviar("usa eco tres veces")
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	assert not eventos.de_tipo(TipoEvento.PERMISO)
	assert [r["response"]["behavior"] for r in _respuestas(registro)] == ["allow", "allow", "allow"]


def test_en_modo_planificar_no_se_salta_la_pregunta(claude_falso):
	crear, _registro = claude_falso
	conversacion, eventos = crear("mcp_paralelo", reglas_permitidas=[HERRAMIENTA], modo_permiso="plan")
	conversacion.enviar("usa eco tres veces")
	assert eventos.esperar(lambda: len(eventos.de_tipo(TipoEvento.PERMISO)) >= 3)
	for identificador in ("mcp-a", "mcp-b", "mcp-c"):
		conversacion.responder(identificador, RespuestaPermiso("denegar"))
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))


def test_herramienta_que_exige_a_la_persona_no_ofrece_reglas(claude_falso):
	crear, _registro = claude_falso
	conversacion, eventos = crear("mcp_interaccion")
	conversacion.enviar("usa eco")
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.PERMISO)))
	solicitud = eventos.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"]
	assert [o.id for o in solicitud.opciones] == ["permitir", "denegar"]
	conversacion.responder(solicitud.id, RespuestaPermiso("permitir"))
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))


def test_en_modo_planificar_avisa_de_que_preguntara(tmp_path):
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(
		AjustesSesion(carpeta=tmp_path, modo_permiso="plan"), lambda evento: None)
	solicitud = conversacion._solicitud_herramienta("x", {}, HERRAMIENTA, {}, [])
	assert "Solo planificar" in solicitud.detalle
	# Sin sugerencia de Claude Code no se ofrece «siempre» (no sabría dónde guardarlo).
	assert "permitir_siempre" not in [o.id for o in solicitud.opciones]


@pytest.mark.parametrize(("regla", "herramienta", "entrada", "esperado"), [
	("mcp__prueba__eco", "mcp__prueba__eco", {}, True),
	("mcp__prueba__eco", "mcp__prueba__hora", {}, False),
	("mcp__prueba", "mcp__prueba__hora", {}, True),
	("mcp__prueba__*", "mcp__prueba__hora", {}, True),
	("mcp__prueba", "mcp__prueba_dos__hora", {}, False),
	("Bash(git push)", "Bash", {"command": "git push"}, True),
	("Bash(git push)", "Bash", {"command": "git push --force"}, False),
	("Bash(git status:*)", "Bash", {"command": "git status -s"}, True),
	("Bash(git status:*)", "Bash", {"command": "git status && rm -rf x"}, False),
	("Read(//c/proyecto/**)", "Read", {"file_path": "C:/proyecto/a.txt"}, False),
	("Edit", "Edit", {"file_path": "a.txt"}, True),
])
def test_regla_cubre(regla, herramienta, entrada, esperado):
	assert regla_cubre(regla, herramienta, entrada) is esperado


def test_consultas_sin_conversar_no_arrancan_servidores_mcp(tmp_path, monkeypatch):
	from app.services.agentes import claude, procesos

	ordenes = []

	class ProcesoFalso:
		def __init__(self, orden, *_a, **_k):
			ordenes.append(orden)

		def iniciar(self):
			raise OSError("sin proceso")

		def cerrar_entrada(self):
			pass

		def terminar(self, espera=0):
			pass

	monkeypatch.setattr(procesos, "ProcesoLineas", ProcesoFalso)
	claude.ProveedorClaude(orden_base=["claude"]).obtener_comandos(tmp_path)
	assert "--strict-mcp-config" in ordenes[0]


# -- Codex ---------------------------------------------------------------------


def test_permiso_mcp_de_codex_de_un_servidor_propio():
	peticion = {"serverName": "prueba", "mode": "form", "message": 'Allow the prueba MCP server to run tool "hora"?',
		"requestedSchema": {"type": "object", "properties": {}},
		"_meta": {"codex_approval_kind": "mcp_tool_call", "persist": ["session", "always"], "tool_params": {}}}
	solicitud = codex._solicitud_mcp(0, peticion)
	assert solicitud.titulo == "Codex quiere usar «hora» del servidor MCP «prueba»"
	assert "¿Permites que el servidor MCP «prueba» use su herramienta «hora»?" in solicitud.detalle
	assert "Allow the" not in solicitud.detalle
	assert [o.id for o in solicitud.opciones] == ["accept", "accept_session", "accept_always", "decline", "cancel"]


# -- Antigravity ---------------------------------------------------------------


def test_antigravity_describe_las_herramientas_mcp():
	assert describir_herramienta("call_mcp_tool", {"ServerName": "prueba", "ToolName": "hora"}) == \
		"Usa la herramienta «hora» del servidor MCP «prueba»"
	assert describir_denegacion("mcp(prueba/hora)") == ("usar la herramienta «hora» del servidor MCP «prueba»", "mcp(prueba/hora)")
	assert describir_denegacion("mcp(<target>)") == ("una acción", "")
	assert regla_servidor_mcp("mcp(prueba/hora)") == "mcp(prueba/*)"
	assert regla_servidor_mcp("command(git)") == ""


def test_antigravity_mcp_denegado_permite_el_servidor(tmp_path, monkeypatch):
	monkeypatch.setenv("FAKE_AGY_REGISTRO", str(tmp_path / "registro.jsonl"))
	ajustes = tmp_path / "settings.json"
	proyecto = tmp_path / "proyecto"
	proyecto.mkdir()
	proveedor = ProveedorAntigravity(ejecutable=[sys.executable, str(FALSOS / "fake_agy.py")], ruta_ajustes=ajustes)
	eventos = Recolector()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=proyecto), eventos)
	try:
		conversacion.enviar("[mcp_denegado] dime la hora")
		assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
		time.sleep(0.6)  # el error estándar se revisa un poco después
		fin = eventos.de_tipo(TipoEvento.HERRAMIENTA_FIN)
		assert fin and "denied permission" in fin[0].datos["error"]
		denegadas = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("denegada")]
		assert len(denegadas) == 1
		assert denegadas[0].datos["regla"] == "mcp(prueba/hora)"
		assert "«hora» del servidor MCP «prueba»" in denegadas[0].texto
		conversacion.responder(denegadas[0].datos["solicitud_id"], RespuestaPermiso(OPCION_SERVIDOR_REINTENTAR))
		assert eventos.esperar(lambda: len(eventos.de_tipo(TipoEvento.FIN_TURNO)) >= 2)
		assert leer_reglas(ajustes) == ["mcp(prueba/*)"]
	finally:
		conversacion.cerrar()


# -- Portapapeles ----------------------------------------------------------------


wx = pytest.importorskip("wx")


class PortapapelesFalso:
	"""Imita ``wx.TheClipboard`` con los formatos que se le den (el de verdad no se toca)."""

	def __init__(self, *, texto: str = "", png: bytes = b"", archivos: list[Path] | None = None,
			mapa: wx.Bitmap | None = None, abre: bool = True) -> None:
		self.texto, self.png, self.archivos, self.mapa, self.abre = texto, png, archivos or [], mapa, abre
		self.abierto = False

	def Open(self) -> bool:  # noqa: N802 - API de wx
		self.abierto = self.abre
		return self.abre

	def Close(self) -> None:  # noqa: N802
		self.abierto = False

	def IsSupported(self, formato: wx.DataFormat) -> bool:  # noqa: N802
		tipo = formato.GetType()
		if tipo in (wx.DF_UNICODETEXT, wx.DF_TEXT):
			return bool(self.texto)
		if tipo == wx.DF_FILENAME:
			return bool(self.archivos)
		if tipo == wx.DF_BITMAP:
			return self.mapa is not None
		return formato.GetId() == "PNG" and bool(self.png)

	def GetData(self, datos) -> bool:  # noqa: N802
		if isinstance(datos, wx.FileDataObject):
			for ruta in self.archivos:
				datos.AddFile(str(ruta))
			return True
		if isinstance(datos, wx.CustomDataObject):
			datos.SetData(self.png)
			return True
		if isinstance(datos, wx.BitmapDataObject) and self.mapa is not None:
			datos.SetBitmap(self.mapa)
			return True
		return False


def _png(app_wx, ruta: Path) -> bytes:
	imagen = wx.Image(4, 3)
	imagen.SetRGB(wx.Rect(0, 0, 4, 3), 255, 0, 0)
	imagen.SaveFile(str(ruta), wx.BITMAP_TYPE_PNG)
	return ruta.read_bytes()


def test_pega_el_formato_png(app_wx, tmp_path, monkeypatch):
	from app.ui import portapapeles

	contenido = _png(app_wx, tmp_path / "origen.png")
	monkeypatch.setattr(wx, "TheClipboard", PortapapelesFalso(png=contenido))
	copiado = portapapeles.leer_adjuntables(tmp_path / "imagenes")
	assert len(copiado.adjuntos) == 1
	assert copiado.adjuntos[0].tipo_mime == "image/png"
	assert copiado.adjuntos[0].ruta.read_bytes() == contenido


def test_pega_archivos_copiados_en_el_explorador(app_wx, tmp_path, monkeypatch):
	from app.ui import portapapeles

	imagen, texto = tmp_path / "captura.png", tmp_path / "notas.txt"
	_png(app_wx, imagen)
	texto.write_text("hola", encoding="utf-8")
	monkeypatch.setattr(wx, "TheClipboard", PortapapelesFalso(archivos=[imagen, texto, tmp_path]))
	copiado = portapapeles.leer_adjuntables(tmp_path / "imagenes")
	assert [(a.ruta.name, a.tipo_mime) for a in copiado.adjuntos] == [("captura.png", "image/png"), ("notas.txt", "text/plain")]


def test_captura_sin_transparencia_de_verdad_no_queda_invisible(app_wx, tmp_path, monkeypatch):
	from app.ui import portapapeles

	imagen = wx.Image(4, 3)
	imagen.SetRGB(wx.Rect(0, 0, 4, 3), 0, 128, 255)
	imagen.InitAlpha()
	imagen.SetAlpha(bytes(4 * 3))  # canal alfa a cero, como muchas capturas de 32 bits

	class MapaFalso:
		"""Un wx.Bitmap de verdad premultiplicaría el alfa y perdería el color: se simula."""

		def IsOk(self) -> bool:  # noqa: N802 - API de wx
			return True

		def ConvertToImage(self) -> wx.Image:  # noqa: N802
			return imagen.Copy()

	class DatosFalsos(wx.BitmapDataObject):
		def GetBitmap(self):  # noqa: N802
			return MapaFalso()

	monkeypatch.setattr(portapapeles.wx, "BitmapDataObject", DatosFalsos)
	monkeypatch.setattr(wx, "TheClipboard", PortapapelesFalso(mapa=wx.Bitmap(4, 3)))
	copiado = portapapeles.leer_adjuntables(tmp_path / "imagenes")
	guardada = wx.Image(str(copiado.adjuntos[0].ruta))
	assert not guardada.HasAlpha() or guardada.GetAlpha(1, 1) == 255
	assert (guardada.GetRed(1, 1), guardada.GetGreen(1, 1), guardada.GetBlue(1, 1)) == (0, 128, 255)


def test_con_texto_ctrl_v_pega_el_texto(app_wx, tmp_path, monkeypatch):
	from app.ui import portapapeles

	contenido = _png(app_wx, tmp_path / "origen.png")
	monkeypatch.setattr(wx, "TheClipboard", PortapapelesFalso(texto="celda A1", png=contenido))
	copiado = portapapeles.leer_adjuntables(tmp_path / "imagenes")
	assert copiado.adjuntos == [] and copiado.hay_texto and copiado.hay_adjuntable
	forzado = portapapeles.leer_adjuntables(tmp_path / "imagenes", aunque_haya_texto=True)
	assert len(forzado.adjuntos) == 1


def test_portapapeles_ocupado(app_wx, tmp_path, monkeypatch):
	from app.ui import portapapeles

	monkeypatch.setattr(wx, "TheClipboard", PortapapelesFalso(abre=False))
	assert portapapeles.leer_adjuntables(tmp_path) is None


def test_copiar_texto_lo_deja_tras_cerrar(app_wx, monkeypatch):
	from app.ui.accesibilidad import teclado

	llamadas = []

	class Falso(PortapapelesFalso):
		def SetData(self, datos):  # noqa: N802
			llamadas.append(("SetData", datos.GetText()))
			return True

		def Flush(self):  # noqa: N802
			llamadas.append(("Flush", self.abierto))

	monkeypatch.setattr(wx, "TheClipboard", Falso())
	assert teclado.copiar_texto("hola")
	assert llamadas == [("SetData", "hola"), ("Flush", True)]
	monkeypatch.setattr(wx, "TheClipboard", Falso(abre=False))
	assert not teclado.copiar_texto("hola")


# -- De AIChat 0.1.9 y 0.1.10 ----------------------------------------------------


def test_permitir_en_la_sesion_conserva_la_carpeta_sugerida(claude_falso, tmp_path, monkeypatch):
	"""AIChat #334: con la regla va la carpeta que sugiere Claude Code, nunca una unidad entera."""
	crear, registro = claude_falso
	fuera = str(tmp_path / "fuera")
	monkeypatch.setenv("FAKE_CLAUDE_FUERA", fuera)
	conversacion, eventos = crear("carpeta")
	conversacion.enviar("crea la carpeta")
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.PERMISO)))
	solicitud = eventos.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"]
	assert fuera in solicitud.detalle
	conversacion.responder(solicitud.id, RespuestaPermiso("permitir_sesion"))
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	cambios = _respuestas(registro)[0]["response"]["updatedPermissions"]
	assert cambios == [
		{"type": "addRules", "behavior": "allow", "destination": "session", "rules": [{"toolName": "Bash", "ruleContent": "mkdir nueva"}]},
		{"type": "addDirectories", "directories": [fuera], "destination": "session"},
	]
	assert conversacion.ajustes.reglas_permitidas == ["Bash(mkdir nueva)", f"Carpeta({fuera})"]
	# En el siguiente arranque la carpeta va como --add-dir, no como regla.
	orden = conversacion.construir_orden()
	assert orden[orden.index("--allowedTools") + 1:orden.index("--allowedTools") + 2] == ["Bash(mkdir nueva)"]
	assert f"Carpeta({fuera})" not in orden
	assert orden[orden.index("--add-dir") + 1] == fuera


@pytest.mark.parametrize(("carpeta", "posix", "esperado"), [
	(r"C:\proyectos\web", False, True),
	("C:\\", False, False),
	("C:", False, False),
	(r"\\servidor\recurso", False, False),
	(r"\\servidor\recurso\web", False, True),
	(r"\\?\C:\web", False, False),
	(r"relativa\web", False, False),
	("-x", False, False),
	("/", True, False),
	("/home/ana/web", True, True),
])
def test_carpeta_concreta(carpeta, posix, esperado):
	from app.services.agentes.claude import carpeta_concreta

	assert carpeta_concreta(carpeta, posix) is esperado


def test_reglas_de_la_configuracion_de_claude(tmp_path, monkeypatch):
	"""AIChat #336: las reglas de Claude Code para la carpeta, solo para leer; las compartidas esperan a la confianza."""
	from app.services.config_agentes import reglas_claude
	from app.ui.dialogos.permisos_sesion import describir_regla

	casa = tmp_path / "casa"
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(casa))
	monkeypatch.setattr(reglas_claude, "archivos_gestionados", lambda: [])
	proyecto = tmp_path / "proyecto"
	(proyecto / ".claude").mkdir(parents=True)
	casa.mkdir()
	(casa / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Bash(rm -rf:*)"]}}), encoding="utf-8")
	(proyecto / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(npm test)"]}}), encoding="utf-8")
	(proyecto / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"allow": ["mcp__prueba"]}}), encoding="utf-8")
	reglas, ilegibles = reglas_claude.reglas_de(str(proyecto))
	assert [(r.clase, r.regla, r.aplicada) for r in reglas] == [
		("deny", "Bash(rm -rf:*)", True), ("allow", "Bash(npm test)", False), ("allow", "mcp__prueba", True)]
	texto = reglas_claude.describir(reglas, ilegibles, describir_regla)
	assert "Nunca permitido: Ejecutar «rm -rf» con cualquier argumento" in texto
	assert "Permitido sin preguntar: Herramientas del servidor MCP prueba: cualquiera" in texto
	assert "No cuentan hasta que esta carpeta sea de confianza" in texto
	# Con la carpeta de confianza, la compartida cuenta.
	(casa / ".claude.json").write_text(json.dumps({"projects": {str(proyecto).replace("\\", "/"): {
		"hasTrustDialogAccepted": True}}}), encoding="utf-8")
	assert all(r.aplicada for r in reglas_claude.reglas_de(str(proyecto))[0])
	# Un archivo dañado se dice, no se oculta.
	(proyecto / ".claude" / "settings.local.json").write_text("{dañado", encoding="utf-8")
	assert reglas_claude.reglas_de(str(proyecto))[1] == [str(proyecto / ".claude" / "settings.local.json")]


def test_archivos_de_reglas_de_codex(tmp_path, monkeypatch):
	from app.services.config_agentes import reglas_codex

	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	assert reglas_codex.archivos_de_reglas() == []
	assert "no tiene archivos de reglas" in reglas_codex.describir([])
	(tmp_path / "rules").mkdir()
	(tmp_path / "rules" / "default.rules").write_text("x", encoding="utf-8")
	(tmp_path / "rules" / "notas.txt").write_text("x", encoding="utf-8")
	archivos = reglas_codex.archivos_de_reglas()
	assert [a.name for a in archivos] == ["default.rules"]
	assert "1 archivos de reglas" in reglas_codex.describir(archivos)


def test_reenviar_elige_la_respuesta_y_los_destinos():
	"""AIChat #248: la respuesta elegida o la última, con su etiqueta, a otra sesión propia."""
	from app.services import reenviar
	from app.services.gestor import FilaSesion, MensajeVista

	mensajes = [MensajeVista("usuario", "revisa", 1), MensajeVista("asistente", "Todo bien.", 2),
		MensajeVista("herramienta", "ls", 3)]
	assert reenviar.mensaje_a_reenviar(mensajes).texto == "Todo bien."
	assert reenviar.mensaje_a_reenviar(mensajes, mensajes[0]).texto == "Todo bien."
	assert reenviar.mensaje_a_reenviar([MensajeVista("usuario", "hola", 1)]) is None
	texto = reenviar.texto_reenviado(mensajes[1], "Codex", "Revisión")
	assert texto.startswith("De Codex, otro agente de IA; te lo reenvía la persona desde su sesión «Revisión»:")
	assert texto.endswith("Todo bien.")
	filas = [
		FilaSesion("ctrlia:a", "claude", "Origen", actividad=5),
		FilaSesion("ctrlia:b", "codex", "Reciente", actividad=9),
		FilaSesion("ctrlia:c", "claude", "Pareja", actividad=1),
		FilaSesion("ctrlia:d", "claude", "Oculta", oculta=True),
		FilaSesion("ctrlia:e", "claude", "Archivada", archivada=True),
		FilaSesion("claude:x", "claude", "De la terminal", actividad=99),
	]
	destinos = reenviar.destinos(filas, "ctrlia:a", lambda f: True, pareja="ctrlia:c")
	assert [d.titulo for d in destinos] == ["Pareja", "Reciente"]
	assert reenviar.demasiado_largo("codex", "Codex", "x" * 32001).startswith("Codex admite mensajes de hasta 32.000")
	assert reenviar.demasiado_largo("claude", "Claude", "x" * 40000) == ""


def test_dialogo_reenviar_no_pierde_lo_escrito(app_wx, monkeypatch):
	from app.ui.dialogos import reenviar as modulo

	avisos_vistos = []
	monkeypatch.setattr(modulo, "mostrar_mensaje", lambda padre, texto, titulo="": avisos_vistos.append(texto))
	dialogo = modulo.DialogoReenviar(None, "Origen", ["Pareja, Claude", "Otra, Codex"], "De Claude...\n\nHola",
		lambda indice, texto: "Demasiado largo" if indice == 1 else "", trabajando=True)
	try:
		assert "sigue trabajando" in dialogo.GetTitle()
		assert dialogo.destinos.GetSelection() == 0
		dialogo.destinos.SetSelection(1)
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		assert not dialogo.enviar()
		assert avisos_vistos == ["Demasiado largo"] and dialogo.elegido is None
		dialogo.destinos.SetSelection(0)
		dialogo.texto.SetValue("  ")
		assert not dialogo.enviar()
		dialogo.texto.SetValue("De Claude...\n\nHola y adiós")
		assert dialogo.enviar()
		assert dialogo.elegido == (0, "De Claude...\n\nHola y adiós")
	finally:
		dialogo.Destroy()
