"""Pruebas del adaptador de Ollama (``app/services/agentes/ollama.py``) con un ollama_falso falso local."""

from __future__ import annotations

import base64
import socket
import threading
import time
from pathlib import Path

import pytest

from app.services.agentes import crear_proveedores, ollama
from app.services.agentes.base import Adjunto, AjustesSesion, Estado, Evento, TipoEvento
from app.services.agentes.ollama import ProveedorOllama, normalizar_direccion
from tests.fakes.fake_ollama import ServidorOllamaFalso


class Registro:
	"""Recoge los eventos y deja esperar a que llegue uno concreto."""

	def __init__(self) -> None:
		self.eventos: list[Evento] = []
		self._condicion = threading.Condition()

	def __call__(self, evento: Evento) -> None:
		with self._condicion:
			self.eventos.append(evento)
			self._condicion.notify_all()

	def de_tipo(self, tipo: TipoEvento) -> list[Evento]:
		with self._condicion:
			return [evento for evento in self.eventos if evento.tipo is tipo]

	def esperar(self, tipo: TipoEvento, cuantos: int = 1, segundos: float = 15.0) -> list[Evento]:
		limite = time.monotonic() + segundos
		with self._condicion:
			while True:
				encontrados = [evento for evento in self.eventos if evento.tipo is tipo]
				if len(encontrados) >= cuantos:
					return encontrados
				restante = limite - time.monotonic()
				if restante <= 0:
					tipos = [str(evento.tipo) + ":" + evento.texto[:80] for evento in self.eventos]
					raise AssertionError("No llegó %s x%d. Eventos: %s" % (tipo, cuantos, tipos))
				self._condicion.wait(restante)

	def esperar_estado(self, estado: Estado, segundos: float = 15.0) -> None:
		limite = time.monotonic() + segundos
		with self._condicion:
			while not any(e.tipo is TipoEvento.ESTADO and e.datos.get("estado") is estado for e in self.eventos):
				restante = limite - time.monotonic()
				if restante <= 0:
					raise AssertionError("No llegó el estado %s" % estado)
				self._condicion.wait(restante)

	def errores(self) -> list[str]:
		return [evento.texto for evento in self.de_tipo(TipoEvento.ERROR)]


@pytest.fixture
def ollama_falso():
	with ServidorOllamaFalso() as falso:
		yield falso


def _puerto_libre() -> int:
	with socket.socket() as sock:
		sock.bind(("127.0.0.1", 0))
		return sock.getsockname()[1]


def conversar(ollama_falso: ServidorOllamaFalso, tmp_path: Path, modelo: str = "normal:latest", **ajustes):
	proveedor = ProveedorOllama(ollama_falso.direccion)
	registro = Registro()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, modelo=modelo, **ajustes), registro)
	return proveedor, conversacion, registro


# -- Dirección del ollama_falso ------------------------------------------------------


@pytest.mark.parametrize(("entrada", "esperada"), [
	("", "http://127.0.0.1:11434"),
	("localhost", "http://localhost:11434"),
	("0.0.0.0:11434", "http://127.0.0.1:11434"),
	(":11434", "http://127.0.0.1:11434"),
	("http://192.168.1.20:8080/", "http://192.168.1.20:8080"),
	("http://127.0.0.1:11434/api", "http://127.0.0.1:11434"),
	("https://ollama.ejemplo.test", "https://ollama.ejemplo.test"),
	('"[::1]:11434"', "http://[::1]:11434"),
])
def test_normalizar_direccion(entrada: str, esperada: str) -> None:
	assert normalizar_direccion(entrada) == esperada


