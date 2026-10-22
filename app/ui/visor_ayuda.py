"""Visor accesible del manual (F1), la lista de teclas y las novedades.

Dos modos, elegidos al abrir:

- **HTML** (si están instalados ``markdown`` y el motor Edge de
  ``wx.html2``): el Markdown se convierte en una página con encabezados
  ``h1``-``h3``, listas y tablas reales. Con NVDA o JAWS se navega en modo
  exploración: H y 1-3 saltan entre encabezados, T entre tablas, L entre listas.
- **Texto** (siempre disponible): un campo de solo lectura con el texto limpio
  (sin almohadillas ni asteriscos) y, delante, una lista «Secciones» para
  saltar a cada encabezado con Intro.

Teclas del visor: Ctrl+F (o Alt+B) busca, F3 (o Alt+I) busca el siguiente,
Escape (o Alt+C) cierra. En el modo HTML el motor web (Edge) se queda con las
teclas y la ventana nunca las ve: la página lleva un pequeño guion que las
recoge y se las pasa con ``postMessage`` (o, si no se puede, navegando a una
dirección ``ctrlia:`` que el visor intercepta). Quien abre el visor puede añadir
acciones propias con sus teclas (``acciones`` y ``teclas``): el visor de un
mensaje las usa para ir al cuadro de respuesta, citar o empezar otra sesión.

El visor no es modal: se puede consultar el manual mientras se usa la
aplicación. Si ya está abierto, se trae al frente en lugar de abrir otro.

Uso::

	from app.ui import visor_ayuda
	barra_menus.Append(visor_ayuda.crear_menu_ayuda(self), _("A&yuda"))
	# Tras mostrar la ventana principal:
	visor_ayuda.mostrar_novedades_si_procede(self, APP_VERSION)

PLANTILLA del agente «documentacion-usuario».
"""

from __future__ import annotations

import builtins
import html
import json
import logging
import re
from collections.abc import Callable
from urllib.parse import unquote

import wx

from app.core import documentacion

log = logging.getLogger(__name__)

if not hasattr(builtins, "_"):
	# Sin gettext instalado todavía (pruebas o proyecto sin traducir): español tal cual.
	builtins._ = lambda texto: texto

_ESTILO = """
body { font-family: "Segoe UI", sans-serif; font-size: 1.05rem; line-height: 1.5; margin: 1.5rem; max-width: 60rem; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { border: 1px solid #888; padding: 0.3rem 0.6rem; text-align: left; vertical-align: top; }
code, kbd { font-family: Consolas, monospace; }
:focus { outline: 3px solid #1a5fb4; }
"""
# Solo si la aplicación está en tema oscuro: así la página nunca va a oscuras
# dentro de una ventana clara (ni al revés) por el modo de Windows.
_ESTILO_OSCURO = """
html { color-scheme: dark; }
body { background: #1e1e1e; color: #f0f0f0; }
a { color: #8ab4f8; }
:focus { outline-color: #8ab4f8; }
"""


def _estilo() -> str:
	from app.ui.accesibilidad.visual import tema_oscuro_activo

	return _ESTILO + (_ESTILO_OSCURO if tema_oscuro_activo() else "html { color-scheme: light; }\n")

# Visores abiertos por título, para no abrir dos veces el mismo documento.
_abiertos: dict[str, VisorAyuda] = {}

#: Teclas que el visor atiende siempre, también dentro de la página: combinación → orden.
#: La combinación se escribe como la compone el guion de la página («Ctrl+», «Alt+», «Mayús+»
#: y la tecla en minúscula, o su nombre: Escape, F3...).
TECLAS_VISOR: dict[str, str] = {
	"Escape": "cerrar",
	"Alt+c": "cerrar",
	"Ctrl+f": "buscar",
	"Alt+b": "buscar",
	"F3": "siguiente",
	"Alt+i": "siguiente",
	"Ctrl+h": "ayuda",
}
#: Prefijo de las direcciones con las que la página avisa al visor si no puede usar ``postMessage``.
ESQUEMA_ORDEN = "ctrlia:"
#: Nombre del canal de mensajes de la página (``window.chrome.webview``).
CANAL_MENSAJES = "ctrlia"
#: Órdenes que usan el texto seleccionado. En el modo HTML, la selección del modo exploración de
#: NVDA o JAWS es virtual y no llega a la página (en Chrome y Edge no hay «selección nativa»;
#: NVDA solo la tiene en Firefox), pero su Ctrl+C sí copia lo seleccionado: si se copió algo
#: con el visor abierto, se usa eso.
ORDENES_CON_SELECCION = frozenset({"citar", "nueva"})


