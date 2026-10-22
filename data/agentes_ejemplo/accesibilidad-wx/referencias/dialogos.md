# Diálogos accesibles y botones en español

## Botones en español

- `wx.Locale(wx.LANGUAGE_SPANISH)` tras crear `wx.App`, **conservando la referencia** toda la sesión (`fijar_idioma_espanol`). wxPython registra solo su carpeta `wx/locale` y trae `es/LC_MESSAGES/wxstd.mo`: los botones estándar (Aceptar, Cancelar, Sí, No, Aplicar...) pasan a español aunque Windows esté en otro idioma. En la versión compilada, el paquete `wx` debe ir fuera del zip para que ese catálogo viaje.
- Además, las plantillas ponen **etiquetas explícitas** (`&Aceptar`, `&Cancelar`, `&Sí`, `&No`): no dependen del catálogo.
- Los cuadros nativos de Windows (`wx.MessageDialog`/`wx.MessageBox`, que usan TaskDialog; `wx.FileDialog`, `wx.DirDialog`) muestran sus botones en el idioma de Windows. Para forzarlos en un `MessageDialog`: `SetYesNoLabels`, `SetOKCancelLabels`, `SetYesNoCancelLabels`, `SetOKLabel` (`dialogo_nativo`).

## Estructura de un diálogo accesible

```python
class DialogoPerfil(wx.Dialog):
	def __init__(self, padre):
		super().__init__(padre, title=_("Editar perfil"))
		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)
		_, self.nombre = crear_campo(panel, _("&Nombre:"), wx.TextCtrl, ayuda=_("Nombre del perfil."), sizer=sizer)
		_, self.formato = crear_campo(panel, _("&Formato:"), wx.Choice, choices=["MP3", "OGG"], sizer=sizer)
		self.subtitulos = wx.CheckBox(panel, label=_("Descargar &subtítulos"))
		sizer.Add(self.subtitulos, 0, wx.ALL, 5)
		sizer.Add(crear_botonera(panel, wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 10)
		panel.SetSizer(sizer)
		marco = wx.BoxSizer(wx.VERTICAL)
		marco.Add(panel, 1, wx.EXPAND)
		self.SetSizerAndFit(marco)
		preparar_dialogo(self, foco_inicial=self.nombre)
```

Qué garantiza:

- Cada campo tiene nombre (etiqueta creada antes) y atajo Alt+letra.
- Intro acepta (botón predeterminado); Escape cancela, **salvo que haya un desplegable abierto**, que se cierra primero.
- Ctrl+H dice la ayuda del control enfocado; los controles sin ayuda reciben una genérica.
- El foco empieza en el primer campo útil. En un campo de una línea se selecciona todo; en uno de solo lectura o multilínea, el cursor va al principio.
- Al cerrar, el foco vuelve al control que abrió el diálogo (`_mostrar`, `pedir_texto`).

## Mensajes y confirmaciones

| Necesidad | Función | Notas |
|---|---|---|
| Informar | `mostrar_mensaje(padre, texto, titulo)` | Botón «Aceptar» |
| Error | `mostrar_error(padre, texto)` | Icono de error; texto concreto y cómo resolverlo |
| Sí/No | `confirmar(padre, texto, titulo, peligroso=True)` | Con `peligroso`, Intro = «No» |
| Elegir una acción | `elegir_opcion(padre, texto, titulo, ["&Eliminar", "&Mover a la papelera"])` | Botones que dicen la acción; Cancelar añadido; devuelve el índice o `None` |
| Pedir texto | `pedir_texto(padre, "&Nombre:", "Renombrar", valor, max_caracteres=80)` | Etiqueta como nombre del campo; `None` si se cancela |
| Cuadro de Windows | `dialogo_nativo(...)` | El más compatible; su texto no se puede recorrer ni copiar |

**Texto largo o de varias líneas:** en un `TextCtrl` multilínea de solo lectura (lo hace `DialogoMensaje` si el texto tiene saltos o más de 200 caracteres). Una `StaticText` no recibe el foco: el lector la lee al abrir el diálogo, pero después no se puede repasar ni copiar.

## Validación de formularios

- Valida al pulsar Aceptar, no a cada tecla.
- Mensaje concreto («El puerto debe estar entre 1 y 65535»), y el foco vuelve al campo que hay que corregir, con su contenido seleccionado.
- No indiques un error solo con color, icono o tooltip.
- Los controles deshabilitados deben entenderse: explica en la ayuda por qué están deshabilitados o qué los habilita.

## Progreso

- Tarea que bloquea: `wx.ProgressDialog` nativo con `wx.PD_CAN_ABORT | wx.PD_APP_MODAL | wx.PD_AUTO_HIDE`. En Windows es un TaskDialog accesible; `Update()` devuelve `(continuar, saltar)`.
- Tarea en segundo plano con la ventana usable: `IndicadorProgreso` (estado consultable + barra + voz solo en fases e hitos del 25 %).
- Cancelar debe decir que se está cancelando, terminar de forma segura y dejar un foco útil.
- Anuncia el final (éxito, error o cancelación) con `interrumpir=False` para no cortar lo que la persona está leyendo.

## Diálogos de wx que conviene conocer

- `wx.TextEntryDialog`, `wx.SingleChoiceDialog`: el mensaje se crea antes del control, así que lo nombra. Son accesibles.
- `wx.MultiChoiceDialog`: usa un CheckListBox; en 4.2.x no anuncia el estado de las casillas. Usa un diálogo propio con `CheckListBoxAccesible`.
- `wx.FileDialog`, `wx.DirDialog`, `wx.ColourDialog`, `wx.FontDialog`: nativos.
- `wx.adv.NotificationMessage`: en Windows 10+ necesita un acceso directo con AppUserModelID para salir como notificación del sistema; NVDA la lee si tiene activado «Informar notificaciones».
