"""Incidencias de AIChat sobre las listas: cambios legibles (#174), indicadores sin adivinar (#273)
y la página «Decisiones» de las listas de la sesión (#279 y #303).

Sin ventanas visibles (nunca ``Show``, ``ShowModal`` real, ``MainLoop`` ni ``SetFocus``), sin voz y sin
tocar archivos del usuario: la base de datos va en ``tmp_path``.
"""

from __future__ import annotations

import time

import pytest

from app.services import extraccion
from app.services.agentes.base import (
	ClaseOpcion,
	Evento,
	OpcionPermiso,
	Pregunta,
	RespuestaPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.almacen import Almacen, SesionPropia
from app.services.gestor import GestorSesiones, MensajeVista
from app.ui import lectura_listas

AHORA = time.time()
CLAVE = "ctrlia:prueba"


def _opcion(valores: dict | None = None):
	valores = valores or {}
	return lambda clave, defecto=None: valores.get(clave, defecto)


# ── #174: las líneas cambiadas, con palabras ───────────────────────────────────

DIFF = (
	"--- a/x.py\n+++ b/x.py\n"
	"@@ -10,3 +12,3 @@ def foo\n uno\n-dos\n+DOS\n \n"
	"\\ No newline at end of file\n"
	"\n"
	"@@ -1 +1 @@\n-a\n+b"
)


def test_diff_con_palabras_y_numeros_de_linea():
	texto = extraccion.diff_legible(DIFF, "palabras").splitlines()
	assert texto == [
		"Antes: a/x.py",
		"Después: b/x.py",
		"Cambio en las líneas 12 a 14, en def foo:",
		"Línea 12, sin cambios: uno",
		"Línea 13, quitada: dos",
		"Línea 13, añadida: DOS",
		"Línea 14, sin cambios: línea en blanco",
		"Nota: sin salto de línea al final del archivo",
		"",
		"Cambio en la línea 1:",
		"Línea 1, quitada: a",
		"Línea 1, añadida: b",
	]
	# Es la de fábrica.
	assert extraccion.diff_legible(DIFF) == extraccion.diff_legible(DIFF, "palabras")


def test_diff_con_palabras_cortas():
	texto = extraccion.diff_legible(DIFF, "cortas").splitlines()
	assert texto[2:8] == [
		"Líneas 12 a 14, en def foo:", "Igual 12: uno", "Menos 13: dos", "Más 13: DOS", "Igual 14: en blanco",
		"Nota: sin salto de línea al final del archivo",
	]
	assert texto[-3:] == ["Línea 1:", "Menos 1: a", "Más 1: b"]


def test_diff_con_simbolos_es_el_original():
	assert extraccion.diff_legible(DIFF, "simbolos") == DIFF
	assert extraccion.diff_legible("", "palabras") == ""


def test_diff_sin_cabeceras_y_casos_raros():
	assert extraccion.diff_legible("-a\n+b\n c") == "Quitada: a\nAñadida: b\nSin cambios: c"
	assert extraccion.diff_legible("-a\n+b", "cortas") == "Menos: a\nMás: b"
	# Un archivo nuevo: todas añadidas desde la 1; una línea que empieza por «--» dentro del trozo es una quitada.
	nuevo = "--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1,2 @@\n+hola\n+\n"
	assert extraccion.diff_legible(nuevo).splitlines() == [
		"Antes: no existía", "Después: b/n.txt", "Cambio en las líneas 1 a 2:",
		"Línea 1, añadida: hola", "Línea 2, añadida: línea en blanco",
	]
	quitar = "@@ -5,2 +4,0 @@\n---x\n-y"
	assert extraccion.diff_legible(quitar).splitlines() == [
		"Cambio en la línea 5:", "Línea 5, quitada: --x", "Línea 5, quitada: y",
	]
	# Las cabeceras de Git que no son líneas del cambio se quedan como están.
	assert extraccion.diff_legible("diff --git a/x b/x\nindex 1..2\n@@ -1 +1 @@\n-a\n+b").splitlines()[:2] == [
		"diff --git a/x b/x", "index 1..2",
	]


def test_preferencia_del_estilo_de_los_cambios():
	from app.core.configuracion import VALORES_POR_DEFECTO, ErrorValidacion, validar_valor
	from app.ui.dialogo_preferencias import OPCION, PAGINAS

	assert VALORES_POR_DEFECTO["mensajes"]["estilo_cambios"] == "palabras"
	for valor in extraccion.ESTILOS_CAMBIOS:
		validar_valor("mensajes.estilo_cambios", valor)
	with pytest.raises(ErrorValidacion):
		validar_valor("mensajes.estilo_cambios", "colores")
	mensajes = next(p for p in PAGINAS if p.titulo == "Mensajes")
	opcion = next(o for o in mensajes.opciones if o.clave == "mensajes.estilo_cambios")
	assert opcion.tipo == OPCION and [v for v, _t in opcion.opciones] == list(extraccion.ESTILOS_CAMBIOS)
	assert "puntuación" in opcion.ayuda
	# El acelerador no se repite en su página.
	letras = [o.etiqueta.split("&", 1)[1][0].lower() for o in mensajes.opciones if "&" in o.etiqueta]
	assert len(letras) == len(set(letras))


# ── #273: indicadores solo con datos reales ────────────────────────────────────


def _solicitud(tipo: str, estado: str = "pendiente", clase: str = "", identificador: str = "1") -> dict:
	entrada = {"id": identificador, "tipo": tipo, "estado": estado}
	if estado == "respondida":
		entrada["respuesta"] = {"opcion": "x", "etiqueta": "X", "clase": clase}
	return entrada


def test_un_signo_de_interrogacion_ya_no_es_una_pregunta():
	assert lectura_listas.indicadores_mensaje("¿Lo publico?", "asistente", 0, _opcion()) == ""


def test_indicadores_de_preguntas_permisos_y_planes():
	opcion = _opcion()

	def indicadores(*solicitudes) -> str:
		return lectura_listas.indicadores_mensaje("Texto", "asistente", 0, opcion, {"solicitudes": list(solicitudes)})

	assert indicadores(_solicitud("pregunta")) == "Pregunta por responder"
	assert indicadores(_solicitud("pregunta", "respondida", "permitir")) == "Pregunta respondida"
	assert indicadores(_solicitud("pregunta", "respondida", "denegar")) == "Pregunta sin responder"
	assert indicadores(_solicitud("pregunta", "sin_respuesta")) == "Pregunta sin responder"
	assert indicadores(_solicitud("plan")) == "Plan por aprobar"
	assert indicadores(_solicitud("plan", "respondida", "permitir_sesion")) == "Plan aprobado"
	assert indicadores(_solicitud("plan", "respondida", "denegar")) == "Plan rechazado"
	assert indicadores(_solicitud("herramienta", "respondida", "permitir_siempre")) == "Permiso concedido"
	assert indicadores(_solicitud("herramienta", "respondida", "denegar")) == "Permiso denegado"
	assert indicadores(_solicitud("herramienta", "retirada")) == "Permiso retirado"
	assert indicadores(_solicitud("herramienta", "respondida", "permitir", "1"),
		_solicitud("herramienta", "respondida", "denegar", "2")) == "Permisos concedidos y denegados"
	# Lo pendiente manda: es lo que hay que contestar.
	assert indicadores(_solicitud("herramienta", "respondida", "denegar", "1"), _solicitud("herramienta", "pendiente", "", "2")) == (
		"Permiso por responder"
	)
	assert indicadores(_solicitud("pregunta"), _solicitud("herramienta", "respondida", "permitir", "2")) == (
		"Pregunta por responder, Permiso concedido"
	)


def test_indicadores_de_error_detenido_y_adjuntos():
	opcion = _opcion()
	error = lectura_listas.indicadores_mensaje("Ejecutar orden: pytest", "herramienta", 0, opcion, {},
		"pytest\n\nError:\nfallo")
	assert error == "Con error"
	bien = lectura_listas.indicadores_mensaje("Ejecutar orden: pytest", "herramienta", 0, opcion, {},
		"pytest\n\nResultado:\nok")
	assert bien == ""
	assert lectura_listas.indicadores_mensaje("Hola", "asistente", 0, opcion, {"detenido": True}) == "Detenido"
	assert lectura_listas.indicadores_mensaje("Mira", "usuario", 0, opcion, {"adjuntos": ["a.png", "b.txt"]}) == "2 adjuntos"
	assert lectura_listas.indicadores_mensaje("Mira", "usuario", 0, opcion, {"adjuntos": ["a.png"]}) == "1 adjunto"


def test_cada_indicador_nuevo_se_puede_apagar():
	datos = {"solicitudes": [_solicitud("pregunta"), _solicitud("plan", identificador="2")], "detenido": True,
		"adjuntos": ["a"]}
	apagados = _opcion({"lectura.indicador_pregunta": False, "lectura.indicador_permiso": False,
		"lectura.indicador_detenido": False, "lectura.indicador_adjuntos": False})
	assert lectura_listas.indicadores_mensaje("x", "asistente", 0, apagados, datos) == ""
	assert lectura_listas.indicadores_mensaje("x", "asistente", 0, _opcion({"lectura.indicadores": False}), datos) == ""
	from app.core.configuracion import VALORES_POR_DEFECTO
	from app.ui.dialogo_preferencias import PAGINAS

	claves = {o.clave for p in PAGINAS for o in p.opciones}
	for indicador in lectura_listas.INDICADORES:
		assert VALORES_POR_DEFECTO["lectura"]["indicador_" + indicador] is True
		assert "lectura.indicador_" + indicador in claves


# ── Lo que guarda el gestor de cada petición ───────────────────────────────────


@pytest.fixture
def gestor(tmp_path):
	from tests.test_gestor import ProveedorFalso

	almacen = Almacen(tmp_path / "pruebas.db")
	almacen.guardar_sesion(SesionPropia(CLAVE, "falso", "Prueba", str(tmp_path)))
	gestor = GestorSesiones(almacen, {"falso": ProveedorFalso()}, lambda clave, evento: None)
	yield gestor
	almacen.cerrar()


def _pregunta(identificador: str = "p1") -> SolicitudPermiso:
	return SolicitudPermiso(
		identificador, TipoSolicitud.PREGUNTA, "Claude te hace una pregunta", herramienta="AskUserQuestion",
		preguntas=[Pregunta("¿Qué base de datos?", "¿Qué base de datos?", "Base", [("SQLite", ""), ("Postgres", "")])],
		opciones=[OpcionPermiso("responder", "Responder", ClaseOpcion.PERMITIR),
			OpcionPermiso("no_responder", "No responder", ClaseOpcion.DENEGAR)],
	)


def _plan(identificador: str = "plan1") -> SolicitudPermiso:
	return SolicitudPermiso(
		identificador, TipoSolicitud.PLAN, "Claude te propone un plan", plan="# Plan\n1. Leer\n2. Cambiar",
		opciones=[OpcionPermiso("aprobar", "Aprobar y preguntar cada cambio", ClaseOpcion.PERMITIR),
			OpcionPermiso("seguir", "Seguir planificando", ClaseOpcion.DENEGAR)],
	)


def _permiso(identificador: str = "h1") -> SolicitudPermiso:
	return SolicitudPermiso(identificador, TipoSolicitud.HERRAMIENTA, "Ejecutar: rm x", herramienta="Bash",
		opciones=[OpcionPermiso("permitir", "Permitir una vez", ClaseOpcion.PERMITIR),
			OpcionPermiso("denegar", "Denegar", ClaseOpcion.DENEGAR)])


def _datos(gestor, id_mensaje: int) -> dict:
	return gestor.almacen.mensaje(id_mensaje).datos


def test_la_peticion_se_apunta_en_la_respuesta_del_agente_y_luego_la_respuesta(gestor):
	almacen = gestor.almacen
	almacen.anadir_mensaje(CLAVE, "usuario", "¿Con qué base empiezo?")
	agente = almacen.anadir_mensaje(CLAVE, "asistente", "Antes de seguir, una duda.")
	almacen.anadir_mensaje(CLAVE, "herramienta", "Preguntar al usuario", herramienta="AskUserQuestion")
	evento = Evento(TipoEvento.PERMISO, "Claude te hace una pregunta", {"solicitud": _pregunta()})
	gestor._recibir(CLAVE, evento)
	assert evento.datos["id_mensaje"] == agente  # la interfaz refresca esa fila
	entrada = _datos(gestor, agente)["solicitudes"][0]
	assert (entrada["tipo"], entrada["estado"], entrada["preguntas"][0]["opciones"]) == (
		"pregunta", "pendiente", ["SQLite", "Postgres"])
	# Mientras espera, el indicador lo dice.
	vista = next(m for m in gestor.mensajes(CLAVE) if m.id == agente)
	assert lectura_listas.indicadores_mensaje(vista.texto, vista.rol, 0, _opcion(), vista.datos) == "Pregunta por responder"
	# La misma petición repetida se apunta una vez.
	gestor._recibir(CLAVE, Evento(TipoEvento.PERMISO, "", {"solicitud": _pregunta()}))
	assert len(_datos(gestor, agente)["solicitudes"]) == 1
	# Al responder se guarda lo elegido y se devuelve el mensaje para refrescarlo.
	assert gestor.responder(CLAVE, "p1", RespuestaPermiso("responder", respuestas={"¿Qué base de datos?": ["SQLite"]})) == agente
	entrada = _datos(gestor, agente)["solicitudes"][0]
	assert entrada["estado"] == "respondida"
	assert entrada["respuesta"] == {"opcion": "responder", "etiqueta": "Responder", "clase": "permitir", "mensaje": "",
		"respuestas": {"¿Qué base de datos?": ["SQLite"]}}
	assert gestor.solicitudes(CLAVE) == []
	vista = next(m for m in gestor.mensajes(CLAVE) if m.id == agente)
	assert lectura_listas.indicadores_mensaje(vista.texto, vista.rol, 0, _opcion(), vista.datos) == "Pregunta respondida"


def test_sin_respuesta_del_agente_se_apunta_en_tu_mensaje(gestor):
	almacen = gestor.almacen
	almacen.anadir_mensaje(CLAVE, "asistente", "Respuesta de un turno anterior")
	tuyo = almacen.anadir_mensaje(CLAVE, "usuario", "Borra los temporales")
	almacen.anadir_mensaje(CLAVE, "herramienta", "Ejecutar: rm x", herramienta="Bash")
	gestor._recibir(CLAVE, Evento(TipoEvento.PERMISO, "", {"solicitud": _permiso()}))
	assert _datos(gestor, tuyo)["solicitudes"][0]["tipo"] == "herramienta"
	# El agente la retira (por ejemplo, la cubre una regla recién concedida).
	retirada = Evento(TipoEvento.PERMISO_RETIRADO, "", {"id": "h1"})
	gestor._recibir(CLAVE, retirada)
	assert retirada.datos["id_mensaje"] == tuyo
	assert _datos(gestor, tuyo)["solicitudes"][0]["estado"] == "retirada"


def test_una_peticion_que_nadie_espera_ya_sale_sin_respuesta(gestor):
	agente = gestor.almacen.anadir_mensaje(CLAVE, "asistente", "¿Sigo?")
	gestor._recibir(CLAVE, Evento(TipoEvento.PERMISO, "", {"solicitud": _plan()}))
	# Como si Ctrl+IA se hubiera cerrado sin responder: el agente ya no la espera.
	gestor._solicitudes.clear()
	vista = next(m for m in gestor.mensajes(CLAVE) if m.id == agente)
	assert vista.datos["solicitudes"][0]["estado"] == "sin_respuesta"
	assert lectura_listas.indicadores_mensaje(vista.texto, vista.rol, 0, _opcion(), vista.datos) == "Plan sin respuesta"
	# Lo guardado no cambia: solo se presenta así.
	assert _datos(gestor, agente)["solicitudes"][0]["estado"] == "pendiente"


def test_el_turno_detenido_queda_marcado(gestor):
	gestor.almacen.anadir_mensaje(CLAVE, "usuario", "Hazlo")
	agente = gestor.almacen.anadir_mensaje(CLAVE, "asistente", "Empiezo...")
	gestor._recibir(CLAVE, Evento(TipoEvento.FIN_TURNO, "", {"correcto": False, "interrumpido": True}))
	assert _datos(gestor, agente)["detenido"] is True
	otro = gestor.almacen.anadir_mensaje(CLAVE, "asistente", "Listo")
	gestor._recibir(CLAVE, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True, "interrumpido": False}))
	assert "detenido" not in _datos(gestor, otro)


