"""Ayuda, «Buscar una acción...»: encontrar cualquier orden del programa por su nombre y ejecutarla.

Es la «paleta de órdenes» hecha con foco REAL, para que el lector de pantalla
sepa siempre dónde está (una paleta que deja el foco en el cuadro y mueve con
las flechas una lista sin foco que lee con su propia voz no da «3 de 40» ni
braille, y choca con el eco del cuadro):

- Un cuadro «Buscar» y debajo una lista normal. Flecha abajo en el cuadro lleva
  el foco a la lista; escribir una letra en la lista vuelve al cuadro con esa
  letra; Retroceso en la lista vuelve al cuadro y borra la última; Flecha arriba
  en la primera fila vuelve al cuadro.
- Cada fila dice el nombre de la acción, su tecla (la tuya, si la cambiaste) y
  su menú. Las que no se pueden usar ahora se marcan «(no disponible ahora)»:
  así se aprende dónde están, y Intro explica que no se puede en lugar de no
  hacer nada.
- Se busca por palabras en cualquier orden, sin tildes ni mayúsculas, también
  por la tecla («ctrl+/») y por la ayuda. Las usadas recientemente salen primero.
- Intro (en la lista o en el cuadro) cierra con ``wx.ID_OK`` y deja la acción
  en ``resultado``: la ventana la ejecuta después de cerrar el cuadro.

El número de resultados se ve en la etiqueta de la lista y se dice una vez,
cuando dejas de escribir, nunca a cada letra.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import wx

from app.core.teclas import Accion, buscar_acciones, para_leer
from app.ui import avisos
from app.ui.accesibilidad.dialogos import preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

COLUMNAS = [(_("Acción"), 360), (_("Tecla"), 170), (_("Menú"), 230)]
#: Espera tras la última letra antes de decir cuántas acciones hay (milisegundos).
ESPERA_RECUENTO_MS = 900


def texto_fila(accion: Accion) -> list[str]:
	"""Las columnas de una fila: nombre (con su estado), tecla y menú."""
	nombre = accion.nombre
	if accion.marcada is not None:
		nombre += " " + (_("(marcada)") if accion.marcada else _("(sin marcar)"))
	if not accion.disponible:
		nombre += " " + _("(no disponible ahora)")
	return [nombre, para_leer(accion.tecla) or _("Sin tecla"), accion.menu]


class DialogoBuscarAccion(wx.Dialog):
	"""Busca una acción por su nombre; Intro la elige (``resultado``) y cierra."""

	def __init__(self, padre: wx.Window | None, acciones: Sequence[Accion], recientes: Sequence[str] = ()) -> None:
		titulo = _("Buscar una acción")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._acciones = list(acciones)
		self._recientes = list(recientes)
		self._mostradas: list[Accion] = []
		self._recuento: wx.CallLater | None = None
		#: La acción elegida con Intro (``None`` si se cierra sin elegir).
		self.resultado: Accion | None = None

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(panel, label=_("&Buscar:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.cuadro = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER, name=_("Buscar"))
		self.cuadro.SetHelpText(_(
			"Escribe palabras del nombre de la acción, en cualquier orden y sin importar las tildes; también vale su tecla "
			"(por ejemplo «ctrl+/») o algo de su explicación. Flecha abajo pasa a la lista de resultados; Intro ejecuta la "
			"primera, o la elegida en la lista."
		))
		sizer.Add(self.cuadro, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
		self.etiqueta = wx.StaticText(panel, label=_("&Acciones:"))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.lista = ListaAccesible(panel, _("Acciones"), COLUMNAS)
		self.lista.SetHelpText(_(
			"Las acciones que coinciden con lo buscado, con su tecla y su menú; las usadas hace poco, primero. Intro "
			"cierra este cuadro y ejecuta la elegida. Escribir una letra vuelve al cuadro de búsqueda con ella; Retroceso "
			"vuelve y borra la última."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(780, 320)))
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra sin ejecutar nada."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
		panel.SetSizer(sizer)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)

		self.cuadro.Bind(wx.EVT_TEXT, self._al_escribir)
		self.cuadro.Bind(wx.EVT_TEXT_ENTER, lambda _e: self.elegir())
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.elegir())
		self.lista.Bind(wx.EVT_CHAR, self._al_escribir_en_lista)
		vincular_menu_contextual(self.lista, self._crear_menu)
		preparar_dialogo(self, foco_inicial=self.cuadro)
		# Después de preparar_dialogo, para ir antes que él (Escape y Ctrl+H siguen allí).
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)
		self.Bind(wx.EVT_WINDOW_DESTROY, self._al_destruir)
		self._rellenar()
		self.Layout()
		self.Fit()
		self.CentreOnParent()

	# ── Resultados ─────────────────────────────────────────────────────────────

	def _rellenar(self) -> None:
		self._mostradas = buscar_acciones(self._acciones, self.cuadro.GetValue(), self._recientes)
		self.lista.reemplazar_filas([texto_fila(a) for a in self._mostradas], conservar_foco=False)
		total = len(self._mostradas)
		if total:
			self.etiqueta.SetLabel(_("&Acciones ({n}):").format(n=total))
			self.lista.SetName(_("Acciones ({n})").format(n=total))
		else:
			self.etiqueta.SetLabel(_("&Acciones: ninguna coincide."))
			self.lista.SetName(_("Acciones: ninguna coincide"))

	def texto_recuento(self) -> str:
		total = len(self._mostradas)
		if not total:
			return _("Ninguna acción coincide.")
		return _("Una acción.") if total == 1 else _("{n} acciones.").format(n=total)

	def _al_escribir(self, _evento: wx.CommandEvent) -> None:
		self._rellenar()
		# El recuento se dice una vez al dejar de escribir: a cada letra chocaría con el eco del cuadro.
		if self._recuento is not None:
			self._recuento.Stop()
		self._recuento = wx.CallLater(ESPERA_RECUENTO_MS, self._decir_recuento)

	def _decir_recuento(self) -> None:
		self._recuento = None
		if self and wx.Window.FindFocus() is self.cuadro and self.cuadro.GetValue().strip():
			avisos.anunciar(self.texto_recuento())

	def _al_destruir(self, evento: wx.WindowDestroyEvent) -> None:
		if evento.GetEventObject() is self and self._recuento is not None:
			self._recuento.Stop()
			self._recuento = None
		evento.Skip()

	def accion_actual(self) -> Accion | None:
		fila = self.lista.fila_actual()
		return self._mostradas[fila] if 0 <= fila < len(self._mostradas) else None

	# ── Teclado y foco ─────────────────────────────────────────────────────────

	def ir_a_lista(self) -> bool:
		if not self._mostradas:
			avisos.anunciar(_("Ninguna acción coincide."), interrumpir=True)
			return False
		self.lista.SetFocus()
		fila = self.lista.fila_actual()
		self.lista.enfocar_fila(fila if fila >= 0 else 0, anunciar=False)
		return True

	def ir_al_cuadro(self, anadir: str = "", borrar: bool = False) -> None:
		texto = self.cuadro.GetValue()
		if borrar:
			texto = texto[:-1]
		texto += anadir
		self.cuadro.SetFocus()
		if texto != self.cuadro.GetValue():
			self.cuadro.SetValue(texto)  # SetValue: rellena de nuevo la lista
		self.cuadro.SetInsertionPointEnd()

	def _al_pulsar(self, evento: wx.KeyEvent) -> None:
		# atajo: Abajo ir a los resultados desde el cuadro de búsqueda
		foco = wx.Window.FindFocus()
		codigo, mods = evento.GetKeyCode(), evento.GetModifiers()
		if foco is self.cuadro and mods == wx.MOD_NONE and codigo in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
			self.ir_a_lista()
			return
		if foco is self.lista and mods == wx.MOD_NONE:
			if codigo in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
				self.elegir()
				return
			if codigo in (wx.WXK_UP, wx.WXK_NUMPAD_UP) and self.lista.fila_actual() <= 0:
				self.ir_al_cuadro()
				return
			if codigo == wx.WXK_BACK:
				self.ir_al_cuadro(borrar=True)
				return
		evento.Skip()

	def _al_escribir_en_lista(self, evento: wx.KeyEvent) -> None:
		"""Una letra escrita en la lista vuelve al cuadro con ella (sin buscar dentro de la lista)."""
		caracter = evento.GetUnicodeKey()
		if (caracter not in (wx.WXK_NONE, wx.WXK_SPACE) and caracter >= 32 and not evento.ControlDown()
				and not evento.AltDown()):
			self.ir_al_cuadro(chr(caracter))
			return
		evento.Skip()

	# ── Elegir ─────────────────────────────────────────────────────────────────

	def elegir(self) -> bool:
		"""Intro: deja la acción en ``resultado`` y cierra con ``wx.ID_OK`` (desde el cuadro, la elegida o la primera)."""
		accion = self.accion_actual()
		if accion is None and self._mostradas:
			accion = self._mostradas[0]
		if accion is None:
			avisos.anunciar(_("Ninguna acción coincide."), interrumpir=True)
			return False
		if not accion.disponible:
			avisos.anunciar(_("«{accion}» no está disponible ahora.").format(accion=accion.nombre), interrumpir=True)
			return False
		self.resultado = accion
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True

	def _crear_menu(self) -> wx.Menu | None:
		if self.accion_actual() is None:
			return None
		return menu_de_acciones(self.lista, [
			(_("&Ejecutar\tIntro"), self.elegir),
			(_("&Volver al cuadro de búsqueda"), self.ir_al_cuadro),
		])
