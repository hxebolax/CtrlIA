"""Pruebas del cliente ACP (``app/services/agentes/acp.py``) con un agente falso."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from app.services.agentes import acp
from app.services.agentes.acp import ConversacionAcp, ProveedorAcp, diff_unificado, proveedores_personalizados
from app.services.agentes.base import (
	Adjunto,
	AjustesSesion,
	ClaseOpcion,
	Estado,
	Evento,
	RespuestaPermiso,
	TipoEvento,
)

FALSO = str(Path(__file__).resolve().parent / "fakes" / "fake_acp.py")


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

	def errores(self) -> list[str]:
		return [evento.texto for evento in self.de_tipo(TipoEvento.ERROR)]


def proveedor(escenario: str = "normal", **opciones) -> ProveedorAcp:
	return ProveedorAcp("prueba", "Agente de prueba", "Agente falso", [sys.executable, FALSO, escenario], **opciones)


@pytest.fixture
def conversacion(tmp_path):
	creadas: list[ConversacionAcp] = []

	def crear(escenario: str = "normal", **ajustes_extra) -> tuple[ConversacionAcp, Registro]:
		registro = Registro()
		ajustes = AjustesSesion(carpeta=tmp_path, **ajustes_extra)
		conv = proveedor(escenario).crear_conversacion(ajustes, registro)
		creadas.append(conv)
		return conv, registro

	yield crear
	for conv in creadas:
		conv.cerrar()


def test_inicio_y_sesion_nueva(conversacion):
	conv, registro = conversacion()
	conv.enviar("hola")
	iniciado = registro.esperar(TipoEvento.INICIADO)[0]
	fin = registro.esperar(TipoEvento.FIN_TURNO)[0]
	assert registro.errores() == []
	assert iniciado.datos["id_externo"] == "ses-1"
	assert iniciado.datos["modo"] == "default"
	assert conv.id_externo == "ses-1"
	assert fin.datos["correcto"] is True and fin.datos["interrumpido"] is False
	assert registro.de_tipo(TipoEvento.MENSAJE)[0].texto == "eco: hola"
	assert [modo.id for modo in conv.modos_disponibles()] == ["default", "plan"]
	assert [modelo.id for modelo in conv.modelos_disponibles()] == ["rapido", "listo"]
	assert [modo.id for modo in conv.proveedor.modos_permiso()] == ["default", "plan"]
	registro.esperar(TipoEvento.ESTADO, 2)
	_esperar(lambda: conv.estado is Estado.INACTIVA)


def test_turno_completo(conversacion):
	conv, registro = conversacion()
	conv.enviar("turno")
	registro.esperar(TipoEvento.FIN_TURNO)
	assert registro.errores() == []
	parciales = [evento.texto for evento in registro.de_tipo(TipoEvento.TEXTO_PARCIAL)]
	assert parciales == ["Hola, ", "voy a editar.", "Listo."]
	mensajes = [evento.texto for evento in registro.de_tipo(TipoEvento.MENSAJE)]
	# El texto anterior a la herramienta se publica antes de ella.
	assert mensajes == ["Hola, voy a editar.", "Listo."]
	assert registro.de_tipo(TipoEvento.RAZONAMIENTO)[0].texto == "pensando"
	herramienta = registro.de_tipo(TipoEvento.HERRAMIENTA)
	assert len(herramienta) == 1
	assert herramienta[0].datos["herramienta"] == "Editar"
	assert herramienta[0].datos["id"] == "t1"
	assert "/proyecto/config.txt (línea 3)" in herramienta[0].datos["detalle"]
	archivo = registro.de_tipo(TipoEvento.ARCHIVO)[0]
	assert archivo.datos["tipo"] == "modificado"
	assert "-viejo" in archivo.datos["diff"] and "+nuevo" in archivo.datos["diff"]
	fin_herramienta = registro.de_tipo(TipoEvento.HERRAMIENTA_FIN)
	assert len(fin_herramienta) == 1
	assert fin_herramienta[0].datos["salida"] == "Hecho" and fin_herramienta[0].datos["error"] is False
	plan = registro.de_tipo(TipoEvento.PLAN)[0]
	assert plan.datos["pasos"] == [("Leer", "completed"), ("Editar", "in_progress")]
	assert registro.de_tipo(TipoEvento.COMANDOS)[0].datos["comandos"] == [("web", "Busca en internet")]
	assert conv.comandos() == [("web", "Busca en internet")]
	uso = registro.de_tipo(TipoEvento.USO)[0]
	assert uso.datos["contexto_usado"] == 500 and uso.datos["contexto_total"] == 1000 and uso.datos["coste"] == 0.5
	# Orden: el archivo y el fin de la herramienta van antes del plan y del mensaje final.
	tipos = [evento.tipo for evento in registro.eventos]
	assert tipos.index(TipoEvento.HERRAMIENTA) < tipos.index(TipoEvento.ARCHIVO) < tipos.index(TipoEvento.HERRAMIENTA_FIN)
	assert tipos.index(TipoEvento.PLAN) < tipos.index(TipoEvento.FIN_TURNO)
	assert registro.de_tipo(TipoEvento.FIN_TURNO)[0].datos["correcto"] is True


@pytest.mark.parametrize(("opcion", "esperado"), [("allow", "elegido: allow"), ("reject", "elegido: reject")])
def test_permiso(conversacion, opcion, esperado):
	conv, registro = conversacion()
	conv.enviar("permiso")
	permiso = registro.esperar(TipoEvento.PERMISO)[0]
	solicitud = permiso.datos["solicitud"]
	_esperar(lambda: conv.estado is Estado.ESPERANDO_USUARIO)
	assert solicitud.herramienta == "Ejecutar orden"
	assert "Ejecutar ls" in solicitud.titulo
	clases = [(op.id, op.clase) for op in solicitud.opciones]
	assert clases == [("allow", ClaseOpcion.PERMITIR), ("always", ClaseOpcion.PERMITIR_SIEMPRE), ("reject", ClaseOpcion.DENEGAR)]
	etiquetas = [op.etiqueta for op in solicitud.opciones]
	assert etiquetas[0] == "Permitir una vez" and etiquetas[2] == "Denegar"
	assert etiquetas[1].startswith("Permitir siempre (") and "ls" in etiquetas[1]
	assert solicitud.opcion_segura().id == "reject"
	conv.responder(solicitud.id, RespuestaPermiso(opcion))
	registro.esperar(TipoEvento.FIN_TURNO)
	assert [evento.texto for evento in registro.de_tipo(TipoEvento.MENSAJE)] == [esperado]
	assert registro.de_tipo(TipoEvento.PERMISO_RETIRADO) == []
	assert registro.errores() == []


def test_cancelacion_con_permiso_pendiente(conversacion):
	conv, registro = conversacion()
	conv.enviar("permiso")
	solicitud = registro.esperar(TipoEvento.PERMISO)[0].datos["solicitud"]
	conv.detener()
	retirado = registro.esperar(TipoEvento.PERMISO_RETIRADO)[0]
	assert retirado.datos["id"] == solicitud.id
	fin = registro.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["interrumpido"] is True and fin.datos["correcto"] is False
	# El agente recibió «cancelled» como respuesta al permiso.
	assert [evento.texto for evento in registro.de_tipo(TipoEvento.MENSAJE)] == ["elegido: cancelled"]
	# Responder después ya no hace nada.
	conv.responder(solicitud.id, RespuestaPermiso("allow"))
	assert registro.errores() == []


def test_cancelacion_sin_permiso(conversacion):
	conv, registro = conversacion()
	conv.enviar("esperar_cancelacion")
	registro.esperar(TipoEvento.TEXTO_PARCIAL)
	conv.detener()
	fin = registro.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["interrumpido"] is True
	assert fin.datos["motivo"] == "cancelled"


def test_leer_archivo(conversacion, tmp_path):
	conv, registro = conversacion()
	archivo = tmp_path / "datos.txt"
	archivo.write_bytes(b"uno\ndos\ntres\n")
	conv.enviar("leer %s" % archivo)
	registro.esperar(TipoEvento.FIN_TURNO)
	conv.enviar("leer %s 2 1" % archivo)
	registro.esperar(TipoEvento.FIN_TURNO, 2)
	conv.enviar("leer %s" % (tmp_path / "no_existe.txt"))
	registro.esperar(TipoEvento.FIN_TURNO, 3)
	mensajes = [evento.texto for evento in registro.de_tipo(TipoEvento.MENSAJE)]
	assert mensajes == ["contenido: uno\ndos\ntres\n", "contenido: dos\n", "error -32002"]


def test_escribir_archivo_dentro_y_fuera(conversacion, tmp_path):
	carpeta = tmp_path / "proyecto"
	carpeta.mkdir()
	fuera = tmp_path / "fuera.txt"
	registro = Registro()
	conv = proveedor().crear_conversacion(AjustesSesion(carpeta=carpeta), registro)
	try:
		destino = carpeta / "sub" / "nuevo.txt"
		conv.enviar("escribir %s" % destino)
		registro.esperar(TipoEvento.FIN_TURNO)
		assert destino.read_bytes() == "línea 1\nlínea 2\n".encode()
		assert list(destino.parent.glob(".ctrlia-*")) == []
		conv.enviar("escribir %s" % fuera)
		registro.esperar(TipoEvento.FIN_TURNO, 2)
		conv.enviar("escribir %s" % (carpeta / ".." / "trampa.txt"))
		registro.esperar(TipoEvento.FIN_TURNO, 3)
	finally:
		conv.cerrar()
	mensajes = [evento.texto for evento in registro.de_tipo(TipoEvento.MENSAJE)]
	assert mensajes[0] == "escrito"
	assert mensajes[1].startswith("error -32602") and "carpeta de la conversación" in mensajes[1]
	assert mensajes[2].startswith("error -32602")
	assert not fuera.exists() and not (tmp_path / "trampa.txt").exists()


def test_terminal_no_admitido(conversacion):
	conv, registro = conversacion()
	conv.enviar("terminal")
	registro.esperar(TipoEvento.FIN_TURNO)
	assert registro.de_tipo(TipoEvento.MENSAJE)[0].texto == "error -32601"


def test_proceso_que_muere(conversacion):
	conv2, registro2 = conversacion()
	conv2.enviar("morir")
	error = registro2.esperar(TipoEvento.ERROR)[0]
	assert "fallo catastrófico" in error.texto
	assert "código 3" in error.texto
	fin = registro2.esperar(TipoEvento.FIN_TURNO)[0]
	assert fin.datos["correcto"] is False
	_esperar(lambda: conv2.estado is Estado.ERROR)
	assert len(registro2.errores()) == 1
	# El siguiente mensaje relanza el agente y retoma la sesión.
	conv2.enviar("otra vez")
	registro2.esperar(TipoEvento.FIN_TURNO, 2)
	assert registro2.de_tipo(TipoEvento.INICIADO)[-1].datos["retomada"] is True
	assert registro2.de_tipo(TipoEvento.MENSAJE)[-1].texto == "eco: otra vez"


def test_muerte_con_permiso_pendiente(conversacion):
	conv, registro = conversacion()
	conv.enviar("permiso")
	solicitud = registro.esperar(TipoEvento.PERMISO)[0].datos["solicitud"]
	conv._proceso.terminar()  # noqa: SLF001 - simula que el agente muere
	registro.esperar(TipoEvento.ERROR)
	retirado = registro.esperar(TipoEvento.PERMISO_RETIRADO)[0]
	assert retirado.datos["id"] == solicitud.id
	registro.esperar(TipoEvento.FIN_TURNO)
	_esperar(lambda: conv.estado is Estado.ERROR)


def test_cola_de_mensajes(conversacion):
	conv, registro = conversacion()
	conv.enviar("lento 1")
	conv.enviar("lento 2")
	conv.enviar("lento 3")
	avisos = registro.esperar(TipoEvento.AVISO, 2)
	assert "cola" in avisos[0].texto
	registro.esperar(TipoEvento.FIN_TURNO, 3)
	assert registro.errores() == []
	assert [evento.texto for evento in registro.de_tipo(TipoEvento.MENSAJE)] == [
		"respuesta lento 1",
		"respuesta lento 2",
		"respuesta lento 3",
	]
	_esperar(lambda: conv.estado is Estado.INACTIVA)


def test_detener_devuelve_la_cola(conversacion):
	conv, registro = conversacion()
	conv.enviar("esperar_cancelacion")
	conv.enviar("segundo")
	registro.esperar(TipoEvento.TEXTO_PARCIAL)
	conv.detener()
	registro.esperar(TipoEvento.FIN_TURNO)
	time.sleep(0.3)
	assert conv.pendientes() == ["segundo"]
	assert conv.pendientes() == []
	assert len(registro.de_tipo(TipoEvento.FIN_TURNO)) == 1


def test_cambiar_modo(conversacion):
	conv, registro = conversacion()
	conv.enviar("hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	conv.cambiar_ajustes(modo_permiso="plan")
	aviso = registro.esperar(TipoEvento.AVISO)[0]
	assert "Planificar" in aviso.texto
	assert conv.modo_actual == "plan" and conv.ajustes.modo_permiso == "plan"
	time.sleep(0.2)
	assert len(registro.de_tipo(TipoEvento.AVISO)) == 1
	conv.cambiar_ajustes(modelo="listo")
	registro.esperar(TipoEvento.AVISO, 2)
	assert conv.modelo_actual == "listo"


def test_modo_inicial(conversacion):
	conv, registro = conversacion(modo_permiso="plan")
	conv.enviar("hola")
	iniciado = registro.esperar(TipoEvento.INICIADO)[0]
	assert iniciado.datos["modo"] == "plan"
	registro.esperar(TipoEvento.FIN_TURNO)
	assert registro.errores() == []


def test_retomar_sesion(conversacion):
	conv, registro = conversacion(id_externo="ses-vieja")
	conv.enviar("hola")
	iniciado = registro.esperar(TipoEvento.INICIADO)[0]
	registro.esperar(TipoEvento.FIN_TURNO)
	assert iniciado.datos["retomada"] is True and iniciado.datos["id_externo"] == "ses-vieja"
	assert conv.historial() == [("usuario", "hola agente"), ("asistente", "hola humano")]
	# El reenvío del historial no se emite como mensajes nuevos.
	assert [evento.texto for evento in registro.de_tipo(TipoEvento.MENSAJE)] == ["eco: hola"]


def test_retomar_sin_soporte(conversacion):
	conv, registro = conversacion("sin_carga", id_externo="ses-vieja")
	conv.enviar("hola")
	iniciado = registro.esperar(TipoEvento.INICIADO)[0]
	registro.esperar(TipoEvento.FIN_TURNO)
	assert iniciado.datos["retomada"] is False and conv.id_externo == "ses-1"
	assert any("nueva" in evento.texto for evento in registro.de_tipo(TipoEvento.AVISO))


def test_autenticacion_con_un_metodo(conversacion):
	conv, registro = conversacion("auth")
	conv.enviar("hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	assert registro.errores() == []
	assert [metodo.id for metodo in conv.metodos_autenticacion()] == ["clave"]
	assert "Clave de API" in conv.proveedor.detectar().mensaje


def test_orden_alternativa_para_versiones_antiguas(tmp_path):
	prov = proveedor("viejo", sustituciones={"--acp": "--experimental-acp"})
	prov._orden = [sys.executable, FALSO, "viejo", "--acp"]  # noqa: SLF001
	registro = Registro()
	conv = prov.crear_conversacion(AjustesSesion(carpeta=tmp_path), registro)
	try:
		conv.enviar("hola")
		registro.esperar(TipoEvento.FIN_TURNO)
	finally:
		conv.cerrar()
	assert registro.errores() == []
	assert prov.resolver_orden()[-1] == "--experimental-acp"


def test_agente_inexistente(tmp_path):
	prov = ProveedorAcp("nada", "Nada", "", [str(tmp_path / "no_existe.exe")], instrucciones="Instálalo.")
	assert prov.detectar().disponible is False
	registro = Registro()
	conv = prov.crear_conversacion(AjustesSesion(carpeta=tmp_path), registro)
	conv.enviar("hola")
	error = registro.esperar(TipoEvento.ERROR)[0]
	assert "No se encuentra Nada" in error.texto
	registro.esperar(TipoEvento.FIN_TURNO)
	_esperar(lambda: conv.estado is Estado.ERROR)
	assert conv.pendientes() == ["hola"]
	conv.cerrar()


def test_adjuntos(conversacion, tmp_path):
	conv, registro = conversacion()
	imagen = tmp_path / "foto.png"
	imagen.write_bytes(b"\x89PNG\r\n\x1a\n")
	texto = tmp_path / "notas.txt"
	texto.write_text("x", encoding="utf-8")
	conv.enviar("eco_adjuntos", [Adjunto(imagen, "image/png"), Adjunto(texto)])
	registro.esperar(TipoEvento.FIN_TURNO)
	assert registro.de_tipo(TipoEvento.MENSAJE)[0].texto == "tipos: text,image,resource_link"


def test_cerrar_es_idempotente(conversacion):
	conv, registro = conversacion()
	conv.enviar("hola")
	registro.esperar(TipoEvento.FIN_TURNO)
	conv.cerrar()
	conv.cerrar()
	assert conv.estado is Estado.CERRADA
	conv.enviar("tarde")
	assert conv.pendientes() == []


def test_diff_unificado():
	diff = diff_unificado("a.txt", "uno\ndos", "uno\ntres")
	assert "--- a/a.txt" in diff and "+++ b/a.txt" in diff
	assert "-dos\n" in diff and "+tres\n" in diff
	nuevo = diff_unificado("b.txt", None, "hola\n")
	assert "--- /dev/null" in nuevo and "+hola" in nuevo


def test_proveedores_personalizados(tmp_path):
	lista = [
		{"id": "Mi Agente", "nombre": "Mi agente", "orden": [sys.executable, FALSO, "normal"], "carpeta": str(tmp_path)},
		{"id": "malo", "nombre": "Malo", "orden": "no es lista"},
		{"id": "vacio", "orden": []},
		{"nombre": "Sin id", "orden": [sys.executable, "x"]},
		{"id": "mi-agente", "nombre": "Repetido", "orden": [sys.executable]},
		"basura",
	]
	proveedores = proveedores_personalizados(lista)
	assert [p.id for p in proveedores] == ["mi-agente", "acp-sin-id"]
	assert proveedores[0].carpeta == tmp_path
	assert proveedores[0].detectar().disponible is True
	assert proveedores_personalizados([]) == []


def test_lanzadores_rechazados(tmp_path):
	lanzador = tmp_path / "agente.cmd"
	lanzador.write_text("@echo off", encoding="utf-8")
	assert acp.preparar_orden([str(lanzador), "--acp"]) is None


def test_proveedores_conocidos():
	for prov in acp.proveedores_acp():
		assert prov.id and prov.nombre and prov.descripcion and prov.instrucciones_instalacion()
		prov.detectar()  # no debe lanzar aunque no esté instalado
	gemini = acp.proveedor_gemini_cli()
	assert gemini.id == "gemini" and "junio de 2026" in gemini.descripcion
	assert "no es oficial" in acp.proveedor_antigravity_acp().descripcion.lower()


def _esperar(condicion, segundos: float = 10.0) -> None:
	limite = time.monotonic() + segundos
	while time.monotonic() < limite:
		if condicion():
			return
		time.sleep(0.02)
	raise AssertionError("La condición no se cumplió a tiempo")
