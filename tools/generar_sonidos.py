"""Genera los sonidos de Ctrl+IA en ``data/sounds`` (WAV de 16 bits, mono, 44,1 kHz).

Son tonos breves y suaves, sintetizados aquí mismo para no depender de
archivos con licencia ajena. Cada uno tiene un carácter distinto para que se
reconozca sin mirar:

- ``enviado``: subida corta (tu mensaje ha salido).
- ``respuesta``: dos notas ascendentes (el agente ha terminado de responder).
- ``permiso``: tres pulsos (el agente te necesita).
- ``error``: dos notas graves descendentes.
- ``aviso``: una nota suave.
- ``nueva``: campanita (sesión nueva creada).
- ``herramienta``: toque muy corto y suave (el agente lee, busca o ejecuta).
- ``archivo``: dos toques cortos (el agente cambia un archivo).
- ``segundo_plano``: como ``respuesta``, más grave y bajo (otra sesión termina).
- ``detenido``: nota que baja (el turno se ha interrumpido).
- ``compactado``: tres notas suaves que bajan, como algo que se pliega (el agente
  ha resumido la conversación para liberar memoria).
- ``limite``: dos pulsos graves (te acercas al límite de uso de tu cuenta).

Si tienes tus propios sonidos, no hace falta tocar este archivo: ponlos en
``data/sounds/propios`` y elige el esquema «Propio» en Preferencias.

Uso: ``python tools/generar_sonidos.py``.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

FRECUENCIA_MUESTREO = 44100
CARPETA = Path(__file__).resolve().parent.parent / "data" / "sounds"


def _nota(frecuencia: float, duracion: float, volumen: float = 0.35) -> list[float]:
	muestras = int(FRECUENCIA_MUESTREO * duracion)
	ataque = int(FRECUENCIA_MUESTREO * 0.008)
	resultado = []
	for i in range(muestras):
		envolvente = min(1.0, i / ataque) if ataque else 1.0
		envolvente *= math.exp(-3.5 * i / muestras)
		valor = math.sin(2 * math.pi * frecuencia * i / FRECUENCIA_MUESTREO)
		valor += 0.25 * math.sin(4 * math.pi * frecuencia * i / FRECUENCIA_MUESTREO)
		resultado.append(valor * envolvente * volumen / 1.25)
	return resultado


def _silencio(duracion: float) -> list[float]:
	return [0.0] * int(FRECUENCIA_MUESTREO * duracion)


def _escribir(nombre: str, muestras: list[float]) -> None:
	CARPETA.mkdir(parents=True, exist_ok=True)
	with wave.open(str(CARPETA / f"{nombre}.wav"), "wb") as archivo:
		archivo.setnchannels(1)
		archivo.setsampwidth(2)
		archivo.setframerate(FRECUENCIA_MUESTREO)
		archivo.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, m)) * 32767)) for m in muestras))


def main() -> None:
	_escribir("enviado", _nota(660, 0.07) + _nota(880, 0.09))
	_escribir("respuesta", _nota(523.25, 0.12) + _silencio(0.02) + _nota(783.99, 0.2))
	_escribir("permiso", _nota(880, 0.08) + _silencio(0.05) + _nota(880, 0.08) + _silencio(0.05) + _nota(1046.5, 0.14))
	_escribir("error", _nota(311.13, 0.16, 0.4) + _nota(233.08, 0.26, 0.4))
	_escribir("aviso", _nota(698.46, 0.15, 0.25))
	_escribir("nueva", _nota(1046.5, 0.1, 0.25) + _nota(1318.5, 0.1, 0.25) + _nota(1568, 0.22, 0.25))
	_escribir("herramienta", _nota(1760, 0.035, 0.14))
	_escribir("archivo", _nota(1174.66, 0.04, 0.18) + _silencio(0.03) + _nota(1567.98, 0.05, 0.18))
	_escribir("segundo_plano", _nota(392, 0.1, 0.2) + _silencio(0.02) + _nota(587.33, 0.16, 0.2))
	_escribir("detenido", _nota(587.33, 0.09, 0.3) + _nota(440, 0.16, 0.3))
	_escribir("compactado", _nota(987.77, 0.06, 0.18) + _nota(783.99, 0.06, 0.18) + _nota(587.33, 0.12, 0.18))
	_escribir("limite", _nota(293.66, 0.12, 0.35) + _silencio(0.08) + _nota(293.66, 0.18, 0.35))
	print(f"Sonidos generados en {CARPETA}")


if __name__ == "__main__":
	main()
