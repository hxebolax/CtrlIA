"""Diálogo «Mensajes favoritos»: todos los mensajes marcados de todas las sesiones.

Una lista (Mensaje, Nota, Sesión, Quién, Fecha), del marcado más recientemente
al más antiguo. Intro deja el favorito en ``resultado`` y cierra con
``wx.ID_OK`` (la ventana principal abre su sesión y lleva el foco a él);
Supr lo quita de favoritos tras confirmarlo (Intro en la confirmación
responde «No»); Ctrl+C copia su texto completo; F2 escribe, cambia o quita
(vacía) su nota. Todo está también en el menú contextual.

El diálogo no toca la base de datos: lo hacen las funciones que recibe
(``existe``, ``quitar``, ``copiar`` y ``anotar``), así se prueba sin ventana principal.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import wx

from app.services.almacen import MensajeFavorito
from app.services.favoritos import LARGO_NOTA, limpiar_nota
from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, pedir_texto, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import copiar_texto, menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Columnas de la lista: (cabecera, ancho).
COLUMNAS = [(_("Mensaje"), 360), (_("Nota"), 200), (_("Sesión"), 200), (_("Quién"), 110), (_("Fecha"), 150)]
#: Posición de cada columna en los valores de una fila.
COLUMNA_NOTA = 1
COLUMNA_SESION = 2


class DialogoFavoritos(wx.Dialog):
	"""Lista de todos los mensajes favoritos; Intro lleva al elegido."""

	def __init__(
		self,
		padre: wx.Window | None,
		filas: list[tuple[MensajeFavorito, list[str]]],
		existe: Callable[[MensajeFavorito], bool],
		quitar: Callable[[MensajeFavorito], bool],
		copiar: Callable[[MensajeFavorito], None],
		anotar: Callable[[MensajeFavorito, str], bool] | None = None,
	) -> None:
		titulo = _("Mensajes favoritos")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._favoritos = [favorito for favorito, _valores in filas]
		self._valores = [list(valores) for _favorito, valores in filas]
		self._existe = existe
		self._quitar = quitar
		self._copiar = copiar
		self._anotar = anotar
		#: El favorito elegido con Intro (``None`` si se cierra sin elegir).
		self.resultado: MensajeFavorito | None = None

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.etiqueta = wx.StaticText(panel, label=_("&Mensajes favoritos:"))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.lista = ListaAccesible(panel, _("Mensajes favoritos"), COLUMNAS)
		self.lista.SetHelpText(_(
			"Los mensajes que has marcado como favoritos en todas las sesiones, de cualquier agente, del marcado más "
			"recientemente al más antiguo. Intro cierra este cuadro y lleva a ese mensaje en su sesión; Suprimir lo "
			"quita de favoritos; Control+C copia su texto completo; F2 le escribe una nota corta (vacía, la quita). "
			"En la lista de mensajes, Control+Alt+F marca o quita un favorito."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(900, 320)))
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra la lista sin ir a ningún mensaje."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
		panel.SetSizer(sizer)

		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _evento: self.abrir())
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		vincular_menu_contextual(self.lista, self._crear_menu)
		preparar_dialogo(self, foco_inicial=self.lista)

		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self._rellenar()
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		if not self._favoritos:
			wx.CallAfter(avisos.anunciar, _(
				"No tienes mensajes favoritos. Márcalos con Control+Alt+F en la lista de mensajes de cualquier sesión."
			))

	# ── Contenido ─────────────────────────────────────────────────────────────

	def _rellenar(self, fila: int = 0) -> None:
		self.lista.reemplazar_filas(self._valores, conservar_foco=False)
		total = len(self._favoritos)
		if total:
			self.etiqueta.SetLabel(_("&Mensajes favoritos ({n}):").format(n=total))
			self.lista.SetName(_("Mensajes favoritos ({n})").format(n=total))
			self.lista.enfocar_fila(min(max(fila, 0), total - 1), anunciar=False)
		else:
			self.etiqueta.SetLabel(_("&Mensajes favoritos: ninguno."))
			self.lista.SetName(_("Mensajes favoritos: ninguno"))

	def favorito_actual(self) -> MensajeFavorito | None:
		fila = self.lista.fila_actual()
		return self._favoritos[fila] if 0 <= fila < len(self._favoritos) else None

	# ── Acciones ──────────────────────────────────────────────────────────────

	def _elegido(self) -> MensajeFavorito | None:
		favorito = self.favorito_actual()
		if favorito is None:
			avisos.anunciar(_("No hay ningún mensaje favorito elegido."), interrumpir=True)
		return favorito

	def abrir(self) -> bool:
		"""Intro: cierra con ``wx.ID_OK`` y el favorito en ``resultado`` (si su sesión sigue existiendo)."""
		favorito = self._elegido()
		if favorito is None:
			return False
		if not self._existe(favorito):
			avisos.anunciar(_(
				"La sesión de este mensaje ya no está en Ctrl+IA: se borró o es de un programa cuyas sesiones ya no se "
				"leen. Puedes quitarlo de favoritos con Suprimir."
			), interrumpir=True)
			return False
		self.resultado = favorito
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True

	def quitar(self) -> bool:
		"""Supr: quita el favorito elegido, tras confirmarlo."""
		favorito = self._elegido()
		if favorito is None:
			return False
		pregunta = _("¿Quitar este mensaje de favoritos? El mensaje no se borra.") + "\n\n" + favorito.extracto
		if confirmar(self, pregunta, _("Quitar de favoritos"), peligroso=True) != wx.ID_YES:
			return False
		self._quitar(favorito)
		fila = self._favoritos.index(favorito)
		del self._favoritos[fila]
		del self._valores[fila]
		self._rellenar(fila)
		quedan = len(self._favoritos)
		avisos.anunciar(_("Quitado de favoritos. Quedan {n}.").format(n=quedan) if quedan
			else _("Quitado de favoritos. Ya no queda ninguno."), interrumpir=True)
		return True

	def copiar(self) -> None:
		"""Ctrl+C: copia el texto completo del favorito elegido."""
		favorito = self._elegido()
		if favorito is not None:
			self._copiar(favorito)

	def anotar(self) -> bool:
		"""F2: escribe, cambia o (vacía) quita la nota del favorito elegido."""
		favorito = self._elegido()
		if favorito is None or self._anotar is None:
			return False
		texto = pedir_texto(self, _("&Nota del favorito (vacía para quitarla):"), _("Nota del favorito"), favorito.nota,
			max_caracteres=LARGO_NOTA, ayuda=_(
				"Un recordatorio corto de por qué marcaste este mensaje, de una línea y como mucho {n} caracteres. "
				"Déjala vacía y acepta para quitarla.").format(n=LARGO_NOTA))
		if texto is None:
			return False
		nota = limpiar_nota(texto)
		if nota == favorito.nota:
			return False
		self._anotar(favorito, nota)
		favorito.nota = nota
		fila = self._favoritos.index(favorito)
		self._valores[fila][COLUMNA_NOTA] = nota
		self.lista.actualizar_fila(fila, self._valores[fila])
		avisos.anunciar(_("Nota guardada.") if nota else _("Nota quitada."), interrumpir=True)
		return True

	def _copiar_titulo(self) -> None:
		fila = self.lista.fila_actual()
		if 0 <= fila < len(self._valores) and copiar_texto(self._valores[fila][COLUMNA_SESION]):
			avisos.anunciar(_("Copiado."), interrumpir=True)

	def _crear_menu(self) -> wx.Menu | None:
		if self.favorito_actual() is None:
			return None
		acciones: list[tuple[str, Callable[[], object]] | None] = [
			(_("&Ir al mensaje\tIntro"), self.abrir),
			(_("&Copiar el texto\tCtrl+C"), self.copiar),
			(_("Copiar el &título de la sesión"), self._copiar_titulo),
		]
		if self._anotar is not None:
			acciones.append((_("Escribir una &nota...\tF2"), self.anotar))
		acciones += [None, (_("&Quitar de favoritos...\tSupr"), self.quitar)]
		return menu_de_acciones(self.lista, acciones)

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if mods == wx.MOD_NONE and tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.quitar()
			return
		if mods == wx.MOD_NONE and tecla == wx.WXK_F2:
			self.anotar()
			return
		if mods == wx.MOD_CONTROL and tecla == ord("C"):
			self.copiar()
			return
		evento.Skip()
