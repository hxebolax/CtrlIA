"""Mensajes favoritos: almacén, identificador estable, marcar y quitar, Alt+Flecha, indicador y lista de todos.

Sin mostrar ventanas ni hablar: la voz se sustituye por una lista y los modales se simulan.
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")

from app.services import favoritos  # noqa: E402
from app.services.almacen import Almacen, MensajeFavorito  # noqa: E402
from app.services.gestor import MensajeVista  # noqa: E402
from tests.test_conversacion_agente import _sesion, _sesion_larga, anuncios, ventana  # noqa: E402, F401


# ── Almacén ─────────────────────────────────────────────────────────────────────


def test_almacen_marca_lista_y_quita(tmp_path):
	almacen = Almacen(tmp_path / "prueba.db")
	try:
		almacen.marcar_favorito("ctrlia:a", "7", "asistente", 200.0, "Segunda respuesta")
		almacen.marcar_favorito("ctrlia:a", "3", "usuario", 100.0, "Primera pregunta", nota="importante")
		almacen.marcar_favorito("claude:b", "uuid-1", "asistente", 50.0, "De otro programa")
		assert almacen.es_favorito("ctrlia:a", "7") and not almacen.es_favorito("ctrlia:a", "8")
		# Los de una sesión, en el orden de la conversación.
		assert [f.mensaje for f in almacen.favoritos_de_sesion("ctrlia:a")] == ["3", "7"]
		# Todos, del marcado más recientemente al más antiguo.
		assert [f.mensaje for f in almacen.favoritos()] == ["uuid-1", "3", "7"]
		# Volver a marcarlo pone al día los datos y conserva la nota.
		almacen.marcar_favorito("ctrlia:a", "3", "usuario", 100.0, "Primera pregunta, cambiada")
		guardado = next(f for f in almacen.favoritos_de_sesion("ctrlia:a") if f.mensaje == "3")
		assert guardado.nota == "importante" and guardado.extracto == "Primera pregunta, cambiada"
		assert almacen.desmarcar_favorito("ctrlia:a", "3") is True
		assert almacen.desmarcar_favorito("ctrlia:a", "3") is False
		# Al borrar una sesión se van sus favoritos (los de las demás se quedan).
		almacen.borrar_sesion("ctrlia:a")
		assert almacen.favoritos_de_sesion("ctrlia:a") == []
		assert [f.sesion for f in almacen.favoritos()] == ["claude:b"]
	finally:
		almacen.cerrar()


# ── Identificador estable ───────────────────────────────────────────────────────


def test_clave_del_mensaje_usa_el_id_o_una_huella_estable():
	assert favoritos.clave_mensaje("ctrlia:x", MensajeVista("asistente", "Hola", 10.0, id=42)) == "42"
	assert favoritos.clave_mensaje("claude:x", MensajeVista("asistente", "Hola", 10.0, id="abc-uuid")) == "abc-uuid"
	# Sin id (Codex, Ollama): huella de rol, fecha en segundos y texto; igual en cada apertura.
	uno = favoritos.clave_mensaje("codex:x", MensajeVista("asistente", "Respuesta", 10.2))
	otro = favoritos.clave_mensaje("codex:x", MensajeVista("asistente", "Respuesta", 10.4))
	assert uno == otro and uno.startswith(favoritos.PREFIJO_HUELLA)
	assert favoritos.clave_mensaje("codex:x", MensajeVista("usuario", "Respuesta", 10.2)) != uno
	assert favoritos.clave_mensaje("codex:x", MensajeVista("asistente", "Otra cosa", 10.2)) != uno
	# Las importadas cambian de número al volver a importarlas: siempre huella.
	importada = favoritos.clave_mensaje("importada:1", MensajeVista("asistente", "Respuesta", 10.2, id=5))
	assert importada == uno
	# Solo cuentan los primeros 500 caracteres.
	largo = "x" * 600
	assert favoritos.huella("asistente", 1, largo) == favoritos.huella("asistente", 1, largo + "y")


def test_marcas_reconocen_por_extracto_si_cambia_la_fecha():
	mensaje = MensajeVista("asistente", "Respuesta  de\nCodex", 100.0)
	guardado = MensajeFavorito("codex:x", favoritos.clave_mensaje("codex:x", mensaje), "asistente", 100.0,
		favoritos.extracto(mensaje.texto))
	assert guardado.extracto == "Respuesta de Codex"
	# Codex devuelve el mismo mensaje con la fecha del turno en vez de la del paso.
	movido = MensajeVista("asistente", "Respuesta de\nCodex", 160.0)
	otros = [MensajeVista("usuario", "Pregunta", 90.0), movido]
	marcas = favoritos.Marcas.crear("codex:x", [guardado], otros)
	assert marcas.favorito(movido) == guardado.mensaje
	assert not marcas.favorito(otros[0])
	assert favoritos.posiciones(marcas, otros) == [1]
	assert favoritos.buscar(marcas, otros, guardado.mensaje) == 1


# ── Ventana: marcar, quitar, indicador y menú contextual ─────────────────────────


def _abrir(ventana, clave) -> None:
	ventana.lista_mensajes.SetFocus = lambda: None  # sin foco real en las pruebas
	ventana.abrir_sesion(clave, mover_foco=False)


def _ir_a(ventana, texto: str) -> None:
	indice = next(i for i, m in enumerate(ventana._mensajes) if m.texto == texto)
	ventana.lista_mensajes.enfocar_fila(indice, anunciar=False)


def _indicadores(ventana) -> str:
	columna = ventana.columnas_mensajes().index("indicadores")
	return ventana.lista_mensajes.valores_fila(ventana.lista_mensajes.GetFocusedItem())[columna]


def test_marcar_y_quitar_desde_la_lista(ventana, tmp_path, anuncios):
	clave = _sesion_larga(ventana, tmp_path, 10)
	_abrir(ventana, clave)
	_ir_a(ventana, "mensaje 4")
	assert "Favorito" not in _indicadores(ventana)
	ventana.accion_favorito_mensaje()
	assert anuncios[-1] == "Marcado como favorito."
	assert _indicadores(ventana).startswith("Favorito")
	guardados = ventana.almacen.favoritos_de_sesion(clave)
	assert len(guardados) == 1 and guardados[0].extracto == "mensaje 4" and guardados[0].rol == "asistente"
	assert guardados[0].mensaje == str(ventana.mensaje_actual().id)
	# Al volver a abrir la sesión sigue siendo favorito (el id es estable).
	_abrir(ventana, clave)
	_ir_a(ventana, "mensaje 4")
	assert _indicadores(ventana).startswith("Favorito")
	# El indicador se puede apagar en Preferencias.
	ventana.poner_opcion("lectura.indicador_favorito", False)
	ventana._repintar_mensajes()
	assert "Favorito" not in _indicadores(ventana)
	ventana.poner_opcion("lectura.indicador_favorito", True)
	ventana._repintar_mensajes()
	ventana.accion_favorito_mensaje()
	assert anuncios[-1] == "Quitado de favoritos."
	assert "Favorito" not in _indicadores(ventana)
	assert ventana.almacen.favoritos_de_sesion(clave) == []


def test_menu_contextual_dice_marcar_o_quitar(ventana, tmp_path):
	clave = _sesion_larga(ventana, tmp_path, 4)
	_abrir(ventana, clave)
	_ir_a(ventana, "mensaje 2")

	def etiquetas() -> list[str]:
		menu = ventana._menu_contextual_mensajes()
		try:
			return [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		finally:
			menu.Destroy()

	actuales = etiquetas()
	assert "Marcar como &favorito\tCtrl+Alt+F" in actuales
	assert "Ir al favorito anterior\tAlt+Arriba" in actuales and "Ir al favorito siguiente\tAlt+Abajo" in actuales
	ventana.alternar_favorito(ventana.mensaje_actual())
	assert "Quitar de &favoritos\tCtrl+Alt+F" in etiquetas()


def test_sin_mensaje_elegido_lo_dice(ventana, anuncios):
	ventana.accion_favorito_mensaje()
	assert anuncios[-1] == "Elige primero un mensaje en la lista de mensajes."


# ── Alt+Flecha arriba y abajo ────────────────────────────────────────────────────


def _marcar(ventana, clave, *numeros: int) -> None:
	for guardado in ventana.almacen.mensajes(clave):
		if guardado.texto in {"mensaje %d" % n for n in numeros}:
			ventana.almacen.marcar_favorito(clave, str(guardado.id), guardado.rol, guardado.fecha, guardado.texto)


def test_alt_flecha_recorre_los_favoritos_y_carga_los_antiguos(ventana, tmp_path, anuncios):
	ventana.poner_opcion("mensajes.recientes", 50)
	clave = _sesion_larga(ventana, tmp_path)
	_marcar(ventana, clave, 5, 100, 120)
	_abrir(ventana, clave)
	assert len(ventana._anteriores) == 80
	assert ventana.mensaje_actual().texto == "mensaje 130"

	assert ventana._tecla_favoritos(wx.WXK_UP, wx.MOD_ALT, desde_lectura=False) is True
	assert ventana.mensaje_actual().texto == "mensaje 120"
	assert anuncios[-1].startswith("Favorito 3 de 3. ") and "mensaje 120" in anuncios[-1]
	ventana.ir_a_favorito(-1)
	assert ventana.mensaje_actual().texto == "mensaje 100"
	# El 5 aún no está en la lista: se cargan los mensajes justos para llegar a él.
	ventana.ir_a_favorito(-1)
	assert ventana.mensaje_actual().texto == "mensaje 5"
	assert len(ventana._anteriores) == 4 and ventana._mensajes[0].rol == "anteriores"
	assert anuncios[-1].startswith("Favorito 1 de 3. ")
	assert ventana.ir_a_favorito(-1) is False
	assert anuncios[-1] == "No hay ningún favorito antes de este mensaje."
	assert ventana.mensaje_actual().texto == "mensaje 5"

	assert ventana._tecla_favoritos(wx.WXK_DOWN, wx.MOD_ALT, desde_lectura=False) is True
	assert ventana.mensaje_actual().texto == "mensaje 100"
	ventana.ir_a_favorito(1)
	ventana.ir_a_favorito(1)
	assert anuncios[-1] == "No hay ningún favorito después de este mensaje."
	# Otras teclas siguen su curso.
	assert ventana._tecla_favoritos(wx.WXK_UP, wx.MOD_NONE, desde_lectura=False) is False
	assert ventana._tecla_favoritos(wx.WXK_LEFT, wx.MOD_ALT, desde_lectura=False) is False


def test_alt_flecha_desde_la_fila_de_anteriores(ventana, tmp_path, anuncios):
	ventana.poner_opcion("mensajes.recientes", 50)
	clave = _sesion_larga(ventana, tmp_path)
	_marcar(ventana, clave, 80, 81)
	_abrir(ventana, clave)
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)  # «Cargar mensajes anteriores»
	ventana.ir_a_favorito(1)
	assert ventana.mensaje_actual().texto == "mensaje 81"
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	ventana.ir_a_favorito(-1)
	assert ventana.mensaje_actual().texto == "mensaje 80"


def test_alt_flecha_sin_favoritos_lo_dice(ventana, tmp_path, anuncios):
	clave = _sesion_larga(ventana, tmp_path, 5)
	_abrir(ventana, clave)
	assert ventana.ir_a_favorito(-1) is False
	assert "no tiene mensajes favoritos" in anuncios[-1]


def test_alt_flecha_desde_el_panel_de_lectura_no_mueve_el_foco(ventana, tmp_path, anuncios):
	ventana.poner_opcion("ventana.panel_lectura", True)
	ventana.aplicar_disposicion()
	clave = _sesion_larga(ventana, tmp_path, 10)
	_marcar(ventana, clave, 3)
	_abrir(ventana, clave)
	enfocada = []
	ventana.lista_mensajes.SetFocus = lambda: enfocada.append(True)
	evento = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
	evento.SetKeyCode(wx.WXK_UP)
	evento.SetAltDown(True)
	ventana._al_tecla_lectura(evento)
	assert ventana.mensaje_actual().texto == "mensaje 3"
	assert ventana.panel_lectura.GetValue() == "mensaje 3"
	assert not enfocada
	assert anuncios[-1].startswith("Favorito 1 de 1. ")


# ── Lista de todos los favoritos ─────────────────────────────────────────────────


def _filas(*favoritos_):
	return [(f, [f.extracto, f.nota, "Sesión", "Agente", "hoy"]) for f in favoritos_]


def test_dialogo_abre_quita_y_copia(app_wx, dialogo_modal, monkeypatch):
	from app.ui import avisos
	from app.ui.dialogos.favoritos import DialogoFavoritos

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	uno = MensajeFavorito("ctrlia:a", "1", "asistente", 1.0, "Primero")
	dos = MensajeFavorito("claude:borrada", "x", "usuario", 2.0, "Segundo")
	quitados, copiados = [], []
	dialogo = DialogoFavoritos(None, _filas(uno, dos), existe=lambda f: f.sesion != "claude:borrada",
		quitar=lambda f: quitados.append(f) or True, copiar=copiados.append)
	try:
		assert dialogo.lista.GetItemCount() == 2
		assert dialogo.lista.GetColumnCount() == 5  # Mensaje, Nota, Sesión, Quién y Fecha
		assert dialogo.lista.GetColumn(0).GetText() == "Mensaje"
		assert dialogo.etiqueta.GetLabel() == "&Mensajes favoritos (2):"
		# Ctrl+C copia el elegido (con todo su texto, lo hace la ventana principal).
		dialogo.copiar()
		assert copiados == [uno]
		# Una sesión que ya no existe: lo dice y no cierra.
		dialogo.lista.enfocar_fila(1, anunciar=False)
		assert dialogo.abrir() is False and dialogo.resultado is None
		assert "ya no está en Ctrl+IA" in dichos[-1]
		# Suprimir pregunta antes; «No» no quita nada.
		dialogo_modal.responder(wx.ID_NO)
		assert dialogo.quitar() is False and not quitados
		dialogo_modal.responder(wx.ID_YES)
		assert dialogo.quitar() is True and quitados == [dos]
		assert dialogo.lista.GetItemCount() == 1 and dichos[-1] == "Quitado de favoritos. Quedan 1."
		# Intro elige el que queda.
		assert dialogo.abrir() is True and dialogo.resultado == uno
		menu = dialogo._crear_menu()
		try:
			etiquetas = [i.GetItemLabel() for i in menu.GetMenuItems() if not i.IsSeparator()]
		finally:
			menu.Destroy()
		assert "&Quitar de favoritos...\tSupr" in etiquetas and "&Copiar el texto\tCtrl+C" in etiquetas
	finally:
		dialogo.Destroy()


def test_dialogo_vacio(app_wx):
	from app.ui.dialogos.favoritos import DialogoFavoritos

	dialogo = DialogoFavoritos(None, [], existe=lambda f: True, quitar=lambda f: True, copiar=lambda f: None)
	try:
		assert dialogo.lista.GetItemCount() == 0 and dialogo.favorito_actual() is None
		assert dialogo._crear_menu() is None
		assert "ninguno" in dialogo.etiqueta.GetLabel()
	finally:
		dialogo.Destroy()


def test_lista_global_lleva_al_mensaje_aunque_no_este_cargado(ventana, tmp_path, dialogo_modal, anuncios):
	ventana.poner_opcion("mensajes.recientes", 50)
	larga = _sesion_larga(ventana, tmp_path)
	_marcar(ventana, larga, 3)
	otra = _sesion(ventana, tmp_path, "Otra")
	ventana.almacen.anadir_mensaje(otra, "usuario", "hola")
	_abrir(ventana, otra)
	vistos = []

	def elegir(dialogo):
		vistos.append([dialogo.lista.valores_fila(i) for i in range(dialogo.lista.GetItemCount())])
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.abrir()
		return wx.ID_OK

	dialogo_modal.responder(elegir)
	ventana.accion_mensajes_favoritos()
	assert vistos[0] == [["mensaje 3", "", "Larga", "Tú", vistos[0][0][4]]]
	assert ventana.sesion_actual == larga
	assert ventana.mensaje_actual().texto == "mensaje 3"
	assert len(ventana._anteriores) == 2


def test_lista_global_sesion_que_ya_no_existe(ventana, dialogo_modal):
	favorito = MensajeFavorito("claude:no-existe", "abc", "asistente", 1.0, "Algo")
	assert ventana.valores_favorito(favorito)[2] == "Sesión que ya no está en Ctrl+IA"
	assert ventana.ir_a_favorito_guardado(favorito) is False
	assert any("ya no está en Ctrl+IA" in texto for texto in dialogo_modal.ultimo.textos)


def test_quitar_y_copiar_desde_la_lista_global(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion_larga(ventana, tmp_path, 6)
	ventana.almacen.actualizar_mensaje(ventana.almacen.mensajes(clave)[1].id, detalle="detalle del 2")
	_marcar(ventana, clave, 2)
	favorito = ventana.almacen.favoritos()[0]
	copiados = []
	monkeypatch.setattr(type(ventana), "copiar", lambda self, texto: copiados.append(texto))
	# Con la sesión cerrada, se lee (en segundo plano) para copiar el mensaje completo.
	ventana.copiar_favorito(favorito)
	assert copiados[-1] == "mensaje 2\n\ndetalle del 2"
	_abrir(ventana, clave)
	_ir_a(ventana, "mensaje 2")
	assert _indicadores(ventana).startswith("Favorito")
	assert ventana.quitar_favorito_guardado(favorito) is True
	assert "Favorito" not in _indicadores(ventana)
	# Si el mensaje ya no está, se copia el principio guardado y se dice.
	ventana.copiar_favorito(MensajeFavorito(clave, "999999", "asistente", 1.0, "lo guardado"))
	assert copiados[-1] == "lo guardado" and "principio" in anuncios[-1]


def test_menu_ver_tiene_las_dos_acciones(ventana):
	from app.ui.ventana_principal import MENUS

	ver = dict(MENUS)["&Ver"]
	acciones = {e[3]: e[1] for e in ver if len(e) == 4}
	assert acciones["accion_favorito_mensaje"].endswith("\tCtrl+Alt+F")
	assert acciones["accion_mensajes_favoritos"].endswith("\tCtrl+Alt+Shift+F")
	assert callable(ventana.accion_mensajes_favoritos) and callable(ventana.accion_favorito_mensaje)
