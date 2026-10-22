"""Pruebas de los grupos que se abren, la lectura configurable de las listas y los registros.

Los diálogos se crean SIN mostrarse: nunca ``Show``, ``ShowModal`` real,
``MainLoop`` ni ``SetFocus`` (el usuario trabaja con un lector de pantalla en
este equipo). Los cuadros que abrirían las acciones se sustituyen.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from app.core.base_datos import BaseDatos  # noqa: E402
from app.core.migraciones import MIGRACIONES  # noqa: E402
from app.services import exportar as modulo_exportar  # noqa: E402
from app.services.almacen import Almacen  # noqa: E402
from app.services.gestor import FilaSesion, MensajeVista  # noqa: E402
from app.ui import lectura_listas  # noqa: E402
from app.ui.accesibilidad import voz  # noqa: E402
from app.ui.dialogos import grupo as modulo_grupo  # noqa: E402
from app.ui.dialogos import grupos as modulo_grupos  # noqa: E402
from tests.ayudas_wx import procesar_eventos, simular_tecla  # noqa: E402

AHORA = time.time()
INTERACTIVOS = (wx.Button, wx.TextCtrl, wx.ListCtrl, wx.ListBox, wx.Choice, wx.CheckBox)
INGLES = {"ok", "cancel", "close", "yes", "no", "apply", "help"}


@pytest.fixture(autouse=True)
def anuncios(monkeypatch):
	"""Silencia la voz y guarda lo que se habría dicho."""
	dichos: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *args, **kwargs: dichos.append(texto))
	return dichos


@pytest.fixture
def almacen(tmp_path, data_temporal):
	datos = Almacen(tmp_path / "pruebas.db")
	yield datos
	datos.cerrar()


def _opcion_desde(valores: dict):
	return lambda clave, defecto=None: valores.get(clave, defecto)


class Contexto:
	"""Lo que un grupo abierto usa de la ventana principal."""

	def __init__(self, filas: list[FilaSesion], valores: dict | None = None) -> None:
		self.filas = filas
		self.valores = dict(valores or {})
		self.renombradas: list[tuple[str, str]] = []
		self.exportados: list[int] = []

	def opcion(self, clave, defecto=None):
		return self.valores.get(clave, defecto)

	def preferencias_lectura(self):
		return lectura_listas.preferencias_lectura(self.opcion)

	def filas_sesiones(self):
		return list(self.filas)

	def renombrar_sesion(self, clave, titulo):
		self.renombradas.append((clave, titulo))

	def exportar_grupo(self, id_grupo, padre=None):
		self.exportados.append(id_grupo)


def _filas() -> list[FilaSesion]:
	return [
		FilaSesion("ctrlia:a", "claude", "Mercado europeo", actividad=AHORA - 50, estado="esperando"),
		FilaSesion("claude:b", "claude", "Competidores", actividad=AHORA - 10, origen="terminal"),
		FilaSesion("codex:c", "codex", "Precios", actividad=AHORA - 30),
		FilaSesion("ctrlia:d", "gemini", "Sin relación", actividad=AHORA - 5),
	]


def _descendientes(ventana: wx.Window):
	for hijo in ventana.GetChildren():
		yield hijo
		yield from _descendientes(hijo)


def _comprobar_accesibilidad(dialogo: wx.Dialog) -> None:
	"""Ayuda en todo control interactivo, etiquetas delante y nada en inglés."""
	assert dialogo.GetEscapeId() == wx.ID_CANCEL
	for control in _descendientes(dialogo):
		if not isinstance(control, INTERACTIVOS):
			continue
		assert control.GetHelpText().strip(), "sin ayuda (Ctrl+H): {0!r} {1}".format(control, control.GetLabel())
		if isinstance(control, wx.Button):
			etiqueta = control.GetLabel()
			assert "&" in etiqueta, "botón sin acelerador: {0}".format(etiqueta)
			assert etiqueta.replace("&", "").strip(" .…").lower() not in INGLES, etiqueta
		if isinstance(control, (wx.ListCtrl, wx.ListBox, wx.TextCtrl)):
			hermanos = list(control.GetParent().GetChildren())
			anterior = hermanos[hermanos.index(control) - 1]
			assert isinstance(anterior, wx.StaticText), "la etiqueta debe crearse justo antes: {0}".format(control.GetName())
			assert "&" in anterior.GetLabel()


def _cerrar(dialogo: wx.Dialog) -> None:
	dialogo.Destroy()
	procesar_eventos()


# ── Almacén ─────────────────────────────────────────────────────────────────────


def test_migracion_conserva_los_grupos_y_su_orden(tmp_path, data_temporal):
	ruta = tmp_path / "v1.db"
	bd = BaseDatos(ruta, MIGRACIONES[:1], carpeta_copias=tmp_path / "copias")
	grupo = bd.insertar("INSERT INTO grupos (nombre, posicion) VALUES ('Investigación', 1)")
	for clave in ("x", "y", "z"):
		bd.ejecutar("INSERT INTO sesion_grupo (clave, grupo) VALUES (?, ?)", (clave, grupo))
	bd.cerrar()
	almacen = Almacen(ruta)
	try:
		assert sqlite3.connect(ruta).execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES)
		assert almacen.sesiones_de_grupo(grupo) == ["x", "y", "z"]
		detalle = almacen.grupos_detalle()[0]
		assert (detalle.nombre, detalle.sesiones, detalle.descripcion) == ("Investigación", 3, "")
	finally:
		almacen.cerrar()


def test_orden_mover_y_datos_de_los_grupos(almacen):
	investigacion = almacen.crear_grupo("Investigación", descripcion="Mercado", agente="codex")
	trabajo = almacen.crear_grupo("Trabajo")
	for clave in ("a", "b", "c"):
		almacen.anadir_a_grupo(clave, investigacion)
	almacen.anadir_a_grupo("a", investigacion)  # repetida: no cambia nada
	assert almacen.sesiones_de_grupo(investigacion) == ["a", "b", "c"]
	assert almacen.mover_en_grupo("c", investigacion, -1) == 1
	assert almacen.sesiones_de_grupo(investigacion) == ["a", "c", "b"]
	assert almacen.mover_en_grupo("a", investigacion, -1) == 0  # ya era la primera
	almacen.ordenar_grupo(investigacion, ["b", "a"])
	assert almacen.sesiones_de_grupo(investigacion) == ["b", "a", "c"]
	almacen.mover_a_grupo("b", investigacion, trabajo)
	assert almacen.sesiones_de_grupo(investigacion) == ["a", "c"]
	assert almacen.sesiones_de_grupo(trabajo) == ["b"]
	assert almacen.mover_grupo(trabajo, -1) == 0
	assert [n for _i, n in almacen.grupos()] == ["Trabajo", "Investigación"]
	almacen.actualizar_grupo(investigacion, descripcion="Nuevo texto", carpeta="C:\\x", agente="claude", otro="no")
	datos = almacen.grupo(investigacion)
	assert (datos.descripcion, datos.carpeta, datos.agente, datos.sesiones) == ("Nuevo texto", "C:\\x", "claude", 2)
	almacen.borrar_grupo(trabajo)
	assert almacen.grupo(trabajo) is None
	assert almacen.sesiones_de_grupo(trabajo) == []


# ── Lectura de las listas ──────────────────────────────────────────────────────


def test_indicadores_de_los_mensajes():
	todo = _opcion_desde({})
	texto = "Mira https://a.es y https://b.es (otra vez https://a.es).\n```py\nx = 1\n```\n¿Lo publico?"
	# Un «?» al final no es una pregunta: solo cuentan las peticiones reales (ver test_aichat_listas).
	assert lectura_listas.indicadores_mensaje(texto, "asistente", 2, todo) == (
		"1 bloque de código, 2 enlaces, 2 archivos cambiados"
	)
	assert lectura_listas.indicadores_mensaje("¿Seguro?", "usuario", 0, todo) == ""  # solo el agente pregunta
	assert lectura_listas.indicadores_mensaje("x" * 2000, "asistente", 0, todo) == "Largo"
	sin_enlaces = _opcion_desde({"lectura.indicador_enlaces": False})
	assert "enlace" not in lectura_listas.indicadores_mensaje(texto, "asistente", 0, sin_enlaces)
	nada = _opcion_desde({"lectura.indicadores": False})
	assert lectura_listas.indicadores_mensaje(texto, "asistente", 3, nada) == ""


def test_columnas_fechas_y_preferencias_de_lectura():
	disponibles = lectura_listas.COLUMNAS_MENSAJES
	assert lectura_listas.columnas_validas(["hora", "x", "hora", "quien"], disponibles, ["mensaje"]) == ["hora", "quien"]
	assert lectura_listas.columnas_validas([], disponibles, ["mensaje"]) == ["mensaje"]
	assert lectura_listas.columnas_validas("roto", disponibles, ["mensaje"]) == ["mensaje"]
	completas = _opcion_desde({"lectura.fechas": "absolutas"})
	fecha = time.mktime((2026, 10, 17, 21, 39, 0, 0, 0, -1))
	assert lectura_listas.escribir_fecha(fecha, completas) == "17/10/2026 21:39"
	assert lectura_listas.escribir_hora(0, completas) == ""
	preferencias = lectura_listas.preferencias_lectura(_opcion_desde({"lectura.numero_fila": False}))
	assert (preferencias.numero_fila, preferencias.cabecera, preferencias.contenido) == (False, True, True)
	nombres = {"titulo": "Título", "estado": "Estado"}
	assert lectura_listas.ejemplo_lectura(nombres, {"titulo": "Informe", "estado": "Inactiva"}, ["estado", "titulo"]) == (
		"Estado: Inactiva; Título: Informe"
	)
	assert lectura_listas.ejemplo_lectura(nombres, {}, []) == "No hay ninguna columna marcada."


def test_valores_de_sesion_segun_preferencias():
	from app.ui.vp_sesiones import valores_sesion

	fila = FilaSesion("ctrlia:a", "claude", "Informe", favorita=True, no_leidas=1, estado="esperando")
	columnas = ["titulo", "estado"]
	con_todo = valores_sesion(fila, columnas, _opcion_desde({}))
	assert con_todo[0] == "Favorita: Informe" and con_todo[1].endswith(", respuesta nueva")
	sin_extras = valores_sesion(fila, columnas, _opcion_desde({
		"lectura.favorita_en_titulo": False, "lectura.sin_leer_en_estado": False}))
	assert sin_extras[0] == "Informe" and "respuesta nueva" not in sin_extras[1]


# ── Lista de grupos ────────────────────────────────────────────────────────────


def test_lista_de_grupos_abre_ordena_y_edita(app_wx, almacen, monkeypatch, anuncios):
	investigacion = almacen.crear_grupo("Investigación", descripcion="Consultas del mercado")
	trabajo = almacen.crear_grupo("Trabajo")
	almacen.anadir_a_grupo("ctrlia:a", investigacion)
	almacen.anadir_a_grupo("claude:b", investigacion)
	contexto = Contexto(_filas())
	dialogo = modulo_grupos.DialogoGrupos(None, almacen, contexto=contexto)
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.lista.valores_fila(0) == ["Investigación", "2 sesiones", "Consultas del mercado"]
		assert dialogo.lista.valores_fila(1) == ["Trabajo", "Vacío", "Sin descripción"]
		# Intro abre el grupo; si el grupo pide abrir una sesión, la lista de grupos se cierra con esa petición.
		abiertos: list[int] = []
		monkeypatch.setattr(modulo_grupo, "abrir_grupo",
			lambda padre, al, identificador, ctx: abiertos.append(identificador) or ("abrir", "claude:b"))
		assert dialogo.abrir() is True
		assert abiertos == [investigacion] and dialogo.accion == ("abrir", "claude:b")
	finally:
		_cerrar(dialogo)

	dialogo = modulo_grupos.DialogoGrupos(None, almacen, contexto=contexto)
	try:
		dialogo.lista.enfocar_fila(1, anunciar=False)
		assert dialogo.mover(-1) is True
		assert [n for _i, n in almacen.grupos()] == ["Trabajo", "Investigación"]
		assert anuncios[-1] == "Movido a la posición 1 de 2"
		assert dialogo.actual() == (trabajo, "Trabajo")
		assert dialogo.mover(-1) is False and anuncios[-1] == "Ya es el primero"
		# Alt+Intro edita los datos; F2 renombra y se anota para corregir la vista de la lista principal.
		monkeypatch.setattr(modulo_grupos, "editar_grupo",
			lambda padre, al, grupo: al.actualizar_grupo(grupo.id, nombre="Oficina", descripcion="Del día a día") or grupo.id)
		assert simular_tecla(dialogo, wx.WXK_RETURN, alt=True).gestionado
		assert almacen.grupo(trabajo).descripcion == "Del día a día"
		assert dialogo.renombrados == {"Trabajo": "Oficina"}
		monkeypatch.setattr(modulo_grupos, "pedir_texto", lambda *args, **kwargs: "Despacho")
		assert dialogo.renombrar() is True
		assert dialogo.renombrados == {"Trabajo": "Despacho"}
		# Control+E exporta el grupo entero y «Mostrar en la lista principal» cierra con esa petición.
		assert simular_tecla(dialogo, "e", ctrl=True).gestionado
		assert contexto.exportados == [trabajo]
		assert dialogo.mostrar_en_lista() is True and dialogo.accion == ("mostrar", trabajo)
	finally:
		_cerrar(dialogo)


def test_editar_grupo_valida_y_devuelve_los_valores(app_wx, almacen, tmp_path):
	almacen.crear_grupo("Trabajo")
	dialogo = modulo_grupos.DialogoEditarGrupo(None, almacen, None)
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.validar() == "Escribe un nombre para el grupo."
		dialogo.campo_nombre.SetValue(" trabajo ")
		assert "Ya hay un grupo" in dialogo.validar()
		dialogo.campo_nombre.SetValue("  Nueva   investigación ")
		dialogo.campo_carpeta.SetValue(str(tmp_path / "no_existe"))
		assert "no existe" in dialogo.validar()
		dialogo.campo_carpeta.SetValue(str(tmp_path))
		dialogo.campo_descripcion.SetValue("  De qué trata  ")
		dialogo.campo_agente.SetSelection(modulo_grupos.AGENTES_GRUPO.index("codex"))
		assert dialogo.validar() is None
		assert dialogo.aceptar() is True
		assert dialogo.valores == {"nombre": "Nueva investigación", "descripcion": "De qué trata", "agente": "codex",
			"carpeta": str(tmp_path)}
	finally:
		_cerrar(dialogo)


# ── Un grupo abierto ────────────────────────────────────────────────────────────


@pytest.fixture
def grupo_abierto(app_wx, almacen):
	identificador = almacen.crear_grupo("Investigación", descripcion="Todo sobre el mercado europeo")
	for clave in ("ctrlia:a", "claude:b", "borrada:z"):
		almacen.anadir_a_grupo(clave, identificador)
	contexto = Contexto(_filas())
	dialogo = modulo_grupo.DialogoGrupo(None, almacen, identificador, contexto)
	yield dialogo, identificador, contexto
	_cerrar(dialogo)


def test_grupo_abierto_muestra_sus_sesiones_en_orden(grupo_abierto):
	dialogo, _identificador, _contexto = grupo_abierto
	_comprobar_accesibilidad(dialogo)
	assert dialogo.GetTitle() == "Grupo «Investigación»"
	assert dialogo.campo_descripcion.GetValue() == "Todo sobre el mercado europeo"
	assert [f.clave for f in dialogo._filas] == ["ctrlia:a", "claude:b", "borrada:z"]
	assert dialogo.lista.texto_celda(0, 0) == "Mercado europeo"
	assert dialogo.lista.texto_celda(2, 0) == "Sesión no disponible (borrada:z)"
	assert dialogo.lista.GetName() == "Sesiones del grupo «Investigación» (3)"


def test_grupo_abierto_abre_crea_y_no_abre_las_no_disponibles(grupo_abierto, monkeypatch):
	dialogo, identificador, _contexto = grupo_abierto
	errores: list[str] = []
	monkeypatch.setattr(modulo_grupo, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
	dialogo.lista.enfocar_fila(2, anunciar=False)
	assert dialogo.abrir() is False and "ya no está disponible" in errores[0]
	dialogo.lista.enfocar_fila(1, anunciar=False)
	assert dialogo.abrir() is True and dialogo.accion == ("abrir", "claude:b")
	assert simular_tecla(dialogo, "n", ctrl=True).gestionado
	assert dialogo.accion == ("nueva", identificador)


def test_grupo_abierto_ordena_quita_y_mueve(grupo_abierto, almacen, monkeypatch, anuncios):
	dialogo, identificador, _contexto = grupo_abierto
	dialogo.lista.enfocar_fila(1, anunciar=False)
	assert dialogo.mover(-1) is True
	assert almacen.sesiones_de_grupo(identificador) == ["claude:b", "ctrlia:a", "borrada:z"]
	assert anuncios[-1] == "Movida a la posición 1 de 3"
	assert dialogo.actual().clave == "claude:b"
	# Supr la saca del grupo sin borrarla.
	dialogo.lista.enfocar_fila(2, anunciar=False)
	assert dialogo.quitar() is True
	assert almacen.sesiones_de_grupo(identificador) == ["claude:b", "ctrlia:a"]
	# Control+M la mueve a otro grupo en un solo paso (el de origen no se ofrece).
	otro = almacen.crear_grupo("Archivo")
	ofrecidos: list[tuple[int, ...]] = []
	monkeypatch.setattr(modulo_grupos, "elegir_grupo",
		lambda padre, al, titulo, excluir=(): ofrecidos.append(excluir) or otro)
	dialogo.lista.enfocar_fila(0, anunciar=False)
	assert dialogo.mover_a_otro() is True
	assert ofrecidos == [(identificador,)]
	assert almacen.sesiones_de_grupo(identificador) == ["ctrlia:a"]
	assert almacen.sesiones_de_grupo(otro) == ["claude:b"]
	assert anuncios[-1] == "Movida al grupo Archivo."
	# Control+G la añade también a otro sin sacarla de este.
	assert dialogo.anadir_a_otro() is True
	assert almacen.sesiones_de_grupo(identificador) == ["ctrlia:a"]
	assert almacen.sesiones_de_grupo(otro) == ["claude:b", "ctrlia:a"]


def test_grupo_abierto_anade_varias_filtra_renombra_y_ordena(grupo_abierto, almacen, monkeypatch, anuncios):
	dialogo, identificador, contexto = grupo_abierto
	ofrecidas: list[list[str]] = []

	def elegir(padre, titulo, filas, ayuda):
		ofrecidas.append([f.clave for f in filas])
		return ["codex:c", "ctrlia:d"]

	monkeypatch.setattr(modulo_grupo, "elegir_sesiones", elegir)
	assert simular_tecla(dialogo.lista, wx.WXK_INSERT).gestionado
	assert ofrecidas == [["codex:c", "ctrlia:d"]], "solo se ofrecen las que no están ya en el grupo"
	assert almacen.sesiones_de_grupo(identificador)[-2:] == ["codex:c", "ctrlia:d"]
	assert anuncios[-1] == "2 sesiones añadidas al grupo."
	# Filtro: solo las que coinciden; mientras hay filtro no se cambia el orden.
	monkeypatch.setattr(modulo_grupo, "pedir_texto", lambda *args, **kwargs: "precios")
	dialogo.filtrar()
	assert [f.clave for f in dialogo._filas] == ["codex:c"]
	assert dialogo.mover(-1) is False and "Quita el filtro" in anuncios[-1]
	assert simular_tecla(dialogo.lista, wx.WXK_ESCAPE).gestionado
	assert len(dialogo._filas) == 5
	# F2 renombra a través de la ventana principal.
	monkeypatch.setattr(modulo_grupo, "pedir_texto", lambda *args, **kwargs: "  Precios 2026 ")
	dialogo.lista.enfocar_fila(3, anunciar=False)
	assert dialogo.renombrar() is True
	assert contexto.renombradas == [("codex:c", "Precios 2026")]
	# «Ordenar el grupo por título» reescribe el orden una vez.
	dialogo.ordenar_por("titulo")
	titulos = [f.titulo for f in dialogo._filas]
	assert titulos == sorted(titulos, key=str.casefold)


def test_grupo_vacio_lo_dice(app_wx, almacen, anuncios):
	identificador = almacen.crear_grupo("Vacío")
	dialogo = modulo_grupo.DialogoGrupo(None, almacen, identificador, Contexto(_filas()))
	try:
		procesar_eventos()
		assert any("Este grupo está vacío" in texto for texto in anuncios)
		assert dialogo.quitar() is False
	finally:
		_cerrar(dialogo)


def test_elegir_sesiones_conserva_lo_marcado_al_filtrar(app_wx, monkeypatch):
	errores: list[str] = []
	monkeypatch.setattr(modulo_grupo, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
	dialogo = modulo_grupo.DialogoElegirSesiones(None, "Añadir sesiones", _filas(), "Marca las sesiones.")
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.aceptar() is False and errores
		dialogo.lista.Check(0, True)  # Mercado europeo
		dialogo.filtrar("precios")
		assert dialogo.lista.GetCount() == 1
		dialogo.lista.Check(0, True)  # Precios
		dialogo.filtrar("")
		assert dialogo.lista.GetCount() == 4
		assert dialogo.aceptar() is True
		assert dialogo.elegidas == ["ctrlia:a", "codex:c"]
	finally:
		_cerrar(dialogo)


# ── Exportar un grupo ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("extension", [".md", ".html", ".txt"])
def test_exportar_grupo_en_un_archivo(tmp_path, extension):
	conversaciones = [
		modulo_exportar.Conversacion("Mercado", [MensajeVista("usuario", "¿Cómo va?", AHORA),
			MensajeVista("asistente", "# Resumen\nBien.", AHORA)], {"Agente": "claude"}),
		modulo_exportar.Conversacion("Precios", [MensajeVista("asistente", "Suben <b>un poco</b>.", AHORA)]),
	]
	ruta = modulo_exportar.exportar_grupo(conversaciones, tmp_path / f"grupo{extension}", "Investigación", "Notas del grupo")
	texto = ruta.read_text(encoding="utf-8")
	for esperado in ("Investigación", "Notas del grupo", "Mercado", "Precios", "¿Cómo va?"):
		assert esperado in texto
	assert texto.index("Mercado") < texto.index("Precios")
	if extension == ".md":
		assert "## Índice" in texto and "## Mercado" in texto and "#### " in texto
	if extension == ".html":
		assert '<html lang="es">' in texto and "<h1>Investigación</h1>" in texto
		assert "<b>" not in texto, "el HTML de los mensajes se escapa"
		assert "<h4>Resumen</h4>" in texto, "los encabezados del mensaje quedan por debajo del suyo"


# ── Preferencias: columnas ─────────────────────────────────────────────────────


def test_preferencias_columnas_con_vista_previa(app_wx, tmp_path, monkeypatch):
	from app.core.configuracion import Configuracion
	from app.ui import dialogo_preferencias as mod
	from app.ui.dialogos import columnas as modulo_columnas

	config = Configuracion(tmp_path / "settings.json")
	dialogo = mod.DialogoPreferencias(None, config)
	try:
		titulos = [dialogo.libro.GetPageText(i) for i in range(dialogo.libro.GetPageCount())]
		assert "Lectura de listas" in titulos
		boton = dialogo.control_de("mensajes.columnas")
		assert isinstance(boton, wx.Button)
		assert boton.GetLabel().startswith("Lo que se lee al subir y bajar por la lista de mensajes: Quién, Mensaje, Hora")
		vistos: dict[str, str] = {}

		def mostrar(cuadro):
			vistos["titulo"] = cuadro.GetTitle()
			vistos["ejemplo"] = cuadro.campo_ejemplo.GetValue()
			cuadro.activas = ["hora", "mensaje"]
			return wx.ID_OK

		monkeypatch.setattr(modulo_columnas.DialogoColumnas, "ShowModal", mostrar)
		monkeypatch.setattr(wx.Window, "SetFocus", lambda self: None)
		dialogo._elegir_columnas(dialogo._controles["mensajes.columnas"][0], boton)
		assert vistos["titulo"] == "Lo que se lee al subir y bajar por la lista de mensajes"
		assert vistos["ejemplo"].startswith("Quién: Claude Code; Mensaje: ¿Quieres que lo publique?")
		assert dialogo.valores()["mensajes.columnas"] == ["hora", "mensaje"]
		assert "Hora, Mensaje" in boton.GetLabel()
		assert dialogo.aplicar() is True
		assert config.obtener("mensajes.columnas") == ["hora", "mensaje"]
	finally:
		dialogo.Destroy()
		procesar_eventos()


# ── Ventana principal ──────────────────────────────────────────────────────────


@pytest.fixture
def ventana(app_wx, tmp_path, monkeypatch, data_temporal):
	from app.core.configuracion import Configuracion
	from app.services.agentes.claude import ProveedorClaude
	from app.ui import avisos
	from app.ui.ventana_principal import VentanaPrincipal

	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	almacen = Almacen(tmp_path / "ventana.db")
	fake = Path(__file__).parent / "fakes" / "fake_claude.py"
	marco = VentanaPrincipal(config, None, almacen=almacen, proveedores={"claude": ProveedorClaude(orden_base=[sys.executable, str(fake)])})
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()
		procesar_eventos()


def test_ventana_lee_las_preferencias_y_las_columnas_de_mensajes(ventana):
	assert ventana.preferencias_lectura().numero_fila is True
	ventana.poner_opcion("lectura.numero_fila", False)
	assert ventana.preferencias_lectura().numero_fila is False
	assert ventana.lista_mensajes.GetColumnCount() == 4
	# La pregunta sale de la petición real del agente guardada con el mensaje, no del «?».
	ventana._mensajes = [MensajeVista("asistente", "Mira https://ejemplo.es ¿Seguimos?", AHORA,
		datos={"solicitudes": [{"id": "1", "tipo": "pregunta", "estado": "pendiente"}]})]
	ventana.poner_opcion("mensajes.columnas", ["numero", "indicadores", "mensaje"])
	procesar_eventos()
	assert ventana.lista_mensajes.GetColumnCount() == 3
	assert ventana.lista_mensajes.valores_fila(0) == ["1", "Pregunta por responder, 1 enlace", "Mira https://ejemplo.es ¿Seguimos?"]
	ventana.poner_opcion("lectura.indicador_enlaces", False)
	procesar_eventos()
	assert ventana.lista_mensajes.texto_celda(0, 1) == "Pregunta por responder"


def test_ventana_corrige_la_vista_de_un_grupo_renombrado_o_borrado(ventana, monkeypatch):
	identificador = ventana.almacen.crear_grupo("Investigación")
	ventana.poner_opcion("lista.vista", "grupo:Investigación")
	ventana.almacen.renombrar_grupo(identificador, "Mercado")
	ventana._corregir_vista_de_grupo({"Investigación": "Mercado"})
	assert ventana.opcion("lista.vista") == "grupo:Mercado"
	ventana.almacen.borrar_grupo(identificador)
	ventana._corregir_vista_de_grupo({})
	assert ventana.opcion("lista.vista") == "todas"


def test_ventana_hace_lo_que_pide_un_grupo(ventana, monkeypatch, tmp_path):
	from app.ui import avisos

	identificador = ventana.almacen.crear_grupo("Investigación", carpeta=str(tmp_path), agente="claude")
	nuevas: list[int | None] = []
	monkeypatch.setattr(type(ventana), "accion_nueva_sesion", lambda self, carpeta=None, mensaje="", grupo=None: nuevas.append(grupo))
	ventana._hacer_accion_de_grupo(("nueva", identificador))
	assert nuevas == [identificador]
	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	ventana._hacer_accion_de_grupo(("mostrar", identificador))
	assert ventana.opcion("lista.vista") == "grupo:Investigación"
	assert dichos[-1] == "No hay sesiones en esta vista. Elige otra en Ver, Mostrar."


def test_nueva_sesion_en_un_grupo_queda_dentro(ventana, monkeypatch, tmp_path):
	from app.ui.dialogos import nueva_sesion

	identificador = ventana.almacen.crear_grupo("Investigación", carpeta=str(tmp_path), agente="claude")
	vistos = {}

	def mostrar(dialogo):
		vistos["grupo"] = dialogo.grupo.GetStringSelection()
		vistos["carpeta"] = dialogo.carpeta.GetValue()
		dialogo._aceptar()
		return wx.ID_OK

	monkeypatch.setattr(nueva_sesion.DialogoNuevaSesion, "ShowModal", mostrar)
	monkeypatch.setattr(nueva_sesion.DialogoNuevaSesion, "EndModal", lambda self, codigo: None)
	monkeypatch.setattr(type(ventana), "abrir_sesion", lambda self, clave, mover_foco=True: None)
	monkeypatch.setattr(type(ventana), "ir_a_respuesta", lambda self: None)
	assert ventana.accion_nueva_sesion(grupo=identificador) is True
	assert vistos == {"grupo": "Investigación", "carpeta": str(tmp_path)}
	assert len(ventana.almacen.sesiones_de_grupo(identificador)) == 1


# ── Registros ──────────────────────────────────────────────────────────────────


def test_volcados_nativos_no_mortales_se_marcan_al_cerrar_bien(tmp_path, monkeypatch, caplog):
	from app.core import registro

	ruta = tmp_path / "fallo_nativo.log"
	archivo = open(ruta, "a+", encoding="utf-8")  # noqa: SIM115 - lo cierra la prueba
	try:
		archivo.write("\n=== Sesión vieja ===\nWindows fatal exception: code 0xc0000005\n")
		archivo.flush()
		monkeypatch.setitem(registro._estado, "archivo_fallo_nativo", archivo)
		monkeypatch.setitem(registro._estado, "ruta_fallo_nativo", ruta)
		monkeypatch.setitem(registro._estado, "inicio_fallo_nativo", archivo.tell())
		assert registro.revisar_volcados_no_mortales() == []
		archivo.write("Windows fatal exception: code 0x8001010d\n\nThread ...\nWindows fatal exception: code 0x8001010d\n")
		archivo.flush()
		with caplog.at_level(logging.INFO, logger="app.registro"):
			assert registro.revisar_volcados_no_mortales() == ["0x8001010d", "0x8001010d"]
		assert "2 excepciones nativas no mortales (0x8001010D)" in caplog.text
		texto = ruta.read_text(encoding="utf-8")
		assert "Los 2 volcados de arriba (0x8001010D)" in texto and "0xc0000005" in texto
		assert registro.revisar_volcados_no_mortales() == [], "lo ya anotado no se vuelve a contar"
	finally:
		archivo.close()


def test_agente_detenido_por_ctrlia_no_parece_un_fallo(caplog):
	from app.services.agentes.procesos import ProcesoLineas

	proceso = ProcesoLineas([sys.executable, "-c", "import time; time.sleep(30)"], lambda linea: None, nombre="dormilon")
	with caplog.at_level(logging.INFO, logger="app.services.agentes.procesos"):
		proceso.iniciar()
		proceso.terminar()
		assert proceso.esperar_fin(10)
	assert proceso.detenido_por_ctrlia is True
	assert "dormilon detenido por Ctrl+IA" in caplog.text
	assert "dormilon terminó con código" not in caplog.text
