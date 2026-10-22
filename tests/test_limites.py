"""Límites de uso de las cuentas (ventana de 5 horas y semanal)."""

from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path

import pytest

from app.services import limites
from app.services.limites import LimitesCuenta, VentanaLimite

AHORA = 1_792_250_000.0

RESPUESTA_CODEX = {
	"ordinaryUsageAllowed": True,
	"rateLimits": {
		"limitId": "codex", "limitName": None,
		"primary": {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": AHORA + 2 * 3600 + 15 * 60},
		"secondary": {"usedPercent": 39, "windowDurationMins": 10080, "resetsAt": AHORA + 4 * 86400},
		"credits": {"hasCredits": False, "unlimited": False, "balance": "0"}, "planType": "plus",
	},
	"rateLimitsByLimitId": {
		"codex": {},
		"codex_spark": {"limitName": "GPT-5 Spark", "primary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": None}},
	},
}


def test_codex_en_vivo():
	resultado = limites.desde_codex(RESPUESTA_CODEX)
	assert resultado is not None and resultado.en_vivo and resultado.plan == "plus"
	texto = limites.lineas(resultado, AHORA)
	assert texto[0].startswith("Límite de 5 horas: 37 % usado, se reinicia en 2 h 15 min (a las ")
	assert texto[1].startswith("Límite semanal: 39 % usado, se reinicia el ")
	assert "GPT-5 Spark, límite semanal: 5 % usado." in texto
	assert "Plan: plus." in texto


def test_codex_solo_semanal_se_reconoce_por_la_duracion():
	"""Codex ha llegado a mandar la semanal como «primaria» y sin la de 5 horas (openai/codex#32707)."""
	resultado = limites.desde_codex({"rateLimits": {"primary": {"usedPercent": 12, "windowDurationMins": 10080}, "secondary": None}})
	texto = limites.lineas(resultado, AHORA)
	assert texto[0] == "Límite semanal: 12 % usado."
	assert "no informa de un límite de 5 horas" in texto[1]


def test_codex_sin_datos_o_raro():
	assert limites.desde_codex(None) is None
	assert limites.desde_codex({"rateLimits": {"primary": None}}) is None
	assert limites.desde_codex({"rateLimits": {"primary": {"usedPercent": True}}}) is None
	ventana = limites.desde_codex({"rateLimits": {"primary": {"usedPercent": 140}}}).ventanas[0]
	assert ventana.porcentaje == 100 and limites.nombre_ventana(ventana) == "Límite"


def test_ventana_caducada_vale_cero():
	ventana = VentanaLimite(80, 300, AHORA - 10)
	assert limites.texto_ventana(ventana, AHORA) == "Límite de 5 horas: 0 % usado (ya se ha reiniciado)."


@pytest.mark.parametrize(("minutos", "nombre"), [
	(300, "Límite de 5 horas"), (10080, "Límite semanal"), (1440, "Límite de 1 día"), (60, "Límite de 1 hora"),
	(120, "Límite de 2 horas"), (45, "Límite de 45 minutos"), (None, "Límite"),
])
def test_nombres_de_ventana(minutos, nombre):
	assert limites.nombre_ventana(VentanaLimite(1, minutos)) == nombre


def test_reinicio_textos():
	assert limites.texto_reinicio(None) == ""
	assert limites.texto_reinicio(AHORA - 1, AHORA) == "ya se ha reiniciado"
	assert limites.texto_reinicio(AHORA + 30, AHORA).startswith("se reinicia en 1 min")
	assert limites.texto_reinicio(AHORA + 3 * 86400, AHORA).startswith("se reinicia el ")


def test_dato_guardado_dice_de_cuando_es():
	resultado = LimitesCuenta([VentanaLimite(10, 300, AHORA + 60)], en_vivo=False, fecha=AHORA - 3600, nota="Nota.")
	texto = limites.lineas(resultado, AHORA)
	assert any(linea.startswith("Es el último dato guardado, del ") for linea in texto)
	assert texto[-1] == "Nota."


def _rollout(carpeta: Path, nombre: str, lineas: list[dict], fecha: float) -> Path:
	carpeta.mkdir(parents=True, exist_ok=True)
	ruta = carpeta / nombre
	ruta.write_text("\n".join(json.dumps(linea) for linea in lineas) + "\n", encoding="utf-8")
	os.utime(ruta, (fecha, fecha))
	return ruta


