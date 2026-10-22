"""Modo de permisos inicial que cada agente tiene en su propia configuración.

Ctrl+IA arranca los agentes diciéndoles el modo de la sesión (Claude Code con
``--permission-mode``, Codex con su política de aprobación y su sandbox), y
eso manda sobre lo que diga su archivo. Para que «Nueva sesión» respete lo que
la persona eligió en Herramientas, «Configuración de los agentes», aquí se lee
ese valor y se traduce al modo de Ctrl+IA que se propone.

Todas las funciones devuelven ``None`` si el archivo no dice nada (o no se
puede leer): entonces se propone el modo de siempre. Leen archivos pequeños y
no lanzan errores.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services.config_agentes.modelo import AgenteConfigurable, DocumentoConfig, ErrorConfigAgente

log = logging.getLogger(__name__)

#: Alias que Claude Code admite en ``defaultMode``.
_ALIAS_CLAUDE = {"manual": "default"}
#: Modos que Claude Code no acepta desde los archivos del proyecto.
_SOLO_USUARIO_CLAUDE = ("auto", "bypassPermissions")


def _documento(agente: AgenteConfigurable, ambito: str, carpeta: Path | None) -> DocumentoConfig | None:
	"""El archivo de ``ambito`` ya leído, o ``None`` si no existe o no se entiende."""
	try:
		ruta = agente.ambito(ambito).ruta(carpeta)
		if ruta is None:
			return None
		documento = DocumentoConfig(ruta, agente.formato)
	except (ErrorConfigAgente, OSError, StopIteration, ValueError):
		log.debug("No se pudo leer la configuración %s de %s", ambito, agente.id, exc_info=True)
		return None
	return documento if documento.existe else None


def _texto(documento: DocumentoConfig | None, *ruta: str) -> str | None:
	if documento is None:
		return None
	existe, valor = documento.obtener(ruta)
	return valor.strip() if existe and isinstance(valor, str) and valor.strip() else None


def modo_claude(carpeta: Path | None = None) -> str | None:
	"""``permissions.defaultMode`` que aplicaría Claude Code en ``carpeta``.

	Prioridad (de más a menos): proyecto solo tuyo, proyecto compartido y tu
	usuario. Como hace Claude Code, «auto» en un proyecto no vale y deja el modo
	de serie (sin mirar el del usuario), y «bypassPermissions» en un proyecto
	empieza en «Preguntar».
	"""
	from app.services.config_agentes.catalogo_claude import AGENTE, LOCAL, PROYECTO, USUARIO

	ambitos = (LOCAL, PROYECTO, USUARIO) if carpeta is not None else (USUARIO,)
	for ambito in ambitos:
		valor = _texto(_documento(AGENTE, ambito, carpeta), "permissions", "defaultMode")
		if valor is None:
			continue
		valor = _ALIAS_CLAUDE.get(valor, valor)
		if ambito != USUARIO and valor in _SOLO_USUARIO_CLAUDE:
			return None if valor == "auto" else "default"
		return valor
	return None


def _ajustes_codex(documento: DocumentoConfig | None) -> tuple[str | None, str | None]:
	"""``(sandbox_mode, approval_policy)`` de un config.toml, mirando antes el perfil activo."""
	if documento is None:
		return None, None
	perfil = _texto(documento, "profile")
	sandbox = _texto(documento, "profiles", perfil, "sandbox_mode") if perfil else None
	aprobacion = _texto(documento, "profiles", perfil, "approval_policy") if perfil else None
	return sandbox or _texto(documento, "sandbox_mode"), aprobacion or _texto(documento, "approval_policy")


def modo_codex(carpeta: Path | None = None) -> str | None:
	"""Modo de Ctrl+IA que corresponde a ``sandbox_mode`` y ``approval_policy`` de Codex.

	El config.toml del proyecto manda sobre el de tu usuario, clave a clave.
	"""
	from app.services.config_agentes.catalogo_codex import AGENTE, PROYECTO, USUARIO

	sandbox, aprobacion = _ajustes_codex(_documento(AGENTE, USUARIO, carpeta))
	if carpeta is not None:
		sandbox_p, aprobacion_p = _ajustes_codex(_documento(AGENTE, PROYECTO, carpeta))
		sandbox, aprobacion = sandbox_p or sandbox, aprobacion_p or aprobacion
	if sandbox == "read-only":
		return "solo_lectura"
	if sandbox == "danger-full-access":
		return "acceso_total"
	if aprobacion == "never":
		return "sin_preguntas"
	if sandbox == "workspace-write":
		return "espacio_trabajo"
	return None


def modo_antigravity() -> str | None:
	"""Modo de Ctrl+IA que corresponde a ``toolPermission`` de Antigravity CLI."""
	from app.services.config_agentes.catalogo_antigravity import AGENTE

	valor = _texto(_documento(AGENTE, "usuario", None), "toolPermission")
	return {"proceed-in-sandbox": "sandbox", "always-proceed": "sin_preguntas"}.get(valor or "")


def elegir(propuesto: str | None, modos: list[Any], por_defecto: str) -> str:
	"""``propuesto`` si es uno de ``modos`` (``OpcionLista``); si no, ``por_defecto``."""
	return propuesto if propuesto and any(modo.id == propuesto for modo in modos) else por_defecto
