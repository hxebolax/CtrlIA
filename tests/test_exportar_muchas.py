"""Exportar muchas sesiones, cada una en su archivo (AIChat #462).

Nunca se sustituye nada, un fallo no detiene el resto y hay resumen final. Sin mostrar ventanas.
"""

from __future__ import annotations

import pytest

from app.services import exportar
from app.services.gestor import FilaSesion, MensajeVista


def _fila(clave: str, titulo: str = "Sesión") -> FilaSesion:
	return FilaSesion(clave, "claude", titulo, carpeta="C:\\proyecto")


def _leer(_clave: str) -> list[MensajeVista]:
	return [MensajeVista("usuario", "Hola", 1.0), MensajeVista("asistente", "Adiós **ya**", 2.0)]


@pytest.mark.parametrize(("formato", "extension", "trozo"), [
	("md", ".md", "### "), ("html", ".html", "<!DOCTYPE html>"), ("txt", ".txt", "Hola"),
])
def test_cada_sesion_en_su_archivo_con_su_formato(tmp_path, formato, extension, trozo):
	resultado = exportar.exportar_muchas([_fila("a", "Uno"), _fila("b", "Dos")], _leer, tmp_path, formato)
	assert [t for t, _r, _m in resultado.escritas] == ["Uno", "Dos"]
	assert (tmp_path / ("Uno" + extension)).is_file() and (tmp_path / ("Dos" + extension)).is_file()
	assert trozo in (tmp_path / ("Uno" + extension)).read_text(encoding="utf-8")
	assert resultado.fallidas == [] and not resultado.detenida


def test_lleva_los_datos_de_la_sesion(tmp_path):
	exportar.exportar_muchas([_fila("a", "Uno")], _leer, tmp_path, "md")
	texto = (tmp_path / "Uno.md").read_text(encoding="utf-8")
	assert "# Uno" in texto and "C:\\proyecto" in texto and "Claude" in texto


def test_crea_la_carpeta_si_no_existe(tmp_path):
	destino = tmp_path / "nueva" / "dentro"
	resultado = exportar.exportar_muchas([_fila("a")], _leer, destino, "txt")
	assert len(resultado.escritas) == 1 and destino.is_dir()


def test_nunca_sustituye_lo_que_ya_hay(tmp_path):
	existente = tmp_path / "Uno.md"
	existente.write_text("lo mío", encoding="utf-8")
	resultado = exportar.exportar_muchas([_fila("a", "Uno")], _leer, tmp_path, "md")
	assert existente.read_text(encoding="utf-8") == "lo mío"
	assert resultado.escritas[0][1].name == "Uno (2).md"


def test_dos_sesiones_con_el_mismo_titulo(tmp_path):
	resultado = exportar.exportar_muchas([_fila("a", "Igual"), _fila("b", "Igual"), _fila("c", "Igual")], _leer, tmp_path, "md")
	assert [r.name for _t, r, _m in resultado.escritas] == ["Igual.md", "Igual (2).md", "Igual (3).md"]


def test_exportar_dos_veces_no_pisa_nada(tmp_path):
	exportar.exportar_muchas([_fila("a", "Uno")], _leer, tmp_path, "md")
	antes = (tmp_path / "Uno.md").read_bytes()
	exportar.exportar_muchas([_fila("a", "Uno")], _leer, tmp_path, "md")
	assert (tmp_path / "Uno.md").read_bytes() == antes and (tmp_path / "Uno (2).md").is_file()


def test_un_fallo_no_detiene_el_resto(tmp_path):
	def leer(clave: str) -> list[MensajeVista]:
		if clave == "mala":
			raise OSError("disco roto")
		return _leer(clave)

	resultado = exportar.exportar_muchas([_fila("a", "Uno"), _fila("mala", "Mala"), _fila("c", "Tres")], leer, tmp_path, "md")
	assert [t for t, _r, _m in resultado.escritas] == ["Uno", "Tres"]
	assert resultado.fallidas == [("Mala", "disco roto")]
	assert not (tmp_path / "Mala.md").exists()


def test_una_sesion_sin_mensajes_cuenta_como_fallo(tmp_path):
	destino = tmp_path / "salida"
	resultado = exportar.exportar_muchas([_fila("a", "Vacía")], lambda _c: [], destino, "md")
	assert resultado.fallidas == [("Vacía", "no tiene mensajes")] and not list(destino.iterdir())


