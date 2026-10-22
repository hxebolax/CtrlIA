"""Enviar la respuesta de un agente a otra sesión (AIChat #248), sin wx.

Una respuesta de una sesión va a otra sesión de Ctrl+IA, de cualquier agente,
para que un agente revise o conteste el trabajo de otro. Lleva delante una
línea que dice qué agente la escribió y en qué sesión: así el agente que la
recibe no toma las palabras de otro agente como instrucciones de la persona.
La persona lee y puede cambiar el mensaje entero antes de enviarlo, y va como
un mensaje normal: nunca responde una pregunta ni aprueba una herramienta, que
siguen llegando a la persona en cada sesión.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from app.services.gestor import FilaSesion, MensajeVista

#: Lo que se puede reenviar: lo que dijo un agente o el plan que propuso. Los
#: errores, avisos y salidas de herramientas son de esa sesión.
ROLES_REENVIABLES = ("asistente", "plan")

#: Codex rechaza los mensajes más largos; de los demás no se conoce un límite.
LIMITES = {"codex": 32000}


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
