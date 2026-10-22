# Manual de MiAplicacion

Versión 1.0.0. Este manual explica cómo instalar y usar MiAplicacion. Está pensado para leerse con un lector de pantalla: cada apartado es un encabezado y las instrucciones se dan con el teclado.

Para abrir este manual desde la aplicación, pulse F1. Para ver todas las combinaciones de teclas, abra el menú Ayuda y elija «Teclas de la aplicación».

## Introducción

MiAplicacion sirve para (describa aquí en una o dos frases qué problema resuelve y para quién).

Qué puede hacer con ella:

- (Función principal 1.)
- (Función principal 2.)
- (Función principal 3.)

## Requisitos

- Windows 10 o Windows 11 de 64 bits.
- Un lector de pantalla, si lo usa: NVDA, JAWS o Narrador.
- (Conexión a Internet, si alguna función la necesita. Diga cuál.)

No necesita instalar Python ni ningún otro programa.

## Instalación portable

MiAplicacion es portable: no se instala en el sistema ni escribe en el registro de Windows. Todo lo que guarda está dentro de su carpeta.

1. Descargue el archivo ZIP de la versión más reciente.
2. Extraiga el ZIP en la carpeta que prefiera, por ejemplo en Documentos o en una memoria USB. Con el Explorador: seleccione el archivo, pulse la tecla Aplicaciones y elija «Extraer todo».
3. Entre en la carpeta extraída y pulse Intro sobre MiAplicacion.exe.

Para actualizar, extraiga la versión nueva encima de la anterior. Su configuración se conserva porque está en la carpeta data, que el ZIP no sobrescribe.

Para desinstalar, borre la carpeta de la aplicación.

## Primeros pasos

Al abrir MiAplicacion por primera vez:

1. (Primer paso: por ejemplo, qué ventana aparece y dónde está el foco.)
2. (Segundo paso.)
3. (Tercer paso.)

Para moverse por la ventana principal, use Tab y Mayús+Tab. F6 alterna entre (las zonas de la ventana, si las hay).

## (Nombre de la primera función)

Explique para qué sirve y cómo se usa, paso a paso, con los nombres exactos de menús, botones y campos.

1. Abra el menú Archivo con Alt+A y elija «(Nombre exacto de la opción)».
2. Escriba (qué) en el campo «(Nombre exacto del campo)».
3. Pulse el botón «(Nombre exacto)» o la tecla Intro.

Qué anuncia el lector de pantalla al terminar: (el mensaje exacto, si lo hay).

## (Nombre de la segunda función)

(Repita la estructura anterior para cada función. Un encabezado por función.)

## Preferencias

Abra las preferencias con (atajo) o desde el menú (nombre del menú). Cada pestaña agrupa opciones relacionadas; cambie de pestaña con Ctrl+Tab.

### General

- «(Nombre de la opción)»: qué hace y cuál es el valor por defecto.

### Accesibilidad

- «(Nombre de la opción)»: qué hace y cuál es el valor por defecto.

## Accesibilidad y lectores de pantalla

- Todos los controles tienen una etiqueta que el lector anuncia. Las letras de acceso rápido (Alt más la letra subrayada) se indican entre paréntesis en este manual cuando existen.
- En cualquier campo, (atajo de la ayuda contextual, si existe) explica para qué sirve el control.
- Los avisos importantes se leen automáticamente. (Diga cómo repetir el último aviso, si hay un atajo para ello.)
- (Diga si la aplicación usa sonidos y cómo desactivarlos.)
- Se ha probado con (lectores y versiones con los que realmente se haya probado).

## Solución de problemas

### La aplicación no arranca

- Compruebe que ha extraído el ZIP completo y que no la ejecuta desde dentro del ZIP.
- Si Windows muestra un aviso de SmartScreen, elija «Más información» y después «Ejecutar de todas formas».
- Si sigue sin arrancar, busque el archivo arranque_fallido.log en la carpeta data\logs y envíelo al soporte.

### (Otro problema frecuente)

- (Causa y solución, en pasos.)

## Dónde están los datos y los registros

Todo está dentro de la carpeta data, junto a MiAplicacion.exe:

- data\config: su configuración. Cópiela para conservar sus preferencias en otro equipo.
- data\logs: los registros de funcionamiento. Si informa de un fallo, adjunte el archivo incidencias.log.
- data\docs: este manual, la lista de teclas y las novedades.
- (Otras carpetas con datos de la persona usuaria, si las hay.)

Borrar data\config devuelve la aplicación a su configuración de fábrica.

## Preguntas frecuentes

### ¿Puedo usar MiAplicacion desde una memoria USB?

Sí. Es portable: copie la carpeta completa y ejecútela desde allí.

### ¿Cómo sé qué ha cambiado en cada versión?

Tras actualizar, la aplicación muestra las novedades una vez. También puede verlas en el menú Ayuda, «Novedades de cada versión».

### (Otra pregunta)

(Respuesta breve.)

## Contacto y soporte

(Dónde informar de fallos o pedir mejoras. Recuerde pedir el archivo incidencias.log.)
