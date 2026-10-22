"""Diálogo de preferencias accesible y declarativo.

Añadir una opción nueva es añadir un ``Opcion(...)`` a ``PAGINAS`` (y su valor
por defecto en ``configuracion.VALORES_POR_DEFECTO``); el diálogo crea el
control, su etiqueta, su ayuda, la carga y el guardado.

Accesibilidad (por qué está hecho así):

- Una página de ``wx.Notebook`` por categoría: el lector anuncia el nombre de
  la pestaña y Ctrl+Tab / Ctrl+Mayús+Tab cambian de página.
- Cada control va PRECEDIDO por su ``wx.StaticText``, creado justo antes: en
  Windows el lector toma de ahí el nombre del control (``name=`` o ``SetName``
  no nombran los controles nativos). Las casillas llevan su propio texto.
- La etiqueta puede llevar ``&`` para saltar al control con Alt+letra.
- Ctrl+H lee la ayuda del control con el foco (``SetHelpText``) en un cuadro de
  texto de solo lectura que se puede recorrer línea a línea.
- Botones en español con acelerador: &Aceptar, &Cancelar, A&plicar y
  &Restablecer valores de fábrica. Escape cancela. El foco empieza en la
  primera opción.
- Cancelar no cambia nada: los valores solo se escriben al Aplicar o Aceptar.
  Lo ya aplicado no se deshace al cancelar después (comportamiento estándar).
- Aplicar/Aceptar validan TODO antes de guardar; si algo no vale, se avisa, se
  lleva el foco a ese control y no se guarda nada.
- Al guardar, la configuración avisa a sus suscriptores: las ventanas abiertas
  aplican la preferencia al momento.

PLANTILLA del agente «configuracion».
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import wx
import wx.lib.scrolledpanel as scrolled

from app.core.configuracion import Configuracion, ErrorValidacion

log = logging.getLogger(__name__)


def _(texto: str) -> str:
	"""Traduce con gettext si está instalado en builtins; si no, devuelve el texto."""
	traducir = getattr(builtins, "_", None)
	return traducir(texto) if callable(traducir) else texto


def N_(texto: str) -> str:  # noqa: N802 - convención de gettext
	"""Marca un texto para traducir SIN traducirlo aún.

	Los descriptores se crean al importar el módulo, antes de que gettext esté
	listo; se traducen al construir el diálogo con ``_()``.
	"""
	return texto


# Tipos de opción admitidos.
BOOL, ENTERO, DECIMAL, TEXTO, OPCION, CARPETA, SECRETO, COLUMNAS = (
	"bool", "entero", "decimal", "texto", "opcion", "carpeta", "secreto", "columnas",
)


@dataclass(frozen=True)
class Opcion:
	"""Descriptor de una preferencia.

	:param clave: «seccion.clave» en la configuración (o nombre del secreto).
	:param etiqueta: texto visible y nombre para el lector (con ``&`` opcional).
	:param tipo: ``bool``, ``entero``, ``decimal``, ``texto``, ``opcion``,
		``carpeta`` o ``secreto``.
	:param ayuda: explicación que se lee con Ctrl+H.
	:param minimo, maximo: límites numéricos (si faltan, se toman de la regla
		de la configuración).
	:param incremento: paso de los decimales.
	:param opciones: pares ``(valor, etiqueta)`` para el tipo ``opcion``; para
		``columnas``, pares ``(id, nombre)`` de todas las columnas posibles.
	:param ejemplos: para ``columnas``, pares ``(id, texto de ejemplo)`` con los
		que se enseña cómo sonará una fila.
	"""

	clave: str
	etiqueta: str
	tipo: str
	ayuda: str = ""
	minimo: float | None = None
	maximo: float | None = None
	incremento: float = 0.1
	opciones: tuple[tuple[Any, str], ...] = ()
	ejemplos: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Pagina:
	"""Una pestaña del diálogo."""

	titulo: str
	opciones: tuple[Opcion, ...]


# ── Páginas de Preferencias de Ctrl+IA ────────────────────────────────────────
# Los aceleradores no repiten los de la botonera: A (Aceptar), C (Cancelar),
# P (Aplicar) y R (Restablecer).

PAGINAS: tuple[Pagina, ...] = (
	Pagina(N_("General"), (
		Opcion("general.idioma", N_("&Idioma de la interfaz:"), OPCION,
			N_("Idioma de menús y mensajes. «Automático» usa el de Windows. Se aplica al reiniciar Ctrl+IA."),
			opciones=(("auto", N_("Automático")), ("es", N_("Español")), ("en", N_("Inglés")))),
		Opcion("general.confirmar_salida", N_("Con&firmar antes de salir"), BOOL,
			N_("Pregunta antes de cerrar Ctrl+IA. Si hay agentes trabajando, siempre se pregunta.")),
		Opcion("general.minimizar_a_bandeja", N_("&Minimizar a la bandeja del sistema"), BOOL,
			N_("Al minimizar, la ventana se oculta y queda un icono junto al reloj. Los agentes siguen trabajando.")),
		Opcion("general.anunciar_arranque", N_("Anunciar el arran&que"), BOOL,
			N_("Dice «Iniciando Ctrl+IA» mientras el programa se abre.")),
		Opcion("general.abrir_ultima_sesion", N_("Abrir la ú&ltima sesión al iniciar"), BOOL,
			N_("Al abrir Ctrl+IA se carga la sesión que tenías abierta al salir.")),
		Opcion("general.comprobar_actualizaciones", N_("&Buscar actualizaciones automáticamente"), BOOL,
			N_("Una vez al día, Ctrl+IA comprueba en segundo plano si hay una versión nueva y te avisa. Siempre puedes "
				"comprobarlo a mano en Ayuda, «Buscar actualizaciones». Se aplica al reiniciar Ctrl+IA.")),
	)),
	Pagina(N_("Agentes"), (
		Opcion("agentes.predeterminado", N_("A&gente predeterminado:"), OPCION,
			N_("El agente que aparece elegido al crear una sesión nueva."),
			opciones=(("claude", N_("Claude Code")), ("codex", N_("Codex")), ("antigravity", N_("Antigravity")),
				("gemini", N_("Gemini CLI")), ("ollama", N_("Ollama (solo chat)")))),
		Opcion("agentes.carpeta_predeterminada", N_("Carpeta de &trabajo predeterminada:"), CARPETA,
			N_("Carpeta que se propone al crear una sesión si no hay ninguna reciente.")),
		Opcion("agentes.editor_externo", N_("&Editor de código (ruta del programa):"), TEXTO,
			N_("Programa con el que se abre la carpeta de una sesión. Vacío: Visual Studio Code si está instalado.")),
		Opcion("agentes.adaptadores_acp", N_("Mostrar los a&daptadores ACP (avanzado)"), BOOL,
			N_("Añade a la lista de agentes las versiones por el protocolo ACP de Claude, Codex y Antigravity. "
				"Necesitan Node.js. Se aplica al reiniciar Ctrl+IA.")),
		Opcion("agentes.otros_acp", N_("Mostrar otros agentes ACP co&nocidos (OpenCode, Goose, Copilot...)"), BOOL,
			N_("Añade a la lista de agentes OpenCode, Goose, GitHub Copilot CLI, Qwen Code, Kilo Code y Cline, que hablan "
				"el protocolo ACP. Cada uno se instala aparte y usa tu propia cuenta o modelos gratuitos o locales. "
				"Se aplica al reiniciar Ctrl+IA.")),
		Opcion("agentes.puntos_restauracion", N_("Guardar puntos de re&stauración antes de cada mensaje"), BOOL,
			N_("Antes de enviar cada mensaje, Ctrl+IA guarda cómo están los archivos de la carpeta de la sesión, en un "
				"repositorio aparte que nunca toca el tuyo. Así puedes deshacer lo que cambió el agente desde uno de tus "
				"mensajes (Control+Alt+Z) y rehacerlo (Control+Alt+Mayúsculas+Z). Vale para Claude Code, Codex, "
				"Antigravity, Gemini y los agentes ACP de este equipo; necesita Git para Windows. No se guardan en la "
				"carpeta personal, el Escritorio, Documentos, Descargas, la raíz de un disco ni en carpetas de red o con "
				"más de 50.000 archivos, ni los archivos de más de 5 MB. Los puntos se borran a los 30 días.")),
		Opcion("agentes.ollama", N_("Mostrar &Ollama (modelos locales, solo chat)"), BOOL,
			N_("Añade Ollama a la lista de agentes para conversar con los modelos que tienes descargados en tu equipo. "
				"Es solo chat: el modelo no puede leer ni cambiar archivos. Se aplica al reiniciar Ctrl+IA.")),
		Opcion("agentes.servidor_ollama", N_("Dirección del ser&vidor de Ollama:"), TEXTO,
			N_("Dónde escucha Ollama. Vacío: la variable OLLAMA_HOST o, si no existe, http://127.0.0.1:11434 (este equipo). "
				"Si escribes la dirección de otro equipo, tus conversaciones viajarán hasta él.")),
		Opcion("agentes.lista_tareas_claude", N_("Dejar que Claude lleve una &lista de tareas"), BOOL,
			N_("Claude apunta los pasos de su trabajo y los marca al hacerlos: los ves en Ver, «Tareas del "
				"agente» (Ctrl+Mayús+J), en el estado del turno y en Propiedades, y se anuncian al acabar. Sin esta "
				"opción, Claude Code solo la usa con algunos modelos. Vale para las sesiones de Ctrl+IA desde el "
				"siguiente mensaje; necesita Claude Code 2.1.233 o posterior.")),
		Opcion("agentes.esperar_mcp_claude", N_("Esperar a que se conecten las &herramientas MCP de Claude antes de cada mensaje"), BOOL,
			N_("Claude Code arranca de nuevo en cada mensaje y, sin esta opción, empieza a responder mientras sus "
				"servidores MCP y conectores todavía se están conectando: entonces dice que se han desconectado o que "
				"no los tiene. Con ella, espera a que estén listos (un poco más al empezar cada respuesta; un servidor "
				"que no responde la retrasa hasta que Claude Code se rinde con él). Vale desde el siguiente mensaje.")),
		Opcion("agentes.chrome_claude", N_("Dejar que Claude &use Chrome (Claude en Chrome)"), BOOL,
			N_("Arranca Claude Code con su integración «Claude en Chrome»: si tienes Chrome abierto con la extensión "
				"Claude instalada y con tu sesión iniciada, Claude puede ver tus pestañas, leer páginas y rellenar "
				"formularios. Te pide permiso antes de cada acción en el navegador, como con las demás herramientas. "
				"Viene activada. Vale desde el siguiente mensaje.")),
		Opcion("agentes.claude_minutos_abierto", N_("&Mantener Claude Code abierto entre mensajes:"), OPCION,
			N_("Claude Code sigue en marcha tras responder, esperando tu siguiente mensaje: así no vuelve a arrancar ni a "
				"conectar sus herramientas MCP cada vez y responde antes. Se cierra solo tras ese tiempo sin mensajes, y "
				"se vuelve a abrir si cambias el modelo, el esfuerzo o quitas permisos. Cada sesión abierta ocupa algo de "
				"memoria. «No» arranca Claude Code de nuevo en cada mensaje. Vale desde el siguiente mensaje."),
			opciones=((0, N_("No: arrancarlo en cada mensaje")), (5, N_("5 minutos")), (15, N_("15 minutos")),
				(30, N_("30 minutos")), (60, N_("1 hora")))),
		Opcion("agentes.permitir_clave_api", N_("Permitir que Claude use una clave de API (puede costar dinero)"), BOOL,
			N_("Normalmente Ctrl+IA solo usa tu suscripción de Claude y se niega a usar claves de API, que se cobran "
				"por uso. Actívalo solo si sabes que quieres pagar por uso. Se aplica al reiniciar Ctrl+IA.")),
		Opcion("agentes.herramientas_ctrlia",
			N_("Dar a los agentes las herramientas de Ctrl+IA (av&isarte, dar la sesión por terminada, renombrarla)"), BOOL,
			N_("Los agentes de tus sesiones pueden avisarte de algo importante (Ctrl+IA lo dice con voz y braille y, si no "
				"estás en su ventana, suena y sale una notificación de Windows; como mucho un aviso cada 10 segundos), dar la "
				"sesión por terminada como con Control+D y cambiarle el título. Solo afectan a su propia sesión y cada uso "
				"queda en la conversación. Valen para Claude Code, Codex, Antigravity, Gemini y los agentes ACP de este "
				"equipo, y para Claude Code por SSH. Viene activada. Vale desde la próxima vez que arranque el agente de "
				"cada sesión.")),
	)),
	Pagina(N_("Historial"), (
		Opcion("historial.claude", N_("Mostrar las sesiones de C&laude Code de otros programas"), BOOL,
			N_("Incluye en la lista las sesiones de la terminal, de la app de escritorio de Claude y de Cowork.")),
		Opcion("historial.codex", N_("Mostrar las conversaciones de C&odex"), BOOL,
			N_("Incluye en la lista las conversaciones de Codex hechas desde otros programas.")),
		Opcion("historial.antigravity", N_("Mostrar las conversaciones de A&ntigravity (app y CLI)"), BOOL,
			N_("Incluye las conversaciones de la app de escritorio de Antigravity y de Antigravity CLI en la terminal, "
				"con su título, su carpeta y sus pasos. Sus mensajes no se pueden leer desde otros programas.")),
		Opcion("historial.ollama", N_("Mostrar los chats de la app de Olla&ma"), BOOL,
			N_("Incluye los chats de la aplicación de escritorio de Ollama, para leerlos, buscar en ellos y "
				"continuarlos en Ctrl+IA.")),
		Opcion("historial.seguir_en_vivo", N_("Seguir en &vivo las sesiones de otros programas"), BOOL,
			N_("Si abres una sesión que está usando otro programa, sus mensajes nuevos aparecen solos.")),
		Opcion("historial.continuar_original", N_("Al continuar una sesión de otro programa, &seguir la original sin copiarla"), BOOL,
			N_("Con Claude Code y Codex, aquí o en un equipo por SSH: «Continuar aquí» y escribir en una sesión de la "
				"terminal, de la app o del Mac siguen esa misma sesión en lugar de crear una copia, así que lo que hables "
				"aquí aparece después allí. Solo se hace si la sesión no está abierta en el otro programa ni se ha usado "
				"en los dos últimos minutos; si lo está, Ctrl+IA te ofrece hacer una copia. Desactivada, siempre se copia.")),
		Opcion("lista.mostrar_ocultas", N_("Mostrar también las sesiones oc&ultas"), BOOL,
			N_("Las sesiones ocultas aparecen en todas las vistas, no solo en Ver, Mostrar, Ocultas.")),
		Opcion("lista.mostrar_archivadas", N_("Mostrar también las sesiones arc&hivadas"), BOOL,
			N_("Incluye las sesiones que archivaste en la app de Claude.")),
	)),
	Pagina(N_("Mensajes"), (
		Opcion("mensajes.herramientas", N_("Mostrar las acciones del agente (&herramientas)"), BOOL,
			N_("Muestra en la lista de mensajes cada archivo que lee o edita el agente y cada orden que ejecuta.")),
		Opcion("mensajes.razonamiento", N_("Guardar y mostrar el ra&zonamiento"), BOOL,
			N_("Algunos agentes publican lo que piensan antes de responder. Puede ser muy largo.")),
		Opcion("mensajes.vista_formateada", N_("Abrir los mensajes con &formato"), BOOL,
			N_("Intro abre el mensaje como página con encabezados y listas. Si lo desactivas, se abre como texto plano. "
				"Mayúsculas+Intro hace lo contrario.")),
		Opcion("mensajes.recientes", N_("&Mensajes que se cargan al abrir una sesión:"), OPCION,
			N_("En las sesiones largas, al abrirlas solo se ponen en la lista los últimos mensajes; la primera fila "
				"carga los anteriores (también Alt+Inicio). Buscar, las listas de la sesión y exportar usan siempre "
				"la conversación entera."),
			opciones=((50, N_("Los últimos 50")), (100, N_("Los últimos 100")), (250, N_("Los últimos 250")),
				(500, N_("Los últimos 500")), (0, N_("Todos")))),
		Opcion("mensajes.estilo_cambios", N_("Cómo se &leen las líneas cambiadas de un archivo:"), OPCION,
			N_("Al ver los cambios de un archivo (en las listas de la sesión o al abrir un mensaje de cambio), cada "
				"línea dice si se añadió, se quitó o sigue igual. «Con palabras»: «Línea 12, añadida: …». «Con palabras "
				"cortas»: «Más 12: …», «Menos 12: …», «Igual 13: …». «Con los signos + y −»: el cambio tal cual, como en "
				"Git; los signos solo se oyen si el nivel de puntuación de tu lector de pantalla los lee. Guardar los "
				"cambios como archivo siempre guarda el original con los signos."),
			opciones=(("palabras", N_("Con palabras")), ("cortas", N_("Con palabras cortas")),
				("simbolos", N_("Con los signos + y −")))),
	)),
	Pagina(N_("Lectura de listas"), (
		Opcion("lectura.numero_fila", N_("Al ir a izquierda y derecha, decir el &número de fila"), BOOL,
			N_("Con las flechas izquierda y derecha (y Control más un número) se recorren las columnas de la fila. "
				"Si está marcada, se dice primero «Fila 3». Se aplica al momento.")),
		Opcion("lectura.cabecera", N_("Al ir a izquierda y derecha, decir el nombre de la co&lumna"), BOOL,
			N_("Dice el nombre de la columna («Estado», «Agente»...) antes de su contenido. Se aplica al momento.")),
		Opcion("lectura.contenido", N_("Al ir a izquierda y derecha, decir el con&tenido de la celda"), BOOL,
			N_("Dice lo que hay en la columna. Si desmarcas las tres opciones, se dice igualmente el contenido.")),
		Opcion("lista.columnas", N_("Lo que se lee al subir y bajar por la lista de &sesiones:"), COLUMNAS,
			N_("Al moverte arriba y abajo, el lector lee las columnas de la fila en este orden. Pulsa el botón para "
				"marcar las que quieres oír y ordenarlas con Alt+flecha arriba y Alt+flecha abajo."),
			opciones=(("titulo", N_("Título")), ("estado", N_("Estado")), ("agente", N_("Agente")),
				("carpeta", N_("Carpeta")), ("actividad", N_("Última actividad")), ("origen", N_("Origen")),
				("sin_leer", N_("Sin leer")), ("grupos", N_("Grupos")), ("ultimo", N_("Último mensaje")),
				("creada", N_("Creada")), ("modelo", N_("Modelo"))),
			ejemplos=(("titulo", N_("Informe de ventas")), ("estado", N_("Te necesita, respuesta nueva")),
				("agente", "Claude Code"), ("carpeta", "C:\\Proyectos\\ventas"), ("actividad", N_("hace 5 minutos")),
				("origen", "Ctrl+IA"), ("sin_leer", "1"), ("grupos", N_("Investigación")),
				("ultimo", N_("He terminado el resumen.")), ("creada", N_("ayer a las 18:30")), ("modelo", "opus"))),
		Opcion("mensajes.columnas", N_("Lo que se lee al subir y bajar por la lista de &mensajes:"), COLUMNAS,
			N_("Columnas de la lista de mensajes y su orden. «Indicadores» resume el mensaje: si el agente te hizo "
				"una pregunta o te pidió permiso, si trae código, enlaces o archivos cambiados."),
			opciones=(("numero", N_("Número")), ("quien", N_("Quién")), ("mensaje", N_("Mensaje")),
				("indicadores", N_("Indicadores")), ("hora", N_("Hora"))),
			ejemplos=(("numero", N_("3 de 12")), ("quien", "Claude Code"), ("mensaje", N_("¿Quieres que lo publique?")),
				("indicadores", N_("Pregunta por responder, 2 enlaces")), ("hora", "10:15"))),
		Opcion("grupos.columnas", N_("Lo que se lee en la lista de &grupos:"), COLUMNAS,
			N_("Columnas de la lista de grupos (Organizar, Abrir un grupo) y su orden."),
			opciones=(("nombre", N_("Nombre")), ("sesiones", N_("Sesiones")), ("descripcion", N_("Descripción")),
				("agente", N_("Agente predeterminado")), ("carpeta", N_("Carpeta predeterminada"))),
			ejemplos=(("nombre", N_("Investigación")), ("sesiones", N_("4 sesiones")),
				("descripcion", N_("Consultas sobre el mercado")), ("agente", "Claude Code"), ("carpeta", N_("Ninguna")))),
		Opcion("grupos.columnas_sesiones", N_("Lo que se lee de las sesiones dentro de un gr&upo:"), COLUMNAS,
			N_("Columnas de la lista de sesiones de un grupo abierto y su orden."),
			opciones=(("titulo", N_("Título")), ("estado", N_("Estado")), ("agente", N_("Agente")),
				("carpeta", N_("Carpeta")), ("actividad", N_("Última actividad")), ("origen", N_("Origen")),
				("sin_leer", N_("Sin leer")), ("grupos", N_("Grupos")), ("ultimo", N_("Último mensaje")),
				("creada", N_("Creada")), ("modelo", N_("Modelo"))),
			ejemplos=(("titulo", N_("Informe de ventas")), ("estado", N_("Inactiva")), ("agente", "Codex"),
				("carpeta", "C:\\Proyectos\\ventas"), ("actividad", N_("ayer a las 18:30")), ("origen", "Ctrl+IA"),
				("sin_leer", N_("No")), ("grupos", N_("Investigación")), ("ultimo", N_("Listo.")),
				("creada", N_("3 de marzo")), ("modelo", N_("Predeterminado")))),
		Opcion("lectura.favorita_en_titulo", N_("Decir «&Favorita» delante del título"), BOOL,
			N_("Las sesiones favoritas se leen como «Favorita: título».")),
		Opcion("lectura.sin_leer_en_estado", N_("Añadir «respuesta nueva» al &estado"), BOOL,
			N_("Si una sesión tiene respuestas que no has leído, su estado lo dice.")),
		Opcion("lectura.fechas", N_("Formato de las fec&has:"), OPCION,
			N_("Relativas: «hace 5 minutos», «ayer a las 18:30». Completas: «17/10/2026 21:39»."),
			opciones=(("relativas", N_("Relativas")), ("absolutas", N_("Completas, con día y hora")))),
		Opcion("lectura.indicadores", N_("Mostrar los &indicadores de cada mensaje"), BOOL,
			N_("La columna «Indicadores» de los mensajes resume lo que trae cada uno. Va en el texto de la fila, "
				"así que también sale en braille. Debajo eliges qué indicadores quieres.")),
		Opcion("lectura.indicador_favorito", N_("Indicador «Favorito» en los mensajes que has marcado"), BOOL,
			N_("Dice «Favorito» en los mensajes que marcaste con Control+Alt+F, de cualquier sesión y de cualquier "
				"agente. Alt+Flecha arriba y Alt+Flecha abajo llevan al favorito anterior y al siguiente.")),
		Opcion("lectura.indicador_pregunta", N_("Indicador de las preguntas que te hace el agente"), BOOL,
			N_("Dice «Pregunta por responder» en el mensaje en el que el agente te hizo una pregunta de verdad, de las "
				"que se contestan con Control+Mayúsculas+A, y después «Pregunta respondida». No se fija en los signos "
				"de interrogación del texto. Solo en las sesiones de Ctrl+IA.")),
		Opcion("lectura.indicador_permiso", N_("Indicador de los permisos y planes que pide el agente"), BOOL,
			N_("Dice «Permiso por responder», «Permiso concedido» o «Permiso denegado», y «Plan por aprobar», «Plan "
				"aprobado» o «Plan rechazado», según lo que respondiste. Solo en las sesiones de Ctrl+IA.")),
		Opcion("lectura.indicador_error", N_("Indicador «Con error» en las acciones que fallaron"), BOOL,
			N_("Avisa de que una acción del agente (una orden, una herramienta) terminó con error.")),
		Opcion("lectura.indicador_detenido", N_("Indicador «Detenido» en los turnos que no terminaron"), BOOL,
			N_("Avisa en el último mensaje de un turno que se detuvo antes de acabar, por ejemplo porque lo detuviste tú.")),
		Opcion("lectura.indicador_adjuntos", N_("Indicador de archivos adjuntos"), BOOL,
			N_("Dice cuántos archivos adjuntaste a tu mensaje.")),
		Opcion("lectura.indicador_codigo", N_("Indicador de bloques de có&digo"), BOOL,
			N_("Dice cuántos bloques de código trae el mensaje.")),
		Opcion("lectura.indicador_enlaces", N_("Indicador de enlaces"), BOOL,
			N_("Dice cuántos enlaces distintos trae el mensaje.")),
		Opcion("lectura.indicador_archivos", N_("Indicador de archivos cambiados"), BOOL,
			N_("Dice cuántos archivos cambió el agente en ese paso.")),
		Opcion("lectura.indicador_largo", N_("Indicador «Largo» en los mensajes muy largos"), BOOL,
			N_("Avisa de que el mensaje es largo (más de 1500 caracteres): mejor abrirlo con Intro.")),
		Opcion("lectura.anunciar_vista_vacia", N_("A&visar cuando una vista o un grupo está vacío"), BOOL,
			N_("Al cambiar a una vista o abrir un grupo sin sesiones, se dice «No hay sesiones en esta vista».")),
	)),
	Pagina(N_("Anuncios"), (
		Opcion("anuncios.nivel", N_("¿Qué se lee &de las respuestas?:"), OPCION,
			N_("Completo: se lee la respuesta del agente al llegar. Resumen: solo «ha respondido». Silencio: nada."),
			opciones=(("completo", N_("Completo")), ("resumen", N_("Resumen")), ("silencio", N_("Silencio")))),
		Opcion("anuncios.todas_las_sesiones", N_("Leer las respuestas de &todas las sesiones"), BOOL,
			N_("Si está desactivado, solo se lee la sesión abierta; de las demás se dice que han respondido.")),
		Opcion("anuncios.herramientas", N_("Anunciar cada acción del agente (&herramientas)"), BOOL,
			N_("Dice cada archivo que lee o edita y cada orden que ejecuta. Puede ser mucha información.")),
		Opcion("anuncios.tareas", N_("Anunciar el progreso de la lista de tareas del a&gente"), BOOL,
			N_("Cuando el agente da por hecha una tarea de su lista, dice cuál y cuántas lleva, por ejemplo «Hecha: "
				"ejecutar las pruebas. 4 de 7». Solo si se leen las respuestas completas, y sin cortar lo que se esté leyendo.")),
		Opcion("anuncios.mis_mensajes", N_("Leer mis propios mensajes al en&viarlos"), BOOL,
			N_("Repite lo que acabas de enviar (hasta 300 caracteres).")),
		Opcion("anuncios.avisos_importantes", N_("Avisos i&mportantes:"), OPCION,
			N_("Errores y peticiones de permiso: por voz, en un cuadro de mensaje o de las dos formas."),
			opciones=(("voz", N_("Por voz")), ("mensaje", N_("En un cuadro de mensaje")), ("ambos", N_("Las dos cosas")))),
		Opcion("anuncios.braille", N_("Enviar los avisos también a la línea &braille"), BOOL,
			N_("Cada aviso de Ctrl+IA aparece también en tu línea braille, si tu lector de pantalla lo permite.")),
		Opcion("anuncios.braille_sin_voz", N_("Mostrar en braille los avisos aunque la voz esté &silenciada"), BOOL,
			N_("Con la voz de Ctrl+IA silenciada (Control+Mayúsculas+V) o con «Silencio» en «¿Qué se lee de las respuestas?», "
				"los avisos no se dicen pero siguen apareciendo en la línea braille.")),
		Opcion("anuncios.barra_estado", N_("Escribir el último aviso en la barra de &estado"), BOOL,
			N_("La última parte de la barra de estado muestra siempre el último aviso, para leerlo o verlo cuando quieras. "
				"Todos quedan además en el historial de avisos (Control+Mayúsculas+H).")),
		Opcion("anuncios.palabras_curiosas", N_("Palabras curiosas en la barra de estado mientras el agente escribe («&Zascandileando…»)"), BOOL,
			N_("Solo por gusto: en lugar de «Claude está escribiendo...», la barra de estado dice una palabra curiosa "
				"(«Cavilando…», «Tramando…», «Hilvanando…») que cambia cada pocos segundos. Nunca se dice por voz ni "
				"interrumpe nada; Alt+4 sigue diciendo «está escribiendo» y lo que hace de verdad el agente está en "
				"Estado del turno (Control+Mayúsculas+T).")),
		Opcion("anuncios.retardo_voz_ms", N_("Retardo de &los anuncios (milisegundos):"), ENTERO,
			N_("Espera antes de hablar para no pisar lo que el lector dice al moverte.")),
		Opcion("anuncios.aviso_contexto", N_("Avisar cuando el c&ontexto pase del (por ciento):"), ENTERO,
			N_("Aviso cuando la conversación ocupa ese porcentaje de la memoria del agente. 0 desactiva el aviso.")),
		Opcion("anuncios.aviso_limite", N_("Avisar cuando el límite de &uso pase del (por ciento):"), ENTERO,
			N_("Aviso cuando hayas gastado ese porcentaje del límite de tu cuenta. 0 desactiva el aviso.")),
		Opcion("anuncios.comprobaciones_pr", N_("Av&isar cuando las comprobaciones de la PR de una sesión fallen o pasen"), BOOL,
			N_("Si la carpeta de una sesión es un repositorio de GitHub y su rama tiene una PR, Ctrl+IA la consulta "
				"despacio en segundo plano con la herramienta gh (cada minuto mientras hay comprobaciones en curso y "
				"cada 5 minutos si no; solo en las sesiones abiertas o usadas en las últimas 3 horas) y avisa una vez "
				"cuando las comprobaciones automáticas fallan o pasan, cuando la PR se fusiona y cuando en la revisión "
				"piden cambios. Necesita gh instalado y con tu sesión de GitHub iniciada; si no, no dice nada. Viene activada.")),
		Opcion("notificaciones.modo", N_("&Notificaciones de Windows:"), OPCION,
			N_("Cuándo aparece una notificación si Ctrl+IA no está en primer plano."),
			opciones=(("todas", N_("Al terminar y cuando un agente te necesita")), ("te_necesita", N_("Solo cuando un agente te necesita")),
				("nunca", N_("Nunca")))),
	)),
	Pagina(N_("Sonidos"), (
		Opcion("anuncios.sonidos", N_("&Sonidos activados"), BOOL,
			N_("Interruptor general de los sonidos. También se cambia con F9 desde la ventana principal.")),
		Opcion("sonidos.esquema", N_("&Esquema de sonidos:"), OPCION,
			N_("«Ctrl+IA» usa los sonidos que trae el programa. «Propio» usa los archivos WAV de la carpeta "
				"data\\sounds\\propios, con el nombre de cada evento (por ejemplo respuesta.wav); los que falten "
				"suenan como en el esquema de Ctrl+IA. Herramientas, «Sonidos...» abre esa carpeta y permite probarlos."),
			opciones=(("predeterminado", N_("Ctrl+IA")), ("propio", N_("Propio (carpeta de sonidos propios)")))),
		Opcion("sonidos.volumen", N_("&Volumen de los sonidos (por ciento):"), ENTERO,
			N_("Volumen de los sonidos de Ctrl+IA, aparte del volumen de Windows y del de tu lector. 0 los calla.")),
		Opcion("sonidos.nueva", N_("Al crear una sesió&n"), BOOL,
			N_("Suena al crear una sesión nueva.")),
		Opcion("sonidos.enviado", N_("Al enviar un mensa&je"), BOOL,
			N_("Suena cuando tu mensaje sale hacia el agente.")),
		Opcion("sonidos.herramienta", N_("Cuando el agente usa una &herramienta"), BOOL,
			N_("Un toque suave cada vez que el agente lee, busca o ejecuta algo, para saber que sigue trabajando sin "
				"oír cada acción. Como mucho suena un par de veces por segundo.")),
		Opcion("sonidos.archivo", N_("Cuando el agente cambia un arch&ivo"), BOOL,
			N_("Un toque distinto cuando el agente crea o modifica un archivo.")),
		Opcion("sonidos.respuesta", N_("Cuando el agente termina de respon&der"), BOOL,
			N_("Suena cuando termina la respuesta en la sesión que tienes abierta.")),
		Opcion("sonidos.segundo_plano", N_("Cuando termina otra sesión (se&gundo plano)"), BOOL,
			N_("Un sonido más suave cuando termina de responder una sesión que no tienes abierta.")),
		Opcion("sonidos.permiso", N_("Cuando el agente te necesita (per&miso o pregunta)"), BOOL,
			N_("Suena cuando el agente te pide permiso, te pregunta algo o te propone un plan.")),
		Opcion("sonidos.error", N_("En los err&ores"), BOOL,
			N_("Suena cuando el agente o Ctrl+IA informan de un error.")),
		Opcion("sonidos.detenido", N_("Cuando el agente se de&tiene"), BOOL,
			N_("Suena cuando el turno se interrumpe, por ejemplo al pulsar Control+punto.")),
		Opcion("sonidos.compactado", N_("Cuando el agente res&ume la conversación (compactación)"), BOOL,
			N_("Tres notas suaves que bajan cuando el agente resume lo anterior de la conversación para liberar memoria.")),
		Opcion("sonidos.limite", N_("Al acercarte al &límite de uso de tu cuenta"), BOOL,
			N_("Dos pulsos graves cuando llegas al porcentaje de tu límite de uso que eliges en Anuncios.")),
	)),
	Pagina(N_("Ventana"), (
		Opcion("ventana.mostrar_sesiones", N_("Mostrar la lista de &sesiones"), BOOL,
			N_("Si la ocultas, la conversación ocupa toda la ventana. Alt+1 la vuelve a mostrar. También se cambia con F7.")),
		Opcion("ventana.panel_lectura", N_("Panel de &lectura del mensaje seleccionado"), BOOL,
			N_("Debajo de la lista de mensajes aparece el mensaje elegido completo, sin abrirlo. Con el lector de pantalla "
				"no se dice nada al moverte; el panel es una parada más de F6 y Alt+5 lleva a él. También se cambia con F8.")),
		Opcion("ventana.reparto", N_("&Distribución del espacio:"), OPCION,
			N_("Cómo se reparte la altura de la ventana entre las sesiones, los mensajes y el cuadro de respuesta. "
				"En ventanas pequeñas, «Más espacio para los mensajes» deja ver más conversación."),
			opciones=(("equilibrado", N_("Equilibrada")), ("mensajes", N_("Más espacio para los mensajes")),
				("respuesta", N_("Más espacio para el cuadro de respuesta")))),
		Opcion("ventana.resaltar_foco", N_("Resaltar la &zona que tiene el foco"), BOOL,
			N_("El título de la zona donde está el foco (Sesiones, Mensajes, Lectura o Respuesta) se ve en negrita y "
				"con los colores de selección de Windows, para ver de un vistazo dónde estás.")),
		Opcion("ventana.tema", N_("&Tema de colores:"), OPCION,
			N_("Claro u oscuro en toda la aplicación, incluidos el manual y los mensajes con formato. «Como Windows» "
				"sigue el modo de las aplicaciones de Windows. Con un tema de alto contraste de Windows se usan siempre "
				"sus colores. Se aplica al reiniciar Ctrl+IA."),
			opciones=(("claro", N_("Claro")), ("oscuro", N_("Oscuro")), ("sistema", N_("Como Windows")))),
	)),
	Pagina(N_("Avanzado"), (
		Opcion("registro.nivel", N_("&Nivel de detalle del registro:"), OPCION,
			N_("Cuánto se anota en los registros de funcionamiento. «Depuración» solo si te lo piden para investigar un fallo."),
			opciones=(("INFO", N_("Normal")), ("DEBUG", N_("Depuración")), ("WARNING", N_("Solo avisos")), ("ERROR", N_("Solo errores")))),
	)),
)


# ── Avisos modales (funciones de módulo para poder sustituirlas en pruebas) ──

def mostrar_aviso(padre: wx.Window | None, texto: str, titulo: str) -> None:
	"""Mensaje de error o aviso con botón en español."""
	dialogo = wx.MessageDialog(padre, texto, titulo, wx.OK | wx.ICON_WARNING)
	dialogo.SetOKLabel(_("&Aceptar"))
	try:
		dialogo.ShowModal()
	finally:
		dialogo.Destroy()


def confirmar(padre: wx.Window | None, texto: str, titulo: str) -> bool:
	"""Pregunta Sí/No (con «No» por defecto para las acciones que borran)."""
	dialogo = wx.MessageDialog(padre, texto, titulo, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
	dialogo.SetYesNoLabels(_("&Sí"), _("&No"))
	try:
		return dialogo.ShowModal() == wx.ID_YES
	finally:
		dialogo.Destroy()


class DialogoAyuda(wx.Dialog):
	"""Muestra un texto de ayuda en un campo de solo lectura (navegable con flechas)."""

	def __init__(self, padre: wx.Window | None, titulo: str, texto: str) -> None:
		super().__init__(padre, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		sizer = wx.BoxSizer(wx.VERTICAL)
		etiqueta = wx.StaticText(self, label=_("&Ayuda:"))
		self.texto = wx.TextCtrl(self, value=texto, size=(420, 140),
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
		# Escape y el botón comparten ID_CANCEL: Escape cierra el cuadro.
		cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"))
		sizer.Add(etiqueta, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		sizer.Add(self.texto, 1, wx.EXPAND | wx.ALL, 10)
		sizer.Add(cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
		self.SetSizerAndFit(sizer)
		self.SetEscapeId(wx.ID_CANCEL)
		self.Bind(wx.EVT_INIT_DIALOG, self._al_iniciar)

	def _al_iniciar(self, evento: wx.InitDialogEvent) -> None:
		self.texto.SetFocus()
		self.texto.SetInsertionPoint(0)
		evento.Skip()


def mostrar_ayuda(padre: wx.Window | None, titulo: str, texto: str) -> None:
	"""Abre ``DialogoAyuda`` de forma modal."""
	dialogo = DialogoAyuda(padre, titulo, texto)
	try:
		dialogo.ShowModal()
	finally:
		dialogo.Destroy()


class DialogoPreferencias(wx.Dialog):
	"""Diálogo de preferencias generado a partir de descriptores.

	:param padre: ventana padre.
	:param config: configuración de la aplicación.
	:param paginas: descriptores (por defecto ``PAGINAS``).
	:param secretos: almacén de secretos (``obtener``/``guardar_varios``) para
		las opciones de tipo ``secreto``; sin él, esas opciones no se muestran.
	:param anunciar: función opcional que dice un texto por el lector.
	"""

	def __init__(
		self,
		padre: wx.Window | None,
		config: Configuracion,
		paginas: tuple[Pagina, ...] = PAGINAS,
		secretos: Any | None = None,
		anunciar: Callable[[str], None] | None = None,
		titulo: str | None = None,
	) -> None:
		# Sin proveedor de ayuda, SetHelpText no guarda nada. Se instala uno si falta.
		if wx.HelpProvider.Get() is None:
			wx.HelpProvider.Set(wx.SimpleHelpProvider())
		super().__init__(padre, title=titulo or _("Preferencias"),
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self._config = config
		self._secretos = secretos
		self._anunciar = anunciar
		# clave → (opción, control, índice de página)
		self._controles: dict[str, tuple[Opcion, wx.Window, int]] = {}
		# id de control (y de sus hijos, p. ej. el texto de un SpinCtrl) → clave
		self._clave_por_id: dict[int, str] = {}
		self._valores_secretos: dict[str, str] = {}
		# clave de una opción de columnas → ids elegidos, en su orden
		self._valores_columnas: dict[str, list[str]] = {}

		sizer = wx.BoxSizer(wx.VERTICAL)
		self.libro = wx.Notebook(self)
		for indice, pagina in enumerate(paginas):
			panel = self._crear_pagina(pagina, indice)
			self.libro.AddPage(panel, _(pagina.titulo))
		sizer.Add(self.libro, 1, wx.EXPAND | wx.ALL, 8)

		botones = wx.BoxSizer(wx.HORIZONTAL)
		self.boton_restablecer = wx.Button(self, label=_("&Restablecer valores de fábrica"))
		self.boton_aceptar = wx.Button(self, wx.ID_OK, _("&Aceptar"))
		self.boton_cancelar = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"))
		self.boton_aplicar = wx.Button(self, wx.ID_APPLY, _("A&plicar"))
		self.boton_restablecer.SetHelpText(_("Pone todas las opciones con sus valores de fábrica. "
			"No se guarda nada hasta pulsar Aplicar o Aceptar."))
		botones.Add(self.boton_restablecer, 0, wx.ALL, 4)
		botones.AddStretchSpacer()
		for boton in (self.boton_aceptar, self.boton_cancelar, self.boton_aplicar):
			botones.Add(boton, 0, wx.ALL, 4)
		sizer.Add(botones, 0, wx.EXPAND | wx.ALL, 4)
		self.SetSizerAndFit(sizer)
		self.SetMinSize((520, 420))

		self.boton_aceptar.SetDefault()
		self.SetEscapeId(wx.ID_CANCEL)
		self.boton_aceptar.Bind(wx.EVT_BUTTON, self._al_aceptar)
		self.boton_cancelar.Bind(wx.EVT_BUTTON, self._al_cancelar)
		self.boton_aplicar.Bind(wx.EVT_BUTTON, self._al_aplicar)
		self.boton_restablecer.Bind(wx.EVT_BUTTON, self._al_restablecer)
		# EVT_CHAR_HOOK ve la tecla antes que el control: en un cuadro de texto de
		# Windows, Ctrl+H sería «borrar hacia atrás».
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla)
		self.Bind(wx.EVT_INIT_DIALOG, self._al_iniciar)
		self.CentreOnParent()

	# ── Construcción ─────────────────────────────────────────────────────────

	def _crear_pagina(self, pagina: Pagina, indice: int) -> wx.Window:
		"""Crea el panel de una página, con la etiqueta de cada control justo antes."""
		panel = scrolled.ScrolledPanel(self.libro)
		sizer = wx.BoxSizer(wx.VERTICAL)
		for opcion in pagina.opciones:
			if opcion.tipo == SECRETO and self._secretos is None:
				log.warning("Opción secreta «%s» omitida: no se pasó almacén de secretos", opcion.clave)
				continue
			control = self._crear_opcion(panel, sizer, opcion)
			ayuda = _(opcion.ayuda)
			if ayuda:
				control.SetHelpText(ayuda)
			self._controles[opcion.clave] = (opcion, control, indice)
			self._registrar_ids(control, opcion.clave)
			self._escribir_control(opcion, control, self._valor_guardado(opcion))
		panel.SetSizer(sizer)
		# Desplaza la vista hasta el control enfocado (útil con muchas opciones).
		panel.SetupScrolling(scroll_x=False)
		return panel

	def _registrar_ids(self, control: wx.Window, clave: str) -> None:
		self._clave_por_id[control.GetId()] = clave
		for hijo in control.GetChildren():
			self._registrar_ids(hijo, clave)

	def _crear_opcion(self, panel: wx.Window, sizer: wx.Sizer, opcion: Opcion) -> wx.Window:
		"""Crea (etiqueta +) control según el tipo."""
		etiqueta = _(opcion.etiqueta)
		if opcion.tipo == BOOL:
			# La casilla lleva su propio texto: no necesita StaticText.
			control = wx.CheckBox(panel, label=etiqueta)
			sizer.Add(control, 0, wx.ALL, 6)
			return control
		# La etiqueta se crea ANTES que el control: así la enlaza el lector.
		texto = wx.StaticText(panel, label=etiqueta)
		sizer.Add(texto, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
		minimo, maximo = self._limites(opcion)
		if opcion.tipo == ENTERO:
			control = wx.SpinCtrl(panel, min=int(minimo), max=int(maximo))
		elif opcion.tipo == DECIMAL:
			control = wx.SpinCtrlDouble(panel, min=minimo, max=maximo, inc=opcion.incremento)
			control.SetDigits(2)
		elif opcion.tipo == OPCION:
			control = wx.Choice(panel, choices=[_(nombre) for _valor, nombre in opcion.opciones])
		elif opcion.tipo == SECRETO:
			control = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
		elif opcion.tipo in (TEXTO, CARPETA):
			control = wx.TextCtrl(panel)
		elif opcion.tipo == COLUMNAS:
			# Un botón cuyo texto dice las columnas elegidas; al pulsarlo se eligen y ordenan.
			control = wx.Button(panel, label=_("Elegir..."), style=wx.BU_LEFT)
			control.Bind(wx.EVT_BUTTON, lambda _evento, o=opcion, c=control: self._elegir_columnas(o, c))
		else:
			raise ValueError(f"Tipo de opción desconocido: {opcion.tipo}")
		if opcion.tipo == CARPETA:
			fila = wx.BoxSizer(wx.HORIZONTAL)
			fila.Add(control, 1, wx.EXPAND)
			boton = wx.Button(panel, label=_("E&xaminar..."))
			boton.SetHelpText(_("Elige la carpeta en una ventana de selección."))
			boton.Bind(wx.EVT_BUTTON, lambda _evento, c=control: self._elegir_carpeta(c))
			fila.Add(boton, 0, wx.LEFT, 6)
			sizer.Add(fila, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
		else:
			sizer.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
		return control

	def _limites(self, opcion: Opcion) -> tuple[float, float]:
		"""Límites del descriptor o, si faltan, de la regla de la configuración."""
		regla = self._config.regla(opcion.clave) if opcion.tipo in (ENTERO, DECIMAL) else None
		minimo = opcion.minimo if opcion.minimo is not None else (regla.minimo if regla and regla.minimo is not None else 0)
		maximo = opcion.maximo if opcion.maximo is not None else (
			regla.maximo if regla and regla.maximo is not None else 1_000_000)
		return minimo, maximo

	def _nombres_columnas(self, opcion: Opcion) -> dict[str, str]:
		return {identificador: _(nombre) for identificador, nombre in opcion.opciones}

	def _rotular_columnas(self, opcion: Opcion, control: wx.Button) -> None:
		nombres = self._nombres_columnas(opcion)
		elegidas = [nombres[c] for c in self._valores_columnas.get(opcion.clave, []) if c in nombres]
		# El lector lee el texto del botón (no la etiqueta de delante): se repite qué es.
		nombre = _(opcion.etiqueta).replace("&", "").rstrip(":")
		control.SetLabel(_("{nombre}: {columnas}. Pulsa para cambiarlas").format(
			nombre=nombre, columnas=", ".join(elegidas) or _("ninguna")))

	def _elegir_columnas(self, opcion: Opcion, control: wx.Button) -> None:
		"""Abre el cuadro de columnas (marcar y ordenar) con una vista previa de la lectura."""
		from app.ui.dialogos.columnas import DialogoColumnas
		from app.ui.lectura_listas import ejemplo_lectura

		nombres = self._nombres_columnas(opcion)
		ejemplos = {identificador: _(texto) for identificador, texto in opcion.ejemplos}
		predeterminadas = self._config.valor_por_defecto(opcion.clave) or list(nombres)
		dialogo = DialogoColumnas(
			self, list(nombres.items()), self._valores_columnas.get(opcion.clave, []), predeterminadas,
			titulo=_(opcion.etiqueta).replace("&", "").rstrip(":"),
			ejemplo=(lambda activas: ejemplo_lectura(nombres, ejemplos, activas)) if ejemplos else None,
		)
		try:
			if dialogo.ShowModal() == wx.ID_OK:
				self._valores_columnas[opcion.clave] = list(dialogo.activas)
				self._rotular_columnas(opcion, control)
		finally:
			dialogo.Destroy()
		control.SetFocus()

	def _elegir_carpeta(self, control: wx.TextCtrl) -> None:
		dialogo = wx.DirDialog(self, _("Elige una carpeta"), control.GetValue())
		try:
			if dialogo.ShowModal() == wx.ID_OK:
				control.SetValue(dialogo.GetPath())
		finally:
			dialogo.Destroy()
		control.SetFocus()

	# ── Valores ──────────────────────────────────────────────────────────────

	def _valor_guardado(self, opcion: Opcion) -> Any:
		if opcion.tipo == SECRETO:
			valor = self._secretos.obtener(opcion.clave) or ""
			self._valores_secretos[opcion.clave] = valor
			return valor
		return self._config.obtener(opcion.clave)

	def _escribir_control(self, opcion: Opcion, control: wx.Window, valor: Any) -> None:
		if opcion.tipo == BOOL:
			control.SetValue(bool(valor))
		elif opcion.tipo in (ENTERO, DECIMAL):
			control.SetValue(valor)
		elif opcion.tipo == OPCION:
			valores = [v for v, _nombre in opcion.opciones]
			control.SetSelection(valores.index(valor) if valor in valores else 0)
		elif opcion.tipo == COLUMNAS:
			conocidas = {identificador for identificador, _nombre in opcion.opciones}
			elegidas = [c for c in (valor if isinstance(valor, list) else []) if c in conocidas]
			self._valores_columnas[opcion.clave] = list(dict.fromkeys(elegidas))
			self._rotular_columnas(opcion, control)
		else:
			control.ChangeValue("" if valor is None else str(valor))

	def _leer_control(self, opcion: Opcion, control: wx.Window) -> Any:
		if opcion.tipo == BOOL:
			return bool(control.GetValue())
		if opcion.tipo == ENTERO:
			return int(control.GetValue())
		if opcion.tipo == DECIMAL:
			return round(float(control.GetValue()), 6)
		if opcion.tipo == OPCION:
			indice = control.GetSelection()
			return opcion.opciones[indice][0] if indice != wx.NOT_FOUND else None
		if opcion.tipo == COLUMNAS:
			return list(self._valores_columnas.get(opcion.clave, []))
		return control.GetValue()

	def control_de(self, clave: str) -> wx.Window:
		"""Control asociado a una clave (para pruebas y para dar el foco)."""
		return self._controles[clave][1]

	def valores(self) -> dict[str, Any]:
		"""Valores actuales de los controles (sin secretos)."""
		return {
			clave: self._leer_control(opcion, control)
			for clave, (opcion, control, _pagina) in self._controles.items()
			if opcion.tipo != SECRETO
		}

	# ── Acciones ─────────────────────────────────────────────────────────────

	def aplicar(self) -> bool:
		"""Valida todo y guarda lo que cambió. ``False`` si algo no es válido."""
		cambios: dict[str, Any] = {}
		for clave, valor in self.valores().items():
			try:
				self._config.validar(clave, valor)
			except ErrorValidacion as error:
				self._senalar(clave, str(error))
				return False
			if valor != self._config.obtener(clave, None):
				cambios[clave] = valor
		cambios_secretos = {
			clave: (control.GetValue() or None)
			for clave, (opcion, control, _pagina) in self._controles.items()
			if opcion.tipo == SECRETO and control.GetValue() != self._valores_secretos.get(clave, "")
		}
		try:
			if cambios:
				self._config.establecer_varias(cambios)
			if cambios_secretos:
				self._secretos.guardar_varios(cambios_secretos)
				self._valores_secretos.update({c: v or "" for c, v in cambios_secretos.items()})
		except Exception as error:  # noqa: BLE001 - ErrorConfiguracion, ErrorSecretos, OSError...: se informa al usuario
			log.exception("No se pudieron guardar las preferencias")
			mostrar_aviso(self, _("No se pudieron guardar las preferencias: %s") % error, _("Error"))
			return False
		log.info("Preferencias aplicadas: %d cambios, %d secretos", len(cambios), len(cambios_secretos))
		if self._anunciar is not None and (cambios or cambios_secretos):
			self._anunciar(_("Preferencias guardadas."))
		return True

	def _senalar(self, clave: str, mensaje: str) -> None:
		"""Avisa de un valor no válido y lleva el foco a su control."""
		opcion, control, pagina = self._controles[clave]
		self.libro.SetSelection(pagina)
		nombre = _(opcion.etiqueta).replace("&", "").rstrip(":")
		mostrar_aviso(self, f"{nombre}\n\n{mensaje}", _("Valor no válido"))
		control.SetFocus()

	def restablecer_controles(self) -> None:
		"""Pone los valores de fábrica en los controles (sin guardar)."""
		for clave, (opcion, control, _pagina) in self._controles.items():
			if opcion.tipo == SECRETO:
				continue  # los secretos no tienen valor de fábrica: no se borran por esto
			self._escribir_control(opcion, control, self._config.valor_por_defecto(clave))

	def _cerrar(self, codigo: int) -> None:
		if self.IsModal():
			self.EndModal(codigo)
		else:
			self.SetReturnCode(codigo)
			self.Hide()

	# ── Eventos ──────────────────────────────────────────────────────────────

	def control_inicial(self) -> wx.Window | None:
		"""Primer control de la primera página (foco inicial)."""
		for _opcion, control, pagina in self._controles.values():
			if pagina == 0:
				return control
		return None

	def _al_iniciar(self, evento: wx.InitDialogEvent) -> None:
		control = self.control_inicial()
		if control is not None:
			control.SetFocus()
		evento.Skip()

	def _al_aceptar(self, _evento: wx.CommandEvent) -> None:
		if self.aplicar():
			self._cerrar(wx.ID_OK)

	def _al_cancelar(self, _evento: wx.CommandEvent) -> None:
		log.debug("Preferencias canceladas; no se guarda nada")
		self._cerrar(wx.ID_CANCEL)

	def _al_aplicar(self, _evento: wx.CommandEvent) -> None:
		self.aplicar()

	def _al_restablecer(self, _evento: wx.CommandEvent) -> None:
		if not confirmar(self, _("¿Poner todas las opciones con sus valores de fábrica? "
				"No se guardará nada hasta que pulses Aplicar o Aceptar."), _("Restablecer valores de fábrica")):
			return
		self.restablecer_controles()
		self.libro.SetSelection(0)
		control = self.control_inicial()
		if control is not None:
			control.SetFocus()
		if self._anunciar is not None:
			self._anunciar(_("Valores de fábrica puestos. Pulsa Aceptar para guardarlos."))

	def _al_tecla(self, evento: wx.KeyEvent) -> None:
		if evento.GetKeyCode() in (ord("H"), ord("h")) and evento.GetModifiers() == wx.MOD_CONTROL:
			self.mostrar_ayuda_de(wx.Window.FindFocus())
			return
		evento.Skip()

	def texto_ayuda(self, ventana: wx.Window | None) -> str:
		"""Ayuda del control (o de su antecesor con ayuda) para Ctrl+H."""
		actual = ventana
		while actual is not None and actual is not self:
			clave = self._clave_por_id.get(actual.GetId())
			if clave is not None:
				opcion = self._controles[clave][0]
				nombre = _(opcion.etiqueta).replace("&", "").rstrip(":")
				return f"{nombre}. {_(opcion.ayuda)}" if opcion.ayuda else nombre
			ayuda = actual.GetHelpText()
			if ayuda:
				return ayuda
			actual = actual.GetParent()
		return _("No hay ayuda para este elemento.")

	def mostrar_ayuda_de(self, ventana: wx.Window | None) -> None:
		mostrar_ayuda(self, _("Ayuda"), self.texto_ayuda(ventana))
