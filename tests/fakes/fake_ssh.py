"""Imitación de ``ssh.exe`` para las pruebas de los agentes en otro equipo.

Recibe lo mismo que el ``ssh`` real tras las opciones (``-- destino 'exec "$SHELL" -l -c "guion"'``),
deshace las comillas como lo haría el shell del otro equipo (``shlex``), anota lo que habría
ejecutado y lanza en su lugar el programa falso que corresponda, con el entorno que pide el guion.

Variables:

- ``FAKE_SSH_REGISTRO``: archivo JSON lines donde se anota cada llamada.
- ``FAKE_SSH_PROGRAMAS``: JSON ``{"claude": ["python", "fake_claude.py"], ...}``.
"""

import json
import os
import shlex
import subprocess
import sys


def analizar(guion: str) -> dict:
	"""Parte el guion de Ctrl+IA: ``export PATH=...; cd carpeta && exec env -u X K=V programa args``."""
	partes = shlex.split(guion)
	carpeta = ""
	if "cd" in partes:
		carpeta = partes[partes.index("cd") + 1]
	resto = partes[partes.index("env") + 1:]
	quitar, anadir = [], {}
	while resto:
		if resto[0] == "-u":
			quitar.append(resto[1])
			resto = resto[2:]
		elif "=" in resto[0] and not resto[0].startswith(("/", "-")) and resto[0].split("=", 1)[0].isidentifier():
			nombre, valor = resto[0].split("=", 1)
			anadir[nombre] = valor
			resto = resto[1:]
		else:
			break
	return {"carpeta": carpeta, "quitar": quitar, "anadir": anadir, "programa": resto[0], "argumentos": resto[1:]}


def main() -> int:
	argumentos = sys.argv[1:]
	separador = argumentos.index("--")
	destino, orden = argumentos[separador + 1], argumentos[separador + 2]
	exterior = shlex.split(orden)
	assert exterior[:4] == ["exec", "$SHELL", "-l", "-c"], exterior
	datos = analizar(exterior[4])
	datos["destino"] = destino
	ruta = os.environ.get("FAKE_SSH_REGISTRO")
	if ruta:
		with open(ruta, "a", encoding="utf-8") as archivo:
			archivo.write(json.dumps(datos, ensure_ascii=False) + "\n")
	programas = json.loads(os.environ.get("FAKE_SSH_PROGRAMAS", "{}"))
	base = programas.get(os.path.basename(datos["programa"]))
	if base is None:
		sys.stderr.write(f"sh: {datos['programa']}: command not found\n")
		return 127
	entorno = {k: v for k, v in os.environ.items() if k not in datos["quitar"]}
	entorno.update(datos["anadir"])
	return subprocess.call([*base, *datos["argumentos"]], env=entorno)


if __name__ == "__main__":
	sys.exit(main())
