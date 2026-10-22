"""Configuración propia de cada agente: sus archivos ``settings.json`` o ``config.toml``.

Cada agente (Claude Code, Codex, Gemini CLI, Antigravity CLI) guarda sus
preferencias en archivos propios, con ámbitos (tu usuario, un proyecto...).
Este paquete los describe (``catalogo_*``: qué opciones hay, de qué tipo, qué
valores admiten y qué hacen) y los lee y escribe sin perder nada de lo que
Ctrl+IA no conoce (``modelo``).

Uso::

	from app.services.config_agentes import agentes_configurables
	agente = agentes_configurables()["claude"]
"""

from __future__ import annotations

from app.services.config_agentes.modelo import AgenteConfigurable


def agentes_configurables() -> dict[str, AgenteConfigurable]:
	"""Los agentes cuya configuración se puede editar, en el orden del menú."""
	from app.services.config_agentes import catalogo_antigravity, catalogo_claude, catalogo_codex, catalogo_gemini

	agentes = (catalogo_claude.AGENTE, catalogo_codex.AGENTE, catalogo_gemini.AGENTE, catalogo_antigravity.AGENTE)
	return {agente.id: agente for agente in agentes}
