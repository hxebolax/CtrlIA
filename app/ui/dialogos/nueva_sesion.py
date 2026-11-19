"""Diálogo «Nueva sesión»: agente, carpeta, opciones y primer mensaje.

El modelo y el esfuerzo que se eligen para cada agente se recuerdan
(``agentes.ultimas_opciones``) y se proponen la vez siguiente con ese agente,
también si se cambian después en «Opciones de la sesión».

1.6.0:

- **Instrucciones de la sesión y más carpetas de trabajo** (``PanelInstrucciones``,
  también en «Opciones de la sesión»): solo con los agentes que tienen
  ``Capacidades.instrucciones_sesion``; van en ``extra["instrucciones"]`` y
  ``extra["carpetas_adicionales"]`` (nunca una unidad entera ni la raíz de un
  recurso compartido: ``claude.carpeta_concreta``).
- **Carpeta base de proyectos** (``general.carpeta_proyectos``, FastChat
  ``ResolveFolder``): un nombre o una ruta relativa se busca dentro de ella;
  admite ``~`` y quita las comillas de una ruta pegada (``resolver_carpeta``).
  Si la carpeta no existe, se ofrece crearla.
- **Copia aislada** (AIChat #561, #587, #622): la rama nueva empieza en lo que
  tiene la carpeta o en la rama principal de ``origin`` (que se trae antes, en
  segundo plano); o se usa una rama que ya existe (``git.ramas_disponibles``).
- Las etiquetas de los controles desactivados pierden su ``&`` mientras lo
  están (``nombres.habilitar``, AIChat #515).
"""

from __future__ import annotations

import logging
import ntpath
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import wx

from app.services import git
from app.services.agentes import remoto
from app.services.agentes.base import MAX_INSTRUCCIONES, Adjunto, AjustesSesion, OpcionLista
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import confirmar, crear_botonera, enfocar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo, habilitar
from app.ui.comun import _

log = logging.getLogger(__name__)


#: Clave de la configuración con el último modelo y esfuerzo de cada agente.
CLAVE_ULTIMAS_OPCIONES = "agentes.ultimas_opciones"


#: Lo que «Nueva sesión» recuerda de la última vez con cada agente.
OPCIONES_RECORDADAS = ("modelo", "esfuerzo", "estilo")

#: Clave de la configuración con la carpeta base de proyectos (Preferencias, General).
CLAVE_CARPETA_PROYECTOS = "general.carpeta_proyectos"

#: Formas de empezar la rama de una copia aislada.
COPIA_DESDE_CARPETA, COPIA_DESDE_REMOTO, COPIA_RAMA_EXISTENTE = range(3)


class ErrorCarpeta(ValueError):
	"""La carpeta escrita no sirve; el texto es para la persona."""


def resolver_carpeta(texto: str, base: str = "") -> Path:
	"""La carpeta de trabajo que quiere decir ``texto`` (FastChat ``ResolveFolder``).

	Quita espacios y las comillas de una ruta pegada, admite ``~`` (tu carpeta personal) y, si no es una ruta
	completa, la busca dentro de la carpeta base de proyectos ``base``. Sin base, una ruta que no es completa
	no vale (no se sabría desde dónde). No mira si existe. Lanza :class:`ErrorCarpeta`.
	"""
	limpio = (texto or "").strip()
	for abre, cierra in (('"', '"'), ("'", "'"), ("\u201c", "\u201d"), ("\u00ab", "\u00bb")):
		if len(limpio) >= 2 and limpio.startswith(abre) and limpio.endswith(cierra):
			limpio = limpio[1:-1].strip()
	if not limpio:
		raise ErrorCarpeta(_("Escribe la carpeta de trabajo o pulsa Examinar."))
	if limpio == "~" or limpio.startswith(("~/", "~\\")):
		limpio = os.path.expanduser(limpio)
	if ntpath.isabs(limpio) and ntpath.splitdrive(limpio)[0]:
		return Path(limpio)
	if ntpath.splitdrive(limpio)[0]:
		raise ErrorCarpeta(_("«{t}» no es una ruta completa: escribe también la barra tras la unidad, por ejemplo C:\\proyectos.").format(t=limpio))
	base = (base or "").strip().strip('"')
	if base:
		base = os.path.expanduser(base)
	if not base or not ntpath.isabs(base) or not ntpath.splitdrive(base)[0]:
		raise ErrorCarpeta(_("«{t}» no es una ruta completa. Escríbela entera, pulsa Examinar o elige una carpeta base de "
			"proyectos en Preferencias, General, para escribir solo el nombre.").format(t=limpio))
	return Path(base) / limpio.lstrip("\\/")


def carpeta_valida_adicional(ruta: str) -> bool:
	"""Si ``ruta`` puede ser una carpeta adicional: completa y concreta (nunca una unidad ni la raíz de un recurso)."""
	from app.services.agentes.claude import carpeta_concreta

	return carpeta_concreta(ruta)


def opciones_recordadas(config, proveedor: str) -> dict[str, str]:
	"""``{"modelo": ..., "esfuerzo": ...}`` elegidos la última vez con ``proveedor`` (vacío si nunca)."""
	try:
		todas = config.obtener(CLAVE_ULTIMAS_OPCIONES, {}) or {}
	except Exception:  # noqa: BLE001 - una configuración rara no impide crear sesiones
		log.debug("No se pudieron leer las últimas opciones de los agentes", exc_info=True)
		return {}
	datos = todas.get(proveedor) if isinstance(todas, dict) else None
	if not isinstance(datos, dict):
		return {}
	return {clave: valor for clave, valor in datos.items() if clave in OPCIONES_RECORDADAS and isinstance(valor, str)}


