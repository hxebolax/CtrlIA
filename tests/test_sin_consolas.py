"""Ningún proceso que lanza Ctrl+IA abre una ventana de consola sin querer.

Una consola que aparece a mitad de un turno le quita el foco al lector de
pantalla y lo hace hablar. Esta prueba lee el código con ``ast`` y exige que
cada ``subprocess.run``, ``Popen``, ``check_output``, ``check_call`` o ``call``
lleve ``creationflags`` (o un ``**opciones`` que lo traiga). Las excepciones
están en :data:`EXENTAS`, cada una con su motivo.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUNCIONES = {"run", "Popen", "check_output", "check_call", "call"}

#: (archivo relativo con «/», función que contiene la llamada) → motivo.
EXENTAS: dict[tuple[str, str], str] = {
	("app/ui/ventana_principal.py", "hecho"):
		"abre el Explorador de archivos, que es una ventana normal y no una consola",
}


def _es_subprocess(nodo: ast.expr) -> bool:
	"""``subprocess`` o algo que acaba en ``.subprocess`` (``procesos.subprocess``)."""
	return (isinstance(nodo, ast.Name) and nodo.id == "subprocess") or (
		isinstance(nodo, ast.Attribute) and nodo.attr == "subprocess")


def _llamadas_sin_bandera(ruta: Path) -> list[tuple[str, int]]:
	arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
	encontradas: list[tuple[str, int]] = []

	def visitar(nodo: ast.AST, funcion: str) -> None:
		for hijo in ast.iter_child_nodes(nodo):
			nombre = hijo.name if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)) else funcion
			if isinstance(hijo, ast.Call) and isinstance(hijo.func, ast.Attribute) \
					and hijo.func.attr in FUNCIONES and _es_subprocess(hijo.func.value):
				claves = {k.arg for k in hijo.keywords}
				if "creationflags" not in claves and None not in claves:
					encontradas.append((funcion, hijo.lineno))
			visitar(hijo, nombre)

	visitar(arbol, "<módulo>")
	return encontradas


def test_todos_los_procesos_se_lanzan_sin_consola():
	problemas = []
	for ruta in sorted((RAIZ / "app").rglob("*.py")):
		relativa = ruta.relative_to(RAIZ).as_posix()
		for funcion, linea in _llamadas_sin_bandera(ruta):
			if (relativa, funcion) not in EXENTAS:
				problemas.append(f"{relativa}:{linea} (en {funcion})")
	assert not problemas, "Lanzan un proceso sin creationflags (podría abrir una consola):\n" + "\n".join(problemas)


def test_las_excepciones_siguen_existiendo():
	"""Una excepción que ya no corresponde a ninguna llamada se quita de la lista."""
	for (relativa, funcion), _motivo in EXENTAS.items():
		ruta = RAIZ / relativa
		assert ruta.is_file(), relativa
		assert funcion in {f for f, _l in _llamadas_sin_bandera(ruta)}, f"{relativa}: {funcion}"
