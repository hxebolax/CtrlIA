"""Listas de la sesión: filtro, ámbito y páginas nuevas (AIChat #279 y #303).

Extracción sin interfaz (errores, commits, incidencias y PR, archivos leídos,
tus mensajes y el turno de un mensaje) y el diálogo creado SIN mostrarse:
nunca ``Show``, ``ShowModal`` real, ``MainLoop`` ni ``SetFocus``. La voz se
sustituye por una lista.
"""

from __future__ import annotations

import json
import time

import pytest

from app.services import extraccion
from app.services.gestor import MensajeVista

AHORA = time.time()


def _m(rol: str, texto: str, detalle: str = "", herramienta: str = "", fecha: float = AHORA) -> MensajeVista:
	return MensajeVista(rol, texto, fecha, detalle, herramienta)


def _orden(orden: str, salida: str = "", error: bool | None = False) -> MensajeVista:
	detalle = "Orden:\n" + orden
	if error is not None:
		detalle += "\n\n{c}:\n{s}".format(c="Error" if error else "Resultado", s=salida)
	return _m("herramienta", "Ejecutar orden: " + orden.splitlines()[0], detalle, "Bash")


COMMIT_HEREDOC = "git add -A && git commit -m \"$(cat <<'EOF'\nArregla el filtro de las listas\n\nCierra #279.\nEOF\n)\""


def _conversacion(carpeta=None) -> list[MensajeVista]:
	"""Dos turnos: el primero con enlaces, órdenes y un error; el segundo con commits, lecturas e incidencias."""
	leido = "C:\\proyecto\\app\\leido.py" if carpeta is None else str(carpeta / "leido.py")
	return [
		_m("usuario", "Mira la incidencia #279 y https://github.com/TheIdeaPlace/AIChat/pull/310", fecha=AHORA - 100),
		_m("asistente", "Veo https://ejemplo.es/uno y [la guía](https://ejemplo.es/guia)."),
		_orden("git remote -v", "origin\tgit@github.com:TheIdeaPlace/AIChat.git (fetch)"),
		_orden("pytest -q", "=== FAILURES ===\nE assert 1 == 2\n1 failed in 0.2s", error=True),
		_m("error", "Claude se cerró sin responder"),
		_m("usuario", "Ahora haz el commit", fecha=AHORA - 50),
		_m("asistente", "Lo subo. Mira https://ejemplo.es/dos y la PR TheIdeaPlace/Otro#7."),
		_m("herramienta", "Leer archivo: " + leido, "file_path: " + leido + "\noffset: 12\n\nResultado:\nprint(1)", "Read"),
		_orden(COMMIT_HEREDOC, "[main 1a2b3c4] Arregla el filtro de las listas\n 2 files changed"),
		_orden("git push origin main", "To github.com:TheIdeaPlace/AIChat.git\n   1a2b3c4..5d6e7f8  main -> main"),
		_m("herramienta", "Ejecutar: Get-Content", "Ejecutar: \"C:\\WINDOWS\\powershell.exe\" -Command "
			"\"Get-Content -Path README.md -TotalCount 40\"\n\nResultado:\n# Ctrl+IA", "Ejecutar"),
	]


# ── Extracción ──────────────────────────────────────────────────────────────────


def test_rango_del_turno():
	mensajes = _conversacion()
	assert extraccion.rango_turno(mensajes, 3) == (0, 5)
	assert extraccion.rango_turno(mensajes, 0) == (0, 5)
	assert extraccion.rango_turno(mensajes, 5) == (5, len(mensajes))
	assert extraccion.rango_turno(mensajes, 99) == (0, len(mensajes))
	# Sin un mensaje tuyo delante, el turno empieza en el primero.
	sueltos = [_m("asistente", "a"), _m("asistente", "b"), _m("usuario", "c")]
	assert extraccion.rango_turno(sueltos, 1) == (0, 2)


