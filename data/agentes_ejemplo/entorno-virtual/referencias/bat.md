# entorno.bat: anatomía, reglas y accesibilidad

## Estructura de la plantilla

1. **Cabecera** con uso y órdenes disponibles.
2. `setlocal EnableExtensions EnableDelayedExpansion`, `chcp 65001 >nul` y `cd /d "%~dp0"` (el `.bat` funciona aunque se lance desde otra carpeta o con doble clic).
3. **Configuración**: `NOMBRE_APP`, `VERSION_PY`, `VENV` (`.venv` o `venv`), `PY` y `PYW` (rutas directas al Python del entorno), `SETUP`, `BUILD_DIR`.
4. Si hay argumento, `:ORDEN` lo despacha **sin menú** (útil para una IA, para CI o para accesos directos): `entorno.bat compilar` devuelve 0 si todo fue bien.
5. `:MENU` numerado, sin `cls`, con `set /p`.
6. **Subrutinas** (`:CREAR`, `:INSTALAR`, `:COMPILAR`...) que terminan en `exit /b <código>`; se llaman con `call :ETIQUETA` para poder encadenarlas (`CREAR` llama a `INSTALAR`, `COMPILAR` a `EMPAQUETAR`).

## Órdenes

| Orden | Menú | Qué hace |
|---|---|---|
| `crear` | 1 | Crea el venv con `py -<VERSION_PY>` (o la predeterminada del lanzador `py`, o `python`) e instala dependencias |
| `instalar` | 2 | `pip install -r requirements.txt` y `requirements-dev.txt`; ejecuta `tools\despues_de_instalar.py` si existe |
| `actualizar` | 3 | `pip install --upgrade -r ...` |
| `ejecutar` | 4 | `start "" pythonw.exe main.py`: sin consola, como la verá el usuario |
| `depurar` | 5 | `python.exe -X dev -X faulthandler main.py` con `APP_DEPURACION=1` (registro en DEBUG y en consola) |
| `pruebas` | 6 | `python -m pytest tests -q` |
| `compilar` | 7 | `tools\antes_de_compilar.py` (si existe) → `setup_cx.py build` → `tools\empaquetar.py` |
| `compilar-debug` | 8 | `setup_cx.py build --debug`: misma compilación con consola, para ver errores |
| `empaquetar` | — | Solo el ZIP y `version.json` de una compilación existente |
| `limpiar` | 9 | Borra `build`, `dist`, cachés y `__pycache__` (no toca el venv ni `data`) |
| `datos` / `registros` | 10 / 11 | Abre `data` o `data\logs` en el Explorador |
| `diagnostico` | 12 | Versiones de Python, wx, cx_Freeze, `pip check`, lectores de pantalla en ejecución |
| `congelar` | 13 | `pip freeze > requirements-lock.txt` (versiones exactas probadas) |

## Reglas técnicas

- **Finales de línea CRLF.** Con LF puro, `cmd` calcula mal las posiciones de las etiquetas y `goto`/`call :x` fallan de forma intermitente. Al generar el archivo desde Python: `ruta.write_text(texto.replace("\n", "\r\n"), encoding="utf-8", newline="")`.
- **UTF-8 sin BOM** y `chcp 65001` como primera orden tras `@echo off`: con BOM la primera línea se corrompe. Si hay dudas sobre el equipo, escribe los mensajes sin tildes.
- **Rutas entre comillas siempre** (`"%PY%"`, `"%VENV%"`): los proyectos pueden estar en carpetas con espacios.
- **No uses `activate`**: llama al Python del entorno por su ruta (`"%VENV%\Scripts\python.exe" -m pip ...`). Es más robusto y funciona igual desde cualquier consola.
- **`python -m pip`**, nunca `pip` a secas (evita usar un pip de otro Python).
- **Errores**: fuera de bloques, `if errorlevel 1`; dentro de bloques `( ... )`, también `if errorlevel 1` (no `%ERRORLEVEL%`, que se expande al leer el bloque) o `!ERRORLEVEL!` con expansión retardada.
- **Variables dentro de un `for` o de un bloque**: `!VAR!` (expansión retardada).
- **Excluir el venv al limpiar `__pycache__`**: con sustitución de cadenas (`"!RUTA:\.venv\=!"=="!RUTA!"`), no con `findstr` (sus barras invertidas son escapes).
- **`start "" ...`**: el primer argumento entre comillas es el título; sin él, una ruta entre comillas se tomaría como título.
- **`py` frente a `python`**: el lanzador `py` elige versión (`py -3.12`, `py -3.14`...). Desde Python 3.14 el lanzador pasa a ser el «Python install manager» (`py list`, `py install 3.x`), y `py -3.x` sigue funcionando. Sin versión, usa `py` a secas (la predeterminada, marcada con `*` en `py -0p`), nunca `py -3`: elegiría la más alta instalada aunque sea una versión de prueba sin ruedas de wxPython. La versión se fija en `VERSION_PY` (ver [version-python.md](version-python.md)).

## Accesibilidad del menú

- **Sin `cls`**: el lector de pantalla pierde el contexto y no se puede revisar lo anterior.
- Opciones **numeradas**, una por línea, con verbos claros.
- `set /p "OPCION=Elija una opción y pulse Intro: "` en lugar de `choice` (que pita y da menos pistas).
- Cada acción termina con una frase de resultado inequívoca («Dependencias instaladas correctamente.», «ERROR: la compilación ha fallado.»).
- `pause` solo en el menú interactivo, nunca en el modo por órdenes (bloquearía a una IA o a un script).

## Extender el .bat

- Pasos previos a la compilación del proyecto (generar sonidos, empaquetar extensiones, construir un complemento de NVDA): ponlos en `tools\antes_de_compilar.py`; el `.bat` lo llama si existe y para si devuelve error.
- Pasos tras instalar (compilar una extensión nativa opcional, descargar un binario...): `tools\despues_de_instalar.py`.
- Nueva orden: añade la línea al menú, la línea en `:ORDEN`, la subrutina con `exit /b`, y la fila en la cabecera.
