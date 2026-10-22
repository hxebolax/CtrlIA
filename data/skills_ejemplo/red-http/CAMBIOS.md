# Cambios del agente red-http

## Cómo ampliarlo

- **Nuevo caso de error**: añade la rama en `clasificar_error` (o el código en `_MENSAJES_HTTP`) de `plantillas/app/core/red.py`, un caso en `test_clasificacion_de_errores` y una fila en `referencias/errores-de-red.md`.
- **Nuevo comportamiento de servidor que probar**: añade una ruta a `plantillas/tests/servidor_http_prueba.py` y su prueba. Nunca pruebas contra Internet.
- **Nueva capacidad de descarga** (segmentada, límite de velocidad...): en `plantillas/app/core/descargas.py`, con su prueba en `test_descargas.py` y su explicación en `referencias/descargas.md`.
- **Nuevo parámetro secreto** que redactar: en `_PARAMETROS_SECRETOS` y un caso en `test_redactar_url`.
- Sube `metadata.version`, anótalo aquí y ejecuta `agentes.bat validar`.

## 1.1.0 - 2026-10-17

- Hallazgos al combinar las plantillas de varios agentes en un mismo proyecto real.
- `red.py` sube el registro `urllib3.connectionpool` a INFO: en DEBUG, urllib3 anotaba la URL completa con los tokens sin redactar. Prueba nueva con el registro raíz en DEBUG. Probado: 37 pruebas.

## 1.0.0 - 2026-09-25

- Primera versión: `crear_sesion` con reintentos de urllib3 (backoff con tope, 429/5xx, `Retry-After` con tope propio, solo métodos seguros, `raise_on_status=False`), adaptador que fuerza tiempos de espera, `LimitadorServidores` (bloqueo por `Retry-After` compartido entre hilos e intervalo mínimo), User-Agent propio, proxy de configuración, del sistema o ninguno, `certifi` con búsqueda junto al ejecutable compilado, sesión por hilo, `instalar_tiempo_global`, `redactar_url`, `segundos_retry_after`, `clasificar_error` recorriendo la cadena de causas y `comprobar_respuesta`; `descargar` con parcial, `Range`/`If-Range`, reinicio ante 200 o rango erróneo, 416 de archivo completo, tamaño y SHA-256, progreso limitado, cancelación, límite de tamaño, bloqueo de https→http, movimiento atómico y nombre libre; `nombre_seguro` y `nombre_desde_respuesta`.
- Probado de verdad: 36 pruebas con pytest contra un servidor `http.server` local (puerto 0, sin Internet) en Python 3.13 con requests 2.34 y urllib3 2.7; también en un entorno propio con Python 3.14 y urllib3 2.8 y con urllib3 1.26.20 (36 de 36 en los tres). Más de veinte ejecuciones seguidas y cuatro rondas de ejecuciones simultáneas sin fallos; un fallo aislado en una ejecución temprana no se pudo reproducir ni identificar, y por prudencia las comprobaciones de tiempo (progreso limitado, tiempo de espera) se hicieron proporcionales o con más margen. Sintaxis comprobada para Python 3.10.
- Hallazgos durante las pruebas: con un `Retry` en el adaptador, requests entrega el tiempo de lectura agotado como `ConnectionError` (por eso se clasifica recorriendo la cadena de causas); urllib3 pierde los bytes del último bloque leído cuando la conexión se corta (la reanudación lo corrige); y en Windows, un servidor local que cierra el socket con datos pendientes puede dejar al cliente esperando sin recibir el resto, así que el corte se simula con un servidor que deja de responder.
- Sin verificar: HTTPS real (las pruebas usan HTTP local; la verificación TLS se comprueba solo por configuración), proxies reales o con autenticación, SOCKS, `truststore`, la versión compilada con cx_Freeze y Python 3.10 ejecutándose de verdad (no estaba instalado).
