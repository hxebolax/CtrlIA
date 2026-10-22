"""Pruebas de la ventana principal sin mostrarla (el equipo tiene NVDA en marcha).

Se usa un Claude falso y una base de datos temporal; los historiales reales
del usuario (Claude, Codex, Antigravity) se desactivan en la configuración.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from app.core.configuracion import Configuracion  # noqa: E402
from app.services.agentes.base import (  # noqa: E402
	AjustesSesion,
	ClaseOpcion,
	Evento,
	OpcionPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.agentes.claude import ProveedorClaude  # noqa: E402
from app.services.almacen import Almacen  # noqa: E402
from app.services.gestor import FilaSesion  # noqa: E402
from app.ui import avisos  # noqa: E402
from tests.ayudas_wx import esperar_hasta, procesar_eventos  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).parent / "fakes" / "fake_claude.py"


@pytest.fixture
def ventana(app_wx, tmp_path, monkeypatch):
	from app.ui.ventana_principal import VentanaPrincipal

	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	almacen = Almacen(tmp_path / "prueba.db")
	proveedores = {"claude": ProveedorClaude(orden_base=[sys.executable, str(FAKE)])}
	marco = VentanaPrincipal(config, None, almacen=almacen, proveedores=proveedores)
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()
		procesar_eventos()


def test_todas_las_acciones_de_menu_existen(app_wx):
	from app.ui.ventana_principal import MENUS, VentanaPrincipal

	faltan = []
	for _titulo, elementos in MENUS:
		for elemento in elementos:
			if elemento[0] == "-":
				continue
			metodo = elemento[2] if elemento[0] == "submenu" else elemento[3]
			if not hasattr(VentanaPrincipal, metodo):
				faltan.append(metodo)
	assert not faltan


def test_atajos_de_menu_no_se_repiten():
	from app.ui.ventana_principal import MENUS

	vistos: dict[str, str] = {}
	for _titulo, elementos in MENUS:
		for elemento in elementos:
			if elemento[0] in ("-", "submenu") or "\t" not in elemento[1]:
				continue
			atajo = elemento[1].split("\t", 1)[1].lower()
			assert atajo not in vistos, f"{atajo} repetido en {elemento[0]} y {vistos[atajo]}"
			vistos[atajo] = elemento[0]


def test_la_ventana_no_se_muestra_y_tiene_tres_zonas(ventana):
	assert not ventana.IsShown()
	assert ventana.lista_sesiones.GetHelpText()
	assert ventana.lista_mensajes.GetHelpText()
	assert ventana.respuesta.GetHelpText()


def test_vistas_y_orden(ventana):
	filas = [
		FilaSesion("ctrlia:a", "claude", "Alfa", actividad=1, estado="esperando"),
		FilaSesion("claude:b", "claude", "Beta", actividad=3, origen="terminal"),
		FilaSesion("codex:c", "codex", "Gamma", actividad=2, oculta=True),
	]
	ventana._todas = filas
	ventana.poner_opcion("lista.vista", "todas")
	ventana.mostrar_sesiones(True)
	assert [f.titulo for f in ventana._visibles] == ["Beta", "Alfa"]
	ventana.poner_opcion("lista.vista", "te_necesitan")
	ventana.mostrar_sesiones(True)
	assert [f.titulo for f in ventana._visibles] == ["Alfa"]
	ventana.poner_opcion("lista.vista", "ocultas")
	ventana.mostrar_sesiones(True)
	assert [f.titulo for f in ventana._visibles] == ["Gamma"]
	ventana.poner_opcion("lista.vista", "todas")
	ventana._filtro = "bet"
	ventana.mostrar_sesiones(True)
	assert [f.titulo for f in ventana._visibles] == ["Beta"]


def test_conversacion_completa_con_claude_falso(ventana, tmp_path):
	clave = ventana.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Prueba"))
	ventana.refrescar_sesiones(True)
	esperar_hasta(lambda: ventana.fila_de(clave) is not None, 30)
	ventana.abrir_sesion(clave, mover_foco=False)
	procesar_eventos()
	assert ventana.enviar_mensaje(clave, "Hola")
	esperar_hasta(lambda: any(m.rol == "asistente" for m in ventana._mensajes), 45)
	textos = [m.texto for m in ventana._mensajes]
	assert textos[0] == "Hola"
	assert "Hola mundo" in textos
	esperar_hasta(lambda: not ventana.gestor.ocupada(clave), 45)
	assert ventana.almacen.sesion(clave).id_externo


def test_peticion_de_permiso_se_anuncia_y_queda_pendiente(ventana, tmp_path, monkeypatch):
	anuncios = []
	monkeypatch.setattr(avisos, "notificar", lambda texto, titulo="", padre=None: anuncios.append(texto))
	clave = ventana.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Permisos"))
	solicitud = SolicitudPermiso("s1", TipoSolicitud.HERRAMIENTA, "Claude quiere usar «Ejecutar orden»",
		opciones=[OpcionPermiso("permitir", "Permitir", ClaseOpcion.PERMITIR), OpcionPermiso("denegar", "Denegar", ClaseOpcion.DENEGAR)])
	ventana.gestor._recibir(clave, Evento(TipoEvento.PERMISO, solicitud.titulo, {"solicitud": solicitud}))
	procesar_eventos()
	assert any("Control+Mayúsculas+A" in texto for texto in anuncios)
	assert ventana.gestor.solicitudes(clave)[0][1].id == "s1"


def test_peticiones_iguales_en_paralelo_se_avisan_una_vez(ventana, tmp_path, monkeypatch):
	anuncios = []
	monkeypatch.setattr(avisos, "notificar", lambda texto, titulo="", padre=None: anuncios.append(texto))
	clave = ventana.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="MCP"))
	for numero in range(3):
		solicitud = SolicitudPermiso(f"m{numero}", TipoSolicitud.HERRAMIENTA, "Claude quiere usar «Eco» del servidor MCP «prueba»",
			herramienta="mcp__prueba__eco", opciones=[OpcionPermiso("permitir", "Permitir", ClaseOpcion.PERMITIR)])
		ventana.gestor._recibir(clave, Evento(TipoEvento.PERMISO, solicitud.titulo, {"solicitud": solicitud}))
	procesar_eventos()
	assert len([texto for texto in anuncios if "Control+Mayúsculas+A" in texto]) == 1
	assert len(ventana.gestor.solicitudes(clave)) == 3


def test_dialogo_de_permiso_tiene_la_opcion_segura_por_defecto(app_wx):
	from app.ui.dialogos.solicitud import DialogoSolicitud

	solicitud = SolicitudPermiso("s1", TipoSolicitud.HERRAMIENTA, "Prueba", detalle="rm -rf /",
		opciones=[OpcionPermiso("permitir", "Permitir una vez", ClaseOpcion.PERMITIR),
			OpcionPermiso("denegar", "Denegar", ClaseOpcion.DENEGAR)])
	dialogo = DialogoSolicitud(None, solicitud)
	try:
		botones = [h for h in dialogo.GetChildren()[0].GetChildren() if isinstance(h, wx.Button)]
		por_defecto = dialogo.GetDefaultItem()
		assert por_defecto is not None and "Denegar" in por_defecto.GetLabel().replace("&", "")
		assert all(b.GetHelpText() for b in botones)
		assert "rm -rf /" in dialogo.detalle.GetValue()
	finally:
		dialogo.Destroy()


def test_dialogo_nueva_sesion_valida_la_carpeta(ventana, monkeypatch, tmp_path):
	from app.ui.dialogos import nueva_sesion

	errores = []
	monkeypatch.setattr(nueva_sesion, "mostrar_error", lambda padre, texto, titulo="": errores.append(texto))
	dialogo = nueva_sesion.DialogoNuevaSesion(ventana, ventana.gestor, ventana.almacen, ventana.config, carpeta=tmp_path / "no_existe")
	try:
		dialogo._aceptar()
		assert errores and "no existe" in errores[0]
		dialogo.carpeta.SetValue(str(tmp_path))
		dialogo.mensaje.SetValue("Revisa el proyecto")
		monkeypatch.setattr(dialogo, "EndModal", lambda codigo: None)
		dialogo._aceptar()
		assert dialogo.resultado is not None
		assert dialogo.resultado.ajustes.titulo == "Revisa el proyecto"
		assert dialogo.resultado.proveedor == "claude"
	finally:
		dialogo.Destroy()


def test_mensaje_enviado_mientras_carga_la_sesion_no_se_pierde(ventana, monkeypatch, tmp_path):
	"""Si la carga de la sesión llega después de enviar, no borra el mensaje recién enviado."""
	from app.services.gestor import MensajeVista

	clave = ventana.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo="Carrera"))
	pendientes = []
	monkeypatch.setattr(type(ventana), "en_segundo_plano",
		lambda self, funcion, *args, al_terminar=None, al_fallar=None, clave=None: pendientes.append(al_terminar))
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.anadir_o_actualizar_mensaje(MensajeVista("usuario", "Hola", 1.0, id=7))
	pendientes[-1]([])  # la carga llega tarde y vacía
	assert [m.texto for m in ventana._mensajes] == ["Hola"]
	assert ventana.lista_mensajes.GetItemCount() == 1
	pendientes[-1]([MensajeVista("usuario", "Hola", 1.0, id=7)])  # o ya trae el mensaje: no se duplica
	assert [m.texto for m in ventana._mensajes] == ["Hola"]


def test_ningun_cuadro_nativo_con_botones_en_ingles():
	"""Nada de wx.MessageBox / TextEntryDialog en la aplicación (salvo el último recurso del arranque)."""
	prohibidos = {"MessageBox", "TextEntryDialog", "GetTextFromUser", "PasswordEntryDialog"}
	encontrados = []
	for ruta in (RAIZ / "app").rglob("*.py"):
		arbol = ast.parse(ruta.read_text(encoding="utf-8"))
		for nodo in ast.walk(arbol):
			if isinstance(nodo, ast.Attribute) and nodo.attr in prohibidos and isinstance(nodo.value, ast.Name) and nodo.value.id == "wx":
				encontrados.append(f"{ruta.relative_to(RAIZ)}:{nodo.lineno}")
	assert not encontrados
