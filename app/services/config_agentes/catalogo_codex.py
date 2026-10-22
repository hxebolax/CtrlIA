"""Opciones de ``config.toml`` de Codex.

Fuente: https://learn.chatgpt.com/docs/config-file/config-reference (Codex
0.163, consultada el 18 de octubre de 2026). El archivo se edita con
``tomlkit``: comentarios, proyectos de confianza, servidores MCP y complementos
se conservan tal cual.
"""

from __future__ import annotations

from app.services.config_agentes.modelo import (
	BOOL,
	DICCIONARIO,
	ENTERO,
	LISTA,
	OPCION,
	TEXTO,
	AgenteConfigurable,
	Ajuste,
	Ambito,
)

USUARIO, PROYECTO = "usuario", "proyecto"
SOLO_USUARIO = (USUARIO,)


def _raiz():
	from app.services.memoria import raiz_codex

	return raiz_codex()


AMBITOS = (
	Ambito(USUARIO, "Tu usuario (todas las carpetas)",
		"~/.codex/config.toml (o CODEX_HOME): vale para todos tus proyectos.", lambda _carpeta: _raiz() / "config.toml"),
	Ambito(PROYECTO, "Proyecto",
		"<proyecto>/.codex/config.toml: solo se lee si el proyecto es de confianza. Algunas claves, como notify o "
		"model_provider, se ignoran aquí.",
		lambda carpeta: carpeta / ".codex" / "config.toml" if carpeta else None, necesita_carpeta=True),
)

ESFUERZOS = (("low", "Bajo"), ("medium", "Medio"), ("high", "Alto"), ("xhigh", "Muy alto"), ("max", "Máximo"))
M, P, H, I, AU, F, O = (
	"Modelo y razonamiento", "Permisos y sandbox", "Herramientas y entorno", "Interfaz de la terminal",
	"Autenticación", "Funciones", "Claves que ya no tienen efecto",
)

