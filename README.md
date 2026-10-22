# Ctrl+IA

Ctrl+IA («Control IA») es un programa para Windows que reúne en una sola ventana, accesible con lector de pantalla y teclado, tus conversaciones con los agentes de IA de programación: Claude Code, Codex, Antigravity, Gemini CLI, Ollama y cualquier agente compatible con ACP, en tu equipo o en otro por SSH.

- Versión actual: 1.5.1.
- Autor: Héctor J. Benítez Corredera.
- Licencia: GNU GPL versión 3 (ver «Licencia»).
- Repositorio: <https://github.com/hxebolax/CtrlIA>.

Este documento tiene dos partes: la primera es para quien quiere usar Ctrl+IA; la segunda, para quien quiere compilarlo, modificarlo o contribuir.

## Índice

- Para usar Ctrl+IA
  - Qué es y para quién
  - Agentes que admite
  - Qué necesitas
  - Descargar e instalar
  - Actualizar
  - Primeros pasos
  - Manual y lista de teclas
  - Privacidad
  - Ayuda, fallos y sugerencias
  - Donaciones
- Para programadores
  - Requisitos de desarrollo
  - Preparar el entorno
  - Ejecutar desde el código
  - Pruebas y comprobaciones
  - Traducciones
  - Compilar y empaquetar
  - Firma de las versiones
  - Publicar una versión (solo el autor)
  - Subir cambios a GitHub
  - Estructura del proyecto
  - Normas para contribuir
- Licencia

# Para usar Ctrl+IA

## Qué es y para quién

Un agente de IA de programación, además de conversar, lee los archivos de tu proyecto, los modifica y ejecuta órdenes. Cada empresa ofrece el suyo con su propio programa, y no todos son cómodos con un lector de pantalla. Ctrl+IA los reúne en una interfaz única, en español, pensada desde el principio para NVDA, JAWS y Narrador:

- Una lista de sesiones y una lista de mensajes que se leen bien, con columnas configurables.
- Avisos por voz, braille, sonidos y barra de estado cuando el agente responde, termina o te necesita.
- Diálogos de permisos y preguntas claros, en los que Intro siempre elige la opción inofensiva.
- Todo se maneja con el teclado, con atajos en los menús y ayuda de cada control con Ctrl+H.
- Un catálogo de skills (carpetas con un SKILL.md con instrucciones para un trabajo concreto) que eliges en cada sesión, con cualquier agente. Trae 14 de ejemplo para programar aplicaciones wxPython accesibles.

Está pensado para personas ciegas o con baja visión que programan, y para cualquiera que prefiera trabajar con varios agentes desde una sola ventana.

Ctrl+IA no es un modelo de IA ni un servicio de pago: abre el programa oficial de cada agente con la cuenta en la que ya iniciaste sesión. Lo que hagas se cobra como si usaras el agente directamente, normalmente dentro de tu suscripción. Ctrl+IA no cobra nada y nunca usa por su cuenta claves de API de pago.

## Agentes que admite

| Agente | Empresa | Cuenta que necesitas |
| --- | --- | --- |
| Claude Code | Anthropic | Tu suscripción de Claude |
| Codex | OpenAI | Tu cuenta de ChatGPT |
| Antigravity (CLI `agy`, Antigravity IDE y su app de escritorio) | Google | Tu suscripción de Google |
| Gemini CLI | Google | Licencia de empresa o clave de API de pago (con suscripción personal, usa Antigravity) |
| Ollama (modelos en tu equipo, solo chat) | Ollama | Ninguna, gratis |
| Otros agentes ACP: OpenCode, Goose, GitHub Copilot CLI, Qwen Code, Kilo Code, Cline y los que añadas | Varias | La de cada agente |

Los agentes pueden trabajar en tu equipo o en un Mac o un Linux al que entras por SSH. El manual detalla qué puede hacer cada uno.

## Qué necesitas

