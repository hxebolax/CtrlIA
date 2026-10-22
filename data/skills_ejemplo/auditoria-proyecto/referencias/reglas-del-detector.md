# Reglas del detector

El detector (`tools/auditoria_rapida.py`) lee el código con `ast`: no lo ejecuta ni lo importa. Por eso conoce la estructura (llamadas, argumentos, excepciones, clases) pero no los valores en tiempo de ejecución. Cada regla tiene una severidad orientativa y algunas son heurísticas (salen marcadas «revisar»).

Reglas «solo app»: se aplican al paquete de la aplicación (`app/` o lo indicado con `--carpeta-app`) y a `main.py`, no a `tools/` ni a `setup_cx.py`.

## Exclusiones

- **En la línea:** `# auditoria: ignorar getcwd - la herramienta se lanza siempre desde la raíz`. Varias reglas separadas por comas; sin reglas, se ignoran todas. Un comentario en su propia línea se aplica a la línea siguiente. Escribe siempre el motivo después de ` - `.
- **Por archivo** (`.auditoria-ignorar` en la raíz, o `--exclusiones`): una línea por patrón de ruta con `/`, al estilo glob, opcionalmente seguido de `:` y las reglas. `#` inicia un comentario.

```
# Generado automáticamente, no se edita a mano
app/recursos_generados/*
# Herramienta de consola: el print es su salida
app/consola.py: print-en-app
```

- Los `except` con `# noqa: BLE001`, `S110`, `S112` o `E722` en su línea se consideran decisiones documentadas y no se marcan.

## Robustez

### `except-silencioso`

- **Detecta:** un `except` cuyo cuerpo es solo `pass`, `...`, `continue` o una cadena. Severidad media si captura `Exception`, `BaseException` o todo; baja si captura algo concreto.
- **Por qué importa:** el error desaparece sin rastro. Cuando algo falla después, no hay nada en los registros que diga dónde empezó.
- **Falsos positivos conocidos:** no se marcan las excepciones que se ignoran por costumbre y no esconden fallos (`FileNotFoundError`, `KeyError`, `ValueError`, `IndexError`, `ImportError`, `queue.Empty`, `StopIteration`...). Una limpieza que tolera `OSError` al borrar un temporal puede ser correcta: añade el `log.debug`.
- **Cómo corregir:** `log.debug("No se pudo borrar %s", ruta, exc_info=True)` como mínimo, o capturar una excepción más concreta.

### `except-amplio`

- **Detecta:** `except:`, `except Exception` o `except BaseException` que no relanza, no registra (ninguna llamada a `log.*`, `logging`, `traceback`, `exc_info`...) y no usa la variable de la excepción.
- **Por qué importa:** atrapa también los errores de programación (`NameError`, `AttributeError`, `TypeError`) y los convierte en un valor por defecto silencioso. Así se esconden fallos durante meses.
- **Falsos positivos conocidos:** una función de «mejor esfuerzo» que devuelve un valor por defecto a propósito. Aun así, merece un `log.debug(..., exc_info=True)`. Si la llamada que informa al usuario también registra, pero no recibe la excepción, se marca igualmente.
- **Cómo corregir:** `log.exception("No se pudo ...")` y el valor por defecto, o capturar las excepciones esperables (`OSError`, `requests.RequestException`, `json.JSONDecodeError`).

### `requests-sin-timeout`

- **Detecta:** `requests.get/post/...` y `urllib.request.urlopen` sin `timeout`, y métodos de un objeto llamado `session`/`sesion` en archivos que importan `requests`. No marca las llamadas con `**opciones`.
- **Por qué importa:** requests no tiene tiempo límite por defecto: una conexión colgada bloquea el hilo para siempre (y la interfaz, si es el principal).
- **Falsos positivos conocidos:** una sesión con un adaptador propio que impone el tiempo límite; en ese caso, exclúyelo con un comentario que lo diga.
- **Cómo corregir:** `timeout=(10, 60)` (conexión, lectura) o una constante del proyecto.

## Registro

### `print-en-app`

- **Detecta:** `print()` en el código de la aplicación, fuera de `if __name__ == "__main__":`. No se marca en módulos de línea de órdenes (`cli*.py`, `__main__.py` o que importan `argparse`).
- **Por qué importa:** no queda en los registros y, en la versión compilada con `base="gui"`, `sys.stdout` puede ser `None`.
- **Cómo corregir:** `log = logging.getLogger(__name__)` y `log.info(...)`.

