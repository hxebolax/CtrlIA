"""Lo que corre junto a cada conversación: subagentes y órdenes en segundo plano.

Los adaptadores emiten ``TipoEvento.ACTIVIDAD`` con solo lo que cambia de cada
tarea (campos en ``agentes.base.CAMPOS_ACTIVIDAD``); este módulo los acumula
por sesión y por ``id`` para la ventana «Actividad en segundo plano»
(``ui/dialogos/actividad.py``), el Estado del turno y los avisos.

No importa wx: se prueba sin ventanas. Es seguro entre hilos (el gestor lo
alimenta desde los hilos de los agentes y la interfaz lo lee).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

#: Pasos que se guardan de cada tarea (los más recientes).
MAX_PASOS = 400
#: Caracteres de salida que se guardan de cada orden (el final).
MAX_SALIDA = 16000
#: Tareas terminadas que se conservan por sesión (las más recientes).
MAX_TERMINADAS = 50

ESTADOS = {
	"en_marcha": "en marcha",
	"completada": "terminada",
	"fallida": "fallida",
	"detenida": "detenida",
}
CLASES = {"subagente": "Subagente", "orden": "Orden", "otra": "Tarea"}


@dataclass
class Tarea:
	"""Un subagente, una orden en segundo plano u otra tarea que acompaña a la conversación."""

	id: str
	clase: str = "otra"
	estado: str = "en_marcha"
	titulo: str = ""
	tipo_agente: str = ""
	encargo: str = ""
	orden: str = ""
	paso: str = ""
	pasos: list[str] = field(default_factory=list)
	salida: str = ""
	archivo_salida: str = ""
	tokens: int = 0
	usos_herramientas: int = 0
	duracion_ms: int = 0
	resumen: str = ""
	segundo_plano: bool = False
	detenible: bool = False
	inicio: float = field(default_factory=time.time)
	fin: float = 0.0
	#: Cambia con cada actualización: la ventana solo repinta lo que cambió.
	version: int = 0

	@property
	def en_marcha(self) -> bool:
		return self.estado == "en_marcha"

	def nombre(self) -> str:
		"""«Subagente 'contar archivos' (general-purpose)», «Orden: npm test»..."""
		if self.clase == "orden":
			return "Orden: " + (self.orden or self.titulo or self.id)
		base = CLASES.get(self.clase, "Tarea")
		titulo = self.titulo or self.id
		tipo = f" ({self.tipo_agente})" if self.tipo_agente and self.tipo_agente not in titulo else ""
		return f"{base} «{titulo}»{tipo}"

	def fila(self) -> str:
		"""Lo que dice su fila en la lista: nombre, estado, cifras y lo que hace ahora."""
		partes = [self.nombre(), ESTADOS.get(self.estado, self.estado)]
		if self.usos_herramientas:
			partes.append(f"{self.usos_herramientas} usos de herramientas")
		if self.tokens:
			partes.append(f"{self.tokens} tokens")
		segundos = self.segundos()
		if segundos >= 1:
			partes.append(_duracion(segundos))
		if self.en_marcha and self.paso:
			partes.append(self.paso)
		elif not self.en_marcha and self.resumen:
			partes.append(self.resumen.splitlines()[0][:200])
		return ", ".join(partes)

	def segundos(self) -> float:
		if self.duracion_ms:
			return self.duracion_ms / 1000
		return max(0.0, (self.fin or time.time()) - self.inicio)

	def detalle(self) -> str:
		"""Todo lo que se sabe de ella, en texto para leer línea a línea."""
		lineas = [self.nombre(), f"Estado: {ESTADOS.get(self.estado, self.estado)}."]
		if self.tipo_agente:
			lineas.append(f"Tipo de subagente: {self.tipo_agente}.")
		if self.orden:
			lineas.append(f"Orden: {self.orden}")
		if self.segundo_plano:
			lineas.append("Corre en segundo plano: la conversación sigue sin esperarla.")
		cifras = []
		if self.usos_herramientas:
			cifras.append(f"{self.usos_herramientas} usos de herramientas")
		if self.tokens:
			cifras.append(f"{self.tokens} tokens")
		cifras.append(_duracion(self.segundos()))
		lineas.append("Lleva " + ", ".join(cifras) + "." if self.en_marcha else "Ha usado " + ", ".join(cifras) + ".")
		if self.encargo:
			lineas += ["", "Encargo:", self.encargo]
		if self.paso and self.en_marcha:
			lineas += ["", "Ahora: " + self.paso]
		if self.pasos:
			lineas += ["", "Pasos:"] + [f"- {paso}" for paso in self.pasos]
		if self.resumen:
			lineas += ["", "Resumen:", self.resumen]
		if self.salida:
			lineas += ["", "Salida (el final):", self.salida]
		if self.archivo_salida:
			lineas += ["", f"Su salida completa está en: {self.archivo_salida}"]
		return "\n".join(lineas)


def _duracion(segundos: float) -> str:
	segundos = int(segundos)
	if segundos < 60:
		return f"{segundos} s"
	minutos, segundos = divmod(segundos, 60)
	if minutos < 60:
		return f"{minutos} min {segundos} s"
	horas, minutos = divmod(minutos, 60)
	return f"{horas} h {minutos} min"


@dataclass(frozen=True)
class Cambio:
	"""Lo que supuso un evento para una tarea: para sonar y anunciar solo lo que importa."""

	tarea: Tarea
	empezada: bool = False
	terminada: bool = False


class RegistroActividad:
	"""Las tareas de todas las sesiones, acumuladas por sesión e ``id``."""

	def __init__(self) -> None:
		self._tareas: dict[str, dict[str, Tarea]] = {}
		self._candado = threading.Lock()

	def aplicar(self, clave: str, datos: dict[str, Any]) -> Cambio | None:
		"""Incorpora un evento ``ACTIVIDAD``. ``None`` si no trae ``id``."""
		identificador = str(datos.get("id") or "")
		if not identificador:
			return None
		with self._candado:
			tareas = self._tareas.setdefault(clave, {})
			tarea = tareas.get(identificador)
			empezada = tarea is None
			if tarea is None:
				tarea = tareas[identificador] = Tarea(identificador)
			antes = tarea.estado
			for campo in ("clase", "titulo", "tipo_agente", "encargo", "orden", "archivo_salida", "resumen"):
				valor = datos.get(campo)
				if valor:
					setattr(tarea, campo, str(valor))
			if datos.get("paso"):
				tarea.paso = " ".join(str(datos["paso"]).split())[:300]
			if datos.get("paso_nuevo"):
				tarea.pasos.append(" ".join(str(datos["paso_nuevo"]).split())[:600])
				del tarea.pasos[:-MAX_PASOS]
			if datos.get("salida"):
				tarea.salida = (tarea.salida + str(datos["salida"]))[-MAX_SALIDA:]
			for campo in ("tokens", "usos_herramientas", "duracion_ms"):
				valor = datos.get(campo)
				if isinstance(valor, (int, float)) and not isinstance(valor, bool):
					setattr(tarea, campo, int(valor))
			for campo in ("segundo_plano", "detenible"):
				if campo in datos:
					setattr(tarea, campo, bool(datos[campo]))
			estado = str(datos.get("estado") or "")
			if estado in ESTADOS:
				tarea.estado = estado
			terminada = antes == "en_marcha" and not tarea.en_marcha
			if terminada:
				tarea.fin = time.time()
				tarea.paso = ""
			elif not tarea.en_marcha and estado == "en_marcha":
				tarea.fin = 0.0
			tarea.version += 1
			self._recortar(tareas)
			return Cambio(tarea, empezada=empezada and tarea.en_marcha, terminada=terminada)

	@staticmethod
	def _recortar(tareas: dict[str, Tarea]) -> None:
		terminadas = sorted((t for t in tareas.values() if not t.en_marcha), key=lambda t: t.fin)
		for vieja in terminadas[:-MAX_TERMINADAS]:
			tareas.pop(vieja.id, None)

	def tareas(self, clave: str) -> list[Tarea]:
		"""Las de una sesión: primero las que siguen en marcha, después las terminadas (las últimas antes)."""
		with self._candado:
			lista = list(self._tareas.get(clave, {}).values())
		return sorted(lista, key=lambda t: (not t.en_marcha, -(t.fin or t.inicio)))

	def en_marcha(self, clave: str) -> list[Tarea]:
		return [t for t in self.tareas(clave) if t.en_marcha]

	def tarea(self, clave: str, identificador: str) -> Tarea | None:
		with self._candado:
			return self._tareas.get(clave, {}).get(identificador)

	def terminar_todas(self, clave: str, estado: str = "detenida") -> list[Tarea]:
		"""Al cerrarse la conversación: lo que seguía en marcha se da por ``estado`` (nada se queda «en marcha» para siempre)."""
		cerradas = []
		with self._candado:
			for tarea in self._tareas.get(clave, {}).values():
				if tarea.en_marcha:
					tarea.estado = estado
					tarea.fin = time.time()
					tarea.paso = ""
					tarea.version += 1
					cerradas.append(tarea)
		return cerradas

	def olvidar(self, clave: str) -> None:
		with self._candado:
			self._tareas.pop(clave, None)

	def resumen(self, clave: str) -> str:
		"""Frase para el Estado del turno: «2 subagentes y 1 orden en segundo plano: ...» o «»."""
		vivas = self.en_marcha(clave)
		if not vivas:
			return ""
		subagentes = [t for t in vivas if t.clase == "subagente"]
		ordenes = [t for t in vivas if t.clase == "orden"]
		otras = [t for t in vivas if t.clase not in ("subagente", "orden")]
		partes = []
		if subagentes:
			partes.append(f"{len(subagentes)} subagente" + ("s" if len(subagentes) != 1 else ""))
		if ordenes:
			partes.append(f"{len(ordenes)} orden" + ("es" if len(ordenes) != 1 else ""))
		if otras:
			partes.append(f"{len(otras)} tarea" + ("s" if len(otras) != 1 else ""))
		cuantas = " y ".join([", ".join(partes[:-1]), partes[-1]] if len(partes) > 1 else partes)
		nombres = "; ".join(t.nombre() + (f": {t.paso}" if t.paso else "") for t in vivas[:3])
		return f"En marcha {cuantas}: {nombres}."