def test_un_archivo_a_medias_no_se_queda(tmp_path, monkeypatch):
	"""Sin espacio en disco, por ejemplo: nada escrito a medias se queda con el nombre."""
	def generador(_mensajes, _titulo, _datos):
		return "texto con un sustituto suelto \ud800"  # no se puede escribir en UTF-8

	monkeypatch.setitem(exportar._GENERADORES, "txt", generador)  # noqa: SLF001
	destino = tmp_path / "salida"
	resultado = exportar.exportar_muchas([_fila("a", "Rota")], _leer, destino, "txt")
	assert len(resultado.fallidas) == 1 and not list(destino.iterdir())


def test_se_puede_detener_y_lo_escrito_se_queda(tmp_path):
	vistas: list[int] = []

	def progreso(hechas: int, _total: int) -> None:
		vistas.append(hechas)

	resultado = exportar.exportar_muchas([_fila("a", "Uno"), _fila("b", "Dos"), _fila("c", "Tres")], _leer, tmp_path, "md",
		cancelado=lambda: len(vistas) >= 2, progreso=progreso)
	assert resultado.detenida and [t for t, _r, _m in resultado.escritas] == ["Uno", "Dos"]
	assert (tmp_path / "Uno.md").is_file()


def test_progreso_de_principio_a_fin(tmp_path):
	vistas: list[tuple[int, int]] = []
	exportar.exportar_muchas([_fila("a", "Uno"), _fila("b", "Dos")], _leer, tmp_path, "md",
		progreso=lambda hechas, total: vistas.append((hechas, total)))
	assert vistas == [(0, 2), (1, 2), (2, 2)]


def test_sin_herramientas_no_lleva_su_salida(tmp_path):
	def leer(_clave: str) -> list[MensajeVista]:
		return [
			MensajeVista("herramienta", "Ejecutar", 1.0, detalle="salida secreta", herramienta="Bash"),
			MensajeVista("cambio", "Archivo modificado: x.py", 2.0, detalle="+diff secreto",
				cambios=[{"ruta": "x.py", "diff": "+diff secreto"}]),
		]

	exportar.exportar_muchas([_fila("a", "Entera")], leer, tmp_path, "md")
	exportar.exportar_muchas([_fila("a", "Corta")], leer, tmp_path, "md", incluir_herramientas=False)
	assert "salida secreta" in (tmp_path / "Entera.md").read_text(encoding="utf-8")
	corta = (tmp_path / "Corta.md").read_text(encoding="utf-8")
	assert "secret" not in corta and "Ejecutar" in corta and "x.py" in corta


def test_formato_desconocido(tmp_path):
	with pytest.raises(ValueError, match="desconocido"):
		exportar.exportar_muchas([_fila("a")], _leer, tmp_path, "pdf")


@pytest.mark.parametrize(("titulo", "esperado"), [
	("Normal", "Normal.md"),
	("Con / barra: y ¿acentos?", "Con _ barra_ y ¿acentos.md"),
	("Conversación", "Conversación.md"),
	("  puntos y espacios...  ", "puntos y espacios.md"),
	("", "sesion.md"),
	("...", "sesion.md"),
	("varias\nlíneas", "varias líneas.md"),
	("CON", "_CON.md"),
	("nul.txt", "_nul.txt.md"),
	("com1", "_com1.md"),
])
def test_nombres_validos_en_windows(titulo, esperado):
	assert exportar.nombre_archivo(titulo, "md") == esperado


def test_nombre_largo_se_acorta():
	assert len(exportar.nombre_archivo("x" * 500, "html")) == exportar.MAX_NOMBRE + len(".html")


def test_resumen_de_todo_bien(tmp_path):
	resultado = exportar.exportar_muchas([_fila("a", "Uno"), _fila("b", "Dos")], _leer, tmp_path, "md")
	assert exportar.resumen_muchas(resultado, tmp_path) == f"Sesiones exportadas en {tmp_path}: 2."


