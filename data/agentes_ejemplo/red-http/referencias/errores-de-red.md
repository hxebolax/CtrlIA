# Errores de red: qué decir, qué registrar y si reintentar

`clasificar_error(error)` o `clasificar_error(respuesta=r)` devuelve un `ErrorClasificado` con `categoria`, `mensaje` (para el usuario, en español), `reintentar`, `codigo_http`, `espera` (segundos de `Retry-After`) y `detalle` (técnico y redactado, para el registro).

## Categorías

- **red**: fallo pasajero de la conexión (tiempo agotado, corte, DNS, conexión rechazada). Normalmente se puede reintentar.
- **servidor**: el otro extremo falla o pide calma (5xx, 429, redirecciones infinitas, archivo dañado).
- **cliente**: la petición no es válida o no hay permiso (4xx, dirección mal escrita, archivo demasiado grande). Reintentar igual no sirve.
- **cancelado**: lo pidió el usuario o el cierre de la aplicación. No es un error: no se muestra diálogo de error.
- **seguridad**: certificado no válido o degradación de https a http. Nunca se reintenta ni se «arregla» desactivando la verificación.
- **local**: disco lleno, sin permisos, archivo bloqueado.
- **desconocido**: todo lo demás; se registra con traza completa.

## Tabla

| Situación (excepción o estado) | Mensaje para el usuario (resumido) | Qué registrar | ¿Reintentar? |
|---|---|---|---|
| Tiempo de conexión agotado (`ConnectTimeout`) | «El servidor no ha respondido a tiempo al conectar...» | WARNING, una línea, servidor | Sí |
| Tiempo de lectura agotado (`ReadTimeout`, o `ConnectionError` que lo envuelve) | «El servidor ha dejado de responder...» | WARNING, una línea | Sí (solo idempotentes) |
| Nombre no resuelto (`gaierror`, `NameResolutionError`) | «No se ha encontrado el servidor. Comprueba la dirección y la conexión...» | WARNING | Sí, más tarde |
| Conexión rechazada o sin red (`ConnectionError`) | «No se ha podido conectar con el servidor...» | WARNING | Sí |
| Corte a mitad (`ChunkedEncodingError`, `ProtocolError`, reinicio) | «La conexión se ha cortado mientras se recibían los datos...» | WARNING con bytes recibidos | Sí; las descargas continúan desde el parcial |
| Proxy (`ProxyError`) | «No se ha podido conectar a través del proxy. Revisa la configuración...» | WARNING con proxy redactado | No (revisar configuración) |
| Certificado (`SSLError`) | «No se ha podido comprobar la identidad del servidor... revisa la fecha y la hora» | ERROR con detalle | No |
| 400 | «El servidor no ha entendido la petición (error 400).» | ERROR con traza: suele ser un fallo nuestro | No |
| 401 | «Hace falta iniciar sesión o la sesión ha caducado (error 401).» | WARNING | No: pedir credenciales |
| 403 | «El servidor ha denegado el acceso (error 403).» | WARNING | No |
| 404 / 410 | «No se ha encontrado...» / «ya no existe» | INFO o WARNING | No |
| 408 | «El servidor tardó demasiado en recibir la petición (error 408).» | WARNING | Sí |
| 413 | «El envío es demasiado grande para el servidor (error 413).» | WARNING | No |
| 416 | «El servidor no admite continuar desde ese punto (error 416).» | INFO (la descarga lo resuelve sola) | Reiniciar la descarga |
| 429 | «El servidor está recibiendo demasiadas peticiones; se volverá a intentar más tarde...» | WARNING con `Retry-After` | Sí, tras la espera |
| 500 | «El servidor ha tenido un error interno (error 500).» | WARNING | Sí, pocas veces |
| 502 / 503 / 504 | «no disponible» / «saturado o en mantenimiento» / «tardó demasiado» | WARNING | Sí |
| Otros 4xx / 5xx | «El servidor ha rechazado la petición / ha fallado (error N).» | WARNING | No |
| Demasiadas redirecciones | «El servidor redirige una y otra vez...» | WARNING con la última URL redactada | No |
| URL mal formada | «La dirección no es válida.» | INFO | No |
| Sin permiso al escribir (`PermissionError`) | «No hay permiso para escribir en la carpeta de destino, o el archivo está abierto en otro programa.» | WARNING con la ruta | No: elegir otra carpeta |
| Disco lleno (`ENOSPC`) | «No queda espacio libre en el disco.» | WARNING | No: liberar espacio |
| SHA-256 distinto (`ErrorIntegridad`) | «El archivo descargado está dañado... se ha descartado.» | ERROR con ambas sumas | Sí, una vez |
| Cancelación | «Operación cancelada.» / «Descarga cancelada.» | INFO | No |

## Cómo usarlo en la interfaz

- **Anuncia `mensaje`** con la voz del proyecto o muéstralo en un campo de estado legible. Un diálogo solo si el usuario tiene que decidir algo (reintentar, cambiar la configuración).
- **Botón «&Reintentar»** habilitado si `reintentar`; si hay `espera`, anuncia cuánto falta («Se volverá a intentar en 2 minutos»).
- **Sin cascadas**: si fallan 50 elementos por la misma causa (sin red), un único aviso: «No hay conexión; 50 elementos pendientes».
- Para errores 401, abre directamente el diálogo de iniciar sesión.

## Cómo registrarlo

```python
info = red.clasificar_error(error)
if info.categoria == "cancelado":
	log.info("Descarga cancelada: %s", nombre)
elif info.transitorio:
	log.warning("No se pudo actualizar %s: %s", nombre, info.detalle)
else:
	log.error("No se pudo actualizar %s (%s)", nombre, info.categoria, exc_info=error)
```

- `detalle` ya lleva la URL redactada; no registres `str(error)` de requests sin redactar (incluye la URL completa con sus parámetros).
- Los fallos transitorios repetidos (un sondeo cada minuto sin red) se agrupan: primera vez como aviso y luego un resumen cada cierto tiempo.

## Excepciones propias

Hereda de `ErrorRed` (o pon los atributos `mensaje_usuario`, `categoria`, `reintentar`, `codigo_http`): `clasificar_error` los respeta, y su atributo `transitorio` lo entienden los gestores de tareas que no quieren trazas para fallos pasajeros.

## Fuentes

- Excepciones de requests: <https://requests.readthedocs.io/en/latest/api/#exceptions>
- Excepciones de urllib3: <https://urllib3.readthedocs.io/en/stable/reference/urllib3.exceptions.html>
- Códigos de estado HTTP (RFC 9110, sección 15): <https://www.rfc-editor.org/rfc/rfc9110#name-status-codes>
