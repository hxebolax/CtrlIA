# Ciclo de la documentación de usuario

La documentación se actualiza **en el mismo cambio** que el código, no al final. Así nunca se publica una versión con un manual que describe botones que ya no existen.

## Qué se actualiza en cada cambio

| Si el cambio... | Actualice |
|---|---|
| Añade o cambia una función visible | El apartado del manual, con pasos y nombres exactos |
| Añade, quita o cambia un atajo | `docs/teclas.md` y ejecute `tools/comprobar_teclas.py` |
| Cambia el nombre de un menú, botón o campo | Todas sus menciones en el manual y en las teclas |
| Cambia una preferencia o su valor por defecto | El apartado «Preferencias» |
| Corrige un fallo que la persona usuaria notaba | Una línea en «Correcciones» del registro de cambios |
| Cambia dónde se guardan datos o registros | El apartado «Dónde están los datos y los registros» |
| Solo reorganiza el código, sin efecto visible | Nada de la documentación de usuario |

## El registro de cambios

- Se escribe **mientras se trabaja**: en la entrada de la versión en curso, arriba del todo en `docs/changelog.md`.
- Formato de cada versión: `## Versión 1.2.0 - AAAA-MM-DD`, y dentro `### Novedades`, `### Mejoras` y `### Correcciones` (solo las que tengan contenido).
- **Lenguaje de quien usa la aplicación**: qué puede hacer ahora y dónde, no cómo se programó. Mal: «Refactorizado el gestor de descargas con asyncio». Bien: «Las descargas grandes ya no congelan la ventana».
- Una línea por cambio, empezando por lo que la persona nota. Si hay un atajo nuevo, dígalo.
- Nada de nombres de archivos del código, clases, números de incidencia internos ni jerga técnica.
- La fecha se pone al publicar. La versión coincide con `APP_VERSION` de `app/core/version.py`.

## Antes de publicar una versión

1. `python tools/comprobar_teclas.py`: sin atajos sin documentar, obsoletos ni duplicados.
2. `python tools/notas_version.py`: la versión actual tiene entrada y no está vacía. Con `--salida` guarda las notas para la página de descargas o el anuncio.
3. Revise que el manual menciona la versión nueva si lo hace en su introducción.
4. Lea con el lector de pantalla los apartados cambiados.
5. Compile y compruebe que `build/<App>/data/docs` contiene el manual, las teclas y el registro de cambios.

## Qué se distribuye y qué no

La documentación de usuario viaja con la aplicación; la de desarrollo, no.

| Archivo | Dónde vive en el proyecto | ¿Viaja en la compilación? |
|---|---|---|
| Manual | `docs/manual.md` | Sí, a `data/docs/manual.md` |
| Lista de teclas | `docs/teclas.md` | Sí, a `data/docs/teclas.md` |
| Registro de cambios | `docs/changelog.md` | Sí, a `data/docs/changelog.md` |
| Imágenes del manual, si las hay | `docs/imagenes/` | Sí, con texto alternativo en cada una |
| README del repositorio | `README.md` | No |
| Guía de desarrollo, arquitectura, notas | `docs/desarrollo/` o `AGENTS.md` | No |
| Planes, tareas, informes de pruebas | `docs/desarrollo/` | No |

- En la compilación, copie los tres documentos de `docs/` a `data/docs/` (en cx_Freeze, con `include_files`: `("docs/manual.md", "data/docs/manual.md")`, y lo mismo con los otros dos). Si la compilación usa una lista blanca de carpetas de `data`, `docs` debe estar en ella.
- La documentación de desarrollo va en una subcarpeta aparte (`docs/desarrollo/`) para que no se copie por error.
- El visor busca primero en `data/docs` (versión compilada) y después en `docs/` (al ejecutar desde código), así que no hace falta duplicar los archivos en el proyecto.

## Traducciones de la documentación

Si la aplicación está traducida, cada idioma tiene su manual: `docs/manual.en.md`, `docs/teclas.en.md`... La plantilla del visor abre siempre `manual.md`; en un proyecto traducido, adapte `abrir_manual` para probar antes `manual.<idioma>.md` con el idioma activo. El registro de cambios suele mantenerse solo en el idioma principal.

## Mensajes de la aplicación y documentación

Los textos de la interfaz también son documentación: un mensaje de error bueno dice qué pasó y qué hacer. Cuando cambie un mensaje importante (un error frecuente), actualice «Solución de problemas» del manual con el texto exacto que se anuncia.
