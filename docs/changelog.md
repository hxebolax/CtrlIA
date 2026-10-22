# Novedades de Ctrl+IA

Cada versión tiene su apartado, de la más reciente a la más antigua. Los cambios se cuentan con palabras de quien usa el programa: qué puedes hacer y dónde.

## Versión 1.5.1 - 2026-10-22

### Novedades

- Los «agentes» del catálogo se llaman ahora por su nombre: **skills** (habilidades), carpetas con un SKILL.md que explican al agente de IA cómo hacer un trabajo. Herramientas, «Gestor de skills...» (Ctrl+Alt+G); en «Nueva sesión», «Skills del catálogo para la sesión»; en «Opciones de la sesión», «Skills de la sesión»; «Restaurar las skills de ejemplo»; y en Claude Code salen como «ctrlia-skills:nombre». Funcionan igual que antes, con cualquier agente de IA. Al abrir esta versión, Ctrl+IA pasa solo tu catálogo, tus skills (data\skills) y las de ejemplo (data\skills_ejemplo) a los nombres nuevos, sin perder nada.
- Esta versión incluye todas las novedades de la 1.5.0, la primera publicada también en GitHub (github.com/hxebolax/CtrlIA): están a continuación.
- Deshacer los cambios de archivos desde un mensaje (Ctrl+Alt+Z, en el menú Sesión y en el menú contextual de tus mensajes), con Claude Code, Codex, Antigravity, Gemini y los agentes ACP. Antes de cada mensaje, Ctrl+IA guarda cómo están los archivos de la carpeta en un repositorio aparte que nunca toca el tuyo, y puedes devolverlos a como estaban antes de cualquiera de tus mensajes, viendo antes qué pasará con cada archivo. «Rehacer los cambios de archivos deshechos» (Ctrl+Alt+Mayús+Z) vuelve atrás el último deshacer. Con tu siguiente mensaje, el agente sabe qué archivos han vuelto atrás. En las sesiones por SSH, solo con Claude Code. Se desactiva en Preferencias, Agentes; necesita Git para Windows.
- Herramientas de Ctrl+IA para los agentes: pueden avisarte de algo importante (con voz y braille y, si no estás en la ventana de Ctrl+IA, con sonido y notificación de Windows), dar su sesión por terminada como con Ctrl+D y cambiarle el título. Solo afectan a su sesión, y cada uso queda en la conversación como mensaje de Ctrl+IA. Valen para Claude Code (también por SSH), Codex, Antigravity, Gemini y los agentes ACP de este equipo, y salen como «ctrlia» en «Herramientas del agente» (Ctrl+Alt+H). Se desactivan en Preferencias, Agentes.
- Estado de la PR y de las comprobaciones automáticas (CI) de cada sesión, con cualquier agente: si la carpeta es un repositorio de GitHub y su rama tiene una PR, Ctrl+IA te avisa una vez cuando las comprobaciones fallan o pasan, cuando la PR se fusiona y cuando en la revisión piden cambios. En el menú Sesión, «PR y comprobaciones» (también en el menú contextual de la lista de sesiones): ver el estado (Ctrl+Alt+P), abrir la PR en el navegador, copiar su enlace, leer el fallo de las comprobaciones y pedir al agente que lo arregle (prepara el mensaje sin enviarlo). Propiedades y Estado del turno dicen cómo va. Necesita la herramienta gh con tu sesión de GitHub; se desactiva en Preferencias, Anuncios.
- Conversación automática entre sesiones (Sesión, Ctrl+Alt+Mayús+R): dos sesiones de Ctrl+IA, de cualquier agente, se hablan solas los turnos que elijas (4 de fábrica, hasta 20); por ejemplo, Claude escribe y Codex revisa. Para cuando una escribe [FIN], al llegar al máximo, si un turno falla o si la detienes (Sesión, «Detener la conversación entre sesiones», o Ctrl+. en cualquiera de las dos). Los permisos y las preguntas te siguen llegando a ti: la conversación se pausa hasta que respondes. «Ver la conversación entre sesiones» (Ctrl+Alt+Mayús+C) muestra lo que se han dicho, en orden.
- Listas de la sesión (Ctrl+Mayús+I): cinco páginas nuevas, «Errores» (Ctrl+9), «Commits» (Ctrl+0), «Incidencias y PR», «Archivos leídos» y «Tus mensajes»; un cuadro «Filtrar» (Ctrl+F) que reduce la lista sin distinguir mayúsculas ni tildes y dice cuántos quedan («3 de 12»); y «Mostrar» (Ctrl+T) para ver toda la sesión o solo el mensaje elegido o su turno. Las sesiones de Codex también llenan commits, errores y archivos leídos.
- Notas en los mensajes favoritos, como recordatorio: Ctrl+Alt+Mayús+N, el menú contextual del favorito o F2 en la lista de favoritos. La nota se oye al ir al favorito con Alt+Flecha, sale en la columna Indicadores («Favorito: nota») y tiene su columna en la lista de favoritos.
- Continuar en este equipo una sesión importada de otro: «Continuar aquí» crea una sesión nueva en la que eliges el agente y la carpeta, con los mensajes de la importada para leerlos. Tu primer mensaje le llega al agente con la conversación anterior delante (recortada si es muy larga, y se le dice). La importada no cambia.
- Divisor entre los mensajes y el cuadro de respuesta: se arrastra con el ratón (doble clic lo devuelve a su sitio) y, con el teclado, Ver, Distribución de la ventana, «Un poco más de espacio para los mensajes» (Ctrl+Alt+Mayús+Abajo) y «Un poco más de espacio para la respuesta» (Ctrl+Alt+Mayús+Arriba). Se recuerda. Nunca recibe el foco.
- Manual reescrito (F1): la mitad de largo y con todo lo que hace el programa, comprobado contra el propio programa, con una tabla de lo que puede hacer cada agente. La lista de teclas (Ctrl+F1) está completa: añade las del panel de lectura, las listas de casillas, enviar a otra sesión y la conversación entre sesiones, y corrige varias descripciones.
- Opción nueva en Preferencias, Anuncios, desactivada de fábrica: palabras curiosas en la barra de estado mientras el agente escribe («Cavilando…», «Hilvanando…»). Nunca se dicen por voz.

- Actualizaciones desde dos sitios: el servidor de Ctrl+IA y GitHub (github.com/hxebolax/CtrlIA, donde además está ya el código del programa). Si uno no responde se usa el otro, y si GitHub tiene una versión más nueva se ofrece esa; siempre se comprueban la firma y la huella del paquete antes de instalar. También traen las skills de ejemplo nuevas.
- Cuando la versión nueva está descargada, Ctrl+IA pregunta una sola vez si quieres reiniciar ahora para aplicar la actualización, y avisa de las sesiones con el agente trabajando. Ya no pregunta después si quieres salir. Con «Más tarde» no se instala nada y la descarga se guarda para cuando la pidas desde Ayuda, «Buscar actualizaciones...».

