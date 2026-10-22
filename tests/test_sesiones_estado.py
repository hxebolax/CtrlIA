"""Sesiones terminadas, respuestas nuevas de cualquier sesión, informe de sesiones e intercambio entre equipos.

Issues del proyecto original: #160 (terminadas), #237 (respuesta nueva y marcar
como leída), #251 (informe de chats) y #228 (exportar e importar sesiones).
Sin ventanas visibles ni voz; los historiales reales del usuario nunca se leen.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from app.core.base_datos import BaseDatos  # noqa: E402
from app.core.migraciones import MIGRACIONES  # noqa: E402
from app.services import informe, intercambio  # noqa: E402
from app.services.agentes.base import AjustesSesion, Estado  # noqa: E402
from app.services.almacen import LEIDA_NUNCA, Almacen  # noqa: E402
from app.services.gestor import FilaSesion, GestorSesiones  # noqa: E402
from app.ui.accesibilidad import voz  # noqa: E402
from tests.ayudas_wx import procesar_eventos  # noqa: E402
from tests.test_gestor import ProveedorFalso  # noqa: E402

AHORA = time.time()
INTERACTIVOS = (wx.Button, wx.TextCtrl, wx.ListCtrl, wx.ListBox, wx.Choice, wx.CheckBox)
INGLES = {"ok", "cancel", "close", "yes", "no", "apply", "help"}
OPCIONES = {"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False}


@pytest.fixture(autouse=True)
def anuncios(monkeypatch):
	dichos: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *args, **kwargs: dichos.append(texto))
	return dichos


def _gestor(ruta: Path) -> GestorSesiones:
	return GestorSesiones(Almacen(ruta), {"falso": ProveedorFalso()}, lambda c, e: None,
		lambda clave, defecto: OPCIONES.get(clave, defecto))


@pytest.fixture
def gestor(tmp_path, data_temporal):
	datos = _gestor(tmp_path / "a.db")
	yield datos
	datos.almacen.cerrar()


def _con_externas(gestor: GestorSesiones, filas: list[FilaSesion]) -> None:
	"""Sustituye la lectura de historiales reales por filas fijas."""
	gestor._listar_externas = lambda: [FilaSesion(f.clave, f.proveedor, f.titulo, origen=f.origen, estado=f.estado,
		actividad=f.actividad) for f in filas]


def _fila(gestor: GestorSesiones, clave: str) -> FilaSesion:
	return next(f for f in gestor.listar() if f.clave == clave)


# ── Migración ───────────────────────────────────────────────────────────────────


def test_migracion_3_anade_terminada_y_conserva_los_metadatos(tmp_path, data_temporal):
	ruta = tmp_path / "v2.db"
	bd = BaseDatos(ruta, MIGRACIONES[:2], carpeta_copias=tmp_path / "copias")
	bd.ejecutar("INSERT INTO metadatos (clave, titulo, favorita) VALUES ('claude:x', 'Mío', 1)")
	bd.cerrar()
	almacen = Almacen(ruta)
	try:
		assert sqlite3.connect(ruta).execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES) >= 3
		meta = almacen.metadatos()["claude:x"]
		assert (meta.titulo, meta.favorita, meta.terminada) == ("Mío", True, 0.0)
	finally:
		almacen.cerrar()


# ── Terminadas (#160) ───────────────────────────────────────────────────────────


def test_terminada_se_marca_se_desmarca_y_vuelve_sola_con_actividad(gestor, tmp_path):
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=tmp_path, titulo="Vieja"))
	gestor.almacen.actualizar_sesion(clave, no_leidas=2, actividad=AHORA - 100)
	gestor.marcar_terminada(clave)
	fila = _fila(gestor, clave)
	assert fila.terminada and fila.no_leidas == 0
	gestor.marcar_terminada(clave, False)
	assert not _fila(gestor, clave).terminada
	gestor.marcar_terminada(clave)
	gestor.almacen.actualizar_sesion(clave, actividad=time.time() + 60)  # le escribes otra vez
	assert not _fila(gestor, clave).terminada


def test_vistas_de_la_ventana_con_terminadas_e_importadas(ventana):
	ventana._todas = [
		FilaSesion("claude:a", "claude", "Espera", estado="esperando", origen="terminal"),
		FilaSesion("claude:b", "claude", "Vieja", estado="esperando", origen="terminal", terminada=True, no_leidas=1),
		FilaSesion("importada:c", "claude", "De fuera", origen="importada", equipo="PORTATIL"),
	]
	for vista, esperadas in (("te_necesitan", ["claude:a"]), ("actividad", ["claude:a"]), ("sin_leer", []),
			("terminadas", ["claude:b"]), ("importadas", ["importada:c"])):
		ventana.poner_opcion("lista.vista", vista)
		ventana.mostrar_sesiones(True)
		assert [f.clave for f in ventana._visibles] == esperadas, vista
	ventana.poner_opcion("lista.vista", "todas")
	ventana.mostrar_sesiones(True)
	estados = {f.clave: ventana.valores_fila(f)[1] for f in ventana._visibles}
	assert estados["claude:b"].startswith("Terminada")
	assert ventana._visibles[[f.clave for f in ventana._visibles].index("importada:c")].nombre_origen == "Importada de PORTATIL"


def test_accion_terminada_y_marcar_leida_desde_la_ventana(ventana, monkeypatch):
	from app.ui import avisos

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	fila = FilaSesion("claude:z", "claude", "Charla", estado="esperando", origen="terminal", no_leidas=1)
	ventana._todas = [fila]
	ventana.poner_opcion("lista.vista", "te_necesitan")
	ventana.mostrar_sesiones(True)
	ventana.lista_sesiones.enfocar_fila(0, anunciar=False)
	ventana.accion_terminada()
	assert fila.terminada and ventana._visibles == []
	assert "Terminadas" in dichos[-1]
	assert ventana.almacen.metadatos()["claude:z"].terminada > 0
	ventana.poner_opcion("lista.vista", "todas")
	ventana.mostrar_sesiones(True)
	ventana.lista_sesiones.enfocar_fila(0, anunciar=False)
	ventana.accion_terminada()
	assert not fila.terminada and dichos[-1] == "Vuelve a estar activa."
	ventana.accion_marcar_leida()  # no tenía respuestas nuevas (terminar las quitó): se marca como no leída
	assert fila.no_leidas == 1 and dichos[-1] == "Marcada como no leída."
	assert ventana.almacen.metadatos()["claude:z"].leida_hasta == LEIDA_NUNCA
	ventana.accion_marcar_leida()
	assert fila.no_leidas == 0 and dichos[-1] == "Marcada como leída."
	fila.no_leidas = 1
	ventana.accion_marcar_todas_leidas()
	assert fila.no_leidas == 0 and dichos[-1] == "1 sesiones marcadas como leídas."


def test_menu_contextual_ofrece_terminar_y_leer(ventana):
	ventana._todas = [FilaSesion("claude:z", "claude", "Charla", origen="terminal", no_leidas=1)]
	ventana.mostrar_sesiones(True)
	ventana.lista_sesiones.enfocar_fila(0, anunciar=False)
	menu = ventana._menu_contextual_sesiones()
	try:
		etiquetas = [item.GetItemLabelText() for item in menu.GetMenuItems()]
	finally:
		menu.Destroy()
	assert "Dar por terminada" in etiquetas
	assert "Marcar como leída" in etiquetas


# ── Respuestas nuevas de cualquier sesión (#237) ──────────────────────────────


def test_respuesta_nueva_en_sesiones_de_otros_programas(gestor):
	base = AHORA - 1000
	externa = FilaSesion("claude:t", "claude", "Terminal", origen="terminal", actividad=base)
	_con_externas(gestor, [externa])
	# La primera vez se da por leída: no se marca todo el historial como nuevo.
	assert _fila(gestor, "claude:t").no_leidas == 0
	assert gestor.almacen.metadatos()["claude:t"].leida_hasta == base
	# El agente responde en la terminal mientras Ctrl+IA está abierto.
	externa.actividad = base + 100
	externa.estado = Estado.TRABAJANDO
	assert _fila(gestor, "claude:t").no_leidas == 0  # todavía trabajando: no hay respuesta aún
	externa.estado = Estado.INACTIVA
	assert _fila(gestor, "claude:t").no_leidas == 1
	gestor.marcar_terminada("claude:t")
	assert _fila(gestor, "claude:t").no_leidas == 0
	gestor.marcar_terminada("claude:t", False)
	gestor.almacen.fijar_metadato("claude:t", leida_hasta=base)
	assert _fila(gestor, "claude:t").no_leidas == 1
	gestor.marcar_leida("claude:t")
	assert _fila(gestor, "claude:t").no_leidas == 0
	gestor.marcar_no_leida("claude:t")
	assert _fila(gestor, "claude:t").no_leidas == 1
	gestor.marcar_todas_leidas(["claude:t"])
	assert _fila(gestor, "claude:t").no_leidas == 0


def test_la_sesion_abierta_se_va_leyendo_sola(gestor):
	externa = FilaSesion("codex:h", "codex", "Hilo", origen="codex", actividad=AHORA - 500)
	_con_externas(gestor, [externa])
	gestor.listar()
	gestor.sesion_abierta = "codex:h"
	externa.actividad = AHORA - 10
	assert _fila(gestor, "codex:h").no_leidas == 0
	assert gestor.almacen.metadatos()["codex:h"].leida_hasta == AHORA - 10
	gestor.sesion_abierta = ""
	assert _fila(gestor, "codex:h").no_leidas == 0


def test_marcar_leida_y_no_leida_en_sesiones_propias(gestor, tmp_path):
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=tmp_path))
	gestor.marcar_no_leida(clave)
	assert gestor.almacen.sesion(clave).no_leidas == 1
	gestor.marcar_todas_leidas([clave])
	assert gestor.almacen.sesion(clave).no_leidas == 0


# ── Exportar e importar entre equipos (#228) ───────────────────────────────────


def _sesion_con_mensajes(gestor: GestorSesiones, carpeta: Path, titulo: str = "Informe trimestral") -> FilaSesion:
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta, titulo=titulo))
	gestor.enviar(clave, "¿Qué hay en la canción del árbol?")
	gid = gestor.almacen.crear_grupo("Investigación")
	gestor.almacen.anadir_a_grupo(clave, gid)
	return _fila(gestor, clave)


def test_exportar_e_importar_sesiones_de_solo_lectura(gestor, tmp_path):
	fila = _sesion_con_mensajes(gestor, tmp_path)
	ruta = tmp_path / "salida" / "sesiones.zip"
	resultado = intercambio.exportar_sesiones([fila], gestor.mensajes, ruta, almacen=gestor.almacen, equipo="PORTATIL",
		version_app="Ctrl+IA 1.1.0")
	assert (resultado.sesiones, resultado.mensajes) == (1, 4)
	with zipfile.ZipFile(ruta) as archivo:
		nombres = archivo.namelist()
		manifiesto = json.loads(archivo.read("manifiesto.json"))
		sesion = json.loads(archivo.read("sesiones/0001.json"))
	assert manifiesto["formato"] == "ctrlia-sesiones" and manifiesto["version"] == 1
	assert manifiesto["equipo"] == "PORTATIL"
	assert any(n.endswith(".md") for n in nombres)
	assert sesion["clave"] == fila.clave and sesion["grupos"] == ["Investigación"]
	assert "Resultado:\na.txt" in sesion["mensajes"][2]["detalle"]
	assert intercambio.leer_manifiesto(ruta)["sesiones"][0]["titulo"] == "Informe trimestral"

	otro = _gestor(tmp_path / "b.db")
	try:
		importado = intercambio.importar_sesiones(ruta, otro.almacen, equipo_local="SOBREMESA")
		assert (importado.nuevas, importado.equipos) == (1, {"PORTATIL"})
		copia = _fila(otro, importado.claves[0])
		assert copia.importada and not copia.propia
		assert copia.nombre_origen == "Importada de PORTATIL"
		assert copia.grupos == ["Investigación"]
		assert [m.rol for m in otro.mensajes(copia.clave)] == ["usuario", "asistente", "herramienta", "cambio"]
		assert otro.mensajes(copia.clave)[3].cambios == [{"ruta": "a.txt", "diff": "+x"}]
		# Solo lectura: no se puede escribir ni continuar.
		assert otro.conversacion(copia.clave) is None
		with pytest.raises(ValueError):
			otro.enviar(copia.clave, "hola")
		with pytest.raises(ValueError, match="solo lectura"):
			otro.continuar_aqui(copia.clave)
		# Se puede buscar en ella (sin acentos).
		assert any(c == copia.clave for c, _t, _m in otro.buscar("cancion arbol"))
		# Importar otra vez no duplica.
		otra_vez = intercambio.importar_sesiones(ruta, otro.almacen, equipo_local="SOBREMESA")
		assert (otra_vez.nuevas, otra_vez.sin_cambios) == (0, 1)
		# Si la sesión crece en el equipo de origen, se actualiza.
		gestor.enviar(fila.clave, "Otra pregunta")
		intercambio.exportar_sesiones([_fila(gestor, fila.clave)], gestor.mensajes, ruta, equipo="PORTATIL")
		crecida = intercambio.importar_sesiones(ruta, otro.almacen, equipo_local="SOBREMESA")
		assert (crecida.nuevas, crecida.actualizadas) == (0, 1)
		assert len(otro.mensajes(copia.clave)) == len(gestor.mensajes(fila.clave)) == 7
		assert len([f for f in otro.listar() if f.importada]) == 1
		# Reexportada desde el segundo equipo conserva su origen.
		ruta2 = tmp_path / "reexportada.zip"
		intercambio.exportar_sesiones([_fila(otro, copia.clave)], otro.mensajes, ruta2, almacen=otro.almacen, equipo="SOBREMESA")
		with zipfile.ZipFile(ruta2) as archivo:
			reexportada = json.loads(archivo.read("sesiones/0001.json"))
		assert (reexportada["clave"], reexportada["equipo"]) == (fila.clave, "PORTATIL")
		# Borrar las importadas solo quita las copias.
		assert otro.borrar_importadas() == 1
		assert not [f for f in otro.listar() if f.importada]
	finally:
		otro.almacen.cerrar()


def test_importar_omite_las_del_mismo_equipo_y_rechaza_archivos_ajenos(gestor, tmp_path):
	fila = _sesion_con_mensajes(gestor, tmp_path)
	ruta = tmp_path / "s.zip"
	intercambio.exportar_sesiones([fila], gestor.mensajes, ruta, equipo="PORTATIL")
	resultado = intercambio.importar_sesiones(ruta, gestor.almacen, equipo_local="portatil")
	assert (resultado.nuevas, resultado.del_equipo) == (0, 1)
	ajeno = tmp_path / "ajeno.zip"
	with zipfile.ZipFile(ajeno, "w") as archivo:
		archivo.writestr("manifiesto.json", json.dumps({"formato": "otra-cosa", "sesiones": []}))
	with pytest.raises(ValueError, match="no es un archivo de sesiones"):
		intercambio.importar_sesiones(ajeno, gestor.almacen)
	roto = tmp_path / "roto.zip"
	roto.write_bytes(b"esto no es un zip")
	with pytest.raises(ValueError):
		intercambio.importar_sesiones(roto, gestor.almacen)


def test_importar_tolera_campos_desconocidos_y_entradas_rotas(gestor, tmp_path):
	ruta = tmp_path / "futuro.zip"
	with zipfile.ZipFile(ruta, "w") as archivo:
		archivo.writestr("manifiesto.json", json.dumps({"formato": "ctrlia-sesiones", "version": 7, "equipo": "MAC",
			"campo_nuevo": True, "sesiones": [{"archivo": "s/1.json", "titulo": "Buena"}, {"archivo": "s/falta.json", "titulo": "Rota"}]}))
		archivo.writestr("s/1.json", json.dumps({"clave": "claude:1", "proveedor": "claude", "titulo": "Buena",
			"otra_cosa": [1, 2], "mensajes": [{"rol": "usuario", "texto": "hola", "fecha": 5, "desconocido": 1}, "basura"]}))
	resultado = intercambio.importar_sesiones(ruta, gestor.almacen, equipo_local="PC")
	assert (resultado.nuevas, resultado.errores) == (1, ["Rota"])
	assert [m.texto for m in gestor.mensajes(resultado.claves[0])] == ["hola"]


def test_exportar_sin_la_salida_de_las_herramientas(gestor, tmp_path):
	fila = _sesion_con_mensajes(gestor, tmp_path)
	ruta = tmp_path / "ligero.zip"
	intercambio.exportar_sesiones([fila], gestor.mensajes, ruta, incluir_herramientas=False, equipo="PC")
	with zipfile.ZipFile(ruta) as archivo:
		sesion = json.loads(archivo.read("sesiones/0001.json"))
		markdown = archivo.read([n for n in archivo.namelist() if n.endswith(".md")][0]).decode("utf-8")
	herramienta, cambio = sesion["mensajes"][2], sesion["mensajes"][3]
	assert herramienta["detalle"] == "" and herramienta["texto"] == "Ejecutar orden: ls"
	assert cambio["cambios"] == [{"ruta": "a.txt"}]
	assert "a.txt" in markdown and "Resultado:" not in markdown


# ── Informe de sesiones (#251) ─────────────────────────────────────────────────


def test_informe_con_tamanos_recuentos_cache_y_problemas(gestor, tmp_path):
	informe.olvidar_cache()
	propia = _sesion_con_mensajes(gestor, tmp_path, "Propia")
	historial = tmp_path / "historial.jsonl"
	historial.write_text("x" * 2048, encoding="utf-8")
	lecturas: list[Path] = []

	def leer(ruta: Path):
		lecturas.append(ruta)
		return [SimpleNamespace(rol="usuario"), SimpleNamespace(rol="asistente"),
			SimpleNamespace(rol="herramienta", cambios=[{"ruta": "b"}])]

	filas = [
		propia,
		FilaSesion("claude:t", "claude", "Terminal grande", origen="terminal", actividad=AHORA, oculta=True),
		FilaSesion("claude:perdida", "claude", "Perdida", origen="terminal", carpeta=str(tmp_path / "no-existe")),
		FilaSesion("antigravity:a", "antigravity", "App", origen="antigravity", terminada=True),
	]
	fuentes = {"claude:t": SimpleNamespace(ruta_transcript=historial), "claude:perdida": SimpleNamespace(ruta_transcript=None)}
	progreso: list[tuple[int, int]] = []

	def construir():
		return informe.construir(filas, gestor.almacen, fuente_externa=fuentes.get, leer_historial=leer,
			progreso=lambda i, n: progreso.append((i, n)))

	datos = construir()
	assert progreso[-1] == (4, 4)
	por_clave = {d.clave: d for d in datos}
	assert por_clave[propia.clave].recuento.mensajes == 4
	assert por_clave[propia.clave].recuento.usuario == 1 and por_clave[propia.clave].recuento.cambios == 1
	assert por_clave[propia.clave].tamano > 0
	terminal = por_clave["claude:t"]
	assert (terminal.tamano, terminal.recuento.mensajes, terminal.recuento.cambios) == (2048, 3, 1)
	assert por_clave["claude:perdida"].problema and por_clave["claude:perdida"].tamano is None
	assert por_clave["antigravity:a"].problema and por_clave["antigravity:a"].recuento is None
	construir()
	assert len(lecturas) == 1  # el segundo informe usa la caché
	historial.write_text("y" * 4096, encoding="utf-8")
	construir()
	assert len(lecturas) == 2  # el archivo cambió: se vuelve a leer

	totales = informe.calcular_totales(datos)
	assert (totales.sesiones, totales.ocultas, totales.terminadas, totales.con_problemas) == (4, 1, 1, 2)
	assert totales.mas_grandes[0].clave == "claude:t"
	texto = informe.texto_totales(totales)
	assert "Sesiones: 4." in texto and "Claude: 2 sesiones" in texto
	markdown = informe.a_markdown(datos)
	csv = informe.a_csv(datos)
	for contenido in (markdown, csv):
		assert "canción" not in contenido and "Hola de nuevo" not in contenido  # nunca el texto de los mensajes
		assert "Terminal grande" in contenido
	assert "### Terminal grande" in markdown and "## Totales" in markdown
	guardado = informe.guardar(datos, tmp_path / "informe.csv")
	assert guardado.read_text(encoding="utf-8-sig").startswith("Título;Agente")


def test_tamano_y_numero_legibles():
	assert informe.tamano_legible(None) == "No disponible"
	assert informe.tamano_legible(812) == "812 bytes"
	assert informe.tamano_legible(51 * 1024 * 1024 + 200 * 1024) == "51,2 MB"
	assert informe.numero(9550) == "9.550"


# ── Diálogos ────────────────────────────────────────────────────────────────────


def _descendientes(ventana: wx.Window):
	for hijo in ventana.GetChildren():
		yield hijo
		yield from _descendientes(hijo)


def _comprobar_accesibilidad(dialogo: wx.Dialog) -> None:
	assert dialogo.GetEscapeId() == wx.ID_CANCEL
	letras: list[str] = []
	for control in _descendientes(dialogo):
		if isinstance(control, (wx.StaticText, wx.Button, wx.CheckBox)) and "&" in control.GetLabel():
			letras.append(control.GetLabel().split("&", 1)[1][:1].lower())
		if not isinstance(control, INTERACTIVOS):
			continue
		assert control.GetHelpText().strip(), f"sin ayuda (Ctrl+H): {control!r} {control.GetLabel()}"
		if isinstance(control, wx.Button):
			assert control.GetLabel().replace("&", "").strip(" .…").lower() not in INGLES
		if isinstance(control, (wx.ListCtrl, wx.TextCtrl, wx.Choice)):
			hermanos = list(control.GetParent().GetChildren())
			anterior = hermanos[hermanos.index(control) - 1]
			assert isinstance(anterior, wx.StaticText) and "&" in anterior.GetLabel(), control.GetName()
	assert len(letras) == len(set(letras)), f"letras repetidas: {sorted(letras)}"


def test_dialogo_informe_filtra_ordena_y_actua(app_wx, gestor, tmp_path, anuncios):
	from app.ui.dialogos.informe import DialogoInforme

	datos = [
		informe.FilaInforme("claude:g", "Grande", "claude", "Terminal", tamano=5000, recuento=informe.Recuento(10)),
		informe.FilaInforme("ctrlia:p", "Pequeña", "codex", "Ctrl+IA", tamano=10, recuento=informe.Recuento(50), propia=True),
		informe.FilaInforme("antigravity:x", "Ilegible", "antigravity", "App Antigravity", problema="No se puede leer"),
	]
	contexto = SimpleNamespace(gestor=gestor, preferencias_lectura=None)
	dialogo = DialogoInforme(None, datos, contexto=contexto)
	try:
		_comprobar_accesibilidad(dialogo)
		assert "Sesiones: 3." in dialogo.campo_totales.GetValue()
		assert [f.titulo for f in dialogo.visibles] == ["Grande", "Pequeña", "Ilegible"]
		assert dialogo.lista.valores_fila(0)[:3] == ["Grande", "4,9 KB", "10"]
		dialogo.campo_orden.SetSelection(1)  # por mensajes
		dialogo.recargar()
		assert dialogo.visibles[0].titulo == "Pequeña"
		dialogo.campo_mostrar.SetSelection([i for i, (c, _e) in enumerate(dialogo._filtros) if c == "problemas"][0])
		dialogo.recargar()
		assert [f.titulo for f in dialogo.visibles] == ["Ilegible"]
		dialogo.campo_mostrar.SetSelection(0)
		dialogo.campo_filtro.ChangeValue("grande")
		dialogo.recargar()
		assert [f.titulo for f in dialogo.visibles] == ["Grande"]
		assert dialogo.alternar_terminada() and gestor.almacen.metadatos()["claude:g"].terminada > 0
		assert dialogo.alternar_oculta() and gestor.almacen.metadatos()["claude:g"].oculta
		assert dialogo.cambiado
		assert dialogo.abrir() and dialogo.accion == ("abrir", "claude:g")
	finally:
		dialogo.Destroy()
		procesar_eventos()


def test_dialogo_exportar_elige_y_desmarca(app_wx, tmp_path):
	from app.ui.dialogos.intercambio import DialogoExportarSesiones

	filas = [FilaSesion("ctrlia:a", "claude", "Uno"), FilaSesion("claude:b", "claude", "Dos", origen="terminal")]
	dialogo = DialogoExportarSesiones(None, [("Elegida", filas[:1]), ("Todas", filas)], inicial=1)
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.lista.numero_filas() == 2 and dialogo.lista.filas_marcadas() == [0, 1]
		menu = dialogo._crear_menu()
		try:
			assert [i.GetItemLabelText() for i in menu.GetMenuItems()][0] == "Marcar o desmarcar esta sesión"
		finally:
			menu.Destroy()
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.alternar_actual()
		dialogo.casilla_herramientas.SetValue(False)
		assert dialogo.aceptar()
		assert dialogo.claves == ["claude:b"] and dialogo.incluir_herramientas is False
		dialogo.campo_alcance.SetSelection(0)
		dialogo.rellenar()
		assert dialogo.lista.numero_filas() == 1
	finally:
		dialogo.Destroy()
		procesar_eventos()


def test_menus_contextuales_de_elegir_sesiones_y_grupo(app_wx, gestor, monkeypatch):
	from app.ui.dialogos import grupos as modulo_grupos
	from app.ui.dialogos.grupo import DialogoElegirSesiones
	from app.ui.dialogos.grupos import DialogoElegirGrupo

	filas = [FilaSesion("ctrlia:a", "claude", "Uno"), FilaSesion("claude:b", "claude", "Dos", origen="terminal")]
	elegir = DialogoElegirSesiones(None, "Añadir", filas, "Marca las sesiones.")
	try:
		menu = elegir._crear_menu()
		try:
			assert [i.GetItemLabelText() for i in menu.GetMenuItems()][0] == "Marcar o desmarcar esta sesión"
		finally:
			menu.Destroy()
		elegir.marcar_visibles(True)
		assert elegir.marcadas == {"ctrlia:a", "claude:b"}
		elegir.lista.SetSelection(0)
		elegir.alternar_actual()
		assert elegir.marcadas == {"claude:b"}
	finally:
		elegir.Destroy()
	gestor.almacen.crear_grupo("Trabajo")
	monkeypatch.setattr(modulo_grupos, "pedir_nombre_grupo", lambda *a, **k: "Nuevo")
	grupo = DialogoElegirGrupo(None, gestor.almacen, "Elegir")
	try:
		menu = grupo._crear_menu()
		try:
			assert [i.GetItemLabelText() for i in menu.GetMenuItems()] == ["Elegir este grupo", "Nuevo grupo..."]
		finally:
			menu.Destroy()
		identificador = grupo.nuevo()
		assert dict(gestor.almacen.grupos())[identificador] == "Nuevo"
	finally:
		grupo.Destroy()
		procesar_eventos()


def test_menus_nuevos_existen_en_la_ventana():
	from app.ui.ventana_principal import MENUS, VentanaPrincipal

	metodos = {e[3] for _t, elementos in MENUS for e in elementos if e[0] not in ("-", "submenu")}
	for metodo in ("accion_terminada", "accion_marcar_leida", "accion_marcar_todas_leidas", "accion_informe",
			"accion_exportar_varias", "accion_importar", "accion_borrar_importadas"):
		assert metodo in metodos and hasattr(VentanaPrincipal, metodo)


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
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False,
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
