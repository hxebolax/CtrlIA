"""Opciones de ``settings.json`` de Gemini CLI (formato por categorías).

Fuente: https://github.com/google-gemini/gemini-cli docs/reference/configuration.md
y schemas/settings.schema.json (consultados el 18 de octubre de 2026). Muchas
opciones se aplican al reiniciar Gemini CLI.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.services.config_agentes.modelo import (
	BOOL,
	DECIMAL,
	ENTERO,
	JSON,
	LISTA,
	OPCION,
	TEXTO,
	AgenteConfigurable,
	Ajuste,
	Ambito,
)

USUARIO, PROYECTO = "usuario", "proyecto"


def _raiz() -> Path:
	otra = os.environ.get("GEMINI_CLI_HOME")
	return (Path(otra) if otra else Path.home()) / ".gemini"


AMBITOS = (
	Ambito(USUARIO, "Tu usuario (todas las carpetas)", "~/.gemini/settings.json: vale para todos tus proyectos.",
		lambda _carpeta: _raiz() / "settings.json"),
	Ambito(PROYECTO, "Proyecto", "<proyecto>/.gemini/settings.json: solo se lee si la carpeta es de confianza.",
		lambda carpeta: carpeta / ".gemini" / "settings.json" if carpeta else None, necesita_carpeta=True),
)

AC, G, M, P, C, H, U, PR = (
	"Accesibilidad", "General", "Modelo", "Permisos y seguridad", "Contexto y archivos", "Herramientas",
	"Interfaz de la terminal", "Privacidad",
)
TEMAS = tuple((t, t) for t in (
	"Default", "ANSI", "Atom One", "Ayu", "Dracula", "GitHub", "Shades Of Purple", "Solarized Dark", "Tokyo Night",
	"Default Light", "ANSI Light", "Ayu Light", "GitHub Light", "Google Code", "Solarized Light", "Xcode",
))

AJUSTES = (
	Ajuste("ui.accessibility.screenReader", "Modo lector de pantalla", BOOL,
		"Gemini CLI escribe texto plano pensado para NVDA y JAWS. Se aplica al reiniciar.", AC, defecto="Desactivado"),
	Ajuste("ui.loadingPhrases", "Frases mientras piensa", OPCION, "«Desactivadas» evita frases que el lector repite.", AC,
		(("off", "Desactivadas"), ("tips", "Consejos"), ("witty", "Ingeniosas"), ("all", "Todas")), defecto="Desactivadas"),
	Ajuste("ui.showSpinner", "Indicador de actividad", BOOL, "", AC, defecto="Activado"),

	Ajuste("general.defaultApprovalMode", "Modo de aprobación inicial", OPCION, "Qué puede hacer sin preguntarte. "
		"El modo YOLO no se puede poner aquí: solo al arrancar con --yolo.", P,
		(("default", "Preguntar"), ("auto_edit", "Aprobar las ediciones solo"), ("plan", "Solo planificar")),
		defecto="Preguntar", riesgo="Con «Aprobar las ediciones solo», Gemini cambia archivos sin preguntarte."),
	Ajuste("tools.allowed", "Herramientas sin confirmación", LISTA,
		"Herramientas que no piden permiso, por ejemplo «run_shell_command(git)».", P,
		riesgo="Se ejecutan sin preguntarte."),
	Ajuste("tools.confirmationRequired", "Herramientas que piden permiso siempre", LISTA, "", P),
	Ajuste("tools.exclude", "Herramientas desactivadas", LISTA, "", P),
	Ajuste("tools.core", "Herramientas incorporadas permitidas", LISTA, "Si la usas, solo estas están disponibles.", P),
	Ajuste("tools.sandbox", "Sandbox", JSON, "true, false o el nombre de un sandbox entre comillas (\"docker\", "
		"\"podman\", \"windows-native\"). Se aplica al reiniciar.", P,
		riesgo="Sin sandbox, las órdenes se ejecutan directamente en tu equipo."),
	Ajuste("tools.sandboxNetworkAccess", "Red dentro del sandbox", BOOL, "", P, defecto="Desactivado",
		riesgo="Las órdenes podrán descargar o enviar datos."),
	Ajuste("security.folderTrust.enabled", "Confianza por carpeta", BOOL,
		"Pregunta si confías en una carpeta antes de usar su configuración.", P, defecto="Activado",
		riesgo="Desactivada, cualquier carpeta podrá aplicar su configuración y ejecutar sus órdenes."),
	Ajuste("security.disableYoloMode", "Prohibir el modo YOLO", BOOL, "", P, defecto="Desactivado"),
	Ajuste("security.disableAlwaysAllow", "Quitar «Permitir siempre»", BOOL, "", P, defecto="Desactivado"),
	Ajuste("security.enablePermanentToolApproval", "Permitir para todas las sesiones futuras", BOOL, "", P,
		riesgo="Una aprobación quedará para siempre."),
	Ajuste("security.toolSandboxing", "Aislar cada herramienta", BOOL, "", P, defecto="Desactivado"),
	Ajuste("security.auth.selectedType", "Tipo de inicio de sesión", OPCION, "", P,
		(("oauth-personal", "Cuenta de Google"), ("gemini-api-key", "Clave de API de Gemini"), ("vertex-ai", "Vertex AI"),
			("cloud-shell", "Cloud Shell"), ("compute-default-credentials", "Credenciales de Google Cloud")),
		riesgo="Con una clave de API, el uso puede cobrarse."),
	Ajuste("billing.overageStrategy", "Uso por encima de tu cuota", OPCION, "", P,
		(("ask", "Preguntar"), ("never", "Nunca"), ("always", "Siempre")), defecto="Preguntar",
		riesgo="Con «Siempre», puede cobrarse el uso extra sin preguntarte."),

	Ajuste("model.name", "Modelo", TEXTO, "Por ejemplo gemini-2.5-pro. Vacío: el predeterminado.", M),
	Ajuste("model.maxSessionTurns", "Turnos máximos por sesión", ENTERO, "-1: sin límite.", M, defecto="-1", minimo=-1),
	Ajuste("model.compressionThreshold", "Umbral de compresión del historial", DECIMAL, "De 0 a 1.", M, defecto="0.5",
		minimo=0, maximo=1),
	Ajuste("model.disableLoopDetection", "Desactivar la detección de bucles", BOOL, "", M),

	Ajuste("general.preferredEditor", "Editor preferido", OPCION, "", G,
		tuple((e, e) for e in ("vscode", "vscodium", "cursor", "windsurf", "zed", "antigravity", "sublimetext", "vim",
			"neovim", "emacs", "micro")), libre=True),
	Ajuste("general.vimMode", "Modo Vim", BOOL, "", G),
	Ajuste("general.enableAutoUpdate", "Actualizarse solo", BOOL, "", G, defecto="Activado"),
	Ajuste("general.enableAutoUpdateNotification", "Avisar de actualizaciones", BOOL, "", G, defecto="Activado"),
	Ajuste("general.enableNotifications", "Notificaciones", BOOL, "", G, defecto="Desactivado"),
	Ajuste("general.checkpointing.enabled", "Puntos de restauración", BOOL,
		"Guarda cómo estaban los archivos antes de cada edición para poder volver atrás.", G, defecto="Desactivado"),
	Ajuste("general.sessionRetention.maxAge", "Antigüedad máxima de las sesiones", TEXTO,
		"Por ejemplo «30d» (30 días).", G, defecto="30d"),
	Ajuste("general.maxAttempts", "Reintentos", ENTERO, "", G, defecto="10", minimo=1, maximo=10),

	Ajuste("context.fileName", "Archivos de contexto", JSON, "Nombre (\"GEMINI.md\") o lista de nombres.", C,
		defecto="GEMINI.md"),
	Ajuste("context.includeDirectories", "Carpetas adicionales", LISTA, "", C),
	Ajuste("context.fileFiltering.respectGitIgnore", "Respetar .gitignore", BOOL, "", C, defecto="Activado"),
	Ajuste("context.fileFiltering.respectGeminiIgnore", "Respetar .geminiignore", BOOL, "", C, defecto="Activado"),
	Ajuste("context.includeDirectoryTree", "Incluir el árbol de carpetas", BOOL, "", C, defecto="Activado"),

	Ajuste("tools.shell.enableInteractiveShell", "Terminal interactiva", BOOL, "", H, defecto="Activado"),
	Ajuste("tools.shell.showColor", "Colores en la salida", BOOL, "", H, defecto="Activado"),
	Ajuste("tools.shell.inactivityTimeout", "Espera sin actividad (segundos)", ENTERO, "", H, defecto="300", minimo=1),
	Ajuste("tools.useRipgrep", "Buscar con ripgrep", BOOL, "", H, defecto="Activado"),
	Ajuste("mcp.allowed", "Servidores MCP permitidos", LISTA, "", H),
	Ajuste("mcp.excluded", "Servidores MCP excluidos", LISTA, "", H),
	Ajuste("hooksConfig.enabled", "Hooks", BOOL, "", H, defecto="Activado"),
	Ajuste("skills.enabled", "Skills", BOOL, "", H, defecto="Activado"),

	Ajuste("ui.theme", "Tema de colores", OPCION, "", U, TEMAS, libre=True),
	Ajuste("ui.hideTips", "Ocultar los consejos", BOOL, "", U),
	Ajuste("ui.hideBanner", "Ocultar el cartel de inicio", BOOL, "", U),
	Ajuste("ui.hideFooter", "Ocultar el pie", BOOL, "", U),
	Ajuste("ui.showLineNumbers", "Números de línea", BOOL, "", U, defecto="Activado"),
	Ajuste("ui.showCitations", "Mostrar las citas", BOOL, "", U),
	Ajuste("ui.inlineThinkingMode", "Razonamiento en la conversación", OPCION, "", U,
		(("off", "No mostrar"), ("full", "Completo")), defecto="No mostrar"),
	Ajuste("ui.errorVerbosity", "Detalle de los errores", OPCION, "", U, (("low", "Poco"), ("full", "Todo")), defecto="Poco"),

	Ajuste("privacy.usageStatisticsEnabled", "Estadísticas de uso", BOOL, "", PR, defecto="Activado"),
	Ajuste("telemetry.enabled", "Telemetría", BOOL, "", PR, defecto="Desactivado"),
	Ajuste("telemetry.logPrompts", "Incluir tus mensajes en la telemetría", BOOL, "", PR, defecto="Activado",
		riesgo="Tus mensajes quedarán en los registros de telemetría."),
)

AGENTE = AgenteConfigurable(
	id="gemini",
	nombre="Gemini CLI",
	formato="json",
	ambitos=AMBITOS,
	ajustes=AJUSTES,
	documentacion="https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md",
	nota="Gemini CLI solo admite claves que conoce: una clave mal escrita invalida el archivo.",
	ocultas=("mcpServers", "hooks", "modelConfigs", "agents", "ui.customThemes"),
)
