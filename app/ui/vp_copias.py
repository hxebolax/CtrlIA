"""Copias de seguridad: mixin de la ventana principal.

Herramientas, «Copias de seguridad» y la comprobación de las copias programadas.
Toda la lógica está en ``app.services.copias`` (sin wx); aquí se pregunta, se lanza en
un hilo con ``app.core.tareas`` y se cuenta cómo va: por hitos en la barra de estado y,
si la copia la pidió la persona, también por voz. Las programadas no molestan: solo se
dice algo si fallan, y como mucho una vez al día.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import wx

from app.core import tareas
from app.services import copias
from app.ui import avisos
from app.ui.accesibilidad.dialogos import mostrar_error, mostrar_mensaje, pedir_texto
from app.ui.comun import _

log = logging.getLogger(__name__)

#: Solo hay una copia a la vez (a mano o programada).
CLAVE_COPIA = "copia-seguridad"
#: Cada cuánto se mira si toca una copia programada (milisegundos).
INTERVALO_COPIAS_MS = 10 * 60 * 1000


class MixinCopias:
	"""Se mezcla con ``VentanaPrincipal``."""

	# ── Menú ──────────────────────────────────────────────────────────────────

	def crear_menu_copias(self) -> wx.Menu:
		"""Submenú de Herramientas, «Copias de seguridad»."""
		menu = wx.Menu()
		entradas = (
			(_("&Hacer una copia ahora...\tCtrl+Alt+B"), _("Guarda en un archivo el historial, la memoria y los ajustes de tus agentes "
				"y los datos de Ctrl+IA, sin ningún inicio de sesión."), "accion_hacer_copia"),
			(_("&Programar copias..."), _("Elige si se hacen copias solas cada día o cada semana, cuántas se conservan y dónde."),
				"accion_programar_copias"),
			(_("&Comprobar una copia..."), _("Relee una copia y dice si está entera."), "accion_comprobar_copia"),
			(_("&Restaurar desde una copia..."), _("Trae sesiones de una copia como importadas, solo para leer. Los archivos de "
				"los agentes no se tocan."), "accion_restaurar_copia"),
			(_("&Detener la copia en curso"), _("Detiene la copia que se está haciendo. No queda ningún archivo a medias."),
				"accion_detener_copia"),
		)
		for etiqueta, ayuda, metodo in entradas:
			item = menu.Append(wx.ID_ANY, etiqueta, ayuda)
			self.Bind(wx.EVT_MENU, lambda _e, m=metodo: self._ejecutar_accion(m), item)
		return menu

	# ── Hacer una copia ───────────────────────────────────────────────────────

	def accion_hacer_copia(self) -> None:
		"""Herramientas, Copias de seguridad, «Hacer una copia ahora...» (Ctrl+Alt+B)."""
		from app.ui.dialogos.copias import DialogoHacerCopia

		if self.tareas.activa(CLAVE_COPIA):
			avisos.anunciar(_("Ya hay una copia en marcha."), interrumpir=True)
			return
		estado = copias.cargar_estado()
		dialogo = DialogoHacerCopia(self, str(estado.get("carpeta") or copias.cargar_programa(estado).carpeta),
			copias.texto_ultima_copia(estado))
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			carpeta, frase = dialogo.carpeta, dialogo.frase
		finally:
			dialogo.Destroy()
		if frase:
			from app.core.registro import registrar_secreto

			registrar_secreto(frase)
		avisos.anunciar(_("Haciendo la copia de seguridad en segundo plano. Puedes seguir trabajando."), interrumpir=True)

		def trabajo(tarea: tareas.Tarea) -> copias.ResultadoCopia:
			def progreso(hechos: int, total: int, nombre: str) -> None:
				tarea.progresar(hechos, total, nombre)

			try:
				resultado = copias.crear(Path(carpeta), frase=frase, progreso=progreso, cancelado=lambda: tarea.cancelado)
			except InterruptedError as error:
				raise tareas.TareaCancelada(str(error)) from error
			copias.registrar(resultado)
			return resultado

		def hecho(resultado: copias.ResultadoCopia) -> None:
			self.poner_estado(_("Copia de seguridad terminada."), 3)
			avisos.sonar("respuesta")
			avisos.notificar(copias.resumen(resultado), _("Copia de seguridad"), padre=self)

		def fallo(error: BaseException) -> None:
			if isinstance(error, tareas.TareaCancelada):
				self.poner_estado(_("Copia de seguridad detenida."), 3)
				avisos.anunciar(_("Copia de seguridad detenida. No ha quedado ningún archivo a medias."), interrumpir=True)
			elif isinstance(error, tareas.TareaDuplicada):
				avisos.anunciar(_("Ya hay una copia en marcha."), interrumpir=True)
			else:
				log.error("Falló la copia de seguridad: %s", error, exc_info=error)
				avisos.sonar("error")
				mostrar_error(self, _("No se ha podido hacer la copia: {e}").format(e=error), _("Copia de seguridad"))

		self.tareas.enviar(trabajo, al_terminar=hecho, al_fallar=fallo, al_progresar=self._progreso_por_hitos(_("Copia de seguridad"), True),
			clave_unica=CLAVE_COPIA, nombre="copia de seguridad", duplicadas="rechazar")

	def _progreso_por_hitos(self, nombre: str, con_voz: bool):
		"""Función de progreso: «{nombre}: 25 %» en la barra de estado y, con ``con_voz``, también dicho."""
		hitos = tareas.HitosProgreso(25)

		def al_progresar(progreso: tareas.Progreso) -> None:
			hito = hitos.nuevo_hito(progreso.porcentaje)
			if hito is None or hito >= 100:
				return
			texto = _("{nombre}: {p} %").format(nombre=nombre, p=hito)
			if con_voz:
				avisos.anunciar(texto)
			else:
				self.poner_estado(texto, 3)

		return al_progresar

	def accion_detener_copia(self) -> None:
		"""Detiene la copia en curso: su hilo lo ve en el siguiente archivo y no deja nada a medias."""
		if not self.tareas.cancelar(CLAVE_COPIA, _("La persona la ha detenido")):
			avisos.anunciar(_("No hay ninguna copia en marcha."), interrumpir=True)
			return
		avisos.anunciar(_("Deteniendo la copia de seguridad..."), interrumpir=True)

	# ── Programar ─────────────────────────────────────────────────────────────

	def accion_programar_copias(self) -> None:
		"""Herramientas, Copias de seguridad, «Programar copias...»."""
		from app.ui.dialogos.copias import DialogoProgramarCopias

		estado = copias.cargar_estado()
		lineas = [copias.texto_ultima_copia(estado), copias.texto_fallo(estado)]
		dialogo = DialogoProgramarCopias(self, copias.cargar_programa(estado), copias.hay_frase(), "\n".join(t for t in lineas if t))
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			programa, frase = dialogo.programa, dialogo.frase_nueva
		finally:
			dialogo.Destroy()

		def guardar() -> None:
			if frase:
				copias.guardar_frase(frase)
			elif not programa.cifrar:
				copias.borrar_frase()
			copias.guardar_programa(programa)
			copias.borrar_fallo()

		def hecho(_resultado: Any) -> None:
			avisos.anunciar(_("Programa de copias guardado.") if programa.activo else _("Copias programadas desactivadas."), interrumpir=True)
			self.comprobar_copia_programada()

		def fallo(error: BaseException) -> None:
			mostrar_error(self, _("No se ha podido guardar el programa: {e}").format(e=error), _("Programar copias"))

		if frase:
			from app.core.registro import registrar_secreto

			registrar_secreto(frase)
		self.en_segundo_plano(guardar, al_terminar=hecho, al_fallar=fallo)

	def comprobar_copia_programada(self) -> None:
		"""Mira, en un hilo, si toca una copia programada; se llama al arrancar y con un temporizador."""
		if self.tareas.activa(CLAVE_COPIA):
			return

		def pendiente(toca: bool) -> None:
			if toca:
				self._lanzar_copia_programada()

		self.en_segundo_plano(copias.copia_pendiente, al_terminar=pendiente, al_fallar=lambda error: log.warning(
			"No se pudo mirar si toca una copia: %s", error), clave="mirar-copia-programada")

	def _lanzar_copia_programada(self) -> None:
		def trabajo(tarea: tareas.Tarea) -> copias.ResultadoCopia:
			def progreso(hechos: int, total: int, nombre: str) -> None:
				tarea.progresar(hechos, total, nombre)

			try:
				return copias.ejecutar_programada(cancelado=lambda: tarea.cancelado, progreso=progreso)
			except InterruptedError as error:
				raise tareas.TareaCancelada(str(error)) from error

		def hecho(resultado: copias.ResultadoCopia) -> None:
			log.info("Copia programada: %s", copias.resumen(resultado))
			self.poner_estado(_("Copia programada terminada."), 3)
			if not resultado.completa:
				avisos.anunciar(_("La copia programada ha salido incompleta: no se han podido copiar {n} elementos. Su manifiesto "
					"dice cuáles.").format(n=len(resultado.fallos)))

		def fallo(error: BaseException) -> None:
			if isinstance(error, tareas.TareaCancelada | tareas.TareaDuplicada):
				self.poner_estado(_("Copia programada detenida."), 3)
				return
			log.warning("Falló una copia programada: %s", error)

			def decidido(decir: bool) -> None:
				if decir:
					avisos.sonar("error")
					avisos.notificar(_("No se ha podido hacer la copia programada: {e}").format(e=error), _("Copia de seguridad"), padre=self)

			self.en_segundo_plano(copias.anotar_fallo, str(error), al_terminar=decidido)

		self.tareas.enviar(trabajo, al_terminar=hecho, al_fallar=fallo, al_progresar=self._progreso_por_hitos(_("Copia programada"), False),
			clave_unica=CLAVE_COPIA, nombre="copia programada", duplicadas="rechazar")

	# ── Comprobar ─────────────────────────────────────────────────────────────

	def _elegir_copia(self, titulo: str) -> Path | None:
		carpeta = str(copias.cargar_estado().get("carpeta") or "")
		with wx.FileDialog(self, titulo, defaultDir=carpeta, wildcard=_("Copia de Ctrl+IA (*.zip)|*.zip"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as elegir:
			return Path(elegir.GetPath()) if elegir.ShowModal() == wx.ID_OK else None

	def _pedir_frase(self, titulo: str, problema: str = "") -> str | None:
		"""Pide la frase de una copia. Solo se usa para abrirla: nunca se guarda."""
		frase = pedir_texto(self, _("&Frase de la copia:"), titulo, contrasena=True,
			ayuda=(problema + " " if problema else "") + _("La frase con la que se cifró esta copia. No se guarda."))
		if frase:
			from app.core.registro import registrar_secreto

			registrar_secreto(frase)
		return frase or None

	def accion_comprobar_copia(self) -> None:
		"""Herramientas, Copias de seguridad, «Comprobar una copia...»."""
		ruta = self._elegir_copia(_("Comprobar una copia"))
		if ruta is not None:
			self._comprobar_copia(ruta, None)

	def _comprobar_copia(self, ruta: Path, frase: str | None) -> None:
		avisos.anunciar(_("Comprobando la copia..."), interrumpir=True)

		def trabajo(tarea: tareas.Tarea) -> copias.Comprobacion:
			try:
				return copias.comprobar(ruta, lambda: tarea.cancelado, frase)
			except InterruptedError as error:
				raise tareas.TareaCancelada(str(error)) from error

		def hecho(comprobacion: copias.Comprobacion) -> None:
			if comprobacion.pide_frase:
				otra = self._pedir_frase(_("Comprobar una copia"), " ".join(comprobacion.problemas))
				if otra:
					self._comprobar_copia(ruta, otra)
				return
			avisos.sonar("respuesta" if comprobacion.correcta else "error")
			mostrar_mensaje(self, copias.texto_comprobacion(ruta, comprobacion), _("Comprobar una copia"))

		def fallo(error: BaseException) -> None:
			if not isinstance(error, tareas.TareaCancelada):
				mostrar_error(self, _("No se ha podido comprobar la copia: {e}").format(e=error), _("Comprobar una copia"))

		self.tareas.enviar(trabajo, al_terminar=hecho, al_fallar=fallo, nombre="comprobar copia")

	# ── Restaurar ─────────────────────────────────────────────────────────────

	def accion_restaurar_copia(self) -> None:
		"""Herramientas, Copias de seguridad, «Restaurar desde una copia...»."""
		ruta = self._elegir_copia(_("Restaurar desde una copia"))
		if ruta is not None:
			self._abrir_copia(ruta, None)

	def _abrir_copia(self, ruta: Path, frase: str | None) -> None:
		avisos.anunciar(_("Leyendo la copia..."), interrumpir=True)
		presentes = [f.clave for f in self._todas]

		def trabajo(tarea: tareas.Tarea) -> copias.CopiaAbierta:
			def progreso(hechos: int, total: int, nombre: str) -> None:
				tarea.progresar(hechos, total, nombre)

			try:
				copia = copias.CopiaAbierta(ruta, frase, lambda: tarea.cancelado, progreso)
			except InterruptedError as error:
				raise tareas.TareaCancelada(str(error)) from error
			try:
				copia.situar(self.almacen, presentes)
			except Exception:
				copia.cerrar()
				raise
			return copia

		def fallo(error: BaseException) -> None:
			if isinstance(error, copias.ErrorFrase):
				otra = self._pedir_frase(_("Restaurar desde una copia"), copias.PROBLEMAS_FRASE.get(str(error), ""))
				if otra:
					self._abrir_copia(ruta, otra)
			elif not isinstance(error, tareas.TareaCancelada):
				mostrar_error(self, _("No se ha podido leer la copia: {e}").format(e=error), _("Restaurar desde una copia"))

		self.tareas.enviar(trabajo, al_terminar=self._elegir_y_restaurar, al_fallar=fallo,
			al_progresar=self._progreso_por_hitos(_("Leyendo la copia"), True), nombre="abrir copia")

	@staticmethod
	def _informacion_copia(copia: copias.CopiaAbierta) -> str:
		lineas = [_("Copia de {e}: {n}.").format(e=copia.equipo or _("un equipo desconocido"), n=copia.ruta.name)]
		lineas.append(_("Las sesiones restauradas son copias para leer. Las de Antigravity van en la copia, pero sus mensajes "
			"no se pueden traer: sus archivos están en un formato que Ctrl+IA no sabe leer."))
		lineas.extend(dict.fromkeys(copia.notas))
		return "\n".join(lineas)

	def _elegir_y_restaurar(self, copia: copias.CopiaAbierta) -> None:
		from app.ui.dialogos.copias import DialogoRestaurarCopia

		if not copia.sesiones:
			texto = _("Esta copia no tiene sesiones que se puedan restaurar.")
			if copia.notas:
				texto += " " + " ".join(dict.fromkeys(copia.notas))
			copia.cerrar()
			mostrar_mensaje(self, texto, _("Restaurar desde una copia"))
			return
		dialogo = DialogoRestaurarCopia(self, copia.sesiones, self._informacion_copia(copia), leer_preferencias=self.preferencias_lectura)
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				copia.cerrar()
				return
			elegidas = list(dialogo.elegidas)
		finally:
			dialogo.Destroy()
		avisos.anunciar(_("Restaurando {n} sesiones...").format(n=len(elegidas)), interrumpir=True)

		def trabajo(tarea: tareas.Tarea) -> copias.ResultadoRestauracion:
			def progreso(hechos: int, total: int, nombre: str) -> None:
				tarea.progresar(hechos, total, nombre)

			try:
				return copia.restaurar(elegidas, self.almacen, progreso, lambda: tarea.cancelado)
			except InterruptedError as error:
				raise tareas.TareaCancelada(str(error)) from error
			finally:
				copia.cerrar()

		def hecho(resultado: copias.ResultadoRestauracion) -> None:
			self.refrescar_sesiones(True)
			mostrar_mensaje(self, copias.resumen_restauracion(resultado), _("Restaurar desde una copia"))

		def fallo(error: BaseException) -> None:
			if not isinstance(error, tareas.TareaCancelada):
				mostrar_error(self, _("No se han podido restaurar las sesiones: {e}").format(e=error), _("Restaurar desde una copia"))

		self.tareas.enviar(trabajo, al_terminar=hecho, al_fallar=fallo,
			al_progresar=self._progreso_por_hitos(_("Restaurando"), True), nombre="restaurar copia")