def test_errores_de_herramientas_y_avisos():
	mensajes = _conversacion() + [
		_m("herramienta", "Editar archivo: x.py", "file_path: x.py\n\nError:\nEl usuario lo ha rechazado.", "Edit"),
		_m("herramienta", "Leer archivo: y.py", "file_path: y.py\n\nResultado:\nbien", "Read"),
	]
	fallos = extraccion.errores(mensajes)
	assert [(f.tipo, f.titulo) for f in fallos] == [
		("herramienta", "Ejecutar orden: pytest -q"), ("aviso", "Claude se cerró sin responder"),
		("herramienta", "Editar archivo: x.py"),
	]
	assert fallos[0].resumen == "1 failed in 0.2s" and fallos[0].herramienta == "Bash"
	assert "E assert 1 == 2" in fallos[0].detalle and fallos[0].entrada.startswith("Orden:")
	assert fallos[1].resumen == "" and fallos[1].indice == 4
	assert fallos[2].resumen == "El usuario lo ha rechazado."


@pytest.mark.parametrize(("orden", "esperado"), [
	(COMMIT_HEREDOC, "Arregla el filtro de las listas\n\nCierra #279."),
	("git commit -m \"Uno\" -m 'Dos'", "Uno\n\nDos"),
	("git commit -am \"Con \\\"comillas\\\"\" && git push", "Con \"comillas\""),
	("git -C C:\\repo commit --message=Corto", "Corto"),
	("git commit -m @'\nDesde PowerShell\nsegunda línea\n'@", "Desde PowerShell\nsegunda línea"),
	("git commit -F mensaje.txt", ""),
	("git commit --amend --no-edit", ""),
	("git status", ""),
])
def test_mensaje_de_git_commit(orden, esperado):
	assert extraccion.mensaje_commit(orden) == esperado


def test_commits_subidas_y_hashes_de_otras_ordenes():
	mensajes = _conversacion() + [
		_orden("git commit -m 'Nada'", "On branch main\nnothing to commit, working tree clean", error=True),
		_orden("git cherry-pick 9f8e7d6", "[main 9f8e7d6] Arreglo traído\n 1 file changed"),
		_orden("git log -1", "[main 1a2b3c4] Arregla el filtro de las listas"),  # el mismo hash no se repite
		_orden("echo [main abcdef1] no es git"),
	]
	lista = extraccion.commits(mensajes)
	assert [(c.tipo, c.hash, c.rama, c.error) for c in lista] == [
		("commit", "1a2b3c4", "main", False), ("subida", "5d6e7f8", "main", False),
		("commit", "", "", True), ("otro", "9f8e7d6", "main", False),
	]
	assert lista[0].mensaje == "Arregla el filtro de las listas\n\nCierra #279."
	assert lista[0].indice == 8 and "git commit" in lista[0].orden
	assert lista[1].mensaje == "1a2b3c4..5d6e7f8 main -> main"
	assert lista[2].mensaje == "Nada"
	assert lista[3].mensaje == "Arreglo traído"


def test_incidencias_y_pr_con_su_repositorio():
	mensajes = _conversacion() + [
		_m("asistente", "En el código no cuenta: `#55` ni\n```\n#66\n```\nPero sí #12."),
		_orden("gh pr view 310 --repo TheIdeaPlace/AIChat"),
		_orden("gh issue view 8 -R otra/cosa"),
	]
	referencias = extraccion.incidencias(mensajes)
	assert [(r.referencia, r.tipo) for r in referencias] == [
		("TheIdeaPlace/AIChat#279", ""), ("TheIdeaPlace/AIChat#310", "pr"), ("TheIdeaPlace/Otro#7", ""),
		("TheIdeaPlace/AIChat#12", ""), ("otra/cosa#8", "incidencia"),
	]
	# El #279 suelto es del repositorio del remoto de git; el enlace a la PR lleva a /pull/.
	assert referencias[0].url == "https://github.com/TheIdeaPlace/AIChat/issues/279"
	assert referencias[1].url == "https://github.com/TheIdeaPlace/AIChat/pull/310"
	assert referencias[0].indice == 0 and "Mira la incidencia #279" in referencias[0].contexto
	# Sin repositorio conocido, el #N queda sin enlace.
	sueltas = extraccion.incidencias([_m("usuario", "Mira #4, por favor")])
	assert [(r.referencia, r.url) for r in sueltas] == [("#4", "")]
	assert extraccion.incidencias([_m("usuario", "Mira #4")], "yo/repo")[0].url == "https://github.com/yo/repo/issues/4"
	# Los colores y las anclas no son incidencias.
	assert extraccion.incidencias([_m("asistente", "Ve a la sección a#b, el color &#1 o x#3")], "yo/repo") == []


