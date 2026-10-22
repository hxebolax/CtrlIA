# Auditoría de accesibilidad: método y ejemplo

## Método

1. **Alcance.** Qué ventanas y tareas se revisan, y si se puede cambiar código (normalmente, en una auditoría, no).
2. **Fuentes activas.** Revisa el código que se ejecuta; ignora copias de seguridad, compilaciones, dependencias y carpetas de material de referencia.
3. **Recorrido por tareas.** Para cada tarea principal (abrir, buscar, seleccionar, actuar, configurar, cerrar): cómo se llega con teclado, qué oye el usuario, cómo vuelve.
4. **Revisión estática**, buscando en el código:
   - controles creados antes que su `StaticText` o sin etiqueta;
   - `CheckListBox`, `CheckListCtrlMixin`, `Grid`, `RichTextCtrl`, `HtmlWindow`, `wx.lib.agw`, `GenButton`;
   - `EVT_CHAR_HOOK` sin `Skip()`; menú contextual solo con `EVT_RIGHT_UP`;
   - diálogos sin botón predeterminado, sin escape, con botones en inglés o sin foco inicial;
   - textos largos en `StaticText`;
   - voz que duplica al lector (`speak` en `EVT_LIST_ITEM_SELECTED` o `EVT_SET_FOCUS`);
   - controles tocados desde hilos sin `wx.CallAfter`;
   - colores fijos y tamaños en píxeles;
   - preferencias que no se aplican a ventanas abiertas o que Cancelar no respeta.
5. **Pruebas automáticas** existentes y, si se puede, pruebas MSAA de las ventanas revisadas.
6. **Pruebas con lector** si están disponibles; si no, quedan como pendientes.

## Formato de cada hallazgo

- **Identificador y título** en una frase.
- **Prioridad:** bloqueo (impide la tarea), grave (pérdida de información o de foco), media (verbosidad, incomodidad), mejora.
- **Tarea afectada.**
- **Evidencia:** `archivo:línea` y símbolo; indica si es un fallo demostrado por el código, una hipótesis o una observación real.
- **Reproducción:** pasos con teclado.
- **Resultado esperado.**
- **Propuesta concreta** (y qué plantilla o función del agente la resuelve).
- **Validación:** qué prueba automática y qué comprobación manual confirmarán la corrección.

No inventes hallazgos para llenar una plantilla, ni cambies preferencias del usuario para que una prueba pase.

## Ejemplo de hallazgos

### A01. El campo «Carpeta de destino» no tiene nombre accesible

- **Prioridad:** grave.
- **Tarea:** configurar una descarga.
- **Evidencia (código):** `app/ui/dialogo_descarga.py:48` crea `self.carpeta = wx.TextCtrl(panel)` y en la línea 50 su `wx.StaticText`. La etiqueta se crea después del campo, así que el lector no la asocia.
- **Reproducción:** abrir el diálogo, tabular hasta el campo; NVDA dice solo «editar».
- **Esperado:** «Carpeta de destino, editar».
- **Propuesta:** crear la etiqueta antes (`crear_campo(panel, _("&Carpeta de destino:"), wx.TextCtrl, sizer=sizer)`). `MoveBeforeInTabOrder` no lo arregla.
- **Validación:** prueba MSAA del diálogo sin controles anónimos; comprobación con NVDA.

### A02. La lectura por columnas queda muda si se desactivan los avisos

- **Prioridad:** grave (hipótesis del código, sin observar con lector).
- **Evidencia:** `app/core/voz.py:40` sale sin hablar si `avisos_voz` es falso, y la lista usa esa misma función para leer columnas.
- **Esperado:** desactivar los avisos no afecta a la lectura que pide el usuario.
- **Propuesta:** canales separados (`voz.hablar(..., canal=voz.NAVEGACION)`) y una ayuda en Preferencias que explique qué controla cada ajuste.
- **Validación:** prueba con la voz de avisos desactivada; comprobación manual de la lectura de columnas.

### A03. El respaldo «todo desactivado» puede producir un anuncio vacío

- **Prioridad:** media (caso límite).
- **Evidencia:** la función que compone el anuncio fuerza el contenido si las tres preferencias globales están desactivadas, pero después aplica las restricciones de la lista, que pueden volver a quitarlo; una celda vacía tampoco aporta texto.
- **Esperado:** nunca una cadena vacía: contenido o «Sin datos».
- **Propuesta:** decidir el respaldo después de combinar preferencias (`componer_anuncio_celda`).
- **Validación:** matriz de 8 combinaciones más restricciones locales (`test_composicion.py`).

## Cierre de la auditoría

- Resumen por prioridad.
- Qué se revisó y qué no.
- Qué se ejecutó (pruebas) y qué queda pendiente con tecnología de apoyo real.
- Fecha y versión revisada.
