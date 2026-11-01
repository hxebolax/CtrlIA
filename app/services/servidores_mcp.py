"""Estado de los servidores MCP de una sesión, en español y sin wx.

Claude Code lo da con la petición de control ``mcp_status`` (y, sin motivo, en su
``system/init``): nombre, estado (``connected``, ``pending``, ``failed``,
``needs-auth``, ``disabled``), error, herramientas y de dónde viene el servidor.
Aquí se convierte en :class:`ServidorMcp`, que usan los avisos, «Herramientas del
agente» y el diálogo «Servidores MCP de la sesión».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Estados de un servidor, en español.
ESTADOS = {
	"connected": "conectado",
	"pending": "conectándose",
	"failed": "no ha podido arrancar",
	"needs-auth": "necesita que inicies sesión",
	"disabled": "desactivado",
}
#: Estados que dejan sin herramientas a la sesión hasta que se haga algo.
ESTADOS_CON_PROBLEMA = frozenset({"failed", "needs-auth"})
#: De dónde sale un servidor (``scope`` o ``source`` de Claude Code).
ORIGENES = {
	"project": "del proyecto (.mcp.json)",
	"local": "solo tuyo, en esta carpeta",
	"user": "tuyo, en todas tus carpetas",
	"claudeai": "conector de claude.ai",
	"plugin": "de un complemento",
	"managed": "de tu organización",
	"enterprise": "de tu organización",
	"dynamic": "de esta sesión",
	"sdk": "de Ctrl+IA",
}
#: Errores de Claude Code que se pueden explicar mejor (en minúsculas).
MOTIVOS = {
	"connection closed": ("su programa se cierra al arrancar: comprueba en una consola que la orden existe y funciona, y "
		"que no le falta ninguna variable o clave"),
	"request timed out": ("no ha contestado a tiempo: puede que tarde mucho en arrancar (auméntalo en Preferencias, "
		"Agentes) o que esté colgado"),
}
#: Por qué falla un servidor, cuando Claude Code da el código (``error_code``).
MOTIVOS_POR_CODIGO = {
	"CLAUDEAI_BEARER_REJECTED": "claude.ai ha rechazado tu sesión: vuelve a iniciar sesión en Claude Code",
	"FIRST_PARTY_AUTH_REJECTED": "se ha rechazado tu sesión: vuelve a iniciar sesión en Claude Code",
	"APPROVAL_REQUIRED": "es un servidor del proyecto que aún no has aprobado: ejecuta «claude» en esta carpeta y apruébalo",
}
_LARGO_MOTIVO = 300


def _texto(valor: Any) -> str:
	return " ".join(valor.split()) if isinstance(valor, str) else ""


def motivo_legible(error: str, codigo: str = "") -> str:
	"""Por qué falla un servidor, en español si es un error conocido."""
	if codigo in MOTIVOS_POR_CODIGO:
		return MOTIVOS_POR_CODIGO[codigo]
	limpio = _texto(error)
	conocido = MOTIVOS.get(limpio.rstrip(".").lower())
	if conocido:
		return conocido
	return limpio[:_LARGO_MOTIVO]


@dataclass(slots=True)
class ServidorMcp:
	"""Un servidor MCP tal como lo ve la sesión."""

	nombre: str
	estado: str
	motivo: str = ""
	#: Número de herramientas; ``None`` si no se sabe (el ``system/init`` no lo dice).
	herramientas: int | None = None
	#: Clave de ``ORIGENES`` tal como llegó.
	origen: str = ""
	#: Qué se ejecuta o a qué dirección se conecta.
	destino: str = ""

	@property
	def estado_legible(self) -> str:
		return ESTADOS.get(self.estado, self.estado)

	@property
	def origen_legible(self) -> str:
		return ORIGENES.get(self.origen, self.origen)

	@property
	def conector(self) -> bool:
		"""Conector de claude.ai: su sesión se inicia en claude.ai, no con ``claude mcp login``."""
		return self.origen == "claudeai"

	@property
	def con_problema(self) -> bool:
		return self.estado in ESTADOS_CON_PROBLEMA


def _destino(configuracion: Any) -> str:
	if not isinstance(configuracion, dict):
		return ""
	if configuracion.get("url"):
		return _texto(configuracion["url"])
	partes = [configuracion.get("command"), *(configuracion.get("args") or [])]
	return " ".join(str(parte) for parte in partes if parte)


def desde_claude(fila: dict[str, Any]) -> ServidorMcp | None:
	"""Convierte una fila de ``mcp_status`` o del ``system/init``; ``None`` si no tiene nombre."""
	nombre = _texto(fila.get("name"))
	if not nombre:
		return None
	herramientas = fila.get("tools")
	return ServidorMcp(
		nombre=nombre,
		estado=_texto(fila.get("status")),
		motivo=motivo_legible(str(fila.get("error") or ""), _texto(fila.get("error_code"))),
		herramientas=len(herramientas) if isinstance(herramientas, list) else None,
		origen=_texto(fila.get("source")) or _texto(fila.get("scope")),
		destino=_destino(fila.get("config")),
	)


def lista_desde_claude(filas: Any) -> list[ServidorMcp]:
	"""Todas las filas válidas de ``mcp_status``, en el orden en que llegan."""
	servidores = [desde_claude(fila) for fila in filas or [] if isinstance(fila, dict)]
	return [servidor for servidor in servidores if servidor is not None]


def detalle(servidor: ServidorMcp) -> str:
	"""Una frase con su estado para «Herramientas del agente»: «conectado, 3 herramientas»."""
	partes = [servidor.estado_legible]
	if servidor.herramientas:
		partes.append("%d herramientas" % servidor.herramientas if servidor.herramientas != 1 else "1 herramienta")
	if servidor.motivo:
		partes.append("motivo: %s" % servidor.motivo)
	return ", ".join(parte for parte in partes if parte)


def aviso_de_problema(servidor: ServidorMcp, atajo: str = "Ctrl+Alt+M") -> str:
	"""Aviso para la conversación cuando un servidor se queda sin conectar."""
	if servidor.estado == "needs-auth":
		if servidor.conector:
			return ("El conector «%s» de Claude necesita que vuelvas a conectarlo: hazlo en claude.ai, en Ajustes, Conectores."
				% servidor.nombre)
		return ("La herramienta externa «%s» de Claude necesita que inicies sesión en ella. Hazlo en Sesión, «Servidores MCP "
			"de la sesión» (%s), o con «claude mcp login %s» en una consola." % (servidor.nombre, atajo, servidor.nombre))
	texto = "La herramienta externa «%s» de Claude no se ha podido conectar." % servidor.nombre
	if servidor.motivo:
		texto += " Motivo: %s." % servidor.motivo
	return texto + " Puedes reintentarlo en Sesión, «Servidores MCP de la sesión» (%s)." % atajo