def test_repositorio_de_la_carpeta(tmp_path):
	(tmp_path / ".git").mkdir()
	(tmp_path / ".git" / "config").write_text(
		"[core]\n\tbare = false\n[remote \"copia\"]\n\turl = https://github.com/yo/copia.git\n"
		"[remote \"origin\"]\n\turl = git@github.com:TheIdeaPlace/AIChat.git\n", encoding="utf-8")
	assert extraccion.repositorio_de_carpeta(str(tmp_path)) == "TheIdeaPlace/AIChat"
	# Copia de trabajo extra: «.git» es un archivo que lleva a la carpeta común.
	extra = tmp_path / "extra"
	extra.mkdir()
	(tmp_path / ".git" / "worktrees" / "extra").mkdir(parents=True)
	(tmp_path / ".git" / "worktrees" / "extra" / "commondir").write_text("../..\n", encoding="utf-8")
	(extra / ".git").write_text("gitdir: " + str(tmp_path / ".git" / "worktrees" / "extra") + "\n", encoding="utf-8")
	assert extraccion.repositorio_de_carpeta(str(extra)) == "TheIdeaPlace/AIChat"
	assert extraccion.repositorio_de_carpeta(str(tmp_path / "no_existe")) == ""
	assert extraccion.repositorio_de_carpeta("\\\\servidor\\recurso") == ""
	# Sin remotos en las órdenes, manda la carpeta.
	assert extraccion.repositorio_de_sesion([_m("usuario", "Hola")], str(tmp_path)) == "TheIdeaPlace/AIChat"


def test_archivos_leidos_con_herramientas_y_ordenes():
	acp = _m("herramienta", "Leer: Read config", "Read config. Archivo: /p/config.txt (línea 3)", "Leer")
	historial = _m("herramienta", "Leer: C:\\proyecto\\app\\leido.py", "Entrada:\n" + json.dumps({"file_path": "C:\\proyecto\\app\\leido.py"}),
		"Read")
	mensajes = _conversacion() + [
		acp, historial,
		_orden("cat docs/a.md | head -n 5 && sed -n '1,80p' app/b.py"),
		_orden("cat > nuevo.txt <<'EOF'\nhola\nEOF"),  # escribe, no lee
		_orden("type C:\\x\\c.txt; head -n 20 d.log"),
		_m("herramienta", "Leer página web: https://ejemplo.es", "url: https://ejemplo.es", "WebFetch"),
	]
	lecturas = extraccion.archivos_leidos(mensajes)
	assert [(le.ruta, le.veces, le.como) for le in lecturas] == [
		("C:\\proyecto\\app\\leido.py", 2, "Read"), ("README.md", 1, "Get-Content"), ("/p/config.txt", 1, "Leer"),
		("docs/a.md", 1, "cat"), ("app/b.py", 1, "sed"), ("C:\\x\\c.txt", 1, "type"), ("d.log", 1, "head"),
	]
	assert lecturas[0].linea == 12 and lecturas[0].indice == 12


