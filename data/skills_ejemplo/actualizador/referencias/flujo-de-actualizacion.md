# Flujo de actualización

Este documento describe el recorrido completo, desde que la aplicación pregunta al servidor hasta que la versión nueva confirma que funciona, y el porqué de cada paso.

## Visión general

El flujo tiene tres actores:

1. **La aplicación en ejecución** (versión actual): comprueba, descarga, verifica, extrae y prepara el script. Después se cierra.
2. **El script de sustitución** (`data/temp/actualizacion/actualizar.bat`): espera al cierre, hace la copia de seguridad, copia la versión nueva, la lanza y vigila su salud.
3. **La versión nueva**: arranca con `--tras-actualizar` y, si todo va bien, escribe el marcador de salud.

## Paso a paso

### En la aplicación (función por función)

1. **`comprobar(config)`**
   - Descarga `version.json` con un tiempo límite de 15 s y un máximo de 256 KB. Por qué: un servidor lento o malicioso no debe colgar el hilo ni llenar la memoria.
   - **Verifica la firma antes de leer ningún campo.** Por qué: hasta ese momento el contenido no es de fiar; un campo manipulado podría, por ejemplo, apuntar a otra URL.
   - Valida los campos: versión con formato válido, nombre de archivo sin rutas, SHA-256 de 64 cifras hexadecimales, tamaño entero positivo y razonable, y el campo `app` (si existe) igual al nombre de la aplicación.
   - Compara con la versión instalada. Si no es mayor, no hay actualización.
   - **Anti-rollback:** si es menor que la última versión confirmada en este equipo, se rechaza con `ErrorRollback`.
   - En la comprobación periódica se respeta la versión que el usuario pidió omitir; en la manual, no.
2. **`descargar(config, info)`**
   - Descarga a `data/temp/actualizacion/descarga/<archivo>.parcial`. Por qué `data/temp`: es portable y no ensucia la carpeta del programa.
   - Si ya existe una parte, pide `Range: bytes=N-` y continúa. Si el servidor no admite rangos (responde 200) empieza de cero; si responde 416, borra la parte y reintenta.
   - Corta en cuanto se supera el tamaño del manifiesto firmado. Por qué: la firma solo se puede comprobar al final; sin este tope, un servidor comprometido podría enviar datos sin fin.
   - Cancelar conserva la parte descargada para continuar después.
3. **`verificar_paquete(ruta, info)`**: tamaño exacto y SHA-256 (comparación en tiempo constante). Si falla, el archivo se borra para que el siguiente intento empiece limpio.
4. **`extraer_seguro(zip, destino, config)`**
   - Valida todas las entradas antes de escribir ninguna.
   - Quita la carpeta raíz común (`MiAplicacion/`) que crea el empaquetador.
   - Reparte: el programa va a `nueva/programa`; los recursos de `data` permitidos van a `nueva/recursos/<subcarpeta>`; cualquier otra cosa de `data` se descarta con un aviso en el registro.
   - Exige que el ejecutable esté en la raíz del programa extraído. Por qué: evita instalar un ZIP de otra aplicación.
   - Borra el ZIP: ya no hace falta y ocupa espacio.
5. **`preparar_sustitucion(config, info)`**: comprueba que la carpeta de la aplicación no es la raíz de una unidad y contiene el ejecutable, y genera el `.bat` desde la plantilla del módulo (CRLF, UTF-8 sin BOM).
6. **`lanzar_sustitucion(ruta_bat)`**: lo lanza oculto (`CREATE_NO_WINDOW`), en otro grupo de procesos y, si se puede, fuera del Job Object de la aplicación, para que sobreviva a su cierre.
7. **La interfaz cierra la ventana principal** de forma normal (se guardan preferencias y trabajo).

### En el script de sustitución

1. **Comprobaciones iniciales:** el ejecutable existe en la instalación y en la versión nueva. Si no, no toca nada.
2. **Espera al cierre** de la aplicación por su PID (`tasklist`), hasta `ESPERA_CIERRE` segundos. Si no se cierra, **cancela** sin tocar nada: nunca mata una aplicación con trabajo sin guardar.
3. **Copia de seguridad** en `data/temp/version_anterior`:
   - `programa/`: los archivos del primer nivel y todas las carpetas salvo `data`.
   - `recursos/`: las subcarpetas de `DATA_ACTUALIZABLE`.
   - Si algo falla (robocopy devuelve 8 o más) o falta el ejecutable en la copia, no se toca la instalación y se relanza la versión actual.
