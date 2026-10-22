"""Paso de los «agentes» del catálogo (1.5.0 y anteriores) a skills, al arrancar.

Hasta la 1.5.0 el catálogo de habilidades con su SKILL.md se llamaba «agentes»;
ahora son skills. Este módulo nombra A PROPÓSITO lo de antes y lo pone al día
sin interfaz, dejando en el registro lo que hace:

1. ``data/agentes_ejemplo`` (del programa) → ``data/skills_ejemplo``: si solo
   existe la vieja, se renombra (pasa al actualizar desde la 1.5.0, cuyo
   actualizador no copia la carpeta nueva); si existen las dos, se borra la
   vieja, que es del programa.
2. ``data/agentes`` (las skills que creó la persona) → ``data/skills``: si no
   existe la nueva, se renombra; si existen las dos, se mueven las subcarpetas
   que no chocan y las demás se quedan donde están, con un aviso en el
   registro. NUNCA se borra nada de la persona.
3. ``data/cache/agentes_sesion`` (los complementos de Claude Code de antes) se
   quita con ``skills_sesion._quitar``, que NO entra en los enlaces: dentro hay
   junctions a las carpetas ORIGINALES de la persona.
4. ``reparar_rutas_catalogo``: las skills del catálogo que aún apunten a las
   carpetas viejas se apuntan a las nuevas (lo mismo que hace la migración 5
   de la base de datos, por si se abrió antes de mover las carpetas).

``main.py`` llama a ``proteger_carpetas`` antes de crear la ventana (y, con
ella, la base de datos y el catálogo). Nunca lanza: lo que falla se registra.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from app.services.almacen import Almacen

log = logging.getLogger(__name__)

#: Carpeta de las skills de ejemplo (del programa) en la 1.5.0 y anteriores, y la de ahora.
EJEMPLOS_ANTIGUA = "agentes_ejemplo"
EJEMPLOS_NUEVA = "skills_ejemplo"
#: Carpeta de las skills creadas por la persona en la 1.5.0 y anteriores, y la de ahora.
PROPIAS_ANTIGUA = "agentes"
PROPIAS_NUEVA = "skills"
#: Caché de los complementos de Claude Code de la 1.5.0 y anteriores (``data/cache/<esto>``).
CACHE_ANTIGUA = "agentes_sesion"
#: Formato y clave de la lista de los catálogos exportados con la 1.5.0 y anteriores (se siguen importando).
FORMATO_JSON_ANTIGUO = "ctrlia-catalogo-agentes"
CLAVE_JSON_ANTIGUA = "agentes"


@dataclass(slots=True)
class ResultadoProteccion:
	"""Lo que se ha hecho (``acciones``) y lo que se ha dejado sin hacer a propósito (``avisos``)."""

	acciones: list[str] = field(default_factory=list)
	avisos: list[str] = field(default_factory=list)


def _existe(ruta: Path) -> bool:
	"""Existe como archivo, carpeta o enlace (aunque el enlace esté roto)."""
	return os.path.lexists(ruta)


def _es_enlace(ruta: Path) -> bool:
	from app.services.skills_sesion import _es_enlace as es_enlace

	return es_enlace(ruta)


def _quitar_sin_seguir_enlaces(carpeta: Path) -> None:
	"""Borra ``carpeta`` sin entrar NUNCA en un enlace ni una junction (solo se quita el enlace)."""
	from app.services.skills_sesion import _quitar

	_quitar(carpeta, carpeta)


def _anotar(resultado: ResultadoProteccion, texto: str, *argumentos: object) -> None:
	log.info(texto, *argumentos)
	resultado.acciones.append(texto % argumentos)


def _avisar(resultado: ResultadoProteccion, texto: str, *argumentos: object) -> None:
	log.warning(texto, *argumentos)
	resultado.avisos.append(texto % argumentos)


def _ejemplos(programa: Path, resultado: ResultadoProteccion) -> None:
	vieja, nueva = programa / EJEMPLOS_ANTIGUA, programa / EJEMPLOS_NUEVA
	if not _existe(vieja):
		return
	if not _existe(nueva):
		os.rename(vieja, nueva)
		_anotar(resultado, "Skills de ejemplo: %s renombrada a %s", vieja, nueva)
		return
	# Las dos: la vieja es del programa (la nueva viene con esta versión).
	_quitar_sin_seguir_enlaces(vieja)
	_anotar(resultado, "Skills de ejemplo: borrada la carpeta antigua %s (ya está %s)", vieja, nueva)


def _propias(datos: Path, resultado: ResultadoProteccion) -> None:
	vieja, nueva = datos / PROPIAS_ANTIGUA, datos / PROPIAS_NUEVA
	if not _existe(vieja):
		return
	if not _existe(nueva):
		os.rename(vieja, nueva)  # una junction se renombra como enlace: su destino no se toca
		_anotar(resultado, "Skills de la persona: %s renombrada a %s", vieja, nueva)
		return
	if _es_enlace(vieja) or _es_enlace(nueva) or not vieja.is_dir() or not nueva.is_dir():
		_avisar(resultado, "Skills de la persona: existen %s y %s y una no es una carpeta normal; no se toca nada", vieja, nueva)
		return
	quedan: list[str] = []
	for hija in sorted(vieja.iterdir(), key=lambda p: p.name.casefold()):
		destino = nueva / hija.name
		if _existe(destino):
			quedan.append(hija.name)
			continue
		try:
			os.rename(hija, destino)
		except OSError:
			log.warning("No se pudo mover %s a %s", hija, destino, exc_info=True)
			quedan.append(hija.name)
			continue
		_anotar(resultado, "Skills de la persona: %s movida a %s", hija, destino)
	if quedan:
		_avisar(resultado, "Skills de la persona: se quedan en %s porque ya hay otras con el mismo nombre en %s (o no se "
			"pudieron mover): %s. Revísalas y muévelas a mano.", vieja, nueva, ", ".join(quedan))
		return
	try:
		os.rmdir(vieja)  # vacía: solo se quita si no queda nada dentro
		_anotar(resultado, "Skills de la persona: quitada la carpeta antigua %s, ya vacía", vieja)
	except OSError:
		log.warning("No se pudo quitar la carpeta vacía %s", vieja, exc_info=True)


def _cache(datos: Path, resultado: ResultadoProteccion) -> None:
	vieja = datos / "cache" / CACHE_ANTIGUA
	if not _existe(vieja):
		return
	_quitar_sin_seguir_enlaces(vieja)
	_anotar(resultado, "Quitada la caché antigua de complementos %s (sin tocar las carpetas enlazadas)", vieja)


def proteger_carpetas(datos: str | Path | None = None, programa: str | Path | None = None) -> ResultadoProteccion:
	"""Pone al día las carpetas de la 1.5.0 y anteriores (pasos 1 a 3 del módulo). Nunca lanza.

	:param datos: la carpeta ``data`` de la persona (por defecto ``ruta_datos()``).
	:param programa: la carpeta ``data`` del programa, donde vienen las skills de
		ejemplo (por defecto ``ruta_base() / "data"``).
	"""
	resultado = ResultadoProteccion()
	try:
		from app.core.paths import ruta_base, ruta_datos

		datos = Path(datos) if datos is not None else ruta_datos()
		programa = Path(programa) if programa is not None else ruta_base() / "data"
	except Exception:  # noqa: BLE001 - sin rutas no se toca nada
		log.warning("No se pudieron calcular las carpetas de datos: no se revisan las de las skills", exc_info=True)
		return resultado
	for paso, carpeta in ((_ejemplos, programa), (_propias, datos), (_cache, datos)):
		try:
			paso(carpeta, resultado)
		except Exception:  # noqa: BLE001 - un paso que falla no impide los demás ni el arranque
			log.warning("No se pudo completar %s en %s", paso.__name__, carpeta, exc_info=True)
			resultado.avisos.append("Falló %s en %s" % (paso.__name__, carpeta))
	return resultado


def reparar_rutas_catalogo(almacen: Almacen) -> int:
	"""Apunta a las carpetas nuevas las skills del catálogo que aún tengan las viejas. Devuelve cuántas."""
	from app.core.migraciones import renombrar_rutas_skills

	try:
		with almacen.bd.transaccion() as conexion:
			return renombrar_rutas_skills(conexion)
	except Exception:  # noqa: BLE001 - nunca impide arrancar
		log.warning("No se pudieron reparar las rutas del catálogo de skills", exc_info=True)
		return 0
