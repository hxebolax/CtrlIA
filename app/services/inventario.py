"""Qué herramientas, servidores MCP, complementos y habilidades tiene cargadas una sesión (AIChat #311).

Cada agente lo da a su manera; aquí se une en un formato común y se describe en
español, para leerlo en el visor de texto. Solo se lee: no se cambia nada.

- Claude Code lo cuenta al empezar cada turno (``system/init``): servidores
  MCP con su estado, complementos, habilidades, subagentes y herramientas.
- Codex lo da a petición: ``mcpServerStatus/list``, ``skills/list`` y
  ``plugin/list`` (de este, solo los complementos instalados y activos).

Sin wx.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Estados de los servidores MCP, en español.
ESTADOS_MCP = {
	"connected": "conectado",
	"pending": "conectándose",
	"failed": "no ha podido arrancar",
	"needs-auth": "necesita que inicies sesión",
	"disabled": "desactivado",
	"ready": "listo",
	"starting": "arrancando",
	"notLoggedIn": "necesita que inicies sesión",
	"oAuth": "con inicio de sesión",
	"bearerToken": "con clave",
	"unsupported": "sin inicio de sesión",
}


@dataclass(slots=True)
class Inventario:
	"""Lo que tiene cargado una sesión. Listas de ``(nombre, detalle)``."""

	agente: str
	servidores_mcp: list[tuple[str, str]] = field(default_factory=list)
	complementos: list[tuple[str, str]] = field(default_factory=list)
	habilidades: list[tuple[str, str]] = field(default_factory=list)
	subagentes: list[tuple[str, str]] = field(default_factory=list)
	herramientas: list[str] = field(default_factory=list)
	#: Por qué falta algo (por ejemplo, «aún no ha empezado»).
	nota: str = ""


def _texto(valor: Any) -> str:
	return " ".join(valor.split()) if isinstance(valor, str) else ""


def _estado(valor: Any) -> str:
	texto = _texto(valor)
	return ESTADOS_MCP.get(texto, texto)


def desde_init_claude(datos: dict[str, Any]) -> Inventario:
	"""El inventario de Claude Code a partir de su ``system/init``."""
	inventario = Inventario("Claude Code")
	for servidor in datos.get("mcp_servers") or []:
		if isinstance(servidor, dict) and servidor.get("name"):
			inventario.servidores_mcp.append((_texto(servidor["name"]), _estado(servidor.get("status"))))
	for complemento in datos.get("plugins") or []:
		if isinstance(complemento, dict) and complemento.get("name"):
			version = _texto(complemento.get("version"))
			inventario.complementos.append((_texto(complemento["name"]), "versión %s" % version if version else ""))
		elif isinstance(complemento, str):
			inventario.complementos.append((_texto(complemento), ""))
	for habilidad in datos.get("skills") or []:
		if isinstance(habilidad, str) and habilidad.strip():
			inventario.habilidades.append((habilidad.strip(), ""))
	for subagente in datos.get("agents") or []:
		if isinstance(subagente, str) and subagente.strip():
			inventario.subagentes.append((subagente.strip(), ""))
	inventario.herramientas = [_texto(h) for h in datos.get("tools") or [] if isinstance(h, str) and h.strip()]
	return inventario


def desde_codex(mcp: Any, habilidades: Any, complementos: Any) -> Inventario:
	"""El inventario de Codex a partir de sus tres respuestas (cualquiera puede faltar: ``None``)."""
	inventario = Inventario("Codex")
	for servidor in (mcp or {}).get("data") or [] if isinstance(mcp, dict) else []:
		if not isinstance(servidor, dict) or not servidor.get("name"):
			continue
		partes = []
		herramientas = servidor.get("tools")
		if isinstance(herramientas, dict) and herramientas:
			partes.append("%d herramientas" % len(herramientas))
		if servidor.get("toolsError"):
			partes.append("error: %s" % _texto(str(servidor["toolsError"]))[:200])
		autenticacion = _estado(servidor.get("authStatus"))
		if autenticacion and autenticacion != "sin inicio de sesión":
			partes.append(autenticacion)
		if servidor.get("pluginId"):
			partes.append("del complemento %s" % _texto(servidor["pluginId"]))
		inventario.servidores_mcp.append((_texto(servidor["name"]), ", ".join(partes)))
	for grupo in (habilidades or {}).get("data") or [] if isinstance(habilidades, dict) else []:
		for habilidad in grupo.get("skills") or [] if isinstance(grupo, dict) else []:
			if not isinstance(habilidad, dict) or not habilidad.get("name"):
				continue
			if habilidad.get("enabled") is False:
				continue
			ambito = {"repo": "del proyecto", "user": "tuya", "system": "de Codex", "admin": "del administrador"}.get(
				_texto(habilidad.get("scope")), _texto(habilidad.get("scope")))
			inventario.habilidades.append((_texto(habilidad["name"]), ambito))
	if isinstance(complementos, dict):
		for mercado in complementos.get("marketplaces") or []:
			for complemento in mercado.get("plugins") or [] if isinstance(mercado, dict) else []:
				if not isinstance(complemento, dict) or not complemento.get("installed") or not complemento.get("enabled"):
					continue
				interfaz = complemento.get("interface") if isinstance(complemento.get("interface"), dict) else {}
				nombre = _texto(interfaz.get("displayName")) or _texto(complemento.get("name"))
				inventario.complementos.append((nombre, _texto(interfaz.get("shortDescription"))))
	return inventario


def describir(inventario: Inventario, titulo: str = "") -> str:
	"""Texto para el visor, con un apartado por tipo y una línea por elemento."""
	lineas = ["Herramientas de %s%s" % (inventario.agente, " en «%s»" % titulo if titulo else ""), ""]
	if inventario.nota:
		lineas += [inventario.nota, ""]

	def apartado(nombre: str, elementos: list[tuple[str, str]], vacio: str) -> None:
		lineas.append("%s (%d):" % (nombre, len(elementos)))
		if not elementos:
			lineas.append(vacio)
		for elemento, detalle in elementos:
			lineas.append("- %s%s" % (elemento, ": " + detalle if detalle else ""))
		lineas.append("")

	apartado("Servidores MCP y conectores", inventario.servidores_mcp, "Ninguno.")
	apartado("Complementos", inventario.complementos, "Ninguno.")
	apartado("Habilidades", inventario.habilidades, "Ninguna.")
	if inventario.subagentes:
		apartado("Subagentes", inventario.subagentes, "")
	if inventario.herramientas:
		propias = [h for h in inventario.herramientas if not h.startswith("mcp__")]
		externas = len(inventario.herramientas) - len(propias)
		lineas.append("Herramientas (%d): %d propias del agente y %d de servidores MCP." % (
			len(inventario.herramientas), len(propias), externas))
		lineas.append("Propias: " + ", ".join(propias) + ".")
	return "\n".join(lineas).rstrip() + "\n"