### Correcciones

- «Nueva sesión» ya no da «Error inesperado» (wxAssertionError en IsChecked) al abrirse con NVDA: el lector preguntaba por una casilla de la lista de skills que acababa de cambiar y Ctrl+IA no lo comprobaba. Afectaba a todas las listas de casillas.
- El cuadro de versión nueva muestra las novedades solo cuando vienen publicadas; ya no dice que el servidor no las ha publicado. El progreso de la actualización se dice por voz también cuando el aviso llega solo.
- Antigravity: la lista de modelos de «Nueva sesión» y «Opciones de la sesión» trae solo los que anuncia tu Antigravity CLI. Desde su versión 1.4.4 ya no acepta los nombres genéricos «flash», «pro» y «flash_lite», y elegirlos hacía fallar la sesión. Si una sesión ya guardada usa un modelo que Antigravity no reconoce, Ctrl+IA te lo dice, sigue con el modelo predeterminado y lo recuerda; solo tienes que volver a enviar el mensaje.

## Versión 1.5.0 - 2026-10-21

Primera versión publicada también en GitHub. Todas sus novedades y correcciones están en la 1.5.1.

## Versión 1.4.4 - 2026-10-21

### Novedades

- Gestor de agentes (Herramientas, Ctrl+Alt+G): un catálogo con tus agentes, las carpetas con un SKILL.md que explican al agente de IA cómo hacer un trabajo (revisar la accesibilidad, escribir pruebas, traducir...). Añade de una vez todos los de una carpeta eligiendo con casillas cuáles y con qué categoría, añade uno, crea uno nuevo con un SKILL.md de plantilla, cambia su nombre, descripción, categoría y notas, míralo o edítalo en tu editor, abre su carpeta o quítalo. F5 revisa si algún SKILL.md ha cambiado o ya no está. El catálogo se puede exportar e importar.
- 14 agentes de ejemplo listos para usar (accesibilidad de wxPython, pruebas, traducciones, base de datos, registro, actualizador y más): se añaden al catálogo la primera vez, en la categoría «Ejemplos de Ctrl+IA» y con «(ejemplo)» detrás del nombre, para que veas cómo se usan, se editan o se quitan. Si los quitas, «Restaurar los agentes de ejemplo», en el gestor, los vuelve a añadir.
- Agentes en las sesiones, con cualquier agente de IA: en «Nueva sesión» y en «Opciones de la sesión» eliges con casillas qué agentes del catálogo usa la sesión. Claude Code los carga como habilidades propias de la sesión (y puede leer sus carpetas, solo leer, sin pedirte permiso); a Codex, Antigravity, Gemini y los demás se les dice con tu primer mensaje qué agentes tienen y dónde está cada SKILL.md, y los leen cuando hace falta; Ollama y los equipos por SSH reciben su contenido. Ctrl+IA nunca copia, cambia ni borra tus carpetas de agentes.
- Mensajes favoritos en cualquier sesión, de cualquier agente (también las de otros programas y las importadas): Ctrl+Alt+F marca o quita el mensaje elegido, y Alt+Flecha arriba y Alt+Flecha abajo llevan al favorito anterior o al siguiente en la lista de mensajes y en el panel de lectura, aunque esté entre los mensajes antiguos aún sin cargar. Así vuelves enseguida a una respuesta importante aunque después hayan llegado muchos mensajes.
- Ver, «Mensajes favoritos...» (Ctrl+Alt+Mayús+F): los favoritos de todas las sesiones; Intro te lleva al mensaje, Suprimir lo quita de favoritos y Ctrl+C copia su texto. Nuevo indicador «Favorito» en la columna Indicadores.
- Ctrl+O abre en la app de escritorio de Claude también las sesiones de Claude Code creadas en Ctrl+IA (la app las importa la primera vez y desde entonces salen en su lista), y en la app de Codex las sesiones de Codex creadas aquí.
- Sesión, «Herramientas del agente...» (Ctrl+Alt+H): qué tiene cargado el agente en la sesión, con Claude Code y Codex: servidores MCP y conectores con su estado, complementos, habilidades y, en Claude, subagentes y herramientas.
- «Nueva sesión» tiene también el «Estilo de las respuestas» (Claude Code) y recuerda el último que elegiste.
- Dos sonidos nuevos, con su casilla en Preferencias, Sonidos: cuando el agente resume la conversación para liberar memoria (compactación) y cuando te acercas al límite de uso de tu cuenta.

### Correcciones

- Claude Code tiene ya las herramientas de Chrome: «Dejar que Claude use Chrome (Claude en Chrome)», en Preferencias, Agentes, viene activada (y se activa una vez al actualizar). Antes venía apagada y las sesiones de Ctrl+IA no podían usar el navegador. El panel de navegador propio de la app de escritorio y el uso del ordenador solo existen dentro de esa app.
- Al citar dos o más fragmentos en tu respuesta (Ctrl+R), Ctrl+IA añade al principio una frase que le explica al agente que respondes por partes, debajo de cada cita, para que relacione cada cita con tu respuesta.
- Codex: si una sesión usaba un modelo retirado y Codex lo cambió por otro, Ctrl+IA lo recuerda; antes, al volver a abrir Ctrl+IA, daba otra vez el error.
- Al abrir una sesión, el foco ya no cae en un aviso «Permitido…» que Ctrl+IA dejó al final al contestar por ti con una regla, sino en la última respuesta.
- Las listas de casillas (Sonidos, Columnas, Grupo y las nuevas de agentes) dicen siempre si cada elemento está marcado y lo anuncian al cambiarlo; Espacio funciona aunque no haya ninguno elegido.

## Versión 1.4.3 - 2026-10-21

### Novedades

