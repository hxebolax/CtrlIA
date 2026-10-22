"""Firma Ed25519 del manifiesto: herramienta y verificación en la aplicación.

PLANTILLA del agente «actualizador». Necesita el paquete ``cryptography``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("cryptography")

from apoyo_actualizador import RAIZ, cargar_firmador, publicar  # noqa: E402

from app.services import actualizador  # noqa: E402


@pytest.fixture
def firmador():
	return cargar_firmador()


@pytest.fixture
def clave(tmp_path, firmador, monkeypatch):
	monkeypatch.delenv(firmador.VARIABLE_CONTRASENA, raising=False)
	ruta = tmp_path / "claves" / "firma.key"
	publica = firmador.generar(ruta)
	return ruta, publica


def _manifiesto_firmado(tmp_path, clave, **extra):
	ruta, _publica = clave
	servidor = tmp_path / "servidor"
	servidor.mkdir(exist_ok=True)
	return publicar(servidor, "1.2.0", {"MiAplicacion.exe": b"exe"}, ruta, novedades="- Novedad", extra=extra)


def test_generar_protege_la_carpeta_y_no_sobrescribe(tmp_path, firmador, clave):
	ruta, publica = clave
	assert len(publica) == 64 and int(publica, 16) >= 0
	assert "PRIVATE KEY" in ruta.read_text(encoding="ascii")
	assert (ruta.parent / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"
	with pytest.raises(FileExistsError):
		firmador.generar(ruta)
	assert firmador.generar(ruta, forzar=True) != publica


def test_clave_cifrada_con_contrasena(tmp_path, firmador, monkeypatch):
	monkeypatch.setenv(firmador.VARIABLE_CONTRASENA, "una frase larga")
	ruta = tmp_path / "cifrada.key"
	publica = firmador.generar(ruta)
	assert "ENCRYPTED" in ruta.read_text(encoding="ascii")
	assert firmador.clave_publica_hex(firmador.cargar_privada(ruta)) == publica
	monkeypatch.setenv(firmador.VARIABLE_CONTRASENA, "otra")
	with pytest.raises(ValueError):
		firmador.cargar_privada(ruta)


def test_firma_valida_se_acepta(tmp_path, clave):
	manifiesto = _manifiesto_firmado(tmp_path, clave)
	assert manifiesto["novedades"] == "- Novedad"
	actualizador.verificar_firma(manifiesto, (clave[1],))


def test_serializacion_canonica_identica(tmp_path, firmador, clave):
	manifiesto = _manifiesto_firmado(tmp_path, clave)
	assert firmador.serializar_canonico(manifiesto) == actualizador.serializar_canonico(manifiesto)
	desordenado = dict(reversed(list(manifiesto.items())))
	assert actualizador.serializar_canonico(desordenado) == actualizador.serializar_canonico(manifiesto)
	assert b"firma_ed25519" not in actualizador.serializar_canonico(manifiesto)


@pytest.mark.parametrize(
	("campo", "valor"),
	[
		("version", "9.9.9"),
		("sha256", "0" * 64),
		("size", 1),
		("novedades", "Otra cosa"),
		("url", "https://malo.invalid/x.zip"),
	],
)
def test_manifiesto_manipulado_se_rechaza(tmp_path, clave, campo, valor):
	manifiesto = _manifiesto_firmado(tmp_path, clave)
	manifiesto[campo] = valor
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.verificar_firma(manifiesto, (clave[1],))


def test_sin_firma_firma_rota_u_otra_clave(tmp_path, firmador, clave):
	manifiesto = _manifiesto_firmado(tmp_path, clave)
	otra = firmador.generar(tmp_path / "otra.key")
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.verificar_firma(manifiesto, (otra,))
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.verificar_firma({**manifiesto, "firma_ed25519": "no es base64!"}, (clave[1],))
	sin_firma = {k: v for k, v in manifiesto.items() if k != "firma_ed25519"}
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.verificar_firma(sin_firma, (clave[1],))
	with pytest.raises(actualizador.ErrorFirma):
		actualizador.verificar_firma(manifiesto, ())


def test_rotacion_acepta_cualquiera_de_las_claves(tmp_path, firmador, clave):
	manifiesto = _manifiesto_firmado(tmp_path, clave)
	nueva = firmador.generar(tmp_path / "nueva.key")
	actualizador.verificar_firma(manifiesto, (nueva, "zz-mal-formada", clave[1]))


def test_clave_en_bruto_heredada(tmp_path, firmador):
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

	privada = Ed25519PrivateKey.generate()
	ruta = tmp_path / "bruto.key"
	ruta.write_bytes(
		privada.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
	)
	servidor = tmp_path / "servidor"
	servidor.mkdir()
	manifiesto = publicar(servidor, "1.0.1", {"MiAplicacion.exe": b"x"}, ruta)
	actualizador.verificar_firma(manifiesto, (firmador.clave_publica_hex(privada),))


def test_linea_de_ordenes(tmp_path):
	herramienta = str(RAIZ / "tools" / "firmar_version.py")
	clave = tmp_path / "cli" / "firma.key"

	def ejecutar(*argumentos):
		entorno = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # salida con tildes aunque no sea una consola
		return subprocess.run(
			[sys.executable, herramienta, *argumentos],
			capture_output=True,
			text=True,
			encoding="utf-8",
			env=entorno,
			timeout=60,
		)

	generado = ejecutar("generar", "--clave", str(clave))
	assert generado.returncode == 0, generado.stderr
	assert "CLAVES_PUBLICAS_ED25519" in generado.stdout
	assert ejecutar("generar", "--clave", str(clave)).returncode == 1  # no sobrescribe

	servidor = tmp_path / "servidor"
	servidor.mkdir()
	publicar(servidor, "2.0.0", {"MiAplicacion.exe": b"exe"}, None)
	manifiesto = servidor / "version.json"
	assert ejecutar("firmar", str(manifiesto), "--clave", str(clave)).returncode == 0
	verificado = ejecutar("verificar", str(manifiesto), "--clave", str(clave))
	assert verificado.returncode == 0, verificado.stdout
	assert "Firma válida" in verificado.stdout

	datos = json.loads(manifiesto.read_text(encoding="utf-8"))
	datos["version"] = "2.0.1"
	manifiesto.write_text(json.dumps(datos), encoding="utf-8")
	assert ejecutar("verificar", str(manifiesto), "--clave", str(clave)).returncode == 1

	(servidor / datos["file"]).write_bytes(b"zip cambiado")
	datos["version"] = "2.0.0"
	manifiesto.write_text(json.dumps(datos), encoding="utf-8")
	resultado = ejecutar("verificar", str(manifiesto), "--clave", str(clave))
	assert resultado.returncode == 1 and "SHA-256" in resultado.stdout
