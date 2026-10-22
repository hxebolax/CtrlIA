"""Conversar con los agentes desde la ventana principal.

Aquí llegan, ya en el hilo de la interfaz, los eventos de todas las sesiones
vivas. Qué se dice por voz depende de Preferencias, Anuncios:

- Nivel «completo»: se lee la respuesta del agente según llega.
- Nivel «resumen»: solo «Claude ha respondido en tal sesión».
- Nivel «silencio»: nada (los avisos importantes siguen su propia opción).

Los avisos importantes (errores y peticiones de permiso) no dependen del
nivel: siguen la opción «Avisos importantes». Las peticiones de permiso
suenan, se anuncian y, si Ctrl+IA no está en primer plano, aparecen como
notificación de Windows. Se responden con Control+Mayúsculas+A.
"""

from __future__ import annotations

import logging
import mimetypes
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import wx

from app.services.agentes.base import Adjunto, Evento, SolicitudPermiso, TipoEvento
from app.services.gestor import MensajeVista
from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, elegir_opcion, mostrar_error, mostrar_mensaje
from app.ui.comun import _, duracion, nombre_agente, nombre_estado

if TYPE_CHECKING:
	from app.services.gestor import FilaSesion

log = logging.getLogger(__name__)

MAX_ANUNCIO = 4000


