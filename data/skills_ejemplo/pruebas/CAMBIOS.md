# Cambios del agente pruebas

## Cómo ampliarlo

- **Nueva fixture o utilidad:** en `plantillas/tests/conftest.py` (fixtures) o `plantillas/tests/ayudas_wx.py` (funciones), con su prueba en `plantillas/tests/test_infraestructura.py` y una fila en la tabla «Fixtures y utilidades» de `SKILL.md`.
- **Nueva técnica de ejemplo:** un bloque numerado más en `plantillas/tests/test_ejemplos.py`, autónomo (el código probado dentro del propio archivo).
- **Nuevo conocimiento sobre wx** (un evento que se comporta distinto, una versión que cambia algo): en `referencias/probar-interfaz-wx.md`, con la versión de wxPython y cómo se comprobó.
- **Nuevo marcador:** en `MARCADORES` del conftest, en `markers` de `plantillas/pyproject-pytest.toml` y en `referencias/cobertura-y-ci.md`.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- `conftest.py`: fixture automática `_sin_modales_reales` que hace fallar al momento cualquier `ShowModal` (de `wx.Dialog`, de los diálogos propios y de los nativos) o `wx.MessageBox` real no simulado, con el título del cuadro y la prueba, salvo en las marcadas `interfaz`; `dialogo_modal` la sustituye. Tres pruebas nuevas. Probado: 46 pruebas, 1 omitida y 3 fallos esperados.

## 1.0.0 - 2026-09-25

- Primera versión: conftest con aislamiento de `data/` (al importar y por prueba, con aviso si el `data/` real cambia), bloqueo de red con `ConexionBloqueada`, `wx.App` de sesión, marcos que se destruyen solos, voz y diálogos modales simulados (`ShowModal` y `wx.MessageBox`) y marcadores `lento`, `red` e `interfaz`; utilidades para procesar eventos, esperar condiciones con temporizadores activos, teclas sintéticas, órdenes, `UIActionSimulator` protegido y MSAA; configuración de pytest y coverage; ocho ejemplos de técnicas; cuatro referencias.
- Probado de verdad con Python 3.13, wxPython 4.2.5, pytest 9.1, pytest-cov 7.1 y comtypes 1.4 en Windows 11, en una copia de proyecto con `app/` y un `data/` real de control:
  - 47 pruebas (23 de ejemplos y 24 de infraestructura): 42 pasan, 3 `xfail` esperados y 2 excluidas por defecto (`red` e `interfaz`); con `-m red`, la de red pasa.
  - La prueba `interfaz` con teclas reales pasó en una ejecución y se saltó en otra porque Windows no dejó la ventana en primer plano (es el comportamiento previsto de la protección).
  - Comprobado aparte: una excepción en un hilo hace fallar la prueba, un `xfail` que pasa falla con `XPASS(strict)`, un marcador mal escrito es un error, escribir en el `data/` real produce el aviso final, la cobertura con umbral funciona y el `data/` real quedó intacto.
- Sin verificar: Python 3.10 a 3.12 (el código evita sintaxis posterior a 3.10), el flujo de GitHub Actions de `referencias/cobertura-y-ci.md` y otras versiones de wxPython.
