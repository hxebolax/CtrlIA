# Elegir y fijar la versión de Python

No hay una versión fija. El usuario puede necesitar la más reciente o, en algún caso, una más antigua.

## Cómo decidir

1. **Si el proyecto ya la declara, esa manda.** Busca por este orden:
   - `requires-python` en `pyproject.toml`;
   - `.python-version`;
   - `VERSION_PY` en `entorno.bat`;
   - el Python del entorno virtual existente (`.venv\Scripts\python.exe --version`).
2. **Si no hay nada declarado,** pregunta al usuario. Si no puede contestar, propón la versión **más reciente** que cumpla todo esto:
   - hay ruedas (wheels) de **wxPython** para esa versión y Windows de 64 bits (https://pypi.org/project/wxPython/#files);
   - **cx_Freeze** la admite (https://pypi.org/project/cx-Freeze/);
   - las demás dependencias tienen ruedas para ella, sobre todo las que llevan código nativo.
3. **Versiones antiguas:** úsalas solo si hay un motivo, por ejemplo una dependencia que no se ha actualizado o un sistema operativo antiguo. Python 3.9 y posteriores no funcionan en Windows 7; para eso haría falta 3.8, con versiones antiguas de wxPython y cx_Freeze.

Comprueba qué versiones hay instaladas con `py -0p` (o `py list` desde Python 3.14). Para instalar una que falte: `py install 3.x` o el instalador de python.org.

## Dónde se fija (y debe coincidir)

| Lugar | Ejemplo |
|---|---|
| `entorno.bat` | `set "VERSION_PY=3.12"` (vacío = la más reciente instalada) |
| `pyproject.toml` | `requires-python = ">=3.12"` |
| `pyproject.toml` (ruff) | `target-version = "py312"` |
| `.python-version` (opcional) | `3.12` |

## Compatibilidad de las plantillas

- Las plantillas de los agentes usan `from __future__ import annotations` y solo la biblioteca estándar, así que funcionan con **Python 3.10 o superior**. Se han probado con la versión instalada en el equipo de desarrollo.
- `tests/test_distribucion.py` usa `tomllib` (3.11+) y se salta esa comprobación si no está disponible.
- Si el proyecto necesita una versión anterior a la 3.10, revisa las plantillas antes de usarlas: sintaxis de tipos fuera de anotaciones, `traceback.format_exception` con un solo argumento, `ExceptionGroup`/`add_note` (3.11), etc.

## Cambiar de versión en un proyecto existente

1. Cambia `VERSION_PY`, `requires-python` y `target-version`.
2. Borra el entorno virtual (`.venv`) y recréalo con `entorno.bat crear`.
3. Ejecuta `entorno.bat pruebas` y `entorno.bat compilar`, y arranca la versión compilada.
4. Anota el cambio en el changelog del proyecto.
