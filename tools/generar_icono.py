"""Genera el icono de Ctrl+IA (``data/icons/app.ico``) con varios tamaños.

Un cuadrado redondeado azul oscuro con «IA» en blanco: alto contraste y
reconocible en la barra de tareas. Uso: ``python tools/generar_icono.py``.
"""

from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path

import wx

DESTINO = Path(__file__).resolve().parent.parent / "data" / "icons" / "app.ico"
TAMANOS = (16, 24, 32, 48, 64, 128, 256)


def _png(lado: int) -> bytes:
	mapa = wx.Bitmap(lado, lado, 32)
	mapa.UseAlpha(True)
	dc = wx.MemoryDC(mapa)
	dc.SetBackground(wx.Brush(wx.Colour(0, 0, 0, 0)))
	dc.Clear()
	contexto = wx.GraphicsContext.Create(dc)
	contexto.SetBrush(wx.Brush(wx.Colour(18, 52, 110)))
	contexto.SetPen(wx.TRANSPARENT_PEN)
	radio = lado * 0.18
	contexto.DrawRoundedRectangle(0, 0, lado, lado, radio)
	fuente = wx.Font(wx.FontInfo(max(6, int(lado * 0.42))).Bold().FaceName("Segoe UI"))
	contexto.SetFont(fuente, wx.Colour(255, 255, 255))
	ancho, alto = contexto.GetTextExtent("IA")
	contexto.DrawText("IA", (lado - ancho) / 2, (lado - alto) / 2)
	del contexto
	dc.SelectObject(wx.NullBitmap)
	flujo = BytesIO()
	imagen = mapa.ConvertToImage()
	imagen.SaveFile(flujo, wx.BITMAP_TYPE_PNG)
	return flujo.getvalue()


def main() -> None:
	aplicacion = wx.App(False)
	imagenes = [_png(lado) for lado in TAMANOS]
	cabecera = struct.pack("<HHH", 0, 1, len(imagenes))
	desplazamiento = 6 + 16 * len(imagenes)
	entradas = b""
	for lado, datos in zip(TAMANOS, imagenes, strict=True):
		entradas += struct.pack("<BBBBHHII", lado % 256, lado % 256, 0, 0, 1, 32, len(datos), desplazamiento)
		desplazamiento += len(datos)
	DESTINO.parent.mkdir(parents=True, exist_ok=True)
	DESTINO.write_bytes(cabecera + entradas + b"".join(imagenes))
	del aplicacion
	print(f"Icono generado en {DESTINO}")


if __name__ == "__main__":
	main()
