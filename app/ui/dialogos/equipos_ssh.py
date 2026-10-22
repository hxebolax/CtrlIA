"""«Equipos por SSH»: otros equipos (Mac o Linux) donde trabajan los agentes.

Cada equipo tiene un nombre, el destino de SSH (``usuario@equipo`` o un nombre
del archivo ``.ssh/config``), el puerto, el sistema y, si hace falta, la ruta
de cada agente y variables propias. «Comprobar» (F5) prueba la conexión sin
contraseña, qué agentes hay con su versión y si tienen la sesión iniciada; el
resultado queda en un campo de solo lectura. «Abrir una consola en el equipo»
abre ``ssh -t`` en una consola de Windows para aceptar la huella del equipo la
primera vez o iniciar sesión en los agentes.

Claude Code y Codex son estables por SSH; Gemini CLI, Antigravity CLI, los
agentes ACP personalizados y el túnel de Ollama son experimentales.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from typing import Any

import wx

from app.services.agentes import procesos, remoto
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import (
	confirmar,
	crear_botonera,
	enfocar,
	mostrar_error,
	preparar_dialogo,
)
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

TITULO = _("Equipos por SSH")

#: Agentes con campo de ruta propio: (id, etiqueta, ayuda).
_RUTAS = (
	("claude", _("&Ruta de Claude Code (opcional):"), _("Déjalo vacío si «claude» funciona en una terminal del otro equipo. Si no, escribe su ruta completa, por ejemplo /Users/ana/.local/bin/claude.")),
	("codex", _("Ruta de C&odex (opcional):"), _("Déjalo vacío si «codex» funciona en una terminal del otro equipo. Si no, escribe su ruta completa, por ejemplo /opt/homebrew/bin/codex.")),
	("gemini", _("Ruta de &Gemini CLI (opcional, experimental):"), _("Déjalo vacío si «gemini» funciona en una terminal del otro equipo. Gemini CLI por SSH es experimental.")),
	("antigravity", _("Ruta de Antigravity CL&I, agy (opcional, experimental):"), _("Déjalo vacío si «agy» funciona en una terminal del otro equipo. Antigravity CLI por SSH es experimental.")),
)


class DialogoEditarEquipo(wx.Dialog):
	"""Datos de un equipo; el resultado queda en ``self.equipo``."""

	def __init__(self, padre: wx.Window | None, equipo: remoto.EquipoSSH | None = None, ids_usados: tuple[str, ...] = ()) -> None:
		super().__init__(padre, title=_("Equipo por SSH"), style=wx.DEFAULT_DIALOG_STYLE)
		self._original = equipo
		self._ids_usados = ids_usados
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.nombre = crear_campo(self, _("&Nombre:"), wx.TextCtrl, value=equipo.nombre if equipo else "", sizer=raiz,
			ayuda=_("Cómo aparecerá el equipo en «Nueva sesión» y en la lista de sesiones, por ejemplo «Mac del estudio»."))
		_e, self.destino = crear_campo(self, _("&Destino:"), wx.TextCtrl, value=equipo.destino if equipo else "", sizer=raiz,
			ayuda=_("usuario@equipo (por ejemplo ana@192.168.1.40 o ana@mac-estudio.local) o el nombre de un equipo de tu "
				"archivo .ssh\\config. Hace falta una clave SSH: Ctrl+IA nunca usa contraseñas."))
		_e, self.puerto = crear_campo(self, _("&Puerto:"), wx.SpinCtrl, min=1, max=65535,
			initial=equipo.puerto if equipo else 22, sizer=raiz, expandir=False,
			ayuda=_("Casi siempre 22. Cámbialo solo si el otro equipo usa otro puerto para SSH."))
		sistemas = [nombre for _clave, nombre in remoto.SISTEMAS]
		_e, self.sistema = crear_campo(self, _("&Sistema del otro equipo:"), wx.Choice, choices=sistemas, sizer=raiz,
			ayuda=_("macOS o Linux. Sirve para darte las explicaciones adecuadas."))
		claves = [clave for clave, _nombre in remoto.SISTEMAS]
		self.sistema.SetSelection(claves.index(equipo.sistema) if equipo and equipo.sistema in claves else 0)
		self.rutas: dict[str, wx.TextCtrl] = {}
		for agente, etiqueta, ayuda in _RUTAS:
			_e, campo = crear_campo(self, etiqueta, wx.TextCtrl, value=equipo.rutas.get(agente, "") if equipo else "",
				sizer=raiz, ayuda=ayuda)
			self.rutas[agente] = campo
		texto_variables = "\n".join(f"{k}={v}" for k, v in (equipo.variables if equipo else {}).items())
		_e, self.variables = crear_campo(self, _("&Variables (una por línea, NOMBRE=valor; opcional):"), wx.TextCtrl,
			value=texto_variables, style=wx.TE_MULTILINE, sizer=raiz,
			ayuda=_("Variables que se ponen al arrancar los agentes en ese equipo, por ejemplo "
				"GEMINI_FORCE_FILE_STORAGE=true. Las claves de API de pago se quitan siempre."))
		self.variables.SetMinSize(self.FromDIP(wx.Size(420, 70)))
		self.ollama = wx.CheckBox(self, label=_("&Usar el Ollama de este equipo por un túnel SSH (experimental)"))
		self.ollama.SetValue(bool(equipo and equipo.ollama))
		self.ollama.SetHelpText(_("Ctrl+IA abre un túnel cifrado hasta el Ollama del otro equipo y lo usa en lugar del de "
			"Windows. Experimental. Solo puede haber un equipo con esta casilla marcada."))
		raiz.Add(self.ollama, 0, wx.ALL, 6)
		self.historial = wx.CheckBox(self, label=_("&Mostrar en la lista las sesiones que ya tiene este equipo"))
		self.historial.SetValue(equipo.historial if equipo else True)
		self.historial.SetHelpText(_("Las sesiones de Claude Code (terminal y app de escritorio) y los hilos de Codex "
			"de ese equipo aparecen en la lista de sesiones con su nombre en la columna Equipo. Se leen por SSH cada "
			"poco, sin cambiar nada allí. Desmárcala si el equipo suele estar apagado o lejos."))
		raiz.Add(self.historial, 0, wx.ALL, 6)
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.Bind(wx.EVT_BUTTON, lambda _e: self._aceptar(), id=wx.ID_OK)
		self.equipo: remoto.EquipoSSH | None = None
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.nombre)

	def _aceptar(self) -> None:
		nombre = self.nombre.GetValue().strip()
		try:
			variables = remoto.leer_variables(self.variables.GetValue())
		except remoto.ErrorRemoto as error:
			mostrar_error(self, str(error), _("Equipo por SSH"))
			enfocar(self.variables)
			return
		ident = self._original.id if self._original else remoto.nuevo_id(nombre, self._ids_usados)
		equipo = remoto.EquipoSSH(
			id=ident,
			nombre=nombre,
			destino=self.destino.GetValue().strip(),
			puerto=int(self.puerto.GetValue()),
			sistema=remoto.SISTEMAS[max(0, self.sistema.GetSelection())][0],
			rutas={agente: campo.GetValue().strip() for agente, campo in self.rutas.items() if campo.GetValue().strip()},
			variables=variables,
			ollama=self.ollama.GetValue(),
			historial=self.historial.GetValue(),
		)
		error = remoto.validar_equipo(equipo)
		if error is not None:
			mostrar_error(self, error, _("Equipo por SSH"))
			enfocar(self.nombre if not nombre else self.destino)
			return
		self.equipo = equipo
		self.EndModal(wx.ID_OK)


class DialogoEquiposSSH(wx.Dialog):
	"""Lista de equipos; el resultado queda en ``self.equipos`` (lista de diccionarios)."""

	def __init__(self, padre: wx.Window | None, equipos: list[dict[str, Any]]) -> None:
		super().__init__(padre, title=TITULO, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.equipos = [remoto.EquipoSSH.desde_dict(e) for e in equipos if isinstance(e, dict)]
		self.equipos = [e for e in self.equipos if e.id]
		self._comprobando = False
		self._generacion = 0
		raiz = wx.BoxSizer(wx.VERTICAL)
		explicacion = wx.StaticText(self, label=_(
			"Añade aquí un Mac o un equipo Linux al que entras por SSH con una clave. Después, en «Nueva sesión», "
			"elige ese equipo y los agentes trabajarán allí. Las sesiones de Claude Code y Codex que ya tenga el equipo "
			"aparecen en la lista con su nombre en la columna Equipo. Claude Code y Codex funcionan de forma estable; "
			"Gemini CLI, Antigravity CLI, los agentes ACP personalizados y Ollama son experimentales."))
		explicacion.Wrap(self.FromDIP(560))
		raiz.Add(explicacion, 0, wx.ALL, 6)
		raiz.Add(wx.StaticText(self, label=_("&Equipos:")), 0, wx.LEFT | wx.RIGHT, 6)
		self.lista = ListaAccesible(self, _("Equipos"), [(_("Nombre"), 180), (_("Destino"), 220), (_("Sistema"), 90), (_("Ollama"), 80)])
		self.lista.SetHelpText(_("Equipos guardados. Intro o F2 los edita, Suprimir los quita, F5 los comprueba. "
			"La tecla Aplicaciones muestra todas las acciones."))
		self.lista.SetMinSize(self.FromDIP(wx.Size(580, 140)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_anadir = wx.Button(self, label=_("Añadir &nuevo..."))
		self.boton_anadir.SetHelpText(_("Añade un equipo indicando su nombre y su destino de SSH."))
		self.boton_comprobar = wx.Button(self, label=_("Co&mprobar (F5)"))
		self.boton_comprobar.SetHelpText(_("Prueba la conexión sin contraseña, qué agentes hay en el equipo y si tienen la sesión "
			"iniciada. Puede tardar hasta un minuto."))
		self.boton_consola = wx.Button(self, label=_("Abrir una c&onsola en el equipo"))
		self.boton_consola.SetHelpText(_("Abre una consola de Windows conectada al equipo. Sirve para aceptar su huella la "
			"primera vez (escribe yes) y para iniciar sesión en los agentes."))
		self.boton_quitar = wx.Button(self, label=_("&Quitar"))
		self.boton_quitar.SetHelpText(_("Quita el equipo elegido. Sus sesiones siguen en la lista, pero no se pueden continuar "
			"hasta que vuelvas a añadir un equipo con el mismo nombre."))
		for boton in (self.boton_anadir, self.boton_comprobar, self.boton_consola, self.boton_quitar):
			fila.Add(boton, 0, wx.RIGHT, 6)
		raiz.Add(fila, 0, wx.ALL, 6)
		_e, self.resultado = crear_campo(self, _("&Resultado de la comprobación:"), wx.TextCtrl,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, sizer=raiz,
			ayuda=_("Lo que ha encontrado «Comprobar», con la solución de cada problema."))
		self.resultado.SetMinSize(self.FromDIP(wx.Size(580, 140)))
		raiz.Add(crear_botonera(self, wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 6)
		self.SetSizerAndFit(raiz)
		self.boton_anadir.Bind(wx.EVT_BUTTON, lambda _e: self.anadir())
		self.boton_comprobar.Bind(wx.EVT_BUTTON, lambda _e: self.comprobar())
		self.boton_consola.Bind(wx.EVT_BUTTON, lambda _e: self.abrir_consola())
		self.boton_quitar.Bind(wx.EVT_BUTTON, lambda _e: self.quitar())
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.editar())
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		vincular_menu_contextual(self.lista, self._menu)
		self._rellenar()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.lista if self.equipos else self.boton_anadir)

	# -- Lista ----------------------------------------------------------------

	def _rellenar(self, enfocar_en: int = 0) -> None:
		sistemas = dict(remoto.SISTEMAS)
		self.lista.reemplazar_filas([(e.nombre, e.destino, sistemas.get(e.sistema, e.sistema), _("Sí") if e.ollama else _("No"))
			for e in self.equipos], conservar_foco=False)
		if self.equipos:
			self.lista.enfocar_fila(min(max(enfocar_en, 0), len(self.equipos) - 1), anunciar=False)
		hay = bool(self.equipos)
		for boton in (self.boton_comprobar, self.boton_consola, self.boton_quitar):
			boton.Enable(hay)

	def seleccionado(self) -> remoto.EquipoSSH | None:
		indice = self.lista.fila_actual()
		return self.equipos[indice] if 0 <= indice < len(self.equipos) else None

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		# Los atajos los documentan las etiquetas del menú contextual (_menu).
		tecla = evento.GetKeyCode()
		if tecla == wx.WXK_F5 and not evento.HasAnyModifiers():
			self.comprobar()
			return
		if self.FindFocus() is self.lista and not evento.HasAnyModifiers():
			if tecla == wx.WXK_DELETE:
				self.quitar()
				return
			if tecla == wx.WXK_F2:
				self.editar()
				return
		evento.Skip()

	def _menu(self) -> wx.Menu:
		menu = wx.Menu()
		acciones = [(_("&Editar...\tF2"), self.editar), (_("Co&mprobar\tF5"), self.comprobar),
			(_("Abrir una c&onsola en el equipo"), self.abrir_consola), (_("&Quitar\tSupr"), self.quitar),
			(_("Añadir &nuevo..."), self.anadir)]
		for etiqueta, accion in acciones:
			item = menu.Append(wx.ID_ANY, etiqueta)
			self.lista.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), item)
		return menu

	def _poner(self, equipo: remoto.EquipoSSH, indice: int | None = None) -> int:
		"""Guarda el equipo en la lista; si marca Ollama, lo desmarca en los demás (solo uno a la vez)."""
		if equipo.ollama:
			self.equipos = [dataclasses.replace(e, ollama=False) if e.ollama and e.id != equipo.id else e
				for e in self.equipos]
		if indice is None:
			self.equipos.append(equipo)
			return len(self.equipos) - 1
		self.equipos[indice] = equipo
		return indice

	def anadir(self) -> None:
		dialogo = DialogoEditarEquipo(self, ids_usados=tuple(e.id for e in self.equipos))
		try:
			if dialogo.ShowModal() != wx.ID_OK or dialogo.equipo is None:
				return
			indice = self._poner(dialogo.equipo)
		finally:
			dialogo.Destroy()
		self._rellenar(indice)
		if self.equipos[indice].ollama:
			voz.hablar(_("Ollama por túnel SSH es experimental. Si falla, desmarca la casilla para volver al Ollama de Windows."))

	def editar(self) -> None:
		indice = self.lista.fila_actual()
		if not 0 <= indice < len(self.equipos):
			return
		dialogo = DialogoEditarEquipo(self, self.equipos[indice], tuple(e.id for e in self.equipos))
		try:
			if dialogo.ShowModal() != wx.ID_OK or dialogo.equipo is None:
				return
			self._poner(dialogo.equipo, indice)
		finally:
			dialogo.Destroy()
		self._rellenar(indice)

	def quitar(self) -> None:
		indice = self.lista.fila_actual()
		if not 0 <= indice < len(self.equipos):
			return
		equipo = self.equipos[indice]
		pregunta = _("¿Quitar el equipo «{n}»? Sus sesiones siguen en la lista, pero no se podrán continuar hasta que "
			"vuelvas a añadir un equipo con el mismo nombre.").format(n=equipo.nombre)
		if confirmar(self, pregunta, _("Quitar equipo")) != wx.ID_YES:
			return
		del self.equipos[indice]
		self._rellenar(indice)

	# -- Acciones con el equipo -----------------------------------------------

	def abrir_consola(self) -> None:
		equipo = self.seleccionado()
		if equipo is None:
			return
		try:
			orden = remoto.orden_terminal(equipo)
		except remoto.ErrorRemoto as error:
			mostrar_error(self, str(error), TITULO)
			return
		if procesos.abrir_en_consola(orden):
			voz.hablar(_("Consola abierta. Si pregunta por la huella del equipo, escribe yes. Al terminar, escribe exit."))
		else:
			mostrar_error(self, _("No se pudo abrir la consola."), TITULO)

	def comprobar(self) -> None:
		equipo = self.seleccionado()
		if equipo is None:
			return
		if self._comprobando:
			voz.hablar(_("Ya se está comprobando"))
			return
		self._comprobando = True
		self._generacion += 1
		self.boton_comprobar.Disable()
		self.resultado.SetValue(_("Comprobando «{n}»… Puede tardar hasta un minuto.").format(n=equipo.nombre))
		voz.hablar(_("Comprobando {n}").format(n=equipo.nombre))
		agentes = ["claude", "codex", "gemini", "antigravity"] + (["ollama"] if equipo.ollama else [])
		threading.Thread(target=self._comprobar_en_fondo, args=(equipo, agentes, self._generacion),
			name="comprobar-ssh", daemon=True).start()

	def _comprobar_en_fondo(self, equipo: remoto.EquipoSSH, agentes: list[str], generacion: int) -> None:
		try:
			resultados = remoto.comprobar(equipo, agentes)
		except Exception as error:  # noqa: BLE001 - el fallo se muestra en el campo de resultado
			log.exception("Falló la comprobación del equipo %s", equipo.nombre)
			resultados = [remoto.Resultado(_("Comprobación"), False, str(error))]
		try:
			wx.CallAfter(self._mostrar_resultado, equipo, resultados, generacion)
		except (RuntimeError, AssertionError, wx.PyAssertionError):
			log.debug("No se pudo entregar el resultado de la comprobación", exc_info=True)

	def _mostrar_resultado(self, equipo: remoto.EquipoSSH, resultados: list[remoto.Resultado], generacion: int) -> None:
		try:
			if not self or self.IsBeingDeleted() or generacion != self._generacion:
				return
		except RuntimeError:
			return
		self._comprobando = False
		self.boton_comprobar.Enable(bool(self.equipos))
		self.resultado.SetValue(texto_resultados(equipo, resultados))
		fallos = sum(1 for r in resultados if not r.correcto)
		if fallos:
			voz.hablar(_("Comprobación terminada con {n} problemas. Los tienes en el campo de resultado.").format(n=fallos))
		else:
			voz.hablar(_("Comprobación terminada: todo correcto."))

	def resultado_final(self) -> list[dict[str, Any]]:
		return [e.a_dict() for e in self.equipos]


def texto_resultados(equipo: remoto.EquipoSSH, resultados: list[remoto.Resultado]) -> str:
	"""Una línea por prueba: «Correcto» o «Problema», el nombre y el detalle."""
	lineas = [_("Comprobación de «{n}» ({d}):").format(n=equipo.nombre, d=equipo.destino)]
	for resultado in resultados:
		estado = _("Correcto") if resultado.correcto else _("Problema")
		experimental = ""
		if resultado.nombre in ("Gemini CLI", "Antigravity CLI", "Ollama"):
			experimental = _(" (experimental)")
		lineas.append(f"{estado}. {resultado.nombre}{experimental}: {resultado.detalle}")
	return "\n".join(lineas)