### `log-formato-inmediato`

- **Detecta:** llamadas a `log.info/debug/warning/error/exception/critical/log` sobre un objeto llamado `log`, `logger`, `_log`, `registro`... (o `logging.getLogger(...)`) cuyo mensaje es una f-string con valores, `.format()`, `%` o una concatenación.
- **Por qué importa:** se formatea aunque el nivel esté desactivado, rompe la agrupación de mensajes repetidos y, si un valor falla al convertirse a texto, la excepción salta en la línea del registro.
- **Cómo corregir:** `log.info("Descargados %s archivos de %s", n, origen)`.

### `basicconfig-en-modulo`

- **Detecta:** `logging.basicConfig` en un módulo de la aplicación que no es `main.py` ni está bajo `if __name__ == "__main__":`.
- **Por qué importa:** el primero que lo llama decide la configuración de todo el programa; el resto se ignora sin avisar.
- **Cómo corregir:** configurar una sola vez en el punto de entrada.

## Seguridad

### `subprocess-shell`

- **Detecta:** `subprocess.run/call/check_call/check_output/Popen` con `shell=True`, y `os.system`, `os.popen`, `subprocess.getoutput`, `asyncio.create_subprocess_shell`. Alta si la orden se construye en tiempo de ejecución; media si es un texto fijo.
- **Por qué importa:** un nombre de archivo con `&`, `|` o comillas ejecuta órdenes arbitrarias; además, las comillas y los espacios se rompen con facilidad.
- **Cómo corregir:** lista de argumentos sin `shell`: `subprocess.run([exe, "-i", entrada], check=True, timeout=600)`. Para abrir un archivo con su programa, `os.startfile`.

### `eval-exec`

- **Detecta:** llamadas a `eval()` y `exec()` (no a métodos como `cursor.execute` o `dialogo.exec`).
- **Por qué importa:** ejecutan cualquier código contenido en el texto.
- **Cómo corregir:** `ast.literal_eval` o `json.loads` para datos; un diccionario de funciones permitidas para elegir acciones.

### `pickle-inseguro`

- **Detecta:** `pickle.load/loads/Unpickler`, `dill`, `joblib.load`, `shelve.open`, `marshal.load`. Heurística.
- **Por qué importa:** deserializar un pickle manipulado ejecuta código (lo advierte la documentación de Python).
- **Falsos positivos conocidos:** una caché propia que nunca sale del equipo y que nadie más escribe.
- **Cómo corregir:** JSON para todo lo que pueda venir de fuera (importaciones, descargas, archivos compartidos).

### `yaml-inseguro`

- **Detecta:** `yaml.load` o `load_all` sin un `Loader` que contenga `Safe`, y `yaml.unsafe_load`.
- **Por qué importa:** los cargadores completos pueden construir objetos de Python arbitrarios.
- **Cómo corregir:** `yaml.safe_load(texto)`.

### `verify-false`

- **Detecta:** `verify=False`, `x.verify = False`, `check_hostname = False`, `ssl.CERT_NONE`, `ssl._create_unverified_context()` y, con severidad baja, `urllib3.disable_warnings()`.
- **Por qué importa:** cualquiera en la misma red puede suplantar al servidor y leer o alterar el tráfico (credenciales incluidas).
- **Falsos positivos conocidos:** una opción avanzada que el usuario activa conscientemente (servidor propio con certificado autofirmado). Sigue siendo un hallazgo: al menos, que sea explícita, esté desactivada por defecto y se avise.
- **Cómo corregir:** `certifi` o `verify="ruta/ca.pem"` con la CA del servidor.

### `secreto-en-codigo`

- **Detecta:** cadenas con formato de token conocido (GitHub, `sk-...`, AWS, Slack, Google, bot de Telegram, clave privada PEM, JWT), con severidad alta; y variables, argumentos o claves de diccionario llamados `password`, `token`, `api_key`, `secret`, `clave_api`... con un valor literal largo, sin espacios, con letras y dígitos, con severidad media. Ignora docstrings, marcadores (`tu_token`, `xxx`, `<...>`) y nombres de campo (`"access_token"`). Heurística.
- **Por qué importa:** lo que está en el código acaba en el repositorio, en la compilación y en los ZIP distribuidos.
- **Falsos positivos conocidos:** identificadores públicos que el servicio exige y que no son secretos (el propio servicio los publica). Documéntalo con una exclusión en la línea.
- **Cómo corregir:** mover a la configuración del usuario o a un almacén cifrado; si llegó a publicarse, revocarlo.

