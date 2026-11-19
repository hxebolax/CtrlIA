"""Preguntas que el agente escribe en su respuesta: opciones en lista y varias preguntas.

Reproduce el fallo de que, con Ctrl+Mayús+A, el diálogo solo mostrara «Otra
respuesta» y el campo para escribir: las opciones escritas en una lista no se
podían elegir con las flechas y, con varias preguntas, solo salía la última.
Nada se muestra ni habla.
"""

from __future__ import annotations

import wx

from app.services import preguntas_escritas as pe
from app.services.agentes.base import ClaseOpcion, OpcionPermiso, Pregunta, SolicitudPermiso, TipoSolicitud


def test_opciones_numeradas_detras_de_la_pregunta():
	texto = ("He revisado el proyecto. ¿Qué base de datos prefieres?\n\n"
		"1. **SQLite** — local y sin servidor\n2. **PostgreSQL**: más potente\n3. MySQL\n")
	(pregunta,) = pe.preguntas_del_final(texto)
	assert pregunta.texto == "¿Qué base de datos prefieres?"
	assert pregunta.opciones == [("SQLite", "local y sin servidor"), ("PostgreSQL", "más potente"), ("MySQL", "")]
	assert pregunta.admite_otra


def test_opciones_con_vinetas_y_coletilla_final():
	texto = "¿Cómo seguimos?\n- Rehacer la tabla\n- Dejarla como está\n\nDime y sigo."
	(pregunta,) = pe.preguntas_del_final(texto)
	assert [t for t, _d in pregunta.opciones] == ["Rehacer la tabla", "Dejarla como está"]


def test_opciones_delante_de_cual_prefieres():
	texto = "Tengo dos caminos:\n\n1. Reescribir el módulo\n2. Parchear la función\n\n¿Cuál prefieres?"
	(pregunta,) = pe.preguntas_del_final(texto)
	assert pregunta.texto == "¿Cuál prefieres?"
	assert [t for t, _d in pregunta.opciones] == ["Reescribir el módulo", "Parchear la función"]


def test_una_lista_de_cambios_antes_de_una_pregunta_cualquiera_no_son_opciones():
	texto = "He hecho esto:\n- Cambié la tabla\n- Añadí pruebas\n\n¿Te parece bien el enfoque?"
	(pregunta,) = pe.preguntas_del_final(texto)
	assert pregunta.opciones == []


def test_varias_preguntas_en_lista_salen_todas_con_sus_opciones():
	texto = ("Antes de seguir necesito saber:\n\n"
		"1. ¿Qué base de datos quieres usar?\n   - SQLite\n   - PostgreSQL\n"
		"2. ¿Necesitas inicio de sesión?\n"
		"3. ¿Lo despliego en Docker?\n")
	preguntas = pe.preguntas_del_final(texto)
	assert [p.texto for p in preguntas] == [
		"¿Qué base de datos quieres usar?", "¿Necesitas inicio de sesión?", "¿Lo despliego en Docker?"]
	assert [t for t, _d in preguntas[0].opciones] == ["SQLite", "PostgreSQL"]
	assert preguntas[1].opciones == [] and preguntas[2].opciones == []
	solicitud = pe.solicitud_de(texto)
	assert solicitud is not None and "3 preguntas" in solicitud.titulo
	assert "2. ¿Necesitas inicio de sesión?" in solicitud.detalle
	respuesta = pe.texto_respuesta(solicitud, {"escrita-0": ["SQLite"], "escrita-1": ["No"], "escrita-2": ["Sí"]})
	assert respuesta.startswith("¿Qué base de datos quieres usar?\nSQLite\n\n¿Necesitas inicio de sesión?\nNo")


def test_un_ofrecimiento_con_opciones_si_es_pregunta_y_sin_ellas_no():
	assert pe.preguntas_del_final("Hecho. ¿Quieres que siga?") == []
	(pregunta,) = pe.preguntas_del_final("Hecho. ¿Quieres que siga?\n1. Sí, con las pruebas\n2. No, para aquí")
	assert len(pregunta.opciones) == 2


def test_lo_que_ya_funcionaba_sigue_igual():
	(pregunta,) = pe.preguntas_del_final("He mirado el código. ¿Qué base de datos prefieres?")
	assert pregunta.texto == "¿Qué base de datos prefieres?" and pregunta.opciones == []
	assert pe.preguntas_del_final("Todo listo.") == []
	assert pe.preguntas_del_final("¿Empezamos? " + "x" * (pe.MAX_DESPUES + 10)) == []
	assert pe.preguntas_del_final("Mira:\n```\nif x?\n```\n") == []


def _solicitud(pregunta: Pregunta) -> SolicitudPermiso:
	return SolicitudPermiso(
		"p1", TipoSolicitud.PREGUNTA, pregunta.texto, preguntas=[pregunta],
		opciones=[OpcionPermiso("responder", "Responder", ClaseOpcion.PERMITIR),
			OpcionPermiso("no_responder", "No responder", ClaseOpcion.DENEGAR)],
	)


def test_dialogo_sin_opciones_es_un_campo_de_texto_y_no_una_lista_vacia(app_wx):
	from app.ui.dialogos.solicitud import DialogoSolicitud

	dialogo = DialogoSolicitud(None, _solicitud(Pregunta("q", "¿Cómo se llama?", admite_otra=True)))
	try:
		((_pregunta, control, otra),) = dialogo._preguntas
		assert isinstance(control, wx.TextCtrl) and otra is None
		control.ChangeValue("Ctrl+IA")
		assert dialogo._respuestas() == {"q": ["Ctrl+IA"]}
	finally:
		dialogo.Destroy()


def test_dialogo_con_opciones_escritas_se_eligen_con_las_flechas(app_wx):
	from app.ui.dialogos.solicitud import DialogoSolicitud

	(pregunta,) = pe.preguntas_del_final("¿Cuál?\n1. Uno\n2. Dos\n3. Tres")
	dialogo = DialogoSolicitud(None, _solicitud(pregunta))
	try:
		((_p, control, otra),) = dialogo._preguntas
		assert isinstance(control, wx.ListBox)
		assert control.GetStrings() == ["Uno", "Dos", "Tres", "Otra respuesta (escríbela debajo)"]
		control.SetSelection(1)
		assert dialogo._respuestas() == {"escrita-0": ["Dos"]}
		# Escribir otra respuesta elige «Otra respuesta» sin tener que bajar a ella.
		otra.SetValue("Cuatro")
		assert control.GetSelection() == 3
		assert dialogo._respuestas() == {"escrita-0": ["Cuatro"]}
	finally:
		dialogo.Destroy()
