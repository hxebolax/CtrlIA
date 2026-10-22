# Estrategia de pruebas para una aplicación wxPython

## La pirámide

Tres niveles, de más barato y estable a más caro y frágil. Reparte el esfuerzo en esa proporción.

1. **Lógica sin wx (la gran mayoría, en torno al 70-80 %).** Funciones y clases que reciben datos y devuelven datos: formatear, validar, calcular, interpretar respuestas de un servicio, decidir qué anunciar, migrar ajustes, construir órdenes para un proceso externo. Se ejecutan en milisegundos y no dependen del sistema.
2. **wx sin mostrar (una parte menor).** Diálogos, listas y ventanas creados de verdad pero sin `Show()`: se comprueban valores, estados, eventos, atajos, `wx.CallAfter` y nombres accesibles (MSAA). Usan las fixtures `marco`, `panel` y `dialogo_modal`.
3. **Interfaz real (excepcional, marcador `interfaz`).** Ventana visible, foco real y teclas reales con `wx.UIActionSimulator`. Lentas, frágiles y roban el foco: solo para lo que no se puede comprobar de otra forma, y nunca en la ejecución por defecto.

Por encima de la pirámide queda lo que **no se automatiza**: la experiencia con un lector de pantalla real.

## Separar lógica e interfaz para poder probar

Si algo es difícil de probar, casi siempre es porque la lógica está dentro de un manejador de eventos. Señales:

- Un `al_pulsar_guardar` de 80 líneas que lee controles, valida, escribe en disco y muestra mensajes.
- Una función que llama a `wx.MessageBox` en mitad de un cálculo.
- Un servicio que importa `wx` para anunciar por voz.

Cómo separarlo, sin rehacer la aplicación:

1. **Extrae la decisión a una función pura.** El manejador lee los controles, llama a `validar_perfil(nombre, correo)` (que devuelve una lista de errores) y muestra el resultado. La función se prueba sin wx.
2. **Devuelve datos en vez de mostrar.** `componer_mensaje_error(excepcion) -> str` se prueba; el diálogo que lo muestra es trivial.
3. **Inyecta lo externo.** Un servicio recibe la función de descarga, el reloj o la ruta base como parámetro con valor por defecto. En la prueba se pasa una versión simulada; así no hace falta parchear a ciegas.
4. **Un único punto para la voz.** Si todo el código habla a través de `app.core.voz.hablar`, `voz_simulada` lo captura en un solo sitio.
5. **Un único módulo de rutas** que lea `APP_DATA_DIR` al pedir cada ruta (no solo al importarse). Así `data_temporal` basta para aislar.

Refactoriza en pasos pequeños y **con una prueba antes**: primero una prueba del comportamiento actual (aunque sea de nivel 2), después extrae la lógica, después pruebas de nivel 1 más finas.

## Qué merece prueba

- Todo fallo corregido (prueba de regresión).
- Las ramas de error: archivo dañado, archivo ausente, permiso denegado, respuesta inesperada del servidor, red caída simulada, cancelación a mitad.
- Los límites: lista vacía, un elemento, muchos, Unicode y acentos, textos muy largos, espacios, rutas con espacios y caracteres no ASCII.
- Las preferencias: valor por defecto, guardar, releer, Cancelar que no cambia nada, ajustes antiguos sin las claves nuevas.
- La accesibilidad comprobable: etiquetas que nombran los controles (MSAA), atajos que llaman a `Skip()` para lo no gestionado, anuncios de voz esperados (y los que NO deben producirse, como releer la fila al moverse).

## Qué no merece la pena automatizar

- **La experiencia real con lector de pantalla**: qué se oye, en qué orden, con qué verbosidad, si hay dobles lecturas. Una prueba MSAA o una lista de anuncios simulados es una red contra regresiones, no una prueba con usuarios. Queda como comprobación manual con NVDA (Visor de voz), JAWS o Narrador, y se dice así en la entrega.
- El aspecto visual (colores, alto contraste, escalado): comprobación manual o capturas revisadas por alguien que vea.
- Servicios externos reales: se simula la respuesta; como mucho, una prueba `red` de humo que no se ejecuta por defecto.
- Código de terceros (wx, requests): se prueba nuestro uso, no la biblioteca.

## Buenas prácticas generales

- Una prueba comprueba un comportamiento; su nombre lo dice en español.
- Organiza por módulo probado (`tests/test_ajustes.py`, `tests/test_dialogo_perfil.py`), no por fecha ni por auditoría; si un fallo viene de una auditoría, cita su identificador en la docstring.
- `pytest.mark.parametrize` para tablas de casos en lugar de copiar pruebas.
- Sin `print` en las pruebas: `caplog` para el registro y aserciones con mensaje.
- Sin dependencias de orden: cada prueba prepara lo suyo con fixtures.
- Rápidas: la batería por defecto debería tardar segundos. Lo lento, con `@pytest.mark.lento`.

## Fuentes

- Documentación de pytest sobre fixtures: https://docs.pytest.org/en/stable/how-to/fixtures.html
- monkeypatch: https://docs.pytest.org/en/stable/how-to/monkeypatch.html
