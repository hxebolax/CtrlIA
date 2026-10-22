"""Datos de publicación: autor, donaciones y servidor de actualizaciones firmado.

Las pruebas no salen a Internet ni abren el navegador de verdad.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from app.core import version
from app.services import actualizador

RAIZ = Path(__file__).resolve().parent.parent
CLAVE_PRIVADA = RAIZ / "tools" / "claves" / "firma_ed25519.key"


def test_acerca_de_dice_el_autor():
	pytest.importorskip("wx")
	from app.ui.acerca_de import texto_acerca_de

	lineas = texto_acerca_de().splitlines()
	assert lineas[0] == f"Ctrl+IA {version.APP_VERSION}"
	assert lineas[1] == "Autor: Héctor J. Benítez Corredera"


def test_servidor_de_actualizaciones_activado():
	assert actualizador.URL_BASE == "https://xebolax.synology.me/CtrlIA"
	assert actualizador.URL_MANIFIESTO == "https://xebolax.synology.me/CtrlIA/version.json"
	actualizador._comprobar_url(actualizador.URL_MANIFIESTO)  # https: no lanza
	assert actualizador.CLAVES_PUBLICAS_ED25519
	assert all(re.fullmatch(r"[0-9a-f]{64}", clave) for clave in actualizador.CLAVES_PUBLICAS_ED25519)
	configuracion = actualizador.configuracion_por_defecto(version.APP_VERSION)
	# Dos fuentes: primero el servidor propio y después GitHub.
	assert configuracion.urls_manifiesto == actualizador.URLS_MANIFIESTO
	assert actualizador.URLS_MANIFIESTO == (
		actualizador.URL_MANIFIESTO,
		"https://github.com/hxebolax/CtrlIA/releases/latest/download/version.json",
	)
	assert configuracion.claves_publicas == actualizador.CLAVES_PUBLICAS_ED25519


@pytest.mark.skipif(not CLAVE_PRIVADA.is_file(), reason="La clave privada solo está en el equipo que publica")
def test_la_clave_privada_firma_lo_que_acepta_el_programa(tmp_path):
	"""Un version.json firmado con la clave privada del equipo lo acepta el actualizador."""
	pytest.importorskip("cryptography")
	sys.path.insert(0, str(RAIZ / "tools"))
	try:
		import firmar_version
	finally:
		sys.path.remove(str(RAIZ / "tools"))
	assert firmar_version.clave_publica_hex(firmar_version.cargar_privada(CLAVE_PRIVADA)) in actualizador.CLAVES_PUBLICAS_ED25519
	manifiesto = {"version": "9.9.9", "file": "CtrlIA_v9.9.9.zip", "sha256": "0" * 64, "size": 10, "date": "2026-10-18"}
	ruta = tmp_path / "version.json"
	ruta.write_text(json.dumps(manifiesto), encoding="utf-8")
	firmar_version.firmar(CLAVE_PRIVADA, ruta)
	firmado = json.loads(ruta.read_text(encoding="utf-8"))
	actualizador.verificar_firma(firmado, actualizador.CLAVES_PUBLICAS_ED25519)
	firmado["version"] = "9.9.10"
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.verificar_firma(firmado, actualizador.CLAVES_PUBLICAS_ED25519)


def test_invitame_a_un_cafe_abre_paypal(app_wx, tmp_path, monkeypatch):
	import webbrowser

	from app.core.configuracion import Configuracion
	from app.services.almacen import Almacen
	from app.ui import avisos
	from app.ui.ventana_principal import MENUS, VentanaPrincipal

	ayuda = next(elementos for titulo, elementos in MENUS if titulo == "Ay&uda")
	claves = [elemento[0] for elemento in ayuda]
	assert claves.index("cafe") == claves.index("acerca") - 1, "justo encima de «Acerca de»"
	assert ayuda[claves.index("cafe")][1] == "Invítame a un &café si te gusta mi trabajo"
	abiertas: list[str] = []
	dichos: list[str] = []
	monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: abiertas.append(url) or True)
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"general.confirmar_salida": False, "general.abrir_ultima_sesion": False})
	ventana = VentanaPrincipal(config, None, almacen=Almacen(tmp_path / "v.db"), proveedores={})
	try:
		ventana.accion_invitar_cafe()
	finally:
		ventana.salir()
	assert abiertas == [version.URL_DONACIONES]
	assert abiertas[0].startswith("https://www.paypal.com/paypalme/hjbcdonaciones")
	assert dichos[-1] == "Gracias. Se ha abierto la página de donaciones en el navegador."


def test_el_verificador_de_firmas_carga():
	assert actualizador.comprobar_componente_firmas() is None
	assert "ValueError" in actualizador.comprobar_componente_firmas(("zz",))


def test_verificar_build_detecta_dll_del_abi_estable_que_no_carga(tmp_path):
	sys.path.insert(0, str(RAIZ / "tools"))
	try:
		import verificar_build
	finally:
		sys.path.remove(str(RAIZ / "tools"))
	extension = tmp_path / "lib" / "cryptography" / "_rust.pyd"
	extension.parent.mkdir(parents=True)
	extension.write_bytes(b"MZ...PYTHON3T.DLL...")
	assert "Falta python3t.dll" in verificar_build.problemas_abi_estable(tmp_path)[0]
	(tmp_path / "python3t.dll").write_bytes(b"MZ python315t.PyArg_Parse python315t.PyAIter_Check")
	problemas = verificar_build.problemas_abi_estable(tmp_path)
	assert len(problemas) == 1 and "reenvía a python315t.dll" in problemas[0]
	(tmp_path / "python3t.dll").write_bytes(b"MZ python315.PyArg_Parse")  # el de abi3t-compat
	(tmp_path / "python315.dll").write_bytes(b"MZ")
	assert verificar_build.problemas_abi_estable(tmp_path) == []
