# Qué controles de wxPython son accesibles en Windows

Situación en wxPython 4.2.x (wxWidgets 3.2), con las mejoras de 4.3.x (wxWidgets 3.3) donde las hay. Comprobado con MSAA salvo lo marcado como «sin verificar».

| Control | Situación | Qué hacer |
|---|---|---|
| `wx.Button`, `wx.CheckBox`, `wx.RadioButton` | Nativos; nombre desde su etiqueta | Etiqueta clara con `&` |
| `wx.TextCtrl` (también multilínea y de solo lectura) | Nativo; nombre desde la etiqueta anterior | Etiqueta antes; solo lectura para textos largos |
| `wx.Choice`, `wx.ComboBox`, `wx.ListBox` | Nativos | Etiqueta antes |
| `wx.ListCtrl` (informe y virtual) | Nativo | `ListaAccesible` |
| `wx.CheckListBox` | Sin estado marcado en 4.2.x; corregido en 4.3 | `CheckListBoxAccesible` |
| `wx.TreeCtrl` | Nativo | Etiqueta antes |
| `wx.SpinCtrl` | Nativo; el campo interno toma la etiqueta | Seleccionar el valor al enfocar |
| `wx.SpinCtrlDouble` | Genérico; campo interno sin nombre | `nombrar_campo_interno` |
| `wx.Slider` | Nativo, pero su valor MSAA es el porcentaje del rango | `SliderAccesible` («5 de 10»); ajustar `LineSize` y `PageSize`; las etiquetas de mínimo y máximo, después |
| `wx.Gauge` | Nativo; valor «40 %» | Etiqueta antes; o `IndicadorProgreso` |
| `wx.adv.DatePickerCtrl`, `TimePickerCtrl` | Nativos | Etiqueta antes |
| `wx.DirPickerCtrl`, `wx.FilePickerCtrl` | Campo interno sin nombre | `crear_selector_ruta` |
| `wx.SearchCtrl` | Genérico; campo interno sin nombre | `wx.TextCtrl` con etiqueta, o `nombrar_campo_interno` |
| `wx.StaticText` | No recibe el foco | No usarla para información que haya que repasar |
| `wx.Notebook` | Nativo; pestañas con nombre; páginas se anuncian como «panel» | Nombre para cada página (`name=` o `SetLabel`) |
| Choicebook, Listbook, Treebook | Usan controles nativos; sin verificar con lector | Probar |
| `wx.Menu`, `wx.MenuBar` | Nativos | `&` y `\t` con el atajo |
| `wx.ToolBar` | Nativa; nombre desde `shortHelp`; fuera del orden de Tab | Poner `shortHelp` y duplicar cada acción en el menú |
| `wx.StatusBar` | Nativa, sin nombre; NVDA la lee con NVDA+Fin | No confiar en ella para avisos importantes |
| `wx.adv.HyperlinkCtrl` | Nativo (SysLink) | Preferir a `wx.lib.agw.hyperlink` |
| `wx.adv.TaskBarIcon` | Icono de bandeja nativo; sin verificar con lector | Tooltip y menú con `CreatePopupMenu` |
| `wx.dataview.DataViewCtrl` | Accesibilidad propia limitada; no toma la etiqueta | `SetName`; para tablas, `ListCtrl` |
| `wx.grid.Grid` | Inaccesible en 4.2.x; básico en 4.3 | `ListCtrl` |
| `wx.richtext.RichTextCtrl` | Contenido inaccesible en 4.2.x | `wx.TextCtrl(style=wx.TE_MULTILINE \| wx.TE_RICH2)` |
| `wx.stc.StyledTextCtrl` | 4.2.x: los lectores no aplican su soporte de Scintilla | Evitar para usuarios ciegos en 4.2 |
| `wx.html.HtmlWindow` | Solo nombre; contenido inaccesible | Mostrar el texto en un `TextCtrl` de solo lectura, o un WebView bien probado |
| `wx.html2.WebView` (Edge) | Contenido accesible, pero problemas de foco y aceleradores | Probar a fondo; ofrecer alternativa de texto |
| `wx.lib.buttons.GenButton` y controles de `wx.lib` | Rol genérico, no de botón | `wx.Button`, o `wx.Accessible` con el rol correcto |
| `wx.lib.agw.*`, `UltimateListCtrl` | Dibujados a mano, sin hijos accesibles | Evitar |
| `wx.lib.mixins.listctrl.CheckListCtrlMixin` | Casillas invisibles para el lector | `EnableCheckBoxes(True)` |

## Diálogos estándar

- `wx.MessageDialog`/`MessageBox` (TaskDialog), `wx.FileDialog`, `wx.DirDialog`, `wx.ProgressDialog`, `wx.ColourDialog`, `wx.FontDialog`: nativos y accesibles.
- `wx.TextEntryDialog`, `wx.SingleChoiceDialog`: accesibles.
- `wx.MultiChoiceDialog`: casillas sin estado en 4.2.x.

## Cuándo compensa subir a wxPython 4.3

Si el proyecto usa `CheckListBox`, `Grid`, `RichTextCtrl` o `StyledTextCtrl`, wxPython 4.3 (wxWidgets 3.3) mejora su accesibilidad. Comprueba antes que hay ruedas para la versión de Python del proyecto y prueba la aplicación completa.
