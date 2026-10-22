# Tareas de instalación del complemento (OPCIONAL).
# PLANTILLA del agente «complemento-nvda».
"""NVDA llama a estas funciones al instalar y al desinstalar el complemento.

- ``onInstall()`` se ejecuta tras descomprimir el complemento y antes de darlo
  por instalado. Si lanza una excepción, la instalación se cancela y NVDA lo
  indica. Sirve para migrar ajustes de una versión anterior o comprobar
  requisitos.
- ``onUninstall()`` se ejecuta al reiniciar NVDA tras pedir la desinstalación.
  No puede preguntar nada a la persona.

Ambas corren dentro de NVDA: deben ser rápidas y no abrir diálogos modales
largos. Si el complemento no necesita nada de esto, borra el archivo.
"""

from logHandler import log

# Si muestras mensajes traducidos, añade «import addonHandler» y
# «addonHandler.initTranslation()» antes de usar «_».


def onInstall():
	"""Deja constancia en el registro de NVDA; aquí iría una migración de ajustes."""
	log.info("Complemento de MiAplicacion: instalación en curso.")


def onUninstall():
	"""Nada que limpiar: el complemento no guarda archivos fuera de su carpeta."""
	log.info("Complemento de MiAplicacion: desinstalado.")
