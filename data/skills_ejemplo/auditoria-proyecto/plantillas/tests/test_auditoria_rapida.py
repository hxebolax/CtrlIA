"""Pruebas del detector de antipatrones (PLANTILLA del agente «auditoria-proyecto»).

Cada regla tiene al menos un ejemplo que debe detectarse y otro parecido que
no. Si ajustas una regla para quitar falsos positivos, añade aquí el caso.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

RUTA_DETECTOR = Path(__file__).resolve().parent.parent / "tools" / "auditoria_rapida.py"
_especificacion = importlib.util.spec_from_file_location("auditoria_rapida", RUTA_DETECTOR)
auditoria = importlib.util.module_from_spec(_especificacion)
sys.modules["auditoria_rapida"] = auditoria
_especificacion.loader.exec_module(auditoria)


def hallazgos(codigo: str, ruta: str = "app/modulo.py", es_app: bool = True):
	return auditoria.analizar_fuente(textwrap.dedent(codigo), ruta, es_app)


def reglas(codigo: str, ruta: str = "app/modulo.py", es_app: bool = True) -> list[str]:
	return [h.regla for h in hallazgos(codigo, ruta, es_app)]


# ── Robustez ─────────────────────────────────────────────────────────────────

def test_except_silencioso():
	codigo = """
	try:
		hacer()
	except Exception:
		pass
	"""
	resultado = hallazgos(codigo)
	assert [h.regla for h in resultado] == ["except-silencioso"]
	assert resultado[0].severidad == "media"
	assert resultado[0].linea == 4


def test_except_silencioso_con_continue_y_puntos():
	codigo = """
	for x in datos:
		try:
			hacer(x)
		except OSError:
			continue
	try:
		otra()
	except:
		...
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.severidad) for h in resultado] == [("except-silencioso", "baja"), ("except-silencioso", "media")]


def test_except_con_registro_o_esperable_no_se_marca():
	codigo = """
	import logging
	log = logging.getLogger(__name__)
	try:
		hacer()
	except Exception:
		log.debug("Sin conexión", exc_info=True)
	try:
		os.remove(ruta)
	except FileNotFoundError:
		pass
	try:
		hacer()
	except Exception:  # noqa: BLE001 - se informa en la interfaz
		pass
	"""
	assert reglas(codigo) == []


def test_except_amplio():
	codigo = """
	def leer():
		try:
			return cargar()
		except Exception:
			return None
	"""
	assert reglas(codigo) == ["except-amplio"]


def test_except_amplio_que_usa_relanza_o_registra_no_se_marca():
	codigo = """
	def a():
		try:
			return cargar()
		except Exception as error:
			mostrar_error(error)
	def b():
		try:
			return cargar()
		except Exception:
			limpiar()
			raise
	def c():
		try:
			return cargar()
		except BaseException:
			self._log.exception("Fallo")
	def d():
		try:
			return cargar()
		except ValueError:
			return None
	"""
	assert reglas(codigo) == []


def test_requests_sin_timeout():
	codigo = """
	import requests
	from urllib.request import urlopen
	requests.get(url)
	requests.post(url, data=datos, timeout=10)
	requests.get(url, **opciones)
	urlopen(url)
	urlopen(url, timeout=5)
	sesion = requests.Session()
	self.session.get(url)
	self.session.get(url, timeout=(5, 30))
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea) for h in resultado] == [
		("requests-sin-timeout", 4), ("requests-sin-timeout", 7), ("requests-sin-timeout", 10),
	]


# ── Registro ─────────────────────────────────────────────────────────────────

def test_print_solo_en_app_y_fuera_de_main():
	codigo = """
	print("hola")
	if __name__ == "__main__":
		print("uso")
	"""
	assert reglas(codigo) == ["print-en-app"]
	assert reglas(codigo, "tools/generar.py", es_app=False) == []


def test_print_en_modulo_de_linea_de_ordenes_no_se_marca():
	codigo = """
	import argparse
	print("resultado")
	"""
	assert reglas(codigo) == []
	assert reglas('print("x")\n', "app/cli.py") == []


def test_log_formato_inmediato():
	codigo = """
	import logging
	log = logging.getLogger(__name__)
	log.info(f"Descargados {n} archivos")
	log.error("Fallo en %s" % ruta)
	self._logger.warning("Aviso: {}".format(x))
	logging.getLogger(__name__).debug("a" + b)
	log.log(logging.INFO, f"{n}")
	log.info("Descargados %s archivos", n)
	log.info(f"Texto sin valores")
	catalogo.info(f"{n}")
	"""
	resultado = hallazgos(codigo)
	assert [h.linea for h in resultado if h.regla == "log-formato-inmediato"] == [4, 5, 6, 7, 8]


def test_basicconfig_en_modulo():
	codigo = """
	import logging
	logging.basicConfig(level=logging.INFO)
	"""
	assert reglas(codigo) == ["basicconfig-en-modulo"]
	assert reglas(codigo, "main.py") == []
	assert reglas("import logging\nif __name__ == '__main__':\n\tlogging.basicConfig()\n") == []


# ── Seguridad ────────────────────────────────────────────────────────────────

def test_subprocess_shell():
	codigo = """
	import subprocess as sp
	import os
	sp.run(f"ffmpeg -i {entrada}", shell=True)
	sp.run("dir", shell=True)
	sp.run(["ffmpeg", "-i", entrada], check=True)
	sp.run(orden, shell=False)
	os.system("cls")
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea, h.severidad) for h in resultado] == [
		("subprocess-shell", 4, "alta"), ("subprocess-shell", 5, "media"), ("subprocess-shell", 8, "media"),
	]


