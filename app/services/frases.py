"""Texto de los agentes preparado para la voz: sin marcas de Markdown y por frases mientras llega.

- :func:`para_voz` quita lo que el lector diría como símbolos (``#``, ``*``,
  ``|``, vallas de código...) y, si se pide, cambia cada bloque de código por
  «Bloque de código.» (FastChat ``StripMarkdownForSpeech`` y ``PrepareForSpeech``).
- :class:`LectorFrases` suelta frases enteras según llega la respuesta, para
  oír las primeras sin esperar a que termine (BlindPilot ``SentenceStreamer``):
  una frase solo sale cuando ya ha llegado lo que va detrás de su signo de
  cierre, así nunca se lee media palabra («*.» seguido de «txt»), y un bloque
  de código abierto espera a cerrarse. Solo guarda lo pendiente: el coste no
  crece con la longitud de la respuesta.

Sin wx: se prueba sin ventanas.
"""

from __future__ import annotations

import re

#: Lo que se dice en lugar de un bloque de código con «avisar».
AVISO_CODIGO = "Bloque de código."

_VALLA = re.compile(r"^[ \t]*(```|~~~)", re.MULTILINE)
_BLOQUE = re.compile(r"(?ms)^[ \t]*(```|~~~)[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)")
_ENCABEZADO = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+")
_CITA = re.compile(r"(?m)^[ \t]*>[ \t]?")
_VINETA = re.compile(r"(?m)^([ \t]*)[-*+][ \t]+")
_REGLA = re.compile(r"(?m)^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$")
_ENLACE = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_ENFASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_CODIGO_EN_LINEA = re.compile(r"`+([^`\n]+)`+")
_SEPARADOR_TABLA = re.compile(r"(?m)^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$\n?")
_ESPACIOS = re.compile(r"[ \t]{2,}")
_LINEAS_VACIAS = re.compile(r"\n{3,}")

#: Abreviaturas tras las que un punto no acaba la frase (en minúsculas, sin el punto).
ABREVIATURAS = frozenset({
	"etc", "p. ej", "ej", "sr", "sra", "srta", "dr", "dra", "ud", "uds", "vd", "vds", "aprox", "pág", "págs",
	"núm", "nº", "art", "cap", "vol", "fig", "e.g", "i.e", "vs", "cf", "mr", "mrs", "ms", "no", "approx",
})


def para_voz(texto: str, avisar_codigo: bool = False) -> str:
	"""El texto sin marcas de Markdown, listo para el lector de pantalla.

	:param avisar_codigo: cada bloque de código se cambia por «Bloque de código.» en vez de leerse.
	"""
	if not texto:
		return ""
	if avisar_codigo:
		texto = _BLOQUE.sub(lambda _m: AVISO_CODIGO + "\n", texto)
	else:
		# Solo las vallas (con su lenguaje): el código se lee.
		texto = re.sub(r"(?m)^[ \t]*(```|~~~)[\w+.#-]*[ \t]*$\n?", "", texto)
	texto = _SEPARADOR_TABLA.sub("", texto)
	texto = _REGLA.sub("", texto)
	texto = _ENCABEZADO.sub("", texto)
	texto = _CITA.sub("", texto)
	texto = _VINETA.sub(r"\1", texto)
	texto = _ENLACE.sub(lambda m: m.group(1) or m.group(2), texto)
	texto = _CODIGO_EN_LINEA.sub(r"\1", texto)
	for _ in range(2):  # «***negrita y cursiva***»
		texto = _ENFASIS.sub(r"\2", texto)
	# Las tablas: cada «|» separa celdas; se leen como una lista con comas.
	lineas = []
	for linea in texto.split("\n"):
		if linea.count("|") >= 2:
			celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
			linea = ", ".join(c for c in celdas if c)
		lineas.append(linea.rstrip())
	texto = "\n".join(lineas)
	texto = _ESPACIOS.sub(" ", texto)
	texto = _LINEAS_VACIAS.sub("\n\n", texto)
	return texto.strip()


