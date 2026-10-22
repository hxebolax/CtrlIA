# Peticiones robustas

## Una sesión, no llamadas sueltas

`requests.get(...)` suelto abre y cierra una conexión TLS en cada llamada. Una `requests.Session` reutiliza conexiones (keep-alive), comparte cabeceras y permite montar un adaptador con reintentos y tiempos por defecto.

- `crear_sesion(ConfiguracionRed(...))` para un servicio concreto (por ejemplo, una por cuenta si cada una tiene su token).
- `sesion_de_hilo()` para peticiones generales: una sesión por hilo. requests no garantiza que una sesión sea segura si varios hilos la usan a la vez.
- `configurar(nueva)` tras cambiar las preferencias de red: cada hilo recrea su sesión en el siguiente uso.

## Tiempos de espera

- `timeout=(conexión, lectura)`. Conexión: lo que se tarda en establecerla (10 s es generoso). Lectura: tiempo máximo **entre dos bloques recibidos**, no la duración total de la descarga (30 s).
- requests **no tiene tiempo de espera por defecto**: sin él, un servidor que acepta la conexión y no contesta deja el hilo esperando para siempre. El adaptador de `red.py` sustituye `timeout=None` por los valores de la configuración.
- Para un tiempo total (por ejemplo, «esta consulta no puede durar más de 20 s») combina el tiempo de lectura con `tiempo_maximo` del gestor de tareas o una comprobación del reloj al leer en streaming.
- `instalar_tiempo_global(60)` (`socket.setdefaulttimeout`) es la red de seguridad para bibliotecas que abren sockets sin permitir indicar límite (`urllib.request`, `ftplib`, `feedparser` al descargar). Afecta a todos los sockets creados después, también a los que escuchan: si la aplicación abre un servidor local, ponle `settimeout(None)` explícito.

## Reintentos

Política de `red.py` (urllib3 `Retry`):

- `total`, `connect`, `read` y `status` = 3 por defecto.
- `status_forcelist` = 429, 500, 502, 503, 504.
- `allowed_methods` = GET, HEAD, OPTIONS. PUT y DELETE son idempotentes según RFC 9110, pero muchas API no lo cumplen; añádelos solo si la del proyecto lo garantiza. POST y PATCH, nunca.
- Espera exponencial: el primer reintento es inmediato y después `factor_espera * 2 ** (fallos_seguidos - 1)` (con 0,5: 0 s, 1 s, 2 s, 4 s...), con tope `espera_maxima` (30 s). urllib3 2.x admite además `backoff_jitter` para repartir en el tiempo los reintentos de muchos clientes.
- `respect_retry_after_header=True`: ante 429, 503 (y 413) con `Retry-After`, espera lo que pida el servidor. Con tope propio (`espera_maxima_servidor`, 120 s): urllib3 no lo limitaba hasta la 2.6 y desde entonces su máximo por defecto es de 6 horas.
- `raise_on_status=False`: al agotar los reintentos se devuelve la última respuesta (503, 429...) y el código decide con `comprobar_respuesta` o `clasificar_error(respuesta=r)`.
- Con un `Retry` en el adaptador, requests entrega un tiempo de lectura agotado como `requests.ConnectionError` que envuelve `ReadTimeoutError`, no como `ReadTimeout`. `clasificar_error` recorre la cadena de causas para dar el mensaje correcto; no compares por tipo de excepción a mano.
- Reintentos «de usuario» (botón «Reintentar», reintento de una descarga tras un corte) son aparte: los decide la interfaz con `info.reintentar`.

## Límite de peticiones por servidor

`LimitadorServidores` (uno por sesión):

- Tras un 429 o 503 con `Retry-After`, las siguientes peticiones a ese servidor esperan el tiempo pedido si es corto, o fallan al instante con `ErrorRed` («El servidor ha pedido que esperemos unos N minutos...») si supera `espera_maxima_servidor`. Así no se despierta a un servidor saturado desde cinco hilos a la vez ni se duerme un hilo una hora.
- `intervalo_minimo_por_servidor`: separación mínima entre peticiones (para API con límites estrictos o sondeos de muchos elementos del mismo sitio).
- Para sondeos periódicos, además: intervalos razonables, `If-None-Match`/`If-Modified-Since` (respuesta 304 sin cuerpo) y no sondear lo que no está visible.

## User-Agent

`Nombre/1.2.3 (Windows; +contacto) python-requests/2.x`. Identifica la aplicación ante el servidor, permite al administrador contactar si algo va mal y evita bloqueos de servicios que rechazan el User-Agent genérico. No incluyas datos del usuario.

## Proxy

- `proxy="sistema"` (por defecto): requests usa las variables `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` y, en Windows, la configuración de proxy de Internet del sistema (a través de `urllib.request.getproxies`). Los archivos PAC (configuración automática) no se interpretan.
- `proxy="ninguno"`: `trust_env=False` (ignora también `.netrc` y `REQUESTS_CA_BUNDLE`).
- Una URL (`http://servidor:8080`, `http://usuario:clave@servidor:8080`, `socks5h://servidor:1080`): se aplica a http y https. SOCKS necesita `requests[socks]` (PySocks); `socks5h` resuelve los nombres a través del proxy.
- La contraseña del proxy se guarda cifrada en la configuración y nunca aparece en el registro (`redactar_url` la sustituye por `***`).
- En las preferencias: «Proxy: del sistema / ninguno / manual», con campos etiquetados y un botón «Probar conexión» que ejecute una petición en un hilo y anuncie el resultado.

## Certificados y TLS

- `verify` apunta a `certifi.where()` (la lista de autoridades de Mozilla). Nunca `verify=False` fuera de pruebas locales.
- **cx_Freeze**: `certifi` debe ir en `packages` y **fuera** de `zip_include_packages`, para que `lib/certifi/cacert.pem` sea un archivo real. `ruta_certificados()` lo busca primero junto al ejecutable. Comprueba en la compilada que existe y haz una petición HTTPS de humo.
- **Redes corporativas** con inspección TLS (su proxy firma con un certificado propio instalado en Windows): certifi no lo conoce y todo falla con `SSLError`. La solución es usar el almacén de certificados de Windows con el paquete `truststore` (`truststore.inject_into_ssl()` al arrancar, Python 3.10+), ofrecido como opción, o permitir indicar un archivo de autoridades propio (`ConfiguracionRed(verificar="ruta.pem")`).
- Un `SSLError` en un solo equipo suele deberse a la fecha y hora del sistema.

## Registro

- Cada respuesta deja una línea DEBUG: método, URL redactada, estado y duración.
- Los errores se registran donde se manejan, con `info.detalle` (ya redactado) y la categoría. Los transitorios en WARNING de una línea; los inesperados en ERROR con traza.
- Nunca se registran cuerpos de petición o respuesta con datos del usuario, cabeceras `Authorization` ni `Cookie`.

## Fuentes

- requests, tiempos de espera: <https://requests.readthedocs.io/en/latest/user/advanced/#timeouts>
- requests, sesiones y adaptadores: <https://requests.readthedocs.io/en/latest/user/advanced/#session-objects>
- urllib3 `Retry` (backoff, `backoff_max`, `Retry-After`, `retry_after_max`): <https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html>
- RFC 9110, métodos idempotentes y `Retry-After`: <https://www.rfc-editor.org/rfc/rfc9110>
- certifi: <https://github.com/certifi/python-certifi>
- truststore: <https://truststore.readthedocs.io/>
