"""Novedades traídas de AIChat 0.1.8 y 0.1.9 (versión 1.3.1 de Ctrl+IA).

- La lista de tareas de Claude con todos los modelos (``CLAUDE_CODE_ENABLE_TODO_TOOLS``),
  con su opción en Preferencias.
- Las consolas de instalar e iniciar sesión avisan al cerrarse.
- «Archivos de memoria» no repite lo tuyo en una sesión de tu carpeta personal.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from app.services import memoria
from app.services.agentes import crear_proveedores, procesos
from app.services.agentes.base import AjustesSesion
from app.services.agentes.claude import VARIABLE_LISTA_TAREAS, ProveedorClaude, entorno_claude, variables_anadidas

# ── Lista de tareas de Claude ────────────────────────────────────────────────


def test_variable_lista_tareas_solo_si_se_pide(monkeypatch):
	monkeypatch.delenv(VARIABLE_LISTA_TAREAS, raising=False)
	assert entorno_claude(True)[VARIABLE_LISTA_TAREAS] == "1"
	assert VARIABLE_LISTA_TAREAS not in entorno_claude(False)
	assert variables_anadidas(True)[VARIABLE_LISTA_TAREAS] == "1"
	assert VARIABLE_LISTA_TAREAS not in variables_anadidas(False)
	# Sin la opción, una variable puesta por la persona sigue valiendo.
	monkeypatch.setenv(VARIABLE_LISTA_TAREAS, "1")
	assert entorno_claude(False)[VARIABLE_LISTA_TAREAS] == "1"


def test_preferencia_se_lee_en_cada_turno():
	activa = {"valor": True}
	proveedor = ProveedorClaude(orden_base=["claude"], lista_tareas=lambda: activa["valor"])
	assert proveedor.usar_lista_tareas()
	activa["valor"] = False
	assert not proveedor.usar_lista_tareas()
	assert ProveedorClaude(orden_base=["claude"]).usar_lista_tareas()  # de fábrica, activada

	def rota() -> bool:
		raise RuntimeError("configuración rota")

	assert ProveedorClaude(orden_base=["claude"], lista_tareas=rota).usar_lista_tareas()


def test_crear_proveedores_lee_la_preferencia(monkeypatch):
	from app.core import configuracion

	class _Config:
		def obtener(self, clave, defecto=None):
			return False if clave == "agentes.lista_tareas_claude" else defecto

	monkeypatch.setattr(configuracion, "_instancia", _Config(), raising=False)
	assert not crear_proveedores()["claude"].usar_lista_tareas()
	assert crear_proveedores({"lista_tareas_claude": True})["claude"].usar_lista_tareas()


def test_valor_de_fabrica_y_opcion_en_preferencias():
	from app.core.configuracion import VALORES_POR_DEFECTO
	from app.ui.dialogo_preferencias import PAGINAS

	assert VALORES_POR_DEFECTO["agentes"]["lista_tareas_claude"] is True
	claves = [opcion.clave for pagina in PAGINAS for opcion in pagina.opciones]
	assert "agentes.lista_tareas_claude" in claves


def test_turno_de_claude_lleva_la_variable(tmp_path, monkeypatch):
	"""El proceso de Claude recibe la variable cuando la opción está activada."""
	monkeypatch.delenv(VARIABLE_LISTA_TAREAS, raising=False)
	vistos = []

	class _Proceso:
		"""Se queda con el entorno y no lanza nada (iniciar falla como si faltara el programa)."""

		def __init__(self, orden, *args, entorno=None, **kwargs):
			vistos.append(dict(entorno or {}))

		def iniciar(self):
			raise OSError("sin lanzar de verdad")

	monkeypatch.setattr(procesos, "ProcesoLineas", _Proceso)
	for activa in (True, False):
		proveedor = ProveedorClaude(orden_base=[sys.executable, "-c", "pass"], lista_tareas=activa)
		conversacion = proveedor.crear_conversacion(AjustesSesion(carpeta=tmp_path), lambda e: None)
		conversacion._lanzar_turno("hola", [])
	assert vistos[0].get(VARIABLE_LISTA_TAREAS) == "1"
	assert VARIABLE_LISTA_TAREAS not in vistos[1]


# ── Consolas que avisan al cerrarse ──────────────────────────────────────────


def test_abrir_en_consola_avisa_al_terminar(monkeypatch):
	monkeypatch.setattr(procesos, "CREATE_NEW_CONSOLE", 0)
	monkeypatch.setattr(procesos, "ES_WINDOWS", False)
	terminado = threading.Event()
	codigos = []

	def al_terminar(codigo: int) -> None:
		codigos.append(codigo)
		terminado.set()

	assert procesos.abrir_en_consola([sys.executable, "-c", "raise SystemExit(3)"], al_terminar=al_terminar)
	assert terminado.wait(20)
	assert codigos == [3]


def test_abrir_en_consola_sin_programa_no_avisa():
	assert not procesos.abrir_en_consola([str(Path("no-existe-ctrlia.exe"))], al_terminar=lambda codigo: None)


# ── Archivos de memoria sin repetir ──────────────────────────────────────────


def test_memoria_en_la_carpeta_personal_no_repite(tmp_path, monkeypatch):
	"""Sesión en la carpeta personal: su .claude es la tuya y no se lista dos veces (AIChat #315)."""
	casa = tmp_path / "Casa"
	raiz = casa / ".claude"
	(raiz / "skills" / "resumir").mkdir(parents=True)
	(raiz / "skills" / "resumir" / "SKILL.md").write_text("# Resumir", encoding="utf-8")
	(raiz / "settings.json").write_text("{}", encoding="utf-8")
	monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(raiz))
	carpeta = Path(str(casa).upper()) if os.name == "nt" else casa
	archivos = [a for a in memoria.archivos_memoria(carpeta, "claude") if a.agente == memoria.CLAUDE]
	rutas = [os.path.normcase(os.path.realpath(a.ruta)) for a in archivos]
	assert len(rutas) == len(set(rutas))
	habilidades = [a for a in archivos if a.nombre == "Habilidad: resumir"]
	assert len(habilidades) == 1 and habilidades[0].ambito == memoria.GLOBAL
