"""Pruebas de «Configuración de los agentes»: catálogos, lectura y escritura segura, y la ventana.

Nunca se tocan los archivos reales del usuario: las carpetas de configuración
de cada agente se redirigen a ``tmp_path`` con sus variables de entorno
(CLAUDE_CONFIG_DIR, CODEX_HOME, GEMINI_CLI_HOME) o sustituyendo la función
que da la ruta. Los diálogos se crean sin mostrarse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.config_agentes import agentes_configurables
from app.services.config_agentes import modelo
from app.services.config_agentes.modelo import (
	BOOL,
	DICCIONARIO,
	JSON,
	LISTA,
	OPCION,
	QUITAR,
	TEXTO,
	Ajuste,
	DocumentoConfig,
	ErrorConfigAgente,
	describir,
	filas,
	validar,
)

TIPOS = {modelo.BOOL, modelo.TEXTO, modelo.ENTERO, modelo.DECIMAL, modelo.OPCION, modelo.LISTA, modelo.DICCIONARIO, modelo.JSON}

TOML_USUARIO = """# Mi configuración de Codex
model = "gpt-6"   # el que más uso
model_reasoning_effort = "high"

[projects.'C:\\Proyectos\\uno']
trust_level = "trusted"

[mcp_servers.repl]
command = "node"
args = ["repl.js"]
"""


@pytest.fixture
def carpetas(tmp_path, monkeypatch):
	"""Redirige las carpetas de configuración de todos los agentes a tmp_path."""
	claude = tmp_path / "claude"
	codex = tmp_path / "codex"
	gemini = tmp_path / "casa_gemini"
	claude.mkdir()
	codex.mkdir()
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
	monkeypatch.setenv("CODEX_HOME", str(codex))
	monkeypatch.setenv("GEMINI_CLI_HOME", str(gemini))
	from app.services.agentes import antigravity

	monkeypatch.setattr(antigravity, "ruta_ajustes_cli", lambda: tmp_path / "agy" / "settings.json")
	return {"claude": claude, "codex": codex, "gemini": gemini / ".gemini", "agy": tmp_path / "agy", "copias": tmp_path / "copias"}


# ── Catálogos ───────────────────────────────────────────────────────────────────


def test_catalogos_bien_formados():
	agentes = agentes_configurables()
	assert list(agentes) == ["claude", "codex", "gemini", "antigravity"]
	for agente in agentes.values():
		assert agente.formato in ("json", "toml")
		ids_ambito = {a.id for a in agente.ambitos}
		claves = [a.clave for a in agente.ajustes]
		assert len(claves) == len(set(claves)), f"claves repetidas en {agente.id}"
		assert agente.documentacion.startswith("https://")
		for ajuste in agente.ajustes:
			assert ajuste.tipo in TIPOS, ajuste.clave
			assert ajuste.etiqueta and ajuste.categoria, ajuste.clave
			assert set(ajuste.ambitos) <= ids_ambito, ajuste.clave
			if ajuste.tipo == OPCION:
				assert ajuste.opciones, ajuste.clave
				assert len({v for v, _e in ajuste.opciones}) == len(ajuste.opciones), ajuste.clave


def test_lo_que_importa_con_lector_de_pantalla_esta_documentado():
	agentes = agentes_configurables()
	assert agentes["claude"].ajuste("axScreenReader").tipo == BOOL
	assert agentes["gemini"].ajuste("ui.accessibility.screenReader").tipo == BOOL
	assert agentes["codex"].ajuste("tui.animations").tipo == BOOL
	assert "off" in [v for v, _e in agentes["antigravity"].ajuste("runningLightSpeed").opciones]
	# Lo peligroso lo dice.
	assert agentes["claude"].ajuste("permissions.defaultMode").riesgo
	assert agentes["codex"].ajuste("sandbox_mode").riesgo
	assert agentes["claude"].ajuste("apiKeyHelper").riesgo


def test_rutas_de_cada_ambito(carpetas, tmp_path):
	agentes = agentes_configurables()
	proyecto = tmp_path / "proyecto"
	assert agentes["claude"].ambito("usuario").ruta(None) == carpetas["claude"] / "settings.json"
	assert agentes["claude"].ambito("proyecto").ruta(proyecto) == proyecto / ".claude" / "settings.json"
	assert agentes["claude"].ambito("local").ruta(proyecto) == proyecto / ".claude" / "settings.local.json"
	assert agentes["claude"].ambito("proyecto").ruta(None) is None
	assert agentes["codex"].ambito("usuario").ruta(None) == carpetas["codex"] / "config.toml"
	assert agentes["codex"].ambito("proyecto").ruta(proyecto) == proyecto / ".codex" / "config.toml"
	assert agentes["gemini"].ambito("usuario").ruta(None) == carpetas["gemini"] / "settings.json"
	assert agentes["antigravity"].ambito("usuario").ruta(None) == carpetas["agy"] / "settings.json"


# ── Valores ─────────────────────────────────────────────────────────────────────


def test_validar_y_describir():
	opcion = Ajuste("x", "X", OPCION, "", "C", (("a", "Ana"), ("b", "Berta")))
	libre = Ajuste("x", "X", OPCION, "", "C", (("a", "Ana"),), libre=True)
	entero = Ajuste("n", "N", modelo.ENTERO, "", "C", minimo=1, maximo=10)
	assert validar(opcion, "a") is None and validar(opcion, "c") is not None
	assert validar(libre, "otro") is None and validar(libre, "  ") is not None
	assert validar(entero, 5) is None and validar(entero, 0) and validar(entero, True) and validar(entero, "5")
	assert validar(Ajuste("l", "L", LISTA, "", "C"), ["a", "b"]) is None
	assert validar(Ajuste("l", "L", LISTA, "", "C"), ["a", 1]) is not None
	assert validar(Ajuste("d", "D", DICCIONARIO, "", "C"), {"A": "1"}) is None
	assert validar(Ajuste("d", "D", DICCIONARIO, "", "C"), {"A": 1}) is not None
	assert describir(Ajuste("b", "B", BOOL, "", "C"), True) == "Activado"
	assert describir(opcion, "b") == "Berta"
	assert describir(None, ["a"]) == "1 elemento: a"
	assert describir(None, list("abcdefg")).startswith("7 elementos: a, b, c, d, e y 2 más")
	assert describir(None, {"A": "1", "B": "2"}) == "2 entradas: A, B"
	assert describir(None, "") == "(texto vacío)"


# ── Documento ───────────────────────────────────────────────────────────────────


def test_toml_conserva_comentarios_y_lo_que_no_se_toca(tmp_path):
	ruta = tmp_path / "config.toml"
	ruta.write_text(TOML_USUARIO, encoding="utf-8")
	documento = DocumentoConfig(ruta, "toml")
	assert documento.obtener(("model",)) == (True, "gpt-6")
	assert documento.obtener(("sandbox_workspace_write", "network_access")) == (False, None)
	copia = documento.guardar({"model": "gpt-7", "sandbox_workspace_write.network_access": True,
		"model_reasoning_effort": QUITAR}, tmp_path / "copias")
	texto = ruta.read_text(encoding="utf-8")
	assert "# Mi configuración de Codex" in texto and "# el que más uso" in texto
	assert 'model = "gpt-7"' in texto and "model_reasoning_effort" not in texto
	assert "[projects.'C:\\Proyectos\\uno']" in texto and 'args = ["repl.js"]' in texto
	assert "[sandbox_workspace_write]" in texto and "network_access = true" in texto
	assert copia is not None and copia.read_text(encoding="utf-8") == TOML_USUARIO
	documento.guardar({"sandbox_workspace_write.network_access": QUITAR})
	assert "[sandbox_workspace_write]" not in ruta.read_text(encoding="utf-8"), "la tabla vacía se quita"


def test_json_crea_tablas_y_conserva_lo_desconocido(tmp_path):
	ruta = tmp_path / ".claude" / "settings.json"
	documento = DocumentoConfig(ruta, "json")
	assert documento.existe is False
	documento.guardar({"permissions.defaultMode": "plan", "env.DISABLE_TELEMETRY": "1"})
	assert json.loads(ruta.read_text(encoding="utf-8")) == {"permissions": {"defaultMode": "plan"}, "env": {"DISABLE_TELEMETRY": "1"}}
	# Algo que el agente escribe mientras tanto se conserva al guardar.
	datos = json.loads(ruta.read_text(encoding="utf-8"))
	datos["claveDelAgente"] = {"x": 1}
	ruta.write_text(json.dumps(datos), encoding="utf-8")
	documento.guardar({"env.DISABLE_TELEMETRY": QUITAR})
	assert json.loads(ruta.read_text(encoding="utf-8")) == {"permissions": {"defaultMode": "plan"}, "claveDelAgente": {"x": 1}}


def test_archivo_danado_no_se_toca(tmp_path):
	ruta = tmp_path / "settings.json"
	ruta.write_text('{"model": "opus",}', encoding="utf-8")
	with pytest.raises(ErrorConfigAgente, match="línea 1"):
		DocumentoConfig(ruta, "json")
	ruta.write_text("[1, 2]", encoding="utf-8")
	with pytest.raises(ErrorConfigAgente, match="objeto JSON"):
		DocumentoConfig(ruta, "json")
	toml = tmp_path / "config.toml"
	toml.write_text("model = \n", encoding="utf-8")
	with pytest.raises(ErrorConfigAgente, match="TOML"):
		DocumentoConfig(toml, "toml")
	assert toml.read_text(encoding="utf-8") == "model = \n"


def test_copias_de_seguridad_rotan(tmp_path, monkeypatch):
	monkeypatch.setattr(modelo, "MAX_COPIAS", 3)
	ruta = tmp_path / "settings.json"
	ruta.write_text("{}", encoding="utf-8")
	documento = DocumentoConfig(ruta, "json")
	for numero in range(5):
		documento.guardar({"n": numero}, tmp_path / "copias")
	assert len(list((tmp_path / "copias").iterdir())) == 3


def test_filas_del_editor(tmp_path):
	agente = agentes_configurables()["codex"]
	ruta = tmp_path / "config.toml"
	# Claves sueltas al principio: en TOML, lo que va después de una tabla queda dentro de ella.
	ruta.write_text('profile = "viejo"\nraro = 3\n' + TOML_USUARIO + '\n[features]\njs_repl = true\n', encoding="utf-8")
	resultado = {f.clave: f for f in filas(agente, DocumentoConfig(ruta, "toml"), "usuario")}
	assert resultado["model"].existe and resultado["model"].valor == "gpt-6"
	assert not resultado["approval_policy"].existe
	assert resultado["profile"].ajuste.obsoleta, "la obsoleta aparece porque está en el archivo"
	assert "preferred_auth_method" not in resultado, "la obsoleta que no está no se lista"
	assert resultado["raro"].ajuste is None and resultado["raro"].categoria == "Otras claves del archivo"
	assert resultado["features.js_repl"].ajuste is None
	assert not any(c.startswith(("projects", "mcp_servers")) for c in resultado), "las tablas ocultas no se listan"


# ── Ventana ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def dialogo(app_wx, carpetas, monkeypatch):
	from app.ui.accesibilidad import voz
	from app.ui.dialogos import config_agentes

	dichos: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *a, **k: dichos.append(texto))
	ventana = config_agentes.DialogoConfigAgentes(None, carpeta_copias=carpetas["copias"])
	ventana.dichos = dichos
	yield ventana
	ventana.Destroy()


def _ir_a(ventana, clave: str) -> None:
	indice = next(i for i, f in enumerate(ventana._visibles) if f.clave == clave)
	ventana.lista.enfocar_fila(indice, anunciar=False)


def test_ventana_accesible_y_en_espanol(dialogo):
	import wx

	assert dialogo.GetTitle() == "Configuración de los agentes"
	for control in (dialogo.campo_agente, dialogo.campo_ambito, dialogo.campo_carpeta, dialogo.campo_archivo,
			dialogo.campo_categoria, dialogo.campo_filtro, dialogo.lista):
		assert control.GetHelpText().strip()
		hermanos = list(control.GetParent().GetChildren())
		anterior = hermanos[hermanos.index(control) - 1]
		assert isinstance(anterior, wx.StaticText) and "&" in anterior.GetLabel(), control.GetName()
	botones = [h for h in dialogo.GetChildren()[0].GetChildren() if isinstance(h, wx.Button)]
	letras = [b.GetLabel().split("&")[1][0].lower() for b in botones if "&" in b.GetLabel()]
	assert len(letras) == len(botones) and len(set(letras)) == len(letras)
	assert "No existe todavía" in dialogo.campo_archivo.GetValue()
	assert not dialogo.campo_carpeta.IsEnabled(), "en el ámbito de usuario no hace falta carpeta"


def test_ventana_cambia_guarda_y_conserva(dialogo, carpetas, monkeypatch):
	from app.ui.dialogos import config_agentes

	ruta = carpetas["claude"] / "settings.json"
	ruta.write_text(json.dumps({"autoUpdatesChannel": "stable", "otraCosa": [1, 2]}), encoding="utf-8")
	dialogo.cargar()
	assert any(f.clave == "otraCosa" for f in dialogo._filas)
	_ir_a(dialogo, "axScreenReader")
	assert dialogo.alternar() is True
	assert dialogo.lista.valores_fila(dialogo.lista.fila_actual())[1] == "Activado (sin guardar)"
	_ir_a(dialogo, "autoUpdatesChannel")
	assert dialogo.quitar() is True
	# Lo arriesgado se confirma antes de guardar.
	_ir_a(dialogo, "permissions.defaultMode")
	dialogo._poner(dialogo.actual(), "bypassPermissions")
	preguntas: list[str] = []
	monkeypatch.setattr(config_agentes, "confirmar", lambda padre, mensaje, titulo="", con_cancelar=False, peligroso=False:
		preguntas.append(mensaje) or 5104)  # wx.ID_NO
	assert dialogo.guardar() is False and "Saltarse" not in ruta.read_text(encoding="utf-8")
	assert "Modo de permisos inicial" in preguntas[0]
	monkeypatch.setattr(config_agentes, "confirmar", lambda *a, **k: 5103)  # wx.ID_YES
	assert dialogo.guardar() is True
	assert json.loads(ruta.read_text(encoding="utf-8")) == {
		"otraCosa": [1, 2], "axScreenReader": True, "permissions": {"defaultMode": "bypassPermissions"}}
	assert dialogo.cambios == {}
	assert list((carpetas["copias"] / "claude").iterdir()), "copia de seguridad hecha"
	assert "Guardado: 3 cambios" in dialogo.dichos[-1]


def test_ventana_filtra_por_categoria_y_texto(dialogo):
	total = len(dialogo._visibles)
	dialogo.campo_filtro.SetValue("lector de pantalla")
	assert [f.clave for f in dialogo._visibles] == ["axScreenReader", "prefersReducedMotion"]
	dialogo.campo_filtro.SetValue("")
	dialogo.campo_categoria.SetStringSelection("Permisos")
	dialogo.mostrar()
	assert dialogo._visibles and all(f.categoria == "Permisos" for f in dialogo._visibles)
	assert len(dialogo._visibles) < total


def test_ventana_codex_conserva_el_toml(dialogo, carpetas, monkeypatch):
	ruta = carpetas["codex"] / "config.toml"
	ruta.write_text(TOML_USUARIO, encoding="utf-8")
	dialogo.campo_agente.SetSelection(1)
	dialogo._al_cambiar_archivo(rellenar_ambitos=True)
	assert dialogo.agente.id == "codex" and dialogo.documento.existe
	_ir_a(dialogo, "tui.animations")
	dialogo.alternar()
	assert dialogo.guardar() is True
	texto = ruta.read_text(encoding="utf-8")
	assert "# el que más uso" in texto and "[tui]" in texto and "animations = true" in texto
	assert "[mcp_servers.repl]" in texto


def test_ventana_con_archivo_danado(dialogo, carpetas, monkeypatch):
	from app.ui.dialogos import config_agentes

	ruta = carpetas["claude"] / "settings.json"
	ruta.write_text("{roto", encoding="utf-8")
	dialogo.cargar()
	assert "No se puede usar" in dialogo.campo_archivo.GetValue()
	assert not dialogo.lista.IsEnabled() and not dialogo.boton_guardar.IsEnabled()
	errores: list[str] = []
	monkeypatch.setattr(config_agentes, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
	assert dialogo.guardar() is False and errores
	assert ruta.read_text(encoding="utf-8") == "{roto"


def test_ventana_ambito_de_proyecto(dialogo, tmp_path):
	proyecto = tmp_path / "proyecto"
	proyecto.mkdir()
	dialogo.campo_carpeta.SetValue(str(proyecto))
	dialogo.campo_ambito.SetSelection(2)
	dialogo._al_cambiar_archivo()
	assert dialogo.ruta() == proyecto / ".claude" / "settings.local.json"
	assert dialogo.campo_carpeta.IsEnabled()
	fila = next(f for f in dialogo._filas if f.clave == "permissions.defaultMode")
	from app.ui.dialogos.config_agentes import explicacion

	assert "Desde un proyecto no tienen efecto" in explicacion(fila, dialogo.agente, dialogo.ambito)
	dialogo.campo_carpeta.SetValue(str(tmp_path / "no_existe"))
	dialogo._al_cambiar_archivo()
	assert dialogo.documento is None and "no existe" in dialogo.campo_archivo.GetValue()


def test_cambios_sin_guardar_preguntan_al_cerrar(dialogo, monkeypatch):
	from app.ui.dialogos import config_agentes

	_ir_a(dialogo, "verbose")
	dialogo.alternar()
	monkeypatch.setattr(config_agentes, "confirmar", lambda *a, **k: 5101)  # wx.ID_CANCEL
	dialogo.cerrar()
	assert dialogo.cambios, "cancelar no descarta nada"
	monkeypatch.setattr(config_agentes, "confirmar", lambda *a, **k: 5104)  # wx.ID_NO: descartar
	dialogo.cerrar()
	assert dialogo.cambios == {}


# ── Cuadro para cambiar un valor ────────────────────────────────────────────────


def _dialogo_valor(app_wx, ajuste, valor=None, existe=False):
	from app.services.config_agentes.modelo import Fila
	from app.ui.dialogos.config_agentes import DialogoValor

	return DialogoValor(None, Fila(ajuste.clave if ajuste else "clave.rara", ajuste, existe, valor), valor, existe, "Ayuda")


def test_valor_de_cada_tipo(app_wx, monkeypatch):
	from app.ui.dialogos import config_agentes

	errores: list[str] = []
	monkeypatch.setattr(config_agentes, "mostrar_error", lambda padre, mensaje, titulo="": errores.append(mensaje))
	cuadro = _dialogo_valor(app_wx, Ajuste("a", "A", modelo.ENTERO, "", "C", minimo=1))
	try:
		cuadro.control.SetValue("cero")
		assert cuadro.aceptar() is False and "entero" in errores[-1]
		cuadro.control.SetValue("0")
		assert cuadro.aceptar() is False and "mínimo" in errores[-1]
		cuadro.control.SetValue("7")
		assert cuadro.aceptar() is True and cuadro.valor == 7
	finally:
		cuadro.Destroy()
	cuadro = _dialogo_valor(app_wx, Ajuste("m", "M", OPCION, "", "C", (("opus", "Opus"),), libre=True), "mio", True)
	try:
		assert cuadro.control.GetSelection() == 1 and cuadro.campo_otro.GetValue() == "mio"
		cuadro.control.SetSelection(0)
		assert cuadro.leer() == "opus"
	finally:
		cuadro.Destroy()
	cuadro = _dialogo_valor(app_wx, Ajuste("l", "L", LISTA, "", "C"), ["Bash(git *)"], True)
	try:
		monkeypatch.setattr(config_agentes, "pedir_texto", lambda *a, **k: "  Read(./docs/**) ")
		cuadro.anadir()
		assert cuadro.leer() == ["Bash(git *)", "Read(./docs/**)"]
		cuadro.mover(-1)
		assert cuadro.leer() == ["Read(./docs/**)", "Bash(git *)"]
		cuadro.quitar()
		assert cuadro.leer() == ["Bash(git *)"]
	finally:
		cuadro.Destroy()
	cuadro = _dialogo_valor(app_wx, Ajuste("env", "E", DICCIONARIO, "", "C"), {"A": "1"}, True)
	try:
		monkeypatch.setattr(config_agentes, "pedir_texto", lambda *a, **k: "SIN_IGUAL")
		cuadro.anadir()
		assert "NOMBRE=valor" in errores[-1]
		monkeypatch.setattr(config_agentes, "pedir_texto", lambda *a, **k: "B = dos=2")
		cuadro.anadir()
		assert cuadro.leer() == {"A": "1", "B": " dos=2"}
	finally:
		cuadro.Destroy()
	cuadro = _dialogo_valor(app_wx, None, {"x": 1}, True)
	try:
		assert json.loads(cuadro.control.GetValue()) == {"x": 1}
		cuadro.control.SetValue("{mal")
		assert cuadro.aceptar() is False and "JSON" in errores[-1]
		cuadro.control.SetValue('"texto"')
		assert cuadro.aceptar() is True and cuadro.valor == "texto"
	finally:
		cuadro.Destroy()
	cuadro = _dialogo_valor(app_wx, Ajuste("t", "T", TEXTO, "", "C"))
	try:
		cuadro.control.SetValue("spanish")
		assert cuadro.aceptar() and cuadro.valor == "spanish"
	finally:
		cuadro.Destroy()
	assert JSON == "json" and Path  # tipos usados en el módulo
