"""Punto de entrada de Ctrl+IA.

El ORDEN de arranque importa:

1. ``sys.setswitchinterval(0.001)``: con hilos de fondo leyendo a los agentes,
   el hilo de la interfaz recupera el GIL enseguida y el lector de pantalla no
   se queda mudo.
2. ``multiprocessing.freeze_support()`` lo primero.
3. Registro (solo biblioteca estándar) ANTES de importar wx: hasta un fallo de
   importación queda escrito en ``data/logs``.
4. Configuración e idioma (gettext).
5. ``wx.App`` → ayuda contextual (Ctrl+H) → ``wx.Locale`` (botones en
   español) → instancia única → aviso accesible de errores → vigilante de la
   interfaz.
6. Aviso por voz «Iniciando Ctrl+IA» SIN ventana de espera (una ventana que
   no responde mientras se importa bloquearía al lector de pantalla).
7. Ventana principal y bucle de eventos.

Argumentos:

- ``--comprobar-arranque``: arranca sin mostrar la ventana y sale con código 0
  (pruebas automáticas y comprobación del ``.exe`` recién compilado).
- ``--tras-actualizar``: lo pasa el actualizador al relanzar; se acepta.
- Una carpeta: abre «Nueva sesión» con esa carpeta.
"""

from __future__ import annotations

import logging
import sys
import time

sys.setswitchinterval(0.001)

log = logging.getLogger("app.main")

ARGUMENTO_COMPROBAR = "--comprobar-arranque"

# Objetos que deben vivir toda la sesión (el recolector no debe llevárselos).
_VIVOS: dict[str, object] = {}


def _(texto: str) -> str:
	import builtins

	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def _mostrar_error_arranque(mensaje: str) -> None:
	"""Cartel de error aunque wx no esté disponible (último recurso, sin otra salida posible)."""
	titulo = _("Error al iniciar Ctrl+IA")
	try:
		import wx

		if wx.GetApp() is None:
			wx.App(False)
		from app.ui.accesibilidad.dialogos import mostrar_error

		mostrar_error(None, mensaje, titulo)
		return
	except Exception:  # noqa: BLE001 - si wx falla, se prueba el cartel nativo
		log.debug("No se pudo mostrar el cartel con wx", exc_info=True)
	try:
		import ctypes

		ctypes.windll.user32.MessageBoxW(0, mensaje, titulo, 0x10)
	except Exception:  # noqa: BLE001 - ya no queda dónde avisar salvo la consola
		if sys.__stderr__ is not None:
			print(mensaje, file=sys.__stderr__)  # auditoria: ignorar print-en-app - último recurso si ni wx ni la ventana de Windows pueden mostrar el error


