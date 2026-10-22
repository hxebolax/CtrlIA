# Preferencias accesibles: el diálogo y el recorrido completo

Cómo funciona `app/ui/dialogo_preferencias.py` y qué pasa con una preferencia desde que se declara hasta que cambia una ventana abierta. Probado en `tests/test_dialogo_preferencias.py` (sin mostrar el diálogo).

## Recorrido completo de una preferencia

Ejemplo: «Tamaño de la letra», que debe aplicarse sin reiniciar.

### 1. Valor por defecto

```python
VALORES_POR_DEFECTO = {
	"apariencia": {"tamano_fuente": 11},
}
REGLAS = {"apariencia.tamano_fuente": Regla(minimo=8, maximo=40)}
```

### 2. Descriptor en el diálogo

```python
Pagina(N_("Apariencia"), (
	Opcion("apariencia.tamano_fuente", N_("&Tamaño de la letra:"), ENTERO,
		N_("Tamaño en puntos del texto de las listas. Se aplica al momento.")),
))
```

- `N_()` marca el texto para traducir sin traducirlo al importar (gettext aún no está listo); el diálogo lo traduce con `_()` al construirse.
- Los límites del `SpinCtrl` salen de la `Regla` si el descriptor no los fija.

### 3. El diálogo carga el valor

Al construirse, cada control recibe `config.obtener(clave)`. Los secretos, `secretos.obtener(clave)`.

### 4. El usuario cambia y pulsa Aplicar o Aceptar

- `aplicar()` lee todos los controles, **valida todo** con `config.validar` y, si algo no vale, muestra el motivo, cambia a la página del control, le da el foco y no guarda nada.
- Si todo vale, guarda solo lo que cambió con **un único** `establecer_varias` (una escritura, no una por opción) y los secretos con `guardar_varios`.
- Anuncia «Preferencias guardadas» si se pasó una función `anunciar`.
- Aceptar hace lo mismo y cierra con `wx.ID_OK`; Aplicar no cierra.

### 5. Consumo

```python
tamano = config.obtener("apariencia.tamano_fuente")
```

Léela donde se use, no una sola vez al arrancar guardada en una variable global: así el valor siempre está al día.

### 6. Efecto en las ventanas abiertas

```python
class VentanaPrincipal(wx.Frame):
	def __init__(self, config):
		super().__init__(None, title=_("Mi aplicación"))
		...
		self._cancelar = config.suscribir(self._al_cambiar_preferencia, prefijo="apariencia.")
		self.Bind(wx.EVT_WINDOW_DESTROY, self._al_destruir)

	def _al_cambiar_preferencia(self, clave, _anterior, nuevo):
		# Puede llegar desde cualquier hilo: los controles solo en el principal.
		if clave == "apariencia.tamano_fuente":
			wx.CallAfter(self._aplicar_tamano, nuevo)

	def _aplicar_tamano(self, tamano):
		if not self:  # la ventana se cerró mientras esperaba el CallAfter
			return
		fuente = self.lista.GetFont()
		fuente.SetPointSize(tamano)
		self.lista.SetFont(fuente)
		self.Layout()

	def _al_destruir(self, evento):
		if evento.GetEventObject() is self:
			self._cancelar()
		evento.Skip()
```

La prueba `test_suscriptor_de_ventana_abierta_aplica_con_callafter` hace este recorrido completo.

### 7. Cancelar

- Cancelar, Escape y cerrar la ventana no guardan nada: los valores solo se escriben al Aplicar o Aceptar. Tampoco llegan avisos a los suscriptores.
- Lo ya aplicado con «Aplicar» no se deshace al cancelar después (así funcionan los diálogos de Windows). Si el usuario necesita deshacer, que use «Restablecer valores de fábrica» o vuelva a cambiarlo.
- **Vista previa en vivo** (opcional): si quieres que el tamaño de letra cambie mientras se mueve el control, aplícalo a la ventana **sin guardar** y, al cancelar, vuelve a aplicar el valor guardado. No uses `establecer` para la vista previa: guardaría y avisaría a todos.

## Reglas de accesibilidad del diálogo

