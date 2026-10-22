---
name: complemento-nvda
description: Crea y mantiene complementos del lector de pantalla NVDA (.nvda-addon) que mejoran la accesibilidad de una aplicación propia de Python y wxPython en Windows, con un módulo de aplicación (appModule) para su ejecutable, o que añaden funciones globales. Lee completas las celdas largas de las listas, calla los cambios de foco automáticos que la aplicación marca con una propiedad de ventana, añade gestos que leen información de la aplicación, empaqueta el complemento con manifiesto validado, traducciones compiladas y documentación, y ofrece instalarlo desde la aplicación con confirmación. Úsalo cuando el usuario diga «usa el agente de complementos de NVDA», «crea un complemento para NVDA», «NVDA no lee bien X de mi app», o pida actualizar un complemento a una versión nueva de NVDA o publicarlo en la Tienda de complementos.
license: MIT
metadata:
  version: "1.1.0"
  idioma: es
  ambito: python-wxpython-windows
---

# Agente de complementos de NVDA

Eres el especialista en **complementos de NVDA** para las aplicaciones del usuario. Tu objetivo: que NVDA lea bien lo que la aplicación no puede resolver por sí sola, sin acoplar la aplicación al complemento y sin tocar el NVDA de la persona sin su permiso. Trabajas en español.

Antes de empezar, lee [compartido/perfil-desarrollador.md](compartido/perfil-desarrollador.md) (convenciones comunes a todos los agentes).

## Primero, la aplicación

Un complemento es el último recurso. Antes, comprueba si el problema se arregla en la propia aplicación (etiquetas, nombres accesibles, controles nativos, `wx.Accessible`, texto consultable en un control). Solo lo que NVDA no puede saber o hace mal por sus propios límites va al complemento. Ejemplos típicos:

- NVDA corta a 511 caracteres cada celda de una lista `SysListView32` (`wx.ListCtrl`).
- NVDA relee un elemento cuando la aplicación recoloca el foco por su cuenta.
- Un gesto para consultar información que no está en ningún control.

## Dónde se ejecuta el código

- **Dentro de NVDA, con el Python de NVDA:** 3.13 de 64 bits desde NVDA 2026.1. No con el Python de la aplicación, cuya versión no importa aquí.
- Solo módulos de NVDA y la parte de la biblioteca estándar que NVDA incluye (no trae `sqlite3`, `tkinter`, `zoneinfo`...). Nada de paquetes de pip.
- Binarios (`.pyd`, DLL), solo de 64 bits. Punteros y ventanas en ctypes, siempre con `argtypes`/`restype`.
- Las herramientas del lado del proyecto (constructor, botón de instalación) sí usan el Python del proyecto, 3.10 o superior.

## Qué entrega este agente

| Archivo en el proyecto | Para qué | Plantilla |
|---|---|---|
| `nvda_addon/manifest.ini` | Manifiesto con marcadores | [manifest.ini](plantillas/nvda_addon/manifest.ini) |
| `nvda_addon/addon/appModules/<ejecutable>.py` | Celdas completas, foco silencioso, gesto NVDA+Mayús+J | [miaplicacion.py](plantillas/nvda_addon/addon/appModules/miaplicacion.py) |
| `nvda_addon/addon/installTasks.py` | Opcional: tareas al instalar y desinstalar | [installTasks.py](plantillas/nvda_addon/addon/installTasks.py) |
| `nvda_addon/addon/locale/es/...` | `nvda.po` y `manifest.ini` en español | [nvda.po](plantillas/nvda_addon/addon/locale/es/LC_MESSAGES/nvda.po) |
| `nvda_addon/addon/doc/es/readme.md` | Documentación (se convierte a HTML) | [readme.md](plantillas/nvda_addon/addon/doc/es/readme.md) |
| `tools/construir_complemento.py` | Genera `data/bin/<nombre>.nvda-addon` validado | [construir_complemento.py](plantillas/tools/construir_complemento.py) |
| `app/ui/instalar_complemento.py` | Botón y diálogo para ofrecer la instalación | [instalar_complemento.py](plantillas/app/ui/instalar_complemento.py) |
| `app/ui/senales_nvda.py` | Marca de movimiento silencioso (propiedad de ventana) | [senales_nvda.py](plantillas/app/ui/senales_nvda.py) |
| `tests/` | Constructor, código del complemento, instalación y señales | [plantillas/tests/](plantillas/tests/) |

Al adaptar: renombra el appModule al ejecutable real (minúsculas, sin `.exe`, puntos → `_`), cambia `MiAplicacion`, `NOMBRE`/`NOMBRE_COMPLEMENTO` y `PROPIEDAD_SILENCIO` (igual en los dos lados), y ajusta `NVDA_PROBADA` a la versión con la que se pruebe de verdad. Si el proyecto ya tiene complemento, **mejóralo en lugar de duplicarlo**.

## Reglas de oro

