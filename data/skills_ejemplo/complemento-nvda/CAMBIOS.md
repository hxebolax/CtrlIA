# Cambios del agente complemento-nvda

## Cómo ampliarlo

- **Nueva mejora del complemento** (otro tipo de control, otro gesto): en el appModule de `plantillas/nvda_addon/addon/appModules/`, con sus cadenas en `locale/es/LC_MESSAGES/nvda.po`, pruebas con la NVDA simulada en `plantillas/tests/` (amplía `nvda_falso.py` solo con lo que el código use) y su explicación en `referencias/api-appmodules.md`.
- **Nueva señal entre app y complemento:** en `plantillas/app/ui/senales_nvda.py` y en el appModule, con el mismo nombre en ambos, una prueba que lo compruebe y su fila en `referencias/coordinacion-con-la-app.md`.
- **Nueva versión de NVDA:** revisa sus «Changes for Developers», actualiza `referencias/compatibilidad-y-publicacion.md` (tabla de API, cambios), `NVDA_PROBADA` en el constructor, la lista `AUSENTES_EN_NVDA` y los gestos ocupados si han cambiado. Indica cómo lo comprobaste.
- **Nueva regla:** en «Reglas de oro» de `SKILL.md` si es corta y general; si no, en `referencias/`.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- El ofrecimiento de instalación ya no usa `wx.MessageBox` para los errores (botón en inglés): `_mostrar_error` usa los diálogos en español del proyecto si existen o un `wx.MessageDialog` con «Aceptar».
- Las pruebas con wx ya no crean ni destruyen su propia `wx.App`: reutilizan la de la sesión (`wx.GetApp() or wx.App(False)`, guardada en una variable de módulo para que Python no la destruya) y nunca la destruyen. Crearla y destruirla por módulo dejaba sin aplicación a los módulos siguientes de la batería del proyecto. Dos pruebas nuevas de los errores en español. Probado: 76 pruebas y 1 omitida.

## 1.0.0 - 2026-09-25

- Primera versión: plantilla de complemento (manifiesto con marcadores, appModule con celdas completas, movimiento silencioso con marca de tiempo que caduca y gesto NVDA+Mayús+J, `installTasks.py`, traducción y documentación en español), constructor sin dependencias (validación del manifiesto, compilador `.po` → `.mo`, readme a HTML, exclusión de cachés, escritura atómica en `data/bin`), módulo de la app para ofrecer la instalación y módulo de señales.
- Investigado en fuentes oficiales: guía de desarrollo de NVDA, plantilla de NV Access, guía de envío y lista de versiones de API de la tienda, novedades de NVDA 2026.1 y guía de 64 bits.
- Comprobado en el equipo de pruebas, con NVDA 2026.3 beta 2 instalado (solo lectura de su `library.zip`, sin ejecutar NVDA ni tocar su configuración): Python 3.13 de 64 bits, `BACK_COMPAT_TO` = 2026.1.0, especificación del manifiesto, `CBEMAXSTRLEN` = 512 y el código de lectura de celdas, módulos de la biblioteca estándar ausentes, gestos ocupados y nombres de la API usada.
- Comprobado con wxPython 4.2.5 y Python 3.13: un `wx.ListCtrl` virtual devuelve 511 caracteres con el búfer de NVDA y el texto completo (3000) con el de la plantilla, leyendo desde OTRO proceso; `GetPropW` lee desde otro proceso la propiedad puesta por la app; el `id` de wx es el `GetDlgCtrlID` del control.
- Pruebas: 75 automáticas (74 pasan y 1 se omite porque el entorno no tiene `configobj`; esa también pasa usando el `configobj` del `library.zip` de NVDA). El manifiesto generado valida con el `configobj` y la especificación reales de NVDA 2026.3 beta 2.
- **No probado con NVDA real:** no se instaló el complemento ni se cargó en NVDA. Queda pendiente la prueba manual de `referencias/pruebas-manuales.md` (lectura completa, silencio del foco, gesto, instalación desde la app).
