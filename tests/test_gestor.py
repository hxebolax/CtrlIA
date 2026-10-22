"""Pruebas del gestor de sesiones con un agente simulado (sin procesos ni red)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	ClaseOpcion,
	Conversacion,
	Estado,
	EstadoProveedor,
	OpcionPermiso,
	Proveedor,
	RespuestaPermiso,
	SolicitudPermiso,
	TipoEvento,
	TipoSolicitud,
)
from app.services.almacen import Almacen
from app.services.gestor import GestorSesiones, coincide, normalizar


class ConversacionFalsa(Conversacion):
	def __init__(self, ajustes, al_evento):
		super().__init__(ajustes, al_evento)
		self.enviados: list[str] = []
		self.respuestas: list[tuple[str, RespuestaPermiso]] = []
		self.cerrada = False

	def enviar(self, texto: str, adjuntos: Sequence[Adjunto] = ()) -> None:
		self.enviados.append(texto)
		self.ajustes.id_externo = self.ajustes.id_externo or "externo-1"
		self._emitir(TipoEvento.INICIADO, id_externo=self.ajustes.id_externo)
		self._cambiar_estado(Estado.TRABAJANDO)
		self._emitir(TipoEvento.TEXTO_PARCIAL, "Hol", id="m1")
		self._emitir(TipoEvento.MENSAJE, "Hola", id="m1")
		self._emitir(TipoEvento.MENSAJE, "Hola de nuevo", id="m1")
		self._emitir(TipoEvento.HERRAMIENTA, "Ejecutar orden: ls", herramienta="Bash", detalle="command: ls", id="t1")
		self._emitir(TipoEvento.HERRAMIENTA_FIN, herramienta="Bash", salida="a.txt", error=False, id="t1")
		self._emitir(TipoEvento.ARCHIVO, "a.txt", ruta="a.txt", tipo="modificado", diff="+x")
		self._emitir(TipoEvento.FIN_TURNO, correcto=True, interrumpido=False)
		self._cambiar_estado(Estado.INACTIVA)

	def detener(self) -> None:
		pass

	def responder(self, solicitud_id: str, respuesta: RespuestaPermiso) -> None:
		self.respuestas.append((solicitud_id, respuesta))
		self.ajustes.reglas_permitidas.append("Bash(ls)")

	def cerrar(self) -> None:
		self.cerrada = True


class ProveedorFalso(Proveedor):
	id = "falso"
	nombre = "Agente falso"

	def __init__(self):
		self.conversaciones: list[ConversacionFalsa] = []

	def detectar(self) -> EstadoProveedor:
		return EstadoProveedor(True, version="1.0")

	def crear_conversacion(self, ajustes, al_evento):
		conversacion = ConversacionFalsa(ajustes, al_evento)
		self.conversaciones.append(conversacion)
		return conversacion


@pytest.fixture
def entorno(tmp_path):
	almacen = Almacen(tmp_path / "g.db")
	proveedor = ProveedorFalso()
	eventos = []
	opciones = {"historial.claude": False, "historial.codex": False, "historial.antigravity": False, "historial.ollama": False}
	gestor = GestorSesiones(almacen, {"falso": proveedor}, lambda c, e: eventos.append((c, e)),
		lambda clave, defecto: opciones.get(clave, defecto))
	yield gestor, proveedor, eventos, tmp_path
	almacen.cerrar()


def test_normalizar_y_coincidir():
	assert normalizar("Canción ÁRBOL") == "cancion arbol"
	assert coincide("La canción del árbol", "arbol cancion")
	assert not coincide("La canción", "cancion perro")


def test_enviar_guarda_mensajes_y_estado(entorno):
	gestor, proveedor, eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta, titulo="Prueba"))
	gestor.enviar(clave, "¿Qué hay?")
	mensajes = gestor.mensajes(clave)
	roles = [m.rol for m in mensajes]
	assert roles == ["usuario", "asistente", "herramienta", "cambio"]
	assert mensajes[1].texto == "Hola de nuevo"  # el mismo id actualiza, no duplica
	assert "Resultado:\na.txt" in mensajes[2].detalle
	sesion = gestor.almacen.sesion(clave)
	assert sesion.id_externo == "externo-1"
	assert sesion.no_leidas == 1  # no estaba abierta
	assert gestor.texto_parcial(clave) == ""
	assert any(e.tipo is TipoEvento.FIN_TURNO for _c, e in eventos)
	assert gestor.almacen.carpetas_recientes() == [str(carpeta)]


def test_sesion_abierta_no_suma_sin_leer(entorno):
	gestor, _proveedor, _eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta))
	gestor.sesion_abierta = clave
	gestor.enviar(clave, "hola")
	assert gestor.almacen.sesion(clave).no_leidas == 0


def test_herramientas_ocultables(tmp_path):
	almacen = Almacen(tmp_path / "h.db")
	gestor = GestorSesiones(almacen, {"falso": ProveedorFalso()}, lambda c, e: None,
		lambda clave, defecto: False if clave in ("mensajes.herramientas", "historial.claude", "historial.codex", "historial.antigravity", "historial.ollama") else defecto)
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=tmp_path))
	gestor.enviar(clave, "hola")
	assert [m.rol for m in gestor.mensajes(clave)] == ["usuario", "asistente", "cambio"]
	almacen.cerrar()


def test_permisos_pendientes_y_respuesta(entorno):
	gestor, proveedor, _eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta))
	conversacion = gestor.conversacion(clave)
	solicitud = SolicitudPermiso("p1", TipoSolicitud.HERRAMIENTA, "¿Ejecutar?",
		opciones=[OpcionPermiso("permitir_sesion", "Permitir", ClaseOpcion.PERMITIR_SESION)])
	conversacion._emitir(TipoEvento.PERMISO, solicitud.titulo, solicitud=solicitud)
	assert gestor.solicitudes(clave)[0][1].id == "p1"
	assert gestor.listar()[0].estado == Estado.ESPERANDO_USUARIO
	gestor.responder(clave, "p1", RespuestaPermiso("permitir_sesion"))
	assert not gestor.solicitudes(clave)
	assert proveedor.conversaciones[0].respuestas[0][0] == "p1"
	assert gestor.almacen.sesion(clave).reglas == ["Bash(ls)"]


def test_denegacion_reintentable_se_convierte_en_peticion(entorno):
	gestor, _proveedor, _eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta))
	conversacion = gestor.conversacion(clave)
	conversacion._emitir(TipoEvento.AVISO, "Antigravity no pudo ejecutar npm porque necesita permiso.",
		reintentable=True, solicitud_id="denegada-1", regla="command(npm)")
	pendiente = gestor.solicitudes(clave)[0][1]
	assert pendiente.id == "denegada-1"
	assert "command(npm)" in pendiente.detalle
	assert pendiente.opcion_segura().id == "no_permitir"
	assert [o.id for o in pendiente.opciones] == ["permitir_reintentar", "no_permitir"]


def test_denegacion_de_herramienta_mcp_ofrece_todo_el_servidor(entorno):
	gestor, _proveedor, _eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta))
	conversacion = gestor.conversacion(clave)
	conversacion._emitir(TipoEvento.AVISO, "Antigravity no pudo usar la herramienta «hora» del servidor MCP «prueba».",
		reintentable=True, solicitud_id="denegada-2", regla="mcp(prueba/hora)")
	pendiente = gestor.solicitudes(clave)[0][1]
	assert [o.id for o in pendiente.opciones] == ["permitir_reintentar", "permitir_servidor_reintentar", "no_permitir"]
	assert "mcp(prueba/*)" in pendiente.opciones[1].ayuda
	assert pendiente.opcion_segura().id == "no_permitir"


def test_continuar_aqui_bifurca(entorno):
	gestor, proveedor, _eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta, titulo="Original"))
	gestor.enviar(clave, "hola")
	copia = gestor.continuar_aqui(clave)
	sesion = gestor.almacen.sesion(copia)
	assert sesion.bifurcada_de == "externo-1"
	assert sesion.titulo == "Original (continuación)"
	conversacion = gestor.conversacion(copia)
	assert conversacion.ajustes.bifurcar_de == "externo-1"


def test_organizar_y_borrar(entorno):
	gestor, proveedor, _eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta, titulo="Uno"))
	gestor.renombrar(clave, "Dos")
	gestor.marcar_favorita(clave, True)
	gestor.ocultar(clave, True)
	fila = gestor.listar()[0]
	assert (fila.titulo, fila.favorita, fila.oculta, fila.propia) == ("Dos", True, True, True)
	gestor.conversacion(clave)
	gestor.borrar(clave)
	assert gestor.listar() == []
	assert proveedor.conversaciones[0].cerrada
	with pytest.raises(ValueError):
		gestor.borrar("claude:abc")


def test_buscar_sin_acentos(entorno):
	gestor, _proveedor, _eventos, carpeta = entorno
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=carpeta, titulo="Búsqueda"))
	gestor.almacen.anadir_mensaje(clave, "asistente", "La canción está lista")
	resultados = gestor.buscar("cancion lista")
	assert resultados and resultados[0][0] == clave and resultados[0][1] == "Búsqueda"
	assert gestor.buscar("inexistente") == []
