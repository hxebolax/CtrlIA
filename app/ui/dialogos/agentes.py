"""«Agentes instalados»: qué agentes hay en el equipo y cómo instalarlos.

Una fila por agente con su estado (instalado o no), versión, sesión y un
detalle. La comprobación puede tardar varios segundos (cada agente ejecuta
``--version``), así que se hace en un hilo de fondo, agente por agente, y la
lista se va rellenando; al terminar se anuncia «Comprobación terminada».

Debajo, un campo de solo lectura con la descripción del agente seleccionado,
cómo instalarlo y su página oficial.

Botones imprescindibles: Instalar, Iniciar sesión, Comprobar de nuevo (F5) y
Cerrar. El resto (página oficial; para Antigravity, reglas de permisos y abrir
el IDE o la app) está en el menú contextual de la lista (tecla Aplicaciones)
y en el botón «Más acciones».
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from collections.abc import Sequence
from typing import Any

import wx

from app.services.agentes import antigravity, procesos
from app.services.agentes.base import EstadoProveedor
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import confirmar, mostrar_error, mostrar_mensaje, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import instalar_atajos, vincular_menu_contextual
from app.ui.comun import _, nombre_agente

log = logging.getLogger(__name__)

URL_ANTIGRAVITY = "https://antigravity.google"

# Columnas de la lista.
COL_AGENTE, COL_ESTADO, COL_VERSION, COL_SESION, COL_DETALLE = range(5)


def texto_estado(estado: EstadoProveedor | None, comprobando: bool = False) -> str:
	"""«Comprobando…», «Instalado», «No instalado» o «No se pudo comprobar»."""
	if comprobando:
		return _("Comprobando…")
	if estado is None:
		return _("No se pudo comprobar")
	return _("Instalado") if estado.disponible else _("No instalado")


def texto_sesion(estado: EstadoProveedor | None) -> str:
	"""«Iniciada», «No iniciada» o «Desconocida» (con la cuenta, si se conoce)."""
	if estado is None or estado.sesion_iniciada is None:
		return _("Desconocida")
	if estado.sesion_iniciada:
		return _("Iniciada: {cuenta}").format(cuenta=estado.cuenta) if estado.cuenta else _("Iniciada")
	return _("No iniciada")


def es_antigravity(identificador: str, proveedor: Any) -> bool:
	"""Si la fila es de Antigravity (tiene reglas de permisos, IDE y app)."""
	return identificador == "antigravity" or (
		identificador.startswith("antigravity") and hasattr(proveedor, "leer_reglas")
	)


class DialogoAgentes(wx.Dialog):
	"""Lista de agentes con su estado y acciones para instalarlos o abrirlos.

	:param gestor: objeto con ``proveedores`` (``{id: Proveedor}``) y
		``detectar(id) -> EstadoProveedor | None`` (bloqueante); normalmente
		``GestorSesiones``.
	:param comprobar_al_abrir: lanza la detección en un hilo nada más crearse.
	"""

	def __init__(self, padre: wx.Window | None, gestor: Any, comprobar_al_abrir: bool = True) -> None:
		super().__init__(
			padre, title=_("Agentes instalados"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
			name=_("Agentes instalados"),
		)
		self.gestor = gestor
		self.ids: list[str] = list(getattr(gestor, "proveedores", {}).keys())
		self.estados: dict[str, EstadoProveedor | None] = {}
		self.pendientes: set[str] = set()
		self.comprobando = False
		self._generacion = 0
		#: Agente del que se dice cómo está su sesión al acabar la comprobación (tras cerrar su consola).
		self._anunciar_sesion: str | None = None

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		etiqueta = wx.StaticText(panel, label=_("&Agentes:"))
		self.lista = ListaAccesible(
			panel, _("Agentes"),
			columnas=[(_("Agente"), 200), (_("Estado"), 120), (_("Versión"), 140), (_("Sesión"), 130), (_("Detalle"), 380)],
		)
		self.lista.SetHelpText(_(
			"Agentes de programación que Ctrl+IA sabe usar y si están instalados en este equipo. Arriba y abajo "
			"cambian de agente; izquierda y derecha leen las columnas. Intro o la tecla Aplicaciones abren las "
			"acciones del agente: instalar, iniciar sesión, página oficial y, para Antigravity, sus reglas de "
			"permisos y el IDE o la app. F5 vuelve a comprobar."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(760, 200)))
		raiz.Add(etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		_etiqueta, self.descripcion = crear_campo(
			panel, _("&Descripción:"), wx.TextCtrl, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			ayuda=_("Qué es el agente seleccionado, cómo instalarlo y su página oficial. Es de solo lectura."),
			sizer=raiz,
		)
		self.descripcion.SetMinSize(self.FromDIP(wx.Size(760, 150)))

		fila = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_instalar = wx.Button(panel, label=_("&Instalar"))
		self.boton_instalar.SetHelpText(_(
			"Instala el agente seleccionado: explica lo que va a pasar y, si aceptas, abre una consola que lo "
			"descarga e instala. Cuando la consola se cierre, Ctrl+IA vuelve a comprobarlo solo."
		))
		self.boton_sesion = wx.Button(panel, label=_("Iniciar &sesión"))
		self.boton_sesion.SetHelpText(_(
			"Abre el agente seleccionado en una consola nueva para que inicies sesión con tu cuenta. "
			"Cuando la consola se cierre, Ctrl+IA vuelve a comprobarlo solo y te dice si la sesión quedó iniciada."
		))
		self.boton_comprobar = wx.Button(panel, label=_("Comprobar de &nuevo (F5)"))
		self.boton_comprobar.SetHelpText(_("Vuelve a buscar todos los agentes en el equipo, por ejemplo después de instalar uno."))
		self.boton_mas = wx.Button(panel, label=_("Más acci&ones…"))
		self.boton_mas.SetHelpText(_(
			"Abre el menú con todas las acciones del agente seleccionado: página oficial y, para Antigravity, "
			"reglas de permisos y abrir el IDE o la app. Es el mismo menú de la tecla Aplicaciones."
		))
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra esta ventana. También con Escape."))
		for boton in (self.boton_instalar, self.boton_sesion, self.boton_comprobar, self.boton_mas):
			fila.Add(boton, 0, wx.RIGHT, 6)
		fila.AddStretchSpacer()
		fila.Add(self.boton_cerrar, 0)
		raiz.Add(fila, 0, wx.EXPAND | wx.ALL, 8)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)

		self.boton_instalar.Bind(wx.EVT_BUTTON, lambda _e: self.instalar())
		self.boton_sesion.Bind(wx.EVT_BUTTON, lambda _e: self.iniciar_sesion())
		self.boton_comprobar.Bind(wx.EVT_BUTTON, lambda _e: self.comprobar())
		self.boton_mas.Bind(wx.EVT_BUTTON, lambda _e: self._mostrar_menu(self.boton_mas))
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self._mostrar_menu(self.lista))
		self.lista.Bind(wx.EVT_LIST_ITEM_FOCUSED, lambda e: (self.al_cambiar_fila(), e.Skip()))
		self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: (self.al_cambiar_fila(), e.Skip()))
		vincular_menu_contextual(self.lista, self.crear_menu)
		instalar_atajos(self, [(wx.ACCEL_NORMAL, wx.WXK_F5, self.comprobar)])

		self.rellenar_filas()
		preparar_dialogo(self, foco_inicial=self.lista)
		panel.Layout()
		self.CentreOnParent()
		if comprobar_al_abrir:
			self.comprobar()

	# -- Filas -------------------------------------------------------------------

	def proveedor(self, identificador: str) -> Any:
		"""Proveedor de un identificador (o ``None``)."""
		return getattr(self.gestor, "proveedores", {}).get(identificador)

	def nombre(self, identificador: str) -> str:
		"""Nombre visible del agente."""
		proveedor = self.proveedor(identificador)
		return getattr(proveedor, "nombre", "") or nombre_agente(identificador)

	def _valores(self, identificador: str) -> list[str]:
		comprobando = identificador in self.pendientes
		estado = self.estados.get(identificador)
		if comprobando:
			return [self.nombre(identificador), texto_estado(None, True), "", "", ""]
		if identificador not in self.estados:
			return [self.nombre(identificador), _("Sin comprobar"), "", "", _("Pulsa F5 para comprobarlo.")]
		if estado is None:
			detalle = _("Hubo un error al buscar este agente. Puedes volver a intentarlo con F5.")
			return [self.nombre(identificador), texto_estado(None), "", texto_sesion(None), detalle]
		version = estado.version or (_("Desconocida") if estado.disponible else "")
		return [self.nombre(identificador), texto_estado(estado), version, texto_sesion(estado), estado.mensaje]

	def rellenar_filas(self) -> None:
		"""Una fila por agente con lo que se sabe ahora mismo (o «Comprobando…»)."""
		filas = [self._valores(identificador) for identificador in self.ids]
		if self.lista.numero_filas() == len(filas):
			# Celda a celda: solo se repinta lo que cambia (el lector no relee la fila enfocada).
			for indice, valores in enumerate(filas):
				self.lista.actualizar_fila(indice, valores)
		else:
			self.lista.reemplazar_filas(filas)
		self.al_cambiar_fila()

	def poner_estado(self, identificador: str, estado: EstadoProveedor | None) -> None:
		"""Guarda el resultado de detectar un agente y actualiza su fila."""
		self.pendientes.discard(identificador)
		self.estados[identificador] = estado
		if identificador in self.ids:
			self.lista.actualizar_fila(self.ids.index(identificador), self._valores(identificador))
		if self.seleccionado() == identificador:
			self.al_cambiar_fila()

	def terminar_comprobacion(self) -> None:
		"""Fin de la detección: reactiva el botón y lo anuncia."""
		self.comprobando = False
		self.pendientes.clear()
		self.rellenar_filas()
		instalados = sum(1 for estado in self.estados.values() if estado is not None and estado.disponible)
		identificador, self._anunciar_sesion = self._anunciar_sesion, None
		if identificador is not None:
			voz.hablar(self.texto_tras_consola(identificador))
			return
		voz.hablar(
			_("Comprobación terminada. {n} de {total} agentes instalados.").format(n=instalados, total=len(self.ids))
		)

	def texto_tras_consola(self, identificador: str) -> str:
		"""Lo que se dice al volver a comprobar un agente después de cerrar su consola."""
		nombre = self.nombre(identificador)
		estado = self.estados.get(identificador)
		if estado is None or not estado.disponible:
			return _("{n} sigue sin estar instalado. Si la consola dio un error, vuelve a intentarlo.").format(n=nombre)
		if estado.sesion_iniciada:
			texto = _("{n}: sesión iniciada.").format(n=nombre)
			if estado.cuenta:
				texto = _("{n}: sesión iniciada con {c}.").format(n=nombre, c=estado.cuenta)
		elif estado.sesion_iniciada is False:
			texto = _("{n} está instalado, pero la sesión no está iniciada. Pulsa «Iniciar sesión».").format(n=nombre)
		else:
			texto = _("{n} está instalado.").format(n=nombre)
		return texto + (" " + estado.mensaje if estado.mensaje else "")

	def _al_cerrar_consola(self, identificador: str) -> None:
		"""La consola de instalar o iniciar sesión se ha cerrado: se comprueba otra vez y se dice cómo quedó."""
		if not self:
			return
		self._anunciar_sesion = identificador
		if not self.comprobando:
			self.comprobar()

	# -- Detección en segundo plano -----------------------------------------------

	def comprobar(self) -> None:
		"""Lanza la detección de todos los agentes en un hilo; las filas se rellenan al llegar."""
		if self.comprobando:
			voz.hablar(_("Ya se está comprobando"))
			return
		self.comprobando = True
		self._generacion += 1
		self.pendientes = set(self.ids)
		self.rellenar_filas()
		hilo = threading.Thread(
			target=_detectar_todos, args=(self, self.gestor, self._generacion, list(self.ids)),
			name="detectar-agentes", daemon=True,
		)
		hilo.start()

	def _si_vigente(self, generacion: int, funcion: Any, *argumentos: Any) -> None:
		"""Ejecuta ``funcion`` solo si la comprobación ``generacion`` sigue siendo la última."""
		_si_vigente(self, generacion, funcion.__name__, *argumentos)

	# -- Selección ---------------------------------------------------------------

	def seleccionado(self) -> str | None:
		"""Identificador del agente de la fila actual."""
		fila = self.lista.fila_actual()
		return self.ids[fila] if 0 <= fila < len(self.ids) else None

	def texto_descripcion(self, identificador: str) -> str:
		"""Descripción, estado, instrucciones de instalación y web del agente."""
		proveedor = self.proveedor(identificador)
		partes = [self.nombre(identificador)]
		descripcion = getattr(proveedor, "descripcion", "") or ""
		if descripcion:
			partes.append(descripcion)
		estado = self.estados.get(identificador)
		if estado is not None and estado.ruta:
			partes.append(_("Programa: {ruta}").format(ruta=estado.ruta))
		try:
			instrucciones = proveedor.instrucciones_instalacion() if proveedor is not None else ""
		except Exception:  # noqa: BLE001 - un proveedor roto no debe impedir ver los demás
			log.debug("instrucciones_instalacion falló para %s", identificador, exc_info=True)
			instrucciones = ""
		if instrucciones:
			partes.append(_("Cómo instalarlo:") + "\n" + instrucciones)
		web = getattr(proveedor, "web", "") or ""
		if web:
			partes.append(_("Página oficial: {web}").format(web=web))
		return "\n\n".join(partes)

	def al_cambiar_fila(self) -> None:
		"""Actualiza la descripción y los botones según la fila actual."""
		identificador = self.seleccionado()
		texto = self.texto_descripcion(identificador) if identificador else ""
		if self.descripcion.GetValue() != texto:
			self.descripcion.ChangeValue(texto)
			self.descripcion.SetInsertionPoint(0)
		self.actualizar_botones()

	def _acciones(self, identificador: str | None) -> dict[str, bool]:
		"""Qué acciones tienen sentido ahora para el agente indicado."""
		if identificador is None:
			return {"instalar": False, "sesion": False, "web": False, "antigravity": False}
		proveedor = self.proveedor(identificador)
		comprobado = identificador not in self.pendientes and identificador in self.estados
		estado = self.estados.get(identificador)
		instalado = estado is not None and estado.disponible
		return {
			"instalar": comprobado and not instalado,
			"sesion": comprobado and instalado,
			"web": bool(getattr(proveedor, "web", "")),
			"antigravity": es_antigravity(identificador, proveedor),
		}

	def actualizar_botones(self) -> None:
		"""Habilita cada botón según el agente seleccionado y si hay una comprobación en marcha."""
		acciones = self._acciones(self.seleccionado())
		self.boton_instalar.Enable(acciones["instalar"])
		self.boton_sesion.Enable(acciones["sesion"])
		self.boton_comprobar.Enable(not self.comprobando)
		self.boton_mas.Enable(self.seleccionado() is not None)

	# -- Menú ---------------------------------------------------------------------

	def crear_menu(self) -> wx.Menu | None:
		"""Menú de acciones del agente seleccionado (contextual y «Más acciones»)."""
		identificador = self.seleccionado()
		if identificador is None:
			return None
		acciones = self._acciones(identificador)
		menu = wx.Menu()
		elementos = [
			(_("&Instalar"), self.instalar, acciones["instalar"]),
			(_("Iniciar &sesión"), self.iniciar_sesion, acciones["sesion"]),
			(_("&Comprobar de nuevo (F5)"), self.comprobar, not self.comprobando),
			(_("Página &oficial"), self.abrir_web, acciones["web"]),
		]
		if acciones["antigravity"]:
			elementos += [
				None,
				(_("&Reglas de permisos…"), self.reglas_antigravity, True),
				(_("Abrir Antigravity I&DE"), self.abrir_ide, True),
				(_("Abrir la &app Antigravity"), self.abrir_app, True),
			]
		for elemento in elementos:
			if elemento is None:
				menu.AppendSeparator()
				continue
			etiqueta, accion, activa = elemento
			item = menu.Append(wx.ID_ANY, etiqueta)
			item.Enable(activa)
			menu.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), item)
		return menu

	def _mostrar_menu(self, control: wx.Window) -> None:
		menu = self.crear_menu()
		if menu is None:
			return
		posicion = wx.Point(0, control.GetSize().GetHeight()) if control is not self.lista else wx.DefaultPosition
		if control is self.lista and self.lista.GetFocusedItem() != wx.NOT_FOUND:
			posicion = self.lista.GetItemRect(self.lista.GetFocusedItem()).GetBottomLeft()
		control.PopupMenu(menu, posicion)
		menu.Destroy()

	# -- Acciones -------------------------------------------------------------------

	def instalar(self) -> None:
		"""Explica la instalación, pide confirmación y la abre en una consola nueva."""
		identificador = self.seleccionado()
		if identificador is None:
			return
		proveedor = self.proveedor(identificador)
		nombre = self.nombre(identificador)
		instrucciones = proveedor.instrucciones_instalacion() if proveedor is not None else ""
		orden = proveedor.orden_instalacion() if proveedor is not None else None
		if not orden:
			texto = _("{nombre} no se puede instalar automáticamente desde Ctrl+IA.").format(nombre=nombre)
			if instrucciones:
				texto += "\n\n" + instrucciones
			web = getattr(proveedor, "web", "")
			if web:
				texto += "\n\n" + _("Página oficial: {web}").format(web=web)
			mostrar_mensaje(self, texto, _("Instalar {nombre}").format(nombre=nombre))
			return
		pregunta = _(
			"Se abrirá una consola nueva que descarga e instala {nombre} con su instalador oficial. "
			"Sigue lo que te diga la consola y, cuando termine, ciérrala: Ctrl+IA lo comprobará de nuevo solo."
		).format(nombre=nombre)
		if instrucciones:
			pregunta += "\n\n" + instrucciones
		pregunta += "\n\n" + _("¿Abrir la consola de instalación?")
		if confirmar(self, pregunta, _("Instalar {nombre}").format(nombre=nombre)) != wx.ID_YES:
			return
		if procesos.abrir_en_consola(orden, al_terminar=lambda _codigo: wx.CallAfter(self._al_cerrar_consola, identificador)):
			voz.hablar(_("Consola de instalación abierta. Cuando se cierre, lo compruebo de nuevo."))
		else:
			mostrar_error(self, _("No se pudo abrir la consola para instalar {nombre}.").format(nombre=nombre))

	def iniciar_sesion(self) -> None:
		"""Abre el inicio de sesión del agente en una consola nueva."""
		identificador = self.seleccionado()
		if identificador is None:
			return
		proveedor = self.proveedor(identificador)
		nombre = self.nombre(identificador)
		try:
			orden = proveedor.orden_inicio_sesion() if proveedor is not None else None
		except Exception:  # noqa: BLE001 - se informa al usuario en lugar de fallar
			log.warning("orden_inicio_sesion falló para %s", identificador, exc_info=True)
			orden = None
		if not orden:
			mostrar_mensaje(
				self,
				_("No sé cómo abrir el inicio de sesión de {nombre}. Comprueba que está instalado (F5) o consulta su página oficial.").format(
					nombre=nombre
				),
				_("Iniciar sesión"),
			)
			return
		if procesos.abrir_en_consola(orden, al_terminar=lambda _codigo: wx.CallAfter(self._al_cerrar_consola, identificador)):
			voz.hablar(_("Consola abierta. Inicia sesión en ella; cuando se cierre, compruebo si la sesión quedó iniciada."))
		else:
			mostrar_error(self, _("No se pudo abrir la consola para iniciar sesión en {nombre}.").format(nombre=nombre))

	def abrir_web(self) -> None:
		"""Abre la página oficial del agente en el navegador."""
		identificador = self.seleccionado()
		web = getattr(self.proveedor(identificador), "web", "") if identificador else ""
		if not web:
			mostrar_mensaje(self, _("Este agente no tiene página oficial registrada."))
			return
		try:
			webbrowser.open(web)
		except Exception:  # noqa: BLE001 - el navegador puede fallar de muchas formas
			log.warning("No se pudo abrir %s", web, exc_info=True)
			mostrar_error(self, _("No se pudo abrir el navegador. La dirección es {web}").format(web=web))

	def reglas_antigravity(self) -> None:
		"""Abre el editor de reglas de permisos de Antigravity CLI."""
		from app.ui.dialogos.reglas_antigravity import DialogoReglasAntigravity

		identificador = self.seleccionado()
		proveedor = self.proveedor(identificador) if identificador else None
		dialogo = DialogoReglasAntigravity(self, proveedor if hasattr(proveedor, "leer_reglas") else None)
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()

	def _abrir_programa(self, ruta: Any, falta: str, abriendo: str, fallo: str) -> None:
		if ruta is None:
			mostrar_mensaje(self, falta.format(web=URL_ANTIGRAVITY), _("Antigravity"))
			return
		if procesos.lanzar_independiente([str(ruta)]):
			voz.hablar(abriendo)
		else:
			mostrar_error(self, fallo)

	def abrir_ide(self) -> None:
		"""Abre el IDE de Antigravity sin carpeta."""
		self._abrir_programa(
			antigravity.buscar_antigravity_ide(),
			_("El IDE de Antigravity no está instalado en este equipo. Puedes descargarlo desde {web}"),
			_("Abriendo el IDE de Antigravity"),
			_("No se pudo abrir el IDE de Antigravity."),
		)

	def abrir_app(self) -> None:
		"""Abre la app de escritorio de Antigravity."""
		self._abrir_programa(
			antigravity.buscar_antigravity_app(),
			_("La app de escritorio de Antigravity no está instalada en este equipo. Puedes descargarla desde {web}"),
			_("Abriendo la app de Antigravity"),
			_("No se pudo abrir la app de Antigravity."),
		)


# -- Hilo de detección (funciones de módulo: no tocan el diálogo fuera del hilo principal) --


def _detectar_todos(dialogo: DialogoAgentes, gestor: Any, generacion: int, ids: Sequence[str]) -> None:
	"""Detecta los agentes uno a uno (bloqueante) y entrega cada resultado al hilo principal."""
	for identificador in ids:
		try:
			estado = gestor.detectar(identificador)
		except Exception:  # noqa: BLE001 - un detector roto no debe dejar la lista a medias
			log.exception("Falló la detección de %s", identificador)
			estado = None
		if not _entregar(dialogo, generacion, "poner_estado", identificador, estado):
			return
	_entregar(dialogo, generacion, "terminar_comprobacion")


def _entregar(dialogo: DialogoAgentes, generacion: int, metodo: str, *argumentos: Any) -> bool:
	"""``wx.CallAfter`` protegido; ``False`` si la aplicación ya se ha cerrado."""
	try:
		wx.CallAfter(_si_vigente, dialogo, generacion, metodo, *argumentos)
	except (RuntimeError, AssertionError, wx.PyAssertionError):
		log.debug("No se pudo entregar el resultado de la detección", exc_info=True)
		return False
	return True


def _si_vigente(dialogo: DialogoAgentes, generacion: int, metodo: str, *argumentos: Any) -> None:
	"""En el hilo principal: llama a ``metodo`` solo si el diálogo vive y la comprobación es la última."""
	try:
		if not dialogo or dialogo.IsBeingDeleted() or generacion != dialogo._generacion:
			return
		funcion = getattr(dialogo, metodo)
	except RuntimeError:
		log.debug("El diálogo de agentes ya no existe", exc_info=True)
		return
	funcion(*argumentos)
