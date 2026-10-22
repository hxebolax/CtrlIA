"""Pruebas de los diálogos secundarios (``app.ui.dialogos``).

Los diálogos se crean SIN mostrarse: nunca ``Show``, ``ShowModal`` real,
``MainLoop`` ni ``SetFocus`` (el usuario trabaja con un lector de pantalla
en este mismo equipo y una ventana visible lo interrumpiría). Los cuadros
modales que abrirían las acciones se sustituyen con ``monkeypatch``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from app.services.almacen import Almacen  # noqa: E402
from app.services.gestor import MensajeVista  # noqa: E402
from app.ui.accesibilidad import voz  # noqa: E402
from app.ui.dialogos import buscar as modulo_buscar  # noqa: E402
from app.ui.dialogos import columnas as modulo_columnas  # noqa: E402
from app.ui.dialogos import grupos as modulo_grupos  # noqa: E402
from app.ui.dialogos import listas as modulo_listas  # noqa: E402
from app.ui.dialogos import plantillas as modulo_plantillas  # noqa: E402
from tests.ayudas_wx import esperar_hasta, procesar_eventos, simular_tecla  # noqa: E402

AHORA = time.time()
INTERACTIVOS = (wx.Button, wx.TextCtrl, wx.ListCtrl, wx.ListBox, wx.Notebook, wx.Choice, wx.CheckBox)
INGLES = {"ok", "cancel", "close", "yes", "no", "apply", "help"}


@pytest.fixture(scope="module")
def app():
	"""Una ``wx.App`` para todo el módulo (se reutiliza si ya existe)."""
	aplicacion = wx.App.Get() or wx.App(False)
	wx.HelpProvider.Set(wx.SimpleHelpProvider())
	yield aplicacion


@pytest.fixture(autouse=True)
def anuncios(monkeypatch):
	"""Silencia la voz y guarda lo que se habría dicho."""
	dichos: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *args, **kwargs: dichos.append(texto))
	return dichos


@pytest.fixture
def almacen(tmp_path):
	datos = Almacen(tmp_path / "pruebas.db")
	yield datos
	datos.cerrar()


def _m(rol: str, texto: str, detalle: str = "", herramienta: str = "", cambios=None) -> MensajeVista:
	return MensajeVista(rol, texto, AHORA, detalle, herramienta, 0, list(cambios or []))


def _descendientes(ventana: wx.Window):
	for hijo in ventana.GetChildren():
		yield hijo
		yield from _descendientes(hijo)


def _cerrar(dialogo: wx.Dialog) -> None:
	dialogo.Destroy()
	procesar_eventos()


def _comprobar_accesibilidad(dialogo: wx.Dialog) -> None:
	"""Ayuda en todo control interactivo, etiquetas delante y nada en inglés."""
	assert dialogo.GetEscapeId() == wx.ID_CANCEL
	for control in _descendientes(dialogo):
		if not isinstance(control, INTERACTIVOS):
			continue
		assert control.GetHelpText().strip(), "sin ayuda (Ctrl+H): {0!r} {1}".format(control, control.GetLabel())
		if isinstance(control, wx.Button):
			etiqueta = control.GetLabel()
			assert "&" in etiqueta, "botón sin acelerador: {0}".format(etiqueta)
			assert etiqueta.replace("&", "").strip(" .…").lower() not in INGLES, etiqueta
		if isinstance(control, (wx.ListCtrl, wx.ListBox, wx.TextCtrl)):
			hermanos = list(control.GetParent().GetChildren())
			anterior = hermanos[hermanos.index(control) - 1]
			assert isinstance(anterior, wx.StaticText), "la etiqueta debe crearse justo antes: {0}".format(control.GetName())
			assert "&" in anterior.GetLabel()


# ── Listas de la sesión ─────────────────────────────────────────────────────────

MENSAJES = [
	_m("usuario", "Prueba esto:\n```python\nimport os\nprint(os.getcwd())\n```\nY mira https://docs.python.org/3/."),
	_m("herramienta", "Ejecutar orden: git status", herramienta="Bash"),
	_m("cambio", "Archivo modificado: app\\main.py", cambios=[{"ruta": "app\\main.py", "diff": "-a\n+b\n+c"}]),
	_m("asistente", "Hecho. Ver [la guía](https://ejemplo.es/guia).\n```\nsin lenguaje\n```"),
]


def test_listas_carga_los_datos(app):
	dialogo = modulo_listas.DialogoListas(None, MENSAJES, "Mi sesión", pestana="enlaces")
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.GetTitle() == "Listas de la sesión «Mi sesión»"
		assert dialogo.libro.GetPageCount() == len(modulo_listas.PAGINAS) == 13
		assert [dialogo.libro.GetPageText(i) for i in range(8)] == [
			"Bloques de código", "Enlaces", "Archivos cambiados", "Órdenes ejecutadas",
			"Tareas del agente", "Subagentes", "Hallazgos de la revisión", "Decisiones",
		]
		# Cada etiqueta tiene su propia letra en todo el diálogo.
		letras = [etiqueta.GetLabel().split("&", 1)[1][0].lower() for etiqueta in dialogo.etiquetas.values()]
		letras.append("c")  # «&Cerrar»
		assert len(letras) == len(set(letras)), letras
		assert dialogo.pagina_actual() == "enlaces"
		codigo = dialogo.listas["codigo"]
		assert codigo.numero_filas() == 2
		assert codigo.valores_fila(0) == ["python", "import os", "2"]
		assert codigo.valores_fila(1) == ["sin indicar", "sin lenguaje", "1"]
		assert codigo.fila_actual() == 0, "primer elemento seleccionado"
		enlaces = dialogo.listas["enlaces"]
		assert enlaces.valores_fila(0) == ["docs.python.org", "https://docs.python.org/3/", "Web"]
		assert enlaces.valores_fila(1) == ["la guía", "https://ejemplo.es/guia", "Web"]
		assert dialogo.listas["archivos"].valores_fila(0) == ["app\\main.py", "+2/−1"]
		assert dialogo.listas["ordenes"].valores_fila(0) == ["git status", "Sin resultado"]
		assert dialogo.etiquetas["codigo"].GetLabel() == "&Bloques de código (2):"
		assert dialogo.boton_cerrar.GetLabel() == "&Cerrar"
	finally:
		_cerrar(dialogo)


def test_listas_acciones(app, monkeypatch, tmp_path, anuncios):
	copiados: list[str] = []
	documentos: list[tuple[str, str, bool]] = []
	abiertos: list[str] = []
	monkeypatch.setattr(modulo_listas, "copiar_al_portapapeles", lambda texto: copiados.append(texto) or True)
	monkeypatch.setattr(modulo_listas, "mostrar_documento",
		lambda padre, titulo, texto, forzar_texto=False: documentos.append((titulo, texto, forzar_texto)))
	monkeypatch.setattr(modulo_listas.webbrowser, "open", lambda url, *a, **k: abiertos.append(url) or True)
	dialogo = modulo_listas.DialogoListas(None, MENSAJES, "Sesión")
	try:
		assert dialogo.pagina_actual() == "codigo"
		# Intro: ver el bloque completo en modo texto.
		dialogo.activar()
		titulo, texto, forzar = documentos[-1]
		assert titulo == "Bloque de código 1 (python)" and forzar is True
		assert "import os\nprint(os.getcwd())" in texto
		# Ctrl+C copia el código y lo anuncia.
		assert simular_tecla(dialogo, "c", ctrl=True).gestionado
		assert copiados == ["import os\nprint(os.getcwd())"]
		assert anuncios[-1] == "Copiado"
		# Ctrl+S guarda con la extensión del lenguaje.
		sugeridos: list[tuple[str, str]] = []

		def pedir_ruta(sugerido, comodin):
			sugeridos.append((sugerido, comodin))
			return tmp_path / sugerido

		monkeypatch.setattr(dialogo, "_pedir_ruta_guardar", pedir_ruta)
		assert simular_tecla(dialogo, "s", ctrl=True).gestionado
		assert sugeridos[0][0] == "bloque_1.py" and "*.py" in sugeridos[0][1]
		assert (tmp_path / "bloque_1.py").read_text(encoding="utf-8") == "import os\nprint(os.getcwd())\n"
		# Ctrl+2: página de enlaces; Intro abre en el navegador.
		assert simular_tecla(dialogo, "2", ctrl=True).gestionado
		assert dialogo.pagina_actual() == "enlaces"
		dialogo.activar()
		assert abiertos == ["https://docs.python.org/3/"]
		dialogo.copiar()
		assert copiados[-1] == "https://docs.python.org/3/"
		# Ctrl+S fuera de código/archivos no guarda nada.
		assert dialogo.guardar() is None
		# Archivos: Intro muestra el diff y Ctrl+C copia la ruta.
		dialogo.ir_a_pagina(2)
		dialogo.activar()
		# De fábrica, las líneas se leen con palabras (sin cabeceras «@@» no hay número de línea).
		assert documentos[-1][0] == "Cambios en app\\main.py" and "Quitada: a\nAñadida: b\nAñadida: c" in documentos[-1][1]
		dialogo.copiar()
		assert copiados[-1] == "app\\main.py"
		# Órdenes: Ctrl+C copia la orden.
		dialogo.ir_a_pagina(3)
		assert dialogo.texto_a_copiar() == "git status"
		# El menú contextual existe y está en español.
		menu = dialogo._crear_menu("ordenes")
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		assert etiquetas[0].startswith("&Ver la orden y su resultado") and any("Ir al mensaje" in e for e in etiquetas)
		menu.Destroy()
		# Alt+Intro: cierra e indica el mensaje.
		assert simular_tecla(dialogo, wx.WXK_RETURN, alt=True).gestionado
		assert dialogo.indice_mensaje == 1
		assert dialogo.GetReturnCode() == wx.ID_OK
	finally:
		_cerrar(dialogo)


def test_listas_vacias_lo_anuncian(app, anuncios, monkeypatch):
	monkeypatch.setattr(modulo_listas, "copiar_al_portapapeles", lambda texto: pytest.fail("no debe copiar"))
	dialogo = modulo_listas.DialogoListas(None, [_m("asistente", "Sin nada")], "", pestana="desconocida")
	try:
		assert dialogo.pagina_actual() == "codigo"
		assert all(lista.numero_filas() == 0 for lista in dialogo.listas.values())
		dialogo._anunciar_si_vacia()
		assert anuncios[-1] == "No hay bloques de código en esta sesión"
		assert dialogo.copiar() is False
		dialogo.ir_a_pagina(3)
		assert anuncios[-1] == "No hay órdenes ejecutadas en esta sesión"
		dialogo.ir_al_mensaje()
		assert dialogo.indice_mensaje is None
		assert dialogo._crear_menu("ordenes") is None
	finally:
		_cerrar(dialogo)


def test_extension_por_lenguaje():
	assert modulo_listas.extension_para("Python") == ".py"
	assert modulo_listas.extension_para("powershell") == ".ps1"
	assert modulo_listas.extension_para("") == ".txt"
	assert modulo_listas.extension_para("inventado") == ".txt"


def _mensajes_completos(carpeta) -> list[MensajeVista]:
	import json

	borrador = carpeta / "borrador.md"
	borrador.write_text("# Borrador\nTexto del borrador.\n", encoding="utf-8")
	(carpeta / "script.py").write_text("print('no se ejecuta')\n", encoding="utf-8")
	lanzado = _m("herramienta", "Lanzar subagente: Buscar fallos", herramienta="Task",
		detalle="description: Buscar fallos\nsubagent_type: Explore\nprompt: Busca fallos\n\nResultado:\nNo hay fallos.")
	hallazgos = [{"file": str(carpeta / "script.py"), "line": 3, "summary": "Divide entre cero", "short_summary": "División por cero",
		"category": "correctness", "verdict": "CONFIRMED", "failure_scenario": "Con una lista vacía"}]
	return [
		_m("asistente", "Te dejé `{b}`, el script {s} y [la guía](docs/guia.md).".format(b=borrador, s=carpeta / "script.py")),
		_m("plan", "[hecho] Leer el código\n[en curso] Escribir pruebas\n[pendiente] Documentar"),
		lanzado,
		_m("herramienta", "Informar de los hallazgos de la revisión", herramienta="ReportFindings",
			detalle="findings: " + json.dumps(hallazgos, indent=2) + "\nlevel: high"),
		_m("herramienta", "Ejecutar orden: pytest", detalle="command: pytest\n\nError:\n1 failed", herramienta="Bash"),
		_m("cambio", "Archivo modificado: " + str(borrador), cambios=[{"ruta": str(borrador), "diff": "-a\n+b"}]),
	]


def _transcrito_subagente(carpeta):
	from types import SimpleNamespace

	return SimpleNamespace(id_herramienta="", tipo="Explore", descripcion="Buscar fallos", mensajes=[
		_m("herramienta", "Leer: x", herramienta="Read"),
		_m("herramienta", "Editar: " + str(carpeta / "sub.py"), herramienta="Edit",
			cambios=[{"ruta": str(carpeta / "sub.py"), "diff": "+nuevo"}]),
		_m("asistente", "Listo."),
	])


def test_listas_paginas_nuevas(app, tmp_path):
	mensajes = _mensajes_completos(tmp_path)
	dialogo = modulo_listas.DialogoListas(None, mensajes, "Sesión", pestana="tareas", carpeta=str(tmp_path),
		editor="", propia=True, transcritos=[_transcrito_subagente(tmp_path)])
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.pagina_actual() == "tareas"
		tareas = dialogo.listas["tareas"]
		assert [tareas.valores_fila(i) for i in range(3)] == [
			["Leer el código", "Hecha"], ["Escribir pruebas", "En curso"], ["Documentar", "Pendiente"],
		]
		assert dialogo.etiquetas["tareas"].GetLabel() == "&Tareas del agente (1 de 3 hechas):"
		assert dialogo.listas["subagentes"].valores_fila(0) == ["Buscar fallos", "Explore", "Terminado", "2", "1"]
		assert dialogo.listas["hallazgos"].valores_fila(0) == [
			"División por cero", "script.py", "3", "correctness", "Confirmado", "Pendiente",
		]
		assert dialogo.listas["ordenes"].valores_fila(0) == ["pytest", "Con error"]
		# Los archivos del subagente se suman a los del agente.
		archivos = dialogo.listas["archivos"]
		assert archivos.numero_filas() == 2
		assert archivos.valores_fila(1) == [str(tmp_path / "sub.py"), "+1/−0, de un subagente"]
		# Las rutas de archivos salen en Enlaces, con si existen.
		enlaces = dialogo.listas["enlaces"]
		assert [enlaces.valores_fila(i)[2] for i in range(enlaces.numero_filas())] == [
			"Archivo", "Archivo", "Archivo que no existe",
		]
		assert enlaces.valores_fila(2)[:2] == ["la guía", "docs/guia.md"]
		assert "pueden haber cambiado archivos" in archivos.GetHelpText()
		# Ctrl+7 lleva a los hallazgos.
		assert simular_tecla(dialogo, "7", ctrl=True).gestionado
		assert dialogo.pagina_actual() == "hallazgos"
	finally:
		_cerrar(dialogo)


def test_listas_menus_sin_letras_repetidas(app, tmp_path):
	dialogo = modulo_listas.DialogoListas(None, _mensajes_completos(tmp_path), "", carpeta=str(tmp_path), editor="",
		propia=False, transcritos=[])
	try:
		for numero, clave in enumerate(modulo_listas.PAGINAS):
			dialogo.ir_a_pagina(numero)
			filas = dialogo.listas[clave].numero_filas()
			for fila in range(filas):
				dialogo.listas[clave].enfocar_fila(fila, anunciar=False)
				menu = dialogo._crear_menu(clave)
				assert menu is not None, clave
				etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
				letras = [etiqueta.split("&", 1)[1][0].lower() for etiqueta in etiquetas]
				assert len(letras) == len(set(letras)), (clave, etiquetas)
				assert all("\t" in etiqueta for etiqueta in etiquetas), etiquetas
				menu.Destroy()
	finally:
		_cerrar(dialogo)


def test_listas_acciones_de_archivo(app, monkeypatch, tmp_path, anuncios):
	from app.services.agentes import procesos

	documentos: list[tuple[str, str]] = []
	abiertos: list = []
	lanzados: list[list[str]] = []
	copiados: list[str] = []
	monkeypatch.setattr(modulo_listas, "mostrar_documento",
		lambda padre, titulo, texto, forzar_texto=False: documentos.append((titulo, texto)))
	monkeypatch.setattr(modulo_listas.os, "startfile", lambda ruta: abiertos.append(ruta), raising=False)
	monkeypatch.setattr(procesos, "lanzar_independiente", lambda orden, carpeta=None, entorno=None: lanzados.append(list(orden)) or True)
	monkeypatch.setattr(modulo_listas, "_buscar_editor", lambda configurado: "C:\\Programas\\Code.exe")
	monkeypatch.setattr(modulo_listas, "copiar_al_portapapeles", lambda texto: copiados.append(texto) or True)
	borrador = tmp_path / "borrador.md"
	dialogo = modulo_listas.DialogoListas(None, _mensajes_completos(tmp_path), "", pestana="enlaces",
		carpeta=str(tmp_path), editor="", propia=False, transcritos=[])
	try:
		# Enlace a un archivo: Intro lo abre con su programa.
		dialogo.activar()
		assert abiertos == [borrador]
		# Ctrl+L lo lee entero desde el disco.
		assert simular_tecla(dialogo, "l", ctrl=True).gestionado
		assert documentos[-1][0] == "Archivo {r}".format(r=borrador) and "Texto del borrador." in documentos[-1][1]
		# Ctrl+Mayús+E lo muestra seleccionado en el Explorador.
		assert simular_tecla(dialogo, "e", ctrl=True, mayus=True).gestionado
		assert lanzados[-1] == ["explorer.exe", "/select,{r}".format(r=borrador)]
		# Un script no se abre nunca: se ejecutaría.
		dialogo.listas["enlaces"].enfocar_fila(1, anunciar=False)
		assert dialogo.abrir_con_windows() is False
		assert abiertos == [borrador] and "Por seguridad" in anuncios[-1]
		# Ruta relativa que no existe: no se abre y se dice por qué.
		dialogo.listas["enlaces"].enfocar_fila(2, anunciar=False)
		assert dialogo.abrir_con_windows() is False and "ya no existe" in anuncios[-1]
		# Archivos cambiados: Ctrl+E abre el editor, Ctrl+Mayús+C copia el contenido.
		dialogo.ir_a_pagina(2)
		assert simular_tecla(dialogo, "e", ctrl=True).gestionado
		assert lanzados[-1] == ["C:\\Programas\\Code.exe", str(borrador)]
		assert simular_tecla(dialogo, "c", ctrl=True, mayus=True).gestionado
		assert copiados[-1] == "# Borrador\nTexto del borrador.\n"
		# Órdenes: Ctrl+Mayús+C copia la salida; Intro enseña orden y resultado.
		dialogo.ir_a_pagina(3)
		assert dialogo.copiar_contenido() is True and copiados[-1] == "1 failed"
		dialogo.activar()
		assert "pytest" in documentos[-1][1] and "con error" in documentos[-1][1]
		# Hallazgos: Ctrl+E abre el editor en la línea del hallazgo.
		dialogo.ir_a_pagina(6)
		assert simular_tecla(dialogo, "e", ctrl=True).gestionado
		assert lanzados[-1] == ["C:\\Programas\\Code.exe", "-g", "{r}:3".format(r=tmp_path / "script.py")]
		dialogo.activar()
		assert "Con una lista vacía" in documentos[-1][1]
		# En una sesión que no es propia, la corrección se copia al portapapeles.
		assert simular_tecla(dialogo, "r", ctrl=True).gestionado
		assert copiados[-1].startswith("Corrige este hallazgo") and "script.py, línea 3: Divide entre cero" in copiados[-1]
		# Subagentes: Intro enseña su encargo y su respuesta.
		dialogo.ir_a_pagina(5)
		dialogo.activar()
		assert "Busca fallos" in documentos[-1][1] and "No hay fallos." in documentos[-1][1]
		# Las acciones de archivo no hacen nada fuera de su sitio.
		dialogo.ir_a_pagina(4)
		assert dialogo.leer_archivo() is False and anuncios[-1] == "Este elemento no es un archivo"
		assert dialogo.copiar_contenido() is False
	finally:
		_cerrar(dialogo)


def test_listas_corregir_hallazgo_en_el_cuadro_de_respuesta(app, tmp_path, anuncios):
	marco = wx.Frame(None)
	marco.respuesta = wx.TextCtrl(marco, style=wx.TE_MULTILINE)
	marco.respuesta.SetValue("Hola")
	dialogo = modulo_listas.DialogoListas(marco, _mensajes_completos(tmp_path), "", pestana="hallazgos",
		carpeta=str(tmp_path), editor="", propia=True, transcritos=[])
	try:
		assert dialogo.preparar_correccion() is True
		valor = marco.respuesta.GetValue()
		assert valor.startswith("Hola\n\nCorrige este hallazgo") and "Divide entre cero" in valor
		assert dialogo.mensaje_preparado in valor
		assert dialogo.GetReturnCode() == wx.ID_OK and "Control+Intro" in anuncios[-1]
	finally:
		_cerrar(dialogo)
		marco.Destroy()
		procesar_eventos()


def test_listas_leen_los_subagentes_en_segundo_plano(app, tmp_path, monkeypatch):
	from types import SimpleNamespace

	from app.services.historial import claude as historial_claude

	leidos: list = []

	def leer(ruta):
		leidos.append(ruta)
		return [_transcrito_subagente(tmp_path)]

	monkeypatch.setattr(historial_claude, "leer_subagentes", leer)
	transcript = tmp_path / "sesion.jsonl"
	dialogo = modulo_listas.DialogoListas(None, _mensajes_completos(tmp_path), "", pestana="subagentes",
		carpeta=str(tmp_path), editor="", propia=True, ruta_transcript=transcript)
	try:
		esperar_hasta(lambda: dialogo.listas["subagentes"].valores_fila(0)[3] == "2", mensaje="subagentes sin completar")
		assert leidos == [transcript]
		assert dialogo.listas["archivos"].numero_filas() == 2
		assert dialogo.etiquetas["archivos"].GetLabel() == "&Archivos cambiados (2):"
	finally:
		_cerrar(dialogo)
	# El contexto (carpeta, transcript, editor, si es propia) sale de la ventana principal.
	monkeypatch.setattr(historial_claude, "ruta_transcript", lambda id_sesion, carpeta="": Path(carpeta) / (id_sesion + ".jsonl"))
	fila = SimpleNamespace(carpeta=str(tmp_path), propia=True, proveedor="claude", id_externo="abc")
	padre = SimpleNamespace(sesion_actual="ctrlia:1", fila_de=lambda clave: fila, opcion=lambda clave, defecto=None: "C:\\ed.exe")
	contexto = modulo_listas._contexto_de_padre(padre)
	assert contexto == {"carpeta": str(tmp_path), "ruta_transcript": tmp_path / "abc.jsonl", "editor": "C:\\ed.exe", "propia": True}
	padre.sesion_actual = "claude:xyz"
	fila.propia = False
	assert modulo_listas._contexto_de_padre(padre)["ruta_transcript"] == tmp_path / "xyz.jsonl"
	assert modulo_listas._contexto_de_padre(None)["ruta_transcript"] is None


def test_leer_texto_archivo_y_seguridad(tmp_path):
	texto = tmp_path / "a.txt"
	texto.write_text("hola\n", encoding="utf-8")
	assert modulo_listas.leer_texto_archivo(texto) == ("hola\n", "")
	binario = tmp_path / "b.bin"
	binario.write_bytes(b"\0\1\2")
	assert modulo_listas.leer_texto_archivo(binario)[0] is None
	assert "ya no está" in modulo_listas.leer_texto_archivo(tmp_path / "no.txt")[1]
	assert "carpeta" in modulo_listas.leer_texto_archivo(tmp_path)[1]
	latin = tmp_path / "c.txt"
	latin.write_bytes("año".encode("cp1252"))
	assert modulo_listas.leer_texto_archivo(latin)[0] == "año"
	for nombre in ("x.exe", "x.BAT", "x.ps1", "x.lnk", "x.py", "x.vbs"):
		assert modulo_listas.es_peligroso(Path(nombre)), nombre
	for nombre in ("x.md", "x.txt", "x.docx", "x.png"):
		assert not modulo_listas.es_peligroso(Path(nombre)), nombre
	assert modulo_listas.orden_editor("C:\\VS\\Code.exe", "a.py", 4) == ["C:\\VS\\Code.exe", "-g", "a.py:4"]
	assert modulo_listas.orden_editor("notepad++.exe", "a.py", 4) == ["notepad++.exe", "-n4", "a.py"]
	assert modulo_listas.orden_editor("notepad.exe", "a.py", 4) == ["notepad.exe", "a.py"]


# ── Buscar en todas las sesiones ────────────────────────────────────────────────


def test_fragmento_alrededor_de_la_coincidencia():
	texto = ("palabra " * 60) + "La canción del verano " + ("relleno " * 60)
	resultado = modulo_buscar.fragmento(texto, "cancion")
	assert "canción" in resultado
	assert resultado.startswith("…") and resultado.endswith("…")
	assert len(resultado) <= modulo_buscar.ANCHO_FRAGMENTO + 2
	assert modulo_buscar.fragmento("corto\n\ntexto", "x") == "corto texto"
	mensaje = _m("herramienta", "Ejecutar: ls", detalle="salida con la palabra clave")
	assert "clave" in modulo_buscar.fragmento_mensaje(mensaje, "clave")


def test_buscar_en_hilo_y_elegir(app, anuncios):
	llamadas: list[tuple[str, str]] = []
	resultados = [
		("ctrlia:1", "Sesión uno", _m("asistente", "Hay una canción aquí")),
		("claude:2", "Sesión dos", _m("usuario", "otra cancion")),
	]

	def buscar(consulta, cancelado):
		llamadas.append((consulta, threading.current_thread().name))
		assert cancelado() is False
		return resultados

	dialogo = modulo_buscar.DialogoBuscarTodo(None, buscar)
	try:
		_comprobar_accesibilidad(dialogo)
		hijos = list(dialogo.campo.GetParent().GetChildren())
		assert hijos[hijos.index(dialogo.campo) - 1].GetLabel() == "&Buscar:"
		assert dialogo.buscar() is False
		assert anuncios[-1] == "Escribe el texto que quieres buscar"
		dialogo.campo.SetValue("canción")
		assert dialogo.buscar() is True
		assert anuncios[-1] == "Buscando…"
		assert dialogo.boton_buscar.GetLabel() == "&Detener"
		esperar_hasta(lambda: not dialogo.buscando, 5, "la búsqueda no terminó")
		assert llamadas == [("canción", "buscar-todo")], "se busca en un hilo aparte"
		assert anuncios[-1] == "2 resultados"
		assert dialogo.boton_buscar.GetLabel() == "B&uscar"
		assert dialogo.lista.numero_filas() == 2
		fila = dialogo.lista.valores_fila(1)
		assert fila[0] == "Sesión dos" and fila[1] == "Tú" and "cancion" in fila[2]
		dialogo.lista.enfocar_fila(1, anunciar=False)
		assert dialogo.abrir_resultado() is True
		assert dialogo.resultado == ("claude:2", resultados[1][2])
		assert dialogo.GetReturnCode() == wx.ID_OK
	finally:
		_cerrar(dialogo)


def test_buscar_sin_resultados_y_consulta_inicial(app, anuncios):
	dialogo = modulo_buscar.DialogoBuscarTodo(None, lambda consulta, cancelado: [], consulta_inicial="nada")
	try:
		esperar_hasta(lambda: anuncios and anuncios[-1] == "No hay resultados", 5, "no se anunció el resultado")
		assert dialogo.lista.numero_filas() == 0
		assert dialogo.abrir_resultado() is False
		assert dialogo.resultado is None
	finally:
		_cerrar(dialogo)


def test_escape_detiene_la_busqueda_sin_cerrar(app, anuncios, monkeypatch):
	empezada = threading.Event()

	def buscar_lento(consulta, cancelado):
		empezada.set()
		limite = time.monotonic() + 5
		while not cancelado() and time.monotonic() < limite:
			time.sleep(0.01)
		return [("ctrlia:1", "Sesión", _m("asistente", "parcial"))]

	dialogo = modulo_buscar.DialogoBuscarTodo(None, buscar_lento)
	cierres: list[int] = []
	monkeypatch.setattr(dialogo, "Close", lambda *args: cierres.append(1))
	monkeypatch.setattr(dialogo, "EndModal", lambda codigo: cierres.append(codigo))
	try:
		dialogo.campo.SetValue("algo")
		dialogo.buscar()
		assert empezada.wait(5)
		assert simular_tecla(dialogo, wx.WXK_ESCAPE).gestionado
		esperar_hasta(lambda: not dialogo.buscando, 5, "la búsqueda no se detuvo")
		assert cierres == [], "Escape durante la búsqueda no cierra el diálogo"
		assert anuncios[-1] == "Búsqueda detenida. 1 resultado"
		assert dialogo.lista.numero_filas() == 1
	finally:
		_cerrar(dialogo)


def test_buscar_error_se_muestra(app, monkeypatch):
	errores: list[str] = []
	monkeypatch.setattr(modulo_buscar, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))

	def falla(consulta, cancelado):
		raise RuntimeError("base de datos bloqueada")

	dialogo = modulo_buscar.DialogoBuscarTodo(None, falla)
	try:
		dialogo.campo.SetValue("x")
		dialogo.buscar()
		esperar_hasta(lambda: bool(errores), 5, "no se mostró el error")
		assert "base de datos bloqueada" in errores[0]
		assert not dialogo.buscando
	finally:
		_cerrar(dialogo)


# ── Plantillas ──────────────────────────────────────────────────────────────────


def test_plantillas_listar_y_usar(app, almacen):
	almacen.guardar_plantilla("Saludo", "Hola, ¿puedes revisar esto?\nGracias.")
	almacen.guardar_plantilla("Pruebas", "Ejecuta las pruebas")
	dialogo = modulo_plantillas.DialogoPlantillas(None, almacen)
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.boton_guardar_actual is None, "sin texto actual no hay botón para guardarlo"
		assert dialogo.lista.numero_filas() == 2
		titulos = sorted(dialogo.lista.texto_celda(fila, 0) for fila in range(2))
		assert titulos == ["Pruebas", "Saludo"]
		fila_saludo = [p.titulo for p in dialogo.plantillas].index("Saludo")
		assert dialogo.lista.valores_fila(fila_saludo) == ["Saludo", "Hola, ¿puedes revisar esto? Gracias.", "0"]
		assert dialogo.lista.fila_actual() == 0
		dialogo.lista.enfocar_fila(fila_saludo, anunciar=False)
		assert dialogo.usar() is True
		assert dialogo.texto_elegido == "Hola, ¿puedes revisar esto?\nGracias."
		assert dialogo.GetReturnCode() == wx.ID_OK
		assert {p.titulo: p.usos for p in almacen.plantillas()}["Saludo"] == 1
	finally:
		_cerrar(dialogo)


def test_plantillas_nueva_editar_borrar(app, almacen, monkeypatch, anuncios):
	dialogo = modulo_plantillas.DialogoPlantillas(None, almacen, texto_actual="Texto que estaba escribiendo")
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.lista.numero_filas() == 0
		assert dialogo.boton_guardar_actual is not None
		assert not dialogo.boton_editar.IsEnabled()
		propuestas: list[tuple[str, str]] = []

		def editar(titulo, texto):
			propuestas.append((titulo, texto))
			return ("Mi plantilla", texto + " y más")

		monkeypatch.setattr(dialogo, "_editar_en_dialogo", editar)
		identificador = dialogo.nueva()
		assert propuestas[0] == ("Texto que estaba escribiendo", "Texto que estaba escribiendo")
		assert [(p.id, p.titulo, p.texto) for p in almacen.plantillas()] == [
			(identificador, "Mi plantilla", "Texto que estaba escribiendo y más"),
		]
		assert dialogo.lista.numero_filas() == 1 and dialogo.lista.fila_actual() == 0
		assert anuncios[-1] == "Plantilla creada"
		# F2 edita.
		monkeypatch.setattr(dialogo, "_editar_en_dialogo", lambda titulo, texto: ("Renombrada", texto))
		assert simular_tecla(dialogo, wx.WXK_F2).gestionado
		assert almacen.plantillas()[0].titulo == "Renombrada"
		# Guardar el texto actual: solo pide el título.
		monkeypatch.setattr(modulo_plantillas, "pedir_texto", lambda padre, etiqueta, titulo, valor="", **o: "Actual")
		dialogo.guardar_actual()
		assert sorted(p.titulo for p in almacen.plantillas()) == ["Actual", "Renombrada"]
		# Supr pide confirmación peligrosa; «No» no borra.
		respuestas = [wx.ID_NO, wx.ID_YES]
		preguntas: list[dict] = []

		def confirmar(padre, mensaje, titulo="", con_cancelar=False, peligroso=False):
			preguntas.append({"mensaje": mensaje, "peligroso": peligroso})
			return respuestas.pop(0)

		monkeypatch.setattr(modulo_plantillas, "confirmar", confirmar)
		assert simular_tecla(dialogo, wx.WXK_DELETE).gestionado
		assert len(almacen.plantillas()) == 2
		assert dialogo.borrar() is True
		assert len(almacen.plantillas()) == 1
		assert all(p["peligroso"] for p in preguntas) and "¿Borrar la plantilla" in preguntas[0]["mensaje"]
		assert anuncios[-1] == "Plantilla borrada"
	finally:
		_cerrar(dialogo)


def test_dialogo_editar_plantilla(app):
	dialogo = modulo_plantillas.DialogoEditarPlantilla(None, "", "")
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.GetTitle() == "Nueva plantilla"
		assert dialogo.campo_texto.IsMultiLine()
		etiquetas = {dialogo.FindWindowById(i, dialogo).GetLabel() for i in (wx.ID_OK, wx.ID_CANCEL)}
		assert etiquetas == {"&Aceptar", "&Cancelar"}
		assert dialogo.validar() == "Escribe un título para la plantilla."
		dialogo.campo_titulo.SetValue("  Título ")
		assert dialogo.validar() == "Escribe el texto de la plantilla."
		dialogo.campo_texto.SetValue("Línea 1\nLínea 2\n")
		assert dialogo.validar() is None
		assert dialogo.valores() == ("Título", "Línea 1\nLínea 2")
	finally:
		_cerrar(dialogo)
	editar = modulo_plantillas.DialogoEditarPlantilla(None, "Hola", "Texto")
	try:
		assert editar.GetTitle() == "Editar plantilla"
	finally:
		_cerrar(editar)


# ── Grupos ──────────────────────────────────────────────────────────────────────


def test_grupos_nuevo_renombrar_borrar(app, almacen, monkeypatch, anuncios):
	almacen.crear_grupo("Trabajo")
	dialogo = modulo_grupos.DialogoGrupos(None, almacen)
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.lista.numero_filas() == 1 and dialogo.lista.texto_celda(0, 0) == "Trabajo"
		errores: list[str] = []
		monkeypatch.setattr(modulo_grupos, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
		nombres = iter(["  trabajo ", "Casa  nueva", "Ocio"])
		monkeypatch.setattr(modulo_grupos, "pedir_texto", lambda *args, **kwargs: next(nombres))
		identificador = dialogo.nuevo()
		assert errores and "Ya hay un grupo llamado" in errores[0], "no admite nombres repetidos"
		assert dict(almacen.grupos())[identificador] == "Casa nueva"
		assert dialogo.actual() == (identificador, "Casa nueva")
		# F2 renombra el seleccionado.
		assert simular_tecla(dialogo, wx.WXK_F2).gestionado
		assert dict(almacen.grupos())[identificador] == "Ocio"
		# Supr borra con confirmación peligrosa.
		peligrosos: list[bool] = []
		monkeypatch.setattr(modulo_grupos, "confirmar",
			lambda padre, mensaje, titulo="", con_cancelar=False, peligroso=False: peligrosos.append(peligroso) or wx.ID_YES)
		assert simular_tecla(dialogo, wx.WXK_DELETE).gestionado
		assert peligrosos == [True]
		assert [nombre for _id, nombre in almacen.grupos()] == ["Trabajo"]
		assert anuncios[-1] == "Grupo borrado"
		assert dialogo.cambiado is True
	finally:
		_cerrar(dialogo)


def test_elegir_grupo(app, almacen, monkeypatch):
	trabajo = almacen.crear_grupo("Trabajo")
	dialogo = modulo_grupos.DialogoElegirGrupo(None, almacen, "Añadir a un grupo")
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.GetTitle() == "Añadir a un grupo"
		assert [dialogo.lista.texto_celda(f, 0) for f in range(dialogo.lista.numero_filas())] == ["Trabajo", "Nuevo grupo..."]
		assert dialogo.lista.fila_actual() == 0
		assert dialogo.aceptar() == trabajo
	finally:
		_cerrar(dialogo)

	monkeypatch.setattr(modulo_grupos, "pedir_texto", lambda *args, **kwargs: "Personal")

	def mostrar_falso(self):
		self.lista.enfocar_fila(self.lista.numero_filas() - 1, anunciar=False)
		return wx.ID_OK if self.aceptar() is not None else wx.ID_CANCEL

	monkeypatch.setattr(modulo_grupos.DialogoElegirGrupo, "ShowModal", mostrar_falso)
	nuevo = modulo_grupos.elegir_grupo(None, almacen, "Mover a un grupo")
	assert nuevo is not None and dict(almacen.grupos())[nuevo] == "Personal"

	monkeypatch.setattr(modulo_grupos.DialogoElegirGrupo, "ShowModal", lambda self: wx.ID_CANCEL)
	assert modulo_grupos.elegir_grupo(None, almacen, "Mover a un grupo") is None


# ── Columnas ────────────────────────────────────────────────────────────────────

DISPONIBLES = [("titulo", "Título"), ("estado", "Estado"), ("agente", "Agente"), ("fecha", "Fecha")]


def _filas_columnas(dialogo) -> list[tuple[str, bool]]:
	return [(dialogo.lista.GetString(i), dialogo.lista.IsChecked(i)) for i in range(dialogo.lista.GetCount())]


def test_columnas_orden_y_mover(app, anuncios):
	dialogo = modulo_columnas.DialogoColumnas(None, DISPONIBLES, ["agente", "titulo", "inexistente"])
	try:
		_comprobar_accesibilidad(dialogo)
		assert _filas_columnas(dialogo) == [("Agente", True), ("Título", True), ("Estado", False), ("Fecha", False)]
		assert dialogo.lista.GetSelection() == 0
		assert dialogo.mover(-1) is False and anuncios[-1] == "Ya está la primera"
		assert simular_tecla(dialogo, wx.WXK_DOWN, alt=True).gestionado
		assert anuncios[-1] == "Movida a la posición 2"
		assert _filas_columnas(dialogo)[:2] == [("Título", True), ("Agente", True)]
		assert dialogo.lista.GetSelection() == 1
		dialogo.lista.SetSelection(3)
		assert simular_tecla(dialogo, wx.WXK_UP, alt=True).gestionado
		assert anuncios[-1] == "Movida a la posición 3"
		assert dialogo.marcadas() == ["titulo", "agente"]
		dialogo.lista.Check(2, True)
		assert dialogo.aceptar() is True
		assert dialogo.activas == ["titulo", "agente", "fecha"]
		assert dialogo.GetReturnCode() == wx.ID_OK
		etiquetas = {dialogo.FindWindowById(i, dialogo).GetLabel() for i in (wx.ID_OK, wx.ID_CANCEL)}
		assert etiquetas == {"&Aceptar", "&Cancelar"}
	finally:
		_cerrar(dialogo)


def test_columnas_al_menos_una_y_restablecer(app, monkeypatch):
	errores: list[str] = []
	monkeypatch.setattr(modulo_columnas, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
	dialogo = modulo_columnas.DialogoColumnas(None, DISPONIBLES, ["fecha"], predeterminadas=["titulo", "estado"])
	try:
		dialogo.lista.Check(0, False)
		assert dialogo.aceptar() is False
		assert errores == ["Marca al menos una columna."]
		assert dialogo.activas == ["fecha"], "sin aceptar no cambia"
		dialogo.restablecer()
		assert _filas_columnas(dialogo) == [("Título", True), ("Estado", True), ("Agente", False), ("Fecha", False)]
		assert dialogo.aceptar() is True and dialogo.activas == ["titulo", "estado"]
	finally:
		_cerrar(dialogo)