- Windows 10 u 11 de 64 bits.
- Un lector de pantalla si lo usas (pensado para NVDA y JAWS); sin él, Ctrl+IA habla con su propia voz.
- Al menos un agente instalado y con sesión iniciada en tu cuenta. Ctrl+IA te ayuda a instalarlos desde Herramientas, «Agentes instalados».
- Conexión a Internet (salvo con Ollama).
- Para algunas funciones: Git para Windows (deshacer cambios de archivos y copias aisladas), `gh`, la herramienta de GitHub, con `gh auth login` hecho una vez (clonar repositorios y estado de las PR), Node.js (instalar Codex, Gemini CLI y los adaptadores ACP) y el cliente OpenSSH que ya trae Windows (agentes en otro equipo).

## Descargar e instalar

1. Ve a la página de versiones: <https://github.com/hxebolax/CtrlIA/releases>.
2. En la última versión, descarga el archivo `CtrlIA_v<versión>.zip` (por ejemplo, `CtrlIA_v1.5.1.zip`).
3. Extrae el ZIP en la carpeta que quieras (por ejemplo, en tus Documentos o en un pendrive). Se crea una carpeta `CtrlIA`.
4. Abre `CtrlIA.exe`. Si Windows SmartScreen avisa, elige «Más información» y «Ejecutar de todas formas».

Ctrl+IA es portable: no se instala ni toca el registro de Windows. Todo lo que guarda (configuración, sesiones, registros) está en la carpeta `data`, junto al programa. Para llevártelo a otro equipo, copia la carpeta entera; para desinstalarlo, bórrala (copia antes `data` si quieres conservar tus sesiones).

## Actualizar

Ctrl+IA busca versiones nuevas por su cuenta una vez al día, en el servidor del autor y en GitHub. También puedes buscarlas cuando quieras con Ayuda, «Buscar actualizaciones...». Si hay una, te muestra sus novedades y la instala con «Actualizar ahora»:

- Antes de instalar, comprueba que el paquete está firmado por el autor y que no se ha dañado por el camino.
- Sustituye los archivos del programa, nunca tus datos.
- Cuando la descarga termina, te pregunta una sola vez si quieres reiniciar ahora para aplicarla; con «Más tarde» la descarga se guarda para cuando quieras.
- Si GitHub tiene una versión más nueva que el servidor, o el servidor no responde, la toma de GitHub.
- Si la versión nueva no arrancara, vuelve sola a la anterior.

La búsqueda automática se desactiva en Preferencias, General. Si prefieres actualizar a mano, descarga el ZIP nuevo de la página de versiones y extráelo encima de la carpeta del programa.

## Primeros pasos

1. Comprueba los agentes: Herramientas, «Agentes instalados...». Si falta uno, «Instalar» (Alt+I); si no tiene sesión, «Iniciar sesión» (Alt+S).
2. Crea una sesión con Ctrl+N: elige el agente y la carpeta de tu proyecto, y pulsa «Crear la sesión».
3. Escribe en el cuadro de respuesta (Alt+3). Intro empieza una línea nueva y Ctrl+Intro envía.
4. Ctrl+IA lee la respuesta al llegar. Alt+2 va a la lista de mensajes; Intro abre el mensaje en el visor.
5. Cuando el agente te pide permiso, suena un aviso y te lo dice: pulsa Ctrl+Mayús+A, lee el detalle y elige.

Si no sabes qué hace un control, pulsa Ctrl+H sobre él.

## Manual y lista de teclas

- Manual completo: dentro del programa con F1, o en [docs/manual.md](docs/manual.md).
- Lista de teclas, ventana por ventana: dentro del programa con Ctrl+F1, o en [docs/teclas.md](docs/teclas.md).
- Novedades de cada versión: en Ayuda, o en [docs/changelog.md](docs/changelog.md).

## Privacidad

Ctrl+IA no tiene servidores para tus datos: tus mensajes los envía cada agente a su empresa, igual que desde su propio programa (con Ollama no salen de tu equipo). Ctrl+IA solo se conecta para buscar actualizaciones (se puede desactivar), no guarda contraseñas ni credenciales y quita de sus registros las claves y datos sensibles que reconoce.

