# Complemento de NVDA para MiAplicacion

Este complemento mejora la lectura de MiAplicacion con el lector de pantalla NVDA. Solo actúa mientras MiAplicacion está en primer plano; en el resto de programas NVDA funciona como siempre.

## Qué mejora

- **Texto completo en las listas.** NVDA corta cada columna de una lista a 511 caracteres. Con el complemento, en MiAplicacion se lee el texto entero.
- **Sin repeticiones.** Cuando MiAplicacion recoloca el foco por su cuenta (por ejemplo, al llegar elementos nuevos), NVDA no vuelve a leer el elemento en el que ya estabas.
- **Leer la fila completa.** NVDA+Mayúsculas+J lee todas las columnas de la fila enfocada con su cabecera. Si lo pulsas dos veces seguidas, la fila se abre en una ventana donde puedes leerla con las flechas y copiarla.

## Teclas

- NVDA+Mayúsculas+J: leer la fila enfocada completa; dos pulsaciones, mostrarla en una ventana.

Puedes cambiar esta tecla en el menú de NVDA, Preferencias, Gestos de entrada, categoría «MiAplicacion».

## Instalar y desinstalar

- MiAplicacion puede ofrecerte instalarlo desde sus preferencias. NVDA te pedirá confirmación y se reiniciará.
- También puedes abrir el archivo `.nvda-addon` desde el Explorador.
- Para desactivarlo o quitarlo: menú de NVDA, Herramientas, Tienda de complementos, pestaña «Complementos instalados».

## Requisitos

NVDA 2026.1 o posterior.
