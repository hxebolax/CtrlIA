# Manual de Ctrl+IA

Este manual explica todo lo que hace Ctrl+IA. Cada apartado es un encabezado: con NVDA o JAWS, H salta al siguiente y 2 o 3 a los de ese nivel. Todo se maneja con el teclado. F1 abre este manual y Ctrl+F1 la lista completa de teclas, ventana por ventana.

## Qué es Ctrl+IA

Ctrl+IA es un programa para Windows que reúne en una sola ventana accesible tus conversaciones con los agentes de IA de programación. Un agente, además de conversar, lee los archivos de tu proyecto, los modifica y ejecuta órdenes. Ctrl+IA trabaja con:

- **Claude Code** (Anthropic), con tu suscripción de Claude.
- **Codex** (OpenAI), con tu cuenta de ChatGPT.
- **Antigravity** (Google), con tu suscripción de Google: su consola (agy), su editor (Antigravity IDE) y su app de escritorio.
- **Gemini CLI** (Google). Desde junio de 2026 solo funciona con licencia de empresa o clave de API de pago; con suscripción personal, usa Antigravity.
- **Ollama**, para conversar con modelos que se ejecutan en tu equipo (solo chat, gratis y sin cuenta).
- **Otros agentes ACP** (OpenCode, Goose, GitHub Copilot CLI, Qwen Code, Kilo Code, Cline y los que añadas tú). Vienen ocultos; se muestran en Preferencias, Agentes.

Ctrl+IA no es un modelo de IA: abre el programa oficial de cada agente con la cuenta en la que ya iniciaste sesión. Por eso necesitas el agente instalado y con sesión iniciada, y lo que hagas se cobra como si usaras el agente directamente, normalmente dentro de tu suscripción. Ctrl+IA no cobra nada y nunca usa por su cuenta claves de API de pago.

Los agentes pueden trabajar en este equipo o en un Mac o un Linux al que entras por SSH (ver «Agentes en otro equipo por SSH»).

## Instalar y empezar

### Requisitos

- Windows 10 u 11. Un lector de pantalla si lo usas (pensado para NVDA y JAWS); sin él, Ctrl+IA habla con su propia voz.
- Al menos un agente instalado con sesión iniciada (Ctrl+IA ayuda a instalarlos) e Internet (salvo con Ollama).
- Para algunas funciones: **Git para Windows** (deshacer cambios y copia aislada), **gh**, la herramienta de GitHub, con `gh auth login` hecho una vez (clonar y PR), **Node.js** (instalar Codex o Gemini CLI y los adaptadores ACP) y el **cliente OpenSSH** de Windows, que ya viene instalado (equipos por SSH).

### Instalar, desinstalar y abrir

Ctrl+IA es portable: no se instala ni toca el registro de Windows, y todo lo que guarda está en la carpeta data, junto al programa. Extrae el ZIP en la carpeta que quieras y abre CtrlIA.exe (si sale SmartScreen, «Más información» y «Ejecutar de todas formas»). Para desinstalarlo, borra su carpeta; copia antes la carpeta data si quieres conservar tus sesiones (lo que cambiaste en la configuración de los agentes se queda en sus carpetas).

- Mientras arranca dice «Iniciando Ctrl+IA» (se quita en Preferencias, General). Si no encuentra ningún agente, te avisa de que lo instales desde Herramientas, «Agentes instalados».
- Solo puede haber un Ctrl+IA abierto por cada carpeta del programa: si lo abres otra vez, la ventana que ya tenías vuelve al frente, aunque estuviera en la bandeja.
- Si abres CtrlIA.exe pasándole una carpeta (por ejemplo, desde un acceso directo), se abre «Nueva sesión» con esa carpeta, también si Ctrl+IA ya estaba abierto.
- La ventana recuerda su posición, su tamaño y si estaba maximizada.

### Actualizar

Ctrl+IA busca versiones nuevas una vez al día (la primera, un minuto después de abrirse) en dos sitios: su servidor y GitHub. Si uno no responde usa el otro, y te ofrece la más reciente que encuentre. Se desactiva en Preferencias, General, y se busca cuando quieras con Ayuda, «Buscar actualizaciones...». Si hay una, un cuadro te dice tu versión, la nueva y, si se han publicado, sus «Novedades» (Alt+N), con tres botones:

- «Actualizar ahora» (Alt+A): la descarga y comprueba que está firmada por el autor y que el archivo está intacto. El progreso se lee en «Estado» (Alt+E) y se dice por voz. Durante la descarga, «Más tarde» pasa a ser «Cancelar» (lo descargado se conserva); si falla, «Reintentar» (Alt+R) sigue donde se quedó, también desde la otra fuente. Hace falta libre unas cuatro veces el tamaño del paquete.
- «Omitir esta versión» (Alt+V): la búsqueda automática no vuelve a avisarte de ella; la manual sí.
- «Más tarde» (Alt+M o Escape).

Cuando la descarga termina, Ctrl+IA te pregunta una sola vez si quieres **reiniciar para aplicar la actualización** y, si hay sesiones con el agente trabajando, cuántas se detendrán (sus conversaciones se conservan). Con «Reiniciar ahora» (Intro), Ctrl+IA se cierra sin más preguntas, se instala y se vuelve a abrir solo, diciendo «Aplicación actualizada a la versión...». Con «Más tarde» (Escape) no se instala nada: cuando quieras, Ayuda, «Buscar actualizaciones...» y «Actualizar ahora» aprovechan lo ya descargado. Si la versión nueva no arrancara, vuelve sola a la anterior y ya no te la ofrece.

La actualización sustituye los archivos del programa (también las skills de ejemplo), nunca tus datos (configuración, sesiones, registros, tus skills). Si prefieres actualizar a mano, descarga el ZIP nuevo de GitHub y extráelo encima de la carpeta del programa. Tras actualizar, Ctrl+IA te muestra una vez las novedades.

### Los primeros minutos

1. **Comprueba los agentes:** Herramientas, «Agentes instalados...» (ver «Agentes instalados»). Si uno no está, «Instalar» (Alt+I); si no tiene sesión, «Iniciar sesión» (Alt+S).
2. **Crea una sesión:** Ctrl+N. Elige el agente (Alt+A) y la carpeta de tu proyecto (Alt+B o «Examinar», Alt+X), escribe si quieres un primer mensaje (Alt+N) y pulsa «Crear la sesión» (Alt+S).
3. **Escribe y envía:** en el cuadro de respuesta (Alt+3), Intro empieza una línea nueva y Ctrl+Intro envía.
4. **Lee la respuesta:** Ctrl+IA la lee al llegar. Alt+2 va a la lista de mensajes; Intro abre el mensaje en el visor.
5. **Responde a un permiso:** cuando el agente te necesita suena un aviso y te lo dice. Pulsa Ctrl+Mayús+A, lee el «Detalle» y elige un botón. Intro sin moverte elige siempre lo inofensivo.

Si no sabes qué hace un control, pulsa Ctrl+H sobre él.

## La ventana principal

### Zonas y movimiento

La ventana no tiene botones: todo está en los menús, en los menús contextuales y en atajos. Tiene tres zonas, de arriba abajo:

- **Sesiones** (Alt+1): tus conversaciones. Su nombre dice cuántas hay y la vista, por ejemplo «Sesiones (12, vista: Todas)».
- **Mensajes** (Alt+2): lo dicho en la sesión abierta.
- **Respuesta** (Alt+3): el cuadro donde escribes.

Si lo activas, entre los mensajes y la respuesta está el **panel de lectura** (Alt+5). F6 y Mayús+F6 recorren las zonas visibles. Escape, desde los mensajes, el panel o la respuesta, vuelve a la lista de sesiones (Retroceso en los mensajes hace lo mismo). Alt+6 vuelve al visor del último mensaje abierto.

### Barra de estado y título

Alt+4 lee las tres primeras partes de la barra de estado: la sesión y su estado (mientras trabaja, lo que hace; si te espera, «Te necesita (Control+Mayúsculas+A)»; sin sesión, «Ninguna sesión abierta»), el agente y el modelo (o cuántos adjuntos tienes preparados) y el contexto usado. La cuarta parte muestra el último aviso, para leerlo con la orden de barra de estado de tu lector (Insertar+Fin en NVDA). El título de la ventana dice la sesión y su agente.

Con «Palabras curiosas en la barra de estado» (Preferencias, Anuncios), mientras el agente escribe la barra dice cosas como «Claude: Cavilando…», que cambian cada pocos segundos. Es solo por gusto: nunca se dicen por voz y Alt+4 sigue diciendo «está escribiendo».

### Distribución de la ventana

Ver, «Distribución de la ventana», cambia lo que se ve, sin cambiar lo que dice el lector:

- «Mostrar la lista de sesiones» (F7): ocúltala para que la conversación ocupe toda la ventana; Alt+1 o Escape la vuelven a mostrar.
- «Panel de lectura» (F8): debajo de los mensajes, el mensaje elegido completo, como en un programa de correo. Su nombre dice cuál es («Lectura: mensaje 12 de 40, Claude»). Se recorre con las flechas, se copia con Ctrl+C y se actualiza sin mover tu cursor si el agente sigue escribiendo.
- «Reparto equilibrado», «Más espacio para los mensajes» y «Más espacio para el cuadro de respuesta».
- El divisor: una franja entre los mensajes y la respuesta que se arrastra con el ratón (doble clic la devuelve a su sitio). Con el teclado, «Un poco más de espacio para los mensajes» (Ctrl+Alt+Mayús+Abajo) y «… para la respuesta» (Ctrl+Alt+Mayús+Arriba) la mueven de 5 en 5 y dicen cuánto ocupa la respuesta. Nunca recibe el foco. Elegir uno de los tres repartos la devuelve a su sitio.
- «Resaltar la zona con el foco»: el título de la zona donde estás se ve en negrita y con colores de selección.

Todo se recuerda y también está en Preferencias, Ventana.

### Los menús

Alt o F10 llevan a la barra de menús. Cada orden dice su atajo.

- **Archivo:** nueva sesión, continuar aquí, nueva sesión a partir del mensaje, clonar de GitHub, exportar la sesión o varias, importar y borrar las importadas, abrir la carpeta en el Explorador (Ctrl+Mayús+E), en Antigravity IDE (Ctrl+Mayús+G; si no lo tienes, su app) o en el editor de código, abrir en la app del agente (Ctrl+O) y salir.
- **Sesión:** enviar, enviar ahora, añadir al turno, detener, responder al agente, comandos, adjuntar, plantillas, enviar la respuesta a otra sesión, la conversación automática entre sesiones, citar, deshacer y rehacer cambios de archivos, opciones y permisos de la sesión, estado del turno, uso y límites, propiedades, «PR y comprobaciones», herramientas del agente y lo que el agente sabe de ti.
- **Ver:** ir a cada zona, leer la barra de estado, distribución, vistas («Mostrar»), «Ordenar por», columnas, filtrar o buscar, buscar en todas las sesiones, cargar mensajes, favoritos, listas de la sesión, tareas del agente, copiar el último bloque de código, mostrar las herramientas, actualizar, repetir el último aviso, historial de avisos y silenciar la voz.
- **Organizar:** renombrar, favorita, ocultar y volver a mostrar, borrar, dar por terminada, leída o no leída, grupos.
- **Herramientas:** agentes instalados, configuración de los agentes, reglas de Antigravity, agentes ACP personalizados, gestor de skills, equipos por SSH, límites de uso, informe de sesiones, preferencias, sonidos, sonidos activados (F9), carpetas de datos y de registros, y paquete de diagnóstico.
- **Ayuda:** manual (F1), lista de teclas (Ctrl+F1), novedades de la versión, ayuda del control (Ctrl+H), buscar actualizaciones, «Invítame a un café si te gusta mi trabajo» (abre la página de donaciones del autor) y «Acerca de Ctrl+IA» (versión, autor, versiones de Python y wxPython, sistema y carpeta de datos, en un texto que se copia).

**A qué sesión se aplica cada orden.** Las de organizar, exportar, abrir o propiedades actúan sobre la sesión seleccionada si estás en la lista de sesiones, y sobre la abierta en las demás zonas. Uso, permisos de la sesión y lo que el agente sabe de ti usan la abierta o, si no hay, la seleccionada. Enviar, detener, opciones de la sesión, comandos, citar, reenviar, estado del turno y herramientas del agente necesitan una sesión abierta.

Opciones y permisos de la sesión, herramientas del agente, deshacer cambios, añadir al turno, volver a enviar y la conversación entre sesiones solo funcionan en sesiones creadas en Ctrl+IA; con las demás, usa antes «Continuar aquí».

En todas las listas, también las de los diálogos, la tecla Aplicaciones (o Mayús+F10) abre el menú contextual del elemento; en los cuadros de texto, el de edición. En los cuadros, Intro pulsa el botón predeterminado y Escape equivale a «Cancelar» o «Cerrar» (con un desplegable abierto, solo lo cierra); en los que tienen «Aceptar» no se guarda nada hasta pulsarlo, y en las preguntas de borrar o quitar, «No» es lo predeterminado. Los textos largos (Acerca de, herramientas del agente, la PR) se abren en un cuadro con «Copiar al portapapeles» (Alt+C) y «Cerrar» (Alt+E o Escape).

### Bandeja y salir

Con «Minimizar a la bandeja del sistema» (Preferencias, General), al minimizar queda un icono junto al reloj y los agentes siguen trabajando; Ctrl+IA dice «Ctrl+IA sigue en la bandeja del sistema.». Para volver: Windows+B, ve al icono y pulsa Intro (o un clic); su menú tiene «Mostrar la ventana» y «Salir».

Si hay agentes trabajando, al salir Ctrl+IA te dice cuántos se detendrán y te pregunta. Si no hay ninguno, cerrar la ventana (Alt+F4) pregunta «¿Quieres salir de Ctrl+IA?» si tienes «Confirmar antes de salir» (Preferencias, General); Archivo, «Salir» y «Salir» de la bandeja no lo preguntan.

## La lista de sesiones

Una **sesión** es una conversación con un agente en una carpeta de trabajo; el agente recuerda todo lo dicho aunque vuelvas otro día. En la lista están las sesiones creadas en Ctrl+IA y también las de otros programas (la terminal, la app de Claude, Codex, Antigravity, Ollama) de este equipo y de tus equipos por SSH. Esas solo se leen: para seguirlas, «Continuar aquí».

### Columnas

Al subir y bajar, el lector lee las columnas visibles en su orden. Con Flecha derecha e izquierda las oyes una a una, Ctrl+1 a Ctrl+9 y Ctrl+0 leen esa columna, y Ctrl+Mayús+Inicio y Ctrl+Mayús+Fin la primera y la última (vale en todas las listas de Ctrl+IA, salvo en las listas de la sesión, donde Ctrl+1 a Ctrl+0 cambian de página).

- **Título** («Favorita:» delante si lo es), **Estado** (con «respuesta nueva» si hay respuesta sin leer), **Agente**, **Carpeta** y **Última actividad** («hace 5 minutos» o la fecha completa, según Preferencias): las de fábrica.
- Además: **Origen** (Ctrl+IA, Terminal, App de Claude, Cowork, Codex, App Antigravity, Antigravity CLI, App de Ollama, Otra, «Importada de» un equipo o «… en Mac del estudio (SSH)»), **Sin leer**, **Grupos**, **Último mensaje**, **Creada**, **Modelo** y **Equipo** («Este equipo (Windows)» o el equipo por SSH).

