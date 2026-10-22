# Estructura del complemento y manifest.ini

## Qué es un .nvda-addon

Un archivo ZIP con la extensión `.nvda-addon`. NVDA lo descomprime en la carpeta `addons` de su configuración. Dentro, en la raíz:

- `manifest.ini` (obligatorio).
- `appModules/`: un módulo por ejecutable (`miaplicacion.py` para `MiAplicacion.exe`).
- `globalPlugins/`: funciones activas en cualquier programa.
- `synthDrivers/`, `brailleDisplayDrivers/`, `brailleTables/`, `speechDicts/`: controladores y datos (no los usa este agente).
- `installTasks.py` (opcional): `onInstall()` y `onUninstall()`.
- `locale/<idioma>/LC_MESSAGES/nvda.mo`: traducciones compiladas.
- `locale/<idioma>/manifest.ini`: `summary`, `description` y `changelog` traducidos.
- `doc/<idioma>/readme.html`: documentación. NVDA busca el idioma de la persona, luego el idioma sin país y por último `en`.

Fuente: [NVDA Developer Guide, «Add-ons»](https://download.nvaccess.org/documentation/developerGuide.html) y la [plantilla oficial de NV Access](https://github.com/nvaccess/AddonTemplate).

## Carpeta fuente en el proyecto

La plantilla sigue la organización de la plantilla oficial, simplificada:

- `nvda_addon/manifest.ini`: manifiesto con marcadores (`@NOMBRE@`, `@VERSION@`, `@NVDA_MINIMA@`, `@NVDA_PROBADA@`, `@AUTOR@`, `@URL@`, `@CAMBIOS@`).
- `nvda_addon/addon/`: todo lo que irá a la raíz del ZIP (`appModules`, `installTasks.py`, `locale`, `doc`).
- `tools/construir_complemento.py`: genera `data/bin/<nombre>.nvda-addon`.

El `.po` y el `readme.md` son fuentes: el paquete lleva el `.mo` y el `.html`. `data/bin/` se distribuye con la aplicación (lista blanca de la compilación), así que el botón de la aplicación encuentra el paquete también en la versión compilada.

## Campos del manifiesto

Especificación que usa NVDA 2026.3 (leída de `addonHandler` en el `library.zip` de NVDA 2026.3 beta 2 instalado en el equipo de pruebas):

| Campo | ¿Obligatorio? | Formato y notas |
|---|---|---|
| `name` | Sí | Identificador único. lowerCamelCase recomendado; la tienda solo admite letras, números, `_` y `-` |
| `summary` | Sí | Nombre visible |
| `author` | Sí | «Nombre <correo>» |
| `version` | Sí | La tienda exige `mayor.menor` o `mayor.menor.parche` |
| `description` | No | Qué hace. Sin él, la tienda y el gestor no lo explican |
| `changelog` | No | Novedades de la versión. Está en la especificación de 2026.3; no se comprobó desde qué versión existe |
| `url` | No | Debe empezar por `https://` para la tienda |
| `docFileName` | No | Normalmente `readme.html` |
| `minimumNVDAVersion` | En la práctica, sí | `año.mayor[.menor]`; si falta vale 0.0.0 |
| `lastTestedNVDAVersion` | En la práctica, sí | Igual; debe ser ≥ la mínima. Si falta, el complemento es incompatible |

Además hay secciones opcionales `[brailleTables]`, `[symbolDictionaries]` y `[speechDictionaries]`.

La plantilla oficial añade `updateChannel`, pero la especificación de NVDA 2026.3 no lo define: NVDA lo ignora y el canal (estable, beta, dev) se indica al enviar a la tienda. El constructor lo acepta sin error.

### Formato de las versiones de NVDA

NVDA valida con `^(0|\d{4})\.(\d)(?:\.(\d))?$` (`addonAPIVersion.ADDON_API_VERSION_REGEX`): año de cuatro cifras y **una sola cifra** en mayor y menor. `2026.1`, `2026.2.0` y `0.0.0` son válidos; `2026.10` no. El constructor aplica la misma expresión.

### Sintaxis

NVDA lee el manifiesto con `configobj` (UTF-8). Valores:

- Sin comillas para identificadores y versiones: `version = 1.2.0`.
- Comillas dobles para textos con espacios: `summary = "MiAplicacion: mejor lectura"`.
- Comillas triples para varias líneas: `description = """..."""`.
- Una clave repetida es un error. Un `#` fuera de comillas empieza un comentario.

`construir_complemento.py` valida con un lector propio de ese subconjunto. En el equipo de pruebas se comprobó además que el manifiesto generado pasa la validación con el `configobj` 5.1.0 y la especificación reales de NVDA 2026.3 beta 2.

## Manifiesto traducido

`locale/es/manifest.ini` solo aporta `summary`, `description` y `changelog`. El manifiesto raíz va en inglés, como en NVDA: es el que ve quien usa NVDA en un idioma sin traducción. Si el complemento es solo para personas de habla hispana, puedes escribir la raíz en español y no crear el traducido.

## Traducciones del código

- En cada módulo: `import addonHandler` y `addonHandler.initTranslation()` antes de usar `_`. Instala `_` en los globales de ese módulo con el catálogo del complemento.
- Los `msgid` van en inglés (convención de NVDA y de la plataforma de traducción de la tienda); `locale/es/LC_MESSAGES/nvda.po` los traduce.
- Encima de cada cadena, un comentario `# Translators: ...` explica el contexto. La plantilla oficial extrae esos comentarios con xgettext.
- `construir_complemento.py` compila el `.po` con un compilador propio (sin gettext ni Babel): omite entradas sin traducir y las marcadas `fuzzy`, admite contexto (`msgctxt`) y plurales. Las pruebas comprueban que cada `_("...")` del código tiene traducción.

## Documentación

`doc/es/readme.md` se convierte en `doc/es/readme.html`. Si está instalado el paquete `markdown`, lo usa; si no, un conversor propio cubre encabezados, listas, párrafos, negrita, cursiva y código. La página sale con `lang="es"` y `<title>`, y conserva encabezados y listas para navegar con el lector.

## Qué nunca viaja en el paquete

`__pycache__`, `.pyc`, `.pyo`, `.po`, `.pot`, copias de editor (`.bak`, `~`), `Thumbs.db`, `desktop.ini` y carpetas de control de versiones. El constructor los excluye y vuelve a comprobar el ZIP ya generado.
