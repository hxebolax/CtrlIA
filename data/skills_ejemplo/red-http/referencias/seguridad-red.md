# Seguridad en la red

## Verificación TLS

- **Nunca `verify=False`** en código que llegue al usuario, ni «temporalmente». Un fallo de certificado es justo el caso en que alguien puede estar interceptando la conexión. `crear_sesion` registra un aviso si se desactiva.
- Tampoco `urllib3.disable_warnings()` para ocultar el problema.
- Las soluciones correctas a un `SSLError`: fecha y hora del equipo, `certifi` presente en la versión compilada, el almacén de Windows con `truststore` en redes corporativas, o un archivo de autoridades propio para un servidor interno (`ConfiguracionRed(verificar="data/certs/interna.pem")`).
- Mantén `certifi` actualizado en cada versión de la aplicación (sus autoridades caducan y se revocan).

## Secretos fuera del registro

- `redactar_url(url)` antes de registrar cualquier URL: sustituye por `***` la contraseña de `usuario:clave@`, los parámetros `token`, `access_token`, `key`, `api_key`, `password`, `secret`, `signature`, `code`, `session`, las firmas de S3 (`X-Amz-Signature`...) y el `access_token` del fragmento.
- Nunca registres cabeceras `Authorization`, `Cookie`, `Set-Cookie`, `Proxy-Authorization`, ni cuerpos de petición con credenciales. Si hay que depurar cabeceras, registra solo sus nombres.
- `str(excepcion)` de requests incluye la URL completa: pásalo por `redactar_url` (lo hace `clasificar_error` en `detalle`).
- Los tokens se envían en cabeceras (`Authorization: Bearer ...`), no en la URL, siempre que la API lo permita: las URLs acaban en registros, historiales y servidores intermedios.
- Tokens y contraseñas del proxy se guardan cifrados (por ejemplo, con DPAPI de Windows o `keyring`), nunca en texto plano en `data/config`.

## Redirecciones

- requests sigue hasta 30 redirecciones por defecto; `crear_sesion` las limita a 10.
- requests quita la cabecera `Authorization` al redirigir a otro servidor; las cabeceras propias con tokens (`X-Api-Key`) **no** las quita: no las pongas en la sesión si hay redirecciones a terceros, pásalas por petición o usa un `auth` propio.
- Una descarga que empieza en `https://` y termina en `http://` se detiene (`permitir_http_tras_https=False`).
- Para URLs que llegan de contenido externo (enlaces en mensajes, vistas previas), desactiva las redirecciones automáticas (`allow_redirects=False`) y valida cada salto: solo `http`/`https` y, si procede, ningún destino en la red local (ver más abajo).

## URLs que no controlas (SSRF)

Si la aplicación descarga URLs que vienen de terceros (vistas previas de enlaces, imágenes de un feed), un enlace malicioso puede apuntar a `http://127.0.0.1:puerto/...`, al router (`192.168.1.1`) o a servicios internos:

- Acepta solo `http` y `https`.
- Resuelve el nombre y rechaza direcciones de bucle local, privadas, de enlace local, multidifusión o reservadas (`ipaddress.ip_address(...).is_private`...). Vuelve a comprobarlo en cada redirección.
- Tiempos cortos (5 s) y tamaño máximo pequeño (por ejemplo, 2 MB para una vista previa).

## Límites de tamaño y descompresión

- `tamano_maximo` en `descargar`: se comprueba con `Content-Length` antes de empezar y contando bytes mientras se descarga (la cabecera puede mentir o faltar).
- Para respuestas que se leen en memoria (JSON, HTML), usa `stream=True` y lee con tope: `iter_content` hasta N bytes y corta.
- **Bombas de descompresión**: requests descomprime `gzip`/`deflate`/`br` automáticamente y `Content-Length` mide los bytes comprimidos. Cuenta los bytes descomprimidos (lo hace `descargar`) o pide `Accept-Encoding: identity`.
- Archivos comprimidos descargados (ZIP): antes de extraer, comprueba el tamaño total declarado, el número de entradas y que ninguna ruta salga de la carpeta de destino (`..`, rutas absolutas); `zipfile.extractall` no protege de todo.

## Nombres y rutas

- El servidor nunca decide la ruta: `nombre_seguro` para cualquier nombre que venga de fuera, y la carpeta la elige el usuario o la aplicación.
- No ejecutes ni abras automáticamente lo descargado. Si la aplicación descarga binarios auxiliares (ffmpeg, actualizaciones), verifica su SHA-256 contra un valor publicado por un canal de confianza y, si es posible, la firma.

## Actualizaciones de la propia aplicación

- Siempre por HTTPS, con verificación TLS y SHA-256 del paquete comprobado antes de instalar.
- El archivo de versión (`version.json`) también por HTTPS; idealmente firmado.
- Descarga a `data/temp` (parcial), verifica, y solo después sustituye.

## Fuentes

- requests, verificación de certificados: <https://requests.readthedocs.io/en/latest/user/advanced/#ssl-cert-verification>
- requests, cabeceras al redirigir: <https://requests.readthedocs.io/en/latest/user/quickstart/#redirection-and-history>
- OWASP, SSRF: <https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html>
- `zipfile`, advertencias de extracción: <https://docs.python.org/3/library/zipfile.html>
