# Manual de MiAplicacion

## Qué es

Describa aquí en dos o tres frases para qué sirve la aplicación y a quién va dirigida.

## Requisitos

- Windows 10 u 11 de 64 bits.
- Funciona con NVDA, JAWS y Narrador. No necesita instalación: es portable.

## Primeros pasos

1. Descomprima el archivo ZIP en una carpeta donde tenga permiso de escritura (por ejemplo, en Documentos o en un pendrive). No la ponga en Archivos de programa.
2. Abra `MiAplicacion.exe`.
3. El foco empieza en la lista de elementos. Use Archivo, Abrir (Control+O) para añadir archivos.

## La ventana principal

- **Lista de elementos**: flechas para moverse, Intro para abrir el elemento, Suprimir para quitarlo de la lista, tecla Aplicaciones para el menú contextual.
- **Barra de estado**: muestra el resultado de la última acción. Se oculta desde Ver, Barra de estado.

## Opciones

En Herramientas, Opciones:

- **Anunciar el arranque por voz**.
- **Minimizar a la bandeja del sistema**: al minimizar, la ventana se oculta y queda un icono en la bandeja. Windows+B lleva a la bandeja.
- **Cerrar a la bandeja del sistema**: Alt+F4 oculta la ventana en lugar de salir. Para salir, use Archivo, Salir.
- **Iniciar oculta en la bandeja**.

Si la aplicación ya está abierta (aunque esté oculta en la bandeja), volver a abrirla trae su ventana al frente.

## Dónde guarda sus datos

Todo queda en la carpeta `data` junto al programa: configuración, registros y documentación. Para hacer una copia de seguridad basta con copiar la carpeta de la aplicación. Para desinstalarla, bórrela.

## Si algo falla

1. Use Herramientas, Abrir la carpeta de registros.
2. Envíe el archivo `sesion_...` más reciente (y `arranque_fallido.log` si existe) junto con la versión que aparece en Ayuda, Acerca de.
