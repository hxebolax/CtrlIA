"""Fusiona la plantilla .pot nueva con los .po de cada idioma, o crea uno nuevo.

Uso (desde la raíz del proyecto, después de ``extraer_cadenas.py``)::

	python tools/actualizar_po.py                 # actualiza todos los idiomas
	python tools/actualizar_po.py --nuevo en      # crea data/locales/en/LC_MESSAGES/<dominio>.po
	python tools/actualizar_po.py --purgar-obsoletas

Al fusionar (como ``msgmerge``, pero sin instalar gettext):

- Las traducciones existentes se conservan, junto con los comentarios de
  quien traduce; referencias y comentarios del código se renuevan.
- Los mensajes nuevos entran vacíos.
- Si un mensaje nuevo se parece mucho a uno que desapareció (se corrigió
  una errata en el original), se propone la traducción antigua marcada como
  «fuzzy» y con el texto anterior en ``#|``. No se compila hasta revisarla.
- Los mensajes que ya no están en el código quedan como obsoletos (``#~``)
  al final: no se compilan, pero la traducción no se pierde si vuelven.

Solo biblioteca estándar. PLANTILLA del agente «traducciones».
"""

from __future__ import annotations

import argparse
import copy
import difflib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
	from tools import catalogo_po  # importado como paquete (pruebas)
except ImportError:
	import catalogo_po  # ejecutado como script

RAIZ = Path(__file__).resolve().parent.parent

# Parecido mínimo (0 a 1) para proponer una traducción antigua como aproximada.
PARECIDO_MINIMO = 0.8

# Reglas de plural por idioma. Fuentes: manual de GNU gettext («Plural
# forms») y Unicode CLDR. Si falta un idioma, se deja el marcador y la
# herramienta de traducción (Poedit) lo pedirá.
FORMAS_PLURALES = {
	"en": "nplurals=2; plural=(n != 1);",
	"es": "nplurals=2; plural=(n != 1);",
	"ca": "nplurals=2; plural=(n != 1);",
	"gl": "nplurals=2; plural=(n != 1);",
	"eu": "nplurals=2; plural=(n != 1);",
	"de": "nplurals=2; plural=(n != 1);",
	"it": "nplurals=2; plural=(n != 1);",
	"nl": "nplurals=2; plural=(n != 1);",
	"pt": "nplurals=2; plural=(n != 1);",
	"sv": "nplurals=2; plural=(n != 1);",
	"da": "nplurals=2; plural=(n != 1);",
	"nb": "nplurals=2; plural=(n != 1);",
	"fi": "nplurals=2; plural=(n != 1);",
	"el": "nplurals=2; plural=(n != 1);",
	"hu": "nplurals=2; plural=(n != 1);",
	"tr": "nplurals=2; plural=(n != 1);",
	"fr": "nplurals=2; plural=(n > 1);",
	"pt_BR": "nplurals=2; plural=(n > 1);",
	"ru": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
	"uk": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
	"pl": "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
	"cs": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
	"sk": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
	"ro": "nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2);",
	"ar": "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5);",
	"ja": "nplurals=1; plural=0;",
	"zh": "nplurals=1; plural=0;",
	"ko": "nplurals=1; plural=0;",
	"vi": "nplurals=1; plural=0;",
}


@dataclass
class Resumen:
	"""Estado de un .po tras la fusión (lo que se muestra a quien traduce)."""

	traducidas: int = 0
	aproximadas: int = 0
	sin_traducir: int = 0
	obsoletas: int = 0

	def __str__(self) -> str:
		return (
			f"Traducidas: {self.traducidas}. Aproximadas (revisar): {self.aproximadas}. "
			f"Sin traducir: {self.sin_traducir}. Obsoletas: {self.obsoletas}."
		)


def regla_plural(idioma: str) -> str | None:
	return FORMAS_PLURALES.get(idioma) or FORMAS_PLURALES.get(idioma.split("_")[0])


def _nplurals(catalogo: catalogo_po.Catalogo) -> int:
	formas = catalogo.metadatos().get("Plural-Forms", "")
	try:
		return max(1, int(formas.split("nplurals=")[1].split(";")[0]))
	except (IndexError, ValueError):
		return 2


