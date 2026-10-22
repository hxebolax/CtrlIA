"""Informe de sesiones: todas las sesiones con su tamaño, sus mensajes y los totales.

- «Totales»: texto de solo lectura, una línea por dato (sesiones, tamaño,
  mensajes, por agente, por origen, las que más ocupan...).
- «Mostrar» y «Filtrar» reducen la lista; «Ordenar por» la reordena (por
  tamaño, lo más útil para decidir qué archivar u ocultar).
- La lista: una fila por sesión con sus datos en columnas (título, tamaño,
  mensajes, agente...). Intro abre la sesión en la ventana principal; Alt+Intro
  muestra todos sus datos; Supr la oculta o la vuelve a mostrar; Ctrl+D la da
  por terminada o la vuelve a activar.
- «Ver como documento» abre el informe en el visor, navegable por encabezados;
  «Guardar» lo guarda en Markdown o CSV; «Copiar» lo copia. Ninguno lleva el
  texto de los mensajes.

Cuando se pide abrir una sesión, el diálogo se cierra y deja ``("abrir", clave)``
en ``accion`` para que lo haga la ventana principal.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import wx

from app.services import informe
from app.services.informe import FilaInforme
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.dialogos import mostrar_error, preparar_dialogo
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import crear_campo
from app.ui.accesibilidad.teclado import vincular_menu_contextual
from app.ui.comun import _, nombre_agente

log = logging.getLogger(__name__)

#: Columnas de la lista del informe: (cabecera, ancho).
COLUMNAS: list[tuple[str, int]] = [
	(_("Título"), 260), (_("Tamaño"), 90), (_("Mensajes"), 80), (_("Agente"), 90), (_("Origen"), 130),
	(_("Última actividad"), 130), (_("Estado"), 130), (_("Tus mensajes"), 80), (_("Respuestas"), 80),
	(_("Herramientas"), 90), (_("Archivos cambiados"), 90), (_("Carpeta"), 200), (_("Grupos"), 120), (_("Lectura"), 160),
]

#: «Mostrar»: (id, etiqueta).
FILTROS: list[tuple[str, str]] = [
	("todas", _("Todas")),
	("visibles", _("Las que no están ocultas ni archivadas")),
	("ocultas", _("Ocultas")),
	("archivadas", _("Archivadas")),
	("terminadas", _("Terminadas")),
	("importadas", _("Importadas de otro equipo")),
	("propias", _("Creadas en Ctrl+IA")),
	("problemas", _("Las que no se pudieron leer del todo")),
]

#: «Ordenar por»: (id, etiqueta).
ORDENES: list[tuple[str, str]] = [
	("tamano", _("Tamaño (la que más ocupa primero)")),
	("mensajes", _("Mensajes (la que más tiene primero)")),
	("actividad", _("Última actividad (las recientes primero)")),
	("antiguas", _("Última actividad (las más antiguas primero)")),
	("titulo", _("Título")),
	("agente", _("Agente")),
	("carpeta", _("Carpeta")),
]


def pasa_filtro(fila: FilaInforme, filtro: str, texto: str = "") -> bool:
	if filtro == "visibles" and (fila.oculta or fila.archivada):
		return False
	if filtro in ("ocultas", "archivadas", "terminadas", "importadas", "propias"):
		if not getattr(fila, {"ocultas": "oculta", "archivadas": "archivada", "terminadas": "terminada",
				"importadas": "importada", "propias": "propia"}[filtro]):
			return False
	if filtro == "problemas" and not fila.problema:
		return False
	if filtro.startswith("agente:") and fila.proveedor != filtro.split(":", 1)[1]:
		return False
	if texto:
		from app.services.gestor import coincide

		return coincide(" ".join((fila.titulo, fila.carpeta, fila.origen, nombre_agente(fila.proveedor),
			" ".join(fila.grupos))), texto)
	return True


def ordenar(filas: list[FilaInforme], orden: str) -> list[FilaInforme]:
	if orden == "mensajes":
		return sorted(filas, key=lambda f: (f.mensajes is None, -(f.mensajes or 0), -(f.tamano or 0)))
	if orden == "actividad":
		return sorted(filas, key=lambda f: -f.actividad)
	if orden == "antiguas":
		return sorted(filas, key=lambda f: f.actividad)
	if orden == "titulo":
		return sorted(filas, key=lambda f: f.titulo.casefold())
	if orden == "agente":
		return sorted(filas, key=lambda f: (nombre_agente(f.proveedor), -(f.tamano or 0)))
	if orden == "carpeta":
		return sorted(filas, key=lambda f: (f.carpeta.casefold(), -(f.tamano or 0)))
	return sorted(filas, key=lambda f: (f.tamano is None, -(f.tamano or 0), -(f.mensajes or 0)))


def valores_fila(fila: FilaInforme) -> list[str]:
	recuento = fila.recuento

	def contar(valor: int | None) -> str:
		return informe.numero(valor)

	return [
		fila.titulo, informe.tamano_legible(fila.tamano), contar(recuento.mensajes if recuento else None),
		nombre_agente(fila.proveedor), fila.origen, informe.fecha(fila.actividad), informe.estado_legible(fila),
		contar(recuento.usuario if recuento else None), contar(recuento.asistente if recuento else None),
		contar(recuento.herramientas if recuento else None), contar(recuento.cambios if recuento else None),
		fila.carpeta or _("Sin carpeta"), ", ".join(fila.grupos) or _("Ninguno"), fila.problema or _("Correcta"),
	]


class DialogoInforme(wx.Dialog):
	"""El informe de sesiones.

	:param contexto: la ventana principal (``gestor``, ``preferencias_lectura``...). Sin ella no se
		puede ocultar ni dar por terminada una sesión desde el informe.
	"""

	def __init__(self, padre: wx.Window | None, filas: list[FilaInforme], contexto: Any | None = None) -> None:
		titulo = _("Informe de sesiones")
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX, name=titulo)
		self.todas = list(filas)
		self.visibles: list[FilaInforme] = []
		self.contexto = contexto
		#: Petición para la ventana principal al cerrar: ("abrir", clave).
		self.accion: tuple[str, Any] | None = None
		#: Hubo cambios (ocultar, terminar) que la lista principal debe releer.
		self.cambiado = False
		agentes = sorted({f.proveedor for f in self.todas}, key=nombre_agente)
		self._filtros = FILTROS + [(f"agente:{a}", _("Solo {a}").format(a=nombre_agente(a))) for a in agentes]

		panel = wx.Panel(self)
		raiz = wx.BoxSizer(wx.VERTICAL)
		_e, self.campo_totales = crear_campo(panel, _("&Totales:"), wx.TextCtrl, sizer=raiz,
			value=informe.texto_totales(informe.calcular_totales(self.todas)),
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
			ayuda=_("Totales de todas las sesiones: cuántas hay, cuánto ocupan, cuántos mensajes tienen, por agente, "
				"por origen y las que más ocupan. Una línea por dato."))
		self.campo_totales.SetMinSize(self.FromDIP(wx.Size(-1, 110)))

		opciones = wx.BoxSizer(wx.HORIZONTAL)
		_e, self.campo_mostrar = crear_campo(panel, _("&Mostrar:"), wx.Choice, sizer=opciones, horizontal=True,
			choices=[e for _i, e in self._filtros], ayuda=_("Qué sesiones aparecen en la lista."))
		self.campo_mostrar.SetSelection(0)
		_e, self.campo_orden = crear_campo(panel, _("&Ordenar por:"), wx.Choice, sizer=opciones, horizontal=True,
			choices=[e for _i, e in ORDENES], ayuda=_("En qué orden aparecen las sesiones en la lista."))
		self.campo_orden.SetSelection(0)
		raiz.Add(opciones, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
		_e, self.campo_filtro = crear_campo(panel, _("&Filtrar:"), wx.TextCtrl, sizer=raiz,
			ayuda=_("Escribe para dejar solo las sesiones cuyo título, carpeta, agente, origen o grupo contengan esas palabras."))

		self.etiqueta_lista = wx.StaticText(panel, label=_("&Sesiones:"))
		raiz.Add(self.etiqueta_lista, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.lista = ListaAccesible(panel, _("Sesiones"), COLUMNAS,
			leer_preferencias=getattr(contexto, "preferencias_lectura", None))
		self.lista.SetHelpText(_(
			"Una fila por sesión con su tamaño, sus mensajes y sus datos; izquierda y derecha recorren las columnas. "
			"Intro abre la sesión; Alt+Intro muestra todos sus datos; Supr la oculta o la vuelve a mostrar; "
			"Control+D la da por terminada o la vuelve a activar. Tecla Aplicaciones: todas las acciones."
		))
		self.lista.SetMinSize(self.FromDIP(wx.Size(760, 280)))
		raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_abrir = self._boton(panel, botones, _("&Abrir la sesión"), _("Cierra el informe y abre la sesión elegida (Intro)."), self.abrir)
		self.boton_datos = self._boton(panel, botones, _("&Datos de la sesión"), _("Todos los datos de la sesión elegida, incluido dónde está su historial (Alt+Intro)."), self.datos)
		self.boton_documento = self._boton(panel, botones, _("&Ver como documento"), _("Abre el informe completo en el visor, con un encabezado por sesión (tecla H para saltar)."), self.ver_documento)
		self.boton_guardar = self._boton(panel, botones, _("&Guardar..."), _("Guarda el informe en Markdown o CSV (para una hoja de cálculo). No lleva el texto de los mensajes."), self.guardar)
		self.boton_copiar = self._boton(panel, botones, _("Cop&iar"), _("Copia el informe completo al portapapeles."), self.copiar)
		raiz.Add(botones, 0, wx.LEFT | wx.RIGHT, 4)
		self.boton_cerrar = wx.Button(panel, wx.ID_CANCEL, _("&Cerrar"))
		self.boton_cerrar.SetHelpText(_("Cierra el informe."))
		raiz.Add(self.boton_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		panel.SetSizer(raiz)

		self.campo_mostrar.Bind(wx.EVT_CHOICE, lambda _e: self.recargar())
		self.campo_orden.Bind(wx.EVT_CHOICE, lambda _e: self.recargar())
		self.campo_filtro.Bind(wx.EVT_TEXT, lambda _e: self.recargar())
		self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self.abrir())
		self.lista.Bind(wx.EVT_KEY_DOWN, self._al_tecla_lista)
		vincular_menu_contextual(self.lista, self._crear_menu)
		self.recargar()
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizer(marco)
		self.Fit()
		self.CentreOnParent()
		preparar_dialogo(self, foco_inicial=self.lista if self.visibles else self.campo_totales)

	@staticmethod
	def _boton(panel: wx.Window, sizer: wx.Sizer, etiqueta: str, ayuda: str, accion: Callable[[], object]) -> wx.Button:
		boton = wx.Button(panel, label=etiqueta)
		boton.SetHelpText(ayuda)
		boton.Bind(wx.EVT_BUTTON, lambda _evento: accion())
		sizer.Add(boton, 0, wx.ALL, 4)
		return boton

	# ── Lista ─────────────────────────────────────────────────────────────────

	def filtro(self) -> str:
		indice = self.campo_mostrar.GetSelection()
		return self._filtros[indice][0] if 0 <= indice < len(self._filtros) else "todas"

	def orden(self) -> str:
		indice = self.campo_orden.GetSelection()
		return ORDENES[indice][0] if 0 <= indice < len(ORDENES) else "tamano"

	def recargar(self, enfocar_clave: str = "") -> None:
		actual = self.actual()
		clave = enfocar_clave or (actual.clave if actual else "")
		texto = self.campo_filtro.GetValue().strip()
		self.visibles = ordenar([f for f in self.todas if pasa_filtro(f, self.filtro(), texto)], self.orden())
		self.lista.reemplazar_filas([valores_fila(f) for f in self.visibles], conservar_foco=False)
		self.etiqueta_lista.SetLabel(_("&Sesiones ({n}):").format(n=len(self.visibles)))
		self.lista.SetName(_("Sesiones ({n})").format(n=len(self.visibles)))
		if self.visibles:
			indice = next((i for i, f in enumerate(self.visibles) if f.clave == clave), 0)
			self.lista.enfocar_fila(indice, anunciar=False)
		for boton in (self.boton_abrir, self.boton_datos):
			boton.Enable(bool(self.visibles))

	def actual(self) -> FilaInforme | None:
		fila = self.lista.fila_actual() if hasattr(self, "lista") else -1
		return self.visibles[fila] if 0 <= fila < len(self.visibles) else None

	def _al_tecla_lista(self, evento: wx.KeyEvent) -> None:
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		if tecla == wx.WXK_RETURN and mods == wx.MOD_ALT:
			self.datos()
			return
		if tecla == wx.WXK_DELETE and mods == wx.MOD_NONE:
			self.alternar_oculta()
			return
		if tecla == ord("D") and mods == wx.MOD_CONTROL:
			self.alternar_terminada()
			return
		evento.Skip()

	def _crear_menu(self) -> wx.Menu | None:
		fila = self.actual()
		if fila is None:
			return None
		menu = wx.Menu()

		def anadir(etiqueta: str, accion: Callable[[], object]) -> None:
			menu.Bind(wx.EVT_MENU, lambda _e: accion(), menu.Append(wx.ID_ANY, etiqueta))

		anadir(_("&Abrir la sesión\tIntro"), self.abrir)
		anadir(_("&Datos de la sesión\tAlt+Intro"), self.datos)
		if self.contexto is not None:
			# Una sola etiqueta según el estado; el atajo se escribe una vez.
			ocultar = _("&Volver a mostrar") if fila.oculta else _("&Ocultar")
			anadir(ocultar + "\tSupr", self.alternar_oculta)
			terminar = _("Volver a ac&tivar") if fila.terminada else _("Dar por &terminada")
			anadir(terminar + "\tCtrl+D", self.alternar_terminada)
		return menu

	def _sin_seleccion(self) -> None:
		voz.hablar(_("No hay ninguna sesión elegida") if self.visibles else _("No hay sesiones en la lista"))

	# ── Acciones ──────────────────────────────────────────────────────────────

	def abrir(self) -> bool:
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		self.accion = ("abrir", fila.clave)
		if self.IsModal():
			self.EndModal(wx.ID_OK)
		else:
			self.SetReturnCode(wx.ID_OK)
			self.Hide()
		return True

	def datos(self) -> bool:
		fila = self.actual()
		if fila is None:
			self._sin_seleccion()
			return False
		from app.ui.visor_ayuda import mostrar_documento

		texto = "\n\n".join(f"{etiqueta}: {valor}" for etiqueta, valor in informe.resumen_fila(fila))
		mostrar_documento(self, _("Datos de «{t}»").format(t=fila.titulo), texto, forzar_texto=True)
		return True

	def alternar_oculta(self) -> bool:
		fila = self.actual()
		gestor = getattr(self.contexto, "gestor", None)
		if fila is None or gestor is None:
			self._sin_seleccion()
			return False
		fila.oculta = not fila.oculta
		gestor.ocultar(fila.clave, fila.oculta)
		self.cambiado = True
		self.recargar(fila.clave)
		voz.hablar(_("Sesión oculta") if fila.oculta else _("La sesión vuelve a estar en la lista"))
		return True

	def alternar_terminada(self) -> bool:
		fila = self.actual()
		gestor = getattr(self.contexto, "gestor", None)
		if fila is None or gestor is None:
			self._sin_seleccion()
			return False
		fila.terminada = not fila.terminada
		gestor.marcar_terminada(fila.clave, fila.terminada)
		self.cambiado = True
		self.recargar(fila.clave)
		voz.hablar(_("Dada por terminada") if fila.terminada else _("Vuelve a estar activa"))
		return True

	def ver_documento(self) -> None:
		from app.ui.visor_ayuda import mostrar_documento

		mostrar_documento(self, _("Informe de sesiones (documento)"), informe.a_markdown(self.todas))

	def guardar(self) -> Path | None:
		comodines = _("Markdown (*.md)|*.md|CSV para hoja de cálculo (*.csv)|*.csv")
		with wx.FileDialog(self, _("Guardar el informe de sesiones"), defaultFile=_("Informe de sesiones") + ".md",
				wildcard=comodines, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialogo:
			if dialogo.ShowModal() != wx.ID_OK:
				return None
			ruta = Path(dialogo.GetPath())
			if ruta.suffix.lower() not in (".md", ".csv"):
				ruta = ruta.with_suffix((".md", ".csv")[dialogo.GetFilterIndex()])
		try:
			ruta = informe.guardar(self.todas, ruta)
		except OSError as error:
			log.warning("No se pudo guardar el informe en %s", ruta, exc_info=True)
			mostrar_error(self, _("No se pudo guardar el informe: {e}").format(e=error), _("Guardar el informe"))
			return None
		voz.hablar(_("Informe guardado en {r}").format(r=ruta))
		return ruta

	def copiar(self) -> bool:
		from app.ui.accesibilidad.teclado import copiar_texto

		if not copiar_texto(informe.a_markdown(self.todas)):
			voz.hablar(_("No se pudo usar el portapapeles."))
			return False
		voz.hablar(_("Informe copiado."))
		return True
