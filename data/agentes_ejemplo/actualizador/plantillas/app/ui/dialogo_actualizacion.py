"""Interfaz accesible del actualizador: diálogo, comprobación manual y periódica.

- :class:`DialogoActualizacion`: versión instalada y nueva, novedades en un cuadro
  de texto de solo lectura (navegable con el lector de pantalla), botones
  «&Actualizar ahora», «&Más tarde» y «Omitir esta &versión», y progreso por
  fases e hitos (no cada byte: el lector se saturaría).
- :func:`anadir_menu_ayuda` y :func:`comprobar_manualmente`: «Buscar
  actualizaciones...» en el menú Ayuda.
- :class:`ComprobadorPeriodico`: comprobación en segundo plano cada N horas.
- :func:`al_arrancar`: lee el resultado de la actualización anterior y confirma el
  arranque (marcador de salud + anti-rollback) unos segundos después de abrir.

La red y el disco van SIEMPRE en hilos; los controles solo se tocan desde el hilo
principal con ``wx.CallAfter``. ``anunciar`` es opcional: una función que reciba
un texto y lo diga por voz (por ejemplo con accessible_output2).

PLANTILLA del agente «actualizador».
"""

from __future__ import annotations

import builtins
import logging
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone

import wx

from app.services import actualizador

log = logging.getLogger(__name__)

Anunciar = Callable[[str], None]


