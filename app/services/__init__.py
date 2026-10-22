"""Servicios: la lógica de la aplicación, SIN importar wx.

Aquí va lo que hace el trabajo (descargar, convertir, consultar una API...).
Al no depender de wx se prueba con pytest sin interfaz, y la ventana solo
llama a estos servicios desde hilos de fondo y pinta el resultado con
``wx.CallAfter``.
"""
