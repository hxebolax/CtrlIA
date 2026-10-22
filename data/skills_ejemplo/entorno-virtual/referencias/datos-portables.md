# La carpeta data: aplicación portable y distribución limpia

## Principio

Todo lo que la aplicación **necesita** o **genera** vive en `data/`, junto a `main.py` (desde código) o junto al `.exe` (compilada). La raíz se calcula así:

```python
if getattr(sys, "frozen", False):
	raiz = Path(sys.executable).resolve().parent
else:
	raiz = Path(__file__).resolve().parent.parent.parent  # <raíz>/app/core/paths.py
```

Nunca uses `os.getcwd()` (un acceso directo o el Explorador pueden cambiarlo), ni `%APPDATA%`, `%LOCALAPPDATA%`, `tempfile.gettempdir()` o el registro, salvo petición expresa del usuario. Un único módulo (`app/core/paths.py`) resuelve todas las rutas; el resto del código no construye rutas a mano.

## Qué hay en data/

| Subcarpeta | Contenido | ¿Se distribuye? |
|---|---|---|
| `bin/` | Binarios auxiliares (ffmpeg, aria2c) | Sí (verificar SHA-256 si procede) |
| `sounds/` | Sonidos de la interfaz por defecto | Sí (o se generan en el primer arranque) |
| `locales/` | Traducciones `.mo` (`<idioma>/LC_MESSAGES/<app>.mo`) | Sí |
| `docs/` | Manual de usuario y lista de teclas | Sí (no la documentación de desarrollo) |
| `icons/` | Iconos | Sí |
| `plugins/` | Complementos incluidos de fábrica | Sí (sin `__pycache__`) |
| `config/` | `settings.json`, secretos cifrados | **No**: se crea en el primer arranque |
| `logs/` | Registros | **No** |
| `db/` o `database/` | SQLite | **No**: se crea y migra al arrancar |
| `cache/`, `temp/` | Cachés y temporales | **No** |
| `sessions/` | Sesiones iniciadas en servicios externos | **No**, nunca |
| `backups/`, `downloads/` | Copias y descargas del usuario | **No** |

## Primer arranque

La configuración por defecto vive **en el código** (un diccionario de valores predeterminados), no en un archivo distribuido: si falta `data/config/settings.json`, se crea con esos valores. Así la distribución no arrastra preferencias del desarrollador y una configuración antigua sin claves nuevas sigue funcionando (se completan con los valores por defecto).

Las carpetas se crean **bajo demanda** (`paths.subcarpeta("x")`), no todas al arrancar.

## Pruebas sin tocar la data real

`paths.ruta_datos()` respeta la variable `APP_DATA_DIR`. En pytest:

```python
def test_algo(tmp_path, monkeypatch):
	monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
	...
```

Si el proyecto ya usa otra variable con el mismo fin (`<APP>_DATA_DIR`), respeta el nombre que tenga.

## Cómo garantiza la plantilla una distribución limpia

1. **Lista blanca** `DATA_DISTRIBUIBLE` en `setup_cx.py`: lo nuevo no se cuela sin querer.
2. **Lista negra de seguridad** `DATA_PROHIBIDO`: si alguien añade `config` a la lista blanca, la compilación se detiene.
3. **Copia archivo a archivo** filtrando `PATRONES_EXCLUIDOS` (`__pycache__`, `*.log`, `*.db`, `*.key`...).
4. **Borrado del destino** antes de compilar (restos de haber ejecutado la copia compilada).
5. **Verificación posterior** (`tools/verificar_build.py`) y de nuevo antes de comprimir (`tools/empaquetar.py`).
6. **`.gitignore`** con las carpetas generadas, para que tampoco lleguen al repositorio.

## Qué revisar en el código al adoptar esta política

- Búsquedas: `APPDATA`, `LOCALAPPDATA`, `gettempdir`, `mkdtemp`, `expanduser`, `Path.home()`, `os.getcwd()`, `winreg`. Cada resultado debe estar justificado (por ejemplo, la carpeta de descargas del usuario o el arranque con Windows cuando el usuario lo activa).
- Bibliotecas que guardan por su cuenta (cachés de descargadores, cookies, perfiles de navegador...): configúralas para que usen una subcarpeta de `data/`.
- `tempfile.NamedTemporaryFile(dir=paths.ruta_temporales())` en lugar del temporal del sistema.