def test_tus_mensajes():
	mensajes = [_m("usuario", "Primero", fecha=1.0), _m("asistente", "Vale"), _m("usuario", "  "), _m("usuario", "Segundo", fecha=2.0)]
	mios = extraccion.mensajes_usuario(mensajes)
	assert [(m.texto, m.indice, m.numero, m.fecha) for m in mios] == [("Primero", 0, 1, 1.0), ("Segundo", 3, 2, 2.0)]


# ── Diálogo ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def dichos(monkeypatch):
	from app.ui.accesibilidad import voz

	lista: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *args, **kwargs: lista.append(texto))
	return lista


@pytest.fixture
def documentos(monkeypatch):
	from app.ui.dialogos import listas

	vistos: list[tuple[str, str]] = []
	monkeypatch.setattr(listas, "mostrar_documento", lambda padre, titulo, texto, forzar_texto=False: vistos.append((titulo, texto)))
	return vistos


@pytest.fixture
def copiados(monkeypatch):
	from app.ui.dialogos import listas

	lista: list[str] = []
	monkeypatch.setattr(listas, "copiar_al_portapapeles", lambda texto: lista.append(texto) or True)
	return lista


def _dialogo(mensajes, **opciones):
	from app.ui.dialogos import listas

	valores = {"carpeta": "", "editor": "", "propia": False, "transcritos": [], "ambito": "sesion"}
	valores.update(opciones)
	padre = valores.pop("padre", None)
	return listas.DialogoListas(padre, mensajes, "Prueba", valores.pop("pestana", "codigo"), **valores)


def _cerrar(dialogo) -> None:
	from tests.ayudas_wx import procesar_eventos

	dialogo.Destroy()
	procesar_eventos()


def _letra(etiqueta: str) -> str:
	return etiqueta.split("&", 1)[1][0].lower()


def test_trece_paginas_con_atajos_y_letras_propias(app_wx, dichos):
	from app.ui.dialogos import listas
	from tests.ayudas_wx import simular_tecla

	dialogo = _dialogo(_conversacion())
	try:
		assert dialogo.libro.GetPageCount() == 13
		assert [dialogo.libro.GetPageText(i) for i in range(8, 13)] == [
			"Errores", "Commits", "Incidencias y PR", "Archivos leídos", "Tus mensajes",
		]
		# Ninguna letra se repite en el diálogo: etiquetas de las listas, «Mostrar», «Filtrar» y «Cerrar».
		letras = [_letra(e.GetLabel()) for e in dialogo.etiquetas.values()]
		letras += [_letra(dialogo.etiqueta_ambito.GetLabel()), _letra(dialogo.etiqueta_filtro.GetLabel()),
			_letra(dialogo.boton_cerrar.GetLabel())]
		assert len(letras) == len(set(letras)), letras
		# Cada etiqueta se crea justo antes de su control, y todos tienen ayuda (Ctrl+H).
		for etiqueta, control in ((dialogo.etiqueta_ambito, dialogo.selector_ambito), (dialogo.etiqueta_filtro, dialogo.filtro)):
			hermanos = list(control.GetParent().GetChildren())
			assert hermanos[hermanos.index(control) - 1] is etiqueta
			assert control.GetHelpText().strip()
		for clave in listas.PAGINAS:
			lista = dialogo.listas[clave]
			hermanos = list(lista.GetParent().GetChildren())
			assert hermanos[hermanos.index(lista) - 1] is dialogo.etiquetas[clave]
			assert "Intro" in lista.GetHelpText()
		# Ctrl+9 lleva a Errores y Ctrl+0 a Commits.
		assert simular_tecla(dialogo, "9", ctrl=True).gestionado
		assert dialogo.pagina_actual() == "errores"
		assert simular_tecla(dialogo, "0", ctrl=True).gestionado
		assert dialogo.pagina_actual() == "commits"
		assert dialogo.etiquetas["errores"].GetLabel() == "Err&ores (2):"
		assert dialogo.etiquetas["mios"].GetLabel() == "T&us mensajes (2):"
		cuentas = listas.contar(_conversacion())
		assert (cuentas["errores"], cuentas["commits"], cuentas["incidencias"], cuentas["leidos"], cuentas["mios"]) == (2, 2, 3, 2, 2)
	finally:
		_cerrar(dialogo)


