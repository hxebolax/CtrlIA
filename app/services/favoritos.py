"""Mensajes favoritos: cómo se identifica un mensaje dentro de su sesión (sin interfaz).

Un favorito se guarda en la tabla ``mensajes_favoritos`` con la clave de la
sesión y un identificador del mensaje que debe ser el mismo cada vez que se
abre la sesión:

- Sesiones de Ctrl+IA (``ctrlia:``): el número del mensaje en la base de datos.
- Claude Code de otros programas (``claude:``): el ``uuid`` o el ``message.id``
  del transcript.
- Lo que no trae identificador (hilos de Codex, chats de Ollama, avisos) y las
  sesiones importadas (sus números cambian al volver a importarlas): una
  HUELLA ``h:<sha1>`` de rol, fecha en segundos y los primeros 500 caracteres.

Codex no siempre da la misma fecha a un mensaje (según cómo devuelva el hilo
usa la del paso o la del turno): por eso, si una huella guardada no aparece,
se reconoce el mensaje por su rol y su extracto (``Marcas.favorito``).

Cada favorito puede llevar una nota corta de una línea (``limpiar_nota``, como
mucho ``LARGO_NOTA`` caracteres), que se lee al ir a él, en los indicadores y en
la lista de todos.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.services.almacen import PREFIJO_IMPORTADA, MensajeFavorito

#: Prefijo de los identificadores que son una huella y no el id del agente.
PREFIJO_HUELLA = "h:"
#: Caracteres del texto que entran en la huella.
LARGO_HUELLA = 500
#: Caracteres del extracto que se guarda para listar el favorito sin abrir la sesión.
LARGO_EXTRACTO = 300
#: Caracteres como máximo de la nota de un favorito (un recordatorio corto, de una línea).
LARGO_NOTA = 200


def huella(rol: str, fecha: float, texto: str) -> str:
	"""Huella estable de un mensaje sin identificador propio: ``h:`` y un sha1."""
	try:
		segundos = round(float(fecha or 0))
	except (TypeError, ValueError):
		segundos = 0
	base = f"{rol}|{segundos}|{(texto or '')[:LARGO_HUELLA]}"
	return PREFIJO_HUELLA + hashlib.sha1(base.encode("utf-8", "surrogatepass")).hexdigest()  # noqa: S324 - no es seguridad, solo un identificador


def clave_mensaje(sesion: str, mensaje: Any) -> str:
	"""Identificador estable de ``mensaje`` (un ``MensajeVista``) dentro de la sesión ``sesion``."""
	identificador = getattr(mensaje, "id", 0)
	if sesion.startswith(PREFIJO_IMPORTADA) or identificador in (0, "", None) or isinstance(identificador, bool):
		return huella(str(getattr(mensaje, "rol", "")), getattr(mensaje, "fecha", 0), str(getattr(mensaje, "texto", "")))
	return str(identificador)


def extracto(texto: str, largo: int = LARGO_EXTRACTO) -> str:
	"""El principio del texto en una sola línea (sin saltos ni espacios repetidos)."""
	return " ".join((texto or "").split())[:largo]


def limpiar_nota(texto: str | None) -> str:
	"""La nota tal como se guarda: una sola línea, sin espacios repetidos y como mucho ``LARGO_NOTA`` caracteres.

	Vacía quiere decir «sin nota».
	"""
	return extracto(texto or "", LARGO_NOTA).strip()


@dataclass
class Marcas:
	"""Los favoritos de una sesión, listos para preguntar mensaje a mensaje si lo es.

	Se calcula una vez por sesión cargada (no consulta la base de datos por cada fila).
	"""

	sesion: str = ""
	guardados: dict[str, MensajeFavorito] = field(default_factory=dict)
	#: (rol, extracto) → identificador guardado, de las huellas que no se encontraron tal cual.
	sueltos: dict[tuple[str, str], str] = field(default_factory=dict)

	@classmethod
	def crear(cls, sesion: str, guardados: Iterable[MensajeFavorito], mensajes: Iterable[Any]) -> Marcas:
		por_clave = {f.mensaje: f for f in guardados}
		presentes = {clave_mensaje(sesion, m) for m in mensajes} & por_clave.keys()
		sueltos = {
			(f.rol, f.extracto): clave for clave, f in por_clave.items()
			if clave not in presentes and clave.startswith(PREFIJO_HUELLA) and f.extracto
		}
		return cls(sesion, por_clave, sueltos)

	def favorito(self, mensaje: Any) -> str:
		"""Identificador con el que está guardado ``mensaje`` como favorito ('' si no lo es)."""
		if not self.guardados:
			return ""
		clave = clave_mensaje(self.sesion, mensaje)
		if clave in self.guardados:
			return clave
		if clave.startswith(PREFIJO_HUELLA) and self.sueltos:
			return self.sueltos.get((str(getattr(mensaje, "rol", "")), extracto(str(getattr(mensaje, "texto", "")))), "")
		return ""

	def nota(self, mensaje: Any) -> str:
		"""La nota del favorito ``mensaje`` ('' si no es favorito o no tiene nota)."""
		guardado = self.guardados.get(self.favorito(mensaje)) if self.guardados else None
		return guardado.nota if guardado is not None else ""

	def __len__(self) -> int:
		return len(self.guardados)


def posiciones(marcas: Marcas, mensajes: list[Any]) -> list[int]:
	"""Posiciones (en ``mensajes``) de los que son favoritos, en orden."""
	return [indice for indice, mensaje in enumerate(mensajes) if marcas.favorito(mensaje)]


def buscar(marcas: Marcas, mensajes: list[Any], identificador: str) -> int:
	"""Posición del favorito guardado como ``identificador`` en ``mensajes`` (-1 si no está)."""
	return next((i for i, m in enumerate(mensajes) if marcas.favorito(m) == identificador), -1)
