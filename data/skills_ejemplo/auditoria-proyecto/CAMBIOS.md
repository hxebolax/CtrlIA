# Cambios del agente auditoria-proyecto

## Cómo ampliarlo

- **Nueva regla del detector:** entrada en `REGLAS` y función `regla_...` en `plantillas/tools/auditoria_rapida.py`, añadida a `FUNCIONES_REGLA`; pruebas con un caso positivo y otro negativo parecido en `plantillas/tests/test_auditoria_rapida.py`; apartado en `referencias/reglas-del-detector.md` (una prueba lo exige). Antes de darla por buena, ejecútala en modo solo lectura sobre un proyecto real y revisa una muestra de avisos.
- **Ajuste por falsos positivos:** añade el caso que no debe marcarse a las pruebas y anota el falso positivo en `referencias/reglas-del-detector.md`.
- **Nueva comprobación manual:** en su área de `referencias/areas-y-comprobaciones.md`.
- **Cambio en el formato del informe:** en `plantillas/docs/auditorias/plantilla_auditoria.md` y en «Formato de un hallazgo» de `SKILL.md`.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.0.0 - 2026-09-25

- Primera versión: método de auditoría por fases, lista de comprobación de once áreas, plantilla de informe numerado con seguimiento, y detector `auditoria_rapida.py` con 28 reglas basadas en `ast` (solo biblioteca estándar), salida en texto, Markdown y JSON, exclusiones en línea y por archivo, filtros por regla y severidad, y código de salida.
- Probado de verdad:
  - 44 pruebas del detector con Python 3.13 y pytest 9 (43 en una copia del proyecto, más la que comprueba que cada regla está documentada, que solo se ejecuta dentro de la carpeta del agente).
  - El detector se ejecutó en modo solo lectura sobre dos aplicaciones wxPython reales de tamaño medio para calibrar falsos positivos, con Python 3.13, 3.14 y 3.15 (mismo resultado en las tres). Se ajustaron: `EVT_CHAR_HOOK` (solo el camino de teclas no gestionadas), etiquetas seguidas de clases propias del proyecto, marcadores `?` guardados en variables, severidad del SQL según la posición del valor, `noqa` de ruff en los `except`, excepciones esperables, módulos de línea de órdenes y escrituras en el registro agrupadas por archivo.
- Sin verificar: Python 3.10, 3.11 y 3.12 (no había intérpretes en el equipo; el código evita sintaxis posterior a 3.10); `pip-audit` (necesita red).
