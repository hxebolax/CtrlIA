"""Publica una versión de Ctrl+IA de una sola vez: servidor de actualizaciones y GitHub.

Uso (desde la raíz del proyecto, con ``publicar.bat`` o ``entorno.bat publicar``)::

	python tools/publicar.py                    # publica APP_VERSION de verdad
	python tools/publicar.py --ensayo           # dice todo lo que haría, sin copiar, subir ni crear nada
	python tools/publicar.py --reemplazar       # vuelve a subir los archivos de una versión ya publicada

Pasos (se para en el primer error y explica qué pasa y cómo seguir):

1. Comprobaciones previas: versión igual en ``app/core/version.py`` y
   ``pyproject.toml``; sección de la versión en ``docs/changelog.md``;
   repositorio git en ``main`` con el remoto ``origin`` y sin archivos
   prohibidos; ``gh`` con sesión iniciada; la etiqueta ``v<versión>`` no existe
   aún en GitHub (salvo ``--reemplazar``); la carpeta de Synology Drive existe y
   no tiene ya esta versión ni una más nueva; existe la clave privada de firma.
2. Pruebas (``entorno.bat pruebas``), salvo ``--sin-pruebas``.
3. Compilar y empaquetar (``entorno.bat compilar``), salvo ``--sin-compilar``.
4. Verificar la firma de ``build/version.json`` con la clave pública que lleva
   el programa, y el tamaño y el SHA-256 del ZIP.
5. Copiar al servidor (carpeta sincronizada de Synology Drive): primero el ZIP
   y al final ``version.json``, cada uno con nombre temporal, comprobando
   tamaño y SHA-256 y renombrándolo después. La carpeta es personal y no está
   en el código: sale de ``--destino``, de la variable de entorno
   ``CTRLIA_DESTINO_SERVIDOR`` o de ``tools/publicar.local.json``
   (``{"destino_servidor": "C:\\...\\CtrlIA"}``, que nunca se sube a GitHub).
   Si no hay ninguna, este paso se salta y se dice.
6. Subir el código a GitHub (``tools/subir_github.py``) y crear la etiqueta.
7. Crear la release en GitHub con ``gh release create`` (notas del registro de
   cambios, el ZIP y el MISMO ``version.json`` firmado), marcada como la última.
8. Resumen con las direcciones de todo lo publicado.

Solo biblioteca estándar (más ``cryptography`` para verificar la firma).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

CARPETA_TOOLS = Path(__file__).resolve().parent
RAIZ = CARPETA_TOOLS.parent
for _ruta in (str(CARPETA_TOOLS), str(RAIZ)):
	if _ruta not in sys.path:
		sys.path.insert(0, _ruta)

import subir_github  # noqa: E402
from subir_github import Ejecutor, ErrorPublicacion, Resultado, decir, detalle, plural  # noqa: E402

from app.core import documentacion  # noqa: E402

NOMBRE_VISIBLE = "Ctrl+IA"
NOMBRE_APP = "CtrlIA"
REPOSITORIO = "hxebolax/CtrlIA"
# La carpeta del servidor es personal y NO va en el código (el repositorio es público):
# se toma de --destino, de esta variable de entorno o del archivo local sin versionar.
VARIABLE_DESTINO = "CTRLIA_DESTINO_SERVIDOR"
CONFIG_LOCAL = CARPETA_TOOLS / "publicar.local.json"
CLAVE_DESTINO = "destino_servidor"
URL_SERVIDOR = "https://xebolax.synology.me/CtrlIA"
CLAVE_FIRMA = RAIZ / "tools" / "claves" / "firma_ed25519.key"
TOTAL_PASOS = 8


class Cancelado(Exception):
	"""La persona ha dicho que no a la pregunta de confirmación."""


# ── Datos del proyecto ───────────────────────────────────────────────────────

def version_de_codigo(raiz: Path) -> str | None:
	"""``APP_VERSION`` de ``app/core/version.py`` sin importar la aplicación."""
	return _asignacion_en(raiz / "app" / "core" / "version.py", "APP_VERSION")


def version_de_pyproject(raiz: Path) -> str | None:
	try:
		datos = tomllib.loads((raiz / "pyproject.toml").read_text(encoding="utf-8"))
	except (OSError, tomllib.TOMLDecodeError):
		return None
	valor = datos.get("project", {}).get("version")
	return valor if isinstance(valor, str) else None


def claves_publicas(raiz: Path) -> list[str]:
	"""``CLAVES_PUBLICAS_ED25519`` de ``app/services/actualizador.py``: las que tienen las instalaciones."""
	valor = _asignacion_en(raiz / "app" / "services" / "actualizador.py", "CLAVES_PUBLICAS_ED25519")
	if isinstance(valor, str):
		return [valor]
	return [clave for clave in (valor or ()) if isinstance(clave, str)]


def _asignacion_en(ruta: Path, nombre: str):
	"""Valor literal de ``nombre = ...`` en un archivo de Python, leído con ast (sin ejecutarlo)."""
	try:
		arbol = ast.parse(ruta.read_text(encoding="utf-8"))
	except (OSError, SyntaxError):
		return None
	for nodo in arbol.body:
		if isinstance(nodo, ast.Assign):
			objetivos, valor = nodo.targets, nodo.value
		elif isinstance(nodo, ast.AnnAssign) and nodo.value is not None:
			objetivos, valor = [nodo.target], nodo.value
		else:
			continue
		if any(isinstance(objetivo, ast.Name) and objetivo.id == nombre for objetivo in objetivos):
			try:
				return ast.literal_eval(valor)
			except ValueError:
				return None
	return None


def notas_de_release(raiz: Path, version: str) -> str | None:
	"""Notas en Markdown de la versión (el cuerpo de su sección del registro de cambios)."""
	try:
		texto = (raiz / "docs" / documentacion.CHANGELOG).read_text(encoding="utf-8-sig")
	except OSError:
		return None
	entrada = documentacion.notas_de_version(texto, version)
	if entrada is None or not entrada.texto.strip():
		return None
	return entrada.texto.strip() + "\n"


def destino_configurado(config_local: Path = CONFIG_LOCAL) -> tuple[Path | None, str]:
	"""Carpeta del servidor y de dónde sale: variable de entorno o archivo local; ``(None, motivo)`` si no hay."""
	valor = os.environ.get(VARIABLE_DESTINO, "").strip()
	if valor:
		return Path(valor).expanduser(), f"la variable de entorno {VARIABLE_DESTINO}"
	try:
		datos = json.loads(config_local.read_text(encoding="utf-8-sig"))
	except FileNotFoundError:
		return None, f"no hay --destino, ni variable {VARIABLE_DESTINO}, ni archivo {config_local.name}"
	except (OSError, ValueError) as error:
		return None, f"no se puede leer {config_local.name}: {error}"
	valor = str(datos.get(CLAVE_DESTINO) or "").strip() if isinstance(datos, dict) else ""
	if not valor:
		return None, f"{config_local.name} no tiene la clave «{CLAVE_DESTINO}»"
	return Path(valor).expanduser(), f"el archivo local {config_local.name}"


def sha256_de(ruta: Path) -> str:
	resumen = hashlib.sha256()
	with ruta.open("rb") as archivo:
		for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
			resumen.update(bloque)
	return resumen.hexdigest()


def cargar_firmador():
	"""Importa ``tools/firmar_version.py`` como módulo (``tools`` no es un paquete)."""
	especificacion = importlib.util.spec_from_file_location("firmar_version", CARPETA_TOOLS / "firmar_version.py")
	if especificacion is None or especificacion.loader is None:
		raise ErrorPublicacion("No se puede cargar tools/firmar_version.py.")
	modulo = importlib.util.module_from_spec(especificacion)
	especificacion.loader.exec_module(modulo)
	return modulo


def version_en_manifiesto(ruta: Path) -> str | None:
	try:
		datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
	except (OSError, ValueError):
		return None
	valor = datos.get("version") if isinstance(datos, dict) else None
	return valor if isinstance(valor, str) else None


def copiar_verificando(origen: Path, destino: Path) -> bool:
	"""Copia ``origen`` a ``destino`` con un nombre temporal, comprueba tamaño y SHA-256 y renombra.

	Devuelve ``False`` si el destino ya era idéntico (no se copia). El renombrado
	final es atómico: nadie ve nunca un archivo a medio copiar con su nombre real.
	"""
	huella = sha256_de(origen)
	tamano = origen.stat().st_size
	if destino.is_file() and destino.stat().st_size == tamano and sha256_de(destino) == huella:
		return False
	temporal = destino.with_name(destino.name + ".parcial")
	try:
		shutil.copyfile(origen, temporal)
		_comprobar_copia(temporal, tamano, huella)
		os.replace(temporal, destino)
	finally:
		temporal.unlink(missing_ok=True)
	_comprobar_copia(destino, tamano, huella)
	return True


def _comprobar_copia(ruta: Path, tamano: int, huella: str) -> None:
	if ruta.stat().st_size != tamano:
		raise ErrorPublicacion(f"La copia de {ruta.name} no tiene el tamaño esperado ({tamano} bytes).")
	if sha256_de(ruta) != huella:
		raise ErrorPublicacion(f"La copia de {ruta.name} no tiene el SHA-256 esperado: se ha corrompido al copiar.")


# ── Publicación ──────────────────────────────────────────────────────────────

@dataclass
class Opciones:
	ensayo: bool = False
	sin_pruebas: bool = False
	sin_compilar: bool = False
	reemplazar: bool = False
	sin_confirmar: bool = False
	agente: bool = False
	mensaje: str | None = None
	raiz: Path = RAIZ
	destino: Path | None = None  # None: sin servidor configurado, se salta el paso 5.
	origen_destino: str = "la opción --destino"
	repositorio: str = REPOSITORIO


@dataclass
class Publicador:
	opciones: Opciones
	ejecutar: Ejecutor
	salida: Callable[[str], None] = decir
	preguntar: Callable[[str], str] | None = None
	problemas: list[str] = field(default_factory=list)
	version: str = ""
	notas: str = ""
	repo_ok: bool = False
	gh_ok: bool = False
	etiqueta_remota: bool = False
	etiqueta_local: bool = False
	release_existe: bool = False
	cambios: list = field(default_factory=list)
	hechos: list[str] = field(default_factory=list)

	# ── Utilidades ───────────────────────────────────────────────────────────

	@property
	def etiqueta(self) -> str:
		return f"v{self.version}"

	@property
	def titulo(self) -> str:
		return f"{NOMBRE_VISIBLE} {self.version}"

	@property
	def build(self) -> Path:
		return self.opciones.raiz / "build"

	@property
	def zip_(self) -> Path:
		return self.build / f"{NOMBRE_APP}_v{self.version}.zip"

	@property
	def manifiesto(self) -> Path:
		return self.build / "version.json"

	def paso(self, numero: int, titulo: str) -> None:
		self.salida("")
		self.salida(f"Paso {numero} de {TOTAL_PASOS}: {titulo}.")

	def bien(self, texto: str) -> None:
		self.salida(f"Correcto: {texto}")

	def ensayo(self, texto: str) -> None:
		self.salida(f"[ensayo] {texto}")

	def fallo(self, texto: str) -> None:
		"""En una publicación real se para; en el ensayo lo apunta y sigue para enseñarlo todo."""
		if self.opciones.ensayo:
			self.problemas.append(texto)
			self.salida(f"PROBLEMA (una publicación real se pararía aquí): {texto}")
			return
		raise ErrorPublicacion(texto)

	def gh(self, *argumentos: str, entrada: str | None = None) -> Resultado:
		return self.ejecutar(["gh", *argumentos], entrada=entrada)

	# ── Paso 1 ───────────────────────────────────────────────────────────────

	def comprobaciones_previas(self) -> None:
		self.paso(1, "comprobaciones previas")
		raiz = self.opciones.raiz
		version = version_de_codigo(raiz)
		version_toml = version_de_pyproject(raiz)
		if not version:
			raise ErrorPublicacion("No se pudo leer APP_VERSION de app/core/version.py.")
		self.version = version
		if version_toml != version:
			self.fallo(
				f"La versión no coincide: app/core/version.py dice {version} y pyproject.toml dice "
				f"{version_toml or 'nada'}. Ponga la misma en los dos."
			)
		else:
			self.bien(f"versión {version} en app/core/version.py y en pyproject.toml.")

		notas = notas_de_release(raiz, version)
		if notas is None:
			self.fallo(
				f"docs/changelog.md no tiene la sección de la versión {version} (o está vacía). "
				f"Añada «## Versión {version} - AAAA-MM-DD» con sus cambios."
			)
		else:
			self.notas = notas
			lineas = [linea for linea in notas.splitlines() if linea.strip()]
			self.bien(f"docs/changelog.md tiene las notas de la versión {version} ({plural(len(lineas), 'línea', 'líneas')}).")

		self._comprobar_git()
		self._comprobar_github()
		self._comprobar_servidor()
		self._comprobar_compilacion()

	def _comprobar_git(self) -> None:
		try:
			subir_github.comprobar_repositorio(self.ejecutar, self.opciones.raiz)
		except ErrorPublicacion as error:
			self.fallo(str(error))
			return
		remoto = self.ejecutar(["git", "remote", "get-url", subir_github.REMOTO]).salida.strip()
		if self.opciones.repositorio.lower() not in remoto.lower().removesuffix(".git"):
			self.fallo(f"El remoto origin apunta a {remoto}, no al repositorio {self.opciones.repositorio}.")
			return
		self.repo_ok = True
		self.bien(f"repositorio git en la rama main con el remoto origin ({remoto}).")
		try:
			self.cambios = subir_github.cambios_pendientes(self.ejecutar)
			prohibidos = subir_github.buscar_prohibidos(self.ejecutar, self.opciones.raiz, self.cambios)
		except ErrorPublicacion as error:
			self.fallo(str(error))
			return
		if prohibidos:
			lista = "; ".join(prohibidos)
			self.fallo(f"Hay archivos que no deben subirse a GitHub: {lista}. Añádalos a .gitignore.")
		else:
			self.bien("no hay archivos prohibidos (claves, datos personales, compilaciones...).")
		if self.cambios:
			self.salida(
				f"Aviso: hay {plural(len(self.cambios), 'archivo cambiado', 'archivos cambiados')} sin guardar "
				f"({subir_github.resumen_cambios(self.cambios)}); se guardarán en un commit en el paso 6."
			)
		else:
			self.bien("no hay cambios sin guardar en git.")
		self.etiqueta_local = self.ejecutar(
			["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{self.etiqueta}"]
		).correcto

	def _comprobar_github(self) -> None:
		if isinstance(self.ejecutar, Ejecutor) and shutil.which("gh") is None:
			self.fallo("No se encuentra la herramienta gh de GitHub. Instálela desde cli.github.com.")
			return
		sesion = self.gh("auth", "status")
		if not sesion.correcto:
			self.fallo(f"gh no tiene sesión iniciada en GitHub ({detalle(sesion)}). Ejecute: gh auth login")
			return
		repo = self.gh("repo", "view", self.opciones.repositorio, "--json", "name")
		if not repo.correcto:
			self.fallo(f"No se encuentra el repositorio {self.opciones.repositorio} en GitHub: {detalle(repo)}")
			return
		self.gh_ok = True
		self.bien(f"gh tiene sesión iniciada y el repositorio {self.opciones.repositorio} existe.")
		release = self.gh("release", "view", self.etiqueta, "--repo", self.opciones.repositorio, "--json", "tagName")
		if release.correcto:
			self.release_existe = True
		elif "not found" not in (release.error + release.salida).lower():
			self.fallo(f"No se pudo consultar la release {self.etiqueta}: {detalle(release)}")
			return
		if self.repo_ok:
			remota = self.ejecutar(["git", "ls-remote", "--tags", subir_github.REMOTO, f"refs/tags/{self.etiqueta}"])
			if not remota.correcto:
				self.fallo(f"No se pudo consultar las etiquetas de GitHub: {detalle(remota)}")
				return
			self.etiqueta_remota = bool(remota.salida.strip())
		if self.release_existe or self.etiqueta_remota:
			que = "la release" if self.release_existe else "la etiqueta"
			if self.opciones.reemplazar:
				self.salida(
					f"Aviso: {que} {self.etiqueta} ya existe en GitHub. Con --reemplazar se vuelven a subir "
					"sus archivos; la etiqueta no se mueve."
				)
			else:
				self.fallo(
					f"{que.capitalize()} {self.etiqueta} ya existe en GitHub: esta versión ya se publicó. "
					"Si es una versión nueva, suba APP_VERSION en app/core/version.py y en pyproject.toml. "
					"Si quiere volver a subir sus archivos, use --reemplazar."
				)
		else:
			self.bien(f"la etiqueta {self.etiqueta} todavía no existe en GitHub.")

	def _comprobar_servidor(self) -> None:
		destino = self.opciones.destino
		if destino is None:
			self.salida(
				f"Aviso: no hay carpeta del servidor de actualizaciones ({self.opciones.origen_destino}); "
				f"el paso 5 se saltará y solo se publicará en GitHub. Para configurarla, use --destino, la "
				f"variable de entorno {VARIABLE_DESTINO} o el archivo tools/{CONFIG_LOCAL.name} con "
				f'{{"{CLAVE_DESTINO}": "ruta de la carpeta"}}.'
			)
			return
		self.bien(f"carpeta del servidor {destino} (la indica {self.opciones.origen_destino}).")
		if not destino.is_dir():
			self.fallo(
				f"No se encuentra la carpeta del servidor {destino}. Compruebe que Synology Drive está "
				"instalado y sincronizando la carpeta web."
			)
			return
		publicada = version_en_manifiesto(destino / "version.json")
		if publicada is None:
			self.bien("el servidor todavía no tiene version.json; esta será la primera versión publicada.")
			return
		actual, nueva = documentacion.clave_version(publicada), documentacion.clave_version(self.version)
		if actual > nueva:
			self.fallo(
				f"El servidor ya publica la versión {publicada}, más nueva que la {self.version}. "
				"Nunca se publica una versión anterior."
			)
		elif actual == nueva and not self.opciones.reemplazar:
			self.fallo(
				f"El servidor ya publica la versión {self.version}. Si quiere volver a subirla, use --reemplazar."
			)
		else:
			self.bien(f"el servidor publica ahora la versión {publicada}; se sustituirá por la {self.version}.")

	def _comprobar_compilacion(self) -> None:
		if self.opciones.sin_compilar:
			faltan = [ruta.name for ruta in (self.zip_, self.manifiesto) if not ruta.is_file()]
			if faltan:
				self.fallo(f"Con --sin-compilar hacen falta en build: {', '.join(faltan)}. Compile primero.")
			else:
				self.bien(f"se usará la compilación que ya hay en build ({self.zip_.name} y version.json).")
		elif not CLAVE_FIRMA.is_file():
			self.fallo(f"Falta la clave privada de firma {CLAVE_FIRMA}; sin ella no se puede firmar version.json.")
		else:
			self.bien("está la clave privada de firma (no se copia a ningún sitio).")

	def confirmar(self) -> None:
		"""Una sola pregunta antes de empezar, solo si hay alguien delante de la consola."""
		if self.opciones.ensayo or self.opciones.sin_confirmar or self.preguntar is None:
			return
		self.salida("")
		self.salida(
			f"Se va a publicar {self.titulo}: en el servidor de actualizaciones y en GitHub "
			f"(repositorio {self.opciones.repositorio})."
		)
		respuesta = self.preguntar("¿Continuar? Escriba s y pulse Intro para publicar, o solo Intro para cancelar: ")
		if respuesta.strip().lower() not in {"s", "si", "sí"}:
			raise Cancelado

	# ── Pasos 2 y 3 ──────────────────────────────────────────────────────────

	def _entorno(self, orden: str, descripcion: str) -> None:
		if self.opciones.ensayo:
			self.ensayo(f"Se ejecutaría: entorno.bat {orden}")
			return
		self.salida(f"Ejecutando entorno.bat {orden}. Puede tardar unos minutos...")
		# Ruta completa: algunas consolas no buscan programas en la carpeta actual.
		bat = self.opciones.raiz / "entorno.bat"
		resultado = self.ejecutar(["cmd", "/d", "/c", str(bat), orden], capturar=False)
		if not resultado.correcto:
			raise ErrorPublicacion(f"Falló «entorno.bat {orden}» (código {resultado.codigo}). Revise los mensajes anteriores.")
		self.bien(descripcion)

	def pruebas(self) -> None:
		self.paso(2, "pruebas")
		if self.opciones.sin_pruebas:
			self.salida("Se omiten las pruebas (--sin-pruebas).")
			return
		self._entorno("pruebas", "todas las pruebas pasan.")

	def compilar(self) -> None:
		self.paso(3, "compilar y empaquetar")
		if self.opciones.sin_compilar:
			self.salida("Se omite la compilación (--sin-compilar): se usa lo que ya hay en build.")
			return
		self._entorno("compilar", f"compilado y empaquetado en {self.zip_.name} y version.json.")

	# ── Paso 4 ───────────────────────────────────────────────────────────────

	def verificar(self) -> None:
		self.paso(4, "verificar la firma de version.json")
		if self.opciones.ensayo and not self.opciones.sin_compilar:
			self.ensayo("Se verificaría la firma del version.json recién generado y el SHA-256 del ZIP.")
			return
		if not self.manifiesto.is_file() or not self.zip_.is_file():
			self.fallo(f"No están {self.zip_.name} y version.json en build; no se puede verificar.")
			return
		try:
			datos = json.loads(self.manifiesto.read_text(encoding="utf-8"))
		except (OSError, ValueError) as error:
			self.fallo(f"No se puede leer build/version.json: {error}")
			return
		if datos.get("version") != self.version or datos.get("file") != self.zip_.name:
			self.fallo(
				f"build/version.json es de otra versión ({datos.get('version')}, {datos.get('file')}); "
				f"se esperaba la {self.version} con {self.zip_.name}. Vuelva a compilar."
			)
			return
		claves = claves_publicas(self.opciones.raiz)
		if not claves:
			self.fallo("No se encuentran las claves públicas en app/services/actualizador.py.")
			return
		try:
			problemas = cargar_firmador().verificar(self.manifiesto, claves)
		except Exception as error:  # noqa: BLE001 - cualquier fallo del verificador detiene la publicación
			self.fallo(f"No se pudo verificar la firma: {error}")
			return
		if problemas:
			self.fallo("La verificación de version.json ha fallado: " + " ".join(problemas))
			return
		self.bien("la firma es válida con la clave pública del programa, y el tamaño y el SHA-256 del ZIP coinciden.")
		if str(datos.get("novedades") or "").strip():
			lineas = len(str(datos["novedades"]).splitlines())
			self.bien(f"el manifiesto trae las novedades ({plural(lineas, 'línea', 'líneas')}).")
		else:
			self.salida("Aviso: el manifiesto no trae novedades; el programa dirá que no hay novedades publicadas.")

	# ── Paso 5 ───────────────────────────────────────────────────────────────

	def copiar_servidor(self) -> None:
		self.paso(5, "copiar al servidor de actualizaciones (Synology Drive)")
		destino = self.opciones.destino
		if destino is None:
			self.salida("Se salta este paso: no hay carpeta del servidor configurada. Solo se publica en GitHub.")
			return
		if self.opciones.ensayo:
			self.ensayo(f"Se copiaría {self.zip_.name} a {destino}, comprobando tamaño y SHA-256.")
			self.ensayo(f"Después se sustituiría {destino / 'version.json'} por el nuevo, comprobándolo igual.")
			return
		for origen in (self.zip_, self.manifiesto):  # El ZIP primero; el manifiesto, al final.
			self.salida(f"Copiando {origen.name} a {destino}...")
			try:
				copiado = copiar_verificando(origen, destino / origen.name)
			except OSError as error:
				raise ErrorPublicacion(f"No se pudo copiar {origen.name} al servidor: {error}") from error
			if copiado:
				self.bien(f"{origen.name} copiado; tamaño y SHA-256 comprobados.")
			else:
				self.bien(f"{origen.name} ya estaba en el servidor, idéntico.")
		self.hechos.append(f"Servidor de actualizaciones: {URL_SERVIDOR}/version.json y {URL_SERVIDOR}/{self.zip_.name}")

	# ── Paso 6 ───────────────────────────────────────────────────────────────

	def subir_codigo(self) -> None:
		self.paso(6, "subir el código a GitHub y crear la etiqueta")
		mensaje = self.opciones.mensaje or self.titulo
		if self.opciones.ensayo and not self.repo_ok:
			self.ensayo("Se subirían los cambios del código a GitHub (origin, rama main) cuando el repositorio esté listo.")
			self.ensayo(f"Se crearía la etiqueta {self.etiqueta} y se subiría a GitHub.")
			return
		try:
			subir_github.subir(
				self.ejecutar,
				raiz=self.opciones.raiz,
				mensaje=mensaje,
				agente=self.opciones.agente,
				ensayo=self.opciones.ensayo,
				salida=self.salida,
			)
		except ErrorPublicacion as error:
			self.fallo(str(error))
			return
		if not self.opciones.ensayo:
			self.hechos.append(f"Código subido a https://github.com/{self.opciones.repositorio} (rama main)")
		if self.etiqueta_remota:
			self.salida(f"La etiqueta {self.etiqueta} ya está en GitHub; no se mueve.")
			return
		if self.opciones.ensayo:
			if not self.etiqueta_local:
				self.ensayo(f"Se ejecutaría: git tag -a {self.etiqueta} -m \"{self.titulo}\"")
			self.ensayo(f"Se ejecutaría: git push origin {self.etiqueta}")
			return
		if self.etiqueta_local:
			etiqueta = self.ejecutar(["git", "rev-parse", f"{self.etiqueta}^{{commit}}"]).salida.strip()
			cabeza = self.ejecutar(["git", "rev-parse", "HEAD"]).salida.strip()
			if etiqueta != cabeza:
				raise ErrorPublicacion(
					f"La etiqueta {self.etiqueta} ya existe en este equipo y apunta a otro commit. "
					f"Si sobra, bórrela con: git tag -d {self.etiqueta}"
				)
		else:
			resultado = self.ejecutar(["git", "tag", "-a", self.etiqueta, "-m", self.titulo])
			if not resultado.correcto:
				raise ErrorPublicacion(f"No se pudo crear la etiqueta {self.etiqueta}: {detalle(resultado)}")
		resultado = self.ejecutar(["git", "push", subir_github.REMOTO, self.etiqueta])
		if not resultado.correcto:
			raise ErrorPublicacion(f"No se pudo subir la etiqueta {self.etiqueta}: {detalle(resultado)}")
		self.bien(f"etiqueta {self.etiqueta} creada y subida a GitHub.")
		self.hechos.append(f"Etiqueta {self.etiqueta} en GitHub")

	# ── Paso 7 ───────────────────────────────────────────────────────────────

	def crear_release(self, carpeta_temporal: Path) -> None:
		self.paso(7, "crear la release en GitHub")
		repo = self.opciones.repositorio
		notas = carpeta_temporal / f"notas_{self.etiqueta}.md"
		notas.write_text(self.notas or f"{self.titulo}\n", encoding="utf-8")
		recursos = [str(self.zip_), str(self.manifiesto)]
		if self.release_existe:
			ordenes = [
				["release", "upload", self.etiqueta, *recursos, "--clobber", "--repo", repo],
				[
					"release", "edit", self.etiqueta, "--repo", repo, "--title", self.titulo,
					"--notes-file", str(notas), "--draft=false", "--prerelease=false", "--latest",
				],
			]
		else:
			ordenes = [
				[
					"release", "create", self.etiqueta, *recursos, "--repo", repo, "--title", self.titulo,
					"--notes-file", str(notas), "--latest", "--verify-tag",
				]
			]
		if self.opciones.ensayo:
			for orden in ordenes:
				texto = " ".join(orden).replace(str(notas), "<notas del changelog>")
				self.ensayo(f"Se ejecutaría: gh {texto}")
			self.ensayo(
				f"La release se llamaría «{self.titulo}», con {self.zip_.name} y version.json, "
				"marcada como la última (ni borrador ni versión preliminar)."
			)
			return
		for orden in ordenes:
			resultado = self.gh(*orden)
			if not resultado.correcto:
				raise ErrorPublicacion(f"Falló gh {orden[0]} {orden[1]}: {detalle(resultado)}")
		self._comprobar_release()
		self.hechos.append(f"Release de GitHub: https://github.com/{repo}/releases/tag/{self.etiqueta}")
		self.hechos.append(
			f"Manifiesto de respaldo: https://github.com/{repo}/releases/latest/download/version.json"
		)

	def _comprobar_release(self) -> None:
		repo = self.opciones.repositorio
		vista = self.gh("release", "view", self.etiqueta, "--repo", repo, "--json", "isDraft,isPrerelease,assets")
		try:
			datos = json.loads(vista.salida) if vista.correcto else None
		except ValueError:
			datos = None
		if not isinstance(datos, dict):
			raise ErrorPublicacion(f"No se pudo comprobar la release creada: {detalle(vista)}")
		if datos.get("isDraft") or datos.get("isPrerelease"):
			raise ErrorPublicacion("La release ha quedado como borrador o versión preliminar; revísela en GitHub.")
		tamanos = {recurso.get("name"): recurso.get("size") for recurso in datos.get("assets") or []}
		for archivo in (self.zip_, self.manifiesto):
			if tamanos.get(archivo.name) != archivo.stat().st_size:
				raise ErrorPublicacion(f"En la release falta {archivo.name} o no tiene el tamaño correcto.")
		ultima = self.gh("api", f"repos/{repo}/releases/latest", "--jq", ".tag_name")
		if ultima.salida.strip() != self.etiqueta:
			raise ErrorPublicacion(
				f"La release {self.etiqueta} no ha quedado como la última de GitHub "
				f"(la última es {ultima.salida.strip() or 'desconocida'})."
			)
		self.bien(f"release {self.titulo} creada con {self.zip_.name} y version.json, marcada como la última.")

	# ── Paso 8 y flujo ───────────────────────────────────────────────────────

	def resumen(self) -> None:
		self.paso(8, "resumen")
		repo = self.opciones.repositorio
		if self.opciones.ensayo:
			self.salida(f"Ensayo de la publicación de {self.titulo or NOMBRE_VISIBLE} terminado: no se ha copiado, subido ni creado nada.")
			if self.opciones.destino is not None:
				self.salida(f"Se publicaría en {URL_SERVIDOR}/version.json (copiando a {self.opciones.destino}).")
			else:
				self.salida("No se copiaría nada al servidor de actualizaciones: no hay carpeta configurada.")
			self.salida(f"Se publicaría en https://github.com/{repo}/releases/tag/{self.etiqueta}")
			if self.problemas:
				self.salida(
					f"Antes de publicar hay que resolver {plural(len(self.problemas), 'problema', 'problemas')}:"
				)
				for numero, problema in enumerate(self.problemas, 1):
					self.salida(f"  {numero}. {problema}")
			else:
				self.salida("No se ha encontrado ningún problema: la publicación real debería salir bien.")
			return
		self.salida(f"{self.titulo} publicada.")
		for hecho in self.hechos:
			self.salida(f"  - {hecho}")
		self.salida(f"  - Página del proyecto: https://github.com/{repo}")
		if self.opciones.destino is None:
			self.salida("No se ha copiado nada al servidor de actualizaciones: no había carpeta configurada.")
		else:
			self.salida(
				"Synology Drive sube los archivos al servidor en segundo plano: espere a que termine de "
				"sincronizar antes de apagar el equipo."
			)

	def publicar(self) -> int:
		"""Ejecuta todos los pasos. Devuelve el código de salida (0 si todo fue bien)."""
		self.comprobaciones_previas()
		self.confirmar()
		self.pruebas()
		self.compilar()
		self.verificar()
		self.copiar_servidor()
		self.subir_codigo()
		with tempfile.TemporaryDirectory(prefix="ctrlia_publicar_") as temporal:
			self.crear_release(Path(temporal))
		self.resumen()
		return 1 if self.problemas else 0


def main(argumentos: list[str] | None = None, ejecutar: Ejecutor | None = None) -> int:
	analizador = argparse.ArgumentParser(
		description="Publica la versión de Ctrl+IA en el servidor de actualizaciones y en GitHub."
	)
	analizador.add_argument("--ensayo", "--dry-run", action="store_true", help="Lo dice todo sin copiar, subir ni crear nada.")
	analizador.add_argument("--sin-pruebas", action="store_true", help="No ejecuta las pruebas.")
	analizador.add_argument("--sin-compilar", action="store_true", help="No compila: usa el ZIP y version.json que ya hay en build.")
	analizador.add_argument(
		"--reemplazar", action="store_true", help="La versión ya está publicada: vuelve a subir sus archivos."
	)
	analizador.add_argument("--sin-confirmar", action="store_true", help="No pregunta antes de publicar.")
	analizador.add_argument("--mensaje", help="Mensaje del commit si hay cambios sin guardar (por defecto, «Ctrl+IA <versión>»).")
	analizador.add_argument(
		"--agente", action="store_true", help="Lo ejecuta un agente de IA: el commit lleva la línea Co-Authored-By."
	)
	analizador.add_argument(
		"--destino",
		type=Path,
		help=f"Carpeta sincronizada del servidor (si no, la de {VARIABLE_DESTINO} o la de tools/{CONFIG_LOCAL.name}).",
	)
	analizador.add_argument("--repositorio", default=REPOSITORIO, help="Repositorio de GitHub (propietario/nombre).")
	args = analizador.parse_args(argumentos)
	subir_github.preparar_consola()
	if args.destino is not None:
		destino, origen_destino = args.destino, "la opción --destino"
	else:
		destino, origen_destino = destino_configurado()
	opciones = Opciones(
		ensayo=args.ensayo,
		sin_pruebas=args.sin_pruebas,
		sin_compilar=args.sin_compilar,
		reemplazar=args.reemplazar,
		sin_confirmar=args.sin_confirmar,
		agente=args.agente,
		mensaje=args.mensaje,
		destino=destino,
		origen_destino=origen_destino,
		repositorio=args.repositorio,
	)
	preguntar = input if sys.stdin is not None and sys.stdin.isatty() else None
	publicador = Publicador(opciones, ejecutar or Ejecutor(RAIZ), preguntar=preguntar)
	decir(
		"Publicar una versión de Ctrl+IA"
		+ (" (ENSAYO: no se copia, sube ni crea nada)" if opciones.ensayo else "")
	)
	try:
		return publicador.publicar()
	except Cancelado:
		decir("Publicación cancelada. No se ha cambiado nada.")
		return 1
	except ErrorPublicacion as error:
		decir("")
		decir(f"ERROR: {error}")
		decir("La publicación se ha detenido en este paso.")
		if publicador.hechos:
			decir("Ya se había hecho:")
			for hecho in publicador.hechos:
				decir(f"  - {hecho}")
		return 1
	except KeyboardInterrupt:
		decir("Publicación interrumpida con Control+C.")
		return 1


if __name__ == "__main__":
	sys.exit(main())
