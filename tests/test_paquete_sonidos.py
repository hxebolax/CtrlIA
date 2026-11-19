"""Importar un paquete de sonidos en ZIP a la carpeta propia. Nada suena ni se muestra."""

from __future__ import annotations

import io
import wave
import zipfile
from pathlib import Path

import pytest

from app.ui import avisos


def _wav() -> bytes:
	salida = io.BytesIO()
	with wave.open(salida, "wb") as archivo:
		archivo.setnchannels(1)
		archivo.setsampwidth(2)
		archivo.setframerate(8000)
		archivo.writeframes(b"\x00\x00" * 80)
	return salida.getvalue()


def _zip(ruta: Path, archivos: dict[str, bytes]) -> Path:
	with zipfile.ZipFile(ruta, "w") as paquete:
		for nombre, datos in archivos.items():
			paquete.writestr(nombre, datos)
	return ruta


def test_instala_solo_los_wav_con_nombre_de_sonido(tmp_path):
	destino = tmp_path / "propios"
	destino.mkdir()
	(destino / "otro.wav").write_bytes(b"mio")
	archivo = _zip(tmp_path / "paquete.zip", {
		"mi paquete/Respuesta.WAV": _wav(),
		"mi paquete/enviado.wav": _wav(),
		"mi paquete/leeme.txt": b"hola",
		"mi paquete/inventado.wav": _wav(),
		"mi paquete/nueva.wav": b"no es un wav",
	})
	instalados, descartados = avisos.importar_paquete_sonidos(archivo, destino)
	assert instalados == ["enviado", "respuesta"]
	assert sorted(descartados) == ["inventado.wav", "leeme.txt", "nueva.wav"]
	assert (destino / "respuesta.wav").read_bytes()[:4] == b"RIFF"
	assert (destino / "otro.wav").read_bytes() == b"mio"  # lo demás de la carpeta no se toca
	assert sorted(p.name for p in tmp_path.iterdir() if p.name != "data") == ["paquete.zip", "propios"]  # sin restos temporales


def test_un_zip_sin_sonidos_o_danado_se_rechaza_sin_tocar_nada(tmp_path):
	destino = tmp_path / "propios"
	with pytest.raises(ValueError):
		avisos.importar_paquete_sonidos(_zip(tmp_path / "vacio.zip", {"leeme.txt": b"x"}), destino)
	(tmp_path / "roto.zip").write_bytes(b"no es un zip")
	with pytest.raises(ValueError):
		avisos.importar_paquete_sonidos(tmp_path / "roto.zip", destino)
	assert not destino.exists()


def test_un_zip_demasiado_grande_se_rechaza(tmp_path, monkeypatch):
	monkeypatch.setattr(avisos, "MAX_PAQUETE_BYTES", 10)
	with pytest.raises(ValueError, match="demasiado grande"):
		avisos.importar_paquete_sonidos(_zip(tmp_path / "grande.zip", {"respuesta.wav": _wav()}), tmp_path / "p")
