"""Catálogo de agentes de IA que Ctrl+IA sabe manejar.

Los módulos de cada agente se importan al crear el catálogo, no al importar
este paquete, para que el arranque no pague por agentes que no se usan.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from app.services.agentes.base import Proveedor

log = logging.getLogger(__name__)

#: Orden en que aparecen los agentes en los menús.
ORDEN = ("claude", "codex", "antigravity", "gemini", "ollama", "claude-acp", "codex-acp", "antigravity-acp")

#: Opciones de :func:`crear_proveedores` que, si no se indican, se leen de la
#: configuración ya cargada: ``opción -> (clave de configuración, valor por omisión)``.
_DE_CONFIGURACION = {
	"ollama": ("agentes.ollama", True),
	"otros_acp": ("agentes.otros_acp", False),
}


def _opcion_configurada(clave: str, por_defecto: Any) -> Any:
	"""Valor de la configuración que ya cargó el programa, sin crearla si no existe.

	Así las pruebas y las herramientas que llaman a :func:`crear_proveedores`
	sin configuración no escriben nada en ``data``.
	"""
	from app.core import configuracion

	instancia = getattr(configuracion, "_instancia", None)
	if instancia is None:
		return por_defecto
	try:
		return instancia.obtener(clave, por_defecto)
	except Exception:  # noqa: BLE001 - una clave rara no debe impedir crear los agentes
		log.debug("No se pudo leer %s de la configuración", clave, exc_info=True)
		return por_defecto


def crear_proveedores(opciones: dict[str, Any] | None = None) -> dict[str, Proveedor]:
	"""Crea un proveedor por agente conocido. Un agente roto no impide cargar los demás.

	:param opciones: ``permitir_clave_api`` y ``lista_tareas_claude`` (Claude), ``acp_adaptadores``
		(activa los adaptadores ACP opcionales), ``acp_personalizados``
		(lista de agentes ACP añadidos por el usuario), ``ollama`` (muestra
		Ollama; por omisión, sí), ``ollama_servidor`` (dirección del servidor
		de Ollama, o función que la devuelve; por omisión, la de Preferencias)
		y ``otros_acp`` (OpenCode, Goose, Copilot y otros agentes ACP conocidos).
	"""
	opciones = dict(opciones or {})
	for nombre, (clave, por_defecto) in _DE_CONFIGURACION.items():
		if nombre not in opciones:
			opciones[nombre] = _opcion_configurada(clave, por_defecto)
	proveedores: dict[str, Proveedor] = {}

	def anadir(fabrica) -> None:
		try:
			proveedor = fabrica()
		except Exception:  # noqa: BLE001 - un agente que no carga no debe dejar la aplicación sin los demás
			log.exception("No se pudo cargar un agente")
			return
		if proveedor is not None:
			proveedores[proveedor.id] = proveedor

	def claude():
		from app.services.agentes.claude import ProveedorClaude

		lista_tareas = opciones.get("lista_tareas_claude")
		if lista_tareas is None:
			# Se lee en cada turno: un cambio en Preferencias vale desde el siguiente mensaje.
			def lista_tareas() -> bool:
				return bool(_opcion_configurada("agentes.lista_tareas_claude", True))
		esperar_mcp = opciones.get("esperar_mcp_claude")
		if esperar_mcp is None:
			def esperar_mcp() -> bool:
				return bool(_opcion_configurada("agentes.esperar_mcp_claude", True))
		chrome = opciones.get("chrome_claude")
		if chrome is None:
			def chrome() -> bool:
				return bool(_opcion_configurada("agentes.chrome_claude", True))
		minutos_abierto = opciones.get("claude_minutos_abierto")
		if minutos_abierto is None:
			def minutos_abierto() -> int:
				return int(_opcion_configurada("agentes.claude_minutos_abierto", 15))
		return ProveedorClaude(permitir_clave_api=bool(opciones.get("permitir_clave_api")), lista_tareas=lista_tareas,
			esperar_mcp=esperar_mcp, minutos_abierto=minutos_abierto, chrome=chrome)

	def codex():
		from app.services.agentes.codex import ProveedorCodex

		return ProveedorCodex()

	def antigravity():
		from app.services.agentes.antigravity import ProveedorAntigravity

		return ProveedorAntigravity()

	def gemini():
		from app.services.agentes.acp import proveedor_gemini_cli

		return proveedor_gemini_cli()

	def ollama():
		from app.services.agentes.ollama import ProveedorOllama

		servidor = opciones.get("ollama_servidor")
		if servidor is None:
			# Se lee cada vez: un cambio en Preferencias vale sin reiniciar.
			def servidor() -> str:
				# Un equipo SSH marcado para Ollama (experimental): se llega a él por un túnel.
				from app.services.agentes import remoto

				tunel = remoto.direccion_ollama()
				return tunel or str(_opcion_configurada("agentes.servidor_ollama", "") or "")

		return ProveedorOllama(servidor)

	anadir(claude)
	anadir(codex)
	anadir(antigravity)
	anadir(gemini)
	if opciones.get("ollama"):
		anadir(ollama)
	if opciones.get("acp_adaptadores"):
		from app.services.agentes import acp

		for nombre in ("proveedor_claude_acp", "proveedor_codex_acp", "proveedor_antigravity_acp"):
			fabrica = getattr(acp, nombre, None)
			if fabrica is not None:
				anadir(fabrica)
	if opciones.get("otros_acp"):
		try:
			from app.services.agentes.acp_conocidos import proveedores_conocidos

			for proveedor in proveedores_conocidos():
				proveedores[proveedor.id] = proveedor
		except Exception:  # noqa: BLE001 - un agente que no carga no debe dejar la aplicación sin los demás
			log.exception("No se pudieron cargar los otros agentes ACP conocidos")
	personalizados = opciones.get("acp_personalizados") or []
	if personalizados:
		try:
			from app.services.agentes.acp import proveedores_personalizados

			for proveedor in proveedores_personalizados(personalizados):
				proveedores[proveedor.id] = proveedor
		except Exception:  # noqa: BLE001 - una entrada mal escrita no debe tumbar el arranque
			log.exception("No se pudieron cargar los agentes ACP personalizados")
	ordenados = {clave: proveedores[clave] for clave in ORDEN if clave in proveedores}
	ordenados.update({clave: valor for clave, valor in proveedores.items() if clave not in ordenados})
	return ordenados
