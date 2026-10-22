"""Informe de sesiones: todas las sesiones con su tamaño, sus mensajes y los totales.

Sin interfaz (no importa wx). Sirve para decidir qué ocultar, archivar o
borrar: qué sesiones ocupan más, cuáles tienen más mensajes, de qué agente.

- Sesiones de Ctrl+IA e importadas: recuentos y tamaño salen de la base de
  datos con una sola consulta.
- Sesiones de Claude Code (terminal, escritorio, Cowork): el tamaño es el del
  archivo de historial (barato); los recuentos exigen leerlo entero, así que se
  guardan en memoria por ruta, tamaño y fecha de modificación y el siguiente
  informe es inmediato.
- Hilos de Codex: los mensajes se piden a Codex; el tamaño en disco no está
  disponible (Codex no deja leer sus archivos).
- Conversaciones de la app Antigravity: no se pueden leer; se dice así.

Nunca escribe en los archivos de los agentes. Las exportaciones (Markdown y
CSV) solo llevan datos de cada sesión, nunca el texto de los mensajes.
"""

from __future__ import annotations

import csv
import io
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.almacen import Almacen
from app.services.gestor import FilaSesion, MensajeVista
from app.ui.comun import _, nombre_agente, nombre_estado

log = logging.getLogger(__name__)

#: Cuántas sesiones salen en «Las que más ocupan».
MAS_GRANDES = 10

Progreso = Callable[[int, int], None]


@dataclass(slots=True)
class Recuento:
	mensajes: int = 0
	usuario: int = 0
	asistente: int = 0
	herramientas: int = 0
	cambios: int = 0


@dataclass(slots=True)
class FilaInforme:
	"""Los datos de una sesión en el informe. ``None`` = no disponible."""

	clave: str
	titulo: str
	proveedor: str
	origen: str
	carpeta: str = ""
	grupos: list[str] = field(default_factory=list)
	estado: str = ""
	creada: float = 0.0
	actividad: float = 0.0
	modelo: str = ""
	oculta: bool = False
	archivada: bool = False
	terminada: bool = False
	propia: bool = False
	importada: bool = False
	tamano: int | None = None
	recuento: Recuento | None = None
	#: Archivo del historial (sesiones de Claude Code).
	ruta: str = ""
	#: Por qué faltan datos («No se pudo leer el historial»...). Vacío si todo fue bien.
	problema: str = ""

	@property
	def mensajes(self) -> int | None:
		return self.recuento.mensajes if self.recuento is not None else None


# Recuentos de historiales ya leídos: (ruta, tamaño, modificación) → Recuento.
_cache: dict[tuple[str, int, int], Recuento] = {}
_candado_cache = threading.Lock()


def olvidar_cache() -> None:
	with _candado_cache:
		_cache.clear()


def _contar(mensajes: Sequence[Any]) -> Recuento:
	recuento = Recuento()
	for mensaje in mensajes:
		rol = getattr(mensaje, "rol", "")
		recuento.mensajes += 1
		if rol == "usuario":
			recuento.usuario += 1
		elif rol == "asistente":
			recuento.asistente += 1
		elif rol == "herramienta":
			recuento.herramientas += 1
		recuento.cambios += len(getattr(mensaje, "cambios", None) or []) or (1 if rol == "cambio" else 0)
	return recuento


def recuento_historial(ruta: Path, leer: Callable[[Path], Sequence[Any]] | None = None) -> Recuento:
	"""Cuenta los mensajes de un historial de Claude Code, con caché por tamaño y fecha.

	:raises OSError: no se pudo leer el archivo.
	"""
	estado = ruta.stat()
	clave = (str(ruta), int(estado.st_size), int(estado.st_mtime_ns))
	with _candado_cache:
		guardado = _cache.get(clave)
	if guardado is not None:
		return guardado
	if leer is None:
		from app.services.historial import claude as historial_claude

		leer = historial_claude.leer_mensajes
	recuento = _contar(leer(ruta))
	with _candado_cache:
		for vieja in [c for c in _cache if c[0] == clave[0]]:
			del _cache[vieja]
		_cache[clave] = recuento
	return recuento


