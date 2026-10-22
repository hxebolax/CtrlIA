"""Palabras curiosas para la barra de estado mientras el agente escribe (AIChat #314).

Solo por gusto y desactivado de fábrica (Preferencias, Anuncios): en lugar de
«Claude está escribiendo...», la barra de estado dice «Claude: Cavilando…» y la
palabra cambia cada pocos segundos. Nunca se dice por voz ni interrumpe nada:
Alt+4 sigue diciendo «está escribiendo» y el estado de verdad está en «Estado
del turno» (Control+Mayúsculas+T).

La lista es propia de Ctrl+IA (no es la de Claude Code).
"""

from __future__ import annotations

import random
from collections.abc import Callable

#: Cada cuánto cambia la palabra (milisegundos).
INTERVALO_MS = 3500

#: Gerundios (y alguna expresión) para «el agente está en ello».
PALABRAS: tuple[str, ...] = (
	"Cavilando…", "Tramando…", "Hilvanando…", "Rumiando…", "Urdiendo…", "Maquinando…", "Barruntando…",
	"Elucubrando…", "Pergeñando…", "Fraguando…", "Entretejiendo…", "Destilando…", "Amasando…", "Sopesando…",
	"Husmeando…", "Escudriñando…", "Zurciendo…", "Bordando…", "Puliendo…", "Cincelando…", "Calibrando…",
	"Ensamblando…", "Enhebrando…", "Trasteando…", "Apañando…", "Afinando…", "Macerando…", "Desenredando…",
	"Ovillando…", "Garabateando…", "Rebuscando…", "Conjeturando…", "Engarzando…", "Atando cabos…",
	"Echando cuentas…", "Dándole vueltas…", "Hurgando…", "Tejiendo…", "Aderezando…", "Remendando…",
	"Zascandileando…",
)


def siguiente(anterior: str = "", elegir: Callable[[tuple[str, ...]], str] = random.choice) -> str:
	"""Una palabra al azar distinta de ``anterior``."""
	candidatas = tuple(p for p in PALABRAS if p != anterior) or PALABRAS
	return elegir(candidatas)
