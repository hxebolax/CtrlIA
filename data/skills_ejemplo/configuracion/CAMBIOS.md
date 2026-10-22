# Cambios del agente configuracion

## Cómo ampliarlo

- **Nueva capacidad de la configuración** (exportar/importar, perfiles, varias instancias): en `plantillas/app/core/configuracion.py`, con su prueba en `plantillas/tests/test_configuracion.py` y su explicación en `referencias/diseno-configuracion.md`.
- **Nuevo tipo de opción en el diálogo**: en `_crear_opcion`, `_leer_control` y `_escribir_control` de `plantillas/app/ui/dialogo_preferencias.py`, una fila en la tabla de tipos de `referencias/preferencias-accesibles.md` y una prueba que compruebe que la etiqueta va justo antes del control.
- **Nuevo método de protección de secretos**: una clase con `nombre`, `proteger` y `desproteger` en `plantillas/app/core/secretos.py`, con prueba de ida y vuelta, y sus contrapartidas en `referencias/secretos.md`.
- **Nueva regla o error frecuente**: en `SKILL.md` si es corta y general; si no, en la referencia que corresponda.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- Las pruebas con wx ya no crean ni destruyen su propia `wx.App`: reutilizan la de la sesión (`wx.GetApp() or wx.App(False)`, guardada en una variable de módulo para que Python no la destruya) y nunca la destruyen. Crearla y destruirla por módulo dejaba sin aplicación a los módulos siguientes de la batería del proyecto. Afecta a `test_dialogo_preferencias.py`. Probado: 42 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: `Configuracion` (valores por defecto en código, fusión que conserva claves desconocidas, archivo corrupto apartado con recuperación desde `.bak`, archivo ilegible no sobrescrito, escritura atómica con temporal único, `fsync`, `.bak` y reintentos de `os.replace`, `RLock` más candado de disco, claves «seccion.clave», versión de esquema y migraciones, validación de tipos y reglas, suscriptores con referencia débil, lotes, guardado diferido y valores de fábrica); `AlmacenSecretos` con DPAPI por `ctypes` y Fernet opcional; `DialogoPreferencias` declarativo y accesible.
- Probado en este equipo con Python 3.13, wxPython 4.2.5, pytest 9.1 y cryptography 49 en Windows 11: **42 pruebas superadas** (20 de configuración, 8 de secretos con DPAPI real y Fernet, 14 del diálogo creado sin mostrarlo). Además, con Python 3.14 en un entorno propio sin wxPython: 28 superadas y la del diálogo omitida. `ruff check` (objetivo Python 3.10) sin avisos salvo la convención N818 (nombres de excepción en español).
- Sin verificar: comportamiento real con NVDA o JAWS (cómo se anuncian las etiquetas, las pestañas y la ayuda de Ctrl+H), el diálogo mostrado de forma modal, DPAPI con perfiles móviles o tras cambiar la contraseña de Windows desde fuera del equipo, y la aplicación compilada con cx_Freeze.
