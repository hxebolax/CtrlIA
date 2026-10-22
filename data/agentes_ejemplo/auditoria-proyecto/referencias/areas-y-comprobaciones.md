# Áreas y comprobaciones

Lista de comprobación por área. Entre paréntesis, la regla del detector que ayuda (si la hay); lo demás es revisión manual. No hace falta citar cada punto en el informe: solo los que den hallazgos, y en «Qué se revisó» las áreas cubiertas.

## 1. Corrección

- Nombres sin definir, importaciones que faltan, atributos mal escritos: `ruff check --select F821,F822,F823,F811`. Especialmente en ramas poco usadas (manejo de errores, opciones raras), donde el `NameError` solo aparece cuando algo ya ha ido mal.
- La función de traducción `_` redefinida dentro de una función (`UnboundLocalError`), o usada como variable desechable (`for _ in ...`) en un módulo que traduce.
- Estado compartido entre hilos sin protección; controles modificados desde hilos (`hilo-toca-wx`); `wx.CallAfter` sobre una ventana ya destruida sin comprobar `if self:`.
- Cierres: ¿se detienen hilos, temporizadores, procesos hijos y conexiones al cerrar la ventana o la aplicación? ¿Se guarda el estado antes de destruir? ¿`EVT_CLOSE` llama a `Skip()` o a `Destroy()`?
- Diálogos modales que no se destruyen (`Destroy()` tras `ShowModal()`), fugas de ventanas ocultas.
- Comparaciones y lógica: `is` con literales, `==` con `None`, condiciones invertidas, índices fuera de rango con listas vacías.
- Migraciones de configuración o base de datos: ¿qué pasa con datos antiguos, campos que faltan, versiones futuras?

## 2. Seguridad

- Secretos en el código o en archivos que se distribuyen (`secreto-en-codigo`); secretos en los registros (¿se redactan tokens y cabeceras?).
- Órdenes del sistema con `shell=True`, `os.system`, `os.popen` (`subprocess-shell`); argumentos construidos con datos del usuario o de Internet.
- `eval`/`exec` (`eval-exec`); `pickle`, `shelve`, `marshal` sobre datos externos (`pickle-inseguro`); `yaml.load` sin `SafeLoader` (`yaml-inseguro`).
- TLS: `verify=False`, `CERT_NONE`, `check_hostname = False` (`verify-false`).
- SQL construido con cadenas (`sql-formateado`): valores siempre con marcadores `?`; nombres de tabla o columna solo desde una lista blanca.
- Rutas: nombres de archivo que vienen de fuera (descargas, adjuntos, respuestas de servidor) sin sanear (`..`, rutas absolutas, nombres reservados de Windows como `CON` o `NUL`); extracción de ZIP o tar sin validar cada miembro (`extraccion-insegura`).
- Descargas y actualizaciones: ¿se verifica el SHA-256 o la firma antes de ejecutar o reemplazar?
- Complementos o plugins: ¿qué código se carga y desde dónde?

## 3. Robustez

- Errores silenciados (`except-silencioso`, `except-amplio`): cada fallo debe quedar registrado con traza.
- Red: `timeout` en todas las peticiones (`requests-sin-timeout`), reintentos con espera, mensajes comprensibles sin conexión, respuestas inesperadas (HTML en vez de JSON, 429, 5xx).
- Recursos sin cerrar: archivos sin `with`, conexiones SQLite abiertas en hilos, procesos sin `wait()` o sin `timeout`.
- Escrituras atómicas de configuración (escribir en temporal y reemplazar) para no dejar archivos a medias si se corta.
- Entradas vacías, enormes, con Unicode, con rutas largas o con espacios.

## 4. Rendimiento

- Trabajo pesado en el hilo de la interfaz: red, disco, procesos, `time.sleep` en manejadores (`bloqueo-interfaz`); si el hilo principal se congela, el lector de pantalla enmudece.
- Importaciones pesadas al arrancar (bibliotecas grandes que solo se usan en una opción): impórtalas donde se usen.
- Algoritmos cuadráticos: búsquedas con `in` sobre listas dentro de bucles, `list.remove` o `insert(0, ...)` repetidos, consultas SQL dentro de bucles (N+1), repintar una lista entera por cada elemento añadido (usa `Freeze`/`Thaw` o una lista virtual).
- Listas grandes en `ListCtrl` normal en vez de virtual; temporizadores muy frecuentes.

## 5. Accesibilidad