def secuencia_portapapeles() -> int:
	"""Número que Windows cambia cada vez que algo se copia (0 si no se puede saber)."""
	try:
		import ctypes

		return int(ctypes.windll.user32.GetClipboardSequenceNumber())
	except (AttributeError, OSError):
		return 0


def texto_portapapeles() -> str:
	"""Texto del portapapeles, o ``""``."""
	if not wx.TheClipboard.Open():
		return ""
	try:
		datos = wx.TextDataObject()
		return datos.GetText() if wx.TheClipboard.GetData(datos) else ""
	finally:
		wx.TheClipboard.Close()

_GUION_TECLAS = """
(function () {
  var teclas = %s;
  var porMensaje = %s;
  function nombre(e) {
    var tecla = e.key || "";
    if (tecla.length === 1) { tecla = tecla.toLowerCase(); }
    return (e.ctrlKey ? "Ctrl+" : "") + (e.altKey ? "Alt+" : "") + (e.shiftKey ? "Mayús+" : "") + tecla;
  }
  document.addEventListener("keydown", function (e) {
    var orden = teclas[nombre(e)];
    if (!orden) { return; }
    e.preventDefault();
    e.stopPropagation();
    var seleccion = window.getSelection ? String(window.getSelection()) : "";
    var datos = JSON.stringify({orden: orden, seleccion: seleccion});
    if (porMensaje) {
      try { window.chrome.webview.postMessage(datos); return; } catch (error) { }
    }
    window.location.href = "%s" + encodeURIComponent(datos);
  }, true);
})();
"""


# ── Conversión de Markdown ───────────────────────────────────────────────────

def html_disponible() -> bool:
	"""True si se puede usar el modo HTML (``markdown`` y Edge disponibles)."""
	try:
		import markdown  # noqa: F401
		import wx.html2
	except ImportError:
		return False
	try:
		return bool(wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendEdge))
	except Exception:
		log.debug("No se pudo consultar el motor Edge de wx.html2", exc_info=True)
		return False


def guion_teclas(teclas: dict[str, str], por_mensaje: bool = True) -> str:
	"""Guion de la página que pasa al visor las teclas de ``teclas`` (combinación → orden)."""
	return _GUION_TECLAS % (json.dumps(teclas, ensure_ascii=False), "true" if por_mensaje else "false", ESQUEMA_ORDEN)


def markdown_a_html(texto: str, titulo: str, teclas: dict[str, str] | None = None, por_mensaje: bool = True) -> str:
	"""Página HTML completa y bien estructurada (idioma, título, encabezados, tablas).

	:param teclas: si se da, la página recoge esas teclas y se las pasa al visor.
	"""
	import markdown

	cuerpo = markdown.markdown(texto, extensions=["extra", "sane_lists", "toc"], output_format="html")
	if "<h1" not in cuerpo:
		cuerpo = f"<h1>{html.escape(titulo)}</h1>\n{cuerpo}"
	guion = f"<script>{guion_teclas(teclas, por_mensaje)}</script>\n" if teclas else ""
	return (
		'<!DOCTYPE html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
		f"<title>{html.escape(titulo)}</title>\n<style>{_estilo()}</style>\n{guion}</head>\n"
		f"<body>\n<main>\n{cuerpo}\n</main>\n</body>\n</html>\n"
	)


def parrafo_en(texto: str, posicion: int) -> str:
	"""El párrafo (entre líneas en blanco) que contiene ``posicion``, sin espacios a los lados."""
	if not texto:
		return ""
	posicion = max(0, min(posicion, len(texto)))
	inicio = texto.rfind("\n\n", 0, posicion)
	inicio = 0 if inicio < 0 else inicio + 2
	fin = texto.find("\n\n", posicion)
	fin = len(texto) if fin < 0 else fin
	parrafo = texto[inicio:fin].strip()
	if not parrafo:
		# El cursor está en una línea en blanco: el párrafo siguiente.
		resto = texto[posicion:].strip()
		parrafo = resto.split("\n\n", 1)[0].strip() if resto else ""
	return parrafo


