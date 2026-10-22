"""Pruebas de lo nuevo en la versión 1.4.4 (lo que no tiene archivo propio).

- Abrir en la app de Claude o de Codex las sesiones creadas en Ctrl+IA.
- «Dejar que Claude use Chrome» activada de fábrica (y en la configuración ya guardada).
- Respuestas con varias citas: una frase al principio para que el agente las relacione.
- Codex guarda el modelo que sustituyó a uno retirado.
- Sonidos de compactación y de límite de uso.
- Herramientas del agente (inventario de Claude Code y de Codex).
- Agentes del catálogo en las sesiones: complemento de Claude Code y presentación para los demás.
- Estilo de las respuestas en «Nueva sesión».
- El foco al abrir una sesión no cae en un aviso «Permitido…».

Nada se muestra ni habla.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import agentes_sesion, inventario
from app.services.agentes.base import AjustesSesion, TipoEvento
from app.services.agentes.claude import ProveedorClaude
from app.services.agentes.codex import url_abrir_en_codex
from app.services.gestor import MensajeVista
from app.services.historial.claude import url_importar_en_claude

UUID = "50ea5ef5-ac4c-4c46-a628-a108693840ca"


# -- Abrir en la app del agente ----------------------------------------------------


def test_enlaces_para_abrir_en_las_apps():
	assert url_importar_en_claude(UUID) == f"claude://resume?session={UUID}"
	assert url_importar_en_claude("../../malo") is None
	assert url_importar_en_claude("") is None
	assert url_abrir_en_codex("01a15dcf-dfac-7cc0-8e8c-11b2478747f2") == "codex://threads/01a15dcf-dfac-7cc0-8e8c-11b2478747f2"
	assert url_abrir_en_codex("x y") is None


# -- Configuración -------------------------------------------------------------------


def test_chrome_queda_activado_al_migrar(tmp_path):
	from app.core import configuracion as mod

	ruta = tmp_path / "settings.json"
	ruta.write_text(json.dumps({mod.CLAVE_VERSION: 1, "agentes": {"chrome_claude": False}}), encoding="utf-8")
	config = mod.Configuracion(ruta)
	assert config.obtener("agentes.chrome_claude") is True
	assert json.loads(ruta.read_text(encoding="utf-8"))[mod.CLAVE_VERSION] == mod.VERSION_ESQUEMA
	# Si después la persona lo quita, se respeta.
	config.establecer("agentes.chrome_claude", False)
	assert mod.Configuracion(ruta).obtener("agentes.chrome_claude") is False


def test_chrome_de_fabrica():
	from app.core.configuracion import VALORES_POR_DEFECTO

	assert VALORES_POR_DEFECTO["agentes"]["chrome_claude"] is True


# -- Citas -------------------------------------------------------------------------


def test_contar_citas():
	from app.ui.vp_mensajes import contar_citas

	assert contar_citas("") == 0
	assert contar_citas("> uno\n> sigue\n\nrespuesta\n\n> dos\n\notra") == 2
	assert contar_citas("texto normal") == 0


def test_indice_al_abrir_salta_los_avisos_de_regla():
	from app.ui.vp_mensajes import indice_al_abrir

	mensajes = [MensajeVista("usuario", "hola", 1), MensajeVista("asistente", "Hecho.", 2),
		MensajeVista("sistema", "Permitido «git status», como dijiste para esta sesión.", 3)]
	assert indice_al_abrir(mensajes) == 1
	mensajes.append(MensajeVista("sistema", "Claude ha compactado la conversación.", 4))
	assert indice_al_abrir(mensajes) == 3
	assert indice_al_abrir([MensajeVista("sistema", "Permitido «ls».", 1)]) == 0


# -- Inventario -----------------------------------------------------------------------


def test_inventario_de_claude():
	datos = {"mcp_servers": [{"name": "claude-in-chrome", "status": "connected"}, {"name": "gmail", "status": "needs-auth"}],
		"plugins": [{"name": "ctrlia-agentes", "version": "1.0.0"}], "skills": ["ctrlia-agentes:pruebas", "loop"],
		"agents": ["general-purpose"], "tools": ["Bash", "Read", "mcp__gmail__buscar"]}
	resultado = inventario.desde_init_claude(datos)
	texto = inventario.describir(resultado, "Web")
	assert "Herramientas de Claude Code en «Web»" in texto
	assert "- claude-in-chrome: conectado" in texto and "- gmail: necesita que inicies sesión" in texto
	assert "- ctrlia-agentes: versión 1.0.0" in texto
	assert "Habilidades (2):" in texto and "Subagentes (1):" in texto
	assert "Herramientas (3): 2 propias del agente y 1 de servidores MCP." in texto


def test_inventario_de_codex():
	mcp = {"data": [{"name": "graft", "tools": {"a": {}, "b": {}}, "authStatus": "unsupported"},
		{"name": "malo", "tools": {}, "toolsError": "no arranca"}]}
	habilidades = {"data": [{"cwd": "x", "skills": [{"name": "pruebas", "scope": "repo", "enabled": True},
		{"name": "apagada", "scope": "user", "enabled": False}]}]}
	complementos = {"marketplaces": [{"plugins": [
		{"name": "documents", "installed": True, "enabled": True, "interface": {"displayName": "Documents",
			"shortDescription": "Create docs"}},
		{"name": "otro", "installed": False, "enabled": False}]}]}
	resultado = inventario.desde_codex(mcp, habilidades, complementos)
	assert resultado.servidores_mcp == [("graft", "2 herramientas"), ("malo", "error: no arranca")]
	assert resultado.habilidades == [("pruebas", "del proyecto")]
	assert resultado.complementos == [("Documents", "Create docs")]
	# Lo que no llegó no impide lo demás.
	assert inventario.desde_codex(None, None, None).servidores_mcp == []


def test_claude_dice_su_inventario_tras_el_inicio(tmp_path):
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda e: None)
	assert "envía un mensaje" in conversacion.inventario().nota
	conversacion._init = {"mcp_servers": [{"name": "claude-in-chrome", "status": "connected"}]}
	assert conversacion.inventario().servidores_mcp == [("claude-in-chrome", "conectado")]


# -- Agentes del catálogo en las sesiones -----------------------------------------------


@pytest.fixture
def agente_real(tmp_path, monkeypatch):
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
	carpeta = tmp_path / "agentes" / "pruebas"
	carpeta.mkdir(parents=True)
	(carpeta / "SKILL.md").write_text("---\nname: pruebas\ndescription: Escribe pruebas.\n---\nCuerpo.", encoding="utf-8")
	(carpeta / "plantilla.txt").write_text("importante", encoding="utf-8")
	return {"nombre": "Pruebas", "descripcion": "Escribe pruebas con pytest.", "carpeta": str(carpeta), "existe": True}


def test_complemento_de_claude_enlaza_y_limpiar_no_toca_el_original(agente_real):
	carpeta = agentes_sesion.preparar_complemento("claude:" + UUID, [agente_real])
	assert carpeta is not None
	plugin = json.loads((carpeta / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
	assert plugin["name"] == agentes_sesion.NOMBRE_COMPLEMENTO
	habilidad = carpeta / "skills" / "pruebas"
	assert (habilidad / "SKILL.md").is_file()
	# Volver a prepararla (otro arranque) y limpiarla no borra NUNCA la carpeta original.
	agentes_sesion.preparar_complemento("claude:" + UUID, [agente_real])
	agentes_sesion.limpiar_complemento("claude:" + UUID)
	assert not carpeta.exists()
	assert sorted(p.name for p in Path(agente_real["carpeta"]).iterdir()) == ["SKILL.md", "plantilla.txt"]


def test_complemento_sin_agentes_validos(agente_real, tmp_path):
	assert agentes_sesion.preparar_complemento("claude:x", [{"nombre": "No", "carpeta": str(tmp_path / "no")}]) is None


def test_claude_carga_los_agentes_con_plugin_dir(agente_real, tmp_path):
	ajustes = AjustesSesion(carpeta=tmp_path, id_externo=UUID)
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None)
	sin_agentes = conversacion._firma(True, True)
	conversacion.agentes = [agente_real]
	orden = conversacion.construir_orden()
	complemento = Path(orden[orden.index("--plugin-dir") + 1])
	assert (complemento / "skills" / "pruebas" / "SKILL.md").is_file()
	assert conversacion._firma(True, True) != sin_agentes
	agentes_sesion.limpiar_complemento("claude:" + UUID)


def test_presentacion_para_los_demas(agente_real):
	texto = agentes_sesion.presentacion([agente_real])
	assert texto.startswith(agentes_sesion.CABECERA_PRESENTACION)
	assert "- Pruebas: Escribe pruebas con pytest." in texto and "SKILL.md" in texto
	assert "Cuerpo." not in texto
	completo = agentes_sesion.presentacion([agente_real], con_contenido=True)
	assert "Cuerpo." in completo
	assert agentes_sesion.presentacion([]) == ""
	assert agentes_sesion.firma([agente_real]) == agentes_sesion.firma([dict(agente_real)])
	assert agentes_sesion.firma([]) == ""


# -- Estilo en «Nueva sesión» ----------------------------------------------------------------


def test_nueva_sesion_recuerda_el_estilo(app_wx, tmp_path):
	from app.core.configuracion import Configuracion
	from app.ui.dialogos.nueva_sesion import opciones_recordadas, recordar_opciones

	config = Configuracion(tmp_path / "settings.json")
	recordar_opciones(config, "claude", modelo="opus", estilo="Concise")
	assert opciones_recordadas(config, "claude") == {"modelo": "opus", "estilo": "Concise"}


# -- Codex: modelo sustituido ------------------------------------------------------------------


def test_el_modelo_sustituido_se_guarda(tmp_path):
	from app.services.almacen import Almacen
	from app.services.gestor import GestorSesiones
	from tests.test_gestor import ProveedorFalso

	almacen = Almacen(tmp_path / "prueba.db")
	gestor = GestorSesiones(almacen, {"falso": ProveedorFalso()}, lambda c, e: None, lambda clave, defecto: defecto)
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=tmp_path, modelo="gpt-viejo"))
	conversacion = gestor.conversacion(clave)
	conversacion._emitir(TipoEvento.AVISO, "El modelo ya no está.", modelo_sustituido="gpt-nuevo")
	assert almacen.sesion(clave).modelo == "gpt-nuevo"
	almacen.cerrar()


# -- En la ventana ------------------------------------------------------------------------------

wx = pytest.importorskip("wx")
from tests.test_conversacion_agente import _sesion, anuncios, ventana  # noqa: E402, F401


def test_abrir_en_la_app_una_sesion_propia_de_claude(ventana, tmp_path, monkeypatch, anuncios):
	import webbrowser

	abiertos = []
	monkeypatch.setattr(webbrowser, "open", lambda url: abiertos.append(url))
	clave = _sesion(ventana, tmp_path, "Mía")
	ventana.almacen.actualizar_sesion(clave, id_externo=UUID)
	ventana.refrescar_sesiones(True)
	ventana.fila_de(clave).id_externo = UUID
	ventana.abrir_sesion(clave, mover_foco=False)
	cerradas = []
	monkeypatch.setattr(ventana.gestor, "cerrar_conversacion", lambda c: cerradas.append(c))
	ventana.accion_abrir_en_agente()
	assert abiertos == [f"claude://resume?session={UUID}"]
	assert cerradas == [clave]  # Ctrl+IA deja de tenerla abierta: un solo programa escribe en ella
	assert "app de Claude" in anuncios[-1]


def test_al_citar_dos_veces_se_explica_al_agente(ventana, tmp_path, anuncios):
	from app.ui.vp_mensajes import NOTA_CITAS

	clave = _sesion(ventana, tmp_path)
	ventana.abrir_sesion(clave, mover_foco=False)
	ventana.respuesta.SetValue("")
	ventana.citar_en_respuesta("Primer punto.")
	assert NOTA_CITAS not in ventana.respuesta.GetValue()
	ventana.respuesta.AppendText("Mi respuesta al primero.\n\n")
	ventana.citar_en_respuesta("Segundo punto.")
	texto = ventana.respuesta.GetValue()
	assert texto.startswith(NOTA_CITAS)
	assert texto.index("> Primer punto.") < texto.index("Mi respuesta al primero.") < texto.index("> Segundo punto.")
	assert "respondes por partes" in anuncios[-1]
	ventana.citar_en_respuesta("Tercer punto.")
	assert ventana.respuesta.GetValue().count(NOTA_CITAS) == 1


def test_herramientas_del_agente_desde_el_menu(ventana, tmp_path, monkeypatch):
	from app.ui import dialogo_texto

	vistos = []
	monkeypatch.setattr(dialogo_texto, "mostrar_texto", lambda padre, titulo, etiqueta, texto, copiable=False: vistos.append(texto))
	clave = _sesion(ventana, tmp_path, "Con herramientas")
	ventana.abrir_sesion(clave, mover_foco=False)
	conversacion = ventana.gestor.conversacion(clave)
	conversacion._init = {"mcp_servers": [{"name": "claude-in-chrome", "status": "connected"}], "tools": ["Bash"]}
	ventana.accion_herramientas_agente()
	assert vistos and "claude-in-chrome: conectado" in vistos[0] and "«Con herramientas»" in vistos[0]


def test_regla_de_lectura_de_la_carpeta_de_un_agente():
	assert agentes_sesion.regla_lectura(r"E:\Proyectos\agentes\traducciones") == \
		"Read(//e/Proyectos/agentes/traducciones/**)"
	assert agentes_sesion.regla_lectura("C:\\") == ""
	assert agentes_sesion.regla_lectura("/home/ana/agente") == ""
	assert agentes_sesion.regla_lectura(r"C:\raro(1)") == ""


def test_claude_dice_que_agentes_tiene_y_puede_leerlos(agente_real, tmp_path):
	ajustes = AjustesSesion(carpeta=tmp_path, id_externo=UUID)
	conversacion = ProveedorClaude(orden_base=["claude"]).crear_conversacion(ajustes, lambda e: None)
	conversacion.agentes = [agente_real]
	orden = conversacion.construir_orden()
	indicacion = orden[orden.index("--append-system-prompt") + 1]
	assert "ctrlia-agentes:pruebas" in indicacion and "SKILL.md" in indicacion
	lectura = agentes_sesion.regla_lectura(agente_real["carpeta"])
	assert lectura and lectura in orden
	agentes_sesion.limpiar_complemento("claude:" + UUID)


def test_el_gestor_presenta_los_agentes_una_vez(agente_real, tmp_path):
	from app.services.almacen import Almacen
	from app.services.catalogo_agentes import CatalogoAgentes
	from app.services.gestor import GestorSesiones
	from tests.test_gestor import ProveedorFalso

	almacen = Almacen(tmp_path / "prueba.db")
	CatalogoAgentes(almacen).importar_carpeta(agente_real["carpeta"])
	ids = [a.id for a in almacen.agentes_catalogo()]
	proveedor = ProveedorFalso()
	gestor = GestorSesiones(almacen, {"falso": proveedor}, lambda c, e: None, lambda clave, defecto: defecto)
	clave = gestor.nueva_sesion("falso", AjustesSesion(carpeta=tmp_path, extra={"agentes": ids}))
	gestor.enviar(clave, "hola")
	gestor.enviar(clave, "otra vez")
	enviados = proveedor.conversaciones[0].enviados
	assert enviados[0].startswith(agentes_sesion.CABECERA_PRESENTACION) and enviados[0].endswith("hola")
	assert enviados[1] == "otra vez"
	# Lo guardado y lo que se ve es lo que escribió la persona.
	assert [m.texto for m in almacen.mensajes(clave) if m.rol == "usuario"][:2] == ["hola", "otra vez"]
	# Si se quitan los agentes, se le dice una vez.
	gestor.cambiar_ajustes(clave, agentes=[])
	gestor.enviar(clave, "sin agentes")
	assert enviados[2].startswith("[Ctrl+IA] Ya no hay agentes") and enviados[2].endswith("sin agentes")
	assert almacen.sesion(clave).extra["agentes"] == []
	almacen.cerrar()


# -- Agentes de ejemplo ---------------------------------------------------------------------------


def test_los_agentes_de_ejemplo_viajan_con_el_programa():
	from app.core.paths import ruta_agentes_ejemplo

	carpeta = ruta_agentes_ejemplo()
	agentes = sorted(p.name for p in carpeta.iterdir() if (p / "SKILL.md").is_file())
	assert len(agentes) >= 10 and "accesibilidad-wx" in agentes and "traducciones" in agentes
	assert not list(carpeta.rglob("__pycache__")) and not list(carpeta.rglob("*.pyc"))


def test_cargar_restaurar_y_reparar_ejemplos(tmp_path):
	import shutil

	from app.core.paths import ruta_agentes_ejemplo
	from app.services.almacen import Almacen
	from app.services.catalogo_agentes import CATEGORIA_EJEMPLOS, CatalogoAgentes

	almacen = Almacen(tmp_path / "prueba.db")
	catalogo = CatalogoAgentes(almacen)
	total = len([p for p in ruta_agentes_ejemplo().iterdir() if (p / "SKILL.md").is_file()])
	assert catalogo.cargar_ejemplos() == total
	assert catalogo.cargar_ejemplos() == 0  # no se duplican
	agentes = almacen.agentes_catalogo()
	assert {a.categoria for a in agentes} == {CATEGORIA_EJEMPLOS}
	# Cada uno dice que es de ejemplo: en el nombre y en sus notas.
	assert all(a.nombre.endswith(" (ejemplo)") and "Agente de ejemplo" in a.notas for a in agentes)
	assert any(a.nombre == "traducciones (ejemplo)" for a in agentes)
	# Quitar uno y restaurarlo.
	almacen.borrar_agente_catalogo(agentes[0].id)
	assert catalogo.cargar_ejemplos() == 1
	# El programa portable se mueve: las rutas se corrigen al arrancar.
	movida = tmp_path / "otra" / "data" / "agentes_ejemplo"
	shutil.copytree(ruta_agentes_ejemplo(), movida)
	for agente in almacen.agentes_catalogo():
		almacen.actualizar_agente_catalogo(agente.id, carpeta=str(tmp_path / "vieja" / "data" / "agentes_ejemplo" / Path(agente.carpeta).name))
	assert catalogo.reparar_ejemplos(movida) == total
	assert all(Path(a.carpeta).is_dir() for a in almacen.agentes_catalogo())
	almacen.cerrar()


def test_la_ventana_los_carga_solo_la_primera_vez(tmp_path):
	from types import SimpleNamespace

	from app.services.almacen import Almacen
	from app.ui.ventana_principal import VentanaPrincipal

	almacen = Almacen(tmp_path / "prueba.db")
	opciones = {}
	falsa = SimpleNamespace(almacen=almacen, opcion=lambda c, d=None: opciones.get(c, d),
		poner_opcion=lambda c, v: opciones.__setitem__(c, v))
	VentanaPrincipal._preparar_agentes_ejemplo(falsa)
	cargados = len(almacen.agentes_catalogo())
	assert cargados >= 10 and opciones["agentes.ejemplos_cargados"] is True
	for agente in almacen.agentes_catalogo():
		almacen.borrar_agente_catalogo(agente.id)
	VentanaPrincipal._preparar_agentes_ejemplo(falsa)
	assert almacen.agentes_catalogo() == []  # si la persona los quitó, no vuelven solos
	almacen.cerrar()
