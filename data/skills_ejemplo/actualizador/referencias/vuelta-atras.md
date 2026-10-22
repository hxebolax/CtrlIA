# Vuelta atrás

Una actualización puede fallar después de copiar los archivos: falta una DLL, un módulo no se incluyó en la compilación, la versión nueva se cuelga al abrir. La plantilla detecta esos casos y restaura la versión anterior sin intervención del usuario y sin tocar sus datos.

## El marcador de salud

- El script lanza la versión nueva con `--tras-actualizar` y espera el archivo `data/temp/actualizacion/salud.txt` con el texto `ok <versión nueva>`.
- La aplicación lo escribe con `confirmar_arranque`, que llama `dialogo_actualizacion.al_arrancar` **unos segundos después de mostrar la ventana principal** (`RETRASO_CONFIRMACION`, 5 por defecto).
- Por qué esperar: si el marcador se escribiera en la primera línea de `main.py`, una versión que falla al crear la ventana también lo escribiría.
- Se escribe de forma atómica (temporal y renombrado) para que el script nunca lea un archivo a medias.
- El script comprueba que el texto contiene la versión nueva: un marcador viejo o de otra versión no cuenta.

## Cuándo se vuelve atrás

| Motivo en `resultado.txt` | Qué pasó | Cuánto tarda |
|---|---|---|
| `se_cerro_sin_confirmar` | La versión nueva terminó (fallo al arrancar) sin escribir el marcador | Unos segundos |
| `sin_marcador_de_salud` | Siguió abierta pero no confirmó en `ESPERA_SALUD` segundos (60 por defecto): colgada | `ESPERA_SALUD` |
| `marcador_incorrecto` | El marcador no corresponde a la versión nueva | Inmediato |
| `no_se_pudo_lanzar` | PowerShell no pudo lanzarla o no devolvió su PID | Inmediato |
| `fallo_copia` | robocopy no pudo copiar la versión nueva (archivo bloqueado, disco lleno) | Inmediato |

En los casos de proceso colgado o marcador incorrecto, el script termina **ese proceso y sus hijos** (`taskkill /F /T /PID`) antes de restaurar: con él vivo, sus archivos seguirían bloqueados.

## Cómo se restaura

1. Comprueba que la copia de seguridad tiene el ejecutable. Si no, no borra nada (estado `rota`).
2. Borra los archivos y carpetas del **primer nivel** de la aplicación **salvo `data`**. Así desaparecen también los archivos que solo existían en la versión nueva y no quedan instalaciones mezcladas.
3. Copia `data/temp/version_anterior/programa` sobre la aplicación y `recursos/` sobre sus subcarpetas de `data` (sin borrar nada en `data`).
4. Escribe `resultado.txt` con `estado=revertida` y relanza la versión anterior (sin `--tras-actualizar`).
5. **Conserva la copia de seguridad** para poder diagnosticar. Se sustituye en la siguiente actualización.

Tras una actualización correcta la copia de seguridad se borra: no tiene sentido ocupar el tamaño de toda la aplicación.

## Qué ve el usuario

- La versión anterior se abre sola. Al arrancar, `al_arrancar` lee `resultado.txt` y muestra: «La versión X no arrancó correctamente y se ha vuelto a la versión Y. Sus datos no se han tocado.»
- Esa versión queda **omitida**: la comprobación periódica no la vuelve a ofrecer (evita un bucle de actualizar y volver atrás). La comprobación manual sí la ofrece, por si el usuario quiere intentarlo otra vez.
- Si la aplicación no llegó a cerrarse, el mensaje dice que la actualización no se aplicó y que puede intentarlo de nuevo desde el menú Ayuda.

## Qué no cubre

- **Fallos que aparecen más tarde** (un menú concreto que falla a los diez minutos): el marcador ya se escribió. La solución es publicar una versión mayor con el arreglo.
- **Cierre rapidísimo del usuario:** si cierra la versión nueva antes de `RETRASO_CONFIRMACION` segundos, el script lo interpreta como fallo y vuelve atrás. Es seguro (no se pierde nada) aunque innecesario; mantenga el retraso corto.
- **Migraciones de datos:** si la versión nueva cambia el formato de la base de datos o de la configuración y después se vuelve atrás, la versión anterior encontrará el formato nuevo. Haga las migraciones **después** de confirmar el arranque, con copia previa en `data/backups`, y que sean compatibles hacia atrás siempre que se pueda.
- **Recursos nuevos en `data`:** los archivos que la versión nueva añadió a `bin`, `sounds`... se quedan (en `data` nunca se borra nada). No molestan a la versión anterior.

## Recuperación manual (estado `rota`)

Solo ocurre si también falla la restauración (disco lleno, antivirus bloqueando archivos). El registro `data/logs/actualizacion.log` lo explica. Para recuperar a mano:

1. Cierre la aplicación si está abierta.
2. Copie el contenido de `data/temp/version_anterior/programa` sobre la carpeta de la aplicación, sustituyendo lo que haya.
3. Si existe `data/temp/version_anterior/recursos`, copie cada subcarpeta sobre la de `data` del mismo nombre.
4. No toque el resto de `data`: ahí están la configuración y los datos del usuario.

## Cómo se ha probado

`tests/test_sustitucion.py` ejecuta el `.bat` real sobre una instalación falsa en una carpeta con espacios, tilde, eñe y apóstrofo, y comprueba byte a byte que los datos del usuario no cambian en ninguno de estos casos: actualización correcta, sin recursos actualizables, versión nueva colgada, versión nueva que falla al arrancar y aplicación que no se cierra. En los dos casos de vuelta atrás verifica además que el proceso colgado queda terminado, que se relanza la versión anterior y que el usuario recibe el aviso.