def _limpiar_linea(texto: str) -> str:
	texto = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", texto)  # imagen: su texto alternativo
	texto = re.sub(r"\[([^\]]+)\]\(#[^)]*\)", r"\1", texto)  # enlace interno: solo el texto
	texto = re.sub(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)", r"\1 (\2)", texto)  # enlace externo: texto y dirección
	texto = re.sub(r"<(https?://[^>]+)>", r"\1", texto)
	texto = re.sub(r"(\*\*|__)(.+?)\1", r"\2", texto)
	texto = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", texto)
	texto = re.sub(r"`([^`]+)`", r"\1", texto)
	texto = re.sub(r"</?[A-Za-z][^>]*>", "", texto)
	return html.unescape(texto)


def _fila(linea: str) -> list[str]:
	return [_limpiar_linea(c.strip()) for c in linea.strip().strip("|").split("|")]


def markdown_a_texto(texto: str) -> tuple[str, list[tuple[int, str, int]]]:
	"""Texto limpio para un campo de solo lectura y sus encabezados.

	:return: (texto, [(nivel, título, posición en el texto)]).
	"""
	salida: list[str] = []
	encabezados: list[tuple[int, str, int]] = []
	lineas = texto.replace("\r\n", "\n").split("\n")
	en_codigo = en_comentario = False
	indice = 0

	def posicion_actual() -> int:
		return sum(len(linea) + 1 for linea in salida)

	def separar() -> None:
		if salida and salida[-1] != "":
			salida.append("")

	while indice < len(lineas):
		linea = lineas[indice]
		limpia = linea.strip()
		indice += 1
		if en_comentario:
			en_comentario = "-->" not in limpia
			continue
		if limpia.startswith("<!--"):
			en_comentario = "-->" not in limpia
			continue
		if limpia.startswith(("```", "~~~")):
			en_codigo = not en_codigo
			continue
		if en_codigo:
			salida.append(linea)
			continue
		if limpia in ("[TOC]", "[toc]") or re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", limpia):
			separar()
			continue
		encabezado = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", limpia)
		if encabezado:
			separar()
			titulo = _limpiar_linea(encabezado.group(2))
			encabezados.append((len(encabezado.group(1)), titulo, posicion_actual()))
			salida.append(titulo)
			salida.append("")
			continue
		if limpia.startswith("|"):
			filas = [limpia]
			while indice < len(lineas) and lineas[indice].strip().startswith("|"):
				filas.append(lineas[indice].strip())
				indice += 1
			cabecera = _fila(filas[0])
			datos = [f for f in filas[1:] if not re.fullmatch(r"[\s|:\-]+", f)]
			for fila in datos:
				celdas = _fila(fila)
				if len(cabecera) == 2 and len(celdas) >= 2:
					salida.append(f"{celdas[0]}: {celdas[1]}")
				else:
					salida.append("; ".join(f"{c}: {v}" for c, v in zip(cabecera, celdas, strict=False) if v))
			salida.append("")
			continue
		elemento = re.match(r"^(\s*)[-*+]\s+(.*)$", linea)
		if elemento:
			nivel = len(elemento.group(1).replace("\t", "  ")) // 2
			salida.append("  " * nivel + "• " + _limpiar_linea(elemento.group(2)))
			continue
		cita = re.match(r"^\s*>\s?(.*)$", linea)
		if cita:
			salida.append(_limpiar_linea(cita.group(1)))
			continue
		if not limpia:
			separar()
			continue
		salida.append(_limpiar_linea(linea.rstrip()))
	while salida and salida[-1] == "":
		salida.pop()
	return "\n".join(salida) + "\n", encabezados


# ── Visor ────────────────────────────────────────────────────────────────────

def avisar(padre: wx.Window | None, mensaje: str) -> None:
	"""Aviso modal. Separado en una función para poder sustituirlo en las pruebas."""
	from app.ui.accesibilidad.dialogos import mostrar_mensaje

	mostrar_mensaje(padre, mensaje, _("Ayuda"))


