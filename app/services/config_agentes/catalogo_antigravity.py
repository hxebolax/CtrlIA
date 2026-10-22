"""Opciones de ``settings.json`` de Antigravity CLI (``agy``).

Fuente: https://antigravity.google/docs/settings?tab=cli y /docs/permissions
(consultadas el 18 de octubre de 2026). Solo se incluyen las claves
confirmadas en la documentación oficial. El archivo solo guarda lo que
difiere de los valores por defecto.
"""

from __future__ import annotations

from app.services.config_agentes.modelo import BOOL, JSON, LISTA, OPCION, TEXTO, AgenteConfigurable, Ajuste, Ambito


def _ruta():
	from app.services.agentes.antigravity import ruta_ajustes_cli

	return ruta_ajustes_cli()


AMBITOS = (
	Ambito("usuario", "Tu usuario", "~/.gemini/antigravity-cli/settings.json: el de Antigravity CLI.", lambda _carpeta: _ruta()),
)

AC, P, I, PR = "Accesibilidad", "Permisos y sandbox", "Interfaz de la terminal", "Privacidad"

AJUSTES = (
	Ajuste("runningLightSpeed", "Animación de progreso", OPCION,
		"La luz que se mueve mientras trabaja. «Desactivada» evita que el lector la anuncie.", AC,
		(("off", "Desactivada"), ("slow", "Lenta"), ("medium", "Normal"), ("fast", "Rápida")), defecto="Normal"),
	Ajuste("verbosity", "Detalle de la salida", OPCION, "", AC, (("high", "Mucho"), ("low", "Poco")), defecto="Mucho"),
	Ajuste("showTips", "Mostrar consejos", BOOL, "", AC, defecto="Activado"),

	Ajuste("toolPermission", "Ejecución de órdenes y herramientas", OPCION,
		"Cuánta autonomía tiene con la terminal y las herramientas.", P,
		(("request-review", "Pedir revisión"), ("proceed-in-sandbox", "Hacerlo dentro del sandbox"),
			("always-proceed", "Hacerlo siempre"), ("strict", "Estricto")), defecto="Pedir revisión",
		riesgo="Con «Hacerlo siempre», ejecuta órdenes sin preguntarte."),
	Ajuste("artifactReviewPolicy", "Revisión de los planes y documentos", OPCION, "", P,
		(("asks-for-review", "Pedir revisión"), ("agent-decides", "Decide el agente"), ("always-proceed", "Seguir siempre")),
		defecto="Pedir revisión", riesgo="Con «Seguir siempre», no te pide revisar sus planes."),
	Ajuste("agentMode", "Modo del agente", OPCION, "", P,
		(("default", "Normal"), ("accept-edits", "Aceptar las ediciones solo"), ("plan", "Solo planificar")),
		defecto="Normal", riesgo="Con «Aceptar las ediciones solo», cambia archivos sin preguntarte."),
	Ajuste("enableTerminalSandbox", "Sandbox de la terminal", BOOL,
		"Las órdenes se ejecutan aisladas (AppContainer en Windows).", P, defecto="Desactivado"),
	Ajuste("allowNonWorkspaceAccess", "Acceso fuera del proyecto", BOOL, "", P, defecto="Desactivado",
		riesgo="Podrá leer y cambiar archivos fuera de la carpeta de trabajo."),
	Ajuste("permissions.allow", "Permitido sin preguntar", LISTA,
		"Reglas «acción(objetivo)»: command(git), command(regex:npm run (build|test)), read_file(src/), write_file(src/), "
		"read_url(google.com), mcp(servidor/*). Son las mismas que Ctrl+IA añade con «Permitir y reintentar».", P,
		riesgo="Lo que pongas aquí se hace sin preguntarte."),
	Ajuste("permissions.ask", "Preguntar siempre", LISTA, "Por ejemplo command(*) pregunta por todas las órdenes.", P),
	Ajuste("permissions.deny", "Prohibido", LISTA, "Por ejemplo command(rm -rf), command(sudo), write_file(.git/).", P),

	Ajuste("colorScheme", "Colores", OPCION, "", I,
		(("terminal", "Los de la terminal"), ("light", "Claro"), ("dark", "Oscuro"), ("solarized light", "Solarized claro"),
			("solarized dark", "Solarized oscuro"), ("colorblind-friendly light", "Claro para daltonismo"),
			("colorblind-friendly dark", "Oscuro para daltonismo"), ("tokyo night", "Tokyo Night")), defecto="Los de la terminal"),
	Ajuste("altScreenMode", "Pantalla alternativa", OPCION, "", I,
		(("default", "Automática"), ("always", "Siempre"), ("never", "Nunca")), defecto="Automática"),
	Ajuste("editor", "Editor", TEXTO, "«auto» usa el de la variable EDITOR; también «vim», «emacs»...", I, defecto="auto"),
	Ajuste("editorMode", "Modo del editor", OPCION, "", I, (("default", "Normal"), ("vim", "Vim")), defecto="Normal"),
	Ajuste("notifications", "Notificaciones", BOOL, "", I, defecto="Desactivado"),
	Ajuste("statusLine", "Línea de estado personalizada", JSON, "{\"type\": \"command\", \"command\": \"...\"}", I,
		riesgo="Ejecuta una orden de tu equipo."),

	Ajuste("enableTelemetry", "Telemetría", BOOL, "", PR, defecto="Activado"),
	Ajuste("showFeedbackSurvey", "Encuestas de opinión", BOOL, "", PR, defecto="Activado"),
)

AGENTE = AgenteConfigurable(
	id="antigravity",
	nombre="Antigravity CLI",
	formato="json",
	ambitos=AMBITOS,
	ajustes=AJUSTES,
	documentacion="https://antigravity.google/docs/settings?tab=cli",
	nota="Antigravity IDE y la app de escritorio se configuran en su propia ventana: no tienen archivo documentado.",
)
