"""Otros agentes de programación con soporte de ACP que Ctrl+IA conoce de serie.

Salen del registro oficial de agentes ACP (https://agentclientprotocol.com,
repositorio ``agentclientprotocol/registry``). Se incluyen los que cumplen el
contrato de Ctrl+IA:

- se lanzan con un ejecutable nativo o un paquete de npm (nunca con un
  lanzador ``.cmd``, que ``cmd.exe`` reinterpretaría);
- pueden funcionar con la cuenta o suscripción del usuario, con modelos
  gratuitos o con modelos locales de Ollama, sin obligar a una clave de API de
  pago.

Como los demás agentes ACP, no heredan las variables de entorno con claves de
API de pago (:data:`CLAVES_DE_PAGO`): Ctrl+IA nunca cobra por uso sin que el
usuario lo sepa. Quien quiera usar una clave puede configurarla dentro del
propio agente.

Se muestran en los menús solo si se activa «Mostrar otros agentes ACP
conocidos» en Preferencias, Agentes. Cualquier otro agente ACP se puede añadir
a mano en «Agentes ACP personalizados».
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.services.agentes.acp import ProveedorAcp, _orden_paquete

#: Variables de entorno con claves de API que se cobran por uso.
CLAVES_DE_PAGO = (
	"ANTHROPIC_API_KEY",
	"ANTHROPIC_AUTH_TOKEN",
	"OPENAI_API_KEY",
	"GEMINI_API_KEY",
	"GOOGLE_API_KEY",
	"OPENROUTER_API_KEY",
	"DASHSCOPE_API_KEY",
	"MISTRAL_API_KEY",
	"GROQ_API_KEY",
	"DEEPSEEK_API_KEY",
	"XAI_API_KEY",
	"AZURE_OPENAI_API_KEY",
)

_NODE = "1) instala Node.js (versión LTS) desde nodejs.org; 2) en una consola escribe: npm install -g %s; "


@dataclass(frozen=True)
class AgenteConocido:
	"""Ficha de un agente ACP conocido."""

	id: str
	nombre: str
	descripcion: str
	web: str
	instrucciones: str
	argumentos: tuple[str, ...]
	nativos: tuple[str, ...] = ()
	paquetes: tuple[tuple[str, str], ...] = ()
	instalacion: str = ""


AGENTES: tuple[AgenteConocido, ...] = (
	AgenteConocido(
		"opencode",
		"OpenCode",
		"Agente de programación de código abierto. Funciona con muchos proveedores: modelos gratuitos, "
		"tu suscripción de GitHub Copilot o ChatGPT, y modelos locales de Ollama.",
		"https://opencode.ai",
		"Para usar OpenCode: " + _NODE % "opencode-ai" + "3) abre opencode una vez en una consola y elige un "
		"proveedor con /connect (por ejemplo, un modelo gratuito o Ollama).",
		("acp",),
		nativos=("opencode",),
		paquetes=(("opencode-ai", "opencode"),),
		instalacion="npm install -g opencode-ai",
	),
	AgenteConocido(
		"goose",
		"Goose",
		"Agente de código abierto de Block. Puede usar modelos locales de Ollama, así que todo se queda en tu equipo.",
		"https://block.github.io/goose/",
		"Para usar Goose: 1) descarga goose para Windows desde su web (la versión de línea de órdenes) y deja goose.exe "
		"en una carpeta del PATH; 2) en una consola escribe: goose configure, y elige un proveedor (por ejemplo, Ollama).",
		("acp",),
		nativos=("goose",),
	),
	AgenteConocido(
		"copilot",
		"GitHub Copilot CLI",
		"El agente de programación de GitHub. Usa tu suscripción de GitHub Copilot (hay un plan gratuito).",
		"https://github.com/features/copilot/cli/",
		"Para usar GitHub Copilot CLI: " + _NODE % "@github/copilot" + "3) abre copilot una vez en una consola e "
		"inicia sesión con /login.",
		("--acp",),
		nativos=("copilot",),
		paquetes=(("@github/copilot", "copilot"),),
		instalacion="npm install -g @github/copilot",
	),
	AgenteConocido(
		"qwen",
		"Qwen Code",
		"Agente de programación de Alibaba (Qwen). Inicia sesión con tu cuenta de Qwen o usa un servidor local "
		"compatible, como Ollama.",
		"https://qwenlm.github.io/qwen-code-docs/",
		"Para usar Qwen Code: " + _NODE % "@qwen-code/qwen-code" + "3) abre qwen una vez en una consola e inicia sesión.",
		("--acp",),
		paquetes=(("@qwen-code/qwen-code", "qwen"),),
		instalacion="npm install -g @qwen-code/qwen-code",
	),
	AgenteConocido(
		"kilo",
		"Kilo Code",
		"Agente de programación de código abierto con modelos gratuitos y de muchos proveedores, incluido Ollama.",
		"https://kilo.ai/",
		"Para usar Kilo: " + _NODE % "@kilocode/cli" + "3) abre kilo una vez en una consola e inicia sesión o elige un proveedor.",
		("acp",),
		nativos=("kilo",),
		paquetes=(("@kilocode/cli", "kilo"),),
		instalacion="npm install -g @kilocode/cli",
	),
	AgenteConocido(
		"cline",
		"Cline",
		"Agente de programación de código abierto. Funciona con tu cuenta de Cline o con modelos locales de Ollama.",
		"https://cline.bot/cli",
		"Para usar Cline: " + _NODE % "cline" + "3) abre cline una vez en una consola e inicia sesión o elige un proveedor.",
		("--acp",),
		paquetes=(("cline", "cline"),),
		instalacion="npm install -g cline",
	),
)


def _orden(agente: AgenteConocido) -> Callable[[], list[str] | None]:
	buscar = _orden_paquete(agente.paquetes, agente.nativos)

	def orden() -> list[str] | None:
		base = buscar()
		return [*base, *agente.argumentos] if base else None

	return orden


def proveedor_conocido(agente: AgenteConocido) -> ProveedorAcp:
	"""Proveedor ACP de una ficha de :data:`AGENTES`."""
	return ProveedorAcp(
		agente.id,
		agente.nombre,
		agente.descripcion,
		_orden(agente),
		web=agente.web,
		instrucciones=agente.instrucciones,
		entorno_quitar=CLAVES_DE_PAGO,
		instalacion=["cmd.exe", "/k", agente.instalacion] if agente.instalacion else None,
	)


def proveedores_conocidos(excluir: Sequence[str] = ()) -> list[ProveedorAcp]:
	"""Un proveedor por cada agente de :data:`AGENTES` (salvo los ids de ``excluir``)."""
	return [proveedor_conocido(agente) for agente in AGENTES if agente.id not in excluir]


#: Nombres cortos para listas y anuncios (los usa ``app.ui.comun.NOMBRES_AGENTE``).
NOMBRES_CORTOS = {agente.id: agente.nombre for agente in AGENTES}
