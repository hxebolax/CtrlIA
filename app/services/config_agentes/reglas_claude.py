"""Las reglas de permisos de la propia configuración de Claude Code que valen en una carpeta (AIChat #336).

Solo se leen: Ctrl+IA nunca escribe estos archivos (lo que guarda «Permitir
siempre en esta carpeta» lo escribe Claude Code). Como cada turno lo hace
Claude Code, estas reglas valen en las sesiones de Ctrl+IA igual que en la
terminal y en la app de escritorio. Comprobado en AIChat con Claude Code 2.1.296:

- Las de tu usuario (``~/.claude/settings.json``) valen en todas las carpetas.
- Las de ``.claude/settings.local.json`` de la carpeta, valen.
- Las de ``.claude/settings.json`` compartido de la carpeta, también, pero sus
  reglas «permitir» no cuentan hasta que la carpeta sea de confianza en Claude
  Code («this workspace has not been trusted»); la confianza es por carpeta
  exacta (``projects[<carpeta>].hasTrustDialogAccepted`` en ``~/.claude.json``).
- Las de la carpeta de encima no valen en una subcarpeta.
- Las que pone el administrador del equipo (``managed-settings.json``) valen siempre.

Sin wx: se prueba por separado.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Cada clase de regla, como la dice la lista.
CLASES = (("allow", "Permitido sin preguntar"), ("ask", "Pregunta siempre"), ("deny", "Nunca permitido"))
#: Un archivo de ajustes más grande no se lee: el de un proyecto es pequeño, y uno de un repositorio clonado podría ser cualquier cosa.
TAMANO_MAXIMO = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ReglaClaude:
	clase: str  # allow, ask o deny
	regla: str
	origen: str  # «tu usuario», «el proyecto, compartido»...
	ruta: str
	#: ``None``: depende de una confianza que no se ha podido comprobar.
	aplicada: bool | None = True


def _raiz_claude() -> Path:
	from app.services.historial.claude import raiz_claude

	return raiz_claude()


def archivo_config_claude() -> Path:
	"""``~/.claude.json`` (o el de ``CLAUDE_CONFIG_DIR``): donde Claude Code apunta las carpetas de confianza."""
	otra = os.environ.get("CLAUDE_CONFIG_DIR")
	return Path(otra) / ".claude.json" if otra else Path.home() / ".claude.json"


def archivos_gestionados() -> list[Path]:
	"""Donde puede estar la configuración que impone el administrador del equipo."""
	return [
		Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode" / "managed-settings.json",
		Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ClaudeCode" / "managed-settings.json",  # auditoria: ignorar ruta-no-portable - solo se lee la política de Claude Code; no guarda datos
	]


def archivos_ajustes(carpeta: str) -> list[tuple[str, Path, bool]]:
	"""``(de quién, ruta, ¿compartido del proyecto?)`` de cada archivo que puede valer en ``carpeta``."""
	archivos = [("el administrador del equipo", ruta, False) for ruta in archivos_gestionados()]
	archivos.append(("tu usuario (todas las carpetas)", _raiz_claude() / "settings.json", False))
	if carpeta:
		proyecto = Path(carpeta) / ".claude"
		archivos.append(("el proyecto, compartido", proyecto / "settings.json", True))
		archivos.append(("el proyecto, solo tú", proyecto / "settings.local.json", False))
	return archivos


def carpeta_de_confianza(carpeta: str) -> bool | None:
	"""Si la carpeta exacta es de confianza en Claude Code; ``None`` si no se ha podido leer (lo reescribe a menudo)."""
	try:
		datos = json.loads(archivo_config_claude().read_text(encoding="utf-8"))
	except FileNotFoundError:
		return False
	except (OSError, ValueError):
		log.debug("No se pudo leer la confianza de las carpetas de Claude Code", exc_info=True)
		return None
	proyectos = datos.get("projects") if isinstance(datos, dict) else None
	if not isinstance(proyectos, dict) or not carpeta:
		return False
	buscada = carpeta.replace("\\", "/").rstrip("/").lower()
	for clave, valor in proyectos.items():
		if isinstance(clave, str) and clave.replace("\\", "/").rstrip("/").lower() == buscada:
			return isinstance(valor, dict) and valor.get("hasTrustDialogAccepted") is True
	return False


def _permisos(ruta: Path) -> dict | None:
	"""La sección ``permissions`` de un archivo de ajustes, o ``None`` si no se puede leer."""
	try:
		if ruta.stat().st_size > TAMANO_MAXIMO:
			return None
		datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
	except (OSError, ValueError):
		log.debug("No se pudieron leer los permisos de %s", ruta, exc_info=True)
		return None
	permisos = datos.get("permissions") if isinstance(datos, dict) else None
	return permisos if isinstance(permisos, dict) else None


def reglas_de(carpeta: str) -> tuple[list[ReglaClaude], list[str]]:
	"""Las reglas que valen en ``carpeta`` y los archivos que existen pero no se han podido leer."""
	confianza = carpeta_de_confianza(carpeta)
	encontradas: list[ReglaClaude] = []
	ilegibles: list[str] = []
	for origen, ruta, compartido in archivos_ajustes(carpeta):
		try:
			existe = ruta.is_file()
		except OSError:
			existe = False
		if not existe:
			continue
		permisos = _permisos(ruta)
		if permisos is None:
			ilegibles.append(str(ruta))
			continue
		for clase, _nombre in CLASES:
			entradas = permisos.get(clase)
			for regla in entradas if isinstance(entradas, list) else []:
				# En una sola línea, tenga lo que tenga el archivo de un repositorio clonado.
				texto = " ".join(regla.split()) if isinstance(regla, str) else ""
				if not texto:
					continue
				aplicada: bool | None = True
				if compartido and clase == "allow":
					aplicada = confianza
				encontradas.append(ReglaClaude(clase, texto, origen, str(ruta), aplicada))
	return encontradas, ilegibles


def describir(reglas: list[ReglaClaude], ilegibles: list[str], describir_regla) -> str:
	"""El texto que muestra «Permisos de la sesión», una regla por línea."""
	lineas = [
		"De la propia configuración de Claude Code. Valen en todas las sesiones de Claude en esta carpeta: aquí, "
		"en la app de escritorio y en la terminal. Ctrl+IA solo las lee; se cambian en Herramientas, «Configuración "
		"de los agentes», o en Claude Code con /permissions.",
	]
	aplicadas = [r for r in reglas if r.aplicada is True]
	if not aplicadas:
		lineas.append("La configuración de Claude Code no permite nada más en esta carpeta.")
	for clase, nombre in CLASES:
		for regla in (r for r in aplicadas if r.clase == clase):
			lineas.append(f"{nombre}: {describir_regla(regla.regla)}. Regla: {regla.regla}. De {regla.origen}, {regla.ruta}.")
	esperando = [r for r in reglas if r.aplicada is False]
	if esperando:
		lineas.append(
			"No cuentan hasta que esta carpeta sea de confianza en Claude Code (abre una consola en esta carpeta, "
			"escribe claude y responde que sí cuando pregunte si confías en sus archivos): las reglas «permitir» "
			f"del archivo compartido del proyecto, {esperando[0].ruta}:")
		lineas.extend(f"{describir_regla(r.regla)}. Regla: {r.regla}." for r in esperando)
	dudosas = [r for r in reglas if r.aplicada is None]
	if dudosas:
		lineas.append(
			"Solo cuentan si esta carpeta es de confianza en Claude Code, y no se ha podido comprobar: las reglas "
			f"«permitir» del archivo compartido del proyecto, {dudosas[0].ruta}:")
		lineas.extend(f"{describir_regla(r.regla)}. Regla: {r.regla}." for r in dudosas)
	for ruta in ilegibles:
		lineas.append(f"No se ha podido leer {ruta}, así que sus reglas no salen aquí.")
	return "\n".join(lineas)