def test_menus_de_las_paginas_nuevas(app_wx, dichos, tmp_path):
	from app.ui.dialogos import listas

	mensajes = _conversacion(tmp_path) + [_m("usuario", "Y #4")]
	dialogo = _dialogo(mensajes, carpeta=str(tmp_path))
	try:
		for numero, clave in enumerate(listas.PAGINAS[8:], start=8):
			dialogo.ir_a_pagina(numero)
			for fila in range(dialogo.listas[clave].numero_filas()):
				dialogo.listas[clave].enfocar_fila(fila, anunciar=False)
				menu = dialogo._crear_menu(clave)
				assert menu is not None, clave
				etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
				letras = [_letra(e) for e in etiquetas]
				assert len(letras) == len(set(letras)), (clave, etiquetas)
				assert all("\t" in e for e in etiquetas), etiquetas
				menu.Destroy()
		dialogo.ir_a_pagina(12)
		menu = dialogo._crear_menu("mios")
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems()]
		assert etiquetas == ["&Ir al mensaje\tIntro", "Citar en la &respuesta\tCtrl+R", "&Copiar el mensaje\tCtrl+C"]
		menu.Destroy()
	finally:
		_cerrar(dialogo)


def test_filtro_sin_mayusculas_ni_tildes(app_wx, dichos):
	from tests.ayudas_wx import simular_tecla

	mensajes = [
		_m("asistente", "```python\nprint('Canción')\n```\n```js\nconsole.log(1)\n```\n```python\nx = 'cafe'\n```"),
		_m("asistente", "Mira https://ejemplo.es/a"),
	]
	dialogo = _dialogo(mensajes)
	try:
		lista = dialogo.listas["codigo"]
		assert dialogo.filtrar("CANCION") == 1
		assert lista.valores_fila(0)[1] == "print('Canción')"
		assert dialogo.etiquetas["codigo"].GetLabel() == "&Bloques de código (1 de 3):"
		assert dialogo.filtro.GetValue() == "CANCION"
		# Varias palabras: tiene que tenerlas todas (y se mira en el código entero).
		assert dialogo.filtrar("python café") == 1
		assert dialogo.texto_cuenta() == "1 de 3"
		assert dialogo.filtrar("nada de esto") == 0
		assert dialogo.texto_cuenta() == "Nada coincide; hay 3"
		dialogo.activar()
		assert dichos[-1].startswith("Nada coincide con el filtro «nada de esto»")
		# Cada página recuerda su filtro: la de enlaces empieza sin filtro y la de código lo conserva.
		dialogo.ir_a_pagina(1)
		assert dialogo.filtro.GetValue() == "" and dialogo.listas["enlaces"].numero_filas() == 1
		dialogo.ir_a_pagina(0)
		assert dialogo.filtro.GetValue() == "nada de esto"
		# En el filtro, Escape lo vacía (y no cierra) y Ctrl+C es del cuadro de texto.
		dialogo._foco_en_filtro = lambda: True
		resultado = simular_tecla(dialogo, wx_tecla("ESCAPE"))
		assert not resultado.saltado
		assert dialogo.filtro.GetValue() == "" and lista.numero_filas() == 3
		assert dichos[-1] == "Filtro vacío. 3 elementos"
		assert dialogo.etiquetas["codigo"].GetLabel() == "&Bloques de código (3):"
		assert simular_tecla(dialogo, "c", ctrl=True).saltado
		# Con el filtro ya vacío, Escape sigue hasta el manejador común del diálogo, que lo cierra.
		cierres: list[bool] = []
		dialogo.Close = lambda *args: cierres.append(True)
		simular_tecla(dialogo, wx_tecla("ESCAPE"))
		assert cierres == [True]
	finally:
		_cerrar(dialogo)