def test_las_sesiones_ajenas_no_se_tocan(gestor):
	assert gestor._apuntar_solicitud("claude:ajena", _pregunta()) is None
	assert gestor.responder("claude:ajena", "nada", RespuestaPermiso("x")) is None


def test_la_vista_conserva_los_adjuntos(gestor):
	gestor.almacen.anadir_mensaje(CLAVE, "usuario", "Mira", datos={"adjuntos": ["C:/a.png"]})
	vista = gestor.mensajes(CLAVE)[0]
	assert vista.datos["adjuntos"] == ["C:/a.png"]
	# Los datos no cuentan al comparar mensajes.
	assert vista == MensajeVista(vista.rol, vista.texto, vista.fecha, vista.detalle, vista.herramienta, vista.id, [])


# ── #279 y #303: decisiones ────────────────────────────────────────────────────


def _con_decisiones(gestor) -> list[MensajeVista]:
	almacen = gestor.almacen
	almacen.anadir_mensaje(CLAVE, "usuario", "Empieza")
	almacen.anadir_mensaje(CLAVE, "asistente", "Una duda")
	gestor._recibir(CLAVE, Evento(TipoEvento.PERMISO, "", {"solicitud": _pregunta()}))
	gestor.responder(CLAVE, "p1", RespuestaPermiso("responder", mensaje="Y con índices",
		respuestas={"¿Qué base de datos?": ["SQLite"]}))
	gestor._recibir(CLAVE, Evento(TipoEvento.PERMISO, "", {"solicitud": _permiso()}))
	gestor.responder(CLAVE, "h1", RespuestaPermiso("permitir"))
	almacen.anadir_mensaje(CLAVE, "usuario", "Ahora el plan")
	almacen.anadir_mensaje(CLAVE, "asistente", "Mi propuesta")
	gestor._recibir(CLAVE, Evento(TipoEvento.PERMISO, "", {"solicitud": _plan()}))
	gestor.responder(CLAVE, "plan1", RespuestaPermiso("seguir"))
	return gestor.mensajes(CLAVE)


