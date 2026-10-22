# Escribir para personas que usan lector de pantalla

Quien lee el manual con NVDA, JAWS o Narrador no lo ve de un vistazo: lo recorre por encabezados, por listas o línea a línea. Estas reglas hacen que encuentre rápido lo que busca y que cada instrucción se pueda seguir con el teclado.

## Estructura

- **Un solo encabezado de nivel 1** con el nombre del documento. Después, nivel 2 para cada función o tema y nivel 3 para sus partes. Nunca se salta un nivel (de 2 a 4).
- Con NVDA o JAWS en modo exploración, H salta al siguiente encabezado y 1, 2 o 3 al siguiente de ese nivel. En el visor en modo texto, la lista «Secciones» cumple esa función. Un buen título de encabezado basta para saber si el apartado interesa.
- **Títulos que dicen de qué trata**: «Descargar un vídeo», no «Uso» ni «Más cosas».
- **Párrafos cortos**, de una a tres frases. Un párrafo largo obliga a escuchar entero lo que no interesa.
- **Listas** para opciones o elementos sueltos; **listas numeradas** para pasos que se siguen en orden. El lector anuncia «lista de 5 elementos» y la persona sabe cuánto queda.
- **Tablas pequeñas** (dos o tres columnas) y solo para datos que se consultan por fila, como atajos. Con cabecera. Una tabla de muchas columnas se oye como una sucesión de celdas sin contexto: mejor una lista.
- **Sin arte ASCII**, líneas de guiones decorativas, emojis ni símbolos que se leen como «flecha derecha» o «estrella». Para separar, un encabezado.

## Frases

- **Frases cortas y directas**, en segunda persona de cortesía o impersonal, siempre la misma forma en todo el documento.
- **Lo importante primero**: «Para guardar, pulse Ctrl+S», no «Si en algún momento desea conservar los cambios realizados, puede pulsar Ctrl+S».
- **Sin abreviaturas** que el sintetizador lea mal («p. ej.», «aprox.», «nº»). Escriba la palabra entera.
- **Números y unidades con claridad**: «5 minutos», «2 GB».
- Evite la voz pasiva larga y las dobles negaciones.

## Instrucciones con teclado, no con ratón

- Describa **teclas y nombres**, no gestos ni posiciones: «pulse Intro sobre el archivo», no «haga doble clic»; «abra el menú Archivo con Alt+A», no «arriba a la izquierda».
- Nada de «haga clic aquí», «el botón verde», «el icono de la rueda», «a la derecha de la lista». El lector no transmite colores, formas ni posiciones.
- Si existe un atajo, dígalo en el mismo paso: «Elija Guardar (Ctrl+S)».
- Escriba las combinaciones siempre igual: Ctrl, Alt, Mayús, Intro, Escape, Supr, Retroceso, Tab, F1... unidas con «+» y sin espacios: Ctrl+Mayús+S. Es el formato que entiende `tools/comprobar_teclas.py`.

## Nombres exactos de la interfaz

- Use **los mismos nombres** que anuncia el lector: menús, opciones, botones, campos y pestañas, entre comillas latinas: el botón «Aceptar», el campo «Carpeta de destino».
- Sin la «&» del acelerador: se escribe «Guardar», no «&Guardar».
- Si cambia el nombre de un control en el código, cambie el manual en el mismo momento (ver [ciclo-de-documentacion.md](ciclo-de-documentacion.md)).

## Qué dice el lector en cada momento

- Indique **dónde queda el foco** tras una acción: «Al abrirse el diálogo, el foco está en el campo «Nombre»».
- Indique **qué se anuncia** al terminar una tarea larga («Descarga completada») y cómo repetir el último aviso si hay un atajo.
- Si algo solo se ve (una barra de progreso, un color), diga cómo conocer ese dato con el teclado.

## Enlaces

- El texto del enlace dice adónde lleva: «[página de descargas](https://...)», nunca «[aquí](https://...)». Con NVDA, la lista de enlaces (Insert+F7) muestra solo ese texto.
- En el visor en modo texto, los enlaces aparecen como «texto (dirección)»: que la dirección sea corta y legible.

## Documentos de la aplicación

| Documento | Forma recomendada |
|---|---|
| Manual (`docs/manual.md`) | Encabezados por tarea, pasos numerados, nombres exactos |
| Teclas (`docs/teclas.md`) | Un apartado por ventana, tabla «Atajo, Acción» o lista `- **Atajo**: acción` |
| Novedades (`docs/changelog.md`) | Una versión por apartado de nivel 2, con Novedades, Mejoras y Correcciones en nivel 3 |

## Lista de comprobación

1. Un solo `#`; sin saltos de nivel.
2. Cada paso con teclado y con el nombre exacto del control.
3. Ningún «haga clic», «aquí», «a la derecha», «el botón azul».
4. Tablas de dos o tres columnas como máximo.
5. Sin arte ASCII ni emojis.
6. Leído de principio a fin con el lector de pantalla al menos una vez por versión.
