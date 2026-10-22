# Flujo de traducción paso a paso

El código se escribe en español. Cada idioma adicional es un catálogo gettext que se genera a partir del código. Este documento describe el ciclo completo y dónde queda cada archivo.

## Carpetas y archivos

| Archivo | Qué es | ¿Se versiona? | ¿Se distribuye? |
|---|---|---|---|
| `data/locales/<dominio>.pot` | Plantilla con todos los textos del código | Sí | No |
| `data/locales/<idioma>/LC_MESSAGES/<dominio>.po` | Traducción editable de un idioma | Sí | No (opcional) |
| `data/locales/<idioma>/LC_MESSAGES/<dominio>.mo` | Traducción compilada que lee la aplicación | Opcional | **Sí** |

- `<dominio>` es la constante `DOMINIO` de `app/core/i18n.py`. Las tres herramientas la leen de ahí; no la repitas en ningún otro sitio.
- `<idioma>` es un código ISO: `en`, `fr`, `pt_BR`. gettext prueba primero `pt_BR` y después `pt`.
- El español no tiene carpeta: es el idioma del código.

## Paso 1. Marcar las cadenas en el código

- Todo texto que vea la persona usuaria va dentro de `_()`: etiquetas, botones, menús, títulos, mensajes, anuncios de voz, textos de ayuda, nombres accesibles.
- Plurales con `ngettext`; palabras ambiguas con `pgettext`. Detalles en [buenas-practicas-i18n.md](buenas-practicas-i18n.md).
- Las constantes de módulo se marcan con `N_("...")` y se traducen al usarlas con `_(CONSTANTE)`: si se traducen al importar, se quedan en el idioma del arranque.
- Un comentario `# Traductores: ...` justo encima de la llamada llega a quien traduce (límite de longitud, qué es cada marcador, dónde aparece).

## Paso 2. Extraer

```
python tools/extraer_cadenas.py
```

- Genera o actualiza `data/locales/<dominio>.pot` con referencias `archivo:línea`.
- Termina con código 1 si encuentra `_()` con f-strings, variables o formato dentro: esas cadenas nunca se traducirían. Corrígelas antes de seguir.
- Si nada cambió, no reescribe el archivo (la fecha no ensucia el historial).

## Paso 3. Crear o actualizar los .po

```
python tools/actualizar_po.py --nuevo en     (solo la primera vez para cada idioma)
python tools/actualizar_po.py                (cada vez que cambie el .pot)
```

- `--nuevo` escribe la cabecera con el idioma y su regla de plurales (`Plural-Forms`).
- Al actualizar se conservan las traducciones, los mensajes nuevos entran vacíos, los que desaparecieron pasan a obsoletos (`#~`) y los muy parecidos a uno desaparecido se proponen como aproximados («fuzzy»).
- El informe dice, por idioma: traducidas, aproximadas, sin traducir y obsoletas.

## Paso 4. Traducir

- **Poedit** (https://poedit.net) es el editor de catálogos más extendido y valida marcadores y plurales. Comprueba con tu lector de pantalla qué partes se leen bien antes de encargar trabajo largo con él. Abre el `.po`, traduce, revisa las aproximadas y guarda. Desactiva en sus preferencias «compilar .mo al guardar» si prefieres compilar con la herramienta (así pasa siempre la validación).
- Cualquier editor de texto sirve: el `.po` es texto UTF-8. Rellena `msgstr` (o `msgstr[0]`, `msgstr[1]`... en plurales) y borra la marca `#, fuzzy` cuando hayas revisado una entrada.
- Una IA puede proponer traducciones: pásale el `.po` completo con los comentarios `#.` y pídele que no toque `msgid`, marcadores `{...}` ni atajos tras el tabulador. Revísalas igual que las aproximadas.

## Paso 5. Compilar

```
python tools/compilar_traducciones.py
```

- Genera cada `.mo` junto a su `.po`.
- Omite las entradas aproximadas y las vacías: se mostrarán en español.
- Una entrada cuyos marcadores no coinciden con el original se omite y se informa como **error** (código de salida 1): en ejecución haría fallar `.format()`.
- Avisa si cambia el número de `&` o el atajo de un menú.

## Paso 6. Probar

- Sin tocar la configuración: `set APP_IDIOMA=en` y arranca la aplicación desde código.
- Recorre con el lector de pantalla los menús y diálogos principales: textos cortados, aceleradores repetidos, frases a medio traducir.
- Ejecuta las pruebas: `python -m pytest tests/test_traducciones.py`.

## Compilación con cx_Freeze

- `data/locales` debe viajar a `build/<App>/data/locales` con los `.mo`. Si la compilación usa una lista blanca de carpetas de `data`, `locales` tiene que estar en ella.
- Los `.po` y el `.pot` no hacen falta en la distribución: añade `*.po` y `*.pot` a los patrones excluidos si quieres una distribución mínima.
- Los botones estándar de wx necesitan `lib/wx/locale/<idioma>/LC_MESSAGES/wxstd.mo`: no metas el paquete `wx` en el zip de bibliotecas.
- Comprueba tras compilar: `build/<App>/data/locales/en/LC_MESSAGES/<dominio>.mo` existe y la versión compilada arranca en inglés con `APP_IDIOMA=en`.

## Integrarlo en el menú de tareas del proyecto

Si el proyecto tiene un `.bat` con menú, añade una opción «Traducciones» que ejecute, en orden, extraer, actualizar y compilar, y que se detenga si alguno devuelve un código distinto de 0.