4. **Copia de la versión nueva** con `robocopy /E /IS /IT` (sin `/MIR`: no borra nada). `/IS /IT` fuerzan la copia aunque tamaño y fecha coincidan. Los recursos se copian a sus subcarpetas de `data`.
5. **Lanzamiento** con PowerShell `Start-Process -PassThru`, que devuelve el PID (con `start` de cmd no se puede saber qué proceso vigilar ni cuál terminar).
6. **Vigilancia de la salud:** cada segundo mira si existe el marcador `data/temp/actualizacion/salud.txt` con el texto `ok <versión nueva>`. Si el proceso termina sin marcador, vuelve atrás en el acto; si se agota `ESPERA_SALUD`, termina el proceso (con su árbol) y vuelve atrás.
7. **Resultado:** escribe `resultado.txt` (`estado`, `motivo`, `version`, `anterior`), anota cada paso en `data/logs/actualizacion.log` y se borra a sí mismo.

### En la versión nueva

1. Arranca con `--tras-actualizar`.
2. `al_arrancar` lee `resultado.txt` si existe (para avisar de una vuelta atrás anterior).
3. Tras `RETRASO_CONFIRMACION` segundos (5 por defecto) con la ventana abierta, `confirmar_arranque` anota la versión como confirmada (anti-rollback) y escribe el marcador. Por qué esperar: demuestra que la ventana se abrió y el bucle de eventos funciona, no solo que `main.py` empezó a ejecutarse.

## Por qué un .bat y no un .py

- La aplicación compilada con cx_Freeze no incluye un `python.exe` utilizable: el intérprete va dentro del propio ejecutable, que es justo lo que hay que sustituir.
- `cmd.exe`, `robocopy`, `tasklist`, `taskkill` y `powershell` están en todos los Windows admitidos.
- El script vive en `data/temp`, fuera de lo que se copia, para no sobrescribirse mientras se ejecuta.

## Reglas del .bat (todas comprobadas por las pruebas)

- Todo el flujo con `goto`: dentro de bloques `( )` las variables `%x%` se expanden al leer el bloque y darían valores viejos.
- Esperas con `ping -n 2 127.0.0.1`: `timeout` falla cuando no hay consola (versión compilada sin ventana de consola).
- `find`, `findstr`, `robocopy`, `tasklist` con ruta completa de `System32`: si el PATH tiene utilidades de Git, `find` es otro programa.
- Los `%` de las rutas se duplican; las comillas simples se duplican dentro de PowerShell; las comillas dobles de los argumentos se construyen con `[char]34`.
- Los mensajes del registro no llevan rutas ni tildes: una `&` en una ruta partiría la orden `echo`.
- Se lanza como `.\actualizar.bat` con la carpeta del script como directorio de trabajo: con `NoDefaultCurrentDirectoryInExePath` definida, cmd no busca en la carpeta actual ([documentación de Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/processenv/nf-processenv-needcurrentdirectoryforexepathw)).
- Códigos de salida de robocopy: 0 a 7 es éxito; 8 o más, algún fallo ([documentación de Microsoft](https://learn.microsoft.com/en-us/troubleshoot/windows-server/backup-and-storage/return-codes-used-robocopy-utility)).

## Estados del resultado

| Estado | Significa | Qué ve el usuario |
|---|---|---|
| `correcta` | La versión nueva confirmó el arranque | Aviso por voz «Aplicación actualizada» |
| `revertida` | Se restauró la versión anterior | Mensaje al arrancar; la versión queda omitida |
| `no_aplicada` | No se tocó la instalación (no se cerró, falló la copia de seguridad o faltaba el ejecutable) | Mensaje al arrancar |
| `rota` | Falló también la restauración | La app no arranca; ver [vuelta-atras.md](vuelta-atras.md) |