def construir(
	filas: Sequence[FilaSesion],
	almacen: Almacen,
	*,
	fuente_externa: Callable[[str], Any] | None = None,
	leer_mensajes: Callable[[str], list[MensajeVista]] | None = None,
	leer_historial: Callable[[Path], Sequence[Any]] | None = None,
	progreso: Progreso | None = None,
	cancelado: Callable[[], bool] | None = None,
) -> list[FilaInforme]:
	"""Calcula el informe de todas las ``filas`` (bloquea: llamar desde un hilo).

	:param fuente_externa: ``GestorSesiones.fuente_externa`` (para la ruta del historial de Claude).
	:param leer_mensajes: ``GestorSesiones.mensajes`` (para los hilos de Codex).
	"""
	cancelado = cancelado or (lambda: False)
	estadisticas = almacen.estadisticas_mensajes()
	resultado: list[FilaInforme] = []
	rollouts: dict[str, Path] | None = None
	for numero, fila in enumerate(filas, start=1):
		if cancelado():
			break
		if progreso is not None:
			progreso(numero, len(filas))
		datos = FilaInforme(
			clave=fila.clave, titulo=fila.titulo, proveedor=fila.proveedor, origen=fila.nombre_origen,
			carpeta=fila.carpeta, grupos=list(fila.grupos), estado=str(fila.estado), creada=fila.creada,
			actividad=fila.actividad, modelo=fila.modelo, oculta=fila.oculta, archivada=fila.archivada,
			terminada=fila.terminada, propia=fila.propia, importada=fila.importada,
		)
		if fila.propia or fila.importada:
			guardadas = estadisticas.get(fila.clave)
			datos.tamano = guardadas.bytes if guardadas else 0
			datos.recuento = Recuento(guardadas.total, guardadas.usuario, guardadas.asistente, guardadas.herramientas,
				guardadas.cambios) if guardadas else Recuento()
		elif fila.clave.startswith("claude:"):
			_datos_claude(datos, fuente_externa, leer_historial)
		elif fila.clave.startswith(("codex:", "ollama:")) and leer_mensajes is not None:
			if fila.clave.startswith("codex:"):
				if rollouts is None:
					from app.services.historial import codex as historial_codex

					rollouts = historial_codex.indice_rollouts()
				ruta = rollouts.get(fila.clave.split(":", 1)[1].lower())
				if ruta is not None:
					datos.ruta = str(ruta)
					try:
						datos.tamano = ruta.stat().st_size
					except OSError:
						log.debug("No se pudo medir %s", ruta, exc_info=True)
			try:
				datos.recuento = _contar(leer_mensajes(fila.clave))
			except Exception:  # noqa: BLE001 - el agente puede no responder; el informe sigue
				log.warning("No se pudieron contar los mensajes de %s", fila.clave, exc_info=True)
				datos.problema = _("No se han podido leer los mensajes")
		else:
			datos.problema = _("Este programa no deja leer sus conversaciones")
		resultado.append(datos)
	return resultado


def _datos_claude(datos: FilaInforme, fuente_externa: Callable[[str], Any] | None,
		leer_historial: Callable[[Path], Sequence[Any]] | None) -> None:
	fuente = fuente_externa(datos.clave) if fuente_externa is not None else None
	ruta = getattr(fuente, "ruta_transcript", None)
	if ruta is None:
		from app.services.historial import claude as historial_claude

		ruta = historial_claude.ruta_transcript(datos.clave.split(":", 1)[1], datos.carpeta)
	if ruta is None or not Path(ruta).is_file():
		datos.problema = _("No se encuentra el archivo del historial")
		return
	ruta = Path(ruta)
	datos.ruta = str(ruta)
	try:
		datos.tamano = ruta.stat().st_size
		datos.recuento = recuento_historial(ruta, leer_historial)
	except Exception:  # noqa: BLE001 - un historial dañado no debe impedir el informe
		log.warning("No se pudo leer el historial %s para el informe", ruta, exc_info=True)
		datos.problema = _("No se pudo leer el historial")


# ── Totales ─────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Totales:
	sesiones: int = 0
	tamano: int = 0
	mensajes: int = 0
	herramientas: int = 0
	sin_tamano: int = 0
	con_problemas: int = 0
	por_agente: Counter = field(default_factory=Counter)
	tamano_por_agente: Counter = field(default_factory=Counter)
	por_origen: Counter = field(default_factory=Counter)
	ocultas: int = 0
	archivadas: int = 0
	terminadas: int = 0
	importadas: int = 0
	mas_grandes: list[FilaInforme] = field(default_factory=list)