Ver, «Columnas de la lista...»: en «Columnas visibles» (Alt+V), Espacio marca o desmarca, y Alt+Flecha arriba o abajo (o «Subir», Alt+S, y «Bajar», Alt+B) cambian el orden. «Restablecer» (Alt+R) las marca todas. Tiene que quedar al menos una. Preferencias, Lectura de listas, ofrece las mismas columnas salvo Equipo, que solo se añade desde aquí.

### Estados

«Inactiva», «Trabajando», «Te necesita» (espera tu respuesta a un permiso, una pregunta o un plan), «Trabajo en segundo plano» (respondió, pero sigue con algo por detrás), «Error» (falló el último turno), «Cerrada», «Terminada» (la diste por terminada) y, rara vez, «Estado desconocido».

### Vistas, orden y filtro

Ver, «Mostrar», elige qué sesiones salen; Ctrl+IA recuerda la vista y dice cuántas hay:

- «Todas»; «Te necesitan o trabajando», «Te necesitan», «Trabajando» y «Con respuestas sin leer» (sin las terminadas).
- «Creadas en Ctrl+IA», «Favoritas» y «Sin grupo».
- «Solo Claude», «Solo Codex», «Solo Antigravity» y «Solo Gemini» (Ollama y los demás ACP no tienen vista propia).
- «Abiertas en la terminal» y «De la app de Claude (escritorio y Cowork)».
- «Importadas de otro equipo», «Solo de este equipo (Windows)» y «De otros equipos por SSH».
- «Terminadas», «Ocultas» y «Archivadas» (las que archivaste en la app de Claude). Las ocultas y archivadas solo salen en su vista, salvo que lo cambies en Preferencias, Historial.
- «Grupo: …», una por grupo.

Ver, «Ordenar por»: «Más recientes primero» (de fábrica), «Más antiguas primero», «Por estado (las que te necesitan primero)», «Por título», «Por carpeta» y «Por agente».

**Filtrar:** en la lista, Ctrl+F, escribe y pulsa Intro. Quedan las sesiones cuyo título, carpeta, último mensaje, agente, grupos o equipo contienen todas las palabras, sin importar mayúsculas ni tildes. Escape en la lista, o vaciar el cuadro, quita el filtro; no se guarda al cerrar.

### Cómo se actualiza

Ctrl+IA relee las sesiones cada pocos segundos (las de los equipos por SSH, cada medio minuto). Mientras recorres la lista, las filas no cambian de sitio: se actualizan donde están y las nuevas van al final. F5 reordena y relee ya los equipos por SSH.

### Abrir una sesión y respuestas nuevas

Intro abre la sesión: Ctrl+IA dice cuántos mensajes tiene y deja el foco en el último (saltando los avisos automáticos «Permitido…» del final). Sus respuestas sin leer se dan por leídas. Con «Abrir la última sesión al iniciar» (Preferencias, General, activada) se abre la que tenías al salir, si está en la vista elegida.

Una sesión tiene «respuesta nueva» cuando el agente contestó después de la última vez que la abriste en Ctrl+IA, también si es de otro programa (Ctrl+IA no sabe si ya la leíste allí; la primera vez que ve una sesión ajena la da por leída). Ctrl+Q la marca como leída o, si no tenía nada nuevo, como no leída; Ctrl+Mayús+Q marca todas como leídas. Esta marca solo existe en Ctrl+IA (las importadas no la tienen).

### Menú contextual de una sesión

Según el caso: «Abrir», «Continuar aquí» (en las de otros programas y las importadas), «Abrir en la app del agente», abrir la carpeta en Antigravity IDE o en el Explorador, «Renombrar», favorita, «Dar por terminada» o «Volver a activar», leída o no leída, añadir, quitar o mover de grupo, ocultar o volver a mostrar, «Borrar para siempre» (en las importadas, «Borrar la copia importada»), «Exportar», «Copiar el título» (Ctrl+C), «Propiedades» y, en las de este equipo con carpeta, «PR y comprobaciones».

## Crear y continuar sesiones

### Nueva sesión

Ctrl+N. Los campos, en orden:

- «Agente» (Alt+A). Los no instalados dicen «(no instalado)». El elegido pasa a ser el predeterminado.
- «Equipo» (Alt+Q): solo si tienes equipos por SSH.
- «Carpeta de trabajo» (Alt+B): escríbela o elige una reciente con las flechas (se propone la última, la del grupo si la creas desde un grupo o la de Preferencias si no hay ninguna). «Examinar...» (Alt+X).
- «Trabajar en una copia aislada (rama de git nueva)» (Alt+I) y «Nombre de la rama» (Alt+R): ver el apartado siguiente.
- «Grupo» (Alt+G) y «Título (opcional)» (Alt+T). Sin título, se usa el principio del primer mensaje o el nombre de la carpeta.
- «Modelo» (Alt+M): «Predeterminado de…» deja decidir al agente. Codex, Antigravity y Ollama traen su lista real, que llega sola; los ACP, la que anuncian al conversar.
- «Esfuerzo» (Alt+E): cuánto piensa antes de responder; más esfuerzo, respuestas más cuidadas pero más lentas y con más gasto.
- «Modo de permisos» (Alt+P): qué puede hacer sin preguntarte (Ctrl+H explica el elegido). Se propone el modo inicial de la configuración del agente para esa carpeta (ver «Configuración de los agentes»), y cambia con la carpeta salvo que lo hayas elegido a mano.
- «Estilo de las respuestas» (Alt+L), solo con Claude Code: «Conciso» (breve y directo, cómodo de escuchar), «Proactivo», «Explicativo», «Aprendizaje» o los tuyos.
- «Categoría de skills» (Alt+D), «Skills del catálogo para la sesión» (Alt+O) y «Ver y gestionar el catálogo de skills...» (Alt+V): ver «Skills».
- «Primer mensaje (opcional)» (Alt+N; Intro es línea nueva) y «Crear la sesión» (Alt+S).

Un desplegable con una sola opción sale desactivado. Por SSH, las flechas en «Carpeta» ofrecen las ya usadas en ese equipo.

Ctrl+IA recuerda, para cada agente, el modelo, el esfuerzo y el estilo que elegiste la última vez, aquí o en «Opciones de la sesión». Si falta algo (la carpeta no existe, el agente no está instalado), te lo dice y te lleva al campo. Con un primer mensaje, se envía y vas a los mensajes; sin él, vas al cuadro de respuesta.

### Copia aislada en una rama nueva

Con «Trabajar en una copia aislada», Ctrl+IA crea una segunda copia de tu repositorio de git con una rama propia (se propone «ctrlia/fecha-hora»; admite letras sin tilde, números, puntos, guiones, guiones bajos y barras) dentro de tu proyecto, en .ctrlia\worktrees, y el agente trabaja allí sin tocar tu copia habitual. Sirve para cambios grandes o para tener dos agentes en el mismo proyecto. Ctrl+IA no une los cambios a tu rama: hazlo tú o pídeselo al agente. Necesita Git y que la carpeta sea un repositorio.

### Clonar un repositorio de GitHub

Archivo, «Clonar un repositorio de GitHub...». Ctrl+IA pide a gh tus repositorios y los de tus organizaciones y los muestra en «Repositorios» (Alt+R), con dueño, descripción y visibilidad; el foco empieza en la lista, «Filtrar» (Alt+F) la reduce y Flecha abajo vuelve a ella. La última fila, siempre presente, es «Otro repositorio: escribir dueño/nombre a mano...». Elige la carpeta: la copia se crea dentro, con el nombre del repositorio, y después se abre «Nueva sesión» con ella. Si no tienes gh o no has iniciado sesión en él, te dice qué hacer. Ctrl+IA no guarda tus credenciales.

### Nueva sesión a partir de un mensaje

Para explorar algo aparte sin desviar la conversación: en la lista de mensajes, el visor o el panel de lectura, Ctrl+Alt+N (también en Archivo y en el menú contextual del mensaje). Se abre «Nueva sesión» con el mismo agente, la misma carpeta (o equipo) y el mensaje, o lo que hayas seleccionado de él, citado como primer mensaje; escribe debajo tu pregunta. La sesión nueva solo conoce lo citado; para que recuerde toda la conversación, usa «Continuar aquí».

### Continuar aquí

Ctrl+Mayús+N crea una sesión nueva en Ctrl+IA que continúa otra: el agente recuerda toda la conversación y la sesión nueva trae los mensajes de la original para leerlos. Sirve para seguir aquí una sesión de otro programa o para probar otro camino sin perder la original. Confirma el título (propone el mismo con «(continuación)») y pulsa Intro.

- Funciona con Claude Code, Codex y Ollama (también los chats de su app). Conserva el modelo, el esfuerzo y el modo; las de otros programas empiezan en el modo inicial de la configuración del agente.
- Si la original es de Ctrl+IA, la continuación no copia sus mensajes (siguen en la original), aunque Claude Code, Codex y Ollama la recuerdan. Con Antigravity, Gemini y los ACP, continuar una sesión de Ctrl+IA empieza una conversación nueva que no recuerda nada.
- Con las de Antigravity CLI no se hace copia: la sesión de Ctrl+IA sigue la misma conversación. Las de la app de Antigravity no se pueden seguir: Ctrl+O las abre en la app.
- Con «Al continuar una sesión de otro programa, seguir la original sin copiarla» (Preferencias, Historial), las de Claude Code y Codex tampoco se copian: lo que hables aquí aparece después en la terminal, la app o tu Mac. Por seguridad, solo si la original no está abierta en otro programa ni se ha usado en los dos últimos minutos; si no, te ofrece una copia. Cierra antes la sesión en la terminal o en la app y no la uses en los dos sitios a la vez: las conversaciones se mezclarían.
- La carpeta original tiene que existir y la sesión tiene que tener algún mensaje. Si trabaja en otro equipo por SSH, la continuación sigue allí.

### Continuar una sesión importada

Con una sesión importada de otro equipo, Ctrl+Mayús+N (o escribir en su cuadro y aceptar) abre «Nueva sesión» con el mismo agente si está en este equipo, la carpeta original si existe aquí y el título «(continuación)». La sesión nueva trae sus mensajes, y tu primer mensaje le llega al agente con la conversación anterior delante (solo lo que dijisteis tú y el agente; recortada si es muy larga, conservando el principio y lo más reciente, y se le dice). La importada no cambia.

## Conversar

### Enviar, la cola y detener

- **Enviar:** Ctrl+Intro desde el cuadro de respuesta. Ctrl+IA dice «Enviado» y suena (con «Leer mis propios mensajes al enviarlos», Preferencias, Anuncios, repite lo enviado). Lo escrito y los adjuntos se quedan en el cuadro aunque cambies de sesión. Sin sesión abierta, te ofrece crear una con tu texto; en una de otro programa, continuarla aquí; en una importada, continuarla en una sesión nueva.
- **La cola:** si envías mientras el agente trabaja, el mensaje espera y sale cuando termine («En cola. Se enviará cuando el agente termine»). Puedes dejar varios (con Codex, hasta 20).
- **Enviar ahora** (Ctrl+Mayús+Intro): interrumpe al agente y le pasa tu mensaje en ese momento («para, ese no es el archivo»). Si no trabaja, es un envío normal. Con adjuntos no se puede interrumpir: queda en cola.
- **Añadir al turno en marcha** (Ctrl+Alt+Intro), con Claude Code y Codex: mete un matiz en lo que el agente está haciendo, sin detenerlo («actualiza también el README»); lo tiene en cuenta en el mismo turno. Sin adjuntos. Con otros agentes, o si no trabaja, te dice qué usar y tu texto se queda en el cuadro.
- **Detener** (Ctrl+.): para al agente. Los mensajes en cola vuelven al cuadro de respuesta y te lo dice.
- **Si un mensaje no llega al agente** (por ejemplo, se cerró), nunca se pierde: vuelve al cuadro de respuesta, y si la sesión no estaba abierta te avisa de que lo encontrarás allí al abrirla. Si un turno falla, tu mensaje sigue en la conversación: «Volver a enviar...», en su menú contextual, lo repite.

### Adjuntar archivos e imágenes

- Ctrl+Mayús+F elige uno o varios archivos para el próximo mensaje; si ya hay adjuntos, ofrece «Añadir más» o «Quitar todos». La barra de estado dice cuántos tienes preparados.
- Ctrl+V en el cuadro adjunta lo copiado si es una imagen (una captura, una imagen del navegador) o archivos copiados en el Explorador. Si lo copiado trae también texto (una celda de Excel, un párrafo de Word), Ctrl+V pega el texto y te avisa; **Ctrl+Alt+V** («Adjuntar lo copiado») adjunta la imagen o los archivos igualmente.
- Claude Code, Codex y los agentes ACP reciben imágenes y archivos; Antigravity recibe la ruta y los lee él; Ollama, ver su apartado; por SSH, ver «Qué cambia en una sesión de otro equipo».

### Comandos de barra y plantillas

