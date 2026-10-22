# Compatibilidad, herramientas de desarrollo y publicación

## Versiones de la API de complementos

- Cada versión de NVDA tiene una versión de API (`addonAPIVersion.CURRENT`, igual a `año.mayor.menor` de NVDA) y una versión hacia atrás (`BACK_COMPAT_TO`). NVDA carga un complemento si `minimumNVDAVersion ≤ versión de NVDA` y `lastTestedNVDAVersion ≥ BACK_COMPAT_TO`.
- Las versiones `año.1` suelen romper la compatibilidad («API breaking release»): suben `BACK_COMPAT_TO` a `año.1.0`. Las `año.2`, `año.3` no la rompen.
- Situación a 25 de septiembre de 2026, según [`nvdaAPIVersions.json`](https://github.com/nvaccess/addon-datastore/blob/master/transform/nvdaAPIVersions.json) de la tienda:
  - NVDA 2026.1, 2026.1.1 y 2026.2: `BACK_COMPAT_TO` = 2026.1.0.
  - NVDA 2026.3: experimental (en beta), `BACK_COMPAT_TO` = 2026.1.0.
  - NVDA 2027.1: experimental, `BACK_COMPAT_TO` = 2027.1.0. Cuando salga, todo complemento con `lastTestedNVDAVersion` menor que 2027.1 quedará incompatible hasta que se pruebe y se publique con el manifiesto actualizado.
- En el equipo de pruebas, NVDA 2026.3 beta 2 declara `BACK_COMPAT_TO = (2026, 1, 0)`.

Consecuencias prácticas:

- **`minimumNVDAVersion = 2026.1`** para complementos nuevos: es la primera de 64 bits y Python 3.13, y evita mantener código para 32 bits. Bájala solo si hace falta y prueba en ambas.
- **`lastTestedNVDAVersion`** = la última versión estable con la que se ha probado DE VERDAD. No pongas una futura para «adelantarte»: la tienda lo rechaza si no es una versión conocida y, si es experimental, obliga al canal beta o dev.
- Una persona puede forzar un complemento incompatible desde la Tienda de complementos bajo su responsabilidad; no cuentes con ello.

### Qué cambió en NVDA 2026.1 (lo que afecta a este tipo de complementos)

- Python 3.13 de 64 bits; Windows de 32 bits ya no está soportado.
- Envolturas de la API de Windows en `winBindings.*`; parte de `winUser`, `winKernel` y `winGDI`, obsoleta.
- `versionInfo.copyrightYears` y `url` pasan a `buildVersion`; se retiró `typing_extensions`.
- `ListViewWindowStyle` sustituye a las constantes `LVS_*` de `sysListView32`.

Fuente: [novedades de NVDA 2026.1](https://download.nvaccess.org/releases/2026.1beta4/documentation/changes.html), sección para desarrolladores. Suscríbete a [nvda-api](https://groups.google.com/a/nvaccess.org/g/nvda-api) para enterarte antes de cada versión que rompe la compatibilidad.

### Cómo revisar un complemento para una versión nueva

1. Lee la sección «Changes for Developers» de las novedades de la versión.
2. Busca en el código del complemento cada símbolo retirado u obsoleto.
3. Instala la beta de NVDA en un equipo o máquina de pruebas (o como copia portátil) y haz la prueba manual completa ([pruebas-manuales.md](pruebas-manuales.md)).
4. Revisa el registro de NVDA en nivel de depuración: avisos de obsolescencia y excepciones del complemento.
5. Sube `lastTestedNVDAVersion` y la versión del complemento; publica en canal beta si NVDA aún está en beta.

## Herramientas de desarrollo de NVDA

Verificadas en NVDA 2026.3 beta 2:

- **Carpeta de pruebas del desarrollador (scratchpad).** Opciones de NVDA, panel Avanzado: «Habilitar la carga de código personalizado desde el directorio Developer Scratchpad» y botón para abrir la carpeta. Dentro, `appModules/` y `globalPlugins/`: copia ahí el módulo para probarlo sin empaquetar. Desactívalo al terminar.
- **Recargar complementos:** NVDA+Control+F3 recarga módulos de aplicación y complementos globales sin reiniciar.
- **Consola Python de NVDA:** NVDA+Control+Z. Trae ya importados `focus`, `nav`, `fg`, `api`, `speech`... Ejemplos: `focus.windowClassName`, `focus.windowControlID`, `focus.appModule`, `focus.childCount`, `len(focus._getColumnContent(1))`.
- **Información del objeto:** NVDA+F1 registra los datos del objeto de navegación y abre el visor del registro. NVDA+Control+F1 dice el ejecutable y el módulo de aplicación cargado: sirve para confirmar el nombre del archivo del appModule.
- **Visor del registro:** menú de NVDA, Herramientas, Ver registro. El nivel se cambia en Opciones, panel «Privacidad y seguridad», «Nivel de registro» (en versiones anteriores estaba en General). Para ver los `log.debug` del complemento, nivel «Depuración».
- **Visor de voz:** menú de NVDA, Herramientas, Visor de voz. Muestra por escrito todo lo que NVDA dice: la forma más rápida de comprobar si un anuncio se repite o se calla.

## Seguridad

- Un complemento corre con los mismos privilegios que NVDA (que tiene UIAccess y ve todas las ventanas): un error puede dejar sin voz a la persona y un código malicioso puede leer todo lo que hay en pantalla.
- No leas ni envíes datos de la aplicación a ningún sitio, no ejecutes código recibido de la app (`eval`, `pickle`), no abras conexiones de red si no es imprescindible.
- Los complementos de NVDA **no se firman** con certificado. La integridad en la tienda la da el SHA-256 de cada versión y el análisis con VirusTotal de cada envío ([addon-datastore](https://github.com/nvaccess/addon-datastore)). Fuera de la tienda, NVDA avisa de que solo se instalen complementos de fuentes de confianza. No se ha verificado si NV Access planea firmas en el futuro.
- Si la aplicación distribuye el `.nvda-addon` en su propio paquete, la integridad la da el SHA-256 del ZIP de la aplicación (y su firma, si la tiene).

## Publicar en la Tienda de complementos (opcional)

Solo si el complemento es útil para otras personas. Para uno ligado a una aplicación propia basta con distribuirlo con ella.

Requisitos ([guía de envío](https://github.com/nvaccess/addon-datastore/blob/master/docs/submitters/submissionGuide.md)):

1. Publicar el `.nvda-addon` en una URL `https://` de descarga directa que termine en `.nvda-addon` (por ejemplo, una versión de GitHub).
2. Rellenar el formulario de GitHub «Add-on registration» de nvaccess/addon-datastore: crea una incidencia y una solicitud de cambios.
3. La primera vez, NV Access aprueba a quien publica (hasta 2 semanas). Las actualizaciones posteriores no necesitan aprobación.
4. Validación automática: el manifiesto se carga con `AddonManifest`; `name` único y solo con letras, números, `_` y `-`; `version` `mayor.menor[.parche]`; versiones de NVDA válidas según `nvdaAPIVersions.json`; todas las URL con https; si `lastTestedNVDAVersion` es una beta, canal beta o dev.
5. VirusTotal: si marca algo, queda pendiente de revisión manual.
6. Datos que pide el formulario además del manifiesto: editor, URL del código fuente, licencia y su URL, canal.
7. Traducciones: la plantilla oficial se integra con Crowdin para que la comunidad traduzca la interfaz y la documentación.

El constructor de este agente ya aplica las reglas del manifiesto; lo que no comprueba es que el nombre sea único en la tienda.
