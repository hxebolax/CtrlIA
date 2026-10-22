"""Pruebas de las correcciones de interfaz (teclas Alt, modos, copiar, agentes ACP propios)."""

from __future__ import annotations

import re

import pytest

wx = pytest.importorskip("wx")

from app.services.agentes.claude import ProveedorClaude  # noqa: E402
from app.services.agentes.codex import ProveedorCodex  # noqa: E402
from app.ui.dialogos.acp_personalizados import (  # noqa: E402
	DialogoAcpPersonalizados,
	DialogoEditarAgenteAcp,
	identificador_de,
	separar_orden,
)


def _letras_alt(ventana: wx.Window) -> list[str]:
	letras = []
	for hijo in ventana.GetChildren():
		if isinstance(hijo, wx.StaticText | wx.Button | wx.CheckBox):
			coincidencia = re.search(r"&(\w)", hijo.GetLabel())
			if coincidencia:
				letras.append(coincidencia.group(1).lower())
		letras.extend(_letras_alt(hijo))
	return letras


def test_separar_orden_respeta_comillas():
	assert separar_orden('"C:\\Mis programas\\agente.exe" --acp -v') == ["C:\\Mis programas\\agente.exe", "--acp", "-v"]
	assert separar_orden("agente --acp") == ["agente", "--acp"]
	assert identificador_de("Mi Agente 2") == "acp-mi-agente-2"


def test_modo_predeterminado_por_agente():
	assert ProveedorCodex().modo_predeterminado() == "espacio_trabajo"
	assert ProveedorClaude(orden_base=["claude"]).modo_predeterminado() == "default"


def test_editar_agente_rechaza_lanzadores(app_wx, monkeypatch):
	from app.ui.dialogos import acp_personalizados

	errores = []
	monkeypatch.setattr(acp_personalizados, "mostrar_error", lambda padre, texto, titulo="": errores.append(texto))
	dialogo = DialogoEditarAgenteAcp(None)
	try:
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		dialogo.nombre.SetValue("Prueba")
		dialogo.orden.SetValue("agente.cmd --acp")
		dialogo._aceptar()
		assert errores and ".cmd" in errores[0]
		dialogo.orden.SetValue('"C:\\a b\\agente.exe" --acp')
		dialogo._aceptar()
		assert dialogo.agente == {"id": "acp-prueba", "nombre": "Prueba", "orden": ["C:\\a b\\agente.exe", "--acp"]}
		assert len(set(_letras_alt(dialogo))) == len(_letras_alt(dialogo)), "teclas Alt repetidas"
	finally:
		dialogo.Destroy()


def test_lista_de_agentes_acp(app_wx):
	dialogo = DialogoAcpPersonalizados(None, [{"id": "acp-x", "nombre": "X", "orden": ["x.exe", "--acp"]}, "basura"])
	try:
		assert dialogo.agentes == [{"id": "acp-x", "nombre": "X", "orden": ["x.exe", "--acp"]}]
		assert dialogo.lista.GetItemCount() == 1
		assert all(c.GetHelpText() for c in (dialogo.lista, dialogo.boton_anadir, dialogo.boton_quitar))
	finally:
		dialogo.Destroy()


def test_nueva_sesion_sin_teclas_alt_repetidas(app_wx, tmp_path):
	from app.core.configuracion import Configuracion
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones
	from app.ui.dialogos.nueva_sesion import DialogoNuevaSesion

	almacen = Almacen(tmp_path / "n.db")
	gestor = GestorSesiones(almacen, {"claude": ProveedorClaude(orden_base=["claude"])}, lambda c, e: None)
	dialogo = DialogoNuevaSesion(None, gestor, almacen, Configuracion(tmp_path / "s.json"), carpeta=tmp_path, mensaje="Hola")
	try:
		letras = _letras_alt(dialogo)
		assert len(set(letras)) == len(letras), f"teclas Alt repetidas: {letras}"
		assert dialogo.mensaje.GetValue() == "Hola"
	finally:
		dialogo.Destroy()
		almacen.cerrar()


def test_preferencias_sin_teclas_de_la_botonera():
	"""En Preferencias, A, C, P y R son de los botones (Aceptar, Cancelar, Aplicar, Restablecer)."""
	from app.ui.dialogo_preferencias import PAGINAS

	for pagina in PAGINAS:
		letras = []
		for opcion in pagina.opciones:
			coincidencia = re.search(r"&(\w)", opcion.etiqueta)
			if coincidencia:
				letras.append(coincidencia.group(1).lower())
		assert not set(letras) & set("acpr"), (pagina.titulo, letras)
		assert len(set(letras)) == len(letras), (pagina.titulo, letras)


def test_menus_sin_letras_alt_repetidas():
	from app.ui.ventana_principal import MENUS

	for titulo, elementos in MENUS:
		letras = []
		for elemento in elementos:
			if elemento[0] == "-":
				continue
			coincidencia = re.search(r"&(\w)", elemento[1].split("\t")[0])
			if coincidencia:
				letras.append(coincidencia.group(1).lower())
		assert len(set(letras)) == len(letras), (titulo, sorted(letras))
