"""Catálogo de agentes (habilidades con SKILL.md): servicio, gestor, elegir agentes y lista de casillas."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from app.services import catalogo_agentes as servicio
from app.services.almacen import Almacen
from app.services.catalogo_agentes import CatalogoAgentes, ErrorCatalogo

#: Frontmatter con el formato real de los agentes de ejemplo (accesibilidad-wx, recortado).
SKILL_REAL = """---
name: accesibilidad-wx
description: Especialista en accesibilidad de aplicaciones de escritorio wxPython en Windows para lectores de pantalla (NVDA, JAWS, Narrador), teclado, braille y baja visión. Úsalo cuando el usuario diga «usa el agente de accesibilidad».
license: MIT
metadata:
  version: "1.1.0"
  idioma: es
  ambito: python-wxpython-windows
---

# Agente de accesibilidad para wxPython

Eres un especialista en accesibilidad.
"""

#: Agentes reales: los de ejemplo que trae el propio proyecto.
CARPETA_AGENTES_REAL = Path(__file__).resolve().parents[1] / "data" / "agentes_ejemplo"


def escribir_skill(carpeta: Path, texto: str | None = None, nombre: str = "SKILL.md", **campos: str) -> Path:
	"""Crea ``carpeta/SKILL.md`` con ``texto`` o con un frontmatter hecho de ``campos``."""
	carpeta.mkdir(parents=True, exist_ok=True)
	if texto is None:
		lineas = ["---", *(f"{clave}: {valor}" for clave, valor in campos.items()), "---", "", "# Cuerpo", ""]
		texto = "\n".join(lineas)
	archivo = carpeta / nombre
	archivo.write_text(texto, encoding="utf-8")
	return archivo


@pytest.fixture
def almacen(tmp_path):
	base = Almacen(tmp_path / "catalogo.db")
	yield base
	base.cerrar()


@pytest.fixture
def catalogo(almacen, tmp_path):
	return CatalogoAgentes(almacen, tmp_path / "creados")


# ════════════════════════════════════════════════════════════════════════════
# Frontmatter
# ════════════════════════════════════════════════════════════════════════════

def test_lee_un_skill_real(tmp_path):
	escribir_skill(tmp_path / "accesibilidad-wx", SKILL_REAL)
	ficha = servicio.leer_skill(tmp_path / "accesibilidad-wx")
	assert ficha.nombre == "accesibilidad-wx"
	assert ficha.descripcion.startswith("Especialista en accesibilidad")
	assert ficha.descripcion.endswith("«usa el agente de accesibilidad».")
	assert ficha.version == "1.1.0"
	assert ficha.categoria == ""
	assert ficha.datos["metadata"]["ambito"] == "python-wxpython-windows"
	assert ficha.aviso == ""


@pytest.mark.skipif(not CARPETA_AGENTES_REAL.is_dir(), reason="Faltan los agentes de ejemplo del proyecto")
def test_lee_los_skill_de_la_carpeta_de_agentes_copiados(tmp_path):
	# Se copian solo los SKILL.md (nunca se escribe en la carpeta real).
	copiados = 0
	for original in sorted(CARPETA_AGENTES_REAL.glob("*/SKILL.md"))[:6]:
		destino = tmp_path / "agentes" / original.parent.name
		destino.mkdir(parents=True)
		shutil.copyfile(original, destino / "SKILL.md")
		copiados += 1
	resultado = servicio.buscar_agentes(tmp_path)
	assert len(resultado.encontrados) == copiados > 0
	for ficha in resultado.encontrados:
		assert ficha.nombre == ficha.carpeta.name
		assert len(ficha.descripcion) > 50 and "\n" not in ficha.descripcion
		assert ficha.version
		# «agentes» es una carpeta genérica: no se propone como categoría.
		assert servicio.categoria_propuesta(ficha, tmp_path) == ""


def test_yaml_bloques_comillas_listas_y_anidados():
	datos = servicio.parsear_yaml_simple(
		"name: x  # comentario\n"
		"description: >\n  Línea uno\n  sigue aquí\n\n  otro párrafo\n"
		"literal: |-\n  uno\n  dos\n"
		"metadata:\n  version: \"1.2\"\n  category: 'Web  ''tools'''\n"
		"tags: [a, b]\n"
		"lista:\n  - uno\n  - dos: 2\n    tres: 3\n"
		"largo: esto es\n  un texto largo # comentario\n"
		"cita: \"con \\\"escape\\\" y\n  salto \\u00e1\"\n"
		"vacio:\n"
		"nulo: ~\n"
	)
	assert datos["name"] == "x"
	assert datos["description"] == "Línea uno sigue aquí\notro párrafo\n"
	assert datos["literal"] == "uno\ndos"
	assert datos["metadata"] == {"version": "1.2", "category": "Web  'tools'"}
	assert datos["tags"] == ["a", "b"]
	assert datos["lista"] == ["uno", {"dos": "2", "tres": "3"}]
	assert datos["largo"] == "esto es un texto largo"
	assert datos["cita"] == 'con "escape" y salto á'
	assert datos["vacio"] == "" and datos["nulo"] == ""


def test_yaml_roto_no_lanza():
	assert servicio.parsear_yaml_simple("") == {}
	assert servicio.parsear_yaml_simple("::: nada\n- suelto\n  : raro") == {}
	datos = servicio.parsear_yaml_simple("name: bien\nesto no es yaml\n\tdescription: con tabulador\n")
	assert datos["name"] == "bien"


def test_skill_sin_frontmatter_usa_carpeta_y_primer_parrafo(tmp_path):
	escribir_skill(tmp_path / "mi-agente", "# Título\n\nHace cosas útiles\nen dos líneas.\n\nSegundo párrafo.\n")
	ficha = servicio.leer_skill(tmp_path / "mi-agente" / "SKILL.md")
	assert ficha.nombre == "mi-agente"
	assert ficha.descripcion == "Hace cosas útiles en dos líneas."
	assert "frontmatter" in ficha.aviso


def test_skill_con_bom_crlf_categoria_y_minusculas(tmp_path):
	texto = "\ufeff---\r\nname: \"Revisor\"\r\ndescription: |\r\n  Revisa\r\n  textos\r\nversion: 2.0\r\nmetadata:\r\n  category: Escritura\r\n---\r\nCuerpo\r\n"
	(tmp_path / "revisor").mkdir()
	(tmp_path / "revisor" / "skill.md").write_bytes(texto.encode("utf-8"))
	ficha = servicio.leer_skill(tmp_path / "revisor")
	assert (ficha.nombre, ficha.descripcion, ficha.version, ficha.categoria) == ("Revisor", "Revisa textos", "2.0", "Escritura")
	assert ficha.archivo.name.lower() == "skill.md"


def test_frontmatter_sin_cierre_y_sin_descripcion(tmp_path):
	escribir_skill(tmp_path / "a", "---\nname: a\n\nTexto del cuerpo.\n")
	ficha = servicio.leer_skill(tmp_path / "a")
	assert ficha.nombre == "a" and "frontmatter" in ficha.aviso
	escribir_skill(tmp_path / "b", "---\nname: b\n---\n\nPrimer párrafo.\n")
	ficha = servicio.leer_skill(tmp_path / "b")
	assert ficha.descripcion == "Primer párrafo." and "descripción" in ficha.aviso


def test_leer_skill_errores(tmp_path, monkeypatch):
	with pytest.raises(ErrorCatalogo):
		servicio.leer_skill(tmp_path / "no-existe")
	(tmp_path / "vacia").mkdir()
	with pytest.raises(ErrorCatalogo, match="SKILL.md"):
		servicio.leer_skill(tmp_path / "vacia")
	escribir_skill(tmp_path / "grande", "---\nname: g\n---\n" + "x" * 200)
	monkeypatch.setattr(servicio, "MAX_TAMANO_SKILL", 100)
	with pytest.raises(ErrorCatalogo, match="1 MB"):
		servicio.leer_skill(tmp_path / "grande")


# ════════════════════════════════════════════════════════════════════════════
# Búsqueda
# ════════════════════════════════════════════════════════════════════════════

def test_busqueda_recursiva_con_limites(tmp_path):
	escribir_skill(tmp_path / "web" / "html", name="html", description="Páginas")
	escribir_skill(tmp_path / "web" / "css", name="css", description="Estilos")
	escribir_skill(tmp_path / "uno" / "dos" / "tres" / "hondo", name="hondo", description="Profundo")
	# Dentro de un agente no se busca; las carpetas de herramientas se saltan; las plantillas no cuentan.
	escribir_skill(tmp_path / "web" / "html" / "plantillas" / "interno", name="interno", description="No")
	escribir_skill(tmp_path / "proyecto" / "node_modules" / "paquete", name="paquete", description="No")
	escribir_skill(tmp_path / "proyecto" / ".git" / "x", name="x", description="No")
	escribir_skill(tmp_path / "plantillas" / "agente-nuevo", name="{{NOMBRE}}", description="{{DESCRIPCION}}")
	resultado = servicio.buscar_agentes(tmp_path)
	assert sorted(f.nombre for f in resultado.encontrados) == ["css", "hondo", "html"]
	assert resultado.plantillas == 1 and not resultado.truncado
	poco_hondo = servicio.buscar_agentes(tmp_path, profundidad=2)
	assert sorted(f.nombre for f in poco_hondo.encontrados) == ["css", "html"]
	cortado = servicio.buscar_agentes(tmp_path, max_entradas=3)
	assert cortado.truncado
	uno = servicio.buscar_agentes(tmp_path, max_agentes=1)
	assert len(uno.encontrados) == 1 and uno.truncado
	cancelado = servicio.buscar_agentes(tmp_path, cancelado=lambda: True)
	assert cancelado.truncado and not cancelado.encontrados


def test_busqueda_en_la_propia_carpeta_del_agente_y_errores(tmp_path):
	escribir_skill(tmp_path / "solo", name="solo", description="Uno")
	assert [f.nombre for f in servicio.buscar_agentes(tmp_path / "solo").encontrados] == ["solo"]
	resultado = servicio.buscar_agentes(tmp_path / "no-existe")
	assert resultado.errores and not resultado.encontrados


def test_busqueda_no_sigue_uniones(tmp_path):
	_winapi = pytest.importorskip("_winapi")
	escribir_skill(tmp_path / "fuera" / "oculto", name="oculto", description="No")
	(tmp_path / "raiz").mkdir()
	try:
		_winapi.CreateJunction(str(tmp_path / "fuera"), str(tmp_path / "raiz" / "enlace"))
	except OSError:
		pytest.skip("No se pueden crear uniones aquí")
	assert servicio.buscar_agentes(tmp_path / "raiz").encontrados == []


def test_categoria_propuesta(tmp_path):
	con_metadata = escribir_skill(tmp_path / "web" / "a", "---\nname: a\nmetadata:\n  category: Diseño\n---\n")
	sin = escribir_skill(tmp_path / "web" / "b", name="b", description="B")
	generica = escribir_skill(tmp_path / "skills" / "c", name="c", description="C")
	directo = escribir_skill(tmp_path / "d", name="d", description="D")
	assert servicio.categoria_propuesta(servicio.leer_skill(con_metadata), tmp_path) == "Diseño"
	assert servicio.categoria_propuesta(servicio.leer_skill(sin), tmp_path) == "web"
	assert servicio.categoria_propuesta(servicio.leer_skill(generica), tmp_path) == ""
	assert servicio.categoria_propuesta(servicio.leer_skill(directo), tmp_path) == ""
	# Fuera de la carpeta buscada no se propone nada.
	assert servicio.categoria_propuesta(servicio.leer_skill(sin), tmp_path / "otra") == ""


def test_nombre_seguro():
	assert servicio.nombre_seguro("Revisión de código") == "revision-de-codigo"
	assert servicio.nombre_seguro("  ¡¡Hola!!  mundo//") == "hola-mundo"
	assert servicio.nombre_seguro("???") == "agente"
	assert servicio.nombre_seguro("CON") == "con-agente"
	assert len(servicio.nombre_seguro("x" * 300)) <= servicio.MAX_NOMBRE_CARPETA


# ════════════════════════════════════════════════════════════════════════════
# Catálogo
# ════════════════════════════════════════════════════════════════════════════

def test_importar_sin_duplicar(catalogo, tmp_path):
	escribir_skill(tmp_path / "web" / "html", name="html", description="Páginas", version="1.0")
	ficha = servicio.leer_skill(tmp_path / "web" / "html")
	primero = catalogo.importar(ficha, "Web")
	assert primero.nuevo
	# La misma carpeta, escrita con otras mayúsculas, no se duplica.
	otra = servicio.leer_skill(Path(str(tmp_path / "WEB" / "HTML")))
	segundo = catalogo.importar(otra)
	assert (segundo.id, segundo.nuevo, segundo.actualizado) == (primero.id, False, False)
	assert len(catalogo.listar()) == 1
	escribir_skill(tmp_path / "web" / "html", name="html", description="Páginas web nuevas", version="1.1")
	tercero = catalogo.importar(servicio.leer_skill(tmp_path / "web" / "html"), actualizar_descripcion=True)
	agente = catalogo.obtener(primero.id)
	assert tercero.actualizado and agente.descripcion == "Páginas web nuevas" and agente.version == "1.1"
	assert agente.categoria == "Web" and agente.origen == "importado"
	assert catalogo.en_catalogo(tmp_path / "web" / "html").id == primero.id


def test_importar_carpeta_y_varios(catalogo, tmp_path):
	escribir_skill(tmp_path / "a", name="a", description="A")
	escribir_skill(tmp_path / "b", name="b", description="B")
	assert catalogo.importar_carpeta(tmp_path / "a").nuevo
	with pytest.raises(ErrorCatalogo):
		catalogo.importar_carpeta(tmp_path / "nada")
	fichas = [(servicio.leer_skill(tmp_path / n), "Cat") for n in ("a", "b")]
	resultados = catalogo.importar_varios(fichas)
	assert [r.nuevo for r in resultados] == [False, True]


def test_listar_filtrar_y_editar(catalogo, tmp_path):
	for nombre, categoria, descripcion in (("html", "Web", "Páginas"), ("pruebas", "Programación", "Pytest"),
			("suelto", "", "Sin categoría")):
		escribir_skill(tmp_path / nombre, name=nombre, description=descripcion)
		catalogo.importar(servicio.leer_skill(tmp_path / nombre), categoria)
	assert catalogo.categorias() == ["Programación", "Web"]
	assert [a.nombre for a in catalogo.listar()] == ["suelto", "pruebas", "html"]
	assert [a.nombre for a in catalogo.listar("web")] == ["html"]
	assert [a.nombre for a in catalogo.listar("")] == ["suelto"]
	assert [a.nombre for a in catalogo.listar(texto="PROGRAMACION")] == ["pruebas"]
	assert [a.nombre for a in catalogo.listar(texto="paginas")] == ["html"]
	html = catalogo.listar("Web")[0]
	catalogo.editar(html.id, nombre="  HTML  bonito ", descripcion="Hace\npáginas", categoria=" Diseño ", notas="  apunte \n")
	editado = catalogo.obtener(html.id)
	assert (editado.nombre, editado.descripcion, editado.categoria, editado.notas) == ("HTML bonito", "Hace páginas", "Diseño", "apunte")
	assert editado.modificado >= html.modificado
	with pytest.raises(ErrorCatalogo):
		catalogo.editar(html.id, nombre="   ")


def test_crear_nuevo(catalogo, tmp_path):
	identificador = catalogo.crear("Revisión de código", 'Revisa "todo" en C:\\proyectos', "Programación", "nota")
	agente = catalogo.obtener(identificador)
	carpeta = Path(agente.carpeta)
	assert carpeta == tmp_path / "creados" / "revision-de-codigo"
	assert agente.origen == "creado" and agente.version == "0.1.0" and agente.notas == "nota"
	ficha = servicio.leer_skill(carpeta)
	assert ficha.nombre == "revision-de-codigo"
	assert ficha.descripcion == 'Revisa "todo" en C:\\proyectos'
	assert ficha.categoria == "Programación" and ficha.version == "0.1.0"
	assert "## Instrucciones" in (carpeta / "SKILL.md").read_text(encoding="utf-8")
	segundo = catalogo.obtener(catalogo.crear("Revisión de código", "Otra"))
	assert Path(segundo.carpeta).name == "revision-de-codigo-2"
	with pytest.raises(ErrorCatalogo):
		catalogo.crear("   ", "x")


def test_crear_en_data_agentes_por_defecto(almacen):
	from app.core.paths import ruta_datos

	identificador = CatalogoAgentes(almacen).crear("Nuevo", "Descripción")
	assert Path(almacen.agente_catalogo(identificador).carpeta).parent == ruta_datos() / "agentes"


def test_eliminar_con_y_sin_borrar_carpeta(catalogo, tmp_path):
	escribir_skill(tmp_path / "ajeno", name="ajeno", description="De la persona")
	importado = catalogo.importar(servicio.leer_skill(tmp_path / "ajeno")).id
	# Un agente importado nunca borra su carpeta (y sigue en el catálogo si se pide).
	with pytest.raises(ErrorCatalogo):
		catalogo.eliminar(importado, borrar_carpeta=True)
	assert catalogo.obtener(importado) is not None
	assert catalogo.eliminar(importado) is False
	assert catalogo.obtener(importado) is None and (tmp_path / "ajeno" / "SKILL.md").is_file()
	creado = catalogo.crear("Mío", "Creado aquí")
	carpeta = Path(catalogo.obtener(creado).carpeta)
	conservado = catalogo.crear("Otro", "Creado aquí")
	assert catalogo.eliminar(conservado) is False and catalogo.obtener(conservado) is None
	assert catalogo.eliminar(creado, borrar_carpeta=True) is True
	assert not carpeta.exists() and catalogo.obtener(creado) is None
	assert catalogo.eliminar(9999) is False


def test_no_borra_carpetas_sospechosas(catalogo, monkeypatch):
	creado = catalogo.crear("Mío", "Creado aquí")
	agente = catalogo.obtener(creado)
	(Path(agente.carpeta) / "SKILL.md").unlink()
	assert "SKILL.md" in catalogo.se_puede_borrar_carpeta(agente)
	(Path(agente.carpeta) / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
	monkeypatch.setattr(servicio, "MAX_ENTRADAS_BORRAR", 0)
	assert "más de" in catalogo.se_puede_borrar_carpeta(agente)


def test_refrescar(catalogo, tmp_path):
	escribir_skill(tmp_path / "a", name="a", description="A", version="1.0")
	escribir_skill(tmp_path / "b", name="b", description="B")
	escribir_skill(tmp_path / "c", name="c", description="C")
	ids = {n: catalogo.importar(servicio.leer_skill(tmp_path / n)).id for n in "abc"}
	shutil.rmtree(tmp_path / "b")
	archivo_a = escribir_skill(tmp_path / "a", name="a", description="A nueva", version="1.1")
	futuro = time.time() + 60
	os.utime(archivo_a, (futuro, futuro))
	resultado = catalogo.refrescar()
	assert resultado.revisados == 3
	assert resultado.no_encontrados == [ids["b"]]
	assert [(c.id, c.descripcion_archivo) for c in resultado.cambiados] == [(ids["a"], "A nueva")]
	assert resultado.versiones == 1 and catalogo.obtener(ids["a"]).version == "1.1"
	assert not servicio.disponible(catalogo.obtener(ids["b"]))
	assert servicio.estado(catalogo.obtener(ids["c"])) == servicio.ESTADO_DISPONIBLE
	assert catalogo.aplicar_cambios(resultado.cambiados) == 1
	assert catalogo.obtener(ids["a"]).descripcion == "A nueva"


def test_refrescar_descartar_no_vuelve_a_preguntar(catalogo, tmp_path):
	archivo = escribir_skill(tmp_path / "a", name="a", description="A")
	identificador = catalogo.importar(servicio.leer_skill(tmp_path / "a")).id
	catalogo.editar(identificador, descripcion="Mi descripción")
	# Editar en el catálogo después del archivo: no se propone la del archivo.
	assert catalogo.refrescar().cambiados == []
	escribir_skill(tmp_path / "a", name="a", description="Otra del archivo")
	futuro = time.time() + 60
	os.utime(archivo, (futuro, futuro))
	cambios = catalogo.refrescar().cambiados
	assert len(cambios) == 1
	catalogo.almacen.actualizar_agente_catalogo(identificador, modificado=futuro + 1)
	assert catalogo.refrescar().cambiados == []
	assert catalogo.obtener(identificador).descripcion == "Mi descripción"


def test_datos_para_sesion(catalogo, almacen, tmp_path):
	escribir_skill(tmp_path / "a", name="a", description="A", version="2")
	escribir_skill(tmp_path / "b", name="b", description="B")
	a = catalogo.importar(servicio.leer_skill(tmp_path / "a"), "Cat").id
	b = catalogo.importar(servicio.leer_skill(tmp_path / "b")).id
	shutil.rmtree(tmp_path / "b")
	datos = servicio.datos_para_sesion(almacen, [b, a, 9999, a])
	assert [d["nombre"] for d in datos] == ["b", "a"]
	assert datos[1] == {
		"id": a, "nombre": "a", "descripcion": "A", "categoria": "Cat", "carpeta": str(tmp_path / "a"),
		"skill": str(tmp_path / "a" / "SKILL.md"), "existe": True, "version": "2",
	}
	assert datos[0]["existe"] is False and datos[0]["skill"].endswith("SKILL.md")


def test_exportar_e_importar_json(catalogo, almacen, tmp_path):
	escribir_skill(tmp_path / "a", name="a", description="A")
	catalogo.importar(servicio.leer_skill(tmp_path / "a"), "Cat")
	catalogo.crear("Nuevo", "Creado")
	ruta = tmp_path / "catalogo.json"
	assert catalogo.exportar_json(ruta) == 2
	otro_almacen = Almacen(tmp_path / "otro.db")
	try:
		otro = CatalogoAgentes(otro_almacen)
		resultado = otro.importar_json(ruta)
		assert (resultado.nuevos, resultado.omitidos) == (2, 0)
		assert {a.origen for a in otro.listar()} == {"importado", "creado"}
		assert otro.importar_json(ruta).omitidos == 2
		assert otro.importar_json(ruta, actualizar=True).actualizados == 2
	finally:
		otro_almacen.cerrar()
	malo = tmp_path / "malo.json"
	malo.write_text('{"formato": "otro"}', encoding="utf-8")
	with pytest.raises(ErrorCatalogo):
		catalogo.importar_json(malo)
	malo.write_text("no es json", encoding="utf-8")
	with pytest.raises(ErrorCatalogo):
		catalogo.importar_json(malo)


# ════════════════════════════════════════════════════════════════════════════
# Interfaz
# ════════════════════════════════════════════════════════════════════════════

wx = pytest.importorskip("wx")


@pytest.fixture
def anuncios(monkeypatch):
	"""Lo que se diría por voz o por avisos (texto)."""
	from app.ui import avisos
	from app.ui.accesibilidad import voz

	textos: list[str] = []
	monkeypatch.setattr(voz, "hablar", lambda texto, *a, **k: textos.append(texto))
	monkeypatch.setattr(avisos, "anunciar", lambda texto, *a, **k: textos.append(texto))
	return textos


@pytest.fixture
def gestor(app_wx, almacen, tmp_path, anuncios):
	from app.ui.dialogos.gestor_agentes import DialogoGestorAgentes

	dialogo = DialogoGestorAgentes(None, almacen, carpeta_creados=tmp_path / "creados")
	yield dialogo
	dialogo.Destroy()


def _rellenar(**campos):
	"""Respuesta para ``dialogo_modal``: escribe en los campos del diálogo y acepta."""
	def responder(dialogo):
		for nombre, valor in campos.items():
			control = getattr(dialogo, nombre)
			control.SetValue(valor)
		dialogo.aceptar()
		return wx.ID_OK
	return responder


def test_gestor_lista_columnas_estado_y_filtros(gestor, tmp_path):
	for nombre, categoria in (("html", "Web"), ("pruebas", "Programación")):
		escribir_skill(tmp_path / nombre, name=nombre, description=f"Descripción de {nombre}")
		gestor.catalogo.importar(servicio.leer_skill(tmp_path / nombre), categoria)
	shutil.rmtree(tmp_path / "html")
	gestor.recargar()
	assert gestor.lista.numero_filas() == 2
	assert gestor.lista.valores_fila(0) == ["pruebas", "Programación", "Descripción de pruebas", str(tmp_path / "pruebas"), "Disponible"]
	assert gestor.lista.valores_fila(1)[4] == "No encontrado"
	assert gestor.etiqueta.GetLabel() == "&Agentes (2):"
	assert list(gestor.campo_categoria.GetItems()) == ["Todas", "Sin categoría", "Programación", "Web"]
	gestor.campo_categoria.SetSelection(3)
	gestor.recargar()
	assert [a.nombre for a in gestor.agentes] == ["html"]
	gestor.campo_categoria.SetSelection(0)
	gestor.campo_buscar.SetValue("PRUEBAS")  # EVT_TEXT recarga
	assert [a.nombre for a in gestor.agentes] == ["pruebas"]


def test_gestor_menu_contextual(gestor, tmp_path):
	menu = gestor.lista.crear_menu_contextual()
	try:
		etiquetas = {i.GetItemLabelText(): i.IsEnabled() for i in menu.GetMenuItems() if not i.IsSeparator()}
	finally:
		menu.Destroy()
	assert etiquetas["Ver el SKILL.md"] is False  # lista vacía
	assert etiquetas["Añadir desde una carpeta..."] is True
	assert etiquetas["Crear uno nuevo..."] is True
	assert "Quitar del catálogo..." in etiquetas and "Refrescar" in etiquetas


def test_gestor_letras_del_menu_sin_repetir(gestor):
	menu = gestor._crear_menu()
	try:
		letras = [i.GetItemLabel().split("\t")[0].split("&")[1][0].lower() for i in menu.GetMenuItems() if not i.IsSeparator()]
	finally:
		menu.Destroy()
	assert len(letras) == len(set(letras))


def test_gestor_anadir_desde_carpeta(gestor, dialogo_modal, tmp_path, anuncios):
	escribir_skill(tmp_path / "busca" / "web" / "html", name="html", description="Páginas")
	escribir_skill(tmp_path / "busca" / "web" / "css", name="css", description="Estilos")
	escribir_skill(tmp_path / "busca" / "otro", name="otro", description="Otro")

	def elegir(dialogo):
		assert dialogo.lista.GetCount() == 3
		assert dialogo.lista.indices_marcados() == [0, 1, 2]
		otro = [f.nombre for f in dialogo.fichas].index("otro")
		dialogo.lista.Check(otro, False)
		css = [f.nombre for f in dialogo.fichas].index("css")
		dialogo.lista.SetSelection(css)
		dialogo.mostrar_elegido()
		assert dialogo.campo_categoria.GetValue() == "web"
		assert "Estilos" in dialogo.detalles.GetValue()
		dialogo.campo_categoria.SetValue("Diseño")
		assert "(categoría: Diseño)" in dialogo.lista.GetString(css)
		assert dialogo.lista.IsChecked(css)
		dialogo.aceptar()
		return wx.ID_OK

	dialogo_modal.responder(elegir)
	assert gestor.anadir_desde_carpeta(tmp_path / "busca") == 2
	assert {(a.nombre, a.categoria) for a in gestor.catalogo.listar()} == {("css", "Diseño"), ("html", "web")}
	assert anuncios[-1] == "2 agentes añadidos"

	# Otra vez: los que ya están salen sin marcar y con su aviso.
	def revisar(dialogo):
		textos = [dialogo.lista.GetString(i) for i in range(dialogo.lista.GetCount())]
		assert sum("(ya en el catálogo)" in t for t in textos) == 2
		assert len(dialogo.lista.indices_marcados()) == 1
		return wx.ID_CANCEL

	dialogo_modal.responder(revisar)
	assert gestor.anadir_desde_carpeta(tmp_path / "busca") == 0


def test_gestor_anadir_desde_carpeta_sin_agentes(gestor, dialogo_modal, tmp_path):
	(tmp_path / "vacia").mkdir()
	assert gestor.anadir_desde_carpeta(tmp_path / "vacia") == 0
	assert "ninguna carpeta con un archivo SKILL.md" in " ".join(dialogo_modal.ultimo.textos)


def test_gestor_encontrados_sin_marcar_no_cierra(app_wx, catalogo, tmp_path, dialogo_modal):
	from app.ui.dialogos.gestor_agentes import DialogoAgentesEncontrados

	escribir_skill(tmp_path / "a", name="a", description="A")
	dialogo = DialogoAgentesEncontrados(None, catalogo, servicio.buscar_agentes(tmp_path), tmp_path)
	try:
		dialogo.lista.marcar_todas(False)
		assert dialogo.aceptar() is False
		assert dialogo_modal.ultimo.titulo == "Ningún agente marcado"
		menu = dialogo.lista.crear_menu_contextual()
		assert [i.GetItemLabelText() for i in menu.GetMenuItems()][0] == "Marcar o desmarcar"
		menu.Destroy()
	finally:
		dialogo.Destroy()


def test_gestor_anadir_uno_editar_y_ver(gestor, dialogo_modal, tmp_path, monkeypatch, anuncios):
	from app.ui import visor_ayuda

	escribir_skill(tmp_path / "web" / "html", name="html", description="Páginas")
	dialogo_modal.responder(_rellenar(campo_nombre="HTML", campo_categoria="Web", campo_notas="apunte"))
	identificador = gestor.anadir_uno(tmp_path / "web" / "html")
	agente = gestor.catalogo.obtener(identificador)
	assert (agente.nombre, agente.categoria, agente.notas, agente.descripcion) == ("HTML", "Web", "apunte", "Páginas")
	assert gestor.actual().id == identificador

	# Ya está: se ofrece poner la descripción nueva del SKILL.md.
	escribir_skill(tmp_path / "web" / "html", name="html", description="Páginas nuevas")
	dialogo_modal.responder(wx.ID_YES)
	assert gestor.anadir_uno(tmp_path / "web" / "html") == identificador
	assert gestor.catalogo.obtener(identificador).descripcion == "Páginas nuevas"

	dialogo_modal.responder(_rellenar(campo_descripcion="Otra\ncosa"))
	assert gestor.editar() is True
	assert gestor.catalogo.obtener(identificador).descripcion == "Otra cosa"

	vistos = []
	monkeypatch.setattr(visor_ayuda, "mostrar_documento", lambda padre, titulo, texto, **k: vistos.append((titulo, texto)))
	assert gestor.ver_skill() is True
	assert vistos[0][0] == "HTML (SKILL.md)" and "Páginas nuevas" in vistos[0][1]

	dialogo_modal.responder(wx.ID_OK)
	assert gestor.anadir_uno(tmp_path / "no-existe") is None


def test_gestor_crear_nuevo(gestor, dialogo_modal, tmp_path):
	dialogo_modal.responder(_rellenar(campo_nombre="Revisor de textos", campo_descripcion="Revisa textos"), wx.ID_NO)
	identificador = gestor.crear_nuevo()
	agente = gestor.catalogo.obtener(identificador)
	assert Path(agente.carpeta) == tmp_path / "creados" / "revisor-de-textos"
	assert (Path(agente.carpeta) / "SKILL.md").is_file()
	assert dialogo_modal.ultimo.titulo == "Agente creado"
	# Sin descripción no se crea.
	dialogo_modal.responder(_rellenar(campo_nombre="Otro", campo_descripcion=""), wx.ID_CANCEL)
	assert gestor.crear_nuevo() is None


def test_gestor_eliminar(gestor, dialogo_modal, tmp_path, anuncios):
	escribir_skill(tmp_path / "ajeno", name="ajeno", description="A")
	gestor.catalogo.importar(servicio.leer_skill(tmp_path / "ajeno"))
	gestor.recargar()
	dialogo_modal.responder(wx.ID_NO)
	assert gestor.eliminar() is False and gestor.lista.numero_filas() == 1
	dialogo_modal.responder(wx.ID_YES)
	assert gestor.eliminar() is True and gestor.lista.numero_filas() == 0
	assert (tmp_path / "ajeno" / "SKILL.md").is_file()
	assert anuncios[-1] == "Agente quitado del catálogo"

	creado = gestor.catalogo.crear("Mío", "Creado")
	carpeta = Path(gestor.catalogo.obtener(creado).carpeta)
	gestor.recargar(creado)
	dialogo_modal.responder(wx.ID_YES, wx.ID_YES)
	assert gestor.eliminar() is True
	assert not carpeta.exists() and anuncios[-1] == "Agente quitado y carpeta borrada"
	assert "No se puede deshacer" in " ".join(dialogo_modal.ultimo.textos)


def test_gestor_confirmacion_de_quitar_tiene_no_por_defecto(gestor, tmp_path, monkeypatch):
	escribir_skill(tmp_path / "a", name="a", description="A")
	gestor.catalogo.importar(servicio.leer_skill(tmp_path / "a"))
	gestor.recargar()
	from app.ui.dialogos import gestor_agentes as modulo

	peligrosos = []

	def confirmar_falso(padre, mensaje, titulo="", con_cancelar=False, peligroso=False):
		peligrosos.append(peligroso)
		return wx.ID_NO

	monkeypatch.setattr(modulo, "confirmar", confirmar_falso)
	assert gestor.eliminar() is False
	# Intro en la confirmación es «No» (peligroso=True).
	assert peligrosos == [True]


def test_gestor_refrescar(gestor, dialogo_modal, tmp_path, anuncios):
	archivo = escribir_skill(tmp_path / "a", name="a", description="A")
	escribir_skill(tmp_path / "b", name="b", description="B")
	ida = gestor.catalogo.importar(servicio.leer_skill(tmp_path / "a")).id
	gestor.catalogo.importar(servicio.leer_skill(tmp_path / "b"))
	shutil.rmtree(tmp_path / "b")
	escribir_skill(tmp_path / "a", name="a", description="A nueva")
	futuro = time.time() + 60
	os.utime(archivo, (futuro, futuro))
	dialogo_modal.responder(wx.ID_YES)
	resultado = gestor.refrescar()
	assert len(resultado.cambiados) == 1 and gestor.catalogo.obtener(ida).descripcion == "A nueva"
	assert anuncios[-1] == "Catálogo revisado: 2 agentes, 1 no encontrados, 1 con descripción nueva"
	assert [gestor.lista.valores_fila(i)[4] for i in range(2)] == ["Disponible", "No encontrado"]


def test_gestor_exportar_importar(gestor, tmp_path, anuncios):
	gestor.catalogo.crear("Nuevo", "Creado")
	assert gestor.exportar(tmp_path / "c.json") == 1
	resultado = gestor.importar_catalogo(tmp_path / "c.json", actualizar=False)
	assert resultado.omitidos == 1


def test_gestor_teclas(gestor, monkeypatch):
	llamadas = []
	for nombre in ("editar", "refrescar", "crear_nuevo", "anadir_desde_carpeta", "anadir_uno", "editar_skill",
			"abrir_carpeta", "eliminar", "copiar_ruta"):
		monkeypatch.setattr(gestor, nombre, lambda n=nombre: llamadas.append(n))

	def pulsar(codigo, modificadores=wx.MOD_NONE, en_lista=True):
		monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: gestor.lista if en_lista else gestor.campo_buscar))
		evento = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
		evento.SetKeyCode(codigo)
		evento.SetControlDown(bool(modificadores & wx.MOD_CONTROL))
		evento.SetShiftDown(bool(modificadores & wx.MOD_SHIFT))
		gestor._al_pulsar(evento)

	pulsar(wx.WXK_F2)
	pulsar(wx.WXK_F5)
	pulsar(ord("N"), wx.MOD_CONTROL)
	pulsar(ord("O"), wx.MOD_CONTROL)
	pulsar(ord("I"), wx.MOD_CONTROL)
	pulsar(ord("E"), wx.MOD_CONTROL)
	pulsar(ord("E"), wx.MOD_CONTROL | wx.MOD_SHIFT)
	pulsar(wx.WXK_DELETE)
	pulsar(ord("C"), wx.MOD_CONTROL)
	pulsar(wx.WXK_DELETE, en_lista=False)  # en el cuadro de búsqueda, Supr borra texto
	assert llamadas == ["editar", "refrescar", "crear_nuevo", "anadir_desde_carpeta", "anadir_uno", "editar_skill",
		"abrir_carpeta", "eliminar", "copiar_ruta"]


def test_abrir_gestor_y_menu_de_la_ventana_principal(app_wx, almacen, dialogo_modal):
	from app.ui.dialogos.gestor_agentes import abrir_gestor
	from app.ui.ventana_principal import MENUS, VentanaPrincipal

	assert abrir_gestor(None, almacen) is False
	assert dialogo_modal.ultimo.titulo == "Gestor de agentes"
	herramientas = dict(MENUS)[next(t for t, _e in MENUS if "Herramientas" in t)]
	entrada = next(e for e in herramientas if e[0] == "gestor_agentes")
	assert entrada[1].endswith("\tCtrl+Alt+G") and entrada[3] == "accion_gestor_agentes"
	assert callable(VentanaPrincipal.accion_gestor_agentes)


# ── Elegir agentes para una sesión ────────────────────────────────────────────

@pytest.fixture
def con_agentes(catalogo, tmp_path):
	ids = {}
	for nombre, categoria in (("html", "Web"), ("css", "Web"), ("pruebas", "Programación"), ("suelto", "")):
		escribir_skill(tmp_path / "ag" / nombre, name=nombre, description=f"Hace {nombre}")
		ids[nombre] = catalogo.importar(servicio.leer_skill(tmp_path / "ag" / nombre), categoria).id
	return ids


def test_panel_agentes_marcar_filtrar_y_seleccionar(marco, almacen, con_agentes, anuncios):
	from app.ui.dialogos.elegir_agentes import PanelAgentes

	panel = PanelAgentes(marco, almacen, seleccionados=[con_agentes["css"]])
	textos = [panel.lista.GetString(i) for i in range(panel.lista.GetCount())]
	assert textos[0] == "suelto — Hace suelto"
	assert textos[1] == "pruebas (Programación) — Hace pruebas"
	assert panel.lista.GetName() == "Agentes para la sesión (4)"
	assert panel.seleccionados() == [con_agentes["css"]]
	# Filtrar a Programación y marcar «pruebas»: «css» sigue marcado aunque no se vea.
	panel.campo_categoria.SetSelection(list(panel.campo_categoria.GetItems()).index("Programación"))
	panel.cargar()
	assert panel.lista.GetCount() == 1
	panel.lista.SetSelection(0)
	assert panel.lista.alternar() is True
	assert anuncios[-1] == "pruebas (Programación) — Hace pruebas, marcada"
	assert set(panel.seleccionados()) == {con_agentes["css"], con_agentes["pruebas"]}
	# Ctrl+A no envía eventos: se recoge igualmente.
	panel.campo_categoria.SetSelection(0)
	panel.cargar()
	panel.lista.marcar_todas(True)
	assert set(panel.seleccionados()) == set(con_agentes.values())
	panel.seleccionar([con_agentes["html"], 9999])
	assert panel.seleccionados() == [con_agentes["html"]]
	assert panel.lista.indices_marcados() == [3]
	menu = panel.lista.crear_menu_contextual()
	assert [i.GetItemLabelText() for i in menu.GetMenuItems()][-1] == "Gestionar agentes..."
	menu.Destroy()


def test_panel_agentes_gestionar_recarga(marco, almacen, con_agentes, monkeypatch, tmp_path):
	from app.ui.dialogos import elegir_agentes as modulo

	panel = modulo.PanelAgentes(marco, almacen, seleccionados=[con_agentes["html"]], editor="")

	def gestor_falso(padre, almacen_recibido, editor):
		assert almacen_recibido is almacen
		almacen_recibido.borrar_agente_catalogo(con_agentes["suelto"])
		return True

	monkeypatch.setattr(modulo, "abrir_gestor", gestor_falso)
	assert panel.gestionar() is True
	assert panel.lista.GetCount() == 3
	assert panel.seleccionados() == [con_agentes["html"]]


def test_panel_agentes_busca_el_almacen_en_la_ventana(app_wx, almacen, con_agentes):
	from app.ui.dialogos.elegir_agentes import PanelAgentes, almacen_de, editor_de

	ventana = wx.Frame(None)
	try:
		ventana.almacen = almacen
		ventana.opcion = lambda clave, defecto=None: "notepad++.exe" if clave == "agentes.editor_externo" else defecto
		interior = wx.Panel(ventana)
		assert almacen_de(interior) is almacen
		assert editor_de(interior) == "notepad++.exe"
		panel = PanelAgentes(interior, con_filtro=False)
		assert panel.campo_categoria is None and panel.lista.GetCount() == 4
	finally:
		ventana.Destroy()


def test_panel_agentes_catalogo_vacio(marco, almacen):
	from app.ui.dialogos.elegir_agentes import PanelAgentes

	panel = PanelAgentes(marco, almacen)
	assert panel.lista.GetCount() == 0 and "vacío" in panel.lista.GetName()
	assert panel.seleccionados() == []


def test_elegir_agentes(app_wx, almacen, con_agentes, dialogo_modal):
	from app.ui.dialogos.elegir_agentes import elegir_agentes

	def marcar(dialogo):
		assert dialogo.GetTitle() == "Agentes de la sesión"
		dialogo.panel.lista.Check(1, True)  # «pruebas»
		dialogo.aceptar()
		return wx.ID_OK

	dialogo_modal.responder(marcar)
	assert elegir_agentes(None, [con_agentes["html"]], almacen) == [con_agentes["pruebas"], con_agentes["html"]]
	dialogo_modal.responder(wx.ID_CANCEL)
	assert elegir_agentes(None, [con_agentes["html"]], almacen) is None


# ── Lista de casillas ─────────────────────────────────────────────────────────

def test_casillas_anuncian_el_estado_y_formas_de_lectura(marco, anuncios):
	from app.ui.accesibilidad.casillas import CheckListBoxAccesible
	from app.ui.accesibilidad.composicion import LECTURA_ESTADO, LECTURA_ESTADO_NOMBRE

	lista = CheckListBoxAccesible(marco, "Opciones", ["Subtítulos", "Miniatura"], recordar_posicion=False)
	# El accesible propio se instala siempre (como en SocialRSS): rol de casilla y estado.
	assert lista.GetAccessible().GetRole(1) == (wx.ACC_OK, wx.ROLE_SYSTEM_CHECKBUTTON)
	eventos = []
	lista.Bind(wx.EVT_CHECKLISTBOX, lambda e: (eventos.append(e.GetInt()), e.Skip()))
	assert lista.alternar(1) is True
	assert anuncios[-1] == "Miniatura, marcada" and eventos == [1]
	lista.lectura = LECTURA_ESTADO_NOMBRE
	lista.alternar(1)
	assert anuncios[-1] == "No marcada, Miniatura"
	lista.lectura = LECTURA_ESTADO
	lista.alternar(0)
	assert anuncios[-1] == "Marcada"
	lista.anunciar_estado = False
	total = len(anuncios)
	lista.alternar(0)
	assert len(anuncios) == total
	lista.marcar_indices([1])
	assert lista.indices_marcados() == [1]


def test_casillas_espacio_sin_elegido_marca_el_primero(marco, anuncios):
	from app.ui.accesibilidad.casillas import CheckListBoxAccesible

	lista = CheckListBoxAccesible(marco, "Opciones", ["Uno", "Dos"], recordar_posicion=False)
	assert lista.GetSelection() == wx.NOT_FOUND
	evento = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
	evento.SetKeyCode(wx.WXK_SPACE)
	lista._al_pulsar(evento)
	assert lista.GetSelection() == 0 and lista.IsChecked(0)
	assert anuncios[-1] == "Uno, marcada"
	vacia = CheckListBoxAccesible(marco, "Vacía", [], recordar_posicion=False)
	assert vacia.alternar() is False and anuncios[-1] == "Lista vacía"


def test_casillas_sin_accesible_propio_y_configuracion(marco, monkeypatch):
	from app.ui.accesibilidad import casillas

	monkeypatch.setattr(casillas, "ANUNCIAR_ESTADO", True)
	monkeypatch.setattr(casillas, "LECTURA_ESTADO", casillas.LECTURA_NOMBRE_ESTADO)
	casillas.configurar(anunciar_estado=False, lectura="estado")
	lista = casillas.CheckListBoxAccesible(marco, "Opciones", ["A"], forzar_accesible=False, recordar_posicion=False)
	assert not lista.anunciar_estado and lista.lectura == "estado"
	assert not isinstance(lista.GetAccessible(), casillas._AccesibleCasillas)
	casillas.configurar(lectura="inventada")
	assert casillas.LECTURA_ESTADO == "estado"
