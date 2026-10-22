"""PR y comprobaciones automáticas (CI) de las sesiones (AIChat #236): mixin de la ventana principal.

- Menú Sesión, «PR y comprobaciones» (y el mismo submenú en el menú contextual
  de la lista de sesiones): estado de la PR (Ctrl+Alt+P), abrirla en el
  navegador, copiar su enlace, leer el fallo de las comprobaciones y preparar
  en el cuadro de respuesta un mensaje para que el agente lo arregle (nunca se
  envía solo).
- Vigilancia en segundo plano, despacio: cada 60 segundos mientras haya
  comprobaciones en curso y cada 5 minutos si no, solo en las sesiones de este
  equipo abiertas o con actividad reciente. Avisa una vez por cambio («han
  fallado», «han pasado», «se ha fusionado», «piden cambios»). Si no hay gh,
  sesión de GitHub, red o PR, calla: los errores solo se explican cuando la
  persona lo pide desde el menú.
- Propiedades y «Estado del turno» dicen lo último que se sabe, sin esperar a GitHub.

Toda la lógica de GitHub está en ``app.services.pr`` (sin wx).
"""

from __future__ import annotations

import logging
import os
import time
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import wx

from app.services import pr
from app.ui import avisos
from app.ui.accesibilidad.dialogos import mostrar_mensaje
from app.ui.comun import _, fecha_relativa

log = logging.getLogger(__name__)

#: Cada cuánto mira la vigilancia qué sesiones toca consultar (milisegundos).
INTERVALO_TIC_MS = 15000
#: Segundos entre consultas de una misma carpeta.
CADA_EN_CURSO = 60
CADA_NORMAL = 300
CADA_SIN_PR = 300
CADA_TRAS_ERROR = 900
CADA_SIN_REPOSITORIO = 1800
#: Una sesión con actividad en estas últimas horas se vigila aunque no esté abierta.
SEGUNDOS_RECIENTE = 3 * 3600
#: Como mucho se lanzan estas consultas en cada repaso (para no saturar gh ni los hilos).
MAX_POR_TIC = 2
#: Lo sabido de una PR vale para abrirla o copiar su enlace sin volver a preguntar durante este tiempo.
SEGUNDOS_CACHE_ENLACE = 600

_ESPERA_POR_ERROR = {
	"sin_pr": CADA_SIN_PR,
	"sin_repositorio": CADA_SIN_REPOSITORIO,
	"sin_github": CADA_SIN_REPOSITORIO,
	"sin_rama": CADA_SIN_PR,
	"sin_gh": CADA_SIN_REPOSITORIO,
	"sin_sesion": CADA_SIN_REPOSITORIO,
}


@dataclass
class RegistroPR:
	"""Lo último que se sabe de la PR de una carpeta."""

	estado: pr.EstadoPR | None
	codigo_error: str
	momento: float


def clave_carpeta(carpeta: str | Path) -> str:
	return os.path.normcase(os.path.normpath(str(carpeta)))


