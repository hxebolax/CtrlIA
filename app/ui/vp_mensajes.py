"""Lista de mensajes de la sesión abierta y herramientas de lectura.

Cada fila es un mensaje: quién lo dice, el texto y la hora. La celda del texto
guarda el mensaje ENTERO: con flecha derecha se oye completo aunque Windows
recorte lo que se ve. Intro lo abre en un visor (con formato o en texto plano).

Sesiones largas: al abrirlas solo se ponen en la lista los últimos mensajes
(Preferencias, Mensajes). Los anteriores quedan en ``_anteriores`` y la
primera fila es «Cargar N mensajes anteriores»; Intro sobre ella, Alt+Inicio
o el menú Ver los cargan sin mover el foco del mensaje en el que estabas.
Buscar, las listas de la sesión y exportar usan siempre la conversación entera.

Leer y responder a la vez: el visor de un mensaje no es modal y tiene teclas
propias (Alt+3 va al cuadro de respuesta sin cerrarlo, Ctrl+R cita en la
respuesta lo seleccionado, Ctrl+Alt+N empieza otra sesión a partir del mensaje);
Alt+6 vuelve al visor desde la ventana principal. Ctrl+R y Ctrl+Alt+N valen
también en la lista de mensajes y en el panel de lectura (allí, sin selección,
Ctrl+R cita el párrafo del cursor).

Mensajes favoritos (de cualquier sesión y agente): Ctrl+Alt+F marca o quita el
mensaje elegido; Alt+Arriba y Alt+Abajo, en la lista y en el panel de lectura,
llevan al favorito anterior y al siguiente, cargando los mensajes antiguos si
hace falta; Ver, «Mensajes favoritos» (Ctrl+Alt+Mayús+F) los lista todos. Qué
mensaje es cada favorito lo decide ``app.services.favoritos``; las marcas de la
sesión abierta se calculan una vez por carga (``_marcas_actuales``).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import wx

from app.services import favoritos
from app.services.almacen import MensajeFavorito
from app.services.gestor import MensajeVista, coincide
from app.services.reenviar import ROLES_REENVIABLES
from app.ui import avisos
from app.ui.accesibilidad.dialogos import confirmar, mostrar_mensaje, pedir_texto
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _, hora_corta, markdown_seguro, nombre_agente

if TYPE_CHECKING:
	from app.ui.accesibilidad.lista import ListaAccesible

log = logging.getLogger(__name__)

#: Rol de la fila «Cargar mensajes anteriores» (no es un mensaje de verdad).
ROL_ANTERIORES = "anteriores"
#: Cuántos se cargan de golpe con «Cargar anteriores» si la preferencia es «Todos».
LOTE_ANTERIORES = 100


#: Frase que se pone al principio de la respuesta cuando lleva dos o más citas, para que el agente
#: relacione cada cita con lo que se le contesta debajo (pedido por un probador).
NOTA_CITAS = ("Te respondo por partes: debajo de cada fragmento citado (las líneas que empiezan por «>») va mi "
	"respuesta a ese fragmento.")


#: Avisos que Ctrl+IA deja cuando contesta por ti con una regla que ya diste: no son lo último que
#: quieres oír al abrir la sesión (AIChat #338).
_AVISOS_DE_REGLA = ("Permitido «", "También se ha permitido", "También se han permitido")


def indice_al_abrir(mensajes: Sequence[MensajeVista]) -> int:
	"""Dónde queda el foco al abrir una sesión: el último mensaje, saltando esos avisos automáticos del final."""
	indice = len(mensajes) - 1
	while indice > 0 and mensajes[indice].rol == "sistema" and mensajes[indice].texto.startswith(_AVISOS_DE_REGLA):
		indice -= 1
	return max(indice, 0)


def contar_citas(texto: str) -> int:
	"""Cuántos bloques de cita (líneas seguidas que empiezan por «>») tiene ``texto``."""
	bloques = 0
	dentro = False
	for linea in texto.splitlines():
		es_cita = linea.lstrip().startswith(">")
		if es_cita and not dentro:
			bloques += 1
		dentro = es_cita
	return bloques


def es_fila_anteriores(mensaje: MensajeVista | None) -> bool:
	return mensaje is not None and mensaje.rol == ROL_ANTERIORES


def fragmento_de(fragmento: str, mensaje: str, minimo: float = 0.8) -> bool:
	"""Si ``fragmento`` sale de ``mensaje``: casi todas sus palabras están en él.

	Lo copiado de la página con formato no lleva los signos del Markdown (asteriscos,
	almohadillas), así que se comparan palabras y no el texto exacto.
	"""
	palabras = re.findall(r"\w+", fragmento.lower())
	if not palabras:
		return False
	del_mensaje = set(re.findall(r"\w+", mensaje.lower()))
	return sum(1 for palabra in palabras if palabra in del_mensaje) >= minimo * len(palabras)


class MixinMensajes:
	"""Comportamiento de la lista de mensajes (se mezcla con ``VentanaPrincipal``)."""

	lista_mensajes: ListaAccesible

	def columnas_mensajes(self) -> list[str]:
		from app.ui.lectura_listas import COLUMNAS_MENSAJES, MENSAJES_PREDETERMINADAS, columnas_validas

		return columnas_validas(self.opcion("mensajes.columnas", None), COLUMNAS_MENSAJES, MENSAJES_PREDETERMINADAS)

	def crear_lista_mensajes(self, padre: wx.Window) -> ListaAccesible:
		from app.ui.accesibilidad.lista import ListaAccesible
		from app.ui.lectura_listas import COLUMNAS_MENSAJES

		lista = ListaAccesible(padre, _("Mensajes"), [COLUMNAS_MENSAJES[c] for c in self.columnas_mensajes()],
			leer_preferencias=self.preferencias_lectura)
		lista.SetHelpText(_(
			"Mensajes de la sesión abierta, del más antiguo al más reciente. Intro abre el mensaje completo; "
			"Control+C lo copia; flecha derecha recorre las columnas y lee el texto entero; Fin va al último. "
			"Control+Alt+F marca el mensaje como favorito o lo quita; Alt+flecha arriba y Alt+flecha abajo van al "
			"favorito anterior y al siguiente; Control+Alt+Mayúsculas+N le escribe una nota. "
			"Escape vuelve a las sesiones. Las columnas y su orden se eligen en Preferencias, Lectura de listas."
		))
		lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.ver_mensaje_actual())
		lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla_mensajes)
		vincular_menu_contextual(lista, self._menu_contextual_mensajes)
		return lista

	def recrear_lista_mensajes(self) -> None:
		"""Vuelve a crear la lista de mensajes con las columnas de Preferencias, sin perder la posición."""
		vieja = self.lista_mensajes
		tenia_foco = wx.Window.FindFocus() is vieja
		fila = vieja.GetFocusedItem()
		nueva = self.crear_lista_mensajes(self.panel)
		self._sizer_raiz.Replace(vieja, nueva)
		vieja.Destroy()
		self.lista_mensajes = nueva
		nueva.MoveAfterInTabOrder(self.etiqueta_mensajes)
		self.panel.Layout()
		self._pintar_mensajes()
		if 0 <= fila < nueva.GetItemCount():
			nueva.enfocar_fila(fila, anunciar=False)
		if tenia_foco:
			nueva.SetFocus()

	def iniciar_mensajes(self) -> None:
		self._mensajes: list[MensajeVista] = []
		#: Mensajes más antiguos que todavía no están en la lista (del más antiguo al más reciente).
		self._anteriores: list[MensajeVista] = []
		self._busqueda = ""
		self._generacion_carga = 0
		#: Favoritos de la sesión abierta (se recalculan al pintar la lista y al marcar o quitar uno).
		self._marcas = favoritos.Marcas()
		self._marcas_al_dia = False
		self.panel_lectura.Bind(wx.EVT_KEY_DOWN, self._al_tecla_lectura)

	# ------------------------------------------------------------------
	# Abrir y cerrar sesiones
	# ------------------------------------------------------------------

	def abrir_sesion_seleccionada(self) -> None:
		fila = self.fila_seleccionada()
		if fila is not None:
			self.abrir_sesion(fila.clave)

	def abrir_sesion(self, clave: str, mover_foco: bool = True) -> None:
		"""Carga los mensajes de una sesión (en segundo plano) y la deja como sesión abierta."""
		self.sesion_actual = clave
		self.gestor.sesion_abierta = clave
		fila = self.fila_de(clave)
		if fila is not None and fila.no_leidas:
			fila.no_leidas = 0
			self.mostrar_sesiones(False)
		self.gestor.marcar_leida(clave)
		self.actualizar_titulo_ventana()
		self.actualizar_barra_estado()
		self.preparar_cuadro_respuesta()
		self._generacion_carga += 1
		generacion = self._generacion_carga
		# La lista empieza vacía: lo que se añada mientras carga (un mensaje enviado
		# enseguida, una respuesta) se conserva al llegar la carga.
		self._mensajes = []
		self._anteriores = []
		self.lista_mensajes.limpiar()
		if mover_foco:
			avisos.anunciar(_("Cargando la sesión..."))

		def cargado(mensajes: list[MensajeVista]) -> None:
			if generacion != self._generacion_carga or clave != self.sesion_actual:
				return
			ids = {m.id for m in mensajes if m.id}
			textos = {(m.rol, m.texto) for m in mensajes}
			mientras = [m for m in self._mensajes if not es_fila_anteriores(m) and (not m.id or m.id not in ids)
				and (m.rol, m.texto) not in textos]
			self.mostrar_mensajes(list(mensajes) + mientras)
			if mover_foco:
				self.ir_a_mensajes()
				if self._anteriores:
					avisos.anunciar(_("Últimos {n} de {t} mensajes. Los anteriores se cargan con Alt+Inicio.").format(
						n=self.mensajes_cargados(), t=self.total_mensajes()))
				else:
					avisos.anunciar(_("{n} mensajes.").format(n=len(mensajes)) if mensajes else _("La sesión no tiene mensajes todavía."))
			self._configurar_seguimiento(clave)

		def fallo(error: BaseException) -> None:
			mostrar_mensaje(self, _("No se pudo leer la sesión: {e}").format(e=error), _("Abrir sesión"))

		self.en_segundo_plano(self.gestor.mensajes, clave, al_terminar=cargado, al_fallar=fallo)

	def cerrar_sesion_abierta(self) -> None:
		self.sesion_actual = ""
		self.gestor.sesion_abierta = ""
		self._mensajes = []
		self._anteriores = []
		self.lista_mensajes.limpiar()
		self._temporizador_seguimiento.Stop()
		self.actualizar_titulo_ventana()
		self.actualizar_barra_estado()

	def recargar_mensajes(self) -> None:
		if self.sesion_actual:
			self.abrir_sesion(self.sesion_actual, mover_foco=False)

	def _configurar_seguimiento(self, clave: str) -> None:
		"""Las sesiones de Claude de otros programas se releen cada poco (para verlas en vivo)."""
		if clave.startswith("claude:") and self.opcion("historial.seguir_en_vivo", True):
			from app.ui.ventana_principal import INTERVALO_SEGUIMIENTO_MS

			self._temporizador_seguimiento.Start(INTERVALO_SEGUIMIENTO_MS)
		else:
			self._temporizador_seguimiento.Stop()

	def seguir_sesion_externa(self) -> None:
		clave = self.sesion_actual
		if not clave.startswith("claude:"):
			self._temporizador_seguimiento.Stop()
			return

		def hecho(cambios: dict) -> None:
			if clave != self.sesion_actual:
				return
			if cambios.get("reiniciado"):
				self.recargar_mensajes()
				return
			for mensaje in cambios.get("modificados") or []:
				self.anadir_o_actualizar_mensaje(mensaje)
			nuevos = cambios.get("nuevos") or []
			for mensaje in nuevos:
				self.anadir_o_actualizar_mensaje(mensaje)
			respuestas = [m for m in nuevos if m.rol == "asistente"]
			if respuestas and self.opcion("anuncios.nivel", "completo") == "completo":
				avisos.anunciar(respuestas[-1].texto[:2000])

		self.en_segundo_plano(self.gestor.mensajes_nuevos, clave, al_terminar=hecho, clave="seguir",
			al_fallar=lambda error: log.debug("No se pudo seguir la sesión: %s", error))

	# ------------------------------------------------------------------
	# Presentación
	# ------------------------------------------------------------------

	def _quien(self, mensaje: MensajeVista) -> str:
		if mensaje.rol == "asistente":
			fila = self.fila_de(self.sesion_actual)
			if fila is not None:
				return nombre_agente(fila.proveedor)
		return mensaje.nombre_rol

	def _valores_mensaje(self, mensaje: MensajeVista, numero: int = 0) -> list[str]:
		"""Texto de cada columna de un mensaje (``numero`` empieza en 1)."""
		from app.ui.lectura_listas import escribir_hora, indicadores_mensaje

		if es_fila_anteriores(mensaje):
			return [mensaje.texto if columna == "mensaje" else (_("Anteriores") if columna == "quien" else "")
				for columna in self.columnas_mensajes()]
		valores = []
		for columna in self.columnas_mensajes():
			if columna == "numero":
				valores.append(str(numero) if numero else "")
			elif columna == "quien":
				valores.append(self._quien(mensaje))
			elif columna == "mensaje":
				valores.append(mensaje.texto or _("(vacío)"))
			elif columna == "indicadores":
				marcas = self._marcas_actuales()
				valores.append(indicadores_mensaje(mensaje.texto, mensaje.rol, len(mensaje.cambios or []), self.opcion,
					mensaje.datos, mensaje.detalle, favorito=bool(marcas.favorito(mensaje)), nota_favorito=marcas.nota(mensaje)))
			elif columna == "hora":
				valores.append(escribir_hora(mensaje.fecha, self.opcion))
		return valores

	def limite_recientes(self) -> int:
		"""Cuántos mensajes se ponen en la lista al abrir una sesión (0 = todos)."""
		try:
			return max(0, int(self.opcion("mensajes.recientes", 100) or 0))
		except (TypeError, ValueError):
			return 0

	def mostrar_mensajes(self, mensajes: list[MensajeVista]) -> None:
		"""Pone en la lista una conversación entera; si es larga, solo sus últimos mensajes."""
		mensajes = [m for m in mensajes if not es_fila_anteriores(m)]
		limite = self.limite_recientes()
		if limite and len(mensajes) > limite:
			self._anteriores, visibles = mensajes[:-limite], mensajes[-limite:]
		else:
			self._anteriores, visibles = [], mensajes
		self._mensajes = visibles
		self._pintar_mensajes()
		if self._mensajes:
			self.lista_mensajes.enfocar_fila(indice_al_abrir(self._mensajes), anunciar=False)

	def _fila_anteriores(self) -> MensajeVista:
		lote = min(len(self._anteriores), self.limite_recientes() or LOTE_ANTERIORES)
		texto = _("Cargar {n} mensajes anteriores ({q} sin cargar). Pulsa Intro.").format(n=lote, q=len(self._anteriores))
		return MensajeVista(ROL_ANTERIORES, texto, 0.0, id="__anteriores__")

	def _pintar_mensajes(self) -> None:
		"""Escribe todas las filas a partir de ``_mensajes`` y ``_anteriores`` (sin mover el foco a propósito)."""
		reales = [m for m in self._mensajes if not es_fila_anteriores(m)]
		self._mensajes = ([self._fila_anteriores()] if self._anteriores else []) + reales
		self._marcas_al_dia = False
		lista = self.lista_mensajes
		lista.Freeze()
		try:
			lista.reemplazar_filas([self._valores_mensaje(m, self.numero_mensaje(i)) for i, m in enumerate(self._mensajes)],
				conservar_foco=False)
		finally:
			lista.Thaw()
		self._nombrar_lista_mensajes()

	def _nombrar_lista_mensajes(self) -> None:
		if self._anteriores:
			self.lista_mensajes.SetName(_("Mensajes ({n} de {t})").format(n=self.mensajes_cargados(), t=self.total_mensajes()))
		else:
			self.lista_mensajes.SetName(_("Mensajes ({n})").format(n=self.mensajes_cargados()))

	def numero_mensaje(self, indice: int) -> int:
		"""Número del mensaje de la fila ``indice`` en la conversación entera (0 en la fila «Cargar anteriores»)."""
		if self._anteriores:
			return 0 if indice == 0 else len(self._anteriores) + indice
		return indice + 1

	def mensajes_cargados(self) -> int:
		return len(self._mensajes) - (1 if self._anteriores else 0)

	def total_mensajes(self) -> int:
		return len(self._anteriores) + self.mensajes_cargados()

	def mensajes_completos(self) -> list[MensajeVista]:
		"""La conversación entera, también lo que aún no está en la lista."""
		return self._anteriores + [m for m in self._mensajes if not es_fila_anteriores(m)]

	def _repintar_mensajes(self) -> None:
		"""Reescribe las filas con las preferencias de lectura vigentes, sin mover el foco ni hablar."""
		for indice, mensaje in enumerate(self._mensajes):
			valores = self._valores_mensaje(mensaje, self.numero_mensaje(indice))
			if self.lista_mensajes.valores_fila(indice) != valores:
				self.lista_mensajes.actualizar_fila(indice, valores)

	def anadir_o_actualizar_mensaje(self, mensaje: MensajeVista) -> None:
		"""Pone un mensaje en la lista sin mover el foco (si ya estaba, lo actualiza)."""
		indice = next((i for i, m in enumerate(self._mensajes) if mensaje.id and m.id == mensaje.id), -1)
		if indice >= 0:
			self._mensajes[indice] = mensaje
			self.lista_mensajes.actualizar_fila(indice, self._valores_mensaje(mensaje, self.numero_mensaje(indice)))
			return
		anterior = next((i for i, m in enumerate(self._anteriores) if mensaje.id and m.id == mensaje.id), -1)
		if anterior >= 0:
			# Está entre los que aún no se han cargado: se actualiza ahí, sin tocar la lista.
			self._anteriores[anterior] = mensaje
			return
		self._mensajes.append(mensaje)
		lista = self.lista_mensajes
		estaba_al_final = lista.GetFocusedItem() in (-1, lista.GetItemCount() - 1)
		lista.agregar_fila(self._valores_mensaje(mensaje, self.numero_mensaje(len(self._mensajes) - 1)))
		self._nombrar_lista_mensajes()
		if estaba_al_final and wx.Window.FindFocus() is not lista:
			lista.enfocar_fila(lista.GetItemCount() - 1, anunciar=False)

	def mensaje_actual(self) -> MensajeVista | None:
		indice = self.lista_mensajes.GetFocusedItem()
		if 0 <= indice < len(self._mensajes):
			return self._mensajes[indice]
		return None

	# ------------------------------------------------------------------
	# Cargar los mensajes anteriores
	# ------------------------------------------------------------------

	def accion_cargar_anteriores(self) -> None:
		self.cargar_anteriores()

	def accion_cargar_todos(self) -> None:
		self.cargar_anteriores(todos=True)

	def cargar_anteriores(self, todos: bool = False) -> int:
		"""Pone en la lista el siguiente lote de mensajes anteriores (o todos) sin perder la posición."""
		if not self.sesion_actual:
			avisos.anunciar(_("Primero abre una sesión."), interrumpir=True)
			return 0
		if not self._anteriores:
			avisos.anunciar(_("Ya están cargados todos los mensajes de la sesión."), interrumpir=True)
			return 0
		lote = len(self._anteriores) if todos else min(len(self._anteriores), self.limite_recientes() or LOTE_ANTERIORES)
		lista = self.lista_mensajes
		actual = self.mensaje_actual()
		en_la_fila = actual is None or es_fila_anteriores(actual)
		nuevos = self._anteriores[-lote:]
		self._anteriores = self._anteriores[:-lote]
		reales = [m for m in self._mensajes if not es_fila_anteriores(m)]
		self._mensajes = nuevos + reales
		tenia_foco = wx.Window.FindFocus() is lista
		self._pintar_mensajes()
		# Desde la fila «Cargar anteriores», el foco va al más reciente de los recién cargados
		# (justo encima de donde estabas); si no, se queda en el mismo mensaje.
		objetivo = nuevos[-1] if en_la_fila else actual
		indice = next((i for i, m in enumerate(self._mensajes) if m is objetivo), len(self._mensajes) - 1)
		lista.enfocar_fila(indice, anunciar=False)
		if tenia_foco:
			lista.SetFocus()
		if self._anteriores:
			avisos.anunciar(_("Cargados {n} mensajes anteriores. Quedan {q} sin cargar.").format(n=lote, q=len(self._anteriores)),
				interrumpir=True)
		else:
			avisos.anunciar(_("Cargados {n} mensajes anteriores. Ya está toda la conversación.").format(n=lote), interrumpir=True)
		return lote

	def _mostrar_mensaje_completo(self, indice_completo: int, anunciar: bool = True, enfocar_lista: bool = True) -> None:
		"""Enfoca el mensaje ``indice_completo`` de la conversación entera, cargando lo que haga falta.

		Con ``enfocar_lista=False`` la fila cambia pero el foco del teclado se queda donde
		estaba (por ejemplo, en el panel de lectura).
		"""
		if indice_completo < len(self._anteriores):
			nuevos = self._anteriores[indice_completo:]
			self._anteriores = self._anteriores[:indice_completo]
			self._mensajes = nuevos + [m for m in self._mensajes if not es_fila_anteriores(m)]
			self._pintar_mensajes()
			indice = 1 if self._anteriores else 0
		else:
			indice = indice_completo - len(self._anteriores) + (1 if self._anteriores else 0)
		if 0 <= indice < len(self._mensajes):
			if enfocar_lista:
				self.lista_mensajes.SetFocus()
			self.lista_mensajes.enfocar_fila(indice, anunciar=anunciar)

	def actualizar_titulo_ventana(self) -> None:
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		titulo = "Ctrl+IA"
		if fila is not None:
			titulo = "{t} - {a} - Ctrl+IA".format(t=fila.titulo, a=nombre_agente(fila.proveedor))
		self.SetTitle(titulo)

	# ------------------------------------------------------------------
	# Ver y copiar
	# ------------------------------------------------------------------

	def _al_tecla_mensajes(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if tecla in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and mods == wx.MOD_SHIFT:
			self.ver_mensaje_actual(alternativo=True)
			return
		if tecla == ord("C") and mods == wx.MOD_CONTROL:
			mensaje = self.mensaje_actual()
			if mensaje is not None and not es_fila_anteriores(mensaje):
				self.copiar(mensaje.texto + ("\n\n" + mensaje.detalle if mensaje.detalle else ""))
			return
		if tecla == wx.WXK_BACK and mods == wx.MOD_NONE:
			self.ir_a_sesiones()
			return
		if self._tecla_favoritos(tecla, mods, desde_lectura=False):
			return
		evento.Skip()

	def ver_mensaje_actual(self, alternativo: bool = False) -> None:
		mensaje = self.mensaje_actual()
		if mensaje is None:
			return
		if es_fila_anteriores(mensaje):
			self.cargar_anteriores()
			return
		formato = bool(self.opcion("mensajes.vista_formateada", True))
		if alternativo:
			formato = not formato
		self.ver_mensaje(mensaje, formato)

	def ver_mensaje(self, mensaje: MensajeVista, formato: bool = True) -> None:
		from app.ui.visor_ayuda import mostrar_documento

		indice = self.numero_mensaje(self._mensajes.index(mensaje)) if mensaje in self._mensajes else 0
		titulo = _("Mensaje {n} de {q} ({h})").format(n=indice, q=self._quien(mensaje), h=hora_corta(mensaje.fecha))
		texto = mensaje.texto
		if mensaje.detalle:
			if mensaje.rol == "cambio":
				from app.services.extraccion import diff_legible

				# Las líneas cambiadas, con palabras o con los signos del diff según Preferencias.
				estilo = str(self.opcion("mensajes.estilo_cambios", "palabras") or "palabras")
				legible = diff_legible(mensaje.detalle, estilo)
				texto += "\n\n---\n\n" + ("```diff\n" + legible + "\n```" if estilo == "simbolos" else "```\n" + legible + "\n```")
			else:
				texto += "\n\n---\n\n```\n" + mensaje.detalle + "\n```"
		if formato:
			texto = markdown_seguro(texto)
		clave = self.sesion_actual

		def citar(seleccion: str) -> None:
			if seleccion and fragmento_de(seleccion, mensaje.texto):
				self.citar_en_respuesta(seleccion, clave)
			else:
				self.citar_en_respuesta(mensaje.texto, clave, entero=True, con_formato=formato)

		def nueva(seleccion: str) -> None:
			self.nueva_sesion_desde_mensaje(mensaje, seleccion if fragmento_de(seleccion, mensaje.texto) else "", clave)

		acciones = {"respuesta": lambda _seleccion: self.volver_a_la_respuesta(), "citar": citar, "nueva": nueva}
		teclas = {"Alt+3": "respuesta", "Ctrl+r": "citar", "Ctrl+Alt+n": "nueva"}
		ayuda = _(
			"Mensaje completo. Alt+3 va al cuadro de respuesta sin cerrar el visor y Alt+6 te devuelve aquí. "
			"Control+R cita en tu respuesta lo que tengas seleccionado. En el visor con formato, selecciona el trozo, "
			"pulsa Control+C y después Control+R; en el visor como texto, sin selección, cita el párrafo del cursor. "
			"Control+Alt+N empieza otra sesión a partir de este mensaje. Escape o Alt+C cierran el visor."
		)
		self._visor_mensaje = mostrar_documento(self, titulo, texto, forzar_texto=not formato, acciones=acciones,
			teclas=teclas, ayuda=ayuda)

	# ------------------------------------------------------------------
	# Leer y responder a la vez, citar y empezar otra sesión
	# ------------------------------------------------------------------

	def volver_a_la_respuesta(self) -> None:
		"""Desde el visor de un mensaje: al cuadro de respuesta, con el visor abierto detrás."""
		self.Raise()
		wx.CallAfter(self.ir_a_respuesta)

	def accion_ir_al_visor(self) -> None:
		"""Alt+6: vuelve al visor del último mensaje abierto, si sigue abierto."""
		visor = getattr(self, "_visor_mensaje", None)
		if not visor or getattr(visor, "_cerrado", True):
			avisos.anunciar(_("No hay ningún mensaje abierto en el visor. Ábrelo con Intro en la lista de mensajes."),
				interrumpir=True)
			return
		visor.Raise()
		visor.enfocar_contenido()

	def _texto_de_lectura(self, citar: bool) -> str:
		"""Lo seleccionado en el panel de lectura; sin selección, el párrafo del cursor (``citar``) o todo."""
		from app.ui.visor_ayuda import parrafo_en

		seleccion = self.panel_lectura.GetStringSelection().strip()
		if seleccion or not citar:
			return seleccion
		return parrafo_en(self.panel_lectura.GetValue(), self.panel_lectura.GetInsertionPoint())

	def accion_citar(self) -> None:
		"""Ctrl+R: cita en la respuesta el fragmento del panel de lectura o el mensaje elegido."""
		mensaje = self.mensaje_actual()
		if mensaje is None or es_fila_anteriores(mensaje):
			avisos.anunciar(_("Elige primero un mensaje en la lista de mensajes."), interrumpir=True)
			return
		texto = ""
		if wx.Window.FindFocus() is self.panel_lectura:
			texto = self._texto_de_lectura(citar=True)
		self.citar_en_respuesta(texto or mensaje.texto)

	def accion_nueva_desde_mensaje(self) -> None:
		"""Ctrl+Alt+N: nueva sesión a partir del mensaje elegido (o de lo seleccionado en el panel de lectura)."""
		mensaje = self.mensaje_actual()
		if mensaje is None or es_fila_anteriores(mensaje):
			avisos.anunciar(_("Elige primero un mensaje en la lista de mensajes."), interrumpir=True)
			return
		seleccion = self._texto_de_lectura(citar=False) if wx.Window.FindFocus() is self.panel_lectura else ""
		self.nueva_sesion_desde_mensaje(mensaje, seleccion)

	def accion_reenviar(self) -> None:
		"""Ctrl+Alt+R: enviar la respuesta elegida (o la última del agente) a otra sesión (AIChat #248)."""
		elegido = self.mensaje_actual() if wx.Window.FindFocus() is self.lista_mensajes else None
		self.reenviar_a_otra_sesion(elegido)

	def reenviar_a_otra_sesion(self, elegido: MensajeVista | None = None) -> None:
		from app.services import reenviar
		from app.ui.dialogos.reenviar import DialogoReenviar

		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None:
			avisos.anunciar(_("Abre primero una sesión y elige una respuesta del agente."), interrumpir=True)
			return
		mensaje = reenviar.mensaje_a_reenviar(self._mensajes, None if elegido is None or es_fila_anteriores(elegido) else elegido)
		if mensaje is None:
			avisos.anunciar(_("«{t}» todavía no tiene ninguna respuesta del agente que enviar.").format(t=fila.titulo), interrumpir=True)
			return
		parejas: dict[str, str] = self.__dict__.setdefault("_parejas_reenvio", {})
		destinos = reenviar.destinos(self._todas, fila.clave, lambda f: self.gestor.proveedor(f.proveedor) is not None,
			parejas.get(fila.clave))
		if not destinos:
			mostrar_mensaje(self, _("No hay otra sesión a la que enviarla. Se puede enviar a las sesiones de Ctrl+IA de "
				"cualquier agente (no a las de otros programas, ni a las ocultas o archivadas). Crea una con Archivo, "
				"Nueva sesión."), _("Enviar la respuesta a otra sesión"))
			return
		nombres = [_("{t}, {a}, {c}").format(t=d.titulo, a=nombre_agente(d.proveedor), c=d.carpeta or _("sin carpeta"))
			for d in destinos]
		texto = reenviar.texto_reenviado(mensaje, nombre_agente(fila.proveedor), fila.titulo)

		def comprobar(indice: int, escrito: str) -> str:
			destino = destinos[indice]
			return reenviar.demasiado_largo(destino.proveedor, nombre_agente(destino.proveedor), escrito)

		dialogo = DialogoReenviar(self, fila.titulo, nombres, texto, comprobar, trabajando=self.gestor.ocupada(fila.clave))
		try:
			if dialogo.ShowModal() != wx.ID_OK or dialogo.elegido is None:
				return
			indice, texto = dialogo.elegido
		finally:
			dialogo.Destroy()
		destino = destinos[indice]
		parejas[destino.clave] = fila.clave
		# Se envía antes de abrir la sesión: así su lista ya trae el mensaje al cargarse.
		enviado = self.enviar_mensaje(destino.clave, texto)
		self.abrir_sesion(destino.clave, mover_foco=False)
		if enviado:
			avisos.anunciar(_("Enviado a «{t}». Su respuesta será lo siguiente que oigas; Control+Alt+R la devuelve.").format(
				t=destino.titulo), interrumpir=True)
			self.ir_a_mensajes()
			return
		# No ha salido: se queda en el cuadro de respuesta de esa sesión, nunca se pierde.
		self.respuesta.SetValue(texto)
		self.respuesta.SetInsertionPointEnd()
		self.ir_a_respuesta()

	# Conversación automática entre dos sesiones (AIChat #248, segunda parte): app/ui/dialogos/dialogo_agentes.py.

	def accion_conversacion_sesiones(self) -> None:
		from app.ui.dialogos import dialogo_agentes

		dialogo_agentes.empezar(self)

	def accion_detener_conversacion_sesiones(self) -> None:
		from app.ui.dialogos import dialogo_agentes

		dialogo_agentes.detener(self)

	def accion_ver_conversacion_sesiones(self) -> None:
		from app.ui.dialogos import dialogo_agentes

		dialogo_agentes.ver(self)

	def citar_en_respuesta(self, texto: str, clave: str | None = None, entero: bool = False, con_formato: bool = False) -> bool:
		"""Añade ``texto`` al final del cuadro de respuesta como cita («> »), sin mover el foco.

		:param entero: se cita el mensaje entero porque no había nada seleccionado (se dice).
		:param con_formato: venía del visor con formato: se explica cómo citar solo un trozo.
		"""
		texto = texto.strip()
		if not self.sesion_actual:
			avisos.anunciar(_("Primero abre una sesión."), interrumpir=True)
			return False
		if clave is not None and clave != self.sesion_actual:
			fila = self.fila_de(clave)
			avisos.anunciar(_("Ese mensaje es de «{t}», que ya no es la sesión abierta. Ábrela para citarlo.").format(
				t=fila.titulo if fila else _("otra sesión")), interrumpir=True)
			return False
		if not texto:
			avisos.anunciar(_("No hay texto que citar."), interrumpir=True)
			return False
		lineas = texto.splitlines()
		cita = "\n".join("> " + linea if linea.strip() else ">" for linea in lineas)
		actual = self.respuesta.GetValue()
		if not actual.strip():
			antes = ""
		elif actual.endswith("\n\n"):
			antes = ""
		elif actual.endswith("\n"):
			antes = "\n"
		else:
			antes = "\n\n"
		citas_previas = contar_citas(actual)
		self.respuesta.SetInsertionPointEnd()
		self.respuesta.WriteText(antes + cita + "\n\n")
		self.respuesta.SetInsertionPointEnd()
		nota_puesta = False
		if citas_previas >= 1 and NOTA_CITAS not in actual:
			# Con dos o más citas, el agente debe saber que cada respuesta va con la cita de encima.
			self.respuesta.SetInsertionPoint(0)
			self.respuesta.WriteText(NOTA_CITAS + "\n\n")
			self.respuesta.SetInsertionPointEnd()
			nota_puesta = True
		if entero:
			aviso = _("Citado el mensaje entero: no había nada seleccionado.")
			if con_formato:
				aviso += " " + _("Para citar solo un trozo, selecciónalo, pulsa Control+C y después Control+R.")
		elif len(lineas) == 1:
			aviso = _("Citado en la respuesta. Alt+3 para escribir debajo.")
		else:
			aviso = _("Citadas {n} líneas en la respuesta. Alt+3 para escribir debajo.").format(n=len(lineas))
		if nota_puesta:
			aviso += " " + _("Al principio se ha añadido una frase que le explica al agente que respondes por partes, "
				"debajo de cada cita.")
		avisos.anunciar(aviso, interrumpir=True)
		return True

	def nueva_sesion_desde_mensaje(self, mensaje: MensajeVista, seleccion: str = "", clave: str | None = None) -> bool:
		"""Abre «Nueva sesión» con el mensaje (o el fragmento elegido) citado como primer mensaje.

		Vale con todos los agentes: la sesión nueva empieza de cero y el fragmento le da el contexto.
		Propone el mismo agente, la misma carpeta (o el mismo equipo por SSH) y un título.
		"""
		fila = self.fila_de(clave or self.sesion_actual)
		texto = (seleccion or mensaje.texto).strip()
		if fila is None or not texto:
			avisos.anunciar(_("No hay ningún mensaje con texto del que partir."), interrumpir=True)
			return False
		cita = "\n".join("> " + linea if linea.strip() else ">" for linea in texto.splitlines())
		quien = self._quien(mensaje)
		primero = _("Partimos de esto que escribió {q} en otra conversación («{t}»):").format(q=quien, t=fila.titulo)
		primer_mensaje = primero + "\n\n" + cita + "\n\n"
		resumen = " ".join(texto.split())
		titulo = _("Sobre: {r}").format(r=resumen[:50] + ("…" if len(resumen) > 50 else ""))
		agente = fila.proveedor if fila.proveedor in self.gestor.proveedores else ""
		if fila.remota:
			return self.accion_nueva_sesion(mensaje=primer_mensaje, agente=agente, titulo=titulo, equipo=fila.equipo,
				carpeta_remota=fila.carpeta)
		carpeta = Path(fila.carpeta) if fila.carpeta and Path(fila.carpeta).is_dir() else None
		return self.accion_nueva_sesion(carpeta=carpeta, mensaje=primer_mensaje, agente=agente, titulo=titulo)

	def _menu_contextual_mensajes(self) -> wx.Menu | None:
		mensaje = self.mensaje_actual()
		if mensaje is None:
			return None
		menu = wx.Menu()

		def anadir(etiqueta: str, accion) -> None:
			item = menu.Append(wx.ID_ANY, etiqueta)
			self.lista_mensajes.Bind(wx.EVT_MENU, lambda _e: self._ejecutar_accion_funcion(accion), item)

		if es_fila_anteriores(mensaje):
			anadir(_("&Cargar mensajes anteriores"), self.cargar_anteriores)
			anadir(_("Cargar &todos los mensajes"), lambda: self.cargar_anteriores(todos=True))
			return menu
		anadir(_("&Ver con formato\tIntro"), lambda: self.ver_mensaje(mensaje, True))
		anadir(_("Ver como &texto\tMayús+Intro"), lambda: self.ver_mensaje(mensaje, False))
		anadir(_("&Copiar\tCtrl+C"), lambda: self.copiar(mensaje.texto + ("\n\n" + mensaje.detalle if mensaje.detalle else "")))
		anadir(_("Copiar solo el te&xto"), lambda: self.copiar(mensaje.texto))
		if mensaje.detalle:
			anadir(_("Copiar solo el &detalle"), lambda: self.copiar(mensaje.detalle))
		anadir(_("&Leer en voz alta"), lambda: avisos.anunciar(mensaje.texto, interrumpir=True))
		anadir(_("C&itar en la respuesta\tCtrl+R"), lambda: self.citar_en_respuesta(mensaje.texto))
		anadir(_("Nueva &sesión a partir de este mensaje...\tCtrl+Alt+N"), lambda: self.nueva_sesion_desde_mensaje(mensaje))
		if mensaje.rol in ROLES_REENVIABLES:
			anadir(_("Enviar la respuesta a otra sesi&ón...\tCtrl+Alt+R"), lambda: self.reenviar_a_otra_sesion(mensaje))
		menu.AppendSeparator()
		# Una sola etiqueta con el atajo: «Marcar» o «Quitar» según el mensaje.
		favorito = bool(self._marcas_actuales().favorito(mensaje))
		etiqueta = _("Quitar de &favoritos") if favorito else _("Marcar como &favorito")
		anadir(etiqueta + "\tCtrl+Alt+F", lambda: self.alternar_favorito(mensaje))
		if favorito:
			# Sin letra subrayada, como las dos siguientes: las de estas palabras ya están usadas en este menú.
			anadir(_("Escribir una nota en el favorito...\tCtrl+Alt+Mayús+N"), lambda: self.escribir_nota_favorito(mensaje))
		# Sin letra subrayada: todas las de estas palabras ya están usadas en este menú.
		anadir(_("Ir al favorito anterior\tAlt+Arriba"), lambda: self.ir_a_favorito(-1))
		anadir(_("Ir al favorito siguiente\tAlt+Abajo"), lambda: self.ir_a_favorito(1))
		menu.AppendSeparator()
		anadir(_("&Bloques de código\tCtrl+Mayús+B"), self.accion_bloques)
		anadir(_("&Enlaces\tCtrl+L"), self.accion_enlaces)
		anadir(_("&Archivos cambiados\tCtrl+Mayús+D"), self.accion_archivos)
		if mensaje.rol == "usuario":
			menu.AppendSeparator()
			fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
			if fila is not None and fila.propia:
				anadir(_("Volver a e&nviar..."), lambda: self.volver_a_enviar(mensaje))
				anadir(_("Des&hacer los cambios de archivos desde este mensaje...\tCtrl+Alt+Z"),
					lambda: self.deshacer_archivos_desde(mensaje))
				if self.gestor.puede_rehacer(fila.clave):
					# Sin letra subrayada: las de estas palabras ya están usadas en este menú.
					anadir(_("Rehacer los cambios de archivos deshechos...\tCtrl+Alt+Mayús+Z"), self.accion_rehacer_archivos)
			anadir(_("Copiar al cuadro de &respuesta"), lambda: self._poner_en_respuesta(mensaje.texto))
		return menu

	def _poner_en_respuesta(self, texto: str) -> None:
		self.respuesta.SetValue(texto)
		self.respuesta.SetInsertionPointEnd()
		self.ir_a_respuesta()

	def volver_a_enviar(self, mensaje: MensajeVista) -> bool:
		"""Envía otra vez uno de tus mensajes, tras confirmarlo (nunca solo). Si el agente trabaja, va a la cola."""
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None or not fila.propia or mensaje.rol != "usuario":
			avisos.anunciar(_("Solo se pueden volver a enviar tus mensajes de las sesiones de Ctrl+IA."), interrumpir=True)
			return False
		if not mensaje.texto.strip():
			avisos.anunciar(_("Ese mensaje no tiene texto que enviar."), interrumpir=True)
			return False
		agente = nombre_agente(fila.proveedor)
		aviso = _("¿Volver a enviar este mensaje a {a}?").format(a=agente)
		if self.gestor.ocupada(fila.clave):
			aviso += " " + _("{a} está trabajando: el mensaje quedará en cola hasta que termine.").format(a=agente)
		recorte = mensaje.texto if len(mensaje.texto) <= 600 else mensaje.texto[:600] + "…"
		if confirmar(self, aviso + "\n\n" + recorte, _("Volver a enviar")) != wx.ID_YES:
			return False
		return bool(self.enviar_mensaje(fila.clave, mensaje.texto))

	# ------------------------------------------------------------------
	# Deshacer y rehacer los cambios de archivos (puntos de restauración, app/services/puntos.py)
	# ------------------------------------------------------------------

	def accion_deshacer_archivos(self) -> None:
		"""Ctrl+Alt+Z: desde el mensaje tuyo elegido (o el tuyo que provocó la respuesta elegida) o, si no, el último tuyo."""
		completos = self.mensajes_completos()
		elegido = self.mensaje_actual() if wx.Window.FindFocus() is self.lista_mensajes else None
		desde = len(completos) - 1
		if elegido is not None and elegido in completos:
			desde = completos.index(elegido)
		mensaje = next((m for m in reversed(completos[: desde + 1]) if m.rol == "usuario"), None)
		if mensaje is None:
			avisos.anunciar(_("Esta sesión no tiene ningún mensaje tuyo desde el que deshacer los cambios."), interrumpir=True)
			return
		self.deshacer_archivos_desde(mensaje)

	def _sesion_para_deshacer(self):
		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else None
		if fila is None or not fila.propia:
			avisos.anunciar(_("Abre una sesión de Ctrl+IA: solo en ellas se pueden deshacer los cambios de archivos."),
				interrumpir=True)
			return None
		if self.gestor.ocupada(fila.clave):
			avisos.anunciar(_("El agente está trabajando. Detenlo con Control+punto o espera a que termine para deshacer o "
				"rehacer sus cambios."), interrumpir=True)
			return None
		return fila

	def deshacer_archivos_desde(self, mensaje: MensajeVista) -> bool:
		"""Calcula en segundo plano qué pasará con cada archivo y lo enseña para confirmarlo."""
		if mensaje.rol != "usuario" or not isinstance(mensaje.id, int) or not mensaje.id:
			avisos.anunciar(_("Elige uno de tus mensajes: se deshace lo que cambió en los archivos desde él."), interrumpir=True)
			return False
		fila = self._sesion_para_deshacer()
		if fila is None:
			return False
		titulo = _("Deshacer los cambios de archivos")
		avisos.anunciar(_("Mirando qué archivos cambiarán..."), interrumpir=True)
		self.en_segundo_plano(self.gestor.plan_deshacer, fila.clave, mensaje.id, al_terminar=self._confirmar_deshacer,
			al_fallar=lambda error: mostrar_mensaje(self, str(error), titulo))
		return True

	def accion_rehacer_archivos(self) -> None:
		"""Ctrl+Alt+Mayús+Z: vuelve a poner los cambios del último deshacer de la sesión abierta."""
		fila = self._sesion_para_deshacer()
		if fila is None:
			return
		if not self.gestor.puede_rehacer(fila.clave):
			avisos.anunciar(_("No hay cambios deshechos que rehacer en esta sesión: solo se puede rehacer el último deshacer."),
				interrumpir=True)
			return
		titulo = _("Rehacer los cambios deshechos")
		avisos.anunciar(_("Mirando qué archivos cambiarán..."), interrumpir=True)
		self.en_segundo_plano(self.gestor.plan_rehacer, fila.clave, al_terminar=self._confirmar_deshacer,
			al_fallar=lambda error: mostrar_mensaje(self, str(error), titulo))

	def _confirmar_deshacer(self, plan) -> None:
		from app.ui.dialogos.deshacer import DialogoDeshacer

		titulo = _("Rehacer los cambios deshechos") if plan.rehacer else _("Deshacer los cambios de archivos")
		if not plan.cambios:
			if plan.rehacer:
				texto = _("No hay nada que rehacer: los archivos ya están como los dejó el agente.")
			else:
				texto = _("No hay nada que deshacer: los archivos ya están como antes de ese mensaje.")
			mostrar_mensaje(self, "\n\n".join([texto, *plan.avisos]), titulo)
			return
		dialogo = DialogoDeshacer(self, plan)
		try:
			aceptado = dialogo.ShowModal() == wx.ID_OK
		finally:
			dialogo.Destroy()
		if not aceptado:
			avisos.anunciar(_("No se ha cambiado ningún archivo."), interrumpir=True)
			return
		avisos.anunciar(_("Rehaciendo los cambios...") if plan.rehacer else _("Deshaciendo los cambios..."), interrumpir=True)
		funcion = self.gestor.rehacer if plan.rehacer else self.gestor.deshacer
		self.en_segundo_plano(funcion, plan, al_terminar=lambda resultado: self._deshacer_hecho(plan, resultado),
			al_fallar=lambda error: mostrar_mensaje(self, str(error), titulo))

	def _deshacer_hecho(self, plan, resultado) -> None:
		from app.services.puntos import archivos

		titulo = _("Rehacer los cambios deshechos") if plan.rehacer else _("Deshacer los cambios de archivos")
		if plan.rehacer:
			texto = _("Rehechos los cambios de {n}.").format(n=archivos(resultado.total))
		else:
			texto = _("Deshechos los cambios de {n}.").format(n=archivos(resultado.total))
			if resultado.seguridad:
				texto += " " + _("Control+Alt+Mayúsculas+Z los vuelve a poner.")
		if plan.clave == self.sesion_actual:
			ultimo = max((m.id for m in self._mensajes if isinstance(m.id, int)), default=0)
			for guardado in self.almacen.mensajes(plan.clave, ultimo):
				self.anadir_o_actualizar_mensaje(self._vista_de(guardado))
		if resultado.fallidos:
			detalle = "\n".join(f"{ruta}: {motivo}" for ruta, motivo in resultado.fallidos[:30])
			mostrar_mensaje(self, texto + "\n\n" + _("No se pudieron cambiar estos archivos (quizá están abiertos en otro "
				"programa). Ciérralos y vuelve a intentarlo:") + "\n" + detalle, titulo)
		else:
			avisos.anunciar(texto, interrumpir=True)

	# ------------------------------------------------------------------
	# Buscar
	# ------------------------------------------------------------------

	def accion_buscar(self) -> None:
		if wx.Window.FindFocus() is self.lista_sesiones or not self.sesion_actual:
			self.filtrar_sesiones()
			return
		texto = pedir_texto(self, _("&Buscar en los mensajes de esta sesión:"), _("Buscar"), self._busqueda)
		if texto is None or not texto.strip():
			return
		self._busqueda = texto.strip()
		self._buscar_desde(1)

	def accion_buscar_siguiente(self) -> None:
		if not self._busqueda:
			self.accion_buscar()
			return
		self._buscar_desde(1)

	def accion_buscar_anterior(self) -> None:
		if not self._busqueda:
			self.accion_buscar()
			return
		self._buscar_desde(-1)

	def _indice_completo_actual(self) -> int:
		"""Posición del mensaje enfocado en la conversación entera (-1 si no hay ninguno real)."""
		fila = self.lista_mensajes.GetFocusedItem()
		if not 0 <= fila < len(self._mensajes):
			return -1
		if es_fila_anteriores(self._mensajes[fila]):
			return len(self._anteriores) - 1 if self._anteriores else -1
		return len(self._anteriores) + fila - (1 if self._anteriores else 0)

	def _buscar_desde(self, paso: int) -> None:
		# Se busca en la conversación entera, también en lo que aún no está en la lista.
		completos = self.mensajes_completos()
		total = len(completos)
		if not total:
			avisos.anunciar(_("No hay mensajes en los que buscar."), interrumpir=True)
			return
		inicio = self._indice_completo_actual()
		if wx.Window.FindFocus() is not self.lista_mensajes or inicio < 0:
			inicio = -1 if paso > 0 else total
		for desplazamiento in range(1, total + 1):
			indice = (inicio + paso * desplazamiento) % total
			mensaje = completos[indice]
			if coincide(mensaje.texto + "\n" + mensaje.detalle, self._busqueda):
				self._mostrar_mensaje_completo(indice)
				return
		avisos.anunciar(_("No se encuentra «{t}».").format(t=self._busqueda), interrumpir=True)

	def accion_buscar_todo(self) -> None:
		from app.ui.dialogos.buscar import DialogoBuscarTodo

		dialogo = DialogoBuscarTodo(self, self.gestor.buscar, self._busqueda)
		try:
			if dialogo.ShowModal() != wx.ID_OK or not getattr(dialogo, "resultado", None):
				return
			clave, mensaje = dialogo.resultado
		finally:
			dialogo.Destroy()
		self._ir_a_mensaje_de_sesion(clave, mensaje)

	def _ir_a_mensaje_de_sesion(self, clave: str, objetivo: MensajeVista | None,
			localizar: Callable[[list[MensajeVista]], int] | None = None, si_no_esta: str = "") -> None:
		"""Abre la sesión ``clave`` y deja el foco en un mensaje, cargando los anteriores si hace falta.

		El mensaje es ``objetivo`` (por su id o su texto) o el que diga ``localizar``; si no
		aparece, el foco va al último y se dice ``si_no_esta``.
		"""
		indice_sesion = next((i for i, f in enumerate(self._visibles) if f.clave == clave), -1)
		if indice_sesion >= 0:
			self.lista_sesiones.enfocar_fila(indice_sesion, anunciar=False)

		def cargado(mensajes: list[MensajeVista]) -> None:
			self.sesion_actual = clave
			self.gestor.sesion_abierta = clave
			self.actualizar_titulo_ventana()
			self.actualizar_barra_estado()
			self.preparar_cuadro_respuesta()
			self.mostrar_mensajes(mensajes)
			if localizar is not None:
				indice = localizar(mensajes)
			elif objetivo is not None:
				indice = next((i for i, m in enumerate(mensajes) if objetivo.id and m.id == objetivo.id), -1)
				if indice < 0:
					indice = next((i for i, m in enumerate(mensajes) if m.texto == objetivo.texto), -1)
			else:
				indice = -1
			if indice < 0:
				indice = len(mensajes) - 1
				if si_no_esta:
					avisos.anunciar(si_no_esta, interrumpir=True)
			if mensajes:
				# Si el mensaje es de los antiguos, se cargan los necesarios para enseñarlo.
				self._mostrar_mensaje_completo(max(indice, 0))
			self._configurar_seguimiento(clave)

		self.en_segundo_plano(self.gestor.mensajes, clave, al_terminar=cargado)

	# ------------------------------------------------------------------
	# Mensajes favoritos
	# ------------------------------------------------------------------

	def _marcas_actuales(self) -> favoritos.Marcas:
		"""Favoritos de la sesión abierta (una consulta por carga, no una por fila)."""
		marcas = self._marcas
		if not self._marcas_al_dia or marcas.sesion != self.sesion_actual:
			if self.sesion_actual:
				marcas = favoritos.Marcas.crear(self.sesion_actual, self.almacen.favoritos_de_sesion(self.sesion_actual),
					self.mensajes_completos())
			else:
				marcas = favoritos.Marcas()
			self._marcas, self._marcas_al_dia = marcas, True
		return marcas

	def _favoritos_cambiados(self) -> None:
		"""Vuelve a leer los favoritos de la sesión abierta y repinta sus indicadores sin mover el foco."""
		self._marcas_al_dia = False
		self._repintar_mensajes()

	def accion_favorito_mensaje(self) -> None:
		"""Ctrl+Alt+F: marca el mensaje elegido como favorito o lo quita."""
		mensaje = self.mensaje_actual()
		if not self.sesion_actual or mensaje is None or es_fila_anteriores(mensaje):
			avisos.anunciar(_("Elige primero un mensaje en la lista de mensajes."), interrumpir=True)
			return
		self.alternar_favorito(mensaje)

	def alternar_favorito(self, mensaje: MensajeVista) -> bool:
		"""Marca ``mensaje`` (de la sesión abierta) como favorito o lo quita. Devuelve si queda marcado."""
		clave = self.sesion_actual
		guardado = self._marcas_actuales().favorito(mensaje)
		if guardado:
			self.almacen.desmarcar_favorito(clave, guardado)
			aviso = _("Quitado de favoritos.")
		else:
			self.almacen.marcar_favorito(clave, favoritos.clave_mensaje(clave, mensaje), mensaje.rol, mensaje.fecha,
				favoritos.extracto(mensaje.texto))
			aviso = _("Marcado como favorito.")
		self._favoritos_cambiados()
		avisos.anunciar(aviso, interrumpir=True)
		return not guardado

	def accion_nota_favorito(self) -> None:
		"""Ctrl+Alt+Mayús+N: escribe, cambia o quita la nota del mensaje favorito elegido."""
		mensaje = self.mensaje_actual()
		if not self.sesion_actual or mensaje is None or es_fila_anteriores(mensaje):
			avisos.anunciar(_("Elige primero un mensaje en la lista de mensajes."), interrumpir=True)
			return
		self.escribir_nota_favorito(mensaje)

	def escribir_nota_favorito(self, mensaje: MensajeVista) -> bool:
		"""Pide la nota de ``mensaje`` (de la sesión abierta) y la guarda; vacía, la quita.

		Si el mensaje aún no es favorito, escribir una nota lo marca. Devuelve si cambió algo.
		"""
		clave = self.sesion_actual
		guardado = self._marcas_actuales().favorito(mensaje)
		actual = self._marcas_actuales().nota(mensaje)
		texto = pedir_texto(self, _("&Nota del favorito (vacía para quitarla):"), _("Nota del favorito"), actual,
			max_caracteres=favoritos.LARGO_NOTA, ayuda=_(
				"Un recordatorio corto de por qué marcaste este mensaje, de una línea y como mucho {n} caracteres. "
				"Se lee al ir al favorito con Alt+flecha arriba y Alt+flecha abajo, en los indicadores del mensaje y en "
				"la lista de mensajes favoritos. Déjala vacía y acepta para quitarla.").format(n=favoritos.LARGO_NOTA))
		if texto is None:
			return False
		nota = favoritos.limpiar_nota(texto)
		if guardado:
			if nota == actual:
				return False
			self.almacen.poner_nota_favorito(clave, guardado, nota)
			aviso = _("Nota guardada.") if nota else _("Nota quitada.")
		else:
			if not nota:
				avisos.anunciar(_("Sin nota: el mensaje sigue sin ser favorito."), interrumpir=True)
				return False
			self.almacen.marcar_favorito(clave, favoritos.clave_mensaje(clave, mensaje), mensaje.rol, mensaje.fecha,
				favoritos.extracto(mensaje.texto), nota=nota)
			aviso = _("Marcado como favorito con la nota.")
		self._favoritos_cambiados()
		avisos.anunciar(aviso, interrumpir=True)
		return True

	def _posicion_para_favoritos(self, paso: int) -> float:
		"""Dónde está el foco en la conversación entera, para buscar el favorito anterior o siguiente."""
		fila = self.lista_mensajes.GetFocusedItem()
		if not 0 <= fila < len(self._mensajes):
			return float(self.total_mensajes()) if paso < 0 else -1.0
		if es_fila_anteriores(self._mensajes[fila]):
			# La fila «Cargar anteriores» está entre los no cargados y el primero cargado.
			return len(self._anteriores) - 0.5
		return float(len(self._anteriores) + fila - (1 if self._anteriores else 0))

	def ir_a_favorito(self, paso: int, desde_lectura: bool = False) -> bool:
		"""Alt+Arriba (``paso`` -1) y Alt+Abajo (1): al favorito anterior o siguiente de la sesión abierta.

		Si está entre los mensajes aún no cargados, los carga. Dice «Favorito 2 de 5» y el principio
		del mensaje. Desde el panel de lectura, el foco se queda en el panel con el mensaje nuevo.
		"""
		if not self.sesion_actual:
			avisos.anunciar(_("Primero abre una sesión."), interrumpir=True)
			return False
		completos = self.mensajes_completos()
		posiciones = favoritos.posiciones(self._marcas_actuales(), completos)
		if not posiciones:
			avisos.anunciar(_("Esta sesión no tiene mensajes favoritos. Márcalos con Control+Alt+F."), interrumpir=True)
			return False
		actual = self._posicion_para_favoritos(paso)
		if paso < 0:
			candidatos = [p for p in posiciones if p < actual]
			destino = candidatos[-1] if candidatos else None
		else:
			candidatos = [p for p in posiciones if p > actual]
			destino = candidatos[0] if candidatos else None
		if destino is None:
			if paso < 0:
				avisos.anunciar(_("No hay ningún favorito antes de este mensaje."), interrumpir=True)
			else:
				avisos.anunciar(_("No hay ningún favorito después de este mensaje."), interrumpir=True)
			return False
		self._mostrar_mensaje_completo(destino, anunciar=not desde_lectura, enfocar_lista=not desde_lectura)
		if desde_lectura:
			self.actualizar_panel_lectura()
		mensaje = completos[destino]
		principio = favoritos.extracto(mensaje.texto, 150) or _("(vacío)")
		numero, total = posiciones.index(destino) + 1, len(posiciones)
		nota = self._marcas_actuales().nota(mensaje)
		if nota:
			aviso = _("Favorito {n} de {t}, nota: {nota}. {q}: {p}").format(n=numero, t=total, nota=nota,
				q=self._quien(mensaje), p=principio)
		else:
			aviso = _("Favorito {n} de {t}. {q}: {p}").format(n=numero, t=total, q=self._quien(mensaje), p=principio)
		avisos.anunciar(aviso, interrumpir=True)
		return True

	def _tecla_favoritos(self, tecla: int, mods: int, desde_lectura: bool) -> bool:
		"""Alt+Arriba y Alt+Abajo en la lista de mensajes y en el panel de lectura. Devuelve si la ha atendido."""
		if mods != wx.MOD_ALT:
			return False
		if tecla in (wx.WXK_UP, wx.WXK_NUMPAD_UP):
			self.ir_a_favorito(-1, desde_lectura)
			return True
		if tecla in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
			self.ir_a_favorito(1, desde_lectura)
			return True
		return False

	def _al_tecla_lectura(self, evento: wx.KeyEvent) -> None:
		if not self._tecla_favoritos(evento.GetKeyCode(), evento.GetModifiers(), desde_lectura=True):
			evento.Skip()

	def _quien_favorito(self, favorito: MensajeFavorito) -> str:
		if favorito.rol == "asistente":
			fila = self.fila_de(favorito.sesion)
			if fila is not None:
				return nombre_agente(fila.proveedor)
		return MensajeVista(favorito.rol, "", 0.0).nombre_rol

	def valores_favorito(self, favorito: MensajeFavorito) -> list[str]:
		"""Columnas de un favorito en la lista de todos: Mensaje, Nota, Sesión, Quién y Fecha."""
		from app.ui.lectura_listas import escribir_fecha

		fila = self.fila_de(favorito.sesion)
		sesion = (fila.titulo or _("Sesión sin título")) if fila is not None else _("Sesión que ya no está en Ctrl+IA")
		return [favorito.extracto or _("(vacío)"), favorito.nota, sesion, self._quien_favorito(favorito),
			escribir_fecha(favorito.fecha, self.opcion)]

	def accion_mensajes_favoritos(self) -> None:
		"""Ctrl+Alt+Mayús+F: lista de todos los mensajes favoritos de todas las sesiones."""
		from app.ui.dialogos.favoritos import DialogoFavoritos

		filas = [(favorito, self.valores_favorito(favorito)) for favorito in self.almacen.favoritos()]
		dialogo = DialogoFavoritos(self, filas, existe=lambda f: self.fila_de(f.sesion) is not None,
			quitar=self.quitar_favorito_guardado, copiar=self.copiar_favorito, anotar=self.anotar_favorito_guardado)
		try:
			if dialogo.ShowModal() != wx.ID_OK or dialogo.resultado is None:
				return
			elegido = dialogo.resultado
		finally:
			dialogo.Destroy()
		self.ir_a_favorito_guardado(elegido)

	def quitar_favorito_guardado(self, favorito: MensajeFavorito) -> bool:
		"""Quita un favorito desde la lista de todos (ya confirmado)."""
		quitado = self.almacen.desmarcar_favorito(favorito.sesion, favorito.mensaje)
		if favorito.sesion == self.sesion_actual:
			self._favoritos_cambiados()
		return quitado

	def anotar_favorito_guardado(self, favorito: MensajeFavorito, nota: str) -> bool:
		"""Escribe o quita (vacía) la nota de un favorito desde la lista de todos."""
		cambiado = self.almacen.poner_nota_favorito(favorito.sesion, favorito.mensaje, favoritos.limpiar_nota(nota))
		if favorito.sesion == self.sesion_actual:
			self._favoritos_cambiados()
		return cambiado

	@staticmethod
	def _localizador(favorito: MensajeFavorito) -> Callable[[list[MensajeVista]], int]:
		def localizar(mensajes: list[MensajeVista]) -> int:
			marcas = favoritos.Marcas.crear(favorito.sesion, [favorito], mensajes)
			return favoritos.buscar(marcas, mensajes, favorito.mensaje)

		return localizar

	def ir_a_favorito_guardado(self, favorito: MensajeFavorito) -> bool:
		"""Abre la sesión del favorito y deja el foco en ese mensaje (cargando los anteriores si hace falta)."""
		if self.fila_de(favorito.sesion) is None:
			mostrar_mensaje(self, _(
				"La sesión de este mensaje ya no está en Ctrl+IA: se borró o es de un programa cuyas sesiones ya no se leen "
				"(Preferencias, Historial). Puedes quitarlo de favoritos con Suprimir en la lista de mensajes favoritos."
			), _("Mensajes favoritos"))
			return False
		no_esta = _("No se encuentra ese mensaje en la sesión: puede que ya no exista. Estás en el último.")
		localizar = self._localizador(favorito)
		if favorito.sesion == self.sesion_actual and self.total_mensajes():
			indice = localizar(self.mensajes_completos())
			if indice < 0:
				self._mostrar_mensaje_completo(self.total_mensajes() - 1, anunciar=False)
				avisos.anunciar(no_esta, interrumpir=True)
				return False
			self._mostrar_mensaje_completo(indice)
			return True
		self._ir_a_mensaje_de_sesion(favorito.sesion, None, localizar, no_esta)
		return True

	def copiar_favorito(self, favorito: MensajeFavorito) -> None:
		"""Copia el texto completo de un favorito (lee su sesión si no es la abierta)."""
		localizar = self._localizador(favorito)

		def hecho(mensajes: list[MensajeVista]) -> None:
			indice = localizar(mensajes)
			if indice < 0:
				self.copiar(favorito.extracto)
				avisos.anunciar(_("No se encuentra el mensaje completo; se ha copiado el principio que se guardó."),
					interrumpir=True)
				return
			mensaje = mensajes[indice]
			self.copiar(mensaje.texto + ("\n\n" + mensaje.detalle if mensaje.detalle else ""))

		if favorito.sesion == self.sesion_actual and self.total_mensajes():
			hecho(self.mensajes_completos())
		elif self.fila_de(favorito.sesion) is None:
			hecho([])
		else:
			self.en_segundo_plano(self.gestor.mensajes, favorito.sesion, al_terminar=hecho, al_fallar=lambda _e: hecho([]))

	# ------------------------------------------------------------------
	# Listas de la sesión
	# ------------------------------------------------------------------

	def _abrir_listas(self, pestana: str) -> None:
		if not self.sesion_actual:
			avisos.anunciar(_("Primero abre una sesión."), interrumpir=True)
			return
		from app.ui.dialogos.listas import DialogoListas

		fila = self.fila_de(self.sesion_actual)
		# Las listas salen siempre de la conversación entera, aunque no esté toda cargada.
		completos = self.mensajes_completos()
		dialogo = DialogoListas(self, completos, fila.titulo if fila else "", pestana)
		try:
			dialogo.ShowModal()
			indice = getattr(dialogo, "indice_mensaje", None)
			self._ultima_pagina_listas = dialogo.pagina_actual()
		finally:
			dialogo.Destroy()
		if indice is not None and 0 <= indice < len(completos):
			self._mostrar_mensaje_completo(indice)

	def accion_listas(self) -> None:
		"""Abre las listas en la última página usada (la primera vez, los bloques de código)."""
		self._abrir_listas(getattr(self, "_ultima_pagina_listas", "codigo"))

	def accion_enlaces(self) -> None:
		self._abrir_listas("enlaces")

	def accion_bloques(self) -> None:
		self._abrir_listas("codigo")

	def accion_archivos(self) -> None:
		self._abrir_listas("archivos")

	def accion_copiar_ultimo_bloque(self) -> None:
		from app.services.extraccion import ultimo_bloque

		bloque = ultimo_bloque(self.mensajes_completos())
		if bloque is None:
			avisos.anunciar(_("No hay bloques de código en esta sesión."), interrumpir=True)
			return
		self.copiar(bloque.codigo)

	def accion_alternar_herramientas(self) -> None:
		valor = not bool(self.opcion("mensajes.herramientas", True))
		self.poner_opcion("mensajes.herramientas", valor)
		self._elementos_menu["herramientas"].Check(valor)
		avisos.anunciar(_("Se muestran las acciones del agente.") if valor else _("Se ocultan las acciones del agente."), interrumpir=True)

	def accion_memoria(self) -> None:
		from app.ui.dialogos.memoria import DialogoMemoria

		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else self.fila_seleccionada()
		carpeta = Path(fila.carpeta) if fila and fila.carpeta and Path(fila.carpeta).is_dir() else None
		proveedor = fila.proveedor if fila else str(self.opcion("agentes.predeterminado", "claude"))
		dialogo = DialogoMemoria(self, carpeta, proveedor, editor=str(self.opcion("agentes.editor_externo", "") or ""))
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()

	def momento(self) -> float:
		return time.time()
