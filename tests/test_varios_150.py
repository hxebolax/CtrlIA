"""Novedades de la 1.5.0 (lote «varios»): notas en los favoritos, continuar una sesión importada (#228,
segunda parte), divisor entre los mensajes y la respuesta (#242) y palabras curiosas en la barra de
estado (#314).

Sin mostrar ventanas ni hablar: la voz se sustituye por una lista y los cuadros se simulan.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from app.services import favoritos, intercambio  # noqa: E402
from app.services.agentes.base import AjustesSesion, Evento, TipoEvento  # noqa: E402
from app.services.almacen import Almacen, MensajeFavorito, SesionPropia, nueva_clave_importada  # noqa: E402
from app.services.gestor import GestorSesiones, MensajeVista  # noqa: E402
from app.ui import palabras_curiosas, vp_disposicion  # noqa: E402
from app.ui.lectura_listas import indicadores_mensaje  # noqa: E402
from tests.ayudas_wx import esperar_hasta  # noqa: E402
from tests.test_conversacion_agente import _sesion, _sesion_larga, anuncios, ventana  # noqa: E402, F401
from tests.test_gestor import ProveedorFalso  # noqa: E402

OPCIONES = {"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False}


def _abrir(ventana, clave) -> None:
	ventana.lista_mensajes.SetFocus = lambda: None  # sin foco real en las pruebas
	ventana.abrir_sesion(clave, mover_foco=False)


def _ir_a(ventana, texto: str) -> None:
	indice = next(i for i, m in enumerate(ventana._mensajes) if m.texto == texto)
	ventana.lista_mensajes.enfocar_fila(indice, anunciar=False)


def _indicadores(ventana) -> str:
	columna = ventana.columnas_mensajes().index("indicadores")
	return ventana.lista_mensajes.valores_fila(ventana.lista_mensajes.GetFocusedItem())[columna]


def _escribir(monkeypatch, modulo, respuesta: str | None) -> list[str]:
	"""Sustituye ``pedir_texto`` de ``modulo``: devuelve ``respuesta`` y apunta el valor propuesto."""
	propuestos: list[str] = []

	def pedir(_padre, _etiqueta, _titulo, valor="", **opciones):
		propuestos.append(valor)
		assert opciones.get("max_caracteres") == favoritos.LARGO_NOTA
		return respuesta

	monkeypatch.setattr(modulo, "pedir_texto", pedir)
	return propuestos


# ── 1. Notas en los mensajes favoritos ───────────────────────────────────────────


def test_almacen_pone_y_quita_la_nota(tmp_path):
	almacen = Almacen(tmp_path / "prueba.db")
	try:
		almacen.marcar_favorito("ctrlia:a", "3", "usuario", 1.0, "Pregunta")
		assert almacen.poner_nota_favorito("ctrlia:a", "3", "la buena") is True
		assert almacen.favoritos()[0].nota == "la buena"
		# Volver a marcarlo conserva la nota; vacía, se quita.
		almacen.marcar_favorito("ctrlia:a", "3", "usuario", 1.0, "Pregunta")
		assert almacen.favoritos()[0].nota == "la buena"
		assert almacen.poner_nota_favorito("ctrlia:a", "3", "") is True
		assert almacen.favoritos()[0].nota == ""
		# Un mensaje que no es favorito no se anota.
		assert almacen.poner_nota_favorito("ctrlia:a", "99", "nada") is False
	finally:
		almacen.cerrar()


def test_limpiar_nota_y_marcas():
	assert favoritos.limpiar_nota("  la explicación\n buena  ") == "la explicación buena"
	assert len(favoritos.limpiar_nota("x" * 500)) == favoritos.LARGO_NOTA
	assert favoritos.limpiar_nota(None) == ""
	mensaje = MensajeVista("asistente", "Respuesta", 10.0, id=7)
	marcas = favoritos.Marcas.crear("ctrlia:a", [MensajeFavorito("ctrlia:a", "7", "asistente", 10.0, "Respuesta", "ojo")],
		[mensaje])
	assert marcas.nota(mensaje) == "ojo"
	assert marcas.nota(MensajeVista("usuario", "Otro", 11.0, id=8)) == ""


def test_indicador_dice_favorito_con_su_nota():
	opcion = lambda _clave, defecto=None: defecto  # noqa: E731
	assert indicadores_mensaje("hola", "asistente", 0, opcion, favorito=True).startswith("Favorito")
	assert indicadores_mensaje("hola", "asistente", 0, opcion, favorito=True, nota_favorito="la buena") == "Favorito: la buena"
	# Sin ser favorito, la nota no sale.
	assert indicadores_mensaje("hola", "asistente", 0, opcion, nota_favorito="la buena") == ""


def test_escribir_cambiar_y_quitar_la_nota_desde_la_lista(ventana, tmp_path, anuncios, monkeypatch):
	from app.ui import vp_mensajes

	clave = _sesion_larga(ventana, tmp_path, 6)
	_abrir(ventana, clave)
	_ir_a(ventana, "mensaje 3")
	ventana.accion_favorito_mensaje()
	propuestos = _escribir(monkeypatch, vp_mensajes, "  la explicación\nbuena ")
	ventana.accion_nota_favorito()
	assert propuestos == [""] and anuncios[-1] == "Nota guardada."
	assert ventana.almacen.favoritos_de_sesion(clave)[0].nota == "la explicación buena"
	assert _indicadores(ventana).startswith("Favorito: la explicación buena")
	# Al ir al favorito con Alt+flecha se dice la nota.
	_ir_a(ventana, "mensaje 6")
	ventana.ir_a_favorito(-1)
	assert anuncios[-1].startswith("Favorito 1 de 1, nota: la explicación buena. ")
	# Cancelar no cambia nada; vacía la quita.
	_escribir(monkeypatch, vp_mensajes, None)
	assert ventana.escribir_nota_favorito(ventana.mensaje_actual()) is False
	propuestos = _escribir(monkeypatch, vp_mensajes, "   ")
	ventana.accion_nota_favorito()
	assert propuestos == ["la explicación buena"] and anuncios[-1] == "Nota quitada."
	assert ventana.almacen.favoritos_de_sesion(clave)[0].nota == ""
	assert _indicadores(ventana).startswith("Favorito") and not _indicadores(ventana).startswith("Favorito:")
	# Ctrl+Alt+F sobre un favorito con nota lo sigue quitando.
	_escribir(monkeypatch, vp_mensajes, "otra")
	ventana.accion_nota_favorito()
	ventana.accion_favorito_mensaje()
	assert anuncios[-1] == "Quitado de favoritos." and ventana.almacen.favoritos_de_sesion(clave) == []


def test_nota_en_un_mensaje_que_no_es_favorito_lo_marca(ventana, tmp_path, anuncios, monkeypatch):
	from app.ui import vp_mensajes

	clave = _sesion_larga(ventana, tmp_path, 4)
	_abrir(ventana, clave)
	_ir_a(ventana, "mensaje 2")
	_escribir(monkeypatch, vp_mensajes, "")
	assert ventana.escribir_nota_favorito(ventana.mensaje_actual()) is False
	assert anuncios[-1] == "Sin nota: el mensaje sigue sin ser favorito."
	_escribir(monkeypatch, vp_mensajes, "recordar")
	assert ventana.escribir_nota_favorito(ventana.mensaje_actual()) is True
	assert anuncios[-1] == "Marcado como favorito con la nota."
	guardados = ventana.almacen.favoritos_de_sesion(clave)
	assert len(guardados) == 1 and guardados[0].nota == "recordar" and guardados[0].extracto == "mensaje 2"


def test_menu_contextual_y_menu_ver_tienen_la_orden(ventana, tmp_path):
	from app.ui.ventana_principal import MENUS

	clave = _sesion_larga(ventana, tmp_path, 4)
	_abrir(ventana, clave)
	_ir_a(ventana, "mensaje 2")

	def etiquetas() -> list[str]:
		menu = ventana._menu_contextual_mensajes()
		try:
			return [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		finally:
			menu.Destroy()

	orden = "Escribir una nota en el favorito...\tCtrl+Alt+Mayús+N"
	assert orden not in etiquetas()
	ventana.alternar_favorito(ventana.mensaje_actual())
	assert orden in etiquetas()
	ver = dict(MENUS)["&Ver"]
	acciones = {e[3]: e[1] for e in ver if len(e) == 4}
	assert acciones["accion_nota_favorito"].endswith("\tCtrl+Alt+Shift+N")
	assert callable(ventana.accion_nota_favorito)


def test_lista_global_tiene_la_columna_nota(ventana, tmp_path):
	clave = _sesion_larga(ventana, tmp_path, 3)
	guardado = ventana.almacen.mensajes(clave)[0]
	ventana.almacen.marcar_favorito(clave, str(guardado.id), guardado.rol, guardado.fecha, guardado.texto, nota="ojo")
	favorito = ventana.almacen.favoritos()[0]
	assert ventana.valores_favorito(favorito)[:3] == ["mensaje 1", "ojo", "Larga"]
	assert ventana.anotar_favorito_guardado(favorito, " otra\nnota ") is True
	assert ventana.almacen.favoritos()[0].nota == "otra nota"


def test_dialogo_f2_escribe_la_nota(app_wx, monkeypatch):
	from app.ui.dialogos import favoritos as dialogo_favoritos
	from app.ui.dialogos.favoritos import COLUMNAS, DialogoFavoritos

	dichos: list[str] = []
	from app.ui import avisos

	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	uno = MensajeFavorito("ctrlia:a", "1", "asistente", 1.0, "Primero")
	anotados: list[tuple[str, str]] = []
	filas = [(uno, ["Primero", "", "Sesión", "Agente", "hoy"])]
	dialogo = DialogoFavoritos(None, filas, existe=lambda f: True, quitar=lambda f: True, copiar=lambda f: None,
		anotar=lambda f, nota: anotados.append((f.mensaje, nota)) or True)
	try:
		assert dialogo.lista.GetColumnCount() == len(COLUMNAS) == 5
		assert dialogo.lista.GetColumn(1).GetText() == "Nota"
		_escribir(monkeypatch, dialogo_favoritos, "  importante ")
		evento = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
		evento.SetKeyCode(wx.WXK_F2)
		dialogo._al_tecla(evento)
		assert anotados == [("1", "importante")] and uno.nota == "importante"
		assert dialogo.lista.valores_fila(0)[1] == "importante" and dichos[-1] == "Nota guardada."
		menu = dialogo._crear_menu()
		try:
			etiquetas = [i.GetItemLabel() for i in menu.GetMenuItems() if not i.IsSeparator()]
		finally:
			menu.Destroy()
		assert "Escribir una &nota...\tF2" in etiquetas
	finally:
		dialogo.Destroy()


# ── 2. Continuar desde una sesión importada (#228, segunda parte) ───────────────


def test_contexto_entero_y_recortado():
	mensajes = [MensajeVista("usuario", "Arregla el informe", 1.0), MensajeVista("herramienta", "leer a.txt", 2.0),
		MensajeVista("asistente", "Hecho: cambiado el total", 3.0)]
	entero = intercambio.contexto_para_continuar(mensajes, "Informe", "PORTATIL", "claude")
	assert not entero.recortada and entero.mensajes == 2
	assert "otro equipo (PORTATIL) con Claude Code" in entero.texto and "«Informe»" in entero.texto
	assert "Va ENTERA" in entero.texto
	assert "Persona: Arregla el informe" in entero.texto and "Agente: Hecho: cambiado el total" in entero.texto
	assert "leer a.txt" not in entero.texto
	assert entero.texto.endswith("Mensaje nuevo de la persona:")
	# Larga: se conserva el primero, se quitan los del medio y se cortan los enormes.
	largos = [MensajeVista("usuario", "La tarea: " + "a" * 50, 0.0)]
	largos += [MensajeVista("asistente" if i % 2 else "usuario", f"paso {i} " + "b" * 900, float(i)) for i in range(1, 40)]
	largos.append(MensajeVista("asistente", "z" * 9000, 50.0))
	recortado = intercambio.contexto_para_continuar(largos, maximo=8000)
	assert recortado.recortada and recortado.omitidos > 0 and recortado.cortados == 1
	assert "RECORTADA" in recortado.texto and "mensajes del medio omitidos" in recortado.texto
	assert "La tarea: " in recortado.texto and "paso 39" in recortado.texto and "paso 1 " not in recortado.texto
	assert len(recortado.texto) < 8000 + 2000


def _gestor(ruta: Path) -> GestorSesiones:
	return GestorSesiones(Almacen(ruta), {"falso": ProveedorFalso()}, lambda c, e: None,
		lambda clave, defecto: OPCIONES.get(clave, defecto))


def _importada(almacen: Almacen, proveedor: str = "falso", carpeta: str = "") -> str:
	clave = nueva_clave_importada()
	almacen.guardar_sesion(SesionPropia(clave=clave, proveedor=proveedor, titulo="Informe", carpeta=carpeta,
		creada=1.0, actividad=2.0, extra={"importada": {"identidad": "PORTATIL|x", "equipo": "PORTATIL",
			"clave_original": "ctrlia:x", "origen": "propia", "importada": time.time()}}))
	almacen.reemplazar_mensajes(clave, [
		{"rol": "usuario", "texto": "Arregla el informe", "fecha": 10.0},
		{"rol": "asistente", "texto": "Hecho", "fecha": 11.0, "datos": {"cambios": [{"ruta": "a.txt", "diff": "+x"}]}},
	])
	return clave


def test_preparar_desde_importada_y_primer_envio(tmp_path, data_temporal):
	gestor = _gestor(tmp_path / "a.db")
	try:
		importada = _importada(gestor.almacen)
		antes = [(m.rol, m.texto) for m in gestor.almacen.mensajes(importada)]
		clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=tmp_path, titulo="Informe (continuación)"))
		contexto = gestor.preparar_desde_importada(clave, importada)
		assert not contexto.recortada and gestor.contexto_pendiente(clave)
		copiados = gestor.almacen.mensajes(clave)
		assert [m.rol for m in copiados] == ["usuario", "asistente", "sistema"]
		assert copiados[1].datos["cambios"] == [{"ruta": "a.txt", "diff": "+x"}] and copiados[1].datos["anterior"]
		assert "importada de PORTATIL" in copiados[2].texto and "entera" in copiados[2].texto
		assert gestor.almacen.sesion(clave).extra["desde_importada"] == importada
		# El primer mensaje lleva delante la conversación anterior; se guarda solo lo que escribió la persona.
		gestor.enviar(clave, "Sigue con el gráfico")
		conversacion = gestor.proveedores["falso"].conversaciones[-1]
		assert conversacion.enviados[0].startswith("[Ctrl+IA] Esta conversación empezó en otro equipo (PORTATIL)")
		assert conversacion.enviados[0].endswith("Mensaje nuevo de la persona:\n\nSigue con el gráfico")
		assert gestor.almacen.mensajes(clave)[3].texto == "Sigue con el gráfico"
		assert not gestor.contexto_pendiente(clave)
		assert "contexto_importada_enviado" in gestor.almacen.sesion(clave).extra
		# El segundo, ya no.
		gestor.enviar(clave, "Y la leyenda")
		assert conversacion.enviados[-1] == "Y la leyenda"
		# La importada no se ha tocado y sigue sin poder continuarse tal cual.
		assert [(m.rol, m.texto) for m in gestor.almacen.mensajes(importada)] == antes
		assert gestor.conversacion(importada) is None
		with pytest.raises(ValueError, match="solo lectura"):
			gestor.continuar_aqui(importada)
		with pytest.raises(ValueError):
			gestor.preparar_desde_importada(importada, clave)
	finally:
		gestor.almacen.cerrar()


def _fila_importada(ventana, clave: str):
	def esta() -> bool:
		if ventana.fila_de(clave) is None:
			ventana.refrescar_sesiones(True)
			return False
		return True

	esperar_hasta(esta, 30, intervalo=0.05)
	return ventana.fila_de(clave)


def test_continuar_aqui_en_una_importada_abre_nueva_sesion(ventana, tmp_path, anuncios, monkeypatch):
	importada = _importada(ventana.almacen, proveedor="claude", carpeta=str(tmp_path))
	fila = _fila_importada(ventana, importada)
	llamadas: list[dict] = []

	def nueva(self, carpeta=None, mensaje="", agente="", titulo="", preparar=None, **_otros):
		llamadas.append({"carpeta": carpeta, "mensaje": mensaje, "agente": agente, "titulo": titulo})
		clave = self.gestor.nueva_sesion(agente or "claude", AjustesSesion(carpeta=carpeta or tmp_path, titulo=titulo))
		preparar(clave)
		self.sesion_actual = clave
		return True

	monkeypatch.setattr(type(ventana), "accion_nueva_sesion", nueva)
	# El menú contextual ya ofrece «Continuar aquí» en las importadas.
	ventana.lista_sesiones.enfocar_fila(next(i for i, f in enumerate(ventana._visibles) if f.clave == importada),
		anunciar=False)
	menu = ventana._menu_contextual_sesiones()
	try:
		assert any(i.GetItemLabel().startswith("&Continuar aquí") for i in menu.GetMenuItems())
	finally:
		menu.Destroy()
	assert ventana.continuar_importada(fila) is True
	assert llamadas[0] == {"carpeta": tmp_path, "mensaje": "", "agente": "claude", "titulo": "Informe (continuación)"}
	nueva_clave = ventana.sesion_actual
	assert nueva_clave.startswith("ctrlia:") and ventana.gestor.contexto_pendiente(nueva_clave)
	assert "conversación anterior entera" in anuncios[-1]
	assert len(ventana.almacen.mensajes(importada)) == 2


def test_continuar_importada_con_agente_que_no_esta(ventana, tmp_path, anuncios, monkeypatch):
	importada = _importada(ventana.almacen, proveedor="gemini", carpeta="\\\\servidor\\no-existe")
	fila = _fila_importada(ventana, importada)
	llamadas: list[dict] = []
	monkeypatch.setattr(type(ventana), "accion_nueva_sesion",
		lambda self, **datos: llamadas.append(datos) or False)
	assert ventana.continuar_importada(fila, "hola") is False
	assert llamadas[0]["agente"] == "" and llamadas[0]["carpeta"] is None and llamadas[0]["mensaje"] == "hola"
	assert any("no está en este equipo" in texto for texto in anuncios)


def test_enviar_en_una_importada_ofrece_continuarla(ventana, tmp_path, anuncios, monkeypatch):
	from app.ui import vp_conversacion

	importada = _importada(ventana.almacen, proveedor="claude", carpeta=str(tmp_path))
	_fila_importada(ventana, importada)
	ventana.sesion_actual = importada
	pedidas: list[str] = []
	monkeypatch.setattr(vp_conversacion, "confirmar", lambda *a, **k: wx.ID_YES)
	monkeypatch.setattr(type(ventana), "continuar_importada", lambda self, fila, mensaje="": pedidas.append(mensaje) or True)
	ventana.respuesta.SetValue("Sigue, por favor")
	ventana._enviar_desde_cuadro(False)
	assert pedidas == ["Sigue, por favor"] and ventana.respuesta.GetValue() == ""


# ── 3. Divisor entre los mensajes y la respuesta (#242) ─────────────────────────


def test_proporciones_del_divisor():
	assert vp_disposicion.proporciones("equilibrado", 0, False) == vp_disposicion.REPARTOS["equilibrado"]
	assert vp_disposicion.porcentaje_de_reparto("equilibrado") == 33
	assert vp_disposicion.porcentaje_de_reparto("respuesta") == 57
	sesiones, mensajes, _lectura, respuesta = vp_disposicion.proporciones("equilibrado", 40, False)
	assert (mensajes, respuesta) == (60, 40) and sesiones == 50
	_s, mensajes, lectura, respuesta = vp_disposicion.proporciones("equilibrado", 40, True)
	assert mensajes + lectura == 60 and respuesta == 40
	# Fuera de los límites se ajusta.
	assert vp_disposicion.proporciones("mensajes", 99, False)[3] == vp_disposicion.MAXIMO_RESPUESTA


def test_divisor_con_teclado_se_recuerda_y_lo_deshace_un_reparto(ventana, anuncios):
	assert ventana.porcentaje_respuesta() == 33
	ventana.accion_mas_espacio_respuesta()
	assert ventana.opcion("ventana.porcentaje_respuesta") == 38
	assert anuncios[-1] == "Más espacio para la respuesta: ocupa el 38 % de la conversación."
	assert ventana._sizer_raiz.GetItem(ventana.respuesta).GetProportion() == 38
	ventana.accion_mas_espacio_mensajes()
	ventana.accion_mas_espacio_mensajes()
	assert ventana.opcion("ventana.porcentaje_respuesta") == 28
	assert anuncios[-1].startswith("Más espacio para los mensajes")
	# En el límite lo dice y no cambia.
	ventana.poner_opcion("ventana.porcentaje_respuesta", vp_disposicion.MINIMO_RESPUESTA)
	assert ventana.mover_divisor(-vp_disposicion.PASO_DIVISOR) == vp_disposicion.MINIMO_RESPUESTA
	assert anuncios[-1] == "Ya no se puede dar más espacio a los mensajes."
	# Elegir un reparto lo deshace.
	ventana.accion_reparto("respuesta")
	assert ventana.opcion("ventana.porcentaje_respuesta") == 0
	assert ventana._sizer_raiz.GetItem(ventana.respuesta).GetProportion() == vp_disposicion.REPARTOS["respuesta"][3]


def test_divisor_con_raton_y_sin_foco(ventana, anuncios):
	divisor = ventana.divisor
	assert divisor.AcceptsFocus() is False and divisor.AcceptsFocusFromKeyboard() is False
	assert divisor not in ventana.zonas()
	# Está entre los mensajes (o el panel de lectura) y la etiqueta de la respuesta.
	hijos = [elemento.GetWindow() for elemento in ventana._sizer_raiz.GetChildren()]
	assert hijos.index(ventana.panel_lectura) < hijos.index(divisor) < hijos.index(ventana.etiqueta_respuesta)
	ventana.SetSize(ventana.FromDIP(wx.Size(900, 800)))
	ventana.panel.Layout()
	arriba = ventana.lista_mensajes.GetScreenRect().GetTop()
	abajo = ventana.respuesta.GetScreenRect().GetBottom()
	ventana._arrastrar_divisor(arriba + (abajo - arriba) // 2)
	assert ventana.porcentaje_respuesta() == 50
	assert ventana.opcion("ventana.porcentaje_respuesta") == 0  # aún no se guarda
	ventana._soltar_divisor()
	assert ventana.opcion("ventana.porcentaje_respuesta") == 50 and ventana._porcentaje_arrastre == 0
	ventana._restablecer_divisor()
	assert ventana.opcion("ventana.porcentaje_respuesta") == 0


def test_menu_distribucion_tiene_las_dos_ordenes(ventana):
	etiquetas = {clave: item.GetItemLabel() for clave, item in ventana._menu_disposicion.items()}
	assert etiquetas["divisor_mensajes"].endswith("\tCtrl+Alt+Shift+Down")
	assert etiquetas["divisor_respuesta"].endswith("\tCtrl+Alt+Shift+Up")


# ── 4. Palabras curiosas en la barra de estado (#314) ───────────────────────────


def test_lista_de_palabras():
	assert len(palabras_curiosas.PALABRAS) >= 40 and len(set(palabras_curiosas.PALABRAS)) == len(palabras_curiosas.PALABRAS)
	assert "Cavilando…" in palabras_curiosas.PALABRAS
	for anterior in palabras_curiosas.PALABRAS[:5]:
		assert palabras_curiosas.siguiente(anterior) != anterior


def test_palabras_curiosas_solo_en_la_barra(ventana, tmp_path, anuncios, monkeypatch):
	from app.ui.accesibilidad import voz

	hablado: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *a, **k: hablado.append(texto))
	clave = _sesion(ventana, tmp_path)
	_abrir(ventana, clave)
	barra = ventana.GetStatusBar()
	# Desactivada de fábrica: lo de siempre.
	ventana._al_evento(clave, Evento(TipoEvento.TEXTO_PARCIAL, "Ho"))
	assert barra.GetStatusText(0) == "Claude Code está escribiendo..." or barra.GetStatusText(0).endswith("está escribiendo...")
	ventana._al_evento(clave, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	ventana.poner_opcion("anuncios.palabras_curiosas", True)
	dichos_antes = len(anuncios)
	ventana._al_evento(clave, Evento(TipoEvento.TEXTO_PARCIAL, "Ho"))
	primera = barra.GetStatusText(0)
	agente, _dos_puntos, palabra = primera.partition(": ")
	assert palabra in palabras_curiosas.PALABRAS and ventana._temporizador_palabras.IsRunning()
	assert len(anuncios) == dichos_antes  # nada por voz
	# Alt+4 no dice la palabra curiosa.
	ventana.leer_barra_estado()
	assert hablado and palabra not in hablado[-1] and "está escribiendo" in hablado[-1]
	# Cada pocos segundos cambia.
	ventana._cambiar_palabra_curiosa()
	assert barra.GetStatusText(0) != primera and barra.GetStatusText(0).startswith(agente + ": ")
	# Si la barra ya dice otra cosa (una acción del agente), no la pisa y se para.
	ventana.poner_estado("Leyendo a.txt", 0)
	ventana._cambiar_palabra_curiosa()
	assert barra.GetStatusText(0) == "Leyendo a.txt" and not ventana._temporizador_palabras.IsRunning()
