"""Ejemplos de pruebas para una aplicación wxPython (PLANTILLA del agente «pruebas»).

Cada bloque muestra UNA técnica. El «código de la aplicación» está aquí mismo
para que el archivo funcione solo; en un proyecto real vive en ``app/`` y la
prueba lo importa. Copia la técnica que necesites y borra el resto.

Técnicas:

1. Lógica pura, sin wx (la mayoría de las pruebas deberían ser así).
2. Un diálogo: valores, validación, Aceptar y Cancelar.
3. Un ListCtrl: contenido, columnas y evento de selección.
4. Un hilo que devuelve el resultado con ``wx.CallAfter``.
5. Un atajo de teclado en ``EVT_CHAR_HOOK`` que no bloquea el resto de teclas.
6. Un error que debe quedar registrado (``caplog``).
7. Una ruta portable dentro de ``data/``.
8. Reproducir un fallo con una prueba ANTES de corregirlo.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

from tests.ayudas_wx import esperar_hasta, simular_tecla  # noqa: E402

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# 1. Lógica pura
# ════════════════════════════════════════════════════════════════════════════

def formatear_tamano(num_bytes: int) -> str:
	"""Tamaño legible en español («1,5 MB»). En el proyecto: app/services/..."""
	if num_bytes < 0:
		raise ValueError("El tamaño no puede ser negativo")
	unidades = ["B", "KB", "MB", "GB", "TB"]
	valor = float(num_bytes)
	for unidad in unidades:
		if valor < 1024 or unidad == unidades[-1]:
			texto = f"{valor:.1f}".rstrip("0").rstrip(".") if unidad != "B" else str(int(valor))
			return f"{texto.replace('.', ',')} {unidad}"
		valor /= 1024
	raise AssertionError("inalcanzable")


@pytest.mark.parametrize(
	("entrada", "esperado"),
	[(0, "0 B"), (1023, "1023 B"), (1024, "1 KB"), (1536, "1,5 KB"), (5 * 1024**3, "5 GB")],
)
def test_formatear_tamano(entrada, esperado):
	assert formatear_tamano(entrada) == esperado


def test_formatear_tamano_rechaza_negativos():
	with pytest.raises(ValueError, match="negativo"):
		formatear_tamano(-1)


# ════════════════════════════════════════════════════════════════════════════
# 2. Un diálogo
# ════════════════════════════════════════════════════════════════════════════

class DialogoNombre(wx.Dialog):
	"""Pide un nombre. La etiqueta se crea ANTES del campo (así la lee el lector)."""

	def __init__(self, padre, valor: str = "") -> None:
		super().__init__(padre, title="Nombre del perfil")
		wx.StaticText(self, label="&Nombre:")
		self.campo = wx.TextCtrl(self, value=valor)
		botones = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
		caja = wx.BoxSizer(wx.VERTICAL)
		caja.Add(self.campo, 0, wx.EXPAND | wx.ALL, 8)
		caja.Add(botones, 0, wx.ALL, 8)
		self.SetSizerAndFit(caja)

	def valor(self) -> str:
		return self.campo.GetValue().strip()

	def es_valido(self) -> bool:
		return bool(self.valor())


def pedir_nombre(padre) -> str | None:
	"""Muestra el diálogo y devuelve el nombre, o None si se cancela."""
	dialogo = DialogoNombre(padre)
	try:
		if dialogo.ShowModal() != wx.ID_OK or not dialogo.es_valido():
			return None
		return dialogo.valor()
	finally:
		dialogo.Destroy()


def test_dialogo_sin_mostrarlo(marco):
	"""La lógica del diálogo se prueba creándolo, sin ShowModal."""
	dialogo = DialogoNombre(marco, valor="  Ana  ")
	assert dialogo.valor() == "Ana"
	dialogo.campo.SetValue("   ")
	assert not dialogo.es_valido()
	assert dialogo.FindWindow(wx.ID_CANCEL) is not None


def test_pedir_nombre_aceptar(marco, dialogo_modal):
	# La respuesta puede ser una función: rellena el campo y «pulsa» Aceptar.
	dialogo_modal.responder(lambda dialogo: (dialogo.campo.SetValue("Luis"), wx.ID_OK)[1])
	assert pedir_nombre(marco) == "Luis"
	assert dialogo_modal.ultimo.titulo == "Nombre del perfil"
	assert "&Nombre:" in dialogo_modal.ultimo.textos


def test_pedir_nombre_cancelar(marco, dialogo_modal):
	dialogo_modal.responder(wx.ID_CANCEL)
	assert pedir_nombre(marco) is None


def confirmar_borrado(padre, nombre: str) -> bool:
	respuesta = wx.MessageBox(f"¿Borrar «{nombre}»?", "Confirmar", wx.YES_NO | wx.NO_DEFAULT, padre)
	return respuesta == wx.YES


def test_confirmacion_con_messagebox(marco, dialogo_modal):
	dialogo_modal.responder(wx.ID_YES, wx.ID_NO)
	assert confirmar_borrado(marco, "copia.zip") is True
	assert confirmar_borrado(marco, "copia.zip") is False
	assert "copia.zip" in dialogo_modal.ultimo.mensaje


# ════════════════════════════════════════════════════════════════════════════
# 3. Un ListCtrl
# ════════════════════════════════════════════════════════════════════════════

def hablar(texto: str) -> None:
	"""Voz de la aplicación (en el proyecto: app/core/voz.py). Aquí no habla."""


class ListaDescargas(wx.ListCtrl):
	"""Lista de informe con dos columnas; anuncia el total al rellenarse."""

	def __init__(self, padre) -> None:
		super().__init__(padre, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
		self.InsertColumn(0, "Nombre")
		self.InsertColumn(1, "Estado")
		self.elegidos: list[int] = []
		self.Bind(wx.EVT_LIST_ITEM_SELECTED, self._al_seleccionar)

	def rellenar(self, filas: list[tuple[str, str]]) -> None:
		self.DeleteAllItems()
		for indice, (nombre, estado) in enumerate(filas):
			self.InsertItem(indice, nombre)
			self.SetItem(indice, 1, estado)
		if filas:
			self.Focus(0)  # siempre hay un elemento enfocado si hay filas
		hablar(f"{len(filas)} descargas")

	def _al_seleccionar(self, evento) -> None:
		self.elegidos.append(evento.GetIndex())


def test_lista_rellena_columnas_y_anuncia(panel, voz_simulada):
	voz_simulada.parchear(f"{__name__}.hablar")
	lista = ListaDescargas(panel)
	lista.rellenar([("a.zip", "Terminado"), ("b.iso", "En cola")])
	assert lista.GetItemCount() == 2
	assert lista.GetItemText(1, 1) == "En cola"
	assert lista.GetColumn(1).GetText() == "Estado"
	assert lista.GetFocusedItem() == 0
	assert voz_simulada.textos == ["2 descargas"]


def test_lista_evento_de_seleccion(panel):
	lista = ListaDescargas(panel)
	lista.rellenar([("a.zip", "Terminado"), ("b.iso", "En cola")])
	lista.Select(1)  # en Windows genera EVT_LIST_ITEM_SELECTED aunque no se vea
	assert lista.elegidos == [1]


# ════════════════════════════════════════════════════════════════════════════
# 4. Un hilo con wx.CallAfter
# ════════════════════════════════════════════════════════════════════════════

class PanelCarga(wx.Panel):
	"""Calcula en un hilo y actualiza la etiqueta desde el hilo principal."""

	def __init__(self, padre) -> None:
		super().__init__(padre)
		self.estado = wx.StaticText(self, label="Preparado")

	def cargar(self, numeros: list[int]) -> threading.Thread:
		hilo = threading.Thread(target=self._trabajo, args=(numeros,), daemon=True, name="carga")
		hilo.start()
		return hilo

	def _trabajo(self, numeros: list[int]) -> None:
		total = sum(numeros)  # trabajo pesado, fuera del hilo de la interfaz
		wx.CallAfter(self._terminar, total)

	def _terminar(self, total: int) -> None:
		if self:  # la ventana pudo cerrarse mientras el hilo trabajaba
			self.estado.SetLabel(f"Total: {total}")


def test_hilo_actualiza_la_interfaz_con_callafter(marco):
	panel = PanelCarga(marco)
	hilo = panel.cargar([1, 2, 3])
	hilo.join(timeout=2)
	# El resultado no aparece hasta procesar los eventos pendientes.
	esperar_hasta(lambda: panel.estado.GetLabel() == "Total: 6", mensaje="La etiqueta no se actualizó")


# ════════════════════════════════════════════════════════════════════════════
# 5. Un atajo de teclado
# ════════════════════════════════════════════════════════════════════════════

class VentanaConAtajo(wx.Frame):
	def __init__(self) -> None:
		super().__init__(None, title="Atajos")
		self.actualizaciones = 0
		self.Bind(wx.EVT_CHAR_HOOK, self._al_pulsar)

	def _al_pulsar(self, evento) -> None:
		if evento.GetKeyCode() == wx.WXK_F5 and not evento.HasAnyModifiers():
			self.actualizaciones += 1
			return
		evento.Skip()  # imprescindible: si no, Tab, flechas y escritura dejan de funcionar


def test_f5_actualiza_y_el_resto_de_teclas_sigue_su_camino(marco):
	ventana = VentanaConAtajo()
	resultado = simular_tecla(ventana, wx.WXK_F5)
	assert ventana.actualizaciones == 1
	assert resultado.gestionado and not resultado.saltado
	assert simular_tecla(ventana, wx.WXK_TAB).saltado
	assert simular_tecla(ventana, "a", ctrl=True).saltado


# ════════════════════════════════════════════════════════════════════════════
# 6. Un error registrado
# ════════════════════════════════════════════════════════════════════════════

AJUSTES_POR_DEFECTO = {"idioma": "es", "volumen": 50}


def leer_ajustes(ruta: Path) -> dict:
	"""Lee los ajustes; si el archivo está dañado, lo registra y usa los de fábrica."""
	try:
		return json.loads(ruta.read_text(encoding="utf-8"))
	except FileNotFoundError:
		return dict(AJUSTES_POR_DEFECTO)
	except json.JSONDecodeError:
		log.warning("Ajustes dañados en %s; se usan los valores por defecto", ruta, exc_info=True)
		return dict(AJUSTES_POR_DEFECTO)


def test_ajustes_danados_se_registran(tmp_path, caplog):
	ruta = tmp_path / "ajustes.json"
	ruta.write_text("{roto", encoding="utf-8")
	with caplog.at_level(logging.WARNING, logger=__name__):
		assert leer_ajustes(ruta) == AJUSTES_POR_DEFECTO
	registro = caplog.records[-1]
	assert registro.levelno == logging.WARNING
	assert "ajustes.json" in registro.getMessage()
	assert registro.exc_info is not None  # con traza: se sabrá dónde falló


# ════════════════════════════════════════════════════════════════════════════
# 7. Ruta portable
# ════════════════════════════════════════════════════════════════════════════

def ruta_ajustes() -> Path:
	"""En el proyecto: app/core/paths.py. Respeta APP_DATA_DIR para las pruebas."""
	base = os.environ.get("APP_DATA_DIR") or str(Path(__file__).resolve().parent.parent / "data")
	return Path(base) / "config" / "ajustes.json"


def test_la_ruta_queda_dentro_del_data_de_la_prueba(data_temporal):
	ruta = ruta_ajustes()
	assert ruta.is_relative_to(data_temporal)
	ruta.parent.mkdir(parents=True)
	ruta.write_text("{}", encoding="utf-8")
	assert (data_temporal / "config" / "ajustes.json").is_file()
	# Nada fuera de la carpeta del programa: ni APPDATA ni TEMP del usuario.
	appdata = os.environ.get("APPDATA")
	assert not appdata or not ruta.is_relative_to(appdata)


# ════════════════════════════════════════════════════════════════════════════
# 8. Reproducir un fallo antes de corregirlo
# ════════════════════════════════════════════════════════════════════════════

def contar_palabras_version_con_fallo(texto: str) -> int:
	"""Versión con el fallo del informe: dos espacios seguidos cuentan una palabra de más."""
	return len(texto.split(" ")) if texto else 0


def contar_palabras(texto: str) -> int:
	"""Versión corregida: ``split()`` sin argumento ignora espacios repetidos y saltos."""
	return len(texto.split())


CASOS_DEL_INFORME = [("hola  mundo", 2), ("  espacios al principio", 3), ("una\nlínea más", 3), ("", 0)]


@pytest.mark.xfail(reason="Paso 1: la prueba reproduce el fallo antes de tocar el código", strict=True)
@pytest.mark.parametrize(("texto", "esperado"), CASOS_DEL_INFORME[:3])
def test_paso_1_la_prueba_falla_con_la_version_antigua(texto, esperado):
	assert contar_palabras_version_con_fallo(texto) == esperado


@pytest.mark.parametrize(("texto", "esperado"), CASOS_DEL_INFORME)
def test_paso_2_regresion_contar_palabras(texto, esperado):
	"""Se queda en el proyecto para que el fallo no vuelva."""
	assert contar_palabras(texto) == esperado