- Claude Code: «Permitir siempre en esta carpeta» en sus peticiones de permiso, cuando Claude Code lo propone (siempre con las herramientas externas, los servidores MCP). Lo apunta el propio Claude Code en el archivo .claude\settings.local.json de la carpeta del proyecto, igual que su «Sí, y no volver a preguntar» de la terminal, y ya no te lo vuelve a preguntar en ninguna conversación de esa carpeta. Lo ves y lo quitas en Herramientas, «Configuración de los agentes» (Ctrl+Mayús+W), Claude Code, ámbito «Proyecto, solo tú», «Permitido sin preguntar».
- Claude Code: con una herramienta de un servidor MCP puedes permitir de una vez todas las de ese servidor, con «Permitir todo «servidor» durante esta sesión» o «… siempre en esta carpeta».
- Antigravity: cuando no puede usar una herramienta externa (servidor MCP) por falta de permiso, Ctrl+IA te dice qué herramienta y de qué servidor, y te ofrece «Permitir y reintentar» (solo esa herramienta) o «Permitir todo el servidor y reintentar».
- Reglas de permisos de Antigravity (Herramientas): nuevo tipo de regla «Usar las herramientas de un servidor MCP…».
- Sesión, «Adjuntar lo copiado (imagen o archivos)» (Ctrl+Alt+V): adjunta la imagen o los archivos que hayas copiado, aunque lo copiado traiga también texto.
- Enviar la respuesta a otra sesión (Ctrl+Alt+R, en el menú Sesión y en el menú contextual de cada respuesta): pasa la respuesta elegida, o la última del agente, a otra de tus sesiones de Ctrl+IA, de cualquier agente, para que la revise o la conteste. Lleva delante una línea que dice qué agente la escribió, la puedes cambiar antes de enviarla y Ctrl+IA te lleva a esa sesión para oír su respuesta; Ctrl+Alt+R e Intro se la devuelven a la primera.
- Añadir al turno en marcha (Ctrl+Alt+Intro) funciona también con Claude Code: tu mensaje llega a lo que está haciendo sin detenerlo.
- Claude Code: «Estilo de las respuestas» en «Opciones de la sesión» (Ctrl+Mayús+O): «Conciso» (respuestas breves, cómodas de escuchar), «Proactivo», «Explicativo», «Aprendizaje» o los tuyos, solo para esa sesión y sin cambiar la configuración de Claude Code.
- Claude Code: nueva opción en Preferencias, Agentes, «Dejar que Claude use Chrome (Claude en Chrome)» (Alt+U), desactivada de fábrica, para que Claude pueda ver tus pestañas, leer páginas y rellenar formularios con tu permiso.
- Claude Code: «Permitir durante esta sesión» guarda también la carpeta que Claude Code propone cuando la acción trabaja fuera de la carpeta de la sesión, así que en los mensajes siguientes no vuelve a preguntar por lo mismo. El detalle te la dice, y nunca se guarda un disco entero ni la raíz de una carpeta compartida de la red.
- Permisos de la sesión (Ctrl+Mayús+M): las carpetas guardadas salen con las reglas, y el campo nuevo «Reglas de la configuración de...» (Alt+R) muestra, solo para leer, lo que la propia configuración de Claude Code permite, pregunta siempre o no permite nunca en esa carpeta, con su archivo, y si alguna regla espera a que la carpeta sea de confianza. Con Codex dice cuántos archivos de reglas propios tiene.
- Claude Code: si en una pregunta Claude añade a una opción una vista previa (un borrador, código...), el diálogo la muestra junto a la opción.
- Claude Code: si Claude programa volver a algo más tarde (comprobar algo dentro de unos minutos o una tarea que se repite), Ctrl+IA te lo dice y mantiene Claude Code abierto hasta entonces, para que no se pierda.
- Clonar un repositorio de GitHub: salen también los repositorios de tus organizaciones, con una columna «Dueño», y siempre está la fila «Otro repositorio: escribir dueño/nombre a mano...».
- Propiedades (Alt+Intro) dice en qué rama está la carpeta de la sesión y si es un repositorio normal o una copia aislada.
- «Lo que el agente sabe de ti» (Ctrl+Mayús+K) muestra también las habilidades, los comandos guardados y los AGENTS.override.md de Codex, tiene una columna con la fecha de modificación y se puede ordenar (más recientes, más antiguos, por nombre o por agente) con la tecla Aplicaciones; el orden se recuerda.
- Codex: si trabaja con menos permisos de los que pediste (por ejemplo, solo lectura porque el aislamiento de Windows no está preparado), Ctrl+IA te avisa una vez y te dice cómo arreglarlo.
- Las listas de la sesión tienen una página nueva, «Decisiones» (Ctrl+8): las preguntas que te hizo el agente y los planes que te propuso, con lo que respondiste. Intro las muestra completas y Ctrl+C las copia. En las sesiones de Ctrl+IA, desde esta versión.
- Los cambios de un archivo se leen con palabras: «Línea 12, añadida: …», «Línea 13, quitada: …». En Preferencias, Mensajes, «Cómo se leen las líneas cambiadas de un archivo» eliges también palabras cortas («Más 12», «Menos 12») o los signos + y −, como en Git. Guardar los cambios como archivo guarda siempre el original.
- Claude Code responde antes: se queda abierto entre un mensaje y el siguiente, en lugar de arrancar de nuevo cada vez, así que no vuelve a cargar ni a conectar sus herramientas externas (servidores MCP y conectores) en cada mensaje. Se cierra solo tras un rato sin mensajes y se vuelve a abrir cuando escribes. Se elige en Preferencias, Agentes, «Mantener Claude Code abierto entre mensajes» (Alt+M): 5, 15 (de fábrica), 30 minutos o 1 hora, o «No» para arrancarlo en cada mensaje como antes. Si cambias el modelo, el esfuerzo o quitas permisos de la sesión, Claude Code se reinicia solo antes del siguiente mensaje; el modo de permisos cambia al momento. Codex, Antigravity y los agentes ACP (como Gemini CLI) ya funcionaban así.
- Codex avisa si una de sus herramientas externas (servidor MCP) no ha podido arrancar, con el motivo, o si necesita que vuelvas a iniciar sesión en ella («codex mcp login» y su nombre). Se avisa una vez, no a cada momento.
- Seguir la sesión original en lugar de una copia: activa en Preferencias, Historial, «Al continuar una sesión de otro programa, seguir la original sin copiarla» (Alt+S). Con ella, «Continuar aquí» y escribir en una sesión de Claude Code o de Codex de la terminal, de su app o de tu Mac por SSH siguen esa misma sesión: lo que hables en Ctrl+IA aparece después allí, por ejemplo si vuelves a la terminal del Mac. Por seguridad, solo se hace si la sesión no está abierta en el otro programa ni se ha usado en los dos últimos minutos; si lo está, Ctrl+IA te lo explica y te ofrece una copia. Viene desactivada: de fábrica se sigue haciendo una copia.
- «Continuar aquí» trae la conversación anterior: la sesión nueva empieza con todos los mensajes que ya tenía la original, para leerla entera en Ctrl+IA. Antes empezaba vacía, aunque el agente sí la recordaba.
- Nueva opción en Preferencias, Agentes, «Esperar a que se conecten las herramientas MCP de Claude antes de cada mensaje» (Alt+H), activada de fábrica (ver Correcciones).
- Leer y responder a la vez: en el visor de un mensaje (Intro en la lista de mensajes), Alt+3 te lleva al cuadro de respuesta sin cerrarlo, y Alt+6 te devuelve al visor desde la ventana principal. Así puedes leer un mensaje largo e ir contestando a cada punto.
- Citar en la respuesta (Ctrl+R, también en el menú Sesión): añade a tu respuesta, como cita, lo que tengas seleccionado en el visor o en el panel de lectura, para contestar punto por punto (en el visor con formato, selecciona, pulsa Ctrl+C y después Ctrl+R). Sin seleccionar nada, en el visor como texto y en el panel de lectura cita el párrafo donde estás, y en la lista de mensajes, el mensaje entero. El foco no se mueve: sigues leyendo y escribes cuando quieras con Alt+3.
- Nueva sesión a partir de un mensaje (Ctrl+Alt+N, en Archivo, en el menú contextual del mensaje y desde el visor): abre «Nueva sesión» con el mismo agente, la misma carpeta (o el mismo equipo por SSH) y el mensaje, o lo que hayas seleccionado de él, citado como primer mensaje. Escribe debajo lo que quieres explorar y créala. Funciona con todos los agentes.
- «Nueva sesión» recuerda el modelo y el esfuerzo que elegiste la última vez con cada agente y te los propone, también los de la lista real de modelos de Codex y Antigravity. Lo que cambies en «Opciones de la sesión» también cuenta.
- «Opciones de la sesión» (Ctrl+Mayús+O) muestra la lista real de modelos de Codex y Antigravity, como «Nueva sesión».
- Claude Code: cuando una herramienta externa (un servidor MCP) te pide datos, que abras un enlace o que confirmes algo, Ctrl+IA te lo pregunta. Antes se cancelaba sin decirte nada.
- Las sesiones que ya tiene tu Mac o tu Linux aparecen en la lista: las de Claude Code de su terminal y de su app de escritorio, y los hilos de Codex, junto a las de Windows. Basta con tener el equipo en Herramientas, «Equipos por SSH»; Ctrl+IA las lee por SSH sin instalar ni cambiar nada en él, al arrancar, cada medio minuto y con F5, y la lista nunca espera al otro equipo.
- Siempre sabes a qué equipo pertenece cada sesión: el Origen lo dice (por ejemplo «Terminal en Mac del estudio (SSH)»), hay una columna nueva, Equipo («Este equipo (Windows)» o «Mac del estudio (macOS)»), que añades en Ver, «Columnas de la lista...», y dos vistas nuevas en Ver, Mostrar: «Solo de este equipo (Windows)» y «De otros equipos por SSH». Propiedades también dice el equipo, y Ctrl+F encuentra las sesiones por su nombre.
- Al abrir una sesión del otro equipo se traen sus mensajes, y si alguien sigue trabajando en ella allí, lo nuevo aparece solo. «Continuar aquí» la sigue en el mismo equipo y en la misma carpeta.
- Si el equipo está apagado o no responde, Ctrl+IA te lo dice una vez, sigue enseñando lo último que leyó y te avisa cuando vuelve a responder.
- Nueva casilla en cada equipo, «Mostrar en la lista las sesiones que ya tiene este equipo» (Alt+M), marcada de fábrica: desmárcala si el equipo suele estar apagado.

