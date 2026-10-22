# Diagnóstico y soporte

## Aviso accesible de errores (`app/ui/dialogo_error.py`)

Tras crear `wx.App`: `dialogo_error.instalar(nombre_app="MiAplicacion", anunciar=funcion_de_voz)`.

- Se muestra en el hilo de la interfaz, venga el error de donde venga.
- Explica en lenguaje claro qué pasó y que se ha guardado en el registro; el texto está en un campo de solo lectura que el lector recorre y copia.
- Botones «&Copiar detalles» (tipo, mensaje y origen, redactados), «Abrir la carpeta de &registros» y «&Aceptar» (predeterminado y escape).
- Nunca se apilan diálogos: si hay más errores mientras está abierto, al cerrarlo se anuncia cuántos más hubo.
- Devuelve el foco a donde estaba el usuario.
- `anunciar` es opcional: la función de voz del proyecto, para confirmar «Detalles copiados».

## Vigilante de congelaciones (`app/core/vigilante_ui.py`)

`vigilante_ui.iniciar()` tras crear `wx.App`; `vigilante_ui.detener()` al salir.

- Cada 2 s envía un `wx.CallAfter` y mide cuánto tarda en atenderse.
- Si pasan 3 s sin respuesta, **mientras sigue bloqueado** vuelca la pila del hilo principal (la última línea es la función atascada) y lo que hacía cada otro hilo.
- Al recuperarse anota cuánto duró el bloqueo (WARNING); los retrasos de más de 0,75 s quedan en DEBUG.
- Un hilo de interfaz bloqueado deja la ventana como «No responde» y **al lector de pantalla mudo**: este registro es la prueba para encontrar al culpable (importaciones pesadas, E/S en el hilo principal, bucles largos, cerrojos).
- Alternativa que funciona aunque el hilo retenga el GIL: rearmar `faulthandler.dump_traceback_later(15, file=archivo)` desde un temporizador de wx.

## Paquete de diagnóstico (`app/core/diagnostico.py`)

`ruta = diagnostico.generar_paquete(nombre_app, version)` crea `data/diagnostico/diagnostico_AAAAMMDD_HHMMSS.zip` con:

- las últimas sesiones de registro, `incidencias.log`, `fallo_nativo.log` y `arranque_fallido.log`, redactados de nuevo;
- `sistema.txt`: versión de la aplicación, si está compilada, Python, wxPython, Windows, lectores de pantalla en ejecución y paquetes instalados.

Nunca incluye configuración, bases de datos, sesiones ni claves. Ofrécelo desde el menú Ayuda («Generar informe de diagnóstico...») y abre después la carpeta, o copia la ruta al portapapeles, anunciándolo.

## Visor de registros dentro de la aplicación

- `wx.TextCtrl` multilínea de solo lectura con etiqueta (accesible y copiable), o `ListaAccesible` con columnas Hora, Nivel, Origen y Mensaje.
- Carga las últimas N líneas **abriendo, leyendo y cerrando** el archivo; nunca lo dejes abierto (bloquearía la rotación).
- Filtro por nivel (Todos / Avisos y errores / Solo errores) y búsqueda.
- Atajos: F5 recargar, Ctrl+C copiar la entrada, Intro ver la entrada completa con su traza.
- Opción para abrir la carpeta de registros en el Explorador.

## Preferencias relacionadas

- Nivel de registro (Normal = INFO, Detallado = DEBUG) aplicado en caliente con `registro.cambiar_nivel`.
- Número de sesiones que se conservan.
- Un «modo diagnóstico» temporal que sube a DEBUG hasta el próximo reinicio, útil para reproducir un fallo que ha contado alguien.