def test_decisiones_de_preguntas_y_planes(gestor):
	lista = extraccion.decisiones(_con_decisiones(gestor))
	assert [(d.tipo, d.pregunta, d.respuesta, d.estado, d.indice) for d in lista] == [
		("pregunta", "¿Qué base de datos?", "SQLite", "respondida", 1),
		("plan", "Plan", "Seguir planificando", "respondida", 3),
	]
	pregunta, plan = lista
	assert "Opciones: SQLite; Postgres." in pregunta.documento
	assert "Tu respuesta: SQLite." in pregunta.documento
	assert "## Lo que le escribiste\n\nY con índices" in pregunta.documento
	assert "1. Leer\n2. Cambiar" in plan.documento and "Seguir planificando." in plan.documento
	# Los permisos para herramientas no son decisiones; lo que no se entiende se salta.
	raros = [MensajeVista("asistente", "x", AHORA, datos={"solicitudes": [
		"basura", {"tipo": "pregunta", "preguntas": 3}, {"tipo": "herramienta", "estado": "pendiente"}]})]
	assert extraccion.decisiones(raros) == []


def test_decision_pendiente_y_no_respondida():
	pendiente = {"id": "1", "tipo": "pregunta", "estado": "pendiente", "titulo": "Pregunta",
		"preguntas": [{"id": "q", "texto": "¿Sí o no?", "opciones": ["Sí", "No"]}]}
	negada = dict(pendiente, id="2", estado="respondida",
		respuesta={"opcion": "no_responder", "etiqueta": "No responder", "clase": "denegar"})
	lista = extraccion.decisiones([MensajeVista("asistente", "x", AHORA, datos={"solicitudes": [pendiente, negada]})])
	assert [d.respuesta for d in lista] == ["Por responder", "No la respondiste (No responder)"]


