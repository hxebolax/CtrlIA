"""Teclado: atajos, menú contextual y Escape en ventanas principales.

Reglas:

- Toda acción del ratón o de una barra de herramientas tiene equivalente de
  teclado (menú con acelerador ``"&Abrir...\\tCtrl+O"`` o tabla de atajos).
- El menú contextual se abre con ``EVT_CONTEXT_MENU`` (tecla Aplicaciones y
  Mayús+F10), NUNCA solo con ``EVT_RIGHT_UP``.
- ``EVT_CHAR_HOOK`` siempre termina en ``evento.Skip()`` para lo que no se
  gestiona; si no, deja de funcionar todo el teclado de la ventana.

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import wx


def vincular_menu_contextual(control: wx.Window, crear_menu: Callable[[], wx.Menu | None]) -> None:
	"""Abre el menú contextual con ratón, tecla Aplicaciones o Mayús+F10.

	Si el evento viene del teclado, la posición es ``wx.DefaultPosition``: el
	menú se coloca junto al elemento enfocado (en listas y árboles) para que
	el lector lo asocie con él.
	"""

	def al_pedir_menu(evento: wx.ContextMenuEvent) -> None:
		menu = crear_menu()
		if menu is None:
			return
		posicion = evento.GetPosition()
		if posicion == wx.DefaultPosition:
			posicion = wx.Point(0, 0)
			if isinstance(control, wx.ListCtrl) and control.GetFocusedItem() != wx.NOT_FOUND:
				posicion = control.GetItemRect(control.GetFocusedItem()).GetBottomLeft()
			elif isinstance(control, wx.TreeCtrl) and control.GetFocusedItem().IsOk():
				rectangulo = control.GetBoundingRect(control.GetFocusedItem())
				if rectangulo:
					posicion = rectangulo.GetBottomLeft()
		else:
			posicion = control.ScreenToClient(posicion)
		control.PopupMenu(menu, posicion)
		menu.Destroy()

	control.Bind(wx.EVT_CONTEXT_MENU, al_pedir_menu)


def instalar_atajos(ventana: wx.Window, atajos: Sequence[tuple[int, int, Callable[[], None]]]) -> list[int]:
	"""Crea una tabla de aceleradores para acciones sin elemento de menú.

	::

		instalar_atajos(self, [
			(wx.ACCEL_CTRL, ord("F"), self.buscar),
			(wx.ACCEL_NORMAL, wx.WXK_F5, self.actualizar),
		])

	Documente cada atajo en la ayuda o en la lista de teclas del manual.
	Devuelve los ids creados.
	"""
	entradas, ids = [], []
	for modificadores, tecla, accion in atajos:
		identificador = wx.NewIdRef()
		ventana.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), id=identificador)
		entradas.append(wx.AcceleratorEntry(modificadores, tecla, identificador))
		ids.append(int(identificador))
	ventana.SetAcceleratorTable(wx.AcceleratorTable(entradas))
	return ids


def cerrar_con_escape(ventana: wx.TopLevelWindow) -> None:
	"""Escape cierra una ventana que no es diálogo (los diálogos: ``preparar_dialogo``)."""

	def al_pulsar(evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() == wx.WXK_ESCAPE and not evento.HasAnyModifiers():
			ventana.Close()
			return
		evento.Skip()

	ventana.Bind(wx.EVT_CHAR_HOOK, al_pulsar)
