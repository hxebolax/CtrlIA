"""Qué lee el lector de pantalla en las listas de Ctrl+IA (lógica sin ventanas).

Al moverse arriba y abajo, el lector lee la fila nativa columna a columna: lo
que se oye son las columnas visibles de la lista y en su orden. Por eso aquí
se definen las columnas posibles de cada lista y cómo se escribe cada una
(con o sin «Favorita», fechas relativas o absolutas, indicadores...).

Al moverse a izquierda y derecha, Ctrl+IA lee una celda: el número de fila,
el nombre de la columna y el contenido, según ``preferencias_lectura``.

Todo se lee de la configuración EN CADA USO, así que un cambio en
Preferencias se nota al momento.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

from app.ui.accesibilidad.composicion import PreferenciasLectura
from app.ui.comun import _, fecha_relativa, hora_corta

Opcion = Callable[[str, Any], Any]

#: Columnas posibles de la lista de mensajes: id → (cabecera, ancho).
COLUMNAS_MENSAJES: dict[str, tuple[str, int]] = {
	"numero": (_("Número"), 70),
	"quien": (_("Quién"), 110),
	"mensaje": (_("Mensaje"), 640),
	"indicadores": (_("Indicadores"), 200),
	"hora": (_("Hora"), 120),
}
MENSAJES_PREDETERMINADAS = ["quien", "mensaje", "hora", "indicadores"]

#: Columnas posibles de la lista de grupos.
COLUMNAS_GRUPOS: dict[str, tuple[str, int]] = {
	"nombre": (_("Nombre"), 260),
	"sesiones": (_("Sesiones"), 90),
	"descripcion": (_("Descripción"), 320),
	"agente": (_("Agente predeterminado"), 150),
	"carpeta": (_("Carpeta predeterminada"), 220),
}
GRUPOS_PREDETERMINADAS = ["nombre", "sesiones", "descripcion"]

#: Indicadores que pueden acompañar a un mensaje (cada uno es ``lectura.indicador_<id>``).
INDICADORES = ("favorito", "pregunta", "permiso", "error", "detenido", "adjuntos", "codigo", "enlaces", "archivos", "largo")

#: Clases de respuesta que dicen «sí» (permitir, aprobar el plan, responder la pregunta).
_CLASES_SI = frozenset({"permitir", "permitir_sesion", "permitir_siempre"})

_RE_ENLACE = re.compile(r"https?://[^\s<>()\"']+")
_RE_VALLA = re.compile(r"^\s*(```|~~~)", re.MULTILINE)
#: A partir de cuántos caracteres un mensaje se anuncia como «largo».
MENSAJE_LARGO = 1500


def preferencias_lectura(opcion: Opcion) -> PreferenciasLectura:
	"""Qué se dice al recorrer las columnas con izquierda y derecha."""
	return PreferenciasLectura(
		numero_fila=bool(opcion("lectura.numero_fila", True)),
		cabecera=bool(opcion("lectura.cabecera", True)),
		contenido=bool(opcion("lectura.contenido", True)),
	)


def columnas_validas(elegidas: Any, disponibles: dict[str, tuple[str, int]], predeterminadas: list[str]) -> list[str]:
	"""Las columnas guardadas que existen, sin repetir; si no queda ninguna, las predeterminadas."""
	if not isinstance(elegidas, list):
		return list(predeterminadas)
	validas = [c for c in dict.fromkeys(elegidas) if isinstance(c, str) and c in disponibles]
	return validas or list(predeterminadas)


def escribir_fecha(segundos: float, opcion: Opcion) -> str:
	"""«hace 5 minutos» o «17/10/2026 21:39», según Preferencias."""
	if opcion("lectura.fechas", "relativas") == "absolutas":
		if not segundos:
			return _("Sin fecha")
		return dt.datetime.fromtimestamp(segundos).strftime("%d/%m/%Y %H:%M")
	return fecha_relativa(segundos)


def escribir_hora(segundos: float, opcion: Opcion) -> str:
	"""Hora de un mensaje: corta («10:15») o completa con la fecha."""
	if opcion("lectura.fechas", "relativas") == "absolutas":
		return escribir_fecha(segundos, opcion) if segundos else ""
	return hora_corta(segundos)


def _plural(numero: int, singular: str, plural: str) -> str:
	return (singular if numero == 1 else plural).format(n=numero)


def resultado_solicitud(entrada: dict[str, Any]) -> str:
	"""En qué quedó una petición guardada: «pendiente», «si», «no», «retirada» o «sin_respuesta»."""
	estado = str(entrada.get("estado") or "")
	if estado == "pendiente":
		return "pendiente"
	if estado == "respondida":
		respuesta = entrada.get("respuesta") if isinstance(entrada.get("respuesta"), dict) else {}
		return "si" if str(respuesta.get("clase") or "") in _CLASES_SI else "no"
	return "retirada" if estado == "retirada" else "sin_respuesta"


def _textos_solicitud(tipo: str) -> dict[str, str]:
	if tipo == "pregunta":
		return {"pendiente": _("Pregunta por responder"), "si": _("Pregunta respondida"),
			"no": _("Pregunta sin responder"), "retirada": _("Pregunta retirada"), "sin_respuesta": _("Pregunta sin responder")}
	if tipo == "plan":
		return {"pendiente": _("Plan por aprobar"), "si": _("Plan aprobado"), "no": _("Plan rechazado"),
			"retirada": _("Plan retirado"), "sin_respuesta": _("Plan sin respuesta")}
	return {"pendiente": _("Permiso por responder"), "si": _("Permiso concedido"), "no": _("Permiso denegado"),
		"retirada": _("Permiso retirado"), "sin_respuesta": _("Permiso sin respuesta")}


def _indicadores_solicitudes(solicitudes: Any, opcion: Opcion) -> list[str]:
	"""«Pregunta por responder», «Permiso concedido», «Plan aprobado»... de las peticiones reales del agente."""
	if not isinstance(solicitudes, list):
		return []
	partes: list[str] = []
	for tipo, clave in (("pregunta", "lectura.indicador_pregunta"), ("plan", "lectura.indicador_permiso"),
			("herramienta", "lectura.indicador_permiso")):
		if not opcion(clave, True):
			continue
		resultados = [resultado_solicitud(s) for s in solicitudes if isinstance(s, dict) and s.get("tipo") == tipo]
		if not resultados:
			continue
		if tipo == "herramienta" and "pendiente" not in resultados and "si" in resultados and "no" in resultados:
			partes.append(_("Permisos concedidos y denegados"))
			continue
		for resultado in ("pendiente", "si", "no", "retirada", "sin_respuesta"):
			if resultado in resultados:
				partes.append(_textos_solicitud(tipo)[resultado])
				break
	return partes


def _herramienta_con_error(detalle: str) -> bool:
	"""Si la acción terminó con error (el gestor y el historial ponen «Error:» antes de su salida)."""
	return "\n\nError:\n" in detalle or detalle.startswith("Error:\n")


def indicadores_mensaje(texto: str, rol: str, cambios: int, opcion: Opcion, datos: dict[str, Any] | None = None,
		detalle: str = "", favorito: bool = False, nota_favorito: str = "") -> str:
	"""Palabras que resumen un mensaje: «Favorito, Pregunta por responder, 2 bloques de código, 3 enlaces».

	Van en el texto de una columna (no como voz aparte) para que también salgan
	en braille. Cada indicador se activa por separado en Preferencias; con
	``lectura.indicadores`` desactivado no se dice ninguno.

	Nada se adivina por el texto: las preguntas, los permisos y los planes
	salen de las peticiones reales del agente que el gestor guarda en
	``datos["solicitudes"]`` (un «?» al final no es una pregunta que se pueda
	contestar con Control+Mayúsculas+A); «Detenido» y los adjuntos, de
	``datos``; «Con error», de la salida de la acción; «Favorito», de los mensajes
	que marcaste con Control+Alt+F (va el primero, con su nota si la tiene).
	"""
	if not opcion("lectura.indicadores", True):
		return ""
	texto = texto or ""
	datos = datos if isinstance(datos, dict) else {}
	partes: list[str] = []
	if favorito and opcion("lectura.indicador_favorito", True):
		# Con nota, «Favorito: la nota» (la nota es de una línea y corta).
		partes.append(_("Favorito: {n}").format(n=nota_favorito) if nota_favorito else _("Favorito"))
	partes += _indicadores_solicitudes(datos.get("solicitudes"), opcion)
	if opcion("lectura.indicador_error", True) and rol == "herramienta" and _herramienta_con_error(detalle or ""):
		partes.append(_("Con error"))
	if opcion("lectura.indicador_detenido", True) and datos.get("detenido"):
		partes.append(_("Detenido"))
	adjuntos = datos.get("adjuntos")
	if opcion("lectura.indicador_adjuntos", True) and isinstance(adjuntos, list) and adjuntos:
		partes.append(_plural(len(adjuntos), _("{n} adjunto"), _("{n} adjuntos")))
	if opcion("lectura.indicador_codigo", True):
		bloques = len(_RE_VALLA.findall(texto)) // 2
		if bloques:
			partes.append(_plural(bloques, _("{n} bloque de código"), _("{n} bloques de código")))
	if opcion("lectura.indicador_enlaces", True):
		enlaces = len(set(_RE_ENLACE.findall(texto)))
		if enlaces:
			partes.append(_plural(enlaces, _("{n} enlace"), _("{n} enlaces")))
	if opcion("lectura.indicador_archivos", True) and cambios:
		partes.append(_plural(cambios, _("{n} archivo cambiado"), _("{n} archivos cambiados")))
	if opcion("lectura.indicador_largo", True) and len(texto) >= MENSAJE_LARGO:
		partes.append(_("Largo"))
	return ", ".join(partes)


def valores_grupo(grupo: Any, columnas: list[str], nombre_agente: Callable[[str], str]) -> list[str]:
	"""Texto de cada columna de un grupo en la lista de grupos."""
	valores: list[str] = []
	for columna in columnas:
		if columna == "nombre":
			valores.append(grupo.nombre)
		elif columna == "sesiones":
			valores.append(_plural(grupo.sesiones, _("{n} sesión"), _("{n} sesiones")) if grupo.sesiones else _("Vacío"))
		elif columna == "descripcion":
			valores.append(" ".join((grupo.descripcion or "").split()) or _("Sin descripción"))
		elif columna == "agente":
			valores.append(nombre_agente(grupo.agente) if grupo.agente else _("El de siempre"))
		elif columna == "carpeta":
			valores.append(grupo.carpeta or _("Ninguna"))
	return valores


def ejemplo_lectura(nombres: dict[str, str], ejemplos: dict[str, str], activas: list[str]) -> str:
	"""Cómo sonará una fila con estas columnas: «Título: Informe; Estado: Esperando...»."""
	partes = [f"{nombres[c]}: {ejemplos.get(c, '')}" for c in activas if c in nombres]
	return "; ".join(partes) if partes else _("No hay ninguna columna marcada.")
