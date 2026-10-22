"""Peticiones de los servidores MCP («elicitación»), comunes a Codex y Claude Code.

Un servidor MCP (una herramienta externa del agente: GitHub, una base de datos,
un conector de ChatGPT...) puede pedir algo a la persona en mitad de un turno.
Codex las reenvía como ``mcpServer/elicitation/request`` y Claude Code como
``control_request`` de subtipo ``elicitation``. Hay cuatro formas:

- **Permiso para usar una herramienta.** Codex pregunta así antes de ejecutar
  una herramienta MCP: un formulario SIN campos (``properties`` vacío) y, en
  ``_meta``, ``codex_approval_kind: "mcp_tool_call"`` con el título y la
  descripción de la herramienta, sus parámetros (``tool_params`` y
  ``tool_params_display``) y en ``persist`` si se puede recordar la respuesta
  («session», «always» o las dos). Se responde ``accept`` (con ``_meta.persist``
  para recordarla), ``decline`` o ``cancel``. Un formulario sin campos de
  cualquier otro servidor es una simple confirmación y se trata igual.
- **Formulario** (``form``, y los ``openai/form`` y ``openaiForm`` de Codex):
  campos de texto, número, sí/no y listas de opciones; se contesta
  ``accept`` con ``content``.
- **Enlace** (``url``): la persona lo abre en su navegador y después acepta.
- Cualquier otra cosa (un modo nuevo, un campo que Ctrl+IA no sabe pedir): se
  enseña lo que pide el servidor y solo se ofrece rechazarlo o cancelarlo.
  Nunca se rechaza sin que la persona lo vea.

Las funciones son puras (sin wx ni procesos) para poder probarlas sin agentes.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.agentes.base import ClaseOpcion, OpcionPermiso, Pregunta, RespuestaPermiso, SolicitudPermiso, TipoSolicitud

log = logging.getLogger(__name__)

#: Modos de formulario que se rellenan igual que ``form``.
MODOS_FORMULARIO = frozenset({"form", "openai/form", "openaiForm"})
#: Valor de ``_meta.codex_approval_kind`` en los permisos de herramientas de Codex.
APROBACION_HERRAMIENTA = "mcp_tool_call"
#: Opciones propias de los permisos de herramientas.
OPCION_ACEPTAR = "accept"
OPCION_SESION = "accept_session"
OPCION_SIEMPRE = "accept_always"
OPCION_RECHAZAR = "decline"
OPCION_CANCELAR = "cancel"
#: Lo que se muestra como mucho de los parámetros de una herramienta.
MAX_PARAMETROS = 4000


def _texto(valor: Any) -> str:
	return valor.strip() if isinstance(valor, str) else ""


def _persistencias(meta: dict[str, Any]) -> set[str]:
	"""Qué puede recordar Codex de esta respuesta: ``session``, ``always`` o nada."""
	valor = meta.get("persist")
	if isinstance(valor, str):
		return {valor}
	if isinstance(valor, list):
		return {v for v in valor if isinstance(v, str)}
	return set()


def _valor_legible(valor: Any) -> str:
	if isinstance(valor, str):
		return valor
	try:
		return json.dumps(valor, ensure_ascii=False)
	except (TypeError, ValueError):
		return str(valor)


def _lineas_parametros(meta: dict[str, Any]) -> list[str]:
	"""Los datos con los que se llamará a la herramienta, uno por línea."""
	lineas = []
	mostrar = meta.get("tool_params_display")
	if isinstance(mostrar, list):
		for parametro in mostrar:
			if not isinstance(parametro, dict):
				continue
			nombre = _texto(parametro.get("display_name")) or _texto(parametro.get("name"))
			if nombre:
				lineas.append("%s: %s" % (nombre, _valor_legible(parametro.get("value"))))
	if not lineas and isinstance(meta.get("tool_params"), dict):
		for nombre, valor in meta["tool_params"].items():
			lineas.append("%s: %s" % (nombre, _valor_legible(valor)))
	texto = "\n".join(lineas)
	if len(texto) > MAX_PARAMETROS:
		texto = texto[:MAX_PARAMETROS] + "\n(recortado)"
	return [texto] if texto else []


def _es_formulario_vacio(esquema: Any) -> bool:
	if not isinstance(esquema, dict):
		return False
	propiedades = esquema.get("properties")
	return propiedades is None or (isinstance(propiedades, dict) and not propiedades)


def solicitud_elicitacion(
	identificador: str,
	servidor: str,
	mensaje: str,
	modo: str | None,
	*,
	url: str = "",
	esquema: Any = None,
	meta: dict[str, Any] | None = None,
	agente: str = "El agente",
	descripcion: str = "",
) -> SolicitudPermiso:
	"""Convierte una elicitación MCP en una ``SolicitudPermiso`` (nunca devuelve ``None``)."""
	servidor = servidor or "un servidor MCP"
	meta = meta if isinstance(meta, dict) else {}
	if modo == "url":
		return _solicitud_enlace(identificador, servidor, mensaje, url, descripcion)
	es_formulario = modo in MODOS_FORMULARIO or (modo is None and esquema is not None)
	if meta.get("codex_approval_kind") == APROBACION_HERRAMIENTA or (es_formulario and _es_formulario_vacio(esquema)):
		return _solicitud_herramienta(identificador, servidor, mensaje, meta, agente, descripcion)
	if es_formulario:
		solicitud = _solicitud_formulario(identificador, servidor, mensaje, esquema, descripcion)
		if solicitud is not None:
			return solicitud
	log.warning("Petición MCP que no se puede rellenar: modo %r de %s", modo, servidor)
	return _solicitud_sin_formulario(identificador, servidor, mensaje, url, descripcion)


#: Pregunta en inglés con la que Codex pide permiso para la herramienta de un servidor MCP propio
#: (sin ``tool_title``): «Allow the prueba MCP server to run tool "hora"?».
_RE_PREGUNTA_CODEX = re.compile(r'^Allow the (?P<servidor>.+?) MCP server to run tool "(?P<herramienta>[^"]+)"\?$')


def _solicitud_herramienta(identificador: str, servidor: str, mensaje: str, meta: dict[str, Any], agente: str,
		descripcion: str) -> SolicitudPermiso:
	titulo_herramienta = _texto(meta.get("tool_title"))
	conector = _texto(meta.get("connector_name"))
	pregunta = _RE_PREGUNTA_CODEX.match(mensaje.strip())
	if pregunta:
		titulo_herramienta = titulo_herramienta or pregunta["herramienta"]
		mensaje = "¿Permites que el servidor MCP «%s» use su herramienta «%s»?" % (pregunta["servidor"], pregunta["herramienta"])
	lineas = [mensaje] if mensaje else []
	if descripcion:
		lineas.append(descripcion)
	if titulo_herramienta:
		lineas.append("Herramienta: %s" % titulo_herramienta)
	if _texto(meta.get("tool_description")):
		lineas.append("Qué hace: %s" % _texto(meta["tool_description"]))
	lineas.append("Servidor: %s" % (conector or servidor))
	parametros = _lineas_parametros(meta)
	if parametros:
		lineas.append("Datos que recibirá:\n" + parametros[0])
	persistencias = _persistencias(meta)
	opciones = [OpcionPermiso(OPCION_ACEPTAR, "Permitir una vez", ClaseOpcion.PERMITIR)]
	if "session" in persistencias:
		opciones.append(OpcionPermiso(OPCION_SESION, "Permitir durante esta sesión", ClaseOpcion.PERMITIR_SESION,
			"No volverá a preguntar por esta herramienta mientras dure la conversación."))
	if "always" in persistencias:
		opciones.append(OpcionPermiso(OPCION_SIEMPRE, "Permitir siempre", ClaseOpcion.PERMITIR_SIEMPRE,
			"El agente lo guarda en su configuración y no volverá a preguntar por esta herramienta, tampoco en otras sesiones."))
	opciones.append(OpcionPermiso(OPCION_RECHAZAR, "Denegar", ClaseOpcion.DENEGAR))
	opciones.append(OpcionPermiso(OPCION_CANCELAR, "Cancelar", ClaseOpcion.CANCELAR,
		"Deniega y avisa al agente de que lo has cancelado; puede que deje lo que estaba haciendo."))
	nombre = titulo_herramienta or conector
	if nombre and conector and titulo_herramienta:
		titulo = "%s quiere usar «%s» de %s" % (agente, nombre, conector)
	elif nombre and titulo_herramienta and servidor != "un servidor MCP":
		titulo = "%s quiere usar «%s» del servidor MCP «%s»" % (agente, nombre, servidor)
	elif nombre:
		titulo = "%s quiere usar «%s»" % (agente, nombre)
	else:
		titulo = "%s pide tu permiso" % servidor
	return SolicitudPermiso(
		id=identificador,
		tipo=TipoSolicitud.HERRAMIENTA,
		titulo=titulo,
		detalle="\n".join(lineas),
		herramienta=titulo_herramienta or servidor,
		opciones=opciones,
		datos={"elicitacion": "herramienta", "recordar": bool(persistencias)},
	)


def _solicitud_enlace(identificador: str, servidor: str, mensaje: str, url: str, descripcion: str) -> SolicitudPermiso:
	partes = [mensaje] if mensaje else []
	if descripcion:
		partes.append(descripcion)
	partes.append("Enlace: %s" % url)
	partes.append("Ábrelo tú en el navegador y después pulsa Aceptar.")
	return SolicitudPermiso(
		id=identificador,
		tipo=TipoSolicitud.HERRAMIENTA,
		titulo="%s te pide que abras un enlace" % servidor,
		detalle="\n\n".join(partes),
		herramienta=servidor,
		opciones=[
			OpcionPermiso(OPCION_ACEPTAR, "Aceptar", ClaseOpcion.PERMITIR),
			OpcionPermiso(OPCION_RECHAZAR, "Rechazar", ClaseOpcion.DENEGAR),
			OpcionPermiso(OPCION_CANCELAR, "Cancelar", ClaseOpcion.CANCELAR),
		],
		datos={"elicitacion": "enlace", "url": url},
	)


def _opciones_de_campo(campo: dict[str, Any]) -> tuple[dict[str, str], bool] | None:
	"""``({etiqueta: valor}, varias)`` de un campo, o ``None`` si Ctrl+IA no sabe pedirlo."""
	tipo = campo.get("type")
	if tipo == "boolean":
		return {"Sí": "true", "No": "false"}, False
	if tipo == "string" and isinstance(campo.get("enum"), list):
		valores = [str(v) for v in campo["enum"]]
		nombres = campo.get("enumNames")
		if isinstance(nombres, list) and len(nombres) == len(valores):
			return {str(n): v for n, v in zip(nombres, valores, strict=True)}, False
		return {v: v for v in valores}, False
	if tipo == "string" and isinstance(campo.get("oneOf"), list):
		return {str(o.get("title") or o.get("const")): str(o.get("const")) for o in campo["oneOf"] if isinstance(o, dict)}, False
	if tipo == "array":
		elementos = campo.get("items") if isinstance(campo.get("items"), dict) else {}
		if isinstance(elementos.get("enum"), list):
			return {str(v): str(v) for v in elementos["enum"]}, True
		lista = elementos.get("anyOf") or elementos.get("oneOf")
		if isinstance(lista, list):
			return {str(o.get("title") or o.get("const")): str(o.get("const")) for o in lista if isinstance(o, dict)}, True
		return None
	if tipo in ("string", "number", "integer"):
		return {}, False
	return None


def _solicitud_formulario(identificador: str, servidor: str, mensaje: str, esquema: Any,
		descripcion: str) -> SolicitudPermiso | None:
	propiedades = esquema.get("properties") if isinstance(esquema, dict) else None
	if not isinstance(propiedades, dict) or not propiedades:
		return None
	obligatorios = set(esquema.get("required") or []) if isinstance(esquema.get("required"), list) else set()
	preguntas = []
	tipos: dict[str, tuple[str, dict[str, str]]] = {}
	for nombre, campo in propiedades.items():
		if not isinstance(campo, dict):
			return None
		forma = _opciones_de_campo(campo)
		if forma is None:
			return None
		valores, multiple = forma
		texto = str(campo.get("title") or nombre)
		ayuda = str(campo.get("description") or "")
		if nombre in obligatorios:
			ayuda = (ayuda + "; " if ayuda else "") + "obligatorio"
		tipos[nombre] = ("array" if multiple else str(campo.get("type")), valores)
		preguntas.append(Pregunta(
			id=nombre,
			texto=texto + (" (%s)" % ayuda if ayuda else ""),
			encabezado=texto,
			opciones=[(etiqueta, "") for etiqueta in valores],
			multiple=multiple,
			admite_otra=not valores,
			secreta=campo.get("format") == "password",
		))
	return SolicitudPermiso(
		id=identificador,
		tipo=TipoSolicitud.PREGUNTA,
		titulo="%s te pide datos" % servidor,
		detalle="\n\n".join(p for p in (mensaje, descripcion) if p),
		herramienta=servidor,
		opciones=[
			OpcionPermiso(OPCION_ACEPTAR, "Aceptar", ClaseOpcion.PERMITIR),
			OpcionPermiso(OPCION_RECHAZAR, "Rechazar", ClaseOpcion.DENEGAR),
			OpcionPermiso(OPCION_CANCELAR, "Cancelar", ClaseOpcion.CANCELAR),
		],
		preguntas=preguntas,
		datos={"elicitacion": "formulario", "tipos": tipos},
	)


def _solicitud_sin_formulario(identificador: str, servidor: str, mensaje: str, url: str, descripcion: str) -> SolicitudPermiso:
	partes = [p for p in (mensaje, descripcion) if p]
	if url:
		partes.append("Enlace: %s" % url)
	partes.append("Ctrl+IA todavía no sabe mostrar los campos de esta petición, así que solo puedes rechazarla o "
		"cancelarla. Si la necesitas, responde desde la app del agente.")
	return SolicitudPermiso(
		id=identificador,
		tipo=TipoSolicitud.HERRAMIENTA,
		titulo="%s te pide algo que Ctrl+IA no puede rellenar" % servidor,
		detalle="\n\n".join(partes),
		herramienta=servidor,
		opciones=[
			OpcionPermiso(OPCION_RECHAZAR, "Rechazar", ClaseOpcion.DENEGAR),
			OpcionPermiso(OPCION_CANCELAR, "Cancelar", ClaseOpcion.CANCELAR),
		],
		datos={"elicitacion": "desconocida"},
	)


def resultado_elicitacion(solicitud: SolicitudPermiso, respuesta: RespuestaPermiso, *, con_meta: bool = True) -> dict[str, Any]:
	"""Respuesta ``{action, content[, _meta]}`` para el agente.

	:param con_meta: Codex admite ``_meta`` (para recordar el permiso); Claude Code no.
	"""
	opcion = respuesta.opcion
	if opcion not in {o.id for o in solicitud.opciones}:
		segura = solicitud.opcion_segura()
		log.warning("Opción desconocida %r en una petición MCP; se usa la segura", opcion)
		opcion = segura.id if segura else OPCION_RECHAZAR
	if opcion in (OPCION_RECHAZAR, OPCION_CANCELAR):
		return {"action": opcion}
	clase = solicitud.datos.get("elicitacion")
	if clase == "herramienta":
		resultado: dict[str, Any] = {"action": "accept", "content": {}}
		recordar = {OPCION_SESION: "session", OPCION_SIEMPRE: "always"}.get(opcion)
		if recordar and con_meta:
			resultado["_meta"] = {"persist": recordar}
		return resultado
	tipos = solicitud.datos.get("tipos")
	if not tipos:
		return {"action": "accept"}
	contenido: dict[str, Any] = {}
	for nombre, (tipo, valores) in tipos.items():
		elegidos = [valores.get(v, v) for v in respuesta.respuestas.get(nombre, []) if v != ""]
		if not elegidos and respuesta.mensaje and len(tipos) == 1:
			elegidos = [respuesta.mensaje]
		if elegidos:
			contenido[nombre] = convertir_valor(tipo, elegidos)
	return {"action": "accept", "content": contenido}


def convertir_valor(tipo: str, valores: list[str]) -> Any:
	"""Pasa lo escrito al tipo que pide el formulario (número, sí/no, lista o texto)."""
	if tipo == "array":
		return valores
	valor = valores[0]
	if tipo == "boolean":
		return valor.strip().lower() in ("true", "sí", "si", "1")
	if tipo in ("number", "integer"):
		try:
			numero = float(valor.replace(",", "."))
		except ValueError:
			return valor
		return int(numero) if tipo == "integer" else numero
	return valor
