# Secretos: tokens, contraseñas y claves de API

Cómo guarda `app/core/secretos.py` los secretos, qué protege y qué no. Probado en `tests/test_secretos.py` (con DPAPI real en Windows).

## Reglas

1. **Nunca en `settings.json`.** La configuración se copia, se exporta, se adjunta a un informe de error y se abre con el Bloc de notas. Los secretos van en `data/config/secretos.dat`.
2. **Nunca en el registro (logs)** ni en mensajes de error: como mucho, el nombre del secreto. Si el proyecto tiene redacción de secretos en su registro, registra cada valor en cuanto se lee para que se tape si aparece en una traza.
3. **Nunca en la distribución.** `data/config` no se empaqueta.
4. **Un solo archivo, cifrado entero**: ni los nombres de los secretos van en claro.
5. **Leer-modificar-escribir bajo candado.** Dos hilos que renuevan tokens de dos cuentas a la vez no deben pisarse (hay prueba con 10 hilos).
6. **Escritura atómica** (temporal único + `fsync` + `os.replace`).
7. **Un archivo ilegible no se sobrescribe**: se aparta como `secretos.dat.ilegible-FECHA` antes de empezar uno nuevo, por si se puede recuperar en el equipo original.

## DPAPI (por defecto)

`CryptProtectData`/`CryptUnprotectData` de `crypt32.dll`, llamadas con `ctypes` (sin dependencias).

- Cifra con una clave derivada de las credenciales del **usuario de Windows**. Solo ese usuario, normalmente en ese equipo, puede descifrar. Con perfiles móviles de dominio también desde otros equipos de la red.
- Incluye un código de autenticación: un archivo manipulado no se descifra (se detecta).
- La plantilla usa:
  - `CRYPTPROTECT_UI_FORBIDDEN`: nunca un diálogo del sistema (el lector no sabría de dónde sale y bloquearía la aplicación). Microsoft retira además los avisos interactivos de DPAPI en 2027.
  - **Entropía propia de la aplicación** (`ENTROPIA_APLICACION`): no es secreta, pero impide que otro programa del mismo usuario descifre nuestros datos llamando a DPAPI sin más. Cámbiala por una propia **antes** de guardar nada; si la cambias después, los secretos dejan de leerse.
  - `argtypes`/`restype` explícitos (sin ellos, en 64 bits ctypes puede truncar punteros) y `LocalFree` del búfer de salida (lo reserva Windows).
- **No uses `CRYPTPROTECT_LOCAL_MACHINE`** salvo que se pida: cualquier usuario del equipo podría descifrar.

### La contrapartida con la portabilidad

La aplicación es portable, pero DPAPI no: si el usuario copia la carpeta a otro equipo, o la abre otro usuario de Windows, `secretos.dat` no se puede descifrar. Es lo que se busca (quien se lleva la carpeta no se lleva las contraseñas), pero hay que gestionarlo:

- `AlmacenSecretos.ilegibles` queda a `True` y se registra un aviso.
- La interfaz debe decirlo claramente al arrancar: «Las credenciales guardadas son de otro equipo o de otro usuario. Vuelve a introducirlas en Preferencias». Mejor en un diálogo accesible que solo por voz.
- Al guardar el primer secreto nuevo, el archivo ilegible se aparta (no se borra).
- Para **cambiar de equipo** sin reintroducir nada, ofrece una exportación explícita protegida con una contraseña que elija el usuario (por ejemplo, Fernet con clave derivada por PBKDF2 o scrypt de esa contraseña), nunca una exportación en claro.

## Alternativa: Fernet con clave en `data` (ofuscación)

`ProteccionFernet(ruta_clave)` usa `cryptography.Fernet` con una clave aleatoria en `data/config/secretos.key`.

- Ventaja: la carpeta funciona en cualquier equipo.
- **Es ofuscación, no protección**: la clave viaja junto a los datos. Evita que alguien lea un token abriendo el archivo, pero quien tenga la carpeta completa tiene todo.
- Úsala solo si el usuario lo pide sabiéndolo, y dilo en la documentación.
- Variante más segura: derivar la clave de una contraseña maestra que el usuario escribe al arrancar (`PBKDF2HMAC` o `Scrypt` de `cryptography`, con sal guardada en `data`). Protege de verdad, a cambio de pedir la contraseña.

## Alternativa: keyring (Administrador de credenciales de Windows)

```python
import keyring
keyring.set_password("MiAplicacion", "cuenta1.token", token)
token = keyring.get_password("MiAplicacion", "cuenta1.token")
```

- Guarda en el Administrador de credenciales de Windows (protegido también con DPAPI).
- **Rompe la regla de portabilidad**: el secreto queda fuera de `data`, en el perfil del usuario. Al borrar la carpeta, las credenciales se quedan en el sistema. Úsalo solo si el usuario lo pide expresamente.
- Límite de tamaño por credencial: 2560 bytes (`CRED_MAX_CREDENTIAL_BLOB_SIZE`); con UTF-16, unos 1280 caracteres. Tokens largos (JWT) pueden no caber.
- Dependencia extra que cx_Freeze debe incluir (usa puntos de entrada para elegir el motor: comprueba la versión compilada).

## Uso

```python
from app.core.secretos import obtener_secretos

secretos = obtener_secretos()
secretos.guardar("cuenta1.token", token)
token = secretos.obtener("cuenta1.token")
secretos.borrar("cuenta1.token")
secretos.guardar_varios({"proxy.usuario": u, "proxy.contrasena": c})  # una escritura
```

- Nombres con el patrón `ambito.dato` (`cuenta1.token`, `proxy.contrasena`).
- Guarda todos los datos de un alta de cuenta con `guardar_varios`: si falla, no queda la cuenta a medias.
- `obtener` descifra el archivo en cada llamada: es rápido (milisegundos), pero no lo llames en un bucle de pintado; guarda el valor en memoria en el objeto que lo usa.

## Migrar secretos que estaban en claro en el JSON

En una migración de configuración:

```python
def _migrar_a_4(datos: dict) -> None:
	red = datos.get("red", {})
	contrasena = red.pop("contrasena_proxy", None)
	if contrasena:
		obtener_secretos().guardar("red.contrasena_proxy", contrasena)
```

- Si guardar el secreto falla, la excepción se registra en la migración y el valor se pierde del JSON: valora hacer la migración en dos pasos (guardar el secreto y, solo si fue bien, borrarlo del JSON).
- Después de migrar, revisa las copias `settings.json.bak` y `settings.json.corrupto-*`: pueden seguir conteniendo el secreto en claro. Sobrescribe el `.bak` con un guardado nuevo.

## Fuentes

- Microsoft, `CryptProtectData`: <https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata>
- Microsoft, `CryptUnprotectData`: <https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata>
- cryptography, Fernet: <https://cryptography.io/en/latest/fernet/>
- keyring: <https://pypi.org/project/keyring/>
