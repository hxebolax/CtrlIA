# Listas accesibles: ListCtrl, casillas y CheckListBox

## wx.ListCtrl (modo informe)

En Windows es el control nativo `SysListView32`. NVDA lo lee de forma nativa al moverse con arriba y abajo: el nombre de la fila (primera columna) y el resto de columnas con sus cabeceras, según su configuración. Por eso:

- **La aplicación NO locuta la fila al cambiar de selección o de foco.** Hacerlo provoca doble lectura.
- Lo que sí aporta la aplicación es la **lectura lógica por columnas** (el lector no tiene «columna actual»):
  - Izquierda y Derecha: columna anterior y siguiente, en el orden visible, saltando las de ancho 0.
  - Ctrl+1…Ctrl+9: lee esa columna; Ctrl+0, la décima; si no existe, «No existe la columna N».
  - Ctrl+Mayús+Inicio y Ctrl+Mayús+Fin: primera y última columna.
  - Consultar una columna **no cambia la selección**, no abre nada y no mueve el foco.

### Texto íntegro

Windows recorta el texto de las celdas: el objeto MSAA estándar da la primera columna con un máximo de 255 caracteres, y NVDA lee cada columna hasta unos 512. `GetItemText` tampoco sirve para textos largos. `ListaAccesible` guarda el **texto completo en un almacén paralelo** y lo usa al leer columnas. Para la lectura nativa, mantén las celdas por debajo de unos 255 caracteres cuando sea posible y ofrece el texto completo por la lectura por columnas o en un diálogo de detalles.

### Foco y selección

- El lector sigue el elemento **enfocado**, no el seleccionado. Para ir a una fila: `Select(i)` + `Focus(i)` + `EnsureVisible(i)` (`enfocar_fila`).
- Una lista con filas y **sin elemento enfocado** se anuncia solo como «lista». `ListaAccesible` enfoca la primera fila al rellenarla.
- En selección múltiple, «ir a una fila» deja seleccionada solo esa.

### Actualizar sin mover al usuario

- Al **insertar** filas por encima del elemento del usuario, este debe seguir en el mismo elemento lógico. En modo normal lo hace el control nativo; en modo virtual hay que recolocar el foco (`_mover_foco_silencioso`).
- Al **borrar** la fila enfocada, el foco pasa a la vecina (misma posición o la última).
- Al **recargar** (`reemplazar_filas`), se conserva la posición.
- **No repintes la fila enfocada si no cambió**: algunos lectores la releen (`actualizar_celda` compara antes).
- Una actualización en segundo plano nunca roba el foco ni relee la lista.
- Para que un complemento de NVDA ignore un movimiento automático del foco, `ListaAccesible` puede poner una propiedad de ventana temporal (`propiedad_silencio`). Sin ese complemento, recolocar el foco puede leerse; es inevitable.

### Modo virtual (LC_VIRTUAL)

Para miles de filas: el control solo pide el texto de las celdas visibles (`OnGetItemText`). La API de `ListaAccesible` es la misma en ambos modos. En modo virtual no hay casillas nativas salvo que se implemente `OnGetItemIsChecked`.

### Teclas de acción

- **Intro** activa el elemento (`EVT_LIST_ITEM_ACTIVATED`), también dentro de diálogos con botón predeterminado.
- **Espacio** mantiene su comportamiento nativo (en listas con casillas, marca y desmarca). Si la aplicación le da otra función (reproducir, previsualizar), que sea explícita (`accion_espacio`) y esté documentada.
- **Menú contextual** con la tecla Aplicaciones y Mayús+F10 (`vincular_menu_contextual`), colocado junto al elemento enfocado.
- No consumas combinaciones que no gestionas: `evento.Skip()`.

### Preferencias de lectura por columnas

Recorrido completo que debe existir y probarse:

1. **Valores por defecto** en la configuración: número de fila, cabecera y contenido activados.
2. **Diálogo de Preferencias**: tres casillas con etiqueta clara y una ayuda que explique que, si se desactivan las tres, se leerá el contenido.
3. **Guardado** al Aceptar/Aplicar; Cancelar no cambia nada.
4. **Lectura en el momento del anuncio** (`leer_preferencias` devuelve las vigentes): las listas abiertas cambian sin reiniciar.
5. **Composición** con `componer_anuncio_celda` (probada con la matriz de 8 combinaciones).
6. **Salida** por el canal de voz de navegación.

Estas preferencias no son las de «avisos del sistema». Explica en la interfaz qué controla cada ajuste.

### Casillas en ListCtrl

`EnableCheckBoxes(True)`, `CheckItem`, `IsItemChecked`, `EVT_LIST_ITEM_CHECKED`/`UNCHECKED` (wxPython 4.1+). Cada elemento se expone con rol de casilla y estado marcado (comprobado con MSAA). **No uses `wx.lib.mixins.listctrl.CheckListCtrlMixin`**: dibuja las casillas con imágenes y el lector no las ve.

El anuncio de «marcada/no marcada» desde la aplicación (`anunciar_casillas`) es un respaldo: compruébalo con NVDA y desactívalo si se oye dos veces.

## wx.CheckListBox

En wxPython 4.2.x (wxWidgets 3.2) sus elementos se exponen como elementos de lista **sin estado marcado**: el lector no dice si están marcados. Afecta también a `wx.MultiChoiceDialog`, que lo usa por dentro. wxPython 4.3 (wxWidgets 3.3) lo corrige de serie.

Solución (la de NVDA en `gui/nvdaControls.py`, `CustomCheckListBox`), implementada en `CheckListBoxAccesible`:

1. `wx.Accessible` que devuelve `ROLE_SYSTEM_CHECKBUTTON` para cada hijo y el estado `CHECKED`/`SELECTED`/`FOCUSED` real.
2. `NotifyWinEvent(EVENT_OBJECT_STATECHANGE, hwnd, OBJID_CLIENT, índice + 1)` al marcar, tanto por el usuario (`EVT_CHECKLISTBOX`) como desde código (`Check`), porque wx no genera el evento en el segundo caso.
3. Instalación automática solo en wxPython < 4.3 (`forzar_accesible` para cambiarlo).

Alternativa: `ListaAccesible(con_casillas=True)` de una sola columna.

## Otros controles de lista

- `wx.ListBox` y `wx.TreeCtrl` son nativos y accesibles.
- `wx.dataview.DataViewCtrl` tiene accesibilidad propia pero más pobre (sin columnas por separado); no toma la etiqueta anterior (usa `name=` o `SetName`). Para tablas, mejor `ListCtrl`.
- `UltimateListCtrl` y los controles de `wx.lib.agw`: inaccesibles (rol genérico, sin hijos).
- `wx.grid.Grid`: inaccesible en 4.2.x; accesibilidad básica en 4.3.x.