def recordar_opciones(config, proveedor: str, **opciones: str) -> None:
	"""Guarda el modelo o el esfuerzo elegidos para ``proveedor`` (solo los que se pasan)."""
	if not proveedor:
		return
	try:
		todas = dict(config.obtener(CLAVE_ULTIMAS_OPCIONES, {}) or {})
		datos = dict(todas.get(proveedor) or {}) if isinstance(todas.get(proveedor), dict) else {}
		# Un estilo vacío (el predeterminado) solo se apunta si sustituye a uno elegido antes.
		datos.update({clave: str(valor) for clave, valor in opciones.items() if clave in OPCIONES_RECORDADAS
			and (valor or clave != "estilo" or clave in datos)})
		todas[proveedor] = datos
		config.establecer(CLAVE_ULTIMAS_OPCIONES, todas)
	except (ValueError, KeyError, TypeError):
		log.debug("No se pudieron recordar las opciones de %s", proveedor, exc_info=True)


def _ayuda_modo() -> str:
	return _("Qué puede hacer el agente sin preguntarte. Se propone el modo de permisos inicial de la configuración "
		"del agente (Herramientas, Configuración de los agentes) para esta carpeta.")


class PanelInstrucciones(wx.Panel):
	"""Instrucciones de la sesión y más carpetas de trabajo (``extra["instrucciones"]`` y ``["carpetas_adicionales"]``).

	Lo usan «Nueva sesión» y «Opciones de la sesión», solo con los agentes que tienen
	``Capacidades.instrucciones_sesion``. En la lista de carpetas, Insert añade y Suprimir quita.
	"""

	def __init__(self, padre: wx.Window, instrucciones: str = "", carpetas: Sequence[str] = ()) -> None:
		super().__init__(padre, name=_("Instrucciones y carpetas de la sesión"))
		raiz = wx.BoxSizer(wx.VERTICAL)
		self.etiqueta_instrucciones, self.instrucciones = crear_campo(
			self, _("Instr&ucciones de la sesión (opcional):"), wx.TextCtrl, value=instrucciones, style=wx.TE_MULTILINE,
			sizer=raiz, ayuda=_("Lo que el agente debe tener en cuenta en toda esta sesión, además de tus archivos de "
				"instrucciones: por ejemplo, «responde en español y no toques la carpeta docs». Como mucho {n} "
				"caracteres. No cambia la configuración del agente.").format(n=MAX_INSTRUCCIONES))
		self.instrucciones.SetMaxLength(MAX_INSTRUCCIONES)
		self.instrucciones.SetMinSize(self.FromDIP(wx.Size(560, 70)))
		self.etiqueta_carpetas, self.carpetas = crear_campo(
			self, _("Más carpetas de traba&jo (opcional):"), wx.ListBox, choices=list(carpetas), style=wx.LB_SINGLE,
			vacia=_("No hay más carpetas; Insert añade una"),
			sizer=raiz, ayuda=_("Otras carpetas en las que el agente también puede leer y escribir, además de la de "
				"trabajo. Nunca una unidad entera ni la raíz de una carpeta compartida. Insert añade una; Suprimir "
				"quita la elegida."))
		self.carpetas.SetMinSize(self.FromDIP(wx.Size(560, 60)))
		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_anadir = wx.Button(self, label=_("Añadir carpeta..."))
		self.boton_anadir.SetHelpText(_("Elige otra carpeta en la que pueda trabajar el agente. También con Insert en la lista."))
		self.boton_quitar = wx.Button(self, label=_("Quitar carpeta"))
		self.boton_quitar.SetHelpText(_("Quita de la lista la carpeta elegida. También con Suprimir en la lista."))
		fila.Add(self.boton_anadir, 0, wx.RIGHT, 6)
		fila.Add(self.boton_quitar, 0)
		raiz.Add(fila, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
		self.SetSizer(raiz)
		self.boton_anadir.Bind(wx.EVT_BUTTON, lambda _e: self.anadir_carpeta())
		self.boton_quitar.Bind(wx.EVT_BUTTON, lambda _e: self.quitar_carpeta())
		self.carpetas.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		self._carpetas_activas = True
		self._actualizar_botones()

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		# atajo: Insert añadir otra carpeta de trabajo
		# atajo: Supr quitar la carpeta de trabajo elegida
		tecla, modificadores = evento.GetKeyCode(), evento.GetModifiers()
		if modificadores == wx.MOD_NONE and tecla in (wx.WXK_INSERT, wx.WXK_NUMPAD_INSERT):
			self.anadir_carpeta()
			return
		if modificadores == wx.MOD_NONE and tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.quitar_carpeta()
			return
		evento.Skip()

	def lista_carpetas(self) -> list[str]:
		return [self.carpetas.GetString(i) for i in range(self.carpetas.GetCount())]

	def anadir_carpeta(self, ruta: str | None = None) -> bool:
		"""Añade una carpeta (sin ``ruta``, la elige la persona). ``True`` si se añadió."""
		if ruta is None:
			with wx.DirDialog(self, _("Otra carpeta de trabajo"), style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dialogo:
				if dialogo.ShowModal() != wx.ID_OK:
					return False
				ruta = dialogo.GetPath()
		ruta = str(ruta).strip().strip('"')
		if not carpeta_valida_adicional(ruta) or not Path(ruta).is_dir():
			mostrar_error(self, _("«{r}» no vale: tiene que ser una carpeta concreta que exista, nunca una unidad entera "
				"ni la raíz de una carpeta compartida.").format(r=ruta), _("Más carpetas de trabajo"))
			return False
		if any(os.path.normcase(os.path.normpath(c)) == os.path.normcase(os.path.normpath(ruta)) for c in self.lista_carpetas()):
			voz.hablar(_("Esa carpeta ya está en la lista"))
			return False
		self.carpetas.Append(ruta)
		self.carpetas.SetSelection(self.carpetas.GetCount() - 1)
		self._actualizar_botones()
		voz.hablar(_("Carpeta añadida"))
		return True

	def quitar_carpeta(self) -> bool:
		"""Quita la carpeta elegida. ``True`` si se quitó."""
		indice = self.carpetas.GetSelection()
		if indice == wx.NOT_FOUND:
			voz.hablar(_("No hay ninguna carpeta elegida"))
			return False
		self.carpetas.Delete(indice)
		if self.carpetas.GetCount():
			self.carpetas.SetSelection(min(indice, self.carpetas.GetCount() - 1))
		self._actualizar_botones()
		voz.hablar(_("Carpeta quitada"))
		return True

	def _actualizar_botones(self) -> None:
		self.boton_anadir.Enable(self._carpetas_activas)
		self.boton_quitar.Enable(self._carpetas_activas and self.carpetas.GetCount() > 0)

	def activar(self, visible: bool, carpetas: bool = True) -> None:
		"""Muestra u oculta el panel; ``carpetas=False`` desactiva las carpetas (una sesión en otro equipo)."""
		self.Show(visible)
		habilitar(self.instrucciones, visible)
		self._carpetas_activas = visible and carpetas
		habilitar(self.carpetas, self._carpetas_activas)
		self._actualizar_botones()

	def comprobar(self) -> str:
		"""Qué está mal (para decirlo), o ``""``."""
		if len(self.instrucciones.GetValue()) > MAX_INSTRUCCIONES:
			return _("Las instrucciones de la sesión tienen más de {n} caracteres: acórtalas.").format(n=MAX_INSTRUCCIONES)
		for carpeta in self.lista_carpetas() if self._carpetas_activas else ():
			if not carpeta_valida_adicional(carpeta):
				return _("«{r}» no puede ser una carpeta de trabajo: quítala de la lista.").format(r=carpeta)
		return ""

	def valores(self) -> tuple[str, list[str]]:
		"""``(instrucciones, carpetas)`` tal como se guardan (instrucciones sin espacios de más)."""
		return self.instrucciones.GetValue().strip(), (self.lista_carpetas() if self._carpetas_activas else [])


@dataclass
class ResultadoNuevaSesion:
	proveedor: str
	ajustes: AjustesSesion
	mensaje: str = ""
	adjuntos: list[Adjunto] = field(default_factory=list)
	grupo: int | None = None


class DialogoNuevaSesion(wx.Dialog):
	"""Recoge todo lo necesario para empezar una sesión; el resultado queda en ``self.resultado``."""

	def __init__(self, padre: wx.Window | None, gestor, almacen, config, carpeta: Path | None = None,
			mensaje: str = "", grupo: int | None = None, agente: str = "", titulo: str = "", equipo: str = "",
			carpeta_remota: str = "") -> None:
		"""
		:param equipo: identificador o nombre del equipo por SSH que se propone, con ``carpeta_remota``.
		:param titulo: título propuesto. Con ``mensaje``, el foco empieza al final del primer mensaje.
		"""
		super().__init__(padre, title=_("Nueva sesión"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.gestor = gestor
		self.config = config
		self.almacen = almacen
		self.resultado: ResultadoNuevaSesion | None = None
		self._ids = list(gestor.proveedores)
		self._modelos: list[OpcionLista] = []
		self._esfuerzos: list[OpcionLista] = []
		self._modos: list[OpcionLista] = []
		# Si la persona elige el modo a mano, cambiar de carpeta ya no lo toca.
		self._modo_elegido_a_mano = False
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)

		nombres = []
		for identificador in self._ids:
			proveedor = gestor.proveedores[identificador]
			estado = gestor.estado_proveedor(identificador)
			nombre = proveedor.nombre
			if estado is not None and not estado.disponible:
				nombre = _("{n} (no instalado)").format(n=nombre)
			nombres.append(nombre)
		_e, self.agente = crear_campo(panel, _("&Agente:"), wx.Choice, choices=nombres, sizer=raiz,
			ayuda=_("El programa de IA con el que vas a trabajar. Los que dicen «no instalado» se instalan desde Herramientas, Agentes instalados."))
		predeterminado = agente or str(config.obtener("agentes.predeterminado", "claude"))
		self.agente.SetSelection(self._ids.index(predeterminado) if predeterminado in self._ids else 0)

		# Equipos por SSH: solo se muestra el campo si hay alguno.
		try:
			self._equipos = list(remoto.equipos(lambda clave, defecto: config.obtener(clave, defecto)).values())
		except Exception:  # noqa: BLE001 - una lista de equipos mal escrita no debe impedir crear sesiones
			log.warning("No se pudo leer la lista de equipos por SSH", exc_info=True)
			self._equipos = []
		self.equipo: wx.Choice | None = None
		if self._equipos:
			opciones = [_("Este equipo (Windows)")] + [_("{n} (por SSH)").format(n=e.nombre) for e in self._equipos]
			_e, self.equipo = crear_campo(panel, _("E&quipo:"), wx.Choice, choices=opciones, sizer=raiz,
				ayuda=_("Dónde trabaja el agente: en este equipo o en uno de los equipos de Herramientas, «Equipos por SSH». "
					"Claude Code y Codex funcionan por SSH de forma estable; Gemini CLI, Antigravity CLI y los agentes "
					"ACP personalizados, de forma experimental."))
			self.equipo.SetSelection(0)

		recientes = almacen.carpetas_recientes()
		self._recientes_locales = recientes
		self._carpeta_local = ""
		inicial = str(carpeta) if carpeta else (recientes[0] if recientes else str(config.obtener("agentes.carpeta_predeterminada", "") or ""))
		fila_carpeta = wx.BoxSizer(wx.HORIZONTAL)
		_e, self.carpeta = crear_campo(panel, _("Carpeta de tra&bajo:"), wx.ComboBox, value=inicial, choices=recientes,
			sizer=fila_carpeta, horizontal=True, ayuda=self._ayuda_carpeta_local())
		self.examinar = wx.Button(panel, label=_("E&xaminar..."))
		self.examinar.SetHelpText(_("Abre el cuadro para elegir la carpeta de trabajo."))
		fila_carpeta.Add(self.examinar, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 6)
		raiz.Add(fila_carpeta, 0, wx.EXPAND)

		self.aislada = wx.CheckBox(panel, label=_("Trabajar en una copia a&islada (rama de git aparte)"))
		self.aislada.SetHelpText(_(
			"Si la carpeta es un repositorio de git, el agente trabaja en una copia aparte con su propia rama. "
			"Tu copia de trabajo no se toca hasta que decidas unir los cambios."
		))
		raiz.Add(self.aislada, 0, wx.ALL, 5)
		_e, self.forma_copia = crear_campo(panel, _("&Forma de empezar la copia:"), wx.Choice, choices=[
			_("Rama nueva desde lo que tiene la carpeta"),
			_("Rama nueva desde la rama principal de origin (se trae antes)"),
			_("Usar una rama que ya existe"),
		], sizer=raiz, ayuda=_("Desde dónde empieza la copia aislada: una rama nueva con lo que tiene ahora la carpeta "
			"(lo que hay en tu rama actual, sin tus cambios sin confirmar); una rama nueva con lo último de la rama "
			"principal del remoto origin, que Ctrl+IA trae antes con git fetch; o una rama que ya existe, tuya o de "
			"origin. Las ramas ya sacadas en otra copia salen al final y no se pueden usar."))
		self.forma_copia.SetSelection(COPIA_DESDE_CARPETA)
		_e, self.rama = crear_campo(panel, _("Nombre de la &rama:"), wx.TextCtrl, value="ctrlia/" + wx.DateTime.Now().Format("%Y%m%d-%H%M"),
			sizer=raiz, ayuda=_("Nombre de la rama nueva para la copia aislada."))
		_e, self.rama_existente = crear_campo(panel, _("Rama que &ya existe:"), wx.Choice, sizer=raiz,
			ayuda=_("La rama que se usará en la copia aislada: las tuyas, las de origin (dicen «en origin») y, al final, "
				"las que ya están sacadas en otra copia, que git no deja usar dos veces."))
		self._ramas: list[git.RamaGit] = []
		self._carpeta_ramas = ""
		self._generacion_ramas = 0
		self._creando_copia = False

		self._grupos = [(None, _("Ninguno"))] + list(almacen.grupos())
		_e, self.grupo = crear_campo(panel, _("&Grupo:"), wx.Choice, choices=[n for _i, n in self._grupos], sizer=raiz,
			ayuda=_("Grupo en el que queda la sesión nueva, por ejemplo el de una investigación. «Ninguno»: sin grupo."))
		self.grupo.SetSelection(next((i for i, (g, _n) in enumerate(self._grupos) if g == grupo), 0))

		_e, self.titulo = crear_campo(panel, _("&Título (opcional):"), wx.TextCtrl, value=titulo, sizer=raiz,
			ayuda=_("Nombre de la sesión en la lista. Si lo dejas vacío se usa el principio de tu primer mensaje."))
		_e, self.modelo = crear_campo(panel, _("&Modelo:"), wx.Choice, sizer=raiz,
			ayuda=_("Qué modelo de IA usa el agente. «Predeterminado» deja que el agente decida. Se propone el que "
				"elegiste la última vez con este agente."))
		_e, self.esfuerzo = crear_campo(panel, _("&Esfuerzo:"), wx.Choice, sizer=raiz,
			ayuda=_("Cuánto piensa el agente antes de responder. Más esfuerzo: respuestas más cuidadas pero más lentas. "
				"Se propone el que elegiste la última vez con este agente."))
		_e, self.modo = crear_campo(panel, _("Modo de &permisos:"), wx.Choice, sizer=raiz,
			ayuda=_ayuda_modo())
		_e, self.estilo = crear_campo(panel, _("Esti&lo de las respuestas:"), wx.Choice, sizer=raiz,
			ayuda=_("Cómo escribe el agente sus respuestas en esta sesión: «Conciso» (breve, cómodo de escuchar), "
				"«Explicativo»... Solo lo tienen algunos agentes, como Claude Code; no cambia su configuración. Se "
				"propone el que elegiste la última vez con este agente."))
		self._estilos: list[OpcionLista] = []
		self.panel_instrucciones = PanelInstrucciones(panel)
		raiz.Add(self.panel_instrucciones, 0, wx.EXPAND)
		from app.ui.dialogos.elegir_skills import PanelSkills

		# Las skills del catálogo (habilidades) que usará la sesión, con cualquier agente de IA.
		self.panel_skills = PanelSkills(panel, almacen=almacen, etiqueta_filtro=_("Categoría &de skills:"),
			etiqueta=_("Skills del catál&ogo para la sesión:"), etiqueta_boton=_("&Ver y gestionar el catálogo de skills..."))
		raiz.Add(self.panel_skills, 0, wx.EXPAND)
		_e, self.mensaje = crear_campo(panel, _("Primer me&nsaje (opcional):"), wx.TextCtrl, style=wx.TE_MULTILINE, sizer=raiz,
			ayuda=_("Lo que quieres que haga el agente. Si lo dejas vacío, la sesión se crea y escribes después."))
		self.mensaje.SetMinSize(self.FromDIP(wx.Size(560, 110)))
		if mensaje:
			self.mensaje.SetValue(mensaje)

		botonera = crear_botonera(panel, wx.OK | wx.CANCEL)
		boton = next((h for h in panel.GetChildren() if isinstance(h, wx.Button) and h.GetId() == wx.ID_OK), None)
		if boton is not None:
			boton.SetLabel(_("Crear la &sesión"))
			boton.SetHelpText(_("Crea la sesión y, si has escrito un mensaje, se lo envía al agente."))
		raiz.Add(botonera, 0, wx.EXPAND | wx.ALL, 6)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		self.agente.Bind(wx.EVT_CHOICE, lambda _e: self._al_cambiar_agente())
		if self.equipo is not None:
			self.equipo.Bind(wx.EVT_CHOICE, lambda _e: self._al_cambiar_equipo())
		self.modo.Bind(wx.EVT_CHOICE, lambda _e: self._al_elegir_modo())
		self.carpeta.Bind(wx.EVT_COMBOBOX, lambda _e: (self._proponer_modo(), self._actualizar_copia()))
		self.carpeta.Bind(wx.EVT_KILL_FOCUS, self._al_salir_de_carpeta)
		self.examinar.Bind(wx.EVT_BUTTON, lambda _e: self._examinar())
		self.aislada.Bind(wx.EVT_CHECKBOX, lambda _e: self._actualizar_copia())
		self.forma_copia.Bind(wx.EVT_CHOICE, lambda _e: self._actualizar_copia())
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		self._al_cambiar_agente()
		self._actualizar_copia()
		if equipo:
			self._elegir_equipo(equipo, carpeta_remota)
		self.Layout()
		self.CentreOnParent()
		if mensaje:
			# Viene de un mensaje: lo normal es escribir enseguida qué quieres explorar.
			preparar_dialogo(self, foco_inicial=self.mensaje)
			self.mensaje.SetInsertionPointEnd()
		else:
			preparar_dialogo(self, foco_inicial=self.agente)

	def _ayuda_carpeta_local(self) -> str:
		ayuda = _("Carpeta del proyecto donde trabajará el agente. Escríbela, elige una reciente con las flechas o pulsa Examinar.")
		if self._carpeta_base():
			ayuda += " " + _("Si escribes solo un nombre (o una ruta que no empieza por la unidad), se busca dentro de tu "
				"carpeta base de proyectos, {base}; si no existe, se te ofrece crearla.").format(base=self._carpeta_base())
		return ayuda

	def _carpeta_base(self) -> str:
		try:
			return str(self.config.obtener(CLAVE_CARPETA_PROYECTOS, "") or "").strip()
		except Exception:  # noqa: BLE001 - sin la opción, las rutas se escriben completas
			log.debug("No se pudo leer la carpeta base de proyectos", exc_info=True)
			return ""

	# -- Copia aislada -------------------------------------------------------------

	def _actualizar_copia(self) -> None:
		"""Activa los campos de la copia aislada según la casilla y la forma elegida (sin «&» si están desactivados)."""
		local = self.equipo_elegido() is None
		aislada = local and self.aislada.GetValue()
		forma = self.forma_copia.GetSelection()
		habilitar(self.forma_copia, aislada and not self._creando_copia)
		habilitar(self.rama, aislada and forma != COPIA_RAMA_EXISTENTE and not self._creando_copia)
		habilitar(self.rama_existente, aislada and forma == COPIA_RAMA_EXISTENTE and not self._creando_copia)
		if aislada and forma == COPIA_RAMA_EXISTENTE:
			self._cargar_ramas()

	def _cargar_ramas(self, forzar: bool = False) -> None:
		"""Rellena «Rama que ya existe» en segundo plano (trae antes la lista de origin con ``git fetch --prune``)."""
		texto = self.carpeta.GetValue()
		try:
			carpeta = resolver_carpeta(texto, self._carpeta_base())
		except ErrorCarpeta:
			carpeta = None
		if carpeta is None or not carpeta.is_dir():
			self._ramas = []
			self.rama_existente.Set([_("(la carpeta de trabajo no existe)")])
			self.rama_existente.SetSelection(0)
			return
		if not forzar and self._carpeta_ramas == str(carpeta):
			return
		self._carpeta_ramas = str(carpeta)
		self._generacion_ramas += 1
		generacion = self._generacion_ramas
		self._ramas = []
		self.rama_existente.Set([_("Buscando ramas…")])
		self.rama_existente.SetSelection(0)

		def leer() -> None:
			try:
				ramas, error = git.ramas_disponibles(carpeta), ""
			except git.ErrorGit as fallo:
				ramas, error = [], str(fallo)
			try:
				wx.CallAfter(self._poner_ramas, generacion, ramas, error)
			except (RuntimeError, AssertionError):
				log.debug("La aplicación se cerró antes de listar las ramas", exc_info=True)

		threading.Thread(target=leer, name="ramas-git", daemon=True).start()

	def _poner_ramas(self, generacion: int, ramas: list[git.RamaGit], error: str) -> None:
		try:
			if not self or generacion != self._generacion_ramas:
				return
		except RuntimeError:
			return
		self._ramas = list(ramas)
		if error:
			self.rama_existente.Set([error])
		elif not ramas:
			self.rama_existente.Set([_("(no hay ramas)")])
		else:
			self.rama_existente.Set([rama.texto for rama in ramas])
		self.rama_existente.SetSelection(0)
		if self.FindFocus() is self.rama_existente:
			voz.hablar(_("{n} ramas").format(n=len(ramas)) if not error else error)

	def rama_elegida(self) -> git.RamaGit | None:
		indice = self.rama_existente.GetSelection()
		return self._ramas[indice] if 0 <= indice < len(self._ramas) else None

	def _elegir_equipo(self, equipo: str, carpeta: str) -> None:
		"""Propone un equipo por SSH (por identificador o nombre) y su carpeta."""
		if self.equipo is None:
			return
		indice = next((i for i, e in enumerate(self._equipos) if equipo in (e.id, e.nombre)), -1)
		if indice < 0:
			return
		self.equipo.SetSelection(indice + 1)
		self._al_cambiar_equipo()
		if carpeta:
			self.carpeta.SetValue(carpeta)
			self._proponer_modo()

	def equipo_elegido(self) -> remoto.EquipoSSH | None:
		"""El equipo por SSH elegido, o ``None`` si es este equipo."""
		if self.equipo is None or self.equipo.GetSelection() <= 0:
			return None
		return self._equipos[self.equipo.GetSelection() - 1]

	def _carpetas_de(self, equipo: remoto.EquipoSSH) -> list[str]:
		"""Carpetas usadas antes en ese equipo (de las sesiones que ya tiene), la más reciente primero."""
		try:
			sesiones = sorted(self.almacen.sesiones(), key=lambda s: s.actividad, reverse=True)
		except Exception:  # noqa: BLE001 - sin historial, la lista queda vacía
			log.debug("No se pudieron leer las sesiones para las carpetas remotas", exc_info=True)
			return []
		carpetas: list[str] = []
		for sesion in sesiones:
			datos = remoto.datos_remotos(sesion.extra)
			carpeta = str(datos.get("carpeta") or "") if datos else ""
			if datos and datos.get("equipo") == equipo.id and carpeta and carpeta not in carpetas:
				carpetas.append(carpeta)
		return carpetas[:15]

	def _al_cambiar_equipo(self) -> None:
		"""Cambia la carpeta, Examinar y la copia aislada según el equipo elegido."""
		equipo = self.equipo_elegido()
		if equipo is None:
			self.carpeta.Set(self._recientes_locales)
			self.carpeta.SetValue(self._carpeta_local)
			self.carpeta.SetHelpText(self._ayuda_carpeta_local())
		else:
			if not self._carpeta_local:
				self._carpeta_local = self.carpeta.GetValue()
			carpetas = self._carpetas_de(equipo)
			self.carpeta.Set(carpetas)
			self.carpeta.SetValue(carpetas[0] if carpetas else "~/")
			self.carpeta.SetHelpText(_("Carpeta del proyecto en el otro equipo, con barras normales, por ejemplo "
				"~/proyectos/web o /Users/ana/proyectos/web. Las flechas muestran las que ya has usado allí."))
		self._proponer_modo()
		remota = equipo is not None
		habilitar(self.examinar, not remota)
		habilitar(self.aislada, not remota)
		if remota:
			self.aislada.SetValue(False)
		self._actualizar_copia()
		self._actualizar_instrucciones()

	def _proveedor(self):
		return self.gestor.proveedores[self._ids[self.agente.GetSelection()]]

	@staticmethod
	def _llenar(control: wx.Choice, opciones: list[OpcionLista], elegido: str = "") -> None:
		"""Rellena un desplegable y elige ``elegido`` (o el primero si no está)."""
		control.Set([o.etiqueta for o in opciones])
		indice = next((i for i, o in enumerate(opciones) if o.id == elegido), 0)
		if opciones:
			control.SetSelection(indice)
		habilitar(control, len(opciones) > 1)

	def _al_cambiar_agente(self) -> None:
		proveedor = self._proveedor()
		recordadas = opciones_recordadas(self.config, proveedor.id)
		self._modelos = proveedor.modelos() or [OpcionLista("", _("Predeterminado"))]
		modelo = recordadas.get("modelo", "")
		if modelo and not any(m.id == modelo for m in self._modelos):
			# Un modelo de la lista real del agente que aún no ha llegado: se ofrece ya con su nombre interno.
			self._modelos.append(OpcionLista(modelo, modelo))
		self._esfuerzos = proveedor.esfuerzos() or [OpcionLista("", _("Predeterminado"))]
		self._modos = proveedor.modos_permiso() or [OpcionLista("", _("Predeterminado"))]
		self._llenar(self.modelo, self._modelos, modelo)
		self._llenar(self.esfuerzo, self._esfuerzos, recordadas.get("esfuerzo", ""))
		self._estilos = (proveedor.estilos() if hasattr(proveedor, "estilos") else []) or [OpcionLista("", _("Predeterminado"))]
		self._llenar(self.estilo, self._estilos, recordadas.get("estilo", ""))
		self._modo_elegido_a_mano = False
		self._llenar(self.modo, self._modos, self._modo_propuesto(proveedor))
		self._al_cambiar_modo()
		self._actualizar_instrucciones()
		self._cargar_modelos_en_vivo(proveedor)

	def _admite_instrucciones(self) -> bool:
		capacidades = getattr(self._proveedor(), "capacidades", None)
		return bool(getattr(capacidades, "instrucciones_sesion", False))

	def _actualizar_instrucciones(self) -> None:
		"""Muestra las instrucciones y las carpetas solo si el agente las admite (las carpetas, solo en este equipo)."""
		visible = self._admite_instrucciones()
		if visible != self.panel_instrucciones.IsShown() or visible:
			self.panel_instrucciones.activar(visible, carpetas=self.equipo_elegido() is None)
			self.Layout()
			if visible:
				self.Fit()
	def _modo_propuesto(self, proveedor) -> str:
		"""El modo de permisos inicial de la configuración del agente para la carpeta escrita.

		En otro equipo los archivos del agente están allí y no se leen: se propone el modo de siempre.
		"""
		if self.equipo_elegido() is not None:
			return proveedor.modo_de_serie()
		try:
			ruta = resolver_carpeta(self.carpeta.GetValue(), self._carpeta_base())
			carpeta = ruta if ruta.is_dir() else None
		except (ErrorCarpeta, OSError):
			carpeta = None
		try:
			return proveedor.modo_predeterminado(carpeta)
		except Exception:  # noqa: BLE001 - una configuración ilegible no impide crear la sesión
			log.warning("No se pudo leer el modo de permisos inicial de %s", proveedor.id, exc_info=True)
			return self._modos[0].id if self._modos else ""

	def _proponer_modo(self) -> None:
		"""Vuelve a proponer el modo al cambiar de carpeta o de equipo, salvo si se eligió a mano."""
		if self._modo_elegido_a_mano or not self._modos:
			return
		propuesto = self._modo_propuesto(self._proveedor())
		indice = next((i for i, o in enumerate(self._modos) if o.id == propuesto), 0)
		if indice != self.modo.GetSelection():
			self.modo.SetSelection(indice)
			self._al_cambiar_modo()

	def _al_salir_de_carpeta(self, evento: wx.FocusEvent) -> None:
		evento.Skip()
		self._proponer_modo()
		self._actualizar_copia()

	def _al_elegir_modo(self) -> None:
		self._modo_elegido_a_mano = True
		self._al_cambiar_modo()

	def _cargar_modelos_en_vivo(self, proveedor) -> None:
		"""Pide al agente su lista real de modelos sin bloquear (Codex y Antigravity la dan)."""
		listar = getattr(proveedor, "listar_modelos", None)
		if not callable(listar):
			return
		identificador = proveedor.id

		def trabajo() -> None:
			try:
				modelos = listar()
			except Exception:  # noqa: BLE001 - sin lista en vivo se quedan los modelos conocidos
				log.debug("No se pudo obtener la lista de modelos de %s", identificador, exc_info=True)
				return
			wx.CallAfter(self._poner_modelos, identificador, modelos)

		threading.Thread(target=trabajo, name="modelos", daemon=True).start()

	def _poner_modelos(self, identificador: str, modelos: list[OpcionLista]) -> None:
		if not self or not modelos or self._ids[self.agente.GetSelection()] != identificador:
			return
		actual = self._modelos[self.modelo.GetSelection()] if self._modelos and self.modelo.GetSelection() >= 0 else None
		elegido = actual.id if actual else ""
		if not any(m.id == "" for m in modelos):
			modelos = [OpcionLista("", _("Predeterminado del agente")), *modelos]
		if elegido and not any(m.id == elegido for m in modelos):
			# El elegido (o el recordado) no está en la lista real: se conserva.
			modelos = [*modelos, actual]
		self._modelos = modelos
		self._llenar(self.modelo, self._modelos, elegido)

	def _al_cambiar_modo(self) -> None:
		indice = self.modo.GetSelection()
		if 0 <= indice < len(self._modos) and self._modos[indice].ayuda:
			self.modo.SetHelpText(_ayuda_modo() + " " + _("Ahora: {a}").format(a=self._modos[indice].ayuda))

	def _examinar(self) -> None:
		with wx.DirDialog(self, _("Carpeta de trabajo"), defaultPath=self.carpeta.GetValue(), style=wx.DD_DEFAULT_STYLE) as dialogo:
			if dialogo.ShowModal() == wx.ID_OK:
				self.carpeta.SetValue(dialogo.GetPath())
		enfocar(self.carpeta)

	def _comprobar_instrucciones(self) -> bool:
		if not self.panel_instrucciones.IsShown():
			return True
		problema = self.panel_instrucciones.comprobar()
		if problema:
			mostrar_error(self, problema, _("Nueva sesión"))
			enfocar(self.panel_instrucciones.instrucciones)
			return False
		return True

	def _carpeta_de_trabajo(self) -> Path | None:
		"""La carpeta escrita, resuelta con la carpeta base; si no existe, ofrece crearla. ``None`` si no vale."""
		try:
			ruta = resolver_carpeta(self.carpeta.GetValue(), self._carpeta_base())
		except ErrorCarpeta as error:
			mostrar_error(self, str(error), _("Nueva sesión"))
			enfocar(self.carpeta)
			return None
		try:
			existe, es_carpeta = ruta.exists(), ruta.is_dir()
		except OSError:
			existe, es_carpeta = False, False
		if es_carpeta:
			return ruta
		if existe:
			mostrar_error(self, _("«{r}» es un archivo, no una carpeta.").format(r=ruta), _("Nueva sesión"))
			enfocar(self.carpeta)
			return None
		if not carpeta_valida_adicional(str(ruta)):
			mostrar_error(self, _("«{r}» no puede ser la carpeta de trabajo.").format(r=ruta), _("Nueva sesión"))
			enfocar(self.carpeta)
			return None
		if confirmar(self, _("La carpeta «{r}» no existe. ¿Crearla?").format(r=ruta), _("Nueva sesión")) != wx.ID_YES:
			enfocar(self.carpeta)
			return None
		try:
			ruta.mkdir(parents=True, exist_ok=True)
		except OSError as error:
			mostrar_error(self, _("No se pudo crear la carpeta «{r}»: {e}").format(r=ruta, e=error.strerror or error), _("Nueva sesión"))
			enfocar(self.carpeta)
			return None
		voz.hablar(_("Carpeta creada"))
		return ruta

	def _aceptar(self) -> None:
		if self._creando_copia:
			voz.hablar(_("Se está preparando la copia aislada; espera un momento."))
			return
		if not self._comprobar_instrucciones():
			return
		if self.equipo_elegido() is not None:
			self._aceptar_remota()
			return
		identificador = self._ids[self.agente.GetSelection()]
		estado = self.gestor.estado_proveedor(identificador)
		if estado is not None and not estado.disponible:
			mostrar_error(self, _(
				"{n} no está instalado en este equipo. Instálalo desde Herramientas, Agentes instalados, o elige otro agente."
			).format(n=self._proveedor().nombre), _("Nueva sesión"))
			enfocar(self.agente)
			return
		ruta = self._carpeta_de_trabajo()
		if ruta is None:
			return
		if not self.aislada.GetValue():
			self._terminar(identificador, ruta, ruta.name)
			return
		forma = self.forma_copia.GetSelection()
		if forma == COPIA_DESDE_REMOTO:
			self._crear_copia_en_fondo(identificador, lambda: git.crear_worktree_desde_remoto(ruta, self._rama_nueva()))
			return
		try:
			if forma == COPIA_RAMA_EXISTENTE:
				rama = self.rama_elegida()
				if rama is None:
					mostrar_error(self, _("Elige una rama de la lista «Rama que ya existe» (o espera a que termine de buscarlas)."),
						_("Copia aislada"))
					enfocar(self.rama_existente)
					return
				ruta = git.crear_worktree_rama_existente(ruta, rama)
			else:
				ruta = git.crear_worktree(ruta, self._rama_nueva())
		except git.ErrorGit as error:
			mostrar_error(self, str(error), _("Copia aislada"))
			return
		self._terminar(identificador, ruta, ruta.name)

	def _rama_nueva(self) -> str:
		return self.rama.GetValue().strip()

	def _crear_copia_en_fondo(self, identificador: str, crear) -> None:
		"""Crea la copia aislada en otro hilo (``git fetch`` puede tardar) y, al acabar, crea la sesión."""
		self._creando_copia = True
		self._actualizar_copia()
		voz.hablar(_("Trayendo la rama principal de origin y preparando la copia aislada…"))

		def trabajo() -> None:
			try:
				resultado, error = crear(), ""
			except git.ErrorGit as fallo:
				resultado, error = None, str(fallo)
			try:
				wx.CallAfter(self._copia_creada, identificador, resultado, error)
			except (RuntimeError, AssertionError):
				log.debug("La aplicación se cerró mientras se creaba la copia aislada", exc_info=True)

		threading.Thread(target=trabajo, name="copia-aislada", daemon=True).start()

	def _copia_creada(self, identificador: str, ruta: Path | None, error: str) -> None:
		try:
			if not self:
				return
		except RuntimeError:
			return
		self._creando_copia = False
		self._actualizar_copia()
		if ruta is None:
			mostrar_error(self, error, _("Copia aislada"))
			return
		self._terminar(identificador, ruta, ruta.name)

	def _aceptar_remota(self) -> None:
		"""Sesión en otro equipo por SSH: la carpeta es de ese equipo y no se comprueba desde Windows."""
		equipo = self.equipo_elegido()
		assert equipo is not None
		proveedor = self._proveedor()
		if not remoto.admite_agente(proveedor):
			mostrar_error(self, _("{n} no se puede usar en otro equipo por SSH. Por SSH funcionan Claude Code, Codex, "
				"Gemini CLI, Antigravity CLI y los agentes ACP personalizados.").format(n=proveedor.nombre), _("Nueva sesión"))
			enfocar(self.agente)
			return
		try:
			carpeta = remoto.normalizar_carpeta(self.carpeta.GetValue())
		except remoto.ErrorRemoto as error:
			mostrar_error(self, str(error), _("Nueva sesión"))
			enfocar(self.carpeta)
			return
		if remoto.es_experimental(proveedor.id):
			pregunta = _("{n} en otro equipo por SSH es experimental: puede fallar al arrancar, al iniciar sesión o al "
				"pedir permisos. Si falla, usa Claude Code o Codex, que funcionan de forma estable. "
				"¿Crear la sesión igualmente?").format(n=proveedor.nombre)
			if confirmar(self, pregunta, _("Función experimental")) != wx.ID_YES:
				return
		extra = {"remoto": {"equipo": equipo.id, "nombre": equipo.nombre, "carpeta": carpeta}}
		self._terminar(proveedor.id, PurePosixPath(carpeta), PurePosixPath(carpeta).name or carpeta, extra)

	def _terminar(self, identificador: str, ruta, nombre_carpeta: str, extra: dict | None = None) -> None:
		mensaje = self.mensaje.GetValue()
		titulo = self.titulo.GetValue().strip() or " ".join(mensaje.split())[:60] or nombre_carpeta
		ajustes = AjustesSesion(
			carpeta=ruta, titulo=titulo,
			modelo=self._modelos[self.modelo.GetSelection()].id if self._modelos else "",
			esfuerzo=self._esfuerzos[self.esfuerzo.GetSelection()].id if self._esfuerzos else "",
			modo_permiso=self._modos[self.modo.GetSelection()].id if self._modos else "",
			extra=dict(extra or {}),
		)
		try:
			self.config.establecer("agentes.predeterminado", identificador)
		except (ValueError, KeyError):
			log.debug("No se pudo recordar el agente elegido", exc_info=True)
		skills = self.panel_skills.seleccionados()
		if skills:
			ajustes.extra["skills"] = skills
		estilo = self._estilos[self.estilo.GetSelection()].id if self._estilos and self.estilo.GetSelection() >= 0 else ""
		if estilo:
			ajustes.extra["estilo"] = estilo
		if self.panel_instrucciones.IsShown():
			instrucciones, carpetas = self.panel_instrucciones.valores()
			if instrucciones:
				ajustes.extra["instrucciones"] = instrucciones
			if carpetas:
				ajustes.extra["carpetas_adicionales"] = carpetas
		recordar_opciones(self.config, identificador, modelo=ajustes.modelo, esfuerzo=ajustes.esfuerzo, estilo=estilo)
		indice_grupo = self.grupo.GetSelection()
		grupo = self._grupos[indice_grupo][0] if 0 <= indice_grupo < len(self._grupos) else None
		self.resultado = ResultadoNuevaSesion(identificador, ajustes, mensaje, grupo=grupo)
		self.EndModal(wx.ID_OK)