def calcular_totales(filas: Sequence[FilaInforme]) -> Totales:
	totales = Totales(sesiones=len(filas))
	for fila in filas:
		agente = nombre_agente(fila.proveedor)
		totales.por_agente[agente] += 1
		totales.por_origen[fila.origen] += 1
		if fila.tamano is None:
			totales.sin_tamano += 1
		else:
			totales.tamano += fila.tamano
			totales.tamano_por_agente[agente] += fila.tamano
		if fila.recuento is not None:
			totales.mensajes += fila.recuento.mensajes
			totales.herramientas += fila.recuento.herramientas
		totales.con_problemas += bool(fila.problema)
		totales.ocultas += fila.oculta
		totales.archivadas += fila.archivada
		totales.terminadas += fila.terminada
		totales.importadas += fila.importada
	totales.mas_grandes = sorted((f for f in filas if f.tamano), key=lambda f: f.tamano or 0, reverse=True)[:MAS_GRANDES]
	return totales


# ── Texto ───────────────────────────────────────────────────────────────────────


def tamano_legible(octetos: int | None) -> str:
	"""«812 bytes», «3,4 KB», «51,2 MB» (coma decimal, como se lee en español)."""
	if octetos is None:
		return _("No disponible")
	if octetos < 1024:
		return _("{n} bytes").format(n=octetos) if octetos != 1 else _("1 byte")
	valor = float(octetos)
	for unidad in ("KB", "MB", "GB", "TB"):
		valor /= 1024
		if valor < 1024 or unidad == "TB":
			return f"{valor:.1f}".replace(".", ",") + " " + unidad
	return str(octetos)  # pragma: no cover - el bucle siempre devuelve


def numero(valor: int | None) -> str:
	"""«9.550» (punto de millares) o «No disponible»."""
	if valor is None:
		return _("No disponible")
	return f"{valor:,}".replace(",", ".")


def fecha(segundos: float) -> str:
	if not segundos:
		return _("Sin fecha")
	return time.strftime("%d/%m/%Y %H:%M", time.localtime(segundos))


def marcas(fila: FilaInforme) -> str:
	"""«oculta, archivada, terminada, importada» (vacío si nada)."""
	partes = [texto for activo, texto in (
		(fila.oculta, _("oculta")), (fila.archivada, _("archivada")), (fila.terminada, _("terminada")),
		(fila.importada, _("importada")),
	) if activo]
	return ", ".join(partes)


def estado_legible(fila: FilaInforme) -> str:
	texto = _("Terminada") if fila.terminada else nombre_estado(fila.estado)
	extra = marcas(fila)
	return f"{texto} ({extra})" if extra and not (fila.terminada and extra == _("terminada")) else texto


def texto_totales(totales: Totales) -> str:
	"""Los totales en líneas cortas, para leerlas con las flechas."""
	lineas = [
		_("Sesiones: {n}.").format(n=numero(totales.sesiones)),
		_("Tamaño total: {t}.").format(t=tamano_legible(totales.tamano)),
		_("Mensajes en total: {n}; acciones de herramientas: {h}.").format(
			n=numero(totales.mensajes), h=numero(totales.herramientas)),
	]
	if totales.sin_tamano:
		lineas.append(_("Sesiones sin tamaño disponible: {n}.").format(n=numero(totales.sin_tamano)))
	if totales.con_problemas:
		lineas.append(_("Sesiones que no se pudieron leer del todo: {n}.").format(n=numero(totales.con_problemas)))
	for agente, cantidad in totales.por_agente.most_common():
		lineas.append(_("{a}: {n} sesiones, {t}.").format(a=agente, n=numero(cantidad),
			t=tamano_legible(totales.tamano_por_agente.get(agente, 0))))
	for origen, cantidad in totales.por_origen.most_common():
		lineas.append(_("Origen {o}: {n} sesiones.").format(o=origen, n=numero(cantidad)))
	lineas.append(_("Ocultas: {o}; archivadas: {a}; terminadas: {t}; importadas: {i}.").format(
		o=numero(totales.ocultas), a=numero(totales.archivadas), t=numero(totales.terminadas), i=numero(totales.importadas)))
	if totales.mas_grandes:
		lineas.append(_("Las que más ocupan:"))
		lineas.extend(f"{i}. {f.titulo}: {tamano_legible(f.tamano)}" for i, f in enumerate(totales.mas_grandes, start=1))
	return "\n".join(lineas)


