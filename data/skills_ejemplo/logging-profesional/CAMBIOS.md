# Cambios del agente logging-profesional

## Cómo ampliarlo

- **Nueva capacidad del registro:** en `plantillas/app/core/registro.py` (o un módulo nuevo), con su prueba en `plantillas/tests/test_registro.py` y su explicación en `referencias/`.
- **Nuevo patrón de redacción:** en `_REDACCIONES` y un caso en `test_redaccion`.
- **Nueva regla o antipatrón:** en `SKILL.md` (si es corta y general) o en `referencias/buenas-practicas.md`, y en la lista de auditoría.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- `test_puente_wx` reutiliza la `wx.App` de la sesión en vez de crear otra. El cartel de error de arranque de `main_ejemplo.py` usa `wx.MessageDialog` con «Aceptar» en lugar de `wx.MessageBox`. Probado: 23 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: registro por sesión (legible y JSONL), `incidencias.log`, cola con hilo escritor, manejador de archivo tolerante al `WinError 32`, captura de excepciones no controladas (principal, wx, hilos, `unraisable`, asyncio, warnings), `faulthandler`, flujos estándar en la versión compilada, origen real de las excepciones y código propio, redacción de secretos, resumen de repeticiones, contexto de operación, decorador para manejadores, ejecución registrada de procesos, puente de mensajes de wxWidgets, marca de sesión en curso, vigilante de congelaciones, paquete de diagnóstico y aviso accesible de errores.
- Pruebas: 23 automáticas (incluidas excepción en un manejador de wx real, puente de wxWidgets y paquete de diagnóstico).
