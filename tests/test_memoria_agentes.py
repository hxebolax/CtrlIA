"""Pruebas de «Lo que el agente sabe de ti», «Agentes instalados» y las reglas de Antigravity.

- La lógica de :mod:`app.services.memoria` se prueba con HOME, USERPROFILE,
  CLAUDE_CONFIG_DIR y CODEX_HOME redirigidos a una carpeta temporal.
- Los diálogos se crean SIN mostrarse (nunca Show, ShowModal ni MainLoop): el
  usuario trabaja con un lector de pantalla en este mismo equipo.
- No se lanza nada de verdad: consolas, programas, navegador, ``os.startfile``,
  el visor y los cuadros de mensaje están sustituidos por registradores.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from app.services import memoria  # noqa: E402
from app.services.agentes import procesos  # noqa: E402
from app.services.agentes.antigravity import ProveedorAntigravity  # noqa: E402
from app.services.agentes.base import EstadoProveedor  # noqa: E402
from app.ui import visor_ayuda  # noqa: E402
from app.ui.accesibilidad import voz  # noqa: E402
from app.ui.dialogos import agentes as mod_agentes  # noqa: E402
from app.ui.dialogos import memoria as mod_memoria  # noqa: E402
from app.ui.dialogos import reglas_antigravity as mod_reglas  # noqa: E402
from tests.ayudas_wx import esperar_hasta  # noqa: E402

_INTERACTIVOS = (wx.Button, wx.TextCtrl, wx.Choice, wx.ComboBox, wx.ListCtrl, wx.CheckBox, wx.ListBox)


# ── Preparación ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app():
	"""Una ``wx.App`` para este módulo (reutiliza la de la sesión si ya existe)."""
	aplicacion = wx.App.Get() or wx.App(False)
	wx.HelpProvider.Set(wx.SimpleHelpProvider())
	return aplicacion


@pytest.fixture
def entorno(tmp_path, monkeypatch):
	"""Casa, configuración de Claude y de Codex, y programas en carpetas temporales."""
	casa = tmp_path / "casa"
	casa.mkdir()
	monkeypatch.setenv("HOME", str(casa))
	monkeypatch.setenv("USERPROFILE", str(casa))
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config_claude"))
	monkeypatch.delenv("CODEX_HOME", raising=False)
	monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
	proyecto = tmp_path / "Mi proyecto+1"
	proyecto.mkdir()
	return {"casa": casa, "claude": tmp_path / "config_claude", "proyecto": proyecto, "tmp": tmp_path}


@pytest.fixture(autouse=True)
def sin_efectos(monkeypatch):
	"""Registra en lugar de hablar, lanzar programas o abrir ventanas."""
	llamadas: dict[str, list] = {
		"voz": [], "consola": [], "lanzar": [], "web": [], "startfile": [], "visor": [],
		"mensaje": [], "error": [], "confirmar": [],
	}
	respuestas = {"confirmar": wx.ID_YES}
	monkeypatch.setattr(voz, "hablar", lambda texto, *a, **k: llamadas["voz"].append(texto))
	monkeypatch.setattr(procesos, "abrir_en_consola", lambda orden, carpeta=None, al_terminar=None: llamadas["consola"].append(list(orden)) or True)
	monkeypatch.setattr(
		procesos, "lanzar_independiente", lambda orden, carpeta=None, entorno=None: llamadas["lanzar"].append(list(orden)) or True
	)
	monkeypatch.setattr(mod_agentes.webbrowser, "open", lambda url, *a, **k: llamadas["web"].append(url) or True)
	monkeypatch.setattr(os, "startfile", lambda ruta, *a: llamadas["startfile"].append((ruta, a)), raising=False)
	monkeypatch.setattr(
		visor_ayuda, "mostrar_documento",
		lambda padre, titulo, texto, forzar_texto=False, mostrar=True: llamadas["visor"].append((titulo, texto, forzar_texto)),
	)

	def confirmar(padre, mensaje, titulo="", con_cancelar=False, peligroso=False):
		llamadas["confirmar"].append(mensaje)
		return respuestas["confirmar"]

	for modulo in (mod_agentes, mod_memoria, mod_reglas):
		monkeypatch.setattr(modulo, "mostrar_mensaje", lambda padre, mensaje, *a, **k: llamadas["mensaje"].append(mensaje))
		monkeypatch.setattr(modulo, "mostrar_error", lambda padre, mensaje, *a, **k: llamadas["error"].append(mensaje))
		monkeypatch.setattr(modulo, "confirmar", confirmar)
	llamadas["respuestas"] = respuestas  # type: ignore[assignment]
	return llamadas


def _escribir(ruta: Path, texto: str = "contenido\n") -> Path:
	ruta.parent.mkdir(parents=True, exist_ok=True)
	ruta.write_text(texto, encoding="utf-8")
	return ruta


def _descendientes(ventana: wx.Window):
	for hijo in ventana.GetChildren():
		yield hijo
		yield from _descendientes(hijo)


def _comprobar_accesibilidad(dialogo: wx.Window) -> None:
	"""Todo control interactivo con ayuda (Ctrl+H) y ningún botón «OK»."""
	sin_ayuda = [
		f"{type(c).__name__} {c.GetLabel() or c.GetName()!r}"
		for c in _descendientes(dialogo)
		if isinstance(c, _INTERACTIVOS) and not (c.GetHelpText() or "").strip()
	]
	assert not sin_ayuda, f"Controles sin ayuda: {sin_ayuda}"
	etiquetas = [c.GetLabel().replace("&", "").strip().lower() for c in _descendientes(dialogo) if isinstance(c, wx.Button)]
	assert "ok" not in etiquetas
	for control in _descendientes(dialogo):
		if isinstance(control, (wx.TextCtrl, wx.Choice, wx.ListCtrl)):
			hermanos = list(control.GetParent().GetChildren())
			anterior = hermanos[hermanos.index(control) - 1]
			assert isinstance(anterior, wx.StaticText), f"{control.GetName()} sin etiqueta justo antes"
			assert "&" in anterior.GetLabel(), f"La etiqueta {anterior.GetLabel()!r} no tiene tecla Alt"


# ── Servicio de memoria ──────────────────────────────────────────────────────


def test_solo_globales_sin_proyecto(entorno):
	archivos = memoria.archivos_memoria(None)
	assert all(a.ambito == memoria.GLOBAL for a in archivos)
	assert [a.agente for a in archivos][:2] == [memoria.CLAUDE, memoria.CLAUDE]
	claude_md = archivos[0]
	assert claude_md.nombre == "Tus instrucciones generales para Claude"
	assert claude_md.ruta == entorno["claude"] / "CLAUDE.md"
	assert not claude_md.existe and claude_md.creable
	ajustes = next(a for a in archivos if a.ruta.name == "settings.json" and a.agente == memoria.CLAUDE)
	assert ajustes.nombre == "Ajustes de Claude Code" and not ajustes.creable
	codex = next(a for a in archivos if a.agente == memoria.CODEX and a.ruta.name == "AGENTS.md")
	assert codex.ruta == entorno["casa"] / ".codex" / "AGENTS.md"
	gemini = next(a for a in archivos if a.ruta.name == "GEMINI.md")
	assert gemini.ruta == entorno["casa"] / ".gemini" / "GEMINI.md"
	assert any(a.ruta == entorno["casa"] / ".gemini" / "antigravity-cli" / "settings.json" for a in archivos)


def test_descubre_archivos_de_todos_los_agentes(entorno):
	raiz, casa, proyecto = entorno["claude"], entorno["casa"], entorno["proyecto"]
	_escribir(raiz / "CLAUDE.md")
	_escribir(raiz / "agents" / "revisor.md")
	_escribir(raiz / "skills" / "pdf" / "SKILL.md")
	_escribir(raiz / "commands" / "git" / "commit.md")
	_escribir(proyecto / "CLAUDE.md")
	_escribir(proyecto / ".claude" / "agents" / "probador.md")
	_escribir(proyecto / ".claude" / "commands" / "desplegar.md")
	_escribir(proyecto / "AGENTS.md")
	_escribir(proyecto / "GEMINI.md")
	_escribir(proyecto / ".agents" / "skills" / "web" / "SKILL.md")
	_escribir(casa / ".codex" / "config.toml", "model = 'x'\n")
	codificada = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(str(proyecto)))
	carpeta_memoria = raiz / "projects" / codificada / "memory"
	_escribir(carpeta_memoria / "notas.md")
	_escribir(carpeta_memoria / "MEMORY.md")

	archivos = memoria.archivos_memoria(proyecto, primero="claude")
	por_nombre = {(a.nombre, a.agente): a for a in archivos}
	assert por_nombre[("Tus instrucciones generales para Claude", "claude")].existe
	assert por_nombre[("Agente personalizado: revisor", "claude")].ambito == memoria.GLOBAL
	assert por_nombre[("Habilidad: pdf", "claude")].existe
	assert ("Comando: /commit", "claude") in por_nombre
	assert por_nombre[("Instrucciones de este proyecto", "claude")].existe
	assert not por_nombre[("Instrucciones de este proyecto", "claude")].creable
	assert por_nombre[("Tus instrucciones privadas de este proyecto", "claude")].creable
	assert por_nombre[("Agente personalizado: probador", "claude")].ambito == memoria.PROYECTO
	assert ("Comando: /desplegar", "claude") in por_nombre
	# .agents/skills lo leen Codex y Antigravity: sale una vez, en el primer grupo que lo lee (AIChat #305).
	assert por_nombre[("Habilidad: web", "codex")].ambito == memoria.PROYECTO
	assert ("Habilidad: web", "antigravity") not in por_nombre
	assert por_nombre[("Ajustes de Codex", "codex")].existe
	# Memoria automática: carpeta codificada en la raíz de CLAUDE_CONFIG_DIR, MEMORY.md primero.
	memorias = [a for a in archivos if a.tipo == memoria.MEMORIA]
	assert [a.ruta.name for a in memorias] == ["MEMORY.md", "notas.md"]
	assert memorias[0].nombre == "Memoria automática del proyecto"
	assert memorias[1].nombre == "Memoria automática del proyecto: notas"
	assert memoria.carpeta_memoria_claude(proyecto) == carpeta_memoria
	# AGENTS.md del proyecto lo leen varios agentes, pero sale una sola vez.
	assert sum(1 for a in archivos if a.ruta == proyecto / "AGENTS.md") == 1
	# Agrupados: primero todo Claude, luego Codex, luego Antigravity.
	grupos = [a.agente for a in archivos]
	assert grupos == sorted(grupos, key=["claude", "codex", "antigravity"].index)


def test_orden_segun_el_agente(entorno):
	proyecto = entorno["proyecto"]
	archivos = memoria.archivos_memoria(proyecto, primero="antigravity_acp")
	assert archivos[0].agente == memoria.ANTIGRAVITY
	agents_md = next(a for a in archivos if a.ruta == proyecto / "AGENTS.md")
	assert agents_md.agente == memoria.ANTIGRAVITY
	assert memoria.archivos_memoria(proyecto, primero="codex")[0].agente == memoria.CODEX
	assert memoria.grupo_de("claude_acp") == memoria.CLAUDE
	assert memoria.grupo_de("gemini") == memoria.ANTIGRAVITY
	assert memoria.grupo_de("otro") == ""
	# Sin memoria guardada todavía: una fila que no se puede crear desde aquí.
	sin_memoria = next(a for a in archivos if a.tipo == memoria.MEMORIA)
	assert not sin_memoria.existe and not sin_memoria.creable


def test_plantilla_y_creacion_sin_sobrescribir(entorno):
	proyecto = entorno["proyecto"]
	archivos = memoria.archivos_memoria(proyecto)
	global_claude = archivos[0]
	texto = memoria.plantilla_para(global_claude)
	assert texto.startswith("# Mis instrucciones para Claude")
	assert "en todos tus proyectos" in texto and "español" in texto
	agents = next(a for a in archivos if a.ruta == proyecto / "AGENTS.md")
	assert "Codex y otros agentes" in memoria.plantilla_para(agents)

	ruta = memoria.crear_desde_plantilla(global_claude)
	assert ruta.read_text(encoding="utf-8") == texto
	ruta.write_text("lo mío", encoding="utf-8")
	with pytest.raises(FileExistsError):
		memoria.crear_desde_plantilla(global_claude)
	assert ruta.read_text(encoding="utf-8") == "lo mío"
	ajustes = next(a for a in archivos if a.tipo == memoria.AJUSTES)
	with pytest.raises(ValueError):
		memoria.crear_desde_plantilla(ajustes)
	assert not ajustes.ruta.exists()


# ── Diálogo de memoria ───────────────────────────────────────────────────────


def test_dialogo_memoria_construccion(app, entorno, sin_efectos):
	_escribir(entorno["claude"] / "CLAUDE.md", "# Hola\n")
	dialogo = mod_memoria.DialogoMemoria(None, entorno["proyecto"], "claude")
	try:
		assert not dialogo.IsShown()
		assert dialogo.GetTitle() == "Lo que el agente sabe de ti"
		_comprobar_accesibilidad(dialogo)
		assert dialogo.lista.numero_filas() == len(dialogo.archivos) > 5
		assert dialogo.lista.GetColumnCount() == 6  # con «Modificado» (AIChat #173)
		assert dialogo.lista.texto_celda(0, 0) == "Tus instrucciones generales para Claude"
		assert dialogo.lista.texto_celda(0, 3) == "Sí"
		assert dialogo.GetEscapeId() == wx.ID_CANCEL
		# Fila 0 existe: se puede ver y editar, no crear.
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.actualizar_botones()
		assert dialogo.boton_ver.IsEnabled() and dialogo.boton_editar.IsEnabled()
		assert not dialogo.boton_crear.IsEnabled()
		dialogo.ver()
		titulo, texto, forzar = sin_efectos["visor"][-1]
		assert forzar is True and "# Hola" in texto and "CLAUDE.md" in titulo
		# Un JSON se muestra como bloque de código.
		ajustes = next(i for i, a in enumerate(dialogo.archivos) if a.ruta.name == "settings.json")
		_escribir(dialogo.archivos[ajustes].ruta, '{"a": 1}')
		dialogo.cargar()
		dialogo.lista.enfocar_fila(ajustes, anunciar=False)
		dialogo.ver()
		assert sin_efectos["visor"][-1][1].startswith("```")
		menu = dialogo._crear_menu()
		assert menu is not None and menu.GetMenuItemCount() == 7  # 5 acciones, separador y «Ordenar por»
		menu.Destroy()
	finally:
		dialogo.Destroy()


def test_dialogo_memoria_archivo_grande_no_se_abre(app, entorno, sin_efectos):
	ruta = _escribir(entorno["claude"] / "CLAUDE.md", "x" * (memoria.LIMITE_VER + 10))
	dialogo = mod_memoria.DialogoMemoria(None, None, "claude")
	try:
		dialogo.lista.enfocar_fila(0, anunciar=False)
		assert dialogo.seleccionado().ruta == ruta
		dialogo.ver()
		assert not sin_efectos["visor"]
		assert "1 MB" in sin_efectos["mensaje"][-1]
	finally:
		dialogo.Destroy()


def test_dialogo_memoria_crear_editar_y_carpeta(app, entorno, sin_efectos):
	proyecto = entorno["proyecto"]
	dialogo = mod_memoria.DialogoMemoria(None, proyecto, "codex")
	try:
		indice = next(i for i, a in enumerate(dialogo.archivos) if a.ruta == proyecto / "AGENTS.md")
		dialogo.lista.enfocar_fila(indice, anunciar=False)
		dialogo.actualizar_botones()
		assert dialogo.boton_crear.IsEnabled() and not dialogo.boton_ver.IsEnabled()
		# Si el usuario dice que no, no se crea nada.
		sin_efectos["respuestas"]["confirmar"] = wx.ID_NO
		dialogo.crear()
		assert not (proyecto / "AGENTS.md").exists()
		sin_efectos["respuestas"]["confirmar"] = wx.ID_YES
		dialogo.crear()
		assert (proyecto / "AGENTS.md").read_text(encoding="utf-8").startswith("# Instrucciones de este proyecto")
		assert sin_efectos["startfile"], "tras crear se abre en el editor"
		assert dialogo.seleccionado().ruta == proyecto / "AGENTS.md" and dialogo.seleccionado().existe
		assert not dialogo.boton_crear.IsEnabled()
		dialogo.abrir_carpeta()
		assert sin_efectos["lanzar"][-1] == ["explorer.exe", f"/select,{proyecto / 'AGENTS.md'}"]
	finally:
		dialogo.Destroy()

	con_editor = mod_memoria.DialogoMemoria(None, proyecto, "codex", editor="C:\\Editores\\mi_editor.exe")
	try:
		indice = next(i for i, a in enumerate(con_editor.archivos) if a.ruta == proyecto / "AGENTS.md")
		con_editor.lista.enfocar_fila(indice, anunciar=False)
		con_editor.editar()
		assert sin_efectos["lanzar"][-1] == ["C:\\Editores\\mi_editor.exe", str(proyecto / "AGENTS.md")]
	finally:
		con_editor.Destroy()


# ── Reglas de Antigravity ────────────────────────────────────────────────────


def test_regla_de_y_describir():
	assert mod_reglas.regla_de("orden", " git ") == "command(git)"
	assert mod_reglas.regla_de("regex", "^npm .*") == "command(regex:^npm .*)"
	assert mod_reglas.regla_de("mcp", " github ") == "mcp(github/*)"
	assert mod_reglas.regla_de("mcp", "github/create_issue") == "mcp(github/create_issue)"
	assert mod_reglas.describir_regla("mcp(github/*)") == "Usar todas las herramientas del servidor MCP «github»"
	assert mod_reglas.describir_regla("mcp(prueba/hora)") == "Usar la herramienta «hora» del servidor MCP «prueba»"
	assert mod_reglas.regla_de("escritura", "C:\\web") == "write_file(C:\\web)"
	assert mod_reglas.regla_de("manual", "command(ls)") == "command(ls)"
	for tipo, valor in (("orden", ""), ("regex", "(sin cerrar"), ("orden", "a\nb")):
		with pytest.raises(ValueError):
			mod_reglas.regla_de(tipo, valor)
	assert mod_reglas.describir_regla("command(git)") == "Ejecutar órdenes que empiezan por «git»"
	assert "expresión regular «^npm»" in mod_reglas.describir_regla("command(regex:^npm)")
	assert mod_reglas.describir_regla("write_file(C:\\web)") == "Escribir en «C:\\web»"
	assert mod_reglas.describir_regla("cualquier cosa") == "Regla escrita a mano"


def test_dialogo_reglas_lista_anade_y_quita(app, tmp_path, sin_efectos):
	ruta = tmp_path / "antigravity-cli" / "settings.json"
	_escribir(ruta, json.dumps({"tema": "oscuro", "permissions": {"allow": ["command(git)"]}}))
	proveedor = ProveedorAntigravity(ejecutable="agy-falso", ruta_ajustes=ruta)
	dialogo = mod_reglas.DialogoReglasAntigravity(None, proveedor)
	try:
		assert not dialogo.IsShown()
		_comprobar_accesibilidad(dialogo)
		assert dialogo.reglas == ["command(git)"]
		assert dialogo.lista.texto_celda(0, 0) == "Ejecutar órdenes que empiezan por «git»"
		assert dialogo.lista.IsEnabled() and dialogo.boton_anadir.IsEnabled() and dialogo.boton_quitar.IsEnabled()
		assert dialogo.guardar_regla("command(npm run test)")
		datos = json.loads(ruta.read_text(encoding="utf-8"))
		assert datos["permissions"]["allow"] == ["command(git)", "command(npm run test)"]
		assert datos["tema"] == "oscuro", "el resto del archivo se conserva"
		assert dialogo.seleccionada() == "command(npm run test)"
		assert not dialogo.guardar_regla("command(git)")
		assert sin_efectos["mensaje"], "avisa de que ya estaba"
		# Quitar pide confirmación; con «No» no cambia nada.
		dialogo.lista.enfocar_fila(0, anunciar=False)
		sin_efectos["respuestas"]["confirmar"] = wx.ID_NO
		dialogo.quitar()
		assert proveedor.leer_reglas() == ["command(git)", "command(npm run test)"]
		sin_efectos["respuestas"]["confirmar"] = wx.ID_YES
		dialogo.quitar()
		assert proveedor.leer_reglas() == ["command(npm run test)"]
		assert dialogo.reglas == ["command(npm run test)"]
		assert "Regla quitada" in sin_efectos["voz"]
	finally:
		dialogo.Destroy()


def test_dialogo_reglas_con_archivo_danado_no_lo_toca(app, tmp_path, sin_efectos):
	ruta = _escribir(tmp_path / "settings.json", "{esto no es json")
	original = ruta.read_bytes()
	dialogo = mod_reglas.DialogoReglasAntigravity(None, ruta_ajustes=ruta)
	try:
		assert dialogo.error
		assert dialogo.lista.numero_filas() == 0
		assert not dialogo.lista.IsEnabled()
		assert not dialogo.boton_anadir.IsEnabled() and not dialogo.boton_quitar.IsEnabled()
		assert dialogo._crear_menu() is None
		dialogo.anadir()
		assert sin_efectos["error"], "muestra el error en lugar de pedir una regla"
		assert not dialogo.guardar_regla("command(git)")
		assert ruta.read_bytes() == original
	finally:
		dialogo.Destroy()


def test_dialogo_nueva_regla(app, sin_efectos):
	dialogo = mod_reglas.DialogoNuevaRegla(None)
	try:
		_comprobar_accesibilidad(dialogo)
		assert dialogo.tipo.GetCount() == len(mod_reglas.TIPOS_REGLA)
		assert dialogo.etiqueta_valor.GetLabel() == "&Principio de la orden:"
		dialogo.tipo.SetSelection(2)
		dialogo.al_cambiar_tipo()
		assert dialogo.etiqueta_valor.GetLabel() == "Carpeta o archi&vo:"
		assert "write_file" in dialogo.explicacion.GetValue()
		assert "write_file" in dialogo.valor.GetHelpText()
		dialogo.valor.SetValue("C:\\Proyectos\\web")
		assert dialogo.regla() == "write_file(C:\\Proyectos\\web)"
		dialogo.tipo.SetSelection(1)
		dialogo.al_cambiar_tipo()
		dialogo.valor.SetValue("(roto")
		with pytest.raises(ValueError):
			dialogo.regla()
	finally:
		dialogo.Destroy()


# ── Agentes instalados ───────────────────────────────────────────────────────


class _ProveedorFalso:
	def __init__(self, identificador: str, nombre: str, orden: list[str] | None = None) -> None:
		self.id = identificador
		self.nombre = nombre
		self.descripcion = f"Descripción de {nombre}."
		self.web = f"https://ejemplo.invalid/{identificador}"
		self._orden = orden

	def instrucciones_instalacion(self) -> str:
		return f"Pasos para instalar {self.nombre}."

	def orden_instalacion(self) -> list[str] | None:
		return self._orden

	def orden_inicio_sesion(self) -> list[str] | None:
		return ["programa-falso", "login"]


class _GestorFalso:
	def __init__(self, proveedores: dict) -> None:
		self.proveedores = proveedores
		self.estados = {
			"claude": EstadoProveedor(True, ruta="C:\\claude.exe", version="2.1.0", mensaje="Listo.", sesion_iniciada=True, cuenta="ana@ejemplo.invalid"),
			"antigravity": EstadoProveedor(False, mensaje="Antigravity CLI no está instalado."),
		}

	def detectar(self, identificador: str):
		return self.estados.get(identificador)


@pytest.fixture
def gestor_falso(tmp_path):
	antigravity = ProveedorAntigravity(ejecutable="agy-falso", ruta_ajustes=tmp_path / "settings.json")
	proveedores = {
		"claude": _ProveedorFalso("claude", "Claude Code", ["instalar-claude"]),
		"antigravity": antigravity,
		"codex": _ProveedorFalso("codex", "Codex", None),
	}
	return _GestorFalso(proveedores)


def test_dialogo_agentes_rellena_filas(app, entorno, gestor_falso, sin_efectos):
	dialogo = mod_agentes.DialogoAgentes(None, gestor_falso, comprobar_al_abrir=False)
	try:
		assert not dialogo.IsShown()
		assert dialogo.GetTitle() == "Agentes instalados"
		_comprobar_accesibilidad(dialogo)
		assert dialogo.lista.numero_filas() == 3
		assert dialogo.lista.texto_celda(0, 1) == "Sin comprobar"
		# Lo que hace el hilo, llamado directamente.
		dialogo.comprobando = True
		dialogo.pendientes = set(dialogo.ids)
		dialogo.rellenar_filas()
		assert [dialogo.lista.texto_celda(i, 1) for i in range(3)] == ["Comprobando…"] * 3
		assert not dialogo.boton_comprobar.IsEnabled()
		for identificador in dialogo.ids:
			dialogo.poner_estado(identificador, gestor_falso.detectar(identificador))
		dialogo.terminar_comprobacion()
		assert dialogo.lista.valores_fila(0) == ["Claude Code", "Instalado", "2.1.0", "Iniciada: ana@ejemplo.invalid", "Listo."]
		assert dialogo.lista.valores_fila(1)[:4] == ["Antigravity (Google)", "No instalado", "", "Desconocida"]
		assert dialogo.lista.texto_celda(2, 1) == "No se pudo comprobar"
		assert sin_efectos["voz"][-1].startswith("Comprobación terminada")
		assert dialogo.boton_comprobar.IsEnabled()
		# Descripción del agente seleccionado.
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.al_cambiar_fila()
		texto = dialogo.descripcion.GetValue()
		assert "Descripción de Claude Code." in texto and "Pasos para instalar" in texto
		assert "https://ejemplo.invalid/claude" in texto
		assert dialogo.boton_sesion.IsEnabled() and not dialogo.boton_instalar.IsEnabled()
		# Un resultado de una comprobación anterior se descarta.
		dialogo._si_vigente(dialogo._generacion - 1, dialogo.poner_estado, "claude", None)
		assert dialogo.lista.texto_celda(0, 1) == "Instalado"
		# El recorrido del hilo, ejecutado aquí mismo: entrega con wx.CallAfter.
		gestor_falso.estados["codex"] = EstadoProveedor(True, version="0.9")
		dialogo.comprobando = True
		dialogo._generacion += 1
		mod_agentes._detectar_todos(dialogo, gestor_falso, dialogo._generacion, list(dialogo.ids))
		esperar_hasta(lambda: not dialogo.comprobando, 3.0, "la detección no terminó")
		assert dialogo.lista.valores_fila(2)[:3] == ["Codex", "Instalado", "0.9"]
	finally:
		dialogo.Destroy()


def test_dialogo_agentes_acciones(app, entorno, gestor_falso, sin_efectos):
	dialogo = mod_agentes.DialogoAgentes(None, gestor_falso, comprobar_al_abrir=False)
	try:
		for identificador in dialogo.ids:
			dialogo.poner_estado(identificador, gestor_falso.detectar(identificador))
		dialogo.terminar_comprobacion()
		# Antigravity: no instalado → Instalar, con confirmación y consola nueva.
		dialogo.lista.enfocar_fila(1, anunciar=False)
		dialogo.al_cambiar_fila()
		assert dialogo.boton_instalar.IsEnabled() and not dialogo.boton_sesion.IsEnabled()
		sin_efectos["respuestas"]["confirmar"] = wx.ID_NO
		dialogo.instalar()
		assert not sin_efectos["consola"]
		sin_efectos["respuestas"]["confirmar"] = wx.ID_YES
		dialogo.instalar()
		assert "lo comprobará de nuevo solo" in sin_efectos["confirmar"][-1]
		assert sin_efectos["consola"][-1][0] == "powershell.exe"
		menu = dialogo.crear_menu()
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		menu.Destroy()
		assert "&Reglas de permisos…" in etiquetas and "Abrir Antigravity I&DE" in etiquetas
		# Ni IDE ni app instalados (LOCALAPPDATA temporal): mensaje claro, nada se lanza.
		dialogo.abrir_ide()
		dialogo.abrir_app()
		assert not sin_efectos["lanzar"]
		assert "IDE de Antigravity no está instalado" in sin_efectos["mensaje"][-2]
		assert "app de escritorio de Antigravity no está instalada" in sin_efectos["mensaje"][-1]
		dialogo.abrir_web()
		assert sin_efectos["web"] == ["https://antigravity.google/docs/cli"]
		# Claude: instalado → Iniciar sesión en una consola.
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.iniciar_sesion()
		assert sin_efectos["consola"][-1] == ["programa-falso", "login"]
		menu = dialogo.crear_menu()
		etiquetas = [item.GetItemLabel() for item in menu.GetMenuItems() if not item.IsSeparator()]
		menu.Destroy()
		assert "&Reglas de permisos…" not in etiquetas
		# Codex sin orden de instalación: explica cómo hacerlo, sin consola.
		consolas = len(sin_efectos["consola"])
		dialogo.lista.enfocar_fila(2, anunciar=False)
		dialogo.instalar()
		assert len(sin_efectos["consola"]) == consolas
		assert "Pasos para instalar Codex." in sin_efectos["mensaje"][-1]
	finally:
		dialogo.Destroy()


def test_dialogo_agentes_comprueba_solo_al_cerrar_la_consola(app, entorno, gestor_falso, sin_efectos, monkeypatch):
	"""Como en AIChat 0.1.8: al cerrar la consola de iniciar sesión se vuelve a comprobar y se dice cómo quedó."""
	avisos_fin = []
	monkeypatch.setattr(procesos, "abrir_en_consola",
		lambda orden, carpeta=None, al_terminar=None: avisos_fin.append(al_terminar) or True)
	dialogo = mod_agentes.DialogoAgentes(None, gestor_falso, comprobar_al_abrir=False)
	try:
		for identificador in dialogo.ids:
			dialogo.poner_estado(identificador, gestor_falso.detectar(identificador))
		dialogo.terminar_comprobacion()
		dialogo.lista.enfocar_fila(0, anunciar=False)
		dialogo.iniciar_sesion()
		assert "compruebo si la sesión quedó iniciada" in sin_efectos["voz"][-1]
		assert callable(avisos_fin[-1])
		avisos_fin[-1](0)  # la consola se ha cerrado (llega desde otro hilo con wx.CallAfter)
		esperado = "Claude Code: sesión iniciada con ana@ejemplo.invalid. Listo."
		esperar_hasta(lambda: sin_efectos["voz"][-1] == esperado, 5.0, "no volvió a comprobar ni lo dijo")
		assert not dialogo.comprobando and dialogo._anunciar_sesion is None
		gestor_falso.estados["claude"] = EstadoProveedor(True, version="2.1.0", sesion_iniciada=False)
		dialogo.poner_estado("claude", gestor_falso.detectar("claude"))
		assert "la sesión no está iniciada" in dialogo.texto_tras_consola("claude")
	finally:
		dialogo.Destroy()
