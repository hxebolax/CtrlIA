# Cambios del agente proyecto-nuevo

## Cómo ampliarlo

- **Nueva pieza del esqueleto:** en `plantillas/` con la misma ruta que tendrá en el proyecto, su prueba en `plantillas/tests/` y una fila en la tabla «Qué entrega este agente» de `SKILL.md`. El generador la copia sola; si usa el nombre de la aplicación, escribe `MiAplicacion` (o léelo de `app/core/version.py`).
- **Archivos que no deben llegar al proyecto generado** (herramientas del propio agente): añádelos a `ARCHIVOS_EXCLUIDOS` en `plantillas/tools/crear_proyecto.py`.
- **Nuevas sustituciones del generador:** en `_sustituir()` y un caso en `tests/test_crear_proyecto.py`.
- **Reglas o explicaciones:** en `SKILL.md` si son cortas y siempre aplicables; si no, en `referencias/`.
- Para probar: copia `plantillas/` a una carpeta temporal y ejecuta `python -m pytest tests` con un Python que tenga wxPython. La prueba `test_proyecto_generado_pasa_sus_pruebas` genera un proyecto y ejecuta también sus pruebas.
- Tras cambiar algo, sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- Fuera `wx.MessageBox` (botones en inglés): `dialogo_texto.py` ofrece `mostrar_mensaje` y `mostrar_error`, que usan los diálogos en español de `app/ui/accesibilidad/dialogos.py` con importación diferida si existen o un `wx.MessageDialog` con «Aceptar»; los usan la ventana principal, `main.py` (error inesperado y «ya está en ejecución») y el cartel de error de arranque.
- `tests/conftest.py`: fixture automática que hace fallar al momento cualquier `ShowModal` o `wx.MessageBox` real no simulado, con el título del cuadro, salvo en pruebas marcadas `interfaz` (antes una prueba así se quedaba colgada esperando). Tres pruebas nuevas de los mensajes. Probado: 60 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: `main.py` con orden de arranque y red de seguridad; `app/core` (rutas, versión, configuración, registro básico, instancia única con buzón y cesión de primer plano, voz opcional); ventana principal con cinco menús en español, atajos, F1, Ctrl+H, lista con etiqueta previa, menú contextual, bandeja opcional, geometría recordada, tareas en hilos y cierre ordenado; diálogos Acerca de, Opciones y de texto; documentación de usuario; `entorno.bat`; `setup_cx.py`; generador `tools/crear_proyecto.py` con `--nombre`, `--destino`, `--id`, `--paquete`, `--python` y `--simular`.
- Probado de verdad en este equipo (Windows 11, Python 3.13.13):
  - 57 pruebas automáticas de las plantillas con wxPython 4.2.5, todas pasan: arranque real de `main.py` en otro proceso, segunda instancia real que deja la orden de mostrarse, mutex detectado desde otro proceso, configuración (valores por defecto, lectura, escritura, archivo dañado, tipos erróneos), registro, menús y atajos, diálogos y generador.
  - Un proyecto generado con otro nombre de paquete pasa sus propias 49 pruebas.
  - Compilación real con cx_Freeze 8.6.4: `data` de la distribución solo con `docs`, runtimes de Visual C++ y catálogo español de wx presentes, y el `.exe` arranca y se cierra con código 0 (`--comprobar-arranque`).
  - `entorno.bat crear`, `pruebas` y `compilar` en un proyecto generado con `--python 3.13`, en un entorno virtual nuevo: instalan wxPython 4.3.1 y cx_Freeze 8.7.1, pasan las 49 pruebas y el `.exe` compilado arranca. En esa prueba se descubrió que `py -3` elegía una versión preliminar sin ruedas de wxPython; ahora, sin `VERSION_PY`, se usa la predeterminada del lanzador `py`.
  - `ruff check` y `ruff format --check` sin avisos.
- Sin verificar: comportamiento con NVDA, JAWS o Narrador reales (anuncios, orden de foco, menú de la bandeja con el teclado: Windows+B, Intro y tecla Aplicaciones); traer al frente la primera instancia con ventanas reales en pantalla (la prueba automática verifica el buzón, no el primer plano); el `.exe` en un Windows sin Python ni el redistribuible de Visual C++; Python 3.10 a 3.12 (las plantillas se escribieron compatibles pero solo se ejecutaron con 3.13).
