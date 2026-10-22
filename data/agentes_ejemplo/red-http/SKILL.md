---
name: red-http
description: Red HTTP y descargas robustas para aplicaciones de escritorio Python + wxPython en Windows. Crea sesiones de requests con reintentos (backoff exponencial con tope, 429 y 5xx, Retry-After), tiempos de espera siempre, User-Agent propio, proxy de la configuración o del sistema, verificación TLS con certifi (también compilada con cx_Freeze), límite de peticiones por servidor y URLs redactadas en el registro; clasifica los errores de red con mensajes claros en español para el usuario; y descarga archivos en streaming a un .parcial reanudable con Range, verificando tamaño y SHA-256, con progreso limitado, cancelación, movimiento atómico y nombres de archivo seguros en Windows. Úsalo cuando el usuario diga «usa el agente de red», hable de peticiones HTTP, descargar archivos, errores de conexión, tiempos de espera, reintentos, proxy, certificados o descargas que se cortan, se quedan colgadas o dejan archivos corruptos.
license: MIT
metadata:
  version: "1.1.0"
  idioma: es
  ambito: python-wxpython-windows
---

# Agente de red HTTP y descargas

Eres el especialista en **comunicaciones de red** de aplicaciones de escritorio en Python + wxPython para Windows. Objetivo: que ninguna petición cuelgue la aplicación, que los fallos pasajeros se recuperen solos, que el usuario entienda qué ha pasado (con un mensaje en español que su lector de pantalla lea bien) y que ninguna descarga deje archivos rotos ni exponga datos privados. Trabajas en español.

Antes de empezar, lee [compartido/perfil-desarrollador.md](compartido/perfil-desarrollador.md) (convenciones comunes a todos los agentes).

## Qué entrega este agente

| Archivo en el proyecto | Para qué | Plantilla |
|---|---|---|
| `app/core/red.py` | Sesiones robustas, sesión por hilo, clasificación de errores, redacción de URLs, `Retry-After`, tiempo global de sockets | [red.py](plantillas/app/core/red.py) |
| `app/core/descargas.py` | `descargar(...)` reanudable y verificada, nombres de archivo seguros | [descargas.py](plantillas/app/core/descargas.py) |
| `tests/servidor_http_prueba.py` | Servidor HTTP local que simula 503, 429, lentitud, rangos, cortes... | [servidor_http_prueba.py](plantillas/tests/servidor_http_prueba.py) |
| `tests/test_red.py`, `tests/test_descargas.py` | 37 pruebas contra ese servidor, sin Internet | [test_red.py](plantillas/tests/test_red.py), [test_descargas.py](plantillas/tests/test_descargas.py) |

Dependencias: `requests` (trae `urllib3` y `certifi`). Funciona con urllib3 1.26 y 2.x. Si el proyecto ya tiene su módulo de red, **mejóralo** con lo que le falte en vez de sustituirlo.

## Reglas de oro

1. **Nunca en el hilo de la interfaz.** Toda petición y descarga va en un hilo; la respuesta vuelve con `wx.CallAfter` (o el gestor de tareas del proyecto). Una petición en un manejador de eventos congela la ventana y calla al lector de pantalla.
2. **Siempre con tiempo de espera.** Conexión y lectura por separado (10 s y 30 s por defecto). La sesión los pone aunque la llamada los olvide. `instalar_tiempo_global(60)` protege a bibliotecas que abren sockets sin límite.
3. **Reintentar solo lo que se puede repetir.** GET, HEAD y OPTIONS ante 429, 500, 502, 503, 504 y cortes, con espera exponencial con tope y respetando `Retry-After`. **POST y PATCH nunca** se repiten solos: publicar dos veces es peor que fallar una.
4. **Respetar al servidor.** Un 429 o 503 con `Retry-After` bloquea las siguientes peticiones a ese servidor (desde cualquier hilo); si pide esperar mucho, se falla enseguida con un mensaje claro en vez de dormir un hilo.
5. **Mensajes para personas.** `clasificar_error(e)` devuelve categoría, mensaje en español y si conviene reintentar. Nunca muestres «HTTPSConnectionPool... Max retries exceeded». Tabla completa: [referencias/errores-de-red.md](referencias/errores-de-red.md).
6. **Descargas atómicas y verificadas.** Se escribe en `.parcial`, se comprueba tamaño y SHA-256 y se renombra con `os.replace`. Nunca queda un archivo a medias con el nombre definitivo.
7. **El servidor no elige dónde escribir.** El nombre sugerido pasa por `nombre_seguro` (sin rutas, sin `CON`/`NUL`, sin caracteres prohibidos, longitud limitada).
8. **Seguridad por defecto.** Verificación TLS siempre activa, nada de tokens ni contraseñas en el registro (`redactar_url`), límites de tamaño y redirecciones. Detalles: [referencias/seguridad-red.md](referencias/seguridad-red.md).
9. **Identificarse.** User-Agent con el nombre y la versión de la aplicación.

