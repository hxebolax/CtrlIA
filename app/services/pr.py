"""Estado de la PR de una sesión y de sus comprobaciones automáticas (CI), sin interfaz (AIChat #236).

Se usa la herramienta oficial ``gh`` con la sesión que la persona ya tenga
iniciada, solo para LEER: Ctrl+IA nunca cambia nada en GitHub.

- ``consultar_pr(carpeta)``: ``gh pr view --json ...`` en la carpeta de la
  sesión, que elige la PR de la rama actual.
- ``leer_fallo(estado)``: ``gh run view --job <id> --repo dueño/nombre
  --log-failed`` de cada comprobación fallida de GitHub Actions, recortado a
  las líneas de alrededor de cada ``##[error]``.

Formato real de ``statusCheckRollup`` (gh 2.100, comprobado con PR de
TheIdeaPlace/AIChat, cli/cli y vercel/next.js)::

	{"__typename": "CheckRun", "name": "lint", "workflowName": "Lint",
	 "status": "COMPLETED", "conclusion": "FAILURE",
	 "detailsUrl": "https://github.com/cli/cli/actions/runs/33321337572/job/99283726990",
	 "startedAt": "2026-08-30T16:02:57Z", "completedAt": "2026-08-30T16:04:06Z"}

Las de otras aplicaciones (CodeQL) traen ``detailsUrl`` sin ``/actions/``
(``https://github.com/dueño/repo/runs/<id>``) y ``workflowName`` vacío; los
estados clásicos llegan como ``StatusContext`` con ``context``, ``state`` y
``targetUrl``. El registro de ``--log-failed`` tiene una línea por renglón
con ``trabajo<TAB>paso<TAB>marca de tiempo texto`` (el paso suele llegar como
``UNKNOWN STEP``). Un registro caducado devuelve ``HTTP 410``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Campos que se piden a ``gh pr view``.
CAMPOS_PR = "number,title,url,state,isDraft,reviewDecision,statusCheckRollup,headRefName,mergeable"
#: Segundos máximos para ``gh pr view`` y para leer un registro.
TIEMPO_PR = 30.0
TIEMPO_REGISTRO = 60.0
#: Como mucho se leen los registros de estas comprobaciones fallidas.
MAX_TRABAJOS = 3
#: Caracteres máximos del fallo recortado (entre todos los trabajos).
MAX_CARACTERES_FALLO = 12000
#: Líneas antes de cada ``##[error]`` y, si no hay ninguno, líneas finales del registro.
LINEAS_ANTES_DEL_ERROR = 40
LINEAS_FINALES = 60
#: Caracteres máximos de cada línea del registro recortado.
MAX_CARACTERES_LINEA = 400

#: ``ejecutar(orden, carpeta, segundos) -> (código, salida, error)``; se sustituye en las pruebas.
Ejecutor = Callable[[list[str], "Path | None", float], tuple[int, str, str]]

_RE_TRABAJO = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/actions/runs/(\d+)/job/(\d+)")
_RE_RAMA_SIN_PR = re.compile(r'no (?:open )?pull requests? found for branch "([^"]+)"', re.IGNORECASE)
_RE_MARCA_TIEMPO = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ?")
_ERRORES_DE_RED = ("error connecting to", "dial tcp", "connectex", "proxyconnect", "no such host",
	"could not resolve", "i/o timeout", "tls handshake", "connection refused", "connection reset",
	"network is unreachable", "check your internet connection")

EN_CURSO, CORRECTA, FALLIDA, CANCELADA, OMITIDA = "en_curso", "correcta", "fallida", "cancelada", "omitida"
#: Resultado de conjunto de las comprobaciones de una PR.
SIN_COMPROBACIONES, RESULTADO_EN_CURSO, RESULTADO_FALLIDAS, RESULTADO_CORRECTAS = (
	"sin_comprobaciones", "en_curso", "fallidas", "correctas")


class ErrorPR(Exception):
	"""Fallo con un mensaje en español listo para la persona usuaria.

	:param codigo: ``sin_gh``, ``sin_sesion``, ``sin_red``, ``sin_pr``, ``sin_rama``,
		``sin_repositorio``, ``sin_github``, ``tiempo``, ``caducado``, ``en_curso``,
		``sin_fallos`` u ``otro``. La vigilancia en segundo plano decide con él
		cuándo volver a preguntar.
	"""

	def __init__(self, mensaje: str, codigo: str = "otro") -> None:
		super().__init__(mensaje)
		self.codigo = codigo


# ──────────────────────────────────────────────────────────────────────────────
# Datos
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Comprobacion:
	"""Una comprobación de la PR (un trabajo de GitHub Actions u otra aplicación)."""

	nombre: str
	estado: str
	flujo: str = ""
	url: str = ""
	terminada: str = ""
	#: ``dueño/nombre``, ejecución y trabajo de GitHub Actions (vacíos si no es de Actions).
	repositorio: str = ""
	id_ejecucion: str = ""
	id_trabajo: str = ""

	@property
	def nombre_completo(self) -> str:
		return f"{self.nombre} ({self.flujo})" if self.flujo and self.flujo != self.nombre else self.nombre


@dataclass
class EstadoPR:
	"""La PR de la rama de una carpeta, tal como la cuenta ``gh pr view``."""

	numero: int
	titulo: str
	url: str
	estado: str = "OPEN"
	borrador: bool = False
	revision: str = ""
	rama: str = ""
	fusionable: str = "UNKNOWN"
	comprobaciones: list[Comprobacion] = field(default_factory=list)

	def de_estado(self, *estados: str) -> list[Comprobacion]:
		return [c for c in self.comprobaciones if c.estado in estados]

	@property
	def resultado(self) -> str:
		"""``sin_comprobaciones``, ``en_curso`` (alguna sin terminar), ``fallidas`` o ``correctas``."""
		if not self.comprobaciones:
			return SIN_COMPROBACIONES
		if self.de_estado(EN_CURSO):
			return RESULTADO_EN_CURSO
		if self.de_estado(FALLIDA, CANCELADA):
			return RESULTADO_FALLIDAS
		return RESULTADO_CORRECTAS

	@property
	def firma(self) -> tuple[str, tuple[str, ...]]:
		"""Cambia con cada tanda nueva de comprobaciones (un envío nuevo a la rama trae trabajos nuevos)."""
		return self.resultado, tuple(sorted(f"{c.url or c.nombre}={c.estado}" for c in self.comprobaciones))

	@property
	def ultima_terminada(self) -> str:
		"""Marca de tiempo ISO (UTC) de la última comprobación terminada ("" si ninguna)."""
		return max((c.terminada for c in self.comprobaciones if c.terminada), default="")

	def nombres(self, *estados: str, maximo: int = 3) -> str:
		"""Nombres sin repetir de las comprobaciones en esos estados: «lint, pruebas y 2 más»."""
		unicos = list(dict.fromkeys(c.nombre for c in self.de_estado(*estados)))
		if len(unicos) > maximo:
			return ", ".join(unicos[:maximo]) + f" y {len(unicos) - maximo} más"
		if len(unicos) > 1:
			return ", ".join(unicos[:-1]) + " y " + unicos[-1]
		return unicos[0] if unicos else ""

	@property
	def nombre_estado(self) -> str:
		if self.estado == "MERGED":
			return "fusionada"
		if self.estado == "CLOSED":
			return "cerrada sin fusionar"
		return "borrador" if self.borrador else "abierta"

	@property
	def nombre_revision(self) -> str:
		return {
			"APPROVED": "aprobada",
			"CHANGES_REQUESTED": "piden cambios",
			"REVIEW_REQUIRED": "pendiente de revisión",
		}.get(self.revision, "no se exige revisión")

	@property
	def nombre_fusionable(self) -> str:
		return {
			"MERGEABLE": "sí, sin conflictos",
			"CONFLICTING": "no, tiene conflictos con la rama de destino",
		}.get(self.fusionable, "todavía no se sabe")

	def resumen_comprobaciones(self) -> str:
		"""Una frase: «las comprobaciones han fallado: lint», «3 de 8 terminadas»..."""
		resultado = self.resultado
		if resultado == SIN_COMPROBACIONES:
			return "sin comprobaciones automáticas"
		if resultado == RESULTADO_EN_CURSO:
			total = len(self.comprobaciones)
			hechas = total - len(self.de_estado(EN_CURSO))
			frase = f"comprobaciones en curso, {hechas} de {total} terminadas"
			if self.de_estado(FALLIDA):
				frase += f"; ya ha fallado {self.nombres(FALLIDA)}"
			return frase
		if resultado == RESULTADO_FALLIDAS:
			fallidas = self.nombres(FALLIDA) or self.nombres(CANCELADA)
			return f"las comprobaciones han fallado: {fallidas}"
		return "todas las comprobaciones han pasado"

	def resumen(self) -> str:
		"""Una línea para Propiedades y «Estado del turno»."""
		frase = f"PR {self.numero} ({self.nombre_estado}): {self.resumen_comprobaciones()}"
		if self.revision == "CHANGES_REQUESTED":
			frase += "; en la revisión piden cambios"
		elif self.revision == "APPROVED":
			frase += "; revisión aprobada"
		return frase + "."

	def texto_completo(self) -> str:
		"""Todo lo que se sabe de la PR, para el visor de texto."""
		lineas = [
			f"PR {self.numero}: {self.titulo}",
			f"Enlace: {self.url}",
			f"Rama: {self.rama or 'desconocida'}",
			f"Estado: {self.nombre_estado}",
			f"Revisión: {self.nombre_revision}",
		]
		if self.estado == "OPEN":
			lineas.append(f"Se puede fusionar: {self.nombre_fusionable}")
		total = len(self.comprobaciones)
		if not total:
			lineas.append("Comprobaciones automáticas: ninguna.")
			return "\n".join(lineas)
		cuentas = []
		for estado, singular, plural in ((FALLIDA, "fallida", "fallidas"), (CANCELADA, "cancelada", "canceladas"),
				(EN_CURSO, "en curso", "en curso"), (CORRECTA, "correcta", "correctas"),
				(OMITIDA, "omitida", "omitidas")):
			n = len(self.de_estado(estado))
			if n:
				cuentas.append(f"{n} {singular if n == 1 else plural}")
		lineas.append("")
		lineas.append(f"Comprobaciones: {total} en total: {', '.join(cuentas)}.")
		lineas.append(f"Resultado: {self.resumen_comprobaciones()}.")
		for estado, titulo in ((FALLIDA, "Fallidas"), (CANCELADA, "Canceladas"), (EN_CURSO, "En curso"),
				(CORRECTA, "Correctas"), (OMITIDA, "Omitidas")):
			grupo = self.de_estado(estado)
			if grupo:
				lineas.append("")
				lineas.append(f"{titulo}:")
				lineas.extend(f"- {c.nombre_completo}" for c in grupo)
		if self.de_estado(FALLIDA, CANCELADA):
			lineas.append("")
			lineas.append("Para leer por qué han fallado: «Leer el fallo de las comprobaciones», en el menú "
				"de la sesión, «PR y comprobaciones».")
		return "\n".join(lineas)


# ──────────────────────────────────────────────────────────────────────────────
# Lectura del JSON de gh
# ──────────────────────────────────────────────────────────────────────────────


def _estado_check_run(datos: dict[str, Any]) -> str:
	if str(datos.get("status") or "").upper() != "COMPLETED":
		return EN_CURSO
	conclusion = str(datos.get("conclusion") or "").upper()
	if conclusion in ("SUCCESS", "NEUTRAL"):
		return CORRECTA
	if conclusion == "SKIPPED":
		return OMITIDA
	if conclusion == "CANCELLED":
		return CANCELADA
	# FAILURE, TIMED_OUT, ACTION_REQUIRED, STARTUP_FAILURE, STALE.
	return FALLIDA


def _estado_contexto(datos: dict[str, Any]) -> str:
	estado = str(datos.get("state") or "").upper()
	if estado == "SUCCESS":
		return CORRECTA
	if estado in ("FAILURE", "ERROR"):
		return FALLIDA
	return EN_CURSO  # PENDING, EXPECTED


def comprobacion_desde_json(datos: dict[str, Any]) -> Comprobacion:
	"""Una entrada de ``statusCheckRollup`` (``CheckRun`` o ``StatusContext``)."""
	if datos.get("__typename") == "StatusContext" or ("context" in datos and "status" not in datos):
		url = str(datos.get("targetUrl") or "")
		return Comprobacion(nombre=str(datos.get("context") or "sin nombre"), estado=_estado_contexto(datos), url=url,
			terminada=str(datos.get("startedAt") or datos.get("createdAt") or "") if _estado_contexto(datos) != EN_CURSO else "")
	url = str(datos.get("detailsUrl") or "")
	comprobacion = Comprobacion(
		nombre=str(datos.get("name") or "sin nombre"),
		estado=_estado_check_run(datos),
		flujo=str(datos.get("workflowName") or ""),
		url=url,
		terminada=str(datos.get("completedAt") or "") if str(datos.get("status") or "").upper() == "COMPLETED" else "",
	)
	coincidencia = _RE_TRABAJO.search(url)
	if coincidencia:
		comprobacion.repositorio, comprobacion.id_ejecucion, comprobacion.id_trabajo = coincidencia.groups()
	return comprobacion


def desde_json(datos: dict[str, Any]) -> EstadoPR:
	"""``EstadoPR`` a partir de lo que devuelve ``gh pr view --json CAMPOS_PR``."""
	if not isinstance(datos, dict) or not datos.get("number"):
		raise ErrorPR("La respuesta de GitHub no se pudo leer.")
	comprobaciones = [comprobacion_desde_json(c) for c in datos.get("statusCheckRollup") or [] if isinstance(c, dict)]
	return EstadoPR(
		numero=int(datos["number"]),
		titulo=str(datos.get("title") or ""),
		url=str(datos.get("url") or ""),
		estado=str(datos.get("state") or "OPEN").upper(),
		borrador=bool(datos.get("isDraft")),
		revision=str(datos.get("reviewDecision") or "").upper(),
		rama=str(datos.get("headRefName") or ""),
		fusionable=str(datos.get("mergeable") or "UNKNOWN").upper(),
		comprobaciones=comprobaciones,
	)


# ──────────────────────────────────────────────────────────────────────────────
# gh
# ──────────────────────────────────────────────────────────────────────────────


def buscar_gh() -> Path | None:
	from app.services import git

	return git.buscar_gh()


def es_repositorio(carpeta: str | Path) -> bool:
	"""``True`` si ``carpeta`` está dentro de un repositorio de git (busca ``.git`` hacia arriba, sin lanzar git)."""
	try:
		ruta = Path(carpeta)
		if not str(carpeta) or not ruta.is_dir():
			return False
		ruta = ruta.resolve()
		return any((parte / ".git").exists() for parte in (ruta, *ruta.parents))
	except OSError:
		return False


def _entorno() -> dict[str, str]:
	entorno = dict(os.environ)
	# Nunca preguntar nada ni abrir un paginador: no hay nadie al otro lado de la consola.
	entorno.update({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "GH_PAGER": "", "PAGER": "",
		"NO_COLOR": "1", "CLICOLOR": "0", "GH_SPINNER_DISABLED": "1"})
	return entorno


def ejecutar_gh(orden: list[str], carpeta: Path | None, tiempo: float) -> tuple[int, str, str]:
	"""Ejecuta ``gh`` en ``carpeta`` sin ventana ni consola; ``(código, salida, error)``."""
	resultado = subprocess.run(  # noqa: S603 - orden construida por la aplicación, sin shell
		orden,
		cwd=str(carpeta) if carpeta else None,
		capture_output=True,
		timeout=tiempo,
		stdin=subprocess.DEVNULL,
		env=_entorno(),
		creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
		check=False,
	)
	return (resultado.returncode, (resultado.stdout or b"").decode("utf-8", "replace"),
		(resultado.stderr or b"").decode("utf-8", "replace"))


def _ultima_linea(texto: str) -> str:
	lineas = [linea.strip() for linea in texto.strip().splitlines() if linea.strip()]
	return lineas[-1] if lineas else "error desconocido"


def explicar_error(codigo: int, error: str) -> ErrorPR:
	"""Convierte lo que dice ``gh`` por su salida de errores en un ``ErrorPR`` en español."""
	texto = error.lower()
	coincidencia = _RE_RAMA_SIN_PR.search(error)
	if coincidencia:
		return ErrorPR(f"No hay ninguna PR para la rama «{coincidencia.group(1)}».", "sin_pr")
	if "no pull requests found" in texto or "no open pull requests" in texto:
		return ErrorPR("No hay ninguna PR para la rama actual.", "sin_pr")
	if codigo == 4 or "gh auth login" in texto or "http 401" in texto or "bad credentials" in texto:
		return ErrorPR("No has iniciado sesión en GitHub con la herramienta gh. Abre una consola y ejecuta: "
			"gh auth login", "sin_sesion")
	if any(parte in texto for parte in _ERRORES_DE_RED):
		return ErrorPR("No se pudo conectar con GitHub. Comprueba la conexión a Internet.", "sin_red")
	if "not on any branch" in texto or "could not determine current branch" in texto:
		return ErrorPR("La carpeta no está en ninguna rama (está en un commit suelto), así que no tiene PR.",
			"sin_rama")
	if "not a git repository" in texto:
		return ErrorPR("La carpeta de la sesión no es un repositorio de git.", "sin_repositorio")
	if "no git remotes" in texto or "none of the git remotes" in texto:
		return ErrorPR("El repositorio de la carpeta no está conectado con GitHub (no tiene un remoto de GitHub).",
			"sin_github")
	if "http 410" in texto or "has expired" in texto:
		return ErrorPR("El registro de esa comprobación ya no está en GitHub: se borra pasado un tiempo "
			"(normalmente 90 días). Abre la PR en el navegador para ver lo que quede.", "caducado")
	if "still in progress" in texto:
		return ErrorPR("Esa comprobación todavía está en marcha; su registro estará cuando termine.", "en_curso")
	if "http 404" in texto:
		return ErrorPR("GitHub no encuentra esa PR o esa comprobación, o no tienes acceso a ella.", "otro")
	return ErrorPR("GitHub respondió con un error: " + _ultima_linea(error), "otro")


def _correr(orden_sin_gh: list[str], carpeta: Path | None, tiempo: float, ejecutar: Ejecutor | None,
		gh: Path | None) -> str:
	if ejecutar is None:
		gh = gh or buscar_gh()
		if gh is None:
			raise ErrorPR("La herramienta de GitHub (gh) no está instalada. Descárgala de cli.github.com e inicia "
				"sesión con: gh auth login", "sin_gh")
		ejecutar = ejecutar_gh
		orden = [str(gh), *orden_sin_gh]
	else:
		orden = [str(gh or "gh"), *orden_sin_gh]
	try:
		codigo, salida, error = ejecutar(orden, carpeta, tiempo)
	except subprocess.TimeoutExpired as fallo:
		raise ErrorPR(f"GitHub no respondió en {int(tiempo)} segundos. Inténtalo más tarde.", "tiempo") from fallo
	except FileNotFoundError as fallo:
		raise ErrorPR("La herramienta de GitHub (gh) no está instalada. Descárgala de cli.github.com.", "sin_gh") from fallo
	except OSError as fallo:
		raise ErrorPR(f"No se pudo ejecutar la herramienta de GitHub (gh): {fallo}", "otro") from fallo
	if codigo != 0:
		raise explicar_error(codigo, error)
	return salida


def consultar_pr(carpeta: str | Path, ejecutar: Ejecutor | None = None, gh: Path | None = None,
		tiempo: float = TIEMPO_PR) -> EstadoPR:
	"""La PR de la rama en la que está ``carpeta``. Lanza ``ErrorPR`` con un mensaje claro. Llamar desde un hilo."""
	import json

	ruta = Path(carpeta)
	if not str(carpeta) or not ruta.is_dir():
		raise ErrorPR("La carpeta de la sesión no existe o no se conoce.", "sin_repositorio")
	if not es_repositorio(ruta):
		raise ErrorPR("La carpeta de la sesión no es un repositorio de git.", "sin_repositorio")
	salida = _correr(["pr", "view", "--json", CAMPOS_PR], ruta, tiempo, ejecutar, gh)
	try:
		datos = json.loads(salida)
	except ValueError as fallo:
		raise ErrorPR("La respuesta de GitHub no se pudo leer.") from fallo
	return desde_json(datos)


# ──────────────────────────────────────────────────────────────────────────────
# Registro del fallo
# ──────────────────────────────────────────────────────────────────────────────


def _limpiar_linea(linea: str) -> tuple[str, str]:
	"""``(paso, texto)`` de una línea de ``--log-failed`` sin trabajo ni marca de tiempo."""
	partes = linea.split("\t", 2)
	paso, texto = ("", linea) if len(partes) < 3 else (partes[1].strip(), partes[2])
	texto = _RE_MARCA_TIEMPO.sub("", texto.lstrip("﻿")).rstrip()
	if paso.upper() == "UNKNOWN STEP":
		paso = ""
	return paso, texto


def recortar_registro(registro: str, maximo: int = MAX_CARACTERES_FALLO) -> str:
	"""Las líneas que explican el fallo: las de antes de cada ``##[error]`` o, si no hay, las últimas."""
	limpias: list[tuple[str, str]] = []
	for linea in registro.splitlines():
		paso, texto = _limpiar_linea(linea)
		if texto.startswith("##[endgroup]") or not texto.strip():
			continue
		if texto.startswith("##[group]"):
			texto = texto[len("##[group]"):]
		limpias.append((paso, texto))
	errores = [i for i, (_paso, texto) in enumerate(limpias) if "##[error]" in texto]
	if errores:
		elegidas: set[int] = set()
		for indice in errores:
			elegidas.update(range(max(0, indice - LINEAS_ANTES_DEL_ERROR), indice + 1))
		indices = sorted(elegidas)
	else:
		indices = list(range(max(0, len(limpias) - LINEAS_FINALES), len(limpias)))
	salida: list[str] = []
	anterior, paso_anterior = -2, None
	for indice in indices:
		paso, texto = limpias[indice]
		if indice != anterior + 1 and salida:
			salida.append("[...]")
		if paso and paso != paso_anterior:
			salida.append(f"Paso: {paso}")
			paso_anterior = paso
		texto = texto.replace("##[error]", "Error: ").replace("##[warning]", "Aviso: ")
		# Una línea enorme (un JSON de resultados) no debe comerse todo el espacio.
		salida.append(texto if len(texto) <= MAX_CARACTERES_LINEA else texto[:MAX_CARACTERES_LINEA] + " [...]")
		anterior = indice
	texto = "\n".join(salida)
	if len(texto) > maximo:
		texto = "[...]\n" + texto[-maximo:].split("\n", 1)[-1]
	return texto


