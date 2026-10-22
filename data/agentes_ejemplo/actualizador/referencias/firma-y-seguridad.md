# Firma y seguridad

Un actualizador es la vía más directa para meter código en el equipo del usuario: quien controle lo que descarga controla el programa. Estas son las defensas de la plantilla y por qué existen.

## Por qué firma además de SHA-256

- El SHA-256 del manifiesto solo demuestra que el ZIP **no se corrompió por el camino**.
- Si alguien entra en el servidor, cambia el ZIP **y** el SHA-256 del manifiesto a la vez, y la comprobación pasa.
- Con la firma Ed25519 necesitaría además la **clave privada**, que no está en el servidor.
- Se firma el **manifiesto entero** (versión, archivo, tamaño, SHA-256, fecha, novedades, url), no solo «versión + SHA». Así nadie puede cambiar las novedades, la URL o el tamaño sin invalidar la firma.

## Serialización canónica

Lo que se firma son estos bytes, idénticos en la herramienta y en la aplicación (una prueba lo comprueba):

```python
json.dumps(manifiesto_sin_firma, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
```

- `sort_keys` y separadores fijos: el orden de los campos o los espacios del archivo no cambian la firma.
- Sin el campo `firma_ed25519`: la firma no puede firmarse a sí misma.
- Use solo textos y números enteros en el manifiesto. Los decimales pueden escribirse de formas distintas y romper la firma.

## La clave privada

- `python tools/firmar_version.py generar` la guarda en `tools/claves/firma_ed25519.key` (PEM PKCS#8) y crea `tools/claves/.gitignore` con `*`: **nunca** entra en el control de versiones.
- No se sobrescribe una clave existente salvo con `--forzar`: perder la clave con la que confían las instalaciones actuales obliga a una rotación.
- Si define `FIRMA_CONTRASENA` al generarla, se guarda cifrada y hace falta la misma variable al firmar.
- Guarde una copia **fuera del equipo** (un pendrive, un gestor de contraseñas). Si se pierde, las instalaciones actuales no aceptarán ninguna versión nueva: habría que reinstalar a mano.
- Nunca se sube al servidor ni viaja en la compilación: `tools/` no se incluye en cx_Freeze, y conviene que la verificación de la compilación rechace cualquier archivo `.key` o `.pem`.

## La clave pública, incrustada

- Va en `CLAVES_PUBLICAS_ED25519` de `app/services/actualizador.py`, dentro del ejecutable. No se descarga: si se descargara, el atacante enviaría la suya.
- Vacía significa que **no se acepta ninguna actualización** (se falla cerrado).
- Si falta el paquete `cryptography` en la compilación, también se rechaza.

## Rotación de claves

`CLAVES_PUBLICAS_ED25519` es una tupla: se acepta la firma de cualquiera de las claves que contenga.

1. Genere la clave nueva con otra ruta: `python tools/firmar_version.py generar --clave tools/claves/firma_2027.key`.
2. Publique una versión **firmada con la clave antigua** que ya incluya **las dos** claves públicas.
3. Espere a que los usuarios actualicen a esa versión.
4. Publique versiones firmadas con la clave nueva. Cuando nadie use ya versiones anteriores al paso 2, quite la antigua.

Si la clave privada se ve comprometida, haga lo mismo cuanto antes y trate como sospechosas las versiones firmadas desde la fecha del compromiso.

## HTTPS

- Solo se admite `https`. `http` solo contra `localhost`, `127.0.0.1` o `::1`, para las pruebas.
- También se comprueba la URL final tras las redirecciones: un servidor no puede bajar a `http`.
- La firma protege la autenticidad aunque el transporte fallara; https protege además la privacidad (qué versión usa cada uno) y evita manipulaciones que agoten tiempo o disco.

## Anti-rollback

- Ataque: un servidor comprometido sirve una versión **antigua pero legítimamente firmada** que tiene un fallo conocido.
- Defensa: `data/config/actualizaciones.json` guarda `version_confirmada`, la más alta que **arrancó bien** en este equipo. Nunca se acepta una versión inferior.
- Solo se alimenta de `confirmar_arranque` (una versión que de verdad funcionó aquí), nunca de lo que diga el servidor. Si se alimentara del servidor, una errata publicada un momento («10.0» en lugar de «1.0») bloquearía las actualizaciones para siempre.
- Si el archivo está dañado se ignora (con aviso en el registro): se prefiere seguir actualizando a quedarse bloqueado.

Limitación conocida: sin una fecha de caducidad firmada, un atacante puede servir siempre el mismo manifiesto válido y «congelar» al usuario en su versión actual (ataque de congelación). Para aplicaciones de alto riesgo, estudie The Update Framework ([ataques que cubre](https://theupdateframework.io/docs/security/)).

## Extracción segura

Se valida el ZIP completo antes de escribir nada; una entrada peligrosa rechaza el paquete entero:

- Rutas absolutas (`/x`, `\\servidor\x`), letras de unidad y cualquier `:` (también flujos alternativos NTFS como `archivo.txt:flujo`).
- Componentes `..` (el ataque «Zip Slip», [descripción de Snyk](https://security.snyk.io/research/zip-slip-vulnerability)).
- Nombres reservados de Windows (`CON`, `NUL`, `COM1`...) y nombres con punto o espacio final.
- Enlaces simbólicos y archivos cifrados.
- Nombres repetidos sin distinguir mayúsculas (en Windows se pisarían).
- Más de 50 000 entradas o más de `MAX_EXPANDIDO` bytes descomprimidos, midiendo lo que **realmente** se escribe (un ZIP puede mentir en su cabecera).
- Además, cada destino se resuelve y se comprueba que queda dentro de la carpeta de extracción.

## Otras defensas

- El nombre del ZIP del manifiesto solo admite letras, números, `.`, `_` y `-`.
- Campo opcional `app`: si el manifiesto lo trae y no coincide con `NOMBRE_APP`, se rechaza. Úselo si firma varias aplicaciones con la misma clave (mejor aún: una clave por aplicación).
- Tope de descarga igual al tamaño firmado; comparación de SHA-256 en tiempo constante.
- Espacio libre mínimo de cuatro veces el tamaño del ZIP (ZIP, extracción y copia de seguridad).

## Referencias externas

- Ed25519 en `cryptography`: https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/
- Comparación de versiones PEP 440: https://peps.python.org/pep-0440/
- Ataques a sistemas de actualización (TUF): https://theupdateframework.io/docs/security/