# ── El diálogo de listas ───────────────────────────────────────────────────────

wx = pytest.importorskip("wx")


@pytest.fixture
def voz_silenciada(monkeypatch):
	from app.ui.accesibilidad import voz

	dichos: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *args, **kwargs: dichos.append(texto))
	return dichos


@pytest.fixture
def documentos(monkeypatch):
	from app.ui.dialogos import listas

	vistos: list[tuple[str, str]] = []
	monkeypatch.setattr(listas, "mostrar_documento", lambda padre, titulo, texto, forzar_texto=False: vistos.append((titulo, texto)))
	return vistos


def test_pagina_decisiones_en_el_dialogo(app_wx, gestor, voz_silenciada, documentos, monkeypatch):
	from app.ui.dialogos import listas
	from tests.ayudas_wx import procesar_eventos, simular_tecla

	copiados: list[str] = []
	monkeypatch.setattr(listas, "copiar_al_portapapeles", lambda texto: copiados.append(texto) or True)
	mensajes = _con_decisiones(gestor)
	dialogo = listas.DialogoListas(None, mensajes, "Prueba", carpeta="", editor="", propia=True, transcritos=[])
	try:
		assert listas.PAGINAS[7] == "decisiones" and dialogo.libro.GetPageCount() == len(listas.PAGINAS)
		assert dialogo.libro.GetPageText(7) == "Decisiones"
		assert dialogo.etiquetas["decisiones"].GetLabel() == "&Decisiones (2):"
		# La etiqueta se crea justo antes de su lista y la lista tiene ayuda (Ctrl+H).
		lista = dialogo.listas["decisiones"]
		hermanos = list(lista.GetParent().GetChildren())
		assert hermanos[hermanos.index(lista) - 1] is dialogo.etiquetas["decisiones"]
		assert "Alt+Intro" in lista.GetHelpText()
		# Ningún acelerador de etiqueta se repite en el diálogo.
		letras = [e.GetLabel().split("&", 1)[1][0].lower() for e in dialogo.etiquetas.values()]
		letras.append(dialogo.boton_cerrar.GetLabel().split("&", 1)[1][0].lower())
		assert len(letras) == len(set(letras))
		assert lista.valores_fila(0) == ["¿Qué base de datos?", "Pregunta", "SQLite"]
		assert lista.valores_fila(1) == ["Plan", "Plan", "Seguir planificando"]
		# Ctrl+8 lleva a la página.
		assert simular_tecla(dialogo, "8", ctrl=True).gestionado
		assert dialogo.pagina_actual() == "decisiones"
		dialogo.activar()
		assert documentos[-1][0] == "Pregunta 1" and "Tu respuesta: SQLite." in documentos[-1][1]
		assert dialogo.copiar() is True
		assert copiados[-1] == "¿Qué base de datos?\nTu respuesta: SQLite"
		menu = dialogo._crear_menu("decisiones")
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		letras_menu = [e.split("&", 1)[1][0].lower() for e in etiquetas]
		assert len(letras_menu) == len(set(letras_menu)) and all("\t" in e for e in etiquetas)
		menu.Destroy()
		assert listas.contar(mensajes)["decisiones"] == 2
		# Alt+Intro va al mensaje de la decisión.
		dialogo.ir_al_mensaje()
		assert dialogo.indice_mensaje == 1
	finally:
		dialogo.Destroy()
		procesar_eventos()


