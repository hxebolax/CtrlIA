"""Controles y utilidades accesibles para wxPython (plantilla del agente «accesibilidad-wx»).

Copie la carpeta ``accesibilidad`` a ``app/ui/`` del proyecto (o adapte las
importaciones si el paquete se llama distinto). Solo depende de wxPython y,
opcionalmente, de accessible_output2 para la voz.

Arranque recomendado (tras crear ``wx.App``)::

	import wx
	from app.ui.accesibilidad import idioma, voz

	wx.HelpProvider.Set(wx.SimpleHelpProvider())   # Ctrl+H necesita esto
	self._idioma = idioma.fijar_idioma_espanol()    # botones estándar en español
	voz.configurar(retraso_ms=120)
"""

from app.ui.accesibilidad.casillas import CheckListBoxAccesible
from app.ui.accesibilidad.controles import SliderAccesible, nombrar_ventana_generica
from app.ui.accesibilidad.composicion import PreferenciasLectura, componer_anuncio_celda
from app.ui.accesibilidad.dialogos import (
	DialogoMensaje,
	DialogoTexto,
	confirmar,
	crear_botonera,
	elegir_opcion,
	mostrar_error,
	mostrar_mensaje,
	pedir_texto,
	preparar_dialogo,
)
from app.ui.accesibilidad.lista import ListaAccesible
from app.ui.accesibilidad.nombres import (
	asignar_nombre_accesible,
	crear_campo,
	crear_selector_ruta,
	etiquetar_selector,
	nombrar_campo_interno,
)
from app.ui.accesibilidad.teclado import cerrar_con_escape, instalar_atajos, vincular_menu_contextual
from app.ui.accesibilidad.progreso import IndicadorProgreso

__all__ = [
	"CheckListBoxAccesible",
	"DialogoMensaje",
	"DialogoTexto",
	"IndicadorProgreso",
	"ListaAccesible",
	"PreferenciasLectura",
	"SliderAccesible",
	"asignar_nombre_accesible",
	"cerrar_con_escape",
	"componer_anuncio_celda",
	"confirmar",
	"crear_botonera",
	"crear_campo",
	"crear_selector_ruta",
	"elegir_opcion",
	"etiquetar_selector",
	"instalar_atajos",
	"mostrar_error",
	"mostrar_mensaje",
	"nombrar_campo_interno",
	"nombrar_ventana_generica",
	"pedir_texto",
	"preparar_dialogo",
	"vincular_menu_contextual",
]