def test_eval_exec():
	codigo = """
	import ast
	valor = eval(texto)
	exec(codigo)
	seguro = ast.literal_eval(texto)
	cursor.execute("SELECT 1")
	dialogo.exec()
	"""
	assert [h.linea for h in hallazgos(codigo)] == [3, 4]


def test_pickle_y_yaml():
	codigo = """
	import pickle
	import yaml
	datos = pickle.loads(contenido)
	a = yaml.load(texto)
	b = yaml.load(texto, Loader=yaml.SafeLoader)
	c = yaml.safe_load(texto)
	d = yaml.load(texto, Loader=yaml.FullLoader)
	e = yaml.unsafe_load(texto)
	"""
	assert [(h.regla, h.linea) for h in hallazgos(codigo)] == [
		("pickle-inseguro", 4), ("yaml-inseguro", 5), ("yaml-inseguro", 8), ("yaml-inseguro", 9),
	]


def test_verify_false():
	codigo = """
	import requests, ssl
	requests.get(url, verify=False, timeout=5)
	sesion.verify = False
	contexto = ssl._create_unverified_context()
	contexto.check_hostname = False
	contexto.verify_mode = ssl.CERT_NONE
	requests.get(url, verify=True, timeout=5)
	requests.get(url, verify=ruta_ca, timeout=5)
	"""
	assert [h.linea for h in hallazgos(codigo)] == [3, 4, 5, 6, 7]


def test_secretos():
	codigo = '''
	TOKEN = "ghp_''' + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + '''"
	API_KEY = "Zx81kQ0pLm29sTq7"
	password = ""
	CLAVE_TOKEN = "access_token"
	token = obtener_token()
	api_key = "tu_api_key_aqui"
	'''
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea, h.severidad) for h in resultado] == [
		("secreto-en-codigo", 2, "alta"), ("secreto-en-codigo", 3, "media"),
	]


def test_sql_formateado():
	codigo = """
	def consultar(db, nombre, tabla, ids):
		db.execute(f"SELECT * FROM usuarios WHERE nombre = '{nombre}'")
		db.execute("SELECT * FROM usuarios WHERE id = %s" % nombre)
		db.execute(f"UPDATE {tabla} SET visto = ? WHERE id = ?", (1, 2))
		db.execute("SELECT * FROM usuarios WHERE nombre = ?", (nombre,))
		marcadores = ",".join("?" * len(ids))
		db.execute(f"DELETE FROM usuarios WHERE id IN ({marcadores})", ids)
		db.execute("DELETE FROM t WHERE id IN (%s)" % marcadores, ids)
		db.execute(f"PRAGMA user_version = {VERSION}")
		executor.execute(f"tarea {nombre}")
	"""
	resultado = hallazgos(codigo)
	assert [(h.linea, h.severidad) for h in resultado] == [(3, "alta"), (4, "media"), (5, "baja")]


def test_extraccion_tar():
	codigo = """
	import tarfile
	with tarfile.open(ruta) as tar:
		tar.extractall(destino)
	with tarfile.open(ruta) as tar:
		tar.extractall(destino, filter="data")
	"""
	assert [h.linea for h in hallazgos(codigo)] == [4]
	assert reglas("import zipfile\nzipfile.ZipFile(r).extractall(d)\n") == []


# ── Portabilidad ─────────────────────────────────────────────────────────────