def test_codex_ultimo_dato_en_disco(tmp_path):
	evento = {"timestamp": "2026-10-17T12:45:10.596Z", "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
		"limit_id": "codex", "primary": {"used_percent": 3.0, "window_minutes": 300, "resets_at": 1792259102},
		"secondary": {"used_percent": 34.0, "window_minutes": 10080, "resets_at": 1792689505}, "plan_type": "plus"}}}
	antiguo = dict(evento, payload=dict(evento["payload"], rate_limits=dict(evento["payload"]["rate_limits"],
		primary={"used_percent": 99.0, "window_minutes": 300})))
	_rollout(tmp_path / "sessions" / "2026" / "10" / "16", "rollout-a.jsonl", [antiguo], time.time() - 86400)
	_rollout(tmp_path / "sessions" / "2026" / "10" / "17", "rollout-b.jsonl",
		[{"type": "session_meta"}, evento, {"type": "otra"}, "no es json"], time.time())
	resultado = limites.ultimo_codex_en_disco(tmp_path)
	assert resultado is not None and not resultado.en_vivo
	assert [v.porcentaje for v in resultado.ventanas] == [3.0, 34.0]
	assert resultado.plan == "plus"
	esperada = datetime.datetime(2026, 10, 17, 12, 45, 10, 596000, tzinfo=datetime.UTC).timestamp()
	assert abs(resultado.fecha - esperada) < 0.01


def test_codex_sin_sesiones_en_disco(tmp_path):
	assert limites.ultimo_codex_en_disco(tmp_path) is None


# ── Proveedores ──────────────────────────────────────────────────────────────


def test_codex_lee_en_vivo_y_si_falla_usa_el_disco(tmp_path, monkeypatch):
	from app.services.agentes.codex import ErrorCodex, ProveedorCodex

	proveedor = ProveedorCodex()
	pedidas = []
	monkeypatch.setattr(proveedor, "_consultar", lambda metodo, params: pedidas.append((metodo, params)) or RESPUESTA_CODEX)
	assert proveedor.leer_limites().ventanas[0].porcentaje == 37
	assert pedidas == [("account/rateLimits/read", {"excludeResetCreditDetails": True})]

	def falla(metodo, params):
		raise ErrorCodex("401 Unauthorized")

	monkeypatch.setattr(proveedor, "_consultar", falla)
	with pytest.raises(limites.ErrorLimites, match="sesión iniciada"):
		proveedor.leer_limites()
	evento = {"type": "event_msg", "payload": {"rate_limits": {"primary": {"used_percent": 8.0, "window_minutes": 300}}}}
	_rollout(Path(os.environ["CODEX_HOME"]) / "sessions", "rollout-x.jsonl", [evento], time.time())
	guardado = proveedor.leer_limites()
	assert not guardado.en_vivo and guardado.ventanas[0].porcentaje == 8.0


def test_los_demas_agentes_no_informan_por_defecto():
	from app.services.agentes.ollama import ProveedorOllama

	assert ProveedorOllama(servidor="http://127.0.0.1:1").leer_limites() is None


# ── Claude Code ──────────────────────────────────────────────────────────────

INFORME_CLAUDE = {"rate_limits": {"limits": [
	{"kind": "session", "group": "session", "percent": 9, "resets_at": "2026-10-19T15:00:00Z", "severity": "normal"},
	{"kind": "weekly_all", "group": "weekly", "percent": 15, "resets_at": "2026-10-23T08:00:00Z"},
	{"kind": "weekly_scoped", "group": "weekly", "percent": 40, "resets_at": None, "scope": {"model": {"display_name": "Opus"}}},
], "extra_usage": {"is_enabled": True}}}


def test_claude_usage_report():
	resultado = limites.desde_claude(INFORME_CLAUDE)
	texto = limites.lineas(resultado, AHORA)
	assert texto[0].startswith("Límite de 5 horas: 9 % usado")
	assert texto[1].startswith("Límite semanal: 15 % usado")
	assert texto[2] == "Solo Opus, límite semanal: 40 % usado."
	assert "uso adicional de pago" in texto[-1]
	assert limites.desde_claude({"rate_limits": None}) is None
	assert limites.desde_claude("no") is None