def wx_tecla(nombre: str) -> int:
	import wx

	return getattr(wx, "WXK_" + nombre)


def test_ambito_de_este_mensaje_o_turno(app_wx, dichos):
	from tests.ayudas_wx import simular_tecla

	mensajes = _conversacion()
	# Elegido: la respuesta del primer turno (índice 1).
	dialogo = _dialogo(mensajes, indice_elegido=1)
	try:
		assert dialogo.selector_ambito.GetString(1) == "Este mensaje o turno (mensaje 2)"
		assert dialogo.listas["enlaces"].numero_filas() == 4
		assert dialogo.cambiar_ambito("turno")
		assert dialogo.selector_ambito.GetSelection() == 1
		# Enlaces: solo los de ese mensaje. Órdenes y errores: los de su turno.
		enlaces = dialogo.listas["enlaces"]
		assert [enlaces.valores_fila(i)[1] for i in range(enlaces.numero_filas())] == [
			"https://ejemplo.es/uno", "https://ejemplo.es/guia",
		]
		assert dialogo.etiquetas["enlaces"].GetLabel() == "&Enlaces (2, solo este mensaje):"
		assert dialogo.listas["ordenes"].numero_filas() == 2
		assert dialogo.etiquetas["errores"].GetLabel() == "Err&ores (2, solo este turno):"
		assert dialogo.listas["commits"].numero_filas() == 0 and dialogo.listas["mios"].numero_filas() == 1
		assert dichos[-1] == "Solo este mensaje. Bloques de código: 0 elementos"
		dialogo.ir_a_pagina(9)
		assert dichos[-1] == "El agente no ha hecho commits en este turno"
		# Alt+Intro en una página del turno lleva al mensaje con su índice en la conversación entera.
		dialogo.ir_a_pagina(3)
		dialogo.listas["ordenes"].enfocar_fila(1, anunciar=False)
		dialogo.ir_al_mensaje()
		assert dialogo.indice_mensaje == 3
		# Ctrl+T vuelve a toda la sesión.
		assert simular_tecla(dialogo, "t", ctrl=True).gestionado
		assert dialogo.ambito == "sesion" and dialogo.listas["enlaces"].numero_filas() == 4
		assert dichos[-1].startswith("Toda la sesión. Órdenes ejecutadas:")
	finally:
		_cerrar(dialogo)


def test_ambito_del_segundo_turno_recalcula_lo_agrupado(app_wx, dichos):
	mensajes = _conversacion()
	dialogo = _dialogo(mensajes, indice_elegido=8, ambito="turno")
	try:
		assert dialogo.ambito == "turno"
		commits = dialogo.listas["commits"]
		assert [commits.valores_fila(i) for i in range(commits.numero_filas())] == [
			["Arregla el filtro de las listas", "1a2b3c4", "main", "Hecho"], ["Subida a main", "5d6e7f8", "main", "Subido"],
		]
		# Las incidencias del turno son las que se citan en él; el repositorio sigue siendo el de la sesión.
		incidencias = dialogo.listas["incidencias"]
		assert [incidencias.valores_fila(i)[0] for i in range(incidencias.numero_filas())] == ["TheIdeaPlace/Otro#7"]
		assert dialogo.listas["leidos"].numero_filas() == 2
		assert dialogo.listas["errores"].numero_filas() == 0
		# Los bloques y los enlaces son los del mensaje elegido (una orden: no tiene).
		assert dialogo.listas["enlaces"].numero_filas() == 0
		dialogo.ir_a_pagina(1)
		assert dichos[-1] == "No hay enlaces en este mensaje"
	finally:
		_cerrar(dialogo)


