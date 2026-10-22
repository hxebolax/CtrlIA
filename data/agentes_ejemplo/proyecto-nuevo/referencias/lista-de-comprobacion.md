# Lista de comprobación de un proyecto nuevo

Repásala al terminar de crear el proyecto y antes de cada versión. Marca solo lo que hayas comprobado de verdad; lo demás, indícalo como pendiente.

## Arranque y estructura

- [ ] `python tools/crear_proyecto.py` ejecutado desde el esqueleto con `--nombre`, `--destino` y `--python` (versión detectada o elegida por el usuario); ningún archivo existente sobrescrito.
- [ ] `NOMBRE_APLICACION`, `ID_APLICACION` y `APP_VERSION` correctos en `app/core/version.py`; `pyproject.toml` con la misma versión.
- [ ] `main.py` conserva el orden: `freeze_support`, red de seguridad, registro, configuración, `wx.App`, `HelpProvider`, `wx.Locale` guardado, instancia única, voz, importaciones pesadas, ventana.
- [ ] `main.py --comprobar-arranque` sale con código 0 y el registro dice «Arranque completado» y «Cierre normal».
- [ ] La lógica nueva va en `app/services` sin importar wx.

## Accesibilidad

- [ ] Cada `TextCtrl`, `ListCtrl`, `Choice`, `ComboBox`, `SpinCtrl`... tiene su `StaticText` creada **justo antes** y con el mismo padre.
- [ ] Todas las etiquetas, botones y menús con `&` y en español (`&Aceptar`, `&Cancelar`, `&Cerrar`); sin letras de acceso repetidas en el mismo nivel.
- [ ] Menús con atajo tras `\t`; atajos estándar de Windows con su significado habitual; ningún atajo que robe teclas a los campos de texto sin reenviarlas.
- [ ] Foco inicial útil en cada ventana y diálogo; al cerrar un diálogo, el foco vuelve a donde estaba.
- [ ] Escape cierra los diálogos (`wx.ID_CANCEL` o `SetEscapeId`) y nunca la ventana principal; Alt+F4 cierra la principal.
- [ ] Textos largos en `TextCtrl` multilínea de solo lectura, no en `StaticText`.
- [ ] Ctrl+H muestra la ayuda de cada control (`SetHelpText`); F1 abre el manual.
- [ ] Menú contextual con `EVT_CONTEXT_MENU` (tecla Aplicaciones y Shift+F10) colocado sobre el elemento enfocado.
- [ ] Ningún control se toca fuera del hilo principal (`wx.CallAfter`); ninguna operación larga en el hilo de la interfaz.
- [ ] Sin ventanas de «espere» mientras el hilo principal está bloqueado.
- [ ] Probado a mano con NVDA (y, si se puede, JAWS y Narrador): nombre de cada control, orden de tabulación, anuncios. Si no se hizo, se dice.

## Portabilidad

- [ ] Todo lo generado va a `data/` (configuración, registros, temporales, caché, base de datos). Nada en `%APPDATA%`, `%LOCALAPPDATA%`, `%TEMP%` ni el registro de Windows.
- [ ] Todas las rutas salen de `app/core/paths.py`; ningún `os.getcwd()` ni ruta absoluta.
- [ ] La aplicación funciona desde una carpeta con espacios y tildes y desde un pendrive.
- [ ] `.gitignore` excluye `data/config`, `data/logs`, `data/temp` y demás carpetas generadas.

## Registro

- [ ] `getLogger(__name__)` en cada módulo y formato diferido (`log.info("... %s", valor)`), nunca f-strings en las llamadas de registro.
- [ ] Ningún `except: pass` silencioso: como mínimo `log.debug(..., exc_info=True)` con el motivo.
- [ ] `log.exception` dentro de los `except` que tratan un error.
- [ ] Un error provocado en un manejador de eventos, en un hilo y en el arranque queda en `data/logs` con archivo, línea, función y traza.
- [ ] Sin datos privados en el registro (contraseñas, tokens, contenido del usuario).

## Compilación con cx_Freeze

- [ ] `setup_cx.py` con `include_msvcr: True`, `base="gui"` y `data` por lista blanca (`DATA_DISTRIBUIBLE`); `DATA_PROHIBIDO` sin cruces.
- [ ] Paquetes cargados de forma dinámica añadidos a `PAQUETES` (cx_Freeze no los detecta).
- [ ] `entorno.bat compilar` termina sin errores y `entorno.bat comprobar` arranca el `.exe` con `--comprobar-arranque` y código 0.
- [ ] `build/<App>/data` solo contiene lo distribuible; están `vcruntime140.dll`, `msvcp140.dll` y `lib/wx/locale/es/LC_MESSAGES/wxstd.mo`.
- [ ] Probado en un Windows sin Python ni el redistribuible de Visual C++ (si no se pudo, queda pendiente).

## Entorno (entorno.bat)

- [ ] Guardado con CRLF y UTF-8 sin BOM; `chcp 65001`; rutas entre comillas; `"%PY%" -m pip` en lugar de `activate`.
- [ ] Menú numerado y órdenes sin menú (`entorno.bat pruebas`) con código de salida.
- [ ] `VERSION_PY` con la versión del proyecto. Vacía usa la predeterminada del lanzador `py`, nunca `py -3` (que elige la más reciente instalada, quizá una preliminar sin ruedas de wxPython).

## Pruebas

- [ ] `pytest` pasa; las pruebas usan `APP_DATA_DIR` o `tmp_path` y nunca el `data` real.
- [ ] Las ventanas se crean sin mostrarse y se destruyen; nunca `ShowModal` sin parchear.
- [ ] Hay pruebas de: arranque, configuración (por defecto, lectura, escritura, archivo dañado), instancia única, menús en español con atajos y etiquetas previas de los controles.
- [ ] `ruff check` y `ruff format --check` sin avisos (tabulaciones).

## Documentación de usuario

- [ ] `data/docs/manual.md` (o `.html`): qué es, requisitos, primeros pasos, ventana principal, opciones, dónde guarda sus datos, qué hacer si algo falla.
- [ ] `data/docs/teclas.md`: todas las teclas, agrupadas por menú y por ventana, igual que en los menús.
- [ ] `data/docs/novedades.md`: una sección por versión, lo más reciente arriba, en lenguaje del usuario.
- [ ] La documentación de desarrollo (arquitectura, decisiones) va fuera de `data/docs`: no se distribuye.

## Fuentes

- cx_Freeze, opciones `include_msvcr` e `include_msvcr_version`: https://cx-freeze.readthedocs.io/en/stable/setup_script.html
- Microsoft, aceleradores de teclado en aplicaciones de Windows: https://learn.microsoft.com/en-us/windows/apps/design/input/keyboard-accelerators
