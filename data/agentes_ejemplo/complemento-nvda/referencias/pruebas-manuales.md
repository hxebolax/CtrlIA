# Pruebas: automáticas y con NVDA real

## Qué prueban las pruebas automáticas

Están en `plantillas/tests/` y se ejecutan con pytest en el Python del proyecto (con wxPython para las que crean ventanas):

- **Constructor** (`test_construir_complemento.py`): genera el `.nvda-addon` en una carpeta temporal desde una copia de la fuente ensuciada con `__pycache__`, `.pyc` y `Thumbs.db`; comprueba el manifiesto (campos, formatos, marcadores, líneas opcionales), el `.mo` (se carga con `gettext` y traduce), el `readme.html`, que no viajan cachés ni `.po`, que la fuente queda intacta y la línea de órdenes.
- **Código del complemento** (`test_codigo_complemento.py`): `py_compile` de cada módulo, importaciones que existen dentro de NVDA (contra el `library.zip` del NVDA instalado, si lo hay), traducción de cada `_("...")`, y la lógica con una NVDA simulada (`nvda_falso.py`): clase superpuesta, gesto, movimiento silencioso con una **propiedad de ventana real** y lectura de una celda de 3000 caracteres de una **lista virtual real de wxPython** con la misma técnica que usará NVDA.
- **Instalación** (`test_instalar_complemento.py`): con `os.startfile` sustituido (una llamada no prevista hace fallar la prueba): diálogo con botones en español y «Cancelar» por defecto, apertura solo tras aceptar, errores de asociación, versión instalada y del paquete, enumeración real de procesos.
- **Señales** (`test_senales_nvda.py`): poner, leer y quitar la propiedad en un `wx.ListCtrl` real.

Lo que NO prueban: que NVDA cargue el complemento, que el evento de foco se calle de verdad, que la voz y el braille digan lo esperado. Eso solo se sabe con NVDA real. **Nunca digas «probado con NVDA» por haber pasado estas pruebas.**

## Preparación para la prueba manual

No instales en el NVDA de uso diario de la persona sin su permiso. Opciones, de más a menos aislada:

1. Máquina virtual o equipo de pruebas con NVDA.
2. Copia portátil de NVDA solo para pruebas.
3. Carpeta scratchpad del NVDA de la persona (Opciones, Avanzado), con su permiso: copia `appModules/miaplicacion.py`; NVDA+Control+F3 para recargar. Las traducciones no se cargan desde el scratchpad (sale en inglés).
4. Instalar el `.nvda-addon` con el botón de la app (flujo real completo), con su permiso.

Antes de empezar: nivel de registro «Depuración» y Visor de voz abierto.

## Protocolo

Para cada punto, anota: versión de NVDA, versión del complemento, qué se hizo, qué dijo NVDA (copiado del Visor de voz) y qué se esperaba.

### Carga

1. Abre la aplicación y pulsa NVDA+Control+F1: debe decir el ejecutable y el módulo cargado con el nombre del archivo del appModule. Si dice otro, el archivo está mal nombrado.
2. En el visor del registro, sin errores del complemento al arrancar NVDA ni al enfocar la aplicación.
3. Menú de NVDA, Herramientas, Tienda de complementos, «Complementos instalados»: aparece con su resumen en español y la versión correcta.

### Celdas completas

1. En una lista con un elemento de más de 511 caracteres en una columna, flecha abajo hasta él: NVDA lee el texto entero (compara el final con el de la aplicación).
2. Control+Alt+flechas (navegación por celdas de NVDA): cada celda completa.
3. Desactiva el complemento y repite: debe cortarse en 511 (demuestra que el efecto es del complemento).
4. Rendimiento: flecha rápida por una lista de cientos de filas. Sin retrasos apreciables ni «NVDA no responde» en el registro.

### Movimiento silencioso

1. Sitúate en un elemento de una lista virtual y provoca la llegada de filas nuevas (o el cambio automático que haga la app). NVDA no debe decir nada; en el Visor de voz, ninguna línea nueva.
2. Justo después, flecha abajo: NVDA anuncia el siguiente elemento con normalidad.
3. Con el complemento desactivado, el mismo cambio se anuncia (demuestra el efecto).
4. Si la app deja de quitar la marca (prueba forzada), a los 500 ms la navegación vuelve a sonar.

### Gesto

1. En la lista, NVDA+Mayúsculas+J: lee todas las columnas con su cabecera, en español.
2. Dos veces seguidas: se abre una ventana con la fila; se recorre con flechas y se cierra con Escape.
3. Fuera de una lista: «No estás en una lista».
4. Menú de NVDA, Preferencias, Gestos de entrada: aparece en la categoría de la app con su descripción y se puede reasignar.
5. NVDA+1 (ayuda de entrada) y NVDA+Mayúsculas+J: dice la descripción del gesto.

### Instalación desde la aplicación

1. Pulsa el botón: el diálogo explica qué hará, con «Cancelar» por defecto. Escape y Cancelar no abren nada.
2. «Abrir el instalador»: aparece el diálogo de NVDA. Cancelarlo no instala nada.
3. Aceptar: NVDA instala y pide reiniciar. Tras reiniciar, el complemento funciona.
4. Repetir con la misma versión y con una nueva: NVDA informa de actualización.
5. Con NVDA cerrado: el diálogo lo advierte.

### Braille

Con una línea braille (o el Visor braille de NVDA, en Herramientas): el texto de `ui.message` y de las celdas completas aparece y se puede desplazar.

## Cómo informar

Separa: lo verificado con NVDA real (versión exacta), lo verificado solo con pruebas automáticas y lo pendiente. Si algo falla, adjunta el fragmento del registro de NVDA con la traza.