### `sql-formateado`

- **Detecta:** `execute`, `executemany` o `executescript` con SQL construido con f-string, `%`, `.format()` o `+` (si el texto contiene SQL reconocible). No marca los valores seguros: constantes en mayúsculas, marcadores `?` generados (también en una variable de la misma función: `marcadores = ",".join("?" * n)`), `int()`, `len()` o aritmética con números. Severidad según dónde va el valor: **alta** si queda entre comillas (`'{nombre}'`), **media** en posición de valor (`= {x}`, `LIMIT {n}`), **baja** si es un nombre de tabla o columna. Heurística.
- **Por qué importa:** inyección SQL y errores con comillas en los datos.
- **Cómo corregir:** `cursor.execute("... WHERE nombre = ?", (nombre,))`. Los nombres de tabla o columna no admiten marcadores: valídalos contra una lista blanca.

### `extraccion-insegura`

- **Detecta:** `extractall`/`extract` sin `filter=` en módulos que importan `tarfile`, y `shutil.unpack_archive` sin `filter`. Heurística.
- **Por qué importa:** un tar puede contener rutas `../` o enlaces que escriben fuera de la carpeta destino. `zipfile` sí limpia las rutas al extraer (quita las absolutas y los `..`), por eso no se marca; si el código construye rutas a mano con los nombres del ZIP, revísalo igualmente.
- **Cómo corregir:** en Python 3.12 o superior, `extractall(destino, filter="data")`; antes, comprobar que cada miembro resuelto queda dentro del destino.

## Portabilidad

### `ruta-no-portable`

