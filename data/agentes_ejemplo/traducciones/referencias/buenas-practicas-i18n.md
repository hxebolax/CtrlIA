# Buenas prácticas de internacionalización

Reglas para que el código se pueda traducir bien y para que la traducción no rompa la aplicación ni la accesibilidad.

## Frases completas, nunca trozos

- Mal: `_("Se borró ") + nombre + _(" de la lista")`. Cada idioma ordena la frase a su manera y quien traduce recibe fragmentos sin sentido.
- Bien: `_("Se borró {nombre} de la lista").format(nombre=nombre)`.
- Tampoco se construyen frases eligiendo palabras sueltas (`_("el")` / `_("la")` según el género): escribe una frase completa por caso.

## Marcadores con nombre y `.format` fuera de `_()`

- Usa `{nombre}`, no `{}` ni `%s` cuando haya más de un dato: la traducción puede necesitar otro orden. El extractor avisa de los marcadores sin nombre.
- El formato se aplica **después** de traducir: `_("Hola, {nombre}").format(nombre=n)`. Con `_(f"Hola, {n}")` el texto que se busca en el catálogo ya lleva el nombre dentro y nunca se encuentra; el extractor lo marca como error.
- No traduzcas el nombre del marcador. El compilador rechaza una traducción con marcadores distintos del original.
- Con `%` literal en un texto que se formatea con `%`, escribe `%%`.

## Plurales con ngettext

- `ngettext("Se descargó {n} archivo", "Se descargaron {n} archivos", n).format(n=n)`.
- Nunca `if n == 1:` con dos `_()`: el ruso tiene tres formas, el árabe seis y el japonés una. La regla de cada idioma está en la cabecera `Plural-Forms` del `.po`.
- Pasa siempre el número real como tercer argumento, aunque el texto singular no lo muestre («Un archivo»).

## Contexto para palabras ambiguas

- «Abrir» (verbo, un botón) y «Abierto» (estado); «Nuevo» (menú) y «Nuevo» (etiqueta de un mensaje sin leer). En inglés pueden coincidir y en otros idiomas no.
- `pgettext("estado de la descarga", "Pausada")` crea una entrada distinta de `_("Pausada")`.
- Con plurales: `npgettext("contexto", "singular", "plural", n)`.

## Qué NO se traduce

- Claves internas: nombres de opciones en `settings.json`, identificadores de acciones, valores guardados en la base de datos, nombres de columnas SQL. Guarda la clave y traduce solo al mostrar.
- Mensajes del registro (logging): van en español y sin `_()`; así cualquier informe de fallo se puede leer, sea cual sea el idioma de quien lo envía.
- Nombres de archivos y carpetas de `data/`, extensiones, URLs, nombres de marcas, nombres de teclas en código (`wx.WXK_F1`).
- Comparaciones con texto traducido: `if boton.GetLabel() == _("Aceptar")` se rompe al cambiar de idioma. Compara identificadores.

## Aceleradores y atajos

- El `&` forma parte del texto: `_("&Guardar")`. La traducción elige su propia letra (`"&Save"`), pero no debe repetir la de otro control del mismo diálogo o menú. El compilador avisa si falta o sobra un `&`; los duplicados se comprueban recorriendo el diálogo con el lector en ese idioma.
- En menús, el atajo tras el tabulador se deja igual: `_("&Guardar\tCtrl+S")` → `"&Save\tCtrl+S"`. wx entiende los nombres de teclas en inglés; si la traducción cambia el atajo, el compilador avisa.
- Para `&` literal, `&&`.

## Longitud de las etiquetas

- Un texto traducido puede ser un 30 % más largo (alemán, francés). Usa sizers, no posiciones ni tamaños fijos, y deja que los botones crezcan.
- Si un texto tiene límite (una columna estrecha, una barra de estado), dilo en un comentario `# Traductores: máximo 20 caracteres`.

## Textos para lectores de pantalla

- Nombres accesibles, descripciones, textos de ayuda (Ctrl+H) y anuncios de voz también van con `_()`: una etiqueta traducida con un nombre accesible en español es peor que no traducir.
- Las etiquetas `StaticText` previas a cada control son las que el lector anuncia: tradúcelas junto con el control.
- Evita abreviaturas en los textos hablados: el sintetizador del otro idioma las deletrea.
- Los números, fechas y tamaños se formatean según el idioma activo, no con cadenas fijas.

## Constantes y textos calculados al importar

- `TITULO = _("Descargas")` en el cuerpo de un módulo se evalúa una vez, al importar. Si se importa antes de `i18n.inicializar`, queda en español para siempre.
- Solución: `TITULO = N_("Descargas")` y `_(TITULO)` al mostrarlo.
- Lo mismo para listas de opciones de un `Choice`, cabeceras de columnas y diccionarios de mensajes.

## No redefinas `_`

- `_` la instala gettext en `builtins`. Si una función hace `for _ in range(3)` o `valor, _ = tupla`, `_` pasa a ser variable local en TODA la función y cualquier `_("texto")` en ella lanza `UnboundLocalError`. Usa `_i` o `_resto` para descartar valores.
- Configura ruff con `builtins = ["_", "ngettext", "n_", "pgettext", "npgettext", "N_"]` para que no marque estos nombres como indefinidos.

## Lista de revisión

1. Ningún texto visible sin `_()` (busca comillas en `label=`, `SetLabel`, `SetStatusText`, `MessageBox`, `SetHelpText`, `SetName`, anuncios de voz).
2. `extraer_cadenas.py` termina sin errores.
3. Sin concatenaciones de textos traducidos.
4. Plurales con `ngettext`.
5. Palabras ambiguas con contexto.
6. Constantes de módulo con `N_`.
7. `compilar_traducciones.py` sin errores ni avisos de aceleradores.
8. Recorrido con lector de pantalla en cada idioma distribuido.
