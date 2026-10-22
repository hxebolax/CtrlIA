"""Dar a cada sesión las skills (habilidades con su SKILL.md) que la persona eligió del catálogo.

Cada agente de IA las recibe a su manera:

- **Claude Code**, en este equipo: de forma nativa. Ctrl+IA monta un complemento
  solo para esa sesión (``--plugin-dir``) en ``data/cache/skills_sesion/<sesión>``
  con un enlace de carpeta (junction de Windows) a cada skill: Claude Code ve
  siempre la versión actual de la carpeta de la persona, sin copiarla, y las
  usa como habilidades (``ctrlia-skills:<nombre>``). Comprobado con Claude Code
  2.1.238. Si no se puede crear el enlace (otro disco sin soporte), se copia.
- **Los demás** (Codex, Antigravity, Gemini y otros agentes ACP): un texto de
  presentación delante del primer mensaje (y cada vez que cambie la lista) con
  el nombre, la descripción y la ruta del SKILL.md de cada skill; el agente lo
  lee él mismo cuando le hace falta, como hace con AGENTS.md.
- **Ollama y los equipos por SSH** no pueden leer los archivos de Windows: el
  texto lleva el contenido del SKILL.md (recortado).

MUY IMPORTANTE: al limpiar la carpeta del complemento, los enlaces se quitan
como enlaces (``os.rmdir``/``os.unlink``) y nunca se recorren: borrar dentro de
un enlace borraría la carpeta ORIGINAL de la persona.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Nombre del complemento de Claude Code (las habilidades salen como «ctrlia-skills:<nombre>»).
NOMBRE_COMPLEMENTO = "ctrlia-skills"
#: Lo que se recorta del contenido de cada SKILL.md cuando hay que mandarlo entero.
MAX_CONTENIDO = 12000
#: Marca del texto de presentación (para reconocerlo y no repetirlo).
CABECERA_PRESENTACION = "[Ctrl+IA] Skills elegidas para esta sesión"
#: Lo que se le dice al agente cuando se quitan todas las skills de la sesión.
AVISO_SIN_SKILLS = "[Ctrl+IA] Ya no hay skills elegidas para esta sesión: deja de usar las de antes."


def _nombre_seguro(nombre: str) -> str:
	limpio = re.sub(r"[^a-z0-9-]+", "-", (nombre or "").lower()).strip("-")
	return limpio[:60] or "skill"


def firma(skills: Sequence[dict[str, Any]]) -> str:
	"""Identifica la lista de skills (para saber si ha cambiado desde la última vez que se le dijo al agente)."""
	partes = sorted("%s|%s" % (s.get("nombre", ""), s.get("carpeta", "")) for s in skills)
	return hashlib.sha1("\n".join(partes).encode("utf-8")).hexdigest()[:16] if partes else ""


def carpeta_complementos() -> Path:
	from app.core.paths import ruta_cache

	return ruta_cache() / "skills_sesion"


def _es_enlace(ruta: Path) -> bool:
	"""Enlace simbólico o junction (``os.path.isjunction`` existe desde Python 3.12)."""
	try:
		if ruta.is_symlink() or os.path.isjunction(ruta):
			return True
		atributos = os.lstat(ruta).st_file_attributes  # type: ignore[attr-defined]
		return bool(atributos & stat.FILE_ATTRIBUTE_REPARSE_POINT)
	except (OSError, AttributeError):
		return False


def _quitar(ruta: Path, base: Path) -> None:
	"""Quita un elemento de la carpeta del complemento sin entrar NUNCA en un enlace."""
	if not ruta.resolve(strict=False).as_posix().lower().startswith(base.resolve().as_posix().lower()) and not _es_enlace(ruta):
		raise ValueError("Ruta fuera de la carpeta de complementos: %s" % ruta)
	if _es_enlace(ruta):
		try:
			os.rmdir(ruta)  # una junction se quita con rmdir y deja intacto su destino
		except OSError:
			os.unlink(ruta)
		return
	if ruta.is_dir():
		for hijo in list(ruta.iterdir()):
			_quitar(hijo, base)
		ruta.rmdir()
	elif ruta.exists():
		ruta.unlink()


def _enlazar(origen: Path, destino: Path) -> str:
	"""Enlaza (junction) o, si no se puede, copia. Devuelve «enlace» o «copia»."""
	try:
		import _winapi

		_winapi.CreateJunction(str(origen), str(destino))
		return "enlace"
	except (ImportError, OSError):
		log.info("No se pudo enlazar %s; se copia", origen, exc_info=True)
	shutil.copytree(origen, destino, symlinks=True)
	return "copia"


def preparar_complemento(clave_sesion: str, skills: Sequence[dict[str, Any]]) -> Path | None:
	"""Monta el complemento de Claude Code de la sesión y devuelve su carpeta (``None`` sin skills válidas)."""
	base = carpeta_complementos()
	carpeta = base / hashlib.sha1(clave_sesion.encode("utf-8")).hexdigest()[:16]
	if carpeta.exists():
		_quitar(carpeta, base)
	validos = [s for s in skills if s.get("carpeta") and (Path(s["carpeta"]) / "SKILL.md").is_file()]
	if not validos:
		return None
	(carpeta / ".claude-plugin").mkdir(parents=True, exist_ok=True)
	(carpeta / ".claude-plugin" / "plugin.json").write_text(json.dumps({
		"name": NOMBRE_COMPLEMENTO, "version": "1.0.0",
		"description": "Skills elegidas en Ctrl+IA para esta sesión.",
	}, ensure_ascii=False), encoding="utf-8")
	habilidades = carpeta / "skills"
	habilidades.mkdir()
	usados: set[str] = set()
	for skill in validos:
		# El nombre de la carpeta: estable aunque la persona cambie el nombre en el catálogo («… (ejemplo)»).
		nombre = _nombre_seguro(Path(skill["carpeta"]).name or str(skill.get("nombre") or ""))
		while nombre in usados:
			nombre += "-2"
		usados.add(nombre)
		_enlazar(Path(skill["carpeta"]), habilidades / nombre)
	return carpeta


def regla_lectura(carpeta: str) -> str:
	"""Regla de Claude Code que deja LEER (solo leer) la carpeta de una skill: ``Read(//e/Skills/x/**)``.

	Claude Code escribe las rutas absolutas de Windows como ``//letra/resto`` con barras normales.
	"""
	texto = str(carpeta or "").strip()
	coincidencia = re.match(r"^([A-Za-z]):[\\/](.+)$", texto)
	if not coincidencia or any(c in texto for c in "()\r\n"):
		return ""
	resto = coincidencia.group(2).replace("\\", "/").strip("/")
	return "Read(//%s/%s/**)" % (coincidencia.group(1).lower(), resto) if resto else ""


def nombres_en_complemento(carpeta: Path) -> list[str]:
	"""Los nombres de las habilidades del complemento (como las ve Claude Code tras «ctrlia-skills:»)."""
	try:
		return sorted(p.name for p in (carpeta / "skills").iterdir())
	except OSError:
		return []


def limpiar_complemento(clave_sesion: str) -> None:
	"""Quita el complemento de una sesión (al borrarla). Los originales no se tocan."""
	base = carpeta_complementos()
	carpeta = base / hashlib.sha1(clave_sesion.encode("utf-8")).hexdigest()[:16]
	try:
		if carpeta.exists():
			_quitar(carpeta, base)
	except (OSError, ValueError):
		log.warning("No se pudo quitar el complemento de skills de %s", carpeta, exc_info=True)


def _contenido(ruta: Path) -> str:
	try:
		texto = ruta.read_text(encoding="utf-8-sig")
	except OSError:
		return ""
	if len(texto) > MAX_CONTENIDO:
		texto = texto[:MAX_CONTENIDO] + "\n[…recortado…]"
	return texto


def presentacion(skills: Sequence[dict[str, Any]], con_contenido: bool = False) -> str:
	"""Texto que se pone delante del mensaje para los agentes sin soporte nativo. ``""`` sin skills."""
	if not skills:
		return ""
	lineas = [CABECERA_PRESENTACION + ". Son instrucciones especializadas (skills) que te da la persona: "
		"cuando la tarea encaje con la descripción de una, o te pida usarla, sigue sus instrucciones."]
	if not con_contenido:
		lineas[0] += " Antes de usar una, lee su SKILL.md entero (y los archivos de su carpeta que cite)."
	for skill in skills:
		nombre = str(skill.get("nombre") or "")
		descripcion = " ".join(str(skill.get("descripcion") or "").split())
		archivo = Path(str(skill.get("skill") or Path(str(skill.get("carpeta") or "")) / "SKILL.md"))
		if con_contenido:
			lineas.append("\n--- Skill «%s» ---\n%s" % (nombre, _contenido(archivo) or descripcion))
		else:
			lineas.append("- %s: %s (SKILL.md en %s)" % (nombre, descripcion, archivo))
	lineas.append("[Fin de las skills. Mi mensaje empieza aquí.]")
	return "\n".join(lineas)
