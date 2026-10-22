"""Límites de uso de las cuentas de los agentes: la ventana de 5 horas y la semanal.

Cada proveedor que sabe leerlos implementa ``Proveedor.leer_limites()`` y
devuelve un :class:`LimitesCuenta`. Se leen sin enviar mensajes y sin gastar
cuota. Si no hay conexión, algunos agentes dejan en disco el último dato
conocido; entonces ``en_vivo`` es falso y se dice de cuándo es.

Las ventanas se reconocen por su duración, no por su posición: Codex ha
llegado a mandar solo la semanal en el lugar de la «primaria».
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MINUTOS_5_HORAS = 300
MINUTOS_SEMANA = 10080


@dataclass(slots=True)
class VentanaLimite:
	"""Una ventana de uso: cuánto llevas gastado y cuándo se reinicia.

	:param porcentaje: de 0 a 100.
	:param minutos: duración de la ventana (300 = 5 horas, 10080 = semana), si se sabe.
	:param reinicio: segundos desde 1970 (UTC) en que vuelve a cero, si se sabe.
	:param nombre: nombre propio, si el servicio da uno que no es «5 horas» ni «semanal».
	:param grupo: a qué modelos se aplica, si la cuenta tiene varios grupos («Modelos Gemini»).
	"""

	porcentaje: float
	minutos: int | None = None
	reinicio: float | None = None
	nombre: str = ""
	grupo: str = ""

	def caducada(self, ahora: float | None = None) -> bool:
		"""Si ya pasó la hora de reinicio (el porcentaje guardado ya no vale)."""
		return self.reinicio is not None and self.reinicio <= (ahora if ahora is not None else time.time())


@dataclass(slots=True)
class LimitesCuenta:
	"""Los límites de una cuenta en un momento dado."""

	ventanas: list[VentanaLimite] = field(default_factory=list)
	plan: str = ""
	#: ``True`` si se acaban de preguntar al servicio; ``False`` si son el último dato guardado en disco.
	en_vivo: bool = True
	#: Cuándo se obtuvieron (segundos desde 1970).
	fecha: float = field(default_factory=time.time)
	#: Algo que conviene decir (por ejemplo, que la cuenta usa créditos o que falta una ventana).
	nota: str = ""
	#: Más información del agente, ya en español (en Claude, qué está gastando tu límite).
	detalle: list[str] = field(default_factory=list)


class ErrorLimites(Exception):
	"""No se han podido leer los límites; el mensaje es para la persona."""


# ── Textos ───────────────────────────────────────────────────────────────────


def nombre_ventana(ventana: VentanaLimite) -> str:
	"""«Límite de 5 horas», «Límite semanal», «Límite de 1 día»..."""
	if ventana.nombre:
		return ventana.nombre
	minutos = ventana.minutos
	if minutos == MINUTOS_5_HORAS:
		return "Límite de 5 horas"
	if minutos == MINUTOS_SEMANA:
		return "Límite semanal"
	if not minutos:
		return "Límite"
	if minutos % 1440 == 0:
		dias = minutos // 1440
		return "Límite de 1 día" if dias == 1 else f"Límite de {dias} días"
	if minutos % 60 == 0:
		horas = minutos // 60
		return "Límite de 1 hora" if horas == 1 else f"Límite de {horas} horas"
	return f"Límite de {minutos} minutos"


def texto_reinicio(reinicio: float | None, ahora: float | None = None) -> str:
	"""«se reinicia en 2 h 15 min (a las 18:40)» o «se reinicia el lunes 20/10 a las 9:05»."""
	if reinicio is None:
		return ""
	ahora = ahora if ahora is not None else time.time()
	falta = int(reinicio - ahora)
	if falta <= 0:
		return "ya se ha reiniciado"
	cuando = _dt.datetime.fromtimestamp(reinicio)
	hoy = _dt.datetime.fromtimestamp(ahora).date()
	horas, resto = divmod(falta // 60, 60)
	if horas < 24:
		duracion = f"{horas} h {resto} min" if horas else f"{max(resto, 1)} min"
		dia = "" if cuando.date() == hoy else " de mañana"
		return f"se reinicia en {duracion} (a las {cuando:%H:%M}{dia})"
	dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
	return f"se reinicia el {dias[cuando.weekday()]} {cuando:%d/%m} a las {cuando:%H:%M}"


def texto_ventana(ventana: VentanaLimite, ahora: float | None = None) -> str:
	"""«Límite de 5 horas: 37 % usado, se reinicia en 2 h 15 min (a las 18:40).»

	Con grupo: «Modelos Gemini, límite de 5 horas: ...».
	"""
	nombre = nombre_ventana(ventana)
	if ventana.grupo:
		nombre = f"{ventana.grupo}, {nombre[:1].lower()}{nombre[1:]}" if not ventana.nombre else f"{ventana.grupo}, {nombre}"
	if ventana.caducada(ahora):
		return f"{nombre}: 0 % usado (ya se ha reiniciado)."
	porcentaje = round(ventana.porcentaje)
	reinicio = texto_reinicio(ventana.reinicio, ahora)
	return f"{nombre}: {porcentaje} % usado" + (f", {reinicio}" if reinicio else "") + "."


def lineas(limites: LimitesCuenta, ahora: float | None = None) -> list[str]:
	"""Las líneas que se leen de una cuenta: por grupos, en su orden, y en cada uno la de 5 horas antes que la semanal."""
	ahora = ahora if ahora is not None else time.time()
	orden = {MINUTOS_5_HORAS: 0, MINUTOS_SEMANA: 1}
	grupos = list(dict.fromkeys(v.grupo for v in limites.ventanas))
	ventanas = sorted(limites.ventanas, key=lambda v: (grupos.index(v.grupo), bool(v.nombre), orden.get(v.minutos or 0, 2),
		v.minutos or 0))
	resultado = [texto_ventana(v, ahora) for v in ventanas]
	minutos = {v.minutos for v in limites.ventanas}
	if limites.ventanas and MINUTOS_5_HORAS not in minutos:
		resultado.append("El servicio no informa de un límite de 5 horas para tu cuenta.")
	if limites.plan:
		resultado.append(f"Plan: {limites.plan}.")
	if not limites.en_vivo:
		cuando = _dt.datetime.fromtimestamp(limites.fecha)
		resultado.append(f"Es el último dato guardado, del {cuando:%d/%m/%Y a las %H:%M}: no se ha podido preguntar ahora.")
	if limites.nota:
		resultado.append(limites.nota)
	return resultado


@dataclass(slots=True)
class ResultadoAgente:
	"""Lo que se sabe de los límites de un agente para el documento."""

	nombre: str
	limites: LimitesCuenta | None = None
	error: str = ""
	#: Lo último que dijo el agente durante una conversación (si no se pudo preguntar ahora).
	durante_sesion: list[str] = field(default_factory=list)


#: Agentes que no permiten preguntar sus límites, con el motivo (se explica en el documento).
SIN_CONSULTA = (
	("Gemini CLI", "su cuota es diaria (peticiones por día de tu licencia de Gemini Code Assist) y solo se puede ver "
		"dentro de Gemini, con /stats en una consola."),
	("Ollama", "los modelos de tu equipo no tienen límites. Los modelos en la nube de Ollama se consultan en tu cuenta "
		"de ollama.com."),
)


def documento(resultados: Iterable[ResultadoAgente], ahora: float | None = None) -> str:
	"""El documento «Límites de uso de tus cuentas», con un encabezado por agente."""
	partes = ["# Límites de uso de tus cuentas", ""]
	for resultado in resultados:
		partes += [f"## {resultado.nombre}", ""]
		if resultado.limites is not None:
			partes += [f"- {linea}" for linea in lineas(resultado.limites, ahora)]
			if resultado.limites.detalle:
				partes += ["", f"### Qué está gastando tu límite de {resultado.nombre}", ""]
				partes += [f"  - {linea.strip()}" if linea.startswith("  ") else f"- {linea}" for linea in resultado.limites.detalle]
		else:
			if resultado.error:
				partes.append(f"- {resultado.error}")
			if resultado.durante_sesion:
				partes.append("- Lo último que dijo durante una sesión de hoy:")
				partes += [f"  - {linea}" for linea in resultado.durante_sesion]
		partes.append("")
	partes += ["## Agentes sin consulta de límites", ""]
	partes += [f"- {nombre}: {motivo}" for nombre, motivo in SIN_CONSULTA]
	partes += ["", "Preguntar los límites no envía ningún mensaje ni gasta cuota."]
	return "\n".join(partes)


# ── Codex ────────────────────────────────────────────────────────────────────


def _numero(valor: Any) -> float | None:
	if isinstance(valor, bool) or not isinstance(valor, (int, float)):
		return None
	return float(valor)


def _ventana_codex(datos: Any, nombre: str = "") -> VentanaLimite | None:
	"""Ventana de Codex en cualquiera de sus dos formas: la del app-server (camelCase) o la de los rollout."""
	if not isinstance(datos, dict):
		return None
	porcentaje = _numero(datos.get("usedPercent", datos.get("used_percent")))
	if porcentaje is None:
		return None
	minutos = _numero(datos.get("windowDurationMins", datos.get("window_minutes")))
	reinicio = _numero(datos.get("resetsAt", datos.get("resets_at")))
	return VentanaLimite(max(0.0, min(porcentaje, 100.0)), int(minutos) if minutos else None, reinicio, nombre)


def _cubo_codex(cubo: Any, grupo: str = "") -> list[VentanaLimite]:
	if not isinstance(cubo, dict):
		return []
	ventanas = []
	for clave in ("primary", "secondary"):
		ventana = _ventana_codex(cubo.get(clave))
		if ventana is not None:
			ventana.grupo = grupo
			ventanas.append(ventana)
	return ventanas


def _nota_creditos(cubo: dict[str, Any]) -> str:
	creditos = cubo.get("credits")
	if not isinstance(creditos, dict):
		return ""
	if creditos.get("unlimited"):
		return "Tu cuenta tiene créditos ilimitados."
	if creditos.get("hasCredits", creditos.get("has_credits")):
		saldo = creditos.get("balance")
		return f"Tienes créditos para cuando llegues al límite (saldo: {saldo})." if saldo else "Tienes créditos para cuando llegues al límite."
	return ""


def desde_codex(respuesta: Any, en_vivo: bool = True, fecha: float | None = None) -> LimitesCuenta | None:
	"""Convierte la respuesta de ``account/rateLimits/read`` (o un ``rate_limits`` de rollout)."""
	if not isinstance(respuesta, dict):
		return None
	principal = respuesta.get("rateLimits") if "rateLimits" in respuesta else respuesta
	if not isinstance(principal, dict):
		return None
	ventanas = _cubo_codex(principal)
	if not ventanas:
		return None
	principal_id = principal.get("limitId", principal.get("limit_id")) or "codex"
	otros = respuesta.get("rateLimitsByLimitId")
	if isinstance(otros, dict):
		for identificador, cubo in otros.items():
			if identificador == principal_id or not isinstance(cubo, dict):
				continue
			nombre = str(cubo.get("limitName") or cubo.get("normalModelSlug") or identificador)
			ventanas += _cubo_codex(cubo, nombre)
	plan = str(principal.get("planType", principal.get("plan_type")) or "")
	return LimitesCuenta(ventanas, plan=plan, en_vivo=en_vivo, fecha=fecha if fecha is not None else time.time(),
		nota=_nota_creditos(principal))


def _rollouts(raiz: Path) -> Iterable[Path]:
	"""Los ``rollout-*.jsonl`` de Codex, del más reciente al más antiguo."""
	archivos = []
	for carpeta in (raiz / "sessions", raiz / "archived_sessions"):
		try:
			for ruta in carpeta.rglob("rollout-*.jsonl"):
				try:
					archivos.append((ruta.stat().st_mtime, ruta))
				except OSError:
					log.debug("No se pudo consultar %s", ruta, exc_info=True)
		except OSError:
			log.debug("No se pudo recorrer %s", carpeta, exc_info=True)
	return [ruta for _fecha, ruta in sorted(archivos, reverse=True)]


def ultimo_codex_en_disco(raiz: Path, max_archivos: int = 5, max_bytes: int = 4_000_000) -> LimitesCuenta | None:
	"""El último ``rate_limits`` que Codex guardó en sus sesiones (sin red). Solo lectura.

	Se mira el final de los archivos más recientes, que es donde está el dato más nuevo.
	"""
	for ruta in list(_rollouts(raiz))[:max_archivos]:
		try:
			with ruta.open("rb") as archivo:
				archivo.seek(0, 2)
				tamano = archivo.tell()
				archivo.seek(max(0, tamano - max_bytes))
				contenido = archivo.read().decode("utf-8", errors="replace")
		except OSError:
			log.debug("No se pudo leer %s", ruta, exc_info=True)
			continue
		for linea in reversed(contenido.splitlines()):
			if '"rate_limits"' not in linea:
				continue
			try:
				dato = json.loads(linea)
			except ValueError:
				continue
			carga = dato.get("payload") if isinstance(dato, dict) else None
			limites = carga.get("rate_limits") if isinstance(carga, dict) else None
			fecha = _fecha_iso(dato.get("timestamp")) if isinstance(dato, dict) else None
			resultado = desde_codex(limites, en_vivo=False, fecha=fecha or ruta.stat().st_mtime)
			if resultado is not None:
				return resultado
	return None


# ── Claude Code ──────────────────────────────────────────────────────────────

_TIPOS_CLAUDE = {"session": MINUTOS_5_HORAS, "weekly_all": MINUTOS_SEMANA, "weekly_scoped": MINUTOS_SEMANA}


def buscar_clave(datos: Any, clave: str) -> Any:
	"""El primer valor de ``clave`` en un JSON anidado (o ``None``)."""
	if isinstance(datos, dict):
		if clave in datos:
			return datos[clave]
		valores: Iterable[Any] = datos.values()
	elif isinstance(datos, list):
		valores = datos
	else:
		return None
	for valor in valores:
		encontrado = buscar_clave(valor, clave)
		if encontrado is not None:
			return encontrado
	return None


def desde_claude(informe: Any) -> LimitesCuenta | None:
	"""Convierte el ``usage_report`` que da Claude Code al recibir ``/usage``.

	``rate_limits.limits`` trae la sesión de 5 horas (``session``), la semana
	de todos los modelos (``weekly_all``) y la de cada modelo (``weekly_scoped``,
	con ``scope.model.display_name``); ``percent`` va de 0 a 100.
	"""
	if not isinstance(informe, dict):
		return None
	limites_claude = informe.get("rate_limits")
	filas = limites_claude.get("limits") if isinstance(limites_claude, dict) else None
	if not isinstance(filas, list):
		return None
	ventanas = []
	for fila in filas:
		if not isinstance(fila, dict):
			continue
		porcentaje = _numero(fila.get("percent"))
		if porcentaje is None:
			continue
		tipo = str(fila.get("kind") or "")
		minutos = _TIPOS_CLAUDE.get(tipo) or (MINUTOS_SEMANA if fila.get("group") == "weekly" else None)
		ventana = VentanaLimite(max(0.0, min(porcentaje, 100.0)), minutos, _fecha_iso(fila.get("resets_at")))
		if tipo == "weekly_scoped":
			modelo = buscar_clave(fila.get("scope"), "display_name")
			ventana.grupo = f"Solo {modelo}" if modelo else "Solo un modelo"
		ventanas.append(ventana)
	nota = ""
	extra = limites_claude.get("extra_usage") if isinstance(limites_claude, dict) else None
	if isinstance(extra, dict) and extra.get("is_enabled"):
		nota = "Tienes activado el uso adicional de pago cuando llegues al límite."
	return LimitesCuenta(ventanas, nota=nota) if ventanas else None


# El texto de «/usage» de Claude Code (en inglés) cuando no llega el ``usage_report``.

_MESES = {nombre: numero for numero, nombre in enumerate(
	("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_RE_LIMITE_CLAUDE = re.compile(
	r"^current (session|week)(?: \((?P<ambito>[^)]*)\))?\s*:\s*(?P<porcentaje>\d+(?:[.,]\d+)?)\s*%\s*used"
	r"(?:\s*[·•|-]\s*resets?\s+(?P<reinicio>.+?))?\s*$", re.IGNORECASE)
_RE_REINICIO_CLAUDE = re.compile(
	r"^(?:(?P<mes>[a-z]{3})[a-z]*\.?\s+(?P<dia>\d{1,2}),?\s*)?(?:at\s+)?"
	r"(?:(?P<hora>\d{1,2})(?::(?P<minuto>\d{2}))?\s*(?P<ampm>am|pm)?)?\s*(?:\((?P<zona>[^)]+)\))?$", re.IGNORECASE)
_RE_PERIODO_CLAUDE = re.compile(r"^last (\d+)\s*(h|d)\b(.*)$", re.IGNORECASE)
_TOPS_CLAUDE = {"subagents": "Subagentes", "mcp servers": "Servidores MCP", "skills": "Habilidades", "tools": "Herramientas",
	"models": "Modelos", "plugins": "Complementos", "commands": "Comandos", "projects": "Proyectos"}
_FRASES_CLAUDE = (
	(re.compile(r"^(\d+)% of your usage came from subagent-heavy sessions$", re.I),
		"El {0} % de tu uso vino de sesiones con muchos subagentes."),
	(re.compile(r"^(\d+)% of your usage was at >\s*(\d+)k context$", re.I),
		"El {0} % de tu uso fue con más de {1} 000 unidades de texto de contexto."),
	(re.compile(r"^(\d+)% of your usage came from sessions active for (\d+)\+ hours$", re.I),
		"El {0} % de tu uso vino de sesiones abiertas durante {1} horas o más."),
	(re.compile(r"^(\d+)% of your usage came from (.+)$", re.I), "El {0} % de tu uso vino de: {1}."),
	(re.compile(r"^approximate, based on local sessions on this machine.*$", re.I),
		"Es aproximado: solo cuenta las sesiones de este equipo, no las de otros dispositivos ni las de claude.ai. "
		"Cada dato es independiente; no es un reparto del total."),
	(re.compile(r"^you are currently using your subscription.*$", re.I), "Claude Code está usando tu suscripción."),
)


def _reinicio_claude(texto: str, ahora: float) -> float | None:
	"""«Oct 19, 4:30pm (Europe/Paris)», «Oct 21, 1am», «4:30pm»... a segundos desde 1970.

	Claude Code escribe la hora en la zona del equipo: si no se conoce la zona
	que dice, se toma la hora local.
	"""
	coincidencia = _RE_REINICIO_CLAUDE.match(texto.strip())
	if not coincidencia or not (coincidencia["mes"] or coincidencia["hora"]):
		return None
	zona = None
	if coincidencia["zona"]:
		try:
			from zoneinfo import ZoneInfo

			zona = ZoneInfo(coincidencia["zona"].strip())
		except Exception:  # noqa: BLE001 - sin base de zonas (Windows sin tzdata): la hora local
			zona = None
	referencia = _dt.datetime.fromtimestamp(ahora, zona)
	hora = int(coincidencia["hora"] or 0)
	if coincidencia["ampm"]:
		hora = hora % 12 + (12 if coincidencia["ampm"].lower() == "pm" else 0)
	minuto = int(coincidencia["minuto"] or 0)
	try:
		if coincidencia["mes"]:
			mes = _MESES.get(coincidencia["mes"].lower())
			if mes is None:
				return None
			fecha = referencia.replace(month=mes, day=int(coincidencia["dia"]), hour=hora, minute=minuto, second=0,
				microsecond=0)
			if fecha.timestamp() < ahora - 2 * 86400:
				fecha = fecha.replace(year=fecha.year + 1)  # «Jan 2» visto en diciembre
		else:
			fecha = referencia.replace(hour=hora, minute=minuto, second=0, microsecond=0)
			if fecha.timestamp() < ahora:
				fecha += _dt.timedelta(days=1)
	except ValueError:
		return None
	return fecha.timestamp()


def _porcentajes(texto: str) -> str:
	return re.sub(r"(\d)%", r"\1 %", texto)


def _traducir_linea_claude(linea: str) -> str | None:
	"""Una línea del bloque de consumo en español, o ``None`` si no se reconoce."""
	periodo = _RE_PERIODO_CLAUDE.match(linea)
	if periodo:
		cantidad, unidad, resto = int(periodo[1]), periodo[2].lower(), periodo[3]
		if unidad == "h":
			cuando = f"Últimas {cantidad} horas"
		else:
			cuando = "Último día" if cantidad == 1 else f"Últimos {cantidad} días"
		peticiones = re.search(r"([\d.,]+)\s+requests?", resto, re.I)
		sesiones = re.search(r"([\d.,]+)\s+sessions?", resto, re.I)
		datos = []
		if peticiones:
			datos.append(f"{peticiones[1].replace(',', '.')} peticiones")
		if sesiones:
			numero = sesiones[1].replace(",", ".")
			datos.append(f"{numero} sesión" if numero == "1" else f"{numero} sesiones")
		return f"{cuando}: " + " en ".join(datos) + "." if datos else f"{cuando}:"
	tops = re.match(r"^top (.+?):\s*(.+)$", linea, re.I)
	if tops:
		nombre = _TOPS_CLAUDE.get(tops[1].strip().lower())
		if nombre:
			return f"{nombre} que más gastan: {_porcentajes(tops[2].strip())}."
	for patron, plantilla in _FRASES_CLAUDE:
		coincidencia = patron.match(linea)
		if coincidencia:
			return plantilla.format(*coincidencia.groups())
	return None


def desde_texto_claude(texto: str, ahora: float | None = None) -> LimitesCuenta | None:
	"""Convierte el texto en inglés de ``/usage`` de Claude Code en ventanas y un detalle en español.

	Las líneas «Current session» y «Current week (...)» son los límites; el
	bloque «What's contributing...» se traduce frase a frase, y lo que no se
	reconoce se deja tal cual, avisando de que va en inglés.
	"""
	ahora = ahora if ahora is not None else time.time()
	ventanas: list[VentanaLimite] = []
	detalle: list[str] = []
	notas: list[str] = []
	en_bloque = False
	for bruta in texto.splitlines():
		linea = bruta.strip()
		if not linea:
			continue
		limite = _RE_LIMITE_CLAUDE.match(linea)
		if limite:
			ambito = (limite["ambito"] or "").strip()
			ventana = VentanaLimite(max(0.0, min(float(limite["porcentaje"].replace(",", ".")), 100.0)),
				MINUTOS_5_HORAS if limite.group(1).lower() == "session" else MINUTOS_SEMANA,
				_reinicio_claude(limite["reinicio"], ahora) if limite["reinicio"] else None)
			if ambito and ambito.lower() not in ("all models", "all"):
				ventana.grupo = f"Solo {ambito}"
			ventanas.append(ventana)
			continue
		if linea.lower().startswith("what's contributing") or linea.lower().startswith("what is contributing"):
			en_bloque = True
			continue
		traducida = _traducir_linea_claude(linea)
		destino = detalle if en_bloque else notas
		if traducida is not None:
			destino.append(("  " + traducida) if en_bloque and bruta.startswith("  ") else traducida)
		else:
			destino.append(("  " if en_bloque and bruta.startswith("  ") else "") + f"(En inglés) {linea}")
	if not ventanas:
		return None
	return LimitesCuenta(ventanas, nota=" ".join(notas), detalle=detalle, fecha=ahora)


# ── Antigravity ──────────────────────────────────────────────────────────────

#: Nombres de grupo que da Antigravity, en español.
_GRUPOS_ANTIGRAVITY = {"gemini models": "Modelos Gemini", "claude and gpt models": "Modelos Claude y GPT"}
_VENTANAS_ANTIGRAVITY = {"5h": MINUTOS_5_HORAS, "weekly": MINUTOS_SEMANA, "daily": 1440}


def desde_antigravity(salida: Any) -> LimitesCuenta | None:
	"""Convierte la salida JSON de ``agy -p /usage --output-format json``.

	Cada grupo de modelos tiene su límite de 5 horas y su semanal; algunos
	modelos tienen además el suyo («Gemini 4 Argon»). Antigravity da lo que
	queda (``remaining_fraction``); aquí se guarda lo usado.
	"""
	if not isinstance(salida, dict):
		return None
	orden = salida.get("command")
	datos = orden.get("data") if isinstance(orden, dict) else None
	grupos = datos.get("groups") if isinstance(datos, dict) else None
	if not isinstance(grupos, list):
		return None
	ventanas = []
	for grupo in grupos:
		if not isinstance(grupo, dict):
			continue
		nombre_grupo = str(grupo.get("name") or "")
		nombre_grupo = _GRUPOS_ANTIGRAVITY.get(nombre_grupo.casefold(), nombre_grupo)
		for cubo in grupo.get("buckets") or []:
			if not isinstance(cubo, dict):
				continue
			queda = _numero(cubo.get("remaining_fraction"))
			if queda is None:
				continue
			minutos = _VENTANAS_ANTIGRAVITY.get(str(cubo.get("window") or ""))
			ventana = VentanaLimite(round(max(0.0, min(1.0 - queda, 1.0)) * 100, 1), minutos,
				_fecha_iso(cubo.get("reset_time")), grupo=nombre_grupo)
			if minutos is None:
				ventana.nombre = str(cubo.get("name") or "Límite")
			ventanas.append(ventana)
	return LimitesCuenta(ventanas) if ventanas else None


def _fecha_iso(texto: Any) -> float | None:
	if not isinstance(texto, str) or not texto:
		return None
	try:
		return _dt.datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp()
	except ValueError:
		return None
