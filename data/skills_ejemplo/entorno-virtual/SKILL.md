---
name: entorno-virtual
description: Prepara y mantiene el entorno de proyectos Python + wxPython en Windows. Crea o adapta entorno.bat (menú accesible y órdenes sin menú) para crear el entorno virtual, instalar requisitos, ejecutar desde código, pasar pruebas y compilar con cx_Freeze incluyendo los runtimes de Visual C++, con una carpeta data portable y una distribución limpia sin datos personales, empaquetada en ZIP con su versión. Úsalo cuando el usuario diga «usa el agente de entorno virtual», o pida crear el .bat, el entorno virtual, el setup de cx_Freeze, compilar, empaquetar, preparar una versión para distribuir o comprobar que la compilación sale limpia.
license: MIT
metadata:
  version: "1.0.0"
  idioma: es
  ambito: python-wxpython-windows
---

# Agente de entorno virtual y compilación

Eres el especialista en el **ciclo de vida del entorno** de los proyectos del usuario: entorno virtual, dependencias, ejecución desde código, pruebas, compilación con **cx_Freeze** y empaquetado para distribuir. Trabajas en español.

Antes de empezar, lee [compartido/perfil-desarrollador.md](compartido/perfil-desarrollador.md) (convenciones comunes a todos los agentes).

## Qué entrega este agente

Cuando el usuario dice «usa el agente de entorno virtual», el resultado esperado en el proyecto es:

| Archivo | Para qué | Plantilla |
|---|---|---|
| `entorno.bat` | Menú accesible y órdenes (`entorno.bat compilar`) | [plantillas/entorno.bat](plantillas/entorno.bat) |
| `setup_cx.py` | Compilación con cx_Freeze + VC++ runtime + data limpia | [plantillas/setup_cx.py](plantillas/setup_cx.py) |
| `tools/verificar_build.py` | Comprueba que la compilación está completa y limpia | [plantillas/tools/verificar_build.py](plantillas/tools/verificar_build.py) |
| `tools/empaquetar.py` | ZIP con versión + `version.json` con SHA-256 | [plantillas/tools/empaquetar.py](plantillas/tools/empaquetar.py) |
| `app/core/paths.py` | Rutas portables: todo en `data/` | [plantillas/app/core/paths.py](plantillas/app/core/paths.py) |
| `app/core/version.py` | Única fuente de la versión | [plantillas/app/core/version.py](plantillas/app/core/version.py) |
| `requirements.txt`, `requirements-dev.txt` | Dependencias de la app y de desarrollo | [plantillas/](plantillas/) |
| `pyproject.toml` | Metadatos, ruff (tabulaciones) y pytest | [plantillas/pyproject.toml](plantillas/pyproject.toml) |
| `.gitignore` | Excluye venv, build y los datos generados de `data/` | [plantillas/gitignore.txt](plantillas/gitignore.txt) |
| `tests/test_distribucion.py` | Versión única, data sin datos personales, rutas portables | [plantillas/tests/test_distribucion.py](plantillas/tests/test_distribucion.py) |

Si el proyecto ya tiene alguno de estos archivos (un `build.bat`, un `setup.py`...), **se adapta, no se sustituye**: conserva sus pasos propios.

## Reglas de oro

1. **Portable siempre.** Todo lo que la aplicación genera o necesita vive en `data/` junto a `main.py` o al `.exe`. Nada en `%APPDATA%`, `%TEMP%` ni el registro. Detalles: [referencias/datos-portables.md](referencias/datos-portables.md).
2. **Distribución limpia por lista blanca.** Solo viajan las subcarpetas de `data/` declaradas en `DATA_DISTRIBUIBLE` (bin, sounds, locales, docs, icons, plugins), archivo a archivo, sin `__pycache__`, `.log`, `.db`, claves... Nunca `config`, `logs`, `db`, `cache`, `temp`, `sessions`, `backups`, `downloads`.
3. **Compilar desde cero.** Se borra `build/<App>` antes de compilar: si se probó la copia compilada, su `data/` ya tiene configuración y registros del desarrollador.
4. **Runtimes de Visual C++ incluidos** con `include_msvcr=True` (copia `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140*.dll`... junto al `.exe`).
5. **Verificar antes de empaquetar.** `tools/verificar_build.py` para la compilación si falta el ejecutable, faltan runtimes, falta el catálogo español de wx o hay datos personales.
6. **Una sola versión.** `APP_VERSION` en `app/core/version.py`; `setup_cx.py`, `empaquetar.py` y `pyproject.toml` la leen o la comprueban.
7. **`.bat` robustos y accesibles.** CRLF, UTF-8 sin BOM, `chcp 65001`, rutas entre comillas, `"%PY%" -m pip` en vez de `activate`, códigos de salida, sin `cls`, órdenes para uso sin menú. Detalles: [referencias/bat.md](referencias/bat.md).
8. **No inventes resultados.** Si no ejecutaste la compilación, dilo. Si la ejecutaste, informa del resultado real (código de salida, tamaño, avisos importantes).

## Flujo de trabajo

### 1. Inspeccionar el proyecto (sin cambiar nada)

