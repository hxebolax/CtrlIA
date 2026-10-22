"""«Configuración de los agentes»: cambia el settings.json o config.toml de cada agente.

La ventana:

- «Agente» y «Ámbito» (tu usuario, el proyecto compartido o solo tú en el
  proyecto) eligen el archivo; «Carpeta del proyecto» dice de qué proyecto.
- «Archivo» dice la ruta y si existe, no existe todavía o tiene un error.
- «Categoría» y «Filtrar» acotan la lista.
- «Opciones»: cada opción con su valor actual. Intro la cambia en un cuadro con
  el control adecuado a su tipo y su explicación; Espacio activa o desactiva las
  de sí o no; Suprimir quita el valor (vuelve al predeterminado del agente).
- Nada se escribe hasta «Guardar» (Ctrl+S): entonces se confirma lo arriesgado,
  se hace copia de seguridad y se aplican los cambios sobre el archivo recién
  leído, sin tocar lo demás.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import wx

from app.services.config_agentes import agentes_configurables
from app.services.config_agentes.modelo import (
	BOOL,
	DECIMAL,
	DICCIONARIO,
	ENTERO,
	JSON,
	LISTA,
	OPCION,
	QUITAR,
	TEXTO,
	AgenteConfigurable,
	DocumentoConfig,
	ErrorConfigAgente,
	Fila,
	describir,
	filas,
	validar,
)
from app.services.gestor import coincide
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import (
	confirmar,
	crear_botonera,
	enfocar,
	mostrar_error,
	mostrar_mensaje,
	pedir_texto,
	preparar_dialogo,
)
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _

log = logging.getLogger(__name__)

TODAS = _("Todas")
SIN_DEFINIR = _("Sin definir")


def _texto_valor(fila: Fila, valor: Any, existe: bool) -> str:
	if not existe:
		defecto = fila.ajuste.defecto if fila.ajuste and fila.ajuste.defecto else _("el del agente")
		return _("{s} (predeterminado: {d})").format(s=SIN_DEFINIR, d=defecto)
	return describir(fila.ajuste, valor)


def explicacion(fila: Fila, agente: AgenteConfigurable, ambito: str) -> str:
	"""Texto de ayuda completo de una opción (para Ctrl+H y el cuadro de cambiar)."""
	ajuste = fila.ajuste
	if ajuste is None:
		return _("Clave «{c}» del archivo que Ctrl+IA no conoce. Puedes cambiar su valor como JSON o quitarla.").format(c=fila.clave)
	partes = [ajuste.ayuda or ajuste.etiqueta]
	partes.append(_("Clave en el archivo: {c}.").format(c=ajuste.clave))
	if ajuste.defecto:
		partes.append(_("Si no se define: {d}.").format(d=ajuste.defecto))
	if ajuste.ambitos and ambito not in ajuste.ambitos:
		nombres = ", ".join(a.nombre for a in agente.ambitos if a.id in ajuste.ambitos)
		partes.append(_("Atención: en este archivo no tiene efecto; solo vale en: {a}.").format(a=nombres))
	if ajuste.obsoleta:
		partes.append(_("Esta opción ya no tiene efecto: puedes quitarla con Suprimir."))
	if ajuste.riesgo:
		partes.append(_("Cuidado: {r}").format(r=ajuste.riesgo))
	return " ".join(partes)


class DialogoValor(wx.Dialog):
	"""Cambia el valor de una opción con el control adecuado a su tipo. Tras ``wx.ID_OK``, ``valor``."""

	def __init__(self, padre: wx.Window | None, fila: Fila, valor: Any, existe: bool, texto_ayuda: str) -> None:
		ajuste = fila.ajuste
		nombre = ajuste.etiqueta if ajuste else fila.clave
		super().__init__(padre, title=_("Cambiar «{n}»").format(n=nombre), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.fila = fila
		self.tipo = ajuste.tipo if ajuste else JSON
		self.valor: Any = None
		self._elementos: list[str] = []
		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_ayuda = crear_campo(panel, _("E&xplicación:"), wx.TextCtrl, sizer=raiz, value=texto_ayuda,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP, ayuda=_("Qué hace esta opción. Solo lectura."))
		self.campo_ayuda.SetMinSize(self.FromDIP(wx.Size(460, 90)))
		self.control: wx.Window
		self.campo_otro: wx.TextCtrl | None = None
		if self.tipo == BOOL:
			_e, self.control = crear_campo(panel, _("&Valor:"), wx.Choice, sizer=raiz, choices=[_("Activado"), _("Desactivado")],
				ayuda=_("Activado o desactivado."))
			self.control.SetSelection(0 if (valor if existe else False) else 1)
		elif self.tipo == OPCION:
			etiquetas = [e for _v, e in ajuste.opciones] + ([_("Otro valor (escríbelo debajo)")] if ajuste.libre else [])
			_e, self.control = crear_campo(panel, _("&Valor:"), wx.Choice, sizer=raiz, choices=etiquetas,
				ayuda=_("Elige el valor."))
			valores = [v for v, _e in ajuste.opciones]
			if existe and valor in valores:
				self.control.SetSelection(valores.index(valor))
			elif existe and ajuste.libre:
				self.control.SetSelection(len(valores))
			else:
				self.control.SetSelection(0)
			if ajuste.libre:
				_e, self.campo_otro = crear_campo(panel, _("&Otro valor:"), wx.TextCtrl, sizer=raiz,
					value=str(valor) if existe and valor not in valores else "",
					ayuda=_("Escribe aquí un valor que no esté en la lista; se usa si eliges «Otro valor»."))
		elif self.tipo in (TEXTO, ENTERO, DECIMAL):
			_e, self.control = crear_campo(panel, _("&Valor:"), wx.TextCtrl, sizer=raiz,
				value="" if not existe or valor is None else str(valor),
				ayuda=_("Escribe el valor.") if self.tipo == TEXTO else _("Escribe un número."))
		elif self.tipo in (LISTA, DICCIONARIO):
			if self.tipo == LISTA:
				self._elementos = [str(v) for v in (valor if existe and isinstance(valor, list) else [])]
			else:
				self._elementos = [f"{k}={v}" for k, v in (valor.items() if existe and isinstance(valor, dict) else [])]
			_e, self.control = crear_campo(panel, _("&Elementos:"), wx.ListBox, sizer=raiz, choices=list(self._elementos),
				ayuda=_("Un elemento por línea. Insertar añade, F2 cambia el elegido, Supr lo quita, Alt+flecha arriba y "
					"abajo lo mueven.") if self.tipo == LISTA else _("Una variable por línea, como NOMBRE=valor. Insertar "
					"añade, F2 cambia la elegida y Supr la quita."))
			self.control.SetMinSize(self.FromDIP(wx.Size(460, 150)))
			if self._elementos:
				self.control.SetSelection(0)
			botones = wx.BoxSizer(wx.HORIZONTAL)
			for etiqueta, ayuda_boton, accion in (
				(_("&Nuevo..."), _("Añade un elemento (Insertar)."), self.anadir),
				(_("&Cambiar..."), _("Cambia el elemento elegido (F2)."), self.cambiar),
				(_("&Quitar"), _("Quita el elemento elegido (Supr)."), self.quitar),
			):
				boton = wx.Button(panel, label=etiqueta)
				boton.SetHelpText(ayuda_boton)
				boton.Bind(wx.EVT_BUTTON, lambda _evento, a=accion: a())
				botones.Add(boton, 0, wx.ALL, 4)
			raiz.Add(botones, 0, wx.LEFT, 4)
			self.control.Bind(wx.EVT_KEY_DOWN, self._al_tecla_lista)
		else:  # JSON
			texto = json.dumps(valor, ensure_ascii=False, indent=2) if existe else ""
			_e, self.control = crear_campo(panel, _("&Valor (JSON):"), wx.TextCtrl, sizer=raiz, value=texto,
				style=wx.TE_MULTILINE | wx.TE_DONTWRAP,
				ayuda=_("Escribe el valor en formato JSON: true, false, un número, \"un texto\" entre comillas, [lista] u {objeto}."))
			self.control.SetMinSize(self.FromDIP(wx.Size(460, 160)))
		raiz.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		for identificador, texto_boton in ((wx.ID_OK, _("Usa este valor. Se guarda al pulsar «Guardar» en la ventana anterior.")),
				(wx.ID_CANCEL, _("Cierra sin cambiar el valor."))):
			boton = panel.FindWindowById(identificador, panel)
			if boton is not None:
				boton.SetHelpText(texto_boton)
		panel.SetSizer(raiz)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.Bind(wx.EVT_BUTTON, lambda _evento: self.aceptar(), id=wx.ID_OK)
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.control)

	# -- Listas -------------------------------------------------------------

	def _refrescar(self, seleccion: int) -> None:
		self.control.Set(list(self._elementos))
		if self._elementos:
			self.control.SetSelection(max(0, min(seleccion, len(self._elementos) - 1)))

	def _pedir(self, valor: str = "") -> str | None:
		etiqueta = _("&Elemento:") if self.tipo == LISTA else _("&Variable (NOMBRE=valor):")
		texto = pedir_texto(self, etiqueta, _("Elemento"), valor)
		if texto is None or not texto.strip():
			return None
		texto = texto.strip()
		if self.tipo == DICCIONARIO and ("=" not in texto or not texto.split("=", 1)[0].strip()):
			mostrar_error(self, _("Escribe la variable como NOMBRE=valor."))
			return None
		return texto

	def anadir(self) -> None:
		texto = self._pedir()
		if texto is not None:
			self._elementos.append(texto)
			self._refrescar(len(self._elementos) - 1)
			voz.hablar(_("Añadido. {n} elementos.").format(n=len(self._elementos)))

	def cambiar(self) -> None:
		indice = self.control.GetSelection()
		if indice == wx.NOT_FOUND:
			voz.hablar(_("No hay ningún elemento elegido"))
			return
		texto = self._pedir(self._elementos[indice])
		if texto is not None:
			self._elementos[indice] = texto
			self._refrescar(indice)

	def quitar(self) -> None:
		indice = self.control.GetSelection()
		if indice == wx.NOT_FOUND:
			voz.hablar(_("No hay ningún elemento elegido"))
			return
		del self._elementos[indice]
		self._refrescar(indice)
		voz.hablar(_("Quitado. Quedan {n}.").format(n=len(self._elementos)))

	def mover(self, paso: int) -> None:
		indice = self.control.GetSelection()
		destino = indice + paso
		if indice == wx.NOT_FOUND or not 0 <= destino < len(self._elementos):
			return
		self._elementos[indice], self._elementos[destino] = self._elementos[destino], self._elementos[indice]
		self._refrescar(destino)
		voz.hablar(_("Posición {n}").format(n=destino + 1))

	def _al_tecla_lista(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if mods == wx.MOD_NONE and tecla in (wx.WXK_INSERT, wx.WXK_NUMPAD_INSERT):
			self.anadir()
		elif mods == wx.MOD_NONE and tecla == wx.WXK_F2:
			self.cambiar()
		elif mods == wx.MOD_NONE and tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.quitar()
		elif mods == wx.MOD_ALT and tecla in (wx.WXK_UP, wx.WXK_DOWN) and self.tipo == LISTA:
			self.mover(-1 if tecla == wx.WXK_UP else 1)
		else:
			evento.Skip()

	# -- Aceptar ------------------------------------------------------------

	def leer(self) -> Any:
		"""El valor de los controles; lanza ``ValueError`` con el mensaje si no se entiende."""
		ajuste = self.fila.ajuste
		if self.tipo == BOOL:
			return self.control.GetSelection() == 0
		if self.tipo == OPCION:
			indice = self.control.GetSelection()
			if indice < len(ajuste.opciones):
				return ajuste.opciones[indice][0]
			return self.campo_otro.GetValue().strip() if self.campo_otro else ""
		if self.tipo == TEXTO:
			return self.control.GetValue()
		if self.tipo == ENTERO:
			try:
				return int(self.control.GetValue().strip())
			except ValueError:
				raise ValueError(_("Escribe un número entero.")) from None
		if self.tipo == DECIMAL:
			try:
				return float(self.control.GetValue().strip().replace(",", "."))
			except ValueError:
				raise ValueError(_("Escribe un número.")) from None
		if self.tipo == LISTA:
			return list(self._elementos)
		if self.tipo == DICCIONARIO:
			return {k.strip(): v for k, v in (e.split("=", 1) for e in self._elementos)}
		try:
			return json.loads(self.control.GetValue())
		except ValueError as error:
			raise ValueError(_("El JSON no es válido: {e}").format(e=error)) from None

	def aceptar(self) -> bool:
		try:
			valor = self.leer()
		except ValueError as error:
			mostrar_error(self, str(error))
			return False
		error = validar(self.fila.ajuste, valor) if self.fila.ajuste else None
		if error is not None:
			mostrar_error(self, error)
			return False
		self.valor = valor
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True


class DialogoConfigAgentes(wx.Dialog):
	"""Editor accesible de los archivos de configuración de los agentes.

	:param carpeta: carpeta de proyecto propuesta (la de la sesión abierta).
	:param recientes: otras carpetas para elegir.
	:param carpeta_copias: dónde se guardan las copias de seguridad.
	"""

	def __init__(self, padre: wx.Window | None, carpeta: Path | None = None, recientes: list[str] | None = None,
			carpeta_copias: Path | None = None, agente_inicial: str = "") -> None:
		titulo = _("Configuración de los agentes")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
		self.agentes = agentes_configurables()
		self.carpeta_copias = carpeta_copias
		self.cambios: dict[str, Any] = {}
		self.documento: DocumentoConfig | None = None
		self.error = ""
		self._filas: list[Fila] = []
		self._visibles: list[Fila] = []

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		fila_arriba = wx.BoxSizer(wx.HORIZONTAL)
		columna = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_agente = crear_campo(panel, _("&Agente:"), wx.Choice, sizer=columna,
			choices=[a.nombre for a in self.agentes.values()], ayuda=_("De qué agente es la configuración."))
		ids = list(self.agentes)
		self.campo_agente.SetSelection(ids.index(agente_inicial) if agente_inicial in ids else 0)
		_e, self.campo_ambito = crear_campo(panel, _("Á&mbito:"), wx.Choice, sizer=columna, choices=[],
			ayuda=_("Qué archivo: el de tu usuario (vale para todo) o el de un proyecto."))
		fila_arriba.Add(columna, 1, wx.EXPAND)
		raiz.Add(fila_arriba, 0, wx.EXPAND)
		fila_carpeta = wx.BoxSizer(wx.HORIZONTAL)
		opciones_carpeta = list(dict.fromkeys(([str(carpeta)] if carpeta else []) + list(recientes or [])))
		_e, self.campo_carpeta = crear_campo(panel, _("Carpeta del &proyecto:"), wx.ComboBox, sizer=fila_carpeta, horizontal=True,
			value=opciones_carpeta[0] if opciones_carpeta else "", choices=opciones_carpeta,
			ayuda=_("El proyecto cuyo archivo quieres cambiar. Solo se usa en los ámbitos de proyecto."))
		self.boton_examinar = wx.Button(panel, label=_("E&xaminar..."))
		self.boton_examinar.SetHelpText(_("Elige la carpeta del proyecto."))
		fila_carpeta.Add(self.boton_examinar, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 6)
		raiz.Add(fila_carpeta, 0, wx.EXPAND)
		_e, self.campo_archivo = crear_campo(panel, _("Arc&hivo:"), wx.TextCtrl, sizer=raiz,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP, ayuda=_("Ruta del archivo y su estado. Solo lectura."))
		self.campo_archivo.SetMinSize(self.FromDIP(wx.Size(-1, 50)))
		fila_filtro = wx.BoxSizer(wx.HORIZONTAL)
		_e, self.campo_categoria = crear_campo(panel, _("Ca&tegoría:"), wx.Choice, sizer=fila_filtro, horizontal=True,
			choices=[TODAS], ayuda=_("Muestra solo las opciones de una categoría."))
		_e, self.campo_filtro = crear_campo(panel, _("&Filtrar:"), wx.TextCtrl, sizer=fila_filtro, horizontal=True,
			ayuda=_("Escribe para ver solo las opciones que contienen esas palabras (en su nombre, su explicación o su clave)."))
		raiz.Add(fila_filtro, 0, wx.EXPAND)
		self.etiqueta_lista = wx.StaticText(panel, label=_("&Opciones:"))
		raiz.Add(self.etiqueta_lista, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		lectura = getattr(padre, "preferencias_lectura", None)
		self.lista = ListaAccesible(panel, _("Opciones"),
			[(_("Opción"), 260), (_("Valor"), 260), (_("Categoría"), 160), (_("Clave"), 220)], leer_preferencias=lectura)
		self.lista.SetHelpText(_(
			"Opciones del archivo con su valor. Intro cambia la opción; Espacio activa o desactiva las de sí o no; "
			"Suprimir quita el valor (vuelve al del agente); Control+H explica la opción; Control+S guarda. "
			"Las cambiadas y sin guardar dicen «sin guardar»."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(760, 300)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_cambiar = self._boton(panel, botones, _("&Cambiar..."), _("Cambia la opción seleccionada (Intro)."), self.cambiar)
		self.boton_quitar = self._boton(panel, botones, _("&Quitar el valor"),
			_("Quita la opción del archivo: el agente usará su valor predeterminado (Supr)."), self.quitar)
		self.boton_guardar = self._boton(panel, botones, _("&Guardar"), _("Escribe los cambios en el archivo (Control+S)."), self.guardar)
		self.boton_editor = self._boton(panel, botones, _("Abrir en el &editor"),
			_("Abre el archivo en el programa asociado, para los cambios que no están en la lista."), self.abrir_en_editor)
		self.boton_documentacion = self._boton(panel, botones, _("&Documentación"),
			_("Abre en el navegador la documentación oficial de estas opciones."), self.abrir_documentacion)
		raiz.Add(botones, 0, wx.LEFT, 2)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("Ce&rrar"))
		self.boton_cerrar.SetHelpText(_("Cierra. Si hay cambios sin guardar, pregunta antes."))
		raiz.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
		panel.SetSizer(raiz)

		self.campo_agente.Bind(wx.EVT_CHOICE, lambda _e: self._al_cambiar_archivo(rellenar_ambitos=True))
		self.campo_ambito.Bind(wx.EVT_CHOICE, lambda _e: self._al_cambiar_archivo())
		self.campo_carpeta.Bind(wx.EVT_COMBOBOX, lambda _e: self._al_cambiar_archivo())
		self.campo_carpeta.Bind(wx.EVT_KILL_FOCUS, lambda evento: (self._al_salir_carpeta(), evento.Skip()))
		self.boton_examinar.Bind(wx.EVT_BUTTON, lambda _e: self._examinar())
		self.campo_categoria.Bind(wx.EVT_CHOICE, lambda _e: self.mostrar())
		self.campo_filtro.Bind(wx.EVT_TEXT, lambda _e: self.mostrar())
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.cambiar())
		vincular_menu_contextual(self.lista, self._crear_menu)
		self._carpeta_anterior = self.campo_carpeta.GetValue()
		self._al_cambiar_archivo(rellenar_ambitos=True, preguntar=False)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.lista)
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		self.Bind(wx.EVT_BUTTON, lambda _e: self.cerrar(), id=wx.ID_CANCEL)
		self.Bind(wx.EVT_CLOSE, lambda _e: self.cerrar())

	@staticmethod
	def _boton(panel: wx.Window, sizer: wx.Sizer, etiqueta: str, ayuda: str, accion) -> wx.Button:
		boton = wx.Button(panel, label=etiqueta)
		boton.SetHelpText(ayuda)
		boton.Bind(wx.EVT_BUTTON, lambda _evento: accion())
		sizer.Add(boton, 0, wx.ALL, 4)
		return boton

	# -- Archivo elegido ----------------------------------------------------

	@property
	def agente(self) -> AgenteConfigurable:
		return list(self.agentes.values())[max(0, self.campo_agente.GetSelection())]

	@property
	def ambito(self) -> str:
		indice = max(0, self.campo_ambito.GetSelection())
		return self.agente.ambitos[min(indice, len(self.agente.ambitos) - 1)].id

	def carpeta(self) -> Path | None:
		texto = self.campo_carpeta.GetValue().strip().strip('"')
		return Path(texto) if texto else None

	def ruta(self) -> Path | None:
		return self.agente.ambito(self.ambito).ruta(self.carpeta())

	def _al_salir_carpeta(self) -> None:
		if self.campo_carpeta.GetValue() != self._carpeta_anterior:
			wx.CallAfter(self._al_cambiar_archivo)

	def _examinar(self) -> None:
		with wx.DirDialog(self, _("Carpeta del proyecto"), defaultPath=self.campo_carpeta.GetValue()) as dialogo:
			if dialogo.ShowModal() == wx.ID_OK:
				self.campo_carpeta.SetValue(dialogo.GetPath())
				self._al_cambiar_archivo()
		enfocar(self.campo_carpeta)

	def _al_cambiar_archivo(self, rellenar_ambitos: bool = False, preguntar: bool = True) -> None:
		if preguntar and self.cambios and not self._resolver_pendientes():
			return
		self.cambios.clear()
		if rellenar_ambitos:
			self.campo_ambito.Set([a.nombre for a in self.agente.ambitos])
			self.campo_ambito.SetSelection(0)
		ambito = self.agente.ambito(self.ambito)
		self.campo_carpeta.Enable(ambito.necesita_carpeta)
		self.boton_examinar.Enable(ambito.necesita_carpeta)
		self._carpeta_anterior = self.campo_carpeta.GetValue()
		self.cargar()

	def cargar(self) -> None:
		"""Lee el archivo elegido y rellena la lista."""
		agente, ruta = self.agente, self.ruta()
		self.documento, self.error = None, ""
		if ruta is None:
			self.error = _("Elige la carpeta del proyecto.")
			estado = self.error
		elif agente.ambito(self.ambito).necesita_carpeta and not (self.carpeta() and self.carpeta().is_dir()):
			self.error = _("La carpeta del proyecto no existe.")
			estado = f"{ruta}\n{self.error}"
		else:
			try:
				self.documento = DocumentoConfig(ruta, agente.formato)
			except ErrorConfigAgente as error:
				self.error = str(error)
			if self.documento is None:
				estado = _("{r}\nNo se puede usar: {e} Ábrelo en el editor para arreglarlo; Ctrl+IA no lo tocará.").format(
					r=ruta, e=self.error)
			elif self.documento.existe:
				estado = _("{r}\nExiste.").format(r=ruta)
			else:
				estado = _("{r}\nNo existe todavía: se creará al guardar.").format(r=ruta)
		self.campo_archivo.ChangeValue(f"{estado}\n{agente.nota}".strip())
		self._filas = filas(agente, self.documento, self.ambito) if self.documento is not None else []
		categorias = [TODAS, *list(dict.fromkeys(f.categoria for f in self._filas))]
		anterior = self.campo_categoria.GetStringSelection()
		self.campo_categoria.Set(categorias)
		self.campo_categoria.SetSelection(categorias.index(anterior) if anterior in categorias else 0)
		for control in (self.lista, self.boton_cambiar, self.boton_quitar, self.boton_guardar):
			control.Enable(self.documento is not None)
		self.boton_editor.Enable(ruta is not None and ruta.is_file())
		self.mostrar()

	# -- Lista --------------------------------------------------------------

	def valor_de(self, fila: Fila) -> tuple[bool, Any]:
		"""``(existe, valor)`` teniendo en cuenta los cambios sin guardar."""
		if fila.clave in self.cambios:
			cambio = self.cambios[fila.clave]
			return (False, None) if cambio is QUITAR else (True, cambio)
		return fila.existe, fila.valor

	def _valores_fila(self, fila: Fila) -> list[str]:
		existe, valor = self.valor_de(fila)
		texto = _texto_valor(fila, valor, existe)
		if fila.clave in self.cambios:
			texto += _(" (sin guardar)")
		nombre = fila.ajuste.etiqueta if fila.ajuste else fila.clave
		if fila.ajuste and fila.ajuste.riesgo:
			nombre += _(" (cuidado)")
		return [nombre, texto, fila.categoria, fila.clave]

	def mostrar(self) -> None:
		"""Rellena la lista con la categoría y el filtro elegidos, sin perder la opción seleccionada."""
		categoria = self.campo_categoria.GetStringSelection()
		texto = self.campo_filtro.GetValue().strip()
		actual = self.actual()
		self._visibles = [
			f for f in self._filas
			if (categoria in ("", TODAS) or f.categoria == categoria)
			and (not texto or coincide(" ".join((f.ajuste.etiqueta if f.ajuste else "", f.ajuste.ayuda if f.ajuste else "",
				f.clave, f.categoria)), texto))
		]
		self.lista.reemplazar_filas([self._valores_fila(f) for f in self._visibles], conservar_foco=False)
		if actual is not None:
			indice = next((i for i, f in enumerate(self._visibles) if f.clave == actual.clave), None)
			if indice is not None:
				self.lista.enfocar_fila(indice, anunciar=False)
		pendientes = _(", {n} sin guardar").format(n=len(self.cambios)) if self.cambios else ""
		self.etiqueta_lista.SetLabel(_("&Opciones ({n} de {t}{p}):").format(n=len(self._visibles), t=len(self._filas), p=pendientes))
		self.lista.SetName(_("Opciones de {a} ({n})").format(a=self.agente.nombre, n=len(self._visibles)))

	def actual(self) -> Fila | None:
		indice = self.lista.fila_actual()
		return self._visibles[indice] if 0 <= indice < len(self._visibles) else None

	def _repintar(self, fila: Fila) -> None:
		indice = next((i for i, f in enumerate(self._visibles) if f is fila), None)
		if indice is not None:
			self.lista.actualizar_fila(indice, self._valores_fila(fila))
		pendientes = _(", {n} sin guardar").format(n=len(self.cambios)) if self.cambios else ""
		self.etiqueta_lista.SetLabel(_("&Opciones ({n} de {t}{p}):").format(n=len(self._visibles), t=len(self._filas), p=pendientes))

	def _crear_menu(self) -> wx.Menu | None:
		if self.actual() is None:
			return None
		menu = wx.Menu()
		for etiqueta, accion in (
			(_("&Cambiar...\tIntro"), self.cambiar),
			(_("&Quitar el valor\tSupr"), self.quitar),
			(_("E&xplicación\tCtrl+H"), self.explicar),
			(_("&Guardar\tCtrl+S"), self.guardar),
		):
			menu.Bind(wx.EVT_MENU, lambda _e, a=accion: a(), menu.Append(wx.ID_ANY, etiqueta))
		return menu

	# -- Acciones -----------------------------------------------------------

	def _poner(self, fila: Fila, valor: Any) -> None:
		original_existe, original = fila.existe, fila.valor
		if (valor is QUITAR and not original_existe) or (valor is not QUITAR and original_existe and valor == original):
			self.cambios.pop(fila.clave, None)
		else:
			self.cambios[fila.clave] = valor
		self._repintar(fila)

	def _sin_seleccion(self) -> None:
		voz.hablar(_("No hay ninguna opción seleccionada") if self._visibles else _("No hay opciones"))

	def cambiar(self) -> bool:
		"""Abre el cuadro para cambiar la opción seleccionada (Intro)."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		existe, valor = self.valor_de(fila)
		dialogo = DialogoValor(self, fila, valor, existe, explicacion(fila, self.agente, self.ambito))
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return False
			nuevo = dialogo.valor
		finally:
			dialogo.Destroy()
		self._poner(fila, nuevo)
		voz.hablar(_("{n}: {v}. Sin guardar.").format(n=fila.ajuste.etiqueta if fila.ajuste else fila.clave,
			v=describir(fila.ajuste, nuevo)))
		return True

	def alternar(self) -> bool:
		"""Espacio: activa o desactiva una opción de sí o no."""
		fila = self.actual()
		if fila is None or fila.ajuste is None or fila.ajuste.tipo != BOOL:
			return False
		existe, valor = self.valor_de(fila)
		nuevo = not (bool(valor) if existe else False)
		self._poner(fila, nuevo)
		voz.hablar(_("Activado, sin guardar") if nuevo else _("Desactivado, sin guardar"))
		return True

	def quitar(self) -> bool:
		"""Quita el valor del archivo (Supr): el agente usará su predeterminado."""
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		existe, _valor = self.valor_de(fila)
		if not existe:
			voz.hablar(_("Ya está sin definir"))
			return False
		self._poner(fila, QUITAR)
		voz.hablar(_("Sin definir, sin guardar"))
		return True

	def explicar(self) -> None:
		fila = self.actual()
		if fila is not None:
			from app.ui.visor_ayuda import mostrar_documento

			mostrar_documento(self, fila.ajuste.etiqueta if fila.ajuste else fila.clave,
				explicacion(fila, self.agente, self.ambito), forzar_texto=True)

	def guardar(self) -> bool:
		"""Escribe los cambios (Control+S), confirmando antes lo arriesgado."""
		if self.documento is None:
			mostrar_error(self, self.error or _("No hay ningún archivo que guardar."), _("Guardar"))
			return False
		if not self.cambios:
			voz.hablar(_("No hay cambios que guardar"))
			return False
		por_clave = {f.clave: f for f in self._filas}
		arriesgados = [
			f"{por_clave[c].ajuste.etiqueta}: {por_clave[c].ajuste.riesgo}" for c, v in self.cambios.items()
			if c in por_clave and por_clave[c].ajuste and por_clave[c].ajuste.riesgo and v is not QUITAR
		]
		if arriesgados and confirmar(self, _("Vas a guardar cambios que conviene pensar:\n\n{l}\n\n¿Guardarlos?").format(
				l="\n".join(arriesgados)), _("Guardar la configuración"), peligroso=True) != wx.ID_YES:
			return False
		try:
			copia = self.documento.guardar(dict(self.cambios), self.carpeta_copias / self.agente.id if self.carpeta_copias else None)
		except ErrorConfigAgente as error:
			mostrar_error(self, str(error), _("Guardar la configuración"))
			return False
		cuantos = len(self.cambios)
		self.cambios.clear()
		self.cargar()
		mensaje = _("Guardado: {n} cambios en {r}.").format(n=cuantos, r=self.documento.ruta)
		if copia is not None:
			mensaje += " " + _("La versión anterior está en {c}.").format(c=copia)
		voz.hablar(mensaje)
		return True

	def abrir_en_editor(self) -> None:
		ruta = self.ruta()
		if ruta is None or not ruta.is_file():
			mostrar_mensaje(self, _("El archivo todavía no existe. Se crea al guardar algún cambio."), _("Abrir en el editor"))
			return
		try:
			os.startfile(ruta)  # noqa: S606 - archivo de configuración elegido por el usuario
		except OSError as error:
			mostrar_error(self, _("No se pudo abrir: {e}").format(e=error), _("Abrir en el editor"))

	def abrir_documentacion(self) -> None:
		import webbrowser

		webbrowser.open(self.agente.documentacion)

	def _resolver_pendientes(self) -> bool:
		"""Pregunta qué hacer con los cambios sin guardar. ``False`` si se cancela."""
		respuesta = confirmar(self, _("Hay {n} cambios sin guardar. ¿Guardarlos?").format(n=len(self.cambios)),
			_("Cambios sin guardar"), con_cancelar=True)
		if respuesta == wx.ID_CANCEL:
			return False
		if respuesta == wx.ID_YES:
			return self.guardar()
		self.cambios.clear()
		return True

	def cerrar(self) -> None:
		if self.cambios and not self._resolver_pendientes():
			return
		if self.IsModal():
			self.EndModal(wx.ID_CANCEL)
		else:
			self.Hide()

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		en_lista = wx.Window.FindFocus() is self.lista
		if mods == wx.MOD_CONTROL and tecla == ord("S"):
			self.guardar()
			return
		if mods == wx.MOD_NONE and tecla == wx.WXK_ESCAPE:
			self.cerrar()
			return
		if en_lista and mods == wx.MOD_NONE and tecla == wx.WXK_SPACE:
			if not self.alternar():
				evento.Skip()
			return
		if en_lista and mods == wx.MOD_NONE and tecla in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.quitar()
			return
		if en_lista and mods == wx.MOD_CONTROL and tecla == ord("H"):
			self.explicar()
			return
		evento.Skip()


def abrir(padre: wx.Window | None, carpeta: Path | None = None, recientes: list[str] | None = None,
		carpeta_copias: Path | None = None, agente_inicial: str = "") -> None:
	dialogo = DialogoConfigAgentes(padre, carpeta, recientes, carpeta_copias, agente_inicial)
	try:
		dialogo.ShowModal()
	finally:
		dialogo.Destroy()


__all__ = ["DialogoConfigAgentes", "DialogoValor", "abrir", "explicacion"]
