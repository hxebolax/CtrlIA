# Auditoría NN: NombreDelProyecto, AAAA-MM-DD

<!--
PLANTILLA del agente «auditoria-proyecto».
Guardar como docs/auditorias/auditoria_NN_AAAA-MM-DD.md, donde NN es el
siguiente número de dos cifras (01, 02...). Borra los comentarios y los
apartados que no apliquen, pero no borres «Qué no se revisó» ni «Pendiente
de verificar»: son parte del resultado.
Identificadores de hallazgo: ANN-MM (A07-03 = auditoría 7, hallazgo 3).
No se reutilizan ni se renumeran.
-->

## Datos de la auditoría

- **Versión revisada:** 1.2.3 (commit o fecha del código).
- **Fecha:** AAAA-MM-DD.
- **Alcance:** proyecto completo / módulos concretos / áreas concretas.
- **Encargo:** solo informe / informe y corrección.
- **Auditoría anterior:** `auditoria_NN_AAAA-MM-DD.md` (o «ninguna»).

## Resumen

Una o dos frases con el estado general y lo más urgente.

Hallazgos nuevos por prioridad:

- Crítica: 0
- Alta: 0
- Media: 0
- Baja: 0

Lo primero que conviene corregir:

1. ANN-01: título.
2. ANN-02: título.

## Seguimiento de la auditoría anterior

<!-- Una línea por hallazgo anterior no cerrado. Estados: corregido y verificado, corregido sin verificar, pendiente, descartado. -->

- **A06-01** (alta), corregido y verificado: `tests/test_ajustes.py::test_guardado_atomico` pasa.
- **A06-02** (media), pendiente: sigue en `app/ui/principal.py:210`.
- **A06-03** (baja), descartado: no era un fallo porque ...

## Qué se ejecutó

- Detector: `python tools/auditoria_rapida.py --markdown` → N avisos (X confirmados, Y descartados, Z por revisar).
- Pruebas: `python -m pytest` → resultado exacto (por ejemplo, `212 passed, 3 skipped in 14.2s`).
- Análisis estático: `ruff check --select ...` → resultado.
- Otras comprobaciones: arranque desde código, compilación, pruebas MSAA, `pip-audit` (si se hicieron).

## Hallazgos

<!-- Ordenados por prioridad. Copia este bloque para cada hallazgo. -->

### ANN-01. Título que describe el problema en una frase

- **Prioridad:** crítica / alta / media / baja.
- **Área:** corrección / seguridad / robustez / rendimiento / accesibilidad / portabilidad / empaquetado / registro / mantenibilidad / dependencias / documentación.
- **Certeza:** demostrado (cómo) / observado en el código / hipótesis (qué falta para confirmarlo).
- **Evidencia:** `app/ruta/archivo.py:123`, en `Clase.metodo`.

```python
# Extracto breve (unas líneas)
```

- **Impacto:** qué le ocurre a la persona usuaria o a sus datos, y cuándo.
- **Reproducción:** pasos con teclado, o la prueba que lo demuestra.
- **Propuesta:** cambio concreto (y alternativa si la hay).
- **Validación:** prueba automática que lo cubrirá y comprobación manual necesaria.

## Qué se revisó

- Áreas: corrección, seguridad, robustez, rendimiento, accesibilidad, portabilidad, empaquetado, registro, mantenibilidad, dependencias, documentación.
- Carpetas y módulos: `app/`, `main.py`, `setup_cx.py`, `tools/`...
- Flujos recorridos: arranque, ...

## Qué no se revisó

- Lo que quedó fuera de alcance y por qué (por ejemplo, «el complemento del lector de pantalla», «la versión compilada, porque no se compiló»).

## Pendiente de verificar a mano

- Con lector de pantalla real (NVDA, JAWS, Narrador): ...
- En la versión compilada o en un equipo limpio: ...
- Con servicios reales o red: ...

## Avisos del detector descartados

<!-- Opcional. Solo los que se repetirán en la próxima ejecución: conviene añadirlos a .auditoria-ignorar con el motivo. -->

- `app/ruta.py:45` `getcwd`: la herramienta se lanza siempre desde la raíz. Propuesto para `.auditoria-ignorar`.