def leer_fallo(estado: EstadoPR, ejecutar: Ejecutor | None = None, gh: Path | None = None,
		maximo: int = MAX_CARACTERES_FALLO) -> str:
	"""Texto con el fallo de cada comprobación fallida (registro recortado). Lanza ``ErrorPR``. Llamar desde un hilo."""
	fallidas = estado.de_estado(FALLIDA) or estado.de_estado(CANCELADA)
	if not fallidas:
		if estado.resultado == RESULTADO_EN_CURSO:
			raise ErrorPR(f"Las comprobaciones de la PR {estado.numero} siguen en curso y ninguna ha fallado todavía.",
				"sin_fallos")
		raise ErrorPR(f"No hay comprobaciones fallidas en la PR {estado.numero}.", "sin_fallos")
	partes: list[str] = []
	trabajos_vistos: set[str] = set()
	leidos = 0
	por_trabajo = max(2000, maximo // min(MAX_TRABAJOS, len(fallidas)))
	for comprobacion in fallidas:
		titulo = f"Comprobación «{comprobacion.nombre_completo}»"
		if not comprobacion.id_trabajo:
			partes.append(f"{titulo}: ha fallado. No es de GitHub Actions; sus detalles están en: "
				f"{comprobacion.url or 'enlace desconocido'}")
			continue
		if comprobacion.id_trabajo in trabajos_vistos:
			continue
		trabajos_vistos.add(comprobacion.id_trabajo)
		if leidos >= MAX_TRABAJOS:
			partes.append(f"{titulo}: también ha fallado (su registro, en {comprobacion.url}).")
			continue
		leidos += 1
		try:
			registro = _correr(["run", "view", "--job", comprobacion.id_trabajo, "--repo", comprobacion.repositorio,
				"--log-failed"], None, TIEMPO_REGISTRO, ejecutar, gh)
		except ErrorPR as error:
			if error.codigo in ("sin_gh", "sin_sesion", "sin_red"):
				raise
			partes.append(f"{titulo}: {error}")
			continue
		recortado = recortar_registro(registro, por_trabajo)
		partes.append(f"{titulo}:\n{recortado}" if recortado else f"{titulo}: el registro está vacío.")
	return "\n\n".join(partes)


def mensaje_para_arreglar(estado: EstadoPR, fallo: str) -> str:
	"""Mensaje para el agente con el fallo, para que la persona lo revise y lo envíe."""
	nombres = estado.nombres(FALLIDA, maximo=6) or estado.nombres(CANCELADA, maximo=6)
	rama = f", rama {estado.rama}" if estado.rama else ""
	return (
		f"Las comprobaciones automáticas (CI) de la PR {estado.numero} («{estado.titulo}»{rama}) han fallado: "
		f"{nombres}.\n\nEste es el registro del fallo, recortado:\n\n```\n{fallo.strip()}\n```\n\n"
		"Busca la causa, corrígela y sube los cambios a la misma rama para que las comprobaciones vuelvan a pasar. "
		f"La PR está en {estado.url}."
	)