class MixinConversacion:
	"""Envío de mensajes, permisos y eventos de los agentes."""

	def iniciar_conversacion(self) -> None:
		self._adjuntos: list[Adjunto] = []
		self._anunciado: dict[tuple[str, Any], int] = {}
		self._turnos: dict[str, dict[str, Any]] = {}
		self._avisos_uso: set[tuple[str, str, int]] = set()
		#: Lo último que escribió el agente en cada sesión desde su última acción (para las preguntas).
		self._texto_reciente: dict[str, str] = {}
		#: Textos no entregados al agente de sesiones que no están abiertas: vuelven al abrirlas.
		self._devueltos: dict[str, list[str]] = {}
		#: Última lista de tareas conocida de cada sesión (para decir solo las que se acaban de hacer).
		self._tareas: dict[str, list[Any]] = {}
		self.respuesta.Bind(wx.EVT_TEXT, lambda e: (self._actualizar_estado_respuesta(), e.Skip()))
		#: Palabras curiosas en la barra de estado (Preferencias, Anuncios; #314): lo último que se escribió.
		self._texto_curioso = ""
		self._palabra_curiosa = ""
		self._agente_curioso = ""
		self._temporizador_palabras = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, lambda _e: self._cambiar_palabra_curiosa(), self._temporizador_palabras)

	# ------------------------------------------------------------------
	# Cuadro de respuesta
	# ------------------------------------------------------------------

	def preparar_cuadro_respuesta(self) -> None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None:
			nombre = _("Respuesta (abre o crea una sesión primero)")
		elif fila.propia:
			nombre = _("Respuesta para {a}").format(a=nombre_agente(fila.proveedor))
		else:
			nombre = _("Respuesta (sesión de solo lectura: al enviar se te ofrecerá continuarla aquí)")
		self.respuesta.SetName(nombre)
		devueltos = self._devueltos.pop(self.sesion_actual, []) if fila is not None else []
		if devueltos:
			self._devolver_al_cuadro("\n\n".join(devueltos))

	def _devolver_al_cuadro(self, texto: str) -> None:
		"""Pone delante del cuadro de respuesta un texto que no llegó al agente."""
		actual = self.respuesta.GetValue()
		self.respuesta.SetValue("\n\n".join(t for t in (texto, actual) if t.strip()))
		self.respuesta.SetInsertionPointEnd()

	def _actualizar_estado_respuesta(self) -> None:
		if self._adjuntos:
			self.poner_estado(_("{n} adjuntos preparados").format(n=len(self._adjuntos)), 1)

	def pegar_imagen(self, aunque_haya_texto: bool = False) -> bool:
		"""Adjunta la imagen o los archivos copiados. ``True`` si lo hizo (si no, Ctrl+V pega el texto).

		Con texto en el portapapeles no se adjunta nada salvo con ``aunque_haya_texto``
		(«Adjuntar lo copiado»): una celda de Excel o un párrafo de Word traen
		también una imagen y Ctrl+V debe pegar su texto.
		"""
		from app.core.paths import ruta_temporales
		from app.ui.portapapeles import leer_adjuntables

		copiado = leer_adjuntables(ruta_temporales() / "imagenes", aunque_haya_texto)
		if copiado is None or not copiado.adjuntos:
			if copiado is not None and copiado.hay_texto and copiado.hay_adjuntable:
				wx.CallAfter(avisos.anunciar,
					_("Lo copiado trae también una imagen o archivos: Ctrl+Alt+V los adjunta."))
			return False
		self._adjuntos.extend(copiado.adjuntos)
		if len(copiado.adjuntos) == 1:
			adjunto = copiado.adjuntos[0]
			texto = _("Imagen adjuntada.") if adjunto.es_imagen and adjunto.ruta.name.startswith("pegada_") \
				else _("Adjuntado {a}.").format(a=adjunto.ruta.name)
		else:
			texto = _("{n} archivos adjuntados.").format(n=len(copiado.adjuntos))
		avisos.anunciar(texto + " " + _("Hay {n} adjuntos; se enviarán con tu próximo mensaje.").format(
			n=len(self._adjuntos)), interrumpir=True)
		self._actualizar_estado_respuesta()
		return True

	def accion_pegar_adjunto(self) -> None:
		"""Adjunta la imagen o los archivos copiados aunque lo copiado traiga también texto."""
		if not self.pegar_imagen(aunque_haya_texto=True):
			avisos.anunciar(_("El portapapeles no tiene ninguna imagen ni archivos que adjuntar."), interrumpir=True)
			return
		self.ir_a_respuesta()

	def accion_adjuntar(self) -> None:
		if self._adjuntos:
			eleccion = elegir_opcion(self, _("Hay {n} archivos adjuntos: {lista}").format(
				n=len(self._adjuntos), lista=", ".join(a.ruta.name for a in self._adjuntos)),
				_("Adjuntos"), [_("Añadir más"), _("Quitar todos")])
			if eleccion is None:
				return
			if eleccion == 1:
				self._adjuntos.clear()
				avisos.anunciar(_("Adjuntos quitados."), interrumpir=True)
				self.poner_estado("", 1)
				return
		with wx.FileDialog(self, _("Adjuntar archivos"), style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST) as dialogo:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			rutas = [Path(r) for r in dialogo.GetPaths()]
		for ruta in rutas:
			tipo = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
			self._adjuntos.append(Adjunto(ruta, tipo))
		avisos.anunciar(_("{n} adjuntos preparados. Se enviarán con tu próximo mensaje.").format(n=len(self._adjuntos)), interrumpir=True)
		self._actualizar_estado_respuesta()
		self.ir_a_respuesta()

	def accion_plantillas(self) -> None:
		from app.ui.dialogos.plantillas import DialogoPlantillas

		dialogo = DialogoPlantillas(self, self.almacen, self.respuesta.GetValue())
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			texto = getattr(dialogo, "texto_elegido", "")
		finally:
			dialogo.Destroy()
		if texto:
			self.respuesta.WriteText(texto)
			self.ir_a_respuesta()

	def accion_comando(self) -> None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None:
			avisos.anunciar(_("Primero abre una sesión."), interrumpir=True)
			return
		comandos = self.gestor.comandos(fila.proveedor)
		proveedor = self.gestor.proveedor(fila.proveedor)
		if comandos or not hasattr(proveedor, "obtener_comandos"):
			self._elegir_comando(comandos)
			return
		avisos.anunciar(_("Consultando los comandos del agente..."))
		carpeta = Path(fila.carpeta) if fila.carpeta and Path(fila.carpeta).is_dir() else Path.home()
		self.en_segundo_plano(proveedor.obtener_comandos, carpeta, al_terminar=self._elegir_comando)

	def _elegir_comando(self, comandos: list[tuple[str, str]]) -> None:
		if not comandos:
			mostrar_mensaje(self, _("Este agente no ha informado de comandos disponibles."), _("Comandos"))
			return
		from app.ui.dialogos.selector import elegir

		indice = elegir(self, _("Comandos y habilidades"), _("Co&mandos:"), [(c, d) for c, d in comandos],
			[_("Comando"), _("Descripción")])
		if indice is None:
			return
		comando = comandos[indice][0]
		actual = self.respuesta.GetValue()
		self.respuesta.SetValue(comando + " " + actual if not actual.startswith("/") else comando + " ")
		self.ir_a_respuesta()

	# ------------------------------------------------------------------
	# Enviar, detener, responder
	# ------------------------------------------------------------------

	def accion_enviar(self) -> None:
		self._enviar_desde_cuadro(ahora=False)

	def accion_enviar_ahora(self) -> None:
		self._enviar_desde_cuadro(ahora=True)

	def accion_anadir_al_turno(self) -> None:
		"""Mete lo escrito en el turno en marcha sin detener al agente (si el agente lo admite)."""
		texto = self.respuesta.GetValue()
		if not texto.strip():
			avisos.anunciar(_("Escribe primero un mensaje en el cuadro de respuesta."), interrumpir=True)
			self.ir_a_respuesta()
			return
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None or not fila.propia:
			avisos.anunciar(_("Solo se puede añadir un mensaje al turno en las sesiones de Ctrl+IA."), interrumpir=True)
			return
		agente = nombre_agente(fila.proveedor)
		proveedor = self.gestor.proveedor(fila.proveedor)
		if proveedor is None or not proveedor.capacidades.anadir_al_turno:
			avisos.anunciar(_(
				"{a} no admite añadir mensajes a un turno en marcha. Usa Control+Intro para dejarlo en cola o "
				"Control+Mayúsculas+Intro para detenerlo y enviarlo ahora."
			).format(a=agente), interrumpir=True)
			return
		conversacion = self.gestor.conversacion(fila.clave, crear=False)
		if conversacion is None or not self.gestor.ocupada(fila.clave):
			avisos.anunciar(_("{a} no está trabajando. Usa Control+Intro para enviar el mensaje.").format(a=agente), interrumpir=True)
			return
		if self._adjuntos:
			avisos.anunciar(_("Los archivos adjuntos no se pueden añadir a un turno en marcha. Usa Control+Intro para dejar "
				"el mensaje en cola con sus adjuntos."), interrumpir=True)
			return
		try:
			guardado = self.almacen.anadir_mensaje(fila.clave, "usuario", texto, datos={"anadido_al_turno": True})
			conversacion.anadir_al_turno(texto)
		except Exception as error:  # noqa: BLE001 - se explica y se registra; el texto sigue en el cuadro
			log.exception("No se pudo añadir el mensaje al turno")
			mostrar_error(self, _("No se pudo añadir el mensaje al turno: {e}").format(e=error), _("Añadir al turno"))
			return
		self.respuesta.Clear()
		avisos.sonar("enviado")
		self._refrescar_fila_mensaje(fila.clave, int(guardado) if isinstance(guardado, int) else 0)
		if self.opcion("anuncios.mis_mensajes", False):
			avisos.anunciar(_("Añadiendo al turno de {a}: {t}").format(a=agente, t=texto[:300]), interrumpir=True)
		else:
			avisos.anunciar(_("Añadiendo al turno de {a}, sin detenerlo.").format(a=agente), interrumpir=True)

	def _enviar_desde_cuadro(self, ahora: bool) -> None:
		texto = self.respuesta.GetValue()
		if not texto.strip() and not self._adjuntos:
			avisos.anunciar(_("Escribe primero un mensaje en el cuadro de respuesta."), interrumpir=True)
			self.ir_a_respuesta()
			return
		if not self.sesion_actual:
			if confirmar(self, _("No hay ninguna sesión abierta. ¿Crear una sesión nueva con este mensaje?"),
					_("Enviar")) == wx.ID_YES and self.accion_nueva_sesion(mensaje=texto):
				self.respuesta.Clear()
			return
		fila = self.fila_de(self.sesion_actual)
		if fila is not None and not fila.propia:
			if fila.importada:
				# La importada no se toca: se ofrece una sesión nueva de este equipo que le cuenta la conversación al agente.
				pregunta = _("Esta sesión se importó de otro equipo y es de solo lectura: el agente de este equipo no la "
					"tiene. ¿Quieres continuarla en una sesión nueva de este equipo? Elegirás el agente y la carpeta; la "
					"nueva tendrá sus mensajes y tu mensaje le llegará al agente con la conversación anterior delante.")
				if confirmar(self, pregunta, _("Continuar aquí")) != wx.ID_YES:
					return
				if self.continuar_importada(fila, texto) and self.respuesta.GetValue() == texto:
					self.respuesta.Clear()
				return
			if fila.proveedor == "antigravity" or self.seguira_la_original(fila):
				pregunta = _("Esta sesión es de {a}. ¿Quieres seguirla desde Ctrl+IA? Es la misma conversación, no una "
					"copia: lo que escribas aquí aparecerá también allí. Se le enviará tu mensaje.")
			else:
				pregunta = _("Esta sesión es de {a} y Ctrl+IA solo puede leerla. ¿Quieres continuarla aquí? Se creará "
					"una copia que recuerda toda la conversación y se le enviará tu mensaje. Si prefieres seguir la "
					"original, activa en Preferencias, Historial, «Al continuar una sesión de otro programa, seguir la "
					"original sin copiarla».")
			if confirmar(self, pregunta.format(a=fila.nombre_origen), _("Continuar aquí")) != wx.ID_YES:
				return
			adjuntos = list(self._adjuntos)

			def creada(clave: str, _original: bool) -> None:
				if self.enviar_mensaje(clave, texto, adjuntos, ahora=False):
					if self.respuesta.GetValue() == texto:
						self.respuesta.Clear()
					self._adjuntos.clear()
					self.poner_estado("", 1)

			self.continuar_sesion(fila, "", creada)
			return
		if self.enviar_mensaje(self.sesion_actual, texto, list(self._adjuntos), ahora=ahora):
			self.respuesta.Clear()
			self._adjuntos.clear()
			self.poner_estado("", 1)

	def enviar_mensaje(self, clave: str, texto: str, adjuntos: list[Adjunto] | None = None, ahora: bool = False,
			anunciar: bool = True) -> bool:
		"""Envía ``texto`` a la sesión ``clave``. ``anunciar=False``: sin «Enviado» (lo anuncia quien llama)."""
		adjuntos = adjuntos or []
		ocupada = self.gestor.ocupada(clave)
		if ahora and ocupada and adjuntos:
			# «Enviar ahora» interrumpe y pasa solo texto: con adjuntos, el mensaje va a la cola.
			avisos.anunciar(_("Con archivos adjuntos no se puede enviar ahora; el mensaje queda en cola."), interrumpir=True)
			ahora = False
		try:
			self.gestor.enviar(clave, texto, adjuntos, ahora=ahora and ocupada)
		except Exception as error:  # noqa: BLE001 - el error se explica al usuario y se registra
			log.exception("No se pudo enviar el mensaje")
			mostrar_error(self, _("No se pudo enviar el mensaje: {e}").format(e=error), _("Enviar"))
			return False
		avisos.sonar("enviado")
		self._turnos.setdefault(clave, {}).update({"inicio": time.time(), "herramienta": "", "escribiendo": False})
		if clave not in self._tareas:
			self._tareas[clave] = self._tareas_de(clave)
		if clave == self.sesion_actual:
			ultimo = self.almacen.mensajes(clave, max(0, (self._mensajes[-1].id if self._mensajes and isinstance(self._mensajes[-1].id, int) else 0)))
			for guardado in ultimo:
				self.anadir_o_actualizar_mensaje(self._vista_de(guardado))
		if anunciar:
			if ocupada and not ahora:
				avisos.anunciar(_("En cola. Se enviará cuando el agente termine."), interrumpir=True)
			elif ahora and ocupada:
				avisos.anunciar(_("Enviado ahora."), interrumpir=True)
			elif self.opcion("anuncios.mis_mensajes", False):
				avisos.anunciar(_("Enviado: {t}").format(t=texto[:300]), interrumpir=True)
			else:
				avisos.anunciar(_("Enviado."), interrumpir=True)
		self.actualizar_barra_estado()
		return True

	def accion_detener(self) -> None:
		if self.sesion_actual and not self.gestor.ocupada(self.sesion_actual):
			from app.ui.dialogos import dialogo_agentes

			# Parada mientras la otra sesión trabaja: Control+punto detiene la conversación entre sesiones.
			if dialogo_agentes.detener_si_participa(self, self.sesion_actual):
				return
		if not self.sesion_actual or not self.gestor.ocupada(self.sesion_actual):
			avisos.anunciar(_("El agente no está trabajando."), interrumpir=True)
			return
		pendientes = self.gestor.detener(self.sesion_actual)
		if pendientes:
			actual = self.respuesta.GetValue()
			self.respuesta.SetValue("\n\n".join([*pendientes, actual]).strip())
			avisos.anunciar(_("Deteniendo. {n} mensajes en cola vuelven al cuadro de respuesta.").format(n=len(pendientes)), interrumpir=True)
		else:
			avisos.anunciar(_("Deteniendo..."), interrumpir=True)

	def accion_responder(self) -> None:
		pendientes = self.gestor.solicitudes(self.sesion_actual) if self.sesion_actual else []
		if not pendientes:
			pendientes = self.gestor.solicitudes()
		if not pendientes:
			avisos.anunciar(_("Ningún agente está esperando una respuesta."), interrumpir=True)
			return
		clave, solicitud = pendientes[0]
		self._responder(clave, solicitud)

	def _responder(self, clave: str, solicitud: SolicitudPermiso) -> None:
		from app.ui.dialogos.solicitud import pedir_respuesta

		fila = self.fila_de(clave)
		if not solicitud.contexto:
			solicitud.contexto = self._texto_reciente.get(clave, "")
		respuesta = pedir_respuesta(self, solicitud, fila.titulo if fila else "")
		if respuesta is None:
			avisos.anunciar(_("Sin responder. El agente sigue esperando."))
			return
		conversacion = self.gestor.conversacion(clave, crear=False)
		antes = set(conversacion.ajustes.reglas_permitidas) if conversacion is not None else set()
		try:
			id_mensaje = self.gestor.responder(clave, solicitud.id, respuesta)
		except Exception as error:  # noqa: BLE001 - se explica y se registra
			log.exception("No se pudo responder al agente")
			mostrar_error(self, _("No se pudo enviar la respuesta: {e}").format(e=error))
			return
		if isinstance(id_mensaje, int) and id_mensaje:
			# El indicador de la fila pasa de «por responder» a lo que has respondido.
			self._refrescar_fila_mensaje(clave, id_mensaje)
		if conversacion is not None:
			nuevas = [r for r in conversacion.ajustes.reglas_permitidas if r not in antes]
			if nuevas:
				self._apuntar_fechas_reglas(clave, nuevas)
		elegida = next((o.etiqueta for o in solicitud.opciones if o.id == respuesta.opcion), respuesta.opcion)
		avisos.anunciar(_("Respondido: {o}.").format(o=elegida), interrumpir=True)
		restantes = self.gestor.solicitudes()
		if restantes:
			avisos.anunciar(_("Quedan {n} peticiones pendientes.").format(n=len(restantes)))

	# ------------------------------------------------------------------
	# Permisos de la sesión
	# ------------------------------------------------------------------

	def _apuntar_fechas_reglas(self, clave: str, reglas: list[str]) -> None:
		"""Guarda cuándo se permitió cada regla (en ``sesiones.extra``, sin tocar el esquema)."""
		sesion = self.almacen.sesion(clave)
		if sesion is None:
			return
		extra = dict(sesion.extra or {})
		fechas = dict(extra.get("reglas_fechas") or {})
		ahora = time.time()
		for regla in reglas:
			fechas.setdefault(regla, ahora)
		extra["reglas_fechas"] = fechas
		self.almacen.actualizar_sesion(clave, extra=extra)

	def accion_permisos_sesion(self) -> None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else self.fila_seleccionada()
		if fila is None or not fila.propia:
			mostrar_mensaje(self, _("Abre primero una sesión de Ctrl+IA. Las sesiones de otros programas son de solo lectura "
				"y no tienen permisos que cambiar aquí."), _("Permisos de la sesión"))
			return
		from app.ui.dialogos.permisos_sesion import ID_CAMBIAR_MODO, DialogoPermisosSesion

		sesion = self.almacen.sesion(fila.clave)
		proveedor = self.gestor.proveedor(fila.proveedor)
		if sesion is None:
			return
		conversacion = self.gestor.conversacion(fila.clave, crear=False)
		reglas = list(conversacion.ajustes.reglas_permitidas) if conversacion is not None else list(sesion.reglas)
		modos = proveedor.modos_permiso() if proveedor is not None else []
		modo_actual = conversacion.ajustes.modo_permiso if conversacion is not None else sesion.modo_permiso
		elegido = next((m for m in modos if m.id == modo_actual), None)
		if elegido is None and modos and not modo_actual:
			elegido = modos[0]
		nombre_modo = elegido.etiqueta if elegido else (modo_actual or _("Predeterminado del agente"))
		cuando = _("Lo que quites se aplica desde el próximo mensaje.")
		if fila.proveedor == "codex":
			cuando = _("Con Codex es Ctrl+IA quien contesta por ti: lo que quites se aplica en la siguiente petición. "
				"Las reglas guardadas en los archivos de Codex son suyas y se cambian en Codex.")
		dialogo = DialogoPermisosSesion(
			self, fila.titulo, nombre_agente(fila.proveedor), nombre_modo, elegido.ayuda if elegido else "", reglas,
			fechas=dict((sesion.extra or {}).get("reglas_fechas") or {}), cuando=cuando,
			trabajando=fila.proveedor != "codex" and self.gestor.ocupada(fila.clave),
			reglas_agente=self._reglas_del_agente(fila),
		)
		try:
			resultado = dialogo.ShowModal()
			cambiado, finales, fechas = dialogo.cambiado, list(dialogo.reglas), dict(dialogo.fechas)
		finally:
			dialogo.Destroy()
		if cambiado:
			self.quitar_reglas(fila.clave, finales, fechas)
		if resultado == ID_CAMBIAR_MODO:
			self.accion_ajustes_sesion()

	def _reglas_del_agente(self, fila: FilaSesion) -> str:
		"""Las reglas de la propia configuración del agente para la carpeta de la sesión, para leerlas (AIChat #336)."""
		try:
			if fila.proveedor == "claude":
				if fila.remota:
					return _("Esta sesión trabaja en otro equipo: sus reglas de Claude Code están allí y no se leen desde aquí.")
				from app.services.config_agentes import reglas_claude
				from app.ui.dialogos.permisos_sesion import describir_regla

				encontradas, ilegibles = reglas_claude.reglas_de(fila.carpeta)
				return reglas_claude.describir(encontradas, ilegibles, describir_regla)
			if fila.proveedor == "codex" and not fila.remota:
				from app.services.config_agentes import reglas_codex

				return reglas_codex.describir(reglas_codex.archivos_de_reglas())
		except Exception:  # noqa: BLE001 - leer reglas ajenas nunca debe impedir abrir el diálogo
			log.warning("No se pudieron leer las reglas de la configuración del agente", exc_info=True)
			return _("No se han podido leer las reglas de la configuración del agente.")
		return ""

	def quitar_reglas(self, clave: str, reglas: list[str], fechas: dict[str, float]) -> None:
		"""Deja la sesión con solo ``reglas`` (en la base de datos y en la conversación viva)."""
		conversacion = self.gestor.conversacion(clave, crear=False)
		if conversacion is not None:
			# Se cambia la misma lista: Codex la consulta en cada petición.
			conversacion.ajustes.reglas_permitidas[:] = reglas
		sesion = self.almacen.sesion(clave)
		extra = dict(sesion.extra or {}) if sesion is not None else {}
		extra["reglas_fechas"] = {r: f for r, f in fechas.items() if r in reglas}
		self.almacen.actualizar_sesion(clave, reglas=list(reglas), extra=extra)
		log.info("Permisos de la sesión cambiados: quedan %d reglas", len(reglas))
		avisos.anunciar(_("Permisos guardados. Quedan {n} reglas en esta sesión.").format(n=len(reglas)) if reglas
			else _("Permisos guardados. No queda nada permitido más allá del modo de permisos."), interrumpir=True)

	# ------------------------------------------------------------------
	# Opciones de la sesión, estado y uso
	# ------------------------------------------------------------------

	def accion_ajustes_sesion(self) -> None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None or not fila.propia:
			mostrar_mensaje(self, _("Abre primero una sesión de Ctrl+IA. Las sesiones de otros programas no se pueden cambiar desde aquí."),
				_("Opciones de la sesión"))
			return
		from app.ui.dialogos.ajustes_sesion import DialogoAjustesSesion

		sesion = self.almacen.sesion(fila.clave)
		proveedor = self.gestor.proveedor(fila.proveedor)
		if sesion is None or proveedor is None:
			return
		dialogo = DialogoAjustesSesion(self, proveedor, sesion)
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			cambios = dialogo.cambios
		finally:
			dialogo.Destroy()
		if not cambios:
			return
		self.gestor.cambiar_ajustes(fila.clave, **cambios)
		elegidas = {clave: cambios[clave] for clave in ("modelo", "esfuerzo", "estilo") if clave in cambios}
		if elegidas:
			from app.ui.dialogos.nueva_sesion import recordar_opciones

			# La próxima sesión nueva con este agente propone lo último que elegiste.
			recordar_opciones(self.config, fila.proveedor, **elegidas)
		if "titulo" in cambios:
			fila.titulo = cambios["titulo"]
		if "modelo" in cambios:
			fila.modelo = cambios["modelo"]
		self.mostrar_sesiones(False)
		self.actualizar_titulo_ventana()
		self.actualizar_barra_estado()
		avisos.anunciar(_("Opciones guardadas. Se aplican desde el próximo mensaje."), interrumpir=True)

	def accion_herramientas_agente(self) -> None:
		"""Ctrl+Alt+H: lo que tiene cargado el agente de la sesión (AIChat #311). Pregunta en segundo plano."""
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None or not fila.propia:
			mostrar_mensaje(self, _("Abre primero una sesión de Ctrl+IA: las de otros programas no se pueden consultar desde "
				"aquí."), _("Herramientas del agente"))
			return
		conversacion = self.gestor.conversacion(fila.clave)
		if conversacion is None:
			return
		avisos.anunciar(_("Preguntando a {a} qué tiene cargado...").format(a=nombre_agente(fila.proveedor)), interrumpir=True)

		def mostrar(inventario: Any) -> None:
			from app.services import inventario as modulo
			from app.ui.dialogo_texto import mostrar_texto

			if inventario is None:
				mostrar_mensaje(self, _("{a} no dice qué herramientas tiene cargadas.").format(a=nombre_agente(fila.proveedor)),
					_("Herramientas del agente"))
				return
			mostrar_texto(self, _("Herramientas del agente"), _("&Herramientas de la sesión:"),
				modulo.describir(inventario, fila.titulo), copiable=True)

		def fallo(error: BaseException) -> None:
			mostrar_error(self, _("No se ha podido saber qué tiene cargado el agente: {e}").format(e=error),
				_("Herramientas del agente"))

		self.en_segundo_plano(conversacion.inventario, al_terminar=mostrar, al_fallar=fallo, clave="inventario")

	def accion_estado_turno(self) -> None:
		if not self.sesion_actual:
			avisos.anunciar(_("No hay ninguna sesión abierta."), interrumpir=True)
			return
		fila = self.fila_de(self.sesion_actual)
		turno = self._turnos.get(self.sesion_actual, {})
		partes = []
		if self.gestor.ocupada(self.sesion_actual):
			partes.append(_("El agente está trabajando desde hace {d}.").format(d=duracion(time.time() - turno.get("inicio", time.time()))))
			if turno.get("herramienta"):
				partes.append(_("Última acción: {h}.").format(h=turno["herramienta"]))
		else:
			partes.append(_("Estado: {e}.").format(e=nombre_estado(fila.estado if fila else "inactiva")))
		conversacion = self.gestor.conversacion(self.sesion_actual, crear=False)
		if conversacion is not None:
			cola = getattr(conversacion, "_cola", None)
			if cola:
				partes.append(_("{n} mensajes en cola.").format(n=len(cola)))
			anadidos = getattr(conversacion, "anadidos_pendientes", 0)
			if anadidos:
				partes.append(_("{n} mensajes añadidos al turno, esperando a que el agente confirme que los ha recibido.").format(
					n=anadidos))
		pendientes = self.gestor.solicitudes(self.sesion_actual)
		if pendientes:
			partes.append(_("Te espera una respuesta: pulsa Control+Mayúsculas+A."))
		from app.services.extraccion import resumen_tareas

		progreso = resumen_tareas(self._tareas_de(self.sesion_actual))
		if progreso:
			partes.append(progreso)
		frase_pr = self.frase_pr(fila) if fila is not None and hasattr(self, "frase_pr") else ""
		if frase_pr:
			partes.append(frase_pr)
		avisos.anunciar(" ".join(partes), interrumpir=True)

	# ------------------------------------------------------------------
	# Lista de tareas y hallazgos de revisión del agente
	# ------------------------------------------------------------------

	def _mensajes_para_listas(self, clave: str) -> list[MensajeVista]:
		"""La conversación entera con las acciones del agente (aunque estén ocultas en la lista)."""
		if clave.startswith("ctrlia:"):
			return [self._vista_de(m) for m in self.almacen.mensajes(clave)]
		if clave == self.sesion_actual:
			return self.mensajes_completos()
		return []

	def _tareas_de(self, clave: str) -> list[Any]:
		from app.services.extraccion import tareas

		try:
			return tareas(self._mensajes_para_listas(clave))
		except Exception:  # noqa: BLE001 - leer las tareas nunca debe romper la conversación
			log.exception("No se pudo leer la lista de tareas")
			return []

	def _revisar_tareas(self, clave: str, fila: FilaSesion | None, datos: dict[str, Any]) -> None:
		"""Si el agente acaba de dar tareas por hechas, lo dice (si está activado en Preferencias)."""
		from app.services.extraccion import anuncio_cambio_tareas, tareas_de_pasos

		despues = self._tareas_de(clave)
		if not despues and datos.get("pasos"):
			despues = tareas_de_pasos(datos["pasos"])
		antes = self._tareas.get(clave)
		self._tareas[clave] = despues
		if antes is None or not self.opcion("anuncios.tareas", False):
			return
		if self.opcion("anuncios.nivel", "completo") != "completo" or not self._debe_anunciar(clave):
			return
		texto = anuncio_cambio_tareas(antes, despues)
		if texto:
			# Sin interrumpir: nunca corta una respuesta que se esté leyendo.
			avisos.anunciar(self._prefijo(clave, fila) + texto)

	def _anunciar_hallazgos(self, clave: str, fila: FilaSesion | None) -> None:
		"""Al terminar un turno que ha informado de hallazgos de revisión: «Revisión: 5 hallazgos, 2 confirmados»."""
		from app.services.extraccion import hallazgos, resumen_hallazgos

		try:
			mensajes = self._mensajes_para_listas(clave)
			inicio = max((i for i, m in enumerate(mensajes) if m.rol == "usuario"), default=0)
			texto = resumen_hallazgos(hallazgos(mensajes[inicio:]))
		except Exception:  # noqa: BLE001 - un formato raro no debe romper el fin de turno
			log.exception("No se pudieron leer los hallazgos de la revisión")
			return
		if texto:
			avisos.anunciar(self._prefijo(clave, fila) + texto)

	def accion_limites(self) -> None:
		"""Herramientas, «Límites de uso de tus cuentas» (Ctrl+Mayús+Y): 5 horas y semana de cada agente."""
		avisos.anunciar(_("Preguntando tus límites a los agentes. Tarda unos segundos..."))

		def mostrar(resultados) -> None:
			from app.services import limites as servicio_limites
			from app.ui import visor_ayuda

			titulo = _("Límites de uso de tus cuentas")
			anterior = visor_ayuda._abiertos.get(titulo)
			if anterior:
				anterior.Close()  # el de la consulta anterior tiene datos viejos
			visor_ayuda.mostrar_documento(self, titulo, servicio_limites.documento(resultados))

		self.en_segundo_plano(self.gestor.leer_limites_todos, al_terminar=mostrar, clave="limites")

	def accion_uso(self) -> None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else self.fila_seleccionada()
		if fila is None:
			avisos.anunciar(_("No hay ninguna sesión elegida."), interrumpir=True)
			return
		uso = self.gestor.uso(fila.clave)
		lineas = ["# " + _("Uso de «{t}»").format(t=fila.titulo), ""]
		usado, total = uso.get("contexto_usado"), uso.get("contexto_total")
		if usado and total:
			lineas.append(_("- Contexto: {p} % usado ({u} de {t} unidades de texto).").format(p=round(100 * usado / total), u=usado, t=total))
		elif usado:
			lineas.append(_("- Contexto usado: {u} unidades de texto.").format(u=usado))
		else:
			lineas.append(_("- Todavía no hay datos de contexto: aparecen tras la primera respuesta."))
		if uso.get("entrada") or uso.get("salida"):
			lineas.append(_("- Último turno: {e} de entrada y {s} de salida.").format(e=uso.get("entrada", 0), s=uso.get("salida", 0)))
		if uso.get("coste"):
			lineas.append(_("- Coste equivalente del turno: {c} dólares (con suscripción no se te cobra aparte).").format(c=uso["coste"]))
		from app.services import limites as servicio_limites

		cuenta = self.gestor.limites_cuenta(fila.proveedor)
		limites = self.gestor.limites(fila.proveedor)
		if cuenta is not None or limites:
			lineas.append("")
			lineas.append("## " + _("Límites de tu cuenta de {a}").format(a=nombre_agente(fila.proveedor)))
			if cuenta is not None:
				lineas += ["- " + linea for linea in servicio_limites.lineas(cuenta)]
			else:
				lineas += ["- " + str(datos.get("texto") or "") for datos in limites.values()]
		lineas.append("")
		lineas.append(_("Para preguntar ahora tus límites de 5 horas y semanales de todos los agentes: Herramientas, "
			"«Límites de uso de tus cuentas» (Control+Mayúsculas+Y)."))
		lineas.append("")
		lineas.append(_("Cuando el contexto se llena, el agente resume la conversación (la «compacta») y sigue trabajando."))
		from app.ui.visor_ayuda import mostrar_documento

		mostrar_documento(self, _("Uso de «{t}»").format(t=fila.titulo), "\n".join(lineas), forzar_texto=True)

	# ------------------------------------------------------------------
	# Palabras curiosas en la barra de estado (#314)
	# ------------------------------------------------------------------

	def _texto_escribiendo(self, agente: str) -> str:
		"""«Claude está escribiendo...» o, con la opción de Anuncios, «Claude: Cavilando…» (y arranca el cambio)."""
		if not self.opcion("anuncios.palabras_curiosas", False):
			self._texto_curioso = ""
			return _("{a} está escribiendo...").format(a=agente)
		from app.ui import palabras_curiosas

		self._palabra_curiosa = palabras_curiosas.siguiente(self._palabra_curiosa)
		self._agente_curioso = agente
		self._texto_curioso = _("{a}: {p}").format(a=agente, p=self._palabra_curiosa)
		if not self._temporizador_palabras.IsRunning():
			self._temporizador_palabras.Start(palabras_curiosas.INTERVALO_MS)
		return self._texto_curioso

	def _cambiar_palabra_curiosa(self) -> None:
		"""Cada pocos segundos, otra palabra; solo si la barra sigue diciendo la anterior (nada se pisa)."""
		if not self or getattr(self, "_saliendo", False):
			self._temporizador_palabras.Stop()
			return
		turno = self._turnos.get(self.sesion_actual) or {}
		barra = self.GetStatusBar()
		if (not self._texto_curioso or not turno.get("escribiendo") or barra is None
				or barra.GetStatusText(0) != self._texto_curioso):
			# Terminó, cambió de sesión o la barra ya dice otra cosa (una acción, el estado): se para.
			self._temporizador_palabras.Stop()
			self._texto_curioso = ""
			return
		self.poner_estado(self._texto_escribiendo(self._agente_curioso), 0)

	def estado_para_leer(self, texto: str) -> str:
		"""Lo que dice Alt+4 de la primera parte de la barra: nunca la palabra curiosa, sino «está escribiendo»."""
		if self._texto_curioso and texto == self._texto_curioso:
			return _("{a} está escribiendo...").format(a=self._agente_curioso)
		return texto

	def actualizar_barra_estado(self) -> None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None:
			self.poner_estado(_("Ninguna sesión abierta"), 0)
			self.poner_estado("", 1)
			self.poner_estado("", 2)
			return
		estado = nombre_estado(fila.estado)
		if self.gestor.solicitudes(fila.clave):
			estado = _("Te necesita (Control+Mayúsculas+A)")
		self.poner_estado(_("{t}: {e}").format(t=fila.titulo, e=estado), 0)
		self.poner_estado(_("{a}, modelo {m}").format(a=nombre_agente(fila.proveedor), m=fila.modelo or _("predeterminado")), 1)
		uso = self.gestor.uso(fila.clave)
		usado, total = uso.get("contexto_usado"), uso.get("contexto_total")
		self.poner_estado(_("Contexto: {p} %").format(p=round(100 * usado / total)) if usado and total else "", 2)

	# ------------------------------------------------------------------
	# Eventos de los agentes
	# ------------------------------------------------------------------

	def _evento_desde_hilo(self, clave: str, evento: Evento) -> None:
		"""Receptor del gestor: llega desde hilos de fondo."""
		wx.CallAfter(self._al_evento_seguro, clave, evento)

	def _al_evento_seguro(self, clave: str, evento: Evento) -> None:
		if not self or self._saliendo:
			return
		try:
			self._al_evento(clave, evento)
		except Exception:  # noqa: BLE001 - un evento mal tratado no debe cerrar la ventana
			log.exception("Error al procesar el evento %s", evento.tipo)

	def _vista_de(self, guardado: Any) -> MensajeVista:
		return MensajeVista(guardado.rol, guardado.texto, guardado.fecha, guardado.detalle, guardado.herramienta,
			guardado.id, list(guardado.datos.get("cambios", [])), self.gestor.datos_para_vista(guardado.sesion, guardado.datos))

	def _refrescar_fila_mensaje(self, clave: str, id_mensaje: int) -> None:
		if clave != self.sesion_actual or not id_mensaje:
			return
		for guardado in self.almacen.mensajes(clave, id_mensaje - 1):
			if guardado.id == id_mensaje:
				if guardado.rol in ("herramienta", "razonamiento") and not self.opcion("mensajes.herramientas", True):
					return
				self.anadir_o_actualizar_mensaje(self._vista_de(guardado))
				return

	def _debe_anunciar(self, clave: str) -> bool:
		return clave == self.sesion_actual or bool(self.opcion("anuncios.todas_las_sesiones", False))

	def _prefijo(self, clave: str, fila: FilaSesion | None) -> str:
		if clave == self.sesion_actual or fila is None:
			return ""
		return _("En «{t}»: ").format(t=fila.titulo)

	def _al_evento(self, clave: str, evento: Evento) -> None:
		tipo, datos = evento.tipo, evento.datos
		fila = self.fila_de(clave)
		agente = nombre_agente(fila.proveedor) if fila else _("El agente")
		turno = self._turnos.setdefault(clave, {"inicio": time.time(), "herramienta": "", "escribiendo": False})
		if datos.get("id_mensaje"):
			self._refrescar_fila_mensaje(clave, int(datos["id_mensaje"]))

		if tipo is TipoEvento.ESTADO:
			if fila is not None:
				fila.estado = str(datos.get("estado") or fila.estado)
				fila.actividad = time.time()
				self.mostrar_sesiones(False)
			if clave == self.sesion_actual:
				self.actualizar_barra_estado()
		elif tipo is TipoEvento.INICIADO:
			if fila is not None and datos.get("id_externo"):
				fila.id_externo = str(datos["id_externo"])
			if fila is not None and datos.get("modelo") and not fila.modelo:
				# Sin modelo elegido, se muestra el que el agente dice estar usando.
				fila.modelo = str(datos["modelo"])
				if clave == self.sesion_actual:
					self.actualizar_barra_estado()
		elif tipo is TipoEvento.TEXTO_PARCIAL:
			if clave == self.sesion_actual and not turno.get("escribiendo"):
				turno["escribiendo"] = True
				self.poner_estado(self._texto_escribiendo(agente), 0)
		elif tipo is TipoEvento.MENSAJE:
			turno["escribiendo"] = False
			self._texto_reciente[clave] = evento.texto
			if self._debe_anunciar(clave) and self.opcion("anuncios.nivel", "completo") == "completo":
				identificador = (clave, datos.get("id") or datos.get("id_mensaje"))
				ya = self._anunciado.get(identificador, 0)
				nuevo = evento.texto[ya:].strip()
				self._anunciado[identificador] = len(evento.texto)
				if nuevo:
					avisos.anunciar(self._prefijo(clave, fila) + nuevo[:MAX_ANUNCIO])
		elif tipo is TipoEvento.HERRAMIENTA:
			turno["herramienta"] = evento.texto
			if clave == self.sesion_actual:
				self.poner_estado(evento.texto, 0)
			if self._debe_anunciar(clave) and self.opcion("anuncios.herramientas", False):
				avisos.anunciar(self._prefijo(clave, fila) + evento.texto[:300])
		elif tipo is TipoEvento.HERRAMIENTA_FIN:
			self._texto_reciente.pop(clave, None)
			if datos.get("herramienta") in ("TaskCreate", "TaskUpdate", "TodoWrite"):
				self._revisar_tareas(clave, fila, datos)
		elif tipo is TipoEvento.PLAN:
			self._revisar_tareas(clave, fila, datos)
		elif tipo is TipoEvento.PERMISO:
			solicitud: SolicitudPermiso = datos["solicitud"]
			if not solicitud.contexto:
				# Agentes que no lo adjuntan: lo último que escribió desde su última acción.
				solicitud.contexto = self._texto_reciente.get(clave, "")
			if self._peticion_repetida(clave, solicitud):
				# Varias llamadas en paralelo a la misma herramienta: se avisa una vez; al responder
				# la primera con una regla, el agente retira las demás.
				if clave == self.sesion_actual:
					self.actualizar_barra_estado()
			else:
				self._avisar_peticion(clave, fila, agente, solicitud)
		elif tipo is TipoEvento.AVISO and datos.get("solicitud") is not None:
			self._avisar_peticion(clave, fila, agente, datos["solicitud"])
		elif tipo is TipoEvento.AVISO and datos.get("devolver_texto"):
			self._texto_no_entregado(clave, fila, str(datos["devolver_texto"]), evento.texto)
		elif tipo is TipoEvento.AVISO and datos.get("herramienta_ctrlia"):
			self._herramienta_ctrlia(clave, fila, agente, evento)
		elif tipo is TipoEvento.AVISO:
			if datos.get("compactado"):
				avisos.sonar("compactado")
			if self._debe_anunciar(clave) and not datos.get("en_cola"):
				avisos.anunciar(self._prefijo(clave, fila) + evento.texto)
		elif tipo is TipoEvento.PERMISO_RETIRADO:
			if clave == self.sesion_actual:
				self.actualizar_barra_estado()
		elif tipo is TipoEvento.ERROR:
			avisos.sonar("error")
			avisos.notificar(self._prefijo(clave, fila) + evento.texto, _("Error de {a}").format(a=agente), padre=self)
			if self.opcion("notificaciones.modo", "te_necesita") != "nunca":
				avisos.notificacion_sistema(_("Error de {a}").format(a=agente), evento.texto[:200])
		elif tipo is TipoEvento.USO:
			self._comprobar_contexto(clave, fila)
			if clave == self.sesion_actual:
				self.actualizar_barra_estado()
		elif tipo is TipoEvento.LIMITES:
			porcentaje = int(datos.get("porcentaje") or 0)
			umbral = int(self.opcion("anuncios.aviso_limite", 90))
			marca = (fila.proveedor if fila else clave, str(datos.get("ventana")), porcentaje // 5)
			if umbral and porcentaje >= umbral and marca not in self._avisos_uso:
				self._avisos_uso.add(marca)
				avisos.sonar("limite")
				avisos.notificar(evento.texto, _("Límite de uso"), padre=self)
		elif tipo is TipoEvento.FIN_TURNO:
			self._al_fin_turno(clave, fila, agente, evento)
		if "_conversacion_sesiones" in self.__dict__:
			from app.ui.dialogos import dialogo_agentes

			# Después de lo de siempre: el aviso del turno siguiente va tras el de la respuesta.
			dialogo_agentes.al_evento(self, clave, evento)

	def _herramienta_ctrlia(self, clave: str, fila: FilaSesion | None, agente: str, evento: Evento) -> None:
		"""El agente ha usado una herramienta de Ctrl+IA (``herramientas_propias``); el aviso ya es un mensaje de sistema."""
		datos = evento.datos
		accion = datos.get("herramienta_ctrlia")
		texto = self._prefijo(clave, fila) + evento.texto
		if accion == "avisar":
			titulo = _("Aviso de {a}").format(a=agente)
			if datos.get("importante"):
				avisos.notificar(texto, titulo, padre=self)
			else:
				avisos.anunciar(texto)
			if not avisos.aplicacion_activa():
				avisos.sonar("permiso")
				avisos.notificacion_sistema(titulo, texto[:200])
		elif accion == "dar_por_terminada":
			avisos.anunciar(texto)
			if self.gestor.ocupada(clave):
				# Se aplica al acabar el turno: la actividad de este turno la volvería a activar.
				self.__dict__.setdefault("_terminar_al_acabar", set()).add(clave)
			else:
				self._terminar_por_agente(clave)
		elif accion == "renombrar_sesion":
			titulo_nuevo = str(datos.get("titulo") or "").strip()
			if titulo_nuevo and clave.startswith("ctrlia:"):
				self.gestor.renombrar(clave, titulo_nuevo)
				if fila is not None:
					fila.titulo = titulo_nuevo
				self.mostrar_sesiones(False)
				if clave == self.sesion_actual:
					self.actualizar_titulo_ventana()
					self.actualizar_barra_estado()
			avisos.anunciar(texto)

	def _terminar_por_agente(self, clave: str) -> None:
		"""Da la sesión por terminada porque lo pidió su agente (como Control+D)."""
		if not self or getattr(self, "_saliendo", False):
			return
		self.__dict__.setdefault("_terminar_al_acabar", set()).discard(clave)
		self.gestor.marcar_terminada(clave, True)
		fila = self.fila_de(clave)
		if fila is not None:
			fila.terminada = True
			fila.no_leidas = 0
		self.mostrar_sesiones(False)

	def _texto_no_entregado(self, clave: str, fila: FilaSesion | None, texto: str, motivo: str) -> None:
		"""Un mensaje que no llegó al agente vuelve al cuadro de respuesta (nunca se reenvía solo)."""
		avisos.sonar("error")
		if clave == self.sesion_actual:
			self._devolver_al_cuadro(texto)
			avisos.anunciar(motivo, interrumpir=True)
			return
		self._devueltos.setdefault(clave, []).append(texto)
		titulo = fila.titulo if fila else ""
		avisos.notificar(_("En «{t}»: {m} Al abrir la sesión lo encontrarás en el cuadro de respuesta.").format(t=titulo, m=motivo),
			_("Mensaje no entregado"), padre=self)

	def _peticion_repetida(self, clave: str, solicitud: SolicitudPermiso) -> bool:
		"""Si esa sesión espera otra petición igual (misma herramienta y título) que ya se avisó."""
		# (sesión, id): Codex numera sus peticiones desde 0 en cada servidor.
		avisadas: set[tuple[str, str]] = self.__dict__.setdefault("_peticiones_avisadas", set())
		avisadas.intersection_update({(c, otra.id) for c, otra in self.gestor.solicitudes()})
		repetida = any(
			otra.id != solicitud.id and (clave, otra.id) in avisadas
			and otra.herramienta == solicitud.herramienta and otra.titulo == solicitud.titulo
			for _clave, otra in self.gestor.solicitudes(clave)
		)
		if not repetida:
			avisadas.add((clave, solicitud.id))
		return repetida

	def _avisar_peticion(self, clave: str, fila: FilaSesion | None, agente: str, solicitud: SolicitudPermiso) -> None:
		avisos.sonar("permiso")
		titulo = fila.titulo if fila else ""
		texto = _("{a} te necesita en «{s}»: {t}. Pulsa Control+Mayúsculas+A para responder.").format(
			a=agente, s=titulo, t=solicitud.titulo)
		avisos.notificar(texto, _("{a} te necesita").format(a=agente), padre=self)
		if self.opcion("notificaciones.modo", "te_necesita") != "nunca":
			avisos.notificacion_sistema(_("{a} te necesita").format(a=agente), "{s}: {t}".format(s=titulo, t=solicitud.titulo))
		if clave == self.sesion_actual:
			self.actualizar_barra_estado()

	def _comprobar_contexto(self, clave: str, fila: FilaSesion | None) -> None:
		uso = self.gestor.uso(clave)
		usado, total = uso.get("contexto_usado"), uso.get("contexto_total")
		umbral = int(self.opcion("anuncios.aviso_contexto", 80))
		if not (usado and total and umbral):
			return
		porcentaje = round(100 * usado / total)
		marca = (clave, "contexto", 1)
		if porcentaje >= umbral and marca not in self._avisos_uso:
			self._avisos_uso.add(marca)
			avisos.notificar(_("{p} % del contexto de «{t}» usado. Cuando se llene, el agente resumirá la conversación.").format(
				p=porcentaje, t=fila.titulo if fila else ""), _("Contexto"), padre=self)

	def _al_fin_turno(self, clave: str, fila: FilaSesion | None, agente: str, evento: Evento) -> None:
		datos = evento.datos
		turno = self._turnos.get(clave, {})
		turno["escribiendo"] = False
		self._texto_reciente.pop(clave, None)
		correcto = bool(datos.get("correcto"))
		if datos.get("interrumpido"):
			avisos.anunciar(self._prefijo(clave, fila) + _("{a} se ha detenido.").format(a=agente))
		elif not correcto and clave == self.sesion_actual and fila is not None and fila.propia:
			avisos.anunciar(_("Tu mensaje sigue en la conversación. Para repetirlo, elige «Volver a enviar» en su menú "
				"contextual de la lista de mensajes."))
		elif correcto:
			avisos.sonar("respuesta")
			nivel = self.opcion("anuncios.nivel", "completo")
			if nivel == "resumen" or (nivel == "completo" and not self._debe_anunciar(clave)):
				avisos.anunciar(_("{a} ha respondido en «{t}».").format(a=agente, t=fila.titulo if fila else ""))
			if self.opcion("notificaciones.modo", "te_necesita") == "todas":
				avisos.notificacion_sistema(_("{a} ha terminado").format(a=agente), fila.titulo if fila else "")
			if nivel != "silencio" and self._debe_anunciar(clave):
				self._anunciar_hallazgos(clave, fila)
		if fila is not None:
			if clave != self.sesion_actual:
				fila.no_leidas += 1
			fila.actividad = time.time()
		if clave == self.sesion_actual:
			self.actualizar_barra_estado()
		if clave in self.__dict__.get("_terminar_al_acabar", ()):
			# El agente la dio por terminada durante el turno: después de su última actividad.
			wx.CallLater(1500, self._terminar_por_agente, clave)
		wx.CallLater(400, self.refrescar_sesiones)
