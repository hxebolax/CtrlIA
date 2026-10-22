"""Pruebas de ``app.services.extraccion`` y ``app.services.exportar`` (puras, sin wx)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services import extraccion, exportar
from app.services.gestor import MensajeVista

AHORA = time.time()


def _m(rol: str, texto: str, detalle: str = "", herramienta: str = "", cambios=None) -> MensajeVista:
	return MensajeVista(rol, texto, AHORA, detalle, herramienta, 0, list(cambios or []))


# ── Bloques de código ───────────────────────────────────────────────────────────


def test_bloques_con_y_sin_lenguaje_y_sin_cerrar():
	mensajes = [
		_m("usuario", "Mira esto:\n```\nprint('hola')\n```"),
		_m("herramienta", "```python\nno_cuenta()\n```"),
		_m("asistente", "Aquí va:\n```python\ndef f():\n\treturn 1\n```\ny también\n~~~bash\nls -la\n~~~\n"),
		_m("asistente", "Empiezo:\n```js\nconst a = 1;\nconst b = 2;"),
	]
	bloques = extraccion.bloques_codigo(mensajes)
	assert [(b.lenguaje, b.indice) for b in bloques] == [("", 0), ("python", 2), ("bash", 2), ("js", 3)]
	assert bloques[0].codigo == "print('hola')"
	assert bloques[1].codigo == "def f():\n\treturn 1"
	assert bloques[1].num_lineas == 2
	assert bloques[3].codigo == "const a = 1;\nconst b = 2;"
	assert extraccion.ultimo_bloque(mensajes) == bloques[-1]


def test_bloques_valla_larga_y_codigo_en_linea():
	texto = "````markdown\n```python\nx = 1\n```\n````\nY `en línea` y ```tampoco```."
	bloques = extraccion.bloques_codigo([_m("asistente", texto)])
	assert len(bloques) == 1
	assert bloques[0].lenguaje == "markdown"
	assert bloques[0].codigo == "```python\nx = 1\n```"


def test_sin_bloques():
	assert extraccion.bloques_codigo([_m("asistente", "Nada de código")]) == []
	assert extraccion.ultimo_bloque([]) is None
	# Un bloque vacío no se cuenta.
	assert extraccion.bloques_codigo([_m("asistente", "```\n\n```")]) == []


# ── Enlaces ─────────────────────────────────────────────────────────────────────


def test_enlaces_sueltos_y_markdown_sin_duplicados():
	mensajes = [
		_m("asistente", "Consulta https://ejemplo.com/docs. También (https://ejemplo.com/a_(b)) y https://x.org/p?q=1,"),
		_m("usuario", "El [manual](https://ejemplo.com/docs) y [otro](https://otro.es/ruta \"título\")."),
		_m("asistente", "Sin enlaces http: nada aquí; ftp://no.cuenta"),
	]
	resultado = extraccion.enlaces(mensajes)
	assert [e.url for e in resultado] == [
		"https://ejemplo.com/docs",
		"https://ejemplo.com/a_(b)",
		"https://x.org/p?q=1",
		"https://otro.es/ruta",
	]
	# La primera aparición era suelta: toma el texto del enlace Markdown posterior.
	assert resultado[0].texto == "manual" and resultado[0].indice == 0
	assert resultado[3].texto == "otro" and resultado[3].indice == 1
	assert resultado[1].texto == ""


def test_limpiar_url():
	assert extraccion.limpiar_url("https://a.es/x).") == "https://a.es/x"
	assert extraccion.limpiar_url("https://a.es/wiki/A_(b)") == "https://a.es/wiki/A_(b)"
	assert extraccion.limpiar_url("https://a.es/**") == "https://a.es/"


# ── Archivos cambiados ──────────────────────────────────────────────────────────


def test_archivos_cambiados_acumula_y_no_duplica():
	diff1 = "--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n-viejo\n+nuevo\n+otro"
	diff2 = "@@ -5 +5 @@\n-a\n+b"
	mensajes = [
		_m("herramienta", "Editar archivo: C:\\proyecto\\app.py", detalle="file_path: C:\\proyecto\\app.py", herramienta="Edit"),
		_m("cambio", "Archivo modificado: C:\\proyecto\\app.py", cambios=[{"ruta": "C:\\proyecto\\app.py", "diff": diff1}]),
		_m("cambio", "Archivo nuevo: notas.md", detalle="+hola"),
		_m("herramienta", "Escribir: c:/proyecto/APP.py", herramienta="Write"),
		_m("cambio", "Archivo modificado: C:\\proyecto\\app.py", cambios=[{"ruta": "C:\\proyecto\\app.py", "diff": diff2}]),
		_m("herramienta", "Leer archivo: otro.py", herramienta="Read"),
	]
	cambios = extraccion.archivos_cambiados(mensajes)
	assert [c.ruta for c in cambios] == ["C:\\proyecto\\app.py", "notas.md"]
	principal = cambios[0]
	assert principal.indice == 4, "el índice es el del último cambio"
	assert diff1 in principal.diff and diff2 in principal.diff
	assert principal.diff.index(diff1) < principal.diff.index(diff2)
	assert extraccion.contar_lineas_diff(principal.diff) == (3, 2)
	assert cambios[1].diff == "+hola" and cambios[1].indice == 2


def test_ordenes():
	mensajes = [
		_m("herramienta", "Ejecutar orden: git status", herramienta="Bash"),
		_m("herramienta", "Ejecutar: npm test", detalle="Descripción: pruebas\nOrden:\nnpm test -- --watch=false\n\nResultado:\nok"),
		_m("herramienta", "Ejecutar orden de PowerShell: Get-ChildItem"),
		_m("herramienta", "Leer archivo: x.py"),
		_m("asistente", "Ejecutar: esto no es una orden"),
		_m("herramienta", "Ejecutar: dir", detalle="Ejecutar: dir /s"),
	]
	assert extraccion.ordenes(mensajes) == [
		("git status", 0),
		("npm test -- --watch=false", 1),
		("Get-ChildItem", 2),
		("dir /s", 5),
	]


def test_ordenes_detalladas_con_salida_y_error():
	mensajes = [
		_m("herramienta", "Ejecutar orden: pytest", detalle="command: pytest -q\ndescription: pruebas\n\nError:\n1 failed", herramienta="Bash"),
		_m("herramienta", "Ejecutar: dir", detalle="Ejecutar: dir\n\nResultado:\na.txt\nb.txt", herramienta="Ejecutar"),
		_m("herramienta", "Ejecutar orden: git status", herramienta="Bash"),
	]
	detalladas = extraccion.ordenes_detalladas(mensajes)
	assert [(o.orden, o.error, o.salida) for o in detalladas] == [
		("pytest -q", True, "1 failed"),
		("dir", False, "a.txt\nb.txt"),
		("git status", None, ""),
	]
	assert [o.indice for o in detalladas] == [0, 1, 2]


# ── Entradas de herramientas ────────────────────────────────────────────────────


def test_entrada_de_herramienta_en_las_dos_formas():
	historial = 'Entrada:\n{\n "subject": "Probar",\n "activeForm": "Probando"\n}\n\nResultado:\nTask #2 created successfully: Probar'
	assert extraccion.entrada_herramienta(historial) == {"subject": "Probar", "activeForm": "Probando"}
	vivo = 'subject: Probar\ndescription:\nlínea uno\nlínea dos\nlista: [\n  1,\n  2\n]\nfin: sí\n\nResultado:\nhecho'
	assert extraccion.entrada_herramienta(vivo) == {
		"subject": "Probar", "description": "línea uno\nlínea dos", "lista": [1, 2], "fin": "sí",
	}
	assert extraccion.separar_resultado(vivo)[1:] == ("hecho", False)
	assert extraccion.separar_resultado("Error:\nno") == ("", "no", True)
	assert extraccion.separar_resultado("solo entrada") == ("solo entrada", "", None)
	# Lo que no se entiende devuelve un diccionario vacío, nunca un error.
	assert extraccion.entrada_herramienta("Entrada:\n{roto") == {}
	assert extraccion.entrada_herramienta("") == {}


# ── Rutas de archivos en los enlaces ────────────────────────────────────────────


def test_enlaces_con_rutas_de_archivos_locales():
	texto = (
		"Lo dejé en `C:\\Users\\ana\\Mis cosas\\borrador.md` y en C:/temp/log.txt. "
		"Mira [el borrador](C:/Users/ana/draft.md), [listas.py:42](app/ui/listas.py:42) y `app/x.py:10`, "
		"\\\\servidor\\compartido\\a.docx, file:///C:/a%20b/c.txt, https://x.es/a y [la guía](README.md).\n"
		"```\ncd C:\\no\\debe\\salir\n```\n"
		"Otra vez C:/temp/log.txt:7 y C:\\Users\\ana\\proyecto\\Vista.xaml.cs:2687."
	)
	resultado = extraccion.enlaces([_m("asistente", texto), _m("herramienta", "Leer: C:\\herramienta\\no.py")])
	archivos = [(e.url, e.texto, e.linea) for e in resultado if e.es_archivo]
	assert archivos == [
		("C:\\Users\\ana\\Mis cosas\\borrador.md", "", 0),
		("C:/temp/log.txt", "", 7),
		("C:/Users/ana/draft.md", "el borrador", 0),
		("app/ui/listas.py", "listas.py:42", 42),
		("app/x.py", "", 10),
		("\\\\servidor\\compartido\\a.docx", "", 0),
		("C:\\a b\\c.txt", "", 0),
		("README.md", "la guía", 0),
		("C:\\Users\\ana\\proyecto\\Vista.xaml.cs", "", 2687),
	]
	assert [e.url for e in resultado if not e.es_archivo] == ["https://x.es/a"]
	assert all(e.tipo == "archivo" for e in resultado if e.es_archivo)
	# Sin rutas si no se piden: solo la web.
	assert [e.url for e in extraccion.enlaces([_m("asistente", texto)], incluir_archivos=False)] == ["https://x.es/a"]


def test_separar_linea_y_url_de_archivo():
	assert extraccion.separar_linea("app/x.py:42") == ("app/x.py", 42)
	assert extraccion.separar_linea("app/x.py:42:7") == ("app/x.py", 42)
	assert extraccion.separar_linea("docs/a.md#L12-L20") == ("docs/a.md", 12)
	assert extraccion.separar_linea("C:\\a.md") == ("C:\\a.md", 0)
	assert extraccion.separar_linea("C:5") == ("C:5", 0)
	assert extraccion.ruta_de_url_archivo("file:///C:/a%20b/c.txt") == "C:\\a b\\c.txt"
	assert extraccion.ruta_de_url_archivo("file://servidor/x/y.txt") == "\\\\servidor\\x\\y.txt"
	assert extraccion.ruta_de_url_archivo("file://localhost/C:/z.txt") == "C:\\z.txt"


# ── Archivos cambiados: historial, Codex y subagentes ───────────────────────────


def test_archivos_del_historial_y_de_codex():
	from app.services.agentes import codex

	historial = _m("herramienta", "Editar: C:\\p\\a.py", detalle="Archivo: C:\\p\\a.py", herramienta="Edit",
		cambios=[{"ruta": "C:\\p\\a.py", "diff": "@@ -1 +1 @@\n-x\n+y"}])
	cuaderno = _m("herramienta", "Editar cuaderno: C:\\p\\n.ipynb", herramienta="NotebookEdit")
	item = {"type": "fileChange", "id": "1", "status": "completed", "changes": [
		{"path": "C:\\p\\b.py", "kind": {"type": "update"}, "diff": "@@ -1 +1 @@\n-1\n+2"},
		{"path": "C:\\p\\nuevo.txt", "kind": {"type": "add"}, "diff": "+hola"},
	]}
	datos = codex.item_a_mensaje(item, AHORA)
	assert datos["rol"] == "cambio"
	desde_codex = MensajeVista(datos["rol"], datos["texto"], AHORA, datos.get("detalle", ""), datos.get("herramienta", ""))
	cambios = extraccion.archivos_cambiados([historial, cuaderno, desde_codex])
	assert [(c.ruta, c.diff) for c in cambios] == [
		("C:\\p\\a.py", "@@ -1 +1 @@\n-x\n+y"),
		("C:\\p\\n.ipynb", ""),
		("C:\\p\\b.py", "@@ -1 +1 @@\n-1\n+2"),
		("C:\\p\\nuevo.txt", "+hola"),
	]
	# Un solo cambio de Codex: el detalle trae la cabecera y el diff.
	solo = codex.item_a_mensaje({"type": "fileChange", "changes": [{"path": "c.py", "kind": {"type": "update"}, "diff": "-a\n+b"}]})
	uno = extraccion.archivos_cambiados([MensajeVista(solo["rol"], solo["texto"], AHORA, solo["detalle"])])
	assert [(c.ruta, c.diff) for c in uno] == [("c.py", "-a\n+b")]


def test_ordenes_y_subagentes_del_historial_de_codex():
	from app.services.agentes import codex

	def vista(item):
		datos = codex.item_a_mensaje(item, AHORA)
		return MensajeVista(datos["rol"], datos["texto"], AHORA, datos.get("detalle", ""), datos.get("herramienta", ""))

	mensajes = [
		vista({"type": "commandExecution", "command": "npm test", "aggregatedOutput": "1 failed", "exitCode": 1, "status": "completed"}),
		vista({"type": "commandExecution", "command": "ls", "aggregatedOutput": "a\nb", "exitCode": 0, "status": "completed"}),
		vista({"type": "collabAgentToolCall", "prompt": "Revisa los permisos"}),
	]
	assert [(o.orden, o.error, o.salida) for o in extraccion.ordenes_detalladas(mensajes)] == [
		("npm test", True, "1 failed"), ("ls", False, "a\nb"),
	]
	subagentes = extraccion.subagentes(mensajes)
	assert len(subagentes) == 1 and subagentes[0].instrucciones == "Revisa los permisos"
	assert subagentes[0].estado == "en_marcha"


# ── Tareas del agente ───────────────────────────────────────────────────────────


def _tarea_creada(asunto: str, numero: int) -> MensajeVista:
	return _m("herramienta", "Crear tarea: " + asunto, herramienta="TaskCreate",
		detalle='Entrada:\n{"subject": "%s", "activeForm": "Haciendo %s"}\n\nResultado:\nTask #%d created successfully: %s'
		% (asunto, asunto, numero, asunto))


def _tarea_actualizada(numero: int, estado: str, error: bool = False) -> MensajeVista:
	cola = "\n\nError:\nTask not found" if error else "\n\nResultado:\nUpdated task #%d status" % numero
	return _m("herramienta", "Actualizar tarea", herramienta="TaskUpdate",
		detalle="taskId: %d\nstatus: %s%s" % (numero, estado, cola))


def test_tareas_de_un_plan_y_de_todowrite():
	mensajes = [
		_m("plan", "[hecho] Leer\n[en curso] Probar\n[pendiente] Documentar"),
		_m("asistente", "Sigo."),
	]
	tareas = extraccion.tareas(mensajes)
	assert [(t.texto, t.estado) for t in tareas] == [("Leer", "hecha"), ("Probar", "en_curso"), ("Documentar", "pendiente")]
	assert extraccion.resumen_tareas(tareas) == "Tarea 2 de 3: Probar. 1 hechas."
	# Un TodoWrite del historial sustituye la lista entera.
	mensajes.append(_m("herramienta", "Actualizar lista de tareas", herramienta="TodoWrite",
		detalle="Tareas:\n[x] Leer\n[x] Probar\n[ ] Documentar\n\nResultado:\nok"))
	tareas = extraccion.tareas(mensajes)
	assert [(t.estado, t.indice) for t in tareas] == [("hecha", 2), ("hecha", 2), ("pendiente", 2)]
	assert extraccion.resumen_tareas(tareas) == "2 de 3 tareas hechas."


def test_tareas_con_taskcreate_y_taskupdate():
	mensajes = [
		_tarea_creada("Esqueleto", 1),
		_tarea_creada("Interfaz", 2),
		_tarea_creada("Sobra", 3),
		_tarea_actualizada(1, "in_progress"),
		_tarea_actualizada(1, "completed"),
		_tarea_actualizada(3, "deleted"),
		_tarea_actualizada(2, "completed", error=True),
		_tarea_actualizada(9, "completed"),
	]
	antes = extraccion.tareas(mensajes[:4])
	tareas = extraccion.tareas(mensajes)
	assert [(t.id, t.texto, t.estado, t.indice) for t in tareas] == [
		("1", "Esqueleto", "hecha", 4), ("2", "Interfaz", "pendiente", 1),
	]
	assert tareas[0].en_curso_texto == "Haciendo Esqueleto"
	assert extraccion.anuncio_cambio_tareas(antes, tareas) == "Hecha: Esqueleto. 1 de 2."
	assert extraccion.anuncio_cambio_tareas(tareas, tareas) == ""
	assert extraccion.resumen_tareas([]) == ""
	todas = [extraccion.Tarea("a", "hecha", 0), extraccion.Tarea("b", "hecha", 0)]
	assert extraccion.resumen_tareas(todas) == "Todas las tareas hechas (2)."


def test_tareas_de_pasos_de_un_evento():
	tareas = extraccion.tareas_de_pasos([("Uno", "completed"), ("Dos", "inProgress"), ("", "x"), "raro"])
	assert [(t.texto, t.estado) for t in tareas] == [("Uno", "hecha"), ("Dos", "en_curso")]
	assert extraccion.normalizar_estado("en curso") == "en_curso"
	assert extraccion.normalizar_estado(None) == "pendiente"


# ── Subagentes ──────────────────────────────────────────────────────────────────


def test_subagentes_y_lo_que_hicieron():
	import json
	from types import SimpleNamespace

	lanzado = _m("herramienta", "Delegar en agente: Buscar fallos", herramienta="Task",
		detalle="Entrada:\n" + json.dumps({"description": "Buscar fallos", "subagent_type": "Explore", "prompt": "Busca fallos"})
		+ "\n\nResultado:\nNo hay fallos.")
	lanzado.id = "toolu_1"
	en_fondo = _m("herramienta", "Lanzar subagente: Escribir pruebas", herramienta="Agent",
		detalle="description: Escribir pruebas\nprompt: Escribe pruebas\n\nResultado:\nAsync agent launched successfully.\nagentId: x")
	en_fondo.id = 7
	sin_resultado = _m("herramienta", "Lanzar subagente: Otro", herramienta="Agent", detalle="description: Otro")
	mensajes = [_m("usuario", "Hola"), lanzado, en_fondo, sin_resultado,
		_m("cambio", "Archivo modificado: C:\\p\\a.py", cambios=[{"ruta": "C:\\p\\a.py", "diff": "-x\n+y"}])]
	subagentes = extraccion.subagentes(mensajes)
	assert [(s.descripcion, s.tipo, s.estado, s.indice) for s in subagentes] == [
		("Buscar fallos", "Explore", "terminado", 1),
		("Escribir pruebas", "", "en_segundo_plano", 2),
		("Otro", "", "en_marcha", 3),
	]
	assert subagentes[0].resultado == "No hay fallos." and subagentes[1].resultado == ""
	transcrito_fondo = SimpleNamespace(id_herramienta="otro_id", tipo="general-purpose", descripcion="Escribir pruebas", mensajes=[
		_m("usuario", "Escribe pruebas"),
		_m("herramienta", "Editar: C:\\p\\test_a.py", herramienta="Edit", cambios=[{"ruta": "C:\\p\\test_a.py", "diff": "+def test(): pass"}]),
		_m("herramienta", "Escribir: C:\\p\\a.py", herramienta="Write", cambios=[{"ruta": "C:\\p\\a.py", "diff": "+z"}]),
		_m("herramienta", "Ejecutar: pytest", herramienta="Bash", detalle="Orden:\npytest\n\nResultado:\nok"),
		_m("asistente", "Pruebas escritas."),
	])
	transcrito_explorar = SimpleNamespace(id_herramienta="toolu_1", tipo="Explore", descripcion="Buscar fallos",
		mensajes=[_m("herramienta", "Leer: C:\\p\\a.py", herramienta="Read"), _m("asistente", "Nada.")])
	extraccion.completar_subagentes(subagentes, [transcrito_fondo, transcrito_explorar])
	assert (subagentes[0].pasos, subagentes[0].ediciones, subagentes[0].resultado) == (1, 0, "No hay fallos.")
	fondo = subagentes[1]
	assert (fondo.pasos, fondo.ediciones, fondo.estado, fondo.tipo) == (3, 2, "terminado", "general-purpose")
	assert fondo.resultado == "Pruebas escritas."
	assert [c.ruta for c in fondo.archivos] == ["C:\\p\\test_a.py", "C:\\p\\a.py"]
	assert [o.orden for o in fondo.ordenes] == ["pytest"]
	assert subagentes[2].pasos is None
	# Los archivos de los subagentes se suman a los del agente, sin repetir rutas.
	combinados = extraccion.combinar_cambios(extraccion.archivos_cambiados(mensajes), subagentes)
	assert [(c.ruta, c.subagente, c.indice) for c in combinados] == [
		("C:\\p\\a.py", "Escribir pruebas", 4), ("C:\\p\\test_a.py", "Escribir pruebas", 2),
	]
	assert "-x\n+y" in combinados[0].diff and "+z" in combinados[0].diff


def test_leer_subagentes_del_historial_de_claude(tmp_path):
	import json

	from app.services.historial import claude as historial_claude

	principal = tmp_path / "sesion.jsonl"
	principal.write_text("", encoding="utf-8")
	carpeta = tmp_path / "sesion" / "subagents"
	carpeta.mkdir(parents=True)
	registros = [
		{"type": "user", "isSidechain": True, "timestamp": "2026-10-17T10:00:00Z", "uuid": "u1",
			"message": {"role": "user", "content": "Haz algo"}},
		{"type": "assistant", "isSidechain": True, "timestamp": "2026-10-17T10:00:01Z", "uuid": "a1",
			"message": {"id": "m1", "role": "assistant", "content": [
				{"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "C:\\p\\x.py", "content": "a"}}]}},
		{"type": "user", "isSidechain": True, "timestamp": "2026-10-17T10:00:02Z", "uuid": "u2",
			"toolUseResult": {"type": "create", "filePath": "C:\\p\\x.py", "content": "a"},
			"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
		{"type": "assistant", "isSidechain": True, "timestamp": "2026-10-17T10:00:03Z", "uuid": "a2",
			"message": {"id": "m2", "role": "assistant", "content": [{"type": "text", "text": "Hecho."}]}},
	]
	(carpeta / "agent-a1.jsonl").write_text("\n".join(json.dumps(r) for r in registros) + "\n", encoding="utf-8")
	(carpeta / "agent-a1.meta.json").write_text(json.dumps({"agentType": "general-purpose", "description": "Hacer algo",
		"toolUseId": "toolu_9"}), encoding="utf-8")
	(carpeta / "agent-vacio.jsonl").write_text("", encoding="utf-8")
	leidos = historial_claude.leer_subagentes(principal)
	assert len(leidos) == 1
	subagente = leidos[0]
	assert (subagente.id_herramienta, subagente.tipo, subagente.descripcion) == ("toolu_9", "general-purpose", "Hacer algo")
	assert [m.rol for m in subagente.mensajes] == ["usuario", "herramienta", "asistente"]
	assert subagente.mensajes[1].cambios == [{"ruta": "C:\\p\\x.py", "diff": "+a"}]
	# El transcript principal sigue sin mostrar los registros de subagente.
	assert historial_claude.leer_subagentes(tmp_path / "no_existe.jsonl") == []


# ── Hallazgos de una revisión ───────────────────────────────────────────────────


def test_hallazgos_de_revision_y_su_resultado():
	import json

	primero = [
		{"file": "app/a.py", "line": 12, "summary": "Divide entre cero", "short_summary": "División por cero",
			"category": "correctness", "verdict": "CONFIRMED", "failure_scenario": "Con 0 elementos falla"},
		{"file": "app/b.py", "line": "x", "summary": "Lento", "category": "efficiency", "verdict": "PLAUSIBLE"},
		"raro", {"file": "c.py"},
	]
	segundo = [{"file": "app/a.py", "line": 12, "summary": "Divide entre cero", "short_summary": "División por cero",
		"category": "correctness", "outcome": "fixed"}]
	mensajes = [
		_m("herramienta", "Informar de los hallazgos de la revisión", herramienta="ReportFindings",
			detalle="findings: " + json.dumps(primero, indent=2) + "\nlevel: high\n\nResultado:\nok"),
		_m("asistente", "Corrijo."),
		_m("herramienta", "Herramienta ReportFindings", herramienta="ReportFindings",
			detalle="Entrada:\n" + json.dumps({"findings": segundo}, indent=1)),
	]
	hallazgos = extraccion.hallazgos(mensajes)
	assert [(h.corto, h.archivo, h.linea, h.categoria, h.veredicto, h.resultado, h.indice) for h in hallazgos] == [
		("División por cero", "app/a.py", 12, "correctness", "confirmado", "corregido", 2),
		("Lento", "app/b.py", 0, "efficiency", "posible", "", 0),
	]
	assert hallazgos[0].escenario == "Con 0 elementos falla"
	assert extraccion.resumen_hallazgos(hallazgos) == "Revisión: 2 hallazgos, 1 confirmado, 1 corregido."
	assert extraccion.resumen_hallazgos([]) == ""
	assert extraccion.nombre_resultado("sin_cambios") == "No hacía falta"


# ── Exportar ────────────────────────────────────────────────────────────────────


def _conversacion() -> list[MensajeVista]:
	return [
		_m("usuario", "Hola, ¿qué tal? <script>alert(1)</script>"),
		_m("asistente", "# Título interno\n\nUn [enlace](javascript:alert(1)) y otro [bueno](https://a.es).\n\n"
			"![foto](https://rastreo.example/img.png)\n\n```python\nprint('<b>')\n```"),
		_m("herramienta", "Ejecutar orden: ls", detalle="Orden:\nls\n\nResultado:\nuno", herramienta="Bash"),
		_m("cambio", "Archivo modificado: a.py", cambios=[{"ruta": "a.py", "diff": "-x\n+y"}]),
	]


def test_deducir_formato():
	assert exportar.deducir_formato(Path("a.MD")) == "md"
	assert exportar.deducir_formato(Path("a.htm")) == "html"
	assert exportar.deducir_formato(Path("a.txt")) == "txt"
	assert exportar.deducir_formato(Path("a.loquesea"), "html") == "html"
	with pytest.raises(ValueError):
		exportar.deducir_formato(Path("a.pdf"))
	with pytest.raises(ValueError):
		exportar.deducir_formato(Path("a.md"), "pdf")


def test_exportar_markdown(tmp_path):
	ruta = exportar.exportar(_conversacion(), tmp_path / "sub" / "conv.md", "Mi sesión",
		datos={"agente": "claude", "carpeta": "E:\\proyecto", "Modelo": "opus"})
	texto = ruta.read_text(encoding="utf-8")
	assert texto.startswith("# Mi sesión\n")
	assert "- **Agente:** Claude" in texto
	assert "- **Carpeta:** E:\\proyecto" in texto
	assert "- **Modelo:** opus" in texto
	assert "- **Exportada el:**" in texto
	assert "### Tú · " in texto and "### Agente · " in texto
	assert "### Herramienta (Bash) · " in texto
	assert "```diff\n-x\n+y\n```" in texto
	assert not list(tmp_path.joinpath("sub").glob("*.tmp")), "no quedan temporales"


def test_exportar_html_seguro_y_accesible(tmp_path):
	ruta = exportar.exportar(_conversacion(), tmp_path / "conv.html", "Sesión <rara>", datos={"agente": "codex"})
	pagina = ruta.read_text(encoding="utf-8")
	assert '<html lang="es">' in pagina
	assert pagina.count("<h1>") == 1 and "<h1>Sesión &lt;rara&gt;</h1>" in pagina
	assert pagina.count("<section") == 4 and pagina.count("<h2") == 4
	assert "<script>" not in pagina and "&lt;script&gt;" in pagina
	assert "javascript:" not in pagina
	assert 'href="https://a.es"' in pagina
	assert "<img" not in pagina and "foto" in pagina
	assert "<pre><code class=\"language-python\">print('&lt;b&gt;')" in pagina
	assert '<pre><code class="language-diff">-x\n+y</code></pre>' in pagina
	assert "<h3>Título interno</h3>" in pagina, "los encabezados del mensaje bajan de nivel"
	assert "<h1>Título interno" not in pagina
	assert "Codex" in pagina


def test_exportar_texto(tmp_path):
	ruta = exportar.exportar(_conversacion(), tmp_path / "conv.txt", "Sesión")
	texto = ruta.read_text(encoding="utf-8")
	assert texto.startswith("Sesión\n======\n")
	assert "Exportada el:" in texto
	assert "Hola, ¿qué tal?" in texto
	assert "Resultado:\nuno" in texto


def test_exportar_formato_explicito_y_sobrescribe(tmp_path):
	ruta = tmp_path / "salida.dat"
	ruta.write_text("viejo", encoding="utf-8")
	exportar.exportar(_conversacion(), ruta, "T", formato="markdown")
	assert ruta.read_text(encoding="utf-8").startswith("# T\n")
	# «data» es la carpeta aislada que crea el conftest en cada prueba.
	assert sorted(p.name for p in tmp_path.iterdir() if p.name != "data") == ["salida.dat"]


def test_exportar_extension_desconocida_no_escribe(tmp_path):
	with pytest.raises(ValueError):
		exportar.exportar(_conversacion(), tmp_path / "conv.pdf", "T")
	assert not any(p for p in tmp_path.iterdir() if p.name != "data")
