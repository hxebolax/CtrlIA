# Ventana principal: menús, atajos, bandeja, geometría y cierre

## Barra de menús

Cinco menús, en este orden y con estas teclas de acceso (son las habituales de Windows en español y no chocan entre sí):

- `&Archivo` (Alt+A)
- `&Editar` (Alt+E)
- `&Ver` (Alt+V)
- `&Herramientas` (Alt+H)
- `Ay&uda` (Alt+U; la A ya la usa Archivo)

Reglas para cada elemento:

- **Tecla de acceso con `&`** y sin repetir letra dentro del mismo menú (una prueba lo comprueba).
- **Atajo tras `\t`**: `"&Abrir...\tCtrl+O"`. El lector lo anuncia al recorrer el menú, así se descubre sin manual. Los modificadores se escriben en inglés (`Ctrl`, `Shift`, `Alt`) porque así los interpreta wx; con `wx.Locale` en español, wx los muestra traducidos («Mayús»).
- **Tres puntos** (`...`) cuando el elemento abre un diálogo que pide algo más.
- **Texto de ayuda** en cada elemento (tercer argumento de `Append`): aparece en la barra de estado y documenta la acción.
- **Nada en inglés**: ni etiquetas ni elementos de `wx.ID_*` con su etiqueta de serie; se pone siempre la etiqueta explícita.
- Los menús se definen en una tabla (`MENUS`) y se construyen en un bucle: así las pruebas los recorren y añadir un elemento es una línea más.

## Atajos estándar de Windows en español

Úsalos con este significado; no los reasignes a otra cosa:

- Ctrl+N nuevo, Ctrl+O abrir, Ctrl+S guardar, Ctrl+Shift+S guardar como, Ctrl+P imprimir.
- Ctrl+Z deshacer, Ctrl+Y rehacer, Ctrl+X cortar, Ctrl+C copiar, Ctrl+V pegar, Ctrl+A seleccionar todo.
- Ctrl+F buscar, F3 buscar siguiente, Shift+F3 buscar anterior, Ctrl+H: en estas aplicaciones, ayuda del control enfocado (no «reemplazar»).
- F1 ayuda o manual, F2 renombrar, F5 actualizar, F6 cambiar de panel, F10 o Alt la barra de menús.
- Supr eliminar, Intro abrir o activar, Aplicaciones o Shift+F10 menú contextual.
- Alt+F4 cerrar la ventana; Escape cierra diálogos, **nunca** la ventana principal.
- Ctrl+, (coma) opciones: convención moderna, poco conflictiva y el lector la dice bien («Control+coma»).

Reservados por Windows o por los lectores de pantalla (no los uses): Insert y Bloq Mayús combinados con cualquier tecla (modificador de NVDA y JAWS), Windows+cualquier tecla, Ctrl+Alt+flechas y Ctrl+Alt+N (NVDA), Alt+Tab, Ctrl+Esc.

## Atajos de menú que «roban» teclas

Un atajo de menú tiene prioridad sobre el control enfocado. Consecuencias:

- **Ctrl+C y Ctrl+A** en el menú Editar dejarían de funcionar en los campos de texto. El manejador comprueba el foco: si es un `TextCtrl` o `ComboBox`, llama a su `Copy()` o `SelectAll()`; si no, actúa sobre la lista.
- **Supr** no va como atajo del menú (borraría en la lista al pulsarlo dentro de un campo de texto). Se atiende en `EVT_KEY_DOWN` de la lista y el menú muestra la acción sin atajo; la ayuda y la lista de teclas lo explican.
- **Alt+F4** no se pone en «Salir»: el menú se saltaría `EVT_CLOSE` y la opción de cerrar a la bandeja. Alt+F4 lo gestiona Windows y llega como `EVT_CLOSE` vetable.

## Control principal y foco

- La `StaticText` («&Elementos:») se crea **justo antes** del `ListCtrl` y con el mismo padre: el lector toma de ella el nombre de la lista. `name=` o `SetName()` no nombran controles nativos.
- `ListCtrl` en modo informe (`wx.LC_REPORT`) con cabeceras en español.
- Foco inicial en la lista con `wx.CallAfter` tras `Show()` y, si tiene elementos, uno enfocado y seleccionado: una lista con foco pero sin elemento enfocado deja al lector callado.
- Tras quitar elementos, el foco pasa al que ocupa el lugar del primero borrado.
- `SetHelpText` en cada control para Ctrl+H (necesita `wx.HelpProvider.Set(wx.SimpleHelpProvider())` al arrancar).

