# Publicar una versión

Lista de comprobación para publicar una versión que las instalaciones existentes acepten y puedan instalar sin riesgo. Siga el orden.

## Antes de compilar

1. **Suba la versión** en la única fuente de verdad (`APP_VERSION` en `app/core/version.py` o equivalente). Debe ser **mayor** que la publicada: el actualizador compara como PEP 440 (`1.2.0 < 1.2.1`, `1.3.0rc1 < 1.3.0`, `1.3.0-beta.2 < 1.3.0`).
2. Si el proyecto también declara la versión en `pyproject.toml`, que coincida.
3. **Escriba las novedades** en un archivo de texto (por ejemplo `NOVEDADES.txt`): frases cortas, una por línea, empezando por guion. Se leen en un cuadro de solo lectura con el lector de pantalla: nada de tablas ni marcas de formato.
4. Pase las pruebas: `python -m pytest tests`.
5. Si cambia la clave de firma en esta versión, siga antes el procedimiento de rotación de [firma-y-seguridad.md](firma-y-seguridad.md).

## Compilar y empaquetar

1. **Compile desde cero** (borrando la carpeta de compilación anterior): una compilación ya ejecutada tiene datos del desarrollador en `data/`.
2. Compruebe que `data/` de la compilación solo tiene recursos (bin, sounds, locales, docs, icons) y nunca config, db, logs, sesiones ni claves.
3. Empaquete en un ZIP con **todo dentro de una carpeta** con el nombre de la aplicación y genere `version.json` con `version`, `file`, `sha256`, `size` y `date`.
4. Compruebe que el ejecutable está en la raíz de esa carpeta: si no, el actualizador rechazará el paquete.

## Firmar

```
python tools/firmar_version.py firmar build/version.json --novedades NOVEDADES.txt
python tools/firmar_version.py verificar build/version.json
```

- `firmar` añade las novedades y el campo `firma_ed25519`. Después de firmar **no edite** el manifiesto: cualquier cambio invalida la firma. Si necesita cambiar algo, vuelva a firmar.
- `verificar` comprueba la firma y, si el ZIP está en la misma carpeta, su tamaño y su SHA-256. Debe decir «Firma válida» y terminar con código 0.

## Subir

1. Suba **primero el ZIP** y **después `version.json`**. Al revés, durante unos segundos las instalaciones verían una versión cuyo ZIP aún no existe.
2. Compruebe desde un navegador que las dos direcciones responden por **https**.
3. Si el servidor usa caché o CDN, invalide `version.json`.

## Probar la actualización desde la versión anterior

Hágalo siempre, no solo con la primera versión:

1. Descomprima el ZIP de la **versión anterior** en una carpeta nueva (por ejemplo, el escritorio) y ábrala una vez para que cree su `data/`.
2. Añada algo propio: un ajuste cambiado, un dato en la base de datos.
3. Use «Ayuda, Buscar actualizaciones...». Debe ofrecer la versión nueva con sus novedades.
4. Pulse «Actualizar ahora». La aplicación se cierra y vuelve a abrirse sola en unos segundos.
5. Compruebe en «Acerca de» la versión nueva, que sus ajustes y datos siguen ahí, y que `data/logs/actualizacion.log` termina con «Arranque confirmado».
6. Compruebe con el lector de pantalla: el diálogo se lee al abrirse, las novedades se recorren con las flechas, el progreso se anuncia por hitos y los errores se leen.

## Si algo sale mal después de publicar

- **Versión rota:** publique cuanto antes una versión **mayor** con el arreglo. No vuelva a publicar la anterior con su número: el anti-rollback la rechaza en quien ya confirmó la nueva.
- **Errata en el número de versión** (por ejemplo `10.0` en lugar de `1.0.1`): corríjala en cuanto la vea. Solo afecta a quien llegó a instalar y arrancar esa versión, que tendrá que recibir versiones mayores que ella.
- Las instalaciones en las que la versión nueva no arranque vuelven solas a la anterior y avisan al usuario (ver [vuelta-atras.md](vuelta-atras.md)).