AJUSTES = (
	# Modelo
	Ajuste("model", "Modelo", TEXTO, "Modelo con el que trabaja Codex, por ejemplo el que eliges en /model. "
		"Vacío: el predeterminado de Codex.", M),
	Ajuste("model_reasoning_effort", "Esfuerzo de razonamiento", OPCION,
		"Cuánto piensa el modelo. Según el modelo, algunos niveles no existen.", M, ESFUERZOS, libre=True),
	Ajuste("plan_mode_reasoning_effort", "Esfuerzo en el modo plan", OPCION, "", M, ESFUERZOS, libre=True),
	Ajuste("model_reasoning_summary", "Resumen del razonamiento", OPCION, "Si muestra un resumen de lo que piensa.", M,
		(("auto", "Automático"), ("concise", "Breve"), ("detailed", "Detallado"), ("none", "Ninguno"))),
	Ajuste("model_verbosity", "Extensión de las respuestas", OPCION, "", M,
		(("low", "Breves"), ("medium", "Normales"), ("high", "Largas"))),
	Ajuste("review_model", "Modelo para las revisiones", TEXTO, "Modelo que usa /review.", M),
	Ajuste("service_tier", "Nivel de servicio", OPCION, "«fast» responde más rápido (gasta más cuota).", M,
		(("fast", "Rápido"),), libre=True),
	Ajuste("model_context_window", "Ventana de contexto", ENTERO, "Unidades de texto (tokens) de memoria del modelo.", M,
		minimo=1),
	Ajuste("model_auto_compact_token_limit", "Compactar a partir de", ENTERO,
		"Unidades de texto a partir de las que la conversación se resume sola.", M, minimo=1),
	Ajuste("developer_instructions", "Instrucciones adicionales", TEXTO,
		"Instrucciones que se añaden siempre (las de cada proyecto van mejor en AGENTS.md).", M),
	Ajuste("model_provider", "Proveedor del modelo", TEXTO, "Identificador del proveedor (por defecto «openai»).", M,
		ambitos=SOLO_USUARIO),
	Ajuste("oss_provider", "Proveedor de modelos locales", OPCION, "Para «codex --oss».", M,
		(("ollama", "Ollama"), ("lmstudio", "LM Studio"))),

	# Permisos y sandbox
	Ajuste("approval_policy", "Cuándo pide permiso", OPCION,
		"«on-request»: Codex decide cuándo preguntarte. «never»: nunca pregunta.", P,
		(("on-request", "Cuando lo necesita"), ("never", "Nunca")), defecto="Lo decide Codex",
		riesgo="Con «Nunca», Codex ejecuta órdenes sin preguntarte."),
	Ajuste("sandbox_mode", "Sandbox", OPCION, "Qué puede tocar Codex al ejecutar órdenes.", P,
		(("read-only", "Solo lectura"), ("workspace-write", "Escribir en el proyecto"),
			("danger-full-access", "Acceso total (sin sandbox)")), defecto="Lo decide Codex",
		riesgo="«Acceso total» quita la protección: Codex puede cambiar cualquier archivo de tu equipo."),
	Ajuste("sandbox_workspace_write.network_access", "Red dentro del sandbox", BOOL,
		"Permite conexiones a Internet en el modo «Escribir en el proyecto».", P, defecto="Desactivado",
		riesgo="Las órdenes podrán descargar o enviar datos."),
	Ajuste("sandbox_workspace_write.writable_roots", "Carpetas adicionales con escritura", LISTA,
		"Otras carpetas en las que Codex también puede escribir.", P, riesgo="Codex podrá cambiar archivos de esas carpetas."),
	Ajuste("windows.sandbox", "Sandbox de Windows", OPCION,
		"Cómo aísla Codex las órdenes en Windows. «mxc» es el recomendado en Windows 11 24H2 o posterior.", P,
		(("mxc", "MXC (nativo, recomendado)"), ("elevated", "Elevado (usuario aparte)"),
			("unelevated", "Sin elevar (aísla menos)")), defecto="Elevado"),
	Ajuste("features.prefer_mxc", "Usar MXC si está disponible", BOOL, "Si MXC no existe, usa otro modo.", P),
	Ajuste("allow_login_shell", "Usar un shell de inicio de sesión", BOOL, "", P, defecto="Activado"),
	Ajuste("approvals_reviewer", "Quién revisa las aprobaciones", OPCION, "", P,
		(("user", "Tú"), ("auto_review", "Revisión automática")), defecto="Tú"),

	# Herramientas y entorno
	Ajuste("web_search", "Búsqueda web", OPCION, "", H,
		(("disabled", "Desactivada"), ("cached", "Resultados guardados"), ("indexed", "Índice"), ("live", "En vivo")),
		defecto="Resultados guardados"),
	Ajuste("tools.view_image", "Ver imágenes", BOOL, "Herramienta para que Codex mire imágenes.", H),
	Ajuste("shell_environment_policy.inherit", "Variables que heredan las órdenes", OPCION, "", H,
		(("all", "Todas"), ("core", "Solo las básicas"), ("none", "Ninguna"))),
	Ajuste("shell_environment_policy.ignore_default_excludes", "Conservar variables con secretos", BOOL,
		"Activado: las variables que contienen KEY, SECRET o TOKEN llegan también a las órdenes.", H, defecto="Activado",
		riesgo="Las órdenes que ejecute Codex podrán leer tus claves y contraseñas de las variables de entorno."),
	Ajuste("shell_environment_policy.set", "Variables que se fijan", DICCIONARIO,
		"Variables de entorno que Codex pone siempre a las órdenes.", H),
	Ajuste("background_terminal_max_timeout", "Tiempo máximo de las órdenes en segundo plano (ms)", ENTERO, "", H,
		defecto="300000", minimo=1000),
	Ajuste("project_doc_max_bytes", "Tamaño máximo de AGENTS.md", ENTERO, "Bytes de AGENTS.md que se leen.", H,
		defecto="32768", minimo=1),
	Ajuste("project_root_markers", "Marcadores de la raíz del proyecto", LISTA, "Por defecto «.git».", H),
	Ajuste("notify", "Orden de notificación", LISTA,
		"Programa y argumentos que se ejecutan al terminar cada turno.", H, ambitos=SOLO_USUARIO,
		riesgo="Ejecuta un programa de tu equipo al final de cada turno."),
	Ajuste("history.persistence", "Guardar el historial", OPCION, "", H,
		(("save-all", "Guardar todo"), ("none", "No guardar"))),
	Ajuste("history.max_bytes", "Tamaño máximo del historial", ENTERO, "", H, minimo=1),
	Ajuste("check_for_update_on_startup", "Buscar actualizaciones al arrancar", BOOL, "", H),
	Ajuste("analytics.enabled", "Estadísticas de uso", BOOL, "", H),
	Ajuste("feedback.enabled", "Enviar comentarios", BOOL, "", H, defecto="Activado"),

	# Interfaz
	Ajuste("tui.animations", "Animaciones", BOOL, "Desactívalas si usas lector de pantalla.", I, defecto="Activado"),
	Ajuste("tui.raw_output_mode", "Salida en bruto", BOOL, "Texto sin formato en la terminal.", I, defecto="Desactivado"),
	Ajuste("tui.alternate_screen", "Pantalla alternativa", OPCION, "", I,
		(("auto", "Automático"), ("always", "Siempre"), ("never", "Nunca")), defecto="Automático"),
	Ajuste("tui.show_tooltips", "Mostrar consejos", BOOL, "", I, defecto="Activado"),
	Ajuste("tui.notification_method", "Método de notificación", OPCION, "", I,
		(("auto", "Automático"), ("osc9", "OSC 9"), ("bel", "Campana")), defecto="Automático"),
	Ajuste("tui.notification_condition", "Cuándo notificar", OPCION, "", I,
		(("unfocused", "Si la terminal no está delante"), ("always", "Siempre"))),
	Ajuste("tui.vim_mode_default", "Modo Vim", BOOL, "", I),
	Ajuste("file_opener", "Abrir archivos con", OPCION, "Editor de los enlaces a archivos.", I,
		(("vscode", "Visual Studio Code"), ("vscode-insiders", "VS Code Insiders"), ("cursor", "Cursor"),
			("windsurf", "Windsurf"), ("none", "Ninguno")), defecto="Visual Studio Code"),
	Ajuste("hide_agent_reasoning", "Ocultar el razonamiento", BOOL, "", I),
	Ajuste("show_raw_agent_reasoning", "Mostrar el razonamiento en bruto", BOOL, "", I),

	# Autenticación
	Ajuste("forced_login_method", "Forzar el inicio de sesión", OPCION, "", AU,
		(("chatgpt", "Cuenta de ChatGPT (suscripción)"), ("api", "Clave de API (de pago)")),
		riesgo="Con «Clave de API», Codex cobra por uso en lugar de usar tu suscripción."),
	Ajuste("cli_auth_credentials_store", "Dónde se guarda la sesión", OPCION, "", AU,
		(("auto", "Automático"), ("keyring", "Almacén de Windows"), ("file", "Archivo"), ("ephemeral", "Solo mientras está abierto"))),

	# Funciones
	Ajuste("features.memories", "Memorias", BOOL, "Codex recuerda cosas entre conversaciones.", F, defecto="Desactivado"),
	Ajuste("features.hooks", "Hooks", BOOL, "", F, defecto="Activado"),
	Ajuste("features.multi_agent", "Varios agentes", BOOL, "", F, defecto="Activado"),
	Ajuste("features.fast_mode", "Modo rápido", BOOL, "", F, defecto="Activado"),
	Ajuste("features.prevent_idle_sleep", "Impedir la suspensión mientras trabaja", BOOL, "", F, defecto="Desactivado"),
	Ajuste("suppress_unstable_features_warning", "No avisar de funciones experimentales", BOOL, "", F),

	# Obsoletas: solo aparecen si están en el archivo, para poder quitarlas.
	Ajuste("profile", "Perfil activo (antiguo)", TEXTO,
		"Ya no se lee desde Codex 0.134: los perfiles van en archivos «nombre.config.toml». Puedes quitarla.", O, obsoleta=True),
	Ajuste("preferred_auth_method", "Método de inicio de sesión (antiguo)", TEXTO, "Ya no tiene efecto. Puedes quitarla.", O,
		obsoleta=True),
	Ajuste("disable_response_storage", "No guardar respuestas (antiguo)", BOOL, "Ya no tiene efecto. Puedes quitarla.", O,
		obsoleta=True),
)

AGENTE = AgenteConfigurable(
	id="codex",
	nombre="Codex",
	formato="toml",
	ambitos=AMBITOS,
	ajustes=AJUSTES,
	documentacion="https://learn.chatgpt.com/docs/config-file/config-reference",
	nota="Los proyectos de confianza, los servidores MCP, los complementos y los proveedores no se listan aquí: se "
		"conservan tal cual. Para cambiarlos, abre el archivo en tu editor.",
	ocultas=("projects", "plugins", "marketplaces", "mcp_servers", "model_providers", "hooks", "apps", "permissions",
		"profiles", "desktop", "agents", "otel", "notice", "skills", "memories", "computer_use", "browser_use"),
)
