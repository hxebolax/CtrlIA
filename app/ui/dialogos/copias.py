"""Diálogos de las copias de seguridad: hacer una copia, programarlas y elegir qué restaurar.

Solo preguntan y validan: el trabajo lo hace ``app/services/copias.py`` en un hilo
(``app/ui/vp_copias.py``). Las frases van en campos de contraseña y nunca se guardan
aquí: la de las copias programadas la protege Windows (DPAPI).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import wx

from app.services import copias
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import confirmar, enfocar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo, crear_selector_ruta
from app.ui.accesibilidad.teclado import menu_de_acciones, vincular_menu_contextual
from app.ui.comun import _, fecha_relativa, nombre_agente

log = logging.getLogger(__name__)

AVISO_PRIVACIDAD = _(
	"La copia lleva tus conversaciones, tu memoria y tus ajustes, también lo que apareciera en ellas, como una contraseña "
	"que saliera en una orden. Nunca lleva los inicios de sesión de los agentes. Guárdala en un sitio privado y, mejor, en "
	"otro disco. Si la cifras y olvidas la frase, no se podrá abrir: nadie puede recuperarla."
)


def _problema_frase(frase: str, repetida: str) -> str:
	if len(frase) < copias.MIN_FRASE:
		return _("La frase debe tener al menos {n} caracteres; cuanto más larga, más segura.").format(n=copias.MIN_FRASE)
	if frase != repetida:
		return _("Las dos frases no coinciden.")
	return ""


def _cerrar_con_aceptar(dialogo: wx.Dialog) -> None:
	if dialogo.IsModal():
		dialogo.EndModal(wx.ID_OK)
	else:
		dialogo.SetReturnCode(wx.ID_OK)
		dialogo.Hide()


class DialogoHacerCopia(wx.Dialog):
	"""Dónde se guarda la copia y si va cifrada. Tras ``wx.ID_OK``: ``carpeta`` y ``frase``."""

	def __init__(self, padre: wx.Window | None, carpeta: str = "", ultima: str = "") -> None:
		titulo = _("Hacer una copia ahora")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.carpeta = ""
		self.frase: str | None = None
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_carpeta, self.boton_examinar = crear_selector_ruta(panel, _("&Carpeta donde se guarda la copia:"), valor=carpeta,
			sizer=raiz, ayuda=_("Carpeta donde se crea el archivo de la copia. Mejor en otro disco, un lápiz USB o una carpeta en la "
				"nube: en el mismo disco no sobreviviría a perder el equipo. No puede estar dentro de lo que se copia."))
		self.casilla_cifrar = wx.CheckBox(panel, label=_("C&ifrar la copia con una frase"))
		self.casilla_cifrar.SetHelpText(_("Cifra toda la copia con AES-256, también los nombres de los archivos. Ctrl+IA la abre "
			"con cualquier frase; 7-Zip, solo si la frase no lleva ñ ni tildes. La frase no se guarda: sin ella, la copia no se "
			"puede abrir."))
		raiz.Add(self.casilla_cifrar, 0, wx.ALL, 5)
		self.casilla_cifrar.Bind(wx.EVT_CHECKBOX, lambda _e: self.actualizar())
		_e, self.campo_frase = crear_campo(panel, _("&Frase:"), wx.TextCtrl, sizer=raiz, style=wx.TE_PASSWORD,
			ayuda=_("La frase que abrirá la copia, de {n} caracteres o más. Cuanto más larga, más segura.").format(n=copias.MIN_FRASE))
		_e, self.campo_repetir = crear_campo(panel, _("&Repite la frase:"), wx.TextCtrl, sizer=raiz, style=wx.TE_PASSWORD,
			ayuda=_("Escribe otra vez la frase, para evitar un error al teclearla."))
		_e, self.campo_aviso = crear_campo(panel, _("A&ntes de copiar:"), wx.TextCtrl, sizer=raiz,
			value=(ultima + "\n\n" if ultima else "") + AVISO_PRIVACIDAD, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
			ayuda=_("Cuándo fue la última copia y qué lleva una copia."))
		self.campo_aviso.SetMinSize(self.FromDIP(wx.Size(460, 110)))
		botones = wx.StdDialogButtonSizer()
		self.boton_copiar = wx.Button(panel, wx.ID_OK, _("&Hacer la copia"))
		self.boton_copiar.SetHelpText(_("Empieza la copia en segundo plano: puedes seguir trabajando. La barra de estado dice "
			"cómo va."))
		self.boton_copiar.SetDefault()
		self.boton_cancelar = wx.Button(panel, wx.ID_CANCEL, _("Cancelar"))
		self.boton_cancelar.SetHelpText(_("Cierra sin hacer la copia."))
		botones.AddButton(self.boton_copiar)
		botones.AddButton(self.boton_cancelar)
		botones.Realize()
		raiz.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Bind(wx.EVT_BUTTON, lambda _e: self.aceptar(), id=wx.ID_OK)
		self.actualizar()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.campo_carpeta)

	def actualizar(self) -> None:
		cifrar = self.casilla_cifrar.GetValue()
		self.campo_frase.Enable(cifrar)
		self.campo_repetir.Enable(cifrar)

	def aceptar(self) -> bool:
		carpeta = self.campo_carpeta.GetValue().strip()
		problema = copias.problema_carpeta_programa(carpeta)
		if problema:
			mostrar_error(self, problema, _("Hacer una copia ahora"))
			enfocar(self.campo_carpeta)
			return False
		frase = None
		if self.casilla_cifrar.GetValue():
			frase = self.campo_frase.GetValue()
			problema = _problema_frase(frase, self.campo_repetir.GetValue())
			if problema:
				mostrar_error(self, problema, _("Hacer una copia ahora"))
				enfocar(self.campo_frase)
				return False
		self.carpeta, self.frase = carpeta, frase
		_cerrar_con_aceptar(self)
		return True


class DialogoProgramarCopias(wx.Dialog):
	"""Cada cuánto, cuántas se conservan, dónde y con qué frase.

	Tras ``wx.ID_OK``: ``programa`` y ``frase_nueva`` (``None`` si no se ha escrito ninguna).
	"""

	def __init__(self, padre: wx.Window | None, programa: copias.Programa, hay_frase: bool = False, estado: str = "") -> None:
		titulo = _("Programar copias")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.programa = programa
		self.frase_nueva: str | None = None
		self._hay_frase = hay_frase
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_frecuencia = crear_campo(panel, _("&Frecuencia:"), wx.Choice, sizer=raiz,
			choices=[_(copias.NOMBRES_FRECUENCIA[f]) for f in copias.FRECUENCIAS],
			ayuda=_("Cada cuánto se hace una copia. Una copia hecha a mano también cuenta. Se hace en segundo plano y sin "
				"molestar, con Ctrl+IA abierto; si estaba cerrado cuando tocaba, se hace al abrirlo."))
		self.campo_frecuencia.SetSelection(copias.FRECUENCIAS.index(programa.frecuencia))
		self.campo_frecuencia.Bind(wx.EVT_CHOICE, lambda _e: self.actualizar())
		_e, self.campo_conservar = crear_campo(panel, _("Cuántas copias &conservar:"), wx.SpinCtrl, sizer=raiz,
			min=1, max=copias.MAX_CONSERVAR, initial=programa.conservar,
			ayuda=_("Tras una copia completa y comprobada se borran las más antiguas de este equipo que haya en la carpeta, hasta "
				"dejar este número. Las de otros equipos y las que no se puedan identificar no se tocan."))
		_e, self.campo_carpeta, self.boton_examinar = crear_selector_ruta(panel, _("Car&peta de las copias:"), valor=programa.carpeta,
			sizer=raiz, ayuda=_("Carpeta donde se guardan las copias programadas. Mejor en otro disco o en una carpeta en la nube."))
		self.casilla_cifrar = wx.CheckBox(panel, label=_("C&ifrar las copias con una frase guardada en este equipo"))
		self.casilla_cifrar.SetValue(programa.cifrar)
		self.casilla_cifrar.SetHelpText(_("Windows protege la frase para que solo tu cuenta pueda usarla: así las copias se hacen "
			"sin nadie delante. Otra persona que tenga el archivo necesitaría la frase. Apúntala en un sitio seguro: para "
			"abrir la copia en otro equipo tendrás que escribirla."))
		raiz.Add(self.casilla_cifrar, 0, wx.ALL, 5)
		self.casilla_cifrar.Bind(wx.EVT_CHECKBOX, lambda _e: self.actualizar())
		ayuda = _("La frase de las copias, de {n} caracteres o más.").format(n=copias.MIN_FRASE)
		if hay_frase:
			ayuda += " " + _("Ya hay una guardada: déjalo vacío para seguir usándola.")
		_e, self.campo_frase = crear_campo(panel, _("&Nueva frase:"), wx.TextCtrl, sizer=raiz, style=wx.TE_PASSWORD, ayuda=ayuda)
		_e, self.campo_repetir = crear_campo(panel, _("Repi&te la frase:"), wx.TextCtrl, sizer=raiz, style=wx.TE_PASSWORD,
			ayuda=_("Escribe otra vez la frase, para evitar un error al teclearla."))
		_e, self.campo_estado = crear_campo(panel, _("&Estado:"), wx.TextCtrl, sizer=raiz, value=estado or _("Sin datos todavía."),
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP, ayuda=_("Cuándo fue la última copia y si falló la última programada."))
		self.campo_estado.SetMinSize(self.FromDIP(wx.Size(460, 70)))
		botones = wx.StdDialogButtonSizer()
		self.boton_guardar = wx.Button(panel, wx.ID_OK, _("&Guardar"))
		self.boton_guardar.SetHelpText(_("Guarda el programa."))
		self.boton_guardar.SetDefault()
		self.boton_cancelar = wx.Button(panel, wx.ID_CANCEL, _("Cancelar"))
		self.boton_cancelar.SetHelpText(_("Cierra sin cambiar nada."))
		botones.AddButton(self.boton_guardar)
		botones.AddButton(self.boton_cancelar)
		botones.Realize()
		raiz.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Bind(wx.EVT_BUTTON, lambda _e: self.aceptar(), id=wx.ID_OK)
		self.actualizar()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.campo_frecuencia)

	def actualizar(self) -> None:
		activo = self.campo_frecuencia.GetSelection() != 0
		for control in (self.campo_conservar, self.campo_carpeta, self.boton_examinar, self.casilla_cifrar):
			control.Enable(activo)
		cifrar = activo and self.casilla_cifrar.GetValue()
		self.campo_frase.Enable(cifrar)
		self.campo_repetir.Enable(cifrar)

	def aceptar(self) -> bool:
		frecuencia = copias.FRECUENCIAS[self.campo_frecuencia.GetSelection()]
		carpeta = self.campo_carpeta.GetValue().strip()
		cifrar = self.casilla_cifrar.GetValue()
		if frecuencia != "no":
			problema = copias.problema_carpeta_programa(carpeta)
			if problema:
				mostrar_error(self, problema, _("Programar copias"))
				enfocar(self.campo_carpeta)
				return False
			frase = self.campo_frase.GetValue()
			if cifrar and (frase or not self._hay_frase):
				problema = _problema_frase(frase, self.campo_repetir.GetValue())
				if problema:
					mostrar_error(self, problema, _("Programar copias"))
					enfocar(self.campo_frase)
					return False
				self.frase_nueva = frase
			aviso = copias.aviso_carpeta_programa(carpeta)
			if aviso and confirmar(self, aviso + " " + _("¿Usarla de todas formas?"), _("Programar copias")) != wx.ID_YES:
				enfocar(self.campo_carpeta)
				return False
		self.programa = copias.Programa(frecuencia, carpeta, int(self.campo_conservar.GetValue()), cifrar)
		_cerrar_con_aceptar(self)
		return True


class DialogoRestaurarCopia(wx.Dialog):
	"""Qué sesiones de una copia vuelven, con una casilla cada una.

	Salen marcadas las que no están en este equipo. Tras ``wx.ID_OK``, ``elegidas`` tiene las marcadas.
	"""

	def __init__(self, padre: wx.Window | None, sesiones: Sequence[copias.SesionEnCopia], informacion: str,
			leer_preferencias: Callable | None = None) -> None:
		titulo = _("Restaurar desde una copia")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.sesiones = list(sesiones)
		self.elegidas: list[copias.SesionEnCopia] = []
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_informacion = crear_campo(panel, _("Sobre esta &copia:"), wx.TextCtrl, sizer=raiz, value=informacion,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP, ayuda=_("De qué equipo es la copia, de cuándo y lo que no se ha podido leer."))
		self.campo_informacion.SetMinSize(self.FromDIP(wx.Size(-1, 70)))
		self.etiqueta_lista = wx.StaticText(panel, label=_("&Sesiones de la copia ({n}):").format(n=len(self.sesiones)))
		raiz.Add(self.etiqueta_lista, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = ListaAccesible(panel, _("Sesiones de la copia ({n})").format(n=len(self.sesiones)),
			[(_("Título"), 280), (_("Agente"), 100), (_("En este equipo"), 170), (_("Carpeta"), 200), (_("Última actividad"), 150)],
			con_casillas=True, leer_preferencias=leer_preferencias)
		self.lista.SetHelpText(_("Las sesiones que hay en la copia, con una casilla cada una. Espacio marca o desmarca la sesión; "
			"Control+A las marca todas y Control+Mayúsculas+A las desmarca. Salen marcadas las que no están en este equipo. "
			"Restaurar otra vez una sesión la actualiza, no la duplica."))
		self.lista.SetMinSize(self.FromDIP(wx.Size(720, 260)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		vincular_menu_contextual(self.lista, self._crear_menu)
		botones = wx.StdDialogButtonSizer()
		self.boton_restaurar = wx.Button(panel, wx.ID_OK, _("&Restaurar"))
		self.boton_restaurar.SetHelpText(_("Trae las sesiones marcadas como importadas, solo para leer. Los archivos de los agentes "
			"no se tocan."))
		self.boton_restaurar.SetDefault()
		self.boton_cancelar = wx.Button(panel, wx.ID_CANCEL, _("Cancelar"))
		self.boton_cancelar.SetHelpText(_("Cierra sin restaurar nada."))
		botones.AddButton(self.boton_restaurar)
		botones.AddButton(self.boton_cancelar)
		botones.Realize()
		raiz.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Bind(wx.EVT_BUTTON, lambda _e: self.aceptar(), id=wx.ID_OK)
		self.lista.reemplazar_filas([
			[s.titulo, nombre_agente(s.proveedor), _(copias.NOMBRES_SITUACION[s.situacion]), s.carpeta or _("Sin carpeta"),
				fecha_relativa(s.actividad)] for s in self.sesiones
		], conservar_foco=False)
		self.marcar_las_que_faltan()
		if self.sesiones:
			self.lista.enfocar_fila(0, anunciar=False)
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.lista)

	def _marcar_filas(self, condicion: Callable[[copias.SesionEnCopia], bool]) -> None:
		anterior, self.lista._anunciar_casillas = self.lista._anunciar_casillas, False
		try:
			for fila, sesion in enumerate(self.sesiones):
				self.lista.CheckItem(fila, condicion(sesion))
		finally:
			self.lista._anunciar_casillas = anterior

	def marcar_las_que_faltan(self) -> None:
		self._marcar_filas(lambda sesion: sesion.situacion != copias.EN_EL_EQUIPO)

	def alternar_actual(self) -> None:
		fila = self.lista.fila_actual()
		if 0 <= fila < len(self.sesiones):
			self.lista.CheckItem(fila, not self.lista.IsItemChecked(fila))

	def _crear_menu(self) -> wx.Menu | None:
		if not self.sesiones:
			return None

		def faltan() -> None:
			self.marcar_las_que_faltan()
			voz.hablar(_("Marcadas las que no están en este equipo"))

		return menu_de_acciones(self.lista, [
			(_("Marcar o &desmarcar esta sesión\tEspacio"), self.alternar_actual),
			(_("Marcar &todas\tCtrl+A"), lambda: self.lista.marcar_todas(True)),
			(_("&Quitar todas las marcas\tCtrl+Mayús+A"), lambda: self.lista.marcar_todas(False)),
			(_("Marcar &solo las que no están en este equipo"), faltan),
		])

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if tecla == ord("A") and mods in (wx.MOD_CONTROL, wx.MOD_CONTROL | wx.MOD_SHIFT):
			self.lista.marcar_todas(mods == wx.MOD_CONTROL)
			return
		evento.Skip()

	def aceptar(self) -> bool:
		elegidas = [self.sesiones[i] for i in self.lista.filas_marcadas() if 0 <= i < len(self.sesiones)]
		if not elegidas:
			mostrar_error(self, _("No hay ninguna sesión marcada. Marca con Espacio las que quieras restaurar."),
				_("Restaurar desde una copia"))
			enfocar(self.lista)
			return False
		self.elegidas = elegidas
		_cerrar_con_aceptar(self)
		return True
