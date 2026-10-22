# Cambios del agente documentacion-usuario

## Cómo ampliarlo

- **Nueva sección del manual o de las teclas:** en `plantillas/docs/`, con la misma estructura de encabezados; si cambia el formato de `teclas.md`, adapta `atajos_documentados` en `plantillas/tools/comprobar_teclas.py` y añade un caso en `plantillas/tests/test_comprobar_teclas.py`.
- **Nueva forma de declarar atajos en el código** (otra API de wx, un sistema propio de atajos configurables): en `atajos_de_codigo` de `comprobar_teclas.py`, con su prueba.
- **Nuevo nombre de tecla:** en `_TECLAS`, `_WXK` o `_MODIFICADORES`, y un caso en `test_normalizar`.
- **Cambios en el visor:** en `plantillas/app/ui/visor_ayuda.py` y `plantillas/tests/test_visor_ayuda.py`; la lógica sin wx va en `plantillas/app/core/documentacion.py`.
- **Nuevas reglas de estilo:** en `referencias/escribir-para-lectores-de-pantalla.md` (y en `SKILL.md` si son cortas y siempre aplicables).
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- El visor de ayuda ya no usa `wx.MessageBox` ni `wx.TextEntryDialog` (botones en inglés): `avisar` y `pedir_busqueda` usan `mostrar_mensaje` y `pedir_texto`, que toman los diálogos en español de `app/ui/accesibilidad/dialogos.py` con importación diferida si existen y, si no, un `wx.MessageDialog` con «Aceptar» o un diálogo propio con la etiqueta antes del campo y «Aceptar»/«Cancelar».
- Las pruebas con wx ya no crean ni destruyen su propia `wx.App`: reutilizan la de la sesión (`wx.GetApp() or wx.App(False)`, guardada en una variable de módulo para que Python no la destruya) y nunca la destruyen. Crearla y destruirla por módulo dejaba sin aplicación a los módulos siguientes de la batería del proyecto. Cuatro pruebas nuevas de los diálogos. Probado: 36 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: plantillas de manual, lista de teclas y registro de cambios; visor de ayuda accesible (HTML con `markdown` y Edge, o texto de solo lectura con lista de secciones), búsqueda con Ctrl+F y F3, cierre con Escape, menú Ayuda, novedades una vez por versión; `tools/comprobar_teclas.py` (menús con tabulador, `AcceleratorEntry`, tuplas de `AcceleratorTable`, comentarios `# atajo:`, rangos, secciones exentas, relación sección-clase); `tools/notas_version.py`; tres referencias.
- Probado en este equipo con Python 3.13 y wxPython 4.2.5: 32 pruebas pasan con `markdown` disponible (30 pasan y 2 se omiten sin él). El modo HTML se comprobó con el motor Edge real: la página carga con 4 encabezados, `lang="es"` y título, y la búsqueda de `WebView.Find` encuentra y avisa cuando no hay resultados. Las herramientas se ejecutaron también como scripts desde la línea de órdenes. `ruff` sin avisos con `target-version = py310`.
- Sin verificar: lectura real con NVDA o JAWS; que Ctrl+F, F3 y Escape lleguen al diálogo con el foco dentro de la página web de Edge; ejecución con Python 3.10 (solo se comprobó la sintaxis con ruff).
