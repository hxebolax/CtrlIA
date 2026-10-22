"""Exportar sesiones a un archivo e importarlas en otro equipo (solo lectura).

Sin interfaz (no importa wx). Sirve para reunir en una sola lista, y poder
buscar en ellas, las sesiones de varios equipos, o como copia de seguridad.

Formato del archivo (``.zip``), versión 1
=========================================

- ``manifiesto.json``::

	{
	  "formato": "ctrlia-sesiones",
	  "version": 1,
	  "aplicacion": "Ctrl+IA 1.1.0",
	  "equipo": "PORTATIL",          # equipo donde se exportó
	  "exportado": 1760000000.0,     # segundos desde 1970 (UTC)
	  "herramientas": true,          # si incluye la salida de las herramientas
	  "sesiones": [{"archivo": "sesiones/0001.json", "markdown": "sesiones/0001 Título.md", "titulo": "..."}]
	}

- ``sesiones/NNNN.json``: una sesión::

	{
	  "clave": "claude:<id>",        # identificador ORIGINAL de la sesión
	  "equipo": "PORTATIL",          # equipo donde nació (se conserva al reexportar)
	  "proveedor": "claude", "titulo": "...", "carpeta": "C:\\\\...", "origen": "terminal",
	  "creada": 0.0, "actividad": 0.0, "modelo": "", "favorita": false, "grupos": ["..."],
	  "mensajes": [{"rol": "usuario", "texto": "...", "fecha": 0.0, "detalle": "", "herramienta": "",
	                "cambios": [{"ruta": "...", "diff": "..."}]}]
	}

- ``sesiones/NNNN Título.md``: la misma sesión en Markdown, para leerla sin Ctrl+IA.

Reglas al leer: se ignora lo que no se conoce (versiones futuras pueden
añadir campos); nunca se extrae nada al disco (se lee cada entrada en memoria,
con un tamaño máximo); la identidad de una sesión es su equipo de origen más
su identificador original, así que importar el mismo archivo otra vez
actualiza las sesiones que han crecido y añade las nuevas sin duplicar nada.
Las sesiones del propio equipo se omiten (ya están en la lista).

Las sesiones importadas se guardan en la base de datos de Ctrl+IA con la clave
``importada:<uuid>``: se leen, se buscan, se exportan y se agrupan como
cualquier otra, pero no se puede escribir en ellas.

Continuar en este equipo (#228, segunda parte): «Continuar aquí» en una
importada crea una sesión NUEVA de Ctrl+IA con el agente y la carpeta que se
elijan; se le copian los mensajes para leerlos y el primer mensaje que se
envía al agente lleva delante ``contexto_para_continuar``: lo que dijeron la
persona y el agente, recortado a ``MAX_CONTEXTO`` caracteres (diciendo si lo
está). La importada no se toca.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import tempfile
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.almacen import PREFIJO_IMPORTADA, Almacen, SesionPropia, nueva_clave_importada
from app.services.gestor import FilaSesion, MensajeVista

log = logging.getLogger(__name__)

FORMATO = "ctrlia-sesiones"
VERSION_FORMATO = 1
MANIFIESTO = "manifiesto.json"
EXTENSION = ".zip"
#: Tamaño máximo de una entrada del archivo (descomprimida).
MAX_ENTRADA = 256 * 1024 * 1024
#: Sesiones como máximo en un archivo.
MAX_SESIONES = 50000
MAX_TITULO = 500

Progreso = Callable[[int, int], None]


@dataclass(slots=True)
class ResultadoExportacion:
	ruta: Path
	sesiones: int = 0
	mensajes: int = 0
	#: Títulos de las sesiones que no se pudieron leer (se exportan sin mensajes).
	fallidas: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResultadoImportacion:
	nuevas: int = 0
	actualizadas: int = 0
	sin_cambios: int = 0
	#: Sesiones de este mismo equipo, que ya están en la lista.
	del_equipo: int = 0
	#: Entradas que no se pudieron leer.
	errores: list[str] = field(default_factory=list)
	equipos: set[str] = field(default_factory=set)
	#: Claves (en este equipo) de las sesiones nuevas o actualizadas.
	claves: list[str] = field(default_factory=list)

	@property
	def total(self) -> int:
		return self.nuevas + self.actualizadas + self.sin_cambios


def nombre_equipo() -> str:
	"""Nombre de este equipo (el que aparece como origen de las sesiones exportadas)."""
	return (os.environ.get("COMPUTERNAME") or platform.node() or "Equipo sin nombre").strip()


def _nombre_seguro(texto: str, maximo: int = 60) -> str:
	limpio = re.sub(r"[^\w\- ]", "_", texto or "", flags=re.UNICODE).strip(" ._")
	return limpio[:maximo].strip() or "sesion"


def _mensaje_a_dict(mensaje: MensajeVista, incluir_herramientas: bool) -> dict[str, Any]:
	detalle = mensaje.detalle
	cambios = [dict(c) for c in (mensaje.cambios or []) if isinstance(c, dict)]
	if not incluir_herramientas and mensaje.rol in ("herramienta", "cambio"):
		detalle = ""
		cambios = [{"ruta": str(c.get("ruta") or "")} for c in cambios]
	datos: dict[str, Any] = {
		"rol": mensaje.rol, "texto": mensaje.texto, "fecha": float(mensaje.fecha or 0),
		"detalle": detalle, "herramienta": mensaje.herramienta,
	}
	if cambios:
		datos["cambios"] = cambios
	return datos


def _identidad_original(fila: FilaSesion, almacen: Almacen | None, equipo: str) -> tuple[str, str, str]:
	"""``(clave original, equipo de origen, origen original)``; una importada conserva los suyos."""
	if fila.importada and almacen is not None:
		sesion = almacen.sesion(fila.clave)
		datos = sesion.extra.get("importada") if sesion is not None else None
		if isinstance(datos, dict) and datos.get("clave_original"):
			return str(datos["clave_original"]), str(datos.get("equipo") or equipo), str(datos.get("origen") or "otra")
	return fila.clave, equipo, fila.origen


def exportar_sesiones(
	filas: Sequence[FilaSesion],
	leer_mensajes: Callable[[str], list[MensajeVista]],
	ruta: Path,
	*,
	almacen: Almacen | None = None,
	incluir_herramientas: bool = True,
	equipo: str | None = None,
	version_app: str = "",
	progreso: Progreso | None = None,
	cancelado: Callable[[], bool] | None = None,
) -> ResultadoExportacion:
	"""Escribe las sesiones en un ZIP (escritura atómica) y devuelve el resumen.

	:param leer_mensajes: normalmente ``GestorSesiones.mensajes`` (bloquea: llamar desde un hilo).
	:raises OSError: no se pudo escribir el archivo.
	"""
	ruta = Path(ruta)
	if not ruta.suffix:
		ruta = ruta.with_suffix(EXTENSION)
	equipo = equipo or nombre_equipo()
	cancelado = cancelado or (lambda: False)
	from app.services.exportar import a_markdown

	resultado = ResultadoExportacion(ruta)
	indice: list[dict[str, Any]] = []
	ruta.parent.mkdir(parents=True, exist_ok=True)
	descriptor, temporal = tempfile.mkstemp(prefix=".exportando-", suffix=".tmp", dir=str(ruta.parent))
	os.close(descriptor)
	try:
		with zipfile.ZipFile(temporal, "w", compression=zipfile.ZIP_DEFLATED) as archivo:
			for numero, fila in enumerate(filas, start=1):
				if cancelado():
					raise InterruptedError("Exportación cancelada")
				if progreso is not None:
					progreso(numero, len(filas))
				try:
					mensajes = list(leer_mensajes(fila.clave))
				except Exception:  # noqa: BLE001 - una sesión ilegible no impide exportar las demás
					log.warning("No se pudieron leer los mensajes de %s para exportarla", fila.clave, exc_info=True)
					resultado.fallidas.append(fila.titulo)
					mensajes = []
				clave, origen_equipo, origen = _identidad_original(fila, almacen, equipo)
				sesion = {
					"clave": clave, "equipo": origen_equipo, "proveedor": fila.proveedor, "titulo": fila.titulo,
					"carpeta": fila.carpeta, "origen": origen, "creada": float(fila.creada or 0),
					"actividad": float(fila.actividad or 0), "modelo": fila.modelo, "favorita": bool(fila.favorita),
					"grupos": list(fila.grupos),
					"mensajes": [_mensaje_a_dict(m, incluir_herramientas) for m in mensajes],
				}
				nombre_json = f"sesiones/{numero:04d}.json"
				nombre_md = f"sesiones/{numero:04d} {_nombre_seguro(fila.titulo)}.md"
				archivo.writestr(nombre_json, json.dumps(sesion, ensure_ascii=False, indent=1))
				vista = [m if incluir_herramientas or m.rol not in ("herramienta", "cambio")
					else MensajeVista(m.rol, m.texto, m.fecha, "", m.herramienta, m.id) for m in mensajes]
				datos = {"agente": fila.proveedor, "carpeta": fila.carpeta, "Equipo": origen_equipo}
				archivo.writestr(nombre_md, a_markdown(vista, fila.titulo, datos))
				indice.append({"archivo": nombre_json, "markdown": nombre_md, "titulo": fila.titulo})
				resultado.sesiones += 1
				resultado.mensajes += len(mensajes)
			manifiesto = {
				"formato": FORMATO, "version": VERSION_FORMATO, "aplicacion": version_app, "equipo": equipo,
				"exportado": time.time(), "herramientas": bool(incluir_herramientas), "sesiones": indice,
			}
			archivo.writestr(MANIFIESTO, json.dumps(manifiesto, ensure_ascii=False, indent=1))
		os.replace(temporal, ruta)
	except BaseException:
		try:
			os.unlink(temporal)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
		raise
	log.info("Exportadas %s sesiones (%s mensajes) en %s", resultado.sesiones, resultado.mensajes, ruta)
	return resultado


# ── Importar ────────────────────────────────────────────────────────────────────


def _leer_json(archivo: zipfile.ZipFile, nombre: str) -> Any:
	try:
		info = archivo.getinfo(nombre)
	except KeyError as error:
		raise ValueError(f"Falta «{nombre}» en el archivo") from error
	if info.file_size > MAX_ENTRADA:
		raise ValueError(f"«{nombre}» es demasiado grande")
	with archivo.open(info) as entrada:
		datos = entrada.read(MAX_ENTRADA + 1)
	if len(datos) > MAX_ENTRADA:
		raise ValueError(f"«{nombre}» es demasiado grande")
	return json.loads(datos.decode("utf-8"))


def _texto(valor: Any, maximo: int = 0) -> str:
	texto = valor if isinstance(valor, str) else ("" if valor is None else str(valor))
	return texto[:maximo] if maximo else texto


def _numero(valor: Any) -> float:
	try:
		return float(valor or 0)
	except (TypeError, ValueError):
		return 0.0


def _mensajes_importados(lista: Any) -> list[dict[str, Any]]:
	mensajes: list[dict[str, Any]] = []
	for mensaje in lista if isinstance(lista, list) else []:
		if not isinstance(mensaje, dict):
			continue
		cambios = [
			{"ruta": _texto(c.get("ruta")), "diff": _texto(c.get("diff"))}
			for c in (mensaje.get("cambios") or []) if isinstance(c, dict)
		]
		mensajes.append({
			"rol": _texto(mensaje.get("rol"), 40) or "asistente", "texto": _texto(mensaje.get("texto")),
			"fecha": _numero(mensaje.get("fecha")), "detalle": _texto(mensaje.get("detalle")),
			"herramienta": _texto(mensaje.get("herramienta"), 200), "datos": {"cambios": cambios} if cambios else {},
		})
	return mensajes


def leer_manifiesto(ruta: Path) -> dict[str, Any]:
	"""Lee y comprueba el manifiesto (sin importar nada).

	:raises ValueError: no es un archivo de sesiones de Ctrl+IA o está dañado.
	"""
	try:
		with zipfile.ZipFile(ruta) as archivo:
			return _comprobar_manifiesto(_leer_json(archivo, MANIFIESTO))
	except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
		raise ValueError("El archivo no es un archivo de sesiones de Ctrl+IA o está dañado.") from error


def _comprobar_manifiesto(manifiesto: Any) -> dict[str, Any]:
	if not isinstance(manifiesto, dict) or manifiesto.get("formato") != FORMATO:
		raise ValueError("El archivo no es un archivo de sesiones de Ctrl+IA.")
	if not isinstance(manifiesto.get("sesiones"), list):
		raise ValueError("El archivo de sesiones está dañado: no tiene la lista de sesiones.")
	if len(manifiesto["sesiones"]) > MAX_SESIONES:
		raise ValueError("El archivo tiene demasiadas sesiones.")
	if int(_numero(manifiesto.get("version"))) > VERSION_FORMATO:
		log.info("Archivo de sesiones de una versión posterior (%s): se lee lo que se conoce", manifiesto.get("version"))
	return manifiesto


def importar_sesiones(
	ruta: Path,
	almacen: Almacen,
	*,
	equipo_local: str | None = None,
	progreso: Progreso | None = None,
) -> ResultadoImportacion:
	"""Importa (o actualiza) las sesiones de un archivo como sesiones de solo lectura.

	:raises ValueError: el archivo no es de sesiones de Ctrl+IA o está dañado.
	:raises OSError: no se pudo leer.
	"""
	equipo_local = (equipo_local or nombre_equipo()).casefold()
	resultado = ResultadoImportacion()
	try:
		with zipfile.ZipFile(ruta) as archivo:
			manifiesto = _comprobar_manifiesto(_leer_json(archivo, MANIFIESTO))
			equipo_archivo = _texto(manifiesto.get("equipo"), 200)
			ya_importadas = almacen.sesiones_importadas()
			entradas = [e for e in manifiesto["sesiones"] if isinstance(e, dict)]
			for numero, entrada in enumerate(entradas, start=1):
				if progreso is not None:
					progreso(numero, len(entradas))
				nombre = _texto(entrada.get("archivo"))
				try:
					datos = _leer_json(archivo, nombre)
					if not isinstance(datos, dict):
						raise ValueError("no es una sesión")
					_importar_una(datos, equipo_archivo, equipo_local, almacen, ya_importadas, resultado)
				except (ValueError, KeyError, UnicodeDecodeError) as error:
					log.warning("No se pudo importar %s: %s", nombre, error)
					resultado.errores.append(_texto(entrada.get("titulo")) or nombre)
	except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
		raise ValueError("El archivo no es un archivo de sesiones de Ctrl+IA o está dañado.") from error
	log.info("Importación de %s: %s nuevas, %s actualizadas, %s sin cambios, %s del propio equipo, %s errores",
		Path(ruta).name, resultado.nuevas, resultado.actualizadas, resultado.sin_cambios, resultado.del_equipo,
		len(resultado.errores))
	return resultado


def _importar_una(datos: dict[str, Any], equipo_archivo: str, equipo_local: str, almacen: Almacen,
		ya_importadas: dict[str, str], resultado: ResultadoImportacion) -> None:
	clave_original = _texto(datos.get("clave"), 300)
	if not clave_original:
		raise ValueError("la sesión no tiene identificador")
	equipo = _texto(datos.get("equipo"), 200) or equipo_archivo or "Equipo sin nombre"
	if equipo.casefold() == equipo_local:
		resultado.del_equipo += 1
		return
	identidad = f"{equipo}|{clave_original}"
	mensajes = _mensajes_importados(datos.get("mensajes"))
	titulo = _texto(datos.get("titulo"), MAX_TITULO) or "Sesión importada"
	actividad = _numero(datos.get("actividad")) or max((m["fecha"] for m in mensajes), default=0.0) or time.time()
	extra = {"importada": {
		"identidad": identidad, "equipo": equipo, "clave_original": clave_original,
		"origen": _texto(datos.get("origen"), 40) or "otra", "importada": time.time(),
	}}
	existente = ya_importadas.get(identidad)
	if existente is not None and almacen.sesion(existente) is not None:
		anteriores = len(almacen.mensajes(existente))
		sesion = almacen.sesion(existente)
		if anteriores == len(mensajes) and sesion is not None and abs(sesion.actividad - actividad) < 1:
			resultado.sin_cambios += 1
			resultado.equipos.add(equipo)
			return
		almacen.actualizar_sesion(existente, titulo=titulo, carpeta=_texto(datos.get("carpeta"), 2000),
			modelo=_texto(datos.get("modelo"), 200), actividad=actividad, extra=extra)
		almacen.reemplazar_mensajes(existente, mensajes)
		resultado.actualizadas += 1
		clave = existente
	else:
		clave = nueva_clave_importada()
		almacen.guardar_sesion(SesionPropia(
			clave=clave, proveedor=_texto(datos.get("proveedor"), 60) or "otro", titulo=titulo,
			carpeta=_texto(datos.get("carpeta"), 2000), modelo=_texto(datos.get("modelo"), 200),
			creada=_numero(datos.get("creada")) or actividad, actividad=actividad, extra=extra,
		))
		almacen.reemplazar_mensajes(clave, mensajes)
		if datos.get("favorita") is True:
			almacen.fijar_metadato(clave, favorita=True)
		ya_importadas[identidad] = clave
		resultado.nuevas += 1
	for nombre_grupo in datos.get("grupos") or []:
		nombre_grupo = " ".join(_texto(nombre_grupo, 80).split())
		if nombre_grupo:
			almacen.anadir_a_grupo(clave, almacen.crear_grupo(nombre_grupo))
	resultado.equipos.add(equipo)
	resultado.claves.append(clave)


def es_importada(clave: str) -> bool:
	return clave.startswith(PREFIJO_IMPORTADA)


# ── Continuar una importada en este equipo (#228, segunda parte) ─────────────────

#: Caracteres como máximo del resumen de la conversación importada que se le pasa al agente.
MAX_CONTEXTO = 40000
#: Caracteres como máximo de cada mensaje dentro del resumen.
MAX_POR_MENSAJE = 6000
#: Nombres de los agentes para el resumen (sin interfaz: no se usa ``app.ui.comun``).
_NOMBRES_AGENTES = {"claude": "Claude Code", "codex": "Codex", "antigravity": "Antigravity", "gemini": "Gemini CLI",
	"ollama": "Ollama"}


@dataclass(slots=True)
class ContextoImportada:
	"""Lo que se pone delante del primer mensaje al agente y cómo ha quedado."""

	texto: str
	mensajes: int = 0
	omitidos: int = 0
	cortados: int = 0

	@property
	def recortada(self) -> bool:
		return bool(self.omitidos or self.cortados)


def contexto_para_continuar(mensajes: Sequence[Any], titulo: str = "", equipo: str = "", agente: str = "",
		maximo: int = MAX_CONTEXTO) -> ContextoImportada:
	"""Resumen de una conversación importada para que el agente de este equipo la siga.

	``mensajes`` son objetos con ``rol`` y ``texto`` (``MensajeGuardado`` o
	``MensajeVista``). Solo entran los de la persona y los del agente. Si no cabe
	en ``maximo`` caracteres, los mensajes muy largos se cortan y, si aun así no
	cabe, se quitan los del medio: se conservan el primero de la persona (suele
	decir la tarea) y los más recientes. El texto dice si va entera o recortada.
	"""
	por_mensaje = max(200, min(MAX_POR_MENSAJE, maximo // 4))
	cortados = 0
	lineas: list[str] = []
	for mensaje in mensajes:
		rol = str(getattr(mensaje, "rol", "") or "")
		texto = str(getattr(mensaje, "texto", "") or "").strip()
		if rol not in ("usuario", "asistente") or not texto:
			continue
		if len(texto) > por_mensaje:
			texto = texto[:por_mensaje].rstrip() + " […]"
			cortados += 1
		lineas.append(("Persona: " if rol == "usuario" else "Agente: ") + texto)
	if not lineas:
		cuerpo: list[str] = []
		omitidos = 0
	else:
		primero, resto = lineas[0], lineas[1:]
		presupuesto = maximo - len(primero)
		elegidos: list[str] = []
		for linea in reversed(resto):
			if presupuesto - len(linea) - 2 < 0:
				break
			elegidos.append(linea)
			presupuesto -= len(linea) + 2
		elegidos.reverse()
		omitidos = len(resto) - len(elegidos)
		cuerpo = [primero]
		if omitidos:
			cuerpo.append(f"[… {omitidos} mensajes del medio omitidos para no alargar demasiado …]")
		cuerpo += elegidos
	nombre_agente = _NOMBRES_AGENTES.get(agente, agente) or "otro agente"
	de_donde = f"en otro equipo ({equipo})" if equipo else "en otro equipo"
	if omitidos or cortados:
		partes = []
		if omitidos:
			partes.append(f"faltan {omitidos} mensajes del medio")
		if cortados:
			partes.append(f"{cortados} mensajes largos van cortados, marcados con «[…]»")
		estado = "RECORTADA (" + "; ".join(partes) + ")"
	else:
		estado = "ENTERA"
	cabecera = (
		f"[Ctrl+IA] Esta conversación empezó {de_donde} con {nombre_agente}"
		+ (f", titulada «{titulo}»" if titulo else "")
		+ ". La persona la exportó a un archivo y la ha traído a este equipo para seguirla contigo, así que tú no la "
		"tienes en tu memoria. Abajo va la conversación anterior, solo lo que dijeron la persona y el agente (sin las "
		f"acciones de las herramientas). Va {estado}. La carpeta de trabajo y los archivos de este equipo pueden no "
		"ser los mismos que entonces: compruébalos antes de dar nada por hecho. Sigue desde donde se quedó."
	)
	if not cuerpo:
		cuerpo = ["(La conversación anterior no tiene mensajes de texto.)"]
	texto = "\n\n".join([cabecera, "--- Conversación anterior ---", *cuerpo, "--- Fin de la conversación anterior ---",
		"Mensaje nuevo de la persona:"])
	return ContextoImportada(texto, len(lineas), omitidos, cortados)
