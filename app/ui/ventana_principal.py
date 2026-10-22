"""Ventana principal de Ctrl+IA.

Tres zonas, de arriba abajo, sin botones a la vista (cada control visible es
una parada más del tabulador):

1. **Sesiones** (Alt+1): todas las conversaciones con agentes, propias y ajenas.
2. **Mensajes** (Alt+2): lo dicho en la sesión abierta, un mensaje por fila.
3. **Respuesta** (Alt+3): lo que escribes al agente (Control+Intro envía).

F6 y Mayúsculas+F6 recorren las zonas; Escape vuelve a la lista de sesiones.
Todas las acciones están en la barra de menús, en los menús contextuales
(tecla Aplicaciones) y en atajos de teclado.

La lógica vive repartida en mixins para que ningún archivo sea enorme:
``vp_sesiones`` (lista de sesiones y su organización), ``vp_mensajes``
(lista de mensajes y herramientas de lectura) y ``vp_conversacion`` (enviar,
detener, permisos, eventos de los agentes y anuncios).
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import wx

from app.core import tareas
from app.core.configuracion import Configuracion
from app.core.instancia_unica import ORDEN_ABRIR, ORDEN_MOSTRAR, InstanciaUnica, traer_al_frente
from app.core.version import APP_VERSION, NOMBRE_APLICACION
from app.services.almacen import Almacen
from app.services.gestor import GestorSesiones
from app.ui import avisos
from app.ui.accesibilidad import voz
from app.ui.accesibilidad.ayuda import describir_control_enfocado, habilitar_ayuda_contextual
from app.ui.accesibilidad.dialogos import mostrar_error, mostrar_mensaje
from app.ui.bandeja import IconoBandeja
from app.ui.comun import _
from app.ui.vp_conversacion import MixinConversacion
from app.ui.vp_disposicion import MixinDisposicion
from app.ui.vp_mensajes import MixinMensajes
from app.ui.vp_pr import MixinPR
from app.ui.vp_sesiones import MixinSesiones

log = logging.getLogger(__name__)

TAMANO_MINIMO = (640, 480)
#: Cada cuánto se relee la lista de sesiones (milisegundos).
INTERVALO_LISTA_MS = 5000
#: Cada cuánto se sigue en vivo una sesión externa abierta.
INTERVALO_SEGUIMIENTO_MS = 2000
#: Cada cuánto se mira si otra instancia ha dejado órdenes.
INTERVALO_BUZON_MS = 700

# Tabla de menús: (identificador, etiqueta con atajo, ayuda, método).
# «-» es un separador; ("submenu", etiqueta, método que crea el submenú).
MENUS: list[tuple[str, list[tuple]]] = [
	(_("&Archivo"), [
		("nueva", _("&Nueva sesión...\tCtrl+N"), _("Empieza una conversación nueva con un agente en una carpeta."), "accion_nueva_sesion"),
		("continuar", _("&Continuar aquí...\tCtrl+Shift+N"), _("Crea una copia de la sesión elegida en Ctrl+IA para seguir conversando."), "accion_continuar_aqui"),
		("nueva_desde_mensaje", _("Nueva sesión a partir del mensa&je...\tCtrl+Alt+N"), _("Empieza otra sesión con el mensaje elegido (o lo que hayas seleccionado de él) como punto de partida, para explorar esa idea aparte."), "accion_nueva_desde_mensaje"),
		("clonar", _("Clonar un repositorio de &GitHub..."), _("Descarga un repositorio de GitHub y empieza una sesión en él."), "accion_clonar_github"),
		("-",),
		("exportar", _("&Exportar la sesión...\tCtrl+E"), _("Guarda la conversación en Markdown, HTML o texto."), "accion_exportar"),
		("exportar_varias", _("Exportar &varias sesiones a un archivo..."), _("Guarda las sesiones que elijas, un grupo o todas en un archivo para leerlas en otro equipo."), "accion_exportar_varias"),
		("importar", _("I&mportar sesiones..."), _("Trae las sesiones de un archivo exportado en otro equipo, para leerlas y buscar en ellas."), "accion_importar"),
		("borrar_importadas", _("Borrar las sesiones im&portadas..."), _("Quita de Ctrl+IA todas las copias importadas de otros equipos."), "accion_borrar_importadas"),
		("carpeta", _("Abrir la carpeta en el E&xplorador\tCtrl+Shift+E"), _("Abre en el Explorador de Windows la carpeta de trabajo de la sesión."), "accion_abrir_explorador"),
		("abrir_app", _("Abrir en la app del &agente\tCtrl+O"), _("Abre la sesión en la app de escritorio de Claude o de Codex, o la carpeta en Antigravity. Las sesiones de Ctrl+IA también: la app de Claude las importa la primera vez."), "accion_abrir_en_agente"),
		("abrir_ide", _("Abrir la carpeta en Antigravity &IDE\tCtrl+Shift+G"), _("Abre la carpeta de la sesión en el editor Antigravity IDE de Google."), "accion_abrir_en_antigravity"),
		("editor", _("Abrir la carpeta en el editor de có&digo"), _("Abre la carpeta en el editor configurado en Preferencias."), "accion_abrir_en_editor"),
		("-",),
		("salir", _("&Salir"), _("Cierra Ctrl+IA. Si hay agentes trabajando, se te pedirá confirmación."), "salir"),
	]),
	(_("&Sesión"), [
		("enviar", _("&Enviar\tCtrl+Enter"), _("Envía lo que has escrito. Si el agente está trabajando, queda en cola."), "accion_enviar"),
		("enviar_ahora", _("Enviar &ahora\tCtrl+Shift+Enter"), _("Interrumpe al agente y le pasa tu mensaje en este mismo momento."), "accion_enviar_ahora"),
		("anadir_turno", _("Añadir al tur&no en marcha\tCtrl+Alt+Enter"), _("Mete tu mensaje en lo que está haciendo el agente sin detenerlo (Codex y Claude Code). Si no se puede entregar, vuelve al cuadro de respuesta."), "accion_anadir_al_turno"),
		("detener", _("&Detener\tCtrl+."), _("Detiene lo que esté haciendo el agente. Los mensajes en cola vuelven al cuadro de respuesta."), "accion_detener"),
		("responder", _("&Responder al agente...\tCtrl+Shift+A"), _("Abre la petición de permiso o la pregunta pendiente del agente."), "accion_responder"),
		("-",),
		("comando", _("Insertar un &comando o habilidad...\tCtrl+/"), _("Elige un comando de barra del agente (como /compact) para insertarlo."), "accion_comando"),
		("adjuntar", _("Ad&juntar archivos...\tCtrl+Shift+F"), _("Adjunta archivos o imágenes al próximo mensaje."), "accion_adjuntar"),
		("pegar_adjunto", _("Adjuntar lo copiado (imagen o archi&vos)\tCtrl+Alt+V"), _("Adjunta al próximo mensaje la imagen o los archivos que hayas copiado, aunque lo copiado traiga también texto."), "accion_pegar_adjunto"),
		("plantillas", _("&Plantillas de mensajes...\tCtrl+Shift+P"), _("Mensajes guardados para reutilizar."), "accion_plantillas"),
		("reenviar", _("Enviar la respuesta a otra sesión...\tCtrl+Alt+R"), _("Pasa la respuesta elegida, o la última del agente, a otra sesión de Ctrl+IA de cualquier agente, para que la revise o la conteste. Puedes cambiarla antes de enviarla."), "accion_reenviar"),
		("conversacion_sesiones", _("Conversación automática entre sesiones...\tCtrl+Alt+Shift+R"), _("Dos sesiones de Ctrl+IA, de cualquier agente, se hablan solas unos turnos: la abierta recibe la tarea y cada respuesta pasa a la otra. Los permisos y las preguntas te siguen llegando a ti."), "accion_conversacion_sesiones"),
		("detener_conversacion_sesiones", _("Detener la conversación entre sesiones"), _("Para la conversación automática: la respuesta del turno en marcha ya no se pasa a la otra sesión. Control+punto en una de las dos también la detiene."), "accion_detener_conversacion_sesiones"),
		("ver_conversacion_sesiones", _("Ver la conversación entre sesiones\tCtrl+Alt+Shift+C"), _("Muestra la conversación automática, en marcha o la última, con las respuestas de las dos sesiones intercaladas y quién dice qué."), "accion_ver_conversacion_sesiones"),
		("citar", _("Citar el mensaje o el fra&gmento en la respuesta\tCtrl+R"), _("Añade al cuadro de respuesta, como cita, el mensaje elegido o lo seleccionado en el panel de lectura, para contestar punto por punto."), "accion_citar"),
		("deshacer_archivos", _("Deshacer los cam&bios de archivos desde el mensaje...\tCtrl+Alt+Z"), _("Devuelve los archivos de la carpeta a como estaban antes de tu mensaje elegido (o del último tuyo): enseña antes qué pasará con cada archivo. Con el agente parado."), "accion_deshacer_archivos"),
		("rehacer_archivos", _("Rehacer los cambios de archivos deshechos...\tCtrl+Alt+Shift+Z"), _("Vuelve a poner los cambios del último deshacer de la sesión, como los dejó el agente."), "accion_rehacer_archivos"),
		("-",),
		("ajustes", _("&Opciones de la sesión...\tCtrl+Shift+O"), _("Cambia el título, el modelo, el esfuerzo y el modo de permisos de la sesión."), "accion_ajustes_sesion"),
		("permisos", _("Permisos de &la sesión...\tCtrl+Shift+M"), _("Lo que el agente puede hacer sin preguntarte en esta sesión: verlo y quitarlo."), "accion_permisos_sesion"),
		("estado_turno", _("Estado del &turno\tCtrl+Shift+T"), _("Dice qué está haciendo el agente y desde cuándo."), "accion_estado_turno"),
		("uso", _("&Uso y límites...\tCtrl+Shift+U"), _("Cuánto contexto y cuánto de tu límite se ha usado."), "accion_uso"),
		("propiedades", _("Propiedade&s...\tAlt+Enter"), _("Todos los datos de la sesión: agente, carpeta, identificadores, fechas."), "accion_propiedades"),
		("submenu", _("PR y co&mprobaciones"), "crear_menu_pr"),
		("herramientas_agente", _("&Herramientas del agente...\tCtrl+Alt+H"), _("Los servidores MCP y conectores, complementos, habilidades y herramientas que tiene cargados el agente en esta sesión, con su estado."), "accion_herramientas_agente"),
		("memoria", _("Lo que el agente sabe de t&i...\tCtrl+Shift+K"), _("Instrucciones y memoria que lee el agente: CLAUDE.md, AGENTS.md, GEMINI.md..."), "accion_memoria"),
	]),
	(_("&Ver"), [
		("ir_sesiones", _("Ir a la lista de &sesiones\tAlt+1"), _("Lleva el foco a la lista de sesiones."), "ir_a_sesiones"),
		("ir_mensajes", _("Ir a la lista de &mensajes\tAlt+2"), _("Lleva el foco a los mensajes de la sesión abierta."), "ir_a_mensajes"),
		("ir_respuesta", _("Ir al cuadro de &respuesta\tAlt+3"), _("Lleva el foco al cuadro donde escribes al agente."), "ir_a_respuesta"),
		("leer_estado", _("Leer la &barra de estado\tAlt+4"), _("Dice el estado de la sesión, el agente y el uso de contexto."), "leer_barra_estado"),
		("ir_visor", _("Volver al visor del mensaje\tAlt+6"), _("Vuelve al visor del último mensaje que abriste, si sigue abierto: para leerlo mientras escribes tu respuesta."), "accion_ir_al_visor"),
		("submenu", _("Distrib&ución de la ventana"), "crear_menu_disposicion"),
		("-",),
		("submenu", _("M&ostrar"), "crear_menu_vistas"),
		("submenu", _("Or&denar por"), "crear_menu_orden"),
		("columnas", _("&Columnas de la lista..."), _("Elige qué columnas tiene la lista de sesiones y en qué orden."), "accion_columnas"),
		("filtrar", _("&Filtrar o buscar...\tCtrl+F"), _("En la lista de sesiones filtra por texto; en los mensajes busca dentro de la sesión."), "accion_buscar"),
		("siguiente", _("Buscar si&guiente\tF3"), _("Siguiente mensaje que contiene el texto buscado."), "accion_buscar_siguiente"),
		("anterior", _("Buscar a&nterior\tShift+F3"), _("Mensaje anterior que contiene el texto buscado."), "accion_buscar_anterior"),
		("buscar_todo", _("Buscar en &todas las sesiones...\tCtrl+Shift+S"), _("Busca un texto en los mensajes de todas las sesiones."), "accion_buscar_todo"),
		("cargar_anteriores", _("Cargar mensa&jes anteriores\tAlt+Home"), _("En una sesión larga, pone en la lista el siguiente grupo de mensajes más antiguos sin mover el foco."), "accion_cargar_anteriores"),
		("cargar_todos", _("Cargar todos los mensajes de la sesión"), _("Pone en la lista toda la conversación de la sesión abierta."), "accion_cargar_todos"),
		("favorito_mensaje", _("Marcar el mensaje como favorito o quitarlo\tCtrl+Alt+F"), _("Marca el mensaje elegido como favorito para volver a él enseguida (Alt+flecha arriba y Alt+flecha abajo en la lista de mensajes) o, si ya lo era, lo quita."), "accion_favorito_mensaje"),
		("nota_favorito", _("Escribir una nota en el favorito...\tCtrl+Alt+Shift+N"), _("Pone, cambia o quita (vacía) una nota corta en el mensaje favorito elegido, para recordar por qué lo marcaste. Si el mensaje aún no es favorito, lo marca con la nota."), "accion_nota_favorito"),
		("favoritos", _("Mensajes favoritos...\tCtrl+Alt+Shift+F"), _("Todos los mensajes favoritos de todas las sesiones: Intro lleva al mensaje en su sesión."), "accion_mensajes_favoritos"),
		("-",),
		("listas", _("L&istas de la sesión...\tCtrl+Shift+I"), _("Bloques de código, enlaces, archivos cambiados, órdenes, tareas, subagentes y hallazgos de revisión de la sesión."), "accion_listas"),
		("tareas", _("Tareas del agente...\tCtrl+Shift+J"), _("La lista de tareas del agente con su progreso: pendientes, en curso y hechas."), "accion_tareas"),
		("enlaces", _("&Enlaces...\tCtrl+L"), _("Enlaces que aparecen en la sesión."), "accion_enlaces"),
		("codigo", _("Blo&ques de código...\tCtrl+Shift+B"), _("Bloques de código de la sesión."), "accion_bloques"),
		("archivos", _("&Archivos cambiados...\tCtrl+Shift+D"), _("Archivos que el agente ha creado o modificado, con sus cambios."), "accion_archivos"),
		("copiar_codigo", _("Copiar el &último bloque de código\tCtrl+Shift+C"), _("Copia al portapapeles el último bloque de código de la sesión."), "accion_copiar_ultimo_bloque"),
		("herramientas", _("Mostrar las &herramientas\tCtrl+T"), _("Muestra u oculta en los mensajes las acciones del agente (leer, editar, ejecutar)."), "accion_alternar_herramientas"),
		("actualizar", _("Actuali&zar\tF5"), _("Vuelve a leer y ordenar la lista de sesiones."), "accion_actualizar"),
		("-",),
		("repetir", _("Re&petir el último aviso\tCtrl+Shift+R"), _("Vuelve a decir el último aviso de Ctrl+IA."), "accion_repetir_aviso"),
		("historial_avisos", _("Historial de a&visos...\tCtrl+Shift+H"), _("Lista de los últimos avisos, con su hora."), "accion_historial_avisos"),
		("silenciar", _("Si&lenciar la voz de Ctrl+IA\tCtrl+Shift+V"), _("Activa o desactiva los anuncios hablados de Ctrl+IA (tu lector sigue funcionando)."), "accion_silenciar"),
	]),
	(_("&Organizar"), [
		("renombrar", _("&Renombrar...\tF2"), _("Cambia el título de la sesión (solo en Ctrl+IA)."), "accion_renombrar"),
		("favorita", _("Marcar como &favorita\tCtrl+Shift+X"), _("Marca o desmarca la sesión como favorita."), "accion_favorita"),
		("ocultar", _("&Ocultar"), _("Quita la sesión de la lista sin borrar nada (tecla Suprimir)."), "accion_ocultar"),
		("mostrar_oculta", _("&Volver a mostrar"), _("Devuelve a la lista una sesión oculta."), "accion_mostrar_oculta"),
		("borrar", _("&Borrar para siempre"), _("Borra una sesión de Ctrl+IA y todos sus mensajes (Mayúsculas+Suprimir)."), "accion_borrar"),
		("-",),
		("terminada", _("Dar por &terminada o volver a activar\tCtrl+D"), _("Una sesión terminada deja de salir en «Te necesitan» y «Trabajando». Vuelve a activarse sola si tiene actividad nueva."), "accion_terminada"),
		("leida", _("Marcar como &leída o no leída\tCtrl+Q"), _("Quita la marca de respuesta nueva de la sesión o, si no la tenía, se la pone para acordarte de leerla."), "accion_marcar_leida"),
		("todas_leidas", _("Marcar todas como l&eídas\tCtrl+Shift+Q"), _("Quita la marca de respuesta nueva de todas las sesiones."), "accion_marcar_todas_leidas"),
		("-",),
		("al_grupo", _("&Añadir a un grupo...\tCtrl+G"), _("Pone la sesión en un grupo para encontrarla rápido."), "accion_anadir_a_grupo"),
		("sacar_grupo", _("&Quitar de un grupo..."), _("Saca la sesión de uno de sus grupos."), "accion_quitar_de_grupo"),
		("mover_grupo", _("&Mover a otro grupo..."), _("Saca la sesión de uno de sus grupos y la mete en otro en un solo paso."), "accion_mover_a_grupo"),
		("grupos", _("&Grupos...\tCtrl+Shift+L"), _("Lista de grupos: ábrelos para ver y trabajar sus sesiones, y créalos, edítalos, ordénalos o expórtalos."), "accion_gestionar_grupos"),
	]),
	(_("&Herramientas"), [
		("agentes", _("&Agentes instalados..."), _("Comprueba qué agentes tienes, instálalos e inicia sesión."), "accion_agentes"),
		("config_agentes", _("Configuración de los agen&tes...\tCtrl+Shift+W"), _("Cambia las opciones propias de Claude Code, Codex, Gemini CLI y Antigravity (sus settings.json y config.toml) de forma accesible."), "accion_config_agentes"),
		("reglas", _("&Reglas de permisos de Antigravity..."), _("Qué puede hacer Antigravity sin preguntar."), "accion_reglas_antigravity"),
		("acp_propios", _("Agentes A&CP personalizados..."), _("Añade otros agentes compatibles con el protocolo ACP indicando la orden que los arranca."), "accion_acp_personalizados"),
		("gestor_skills", _("Gestor de s&kills...\tCtrl+Alt+G"), _("Tu catálogo de skills (habilidades con su SKILL.md): añádelas desde sus carpetas, créalas, clasifícalas por categorías y edítalas, para elegir después cuáles usa cada sesión."), "accion_gestor_skills"),
		("equipos_ssh", _("&Equipos por SSH..."),_("Añade un Mac o un equipo Linux al que entras por SSH para que los agentes trabajen allí."), "accion_equipos_ssh"),
		("limites", _("Lí&mites de uso de tus cuentas...\tCtrl+Shift+Y"), _("Pregunta a Claude Code, Codex y Antigravity cuánto llevas gastado del límite de 5 horas y del semanal, sin enviar mensajes ni gastar cuota."), "accion_limites"),
		("informe", _("&Informe de sesiones...\tCtrl+I"), _("Todas las sesiones con su tamaño, sus mensajes y los totales, para decidir qué ocultar o archivar."), "accion_informe"),
		("-",),
		("preferencias", _("&Preferencias...\tCtrl+,"), _("Configura Ctrl+IA."), "accion_preferencias"),
		("sonidos", _("S&onidos..."), _("Prueba cada sonido, actívalo o desactívalo y abre la carpeta de tus sonidos propios."), "accion_sonidos"),
		("sonidos_activos", _("Sonidos acti&vados\tF9"), _("Activa o desactiva todos los sonidos de Ctrl+IA."), "accion_alternar_sonidos"),
		("datos", _("Abrir la carpeta de &datos"), _("Carpeta donde Ctrl+IA guarda tu configuración y tus sesiones."), "accion_carpeta_datos"),
		("registros", _("Abrir la carpeta de re&gistros"), _("Carpeta con los registros de funcionamiento (útiles si algo falla)."), "accion_carpeta_registros"),
		("diagnostico", _("Crear un paquete de diagnó&stico..."), _("Prepara un archivo con los registros, sin datos personales, para enviarlo si algo falla."), "accion_diagnostico"),
	]),
	(_("Ay&uda"), [
		("manual", _("&Manual de usuario\tF1"), _("Abre el manual completo de Ctrl+IA."), "accion_manual"),
		("teclas", _("&Lista de teclas\tCtrl+F1"), _("Todos los atajos de teclado de Ctrl+IA."), "accion_teclas"),
		("novedades", _("&Novedades de la versión"), _("Qué ha cambiado en esta versión."), "accion_novedades"),
		("ayuda_control", _("Ayuda del control &enfocado\tCtrl+H"), _("Explica para qué sirve el control donde está el foco."), "accion_ayuda_control"),
		("-",),
		("actualizaciones", _("&Buscar actualizaciones..."), _("Comprueba si hay una versión nueva de Ctrl+IA."), "accion_buscar_actualizaciones"),
		("cafe", _("Invítame a un &café si te gusta mi trabajo"), _("Abre la página de donaciones de PayPal en el navegador."), "accion_invitar_cafe"),
		("acerca", _("&Acerca de Ctrl+IA..."), _("Versión y datos del programa."), "accion_acerca_de"),
	]),
]


class VentanaPrincipal(MixinDisposicion, MixinSesiones, MixinMensajes, MixinConversacion, MixinPR, wx.Frame):
	"""Ventana principal: sesiones, mensajes y respuesta."""

	def __init__(self, configuracion: Configuracion, instancia: InstanciaUnica | None = None,
			almacen: Almacen | None = None, proveedores: dict | None = None) -> None:
		super().__init__(None, title=NOMBRE_APLICACION, name="ventana_principal")
		self.config = configuracion
		self._instancia = instancia
		self._bandeja: IconoBandeja | None = None
		self._forzar_salida = False
		# True tras «Reiniciar ahora» del actualizador: el cierre no pregunta nada.
		self._salir_sin_preguntar = False
		self._saliendo = False
		self._rect_normal: wx.Rect | None = None
		self.tareas = tareas.GestorTareas(max_hilos=4, nombre="ctrlia", ejecutor_interfaz=tareas.ejecutor_wx)
		self.almacen = almacen or Almacen()
		if almacen is None:
			self._preparar_skills_ejemplo()
		if proveedores is None:
			from app.services.agentes import crear_proveedores

			proveedores = crear_proveedores({
				"permitir_clave_api": self.opcion("agentes.permitir_clave_api", False),
				"acp_adaptadores": self.opcion("agentes.adaptadores_acp", False),
				"acp_personalizados": self.opcion("agentes.personalizados", []),
			})
		self.gestor = GestorSesiones(self.almacen, proveedores, self._evento_desde_hilo, self.opcion)
		avisos.configurar(self.opcion, self)

		self.SetMinSize(self.FromDIP(wx.Size(*TAMANO_MINIMO)))
		self._aplicar_icono()
		self._crear_menus()
		self._crear_contenido()
		# Cuarta parte: el último aviso, escrito (nada existe solo como voz).
		self.CreateStatusBar(4)
		self.SetStatusWidths([-2, -2, -2, -3])
		self._restaurar_geometria()
		habilitar_ayuda_contextual(self)
		self._instalar_teclas()
		self.iniciar_sesiones()
		self.iniciar_mensajes()
		self.iniciar_conversacion()
		self.iniciar_disposicion()

		self.Bind(wx.EVT_CLOSE, self._al_cerrar)
		self.Bind(wx.EVT_ICONIZE, self._al_minimizar)
		self.Bind(wx.EVT_SIZE, self._al_cambiar_geometria)
		self.Bind(wx.EVT_MOVE, self._al_cambiar_geometria)
		self._temporizador_lista = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, lambda _e: self.refrescar_sesiones(), self._temporizador_lista)
		self._temporizador_seguimiento = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, lambda _e: self.seguir_sesion_externa(), self._temporizador_seguimiento)
		self._temporizador_buzon = wx.Timer(self)
		self.Bind(wx.EVT_TIMER, self._al_revisar_buzon, self._temporizador_buzon)
		self.config.suscribir(self._al_cambiar_configuracion)

	# ------------------------------------------------------------------
	# Construcción
	# ------------------------------------------------------------------

	def _aplicar_icono(self) -> None:
		from app.core.paths import ruta_iconos

		ruta = ruta_iconos() / "app.ico"
		if ruta.is_file():
			icono = wx.Icon(str(ruta), wx.BITMAP_TYPE_ICO)
			if icono.IsOk():
				self.SetIcon(icono)

	def _crear_menus(self) -> None:
		barra = wx.MenuBar()
		self._elementos_menu: dict[str, wx.MenuItem] = {}
		for titulo, elementos in MENUS:
			menu = wx.Menu()
			for elemento in elementos:
				if elemento[0] == "-":
					menu.AppendSeparator()
				elif elemento[0] == "submenu":
					menu.AppendSubMenu(getattr(self, elemento[2])(), elemento[1])
				else:
					clave, etiqueta, ayuda, metodo = elemento
					tipo = wx.ITEM_CHECK if clave in ("herramientas", "silenciar", "favorita", "sonidos_activos") else wx.ITEM_NORMAL
					item = menu.Append(wx.ID_ANY, etiqueta, ayuda, tipo)
					self._elementos_menu[clave] = item
					self.Bind(wx.EVT_MENU, lambda _e, m=metodo: self._ejecutar_accion(m), item)
			barra.Append(menu, titulo)
		self.SetMenuBar(barra)
		self._elementos_menu["herramientas"].Check(bool(self.opcion("mensajes.herramientas", True)))
		self._elementos_menu["sonidos_activos"].Check(bool(self.opcion("anuncios.sonidos", True)))
		self.sincronizar_menu_disposicion()

	def _ejecutar_accion(self, metodo: str) -> None:
		"""Ejecuta una acción de menú; un fallo se registra y se explica, nunca cierra la ventana."""
		try:
			getattr(self, metodo)()
		except Exception as error:  # noqa: BLE001 - una acción que falla no debe tumbar la aplicación
			log.exception("Falló la acción %s", metodo)
			mostrar_error(self, _("No se pudo completar la acción: {error}").format(error=error))

	def _crear_contenido(self) -> None:
		panel = wx.Panel(self)
		self.panel = panel
		raiz = wx.BoxSizer(wx.VERTICAL)

		self.etiqueta_sesiones = wx.StaticText(panel, label=_("&Sesiones:"))
		raiz.Add(self.etiqueta_sesiones, 0, wx.LEFT | wx.TOP, 6)
		self.lista_sesiones = self.crear_lista_sesiones(panel)
		raiz.Add(self.lista_sesiones, 3, wx.EXPAND | wx.ALL, 6)
		self._sizer_raiz = raiz

		self.etiqueta_mensajes = wx.StaticText(panel, label=_("&Mensajes:"))
		raiz.Add(self.etiqueta_mensajes, 0, wx.LEFT, 6)
		self.lista_mensajes = self.crear_lista_mensajes(panel)
		raiz.Add(self.lista_mensajes, 4, wx.EXPAND | wx.ALL, 6)

		# Panel de lectura (oculto de fábrica): entre los mensajes y la respuesta.
		self.crear_panel_lectura(panel, raiz)

		self.etiqueta_respuesta = wx.StaticText(panel, label=_("&Respuesta:"))
		raiz.Add(self.etiqueta_respuesta, 0, wx.LEFT, 6)
		self.respuesta = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_RICH2, name=_("Respuesta"))
		self.respuesta.SetHelpText(_(
			"Escribe aquí tu mensaje para el agente. Control+Intro lo envía; Control+Mayúsculas+Intro lo envía ahora "
			"interrumpiendo al agente; Control+barra inclinada inserta un comando; Control+V pega también imágenes."
		))
		raiz.Add(self.respuesta, 2, wx.EXPAND | wx.ALL, 6)
		panel.SetSizer(raiz)

	def _instalar_teclas(self) -> None:
		self.Bind(wx.EVT_CHAR_HOOK, self._al_tecla_global)

	def _al_tecla_global(self, evento: wx.KeyEvent) -> None:
		"""F6 / Mayús+F6 recorren las zonas; Escape vuelve a las sesiones; Ctrl+H ayuda."""
		# atajo: F6 zona siguiente
		# atajo: Mayús+F6 zona anterior
		tecla, mods = evento.GetKeyCode(), evento.GetModifiers()
		foco = wx.Window.FindFocus()
		if tecla == wx.WXK_F6 and mods in (wx.MOD_NONE, wx.MOD_SHIFT):
			# Solo las zonas visibles: la lista de sesiones oculta y el panel de lectura apagado se saltan.
			zonas = self.zonas()
			actual = zonas.index(foco) if foco in zonas else -1
			paso = -1 if mods == wx.MOD_SHIFT else 1
			destino = zonas[(actual + paso) % len(zonas)]
			{self.lista_sesiones: self.ir_a_sesiones, self.lista_mensajes: self.ir_a_mensajes,
				self.panel_lectura: self.ir_a_lectura, self.respuesta: self.ir_a_respuesta}[destino]()
			return
		if tecla == wx.WXK_ESCAPE and mods == wx.MOD_NONE:
			if foco is self.lista_sesiones and self.filtro_activo():
				self.quitar_filtro()
				return
			if foco in (self.lista_mensajes, self.panel_lectura, self.respuesta):
				self.ir_a_sesiones()
				return
		if mods == wx.MOD_CONTROL and tecla == ord("V") and foco is self.respuesta and self.pegar_imagen():
			return
		evento.Skip()

	# ------------------------------------------------------------------
	# Utilidades comunes
	# ------------------------------------------------------------------

	def opcion(self, clave: str, defecto: Any = None) -> Any:
		return self.config.obtener(clave, defecto)

	def poner_opcion(self, clave: str, valor: Any) -> None:
		try:
			self.config.establecer(clave, valor)
		except (ValueError, KeyError):
			log.warning("No se pudo guardar la opción %s", clave, exc_info=True)

	def _preparar_skills_ejemplo(self) -> None:
		"""La primera vez, carga en el catálogo las skills de ejemplo; siempre, corrige su carpeta si el programa se movió.

		Antes apunta a las carpetas nuevas las skills que el catálogo aún tenga en las de la 1.5.0
		(``proteccion_skills.reparar_rutas_catalogo``; las carpetas las mueve ``main.py`` al arrancar).

		Sin la carpeta de ejemplos no se marca nada: una actualización la trae después. Hasta la 1.5.1 se
		marcaban como cargados aunque no estuviera (no llegaba al actualizar desde la 1.4.3 o la 1.5.0), así
		que, una vez (``skills.ejemplos_revisados``), si el catálogo no tiene ninguno, se cargan.
		"""
		try:
			from app.core.paths import ruta_skills_ejemplo
			from app.services.catalogo_skills import CatalogoSkills
			from app.services.proteccion_skills import reparar_rutas_catalogo

			reparar_rutas_catalogo(self.almacen)
			if not ruta_skills_ejemplo().is_dir():
				log.info("No hay skills de ejemplo en %s; se cargarán cuando lleguen", ruta_skills_ejemplo())
				return
			catalogo = CatalogoSkills(self.almacen)
			cargados = self.opcion("skills.ejemplos_cargados", False)
			if cargados and not self.opcion("skills.ejemplos_revisados", False):
				cargados = catalogo.tiene_ejemplos()
			if not cargados:
				catalogo.cargar_ejemplos()
				self.poner_opcion("skills.ejemplos_cargados", True)
			else:
				catalogo.reparar_ejemplos()
			self.poner_opcion("skills.ejemplos_revisados", True)
		except Exception:  # noqa: BLE001 - los ejemplos nunca deben impedir arrancar
			log.warning("No se pudieron preparar las skills de ejemplo", exc_info=True)

	def en_segundo_plano(self, funcion: Callable[..., Any], *argumentos: Any,
			al_terminar: Callable[[Any], None] | None = None, al_fallar: Callable[[BaseException], None] | None = None,
			clave: str | None = None) -> None:
		"""Ejecuta ``funcion(*argumentos)`` en un hilo y entrega el resultado en la interfaz."""

		def trabajo(_tarea: Any, *args: Any) -> Any:
			return funcion(*args)

		def fallo(error: BaseException) -> None:
			log.error("Falló una tarea en segundo plano: %s", error, exc_info=error)
			if al_fallar is not None:
				al_fallar(error)
			else:
				avisos.notificar(_("Error: {error}").format(error=error), padre=self)

		def terminado(resultado: Any) -> None:
			if self and not self._saliendo and al_terminar is not None:
				al_terminar(resultado)

		self.tareas.enviar(trabajo, *argumentos, al_terminar=terminado, al_fallar=fallo, clave_unica=clave)

	def poner_estado(self, texto: str, campo: int = 0) -> None:
		if self and not self._saliendo:
			self.SetStatusText(texto, campo)

	def leer_barra_estado(self) -> None:
		# La cuarta parte (último aviso) se repite con Ctrl+Mayús+R: aquí solo el estado.
		partes = [self.GetStatusBar().GetStatusText(i) for i in range(3)]
		# Las palabras curiosas (#314) solo se ven: Alt+4 dice «está escribiendo».
		partes[0] = self.estado_para_leer(partes[0])
		voz.hablar(". ".join(p for p in partes if p) or _("Barra de estado vacía."), interrumpir=True)

	def ir_a_sesiones(self) -> None:
		self.mostrar_lista_sesiones_si_oculta()
		self.lista_sesiones.SetFocus()
		if self.lista_sesiones.GetItemCount() and self.lista_sesiones.GetFocusedItem() < 0:
			self.lista_sesiones.enfocar_fila(0, anunciar=False)

	def ir_a_mensajes(self) -> None:
		self.lista_mensajes.SetFocus()
		if self.lista_mensajes.GetItemCount() and self.lista_mensajes.GetFocusedItem() < 0:
			self.lista_mensajes.enfocar_fila(self.lista_mensajes.GetItemCount() - 1, anunciar=False)
		elif not self.lista_mensajes.GetItemCount():
			avisos.anunciar(_("No hay mensajes. Abre una sesión con Intro en la lista de sesiones."))

	def ir_a_respuesta(self) -> None:
		self.respuesta.SetFocus()
		self.respuesta.SetInsertionPointEnd()

	def preferencias_lectura(self):
		"""Qué se dice al recorrer columnas (se lee de Preferencias en cada anuncio)."""
		from app.ui.lectura_listas import preferencias_lectura

		return preferencias_lectura(self.opcion)

	# ------------------------------------------------------------------
	# Mostrar, bandeja, geometría y cierre
	# ------------------------------------------------------------------

	def mostrar(self) -> None:
		self.Show()
		self.Raise()
		wx.CallAfter(self.ir_a_sesiones)
		self._temporizador_lista.Start(INTERVALO_LISTA_MS)
		self._temporizador_buzon.Start(INTERVALO_BUZON_MS)
		self.iniciar_vigilancia_pr()
		wx.CallLater(800, self.al_mostrar_por_primera_vez)

	def al_mostrar_por_primera_vez(self) -> None:
		"""Comprobaciones que no deben retrasar la aparición de la ventana."""
		if not self:
			return
		try:
			from app.ui.visor_ayuda import mostrar_novedades_si_procede

			mostrar_novedades_si_procede(self, APP_VERSION)
		except Exception:  # noqa: BLE001 - las novedades son un extra
			log.debug("No se pudieron mostrar las novedades", exc_info=True)
		self.detectar_agentes_al_arrancar()

	def ocultar_en_bandeja(self) -> bool:
		if self._bandeja is None:
			self._bandeja = IconoBandeja(NOMBRE_APLICACION, self.restaurar, self.salir)
		if not self._bandeja.mostrar():
			self._quitar_bandeja()
			return False
		self.Hide()
		avisos.anunciar(_("Ctrl+IA sigue en la bandeja del sistema."))
		return True

	def _quitar_bandeja(self) -> None:
		if self._bandeja is not None:
			self._bandeja.quitar()
			self._bandeja = None

	def restaurar(self) -> None:
		if not self:
			return
		self.Show()
		if self.IsIconized():
			self.Iconize(False)
		self.Raise()
		wx.CallAfter(self._quitar_bandeja)
		dialogo = next((v for v in wx.GetTopLevelWindows() if isinstance(v, wx.Dialog) and v.IsShown()), None)
		if dialogo is not None:
			dialogo.Raise()
		if not traer_al_frente(self.GetHandle()):
			self.RequestUserAttention()

	def salir(self) -> None:
		self._forzar_salida = True
		self.Close()

	def salir_para_actualizar(self) -> None:
		"""Cierre pedido por el actualizador tras «Reiniciar ahora».

		La persona ya ha respondido a la pregunta de reinicio (que avisa de los
		agentes trabajando): aquí no se vuelve a preguntar «¿Quieres salir?».
		"""
		self._salir_sin_preguntar = True
		self._forzar_salida = True
		self.Close()

	def _restaurar_geometria(self) -> None:
		ancho = int(self.opcion("ventana.ancho", 1000))
		alto = int(self.opcion("ventana.alto", 700))
		if ancho < TAMANO_MINIMO[0] or alto < TAMANO_MINIMO[1]:
			ancho, alto = 1000, 700
		self.SetSize(wx.Size(ancho, alto))
		x, y = int(self.opcion("ventana.x", -1)), int(self.opcion("ventana.y", -1))
		if x >= -50 and y >= -50 and wx.Display.GetFromPoint(wx.Point(x + 60, y + 15)) != wx.NOT_FOUND:
			self.SetPosition(wx.Point(x, y))
		else:
			self.Centre()
		if self.opcion("ventana.maximizada", False):
			self.Maximize(True)

	def _al_cambiar_geometria(self, evento: wx.Event) -> None:
		if self.IsShown() and not self.IsMaximized() and not self.IsIconized():
			self._rect_normal = self.GetRect()
		evento.Skip()

	def _guardar_geometria(self) -> None:
		rect = self._rect_normal or self.GetRect()
		try:
			self.config.establecer_varias({
				"ventana.x": rect.x, "ventana.y": rect.y, "ventana.ancho": rect.width,
				"ventana.alto": rect.height, "ventana.maximizada": bool(self.IsMaximized()),
			})
		except (ValueError, KeyError):
			log.debug("No se pudo guardar la geometría", exc_info=True)

	def _al_minimizar(self, evento: wx.IconizeEvent) -> None:
		if evento.IsIconized() and self.opcion("general.minimizar_a_bandeja", False):
			wx.CallAfter(self.ocultar_en_bandeja)
		evento.Skip()

	def _al_cerrar(self, evento: wx.CloseEvent) -> None:
		ocupadas = self.gestor.sesiones_ocupadas()
		if evento.CanVeto() and not self._salir_sin_preguntar:
			from app.ui.accesibilidad.dialogos import confirmar

			if ocupadas:
				texto = _("Hay {n} sesiones con el agente trabajando. Si sales, se detendrán. ¿Salir de todas formas?").format(n=len(ocupadas))
				if confirmar(self, texto, _("Salir de Ctrl+IA"), peligroso=True) != wx.ID_YES:
					evento.Veto()
					return
			elif self.opcion("general.confirmar_salida", True) and not self._forzar_salida:
				if confirmar(self, _("¿Quieres salir de Ctrl+IA?"), _("Salir")) != wx.ID_YES:
					evento.Veto()
					return
		self._saliendo = True
		for temporizador in (self._temporizador_lista, self._temporizador_seguimiento, self._temporizador_buzon,
				self._temporizador_lectura, self._temporizador_palabras):
			temporizador.Stop()
		self.poner_opcion("general.ultima_sesion", self.sesion_actual or "")
		self._guardar_geometria()
		try:
			self.gestor.cerrar_todo()
		except Exception:  # noqa: BLE001 - al salir se cierra lo que se pueda
			log.exception("Error al cerrar las conversaciones")
		self.tareas.apagar(espera=3.0)
		self.config.guardar()
		try:
			self.almacen.cerrar()
		except Exception:  # noqa: BLE001
			log.exception("Error al cerrar la base de datos")
		self._quitar_bandeja()
		log.info("Ventana principal cerrada.")
		self.Destroy()

	# ------------------------------------------------------------------
	# Segunda instancia y configuración
	# ------------------------------------------------------------------

	def _al_revisar_buzon(self, _evento: wx.TimerEvent) -> None:
		try:
			for orden in (self._instancia.recoger_ordenes() if self._instancia else []):
				if orden.orden in (ORDEN_MOSTRAR, ORDEN_ABRIR):
					self.restaurar()
				if orden.argumentos:
					self.abrir_archivos([Path(a) for a in orden.argumentos])
		except Exception:  # noqa: BLE001 - el temporizador late toda la sesión; no debe romperla
			log.exception("Error al atender las órdenes de otra instancia")

	def abrir_archivos(self, rutas: list[Path]) -> None:
		"""Una carpeta pasada al arrancar abre «Nueva sesión» con esa carpeta."""
		carpetas = [r for r in rutas if r.is_dir()]
		if carpetas:
			wx.CallAfter(self.accion_nueva_sesion, carpetas[0])

	def _al_cambiar_configuracion(self, clave: str, _antes: Any, despues: Any) -> None:
		if clave in ("anuncios.retardo_voz_ms", "anuncios.braille", "anuncios.braille_sin_voz"):
			avisos.aplicar_preferencias_voz()
		elif clave == "anuncios.sonidos":
			wx.CallAfter(lambda: self and self._elementos_menu["sonidos_activos"].Check(bool(despues)))
		elif clave.startswith("ventana."):
			self.preferencias_disposicion_cambiadas(clave, despues)
		elif clave == "lista.columnas":
			wx.CallAfter(self._recrear_lista_sesiones, False)
		elif clave.startswith("lista.") or clave.startswith("historial."):
			wx.CallAfter(self.refrescar_sesiones, True)
		elif clave == "mensajes.columnas":
			wx.CallAfter(self.recrear_lista_mensajes)
		elif clave.startswith("mensajes."):
			wx.CallAfter(self.recargar_mensajes)
		elif clave.startswith("lectura."):
			# Cambia cómo se escriben las filas: se repintan sin mover el foco.
			wx.CallAfter(self.mostrar_sesiones, False)
			wx.CallAfter(self._repintar_mensajes)

	# ------------------------------------------------------------------
	# Acciones generales (menús Herramientas y Ayuda)
	# ------------------------------------------------------------------

	def accion_preferencias(self) -> None:
		from app.ui.dialogo_preferencias import DialogoPreferencias

		dialogo = DialogoPreferencias(self, self.config, anunciar=avisos.anunciar)
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()

	def accion_agentes(self) -> None:
		from app.ui.dialogos.agentes import DialogoAgentes

		dialogo = DialogoAgentes(self, self.gestor)
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()
		self.refrescar_sesiones(True)

	def accion_config_agentes(self) -> None:
		"""Editor de los archivos de configuración de los agentes (Ctrl+Mayús+W)."""
		from app.core.paths import ruta_datos
		from app.ui.dialogos.config_agentes import abrir

		fila = self.fila_de(self.sesion_actual) if self.sesion_actual else self.fila_seleccionada()
		carpeta = Path(fila.carpeta) if fila is not None and fila.carpeta and Path(fila.carpeta).is_dir() else None
		agente = fila.proveedor if fila is not None else ""
		abrir(self, carpeta, self.almacen.carpetas_recientes(), ruta_datos() / "backups" / "agentes", agente)

	def accion_reglas_antigravity(self) -> None:
		from app.ui.dialogos.reglas_antigravity import DialogoReglasAntigravity

		dialogo = DialogoReglasAntigravity(self, self.gestor.proveedor("antigravity"))
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()

	def accion_acp_personalizados(self) -> None:
		from app.ui.dialogos.acp_personalizados import DialogoAcpPersonalizados

		dialogo = DialogoAcpPersonalizados(self, self.opcion("agentes.personalizados", []) or [])
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			agentes = dialogo.agentes
		finally:
			dialogo.Destroy()
		self.poner_opcion("agentes.personalizados", agentes)
		mostrar_mensaje(self, _("Lista guardada. Los cambios se aplican al reiniciar Ctrl+IA."), _("Agentes ACP personalizados"))

	def accion_gestor_skills(self) -> None:
		"""Herramientas, «Gestor de skills...» (Ctrl+Alt+G): el catálogo de skills con su SKILL.md."""
		from app.ui.dialogos.gestor_skills import abrir_gestor

		abrir_gestor(self, self.almacen, editor=str(self.opcion("agentes.editor_externo", "") or ""))

	def accion_equipos_ssh(self) -> None:
		"""Equipos por SSH: otros equipos donde pueden trabajar los agentes."""
		from app.ui.dialogos.equipos_ssh import DialogoEquiposSSH

		dialogo = DialogoEquiposSSH(self, self.opcion("agentes.equipos_ssh", []) or [])
		try:
			if dialogo.ShowModal() != wx.ID_OK:
				return
			equipos = dialogo.resultado_final()
		finally:
			dialogo.Destroy()
		self.poner_opcion("agentes.equipos_ssh", equipos)
		voz.hablar(_("Equipos guardados."))
		# Las sesiones de un equipo nuevo (o vuelto a marcar) aparecen sin esperar al siguiente repaso.
		self.gestor.leer_equipos_ya()
		self.refrescar_sesiones(True)

	def accion_carpeta_datos(self) -> None:
		from app.core.paths import ruta_datos

		os.startfile(ruta_datos())  # noqa: S606 - carpeta propia de la aplicación

	def accion_carpeta_registros(self) -> None:
		from app.core.paths import ruta_registros

		os.startfile(ruta_registros())  # noqa: S606 - carpeta propia de la aplicación

	def accion_diagnostico(self) -> None:
		def crear() -> Path:
			from app.core import diagnostico

			return diagnostico.generar_paquete(NOMBRE_APLICACION, APP_VERSION)

		def hecho(ruta: Path) -> None:
			mostrar_mensaje(self, _(
				"Se ha creado el paquete de diagnóstico:\n{ruta}\n\nNo contiene tus mensajes ni tus claves. "
				"Se abrirá la carpeta para que puedas adjuntarlo."
			).format(ruta=ruta), _("Diagnóstico"))
			subprocess.Popen(["explorer", "/select,", str(ruta)])  # noqa: S603, S607 - Explorador de Windows con ruta propia

		avisos.anunciar(_("Preparando el paquete de diagnóstico..."))
		self.en_segundo_plano(crear, al_terminar=hecho)

	def accion_manual(self) -> None:
		from app.ui.visor_ayuda import abrir_manual

		abrir_manual(self)

	def accion_teclas(self) -> None:
		from app.ui.visor_ayuda import abrir_teclas

		abrir_teclas(self)

	def accion_novedades(self) -> None:
		from app.ui.visor_ayuda import abrir_novedades

		abrir_novedades(self)

	def accion_ayuda_control(self) -> None:
		describir_control_enfocado(self)

	def accion_acerca_de(self) -> None:
		from app.ui.acerca_de import mostrar_acerca_de

		mostrar_acerca_de(self)

	def accion_invitar_cafe(self) -> None:
		"""Abre la página de donaciones del autor en el navegador."""
		import webbrowser

		from app.core.version import URL_DONACIONES

		if webbrowser.open(URL_DONACIONES):
			avisos.anunciar(_("Gracias. Se ha abierto la página de donaciones en el navegador."), interrumpir=True)
		else:
			mostrar_mensaje(self, _("No se pudo abrir el navegador. La dirección es: {url}").format(url=URL_DONACIONES),
				_("Invítame a un café"))

	def accion_buscar_actualizaciones(self) -> None:
		from app.services import actualizador

		if not actualizador.URLS_MANIFIESTO:
			mostrar_mensaje(self, _(
				"Esta copia de Ctrl+IA no tiene configurado un servidor de actualizaciones. "
				"Cuando haya una versión nueva, descárgala de la página donde obtuviste el programa."
			), _("Buscar actualizaciones"))
			return
		from app.ui import dialogo_actualizacion

		dialogo_actualizacion.comprobar_manualmente(self, actualizador.configuracion_por_defecto(APP_VERSION), avisos.anunciar)

	def accion_repetir_aviso(self) -> None:
		avisos.repetir_ultimo()

	def accion_historial_avisos(self) -> None:
		from app.ui.comun import hora_corta
		from app.ui.visor_ayuda import mostrar_documento

		lineas = [f"- {hora_corta(momento)}: {texto}" for momento, texto in reversed(avisos.historial())]
		texto = "# " + _("Historial de avisos") + "\n\n" + ("\n".join(lineas) or _("Todavía no hay avisos."))
		mostrar_documento(self, _("Historial de avisos"), texto, forzar_texto=True)

	def accion_silenciar(self) -> None:
		silencio = not voz.esta_silenciado()
		self._elementos_menu["silenciar"].Check(silencio)
		if silencio:
			# Se avisa ANTES de callar; después ya no se oiría. Se espera a que lo diga.
			voz.hablar(_("Voz de Ctrl+IA desactivada."), interrumpir=True, sincrono=True)
			voz.silenciar(True)
		else:
			voz.silenciar(False)
			voz.hablar(_("Voz de Ctrl+IA activada."), interrumpir=True)

	def accion_tareas(self) -> None:
		"""Abre las listas de la sesión en la página de tareas del agente."""
		self._abrir_listas("tareas")

	# ------------------------------------------------------------------
	# Sonidos
	# ------------------------------------------------------------------

	def accion_alternar_sonidos(self) -> None:
		activos = avisos.alternar_sonidos()
		self._elementos_menu["sonidos_activos"].Check(activos)
		avisos.anunciar(_("Sonidos activados.") if activos else _("Sonidos desactivados."), interrumpir=True)
		if activos:
			avisos.sonar("enviado")

	def accion_sonidos(self) -> None:
		from app.ui.dialogos.sonidos import DialogoSonidos

		dialogo = DialogoSonidos(self, self.config)
		try:
			dialogo.ShowModal()
		finally:
			dialogo.Destroy()

	def _al_evento(self, clave: str, evento: Any) -> None:
		"""Añade los sonidos de herramientas, archivos, turnos detenidos y sesiones en segundo plano."""
		tipo = str(getattr(evento.tipo, "value", evento.tipo))
		sonido = avisos.sonido_de_evento(tipo, evento.datos)
		if sonido:
			avisos.sonar(sonido)
		if tipo == "fin_turno" and clave != self.sesion_actual:
			# La respuesta de una sesión que no tienes abierta suena distinta (más suave).
			with avisos.sustituir_sonido("respuesta", "segundo_plano"):
				super()._al_evento(clave, evento)
			return
		super()._al_evento(clave, evento)