## Menú contextual

- `EVT_CONTEXT_MENU` en el control (no `EVT_RIGHT_DOWN`): así funciona con el ratón, con la tecla Aplicaciones y con Shift+F10.
- Desde el teclado la posición llega como `wx.DefaultPosition`: se abre junto al elemento enfocado (`GetItemRect`), no donde esté el puntero.
- Mismas acciones y mismas etiquetas que en la barra de menús, reutilizando los manejadores.

## Ayuda

- F1 abre `data/docs/manual`: si existe `manual.html` se abre en el navegador (el lector permite saltar por encabezados con la tecla H); si solo hay `.md` o `.txt`, se muestra en `DialogoTexto`.
- Ctrl+F1 la lista de teclas y «Novedades» el historial de cambios para el usuario.
- «Acerca de» con los datos técnicos en un campo de solo lectura y un botón «Copiar al portapapeles» para soporte. No `wx.adv.AboutBox`: su ventana nativa no deja copiar y mezcla etiquetas que el lector no siempre recorre.

## Bandeja del sistema

- Opcional y desactivada de serie: hay usuarios cuya bandeja oculta los iconos y perderían el acceso a la ventana.
- Tres opciones en Herramientas, Opciones: minimizar a la bandeja, cerrar a la bandeja (Alt+F4 oculta; Archivo, Salir cierra de verdad) e iniciar oculta en la bandeja.
- El icono existe **solo mientras la ventana está oculta**. Si `SetIcon` falla, la ventana no se oculta.
- Menú del icono en español con `&`: «&Mostrar la ventana» y «&Salir». Un clic o doble clic restauran.
- Al ocultar, un anuncio por voz («sigue en la bandeja del sistema») porque no hay ningún control que lo diga.
- Al restaurar: `Show`, `Iconize(False)`, `Raise`, foco al diálogo modal abierto si lo hay (la ventana principal está desactivada detrás) o a la lista, y `SetForegroundWindow`. El icono se destruye con `wx.CallAfter`, porque quizá estamos dentro de su propio evento.
- Al cerrar la aplicación: `RemoveIcon()` y `Destroy()` del icono siempre; si no, el proceso puede quedar vivo o el icono «fantasma» en la bandeja.

## Recordar tamaño y posición

- Se guarda el rectángulo **normal** (ni maximizada ni minimizada), actualizado en `EVT_SIZE` y `EVT_MOVE`, más un indicador `maximizada`. Guardar `GetRect()` de una ventana minimizada da coordenadas absurdas (-32000).
- Al restaurar, si el punto de la barra de título no cae en ningún monitor (`wx.Display.GetFromPoint`), se centra: el usuario puede haber quitado el segundo monitor.
- Tamaños por debajo del mínimo se sustituyen por el estándar.

## Cierre ordenado

En `EVT_CLOSE`:

1. Si está activado «cerrar a la bandeja», el evento es vetable y no se pidió salir de verdad: se oculta y se veta.
2. Si no: se paran los temporizadores, se pide a los hilos que terminen (`threading.Event`) y se esperan con tiempo límite, se guardan la geometría y la configuración, se quita el icono de la bandeja y se llama a `Destroy()`.
3. `main.py`, al volver de `MainLoop()`, libera la instancia única y anota «Cierre normal».

Si hay trabajo que se perdería (descargas, documento sin guardar), se pregunta antes con un diálogo de botones explícitos (`&Salir`, `&Cancelar`) y se veta si el usuario cancela.

## Hilos y temporizadores

- `ejecutar_en_segundo_plano(descripcion, trabajo, al_terminar)`: el trabajo va en un hilo con nombre (`tarea-...`, aparece en el registro) y el resultado vuelve al hilo principal con `wx.CallAfter`. Lo que el hilo necesite de los controles se lee antes, en el hilo principal.
- Si el trabajo falla, se registra con traza y se avisa en la interfaz.
- Un resultado que llega cuando la ventana ya se destruyó se descarta (`if self` es falso en una ventana wx destruida).
- Un `wx.Timer` que late toda la sesión protege su cuerpo con `try/except` y `log.exception`: una excepción cada medio segundo no debe tumbar la aplicación.