def test_rutas_no_portables():
	codigo = """
	import os, tempfile
	a = os.getenv("APPDATA")
	b = os.environ["TEMP"]
	c = tempfile.gettempdir()
	d = tempfile.mkdtemp(prefix="x")
	e = tempfile.mkdtemp(prefix="x", dir=carpeta_temp)
	f = os.environ.get("LOCALAPPDATA", "")
	g = os.environ.get("PATH")
	"""
	resultado = hallazgos(codigo)
	assert [(h.linea, h.severidad) for h in resultado] == [(3, "media"), (4, "media"), (5, "media"), (6, "baja"), (8, "baja")]
	assert reglas(codigo, "tools/x.py", es_app=False) == []


def test_getcwd_y_registro():
	codigo = """
	import os, winreg
	from pathlib import Path
	base = os.getcwd()
	otra = Path.cwd()
	with winreg.CreateKey(winreg.HKEY_CURRENT_USER, clave) as k:
		winreg.SetValueEx(k, "x", 0, winreg.REG_SZ, "y")
	valor = winreg.QueryValueEx(k, "x")
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea) for h in resultado] == [("getcwd", 4), ("getcwd", 5), ("registro-windows", 6)]
	assert "2 escrituras" in resultado[-1].mensaje


def test_open_sin_encoding():
	codigo = """
	open(ruta)
	open(ruta, "w")
	open(ruta, "rb")
	open(ruta, "r", encoding="utf-8")
	open(ruta, modo)
	Path(ruta).read_text()
	Path(ruta).write_text(texto, encoding="utf-8")
	Path(ruta).write_text(texto)
	Path(ruta).read_bytes()
	"""
	assert [h.linea for h in hallazgos(codigo, es_app=False)] == [2, 3, 7, 9]


# ── wx: hilos, interfaz y accesibilidad ─────────────────────────────────────

def test_hilo_toca_wx():
	codigo = """
	import threading
	import wx

	class Ventana(wx.Frame):
		def iniciar(self):
			threading.Thread(target=self._trabajo, daemon=True).start()

		def _trabajo(self):
			datos = descargar()
			self.lista.DeleteAllItems()
			wx.CallAfter(self.estado.SetLabel, "Listo")
			wx.CallAfter(lambda: self.estado.SetLabel("Listo"))
			self._pintar(datos)

		def _pintar(self, datos):
			self.lista.Append(datos)
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea) for h in resultado] == [("hilo-toca-wx", 11), ("hilo-toca-wx", 14)]
	assert resultado[0].revisar


def test_hilo_que_usa_callafter_no_se_marca():
	codigo = """
	import threading
	import wx

	class Ventana(wx.Frame):
		def iniciar(self):
			threading.Thread(target=self._trabajo, daemon=True).start()

		def _trabajo(self):
			datos = descargar()
			wx.CallAfter(self._pintar, datos)

		def _pintar(self, datos):
			self.lista.Append(datos)
	"""
	assert reglas(codigo) == []


def test_bloqueo_de_interfaz_y_voz_duplicada():
	codigo = """
	import time, requests, wx

	class Ventana(wx.Frame):
		def __init__(self):
			super().__init__(None)
			self.Bind(wx.EVT_BUTTON, self.al_pulsar)
			self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, self.al_seleccionar)

		def al_pulsar(self, evento):
			respuesta = requests.get(url, timeout=10)
			time.sleep(2)

		def al_seleccionar(self, evento):
			hablar(self.lista.GetItemText(evento.GetIndex()))
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea) for h in resultado] == [
		("bloqueo-interfaz", 11), ("bloqueo-interfaz", 12), ("voz-duplicada", 15),
	]


def test_checklistbox_y_controles_poco_accesibles():
	codigo = """
	import wx
	import wx.grid as gridlib
	from wx.lib.agw import customtreectrl as CT

	class CasillasAccesibles(wx.CheckListBox):
		pass

	a = wx.CheckListBox(panel, choices=opciones)
	b = CasillasAccesibles(panel, choices=opciones)
	c = gridlib.Grid(panel)
	d = CT.CustomTreeCtrl(panel)
	"""
	assert [(h.regla, h.linea) for h in hallazgos(codigo)] == [
		("checklistbox", 9), ("control-poco-accesible", 11), ("control-poco-accesible", 12),
	]


def test_etiqueta_tardia():
	codigo = """
	import wx

	def crear(panel):
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.nombre = wx.TextCtrl(panel)
		sizer.Add(wx.StaticText(panel, label=_("&Nombre:")), 0)
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea) for h in resultado] == [("etiqueta-tardia", 6)]
	assert "Nombre" in resultado[0].mensaje