def test_ambito_sin_mensaje_elegido(app_wx, dichos):
	dialogo = _dialogo(_conversacion(), indice_elegido=-1, ambito="turno")
	try:
		# Sin mensaje elegido, aunque se guardara «turno», se ve toda la sesión.
		assert dialogo.ambito == "sesion" and dialogo.selector_ambito.GetSelection() == 0
		assert "no hay ningún mensaje elegido" in dialogo.selector_ambito.GetString(1)
		assert dialogo.alternar_ambito() is False
		assert dichos[-1].startswith("No había ningún mensaje elegido")
		assert dialogo.ambito == "sesion"
	finally:
		_cerrar(dialogo)


def test_ambito_se_recuerda_y_sale_de_la_ventana(app_wx, dichos):
	import wx

	from app.ui.dialogos import listas

	class Ventana(wx.Frame):
		def __init__(self):
			super().__init__(None)
			self.opciones = {listas.OPCION_AMBITO: "turno"}
			self.citas: list[str] = []
			self.sesion_actual = ""

		def opcion(self, clave, defecto=None):
			return self.opciones.get(clave, defecto)

		def poner_opcion(self, clave, valor):
			self.opciones[clave] = valor

		def _indice_completo_actual(self):
			return 6

		def citar_en_respuesta(self, texto, **_opciones):
			self.citas.append(texto)
			return True

	ventana = Ventana()
	mensajes = _conversacion()
	dialogo = listas.DialogoListas(ventana, mensajes, "Prueba", "mios", carpeta="", editor="", propia=True, transcritos=[])
	try:
		# El mensaje elegido y el ámbito guardado salen de la ventana principal.
		assert dialogo.indice_elegido == 6 and dialogo.ambito == "turno"
		assert dialogo.listas["mios"].numero_filas() == 1
		assert dialogo.cambiar_ambito("sesion", anunciar=False)
		assert ventana.opciones[listas.OPCION_AMBITO] == "sesion"
		# Tus mensajes: Ctrl+R cita el elegido en la respuesta de la ventana y cierra.
		mios = dialogo.listas["mios"]
		assert mios.numero_filas() == 2
		mios.enfocar_fila(1, anunciar=False)
		from tests.ayudas_wx import simular_tecla

		assert simular_tecla(dialogo, "r", ctrl=True).gestionado
		assert ventana.citas == ["Ahora haz el commit"] and dialogo.mensaje_citado == "Ahora haz el commit"
		assert dialogo.GetReturnCode() == wx.ID_OK
	finally:
		_cerrar(dialogo)
		ventana.Destroy()


def test_tus_mensajes_intro_va_al_mensaje_y_cita_al_portapapeles(app_wx, dichos, copiados):
	from tests.ayudas_wx import simular_tecla

	dialogo = _dialogo([_m("usuario", "Hola\n\nsegunda línea"), _m("asistente", "Hola")], pestana="mios")
	try:
		mios = dialogo.listas["mios"]
		assert mios.valores_fila(0)[0] == "Hola"
		assert simular_tecla(dialogo, "c", ctrl=True).gestionado
		assert copiados[-1] == "Hola\n\nsegunda línea"
		# Una sesión que no se puede escribir: la cita va al portapapeles.
		assert dialogo.citar_mensaje() is True
		assert copiados[-1] == "> Hola\n>\n> segunda línea"
		dialogo.activar()
		assert dialogo.indice_mensaje == 0
		# Fuera de «Tus mensajes», citar no hace nada.
		dialogo.ir_a_pagina(0)
		assert dialogo.citar_mensaje() is False
	finally:
		_cerrar(dialogo)


