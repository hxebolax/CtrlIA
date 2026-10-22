# Arquitectura de Ctrl+IA

Documento para quien programa en Ctrl+IA: cómo está organizado el código, qué hace cada módulo, las decisiones que hay detrás y las invariantes que no se deben romper. Para usar el programa, lee el [manual](manual.md); para preparar el entorno, compilar y publicar, el [README](../README.md).

Léelo antes de cambiar nada: muchas decisiones parecen arbitrarias hasta que se conoce el fallo que evitan.

## Índice

1. Visión general
2. Principios de diseño
3. Arranque (`main.py`)
4. Núcleo (`app/core/`)
5. Adaptadores de agentes (`app/services/agentes/`)
6. Configuración de los agentes (`app/services/config_agentes/`)
7. Historial de otros programas (`app/services/historial/`)
8. Servicios (`app/services/`)
9. Interfaz (`app/ui/`)
10. La carpeta `data`
11. Compilación, empaquetado y firma
12. Actualizador
13. Pruebas (`tests/`)
14. Herramientas (`tools/`)
15. Invariantes que no se deben romper

## 1. Visión general

Ctrl+IA no habla con ningún modelo de IA directamente. Lanza en el propio equipo (o en otro, por SSH) el programa oficial de cada agente de programación, con la cuenta en la que la persona ya inició sesión, y conversa con él por su protocolo de línea de comandos. Cada agente tiene un adaptador que traduce su protocolo a un contrato común (`app/services/agentes/base.py`); el gestor de sesiones (`app/services/gestor.py`) une lo que llega de todos los agentes, lo guarda en SQLite y lo entrega a la interfaz wxPython, que lo presenta de forma accesible.

Ctrl+IA es la reescritura profesional, en español y ampliada de *The Chat Place* (repositorio `TheIdeaPlace/AIChat` en GitHub). Las referencias «AIChat #NNN» del código, las pruebas y el registro de cambios son incidencias y PR de ese repositorio que inspiraron una función o un arreglo.

Capas, de abajo arriba:

| Capa | Carpeta | Depende de wx |
| --- | --- | --- |
| Núcleo: rutas, versión, registro, configuración, base de datos, tareas, red, traducciones | `app/core/` | No (salvo lo que avisa a la interfaz) |
| Servicios: adaptadores de agentes, gestor de sesiones, historial, git, PR, exportar, actualizador | `app/services/` | No |
| Interfaz: ventana principal, diálogos, avisos y capa de accesibilidad | `app/ui/` | Sí |

Los servicios no importan wx: así se prueban sin ventanas y la interfaz solo presenta.

## 2. Principios de diseño

- **Portable.** Todo lo que el programa genera vive en la carpeta `data` junto al ejecutable (o junto a `main.py` desde el código). Nunca se escribe en `%APPDATA%`, `%LOCALAPPDATA%`, `%TEMP%` ni en el registro de Windows. `APP_DATA_DIR` redirige `data` a otra carpeta (lo usan las pruebas y la comprobación de la compilación).
- **Accesible primero.** Pensado para NVDA, JAWS y Narrador, con teclado. Nada existe solo como voz: cada aviso pasa por `app/ui/avisos.py` (historial, barra de estado y braille). Sin cuadros nativos con botones en inglés.
- **La interfaz nunca espera.** El trabajo pesado va a hilos de fondo (`app/core/tareas.py`) y vuelve con `wx.CallAfter`; un vigilante (`app/core/vigilante_ui.py`) registra las congelaciones del hilo de la interfaz, que dejarían mudo al lector de pantalla.
- **Los datos de la persona son sagrados.** Las sesiones de otros programas son de solo lectura, la configuración de los agentes se edita sin perder nada y los puntos de restauración nunca tocan el `.git` de la persona.
- **Sin claves de API.** Ctrl+IA usa la sesión iniciada de cada agente y nunca claves de API de pago por su cuenta.
- **Todo en español de España:** interfaz, mensajes, registros, comentarios y documentación. El idioma del código es el español y el resto de idiomas se traducen con gettext.

## 3. Arranque (`main.py`)

El orden de arranque importa:

1. `sys.setswitchinterval(0.001)`: con hilos de fondo leyendo a los agentes, el hilo de la interfaz recupera el GIL enseguida y el lector de pantalla no se queda mudo.
2. `multiprocessing.freeze_support()` lo primero.
3. Registro (solo biblioteca estándar) antes de importar wx: hasta un fallo de importación queda escrito en `data/logs`.
4. Configuración e idioma (gettext).
5. `wx.App`, ayuda contextual (Ctrl+H), `wx.Locale` (botones en español), instancia única, aviso accesible de errores y vigilante de la interfaz.
6. Aviso por voz «Iniciando Ctrl+IA» sin ventana de espera (una ventana que no responde mientras se importa bloquearía al lector de pantalla).
7. Ventana principal y bucle de eventos.

Argumentos:

- `--comprobar-arranque`: arranca sin mostrar la ventana y sale con código 0. Lo usan las pruebas y la comprobación del `.exe` recién compilado.
- `--tras-actualizar`: lo pasa el actualizador al relanzar; se acepta.
- Una carpeta: abre «Nueva sesión» con esa carpeta (también si Ctrl+IA ya estaba abierto, a través de la instancia única).

## 4. Núcleo (`app/core/`)

| Módulo | Qué hace |
| --- | --- |
| `paths.py` | Rutas portables: `ruta_base()` (carpeta del ejecutable o del proyecto), `ruta_datos()` y una función por subcarpeta (`config`, `logs`, `db`, `cache`, `temp`, `backups`, `agentes`...), creadas bajo demanda. `ruta_agentes_ejemplo()` mira la carpeta del programa, no la de datos. |
| `version.py` | Única fuente de verdad del nombre (`NOMBRE_APLICACION`, `ID_APLICACION`), la versión (`APP_VERSION`), el autor y la URL de donaciones. `setup_cx.py` la lee con una expresión regular y `pyproject.toml` debe coincidir (lo comprueba una prueba). |
| `registro.py` | Registro profesional: archivo por sesión legible y JSONL, `incidencias.log`, captura de excepciones no controladas (hilo principal, wx, hilos, asyncio, fallos nativos), redacción de secretos y rotación segura en Windows. |
| `configuracion.py` | Configuración JSON atómica con esquema, valores por defecto en código, claves `seccion.clave`, migraciones de claves y avisos a las ventanas abiertas. |
| `base_datos.py` y `migraciones.py` | SQLite en WAL con migraciones numeradas por `user_version`. Nunca se modifica ni se reordena una migración publicada: se añade otra al final. |
| `tareas.py` | Tareas en hilos de prioridad baja, con cancelación, unificación de duplicadas y respuestas entregadas con `wx.CallAfter`. |
| `red.py` y `descargas.py` | HTTP con reintentos, tiempos de espera y errores comprensibles; descargas reanudables, verificadas (tamaño y SHA-256), cancelables y atómicas. |
| `i18n.py` | gettext con el dominio `ctrlia`; el español es el idioma base y no necesita catálogo. |
| `documentacion.py` | Manual, teclas y novedades: los busca en `data/docs` y, si no están (ejecución desde el código), en `docs/`. Muestra las novedades una vez tras actualizar. |
| `instancia_unica.py` | Una segunda copia pide a la primera que se muestre (y le pasa la carpeta, si la hay) y termina. Una instancia por carpeta del programa. |
| `diagnostico.py` | Paquete de diagnóstico para soporte, con los datos personales anonimizados. |
| `medicion_arranque.py` | Fases del arranque e importaciones diferidas. |
| `vigilante_ui.py` | Vigilante de congelaciones del hilo de la interfaz. |
| `voz.py` | Anuncios por voz mínimos a través del lector de pantalla (`accessible_output2`, opcional). |

## 5. Adaptadores de agentes (`app/services/agentes/`)

`base.py` define el contrato común (proveedores, capacidades, conversación y eventos). Hay un adaptador por agente:

### Claude Code (`claude.py`)

- `claude -p` en *stream-json* con el protocolo de control (`can_use_tool`, `elicitation`).
- El proceso (`_ProcesoClaude`) sigue abierto entre turnos `agentes.claude_minutos_abierto` minutos (0: uno por turno, como antes) y se relanza con `--resume` si cambia su «firma» (modelo, esfuerzo, reglas, carpetas, variables). El modo va en caliente con `set_permission_mode`. Lo que llega entre turnos abre un turno espontáneo.
- Con `agentes.esperar_mcp_claude` pone `MCP_CONNECTION_NONBLOCKING=0` para que cada arranque espere a los servidores MCP (si no, el turno empieza con ellos «desconectados»).
- Las consultas que no conversan (`/usage`, la lista de comandos) van con `--strict-mcp-config` y no arrancan ningún servidor.
- Permisos: «durante esta sesión» va con destino `session` y a `ajustes.reglas_permitidas`; «siempre en esta carpeta» devuelve la sugerencia de Claude Code con su destino (`localSettings`), así que lo escribe Claude Code en `.claude/settings.local.json`; con MCP se ofrece además el servidor entero (`mcp__servidor`).
- Claude Code pregunta a la vez por cada llamada en paralelo y no retira las demás al añadir una regla: al conceder una regla, `_permitir_pendientes_cubiertas` permite con ella las pendientes que cubre (`regla_cubre`, conservadora) y las retira.
- No se ofrecen reglas si llega `requires_user_interaction`, `matched_ask_rule` o `suppress_always_allow_rule`; una petición repetida (mismo `request_id`) se muestra una vez.
- Con la regla de la sesión va la carpeta sugerida (`addDirectories`), guardada como `Carpeta(ruta)` en `reglas_permitidas` y pasada como `--add-dir` (nunca una unidad ni la raíz de un recurso: `carpeta_concreta`).
- «Añadir al turno» escribe el mensaje en la entrada sin `interrupt` (Claude Code 2.1.238 lo atiende en el mismo turno).
- `extra["estilo"]` va como `--settings {"outputStyle": …}`; `agentes.chrome_claude`, como `--chrome`; ambos entran en la firma.
- ScheduleWakeup y CronCreate retrasan el cierre por inactividad (`_ProcesoClaude.tiene_programado`).