def _vacia_desde(plantilla: catalogo_po.Entrada, nplurals: int) -> catalogo_po.Entrada:
	entrada = copy.deepcopy(plantilla)
	entrada.msgstr = ""
	entrada.msgstr_plural = {i: "" for i in range(nplurals)} if entrada.es_plural else {}
	entrada.banderas = [b for b in entrada.banderas if b != "fuzzy"]
	entrada.previos = []
	return entrada


def _copiar_traduccion(destino: catalogo_po.Entrada, origen: catalogo_po.Entrada, nplurals: int) -> None:
	destino.comentarios_traductor = list(origen.comentarios_traductor)
	if destino.es_plural == origen.es_plural:
		destino.msgstr = origen.msgstr
		destino.msgstr_plural = dict(origen.msgstr_plural) if origen.es_plural else {}
	elif destino.es_plural:
		# Antes era simple y ahora es plural: la traducción sirve de punto de partida.
		destino.msgstr_plural = {i: origen.msgstr for i in range(nplurals)}
		destino.marcar_aproximada()
	else:
		destino.msgstr = origen.msgstr_plural.get(0, "")
		destino.marcar_aproximada()
	if origen.aproximada:
		destino.marcar_aproximada()
		destino.previos = list(origen.previos)


def _mas_parecida(
	entrada: catalogo_po.Entrada, candidatas: list[catalogo_po.Entrada]
) -> catalogo_po.Entrada | None:
	mejor, mejor_parecido = None, PARECIDO_MINIMO
	for candidata in candidatas:
		if candidata.msgctxt != entrada.msgctxt:
			continue
		comparador = difflib.SequenceMatcher(None, entrada.msgid, candidata.msgid)
		if comparador.real_quick_ratio() < mejor_parecido or comparador.quick_ratio() < mejor_parecido:
			continue
		parecido = comparador.ratio()
		if parecido >= mejor_parecido:
			mejor, mejor_parecido = candidata, parecido
	return mejor


def fusionar(
	po: catalogo_po.Catalogo, pot: catalogo_po.Catalogo, aproximadas: bool = True, purgar_obsoletas: bool = False
) -> Resumen:
	"""Actualiza ``po`` en memoria con los mensajes de ``pot``."""
	nplurals = _nplurals(po)
	anteriores = po.por_clave()
	usadas: set[tuple[str | None, str]] = set()
	resultado: list[catalogo_po.Entrada] = []
	sin_pareja: list[catalogo_po.Entrada] = []
	for plantilla in pot.entradas:
		nueva = _vacia_desde(plantilla, nplurals)
		previa = anteriores.get(plantilla.clave)
		if previa is not None:
			_copiar_traduccion(nueva, previa, nplurals)
			usadas.add(previa.clave)
		else:
			sin_pareja.append(nueva)
		resultado.append(nueva)
	if aproximadas:
		candidatas = [e for e in po.entradas if e.clave not in usadas and e.traducida]
		for nueva in sin_pareja:
			parecida = _mas_parecida(nueva, candidatas)
			if parecida is None:
				continue
			_copiar_traduccion(nueva, parecida, nplurals)
			nueva.marcar_aproximada()
			nueva.previos = [f'msgid "{catalogo_po.escapar(parecida.msgid)}"']
			usadas.add(parecida.clave)
			candidatas.remove(parecida)
	obsoletas: list[catalogo_po.Entrada] = []
	if not purgar_obsoletas:
		for entrada in po.entradas:
			if entrada.clave not in usadas and entrada.traducida:
				entrada.obsoleta = True
				entrada.referencias = []
				entrada.comentarios_extraidos = []
				entrada.previos = []
				obsoletas.append(entrada)
	po.entradas = resultado + obsoletas
	fecha = pot.metadatos().get("POT-Creation-Date")
	if fecha:
		po.fijar_metadato("POT-Creation-Date", fecha)
	return resumir(po)


def resumir(po: catalogo_po.Catalogo) -> Resumen:
	resumen = Resumen()
	for entrada in po.entradas:
		if entrada.obsoleta:
			resumen.obsoletas += 1
		elif entrada.aproximada:
			resumen.aproximadas += 1
		elif entrada.traducida:
			resumen.traducidas += 1
		else:
			resumen.sin_traducir += 1
	return resumen


