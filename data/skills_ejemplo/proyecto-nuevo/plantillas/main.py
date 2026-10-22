"""Punto de entrada de la aplicación.

El ORDEN de arranque importa (detalles en la referencia del agente):

1. ``multiprocessing.freeze_support()`` lo primero: en la versión compilada,
   un proceso hijo de multiprocessing arranca este mismo ``.exe``; si llegara
   a la comprobación de instancia única creería ser una segunda instancia.
2. Registro (solo biblioteca estándar) antes de importar wx y lo pesado: así
   hasta un fallo de importación queda escrito en ``data/logs``.
3. Configuración (valores por defecto en código; un archivo dañado no impide
   arrancar).
4. ``wx.App`` → ``wx.HelpProvider`` (Ctrl+H) → ``wx.Locale`` en español
   (guardando la referencia) → instancia única.
5. Anuncio de arranque por voz, SIN ventana de espera: una ventana visible
   mientras el hilo principal importa módulos pesados se marca como «no
   responde» y bloquea al lector de pantalla.
6. Importaciones pesadas, ventana principal y bucle de eventos.

Todo va envuelto en una red de seguridad: si el arranque falla, se escribe
``data/logs/arranque_fallido.log`` y se enseña un cartel (la versión
compilada no tiene consola donde ver nada).

Argumentos:

- ``--comprobar-arranque``: arranca sin mostrar la ventana y sale con código
  0 si todo fue bien. Sirve para pruebas automáticas y para comprobar el
  ``.exe`` recién compilado.
- Cualquier otro argumento se toma como archivo que abrir.
"""

from __future__ import annotations

import builtins
import logging
import sys
import time

# El GIL cede el turno cada 5 ms por defecto. Con hilos de fondo trabajando,
# el hilo de la interfaz tardaba en recuperarlo y el lector de pantalla (que
# pregunta a la interfaz en ese hilo) se quedaba mudo. Con 1 ms la interfaz
# responde enseguida; el coste es despreciable en una aplicación de escritorio.
sys.setswitchinterval(0.001)

log = logging.getLogger("app.main")

ARGUMENTO_COMPROBAR = "--comprobar-arranque"

# Objetos que deben vivir toda la sesión: si el recolector se llevara el
# wx.Locale, los botones volverían al inglés; si se llevara el comprobador de
# instancia, se liberaría el mutex.
_VIVOS: dict[str, object] = {}


def _(texto: str) -> str:
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def _mostrar_error_arranque(mensaje: str) -> None:
	"""Cartel de error aunque wx no esté disponible (último recurso)."""
	titulo = _("Error al iniciar")
	try:
		import wx

		if wx.GetApp() is None:
			wx.App(False)
		# MessageDialog y no MessageBox: este deja que el botón diga «Aceptar».
		dialogo = wx.MessageDialog(None, mensaje, titulo, wx.OK | wx.ICON_ERROR)
		dialogo.SetOKLabel(_("&Aceptar"))
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()
		return
	except Exception:  # noqa: BLE001 - si wx falla, se prueba el cartel nativo
		log.debug("No se pudo mostrar el cartel con wx", exc_info=True)
	try:
		import ctypes

		ctypes.windll.user32.MessageBoxW(0, mensaje, titulo, 0x10)
	except Exception:  # noqa: BLE001 - ya no queda dónde avisar salvo la consola
		if sys.__stderr__ is not None:
			print(mensaje, file=sys.__stderr__)


def _fijar_idioma():
	"""Pone wxWidgets en español (Aceptar, Cancelar, Sí, No...) y devuelve el ``wx.Locale``.

	En la versión compilada los catálogos de wx están en ``lib/wx/locale``;
	se añade esa ruta para que los encuentre también allí.
	"""
	from pathlib import Path

	import wx

	wx.Locale.AddCatalogLookupPathPrefix(str(Path(wx.__file__).resolve().parent / "locale"))
	idioma = wx.Locale(wx.LANGUAGE_SPANISH)
	if not idioma.IsOk():
		log.warning("No se pudo activar el idioma español de wxWidgets; se usa el del sistema.")
	return idioma


def _instalar_aviso_de_errores(ventana) -> None:
	"""Enseña al usuario los errores no controlados (como mucho uno cada 30 s)."""
	import wx

	from app.core import registro

	ultimo = [0.0]

	def mostrar(resumen: str) -> None:
		ahora = time.monotonic()
		if ahora - ultimo[0] < 30:
			return
		ultimo[0] = ahora
		padre = ventana if ventana and ventana.IsShown() else None
		from app.ui.dialogo_texto import mostrar_error

		mostrar_error(
			padre,
			_(
				"Se ha producido un error inesperado:\n\n{resumen}\n\n"
				"La aplicación intentará seguir funcionando. Los detalles están en "
				"Herramientas, Abrir la carpeta de registros."
			).format(resumen=resumen),
			_("Error inesperado"),
		)

	# CallAfter: el error puede ocurrir en un hilo y los diálogos solo se
	# pueden abrir desde el hilo principal.
	registro.establecer_aviso_usuario(lambda resumen: wx.CallAfter(mostrar, resumen))