### Codex (`codex.py`)

- `codex app-server` (JSON-RPC por stdio, sin campo `jsonrpc`).
- Las reglas `Orden(prefijo)` de la sesión solo casan en la carpeta raíz de la sesión (`_misma_carpeta`), nunca con órdenes git peligrosas (`orden_git_peligrosa`: forzar, borrar, otro destino, opciones globales) ni con un `.git` anidado o que no sea carpeta (`repositorio_sin_anidados`, como mucho 20.000 entradas; si no, pregunta).
- En las órdenes no se ofrece el `acceptForSession` nativo (sobrevive a un cambio de modo, AIChat #298).
- El `sandbox` efectivo de `thread/start` y `thread/settings/updated` se compara con el pedido y, si es más restrictivo, se avisa con el resultado de `windowsSandbox/readiness`.
- Un modelo que `model/list` ya no tiene no se envía al continuar.

### Antigravity (`antigravity.py`)

- CLI `agy` en *stream-json*. No pregunta permisos en modo no interactivo: deniega en silencio; Ctrl+IA lo convierte en una petición «Permitir y reintentar» que añade una regla a `~/.gemini/antigravity-cli/settings.json`.
- Las herramientas MCP (agy 1.4.2, servidores en `~/.gemini/config/mcp_config.json`) solo traen `{"action": "mcp"}` en `denied_actions`: la regla `mcp(servidor/herramienta)` se saca del error del paso (estado `ERROR`) y se ofrece también `mcp(servidor/*)`.

### ACP (`acp.py` y `acp_conocidos.py`)

- `acp.py`: cliente ACP v1 (Agent Client Protocol) escrito a mano, para Gemini CLI, los adaptadores ACP y los agentes personalizados.
- `acp_conocidos.py`: agentes con soporte de ACP que Ctrl+IA conoce de serie, sacados del registro oficial (`agentclientprotocol/registry`) entre los que cumplen el contrato de Ctrl+IA.

### Ollama (`ollama.py`)

Conversar con modelos locales por HTTP (`http://127.0.0.1:11434`): solo chat, sin herramientas, porque Ollama no es un agente de programación.

### Piezas comunes

- `elicitacion.py`: peticiones de los servidores MCP, comunes a Codex (`mcpServer/elicitation/request`) y Claude Code (`control_request` `elicitation`): permiso para usar una herramienta (formulario sin campos con `_meta.codex_approval_kind = "mcp_tool_call"`; `_meta.persist` recuerda la respuesta), formularios (`form`, `openai/form`, `openaiForm`), enlaces y, lo que no se sabe rellenar, solo rechazar o cancelar: nunca se rechaza sin enseñarlo.
- `procesos.py`: cada agente dentro de un *Job Object* de Windows (muere entero al cerrar); solo binarios nativos.
- `instalacion.py`: diagnóstico de cómo está instalado un agente. Ctrl+IA solo lanza binarios nativos: los lanzadores `.cmd` y `.ps1` que deja npm pasan por `cmd.exe` o PowerShell, que reinterpretan los argumentos.
- `remoto.py`: agentes en otro equipo (macOS o Linux) por `ssh.exe` de Windows: equipos de `agentes.equipos_ssh`, orden `ssh -T -o BatchMode=yes -- destino 'exec "$SHELL" -l -c …'` con cada parte entre comillas POSIX, «Comprobar», lectura y escritura de archivos remotos y túnel `ssh -N -L` para Ollama. Una sesión es remota si `extra["remoto"]` tiene `equipo`; su carpeta es una ruta POSIX que nunca se comprueba desde Windows. Claude y Codex son estables; Gemini, Antigravity, ACP personalizados y Ollama, experimentales.

## 6. Configuración de los agentes (`app/services/config_agentes/`)

- Catálogo y edición de los archivos de configuración de cada agente (`catalogo_claude.py`, `catalogo_codex.py`, `catalogo_antigravity.py`, `catalogo_gemini.py`). `modelo.py` describe las opciones y lee y escribe cada archivo con una regla: nunca se pierde nada; solo se cambian las claves que la persona cambia y se conserva el resto (claves desconocidas, servidores MCP, proyectos, comentarios del `config.toml` de Codex gracias a `tomlkit`).
- `modo_inicial.py` lee su modo de permisos inicial (Claude `permissions.defaultMode`, Codex `sandbox_mode` y `approval_policy`, Antigravity `toolPermission`), que `Proveedor.modo_predeterminado(carpeta)` propone en «Nueva sesión».
- `reglas_claude.py` y `reglas_codex.py`: reglas de la propia configuración de Claude Code para una carpeta (usuario, proyecto compartido, solo tú, administrador; la confianza de `~/.claude.json`) y archivos `.rules` de Codex, solo para leer en «Permisos de la sesión».

## 7. Historial de otros programas (`app/services/historial/`)

Todo con SQLite en modo `ro` y archivos abiertos solo para leer (`comun.py` reúne las ayudas compartidas).

- `claude.py`: sesiones de Claude Code de la terminal, la app de escritorio y Cowork, solo lectura.
- `equipos.py`: sesiones que ya existen en los equipos por SSH (Claude Code de la terminal y la app de escritorio, hilos de Codex). Sin instalar nada allí: un inventario con `find`/`stat` y, de lo que cambió, principio y final de cada transcript con `head`/`tail | gzip | base64`; las rutas van por la entrada estándar. `HistorialEquipos` lee cada equipo en un hilo de fondo (30 s, 120 s tras un fallo, F5 lo fuerza) y la lista nunca espera; al abrir una sesión su transcript se copia a `data/cache/equipos` y se sigue con `tail -c +N`. `FilaSesion.equipo`/`sistema` dicen a qué equipo pertenece (columna Equipo); `remota` vale para propias y ajenas de otro equipo.
- `antigravity.py`: conversaciones de la app y de Antigravity CLI con su título, carpeta y pasos (`agyhub_summaries_proto.pb` y `conversation_summaries.db`; los mensajes `.pb` no se decodifican).
- `ollama.py`: chats de la app de Ollama (`%LOCALAPPDATA%\Ollama\db.sqlite`), que se pueden continuar (`ollama:app-<id>`).
- `codex.py`: los `rollout-*.jsonl` de Codex en disco, solo para buscar (la lista y la lectura van por su servidor).

## 8. Servicios (`app/services/`)

### Sesiones y datos

- `gestor.py`: une sesiones propias, ajenas, importadas y conversaciones vivas; guarda los eventos; calcula «terminada» y «respuesta nueva» (también de las ajenas, con `metadatos.leida_hasta`).
- `almacen.py`: acceso a la base de datos (`data/db/ctrlia.db`), solo SQL y conversiones. Las claves de sesión tienen la forma `<origen>:<id>` (por ejemplo, `ctrlia:<uuid>` para las creadas o continuadas en Ctrl+IA e `importada:<uuid>` para las importadas).
- `intercambio.py`: exportar sesiones a un ZIP versionado (formato descrito en el propio módulo) e importarlas como sesiones `importada:<uuid>` de solo lectura, que se pueden continuar en una sesión nueva (`contexto_para_continuar`).
- `exportar.py`: exporta una conversación a Markdown, HTML o texto plano.
- `extraccion.py`: lo que se suele querer recuperar de una conversación (bloques de código, enlaces y rutas, archivos cambiados con su diff, órdenes con su salida, tareas, errores, commits, incidencias y PR) para «Listas de la sesión» y la ventana principal, sin wx.
- `informe.py`: informe de sesiones (tamaño, recuentos, totales; Markdown y CSV sin texto de mensajes).
- `favoritos.py`: mensajes favoritos de cualquier sesión (tabla `mensajes_favoritos`, migración 4): clave estable del mensaje (id o huella `h:`), Ctrl+Alt+F, Alt+Flecha y la lista global (`ui/dialogos/favoritos.py`).

### Agentes del catálogo

- `catalogo_agentes.py`: catálogo de agentes (habilidades con su SKILL.md) en la tabla `agentes` (migración 4): lector de frontmatter sin dependencias, búsqueda en carpetas con límites y sin seguir enlaces, importar, crear en `data/agentes`, refrescar y `datos_para_sesion(almacen, ids)`. Interfaz en `ui/dialogos/gestor_agentes.py` y `ui/dialogos/elegir_agentes.py` (`PanelAgentes`, en «Nueva sesión» y «Opciones de la sesión»). Los ids van en `sesiones.extra["agentes"]`.
- Los 14 agentes de ejemplo viajan en `data/agentes_ejemplo` (en `DATA_DISTRIBUIBLE`; `paths.ruta_agentes_ejemplo()` mira la carpeta del programa, no la de datos); se cargan una vez al arrancar (`agentes.ejemplos_cargados`), con la categoría «Ejemplos de Ctrl+IA», y `reparar_ejemplos` corrige sus rutas si el programa portable se mueve. Su fuente es el repositorio de agentes del autor: para actualizarlos, se vuelven a copiar sin `__pycache__` ni cachés de herramientas (`.ruff_cache`, `.pytest_cache`).
- `agentes_sesion.py`: cómo recibe cada plataforma los agentes elegidos. Claude Code (`Capacidades.agentes_nativos`): complemento `ctrlia-agentes` en `data/cache/agentes_sesion/<hash>` con una junction por agente (`--plugin-dir`), `--append-system-prompt` con sus rutas y `Read(//letra/…/**)` en `--allowedTools`; al limpiar, las junctions se quitan con `rmdir` y nunca se recorren. Los demás: `presentacion()` delante del mensaje la primera vez y cuando cambia la lista (`extra["agentes_presentados"]`, `GestorSesiones._con_agentes`); lo guardado es el texto de la persona. Ollama y SSH, con el contenido del SKILL.md.
- `memoria.py`: archivos de instrucciones y memoria que leen los agentes («Lo que el agente sabe de ti»): `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, habilidades y ajustes, existan o no.
- `inventario.py`: «Herramientas del agente» (Ctrl+Alt+H): `Conversacion.inventario()` (Claude, del `system/init`; Codex, de `mcpServerStatus/list`, `skills/list` y `plugin/list`).

### Varios agentes a la vez

- `reenviar.py`: «Enviar la respuesta a otra sesión» (AIChat #248): qué respuesta, a qué sesiones propias (la que envió la última, primero) y la etiqueta que dice qué agente la escribió. Va como un mensaje normal: nunca contesta preguntas ni da permisos.
- `dialogo_agentes.py`: «Conversación automática entre sesiones» (AIChat #248 fase 2), sin wx. `ConversacionEntreSesiones` pasa la respuesta de cada sesión a la otra en cada `FIN_TURNO` (con `reenviar.texto_reenviado(..., automatico=True)`), hasta el máximo de turnos, `[FIN]`, un error o la detención. Invariante: nunca responde permisos ni preguntas; si una sesión los pide, la conversación se pausa. Interfaz en `ui/dialogos/dialogo_agentes.py`.

### Archivos, git y GitHub

- `puntos.py`: puntos de restauración («Deshacer los cambios de archivos desde este mensaje», Ctrl+Alt+Z; «Rehacer», Ctrl+Alt+Mayús+Z) para todos los agentes. Un repositorio git «sombra» por carpeta en `data/cache/puntos/<hash>/repo.git` (`--git-dir` aparte, entorno sin `GIT_*` ni configuración global, bytes exactos). Invariante: nunca toca el `.git` de la persona ni borra fuera de la carpeta ni a través de enlaces. Un punto antes de cada mensaje (el envío lo espera como mucho 5 s, desde un hilo) y otro `fin` al acabar cada turno; restaurar guarda antes un punto `seguridad`. `MixinPuntos` lo hereda `GestorSesiones`. En las sesiones de Claude por SSH lo deshace Claude Code (`Conversacion.deshacer_archivos`, `rewind_files`). Diálogo en `ui/dialogos/deshacer.py`.
- `git.py`: git y GitHub para preparar la carpeta de una sesión: copias aisladas (*worktrees*) en `<repositorio>/.ctrlia/worktrees/<rama>`, añadidas a `.git/info/exclude`, y clonado de repositorios con `gh repo clone`, usando la sesión de `gh` de la persona (Ctrl+IA no guarda credenciales de GitHub).
- `pr.py`: estado de la PR de la rama y de sus comprobaciones (AIChat #236) con `gh pr view --json` y el fallo con `gh run view --job --log-failed`, solo lectura y sin wx. `ui/vp_pr.py` (`MixinPR`) vigila despacio las sesiones abiertas o recientes (60 s con comprobaciones en curso, 5 min si no, más tras un error), avisa una vez por cambio (`anuncios.comprobaciones_pr`) y lleva el submenú «PR y comprobaciones».

### Herramientas de Ctrl+IA para los agentes

`herramientas_propias.py`: servidor MCP «ctrlia» con `avisar`, `dar_por_terminada` y `renombrar_sesion`, con un token por sesión.

- Invariante: solo afectan a la sesión del token, con límites de longitud y frecuencia, y cada uso llega como un `AVISO` con `herramienta_ctrlia`.
- Claude Code lo recibe «en proceso» (`sdkMcpServers` y `mcp_message`, también por SSH); Codex (`config` del hilo) y los ACP con HTTP, por un servidor HTTP local (127.0.0.1, Bearer, sin SSE, rechaza `Origin`); los ACP sin HTTP y Antigravity, por el puente stdio `app/puente_mcp.py` (`ctrlia-mcp.exe` en la versión compilada).
- A `agy` se le da un complemento `ctrlia` (`.agents/plugins/ctrlia/mcp_config.json`) en `data/cache/herramientas_ctrlia/agy` con `--add-dir`, y la regla `mcp(ctrlia_ctrlia/*)`; nunca `--agent`, que deja a `agy` sin herramientas de edición. La sesión se reconoce por `antigravity.google/conversation_id`.
- Claude Code además: `ultimo_uuid_usuario` y `deshacer_archivos` (`rewind_files` con `CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING`).

### Límites de uso

`limites.py`: límites de 5 horas y semanales de las cuentas (`Proveedor.leer_limites()`), sin enviar mensajes: Codex con `account/rateLimits/read` (y, sin conexión, el último `rate_limits` de sus rollout), Antigravity con `agy -p /usage --output-format json` (solo desde 1.1.11: antes se enviaría como mensaje) y Claude con `claude -p /usage` (`usage_report`). Nunca se leen ni se usan los tokens de sesión de los agentes.

### Abrir en la app del agente

Ctrl+O: `claude://resume?session=<id>` importa y abre en la app de Claude una sesión de Claude Code (también las de Ctrl+IA, que antes se cierran aquí: `_ceder_a_la_app`); `codex://threads/<id>` abre un hilo en la app de Codex. Comprobado con Claude 3.1623 y Codex 26.1016.

## 9. Interfaz (`app/ui/`)

- `ventana_principal.py`: la ventana principal, con la tabla `MENUS` (menús, atajos y ayudas en un solo sitio) y los mixins:
  - `vp_sesiones.py`: la lista de sesiones.
  - `vp_mensajes.py`: la lista de mensajes.
  - `vp_conversacion.py`: el cuadro de respuesta, los adjuntos y el envío.
  - `vp_disposicion.py`: el panel de lectura, la lista de sesiones ocultable, el reparto del espacio, el divisor (`ventana.porcentaje_respuesta`, solo cambia proporciones del sizer y nunca recibe el foco) y el resaltado de la zona con foco.
  - `vp_pr.py`: PR y comprobaciones.
- `dialogos/`: un módulo por diálogo (nueva sesión, permisos, preferencias de la sesión, gestor de agentes, favoritos, grupos, deshacer, listas, equipos SSH...).
- `avisos.py`: voz, braille, barra de estado y esquema de sonidos. Todo aviso pasa por aquí.
- `accesibilidad/`: la capa de accesibilidad (etiquetas, nombres, listas multicolumna, casillas, diálogos con botones en español, ayuda Ctrl+H, teclado y foco, voz, baja visión). Los diálogos se crean con `app.ui.accesibilidad.dialogos`, nunca con `wx.MessageBox`.
- `lectura_listas.py`: qué lee el lector de pantalla en cada lista (columnas posibles, su orden y cómo se escribe cada una), sin ventanas.
- `portapapeles.py`: lo que se puede adjuntar del portapapeles (archivos del Explorador, formato «PNG», mapa de bits sin alfa vacío). Con texto, Ctrl+V pega el texto; Ctrl+Alt+V adjunta igualmente. Para copiar texto hay una sola función, `accesibilidad.teclado.copiar_texto`, que hace `Flush` (si no, lo copiado se perdía al cerrar).
- `visor_ayuda.py`: visor no modal del manual y de los mensajes. En modo HTML el motor Edge se queda las teclas: la página lleva un guion que se las pasa al visor con `postMessage` (o navegando a `ctrlia:`). El visor de un mensaje añade Alt+3 (ir a la respuesta), Ctrl+R (citar) y Ctrl+Alt+N (nueva sesión a partir del mensaje); Alt+6 vuelve a él.
- `dialogo_actualizacion.py`: el diálogo accesible del actualizador.
- `dialogo_error.py`: aviso accesible de errores inesperados, siempre en el hilo de la interfaz.
- `bandeja.py`: icono de la bandeja, que solo existe mientras la ventana está oculta.
- `palabras_curiosas.py`: palabras de la barra de estado mientras el agente escribe (AIChat #314), desactivadas de fábrica y nunca por voz.
- `comun.py`, `acerca_de.py`, `dialogo_preferencias.py`, `dialogo_texto.py`: utilidades compartidas, «Acerca de», preferencias y textos de solo lectura.

## 10. La carpeta `data`

Todo lo que genera el programa vive en `data/` (portable). Al compilar, `setup_cx.py` copia a la distribución solo las subcarpetas de su lista blanca `DATA_DISTRIBUIBLE`:

| Carpeta | Contenido | En el repositorio |
| --- | --- | --- |
| `data/sounds` | Sonidos WAV generados con `tools/generar_sonidos.py` | Sí (menos `propios/`, que es de la persona) |
| `data/icons` | `app.ico`, generado con `tools/generar_icono.py` | Sí |
| `data/agentes_ejemplo` | Los 14 agentes de ejemplo | Sí |
| `data/locales` | Catálogos de traducción (`.pot`, `.po`, `.mo`) | Sí |
| `data/bin`, `data/plugins` | Reservadas; vacías de momento | Solo el marcador `.gitkeep` |
| `data/docs` | Copia de `docs/manual.md`, `docs/teclas.md` y `docs/changelog.md` que hace `setup_cx.py` al compilar (`ARCHIVOS_EXTRA`) | Solo el marcador `.gitkeep` |

Lo demás es de la persona y nunca se distribuye ni se versiona: `config`, `db`, `logs`, `cache`, `temp`, `backups`, `agentes`, `diagnostico`, etc. `setup_cx.py` se detiene si alguien añade a `DATA_DISTRIBUIBLE` una carpeta de `DATA_PROHIBIDO`, y `PATRONES_EXCLUIDOS` evita copiar cachés, registros, bases de datos, claves y marcadores.

La documentación de usuario tiene una sola fuente: `docs/`. Desde el código, `app/core/documentacion.py` la lee de ahí cuando `data/docs` está vacía.

## 11. Compilación, empaquetado y firma

`setup_cx.py` compila con cx_Freeze en `build/CtrlIA`:

- `CtrlIA.exe`, sin consola (`base="gui"`; con `--debug`, con consola).
- `ctrlia-mcp.exe`, el puente MCP por stdio (`app/puente_mcp.py`), de consola porque habla por su entrada y su salida estándar. El nombre está también en `app/services/herramientas_propias.py` (`EJECUTABLE_PUENTE`).
- Los runtimes de Visual C++ (`include_msvcr`), para que arranque en un Windows recién instalado.
- `replace_paths`: las trazas llevan rutas relativas, sin las carpetas del equipo donde se compiló.
- `optimize=1`: `optimize=2` quitaría las docstrings y rompería bibliotecas que las leen.
- `accessible_output2` se importa de forma diferida, así que sus dependencias se nombran en `PAQUETES_OPCIONALES`.
- ABI estable (`corregir_abi_estable`): Python 3.15 trae dos `python3t.dll`; el de su carpeta raíz reenvía a `python315t.dll` (el intérprete sin GIL) y el de `abi3t-compat` a `python315.dll`. cx_Freeze copia el de la raíz, y las extensiones «abi3t», como `cryptography` (verifica la firma de las actualizaciones), no cargarían («DLL load failed»). Tras compilar se pone el correcto junto al ejecutable y se añade `python3.dll` si falta.
- Cada compilación empieza borrando `build/CtrlIA`, porque si se probó la copia compilada su `data/` tendría configuración y registros.

`tools/empaquetar.py` (lo llama `entorno.bat compilar`):

1. Pasa `tools/verificar_build.py`: ejecutable presente, runtimes de Visual C++, `data/` sin nada personal ni generado y sin cachés ni herramientas de desarrollo. Si falla, no se empaqueta.
2. Comprime `build/CtrlIA` en `build/CtrlIA_v<versión>.zip` (todo dentro de una carpeta `CtrlIA/`).
3. Escribe `build/version.json` con la versión, el nombre del ZIP, su SHA-256, su tamaño y la fecha.
4. Lo firma con `tools/firmar_version.py` y la clave privada `tools/claves/firma_ed25519.key`.

La firma Ed25519 cubre el manifiesto completo con una serialización canónica (JSON ordenado, separadores fijos, sin el campo `firma_ed25519`), idéntica en `tools/firmar_version.py` y en `app/services/actualizador.py` (lo comprueba una prueba). Cambiar cualquier campo (versión, SHA-256, tamaño, novedades, URL) invalida la firma. El SHA-256 solo demuestra que el ZIP no se corrompió; la firma demuestra que lo publicó quien tiene la clave privada, que no está ni en el servidor ni en el repositorio.

## 12. Actualizador

`app/services/actualizador.py` (con su diálogo `app/ui/dialogo_actualizacion.py`):

1. Descarga `version.json` con tiempo límite, solo por https (http solo contra la propia máquina, en las pruebas) y verifica su firma con las claves públicas incrustadas (`CLAVES_PUBLICAS_ED25519`).
2. Compara versiones sin dependencias y rechaza una inferior a la última confirmada en el equipo (anti-rollback).
3. Descarga el ZIP con reanudación, sin pasar del tamaño anunciado, y verifica tamaño y SHA-256.
4. Lo extrae de forma segura y lo instala con un `.bat` que hace copia de seguridad, sustituye el programa sin tocar los datos de la persona (de `data/` solo renueva `DATA_ACTUALIZABLE`) y relanza la aplicación con `--tras-actualizar`.
5. La versión nueva llama a `confirmar_arranque` unos segundos después de mostrar la ventana; si no lo hace, se vuelve a la anterior.

## 13. Pruebas (`tests/`)

- `conftest.py` garantiza que nunca se toca el `data/` real (`APP_DATA_DIR` apunta a una carpeta temporal antes de importar la aplicación y cada prueba tiene la suya), que no se sale a Internet salvo en las pruebas marcadas `red`, que hay una sola `wx.App` para toda la sesión (fixture `app_wx`) con marcos que se crean sin mostrar y se destruyen solos, y que la voz y los diálogos modales están simulados. `_config_agentes_aislada` impide leer los archivos reales de los agentes.
- `tests/fakes/`: imitaciones de cada agente para probar los adaptadores sin el programa real ni red: `fake_claude.py` (`claude -p` en stream-json), `fake_codex.py` (`codex app-server`), `fake_agy.py`, `fake_acp.py`, `fake_ollama.py` (solo en 127.0.0.1) y `fake_ssh.py` (`ssh.exe`).
- Apoyo: `ayudas_wx.py` (interfaces sin ventanas), `msaa.py` (lee lo mismo que un lector de pantalla), `servidor_http_prueba.py`, `apoyo_actualizador.py` y los `*_ejemplo.py`.
- Marcadores (`pyproject.toml`): `lento`, `red` e `interfaz`. Por defecto no se ejecutan `red` ni `interfaz`.
- Las pruebas que necesitan algo del equipo del autor (la clave privada de firma, su carpeta de agentes) se saltan solas cuando no está.

## 14. Herramientas (`tools/`)

| Herramienta | Para qué |
| --- | --- |
| `auditoria_rapida.py` | Detector de antipatrones con `ast` (errores silenciados, peticiones sin tiempo límite, `shell=True`, rutas fuera de la carpeta portable, controles poco accesibles, hilos que tocan la interfaz, secretos...). Exclusiones en `.auditoria-ignorar` o con `# auditoria: ignorar REGLA - motivo`. |
| `comprobar_teclas.py` | Compara los atajos del código con `docs/teclas.md`: sin documentar, documentados que ya no existen y duplicados en la misma ventana. Para teclas atendidas a mano, comentarios `# atajo: Ctrl+Mayús+L`. |
| `notas_version.py` | Extrae las notas de `APP_VERSION` de `docs/changelog.md` y falla si no hay. |
| `verificar_build.py` | Comprueba que una compilación está completa y limpia. |
| `empaquetar.py` | ZIP y `version.json` firmado. |
| `firmar_version.py` | Genera el par de claves Ed25519, firma y verifica `version.json`. |
| `extraer_cadenas.py`, `actualizar_po.py`, `compilar_traducciones.py`, `catalogo_po.py` | Traducciones con gettext sin dependencias: `.pot`, fusión con los `.po` y compilación a `.mo` validando marcadores y aceleradores. |
| `generar_sonidos.py`, `generar_icono.py` | Regeneran `data/sounds` y `data/icons/app.ico`. |

## 15. Invariantes que no se deben romper

- Las sesiones de otros programas son de solo lectura; «Continuar aquí» crea una copia (bifurca) y le copia sus mensajes (`GestorSesiones.continuar_con_historial`). Solo con `historial.continuar_original` (desactivada de fábrica), en Claude Code y Codex, sigue la misma sesión (`id_externo` sin bifurcar, `extra["original"]`), y nunca si está abierta en otro programa o tuvo actividad hace menos de `RECIENTE_EN_USO` (`SesionEnUso`).
- Las importadas (`importada:`) no se escriben nunca: «Continuar aquí» crea una sesión nueva de Ctrl+IA en este equipo (agente y carpeta a elegir) con una copia de sus mensajes, y el primer mensaje al agente lleva delante la conversación anterior, recortada a `intercambio.MAX_CONTEXTO` caracteres y diciendo si lo está (`GestorSesiones.preparar_desde_importada` y `_con_importada`).
- Ctrl+IA nunca usa claves de API: se borran del entorno del agente y, si Claude informa de una, el turno se aborta (salvo opción explícita en Preferencias).
- Por SSH: solo claves (`BatchMode=yes`), nunca contraseñas ni aceptar huellas solo; lo que escribe la persona va por la entrada estándar, nunca en la orden; las claves de API se quitan también en el otro equipo (`env -u`).
- Una petición de permiso nunca se responde sola; Intro siempre elige la opción inofensiva. La única excepción es aplicar una regla que la persona acaba de conceder a las peticiones iguales que ya esperaban (Claude Code), y se avisa.
- Ctrl+IA nunca escribe en la configuración de Claude Code: «Permitir siempre en esta carpeta» lo guarda el propio Claude Code a petición de la persona.
- Un mensaje cuyo envío quedó en duda nunca se reenvía.
- El modo de permisos que Ctrl+IA pasa al agente (`--permission-mode` en Claude, aprobación y sandbox en Codex) manda sobre su archivo: por eso se propone el de su configuración y, sin modo elegido, no se pasa ninguno. Las pruebas no leen los archivos reales de los agentes (`_config_agentes_aislada` en el conftest).
- La conversación automática entre sesiones y «Enviar la respuesta a otra sesión» nunca responden permisos ni preguntas.
- Los puntos de restauración nunca tocan el `.git` de la persona ni borran fuera de la carpeta ni a través de enlaces; las junctions de los agentes de la sesión se quitan con `rmdir` y nunca se recorren.
- Las herramientas de Ctrl+IA para los agentes solo afectan a la sesión de su token.
- Nada de `wx.MessageBox` ni cuadros nativos con botones en inglés (lo comprueba una prueba).
- Nada existe solo como voz: cada aviso pasa por `app/ui/avisos.py` (historial, barra de estado y braille).
- Toda lista tiene menú contextual con `vincular_menu_contextual` (lo comprueba una prueba).
- Las pruebas nunca muestran ventanas ni hablan: el equipo de desarrollo usa un lector de pantalla.
- La distribución nunca lleva datos personales: lista blanca de `data/` en `setup_cx.py` y `tools/verificar_build.py` antes de empaquetar.