def _principal(argumentos: list[str]) -> int:
	inicio = time.perf_counter()
	comprobar = ARGUMENTO_COMPROBAR in argumentos
	rutas = [a for a in argumentos if not a.startswith("--")]

	# 3. Registro.
	from app.core import registro
	from app.core.version import APP_VERSION, ID_APLICACION, NOMBRE_APLICACION

	registro.configurar_registro(NOMBRE_APLICACION, APP_VERSION)

	# 4. Configuración e idioma.
	from app.core.configuracion import obtener_configuracion

	configuracion = obtener_configuracion()
	nivel = str(configuracion.obtener("registro.nivel", "INFO")).upper()
	if nivel != "INFO":
		registro.cambiar_nivel(nivel)
	from app.core import i18n

	idioma = i18n.inicializar(str(configuracion.obtener("general.idioma", "auto")))

	# 5. wx, ayuda contextual, idioma de wx, instancia única.
	import wx

	registro.registrar_version_wx()
	aplicacion = wx.App(False)
	aplicacion.SetAppName(ID_APLICACION)
	wx.HelpProvider.Set(wx.SimpleHelpProvider())
	_VIVOS["idioma"] = i18n.configurar_wx(idioma)
	# Tema claro u oscuro: antes de crear ninguna ventana.
	from app.ui.accesibilidad import visual

	visual.aplicar_tema(aplicacion, str(configuracion.obtener("ventana.tema", "claro")))

	from app.core.instancia_unica import InstanciaUnica

	instancia = InstanciaUnica(ID_APLICACION)
	_VIVOS["instancia"] = instancia
	if instancia.hay_otra():
		log.info("Ya hay otra instancia en marcha; se le pide que se muestre.")
		if not instancia.pedir_mostrar(rutas):
			from app.ui.accesibilidad.dialogos import mostrar_mensaje

			mostrar_mensaje(None, _("Ctrl+IA ya está abierto."), NOMBRE_APLICACION)
		return 0

	from app.ui import dialogo_error

	dialogo_error.instalar(NOMBRE_APLICACION)
	from app.core import vigilante_ui

	vigilante_ui.iniciar()

	# 6. Aviso por voz antes de lo pesado.
	from app.ui.accesibilidad import voz

	voz.configurar(retraso_ms=int(configuracion.obtener("anuncios.retardo_voz_ms", 120)))
	if not comprobar and configuracion.obtener("general.anunciar_arranque", True):
		voz.hablar(_("Iniciando Ctrl+IA"), interrumpir=True, sincrono=True)

	# 7. Ventana principal.
	from pathlib import Path

	from app.ui.ventana_principal import VentanaPrincipal

	with registro.operacion("crear la ventana principal"):
		ventana = VentanaPrincipal(configuracion, instancia)
	if rutas:
		ventana.abrir_archivos([Path(r) for r in rutas])

	if comprobar:
		wx.CallAfter(ventana.salir)
	else:
		instancia.registrar_ventana(ventana.GetHandle())
		ventana.mostrar()
		_actualizaciones_al_arrancar(ventana, configuracion)

	log.info("Arranque completado en %.2f s.", time.perf_counter() - inicio)
	aplicacion.MainLoop()

	vigilante_ui.detener()
	instancia.liberar()
	log.info("Cierre normal.")
	codigo = 0
	if comprobar:
		# La comprobación de la versión compilada prueba también lo que no se usa al arrancar.
		from app.services import actualizador

		fallo = actualizador.comprobar_componente_firmas()
		if fallo is not None:
			log.error("El verificador de firmas de las actualizaciones no funciona: %s", fallo)
			codigo = 3
	registro.revisar_volcados_no_mortales()
	registro.detener_registro()
	return codigo


def _actualizaciones_al_arrancar(ventana, configuracion) -> None:
	"""Si hay servidor de actualizaciones, revisa la anterior y busca una nueva (sin bloquear)."""
	try:
		from app.services import actualizador

		if not actualizador.URLS_MANIFIESTO:
			return
		from app.core.version import APP_VERSION
		from app.ui import avisos, dialogo_actualizacion

		config_act = actualizador.configuracion_por_defecto(APP_VERSION)
		dialogo_actualizacion.al_arrancar(ventana, config_act, avisos.anunciar)
		if configuracion.obtener("general.comprobar_actualizaciones", True):
			# Con «anunciar»: el progreso se dice por voz también cuando el aviso
			# de versión nueva llega por la comprobación automática.
			ventana._comprobador = dialogo_actualizacion.ComprobadorPeriodico(  # noqa: SLF001
				ventana, config_act, anunciar=avisos.anunciar
			)
			ventana._comprobador.iniciar()  # noqa: SLF001
	except Exception:  # noqa: BLE001 - las actualizaciones nunca impiden usar el programa
		log.exception("No se pudo preparar la comprobación de actualizaciones")


def principal(argumentos: list[str] | None = None) -> int:
	"""Arranca Ctrl+IA y garantiza que ningún fallo quede sin explicación."""
	import multiprocessing

	multiprocessing.freeze_support()
	try:
		return _principal(sys.argv[1:] if argumentos is None else argumentos)
	except SystemExit:
		raise
	except BaseException as error:  # noqa: BLE001 - red de seguridad del arranque
		log.critical("Fallo no controlado al iniciar Ctrl+IA.", exc_info=True)
		ruta = None
		try:
			from app.core import registro

			ruta = registro.registrar_fallo_de_arranque(error)
		except Exception:  # noqa: BLE001 - el propio registro puede ser lo que falla
			log.debug("No se pudo escribir arranque_fallido.log", exc_info=True)
		detalle = (
			_("Los detalles están en {ruta}.").format(ruta=ruta)
			if ruta
			else _("Comprueba que la carpeta «data» existe y no es de solo lectura.")
		)
		_mostrar_error_arranque(_("No se pudo iniciar Ctrl+IA.") + f"\n\n{type(error).__name__}: {error}\n\n" + detalle)
		return 1


if __name__ == "__main__":
	sys.exit(principal())
