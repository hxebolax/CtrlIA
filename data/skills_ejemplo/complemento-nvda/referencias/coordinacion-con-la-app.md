# Coordinación entre la aplicación y su complemento

La aplicación y el complemento viven en procesos distintos (la app con su Python; el complemento dentro de NVDA con el suyo) y **no se importan el uno al otro**. Se entienden por señales que Windows expone a cualquier proceso. Regla: cada lado funciona solo. Sin complemento, la app es accesible; sin las señales de la app, el complemento se comporta como NVDA.

## Qué puede ver NVDA de la aplicación

| Señal | Cómo la pone la app (wxPython) | Cómo la lee el complemento | Uso |
|---|---|---|---|
| Nombre del ejecutable | Nombre del `.exe` (cx_Freeze: `target_name`) | Nombre del archivo del appModule | Activar el complemento solo en esa app |
| Clase de ventana | Tipo de control (`wx.ListCtrl` → `SysListView32`, `wx.TextCtrl` → `Edit`, `wx.Button` → `Button`) | `obj.windowClassName` | Elegir clases superpuestas |
| Identificador de control | `id=` al crear el control (`wx.ListCtrl(padre, id=5001, ...)`) | `obj.windowControlID` | Distinguir una lista concreta |
| Nombre accesible | Etiqueta creada antes del control, `wx.Accessible` | `obj.name` | Distinguir controles con la misma clase |
| Propiedad de ventana | `SetPropW(hwnd, "Nombre", valor)` | `GetPropW(obj.windowHandle, "Nombre")` | Estados momentáneos (movimiento silencioso) |
| Texto de la barra de estado | `wx.StatusBar` | `api.getStatusBar()` o NVDA+Fin | Información que la persona consulta |

Comprobado en el equipo de pruebas con wxPython 4.2.5: el `id` pasado a `wx.ListCtrl` es el que devuelve `GetDlgCtrlID` (5001 = 5001), la clase de ventana es `SysListView32`, y una propiedad puesta por un proceso se lee con `GetPropW` desde otro.

Prefiere, por este orden: clase de ventana + id de control (estables, no dependen del idioma), nombre accesible (cambia con la traducción) y, para estados momentáneos, propiedades de ventana. **No** identifiques controles por su posición ni por el texto visible.

## Propiedades de ventana

- Guardan un valor del tamaño de un puntero (`HANDLE`), no texto. Sirven para indicadores y marcas de tiempo, no para pasar contenido.
- Nombre con prefijo de la aplicación (`MiAplicacion.MovimientoSilencioso`) para no chocar con otras. Las dos partes lo definen en una constante con el mismo valor; las pruebas lo comprueban.
- Windows pide quitar con `RemovePropW` las propiedades antes de destruir la ventana (cada nombre de texto ocupa un átomo global). Llama a `quitar_marca` en `EVT_WINDOW_DESTROY`.
- Declara `argtypes`/`restype` en una instancia propia de `ctypes.WinDLL("user32")` en ambos lados.

## Movimiento silencioso (patrón completo)

Problema: en una lista virtual, al entrar filas nuevas arriba, la app recoloca el foco para que la persona siga sobre su elemento. Ese movimiento dispara un evento de foco y NVDA relee el elemento: ruido en cada actualización.

Solución:

1. La app, justo antes de mover el foco, pone la propiedad con `GetTickCount()` (`senales_nvda.marcar_movimiento_silencioso(lista.GetHandle())`) y después hace `Focus`/`Select`.
2. El complemento, en su clase superpuesta del elemento de lista, sobrescribe `shouldAllowIAccessibleFocusEvent`: si la marca tiene menos de `VENTANA_SILENCIO_MS` (500 ms), devuelve `False` y NVDA no trata ese evento como un foco que anunciar.
3. La marca **caduca sola**. Si la app se cuelga o no la quita, en medio segundo NVDA vuelve a anunciar con normalidad: el fallo nunca deja muda la navegación. (Una marca de «presente/ausente» que la app debe quitar a tiempo sí puede quedarse puesta.)
4. La app no vuelve a locutar la fila por su cuenta: el lector ya sabe dónde está la persona.

Límites: si NVDA tarda más de 500 ms en procesar el evento (sistema muy cargado), se oirá el anuncio; es el fallo seguro. Si la persona pulsa una flecha justo dentro de esa ventana, ese movimiento también se calla; por eso la ventana es corta.

## Texto completo que la aplicación ya tiene

Si la app puede dar más información de la que cabe en la lista (texto completo, adjuntos, estado), hay tres vías, de más a menos recomendable:

1. **Que la propia lista lo exponga completo** (la app devuelve el texto entero en `OnGetItemText`) y el complemento lo lea con búfer grande, como hace la plantilla.
2. **Un control de la app** que la persona pueda consultar sin complemento (campo de solo lectura, diálogo de detalles, `Ctrl+H`). Es la vía accesible con cualquier lector.
3. **Voz de la propia app** (accessible_output2, NVDA Controller). Solo como complemento; no sustituye a las anteriores.

No uses el portapapeles, archivos temporales ni sockets como canal entre app y complemento: dependen del estado de la sesión, pueden filtrar datos y añaden fallos difíciles de ver.

## Versión y distribución conjunta

- La versión del complemento es la de la aplicación (`APP_VERSION`): se publican juntos y el botón de la app ofrece exactamente el que corresponde.
- El `.nvda-addon` se genera en `data/bin/` y viaja con la compilación. Desde código fuente, el botón puede generarlo al vuelo (`construir=`).
- La app puede leer (nunca escribir) `%APPDATA%\nvda\addons\<nombre>\manifest.ini` para decir qué versión hay instalada. Es orientativo: NVDA portátil guarda su configuración en otra carpeta.

## Instalar desde la aplicación

- Un botón en las preferencias de accesibilidad: «&Instalar el complemento de NVDA...».
- Antes de abrir nada, un diálogo explica qué hace, que NVDA pedirá confirmación y que se reiniciará; botones «&Abrir el instalador» y «&Cancelar» (este, predeterminado).
- `os.startfile(ruta)` abre el paquete con NVDA, que muestra su diálogo de instalación. Si falla (NVDA portátil, sin asociación), se explica cómo instalarlo desde la Tienda de complementos con el archivo.
- Nunca se copian archivos a la configuración de NVDA ni se instala en silencio, y nunca se ofrece automáticamente al arrancar sin que la persona lo pida.
