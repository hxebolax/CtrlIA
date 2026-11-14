"""Tus atajos de teclado: la tecla de cada orden del menú, la tuya o la de fábrica.

Se guardan en ``data/config/teclas.json``::

	{"version": 1, "teclas": {"nueva": "Ctrl+Alt+Shift+N", "exportar": ""}, "recientes": ["filtrar"]}

- Solo se guarda lo que la persona cambia; ``""`` significa que quitó la tecla.
- La clave de cada orden es el primer campo de su entrada en ``MENUS``
  (``app/ui/ventana_principal.py``): «nueva», «continuar»... La tecla de fábrica
  sale de su etiqueta, de lo que va tras el tabulador.
- Las teclas se guardan con los nombres que entiende wx en los menús
  («Ctrl+Shift+Q», modificadores en el orden Ctrl, Alt, Shift) y se dicen en
  español («Ctrl+Mayús+Q») con ``para_leer``. ``normalizar`` acepta los nombres
  en español y en inglés (Control, Mayús, Mayúsculas, Supr, RePág, Intro...).
- Los identificadores que esta versión no conoce se conservan tal cual, para
  la versión que los escribió.
- Un archivo roto se aparta como ``teclas.json.roto-AAAAMMDD-HHMMSS`` y se usan
  las teclas de fábrica. La escritura es atómica (temporal, ``fsync`` y
  ``os.replace``).
- Dos órdenes nunca tienen la misma tecla: una tuya gana a una de fábrica; entre
  dos tuyas, la primera.
- ``recientes``: las últimas acciones usadas desde «Buscar una acción» (unas 10).

Qué teclas se pueden dar y por qué no otras también está aquí, sin wx, para
probarlo por separado (``motivo_rechazo``). En Windows se comprueba con la
distribución de teclado activa si un Ctrl+Alt escribe un carácter (AltGr).
"""

from __future__ import annotations

import builtins
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) and traducir is not _ else texto


NOMBRE_ARCHIVO = "teclas.json"
VERSION_ARCHIVO = 1
#: Cuántas acciones usadas recientemente se recuerdan para «Buscar una acción».
MAX_RECIENTES = 10

MODIFICADORES = ("Ctrl", "Alt", "Shift")
_NOMBRES_MODIFICADOR = {
	"ctrl": "Ctrl", "control": "Ctrl", "ctl": "Ctrl", "strg": "Ctrl", "rawctrl": "Ctrl",
	"alt": "Alt",
	"shift": "Shift", "mayus": "Shift", "mayusculas": "Shift", "may": "Shift",
}
#: Nombre de cada tecla, escrito de cualquier forma (sin tildes ni espacios, en minúsculas), al nombre de aquí.
_NOMBRES_TECLA = {
	"enter": "Enter", "return": "Enter", "intro": "Enter", "entrar": "Enter",
	"delete": "Delete", "del": "Delete", "supr": "Delete", "suprimir": "Delete",
	"insert": "Insert", "ins": "Insert", "insertar": "Insert",
	"home": "Home", "inicio": "Home", "end": "End", "fin": "End",
	"pageup": "PageUp", "pgup": "PageUp", "prior": "PageUp", "repag": "PageUp", "repagina": "PageUp",
	"pagedown": "PageDown", "pgdn": "PageDown", "next": "PageDown", "avpag": "PageDown", "avpagina": "PageDown",
	"up": "Up", "arriba": "Up", "flechaarriba": "Up",
	"down": "Down", "abajo": "Down", "flechaabajo": "Down",
	"left": "Left", "izquierda": "Left", "flechaizquierda": "Left",
	"right": "Right", "derecha": "Right", "flechaderecha": "Right",
	"space": "Space", "espacio": "Space", "barraespaciadora": "Space",
	"tab": "Tab", "tabulador": "Tab",
	"escape": "Escape", "esc": "Escape",
	"backspace": "Backspace", "back": "Backspace", "retroceso": "Backspace",
	"capslock": "CapsLock", "capital": "CapsLock", "bloqmayus": "CapsLock", "bloqueomayusculas": "CapsLock",
	"apps": "Apps", "aplicaciones": "Apps", "menucontextual": "Apps",
	"comma": ",", "coma": ",", "period": ".", "punto": ".", "dot": ".",
	"slash": "/", "barra": "/", "minus": "-", "menos": "-", "guion": "-",
	"semicolon": ";", "puntoycoma": ";", "equals": "=", "equal": "=", "igual": "=",
	"backslash": "\\", "barrainvertida": "\\", "apostrophe": "'", "apostrofo": "'", "quote": "'",
	"grave": "`", "backquote": "`", "acentograve": "`", "leftbracket": "[", "rightbracket": "]",
}
PUNTUACION = ",./;-=\\'`[]"
#: Cómo se dice cada tecla con nombre (como en la lista de teclas).
_PARA_LEER = {
	"Shift": "Mayús", "Enter": "Intro", "Delete": "Supr", "Insert": "Insert", "Home": "Inicio", "End": "Fin",
	"PageUp": "RePág", "PageDown": "AvPág", "Up": "Arriba", "Down": "Abajo", "Left": "Izquierda",
	"Right": "Derecha", "Space": "Espacio", "Backspace": "Retroceso", "CapsLock": "Bloq Mayús",
	"Apps": "Aplicaciones",
}
_MOVIMIENTO = {"Up", "Down", "Left", "Right", "Home", "End", "PageUp", "PageDown"}

#: Teclas que el programa se reserva: significan algo en una parte de la ventana o en
#: todas, y no se pueden dar a ninguna orden. Se dice «X no se puede usar: <motivo>».
FIJAS: dict[str, str] = {
	"Enter": _("abre la sesión o el mensaje elegido y pulsa el botón predeterminado de los cuadros"),
	"Shift+Enter": _("abre el mensaje como texto plano y empieza una línea nueva"),
	"Ctrl+Enter": _("envía el mensaje desde el cuadro de respuesta"),
	"Ctrl+Shift+Enter": _("envía ahora, interrumpiendo al agente"),
	"Ctrl+Alt+Enter": _("añade el mensaje al turno en marcha"),
	"Delete": _("oculta la sesión elegida y borra texto"),
	"Shift+Delete": _("borra para siempre la sesión elegida"),
	"Ctrl+C": _("copia"),
	"Ctrl+H": _("dice para qué sirve el control enfocado"),
	"F6": _("pasa a la zona siguiente de la ventana"),
	"Shift+F6": _("pasa a la zona anterior de la ventana"),
	"F10": _("lleva a la barra de menús"),
	"Shift+F10": _("abre el menú contextual"),
	"Alt+F4": _("cierra la ventana"),
	"Escape": _("cierra los cuadros y vuelve a la lista de sesiones"),
	"Tab": _("pasa de un control a otro"),
	"Shift+Tab": _("pasa de un control a otro"),
	**{f"Ctrl+{numero}": _("lee una columna en las listas") for numero in "0123456789"},
}
#: Teclas que nunca pueden ser atajo, con cualquier modificador.
_SIEMPRE_RESERVADAS = {
	"Space": _("escribe un espacio y marca elementos en las listas"),
	"Backspace": _("borra texto"),
	"Delete": _("borra texto"),
	"Tab": _("pasa de un control a otro"),
	"Escape": _("cierra los cuadros y vuelve a la lista de sesiones"),
}


# ── Nombres de las teclas ─────────────────────────────────────────────────────

def sin_tildes(texto: str) -> str:
	"""``texto`` en minúsculas y sin tildes, para comparar y buscar."""
	return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").casefold()


def _clave_nombre(parte: str) -> str:
	return sin_tildes(parte).replace(" ", "").replace("_", "")


def normalizar(tecla: str) -> str:
	"""``tecla`` en la forma única de aquí: modificadores en el orden Ctrl, Alt, Shift y la tecla.

	«mayús+control+q» es «Ctrl+Shift+Q»; «Control+RePág» es «Ctrl+PageUp».

	:raises ValueError: con el motivo en palabras, si no es una tecla.
	"""
	texto = (tecla or "").strip()
	if not texto:
		raise ValueError(_("No hay ninguna tecla."))
	partes = [p.strip() for p in texto.split("+")]
	if any(not p for p in partes):
		raise ValueError(_("«{tecla}» no es una tecla que Ctrl+IA pueda usar en un atajo.").format(tecla=texto))
	modificadores: set[str] = set()
	for parte in partes[:-1]:
		nombre = _NOMBRES_MODIFICADOR.get(_clave_nombre(parte))
		if nombre is None:
			raise ValueError(_("«{parte}» no es Ctrl, Alt ni Mayús.").format(parte=parte))
		modificadores.add(nombre)
	ultima = partes[-1]
	if _clave_nombre(ultima) in _NOMBRES_MODIFICADOR:
		raise ValueError(_("Pulsa una tecla junto con Ctrl, Alt o Mayús, no solo el modificador."))
	nombre = _NOMBRES_TECLA.get(_clave_nombre(ultima))
	if nombre is None:
		if len(ultima) == 1 and ultima.isascii() and ultima.isalnum():
			nombre = ultima.upper()
		elif len(ultima) == 1 and ultima in PUNTUACION:
			nombre = ultima
		elif re.fullmatch(r"[Ff]([1-9]|1[0-9]|2[0-4])", ultima):
			nombre = ultima.upper()
		else:
			raise ValueError(_("Ctrl+IA no sabe usar «{tecla}» en un atajo.").format(tecla=ultima))
	return "+".join([m for m in MODIFICADORES if m in modificadores] + [nombre])


def partes(tecla: str) -> tuple[set[str], str]:
	"""(modificadores, tecla) de una tecla ya normalizada."""
	trozos = tecla.split("+")
	return set(trozos[:-1]), trozos[-1]


def es_tecla_de_funcion(nombre: str) -> bool:
	return bool(re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", nombre))


def para_leer(tecla: str) -> str:
	"""``tecla`` (normalizada) como se dice y se ve: «Ctrl+Mayús+Q», «Alt+Intro». ``""`` si no hay."""
	if not tecla:
		return ""
	return "+".join(_PARA_LEER.get(trozo, trozo) for trozo in tecla.split("+"))


def tecla_de_etiqueta(etiqueta: str) -> str:
	"""La tecla de una etiqueta de menú («&Nueva sesión...\\tCtrl+N» → «Ctrl+N»), normalizada; ``""`` si no tiene."""
	if "\t" not in etiqueta:
		return ""
	try:
		return normalizar(etiqueta.rpartition("\t")[2])
	except ValueError:
		log.warning("La etiqueta de menú %r tiene una tecla que no se entiende", etiqueta)
		return ""


def etiqueta_sin_tecla(etiqueta: str) -> str:
	return etiqueta.rpartition("\t")[0] if "\t" in etiqueta else etiqueta


def etiqueta_con_tecla(etiqueta: str, tecla: str) -> str:
	"""La etiqueta de menú con ``tecla`` tras el tabulador (sin tabulador si no hay tecla): wx la usa de acelerador."""
	base = etiqueta_sin_tecla(etiqueta)
	return f"{base}\t{tecla}" if tecla else base


def nombre_de_etiqueta(etiqueta: str) -> str:
	"""El nombre de una orden como se dice: sin «&», sin tecla y sin los puntos suspensivos finales."""
	texto = etiqueta_sin_tecla(etiqueta).replace("&&", "\0").replace("&", "").replace("\0", "&").strip()
	return re.sub(r"\s*(\.\.\.|…)$", "", texto)


# ── Teclas que no se pueden dar ──────────────────────────────────────────────

#: (distribución de teclado, tecla) → carácter que escribe con AltGr ("" ninguno).
_CACHE_ALTGR: dict[tuple[int, str], str] = {}
_USER32 = None


def _user32():
	global _USER32
	if _USER32 is None:
		import ctypes
		from ctypes import wintypes

		user32 = ctypes.WinDLL("user32", use_last_error=True)
		user32.GetKeyboardLayout.restype = ctypes.c_void_p
		user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
		user32.VkKeyScanExW.restype = ctypes.c_short
		user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, ctypes.c_void_p]
		user32.MapVirtualKeyExW.restype = wintypes.UINT
		user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
		user32.ToUnicodeEx.restype = ctypes.c_int
		user32.ToUnicodeEx.argtypes = [
			wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte), wintypes.LPWSTR, ctypes.c_int,
			wintypes.UINT, ctypes.c_void_p,
		]
		_USER32 = user32
	return _USER32


