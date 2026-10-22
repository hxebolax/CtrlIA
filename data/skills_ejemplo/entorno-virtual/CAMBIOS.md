# Cambios del agente entorno-virtual

## Cómo ampliarlo

- Nuevas reglas o avisos: en `SKILL.md` si son cortos y siempre aplicables; si son largos, en un archivo de `referencias/` enlazado desde `SKILL.md`.
- Nuevas plantillas: en `plantillas/` con la misma ruta que tendrán en el proyecto, y una fila en la tabla «Qué entrega este agente».
- Tras cambiar algo, sube `metadata.version` en el frontmatter, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.0.0 - 2026-09-25

- Primera versión: `entorno.bat` (menú accesible y órdenes), `setup_cx.py` con lista blanca de `data/`, `include_msvcr`, `replace_paths` y limpieza previa; `tools/verificar_build.py`; `tools/empaquetar.py` con SHA-256 y firma opcional; `app/core/paths.py` portable; pruebas de distribución.
- Probado en este equipo: compilación real de un proyecto mínimo con wxPython 4.2.5 y cx_Freeze 8.6.4 (data limpia, runtimes VC++ presentes, ZIP generado, `.exe` arranca y sale con código 0).
