# Etiquetas y nombres accesibles

Comprobado con MSAA (la interfaz que consultan NVDA, JAWS y Narrador) en wxPython 4.2.x y 4.3.x, salvo indicación. Fuente de la regla: Microsoft, «Ensuring that UI Elements are Correctly Named» (https://learn.microsoft.com/en-us/windows/win32/winauto/ensure-that-ui-elements-are-named-correctly).

## Cómo obtiene el lector el nombre

- **Botones, casillas, botones de opción y elementos de menú** usan su propio texto (`label`).
- **Campos de texto, Choice, ComboBox, ListBox, ListCtrl, TreeCtrl, Slider, Gauge, SpinCtrl y DatePicker** ignoran su texto y buscan la etiqueta estática (o el título de un grupo) **inmediatamente anterior en el orden Z**. En wxPython el orden Z de los hermanos es el **orden de creación**.

Por eso, **la etiqueta se crea justo antes del control y con el mismo padre**:

```python
etiqueta = wx.StaticText(panel, label=_("&Nombre:"))
self.nombre = wx.TextCtrl(panel)          # nombre accesible: «Nombre:», atajo Alt+N
```

O con la plantilla: `_, self.nombre = crear_campo(panel, _("&Nombre:"), wx.TextCtrl, ayuda=_("..."), sizer=sizer)`.

## Qué funciona y qué no

| Caso | Resultado |
|---|---|
| `StaticText("&Nombre:")` y después el control | Nombre «Nombre:», atajo «Alt+n» |
| Control creado antes que la etiqueta | Sin nombre |
| Etiqueta tardía + `MoveBeforeInTabOrder` | Sin nombre: solo cambia el Tab de wx, no el orden Z |
| `wx.TextCtrl(name="...")` o `SetName()` sin etiqueta | Sin nombre: `name` no afecta a controles nativos |
| Etiqueta oculta con `Hide()` | Sigue dando nombre (útil para controles sin etiqueta visible) |
| `etiqueta.SetLabel("Otro:")` después | El nombre cambia (dinámico) |
| `SetToolTip` / `SetHelpText` | No se exponen como descripción MSAA: sirven para Ctrl+H, no para el lector |

`name=` sigue siendo útil: Ctrl+H lo usa, los registros lo muestran y las pruebas lo encuentran. Pero no es lo que oye el usuario.

## Grupos (wx.StaticBox)

- **Estilo antiguo** (controles **hermanos** de la caja): el primer control sin etiqueta creado tras la caja **recibe como nombre el título del grupo**. Es una trampa.
- **Estilo moderno** (controles **hijos** de `sizer.GetStaticBox()`): cada control necesita su propia etiqueta dentro de la caja.

```python
grupo = wx.StaticBoxSizer(wx.VERTICAL, panel, _("Opciones de descarga"))
caja = grupo.GetStaticBox()
_, self.carpeta = crear_campo(caja, _("&Carpeta:"), wx.TextCtrl, sizer=grupo)
self.subtitulos = wx.CheckBox(caja, label=_("Descargar &subtítulos"))
grupo.Add(self.subtitulos, 0, wx.ALL, 5)
```

NVDA anuncia el título del grupo al entrar en él.

## Ventanas genéricas (no nativas)

`wx.Panel`, páginas de `wx.Notebook`, `HtmlWindow`, `Grid`, `SearchCtrl`, `FilePickerCtrl` y controles de `wx.lib` son ventanas dibujadas por wx. Su nombre es su **texto de ventana**:

- `name="Vista previa"` **en el constructor** funciona.
- `SetName()` después de crear **no** funciona.
- `SetLabel("Vista previa")` después de crear **sí** funciona (`nombrar_ventana_generica`).

En las páginas de pestañas, dale a cada página el título de su pestaña.

## Controles compuestos

- **`wx.SpinCtrl`** (nativo): el campo interno toma la etiqueta anterior. Correcto.
- **`wx.SpinCtrlDouble`** y **`wx.SearchCtrl`** (genéricos): su campo interno queda sin nombre. Usa `nombrar_campo_interno(control, "Nombre")` (comprobado con MSAA).
- **`wx.DirPickerCtrl` / `wx.FilePickerCtrl`**: el campo interno queda sin nombre, y asignarle un `wx.Accessible` no funcionó en una prueba MSAA con 4.2.3. Lo más fiable es `crear_selector_ruta` (etiqueta + TextCtrl + botón «Examinar...» que abre el diálogo nativo), igual que hace NVDA en su interfaz.

## wx.Accessible: cuándo y cómo

Úsalo solo para lo que falta. Sobrescribe el mínimo y devuelve `wx.ACC_NOT_IMPLEMENTED` en lo demás: así el resto lo sigue dando el objeto nativo de Windows.

- `childId == 0` es el propio control; `childId > 0` es el hijo n (empezando en 1).
- Los métodos devuelven tuplas: `GetName(childId) -> (wx.ACC_OK, "texto")`, `GetRole -> (wx.ACC_OK, wx.ROLE_SYSTEM_...)`, `GetState -> (wx.ACC_OK, máscara)`, `GetValue`, `GetDescription`...
- Usa las constantes `wx.ROLE_SYSTEM_*` (wx las traduce), no las numéricas de oleacc.
- Instálalo con **`control.SetAccessible(objeto)`** justo después de crear el control. Sobrescribir `CreateAccessible` en Python no tuvo efecto en las pruebas.
- Guarda una referencia (`self._accesible = ...`): no hace daño y evita sorpresas con el recolector.
- Tras un cambio de nombre, estado o valor que el control nativo no notifica, avisa: `wx.Accessible.NotifyEvent(wx.ACC_EVENT_OBJECT_NAMECHANGE, ventana, wx.OBJID_CLIENT, id_hijo)` o `NotifyWinEvent` de user32.
- En wxPython 4.1.x de 64 bits `wx.Accessible` no funcionaba; desde 4.2 sí.

## Comprobarlo

- Automático: `tests/msaa.py` → `propiedades(control)["nombre"]`.
- Manual: Accessibility Insights for Windows o Inspect.exe (Windows SDK), NVDA con NVDA+Tab (repite el foco) y NVDA+F1 (información del objeto).