def test_direccion_usa_ollama_host_si_no_hay_preferencia(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9999")
	assert ProveedorOllama("").direccion() == "http://127.0.0.1:9999"
	assert ProveedorOllama(lambda: "localhost:1234").direccion() == "http://localhost:1234"


def test_es_local() -> None:
	assert ollama.es_local("http://127.0.0.1:11434")
	assert ollama.es_local("http://localhost:11434")
	assert ollama.es_local("http://[::1]:11434")
	assert not ollama.es_local("http://192.168.1.20:11434")


# -- Detección y modelos ------------------------------------------------------------


def test_detectar_con_modelos(ollama_falso: ServidorOllamaFalso) -> None:
	estado = ProveedorOllama(ollama_falso.direccion).detectar()
	assert estado.disponible
	assert estado.version == "0.12.3"
	assert "normal:latest" in estado.mensaje
	assert "solo chat" in estado.mensaje


def test_detectar_sin_modelos(ollama_falso: ServidorOllamaFalso) -> None:
	ollama_falso.modelos.clear()
	estado = ProveedorOllama(ollama_falso.direccion).detectar()
	assert estado.disponible
	assert "ollama pull" in estado.mensaje


def test_detectar_sin_servidor(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(ollama, "buscar_ollama", lambda: None)
	estado = ProveedorOllama("127.0.0.1:%d" % _puerto_libre()).detectar()
	assert not estado.disponible
	assert "No se puede conectar con Ollama" in estado.mensaje
	assert "ollama.com" in estado.mensaje


def test_detectar_instalado_pero_parado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.setattr(ollama, "buscar_ollama", lambda: tmp_path / "ollama.exe")
	estado = ProveedorOllama("127.0.0.1:%d" % _puerto_libre()).detectar()
	assert estado.disponible
	assert "no responde" in estado.mensaje


def test_listar_modelos_ordenados_y_etiquetados(ollama_falso: ServidorOllamaFalso) -> None:
	proveedor = ProveedorOllama(ollama_falso.direccion)
	assert [m.id for m in proveedor.modelos()] == [""]
	modelos = proveedor.listar_modelos()
	ids = [m.id for m in modelos]
	assert ids[0] == ""
	assert ids[1] == "reciente:latest"  # el más reciente primero
	etiquetas = {m.id: m.etiqueta for m in modelos}
	assert etiquetas["normal:latest"] == "normal:latest, 3.2B, 1,9 GB"
	assert "nube de Ollama" in etiquetas["nube:cloud"]
	ayudas = {m.id: m.ayuda for m in modelos}
	assert "Q4_K_M" in ayudas["normal:latest"]
	# Lo recordado vale para la siguiente vez que se abra el diálogo.
	assert [m.id for m in proveedor.modelos()] == ids


def test_modos_y_esfuerzos() -> None:
	proveedor = ProveedorOllama()
	modos = proveedor.modos_permiso()
	assert [m.id for m in modos] == ["chat"]
	assert "sin herramientas" in modos[0].etiqueta
	assert proveedor.modo_predeterminado() == "chat"
	assert [e.id for e in proveedor.esfuerzos()] == ["", "no", "si", "low", "medium", "high"]
	assert not proveedor.capacidades.permisos_interactivos
	assert "ollama pull" in proveedor.instrucciones_instalacion()


# -- Conversación ---------------------------------------------------------------------


def test_conversacion_normal_guarda_historial(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path)
	try:
		conversacion.enviar("Hola")
		fin = registro.esperar(TipoEvento.FIN_TURNO)[0]
		assert fin.datos["correcto"]
		iniciado = registro.de_tipo(TipoEvento.INICIADO)[0]
		identificador = iniciado.datos["id_externo"]
		assert len(identificador) == 32
		assert iniciado.datos["modelo"] == "normal:latest"
		assert "solo chat" in iniciado.texto
		assert "".join(e.texto for e in registro.de_tipo(TipoEvento.TEXTO_PARCIAL)) == "Hola mundo"
		mensaje = registro.de_tipo(TipoEvento.MENSAJE)[0]
		assert mensaje.texto == "Hola mundo"
		uso = registro.de_tipo(TipoEvento.USO)[0]
		assert (uso.datos["entrada"], uso.datos["salida"], uso.datos["contexto_usado"]) == (12, 5, 17)
		registro.esperar_estado(Estado.INACTIVA)
		assert ollama_falso.mensajes() == [{"role": "user", "content": "Hola"}]
		assert ollama_falso.peticiones[-1]["stream"] is True
		assert "think" not in ollama_falso.peticiones[-1]

		conversacion.enviar("¿Y ahora?")
		registro.esperar(TipoEvento.FIN_TURNO, 2)
		assert ollama_falso.mensajes() == [
			{"role": "user", "content": "Hola"},
			{"role": "assistant", "content": "Hola mundo"},
			{"role": "user", "content": "¿Y ahora?"},
		]
		assert conversacion.historial()[0] == ("usuario", "Hola")
		guardado = ollama.leer_historial(identificador)
		assert len(guardado) == 4
		assert ollama.ruta_historial(identificador).is_file()
		assert not registro.errores()
	finally:
		conversacion.cerrar()
	assert conversacion.estado is Estado.CERRADA


def test_nunca_envia_claves(ollama_falso: ServidorOllamaFalso, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("OPENAI_API_KEY", "sk-secreta")
	monkeypatch.setenv("OLLAMA_API_KEY", "secreta")
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path)
	conversacion.enviar("Hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	conversacion.cerrar()
	for cabeceras in ollama_falso.cabeceras:
		assert not any(nombre.lower() == "authorization" for nombre in cabeceras)
		assert "Ctrl+IA" in cabeceras.get("User-Agent", "")


def test_retomar_y_bifurcar(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, primera, registro = conversar(ollama_falso, tmp_path)
	primera.enviar("Primero")
	registro.esperar(TipoEvento.FIN_TURNO)
	identificador = registro.de_tipo(TipoEvento.INICIADO)[0].datos["id_externo"]
	primera.cerrar()

	_proveedor, retomada, registro2 = conversar(ollama_falso, tmp_path, id_externo=identificador)
	retomada.enviar("Segundo")
	registro2.esperar(TipoEvento.FIN_TURNO)
	retomada.cerrar()
	assert registro2.de_tipo(TipoEvento.INICIADO)[0].datos["id_externo"] == identificador
	assert registro2.de_tipo(TipoEvento.INICIADO)[0].datos["retomada"]
	assert [m["content"] for m in ollama_falso.mensajes()] == ["Primero", "Hola mundo", "Segundo"]

	_proveedor, copia, registro3 = conversar(ollama_falso, tmp_path, bifurcar_de=identificador)
	copia.enviar("Tercero")
	registro3.esperar(TipoEvento.FIN_TURNO)
	copia.cerrar()
	nuevo = registro3.de_tipo(TipoEvento.INICIADO)[0].datos["id_externo"]
	assert nuevo != identificador
	assert len(ollama.leer_historial(nuevo)) == 6
	assert len(ollama.leer_historial(identificador)) == 4  # el original no cambia


def test_modelo_por_omision_es_el_mas_reciente(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="")
	conversacion.enviar("Hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	conversacion.cerrar()
	assert ollama_falso.peticiones[-1]["model"] == "reciente:latest"


def test_sin_modelos_instalados(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	ollama_falso.modelos.clear()
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="")
	conversacion.enviar("Hola")
	fin = registro.esperar(TipoEvento.FIN_TURNO)[0]
	registro.esperar_estado(Estado.ERROR)
	assert not fin.datos["correcto"]
	assert "ollama pull" in registro.errores()[0]
	assert conversacion.pendientes() == ["Hola"]
	conversacion.cerrar()


def test_modelo_no_instalado(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="inexistente")
	conversacion.enviar("Hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	error = registro.errores()[0]
	assert "inexistente" in error and "ollama pull inexistente" in error
	assert conversacion.pendientes() == ["Hola"]
	assert conversacion.historial() == []
	conversacion.cerrar()


def test_servidor_apagado(tmp_path: Path) -> None:
	proveedor = ProveedorOllama("127.0.0.1:%d" % _puerto_libre())
	registro = Registro()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, modelo="normal"), registro)
	conversacion.enviar("Hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	assert "No se puede conectar con Ollama" in registro.errores()[0]
	conversacion.cerrar()


def test_aviso_si_el_modelo_se_esta_cargando(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	ollama_falso.cargados.clear()
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path)
	conversacion.enviar("Hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	conversacion.cerrar()
	assert any("cargando el modelo normal:latest" in e.texto for e in registro.de_tipo(TipoEvento.AVISO))


def test_razonamiento(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="piensa", esfuerzo="si")
	conversacion.enviar("Piensa")
	registro.esperar(TipoEvento.FIN_TURNO)
	conversacion.cerrar()
	assert ollama_falso.peticiones[-1]["think"] is True
	assert registro.de_tipo(TipoEvento.RAZONAMIENTO)[0].texto == "Pensando..."
	assert registro.de_tipo(TipoEvento.MENSAJE)[0].texto == "Hola mundo"


def test_modelo_sin_razonamiento_se_reenvia_sin_think(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="sin-think", esfuerzo="high")
	conversacion.enviar("Hola")
	fin = registro.esperar(TipoEvento.FIN_TURNO)[0]
	conversacion.cerrar()
	assert fin.datos["correcto"]
	assert ollama_falso.peticiones[0]["think"] == "high"
	assert "think" not in ollama_falso.peticiones[1]
	assert any("no admite el ajuste de razonamiento" in e.texto for e in registro.de_tipo(TipoEvento.AVISO))


def test_error_a_mitad_de_respuesta(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="rompe")
	conversacion.enviar("Hola")
	fin = registro.esperar(TipoEvento.FIN_TURNO)[0]
	conversacion.cerrar()
	assert not fin.datos["correcto"]
	assert "se acabó la memoria" in registro.errores()[0]
	assert registro.de_tipo(TipoEvento.MENSAJE)[0].texto == "Empiezo"
	# Ollama recibió el mensaje: queda en el historial (no se reenvía nunca solo).
	assert conversacion.historial() == [("usuario", "Hola"), ("asistente", "Empiezo")]
	assert conversacion.pendientes() == []


def test_respuesta_cortada(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="corta")
	conversacion.enviar("Hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	conversacion.cerrar()
	assert "sin avisar" in registro.errores()[0]
	assert registro.de_tipo(TipoEvento.MENSAJE)[0].texto == "A medias"


@pytest.fixture
def cortes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
	"""Anota cada ``socket.shutdown``: ``(puerto del otro extremo, modo)``.

	Las pruebas comprueban lo que hace Ctrl+IA (cortar la conexión), no cuándo
	se entera el otro extremo: en Windows, un cierre por la interfaz de bucle
	local puede tardar segundos en notarse, y eso haría la prueba intermitente.
	"""
	anotados: list[tuple[int, int]] = []
	original = socket.socket.shutdown

	def espia(sock: socket.socket, modo: int) -> None:
		try:
			puerto = sock.getpeername()[1]
		except OSError:
			puerto = 0
		anotados.append((puerto, modo))
		original(sock, modo)

	monkeypatch.setattr(socket.socket, "shutdown", espia)
	return anotados


def test_detener_a_mitad_de_respuesta(ollama_falso: ServidorOllamaFalso, tmp_path: Path, cortes: list) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="goteo")
	conversacion.enviar("Escribe mucho")
	registro.esperar(TipoEvento.TEXTO_PARCIAL, 3)
	inicio = time.monotonic()
	conversacion.detener()
	fin = registro.esperar(TipoEvento.FIN_TURNO, segundos=5)[0]
	assert time.monotonic() - inicio < 3
	assert fin.datos["interrumpido"]
	assert registro.de_tipo(TipoEvento.MENSAJE)[0].texto.startswith("bla ")
	registro.esperar_estado(Estado.INACTIVA)
	# Ctrl+IA corta la conexión con Ollama (así deja de generar).
	puerto = int(ollama_falso.direccion.rsplit(":", 1)[1])
	assert (puerto, socket.SHUT_RDWR) in cortes
	assert not registro.errores()
	conversacion.cerrar()


@pytest.mark.parametrize("donde", ["conexion", "fp"])
def test_cortar_cierra_el_socket(donde: str, cortes: list) -> None:
	"""``_cortar`` encuentra el socket esté donde lo guarde urllib3, lo corta y cierra la respuesta."""
	sock = socket.socket()
	cerradas = []

	class Crudo:
		pass

	crudo = Crudo()
	if donde == "conexion":
		crudo._connection = type("Conexion", (), {"sock": sock})()
	else:
		crudo._fp = type("Fp", (), {"fp": type("Buffer", (), {"raw": type("SocketIO", (), {"_sock": sock})()})()})()
	respuesta = type("Respuesta", (), {"raw": crudo, "close": lambda self: cerradas.append(True)})()
	try:
		ollama._cortar(respuesta)  # sin conectar, shutdown falla: se registra y se sigue
	finally:
		sock.close()
	assert cortes == [(0, socket.SHUT_RDWR)]
	assert cerradas == [True]


def test_cortar_sin_socket_cierra_igualmente() -> None:
	cerradas = []
	respuesta = type("Respuesta", (), {"raw": None, "close": lambda self: cerradas.append(True)})()
	ollama._cortar(respuesta)
	assert cerradas == [True]


def test_detener_mientras_carga_el_modelo(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="carga")
	conversacion.enviar("Hola")
	limite = time.monotonic() + 5
	while not ollama_falso.peticiones and time.monotonic() < limite:
		time.sleep(0.02)
	conversacion.detener()
	fin = registro.esperar(TipoEvento.FIN_TURNO, segundos=3)[0]
	assert fin.datos["interrumpido"]
	registro.esperar_estado(Estado.INACTIVA)
	ollama_falso.liberar.set()
	assert not registro.errores()
	conversacion.cerrar()


def test_enviar_ahora_no_confunde_la_parada(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	proveedor = ProveedorOllama(ollama_falso.direccion)
	registro = Registro()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, modelo="goteo"), registro)
	conversacion.enviar("Escribe mucho")
	registro.esperar(TipoEvento.TEXTO_PARCIAL, 2)
	conversacion.cambiar_ajustes(modelo="normal:latest")
	conversacion.enviar_ahora("Otra cosa")
	fines = registro.esperar(TipoEvento.FIN_TURNO, 2)
	assert fines[0].datos["interrumpido"]
	assert fines[1].datos["correcto"]
	assert not registro.errores()
	assert ollama_falso.mensajes()[-1] == {"role": "user", "content": "Otra cosa"}
	conversacion.cerrar()


def test_mensajes_en_cola(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path, modelo="goteo")
	conversacion.enviar("Uno")
	registro.esperar(TipoEvento.TEXTO_PARCIAL)
	conversacion.enviar("Dos")
	assert any(e.datos.get("en_cola") == 1 for e in registro.de_tipo(TipoEvento.AVISO))
	conversacion.detener()
	registro.esperar(TipoEvento.FIN_TURNO)
	assert conversacion.pendientes() == ["Dos"]
	conversacion.cerrar()


def test_adjuntos(ollama_falso: ServidorOllamaFalso, tmp_path: Path) -> None:
	texto = tmp_path / "notas.txt"
	texto.write_text("línea uno\nlínea dos", encoding="utf-8")
	binario = tmp_path / "datos.bin"
	binario.write_bytes(b"\x00\x01\x02")
	imagen = tmp_path / "foto.png"
	imagen.write_bytes(b"\x89PNG\r\n\x1a\nfalso")
	_proveedor, conversacion, registro = conversar(ollama_falso, tmp_path)
	conversacion.enviar("Mira esto", [Adjunto(texto), Adjunto(binario), Adjunto(imagen, "image/png")])
	registro.esperar(TipoEvento.FIN_TURNO)
	conversacion.cerrar()
	mensaje = ollama_falso.mensajes()[-1]
	assert "Contenido del archivo notas.txt" in mensaje["content"]
	assert "línea dos" in mensaje["content"]
	assert "[Imagen adjunta: foto.png]" in mensaje["content"]
	assert mensaje["images"] == [base64.b64encode(imagen.read_bytes()).decode("ascii")]
	assert any("datos.bin no es un archivo de texto" in e.texto for e in registro.de_tipo(TipoEvento.AVISO))
	# Las imágenes no se guardan en el historial en disco.
	identificador = registro.de_tipo(TipoEvento.INICIADO)[0].datos["id_externo"]
	assert "images" not in ollama.ruta_historial(identificador).read_text(encoding="utf-8")


def test_historial_rechaza_identificadores_raros() -> None:
	assert ollama.ruta_historial("../../config/settings") is None
	assert ollama.leer_historial("..\\x") == []
	ollama.guardar_historial("no-vale", [{"role": "user", "content": "x"}])


def test_historial_danado(tmp_path: Path) -> None:
	identificador = "a" * 32
	ruta = ollama.ruta_historial(identificador)
	ruta.write_text("{no es json", encoding="utf-8")
	assert ollama.leer_historial(identificador) == []


# -- Catálogo ---------------------------------------------------------------------------


def test_catalogo_incluye_ollama() -> None:
	proveedores = crear_proveedores({"ollama_servidor": "127.0.0.1:1"})
	assert "ollama" in proveedores
	assert list(proveedores).index("ollama") == list(proveedores).index("gemini") + 1
	assert proveedores["ollama"].direccion() == "http://127.0.0.1:1"
	assert "ollama" not in crear_proveedores({"ollama": False})


def test_catalogo_lee_la_direccion_de_la_configuracion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	from app.core import configuracion

	config = configuracion.Configuracion(ruta=tmp_path / "settings.json")
	config.establecer("agentes.servidor_ollama", "localhost:5555", guardar=False)
	monkeypatch.setattr(configuracion, "_instancia", config)
	proveedor = crear_proveedores()["ollama"]
	assert proveedor.direccion() == "http://localhost:5555"
	config.establecer("agentes.servidor_ollama", "localhost:6666", guardar=False)
	assert proveedor.direccion() == "http://localhost:6666"  # sin reiniciar