### Correcciones

- Claude Code ya no te pide varias veces el mismo permiso. Cuando usaba a la vez varias veces la misma herramienta externa (servidor MCP), llegaba una petición por cada uso, y permitir la primera durante la sesión no quitaba las demás. Ahora solo te avisa de la primera, y al permitirla durante la sesión o siempre, las demás iguales se permiten con tu misma respuesta y Ctrl+IA te lo dice.
- Las peticiones de permiso de las herramientas externas dicen qué herramienta es y de qué servidor, por ejemplo «Claude quiere usar «Eco» del servidor MCP «prueba»». Las de Codex de tus propios servidores MCP ya no salen en inglés.
- Claude Code: si una herramienta tiene que confirmarse cada vez (por ejemplo, porque tu organización lo exige), ya no se ofrecen «durante esta sesión» ni «siempre», que no servirían. En el modo «Solo planificar», la petición avisa de que Claude Code pregunta siempre por las herramientas MCP que pueden cambiar algo.
- Leer tus límites de uso de Claude y la lista de sus comandos ya no pone en marcha tus herramientas externas (servidores MCP). Antes cada consulta las arrancaba, y las que abren el navegador o piden iniciar sesión lo hacían sin motivo.
- Antigravity: una acción que falla o que se deniega aparece como terminada y con su motivo; antes se quedaba como si siguiera en marcha.
- Pegar una imagen con Ctrl+V en el cuadro de respuesta funciona también con las imágenes copiadas en el navegador y en las aplicaciones que solo las dan como PNG, y con los archivos copiados en el Explorador de Windows, que se adjuntan tal cual. Las capturas que podían quedar transparentes (vacías) se ven bien.
- Si lo copiado trae texto e imagen, como una celda de Excel o un párrafo de Word, Ctrl+V pega el texto, como en cualquier programa, en lugar de adjuntar la imagen. Te avisa de que hay una imagen y Ctrl+Alt+V la adjunta.
- Lo que copias desde Ctrl+IA (un mensaje, el informe, los detalles de un error, el título de una sesión...) sigue en el portapapeles aunque cierres Ctrl+IA.
- Claude Code: «Enviar ahora» justo después de enviar, mientras Claude Code aún arrancaba, podía hacer que el mensaje nuevo llegase antes que el primero. Ahora queda en cola y sale en cuanto termina ese turno. Y si un turno falla de verdad, se ve el error, en lugar de confundirlo con una interrupción.
- Agentes instalados: si Claude Code está instalado a la antigua, con npm, ya no dice solo que no está instalado: explica cómo pasarse a la versión que Ctrl+IA puede usar.
- Las sesiones de otros programas cuya conversación ya no existe (Claude Code borra las antiguas pasado un tiempo) dicen «conversación no disponible» en la lista, y al abrirlas un aviso explica por qué.
- Ctrl+IA ya no se queda congelado cuando un agente envía un mensaje enorme.
- Codex, más seguro: «Permitir … con cualquier argumento durante esta sesión» solo vale en la carpeta principal de la sesión, y con git vuelve a preguntar siempre si la orden fuerza o borra, va a otra carpeta o dirección, o si hay otro repositorio dentro de la carpeta. Su ayuda avisa de que puede ejecutarse fuera del aislamiento de Codex. En las órdenes ya no se ofrece el «Permitir durante esta sesión» propio de Codex, que seguía valiendo aunque pasaras a un modo más restrictivo.
- Codex: si una conversación usaba un modelo que ya no existe, el error lo explica en español y te pide elegir otro en «Opciones de la sesión»; «Continuar aquí» ya no propone ese modelo, usa el predeterminado y te lo dice.
- La columna «Indicadores» ya no dice «Pregunta» solo porque el mensaje acabe en interrogación. Ahora dice «Pregunta por responder», «Permiso concedido», «Plan aprobado» y similares cuando el agente te preguntó o te pidió algo de verdad, y lo cambia cuando respondes. Hay indicadores nuevos para las acciones con error, los turnos detenidos y los archivos adjuntos; cada uno se puede apagar en Preferencias, Lectura de listas.
- Claude Code: ya no dice a cada momento que una herramienta externa o un conector (servidor MCP) se ha desconectado. Ctrl+IA arrancaba Claude Code de nuevo en cada mensaje, y Claude Code empezaba a responder mientras sus herramientas todavía se estaban conectando, así que las daba por desconectadas. Ahora Claude Code se queda abierto entre mensajes y, cuando arranca, espera a que sus herramientas estén listas; si alguna tarda mucho, desactiva en Preferencias, Agentes, «Esperar a que se conecten las herramientas MCP de Claude antes de cada mensaje».
- Citar en la respuesta (Ctrl+R) en el visor con formato citaba siempre el mensaje entero. Con NVDA, la selección del modo exploración no llega a la página, así que ahora se hace así: selecciona el trozo, pulsa Ctrl+C y después Ctrl+R. Si no hay nada seleccionado ni copiado, se cita el mensaje entero y Ctrl+IA lo dice. En el visor como texto y en el panel de lectura, Ctrl+R cita la selección o el párrafo del cursor, como antes.
- Codex: los permisos para usar una herramienta externa (un servidor MCP o un conector) ya se pueden responder. El diálogo dice qué herramienta es, qué hace y con qué datos, y ofrece «Permitir una vez», «Permitir durante esta sesión», «Permitir siempre» (si Codex lo admite), «Denegar» y «Cancelar». Antes salía «Codex ha pedido algo que Ctrl+IA aún no sabe mostrar; se ha rechazado».
- Codex: también se rellenan sus formularios nuevos. Si alguna vez pide algo que Ctrl+IA no sabe rellenar, te enseña lo que pide y te deja rechazarlo, en lugar de rechazarlo sin que lo veas.
- Visor de mensajes, del manual y de las novedades: Escape y Alt+C lo cierran siempre, también cuando se muestra con formato (antes Escape no hacía nada y Alt+C funcionaba solo a veces). Alt+B y Ctrl+F buscan, y Alt+I y F3 buscan el siguiente, estés donde estés del visor.

