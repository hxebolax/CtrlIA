"""Informe de un fallo para abrir una incidencia en GitHub (AIChat #465).

Sin interfaz (no importa wx). Un informe es lo que escribe la persona (resumen,
qué ha pasado, qué esperaba y los pasos) más una foto del entorno: versiones, cada
agente, cuántas sesiones hay de cada tipo y los servidores MCP de la sesión abierta.

El informe acaba en una incidencia PÚBLICA, así que de cada dato solo sale lo que
tiene una forma conocida:

- De cada agente, un número de versión, si la sesión está iniciada y cómo está
  instalado. Nunca la cuenta, la ruta ni los mensajes del detector. Los agentes ACP
  personalizados no se nombran: su nombre lo eligió la persona.
- De las sesiones, solo recuentos por agente y origen: ni títulos ni carpetas.
- De los servidores MCP, nombre, estado y motivo. Nunca la orden ni la dirección,
  que pueden llevar secretos; y como el motivo a veces las repite, se quitan de él
  esas dos cosas y cualquier dirección, ruta o correo.

Todo pasa además por ``diagnostico.anonimizar``, también lo que escribe la persona.
El diálogo enseña el texto final antes de copiarlo o enviarlo.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

from app.core import diagnostico
from app.core.diagnostico import DatosEntorno, FilaAgente
from app.services.agentes import instalacion
from app.services.gestor import NOMBRES_ORIGEN, FilaSesion
from app.services.servidores_mcp import ServidorMcp
from app.ui.comun import NOMBRES_AGENTE, nombre_agente

log = logging.getLogger(__name__)

#: Página de GitHub donde se abre una incidencia nueva.
URL_NUEVA_INCIDENCIA = "https://github.com/hxebolax/CtrlIA/issues/new"
#: Lo máximo del cuerpo ya codificado: los navegadores y GitHub cortan las direcciones largas (unos 8 KB).
MAX_CUERPO_URL = 6000
#: Largo del campo más largo por debajo del cual ya no se recorta lo escrito.
MIN_CAMPO_RECORTADO = 40
MAX_RESUMEN = 200
#: Segundos que se espera a los agentes: uno que no conteste sale como desconocido.
ESPERA_AGENTES = 15.0
#: Lo que tenga más pinta de versión: «2.1.308», «0.163.0-beta.1». Lo demás podría ser un error con una ruta.
_RE_VERSION = re.compile(r"\d+(?:\.\d+)+[0-9A-Za-z.+-]{0,24}")
AGENTE_PERSONALIZADO = "Agente ACP personalizado"
OTRO_ORIGEN = "otro origen"

_RE_DIRECCION = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+")
_RE_RUTA_WINDOWS = re.compile(r"(?i)\b[a-z]:[\\/]\S*|\\\\\S+")
_RE_RUTA_POSIX = re.compile(r"(?<![\w.])(?:~|\.{0,2})?(?:/[\w.@~+-]+){2,}/?")
_RE_CORREO = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

NOTA_RECORTE = "\n\n…(recortado para que quepa en la dirección: el informe entero está en tu portapapeles; pégalo aquí encima)"


@dataclass(slots=True)
class InformeFallo:
	"""Lo que se cuenta en una incidencia."""

	resumen: str
	que_paso: str
	esperado: str = ""
	pasos: str = ""
	#: Texto de :func:`texto_entorno`.
	entorno: str = ""


# ── Datos del entorno ───────────────────────────────────────────────────────────


def fila_agente(identificador: str, nombre: str, estado: Any) -> FilaAgente:
	"""Un agente para el informe a partir de su ``EstadoProveedor`` (``None``: no contestó)."""
	nombre = nombre if identificador in NOMBRES_AGENTE else AGENTE_PERSONALIZADO
	if estado is None:
		return FilaAgente(nombre, "estado desconocido")
	if not estado.disponible:
		if identificador == "claude" and instalacion.aviso_claude_npm():
			return FilaAgente(nombre, "no se puede usar", instalacion="solo con npm")
		return FilaAgente(nombre, "no instalado")
	version = _RE_VERSION.search(estado.version or "")
	sesion = {True: "sesión iniciada", False: "sesión no iniciada"}.get(estado.sesion_iniciada, "sesión desconocida")
	return FilaAgente(nombre, "versión " + version.group(0) if version else "versión desconocida", sesion,
		instalacion.como_instalado(estado.ruta, estado.disponible))


def filas_agentes(gestor: Any, tiempo: float = ESPERA_AGENTES) -> list[FilaAgente]:
	"""Detecta a la vez todos los agentes (bloquea: llamar desde un hilo); reutiliza lo de «Agentes instalados».

	Si uno no contesta a tiempo, vale lo último que se supo de él.
	"""
	proveedores = dict(gestor.proveedores)
	grupo = ThreadPoolExecutor(max_workers=max(1, len(proveedores)), thread_name_prefix="informe-agentes")
	try:
		futuros = {identificador: grupo.submit(gestor.detectar, identificador) for identificador in proveedores}
		wait(futuros.values(), timeout=tiempo)
	finally:
		grupo.shutdown(wait=False, cancel_futures=True)
	filas: list[FilaAgente] = []
	for identificador, proveedor in proveedores.items():
		futuro = futuros[identificador]
		if futuro.done() and not futuro.cancelled() and futuro.exception() is None:
			estado = futuro.result()
		else:
			estado = gestor.estado_proveedor(identificador)
		filas.append(fila_agente(identificador, str(getattr(proveedor, "nombre", "") or identificador), estado))
	return filas


def recuentos_sesiones(filas: Sequence[FilaSesion]) -> tuple[dict[str, dict[str, int]], int]:
	"""``({agente: {origen: cuántas}}, ocultas)``: nunca sale nada de una sesión, solo cuántas hay."""
	recuentos: dict[str, dict[str, int]] = {}
	ocultas = 0
	for fila in filas:
		if fila.oculta or fila.archivada:
			ocultas += 1
			continue
		agente = nombre_agente(fila.proveedor) if fila.proveedor in NOMBRES_AGENTE else AGENTE_PERSONALIZADO
		origen = NOMBRES_ORIGEN.get(fila.origen, OTRO_ORIGEN)
		por_origen = recuentos.setdefault(agente, {})
		por_origen[origen] = por_origen.get(origen, 0) + 1
	return recuentos, ocultas


def motivo_sin_secretos(servidor: ServidorMcp) -> str:
	"""El motivo de un servidor sin su orden, direcciones, rutas ni correos, que a veces repite."""
	motivo = servidor.motivo
	for parte in sorted(servidor.destino.split(), key=len, reverse=True):
		if len(parte) >= 3:
			motivo = motivo.replace(parte, "<orden>")
	motivo = _RE_DIRECCION.sub("<dirección>", motivo)
	motivo = _RE_RUTA_WINDOWS.sub("<ruta>", motivo)
	motivo = _RE_RUTA_POSIX.sub("<ruta>", motivo)
	return _RE_CORREO.sub("<correo>", motivo)


def filas_mcp(conversacion: Any) -> list[tuple[str, str, str]] | None:
	"""``(nombre, estado, motivo)`` de los servidores de una conversación (bloquea); ``None`` si no hay conversación."""
	if conversacion is None:
		return None
	try:
		servidores = conversacion.servidores_mcp()
	except (OSError, RuntimeError, TimeoutError):
		log.info("No se ha podido preguntar por los servidores MCP para el informe", exc_info=True)
		servidores = None
	return [(s.nombre, s.estado_legible, motivo_sin_secretos(s)) for s in servidores or []]


def recoger_entorno(gestor: Any, filas: Sequence[FilaSesion], clave_abierta: str = "",
		agentes: Callable[[Any], list[FilaAgente]] = filas_agentes) -> DatosEntorno:
	"""Reúne los datos del informe. Bloquea (detecta los agentes): llamar desde un hilo.

	:param filas: la lista de sesiones que ya tiene la ventana.
	:param clave_abierta: la sesión abierta, de la que se dicen los servidores MCP.
	"""
	recuentos, ocultas = recuentos_sesiones(filas)
	conversacion = gestor.conversacion(clave_abierta, crear=False) if clave_abierta else None
	return DatosEntorno(agentes(gestor), recuentos, ocultas, filas_mcp(conversacion))


def texto_entorno(nombre_app: str, version: str, datos: DatosEntorno) -> str:
	"""El apartado «Entorno» del informe, sin la lista de paquetes (no cabría en la dirección)."""
	return diagnostico.informe_sistema(nombre_app, version, datos, paquetes=False)


# ── El informe ──────────────────────────────────────────────────────────────────


def texto_informe(informe: InformeFallo, con_resumen: bool = True) -> str:
	"""El informe en Markdown y anonimizado.

	:param con_resumen: ``False`` lo deja fuera: en la incidencia es el título.
	"""
	partes = ["### Resumen", informe.resumen.strip() or "(sin decir)", ""] if con_resumen else []
	partes += ["### Qué ha pasado", informe.que_paso.strip() or "(sin decir)", ""]
	if informe.esperado.strip():
		partes += ["### Qué esperaba", informe.esperado.strip(), ""]
	if informe.pasos.strip():
		partes += ["### Pasos para reproducirlo", informe.pasos.strip(), ""]
	if informe.entorno.strip():
		partes += ["### Entorno", "```text", informe.entorno.strip(), "```", ""]
	partes.append("_Enviado desde Ayuda, «Informar de un fallo», de Ctrl+IA._")
	return diagnostico.anonimizar("\n".join(partes))


def _largo_en_url(texto: str) -> int:
	return len(urllib.parse.quote(texto))


def _cuerpo_para_url(informe: InformeFallo) -> str:
	"""El cuerpo que cabe en la dirección: se recorta lo escrito (el más largo cada vez) antes que el entorno."""
	cuerpo = texto_informe(informe, con_resumen=False)
	if _largo_en_url(cuerpo) <= MAX_CUERPO_URL:
		return cuerpo
	campos = {"que_paso": informe.que_paso, "esperado": informe.esperado, "pasos": informe.pasos}
	while _largo_en_url(cuerpo) > MAX_CUERPO_URL:
		mas_largo = max(campos, key=lambda clave: len(campos[clave]))
		if len(campos[mas_largo]) < MIN_CAMPO_RECORTADO:
			break
		campos[mas_largo] = campos[mas_largo][:len(campos[mas_largo]) // 2]
		cuerpo = texto_informe(InformeFallo(informe.resumen, campos["que_paso"] + NOTA_RECORTE, campos["esperado"],
			campos["pasos"], informe.entorno), con_resumen=False)
	if _largo_en_url(cuerpo) <= MAX_CUERPO_URL:
		return cuerpo
	# Ni así cabe (muchos servidores MCP): se corta por el final, porque la dirección tiene que abrirse.
	while _largo_en_url(cuerpo + NOTA_RECORTE) > MAX_CUERPO_URL:
		cuerpo = cuerpo[:len(cuerpo) * 9 // 10]
	return cuerpo + NOTA_RECORTE


def url_incidencia(informe: InformeFallo) -> str:
	"""La página «nueva incidencia» de GitHub con el informe puesto, recortado si no cabe."""
	consulta = urllib.parse.urlencode({
		"title": diagnostico.anonimizar(" ".join(informe.resumen.split()))[:MAX_RESUMEN],
		"body": _cuerpo_para_url(informe),
		"labels": "bug",
	})
	return f"{URL_NUEVA_INCIDENCIA}?{consulta}"
