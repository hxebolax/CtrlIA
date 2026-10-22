# Estructura del proyecto y orden de arranque

## Estructura que genera el agente

- `main.py`: punto de entrada; orden de arranque y red de seguridad.
- `app/__init__.py`
- `app/core/` (sin interfaz, salvo el mutex de wx de la instancia única):
  - `paths.py`: rutas portables, todo en `data/` (`APP_DATA_DIR` para pruebas).
  - `version.py`: `NOMBRE_APLICACION`, `ID_APLICACION`, `APP_VERSION`.
  - `config.py`: JSON en `data/config`, valores por defecto en código, escritura atómica.
  - `registro.py`: registro básico, una sesión por archivo en `data/logs`, ganchos de excepciones.
  - `instancia_unica.py`: `wx.SingleInstanceChecker` y buzón en `data/temp/instancia`.
  - `voz.py`: anuncios opcionales con accessible_output2.
- `app/services/`: lógica sin wx (vacío al empezar).
- `app/ui/`:
  - `ventana_principal.py`
  - `bandeja.py`: `wx.adv.TaskBarIcon` con menú accesible.
  - `acerca_de.py`
  - `dialogo_texto.py`: textos largos en `TextCtrl` de solo lectura; manual y teclas.
  - `dialogo_opciones.py`
- `data/bin`, `data/sounds`, `data/locales`, `data/icons`, `data/plugins`: vacías con `.gitkeep`; se distribuyen.
- `data/docs`: `manual.md`, `teclas.md`, `novedades.md`; se distribuyen.
- `tests/`: pytest; nunca tocan el `data` real.
- `entorno.bat`: menú y órdenes (crear, instalar, ejecutar, depurar, pruebas, compilar, comprobar, limpiar, datos).
- `setup_cx.py`: cx_Freeze con `include_msvcr` y `data` por lista blanca.
- `pyproject.toml`: versión, `requires-python`, ruff con tabulaciones, pytest.
- `requirements.txt`, `requirements-dev.txt`, `.gitignore`.

Las carpetas generadas al usar la aplicación (`data/config`, `data/logs`, `data/temp`...) no existen en el esqueleto: se crean bajo demanda con `paths.subcarpeta()`. Así nunca se cuelan en el repositorio ni en la distribución.

## Por qué cada pieza

- **Un solo módulo de rutas.** Desde código la raíz es la carpeta de `main.py`; compilada, `Path(sys.executable).parent`. Nunca `os.getcwd()`: un acceso directo puede arrancar el programa con otra carpeta de trabajo.
- **`APP_DATA_DIR`.** Redirige `data` entera. La usan las pruebas (fixture automática en `tests/conftest.py`) y la comprobación de la versión compilada.
- **`ID_APLICACION` aparte del nombre visible.** El nombre visible puede llevar espacios y tildes («Gestor de notas»); el identificador (`GestorDeNotas`) se usa en el ejecutable, en el mutex de instancia única y en la carpeta de compilación.
- **Configuración mínima con valores por defecto en código.** Si falta el archivo se usan; si una versión nueva añade claves, se fusionan. Un JSON dañado se aparta como `config.danado-FECHA.json` y la aplicación arranca igual. Un valor de tipo erróneo se ignora con un aviso en el registro.
- **Registro básico propio.** Autocontenido (solo biblioteca estándar) para configurarlo antes que wx. Un archivo por sesión en lugar de rotación: en Windows, rotar un archivo que otro proceso tiene abierto falla con `WinError 32`, y una segunda instancia que arranca y sale también escribe en el registro. Si el proyecto crece, se sustituye por un sistema más completo que conserve `configurar_registro()` y `registrar_fallo_de_arranque()`.

## Orden de arranque de main.py y su porqué