## Versión 1.3.1 - 2026-10-19

### Novedades

- Límites de uso de tus cuentas (Herramientas, Ctrl+Mayús+Y): cuánto llevas gastado del límite de 5 horas y del semanal de Codex, de Antigravity (por grupo de modelos: Gemini, y Claude y GPT, más el semanal de Gemini 4 Argon) y de Claude Code (la sesión, la semana y, si tu plan lo tiene, la semana de un modelo como Fable u Opus), con la hora a la que se reinicia cada uno. Se pregunta a todos a la vez, sin enviar mensajes ni gastar cuota, y se abre en el visor con un encabezado por agente. Si Codex no contesta, se usa el último dato que guardó, con su fecha. Todo en español, aunque el agente conteste en inglés: de Claude también se traduce qué está gastando tu límite (peticiones y sesiones de las últimas 24 horas y 7 días, subagentes, contexto largo, servidores MCP). Antigravity necesita su CLI 1.1.11 o posterior, y Claude Code, la sesión iniciada con tu suscripción.
- «Uso y límites» (Ctrl+Mayús+U) muestra también lo que preguntaste con Ctrl+Mayús+Y para el agente de la sesión.
- Al cerrar la consola de «Iniciar sesión» o de «Instalar» en Agentes instalados, Ctrl+IA vuelve a comprobar solo y te dice cómo ha quedado, por ejemplo «Codex: sesión iniciada con ana@ejemplo.com». Ya no hace falta pulsar F5.
- Lista de tareas de Claude con todos los modelos: Claude apunta los pasos de su trabajo y los marca al hacerlos, también con los modelos con los que Claude Code no lo hace por su cuenta. Se ven en Ver, «Tareas del agente» (Ctrl+Mayús+J), en el estado del turno y en Propiedades. Se quita en Preferencias, Agentes, «Dejar que Claude lleve una lista de tareas». Necesita Claude Code 2.1.233 o posterior.
- Propiedades (Alt+Intro) dice cómo va la lista de tareas del agente.

### Correcciones

- El «Modo de permisos inicial» de Configuración de los agentes no se aplicaba: con «Automático» en Claude Code, las sesiones nuevas empezaban en «Preguntar». Ahora «Nueva sesión» propone el modo de la configuración de cada agente para la carpeta elegida (Claude Code, Codex, Gemini CLI y Antigravity CLI) y la sesión empieza en él. Si cambias de carpeta, la propuesta se actualiza, salvo que hayas elegido el modo a mano.
- «Continuar aquí» con una sesión de la terminal, de la app o de Codex empieza en el modo de permisos inicial de la configuración del agente, en lugar de en el de siempre.
- Claude Code tiene también el modo «No preguntar: denegar lo dudoso», el de su configuración «No preguntar».
- Propiedades (Alt+Intro) dice el nombre del modo de permisos, por ejemplo «Automático», en lugar de su clave.
- «Lo que el agente sabe de ti» ya no repite tus ajustes, habilidades y agentes de Claude si la sesión trabaja en tu carpeta personal, aunque la ruta se escriba distinto o pase por un enlace.

## Versión 1.3.0 - 2026-10-18

### Novedades

- Agentes en otro equipo por SSH: maneja desde Windows, con tu lector de pantalla, los agentes y proyectos de un Mac o un Linux. Añade el equipo en Herramientas, «Equipos por SSH...» (nombre, usuario@dirección, puerto, sistema y, si hace falta, la ruta de cada agente y variables propias) y elígelo en el nuevo campo «Equipo» de «Nueva sesión», con la carpeta de ese equipo (por ejemplo ~/proyectos/web). Claude Code y Codex funcionan de forma estable; Gemini CLI, Antigravity CLI y los agentes ACP personalizados, de forma experimental, y Ctrl+IA lo advierte al crear la sesión.
- «Comprobar» (F5) en «Equipos por SSH» prueba la conexión sin contraseña, qué agentes hay con su versión y si Claude Code y Codex tienen la sesión iniciada, y explica cómo arreglar cada problema. «Abrir una consola en el equipo» abre una consola de Windows conectada a él para instalar los agentes e iniciar sesión.
- Ollama de otro equipo por un túnel SSH cifrado, sin abrirlo a la red (experimental): marca su casilla en el equipo y todas tus sesiones de Ollama irán a ese equipo.
- Las sesiones de otro equipo dicen en la columna Origen, por ejemplo, «Ctrl+IA en Mac del estudio (SSH)», y «Continuar aquí» las continúa en el mismo equipo.
- Antigravity en la lista de sesiones: las conversaciones de la app ahora tienen su título real, su carpeta de trabajo, su fecha y cuántos pasos dio el agente, y aparecen también las de Antigravity CLI usado en una consola (origen «Antigravity CLI»), que se pueden seguir en Ctrl+IA con «Continuar aquí» (Ctrl+Mayús+N): es la misma conversación, no una copia. Ctrl+O abre la carpeta correcta en la app.
- Los chats de la aplicación de escritorio de Ollama aparecen en la lista (origen «App de Ollama»): se leen, se buscan y se pueden continuar en Ctrl+IA con «Continuar aquí». Se ocultan en Preferencias, Historial.
- «Buscar en todas las sesiones» busca también en las conversaciones de Codex y en los chats de la app de Ollama.
- El informe de sesiones dice cuánto ocupa cada conversación de Codex.
- El manual se ha revisado entero: más corto y claro, con un capítulo nuevo sobre los agentes por SSH con ejemplos paso a paso para Claude Code, Codex, Gemini CLI, Antigravity CLI, agentes ACP y Ollama.

