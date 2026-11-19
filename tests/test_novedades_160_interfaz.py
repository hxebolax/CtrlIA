"""Novedades de la 1.6.0 en la interfaz y el gestor.

Actividad en segundo plano, voz sin Markdown y por frases, preguntas escritas en la
respuesta, bifurcar o reintentar desde un mensaje, aprobaciones automáticas como
rutina, detalle técnico de los errores, limpieza de restos y vistas de la lista de
mensajes. Nada se muestra ni habla.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from types import SimpleNamespace

import pytest

from app.services import actividad, frases, limpieza, preguntas_escritas
from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	Capacidades,
	Conversacion,
	Estado,
	EstadoProveedor,
	Proveedor,
	RespuestaPermiso,
	TipoEvento,
)
from app.services.almacen import Almacen
from app.services.gestor import GestorSesiones, MensajeVista, es_de_herramientas

# ── Actividad en segundo plano ────────────────────────────────────────────────


def test_la_actividad_acumula_por_tarea_y_avisa_al_empezar_y_terminar():
	registro = actividad.RegistroActividad()
	cambio = registro.aplicar("s", {"id": "t1", "clase": "subagente", "estado": "en_marcha", "titulo": "contar archivos",
		"tipo_agente": "general-purpose", "encargo": "Cuenta los .py", "detenible": True})
	assert cambio.empezada and not cambio.terminada
	cambio = registro.aplicar("s", {"id": "t1", "paso": "Lee x.py", "paso_nuevo": "Lee x.py", "usos_herramientas": 3,
		"tokens": 1200})
	assert not cambio.empezada and not cambio.terminada
	tarea = registro.tarea("s", "t1")
	assert tarea.paso == "Lee x.py" and tarea.pasos == ["Lee x.py"] and tarea.usos_herramientas == 3
	assert "contar archivos" in tarea.fila() and "3 usos de herramientas" in tarea.fila() and "Lee x.py" in tarea.fila()
	assert "Encargo:" in tarea.detalle() and "Cuenta los .py" in tarea.detalle()
	assert registro.resumen("s").startswith("En marcha 1 subagente")
	cambio = registro.aplicar("s", {"id": "t1", "estado": "completada", "resumen": "Hay 40 archivos."})
	assert cambio.terminada
	assert registro.resumen("s") == ""
	assert registro.aplicar("s", {"paso": "sin id"}) is None


def test_la_actividad_ordena_y_recorta_y_cierra_lo_que_seguia():
	registro = actividad.RegistroActividad()
	registro.aplicar("s", {"id": "orden", "clase": "orden", "orden": "npm test", "salida": "a" * 10})
	registro.aplicar("s", {"id": "orden", "salida": "b" * actividad.MAX_SALIDA})
	assert len(registro.tarea("s", "orden").salida) == actividad.MAX_SALIDA
	for numero in range(actividad.MAX_TERMINADAS + 5):
		registro.aplicar("s", {"id": f"v{numero}", "estado": "en_marcha"})
		registro.aplicar("s", {"id": f"v{numero}", "estado": "completada"})
	tareas = registro.tareas("s")
	assert tareas[0].id == "orden"  # lo que sigue en marcha, primero
	assert len([t for t in tareas if not t.en_marcha]) == actividad.MAX_TERMINADAS
	cerradas = registro.terminar_todas("s")
	assert [t.id for t in cerradas] == ["orden"] and registro.tarea("s", "orden").estado == "detenida"


# ── La voz ────────────────────────────────────────────────────────────────────


def test_para_voz_quita_las_marcas_de_markdown():
	texto = "# Título\n\n**Negrita** y [enlace](http://x.es) con `código`\n\n| a | b |\n|---|---|\n| 1 | 2 |\n- uno\n> cita"
	assert frases.para_voz(texto) == "Título\n\nNegrita y enlace con código\n\na, b\n1, 2\nuno\ncita"


def test_para_voz_lee_o_avisa_los_bloques_de_codigo():
	texto = "Mira:\n```python\nx = 1\n```\nYa."
	assert frases.para_voz(texto) == "Mira:\nx = 1\nYa."
	assert frases.para_voz(texto, avisar_codigo=True) == "Mira:\nBloque de código.\n\nYa."


def _leer(trozos: list[str], avisar: bool = False) -> list[str]:
	lector = frases.LectorFrases(avisar)
	dicho: list[str] = []
	for trozo in trozos:
		dicho += lector.anadir(trozo)
	resto = lector.terminar()
	return dicho + ([resto] if resto else [])


def test_por_frases_nunca_corta_media_palabra_ni_una_abreviatura():
	dicho = _leer(["Voy a revisar x.", "py y luego etc. ", "más cosas. Segunda", " frase! ¿Seguro? Sí."])
	assert dicho == ["Voy a revisar x.py y luego etc. más cosas.", "Segunda frase!", "¿Seguro?", "Sí."]


def test_por_frases_espera_a_que_se_cierre_un_bloque_de_codigo():
	dicho = _leer(["Lista:\n1. Uno\n```py\nx = 1\n", "y = 2\n```\nFin. Y algo", " más"], avisar=True)
	assert dicho == ["Lista:", "1. Uno", "Bloque de código.", "Fin.", "Y algo más"]


def test_por_frases_sabe_lo_que_ha_soltado():
	lector = frases.LectorFrases()
	lector.anadir("Hola. Adi")
	assert lector.consumido == len("Hola.")
	lector.callar()
	assert lector.consumido == len("Hola. Adi") and lector.terminar() == ""


def test_lo_dictado_se_calcula_por_diferencia():
	from app.ui.vp_conversacion import _insertado

	assert _insertado("Hola mundo", "Hola querido mundo") == "querido "
	assert _insertado("", "dictado") == "dictado"
	assert _insertado("abc", "abc") == ""


# ── Preguntas escritas en la respuesta ────────────────────────────────────────


def test_la_pregunta_del_final_se_toma_y_los_ofrecimientos_no():
	assert preguntas_escritas.pregunta_final("He mirado el código. ¿Qué base de datos prefieres?") == "¿Qué base de datos prefieres?"
	assert preguntas_escritas.pregunta_final("Hecho. ¿Quieres que ejecute las pruebas?") == ""
	assert preguntas_escritas.pregunta_final("Listo. Want me to run the tests?") == ""
	assert preguntas_escritas.pregunta_final("¿Quieres que lo haga con SQLite o con PostgreSQL?") != ""
	assert preguntas_escritas.pregunta_final("¿Algo más?") == ""
	assert preguntas_escritas.pregunta_final("Todo listo.") == ""
	lejos = "¿Empezamos? " + "x" * (preguntas_escritas.MAX_DESPUES + 10)
	assert preguntas_escritas.pregunta_final(lejos) == ""
	# Lo de dentro de un bloque de código no cuenta.
	assert preguntas_escritas.pregunta_final("Mira:\n```\nif x?\n```\n") == ""


def test_el_bloque_questions_gana_y_da_varias_preguntas():
	texto = ("Necesito saber dos cosas.\n```questions\n[{\"question\": \"¿Qué base?\", \"header\": \"Base\", \"options\": "
		"[{\"label\": \"SQLite\", \"description\": \"local\"}, {\"label\": \"PostgreSQL\"}]}, {\"question\": \"¿Pruebas?\", "
		"\"multiSelect\": true, \"options\": [\"unitarias\", \"de interfaz\"]}]\n```\n")
	solicitud = preguntas_escritas.solicitud_de(texto)
	assert solicitud is not None and len(solicitud.preguntas) == 2
	assert solicitud.preguntas[0].opciones == [("SQLite", "local"), ("PostgreSQL", "")]
	assert solicitud.preguntas[1].multiple
	assert solicitud.opcion_segura().id == "no_responder"
	respuesta = preguntas_escritas.texto_respuesta(solicitud,
		{"escrita-0": ["SQLite"], "escrita-1": ["unitarias", "de interfaz"]}, "Y rápido.")
	assert respuesta == "¿Qué base?\nSQLite\n\n¿Pruebas?\nunitarias, de interfaz\n\nY rápido."


# ── El gestor ─────────────────────────────────────────────────────────────────


class ConversacionPrueba(Conversacion):
	def __init__(self, ajustes, al_evento):
		super().__init__(ajustes, al_evento)
		self.enviados: list[str] = []
		self.respuestas: list[str] = []
		self.compactada = False
		self.detenidas: list[str] = []
		self.final = "He terminado."

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		self.enviados.append(texto)
		self.ajustes.id_externo = self.ajustes.id_externo or "ext-1"
		self._emitir(TipoEvento.INICIADO, id_externo=self.ajustes.id_externo)
		self._cambiar_estado(Estado.TRABAJANDO)
		self._emitir(TipoEvento.MENSAJE, self.final, id=f"m{len(self.enviados)}", ancla=f"uuid-{len(self.enviados)}")
		self._emitir(TipoEvento.FIN_TURNO, correcto=True, interrumpido=False)
		self._cambiar_estado(Estado.INACTIVA)

	def detener(self) -> None:
		pass

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		self.respuestas.append(solicitud_id)

	def cerrar(self) -> None:
		pass

	def compactar(self) -> None:
		self.compactada = True

	def detener_tarea(self, id_tarea: str) -> None:
		self.detenidas.append(id_tarea)


class ProveedorPrueba(Proveedor):
	id = "prueba"
	nombre = "Agente de prueba"
	capacidades = Capacidades(compactar=True, detener_tareas=True, bifurcar_en_mensaje=True, preguntar_aparte=True)

	def __init__(self):
		self.conversaciones: list[ConversacionPrueba] = []
		self.preguntas: list[tuple[AjustesSesion, str]] = []

	def detectar(self) -> EstadoProveedor:
		return EstadoProveedor(True)

	def crear_conversacion(self, ajustes, al_evento):
		conversacion = ConversacionPrueba(ajustes, al_evento)
		self.conversaciones.append(conversacion)
		return conversacion

	def preguntar_aparte(self, ajustes, pregunta, al_texto=None, cancelar=None):
		self.preguntas.append((ajustes, pregunta))
		if al_texto:
			al_texto("Res")
		return "Respuesta aparte"


@pytest.fixture
def entorno(tmp_path):
	almacen = Almacen(tmp_path / "g.db")
	proveedor = ProveedorPrueba()
	eventos: list = []
	opciones = {"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False,
		"agentes.puntos_restauracion": False}
	gestor = GestorSesiones(almacen, {"prueba": proveedor}, lambda c, e: eventos.append((c, e)),
		lambda clave, defecto: opciones.get(clave, defecto))
	yield SimpleNamespace(gestor=gestor, proveedor=proveedor, eventos=eventos, carpeta=tmp_path, opciones=opciones)
	almacen.cerrar()


def test_la_pregunta_escrita_deja_la_sesion_esperando_y_se_contesta_como_mensaje(entorno):
	gestor = entorno.gestor
	clave = gestor.nueva_sesion("prueba", AjustesSesion(carpeta=entorno.carpeta))
	conversacion = gestor.conversacion(clave)
	conversacion.final = "He visto dos opciones. ¿Cuál prefieres, la rápida o la segura?"
	gestor.enviar(clave, "Mira esto")
	pendientes = gestor.solicitudes(clave)
	assert len(pendientes) == 1
	solicitud = pendientes[0][1]
	assert solicitud.datos.get("pregunta_escrita")
	assert any(e.tipo is TipoEvento.PERMISO and e.datos.get("pregunta_escrita") for _c, e in entorno.eventos)
	assert [f for f in gestor.listar() if f.clave == clave][0].estado == Estado.ESPERANDO_USUARIO
	gestor.responder(clave, solicitud.id, RespuestaPermiso("responder", respuestas={"escrita-0": ["La segura"]}))
	assert conversacion.respuestas == []  # nunca se contesta al agente como si lo hubiera preguntado él
	assert gestor.solicitudes(clave) == []


def test_la_pregunta_escrita_se_retira_al_escribir_otra_cosa_y_respeta_la_opcion(entorno):
	gestor = entorno.gestor
	clave = gestor.nueva_sesion("prueba", AjustesSesion(carpeta=entorno.carpeta))
	conversacion = gestor.conversacion(clave)
	conversacion.final = "¿Lo dejo así?"
	gestor.enviar(clave, "Uno")
	assert gestor.solicitudes(clave)
	conversacion.final = "Vale."
	gestor.enviar(clave, "Otra cosa")
	assert gestor.solicitudes(clave) == []
	entorno.opciones["agentes.preguntas_escritas"] = False
	conversacion.final = "¿Lo dejo así?"
	gestor.enviar(clave, "Tres")
	assert gestor.solicitudes(clave) == []


def test_el_gestor_guarda_ancla_detalle_tecnico_modo_y_actividad(entorno):
	gestor = entorno.gestor
	clave = gestor.nueva_sesion("prueba", AjustesSesion(carpeta=entorno.carpeta, modo_permiso="default"))
	gestor.enviar(clave, "Hola")
	conversacion = gestor.conversacion(clave)
	conversacion._emitir(TipoEvento.ERROR, "Algo falló", detalle_tecnico='{"subtype":"error_max_turns"}')  # noqa: SLF001
	conversacion._emitir(TipoEvento.AVISO, "Claude ha pasado al modo plan.", modo_cambiado="plan")  # noqa: SLF001
	conversacion._emitir(TipoEvento.ACTIVIDAD, id="t1", clase="subagente", estado="en_marcha", titulo="x")  # noqa: SLF001
	mensajes = gestor.mensajes(clave)
	asistente = next(m for m in mensajes if m.rol == "asistente")
	assert asistente.datos.get("ancla") == "uuid-1"
	error = next(m for m in mensajes if m.rol == "error")
	assert "error_max_turns" in error.datos["detalle_tecnico"]
	assert gestor.almacen.sesion(clave).modo_permiso == "plan"
	assert gestor.actividad.en_marcha(clave)[0].titulo == "x"
	assert any(e.tipo is TipoEvento.ACTIVIDAD and e.datos.get("_cambio") is not None for _c, e in entorno.eventos)
	gestor.cerrar_conversacion(clave)
	assert gestor.actividad.en_marcha(clave) == []


def test_compactar_detener_tarea_y_preguntar_aparte(entorno):
	gestor = entorno.gestor
	clave = gestor.nueva_sesion("prueba", AjustesSesion(carpeta=entorno.carpeta))
	with pytest.raises(ValueError, match="no ha empezado"):
		gestor.compactar(clave)
	with pytest.raises(ValueError, match="ninguna respuesta"):
		gestor.preguntar_aparte(clave, "¿Qué hiciste?")
	gestor.enviar(clave, "Hola")
	gestor.compactar(clave)
	assert gestor.conversacion(clave).compactada
	gestor.detener_tarea(clave, "t9")
	assert gestor.conversacion(clave).detenidas == ["t9"]
	trozos: list[str] = []
	assert gestor.preguntar_aparte(clave, "¿Qué hiciste?", trozos.append) == "Respuesta aparte"
	ajustes, pregunta = entorno.proveedor.preguntas[0]
	assert ajustes.id_externo == "ext-1" and pregunta == "¿Qué hiciste?" and trozos == ["Res"]


def test_bifurcar_y_reintentar_desde_un_mensaje(entorno):
	gestor = entorno.gestor
	clave = gestor.nueva_sesion("prueba", AjustesSesion(carpeta=entorno.carpeta, titulo="Original", modo_permiso="default"))
	gestor.enviar(clave, "Primero")
	gestor.enviar(clave, "Segundo")
	mensajes = gestor.mensajes(clave)
	assert [m.rol for m in mensajes] == ["usuario", "asistente", "usuario", "asistente"]
	nueva = gestor.bifurcar_desde_mensaje(clave, mensajes, 1, "bifurcar")
	sesion = gestor.almacen.sesion(nueva)
	assert sesion.bifurcada_de == "ext-1" and sesion.extra["cortar_en"] == {"modo": "bifurcar", "ancla": "uuid-1"}
	assert sesion.titulo == "Original (bifurcada)"
	assert [m.texto for m in gestor.mensajes(nueva)] == ["Primero", "He terminado."]
	with pytest.raises(ValueError, match="mensaje tuyo"):
		gestor.bifurcar_desde_mensaje(clave, mensajes, 1, "reintentar")
	with pytest.raises(ValueError, match="marca"):
		gestor.bifurcar_desde_mensaje(clave, mensajes, 2, "reintentar")  # sin uuid propio ni del agente
	# Codex: tu mensaje sin ancla vale con la de lo que te contestó en ese turno.
	con_ancla = [MensajeVista("usuario", "Hola", 1.0), MensajeVista("asistente", "Ya", 2.0, datos={"ancla": "turno:item"})]
	assert GestorSesiones.ancla_para(con_ancla, 0, "reintentar", "codex") == "turno:item"
	assert GestorSesiones.ancla_para(con_ancla, 0, "reintentar", "claude") == ""


def test_lo_que_codex_permite_solo_se_ve_con_las_herramientas():
	permitido = {"revision_automatica": {"estado": "approved"}}
	assert es_de_herramientas("sistema", permitido)
	assert not es_de_herramientas("sistema", {"revision_automatica": {"estado": "denied"}})
	assert es_de_herramientas("herramienta", {}) and not es_de_herramientas("asistente", {})


# ── Restos en data/temp ───────────────────────────────────────────────────────


def test_la_limpieza_solo_borra_restos_viejos_de_ctrlia(tmp_path):
	viejo = tmp_path / "copia-abierta-abc"
	(viejo / "sub").mkdir(parents=True)
	solo_lectura = viejo / "sub" / "a.txt"
	solo_lectura.write_text("chat", encoding="utf-8")
	os.chmod(solo_lectura, 0o444)
	reciente = tmp_path / "restaurar-xyz"
	reciente.mkdir()
	ajeno = tmp_path / "imagenes"
	ajeno.mkdir()
	antes = time.time() - 3600
	os.utime(viejo, (antes, antes))
	assert limpieza.limpiar(tmp_path) == 1
	assert not viejo.exists() and reciente.exists() and ajeno.exists()


# ── Páginas y vistas ──────────────────────────────────────────────────────────


def test_la_conversacion_como_pagina_baja_los_encabezados_salvo_en_el_codigo():
	from app.ui.vp_avanzado import bajar_encabezados_md, pagina_conversacion

	assert bajar_encabezados_md("# Uno\n```\n# no\n```\n## Dos") == "### Uno\n```\n# no\n```\n#### Dos"
	pagina = pagina_conversacion("Mi sesión", [MensajeVista("usuario", "Hola", 0.0),
		MensajeVista("error", "Falló", 0.0, datos={"detalle_tecnico": "x"})], lambda m: m.nombre_rol)
	assert pagina.startswith("# Mi sesión") and "## 1. " in pagina and "## 2. " in pagina and "### Detalle técnico" in pagina


def test_preguntar_aparte_lleva_delante_las_anteriores():
	from app.ui.dialogos.preguntar_aparte import MAX_ANTERIORES, con_anteriores

	assert con_anteriores("¿Y eso?", []) == "¿Y eso?"
	texto = con_anteriores("¿Y eso?", [("¿Qué?", "Esto")])
	assert "Pregunta: ¿Qué?\nRespuesta: Esto" in texto and texto.endswith("Mi pregunta ahora: ¿Y eso?")
	largo = con_anteriores("¿Y?", [("a", "x" * MAX_ANTERIORES), ("b", "corta")])
	assert "corta" in largo and "x" * 100 not in largo


@pytest.fixture
def ventana_con_mensajes(ventana):
	mensajes = [MensajeVista("usuario", "Pregunta", 1.0, id=1), MensajeVista("asistente", "Respuesta 1", 2.0, id=2),
		MensajeVista("herramienta", "Lee x", 3.0, id=3), MensajeVista("asistente", "Respuesta 2", 4.0, id=4)]
	ventana.sesion_actual = "ctrlia:prueba"
	ventana.mostrar_mensajes(mensajes)
	return ventana


from tests.test_ventana_principal import ventana  # noqa: E402, F401


def test_poner_vista_filtra_y_conserva_el_mensaje(ventana_con_mensajes, monkeypatch):
	from app.ui import avisos

	ventana = ventana_con_mensajes
	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	ventana.lista_mensajes.enfocar_fila(2, anunciar=False)  # la herramienta
	ventana.poner_vista_mensajes("respuestas")
	assert [m.texto for m in ventana._mensajes] == ["Respuesta 1", "Respuesta 2"]  # noqa: SLF001
	assert ventana.mensaje_actual().texto == "Respuesta 1"  # la anterior que se ve
	assert dichos[-1].startswith("Vista: Solo las respuestas del agente. 2 mensajes")
	ventana.anadir_o_actualizar_mensaje(MensajeVista("usuario", "Otra", 5.0, id=5))
	assert len(ventana._mensajes) == 2 and len(ventana.mensajes_completos()) == 5  # noqa: SLF001
	ventana.cambiar_vista_mensajes(1)
	assert ventana.vista_mensajes == "tuyos"
	ventana.poner_vista_mensajes("todo")
	assert len(ventana._mensajes) == 5 and ventana.mensaje_actual().texto == "Pregunta"  # noqa: SLF001


def test_ir_a_la_respuesta_siguiente_y_anterior(ventana_con_mensajes, monkeypatch):
	from app.ui import avisos

	ventana = ventana_con_mensajes
	monkeypatch.setattr(avisos, "anunciar", lambda *a, **k: None)
	monkeypatch.setattr(type(ventana.lista_mensajes), "SetFocus", lambda self: None)
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	ventana.accion_respuesta_siguiente()
	assert ventana.mensaje_actual().texto == "Respuesta 1"
	ventana.accion_respuesta_siguiente()
	assert ventana.mensaje_actual().texto == "Respuesta 2"
	ventana.accion_respuesta_anterior()
	assert ventana.mensaje_actual().texto == "Respuesta 1"


def test_la_ventana_de_actividad_se_pone_al_dia_sin_moverte(app_wx, monkeypatch):
	from app.ui.dialogos import actividad as modulo

	registro = actividad.RegistroActividad()
	registro.aplicar("s", {"id": "a", "clase": "subagente", "titulo": "uno", "detenible": True})
	registro.aplicar("s", {"id": "b", "clase": "orden", "orden": "npm test"})
	detenidas: list[str] = []
	monkeypatch.setattr(modulo, "confirmar", lambda *a, **k: __import__("wx").ID_YES)
	monkeypatch.setattr(modulo.avisos, "anunciar", lambda *a, **k: None)
	dialogo = modulo.DialogoActividad(None, registro, "s", "Sesión", detenidas.append)
	try:
		assert dialogo.lista.GetItemCount() == 2
		dialogo.lista.enfocar_fila(1, anunciar=False)
		elegida = dialogo.tarea_actual().id
		registro.aplicar("s", {"id": elegida, "paso": "Ahora otra cosa"})
		dialogo.poner_al_dia()
		assert dialogo.tarea_actual().id == elegida  # no se ha movido
		assert "Ahora otra cosa" in dialogo.lista.valores_fila(1)[0]
		dialogo.lista.enfocar_fila(next(i for i, t in enumerate(dialogo._tareas) if t.id == "a"), anunciar=False)  # noqa: SLF001
		assert dialogo.detener_elegida()
		assert detenidas == ["a"]
	finally:
		dialogo._temporizador.Stop()  # noqa: SLF001
		dialogo.Destroy()


# ── Retirar un mensaje de la cola ─────────────────────────────────────────────


def test_retirar_de_la_cola_saca_solo_ese_y_su_uuid(entorno):
	conversacion = ConversacionPrueba(AjustesSesion(carpeta=entorno.carpeta), lambda evento: None)
	conversacion._candado = __import__("threading").Lock()  # noqa: SLF001
	conversacion._cola = [("Uno", []), ("Skills...\n\nDos", []), ("Tres", [])]  # noqa: SLF001
	conversacion._uuids_cola = ["u1", "u2", "u3"]  # noqa: SLF001
	assert conversacion.retirar_de_cola("Dos")
	assert [t for t, _a in conversacion._cola] == ["Uno", "Tres"] and conversacion._uuids_cola == ["u1", "u3"]  # noqa: SLF001
	assert not conversacion.retirar_de_cola("Dos") and not conversacion.retirar_de_cola("  ")


def test_el_gestor_retira_de_la_cola_y_borra_el_mensaje(entorno):
	gestor = entorno.gestor
	clave = gestor.nueva_sesion("prueba", AjustesSesion(carpeta=entorno.carpeta))
	conversacion = gestor.conversacion(clave)
	conversacion._candado = __import__("threading").Lock()  # noqa: SLF001
	conversacion._cola = ["En espera"]  # noqa: SLF001
	id_mensaje = gestor.almacen.anadir_mensaje(clave, "usuario", "En espera")
	assert gestor.retirar_de_cola(clave, id_mensaje, "En espera")
	assert gestor.almacen.mensaje(id_mensaje) is None
	assert not gestor.retirar_de_cola(clave, 0, "Ya salió")