def test_acciones_de_errores_commits_e_incidencias(app_wx, dichos, documentos, copiados, monkeypatch):
	from app.ui.dialogos import listas
	from tests.ayudas_wx import simular_tecla

	abiertos: list[str] = []
	monkeypatch.setattr(listas.webbrowser, "open", lambda url, *a, **k: abiertos.append(url) or True)
	dialogo = _dialogo(_conversacion(), pestana="errores")
	try:
		errores = dialogo.listas["errores"]
		assert errores.valores_fila(0) == ["Ejecutar orden: pytest -q", "1 failed in 0.2s", "Bash"]
		assert errores.valores_fila(1) == ["Claude se cerró sin responder", "", "Aviso de error"]
		dialogo.activar()
		titulo, texto = documentos[-1]
		assert titulo == "Error 1" and "## Error" in texto and "E assert 1 == 2" in texto and "## Lo que se pidió" in texto
		assert simular_tecla(dialogo, "c", ctrl=True, mayus=True).gestionado
		assert copiados[-1].startswith("=== FAILURES ===")
		# Commits: Ctrl+C copia el hash y Ctrl+Mayús+C el mensaje; Intro lo enseña con su orden.
		dialogo.ir_a_pagina(9)
		dialogo.copiar()
		assert copiados[-1] == "1a2b3c4"
		dialogo.copiar_contenido()
		assert copiados[-1] == "Arregla el filtro de las listas\n\nCierra #279."
		dialogo.activar()
		titulo, texto = documentos[-1]
		assert titulo == "Commit 1" and "Hash: 1a2b3c4." in texto and "git commit" in texto
		# Incidencias: Intro abre en el navegador; sin repositorio conocido, lo dice.
		dialogo.ir_a_pagina(10)
		dialogo.activar()
		assert abiertos == ["https://github.com/TheIdeaPlace/AIChat/issues/279"]
		dialogo.copiar()
		assert copiados[-1] == "https://github.com/TheIdeaPlace/AIChat/issues/279"
	finally:
		_cerrar(dialogo)
	sin_repo = _dialogo([_m("usuario", "Mira #4")], pestana="incidencias")
	try:
		assert sin_repo.listas["incidencias"].valores_fila(0) == ["#4", "Sin saber", "Mira #4"]
		sin_repo.activar()
		assert dichos[-1] == "No se sabe de qué repositorio es #4. Control+C copia la referencia."
		sin_repo.copiar()
		assert copiados[-1] == "#4"
	finally:
		_cerrar(sin_repo)


def test_archivos_leidos_se_leen_con_intro(app_wx, dichos, documentos, copiados, tmp_path):
	(tmp_path / "leido.py").write_text("print('hola')\n", encoding="utf-8")
	(tmp_path / "README.md").write_text("# Léeme\n", encoding="utf-8")
	dialogo = _dialogo(_conversacion(tmp_path), pestana="leidos", carpeta=str(tmp_path))
	try:
		leidos = dialogo.listas["leidos"]
		assert leidos.valores_fila(0) == [str(tmp_path / "leido.py"), "1", "Read"]
		assert leidos.valores_fila(1) == ["README.md", "1", "Get-Content"]
		assert dialogo.ruta_actual() == (str(tmp_path / "leido.py"), 12)
		dialogo.activar()
		assert documentos[-1][0] == "Archivo " + str(tmp_path / "leido.py") and "print('hola')" in documentos[-1][1]
		# Las rutas relativas, desde la carpeta de la sesión; Ctrl+Mayús+C copia el contenido.
		leidos.enfocar_fila(1, anunciar=False)
		assert dialogo.copiar_contenido() is True
		assert copiados[-1] == "# Léeme\n"
	finally:
		_cerrar(dialogo)


def test_paginas_nuevas_vacias_lo_dicen(app_wx, dichos):
	from app.ui.dialogos import listas

	dialogo = _dialogo([_m("asistente", "Nada")], pestana="commits")
	try:
		dialogo._anunciar_si_vacia()
		assert dichos[-1] == "El agente no ha hecho commits en esta sesión"
		for numero, esperado in ((8, "No ha fallado nada en esta sesión"), (10, "No se citan incidencias ni PR de GitHub en esta sesión"),
				(11, "El agente no ha leído archivos en esta sesión"), (12, "No hay mensajes tuyos en esta sesión")):
			dialogo.ir_a_pagina(numero)
			assert dichos[-1] == esperado
			assert dialogo._crear_menu(listas.PAGINAS[numero]) is None
	finally:
		_cerrar(dialogo)