- Etiquetas creadas después de sus controles o con otro padre (`etiqueta-tardia`); controles sin nombre accesible (compruébalo con una prueba MSAA si hay `comtypes`).
- `wx.CheckListBox` en wxPython anterior a 4.3 (`checklistbox`); `Grid`, `RichTextCtrl`, `HtmlWindow`, `wx.lib.agw`, `GenButton` (`control-poco-accesible`).
- `EVT_CHAR_HOOK` o `EVT_KEY_DOWN` que no llaman a `Skip()` para las teclas que no gestionan (`char-hook-sin-skip` cubre `EVT_CHAR_HOOK`; `EVT_KEY_DOWN` a mano).
- Voz duplicada: la aplicación anuncia lo que el lector ya lee al moverse por listas o cambiar el foco (`voz-duplicada`).
- Diálogos: botones en español con acelerador, botón predeterminado y de escape, foco inicial útil, textos largos en `TextCtrl` de solo lectura, foco devuelto al cerrar.
- Menú contextual con la tecla Aplicaciones y Mayús+F10 (`EVT_CONTEXT_MENU`, no solo clic derecho); atajos documentados.
- Progreso y errores anunciados sin saturar; información importante también consultable (no solo hablada).
- Colores fijos, tamaños en píxeles, alto contraste, escalado.
- Lo que no se puede verificar sin lector real queda como pendiente.

## 6. Portabilidad

- Datos fuera de la carpeta de la aplicación: `%APPDATA%`, `%LOCALAPPDATA%`, `%TEMP%`, carpeta del perfil (`ruta-no-portable`), registro de Windows (`registro-windows`).
- Rutas relativas al directorio de trabajo (`getcwd`) en lugar de a la carpeta del programa (`sys.executable` compilado, `main.py` desde código).
- Codificación: archivos de texto sin `encoding="utf-8"` (`open-sin-encoding`); nombres de archivo con acentos; salida de procesos externos decodificada con la página de códigos correcta.
- Separadores de ruta escritos a mano (`"\\"` o `"/"` concatenados) en vez de `pathlib`.
- Un único módulo de rutas que respete una variable de redirección para pruebas.

## 7. Empaquetado (cx_Freeze)

- `include_msvcr=True` (`cxfreeze-msvcr`); `optimize` 0 o 1, no 2 (`cxfreeze-optimize`).
- Distribución limpia: a `build/<App>/data` solo viaja lo imprescindible (binarios, sonidos, traducciones, manual, iconos, plugins sin `__pycache__`), nunca configuración, registros, bases de datos, sesiones ni claves. Comprueba el `include_files` y, si existe, la carpeta compilada.
- Se borra la compilación anterior antes de compilar.
- Paquetes con importaciones dinámicas incluidos (`packages`), recursos de wx (catálogo español) y de otras bibliotecas (certificados de `certifi`, DLL de voz).
- `base="gui"` y `sys.stdout`/`sys.stderr` que pueden ser `None`: un `print` o una biblioteca que escribe en consola puede romper la versión compilada.
- `multiprocessing.freeze_support()` si se usa `multiprocessing`.
- Versión única (`app/core/version.py`) coherente con `pyproject.toml` y el nombre del ZIP.

## 8. Registro

- `print` en la aplicación (`print-en-app`); `basicConfig` o manejadores configurados en módulos (`basicconfig-en-modulo`).
- Mensajes con f-strings, `format` o `%` (`log-formato-inmediato`).
- `log.error(str(e))` en lugar de `log.exception(...)` (se pierde la traza).
- Ganchos de excepciones no controladas (`sys.excepthook`, `threading.excepthook`), `faulthandler`, registro de la versión y del sistema al arrancar.
- Rotación que funcione en Windows (un solo escritor por archivo) y registros en `data/logs`.
- Privacidad: no se registran contraseñas, tokens ni el contenido que escribe o lee el usuario.

## 9. Mantenibilidad

- Archivos muy grandes (más de 1000-1500 líneas) y funciones muy largas: cuéntalos y señala los peores.
- Duplicación: el mismo bloque copiado en varios diálogos o servicios; lógica de negocio dentro de manejadores de eventos (difícil de probar).
- Código muerto: funciones sin uso, módulos que no se importan, opciones que ya no existen.
- Nombres engañosos, comentarios desactualizados, `TODO` antiguos.
- Pruebas: ¿existen para lo crítico? ¿pasan? ¿aíslan `data/` y la red?

## 10. Dependencias

- Versiones: ¿fijadas en `requirements.txt` o con rangos? ¿`requirements-dev.txt` separado?
- Dependencias sin uso o duplicadas; bibliotecas abandonadas.
- Compatibilidad con la versión de Python del proyecto y existencia de ruedas de wxPython y cx_Freeze para ella.
- Vulnerabilidades conocidas: `pip-audit` (opcional; necesita red y permiso).
- Licencias de lo que se distribuye (binarios auxiliares en `data/bin`, avisos de terceros).

## 11. Documentación

- Manual de usuario y lista de teclas actualizados con las funciones reales.
- Registro de cambios con la versión actual.
- Instrucciones para desarrollar: crear el entorno, ejecutar, pruebas, compilar.
- Documentos accesibles: encabezados, listas, sin tablas enormes ni arte ASCII.