def test_etiqueta_en_orden_o_de_otra_ventana_no_se_marca():
	codigo = """
	import wx

	def crear(panel):
		wx.StaticText(panel, label="&Nombre:")
		self.nombre = wx.TextCtrl(panel)
		self.aviso = wx.TextCtrl(panel, style=wx.TE_READONLY)
		wx.StaticText(panel, label="&Método:")
		self.metodo = wx.RadioBox(panel, choices=["a", "b"])
		self.otra = wx.Choice(panel)
		wx.StaticText(panel, label="&Resultados:")
		self.lista = ListaAccesible(panel)
		self.campo = wx.TextCtrl(dialogo)
		wx.StaticText(panel, label="Etiqueta de otro padre:")
		self.ultimo = wx.TextCtrl(panel)
		wx.StaticText(panel, label="Texto de ayuda sin dos puntos")
	"""
	assert reglas(codigo) == []


def test_char_hook_sin_skip():
	codigo = """
	import wx

	class Ventana(wx.Frame):
		def __init__(self):
			super().__init__(None)
			self.Bind(wx.EVT_CHAR_HOOK, self.al_pulsar)

		def al_pulsar(self, evento):
			tecla = evento.GetKeyCode()
			if tecla == wx.WXK_ESCAPE:
				self.Close()
			elif tecla == wx.WXK_F5:
				self.actualizar()
	"""
	resultado = hallazgos(codigo)
	assert [(h.regla, h.linea) for h in resultado] == [("char-hook-sin-skip", 9)]


@pytest.mark.parametrize(
	"cuerpo",
	[
		# else con Skip
		"""
		if tecla == wx.WXK_ESCAPE:
			self.Close()
		elif tecla == wx.WXK_F5:
			self.actualizar()
		else:
			evento.Skip()
		""",
		# return en las teclas gestionadas y Skip al final
		"""
		if tecla == wx.WXK_ESCAPE:
			self.Close()
			return
		evento.Skip()
		""",
		# Skip al principio
		"""
		evento.Skip()
		if tecla == wx.WXK_F5:
			self.actualizar()
		""",
		# delega el evento en otra función
		"""
		if not self.atajos.procesar(evento):
			evento.Skip()
		""",
		# match con comodín que salta
		"""
		match tecla:
			case wx.WXK_ESCAPE:
				self.Close()
			case _:
				evento.Skip()
		""",
	],
)
def test_char_hook_correcto_no_se_marca(cuerpo):
	codigo = "import wx\n\nclass V(wx.Frame):\n\tdef __init__(self):\n\t\tself.Bind(wx.EVT_CHAR_HOOK, self.al_pulsar)\n\n"
	codigo += "\tdef al_pulsar(self, evento):\n\t\ttecla = evento.GetKeyCode()\n"
	codigo += textwrap.indent(textwrap.dedent(cuerpo).strip("\n"), "\t\t") + "\n"
	assert auditoria.analizar_fuente(codigo, "app/v.py") == []


# ── Empaquetado ──────────────────────────────────────────────────────────────

def test_cxfreeze():
	codigo = """
	from cx_Freeze import Executable, setup
	opciones = {"build_exe": {"optimize": 2, "packages": ["wx"]}}
	setup(name="App", options=opciones, executables=[Executable("main.py", base="gui")])
	"""
	resultado = hallazgos(codigo, "setup_cx.py", es_app=False)
	assert [(h.regla, h.linea) for h in resultado] == [("cxfreeze-optimize", 3), ("cxfreeze-msvcr", 4)]
	correcto = codigo.replace('"optimize": 2', '"optimize": 1, "include_msvcr": True')
	assert reglas(correcto, "setup_cx.py", es_app=False) == []


def test_sintaxis():
	resultado = hallazgos("def roto(:\n\tpass\n")
	assert [(h.regla, h.severidad) for h in resultado] == [("sintaxis", "alta")]


# ── Exclusiones, filtros, informes y código de salida ────────────────────────

def test_exclusion_en_la_linea():
	codigo = """
	import os
	a = os.getcwd()  # auditoria: ignorar getcwd - la herramienta se lanza desde la raíz
	b = os.getcwd()  # auditoria: ignorar print-en-app
	# auditoria: ignorar
	c = os.getcwd()
	d = os.getcwd()
	"""
	assert [h.linea for h in hallazgos(codigo)] == [4, 7]