def crear_po(pot: catalogo_po.Catalogo, idioma: str) -> tuple[catalogo_po.Catalogo, bool]:
	"""Catálogo vacío para un idioma nuevo. Devuelve (catálogo, ¿se conocía la regla de plural?)."""
	po = copy.deepcopy(pot)
	if po.cabecera is None:
		po.cabecera = catalogo_po.Entrada(msgid="")
	po.cabecera.marcar_aproximada(False)
	po.cabecera.comentarios_traductor = [f"Traducción al idioma «{idioma}».", "El idioma original de los textos es el español."]
	po.fijar_metadato("Language", idioma)
	po.fijar_metadato("PO-Revision-Date", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M%z"))
	po.fijar_metadato("Content-Type", "text/plain; charset=UTF-8")
	regla = regla_plural(idioma)
	if regla:
		po.fijar_metadato("Plural-Forms", regla)
	nplurals = _nplurals(po)
	po.entradas = [_vacia_desde(e, nplurals) for e in po.entradas]
	return po, regla is not None


def main(argumentos: list[str] | None = None) -> int:
	analizador = argparse.ArgumentParser(description="Actualiza los .po con la plantilla .pot o crea uno nuevo.")
	analizador.add_argument("--raiz", type=Path, default=RAIZ, help="Raíz del proyecto.")
	analizador.add_argument("--dominio", help="Dominio gettext (por defecto, DOMINIO de app/core/i18n.py).")
	analizador.add_argument("--nuevo", metavar="IDIOMA", help="Crea el .po de un idioma nuevo (en, fr, pt_BR...).")
	analizador.add_argument("--sin-aproximadas", action="store_true", help="No propone traducciones de mensajes parecidos.")
	analizador.add_argument("--purgar-obsoletas", action="store_true", help="Elimina las entradas obsoletas (#~).")
	opciones = analizador.parse_args(argumentos)
	catalogo_po.preparar_consola()
	raiz = opciones.raiz.resolve()
	dominio = opciones.dominio or catalogo_po.dominio_del_proyecto(raiz)
	locales = raiz / "data" / "locales"
	ruta_pot = locales / f"{dominio}.pot"
	if not ruta_pot.is_file():
		print(f"No existe {ruta_pot}. Ejecute antes: python tools/extraer_cadenas.py")
		return 1
	try:
		pot = catalogo_po.leer_po(ruta_pot)
	except (OSError, UnicodeDecodeError, catalogo_po.ErrorPO) as error:
		print(f"Error al leer la plantilla: {error}")
		return 1
	if opciones.nuevo:
		idioma = opciones.nuevo.replace("-", "_")
		destino = locales / idioma / "LC_MESSAGES" / f"{dominio}.po"
		if destino.exists():
			print(f"Ya existe {destino}; no se sobrescribe. Ejecute sin --nuevo para actualizarlo.")
			return 1
		po, regla_conocida = crear_po(pot, idioma)
		catalogo_po.escribir_po(po, destino)
		print(f"Creado {destino} con {len(po.entradas)} mensajes por traducir.")
		if not regla_conocida:
			print("Aviso: no se conoce la regla de plural de ese idioma; complete «Plural-Forms» en la cabecera.")
		return 0
	archivos = sorted(locales.glob(f"*/LC_MESSAGES/{dominio}.po"))
	if not archivos:
		print("No hay traducciones todavía. Cree una con: python tools/actualizar_po.py --nuevo en")
		return 0
	codigo = 0
	for archivo in archivos:
		try:
			po = catalogo_po.leer_po(archivo)
		except (OSError, UnicodeDecodeError, catalogo_po.ErrorPO) as error:
			print(f"{archivo}: error al leer, no se modifica. {error}")
			codigo = 1
			continue
		resumen = fusionar(po, pot, aproximadas=not opciones.sin_aproximadas, purgar_obsoletas=opciones.purgar_obsoletas)
		catalogo_po.escribir_po(po, archivo)
		print(f"{archivo.parent.parent.name}: {resumen}")
	return codigo


if __name__ == "__main__":
	sys.exit(main())