1. **`sys.setswitchinterval(0.001)` al importar `main.py`.** El GIL cambia de hilo cada 5 ms por defecto. Con hilos de fondo haciendo trabajo de CPU (analizar HTML, importar SDK pesados), el hilo de la interfaz tarda en recuperar el GIL y el lector de pantalla, que consulta la interfaz en ese hilo, se queda mudo. Con 1 ms la interfaz responde enseguida. Es opcional; el coste es despreciable en escritorio. Bajar la prioridad de los hilos en Windows no sirve: no influye en cómo Python reparte el GIL.
2. **`multiprocessing.freeze_support()` lo primero de `principal()`.** En la versión compilada, un proceso hijo de `multiprocessing` arranca el mismo `.exe`. Si pasara de largo llegaría a la comprobación de instancia única, se creería una segunda instancia y saldría (o abriría otra ventana).
3. **Red de seguridad.** Todo `_principal()` va dentro de `try/except BaseException`: si algo falla, se escribe `data/logs/arranque_fallido.log` con la traza (aunque el registro normal no llegara a configurarse) y se muestra un cartel con `wx.MessageBox` o, si wx no está disponible, con `MessageBoxW` de user32. La versión compilada no tiene consola: sin esto, el ejecutable «se abre y desaparece».
4. **Registro antes que wx.** `registro.configurar_registro()` instala `sys.excepthook` (que en wxPython también recibe las excepciones de los manejadores de eventos), `threading.excepthook`, `faulthandler` (fallos nativos) y redirige `sys.stdout`/`sys.stderr` al registro si valen `None` (compilada con `base="gui"`: un `print` de una biblioteca rompería la aplicación).
5. **Configuración.** Se carga ya con el registro activo, para que un archivo dañado quede anotado.
6. **`wx.App(False)`.** `False` para que wx no redirija la salida a su propia ventana.
7. **`wx.HelpProvider.Set(wx.SimpleHelpProvider())`.** Sin proveedor, `SetHelpText` no guarda nada y Ctrl+H no tendría ayuda.
8. **`wx.Locale(wx.LANGUAGE_SPANISH)` guardando la referencia** (en `_VIVOS`). Traduce los botones estándar (Aceptar, Cancelar, Sí, No) y los atajos que wx muestra en los menús. Si el objeto se libera, vuelve el inglés. Se añade `wx/locale` con `AddCatalogLookupPathPrefix` para la versión compilada.
9. **Instancia única, antes de anunciar nada ni importar lo pesado.** La segunda instancia debe salir rápido y en silencio.
10. **Anuncio de arranque opcional por voz, SIN ventana de espera.** El lector de pantalla es otro proceso: el mensaje se oye aunque a continuación el hilo principal quede ocupado importando. Una ventana «Cargando, espere» visible mientras el hilo principal está bloqueado se marca como «no responde» y bloquea al lector al intentar leerla. Sin ventana, el lector sigue libre en el escritorio hasta que aparece la ventana principal ya lista.
11. **Importaciones pesadas y ventana principal.** Se importa `app.ui.ventana_principal` (y los servicios) solo ahora.
12. **Mostrar u ocultar en la bandeja.** Si la opción «iniciar en la bandeja» está activa pero no se pudo poner el icono, se muestra la ventana: nunca se deja al usuario sin acceso.
13. **`MainLoop()`** y, al volver, liberar la instancia y anotar «Cierre normal» (su ausencia en el registro delata un cierre anómalo).

## Instancia única en detalle

- **Nombre del mutex:** `ID_APLICACION` + usuario + huella SHA-1 de la carpeta `data`. Dos copias portables en carpetas distintas tienen datos distintos y pueden convivir; dos arranques de la misma copia, no. Solo letras, números, guiones y guiones bajos (un objeto con nombre del núcleo no admite barras invertidas).
- **Buzón en `data/temp/instancia`:** la primera instancia publica `instancia.json` con su PID y su HWND; la segunda deja órdenes `orden_*.json` con escritura atómica (temporal + `os.replace`). La primera las recoge con un `wx.Timer` cada 500 ms, descarta las caducadas (más de 60 s), las desconocidas y las ilegibles, y borra todo lo que lee.
- **Traer al frente con cuidado.** Windows solo deja llamar a `SetForegroundWindow` a quien cumple ciertas condiciones (ser el proceso en primer plano, haber recibido la última entrada del usuario...). La segunda instancia acaba de lanzarla el usuario, así que puede; con `AllowSetForegroundWindow(pid_de_la_primera)` le cede ese derecho. La primera, al recibir la orden, se restaura con su propio código (quitar el icono de la bandeja, enfocar la lista o el diálogo modal abierto) y llama a `SetForegroundWindow`. Si aun así Windows no la pone delante, `RequestUserAttention()` hace parpadear el botón de la barra de tareas. El permiso cedido se pierde si el usuario pulsa algo dirigido a otro proceso antes de que la primera lo use; por eso el temporizador es corto.
- **Lo que NO se hace:** simular la tecla Alt con `keybd_event` para saltarse el bloqueo. Funciona a menudo, pero envía una pulsación falsa que puede llegar al lector de pantalla o a la ventana que el usuario estaba usando.
- **ctypes con tipos declarados:** en 64 bits un HWND es un puntero; sin `argtypes`/`restype`, ctypes lo trataría como entero de 32 bits.
- **Argumentos:** si la segunda instancia recibe archivos por línea de órdenes, deja además una orden `abrir` con ellos; la primera los añade a su lista.

## Prueba de arranque sin ventana

`main.py --comprobar-arranque` recorre el arranque entero (registro, configuración, wx, idioma, instancia única, importaciones, ventana) sin mostrar la ventana y sale con código 0. Lo usan `tests/test_arranque.py` y `entorno.bat comprobar` sobre el `.exe` recién compilado, con `APP_DATA_DIR` apuntando fuera de la distribución.

## Fuentes

- Python, `sys.setswitchinterval`: https://docs.python.org/3/library/sys.html#sys.setswitchinterval
- Python, `multiprocessing.freeze_support`: https://docs.python.org/3/library/multiprocessing.html#multiprocessing.freeze_support
- wxPython, `wx.SingleInstanceChecker`: https://docs.wxpython.org/wx.SingleInstanceChecker.html
- Microsoft, `AllowSetForegroundWindow` (condiciones de primer plano y cuándo se pierde el permiso): https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-allowsetforegroundwindow
- Microsoft, `SetForegroundWindow`: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow
