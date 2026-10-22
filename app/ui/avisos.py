"""Cómo avisa Ctrl+IA: voz, braille, barra de estado, sonidos y notificaciones.

Reglas:

- ``anunciar``: frases cortas de progreso o navegación por el lector de
  pantalla. Si el usuario elige «Silencio», no se dicen, pero siguen
  llegando a la línea braille (si así está en Preferencias).
- ``notificar``: lo IMPORTANTE (errores, resultados, peticiones de permiso).
  Respeta «Avisos importantes» en Preferencias: por voz, en un cuadro de
  mensaje o de las dos formas, para no perder nada aunque la voz esté apagada.
- NADA EXISTE SOLO COMO VOZ: cada aviso queda en el historial (Ctrl+Mayús+H),
  se escribe en la última parte de la barra de estado y va también a la línea
  braille, aunque la voz de Ctrl+IA esté silenciada.
- ``sonar``: sonidos breves según el esquema elegido (``data/sounds`` o la
  carpeta de sonidos propios), con volumen, cada evento con su interruptor y
  un límite de frecuencia para que una ráfaga de herramientas no atrone.
- ``notificacion_sistema``: notificación de Windows cuando Ctrl+IA no es la
  ventana activa (se ve en el centro de notificaciones).

Nunca se habla con una voz del sistema por encima del lector de pantalla:
``accessible_output2`` usa el lector si hay uno en marcha.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from array import array
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import wx

from app.ui.accesibilidad import voz
from app.ui.comun import _

log = logging.getLogger(__name__)

_estado: dict[str, Any] = {
	"opcion": lambda clave, defecto: defecto,
	"ventana": None,
	"ultimo": "",
	"historial": [],
}
_candado = threading.Lock()
_MAX_HISTORIAL = 200
#: Parte de la barra de estado de la ventana principal donde queda el último aviso.
CAMPO_AVISO = 3

# ── Sonidos ───────────────────────────────────────────────────────────────────

def N_(texto: str) -> str:  # noqa: N802 - convención de gettext
	"""Marca un texto para traducir sin traducirlo aún (se traduce al mostrarlo)."""
	return texto


#: Eventos con sonido: (nombre del archivo sin «.wav», descripción para la persona usuaria).
EVENTOS_SONIDO: tuple[tuple[str, str], ...] = (
	("nueva", N_("Sesión nueva creada")),
	("enviado", N_("Mensaje enviado")),
	("herramienta", N_("El agente usa una herramienta: leer, buscar, ejecutar")),
	("archivo", N_("El agente crea o modifica un archivo")),
	("respuesta", N_("El agente termina de responder en la sesión abierta")),
	("segundo_plano", N_("Termina de responder otra sesión que no tienes abierta")),
	("permiso", N_("El agente te necesita: permiso, pregunta o plan")),
	("error", N_("Error")),
	("detenido", N_("El agente se ha detenido")),
	("compactado", N_("El agente resume la conversación para liberar memoria (compactación)")),
	("limite", N_("Te acercas al límite de uso de tu cuenta")),
)
#: Carpeta (dentro de ``data/sounds``) del esquema «Propio».
CARPETA_PROPIA = "propios"
#: Segundos mínimos entre dos veces el mismo sonido (las herramientas llegan en ráfagas).
INTERVALO_MINIMO = {"herramienta": 0.6, "archivo": 0.6}
INTERVALO_POR_DEFECTO = 0.25
#: Nombres de herramientas que cambian archivos (en minúsculas, por fragmentos).
_HERRAMIENTAS_DE_ARCHIVO = ("write", "edit", "patch", "escrib", "editar", "crear archivo", "modific", "notebook")

_sonidos: dict[tuple[str, float, int], Any] = {}
_ultimo_sonido: dict[str, float] = {}
_sustituciones: dict[str, str] = {}


def configurar(opcion: Callable[[str, Any], Any], ventana: wx.Window | None = None) -> None:
	"""``opcion(clave, defecto)`` lee la configuración en el momento de cada aviso."""
	_estado["opcion"] = opcion
	if ventana is not None:
		_estado["ventana"] = ventana
	aplicar_preferencias_voz()


def aplicar_preferencias_voz() -> None:
	"""Pasa a la capa de voz el retardo y las opciones de braille de Preferencias."""
	opcion = _estado["opcion"]
	voz.configurar(
		retraso_ms=int(opcion("anuncios.retardo_voz_ms", 120)),
		braille=bool(opcion("anuncios.braille", True)),
		braille_sin_voz=bool(opcion("anuncios.braille_sin_voz", True)),
	)


def _apuntar(texto: str) -> None:
	with _candado:
		_estado["ultimo"] = texto
		historial = _estado["historial"]
		historial.append((time.time(), texto))
		del historial[:-_MAX_HISTORIAL]
	_escribir_en_barra(texto)


def _escribir_en_barra(texto: str) -> None:
	"""Deja el aviso escrito en la barra de estado (se lee con la orden del lector)."""
	if not _estado["opcion"]("anuncios.barra_estado", True):
		return
	ventana = _estado["ventana"]
	linea = " ".join(texto.split())
	if len(linea) > 300:
		linea = linea[:299] + "…"

	def escribir() -> None:
		try:
			if not ventana:
				return
			barra = ventana.GetStatusBar()
			if barra is not None and barra.GetFieldsCount() > CAMPO_AVISO:
				barra.SetStatusText(linea, CAMPO_AVISO)
		except RuntimeError:
			log.debug("La ventana ya no existe; el aviso queda solo en el historial", exc_info=True)

	if ventana is None:
		return
	if wx.IsMainThread():
		escribir()
	else:
		wx.CallAfter(escribir)


def anunciar(texto: str, interrumpir: bool = False) -> None:
	"""Dice una frase corta por el lector de pantalla (seguro desde cualquier hilo).

	Con el nivel «Silencio» no se dice, pero va a la línea braille si la opción
	«Mostrar en braille lo que no se dice» está activa.
	"""
	if not texto:
		return
	_apuntar(texto)
	if _estado["opcion"]("anuncios.nivel", "completo") == "silencio":
		if _estado["opcion"]("anuncios.braille_sin_voz", True):
			voz.hablar(texto, interrumpir=interrumpir, solo_braille=True)
		return
	voz.hablar(texto, interrumpir=interrumpir)


def notificar(texto: str, titulo: str = "", padre: wx.Window | None = None) -> None:
	"""Aviso importante: por voz, en un cuadro o de las dos formas, según Preferencias."""
	if not texto:
		return
	_apuntar(texto)
	modo = _estado["opcion"]("anuncios.avisos_importantes", "voz")
	# El nivel «Silencio» calla las respuestas, no los avisos importantes: esos
	# siguen su propia opción para que nunca se pierda un error o un permiso.
	if modo in ("voz", "ambos"):
		voz.hablar(texto, interrumpir=True)
	if modo in ("mensaje", "ambos"):
		def mostrar() -> None:
			from app.ui.accesibilidad.dialogos import mostrar_mensaje

			mostrar_mensaje(padre or _estado["ventana"], texto, titulo or _("Ctrl+IA"))

		if wx.IsMainThread():
			mostrar()
		else:
			wx.CallAfter(mostrar)


def ultimo_aviso() -> str:
	with _candado:
		return _estado["ultimo"]


def historial() -> list[tuple[float, str]]:
	with _candado:
		return list(_estado["historial"])


def repetir_ultimo() -> None:
	texto = ultimo_aviso()
	voz.hablar(texto or _("Todavía no hay avisos."), interrumpir=True)


# ── Sonidos ───────────────────────────────────────────────────────────────────

def carpeta_sonidos_propios() -> Path:
	"""``data/sounds/propios``: tus WAV, con el nombre de cada evento (``respuesta.wav``...)."""
	from app.core.paths import ruta_sonidos

	return ruta_sonidos() / CARPETA_PROPIA


def ruta_sonido(nombre: str) -> Path | None:
	"""Archivo que suena para ``nombre`` según el esquema; ``None`` si no hay ninguno.

	Con el esquema «Propio», un sonido que falte en tu carpeta se toma del
	esquema de Ctrl+IA: así puedes cambiar solo los que quieras.
	"""
	from app.core.paths import ruta_sonidos

	candidatos = []
	if _estado["opcion"]("sonidos.esquema", "predeterminado") == "propio":
		candidatos.append(carpeta_sonidos_propios() / f"{nombre}.wav")
	candidatos.append(ruta_sonidos() / f"{nombre}.wav")
	return next((ruta for ruta in candidatos if ruta.is_file()), None)


def sonido_activo(nombre: str) -> bool:
	"""Interruptor general y el del evento (los eventos sin interruptor suenan siempre)."""
	opcion = _estado["opcion"]
	return bool(opcion("anuncios.sonidos", True)) and bool(opcion(f"sonidos.{nombre}", True))


def alternar_sonidos() -> bool:
	"""Activa o desactiva todos los sonidos; devuelve el estado nuevo (no lo anuncia)."""
	ventana = _estado["ventana"]
	valor = not bool(_estado["opcion"]("anuncios.sonidos", True))
	config = getattr(ventana, "config", None)
	if config is not None:
		config.establecer("anuncios.sonidos", valor)
	return valor


def sonido_de_evento(tipo: str, datos: dict[str, Any]) -> str:
	"""Sonido propio de un evento de agente que la conversación no hace sonar ya ("" si ninguno).

	:param tipo: valor de ``TipoEvento`` (``"herramienta"``, ``"fin_turno"``...).
	"""
	if tipo == "herramienta":
		nombre = str(datos.get("herramienta") or "").lower()
		return "archivo" if any(parte in nombre for parte in _HERRAMIENTAS_DE_ARCHIVO) else "herramienta"
	if tipo == "archivo":
		return "archivo"
	if tipo == "fin_turno" and datos.get("interrumpido"):
		return "detenido"
	return ""


@contextmanager
def sustituir_sonido(original: str, sustituto: str) -> Iterator[None]:
	"""Mientras dura el bloque, ``sonar(original)`` hace sonar ``sustituto`` (mismo hilo)."""
	_sustituciones[original] = sustituto
	try:
		yield
	finally:
		_sustituciones.pop(original, None)


def _datos_con_volumen(ruta: Path, volumen: int) -> bytes:
	"""WAV de 16 bits con las muestras escaladas al ``volumen`` (0-100)."""
	with wave.open(str(ruta), "rb") as origen:
		parametros = origen.getparams()
		marcos = origen.readframes(origen.getnframes())
	if parametros.sampwidth != 2:
		raise ValueError("solo se ajusta el volumen de WAV de 16 bits")
	muestras = array("h")
	muestras.frombytes(marcos)
	factor = max(0, min(100, volumen)) / 100.0
	for indice, valor in enumerate(muestras):
		muestras[indice] = int(valor * factor)
	salida = io.BytesIO()
	with wave.open(salida, "wb") as destino:
		destino.setparams(parametros)
		destino.writeframes(muestras.tobytes())
	return salida.getvalue()


def _cargar(ruta: Path, volumen: int):
	"""``wx.adv.Sound`` listo para sonar (en caché: debe seguir vivo mientras suena)."""
	import wx.adv

	try:
		marca = ruta.stat().st_mtime
	except OSError:
		return None
	clave = (str(ruta), marca, volumen)
	if clave in _sonidos:
		return _sonidos[clave]
	if volumen >= 100:
		sonido = wx.adv.Sound(str(ruta))
	else:
		sonido = wx.adv.Sound()
		try:
			sonido.CreateFromData(_datos_con_volumen(ruta, volumen))
		except (ValueError, wave.Error, EOFError):
			log.debug("No se pudo ajustar el volumen de %s; suena tal cual", ruta, exc_info=True)
			sonido = wx.adv.Sound(str(ruta))
	if len(_sonidos) > 64:
		_sonidos.clear()
	_sonidos[clave] = sonido
	return sonido


def sonar(nombre: str, forzar: bool = False) -> None:
	"""Reproduce el sonido del evento ``nombre`` sin esperar.

	:param forzar: suena aunque esté desactivado (para probarlo en el cuadro de sonidos).
	"""
	nombre = _sustituciones.get(nombre, nombre)
	if not forzar and not sonido_activo(nombre):
		return
	ahora = time.monotonic()
	if not forzar and ahora - _ultimo_sonido.get(nombre, 0.0) < INTERVALO_MINIMO.get(nombre, INTERVALO_POR_DEFECTO):
		return
	_ultimo_sonido[nombre] = ahora
	try:
		ruta = ruta_sonido(nombre)
		if ruta is None:
			return
		volumen = int(_estado["opcion"]("sonidos.volumen", 100))
		if volumen <= 0:
			return
		sonido = _cargar(ruta, volumen)
		if sonido is not None and sonido.IsOk():
			import wx.adv

			sonido.Play(wx.adv.SOUND_ASYNC)
	except Exception:  # noqa: BLE001 - un sonido que falla nunca debe molestar
		log.debug("No se pudo reproducir el sonido %s", nombre, exc_info=True)


# ── Notificaciones de Windows ─────────────────────────────────────────────────

def aplicacion_activa() -> bool:
	aplicacion = wx.GetApp()
	return bool(aplicacion and aplicacion.IsActive())


def notificacion_sistema(titulo: str, texto: str) -> None:
	"""Notificación de Windows, solo si Ctrl+IA no es la ventana activa."""
	if aplicacion_activa():
		return
	try:
		import wx.adv

		mensaje = wx.adv.NotificationMessage(titulo, texto, parent=_estado["ventana"])
		mensaje.Show(timeout=wx.adv.NotificationMessage.Timeout_Auto)
	except Exception:  # noqa: BLE001 - las notificaciones son un extra
		log.debug("No se pudo mostrar la notificación", exc_info=True)