def test_resumen_con_fallos_recortado(tmp_path):
	resultado = exportar.ResultadoMuchas(fallidas=[(f"S{i}", "falla") for i in range(13)])
	texto = exportar.resumen_muchas(resultado, tmp_path)
	assert texto.startswith("No se ha exportado ninguna sesión.") and "No se pudieron exportar: 13." in texto
	assert "- S9: falla" in texto and "S10" not in texto and "Y 3 más." in texto


def test_resumen_detenida(tmp_path):
	texto = exportar.resumen_muchas(exportar.ResultadoMuchas(detenida=True), tmp_path)
	assert texto.startswith("Exportación detenida.") and ": 0." in texto


# -- Diálogo ------------------------------------------------------------------------------


@pytest.fixture
def dialogo(app_wx):
	pytest.importorskip("wx")
	from app.ui.dialogos.intercambio import DialogoExportarSesiones
	from tests.ayudas_wx import procesar_eventos

	ventana = DialogoExportarSesiones(None, [("Todas", [_fila("ctrlia:a", "Uno"), _fila("ctrlia:b", "Dos")])])
	yield ventana
	ventana.Destroy()
	procesar_eventos()


def test_de_fabrica_es_el_archivo_para_otro_equipo(dialogo):
	assert dialogo.formato_elegido() == "zip"
	assert not dialogo.campo_carpeta.IsEnabled() and not dialogo.boton_carpeta.IsEnabled()
	assert dialogo.aceptar() and dialogo.formato == "zip" and dialogo.carpeta is None


def test_elegir_archivos_sueltos_activa_la_carpeta(dialogo):
	dialogo.campo_formato.SetSelection(2)
	dialogo.actualizar_formato()
	assert dialogo.formato_elegido() == "html" and dialogo.campo_carpeta.IsEnabled() and dialogo.boton_carpeta.IsEnabled()


def test_archivos_sueltos_piden_carpeta(dialogo, monkeypatch, tmp_path):
	from app.ui.dialogos import intercambio

	errores: list[str] = []
	monkeypatch.setattr(intercambio, "mostrar_error", lambda _padre, texto, *a: errores.append(texto))
	dialogo.campo_formato.SetSelection(1)
	dialogo.actualizar_formato()
	assert not dialogo.aceptar() and "Carpeta de destino" in errores[0]
	dialogo.campo_carpeta.SetValue("relativa")
	assert not dialogo.aceptar()
	dialogo.campo_carpeta.SetValue(str(tmp_path))
	assert dialogo.aceptar() and dialogo.formato == "md" and dialogo.carpeta == tmp_path


def test_el_dialogo_no_repite_aceleradores(dialogo):
	wx = pytest.importorskip("wx")

	def textos(ventana):
		for hijo in ventana.GetChildren():
			yield from textos(hijo)
			if isinstance(hijo, (wx.StaticText, wx.Button, wx.CheckBox)):
				yield hijo.GetLabel()

	letras = [t[t.index("&") + 1].lower() for t in textos(dialogo) if "&" in t.replace("&&", "")]
	assert len(letras) == len(set(letras)), letras


def test_el_boton_de_la_carpeta_tiene_ayuda_y_el_campo_su_etiqueta(dialogo):
	assert dialogo.boton_carpeta.GetHelpText() and dialogo.campo_carpeta.GetHelpText()
	assert dialogo.campo_formato.GetHelpText()
	assert "Carpeta de destino" in dialogo.campo_carpeta.GetName()


def test_la_ventana_exporta_en_segundo_plano_y_avisa(tmp_path, monkeypatch):
	from types import SimpleNamespace

	from app.ui import avisos, vp_sesiones

	notificados: list[str] = []
	monkeypatch.setattr(avisos, "anunciar", lambda *a, **k: None)
	monkeypatch.setattr(avisos, "notificar", lambda texto, *a, **k: notificados.append(texto))

	def en_segundo_plano(funcion, al_terminar=None, al_fallar=None):
		al_terminar(funcion())

	marco = SimpleNamespace(gestor=SimpleNamespace(mensajes=_leer), _saliendo=False, en_segundo_plano=en_segundo_plano,
		_progreso_por_voz=lambda plantilla: lambda i, n: None)
	vp_sesiones.MixinSesiones.exportar_a_archivos(marco, [_fila("a", "Uno")], tmp_path, "txt", True)
	assert (tmp_path / "Uno.txt").is_file()
	assert notificados == [f"Sesiones exportadas en {tmp_path}: 1."]
