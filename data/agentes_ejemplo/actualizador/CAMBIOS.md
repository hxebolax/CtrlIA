# Cambios del agente actualizador

## Cómo ampliarlo

- **Nueva comprobación de seguridad del manifiesto o del ZIP:** en `plantillas/app/services/actualizador.py` (`_info_desde_manifiesto` o `_partes_seguras`), con su caso en `plantillas/tests/test_actualizador.py` y su explicación en `referencias/firma-y-seguridad.md`.
- **Cambio en el script de sustitución:** edite `_PLANTILLA_BAT` y ejecute `tests/test_sustitucion.py`, que lanza el `.bat` real. Respete las reglas de la cabecera de la plantilla (flujo con `goto`, `ping` para esperar, rutas completas de `System32`, nada de `/MIR` hacia la instalación, sin rutas en los mensajes del registro). Documente el paso en `referencias/flujo-de-actualizacion.md` y, si afecta a la restauración, en `referencias/vuelta-atras.md`.
- **Otro origen de versiones** (por ejemplo, las publicaciones de una forja): sustituya solo `_leer_manifiesto` y `_info_desde_manifiesto`; el resto del flujo y la firma se mantienen.
- **Cambios en el diálogo:** en `plantillas/app/ui/dialogo_actualizacion.py`, manteniendo botones con acelerador en español, novedades de solo lectura y controles tocados solo desde el hilo principal; con su prueba en `plantillas/tests/test_dialogo_actualizacion.py`.
- Tras cambiar algo, sube `metadata.version` en el frontmatter, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- `_informar_manual` y el aviso tras una actualización fallida ya no usan `wx.MessageBox` (botón en inglés): `mostrar_mensaje` usa los diálogos en español de `app/ui/accesibilidad/dialogos.py` con importación diferida si el proyecto los tiene y, si no, un `wx.MessageDialog` con «Aceptar» puesto a mano.
- Las pruebas con wx ya no crean ni destruyen su propia `wx.App`: reutilizan la de la sesión (`wx.GetApp() or wx.App(False)`, guardada en una variable de módulo para que Python no la destruya) y nunca la destruyen. Crearla y destruirla por módulo dejaba sin aplicación a los módulos siguientes de la batería del proyecto. Tres pruebas nuevas de los avisos en español. Probado: 98 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: servicio de actualización (firma Ed25519 del manifiesto completo con serialización canónica, varias claves públicas para rotar, comparación de versiones PEP 440 y semver sin dependencias, anti-rollback basado en versiones confirmadas, versión omitida, descarga reanudable con tope de tamaño, verificación de tamaño y SHA-256, extracción segura que separa programa y recursos y descarta los datos del usuario), script `.bat` de sustitución con copia de seguridad, marcador de salud, vuelta atrás y cancelación si la aplicación no se cierra, diálogo accesible con comprobación periódica y manual, y herramienta `tools/firmar_version.py` (generar, firmar, verificar).
- Probado en este equipo con Python 3.13, wxPython 4.2.5, cryptography 49 y pytest 9.1: **95 pruebas superadas**, dos ejecuciones seguidas. Incluyen la ejecución real del `.bat` sobre una instalación falsa en una carpeta con espacios, tilde, eñe y apóstrofo, contra un servidor HTTP local: actualización correcta, sin recursos actualizables, versión nueva colgada (el script la termina y restaura), versión nueva que falla al arrancar (vuelta atrás en segundos) y aplicación que no se cierra (no se toca nada). En todos, los datos del usuario de `data/` quedan idénticos byte a byte. `ruff check` y `ruff format` sin avisos (tabulaciones).
- Hallazgos del entorno de prueba incorporados: con `NoDefaultCurrentDirectoryInExePath` definida, `cmd /c actualizar.bat` no encuentra el script (se lanza como `.\actualizar.bat`); en este equipo un filtro de red corta al azar las transferencias por 127.0.0.1, por lo que el servidor de pruebas usa `::1`.
- Sin verificar: compilación real con cx_Freeze y actualización de un `.exe` verdadero, lector de pantalla real (NVDA, JAWS) sobre el diálogo, servidor https real, equipo sin PowerShell o con PowerShell restringido por directiva (el script volvería atrás con el motivo `no_se_pudo_lanzar`), rutas con `%` y la aplicación dentro de un Job Object que prohíba salir de él.
