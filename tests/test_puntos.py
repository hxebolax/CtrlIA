"""Puntos de restauración (``app/services/puntos.py``) con git de verdad en carpetas temporales.

Nunca se toca el proyecto: cada prueba crea su carpeta y su «repositorio de la
persona» en ``tmp_path``; los repositorios sombra van a la ``data`` temporal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import pytest

from app.services import puntos
from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	Conversacion,
	Estado,
	EstadoProveedor,
	Proveedor,
	RespuestaPermiso,
	TipoEvento,
)
from app.services.almacen import Almacen
from app.services.gestor import GestorSesiones

GIT = shutil.which("git") or (r"C:\Program Files\Git\cmd\git.exe" if Path(r"C:\Program Files\Git\cmd\git.exe").is_file() else None)
pytestmark = pytest.mark.skipif(GIT is None, reason="Hace falta Git para Windows")


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------


def git_persona(carpeta: Path, *argumentos: str) -> str:
	"""git en el repositorio «de la persona», como lo usaría ella (sin variables GIT_* de las pruebas)."""
	entorno = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
	entorno["GIT_OPTIONAL_LOCKS"] = "0"  # que «status» no reescriba su índice
	resultado = subprocess.run([GIT, "-c", "user.name=Persona", "-c", "user.email=p@ejemplo.invalid", "-c", "commit.gpgSign=false",
		"-c", "core.hooksPath=NUL", *argumentos], cwd=carpeta, env=entorno, capture_output=True, check=True)
	return resultado.stdout.decode("utf-8", "replace")


def escribir(ruta: Path, datos: bytes | str) -> None:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	ruta.write_bytes(datos.encode("utf-8") if isinstance(datos, str) else datos)


def esperar(condicion, segundos: float = 15.0) -> bool:
	limite = time.monotonic() + segundos
	while time.monotonic() < limite:
		if condicion():
			return True
		time.sleep(0.05)
	return condicion()


def esperar_hilos_de_puntos() -> None:
	for hilo in threading.enumerate():
		if hilo.name in ("puntos", "puntos-envio") and hilo is not threading.current_thread():
			hilo.join(30)


@pytest.fixture(autouse=True)
def _git_de_nuevo():
	puntos.reiniciar_deteccion()
	yield
	esperar_hilos_de_puntos()
	puntos.reiniciar_deteccion()


@pytest.fixture
def proyecto(tmp_path: Path) -> Path:
	"""Proyecto de la persona: repositorio git con .gitignore, finales de línea mezclados, binarios y acentos."""
	carpeta = tmp_path / "proyecto"
	escribir(carpeta / ".gitignore", "*.log\nsalida/\n")
	escribir(carpeta / ".gitattributes", "* text=auto eol=crlf\n")
	escribir(carpeta / "app.py", "print('hola')\n")
	escribir(carpeta / "lf.txt", b"uno\ndos\n")
	escribir(carpeta / "crlf.txt", b"uno\r\ndos\r\n")
	escribir(carpeta / "mixto.txt", b"a\r\nb\nc\r\n")
	escribir(carpeta / "binario.bin", bytes(range(256)) * 4)
	escribir(carpeta / "carpeta con espacios" / "año ñandú ÁÉÍ.txt", "texto con acentos\n")
	escribir(carpeta / "registro.log", "ignorado\n")
	escribir(carpeta / "salida" / "objeto.o", "ignorado\n")
	escribir(carpeta / "node_modules" / "paquete" / "index.js", "excluido\n")
	git_persona(carpeta, "init", "-q")
	git_persona(carpeta, "add", "-A")
	git_persona(carpeta, "commit", "-q", "-m", "inicial")
	# Cambios sin confirmar de la persona: uno preparado y otro sin seguimiento.
	escribir(carpeta / "app.py", "print('hola, persona')\n")
	git_persona(carpeta, "add", "app.py")
	escribir(carpeta / "suyo.txt", "nuevo de la persona\n")
	return carpeta


def contenido(carpeta: Path) -> dict[str, bytes]:
	return {
		p.relative_to(carpeta).as_posix(): p.read_bytes()
		for p in carpeta.rglob("*") if p.is_file() and ".git" not in p.relative_to(carpeta).parts
	}


def huella_git(carpeta: Path) -> dict[str, tuple[int, int]]:
	return {
		str(p.relative_to(carpeta)): (p.stat().st_mtime_ns, p.stat().st_size)
		for p in (carpeta / ".git").rglob("*") if p.is_file()
	}


# ----------------------------------------------------------------------
# El repositorio sombra
# ----------------------------------------------------------------------


def test_punto_vista_previa_restaurar_y_rehacer(proyecto: Path):
	estado_antes = git_persona(proyecto, "status", "--porcelain=v1", "-uall")
	huella_antes = huella_git(proyecto)
	indice_antes = (proyecto / ".git" / "index").stat().st_mtime_ns
	refs_antes = git_persona(proyecto, "for-each-ref")

	punto = puntos.crear_punto(proyecto, "ctrlia:s1", "m1")
	assert len(punto.commit) in (40, 64)
	assert puntos.ruta_repositorio(proyecto).is_dir()
	assert not (proyecto / ".git" / "refs" / "puntos").exists()
	originales = contenido(proyecto)

	# El «agente» cambia, borra y crea archivos (también ignorados y en carpetas excluidas).
	escribir(proyecto / "app.py", "print('cambiado por el agente')\n")
	escribir(proyecto / "crlf.txt", b"cambiado\n")
	escribir(proyecto / "binario.bin", b"\x00\x01otra cosa")
	(proyecto / "lf.txt").unlink()
	shutil.rmtree(proyecto / "carpeta con espacios")
	escribir(proyecto / "nuevo.py", "x = 1\n")
	escribir(proyecto / "nueva carpeta" / "más" / "profundo ñ [1].txt", "nuevo\n")
	escribir(proyecto / "nuevo.log", "nuevo pero ignorado\n")
	escribir(proyecto / "node_modules" / "otro" / "x.js", "excluido nuevo\n")

	vista = puntos.vista_previa(proyecto, "ctrlia:s1", punto.commit)
	acciones = {c.ruta: c.accion for c in vista.cambios}
	assert acciones == {
		"app.py": puntos.ACCION_DEVOLVER, "crlf.txt": puntos.ACCION_DEVOLVER, "binario.bin": puntos.ACCION_DEVOLVER,
		"lf.txt": puntos.ACCION_RECREAR, "carpeta con espacios/año ñandú ÁÉÍ.txt": puntos.ACCION_RECREAR,
		"nuevo.py": puntos.ACCION_BORRAR, "nueva carpeta/más/profundo ñ [1].txt": puntos.ACCION_BORRAR,
	}
	assert puntos.Cambio("x", puntos.ACCION_BORRAR).texto() == "Se borrará (lo creó el agente)"
	assert not vista.head_cambiado

	resultado = puntos.restaurar(proyecto, "ctrlia:s1", punto.commit)
	assert resultado.total == 7 and not resultado.fallidos and resultado.seguridad
	ahora = contenido(proyecto)
	for ruta, datos in originales.items():
		assert ahora.get(ruta) == datos, ruta  # bytes exactos: CRLF, LF, mezclados, binario, acentos
	assert not (proyecto / "nuevo.py").exists() and not (proyecto / "nueva carpeta").exists()
	assert (proyecto / "nuevo.log").exists(), "lo ignorado por el .gitignore no se toca"
	assert (proyecto / "node_modules" / "otro" / "x.js").exists(), "lo excluido no se toca"
	# El repositorio de la persona, intacto.
	assert huella_git(proyecto) == huella_antes
	assert (proyecto / ".git" / "index").stat().st_mtime_ns == indice_antes
	assert git_persona(proyecto, "for-each-ref") == refs_antes
	estado = [linea for linea in git_persona(proyecto, "status", "--porcelain=v1", "-uall").splitlines()
		if "nuevo.log" not in linea and "node_modules" not in linea]
	assert estado == estado_antes.splitlines()
	assert puntos.vista_previa(proyecto, "ctrlia:s1", punto.commit).cambios == []

	# Rehacer: vuelve al punto de seguridad, como lo dejó el agente.
	puntos.restaurar(proyecto, "ctrlia:s1", resultado.seguridad, seguridad="")
	assert (proyecto / "nuevo.py").read_text(encoding="utf-8") == "x = 1\n"
	assert not (proyecto / "lf.txt").exists()
	assert (proyecto / "crlf.txt").read_bytes() == b"cambiado\n"
	assert huella_git(proyecto) == huella_antes


def test_gitignore_y_exclusiones_fuera_del_punto(proyecto: Path):
	punto = puntos.crear_punto(proyecto, "ctrlia:s1", "m1")
	sombra = puntos.Sombra(proyecto)
	lista = sombra.orden("ls-tree", "-r", "--name-only", "-z", punto.commit).stdout.decode("utf-8").split("\0")
	assert "registro.log" not in lista
	assert not any(r.startswith(("salida/", "node_modules/", ".git/")) for r in lista)
	assert {".gitignore", "suyo.txt", "carpeta con espacios/año ñandú ÁÉÍ.txt"} <= set(lista)
	blob = sombra.orden("cat-file", "blob", f"{punto.commit}:crlf.txt").stdout
	assert blob == b"uno\r\ndos\r\n"


def test_commits_nuevos_en_el_repositorio_de_la_persona_se_avisan(proyecto: Path):
	punto = puntos.crear_punto(proyecto, "ctrlia:s1", "m1")
	escribir(proyecto / "app.py", "print('del agente')\n")
	git_persona(proyecto, "commit", "-q", "-am", "commit del agente")
	vista = puntos.vista_previa(proyecto, "ctrlia:s1", punto.commit)
	assert vista.head_cambiado
	assert any("commits" in aviso for aviso in puntos.avisos_de(vista))


def test_cambios_a_mano_despues_del_ultimo_turno(proyecto: Path):
	punto = puntos.crear_punto(proyecto, "ctrlia:s1", "m1")
	escribir(proyecto / "app.py", "print('agente')\n")
	time.sleep(0.01)
	puntos.crear_punto(proyecto, "ctrlia:s1", puntos.PUNTO_FIN)
	escribir(proyecto / "lf.txt", b"cambiado a mano\n")
	vista = puntos.vista_previa(proyecto, "ctrlia:s1", punto.commit)
	assert vista.a_mano == ["lf.txt"]
	assert "lf.txt" in puntos.avisos_de(vista)[0]


def test_archivos_grandes_no_se_guardan_ni_se_borran(proyecto: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setattr(puntos, "MAX_TAMANO", 2000)
	escribir(proyecto / "grande [x].bin", b"\0" * 5000)
	punto = puntos.crear_punto(proyecto, "ctrlia:s1", "m1")
	assert punto.grandes == ["grande [x].bin"]
	escribir(proyecto / "otro grande.bin", b"\1" * 5000)
	escribir(proyecto / "pequeño.txt", "x\n")
	vista = puntos.vista_previa(proyecto, "ctrlia:s1", punto.commit)
	assert [c.ruta for c in vista.cambios] == ["pequeño.txt"]
	assert "otro grande.bin" in vista.grandes
	puntos.restaurar(proyecto, "ctrlia:s1", punto.commit)
	assert (proyecto / "grande [x].bin").exists() and (proyecto / "otro grande.bin").exists()
	assert not (proyecto / "pequeño.txt").exists()


def test_limite_de_archivos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setattr(puntos, "MAX_ARCHIVOS", 5)
	carpeta = tmp_path / "muchos"
	for numero in range(8):
		escribir(carpeta / f"a{numero}.txt", "x\n")
	with pytest.raises(puntos.PuntosNoDisponibles, match="más de 5"):
		puntos.crear_punto(carpeta, "ctrlia:s1", "m1")
	# La segunda vez ni se cuenta: se recuerda unas horas.
	for numero in range(8):
		(carpeta / f"a{numero}.txt").unlink()
	with pytest.raises(puntos.PuntosNoDisponibles):
		puntos.crear_punto(carpeta, "ctrlia:s1", "m2")


def test_carpetas_donde_no_se_guardan(tmp_path: Path):
	assert puntos.motivo_carpeta(tmp_path) == ""
	assert "carpeta personal" in puntos.motivo_carpeta(Path.home())
	assert "raíz" in puntos.motivo_carpeta(Path(Path.home().anchor))
	assert "red" in puntos.motivo_carpeta(r"\\servidor\recurso\proyecto")
	assert "no existe" in puntos.motivo_carpeta(tmp_path / "no_existe")
	assert puntos.motivo_carpeta("relativa") != ""
	with pytest.raises(puntos.PuntosNoDisponibles, match="carpeta personal"):
		puntos.crear_punto(Path.home(), "ctrlia:s1", "m1")


def test_sin_git_no_esta_disponible_y_se_dice_por_que(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setattr(puntos, "_buscar_git", lambda: None)
	puntos.reiniciar_deteccion()
	disponible, motivo = puntos.disponible()
	assert not disponible and "Git para Windows" in motivo
	escribir(tmp_path / "p" / "a.txt", "a\n")
	with pytest.raises(puntos.PuntosNoDisponibles, match="Git para Windows"):
		puntos.crear_punto(tmp_path / "p", "ctrlia:s1", "m1")


def test_rutas_seguras(tmp_path: Path):
	raiz = tmp_path / "raiz"
	escribir(raiz / "a" / "b.txt", "b\n")
	assert puntos.ruta_dentro(raiz, "a/b.txt") == raiz / "a" / "b.txt"
	for mala in ("../fuera.txt", "a/../../fuera.txt", "C:/Windows/x", "/a/b.txt", ".git/config", "a/.GIT/x", "a\\b.txt",
			"a/b.txt:flujo", ""):
		assert puntos.ruta_dentro(raiz, mala) is None, mala
	assert puntos.borrar_seguro(raiz, "../fuera.txt") is not None


def test_no_se_borra_nada_detras_de_una_union(tmp_path: Path):
	fuera = tmp_path / "fuera"
	escribir(fuera / "importante.txt", "no tocar\n")
	carpeta = tmp_path / "trabajo"
	escribir(carpeta / "a.txt", "a\n")
	creada = subprocess.run(["cmd", "/c", "mklink", "/J", str(carpeta / "enlace"), str(fuera)], capture_output=True, check=False)
	if creada.returncode != 0:
		pytest.skip("No se pudo crear una unión de Windows")
	assert puntos.borrar_seguro(carpeta, "enlace/importante.txt") is not None
	assert (fuera / "importante.txt").exists()
	assert puntos.borrar_seguro(carpeta, "a.txt") is None and not (carpeta / "a.txt").exists()


def test_limpieza_y_borrar_la_sesion(proyecto: Path, monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setattr(puntos, "MAX_PUNTOS_SESION", 2)
	for numero in range(4):
		escribir(proyecto / "app.py", f"v{numero}\n")
		puntos.crear_punto(proyecto, "ctrlia:s1", f"m{numero}")
	puntos.crear_punto(proyecto, "ctrlia:s2", "m9")
	sombra = puntos.Sombra(proyecto)
	assert puntos.limpiar(proyecto, "ctrlia:s1") == 2
	assert sorted(r.rsplit("/", 1)[-1] for r, _f in sombra._refs("refs/puntos/ctrlia_s1/")) == ["m2", "m3"]
	# Pasados 30 días se borran todos.
	sombra.limpiar(None, ahora=time.time() + (puntos.DIAS_PUNTOS + 1) * 86400)
	assert sombra._refs() == []
	puntos.crear_punto(proyecto, "ctrlia:s1", "m5")
	puntos.borrar_sesion(proyecto, "ctrlia:s1")
	assert not puntos.ruta_repositorio(proyecto).exists(), "sin puntos, el repositorio sombra se borra"
	puntos.crear_punto(proyecto, "ctrlia:s1", "m6")
	puntos.limpiar_todo(ahora=time.time() + (puntos.DIAS_PUNTOS + 1) * 86400)
	assert not puntos.ruta_repositorio(proyecto).exists()


def test_punto_inexistente_se_explica(proyecto: Path):
	with pytest.raises(puntos.ErrorPuntos, match="ya no existe"):
		puntos.vista_previa(proyecto, "ctrlia:s1", "0" * 40)
	with pytest.raises(puntos.ErrorPuntos, match="no es válido"):
		puntos.vista_previa(proyecto, "ctrlia:s1", "--output=x")


# ----------------------------------------------------------------------
# Con el gestor de sesiones y un agente falso
# ----------------------------------------------------------------------


class ConversacionQueEscribe(Conversacion):
	"""Agente falso que, al recibir un mensaje, cambia archivos de su carpeta y termina el turno."""

	def __init__(self, ajustes, al_evento):
		super().__init__(ajustes, al_evento)
		self.enviados: list[str] = []
		# En las sesiones por SSH la carpeta es de otro equipo: aquí no se escribe nada.
		self.automatico = "remoto" not in ajustes.extra
		self.ultimo_uuid_usuario: str | None = None
		self.llamadas_deshacer: list[tuple[str, bool]] = []

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		self.enviados.append(texto)
		self.ultimo_uuid_usuario = f"uuid-{len(self.enviados)}"
		self._cambiar_estado(Estado.TRABAJANDO)
		if not self.automatico:
			return
		carpeta = Path(str(self.ajustes.carpeta))
		escribir(carpeta / "app.py", f"print('turno {len(self.enviados)}')\n")
		escribir(carpeta / f"nuevo{len(self.enviados)}.py", "x = 1\n")
		self.terminar()

	def terminar(self) -> None:
		self._emitir(TipoEvento.FIN_TURNO, correcto=True, interrumpido=False)
		self._cambiar_estado(Estado.INACTIVA)

	def detener(self) -> None:
		self._cambiar_estado(Estado.INACTIVA)

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		pass

	def cerrar(self) -> None:
		pass

	def deshacer_archivos(self, uuid_mensaje: str, prueba: bool) -> dict:
		self.llamadas_deshacer.append((uuid_mensaje, prueba))
		if uuid_mensaje != "uuid-1":
			raise ValueError("no hay punto")
		return {"canRewind": True, "error": None, "filesChanged": ["/home/p/app.py"], "insertions": 3, "deletions": 1}


class ProveedorQueEscribe(Proveedor):
	id = "falso"
	nombre = "Agente falso"

	def __init__(self):
		self.conversaciones: list[ConversacionQueEscribe] = []

	def detectar(self) -> EstadoProveedor:
		return EstadoProveedor(True, version="1.0")

	def crear_conversacion(self, ajustes, al_evento):
		conversacion = ConversacionQueEscribe(ajustes, al_evento)
		self.conversaciones.append(conversacion)
		return conversacion


@pytest.fixture
def gestor(tmp_path: Path):
	almacen = Almacen(tmp_path / "g.db")
	proveedor = ProveedorQueEscribe()
	opciones = {"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False,
		"agentes.puntos_restauracion": True}
	gestor = GestorSesiones(almacen, {"falso": proveedor, "claude": proveedor}, lambda c, e: None,
		lambda clave, defecto: opciones.get(clave, defecto))
	yield gestor, proveedor, opciones
	esperar_hilos_de_puntos()
	almacen.cerrar()


def _mensajes_usuario(gestor: GestorSesiones, clave: str):
	return [m for m in gestor.almacen.mensajes(clave) if m.rol == "usuario"]


def test_gestor_guarda_el_punto_antes_de_enviar_y_deshace(gestor, proyecto: Path):
	g, proveedor, _opciones = gestor
	clave = g.nueva_sesion("falso", AjustesSesion(carpeta=proyecto, titulo="Prueba"))
	original = (proyecto / "app.py").read_bytes()
	g.enviar(clave, "Cambia app.py")
	assert esperar(lambda: not g.ocupada(clave) and proveedor.conversaciones and proveedor.conversaciones[0].enviados)
	esperar_hilos_de_puntos()
	mensaje = _mensajes_usuario(g, clave)[0]
	assert mensaje.datos.get("punto"), mensaje.datos
	assert (proyecto / "nuevo1.py").exists()

	plan = g.plan_deshacer(clave, mensaje.id)
	assert {c.ruta: c.accion for c in plan.cambios} == {"app.py": puntos.ACCION_DEVOLVER, "nuevo1.py": puntos.ACCION_BORRAR}
	resultado = g.deshacer(plan)
	assert resultado.total == 2
	assert (proyecto / "app.py").read_bytes() == original and not (proyecto / "nuevo1.py").exists()
	sistema = [m.texto for m in g.almacen.mensajes(clave) if m.rol == "sistema"]
	assert sistema[-1] == "Se deshicieron los cambios de 2 archivos hasta el mensaje «Cambia app.py»."
	assert g.puede_rehacer(clave)

	# El siguiente mensaje lleva la nota delante (una sola vez); lo guardado es lo que escribió la persona.
	conversacion = proveedor.conversaciones[0]
	conversacion.automatico = False
	g.enviar(clave, "Sigue")
	assert esperar(lambda: len(conversacion.enviados) == 2)
	assert conversacion.enviados[1].startswith("[Ctrl+IA] La persona ha deshecho los cambios de archivos hasta su mensaje")
	assert "app.py" in conversacion.enviados[1] and conversacion.enviados[1].endswith("\n\nSigue")
	assert _mensajes_usuario(g, clave)[-1].texto == "Sigue"
	assert "nota_deshacer" not in g.almacen.sesion(clave).extra
	conversacion.terminar()
	esperar_hilos_de_puntos()

	# Rehacer: vuelve a como lo dejó el agente.
	plan = g.plan_rehacer(clave)
	assert plan.rehacer and plan.cambios
	g.rehacer(plan)
	assert (proyecto / "nuevo1.py").exists()
	assert (proyecto / "app.py").read_text(encoding="utf-8") == "print('turno 1')\n"
	assert not g.puede_rehacer(clave)
	assert g.almacen.sesion(clave).extra["nota_deshacer"].startswith("[Ctrl+IA] La persona ha vuelto a poner")


def test_no_se_deshace_con_el_agente_trabajando(gestor, proyecto: Path):
	g, proveedor, _opciones = gestor
	clave = g.nueva_sesion("falso", AjustesSesion(carpeta=proyecto))
	g.enviar(clave, "hola")
	assert esperar(lambda: proveedor.conversaciones and proveedor.conversaciones[0].enviados)
	conversacion = proveedor.conversaciones[0]
	conversacion._cambiar_estado(Estado.TRABAJANDO)
	with pytest.raises(puntos.ErrorPuntos, match="trabajando"):
		g.plan_deshacer(clave, _mensajes_usuario(g, clave)[0].id)
	conversacion._cambiar_estado(Estado.INACTIVA)


def test_mensaje_en_cola_recibe_el_punto_del_fin_de_turno(gestor, proyecto: Path):
	g, proveedor, _opciones = gestor
	clave = g.nueva_sesion("falso", AjustesSesion(carpeta=proyecto))
	g.enviar(clave, "primero")
	assert esperar(lambda: not g.ocupada(clave) and proveedor.conversaciones and proveedor.conversaciones[0].enviados)
	conversacion = proveedor.conversaciones[0]
	esperar_hilos_de_puntos()
	conversacion.automatico = False
	conversacion._cambiar_estado(Estado.TRABAJANDO)
	g.enviar(clave, "segundo")  # el agente trabaja: va a su cola, sin esperar
	segundo = _mensajes_usuario(g, clave)[-1]
	assert segundo.datos.get("punto_en_cola") and not segundo.datos.get("punto")
	escribir(proyecto / "durante.txt", "lo hizo el agente en el turno anterior\n")
	conversacion.terminar()
	segundo = g.almacen.mensaje(segundo.id)
	assert segundo.datos.get("punto") and "punto_en_cola" not in segundo.datos
	assert puntos.Sombra(proyecto).resolver(clave, f"m{segundo.id}") == segundo.datos["punto"]


def test_detener_retira_el_mensaje_que_esperaba_su_punto(gestor, proyecto: Path, monkeypatch: pytest.MonkeyPatch):
	g, proveedor, _opciones = gestor
	liberar = threading.Event()
	crear = puntos.crear_punto

	def lento(*argumentos, **opciones):
		liberar.wait(10)
		return crear(*argumentos, **opciones)

	monkeypatch.setattr(puntos, "crear_punto", lento)
	clave = g.nueva_sesion("falso", AjustesSesion(carpeta=proyecto))
	g.enviar(clave, "espera")
	assert g.ocupada(clave) and clave in g.sesiones_ocupadas()
	assert g.detener(clave) == ["espera"]
	liberar.set()
	assert esperar(lambda: not g.ocupada(clave))
	esperar_hilos_de_puntos()
	time.sleep(0.2)
	assert all(not c.enviados for c in proveedor.conversaciones)
	assert _mensajes_usuario(g, clave)[0].datos.get("sin_punto")


def test_sin_opcion_ni_puntos_el_envio_es_inmediato(gestor, proyecto: Path):
	g, proveedor, opciones = gestor
	opciones["agentes.puntos_restauracion"] = False
	clave = g.nueva_sesion("falso", AjustesSesion(carpeta=proyecto))
	g.enviar(clave, "hola")
	assert proveedor.conversaciones[0].enviados == ["hola"]  # sin hilo: ya ha salido
	mensaje = _mensajes_usuario(g, clave)[0]
	assert "punto" not in mensaje.datos
	with pytest.raises(puntos.ErrorPuntos, match="no tiene punto de restauración"):
		g.plan_deshacer(clave, mensaje.id)


def test_sesion_en_carpeta_prohibida_dice_por_que(gestor, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
	g, proveedor, _opciones = gestor
	carpeta = tmp_path / "personal"
	carpeta.mkdir()
	monkeypatch.setattr(Path, "home", classmethod(lambda cls: carpeta))
	clave = g.nueva_sesion("falso", AjustesSesion(carpeta=carpeta))
	g.enviar(clave, "hola")
	assert esperar(lambda: not g.ocupada(clave) and proveedor.conversaciones[0].enviados)
	mensaje = _mensajes_usuario(g, clave)[0]
	assert "carpeta personal" in mensaje.datos.get("sin_punto", "")
	with pytest.raises(puntos.ErrorPuntos, match="carpeta personal"):
		g.plan_deshacer(clave, mensaje.id)


def test_claude_por_ssh_deshace_con_su_propio_punto(gestor):
	g, proveedor, _opciones = gestor
	extra = {"remoto": {"equipo": "mac", "nombre": "Mac del estudio", "carpeta": "/home/p"}}
	clave = g.nueva_sesion("claude", AjustesSesion(carpeta=PurePosixPath("/home/p"), extra=extra))
	g.enviar(clave, "Cambia app.py")
	conversacion = proveedor.conversaciones[0]
	assert conversacion.enviados == ["Cambia app.py"]  # sin repositorio sombra: sale en el acto
	mensaje = _mensajes_usuario(g, clave)[0]
	assert mensaje.datos.get("claude_uuid") == "uuid-1" and "punto" not in mensaje.datos
	conversacion.terminar()

	plan = g.plan_deshacer(clave, mensaje.id)
	assert plan.remoto and [c.ruta for c in plan.cambios] == ["/home/p/app.py"]
	assert any("3 líneas" in aviso for aviso in plan.avisos)
	resultado = g.deshacer(plan)
	assert conversacion.llamadas_deshacer == [("uuid-1", True), ("uuid-1", False)]
	assert resultado.total == 1 and not g.puede_rehacer(clave)
	assert g.almacen.sesion(clave).extra["nota_deshacer"].startswith("[Ctrl+IA]")
	# Un mensaje sin punto en Claude Code: el ValueError se explica.
	g.enviar(clave, "otro")
	otro = _mensajes_usuario(g, clave)[-1]
	assert otro.datos.get("claude_uuid") == "uuid-2"
	conversacion.terminar()
	with pytest.raises(puntos.ErrorPuntos, match="no hay punto"):
		g.plan_deshacer(clave, otro.id)


def test_borrar_la_sesion_borra_sus_puntos(gestor, proyecto: Path):
	g, proveedor, _opciones = gestor
	clave = g.nueva_sesion("falso", AjustesSesion(carpeta=proyecto))
	g.enviar(clave, "hola")
	assert esperar(lambda: not g.ocupada(clave) and proveedor.conversaciones[0].enviados)
	esperar_hilos_de_puntos()
	assert puntos.ruta_repositorio(proyecto).is_dir()
	g.borrar(clave)
	esperar_hilos_de_puntos()
	assert not puntos.ruta_repositorio(proyecto).exists()


# ----------------------------------------------------------------------
# El diálogo y la ventana
# ----------------------------------------------------------------------


def test_deshacer_y_rehacer_desde_la_ventana(app_wx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dialogo_modal,
		proyecto: Path):
	import wx

	from app.core.configuracion import Configuracion
	from app.ui import avisos
	from app.ui.ventana_principal import VentanaPrincipal
	from tests.conftest import procesar_eventos

	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	proveedor = ProveedorQueEscribe()
	marco = VentanaPrincipal(config, None, almacen=Almacen(tmp_path / "v.db"), proveedores={"falso": proveedor})

	def en_el_acto(self, funcion, *argumentos, al_terminar=None, al_fallar=None, clave=None):
		try:
			resultado = funcion(*argumentos)
		except Exception as error:  # noqa: BLE001 - como el gestor de tareas: el fallo va a al_fallar
			if al_fallar is None:
				raise
			al_fallar(error)
			return
		if al_terminar is not None:
			al_terminar(resultado)

	monkeypatch.setattr(type(marco), "en_segundo_plano", en_el_acto)
	try:
		procesar_eventos()
		clave = marco.gestor.nueva_sesion("falso", AjustesSesion(carpeta=proyecto, titulo="Ventana"))
		original = (proyecto / "app.py").read_bytes()
		marco.gestor.enviar(clave, "Cambia app.py")
		assert esperar(lambda: not marco.gestor.ocupada(clave) and proveedor.conversaciones[0].enviados)
		esperar_hilos_de_puntos()
		marco.refrescar_sesiones()
		procesar_eventos(lambda: marco.fila_de(clave) is not None)
		marco.abrir_sesion(clave)
		procesar_eventos(lambda: any(m.rol == "usuario" for m in marco.mensajes_completos()))

		marco.accion_deshacer_archivos()  # sin foco en la lista: desde tu último mensaje
		assert dialogo_modal.ultimo is not None and dialogo_modal.ultimo.titulo == "Deshacer los cambios de archivos"
		assert (proyecto / "app.py").read_bytes() == original and not (proyecto / "nuevo1.py").exists()
		assert any(d.startswith("Deshechos los cambios de 2 archivos.") for d in dichos), dichos
		assert any(m.rol == "sistema" and m.texto.startswith("Se deshicieron") for m in marco.mensajes_completos())

		dialogo_modal.responder(wx.ID_CANCEL)
		marco.accion_rehacer_archivos()
		assert dialogo_modal.ultimo.titulo == "Rehacer los cambios deshechos"
		assert not (proyecto / "nuevo1.py").exists(), "con Cancelar no cambia nada"
		marco.accion_rehacer_archivos()
		assert (proyecto / "nuevo1.py").exists()
		marco.accion_rehacer_archivos()
		assert "No hay cambios deshechos que rehacer" in dichos[-1]
	finally:
		esperar_hilos_de_puntos()
		marco.salir()
		procesar_eventos()



def test_dialogo_deshacer(app_wx):
	import wx

	from app.ui.dialogos.deshacer import DialogoDeshacer

	plan = puntos.PlanDeshacer("ctrlia:1", 3, "Cambia app.py", [puntos.Cambio("app.py", puntos.ACCION_DEVOLVER),
		puntos.Cambio("nuevo.py", puntos.ACCION_BORRAR)], ["Aviso de prueba."])
	dialogo = DialogoDeshacer(None, plan)
	try:
		assert dialogo.lista.numero_filas() == 2
		assert dialogo.lista.texto_celda(1, 1) == "Se borrará (lo creó el agente)"
		assert dialogo.avisos is not None and dialogo.avisos.GetValue() == "Aviso de prueba."
		assert dialogo.boton_aceptar.GetLabel() == "&Deshacer"
		assert dialogo.GetDefaultItem() is dialogo.boton_cancelar
		etiquetas = [w.GetLabel() for w in dialogo.GetChildren()[0].GetChildren() if isinstance(w, wx.StaticText)]
		assert "&Archivos:" in etiquetas and "A&visos:" in etiquetas
		assert dialogo.lista.GetHelpText() and dialogo.boton_aceptar.GetHelpText()
	finally:
		dialogo.Destroy()
	rehacer = DialogoDeshacer(None, puntos.PlanDeshacer("ctrlia:1", 3, "x", [puntos.Cambio("a", puntos.ACCION_BORRAR)],
		rehacer=True))
	try:
		assert rehacer.boton_aceptar.GetLabel() == "&Rehacer" and rehacer.avisos is None
		assert rehacer.lista.texto_celda(0, 1) == "Se borrará (como lo dejó el agente)"
	finally:
		rehacer.Destroy()
