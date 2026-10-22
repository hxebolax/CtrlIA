# Cambios del agente accesibilidad-wx

## Cómo ampliarlo

- **Nuevo control o patrón:** módulo en `plantillas/app/ui/accesibilidad/`, exportado en `__init__.py`, con pruebas en `plantillas/tests/` (lógica, wx y, si es posible, MSAA), una fila en la tabla de plantillas de `SKILL.md` y su explicación en la referencia correspondiente.
- **Nueva regla:** en «Reglas de oro» de `SKILL.md` si es corta y general; si no, en `referencias/`.
- **Nuevo conocimiento sobre un control** (una versión de wxPython que corrige algo, un lector que se comporta distinto): actualiza `referencias/controles.md` con la versión y cómo se comprobó.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- Las pruebas con wx ya no crean ni destruyen su propia `wx.App`: reutilizan la de la sesión (`wx.GetApp() or wx.App(False)`, guardada en una variable de módulo para que Python no la destruya) y nunca la destruyen. Crearla y destruirla por módulo dejaba sin aplicación a los módulos siguientes de la batería del proyecto. Afecta a `test_msaa.py` y `test_controles_wx.py`.
- `test_checklistbox_expone_rol_y_estado` fuerza el `wx.Accessible` propio (desde wxPython 4.3 no se instala por defecto y la prueba fallaba); prueba nueva del comportamiento por defecto. Probado: 44 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: lista multicolumna accesible (normal y virtual), lista de casillas accesible, composición de anuncios, etiquetas y nombres, diálogos en español, ayuda Ctrl+H, voz y braille con canales, deslizador con valor real, teclado, progreso, idioma y accesibilidad visual.
- Pruebas: 43 automáticas con wxPython 4.2.5 (composición, controles y 8 de MSAA). No sustituyen a la prueba con lectores reales.
