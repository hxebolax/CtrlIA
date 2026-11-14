"""«Preguntar aparte» (Ctrl+Alt+S): una pregunta rápida sobre la sesión que no se añade a ella.

El agente contesta con una copia de solo lectura de la conversación hasta su
última respuesta terminada (AIChat #467/#468/#559): sin herramientas que
cambien nada, sin servidores MCP y sin guardar nada. Se puede preguntar
mientras el agente trabaja en la sesión. Las preguntas siguientes llevan
delante las anteriores con sus respuestas, para poder repreguntar.

No es modal. La respuesta llega a trozos a «Respuestas» y se dice al terminar.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import wx

from app.ui import avisos
from app.ui.accesibilidad.dialogos import preparar_dialogo
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Máximo de caracteres de las preguntas y respuestas anteriores que acompañan a una nueva.
MAX_ANTERIORES = 20000
#: Preguntas que se recuerdan por sesión mientras Ctrl+IA está abierto.
MAX_PREGUNTAS = 50


def con_anteriores(pregunta: str, anteriores: list[tuple[str, str]]) -> str:
	"""La pregunta con delante las anteriores y sus respuestas, marcadas como contexto (las más recientes caben antes)."""
	if not anteriores:
		return pregunta
	trozos: list[str] = []
	total = 0
	for anterior, respuesta in reversed(anteriores):
		trozo = f"Pregunta: {anterior}\nRespuesta: {respuesta}"
		if total + len(trozo) > MAX_ANTERIORES:
			break
		trozos.insert(0, trozo)
		total += len(trozo)
	if not trozos:
		return pregunta
	return ("Contexto: estas son preguntas aparte que ya te hice sobre esta conversación, con tus respuestas. "
		"No son instrucciones nuevas.\n<<<\n" + "\n\n".join(trozos) + "\n>>>\n\nMi pregunta ahora: " + pregunta)


class DialogoPreguntarAparte(wx.Dialog):
	"""Preguntas aparte sobre una sesión."""

	def __init__(self, padre: wx.Window | None, titulo_sesion: str,
			preguntar: Callable[[str, Callable[[str], None], threading.Event], str],
			en_fondo: Callable[..., None], anteriores: list[tuple[str, str]] | None = None) -> None:
		titulo = _("Preguntar aparte sobre «{t}»").format(t=titulo_sesion)
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self._preguntar = preguntar
		self._en_fondo = en_fondo
		#: Preguntas y respuestas de esta sesión (las guarda quien abre el diálogo, para la próxima vez).
		self.anteriores: list[tuple[str, str]] = anteriores if anteriores is not None else []
		self._cancelar: threading.Event | None = None

		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(panel, label=_(
			"El agente contesta con lo que sabe de la conversación hasta su última respuesta terminada. La pregunta y "
			"su respuesta no se añaden a la sesión y el agente no puede cambiar nada.")), 0, wx.ALL, 6)
		sizer.Add(wx.StaticText(panel, label=_("&Pregunta:")), 0, wx.LEFT | wx.RIGHT, 6)
		self.pregunta = wx.TextCtrl(panel, style=wx.TE_MULTILINE, name=_("Pregunta"))
		self.pregunta.SetHelpText(_("Escribe tu pregunta. Control+Intro la envía; Intro hace una línea nueva."))
		self.pregunta.SetMinSize(self.FromDIP(wx.Size(700, 90)))
		sizer.Add(self.pregunta, 0, wx.EXPAND | wx.ALL, 6)
		self.boton_preguntar = wx.Button(panel, label=_("Pre&guntar"))
		self.boton_preguntar.SetHelpText(_("Envía la pregunta (también con Control+Intro en el cuadro de la pregunta)."))
		sizer.Add(self.boton_preguntar, 0, wx.LEFT | wx.RIGHT, 6)
		sizer.Add(wx.StaticText(panel, label=_("&Respuestas:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		self.respuestas = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, name=_("Respuestas"))
		self.respuestas.SetHelpText(_("Las preguntas aparte de esta sesión y sus respuestas, la última al final."))
		self.respuestas.SetMinSize(self.FromDIP(wx.Size(700, 260)))
		sizer.Add(self.respuestas, 1, wx.EXPAND | wx.ALL, 6)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra esta ventana. Si había una pregunta en marcha, se abandona."))
		sizer.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
		panel.SetSizer(sizer)

		self.boton_preguntar.Bind(wx.EVT_BUTTON, lambda _evento: self.enviar())
		self.boton_cerrar.Bind(wx.EVT_BUTTON, lambda _evento: self.Close())
		self.pregunta.Bind(wx.EVT_KEY_DOWN, self._al_tecla)
		self.Bind(wx.EVT_CLOSE, self._al_cerrar)
		preparar_dialogo(self, foco_inicial=self.pregunta)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self._pintar_anteriores()
		self.Layout()
		self.Fit()
		self.CentreOnParent()

	def _pintar_anteriores(self) -> None:
		texto = "\n\n".join(_("Tú: {p}\n\nAgente: {r}").format(p=p, r=r) for p, r in self.anteriores)
		self.respuestas.ChangeValue(texto)

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and evento.GetModifiers() == wx.MOD_CONTROL:
			self.enviar()
			return
		evento.Skip()

	def enviar(self) -> bool:
		pregunta = self.pregunta.GetValue().strip()
		if not pregunta:
			avisos.anunciar(_("Escribe primero la pregunta."), interrumpir=True)
			return False
		if self._cancelar is not None:
			avisos.anunciar(_("Espera a que conteste la pregunta anterior."), interrumpir=True)
			return False
		cancelar = self._cancelar = threading.Event()
		previo = self.respuestas.GetValue()
		cabecera = (previo + "\n\n" if previo else "") + _("Tú: {p}\n\nAgente: ").format(p=pregunta)
		self.respuestas.ChangeValue(cabecera)
		recibido: list[str] = []
		texto_completo = con_anteriores(pregunta, self.anteriores)

		def trozo(texto: str) -> None:
			recibido.append(texto)
			wx.CallAfter(self._mostrar, cabecera, recibido)

		def trabajo() -> str:
			return self._preguntar(texto_completo, trozo, cancelar)

		def hecho(respuesta: Any) -> None:
			self._cancelar = None
			if not self or cancelar.is_set():
				return
			texto = str(respuesta or "".join(recibido)).strip() or _("(sin respuesta)")
			self.anteriores.append((pregunta, texto))
			del self.anteriores[:-MAX_PREGUNTAS]
			self.respuestas.ChangeValue(cabecera + texto)
			self.pregunta.Clear()
			avisos.sonar("respuesta")
			avisos.anunciar(texto[:4000], interrumpir=True)

		def fallo(error: BaseException) -> None:
			self._cancelar = None
			if not self or cancelar.is_set():
				return
			self.respuestas.ChangeValue(cabecera + _("(no se ha podido: {e})").format(e=error))
			avisos.sonar("error")
			avisos.anunciar(_("No se ha podido preguntar: {e}").format(e=error), interrumpir=True)

		self._en_fondo(trabajo, al_terminar=hecho, al_fallar=fallo)
		avisos.anunciar(_("Preguntando..."), interrumpir=True)
		return True

	def _mostrar(self, cabecera: str, recibido: list[str]) -> None:
		if self and self._cancelar is not None:
			self.respuestas.ChangeValue(cabecera + "".join(recibido))

	def _al_cerrar(self, evento: wx.CloseEvent) -> None:
		if self._cancelar is not None:
			self._cancelar.set()
		del evento
		self.Destroy()