def test_filtro_de_reglas():
	codigo = "import os\nprint(os.getcwd())\n"
	assert [h.regla for h in auditoria.analizar_fuente(codigo, "app/x.py", True, {"getcwd"})] == ["getcwd"]


@pytest.fixture
def proyecto(tmp_path):
	"""Proyecto mínimo con hallazgos de varias severidades."""
	(tmp_path / "app").mkdir()
	(tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
	(tmp_path / "app" / "servicio.py").write_text(
		"import os\nimport subprocess\n\nsubprocess.run(orden, shell=True)\nprint('hola')\nbase = os.getcwd()\n",
		encoding="utf-8",
	)
	(tmp_path / "tests").mkdir()
	(tmp_path / "tests" / "test_x.py").write_text("print('se ignora por defecto')\n", encoding="utf-8")
	(tmp_path / "venv").mkdir()
	(tmp_path / "venv" / "ajeno.py").write_text("eval(x)\n", encoding="utf-8")
	return tmp_path


def test_auditar_proyecto_omite_venv_y_pruebas(proyecto):
	resultado, total = auditoria.auditar(proyecto)
	assert total == 2
	assert [(h.archivo, h.regla) for h in resultado] == [
		("app/servicio.py", "subprocess-shell"), ("app/servicio.py", "getcwd"), ("app/servicio.py", "print-en-app"),
	]


def test_archivo_de_exclusiones(proyecto):
	(proyecto / ".auditoria-ignorar").write_text("# comentario\napp/servicio.py: print-en-app, getcwd\n", encoding="utf-8")
	resultado, _ = auditoria.auditar(proyecto)
	assert [h.regla for h in resultado] == ["subprocess-shell"]


def test_informe_markdown(proyecto):
	resultado, total = auditoria.auditar(proyecto)
	texto = auditoria.informe_markdown(resultado, total)
	assert texto.startswith("# Auditoría rápida")
	assert "## Severidad alta (1)" in texto
	assert "`app/servicio.py:4`" in texto
	assert "## Por regla" in texto
	assert "|" not in texto  # sin tablas: se lee mejor con lector de pantalla


def test_informe_texto_y_json(proyecto):
	resultado, total = auditoria.auditar(proyecto)
	texto = auditoria.informe_texto(resultado, total)
	assert "3 hallazgos en 1 de 2 archivos" in texto
	assert "app/servicio.py:5: print-en-app." in texto
	datos = json.loads(auditoria.informe_json(resultado, total))
	assert datos["archivos_analizados"] == 2
	assert datos["hallazgos"][0]["regla"] == "subprocess-shell"


def test_codigos_de_salida(proyecto, capsys):
	assert auditoria.main(["--raiz", str(proyecto)]) == 1
	assert auditoria.main(["--raiz", str(proyecto), "--fallar-desde", "alta", "--sin-reglas", "subprocess-shell"]) == 0
	assert auditoria.main(["--raiz", str(proyecto), "--reglas", "no-existe"]) == 2
	assert auditoria.main(["--opcion-inventada"]) == 2
	assert auditoria.main(["--listar-reglas"]) == 0
	assert "char-hook-sin-skip" in capsys.readouterr().out


def test_severidad_minima_y_salida_a_archivo(proyecto):
	destino = proyecto / "informe.md"
	codigo = auditoria.main(["--raiz", str(proyecto), "--markdown", "--severidad-minima", "media", "--salida", str(destino)])
	assert codigo == 1
	texto = destino.read_text(encoding="utf-8")
	assert "subprocess-shell" in texto and "print-en-app" not in texto


def test_ejecucion_como_programa(proyecto):
	proceso = subprocess.run(
		[sys.executable, str(RUTA_DETECTOR), "--raiz", str(proyecto), "--reglas", "getcwd"],
		capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False,
	)
	assert proceso.returncode == 1
	assert "app/servicio.py:6: getcwd." in proceso.stdout


def test_todas_las_reglas_documentadas():
	"""Cada regla del detector tiene su apartado en referencias/reglas-del-detector.md."""
	referencia = RUTA_DETECTOR.parents[2] / "referencias" / "reglas-del-detector.md"
	if not referencia.is_file():
		pytest.skip("La referencia solo existe en la carpeta del agente, no en el proyecto")
	texto = referencia.read_text(encoding="utf-8")
	faltan = [nombre for nombre in auditoria.REGLAS if f"`{nombre}`" not in texto]
	assert faltan == []