def test_pagina_decisiones_vacia_lo_explica(app_wx, voz_silenciada):
	from app.ui.dialogos import listas
	from tests.ayudas_wx import procesar_eventos

	dialogo = listas.DialogoListas(None, [MensajeVista("asistente", "Hola", AHORA)], "", pestana="decisiones",
		carpeta="", editor="", propia=False, transcritos=[])
	try:
		assert dialogo.pagina_actual() == "decisiones"
		dialogo._anunciar_si_vacia()
		assert voz_silenciada[-1].startswith("No hay decisiones guardadas")
	finally:
		dialogo.Destroy()
		procesar_eventos()


@pytest.mark.parametrize(("estilo", "esperado"), [
	("palabras", "Línea 1, quitada: a\nLínea 1, añadida: b"),
	("cortas", "Menos 1: a\nMás 1: b"),
	("simbolos", "```diff\n@@ -1 +1 @@\n-a\n+b\n```"),
])
def test_los_cambios_se_leen_con_el_estilo_elegido(app_wx, voz_silenciada, documentos, monkeypatch, tmp_path, estilo, esperado):
	from app.ui.dialogos import listas
	from tests.ayudas_wx import procesar_eventos

	diff = "@@ -1 +1 @@\n-a\n+b"
	mensajes = [MensajeVista("cambio", "Archivo modificado: x.py", AHORA, diff, "", 1, [{"ruta": "x.py", "diff": diff}])]
	dialogo = listas.DialogoListas(None, mensajes, "", pestana="archivos", carpeta=str(tmp_path), editor="", propia=True,
		transcritos=[], estilo_cambios=estilo)
	try:
		dialogo.activar()
		assert esperado in documentos[-1][1]
		# Guardar los cambios como archivo siempre guarda el diff original.
		monkeypatch.setattr(dialogo, "_pedir_ruta_guardar", lambda sugerido, comodin: tmp_path / sugerido)
		ruta = dialogo.guardar()
		assert ruta is not None and ruta.read_text(encoding="utf-8") == diff + "\n"
	finally:
		dialogo.Destroy()
		procesar_eventos()


def test_el_estilo_sale_de_preferencias_de_la_ventana(app_wx, voz_silenciada):
	from app.ui.dialogos import listas

	class Ventana:
		def opcion(self, clave, defecto=None):
			return {"mensajes.estilo_cambios": "cortas"}.get(clave, defecto)

	assert listas._estilo_cambios(Ventana()) == "cortas"
	assert listas._estilo_cambios(None) == "palabras"

	class Rara:
		def opcion(self, clave, defecto=None):
			return "inventado"

	assert listas._estilo_cambios(Rara()) == "palabras"
