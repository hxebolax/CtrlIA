"""Integración REAL del script de sustitución (.bat) sobre una instalación falsa.

PLANTILLA del agente «actualizador». Solo en Windows. Cada caso:

1. Crea una «instalación» 1.0.0 en una carpeta con espacios, tilde, eñe y apóstrofo:
   un ``CtrlIA.exe`` falso, ``lib/``, una carpeta ``lib/paquete/data`` (que
   NO es la data del usuario), ``app_falsa.py`` (el programa que se relanza) y un
   ``data/`` con configuración, base de datos y notas del usuario.
2. Publica la 1.1.0 firmada en un servidor HTTP local. Su ZIP trae además un
   ``data/config/ajustes.json`` que NUNCA debe llegar a la instalación.
3. Recorre el flujo real: comprobar → descargar y preparar → lanzar el .bat.
   Un proceso aparte hace de «aplicación en ejecución» que el .bat espera.
4. La versión nueva arranca bien (escribe el marcador), se cuelga o falla; se
   comprueba la sustitución o la vuelta atrás y que los datos del usuario siguen
   intactos byte a byte.

Tarda unos segundos por caso (esperas reales del script).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="El script de sustitución es un .bat de Windows")
pytest.importorskip("cryptography")

from tests.apoyo_actualizador import RAIZ, Servidor, cargar_firmador, publicar  # noqa: E402

from app.services import actualizador  # noqa: E402

# pythonw evita que se abran ventanas de consola al relanzar el programa falso.
PYTHON_SIN_VENTANA = (
	str(Path(sys.executable).with_name("pythonw.exe"))
	if Path(sys.executable).with_name("pythonw.exe").is_file()
	else sys.executable
)

APP_FALSA = '''"""Programa falso que hace de aplicación instalada (versión {version})."""
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
VERSION = "{version}"
COMPORTAMIENTO = "{comportamiento}"


def main():
	logs = RAIZ / "data" / "logs"
	logs.mkdir(parents=True, exist_ok=True)
	with open(logs / "arranques.txt", "a", encoding="utf-8") as registro:
		registro.write(VERSION + " " + " ".join(sys.argv[1:]) + "\\n")
	if "--tras-actualizar" not in sys.argv:
		return 0
	if COMPORTAMIENTO == "falla":
		return 3
	if COMPORTAMIENTO == "cuelga":
		time.sleep(600)
		return 0
	time.sleep(1)
	from app.services import actualizador
	config = actualizador.Configuracion(version_actual=VERSION, carpeta_app=RAIZ, comando=[])
	actualizador.confirmar_arranque(config, tras_actualizar=True)
	time.sleep(2)
	return 0


sys.exit(main())
'''


def _codigo_app() -> dict[str, bytes]:
	"""El actualizador real va dentro de la instalación: lo usa la versión nueva."""
	return {
		"app/__init__.py": b"",
		"app/services/__init__.py": b"",
		"app/services/actualizador.py": (RAIZ / "app" / "services" / "actualizador.py").read_bytes(),
	}


def _archivos_version(version: str, comportamiento: str) -> dict[str, bytes]:
	archivos = {
		"CtrlIA.exe": f"EXE {version}".encode(),
		"lib/modulo.txt": f"modulo {version}".encode(),
		f"lib/solo_{version}.txt": b"solo en esta version",
		"lib/paquete/data/interno.txt": f"interno {version}".encode(),
		"app_falsa.py": APP_FALSA.format(version=version, comportamiento=comportamiento).encode("utf-8"),
		"data/sounds/aviso.wav": f"sonido {version}".encode(),
	}
	archivos.update(_codigo_app())
	return archivos


def _crear_instalacion(carpeta: Path) -> None:
	for nombre, contenido in _archivos_version("1.0.0", "sana").items():
		ruta = carpeta / nombre
		ruta.parent.mkdir(parents=True, exist_ok=True)
		ruta.write_bytes(contenido)
	usuario = {
		"data/config/ajustes.json": '{"tema": "oscuro", "voz": true}'.encode(),
		"data/db/base.db": os.urandom(4096),
		"data/notas/nota ñ.txt": "Mis notas personales".encode(),
		"data/sessions/sesion.bin": os.urandom(256),
	}
	for nombre, contenido in usuario.items():
		ruta = carpeta / nombre
		ruta.parent.mkdir(parents=True, exist_ok=True)
		ruta.write_bytes(contenido)


def _datos_usuario(datos: Path) -> dict[str, bytes]:
	"""Todo data/ salvo lo que el propio proceso escribe (registros, temporales,
	estado del actualizador) y los recursos que una versión puede refrescar."""
	resultado = {}
	for ruta in datos.rglob("*"):
		relativa = ruta.relative_to(datos)
		if (
			ruta.is_file()
			and relativa.parts[0] not in ("logs", "temp", "sounds")
			and ruta.name != "actualizaciones.json"
		):
			resultado[relativa.as_posix()] = ruta.read_bytes()
	return resultado


def _esperar(condicion, segundos: float) -> bool:
	limite = time.monotonic() + segundos
	while time.monotonic() < limite:
		if condicion():
			return True
		time.sleep(0.25)
	return condicion()


def _procesos_vivos(carpeta: Path) -> int:
	"""Cuántos procesos (salvo cmd y PowerShell) ejecutan algo de la instalación de prueba."""
	filtro = str(carpeta).replace("'", "''")
	orden = (
		"@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
		+ filtro
		+ "') "
		"-and $_.Name -notmatch '^(cmd|powershell)' }).Count"
	)
	salida = subprocess.run(
		["powershell", "-NoProfile", "-NonInteractive", "-Command", orden],
		capture_output=True,
		text=True,
		timeout=60,
		check=False,
	)
	return int(salida.stdout.strip() or 0)


def _terminar_restos(carpeta: Path) -> None:
	"""Mata cualquier proceso que siga ejecutando algo de la instalación de prueba."""
	filtro = str(carpeta).replace("'", "''")
	orden = (
		"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
		+ filtro
		+ "') } "
		"| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
	)
	subprocess.run(
		["powershell", "-NoProfile", "-NonInteractive", "-Command", orden], capture_output=True, timeout=60, check=False
	)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
	firmador = cargar_firmador()
	monkeypatch.delenv(firmador.VARIABLE_CONTRASENA, raising=False)
	clave = tmp_path / "claves" / "firma.key"
	publica = firmador.generar(clave)
	servidor = Servidor(tmp_path / "servidor")
	instalacion = tmp_path / "Programas d'prueba ñ" / "Mi App"
	_crear_instalacion(instalacion)
	procesos: list[subprocess.Popen] = []
	yield {"clave": clave, "publica": publica, "servidor": servidor, "instalacion": instalacion, "procesos": procesos}
	servidor.cerrar()
	for proceso in procesos:
		if proceso.poll() is None:
			proceso.kill()
	_terminar_restos(instalacion)


def _ejecutar(
	entorno, comportamiento: str, *, espera_salud: int = 20, espera_cierre: int = 30, vida_app: float = 3, **opciones
):
	"""Publica la 1.1.0, prepara el script, lo lanza y espera a que termine."""
	instalacion: Path = entorno["instalacion"]
	publicar(
		entorno["servidor"].carpeta,
		"1.1.0",
		{**_archivos_version("1.1.0", comportamiento), "data/config/ajustes.json": b'{"sobrescrito": 1}'},
		entorno["clave"],
	)
	app_en_marcha = subprocess.Popen([PYTHON_SIN_VENTANA, "-c", f"import time; time.sleep({vida_app})"])
	entorno["procesos"].append(app_en_marcha)
	config = actualizador.Configuracion(
		version_actual="1.0.0",
		carpeta_app=instalacion,
		url_manifiesto=entorno["servidor"].url("version.json"),
		claves_publicas=(entorno["publica"],),
		comando=[PYTHON_SIN_VENTANA, str(instalacion / "app_falsa.py")],
		permitir_desde_codigo=True,
		espera_salud=espera_salud,
		espera_cierre=espera_cierre,
		pid_a_esperar=app_en_marcha.pid,
		**opciones,
	)
	info = actualizador.comprobar(config)
	assert info is not None and info.version == "1.1.0"
	ruta_bat = actualizador.descargar_y_preparar(config, info)
	script = actualizador.lanzar_sustitucion(ruta_bat)
	entorno["procesos"].append(script)
	script.wait(timeout=espera_cierre + espera_salud + 90)
	resultado = {}
	if config.archivo_resultado.is_file():
		for linea in config.archivo_resultado.read_text(encoding="utf-8").splitlines():
			clave, _sep, valor = linea.partition("=")
			resultado[clave] = valor
	return config, ruta_bat, resultado


def _leer(ruta: Path) -> bytes:
	return ruta.read_bytes()


def test_actualizacion_correcta(entorno):
	instalacion = entorno["instalacion"]
	antes = _datos_usuario(instalacion / "data")
	config, ruta_bat, resultado = _ejecutar(entorno, "sana")
	registro = config.registro_script.read_text(encoding="utf-8", errors="replace")
	assert resultado.get("estado") == "correcta", registro
	assert _leer(instalacion / "CtrlIA.exe") == b"EXE 1.1.0"
	assert _leer(instalacion / "lib" / "modulo.txt") == b"modulo 1.1.0"
	assert (instalacion / "lib" / "solo_1.1.0.txt").is_file()
	assert (instalacion / "lib" / "solo_1.0.0.txt").is_file()  # sin /MIR: los sobrantes quedan
	assert _leer(instalacion / "lib" / "paquete" / "data" / "interno.txt") == b"interno 1.1.0"
	assert _leer(instalacion / "data" / "sounds" / "aviso.wav") == b"sonido 1.1.0"  # recurso actualizable
	assert _datos_usuario(instalacion / "data") == antes  # datos del usuario intactos
	assert actualizador.leer_estado(config)["version_confirmada"] == "1.1.0"  # anti-rollback
	assert not config.carpeta_respaldo.exists()
	assert not (config.carpeta_trabajo / "nueva").exists()
	assert _esperar(lambda: not ruta_bat.exists(), 5)  # el .bat se borra solo
	assert "1.1.0 --tras-actualizar" in (instalacion / "data" / "logs" / "arranques.txt").read_text(encoding="utf-8")


def test_sin_recursos_actualizables_no_escribe_en_data(entorno):
	instalacion = entorno["instalacion"]
	config, _ruta, resultado = _ejecutar(entorno, "sana", data_actualizable=())
	assert resultado.get("estado") == "correcta", config.registro_script.read_text(encoding="utf-8", errors="replace")
	assert _leer(instalacion / "CtrlIA.exe") == b"EXE 1.1.0"
	assert _leer(instalacion / "data" / "sounds" / "aviso.wav") == b"sonido 1.0.0"


@pytest.mark.parametrize(
	("comportamiento", "motivo", "espera_salud"),
	[("cuelga", "sin_marcador_de_salud", 6), ("falla", "se_cerro_sin_confirmar", 30)],
)
def test_vuelta_atras(entorno, comportamiento, motivo, espera_salud):
	instalacion = entorno["instalacion"]
	antes = _datos_usuario(instalacion / "data")
	inicio = time.monotonic()
	config, _ruta, resultado = _ejecutar(entorno, comportamiento, espera_salud=espera_salud)
	duracion = time.monotonic() - inicio
	registro = config.registro_script.read_text(encoding="utf-8", errors="replace")
	assert resultado.get("estado") == "revertida", registro
	assert resultado.get("motivo") == motivo
	assert _leer(instalacion / "CtrlIA.exe") == b"EXE 1.0.0"
	assert _leer(instalacion / "lib" / "modulo.txt") == b"modulo 1.0.0"
	assert not (instalacion / "lib" / "solo_1.1.0.txt").exists()  # la restauración quita lo nuevo
	assert (instalacion / "lib" / "solo_1.0.0.txt").is_file()
	assert _leer(instalacion / "lib" / "paquete" / "data" / "interno.txt") == b"interno 1.0.0"
	assert _leer(instalacion / "data" / "sounds" / "aviso.wav") == b"sonido 1.0.0"
	assert _datos_usuario(instalacion / "data") == antes
	assert (config.carpeta_respaldo / "programa" / "CtrlIA.exe").is_file()  # se conserva para diagnosticar
	if comportamiento == "falla":
		assert duracion < 25, "un fallo al arrancar debe detectarse sin agotar la espera"
	# La versión anterior se relanza (sin --tras-actualizar) y el usuario recibe el aviso.
	arranques = instalacion / "data" / "logs" / "arranques.txt"
	assert _esperar(lambda: "1.0.0 \n" in arranques.read_text(encoding="utf-8"), 20)
	# La versión nueva colgada la terminó el script, y la anterior relanzada ya salió.
	assert _esperar(lambda: _procesos_vivos(instalacion) == 0, 20)
	mensaje = actualizador.leer_resultado_anterior(config)
	assert mensaje and "1.1.0" in mensaje
	assert actualizador.leer_estado(config)["version_omitida"] == "1.1.0"


def test_si_la_aplicacion_no_se_cierra_no_se_toca_nada(entorno):
	instalacion = entorno["instalacion"]
	config, _ruta, resultado = _ejecutar(entorno, "sana", espera_cierre=3, vida_app=120)
	assert resultado.get("estado") == "no_aplicada"
	assert resultado.get("motivo") == "la_aplicacion_no_se_cerro"
	assert _leer(instalacion / "CtrlIA.exe") == b"EXE 1.0.0"
	assert not config.carpeta_respaldo.exists()
	assert not (config.carpeta_trabajo / "nueva").exists()