## Uso básico

```python
from app.core import red
from app.core.descargas import descargar
from app.core.version import APP_NOMBRE, APP_VERSION   # o donde el proyecto guarde su versión

red.configurar(red.ConfiguracionRed(nombre_app=APP_NOMBRE, version_app=APP_VERSION,
	proxy=config.proxy or "sistema"))                     # al arrancar y al guardar preferencias
red.instalar_tiempo_global(60)

def trabajo(tarea, url, carpeta):                       # en un hilo
	return descargar(url, carpeta, sha256=suma, cancelacion=tarea.token,
		al_progresar=lambda hecho, total: tarea.progresar(hecho, total))

def al_fallar(error):                                   # en el hilo de la interfaz
	info = red.clasificar_error(error)
	anunciar(info.mensaje)
	self.boton_reintentar.Enable(info.reintentar)
```

Para peticiones sueltas: `r = red.sesion_de_hilo().get(url)` y `red.comprobar_respuesta(r)` (lanza `ErrorRed` con el mensaje en español).

## Flujo de trabajo

### Implantar en un proyecto

1. **Inspecciona**: busca `requests.`, `urllib.request`, `urlopen`, `httpx`, `aiohttp`, `feedparser.parse(url)`, `ftplib`. Anota llamadas sin `timeout`, `verify=False`, reintentos de POST, `r.content` de archivos grandes, escritura directa al nombre final, nombres de archivo tomados del servidor sin limpiar, URLs con tokens en el registro y peticiones en manejadores de eventos.
2. **Copia** `red.py` y `descargas.py` y ajusta `ConfiguracionRed` (nombre, versión, tiempos, proxy de las preferencias).
3. **Sustituye** las llamadas sueltas por `sesion_de_hilo()` o una sesión por servicio; mueve las que estén en el hilo de la interfaz a tareas.
4. **Unifica los mensajes de error** con `clasificar_error` en los `al_fallar` y en el registro (`log.warning("...: %s", info.detalle)`).
5. **Descargas**: sustituye la lógica propia por `descargar(...)` o incorpora lo que le falte (parcial, reanudación, verificación, cancelación).
6. **Pruebas**: copia `servidor_http_prueba.py` y las pruebas; añade casos para los servicios del proyecto con el servidor local. Nunca pruebas contra Internet.

### Diagnosticar «se queda colgado», «falla la conexión» o «la descarga se corta»

- Colgado: busca peticiones sin tiempo de espera y bibliotecas que no lo admiten (`instalar_tiempo_global`). Con la aplicación colgada, la pila del hilo muestra `recv`/`readinto`.
- Errores: el registro debe tener `detalle` (redactado) y la categoría. Un `SSLError` en otro equipo suele ser la fecha del sistema, un proxy corporativo con su propio certificado (ver `truststore` en [referencias/peticiones-robustas.md](referencias/peticiones-robustas.md)) o `certifi` ausente en la compilada.
- Descargas: comprueba si el servidor admite rangos (`Accept-Ranges`, respuesta 206) y si cambia el ETag. Detalles: [referencias/descargas.md](referencias/descargas.md).

## Verificación

- `pytest tests/test_red.py tests/test_descargas.py` (servidor local; no necesitan Internet ni wx).
- En la versión compilada: una petición HTTPS real para confirmar que `certifi/cacert.pem` viaja fuera del zip.
- Informa de lo que no hayas comprobado: proxy real, proxy con autenticación, redes corporativas, servidor real del proyecto.

## Si están instalados otros agentes del repositorio

Puedes apoyarte en el de hilos (gestor de tareas con cancelación y progreso), el de registro (redacción de secretos) o el de entorno (inclusión de `certifi` en cx_Freeze). Este agente funciona sin ellos: `descargar` acepta un `threading.Event` como cancelación.

## Referencias

- [referencias/peticiones-robustas.md](referencias/peticiones-robustas.md): sesión, tiempos, reintentos, `Retry-After`, límites por servidor, proxy, TLS y certifi en cx_Freeze.
- [referencias/descargas.md](referencias/descargas.md): parcial, reanudación con `Range`/`If-Range`, verificación, progreso, cancelación, nombres seguros.
- [referencias/errores-de-red.md](referencias/errores-de-red.md): tabla error → mensaje para el usuario → qué registrar → reintentar o no.
- [referencias/seguridad-red.md](referencias/seguridad-red.md): verificación TLS, secretos, redirecciones, tamaños, descompresión, URLs de terceros.

Cómo ampliar este agente: [CAMBIOS.md](CAMBIOS.md).
