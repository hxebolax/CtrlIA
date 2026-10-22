"""Opciones de ``settings.json`` de Claude Code.

Fuente: https://code.claude.com/docs/en/settings-reference (consultada el
18 de octubre de 2026), más permissions, env-vars y hooks. Se omiten las claves
que solo admite la configuración gestionada por una organización.

Ámbitos (de menos a más prioridad): usuario, proyecto compartido y proyecto
local. Las listas de permisos de los distintos archivos se suman.
"""

from __future__ import annotations

from pathlib import Path

from app.services.config_agentes.modelo import (
	BOOL,
	DICCIONARIO,
	ENTERO,
	JSON,
	LISTA,
	OPCION,
	TEXTO,
	AgenteConfigurable,
	Ajuste,
	Ambito,
)

USUARIO, PROYECTO, LOCAL = "usuario", "proyecto", "local"
SOLO_USUARIO = (USUARIO, LOCAL)


def _raiz() -> Path:
	from app.services.historial.claude import raiz_claude

	return raiz_claude()


AMBITOS = (
	Ambito(USUARIO, "Tu usuario (todas las carpetas)",
		"~/.claude/settings.json: vale para todos tus proyectos.", lambda _carpeta: _raiz() / "settings.json"),
	Ambito(PROYECTO, "Proyecto, compartido",
		"<proyecto>/.claude/settings.json: se comparte con quien use el repositorio.",
		lambda carpeta: carpeta / ".claude" / "settings.json" if carpeta else None, necesita_carpeta=True),
	Ambito(LOCAL, "Proyecto, solo tú",
		"<proyecto>/.claude/settings.local.json: solo para ti en ese proyecto; manda sobre el compartido.",
		lambda carpeta: carpeta / ".claude" / "settings.local.json" if carpeta else None, necesita_carpeta=True),
)

