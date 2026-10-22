"""Diálogo para responder a un agente: permisos, preguntas y planes.

- El foco empieza en el detalle (de solo lectura) para que el lector lea
  entero qué quiere hacer el agente antes de decidir.
- Un botón por cada respuesta posible, con su tecla Alt; Intro pulsa SIEMPRE
  la opción inofensiva (denegar, seguir planificando...). Así un Intro de más
  nunca autoriza nada.
- Escape o «Responder después» cierra sin contestar: el agente sigue
  esperando y la petición se puede retomar con Control+Mayúsculas+A.
"""

from __future__ import annotations

import logging

import wx

from app.services.agentes.base import ClaseOpcion, RespuestaPermiso, SolicitudPermiso, TipoSolicitud
from app.ui.accesibilidad.dialogos import enfocar, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.comun import _

log = logging.getLogger(__name__)

_OTRA = "__otra__"


def _con_acelerador(etiqueta: str, usadas: set[str]) -> str:
	"""Pone ``&`` en la primera letra libre de la etiqueta (para Alt+letra)."""
	for posicion, caracter in enumerate(etiqueta):
		letra = caracter.lower()
		if caracter.isalpha() and letra not in usadas:
			usadas.add(letra)
			return etiqueta[:posicion] + "&" + etiqueta[posicion:]
	return etiqueta