## Ayuda, fallos y sugerencias

- Antes de nada, mira el manual (F1): explica cada función y los problemas más habituales.
- Para informar de un fallo o proponer una mejora, abre una incidencia en <https://github.com/hxebolax/CtrlIA/issues>. Cuenta qué hiciste, qué esperabas y qué pasó, con la versión de Ctrl+IA (Ayuda, «Acerca de Ctrl+IA» la copia), la de Windows, el agente y tu lector de pantalla.
- Los registros están en la carpeta `data\logs`, junto al programa. Revísalos antes de adjuntarlos: aunque Ctrl+IA quita lo sensible que reconoce, pueden contener nombres de carpetas o de archivos.

## Donaciones

Ctrl+IA es gratuito. Si te resulta útil y quieres apoyar su desarrollo, puedes invitar al autor a un café en PayPal: <https://www.paypal.com/paypalme/hjbcdonaciones>. También desde el programa, en Ayuda, «Invítame a un café si te gusta mi trabajo».

# Para programadores

## Requisitos de desarrollo

| Requisito | Detalle |
| --- | --- |
| Sistema | Windows 10 u 11 de 64 bits |
| Python | 3.15 de 64 bits (el proyecto usa 3.15.0; `pyproject.toml` pide `>=3.15`). Se instala con `py install 3.15` o desde python.org |
| Git | Git para Windows, para clonar y para las funciones de deshacer y copias aisladas |
| Opcional | `gh` (publicar y probar a mano las funciones de GitHub), Node.js (instalar agentes), NVDA o JAWS (probar la accesibilidad) y al menos un agente para conversaciones reales |

Dependencias del programa (`requirements.txt`): wxPython, accessible_output2, markdown, requests, cryptography y tomlkit. De desarrollo (`requirements-dev.txt`): cx_Freeze, pytest, pytest-cov, ruff y comtypes.

## Preparar el entorno

```bat
git clone https://github.com/hxebolax/CtrlIA.git
cd CtrlIA
entorno.bat crear
```

`entorno.bat crear` crea el entorno virtual `.venv` con `py -3.15` e instala `requirements.txt` y `requirements-dev.txt`. Todas las órdenes de este documento se ejecutan desde la raíz del proyecto.

`entorno.bat` sin argumentos abre un menú numerado; con un argumento ejecuta esa orden y devuelve un código de salida (0 si todo va bien):

| Orden | Qué hace |
| --- | --- |
| `entorno.bat crear` | Crea o repara `.venv` e instala las dependencias |
| `entorno.bat instalar` | Instala las dependencias en el entorno existente |
| `entorno.bat actualizar` | Actualiza las dependencias a la última versión permitida |
| `entorno.bat ejecutar` | Ejecuta Ctrl+IA desde el código, sin consola |
| `entorno.bat depurar` | Ejecuta con consola, `-X dev` y registro detallado |
| `entorno.bat pruebas` | Pasa las pruebas |
| `entorno.bat compilar` | Compila con cx_Freeze y empaqueta en ZIP |
| `entorno.bat compilar-debug` | Compila con consola, sin empaquetar (no se distribuye) |
| `entorno.bat empaquetar` | Solo empaqueta una compilación ya hecha |
| `entorno.bat comprobar` | Comprueba que la versión compilada arranca |
| `entorno.bat limpiar` | Borra `build`, `dist` y las cachés (no toca `.venv` ni `data`) |
| `entorno.bat datos` | Abre la carpeta `data` |
| `entorno.bat registros` | Abre la carpeta `data\logs` |
| `entorno.bat diagnostico` | Versiones de Python, wxPython y cx_Freeze, y si NVDA o JAWS están en marcha |
| `entorno.bat congelar` | Guarda las versiones exactas instaladas en `requirements-lock.txt` (archivo local, no se versiona) |
| `entorno.bat publicar` | Publica una versión (solo el autor; ver más abajo) |
| `entorno.bat subir-github` | Sube los cambios a GitHub (ver más abajo) |

