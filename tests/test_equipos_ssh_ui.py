"""Pruebas de la interfaz de los equipos por SSH: el diálogo, «Nueva sesión» y la lista de sesiones."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import pytest

wx = pytest.importorskip("wx")

from app.core.configuracion import Configuracion  # noqa: E402
from app.services.agentes import remoto  # noqa: E402
from app.services.agentes.base import AjustesSesion  # noqa: E402
from app.services.agentes.claude import ProveedorClaude  # noqa: E402
from app.services.almacen import Almacen  # noqa: E402
from app.services.gestor import FilaSesion, GestorSesiones  # noqa: E402

MAC = {"id": "mac-del-estudio", "nombre": "Mac del estudio", "destino": "ana@192.168.1.40", "sistema": "macos"}
SIN_HISTORIAL = lambda clave, defecto: False if clave.startswith("historial.") else defecto  # noqa: E731
LINUX = {"id": "servidor", "nombre": "Servidor", "destino": "pepe@servidor", "sistema": "linux", "ollama": True}


def _letras_alt(ventana: wx.Window) -> list[str]:
	letras = []
	for hijo in ventana.GetChildren():
		if isinstance(hijo, wx.StaticText | wx.Button | wx.CheckBox):
			coincidencia = re.search(r"&(\w)", hijo.GetLabel())
			if coincidencia:
				letras.append(coincidencia.group(1).lower())
		letras.extend(_letras_alt(hijo))
	return letras


def _ayudas_completas(ventana: wx.Window) -> None:
	for hijo in ventana.GetChildren():
		if isinstance(hijo, wx.TextCtrl | wx.Choice | wx.CheckBox | wx.SpinCtrl | wx.ListCtrl) or (
				isinstance(hijo, wx.Button) and hijo.GetId() not in (wx.ID_OK, wx.ID_CANCEL)):
			assert hijo.GetHelpText(), f"sin ayuda de Ctrl+H: {hijo.GetName()}"
		_ayudas_completas(hijo)


def test_dialogo_de_equipos(app_wx):
	from app.ui.dialogos.equipos_ssh import DialogoEquiposSSH

	dialogo = DialogoEquiposSSH(None, [MAC, LINUX, {"id": ""}, "basura"])
	try:
		assert dialogo.lista.GetItemCount() == 2
		letras = _letras_alt(dialogo)
		assert len(set(letras)) == len(letras), f"teclas Alt repetidas: {letras}"
		_ayudas_completas(dialogo)
		# Solo un equipo puede usar su Ollama: marcarlo en otro lo desmarca en el anterior.
		nuevo = remoto.EquipoSSH.desde_dict({**MAC, "ollama": True})
		dialogo._poner(nuevo, 0)
		assert [e.ollama for e in dialogo.equipos] == [True, False]
		assert dialogo.resultado_final()[0]["ollama"] is True
	finally:
		dialogo.Destroy()


def test_editar_equipo_valida_y_crea_id(app_wx, monkeypatch):
	from app.ui.dialogos import equipos_ssh

	errores = []
	monkeypatch.setattr(equipos_ssh, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
	dialogo = equipos_ssh.DialogoEditarEquipo(None, ids_usados=("mac-del-estudio",))
	try:
		letras = _letras_alt(dialogo)
		assert len(set(letras)) == len(letras), f"teclas Alt repetidas: {letras}"
		_ayudas_completas(dialogo)
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		dialogo.nombre.SetValue("Mac del estudio")
		dialogo.destino.SetValue("ana@mac; rm -rf /")
		dialogo._aceptar()
		assert dialogo.equipo is None and "destino" in errores[-1]
		dialogo.destino.SetValue("ana@mac-estudio.local")
		dialogo.variables.SetValue("MAL NOMBRE=1")
		dialogo._aceptar()
		assert dialogo.equipo is None and "variable" in errores[-1]
		dialogo.variables.SetValue("CLAUDE_CONFIG_DIR=/Users/ana/.claude")
		dialogo.rutas["claude"].SetValue("/Users/ana/.local/bin/claude")
		dialogo._aceptar()
		assert dialogo.equipo is not None
		assert dialogo.equipo.id == "mac-del-estudio-2"
		assert dialogo.equipo.rutas == {"claude": "/Users/ana/.local/bin/claude"}
		assert dialogo.equipo.variables == {"CLAUDE_CONFIG_DIR": "/Users/ana/.claude"}
	finally:
		dialogo.Destroy()


def test_texto_de_la_comprobacion():
	from app.ui.dialogos.equipos_ssh import texto_resultados

	texto = texto_resultados(remoto.EquipoSSH.desde_dict(MAC), [
		remoto.Resultado("Conexión", True, "Conectado."), remoto.Resultado("Gemini CLI", False, "No está.")])
	assert "Correcto. Conexión: Conectado." in texto
	assert "Problema. Gemini CLI (experimental): No está." in texto


@pytest.fixture
def entorno_nueva(app_wx, tmp_path):
	almacen = Almacen(tmp_path / "n.db")
	config = Configuracion(tmp_path / "s.json")
	config.establecer("agentes.equipos_ssh", [MAC])
	from app.services.agentes.acp import proveedor_gemini_cli

	proveedores = {"claude": ProveedorClaude(orden_base=["claude"]), "gemini": proveedor_gemini_cli()}
	gestor = GestorSesiones(almacen, proveedores, lambda c, e: None)
	yield almacen, config, gestor
	almacen.cerrar()


def test_nueva_sesion_en_otro_equipo(entorno_nueva, tmp_path, monkeypatch):
	from app.ui.dialogos import nueva_sesion

	almacen, config, gestor = entorno_nueva
	dialogo = nueva_sesion.DialogoNuevaSesion(None, gestor, almacen, config, carpeta=tmp_path, agente="claude")
	try:
		assert dialogo.equipo is not None and dialogo.equipo.GetCount() == 2
		letras = _letras_alt(dialogo)
		assert len(set(letras)) == len(letras), f"teclas Alt repetidas: {letras}"
		dialogo.equipo.SetSelection(1)
		dialogo._al_cambiar_equipo()
		assert not dialogo.examinar.IsEnabled() and not dialogo.aislada.IsEnabled()
		assert dialogo.carpeta.GetValue() == "~/"
		errores = []
		monkeypatch.setattr(nueva_sesion, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		dialogo.carpeta.SetValue(r"C:\proyectos\web")
		dialogo._aceptar()
		assert dialogo.resultado is None and "Windows" in errores[-1]
		dialogo.carpeta.SetValue("~/proyectos/web/")
		dialogo._aceptar()
		resultado = dialogo.resultado
		assert resultado is not None and resultado.proveedor == "claude"
		assert resultado.ajustes.extra == {"remoto": {"equipo": "mac-del-estudio", "nombre": "Mac del estudio",
			"carpeta": "~/proyectos/web"}}
		assert str(resultado.ajustes.carpeta) == "~/proyectos/web"
		# Volver a «Este equipo» recupera la carpeta de Windows.
		dialogo.equipo.SetSelection(0)
		dialogo._al_cambiar_equipo()
		assert dialogo.carpeta.GetValue() == str(tmp_path) and dialogo.examinar.IsEnabled()
	finally:
		dialogo.Destroy()


def test_nueva_sesion_experimental_pide_confirmacion(entorno_nueva, tmp_path, monkeypatch):
	from app.ui.dialogos import nueva_sesion

	almacen, config, gestor = entorno_nueva
	dialogo = nueva_sesion.DialogoNuevaSesion(None, gestor, almacen, config, carpeta=tmp_path, agente="gemini")
	try:
		preguntas = []
		monkeypatch.setattr(nueva_sesion, "confirmar", lambda padre, texto, titulo="", **k: preguntas.append(texto) or wx.ID_NO)
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		dialogo.equipo.SetSelection(1)
		dialogo._al_cambiar_equipo()
		dialogo.carpeta.SetValue("/home/ana/web")
		dialogo._aceptar()
		assert dialogo.resultado is None and "experimental" in preguntas[-1]
	finally:
		dialogo.Destroy()


def test_nueva_sesion_sin_equipos_no_muestra_el_campo(app_wx, tmp_path):
	from app.ui.dialogos.nueva_sesion import DialogoNuevaSesion

	almacen = Almacen(tmp_path / "n.db")
	gestor = GestorSesiones(almacen, {"claude": ProveedorClaude(orden_base=["claude"])}, lambda c, e: None)
	dialogo = DialogoNuevaSesion(None, gestor, almacen, Configuracion(tmp_path / "s.json"), carpeta=tmp_path)
	try:
		assert dialogo.equipo is None
	finally:
		dialogo.Destroy()
		almacen.cerrar()


def test_gestor_con_sesiones_remotas(tmp_path):
	almacen = Almacen(tmp_path / "g.db")
	try:
		gestor = GestorSesiones(almacen, {"claude": ProveedorClaude(orden_base=["claude"])}, lambda c, e: None, SIN_HISTORIAL)
		extra = {"remoto": {"equipo": "mac-del-estudio", "nombre": "Mac del estudio", "carpeta": "/Users/ana/web"}}
		clave = gestor.nueva_sesion("claude", AjustesSesion(carpeta=PurePosixPath("/Users/ana/web"), titulo="Web", extra=extra))
		assert almacen.carpetas_recientes() == []  # las carpetas del Mac no son recientes de Windows
		fila = next(f for f in gestor.listar() if f.clave == clave)
		assert fila.remota and fila.nombre_origen == "Ctrl+IA en Mac del estudio (SSH)"
		almacen.actualizar_sesion(clave, id_externo="aaaa")
		continuacion = gestor.continuar_aqui(clave)
		sesion = almacen.sesion(continuacion)
		assert sesion.extra["remoto"]["equipo"] == "mac-del-estudio" and sesion.carpeta == "/Users/ana/web"
	finally:
		almacen.cerrar()


def test_fila_remota():
	fila = FilaSesion(clave="ctrlia:1", proveedor="claude", titulo="x", carpeta="/Users/ana", equipo="Mac")
	assert fila.remota and fila.nombre_origen == "Ctrl+IA en Mac (SSH)"
	importada = FilaSesion(clave="importada:1", proveedor="claude", titulo="x", origen="importada", equipo="PC")
	assert not importada.remota and importada.nombre_origen == "Importada de PC"