class VisorAyuda(wx.Dialog):
	"""Diálogo no modal que muestra un documento Markdown de forma accesible."""

	def __init__(self, padre: wx.Window | None, titulo: str, markdown: str, forzar_texto: bool = False,
			acciones: dict[str, Callable[[str], None]] | None = None, teclas: dict[str, str] | None = None,
			ayuda: str = ""):
		"""
		:param acciones: órdenes propias → función que recibe el texto seleccionado (o ``""``).
		:param teclas: combinación → orden de ``acciones`` (se suman a ``TECLAS_VISOR``).
		:param ayuda: lo que dice Ctrl+H en el contenido.
		"""
		super().__init__(
			padre, title=titulo, size=(820, 620), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX
		)
		self.titulo = titulo
		self.markdown = markdown
		self.acciones = dict(acciones or {})
		self.teclas = {**TECLAS_VISOR, **{k: v for k, v in (teclas or {}).items() if v in self.acciones}}
		self.ayuda = ayuda or _(
			"Contenido del documento. Escape o Alt+C cierran el visor; Control+F busca y F3 busca el siguiente."
		)
		self.modo = "html" if not forzar_texto and html_disponible() else "texto"
		self.ultima_busqueda = ""
		self._cerrado = False
		#: Lo copiado antes de abrir el visor (o ya usado) no cuenta como selección.
		self._portapapeles_visto = secuencia_portapapeles()
		self.web = None
		self.secciones: wx.ListBox | None = None
		self.contenido: wx.TextCtrl | None = None
		self._posiciones: list[int] = []
		raiz = wx.BoxSizer(wx.VERTICAL)
		if self.modo == "html":
			try:
				self._crear_html(raiz)
			except Exception:
				log.exception("No se pudo crear la vista HTML; se usa el modo texto")
				self.modo = "texto"
		if self.modo == "texto":
			self._crear_texto(raiz)
		raiz.Add(self._crear_botones(), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizer(raiz)
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		self.Bind(wx.EVT_CLOSE, self._al_cerrar)
		self.CentreOnParent()
		wx.CallAfter(self.enfocar_contenido)

	# Construcción

	def _crear_html(self, raiz: wx.BoxSizer) -> None:
		import wx.html2

		self.web = wx.html2.WebView.New(self, backend=wx.html2.WebViewBackendEdge)
		self.web.SetName(self.titulo)
		self.web.SetHelpText(self.ayuda)
		self.web.Bind(wx.html2.EVT_WEBVIEW_NAVIGATING, self._al_navegar)
		por_mensaje = False
		try:
			por_mensaje = bool(self.web.AddScriptMessageHandler(CANAL_MENSAJES))
		except Exception:  # noqa: BLE001 - sin canal de mensajes, la página avisa navegando a ctrlia:
			log.debug("No se pudo crear el canal de mensajes del visor", exc_info=True)
		if por_mensaje:
			self.web.Bind(wx.html2.EVT_WEBVIEW_SCRIPT_MESSAGE_RECEIVED, lambda evento: self._orden_de_pagina(evento.GetString()))
		self.web.SetPage(markdown_a_html(self.markdown, self.titulo, teclas=self.teclas, por_mensaje=por_mensaje), "")
		raiz.Add(self.web, 1, wx.EXPAND | wx.ALL, 6)

	def _crear_texto(self, raiz: wx.BoxSizer) -> None:
		texto, encabezados = markdown_a_texto(self.markdown)
		visibles = [(t, p) for nivel, t, p in encabezados if nivel <= 3]
		if visibles:
			raiz.Add(wx.StaticText(self, label=_("&Secciones:")), 0, wx.LEFT | wx.TOP, 6)
			self.secciones = wx.ListBox(self, choices=[t for t, _p in visibles], style=wx.LB_SINGLE)
			self.secciones.SetSelection(0)
			self.secciones.Bind(wx.EVT_LISTBOX_DCLICK, lambda evento: self.ir_a_seccion(self.secciones.GetSelection()))
			self._posiciones = [p for _t, p in visibles]
			from app.ui.accesibilidad.teclado import copiar_texto, menu_de_acciones, vincular_menu_contextual

			vincular_menu_contextual(self.secciones, lambda: menu_de_acciones(self.secciones, [
				(_("&Ir a la sección\tIntro"), lambda: self.ir_a_seccion(self.secciones.GetSelection())),
				(_("&Copiar el título"), lambda: copiar_texto(self.secciones.GetStringSelection())),
			]) if self.secciones.GetSelection() != wx.NOT_FOUND else None)
			raiz.Add(self.secciones, 0, wx.EXPAND | wx.ALL, 6)
		raiz.Add(wx.StaticText(self, label=_("C&ontenido:")), 0, wx.LEFT | wx.TOP, 6)
		# TE_RICH2: sin el límite de 64 KB del campo clásico y con posiciones
		# que coinciden con el texto (un salto de línea = una posición).
		self.contenido = wx.TextCtrl(
			self, value=texto, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_NOHIDESEL
		)
		self.contenido.SetHelpText(self.ayuda)
		self.contenido.SetInsertionPoint(0)
		raiz.Add(self.contenido, 1, wx.EXPAND | wx.ALL, 6)

	def _crear_botones(self) -> wx.BoxSizer:
		fila = wx.BoxSizer(wx.HORIZONTAL)
		buscar = wx.Button(self, label=_("&Buscar... (Ctrl+F)"))
		siguiente = wx.Button(self, label=_("Buscar s&iguiente (F3)"))
		cerrar = wx.Button(self, wx.ID_CLOSE, _("&Cerrar"))
		buscar.Bind(wx.EVT_BUTTON, lambda evento: self.pedir_busqueda())
		siguiente.Bind(wx.EVT_BUTTON, lambda evento: self.buscar_siguiente())
		cerrar.Bind(wx.EVT_BUTTON, lambda evento: self.Close())
		fila.Add(buscar, 0, wx.RIGHT, 6)
		fila.Add(siguiente, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		fila.Add(cerrar, 0)
		return fila

	# Acciones

	def enfocar_contenido(self) -> None:
		# Oculto (las pruebas lo crean sin mostrarlo) no se enfoca: Windows protesta y no sirve de nada.
		if not self or not self.IsShown():
			return
		destino = self.web if self.web is not None else self.contenido
		if destino is not None:
			destino.SetFocus()

	def ir_a_seccion(self, indice: int) -> None:
		"""Lleva el cursor del contenido al encabezado elegido (el lector lo lee)."""
		if self.contenido is None or not 0 <= indice < len(self._posiciones):
			return
		posicion = self._posiciones[indice]
		self.contenido.SetInsertionPoint(posicion)
		self.contenido.ShowPosition(posicion)
		self.contenido.SetFocus()

	def buscar(self, texto: str) -> bool:
		"""Busca a partir de la posición actual y da la vuelta al llegar al final."""
		if not texto:
			return False
		self.ultima_busqueda = texto
		if self.web is not None:
			import wx.html2

			encontrado = self.web.Find(texto, wx.html2.WEBVIEW_FIND_WRAP | wx.html2.WEBVIEW_FIND_HIGHLIGHT_RESULT)
			if encontrado == wx.NOT_FOUND:
				avisar(self, _("No se encontró «{texto}».").format(texto=texto))
				return False
			return True
		if self.contenido is None:
			return False
		valor = self.contenido.GetValue().lower()
		buscado = texto.lower()
		inicio, fin = self.contenido.GetSelection()
		# Tras un resultado, se sigue desde el final de la selección; si no, desde el cursor.
		desde = fin if fin > inicio else self.contenido.GetInsertionPoint()
		posicion = valor.find(buscado, desde)
		if posicion < 0:
			posicion = valor.find(buscado)
		if posicion < 0:
			avisar(self, _("No se encontró «{texto}».").format(texto=texto))
			return False
		self.contenido.SetSelection(posicion, posicion + len(buscado))
		self.contenido.ShowPosition(posicion)
		self.contenido.SetFocus()
		return True

	def pedir_busqueda(self) -> None:
		from app.ui.accesibilidad.dialogos import pedir_texto

		texto = pedir_texto(self, _("&Texto que se busca:"), _("Buscar en «{titulo}»").format(titulo=self.titulo), self.ultima_busqueda)
		if texto is not None and texto.strip():
			self.buscar(texto.strip())

	def buscar_siguiente(self) -> None:
		if self.ultima_busqueda:
			self.buscar(self.ultima_busqueda)
		else:
			self.pedir_busqueda()

	def seleccion(self) -> str:
		"""Texto seleccionado en el modo texto (en el modo HTML lo envía la página con cada orden)."""
		if self.contenido is None:
			return ""
		return self.contenido.GetStringSelection().strip()

	def texto_copiado(self) -> str:
		"""Lo copiado al portapapeles desde que se abrió el visor (o desde la última vez que se usó), una vez."""
		secuencia = secuencia_portapapeles()
		if not secuencia or secuencia == self._portapapeles_visto:
			return ""
		self._portapapeles_visto = secuencia
		return texto_portapapeles().strip()

	def parrafo_actual(self) -> str:
		"""Párrafo donde está el cursor (modo texto); ``""`` en el modo HTML."""
		if self.contenido is None:
			return ""
		return parrafo_en(self.contenido.GetValue(), self.contenido.GetInsertionPoint())

	def ejecutar_orden(self, orden: str, seleccion: str = "") -> bool:
		"""Ejecuta una orden del visor o de ``acciones``; ``True`` si existía."""
		if not self or self._cerrado:
			return False
		if orden == "cerrar":
			self.Close()
		elif orden == "buscar":
			self.pedir_busqueda()
		elif orden == "siguiente":
			self.buscar_siguiente()
		elif orden == "ayuda":
			from app.core import voz

			voz.hablar(self.ayuda, interrumpir=True)
		elif orden in self.acciones:
			if not seleccion.strip() and self.modo == "html" and orden in ORDENES_CON_SELECCION:
				seleccion = self.texto_copiado()
			try:
				self.acciones[orden](seleccion.strip())
			except Exception:  # noqa: BLE001 - una acción que falla no debe cerrar el visor
				log.exception("Falló la acción %s del visor", orden)
		else:
			return False
		return True

	def _orden_de_pagina(self, texto: str) -> None:
		"""La página ha recogido una tecla: ``{"orden": ..., "seleccion": ...}`` en JSON."""
		try:
			datos = json.loads(texto or "{}")
		except ValueError:
			log.debug("Mensaje del visor no válido: %r", (texto or "")[:200])
			return
		if not isinstance(datos, dict) or not isinstance(datos.get("orden"), str):
			return
		seleccion = datos.get("seleccion") if isinstance(datos.get("seleccion"), str) else ""
		# Después del evento: cerrar el visor dentro del evento del motor web lo destruiría en uso.
		wx.CallAfter(self.ejecutar_orden, datos["orden"], seleccion)

	# Eventos

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		# Teclas atendidas a mano (tools/comprobar_teclas.py las lee de aquí):
		# atajo: Escape cerrar
		# atajo: Alt+C cerrar
		# atajo: Ctrl+F buscar
		# atajo: Alt+B buscar
		# atajo: F3 buscar siguiente
		# atajo: Alt+I buscar siguiente
		codigo, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		orden = self.teclas.get(_combinacion(codigo, modificadores), "")
		if orden == "ayuda":
			from app.ui.accesibilidad.ayuda import describir_control_enfocado

			describir_control_enfocado(self)
		elif orden in ("cerrar", "buscar", "siguiente"):
			self.ejecutar_orden(orden)
		elif orden in self.acciones:
			seleccion = self.seleccion()
			if not seleccion and orden == "citar":
				seleccion = self.parrafo_actual()
			self.ejecutar_orden(orden, seleccion)
		elif (
			codigo in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
			and self.secciones is not None
			and wx.Window.FindFocus() is self.secciones
		):
			self.ir_a_seccion(self.secciones.GetSelection())
		else:
			evento.Skip()

	def _al_navegar(self, evento) -> None:
		"""Los enlaces externos se abren en el navegador; la página propia, aquí; ``ctrlia:``, una tecla."""
		direccion = evento.GetURL() or ""
		if direccion.startswith(ESQUEMA_ORDEN):
			evento.Veto()
			self._orden_de_pagina(unquote(direccion[len(ESQUEMA_ORDEN):]))
			return
		if direccion.startswith(("http://", "https://", "mailto:")):
			evento.Veto()
			wx.LaunchDefaultBrowser(direccion)
		else:
			evento.Skip()

	def _al_cerrar(self, evento: wx.CloseEvent) -> None:
		if self._cerrado:
			# Escape puede llegar dos veces (de la página y de la ventana): se cierra una.
			return
		self._cerrado = True
		_abiertos.pop(self.titulo, None)
		if self.IsModal():
			self.EndModal(wx.ID_CLOSE)
		else:
			self.Destroy()


def _combinacion(codigo: int, modificadores: int) -> str:
	"""Combinación de wx escrita como la compone el guion de la página (``Ctrl+Alt+n``, ``Escape``...)."""
	especiales = {wx.WXK_ESCAPE: "Escape", wx.WXK_F3: "F3", wx.WXK_RETURN: "Enter"}
	if codigo in especiales:
		tecla = especiales[codigo]
	elif 32 < codigo < 127:
		tecla = chr(codigo).lower()
	else:
		return ""
	return (("Ctrl+" if modificadores & wx.MOD_CONTROL else "") + ("Alt+" if modificadores & wx.MOD_ALT else "")
		+ ("Mayús+" if modificadores & wx.MOD_SHIFT else "") + tecla)


# ── Funciones de alto nivel ──────────────────────────────────────────────────

def mostrar_documento(
	padre: wx.Window | None, titulo: str, markdown: str, forzar_texto: bool = False, mostrar: bool = True,
	acciones: dict[str, Callable[[str], None]] | None = None, teclas: dict[str, str] | None = None, ayuda: str = "",
) -> VisorAyuda:
	"""Abre (o trae al frente) un visor con ese título."""
	existente = _abiertos.get(titulo)
	if existente:
		existente.Raise()
		existente.enfocar_contenido()
		return existente
	visor = VisorAyuda(padre, titulo, markdown, forzar_texto=forzar_texto, acciones=acciones, teclas=teclas, ayuda=ayuda)
	_abiertos[titulo] = visor
	if mostrar:
		visor.Show()
	return visor


def abrir_documento(
	padre: wx.Window | None, nombre: str, titulo_por_defecto: str, forzar_texto: bool = False, mostrar: bool = True
) -> VisorAyuda | None:
	"""Abre ``data/docs/<nombre>`` (o ``docs/<nombre>``); avisa si no existe."""
	texto = documentacion.leer_documento(nombre)
	if texto is None:
		avisar(padre, _("No se encontró el documento «{nombre}». Reinstale la aplicación o consulte la página del proyecto.").format(nombre=nombre))
		return None
	titulo = documentacion.titulo_de(texto, titulo_por_defecto)
	return mostrar_documento(padre, titulo, texto, forzar_texto=forzar_texto, mostrar=mostrar)


def abrir_manual(padre: wx.Window | None, **opciones) -> VisorAyuda | None:
	return abrir_documento(padre, documentacion.MANUAL, _("Manual de uso"), **opciones)


def abrir_teclas(padre: wx.Window | None, **opciones) -> VisorAyuda | None:
	return abrir_documento(padre, documentacion.TECLAS, _("Teclas de la aplicación"), **opciones)


def abrir_novedades(padre: wx.Window | None, **opciones) -> VisorAyuda | None:
	return abrir_documento(padre, documentacion.CHANGELOG, _("Novedades"), **opciones)


def mostrar_novedades_si_procede(
	padre: wx.Window | None,
	version_actual: str,
	texto_changelog: str | None = None,
	forzar_texto: bool = False,
	mostrar: bool = True,
) -> VisorAyuda | None:
	"""Muestra, UNA sola vez por versión, las novedades desde la última versión vista.

	Llamar tras mostrar la ventana principal (con ``wx.CallAfter``), para que
	el visor quede encima y el lector de pantalla lo anuncie.
	"""
	pendientes = documentacion.novedades_pendientes(version_actual, texto_changelog)
	if pendientes is None:
		return None
	_desde, versiones = pendientes
	titulo = _("Novedades de la versión {version}").format(version=version_actual)
	visor = mostrar_documento(
		padre, titulo, documentacion.componer_novedades(versiones, titulo), forzar_texto=forzar_texto, mostrar=mostrar
	)
	# Se marca al mostrarlas: si la aplicación se cierra después, no se repiten.
	documentacion.marcar_vista(version_actual)
	return visor


def crear_menu_ayuda(ventana: wx.Window) -> wx.Menu:
	"""Menú «Ayuda» con el manual (F1), la lista de teclas y las novedades."""
	menu = wx.Menu()
	manual = menu.Append(wx.ID_HELP, _("&Manual de uso\tF1"))
	teclas = menu.Append(wx.ID_ANY, _("&Teclas de la aplicación"))
	novedades = menu.Append(wx.ID_ANY, _("&Novedades de cada versión"))
	ventana.Bind(wx.EVT_MENU, lambda evento: abrir_manual(ventana), manual)
	ventana.Bind(wx.EVT_MENU, lambda evento: abrir_teclas(ventana), teclas)
	ventana.Bind(wx.EVT_MENU, lambda evento: abrir_novedades(ventana), novedades)
	return menu