## Ejecutar desde el código

```bat
entorno.bat ejecutar
entorno.bat depurar
```

O directamente con `.venv\Scripts\python.exe main.py`. `main.py --comprobar-arranque` arranca sin ventana y sale con código 0.

Desde el código, los datos se guardan en la carpeta `data` del proyecto, que `.gitignore` excluye salvo los recursos del programa. Para trabajar con otra carpeta de datos, define la variable de entorno `APP_DATA_DIR`.

## Pruebas y comprobaciones

Antes de dar un cambio por terminado, todo esto debe pasar:

```bat
entorno.bat pruebas
.venv\Scripts\python.exe tools\auditoria_rapida.py
.venv\Scripts\python.exe tools\comprobar_teclas.py
.venv\Scripts\python.exe tools\notas_version.py
.venv\Scripts\ruff.exe check app main.py tools tests
```

| Herramienta | Qué comprueba |
| --- | --- |
| `entorno.bat pruebas` | Las pruebas de `tests/` con pytest (`.venv\Scripts\python.exe -m pytest tests -q`) |
| `tools\auditoria_rapida.py` | Antipatrones en el código: errores silenciados, peticiones sin tiempo límite, `shell=True`, rutas fuera de la carpeta portable, controles poco accesibles, hilos que tocan la interfaz, secretos. Las exclusiones justificadas van en `.auditoria-ignorar` o con `# auditoria: ignorar REGLA - motivo` |
| `tools\comprobar_teclas.py` | Que los atajos del código y los de `docs/teclas.md` coinciden, sin duplicados en una misma ventana |
| `tools\notas_version.py` | Que la versión de `app/core/version.py` tiene sus notas en `docs/changelog.md` |
| `ruff check` | Errores reales, excepciones silenciadas y llamadas de registro mal formadas (configuración en `pyproject.toml`) |

Sobre las pruebas:

- Nunca muestran ventanas, nunca hablan y nunca tocan el `data/` real ni salen a Internet: el equipo de desarrollo usa un lector de pantalla.
- Los agentes se sustituyen por imitaciones de `tests/fakes/`, así que no hace falta ninguno instalado.
- Por defecto no se ejecutan las marcadas `red` (necesitan Internet) ni `interfaz` (muestran ventanas o envían teclas): `.venv\Scripts\python.exe -m pytest -m red` o `-m interfaz`. Las lentas se excluyen con `-m "not lento"`.
- Cobertura: `.venv\Scripts\python.exe -m pytest --cov`.
- Unas pocas pruebas necesitan la clave privada de firma del autor y se saltan solas sin ella.

## Traducciones

El idioma del código es el español; los demás idiomas se traducen con gettext (dominio `ctrlia`, catálogos en `data/locales`). Las herramientas solo usan la biblioteca estándar:

```bat
rem 1. Extraer las cadenas del código a data\locales\ctrlia.pot
.venv\Scripts\python.exe tools\extraer_cadenas.py
rem 2. Crear un idioma nuevo (data\locales\en\LC_MESSAGES\ctrlia.po)...
.venv\Scripts\python.exe tools\actualizar_po.py --nuevo en
rem    ...o fusionar el .pot con los .po que ya existen
.venv\Scripts\python.exe tools\actualizar_po.py
rem 3. Traducir el .po con cualquier editor (por ejemplo, Poedit) y compilarlo a .mo
.venv\Scripts\python.exe tools\compilar_traducciones.py
```

`extraer_cadenas.py` da error si a `_()` se le pasa algo que no es un literal (f-strings, `.format()` dentro). `compilar_traducciones.py` omite lo aproximado o sin traducir y no compila una traducción que pierda marcadores como `{nombre}`.

## Compilar y empaquetar

```bat
entorno.bat compilar
entorno.bat comprobar
```

`entorno.bat compilar` ejecuta `setup_cx.py build` y después `tools\empaquetar.py`. Genera:

