# Teclado y foco

## Eventos de teclado en wxPython

| Evento | Cuándo usarlo |
|---|---|
| `EVT_CHAR_HOOK` | Atajos de toda una ventana o diálogo (Escape, Ctrl+H). Llega antes que ningún otro y sube hasta la ventana de nivel superior. **Sin `evento.Skip()` para lo que no gestionas, deja de funcionar todo el teclado de la ventana.** |
| `EVT_KEY_DOWN` | Teclas físicas dentro de un control concreto (flechas en una lista). No se propaga al padre. |
| `EVT_CHAR` | Caracteres ya traducidos (lo que se escribe). |
| `wx.AcceleratorTable` | Atajos de comandos sin elemento de menú (`instalar_atajos`). |
| Menús con `\t` | `"&Guardar\tCtrl+S"`: registra el atajo y el lector lo anuncia en el menú. |

Compara modificadores con `evento.GetModifiers() == wx.MOD_CONTROL` (exacto) y no con `ControlDown()`, para que Ctrl+Mayús+X no dispare el atajo de Ctrl+X.

## Reglas

- **Toda acción tiene teclado.** Lo que se hace con el ratón, con una barra de herramientas o con un menú contextual, se puede hacer también desde el menú principal con atajo. Las barras de herramientas no están en el orden de Tab; ponles siempre `shortHelp`.
- **Tab y Mayús+Tab en orden lógico.** El orden es el de creación. Crea los controles en el orden de lectura (de arriba abajo y de izquierda a derecha). `MoveAfterInTabOrder` corrige el Tab pero no el nombre accesible.
- **Aceleradores sin conflictos** dentro de cada ventana: dos «&A» en el mismo diálogo obligan a pulsar varias veces. Revisa las letras al añadir controles.
- **Escape**: cierra el diálogo o la ventana secundaria, salvo que haya un desplegable abierto (se cierra primero) o un menú. `preparar_dialogo` y `cerrar_con_escape` lo hacen.
- **Menú contextual** con `EVT_CONTEXT_MENU` (tecla Aplicaciones y Mayús+F10), no solo con `EVT_RIGHT_UP`. Si la posición es `wx.DefaultPosition`, el evento viene del teclado: coloca el menú junto al elemento enfocado (`vincular_menu_contextual`).
- **Ayuda contextual Ctrl+H** en todas las ventanas: nombre del control y para qué sirve. Requiere `wx.HelpProvider.Set(wx.SimpleHelpProvider())`.
- **F1** abre el manual o la ayuda de la ventana; documenta todos los atajos en una lista de teclas.
- **No consumas teclas del lector.** Evita atajos globales con Insert o Bloq Mayús (teclas NVDA habituales), y combinaciones que el lector usa en modo foco.
- **`wx.WANTS_CHARS`** solo si el control gestiona Tab e Intro por sí mismo; si no, rompe la navegación.

## Foco

- **Foco inicial útil** al abrir una ventana o diálogo: el primer campo que hay que rellenar, la lista principal o, en un mensaje, el texto o el botón principal. Fíjalo en `EVT_INIT_DIALOG` (o con `wx.CallAfter(control.SetFocus)` en ventanas) para que el lector anuncie solo ese control.
- **Retorno del foco** al cerrar un diálogo: al control que lo abrió (o al elemento de lista sobre el que se actuó). Tras borrar un elemento, al vecino.
- **Nunca robes el foco** desde una tarea en segundo plano (una descarga que termina, un mensaje que llega). Anuncia, pero no muevas.
- **Tras un error de validación**, el foco va al campo que hay que corregir.
- **Elemento enfocado en las listas**: siempre que tengan filas.
- **Al ocultar o deshabilitar el control enfocado**, mueve antes el foco a otro lugar lógico; si no, el foco queda en el vacío y el lector calla.
- **Ventana principal al frente** al arrancar o al traerla desde la bandeja: `Raise()` y, si hace falta, restaurar el foco al último control usado.

## Controles con particularidades de teclado

- `wx.SpinCtrl`: flechas arriba y abajo; conviene seleccionar el valor al recibir el foco para escribir encima.
- `wx.Slider`: `SetLineSize` (flechas) y `SetPageSize` (Re Pág/Av Pág) adecuados al rango.
- `wx.Notebook`: Ctrl+Tab y Ctrl+Mayús+Tab cambian de página; las flechas, cuando el foco está en las pestañas.
- `wx.TextCtrl` multilínea: Tab inserta tabulación solo con `wx.TE_PROCESS_TAB`; si no, sale del campo. En los ricos (`TE_RICH2`), Mayús+Intro inserta un salto «blando» que el lector lee como un carácter raro: conviértelo en un salto normal.
- `wx.html2.WebView`: el foco no siempre entra y puede tragarse aceleradores; pruébalo a fondo.