MODOS = (
	("default", "Preguntar (lo normal)"),
	("acceptEdits", "Aceptar las ediciones de archivos sin preguntar"),
	("plan", "Solo planificar (no cambia nada)"),
	("auto", "Automático (un clasificador decide)"),
	("dontAsk", "No preguntar: deniega lo que habría preguntado"),
	("bypassPermissions", "Saltarse los permisos (todo sin preguntar)"),
)
MODELOS = (
	("default", "Predeterminado de tu cuenta"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"),
	("fable", "Fable"), ("opusplan", "Opus para planificar y Sonnet para ejecutar"),
)
ESFUERZOS = (("low", "Bajo"), ("medium", "Medio"), ("high", "Alto"), ("xhigh", "Muy alto"))
MAX_ESFUERZOS = (*ESFUERZOS, ("max", "Máximo"))

M, P, I, MC, G, H, R, A, PL, PR, AC = (
	"Modelo y respuestas", "Permisos", "Interfaz de la terminal", "Memoria y contexto", "Git y atribución",
	"Hooks y automatización", "Remoto y notificaciones", "Actualizaciones", "Plugins y skills", "Privacidad",
	"Accesibilidad",
)

AJUSTES = (
	# Accesibilidad primero: lo que más importa con un lector de pantalla.
	Ajuste("axScreenReader", "Modo lector de pantalla en la terminal", BOOL,
		"Claude Code escribe texto plano, sin bordes, recuadros ni animaciones, pensado para NVDA y JAWS. "
		"Afecta a Claude Code cuando lo usas en la terminal; en Ctrl+IA no cambia nada.", AC, defecto="Desactivado"),
	Ajuste("prefersReducedMotion", "Reducir las animaciones", BOOL,
		"Menos animaciones en la terminal (útil también con lector de pantalla).", AC, defecto="Desactivado"),
	Ajuste("spinnerTipsEnabled", "Consejos mientras trabaja", BOOL,
		"Muestra consejos junto al indicador de «pensando» en la terminal.", AC, defecto="Activado"),
	Ajuste("language", "Idioma de las respuestas", TEXTO,
		"Idioma en el que Claude responde, por ejemplo «spanish». Vacío: el de tu mensaje.", AC),

	# Modelo
	Ajuste("model", "Modelo", OPCION, "Modelo con el que empieza cada sesión. Se lee al arrancar Claude Code. "
		"Puedes escribir también un identificador completo.", M, MODELOS, defecto="El de tu cuenta", libre=True),
	Ajuste("effortLevel", "Esfuerzo predeterminado", OPCION,
		"Cuánto piensa el modelo antes de responder.", M, ESFUERZOS, defecto="El del modelo"),
	Ajuste("maxEffortLevel", "Esfuerzo máximo permitido", OPCION, "Tope del esfuerzo que se puede pedir.", M, MAX_ESFUERZOS),
	Ajuste("alwaysThinkingEnabled", "Razonamiento extendido", BOOL,
		"Desactivado: Claude no hace el razonamiento extendido antes de responder (más rápido, menos cuidadoso).",
		M, defecto="Activado"),
	Ajuste("showThinkingSummaries", "Resúmenes del razonamiento", BOOL, "Muestra un resumen de lo que Claude piensa.", M,
		defecto="Desactivado"),
	Ajuste("outputStyle", "Estilo de salida", OPCION, "Cómo explica Claude lo que hace: el normal, «Explanatory» "
		"(explica más) o «Learning» (te deja partes para que aprendas). También el nombre de un estilo propio.",
		M, (("default", "Normal"), ("Explanatory", "Explicativo"), ("Learning", "Aprendizaje")), libre=True),
	Ajuste("fallbackModel", "Modelos de reserva", LISTA,
		"Modelos que se usan si el principal está saturado, en orden.", M),
	Ajuste("availableModels", "Modelos que se pueden elegir", LISTA, "Limita los modelos del selector /model.", M),
	Ajuste("fastMode", "Modo rápido", BOOL, "Respuestas más rápidas del mismo modelo (puede gastar más cuota).", M,
		defecto="Desactivado"),
	Ajuste("advisorModel", "Modelo asesor", TEXTO, "Modelo que revisa el trabajo del principal (fable, opus, sonnet o "
		"un identificador). Vacío: sin asesor.", M),

	# Permisos
	Ajuste("permissions.defaultMode", "Modo de permisos inicial", OPCION,
		"Qué puede hacer Claude sin preguntarte al empezar cada sesión.", P, MODOS, defecto="Preguntar",
		riesgo="«Saltarse los permisos» y «Automático» dejan a Claude ejecutar órdenes y cambiar archivos sin "
			"preguntarte. Desde un proyecto no tienen efecto: solo desde tu usuario."),
	Ajuste("permissions.allow", "Permitido sin preguntar", LISTA,
		"Reglas de lo que Claude hace sin preguntarte, una por línea. Ejemplos: «Bash(npm run test *)», «Read(./docs/**)», "
		"«WebFetch(domain:github.com)», «mcp__servidor». «Bash» a secas permite cualquier orden.", P,
		riesgo="Lo que pongas aquí se ejecuta sin preguntarte."),
	Ajuste("permissions.ask", "Preguntar siempre", LISTA,
		"Reglas de lo que Claude debe preguntar siempre, aunque otra regla lo permita.", P),
	Ajuste("permissions.deny", "Prohibido", LISTA,
		"Reglas de lo que Claude no puede hacer nunca, ni en el modo «saltarse los permisos». Ejemplos: «Read(./.env)», "
		"«Bash(git push *)», «WebFetch».", P),
	Ajuste("permissions.additionalDirectories", "Carpetas adicionales", LISTA,
		"Carpetas fuera del proyecto en las que Claude también puede trabajar.", P,
		riesgo="Claude podrá leer y cambiar archivos de esas carpetas."),
	Ajuste("permissions.blockReadsOutsideWorkingDirectories", "No leer fuera de las carpetas de trabajo", BOOL,
		"Impide que Claude lea archivos fuera del proyecto y de las carpetas adicionales.", P),
	Ajuste("permissions.disableBypassPermissionsMode", "Prohibir el modo «saltarse los permisos»", OPCION,
		"Impide usar ese modo aunque se pida.", P, (("disable", "Prohibido"),)),
	Ajuste("skipDangerousModePermissionPrompt", "No confirmar al saltarse los permisos", BOOL,
		"Omite el aviso que pide confirmación antes de entrar en el modo «saltarse los permisos».", P, ambitos=SOLO_USUARIO,
		riesgo="Sin ese aviso es fácil dejar a Claude sin ningún control."),
	Ajuste("useAutoModeDuringPlan", "Modo automático durante el plan", BOOL,
		"Usa el clasificador del modo automático mientras planifica.", P, defecto="Activado", ambitos=SOLO_USUARIO),

	# Memoria y contexto
	Ajuste("autoCompactEnabled", "Compactar la conversación automáticamente", BOOL,
		"Cuando la conversación llena la memoria del modelo, Claude la resume para seguir.", MC, defecto="Activado"),
	Ajuste("autoCompactWindow", "Tamaño de la ventana de compactación", ENTERO,
		"Unidades de texto (tokens) a partir de las que se compacta.", MC, minimo=100000, maximo=1000000),
	Ajuste("idleCompaction", "Compactar en reposo", BOOL, "Compacta mientras la sesión está inactiva.", MC, defecto="Activado"),
	Ajuste("autoMemoryEnabled", "Memoria automática", BOOL,
		"Claude apunta solo lo que aprende de ti y del proyecto para recordarlo en otras sesiones.", MC, defecto="Activado"),
	Ajuste("autoMemoryDirectory", "Carpeta de la memoria automática", TEXTO, "Dónde se guarda esa memoria.", MC,
		defecto="~/.claude/projects/<proyecto>/memory"),
	Ajuste("claudeMdExcludes", "Archivos CLAUDE.md que se omiten", LISTA,
		"Patrones de archivos CLAUDE.md que Claude no debe leer.", MC),
	Ajuste("fileCheckpointingEnabled", "Instantáneas de archivos", BOOL,
		"Guarda cómo estaban los archivos para poder volver atrás con /rewind.", MC, defecto="Activado"),
	Ajuste("plansDirectory", "Carpeta de los planes", TEXTO, "Dónde se guardan los planes.", MC, defecto="~/.claude/plans"),
	Ajuste("bashOutputMaxChars", "Salida máxima de las órdenes", ENTERO,
		"Caracteres de la salida de cada orden que recibe Claude.", MC, defecto="30000", minimo=4000, maximo=128000),
	Ajuste("respectGitignore", "Respetar .gitignore", BOOL,
		"Los archivos ignorados por git no aparecen al buscar.", MC, defecto="Activado"),
	Ajuste("env", "Variables de entorno", DICCIONARIO,
		"Variables para Claude y las órdenes que ejecuta. Útiles: MAX_THINKING_TOKENS, BASH_DEFAULT_TIMEOUT_MS, "
		"BASH_MAX_OUTPUT_LENGTH, MCP_TIMEOUT, DISABLE_TELEMETRY, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, "
		"DISABLE_AUTOUPDATER, CLAUDE_AX_SCREEN_READER, HTTPS_PROXY. Se guardan en texto plano.", MC,
		riesgo="Se guardan sin cifrar. No pongas aquí ANTHROPIC_API_KEY: se cobraría por uso en lugar de tu suscripción."),

	# Interfaz de la terminal
	Ajuste("theme", "Tema de colores", OPCION, "Colores de Claude Code en la terminal.", I,
		(("auto", "Automático"), ("dark", "Oscuro"), ("light", "Claro"), ("dark-daltonized", "Oscuro para daltonismo"),
			("light-daltonized", "Claro para daltonismo"), ("dark-ansi", "Oscuro con colores de la terminal"),
			("light-ansi", "Claro con colores de la terminal")), defecto="Oscuro"),
	Ajuste("editorMode", "Modo del editor", OPCION, "Cómo se edita el cuadro de entrada.", I,
		(("normal", "Normal"), ("vim", "Vim")), defecto="Normal"),
	Ajuste("verbose", "Mostrar todos los detalles", BOOL, "Muestra la salida completa de las herramientas.", I),
	Ajuste("tui", "Interfaz a pantalla completa", OPCION, "Cómo se dibuja la terminal.", I,
		(("default", "Normal"), ("fullscreen", "Pantalla completa"))),
	Ajuste("showTurnDuration", "Decir cuánto ha tardado", BOOL, "Muestra la duración de cada turno.", I, defecto="Activado"),
	Ajuste("terminalProgressBarEnabled", "Barra de progreso de la terminal", BOOL, "", I, defecto="Activado"),
	Ajuste("syntaxHighlightingDisabled", "Desactivar el resaltado de código", BOOL, "", I),
	Ajuste("promptSuggestionEnabled", "Sugerencias de mensajes", BOOL, "", I, defecto="Activado"),
	Ajuste("timeFormat", "Formato de la hora", OPCION, "", I,
		(("auto", "Automático"), ("12-hour", "12 horas"), ("24-hour", "24 horas"), ("24-hour-utc", "24 horas, UTC")), libre=True),
	Ajuste("defaultShell", "Intérprete de órdenes", OPCION, "Qué intérprete usa Claude para las órdenes.", I,
		(("bash", "Bash (Git Bash)"), ("powershell", "PowerShell"))),
	Ajuste("statusLine", "Línea de estado personalizada", JSON,
		"Objeto JSON {\"type\": \"command\", \"command\": \"...\"}: una orden cuya salida se muestra en la línea de estado.", I,
		riesgo="Ejecuta una orden de tu equipo cada pocos segundos."),

	# Git
	Ajuste("attribution", "Atribución en commits y PR", JSON,
		"Objeto JSON {\"commit\": \"texto\", \"pr\": \"texto\", \"sessionUrl\": true} o false para no añadir ninguna firma.", G),
	Ajuste("includeCoAuthoredBy", "Añadir «Co-Authored-By» (antigua)", BOOL,
		"Sustituida por «Atribución en commits y PR».", G, obsoleta=True),
	Ajuste("includeGitInstructions", "Instrucciones de git", BOOL, "Incluye las instrucciones de cómo usar git.", G,
		defecto="Activado"),

	# Hooks
	Ajuste("hooks", "Hooks", JSON,
		"Órdenes que se ejecutan en momentos concretos (antes de usar una herramienta, al terminar...). Objeto JSON "
		"{\"PreToolUse\": [{\"matcher\": \"Bash\", \"hooks\": [{\"type\": \"command\", \"command\": \"...\"}]}]}.", H,
		riesgo="Los hooks ejecutan órdenes en tu equipo automáticamente."),
	Ajuste("disableAllHooks", "Desactivar todos los hooks", BOOL, "", H),
	Ajuste("disableWorkflows", "Desactivar los flujos de trabajo", BOOL, "", H),
	Ajuste("workflowSizeGuideline", "Tamaño de los flujos de trabajo", OPCION, "", H,
		(("small", "Pequeño"), ("medium", "Medio"), ("large", "Grande"), ("unrestricted", "Sin límite")), defecto="Medio"),

	# Plugins y skills
	Ajuste("enabledPlugins", "Plugins activados", JSON, "Objeto JSON {\"plugin@marketplace\": true}.", PL),
	Ajuste("extraKnownMarketplaces", "Marketplaces adicionales", JSON, "Fuentes de plugins de terceros.", PL,
		riesgo="Permite instalar código de terceros."),
	Ajuste("disableBundledSkills", "Desactivar las skills incluidas", BOOL, "", PL),
	Ajuste("enableAllProjectMcpServers", "Aprobar todos los servidores MCP del proyecto", BOOL,
		"Acepta sin preguntar todos los servidores de .mcp.json.", PL,
		riesgo="Un repositorio podría arrancar programas en tu equipo sin preguntarte."),
	Ajuste("enabledMcpjsonServers", "Servidores MCP del proyecto aprobados", LISTA, "", PL),
	Ajuste("disabledMcpjsonServers", "Servidores MCP del proyecto rechazados", LISTA, "", PL),

	# Remoto y notificaciones
	Ajuste("preferredNotifChannel", "Canal de notificaciones", OPCION, "", R,
		(("auto", "Automático"), ("terminal_bell", "Campana de la terminal"), ("notifications_disabled", "Desactivadas")),
		defecto="Automático", libre=True),
	Ajuste("agentPushNotifEnabled", "Avisos al móvil cuando termina", BOOL, "", R, defecto="Desactivado"),
	Ajuste("inputNeededNotifEnabled", "Avisos al móvil cuando te necesita", BOOL, "", R, defecto="Desactivado"),
	Ajuste("remoteControlAtStartup", "Control remoto al arrancar", BOOL, "", R),
	Ajuste("disableRemoteControl", "Desactivar el control remoto", BOOL, "", R),

	# Actualizaciones y privacidad
	Ajuste("autoUpdatesChannel", "Canal de actualizaciones", OPCION, "", A,
		(("latest", "Lo último"), ("stable", "Estable")), defecto="Lo último"),
	Ajuste("cleanupPeriodDays", "Días que se guardan las conversaciones", ENTERO,
		"Las transcripciones más antiguas se borran solas.", PR, defecto="30", minimo=1,
		riesgo="Al bajarlo, Claude borrará en silencio las conversaciones más antiguas."),
	Ajuste("forceLoginMethod", "Forzar el método de inicio de sesión", OPCION, "", PR,
		(("claudeai", "Cuenta de Claude (suscripción)"), ("console", "Consola (de pago por uso)"))),
	Ajuste("apiKeyHelper", "Programa que da la clave de API", TEXTO,
		"Orden que devuelve una clave de API.", PR,
		riesgo="Ejecuta una orden y hace que Claude use una clave de API, que se cobra por uso en lugar de tu suscripción."),
)

AGENTE = AgenteConfigurable(
	id="claude",
	nombre="Claude Code",
	formato="json",
	ambitos=AMBITOS,
	ajustes=AJUSTES,
	documentacion="https://code.claude.com/docs/en/settings-reference",
	nota="Los cambios valen para Claude Code en la terminal, en Ctrl+IA y en las demás apps que lo usan. "
		"El modelo y el esfuerzo se leen al empezar cada sesión.",
)
