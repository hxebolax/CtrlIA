"""Leer un diff por sus recuentos ``@@`` (AIChat #405, #406) y el cuarto estilo, ``!-`` y ``!+``.

Dentro de un trozo, la cabecera ``@@ -1,2 +1,2 @@`` dice cuántas líneas quedan: ``--- x`` ahí
es una línea quitada que empieza por ``-- `` (un comentario de SQL), nunca la cabecera de un archivo.
"""

from __future__ import annotations

from app.services import extraccion
from app.services.gestor import MensajeVista

SQL = "--- a/esquema.sql\n+++ b/esquema.sql\n@@ -1,3 +1,3 @@\n--- comentario viejo\n+++ suma nueva\n SELECT 1;\n-x\n+y"


def test_contar_no_pierde_lineas_que_empiezan_por_guiones_o_mas():
	# Dos quitadas y dos añadidas: las primeras parecen cabeceras, y no lo son.
	assert extraccion.contar_lineas_diff(SQL) == (2, 2)


def test_contar_no_cuenta_las_cabeceras_de_cada_archivo():
	dos = ("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n--- a/y\n+++ b/y\n@@ -1,2 +1,1 @@\n-c\n-d\n+e")
	assert extraccion.contar_lineas_diff(dos) == (2, 3)


def test_contar_sin_cabeceras():
	assert extraccion.contar_lineas_diff("-a\n+b\n c") == (1, 1)
	assert extraccion.contar_lineas_diff("") == (0, 0)
	# Sin «@@»: una cabecera es la pareja «---» y «+++»; suelta, es una línea más.
	assert extraccion.contar_lineas_diff("--- comentario\n-a\n+b") == (1, 2)
	assert extraccion.contar_lineas_diff("--- a/x\n+++ b/x\n-a\n+b") == (1, 1)
	assert extraccion.contar_lineas_diff("+++ suma\n-a") == (1, 1)


def test_legible_con_trozo_no_confunde_las_lineas():
	texto = extraccion.diff_legible(SQL).splitlines()
	assert texto[:3] == ["Antes: a/esquema.sql", "Después: b/esquema.sql", "Cambio en las líneas 1 a 3:"]
	assert "Línea 1, quitada: -- comentario viejo" in texto
	assert "Línea 1, añadida: ++ suma nueva" in texto


def test_legible_sin_trozo_una_quitada_de_sql_no_es_una_cabecera():
	assert extraccion.diff_legible("--- comentario\n+nuevo") == "Quitada: -- comentario\nAñadida: nuevo"
	assert extraccion.diff_legible("+++ suma\n-viejo") == "Añadida: ++ suma\nQuitada: viejo"
	assert extraccion.diff_legible("--- a/x\n+++ b/x\n-a\n+b").splitlines() == [
		"Antes: a/x", "Después: b/x", "Quitada: a", "Añadida: b",
	]
	assert extraccion.diff_legible("--- comentario\n+nuevo", "cortas") == "Menos: -- comentario\nMás: nuevo"


def test_cuarto_estilo_empareja_cada_linea_con_su_sustituta():
	diff = "@@ -11,4 +12,5 @@\n def saluda(nombre):\n-    print(nombre)\n+    mensaje = nombre\n+    print(mensaje)\n \n def main():"
	assert extraccion.diff_legible(diff, "parejas").splitlines() == [
		"@@ -11,4 +12,5 @@",
		" def saluda(nombre):",
		"!-    print(nombre)",
		"!+    mensaje = nombre",
		"+    print(mensaje)",
		" ",
		" def main():",
	]


def test_cuarto_estilo_varias_sustituciones_seguidas_van_por_parejas():
	diff = "@@ -1,3 +1,2 @@\n-uno\n-dos\n-tres\n+UNO\n+DOS"
	assert extraccion.diff_legible(diff, "parejas").splitlines() == [
		"@@ -1,3 +1,2 @@", "!-uno", "!+UNO", "!-dos", "!+DOS", "-tres",
	]


