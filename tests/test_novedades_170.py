"""Novedades de la 1.7.0: coste por respuesta de Claude Code, red y proveedor de modelos
de Codex, listas vacías que se leen y título de la ventana. Nada se muestra ni habla.
"""

from __future__ import annotations

from typing import Any

import pytest
import wx

from app.services.agentes import codex
from app.services.agentes.claude import ConversacionClaude, _ProcesoClaude
from tests.test_aichat_codex import _MODELOS, ServidorFalso, _conversacion

# ── Coste de Claude Code: acumulado de la sesión y de cada respuesta ─────────


def _resultado(total: float, propios: int = 100, acumulados: int = 100) -> dict[str, Any]:
	return {"total_cost_usd": total, "usage": {"input_tokens": propios},
		"modelUsage": {"claude": {"inputTokens": acumulados, "contextWindow": 200000}}}


def test_el_coste_de_cada_respuesta_es_lo_que_crece_el_total():
	vivo = _ProcesoClaude()
	assert ConversacionClaude._coste_de(vivo, _resultado(0.10)) == (0.10, 0.10)
	total, turno = ConversacionClaude._coste_de(vivo, _resultado(0.25))
	assert total == 0.25 and turno == pytest.approx(0.15)


def test_el_primer_resultado_tras_reanudar_no_se_toma_como_de_la_respuesta():
	vivo = _ProcesoClaude()
	# modelUsage acumula la sesión reanudada; usage es solo de esta respuesta.
	assert ConversacionClaude._coste_de(vivo, _resultado(3.0, propios=100, acumulados=90000)) == (3.0, "")
	assert ConversacionClaude._coste_de(vivo, _resultado(3.2))[1] == pytest.approx(0.2)


def test_sin_coste_no_se_inventa_nada():
	assert ConversacionClaude._coste_de(_ProcesoClaude(), {"usage": {}}) == (None, "")


def test_el_dialogo_de_uso_escribe_los_dolares_con_coma():
	from app.ui.vp_conversacion import _dolares

	assert _dolares(0.123456) == "0,1235" and _dolares(2) == "2" and _dolares(0) == "0"


# ── Codex: red de las órdenes y proveedor de modelos ─────────────────────────


def _respuestas(proveedor: str = "", red: bool | None = None) -> dict[str, Any]:
	sandbox: dict[str, Any] = {"type": "workspaceWrite"}
	if red is not None:
		sandbox["networkAccess"] = red
	hilo = {"thread": {"id": "hilo-nuevo"}, "model": "gpt-6", "sandbox": sandbox}
	return {"model/list": _MODELOS, "thread/fork": hilo, "thread/resume": hilo, "thread/start": hilo,
		"thread/read": {"thread": {"id": "hilo-viejo", "modelProvider": proveedor}}}


def test_la_red_que_permite_la_configuracion_de_codex_no_se_pierde(tmp_path):
	servidor = ServidorFalso(_respuestas(red=True))
	conversacion, _eventos = _conversacion(tmp_path, servidor, modo_permiso="espacio_trabajo")
	conversacion._asegurar_hilo()  # noqa: SLF001
	modo = codex._modo("espacio_trabajo")  # noqa: SLF001
	assert conversacion._politica(modo)["networkAccess"] is True  # noqa: SLF001


def test_sin_red_en_la_configuracion_ni_en_la_opcion_no_se_anade(tmp_path, monkeypatch):
	monkeypatch.setattr(codex, "_opcion_global", lambda clave, defecto=False: False)
	servidor = ServidorFalso(_respuestas(red=False))
	conversacion, _eventos = _conversacion(tmp_path, servidor)
	conversacion._asegurar_hilo()  # noqa: SLF001
	politica = conversacion._politica(codex._modo("espacio_trabajo"))  # noqa: SLF001
	assert "networkAccess" not in politica


def test_la_opcion_de_red_la_pide_desde_el_principio(tmp_path, monkeypatch):
	monkeypatch.setattr(codex, "_opcion_global", lambda clave, defecto=False: clave == codex.CLAVE_RED)
	servidor = ServidorFalso(_respuestas())
	conversacion, _eventos = _conversacion(tmp_path, servidor, modo_permiso="espacio_trabajo")
	conversacion._asegurar_hilo()  # noqa: SLF001
	modo = codex._modo("espacio_trabajo")  # noqa: SLF001
	assert servidor.params("thread/start")["config"]["sandbox_workspace_write.network_access"] is True
	assert conversacion._politica(modo)["networkAccess"] is True  # noqa: SLF001


def test_una_conversacion_de_otro_proveedor_no_se_manda_a_chatgpt(tmp_path):
	servidor = ServidorFalso(_respuestas(proveedor="ollama"))
	conversacion, _eventos = _conversacion(tmp_path, servidor, bifurcar_de="hilo-viejo", modelo="gpt-6")
	conversacion._asegurar_hilo()  # noqa: SLF001
	params = servidor.params("thread/fork")
	assert params["modelProvider"] == "ollama" and "model" not in params


def test_una_conversacion_de_chatgpt_sigue_como_siempre(tmp_path):
	servidor = ServidorFalso(_respuestas(proveedor="openai"))
	conversacion, _eventos = _conversacion(tmp_path, servidor, bifurcar_de="hilo-viejo", modelo="gpt-6")
	conversacion._asegurar_hilo()  # noqa: SLF001
	params = servidor.params("thread/fork")
	assert "modelProvider" not in params and params["model"] == "gpt-6"


