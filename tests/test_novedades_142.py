"""Pruebas de lo nuevo en la versión 1.4.2.

- Ctrl+R en el visor con formato: la selección del modo exploración de NVDA no
  llega a la página; se usa lo copiado con Ctrl+C mientras el visor está abierto.
- Claude Code espera a sus servidores MCP antes de cada turno
  (``MCP_CONNECTION_NONBLOCKING=0``), para que no diga que se han desconectado.
- «Continuar aquí» puede seguir la sesión original (Preferencias, Historial),
  también la de un Mac por SSH, si no se está usando; y trae su historial.

Nada se muestra ni habla: el equipo de desarrollo usa un lector de pantalla.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from app.services import gestor as modulo_gestor  # noqa: E402
from app.services.agentes import claude  # noqa: E402
from app.ui import visor_ayuda  # noqa: E402
from app.ui.vp_mensajes import fragmento_de  # noqa: E402
from tests.test_historial_equipos import ID_TERMINAL, gestor_con_mac  # noqa: E402, F401

# -- Citar lo seleccionado en el visor con formato ------------------------------


def test_fragmento_de_un_mensaje():
	mensaje = "## Plan\n\n1. Usar **colas** para los pedidos.\n2. Añadir pruebas."
	assert fragmento_de("Usar colas para los pedidos.", mensaje)
	assert fragmento_de("Plan 1. Usar colas", mensaje)
	assert not fragmento_de("Algo que no tiene nada que ver", mensaje)
	assert not fragmento_de("   ", mensaje)


def test_visor_con_formato_usa_lo_copiado(app_wx, monkeypatch):
	monkeypatch.setattr(visor_ayuda, "avisar", lambda padre, mensaje: None)
	secuencia = [10]
	monkeypatch.setattr(visor_ayuda, "secuencia_portapapeles", lambda: secuencia[0])
	monkeypatch.setattr(visor_ayuda, "texto_portapapeles", lambda: " Usar colas \n")
	citas: list[str] = []
	visor = visor_ayuda.VisorAyuda(None, "Mensaje copiado", "Usar colas.\n\nOtra cosa.", forzar_texto=True,
		acciones={"citar": citas.append, "nueva": citas.append}, teclas={"Ctrl+r": "citar"})
	try:
		# Simula el modo HTML (la página no da selección): lo copiado antes de abrir no cuenta.
		visor.modo = "html"
		visor.ejecutar_orden("citar", "")
		assert citas[-1] == ""
		secuencia[0] = 11  # Ctrl+C de NVDA con el visor abierto
		visor.ejecutar_orden("citar", "")
		assert citas[-1] == "Usar colas"
		# Se usa una sola vez: sin copiar de nuevo, no hay selección.
		visor.ejecutar_orden("citar", "")
		assert citas[-1] == ""
		# Si la página sí da selección, manda la selección.
		secuencia[0] = 12
		visor.ejecutar_orden("citar", "Otra cosa.")
		assert citas[-1] == "Otra cosa."
		# En el modo texto la selección es la real: el portapapeles no se mira.
		visor.modo = "texto"
		secuencia[0] = 13
		visor.ejecutar_orden("citar", "")
		assert citas[-1] == ""
	finally:
		visor.Destroy()


# -- Claude Code espera a sus servidores MCP ------------------------------------


def test_claude_espera_a_los_servidores_mcp(monkeypatch):
	monkeypatch.delenv(claude.VARIABLE_MCP_SIN_ESPERA, raising=False)
	assert claude.variables_anadidas(esperar_mcp=True)[claude.VARIABLE_MCP_SIN_ESPERA] == "0"
	assert claude.VARIABLE_MCP_SIN_ESPERA not in claude.variables_anadidas(esperar_mcp=False)
	assert claude.entorno_claude(esperar_mcp=True)[claude.VARIABLE_MCP_SIN_ESPERA] == "0"
	# Si la persona la tiene puesta por su cuenta, manda la suya.
	monkeypatch.setenv(claude.VARIABLE_MCP_SIN_ESPERA, "1")
	assert claude.VARIABLE_MCP_SIN_ESPERA not in claude.variables_anadidas(esperar_mcp=True)
	assert claude.entorno_claude(esperar_mcp=True)[claude.VARIABLE_MCP_SIN_ESPERA] == "1"
	opcion = [True]
	proveedor = claude.ProveedorClaude(orden_base=["claude"], esperar_mcp=lambda: opcion[0])
	assert proveedor.esperar_mcp()
	opcion[0] = False
	assert not proveedor.esperar_mcp()


# -- Seguir la sesión original --------------------------------------------------


def test_seguir_la_original_del_mac(gestor_con_mac, monkeypatch):  # noqa: F811
	gestor, almacen = gestor_con_mac
	monkeypatch.setattr(modulo_gestor, "RECIENTE_EN_USO", 0)
	fila = next(f for f in gestor.listar() if f.id_externo == ID_TERMINAL)
	assert gestor.puede_seguir_original(fila.clave)
	clave = gestor.continuar_con_historial(fila.clave, "", original=True)
	sesion = almacen.sesion(clave)
	assert sesion.id_externo == ID_TERMINAL and not sesion.bifurcada_de
	assert sesion.titulo == "Menú roto"
	assert sesion.extra["original"] == fila.clave
	assert sesion.extra["remoto"]["equipo"] == "mac-de-jose"
	# Trae lo que ya se había hablado, para leerla entera en Ctrl+IA.
	textos = [m.texto for m in almacen.mensajes(clave)]
	assert "Arregla el menú" in textos and "Hecho, el menú ya funciona" in textos
	# Ya es de Ctrl+IA: la del Mac deja de salir aparte.
	filas = [f for f in gestor.listar() if f.id_externo == ID_TERMINAL]
	assert [f.clave for f in filas] == [clave]


def test_la_copia_sigue_siendo_lo_de_fabrica(gestor_con_mac):  # noqa: F811
	gestor, almacen = gestor_con_mac
	fila = next(f for f in gestor.listar() if f.id_externo == ID_TERMINAL)
	clave = gestor.continuar_con_historial(fila.clave)
	sesion = almacen.sesion(clave)
	assert sesion.bifurcada_de == ID_TERMINAL and not sesion.id_externo and "original" not in sesion.extra
	assert "Arregla el menú" in [m.texto for m in almacen.mensajes(clave)]


def test_no_se_sigue_la_original_si_esta_en_uso(gestor_con_mac, monkeypatch):  # noqa: F811
	gestor, almacen = gestor_con_mac
	fila = next(f for f in gestor.listar() if f.id_externo == ID_TERMINAL)
	monkeypatch.setattr(modulo_gestor, "RECIENTE_EN_USO", 10 ** 12)
	with pytest.raises(modulo_gestor.SesionEnUso):
		gestor.continuar_con_historial(fila.clave, "", original=True)
	assert not [s for s in almacen.sesiones() if s.id_externo == ID_TERMINAL]
	# Abierta en la terminal o en la app, aunque esté sin hacer nada.
	monkeypatch.setattr(modulo_gestor, "RECIENTE_EN_USO", 0)
	with pytest.raises(modulo_gestor.SesionEnUso):
		gestor._comprobar_libre(SimpleNamespace(abierta=True, estado="inactiva", actividad=0.0))
	with pytest.raises(modulo_gestor.SesionEnUso):
		gestor._comprobar_libre({"estado": "trabajando", "actualizado": 0})
	gestor._comprobar_libre({"estado": "inactiva", "actualizado": 0})


def test_que_sesiones_se_pueden_seguir(gestor_con_mac):  # noqa: F811
	gestor, _almacen = gestor_con_mac
	assert not gestor.puede_seguir_original("claude:local_abc")  # de la app, sin conversación de Claude Code
	assert not gestor.puede_seguir_original("codex:hilo")  # Codex no está en este gestor
	assert not gestor.puede_seguir_original("antigravity:x")  # Antigravity ya sigue siempre la misma
	assert not gestor.puede_seguir_original("ctrlia:x")
	assert not gestor.puede_seguir_original("importada:x")


# -- Codex: servidores MCP que no arrancan --------------------------------------


def test_codex_avisa_una_vez_de_un_mcp_que_no_arranca(tmp_path):
	from pathlib import Path

	from app.services.agentes.base import AjustesSesion, TipoEvento
	from app.services.agentes.codex import ProveedorCodex

	eventos = []
	proveedor = ProveedorCodex(orden=[str(Path(tmp_path) / "no-existe.exe")])
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos.append)
	avisar = conversacion._al_notificacion
	avisar("mcpServer/startupStatus/updated", {"name": "github", "status": "starting"})
	avisar("mcpServer/startupStatus/updated", {"name": "github", "status": "failed", "error": "timeout"})
	avisar("mcpServer/startupStatus/updated", {"name": "github", "status": "failed", "error": "timeout"})
	avisar("mcpServer/startupStatus/updated", {"name": "drive", "status": "failed", "failureReason": "reauthenticationRequired"})
	textos = [e.texto for e in eventos if e.tipo is TipoEvento.AVISO]
	assert textos == [
		"La herramienta externa «github» de Codex no se ha podido iniciar. Motivo: timeout",
		"La herramienta externa «drive» de Codex necesita que vuelvas a iniciar sesión en ella. "
		"Hazlo con «codex mcp login drive» en una consola.",
	]
	# Si vuelve a estar lista y falla otra vez, se avisa de nuevo.
	avisar("mcpServer/startupStatus/updated", {"name": "github", "status": "ready"})
	avisar("mcpServer/startupStatus/updated", {"name": "github", "status": "failed"})
	assert len([e for e in eventos if e.tipo is TipoEvento.AVISO]) == 3
