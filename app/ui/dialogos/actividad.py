"""Ventana «Actividad en segundo plano» (Ctrl+Alt+A): lo que corre junto a la conversación.

Cada subagente y cada orden en segundo plano de la sesión, con su estado, sus
cifras y lo que hace ahora; debajo, todo su detalle (el encargo, cada paso,
el resumen y el final de su salida).

- Se pone al día cada segundo SIN moverte: solo se reescriben las filas cuyo
  texto ha cambiado (el lector no relee la fila elegida si no cambia) y el
  detalle no se sustituye mientras tiene el foco; F5 lo fuerza.
- Al abrirse se coloca en lo último que sigue en marcha.
- Supr (o el menú contextual) detiene la tarea elegida, si el agente lo permite.

No es modal: puedes seguir en la ventana principal con ella abierta. Lee el
registro del gestor (``services/actividad.py``); no sabe de ningún agente.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import wx

from app.services.actividad import RegistroActividad, Tarea
from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import copiar_texto, menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Cada cuánto se pone al día (milisegundos).
INTERVALO_MS = 1000
COLUMNAS = [(_("Tarea"), 900)]


class DialogoActividad(wx.Dialog):
	"""Subagentes y órdenes en segundo plano de una sesión, en vivo."""

	def __init__(self, padre: wx.Window | None, registro: RegistroActividad, clave: str, titulo_sesion: str,
			detener: Callable[[str], None] | None = None) -> None:
		titulo = _("Actividad en segundo plano de «{t}»").format(t=titulo_sesion)
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.registro = registro
		self.clave = clave
		self._detener = detener
		self._tareas: list[Tarea] = []
		self._versiones: dict[str, int] = {}

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.etiqueta = wx.StaticText(panel, label=_("&Tareas:"))
		sizer.Add(self.etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.lista = ListaAccesible(panel, _("Tareas"), COLUMNAS)
		self.lista.SetHelpText(_(
			"Subagentes y órdenes que trabajan junto a la conversación: primero las que siguen en marcha. Cada fila dice "
			"qué es, su estado, sus cifras y lo que hace ahora. Se pone al día sola cada segundo sin moverte. Suprimir "
			"detiene la elegida si el agente lo permite; Control+C copia su detalle; F5 lo pone todo al día."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(900, 220)))
		sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		sizer.Add(wx.StaticText(panel, label=_("&Detalle:")), 0, wx.LEFT | wx.RIGHT, 6)
		self.detalle = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, name=_("Detalle"))
		self.detalle.SetHelpText(_(
			"Todo lo de la tarea elegida: el encargo, cada paso, su resumen y el final de su salida. Mientras estás aquí no "
			"cambia; F5 lo pone al día."
		))
		self.detalle.SetMinSize(self.FromDIP(wx.Size(900, 220)))
		sizer.Add(self.detalle, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra esta ventana. Las tareas siguen trabajando."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
		panel.SetSizer(sizer)

		self.lista.Bind(wx.EVT_LIST_ITEM_FOCUSED, lambda _evento: self._poner_detalle(forzar=True))
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		self.detalle.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		vincular_menu_contextual(self.lista, self._crear_menu)
		self.boton_cerrar.Bind(wx.EVT_BUTTON, lambda _evento: self.Close())
		self.Bind(wx.EVT_CLOSE, self._al_cerrar)
		preparar_dialogo(self, foco_inicial=self.lista)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.poner_al_dia(colocar=True)
		self.Layout()
		self.Fit()
		self.CentreOnParent()
		self._temporizador = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, lambda _evento: self.poner_al_dia(), self._temporizador)
		self._temporizador.Start(INTERVALO_MS)

	# ── Contenido ─────────────────────────────────────────────────────────────

	def tarea_actual(self) -> Tarea | None:
		fila = self.lista.fila_actual()
		return self._tareas[fila] if 0 <= fila < len(self._tareas) else None

	def poner_al_dia(self, colocar: bool = False, forzar: bool = False) -> None:
		"""Relee las tareas. Sin ``forzar``, solo cambia lo que ha cambiado y nunca mueve el foco."""
		if not self:
			return
		actual = self.tarea_actual()
		tareas = self.registro.tareas(self.clave)
		filas = [[t.fila()] for t in tareas]
		mismas = [t.id for t in tareas] == [t.id for t in self._tareas]
		self._tareas = tareas
		if mismas and not forzar:
			for indice, tarea in enumerate(tareas):
				if self._versiones.get(tarea.id) != tarea.version or tarea.en_marcha:
					if self.lista.valores_fila(indice) != filas[indice]:
						self.lista.actualizar_fila(indice, filas[indice])
		else:
			self.lista.reemplazar_filas(filas, conservar_foco=False)
			destino = 0
			if actual is not None and not colocar:
				destino = next((i for i, t in enumerate(tareas) if t.id == actual.id), 0)
			elif colocar:
				# Al abrir: en lo último que sigue en marcha (si no hay ninguna, en la más reciente).
				vivas = [i for i, t in enumerate(tareas) if t.en_marcha]
				destino = max(vivas, key=lambda i: tareas[i].inicio) if vivas else 0
			if tareas:
				self.lista.enfocar_fila(destino, anunciar=False)
		self._versiones = {t.id: t.version for t in tareas}
		vivas = sum(1 for t in tareas if t.en_marcha)
		if tareas:
			self.etiqueta.SetLabel(_("&Tareas ({n}, en marcha {v}):").format(n=len(tareas), v=vivas))
			self.lista.SetName(_("Tareas ({n}, en marcha {v})").format(n=len(tareas), v=vivas))
		else:
			self.etiqueta.SetLabel(_("&Tareas: ninguna todavía."))
			self.lista.SetName(_("Tareas: ninguna todavía"))
		self._poner_detalle(forzar=forzar or colocar)

	def _poner_detalle(self, forzar: bool = False) -> None:
		"""El detalle de la elegida; mientras lo lees (tiene el foco) no cambia, salvo con F5."""
		if not forzar and wx.Window.FindFocus() is self.detalle:
			return
		tarea = self.tarea_actual()
		texto = tarea.detalle() if tarea is not None else _(
			"Aquí aparecen los subagentes y las órdenes en segundo plano de la sesión cuando el agente los lanza.")
		if self.detalle.GetValue() != texto:
			self.detalle.ChangeValue(texto)

	# ── Acciones ──────────────────────────────────────────────────────────────

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if tecla == wx.WXK_F5 and mods == wx.MOD_NONE:
			self.poner_al_dia(forzar=True)
			avisos.anunciar(_("Actividad puesta al día."), interrumpir=True)
			return
		if evento.GetEventObject() is self.lista:
			if tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) and mods == wx.MOD_NONE:
				self.detener_elegida()
				return
			if tecla == ord("C") and mods == wx.MOD_CONTROL:
				self.copiar_detalle()
				return
		evento.Skip()

	def _crear_menu(self) -> wx.Menu | None:
		acciones: list[tuple[str, Callable[[], Any]] | None] = [
			(_("&Copiar el detalle\tCtrl+C"), self.copiar_detalle),
		]
		tarea = self.tarea_actual()
		if tarea is not None and tarea.en_marcha and tarea.detenible and self._detener is not None:
			acciones.append((_("&Detener esta tarea\tSupr"), self.detener_elegida))
		acciones += [None, (_("&Poner al día\tF5"), lambda: self.poner_al_dia(forzar=True))]
		return menu_de_acciones(self.lista, acciones)

	def copiar_detalle(self) -> None:
		tarea = self.tarea_actual()
		if tarea is not None and copiar_texto(tarea.detalle()):
			avisos.anunciar(_("Detalle copiado."), interrumpir=True)

	def detener_elegida(self) -> bool:
		"""Supr: pide al agente que pare la tarea elegida (pregunta antes)."""
		tarea = self.tarea_actual()
		if tarea is None:
			avisos.anunciar(_("No hay ninguna tarea elegida."), interrumpir=True)
			return False
		if not tarea.en_marcha:
			avisos.anunciar(_("Esa tarea ya no está en marcha."), interrumpir=True)
			return False
		if not tarea.detenible or self._detener is None:
			avisos.anunciar(_("El agente no deja detener esta tarea suelta. Control+punto detiene todo el turno."),
				interrumpir=True)
			return False
		if confirmar(self, _("¿Detener {t}? Lo que haya hecho hasta ahora se queda como esté.").format(t=tarea.nombre()),
				_("Detener la tarea"), peligroso=True) != wx.ID_YES:
			return False
		self._detener(tarea.id)
		avisos.anunciar(_("Deteniendo {t}...").format(t=tarea.nombre()), interrumpir=True)
		return True

	def _al_cerrar(self, evento: wx.CloseEvent) -> None:
		self._temporizador.Stop()
		del evento
		self.Destroy()