def test_cuarto_estilo_sin_sustituta_conserva_el_signo():
	assert extraccion.diff_legible("-solo\n x\n+otra", "parejas").splitlines() == ["-solo", " x", "+otra"]
	assert extraccion.diff_legible("", "parejas") == ""
	# Las cabeceras y las notas siguen igual, y una quitada de SQL también se empareja.
	assert extraccion.diff_legible(SQL, "parejas").splitlines()[:5] == [
		"--- a/esquema.sql", "+++ b/esquema.sql", "@@ -1,3 +1,3 @@", "!--- comentario viejo", "!+++ suma nueva",
	]
	assert extraccion.diff_legible("@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b", "parejas").splitlines() == [
		"@@ -1 +1 @@", "!-a", "\\ No newline at end of file", "!+b",
	]


def test_preferencia_del_cuarto_estilo():
	from app.core.configuracion import validar_valor
	from app.ui.dialogo_preferencias import PAGINAS

	assert extraccion.ESTILOS_CAMBIOS == ("palabras", "cortas", "simbolos", "parejas")
	assert extraccion.ESTILOS_CON_SIGNOS == ("simbolos", "parejas")
	validar_valor("mensajes.estilo_cambios", "parejas")
	mensajes = next(p for p in PAGINAS if p.titulo == "Mensajes")
	opcion = next(o for o in mensajes.opciones if o.clave == "mensajes.estilo_cambios")
	assert [v for v, _t in opcion.opciones] == list(extraccion.ESTILOS_CAMBIOS)
	assert "!-" in opcion.ayuda and "!+" in opcion.ayuda


# ── Renombrado desde ────────────────────────────────────────────────────────────


def _cambio(ruta: str, diff: str, movido_desde: str = "") -> MensajeVista:
	cambio = {"ruta": ruta, "diff": diff}
	if movido_desde:
		cambio["movido_desde"] = movido_desde
	return MensajeVista("cambio", f"Archivo modificado: {ruta}", 1.0, cambios=[cambio])


def test_el_cambio_recuerda_de_donde_se_renombro():
	cambios = extraccion.archivos_cambiados([_cambio("nuevo.py", "-a\n+b", "viejo.py"), _cambio("nuevo.py", "-b\n+c")])
	assert len(cambios) == 1 and cambios[0].ruta == "nuevo.py" and cambios[0].movido_desde == "viejo.py"
	assert extraccion.texto_cambio(cambios[0], "palabras").splitlines()[0] == "Renombrado desde: viejo.py"
	assert extraccion.texto_cambio(cambios[0], "simbolos").splitlines()[:2] == ["Renombrado desde: viejo.py", "-a"]


def test_sin_renombrar_no_se_dice_nada():
	cambios = extraccion.archivos_cambiados([_cambio("nuevo.py", "-a\n+b")])
	assert extraccion.texto_cambio(cambios[0], "palabras") == "Quitada: a\nAñadida: b"


def test_el_texto_de_codex_con_movido_a_tambien_vale():
	"""Las sesiones de Codex leídas con ``thread/read`` no traen ``cambios``: solo el texto y el detalle."""
	linea = "Archivo modificado: viejo.py (movido a nuevo.py)"
	mensaje = MensajeVista("cambio", linea, 1.0, detalle=linea + "\n-a\n+b")
	cambio = extraccion.archivos_cambiados([mensaje])[0]
	assert (cambio.ruta, cambio.movido_desde, cambio.diff) == ("nuevo.py", "viejo.py", "-a\n+b")


def test_el_gestor_guarda_el_renombrado(tmp_path):
	from app.services.agentes.base import Evento, TipoEvento
	from app.services.almacen import Almacen, SesionPropia
	from app.services.gestor import GestorSesiones

	almacen = Almacen(tmp_path / "prueba.db")
	almacen.guardar_sesion(SesionPropia("ctrlia:x", "codex"))
	gestor = GestorSesiones(almacen, {}, lambda c, e: None)
	gestor._guardar("ctrlia:x", Evento(TipoEvento.ARCHIVO, "", {"ruta": "viejo.py", "tipo": "modificado", "diff": "-a", "destino": "nuevo.py"}))  # noqa: SLF001
	guardado = almacen.mensajes("ctrlia:x")[-1]
	assert guardado.texto == "Archivo modificado: viejo.py (movido a nuevo.py)"
	assert guardado.datos["cambios"] == [{"ruta": "nuevo.py", "diff": "-a", "movido_desde": "viejo.py"}]