| Ruta | Contenido |
| --- | --- |
| `build\CtrlIA\` | El programa compilado y listo para usar: `CtrlIA.exe` (sin consola), `ctrlia-mcp.exe` (puente MCP de las herramientas de Ctrl+IA para los agentes), las bibliotecas, los runtimes de Visual C++ y una carpeta `data` limpia (sonidos, icono, traducciones, skills de ejemplo y la documentación copiada de `docs/`) |
| `build\CtrlIA_v<versión>.zip` | La carpeta anterior comprimida, todo dentro de `CtrlIA\` |
| `build\version.json` | El manifiesto de la versión: versión, nombre del ZIP, SHA-256, tamaño, fecha y firma |

Detalles:

- La versión sale de `APP_VERSION` en `app/core/version.py`, la única fuente de verdad; `pyproject.toml` debe coincidir (lo comprueba una prueba).
- `setup_cx.py` copia de `data/` solo su lista blanca (`DATA_DISTRIBUIBLE`) y se detiene si alguien añade una carpeta personal.
- Antes de comprimir, `tools\verificar_build.py` comprueba que la compilación está completa y limpia (sin configuración, registros, bases de datos, claves ni cachés). También se puede ejecutar a mano: `.venv\Scripts\python.exe tools\verificar_build.py build\CtrlIA CtrlIA.exe`.
- `entorno.bat comprobar` arranca `build\CtrlIA\CtrlIA.exe --comprobar-arranque` con una carpeta de datos aparte (`build\comprobacion_data`), para no ensuciar la distribución.
- `entorno.bat compilar-debug` compila con consola para ver errores; esa compilación no se distribuye.

## Firma de las versiones

El actualizador solo instala una versión si `version.json` lleva una firma Ed25519 válida con alguna de las claves públicas incrustadas en `CLAVES_PUBLICAS_ED25519` de `app/services/actualizador.py`. La firma cubre el manifiesto entero (versión, SHA-256, tamaño, novedades y dirección), así que quien entrara en el servidor no podría cambiar el ZIP sin la clave privada.

La clave privada del autor no está en el repositorio ni lo estará nunca: vive en `tools\claves\`, carpeta que `.gitignore` excluye. Por eso, en un clon nuevo:

- `entorno.bat compilar` genera `build\CtrlIA\` igualmente, pero `tools\empaquetar.py` se detiene al firmar porque falta `tools\claves\firma_ed25519.key`, y no crea el ZIP ni `version.json`.
- La clave pública del autor que lleva el código no te sirve para firmar: solo él puede publicar versiones que acepten las copias instaladas.

Tienes dos caminos:

1. **Solo para uso propio, sin firma.** Usa la carpeta `build\CtrlIA\` tal cual (o comprímela tú). Funciona igual; lo único que no tendrá es tu propio canal de actualizaciones. Ojo: su actualizador seguirá ofreciendo las versiones oficiales del autor, que sí están firmadas con la clave que lleva el código, y al aceptar una sustituiría tu compilación. Desactiva la búsqueda automática en Preferencias, General, si no quieres que te lo ofrezca.
2. **Tu propia versión distribuible, con tu firma.**
   1. Genera tu par de claves una sola vez: `.venv\Scripts\python.exe tools\firmar_version.py generar`. Guarda la privada en `tools\claves\firma_ed25519.key` (con un `.gitignore` propio que la excluye) e imprime la pública. Si defines antes la variable de entorno `FIRMA_CONTRASENA`, la privada se guarda cifrada con esa contraseña, y hará falta la misma variable al firmar.
   2. Sustituye la clave pública en `CLAVES_PUBLICAS_ED25519` de `app/services/actualizador.py` por la tuya.
   3. Cambia también las direcciones de donde se buscan las actualizaciones (`URL_BASE` de tu servidor y `URL_GITHUB` de tu repositorio, en el mismo archivo, y la prueba que las comprueba, `tests/test_publicacion.py`); si no, tu copia buscaría las versiones del autor y rechazaría su firma.
   4. Compila con `entorno.bat compilar`: el manifiesto sale firmado. Para comprobarlo: `.venv\Scripts\python.exe tools\firmar_version.py verificar build\version.json`.
   5. Haz copia de la clave privada fuera del equipo: si la pierdes, las copias instaladas no aceptarán tus versiones nuevas.

Nunca subas la clave privada a ningún repositorio ni servidor.

## Publicar una versión (solo el autor)

`publicar.bat` (o `entorno.bat publicar`) publica `APP_VERSION` de una vez, deteniéndose en el primer error:

1. Comprobaciones previas: versión igual en `app/core/version.py` y `pyproject.toml`, notas en `docs/changelog.md`, repositorio en la rama `main` con el remoto `origin` y sin archivos prohibidos, `gh` con sesión iniciada, etiqueta `v<versión>` aún sin publicar y clave privada de firma presente.
2. Pruebas.
3. Compilación y empaquetado (`entorno.bat compilar`).
4. Verificación de la firma de `build\version.json` con la clave pública del programa, y del tamaño y el SHA-256 del ZIP.
5. Copia al servidor de actualizaciones del autor: primero el ZIP y al final `version.json`.
6. Subida del código a GitHub y etiqueta `v<versión>`.
7. Release en GitHub con las notas del registro de cambios, el ZIP y el mismo `version.json` firmado.
8. Resumen con las direcciones de lo publicado.

Opciones:

| Orden | Qué hace |
| --- | --- |
| `publicar.bat` | Con doble clic: publica de verdad, pregunta antes de empezar y espera una tecla al final |
| `publicar.bat --ensayo` | Dice todo lo que haría sin copiar, subir ni crear nada |
| `publicar.bat --sin-pruebas` | No pasa las pruebas |
| `publicar.bat --sin-compilar` | Usa el ZIP y `version.json` que ya hay en `build` |
| `publicar.bat --reemplazar` | Vuelve a subir los archivos de una versión ya publicada |
| `publicar.bat --help` | Todas las opciones |

Solo funciona en el equipo del autor (necesita su clave privada, su carpeta del servidor y permisos en el repositorio). Si mantienes tu propia versión, adapta el destino y el repositorio con `--destino` y `--repositorio`.

Para publicar una versión nueva: cambia `APP_VERSION` en `app/core/version.py` y `version` en `pyproject.toml`, añade su apartado a `docs/changelog.md`, pasa todas las comprobaciones y ejecuta `publicar.bat --ensayo` y luego `publicar.bat`.

## Subir cambios a GitHub

`subir_github.bat` (o `entorno.bat subir-github`) sube los cambios del código al remoto `origin`, rama `main`:

| Orden | Qué hace |
| --- | --- |
| `subir_github.bat` | Con doble clic: commit con un mensaje automático con la fecha y el número de archivos, y espera una tecla al final |
| `subir_github.bat "Arreglo del menú Ver"` | Commit con ese mensaje |
| `subir_github.bat --ensayo` | Dice lo que haría sin cambiar nada |

Antes de añadir nada comprueba que no se cuela nada prohibido aunque `.gitignore` fallase (claves, material de consulta, configuración de agentes de IA, datos personales, compilaciones, el entorno virtual y archivos de más de 50 MB); si encuentra algo, se para y lo dice. Los colaboradores externos no suben a `main`: trabajan en su copia (*fork*) y envían una *pull request*.

## Estructura del proyecto

| Ruta | Contenido |
| --- | --- |
| `main.py` | Punto de entrada y orden de arranque |
| `app/core/` | Núcleo sin interfaz: rutas portables, versión, registro, configuración, base de datos SQLite, tareas en segundo plano, red, traducciones, diagnóstico |
| `app/services/agentes/` | Un adaptador por agente (Claude Code, Codex, Antigravity, ACP, Ollama, SSH) sobre un contrato común |
| `app/services/config_agentes/` | Lectura y edición de la configuración de cada agente |
| `app/services/historial/` | Lectura, solo lectura, de las sesiones de otros programas |
| `app/services/` | Gestor de sesiones, almacén, catálogo de skills, puntos de restauración, PR, exportar e importar, actualizador... |
| `app/ui/` | Ventana principal, diálogos, avisos y capa de accesibilidad |
| `app/puente_mcp.py` | Puente MCP por stdio (`ctrlia-mcp.exe`) |
| `data/` | Recursos del programa que viajan en la distribución (sonidos, icono, traducciones, skills de ejemplo); lo demás lo genera el programa y no se versiona |
| `docs/` | Manual, teclas y registro de cambios para quien usa el programa, y la arquitectura para programadores |
| `tests/` | Pruebas con pytest e imitaciones de los agentes (`tests/fakes/`) |
| `tools/` | Comprobaciones, empaquetado, firma, traducciones, generadores de sonidos e icono, publicación |
| `setup_cx.py` | Compilación con cx_Freeze |
| `entorno.bat`, `publicar.bat`, `subir_github.bat` | Administración del proyecto |

La arquitectura completa (qué hace cada módulo, las decisiones de diseño y las invariantes que no se deben romper) está en [docs/arquitectura.md](docs/arquitectura.md). Léela antes de cambiar nada.

## Normas para contribuir

Las contribuciones son bienvenidas. Abre antes una incidencia para comentar el cambio si es grande, y envíalo como *pull request*. Normas:

- **Todo en español de España:** interfaz, mensajes, registros, comentarios, documentación y mensajes de commit. La persona que mantiene el proyecto trabaja con NVDA.
- **Accesibilidad, siempre comprobada con lector de pantalla:**
  - La etiqueta de cada control se crea justo antes del control.
  - Un mismo acelerador `&` no se repite en una ventana o un menú.
  - Cada control tiene su ayuda de Ctrl+H.
  - Nada de `wx.MessageBox` ni cuadros nativos: se usan los diálogos de `app.ui.accesibilidad.dialogos`.
  - Las acciones van en menús y atajos, no en botoneras.
  - Nada existe solo como voz: cada aviso pasa por `app/ui/avisos.py`.
- **Pruebas sin ventanas:** nunca `Show()`, un `MainLoop()` real ni `SetFocus` visibles; hay una sola `wx.App` (fixture `app_wx` del conftest) y la voz está silenciada. Cada fallo corregido lleva su prueba.
- **Documentación en el mismo cambio:** cualquier cambio de interfaz se documenta en `docs/manual.md` (solo para quien usa el programa, sin programación), en `docs/teclas.md` y en `docs/changelog.md`.
- **Comprobaciones antes de enviar:** `entorno.bat pruebas`, `tools\auditoria_rapida.py`, `tools\comprobar_teclas.py`, `tools\notas_version.py` y `ruff check`, todo sin errores.
- **Respeta las invariantes** de [docs/arquitectura.md](docs/arquitectura.md): nunca responder permisos solo, nunca escribir en las sesiones de otros programas, nunca usar claves de API, nunca distribuir datos personales.
- Formato del código: tabuladores, líneas de hasta 140 caracteres (configuración de ruff en `pyproject.toml`).

# Licencia

Copyright (C) 2026 Héctor J. Benítez Corredera.

Ctrl+IA es software libre: puedes redistribuirlo y modificarlo según los términos de la Licencia Pública General de GNU (GNU GPL), versión 3, publicada por la Free Software Foundation.

Ctrl+IA se distribuye con la esperanza de que sea útil, pero SIN NINGUNA GARANTÍA, ni siquiera la garantía implícita de COMERCIABILIDAD o de IDONEIDAD PARA UN FIN DETERMINADO. Consulta la GNU GPL para más detalles.

El texto completo de la licencia, en inglés (la única versión oficial), está en el archivo [LICENSE](LICENSE) y en <https://www.gnu.org/licenses/gpl-3.0.html>.

Los agentes de IA con los que trabaja Ctrl+IA (Claude Code, Codex, Antigravity, Gemini CLI, Ollama y los agentes ACP) son programas de sus respectivas empresas, con sus propias licencias y condiciones de uso; Ctrl+IA no los incluye ni está afiliado a ellas.
