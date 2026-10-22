"""Instancia única: una segunda copia pide a la primera que se muestre y termina.

Cómo funciona:

1. ``wx.SingleInstanceChecker`` (un mutex con nombre en Windows) dice si ya
   hay otra instancia. El nombre incluye el usuario y una huella de la carpeta
   ``data``: dos copias portables en carpetas distintas tienen datos
   distintos y pueden convivir; dos arranques de la MISMA copia, no.
2. La primera instancia, al crear su ventana, deja en el buzón
   (``data/temp/instancia``) un ``instancia.json`` con su PID y su HWND.
3. La segunda instancia no intenta manipular la ventana ajena (si la primera
   está oculta en la bandeja, solo ella sabe restaurarse bien: quitar el
   icono, enfocar la lista...). Hace dos cosas y sale:
   - ``AllowSetForegroundWindow(pid_de_la_primera)``: la segunda la acaba de
     lanzar el usuario, así que tiene derecho a poner ventanas en primer
     plano, y se lo CEDE a la primera. Sin esto, Windows solo hace parpadear
     el botón de la barra de tareas.
   - Deja una orden ``mostrar`` en el buzón (escritura atómica).
4. La primera revisa el buzón con un ``wx.Timer`` y, al ver la orden, se
   restaura con su propio código y llama a ``SetForegroundWindow``.

No se usa el truco de simular la tecla Alt con ``keybd_event`` para saltarse
el bloqueo de primer plano: envía una pulsación falsa que puede llegar al
lector de pantalla o a la ventana que el usuario estaba usando.

Las funciones del buzón no dependen de wx, para poder probarlas sin interfaz.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core import paths

log = logging.getLogger(__name__)

# Órdenes que la instancia viva acepta. Cualquier otra se descarta.
ORDEN_MOSTRAR = "mostrar"
ORDEN_ABRIR = "abrir"
ORDENES_VALIDAS = frozenset({ORDEN_MOSTRAR, ORDEN_ABRIR})
# Una orden más vieja es basura de un cierre brusco: no debe abrir la ventana
# «de la nada» mucho después.
MAX_EDAD_SEGUNDOS = 60
ARCHIVO_INSTANCIA = "instancia.json"


@dataclass
class Orden:
	"""Petición que una segunda instancia deja para la instancia viva."""

	orden: str
	argumentos: list[str] = field(default_factory=list)
	creado: float = 0.0


def carpeta_buzon() -> Path:
	"""Carpeta del buzón (``data/temp/instancia``): nunca se distribuye."""
	carpeta = paths.ruta_temporales() / "instancia"
	carpeta.mkdir(parents=True, exist_ok=True)
	return carpeta


def nombre_exclusion(id_aplicacion: str, ruta_datos: Path | None = None) -> str:
	"""Nombre del mutex de instancia única: aplicación + usuario + huella de ``data``.

	Solo letras, números y guiones: el nombre de un objeto del núcleo no admite
	barras invertidas y un nombre de usuario puede traer cualquier carácter.
	"""
	ruta = str((ruta_datos or paths.ruta_datos()).resolve()).lower()
	huella = hashlib.sha1(ruta.encode("utf-8")).hexdigest()[:10]
	try:
		usuario = getpass.getuser()
	except Exception:  # noqa: BLE001 - sin usuario conocido basta con la huella
		log.debug("No se pudo obtener el nombre de usuario", exc_info=True)
		usuario = "usuario"
	limpio = re.sub(r"[^A-Za-z0-9-]+", "_", f"{id_aplicacion}-{usuario}")
	return f"{limpio}-{huella}"


def _escribir_atomico(ruta: Path, datos: dict) -> None:
	"""Escribe JSON en un temporal y lo renombra: el lector nunca ve medio archivo."""
	temporal = ruta.with_name(ruta.name + ".tmp")
	temporal.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
	os.replace(temporal, ruta)


def dejar_orden(orden: str, argumentos: list[str] | None = None, carpeta: Path | None = None) -> bool:
	"""Deja una orden en el buzón. Devuelve ``True`` si quedó escrita."""
	if orden not in ORDENES_VALIDAS:
		log.warning("Orden de instancia desconocida: %s", orden)
		return False
	carpeta = carpeta or carpeta_buzon()
	datos = {"orden": orden, "argumentos": list(argumentos or []), "creado": time.time()}
	try:
		carpeta.mkdir(parents=True, exist_ok=True)
		_escribir_atomico(carpeta / f"orden_{time.time_ns()}_{uuid.uuid4().hex[:8]}.json", datos)
		return True
	except OSError:
		log.exception("No se pudo dejar la orden %s en el buzón", orden)
		return False


def recoger_ordenes(carpeta: Path | None = None, ahora: float | None = None) -> list[Orden]:
	"""Recoge y BORRA las órdenes pendientes, de la más antigua a la más reciente.

	Las ilegibles, desconocidas o caducadas se borran sin atenderlas. Si una no
	se puede borrar, tampoco se atiende: mejor perderla que atenderla dos veces.
	"""
	carpeta = carpeta or carpeta_buzon()
	if not carpeta.is_dir():
		return []
	momento = time.time() if ahora is None else ahora
	ordenes: list[Orden] = []
	for ruta in sorted(carpeta.glob("orden_*.json")):
		try:
			datos = json.loads(ruta.read_text(encoding="utf-8"))
		except (OSError, ValueError):
			log.debug("Orden ilegible en el buzón: %s", ruta.name, exc_info=True)
			datos = None
		try:
			ruta.unlink()
		except OSError:
			log.debug("No se pudo borrar la orden %s; no se atiende", ruta.name, exc_info=True)
			continue
		if not isinstance(datos, dict) or datos.get("orden") not in ORDENES_VALIDAS:
			continue
		try:
			creado = float(datos.get("creado", 0))
		except (TypeError, ValueError):
			creado = 0.0
		if creado <= 0 or momento - creado > MAX_EDAD_SEGUNDOS:
			log.info("Orden caducada descartada: %s", datos.get("orden"))
			continue
		argumentos = [str(a) for a in datos.get("argumentos", []) if isinstance(a, str)]
		ordenes.append(Orden(datos["orden"], argumentos, creado))
	return ordenes


# ── Primer plano en Windows (user32) ─────────────────────────────────────────


def _user32():
	"""Devuelve user32 con los tipos declarados, o ``None`` fuera de Windows.

	Declarar ``argtypes`` es obligatorio en 64 bits: un HWND es un puntero y,
	sin declararlo, ctypes lo pasaría como entero de 32 bits.
	"""
	if not sys.platform.startswith("win"):
		return None
	import ctypes
	from ctypes import wintypes

	user32 = ctypes.WinDLL("user32", use_last_error=True)
	user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
	user32.AllowSetForegroundWindow.restype = wintypes.BOOL
	user32.IsWindow.argtypes = [wintypes.HWND]
	user32.IsWindow.restype = wintypes.BOOL
	user32.IsIconic.argtypes = [wintypes.HWND]
	user32.IsIconic.restype = wintypes.BOOL
	user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
	user32.ShowWindow.restype = wintypes.BOOL
	user32.SetForegroundWindow.argtypes = [wintypes.HWND]
	user32.SetForegroundWindow.restype = wintypes.BOOL
	user32.GetForegroundWindow.argtypes = []
	user32.GetForegroundWindow.restype = wintypes.HWND
	return user32


def permitir_primer_plano(pid: int) -> bool:
	"""Cede al proceso ``pid`` el permiso de ponerse en primer plano (segunda instancia)."""
	user32 = _user32()
	if user32 is None or pid <= 0:
		return False
	resultado = bool(user32.AllowSetForegroundWindow(pid))
	if not resultado:
		import ctypes

		log.debug("AllowSetForegroundWindow(%s) falló (error %s)", pid, ctypes.get_last_error())
	return resultado


def traer_al_frente(hwnd: int) -> bool:
	"""Restaura y pone en primer plano la ventana ``hwnd``. ``True`` si quedó delante.

	La llama la PROPIA instancia viva sobre su ventana, tras recibir la orden
	y el permiso cedido por la segunda instancia.
	"""
	user32 = _user32()
	if user32 is None or not hwnd or not user32.IsWindow(hwnd):
		return False
	sw_restore, sw_show = 9, 5
	user32.ShowWindow(hwnd, sw_restore if user32.IsIconic(hwnd) else sw_show)
	user32.SetForegroundWindow(hwnd)
	return user32.GetForegroundWindow() == hwnd


# ── La instancia ─────────────────────────────────────────────────────────────


class InstanciaUnica:
	"""Comprobador de instancia única con buzón para comunicarse con la primera."""

	def __init__(self, id_aplicacion: str, carpeta: Path | None = None) -> None:
		"""Crea el mutex. Debe existir ya ``wx.App``.

		:param id_aplicacion: identificador sin espacios (``ID_APLICACION``).
		:param carpeta: buzón alternativo (pruebas).
		"""
		import wx

		self._carpeta = carpeta or carpeta_buzon()
		self.nombre = nombre_exclusion(id_aplicacion)
		self._comprobador: wx.SingleInstanceChecker | None = wx.SingleInstanceChecker(self.nombre)

	@property
	def carpeta(self) -> Path:
		"""Carpeta del buzón."""
		return self._carpeta

	def hay_otra(self) -> bool:
		"""``True`` si otra instancia (de esta misma carpeta ``data``) ya está en marcha."""
		return bool(self._comprobador is not None and self._comprobador.IsAnotherRunning())

	def registrar_ventana(self, hwnd: int) -> None:
		"""La PRIMERA instancia publica su PID y el HWND de su ventana principal."""
		try:
			self._carpeta.mkdir(parents=True, exist_ok=True)
			_escribir_atomico(self._carpeta / ARCHIVO_INSTANCIA, {"pid": os.getpid(), "hwnd": int(hwnd)})
		except OSError:
			log.exception("No se pudo registrar la ventana de la instancia")

	def leer_primera(self) -> dict | None:
		"""Datos (``pid``, ``hwnd``) que publicó la primera instancia, si los hay."""
		ruta = self._carpeta / ARCHIVO_INSTANCIA
		try:
			datos = json.loads(ruta.read_text(encoding="utf-8"))
		except FileNotFoundError:
			return None
		except (OSError, ValueError):
			log.debug("No se pudo leer %s", ruta, exc_info=True)
			return None
		return datos if isinstance(datos, dict) else None

	def pedir_mostrar(self, argumentos: list[str] | None = None) -> bool:
		"""SEGUNDA instancia: cede el primer plano y pide a la primera que se muestre.

		Con ``argumentos`` (por ejemplo, archivos recibidos por línea de órdenes)
		deja además una orden ``abrir`` para que la primera los atienda.
		"""
		primera = self.leer_primera() or {}
		try:
			permitir_primer_plano(int(primera.get("pid", 0)))
		except (TypeError, ValueError, OSError):
			log.debug("No se pudo ceder el primer plano", exc_info=True)
		entregada = dejar_orden(ORDEN_MOSTRAR, carpeta=self._carpeta)
		if argumentos:
			entregada = dejar_orden(ORDEN_ABRIR, argumentos, carpeta=self._carpeta) and entregada
		return entregada

	def recoger_ordenes(self) -> list[Orden]:
		"""PRIMERA instancia: órdenes pendientes (se llama desde un ``wx.Timer``)."""
		return recoger_ordenes(self._carpeta)

	def liberar(self) -> None:
		"""Libera el mutex y borra ``instancia.json`` si es nuestro (al cerrar)."""
		datos = self.leer_primera()
		if datos and datos.get("pid") == os.getpid():
			try:
				(self._carpeta / ARCHIVO_INSTANCIA).unlink(missing_ok=True)
			except OSError:
				log.debug("No se pudo borrar %s", ARCHIVO_INSTANCIA, exc_info=True)
		# Destruir el objeto wx cierra el mutex; si no, seguiría hasta que el
		# recolector lo eligiera y una nueva instancia se creería la segunda.
		self._comprobador = None