class DialogoSolicitud(wx.Dialog):
	"""Muestra una :class:`SolicitudPermiso` y recoge la decisión en ``self.respuesta``."""

	def __init__(self, padre: wx.Window | None, solicitud: SolicitudPermiso, sesion: str = "") -> None:
		super().__init__(padre, title=solicitud.titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.solicitud = solicitud
		self.respuesta: RespuestaPermiso | None = None
		self._preguntas: list[tuple[object, wx.Window, wx.TextCtrl | None]] = []
		self.descripciones: list[wx.TextCtrl] = []
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)

		self.contexto: wx.TextCtrl | None = None
		if solicitud.contexto.strip():
			# Lo que el agente escribió justo antes (un borrador, unas notas...): para
			# poder revisarlo entero antes de contestar.
			_etiqueta, self.contexto = crear_campo(
				panel, _("Lo que el agente &escribió antes:"), wx.TextCtrl, value=solicitud.contexto.strip(),
				style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
				ayuda=_("El texto que el agente escribió justo antes de preguntarte, por ejemplo un borrador que quiere "
					"que revises. Léelo con las flechas; también está en la lista de mensajes."),
				sizer=raiz,
			)
			self.contexto.SetMinSize(self.FromDIP(wx.Size(560, 160)))

		texto = solicitud.titulo
		if sesion:
			texto += "\n" + _("Sesión: {sesion}").format(sesion=sesion)
		cuerpo = solicitud.plan or solicitud.detalle
		if cuerpo:
			texto += "\n\n" + cuerpo
		_etiqueta, self.detalle = crear_campo(
			panel, _("&Detalle:"), wx.TextCtrl, value=texto, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			ayuda=_("Lo que el agente quiere hacer, completo. Léelo con las flechas antes de decidir."), sizer=raiz,
		)
		self.detalle.SetMinSize(self.FromDIP(wx.Size(560, 220)))

		if solicitud.tipo is TipoSolicitud.PREGUNTA:
			self._crear_preguntas(panel, raiz)

		self.mensaje: wx.TextCtrl | None = None
		if solicitud.tipo in (TipoSolicitud.HERRAMIENTA, TipoSolicitud.PLAN):
			etiqueta = _("&Mensaje para el agente (opcional):") if solicitud.tipo is TipoSolicitud.HERRAMIENTA \
				else _("&Qué cambiar del plan (opcional):")
			_etiqueta, self.mensaje = crear_campo(
				panel, etiqueta, wx.TextCtrl, style=wx.TE_MULTILINE,
				ayuda=_("Si deniegas o sigues planificando, el agente recibirá este texto como explicación."),
				sizer=raiz,
			)
			self.mensaje.SetMinSize(self.FromDIP(wx.Size(560, 60)))

		raiz.Add(self._crear_botones(panel), 0, wx.EXPAND | wx.ALL, 6)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Layout()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.contexto or self.detalle)

	# -- Construcción ---------------------------------------------------------------

	def _crear_preguntas(self, panel: wx.Panel, raiz: wx.BoxSizer) -> None:
		for numero, pregunta in enumerate(self.solicitud.preguntas, start=1):
			titulo = pregunta.encabezado or _("Pregunta {n}").format(n=numero)
			etiqueta = "{titulo}: {texto}".format(titulo=titulo, texto=pregunta.texto)
			opciones = [f"{t} — {d}" if d else t for t, d in pregunta.opciones]
			if pregunta.admite_otra:
				opciones.append(_("Otra respuesta (escríbela debajo)"))
			if opciones and pregunta.multiple:
				_e, control = crear_campo(panel, etiqueta, wx.CheckListBox, choices=opciones,
					ayuda=_("Marca con la barra espaciadora todas las respuestas que quieras."), sizer=raiz)
			elif opciones:
				_e, control = crear_campo(panel, etiqueta, wx.ListBox, choices=opciones, style=wx.LB_SINGLE,
					ayuda=_("Elige una respuesta con las flechas."), sizer=raiz)
				control.SetSelection(0)
			else:
				_e, control = crear_campo(panel, etiqueta, wx.TextCtrl, style=wx.TE_PASSWORD if pregunta.secreta else 0,
					ayuda=_("Escribe tu respuesta."), sizer=raiz)
			if opciones:
				control.SetMinSize(self.FromDIP(wx.Size(560, -1)))
				if any(d for _t, d in pregunta.opciones) or any(p.strip() for p in pregunta.vistas_previas):
					completas = list(opciones)
					for indice, previa in enumerate(pregunta.vistas_previas[:len(pregunta.opciones)]):
						if previa.strip():
							completas[indice] += "\n\n" + _("Vista previa:") + "\n" + previa.strip()
					self._crear_descripcion(panel, raiz, control, titulo, completas)
			otra = None
			if opciones and pregunta.admite_otra:
				_e, otra = crear_campo(panel, _("Tu otra respuesta a «{t}»:").format(t=titulo), wx.TextCtrl,
					ayuda=_("Solo se usa si eliges «Otra respuesta»."), sizer=raiz)
			self._preguntas.append((pregunta, control, otra))

	def _crear_descripcion(self, panel: wx.Panel, raiz: wx.BoxSizer, control: wx.ListBox, titulo: str,
			opciones: list[str]) -> None:
		"""Campo de solo lectura con la opción elegida entera: una descripción larga no cabe en la fila."""
		_e, descripcion = crear_campo(
			panel, _("Opción elegida en «{t}», completa:").format(t=titulo), wx.TextCtrl,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			ayuda=_("La opción en la que estás, con toda su descripción y, si el agente la incluye, su vista previa "
				"(un borrador, código...), aunque no quepa en la lista."), sizer=raiz,
		)
		descripcion.SetMinSize(self.FromDIP(wx.Size(560, 60)))
		self.descripciones.append(descripcion)

		def actualizar(_evento: wx.CommandEvent | None = None) -> None:
			indice = control.GetSelection()
			descripcion.ChangeValue(opciones[indice] if 0 <= indice < len(opciones) else "")
			if _evento is not None:
				_evento.Skip()

		control.Bind(wx.EVT_LISTBOX, actualizar)
		actualizar()

	def _crear_botones(self, panel: wx.Panel) -> wx.BoxSizer:
		fila = wx.WrapSizer(wx.HORIZONTAL)
		usadas = {"d", "m", "q", "e"}  # Detalle, Mensaje, «Qué cambiar» y «escribió antes» ya tienen esas teclas.
		segura = self.solicitud.opcion_segura()
		for opcion in self.solicitud.opciones:
			boton = wx.Button(panel, label=_con_acelerador(opcion.etiqueta, usadas))
			ayuda = opcion.ayuda or {
				ClaseOpcion.PERMITIR: _("Deja que el agente haga esto una sola vez."),
				ClaseOpcion.PERMITIR_SESION: _("Lo permite ahora y no vuelve a preguntar por lo mismo en esta sesión."),
				ClaseOpcion.PERMITIR_SIEMPRE: _("Lo permite ahora y en el futuro."),
				ClaseOpcion.DENEGAR: _("No lo permite; el agente sigue trabajando por otro camino."),
				ClaseOpcion.DENEGAR_SIEMPRE: _("No lo permite ni ahora ni en el futuro."),
				ClaseOpcion.CANCELAR: _("No lo permite y detiene el turno del agente."),
			}.get(opcion.clase, "")
			boton.SetHelpText(ayuda)
			boton.Bind(wx.EVT_BUTTON, lambda _e, o=opcion.id: self._elegir(o))
			fila.Add(boton, 0, wx.RIGHT | wx.BOTTOM, 6)
			if segura is not None and opcion.id == segura.id:
				boton.SetDefault()
		despues = wx.Button(panel, wx.ID_CANCEL, _con_acelerador(_("Responder después"), usadas))
		despues.SetHelpText(_("Cierra sin contestar. El agente sigue esperando; vuelve con Control+Mayúsculas+A."))
		fila.Add(despues, 0, wx.RIGHT | wx.BOTTOM, 6)
		return fila

	# -- Respuesta ------------------------------------------------------------------

	def _respuestas(self) -> dict[str, list[str]] | None:
		resultado: dict[str, list[str]] = {}
		for pregunta, control, otra in self._preguntas:
			opciones = [t for t, _d in pregunta.opciones]
			elegidas: list[str] = []
			if isinstance(control, wx.CheckListBox):
				indices = list(control.GetCheckedItems())
			elif isinstance(control, wx.ListBox):
				indices = [control.GetSelection()] if control.GetSelection() != wx.NOT_FOUND else []
			else:
				valor = control.GetValue().strip()
				indices = []
				if valor:
					elegidas.append(valor)
			for indice in indices:
				if indice < len(opciones):
					elegidas.append(opciones[indice])
				elif otra is not None and otra.GetValue().strip():
					elegidas.append(otra.GetValue().strip())
			if not elegidas:
				mostrar_mensaje(self, _("Falta responder a: {p}").format(p=pregunta.texto), _("Falta una respuesta"))
				enfocar(control)
				return None
			resultado[pregunta.id] = elegidas
		return resultado

	def _elegir(self, opcion: str) -> None:
		respuestas: dict[str, list[str]] = {}
		elegida = next((o for o in self.solicitud.opciones if o.id == opcion), None)
		if self.solicitud.tipo is TipoSolicitud.PREGUNTA and elegida is not None and elegida.clase is ClaseOpcion.PERMITIR:
			obtenidas = self._respuestas()
			if obtenidas is None:
				return
			respuestas = obtenidas
		mensaje = self.mensaje.GetValue().strip() if self.mensaje is not None else ""
		self.respuesta = RespuestaPermiso(opcion=opcion, mensaje=mensaje, respuestas=respuestas)
		self.EndModal(wx.ID_OK)


def pedir_respuesta(padre: wx.Window | None, solicitud: SolicitudPermiso, sesion: str = "") -> RespuestaPermiso | None:
	"""Abre el diálogo y devuelve la respuesta, o ``None`` si se deja para después."""
	dialogo = DialogoSolicitud(padre, solicitud, sesion)
	try:
		dialogo.ShowModal()
		return dialogo.respuesta
	finally:
		dialogo.Destroy()
