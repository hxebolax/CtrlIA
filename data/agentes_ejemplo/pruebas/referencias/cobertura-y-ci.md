# Cobertura, marcadores y ejecución automática

## Órdenes habituales

| Para qué | Orden |
|---|---|
| Batería por defecto (sin `red` ni `interfaz`) | `python -m pytest` |
| Rápida, sin lo lento | `python -m pytest -m "not lento and not red and not interfaz"` |
| Solo un archivo o una prueba | `python -m pytest tests/test_ajustes.py -k cancelar` |
| Parar en el primer fallo, con detalle | `python -m pytest -x -vv` |
| Repetir solo lo que falló la última vez | `python -m pytest --lf` |
| Pruebas de red (salen a Internet) | `python -m pytest -m red` |
| Pruebas de interfaz real (roban el foco) | `python -m pytest -m interfaz` |
| Todo, incluido lo excluido | `python -m pytest -m ""` |
| Cobertura en consola | `python -m pytest --cov --cov-report=term-missing` |
| Cobertura en HTML (`htmlcov/index.html`) | `python -m pytest --cov --cov-report=html` |
| Las 10 pruebas más lentas | `python -m pytest --durations=10` |

Un `-m` en la línea de órdenes sustituye al de `addopts`.

Usa siempre el Python del entorno virtual del proyecto (`venv\Scripts\python.exe -m pytest`), no un `pytest` suelto del PATH: podría ser otro Python sin wxPython.

## Cobertura con pytest-cov

- `pytest-cov` y `coverage` como dependencias de desarrollo.
- `[tool.coverage.run]`: `source = ["app"]` (solo nuestro código) y `branch = true` (cuenta también las ramas de un `if` que nunca se tomaron, donde suelen esconderse los errores).
- `[tool.coverage.report]`: `show_missing` (líneas sin cubrir) y `skip_covered` (lista más corta: se oye antes lo importante).

### Umbral razonable

1. Mide primero, sin umbral.
2. Fija `fail_under` en la cifra actual redondeada hacia abajo (por ejemplo, 63 % → 60).
3. Súbelo de 5 en 5 cuando la cobertura real lo supere con holgura.
4. Como referencia: en lógica (`app/core`, `app/services`) apunta alto (80 % o más); la interfaz tendrá menos y no pasa nada.

Qué mirar en el informe, más que el total: las ramas de error sin cubrir (`except`, validaciones, cancelaciones) en el código que maneja datos del usuario.

La cobertura no mide si las aserciones comprueban algo útil: una prueba sin `assert` sube la cifra sin proteger nada.

## Marcadores

Registrados en la configuración y en el conftest (`--strict-markers` convierte un marcador mal escrito en error):

- `lento`: tarda más de un segundo. Se incluye por defecto; se excluye con `-m "not lento"` al iterar rápido.
- `red`: necesita Internet de verdad. Excluido por defecto; el resto de pruebas tienen la red bloqueada.
- `interfaz`: muestra ventanas o envía teclas reales. Excluido por defecto.

Si el proyecto necesita otros (por ejemplo, pruebas que modifican el sistema y solo deben ejecutarse en una máquina virtual), decláralos igual y exclúyelos en `addopts`.

## Integración con entorno.bat

Si el proyecto tiene un `entorno.bat` con menú y órdenes, añade (o adapta) estas subrutinas, con el mismo estilo que las existentes. El archivo debe guardarse en CRLF y UTF-8 sin BOM.

```bat
:PRUEBAS
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pytest
exit /b %ERRORLEVEL%

:PRUEBAS_RAPIDAS
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pytest -x -m "not lento and not red and not interfaz"
exit /b %ERRORLEVEL%

:COBERTURA
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pytest --cov --cov-report=term-missing --cov-report=html
exit /b %ERRORLEVEL%
```

El código de salida de pytest se conserva: 0 si todo pasa, 1 si hay fallos, 5 si no se recogió ninguna prueba. Así `entorno.bat pruebas` sirve también en scripts y antes de compilar.

Antes de compilar con cx_Freeze, ejecuta la batería: una compilación de código que no pasa las pruebas no se distribuye.

## Ejecución automática (integración continua)

Si el proyecto está en GitHub, un flujo de trabajo en Windows ejecuta la batería en cada envío. Plantilla orientativa (no se ha ejecutado en este agente; compruébala en el repositorio):

```yaml
name: pruebas
on: [push, pull_request]
jobs:
  pruebas:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"   # la del proyecto
      - run: python -m pip install -r requirements.txt -r requirements-dev.txt
      - run: python -m pytest --cov --cov-report=term-missing
```

Notas:

- wxPython publica ruedas para Windows; en Linux habría que compilarlo o usar las ruedas de extras, y las pruebas MSAA no aplican.
- Las pruebas `interfaz` no se ejecutan en la integración continua.
- Sin red por defecto: una prueba que falla solo en el servidor suele estar saliendo a Internet o dependiendo del idioma o la zona horaria.

## Pruebas que se cuelgan

pytest no tiene tiempo límite por prueba de serie. Si una prueba puede colgarse (un `ShowModal` sin simular, un hilo que no termina), usa `esperar_hasta` con tiempo, `hilo.join(timeout=...)` y, si hace falta, el complemento `pytest-timeout` como dependencia de desarrollo (`--timeout=60`).

## Fuentes

- pytest-cov: https://pytest-cov.readthedocs.io/
- Configuración de coverage: https://coverage.readthedocs.io/en/latest/config.html
- Marcadores de pytest: https://docs.pytest.org/en/stable/how-to/mark.html
- xfail estricto: https://docs.pytest.org/en/stable/how-to/skipping.html