1. **Etiqueta antes del control.** Cada control lleva un `wx.StaticText` creado **justo antes** (mismo padre). En Windows, el lector de pantalla toma el nombre del control del texto estático anterior en el orden de creación; `name=` y `SetName` no nombran los controles nativos. La prueba recorre `GetChildren()` y comprueba que el hermano anterior es la etiqueta correcta.
2. **Casillas con su propio texto.** Un `wx.CheckBox` ya tiene nombre: no le pongas `StaticText` delante.
3. **Aceleradores.** `&` en la etiqueta: Alt+letra lleva al control. No repitas letras en la misma página ni con los botones (A de Aceptar, C de Cancelar, P de Aplicar, R de Restablecer, X de Examinar).
4. **Ayuda con Ctrl+H.** Cada control tiene `SetHelpText`. Requiere `wx.HelpProvider.Set(wx.SimpleHelpProvider())` antes de crear controles (el diálogo lo instala si falta). La tecla se captura con `EVT_CHAR_HOOK`, porque en un cuadro de texto de Windows Ctrl+H borra hacia atrás. La ayuda se abre en un campo de solo lectura que el lector recorre línea a línea, con el nombre de la opción delante.
5. **Botones en español** con acelerador: `&Aceptar`, `&Cancelar`, `A&plicar`, `&Restablecer valores de fábrica`. Aceptar es el botón por defecto (Intro).
6. **Escape cancela** (`SetEscapeId(wx.ID_CANCEL)`).
7. **Foco inicial** en la primera opción de la primera página (`EVT_INIT_DIALOG`).
8. **Pestañas con nombre**: `wx.Notebook`, una página por categoría. Ctrl+Tab y Ctrl+Mayús+Tab cambian de página; el lector anuncia el título.
9. **Muchas opciones**: cada página es un `ScrolledPanel`, que desplaza la vista hasta el control enfocado.
10. **Restablecer con confirmación.** Pregunta con «Sí/No» en español y «No» por defecto; solo cambia los controles (se puede cancelar después); no toca los secretos.
11. **Errores comprensibles.** El aviso dice qué opción y por qué («Tamaño de la letra: debe ser como máximo 40») y el foco vuelve a ese control.
12. **Contraseñas**: `wx.TE_PASSWORD`; el lector anuncia «protegido». Nunca se muestran en la ayuda ni en el registro.
13. **Carpetas**: campo de texto editable (se puede pegar una ruta) más «E&xaminar...» (`wx.DirDialog`). `wx.DirPickerCtrl` es menos predecible con lectores de pantalla.
14. **No bloquees** al abrir: si alguna opción necesita datos lentos (lista de voces, dispositivos de audio), cárgalos en un hilo y rellena el control con `wx.CallAfter`.

## Tipos de opción

| Tipo | Control | Valor |
|---|---|---|
| `bool` | `wx.CheckBox` | `True`/`False` |
| `entero` | `wx.SpinCtrl` | `int` |
| `decimal` | `wx.SpinCtrlDouble` | `float` |
| `texto` | `wx.TextCtrl` | `str` |
| `opcion` | `wx.Choice` con `(valor, etiqueta)` | el valor, no la etiqueta traducida |
| `carpeta` | `wx.TextCtrl` + «Examinar» | `str` |
| `secreto` | `wx.TextCtrl` protegido | va a `AlmacenSecretos` |

Para añadir un tipo (por ejemplo, una lista editable o un selector de sonido): crea el control en `_crear_opcion` (etiqueta antes), léelo en `_leer_control`, escríbelo en `_escribir_control` y añade una prueba.

## Muchas categorías: árbol + libro

Con más de 8 o 10 categorías, las pestañas se hacen largas de recorrer. Alternativa probada en aplicaciones grandes: un `wx.TreeCtrl` a la izquierda (categorías y subcategorías) y un `wx.Simplebook` a la derecha que muestra la página seleccionada. Mantén el mismo sistema de descriptores; solo cambia el contenedor. Pon una etiqueta «&Categorías:» antes del árbol, y al cambiar de categoría no muevas el foco (que el usuario decida con Tab cuándo entrar en la página).

## Pruebas del diálogo sin mostrarlo

- Crea `wx.App(False)` una vez por módulo, construye el diálogo sin `Show`/`ShowModal` y destrúyelo al final de cada prueba.
- Sustituye `mostrar_aviso`, `confirmar` y `mostrar_ayuda` con `monkeypatch` (son funciones de módulo precisamente para esto).
- Simula botones con un `wx.CommandEvent(wx.wxEVT_BUTTON, id)` procesado por el botón.
- Para `wx.CallAfter`, llama a `wx.Yield()` después.
- Lo que no cubren estas pruebas: cómo lo anuncia de verdad NVDA o JAWS. Pruébalo a mano con el lector antes de dar por buena la accesibilidad.

## Fuentes

- wxPython, `wx.HelpProvider` y `wx.SimpleHelpProvider`: <https://docs.wxpython.org/wx.HelpProvider.html>
- wxPython, `wx.Dialog.SetEscapeId`: <https://docs.wxpython.org/wx.Dialog.html>
- wxPython, `wx.lib.scrolledpanel`: <https://docs.wxpython.org/wx.lib.scrolledpanel.html>
- Microsoft, «Ensuring that UI Elements are Correctly Named» (la etiqueta estática inmediatamente anterior en el orden de tabulación da el nombre): <https://learn.microsoft.com/en-us/windows/win32/winauto/ensure-that-ui-elements-are-named-correctly>