def _iso_utc(momento: float) -> str:
	return datetime.fromtimestamp(momento, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class MixinPR:
	"""Estado de la PR y de sus comprobaciones; requiere los mixins de sesiones y mensajes."""

	# ------------------------------------------------------------------
	# Datos de la vigilancia (se crean al usarlos: la ventana no necesita iniciarlos)
	# ------------------------------------------------------------------

	def _datos_pr(self) -> dict[str, Any]:
		datos = getattr(self, "_vigilancia_pr", None)
		if datos is None:
			datos = {"registros": {}, "siguiente": {}, "en_vuelo": set(), "anunciado": set(),
				"inicio": time.time(), "temporizador": None}
			self._vigilancia_pr = datos
		return datos

	def registro_pr(self, carpeta: str | Path) -> RegistroPR | None:
		return self._datos_pr()["registros"].get(clave_carpeta(carpeta))

	def _guardar_registro_pr(self, carpeta: str | Path, estado: pr.EstadoPR | None, codigo_error: str = "") -> RegistroPR | None:
		"""Guarda lo consultado y devuelve lo que se sabía antes."""
		registros = self._datos_pr()["registros"]
		clave = clave_carpeta(carpeta)
		anterior = registros.get(clave)
		registros[clave] = RegistroPR(estado, codigo_error, time.time())
		return anterior

	@staticmethod
	def _fila_local(fila) -> bool:
		return bool(fila is not None and fila.carpeta and not fila.remota and not fila.importada)

	# ------------------------------------------------------------------
	# Vigilancia en segundo plano
	# ------------------------------------------------------------------

	def iniciar_vigilancia_pr(self) -> None:
		"""Arranca el repaso periódico (al mostrar la ventana; nunca en las pruebas)."""
		datos = self._datos_pr()
		if datos["temporizador"] is None:
			temporizador = wx.Timer(self)
			self.Bind(wx.EVT_TIMER, lambda _e: self.repasar_prs(), temporizador)
			datos["temporizador"] = temporizador
		datos["temporizador"].Start(INTERVALO_TIC_MS)

	def carpetas_vigiladas(self, ahora: float | None = None) -> dict[str, list]:
		"""Carpetas de este equipo de las sesiones abiertas o con actividad reciente, con sus sesiones.

		La sesión abierta va primero en su carpeta (su título es el que se anuncia).
		"""
		ahora = time.time() if ahora is None else ahora
		carpetas: dict[str, list] = {}
		for fila in getattr(self, "_todas", []):
			if not self._fila_local(fila):
				continue
			abierta = fila.clave == self.sesion_actual or self.gestor.conversacion(fila.clave, crear=False) is not None
			if not abierta and ahora - float(fila.actividad or 0) > SEGUNDOS_RECIENTE:
				continue
			filas = carpetas.setdefault(clave_carpeta(fila.carpeta), [])
			if fila.clave == self.sesion_actual:
				filas.insert(0, fila)
			else:
				filas.append(fila)
		return carpetas

	def repasar_prs(self, ahora: float | None = None) -> int:
		"""Lanza las consultas que tocan; devuelve cuántas. Lo llama el temporizador."""
		datos = self._datos_pr()
		if getattr(self, "_saliendo", False):
			if datos["temporizador"] is not None:
				datos["temporizador"].Stop()
			return 0
		if not self.opcion("anuncios.comprobaciones_pr", True):
			return 0
		ahora = time.time() if ahora is None else ahora
		lanzadas = 0
		for clave, filas in self.carpetas_vigiladas(ahora).items():
			if lanzadas >= MAX_POR_TIC:
				break
			if clave in datos["en_vuelo"] or datos["siguiente"].get(clave, 0.0) > ahora:
				continue
			self._consultar_para_vigilancia(filas[0].carpeta)
			lanzadas += 1
		return lanzadas

	def _consultar_para_vigilancia(self, carpeta: str) -> None:
		datos = self._datos_pr()
		clave = clave_carpeta(carpeta)
		datos["en_vuelo"].add(clave)

		def consultar() -> tuple[pr.EstadoPR | None, str]:
			try:
				return pr.consultar_pr(carpeta), ""
			except pr.ErrorPR as error:
				log.debug("PR de %s: %s (%s)", carpeta, error, error.codigo)
				return None, error.codigo

		def hecho(resultado: tuple[pr.EstadoPR | None, str]) -> None:
			datos["en_vuelo"].discard(clave)
			self.al_consultar_pr(carpeta, *resultado)

		def fallo(_error: BaseException) -> None:
			datos["en_vuelo"].discard(clave)
			datos["siguiente"][clave] = time.time() + CADA_TRAS_ERROR

		self.en_segundo_plano(consultar, al_terminar=hecho, al_fallar=fallo, clave=f"pr:{clave}")

	def al_consultar_pr(self, carpeta: str, estado: pr.EstadoPR | None, codigo_error: str = "",
			ahora: float | None = None) -> str:
		"""Guarda el resultado, programa la siguiente consulta y avisa si algo cambió. Devuelve lo avisado."""
		datos = self._datos_pr()
		ahora = time.time() if ahora is None else ahora
		clave = clave_carpeta(carpeta)
		anterior = self._guardar_registro_pr(carpeta, estado, codigo_error)
		if estado is None:
			datos["siguiente"][clave] = ahora + _ESPERA_POR_ERROR.get(codigo_error, CADA_TRAS_ERROR)
			return ""
		datos["siguiente"][clave] = ahora + (CADA_EN_CURSO if estado.resultado == pr.RESULTADO_EN_CURSO else CADA_NORMAL)
		antes = anterior.estado if anterior is not None and anterior.estado is not None and anterior.estado.url == estado.url else None
		filas = self.carpetas_vigiladas(ahora).get(clave) or [f for f in getattr(self, "_todas", [])
			if self._fila_local(f) and clave_carpeta(f.carpeta) == clave]
		titulo = filas[0].titulo if filas else Path(carpeta).name
		return self.anunciar_cambios_pr(estado, antes, titulo)

	def anunciar_cambios_pr(self, estado: pr.EstadoPR, antes: pr.EstadoPR | None, titulo: str) -> str:
		"""Avisa UNA vez de cada cambio que importa; devuelve el texto avisado ("" si nada)."""
		datos = self._datos_pr()
		anunciado: set = datos["anunciado"]
		avisos_pr: list[tuple[str, str]] = []
		resultado = estado.resultado
		if resultado in (pr.RESULTADO_FALLIDAS, pr.RESULTADO_CORRECTAS):
			nuevo = antes.firma != estado.firma if antes is not None else estado.ultima_terminada >= _iso_utc(datos["inicio"])
			marca = (estado.url, "comprobaciones", estado.firma)
			if nuevo and marca not in anunciado:
				anunciado.add(marca)
				if resultado == pr.RESULTADO_FALLIDAS:
					nombres = estado.nombres(pr.FALLIDA) or estado.nombres(pr.CANCELADA)
					avisos_pr.append(("error", _("Las comprobaciones de la PR {n} de «{t}» han fallado: {c}.").format(
						n=estado.numero, t=titulo, c=nombres)))
				else:
					avisos_pr.append(("respuesta", _("Las comprobaciones de la PR {n} de «{t}» han pasado.").format(
						n=estado.numero, t=titulo)))
		if antes is not None and antes.estado == "OPEN" and estado.estado == "MERGED" and (estado.url, "fusionada") not in anunciado:
			anunciado.add((estado.url, "fusionada"))
			avisos_pr.append(("respuesta", _("La PR {n} de «{t}» se ha fusionado.").format(n=estado.numero, t=titulo)))
		if antes is not None and antes.revision != "CHANGES_REQUESTED" and estado.revision == "CHANGES_REQUESTED":
			avisos_pr.append(("permiso", _("En la revisión de la PR {n} de «{t}» piden cambios.").format(
				n=estado.numero, t=titulo)))
		if not avisos_pr:
			return ""
		texto = " ".join(t for _s, t in avisos_pr)
		avisos.sonar(avisos_pr[0][0])
		avisos.anunciar(texto)
		if self.opcion("notificaciones.modo", "te_necesita") != "nunca":
			avisos.notificacion_sistema(_("PR {n}").format(n=estado.numero), texto)
		return texto

	# ------------------------------------------------------------------
	# Propiedades y «Estado del turno» (solo lo ya sabido: nunca esperan a GitHub)
	# ------------------------------------------------------------------

	def _registro_de_fila(self, fila) -> RegistroPR | None:
		if not self._fila_local(fila):
			return None
		registro = self.registro_pr(fila.carpeta)
		if registro is None and self.opcion("anuncios.comprobaciones_pr", True):
			# Se pregunta en segundo plano para que la próxima vez se sepa.
			clave = clave_carpeta(fila.carpeta)
			if clave not in self._datos_pr()["en_vuelo"]:
				self._consultar_para_vigilancia(fila.carpeta)
		return registro

	def lineas_pr(self, fila) -> list[str]:
		"""Líneas de Propiedades sobre la PR, si se conoce."""
		registro = self._registro_de_fila(fila)
		if registro is None:
			return []
		cuando = fecha_relativa(registro.momento)
		if registro.estado is None:
			if registro.codigo_error == "sin_pr":
				return [_("PR: ninguna para la rama actual (comprobado {c}).").format(c=cuando)]
			return []
		estado = registro.estado
		return [
			_("PR: {v}").format(v=estado.resumen()),
			_("Título de la PR: {v}").format(v=estado.titulo),
			_("Enlace de la PR: {v}").format(v=estado.url),
			_("Estado de la PR comprobado: {v}").format(v=cuando),
		]

	def frase_pr(self, fila) -> str:
		"""Frase para «Estado del turno»: el estado de la PR, si se conoce."""
		registro = self._registro_de_fila(fila)
		if registro is None or registro.estado is None:
			return ""
		return registro.estado.resumen()

	# ------------------------------------------------------------------
	# Menú «PR y comprobaciones»
	# ------------------------------------------------------------------

	def crear_menu_pr(self, fila=None, destino: wx.EvtHandler | None = None) -> wx.Menu:
		"""Submenú del menú Sesión (``fila`` None: la sesión elegida al usarlo) o del menú contextual.

		:param destino: ventana que recibe las órdenes (la que abre el menú contextual); de fábrica, la ventana.
		"""
		menu = wx.Menu()
		entradas = (
			(_("&Estado de la PR...\tCtrl+Alt+P"), _("Dice cómo va la PR de la rama de la sesión en GitHub y sus "
				"comprobaciones automáticas: en curso, correctas y fallidas, con sus nombres."), self.accion_estado_pr),
			(_("&Abrir la PR en el navegador"), _("Abre en el navegador la página de la PR de la sesión en GitHub."),
				self.accion_abrir_pr),
			(_("&Copiar el enlace de la PR"), _("Copia al portapapeles la dirección de la PR de la sesión."),
				self.accion_copiar_enlace_pr),
			(_("&Leer el fallo de las comprobaciones..."), _("Muestra las líneas del registro donde fallaron las "
				"comprobaciones automáticas de la PR, para leerlas con calma."), self.accion_leer_fallo_pr),
			(_("&Pedir al agente que lo arregle"), _("Prepara en el cuadro de respuesta un mensaje para el agente con "
				"el fallo de las comprobaciones. No se envía solo: revísalo y envíalo con Control+Intro."),
				self.accion_arreglar_pr),
		)
		destino = destino or self
		for etiqueta, ayuda, accion in entradas:
			item = menu.Append(wx.ID_ANY, etiqueta, ayuda)
			destino.Bind(wx.EVT_MENU, lambda _e, a=accion: self._ejecutar_accion_pr(a, fila), item)
		return menu

	def _ejecutar_accion_pr(self, accion, fila) -> None:
		try:
			accion(fila)
		except Exception as error:  # noqa: BLE001 - una acción que falla se explica, no tumba la ventana
			log.exception("Falló una acción de «PR y comprobaciones»")
			from app.ui.accesibilidad.dialogos import mostrar_error

			mostrar_error(self, _("No se pudo completar la acción: {error}").format(error=error))

	def _carpeta_para_pr(self, fila, titulo: str) -> Path | None:
		if fila is None:
			return None
		if fila.remota:
			self._avisar_remota(fila, titulo)
			return None
		if fila.importada:
			mostrar_mensaje(self, _("Esta sesión está importada de otro equipo: su carpeta no está en este."), titulo)
			return None
		if not fila.carpeta or not Path(fila.carpeta).is_dir():
			mostrar_mensaje(self, _("La carpeta de esta sesión no existe o no se conoce."), titulo)
			return None
		return Path(fila.carpeta)

	def _con_pr(self, fila, titulo: str, al_tener, con_fallo: bool = False, usar_cache: bool = False) -> None:
		"""Consulta la PR de ``fila`` en segundo plano y llama a ``al_tener(estado, fallo)``.

		Los errores (sin gh, sin sesión, sin PR, sin red...) se explican en un cuadro.
		"""
		fila = fila if fila is not None else self.sesion_objetivo()
		carpeta = self._carpeta_para_pr(fila, titulo)
		if carpeta is None:
			return
		if usar_cache and not con_fallo:
			registro = self.registro_pr(carpeta)
			if registro is not None and registro.estado is not None and time.time() - registro.momento < SEGUNDOS_CACHE_ENLACE:
				al_tener(registro.estado, "")
				return
		avisos.anunciar(_("Consultando la PR en GitHub..."))

		def trabajo() -> tuple[pr.EstadoPR | None, str, pr.ErrorPR | None]:
			try:
				estado = pr.consultar_pr(carpeta)
			except pr.ErrorPR as error:
				return None, "", error
			if not con_fallo:
				return estado, "", None
			try:
				return estado, pr.leer_fallo(estado), None
			except pr.ErrorPR as error:
				return estado, "", error

		def hecho(resultado: tuple[pr.EstadoPR | None, str, pr.ErrorPR | None]) -> None:
			estado, fallo, error = resultado
			if estado is not None:
				self.al_consultar_pr(str(carpeta), estado)
			elif error is not None and error.codigo == "sin_pr":
				self._guardar_registro_pr(carpeta, None, "sin_pr")
			if error is not None:
				mostrar_mensaje(self, str(error), titulo)
				return
			al_tener(estado, fallo)

		self.en_segundo_plano(trabajo, al_terminar=hecho, clave=f"pr-accion:{clave_carpeta(carpeta)}")

	def accion_estado_pr(self, fila=None) -> None:
		"""Ctrl+Alt+P: el estado de la PR de la sesión y de sus comprobaciones, en el visor de texto."""
		titulo = _("Estado de la PR")

		def mostrar(estado: pr.EstadoPR, _fallo: str) -> None:
			from app.ui.dialogo_texto import mostrar_texto

			mostrar_texto(self, _("Estado de la PR {n}").format(n=estado.numero), _("Estado de la &PR:"),
				estado.texto_completo(), copiable=True)

		self._con_pr(fila, titulo, mostrar)

	def accion_abrir_pr(self, fila=None) -> None:
		titulo = _("Abrir la PR")

		def abrir(estado: pr.EstadoPR, _fallo: str) -> None:
			if estado.url and webbrowser.open(estado.url):
				avisos.anunciar(_("Se ha abierto la PR {n} en el navegador.").format(n=estado.numero), interrumpir=True)
			else:
				mostrar_mensaje(self, _("No se pudo abrir el navegador. La dirección es: {url}").format(url=estado.url), titulo)

		self._con_pr(fila, titulo, abrir, usar_cache=True)

	def accion_copiar_enlace_pr(self, fila=None) -> None:
		self._con_pr(fila, _("Copiar el enlace de la PR"), lambda estado, _f: self.copiar(estado.url), usar_cache=True)

	def accion_leer_fallo_pr(self, fila=None) -> None:
		titulo = _("Fallo de las comprobaciones")

		def mostrar(estado: pr.EstadoPR, fallo: str) -> None:
			from app.ui.dialogo_texto import mostrar_texto

			mostrar_texto(self, _("Fallo de las comprobaciones de la PR {n}").format(n=estado.numero),
				_("&Registro del fallo:"), fallo, copiable=True)

		self._con_pr(fila, titulo, mostrar, con_fallo=True)

	def accion_arreglar_pr(self, fila=None) -> None:
		"""Prepara en el cuadro de respuesta un mensaje con el fallo, sin enviarlo."""
		fila = fila if fila is not None else self.sesion_objetivo()
		if fila is None:
			return
		clave = fila.clave

		def preparar(estado: pr.EstadoPR, fallo: str) -> None:
			self.preparar_mensaje_arreglo(clave, pr.mensaje_para_arreglar(estado, fallo))

		self._con_pr(fila, _("Pedir al agente que lo arregle"), preparar, con_fallo=True)

	def preparar_mensaje_arreglo(self, clave: str, texto: str) -> None:
		"""Abre la sesión si hace falta y añade ``texto`` al final del cuadro de respuesta."""
		if clave != self.sesion_actual:
			self.abrir_sesion(clave, mover_foco=False)
		actual = self.respuesta.GetValue().rstrip()
		self.respuesta.SetValue("\n\n".join(t for t in (actual, texto) if t.strip()))
		self.respuesta.SetInsertionPointEnd()
		self.ir_a_respuesta()
		avisos.anunciar(_("Mensaje con el fallo preparado en el cuadro de respuesta. Revísalo y envíalo con "
			"Control+Intro."), interrumpir=True)
