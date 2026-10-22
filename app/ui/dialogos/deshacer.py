"""Diálogo «Deshacer los cambios de archivos desde este mensaje» (y «Rehacer los cambios deshechos»).

Enseña, antes de tocar nada, qué pasará con cada archivo (lista con las columnas
«Archivo» y «Qué pasará») y los avisos (cambios hechos a mano después del último
turno, archivos no protegidos, repositorios anidados, commits que no se
deshacen). Intro en la lista no deshace: hay que pulsar «Deshacer» (Alt+D); el
botón predeterminado es «Cancelar». El trabajo lo hace ``GestorSesiones`` en
segundo plano (``app/services/puntos.py``).
"""

from __future__ import annotations

import wx

from app.services.puntos import PlanDeshacer, archivos
from app.ui.accesibilidad.dialogos import preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.teclado import copiar_texto, vincular_menu_contextual
from app.ui.comun import _


class DialogoDeshacer(wx.Dialog):
	"""Confirma un deshacer (o un rehacer). ``ShowModal()`` devuelve ``wx.ID_OK`` si la persona lo acepta."""

	def __init__(self, padre: wx.Window | None, plan: PlanDeshacer) -> None:
		self.plan = plan
		if plan.rehacer:
			titulo = _("Rehacer los cambios deshechos")
			explicacion = _(
				"Los archivos volverán a como los dejó el agente antes de que deshicieras sus cambios desde tu mensaje "
				"«{m}». Cambiará {n}."
			).format(m=plan.resumen, n=archivos(len(plan.cambios)))
		else:
			titulo = _("Deshacer los cambios de archivos")
			explicacion = _(
				"Los archivos de la carpeta volverán a como estaban justo antes de tu mensaje «{m}»: se deshace todo lo que "
				"cambió desde entonces. Cambiará {n}."
			).format(m=plan.resumen, n=archivos(len(plan.cambios)))
			if not plan.remoto:
				explicacion += " " + _(
					"Antes se guarda cómo están ahora, así que podrás volver atrás con «Rehacer los cambios deshechos» "
					"(Control+Alt+Mayúsculas+Z). La conversación no cambia, y con tu siguiente mensaje el agente sabrá qué "
					"archivos han vuelto atrás."
				)
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		texto = wx.StaticText(panel, label=explicacion)
		texto.Wrap(self.FromDIP(640))
		raiz.Add(texto, 0, wx.ALL, 8)

		etiqueta = wx.StaticText(panel, label=_("&Archivos:"))
		self.lista = ListaAccesible(panel, _("Archivos"), columnas=[(_("Archivo"), 440), (_("Qué pasará"), 260)])
		self.lista.SetHelpText(_(
			"Cada archivo que cambiará y qué pasará con él: se devolverá como estaba, se borrará porque lo creó el agente "
			"o se volverá a crear porque el agente lo borró. Flecha derecha lee la columna «Qué pasará». La tecla "
			"Aplicaciones copia la ruta o la lista entera. Nada cambia hasta que pulses «{b}».").format(
				b=_("Rehacer") if plan.rehacer else _("Deshacer")))
		self.lista.SetMinSize(self.FromDIP(wx.Size(700, 260)))
		raiz.Add(etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
		self.lista.reemplazar_filas([[cambio.ruta, _(cambio.texto(plan.rehacer))] for cambio in plan.cambios])
		vincular_menu_contextual(self.lista, self._crear_menu)

		self.avisos: wx.TextCtrl | None = None
		if plan.avisos:
			etiqueta_avisos = wx.StaticText(panel, label=_("A&visos:"))
			self.avisos = wx.TextCtrl(panel, value="\n\n".join(plan.avisos),
				style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP, name=_("Avisos"))
			self.avisos.SetHelpText(_("Lo que conviene saber antes de seguir: qué no se deshace y por qué. Es solo lectura."))
			self.avisos.SetMinSize(self.FromDIP(wx.Size(700, 120)))
			raiz.Add(etiqueta_avisos, 0, wx.LEFT | wx.RIGHT, 8)
			raiz.Add(self.avisos, 0, wx.EXPAND | wx.ALL, 8)

		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_aceptar = wx.Button(panel, wx.ID_OK, _("&Rehacer") if plan.rehacer else _("&Deshacer"))
		self.boton_aceptar.SetHelpText(_("Vuelve a poner los archivos como los dejó el agente.") if plan.rehacer else
			_("Deja los archivos de la lista como estaban antes de tu mensaje."))
		self.boton_cancelar = wx.Button(panel, wx.ID_CANCEL, _("&Cancelar"))
		self.boton_cancelar.SetHelpText(_("Cierra sin cambiar nada. También con Escape."))
		self.boton_cancelar.SetDefault()
		fila.Add(self.boton_aceptar, 0, wx.RIGHT, 6)
		fila.Add(self.boton_cancelar, 0)
		raiz.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.boton_aceptar.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_OK))
		preparar_dialogo(self, foco_inicial=self.lista)
		self.CentreOnParent()

	def _crear_menu(self) -> wx.Menu | None:
		if not self.plan.cambios:
			return None
		menu = wx.Menu()
		copiar_ruta = menu.Append(wx.ID_ANY, _("Copiar la &ruta del archivo"))
		copiar_lista = menu.Append(wx.ID_ANY, _("Copiar la &lista entera"))
		self.lista.Bind(wx.EVT_MENU, lambda _e: self.copiar_ruta(), copiar_ruta)
		self.lista.Bind(wx.EVT_MENU, lambda _e: self.copiar_lista(), copiar_lista)
		return menu

	def copiar_ruta(self) -> bool:
		fila = self.lista.fila_actual()
		if not 0 <= fila < len(self.plan.cambios):
			return False
		return copiar_texto(self.plan.cambios[fila].ruta)

	def copiar_lista(self) -> bool:
		return copiar_texto("\n".join(f"{c.ruta}: {_(c.texto(self.plan.rehacer))}" for c in self.plan.cambios))