def _cierra_frase(texto: str, posicion: int) -> bool:
	"""Si el signo de ``posicion`` acaba la frase (no es «etc.», «1.5», «p. ej.» ni «archivo.txt»)."""
	signo = texto[posicion]
	if signo == "\n":
		return True
	siguiente = texto[posicion + 1] if posicion + 1 < len(texto) else ""
	if siguiente and not siguiente.isspace() and siguiente not in "\"'”’»)]":
		return False  # «1.5», «x.py», «a:b»
	if signo == ".":
		inicio = posicion
		while inicio > 0 and (texto[inicio - 1].isalnum() or texto[inicio - 1] in ".º"):
			inicio -= 1
		palabra = texto[inicio:posicion].lower()
		if palabra in ABREVIATURAS or (len(palabra) == 1 and palabra.isalpha()):
			return False
		if palabra.isdigit() and (inicio == 0 or texto[inicio - 1] == "\n") and len(palabra) <= 3:
			return False  # «1. Primer paso» en una lista numerada
	return True


class LectorFrases:
	"""Suelta frases completas de un texto que llega a trozos.

	``anadir`` devuelve las frases que ya se pueden decir; ``terminar`` el resto.
	``consumido`` es cuántos caracteres del texto original se han soltado ya
	(para que, al llegar el mensaje entero, solo se diga lo que falte).
	"""

	SIGNOS = ".!?:;…"

	def __init__(self, avisar_codigo: bool = False) -> None:
		self.avisar_codigo = avisar_codigo
		self._pendiente = ""
		self.consumido = 0

	def anadir(self, trozo: str) -> list[str]:
		if not trozo:
			return []
		self._pendiente += trozo
		frases: list[str] = []
		while True:
			corte = self._corte()
			if corte <= 0:
				break
			frase, self._pendiente = self._pendiente[:corte], self._pendiente[corte:]
			self.consumido += len(frase)
			dicho = para_voz(frase, self.avisar_codigo)
			if dicho:
				frases.append(dicho)
		return frases

	def terminar(self) -> str:
		"""Lo que quedaba sin soltar (al terminar el mensaje o empezar una herramienta)."""
		resto, self._pendiente = self._pendiente, ""
		self.consumido += len(resto)
		return para_voz(resto, self.avisar_codigo)

	def callar(self) -> None:
		"""Se ha detenido: lo pendiente ya no se dirá."""
		self.consumido += len(self._pendiente)
		self._pendiente = ""

	def _corte(self) -> int:
		"""Posición tras la primera frase completa de lo pendiente (0 si aún no hay ninguna)."""
		texto = self._pendiente
		# Un bloque de código abierto se dice entero (o se avisa) cuando se cierra.
		vallas = list(_VALLA.finditer(texto))
		limite = len(texto)
		if len(vallas) % 2 == 1:
			limite = vallas[-1].start()
		elif vallas:
			# Hay bloques cerrados: si la primera frase llega a uno, se corta tras su valla de cierre.
			primera = vallas[0].start()
			antes = self._buscar_fin(texto, 0, primera)
			if antes:
				return antes
			cierre = texto.find("\n", vallas[1].end())
			if cierre < 0:
				return 0  # falta el salto de línea tras la valla de cierre
			return cierre + 1
		return self._buscar_fin(texto, 0, limite)

	def _buscar_fin(self, texto: str, desde: int, hasta: int) -> int:
		for posicion in range(desde, hasta):
			caracter = texto[posicion]
			if caracter == "\n" or caracter in self.SIGNOS:
				# Hace falta lo que viene detrás para saber si acaba (no se lee media palabra).
				if posicion + 1 >= len(texto):
					return 0
				if _cierra_frase(texto, posicion):
					fin = posicion + 1
					while fin < len(texto) and texto[fin] in "\"'”’»)]":
						fin += 1
					if caracter != "\n" and fin >= len(texto):
						return 0
					if caracter != "\n" and texto[fin:fin + 1].strip():
						continue
					return fin
		return 0