**Ctrl+/** («Comandos y habilidades»): las órdenes que empiezan por barra, como /compact, y las habilidades del agente. Se abre en la lista «Comandos» (Alt+M), donde Intro elige; para acortarla, Alt+F y escribe (Flecha abajo vuelve a la lista e Intro elige el primero). El comando se pone al principio del cuadro; si el cuadro ya empezaba por «/», sustituye todo lo escrito. Claude Code los da siempre, los agentes ACP cuando ya habéis empezado a conversar; Codex y Antigravity no los admiten desde Ctrl+IA.

**Ctrl+Mayús+P** («Plantillas de mensajes»): tus mensajes guardados. En «Plantillas» (Alt+P), con título, texto y usos, Intro inserta la plantilla en el cursor del cuadro de respuesta, F2 la edita y Suprimir la borra. Botones «Usar» (Alt+U), «Nueva...» (Alt+N, que viene rellena con lo que tengas escrito; en su cuadro, «Título», Alt+T, y «Texto», Alt+X, donde Intro es línea nueva y Alt+A acepta), «Editar...» (Alt+E), «Borrar» (Alt+B), «Guardar el texto actual como plantilla» (Alt+A) y «Cerrar» (Alt+C).

### Opciones de la sesión

Ctrl+Mayús+O, con una sesión de Ctrl+IA abierta: «Título» (Alt+T; vacío conserva el anterior), «Modelo» (Alt+M), «Esfuerzo» (Alt+E), «Modo de permisos» (Alt+P), «Estilo de las respuestas» (Alt+R, con Claude Code) y las skills del catálogo: «Categoría de skills» (Alt+D), «Skills de la sesión» (Alt+S) y «Ver y gestionar el catálogo de skills...» (Alt+V). Todo vale desde el próximo mensaje; el estilo solo para esta sesión, sin tocar la configuración de Claude Code.

### Estado del turno

Un turno es lo que hace el agente desde que le escribes hasta que acaba de responder. Ctrl+Mayús+T dice desde cuándo trabaja y su última acción (o su estado si no trabaja), los mensajes en cola o añadidos al turno, si te espera una respuesta, cómo va su lista de tareas y, si la rama tiene una PR, cómo van sus comprobaciones. Con «Anunciar el progreso de la lista de tareas» (Preferencias, Anuncios) y la lectura en «Completo», se dice cada tarea hecha («Hecha: ejecutar las pruebas. 4 de 7»). Cuando un turno acaba con una revisión de código, Ctrl+IA la resume («Revisión: 5 hallazgos, 2 confirmados»).

## Responder al agente

### Cuándo te pide algo

El agente se para a esperarte cuando necesita un permiso (modificar un archivo, ejecutar una orden, usar una herramienta externa), te hace una pregunta o te propone un plan. Suena el sonido de permiso, Ctrl+IA dice por ejemplo «Codex te necesita en «Web»… Pulsa Control+Mayúsculas+A para responder», la sesión pasa a «Te necesita» y, si estás en otro programa, sale una notificación de Windows. El agente espera lo que haga falta: Ctrl+IA nunca responde solo.

### El diálogo de respuesta

Ctrl+Mayús+A abre la petición de la sesión abierta o, si no tiene, la de otra sesión. El título dice qué quiere el agente.

- «Lo que el agente escribió antes» (Alt+E): si escribió algo justo antes (un borrador, un mensaje de commit); el foco empieza aquí.
- «Detalle» (Alt+D): todo lo que quiere hacer (orden, carpeta, motivo, cambios). Si no existe el campo anterior, el foco empieza aquí.
- Los campos de la pregunta, si lo es.
- «Mensaje para el agente (opcional)» (Alt+M) o «Qué cambiar del plan (opcional)» (Alt+Q): el agente lo recibe si deniegas o sigues planificando.
- Un botón por respuesta, con su letra (Ctrl+H explica cada uno), y «Responder después».

**Intro pulsa siempre la opción inofensiva** (denegar, no responder o seguir planificando): un Intro de más nunca autoriza nada. Escape o «Responder después» cierran sin contestar y el agente sigue esperando; vuelve con Ctrl+Mayús+A. Si detienes al agente o el turno termina, la petición se retira sola. Al responder, Ctrl+IA dice lo elegido y cuántas peticiones quedan.

### Permisos, preguntas y planes

- **Permisos:** «Permitir una vez», «Permitir durante esta sesión» (no vuelve a preguntar lo mismo en esta sesión; lo guarda Ctrl+IA), «Permitir siempre» o «Permitir siempre en esta carpeta» (lo guarda el propio agente), «Denegar», «Denegar siempre» y «Denegar y detener el turno». Cada agente ofrece las suyas (ver su apartado).
- **Preguntas:** cada una con su encabezado: una lista si se elige una respuesta, casillas si se eligen varias, o un campo para escribir; si el agente lo admite, la última opción es «Otra respuesta (escríbela debajo)». Un campo de solo lectura muestra completa la opción elegida y, si Claude la acompaña de una vista previa (un borrador, código), también esta. Ve con Tab a «Responder» (Intro en la lista pulsa «No responder»); si te falta una pregunta, Ctrl+IA te lleva a ella.
- **Planes:** en los modos de planificación, el plan entero está en «Detalle». Con Claude Code: «Aprobar y aceptar las ediciones», «Aprobar y preguntar cada cambio» o «Seguir planificando» (escribe antes qué cambiar en «Qué cambiar del plan»).

Si el agente pide lo mismo varias veces a la vez, Ctrl+IA solo te avisa de la primera; si la permites para la sesión o para siempre, las demás se permiten con tu respuesta.

**Peticiones de las herramientas externas:** una herramienta (servidor MCP) puede pedirte datos (un campo por dato; los secretos no se muestran), que abras un enlace (ábrelo tú y pulsa «Aceptar») o que confirmes algo: «Aceptar», «Rechazar» o «Cancelar». Si pide algo que Ctrl+IA no sabe rellenar, solo ofrece «Rechazar» o «Cancelar».

### Permisos de la sesión

Ctrl+Mayús+M muestra lo que el agente puede hacer sin preguntarte en la sesión:

- «Modo de permisos de «sesión»» (Alt+P), explicado; «Cambiar el modo...» (Alt+M) abre «Opciones de la sesión». «Cuándo se aplica» explica desde cuándo valen los cambios.
- «Permitido en esta sesión» (Alt+E): cada regla con su explicación y su fecha («Ejecutar «git push» con cualquier argumento… Permitido el 17/10/2026»), y con Claude Code las carpetas que se guardaron con el permiso. «Quitar» (Alt+Q o Suprimir) y «Quitar todas» (Alt+T), tras preguntarte.
- «Reglas de la configuración de…» (Alt+R), solo para leer: con Claude Code, lo que su propia configuración permite, pregunta o prohíbe en esa carpeta, con el archivo de cada regla (también lo de «Permitir siempre en esta carpeta», y si alguna espera a que la carpeta sea de confianza); con Codex, sus archivos de reglas. Se cambian en «Configuración de los agentes» o en el agente (por SSH no se leen).

Lo que quites vale desde el próximo mensaje (con Codex, desde la siguiente petición). Las reglas de la sesión solo las guarda Ctrl+IA, y una continuación empieza sin ninguna. «Cerrar» (Alt+C).

## Leer los mensajes

### La lista de mensajes

Cada fila es un mensaje, del más antiguo al más reciente, con estas columnas:

- **Quién:** «Tú», el agente, «Herramienta» (una acción, como leer o editar), «Aviso», «Error», «Cambio» (un archivo modificado), «Plan» o «Razonamiento».
- **Mensaje:** el texto completo (se lee entero aunque no se vea).
- **Hora:** la hora si es de hoy; si no, la fecha.
- **Indicadores:** lo que trae, por ejemplo «Pregunta por responder, 2 bloques de código, 3 enlaces». También «Permiso concedido», «Plan aprobado», «Con error», «Detenido», adjuntos, archivos cambiados, «Largo» y «Favorito» con su nota. Cada indicador se puede quitar en Preferencias, Lectura de listas.

Puedes añadir la columna «Número» en Preferencias, Lectura de listas. Inicio va al primero y Fin al último; los nuevos se añaden al final sin mover tu foco. Ctrl+T (o Preferencias, Mensajes) muestra u oculta las acciones del agente («Herramienta», con su resultado en el detalle). El razonamiento solo se ve con «Guardar y mostrar el razonamiento» (Preferencias, Mensajes) y las acciones visibles.

**Sesiones largas:** solo se cargan los últimos 100 mensajes (se cambia en Preferencias, Mensajes); Ctrl+IA lo dice al abrir, por ejemplo «Últimos 100 de 520 mensajes». La primera fila, «Cargar 100 mensajes anteriores», los carga con Intro; Alt+Inicio lo hace sin mover el foco y Ver, «Cargar todos los mensajes de la sesión», los carga todos. Buscar, las listas y exportar usan siempre la conversación entera.

### El visor de mensajes

Intro abre el mensaje en un visor que no bloquea la ventana: con formato (encabezados, listas y tablas reales para tu lector) o como texto para leer línea a línea. Intro abre la forma elegida en Preferencias, Mensajes (de fábrica, con formato) y Mayús+Intro la otra. El detalle (por ejemplo, la salida de una orden) va al final. Ctrl+F busca y F3 sigue; Escape o Alt+C lo cierran; Ctrl+H dice sus teclas.

### Leer y responder a la vez: citar

Para contestar punto por punto a un mensaje largo:

1. Ábrelo con Intro y lee hasta el punto que quieras contestar.
2. **Ctrl+R** lo cita al final de tu respuesta, con «>» delante de cada línea. En el visor con formato, selecciona y pulsa Ctrl+C antes de Ctrl+R (tu lector no le pasa la selección a la página; Ctrl+R usa lo copiado de ese mensaje). En el visor como texto y en el panel de lectura cita lo seleccionado o, sin selección, el párrafo del cursor. En la lista de mensajes cita el mensaje entero.
3. **Alt+3** te lleva al cuadro de respuesta con el visor abierto detrás; escribe debajo de la cita. **Alt+6** te devuelve al visor donde lo dejaste.
4. Envía con Ctrl+Intro cuando acabes.

Ctrl+R nunca envía ni mueve el foco. Si citas dos o más fragmentos, Ctrl+IA añade al principio una frase que le explica al agente que respondes por partes, debajo de cada cita (puedes quitarla).

### Mensajes favoritos

Marca como favorita una respuesta que vas a necesitar más tarde y vuelve a ella aunque lleguen muchos mensajes después. Vale en cualquier sesión y con cualquier agente, también en las de otros programas y las importadas.

- **Ctrl+Alt+F** marca o quita el mensaje elegido (en la lista, en el panel de lectura, en el menú Ver o en su menú contextual).
- **Alt+Flecha arriba y Alt+Flecha abajo** van al favorito anterior o al siguiente («Favorito 2 de 5», quién y cómo empieza), aunque esté entre los mensajes aún no cargados. En el panel de lectura lo traen al panel.
- **Ctrl+Alt+Mayús+N** escribe una nota de una línea (hasta 200 caracteres) para recordar por qué lo marcaste; si no era favorito, lo marca con la nota; vacía, la quita. Se oye al ir al favorito y sale en Indicadores («Favorito: nota»).
- **Ctrl+Alt+Mayús+F** («Mensajes favoritos...») reúne los de todas las sesiones, los marcados más recientemente primero, con el principio del mensaje, su nota, la sesión, quién y la fecha. Intro te lleva al mensaje, F2 cambia la nota, Ctrl+C copia el texto, Suprimir lo quita de favoritos tras preguntar (el mensaje no se borra) y el menú contextual añade «Copiar el título de la sesión».
- Borrar para siempre una sesión borra también sus favoritos.

### Menú contextual de un mensaje

Ctrl+C copia el texto y su detalle. La tecla Aplicaciones ofrece además: ver con formato o como texto, copiar solo el texto o solo el detalle, «Leer en voz alta», citar (Ctrl+R), nueva sesión a partir del mensaje (Ctrl+Alt+N), enviar la respuesta o el plan a otra sesión (Ctrl+Alt+R), marcar como favorito y la nota, ir al favorito anterior o siguiente, y abrir los bloques de código, los enlaces y los archivos cambiados. En tus mensajes, además, «Copiar al cuadro de respuesta» (sustituye lo que tuvieras escrito, para corregirlo y volver a enviarlo) y, en las sesiones de Ctrl+IA, «Volver a enviar...» (solo el texto, sin adjuntos) y deshacer o rehacer los cambios de archivos desde ese mensaje. La fila «Cargar mensajes anteriores» tiene «Cargar mensajes anteriores» y «Cargar todos los mensajes».

### Propiedades y exportar

**Alt+Intro** muestra todos los datos de la sesión en el visor: título, agente, estado, origen, equipo, carpeta (y si es un repositorio de git, su rama y si es normal, una copia aislada o un submódulo), modelo, fechas, grupos, favorita, oculta o terminada, sin leer, identificadores, contexto usado y, en las de Ctrl+IA, el esfuerzo, el modo, lo permitido y de cuál continúa. También la lista de tareas del agente y la PR, si las hay. Si el visor de propiedades (o el de uso) ya estaba abierto, solo vuelve al frente: ciérralo y vuelve a pulsarlo para verlo al día.

**Ctrl+E** guarda la conversación entera como «Markdown», «Página web» o «Texto», con el agente, la carpeta y el origen.

## Buscar

- **En la sesión:** con el foco en los mensajes o en la respuesta, Ctrl+F, escribe y pulsa Intro: va al siguiente mensaje que contiene todas las palabras (en el texto o el detalle, sin importar mayúsculas ni tildes), también entre los no cargados. F3 sigue y Mayús+F3 va al anterior (sin búsqueda previa, abren el cuadro). En la lista de sesiones, o sin sesión abierta, Ctrl+F filtra la lista de sesiones.
- **En todas las sesiones:** Ctrl+Mayús+S. Escribe en «Buscar» (Alt+B) y pulsa Intro; si viene relleno con lo último que buscaste en una sesión, la búsqueda empieza sola. «Resultados» (Alt+R) tiene la sesión, quién, un fragmento y cuándo, hasta 500. Intro abre la sesión en ese mensaje; el menú contextual copia el mensaje o el título de la sesión. Mientras busca, el botón es «Detener» (Alt+D) y Escape la detiene; si no, Escape cierra. Busca en las sesiones de Ctrl+IA, las importadas, las de Claude Code y Codex de otros programas y los chats de la app de Ollama; no en las de Antigravity (sus mensajes no se pueden leer) ni dentro de las de equipos por SSH.

## Listas de la sesión

Reúnen el código, los enlaces, los archivos cambiados y más, para no buscarlos mensaje a mensaje. Funcionan con todos los agentes, también en las sesiones de otros programas.

**Abrirlas:** Ctrl+Mayús+I (en la última página que usaste mientras Ctrl+IA siga abierto), o directamente Ctrl+Mayús+B (código), Ctrl+L (enlaces), Ctrl+Mayús+D (archivos cambiados) y Ctrl+Mayús+J (tareas). Ctrl+Mayús+C, desde la ventana principal, copia el último bloque de código sin abrirlas.

**Dentro:** Ctrl+Tab y Ctrl+Mayús+Tab cambian de página, Ctrl+1 a Ctrl+9 y Ctrl+0 van a las diez primeras, Escape o «Cerrar» (Alt+C) cierran. Arriba hay dos controles:

- **«Mostrar» (Alt+M, o Ctrl+T desde cualquier sitio):** «Toda la sesión» o «Este mensaje o turno», el mensaje que tenías elegido al abrir. En código y enlaces se ve solo lo de ese mensaje; en lo demás, lo de su turno (desde tu mensaje hasta el siguiente tuyo). Se recuerda.
- **«Filtrar» (Alt+F o Ctrl+F):** deja solo lo que contiene las palabras escritas, sin importar mayúsculas ni tildes. La etiqueta dice cuántos quedan («Enlaces (3 de 12)»). Flecha abajo o Intro van a la lista; Escape vacía el filtro (si está vacío, cierra). Cada página recuerda el suyo.

**Las trece páginas:**

1. **Bloques de código** (Ctrl+1): lenguaje, primera línea y líneas.
2. **Enlaces** (Ctrl+2): direcciones web y rutas que menciona el agente (como `app/main.py:42`), con su tipo: «Web», «Archivo», «Archivo de red» o «Archivo que no existe».
3. **Archivos cambiados** (Ctrl+3): lo que crearon o modificaron el agente y sus subagentes, con «+12/−3» (con Antigravity, solo la ruta). Lo cambiado con órdenes de la terminal puede no salir.
4. **Órdenes ejecutadas** (Ctrl+4): «Correcta», «Con error» o «Sin resultado».
5. **Tareas del agente** (Ctrl+5): «Pendiente», «En curso» o «Hecha»; la etiqueta dice cuántas lleva.
6. **Subagentes** (Ctrl+6): tipo, estado, pasos y ediciones.
7. **Hallazgos de la revisión** (Ctrl+7): archivo, línea, categoría, veredicto («Confirmado» o «Posible») y resultado («Pendiente», «Corregido», «Omitido» o «No hacía falta»).
8. **Decisiones** (Ctrl+8): las preguntas que te hizo y los planes que te propuso, con tu respuesta (solo en sesiones de Ctrl+IA).
9. **Errores** (Ctrl+9): herramientas y órdenes que fallaron (también las que rechazaste) y avisos de error.
10. **Commits** (Ctrl+0): los del agente, con su mensaje, hash, rama y si salieron bien, y sus git push.
11. **Incidencias y PR** (con Ctrl+Tab): las de GitHub citadas («dueño/repo#12», «#12» del repositorio de la sesión, o enlaces).
12. **Archivos leídos** (con Ctrl+Tab): lo que leyó el agente y cuántas veces.
13. **Tus mensajes** (con Ctrl+Tab): los que escribiste.

Las sesiones de Codex también llenan errores, commits y archivos leídos a partir de sus órdenes.

**Con cada elemento:**

- Intro: verlo completo (el bloque, los cambios del archivo, la orden con su salida, la tarea, lo que hizo el subagente, el hallazgo, la decisión, el error o el commit), abrir el enlace o la incidencia en el navegador, leer el archivo, o ir a tu mensaje.
- Ctrl+C: copiarlo (en un commit, el hash). Ctrl+Mayús+C: el contenido actual del archivo, la salida de la orden, el detalle del error o el mensaje del commit.
- Ctrl+S: guardar el bloque como archivo o los cambios como archivo de diferencias (siempre los originales).
- Alt+Intro: ir al mensaje donde aparece.
- En archivos, rutas y hallazgos: Ctrl+L lee el archivo tal como está ahora (de texto, hasta 2 MB); Ctrl+O lo abre con su programa; Ctrl+E en tu editor de código (en un hallazgo, en su línea); Ctrl+Mayús+E lo muestra en el Explorador. Por seguridad, nunca se abren con su programa ejecutables, scripts ni accesos directos, y nada de aquí modifica tus archivos.
- Ctrl+R en un hallazgo prepara un mensaje para que el agente lo corrija, y en «Tus mensajes» cita el mensaje; cierra las listas y lo deja en el cuadro de respuesta sin enviarlo (en sesiones de otros programas, lo copia al portapapeles).

Los cambios de un archivo se leen con palabras («Línea 12, añadida: …»); se cambia en Preferencias, Mensajes.

## Trabajar con varios agentes

### Enviar la respuesta a otra sesión

Para que un agente revise o conteste el trabajo de otro (por ejemplo, que Codex revise lo de Claude): elige la respuesta o el plan en la lista de mensajes (o, desde otro sitio, se usa la última respuesta del agente) y pulsa **Ctrl+Alt+R**.

1. «Enviar a» (Alt+A): tus sesiones de Ctrl+IA, de cualquier agente, menos esta, las ocultas y las archivadas. La sesión de la que vino lo último que recibiste sale la primera (mientras Ctrl+IA siga abierto), así que Ctrl+Alt+R e Intro devuelven la respuesta.
2. «Mensaje» (Alt+M): la respuesta, con una primera línea que dice qué agente la escribió y en qué sesión, para que el otro sepa que no son órdenes tuyas. Cámbiala si quieres.
3. Ctrl+Intro, Intro en la lista o «Enviar» (Alt+E). Se envía como si lo escribieras allí (si trabaja, a la cola) y Ctrl+IA te lleva a esa sesión para oír su respuesta.

Un mensaje reenviado nunca contesta preguntas ni da permisos por ti. A Codex se le pueden enviar hasta 32.000 caracteres. Si el título del cuadro dice que la sesión sigue trabajando, lo que envías puede ser un paso intermedio. Si el envío falla, el texto queda en el cuadro de respuesta de la otra sesión.

### Conversación automática entre sesiones

Para que dos sesiones se hablen solas unos turnos (por ejemplo, Claude escribe y Codex revisa), con cualquier agente: abre la que debe empezar y pulsa **Ctrl+Alt+Mayús+R**.

- «Primera sesión» (Alt+P, solo lectura), «Segunda sesión» (Alt+S, donde empieza el foco), «Tarea para la primera sesión» (Alt+T; Intro es línea nueva), «Turnos como máximo» (Alt+U; de 2 a 20, 4 de fábrica, contando las respuestas de las dos) e instrucciones opcionales para la primera (Alt+R) y la segunda (Alt+N), como «revisa y señala fallos, sin cambiar archivos». Ctrl+Intro o «Empezar» (Alt+E). Las dos tienen que estar paradas, y solo puede haber una conversación a la vez.
- Ctrl+IA envía la tarea a la primera; cuando termina, pasa su respuesta a la segunda (con una línea que dice de quién viene), y así sucesivamente. Cada turno se anuncia («Turno 3 de 8: Codex responde a Claude») y cada mensaje queda en su sesión.
- **Ctrl+IA nunca responde por ti:** si un agente pide permiso o pregunta, la conversación se pausa y te avisa; responde con Ctrl+Mayús+A y sigue cuando ese agente acabe.
- **Termina** cuando un agente escribe [FIN] (se les pide que lo hagan al acabar), al llegar al máximo, si un turno falla, no aporta nada nuevo o no cabe en Codex, o si la detienes: Sesión, «Detener la conversación entre sesiones» (el agente acaba su turno, pero ya no se pasa), o Ctrl+. en cualquiera de las dos (lo para en el acto).
- **Ctrl+Alt+Mayús+C** («Ver la conversación entre sesiones») muestra todo lo que se han dicho, intercalado, mientras Ctrl+IA siga abierto.

Consejo: que una escriba y la otra solo revise; si las dos cambian archivos en la misma carpeta, una puede deshacer lo de la otra.

## Deshacer los cambios de archivos

Antes de enviar cada mensaje tuyo, Ctrl+IA guarda una foto de los archivos de la carpeta de la sesión en un repositorio aparte (en data\cache), que nunca toca tu repositorio git. Así puedes devolverlos a como estaban antes de cualquiera de tus mensajes, también lo que el agente cambió con órdenes de la terminal. Vale con Claude Code, Codex, Antigravity, Gemini y los agentes ACP; en sesiones por SSH, solo con Claude Code (con su propio sistema, que solo deshace lo que cambia con sus herramientas de edición).

1. En la lista de mensajes, ponte en uno de tus mensajes y pulsa **Ctrl+Alt+Z** (o Sesión, o su menú contextual). En una respuesta del agente, se usa tu mensaje que la provocó; fuera de la lista, tu último mensaje. El agente tiene que estar parado.
2. Se abre una ventana con «Archivos» (Alt+A) y lo que pasará con cada uno: «Se devolverá como estaba», «Se borrará (lo creó el agente)» o «Se volverá a crear». En «Avisos» (Alt+V), si los hay: archivos que cambiaste tú después (también se deshacen; «Rehacer» los recupera), archivos abiertos en otro programa (no se tocan), carpetas que son otro repositorio (no se deshacen), archivos de más de 5 MB (no se guardan) y commits nuevos en tu repositorio (vuelven los archivos, los commits se quedan).
3. Pulsa «Deshacer» (Alt+D). «Cancelar» (Alt+C, Escape o Intro fuera de la lista) es el botón predeterminado. El menú contextual de la lista copia la ruta de un archivo o la lista entera.

Queda un aviso en la conversación y, con tu siguiente mensaje, el agente se entera de qué archivos volvieron atrás. **Ctrl+Alt+Mayús+Z** rehace el último deshacer (botón «Rehacer», Alt+R); por SSH no se puede rehacer.

No se guardan puntos en la carpeta personal, el Escritorio, Documentos, Descargas, la raíz de un disco, carpetas de red ni carpetas de más de 50.000 archivos, ni con Ollama. Solo se guarda lo que git no ignora: lo que excluye tu .gitignore, y node_modules, .venv, build, dist o .git, no se guarda ni se devuelve. Si guardar el punto tarda más de 5 segundos (o falta Git), el mensaje sale sin él; te lo dice al intentar deshacer. Los puntos se borran a los 30 días, al pasar de 50 por sesión o al borrar la sesión. Necesita Git para Windows 2.26 o posterior. Se desactiva en Preferencias, Agentes.

## PR y comprobaciones automáticas

Cuando un agente sube su trabajo a GitHub suele abrir una PR (una propuesta de cambios), y GitHub pasa unas comprobaciones automáticas (compilar, pruebas) que aciertan o fallan. Ctrl+IA lo consulta por ti con gh y tu sesión de GitHub, solo para leer, en las sesiones de este equipo cuya carpeta es un repositorio de GitHub, con cualquier agente.

- **Avisos:** en las sesiones abiertas o usadas en las últimas 3 horas, mira la PR cada minuto mientras hay comprobaciones en marcha y cada 5 minutos si no. Te avisa una vez de cada cambio, con sonido y notificación: «Las comprobaciones de la PR 12 de «Web» han fallado: pruebas» o «han pasado», cuando se fusiona y cuando en la revisión piden cambios. Si no hay gh, sesión, conexión o PR, calla. Se desactiva en Preferencias, Anuncios.
- **Sesión, «PR y comprobaciones»** (también en el menú contextual de la lista de sesiones): «Estado de la PR...» (Ctrl+Alt+P: número, título, enlace, rama, estado, revisión, si se puede fusionar y cada comprobación), «Abrir la PR en el navegador», «Copiar el enlace de la PR», «Leer el fallo de las comprobaciones...» (las líneas del registro donde falló; GitHub los borra a los 90 días) y «Pedir al agente que lo arregle» (deja el mensaje con el fallo en el cuadro de respuesta, sin enviarlo).
- Propiedades y Estado del turno dicen lo último que se sabe de la PR.

## Herramientas del agente

**Sesión, «Herramientas del agente...» (Ctrl+Alt+H)** dice qué tiene cargado el agente de la sesión, en un texto que se copia: servidores MCP y conectores con su estado («conectado», «no ha podido arrancar», «necesita que inicies sesión»), complementos y habilidades (también las skills del catálogo) y, con Claude Code, sus subagentes y cuántas herramientas tiene. Completo con Claude Code (después de su primer mensaje) y Codex; con Antigravity y los ACP, solo las de Ctrl+IA.

### Herramientas de Ctrl+IA para los agentes

Ctrl+IA da a los agentes tres herramientas propias, que solo afectan a su sesión y salen como «ctrlia»:

- **Avisarte** de algo (hasta 300 caracteres, como mucho uno cada 10 segundos): te lo dice con voz y braille y lo deja en la conversación; si es urgente, como aviso importante; si estás en otro programa, con sonido y notificación.
- **Dar la sesión por terminada,** como Ctrl+D (si está trabajando, al acabar; si vuelves a escribirle, se reactiva).
- **Renombrar la sesión** (hasta 80 caracteres).

Pídeselo con naturalidad: «avísame cuando acaben las pruebas», «cuando termines, da la sesión por terminada». Cada uso deja un mensaje de Ctrl+IA en la conversación. Valen con Claude Code (también por SSH), Codex, Antigravity, Gemini y los agentes ACP de este equipo; con Antigravity, Ctrl+IA añade a sus ajustes la regla que las permite (se ve en «Reglas de permisos de Antigravity»). Se desactivan en Preferencias, Agentes, y el cambio vale cuando el agente de cada sesión vuelve a arrancar.

## Skills

Una **skill** (habilidad) es una carpeta con un archivo SKILL.md que explica al agente de IA cómo hacer un trabajo concreto: revisar la accesibilidad, escribir pruebas, traducir una aplicación… El agente de IA (Claude Code, Codex…) es quien trabaja; la skill es la ficha de instrucciones que lee para hacerlo bien. Al empezar solo ve el nombre y la descripción de cada skill, y abre su SKILL.md cuando la tarea encaja o se lo pides. Ctrl+IA guarda las tuyas en un catálogo y en cada sesión eliges cuáles usa, con cualquier agente de IA.

### Skills de ejemplo

Ctrl+IA trae 14, pensadas para programar aplicaciones de escritorio en Python con wxPython accesibles con lector de pantalla: accesibilidad, actualizador, auditoría, base de datos SQLite, complemento de NVDA, configuración, documentación, entorno virtual, hilos y tareas, registro, proyecto nuevo, pruebas, red HTTP y traducciones. La primera vez se añaden al catálogo en la categoría «Ejemplos de Ctrl+IA», con «(ejemplo)» detrás del nombre. Son tuyas: úsalas, cámbialas o quítalas; «Restaurar las skills de ejemplo», en el gestor, devuelve las que falten. Están en la carpeta del programa (data\skills_ejemplo): si quieres cambiar su SKILL.md, copia antes su carpeta a otro sitio y añade la copia, porque una actualización las sustituye.

### Gestor de skills

Herramientas, «Gestor de skills...» (**Ctrl+Alt+G**). Arriba, «Categoría» (Alt+T; «Todas», «Sin categoría» o una) y «Buscar» (Alt+B o Ctrl+F; en el nombre, la descripción, las notas y la carpeta). La lista «Skills» (Alt+S) tiene nombre, categoría, descripción, carpeta y estado («Disponible» o «No encontrada»).

- **Añadir desde una carpeta** (Ctrl+O): busca dentro todas las carpetas con SKILL.md y las muestra en una lista de casillas (Espacio marca; Ctrl+A y Ctrl+Mayús+A, todas o ninguna): las nuevas marcadas y las que ya tienes con «(ya en el catálogo)». «Skills encontradas» (Alt+E); debajo, «Categoría de la skill elegida» (Alt+T; propone la del SKILL.md o la de su subcarpeta, y el menú contextual la pone a todas las marcadas), «Detalles» (Alt+L) y «Actualizar la descripción de las que ya están en el catálogo» (Alt+U). «Añadir las marcadas» (Alt+A).
- **Añadir una** (Ctrl+I): eliges su carpeta y revisas nombre, descripción, categoría y notas.
- **Crear una nueva** (Ctrl+N): nombre, descripción, categoría, notas y carpeta donde se crea (de fábrica, data\skills). Ctrl+IA crea un SKILL.md de plantilla en español y te ofrece abrirlo en tu editor.
- Con una skill elegida: Intro muestra su SKILL.md, F2 edita sus datos (solo en el catálogo, nunca en el SKILL.md), Ctrl+E abre el SKILL.md en tu editor, Ctrl+Mayús+E abre su carpeta, Ctrl+C copia la ruta del SKILL.md y Suprimir la quita del catálogo (Intro responde «No»). La carpeta nunca se borra, salvo que la creases desde Ctrl+IA y lo confirmes aparte.
- F5 revisa el catálogo: marca lo que ya no está y, si cambió la descripción de algún SKILL.md, pregunta si usarla.
- La tecla Aplicaciones o «Acciones...» (Alt+O) tienen todas las órdenes, además de «Restaurar las skills de ejemplo», «Exportar el catálogo» e «Importar un catálogo» (los datos y las rutas, no el contenido de los SKILL.md).

### Usarlas en una sesión

En «Nueva sesión» (u «Opciones de la sesión», desde el próximo mensaje) marca con Espacio las skills que quieras (Ctrl+A todas, Ctrl+Mayús+A ninguna); la categoría filtra sin perder lo marcado y su menú contextual muestra el SKILL.md. Cada agente de IA las recibe a su manera, sin que hagas nada:

- **Claude Code** las carga como skills propias de la sesión («ctrlia-skills:nombre» en Ctrl+Alt+H) y puede leer sus carpetas sin pedirte permiso, solo para leer. Usa siempre tu carpeta actual: Ctrl+IA la enlaza, nunca la copia, cambia ni borra.
- **Codex, Antigravity, Gemini y los ACP:** con tu primer mensaje (y cada vez que cambie la lista), Ctrl+IA les dice qué skills tienen y dónde está cada SKILL.md, y las leen cuando hace falta. En la lista ves tu mensaje tal como lo escribiste.
- **Ollama y las sesiones por SSH** reciben el contenido del SKILL.md (hasta 12.000 caracteres), sin los demás archivos de su carpeta.

Pídeselo («usa la skill de pruebas para…») o deja que la use cuando la tarea encaje. No confundas las skills con los **subagentes**: estos son ayudantes que el propio agente de IA lanza aparte para repartirse el trabajo (salen en las listas de la sesión, página «Subagentes»).

## Organizar

- **F2** renombra (en sesiones de otros programas, solo en Ctrl+IA).
- **Ctrl+Mayús+X** marca o quita una favorita: su título empieza por «Favorita:» y tienen su vista.
- **Suprimir** la oculta sin borrar nada (vista «Ocultas»; Organizar, «Volver a mostrar»).
- **Mayús+Suprimir** borra para siempre una sesión de Ctrl+IA y sus mensajes, tras confirmarlo («No» es lo predeterminado). Las de otros programas no se borran desde aquí (Ctrl+IA te lo explica): ocúltalas con Suprimir. En una importada, borra la copia.
- **Ctrl+D** la da por terminada: deja de salir en «Te necesitan», «Trabajando» y «Con respuestas sin leer» y se quitan sus respuestas nuevas (útil si una sesión vieja se queda diciendo «Te necesita»). Otro Ctrl+D la reactiva, y se reactiva sola con actividad nueva.

### Informe de sesiones

Herramientas, «Informe de sesiones...» (**Ctrl+I**): todas las sesiones, también ocultas, archivadas e importadas, con lo que ocupan y sus mensajes, para decidir qué ocultar o borrar. «Totales» (Alt+T): cuántas hay, cuánto ocupan, por agente y origen, y las diez más grandes. «Mostrar» (Alt+M), «Ordenar por» (Alt+O; de fábrica por tamaño) y «Filtrar» (Alt+F). En «Sesiones» (Alt+S): Intro la abre, Alt+Intro muestra sus datos, Suprimir la oculta o muestra y Ctrl+D la da por terminada o la reactiva. Botones «Abrir la sesión» (Alt+A), «Datos de la sesión» (Alt+D), «Ver como documento» (Alt+V), «Guardar...» (Alt+G, Markdown o CSV) y «Copiar» (Alt+I); estos tres llevan siempre todas las sesiones. No incluye el texto de los mensajes; de Antigravity no puede contar nada.

### Grupos

Un grupo reúne sesiones de un proyecto, una investigación o un cliente; una sesión puede estar en varios y un grupo tener agentes distintos. **Ctrl+G** añade la sesión a un grupo (la última opción crea uno nuevo); Organizar tiene «Quitar de un grupo...» y «Mover a otro grupo...»; «Nueva sesión» la crea ya dentro.

**Ctrl+Mayús+L** («Grupos de sesiones»): en «Grupos» (Alt+G), Intro abre el grupo, F2 lo renombra, Alt+Intro edita sus datos, Suprimir lo borra (sus sesiones no), Ctrl+N crea uno, Alt+Flecha arriba y abajo cambian el orden y Ctrl+E lo exporta entero; el menú contextual añade «Mostrar en la lista principal». Botones: «Abrir», «Nuevo...», «Renombrar...», «Editar...», «Borrar» y «Cerrar». Los datos de un grupo son «Nombre» (Alt+N), «Descripción o notas» (Alt+D), «Agente para las sesiones nuevas» (Alt+G; «El de siempre (Preferencias)» o Claude, Codex, Antigravity o Gemini) y «Carpeta de trabajo para las sesiones nuevas» (Alt+T, con «Examinar», Alt+X).

**Un grupo abierto** tiene «Descripción del grupo» (Alt+G) y «Sesiones» (Alt+S), en el orden que decides tú:

- Intro abre la sesión; Ctrl+N crea una dentro del grupo; Insertar añade sesiones marcándolas con Espacio (Ctrl+A y Ctrl+Mayús+A; las ocultas no salen).
- Suprimir la saca del grupo, Ctrl+M la mueve a otro, Ctrl+G la añade también a otro, F2 la renombra y Ctrl+C copia el título.
- Alt+Flecha arriba y abajo cambian su posición; el menú contextual tiene «Ordenar el grupo por» y «Quitar varias sesiones...».
- Ctrl+F filtra (Escape quita primero el filtro; con filtro no se reordena), Alt+Intro edita el grupo y Ctrl+E lo exporta: todas sus sesiones en un archivo Markdown, HTML o texto, con índice.
- Botones: «Abrir la sesión», «Nueva sesión en el grupo...», «Añadir sesiones...», «Quitar del grupo», «Editar el grupo...», «Exportar el grupo...» y «Cerrar». Una sesión que ya no existe sale como «Sesión no disponible».

## Sesiones de otros programas

Ctrl+IA muestra también, solo para leer, las conversaciones que ya tienes (columna Origen):

- **Claude Code:** de la terminal, la app de escritorio, Cowork y otros (extensiones de editores). Las que archivaste en la app, en la vista «Archivadas».
- **Codex:** su app y su terminal (si Codex está instalado).
- **Antigravity:** su app y Antigravity CLI. Sus mensajes no se pueden leer (formato propio): se ven el título, la carpeta y los pasos, y un aviso explica cómo seguirlas.
- **App de Ollama:** sus chats, que se pueden continuar.
- **Tus equipos por SSH:** sus sesiones de Claude Code y sus hilos de Codex (ver «Sesiones que ya tiene el otro equipo»).

En Preferencias, Historial, eliges cuáles se muestran. Ctrl+IA nunca cambia esos archivos. Si la conversación ya no existe (Claude Code borra las antiguas, 30 días de fábrica), la fila dice «conversación no disponible».

- **Leerlas:** como cualquier otra (buscar, copiar, exportar, listas). Si escribes, Ctrl+IA te ofrece continuarla aquí.
- **Seguir en vivo:** si abres una sesión de Claude Code que está usando otro programa, sus mensajes nuevos aparecen solos y se leen (se desactiva en Preferencias, Historial).
- **Continuar aquí:** ver «Continuar aquí».
- **Ctrl+O** («Abrir en la app del agente») la abre en su app: con Claude Code, las de la app, Cowork, la terminal y también las creadas en Ctrl+IA (la primera vez la app de Claude la importa, la misma conversación, y desde entonces sale en su lista); con Codex, sus hilos y los creados aquí; con Antigravity, su app. Antes, Ctrl+IA deja de tener abierta la sesión para no escribir dos programas a la vez; si el agente está trabajando, la abre igualmente y te avisa de que no escribas allí hasta que termine. Si luego vuelves a escribir aquí, el agente recuerda lo que hiciste en la app, aunque no salga en la lista. No la uses en los dos sitios a la vez.

## Llevar tus sesiones a otro equipo

Para reunir en una sola lista las sesiones de varios equipos (o como copia de seguridad), tú llevas el archivo:

- **Exportar:** Archivo, «Exportar varias sesiones a un archivo...». «Qué sesiones» (Alt+Q): la elegida, las que se ven, todas o las de un grupo; «Sesiones que se exportarán» (Alt+S), con casillas, «Marcar todas» (Alt+T) y «Desmarcar todas» (Alt+D); «Incluir lo que hicieron las herramientas» (Alt+H, marcada; desmárcala para un archivo más pequeño y con menos datos privados). «Exportar...» (Alt+X) crea un .zip con una copia en Markdown de cada sesión que se lee sin Ctrl+IA. Trátalo como privado: puede llevar contraseñas que salieron en una orden.
- **Importar:** en el otro equipo, Archivo, «Importar sesiones...». Ctrl+IA dice cuántas son nuevas, actualizadas, sin cambios u omitidas (las exportadas desde este mismo equipo). Salen con el origen «Importada de» y en la vista «Importadas de otro equipo». Conservan la marca de favorita y los grupos. Se leen, se buscan, se agrupan, se exportan y se renombran; se siguen con «Continuar aquí» (ver «Continuar una sesión importada»). Importar otra vez no duplica. Archivo, «Borrar las sesiones importadas...» las quita todas y Mayús+Suprimir una.

Para llevarte todo Ctrl+IA a otro equipo, copia la carpeta del programa entera, con data.

## Los agentes

### Agentes instalados

Herramientas, «Agentes instalados...» comprueba cada agente de este equipo y dice cuántos hay. La lista «Agentes» (Alt+A) tiene «Estado» («Instalado», «No instalado», «Sin comprobar», «Comprobando…», «No se pudo comprobar»), «Versión», «Sesión» («Iniciada», con tu cuenta si se sabe; «No iniciada»; «Desconocida») y «Detalle»; «Descripción» (Alt+D) explica qué es, dónde está y cómo instalarlo.

- «Instalar» (Alt+I), si falta: explica lo que va a pasar y abre una consola con el instalador oficial (Claude Code y Antigravity, su instalador; Codex, Gemini, los adaptadores y los ACP conocidos salvo Goose, con npm; Ollama, con winget).
- «Iniciar sesión» (Alt+S), si está instalado: abre el agente en una consola para entrar con tu cuenta (con Codex, elige «Sign in with ChatGPT»).
- «Comprobar de nuevo (F5)» (Alt+N), «Más acciones…» (Alt+O; también Intro o Aplicaciones en la lista: además, «Página oficial» y, en Antigravity, «Reglas de permisos…», «Abrir Antigravity IDE» y «Abrir la app Antigravity») y «Cerrar» (Alt+C).

Al cerrar la consola, Ctrl+IA lo comprueba solo y dice cómo ha quedado, por ejemplo «Codex (OpenAI): sesión iniciada con ana@ejemplo.com». Si la sesión de Claude Code o de Codex usa una clave de API, el detalle te avisa de que se cobraría por uso. Si Claude Code está instalado a la antigua, con npm, Ctrl+IA no puede usarlo: escribe `claude install` en una consola (o `claude install --force`), o desinstálalo con `npm uninstall -g @anthropic-ai/claude-code` y pulsa «Instalar». Los agentes de otro equipo se comprueban en «Equipos por SSH».

### Qué puede hacer cada agente

| Función | Claude Code | Codex | Antigravity | Gemini y ACP | Ollama |
|---|---|---|---|---|---|
| Imágenes y archivos | Sí | Sí | Por su ruta | Si el agente los admite | Texto e imágenes (con visión) |
| Permisos y preguntas | Sí, con planes | Sí | «Permitir y reintentar» | Permisos | No hay |
| Comandos de barra | Sí | No | No | Los suyos | No |
| Enviar ahora | Sí | Sí | Detiene y envía | Detiene y envía | Sí |
| Añadir al turno en marcha | Sí | Sí | No | No | No |
| Continuar aquí | Sí | Sí | Solo las de su consola (sigue la misma) | No | Sí |
| Estilos de respuesta | Sí | No | No | No | No |
| Herramientas del agente (Ctrl+Alt+H) | Completo | Completo | Solo las de Ctrl+IA | Solo las de Ctrl+IA | No |
| Límites de uso (Ctrl+Mayús+Y) | Sí | Sí | Sí | No | No tiene |
| Por SSH | Estable | Estable | Experimental | Experimental (los conocidos y adaptadores, no) | Por túnel, experimental |

Todos usan las skills del catálogo, deshacer los cambios (Ollama no cambia archivos), la PR, las listas, los favoritos y la conversación entre sesiones. Si un agente no admite algo, Ctrl+IA te lo dice.

### Claude Code

El agente de Anthropic, el más completo en Ctrl+IA.

- **Modelos:** «Predeterminado de Claude Code», «Opus (el más capaz)», «Sonnet (equilibrado)», «Haiku (el más rápido)» y «Fable» (puede consumir créditos adicionales). **Esfuerzo:** «Predeterminado», «Bajo», «Medio», «Alto», «Muy alto» y «Máximo».
- **Modos:** «Preguntar antes de cada acción» (el de fábrica), «Aceptar las ediciones de archivos» (edita sin preguntar, pregunta antes de ejecutar órdenes), «Solo planificar» (te propone un plan sin cambiar nada), «Automático» (solo pregunta lo dudoso), «No preguntar: denegar lo dudoso» (hace lo que no necesita permiso o permiten tus reglas y deniega lo demás) y «Sin preguntas (peligroso)».
- **Permisos:** «Permitir una vez»; «Permitir durante esta sesión» (si la acción trabaja fuera de la carpeta, guarda también esa carpeta, nunca un disco entero); «Permitir siempre en esta carpeta», cuando Claude Code lo propone (lo apunta él en .claude\settings.local.json del proyecto y no vuelve a preguntar en ninguna conversación de esa carpeta; se quita en «Configuración de los agentes», Claude Code, «Proyecto, solo tú»); «Permitir y aceptar todas las ediciones» (pasa la sesión a ese modo); y «Denegar».
- **Herramientas externas (MCP y conectores de claude.ai):** pide permiso por cada una, con su servidor y los datos que recibirá; además puedes «Permitir todo «servidor» durante esta sesión» o «… siempre en esta carpeta». En «Solo planificar» pregunta siempre por las que pueden cambiar algo. Si una regla dice «preguntar siempre» o tu organización lo exige, solo hay «Permitir una vez» y «Denegar».
- Si Claude termina solo una tarea que dejó en segundo plano, puede responder sin que le escribas.
- Se queda abierto entre mensajes (15 minutos de fábrica) y espera a sus herramientas externas antes de responder; si Claude programa volver a algo más tarde, Ctrl+IA te lo dice y lo mantiene abierto. Con «Dejar que Claude use Chrome» puede usar tu Chrome con la extensión de Claude, pidiendo permiso (no por SSH) (el panel de navegador y el uso del ordenador de su app de escritorio solo existen dentro de ella). Todo en Preferencias, Agentes.
- **Siempre con tu suscripción:** si Claude Code fuera a usar una clave de API (que se cobra por uso), Ctrl+IA detiene el mensaje y te avisa; para pagar por uso, actívalo en Preferencias, Agentes. Otros avisos: que ha compactado la conversación, que reintenta por un problema de conexión, o que su versión es antigua (escribe `claude update` en una consola).

### Codex

El agente de OpenAI, con tu cuenta de ChatGPT. Se instala con la app Codex para Windows (Ctrl+IA encuentra su programa) o, con Node.js, desde «Agentes instalados».

- **Modelos:** «Predeterminado de Codex» y su lista real. **Esfuerzo:** «Predeterminado del modelo», «Bajo», «Medio», «Alto» y «Muy alto».
- **Modos:** «Solo lectura», «Trabajar en la carpeta del proyecto» (de fábrica; pregunta antes de salir de ella o usar Internet), «Carpeta del proyecto sin preguntar» (lo que no puede, no lo hace) y «Acceso total al equipo (peligroso)».
- **Permisos:** «Codex quiere ejecutar una orden» o «… modificar archivos» («Permitir una vez», «Permitir durante esta sesión» solo en los archivos, «Denegar» y «Denegar y detener el turno») y «Codex pide permisos adicionales» («Permitir en este turno», «Permitir durante esta sesión» y «Denegar»). El «Permitir durante esta sesión» de archivos y permisos adicionales lo recuerda Codex hasta cerrar la sesión: no sale en Permisos de la sesión ni se quita al cambiar de modo.
- **«Permitir «git commit» con cualquier argumento durante esta sesión»:** sale con una orden sencilla. Esa regla la guarda Ctrl+IA y contesta por ti a las siguientes iguales, solo en la carpeta raíz de la sesión y nunca con órdenes encadenadas, redirigidas, con variables o con intérpretes; con git sigue preguntando si fuerza, borra o va a otra carpeta o repositorio (un git push normal sí entra). Ojo: si Codex pedía salir de su aislamiento, esas órdenes se ejecutarán con acceso a todos tus archivos e Internet sin preguntarte.
- **Herramientas externas:** «Permitir una vez», «Permitir durante esta sesión», «Permitir siempre» (lo guarda Codex en su configuración), «Denegar» y «Cancelar». Si una no arranca o necesita iniciar sesión, te lo dice una vez con la orden que lo arregla (por ejemplo `codex mcp login github`). Si pide algo que Ctrl+IA no sabe rellenar, solo ofrece «Rechazar» o «Cancelar».
- Si el modelo de una conversación ya no existe, el error te pide elegir otro en Opciones de la sesión; al continuarla aquí, Ctrl+IA usa uno de la lista de Codex, te lo dice y lo recuerda. Si Codex trabaja con menos permisos de los pedidos (por ejemplo, porque el aislamiento de Windows no está preparado), te avisa una vez. Si no se sabe si recibió un mensaje, no lo reenvía, para no repetir una orden.

### Antigravity

El entorno de Google, sustituto de Gemini CLI para suscripciones personales. Ctrl+IA conversa con **Antigravity CLI** (agy); Ctrl+Mayús+G abre la carpeta en **Antigravity IDE** y Ctrl+O en su **app de escritorio**.

- **Modelos:** «Predeterminado de Antigravity» y los que anuncia tu Antigravity CLI (por ejemplo «Gemini 3.8 Flash (Medium)»). Si no se le puede preguntar, salen «Gemini Flash (rápido)», «Gemini Pro (el más capaz, más lento)» y «Gemini Flash Lite (el más ligero)», que solo aceptan sus versiones anteriores a la 1.4.4; si una sesión usa un modelo que ya no reconoce, Ctrl+IA te lo dice y sigue con el predeterminado. **Esfuerzo:** «Predeterminado», «Bajo», «Medio» y «Alto».
- **Modos:** «Revisar (recomendado)» (edita los archivos del proyecto; las órdenes de terminal se deniegan salvo las permitidas), «Con mis reglas» (como «Revisar», aplicando también las reglas permitidas en la sesión), «Aislado (sandbox)» (en un entorno aislado que limita lo que toca) y «Sin restricciones (peligroso)». Al cambiar modelo, esfuerzo o modo, reabre Antigravity antes del siguiente mensaje sin perder la conversación.
- **Permitir y reintentar:** desde Ctrl+IA, Antigravity no puede preguntarte: lo no permitido lo deniega y sigue. Ctrl+IA te avisa de cada denegación («Antigravity no pudo ejecutar git status porque necesita permiso…»); con Ctrl+Mayús+A ves qué no pudo hacer y eliges «Permitir y reintentar», «No permitir» o, con herramientas externas, «Permitir todo el servidor y reintentar». La regla se guarda en la configuración de Antigravity CLI y vale en todas tus sesiones, también fuera de Ctrl+IA.
- **Reglas de permisos de Antigravity** (Herramientas, o «Más acciones…» en Agentes instalados): «Reglas permitidas» (Alt+R), «Añadir…» (Alt+A), «Quitar» (Alt+Q o Suprimir) y «Cerrar». Se guardan al momento. Tipos: «Ejecutar órdenes que empiezan por…» (git permite todas las de git), «… que cumplen una expresión regular…», «Escribir en la carpeta o archivo…», «Usar las herramientas de un servidor MCP…» y «Regla escrita a mano»; «Explicación y ejemplo» dice qué permite.
- Recibe los adjuntos por su ruta. «Continuar aquí» sigue la misma conversación de Antigravity CLI (no las de su app); si no la encuentra, empieza una nueva y te avisa.

### Gemini CLI y otros agentes ACP

ACP es un protocolo abierto con el que Ctrl+IA conversa con muchos agentes. Con ellos, los modelos y modos son los que anuncia el propio agente, así que antes de la primera conversación puede salir solo «Predeterminado del agente»; no tienen esfuerzo. Algunos no saben retomar una conversación: al volver a abrirla empiezan una nueva y Ctrl+IA te lo dice.

- **Gemini CLI:** necesita licencia de empresa (Gemini Code Assist) o clave de API de pago (Google AI Studio o Vertex AI); Ctrl+IA sí le pasa GEMINI_API_KEY, GOOGLE_API_KEY o las de Vertex. Instala Node.js, pulsa «Instalar» en Agentes instalados y configura el acceso (la variable GEMINI_API_KEY o «Iniciar sesión»). Sus permisos suelen ser «Permitir una vez», «Permitir siempre», «Denegar» y «Denegar siempre».
- **Adaptadores ACP** (Preferencias, Agentes, «Mostrar los adaptadores ACP», y reiniciar): otra forma, avanzada, de usar Claude Code y Codex (oficiales) o Antigravity (de la comunidad, experimental). Necesitan Node.js.
- **Otros agentes ACP conocidos** («Mostrar otros agentes ACP conocidos», y reiniciar): OpenCode, Goose, GitHub Copilot CLI, Qwen Code, Kilo Code y Cline, con su cuenta, sus modelos gratuitos u Ollama. «Instalar» los instala con npm (Goose, aparte; su descripción dice cómo) y conviene abrirlos una vez en una consola para iniciar sesión. Ctrl+IA no les pasa claves de API.
- **Agentes ACP personalizados** (Herramientas): en «Lista de agentes» (Alt+L), Intro o F2 editan y Suprimir quita; «Añadir nuevo...» (Alt+N) pide «Nombre» (Alt+N), «Orden que lo arranca» (Alt+O; por ejemplo, la ruta de su .exe y --acp; no se admiten .cmd, .bat ni .ps1: para Node.js, la ruta de node.exe y la del .js) y «Carpeta desde la que se lanza (opcional)» (Alt+D); las rutas con espacios, entre comillas. «Quitar» (Alt+Q) pregunta antes. Nada se guarda hasta «Aceptar», y se aplica al reiniciar. Hay una lista de agentes ACP en agentclientprotocol.com.

### Ollama

Un programa gratuito que ejecuta modelos de IA en tu equipo. Ctrl+IA conversa con ellos como con los agentes, pero **solo es chat**: no lee ni cambia archivos ni ejecuta órdenes, así que su modo es «Solo conversación (sin herramientas)» y la carpeta solo sirve para ordenar. Todo se queda en tu equipo y no cuesta nada, aunque los modelos pequeños son menos capaces. Viene visible (se oculta en Preferencias, Agentes).

- **Instalar:** desde ollama.com o «Instalar» en Agentes instalados; ábrelo (queda junto al reloj) y descarga un modelo en una consola, por ejemplo `ollama pull llama3.2`. Después, F5 en Agentes instalados, cuya descripción dice si Ollama está en marcha y qué modelos tienes.
- **Modelo:** los descargados, con su tamaño; «Predeterminado: el último modelo descargado». Los «en la nube de Ollama» se ejecutan en sus servidores con una cuenta de Ollama (`ollama signin`). La primera respuesta tarda mientras carga el modelo.
- **Esfuerzo:** «Predeterminado del modelo», «Sin razonar» y «Razonar» (qwen3, deepseek-r1) o razonamiento bajo, medio y alto (gpt-oss); si el modelo no lo admite, te avisa.
- **Adjuntos:** los de texto se copian en tu mensaje (si son largos, el principio); las imágenes solo las entienden los modelos con visión (llava, gemma3, llama3.2-vision), hasta 20 MB; PDF o Word no.
- Ctrl+IA guarda sus conversaciones en data\ollama y le pasa los últimos 400 mensajes; una imagen solo la ve en el mensaje en que la adjuntas. Detener corta al momento; la cola y Enviar ahora funcionan igual.
- **En otro equipo:** por SSH y cifrado (ver el ejemplo de Ollama en «Agentes en otro equipo por SSH») o en tu red, escribiendo su dirección en Preferencias, Agentes (sin cifrar). Si quieres un agente que trabaje con archivos usando modelos de Ollama, usa OpenCode, Goose o Cline.

## Agentes en otro equipo por SSH

Puedes tener los agentes y tus proyectos en un Mac o un Linux y manejarlos desde Ctrl+IA en Windows con tu lector. Ctrl+IA entra por SSH (una conexión cifrada que trae Windows), arranca allí el agente y te enseña la conversación como cualquier otra. El agente trabaja en el otro equipo; Windows solo muestra y guarda la conversación.

- **Estables:** Claude Code y Codex. **Experimentales** (Ctrl+IA te lo advierte al crear la sesión): Gemini CLI, Antigravity CLI, los agentes ACP personalizados y Ollama por túnel. Los adaptadores y los otros agentes ACP conocidos no funcionan por SSH (añádelos como personalizados).
- Necesitas el cliente OpenSSH de Windows (ya instalado; si faltara, Configuración, Sistema, Características opcionales), el servidor SSH activado en el otro equipo, el agente instalado allí con su sesión iniciada y una clave SSH: Ctrl+IA nunca usa contraseñas.

### Preparar el otro equipo

1. **Activa SSH.** En un Mac: Ajustes del Sistema, General, Compartir, «Sesión remota» (debajo sale tu usuario y dirección, como «ssh ana@192.168.1.40»). En Linux: `sudo apt install openssh-server` y `sudo systemctl enable --now ssh` (usuario: `whoami`; dirección: `hostname -I`).
2. **Crea tu clave en Windows** (en PowerShell): `ssh-keygen -t ed25519`, aceptando con Intro las tres preguntas (sin frase de paso; con ella, tendrías que cargarla con `ssh-add` cada vez que entras en Windows).
3. **Cópiala al otro equipo** con esta orden en PowerShell (cambia el usuario y la dirección):

```
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh ana@192.168.1.40 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```

La primera vez escribe yes y la contraseña de tu usuario allí (es la única vez). `ssh ana@192.168.1.40 echo hola` tiene que responder «hola» sin pedir contraseña.

### Añadir y comprobar el equipo

Herramientas, «Equipos por SSH...»: «Equipos» (Alt+E; Intro o F2 editan, Suprimir quita, F5 comprueba), «Añadir nuevo...» (Alt+N), «Comprobar (F5)» (Alt+M), «Abrir una consola en el equipo» (Alt+O), «Quitar» (Alt+Q), «Resultado de la comprobación» (Alt+R) y «Aceptar» o «Cancelar». Cada equipo tiene:

- «Nombre» (Alt+N), «Destino» (Alt+D: usuario@dirección o un nombre de tu .ssh\config), «Puerto» (Alt+P; 22) y «Sistema del otro equipo» (Alt+S; macOS o Linux).
- Las rutas de Claude Code, Codex, Gemini CLI y Antigravity CLI (Alt+R, O, G, I): vacías si el agente responde por su nombre en la terminal del otro equipo; si no, su ruta completa (la dice `which claude`).
- «Variables» (Alt+V): una por línea, NOMBRE=valor, para los agentes de ese equipo. Ctrl+IA quita allí las claves de pago de Claude Code y Codex (a Gemini se las deja); no escribas claves aquí.
- «Usar el Ollama de este equipo por un túnel SSH (experimental)» (Alt+U; solo uno puede tenerla) y «Mostrar en la lista las sesiones que ya tiene este equipo» (Alt+M, marcada).

F5 comprueba, línea a línea, la conexión sin contraseña, cada agente con su ruta y versión y si Claude Code y Codex tienen la sesión iniciada, con la solución de cada problema (puede tardar más de un minuto; la prueba de Claude gasta muy poco). «Abrir una consola en el equipo» abre una consola ya conectada para instalar los agentes e iniciar sesión; al acabar, `exit`.

Después, en «Nueva sesión», elige el agente y el «Equipo» (Alt+Q), y escribe la carpeta del otro equipo con barras normales (/Users/ana/proyectos/web, /home/ana/web o ~/web). «Examinar» y la copia aislada no están disponibles.

### Instalar los agentes allí

Todo se escribe en la consola del otro equipo («Abrir una consola en el equipo»).

- **Claude Code (estable):** `curl -fsSL https://claude.ai/install.sh | bash`. Para iniciar sesión en un Mac (el llavero está bloqueado por SSH): escribe `claude` y, dentro, `/login`, abre la dirección en el navegador de Windows y pega el código; o, delante del Mac, `claude setup-token` y guarda el token allí con `echo 'export CLAUDE_CODE_OAUTH_TOKEN=tu-token' >> ~/.zshenv` y `chmod 600 ~/.zshenv`. En Linux basta con `claude` y `/login`. Para actualizarlo, `claude update`.
- **Codex (estable):** `npm install -g @openai/codex` (en un Mac, también `brew install codex`). Para que guarde la sesión en un archivo, pon `cli_auth_credentials_store = "file"` al principio de ~/.codex/config.toml (o créalo con `mkdir -p ~/.codex && printf 'cli_auth_credentials_store = "file"\n' > ~/.codex/config.toml`). Inicia sesión con `codex login --device-auth`, abriendo la dirección en Windows.
- **Gemini CLI (experimental):** `npm install -g @google/gemini-cli`; en las «Variables» del equipo, `GEMINI_FORCE_FILE_STORAGE=true` y `GEMINI_CLI_TRUST_WORKSPACE=true`; inicia sesión con `gemini` (la clave, mejor en ~/.profile o ~/.zshenv).
- **Antigravity CLI (experimental):** instálalo según antigravity.google y entra con `agy` (si se queja del llavero en un Mac, `security unlock-keychain` antes). «Permitir y reintentar» escribe la regla en el equipo, no en Windows.
- **Agente ACP personalizado (experimental):** en el otro equipo se ejecuta el nombre del programa de su orden, sin la ruta ni la extensión de Windows (C:\…\opencode.exe acp pasa a `opencode acp`). Si no lo encuentra, añade su carpeta al PATH en ~/.zshenv o ~/.profile.
- **Ollama por túnel (experimental):** en un equipo más potente, `curl -fsSL https://ollama.com/install.sh | sh` y `ollama pull llama3.2`; marca en él la casilla de Ollama. Todas tus sesiones de Ollama irán allí por un túnel cifrado, que Ctrl+IA abre cuando hace falta y cierra al salir.

### Qué cambia en una sesión de otro equipo

- **Adjuntos:** Claude Code y los ACP reciben las imágenes; los demás archivos, y todos los de Codex y Antigravity, no se envían y el mensaje lo dice. Cópialos antes con `scp` y dile al agente dónde están.
- Abrir la carpeta en el Explorador, en el editor, en Antigravity IDE o en la app del agente no sirve (usa «Abrir una consola en el equipo»). «Lo que el agente sabe de ti», «Configuración de los agentes», «Agentes instalados» y Ctrl+/ son los de Windows; los archivos del otro equipo se cambian en su consola.
- «Continuar aquí» sigue en el mismo equipo. Si se corta la conexión, el turno acaba con error: «Volver a enviar...». Si quitas un equipo, sus sesiones quedan en la lista pero no se continúan hasta que añadas otro con el mismo nombre.

### Sesiones que ya tiene el otro equipo

Las sesiones de Claude Code (terminal y app) y los hilos de Codex del otro equipo salen en tu lista, con el equipo en el Origen («Terminal en Mac del estudio (SSH)»), en la columna Equipo, en Propiedades y en las vistas «Solo de este equipo» y «De otros equipos por SSH»; el filtro también las encuentra por el nombre del equipo. Ctrl+IA las lee por SSH al arrancar, cada medio minuto y con F5, solo trae lo que cambia y nunca hace esperar a la lista; al abrir una, trae sus mensajes y sigue los nuevos. Son de solo lectura: escribir o «Continuar aquí» crea una copia en el mismo equipo y carpeta. No se busca dentro de ellas con Ctrl+Mayús+S y no salen las de Cowork, Antigravity ni Gemini (de Claude, las 400 más recientes). Ocultar en Preferencias, Historial, las de Claude o Codex de otros programas las oculta también aquí. Si el equipo no responde, te lo dice una vez, muestra lo último leído y lo reintenta cada dos minutos. Para no leer un equipo, desmarca su casilla en «Equipos por SSH».

### Seguridad y problemas por SSH

Ctrl+IA solo entra con tu clave, nunca pide ni guarda contraseñas y nunca acepta por su cuenta la huella de un equipo nuevo. Tus mensajes viajan cifrados y nunca van en la orden de arranque. Problemas habituales (F5 los explica):

- «El otro equipo no acepta tu clave SSH»: repite el paso 3 y revisa el usuario del destino.
- «Es la primera conexión (o la huella del equipo ha cambiado)»: abre una consola en el equipo y escribe yes; si cambió sin motivo, no sigas.
- «No se encuentra el equipo» (prueba con la dirección IP), «rechaza la conexión» (activa SSH) o «no responde» (encendido, despierto y en la misma red; un Mac dormido no responde, y en sus ajustes puedes impedir que se duerma).
- «No se encuentra «claude» en el otro equipo»: escribe su ruta o añádela al PATH (pasa a menudo con nvm).
- Sin sesión iniciada de Claude Code o Codex: ver «Instalar los agentes allí».

## Uso, contexto y límites

El **contexto** es la memoria de trabajo del agente (la conversación, los archivos leídos, los resultados de las órdenes), medida en tokens. Cuando se llena, el agente la resume (la compacta) y puede olvidar detalles antiguos; suena un sonido y Ctrl+IA te lo dice.

- La barra de estado dice el porcentaje usado. Al pasar del 80 %, Ctrl+IA avisa una vez por sesión cada vez que abres Ctrl+IA.
- **Ctrl+Mayús+U** («Uso y límites»): el contexto en porcentaje y en tokens, la entrada y la salida del último turno, su coste equivalente si el agente lo da (con suscripción no se cobra aparte) y los límites de tu cuenta.
- Cuando el límite de uso de Claude Code o de Codex pasa del 90 %, Ctrl+IA avisa con un sonido grave, y otra vez cada 5 puntos más. Los dos porcentajes se cambian en Preferencias, Anuncios (0 desactiva el aviso).

**Herramientas, «Límites de uso de tus cuentas...» (Ctrl+Mayús+Y)** pregunta a la vez, sin enviar mensajes ni gastar cuota, cuánto llevas gastado del límite de 5 horas y del semanal de cada cuenta y cuándo se reinician, en un texto con un encabezado por agente:

- **Codex:** cada límite y tu plan (si no contesta, el último dato que guardó, con su fecha).
- **Antigravity:** lo mismo por grupo de modelos (Gemini; Claude y GPT) y los límites semanales propios de algunos modelos. Necesita Antigravity CLI 1.1.11 o posterior (si no, pide `agy update`).
- **Claude Code:** la sesión de 5 horas, la semana y, si tu plan lo tiene, la de un modelo concreto, y debajo «Qué está gastando tu límite»: peticiones y sesiones de las últimas 24 horas y 7 días, el peso de los subagentes y del contexto, y qué subagentes y servidores MCP gastan más (traducido al español; lo que no reconoce lo deja en inglés y lo avisa).

Gemini CLI tiene una cuota diaria que solo se ve dentro de Gemini (/stats), y Ollama no tiene límites.

## Lo que el agente sabe de ti

Los agentes leen al empezar unos archivos con tus instrucciones (cómo trabajar, normas del proyecto) y algunos guardan su propia memoria. **Ctrl+Mayús+K** los muestra, primero los del agente de la sesión (sin sesión, los del predeterminado), también los que aún no existen:

- **Claude:** CLAUDE.md general, su configuración, agentes, habilidades y comandos; en el proyecto, CLAUDE.md, CLAUDE.local.md y la carpeta .claude; y su memoria automática.
- **Codex:** AGENTS.md general, su configuración, sus habilidades, sus comandos guardados y los AGENTS.override.md; en el proyecto, AGENTS.md y las habilidades de .agents.
- **Antigravity y Gemini:** GEMINI.md general y la configuración de Antigravity CLI; en el proyecto, GEMINI.md, AGENTS.md y las habilidades de .agents.

En «Archivos de instrucciones y memoria» (Alt+I), con archivo, agente, ámbito («Global» o «Proyecto»), si existe, cuándo se modificó y ruta: «Ver» (Alt+V o Intro), «Editar» (Alt+E, en tu editor), «Abrir la carpeta» (Alt+C), «Crear» (Alt+R, para un archivo de instrucciones que aún no existe; la memoria la crea el agente) y «Cerrar» (Alt+A). El menú contextual añade «Copiar la ruta» y «Ordenar por» (por agente, más recientes, más antiguos o por nombre), que se recuerda. Ctrl+IA solo escribe en ellos al crear uno.

## Configuración de los agentes

Cada agente guarda sus opciones (modelo inicial, permisos, su modo para lector de pantalla, días que guarda las conversaciones y muchas más) en un archivo que vale en la terminal, en su app y en Ctrl+IA. Herramientas, «Configuración de los agentes...» (**Ctrl+Mayús+W**) las cambia en una lista accesible, con la explicación de cada una y sin riesgo de estropear el archivo.

- **Archivos:** Claude Code, su settings.json con tres ámbitos: «Tu usuario (todas las carpetas)», «Proyecto, compartido» (va con el repositorio) y «Proyecto, solo tú» (manda sobre el compartido). Codex, config.toml de tu usuario o de un proyecto de confianza. Gemini CLI, settings.json de tu usuario o de un proyecto de confianza. Antigravity CLI, el settings.json de tu usuario (el IDE y la app se configuran en su ventana).
- **La ventana:** «Agente» (Alt+A; de entrada, el de la sesión, o Claude Code), «Ámbito» (Alt+M), «Carpeta del proyecto» (Alt+P) con «Examinar...» (Alt+X), «Archivo» (Alt+H: ruta, si existe o tiene un error, y una nota), «Categoría» (Alt+T), «Filtrar» (Alt+F) y «Opciones» (Alt+O): cada opción con su valor, categoría y clave. «Sin definir» dice el valor que usa el agente si no la pones; «(cuidado)» marca las que conviene pensar; las claves desconocidas salen en «Otras claves del archivo».
- **En la lista:** Intro cambia la opción en un cuadro con su explicación; Espacio activa o desactiva las de sí o no; Suprimir quita el valor; Ctrl+H abre la explicación; Ctrl+S guarda. Botones «Cambiar...» (Alt+C), «Quitar el valor» (Alt+Q), «Guardar» (Alt+G), «Abrir en el editor» (Alt+E, con el programa asociado de Windows), «Documentación» (Alt+D) y «Cerrar» (Alt+R o Escape). En las listas y variables de entorno: Insertar o «Nuevo...» añade, F2 cambia, Suprimir quita y, en las listas, Alt+Flecha arriba y abajo mueven. Las variables se escriben NOMBRE=valor.
- **Sin miedo:** nada se escribe hasta «Guardar» (lo cambiado dice «sin guardar», y Ctrl+IA pregunta al cerrar o al cambiar de agente, ámbito o carpeta); lo arriesgado se confirma («No» de fábrica); se conserva todo lo que no tocas, incluidos los comentarios de Codex (sus proyectos de confianza, servidores MCP, complementos y proveedores no se listan: se cambian en el editor); antes de escribir se guarda una copia en data\backups\agentes (las 20 últimas por nombre de archivo); y si el archivo tiene un error, no lo toca y te dice cuál.

**El modo de permisos inicial.** «Nueva sesión» propone el modo que diga la configuración del agente para esa carpeta: con Claude Code, «Modo de permisos inicial» (manda «Proyecto, solo tú», luego el compartido y luego tu usuario; «Automático» y «Saltarse los permisos» solo valen desde tu usuario); con Codex, «Sandbox» y «Cuándo pide permiso» («Solo lectura», «Acceso total» o «Nunca» proponen sus modos; si no dicen nada, «Trabajar en la carpeta del proyecto»); con Gemini, el modo con que abrió tu última conversación; con Antigravity, «Ejecución de órdenes y herramientas». Lo que elijas en «Nueva sesión» manda solo en esa sesión.

**Para usar los agentes en una terminal con tu lector** (en Ctrl+IA no cambian nada): en Claude Code, «Modo lector de pantalla en la terminal» y «Reducir las animaciones»; en Gemini, «Modo lector de pantalla» y «Frases mientras piensa» desactivadas; en Codex, «Animaciones» desactivada y «Salida en bruto» activada; en Antigravity CLI, «Animación de progreso» desactivada.

## Avisos, voz, sonidos y notificaciones

Ctrl+IA habla por tu lector de pantalla o, sin él, con su propia voz, esperando un poco para no pisar al lector («Retardo de los anuncios»). Hay **anuncios** (como «Enviado» o la respuesta del agente) y **avisos importantes** (errores, peticiones del agente, contexto y límites), que según Preferencias, Anuncios, se dicen, salen en un cuadro o las dos cosas. Nada existe solo como voz: los avisos van a la línea braille (también con la voz silenciada), la barra de estado muestra el último, **Ctrl+Mayús+R** lo repite y **Ctrl+Mayús+H** abre el historial de los 200 últimos, con su hora.

- **Qué se lee de las respuestas** (Preferencias, Anuncios): «Completo» (la respuesta al llegar, de fábrica), «Resumen» (solo «Claude ha respondido en «Mi proyecto»») o «Silencio» (solo los avisos importantes y lo que pides tú). De las sesiones que no tienes abiertas solo se dice que han respondido, salvo con «Leer las respuestas de todas las sesiones».
- **Ctrl+Mayús+V** calla la voz propia de Ctrl+IA (también Flecha derecha, Ctrl+H y Alt+4); tu lector sigue funcionando y los avisos siguen en braille.
- **Sonidos:** sesión nueva, mensaje enviado, el agente usa una herramienta o cambia un archivo (estos dos, apagados de fábrica), termina de responder, termina otra sesión (más suave), te necesita, error, detenido, compactación y límite de uso. Un sonido nunca sustituye al texto. **F9** los activa o desactiva todos. Herramientas, «Sonidos...»: la lista «Sonidos» (Alt+S), donde Espacio activa o desactiva cada uno e Intro (o «Reproducir», Alt+R) lo prueba aunque esté apagado; Ctrl+A y Ctrl+Mayús+A, todos o ninguno; cada uno dice si suena «de Ctrl+IA», «propio» o «sin archivo». Se guarda al aceptar.
- **Tus propios sonidos:** «Abrir la carpeta de sonidos propios» (Alt+P), copia allí archivos WAV llamados nueva, enviado, herramienta, archivo, respuesta, segundo_plano, permiso, error, detenido, compactado o limite (.wav) y elige el esquema «Propio» en Preferencias, Sonidos. Los que falten suenan como siempre. El volumen de Ctrl+IA se aplica a los WAV de 16 bits.
- **Notificaciones de Windows,** cuando Ctrl+IA no es la ventana activa: «Al terminar y cuando un agente te necesita», «Solo cuando un agente te necesita» (de fábrica) o «Nunca». Salvo con «Nunca», también los errores y los cambios de las PR. Los avisos que te manda un agente con la herramienta de Ctrl+IA se notifican siempre.

## Preferencias

Ctrl+, o Herramientas, «Preferencias...». Nueve páginas, que se cambian con Ctrl+Tab y Ctrl+Mayús+Tab. Ctrl+H explica la opción enfocada. Botones «Restablecer valores de fábrica» (Alt+R; todas las páginas, tras preguntar, pero no guarda hasta «Aceptar» o «Aplicar»), «Aceptar» (Alt+A, también Intro), «Cancelar» (Alt+C o Escape; no deshace lo ya aplicado) y «Aplicar» (Alt+P). Si un valor no vale, Ctrl+IA te lleva a él sin guardar nada. Salvo que se diga otra cosa, todo se aplica al momento o desde el siguiente mensaje.

### General

- «Idioma de la interfaz» (Alt+I): «Automático» (de fábrica), «Español» o «Inglés». Al reiniciar.
- «Confirmar antes de salir» (Alt+F), activada. «Minimizar a la bandeja del sistema» (Alt+M), desactivada. «Anunciar el arranque» (Alt+Q), activada. «Abrir la última sesión al iniciar» (Alt+L), activada.
- «Buscar actualizaciones automáticamente» (Alt+B), activada. Al reiniciar.

### Agentes

- «Agente predeterminado» (Alt+G): Claude Code (cambia solo al crear una sesión con otro).
- «Carpeta de trabajo predeterminada» (Alt+T) y «Examinar...» (Alt+X): se propone si no hay ninguna reciente.
- «Editor de código (ruta del programa)» (Alt+E): para abrir la carpeta en el editor, Ctrl+E en las listas y editar instrucciones y SKILL.md. Vacío: Visual Studio Code si está (para editar un archivo, el editor que Windows tenga asociado o el Bloc de notas).
- «Mostrar los adaptadores ACP (avanzado)» (Alt+D) y «Mostrar otros agentes ACP conocidos (OpenCode, Goose, Copilot...)» (Alt+N), desactivadas. Al reiniciar.
- «Guardar puntos de restauración antes de cada mensaje» (Alt+S), activada: ver «Deshacer los cambios de archivos».
- «Mostrar Ollama (modelos locales, solo chat)» (Alt+O), activada, al reiniciar. «Dirección del servidor de Ollama» (Alt+V): vacía usa OLLAMA_HOST o tu equipo; un equipo por SSH con Ollama marcado manda sobre ella.
- «Dejar que Claude lleve una lista de tareas» (Alt+L), activada: con todos los modelos (Claude Code 2.1.233 o posterior).
- «Esperar a que se conecten las herramientas MCP de Claude antes de cada mensaje» (Alt+H), activada: así no dice que se han desconectado, aunque cada respuesta tarde algo más en empezar.
- «Dejar que Claude use Chrome (Claude en Chrome)» (Alt+U), activada: ver «Claude Code».
- «Mantener Claude Code abierto entre mensajes» (Alt+M): «No: arrancarlo en cada mensaje», 5, 15 (de fábrica) o 30 minutos o 1 hora. Responde antes porque no vuelve a arrancar; cada sesión abierta ocupa algo de memoria. Se reinicia solo, sin perder la conversación, si cambias el modelo, el esfuerzo, el estilo, las skills del catálogo o estas opciones, o si quitas permisos.
- «Permitir que Claude use una clave de API (puede costar dinero)», desactivada. Al reiniciar.
- «Dar a los agentes las herramientas de Ctrl+IA (avisarte, dar la sesión por terminada, renombrarla)» (Alt+I), activada; vale cuando el agente de cada sesión vuelve a arrancar.

### Historial

Activadas: «Mostrar las sesiones de Claude Code de otros programas» (Alt+L), «Mostrar las conversaciones de Codex» (Alt+O), «… de Antigravity (app y CLI)» (Alt+N), «Mostrar los chats de la app de Ollama» (Alt+M) y «Seguir en vivo las sesiones de otros programas» (Alt+V). Desactivadas: «Al continuar una sesión de otro programa, seguir la original sin copiarla» (Alt+S; ver «Continuar aquí»), «Mostrar también las sesiones ocultas» (Alt+U) y «… archivadas» (Alt+H).

### Mensajes

«Mostrar las acciones del agente (herramientas)» (Alt+H), activada, como Ctrl+T. «Guardar y mostrar el razonamiento» (Alt+Z), desactivada. «Abrir los mensajes con formato» (Alt+F), activada. «Mensajes que se cargan al abrir una sesión» (Alt+M): los últimos 50, 100 (de fábrica), 250 o 500, o todos. «Cómo se leen las líneas cambiadas de un archivo» (Alt+L): «Con palabras» («Línea 12, añadida: …», de fábrica), «Con palabras cortas» («Más 12») o «Con los signos + y −», como en Git (solo se oyen si tu lector lee esa puntuación).

### Lectura de listas

- Al ir a izquierda y derecha, «decir el número de fila» (Alt+N), «decir el nombre de la columna» (Alt+L) y «decir el contenido de la celda» (Alt+T), activadas.
- Qué columnas se leen al subir y bajar, en cuatro botones que abren un cuadro de casillas (Espacio; Alt+Flecha arriba y abajo para ordenar; «Restablecer»; «Ejemplo de lo que se leerá», Alt+E): lista de sesiones (Alt+S), mensajes (Alt+M; también «Número»), grupos (Alt+G; nombre, sesiones, descripción, agente y carpeta) y sesiones de un grupo (Alt+U).
- «Decir «Favorita» delante del título» (Alt+F) y «Añadir «respuesta nueva» al estado» (Alt+E), activadas. «Formato de las fechas» (Alt+H): «Relativas» (de fábrica) o «Completas, con día y hora».
- «Mostrar los indicadores de cada mensaje» (Alt+I) y uno por uno, todos activados: preguntas, permisos y planes, «Con error», «Detenido», adjuntos, bloques de código (Alt+D), enlaces, archivos cambiados, «Largo» (más de 1500 caracteres) y «Favorito».
- «Avisar cuando una vista o un grupo está vacío» (Alt+V), activada.

### Anuncios

- «¿Qué se lee de las respuestas?» (Alt+D): «Completo». «Leer las respuestas de todas las sesiones» (Alt+T), desactivada.
- «Anunciar cada acción del agente (herramientas)» (Alt+H), «Anunciar el progreso de la lista de tareas del agente» (Alt+G) y «Leer mis propios mensajes al enviarlos» (Alt+V, hasta 300 caracteres), desactivadas.
- «Avisos importantes» (Alt+M): «Por voz» (de fábrica), «En un cuadro de mensaje» o «Las dos cosas».
- «Enviar los avisos también a la línea braille» (Alt+B), «Mostrar en braille los avisos aunque la voz esté silenciada» (Alt+S) y «Escribir el último aviso en la barra de estado» (Alt+E), activadas.
- «Palabras curiosas en la barra de estado mientras el agente escribe» (Alt+Z), desactivada.
- «Retardo de los anuncios (milisegundos)» (Alt+L): de 0 a 600; 120.
- «Avisar cuando el contexto pase del (por ciento)» (Alt+O): 80. «Avisar cuando el límite de uso pase del (por ciento)» (Alt+U): 90. 0 desactiva.
- «Avisar cuando las comprobaciones de la PR de una sesión fallen o pasen» (Alt+I), activada.
- «Notificaciones de Windows» (Alt+N): «Solo cuando un agente te necesita».

### Sonidos

«Sonidos activados» (Alt+S, como F9), «Esquema de sonidos» (Alt+E: «Ctrl+IA» o «Propio (carpeta de sonidos propios)»), «Volumen de los sonidos (por ciento)» (Alt+V; 100; 0 los calla) y una casilla por sonido: crear una sesión (Alt+N), enviar (Alt+J), herramienta (Alt+H), cambio de archivo (Alt+I), termina de responder (Alt+D), termina otra sesión (Alt+G), te necesita (Alt+M), errores (Alt+O), detenido (Alt+T), compactación (Alt+U) y límite de uso (Alt+L). Todas activadas menos herramienta y cambio de archivo.

### Ventana

«Mostrar la lista de sesiones» (Alt+S), activada; «Panel de lectura del mensaje seleccionado» (Alt+L), desactivada; «Distribución del espacio» (Alt+D): «Equilibrada»; «Resaltar la zona que tiene el foco» (Alt+Z), activada (las cuatro, también en Ver, «Distribución de la ventana»); y «Tema de colores» (Alt+T): «Claro» (de fábrica), «Oscuro» o «Como Windows», en toda la aplicación, con los colores de alto contraste si Windows los usa. El tema, al reiniciar.

### Avanzado

«Nivel de detalle del registro» (Alt+N): «Normal» (de fábrica), «Depuración» (solo si te lo piden para investigar un fallo), «Solo avisos» o «Solo errores». Al reiniciar.

## Ayuda dentro del programa

- **F1** abre este manual, **Ctrl+F1** la lista de teclas y Ayuda, «Novedades de la versión», los cambios de cada versión. Tras actualizar, Ctrl+IA te muestra una vez las novedades nuevas.
- **Ctrl+H** dice el nombre del control enfocado y para qué sirve.

El manual, las teclas, las novedades, los mensajes y las propiedades se abren en un visor que no bloquea la ventana principal, de dos formas:

- **Como página** (necesita el motor Edge de Windows): encabezados, listas y tablas reales; con NVDA o JAWS, H y Mayús+H saltan entre encabezados, 1 a 3 por nivel, T entre tablas y L entre listas. Los enlaces se abren en el navegador.
- **Como texto:** «Secciones» (Alt+S), con los encabezados, y «Contenido» (Alt+O); Intro en una sección salta a ella, y su menú tiene «Ir a la sección» y «Copiar el título». Las propiedades, el uso, el historial de avisos, los SKILL.md y lo de las listas se abren siempre así.

En las dos: «Buscar...» (Alt+B o Ctrl+F; da la vuelta al llegar al final), «Buscar siguiente» (Alt+I o F3) y «Cerrar» (Alt+C o Escape).

## Privacidad y datos

Ctrl+IA no tiene servidores para tus datos. Tus mensajes los envía el agente a su empresa (Anthropic, OpenAI, Google o la del agente ACP), igual que desde su programa; con Ollama no salen de tu equipo (salvo con un modelo en su nube o un Ollama de otro equipo), y por SSH viajan cifrados hasta el otro equipo. Ctrl+IA solo contacta con su servidor de actualizaciones y con GitHub para saber si hay versión nueva (se puede desactivar) y no guarda contraseñas ni credenciales: cada agente gestiona su inicio de sesión y SSH usa tu clave. Al escribir los registros, quita las claves y datos sensibles que reconoce.

Todo está en la carpeta data, junto a CtrlIA.exe (Herramientas, «Abrir la carpeta de datos»):

- **config:** tu configuración (con la lista de equipos por SSH, las novedades vistas y la versión omitida).
- **db:** tus sesiones, mensajes, plantillas, grupos, favoritos, catálogo de skills, importadas y marcas.
- **cache:** los puntos para deshacer cambios, las sesiones leídas de los equipos por SSH y lo que Ctrl+IA prepara para los agentes. Borrarla pierde los puntos.
- **skills:** las skills que creas en el gestor.
- **backups:** una copia de la base de datos antes de cada actualización que cambia su formato (las 10 últimas) y las copias de los archivos de configuración de los agentes.
- **logs:** los registros. **diagnostico:** los paquetes de diagnóstico. **ollama:** las conversaciones con Ollama. **temp:** temporales, como las imágenes pegadas. **sounds:** los sonidos (y **sounds\propios**, los tuyos).
- **docs, bin, locales, icons, skills_ejemplo y plugins** son del programa.

Si la configuración se daña, Ctrl+IA vuelve a su copia anterior; si se daña la base de datos, a su última copia, que puede ser antigua o no existir. Copia de vez en cuando la carpeta data. Las sesiones de otros programas no se guardan en Ctrl+IA: se leen cada vez sin modificarlas.

## Solución de problemas

- **No se encuentra el agente:** en Agentes instalados, lee su «Descripción», pulsa «Instalar», sigue la consola, ciérrala y pulsa F5; si sigue sin aparecer, reinicia Ctrl+IA.
- **No has iniciado sesión** («No iniciada», o el agente dice que la sesión caducó): «Iniciar sesión» en Agentes instalados.
- **Versión antigua** del agente (te lo dice, o falla al arrancar): actualízalo (Claude Code, `claude update`; los demás, «Instalar» otra vez o su actualizador) y F5.
- **Antigravity deniega acciones:** es lo normal desde Ctrl+IA. Ctrl+Mayús+A y «Permitir y reintentar», o añade reglas en «Reglas de permisos de Antigravity», o cambia su modo.
- **Codex no modifica archivos:** comprueba con Ctrl+Mayús+O que el modo no es «Solo lectura». Si falla su entorno aislado, actualiza Codex; como último recurso, «Acceso total al equipo», solo con copia de seguridad.
- **Claude se detiene por una clave de API:** tu equipo tiene una clave de Anthropic. Inicia sesión con tu suscripción o permite el pago por uso en Preferencias, Agentes.
- **Gemini CLI no funciona con tu cuenta de Google:** necesita licencia de empresa o clave de API de pago; con suscripción personal, usa Antigravity.
- **Ollama no responde:** «No se puede conectar con Ollama»: ábrelo (o `ollama serve`), o revisa su dirección o el equipo por SSH. «No hay ningún modelo instalado»: `ollama pull` y el nombre, y F5. Si tarda en empezar, está cargando el modelo.
- **Un agente en otro equipo no funciona:** «Equipos por SSH», elige el equipo y F5 (ver «Seguridad y problemas por SSH»).
- **No oigo la voz o los sonidos:** comprueba que no has silenciado la voz (Ctrl+Mayús+V), que «Qué se lee de las respuestas» no está en «Silencio», que tu lector no está en suspensión, F9, el volumen de Ctrl+IA y de Windows y la casilla de cada sonido. Si la voz pisa al lector, sube el retardo.
- **Una sesión no se puede escribir:** «sesión de solo lectura» es de otro programa o importada (al enviar te ofrece continuarla); «abre o crea una sesión primero»: Intro sobre una o Ctrl+N.
- **La lista no muestra lo que esperas:** mira la vista activa (el nombre de la lista la dice) y elige «Todas», quita el filtro con Escape, mira en «Terminadas», revisa Preferencias, Historial y pulsa F5.
- **El programa no arranca:** extrae el ZIP completo (no lo ejecutes desde dentro) y comprueba que la carpeta data se puede escribir; el motivo está en data\logs\arranque_fallido.log. Si volviste a una versión anterior, la base de datos de una más nueva no le deja arrancar.
- **«Error inesperado»:** el cuadro explica qué ha pasado en «Qué ha pasado» (Alt+Q), con «Copiar detalles» (Alt+C), «Abrir la carpeta de registros» (Alt+R) y «Aceptar» (Alt+A). El programa sigue funcionando.
- **Para pedir ayuda:** Herramientas, «Crear un paquete de diagnóstico...» prepara un ZIP con los registros de las tres últimas sesiones y datos del sistema (sin tus mensajes, sesiones, configuración ni claves) y abre su carpeta. Adjúntalo a tu mensaje de soporte.
- **Los registros** están en data\logs (Herramientas, «Abrir la carpeta de registros»): incidencias.log (solo avisos y errores, el primero que conviene mirar), uno por cada vez que abriste Ctrl+IA (las 10 últimas), arranque_fallido.log, fallo_nativo.log, actualizacion.log y la carpeta no_controladas (fallos graves; esta no va en el paquete de diagnóstico).

## Glosario

- **Agente:** un programa de IA que conversa y además lee y modifica los archivos de tu proyecto y ejecuta órdenes, como Claude Code o Codex.
- **Sesión:** una conversación con un agente en una carpeta de trabajo. **Turno:** lo que hace el agente desde que le escribes hasta que termina de responder.
- **Modelo:** el «cerebro» de IA que usa el agente. **Esfuerzo:** cuánto piensa antes de responder. **Modo de permisos:** qué puede hacer sin preguntarte.
- **Contexto:** la memoria de trabajo del agente, con un tamaño máximo. **Token:** la unidad en que se mide el texto, de unas pocas letras. **Compactar:** resumir la conversación para liberar contexto.
- **Comando de barra:** una orden para el agente que empieza por barra, como /compact. **Skill (habilidad):** una carpeta con un SKILL.md, las instrucciones que el agente sigue para una tarea concreta.
- **PR (pull request):** una propuesta de cambios en GitHub. **Comprobaciones (CI):** las pruebas automáticas que GitHub pasa a una PR.
- **Copia aislada (worktree):** una copia aparte de tu repositorio de git, con su propia rama. **Punto de restauración:** la foto de los archivos que Ctrl+IA guarda antes de cada mensaje.
- **MCP:** un protocolo para que los agentes usen herramientas y servicios externos. **ACP:** un protocolo abierto para que un programa como Ctrl+IA converse con agentes.
- **SSH:** una forma cifrada de entrar en otro equipo. **Clave SSH:** el par de archivos que te identifica (la parte pública se copia al otro equipo). **Túnel SSH:** un camino cifrado entre Windows y un programa del otro equipo, como Ollama.
- **CLI:** un programa de consola. **IDE:** un editor de código con herramientas para programar.
