"""Novedades de la 1.6.0 en el adaptador de Claude Code (paquete CLAUDE), con el CLI falso.

Protocolo comprobado en el binario de Claude Code 2.1.308 (ver NOTAS_CLAUDE.md): compactación con
``compact_metadata.pre_tokens``, ``system/status``, ``task_*``, pasos de subagentes con ``parent_tool_use_id``,
``api_retry``, ``model_fallback``, ``memory_saved``, ``rate_limit_event``, ``stop_task``, ``set_model``,
``get_context_usage``, ``mcp_authenticate``, ``remote_control``, ``--resume-session-at`` y
``--system-prompt-snapshot``.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services import extraccion, servidores_mcp
from app.services.agentes import claude as modulo
from app.services.agentes.base import MAX_INSTRUCCIONES, AjustesSesion, RespuestaPermiso, TipoEvento
from app.services.agentes.claude import (
	CORTE_COMPACTADA,
	NOTA_DIFERIDA,
	NOTA_RESERVA,
	SIN_RESPUESTA_TERMINADA,
	TEXTO_PREGUNTAS_HERRAMIENTA,
	ProveedorClaude,
	corte_terminado,
	describir_herramienta,
	detalle_tecnico,
	paso_de_herramienta,
	pregunta_con_contexto,
)
from app.services.historial import claude as historial_claude

FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"
UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"
UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"
UUID_C = "cccccccc-3333-4333-8333-333333333333"
UUID_D = "dddddddd-4444-4444-8444-444444444444"
SESION = "11111111-2222-3333-4444-555555555555"


class Recolector:
	def __init__(self):
		self.eventos = []
		self.fin = threading.Event()
		self.permiso = threading.Event()
		self.resultado = threading.Event()

	def __call__(self, evento):
		self.eventos.append(evento)
		if evento.tipo is TipoEvento.FIN_TURNO:
			self.fin.set()
		if evento.tipo is TipoEvento.PERMISO:
			self.permiso.set()
		if evento.tipo is TipoEvento.USO and evento.datos.get("coste") is not None:
			self.resultado.set()

	def de_tipo(self, tipo):
		return [e for e in self.eventos if e.tipo is tipo]

	def avisos(self, clave):
		return [e for e in self.de_tipo(TipoEvento.AVISO) if e.datos.get(clave)]


@pytest.fixture
def registro(tmp_path, monkeypatch):
	ruta = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(ruta))
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	return ruta


def _lineas(ruta):
	return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()] if ruta.exists() else []


def _controles(ruta):
	return [linea["control"] for linea in _lineas(ruta) if "control" in linea]


def _proveedor(minutos=15):
	return ProveedorClaude(orden_base=[sys.executable, str(FAKE)], minutos_abierto=minutos)


def _conversacion(tmp_path, recolector, minutos=15, **ajustes):
	return _proveedor(minutos).crear_conversacion(AjustesSesion(carpeta=tmp_path, **ajustes), recolector)


def _turno(conversacion, recolector, texto="Hola", espera=20):
	recolector.fin.clear()
	recolector.resultado.clear()
	conversacion.enviar(texto)
	assert recolector.fin.wait(espera)


def _esperar(condicion, tiempo=10.0):
	limite = time.monotonic() + tiempo
	while time.monotonic() < limite:
		if condicion():
			return True
		time.sleep(0.05)
	return False


# -- 1, 2, 7, 8: avisos en vivo ------------------------------------------------------


def test_avisos_en_vivo_de_compactacion_modo_servicio_limites_y_razonamiento(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "sistema")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	_turno(conversacion, recolector)
	# 1: compactando una vez (aunque llegue dos veces), cifras de compact_metadata y el fallo.
	compactando = recolector.avisos("compactando")
	assert len(compactando) == 1 and "compactando" in compactando[0].texto
	compactado = recolector.avisos("compactado")
	assert len(compactado) == 1 and "150.000" in compactado[0].texto and "20.000" in compactado[0].texto
	fallo = [e for e in recolector.avisos("servicio") if "compactar" in e.texto]
	assert fallo and "Prompt too long" in fallo[0].texto and fallo[0].datos["detalle_tecnico"] == "Prompt too long"
	# 2: Claude ha pasado al modo «Solo planificar» por su cuenta.
	modo = recolector.avisos("modo_cambiado")
	assert len(modo) == 1 and modo[0].datos["modo_cambiado"] == "plan" and "Solo planificar" in modo[0].texto
	assert conversacion.ajustes.modo_permiso == "plan"
	# 7: reintento con el motivo en palabras, otro modelo, memoria, uso adicional una vez y el límite alcanzado.
	reintento = [e for e in recolector.avisos("servicio") if "Reintento" in e.texto]
	assert reintento and "sobrecargado" in reintento[0].texto and "2 de 10" in reintento[0].texto
	assert "4 segundos" in reintento[0].texto
	otro = recolector.avisos("modelo_cambiado")
	assert otro and otro[0].datos["modelo_cambiado"] == "claude-sonnet" and "sobrecargado" in otro[0].texto
	memoria = recolector.avisos("memoria_guardada")
	assert memoria and "2 recuerdos" in memoria[0].texto
	assert len(recolector.avisos("uso_extra")) == 1
	alcanzado = recolector.avisos("limite_alcanzado")
	assert len(alcanzado) == 1 and "la semana de Opus" in alcanzado[0].texto
	limites = {(e.datos["ventana"], e.datos["porcentaje"]) for e in recolector.de_tipo(TipoEvento.LIMITES)}
	assert ("five_hour", 50) in limites and ("seven_day_overage_included", 120) in limites
	# 8: razonamiento con texto (el firmado vacío no) y «pensando» solo para la barra de estado, una vez.
	razonamiento = recolector.de_tipo(TipoEvento.RAZONAMIENTO)
	assert [e.texto for e in razonamiento] == ["Primero miro el archivo."]
	pensando = [e for e in recolector.avisos("solo_estado") if "pensando" in e.texto]
	assert len(pensando) == 1
	assert recolector.de_tipo(TipoEvento.FIN_TURNO)[-1].datos["correcto"]
	conversacion.cerrar()


def test_cada_mensaje_lleva_su_ancla(tmp_path, registro):
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	_turno(conversacion, recolector)
	mensaje = recolector.de_tipo(TipoEvento.MENSAJE)[-1]
	assert mensaje.datos["ancla"] == "aaaaaaaa-0000-4000-8000-000000000001"
	conversacion.cerrar()


# -- 3, 4, 6: subagentes y tareas en segundo plano ---------------------------------------


def test_subagente_en_segundo_plano_no_lo_corta_el_tope_y_cuenta_sus_pasos(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "tareas")
	monkeypatch.setenv("FAKE_CLAUDE_FONDO", "1.5")
	# Sin el arreglo, el tope tras el resultado cerraba Claude Code (y el subagente) a los 0,2 s.
	monkeypatch.setattr(modulo, "ESPERA_TRAS_RESULTADO", 0.2)
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("Revisa el código")
	assert recolector.resultado.wait(20)
	assert _esperar(lambda: conversacion.tareas_fondo_descritas() == [("agente-1", "Revisar el código")], 2)
	assert recolector.fin.wait(20)
	actividad = recolector.de_tipo(TipoEvento.ACTIVIDAD)
	inicio = next(e.datos for e in actividad if e.datos.get("clase") == "subagente")
	assert inicio == {"id": "agente-1", "clase": "subagente", "estado": "en_marcha", "titulo": "Revisar el código",
		"segundo_plano": True, "detenible": True, "tipo_agente": "general-purpose", "encargo": "Revisa"}
	pasos = [e.datos["paso_nuevo"] for e in actividad if "paso_nuevo" in e.datos]
	assert pasos[0] == "Dice: Empiezo por el principio"  # llegó antes que su task_started
	assert "Lee app.py" in pasos and "Ejecuta: rm -r build" in pasos
	resultado = next(p for p in pasos if p.startswith("Resultado: "))
	assert len(resultado) <= modulo.MAX_PASO
	# Tres progresos seguidos: como mucho uno por segundo.
	progresos = [e.datos for e in actividad if "paso" in e.datos]
	assert len(progresos) == 1 and progresos[0]["paso"] == "Leyendo app.py" and progresos[0]["tokens"] == 1000
	final = [e.datos for e in actividad if e.datos.get("resumen")]
	assert final[-1]["estado"] == "completada" and final[-1]["archivo_salida"] == "C:\\tmp\\agente-1.output"
	assert final[-1]["tokens"] == 5000 and final[-1]["usos_herramientas"] == 4
	# Nada del subagente entra en la conversación principal.
	assert all("Empiezo" not in e.texto for e in recolector.de_tipo(TipoEvento.MENSAJE))
	assert [e.datos["herramienta"] for e in recolector.de_tipo(TipoEvento.HERRAMIENTA)] == ["Agent", "Read"]
	fin = recolector.de_tipo(TipoEvento.FIN_TURNO)[-1]
	assert fin.datos["correcto"] and recolector.eventos.index(fin) > recolector.eventos.index(
		next(e for e in actividad if e.datos.get("estado") == "completada"))
	assert conversacion._vivo is not None and conversacion._vivo.vivo()  # noqa: SLF001 - no se ha cerrado
	conversacion.cerrar()


def test_el_proceso_que_muere_con_tareas_vivas_es_un_fallo(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "fondo_muere")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	_turno(conversacion, recolector)
	assert not recolector.de_tipo(TipoEvento.FIN_TURNO)[-1].datos["correcto"]
	errores = [e for e in recolector.de_tipo(TipoEvento.ERROR) if "segundo plano" in e.texto]
	assert errores and "Revisar el código" in errores[0].texto
	assert any(e.datos.get("estado") == "fallida" for e in recolector.de_tipo(TipoEvento.ACTIVIDAD))
	conversacion.cerrar()


def test_detener_para_tambien_los_subagentes(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "tareas")
	monkeypatch.setenv("FAKE_CLAUDE_FONDO", "15")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("Revisa")
	assert recolector.resultado.wait(20)
	conversacion.detener()
	assert recolector.fin.wait(10)
	assert recolector.de_tipo(TipoEvento.FIN_TURNO)[-1].datos["interrumpido"]
	controles = _controles(registro)
	assert {"subtype": "stop_task", "task_id": "agente-1"} in controles
	assert controles.index({"subtype": "stop_task", "task_id": "agente-1"}) < controles.index({"subtype": "interrupt"})
	assert _esperar(lambda: any(e.datos.get("estado") == "detenida" for e in recolector.de_tipo(TipoEvento.ACTIVIDAD)))
	conversacion.cerrar()


def test_detener_una_tarea(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "tareas")
	monkeypatch.setenv("FAKE_CLAUDE_FONDO", "15")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("Revisa")
	assert recolector.resultado.wait(20)
	conversacion.detener_tarea("agente-1")
	assert recolector.fin.wait(10)
	assert recolector.de_tipo(TipoEvento.FIN_TURNO)[-1].datos["correcto"]
	assert any(e.datos.get("estado") == "detenida" for e in recolector.de_tipo(TipoEvento.ACTIVIDAD))
	with pytest.raises(RuntimeError):
		conversacion.detener_tarea("no valida;")
	conversacion.cerrar()
	with pytest.raises(RuntimeError):
		conversacion.detener_tarea("agente-1")


def test_la_peticion_de_un_subagente_dice_quien_la_hace(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "permiso_subagente")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	conversacion.enviar("Hola")
	assert recolector.permiso.wait(20)
	solicitud = recolector.de_tipo(TipoEvento.PERMISO)[0].datos["solicitud"]
	assert solicitud.titulo == "El subagente «Revisar el código» quiere usar «Ejecutar orden»"
	assert solicitud.datos["subagente"] == "Revisar el código"
	conversacion.responder(solicitud.id, RespuestaPermiso("denegar"))
	assert recolector.fin.wait(20)
	conversacion.cerrar()


def test_subagente_por_su_llamada_sin_agent_id():
	conversacion = _proveedor().crear_conversacion(AjustesSesion(carpeta=Path(".")), lambda e: None)
	vivo = modulo._ProcesoClaude()  # noqa: SLF001
	turno = modulo._Turno(vivo)  # noqa: SLF001
	vivo.tareas["t1"] = {"titulo": "", "tipo_agente": "explorador"}
	vivo.tarea_de_llamada["padre"] = "t1"
	vivo.llamadas_de_subagente["hija"] = ("padre", "Bash")
	assert conversacion._subagente_de_peticion(turno, {"tool_use_id": "hija"}) == "explorador"  # noqa: SLF001
	assert conversacion._subagente_de_peticion(turno, {"tool_use_id": "otra"}) == ""  # noqa: SLF001
	assert conversacion._subagente_de_peticion(turno, {"agent_id": "desconocido"}) == "sin nombre"  # noqa: SLF001


def test_orden_en_segundo_plano_con_su_archivo_de_salida(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "orden_fondo")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	_turno(conversacion, recolector)
	orden = next(e.datos for e in recolector.de_tipo(TipoEvento.ACTIVIDAD) if e.datos["id"] == "bash-7")
	assert orden["clase"] == "orden" and orden["orden"] == "npm run servidor" and orden["detenible"]
	assert orden["archivo_salida"] == "C:\\tmp\\tareas\\bash-7.output"
	conversacion.cerrar()


# -- 9: modelo y modo en marcha ---------------------------------------------------------


def test_cambiar_el_modelo_en_marcha_no_relanza(tmp_path, registro):
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	_turno(conversacion, recolector)
	conversacion.cambiar_ajustes(modelo="opus")
	assert _esperar(lambda: not conversacion._peticiones)  # noqa: SLF001 - ya contestó
	_turno(conversacion, recolector)
	assert len([linea for linea in _lineas(registro) if "argv" in linea]) == 1
	assert {"subtype": "set_model", "model": "opus"} in _controles(registro)
	conversacion.cambiar_ajustes(modelo="")
	assert _esperar(lambda: {"subtype": "set_model", "model": "default"} in _controles(registro))
	conversacion.cerrar()


def test_modo_rechazado_vuelve_al_anterior_y_avisa(tmp_path, registro):
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector, modo_permiso="default")
	_turno(conversacion, recolector)
	conversacion.cambiar_ajustes(modo_permiso="inventado")
	assert _esperar(lambda: recolector.avisos("detalle_tecnico"))
	assert conversacion.ajustes.modo_permiso == "default"
	aviso = recolector.avisos("detalle_tecnico")[-1]
	assert "no ha aceptado" in aviso.texto and aviso.datos["modo_cambiado"] == "default"
	conversacion.cerrar()


def test_sin_preguntas_en_marcha_se_aplica_relanzando(tmp_path, registro):
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector, modo_permiso="default")
	_turno(conversacion, recolector)
	conversacion.cambiar_ajustes(modo_permiso="bypassPermissions")
	assert _esperar(lambda: recolector.avisos("detalle_tecnico"))
	assert "desde el arranque" in recolector.avisos("detalle_tecnico")[-1].texto
	assert conversacion.ajustes.modo_permiso == "bypassPermissions"
	_turno(conversacion, recolector)
	lanzados = [linea["argv"] for linea in _lineas(registro) if "argv" in linea]
	assert len(lanzados) == 2 and lanzados[1][lanzados[1].index("--permission-mode") + 1] == "bypassPermissions"
	# Nunca se lanza con --allow-dangerously-skip-permissions.
	assert all("--allow-dangerously-skip-permissions" not in argv for argv in lanzados)
	conversacion.cerrar()


# -- 10, 13, 17: desglose del contexto, MCP y control remoto ------------------------------


def test_desglose_del_contexto(tmp_path, registro):
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	assert conversacion.uso_contexto() is None  # sin proceso abierto
	_turno(conversacion, recolector)
	filas = conversacion.uso_contexto()
	assert ("Instrucciones del sistema", 3000, "") in filas
	assert ("Herramientas MCP que se cargan cuando hacen falta", 900, NOTA_DIFERIDA) in filas
	assert ("Reserva para compactar", 33000, NOTA_RESERVA) in filas
	assert ("Something new", 5, "") in filas
	assert {"subtype": "get_context_usage", "detail": "summary"} in _controles(registro)
	conversacion.cerrar()


def test_iniciar_sesion_en_un_servidor_mcp(tmp_path, registro):
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	assert conversacion.url_inicio_sesion_mcp("entrar") is None  # sin proceso abierto
	_turno(conversacion, recolector)
	assert conversacion.url_inicio_sesion_mcp("entrar") == "https://auth.example.com/authorize?x=1"
	with pytest.raises(RuntimeError, match="no necesita"):
		conversacion.url_inicio_sesion_mcp("bien")
	with pytest.raises(RuntimeError, match="Server not found"):
		conversacion.url_inicio_sesion_mcp("otro")
	conversacion.cerrar()


def test_desactivar_un_mcp_solo_en_esta_sesion(tmp_path):
	ajustes = AjustesSesion(carpeta=tmp_path, extra={"estilo": "Concise"})
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None)
	firma = conversacion._firma(True, True, 30)  # noqa: SLF001
	conversacion.activar_mcp("roto", False, solo_sesion=True)  # sin proceso: no hace falta
	assert ajustes.extra["mcp_denegados"] == ["roto"]
	assert conversacion._firma(True, True, 30) != firma  # noqa: SLF001 - el próximo mensaje relanza Claude Code
	orden = conversacion.construir_orden()
	assert orden.count("--settings") == 1
	assert json.loads(orden[orden.index("--settings") + 1]) == {"outputStyle": "Concise",
		"deniedMcpServers": [{"serverName": "roto"}]}
	conversacion._init = {"mcp_servers": [{"name": "roto", "status": "failed"}, {"name": "bien", "status": "connected"}]}  # noqa: SLF001
	lista = {s.nombre: s for s in conversacion.servidores_mcp()}
	assert lista["roto"].estado == "disabled" and lista["roto"].solo_sesion
	assert lista["roto"].motivo == servidores_mcp.MOTIVO_SOLO_SESION and not lista["bien"].solo_sesion
	conversacion.activar_mcp("roto", True, solo_sesion=True)
	assert "mcp_denegados" not in ajustes.extra
	assert "--settings" in conversacion.construir_orden()  # sigue el estilo
	with pytest.raises(RuntimeError):
		conversacion.activar_mcp("x\ny", False, solo_sesion=True)


def test_servidores_denegados_que_claude_no_lista():
	lista = servidores_mcp.con_denegados_de_sesion([], ["fuera"])
	assert lista[0].nombre == "fuera" and lista[0].estado == "disabled" and lista[0].solo_sesion


def test_control_remoto_y_volver_a_unirse(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_SET_MODEL", "error")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector, titulo="Mi sesión")
	with pytest.raises(RuntimeError):
		conversacion.control_remoto(True)
	_turno(conversacion, recolector)
	assert conversacion.control_remoto(True) == "https://claude.ai/code/cse_puente1"
	assert conversacion.ajustes.extra["control_remoto"]["bridge_session_id"] == "cse_puente1"
	pedido = next(c for c in _controles(registro) if c["subtype"] == "remote_control")
	assert pedido == {"subtype": "remote_control", "enabled": True, "name": "Mi sesión", "keep_session_on_exit": True}
	# Un proceso nuevo (otro modelo) vuelve a unirse a la misma sesión remota.
	conversacion.cambiar_ajustes(modelo="opus")
	_turno(conversacion, recolector)
	assert _esperar(lambda: any(c.get("reattach_session_id") == "cse_puente1" for c in _controles(registro)))
	assert conversacion.control_remoto(False) == ""
	assert "control_remoto" not in conversacion.ajustes.extra
	conversacion.cerrar()


# -- 14: compactar ---------------------------------------------------------------------------


def test_compactar_y_no_anadir_al_turno_mientras(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_COMPACTAR", "1.5")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	_turno(conversacion, recolector)
	recolector.fin.clear()
	conversacion.compactar()
	assert _esperar(lambda: recolector.avisos("compactando"))
	assert _esperar(lambda: conversacion._turno.mensaje_escrito)  # noqa: SLF001
	conversacion.anadir_al_turno("y además esto")
	devuelto = recolector.avisos("devolver_texto")
	assert devuelto and devuelto[0].datos["devolver_texto"] == "y además esto" and "compactando" in devuelto[0].texto
	assert recolector.fin.wait(20)
	assert "9.000" in recolector.avisos("compactado")[-1].texto
	assert [linea["mensaje"]["message"]["content"] for linea in _lineas(registro) if "mensaje" in linea][-1] == "/compact"
	assert ProveedorClaude.capacidades.compactar
	conversacion.cerrar()


# -- 18: detalle técnico ----------------------------------------------------------------------


def test_error_con_detalle_tecnico_sin_secretos(tmp_path, registro, monkeypatch):
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "error_resultado")
	recolector = Recolector()
	conversacion = _conversacion(tmp_path, recolector)
	_turno(conversacion, recolector)
	error = recolector.de_tipo(TipoEvento.ERROR)[0]
	assert "Reached maximum number of turns" in error.texto
	detalle = error.datos["detalle_tecnico"]
	assert "error_max_turns" in detalle and "max_turns" in detalle and "Error de la API" in detalle
	assert "sk-ant-api03" not in detalle
	conversacion.cerrar()


def test_detalle_tecnico_recorta():
	detalle = detalle_tecnico({"subtype": "x", "result": "a" * 10000}, ["b" * 9000])
	assert len(detalle) < modulo.MAX_DETALLE_RESULTADO + modulo.MAX_DETALLE_ERRORES + 200


# -- 11, 12, 20: orden de Claude Code -----------------------------------------------------------


def test_instrucciones_carpetas_y_preguntas_por_herramienta(tmp_path):
	ajustes = AjustesSesion(carpeta=tmp_path, id_externo=SESION, extra={
		"instrucciones": "-empieza por guion\n" + "x" * (MAX_INSTRUCCIONES + 50),
		"carpetas_adicionales": ["C:\\", str(tmp_path / "otra"), "\\\\servidor\\recurso"],
		"pedir_preguntas_con_herramienta": True,
	})
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None)
	orden = conversacion.construir_orden()
	anadidos = [a for a in orden if a.startswith("--append-system-prompt")]
	assert len(anadidos) == 1 and anadidos[0].startswith("--append-system-prompt=")
	texto = anadidos[0]
	assert "-empieza por guion" in texto and TEXTO_PREGUNTAS_HERRAMIENTA in texto
	assert texto.count("x") <= MAX_INSTRUCCIONES
	carpetas = [orden[i + 1] for i, a in enumerate(orden) if a == "--add-dir"]
	assert carpetas == [str(tmp_path / "otra")]
	assert "--forward-subagent-text" in orden
	firma = conversacion._firma(True, True, 30)  # noqa: SLF001
	ajustes.extra["instrucciones"] = "otra cosa"
	assert conversacion._firma(True, True, 30) != firma  # noqa: SLF001
	assert ProveedorClaude.capacidades.instrucciones_sesion and ProveedorClaude.capacidades.bifurcar_en_mensaje


def test_cambiar_el_prompt_tras_el_primer_turno_lo_renueva(tmp_path):
	ajustes = AjustesSesion(carpeta=tmp_path, extra={"instrucciones": "En francés"})
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None)
	assert "--system-prompt-snapshot" not in conversacion.construir_orden()  # primera vez: se guarda la huella
	conversacion._sesion_creada = True  # noqa: SLF001 - como tras el system/init
	assert "--system-prompt-snapshot" not in conversacion.construir_orden()  # sin cambios
	ajustes.extra["estilo"] = "Concise"
	orden = conversacion.construir_orden()
	assert orden[orden.index("--system-prompt-snapshot") + 1] == "off"
	assert ajustes.extra["prompt_fresco"] is True
	del ajustes.extra["estilo"]
	assert "--system-prompt-snapshot" in conversacion.construir_orden()  # para siempre


def test_sesion_antigua_sin_huella_y_sin_nada_propio_no_cambia(tmp_path):
	ajustes = AjustesSesion(carpeta=tmp_path, id_externo=SESION)
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None)
	assert "--system-prompt-snapshot" not in conversacion.construir_orden()
	ajustes.extra["instrucciones"] = "Nuevas"
	assert "--system-prompt-snapshot" in conversacion.construir_orden()


# -- 15: bifurcar o reintentar desde un mensaje ----------------------------------------------------


def _transcript(tmp_path, monkeypatch, registros, carpeta="C:\\proyecto", sesion=SESION):
	raiz = tmp_path / "claude_config"
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(raiz))
	ruta = raiz / "projects" / historial_claude.codificar_carpeta(carpeta) / f"{sesion}.jsonl"
	ruta.parent.mkdir(parents=True)
	ruta.write_text("\n".join(json.dumps(r) for r in registros) + "\n", encoding="utf-8")
	return ruta


def _usuario(uuid, padre, texto):
	return {"type": "user", "uuid": uuid, "parentUuid": padre, "message": {"role": "user", "content": texto}}


def _asistente(uuid, padre, texto, parada="end_turn", **mas):
	return {"type": "assistant", "uuid": uuid, "parentUuid": padre, **mas, "message": {"id": "m-" + uuid[:4],
		"model": "claude-prueba", "stop_reason": parada, "content": [{"type": "text", "text": texto}]}}


def test_bifurcar_y_reintentar_desde_un_mensaje(tmp_path, monkeypatch):
	_transcript(tmp_path, monkeypatch, [_usuario(UUID_A, None, "Uno"), _asistente(UUID_B, UUID_A, "Respuesta"),
		_usuario(UUID_C, UUID_B, "Dos")])

	def orden(modo, ancla, **extra):
		ajustes = AjustesSesion(carpeta=Path("C:\\proyecto"), bifurcar_de=SESION,
			extra={"cortar_en": {"modo": modo, "ancla": ancla}, **extra})
		conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None)
		return conversacion.construir_orden()

	bifurcar = orden("bifurcar", UUID_B)
	assert bifurcar[bifurcar.index("--resume") + 1] == SESION and bifurcar[bifurcar.index("--resume-session-at") + 1] == UUID_B
	assert "--fork-session" in bifurcar
	reintentar = orden("reintentar", UUID_C)
	assert reintentar[reintentar.index("--resume-session-at") + 1] == UUID_B
	primero = orden("reintentar", UUID_A)  # el primer mensaje: empieza de cero
	assert "--resume" not in primero and "--session-id" in primero
	with pytest.raises(RuntimeError, match="no está"):
		orden("reintentar", UUID_D)
	with pytest.raises(ValueError):
		orden("bifurcar", "no-es-un-uuid")
	with pytest.raises(RuntimeError, match="SSH"):
		orden("reintentar", UUID_C, remoto={"equipo": "mac"})


def test_bifurcar_con_proceso_de_verdad_y_error_si_no_se_puede(tmp_path, registro, monkeypatch):
	recolector = Recolector()
	ajustes = AjustesSesion(carpeta=tmp_path, bifurcar_de=SESION, extra={"cortar_en": {"modo": "reintentar", "ancla": UUID_D}})
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "vacia"))
	conversacion = _proveedor().crear_conversacion(ajustes, recolector)
	conversacion.enviar("Hola")
	assert _esperar(lambda: recolector.de_tipo(TipoEvento.ERROR))
	assert "ya no tiene guardada" in recolector.de_tipo(TipoEvento.ERROR)[0].texto
	conversacion.cerrar()


def test_historial_da_el_ancla_y_el_razonamiento(tmp_path):
	pensado = {"type": "assistant", "uuid": UUID_B, "parentUuid": UUID_A, "message": {"id": "m1", "content": [
		{"type": "thinking", "thinking": "Lo pienso bien.", "signature": "x"},
		{"type": "thinking", "thinking": "", "signature": "y"}]}}
	ruta = tmp_path / "t.jsonl"
	ruta.write_text("\n".join(json.dumps(r) for r in [_usuario(UUID_A, None, "Hola"), pensado,
		_asistente(UUID_C, UUID_B, "Adiós")]) + "\n", encoding="utf-8")
	con = historial_claude.leer_mensajes(ruta, razonamiento=True)
	assert [(m.rol, m.texto) for m in con] == [("usuario", "Hola"), ("razonamiento", "Lo pienso bien."), ("asistente", "Adiós")]
	assert con[0].datos["ancla"] == UUID_A and con[2].datos["ancla"] == UUID_C
	sin = historial_claude.leer_mensajes(ruta, razonamiento=False)
	assert [m.rol for m in sin] == ["usuario", "asistente"]
	assert [m.rol for m in historial_claude.leer_mensajes(ruta)] == ["usuario", "asistente"]  # de fábrica, oculto


# -- 16: preguntar aparte ---------------------------------------------------------------------------


def test_corte_en_la_ultima_respuesta_terminada():
	lineas = [json.dumps(r) for r in [_usuario(UUID_A, None, "Uno"), _asistente(UUID_B, UUID_A, "Hecho"),
		_usuario(UUID_C, UUID_B, "Dos"), _asistente(UUID_D, UUID_C, "Miro", parada="tool_use")]]
	assert corte_terminado(lineas) == UUID_B
	assert corte_terminado(lineas[:1]) == ""
	sintetico = _asistente(UUID_D, UUID_C, "Límite", isApiErrorMessage=True)
	assert corte_terminado([*lineas[:3], json.dumps(sintetico)]) == UUID_B
	lateral = _asistente(UUID_D, UUID_C, "Sub", isSidechain=True)
	assert corte_terminado([*lineas[:3], json.dumps(lateral)]) == UUID_B
	compactada = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}
	assert corte_terminado([*lineas[:2], json.dumps(compactada)]) == CORTE_COMPACTADA
	conservada = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "manual",
		"preservedMessages": {"uuids": [UUID_B]}}}
	assert corte_terminado([*lineas[:2], json.dumps(conservada)]) == UUID_B


def test_pregunta_con_contexto_conserva_lo_reciente():
	assert pregunta_con_contexto([], "¿Y esto?") == "¿Y esto?"
	texto = pregunta_con_contexto([("vieja", "r" * 30000), ("nueva", "bien")], "¿Y esto?")
	assert "[Contexto" in texto and "nueva" in texto and "vieja" not in texto and texto.endswith("¿Y esto?")


def test_preguntar_aparte(tmp_path, registro, monkeypatch):
	_transcript(tmp_path, monkeypatch, [_usuario(UUID_A, None, "Uno"), _asistente(UUID_B, UUID_A, "Hecho"),
		_usuario(UUID_C, UUID_B, "Sigue")], carpeta=str(tmp_path))
	proveedor = _proveedor()
	ajustes = AjustesSesion(carpeta=tmp_path, id_externo=SESION, modelo="haiku")
	trozos = []
	respuesta = proveedor.preguntar_aparte(ajustes, "¿Qué hiciste?", trozos.append, anteriores=[("Antes", "Nada")])
	assert respuesta.startswith("aparte:[Contexto") and respuesta.endswith("¿Qué hiciste?") and trozos == ["apar"]
	argv = next(linea["argv"] for linea in _lineas(registro) if "argv" in linea)
	assert argv[argv.index("--resume") + 1] == SESION and argv[argv.index("--resume-session-at") + 1] == UUID_B
	for opcion in ("--fork-session", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
		assert opcion in argv
	assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
	assert argv[argv.index("--permission-mode") + 1] == "manual"
	assert argv[argv.index("--permission-prompts") + 1] == "none"
	assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}
	assert argv[argv.index("--model") + 1] == "haiku"
	assert ProveedorClaude.capacidades.preguntar_aparte


def test_preguntar_aparte_sin_nada_terminado_o_por_ssh(tmp_path, monkeypatch):
	_transcript(tmp_path, monkeypatch, [_usuario(UUID_A, None, "Uno")], carpeta=str(tmp_path))
	proveedor = _proveedor()
	with pytest.raises(RuntimeError, match="Todavía no hay ninguna respuesta terminada"):
		proveedor.preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo=SESION), "¿Qué?")
	assert SIN_RESPUESTA_TERMINADA.startswith("Todavía")
	with pytest.raises(RuntimeError, match="SSH"):
		proveedor.preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo=SESION, extra={"remoto": {"equipo": "mac"}}), "¿Qué?")
	with pytest.raises(RuntimeError):
		proveedor.preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo=SESION), "  ")


def test_preguntar_aparte_se_cancela(tmp_path, monkeypatch):
	_transcript(tmp_path, monkeypatch, [_usuario(UUID_A, None, "Uno"), _asistente(UUID_B, UUID_A, "Hecho")],
		carpeta=str(tmp_path))
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	cancelar = threading.Event()
	cancelar.set()
	with pytest.raises(RuntimeError, match="cancelada"):
		_proveedor().preguntar_aparte(AjustesSesion(carpeta=tmp_path, id_externo=SESION), "¿Qué?", cancelar=cancelar)


# -- 19: narración de herramientas más corta ------------------------------------------------------


def test_narracion_corta_de_las_herramientas():
	assert describir_herramienta("Read", {"file_path": "C:\\proyecto\\app\\main.py"}) == "Leer archivo: main.py"
	assert describir_herramienta("Edit", {"file_path": "/home/ana/x.py", "old_string": "a\nb", "new_string": "a\nc\nd"}) == \
		"Editar archivo: x.py (2 líneas añadidas, 1 quitada)"
	assert describir_herramienta("Edit", {"file_path": "x.py", "old_string": "a\nb", "new_string": "a"}) == \
		"Editar archivo: x.py (1 línea quitada)"
	assert describir_herramienta("MultiEdit", {"file_path": "x.py", "edits": [{"old_string": "", "new_string": "n"},
		{"old_string": "", "new_string": "m"}]}) == "Editar archivo: x.py (2 líneas añadidas)"
	assert describir_herramienta("Write", {"file_path": "C:\\a\\nuevo.txt", "content": "1\n2\n3"}) == \
		"Escribir archivo: nuevo.txt (3 líneas)"
	assert describir_herramienta("Bash", {"command": "git  status"}) == "Ejecutar orden: git status"
	assert paso_de_herramienta("Read", {"file_path": "C:\\a\\b.py"}) == "Lee b.py"
	assert paso_de_herramienta("Grep", {"pattern": "def"}) == "Busca «def»"
	assert paso_de_herramienta("mcp__github__crear", {}) == "Usa MCP github: crear"


def test_archivos_cambiados_con_la_ruta_del_detalle():
	from app.services.gestor import MensajeVista

	vista = [MensajeVista("herramienta", "Editar archivo: app.py (1 línea añadida)", 0.0,
		"file_path: C:\\proyecto\\app.py\nold_string: a\nnew_string: b", "Edit")]
	cambios = extraccion.archivos_cambiados(vista)
	assert [c.ruta for c in cambios] == ["C:\\proyecto\\app.py"]


def test_capacidades_nuevas():
	capacidades = ProveedorClaude.capacidades
	assert capacidades.detener_tareas and capacidades.desglose_contexto and capacidades.compactar
