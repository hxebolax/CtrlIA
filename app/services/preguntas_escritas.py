"""Preguntas que el agente escribe en su respuesta en vez de hacerlas con su herramienta.

Si un turno termina esperando que contestes («¿Qué base de datos prefieres?»),
Ctrl+IA lo trata como una pregunta más: la sesión pasa a «Te necesita» y
Ctrl+Mayús+A abre el mismo diálogo de siempre; lo que respondas va como tu
siguiente mensaje. Idea de BlindPilot (``questions.py``):

1. Un bloque de código con la etiqueta ``questions`` y dentro el mismo JSON
   que AskUserQuestion (``[{question, header, options:[{label, description}],
   multiSelect}]``). Gana el último bloque válido.
2. Si no hay bloque, la última frase con «?» cerca del final (como mucho
   :data:`MAX_DESPUES` caracteres detrás, sin contar los bloques de código).
3. Se descartan los ofrecimientos de seguir («¿Quieres que ejecute las
   pruebas?», «¿Algo más?») salvo que planteen una alternativa con «o»: no
   esperan nada y la sesión no debe quedarse en «Te necesita».

Sin wx: se prueba sin ventanas. Nunca responde nada: solo prepara la pregunta.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.services.agentes.base import ClaseOpcion, OpcionPermiso, Pregunta, SolicitudPermiso, TipoSolicitud

#: Caracteres que puede haber detrás de la última «?» para tomarla como pregunta final.
MAX_DESPUES = 200
#: Largo máximo de la pregunta tomada del texto.
MAX_PREGUNTA = 600
#: Marca de las solicitudes que crea este módulo (no vienen del agente: se responden con un mensaje).
DATO_ESCRITA = "pregunta_escrita"

_BLOQUE_PREGUNTAS = re.compile(r"(?ms)^[ \t]*(```|~~~)[ \t]*questions[ \t]*\n(.*?)^[ \t]*\1[ \t]*$")
_BLOQUE = re.compile(r"(?ms)^[ \t]*(```|~~~)[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)")

#: Principios de ofrecimientos de seguir trabajando, en español y en inglés (en minúsculas, sin «¿»).
_OFRECIMIENTOS = (
	"quieres que", "te gustaría que", "deseas que", "prefieres que", "quiere que", "le gustaría que",
	"necesitas algo más", "algo más", "te ayudo con algo más", "puedo ayudarte en algo más", "puedo ayudarte con algo más",
	"hay algo más", "te parece que sigamos", "seguimos", "continúo", "sigo", "lo hago", "lo aplico", "procedo",
	"want me to", "would you like me to", "should i", "shall i", "do you want me to", "anything else",
	"is there anything else", "can i help with anything else", "let me know if",
)
#: Una alternativa explícita convierte el ofrecimiento en pregunta de verdad («¿Lo hago con A o con B?»).
_ALTERNATIVA = re.compile(r"\b(o|u|or)\b", re.IGNORECASE)


def _sin_bloques(texto: str) -> str:
	return _BLOQUE.sub("", texto)


def bloque_preguntas(texto: str) -> list[Pregunta]:
	"""Las preguntas del último bloque ``questions`` válido ([] si no hay)."""
	for coincidencia in reversed(list(_BLOQUE_PREGUNTAS.finditer(texto or ""))):
		try:
			datos = json.loads(coincidencia.group(2))
		except ValueError:
			continue
		if isinstance(datos, dict):
			datos = datos.get("questions") or [datos]
		preguntas = _preguntas_de_json(datos)
		if preguntas:
			return preguntas
	return []


def _preguntas_de_json(datos: Any) -> list[Pregunta]:
	if not isinstance(datos, list):
		return []
	preguntas: list[Pregunta] = []
	for indice, entrada in enumerate(datos[:8]):
		if not isinstance(entrada, dict):
			continue
		texto = str(entrada.get("question") or entrada.get("pregunta") or "").strip()
		if not texto:
			continue
		opciones: list[tuple[str, str]] = []
		vistas: list[str] = []
		for opcion in entrada.get("options") or entrada.get("opciones") or []:
			if isinstance(opcion, dict):
				etiqueta = str(opcion.get("label") or opcion.get("etiqueta") or "").strip()
				if etiqueta:
					opciones.append((etiqueta, str(opcion.get("description") or opcion.get("descripcion") or "")))
					vistas.append(str(opcion.get("preview") or ""))
			elif isinstance(opcion, str) and opcion.strip():
				opciones.append((opcion.strip(), ""))
				vistas.append("")
		preguntas.append(Pregunta(
			id=f"escrita-{indice}", texto=texto[:MAX_PREGUNTA], encabezado=str(entrada.get("header") or "")[:60],
			opciones=opciones[:20], multiple=bool(entrada.get("multiSelect") or entrada.get("multiple")),
			admite_otra=True, vistas_previas=vistas[:20] if any(vistas) else [],
		))
	return preguntas


def es_ofrecimiento(pregunta: str) -> bool:
	"""Si la pregunta solo ofrece seguir («¿Quieres que lo haga?») sin plantear una alternativa."""
	limpia = pregunta.strip().lstrip("¿¡ \t\"'«").lower()
	if not limpia.startswith(_OFRECIMIENTOS) and not any(f" {o}" in f" {limpia}" for o in _OFRECIMIENTOS[:4]):
		return False
	return not _ALTERNATIVA.search(limpia.split("?")[0])


def pregunta_final(texto: str) -> str:
	"""La pregunta con la que acaba el texto («» si no acaba preguntando o solo ofrece seguir)."""
	limpio = _sin_bloques(texto or "").rstrip()
	posicion = limpio.rfind("?")
	if posicion < 0 or len(limpio) - posicion - 1 > MAX_DESPUES:
		return ""
	inicio = posicion
	while inicio > 0 and limpio[inicio - 1] not in ".!?\n:":
		inicio -= 1
	# «¿» abre la pregunta en español: si está antes, se empieza ahí.
	apertura = limpio.rfind("¿", 0, posicion)
	if apertura >= 0 and limpio.rfind("\n", apertura, posicion) < 0:
		inicio = min(inicio, apertura)
	pregunta = limpio[inicio:posicion + 1].strip(" \t*_>-")
	if len(pregunta) < 4 or es_ofrecimiento(pregunta):
		return ""
	return pregunta[:MAX_PREGUNTA]


def solicitud_de(texto: str) -> SolicitudPermiso | None:
	"""La pregunta escrita del final de una respuesta, lista para el diálogo; ``None`` si no hay."""
	preguntas = bloque_preguntas(texto)
	if not preguntas:
		pregunta = pregunta_final(texto)
		if not pregunta:
			return None
		preguntas = [Pregunta(id="escrita-0", texto=pregunta, admite_otra=True)]
	titulo = preguntas[0].texto if len(preguntas) == 1 else "%d preguntas" % len(preguntas)
	return SolicitudPermiso(
		id="escrita-" + uuid.uuid4().hex[:12], tipo=TipoSolicitud.PREGUNTA, titulo=titulo,
		detalle="El agente ha terminado preguntándote esto en su respuesta. Lo que contestes le llegará como tu siguiente mensaje.",
		preguntas=preguntas, datos={DATO_ESCRITA: True}, contexto=texto[-4000:],
		opciones=[
			OpcionPermiso("responder", "Responder", ClaseOpcion.PERMITIR,
				"Envía lo que contestes como tu siguiente mensaje."),
			OpcionPermiso("no_responder", "No responder", ClaseOpcion.DENEGAR,
				"Cierra la pregunta sin enviar nada. Puedes escribirle lo que quieras en el cuadro de respuesta."),
		],
	)


def texto_respuesta(solicitud: SolicitudPermiso, respuestas: dict[str, list[str]], mensaje: str = "") -> str:
	"""El mensaje que se envía al agente con lo contestado («» si no se contestó nada)."""
	partes = []
	for pregunta in solicitud.preguntas:
		elegidas = [r for r in respuestas.get(pregunta.id, []) if str(r).strip()]
		if not elegidas:
			continue
		if len(solicitud.preguntas) == 1:
			partes.append(", ".join(elegidas))
		else:
			partes.append(f"{pregunta.texto}\n{', '.join(elegidas)}")
	if mensaje.strip():
		partes.append(mensaje.strip())
	return "\n\n".join(partes)