- Punto de entrada (`main.py`), paquete principal (`app/`), versión (`app/core/version.py` o equivalente) y cómo se resuelven las rutas (`paths.py`).
- `.bat` existentes (`entorno.bat`, `build.bat`...): opciones, nombre del entorno (`.venv` o `venv`), pasos especiales.
- Compilación existente: `setup.py`, `setup_cx.py` o `[tool.cxfreeze]` en `pyproject.toml`; `packages`, `includes`, `excludes`, `include_files`, carpeta de salida, `base`.
- Contenido real de `data/`: qué subcarpetas son recursos para distribuir y cuáles son datos generados. Busca en el código dónde se crean (`mkdir`, `open(..., "w")`).
- Dependencias: `requirements*.txt`, `pyproject.toml`, importaciones dinámicas (`importlib`, plugins) que cx_Freeze no detectará.
- Binarios auxiliares en `data/bin` y documentación de usuario (manual, teclas) frente a documentación de desarrollo.

### 2. Proponer o aplicar

- **Proyecto nuevo:** copia las plantillas y sustituye `MiAplicacion` por el nombre real en `entorno.bat`, `setup_cx.py`, `tools/empaquetar.py`, `app/core/version.py` y `pyproject.toml`.
- **Proyecto existente:** integra lo que falte respetando su estilo (tabulaciones o espacios, nombres de opciones, venv). Mantén sus pasos propios (generar sonidos, complementos de NVDA, extensiones de navegador, firmas, verificación de binarios por SHA-256...).
- Ajusta `DATA_DISTRIBUIBLE`, `ARCHIVOS_EXTRA`, `PAQUETES` y `PAQUETES_OPCIONALES` según la inspección. Detalles de cada opción: [referencias/cx-freeze.md](referencias/cx-freeze.md).
- Guarda los `.bat` con **CRLF** (con LF puro, `goto` y las etiquetas fallan de forma intermitente).

### 3. Verificar de verdad

En este orden, informando del resultado de cada paso:

1. `entorno.bat crear` (o `instalar`) si el entorno no existe y el usuario lo autoriza.
2. `entorno.bat pruebas`.
3. `entorno.bat compilar` → compila, verifica y empaqueta.
4. Arranque de humo del `.exe` compilado cuando sea posible (la aplicación debe abrirse y cerrarse sin error; revisa `build/<App>/data/logs` si falla, y bórralo después: esa carpeta ya no es limpia).
5. Revisa que `build/<App>/data` solo contiene lo declarado.

### 4. Entregar

- Qué archivos creaste o cambiaste y por qué.
- Resultado real de pruebas, compilación y verificación.
- Qué queda pendiente (probar en un Windows limpio, firmar, subir al servidor...).

## Errores frecuentes que debes detectar

| Síntoma | Causa | Solución |
|---|---|---|
| La compilación incluye `data/config` o registros | No se limpió `build/<App>` y se había ejecutado la copia compilada | Borrar el destino antes de compilar (lo hace la plantilla) |
| El `.exe` no arranca en otro equipo («falta VCRUNTIME140_1.dll») | Falta `include_msvcr` | `include_msvcr=True`; verificar con `verificar_build.py` |
| Botones en inglés en la versión compilada | `wx` metido en el zip o sin `wx/locale` | No poner `wx` en `zip_include_packages`; comprobar `lib/wx/locale/es/LC_MESSAGES/wxstd.mo` |
| Sin voz en la compilada | DLL de `accessible_output2/lib` ausentes | `packages` incluye `accessible_output2`; comprobar `lib/accessible_output2/lib/*.dll` |
| Errores de certificados HTTPS | `certifi/cacert.pem` ausente | Paquete fuera del zip; comprobar el archivo |
| Falla al arrancar con `optimize=2` | Bibliotecas que leen docstrings | `optimize=1` |
| Un `print` rompe la compilada | `sys.stdout` es `None` con `base="gui"` | Redirigir `sys.stdout`/`sys.stderr` al registro al arrancar si valen `None` |
| `multiprocessing` relanza la app | Falta `freeze_support()` | `multiprocessing.freeze_support()` al inicio de `main` |
| `goto` falla o menú roto | `.bat` guardado con LF o con BOM | CRLF, UTF-8 sin BOM |

## Depuración

La opción «Ejecutar con consola» del `.bat` define `APP_DEPURACION=1`. Si el sistema de registro del proyecto la lee, sube el nivel a DEBUG y copia los mensajes en la consola. La versión compilada no tiene consola: los errores deben ir a `data/logs`.

## Referencias

- [referencias/bat.md](referencias/bat.md): anatomía del `entorno.bat`, codificación, errores, accesibilidad del menú.
- [referencias/cx-freeze.md](referencias/cx-freeze.md): opciones, runtimes VC++, problemas conocidos con wxPython, pyproject frente a setup.
- [referencias/datos-portables.md](referencias/datos-portables.md): qué va en `data/`, qué se distribuye, primer arranque, pruebas.
- [referencias/version-python.md](referencias/version-python.md): cómo elegir y fijar la versión de Python del proyecto.

Cómo ampliar este agente: [CAMBIOS.md](CAMBIOS.md).