def caracter_altgr(tecla: str) -> str | None:
	"""Qué escribe ``tecla`` (un Ctrl+Alt normalizado) con la distribución de teclado activa.

	Windows lee AltGr como Ctrl+Alt: en muchos teclados escribe €, @, #, corchetes,
	llaves... y un atajo con esa tecla se los quitaría a quien escribe.

	:return: el carácter, ``""`` si no escribe ninguno, o ``None`` si no se puede
		comprobar (fuera de Windows o si falla la API).
	"""
	if sys.platform != "win32":
		return None
	modificadores, nombre = partes(tecla)
	if len(nombre) != 1:
		return ""  # F1, Intro...: no escriben nada
	try:
		import ctypes

		user32 = _user32()
		distribucion = int(user32.GetKeyboardLayout(0) or 0)
		guardado = _CACHE_ALTGR.get((distribucion, tecla))
		if guardado is not None:
			return guardado
		con_mayus = "Shift" in modificadores
		if nombre.isascii() and nombre.isalnum():
			codigo = ord(nombre.upper())
		else:
			resultado = int(user32.VkKeyScanExW(nombre, distribucion))
			if resultado == -1:
				return None
			codigo, estados = resultado & 0xFF, (resultado >> 8) & 0xFF
			if estados & 0x06:
				return nombre  # el propio signo ya se escribe con Ctrl o Alt (AltGr)
			con_mayus = con_mayus or bool(estados & 0x01)
		estado = (ctypes.c_ubyte * 256)()
		for tecla_virtual in (0x11, 0x12, 0xA2, 0xA5):  # Ctrl, Alt, Ctrl izquierdo, Alt derecho (AltGr)
			estado[tecla_virtual] = 0x80
		if con_mayus:
			estado[0x10] = estado[0xA0] = 0x80
		memoria = ctypes.create_unicode_buffer(8)
		codigo_exploracion = user32.MapVirtualKeyExW(codigo, 0, distribucion)
		# Bandera 4: no cambia el estado del teclado (las teclas muertas no se quedan pendientes).
		escritos = int(user32.ToUnicodeEx(codigo, codigo_exploracion, estado, memoria, 8, 4, distribucion))
		if escritos < 0:
			caracter = memoria.value[:1] or "?"  # tecla muerta (~, ^...): también escribe
		elif escritos > 0:
			texto = memoria.value[:escritos]
			caracter = texto if any(ord(c) >= 32 for c in texto) else ""
		else:
			caracter = ""
		_CACHE_ALTGR[(distribucion, tecla)] = caracter
		return caracter
	except (OSError, AttributeError, ValueError, TypeError):
		log.debug("No se pudo comprobar si %s escribe con AltGr", tecla, exc_info=True)
		return None


def motivo_rechazo(
	tecla: str,
	de_fabrica: Collection[str] = (),
	altgr: Callable[[str], str | None] | None = caracter_altgr,
) -> str:
	"""Por qué ``tecla`` (normalizada) no puede ser el atajo de ninguna orden, o ``""`` si puede.

	:param de_fabrica: teclas de fábrica del programa: si no se puede comprobar
		AltGr, un Ctrl+Alt de fábrica sigue valiendo (Ctrl+IA usa muchos).
	:param altgr: comprueba qué escribe un Ctrl+Alt (``caracter_altgr``).
	"""
	leer = para_leer(tecla)
	if tecla in FIJAS:
		return _("{tecla} no se puede usar: {motivo}.").format(tecla=leer, motivo=FIJAS[tecla])
	modificadores, nombre = partes(tecla)
	funcion = es_tecla_de_funcion(nombre)
	if nombre == "Insert":
		return _("Los lectores de pantalla usan Insert: no puede ser un atajo.")
	if nombre == "CapsLock":
		return _("Los lectores de pantalla usan Bloq Mayús: no puede ser un atajo.")
	if nombre == "Apps":
		return _("La tecla Aplicaciones abre el menú contextual: no puede ser un atajo.")
	if nombre in _MOVIMIENTO:
		return _("{tecla} mueve por la ventana y por el texto: no puede ser un atajo.").format(tecla=para_leer(nombre))
	if nombre in _SIEMPRE_RESERVADAS:
		return _("{tecla} {motivo}: no puede ser un atajo.").format(tecla=para_leer(nombre), motivo=_SIEMPRE_RESERVADAS[nombre])
	if nombre == "F4" and "Alt" in modificadores:
		return _("{tecla} cierra la ventana: no puede ser un atajo.").format(tecla=leer)
	if not modificadores and not funcion:
		return _("Una tecla sola escribe o mueve. Úsala con Ctrl, o elige una tecla de función (F1 a F24).")
	if modificadores == {"Shift"} and not funcion:
		return _("Mayús con esa tecla escribe un carácter. Úsala con Ctrl, o elige una tecla de función.")
	if "Alt" in modificadores and "Ctrl" not in modificadores and not funcion and nombre != "Enter":
		return _("Alt con una letra, un número o un signo abre un menú o pulsa un botón. Añade Ctrl.")
	if modificadores == {"Ctrl"} and nombre in ("X", "V", "A", "Z", "Y"):
		return _("{tecla} edita texto (cortar, pegar, seleccionar todo, deshacer o rehacer): no puede ser un atajo.").format(
			tecla=leer)
	if {"Ctrl", "Alt"} <= modificadores and not funcion and nombre != "Enter":
		caracter = altgr(tecla) if altgr is not None else None
		if caracter:
			return _("Con tu distribución de teclado, {tecla} (AltGr) escribe «{caracter}»: no puede ser un atajo.").format(
				tecla=leer, caracter=caracter)
		if caracter is None and tecla not in de_fabrica:
			return _(
				"No se puede comprobar si {tecla} escribe un carácter con tu teclado (AltGr), así que por prudencia no "
				"se usa. Elige una con Ctrl y Mayús, o una tecla de función."
			).format(tecla=leer)
	return ""