### Correcciones

- Al adjuntar archivos en una sesión de otro equipo, el mensaje dice cuáles no se han enviado porque están en Windows, en lugar de pasar rutas que el agente no puede abrir.
- El aviso de versión antigua de Claude Code explica cómo actualizarla: «claude update» en una consola y F5 en «Agentes instalados».
- Datos corregidos en el manual: Gemini CLI ya no admite cuentas gratuitas ni suscripciones personales desde el 18 de junio de 2026; Ctrl+IA sí contacta con su servidor de actualizaciones; menús, teclas y valores de fábrica revisados.

## Versión 1.2.1 - 2026-10-18

### Correcciones

- Buscar actualizaciones decía «Falta el componente que verifica las firmas» en la versión compilada y no dejaba actualizar. Faltaba un archivo del sistema que necesita ese componente; ya viaja con el programa y la compilación comprueba que está. Si tienes la 1.1.0 o la 1.2.0, instala esta versión a mano una vez (descarga el ZIP y extráelo encima): a partir de aquí, las actualizaciones vuelven a ser automáticas.

## Versión 1.2.0 - 2026-10-18

### Novedades

- Configuración de los agentes (Herramientas, Ctrl+Mayús+W): cambia en una lista accesible las opciones propias de Claude Code (settings.json de tu usuario, del proyecto o solo tuyo en el proyecto), Codex (config.toml), Gemini CLI y Antigravity CLI, más de 190 opciones documentadas con su explicación: modelo, permisos y reglas, modo lector de pantalla, animaciones, sandbox, variables de entorno, memoria, actualizaciones y privacidad. Espacio activa o desactiva, Intro cambia con el control adecuado y Suprimir vuelve al valor del agente. Al guardar se confirma lo arriesgado, se hace copia de seguridad y se conserva todo lo demás del archivo, incluidos los comentarios del config.toml de Codex.

## Versión 1.1.0 - 2026-10-17

### Novedades

- Los grupos ahora se abren. Organizar, «Grupos...» (Ctrl+Mayús+L) muestra la lista de grupos con cuántas sesiones tiene cada uno; Intro abre el grupo y enseña su descripción y sus sesiones. Ideal para reunir todas las consultas de una investigación.
- Dentro de un grupo abierto: Intro abre la sesión; Ctrl+N crea una sesión nueva ya dentro del grupo; Insertar añade varias sesiones a la vez con casillas y un filtro; Suprimir la saca del grupo sin borrarla; Ctrl+M la mueve a otro grupo; Ctrl+G la añade también a otro; Alt+Flecha arriba y abajo cambian su orden; F2 la renombra; Ctrl+F filtra; «Ordenar el grupo por» reordena todo de una vez.
- Cada grupo tiene descripción o notas, y un agente y una carpeta para sus sesiones nuevas (Alt+Intro para editarlos).
- Exportar un grupo entero (Ctrl+E) a un solo archivo Markdown, HTML o de texto, con índice y todas sus sesiones en orden.
- Los grupos se pueden reordenar (Alt+Flecha arriba y abajo en la lista de grupos).
- «Mover a otro grupo...» en el menú Organizar y en el menú contextual de la sesión.
- «Nueva sesión» tiene un campo «Grupo» para crear la sesión directamente dentro de un grupo.
- Nueva pestaña de Preferencias, «Lectura de listas»: elige qué se dice al ir a izquierda y derecha (número de fila, nombre de la columna, contenido) y qué columnas se leen, y en qué orden, al subir y bajar por las listas de sesiones, mensajes, grupos y sesiones de un grupo, con un ejemplo de cómo sonará una fila.
- Columna «Indicadores» en los mensajes: dice si el agente te pregunta algo, cuántos bloques de código, enlaces o archivos cambiados trae, y si el mensaje es largo. Va en el texto de la fila, así que también sale en braille. Cada indicador se activa por separado.
- Nueva columna opcional «Número» en los mensajes.
- Fechas relativas («hace 5 minutos») o completas («17/10/2026 21:39»), a elegir.
- Se puede quitar «Favorita:» del título y «respuesta nueva» del estado.
- Dar una sesión por terminada (Ctrl+D, menú Organizar): deja de salir en «Te necesitan», «Trabajando» y «Con respuestas sin leer», su estado dice «Terminada» y todas están juntas en Ver, Mostrar, «Terminadas». Otro Ctrl+D la vuelve a activar, y se activa sola si vuelve a tener actividad.
- «Respuesta nueva» también en las sesiones de la terminal, la app de Claude, Cowork, Codex y la app de Antigravity: se marcan cuando el agente responde después de la última vez que las abriste en Ctrl+IA.
- «Marcar como leída o no leída» (Ctrl+Q) y «Marcar todas como leídas» (Ctrl+Mayús+Q), en el menú Organizar y en el menú contextual de la sesión.
- Informe de sesiones (Herramientas, Ctrl+I): todas las sesiones con lo que ocupan, cuántos mensajes, respuestas y acciones de herramientas tienen, y los totales por agente y por origen. Se puede filtrar, ordenar por tamaño, abrir u ocultar una sesión desde él, verlo como documento con un encabezado por sesión y guardarlo en Markdown o CSV, siempre sin el texto de los mensajes.
- Llevar sesiones a otro equipo: Archivo, «Exportar varias sesiones a un archivo...» guarda la sesión elegida, las de la lista, las de un grupo o todas en un .zip, con o sin la salida de las herramientas; «Importar sesiones...» las trae como sesiones de solo lectura, marcadas con el equipo de origen, en las que se puede leer y buscar. Importar otra vez no duplica: actualiza y añade. «Borrar las sesiones importadas...» quita las copias.
- Vistas nuevas en Ver, Mostrar: «Terminadas» e «Importadas de otro equipo».
- Ollama: conversa con los modelos de IA que tienes descargados en tu equipo, gratis y sin que tus mensajes salgan de él. Es solo chat (el modelo no lee ni cambia archivos), y así lo dice el «Modo de permisos». Elige el modelo entre los instalados, con su tamaño; la respuesta se lee mientras se escribe, Detener corta al momento aunque el modelo se esté cargando, los archivos de texto adjuntos se copian en el mensaje y las imágenes llegan a los modelos con visión. Ctrl+IA guarda estas conversaciones para seguirlas otro día. Avisos claros si Ollama no está abierto, si no hay modelos o si el modelo se está cargando.
- Preferencias, Agentes: «Mostrar Ollama (modelos locales, solo chat)» y «Dirección del servidor de Ollama», por si lo tienes en otro equipo.
- Otros agentes ACP conocidos: OpenCode, Goose, GitHub Copilot CLI, Qwen Code, Kilo Code y Cline. Se activan en Preferencias, Agentes, «Mostrar otros agentes ACP conocidos». Como el resto, no reciben las claves de API de pago que tengas en Windows.
- El manual explica cómo añadir cualquier otro agente ACP como «Agente ACP personalizado».
- Nada existe solo como voz: cada aviso va también a la línea braille, aunque hayas silenciado la voz de Ctrl+IA o elegido «Silencio», y queda escrito en una cuarta parte de la barra de estado. Tres opciones nuevas en Preferencias, Anuncios, para decidirlo.
- Esquema de sonidos: sonidos nuevos para cuando el agente usa una herramienta, cuando cambia un archivo, cuando se detiene y cuando termina otra sesión que no tienes abierta. Nueva pestaña de Preferencias, «Sonidos», con el volumen, el esquema y una casilla por sonido. F9 activa o desactiva todos. Herramientas, «Sonidos...», permite oír cada uno y activarlo o desactivarlo.
- Tus propios sonidos: copia tus WAV a la carpeta de sonidos propios, con el nombre de cada evento, y elige el esquema «Propio». Los que falten suenan como siempre.
- Menús contextuales en todas las listas: también en los resultados de «Buscar en todas las sesiones», en el selector de comandos y repositorios, en el cuadro de columnas, en la lista de secciones del visor y en el nuevo cuadro de sonidos. Se abren con la tecla Aplicaciones, Mayús+F10 o el botón derecho.
- Panel de lectura (F8): el mensaje elegido aparece completo debajo de la lista de mensajes, sin abrirlo y sin que se diga nada más al moverte. Alt+5 lleva a él y F6 lo recorre.
- Ocultar la lista de sesiones (F7) para que la conversación ocupe toda la ventana. Alt+1 la vuelve a mostrar.
- Ver, «Distribución de la ventana»: reparte la altura de forma equilibrada, con más espacio para los mensajes o con más espacio para el cuadro de respuesta. En ventanas pequeñas, los mensajes ya no quedan más bajos que el cuadro de respuesta.
- La zona con el foco se ve resaltada: su título sale en negrita y con los colores de selección de Windows.
- Tema de colores claro, oscuro o como Windows (Preferencias, pestaña Ventana). Toda la aplicación cambia a la vez, también el manual y los mensajes con formato; con alto contraste mandan siempre los colores de Windows.

