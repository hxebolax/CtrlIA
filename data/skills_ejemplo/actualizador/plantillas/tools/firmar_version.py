"""Firma Ed25519 del manifiesto de actualización (``version.json``).

Por qué firmar además del SHA-256: el SHA-256 del manifiesto solo demuestra que el
ZIP no se corrompió por el camino. Si alguien entra en el servidor, cambia el ZIP
Y el SHA-256 a la vez. Con la firma necesitaría además la clave PRIVADA, que no
está en el servidor ni en el repositorio.

Uso (desde la raíz del proyecto):

	python tools/firmar_version.py generar
		Crea el par de claves UNA sola vez. Guarda la privada en tools/claves/
		(con un .gitignore que la excluye del control de versiones) e imprime la
		PÚBLICA para pegarla en CLAVES_PUBLICAS_ED25519 de
		app/services/actualizador.py.

	python tools/firmar_version.py firmar build/version.json [--novedades NOVEDADES.txt]
		Añade (opcionalmente) las novedades y el campo «firma_ed25519». Firma el
		manifiesto COMPLETO con una serialización canónica, de modo que cambiar
		cualquier campo (versión, sha256, tamaño, novedades, url) invalida la firma.

	python tools/firmar_version.py verificar build/version.json [--publica HEX ...]
		Comprueba la firma (con la clave pública indicada o la derivada de la
		privada) y, si el ZIP está junto al manifiesto, su tamaño y su SHA-256.

Si la variable de entorno FIRMA_CONTRASENA está definida, la clave privada se
cifra al generarla y se descifra al usarla.

La función ``firmar(ruta_clave, ruta_json)`` es la que llama ``tools/empaquetar.py``.

PLANTILLA del agente «actualizador».
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CARPETA_CLAVES = RAIZ / "tools" / "claves"
CLAVE_POR_DEFECTO = CARPETA_CLAVES / "firma_ed25519.key"
CAMPO_FIRMA = "firma_ed25519"
VARIABLE_CONTRASENA = "FIRMA_CONTRASENA"
CAMPOS_OBLIGATORIOS = ("version", "file", "sha256", "size")


def serializar_canonico(manifiesto: dict) -> bytes:
	"""Bytes que se firman: JSON ordenado, separadores fijos y sin el campo de firma.

	Debe ser IDÉNTICA a ``serializar_canonico`` de ``app/services/actualizador.py``.
	"""
	datos = {clave: valor for clave, valor in manifiesto.items() if clave != CAMPO_FIRMA}
	return json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _contrasena() -> bytes | None:
	valor = os.environ.get(VARIABLE_CONTRASENA, "")
	return valor.encode("utf-8") if valor else None


def _proteger_carpeta(carpeta: Path) -> None:
	"""Deja un .gitignore que excluye todo el contenido de la carpeta de claves."""
	carpeta.mkdir(parents=True, exist_ok=True)
	ignorar = carpeta / ".gitignore"
	if not ignorar.exists():
		ignorar.write_text(
			"# Claves privadas de firma: NUNCA al control de versiones ni al servidor.\n*\n",
			encoding="utf-8",
		)


def generar(ruta_privada: Path | str = CLAVE_POR_DEFECTO, forzar: bool = False) -> str:
	"""Genera un par Ed25519, guarda la privada (PEM PKCS#8) y devuelve la pública en hex.

	No sobrescribe una clave existente salvo con ``forzar``: perder la clave con la
	que ya se firmaron versiones obliga a una rotación (ver firma-y-seguridad.md).
	"""
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

	ruta = Path(ruta_privada)
	if ruta.exists() and not forzar:
		raise FileExistsError(f"Ya existe la clave {ruta}. No se sobrescribe (use --forzar solo si sabe lo que hace).")
	_proteger_carpeta(ruta.parent)
	privada = Ed25519PrivateKey.generate()
	contrasena = _contrasena()
	cifrado = serialization.BestAvailableEncryption(contrasena) if contrasena else serialization.NoEncryption()
	ruta.write_bytes(privada.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, cifrado))
	return clave_publica_hex(privada)


def cargar_privada(ruta_privada: Path | str):
	"""Carga la clave privada (PEM, cifrada o no, o 32 bytes en bruto)."""
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

	datos = Path(ruta_privada).read_bytes()
	if len(datos) == 32:
		return Ed25519PrivateKey.from_private_bytes(datos)
	clave = serialization.load_pem_private_key(datos, password=_contrasena())
	if not isinstance(clave, Ed25519PrivateKey):
		raise ValueError("La clave no es Ed25519.")
	return clave


def clave_publica_hex(privada) -> str:
	"""Clave pública en hexadecimal (64 caracteres), como la espera el actualizador."""
	from cryptography.hazmat.primitives import serialization

	return privada.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def firmar(ruta_privada: Path | str, ruta_json: Path | str, novedades: Path | str | None = None) -> str:
	"""Firma ``version.json`` en su sitio y devuelve la firma en base64."""
	ruta = Path(ruta_json)
	manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
	faltan = [campo for campo in CAMPOS_OBLIGATORIOS if not manifiesto.get(campo)]
	if faltan:
		raise ValueError(f"Al manifiesto le faltan campos: {', '.join(faltan)}.")
	if novedades:
		manifiesto["novedades"] = Path(novedades).read_text(encoding="utf-8").strip()
	manifiesto.pop(CAMPO_FIRMA, None)
	privada = cargar_privada(ruta_privada)
	firma = base64.b64encode(privada.sign(serializar_canonico(manifiesto))).decode("ascii")
	manifiesto[CAMPO_FIRMA] = firma
	ruta.write_text(json.dumps(manifiesto, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
	return firma


def verificar(ruta_json: Path | str, claves_publicas: list[str], comprobar_zip: bool = True) -> list[str]:
	"""Devuelve los problemas encontrados (lista vacía si todo está bien)."""
	from cryptography.exceptions import InvalidSignature
	from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

	ruta = Path(ruta_json)
	problemas: list[str] = []
	manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
	firma_b64 = manifiesto.get(CAMPO_FIRMA)
	if not firma_b64:
		return ["El manifiesto no está firmado."]
	try:
		firma = base64.b64decode(firma_b64, validate=True)
	except (binascii.Error, ValueError):
		return ["La firma no es base64 válido."]
	mensaje = serializar_canonico(manifiesto)
	valida = False
	for clave_hex in claves_publicas:
		try:
			Ed25519PublicKey.from_public_bytes(bytes.fromhex(clave_hex)).verify(firma, mensaje)
			valida = True
			break
		except (InvalidSignature, ValueError):
			continue
	if not valida:
		problemas.append("La firma NO es válida con ninguna de las claves públicas indicadas.")
	zip_ = ruta.parent / str(manifiesto.get("file", ""))
	if comprobar_zip and zip_.is_file():
		if zip_.stat().st_size != manifiesto.get("size"):
			problemas.append(f"El tamaño de {zip_.name} no coincide con el manifiesto.")
		resumen = hashlib.sha256()
		with zip_.open("rb") as archivo:
			for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
				resumen.update(bloque)
		if not hmac.compare_digest(resumen.hexdigest(), str(manifiesto.get("sha256", "")).lower()):
			problemas.append(f"El SHA-256 de {zip_.name} no coincide con el manifiesto.")
	elif comprobar_zip:
		problemas.append(f"Aviso: no se encuentra {zip_.name} junto al manifiesto; no se ha comprobado su SHA-256.")
	return problemas


def main(argumentos: list[str] | None = None) -> int:
	"""Interfaz de línea de órdenes: generar, firmar y verificar."""
	analizador = argparse.ArgumentParser(description="Firma Ed25519 del manifiesto version.json.")
	ordenes = analizador.add_subparsers(dest="orden", required=True)
	o_generar = ordenes.add_parser("generar", help="Crea el par de claves (una sola vez).")
	o_generar.add_argument("--clave", default=str(CLAVE_POR_DEFECTO), help="Ruta de la clave privada.")
	o_generar.add_argument("--forzar", action="store_true", help="Sobrescribe una clave existente (¡peligroso!).")
	o_firmar = ordenes.add_parser("firmar", help="Firma un version.json.")
	o_firmar.add_argument("manifiesto")
	o_firmar.add_argument("--clave", default=str(CLAVE_POR_DEFECTO))
	o_firmar.add_argument("--novedades", help="Archivo de texto con las novedades de esta versión.")
	o_verificar = ordenes.add_parser("verificar", help="Comprueba la firma y el ZIP.")
	o_verificar.add_argument("manifiesto")
	o_verificar.add_argument("--publica", action="append", default=[], help="Clave pública en hex (repetible).")
	o_verificar.add_argument(
		"--clave", default=str(CLAVE_POR_DEFECTO), help="Clave privada de la que derivar la pública."
	)
	args = analizador.parse_args(argumentos)
	try:
		if args.orden == "generar":
			publica = generar(args.clave, forzar=args.forzar)
			print(f"Clave privada guardada en: {args.clave}")
			print("  NO la suba al repositorio ni al servidor. Guarde una copia fuera del equipo")
			print("  (sin ella no podrá publicar versiones que acepten las instalaciones actuales).")
			print("Clave pública (péguela en CLAVES_PUBLICAS_ED25519 de app/services/actualizador.py):")
			print(f'  CLAVES_PUBLICAS_ED25519 = ("{publica}",)')
			return 0
		if args.orden == "firmar":
			firmar(args.clave, args.manifiesto, args.novedades)
			print(f"Firmado: {args.manifiesto}")
			return 0
		publicas = list(args.publica) or [clave_publica_hex(cargar_privada(args.clave))]
		problemas = verificar(args.manifiesto, publicas)
		for problema in problemas:
			print(problema)
		graves = [p for p in problemas if not p.startswith("Aviso:")]
		if not graves:
			print("Firma válida.")
		return 1 if graves else 0
	except (OSError, ValueError, TypeError) as error:
		print(f"ERROR: {error}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())
