# Método de auditoría

## Fase 1. Alcance

Acuerda (o deduce del encargo y dilo en el informe):

- **Qué se audita:** el proyecto entero, un módulo, una ventana, una versión concreta (anota la versión de `app/core/version.py` o el commit).
- **Qué áreas:** todas por defecto; si el encargo es parcial («revisa la seguridad»), las demás quedan fuera de alcance de forma explícita.
- **Qué se puede ejecutar:** pruebas, la aplicación desde código, la versión compilada. Sin permiso, nada que modifique datos del usuario, instale paquetes o salga a Internet.
- **Si se puede cambiar código.** En una auditoría, normalmente no: se entrega el informe. Si el usuario pide además corregir, se hace después y por separado.

## Fase 2. Inventario

Sin cambiar nada, anota:

- Estructura: `main.py`, paquete principal, `app/core`, `app/services`, `app/ui`, `tools/`, `tests/`.
- Puntos de entrada y arranque: configuración del registro, `wx.Locale`, ganchos de excepciones, instancia única.
- Dependencias: `requirements*.txt`, `pyproject.toml`; versiones fijadas o no; dependencias opcionales.
- Compilación: `setup_cx.py` o `[tool.cxfreeze]`, qué viaja en `data/`, `include_msvcr`, `optimize`.
- Pruebas: cuántas, cómo se lanzan, si pasan (ejecútalas y anota el resultado real).
- Auditorías anteriores en `docs/auditorias/` y pruebas que citan sus identificadores.
- Tamaño: archivos con más líneas y funciones más largas (señalan dónde mirar primero).
- Qué carpetas **no** son código activo (copias, material de consulta, compilaciones) y se excluyen.

## Fase 3. Detector automático

1. `python tools/auditoria_rapida.py --json --salida <temporal>` para tener el inventario completo y `--markdown` para leerlo.
2. `ruff check` con las reglas que delatan fallos reales, si está instalado: `ruff check --select E9,F63,F7,F82,F811,B,BLE,S110,LOG,G .` (nombres sin definir, comparaciones rotas, excepciones silenciadas, registro mal formado). Declara `builtins = ["_"]` si el proyecto usa gettext; si no, los falsos `F821` de `_` tapan los reales.
3. Opcional, con permiso (sale a Internet): `pip-audit -r requirements.txt` para vulnerabilidades conocidas en dependencias.

Los resultados del detector son **candidatos**. Ninguno pasa al informe sin leer el código.

## Fase 4. Revisión manual guiada por áreas

Recorre [areas-y-comprobaciones.md](areas-y-comprobaciones.md) área por área. En cada una:

- Confirma o descarta los avisos del detector de esa área (y anota los descartes frecuentes para ajustar `.auditoria-ignorar`).
- Busca lo que el detector no puede ver: flujos incompletos, estados inconsistentes, cierres a medias, condiciones de carrera, preferencias que no se aplican, mensajes engañosos, recorridos de teclado rotos.
- Sigue los flujos del usuario de principio a fin (abrir, trabajar, guardar, cerrar, volver a abrir), no solo archivos sueltos.

## Fase 5. Verificación

- Ejecuta la batería de pruebas y anota el resultado exacto.
- Para cada hallazgo **crítico o alto**, intenta demostrarlo: una prueba mínima que falle, una ejecución que muestre el error en el registro, un archivo de ejemplo que lo provoque. Si lo consigues, su certeza pasa a «demostrado».
- Las pruebas que escribas para demostrar un fallo se entregan aparte (o marcadas `xfail(strict=True)` con el identificador del hallazgo) si el usuario no ha pedido cambios.
- Lo que depende de un lector de pantalla real, de otra máquina o de la versión compilada queda como «pendiente de verificar a mano».

## Fase 6. Informe

- Archivo: `docs/auditorias/auditoria_NN_AAAA-MM-DD.md`. `NN` es el siguiente número de dos cifras tras el mayor existente (empieza en `01`). Fecha del día de la auditoría.
- Usa la [plantilla](../plantillas/docs/auditorias/plantilla_auditoria.md). Un apartado por hallazgo, ordenados por prioridad.
- Identificadores `ANN-MM` (auditoría, hallazgo). No se reutilizan ni se renumeran: un hallazgo descartado conserva su número con el estado «descartado».
- Extractos de código breves (unas líneas); lo demás, con `archivo:línea`.

## Fase 7. Seguimiento

Al empezar una auditoría nueva, antes que nada, revisa la anterior:

- **Corregido y verificado:** hay un cambio y una prueba (o comprobación) que lo confirma. Cita ambos.
- **Corregido sin verificar:** hay un cambio pero no una prueba.
- **Pendiente:** sigue como estaba (indica si ha empeorado).
- **Descartado:** no era un fallo, con el motivo.

Si hay pruebas que citan identificadores (`A07-03`), búscalas: son la mejor evidencia de corrección.

## Prioridades

- **Crítica:** pérdida o corrupción de datos, ejecución de código o fuga de secretos, la aplicación no arranca o se cierra, la persona que usa lector de pantalla no puede completar una tarea principal.
- **Alta:** fallo frecuente en una tarea habitual, bloqueo de la interfaz o del teclado, error que se pierde sin rastro en una operación importante, compilación que no funciona en un equipo limpio.
- **Media:** fallo en casos límite, verbosidad o ruido que dificulta el uso, degradación de rendimiento notable, portabilidad (datos fuera de `data/`).
- **Baja:** mantenibilidad, estilo con consecuencias menores, documentación desactualizada, mejoras.

La severidad del detector (alta, media, baja) es una pista, no la prioridad final: la decide el impacto real en este proyecto.

## Certeza

- **Demostrado:** se ha reproducido (prueba, ejecución, registro). Indica cómo.
- **Observado en el código:** la lectura lo muestra sin depender de supuestos (por ejemplo, un `except Exception: pass` que envuelve el guardado).
- **Hipótesis:** plausible, pero depende de algo no comprobado (un comportamiento de una biblioteca, del lector de pantalla, de otra versión de Windows). Di qué haría falta para confirmarlo.

Nunca presentes una hipótesis como demostrada. Nunca afirmes «probado con NVDA» si no se ha usado NVDA.
