"""Enviar la respuesta de un agente a otra sesión (AIChat #248), sin wx.

Una respuesta de una sesión va a otra sesión de Ctrl+IA, de cualquier agente,
para que un agente revise o conteste el trabajo de otro. Lleva delante una
línea que dice qué agente la escribió y en qué sesión: así el agente que la
recibe no toma las palabras de otro agente como instrucciones de la persona.
La persona lee y puede cambiar el mensaje entero antes de enviarlo, y va como
un mensaje normal: nunca responde una pregunta ni aprueba una herramienta, que
siguen llegando a la persona en cada sesión.

También puede ir a una sesión nueva (AIChat #379) y arrancar con ella una
conversación automática entre las dos: aquí está lo que no depende de la
ventana, como por qué no se puede empezar o qué parte del mensaje es la
respuesta del otro agente y qué parte son palabras de la persona.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import NamedTuple

from app.services.gestor import FilaSesion, MensajeVista

#: Lo que se puede reenviar: lo que dijo un agente o el plan que propuso. Los
#: errores, avisos y salidas de herramientas son de esa sesión.
ROLES_REENVIABLES = ("asistente", "plan")

#: Codex rechaza los mensajes más largos; de los demás no se conoce un límite.
LIMITES = {"codex": 32000}

#: Lo que separa la respuesta del otro agente de las palabras de la persona.
ANADE_LA_PERSONA = "La persona añade:"


class Problema(NamedTuple):
	"""Por qué no se puede arrancar una conversación automática.

	``corto`` va en la etiqueta de la casilla, sin títulos de sesión (así cabe y no
	trae ningún «&»); ``completo`` se dice al pulsar «Enviar».
	"""

	corto: str
	completo: str


def mensaje_a_reenviar(mensajes: Sequence[MensajeVista], elegido: MensajeVista | None = None) -> MensajeVista | None:
	"""El mensaje elegido si es del agente; si no, la última respuesta del agente."""
	if elegido is not None and elegido.rol in ROLES_REENVIABLES and elegido.texto.strip():
		return elegido
	for mensaje in reversed(mensajes):
		if mensaje.rol in ROLES_REENVIABLES and mensaje.texto.strip():
			return mensaje
	return None


def etiqueta(agente: str, titulo: str, automatico: bool = False) -> str:
	"""Primera línea del mensaje reenviado: de quién es, quién lo reenvía y de qué sesión.

	:param automatico: lo pasa Ctrl+IA en una conversación automática entre sesiones, no la persona.
	"""
	titulo = " ".join((titulo or "").split()) or "Sin título"
	if automatico:
		return (f"De {agente}, otro agente de IA; te lo pasa Ctrl+IA en la conversación automática desde su sesión "
			f"«{titulo}» (no lo ha escrito la persona):")
	return f"De {agente}, otro agente de IA; te lo reenvía la persona desde su sesión «{titulo}»:"


def texto_reenviado(mensaje: MensajeVista, agente: str, titulo: str, automatico: bool = False) -> str:
	return f"{etiqueta(agente, titulo, automatico)}\n\n{mensaje.texto.strip()}"


def destinos(filas: Iterable[FilaSesion], origen: str, se_puede: Callable[[FilaSesion], bool],
		pareja: str | None = None) -> list[FilaSesion]:
	"""Sesiones a las que se puede enviar: las de Ctrl+IA, salvo la de origen, las ocultas y las archivadas.

	La que envió la última respuesta a la de origen va primero (así contestar es
	Intro); después, de la más reciente a la más antigua. Las de otros programas
	nunca: Ctrl+IA no escribe en conversaciones que no empezó.
	"""
	encontradas = [f for f in filas if f.clave != origen and f.propia and not f.oculta and not f.archivada and se_puede(f)]
	encontradas.sort(key=lambda f: (f.clave != pareja, -(f.actividad or 0)))
	return encontradas


def demasiado_largo(proveedor: str, nombre_agente: str, texto: str) -> str:
	"""Por qué ``texto`` no puede ir a una sesión de ``proveedor``, o ``""``."""
	limite = LIMITES.get(proveedor)
	if limite is not None and len(texto) > limite:
		return (f"{nombre_agente} admite mensajes de hasta {limite:,} caracteres y este tiene {len(texto):,}. "
			"Acórtalo y vuelve a enviarlo.").replace(",", ".")
	return ""


def titulo_nueva(titulo_origen: str) -> str:
	"""Título que se propone para una sesión nueva: sin él saldría de las primeras palabras de la etiqueta."""
	titulo = " ".join((titulo_origen or "").split()) or "Sin título"
	return f"Reenviado de {titulo}"


def problema_conversacion(origen: FilaSesion, se_puede: bool, trabajando: bool, hay_otra: bool) -> Problema | None:
	"""Por qué la sesión de la que viene la respuesta no puede arrancar una conversación automática.

	:param se_puede: su agente está en este equipo.
	:param trabajando: si sigue trabajando, su fin de turno se tomaría por una respuesta del relevo.
	:param hay_otra: solo puede haber una conversación a la vez.
	"""
	if hay_otra:
		return Problema("ya hay una en marcha", "ya hay una conversación entre sesiones en marcha; detenla antes")
	if not origen.propia or not se_puede:
		return Problema("esta sesión no es de Ctrl+IA", f"«{origen.titulo}» no es una sesión de Ctrl+IA")
	if trabajando:
		return Problema("esta sesión sigue trabajando", f"«{origen.titulo}» sigue trabajando; espera a que termine su turno")
	return None


def problema_destino(titulo: str, trabajando: bool) -> Problema | None:
	"""Por qué ``titulo`` no puede ser la otra mitad: un fin de turno ajeno se tomaría por su contestación."""
	if trabajando:
		return Problema("la otra sesión sigue trabajando", f"«{titulo}» sigue trabajando; espera a que termine su turno")
	return None


def separar_etiqueta(texto: str, agente: str, titulo: str) -> str | None:
	"""La respuesta de ``texto`` sin la etiqueta, o ``None`` si se cambió o se quitó: entonces todo es de la persona."""
	prefijo = etiqueta(agente, titulo)
	limpio = (texto or "").strip()
	if not limpio.startswith(prefijo):
		return None
	return limpio[len(prefijo):].strip() or None


def partir_mensaje(enviado: str, agente: str, titulo: str, respuesta: str) -> tuple[str | None, str]:
	"""Qué parte de ``enviado`` es la respuesta del otro agente y qué parte es de la persona.

	Lo añadido tras la respuesta sin cambiar es de la persona y va como suyo, no dentro de las
	palabras del otro agente. Sin la etiqueta, es todo suyo: ``(None, enviado)``. Si cambió
	la propia respuesta, se sigue pasando como respuesta.
	"""
	cuerpo = separar_etiqueta(enviado, agente, titulo)
	if cuerpo is None:
		return None, (enviado or "").strip()
	respuesta = (respuesta or "").strip()
	if respuesta and cuerpo.startswith(respuesta):
		return respuesta, cuerpo[len(respuesta):].strip()
	return cuerpo, ""


def mensaje_conjunto(enviado: str, agente: str, titulo: str, respuesta: str) -> str:
	"""El primer mensaje de una conversación automática que nace de una respuesta reenviada."""
	pasada, de_la_persona = partir_mensaje(enviado, agente, titulo, respuesta)
	if pasada is None:
		return de_la_persona
	texto = f"{etiqueta(agente, titulo, automatico=True)}\n\n{pasada}"
	return f"{texto}\n\n{ANADE_LA_PERSONA}\n\n{de_la_persona}" if de_la_persona else texto