def _principal(argumentos: list[str]) -> int:
	inicio = time.perf_counter()
	comprobar = ARGUMENTO_COMPROBAR in argumentos
	archivos = [a for a in argumentos if not a.startswith("--")]

	# 2. Registro (solo biblioteca estándar).
	from app.core import registro
	from app.core.version import APP_VERSION, ID_APLICACION, NOMBRE_APLICACION

	registro.configurar_registro(NOMBRE_APLICACION, APP_VERSION)

	# 3. Configuración.
	from app.core.config import Configuracion

	configuracion = Configuracion()
	nivel = str(configuracion.obtener("registro.nivel", "INFO")).upper()
	if nivel in ("DEBUG", "INFO", "WARNING", "ERROR") and not logging.getLogger().isEnabledFor(logging.DEBUG):
		logging.getLogger().setLevel(nivel)

	# 4. wx, ayuda contextual, idioma e instancia única.
	import wx

	# redirect=False: la salida estándar va al registro, no a una ventana de wx.
	aplicacion = wx.App(False)
	aplicacion.SetAppName(ID_APLICACION)
	wx.HelpProvider.Set(wx.SimpleHelpProvider())
	_VIVOS["idioma"] = _fijar_idioma()

	from app.core.instancia_unica import InstanciaUnica

	instancia = InstanciaUnica(ID_APLICACION)
	_VIVOS["instancia"] = instancia
	if instancia.hay_otra():
		log.info("Ya hay otra instancia en marcha; se le pide que se muestre.")
		if not instancia.pedir_mostrar(archivos):
			from app.ui.dialogo_texto import mostrar_mensaje

			mostrar_mensaje(None, _("{nombre} ya está en ejecución.").format(nombre=NOMBRE_APLICACION), NOMBRE_APLICACION)
		return 0

	# 5. Aviso por voz antes de lo pesado (el lector habla aunque estemos ocupados).
	if not comprobar and configuracion.obtener("general.anunciar_arranque", True):
		from app.core import voz

		voz.hablar(_("Iniciando {nombre}").format(nombre=NOMBRE_APLICACION), interrumpir=True)

	# 6. Importaciones pesadas y ventana principal.
	from pathlib import Path

	from app.ui.ventana_principal import VentanaPrincipal

	ventana = VentanaPrincipal(configuracion, instancia)
	_instalar_aviso_de_errores(ventana)
	if archivos:
		ventana.abrir_archivos([Path(a) for a in archivos])

	if comprobar:
		# Sin mostrar nada: se cierra en cuanto arranca el bucle de eventos.
		wx.CallAfter(ventana.Close, True)
	else:
		instancia.registrar_ventana(ventana.GetHandle())
		if not (configuracion.obtener("general.iniciar_en_bandeja", False) and ventana.ocultar_en_bandeja()):
			ventana.mostrar()

	log.info("Arranque completado en %.2f s.", time.perf_counter() - inicio)
	aplicacion.MainLoop()

	registro.establecer_aviso_usuario(None)
	instancia.liberar()
	log.info("Cierre normal.")
	return 0


def principal(argumentos: list[str] | None = None) -> int:
	"""Arranca la aplicación y garantiza que ningún fallo quede sin explicación."""
	import multiprocessing

	# 1. Imprescindible en la versión compilada si algo usa multiprocessing.
	multiprocessing.freeze_support()
	try:
		return _principal(sys.argv[1:] if argumentos is None else argumentos)
	except SystemExit:
		raise
	except BaseException as error:  # noqa: BLE001 - red de seguridad del arranque
		log.critical("Fallo no controlado al iniciar la aplicación.", exc_info=True)
		ruta = None
		try:
			from app.core import registro

			ruta = registro.registrar_fallo_de_arranque(error)
		except Exception:  # noqa: BLE001 - el propio registro puede ser lo que falla
			log.debug("No se pudo escribir arranque_fallido.log", exc_info=True)
		detalle = (
			_("Los detalles están en {ruta}.").format(ruta=ruta)
			if ruta
			else _("Compruebe que la carpeta «data» existe y no es de solo lectura.")
		)
		_mostrar_error_arranque(_("No se pudo iniciar la aplicación.") + f"\n\n{type(error).__name__}: {error}\n\n" + detalle)
		return 1


if __name__ == "__main__":
	sys.exit(principal())
