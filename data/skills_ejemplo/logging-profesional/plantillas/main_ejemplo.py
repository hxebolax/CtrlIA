"""Ejemplo de punto de entrada con el registro bien integrado.

El ORDEN importa:

1. Configurar el registro lo primero (antes de importar wx y lo pesado): así
   hasta un fallo de importación queda escrito.
2. Crear ``wx.App`` y, justo después, el aviso accesible de errores, el
   vigilante de congelaciones y la versión de wx en la cabecera.
3. Envolver TODO en una red de seguridad que, si el arranque muere, lo
   escriba en ``arranque_fallido.log`` y se lo diga al usuario con un cartel
   (en la versión compilada no hay consola donde ver nada).

PLANTILLA del agente «logging-profesional». Adáptala al main.py del proyecto;
no sustituyas un main.py existente a ciegas.
"""

from __future__ import annotations

import logging
import sys

# Cambios de hilo del GIL más frecuentes: la interfaz (y el lector de pantalla)
# recupera antes el control aunque haya hilos de fondo trabajando.
sys.setswitchinterval(0.001)

log = logging.getLogger("app.main")


def _mostrar_error_arranque(mensaje: str) -> None:
	"""Cartel de error aunque wx no esté disponible (último recurso)."""
	try:
		import wx

		if wx.GetApp() is None:
			wx.App(False)
		# MessageDialog y no MessageBox: este deja que el botón diga «Aceptar».
		dialogo = wx.MessageDialog(None, mensaje, "Error al iniciar", wx.OK | wx.ICON_ERROR)
		dialogo.SetOKLabel("&Aceptar")
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()
		return
	except Exception:  # noqa: BLE001 - si wx falla, se prueba el cartel nativo
		pass
	try:
		import ctypes

		ctypes.windll.user32.MessageBoxW(0, mensaje, "Error al iniciar", 0x10)
	except Exception:  # noqa: BLE001 - ya no queda dónde avisar
		pass


def _principal() -> int:
	from app.core import registro
	from app.core.version import APP_VERSION, NOMBRE_APLICACION

	registro.configurar_registro(nombre_app=NOMBRE_APLICACION, version=APP_VERSION, nivel="INFO")

	import wx

	registro.registrar_version_wx()
	aplicacion = wx.App(False)

	# Ayuda contextual (Ctrl+H): sin proveedor, SetHelpText no guarda nada.
	wx.HelpProvider.Set(wx.SimpleHelpProvider())
	# Botones estándar en español (Aceptar, Cancelar, Sí, No...). La referencia
	# debe vivir toda la sesión o se pierde la traducción.
	idioma = wx.Locale(wx.LANGUAGE_SPANISH)  # noqa: F841

	from app.core import vigilante_ui
	from app.ui import dialogo_error

	dialogo_error.instalar(nombre_app=NOMBRE_APLICACION)
	vigilante_ui.iniciar()

	with registro.operacion("arranque", nivel_fin=logging.INFO):
		from app.ui.ventana_principal import VentanaPrincipal

		ventana = VentanaPrincipal()
		ventana.Show()

	aplicacion.MainLoop()
	vigilante_ui.detener()
	log.info("Cierre normal.")
	return 0


def principal() -> int:
	"""Arranca la aplicación y garantiza que ningún fallo quede sin explicación."""
	import multiprocessing

	# Imprescindible en la versión compilada si se usa multiprocessing.
	multiprocessing.freeze_support()
	try:
		return _principal()
	except SystemExit:
		raise
	except BaseException as error:  # noqa: BLE001 - red de seguridad del arranque
		ruta = None
		try:
			from app.core import registro

			log.critical("Fallo no controlado al iniciar la aplicación.", exc_info=True)
			ruta = registro.registrar_fallo_de_arranque(error)
		except Exception:  # noqa: BLE001 - el propio registro puede ser lo que falla
			pass
		_mostrar_error_arranque(
			"No se pudo iniciar la aplicación.\n\n"
			f"{type(error).__name__}: {error}\n\n"
			+ (f"Los detalles están en {ruta}." if ruta else "Compruebe que la carpeta «data» no es de solo lectura.")
		)
		return 1


if __name__ == "__main__":
	sys.exit(principal())