def resumen_fila(fila: FilaInforme) -> list[tuple[str, str]]:
	"""Todos los datos de una sesión como ``(etiqueta, valor)``."""
	recuento = fila.recuento
	return [
		(_("Agente"), nombre_agente(fila.proveedor)),
		(_("Origen"), fila.origen),
		(_("Estado"), estado_legible(fila)),
		(_("Tamaño"), tamano_legible(fila.tamano)),
		(_("Mensajes"), numero(recuento.mensajes if recuento else None)),
		(_("Tus mensajes"), numero(recuento.usuario if recuento else None)),
		(_("Respuestas"), numero(recuento.asistente if recuento else None)),
		(_("Acciones de herramientas"), numero(recuento.herramientas if recuento else None)),
		(_("Archivos cambiados"), numero(recuento.cambios if recuento else None)),
		(_("Creada"), fecha(fila.creada)),
		(_("Última actividad"), fecha(fila.actividad)),
		(_("Carpeta"), fila.carpeta or _("Sin carpeta")),
		(_("Grupos"), ", ".join(fila.grupos) or _("Ninguno")),
		(_("Modelo"), fila.modelo or _("Predeterminado")),
		(_("Se puede escribir en ella"), _("Sí") if fila.propia else _("No, es de solo lectura")),
		(_("Archivo del historial"), fila.ruta or _("No aplica")),
		(_("Problemas al leerla"), fila.problema or _("Ninguno")),
	]


def a_markdown(filas: Sequence[FilaInforme], titulo: str = "") -> str:
	"""El informe entero, navegable por encabezados (H) en el visor o en un navegador."""
	totales = calcular_totales(filas)
	partes = ["# " + (titulo or _("Informe de sesiones")), "",
		_("Generado el {f}. No incluye el texto de los mensajes.").format(f=fecha(time.time())), "",
		"## " + _("Totales"), ""]
	partes.extend("- " + linea for linea in texto_totales(totales).splitlines())
	partes.extend(["", "## " + _("Sesiones, de la que más ocupa a la que menos"), ""])
	for fila in sorted(filas, key=lambda f: (f.tamano is None, -(f.tamano or 0), -(f.mensajes or 0))):
		partes.extend(["### " + (fila.titulo or _("Sesión sin título")), ""])
		partes.extend(f"- **{etiqueta}:** {valor}" for etiqueta, valor in resumen_fila(fila))
		partes.append("")
	return "\n".join(partes).rstrip("\n") + "\n"


def a_csv(filas: Sequence[FilaInforme]) -> str:
	"""El informe como CSV (punto y coma, para que lo abra bien una hoja de cálculo en español)."""
	salida = io.StringIO()
	escritor = csv.writer(salida, delimiter=";", lineterminator="\r\n")
	escritor.writerow([_("Título"), _("Agente"), _("Origen"), _("Estado"), _("Tamaño en bytes"), _("Mensajes"),
		_("Tus mensajes"), _("Respuestas"), _("Acciones de herramientas"), _("Archivos cambiados"), _("Creada"),
		_("Última actividad"), _("Carpeta"), _("Grupos"), _("Oculta"), _("Archivada"), _("Terminada"), _("Importada"),
		_("Archivo del historial"), _("Problemas")])
	for fila in filas:
		recuento = fila.recuento

		def dato(valor: int | None) -> str:
			return "" if valor is None else str(valor)

		escritor.writerow([
			fila.titulo, nombre_agente(fila.proveedor), fila.origen, estado_legible(fila), dato(fila.tamano),
			dato(recuento.mensajes if recuento else None), dato(recuento.usuario if recuento else None),
			dato(recuento.asistente if recuento else None), dato(recuento.herramientas if recuento else None),
			dato(recuento.cambios if recuento else None), fecha(fila.creada), fecha(fila.actividad), fila.carpeta,
			", ".join(fila.grupos), _("Sí") if fila.oculta else _("No"), _("Sí") if fila.archivada else _("No"),
			_("Sí") if fila.terminada else _("No"), _("Sí") if fila.importada else _("No"), fila.ruta, fila.problema,
		])
	return salida.getvalue()


def guardar(filas: Sequence[FilaInforme], ruta: Path) -> Path:
	"""Guarda el informe en Markdown (``.md``) o CSV (``.csv``), según la extensión."""
	from app.services.exportar import escribir_atomico

	ruta = Path(ruta)
	if ruta.suffix.lower() == ".csv":
		# Con BOM: así la hoja de cálculo reconoce el UTF-8 (acentos y eñes).
		escribir_atomico(ruta, "﻿" + a_csv(filas))
	else:
		if ruta.suffix.lower() not in (".md", ".markdown", ".txt"):
			ruta = ruta.with_suffix(".md")
		escribir_atomico(ruta, a_markdown(filas))
	log.info("Informe de sesiones guardado en %s (%s sesiones)", ruta, len(filas))
	return ruta
