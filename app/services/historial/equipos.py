"""Sesiones que ya existen en otros equipos por SSH (un Mac o un Linux).

Las sesiones de Claude Code y los hilos de Codex de Windows se leen del disco
o del servidor de Codex. Las de un equipo de Herramientas, «Equipos por SSH»,
se leen por SSH y aparecen en la misma lista, con el equipo al que pertenecen.

Claude Code, sin instalar nada en el otro equipo (solo ``sh``, ``find``,
``stat``, ``head``, ``tail``, ``gzip`` y ``base64``, que traen macOS y Linux):

1. Inventario: una conexión lista el tamaño y la fecha de cada transcript
   (``~/.claude/projects/*/*.jsonl``) y de los metadatos de la aplicación de
   escritorio, y trae los archivos ``~/.claude/sessions/<pid>.json`` de los
   procesos vivos (estado trabajando o esperando).
2. Solo de los archivos nuevos o cambiados se trae el principio (64 KB) y el
   final (128 KB) de cada transcript, comprimidos: lo mismo que se lee de un
   transcript de Windows para la lista.
3. Al abrir una sesión, su transcript se copia a ``data/cache/equipos``; al
   seguirla en vivo solo se trae lo añadido (``tail -c +N``).

Codex: su propio servidor en el otro equipo (``codex app-server`` por SSH),
como las sesiones nuevas de ese equipo.

Reglas:

- Solo lectura: nada se escribe en el otro equipo.
- Las rutas de archivos van por la entrada estándar, nunca en la orden.
- Nunca bloquea la lista: cada equipo se lee en un hilo de fondo y la lista
  usa lo último leído. Un equipo apagado no vacía sus sesiones de golpe ni
  frena las de Windows; se vuelve a intentar más tarde.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import json
import logging
import subprocess
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.agentes import remoto
from app.services.historial import claude as historial_claude

log = logging.getLogger(__name__)

#: Cada cuánto se vuelve a leer un equipo (segundos); F5 lo fuerza antes.
INTERVALO = 30.0
#: Tras un fallo de conexión, cuánto se espera antes de volver a intentarlo.
INTERVALO_FALLO = 120.0
#: Cada cuánto, como mucho, se trae lo nuevo de una sesión abierta que se sigue en vivo.
INTERVALO_SEGUIMIENTO = 8.0
#: Transcripts más recientes que se tienen en cuenta por equipo.
MAX_TRANSCRIPTS = 400
#: Transcripts que se piden en cada conexión de trozos.
LOTE_TROZOS = 120
TAM_CABEZA = 64 * 1024
TAM_COLA = 128 * 1024
TIEMPO_INVENTARIO = 60
TIEMPO_TROZOS = 180
TIEMPO_COPIA = 300


# ── Guiones del otro equipo ──────────────────────────────────────────────────


def _orden_stat(sistema: str) -> str:
	"""``stat`` con tamaño, fecha (segundos) y ruta: BSD en macOS, GNU en Linux."""
	return "stat -c '%s %Y %n'" if sistema == "linux" else "stat -f '%z %m %N'"


def guion_inventario(sistema: str) -> str:
	"""Guion POSIX que lista transcripts, metadatos de escritorio y procesos vivos de Claude Code."""
	stat = _orden_stat(sistema)
	return "\n".join([
		'R="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"',
		'echo "@@proyectos"',
		f"[ -d \"$R/projects\" ] && find \"$R/projects\" -mindepth 2 -maxdepth 2 -type f -name '*.jsonl' -exec {stat} {{}} +",
		'echo "@@escritorio"',
		'for E in "$HOME/Library/Application Support/Claude/claude-code-sessions" '
		'"${XDG_CONFIG_HOME:-$HOME/.config}/Claude/claude-code-sessions"; do',
		f"  [ -d \"$E\" ] && find \"$E\" -type f -name 'local_*.json' -exec {stat} {{}} +",
		"done",
		'echo "@@vivos"',
		'for f in "$R"/sessions/*.json; do',
		'  [ -f "$f" ] || continue',
		'  p=$(basename "$f" .json)',
		"  case \"$p\" in ''|*[!0-9]*) continue;; esac",
		'  kill -0 "$p" 2>/dev/null || continue',
		"  printf '@@v %s\\n' \"$(base64 < \"$f\" | tr -d '\\n')\"",
		"done",
		'echo "@@codex"',
		'[ -d "${CODEX_HOME:-$HOME/.codex}/sessions" ] && echo si',
		'echo "@@fin"',
	])


#: Lee de la entrada estándar líneas «t ruta» (principio y final de un
#: transcript) o «c ruta» (archivo entero) y devuelve cada uno en base64.
GUION_TROZOS = "\n".join([
	"while IFS= read -r l; do",
	'  k=${l%% *}; f=${l#* }',
	"  [ -f \"$f\" ] || { printf '@@x %s\\n' \"$f\"; continue; }",
	'  if [ "$k" = t ]; then',
	"    printf '@@t %s %s\\n' \"$(wc -c < \"$f\" | tr -d ' ')\" \"$f\"",
	f"    head -c {TAM_CABEZA} \"$f\" | gzip -c | base64 | tr -d '\\n'; echo",
	f"    tail -c {TAM_COLA} \"$f\" | gzip -c | base64 | tr -d '\\n'; echo",
	"  else",
	"    printf '@@c %s\\n' \"$f\"",
	"    gzip -c < \"$f\" | base64 | tr -d '\\n'; echo",
	"  fi",
	"done",
])

#: Lee «desde ruta» de la entrada estándar y devuelve, comprimido, el archivo
#: a partir del byte ``desde``. Sale con 3 si no existe y con 4 si ahora es más
#: corto (se ha reescrito: hay que traerlo entero).
GUION_COPIA = "\n".join([
	"IFS=' ' read -r o f",
	'[ -f "$f" ] || exit 3',
	"n=$(wc -c < \"$f\" | tr -d ' ')",
	'[ "$n" -ge "$o" ] || exit 4',
	'tail -c +"$((o + 1))" "$f" | gzip -c',
])


# ── Ejecutar en el otro equipo ───────────────────────────────────────────────


def _ejecutar(equipo: remoto.EquipoSSH, guion: str, entrada: bytes, tiempo: float) -> tuple[int, bytes, str]:
	"""Ejecuta ``guion`` con ``sh`` en el otro equipo. Lanza ``ErrorRemoto`` si no hay conexión."""
	orden = remoto.orden_remota(equipo, "sh", ["-c", guion])
	banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0)
	try:
		resultado = subprocess.run(  # noqa: S603 - orden construida por la aplicación, sin shell
			orden, input=entrada, capture_output=True, timeout=tiempo, env=remoto.entorno_ssh(),
			creationflags=banderas, check=False,
		)
	except subprocess.TimeoutExpired as error:
		raise remoto.ErrorRemoto(f"«{equipo.nombre}» no ha respondido a tiempo.") from error
	except OSError as error:
		raise remoto.ErrorRemoto(f"No se pudo conectar con «{equipo.nombre}»: {error}") from error
	errores = resultado.stderr.decode("utf-8", "replace")
	if resultado.returncode == 255:
		raise remoto.ErrorRemoto(remoto.explicar_error_ssh(errores))
	return resultado.returncode, resultado.stdout, errores


def _decodificar(texto: str) -> bytes:
	"""Base64 de un archivo comprimido con gzip → bytes; ``b""`` si llega dañado."""
	try:
		datos = base64.b64decode(texto.strip(), validate=False)
		return gzip.decompress(datos) if datos else b""
	except (binascii.Error, OSError, EOFError, zlib.error):
		log.debug("Trozo ilegible del otro equipo", exc_info=True)
		return b""


@dataclass
class Inventario:
	"""Lo que hay en el otro equipo según una conexión de inventario."""

	transcripts: dict[str, tuple[int, int]] = field(default_factory=dict)  # ruta → (tamaño, fecha)
	escritorio: dict[str, tuple[int, int]] = field(default_factory=dict)
	vivos: list[dict] = field(default_factory=list)
	codex: bool = False


def leer_inventario(salida: str) -> Inventario:
	"""Interpreta la salida de ``guion_inventario`` (tolerante: ignora lo que no entiende)."""
	inventario = Inventario()
	seccion = ""
	for linea in salida.splitlines():
		if linea.startswith("@@") and not linea.startswith("@@v "):
			seccion = linea[2:].strip()
			continue
		if seccion in ("proyectos", "escritorio"):
			tamano, _, resto = linea.partition(" ")
			fecha, _, ruta = resto.partition(" ")
			if not (tamano.isdigit() and fecha.isdigit() and ruta.startswith("/")):
				continue
			destino = inventario.transcripts if seccion == "proyectos" else inventario.escritorio
			destino[ruta] = (int(tamano), int(fecha))
		elif seccion == "vivos" and linea.startswith("@@v "):
			try:
				datos = json.loads(base64.b64decode(linea[4:].strip()).decode("utf-8", "replace"))
			except (binascii.Error, ValueError):
				log.debug("Estado vivo ilegible del otro equipo", exc_info=True)
				continue
			if isinstance(datos, dict):
				inventario.vivos.append(datos)
		elif seccion == "codex" and linea.strip() == "si":
			inventario.codex = True
	return inventario


def leer_trozos(salida: str) -> tuple[dict[str, tuple[int, bytes, bytes]], dict[str, bytes]]:
	"""Interpreta la salida de ``GUION_TROZOS``: ``(transcripts, archivos)``.

	``transcripts`` va de la ruta a ``(tamaño, principio, final)``; ``archivos``,
	de la ruta al archivo entero.
	"""
	transcripts: dict[str, tuple[int, bytes, bytes]] = {}
	archivos: dict[str, bytes] = {}
	lineas = salida.splitlines()
	indice = 0
	while indice < len(lineas):
		linea = lineas[indice]
		indice += 1
		if linea.startswith("@@t "):
			tamano, _, ruta = linea[4:].partition(" ")
			if tamano.isdigit() and indice + 1 < len(lineas):
				transcripts[ruta] = (int(tamano), _decodificar(lineas[indice]), _decodificar(lineas[indice + 1]))
			indice += 2
		elif linea.startswith("@@c "):
			archivos[linea[4:]] = _decodificar(lineas[indice]) if indice < len(lineas) else b""
			indice += 1
	return transcripts, archivos


def _seguro(ruta: str) -> bool:
	"""Una ruta que se puede devolver al otro equipo por la entrada estándar (una sola línea, absoluta)."""
	return ruta.startswith("/") and "\n" not in ruta and "\r" not in ruta and "\0" not in ruta


# ── Lo leído de cada equipo ──────────────────────────────────────────────────


@dataclass
class Lectura:
	"""Lo último leído de un equipo."""

	claude: list[historial_claude.SesionClaude] = field(default_factory=list)
	codex: list[dict[str, Any]] = field(default_factory=list)
	#: Cuándo se leyó bien por última vez (0: nunca).
	momento: float = 0.0
	#: Último intento, bueno o malo.
	intento: float = 0.0
	#: Explicación del último fallo, o ``""``.
	error: str = ""
	leyendo: bool = False
	#: Lo ya traído de cada archivo: ruta → ((tamaño, fecha), datos).
	transcripts: dict[str, tuple[tuple[int, int], Any]] = field(default_factory=dict)
	escritorio: dict[str, tuple[tuple[int, int], dict]] = field(default_factory=dict)


def leer_claude(equipo: remoto.EquipoSSH, lectura: Lectura, incluir_archivadas: bool = True) -> bool:
	"""Lee las sesiones de Claude Code del equipo y las deja en ``lectura``. Devuelve si hay Codex.

	Bloquea (SSH): llamar desde un hilo. Lanza ``ErrorRemoto`` si no hay conexión.
	"""
	codigo, salida, errores = _ejecutar(equipo, guion_inventario(equipo.sistema), b"", TIEMPO_INVENTARIO)
	texto = salida.decode("utf-8", "replace")
	if "@@fin" not in texto:
		raise remoto.ErrorRemoto(f"«{equipo.nombre}» no devolvió la lista de sesiones: "
			+ (errores.strip().splitlines()[-1:] or ["sin respuesta"])[0][:300])
	inventario = leer_inventario(texto)
	recientes = sorted(((sello[1], ruta) for ruta, sello in inventario.transcripts.items() if _seguro(ruta)),
		reverse=True)[:MAX_TRANSCRIPTS]
	vigentes = {ruta: inventario.transcripts[ruta] for _fecha, ruta in recientes}
	pedir = [f"t {ruta}" for ruta, sello in vigentes.items()
		if lectura.transcripts.get(ruta, ((-1, -1), None))[0] != sello]
	escritorio = {ruta: sello for ruta, sello in inventario.escritorio.items() if _seguro(ruta)}
	pedir += [f"c {ruta}" for ruta, sello in escritorio.items()
		if lectura.escritorio.get(ruta, ((-1, -1), {}))[0] != sello]
	for inicio in range(0, len(pedir), LOTE_TROZOS):
		lote = pedir[inicio:inicio + LOTE_TROZOS]
		_codigo, salida, _errores = _ejecutar(equipo, GUION_TROZOS, ("\n".join(lote) + "\n").encode("utf-8"), TIEMPO_TROZOS)
		trozos, archivos = leer_trozos(salida.decode("utf-8", "replace"))
		for ruta, (tamano, cabeza, cola) in trozos.items():
			if ruta in vigentes:
				lectura.transcripts[ruta] = (vigentes[ruta], historial_claude.datos_de_trozos(cabeza, cola, tamano))
		for ruta, contenido in archivos.items():
			if ruta not in escritorio:
				continue
			try:
				datos = json.loads(contenido.decode("utf-8", "replace"))
			except ValueError:
				datos = {}
			lectura.escritorio[ruta] = (escritorio[ruta], datos if isinstance(datos, dict) else {})
	# Lo que ya no está en el otro equipo se olvida.
	lectura.transcripts = {r: v for r, v in lectura.transcripts.items() if r in vigentes}
	lectura.escritorio = {r: v for r, v in lectura.escritorio.items() if r in escritorio}
	datos_transcripts = {ruta: (datos, float(sello[1])) for ruta, (sello, datos) in lectura.transcripts.items()}
	lectura.claude = historial_claude.sesiones_de_otro_equipo(
		equipo.id, datos_transcripts, [datos for _sello, datos in lectura.escritorio.values()],
		historial_claude.estado_vivo_de_datos(inventario.vivos), incluir_archivadas)
	return inventario.codex


# ── Copia local para leer una sesión ─────────────────────────────────────────


def ruta_copia(equipo_id: str, id_sesion: str) -> Path:
	"""Dónde se guarda en Windows la copia del transcript de una sesión de otro equipo."""
	from app.core.paths import ruta_cache

	return ruta_cache() / "equipos" / equipo_id / f"{id_sesion}.jsonl"


_ultima_copia: dict[str, float] = {}
_candado_copias = threading.Lock()


def copiar_transcript(equipo: remoto.EquipoSSH, ruta_remota: str, destino: Path, minimo: float = 0.0) -> Path:
	"""Trae a ``destino`` el transcript del otro equipo; si ya hay copia, solo lo añadido.

	Con ``minimo``, no vuelve a conectarse si lo hizo hace menos de esos
	segundos. Lanza ``ErrorRemoto`` si no se puede.
	"""
	if not _seguro(ruta_remota):
		raise remoto.ErrorRemoto("Ruta del otro equipo no válida.")
	clave = str(destino)
	with _candado_copias:
		if minimo and destino.is_file() and time.monotonic() - _ultima_copia.get(clave, 0.0) < minimo:
			return destino
		_ultima_copia[clave] = time.monotonic()
	destino.parent.mkdir(parents=True, exist_ok=True)
	desde = destino.stat().st_size if destino.is_file() else 0
	codigo, salida, errores = _ejecutar(equipo, GUION_COPIA, f"{desde} {ruta_remota}\n".encode(), TIEMPO_COPIA)
	if codigo == 4 and desde:
		# Más corto que la copia: Claude Code lo ha reescrito. Se trae entero.
		desde = 0
		codigo, salida, errores = _ejecutar(equipo, GUION_COPIA, f"0 {ruta_remota}\n".encode(), TIEMPO_COPIA)
	if codigo == 3:
		raise remoto.ErrorRemoto(f"Esta sesión ya no está en «{equipo.nombre}».")
	if codigo != 0:
		raise remoto.ErrorRemoto(f"No se pudo leer la sesión en «{equipo.nombre}»: "
			+ (errores.strip().splitlines()[-1:] or ["error desconocido"])[0][:300])
	try:
		nuevo = gzip.decompress(salida) if salida else b""
	except (OSError, EOFError, zlib.error) as error:
		raise remoto.ErrorRemoto(f"La sesión llegó dañada desde «{equipo.nombre}».") from error
	if desde == 0:
		temporal = destino.with_suffix(".parcial")
		temporal.write_bytes(nuevo)
		temporal.replace(destino)
	elif nuevo:
		with open(destino, "ab") as archivo:
			archivo.write(nuevo)
	return destino


# ── Lectura de todos los equipos en segundo plano ────────────────────────────


class HistorialEquipos:
	"""Lo último leído de cada equipo, que se renueva en hilos de fondo.

	``sesiones()`` nunca espera al otro equipo: devuelve lo que hay y, si toca,
	lanza la lectura. ``al_cambiar`` se llama (desde el hilo) cuando llega algo nuevo.
	"""

	def __init__(
		self,
		opcion: Callable[[str, Any], Any],
		listar_codex: Callable[[remoto.EquipoSSH], list[dict[str, Any]]] | None = None,
		al_cambiar: Callable[[], None] | None = None,
	) -> None:
		self._opcion = opcion
		self._listar_codex = listar_codex
		self.al_cambiar = al_cambiar
		self._candado = threading.Lock()
		self._lecturas: dict[str, Lectura] = {}

	def equipos(self) -> dict[str, remoto.EquipoSSH]:
		"""Equipos cuyas sesiones se muestran en la lista."""
		try:
			return {ident: equipo for ident, equipo in remoto.equipos(self._opcion).items() if equipo.historial}
		except Exception:  # noqa: BLE001 - una configuración ilegible no debe vaciar la lista
			log.debug("No se pudieron leer los equipos SSH", exc_info=True)
			return {}

	def lecturas(self, forzar: bool = False) -> dict[str, tuple[remoto.EquipoSSH, Lectura]]:
		"""Lo último leído de cada equipo; lanza en segundo plano las lecturas que tocan."""
		equipos = self.equipos()
		ahora = time.monotonic()
		lanzar = []
		with self._candado:
			for ident in [i for i in self._lecturas if i not in equipos]:
				del self._lecturas[ident]
			for ident, equipo in equipos.items():
				lectura = self._lecturas.setdefault(ident, Lectura())
				espera = INTERVALO_FALLO if lectura.error else INTERVALO
				if not lectura.leyendo and (forzar or not lectura.intento or ahora - lectura.intento >= espera):
					lectura.leyendo = True
					lectura.intento = ahora
					lanzar.append(equipo)
			resultado = {ident: (equipos[ident], lectura) for ident, lectura in self._lecturas.items()}
		for equipo in lanzar:
			threading.Thread(target=self._leer, args=(equipo,), name=f"historial-{equipo.id}", daemon=True).start()
		return resultado

	def _leer(self, equipo: remoto.EquipoSSH) -> None:
		with self._candado:
			lectura = self._lecturas.get(equipo.id)
		if lectura is None:
			return
		cambiado = False
		try:
			hay_codex = False
			if self._opcion("historial.claude", True):
				hay_codex = leer_claude(equipo, lectura)
			else:
				lectura.claude = []
			if self._opcion("historial.codex", True) and self._listar_codex is not None and hay_codex:
				try:
					lectura.codex = [{**hilo, "equipo": equipo.id} for hilo in self._listar_codex(equipo)]
				except Exception:  # noqa: BLE001 - Codex puede no tener la sesión iniciada en ese equipo
					log.warning("No se pudieron leer los hilos de Codex de %s", equipo.nombre, exc_info=True)
			else:
				lectura.codex = []
			if lectura.error:
				log.info("Vuelve a responder el equipo %s", equipo.nombre)
			lectura.error = ""
			lectura.momento = time.time()
			cambiado = True
		except remoto.ErrorRemoto as error:
			if str(error) != lectura.error:
				log.warning("No se pudieron leer las sesiones de %s: %s", equipo.nombre, error)
				cambiado = True  # la interfaz lo cuenta una vez, no en cada intento
			lectura.error = str(error)
		except Exception as error:  # noqa: BLE001 - un formato inesperado no debe matar el hilo en silencio
			log.exception("Fallo al leer las sesiones de %s", equipo.nombre)
			cambiado = lectura.error != (str(error) or type(error).__name__)
			lectura.error = str(error) or type(error).__name__
		finally:
			lectura.leyendo = False
		if cambiado and self.al_cambiar is not None:
			try:
				self.al_cambiar()
			except Exception:  # noqa: BLE001 - un aviso fallido no debe perder la lectura
				log.debug("al_cambiar falló", exc_info=True)

	def estado(self) -> list[tuple[str, str]]:
		"""``(nombre del equipo, explicación)`` de los equipos que no se han podido leer."""
		with self._candado:
			lecturas = list(self._lecturas.items())
		equipos = self.equipos()
		return [(equipos[ident].nombre, lectura.error) for ident, lectura in lecturas if lectura.error and ident in equipos]

