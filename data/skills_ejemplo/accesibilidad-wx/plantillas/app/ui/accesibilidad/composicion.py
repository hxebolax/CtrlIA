"""Composición de los anuncios de lectura por columnas (lógica pura, sin wx).

Separar el FORMATO del mensaje del control y de la voz permite probarlo con
pytest sin abrir ventanas (ver ``tests/test_composicion.py``).

Reglas del agente «accesibilidad-wx»:

1. Las preferencias efectivas son las globales (Preferencias del usuario) Y
   las locales del control (una lista concreta puede suprimir, por ejemplo,
   el número de fila).
2. El respaldo se decide DESPUÉS de combinarlas: si no queda nada que decir,
   se dice el contenido. Nunca se emite una cadena vacía.
3. Una celda vacía se anuncia como «Sin datos», nunca como silencio.
4. Un campo protegido (contraseña, dato sensible) no se revela por el
   respaldo: se anuncia «Contenido no disponible».
5. El número de fila es 1 para la primera fila (el índice interno es 0).

PLANTILLA del agente «accesibilidad-wx».
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


@dataclass(frozen=True)
class PreferenciasLectura:
	"""Qué partes se anuncian al leer una celda."""

	numero_fila: bool = True
	cabecera: bool = True
	contenido: bool = True

	def combinar(self, otras: PreferenciasLectura) -> PreferenciasLectura:
		"""Preferencias efectivas: una parte se dice solo si ambas la permiten."""
		return PreferenciasLectura(
			numero_fila=self.numero_fila and otras.numero_fila,
			cabecera=self.cabecera and otras.cabecera,
			contenido=self.contenido and otras.contenido,
		)


def limpiar_para_voz(texto: str | None) -> str:
	"""Normaliza espacios y saltos de línea para que la voz no lea caracteres raros."""
	if not texto:
		return ""
	# El tabulador vertical (\v) que inserta Mayús+Intro en un TextCtrl rico se
	# lee como un símbolo extraño; los saltos de línea se convierten en espacios.
	for raro in ("\r\n", "\r", "\n", "\v", "\f", "\t"):
		texto = texto.replace(raro, " ")
	return " ".join(texto.split())


def componer_anuncio_celda(
	indice_fila: int,
	cabecera: str,
	valor: str | None,
	globales: PreferenciasLectura = PreferenciasLectura(),
	locales: PreferenciasLectura = PreferenciasLectura(),
	protegido: bool = False,
) -> str:
	"""Devuelve el texto que se anunciará para una celda. Nunca devuelve ``""``.

	:param indice_fila: índice interno (0 = primera fila).
	:param cabecera: texto de la cabecera de la columna.
	:param valor: contenido COMPLETO de la celda (no el recortado por Windows).
	:param globales: preferencias del usuario.
	:param locales: restricciones del control concreto.
	:param protegido: la columna contiene un dato que no debe leerse.
	"""
	efectivas = globales.combinar(locales)
	decir_contenido = efectivas.contenido
	if not (efectivas.numero_fila or efectivas.cabecera or efectivas.contenido):
		decir_contenido = True  # respaldo: nunca silencio

	partes: list[str] = []
	if efectivas.numero_fila:
		partes.append(_("Fila {numero}").format(numero=indice_fila + 1))
	cabecera_limpia = limpiar_para_voz(cabecera)
	if efectivas.cabecera and cabecera_limpia:
		partes.append(cabecera_limpia)
	if decir_contenido:
		if protegido:
			partes.append(_("Contenido no disponible"))
		else:
			partes.append(limpiar_para_voz(valor) or _("Sin datos"))
	return ", ".join(partes) if partes else _("Sin datos")


def componer_estado_casilla(nombre: str, marcada: bool) -> str:
	"""Texto del cambio de estado de una casilla: «Nombre, marcada»."""
	estado = _("marcada") if marcada else _("no marcada")
	return f"{limpiar_para_voz(nombre) or _('Elemento')}, {estado}"
