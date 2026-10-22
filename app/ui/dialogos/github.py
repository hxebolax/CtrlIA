"""Clonar un repositorio de GitHub para trabajar en él (usa ``gh`` en segundo plano)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import wx

from app.services import git
from app.ui import avisos
from app.ui.accesibilidad.dialogos import mostrar_error, pedir_texto
from app.ui.comun import _

log = logging.getLogger(__name__)


def texto_dueno(repositorio: dict[str, str]) -> str:
	"""Columna «Dueño»: «ana (tuyo)» o «MiEmpresa (organización)»."""
	dueno = repositorio.get("dueno") or repositorio.get("nombre", "").split("/", 1)[0]
	if repositorio.get("tipo_dueno") == "Organización":
		return _("{d} (organización)").format(d=dueno)
	return _("{d} (tuyo)").format(d=dueno)


def filas_repositorios(repos: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
	"""Filas del selector: un repositorio por fila y, al final y siempre visible, escribirlo a mano."""
	filas = [(r["nombre"], texto_dueno(r), r.get("descripcion") or _("Sin descripción"), r.get("privado", ""))
		for r in repos]
	filas.append((_("Otro repositorio: escribir dueño/nombre a mano..."), "", "", ""))
	return filas


def clonar_repositorio(ventana, al_terminar: Callable[[Path], None]) -> None:
	"""Lista tus repositorios y los de tus organizaciones, deja elegir uno (o escribirlo) y una carpeta,
	lo clona y llama a ``al_terminar(carpeta)``.

	``ventana`` es la ventana principal (usa su ``en_segundo_plano``).
	"""
	avisos.anunciar(_("Consultando tus repositorios de GitHub..."))

	def elegido(repos: list[dict[str, str]]) -> None:
		from app.ui.dialogos.selector import elegir

		nombre = ""
		if repos:
			indice = elegir(ventana, _("Clonar un repositorio de GitHub"), _("&Repositorios:"), filas_repositorios(repos),
				[_("Repositorio"), _("Dueño"), _("Descripción"), _("Visibilidad")],
				ayuda=_("Tus repositorios y los de tus organizaciones. Elige uno con las flechas y pulsa Intro. "
					"La última fila, que se ve siempre aunque filtres, permite escribir dueño/nombre a mano."),
				fijas=1)
			if indice is None:
				return
			if indice < len(repos):
				nombre = repos[indice]["nombre"]
		if not nombre:
			nombre = (pedir_texto(ventana, _("&Repositorio (dueño/nombre):"), _("Clonar un repositorio de GitHub")) or "").strip()
			if not nombre:
				return
		with wx.DirDialog(ventana, _("Carpeta donde se creará la copia de «{n}»").format(n=nombre),
				style=wx.DD_DEFAULT_STYLE) as dialogo:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			padre = Path(dialogo.GetPath())
		avisos.anunciar(_("Clonando {n}. Puede tardar un poco...").format(n=nombre))

		def hecho(carpeta: Path) -> None:
			avisos.notificar(_("Repositorio clonado en {c}.").format(c=carpeta), padre=ventana)
			al_terminar(carpeta)

		ventana.en_segundo_plano(git.clonar, nombre, padre, al_terminar=hecho,
			al_fallar=lambda error: mostrar_error(ventana, str(error), _("Clonar")))

	def fallo(error: BaseException) -> None:
		if isinstance(error, git.ErrorGit) and "gh" in str(error):
			mostrar_error(ventana, str(error), _("GitHub"))
			return
		log.info("No se pudo listar GitHub: %s", error)
		elegido([])

	ventana.en_segundo_plano(git.listar_repositorios, al_terminar=elegido, al_fallar=fallo)
