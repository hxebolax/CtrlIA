"""Utilidades compartidas por toda la interfaz de Ctrl+IA.

- ``_``: traducción con gettext si está instalada; si no, el texto tal cual.
  El español es el idioma del código (no necesita catálogo).
- Fechas legibles para el lector de pantalla («hace 5 minutos», «ayer a las 18:30»).
- Nombres visibles de agentes y estados.
- ``markdown_seguro``: prepara el texto de un agente para verlo con formato
  sin dejar pasar HTML crudo (un mensaje no puede ejecutar código en el visor).
"""

from __future__ import annotations

import builtins
import datetime as dt
import re
import time


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) and traducir is not _ else texto


#: Nombre corto de cada agente para listas y anuncios.
NOMBRES_AGENTE = {
	"claude": "Claude",
	"codex": "Codex",
	"antigravity": "Antigravity",
	"gemini": "Gemini",
	"claude-acp": "Claude (ACP)",
	"codex-acp": "Codex (ACP)",
	"antigravity-acp": "Antigravity (ACP)",
	"ollama": "Ollama",
	# Otros agentes ACP conocidos (app/services/agentes/acp_conocidos.py).
	"opencode": "OpenCode",
	"goose": "Goose",
	"copilot": "Copilot",
	"qwen": "Qwen Code",
	"kilo": "Kilo Code",
	"cline": "Cline",
}

#: Texto visible de cada estado de sesión.
NOMBRES_ESTADO = {
	"inactiva": "Inactiva",
	"trabajando": "Trabajando",
	"esperando": "Te necesita",
	"segundo_plano": "Trabajo en segundo plano",
	"error": "Error",
	"cerrada": "Cerrada",
	"desconocido": "Estado desconocido",
}

_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
	"octubre", "noviembre", "diciembre")


def nombre_agente(identificador: str) -> str:
	return NOMBRES_AGENTE.get(identificador, identificador.capitalize())


def nombre_estado(estado: str) -> str:
	return _(NOMBRES_ESTADO.get(str(estado), str(estado)))


def fecha_relativa(segundos: float, ahora: float | None = None) -> str:
	"""«hace un momento», «hace 5 minutos», «hoy a las 10:15», «ayer a las 18:30», «3 de marzo»."""
	if not segundos:
		return _("Sin fecha")
	ahora = ahora if ahora is not None else time.time()
	diferencia = ahora - segundos
	momento = dt.datetime.fromtimestamp(segundos)
	hoy = dt.datetime.fromtimestamp(ahora).date()
	if 0 <= diferencia < 60:
		return _("hace un momento")
	if 0 <= diferencia < 3600:
		minutos = int(diferencia // 60)
		return _("hace 1 minuto") if minutos == 1 else _("hace {n} minutos").format(n=minutos)
	hora = momento.strftime("%H:%M")
	if momento.date() == hoy:
		return _("hoy a las {hora}").format(hora=hora)
	if momento.date() == hoy - dt.timedelta(days=1):
		return _("ayer a las {hora}").format(hora=hora)
	if momento.year == hoy.year:
		return _("{dia} de {mes} a las {hora}").format(dia=momento.day, mes=_(_MESES[momento.month - 1]), hora=hora)
	return _("{dia} de {mes} de {anio}").format(dia=momento.day, mes=_(_MESES[momento.month - 1]), anio=momento.year)


def hora_corta(segundos: float) -> str:
	"""Hora del mensaje: «10:15» si es de hoy; si no, fecha relativa."""
	if not segundos:
		return ""
	momento = dt.datetime.fromtimestamp(segundos)
	if momento.date() == dt.date.today():
		return momento.strftime("%H:%M")
	return fecha_relativa(segundos)


def primera_linea(texto: str, maximo: int = 250) -> str:
	"""Resumen de una línea para la columna de una lista."""
	limpio = " ".join((texto or "").split())
	return limpio if len(limpio) <= maximo else limpio[: maximo - 1] + "…"


def duracion(segundos: float) -> str:
	"""«45 segundos», «3 minutos», «1 hora y 5 minutos»."""
	segundos = int(max(0, segundos))
	if segundos < 60:
		return _("{n} segundos").format(n=segundos) if segundos != 1 else _("1 segundo")
	minutos, segundos = divmod(segundos, 60)
	if minutos < 60:
		return _("{n} minutos").format(n=minutos) if minutos != 1 else _("1 minuto")
	horas, minutos = divmod(minutos, 60)
	texto_horas = _("1 hora") if horas == 1 else _("{n} horas").format(n=horas)
	return texto_horas if not minutos else _("{h} y {m} minutos").format(h=texto_horas, m=minutos)


_RE_BLOQUE = re.compile(r"(```.*?```|`[^`\n]+`)", re.DOTALL)


def markdown_seguro(texto: str) -> str:
	"""Escapa el HTML que haya fuera del código para que Markdown no lo interprete.

	Dentro de los bloques y del código en línea no hace falta: Markdown ya
	los muestra como texto. Fuera, un ``<script>`` o un ``<img onerror>`` de
	un mensaje se ejecutaría en el visor; escapado, se lee como texto.
	"""
	partes = _RE_BLOQUE.split(texto or "")
	# Solo «<» y «&»: escapar «>» rompería las citas de Markdown («> texto»).
	return "".join(
		parte if indice % 2 else parte.replace("&", "&amp;").replace("<", "&lt;") for indice, parte in enumerate(partes)
	)