def _salida(*mensajes) -> str:
	return "\n".join(json.dumps(m) for m in mensajes)


def test_claude_lee_limites_con_usage(monkeypatch):
	from app.services.agentes import claude, procesos

	llamadas = []
	salida = _salida({"type": "system", "subtype": "init"},
		{"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "Uso"}]}},
		{"type": "result", "num_turns": 0, "total_cost_usd": 0, "usage_report": INFORME_CLAUDE})
	monkeypatch.setattr(procesos, "ejecutar_y_leer", lambda orden, tiempo, entorno=None: llamadas.append(orden) or (0, salida, ""))
	resultado = claude.ProveedorClaude(orden_base=["claude"]).leer_limites()
	# Sin servidores MCP: consultar los límites no debe arrancarlos.
	assert llamadas[0] == ["claude", "-p", "/usage", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
		"--strict-mcp-config"]
	assert [v.porcentaje for v in resultado.ventanas] == [9, 15, 40]


def test_claude_sin_sesion_o_solo_texto(monkeypatch):
	from app.services.agentes import claude, procesos

	def con_texto(texto):
		salida = _salida({"type": "assistant", "message": {"content": [{"type": "text", "text": texto}]}},
			{"type": "result", "num_turns": 0})
		monkeypatch.setattr(procesos, "ejecutar_y_leer", lambda orden, tiempo, entorno=None: (0, salida, ""))
		return claude.ProveedorClaude(orden_base=["claude"])

	with pytest.raises(limites.ErrorLimites, match="no ha iniciado sesión"):
		con_texto("Total cost:            $0").leer_limites()
	resultado = con_texto("Usage is\n  temporarily unavailable").leer_limites()
	assert resultado.ventanas == []
	assert limites.lineas(resultado) == ["Claude Code dice (en inglés): Usage is temporarily unavailable"]


# ── Antigravity ──────────────────────────────────────────────────────────────

SALIDA_AGY = {"conversation_id": "", "status": "SUCCESS", "num_turns": 0, "command": {"name": "usage", "data": {"groups": [
	{"name": "Gemini Models", "buckets": [
		{"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.86, "reset_time": "2026-10-26T10:03:20Z"},
		{"id": "gemini-5h", "window": "5h", "remaining_fraction": 0.95, "reset_time": "2026-10-19T15:03:20Z"}]},
	{"name": "Claude and GPT models", "buckets": [{"id": "3p-5h", "window": "5h", "remaining_fraction": 1}]},
	{"name": "Gemini 4 Argon", "buckets": [{"id": "argon", "window": "weekly", "remaining_fraction": 0.9911}]},
	{"name": "Raro", "buckets": [{"id": "x", "name": "Monthly", "window": "monthly", "remaining_fraction": 0.5}, "no"]},
]}}}


def test_antigravity_usage():
	resultado = limites.desde_antigravity(SALIDA_AGY)
	texto = limites.lineas(resultado, AHORA)
	assert texto[0].startswith("Modelos Gemini, límite de 5 horas: 5 % usado")
	assert texto[1].startswith("Modelos Gemini, límite semanal: 14 % usado")
	assert texto[2] == "Modelos Claude y GPT, límite de 5 horas: 0 % usado."
	assert texto[3] == "Gemini 4 Argon, límite semanal: 1 % usado."
	assert texto[4] == "Raro, Monthly: 50 % usado."
	assert limites.desde_antigravity({"response": "texto"}) is None


def _agy(monkeypatch, version: str, salida: dict | str):
	from app.services.agentes import antigravity

	llamadas = []
	monkeypatch.setattr(antigravity, "version_de", lambda orden: version)
	texto = salida if isinstance(salida, str) else json.dumps(salida)
	monkeypatch.setattr(antigravity, "ejecutar_y_leer",
		lambda orden, tiempo, entorno=None: llamadas.append(orden) or (0, texto, ""))
	return antigravity.ProveedorAntigravity(ejecutable="agy"), llamadas


def test_antigravity_lee_limites(monkeypatch):
	proveedor, llamadas = _agy(monkeypatch, "1.4.2", SALIDA_AGY)
	assert len(proveedor.leer_limites().ventanas) == 5
	assert llamadas == [["agy", "-p", "/usage", "--output-format", "json", "--print-timeout", "60s"]]


def test_antigravity_antiguo_no_pregunta(monkeypatch):
	"""Un agy sin /usage lo tomaría por un mensaje y gastaría cuota: no se le pregunta."""
	proveedor, llamadas = _agy(monkeypatch, "agy 1.1.10", SALIDA_AGY)
	with pytest.raises(limites.ErrorLimites, match="1.1.11 o posterior"):
		proveedor.leer_limites()
	assert llamadas == []


def test_antigravity_descarta_una_respuesta_de_modelo(monkeypatch):
	proveedor, _llamadas = _agy(monkeypatch, "1.4.2", {"status": "SUCCESS", "num_turns": 1, "response": "Hola"})
	with pytest.raises(limites.ErrorLimites, match="no ha dado los límites"):
		proveedor.leer_limites()
	proveedor, _llamadas = _agy(monkeypatch, "1.4.2", "no es json")
	with pytest.raises(limites.ErrorLimites):
		proveedor.leer_limites()


# ── Gestor y documento ───────────────────────────────────────────────────────


class _Proveedor:
	def __init__(self, identificador, nombre, valor):
		self.id, self.nombre, self._valor = identificador, nombre, valor

	def leer_limites(self):
		if isinstance(self._valor, BaseException):
			raise self._valor
		return self._valor


def test_gestor_pregunta_a_todos(tmp_path):
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones

	cuenta = LimitesCuenta([VentanaLimite(20, 300, AHORA + 60)])
	proveedores = {
		"codex": _Proveedor("codex", "Codex", cuenta),
		"claude": _Proveedor("claude", "Claude Code", limites.ErrorLimites("Sin sesión.")),
		"ollama": _Proveedor("ollama", "Ollama", None),
		"roto": _Proveedor("roto", "Roto", RuntimeError("fallo")),
	}
	almacen = Almacen(tmp_path / "n.db")
	try:
		gestor = GestorSesiones(almacen, proveedores, lambda c, e: None)
		gestor._limites["claude"] = {"five_hour": {"texto": "Has usado el 9 % del límite de las próximas horas."}}
		resultados = gestor.leer_limites_todos()
	finally:
		almacen.cerrar()
	assert [r.nombre for r in resultados] == ["Codex", "Claude Code", "Roto"]
	assert resultados[0].limites is cuenta and gestor.limites_cuenta("codex") is cuenta
	assert resultados[1].error == "Sin sesión." and resultados[1].durante_sesion == ["Has usado el 9 % del límite de las próximas horas."]
	assert "fallo" in resultados[2].error
	texto = limites.documento(resultados, AHORA)
	assert texto.startswith("# Límites de uso de tus cuentas")
	assert "## Codex\n\n- Límite de 5 horas: 20 % usado" in texto
	assert "  - Has usado el 9 %" in texto
	assert "## Agentes sin consulta de límites" in texto and "Gemini CLI" in texto
	assert "no envía ningún mensaje ni gasta cuota" in texto


# ── Claude Code: el texto en inglés de /usage (sin usage_report, como en la 2.1.238) ──

TEXTO_CLAUDE = (
	"You are currently using your subscription to power your Claude Code usage\n\n"
	"Current session: 9% used · resets Oct 19, 4:30pm (Europe/Paris)\n"
	"Current week (all models): 50% used · resets Oct 21, 1am (Europe/Paris)\n"
	"Current week (Fable): 0% used · resets Oct 21, 1am (Europe/Paris)\n\n"
	"What's contributing to your limits usage?\n"
	"Approximate, based on local sessions on this machine — does not include other devices or claude.ai. "
	"Behaviors are independent characteristics, not a breakdown.\n\n"
	"Last 24h · 747 requests · 4 sessions\n"
	"  89% of your usage came from subagent-heavy sessions\n"
	"  82% of your usage was at >150k context\n"
	"  Top subagents: general-purpose 12%\n"
	"  Top MCP servers: ccd_turn 4%\n\n"
	"Last 7d · 7807 requests · 21 sessions\n"
	"  44% of your usage came from sessions active for 8+ hours\n"
	"  Top subagents: general-purpose 19%, Explore 1%\n"
	"  Something brand new happened\n"
)


def _hora_local(anio, mes, dia, hora, minuto=0) -> float:
	return datetime.datetime(anio, mes, dia, hora, minuto).timestamp()


def test_claude_texto_se_pone_en_espanol():
	ahora = _hora_local(2026, 10, 19, 13, 0)
	resultado = limites.desde_texto_claude(TEXTO_CLAUDE, ahora)
	assert [(v.porcentaje, v.minutos, v.grupo) for v in resultado.ventanas] == [
		(9.0, 300, ""), (50.0, 10080, ""), (0.0, 10080, "Solo Fable")]
	assert resultado.ventanas[0].reinicio == _hora_local(2026, 10, 19, 16, 30)
	assert resultado.ventanas[1].reinicio == _hora_local(2026, 10, 21, 1, 0)
	texto = limites.lineas(resultado, ahora)
	assert texto[0] == "Límite de 5 horas: 9 % usado, se reinicia en 3 h 30 min (a las 16:30)."
	assert texto[1] == "Límite semanal: 50 % usado, se reinicia el miércoles 21/10 a las 01:00."
	assert texto[2] == "Solo Fable, límite semanal: 0 % usado, se reinicia el miércoles 21/10 a las 01:00."
	assert texto[3] == "Claude Code está usando tu suscripción."
	assert resultado.detalle == [
		"Es aproximado: solo cuenta las sesiones de este equipo, no las de otros dispositivos ni las de claude.ai. "
		"Cada dato es independiente; no es un reparto del total.",
		"Últimas 24 horas: 747 peticiones en 4 sesiones.",
		"  El 89 % de tu uso vino de sesiones con muchos subagentes.",
		"  El 82 % de tu uso fue con más de 150 000 unidades de texto de contexto.",
		"  Subagentes que más gastan: general-purpose 12 %.",
		"  Servidores MCP que más gastan: ccd_turn 4 %.",
		"Últimos 7 días: 7807 peticiones en 21 sesiones.",
		"  El 44 % de tu uso vino de sesiones abiertas durante 8 horas o más.",
		"  Subagentes que más gastan: general-purpose 19 %, Explore 1 %.",
		"  (En inglés) Something brand new happened",
	]
	documento = limites.documento([limites.ResultadoAgente("Claude Code", resultado)], ahora)
	assert "### Qué está gastando tu límite de Claude Code" in documento
	assert "\n  - El 89 % de tu uso vino de sesiones con muchos subagentes." in documento
	assert "Current" not in documento and "used" not in documento


@pytest.mark.parametrize(("texto", "esperado"), [
	("Jan 2, 9am", (2027, 1, 2, 9, 0)),  # visto en diciembre: es del año siguiente
	("Dec 30, 11:15pm (Europe/Paris)", (2026, 12, 30, 23, 15)),
	("10pm", (2026, 12, 29, 22, 0)),
	("8am", (2026, 12, 30, 8, 0)),  # ya pasó hoy: mañana
	("Oct 21", (2027, 10, 21, 0, 0)),  # un reinicio siempre es futuro
])
def test_claude_horas_de_reinicio(texto, esperado):
	ahora = _hora_local(2026, 12, 29, 12, 0)
	assert limites._reinicio_claude(texto, ahora) == _hora_local(*esperado)


@pytest.mark.parametrize("texto", ["soon", "Foo 3, 4pm", "Feb 31, 1am", ""])
def test_claude_horas_raras_sin_reinicio(texto):
	assert limites._reinicio_claude(texto, AHORA) is None


def test_claude_texto_sin_limites():
	assert limites.desde_texto_claude("Total cost: $0") is None
	assert limites.desde_texto_claude("") is None


def test_claude_lee_el_texto_si_no_hay_informe(monkeypatch):
	from app.services.agentes import claude, procesos

	salida = _salida({"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": TEXTO_CLAUDE}]}},
		{"type": "result", "num_turns": 0, "total_cost_usd": 0})
	monkeypatch.setattr(procesos, "ejecutar_y_leer", lambda orden, tiempo, entorno=None: (0, salida, ""))
	resultado = claude.ProveedorClaude(orden_base=["claude"]).leer_limites()
	assert [v.porcentaje for v in resultado.ventanas] == [9.0, 50.0, 0.0]
	assert resultado.detalle
