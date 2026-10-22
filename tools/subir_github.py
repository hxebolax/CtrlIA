"""Sube a GitHub los cambios del código de Ctrl+IA (remoto ``origin``, rama ``main``).

Uso (desde la raíz del proyecto, o con ``subir_github.bat``)::

	python tools/subir_github.py                        # mensaje automático con la fecha
	python tools/subir_github.py Arreglo del menú Ver   # mensaje propio
	python tools/subir_github.py --ensayo               # dice qué haría, sin tocar nada

Pasos:

1. Comprueba que la carpeta es un repositorio git, en la rama ``main`` y con el
   remoto ``origin``.
2. Mira qué ha cambiado (respetando ``.gitignore``) y, ANTES de añadir nada,
   comprueba que no se cuela nada prohibido aunque el ``.gitignore`` fallase:
   claves (``tools/claves``, ``*.key``, ``*.pem``), configuración local de
   publicación (``publicar.local.json``), material de consulta (``adjuntos``,
   ``docs/auditorias``), configuración de agentes (``.agents``, ``.claude``,
   ``AGENTS.md``, ``CLAUDE.md``, ``.agentes-instalados.json``), datos
   personales de ``data`` (config, db, logs, cache, temp, backups, agentes,
   diagnostico, ollama, sessions, downloads y sounds/propios), compilaciones
   (``build``), el entorno virtual (``.venv``), cachés (``.ruff_cache``,
   ``.pytest_cache``), ``requirements-lock.txt`` y archivos de más de 50 MB.
   Si encuentra algo, se para y lo dice.
3. ``git add -A`` y ``git commit`` con el mensaje indicado o uno automático
   («Actualización del 22/10/2026: 5 archivos cambiados»).
4. ``git push origin main``.

Si no hay cambios ni commits pendientes de subir, lo dice y no hace nada.

``tools/publicar.py`` reutiliza este módulo (ejecutor de órdenes, comprobación
del repositorio y de archivos prohibidos).

Solo biblioteca estándar.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
REMOTO = "origin"
RAMA = "main"
LIMITE_TAMANO = 50 * 1024 * 1024
COAUTOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

# Rutas prohibidas (en minúsculas y con barras normales).
PREFIJOS_PROHIBIDOS = {
	"tools/claves/": "clave privada de firma",
	"adjuntos/": "material de consulta (carpeta adjuntos)",
	"build/": "compilación (carpeta build)",
	"data/config/": "configuración personal (data/config)",
	"data/db/": "base de datos personal (data/db)",
	"data/logs/": "registros personales (data/logs)",
	"data/cache/": "caché personal (data/cache)",
	"data/temp/": "archivos temporales (data/temp)",
	"data/backups/": "copias de seguridad personales (data/backups)",
	"data/agentes/": "catálogo personal de agentes (data/agentes)",
	"data/diagnostico/": "diagnósticos personales (data/diagnostico)",
	"data/ollama/": "datos personales de Ollama (data/ollama)",
	"data/sessions/": "sesiones personales (data/sessions)",
	"data/downloads/": "descargas personales (data/downloads)",
	"data/sounds/propios/": "sonidos propios de la persona (data/sounds/propios)",
	"docs/auditorias/": "auditorías internas (docs/auditorias)",
	"requirements-lock.txt": "versiones instaladas en este equipo (requirements-lock.txt)",
}
CARPETAS_PROHIBIDAS = {
	".venv": "entorno virtual (.venv)",
	".agents": "configuración de agentes (.agents)",
	".claude": "configuración de agentes (.claude)",
	".ruff_cache": "caché de ruff (.ruff_cache)",
	".pytest_cache": "caché de pytest (.pytest_cache)",
}
NOMBRES_PROHIBIDOS = {
	"agents.md": "instrucciones para agentes (AGENTS.md)",
	"claude.md": "instrucciones para agentes (CLAUDE.md)",
	".agentes-instalados.json": "lista local de agentes instalados (.agentes-instalados.json)",
	"publicar.local.json": "configuración local de publicación con rutas personales (publicar.local.json)",
}
EXTENSIONES_PROHIBIDAS = {
	".key": "clave privada (.key)",
	".pem": "clave o certificado (.pem)",
}


class ErrorPublicacion(Exception):
	"""Fallo que detiene la subida o la publicación; el texto ya está en español y es para la persona."""


# ── Ejecución de órdenes externas ────────────────────────────────────────────

@dataclass
class Resultado:
	codigo: int
	salida: str = ""
	error: str = ""

	@property
	def correcto(self) -> bool:
		return self.codigo == 0


@dataclass
class Ejecutor:
	"""Ejecuta órdenes externas (git, gh, entorno.bat). Las pruebas lo sustituyen por uno simulado."""

	cwd: Path = RAIZ
	registro: list[list[str]] = field(default_factory=list)

	def __call__(self, orden: Sequence[str], *, entrada: str | None = None, capturar: bool = True) -> Resultado:
		orden = [str(parte) for parte in orden]
		self.registro.append(orden)
		try:
			proceso = subprocess.run(
				orden,
				cwd=self.cwd,
				input=entrada,
				capture_output=capturar,
				text=True,
				encoding="utf-8",
				errors="replace",
				check=False,
			)
		except FileNotFoundError:
			return Resultado(127, "", f"No se encuentra el programa «{orden[0]}».")
		except OSError as error:
			return Resultado(126, "", f"No se pudo ejecutar «{orden[0]}»: {error}")
		return Resultado(proceso.returncode, proceso.stdout or "", proceso.stderr or "")


def decir(texto: str = "") -> None:
	"""Escribe una línea y la vuelca enseguida (las órdenes externas escriben en la misma consola)."""
	print(texto, flush=True)


def preparar_consola() -> None:
	"""Evita que un carácter raro en la consola de Windows tire el script."""
	for flujo in (sys.stdout, sys.stderr):
		if hasattr(flujo, "reconfigure"):
			flujo.reconfigure(errors="replace")


def detalle(resultado: Resultado) -> str:
	"""Última información útil de una orden fallida, en una línea."""
	texto = (resultado.error or resultado.salida).strip()
	return " ".join(texto.splitlines()[-3:]) if texto else f"código de salida {resultado.codigo}"


# ── Repositorio ──────────────────────────────────────────────────────────────

def comprobar_repositorio(ejecutar: Ejecutor, raiz: Path = RAIZ) -> None:
	"""Comprueba git, el repositorio, la rama ``main`` y el remoto ``origin``; si no, ``ErrorPublicacion``."""
	if isinstance(ejecutar, Ejecutor) and shutil.which("git") is None:
		raise ErrorPublicacion("No se encuentra git. Instala Git para Windows y vuelve a intentarlo.")
	dentro = ejecutar(["git", "rev-parse", "--show-toplevel"])
	if not dentro.correcto:
		raise ErrorPublicacion(
			f"La carpeta {raiz} todavía no es un repositorio git. Créelo primero (git init, "
			"primer commit y remoto origin apuntando a GitHub) y vuelve a intentarlo."
		)
	superior = Path(dentro.salida.strip())
	try:
		mismo = superior.resolve() == raiz.resolve()
	except OSError:
		mismo = False
	if not mismo:
		raise ErrorPublicacion(
			f"El repositorio git empieza en {superior}, no en la carpeta del proyecto {raiz}. "
			"Revisa dónde se hizo git init."
		)
	rama = ejecutar(["git", "branch", "--show-current"]).salida.strip()
	if rama != RAMA:
		raise ErrorPublicacion(
			f"Está en la rama «{rama or 'ninguna (HEAD separado)'}» y solo se publica desde «{RAMA}». "
			f"Cambia con: git switch {RAMA}"
		)
	remoto = ejecutar(["git", "remote", "get-url", REMOTO])
	if not remoto.correcto or not remoto.salida.strip():
		raise ErrorPublicacion(
			f"El repositorio no tiene el remoto «{REMOTO}». Añádalo con: "
			"git remote add origin https://github.com/hxebolax/CtrlIA.git"
		)


@dataclass
class Cambio:
	estado: str  # Dos letras de git status --porcelain (por ejemplo « M», «??», « D», «R »).
	ruta: str  # Ruta relativa con barras normales.

	@property
	def borrado(self) -> bool:
		return "D" in self.estado

	@property
	def nuevo(self) -> bool:
		return self.estado == "??" or "A" in self.estado

	@property
	def tipo(self) -> str:
		if self.borrado:
			return "borrado"
		if self.nuevo:
			return "nuevo"
		if "R" in self.estado:
			return "renombrado"
		return "modificado"


def analizar_estado(salida: str) -> list[Cambio]:
	"""Interpreta ``git status --porcelain=v1 -z`` (los renombrados traen también la ruta antigua)."""
	cambios: list[Cambio] = []
	partes = salida.split("\0")
	indice = 0
	while indice < len(partes):
		entrada = partes[indice]
		indice += 1
		if len(entrada) < 4:
			continue
		estado, ruta = entrada[:2], entrada[3:]
		if "R" in estado or "C" in estado:
			indice += 1  # La ruta de origen del renombrado; no se añade nada nuevo con ella.
		cambios.append(Cambio(estado, ruta.replace("\\", "/")))
	return cambios


def cambios_pendientes(ejecutar: Ejecutor) -> list[Cambio]:
	"""Cambios sin guardar en un commit, ya filtrados por ``.gitignore``."""
	resultado = ejecutar(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"])
	if not resultado.correcto:
		raise ErrorPublicacion(f"No se pudo leer el estado del repositorio: {detalle(resultado)}")
	return analizar_estado(resultado.salida)


def archivos_seguidos(ejecutar: Ejecutor) -> list[str]:
	"""Archivos que el repositorio ya guarda (se subirían también con el push)."""
	resultado = ejecutar(["git", "ls-files", "-z"])
	if not resultado.correcto:
		raise ErrorPublicacion(f"No se pudo listar los archivos del repositorio: {detalle(resultado)}")
	return [ruta.replace("\\", "/") for ruta in resultado.salida.split("\0") if ruta]


def motivo_prohibido(ruta: str, raiz: Path = RAIZ, limite: int = LIMITE_TAMANO) -> str | None:
	"""Motivo por el que ``ruta`` (relativa a la raíz) no puede subirse, o ``None`` si se puede."""
	normal = ruta.replace("\\", "/").strip("/")
	minus = normal.lower()
	for prefijo, motivo in PREFIJOS_PROHIBIDOS.items():
		if minus == prefijo.rstrip("/") or minus.startswith(prefijo):
			return motivo
	componentes = minus.split("/")
	for carpeta, motivo in CARPETAS_PROHIBIDAS.items():
		if carpeta in componentes[:-1] or componentes[-1] == carpeta:
			return motivo
	if componentes[-1] in NOMBRES_PROHIBIDOS:
		return NOMBRES_PROHIBIDOS[componentes[-1]]
	for extension, motivo in EXTENSIONES_PROHIBIDAS.items():
		if minus.endswith(extension):
			return motivo
	try:
		tamano = (raiz / normal).stat().st_size
	except OSError:
		return None
	if tamano > limite:
		return f"archivo de {tamano / (1024 * 1024):.0f} MB (el límite es {limite // (1024 * 1024)} MB)"
	return None


def buscar_prohibidos(ejecutar: Ejecutor, raiz: Path = RAIZ, cambios: list[Cambio] | None = None) -> list[str]:
	"""Rutas prohibidas que acabarían en GitHub: lo ya guardado y lo que se añadiría ahora."""
	cambios = cambios_pendientes(ejecutar) if cambios is None else cambios
	borrados = {cambio.ruta for cambio in cambios if cambio.borrado}
	candidatas = dict.fromkeys(archivos_seguidos(ejecutar))
	candidatas.update(dict.fromkeys(cambio.ruta for cambio in cambios if not cambio.borrado))
	problemas = []
	for ruta in candidatas:
		if ruta in borrados:
			continue
		motivo = motivo_prohibido(ruta, raiz)
		if motivo:
			problemas.append(f"{ruta}: {motivo}")
	return problemas


def plural(cantidad: int, singular: str, plural_: str) -> str:
	return f"{cantidad} {singular if cantidad == 1 else plural_}"


def resumen_cambios(cambios: list[Cambio]) -> str:
	"""«2 nuevos, 3 modificados y 1 borrado»."""
	cuentas: dict[str, int] = {}
	for cambio in cambios:
		cuentas[cambio.tipo] = cuentas.get(cambio.tipo, 0) + 1
	nombres = {
		"nuevo": ("nuevo", "nuevos"),
		"modificado": ("modificado", "modificados"),
		"renombrado": ("renombrado", "renombrados"),
		"borrado": ("borrado", "borrados"),
	}
	partes = [plural(cuentas[tipo], *nombres[tipo]) for tipo in nombres if cuentas.get(tipo)]
	if len(partes) > 1:
		return ", ".join(partes[:-1]) + " y " + partes[-1]
	return partes[0] if partes else "sin cambios"


def mensaje_automatico(cambios: list[Cambio], dia: date | None = None) -> str:
	"""«Actualización del 22/10/2026: 5 archivos cambiados», con el detalle en el cuerpo."""
	dia = dia or date.today()
	titulo = f"Actualización del {dia:%d/%m/%Y}: {plural(len(cambios), 'archivo cambiado', 'archivos cambiados')}"
	return f"{titulo}\n\n{resumen_cambios(cambios).capitalize()}.\n"


def componer_mensaje(mensaje: str | None, cambios: list[Cambio], *, agente: bool = False, dia: date | None = None) -> str:
	"""Mensaje del commit; con ``agente`` termina con la línea de coautoría de Claude."""
	texto = mensaje.strip() + "\n" if mensaje and mensaje.strip() else mensaje_automatico(cambios, dia)
	if agente:
		texto = texto.rstrip("\n") + f"\n\n{COAUTOR}\n"
	return texto


def commits_sin_subir(ejecutar: Ejecutor) -> int | None:
	"""Commits locales que aún no están en ``origin/main``; ``None`` si la rama remota no existe todavía."""
	if not ejecutar(["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/{REMOTO}/{RAMA}"]).correcto:
		return None
	resultado = ejecutar(["git", "rev-list", "--count", f"{REMOTO}/{RAMA}..{RAMA}"])
	try:
		return int(resultado.salida.strip()) if resultado.correcto else None
	except ValueError:
		return None


def hay_commits(ejecutar: Ejecutor) -> bool:
	return ejecutar(["git", "rev-parse", "--verify", "--quiet", "HEAD"]).correcto


# ── Flujo completo ───────────────────────────────────────────────────────────

def subir(
	ejecutar: Ejecutor,
	*,
	raiz: Path = RAIZ,
	mensaje: str | None = None,
	agente: bool = False,
	ensayo: bool = False,
	salida: Callable[[str], None] = decir,
	dia: date | None = None,
) -> bool:
	"""Guarda en un commit y sube a GitHub. Devuelve ``True`` si se subió (o se subiría) algo.

	:raises ErrorPublicacion: con la explicación del primer fallo.
	"""
	prefijo = "[ensayo] " if ensayo else ""
	salida("Comprobando el repositorio git...")
	comprobar_repositorio(ejecutar, raiz)
	salida(f"Correcto: repositorio git en la rama {RAMA} con el remoto {REMOTO}.")

	cambios = cambios_pendientes(ejecutar)
	salida("Comprobando que no se sube nada prohibido (claves, datos personales, compilaciones...)...")
	prohibidos = buscar_prohibidos(ejecutar, raiz, cambios)
	if prohibidos:
		lineas = "\n".join(f"  - {problema}" for problema in prohibidos)
		raise ErrorPublicacion(
			f"{'Se ha' if len(prohibidos) == 1 else 'Se han'} encontrado "
			f"{plural(len(prohibidos), 'archivo que no debe subirse', 'archivos que no deben subirse')} "
			f"a GitHub:\n{lineas}\nAñádalos a .gitignore (y, si ya estaban guardados, quítelos con "
			"git rm --cached <ruta>) y vuelve a intentarlo. No se ha subido nada."
		)
	salida("Correcto: no hay archivos prohibidos.")

	if cambios:
		texto = componer_mensaje(mensaje, cambios, agente=agente, dia=dia)
		salida(f"Hay {plural(len(cambios), 'archivo cambiado', 'archivos cambiados')}: {resumen_cambios(cambios)}.")
		salida(f"{prefijo}Mensaje del commit: {texto.splitlines()[0]}")
		if not ensayo:
			resultado = ejecutar(["git", "add", "-A"])
			if not resultado.correcto:
				raise ErrorPublicacion(f"No se pudieron añadir los cambios: {detalle(resultado)}")
			resultado = ejecutar(["git", "commit", "--file=-"], entrada=texto)
			if not resultado.correcto:
				raise ErrorPublicacion(
					f"No se pudo hacer el commit: {detalle(resultado)}. Si git pide su nombre y correo, "
					'configúrelos con git config --global user.name "Su nombre" y git config --global user.email su@correo.'
				)
			salida("Correcto: commit hecho.")
		else:
			salida(f"{prefijo}Se ejecutaría: git add -A y git commit.")
	else:
		if not hay_commits(ejecutar):
			salida("No hay cambios ni commits: no se sube nada.")
			return False
		pendientes = commits_sin_subir(ejecutar)
		if pendientes == 0:
			salida("No hay cambios que subir: GitHub ya tiene todo. No se hace nada.")
			return False
		if pendientes is None:
			salida(f"No hay cambios nuevos, pero la rama {RAMA} aún no está en GitHub; se sube.")
		else:
			salida(f"No hay cambios nuevos, pero hay {plural(pendientes, 'commit', 'commits')} sin subir.")

	orden = ["git", "push", "--set-upstream", REMOTO, RAMA]
	if ensayo:
		salida(f"{prefijo}Se ejecutaría: {' '.join(orden)}")
		return True
	salida(f"Subiendo a GitHub ({REMOTO}, rama {RAMA})...")
	resultado = ejecutar(orden)
	if not resultado.correcto:
		raise ErrorPublicacion(
			f"No se pudo subir a GitHub: {detalle(resultado)}. El commit queda guardado en este equipo; "
			"cuando se resuelva el problema, vuelve a ejecutar la subida."
		)
	salida("Correcto: cambios subidos a GitHub.")
	return True


def main(argumentos: list[str] | None = None, ejecutar: Ejecutor | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Sube a GitHub los cambios del código (origin, rama main).")
	analizador.add_argument("mensaje", nargs="*", help="Mensaje del commit (si no se indica, uno automático con la fecha).")
	analizador.add_argument("--ensayo", "--dry-run", action="store_true", help="Dice lo que haría sin cambiar nada.")
	analizador.add_argument(
		"--agente", action="store_true", help="El commit lo prepara un agente de IA: añade la línea Co-Authored-By."
	)
	opciones = analizador.parse_args(argumentos)
	preparar_consola()
	ejecutar = ejecutar or Ejecutor(RAIZ)
	mensaje = " ".join(opciones.mensaje).strip() or None
	decir("Subir los cambios del código de Ctrl+IA a GitHub" + (" (ENSAYO: no se cambia nada)" if opciones.ensayo else ""))
	try:
		subir(ejecutar, mensaje=mensaje, agente=opciones.agente, ensayo=opciones.ensayo)
	except ErrorPublicacion as error:
		decir(f"ERROR: {error}")
		return 1
	decir("Terminado.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