- «Volver a enviar...» en el menú contextual de tus mensajes: tras confirmarlo, manda otra vez ese mensaje al agente (si está trabajando, queda en cola). Útil cuando un turno falla por el límite de uso o un corte de conexión; Ctrl+IA te lo recuerda al fallar.
- Permisos de la sesión (Sesión, Ctrl+Mayús+M): el modo de permisos explicado y la lista de lo que has permitido «durante esta sesión», con palabras, la regla exacta y la fecha. Quita una regla (Suprimir) o todas, siempre tras confirmarlo.
- Codex: «Permitir «git commit» con cualquier argumento durante esta sesión». Ctrl+IA guarda la regla y contesta por ti a las órdenes que empiezan así, avisándote; nunca a órdenes compuestas, fuera de la carpeta, con más permisos o de intérpretes como cmd, PowerShell o python. Quitarla surte efecto en la siguiente petición.
- Sesiones largas: al abrirlas se cargan solo los últimos 100 mensajes (Preferencias, Mensajes, a elegir entre 50, 100, 250, 500 o todos). La primera fila, Alt+Inicio o el menú Ver cargan los anteriores sin perder tu posición. Buscar, las listas de la sesión y exportar usan siempre la conversación entera.
- Si el agente escribe algo justo antes de preguntarte, como un borrador para revisar, el diálogo de respuesta lo muestra entero en «Lo que el agente escribió antes», y el foco empieza ahí.
- En las preguntas con opciones descritas, un campo debajo de la lista muestra la opción elegida con toda su descripción, aunque sea larga.
- Añadir al turno en marcha (Ctrl+Alt+Intro, con Codex): tu mensaje llega a lo que el agente está haciendo sin detenerlo. Si no puede entregarse, nunca se envía por su cuenta: vuelve al cuadro de respuesta con la explicación. Estado del turno dice si falta la confirmación.
- Con Codex, «Enviar ahora» detiene el turno y después envía tu mensaje, igual que con los demás agentes.
- Estado del turno (Ctrl+Mayús+T) dice en qué tarea de su lista va el agente («Tarea 3 de 7: ejecutar las pruebas. 2 hechas»), y una opción nueva de Preferencias, Anuncios, avisa de cada tarea que termina («Hecha: ejecutar las pruebas. 4 de 7»).
- Al terminar un turno con una revisión de código, Ctrl+IA lo resume: «Revisión: 5 hallazgos, 2 confirmados».
- Las listas de la sesión (Ctrl+Mayús+I) tienen tres páginas nuevas, de Ctrl+1 a Ctrl+7: «Tareas del agente», «Subagentes» y «Hallazgos de la revisión».
- Tareas del agente (Ver, «Tareas del agente...», Ctrl+Mayús+J): la lista de pasos del agente con su estado, pendiente, en curso o hecha, y cuántos lleva hechos en la etiqueta. Sale de la lista de tareas de Claude Code y del plan de Codex y de Gemini, también en las sesiones de otros programas.
- Subagentes: qué encargó el agente a cada subagente, cuántos pasos dio y cuántos archivos editó; Intro muestra lo que hizo paso a paso, los archivos que cambió, las órdenes que ejecutó y su respuesta. Los archivos que cambian los subagentes ya cuentan en «Archivos cambiados».
- Hallazgos de una revisión de código, uno por fila con su archivo, línea, categoría, veredicto y, tras corregirlos, su resultado. Ctrl+R prepara en el cuadro de respuesta un mensaje para que el agente corrija el hallazgo, sin enviarlo nunca solo.
- Archivos cambiados: Ctrl+L lee el archivo entero tal como está ahora, Ctrl+O lo abre con su programa, Ctrl+E en el editor de código, Ctrl+Mayús+E lo muestra en el Explorador y Ctrl+Mayús+C copia su contenido. Si se reescribió sin cambios, la fila dice «sin líneas cambiadas».
- Enlaces: también salen las rutas de archivos de tu equipo que menciona el agente (C:\..., rutas de red, file:///..., rutas de la carpeta de la sesión con su número de línea), con si existen; Intro los abre, y también se pueden leer, abrir en el editor o mostrar en el Explorador. Por seguridad, nunca se abren programas, scripts ni accesos directos.
- Órdenes ejecutadas: nueva columna «Resultado» (correcta, con error o sin resultado); Intro muestra la orden con su salida y Ctrl+Mayús+C copia la salida.
- Codex: en sus conversaciones guardadas, las listas de la sesión ya muestran los archivos cambiados con sus diferencias, las órdenes con su salida y si fallaron, y los subagentes.

- Actualizaciones automáticas desde xebolax.synology.me/CtrlIA: Ctrl+IA comprueba al abrirse y una vez al día si hay una versión nueva, te muestra sus novedades y la instala con «Actualizar ahora», sin tocar tus datos y volviendo a la versión anterior si algo sale mal. Solo acepta versiones firmadas por el autor. Se puede desactivar en Preferencias, General, «Buscar actualizaciones automáticamente».
- Ayuda, «Invítame a un café si te gusta mi trabajo»: abre la página de donaciones de PayPal.
- «Acerca de» dice el autor: Héctor J. Benítez Corredera.

### Correcciones

- En las sesiones de la terminal y la app de Claude, «Archivos cambiados» no enseñaba los cambios de cada archivo; ahora sí, y también cuenta los cuadernos editados (NotebookEdit).
- En las conversaciones guardadas de Codex, las órdenes de la página «Órdenes ejecutadas» ya no llevan pegada su salida.

- Con Claude Code, el texto que Claude escribe justo antes de una pregunta podía no llegar a la lista de mensajes; ahora se rescata y se guarda como un mensaje más.
- Los campos de una línea con un texto largo, como el título de «Continuar aquí», se abrían mostrando solo el final; ahora se ve el principio, con todo el texto seleccionado para escribir encima.
- El manual y los mensajes con formato ya no se ven oscuros dentro de una ventana clara cuando Windows está en modo oscuro.
- Si la lista mostraba un grupo y lo renombras, la vista sigue al nombre nuevo; si lo borras, vuelve a «Todas» y te lo dice, en lugar de quedarse vacía.
- Al cambiar a una vista sin sesiones, Ctrl+IA dice «No hay sesiones en esta vista» en lugar de solo «0 sesiones».
- Si enviabas un mensaje justo al abrir una sesión y el equipo iba cargado, el mensaje podía desaparecer de la lista hasta volver a abrirla. Ya no ocurre.
- Los registros ya no hacen parecer un fallo lo que no lo es: si el programa cierra con normalidad, los avisos de Windows que se gestionaron solos quedan marcados como no mortales, y un agente que cierra Ctrl+IA aparece como «detenido por Ctrl+IA».

## Versión 1.0.0 - 2026-10-17

### Novedades

- Primera versión de Ctrl+IA: una sola ventana, accesible con NVDA y JAWS, para conversar con los agentes de IA de programación que tienes instalados en tu equipo.
- Agentes compatibles: Claude Code (Anthropic), Codex (OpenAI), Antigravity CLI (Google) y Gemini CLI. Opcionalmente, los adaptadores ACP de Claude Code, Codex y Antigravity, que se activan en Preferencias, Agentes.
- Agentes ACP personalizados: añade cualquier otro agente compatible con ACP desde Herramientas, «Agentes ACP personalizados...».
- Ctrl+IA usa la suscripción que ya tienes con cada servicio y se niega a usar claves de API de Claude, que se cobran por uso, salvo que lo permitas en Preferencias.
- Ventana principal en tres zonas: lista de sesiones, lista de mensajes y cuadro de respuesta. F6 y Mayús+F6 las recorren; Alt+1, Alt+2 y Alt+3 llevan a cada una; Alt+4 lee la barra de estado.
- Lista de sesiones única con las sesiones creadas en Ctrl+IA y las de otros programas: Claude Code en la terminal, la app de escritorio de Claude, Cowork, Codex y la app de Antigravity.
- Vistas del menú Ver, Mostrar (te necesitan, trabajando, sin leer, favoritas, por agente, por origen, ocultas, archivadas y por grupo), seis formas de ordenar, filtro por texto con Ctrl+F y columnas a tu gusto.
- Navegación por columnas en todas las listas: flechas izquierda y derecha, y Ctrl+1 a Ctrl+9 para leer una columna concreta.
- Diálogo «Nueva sesión» (Ctrl+N) con agente, carpeta, copia aislada en una rama nueva de git, título, modelo, esfuerzo, modo de permisos y primer mensaje. Con Codex y Antigravity, la lista de modelos se pide al propio agente.
- «Continuar aquí» (Ctrl+Mayús+N): sigue en Ctrl+IA una sesión de otro programa con toda su conversación.
- Clonar un repositorio de GitHub y empezar a trabajar en él desde el menú Archivo.
- Envío con Ctrl+Intro, cola de mensajes mientras el agente trabaja, «Enviar ahora» (Ctrl+Mayús+Intro) y «Detener» (Ctrl+.).
- Adjuntar archivos e imágenes, y pegar imágenes con Ctrl+V en el cuadro de respuesta.
- Comandos de barra y habilidades del agente (Ctrl+/) y plantillas de mensajes reutilizables (Ctrl+Mayús+P).
- Diálogo para responder al agente (Ctrl+Mayús+A): permisos, preguntas y planes, con el detalle completo y una opción inofensiva en Intro.
- Antigravity: aviso de cada acción denegada con la opción «Permitir y reintentar», y un editor de sus reglas de permisos.
- Opciones de cada sesión (Ctrl+Mayús+O): título, modelo, esfuerzo y modo de permisos.
- Estado del turno (Ctrl+Mayús+T), uso de contexto y límites de la cuenta (Ctrl+Mayús+U), y avisos cuando se acercan al máximo.
- Lectura de mensajes con formato o como texto plano, búsqueda dentro de la sesión (Ctrl+F, F3) y en todas las sesiones (Ctrl+Mayús+S).
- Listas de la sesión: bloques de código, enlaces, archivos cambiados con sus diferencias y órdenes ejecutadas, para ver, copiar o guardar.
- Seguimiento en vivo de las sesiones de Claude Code abiertas en otros programas.
- «Lo que el agente sabe de ti» (Ctrl+Mayús+K): los archivos de instrucciones y memoria de cada agente, para verlos, editarlos o crearlos.
- Organización: renombrar, favoritas, ocultar, borrar, grupos y exportar a Markdown, HTML o texto.
- Avisos por voz configurables, sonidos, notificaciones de Windows, historial de avisos (Ctrl+Mayús+H) y silencio rápido de la voz (Ctrl+Mayús+V).
- «Agentes instalados»: comprueba qué agentes tienes, los instala y abre su inicio de sesión.
- Preferencias en seis pestañas: General, Agentes, Historial, Mensajes, Anuncios y Avanzado.
- Ayuda en el programa: manual (F1), lista de teclas (Ctrl+F1), novedades y ayuda del control enfocado (Ctrl+H).
- Versión portable: todo se guarda en la carpeta data, junto al programa. Incluye un paquete de diagnóstico sin datos personales para informar de fallos.
