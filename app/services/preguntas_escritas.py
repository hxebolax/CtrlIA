"""Preguntas que el agente escribe en su respuesta en vez de hacerlas con su herramienta.

Si un turno termina esperando que contestes («¿Qué base de datos prefieres?»),
Ctrl+IA lo trata como una pregunta más: la sesión pasa a «Te necesita» y
Ctrl+Mayús+A abre el mismo diálogo de siempre; lo que respondas va como tu
siguiente mensaje:

1. Un bloque de código con la etiqueta ``questions`` y dentro el mismo JSON
   que AskUserQuestion (``[{question, header, options:[{label, description}],
   multiSelect}]``). Gana el último bloque válido.
2. Si no hay bloque, la última frase con «?» cerca del final (como mucho
   :data:`MAX_DESPUES` caracteres detrás, sin contar los bloques de código ni
   la lista de opciones que la siga). Las opciones escritas en una lista
   (detrás, o delante de «¿Cuál prefieres?») se pueden elegir con las flechas,
   y varias preguntas en una lista dan una pregunta cada una
   (:func:`preguntas_del_final`).
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
#: Opciones que se toman como mucho de una lista escrita.
MAX_OPCIONES = 20

#: Elemento de lista: «1.», «2)», «a)», «-», «*», «+» o «•» seguido de espacio.
_ITEM = re.compile(r"^([ ]*)(?:[-*+•]|\d{1,2}[.)]|[a-zA-Z][)])[ \t]+(\S.*?)\s*$")
#: Preguntas que piden elegir: con ellas, una lista justo antes son sus opciones.
_ELECCION = re.compile(
	r"\b(cu[aá]l(es)?|qu[eé] opci[oó]n|prefier[eo]s?|elig[eo]s?|escog[eo]s?|elegimos|opci[oó]n|which|prefer|choose|"
	r"option|pick)\b", re.IGNORECASE)

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


def _item(linea: str) -> tuple[int, str] | None:
	"""(sangría, contenido) si la línea es un elemento de lista («1.», «2)», «a)», «-», «*», «•»)."""
	coincidencia = _ITEM.match(linea.expandtabs(4))
	if not coincidencia:
		return None
	return len(coincidencia.group(1)), coincidencia.group(2).strip()


def _opcion(contenido: str) -> tuple[str, str]:
	"""(etiqueta, descripción) de un elemento de lista que propone una opción."""
	negrita = re.match(r"^(?:\*\*|__)(.+?)(?:\*\*|__)\s*[:.\-—–]?\s*(.*)$", contenido)
	if negrita:
		etiqueta, descripcion = negrita.group(1), negrita.group(2)
	else:
		etiqueta, descripcion = contenido, ""
		for separador in (" — ", " – ", ": ", " - "):
			posicion = contenido.find(separador)
			if 0 < posicion <= 80:
				etiqueta, descripcion = contenido[:posicion], contenido[posicion + len(separador):]
				break
	limpia = etiqueta.replace("`", "").strip(" \t*_:.")
	return limpia[:200], descripcion.strip()[:600]


def _texto_pregunta(linea: str) -> str:
	"""La pregunta de una línea, desde su principio hasta su última «?» («» si no hay)."""
	posicion = linea.rfind("?")
	if posicion < 0:
		return ""
	inicio = posicion
	while inicio > 0 and linea[inicio - 1] not in ".!?:":
		inicio -= 1
	apertura = linea.rfind("¿", 0, posicion)
	if apertura >= 0:
		inicio = min(inicio, apertura)
	item = _item(linea[:posicion + 1]) if inicio == 0 else None
	pregunta = (item[1] if item else linea[inicio:posicion + 1]).strip(" \t*_>-")
	return pregunta[:MAX_PREGUNTA] if len(pregunta) >= 4 else ""


def _opciones_seguidas(lineas: list[str]) -> tuple[list[tuple[str, str]], int]:
	"""Opciones de una lista que empieza en ``lineas[0]`` y cuántas líneas ocupa (con huecos en blanco)."""
	opciones: list[tuple[str, str]] = []
	usadas = 0
	for numero, linea in enumerate(lineas):
		if not linea.strip():
			continue
		item = _item(linea)
		if item is None or "?" in item[1]:
			break
		opciones.append(_opcion(item[1]))
		usadas = numero + 1
	return opciones, usadas


def preguntas_del_final(texto: str) -> list[Pregunta]:
	"""Las preguntas con las que acaba el texto, con sus opciones si las escribe en una lista.

	- «¿Cuál prefieres?» seguida de una lista: cada elemento es una opción.
	- Una lista de opciones justo antes de «¿Cuál de ellas?», «¿Qué prefieres?»...
	- Varias preguntas en una lista («1. ¿Qué base de datos? 2. ¿Con pruebas?»):
	  una pregunta por elemento, cada una con las opciones que lleve debajo.

	Una pregunta sin opciones se contesta escribiendo. Los ofrecimientos de seguir
	sin opciones no cuentan (:func:`es_ofrecimiento`).
	"""
	limpio = _sin_bloques(texto or "").rstrip()
	lineas = limpio.split("\n")
	ultima = next((i for i in range(len(lineas) - 1, -1, -1) if "?" in lineas[i]), -1)
	if ultima < 0:
		return []
	# Lo que va detrás: opciones en lista y, como mucho, una coletilla corta («Dime y sigo.»).
	resto = lineas[ultima][lineas[ultima].rfind("?") + 1:].strip()
	despues, usadas = _opciones_seguidas(lineas[ultima + 1:])
	coletilla = "\n".join(lineas[ultima + 1 + usadas:]).strip()
	if len(resto) + len(coletilla) > MAX_DESPUES or len(despues) > MAX_OPCIONES:
		return []
	if len(despues) < 2:
		despues = []
		if usadas:  # Un único elemento detrás: se toma como texto, no como opción.
			if len("\n".join(lineas[ultima + 1:]).strip()) + len(resto) > MAX_DESPUES:
				return []

	# Varias preguntas en una lista: se sube por la lista mientras sigan sus elementos.
	if _item(lineas[ultima]) is not None:
		inicio = ultima
		for numero in range(ultima - 1, -1, -1):
			if lineas[numero].strip() and _item(lineas[numero]) is None:
				break
			inicio = numero
		indices = [i for i in range(inicio, ultima + 1) if _item(lineas[i]) is not None and "?" in lineas[i]]
		if len(indices) >= 2:
			sangria = min(_item(lineas[i])[0] for i in indices)  # type: ignore[index]
			indices = [i for i in indices if _item(lineas[i])[0] == sangria]  # type: ignore[index]
		if len(indices) >= 2:
			preguntas: list[Pregunta] = []
			for orden, indice in enumerate(indices[:8]):
				texto_pregunta = _texto_pregunta(lineas[indice])
				if not texto_pregunta:
					continue
				if indice == ultima:
					opciones = despues
				else:
					fin = indices[orden + 1] if orden + 1 < len(indices) else ultima
					opciones, _usadas = _opciones_seguidas(lineas[indice + 1:fin])
					if len(opciones) < 2:
						opciones = []
				preguntas.append(Pregunta(id=f"escrita-{len(preguntas)}", texto=texto_pregunta,
					opciones=opciones[:MAX_OPCIONES], admite_otra=True))
			if len(preguntas) >= 2:
				return preguntas

	pregunta = _texto_pregunta(lineas[ultima])
	if not pregunta:
		return []
	opciones = despues
	if not opciones and _ELECCION.search(pregunta):
		# Opciones escritas justo antes de «¿Cuál prefieres?».
		fin = ultima
		while fin > 0 and not lineas[fin - 1].strip():
			fin -= 1
		inicio = fin
		while inicio > 0 and _item(lineas[inicio - 1]) is not None and "?" not in lineas[inicio - 1]:
			inicio -= 1
		candidatas = [_opcion(_item(linea)[1]) for linea in lineas[inicio:fin]]  # type: ignore[index]
		if 2 <= len(candidatas) <= MAX_OPCIONES:
			opciones = candidatas
	if not opciones and es_ofrecimiento(pregunta):
		return []
	return [Pregunta(id="escrita-0", texto=pregunta, opciones=opciones, admite_otra=True)]


def solicitud_de(texto: str) -> SolicitudPermiso | None:
	"""La pregunta escrita del final de una respuesta, lista para el diálogo; ``None`` si no hay."""
	preguntas = bloque_preguntas(texto) or preguntas_del_final(texto)
	if not preguntas:
		return None
	titulo = preguntas[0].texto if len(preguntas) == 1 else "El agente te hace %d preguntas" % len(preguntas)
	detalle = "El agente ha terminado preguntándote esto en su respuesta. Lo que contestes le llegará como tu siguiente mensaje."
	if len(preguntas) > 1:
		detalle += "\n\n" + "\n".join(f"{n}. {p.texto}" for n, p in enumerate(preguntas, start=1))
	return SolicitudPermiso(
		id="escrita-" + uuid.uuid4().hex[:12], tipo=TipoSolicitud.PREGUNTA, titulo=titulo,
		detalle=detalle,
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