def test_un_proveedor_que_ya_no_existe_se_explica():
	error = codex.error_de_proveedor(codex.ErrorCodex("model provider `ollama` not found"), "ollama")
	assert error is not None and "ollama" in error.mensaje and "otro proveedor" in error.mensaje
	assert codex.error_de_proveedor(codex.ErrorCodex("otra cosa"), "ollama") is None
	assert codex.error_de_proveedor(codex.ErrorCodex("provider not found"), "openai") is None


def test_la_lista_de_historial_guarda_el_proveedor():
	assert codex.convertir_hilo({"id": "h", "modelProvider": "ollama"})["proveedor_modelos"] == "ollama"


# ── Listas que dicen que están vacías ────────────────────────────────────────


def test_una_lista_vacia_dice_lo_que_falta_en_vez_de_desconocido(app_wx):
	from app.ui.accesibilidad.nombres import crear_campo

	marco = wx.Frame(None)
	try:
		_e, lista = crear_campo(marco, "&Reglas:", wx.ListBox, vacia="No hay reglas")
		accesible = lista.GetAccessible()
		assert accesible.GetName(0) == (wx.ACC_OK, "Reglas")
		assert accesible.GetName(1) == (wx.ACC_OK, "No hay reglas")
		assert accesible.GetRole(1) == (wx.ACC_OK, wx.ROLE_SYSTEM_LISTITEM)
		lista.Append("Bash(npm test)")
		assert accesible.GetName(1)[0] == wx.ACC_NOT_IMPLEMENTED  # con filas, las lee Windows
		_e, casillas = crear_campo(marco, "&Casillas:", wx.CheckListBox)
		assert not isinstance(casillas.GetAccessible(), type(accesible))  # las de casillas tienen el suyo
	finally:
		marco.Destroy()


def test_el_titulo_de_la_ventana_no_se_cambia_si_no_cambia(monkeypatch):
	from app.ui.vp_mensajes import MixinMensajes

	llamadas: list[str] = []

	class Falsa:
		sesion_actual = ""
		actualizar_titulo_ventana = MixinMensajes.actualizar_titulo_ventana

		def fila_de(self, clave):
			return None

		def GetTitle(self):  # noqa: N802 - API de wx
			return "Ctrl+IA"

		def SetTitle(self, titulo):  # noqa: N802 - API de wx
			llamadas.append(titulo)

	Falsa().actualizar_titulo_ventana()
	assert llamadas == []


# ── Conversaciones de BasiliskLLM (.bskc) ────────────────────────────────────


def _bskc(ruta, titulo: str = "Recetas", mas: bool = False) -> None:
	import json
	import zipfile

	conversacion = {
		"title": titulo, "systems": [{"content": "Eres un cocinero."}],
		"messages": [
			{"request": {"content": "¿Cómo hago una tortilla?", "attachments": [
				{"location": "C:/fotos/huevos.png", "type": "image"}, {"location": "https://ejemplo.es/a", "type": "url"}]},
				"response": {"content": "Bate los huevos..."}, "model": {"provider_id": "openai", "model_id": "gpt-x"}},
			{"request": {"content": "¿Y con cebolla?"}, "response": {"content": "Sí, pochada."}},
		] + ([{"request": {"content": "Gracias"}, "response": {"content": "De nada."}}] if mas else []),
	}
	with zipfile.ZipFile(ruta, "w") as archivo:
		archivo.writestr("conversation.json", json.dumps(conversacion))
		archivo.writestr("attachments/huevos.png", b"png")


def test_importar_una_conversacion_bskc_y_reimportarla_la_actualiza(tmp_path):
	from app.services.almacen import Almacen
	from app.services.intercambio import ORIGEN_BSKC, es_bskc, es_importada, importar_bskc

	almacen = Almacen(tmp_path / "prueba.db")
	ruta = tmp_path / "Recetas.BSKC"
	_bskc(ruta)
	assert es_bskc(ruta)
	resultado = importar_bskc(ruta, almacen)
	assert resultado.nuevas == 1 and resultado.equipos == {ORIGEN_BSKC}
	(clave,) = resultado.claves
	assert es_importada(clave)
	sesion = almacen.sesion(clave)
	assert sesion.titulo == "Recetas" and sesion.modelo == "gpt-x"
	mensajes = almacen.mensajes(clave)
	assert [m.rol for m in mensajes] == ["sistema", "usuario", "asistente", "usuario", "asistente"]
	assert "huevos.png" in mensajes[1].texto and "https://ejemplo.es/a" in mensajes[1].texto
	assert importar_bskc(ruta, almacen).sin_cambios == 1
	_bskc(ruta, titulo="Recetas de casa", mas=True)
	otra = importar_bskc(ruta, almacen)
	assert otra.nuevas == 0 and otra.actualizadas == 1 and otra.claves == [clave]
	assert almacen.sesion(clave).titulo == "Recetas de casa"


def test_un_bskc_danado_se_rechaza(tmp_path):
	from app.services.almacen import Almacen
	from app.services.intercambio import importar_bskc

	ruta = tmp_path / "roto.bskc"
	ruta.write_bytes(b"no es un zip")
	with pytest.raises(ValueError, match="BasiliskLLM"):
		importar_bskc(ruta, Almacen(tmp_path / "prueba.db"))