def _(texto: str) -> str:
	"""Traduce con el ``_`` de gettext si está instalado en ``builtins``."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def mostrar_mensaje(padre: wx.Window | None, mensaje: str, titulo: str, icono: int = wx.ICON_INFORMATION) -> None:
	"""Mensaje modal con el botón «Aceptar» en español.

	Usa los diálogos accesibles del proyecto (``app/ui/accesibilidad/dialogos.py``)
	si existen; si no, un ``wx.MessageDialog`` con el botón puesto a mano. Nunca
	``wx.MessageBox``: no deja cambiar sus botones y, sin el catálogo de wx
	cargado, salen en inglés. La importación es diferida para que este módulo
	funcione también en un proyecto sin ese archivo.
	"""
	try:
		from app.ui.accesibilidad import dialogos
	except ImportError:
		dialogos = None
	if dialogos is not None:
		if icono == wx.ICON_ERROR:
			dialogos.mostrar_error(padre, mensaje, titulo)
		else:
			dialogos.mostrar_mensaje(padre, mensaje, titulo, icono=icono)
		return
	dialogo = wx.MessageDialog(padre, mensaje, titulo, wx.OK | icono)
	dialogo.SetOKLabel(_("&Aceptar"))
	try:
		dialogo.ShowModal()
	finally:
		dialogo.Destroy()


_NOMBRES_FASE = {
	"descarga": "Descargando",
	"verificacion": "Verificando el paquete",
	"extraccion": "Descomprimiendo",
	"preparacion": "Preparando la instalación",
}
# Porcentajes de la descarga que se anuncian por voz (hitos, no cada cambio).
_HITOS_VOZ = (25, 50, 75, 100)


def _decir(anunciar: Anunciar | None, texto: str) -> None:
	if anunciar is None:
		return
	try:
		anunciar(texto)
	except Exception:  # noqa: BLE001 (la voz es opcional: un fallo nunca rompe la interfaz)
		log.debug("No se pudo anunciar por voz: %s", texto, exc_info=True)


def _cerrar_aplicacion() -> None:
	"""Cierre normal de la ventana principal (se guardan preferencias y trabajo)."""
	aplicacion = wx.GetApp()
	ventana = aplicacion.GetTopWindow() if aplicacion else None
	if ventana:
		ventana.Close()


class DialogoActualizacion(wx.Dialog):
	"""Ofrece la versión nueva y, si el usuario acepta, la descarga y la instala."""

	RESULTADO_ACTUALIZAR = "actualizar"
	RESULTADO_MAS_TARDE = "mas_tarde"
	RESULTADO_OMITIR = "omitir"

	def __init__(
		self,
		padre: wx.Window | None,
		config: actualizador.Configuracion,
		info: actualizador.InfoActualizacion,
		anunciar: Anunciar | None = None,
		al_cerrar_app: Callable[[], None] | None = None,
	) -> None:
		super().__init__(padre, title=_("Actualización disponible"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.config = config
		self.info = info
		self.resultado = self.RESULTADO_MAS_TARDE
		self._anunciar = anunciar
		self._al_cerrar_app = al_cerrar_app or _cerrar_aplicacion
		self._cancelar = threading.Event()
		self._hilo: threading.Thread | None = None
		self._fase = ""
		self._ultimo_hito = -1
		self._ultimo_hito_voz = 0
		self._crear_controles()
		self.CentreOnParent()
		wx.CallAfter(self._enfocar_inicial)

	# ── Construcción ─────────────────────────────────────────────────────────

	def _crear_controles(self) -> None:
		info, config = self.info, self.config
		principal = wx.BoxSizer(wx.VERTICAL)
		fecha = _(", publicada el %s") % info.fecha if info.fecha else ""
		self.resumen = wx.StaticText(
			self,
			label=_(
				"Hay una versión nueva de %(app)s.\nVersión instalada: %(actual)s.\nVersión nueva: %(nueva)s%(fecha)s."
			)
			% {"app": config.nombre_app, "actual": config.version_actual, "nueva": info.version, "fecha": fecha},
		)
		principal.Add(self.resumen, 0, wx.ALL | wx.EXPAND, 10)

		# La etiqueta va justo antes del cuadro: así el lector la usa como nombre.
		principal.Add(wx.StaticText(self, label=_("&Novedades:")), 0, wx.LEFT | wx.RIGHT, 10)
		self.novedades = wx.TextCtrl(
			self,
			value=info.novedades or _("El servidor no ha publicado novedades para esta versión."),
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			size=(520, 220),
		)
		self.novedades.SetName(_("Novedades"))
		principal.Add(self.novedades, 1, wx.ALL | wx.EXPAND, 10)

		self.etiqueta_estado = wx.StaticText(self, label=_("&Estado:"))
		self.estado = wx.TextCtrl(self, style=wx.TE_READONLY)
		self.estado.SetName(_("Estado"))
		self.barra = wx.Gauge(self, range=100)
		self.barra.SetName(_("Progreso de la actualización"))
		principal.Add(self.etiqueta_estado, 0, wx.LEFT | wx.RIGHT, 10)
		principal.Add(self.estado, 0, wx.ALL | wx.EXPAND, 10)
		principal.Add(self.barra, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_actualizar = wx.Button(self, label=_("&Actualizar ahora"))
		self.boton_omitir = wx.Button(self, label=_("Omitir esta &versión"))
		self.boton_mas_tarde = wx.Button(self, wx.ID_CANCEL, label=_("&Más tarde"))
		for boton in (self.boton_actualizar, self.boton_omitir, self.boton_mas_tarde):
			botones.Add(boton, 0, wx.ALL, 5)
		principal.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
		self.SetSizerAndFit(principal)

		self.boton_actualizar.SetDefault()
		self.SetEscapeId(wx.ID_CANCEL)
		self.boton_actualizar.Bind(wx.EVT_BUTTON, self._al_actualizar)
		self.boton_omitir.Bind(wx.EVT_BUTTON, self._al_omitir)
		self.boton_mas_tarde.Bind(wx.EVT_BUTTON, self._al_mas_tarde)
		self.Bind(wx.EVT_CLOSE, self._al_cerrar)

		self._mostrar_progreso(False)
		if not actualizador.puede_instalar(config):
			self.boton_actualizar.Disable()
			self._mostrar_progreso(True, barra=False)
			self.estado.SetValue(
				_("Esta copia se ejecuta desde el código fuente: actualícela con su sistema de control de versiones.")
			)

	def _mostrar_progreso(self, visible: bool, barra: bool = True) -> None:
		self.etiqueta_estado.Show(visible)
		self.estado.Show(visible)
		self.barra.Show(visible and barra)
		self.Layout()

	def _enfocar_inicial(self) -> None:
		"""Foco en «Actualizar ahora»: el lector lee antes el resumen del diálogo."""
		if not self:
			return
		(self.boton_actualizar if self.boton_actualizar.IsEnabled() else self.novedades).SetFocus()

	def _terminar(self, resultado: str, codigo: int) -> None:
		self.resultado = resultado
		if self.IsModal():
			self.EndModal(codigo)
		else:
			self.SetReturnCode(codigo)
			self.Hide()

	# ── Botones ──────────────────────────────────────────────────────────────

	def _al_mas_tarde(self, _evento: wx.CommandEvent | None) -> None:
		if self._trabajando():
			self._cancelar.set()
			self._poner_estado(_("Cancelando la descarga..."))
			return
		self._terminar(self.RESULTADO_MAS_TARDE, wx.ID_CANCEL)

	def _al_cerrar(self, evento: wx.CloseEvent) -> None:
		if self._trabajando() and evento.CanVeto():
			self._cancelar.set()
			evento.Veto()
			return
		self._terminar(self.RESULTADO_MAS_TARDE, wx.ID_CANCEL)

	def _al_omitir(self, _evento: wx.CommandEvent | None) -> None:
		actualizador.omitir_version(self.config, self.info.version)
		self._terminar(self.RESULTADO_OMITIR, wx.ID_CANCEL)

	def _al_actualizar(self, _evento: wx.CommandEvent | None) -> None:
		if self._trabajando():
			return
		self._cancelar.clear()
		self._fase = ""
		self._ultimo_hito = -1
		self._ultimo_hito_voz = 0
		self.boton_actualizar.Disable()
		self.boton_omitir.Disable()
		self.boton_mas_tarde.SetLabel(_("&Cancelar"))
		self._mostrar_progreso(True)
		self.barra.SetValue(0)
		self._poner_estado(_("Iniciando la descarga..."), voz=True)
		self.boton_mas_tarde.SetFocus()  # el único botón útil mientras se descarga
		self._hilo = threading.Thread(target=self._trabajo, name="actualizador", daemon=True)
		self._hilo.start()

	def _trabajando(self) -> bool:
		return self._hilo is not None and self._hilo.is_alive()

	# ── Hilo de trabajo (nunca toca controles directamente) ─────────────────

	def _trabajo(self) -> None:
		try:
			ruta_bat = actualizador.descargar_y_preparar(
				self.config, self.info, progreso=self._progreso_desde_hilo, cancelado=self._cancelar.is_set
			)
		except actualizador.ErrorCancelado as error:
			log.info("Descarga de la actualización cancelada por el usuario")
			wx.CallAfter(self._al_fallar, str(error), cancelado=True)
		except actualizador.ErrorActualizacion as error:
			log.warning("No se pudo preparar la actualización: %s", error)
			wx.CallAfter(self._al_fallar, str(error))
		except Exception:
			log.exception("Fallo inesperado al preparar la actualización")
			wx.CallAfter(
				self._al_fallar,
				_("Se produjo un error inesperado al preparar la actualización. Los detalles están en el registro."),
			)
		else:
			wx.CallAfter(self._al_preparada, ruta_bat)

	def _progreso_desde_hilo(self, fase: str, hecho: int, total: int) -> None:
		wx.CallAfter(self.actualizar_progreso, fase, hecho, total)

	# ── Hilo principal ───────────────────────────────────────────────────────

	def _poner_estado(self, texto: str, voz: bool = False) -> None:
		self.estado.SetValue(texto)
		if voz:
			_decir(self._anunciar, texto)

	def actualizar_progreso(self, fase: str, hecho: int, total: int) -> None:
		"""Refleja el avance: barra siempre; texto y voz solo en hitos y cambios de fase."""
		if not self:
			return
		porcentaje = min(100, int(hecho * 100 / total)) if total else 0
		self.barra.SetValue(porcentaje)
		nombre = _(_NOMBRES_FASE.get(fase, fase))
		if fase != self._fase:
			self._fase = fase
			self._ultimo_hito = -1
			self._ultimo_hito_voz = 0
			self._poner_estado(_("%s...") % nombre, voz=fase != "descarga")
		if fase == "descarga":
			# El texto cambia cada 10 %; la voz solo en los hitos (25, 50, 75, 100).
			decena = porcentaje // 10 * 10
			hito_voz = max((hito for hito in _HITOS_VOZ if porcentaje >= hito), default=0)
			decir = hito_voz > self._ultimo_hito_voz
			if decena > self._ultimo_hito or decir:
				self._ultimo_hito = decena
				if decir:
					self._ultimo_hito_voz = hito_voz
				megas = hecho / (1024 * 1024)
				total_megas = total / (1024 * 1024)
				mostrado = hito_voz if decir else decena
				texto = _("Descargando: %(p)d %% (%(h).1f de %(t).1f MB)") % {
					"p": mostrado,
					"h": megas,
					"t": total_megas,
				}
				self._poner_estado(texto, voz=decir)

	def _al_fallar(self, mensaje: str, cancelado: bool = False) -> None:
		if not self:
			return
		self.boton_actualizar.SetLabel(_("&Reintentar"))
		self.boton_actualizar.Enable()
		self.boton_omitir.Enable()
		self.boton_mas_tarde.SetLabel(_("&Más tarde"))
		self._poner_estado(mensaje, voz=True)
		self.barra.SetValue(0)
		# El foco va al estado para que el lector lea el error y se pueda releer.
		self.estado.SetFocus()
		if not cancelado:
			self.boton_actualizar.SetDefault()

	def _al_preparada(self, ruta_bat) -> None:
		if not self:
			return
		self.barra.SetValue(100)
		self._poner_estado(
			_("Actualización lista. La aplicación se cerrará y volverá a abrirse sola en unos segundos."), voz=True
		)
		try:
			actualizador.lanzar_sustitucion(ruta_bat)
		except OSError:
			log.exception("No se pudo lanzar el script de sustitución")
			self._al_fallar(
				_("No se pudo iniciar la instalación de la actualización. Los detalles están en el registro.")
			)
			return
		self._terminar(self.RESULTADO_ACTUALIZAR, wx.ID_OK)
		# Un momento de margen para que el lector de pantalla diga el aviso.
		wx.CallLater(1500, self._al_cerrar_app)


def mostrar_dialogo(
	padre: wx.Window | None,
	config: actualizador.Configuracion,
	info: actualizador.InfoActualizacion,
	anunciar: Anunciar | None = None,
) -> str:
	"""Muestra el diálogo de forma modal y devuelve su resultado."""
	dialogo = DialogoActualizacion(padre, config, info, anunciar=anunciar)
	try:
		dialogo.ShowModal()
		return dialogo.resultado
	finally:
		dialogo.Destroy()


# ════════════════════════════════════════════════════════════════════════════
# Comprobación manual (menú Ayuda) y periódica
# ════════════════════════════════════════════════════════════════════════════

_comprobacion_en_curso = threading.Lock()


def comprobar_manualmente(
	ventana: wx.Window, config: actualizador.Configuracion, anunciar: Anunciar | None = None
) -> None:
	"""Busca actualizaciones en un hilo y SIEMPRE informa del resultado.

	La manual no respeta la versión omitida: si el usuario pregunta, se le dice.
	"""
	if not _comprobacion_en_curso.acquire(blocking=False):
		_decir(anunciar, _("Ya se están buscando actualizaciones."))
		return
	_decir(anunciar, _("Buscando actualizaciones..."))

	def trabajo() -> None:
		try:
			info = actualizador.comprobar(config, respetar_omitida=False)
		except actualizador.ErrorActualizacion as error:
			wx.CallAfter(_informar_manual, ventana, config, None, str(error), anunciar)
		except Exception:
			log.exception("Fallo inesperado al buscar actualizaciones")
			wx.CallAfter(
				_informar_manual, ventana, config, None, _("Error inesperado al buscar actualizaciones."), anunciar
			)
		else:
			wx.CallAfter(_informar_manual, ventana, config, info, None, anunciar)
		finally:
			_comprobacion_en_curso.release()

	threading.Thread(target=trabajo, name="buscar-actualizaciones", daemon=True).start()


def _informar_manual(ventana, config, info, error, anunciar) -> None:
	padre = ventana if ventana else None
	titulo = _("Buscar actualizaciones")
	if error:
		mostrar_mensaje(padre, error, titulo, wx.ICON_WARNING)
	elif info is None:
		mostrar_mensaje(padre, _("Ya tiene la versión más reciente (%s).") % config.version_actual, titulo)
	else:
		mostrar_dialogo(padre, config, info, anunciar)


def anadir_menu_ayuda(
	ventana: wx.Window, menu_ayuda: wx.Menu, config: actualizador.Configuracion, anunciar: Anunciar | None = None
) -> wx.MenuItem:
	"""Añade «Buscar &actualizaciones...» al menú Ayuda y lo conecta."""
	elemento = menu_ayuda.Append(
		wx.ID_ANY, _("Buscar &actualizaciones..."), _("Comprueba si hay una versión nueva de la aplicación")
	)
	ventana.Bind(wx.EVT_MENU, lambda _evento: comprobar_manualmente(ventana, config, anunciar), elemento)
	return elemento


class ComprobadorPeriodico:
	"""Comprueba en segundo plano cada ``intervalo_horas`` y avisa si hay novedad.

	La fecha de la última comprobación se guarda en ``data/config``: reiniciar la
	aplicación no provoca una consulta nueva cada vez. Los fallos de red solo se
	registran (no se molesta al usuario por no tener conexión).
	"""

	def __init__(
		self,
		ventana: wx.Window,
		config: actualizador.Configuracion,
		intervalo_horas: float = 24,
		retraso_inicial: float = 60,
		anunciar: Anunciar | None = None,
		al_encontrar: Callable[[actualizador.InfoActualizacion], None] | None = None,
	) -> None:
		self.ventana = ventana
		self.config = config
		self.intervalo = intervalo_horas * 3600
		self.retraso_inicial = retraso_inicial
		self.anunciar = anunciar
		self.al_encontrar = al_encontrar or self._mostrar
		self._temporizador = wx.Timer(ventana)
		ventana.Bind(wx.EVT_TIMER, self._al_temporizador, self._temporizador)
		self._en_curso = False
		self._dialogo_abierto = False

	def iniciar(self) -> None:
		wx.CallLater(int(self.retraso_inicial * 1000), self._comprobar_si_toca)
		self._temporizador.Start(int(min(self.intervalo, 3600) * 1000))

	def detener(self) -> None:
		self._temporizador.Stop()

	def _al_temporizador(self, _evento: wx.TimerEvent) -> None:
		self._comprobar_si_toca()

	def toca_comprobar(self) -> bool:
		ultima = actualizador.leer_estado(self.config).get("ultima_comprobacion")
		if not ultima:
			return True
		try:
			transcurrido = (datetime.now(timezone.utc) - datetime.fromisoformat(ultima)).total_seconds()
		except (TypeError, ValueError):
			log.debug("Fecha de última comprobación no válida: %r", ultima)
			return True
		return transcurrido >= self.intervalo or transcurrido < 0

	def _comprobar_si_toca(self) -> None:
		if self._en_curso or self._dialogo_abierto or not self.toca_comprobar():
			return
		self._en_curso = True
		threading.Thread(target=self._hilo, name="actualizaciones-periodicas", daemon=True).start()

	def _hilo(self) -> None:
		info = None
		try:
			info = actualizador.comprobar(self.config, respetar_omitida=True)
		except actualizador.ErrorActualizacion as error:
			log.info("Comprobación periódica sin éxito: %s", error)
		except Exception:
			log.exception("Fallo inesperado en la comprobación periódica")
		finally:
			wx.CallAfter(self._al_terminar, info)

	def _al_terminar(self, info: actualizador.InfoActualizacion | None) -> None:
		self._en_curso = False
		if info is not None and self.ventana:
			self.al_encontrar(info)

	def _mostrar(self, info: actualizador.InfoActualizacion) -> None:
		_decir(self.anunciar, _("Hay una versión nueva disponible: %s.") % info.version)
		self._dialogo_abierto = True
		try:
			mostrar_dialogo(self.ventana, self.config, info, self.anunciar)
		finally:
			self._dialogo_abierto = False


# ════════════════════════════════════════════════════════════════════════════
# Arranque
# ════════════════════════════════════════════════════════════════════════════


def al_arrancar(ventana: wx.Window, config: actualizador.Configuracion, anunciar: Anunciar | None = None) -> None:
	"""Llamar justo después de mostrar la ventana principal.

	1. Si la actualización anterior no salió bien, lo explica al usuario.
	2. Pasados unos segundos confirma el arranque: marcador de salud (si se
	   arrancó con ``--tras-actualizar``) y versión confirmada (anti-rollback).
	"""
	tras_actualizar = actualizador.ARGUMENTO_TRAS_ACTUALIZAR in sys.argv
	try:
		mensaje = actualizador.leer_resultado_anterior(config)
	except Exception:
		log.exception("No se pudo leer el resultado de la actualización anterior")
		mensaje = None
	if mensaje:
		wx.CallAfter(mostrar_mensaje, ventana, mensaje, _("Actualización"), wx.ICON_WARNING)

	def confirmar() -> None:
		actualizador.confirmar_arranque(config, tras_actualizar)
		if tras_actualizar:
			_decir(anunciar, _("Aplicación actualizada a la versión %s.") % config.version_actual)

	wx.CallLater(actualizador.RETRASO_CONFIRMACION * 1000, confirmar)
