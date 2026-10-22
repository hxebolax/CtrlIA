# cx_Freeze para wxPython en Windows

Versiones de referencia (septiembre de 2026): cx_Freeze 8.6-8.7 (Python 3.10-3.15), wxPython 4.2.5 / 4.3.x. Documentación: https://cx-freeze.readthedocs.io/en/stable/setup_script.html

La plantilla (`setup_cx.py` + `verificar_build.py` + `empaquetar.py`) se probó compilando un proyecto mínimo con wxPython 4.2.5 y cx_Freeze 8.6.4: excluye `data/config` y `data/logs`, copia las DLL de Visual C++, genera el ZIP y el `.exe` arranca y se cierra con código 0. En otra versión de Python, repite la prueba.

## setup.py/setup_cx.py frente a pyproject.toml

- Las dos vías siguen documentadas: `python setup_cx.py build` y `cxfreeze build` (configuración en `[tool.cxfreeze]` y `[tool.cxfreeze.build_exe]`).
- **Usa `setup_cx.py`** cuando hace falta lógica: leer la versión, filtrar `data/` archivo a archivo, verificar binarios, limpiar el destino. Es el caso de los proyectos del usuario.
- `base="gui"` (antes `"Win32GUI"`, que ya no se acepta en las versiones recientes de Python con cx_Freeze 8). Otras bases: `"console"`, `"service"`, `"gui_dgpu"`.

## Opciones de build_exe que importan

| Opción | Uso en la plantilla |
|---|---|
| `build_exe` | Carpeta de salida fija `build/<NOMBRE_APP>` (en vez de `exe.win-amd64-3.x`) |
| `packages` | Paquetes completos: `wx`, `app` y los que se cargan dinámicamente |
| `includes` | Módulos sueltos que el análisis no detecta (`win32timezone` para keyring, `sgmllib` para feedparser...) |
| `excludes` | `tkinter`, `unittest`, `test`, `pydoc_data` y herramientas de desarrollo (`pip`, `pytest`, `ruff`, `cx_Freeze`) |
| `include_files` | Tuplas `(origen, destino)`; destino SIEMPRE relativo. La plantilla genera la lista archivo a archivo |
| `include_msvcr` | `True`: runtimes de Visual C++ junto al `.exe` |
| `optimize` | `1`. Con `2` se eliminan las docstrings y fallan bibliotecas que las usan (clientes de API que generan métodos a partir de docstrings, argparse...) |
| `replace_paths` | `[("*", "")]`: las trazas muestran `app/ui/ventana.py`, no la ruta del equipo que compiló |
| `zip_include_packages` | NO metas en el zip paquetes con DLL o datos (`wx`, `accessible_output2`, `certifi`): las DLL no se cargan desde un zip |
| `bin_includes` / `bin_excludes` | Para DLL que el análisis no detecta o que sobran |
| `silent_level` | 0-3; los avisos «? módulo imported from ...» suelen ser inofensivos |

## include_msvcr: qué hace exactamente

- cx_Freeze descarga el `vc_redist.x64.exe` oficial de Microsoft (lo guarda en caché en `%APPDATA%\cxfreeze\vc_redist\<versión>`), extrae sus DLL y las copia **junto al ejecutable**: `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll`, `msvcp140_1.dll`, `msvcp140_2.dll`, `concrt140.dll`, `vcomp140.dll`... y deja la licencia en `share/licenses/vc_redist/`. **Conserva esa licencia.**
- Equivale a `include_msvcr_version=17`. La versión 15 añade la UCRT para Windows 8.1 sin actualizar pero no trae `vcruntime140_1.dll`: no la uses con un Python de 64 bits moderno.
- En Windows 10/11 la UCRT es del sistema. Las versiones de Python posteriores a la 3.8 no funcionan en Windows 7: si el usuario necesita Windows 7, hay que elegir la versión de Python en consecuencia.
- Sin esta opción, esas DLL no se copian y el usuario necesitaría instalar el redistribuible.

## Problemas conocidos con wxPython y compañía

- **Idioma de los botones**: wxPython trae `wx/locale/<idioma>/LC_MESSAGES/wxstd.mo` y lo registra al importarse. cx_Freeze copia los datos de los paquetes que van fuera del zip (el comportamiento por defecto). Verifica `lib/wx/locale/es/LC_MESSAGES/wxstd.mo`.
- **wx.html2 / WebView2**: `WebView2Loader.dll` viaja dentro de `lib/wx`. El equipo necesita el Runtime de Edge WebView2; comprueba `wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendEdge)` y ten alternativa.
- **accessible_output2**: sus DLL (`nvdaControllerClient64.dll`, `SAAPI32.dll`...) están en `accessible_output2/lib`; en la compilada las busca en `lib/accessible_output2/lib`. Con `packages=["accessible_output2"]` quedan ahí.
- **certifi/requests**: `lib/certifi/cacert.pem` debe existir (paquete fuera del zip).
- **sys.stdout / sys.stderr valen `None`** con `base="gui"`: un `sys.stderr.write` o `faulthandler.enable()` sin archivo fallan. Al arrancar, redirígelos a un archivo de registro de `data/logs` si valen `None`.
- **multiprocessing**: `multiprocessing.freeze_support()` justo al empezar `if __name__ == "__main__":`.
- **pkg_resources** se retiró de setuptools 82 (febrero de 2026): usa `importlib.resources` e `importlib.metadata`.
- **Importaciones dinámicas** (plugins, `importlib.import_module`): añade el paquete a `packages` o el módulo a `includes`.
- **Archivos de datos junto al código** (por ejemplo un `.json` dentro de `app/`): cx_Freeze no los copia solos si `app` va a `library.zip`; añádelos con `include_files` a `lib/app/...` o carga los datos desde `data/`.

## Recurso de versión del .exe

Los metadatos de `setup()` van a las propiedades del ejecutable: `version` → FileVersion/ProductVersion (debe cumplir PEP 440), `description` → FileDescription, `author` → CompanyName, `name` → ProductName, `Executable(copyright=...)` → Copyright.

## Más allá del ZIP

- `bdist_msi` crea un instalador MSI (`upgrade_code` fijo para que las actualizaciones funcionen). El usuario distribuye ZIP portable: no lo generes salvo que lo pida.
- No existe «un solo .exe» con cx_Freeze; la distribución es una carpeta.

## Comprobaciones tras compilar (las hace verificar_build.py)

1. Existe `<App>.exe`.
2. Están `vcruntime140.dll`, `vcruntime140_1.dll` y `msvcp140.dll`.
3. Si viajan `wx`, `accessible_output2` o `certifi`, están sus recursos.
4. `data/` no tiene carpetas prohibidas ni archivos `.log`, `.db`, `.key`, `__pycache__`...
5. No viajan `pytest`, `pip`, `ruff` ni `cx_Freeze`.
