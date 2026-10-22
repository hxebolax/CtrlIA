"""Exporta una conversación a Markdown, HTML o texto plano.

Sin interfaz (no importa wx). El formato se deduce de la extensión del
archivo (``.md``, ``.html``/``.htm``, ``.txt``) o se indica a mano.

- **Markdown**: encabezado con el título y los datos de la sesión; cada
  mensaje con ``### Quién · fecha y hora`` y su texto tal cual.
- **HTML**: página autónoma y accesible (``lang="es"``, un ``<h1>``, cada
  mensaje en un ``<section>`` con su ``<h2>``, código en ``<pre><code>``).
  El HTML crudo de los mensajes se escapa (``markdown_seguro``), los enlaces
  que no son http, https o mailto se anulan y las imágenes remotas se
  sustituyen por su texto alternativo: la página no ejecuta nada ni carga
  nada de fuera.
- **Texto**: texto plano para cualquier editor.

La escritura es atómica: se escribe un temporal en la misma carpeta y se
sustituye el destino con ``os.replace`` (un fallo a medias no deja el
archivo cortado). Siempre en UTF-8.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.gestor import MensajeVista
from app.ui.comun import NOMBRES_AGENTE, _, markdown_seguro, nombre_agente

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Conversacion:
	"""Una sesión para exportar dentro de un grupo."""

	titulo: str
	mensajes: list[MensajeVista]
	datos: dict[str, Any] = field(default_factory=dict)

#: Formatos admitidos y sus extensiones.
EXTENSIONES = {".md": "md", ".markdown": "md", ".html": "html", ".htm": "html", ".txt": "txt"}
_ALIAS_FORMATO = {"md": "md", "markdown": "md", "html": "html", "htm": "html", "txt": "txt", "texto": "txt"}


#: Nombre visible de los datos conocidos del encabezado.
_ETIQUETAS_DATOS = {
	"agente": "Agente",
	"carpeta": "Carpeta",
	"modelo": "Modelo",
	"origen": "Origen",
	"creada": "Creada",
	"estado": "Estado",
}

_ESTILO = """
:root { color-scheme: light dark; }
body { font-family: "Segoe UI", system-ui, sans-serif; font-size: 1.05rem; line-height: 1.55;
	margin: 0 auto; padding: 1.5rem; max-width: 60rem; background: #ffffff; color: #1b1b1b; }
h1 { font-size: 1.8rem; margin-top: 0; }
h2 { font-size: 1.2rem; margin: 0 0 0.5rem; color: #0b3d91; }
dl.datos { display: grid; grid-template-columns: max-content 1fr; gap: 0.2rem 1rem; }
dl.datos dt { font-weight: bold; }
dl.datos dd { margin: 0; }
section { border-top: 1px solid #767676; padding: 0.8rem 0; }
pre { background: #f3f3f3; color: #1b1b1b; border: 1px solid #767676; padding: 0.6rem; overflow-x: auto;
	white-space: pre-wrap; word-wrap: break-word; }
code { font-family: Consolas, "Cascadia Mono", monospace; }
a { color: #0645ad; }
:focus { outline: 3px solid #1a5fb4; outline-offset: 2px; }
@media (prefers-color-scheme: dark) {
	body { background: #121212; color: #f0f0f0; }
	h2 { color: #9cc3ff; }
	pre { background: #1e1e1e; color: #f0f0f0; border-color: #8a8a8a; }
	a { color: #8ab4f8; }
}
"""

_RE_HREF = re.compile(r'href="(?!https?:|mailto:|#)[^"]*"', re.IGNORECASE)
_RE_IMAGEN = re.compile(r"<img\b[^>]*?>", re.IGNORECASE)
_RE_ALT = re.compile(r'alt="([^"]*)"', re.IGNORECASE)
_RE_ENCABEZADO = re.compile(r"<(/?)h([1-6])\b", re.IGNORECASE)


def comodin_exportar() -> str:
	"""Comodín para ``wx.FileDialog`` (el primero, Markdown, es el predeterminado)."""
	return _("Markdown (*.md)|*.md|Página web (*.html)|*.html;*.htm|Texto (*.txt)|*.txt")


# ── Utilidades ──────────────────────────────────────────────────────────────────


def deducir_formato(ruta: Path, formato: str | None = None) -> str:
	"""``"md"``, ``"html"`` o ``"txt"`` según ``formato`` o la extensión de ``ruta``.

	:raises ValueError: si el formato o la extensión no se reconocen.
	"""
	if formato:
		normalizado = _ALIAS_FORMATO.get(formato.lower().lstrip("."))
		if normalizado is None:
			raise ValueError(_("Formato de exportación desconocido: {formato}").format(formato=formato))
		return normalizado
	normalizado = EXTENSIONES.get(Path(ruta).suffix.lower())
	if normalizado is None:
		raise ValueError(
			_("No se reconoce la extensión «{ext}». Usa .md, .html o .txt.").format(ext=Path(ruta).suffix or "")
		)
	return normalizado


def _fecha_completa(segundos: float) -> str:
	"""«17/10/2026 18:30» (en un archivo, una fecha relativa quedaría desfasada)."""
	if not segundos:
		return ""
	try:
		return dt.datetime.fromtimestamp(segundos).strftime("%d/%m/%Y %H:%M")
	except (OverflowError, OSError, ValueError):
		log.debug("Fecha no válida en la exportación: %s", segundos, exc_info=True)
		return ""


def _quien(mensaje: MensajeVista) -> str:
	"""Quién habla, con el nombre de la herramienta si lo hay."""
	nombre = _(mensaje.nombre_rol)
	if mensaje.rol == "herramienta" and mensaje.herramienta:
		return "{rol} ({herramienta})".format(rol=nombre, herramienta=mensaje.herramienta)
	return nombre


def _cabecera_mensaje(mensaje: MensajeVista) -> str:
	"""«Agente · 17/10/2026 18:30»."""
	fecha = _fecha_completa(mensaje.fecha)
	return "{quien} · {fecha}".format(quien=_quien(mensaje), fecha=fecha) if fecha else _quien(mensaje)


def _datos_encabezado(datos: Mapping[str, Any] | None) -> list[tuple[str, str]]:
	"""``[(etiqueta, valor)]`` del encabezado, más la fecha de exportación."""
	filas: list[tuple[str, str]] = []
	for clave, valor in (datos or {}).items():
		if valor is None or valor == "" or valor == []:
			continue
		etiqueta = _ETIQUETAS_DATOS.get(str(clave).lower(), str(clave)[:1].upper() + str(clave)[1:])
		texto = ", ".join(str(v) for v in valor) if isinstance(valor, (list, tuple)) else str(valor)
		if str(clave).lower() == "agente" and texto in NOMBRES_AGENTE:
			texto = nombre_agente(texto)
		filas.append((_(etiqueta), texto))
	filas.append((_("Exportada el"), dt.datetime.now().strftime("%d/%m/%Y %H:%M")))
	return filas


def _valla(texto: str) -> str:
	"""Valla de bloque más larga que cualquier racha de comillas invertidas del texto."""
	racha = max((len(m) for m in re.findall(r"`+", texto or "")), default=0)
	return "`" * max(3, racha + 1)


def _extra(mensaje: MensajeVista) -> tuple[str, str]:
	"""``(lenguaje, contenido)`` que se añade tras el texto: diff o detalle de herramienta."""
	if mensaje.rol == "cambio":
		diffs = [str(c.get("diff") or "") for c in (mensaje.cambios or []) if isinstance(c, dict)]
		diff = "\n\n".join(d for d in diffs if d.strip()) or mensaje.detalle
		return ("diff", diff) if (diff or "").strip() else ("", "")
	if mensaje.detalle and mensaje.detalle.strip() and mensaje.detalle.strip() != (mensaje.texto or "").strip():
		return ("", mensaje.detalle) if mensaje.rol in ("herramienta", "error", "sistema") else ("", "")
	return ("", "")


# ── Formatos ────────────────────────────────────────────────────────────────────


def a_markdown(mensajes: Sequence[MensajeVista], titulo: str, datos: Mapping[str, Any] | None = None) -> str:
	"""La conversación en Markdown."""
	partes = ["# {titulo}".format(titulo=titulo or _("Conversación")), ""]
	for etiqueta, valor in _datos_encabezado(datos):
		partes.append("- **{etiqueta}:** {valor}".format(etiqueta=etiqueta, valor=valor))
	partes.append("")
	for mensaje in mensajes:
		partes.append("### {cabecera}".format(cabecera=_cabecera_mensaje(mensaje)))
		partes.append("")
		partes.append((mensaje.texto or "").rstrip())
		lenguaje, extra = _extra(mensaje)
		if extra:
			valla = _valla(extra)
			partes.extend(["", valla + lenguaje, extra.rstrip("\n"), valla])
		partes.append("")
	return "\n".join(partes).rstrip("\n") + "\n"


def _cuerpo_html(texto: str, desplazamiento: int = 2) -> str:
	"""Markdown de un mensaje → HTML seguro y con los encabezados por debajo del de su mensaje."""
	import markdown

	cuerpo = markdown.markdown(markdown_seguro(texto or ""), extensions=["extra", "sane_lists"], output_format="html")
	cuerpo = _RE_HREF.sub('href="#"', cuerpo)

	def imagen_a_texto(coincidencia: re.Match[str]) -> str:
		alternativo = _RE_ALT.search(coincidencia.group(0))
		return alternativo.group(1) if alternativo else ""

	cuerpo = _RE_IMAGEN.sub(imagen_a_texto, cuerpo)
	return _RE_ENCABEZADO.sub(lambda m: "<{barra}h{nivel}".format(barra=m.group(1), nivel=min(6, int(m.group(2)) + desplazamiento)), cuerpo)


def a_html(mensajes: Sequence[MensajeVista], titulo: str, datos: Mapping[str, Any] | None = None) -> str:
	"""La conversación como página HTML autónoma y accesible."""
	titulo = titulo or _("Conversación")
	partes = [
		"<!DOCTYPE html>",
		'<html lang="es">',
		"<head>",
		'<meta charset="utf-8">',
		'<meta name="viewport" content="width=device-width, initial-scale=1">',
		"<title>{t}</title>".format(t=html.escape(titulo)),
		"<style>{estilo}</style>".format(estilo=_ESTILO),
		"</head>",
		"<body>",
		"<header>",
		"<h1>{t}</h1>".format(t=html.escape(titulo)),
		'<dl class="datos">',
	]
	for etiqueta, valor in _datos_encabezado(datos):
		partes.append("<dt>{e}</dt><dd>{v}</dd>".format(e=html.escape(etiqueta), v=html.escape(valor)))
	partes.extend(["</dl>", "</header>", "<main>"])
	for numero, mensaje in enumerate(mensajes, start=1):
		identificador = "m{n}".format(n=numero)
		partes.append('<section aria-labelledby="{i}">'.format(i=identificador))
		partes.append('<h2 id="{i}">{c}</h2>'.format(i=identificador, c=html.escape(_cabecera_mensaje(mensaje))))
		partes.append(_cuerpo_html(mensaje.texto))
		lenguaje, extra = _extra(mensaje)
		if extra:
			clase = ' class="language-{l}"'.format(l=lenguaje) if lenguaje else ""
			partes.append("<pre><code{c}>{x}</code></pre>".format(c=clase, x=html.escape(extra.rstrip("\n"))))
		partes.append("</section>")
	partes.extend(["</main>", "</body>", "</html>"])
	return "\n".join(partes) + "\n"


def a_texto(mensajes: Sequence[MensajeVista], titulo: str, datos: Mapping[str, Any] | None = None) -> str:
	"""La conversación en texto plano."""
	titulo = titulo or _("Conversación")
	partes = [titulo, "=" * min(len(titulo), 70), ""]
	for etiqueta, valor in _datos_encabezado(datos):
		partes.append("{etiqueta}: {valor}".format(etiqueta=etiqueta, valor=valor))
	partes.append("")
	for mensaje in mensajes:
		cabecera = _cabecera_mensaje(mensaje)
		partes.extend(["-" * 40, cabecera, ""])
		partes.append((mensaje.texto or "").rstrip())
		_lenguaje, extra = _extra(mensaje)
		if extra:
			partes.extend(["", extra.rstrip("\n")])
		partes.append("")
	return "\n".join(partes).rstrip("\n") + "\n"


_GENERADORES = {"md": a_markdown, "html": a_html, "txt": a_texto}


# ── Grupos enteros ──────────────────────────────────────────────────────────────


def _markdown_grupo(titulo: str, descripcion: str, conversaciones: Sequence[Conversacion]) -> str:
	partes = ["# {t}".format(t=titulo), ""]
	if descripcion.strip():
		partes.extend([descripcion.strip(), ""])
	partes.append("- **{e}:** {v}".format(e=_("Sesiones"), v=len(conversaciones)))
	partes.append("- **{e}:** {v}".format(e=_("Exportado el"), v=dt.datetime.now().strftime("%d/%m/%Y %H:%M")))
	partes.extend(["", "## {i}".format(i=_("Índice")), ""])
	for numero, conversacion in enumerate(conversaciones, start=1):
		partes.append("{n}. {t}".format(n=numero, t=conversacion.titulo or _("Conversación")))
	partes.append("")
	for conversacion in conversaciones:
		partes.extend(["## {t}".format(t=conversacion.titulo or _("Conversación")), ""])
		for etiqueta, valor in _datos_encabezado(conversacion.datos)[:-1]:
			partes.append("- **{etiqueta}:** {valor}".format(etiqueta=etiqueta, valor=valor))
		partes.append("")
		for mensaje in conversacion.mensajes:
			partes.extend(["#### {c}".format(c=_cabecera_mensaje(mensaje)), "", (mensaje.texto or "").rstrip()])
			lenguaje, extra = _extra(mensaje)
			if extra:
				valla = _valla(extra)
				partes.extend(["", valla + lenguaje, extra.rstrip("\n"), valla])
			partes.append("")
	return "\n".join(partes).rstrip("\n") + "\n"


def _html_grupo(titulo: str, descripcion: str, conversaciones: Sequence[Conversacion]) -> str:
	partes = [
		"<!DOCTYPE html>", '<html lang="es">', "<head>", '<meta charset="utf-8">',
		'<meta name="viewport" content="width=device-width, initial-scale=1">',
		"<title>{t}</title>".format(t=html.escape(titulo)), "<style>{e}</style>".format(e=_ESTILO), "</head>", "<body>",
		"<header>", "<h1>{t}</h1>".format(t=html.escape(titulo)),
	]
	if descripcion.strip():
		partes.append(_cuerpo_html(descripcion, desplazamiento=1))
	partes.extend(["</header>", '<nav aria-label="{i}"><h2>{i}</h2><ol>'.format(i=html.escape(_("Índice")))])
	for numero, conversacion in enumerate(conversaciones, start=1):
		partes.append('<li><a href="#s{n}">{t}</a></li>'.format(n=numero, t=html.escape(conversacion.titulo or _("Conversación"))))
	partes.extend(["</ol></nav>", "<main>"])
	for numero, conversacion in enumerate(conversaciones, start=1):
		partes.append('<article aria-labelledby="s{n}">'.format(n=numero))
		partes.append('<h2 id="s{n}">{t}</h2>'.format(n=numero, t=html.escape(conversacion.titulo or _("Conversación"))))
		partes.append('<dl class="datos">')
		for etiqueta, valor in _datos_encabezado(conversacion.datos)[:-1]:
			partes.append("<dt>{e}</dt><dd>{v}</dd>".format(e=html.escape(etiqueta), v=html.escape(valor)))
		partes.append("</dl>")
		for indice, mensaje in enumerate(conversacion.mensajes, start=1):
			identificador = "s{n}m{i}".format(n=numero, i=indice)
			partes.append('<section aria-labelledby="{i}">'.format(i=identificador))
			partes.append('<h3 id="{i}">{c}</h3>'.format(i=identificador, c=html.escape(_cabecera_mensaje(mensaje))))
			partes.append(_cuerpo_html(mensaje.texto, desplazamiento=3))
			lenguaje, extra = _extra(mensaje)
			if extra:
				clase = ' class="language-{l}"'.format(l=lenguaje) if lenguaje else ""
				partes.append("<pre><code{c}>{x}</code></pre>".format(c=clase, x=html.escape(extra.rstrip("\n"))))
			partes.append("</section>")
		partes.append("</article>")
	partes.extend(["</main>", "</body>", "</html>"])
	return "\n".join(partes) + "\n"


def _texto_grupo(titulo: str, descripcion: str, conversaciones: Sequence[Conversacion]) -> str:
	partes = [titulo, "=" * min(len(titulo), 70), ""]
	if descripcion.strip():
		partes.extend([descripcion.strip(), ""])
	partes.append("{e}: {v}".format(e=_("Sesiones"), v=len(conversaciones)))
	partes.append("")
	for numero, conversacion in enumerate(conversaciones, start=1):
		partes.extend(["#" * 40, "{n}. {t}".format(n=numero, t=conversacion.titulo or _("Conversación")), ""])
		cuerpo = a_texto(conversacion.mensajes, conversacion.titulo, conversacion.datos)
		partes.append(cuerpo.split("\n", 2)[2] if cuerpo.count("\n") >= 2 else cuerpo)
	return "\n".join(partes).rstrip("\n") + "\n"


_GENERADORES_GRUPO = {"md": _markdown_grupo, "html": _html_grupo, "txt": _texto_grupo}


def exportar_grupo(
	conversaciones: Sequence[Conversacion],
	ruta: Path,
	titulo: str,
	descripcion: str = "",
	formato: str | None = None,
) -> Path:
	"""Exporta todas las sesiones de un grupo a UN archivo, con índice, y devuelve la ruta escrita.

	Cada sesión es una sección (``##`` en Markdown, ``<article>`` con ``<h2>`` en
	HTML) con sus datos y sus mensajes, en el orden del grupo.
	"""
	ruta = Path(ruta)
	tipo = deducir_formato(ruta, formato)
	contenido = _GENERADORES_GRUPO[tipo](titulo or _("Grupo"), descripcion or "", list(conversaciones))
	escribir_atomico(ruta, contenido)
	log.info("Grupo exportado en %s (%s, %s sesiones)", ruta, tipo, len(conversaciones))
	return ruta


def escribir_atomico(ruta: Path, contenido: str) -> None:
	"""Escribe ``contenido`` en UTF-8 sin dejar nunca el archivo a medias."""
	ruta = Path(ruta)
	ruta.parent.mkdir(parents=True, exist_ok=True)
	descriptor, temporal = tempfile.mkstemp(prefix=".exportando-", suffix=".tmp", dir=str(ruta.parent))
	try:
		with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
			archivo.write(contenido)
			archivo.flush()
			os.fsync(archivo.fileno())
		os.replace(temporal, ruta)
	except BaseException:
		try:
			os.unlink(temporal)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
		raise


def exportar(
	mensajes: Sequence[MensajeVista],
	ruta: Path,
	titulo: str,
	formato: str | None = None,
	datos: dict | None = None,
) -> Path:
	"""Exporta la conversación a ``ruta`` y devuelve la ruta escrita.

	:param formato: ``"md"``, ``"html"`` o ``"txt"``; si falta, se deduce de la extensión.
	:param datos: datos del encabezado (``agente``, ``carpeta``, ``modelo``... o
		cualquier otra clave, que se muestra con su nombre).
	:raises ValueError: formato desconocido.
	:raises OSError: no se pudo escribir.
	"""
	ruta = Path(ruta)
	tipo = deducir_formato(ruta, formato)
	contenido = _GENERADORES[tipo](list(mensajes), titulo, datos)
	escribir_atomico(ruta, contenido)
	log.info("Conversación exportada en %s (%s, %s mensajes)", ruta, tipo, len(mensajes))
	return ruta
