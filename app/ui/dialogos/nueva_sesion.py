"""Diálogo «Nueva sesión»: agente, carpeta, opciones y primer mensaje.

El modelo y el esfuerzo que se eligen para cada agente se recuerdan
(``agentes.ultimas_opciones``) y se proponen la vez siguiente con ese agente,
también si se cambian después en «Opciones de la sesión».
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import wx

from app.services import git
from app.services.agentes import remoto
from app.services.agentes.base import Adjunto, AjustesSesion, OpcionLista
from app.ui.accesibilidad.dialogos import confirmar, crear_botonera, enfocar, mostrar_error, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.comun import _

log = logging.getLogger(__name__)


#: Clave de la configuración con el último modelo y esfuerzo de cada agente.
CLAVE_ULTIMAS_OPCIONES = "agentes.ultimas_opciones"


#: Lo que «Nueva sesión» recuerda de la última vez con cada agente.
OPCIONES_RECORDADAS = ("modelo", "esfuerzo", "estilo")


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
			sizer=fila_carpeta, horizontal=True,
			ayuda=_("Carpeta del proyecto donde trabajará el agente. Escríbela, elige una reciente con las flechas o pulsa Examinar."))
		self.examinar = wx.Button(panel, label=_("E&xaminar..."))
		self.examinar.SetHelpText(_("Abre el cuadro para elegir la carpeta de trabajo."))
		fila_carpeta.Add(self.examinar, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 6)
		raiz.Add(fila_carpeta, 0, wx.EXPAND)

		self.aislada = wx.CheckBox(panel, label=_("Trabajar en una copia a&islada (rama de git nueva)"))
		self.aislada.SetHelpText(_(
			"Si la carpeta es un repositorio de git, el agente trabaja en una copia aparte con su propia rama. "
			"Tu copia de trabajo no se toca hasta que decidas unir los cambios."
		))
		raiz.Add(self.aislada, 0, wx.ALL, 5)
		_e, self.rama = crear_campo(panel, _("Nombre de la &rama:"), wx.TextCtrl, value="ctrlia/" + wx.DateTime.Now().Format("%Y%m%d-%H%M"),
			sizer=raiz, ayuda=_("Nombre de la rama nueva para la copia aislada."))
		self.rama.Enable(False)

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
		self.carpeta.Bind(wx.EVT_COMBOBOX, lambda _e: self._proponer_modo())
		self.carpeta.Bind(wx.EVT_KILL_FOCUS, self._al_salir_de_carpeta)
		self.examinar.Bind(wx.EVT_BUTTON, lambda _e: self._examinar())
		self.aislada.Bind(wx.EVT_CHECKBOX, lambda _e: self.rama.Enable(self.aislada.GetValue()))
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		self._al_cambiar_agente()
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
			self.carpeta.SetHelpText(_("Carpeta del proyecto donde trabajará el agente. Escríbela, elige una reciente con las flechas o pulsa Examinar."))
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
		self.examinar.Enable(not remota)
		self.aislada.Enable(not remota)
		if remota:
			self.aislada.SetValue(False)
			self.rama.Enable(False)

	def _proveedor(self):
		return self.gestor.proveedores[self._ids[self.agente.GetSelection()]]

	@staticmethod
	def _llenar(control: wx.Choice, opciones: list[OpcionLista], elegido: str = "") -> None:
		"""Rellena un desplegable y elige ``elegido`` (o el primero si no está)."""
		control.Set([o.etiqueta for o in opciones])
		indice = next((i for i, o in enumerate(opciones) if o.id == elegido), 0)
		if opciones:
			control.SetSelection(indice)
		control.Enable(len(opciones) > 1)

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
		self._cargar_modelos_en_vivo(proveedor)

	def _modo_propuesto(self, proveedor) -> str:
		"""El modo de permisos inicial de la configuración del agente para la carpeta escrita.

		En otro equipo los archivos del agente están allí y no se leen: se propone el modo de siempre.
		"""
		if self.equipo_elegido() is not None:
			return proveedor.modo_de_serie()
		texto = self.carpeta.GetValue().strip().strip('"')
		carpeta = Path(texto) if texto and Path(texto).is_dir() else None
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

	def _aceptar(self) -> None:
		if self.equipo_elegido() is not None:
			self._aceptar_remota()
			return
		ruta = Path(self.carpeta.GetValue().strip().strip('"'))
		if not str(ruta) or not ruta.is_dir():
			mostrar_error(self, _("La carpeta de trabajo no existe. Escribe una carpeta válida o pulsa Examinar."), _("Nueva sesión"))
			enfocar(self.carpeta)
			return
		identificador = self._ids[self.agente.GetSelection()]
		estado = self.gestor.estado_proveedor(identificador)
		if estado is not None and not estado.disponible:
			mostrar_error(self, _(
				"{n} no está instalado en este equipo. Instálalo desde Herramientas, Agentes instalados, o elige otro agente."
			).format(n=self._proveedor().nombre), _("Nueva sesión"))
			enfocar(self.agente)
			return
		if self.aislada.GetValue():
			try:
				ruta = git.crear_worktree(ruta, self.rama.GetValue().strip())
			except git.ErrorGit as error:
				mostrar_error(self, str(error), _("Copia aislada"))
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
		recordar_opciones(self.config, identificador, modelo=ajustes.modelo, esfuerzo=ajustes.esfuerzo, estilo=estilo)
		indice_grupo = self.grupo.GetSelection()
		grupo = self._grupos[indice_grupo][0] if 0 <= indice_grupo < len(self._grupos) else None
		self.resultado = ResultadoNuevaSesion(identificador, ajustes, mensaje, grupo=grupo)
		self.EndModal(wx.ID_OK)
