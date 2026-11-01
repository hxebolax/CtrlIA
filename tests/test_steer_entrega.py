"""«Añadir al turno»: qué le pasa al mensaje si el agente no lo confirma (AIChat #238, #239, #402, #410).

Tres finales posibles, y solo tres:

- **Recibido**: Codex acepta el ``turn/steer``; Claude Code devuelve el eco con el uuid del mensaje.
- **No enviado**: no salió de Ctrl+IA, o el agente lo rechazó de forma explícita (en Codex, el
  error -32600). Vuelve al cuadro de respuesta y sale de la lista: si no, estaría dos veces.
- **Sin confirmar**: cualquier otra cosa. Puede que el agente lo tenga, así que se queda en la
  lista marcado y NO vuelve al cuadro: nunca se presenta como no enviado ni se reenvía.

Nada se muestra ni habla.
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest

from app.core.configuracion import Configuracion
from app.services.agentes.base import AjustesSesion, Evento, TipoEvento
from app.services.agentes.claude import ProveedorClaude
from app.services.agentes.codex import ErrorCodex, ProveedorCodex
from app.services.almacen import Almacen
from app.ui import avisos
from tests.ayudas_wx import esperar_hasta, procesar_eventos
from tests.test_agente_codex import ESPERA, FALSO as FALSO_CODEX
from tests.test_agente_codex import Recolector as RecolectorCodex
from tests.test_agente_codex import esperar_registro, peticiones
from tests.test_novedades_143_claude import FALSO as FALSO_CLAUDE
from tests.test_novedades_143_claude import Recolector as RecolectorClaude
from tests.test_novedades_143_claude import _esperar_registro


@pytest.fixture
def anuncios(monkeypatch):
	dichos: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(texto))
	monkeypatch.setattr(avisos, "notificar", lambda texto, *a, **k: dichos.append(texto))
	monkeypatch.setattr(avisos, "notificacion_sistema", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	return dichos


@pytest.fixture
def ventana(app_wx, tmp_path, monkeypatch, anuncios):
	from app.ui.ventana_principal import VentanaPrincipal

	monkeypatch.setenv("FAKE_CLAUDE_MODO", "normal")
	config = Configuracion(tmp_path / "settings.json")
	config.establecer_varias({
		"historial.claude": False, "historial.codex": False, "historial.antigravity": False,
		"general.confirmar_salida": False, "anuncios.sonidos": False, "general.abrir_ultima_sesion": False,
	})
	almacen = Almacen(tmp_path / "prueba.db")
	proveedores = {"claude": ProveedorClaude(orden_base=[sys.executable, str(FALSO_CLAUDE)])}
	marco = VentanaPrincipal(config, None, almacen=almacen, proveedores=proveedores)
	monkeypatch.setattr(type(marco), "en_segundo_plano",
		lambda self, funcion, *args, al_terminar=None, al_fallar=None, clave=None: al_terminar(funcion(*args)) if al_terminar else funcion(*args))
	procesar_eventos()
	yield marco
	if marco:
		marco.salir()
		procesar_eventos()


def _sesion(ventana, tmp_path, titulo="Prueba") -> str:
	clave = ventana.gestor.nueva_sesion("claude", AjustesSesion(carpeta=tmp_path, titulo=titulo))

	def esta() -> bool:
		if ventana.fila_de(clave) is None:
			ventana.refrescar_sesiones(True)
			return False
		return True

	esperar_hasta(esta, 30, intervalo=0.05)
	return clave


def _agente_ocupado(ventana, monkeypatch, anadir=None):
	"""Una conversación de mentira, trabajando, que recoge lo que se le añade."""
	entregados: list[str] = []
	conversacion = SimpleNamespace(anadir_al_turno=anadir or entregados.append, anadidos_pendientes=0, _cola=[])
	proveedor = SimpleNamespace(capacidades=SimpleNamespace(anadir_al_turno=True))
	monkeypatch.setattr(ventana.gestor, "proveedor", lambda _id: proveedor)
	monkeypatch.setattr(ventana.gestor, "conversacion", lambda c, crear=True: conversacion)
	monkeypatch.setattr(ventana.gestor, "ocupada", lambda c: True)
	monkeypatch.setattr(ventana.gestor, "solicitudes", lambda c=None: [])
	return entregados


def _llega(ventana, clave: str, texto: str, **datos) -> None:
	"""Un evento hace lo mismo que en la realidad: primero lo guarda el gestor y luego lo ve la interfaz."""
	evento = Evento(TipoEvento.AVISO, texto, datos)
	ventana.gestor._guardar(clave, evento)  # noqa: SLF001
	ventana._al_evento(clave, evento)  # noqa: SLF001


def _usuarios(ventana, clave: str) -> list:
	return [m for m in ventana.almacen.mensajes(clave) if m.rol == "usuario"]


# ── 1. Rechazado: vuelve al cuadro y no se queda además en la lista ─────────────


def test_rechazado_no_se_queda_duplicado_en_la_lista(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	_agente_ocupado(ventana, monkeypatch)
	ventana.respuesta.SetValue("y mira el README")
	ventana.accion_anadir_al_turno()
	assert ventana._mensajes[-1].texto == "y mira el README"  # noqa: SLF001

	_llega(ventana, clave, "El turno ya había terminado.", devolver_texto="y mira el README")

	assert ventana.respuesta.GetValue() == "y mira el README"
	assert _usuarios(ventana, clave) == []
	assert not [m for m in ventana._mensajes if m.rol == "usuario"]  # noqa: SLF001
	assert ventana.lista_mensajes.GetItemCount() == len(ventana._mensajes)  # noqa: SLF001
	# Lo que pasó se sigue pudiendo leer: el aviso del agente queda en la conversación.
	assert "El turno ya había terminado." in [m.texto for m in ventana.almacen.mensajes(clave)]


def test_si_falla_al_entregarlo_no_queda_guardado(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)

	def falla(_texto: str) -> None:
		raise RuntimeError("no hay hilos libres")

	_agente_ocupado(ventana, monkeypatch, anadir=falla)
	errores: list[str] = []
	monkeypatch.setattr("app.ui.vp_conversacion.mostrar_error", lambda _padre, texto, *a, **k: errores.append(texto))
	ventana.respuesta.SetValue("corrige esto")
	ventana.accion_anadir_al_turno()
	assert errores and "no hay hilos libres" in errores[0]
	assert ventana.respuesta.GetValue() == "corrige esto"
	assert _usuarios(ventana, clave) == []
	assert not [m for m in ventana._mensajes if m.rol == "usuario"]  # noqa: SLF001


def test_de_otra_sesion_no_se_pierde_hasta_llegar_al_cuadro(ventana, tmp_path, monkeypatch, anuncios):
	"""Mientras no se abre la sesión, el texto solo está en memoria: el mensaje guardado no se borra todavía."""
	otra = _sesion(ventana, tmp_path, "Otra")
	abierta = _sesion(ventana, tmp_path, "Abierta")
	ventana.abrir_sesion(otra, mover_foco=False)
	_agente_ocupado(ventana, monkeypatch)
	ventana.respuesta.SetValue("corrige esto")
	ventana.accion_anadir_al_turno()
	ventana.abrir_sesion(abierta, mover_foco=False)

	_llega(ventana, otra, "Codex no lo ha aceptado.", devolver_texto="corrige esto")

	guardado = _usuarios(ventana, otra)
	assert [m.texto for m in guardado] == ["corrige esto"] and guardado[0].datos.get("no_entregado") is True
	assert ventana.respuesta.GetValue() == ""
	ventana.abrir_sesion(otra, mover_foco=False)
	assert ventana.respuesta.GetValue() == "corrige esto"
	assert _usuarios(ventana, otra) == []


def test_con_una_peticion_apuntada_el_mensaje_no_se_borra(ventana, tmp_path, monkeypatch, anuncios):
	"""El mensaje del que cuelga una petición de permiso se queda, marcado: no se pierde el rastro de la petición."""
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	_agente_ocupado(ventana, monkeypatch)
	ventana.respuesta.SetValue("y esto")
	ventana.accion_anadir_al_turno()
	guardado = _usuarios(ventana, clave)[0]
	ventana.almacen.actualizar_mensaje(guardado.id, datos=dict(guardado.datos, solicitudes=[{"id": "1", "tipo": "herramienta"}]))

	_llega(ventana, clave, "No se ha aceptado.", devolver_texto="y esto")

	assert ventana.respuesta.GetValue() == "y esto"
	queda = _usuarios(ventana, clave)
	assert len(queda) == 1 and queda[0].datos.get("no_entregado") is True and queda[0].datos.get("solicitudes")


def test_borrar_mensaje_quita_tambien_su_favorito(tmp_path):
	from app.services.almacen import SesionPropia

	almacen = Almacen(tmp_path / "prueba.db")
	almacen.guardar_sesion(SesionPropia("ctrlia:x", "claude"))
	identificador = almacen.anadir_mensaje("ctrlia:x", "usuario", "hola")
	almacen.marcar_favorito("ctrlia:x", str(identificador), "usuario", 1.0, "hola")
	assert almacen.borrar_mensaje(identificador) is True
	assert almacen.mensaje(identificador) is None
	assert almacen.favoritos() == []
	assert almacen.borrar_mensaje(identificador) is False


# ── 2. Sin confirmar: sigue en la lista, marcado, y nunca vuelve solo ───────────


def test_sin_confirmar_se_queda_marcado_y_no_vuelve_al_cuadro(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	entregados = _agente_ocupado(ventana, monkeypatch)
	ventana.respuesta.SetValue("usa la otra rama")
	ventana.accion_anadir_al_turno()

	_llega(ventana, clave, "Codex no ha confirmado si lo recibió.", sin_confirmar="usa la otra rama")

	assert ventana.respuesta.GetValue() == ""
	guardado = _usuarios(ventana, clave)
	assert len(guardado) == 1 and guardado[0].datos.get("sin_confirmar") is True
	assert anuncios[-1] == "Codex no ha confirmado si lo recibió."
	assert entregados == ["usa la otra rama"]  # nunca se reenvía solo
	from app.ui.lectura_listas import indicadores_mensaje

	vista = next(m for m in ventana._mensajes if m.rol == "usuario")  # noqa: SLF001
	assert vista.datos.get("sin_confirmar") is True
	assert "Sin confirmar" in indicadores_mensaje(vista.texto, vista.rol, 0, lambda c, d=None: d, vista.datos)


def test_sin_confirmar_de_otra_sesion_se_avisa(ventana, tmp_path, monkeypatch, anuncios):
	otra = _sesion(ventana, tmp_path, "Otra")
	abierta = _sesion(ventana, tmp_path, "Abierta")
	ventana.abrir_sesion(otra, mover_foco=False)
	_agente_ocupado(ventana, monkeypatch)
	ventana.respuesta.SetValue("usa la otra rama")
	ventana.accion_anadir_al_turno()
	ventana.abrir_sesion(abierta, mover_foco=False)
	anuncios.clear()

	_llega(ventana, otra, "Claude Code se ha cerrado sin confirmarlo.", sin_confirmar="usa la otra rama")

	assert any("Otra" in texto and "sin confirmarlo" in texto for texto in anuncios)
	assert ventana.respuesta.GetValue() == ""
	assert _usuarios(ventana, otra)[0].datos.get("sin_confirmar") is True


def test_confirmado_no_deja_nada_pendiente_en_el_gestor(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	_agente_ocupado(ventana, monkeypatch)
	ventana.respuesta.SetValue("otro más")
	ventana.accion_anadir_al_turno()
	_llega(ventana, clave, "Claude ha recogido tu mensaje.", anadido=True, texto_anadido="otro más")
	assert ventana.gestor._anadidos.get(clave, []) == []  # noqa: SLF001
	assert _usuarios(ventana, clave)[0].datos == {"anadido_al_turno": True}


def test_dos_mensajes_iguales_se_resuelven_por_orden(ventana, tmp_path, monkeypatch, anuncios):
	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	_agente_ocupado(ventana, monkeypatch)
	for _vez in range(2):
		ventana.respuesta.SetValue("repite")
		ventana.accion_anadir_al_turno()
	primero, segundo = _usuarios(ventana, clave)
	_llega(ventana, clave, "Sin confirmar.", sin_confirmar="repite")
	_llega(ventana, clave, "Rechazado.", devolver_texto="repite")
	queda = _usuarios(ventana, clave)
	assert [m.id for m in queda] == [primero.id] and queda[0].datos.get("sin_confirmar") is True
	assert ventana.almacen.mensaje(segundo.id) is None
	assert ventana.respuesta.GetValue() == "repite"


# ── 2. Codex: solo -32600 es «no enviado» ───────────────────────────────────────


@pytest.fixture
def codex(tmp_path):
	creados: list = []

	def crear(escenario: str):
		registro = tmp_path / ("registro-%s.jsonl" % escenario)
		proveedor = ProveedorCodex(orden=[sys.executable, str(FALSO_CODEX), escenario, str(registro)])
		eventos = RecolectorCodex()
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos)
		creados.append((proveedor, conversacion))
		return conversacion, eventos, registro

	yield crear
	for proveedor, conversacion in creados:
		conversacion.cerrar()
		proveedor.cerrar()


def _avisos_con(eventos, clave: str, tiempo: float = ESPERA) -> list:
	limite = time.monotonic() + tiempo
	encontrados: list = []
	while not encontrados and time.monotonic() < limite:
		time.sleep(0.02)
		encontrados = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get(clave)]
	return encontrados


def test_codex_muerto_tras_recibirlo_queda_sin_confirmar(codex):
	"""Antes decía «no ha recibido tu mensaje» y lo devolvía al cuadro, sin saberlo."""
	conversacion, eventos, registro = codex("steer_muere")
	conversacion.enviar("uno")
	esperar_registro(registro, "turn/start")
	time.sleep(0.2)
	conversacion.anadir_al_turno("cambia de plan")
	dudas = _avisos_con(eventos, "sin_confirmar")
	assert dudas and dudas[0].datos["sin_confirmar"] == "cambia de plan"
	assert "no ha recibido" not in dudas[0].texto
	assert "confirm" in dudas[0].texto
	assert not [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("devolver_texto")]
	assert conversacion.pendientes() == []
	assert conversacion.anadidos_pendientes == 0


def test_codex_otro_error_tampoco_es_un_rechazo(codex):
	conversacion, eventos, registro = codex("steer_interno")
	conversacion.enviar("uno")
	esperar_registro(registro, "turn/start")
	time.sleep(0.2)
	conversacion.anadir_al_turno("cambia de plan")
	dudas = _avisos_con(eventos, "sin_confirmar")
	assert dudas and dudas[0].datos["sin_confirmar"] == "cambia de plan"
	assert not [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("devolver_texto")]
	assert len(peticiones(registro, "turn/start")) == 1
	conversacion.detener()
	eventos.esperar(TipoEvento.FIN_TURNO)


def test_codex_sin_respuesta_a_tiempo_no_vuelve_al_cuadro(tmp_path):
	proveedor = ProveedorCodex(orden=["x"])
	eventos = RecolectorCodex()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos)

	def sin_respuesta(*_a, **_k):
		raise ErrorCodex("Codex no ha respondido a tiempo", tiempo_agotado=True)

	conversacion._srv = SimpleNamespace(peticion=sin_respuesta)  # noqa: SLF001
	conversacion._hilo_id = "h"  # noqa: SLF001
	conversacion._anadir_al_turno("hola", "t1")  # noqa: SLF001
	avisos_ = eventos.de_tipo(TipoEvento.AVISO)
	assert [e.datos.get("sin_confirmar") for e in avisos_] == ["hola"]
	assert not avisos_[0].datos.get("devolver_texto")


def test_codex_sin_llegar_a_escribirlo_si_es_no_enviado(tmp_path):
	proveedor = ProveedorCodex(orden=["x"])
	eventos = RecolectorCodex()
	conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos)

	def apagado(*_a, **_k):
		raise ErrorCodex("Codex no está en marcha", muerto=True, sin_enviar=True)

	conversacion._srv = SimpleNamespace(peticion=apagado)  # noqa: SLF001
	conversacion._hilo_id = "h"  # noqa: SLF001
	conversacion._anadir_al_turno("hola", "t1")  # noqa: SLF001
	assert [e.datos.get("devolver_texto") for e in eventos.de_tipo(TipoEvento.AVISO)] == ["hola"]


def test_codex_confirma_con_el_texto_del_mensaje(codex):
	conversacion, eventos, registro = codex("interrupcion")
	conversacion.enviar("uno")
	esperar_registro(registro, "turn/start")
	time.sleep(0.2)
	conversacion.anadir_al_turno("y esto también")
	aceptados = _avisos_con(eventos, "anadido")
	assert aceptados and aceptados[0].datos["texto_anadido"] == "y esto también"
	conversacion.detener()
	eventos.esperar(TipoEvento.FIN_TURNO)


# ── 2. Claude Code: el eco con el uuid (--replay-user-messages) ─────────────────


@pytest.fixture
def claude(tmp_path, monkeypatch):
	registro = tmp_path / "registro.jsonl"
	monkeypatch.setenv("FAKE_CLAUDE_REGISTRO", str(registro))
	creadas = []

	def crear(modo: str, **opciones):
		monkeypatch.setenv("FAKE_CLAUDE_MODO", modo)
		eventos = RecolectorClaude()
		proveedor = ProveedorClaude(orden_base=[sys.executable, str(FALSO_CLAUDE)], **opciones)
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos)
		creadas.append(conversacion)
		return conversacion, eventos

	yield crear, registro
	for conversacion in creadas:
		conversacion.cerrar()


def _avisos_claude(eventos, clave: str) -> list:
	return [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get(clave)]


def test_claude_pide_el_eco_de_cada_mensaje(tmp_path):
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda e: None)
	assert "--replay-user-messages" in conversacion.construir_orden()


def test_claude_avisa_al_recoger_el_mensaje(claude):
	crear, registro = claude
	conversacion, eventos = crear("anadir")
	conversacion.enviar("haz algo largo")
	assert _esperar_registro(registro, "esperando_anadido")
	conversacion.anadir_al_turno("y mira también el README")
	assert conversacion.anadidos_pendientes in (0, 1)  # el eco llega desde el hilo lector
	assert eventos.esperar(lambda: bool(_avisos_claude(eventos, "anadido")))
	aviso = _avisos_claude(eventos, "anadido")[0]
	assert aviso.datos["texto_anadido"] == "y mira también el README"
	assert "recogido" in aviso.texto
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	assert conversacion.anadidos_pendientes == 0
	assert len(eventos.de_tipo(TipoEvento.FIN_TURNO)) == 1


def test_claude_sin_recoger_cuenta_como_pendiente(claude):
	crear, registro = claude
	conversacion, _eventos = crear("anadir_sin_eco")
	conversacion.enviar("haz algo largo")
	assert _esperar_registro(registro, "esperando_anadido")
	conversacion.anadir_al_turno("y esto")
	assert conversacion.anadidos_pendientes == 1


def test_claude_el_eco_de_otro_mensaje_no_confirma_nada(claude):
	crear, registro = claude
	conversacion, eventos = crear("anadir_sin_eco")
	conversacion.enviar("haz algo largo")
	assert _esperar_registro(registro, "esperando_anadido")
	conversacion.anadir_al_turno("y esto")
	vivo = conversacion._vivo  # noqa: SLF001
	conversacion._al_linea_de(vivo, '{"type":"user","isReplay":true,"uuid":"otro","message":{"content":"x"}}')  # noqa: SLF001
	assert conversacion.anadidos_pendientes == 1
	assert not _avisos_claude(eventos, "anadido")


def test_claude_mensaje_recogido_tras_el_resultado_lo_dice_y_cierra_una_vez(claude):
	crear, registro = claude
	conversacion, eventos = crear("anadir_tarde")
	conversacion.enviar("responde")
	assert _esperar_registro(registro, "esperando_anadido")
	assert eventos.esperar(lambda: any(e.texto.startswith("Hola") for e in eventos.de_tipo(TipoEvento.MENSAJE)))
	conversacion.anadir_al_turno("y otra cosa")
	assert eventos.esperar(lambda: bool(_avisos_claude(eventos, "anadido")))
	assert "ya había terminado" in _avisos_claude(eventos, "anadido")[0].texto
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	assert eventos.de_tipo(TipoEvento.MENSAJE)[-1].texto == "tarde:y otra cosa"
	assert len(eventos.de_tipo(TipoEvento.FIN_TURNO)) == 1


def test_claude_si_muere_sin_recogerlo_queda_sin_confirmar(claude):
	crear, registro = claude
	conversacion, eventos = crear("anadir_muere")
	conversacion.enviar("haz algo largo")
	assert _esperar_registro(registro, "esperando_anadido")
	conversacion.anadir_al_turno("y mira también el README")
	assert eventos.esperar(lambda: bool(_avisos_claude(eventos, "sin_confirmar")))
	aviso = _avisos_claude(eventos, "sin_confirmar")[0]
	assert aviso.datos["sin_confirmar"] == "y mira también el README"
	assert not _avisos_claude(eventos, "devolver_texto")
	assert conversacion.anadidos_pendientes == 0


def test_claude_si_falla_la_escritura_queda_sin_confirmar(tmp_path):
	"""Con el turno listo, un fallo al escribir no demuestra que no llegara: nunca se dice «no enviado»."""
	from app.services.agentes.claude import _ProcesoClaude, _Turno

	eventos = RecolectorClaude()
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(AjustesSesion(carpeta=tmp_path), eventos)
	vivo = _ProcesoClaude()
	vivo.proceso = SimpleNamespace(vivo=True, escribir_json=lambda _d: False)
	turno = _Turno(vivo)
	turno.mensaje_escrito = True
	conversacion._turno = turno  # noqa: SLF001
	conversacion._vivo = vivo  # noqa: SLF001
	conversacion.anadir_al_turno("hola")
	assert [e.datos.get("sin_confirmar") for e in _avisos_claude(eventos, "sin_confirmar")] == ["hola"]
	assert not _avisos_claude(eventos, "devolver_texto")


def test_claude_sin_turno_sigue_siendo_no_enviado(claude):
	crear, _registro = claude
	conversacion, eventos = crear("normal")
	conversacion.anadir_al_turno("no hay turno")
	assert _avisos_claude(eventos, "devolver_texto")
	assert not _avisos_claude(eventos, "sin_confirmar")


def test_claude_el_eco_no_abre_un_turno_ni_molesta_entre_mensajes(claude):
	crear, _registro = claude
	conversacion, eventos = crear("normal", minutos_abierto=5)
	conversacion.enviar("hola")
	assert eventos.esperar(lambda: bool(eventos.de_tipo(TipoEvento.FIN_TURNO)))
	vivo = conversacion._vivo  # noqa: SLF001
	assert vivo is not None
	turnos = len(eventos.de_tipo(TipoEvento.FIN_TURNO))
	conversacion._al_linea_de(vivo, '{"type":"user","isReplay":true,"uuid":"u","message":{"content":"x"}}')  # noqa: SLF001
	assert conversacion._turno.terminado is True  # noqa: SLF001
	assert len(eventos.de_tipo(TipoEvento.FIN_TURNO)) == turnos


def test_claude_al_cerrar_no_avisa_de_nada(claude):
	"""Cerrar la conversación (o el programa) no es una duda que anunciar: solo queda en el registro."""
	crear, registro = claude
	conversacion, eventos = crear("anadir_sin_eco")
	conversacion.enviar("haz algo largo")
	assert _esperar_registro(registro, "esperando_anadido")
	conversacion.anadir_al_turno("y esto")
	conversacion.cerrar()
	time.sleep(0.3)  # el aviso, si lo hubiera, llega desde el hilo que espera al proceso
	assert not _avisos_claude(eventos, "sin_confirmar")


def test_la_ayuda_del_menu_dice_las_dos_salidas():
	from app.core import paths

	fuente = (paths.ruta_base() / "app" / "ui" / "ventana_principal.py").read_text(encoding="utf-8")
	linea = next(ln for ln in fuente.splitlines() if '"anadir_turno"' in ln)
	assert "cuadro de respuesta" in linea and "Sin confirmar" in linea
