"""Diálogo «Listas de la sesión».

Trece páginas (``wx.Notebook``), cada una con una lista accesible:

- **Bloques de código**: Lenguaje, Primera línea, Líneas.
- **Enlaces**: Texto, Dirección, Tipo (web o archivo local, y si existe).
- **Archivos cambiados**: Archivo, Cambios (+añadidas/−quitadas), también los
  de los subagentes.
- **Órdenes ejecutadas**: Orden, Resultado.
- **Tareas del agente**: Tarea, Estado (pendiente, en curso, hecha).
- **Subagentes**: Subagente, Tipo, Estado, Pasos, Ediciones.
- **Hallazgos de la revisión**: Hallazgo, Archivo, Línea, Categoría,
  Veredicto, Resultado.
- **Decisiones**: Decisión, Tipo, Tu respuesta: las preguntas que hizo el
  agente y los planes que propuso, con lo que respondiste (solo en las
  sesiones de Ctrl+IA, de lo que guarda el gestor al responder).
- **Errores**: Qué falló, Error, Tipo: herramientas que fallaron (también las
  que rechazaste) y avisos de error.
- **Commits**: Mensaje, Hash, Rama, Resultado: órdenes ``git commit`` y
  ``git push`` y los commits que salen en otras órdenes de git.
- **Incidencias y PR**: Referencia, Tipo, Dónde se cita (GitHub).
- **Archivos leídos**: Archivo, Veces, Con (herramienta u orden).
- **Tus mensajes**: Mensaje, Fecha.

Encima de las páginas:

- **Mostrar**: «Toda la sesión» o «Este mensaje o turno». En los bloques de
  código y los enlaces, «este mensaje» es el que estaba elegido en la lista de
  mensajes al abrir el diálogo; en las demás páginas, su turno (desde tu
  mensaje hasta el siguiente tuyo). Se recuerda (``listas.ambito``).
- **Filtrar**: reduce la lista de la página visible a lo que contiene todas
  las palabras escritas, sin distinguir mayúsculas ni tildes. Cada página
  recuerda su filtro; la etiqueta de la lista dice entonces «n de m».

Teclas (también en el menú contextual de cada lista, con la tecla
Aplicaciones o Mayús+F10):

- Ctrl+Tab / Ctrl+Mayús+Tab: página siguiente / anterior (lo hace el Notebook;
  desde el filtro o «Mostrar», el diálogo).
- Ctrl+1 a Ctrl+9 y Ctrl+0: van directamente a las diez primeras páginas. En
  estas listas sustituyen a la lectura de la columna N de ``ListaAccesible``
  (las columnas se siguen recorriendo con izquierda y derecha).
- Ctrl+F: ir al filtro. En el filtro, Escape lo vacía (si ya está vacío,
  cierra) y Flecha abajo o Intro llevan a la lista.
- Ctrl+T: cambiar entre toda la sesión y este mensaje o turno.
- Intro: acción principal de la página.
- Ctrl+C: copiar; Ctrl+Mayús+C: copiar el contenido (archivo, salida, detalle
  del error o mensaje del commit).
- Ctrl+S: guardar el bloque de código (o el diff) como archivo.
- Ctrl+L: leer el archivo entero; Ctrl+O: abrirlo con su programa de Windows;
  Ctrl+E: abrirlo en el editor de código; Ctrl+Mayús+E: mostrarlo en el Explorador.
- Ctrl+R: preparar un mensaje para corregir un hallazgo; en «Tus mensajes»,
  citar el mensaje en la respuesta.
- Alt+Intro: cerrar e ir al mensaje (``self.indice_mensaje`` queda con su índice).

Los datos de la sesión (carpeta, transcript de Claude, editor, si es propia,
mensaje elegido y ámbito guardado) se deducen de la ventana principal si no
se pasan.
"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import wx

from app.services import extraccion
from app.services.exportar import escribir_atomico
from app.services.gestor import MensajeVista, coincide
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import enfocar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _, primera_linea
from app.ui.visor_ayuda import mostrar_documento

log = logging.getLogger(__name__)

#: Identificadores de las páginas, en orden (Ctrl+1 a Ctrl+9 y Ctrl+0 las diez primeras).
PAGINAS = (
	"codigo", "enlaces", "archivos", "ordenes", "tareas", "subagentes", "hallazgos", "decisiones",
	"errores", "commits", "incidencias", "leidos", "mios",
)

#: Páginas con atajo propio (Ctrl+1 … Ctrl+9, Ctrl+0); las demás, con Ctrl+Tab.
PAGINAS_CON_ATAJO = 10

#: Páginas cuyos elementos son (o señalan) un archivo local.
PAGINAS_CON_ARCHIVO = ("enlaces", "archivos", "hallazgos", "leidos")

#: Páginas de cosas que están en el texto de un mensaje: con el ámbito «este
#: mensaje o turno» muestran solo las del mensaje elegido (las demás, las de su turno).
PAGINAS_DE_MENSAJE = ("codigo", "enlaces")

#: Ámbitos: toda la sesión o el mensaje elegido al abrir (o su turno). Se guarda en ``listas.ambito``.
AMBITOS = ("sesion", "turno")
OPCION_AMBITO = "listas.ambito"

#: Páginas que, con el ámbito del turno, se vuelven a calcular con sus mensajes
#: (sus elementos se agrupan o se acumulan); las demás se filtran por su mensaje.
_RECALCULAR_EN_TURNO = ("enlaces", "archivos", "hallazgos", "commits", "incidencias", "leidos")

#: Tamaño máximo de un archivo que se lee entero en el visor.
MAX_LECTURA = 2 * 1024 * 1024

#: Milisegundos sin escribir en el filtro antes de decir cuántos quedan.
ESPERA_ANUNCIO_FILTRO = 600

#: Tipos de archivo que nunca se abren con su programa: se ejecutarían.
EXTENSIONES_PELIGROSAS = frozenset({
	".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".ps1xml", ".vbs", ".vbe", ".js", ".jse", ".mjs",
	".wsf", ".wsh", ".ws", ".msi", ".msp", ".mst", ".lnk", ".url", ".scr", ".pif", ".cpl", ".hta", ".reg",
	".inf", ".scf", ".msc", ".jar", ".py", ".pyw", ".pyz", ".pyc", ".appref-ms", ".application", ".gadget",
	".vb", ".sct", ".chm", ".sys", ".dll", ".ocx", ".drv", ".appx", ".msix", ".appinstaller", ".settingcontent-ms",
	".library-ms", ".search-ms", ".diagcab", ".xll", ".sh",
})

#: Extensión sugerida al guardar un bloque según su lenguaje.
EXTENSIONES_LENGUAJE = {
	"python": ".py", "py": ".py", "python3": ".py", "pycon": ".py",
	"javascript": ".js", "js": ".js", "node": ".js", "jsx": ".jsx", "mjs": ".mjs",
	"typescript": ".ts", "ts": ".ts", "tsx": ".tsx",
	"json": ".json", "jsonc": ".json", "html": ".html", "htm": ".html", "xml": ".xml", "svg": ".svg",
	"css": ".css", "scss": ".scss", "less": ".less",
	"bash": ".sh", "sh": ".sh", "shell": ".sh", "zsh": ".sh", "console": ".sh",
	"powershell": ".ps1", "ps1": ".ps1", "pwsh": ".ps1", "ps": ".ps1",
	"bat": ".bat", "batch": ".bat", "cmd": ".bat", "dos": ".bat",
	"c": ".c", "h": ".h", "cpp": ".cpp", "c++": ".cpp", "cc": ".cpp", "hpp": ".hpp",
	"csharp": ".cs", "cs": ".cs", "c#": ".cs", "java": ".java", "kotlin": ".kt", "kt": ".kt",
	"go": ".go", "golang": ".go", "rust": ".rs", "rs": ".rs", "swift": ".swift",
	"ruby": ".rb", "rb": ".rb", "php": ".php", "lua": ".lua", "perl": ".pl", "r": ".r",
	"sql": ".sql", "yaml": ".yaml", "yml": ".yaml", "toml": ".toml", "ini": ".ini", "cfg": ".ini",
	"markdown": ".md", "md": ".md", "diff": ".diff", "patch": ".diff",
	"dockerfile": ".dockerfile", "makefile": ".mk", "csv": ".csv",
	"text": ".txt", "txt": ".txt", "plaintext": ".txt", "": ".txt",
}

#: Atajo de cada acción tal como se ve en el menú contextual (uno por acción:
#: la etiqueta cambia según la página, la tecla no).
TECLAS_ACCION = {
	"activar": "\tIntro",
	"leer": "\tCtrl+L",
	"abrir": "\tCtrl+O",
	"editor": "\tCtrl+E",
	"explorador": "\tCtrl+Shift+E",
	"corregir": "\tCtrl+R",
	"citar": "\tCtrl+R",
	"copiar": "\tCtrl+C",
	"contenido": "\tCtrl+Shift+C",
	"guardar": "\tCtrl+S",
	"ir": "\tAlt+Enter",
}

Elemento = Any


def extension_para(lenguaje: str) -> str:
	"""Extensión de archivo sugerida para un lenguaje (``.txt`` si no se conoce)."""
	return EXTENSIONES_LENGUAJE.get((lenguaje or "").strip().lower(), ".txt")


def copiar_al_portapapeles(texto: str) -> bool:
	"""Copia ``texto`` al portapapeles de Windows. Devuelve si lo consiguió."""
	from app.ui.accesibilidad.teclado import copiar_texto

	return copiar_texto(texto)


def es_peligroso(ruta: Path) -> bool:
	"""Si abrir ``ruta`` con su programa la ejecutaría (programas, scripts, accesos directos)."""
	return ruta.suffix.lower() in EXTENSIONES_PELIGROSAS


def es_ruta_de_red(ruta: str) -> bool:
	return ruta.startswith(("\\\\", "//"))


def leer_texto_archivo(ruta: Path) -> tuple[str | None, str]:
	"""Lee un archivo de texto para mostrarlo. Devuelve ``(texto, motivo)``.

	Si no se puede mostrar, ``texto`` es ``None`` y ``motivo`` lo explica.
	"""
	try:
		if not ruta.exists():
			return None, _("Este archivo ya no está en {ruta}.").format(ruta=ruta)
		if ruta.is_dir():
			return None, _("Es una carpeta, no un archivo. Control+Mayúsculas+E la abre en el Explorador.")
		tamano = ruta.stat().st_size
		if tamano > MAX_LECTURA:
			return None, _("El archivo ocupa {mb} MB, demasiado para leerlo aquí. Ábrelo con Control+E o Control+O.").format(
				mb=round(tamano / (1024 * 1024), 1))
		datos = ruta.read_bytes()
	except OSError as error:
		log.warning("No se pudo leer %s", ruta, exc_info=True)
		return None, _("No se pudo leer el archivo: {error}").format(error=error)
	if b"\0" in datos[:8192]:
		return None, _("No es un archivo de texto. Ábrelo con Control+O o muéstralo en el Explorador con Control+Mayúsculas+E.")
	for codificacion in ("utf-8-sig", "cp1252"):
		try:
			texto = datos.decode(codificacion)
		except UnicodeDecodeError:
			log.debug("%s no está en %s", ruta.name, codificacion)
			continue
		break
	else:
		texto = datos.decode("utf-8", errors="replace")
	return texto.replace("\r\n", "\n").replace("\r", "\n"), ""


def orden_editor(editor: str, ruta: str, linea: int = 0) -> list[str]:
	"""Orden para abrir ``ruta`` en ``editor``, en su línea si el editor lo admite."""
	nombre = Path(editor).stem.lower()
	if linea:
		if nombre in ("code", "code-insiders", "codium", "vscodium", "cursor", "windsurf", "antigravity"):
			return [editor, "-g", "{r}:{l}".format(r=ruta, l=linea)]
		if nombre == "notepad++":
			return [editor, "-n{l}".format(l=linea), ruta]
		if nombre in ("subl", "sublime_text"):
			return [editor, "{r}:{l}".format(r=ruta, l=linea)]
	return [editor, ruta]


def _buscar_editor(configurado: str) -> str:
	"""El editor de Preferencias; si no hay, Visual Studio Code; si no, el Bloc de notas."""
	if configurado and Path(configurado).is_file():
		return configurado
	from app.services.agentes import procesos

	codigo = procesos.buscar_ejecutable(["code"], [
		Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",  # auditoria: ignorar ruta-no-portable - localiza Visual Studio Code instalado; no guarda datos
	])
	return str(codigo) if codigo is not None else "notepad.exe"


def _titulo_pagina(clave: str) -> str:
	return {
		"codigo": _("Bloques de código"),
		"enlaces": _("Enlaces"),
		"archivos": _("Archivos cambiados"),
		"ordenes": _("Órdenes ejecutadas"),
		"tareas": _("Tareas del agente"),
		"subagentes": _("Subagentes"),
		"hallazgos": _("Hallazgos de la revisión"),
		"decisiones": _("Decisiones"),
		"errores": _("Errores"),
		"commits": _("Commits"),
		"incidencias": _("Incidencias y PR"),
		"leidos": _("Archivos leídos"),
		"mios": _("Tus mensajes"),
	}[clave]


def _etiqueta_lista(clave: str, total: int | str, resumen: str = "") -> str:
	"""Etiqueta de la lista: «&Enlaces (12):», «&Enlaces (3 de 12):»… (``total`` puede ser ya un texto)."""
	if clave == "tareas" and resumen:
		return _("&Tareas del agente ({resumen}):").format(resumen=resumen)
	plantilla = {
		"codigo": _("&Bloques de código ({n}):"),
		"enlaces": _("&Enlaces ({n}):"),
		"archivos": _("&Archivos cambiados ({n}):"),
		"ordenes": _("Ó&rdenes ejecutadas ({n}):"),
		"tareas": _("&Tareas del agente ({n}):"),
		"subagentes": _("&Subagentes ({n}):"),
		"hallazgos": _("&Hallazgos de la revisión ({n}):"),
		"decisiones": _("&Decisiones ({n}):"),
		"errores": _("Err&ores ({n}):"),
		"commits": _("Comm&its ({n}):"),
		"incidencias": _("Incidencias y &PR ({n}):"),
		"leidos": _("Archivos &leídos ({n}):"),
		"mios": _("T&us mensajes ({n}):"),
	}[clave]
	return plantilla.format(n=total)


def _donde(donde: str) -> str:
	return {"mensaje": _("en este mensaje"), "turno": _("en este turno")}.get(donde, _("en esta sesión"))


def _mensaje_vacio(clave: str, donde: str = "sesion") -> str:
	"""Lo que se dice si la página no tiene nada (``donde``: ``sesion``, ``mensaje`` o ``turno``)."""
	plantilla = {
		"codigo": _("No hay bloques de código {donde}"),
		"enlaces": _("No hay enlaces {donde}"),
		"archivos": _("No hay archivos cambiados {donde}"),
		"ordenes": _("No hay órdenes ejecutadas {donde}"),
		"tareas": _("El agente no ha hecho una lista de tareas {donde}"),
		"subagentes": _("El agente no ha usado subagentes {donde}"),
		"hallazgos": _("No hay hallazgos de una revisión de código {donde}"),
		"decisiones": _("No hay decisiones guardadas {donde}. Se guardan las preguntas y los planes del agente "
			"que respondes en las sesiones de Ctrl+IA"),
		"errores": _("No ha fallado nada {donde}"),
		"commits": _("El agente no ha hecho commits {donde}"),
		"incidencias": _("No se citan incidencias ni PR de GitHub {donde}"),
		"leidos": _("El agente no ha leído archivos {donde}"),
		"mios": _("No hay mensajes tuyos {donde}"),
	}[clave]
	return plantilla.format(donde=_donde(donde))


def _columnas(clave: str) -> list[tuple[str, int]]:
	return {
		"codigo": [(_("Lenguaje"), 120), (_("Primera línea"), 420), (_("Líneas"), 70)],
		"enlaces": [(_("Texto"), 220), (_("Dirección"), 340), (_("Tipo"), 140)],
		"archivos": [(_("Archivo"), 460), (_("Cambios"), 160)],
		"ordenes": [(_("Orden"), 520), (_("Resultado"), 120)],
		"tareas": [(_("Tarea"), 520), (_("Estado"), 120)],
		"subagentes": [(_("Subagente"), 300), (_("Tipo"), 140), (_("Estado"), 120), (_("Pasos"), 70), (_("Ediciones"), 80)],
		"hallazgos": [(_("Hallazgo"), 330), (_("Archivo"), 180), (_("Línea"), 60), (_("Categoría"), 110),
			(_("Veredicto"), 100), (_("Resultado"), 110)],
		"decisiones": [(_("Decisión"), 420), (_("Tipo"), 90), (_("Tu respuesta"), 260)],
		"errores": [(_("Qué falló"), 300), (_("Error"), 340), (_("Tipo"), 130)],
		"commits": [(_("Mensaje"), 360), (_("Hash"), 90), (_("Rama"), 120), (_("Resultado"), 110)],
		"incidencias": [(_("Referencia"), 220), (_("Tipo"), 100), (_("Dónde se cita"), 380)],
		"leidos": [(_("Archivo"), 440), (_("Veces"), 60), (_("Con"), 160)],
		"mios": [(_("Mensaje"), 520), (_("Fecha"), 150)],
	}[clave]


def _ayuda_lista(clave: str) -> str:
	return {
		"codigo": _(
			"Bloques de código de la conversación. Intro muestra el bloque completo; Control+C lo copia; "
			"Control+S lo guarda como archivo; Alt+Intro cierra y va al mensaje. Tecla Aplicaciones: más acciones."
		),
		"enlaces": _(
			"Enlaces y rutas de archivos citados en la conversación. En un enlace web, Intro lo abre en el navegador. "
			"En un archivo, Intro lo abre con su programa de Windows, Control+L lo lee aquí, Control+E lo abre en el "
			"editor y Control+Mayúsculas+E lo muestra en el Explorador. Control+C copia la dirección o la ruta; "
			"Alt+Intro cierra y va al mensaje. Tecla Aplicaciones: más acciones."
		),
		"archivos": _(
			"Archivos creados o modificados por el agente y sus subagentes. Intro muestra los cambios; Control+L lee "
			"el archivo entero; Control+O lo abre con su programa; Control+E en el editor; Control+Mayúsculas+E lo "
			"muestra en el Explorador; Control+C copia la ruta y Control+Mayúsculas+C el contenido; Control+S guarda "
			"los cambios; Alt+Intro cierra y va al mensaje."
		),
		"ordenes": _(
			"Órdenes de consola que ha ejecutado el agente y si terminaron bien. Intro muestra la orden y su salida; "
			"Control+C copia la orden y Control+Mayúsculas+C la salida; Alt+Intro cierra y va al mensaje."
		),
		"tareas": _(
			"Lista de tareas del agente en su último estado: pendiente, en curso o hecha, en el orden del agente. "
			"Intro muestra la tarea; Control+C la copia; Alt+Intro cierra y va al mensaje donde cambió por última vez."
		),
		"subagentes": _(
			"Trabajos que el agente ha encargado a subagentes, con cuántos pasos y ediciones hicieron. Intro muestra "
			"lo que hizo cada uno; Control+C copia su respuesta; Alt+Intro cierra y va al mensaje que lo lanzó."
		),
		"hallazgos": _(
			"Hallazgos de las revisiones de código: archivo, línea, categoría y veredicto. Intro muestra el hallazgo "
			"completo; Control+R prepara un mensaje para que el agente lo corrija; Control+E abre el archivo en esa "
			"línea; Control+L lo lee; Control+C copia el hallazgo; Alt+Intro cierra y va al mensaje."
		),
		"decisiones": _(
			"Preguntas que te hizo el agente y planes que te propuso, con lo que respondiste. Intro muestra la "
			"pregunta o el plan completo con tu respuesta; Control+C copia la pregunta y la respuesta; Alt+Intro "
			"cierra y va al mensaje. Solo en las sesiones de Ctrl+IA, desde que se guardan las respuestas."
		),
		"errores": _(
			"Lo que ha fallado: herramientas y órdenes que terminaron con error (también las que rechazaste) y avisos "
			"de error. Intro muestra el error completo con lo que se pidió; Control+C copia el error y "
			"Control+Mayúsculas+C solo su detalle; Alt+Intro cierra y va al mensaje."
		),
		"commits": _(
			"Commits que hizo el agente con git commit, sus subidas con git push y los commits que salen en otras "
			"órdenes de git, con su hash, su rama y si salieron bien. Intro muestra el mensaje completo, la orden y "
			"su salida; Control+C copia el hash y Control+Mayúsculas+C el mensaje; Alt+Intro cierra y va al mensaje."
		),
		"incidencias": _(
			"Incidencias y PR de GitHub citadas en la conversación: enlaces, referencias como dueño/repositorio#12 "
			"o #12 y órdenes gh issue y gh pr. Un #12 suelto es del repositorio de la sesión si se sabe. Intro lo "
			"abre en el navegador; Control+C copia la dirección; Alt+Intro cierra y va al mensaje donde se citó."
		),
		"leidos": _(
			"Archivos que leyó el agente, con sus herramientas de lectura o con órdenes como Get-Content o cat, y "
			"cuántas veces. Intro lee el archivo entero; Control+O lo abre con su programa; Control+E en el editor; "
			"Control+Mayúsculas+E lo muestra en el Explorador; Control+C copia la ruta y Control+Mayúsculas+C el "
			"contenido; Alt+Intro cierra y va al mensaje de la última lectura."
		),
		"mios": _(
			"Tus mensajes de la conversación, para volver a uno rápido. Intro cierra y va al mensaje; Control+R lo "
			"cita en el cuadro de respuesta; Control+C lo copia."
		),
	}[clave]


def _primera_no_vacia(texto: str) -> str:
	for linea in (texto or "").splitlines():
		if linea.strip():
			return linea
	return ""


def _valla(texto: str) -> str:
	"""Valla para un bloque Markdown que no choque con el contenido."""
	return "~~~~" if "```" in (texto or "") else "```"


def _en_bloque(texto: str, lenguaje: str = "") -> str:
	valla = _valla(texto)
	return "{v}{l}\n{t}\n{v}\n".format(v=valla, l=lenguaje, t=(texto or "").rstrip("\n"))


def _texto_cambios(cambio: extraccion.Cambio) -> str:
	if not cambio.diff.strip():
		texto = _("sin detalle")
	else:
		anadidas, quitadas = extraccion.contar_lineas_diff(cambio.diff)
		texto = "+{a}/−{q}".format(a=anadidas, q=quitadas) if anadidas or quitadas else _("sin líneas cambiadas")
	if cambio.subagente:
		texto = _("{cambios}, de un subagente").format(cambios=texto)
	return texto


def _texto_tipo_decision(decision: extraccion.Decision) -> str:
	return _("Plan") if decision.tipo == "plan" else _("Pregunta")


def _estilo_cambios(padre: wx.Window | None) -> str:
	"""Cómo se leen las líneas cambiadas, según Preferencias de la ventana principal («palabras» si no se sabe)."""
	try:
		if padre is not None and hasattr(padre, "opcion"):
			estilo = str(padre.opcion("mensajes.estilo_cambios", "palabras") or "palabras")
			if estilo in extraccion.ESTILOS_CAMBIOS:
				return estilo
	except Exception:  # noqa: BLE001 - sin la preferencia se usa la de fábrica
		log.debug("No se pudo leer el estilo de los cambios", exc_info=True)
	return "palabras"


def _ambito_guardado(padre: wx.Window | None) -> str:
	"""El ámbito que se usó la última vez (``sesion`` si no se sabe)."""
	try:
		if padre is not None and hasattr(padre, "opcion"):
			ambito = str(padre.opcion(OPCION_AMBITO, "sesion") or "sesion")
			if ambito in AMBITOS:
				return ambito
	except Exception:  # noqa: BLE001 - sin la preferencia, toda la sesión
		log.debug("No se pudo leer el ámbito de las listas", exc_info=True)
	return "sesion"


def _indice_elegido_de(padre: wx.Window | None) -> int:
	"""Posición en la conversación entera del mensaje elegido en la ventana principal (-1 si no hay)."""
	funcion = getattr(padre, "_indice_completo_actual", None) if padre is not None else None
	if not callable(funcion):
		return -1
	try:
		return int(funcion())
	except Exception:  # noqa: BLE001 - sin mensaje elegido solo se ofrece toda la sesión
		log.debug("No se pudo saber el mensaje elegido", exc_info=True)
		return -1


def _texto_resultado_orden(orden: extraccion.Orden) -> str:
	if orden.error is None:
		return _("Sin resultado")
	return _("Con error") if orden.error else _("Correcta")


def _texto_resultado_commit(commit: extraccion.Commit) -> str:
	if commit.error is None:
		return _("Sin resultado")
	if commit.error:
		return _("Con error")
	return _("Subido") if commit.tipo == "subida" else _("Hecho")


def _titulo_commit(commit: extraccion.Commit) -> str:
	if commit.tipo == "subida":
		return _("Subida a {rama}").format(rama=commit.rama) if commit.rama else _("Subida con git push")
	return primera_linea(_primera_no_vacia(commit.mensaje), 250) or _("(sin mensaje)")


def _texto_tipo_referencia(referencia: extraccion.Referencia) -> str:
	return {"incidencia": _("Incidencia"), "pr": _("PR")}.get(referencia.tipo, _("Sin saber"))


def _texto_estado_subagente(estado: str) -> str:
	return {
		"terminado": _("Terminado"),
		"error": _("Con error"),
		"en_segundo_plano": _("En segundo plano"),
	}.get(estado, _("Sin resultado"))


def _texto_estado_tarea(estado: str) -> str:
	return {"hecha": _("Hecha"), "en_curso": _("En curso")}.get(estado, _("Pendiente"))


def _resumen_tareas(tareas: Sequence[extraccion.Tarea]) -> str:
	if not tareas:
		return ""
	hechas = sum(1 for tarea in tareas if tarea.estado == "hecha")
	return _("{h} de {t} hechas").format(h=hechas, t=len(tareas))


def _citado(texto: str) -> str:
	"""El texto como cita de Markdown («> » en cada línea)."""
	return "\n".join("> " + linea if linea.strip() else ">" for linea in (texto or "").strip().splitlines())


def _contexto_de_padre(padre: wx.Window | None) -> dict[str, Any]:
	"""Carpeta, transcript de Claude, editor y si la sesión es propia, según la ventana principal."""
	contexto: dict[str, Any] = {"carpeta": "", "ruta_transcript": None, "editor": "", "propia": False}
	if padre is None:
		return contexto
	try:
		clave = str(getattr(padre, "sesion_actual", "") or "")
		fila = padre.fila_de(clave) if clave and hasattr(padre, "fila_de") else None
		if hasattr(padre, "opcion"):
			contexto["editor"] = str(padre.opcion("agentes.editor_externo", "") or "").strip()
		if fila is None:
			return contexto
		contexto["carpeta"] = str(fila.carpeta or "")
		contexto["propia"] = bool(fila.propia)
		if fila.proveedor == "claude" and not getattr(fila, "remota", False):  # el transcript de otro equipo no está en Windows
			id_sesion = clave.split(":", 1)[1] if clave.startswith("claude:") else str(fila.id_externo or "")
			if id_sesion:
				from app.services.historial import claude as historial_claude

				contexto["ruta_transcript"] = historial_claude.ruta_transcript(id_sesion, contexto["carpeta"])
	except Exception:  # noqa: BLE001 - sin contexto el diálogo sigue funcionando con menos datos
		log.debug("No se pudo deducir el contexto de la sesión", exc_info=True)
	return contexto


class DialogoListas(wx.Dialog):
	"""Listas de una sesión: código, enlaces, archivos, órdenes, tareas, subagentes, hallazgos,
	decisiones, errores, commits, incidencias y PR, archivos leídos y tus mensajes.

	Tras ``ShowModal``, si el usuario eligió «Ir al mensaje», devuelve
	``wx.ID_OK`` y ``indice_mensaje`` tiene la posición del mensaje en la
	lista recibida; si no, ``indice_mensaje`` es ``None``. Si preparó un
	mensaje para corregir un hallazgo, ``mensaje_preparado`` lo contiene; si
	citó uno de sus mensajes, ``mensaje_citado``.

	:param transcritos: transcripts de los subagentes (como los de
		``historial.claude.leer_subagentes``). Si es ``None`` y la sesión es
		de Claude, se leen en segundo plano.
	:param estilo_cambios: cómo se leen las líneas cambiadas (``extraccion.ESTILOS_CAMBIOS``);
		si es ``None``, el de Preferencias de la ventana principal.
	:param indice_elegido: posición del mensaje elegido al abrir (para el ámbito
		«este mensaje o turno»); si es ``None``, el de la ventana principal.
	:param ambito: ``sesion`` o ``turno``; si es ``None``, el que se usó la última vez.
	"""

	def __init__(
		self, padre: wx.Window | None, mensajes: list[MensajeVista], titulo_sesion: str, pestana: str = "codigo", *,
		carpeta: str | None = None, ruta_transcript: Path | None = None, editor: str | None = None,
		propia: bool | None = None, transcritos: Sequence[Any] | None = None, estilo_cambios: str | None = None,
		indice_elegido: int | None = None, ambito: str | None = None,
	) -> None:
		titulo = _("Listas de la sesión «{titulo}»").format(titulo=titulo_sesion or _("Sesión sin título"))
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		contexto = _contexto_de_padre(padre) if None in (carpeta, editor, propia) or (ruta_transcript is None and transcritos is None) else {}
		self.carpeta = carpeta if carpeta is not None else contexto.get("carpeta", "")
		self.editor = editor if editor is not None else contexto.get("editor", "")
		self.propia = propia if propia is not None else bool(contexto.get("propia", False))
		self.ruta_transcript = ruta_transcript if ruta_transcript is not None else contexto.get("ruta_transcript")
		self.estilo_cambios = estilo_cambios if estilo_cambios in extraccion.ESTILOS_CAMBIOS else _estilo_cambios(padre)
		self.indice_mensaje: int | None = None
		self.mensaje_preparado = ""
		self.mensaje_citado = ""
		self._padre = padre
		self._mensajes = list(mensajes)
		self._existencia: dict[str, bool | None] = {}
		elegido = _indice_elegido_de(padre) if indice_elegido is None else indice_elegido
		self.indice_elegido = elegido if 0 <= elegido < len(self._mensajes) else -1
		self.ambito = ambito if ambito in AMBITOS else _ambito_guardado(padre)
		self._filtros: dict[str, str] = {}
		self._temporizador_filtro: wx.CallLater | None = None
		self._repositorio = self._buscar_repositorio()
		ordenes = extraccion.ordenes_detalladas(mensajes)
		subagentes = extraccion.subagentes(mensajes)
		self._archivos_principales = extraccion.archivos_cambiados(mensajes)
		#: Lo de toda la sesión; ``_base`` es lo del ámbito y ``datos``, lo que se ve tras el filtro.
		self._sesion: dict[str, list[Elemento]] = {
			"codigo": list(extraccion.bloques_codigo(mensajes)),
			"enlaces": list(extraccion.enlaces(mensajes)),
			"archivos": list(self._archivos_principales),
			"ordenes": list(ordenes),
			"tareas": list(extraccion.tareas(mensajes)),
			"subagentes": list(subagentes),
			"hallazgos": list(extraccion.hallazgos(mensajes)),
			"decisiones": list(extraccion.decisiones(mensajes)),
			"errores": list(extraccion.errores(mensajes)),
			"commits": list(extraccion.commits(mensajes)),
			"incidencias": list(extraccion.incidencias(mensajes, self._repositorio)),
			"leidos": list(extraccion.archivos_leidos(mensajes)),
			"mios": list(extraccion.mensajes_usuario(mensajes)),
		}
		if transcritos is not None:
			self._completar_subagentes(list(transcritos))
		self._base: dict[str, list[Elemento]] = {}
		self._filas_base: dict[str, list[list[str]]] = {}
		self.datos: dict[str, list[Elemento]] = {}
		self.listas: dict[str, ListaAccesible] = {}
		self.etiquetas: dict[str, wx.StaticText] = {}

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		arriba = wx.BoxSizer(wx.HORIZONTAL)
		self.etiqueta_ambito = wx.StaticText(panel, label=_("&Mostrar:"))
		self.selector_ambito = wx.Choice(panel, choices=self._opciones_ambito(), name=_("Mostrar"))
		self.selector_ambito.SetHelpText(_(
			"Qué se ve en las listas: todo lo de la sesión, o solo lo del mensaje que estaba elegido al abrir este "
			"diálogo. En los bloques de código y los enlaces es ese mensaje; en las demás páginas, su turno: desde tu "
			"mensaje hasta el siguiente tuyo. Control+T cambia de uno a otro desde cualquier sitio. Se recuerda."
		))
		self.etiqueta_filtro = wx.StaticText(panel, label=_("&Filtrar:"))
		self.filtro = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER, name=_("Filtrar"))
		self.filtro.SetHelpText(_(
			"Escribe unas letras para dejar en la lista de la página solo lo que las contiene, sin distinguir "
			"mayúsculas ni tildes; con varias palabras, lo que tiene todas. Cada página recuerda su filtro. "
			"Control+F trae aquí desde cualquier sitio; Escape vacía el filtro; Flecha abajo o Intro llevan a la lista."
		))
		arriba.Add(self.etiqueta_ambito, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
		arriba.Add(self.selector_ambito, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
		arriba.Add(self.etiqueta_filtro, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
		arriba.Add(self.filtro, 1, wx.ALIGN_CENTER_VERTICAL)
		sizer.Add(arriba, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

		self.libro = wx.Notebook(panel, name=_("Páginas"))
		self.libro.SetHelpText(_(
			"Páginas del diálogo. Control+Tabulador pasa a la siguiente y Control+Mayúsculas+Tabulador a la "
			"anterior; Control+1 a Control+9 y Control+0 van directamente a las diez primeras. Incidencias y PR, "
			"Archivos leídos y Tus mensajes se alcanzan con Control+Tabulador; Tus mensajes es la última: "
			"Control+1 y luego Control+Mayúsculas+Tabulador."
		))
		for clave in PAGINAS:
			self._crear_pagina(clave)
		sizer.Add(self.libro, 1, wx.EXPAND | wx.ALL, 8)

		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra este diálogo y vuelve a la conversación."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
		panel.SetSizer(sizer)

		if self.ambito == "turno" and self.indice_elegido < 0:
			self.ambito = "sesion"  # sin mensaje elegido no hay turno; la preferencia guardada no cambia
		self.selector_ambito.SetSelection(AMBITOS.index(self.ambito))
		self._recalcular(PAGINAS)

		pestana = pestana if pestana in PAGINAS else "codigo"
		self.libro.ChangeSelection(PAGINAS.index(pestana))  # sin evento: no se anuncia nada aún
		preparar_dialogo(self, foco_inicial=self.listas[pestana])
		# Después de preparar_dialogo: este manejador se atiende antes que el suyo.
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		self.libro.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._al_cambiar_pagina)
		self.selector_ambito.Bind(wx.EVT_CHOICE, self._al_elegir_ambito)
		self.filtro.Bind(wx.EVT_TEXT, self._al_escribir_filtro)
		self.filtro.Bind(wx.EVT_TEXT_ENTER, lambda _evento: self.ir_a_lista())
		self.Bind(wx.EVT_WINDOW_DESTROY, self._al_destruir)

		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		wx.CallAfter(self._anunciar_si_vacia)
		if transcritos is None and self.ruta_transcript is not None and any(s.id or s.tipo for s in subagentes):
			self._leer_subagentes_en_segundo_plano(Path(self.ruta_transcript))

	# ── Construcción ──────────────────────────────────────────────────────────

	def _buscar_repositorio(self) -> str:
		"""«dueño/repo» de GitHub para los «#12» sueltos: el de las órdenes de git, el de la carpeta o el más citado."""
		try:
			return extraccion.repositorio_de_sesion(self._mensajes, self.carpeta)
		except Exception:  # noqa: BLE001 - sin repositorio, los #12 sueltos quedan sin enlace
			log.debug("No se pudo saber el repositorio de la sesión", exc_info=True)
			return ""

	def _opciones_ambito(self) -> list[str]:
		if self.indice_elegido < 0:
			turno = _("Este mensaje o turno (no hay ningún mensaje elegido)")
		else:
			turno = _("Este mensaje o turno (mensaje {n})").format(n=self.indice_elegido + 1)
		return [_("Toda la sesión"), turno]

	def _crear_pagina(self, clave: str) -> None:
		pagina = wx.Panel(self.libro, name=_titulo_pagina(clave))
		sizer = wx.BoxSizer(wx.VERTICAL)
		etiqueta = wx.StaticText(pagina, label=_etiqueta_lista(clave, 0))
		lista = ListaAccesible(pagina, _titulo_pagina(clave), _columnas(clave))
		lista.SetHelpText(self._ayuda(clave))
		lista.SetMinSize(self.FromDIP(wx.Size(640, 300)))
		lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.activar())
		vincular_menu_contextual(lista, lambda c=clave: self._crear_menu(c))
		sizer.Add(etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		sizer.Add(lista, 1, wx.EXPAND | wx.ALL, 6)
		pagina.SetSizer(sizer)
		self.libro.AddPage(pagina, _titulo_pagina(clave))
		self.listas[clave] = lista
		self.etiquetas[clave] = etiqueta

	def _ambito_activo(self) -> bool:
		"""Si se ve solo lo del mensaje elegido (o su turno)."""
		return self.ambito == "turno" and self.indice_elegido >= 0

	def _donde(self, clave: str) -> str:
		if not self._ambito_activo():
			return "sesion"
		return "mensaje" if clave in PAGINAS_DE_MENSAJE else "turno"

	def _etiqueta(self, clave: str) -> str:
		total = len(self._base.get(clave, []))
		filtro = self._filtros.get(clave, "").strip()
		cuenta = _("{n} de {m}").format(n=len(self.datos.get(clave, [])), m=total) if filtro else str(total)
		resumen = _resumen_tareas(self._base.get("tareas", [])) if clave == "tareas" and not filtro else ""
		donde = self._donde(clave)
		if donde != "sesion":
			sufijo = _("solo este mensaje") if donde == "mensaje" else _("solo este turno")
			if resumen:
				resumen = "{r}, {s}".format(r=resumen, s=sufijo)
			else:
				cuenta = "{c}, {s}".format(c=cuenta, s=sufijo)
		return _etiqueta_lista(clave, cuenta, resumen)

	def _ayuda(self, clave: str) -> str:
		ayuda = _ayuda_lista(clave)
		if clave == "archivos" and self._sesion["ordenes"]:
			ayuda += " " + _(
				"El agente también ejecutó órdenes de consola, que pueden haber cambiado archivos que no aparecen aquí."
			)
		return ayuda

	def _opcion(self, clave: str, defecto: Any = None) -> Any:
		if self._padre is not None and hasattr(self._padre, "opcion"):
			try:
				return self._padre.opcion(clave, defecto)
			except Exception:  # noqa: BLE001 - sin la preferencia, la de fábrica
				log.debug("No se pudo leer la opción %s", clave, exc_info=True)
		return defecto

	def _fila(self, clave: str, elemento: Elemento) -> list[str]:
		if clave == "codigo":
			bloque: extraccion.BloqueCodigo = elemento
			return [
				bloque.lenguaje or _("sin indicar"),
				primera_linea(_primera_no_vacia(bloque.codigo), 200),
				str(bloque.num_lineas),
			]
		if clave == "enlaces":
			enlace: extraccion.Enlace = elemento
			if not enlace.es_archivo:
				return [enlace.texto or urlparse(enlace.url).netloc or enlace.url, enlace.url, _("Web")]
			direccion = enlace.url + (":{l}".format(l=enlace.linea) if enlace.linea else "")
			return [enlace.texto or Path(enlace.url.replace("\\", "/")).name or enlace.url, direccion, self._tipo_archivo(enlace.url)]
		if clave == "archivos":
			cambio: extraccion.Cambio = elemento
			return [cambio.ruta, _texto_cambios(cambio)]
		if clave == "ordenes":
			orden: extraccion.Orden = elemento
			return [primera_linea(orden.orden, 250), _texto_resultado_orden(orden)]
		if clave == "tareas":
			tarea: extraccion.Tarea = elemento
			return [primera_linea(tarea.texto, 300), _texto_estado_tarea(tarea.estado)]
		if clave == "subagentes":
			subagente: extraccion.Subagente = elemento
			return [
				primera_linea(subagente.descripcion, 200), subagente.tipo or _("sin indicar"),
				_texto_estado_subagente(subagente.estado),
				_("sin datos") if subagente.pasos is None else str(subagente.pasos),
				_("sin datos") if subagente.ediciones is None else str(subagente.ediciones),
			]
		if clave == "decisiones":
			decision: extraccion.Decision = elemento
			return [primera_linea(decision.pregunta, 250), _texto_tipo_decision(decision), primera_linea(decision.respuesta, 200)]
		if clave == "errores":
			fallo: extraccion.Fallo = elemento
			tipo = (fallo.herramienta or _("Herramienta")) if fallo.tipo == "herramienta" else _("Aviso de error")
			return [primera_linea(fallo.titulo, 250), primera_linea(fallo.resumen, 250), tipo]
		if clave == "commits":
			commit: extraccion.Commit = elemento
			return [_titulo_commit(commit), commit.hash[:10], commit.rama, _texto_resultado_commit(commit)]
		if clave == "incidencias":
			referencia: extraccion.Referencia = elemento
			return [referencia.referencia, _texto_tipo_referencia(referencia), primera_linea(referencia.contexto, 200)]
		if clave == "leidos":
			lectura: extraccion.Lectura = elemento
			return [lectura.ruta, str(lectura.veces), lectura.como]
		if clave == "mios":
			from app.ui.lectura_listas import escribir_fecha

			mio: extraccion.MensajeUsuario = elemento
			return [primera_linea(_primera_no_vacia(mio.texto), 300), escribir_fecha(mio.fecha, self._opcion) if mio.fecha else ""]
		hallazgo: extraccion.Hallazgo = elemento
		return [
			primera_linea(hallazgo.corto or hallazgo.resumen, 250),
			Path(hallazgo.archivo.replace("\\", "/")).name if hallazgo.archivo else _("sin archivo"),
			str(hallazgo.linea) if hallazgo.linea else "",
			hallazgo.categoria,
			extraccion.nombre_veredicto(hallazgo.veredicto),
			extraccion.nombre_resultado(hallazgo.resultado) or _("Pendiente"),
		]

	@staticmethod
	def _texto_para_filtrar(clave: str, elemento: Elemento) -> str:
		"""Lo que, además de las columnas, se mira al filtrar (el texto completo del elemento)."""
		if clave == "codigo":
			return elemento.codigo
		if clave == "ordenes":
			return elemento.orden
		if clave == "errores":
			return elemento.detalle
		if clave == "commits":
			return elemento.mensaje + "\n" + elemento.hash
		if clave == "incidencias":
			return elemento.url
		if clave == "decisiones":
			return elemento.pregunta + "\n" + elemento.respuesta
		if clave == "mios":
			return elemento.texto
		if clave == "hallazgos":
			return elemento.resumen + "\n" + elemento.archivo
		return ""

	def _tipo_archivo(self, ruta: str) -> str:
		if es_ruta_de_red(ruta):
			return _("Archivo de red")
		existe = self._existe(ruta)
		if existe is None:
			return _("Archivo")
		return _("Archivo") if existe else _("Archivo que no existe")

	def _crear_menu(self, clave: str) -> wx.Menu | None:
		elemento = self.elemento_actual(clave)
		if elemento is None:
			voz.hablar(self._aviso_vacio(clave))
			return None
		menu = wx.Menu()
		for accion, etiqueta in self._acciones_menu(clave, elemento):
			if accion == "-":
				menu.AppendSeparator()
				continue
			item = menu.Append(wx.ID_ANY, etiqueta + TECLAS_ACCION[accion])
			menu.Bind(wx.EVT_MENU, lambda _e, a=accion: self.ejecutar(a), item)
		return menu

	def _acciones_menu(self, clave: str, elemento: Elemento) -> list[tuple[str, str]]:
		"""``[(acción, etiqueta)]`` del menú contextual de una página (``("-", "")`` es un separador)."""
		ir = [("-", ""), ("ir", _("&Ir al mensaje"))]
		archivo = [
			("leer", _("&Leer el archivo entero")),
			("editor", _("Abrir en el &editor de código")),
			("explorador", _("&Mostrar en el Explorador")),
		]
		if clave == "codigo":
			return [("activar", _("&Ver el bloque completo")), ("copiar", _("&Copiar el código")),
				("guardar", _("&Guardar como archivo...")), *ir]
		if clave == "enlaces":
			if not elemento.es_archivo:
				return [("activar", _("&Abrir en el navegador")), ("copiar", _("&Copiar la dirección")), *ir]
			return [("activar", _("&Abrir con su programa de Windows")), *archivo, ("copiar", _("&Copiar la ruta")), *ir]
		if clave == "archivos":
			return [("activar", _("&Ver los cambios")), archivo[0], ("abrir", _("Abrir con su &programa de Windows")),
				*archivo[1:], ("copiar", _("&Copiar la ruta")), ("contenido", _("Copiar el co&ntenido")),
				("guardar", _("&Guardar los cambios como archivo...")), *ir]
		if clave == "ordenes":
			return [("activar", _("&Ver la orden y su resultado")), ("copiar", _("&Copiar la orden")),
				("contenido", _("Copiar la &salida")), *ir]
		if clave == "tareas":
			return [("activar", _("&Ver la tarea")), ("copiar", _("&Copiar la tarea")), *ir]
		if clave == "subagentes":
			return [("activar", _("&Ver lo que hizo")), ("copiar", _("&Copiar su respuesta")), *ir]
		if clave == "decisiones":
			return [("activar", _("&Ver la decisión completa")), ("copiar", _("&Copiar la pregunta y la respuesta")), *ir]
		if clave == "errores":
			return [("activar", _("&Ver el error completo")), ("copiar", _("&Copiar el error")),
				("contenido", _("Copiar solo el &detalle")), *ir]
		if clave == "commits":
			copiar = _("&Copiar el hash") if elemento.hash else _("&Copiar el mensaje")
			acciones = [("activar", _("&Ver el commit completo")), ("copiar", copiar)]
			if elemento.hash and elemento.mensaje:
				acciones.append(("contenido", _("Copiar el &mensaje")))
			return [*acciones, *ir]
		if clave == "incidencias":
			if not elemento.url:
				return [("copiar", _("&Copiar la referencia")), *ir]
			return [("activar", _("&Abrir en el navegador")), ("copiar", _("&Copiar la dirección")), *ir]
		if clave == "leidos":
			return [("activar", _("&Leer el archivo entero")), ("abrir", _("Abrir con su &programa de Windows")),
				*archivo[1:], ("copiar", _("&Copiar la ruta")), ("contenido", _("Copiar el co&ntenido")), *ir]
		if clave == "mios":
			return [("activar", _("&Ir al mensaje")), ("citar", _("Citar en la &respuesta")),
				("copiar", _("&Copiar el mensaje"))]
		return [("activar", _("&Ver el hallazgo completo")), ("corregir", _("Preparar un mensaje para co&rregirlo")),
			("copiar", _("&Copiar el hallazgo")), ("editor", _("Abrir en el &editor en su línea")),
			("leer", _("&Leer el archivo")), ("explorador", _("&Mostrar en el Explorador")), *ir]

	# ── Ámbito y filtro ───────────────────────────────────────────────────────

	def _calcular_base(self, clave: str) -> list[Elemento]:
		"""Los elementos de la página en el ámbito elegido (antes del filtro)."""
		todos = self._sesion[clave]
		if not self._ambito_activo():
			return list(todos)
		if clave in PAGINAS_DE_MENSAJE:
			inicio, fin = self.indice_elegido, self.indice_elegido + 1
		else:
			inicio, fin = extraccion.rango_turno(self._mensajes, self.indice_elegido)
		if clave not in _RECALCULAR_EN_TURNO:
			return [elemento for elemento in todos if inicio <= elemento.indice < fin]
		# Lo que se agrupa (un enlace, un archivo...) se vuelve a calcular con los mensajes del turno.
		trozo = self._mensajes[inicio:fin]
		try:
			if clave == "enlaces":
				return extraccion.desplazar(extraccion.enlaces(trozo), inicio)
			if clave == "archivos":
				principales = extraccion.desplazar(extraccion.archivos_cambiados(trozo), inicio)
				del_turno = [s for s in self._sesion["subagentes"] if inicio <= s.indice < fin]
				return extraccion.combinar_cambios(principales, del_turno)
			if clave == "hallazgos":
				return extraccion.desplazar(extraccion.hallazgos(trozo), inicio)
			if clave == "commits":
				return extraccion.desplazar(extraccion.commits(trozo), inicio)
			if clave == "incidencias":
				return extraccion.desplazar(extraccion.incidencias(trozo, self._repositorio), inicio)
			return extraccion.desplazar(extraccion.archivos_leidos(trozo), inicio)
		except Exception:  # noqa: BLE001 - si algo falla, al menos lo que se sabe del turno
			log.warning("No se pudo calcular la página %s del turno", clave, exc_info=True)
			return [elemento for elemento in todos if inicio <= elemento.indice < fin]

	def _recalcular(self, claves: Sequence[str]) -> None:
		"""Vuelve a calcular el ámbito de esas páginas y las repinta con su filtro."""
		for clave in claves:
			self._base[clave] = self._calcular_base(clave)
			self._filas_base[clave] = [self._fila(clave, elemento) for elemento in self._base[clave]]
			self._aplicar_filtro(clave, conservar_foco=False)

	def _aplicar_filtro(self, clave: str, conservar_foco: bool = False) -> None:
		consulta = self._filtros.get(clave, "").strip()
		pares = list(zip(self._base[clave], self._filas_base[clave], strict=True))
		if consulta:
			pares = [(elemento, fila) for elemento, fila in pares
				if coincide(" ".join(fila) + "\n" + self._texto_para_filtrar(clave, elemento), consulta)]
		self.datos[clave] = [elemento for elemento, _fila in pares]
		self.listas[clave].reemplazar_filas([fila for _elemento, fila in pares], conservar_foco=conservar_foco)
		self.etiquetas[clave].SetLabel(self._etiqueta(clave))

	def filtrar(self, texto: str, clave: str | None = None) -> int:
		"""Deja en la página (la visible si no se dice) lo que contiene ``texto``. Devuelve cuántos quedan."""
		clave = clave or self.pagina_actual()
		self._filtros[clave] = texto
		if clave == self.pagina_actual() and self.filtro.GetValue() != texto:
			self.filtro.ChangeValue(texto)
		self._aplicar_filtro(clave)
		return len(self.datos[clave])

	def texto_cuenta(self, clave: str | None = None) -> str:
		"""«3 de 12», «Nada coincide de 12» o «12 elementos», para decirlo tras filtrar."""
		clave = clave or self.pagina_actual()
		total, visibles = len(self._base[clave]), len(self.datos[clave])
		if not self._filtros.get(clave, "").strip():
			return _("{n} elementos").format(n=total) if total != 1 else _("1 elemento")
		if not visibles:
			return _("Nada coincide; hay {m}").format(m=total)
		return _("{n} de {m}").format(n=visibles, m=total)

	def vaciar_filtro(self) -> None:
		"""Vacía el filtro de la página visible (Escape en el filtro) y lo dice."""
		self.filtrar("")
		voz.hablar(_("Filtro vacío. {cuenta}").format(cuenta=self.texto_cuenta()))

	def _al_escribir_filtro(self, evento: wx.CommandEvent) -> None:
		self.filtrar(self.filtro.GetValue())
		# Se dice cuántos quedan cuando se deja de escribir (no a cada letra).
		if self._temporizador_filtro is not None and self._temporizador_filtro.IsRunning():
			self._temporizador_filtro.Stop()
		self._temporizador_filtro = wx.CallLater(ESPERA_ANUNCIO_FILTRO, self._anunciar_cuenta)
		evento.Skip()

	def _anunciar_cuenta(self) -> None:
		try:
			if not self:
				return
			voz.hablar(self.texto_cuenta())
		except RuntimeError:
			return  # el diálogo ya no existe

	def _al_destruir(self, evento: wx.WindowDestroyEvent) -> None:
		if evento.GetEventObject() is self and self._temporizador_filtro is not None and self._temporizador_filtro.IsRunning():
			self._temporizador_filtro.Stop()
		evento.Skip()

	def cambiar_ambito(self, ambito: str, anunciar: bool = True) -> bool:
		"""Toda la sesión (``sesion``) o solo el mensaje elegido o su turno (``turno``). Se guarda."""
		if ambito not in AMBITOS:
			return False
		if ambito == "turno" and self.indice_elegido < 0:
			self.selector_ambito.SetSelection(AMBITOS.index(self.ambito))
			voz.hablar(_("No había ningún mensaje elegido al abrir las listas: se muestra toda la sesión. Elige un "
				"mensaje en la lista de mensajes y vuelve a abrirlas."))
			return False
		self.ambito = ambito
		self.selector_ambito.SetSelection(AMBITOS.index(ambito))
		if self._padre is not None and hasattr(self._padre, "poner_opcion"):
			try:
				self._padre.poner_opcion(OPCION_AMBITO, ambito)
			except Exception:  # noqa: BLE001 - si no se guarda, vale para esta vez
				log.warning("No se pudo guardar el ámbito de las listas", exc_info=True)
		self._recalcular(PAGINAS)
		if anunciar:
			clave = self.pagina_actual()
			if ambito == "sesion":
				donde = _("Toda la sesión")
			elif clave in PAGINAS_DE_MENSAJE:
				donde = _("Solo este mensaje")
			else:
				donde = _("Solo este turno")
			voz.hablar(_("{donde}. {pagina}: {cuenta}").format(donde=donde, pagina=_titulo_pagina(clave),
				cuenta=self.texto_cuenta(clave)))
		return True

	def alternar_ambito(self) -> bool:
		"""Ctrl+T: de toda la sesión a este mensaje o turno, y al revés."""
		return self.cambiar_ambito("sesion" if self.ambito == "turno" else "turno")

	def _al_elegir_ambito(self, evento: wx.CommandEvent) -> None:
		indice = self.selector_ambito.GetSelection()
		if 0 <= indice < len(AMBITOS) and AMBITOS[indice] != self.ambito:
			self.cambiar_ambito(AMBITOS[indice])
		evento.Skip()

	# ── Estado ────────────────────────────────────────────────────────────────

	def pagina_actual(self) -> str:
		"""Identificador de la página visible (uno de ``PAGINAS``)."""
		indice = self.libro.GetSelection()
		return PAGINAS[indice] if 0 <= indice < len(PAGINAS) else PAGINAS[0]

	def elemento_actual(self, clave: str | None = None) -> Elemento | None:
		"""Elemento seleccionado en la página indicada (o en la visible)."""
		clave = clave or self.pagina_actual()
		fila = self.listas[clave].fila_actual()
		elementos = self.datos.get(clave, [])
		return elementos[fila] if 0 <= fila < len(elementos) else None

	def _numero(self, clave: str, elemento: Elemento) -> int:
		"""Número del elemento en su página sin filtrar (para los títulos: «Bloque de código 3»)."""
		return next((posicion for posicion, otro in enumerate(self._base[clave], start=1) if otro is elemento),
			self.listas[clave].fila_actual() + 1)

	def _aviso_vacio(self, clave: str) -> str:
		"""Lo que se dice cuando no hay nada que elegir en la página: está vacía o el filtro lo oculta todo."""
		if self._base.get(clave) and not self.datos.get(clave):
			return _("Nada coincide con el filtro «{filtro}». Escape en el filtro lo vacía.").format(
				filtro=self._filtros.get(clave, "").strip())
		return _mensaje_vacio(clave, self._donde(clave))

	def _anunciar_si_vacia(self) -> None:
		try:
			if not self:
				return
			clave = self.pagina_actual()
		except RuntimeError:
			return  # el diálogo ya no existe
		if not self.datos[clave]:
			voz.hablar(self._aviso_vacio(clave))

	@staticmethod
	def _enfocar(control: wx.Window) -> None:
		"""Foco solo si se ve: un control oculto no debe robar el foco a nadie."""
		if control.IsShownOnScreen():
			control.SetFocus()

	def _foco_en_filtro(self) -> bool:
		return wx.Window.FindFocus() is self.filtro

	def _foco_fuera_del_libro(self) -> bool:
		return wx.Window.FindFocus() in (self.filtro, self.selector_ambito)

	def _terminar(self, codigo: int) -> None:
		if self.IsModal():
			self.EndModal(codigo)
		else:
			self.SetReturnCode(codigo)
			self.Hide()

	def resolver(self, ruta: str) -> Path | None:
		"""Ruta absoluta de un archivo citado (las relativas, desde la carpeta de la sesión)."""
		if not ruta:
			return None
		camino = Path(ruta)
		if camino.is_absolute() or es_ruta_de_red(ruta):
			return camino
		if self.carpeta:
			return Path(self.carpeta) / camino
		return None

	def _existe(self, ruta: str) -> bool | None:
		"""Si existe el archivo (``None`` si no se sabe: ruta relativa sin carpeta)."""
		if ruta not in self._existencia:
			camino = self.resolver(ruta)
			try:
				self._existencia[ruta] = None if camino is None else camino.exists()
			except OSError:
				self._existencia[ruta] = False
		return self._existencia[ruta]

	def ruta_actual(self, clave: str | None = None) -> tuple[str, int] | None:
		"""``(ruta escrita, línea)`` del elemento si señala un archivo; ``None`` si no."""
		clave = clave or self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if elemento is None:
			return None
		if clave == "enlaces" and elemento.es_archivo:
			return elemento.url, elemento.linea
		if clave == "archivos":
			return elemento.ruta, 0
		if clave == "leidos":
			return elemento.ruta, elemento.linea
		if clave == "hallazgos" and elemento.archivo:
			return elemento.archivo, elemento.linea
		return None

	# ── Subagentes ────────────────────────────────────────────────────────────

	def _completar_subagentes(self, transcritos: list[Any]) -> None:
		extraccion.completar_subagentes(self._sesion["subagentes"], transcritos)
		self._sesion["archivos"] = extraccion.combinar_cambios(self._archivos_principales, self._sesion["subagentes"])

	def _leer_subagentes_en_segundo_plano(self, ruta: Path) -> None:
		def leer() -> None:
			try:
				from app.services.historial import claude as historial_claude

				transcritos = historial_claude.leer_subagentes(ruta)
			except Exception:  # noqa: BLE001 - sin transcripts la página sigue con lo que se sabe
				log.warning("No se pudieron leer los subagentes de %s", ruta.name, exc_info=True)
				return
			if transcritos:
				wx.CallAfter(self._al_leer_subagentes, transcritos)

		threading.Thread(target=leer, name="listas-subagentes", daemon=True).start()

	def _al_leer_subagentes(self, transcritos: list[Any]) -> None:
		try:
			if not self:
				return
		except RuntimeError:
			return
		self._completar_subagentes(transcritos)
		for clave in ("subagentes", "archivos"):
			self._base[clave] = self._calcular_base(clave)
			self._filas_base[clave] = [self._fila(clave, elemento) for elemento in self._base[clave]]
			self._aplicar_filtro(clave, conservar_foco=True)

	# ── Acciones ──────────────────────────────────────────────────────────────

	def ejecutar(self, accion: str) -> None:
		"""Ejecuta una acción por su nombre (las de ``TECLAS_ACCION``)."""
		{
			"activar": self.activar, "leer": self.leer_archivo, "abrir": self.abrir_con_windows,
			"editor": self.abrir_en_editor, "explorador": self.mostrar_en_explorador, "corregir": self.preparar_correccion,
			"citar": self.citar_mensaje, "copiar": self.copiar, "contenido": self.copiar_contenido, "guardar": self.guardar,
			"ir": self.ir_al_mensaje,
		}[accion]()

	def ir_a_pagina(self, numero: int) -> None:
		"""Muestra la página ``numero`` (desde 0) y pone el foco en su lista."""
		if not 0 <= numero < len(PAGINAS):
			return
		if self.libro.GetSelection() != numero:
			self.libro.SetSelection(numero)  # genera el evento: anuncia si está vacía
		self._enfocar(self.listas[PAGINAS[numero]])

	def pasar_pagina(self, adelante: bool = True) -> None:
		"""Ctrl+Tab desde el filtro o «Mostrar»: cambia de página sin mover el foco y la nombra."""
		self.libro.AdvanceSelection(adelante)
		clave = self.pagina_actual()
		if self.datos[clave]:
			voz.hablar(_("{pagina}: {cuenta}").format(pagina=_titulo_pagina(clave), cuenta=self.texto_cuenta(clave)))

	def ir_al_filtro(self) -> None:
		"""Ctrl+F: lleva al filtro con su texto seleccionado."""
		if self.filtro.IsShownOnScreen():
			enfocar(self.filtro)

	def ir_a_lista(self) -> None:
		"""Del filtro a la lista de la página (Intro o Flecha abajo)."""
		clave = self.pagina_actual()
		if not self.datos[clave]:
			voz.hablar(self._aviso_vacio(clave))
			return
		self._enfocar(self.listas[clave])

	def activar(self) -> None:
		"""Acción principal del elemento seleccionado (Intro)."""
		clave = self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if elemento is None:
			voz.hablar(self._aviso_vacio(clave))
			return
		numero = self._numero(clave, elemento)
		if clave == "codigo":
			titulo = _("Bloque de código {n}").format(n=numero)
			if elemento.lenguaje:
				titulo = _("Bloque de código {n} ({lenguaje})").format(n=numero, lenguaje=elemento.lenguaje)
			mostrar_documento(self, titulo, _en_bloque(elemento.codigo, elemento.lenguaje), forzar_texto=True)
		elif clave == "enlaces":
			if elemento.es_archivo:
				self.abrir_con_windows()
				return
			self._abrir_web(elemento.url)
		elif clave == "archivos":
			if not elemento.diff.strip():
				voz.hablar(_("No se guardó el detalle de los cambios de este archivo. Control+L lee el archivo entero."))
				return
			# Con palabras («Línea 12, añadida: …»), cortas o con los signos del diff, según Preferencias.
			simbolos = self.estilo_cambios == "simbolos"
			mostrar_documento(self, _("Cambios en {ruta}").format(ruta=elemento.ruta),
				_en_bloque(extraccion.diff_legible(elemento.diff, self.estilo_cambios), "diff" if simbolos else ""),
				forzar_texto=True)
		elif clave == "ordenes":
			texto = _en_bloque(elemento.orden)
			if elemento.error is not None:
				texto += "\n## " + _("Resultado: {estado}").format(estado=_texto_resultado_orden(elemento).lower()) + "\n\n"
				texto += _en_bloque(elemento.salida) if elemento.salida.strip() else _("(sin salida)") + "\n"
			mostrar_documento(self, _("Orden {n}").format(n=numero), texto, forzar_texto=True)
		elif clave == "tareas":
			self._mostrar_tarea(elemento)
		elif clave == "subagentes":
			self._mostrar_subagente(elemento, numero)
		elif clave == "decisiones":
			titulo = _("Plan {n}") if elemento.tipo == "plan" else _("Pregunta {n}")
			mostrar_documento(self, titulo.format(n=numero), elemento.documento, forzar_texto=True)
		elif clave == "errores":
			mostrar_documento(self, _("Error {n}").format(n=numero), self._documento_error(elemento), forzar_texto=True)
		elif clave == "commits":
			titulo = _("Subida {n}") if elemento.tipo == "subida" else _("Commit {n}")
			mostrar_documento(self, titulo.format(n=numero), self._documento_commit(elemento), forzar_texto=True)
		elif clave == "incidencias":
			if not elemento.url:
				voz.hablar(_("No se sabe de qué repositorio es {referencia}. Control+C copia la referencia.").format(
					referencia=elemento.referencia))
				return
			self._abrir_web(elemento.url)
		elif clave == "leidos":
			self.leer_archivo()
		elif clave == "mios":
			self.ir_al_mensaje()
		else:
			mostrar_documento(self, _("Hallazgo {n}").format(n=numero), self._documento_hallazgo(elemento), forzar_texto=True)

	def _abrir_web(self, url: str) -> None:
		try:
			webbrowser.open(url)
		except webbrowser.Error:
			log.warning("No se pudo abrir %s en el navegador", url, exc_info=True)
			mostrar_error(self, _("No se pudo abrir el navegador."))
			return
		voz.hablar(_("Abriendo en el navegador"))

	def _mostrar_tarea(self, tarea: extraccion.Tarea) -> None:
		todas = self._sesion["tareas"]
		numero = next((posicion for posicion, otra in enumerate(todas, start=1) if otra is tarea), 1)
		partes = [
			"# " + _("Tarea {n} de {t}: {estado}").format(n=numero, t=len(todas), estado=_texto_estado_tarea(tarea.estado).lower()),
			"", tarea.texto,
		]
		if tarea.en_curso_texto and tarea.en_curso_texto != tarea.texto:
			partes += ["", _("Mientras la hace, el agente dice: {texto}").format(texto=tarea.en_curso_texto)]
		partes += ["", "## " + _("Toda la lista"), ""]
		partes += ["{n}. {t} ({e})".format(n=i, t=t.texto, e=_texto_estado_tarea(t.estado).lower())
			for i, t in enumerate(todas, start=1)]
		mostrar_documento(self, _("Tareas del agente"), "\n".join(partes) + "\n", forzar_texto=True)

	def _mostrar_subagente(self, subagente: extraccion.Subagente, numero: int) -> None:
		partes = ["# " + subagente.descripcion, ""]
		if subagente.tipo:
			partes.append(_("Tipo: {tipo}.").format(tipo=subagente.tipo))
		partes.append(_("Estado: {estado}.").format(estado=_texto_estado_subagente(subagente.estado).lower()))
		if subagente.pasos is None:
			partes.append(_("No se ha podido leer lo que hizo paso a paso: solo se conoce su encargo y su respuesta."))
		else:
			partes.append(_("Pasos: {p}. Ediciones de archivos: {e}.").format(p=subagente.pasos, e=subagente.ediciones))
		if subagente.archivos:
			partes += ["", "## " + _("Archivos que cambió"), ""]
			partes += ["- {r} ({c})".format(r=c.ruta, c=_texto_cambios(c)) for c in subagente.archivos]
		if subagente.ordenes:
			partes += ["", "## " + _("Órdenes que ejecutó"), ""]
			partes += ["- {o} ({r})".format(o=primera_linea(o.orden, 200), r=_texto_resultado_orden(o).lower()) for o in subagente.ordenes]
		if subagente.acciones:
			partes += ["", "## " + _("Lo que hizo"), ""]
			partes += ["{n}. {a}".format(n=i, a=accion) for i, accion in enumerate(subagente.acciones, start=1)]
		if subagente.instrucciones:
			partes += ["", "## " + _("Lo que se le encargó"), "", subagente.instrucciones]
		partes += ["", "## " + _("Su respuesta"), "", subagente.resultado or _("(todavía sin respuesta)")]
		mostrar_documento(self, _("Subagente {n}: {d}").format(n=numero, d=primera_linea(subagente.descripcion, 80)),
			"\n".join(partes) + "\n", forzar_texto=True)

	@staticmethod
	def _documento_error(fallo: extraccion.Fallo) -> str:
		partes = ["# " + fallo.titulo, ""]
		if fallo.tipo == "herramienta":
			partes.append(_("Herramienta que falló: {h}.").format(h=fallo.herramienta or _("sin nombre")))
		else:
			partes.append(_("Aviso de error."))
		if fallo.entrada.strip():
			partes += ["", "## " + _("Lo que se pidió"), "", _en_bloque(fallo.entrada).rstrip("\n")]
		partes += ["", "## " + _("Error"), "", _en_bloque(fallo.detalle).rstrip("\n") if fallo.detalle.strip() else _("(sin detalle)")]
		return "\n".join(partes) + "\n"

	@staticmethod
	def _documento_commit(commit: extraccion.Commit) -> str:
		partes = ["# " + _titulo_commit(commit), ""]
		datos = []
		if commit.hash:
			datos.append(_("Hash: {h}.").format(h=commit.hash))
		if commit.rama:
			datos.append(_("Rama: {r}.").format(r=commit.rama))
		datos.append(_("Resultado: {r}.").format(r=_texto_resultado_commit(commit).lower()))
		if commit.tipo == "otro":
			datos.append(_("Apareció en la salida de otra orden de git."))
		partes.append(" ".join(datos))
		if commit.mensaje.strip():
			titulo = _("Lo que se subió") if commit.tipo == "subida" else _("Mensaje completo")
			partes += ["", "## " + titulo, "", commit.mensaje.strip()]
		if commit.orden:
			partes += ["", "## " + _("Orden"), "", _en_bloque(commit.orden).rstrip("\n")]
		if commit.salida.strip():
			partes += ["", "## " + _("Salida"), "", _en_bloque(commit.salida).rstrip("\n")]
		return "\n".join(partes) + "\n"

	@staticmethod
	def _texto_hallazgo(hallazgo: extraccion.Hallazgo) -> str:
		lugar = hallazgo.archivo
		if hallazgo.archivo and hallazgo.linea:
			lugar = _("{archivo}, línea {linea}").format(archivo=hallazgo.archivo, linea=hallazgo.linea)
		cabecera = ", ".join(parte for parte in (hallazgo.categoria, lugar) if parte)
		texto = "{c}: {r}".format(c=cabecera, r=hallazgo.resumen) if cabecera else hallazgo.resumen
		if hallazgo.escenario:
			texto += "\n" + _("Cuándo falla: {e}").format(e=hallazgo.escenario)
		return texto

	def _documento_hallazgo(self, hallazgo: extraccion.Hallazgo) -> str:
		partes = ["# " + (hallazgo.corto or hallazgo.resumen), ""]
		if hallazgo.archivo:
			partes.append(_("Archivo: {a}.").format(a=hallazgo.archivo + (":{l}".format(l=hallazgo.linea) if hallazgo.linea else "")))
		if hallazgo.categoria:
			partes.append(_("Categoría: {c}.").format(c=hallazgo.categoria))
		if hallazgo.veredicto:
			partes.append(_("Veredicto: {v}.").format(v=extraccion.nombre_veredicto(hallazgo.veredicto).lower()))
		if hallazgo.resultado:
			partes.append(_("Resultado tras la corrección: {r}.").format(r=extraccion.nombre_resultado(hallazgo.resultado).lower()))
		partes += ["", "## " + _("Qué pasa"), "", hallazgo.resumen]
		if hallazgo.escenario:
			partes += ["", "## " + _("Cuándo falla"), "", hallazgo.escenario]
		return "\n".join(partes) + "\n"

	def texto_a_copiar(self, clave: str | None = None) -> str | None:
		"""Lo que copia Ctrl+C en la página indicada (o en la visible)."""
		clave = clave or self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if elemento is None:
			return None
		copiar: dict[str, Callable[[Any], str]] = {
			"codigo": lambda e: e.codigo,
			"enlaces": lambda e: e.url,
			"archivos": lambda e: e.ruta,
			"ordenes": lambda e: e.orden,
			"tareas": lambda e: e.texto,
			"subagentes": lambda e: e.resultado or e.descripcion,
			"decisiones": lambda e: _("{pregunta}\nTu respuesta: {respuesta}").format(pregunta=e.pregunta, respuesta=e.respuesta),
			"errores": lambda e: "{t}\n\n{d}".format(t=e.titulo, d=e.detalle).strip(),
			"commits": lambda e: e.hash or e.mensaje or _titulo_commit(e),
			"incidencias": lambda e: e.url or e.referencia,
			"leidos": lambda e: e.ruta,
			"mios": lambda e: e.texto,
			"hallazgos": self._texto_hallazgo,
		}
		return copiar[clave](elemento)

	def _copiar_texto(self, texto: str) -> bool:
		if copiar_al_portapapeles(texto):
			voz.hablar(_("Copiado"))
			return True
		voz.hablar(_("No se pudo copiar al portapapeles"))
		return False

	def copiar(self) -> bool:
		"""Copia el elemento seleccionado al portapapeles (Ctrl+C)."""
		clave = self.pagina_actual()
		texto = self.texto_a_copiar(clave)
		if texto is None:
			voz.hablar(self._aviso_vacio(clave))
			return False
		return self._copiar_texto(texto)

	def copiar_contenido(self) -> bool:
		"""Copia el contenido del archivo, la salida de la orden, el detalle del error o el mensaje del commit (Ctrl+Mayús+C)."""
		clave = self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if clave not in ("archivos", "ordenes", "errores", "commits", "leidos"):
			voz.hablar(_("Aquí no hay contenido que copiar: solo en los archivos, las órdenes, los errores y los commits"))
			return False
		if elemento is None:
			voz.hablar(self._aviso_vacio(clave))
			return False
		if clave == "ordenes":
			if not elemento.salida.strip():
				voz.hablar(_("Esta orden no tiene salida guardada"))
				return False
			return self._copiar_texto(elemento.salida)
		if clave == "errores":
			if not elemento.detalle.strip():
				voz.hablar(_("Este error no tiene detalle guardado"))
				return False
			return self._copiar_texto(elemento.detalle)
		if clave == "commits":
			if not elemento.mensaje.strip():
				voz.hablar(_("No se sabe el mensaje de este commit"))
				return False
			return self._copiar_texto(elemento.mensaje)
		camino = self.resolver(elemento.ruta)
		if camino is None:
			voz.hablar(_("No se sabe en qué carpeta está este archivo"))
			return False
		texto, motivo = leer_texto_archivo(camino)
		if texto is None:
			voz.hablar(motivo)
			return False
		return self._copiar_texto(texto)

	def _pedir_ruta_guardar(self, sugerido: str, comodin: str) -> Path | None:
		"""Cuadro «Guardar como» de Windows. Devuelve la ruta o ``None`` si se cancela."""
		dialogo = wx.FileDialog(
			self, _("Guardar como"), defaultFile=sugerido, wildcard=comodin, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		)
		try:
			return Path(dialogo.GetPath()) if dialogo.ShowModal() == wx.ID_OK else None
		finally:
			dialogo.Destroy()

	def guardar(self) -> Path | None:
		"""Guarda el bloque de código (o el diff del archivo) en un archivo (Ctrl+S)."""
		clave = self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if clave not in ("codigo", "archivos"):
			voz.hablar(_("Solo se pueden guardar los bloques de código y los cambios de archivos"))
			return None
		if elemento is None:
			voz.hablar(self._aviso_vacio(clave))
			return None
		if clave == "codigo":
			bloque: extraccion.BloqueCodigo = elemento
			extension = extension_para(bloque.lenguaje)
			sugerido = "bloque_{n}{ext}".format(n=self._numero(clave, bloque), ext=extension)
			contenido = bloque.codigo if bloque.codigo.endswith("\n") else bloque.codigo + "\n"
		else:
			cambio: extraccion.Cambio = elemento
			if not cambio.diff.strip():
				voz.hablar(_("No se guardó el detalle de los cambios de este archivo"))
				return None
			extension = ".diff"
			sugerido = "{nombre}.diff".format(nombre=Path(cambio.ruta.replace("\\", "/")).name or "cambios")
			contenido = cambio.diff if cambio.diff.endswith("\n") else cambio.diff + "\n"
		comodin = _("Archivo {ext} (*{ext})|*{ext}|Todos los archivos (*.*)|*.*").format(ext=extension)
		ruta = self._pedir_ruta_guardar(sugerido, comodin)
		if ruta is None:
			return None
		try:
			escribir_atomico(ruta, contenido)
		except OSError as error:
			log.warning("No se pudo guardar %s", ruta, exc_info=True)
			mostrar_error(self, _("No se pudo guardar el archivo:\n{error}").format(error=error))
			return None
		voz.hablar(_("Guardado en {nombre}").format(nombre=ruta.name))
		return ruta

	def _camino_actual(self) -> tuple[Path, int] | None:
		"""Ruta resuelta y línea del archivo del elemento; si no hay, lo dice y devuelve ``None``."""
		clave = self.pagina_actual()
		if self.elemento_actual(clave) is None:
			voz.hablar(self._aviso_vacio(clave))
			return None
		datos = self.ruta_actual(clave)
		if datos is None:
			voz.hablar(_("Este elemento no es un archivo"))
			return None
		camino = self.resolver(datos[0])
		if camino is None:
			voz.hablar(_("No se sabe en qué carpeta está este archivo. Control+C copia la ruta."))
			return None
		return camino, datos[1]

	def leer_archivo(self) -> bool:
		"""Muestra el contenido actual del archivo, leído del disco (Ctrl+L)."""
		datos = self._camino_actual()
		if datos is None:
			return False
		camino, _linea = datos
		texto, motivo = leer_texto_archivo(camino)
		if texto is None:
			voz.hablar(motivo)
			return False
		lenguaje = camino.suffix.lstrip(".").lower()
		mostrar_documento(self, _("Archivo {ruta}").format(ruta=camino), _en_bloque(texto, lenguaje), forzar_texto=True)
		return True

	def abrir_con_windows(self) -> bool:
		"""Abre el archivo con su programa de Windows, salvo programas y scripts (Ctrl+O)."""
		clave = self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if clave == "enlaces" and elemento is not None and not elemento.es_archivo:
			self.activar()
			return True
		datos = self._camino_actual()
		if datos is None:
			return False
		camino, _linea = datos
		try:
			existe = camino.exists()
		except OSError:
			existe = False
		if not existe:
			voz.hablar(_("Este archivo ya no existe en este equipo. Control+C copia la ruta."))
			return False
		if camino.is_file() and es_peligroso(camino):
			voz.hablar(_(
				"Por seguridad, Ctrl+IA no abre programas, scripts ni accesos directos ({ext}): se ejecutarían. "
				"Control+E lo abre en el editor y Control+Mayúsculas+E lo muestra en el Explorador."
			).format(ext=camino.suffix.lower()))
			return False
		try:
			os.startfile(camino)  # noqa: S606 - archivo elegido por el usuario; programas y scripts excluidos arriba
		except OSError as error:
			log.warning("No se pudo abrir %s", camino, exc_info=True)
			mostrar_error(self, _("No se pudo abrir el archivo:\n{error}").format(error=error))
			return False
		voz.hablar(_("Abriendo {nombre}").format(nombre=camino.name))
		return True

	def abrir_en_editor(self) -> bool:
		"""Abre el archivo en el editor de código, en su línea si se sabe (Ctrl+E)."""
		datos = self._camino_actual()
		if datos is None:
			return False
		camino, linea = datos
		if not camino.is_file():
			voz.hablar(_("Este archivo ya no existe en este equipo. Control+C copia la ruta."))
			return False
		from app.services.agentes import procesos

		editor = _buscar_editor(self.editor)
		if not procesos.lanzar_independiente(orden_editor(editor, str(camino), linea), camino.parent):
			mostrar_error(self, _(
				"No se pudo abrir el editor. Elige el programa en Preferencias, Agentes, «Editor de código»."
			))
			return False
		voz.hablar(_("Abriendo en el editor"))
		return True

	def mostrar_en_explorador(self) -> bool:
		"""Abre el Explorador con el archivo seleccionado, o su carpeta si ya no está (Ctrl+Mayús+E)."""
		datos = self._camino_actual()
		if datos is None:
			return False
		camino, _linea = datos
		from app.services.agentes import procesos

		if camino.exists():
			orden = ["explorer.exe", "/select,{r}".format(r=camino)]
		elif camino.parent.is_dir():
			voz.hablar(_("El archivo ya no está; se abre su carpeta."))
			orden = ["explorer.exe", str(camino.parent)]
		else:
			voz.hablar(_("Ni el archivo ni su carpeta existen en este equipo."))
			return False
		return procesos.lanzar_independiente(orden)

	def preparar_correccion(self) -> bool:
		"""Prepara un mensaje para que el agente corrija el hallazgo (Ctrl+R). Nunca lo envía."""
		clave = self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if clave != "hallazgos":
			voz.hablar(_("Solo se puede preparar una corrección desde la página de hallazgos"))
			return False
		if elemento is None:
			voz.hablar(self._aviso_vacio(clave))
			return False
		texto = _("Corrige este hallazgo de la revisión de código:") + "\n\n" + self._texto_hallazgo(elemento)
		self.mensaje_preparado = texto
		cuadro = getattr(self._padre, "respuesta", None) if self._padre is not None else None
		if self.propia and isinstance(cuadro, wx.TextCtrl):
			actual = cuadro.GetValue().rstrip()
			cuadro.SetValue(actual + "\n\n" + texto if actual else texto)
			cuadro.SetInsertionPointEnd()
			voz.hablar(_("Mensaje preparado en el cuadro de respuesta. Revísalo y envíalo con Control+Intro; "
				"Alt+3 lleva al cuadro."))
			self._terminar(wx.ID_OK)
			return True
		if copiar_al_portapapeles(texto):
			voz.hablar(_("Esta sesión no se puede escribir desde aquí: el mensaje se ha copiado al portapapeles."))
			return True
		voz.hablar(_("No se pudo copiar al portapapeles"))
		return False

	def citar_mensaje(self) -> bool:
		"""Cita en el cuadro de respuesta el mensaje tuyo elegido y cierra (Ctrl+R en «Tus mensajes»). Nunca lo envía."""
		clave = self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if clave != "mios":
			voz.hablar(_("Solo se pueden citar tus mensajes, desde la página Tus mensajes"))
			return False
		if elemento is None:
			voz.hablar(self._aviso_vacio(clave))
			return False
		citar = getattr(self._padre, "citar_en_respuesta", None) if self._padre is not None else None
		if self.propia and callable(citar):
			# La ventana principal lo añade como cita («> ») y dice cómo seguir (Alt+3).
			if not citar(elemento.texto):
				return False
			self.mensaje_citado = elemento.texto
			self._terminar(wx.ID_OK)
			return True
		if copiar_al_portapapeles(_citado(elemento.texto)):
			self.mensaje_citado = elemento.texto
			voz.hablar(_("Esta sesión no se puede escribir desde aquí: la cita se ha copiado al portapapeles."))
			return True
		voz.hablar(_("No se pudo copiar al portapapeles"))
		return False

	def ir_al_mensaje(self) -> None:
		"""Cierra el diálogo y deja en ``indice_mensaje`` el mensaje del elemento (Alt+Intro)."""
		clave = self.pagina_actual()
		elemento = self.elemento_actual(clave)
		if elemento is None:
			voz.hablar(self._aviso_vacio(clave))
			return
		if not 0 <= elemento.indice:
			voz.hablar(_("Este elemento no tiene un mensaje al que ir"))
			return
		self.indice_mensaje = elemento.indice
		self._terminar(wx.ID_OK)

	# ── Eventos ───────────────────────────────────────────────────────────────

	def _al_cambiar_pagina(self, evento: wx.BookCtrlEvent) -> None:
		evento.Skip()
		indice = evento.GetSelection()
		if not 0 <= indice < len(PAGINAS):
			return
		clave = PAGINAS[indice]
		# El filtro de arriba muestra el de la página nueva (cada una recuerda el suyo).
		self.filtro.ChangeValue(self._filtros.get(clave, ""))
		if not self.datos[clave]:
			voz.hablar(self._aviso_vacio(clave))

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		# atajo: Ctrl+1 a Ctrl+9 ir a cada una de las nueve primeras páginas
		# atajo: Ctrl+0 ir a la décima página (Commits)
		# atajo: Ctrl+F ir al filtro
		# atajo: Ctrl+T cambiar entre toda la sesión y este mensaje o turno
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		if modificadores == wx.MOD_CONTROL and ord("0") <= tecla <= ord("9"):
			self.ir_a_pagina(PAGINAS_CON_ATAJO - 1 if tecla == ord("0") else tecla - ord("1"))
			return
		if modificadores == wx.MOD_CONTROL and tecla == ord("F"):
			self.ir_al_filtro()
			return
		if modificadores == wx.MOD_CONTROL and tecla == ord("T"):
			self.alternar_ambito()
			return
		en_filtro = self._foco_en_filtro()
		if en_filtro and modificadores == wx.MOD_NONE and tecla == wx.WXK_ESCAPE and self.filtro.GetValue():
			self.vaciar_filtro()
			return
		if en_filtro and modificadores == wx.MOD_NONE and tecla == wx.WXK_DOWN:
			self.ir_a_lista()
			return
		if tecla == wx.WXK_TAB and modificadores in (wx.MOD_CONTROL, wx.MOD_CONTROL | wx.MOD_SHIFT) and self._foco_fuera_del_libro():
			self.pasar_pagina(adelante=modificadores == wx.MOD_CONTROL)
			return
		if en_filtro:
			evento.Skip()  # en el filtro, Ctrl+C, Ctrl+V… son del cuadro de texto
			return
		acciones = {
			(wx.MOD_CONTROL, ord("C")): "copiar",
			(wx.MOD_CONTROL | wx.MOD_SHIFT, ord("C")): "contenido",
			(wx.MOD_CONTROL, ord("S")): "guardar",
			(wx.MOD_CONTROL, ord("L")): "leer",
			(wx.MOD_CONTROL, ord("O")): "abrir",
			(wx.MOD_CONTROL, ord("E")): "editor",
			(wx.MOD_CONTROL | wx.MOD_SHIFT, ord("E")): "explorador",
			(wx.MOD_CONTROL, ord("R")): "corregir",
			(wx.MOD_ALT, wx.WXK_RETURN): "ir",
			(wx.MOD_ALT, wx.WXK_NUMPAD_ENTER): "ir",
		}
		accion = acciones.get((modificadores, tecla))
		if accion == "corregir" and self.pagina_actual() == "mios":
			accion = "citar"
		if accion is not None:
			self.ejecutar(accion)
			return
		evento.Skip()


def contar(mensajes: Sequence[MensajeVista]) -> dict[str, int]:
	"""Cuántos elementos hay en cada página (para menús o anuncios de la ventana principal)."""
	return {
		"codigo": len(extraccion.bloques_codigo(mensajes)),
		"enlaces": len(extraccion.enlaces(mensajes)),
		"archivos": len(extraccion.archivos_cambiados(mensajes)),
		"ordenes": len(extraccion.ordenes(mensajes)),
		"tareas": len(extraccion.tareas(mensajes)),
		"subagentes": len(extraccion.subagentes(mensajes)),
		"hallazgos": len(extraccion.hallazgos(mensajes)),
		"decisiones": len(extraccion.decisiones(mensajes)),
		"errores": len(extraccion.errores(mensajes)),
		"commits": len(extraccion.commits(mensajes)),
		"incidencias": len(extraccion.incidencias(mensajes)),
		"leidos": len(extraccion.archivos_leidos(mensajes)),
		"mios": len(extraccion.mensajes_usuario(mensajes)),
	}
