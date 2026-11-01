"""Codex: lo que se relee con ``thread/read``, la revisión automática de aprobaciones, sus reglas y su memoria.

- Un cambio de archivos rechazado o fallido no cuenta como hecho al releer el hilo.
- «Revisión automática» (AIChat #358, #374): dos modos más; quien aprueba es Codex porque la
  persona lo eligió para esa sesión. Las notificaciones ``item/autoApprovalReview/*`` son solo
  para mostrar: nunca responden nada por su cuenta.
- Reglas guardadas (AIChat #343): los ``.rules`` se analizan con ``ast`` y nunca se ejecutan.
- Lo que Codex sabe de ti (AIChat #371): la carpeta ``memories``.

Nada se muestra ni habla.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from app.services import memoria
from app.services.agentes import codex
from app.services.agentes.base import AjustesSesion, Evento, RespuestaPermiso, TipoEvento
from app.services.agentes.codex import ProveedorCodex, item_a_mensaje
from app.services.config_agentes import modo_inicial, reglas_codex
from app.services.gestor import MensajeVista
from tests.test_agente_codex import FALSO, Recolector, peticiones

# ── 4. fileChange al releer ─────────────────────────────────────────────────────


@pytest.mark.parametrize("estado", ["declined", "failed"])
def test_cambio_rechazado_no_cuenta_como_hecho(estado):
	from app.services.extraccion import archivos_cambiados

	item = {"type": "fileChange", "id": "f", "status": estado,
		"changes": [{"path": "a.txt", "kind": {"type": "update"}, "diff": "-a\n+b"}]}
	mensaje = item_a_mensaje(item, 10.0)
	assert mensaje is not None and mensaje["rol"] == "sistema"
	assert mensaje["texto"].startswith("No se han aplicado") or "no ha podido aplicar" in mensaje["texto"]
	assert mensaje["detalle"] == "a.txt"
	vista = MensajeVista(mensaje["rol"], mensaje["texto"], 10.0, mensaje.get("detalle", ""))
	assert archivos_cambiados([vista]) == []


def test_cambio_hecho_sigue_contando():
	item = {"type": "fileChange", "id": "f", "status": "completed",
		"changes": [{"path": "a.txt", "kind": {"type": "add"}, "diff": "+b"}]}
	assert item_a_mensaje(item, 10.0)["rol"] == "cambio"
	# Lo guardado antes de que Codex dijera el estado sigue valiendo.
	del item["status"]
	assert item_a_mensaje(item, 10.0)["rol"] == "cambio"


def test_cambio_movido_se_relee_con_su_destino():
	from app.services.extraccion import archivos_cambiados

	item = {"type": "fileChange", "id": "f", "status": "completed",
		"changes": [{"path": "viejo.py", "kind": {"type": "update", "move_path": "nuevo.py"}, "diff": "-a\n+b"}]}
	mensaje = item_a_mensaje(item, 10.0)
	vista = MensajeVista(mensaje["rol"], mensaje["texto"], 10.0, mensaje["detalle"])
	cambio = archivos_cambiados([vista])[0]
	assert (cambio.ruta, cambio.movido_desde) == ("nuevo.py", "viejo.py")


# ── 5. Revisión automática ──────────────────────────────────────────────────────


@pytest.fixture
def entorno(tmp_path):
	creados: list = []

	def crear(escenario: str = "normal", **ajustes):
		registro = tmp_path / ("registro-%s-%d.jsonl" % (escenario, len(creados)))
		proveedor = ProveedorCodex(orden=[sys.executable, str(FALSO), escenario, str(registro)])
		eventos = Recolector()
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path, **ajustes), eventos)
		creados.append((proveedor, conversacion))
		return proveedor, conversacion, eventos, registro

	yield crear
	for proveedor, conversacion in creados:
		conversacion.cerrar()
		proveedor.cerrar()


def test_dos_modos_mas_y_nunca_de_fabrica():
	proveedor = ProveedorCodex()
	ids = [m.id for m in proveedor.modos_permiso()]
	assert ids == ["solo_lectura", "espacio_trabajo", "sin_preguntas", "acceso_total", "solo_lectura_auto", "espacio_trabajo_auto"]
	assert codex._modo("espacio_trabajo").revisor == "user"  # noqa: SLF001
	assert codex._modo("").revisor == "user"  # noqa: SLF001
	for identificador, sandbox in (("solo_lectura_auto", "read-only"), ("espacio_trabajo_auto", "workspace-write")):
		modo = codex._modo(identificador)  # noqa: SLF001
		assert modo.revisor == "auto_review" and modo.aprobacion == "on-request" and modo.sandbox == sandbox
	ayudas = [m.ayuda for m in proveedor.modos_permiso() if m.id.endswith("_auto")]
	assert all("Codex" in ayuda and "decide" in ayuda for ayuda in ayudas)


@pytest.mark.parametrize("modo,revisor", [
	("espacio_trabajo_auto", "auto_review"), ("solo_lectura_auto", "auto_review"), ("espacio_trabajo", "user"),
])
def test_el_hilo_lleva_el_revisor_del_modo(entorno, modo, revisor):
	_proveedor, conversacion, eventos, registro = entorno("normal", modo_permiso=modo)
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	inicio = peticiones(registro, "thread/start")[0]["params"]
	assert inicio["approvalsReviewer"] == revisor and inicio["approvalPolicy"] == "on-request"
	assert peticiones(registro, "turn/start")[0]["params"]["approvalsReviewer"] == revisor


def test_cambiar_de_modo_llega_en_el_turno_siguiente(entorno):
	_proveedor, conversacion, eventos, registro = entorno("normal", modo_permiso="espacio_trabajo")
	conversacion.enviar("uno")
	eventos.esperar(TipoEvento.FIN_TURNO)
	conversacion.cambiar_ajustes(modo_permiso="espacio_trabajo_auto")
	conversacion.enviar("dos")
	eventos.esperar(TipoEvento.FIN_TURNO, 2)
	segundo = peticiones(registro, "turn/start")[1]["params"]
	assert segundo["approvalsReviewer"] == "auto_review" and segundo["approvalPolicy"] == "on-request"


def test_si_codex_no_acepta_la_revision_automatica_se_dice(entorno):
	_proveedor, conversacion, eventos, registro = entorno("revisor_ignorado", modo_permiso="espacio_trabajo_auto")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	avisos = [e.texto for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("revisor_efectivo")]
	assert avisos and "te preguntará" in avisos[0]


def test_con_revisor_humano_no_se_acepta_que_siga_el_automatico(entorno):
	"""Un hilo ya cargado ignora lo pedido al retomarlo: el siguiente turno lo fija y no se calla."""
	_proveedor, conversacion, eventos, registro = entorno("revisor_pegado", modo_permiso="espacio_trabajo", id_externo="hilo-9")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	assert peticiones(registro, "turn/start")[0]["params"]["approvalsReviewer"] == "user"
	assert any("revisión automática" in e.texto for e in eventos.de_tipo(TipoEvento.AVISO))


def test_las_decisiones_se_anuncian_y_nunca_responden_nada(entorno):
	_proveedor, conversacion, eventos, registro = entorno("revision", modo_permiso="espacio_trabajo_auto")
	conversacion.enviar("hola")
	eventos.esperar(TipoEvento.FIN_TURNO)
	en_curso = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("solo_estado")]
	assert en_curso and "dir" in en_curso[0].texto
	decisiones = [e for e in eventos.de_tipo(TipoEvento.AVISO) if e.datos.get("revision_automatica")]
	assert [e.datos["revision_automatica"]["estado"] for e in decisiones] == ["approved", "denied", "timedOut", "aborted"]
	permitida = decisiones[0]
	assert permitida.texto.startswith("La revisión automática de Codex ha permitido")
	assert "Orden: dir" in permitida.texto and "Riesgo: bajo" in permitida.texto and "Parece inofensiva" in permitida.texto
	datos = permitida.datos["revision_automatica"]
	assert datos["accion"] == "Orden: dir" and datos["motivo"] == "Parece inofensiva" and datos["riesgo"] == "bajo"
	assert "ha denegado" in decisiones[1].texto and "red: ejemplo.com" in decisiones[1].texto
	assert "no se ha aprobado nada" in decisiones[2].texto
	assert "no se ha aprobado nada" in decisiones[3].texto
	# Nada de esto es una petición: Ctrl+IA no contesta nada al servidor.
	assert not eventos.de_tipo(TipoEvento.PERMISO)
	respuestas = [m for m in _leer(registro) if "result" in m or "error" in m]
	assert respuestas == []


def _leer(registro: Path) -> list[dict]:
	from tests.test_agente_codex import leer_registro

	return leer_registro(registro)


@pytest.mark.parametrize("accion,esperado", [
	({"type": "command", "command": "git push"}, "Orden: git push"),
	({"type": "execve", "program": "git", "argv": ["git", "push"]}, "Orden: git push"),
	({"type": "writeStdin", "processId": "1", "stdin": "s\n"}, "Escribir en una orden en marcha: s"),
	({"type": "applyPatch", "files": ["a.py", "b.py"]}, "Cambiar archivos: a.py, b.py"),
	({"type": "networkAccess", "host": "ejemplo.com", "port": 443, "target": "x"}, "Acceso a la red: ejemplo.com:443"),
	({"type": "mcpToolCall", "server": "gh", "toolName": "issues", "toolTitle": "Incidencias"}, "Herramienta Incidencias del servidor gh"),
	({"type": "requestPermissions", "reason": "Necesita la red", "permissions": {}}, "Pedir permisos: Necesita la red"),
	({"type": "futuro"}, "Acción de Codex (futuro)"),
	("rara", "Una acción de Codex"),
])
def test_descripcion_de_cada_accion(accion, esperado):
	assert codex._describir_accion_revision(accion) == esperado  # noqa: SLF001


def test_una_notificacion_mal_formada_se_ignora():
	eventos = Recolector()
	conversacion = ProveedorCodex(orden=["x"]).crear_conversacion(AjustesSesion(carpeta=Path(".")), eventos)
	conversacion._al_notificacion("item/autoApprovalReview/completed", {"threadId": "h"})  # noqa: SLF001
	conversacion._al_notificacion("item/autoApprovalReview/completed", {"review": {"status": "inventado"}})  # noqa: SLF001
	conversacion._al_notificacion("item/autoApprovalReview/completed", {"review": "x"})  # noqa: SLF001
	assert not eventos.de_tipo(TipoEvento.AVISO)


def test_aviso_de_la_revision_y_revision_estricta():
	eventos = Recolector()
	conversacion = ProveedorCodex(orden=["x"]).crear_conversacion(AjustesSesion(carpeta=Path(".")), eventos)
	conversacion._al_notificacion("guardianWarning", {"threadId": "h", "message": "Cuidado"})  # noqa: SLF001
	assert "Cuidado" in eventos.de_tipo(TipoEvento.AVISO)[0].texto


def test_el_gestor_guarda_la_decision_con_su_aviso_y_el_en_curso_no(tmp_path):
	from app.services.almacen import Almacen, SesionPropia
	from app.services.gestor import GestorSesiones

	almacen = Almacen(tmp_path / "prueba.db")
	almacen.guardar_sesion(SesionPropia("ctrlia:x", "codex"))
	gestor = GestorSesiones(almacen, {}, lambda c, e: None)
	gestor._guardar("ctrlia:x", Evento(TipoEvento.AVISO, "Revisando", {"solo_estado": True}))  # noqa: SLF001
	assert almacen.mensajes("ctrlia:x") == []
	gestor._guardar("ctrlia:x", Evento(TipoEvento.AVISO, "Permitido", {"revision_automatica": {"estado": "approved"}}))  # noqa: SLF001
	gestor._guardar("ctrlia:x", Evento(TipoEvento.AVISO, "Otro aviso", {}))  # noqa: SLF001
	assert [m.texto for m in gestor.revisiones_automaticas("ctrlia:x")] == ["Permitido"]
	assert gestor.revisiones_automaticas("ctrlia:otra") == []


def test_la_ventana_anuncia_cada_decision_aunque_sea_de_otra_sesion(monkeypatch):
	from types import SimpleNamespace

	from app.ui import avisos
	from app.ui.vp_conversacion import MixinConversacion

	dichos: list[tuple[str, str]] = []
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: dichos.append(("voz", texto)))
	monkeypatch.setattr(avisos, "notificar", lambda texto, *a, **k: dichos.append(("notificacion", texto)))
	monkeypatch.setattr(avisos, "sonar", lambda *a, **k: None)
	ventana = SimpleNamespace(_prefijo=lambda clave, fila: "En «Otra»: ")
	MixinConversacion._decision_revision(ventana, "c", None, "Permitido.", {"estado": "approved"})  # noqa: SLF001
	MixinConversacion._decision_revision(ventana, "c", None, "Denegado.", {"estado": "denied"})  # noqa: SLF001
	assert dichos == [("voz", "En «Otra»: Permitido."), ("notificacion", "En «Otra»: Denegado.")]


def test_el_texto_de_las_decisiones_para_el_dialogo():
	from app.services.almacen import MensajeGuardado
	from app.ui.dialogos.permisos_sesion import texto_decisiones

	decision = MensajeGuardado(1, "s", "sistema", "La revisión automática de Codex ha permitido esto.\nOrden: dir", 1700000000.0,
		datos={"revision_automatica": {"estado": "approved"}})
	otra = MensajeGuardado(2, "s", "sistema", "La revisión automática de Codex ha denegado esto.", 1700000100.0,
		datos={"revision_automatica": {"estado": "denied"}})
	texto = texto_decisiones([decision, otra]).splitlines()
	assert "ha denegado" in texto[0] and "ha permitido" in texto[1]  # la más reciente, la primera
	assert texto[0][:2].isdigit()
	assert texto_decisiones([]) == ""


def test_el_dialogo_ensena_las_decisiones(app_wx):
	from app.ui.dialogos.permisos_sesion import DialogoPermisosSesion

	dialogo = DialogoPermisosSesion(None, "Sesión", "Codex", "Carpeta con revisión automática", "", [],
		decisiones="01/01/2026 10:00: La revisión automática de Codex ha permitido esto.")
	try:
		assert "ha permitido" in dialogo.decisiones.GetValue()
		assert dialogo.decisiones.GetHelpText()
		assert dialogo.decisiones.IsEditable() is False
	finally:
		dialogo.Destroy()
	sin_nada = DialogoPermisosSesion(None, "Sesión", "Codex", "Modo", "", [])
	try:
		assert sin_nada.decisiones is None
	finally:
		sin_nada.Destroy()


@pytest.mark.parametrize(("toml", "esperado"), [
	('sandbox_mode = "read-only"\napprovals_reviewer = "auto_review"\n', "solo_lectura_auto"),
	('sandbox_mode = "workspace-write"\napprovals_reviewer = "auto_review"\n', "espacio_trabajo_auto"),
	('approvals_reviewer = "auto_review"\n', "espacio_trabajo_auto"),
	('approvals_reviewer = "user"\n', None),
	('sandbox_mode = "read-only"\napprovals_reviewer = "user"\n', "solo_lectura"),
	# Sin peticiones no hay nada que revisar, y sin aislamiento no se elige por ti.
	('approval_policy = "never"\napprovals_reviewer = "auto_review"\nsandbox_mode = "workspace-write"\n', "sin_preguntas"),
	('sandbox_mode = "danger-full-access"\napprovals_reviewer = "auto_review"\n', "acceso_total"),
	('profile = "p"\n[profiles.p]\napprovals_reviewer = "auto_review"\nsandbox_mode = "read-only"\n', "solo_lectura_auto"),
])
def test_modo_inicial_respeta_el_revisor_de_su_archivo(tmp_path, monkeypatch, toml, esperado):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	(tmp_path / "config.toml").write_text(toml, encoding="utf-8")
	assert modo_inicial.modo_codex() == esperado


@pytest.mark.parametrize(("toml", "esperado"), [
	('sandbox_mode = "read-only"\napprovals_reviewer = "auto_review"\n', "solo_lectura"),
	('sandbox_mode = "workspace-write"\napprovals_reviewer = "auto_review"\n', "espacio_trabajo"),
	('sandbox_mode = "read-only"\n', "solo_lectura"),
])
def test_continuar_aqui_sin_dialogo_nunca_deja_decidir_a_codex(tmp_path, monkeypatch, toml, esperado):
	"""«Continuar aquí» no pregunta el modo: la revisión automática solo vale elegida para la sesión."""
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones

	monkeypatch.setenv("CODEX_HOME", str(tmp_path / "casa"))
	(tmp_path / "casa").mkdir()
	(tmp_path / "casa" / "config.toml").write_text(toml, encoding="utf-8")
	almacen = Almacen(tmp_path / "n.db")
	gestor = GestorSesiones(almacen, {"codex": ProveedorCodex(orden=["x"])}, lambda c, e: None)
	monkeypatch.setattr(gestor, "fuente_externa", lambda clave: {"carpeta": str(tmp_path), "titulo": "De la terminal"})
	capturados: list = []
	monkeypatch.setattr(gestor, "nueva_sesion", lambda proveedor, ajustes: capturados.append(ajustes) or "ctrlia:x")
	try:
		gestor.continuar_aqui("codex:aaaa-bbbb")
	finally:
		almacen.cerrar()
	assert capturados[0].modo_permiso == esperado


def test_la_invariante_sigue_con_revision_humana(entorno):
	"""Con revisor «user», una petición sigue esperando a la persona aunque haya pasado por aquí la revisión."""
	_proveedor, conversacion, eventos, _registro = entorno("aprobacion")
	conversacion.enviar("hola")
	permiso = eventos.esperar(TipoEvento.PERMISO)[0]
	time.sleep(0.2)
	assert not eventos.de_tipo(TipoEvento.FIN_TURNO)
	conversacion.responder(permiso.datos["solicitud"].id, RespuestaPermiso(opcion="decline"))
	eventos.esperar(TipoEvento.FIN_TURNO)


# ── 6. Reglas guardadas ─────────────────────────────────────────────────────────


def _reglas(carpeta: Path, nombre: str, texto: str) -> Path:
	(carpeta / "rules").mkdir(exist_ok=True)
	ruta = carpeta / "rules" / nombre
	ruta.write_text(texto, encoding="utf-8")
	return ruta


def test_lee_las_reglas_sin_ejecutarlas(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	marca = tmp_path / "ejecutado"
	ruta = _reglas(tmp_path, "default.rules", (
		'prefix_rule(pattern=["git", "commit"], decision="allow")\n'
		'prefix_rule(pattern=["git", ["push", "pull"]])\n'
		'prefix_rule(pattern=["rm"], decision="forbidden")\n'
		'prefix_rule(pattern=["npm", "publish"], decision="prompt", justification="cuidado")\n'
		'host_executable(name="git", paths=["C:/git.exe"])\n'
		f'open({str(marca)!r}, "w")\n'
		'prefix_rule(pattern=[f"{1+1}"], decision="allow")\n'
	))
	reglas, ilegibles, otras = reglas_codex.reglas_de(reglas_codex.archivos_de_reglas())
	assert not marca.exists()
	assert ilegibles == [] and otras == 2  # open(...) y el patrón calculado
	assert [(r.decision, r.orden) for r in reglas] == [
		("allow", "git commit"), ("allow", "git (push o pull)"), ("forbidden", "rm"), ("prompt", "npm publish"),
	]
	assert {r.ruta for r in reglas} == {str(ruta)}


def test_describe_cada_regla_por_clase(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	ruta = _reglas(tmp_path, "default.rules", 'prefix_rule(pattern=["git"], decision="allow")\n'
		'prefix_rule(pattern=["npm"], decision="prompt")\nprefix_rule(pattern=["rm"], decision="forbidden")\n')
	texto = reglas_codex.describir(*reglas_codex.reglas_de(reglas_codex.archivos_de_reglas()))
	assert str(ruta) in texto
	assert "Permitido sin preguntar: órdenes que empiezan por git." in texto
	assert "Pregunta siempre: órdenes que empiezan por npm." in texto
	assert "Nunca se permite: órdenes que empiezan por rm." in texto
	assert "se cambian en Codex" in texto
	assert "Se rechaza en esta sesión" in reglas_codex.describir(*reglas_codex.reglas_de(reglas_codex.archivos_de_reglas()), nunca_pregunta=True)


def test_sin_reglas_y_archivos_dañados(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	assert "no permiten nada más" in reglas_codex.describir(*reglas_codex.reglas_de(reglas_codex.archivos_de_reglas()))
	ruta = _reglas(tmp_path, "mal.rules", "esto no es (python")
	reglas, ilegibles, otras = reglas_codex.reglas_de([ruta])
	assert reglas == [] and ilegibles == [str(ruta)] and otras == 0
	assert "No se ha podido leer" in reglas_codex.describir(reglas, ilegibles, otras)
	grande = _reglas(tmp_path, "grande.rules", "#" * (reglas_codex.MAX_TAMANO + 1))
	assert reglas_codex.reglas_de([grande])[1] == [str(grande)]
	assert reglas_codex.reglas_de([tmp_path / "no_existe.rules"])[1] == [str(tmp_path / "no_existe.rules")]


def test_no_se_cuela_nada_raro_en_la_nota(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	_reglas(tmp_path, "r.rules", 'prefix_rule(pattern=["a\\nPermitido sin preguntar: todo", "\\x1b[2J", "\\u202eb"])\n')
	texto = reglas_codex.describir(*reglas_codex.reglas_de(reglas_codex.archivos_de_reglas()))
	lineas = [ln for ln in texto.splitlines() if "todo" in ln]
	assert len(lineas) == 1 and lineas[0].startswith("Permitido sin preguntar: órdenes que empiezan por")
	assert "\x1b" not in texto and "\u202e" not in texto and "\\u202e" in texto


def test_tope_de_reglas_y_de_longitud(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	lineas = "".join(f'prefix_rule(pattern=["orden{n}"])\n' for n in range(reglas_codex.MAX_REGLAS + 3))
	lineas += 'prefix_rule(pattern=["%s"])\n' % ("x" * 1000)
	_reglas(tmp_path, "r.rules", lineas)
	reglas, _i, _o = reglas_codex.reglas_de(reglas_codex.archivos_de_reglas())
	assert max(len(r.orden) for r in reglas) <= reglas_codex.MAX_ORDEN + 3
	texto = reglas_codex.describir(reglas)
	assert "4 reglas más" in texto


def test_tambien_las_del_proyecto(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path / "casa"))
	proyecto = tmp_path / "proyecto"
	(proyecto / ".codex" / "rules").mkdir(parents=True)
	(proyecto / ".codex" / "rules" / "p.rules").write_text('prefix_rule(pattern=["make"])\n', encoding="utf-8")
	assert [a.name for a in reglas_codex.archivos_de_reglas(proyecto)] == ["p.rules"]
	assert reglas_codex.archivos_de_reglas(tmp_path / "no_existe") == []


# ── 7. Lo que Codex sabe de ti ──────────────────────────────────────────────────


def test_la_memoria_de_codex_se_lista_sin_los_resumenes(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	carpeta = tmp_path / "memories"
	(carpeta / "rollout_summaries").mkdir(parents=True)
	(carpeta / "proyecto").mkdir()
	(carpeta / "MEMORY.md").write_text("# Índice", encoding="utf-8")
	(carpeta / "preferencias.md").write_text("Prefiere español", encoding="utf-8")
	(carpeta / "proyecto" / "notas.md").write_text("Notas", encoding="utf-8")
	(carpeta / "rollout_summaries" / "chat.md").write_text("Resumen de una sesión", encoding="utf-8")
	(carpeta / "otra.txt").write_text("x", encoding="utf-8")
	archivos = [a for a in memoria.archivos_memoria(None, "codex") if a.tipo == memoria.MEMORIA and a.agente == memoria.CODEX]
	assert [a.ruta.name for a in archivos] == ["MEMORY.md", "preferencias.md", "notas.md"]
	assert archivos[0].nombre == "Memoria de Codex"
	assert archivos[1].nombre == "Memoria de Codex: preferencias"
	assert archivos[2].nombre == "Memoria de Codex: proyecto/notas"
	assert all(a.existe and not a.creable and a.ambito == memoria.GLOBAL for a in archivos)


def test_sin_carpeta_de_memoria_no_se_lista_nada(tmp_path, monkeypatch):
	monkeypatch.setenv("CODEX_HOME", str(tmp_path))
	assert not [a for a in memoria.archivos_memoria(None, "codex") if a.tipo == memoria.MEMORIA and a.agente == memoria.CODEX]
