# Cambios del agente traducciones

## Cómo ampliarlo

- **Nueva función de traducción que extraer** (por ejemplo, un alias propio): en `FUNCIONES` de `plantillas/tools/extraer_cadenas.py`, en `_instalar` de `plantillas/app/core/i18n.py` y en la lista de `builtins` de ruff que recomienda `SKILL.md`, con un caso en `plantillas/tests/test_traducciones.py`.
- **Nueva validación al compilar:** en `validar_entrada` de `plantillas/tools/compilar_traducciones.py`, decidiendo si es error (la entrada no se compila) o aviso, con su prueba.
- **Nuevo idioma con regla de plural conocida:** en `FORMAS_PLURALES` de `plantillas/tools/actualizar_po.py` (fuente: manual de GNU gettext o Unicode CLDR).
- **Nuevas reglas de estilo:** en `referencias/buenas-practicas-i18n.md` (y en `SKILL.md` si son cortas y siempre aplicables).
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- Las pruebas que crean `wx.Locale` se ejecutan en un intérprete aparte: los `wx.Locale` deben destruirse en orden inverso a su creación y, dentro de la batería de un proyecto que ya tiene uno activo, crear y destruir otros llegó a cerrar el intérprete de golpe. Ya no crean ni destruyen una `wx.App` de módulo. Probado: 20 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: `app/core/i18n.py` (instala `_`, `gettext`, `ngettext`, `n_`, `pgettext`, `npgettext` y `N_`; idioma por `APP_IDIOMA`, configuración, sistema o español; respaldo ante catálogo ausente o dañado; `wx.Locale` con referencia conservada; cambio en caliente con oyentes); `tools/extraer_cadenas.py` con `ast`; `tools/actualizar_po.py` (fusión con aproximadas y obsoletas, idiomas nuevos con su regla de plural); `tools/compilar_traducciones.py` (formato GNU `.mo`, contexto, plurales, validación de marcadores, aceleradores y `Plural-Forms`); `tools/catalogo_po.py`; tres referencias.
- Probado en este equipo con Python 3.13 y wxPython 4.2.5: 20 pruebas pasan. Los `.mo` generados los cargan `gettext.GNUTranslations` y también el lector propio de wxWidgets (`wx.Locale.AddCatalog` y `wx.GetTranslation` con singular y plural). Flujo completo ejecutado como scripts en un proyecto de ejemplo: extraer, crear el `.po` en inglés, traducir, compilar y arrancar con `APP_IDIOMA=en`. `configurar_wx` cambia los botones estándar («Cancel» pasa a «Cancelar» o «Annuler»). `ruff` sin avisos con `target-version = py310`.
- Sin verificar: ejecución con Python 3.10 (solo se comprobó la sintaxis con ruff); apertura y guardado de los `.po` generados en Poedit; recorrido con lector de pantalla de una aplicación traducida.
