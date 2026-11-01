"""Pruebas de lo que se añadió a los relevos entre sesiones (AIChat #379 y las fases 2 y 3 de #248).

Sesión nueva y conversación que nace de una respuesta reenviada, archivos cambiados
junto a cada respuesta, parar por repetición o cortesía, aprobar a mano, plantillas,
aviso de límites, dirigirse a una sesión y vista combinada guardada.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from app.core.base_datos import BaseDatos
from app.core.migraciones import MIGRACIONES
from app.services import dialogo_agentes as servicio
from app.services import limites, reenviar
from app.services.agentes.base import (
	AjustesSesion,
	ClaseOpcion,
	Evento,
	OpcionPermiso,
	RespuestaPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.almacen import Almacen
from app.services.dialogo_agentes import ConversacionEntreSesiones, EstadoDialogo, Participante
from app.services.gestor import FilaSesion, MensajeVista
from tests.test_dialogo_agentes import (
	FIN_BIEN,
	Sesiones,
	_sesion,
	_turno,
	gestor_doble,  # noqa: F401 - fixture
	ventana,  # noqa: F401 - fixture
)

INTERRUMPIDO = Evento(TipoEvento.FIN_TURNO, "", {"correcto": False, "interrumpido": True})


def _permiso(identificador: str = "p1") -> Evento:
	solicitud = SolicitudPermiso(identificador, TipoSolicitud.HERRAMIENTA, "Ejecutar rm", opciones=[
		OpcionPermiso("si", "Permitir", ClaseOpcion.PERMITIR), OpcionPermiso("no", "Denegar", ClaseOpcion.DENEGAR)])
	return Evento(TipoEvento.PERMISO, "", {"solicitud": solicitud})


def _conversacion(sesiones: Sesiones, **opciones) -> ConversacionEntreSesiones:
	a = Participante("A", "Claude Code", "Escritora", "claude")
	b = Participante("B", "Codex", "Revisora", "codex")
	return ConversacionEntreSesiones(a, b, "Haz la función suma.", opciones.pop("max_turnos", 6), sesiones.enviar,
		sesiones.ultima, sesiones.avisar, reloj=lambda: 0.0, **opciones)


# ── Archivos cambiados ──────────────────────────────────────────────────────────


def test_cada_respuesta_lleva_los_archivos_que_cambio_ese_turno():
	sesiones = Sesiones()
	archivos = {"A": ["app/suma.py", "app/suma.py", "docs/manual.md"]}
	conversacion = _conversacion(sesiones, archivos_del_turno=lambda clave: archivos.get(clave, []))
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "Hecho.")
	texto = sesiones.enviados[-1][1]
	assert texto.endswith("Hecho.\n\nArchivos que cambió Claude Code en ese turno:\n- app/suma.py\n- docs/manual.md")
	# Sin archivos, no se añade nada.
	_turno(sesiones, conversacion, "B", "Falta un caso.")
	assert sesiones.enviados[-1][1].endswith("Falta un caso.")


def test_la_lista_de_archivos_tiene_tope_y_no_cuenta_el_plan_de_claude():
	de = Participante("A", "Codex", "Escritora")
	mensaje = MensajeVista("asistente", "Listo.", 1.0)
	texto = servicio.texto_relevo(mensaje, de, [f"a{n}.py" for n in range(servicio.MAX_ARCHIVOS + 3)])
	assert "- a49.py\n- y 3 más" in texto and "a50.py" not in texto
	vistas = [MensajeVista("cambio", "Archivo modificado: C:\\p\\a.py", 1.0, cambios=[{"ruta": "C:\\p\\a.py", "diff": ""}]),
		MensajeVista("cambio", "Archivo nuevo: x", 2.0, cambios=[{"ruta": "C:\\u\\.claude\\plans\\plan.md", "diff": ""}])]
	assert servicio.rutas_cambiadas(vistas) == ["C:\\p\\a.py"]


# ── Parar por repetición o cortesía ─────────────────────────────────────────────


def test_para_si_se_repiten_y_lo_dice():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones, max_turnos=10)
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "La función está bien y no hay nada más que revisar en este módulo de momento.")
	_turno(sesiones, conversacion, "B", "La función está bien y no hay nada más que revisar en este módulo de ahora.")
	assert not conversacion.activa and "repitiendo" in conversacion.motivo_fin
	assert len(sesiones.enviados) == 2
	assert "repitiendo" in sesiones.avisos[-1][0] and sesiones.avisos[-1][1] is True


def test_para_si_solo_se_dan_las_gracias():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones, max_turnos=10)
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "Ya está el módulo.")
	_turno(sesiones, conversacion, "B", "Gracias por el trabajo, de acuerdo.")
	assert conversacion.activa  # una sola no para: puede ser lo que cierra un punto
	_turno(sesiones, conversacion, "A", "Perfecto, un placer.")
	assert not conversacion.activa and "gracias" in conversacion.motivo_fin


@pytest.mark.parametrize(("texto", "esperado"), [
	("Gracias.", True),
	("Perfecto, muchas gracias. Un placer.", True),
	("Sin problema, de nada.", True),
	("Thanks, sounds good.", True),
	("Gracias, pero falta un caso.", False),
	("Perfecto, ahora corrijo el fallo.", False),
	("¿De acuerdo?", False),
	("Gracias.\n```\ncodigo\n```", False),
	("", False),
	("Gracias " * 40, False),
])
def test_cortesia(texto, esperado):
	assert servicio.cortesia(texto) is esperado


def test_repeticion_por_palabras_y_sin_tardar():
	assert servicio.repeticion("Todo correcto.", "todo   CORRECTO")
	assert not servicio.repeticion("", "algo")
	assert not servicio.repeticion("Revisa la función suma.", "He añadido la documentación de la resta.")
	largo = " ".join(f"palabra{n % 400}" for n in range(30000))
	inicio = time.perf_counter()
	assert servicio.repeticion(largo, largo + " y una más")
	assert not servicio.repeticion(largo, " ".join(f"otra{n % 400}" for n in range(30000)))
	assert time.perf_counter() - inicio < 1.0  # va en el hilo de la interfaz


# ── Aprobar a mano cada relevo ──────────────────────────────────────────────────


def test_aprobar_espera_a_la_persona_y_sigue_igual():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones, aprobar=True, archivos_del_turno=lambda clave: ["a.py"] if clave == "A" else [])
	conversacion.empezar()
	assert "y la persona lo apruebe" in sesiones.enviados[0][1]
	_turno(sesiones, conversacion, "A", "Hecho.")
	assert len(sesiones.enviados) == 1  # nada sale hasta que la persona lo vea
	assert conversacion.estado is EstadoDialogo.ESPERANDO_RELEVO and conversacion.activa
	pendiente = conversacion.pendiente
	assert pendiente.de is conversacion.a and pendiente.a is conversacion.b and "- a.py" in pendiente.texto
	texto, importante = sesiones.avisos[-1]
	assert importante and "espera que la apruebes" in texto and "Control+Alt+Mayúsculas+E" in texto
	assert "espera que la apruebes" in conversacion.resumen()
	assert conversacion.aprobar_relevo()
	assert conversacion.estado is EstadoDialogo.EN_MARCHA and conversacion.pendiente is None
	clave, enviado = sesiones.enviados[1]
	assert clave == "B" and enviado.endswith(pendiente.texto) and "[Ctrl+IA] Conversación automática" in enviado
	assert conversacion.turno == 2 and conversacion.turno_de is conversacion.b


def test_aprobar_cambiando_la_respuesta_o_parando():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones, aprobar=True)
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "Hecho.")
	assert conversacion.aprobar_relevo("Hecho, y mira también los tests.")
	assert sesiones.enviados[-1][1].endswith("Hecho, y mira también los tests.")
	assert any("cambiando la respuesta de Claude Code" in i.texto for i in conversacion.historial)
	_turno(sesiones, conversacion, "B", "Falta un caso.")
	assert conversacion.pendiente is not None
	conversacion.detener()
	assert not conversacion.activa and conversacion.pendiente is None
	assert "ya no se pasará" in conversacion.motivo_fin
	assert not conversacion.aprobar_relevo()
	assert len(sesiones.enviados) == 2


def test_aprobar_dice_si_no_cabe_sin_perderlo():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones, aprobar=True)
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "x" * 40000)
	assert conversacion.activa and conversacion.pendiente is not None  # se puede acortar
	assert "32.000" in conversacion.problema_relevo(conversacion.pendiente.texto)
	assert conversacion.problema_relevo("  ")
	assert conversacion.problema_relevo("Corto.") == ""


def test_con_un_relevo_esperando_un_permiso_no_lo_cambia_y_nadie_responde():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones, aprobar=True)
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "Hecho.")
	conversacion.al_evento("A", _permiso())
	conversacion.al_evento("A", FIN_BIEN)  # la persona usó esa sesión por su cuenta
	assert conversacion.estado is EstadoDialogo.ESPERANDO_RELEVO and len(sesiones.enviados) == 1


# ── Plantillas ──────────────────────────────────────────────────────────────────


def test_plantillas_y_dialogo(app_wx, monkeypatch):
	from app.ui.dialogos import dialogo_agentes as modulo

	assert [p.clave for p in servicio.PLANTILLAS] == ["constructor_revisor", "segunda_opinion", "debate", "plan_ejecucion"]
	assert all(p.instruccion_a and p.instruccion_b and p.descripcion for p in servicio.PLANTILLAS)
	dialogo = modulo.DialogoConversacionSesiones(None, "Escritora, Claude Code", ["Revisora, Codex"])
	try:
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		dialogo.instruccion_b.SetValue("lo mío")
		dialogo.plantilla.SetSelection(1)
		dialogo.aplicar_plantilla()
		assert dialogo.instruccion_a.GetValue() == servicio.PLANTILLAS[0].instruccion_a
		assert dialogo.instruccion_b.GetValue() == servicio.PLANTILLAS[0].instruccion_b
		# Volver a «Sin plantilla» quita lo suyo, pero no lo que haya cambiado la persona.
		dialogo.instruccion_a.SetValue("mi papel")
		dialogo.plantilla.SetSelection(0)
		dialogo.aplicar_plantilla()
		assert dialogo.instruccion_a.GetValue() == "mi papel" and dialogo.instruccion_b.GetValue() == ""
		dialogo.tarea.SetValue("Tarea.")
		dialogo.aprobar.SetValue(True)
		assert dialogo.empezar()
		assert dialogo.elegido == servicio.Preparacion(0, "Tarea.", 4, "mi papel", "", True)
		assert dialogo.plantilla.GetHelpText() and dialogo.aprobar.GetHelpText()
	finally:
		dialogo.Destroy()


def _letras(dialogo) -> list[str]:
	import wx

	hijos = list(dialogo.GetChildren()[0].GetChildren())
	etiquetas = [h.GetLabel() for h in hijos if isinstance(h, (wx.StaticText, wx.Button, wx.CheckBox))]
	return [e[e.index("&") + 1].lower() for e in etiquetas if "&" in e]


def test_aceleradores_sin_repetir_en_los_dialogos_nuevos(app_wx):
	from app.ui.dialogos import dialogo_agentes as modulo
	from app.ui.dialogos.reenviar import DialogoReenviar

	dialogos = [
		modulo.DialogoConversacionSesiones(None, "A", ["B"]),
		modulo.DialogoRelevo(None, "Claude", "Codex", "Texto", lambda _t: ""),
		modulo.DialogoConversacionesGuardadas(None, ["Una"], lambda _i: None, lambda _i: False),
		DialogoReenviar(None, "A", ["B", "Nueva sesión..."], "Texto", lambda _i, _t: ""),
	]
	try:
		for dialogo in dialogos:
			letras = _letras(dialogo)
			assert letras and len(letras) == len(set(letras))
	finally:
		for dialogo in dialogos:
			dialogo.Destroy()


# ── Límites de uso ──────────────────────────────────────────────────────────────


def test_limites_cercanos():
	ahora = 1000.0
	cuenta = limites.LimitesCuenta([
		limites.VentanaLimite(93.0, limites.MINUTOS_5_HORAS, ahora + 3600),
		limites.VentanaLimite(50.0, limites.MINUTOS_SEMANA, ahora + 3600),
		limites.VentanaLimite(99.0, limites.MINUTOS_SEMANA, ahora - 1),  # ya reiniciada
	])
	resultados = [limites.ResultadoAgente("Codex", cuenta), limites.ResultadoAgente("Claude Code", error="Sin sesión.")]
	cercanos = limites.cercanos(resultados, 90, ahora)
	assert len(cercanos) == 1 and cercanos[0].startswith("Codex: Límite de 5 horas: 93 % usado")
	assert limites.cercanos(resultados, 95, ahora) == []


def test_gestor_lee_solo_los_limites_que_se_piden(gestor_doble):  # noqa: F811 - fixture
	gestor, escritor, revisor, _receptores, _carpeta = gestor_doble
	preguntados: list[str] = []
	escritor.leer_limites = lambda: preguntados.append("escritor")
	revisor.leer_limites = lambda: preguntados.append("revisor")
	assert gestor.leer_limites_todos(5, ["revisor"]) == []
	assert preguntados == ["revisor"]


# ── Dirigirse a una sesión mientras hablan ──────────────────────────────────────


def _dos(agente_b: str = "Codex", titulo_b: str = "Revisora") -> tuple[Participante, Participante]:
	a = Participante("A", "Claude Code", "Escritora", "claude")
	b = Participante("B", agente_b, titulo_b, "codex")
	return a, b


def test_direccion():
	a, b = _dos()
	assert servicio.direccion("@Codex mira esto", a, b) == ([b], "mira esto")
	assert servicio.direccion("@codex: mira esto", a, b) == ([b], "mira esto")
	assert servicio.direccion("  @Claude, mira esto", a, b) == ([a], "mira esto")
	assert servicio.direccion("@Claude Code mira esto", a, b) == ([a], "mira esto")
	assert servicio.direccion("@Revisora mira", a, b) == ([b], "mira")
	assert servicio.direccion("@ambos mirad esto", a, b) == ([a, b], "mirad esto")
	assert servicio.direccion("@los dos mirad", a, b) == ([a, b], "mirad")
	assert servicio.direccion("Codex, ¿qué falla?", a, b) == ([b], "Codex, ¿qué falla?")
	assert servicio.direccion("Revisora, mira", a, b) == ([b], "Revisora, mira")
	# Lo que no va para nadie sigue tal cual.
	for texto in ("Codex: dijo algo", "@Codexa mira", "@Nadie mira", "Mira esto, Codex", "Ambos, mirad", "codexando, sí"):
		assert servicio.direccion(texto, a, b) == (None, texto)


def test_direccion_con_el_mismo_agente_solo_vale_el_titulo():
	a, b = _dos("Claude Code")
	assert servicio.direccion("@Claude mira", a, b) == (None, "@Claude mira")
	assert servicio.direccion("@Revisora mira", a, b) == ([b], "mira")


def test_un_aparte_no_entra_en_el_relevo():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones)
	conversacion.empezar()
	# La persona escribe a la otra sesión, parada, mientras trabaja la primera.
	assert conversacion.aparte("B", "¿Qué opinas?")
	sesiones.enviar("B", "¿Qué opinas?")
	_turno(sesiones, conversacion, "B", "Opino que sí.")  # no es la respuesta de un turno del relevo
	assert conversacion.turno == 1 and conversacion.turno_de is conversacion.a
	assert [c for c, _t in sesiones.enviados] == ["A", "B"]
	assert any(i.quien.endswith("aparte (no se pasa a la otra sesión)") and i.texto == "Opino que sí." for i in conversacion.historial)
	assert any(i.texto == "(a Codex, aparte) ¿Qué opinas?" for i in conversacion.historial)
	_turno(sesiones, conversacion, "A", "Hecho.")
	assert sesiones.enviados[-1][0] == "B" and "Opino que sí." not in sesiones.enviados[-1][1]


def test_un_relevo_en_cola_tras_un_aparte_espera_su_turno():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones)
	conversacion.empezar()
	conversacion.aparte("B", "¿Qué opinas?")
	_turno(sesiones, conversacion, "A", "Hecho.")  # el relevo va a la cola de B, detrás del aparte
	assert conversacion.turno_de is conversacion.b
	_turno(sesiones, conversacion, "B", "Contesto a lo aparte.")
	assert conversacion.turno == 2 and conversacion.turno_de is conversacion.b  # todavía falta su turno
	_turno(sesiones, conversacion, "B", "Y ahora reviso.")
	assert conversacion.turno == 3 and conversacion.turno_de is conversacion.a
	assert sesiones.enviados[-1][1].endswith("Y ahora reviso.")


def test_un_aparte_detras_del_relevo_tampoco_se_mezcla():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones)
	conversacion.empezar()
	conversacion.aparte("A", "Y esto otro.")  # en cola, detrás del turno del relevo
	_turno(sesiones, conversacion, "A", "Hecho.")
	assert conversacion.turno == 2
	_turno(sesiones, conversacion, "A", "Lo otro también.")
	assert conversacion.turno == 2 and conversacion.turno_de is conversacion.b
	assert "Lo otro también." not in sesiones.enviados[-1][1]


def test_detener_durante_un_aparte_con_el_relevo_en_cola_termina():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones)
	conversacion.empezar()
	conversacion.aparte("B", "¿Qué opinas?")
	_turno(sesiones, conversacion, "A", "Hecho.")
	conversacion.al_evento("B", INTERRUMPIDO)  # Control+punto devuelve también el relevo en cola
	assert not conversacion.activa and "Se ha detenido a Codex" in conversacion.motivo_fin


def test_un_aparte_que_no_sale_no_deja_un_fin_de_turno_esperado():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones)
	conversacion.empezar()
	conversacion.aparte("B", "¿Qué opinas?")
	conversacion.retirar_aparte("B")
	assert not conversacion.aparte("Z", "fuera")
	_turno(sesiones, conversacion, "A", "Hecho.")
	assert conversacion.turno == 2
	_turno(sesiones, conversacion, "B", "Bien.")
	assert conversacion.turno == 3  # era su turno del relevo, no un aparte


def test_un_mensaje_aparte_no_se_compara_con_lo_anterior():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones, max_turnos=10)
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "Todo correcto en el módulo.")
	conversacion.aparte("A", "Vuelve a decirlo.")
	_turno(sesiones, conversacion, "B", "Todo correcto en el módulo.")
	assert conversacion.activa


# ── Con el gestor de verdad: lo que nunca se hace ───────────────────────────────


def test_aprobar_y_apartes_nunca_responden_permisos(gestor_doble):  # noqa: F811 - fixture
	gestor, escritor, revisor, receptores, carpeta = gestor_doble
	clave_a = gestor.nueva_sesion("escritor", AjustesSesion(carpeta=carpeta, titulo="Escritora"))
	clave_b = gestor.nueva_sesion("revisor", AjustesSesion(carpeta=carpeta, titulo="Revisora"))

	def enviar(clave: str, texto: str) -> bool:
		gestor.enviar(clave, texto)
		return True

	conversacion = ConversacionEntreSesiones(
		Participante(clave_a, "Escritor", "Escritora", "escritor"), Participante(clave_b, "Revisor", "Revisora", "revisor"),
		"Escribe un módulo.", 8, enviar, lambda c: reenviar.mensaje_a_reenviar(gestor.mensajes(c)),
		lambda _texto, _importante: None, aprobar=True)
	receptores.append(conversacion.al_evento)
	conversacion.empezar()
	assert conversacion.estado is EstadoDialogo.PAUSADA and gestor.solicitudes(clave_a)
	gestor.responder(clave_a, "p1", RespuestaPermiso("no"))
	# Aun con la opción, el relevo espera: ni pasa la respuesta ni contesta nada.
	assert conversacion.estado is EstadoDialogo.ESPERANDO_RELEVO
	assert revisor.conversaciones == []
	assert escritor.conversaciones[0].respuestas == [("p1", RespuestaPermiso("no"))]
	conversacion.aprobar_relevo()
	assert len(revisor.conversaciones) == 1 and gestor.solicitudes() == []


# ── Una conversación que nace de una respuesta reenviada ────────────────────────


def test_reenviar_titulo_y_partes_del_mensaje():
	assert reenviar.titulo_nueva("  Mi   sesión ") == "Reenviado de Mi sesión"
	etiqueta = reenviar.etiqueta("Claude", "Autora")
	completo = f"{etiqueta}\n\nLa respuesta."
	assert reenviar.separar_etiqueta(completo, "Claude", "Autora") == "La respuesta."
	assert reenviar.separar_etiqueta("Cambié la etiqueta.\n\nLa respuesta.", "Claude", "Autora") is None
	assert reenviar.partir_mensaje(completo, "Claude", "Autora", "La respuesta.") == ("La respuesta.", "")
	assert reenviar.partir_mensaje(f"{completo}\n\n¿Qué opinas?", "Claude", "Autora", "La respuesta.") == (
		"La respuesta.", "¿Qué opinas?")
	assert reenviar.partir_mensaje(f"{etiqueta}\n\nCambiada.", "Claude", "Autora", "La respuesta.") == ("Cambiada.", "")
	assert reenviar.partir_mensaje("Todo mío.", "Claude", "Autora", "La respuesta.") == (None, "Todo mío.")
	automatica = reenviar.etiqueta("Claude", "Autora", automatico=True)
	assert reenviar.mensaje_conjunto(completo, "Claude", "Autora", "La respuesta.") == f"{automatica}\n\nLa respuesta."
	assert reenviar.mensaje_conjunto(f"{completo}\n\n¿Qué opinas?", "Claude", "Autora", "La respuesta.") == (
		f"{automatica}\n\nLa respuesta.\n\nLa persona añade:\n\n¿Qué opinas?")
	assert reenviar.mensaje_conjunto("Todo mío.", "Claude", "Autora", "La respuesta.") == "Todo mío."


def test_reenviar_por_que_no_se_puede_conversar():
	propia = FilaSesion("ctrlia:a", "claude", "Autora")
	assert reenviar.problema_conversacion(propia, True, False, False) is None
	assert "en marcha" in reenviar.problema_conversacion(propia, True, False, True).completo
	assert "sigue trabajando" in reenviar.problema_conversacion(propia, True, True, False).corto
	assert "Autora" in reenviar.problema_conversacion(propia, False, False, False).completo
	ajena = FilaSesion("claude:b", "claude", "Otra")
	problema = reenviar.problema_conversacion(ajena, True, False, False)
	assert "Otra" in problema.completo and "Otra" not in problema.corto
	assert reenviar.problema_destino("Revisora", False) is None
	assert "Revisora" in reenviar.problema_destino("Revisora", True).completo


def test_empezar_con_respuesta_contesta_primero_quien_la_recibe():
	sesiones = Sesiones()
	a = Participante("A", "Codex", "Revisora", "codex")
	b = Participante("B", "Claude Code", "Autora", "claude")
	sesiones.mensajes.setdefault("B", [])
	conversacion = ConversacionEntreSesiones(a, b, "x", 4, sesiones.enviar, sesiones.ultima, sesiones.avisar, reloj=lambda: 0.0)
	texto = reenviar.etiqueta("Claude Code", "Autora", automatico=True) + "\n\nPropongo colas."
	assert conversacion.empezar_con_respuesta(texto, "Propongo colas.", "¿Qué opinas?")
	clave, enviado = sesiones.enviados[0]
	assert clave == "A" and enviado.startswith("[Ctrl+IA] Conversación automática") and enviado.endswith("Propongo colas.")
	assert sesiones.avisos[0] == ("Turno 1 de 4: Codex responde a Claude Code.", False)
	_turno(sesiones, conversacion, "A", "Mejor una base de datos.")
	clave, enviado = sesiones.enviados[1]
	# La sesión de origen no estaba presentada: lo está ahora, con la respuesta detrás.
	assert clave == "B" and enviado.startswith("[Ctrl+IA] Conversación automática") and enviado.endswith("Mejor una base de datos.")
	assert conversacion.turno == 2
	quienes = [i.quien for i in conversacion.historial if not i.nota]
	assert quienes[:3] == ["Claude Code", "Tú", "Codex"]


def test_empezar_con_respuesta_que_no_cabe():
	sesiones = Sesiones()
	conversacion = _conversacion(sesiones)
	# Aquí «a» es Claude y no tiene tope: se prueba con Codex delante.
	conversacion.a.proveedor, conversacion.a.agente = "codex", "Codex"
	assert not conversacion.empezar_con_respuesta("x" * 40000, "x")
	assert not conversacion.activa and sesiones.enviados == []


def test_repeticion_tambien_con_la_respuesta_reenviada():
	sesiones = Sesiones()
	a = Participante("A", "Codex", "Revisora", "codex")
	b = Participante("B", "Claude Code", "Autora", "claude")
	conversacion = ConversacionEntreSesiones(a, b, "x", 4, sesiones.enviar, sesiones.ultima, sesiones.avisar, reloj=lambda: 0.0)
	conversacion.empezar_con_respuesta("Texto.", "Propongo usar colas para todo el sistema de avisos.")
	_turno(sesiones, conversacion, "A", "Propongo usar colas para todo el sistema de avisos.")
	assert not conversacion.activa and "repitiendo" in conversacion.motivo_fin


# ── Vista combinada guardada ────────────────────────────────────────────────────


def _almacen_con_sesiones(tmp_path) -> Almacen:
	from app.services.almacen import SesionPropia

	almacen = Almacen(tmp_path / "v.db")
	for clave in ("ctrlia:a", "ctrlia:b", "ctrlia:c"):
		almacen.guardar_sesion(SesionPropia(clave, "claude", "", clave, "", creada=1.0, actividad=1.0))
	return almacen


def test_almacen_guarda_y_borra_con_la_sesion(tmp_path):
	almacen = _almacen_con_sesiones(tmp_path)
	try:
		primera = almacen.crear_conversacion_sesiones("ctrlia:a", "ctrlia:b", "Claude", "Codex", "Tarea", 4)
		almacen.anadir_entrada_conversacion(primera, "Tú", "(a Claude) Tarea", 10.0)
		almacen.anadir_entrada_conversacion(primera, "Ctrl+IA", "Turno 1 de 4", 11.0, nota=True)
		almacen.actualizar_conversacion_sesiones(primera, 1, "terminada", "Se acabó.")
		guardada = almacen.conversaciones_sesiones()[0]
		assert (guardada.turno, guardada.estado, guardada.motivo_fin, guardada.nombre_b) == (1, "terminada", "Se acabó.", "Codex")
		entradas = almacen.entradas_conversacion(primera)
		assert [(e.quien, e.nota) for e in entradas] == [("Tú", False), ("Ctrl+IA", True)]
		segunda = almacen.crear_conversacion_sesiones("ctrlia:c", "ctrlia:b", "Claude", "Codex", "Otra", 4)
		almacen.anadir_entrada_conversacion(segunda, "Tú", "Otra", 20.0)
		assert [c.id for c in almacen.conversaciones_sesiones()] == [segunda, primera]
		# Borrar una sesión borra con ella la vista que tiene sus palabras; las demás siguen.
		almacen.borrar_sesion("ctrlia:a")
		assert [c.id for c in almacen.conversaciones_sesiones()] == [segunda]
		assert almacen.entradas_conversacion(primera) == []
		almacen.borrar_conversacion_sesiones(segunda)
		assert almacen.conversaciones_sesiones() == []
	finally:
		almacen.cerrar()


def test_migracion_desde_la_version_anterior(tmp_path):
	ruta = tmp_path / "vieja.db"
	bd = BaseDatos(ruta, MIGRACIONES[:5], carpeta_copias=tmp_path / "copias")
	bd.cerrar()
	almacen = Almacen(ruta)
	try:
		assert almacen.bd.version() == len(MIGRACIONES)
		assert almacen.conversaciones_sesiones() == []
	finally:
		almacen.cerrar()
	tablas = sqlite3.connect(ruta).execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
	assert ("conversaciones_sesiones_entradas",) in tablas


def test_resumen_y_texto_de_una_guardada():
	assert "terminada tras 3 de 4 turnos. Se acabó." in servicio.resumen_guardada("Claude", "Codex", 3, 4, "terminada", "Se acabó.")
	assert "a medias al cerrar Ctrl+IA" in servicio.resumen_guardada("Claude", "Codex", 2, 4, "en_marcha", "")
	texto = servicio.componer_texto("Cabecera.", [servicio.Intervencion("Codex", "Hola", 0.0),
		servicio.Intervencion("Ctrl+IA", "Nota", 0.0, nota=True)])
	assert texto.startswith("Cabecera.\n\nCodex, ") and "Hola" in texto and "Ctrl+IA, " in texto


def test_guardar_nunca_para_el_relevo():
	sesiones = Sesiones()

	def falla(*_a) -> None:
		raise sqlite3.OperationalError("database is locked")

	conversacion = _conversacion(sesiones, al_apuntar=falla, al_cambiar=falla)
	conversacion.empezar()
	_turno(sesiones, conversacion, "A", "Hecho.")
	assert conversacion.turno == 2 and conversacion.activa


# ── Diálogos, sin mostrarlos ────────────────────────────────────────────────────


def test_dialogo_reenviar_ofrece_conversar_y_dice_por_que_no(app_wx, monkeypatch):
	from app.ui.dialogos import reenviar as modulo

	vistos: list[str] = []
	monkeypatch.setattr(modulo, "mostrar_mensaje", lambda padre, texto, titulo="": vistos.append(texto))
	problemas = {0: reenviar.Problema("la otra sesión sigue trabajando", "«Revisora» sigue trabajando"), 1: None}
	dialogo = modulo.DialogoReenviar(None, "Autora", ["Revisora, Claude", "Nueva sesión..."], "El texto",
		lambda _i, _t: "", problema_conversacion=lambda i: problemas[i])
	try:
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		assert "no disponible: la otra sesión sigue trabajando" in dialogo.conversar.GetLabel()
		assert not dialogo.turnos.IsEnabled() and not dialogo.casilla_aprobar.IsEnabled()
		dialogo.conversar.SetValue(True)
		dialogo.sincronizar()
		assert dialogo.turnos.IsEnabled() and dialogo.casilla_aprobar.IsEnabled()
		# No se puede con esta sesión: lo dice y el cuadro sigue abierto.
		assert not dialogo.enviar() and "«Revisora» sigue trabajando" in vistos[-1] and dialogo.elegido is None
		dialogo.destinos.SetSelection(1)
		dialogo.sincronizar()
		assert "no disponible" not in dialogo.conversar.GetLabel()
		dialogo.turnos.SetValue(8)
		dialogo.casilla_aprobar.SetValue(True)
		assert dialogo.enviar()
		assert dialogo.elegido == (1, "El texto") and dialogo.conversacion and dialogo.max_turnos == 8 and dialogo.aprobar
		for control in (dialogo.conversar, dialogo.turnos, dialogo.casilla_aprobar):
			assert control.GetHelpText()
	finally:
		dialogo.Destroy()


def test_dialogo_relevo_sigue_para_o_lo_deja(app_wx, monkeypatch):
	from app.ui.dialogos import dialogo_agentes as modulo

	vistos: list[str] = []
	monkeypatch.setattr(modulo, "mostrar_mensaje", lambda padre, texto, titulo="": vistos.append(texto))
	dialogo = modulo.DialogoRelevo(None, "Claude", "Codex", "De Claude:\n\nHecho.", lambda t: "Vacío" if not t else "")
	try:
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		assert dialogo.GetTitle() == "Revisar el relevo: de Claude a Codex"
		assert dialogo.texto.GetValue() == "De Claude:\n\nHecho." and dialogo.texto.GetHelpText()
		dialogo.texto.SetValue("  ")
		assert not dialogo.seguir() and vistos == ["Vacío"] and dialogo.decision is None
		dialogo.texto.SetValue("Cambiado.")
		assert dialogo.seguir() and (dialogo.decision, dialogo.texto_final) == ("seguir", "Cambiado.")
		dialogo.parar()
		assert dialogo.decision == "parar"
	finally:
		dialogo.Destroy()


def test_dialogo_guardadas(app_wx):
	from app.ui.dialogos import dialogo_agentes as modulo

	vistas: list[int] = []
	dialogo = modulo.DialogoConversacionesGuardadas(None, ["Una", "Otra"], vistas.append, lambda i: i == 0)
	try:
		dialogo.ver()
		assert vistas == [0]
		dialogo.borrar()
		assert dialogo.lista.GetCount() == 1 and dialogo.lista.GetSelection() == 0
		assert dialogo._menu() is not None
	finally:
		dialogo.Destroy()


# ── En la ventana principal ─────────────────────────────────────────────────────


class FalsoReenviar:
	"""``DialogoReenviar`` sin ventana: elige el destino ``indice`` con estas opciones."""

	indice = 0
	anadido = ""
	con_conversacion = False
	turnos = 6
	aprobando = False
	visto: dict = {}

	def __init__(self, padre, titulo, destinos, texto, comprobar, trabajando=False, problema_conversacion=None):
		tipo = type(self)
		tipo.visto = dict(destinos=list(destinos), texto=texto, comprobar=comprobar, problema=problema_conversacion)
		self.elegido = (tipo.indice, texto + tipo.anadido)
		self.conversacion, self.max_turnos, self.aprobar = tipo.con_conversacion, tipo.turnos, tipo.aprobando

	def ShowModal(self):  # noqa: N802 - API de wx
		import wx

		return wx.ID_OK

	def Destroy(self):  # noqa: N802 - API de wx
		pass


def _preparar(ventana, tmp_path, monkeypatch, **opciones):  # noqa: F811 - fixture
	from app.ui.dialogos import reenviar as modulo
	from tests.ayudas_wx import esperar_hasta

	origen = _sesion(ventana, tmp_path, "Autora")
	destino = _sesion(ventana, tmp_path, "Revisora")
	ventana.almacen.anadir_mensaje(origen, "asistente", "Propongo usar colas.")
	ventana.abrir_sesion(origen, mover_foco=False)
	esperar_hasta(lambda: any(m.texto == "Propongo usar colas." for m in ventana._mensajes), 10)
	falso = type("Falso", (FalsoReenviar,), dict(opciones))
	monkeypatch.setattr(modulo, "DialogoReenviar", falso)
	enviados: list[tuple[str, str, bool]] = []

	def enviar(clave, texto, adjuntos=None, *_a, relevo=False, **_k):
		enviados.append((clave, texto, relevo))
		ventana.almacen.anadir_mensaje(clave, "usuario", texto)
		return True

	monkeypatch.setattr(ventana, "enviar_mensaje", enviar)
	monkeypatch.setattr(ventana.gestor, "leer_limites_todos", lambda *a, **k: [])
	return origen, destino, falso, enviados


def _falsa_nueva_sesion(monkeypatch, tmp_path, mensaje_final: str | None = None) -> dict:
	import wx

	from app.ui.dialogos import nueva_sesion
	from app.ui.dialogos.nueva_sesion import ResultadoNuevaSesion

	visto: dict = {}

	class Falsa:
		def __init__(self, padre, gestor, almacen, config, **opciones):
			visto.update(opciones)
			ajustes = AjustesSesion(carpeta=tmp_path, titulo=opciones["titulo"])
			self.resultado = ResultadoNuevaSesion("claude", ajustes, opciones["mensaje"] if mensaje_final is None else mensaje_final)

		def ShowModal(self):  # noqa: N802 - API de wx
			return wx.ID_OK

		def Destroy(self):  # noqa: N802 - API de wx
			pass

	monkeypatch.setattr(nueva_sesion, "DialogoNuevaSesion", Falsa)
	return visto


def test_ventana_ofrece_nueva_sesion_al_final(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	origen, _destino, falso, _enviados = _preparar(ventana, tmp_path, monkeypatch, indice=1)
	visto = _falsa_nueva_sesion(monkeypatch, tmp_path)
	ventana.reenviar_a_otra_sesion()
	assert falso.visto["destinos"][-1] == "Nueva sesión..."
	assert falso.visto["destinos"][0].startswith("Revisora, Claude")
	assert falso.visto["comprobar"](1, "x" * 40000) == ""
	assert visto["mensaje"].startswith("De Claude, otro agente de IA; te lo reenvía la persona")
	assert visto["titulo"] == "Reenviado de Autora" and visto["agente"] == "claude" and visto["carpeta"] == tmp_path


def test_ventana_envia_a_la_sesion_nueva_y_recuerda_de_donde_viene(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	origen, destino, _falso, enviados = _preparar(ventana, tmp_path, monkeypatch, indice=1)
	_falsa_nueva_sesion(monkeypatch, tmp_path)
	ventana.reenviar_a_otra_sesion()
	clave_nueva, texto, relevo = enviados[-1]
	assert clave_nueva not in (origen, destino) and texto.endswith("Propongo usar colas.") and not relevo
	assert ventana.sesion_actual == clave_nueva
	assert ventana.__dict__["_parejas_reenvio"][clave_nueva] == origen


def test_ventana_sin_otras_sesiones_ofrece_la_nueva(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	from app.ui.dialogos import reenviar as modulo
	from tests.ayudas_wx import esperar_hasta

	origen = _sesion(ventana, tmp_path, "Autora")
	ventana.almacen.anadir_mensaje(origen, "asistente", "Propongo usar colas.")
	ventana.abrir_sesion(origen, mover_foco=False)
	esperar_hasta(lambda: any(m.texto == "Propongo usar colas." for m in ventana._mensajes), 10)
	falso = type("Falso", (FalsoReenviar,), {})
	monkeypatch.setattr(modulo, "DialogoReenviar", falso)
	visto = _falsa_nueva_sesion(monkeypatch, tmp_path, mensaje_final="")
	ventana.reenviar_a_otra_sesion()
	assert falso.visto["destinos"] == ["Nueva sesión..."] and visto["titulo"] == "Reenviado de Autora"


def test_ventana_mensaje_demasiado_largo_para_la_nueva_vuelve_al_cuadro(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	from app.ui.dialogos import nueva_sesion
	from app.ui.dialogos.nueva_sesion import ResultadoNuevaSesion

	_origen, _destino, _falso, enviados = _preparar(ventana, tmp_path, monkeypatch, indice=1)
	_falsa_nueva_sesion(monkeypatch, tmp_path)
	ajustes = AjustesSesion(carpeta=tmp_path, titulo="Larga")
	clase = nueva_sesion.DialogoNuevaSesion

	class Larga(clase):
		def __init__(self, *a, **k):
			super().__init__(*a, **k)
			self.resultado = ResultadoNuevaSesion("claude", ajustes, "x" * 40000)

	monkeypatch.setattr(nueva_sesion, "DialogoNuevaSesion", Larga)
	monkeypatch.setattr(reenviar, "LIMITES", {"claude": 32000})
	ventana.reenviar_a_otra_sesion()
	assert enviados == [] and ventana.respuesta.GetValue() == "x" * 40000
	assert any("32.000" in dicho for dicho in ventana.dichos)


def test_ventana_arranca_la_conversacion_con_la_respuesta(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	from app.ui.dialogos import dialogo_agentes as modulo

	origen, destino, falso, enviados = _preparar(ventana, tmp_path, monkeypatch, indice=0, con_conversacion=True,
		anadido="\n\n¿Qué opinas?", turnos=6)
	ventana.reenviar_a_otra_sesion()
	assert falso.visto["problema"](0).corto == "ya hay una en marcha"  # solo puede haber una a la vez
	clave, texto, relevo = enviados[0]
	assert clave == destino and relevo
	assert texto.startswith("[Ctrl+IA] Conversación automática")
	assert "te lo pasa Ctrl+IA en la conversación automática desde su sesión «Autora»" in texto
	assert texto.endswith("Propongo usar colas.\n\nLa persona añade:\n\n¿Qué opinas?")
	conversacion = modulo.actual(ventana)
	assert conversacion.activa and conversacion.max_turnos == 6 and conversacion.a.clave == destino
	vista = conversacion.texto_combinado()
	assert vista.count("Propongo usar colas.") == 1 and "¿Qué opinas?" in vista
	assert ventana.almacen.conversaciones_sesiones()[0].tarea == "Respuesta reenviada de «Autora»"
	assert ventana.sesion_actual == destino
	# Su contestación vuelve a la sesión de origen, presentada y etiquetada.
	ventana.almacen.anadir_mensaje(destino, "asistente", "Mejor una base de datos.")
	ventana._al_evento(destino, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	clave, texto, relevo = enviados[1]
	assert clave == origen and relevo and texto.endswith("Mejor una base de datos.")
	assert texto.startswith("[Ctrl+IA] Conversación automática")


def test_ventana_arranca_la_conversacion_con_una_sesion_nueva(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	from app.ui.dialogos import dialogo_agentes as modulo

	origen, destino, _falso, enviados = _preparar(ventana, tmp_path, monkeypatch, indice=1, con_conversacion=True, aprobando=True)
	_falsa_nueva_sesion(monkeypatch, tmp_path)
	ventana.reenviar_a_otra_sesion()
	clave, texto, relevo = enviados[0]
	assert clave not in (origen, destino) and relevo and texto.startswith("[Ctrl+IA]")
	conversacion = modulo.actual(ventana)
	assert conversacion.aprobar and conversacion.b.clave == origen and conversacion.a.clave == clave


def test_ventana_con_el_limite_cerca_pregunta_y_no_pierde_nada(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	import wx

	from app.ui.dialogos import dialogo_agentes as modulo

	_origen, destino, _falso, enviados = _preparar(ventana, tmp_path, monkeypatch, indice=0, con_conversacion=True)
	cuenta = limites.LimitesCuenta([limites.VentanaLimite(95.0, limites.MINUTOS_5_HORAS, time.time() + 3600)])
	pedidos: list = []

	def leer(tiempo, solo=None):
		pedidos.append(tuple(solo))
		return [limites.ResultadoAgente("Claude", cuenta)]

	preguntas: list[str] = []

	def confirmar(padre, texto, titulo="", peligroso=False, **_k):
		preguntas.append(texto)
		assert peligroso  # Intro por error no gasta
		return wx.ID_NO

	monkeypatch.setattr(ventana.gestor, "leer_limites_todos", leer)
	monkeypatch.setattr(modulo, "confirmar", confirmar)
	ventana.reenviar_a_otra_sesion()
	assert pedidos == [("claude", "claude")]
	assert "Claude: Límite de 5 horas: 95 % usado" in preguntas[0] and "de todos modos" in preguntas[0]
	# Ha dicho que no: no sale nada, no hay conversación y el mensaje queda en el cuadro de esa sesión.
	assert enviados == [] and modulo.actual(ventana) is None
	assert ventana.sesion_actual == destino and "Propongo usar colas." in ventana.respuesta.GetValue()
	# Lo que ya hubiera en ese cuadro no se pisa.
	ventana.respuesta.SetValue("mi borrador")
	modulo.conservar(ventana, destino, "El mensaje.", "Motivo.")
	assert ventana.respuesta.GetValue() == "El mensaje.\n\nmi borrador"
	ventana.respuesta.Clear()
	assert any("No se ha empezado la conversación automática." in dicho for dicho in ventana.dichos)
	assert "_comprobando_limites" not in ventana.__dict__
	# Si dice que sí, empieza.
	monkeypatch.setattr(modulo, "confirmar", lambda *a, **k: wx.ID_YES)
	ventana.abrir_sesion(_origen, mover_foco=False)
	ventana.reenviar_a_otra_sesion()
	assert enviados and modulo.actual(ventana) is not None


def test_ventana_sin_umbral_no_pregunta_los_limites(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	from app.ui.dialogos import dialogo_agentes as modulo

	monkeypatch.setattr(ventana.gestor, "leer_limites_todos", lambda *a, **k: pytest.fail("no debe preguntarse"))
	ventana.config.establecer("anuncios.aviso_limite", 0)
	seguidos: list[int] = []
	modulo.comprobar_limites(ventana, ("claude",), lambda: seguidos.append(1))
	assert seguidos == [1]


def test_ventana_dirigir_un_mensaje_mientras_hablan(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	from app.ui.dialogos import dialogo_agentes as modulo

	origen = _sesion(ventana, tmp_path, "Autora")
	destino = _sesion(ventana, tmp_path, "Revisora")
	otra = _sesion(ventana, tmp_path, "Otra")
	ventana.abrir_sesion(origen, mover_foco=False)
	enviados: list[tuple[str, str]] = []

	def gestor_enviar(clave, texto, adjuntos=(), ahora=False):
		enviados.append((clave, texto))

	monkeypatch.setattr(ventana.gestor, "enviar", gestor_enviar)
	a = Participante(origen, "Claude", "Autora", "claude")
	b = Participante(destino, "Claude", "Revisora", "claude")
	conversacion = modulo.crear(ventana, a, b, "Tarea", 4)
	conversacion.empezar()
	assert enviados[0][0] == origen
	# Por su título (los dos son del mismo agente), aparte del relevo.
	ventana.respuesta.SetValue("@Revisora mira los tests")
	ventana.accion_enviar()
	assert enviados[1] == (destino, "mira los tests") and ventana.respuesta.GetValue() == ""
	assert any("aparte" in dicho and "Revisora" in dicho for dicho in ventana.dichos)
	ventana.almacen.anadir_mensaje(destino, "asistente", "Los miro.")
	ventana._al_evento(destino, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	assert conversacion.turno == 1 and len(enviados) == 2  # esa contestación no se pasa
	assert "Los miro." in conversacion.texto_combinado()
	# El nombre solo, sin mensaje: no sale nada y lo escrito sigue ahí.
	ventana.respuesta.SetValue("@Revisora")
	ventana.accion_enviar()
	assert len(enviados) == 2 and ventana.respuesta.GetValue() == "@Revisora"
	# Un mensaje sin nombre va a la sesión abierta, y su turno tampoco cuenta.
	ventana.respuesta.SetValue("Otra cosa")
	ventana.accion_enviar()
	assert enviados[2] == (origen, "Otra cosa")
	# En otra sesión, el nombre es texto normal.
	ventana.abrir_sesion(otra, mover_foco=False)
	ventana.respuesta.SetValue("@Revisora hola")
	ventana.accion_enviar()
	assert enviados[3] == (otra, "@Revisora hola")
	# Se envía desde otro sitio y falla: no queda un fin de turno esperado.
	monkeypatch.setattr(ventana.gestor, "enviar", lambda *a, **k: (_ for _ in ()).throw(ValueError("no")))
	monkeypatch.setattr("app.ui.vp_conversacion.mostrar_error", lambda *a, **k: None)
	assert not ventana.enviar_mensaje(destino, "Hola")
	assert list(conversacion._esperados[destino]) == []


def test_ventana_revisar_relevo(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	import wx

	from app.ui.dialogos import dialogo_agentes as modulo

	assert ventana._elementos_menu["revisar_relevo"].GetItemLabel().endswith("\tCtrl+Alt+Shift+E")
	assert "conversaciones_guardadas" in ventana._elementos_menu
	origen = _sesion(ventana, tmp_path, "Autora")
	destino = _sesion(ventana, tmp_path, "Revisora")
	enviados: list[tuple[str, str]] = []

	def enviar(clave, texto, *_a, relevo=False, **_k):
		enviados.append((clave, texto))
		ventana.almacen.anadir_mensaje(clave, "usuario", texto)
		return True

	monkeypatch.setattr(ventana, "enviar_mensaje", enviar)
	ventana.accion_revisar_relevo()
	assert ventana.dichos[-1] == "No hay ningún relevo esperando tu aprobación."
	conversacion = modulo.crear(ventana, Participante(origen, "Claude", "Autora", "claude"),
		Participante(destino, "Claude", "Revisora", "claude"), "Tarea", 6, aprobar=True)
	conversacion.empezar()
	ventana.almacen.anadir_mensaje(origen, "cambio", "Archivo nuevo: suma.py", datos={"cambios": [{"ruta": "suma.py", "diff": "+1"}]})
	ventana.almacen.anadir_mensaje(origen, "asistente", "Hecho.")
	ventana._al_evento(origen, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	assert "- suma.py" in conversacion.pendiente.texto and len(enviados) == 1

	class Falso:
		decision, texto_final = "seguir", "Cambiado."
		codigo = wx.ID_OK

		def __init__(self, padre, de, a, texto, comprobar):
			self.vistos = (de, a, texto)

		def ShowModal(self):  # noqa: N802 - API de wx
			return type(self).codigo

		def Destroy(self):  # noqa: N802 - API de wx
			pass

	monkeypatch.setattr(modulo, "DialogoRelevo", Falso)
	Falso.codigo = wx.ID_CANCEL  # «Más tarde»: sigue esperando
	ventana.accion_revisar_relevo()
	assert conversacion.pendiente is not None and len(enviados) == 1
	Falso.codigo = wx.ID_OK
	ventana.accion_revisar_relevo()
	assert enviados[-1][0] == destino and enviados[-1][1].endswith("Cambiado.")
	ventana.almacen.anadir_mensaje(destino, "asistente", "Revisado sin problemas graves pendientes.")
	ventana._al_evento(destino, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	Falso.decision = "parar"
	ventana.accion_revisar_relevo()
	assert not conversacion.activa and "no se ha pasado" in conversacion.motivo_fin and len(enviados) == 2


def test_ventana_la_vista_sobrevive_al_cierre_y_se_puede_borrar(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	import wx

	from app.ui import dialogo_texto
	from app.ui.dialogos import dialogo_agentes as modulo

	ventana.accion_ver_conversacion_sesiones()
	assert "Todavía no ha habido" in ventana.dichos[-1]
	ventana.accion_conversaciones_guardadas()
	assert "No hay ninguna" in ventana.dichos[-1]
	origen = _sesion(ventana, tmp_path, "Autora")
	destino = _sesion(ventana, tmp_path, "Revisora")

	def enviar(clave, texto, *_a, **_k):
		ventana.almacen.anadir_mensaje(clave, "usuario", texto)
		return True

	monkeypatch.setattr(ventana, "enviar_mensaje", enviar)
	conversacion = modulo.crear(ventana, Participante(origen, "Claude", "Autora", "claude"),
		Participante(destino, "Codex", "Revisora", "codex"), "La tarea de suma", 4)
	conversacion.empezar()
	ventana.almacen.anadir_mensaje(origen, "asistente", "Primera versión.\n[FIN]")
	ventana._al_evento(origen, Evento(TipoEvento.FIN_TURNO, "", {"correcto": True}))
	assert not conversacion.activa
	vistos: list[str] = []
	monkeypatch.setattr(dialogo_texto, "mostrar_texto", lambda padre, titulo, etiqueta, texto, copiable=False: vistos.append(texto))
	# Se cierra Ctrl+IA: en memoria ya no hay nada, pero sigue en la base de datos.
	ventana.__dict__.pop("_conversacion_sesiones")
	ventana.accion_ver_conversacion_sesiones()
	assert "terminada tras 1 de 4 turnos" in vistos[-1] and "Primera versión." in vistos[-1] and "Tú, " in vistos[-1]

	class Falso:
		def __init__(self, padre, nombres, ver, borrar):
			type(self).nombres, self.ver, self.borrar_una = list(nombres), ver, borrar

		def ShowModal(self):  # noqa: N802 - API de wx
			self.ver(0)
			assert self.borrar_una(0)
			return wx.ID_CANCEL

		def Destroy(self):  # noqa: N802 - API de wx
			pass

	monkeypatch.setattr(modulo, "DialogoConversacionesGuardadas", Falso)
	monkeypatch.setattr(modulo, "confirmar", lambda *a, **k: wx.ID_YES)
	vistos.clear()
	ventana.accion_conversaciones_guardadas()
	assert "La tarea de suma" in Falso.nombres[0] and "Claude y Codex" in Falso.nombres[0] and len(vistos) == 1
	assert ventana.almacen.conversaciones_sesiones() == []
	# A medias: lo dice al volver a abrir.
	conversacion = modulo.crear(ventana, Participante(origen, "Claude", "Autora", "claude"),
		Participante(destino, "Codex", "Revisora", "codex"), "Otra tarea", 4)
	conversacion.empezar()
	ventana.__dict__.pop("_conversacion_sesiones")
	ventana.accion_ver_conversacion_sesiones()
	assert "a medias al cerrar Ctrl+IA" in vistos[-1]


def test_ventana_sin_poder_guardar_la_vista_la_conversacion_sigue(ventana, tmp_path, monkeypatch):  # noqa: F811 - fixture
	from app.ui.dialogos import dialogo_agentes as modulo

	origen = _sesion(ventana, tmp_path, "Autora")
	destino = _sesion(ventana, tmp_path, "Revisora")

	def falla(*_a, **_k):
		raise sqlite3.OperationalError("database is locked")

	monkeypatch.setattr(ventana.almacen, "crear_conversacion_sesiones", falla)
	monkeypatch.setattr(ventana, "enviar_mensaje", lambda *a, **k: True)
	conversacion = modulo.crear(ventana, Participante(origen, "Claude", "Autora", "claude"),
		Participante(destino, "Codex", "Revisora", "codex"), "Tarea", 4)
	assert conversacion.id_guardado is None and conversacion.empezar()