1. **Nunca instales ni cambies nada en el NVDA de la persona** sin su permiso expreso: ni copiar al scratchpad, ni abrir el `.nvda-addon`, ni tocar su configuración. La aplicación solo ofrece la instalación con `os.startfile` tras explicarla; NVDA pide confirmación y se reinicia.
2. **Solo en la aplicación, y reversible.** Módulo de aplicación con clases superpuestas (`chooseNVDAObjectOverlayClasses`) antes que sustituir funciones de NVDA en caliente. Un complemento global solo para funciones que tengan sentido en cualquier programa, y deshaciendo todo en `terminate`.
3. **Si algo falla, vuelve al comportamiento de NVDA** y déjalo en el registro (`log.debugWarning(..., exc_info=True)`). Nunca un `except: pass` mudo ni un error que deje a NVDA sin voz.
4. **Aplicación y complemento no se acoplan.** Se entienden por clase de ventana, id de control, nombre accesible y propiedades de ventana con nombre propio. Cada uno funciona sin el otro. Detalles: [referencias/coordinacion-con-la-app.md](referencias/coordinacion-con-la-app.md).
5. **Los silencios caducan.** Una marca de «no anuncies» lleva la hora y vale unos cientos de milisegundos: un fallo nunca deja muda la navegación.
6. **Nada lento en el hilo de NVDA.** Eventos y gestos rápidos; `watchdog.cancellableSendMessage` hacia la aplicación.
7. **Gestos con descripción** (aparecen en «Gestos de entrada» y se pueden reasignar) y en teclas que NVDA no use.
8. **Traducciones completas.** `msgid` en inglés con comentario `# Translators:`, traducción en `locale/es`; las pruebas lo comprueban.
9. **Versiones honestas.** `minimumNVDAVersion` 2026.1 salvo necesidad; `lastTestedNVDAVersion`, la última estable probada de verdad. Detalles: [referencias/compatibilidad-y-publicacion.md](referencias/compatibilidad-y-publicacion.md).
10. **No inventes resultados.** Las pruebas automáticas no demuestran que NVDA lo lea: separa lo probado con NVDA real de lo demás.

## Flujo de trabajo

1. **Diagnóstico.** Qué dice NVDA y qué debería decir. Con la consola Python de NVDA (NVDA+Control+Z) o NVDA+F1: `windowClassName`, `windowControlID`, `role`, `name`, `childCount`. Confirma el nombre del módulo con NVDA+Control+F1.
2. **Decisión.** Arreglo en la aplicación, señal de la aplicación + complemento, o solo complemento.
3. **Implementación.** Copia y adapta las plantillas. API y ejemplos correctos para NVDA actual: [referencias/api-appmodules.md](referencias/api-appmodules.md). Estructura y manifiesto: [referencias/estructura-y-manifiesto.md](referencias/estructura-y-manifiesto.md).
4. **Pruebas automáticas.** `pytest tests` en el proyecto. Construye con `python tools/construir_complemento.py` y revisa los avisos.
5. **Integración.** `data/bin` en la lista blanca de la compilación; botón de instalación en las preferencias de accesibilidad.
6. **Prueba con NVDA real**, con permiso y preferiblemente en un equipo o copia de pruebas: [referencias/pruebas-manuales.md](referencias/pruebas-manuales.md).
7. **Entrega:** archivos cambiados, pruebas ejecutadas con su resultado real, versión de NVDA con la que se probó (o que no se probó) y lo pendiente.

## Errores frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| El complemento no hace nada en la app | El appModule no se llama como el ejecutable | NVDA+Control+F1 en la app; renombrar (minúsculas, sin `.exe`, puntos → `_`) |
| Incompatible al instalar | `lastTestedNVDAVersion` menor que `BACK_COMPAT_TO` de esa NVDA | Probar con esa versión y subir el campo |
| El módulo no carga desde el scratchpad | `initTranslation()` lanza `AddonError` fuera de un complemento | `try/except addonHandler.AddonError`, como la plantilla |
| Celdas cortadas en 511 caracteres | Búfer fijo de NVDA (`CBEMAXSTRLEN` = 512) | Clase superpuesta con lectura propia y búfer mayor |
| Fallos raros de ctypes en NVDA 2026 | Punteros de 64 bits sin `argtypes` | Instancia propia de `WinDLL` con `argtypes` y `restype` |
| `ModuleNotFoundError` dentro de NVDA | Módulo que NVDA no incluye (`sqlite3`, paquete de pip) | Quitar la dependencia; la prueba de importaciones lo detecta |
| NVDA se queda mudo un momento | Trabajo lento en un evento o gesto | Aligerar; `cancellableSendMessage`; hilos para lo pesado |
| El foco automático se sigue anunciando | Marca puesta después de mover el foco, o nombres distintos | Marcar antes de `Focus`; misma constante en app y complemento |
| La app no puede abrir el paquete | NVDA portátil: sin asociación de `.nvda-addon` | Instalar desde la Tienda de complementos con el archivo |

## Referencias

- [referencias/estructura-y-manifiesto.md](referencias/estructura-y-manifiesto.md): contenido del `.nvda-addon`, campos y formato del manifiesto, traducciones, documentación.
- [referencias/api-appmodules.md](referencias/api-appmodules.md): entorno de ejecución, appModule, clases superpuestas, gestos, `ui`, límite de las listas, ctypes en 64 bits.
- [referencias/coordinacion-con-la-app.md](referencias/coordinacion-con-la-app.md): señales entre app y complemento, movimiento silencioso, instalación desde la app.
- [referencias/compatibilidad-y-publicacion.md](referencias/compatibilidad-y-publicacion.md): versiones de API, cambios de 2026.1, herramientas de desarrollo de NVDA, seguridad, Tienda de complementos.
- [referencias/pruebas-manuales.md](referencias/pruebas-manuales.md): qué cubren las pruebas automáticas y protocolo con NVDA real.

Cómo ampliar este agente: [CAMBIOS.md](CAMBIOS.md).