- **Detecta (solo app):** `os.getenv`/`os.environ` de `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, `PROGRAMDATA`; `tempfile.gettempdir()`; `tempfile.mkdtemp/mkstemp/NamedTemporaryFile/TemporaryDirectory...` sin `dir=` (baja); `os.path.expandvars("%APPDATA%...")`; `platformdirs`/`appdirs`; `wx.StandardPaths` de carpetas del usuario. `LOCALAPPDATA` y `PROGRAMDATA` salen con severidad baja porque se usan a menudo para **localizar** programas instalados, no para guardar datos.
- **Por qué importa:** la aplicación es portable: todo lo que genera vive en `data/`. Lo que va a otra carpeta se pierde al mover la aplicación y deja basura al desinstalar.
- **Falsos positivos conocidos:** localizar un programa externo (un navegador, una herramienta del sistema). Exclúyelo en la línea con el motivo.
- **Cómo corregir:** el módulo de rutas del proyecto (`data/config`, `data/temp`, `data/cache`); `tempfile.mkdtemp(dir=ruta_temporales())`.

### `getcwd`

- **Detecta (solo app):** `os.getcwd()`, `Path.cwd()` y `os.chdir()`.
- **Por qué importa:** el directorio de trabajo depende de cómo se lanzó el programa (acceso directo, otra carpeta, el explorador), no de dónde está instalado.
- **Cómo corregir:** rutas desde la carpeta del programa (`Path(sys.executable).parent` compilado, carpeta de `main.py` desde código).

### `registro-windows`

- **Detecta (solo app):** escrituras en el registro (`winreg.CreateKey`, `CreateKeyEx`, `SetValue`, `SetValueEx`). Un aviso por archivo con el número de escrituras. Heurística.
- **Por qué importa:** la configuración en el registro no viaja con la aplicación portable ni se borra al eliminar su carpeta.
- **Falsos positivos conocidos:** funciones que el usuario pide expresamente y que solo existen en el registro (iniciar con Windows, asociar un protocolo o un tipo de archivo). Comprueba que son opcionales, reversibles y que se deshacen al desactivarlas.

### `open-sin-encoding`

- **Detecta:** `open()` en modo texto (sin `b` en el modo) sin `encoding`, y `read_text()`/`write_text()` sin `encoding`. No marca un modo que no es un texto fijo.
- **Por qué importa:** en Windows, sin `encoding` se usa la página de códigos del sistema (cp1252 en España): un archivo UTF-8 con acentos falla al leerse o se escribe corrupto. Python 3.15 cambia el valor por defecto a UTF-8 (PEP 686), pero el proyecto puede usar versiones anteriores.
- **Cómo corregir:** `encoding="utf-8"` (o `"utf-8-sig"` para leer archivos que pueden traer BOM).

## wx: hilos, interfaz y accesibilidad

### `hilo-toca-wx`

- **Detecta:** funciones que se lanzan en un hilo (`threading.Thread(target=...)`, `threading.Timer`, `executor.submit(...)`, subclases de `Thread` con `run`) y que llaman directamente a métodos de controles (`SetValue`, `SetLabel`, `Append`, `DeleteAllItems`, `Enable`, `Show`, `Refresh`...), a `wx.MessageBox` o a un método de la misma clase que lo hace. Lo que va dentro de `wx.CallAfter`, `wx.CallLater`, `wx.PostEvent` o `QueueEvent` no se marca. Solo resuelve funciones del mismo archivo. Heurística.
- **Por qué importa:** wx no es seguro entre hilos: cierres inesperados, controles corruptos o bloqueos que aparecen de vez en cuando.
- **Falsos positivos conocidos:** objetos no wx con métodos del mismo nombre (`Set`, `Append`, `Clear` en otra biblioteca).
- **Cómo corregir:** `wx.CallAfter(self.lista.Append, datos)` o un método que actualiza la interfaz invocado con `wx.CallAfter`.

### `bloqueo-interfaz`

- **Detecta:** en manejadores enlazados con `Bind` en el mismo archivo, llamadas a `time.sleep`, `requests.*`, `urlopen`, `subprocess.run/call/check_*` u `os.system` fuera de un hilo. Heurística.
- **Por qué importa:** mientras el manejador espera, la ventana no responde y el lector de pantalla no anuncia nada.
- **Falsos positivos conocidos:** una orden muy rápida (abrir el explorador) o una espera de milisegundos.
- **Cómo corregir:** hilo de trabajo y resultado con `wx.CallAfter`; progreso accesible para las operaciones largas.

### `voz-duplicada`

- **Detecta:** llamadas a funciones de voz (`speak`, `hablar`, `anunciar`, `decir`, `output`, `braille`...) en manejadores de `EVT_LIST_ITEM_SELECTED`, `EVT_LIST_ITEM_FOCUSED`, `EVT_SET_FOCUS`, `EVT_LISTBOX`, `EVT_TREE_SEL_CHANGED` o `EVT_CHILD_FOCUS`. Heurística.
- **Por qué importa:** el lector ya lee el elemento al moverse; la aplicación lo repite, se solapa y ralentiza la navegación.
- **Falsos positivos conocidos:** información que el lector no puede saber (una lectura por columnas pedida por el usuario, un estado externo).
- **Cómo corregir:** anunciar solo lo que el lector no lee por sí mismo, con una preferencia para desactivarlo.

### `checklistbox`

- **Detecta:** `wx.CheckListBox(...)` creado directamente (no las subclases).
- **Por qué importa:** en wxPython anterior a 4.3, los lectores no anuncian el estado marcado de cada elemento.
- **Falsos positivos conocidos:** proyectos con wxPython 4.3 o posterior, que lo expone de serie; o una subclase accesible del proyecto definida en otro archivo y usada con otro nombre (esa no se marca).
- **Cómo corregir:** subclase con `wx.Accessible` que exponga rol de casilla y estado, o `wx.ListCtrl` con `EnableCheckBoxes(True)`.

### `control-poco-accesible`

- **Detecta:** instancias o subclases de `wx.grid.Grid`, `wx.richtext.RichTextCtrl`, `wx.html.HtmlWindow`, `wx.stc.StyledTextCtrl`, `wx.dataview.DataViewCtrl`/`TreeListCtrl`, `CheckListCtrlMixin`, todo `wx.lib.agw` y los botones genéricos `wx.lib.buttons.Gen*`. Heurística.
- **Por qué importa:** son controles dibujados por wx o con soporte parcial de MSAA: el lector puede no leer su contenido, su estado o su foco.
- **Falsos positivos conocidos:** un uso decorativo o con alternativa accesible (por ejemplo, un `HtmlWindow` de respaldo cuando falla el navegador integrado, con el texto también disponible).
- **Cómo corregir:** control nativo equivalente (`ListCtrl`, `TextCtrl` de solo lectura, `Button`) o `wx.Accessible` con pruebas MSAA y con lector real.

### `etiqueta-tardia`

- **Detecta:** en un mismo bloque de código, un control que necesita etiqueta (`TextCtrl`, `Choice`, `ComboBox`, `ListBox`, `ListCtrl`, `TreeCtrl`, `Slider`, `SpinCtrl`, `Gauge`, selectores...) sin `StaticText` justo antes con el mismo padre, y con un `StaticText` justo después cuyo texto termina en `:` o lleva `&`, que no va seguido de otra ventana. Heurística.
- **Por qué importa:** Windows toma el nombre del control de la etiqueta creada justo antes (orden de creación, no orden del sizer). Con la etiqueta después, el lector dice solo «editar» o «lista».
- **Falsos positivos conocidos:** controles con nombre dado por `wx.Accessible`; creación repartida entre funciones o bucles (el detector no la sigue).
- **Cómo corregir:** crear la `StaticText` justo antes del control, con el mismo padre. `MoveBeforeInTabOrder` no lo arregla.

### `char-hook-sin-skip`

- **Detecta:** manejadores de `EVT_CHAR_HOOK` en los que una tecla **no gestionada** puede llegar al final sin `Skip()`. Solo sigue el camino de «ninguna condición se cumplió» (los `else` explícitos o implícitos): no saltar las teclas gestionadas es correcto, y un `return` se interpreta como tecla gestionada. Pasar el evento a otra función cuenta como delegar.
- **Por qué importa:** `EVT_CHAR_HOOK` recibe todas las teclas antes que los controles; si no se saltan, Tab, las flechas y la escritura dejan de funcionar en toda la ventana.
- **Falsos positivos conocidos:** un `Skip()` dentro de un bucle (el detector no lo cuenta) o en una función auxiliar que no recibe el evento.
- **Cómo corregir:** `else: evento.Skip()` al final de la cadena de condiciones, o `evento.Skip()` como última línea con `return` en cada tecla gestionada.

## Empaquetado

### `cxfreeze-optimize`

- **Detecta:** `optimize` con valor 2 en archivos que importan `cx_Freeze`.
- **Por qué importa:** con 2 se eliminan las docstrings; algunas bibliotecas las leen al importarse y fallan solo en la versión compilada.
- **Cómo corregir:** `optimize=1`.

### `cxfreeze-msvcr`

- **Detecta:** `setup()` de `cx_Freeze` en un archivo donde no aparece `include_msvcr` con un valor verdadero. No revisa `[tool.cxfreeze]` en `pyproject.toml`.
- **Por qué importa:** sin los runtimes de Visual C++ junto al `.exe`, la aplicación no arranca en equipos que no los tengan instalados.
- **Cómo corregir:** `"include_msvcr": True` en las opciones de `build_exe`.

## Otras

### `sintaxis`

- **Detecta:** archivos que no se pueden analizar (error de sintaxis o de sangría).
- **Por qué importa:** el módulo no se puede importar; si está en una rama poco usada, el fallo aparece tarde.

## Añadir una regla

1. Entrada en `REGLAS` (nombre en minúsculas con guiones, severidad, área, descripción, consejo, `solo_app`, `heuristica`).
2. Función `regla_...(archivo)` que llama a `archivo.anotar(...)`, añadida a `FUNCIONES_REGLA`.
3. Pruebas con un caso positivo y uno negativo parecido en `tests/test_auditoria_rapida.py`.
4. Apartado en este archivo (la prueba `test_todas_las_reglas_documentadas` lo exige dentro del agente).
5. Ejecutarla sobre un proyecto real en modo solo lectura y revisar una muestra de avisos antes de darla por buena.

## Fuentes

- Seguridad de `subprocess`: https://docs.python.org/3/library/subprocess.html#security-considerations
- Advertencia de `pickle`: https://docs.python.org/3/library/pickle.html
- Filtros de extracción de `tarfile` (PEP 706): https://docs.python.org/3/library/tarfile.html#extraction-filters
- Marcadores en `sqlite3`: https://docs.python.org/3/library/sqlite3.html#sqlite3-placeholders
- UTF-8 por defecto (PEP 686): https://peps.python.org/pep-0686/
- Tiempos límite en requests: https://requests.readthedocs.io/en/latest/user/advanced/#timeouts
- `yaml.load` inseguro: https://github.com/yaml/pyyaml/wiki/PyYAML-yaml.load(input)-Deprecation
- Reglas de ruff: https://docs.astral.sh/ruff/rules/
- pip-audit: https://pypi.org/project/pip-audit/