# ── Órdenes del menú ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Comando:
	"""Una orden de la barra de menús."""

	clave: str
	nombre: str
	menu: str
	tecla_fabrica: str
	ayuda: str = ""
	metodo: str = ""
	#: Por qué su tecla no se puede cambiar (``""``: se puede).
	fija: str = ""


def comandos_de_menus(menus: Sequence[tuple[str, Sequence[tuple]]]) -> list[Comando]:
	"""Las órdenes de una tabla como ``MENUS`` (sin separadores ni submenús), en orden."""
	comandos: list[Comando] = []
	for titulo, elementos in menus:
		menu = nombre_de_etiqueta(titulo)
		for elemento in elementos:
			if elemento[0] in ("-", "submenu"):
				continue
			clave, etiqueta, ayuda, metodo = elemento
			tecla = tecla_de_etiqueta(etiqueta)
			fija = FIJAS.get(tecla, "") if tecla else ""
			comandos.append(Comando(clave, nombre_de_etiqueta(etiqueta), menu, tecla, ayuda, metodo, fija))
	return comandos


# ── El mapa de teclas ────────────────────────────────────────────────────────

def _reemplazar(origen: Path, destino: Path, intentos: int = 5) -> None:
	"""``os.replace`` con reintentos (un antivirus puede tener el destino abierto un instante)."""
	espera = 0.05
	for intento in range(1, intentos + 1):
		try:
			os.replace(origen, destino)
			return
		except PermissionError:
			if intento == intentos:
				raise
			time.sleep(espera)
			espera *= 2


def escribir_json_atomico(ruta: Path, datos: object) -> None:
	"""Escribe ``datos`` en ``ruta`` de forma atómica: el archivo queda viejo o nuevo, nunca a medias."""
	ruta.parent.mkdir(parents=True, exist_ok=True)
	temporal = ruta.with_name(f"{ruta.name}.{os.getpid()}-{threading.get_ident()}.tmp")
	try:
		with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
			json.dump(datos, archivo, ensure_ascii=False, indent=2)
			archivo.flush()
			os.fsync(archivo.fileno())
		_reemplazar(temporal, ruta)
	except OSError:
		try:
			temporal.unlink(missing_ok=True)
		except OSError:
			log.debug("No se pudo borrar el temporal %s", temporal, exc_info=True)
		raise


def ruta_por_defecto() -> Path:
	from app.core.paths import ruta_configuracion

	return ruta_configuracion() / NOMBRE_ARCHIVO


class MapaTeclas:
	"""Las teclas en uso: tus cambios sobre las de fábrica de cada orden."""

	def __init__(
		self,
		comandos: Iterable[Comando],
		ruta: Path | None = None,
		ocupadas: dict[str, str] | None = None,
		altgr: Callable[[str], str | None] | None = caracter_altgr,
	) -> None:
		"""
		:param comandos: las órdenes del menú (``comandos_de_menus(MENUS)``).
		:param ocupadas: teclas de órdenes que no se pueden cambiar (las de los
			submenús), con el nombre de la orden: nadie más puede tenerlas.
		:param altgr: comprueba qué escribe un Ctrl+Alt (en las pruebas, una función fija).
		"""
		self.ruta = Path(ruta) if ruta else ruta_por_defecto()
		self._comandos = {c.clave: c for c in comandos}
		#: Tecla de fábrica de cada orden que se puede cambiar, en el orden del menú.
		self._defectos = {c.clave: c.tecla_fabrica for c in self._comandos.values() if not c.fija}
		self._ocupadas = dict(ocupadas or {})
		for comando in self._comandos.values():
			if comando.fija and comando.tecla_fabrica:
				self._ocupadas.setdefault(comando.tecla_fabrica, comando.nombre)
		self._todas_de_fabrica = frozenset(
			{c.tecla_fabrica for c in self._comandos.values() if c.tecla_fabrica} | set(self._ocupadas))
		self._altgr = altgr
		#: Tus cambios por orden: tecla normalizada, o ``""`` si le quitaste la suya.
		self._cambios: dict[str, str] = {}
		#: Entradas de órdenes que esta versión no conoce (o no deja cambiar), tal cual.
		self._desconocidas: dict[str, object] = {}
		#: Acciones usadas hace poco desde «Buscar una acción», la última primero.
		self.recientes: list[str] = []
		#: Qué pasó al leer o guardar el archivo, para contárselo a la persona («» si nada).
		self.aviso = ""
		self._guardado_bloqueado = False
		self.cargar()

	# ── Lectura ────────────────────────────────────────────────────────────────

	def cargar(self) -> None:
		self._cambios, self._desconocidas, self.recientes = {}, {}, []
		self.aviso = ""
		self._guardado_bloqueado = False
		try:
			texto = self.ruta.read_text(encoding="utf-8-sig")
		except FileNotFoundError:
			return
		except OSError as error:
			log.warning("No se pudo leer %s", self.ruta, exc_info=True)
			self.aviso = _(
				"No se pudieron leer tus atajos de teclado ({ruta}): {error}. Se usan los de fábrica, y los cambios no se "
				"guardarán hasta que Ctrl+IA pueda leer el archivo."
			).format(ruta=self.ruta, error=error)
			self._guardado_bloqueado = True
			return
		try:
			datos = json.loads(texto)
		except ValueError as error:
			self._apartar(_("no es un JSON válido ({error})").format(error=error))
			return
		teclas = datos.get("teclas", {}) if isinstance(datos, dict) else None
		if not isinstance(teclas, dict):
			self._apartar(_("no tiene el formato esperado"))
			return
		recientes = datos.get("recientes", [])
		if isinstance(recientes, list):
			self.recientes = [r for r in recientes if isinstance(r, str) and r][:MAX_RECIENTES]
		malas = 0
		for clave, valor in teclas.items():
			if clave not in self._defectos:
				self._desconocidas[str(clave)] = valor
				continue
			if not isinstance(valor, str):
				malas += 1
				continue
			try:
				tecla = normalizar(valor) if valor else ""
			except ValueError:
				malas += 1
				continue
			if tecla and self.por_que_no(clave, tecla):
				malas += 1
				continue
			self._cambios[clave] = tecla
		malas += self._asentar()
		if malas:
			log.warning("%d atajos de %s no se pudieron usar", malas, self.ruta)
			self.aviso = _(
				"{n} de tus atajos de teclado no se pudieron usar, así que esas órdenes tienen su tecla de fábrica."
			).format(n=malas)

	def _asentar(self) -> int:
		"""Dos órdenes nunca con la misma tecla tras leer un archivo escrito a mano o por otra versión.

		Una tecla tuya gana a una de fábrica: la orden que la tenía de fábrica se
		queda sin ella, como al elegirla en el diálogo. Dos tuyas con la misma
		tecla: la primera la conserva y la otra vuelve a la de fábrica (o a
		ninguna, si también está cogida). Devuelve cuántas tuyas no se pudieron conservar.
		"""
		perdidas = 0
		duenos: dict[str, str] = {}
		for clave, tecla in list(self._cambios.items()):
			if not tecla:
				continue
			if tecla in duenos:
				del self._cambios[clave]
				perdidas += 1
			else:
				duenos[tecla] = clave
		for clave in self._defectos:
			if clave in self._cambios:
				continue
			defecto = self._defectos[clave]
			if defecto and duenos.get(defecto, clave) != clave:
				self._cambios[clave] = ""  # una tuya se quedó con su tecla
			elif defecto:
				duenos[defecto] = clave
		return perdidas

	def _apartar(self, motivo: str) -> None:
		marca = time.strftime("%Y%m%d-%H%M%S")
		copia = self.ruta.with_name(f"{self.ruta.name}.roto-{marca}")
		try:
			shutil.move(str(self.ruta), str(copia))
		except OSError as error:
			log.error("No se pudo apartar %s", self.ruta, exc_info=True)
			self.aviso = _(
				"Tu archivo de atajos de teclado ({ruta}) {motivo}, y no se pudo apartar ({error}). Se usan los de "
				"fábrica, y los cambios no se guardarán hasta que se arregle o se borre."
			).format(ruta=self.ruta, motivo=motivo, error=error)
			self._guardado_bloqueado = True
			return
		log.warning("Archivo de atajos apartado como %s: %s", copia.name, motivo)
		self.aviso = _(
			"Tu archivo de atajos de teclado {motivo}, así que se usan los de fábrica. Se ha guardado aparte como {copia}."
		).format(motivo=motivo, copia=copia.name)

	# ── Consultas ──────────────────────────────────────────────────────────────

	def personalizables(self) -> list[str]:
		"""Las claves de las órdenes cuya tecla se puede cambiar, en el orden del menú."""
		return list(self._defectos)

	def es_personalizable(self, clave: str) -> bool:
		return clave in self._defectos

	def comando(self, clave: str) -> Comando:
		return self._comandos[clave]

	def nombre(self, clave: str) -> str:
		comando = self._comandos.get(clave)
		return comando.nombre if comando else clave

	def tecla(self, clave: str) -> str:
		"""La tecla de la orden ahora, normalizada; ``""`` si no tiene."""
		if clave in self._cambios:
			return self._cambios[clave]
		comando = self._comandos.get(clave)
		return comando.tecla_fabrica if comando else ""

	def de_fabrica(self, clave: str) -> str:
		comando = self._comandos.get(clave)
		return comando.tecla_fabrica if comando else ""

	def cambiada(self, clave: str) -> bool:
		return clave in self._cambios and self._cambios[clave] != self.de_fabrica(clave)

	def cambiadas(self) -> list[str]:
		"""Las claves de las órdenes con la tecla cambiada, en el orden del menú."""
		return [clave for clave in self._defectos if self.cambiada(clave)]

	def comando_de(self, tecla: str) -> str | None:
		"""La orden (que se puede cambiar) que tiene ``tecla`` ahora, o ``None``."""
		try:
			tecla = normalizar(tecla)
		except ValueError:
			return None
		return next((clave for clave in self._defectos if self.tecla(clave) == tecla), None)

	def por_que_no(self, clave: str, tecla: str) -> str:
		"""Por qué la orden ``clave`` no puede tener ``tecla`` (normalizada), o ``""`` si puede.

		Su propia tecla de fábrica siempre puede. La de una orden que no se puede
		cambiar (los submenús, Enviar, Ctrl+H) nadie más puede cogerla.
		"""
		if not tecla or tecla == self.de_fabrica(clave):
			return ""
		motivo = motivo_rechazo(tecla, self._todas_de_fabrica, self._altgr)
		if motivo:
			return motivo
		if tecla in self._ocupadas:
			return _("{tecla} es de «{orden}», que no se puede cambiar.").format(
				tecla=para_leer(tecla), orden=self._ocupadas[tecla])
		return ""

	def lineas_cambiadas(self) -> list[str]:
		"""Una frase por cada tecla cambiada, para la lista de teclas (F1)."""
		lineas = []
		for clave in self.cambiadas():
			actual, fabrica = self.tecla(clave), self.de_fabrica(clave)
			if not fabrica:
				lineas.append(_("{orden}: {tecla} (de fábrica no tenía)").format(orden=self.nombre(clave), tecla=para_leer(actual)))
			elif not actual:
				lineas.append(_("{orden}: sin tecla, en vez de {antes}").format(orden=self.nombre(clave), antes=para_leer(fabrica)))
			else:
				lineas.append(_("{orden}: {tecla}, en vez de {antes}").format(
					orden=self.nombre(clave), tecla=para_leer(actual), antes=para_leer(fabrica)))
		return lineas

	# ── Cambios ────────────────────────────────────────────────────────────────

	def asignar(self, clave: str, tecla: str) -> str | None:
		"""Da a ``clave`` la tecla ``tecla`` (``""`` le quita la suya).

		:return: la clave de la orden que tenía esa tecla y la ha perdido, o ``None``.
		:raises ValueError: con el motivo en palabras, si no se puede.
		"""
		if clave not in self._defectos:
			raise ValueError(_("La tecla de «{orden}» no se puede cambiar.").format(orden=self.nombre(clave)))
		tecla = normalizar(tecla) if tecla else ""
		motivo = self.por_que_no(clave, tecla)
		if motivo:
			raise ValueError(motivo)
		perdedora = self.comando_de(tecla) if tecla else None
		if perdedora == clave:
			perdedora = None
		if perdedora:
			self._cambiar(perdedora, "")
		self._cambiar(clave, tecla)
		return perdedora

	def quitar(self, clave: str) -> None:
		self.asignar(clave, "")

	def restablecer(self, clave: str) -> str | None:
		"""Vuelve a la tecla de fábrica. La orden que la tenga ahora la pierde, y se devuelve su clave."""
		if clave not in self._defectos:
			return None
		defecto = self.de_fabrica(clave)
		perdedora = self.comando_de(defecto) if defecto else None
		if perdedora == clave:
			perdedora = None
		if perdedora:
			self._cambiar(perdedora, "")
		self._cambios.pop(clave, None)
		return perdedora

	def restablecer_todo(self) -> None:
		self._cambios = {}

	def _cambiar(self, clave: str, tecla: str) -> None:
		if tecla == self.de_fabrica(clave):
			self._cambios.pop(clave, None)
		else:
			self._cambios[clave] = tecla

	def anotar_reciente(self, identificador: str) -> None:
		"""Pone ``identificador`` el primero de las acciones usadas recientemente."""
		self.recientes = [identificador] + [r for r in self.recientes if r != identificador]
		del self.recientes[MAX_RECIENTES:]

	def guardar(self) -> bool:
		"""Escribe ``teclas.json``; ``False`` (con ``aviso`` diciendo por qué) si no se pudo."""
		if self._guardado_bloqueado:
			return False
		teclas = dict(self._desconocidas)
		teclas.update(self._cambios)
		datos: dict[str, object] = {"version": VERSION_ARCHIVO, "teclas": teclas}
		if self.recientes:
			datos["recientes"] = list(self.recientes)
		try:
			escribir_json_atomico(self.ruta, datos)
		except OSError as error:
			log.error("No se pudieron guardar los atajos en %s", self.ruta, exc_info=True)
			self.aviso = _("No se pudieron guardar tus atajos de teclado ({ruta}): {error}.").format(ruta=self.ruta, error=error)
			return False
		return True


# ── Buscar una acción ────────────────────────────────────────────────────────

@dataclass
class Accion:
	"""Una orden que se puede buscar por su nombre y ejecutar (de la barra de menús o de un submenú)."""

	id: str
	nombre: str
	tecla: str = ""
	menu: str = ""
	ayuda: str = ""
	disponible: bool = True
	#: Para los elementos que se marcan: si lo están ahora (``None``: no se marca).
	marcada: bool | None = None
	orden: int = 0


def _textos_de_tecla(tecla: str) -> str:
	if not tecla:
		return ""
	leida = para_leer(tecla)
	return " ".join({tecla, leida, leida.replace("Ctrl", "Control"), leida.replace("Mayús", "Mayúsculas")})


def buscar_acciones(acciones: Sequence[Accion], texto: str, recientes: Sequence[str] = ()) -> list[Accion]:
	"""Las acciones que contienen todas las palabras de ``texto``, en cualquier orden, sin tildes ni mayúsculas.

	Se busca en el nombre, el menú, la tecla (como «ctrl+/» o «ctrl+mayús+n») y la
	ayuda. Primero las que lo tienen en el nombre, después en el menú o la tecla y
	al final solo en la ayuda; dentro de cada grupo, las usadas recientemente
	primero y luego en el orden del menú. Sin texto, todas: las recientes primero.
	"""
	palabras = sin_tildes(texto).split()
	posicion = {identificador: indice for indice, identificador in enumerate(recientes)}
	encontradas: list[tuple[int, int, int, Accion]] = []
	for accion in acciones:
		puntos = 0
		if palabras:
			nombre = sin_tildes(accion.nombre)
			principal = " ".join((nombre, sin_tildes(accion.menu), sin_tildes(_textos_de_tecla(accion.tecla))))
			if all(p in nombre for p in palabras):
				puntos = 0
			elif all(p in principal for p in palabras):
				puntos = 1
			elif all(p in principal + " " + sin_tildes(accion.ayuda) for p in palabras):
				puntos = 2
			else:
				continue
		encontradas.append((puntos, posicion.get(accion.id, len(posicion)), accion.orden, accion))
	encontradas.sort(key=lambda t: t[:3])
	return [t[3] for t in encontradas]
