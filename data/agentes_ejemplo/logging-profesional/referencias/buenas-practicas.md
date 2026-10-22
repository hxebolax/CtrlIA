# Buenas prácticas, privacidad y auditoría

## Mensajes útiles

- **Qué, sobre qué y dónde:** `log.exception("No se pudo guardar el perfil %s en %s", perfil_id, ruta)`, no `log.error("Error al guardar")`.
- **Accionables:** que digan qué hacer o qué comprobar cuando no es un fallo de código («El servidor devolvió 401: la sesión ha caducado; hay que volver a iniciarla»).
- **Una línea por hecho**; los datos variables como argumentos (`%s`, `%d`, `%r`). Usa `%r` para texto externo: deja ver espacios o caracteres raros y no puede fingir líneas nuevas.
- **Datos estructurados** con `extra={"id_descarga": 42}`: aparecen en el JSONL, en `extra`. No uses nombres de atributos que ya tiene `LogRecord` (`name`, `msg`, `args`, `module`...).
- **Tiempos** con `time.perf_counter()` o `registro.operacion(..., avisar_si_supera=...)`.
- **Arranque:** la cabecera ya recoge versión, Python, arquitectura, sistema, si está compilada, rutas y configuración regional; añade lo propio del proyecto (perfil activo, número de cuentas... sin datos personales).

## Privacidad (OWASP Logging Cheat Sheet)

**Registrar:** errores de la aplicación y del sistema, validaciones fallidas, arranque y cierre, cambios de configuración, acciones de riesgo (exportar, borrar, conectar); para cada una, cuándo, dónde (versión y ubicación en el código) y resultado.

**No registrar nunca:** contraseñas, tokens de acceso o de sesión, claves, cadenas de conexión, datos de pago, datos personales sensibles, **el contenido que el usuario escribe o lee** (mensajes, documentos, historial). Si hace falta identificar algo, su longitud, un identificador interno o un hash.

La redacción automática (`registro.redactar`) cubre:

- secretos registrados con `registrar_secreto(valor)`, que se ocultan en cualquier parte;
- `Authorization`, `Proxy-Authorization`, `Cookie` y `Set-Cookie` completas;
- `usuario:contraseña@` en URLs;
- parámetros sensibles en consultas (`?access_token=...&`, `sig=`, `api_key=`...);
- `"password": "..."` en JSON y diccionarios;
- `password=...`, `token: ...`, `clave = "..."` en texto libre;
- tokens JWT (`eyJ...`).

Es una red de seguridad, no una licencia: no pases secretos a los mensajes. El paquete de diagnóstico vuelve a redactar y quita el nombre de usuario de las rutas.

## Antipatrones

- `print()` o `traceback.print_exc()`: en la versión compilada escriben en la nada.
- `logging.basicConfig()` o manejadores en módulos que no son el punto de entrada.
- `except: pass` / `except Exception: pass` sin registrar nada.
- `log.error(str(e))` (pierde la traza) y `log.exception(f"... {e}")` (f-string que repite el mensaje).
- f-strings o `.format()` en llamadas de log.
- Registrar y relanzar en cada capa (trazas duplicadas).
- Varios manejadores sobre el mismo archivo o varios procesos rotando el mismo archivo.
- El visor de registros de la aplicación con el archivo abierto (bloquea la rotación).
- `QueueListener` sin arrancar o sin pararlo al salir; `faulthandler` sin archivo en una aplicación sin consola.
- Un diálogo por cada excepción repetida.
- `logging.warn` (obsoleto): `logging.warning`.
- Crear loggers por objeto o por petición (crecen sin límite): uno por módulo.
- Rutas relativas al directorio de trabajo en lugar de al ejecutable.

## Reglas de ruff que ayudan

En `pyproject.toml`, `[tool.ruff.lint] select` con:

- `LOG` (uso correcto de logging);
- `G` (sin f-strings ni `.format` en logs);
- `BLE` (`except Exception` demasiado amplio);
- `S110` (`try/except/pass`);
- `E9`, `F` (errores reales).

## Lista de auditoría

1. [ ] El registro se configura antes de importar wx y los módulos pesados.
2. [ ] Cada módulo usa `logging.getLogger(__name__)`; no hay `basicConfig` ni manejadores fuera del punto de entrada.
3. [ ] Un único manejador (la cola) en el raíz; oyente arrancado, con `respect_handler_level` y parado al salir.
4. [ ] Cada archivo lo escribe un solo manejador de un solo proceso; los procesos auxiliares tienen su propio archivo.
5. [ ] `encoding="utf-8"` y `errors="backslashreplace"`; rotación con límite conocido.
6. [ ] Ganchos: `sys.excepthook`, `threading.excepthook`, `sys.unraisablehook`, asyncio si se usa, `captureWarnings`, `faulthandler` con archivo, flujos estándar redirigidos si son `None`.
7. [ ] Las excepciones no controladas dejan archivo inmediato y un único aviso accesible.
8. [ ] El formato incluye fecha con milisegundos, nivel, hilo, logger, archivo:línea, función, operación y origen de la excepción.
9. [ ] Cabecera de sesión con versión, Python, wx, sistema, si está compilada y rutas.
10. [ ] Redacción probada con token, contraseña, `Authorization`, URL con credenciales y JWT; no se registra contenido del usuario.
11. [ ] No hay `except` silenciosos; cada `except` amplio registra al menos en DEBUG con traza.
12. [ ] `log.exception` dentro de `except`; formato diferido; sin f-strings.
13. [ ] Bibliotecas ruidosas silenciadas; repeticiones resumidas.
14. [ ] Vigilante de congelaciones activo; operaciones lentas medidas.
15. [ ] Procesos externos: sin consola, sin entrada estándar, código de salida, duración y final de stderr registrados.
16. [ ] Paquete de diagnóstico que vacía la cola, redacta y no deja archivos abiertos.
17. [ ] Probado en la versión compilada: error en un manejador, en un hilo, en el arranque, carpeta `data` de solo lectura, archivo de registro bloqueado por otro programa, ruta con caracteres no ASCII.
