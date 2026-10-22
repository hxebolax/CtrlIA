# Ayuda dentro de la aplicación

La ayuda tiene que estar a una tecla de distancia y leerse bien con el lector de pantalla. Este documento describe las piezas y cómo integrarlas.

## El menú Ayuda

`visor_ayuda.crear_menu_ayuda(ventana)` crea el menú con:

- «Manual de uso» (F1): `abrir_manual`.
- «Teclas de la aplicación»: `abrir_teclas`.
- «Novedades de cada versión»: `abrir_novedades`.

Añada al final, separado, «Acerca de MiAplicacion». El atajo F1 va en el texto del menú (`"&Manual de uso\tF1"`): así wx lo registra como acelerador, el lector lo anuncia al recorrer el menú y `tools/comprobar_teclas.py` lo encuentra.

## F1: el manual

- F1 abre el manual en el visor accesible, **no modal**: se puede consultar mientras se usa la aplicación y alternar con Alt+Tab.
- Si ya está abierto, F1 lo trae al frente en lugar de abrir otro.
- **Modo HTML** (con el paquete `markdown` y el motor Edge de `wx.html2`): página con encabezados, listas y tablas reales. Se navega con las teclas del modo exploración del lector (H, 1-3, T, L). Los enlaces externos se abren en el navegador.
- **Modo texto** (siempre disponible, sin dependencias): lista «Secciones» + campo de solo lectura. Intro en la lista lleva el cursor al apartado. Es el modo de respaldo y también una opción válida si se prefiere leer con las flechas.
- En los dos modos: Ctrl+F busca, F3 busca el siguiente, Escape cierra.
- Para instalar el modo HTML, añada `markdown` a `requirements.txt`. Sin él, el visor funciona igual en modo texto.

Pendiente de comprobar en cada proyecto: que Ctrl+F, F3 y Escape llegan al diálogo cuando el foco está dentro de la página web (el motor Edge procesa el teclado en su propio proceso). Si no llegan, el botón «Cerrar» y los botones de búsqueda siguen disponibles con Tab.

## F1 contextual (opcional)

Si el manual tiene un apartado por ventana, F1 puede abrirlo directamente en ese apartado:

- Modo texto: `visor.secciones.SetStringSelection("Preferencias")` y `visor.ir_a_seccion(visor.secciones.GetSelection())`.
- Modo HTML: la extensión `toc` de `markdown` da un `id` a cada encabezado; `visor.web.RunScript("location.hash = 'preferencias'")` salta a él.

## Ctrl+H: ayuda del control enfocado

Una ayuda breve por control, sin abrir el manual:

```python
campo.SetHelpText(_("Carpeta donde se guardan las descargas. Pulse Examinar para elegirla."))
```

- Un atajo global (Ctrl+H) lee `wx.Window.FindFocus().GetHelpText()` y lo anuncia por voz o lo muestra en un diálogo de texto de solo lectura. Si el control no tiene ayuda, se anuncia «Sin ayuda para este control».
- Documente Ctrl+H en `docs/teclas.md` y márquelo en el código con `# atajo: Ctrl+H` si se atiende a mano.
- `SetHelpText` no sustituye a la etiqueta: cada control lleva su `StaticText` delante con el nombre que anuncia el lector.
- Una frase o dos: qué es y qué se espera que se escriba o elija. Traducible con `_()`.

## Novedades tras actualizar

```python
wx.CallAfter(visor_ayuda.mostrar_novedades_si_procede, ventana, APP_VERSION)
```

- Se llama una vez al arrancar, cuando la ventana principal ya está visible, para que el visor quede delante y el lector lo anuncie.
- Instalación nueva: no muestra nada (no hay «novedades» para quien empieza) y anota la versión.
- Tras actualizar: muestra las entradas del registro entre la última versión vista y la actual, de la más nueva a la más antigua, y anota la actual en `data/config/novedades.json`.
- Solo una vez por versión. Si la versión no tiene entrada en el registro, no muestra nada.
- Si el proyecto ya guarda su configuración en otro archivo, puede pasar ese dato a `novedades_pendientes(..., ruta=...)` o adaptar `leer_ultima_vista` y `marcar_vista`.
- Ofrezca una preferencia «Mostrar las novedades tras actualizar» si alguien prefiere no verlas.

## Acerca de

Un diálogo de texto de solo lectura (o el mismo visor en modo texto) con:

- Nombre y versión: «MiAplicacion 1.2.0».
- Una frase de qué es.
- Autoría, licencia y dirección web (como texto, no solo como enlace).
- Versiones de Python y wxPython, útiles para el soporte.
- Botones «&Copiar información» (al portapapeles, para pegar en un informe de fallo) y «&Cerrar».

Evite `wx.adv.AboutBox` si la información importante queda en controles que el lector no recorre con Tab; un campo de texto de solo lectura se lee entero con las flechas.

## Mensajes de la interfaz como ayuda

- Un error dice qué pasó y qué hacer: «No se pudo guardar el archivo porque la carpeta es de solo lectura. Elija otra carpeta en Preferencias, General.»
- Los textos vacíos también ayudan: una lista vacía puede decir «No hay descargas. Pulse Ctrl+N para añadir una.»
- La barra de estado no es ayuda: el lector no la anuncia sola. Lo importante se anuncia por voz o se muestra en un diálogo.
