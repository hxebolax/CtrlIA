# Probar la interfaz wxPython

Todo lo que sigue está comprobado con wxPython 4.2.5 en Windows 11 (ver `CAMBIOS.md`). En otras versiones, compruébalo con una prueba antes de darlo por hecho.

## Una sola wx.App y ventanas sin mostrar

- Crea **una** `wx.App(False)` para toda la sesión (fixture `app_wx`). Crear y destruir muchas es lento y, con algunas versiones, inestable.
- Los marcos y diálogos **no se muestran**: casi todo funciona igual sin `Show()` (valores, eventos, nombres MSAA).
- Destruye lo creado al terminar cada prueba (`marco` lo hace, incluidos los diálogos que queden abiertos). Una ventana viva al final impide que la `wx.App` se cierre limpiamente.
- Tras `Destroy()`, el objeto Python sigue existiendo pero cualquier llamada lanza `RuntimeError: wrapped C/C++ object ... has been deleted`. Una ventana destruida vale `False` en un `if`: por eso el código que recibe un `wx.CallAfter` tardío comprueba `if self:`.

## Diálogos modales

`ShowModal()` bloquea hasta que alguien pulsa un botón: en una prueba, se colgaría. Dos técnicas:

1. **Probar el diálogo sin mostrarlo** (preferente): crearlo, dar valores a sus controles y llamar a los métodos que leen o validan. Así se prueba la lógica del diálogo.
2. **Probar el código que lo muestra** con la fixture `dialogo_modal`, que sustituye `ShowModal` en `wx.Dialog` y en los diálogos nativos (`MessageDialog`, `FileDialog`, `DirDialog`, `TextEntryDialog`, `SingleChoiceDialog`...), y también `wx.MessageBox`:
   - `dialogo_modal.responder(wx.ID_CANCEL)` encola respuestas; sin respuestas, devuelve `wx.ID_OK`.
   - Una respuesta puede ser una función que recibe el diálogo, rellena sus campos y devuelve el id: `responder(lambda d: (d.campo.SetValue("Ana"), wx.ID_OK)[1])`.
   - `dialogo_modal.ultimo` guarda clase, título, mensaje y textos del diálogo **en el momento de mostrarse**, porque el código suele destruirlo justo después.
   - `wx.MessageBox` devuelve `wx.OK`, `wx.YES`, `wx.NO` o `wx.CANCEL` (no los `ID_*`); la fixture hace la conversión.

Si un diálogo propio sobrescribe `ShowModal` en Python, parchea también su clase. Funciones como `wx.GetTextFromUser` o `wx.DirSelector` no pasan por `ShowModal`: parchéalas con `monkeypatch.setattr(wx, "DirSelector", ...)`.

No llames a `EndModal` fuera de un bucle modal: wx lanza un error de aserción. Para simular «Aceptar», llama al método que maneja el botón o envía el evento con `enviar_orden(boton)` y comprueba el efecto.

## Eventos

- **Qué genera evento y qué no:** `TextCtrl.SetValue` genera `EVT_TEXT`; `ChangeValue` no. `Choice.SetSelection` y `CheckBox.SetValue` **no** generan evento. `ListCtrl.Select(i)` y `Focus(i)` sí generan `EVT_LIST_ITEM_SELECTED` y `EVT_LIST_ITEM_FOCUSED` en Windows aunque la ventana no se vea.
- **Enviar un evento de orden:** `enviar_orden(boton)` crea un `wx.CommandEvent(wx.wxEVT_BUTTON, id)` y lo procesa. Para una casilla: `casilla.SetValue(True)` y `enviar_orden(casilla, wx.wxEVT_CHECKBOX)`.
- **Teclado:** `simular_tecla(ventana, wx.WXK_F5)` o `simular_tecla(campo, "s", ctrl=True)` crea un `wx.KeyEvent` (por defecto `wxEVT_CHAR_HOOK`) con `SetKeyCode`, `SetControlDown`, `SetShiftDown` y `SetAltDown`. El resultado dice:
  - `gestionado`: un manejador la consumió (`ProcessEvent` devuelve `True` solo si NO se llamó a `Skip()`);
  - `saltado`: se llamó a `Skip()`, así que la tecla sigue su camino.
  Un `EVT_CHAR_HOOK` enlazado en el marco recibe también las teclas enviadas a sus controles hijos. Comprueba siempre que las teclas no gestionadas (Tab, flechas, letras) quedan `saltado`: si no, la ventana deja sin teclado al usuario.
- Los caracteres se pasan en mayúscula (`"s"` → `ord("S")`), que es lo que entrega Windows en `EVT_KEY_DOWN` y `EVT_CHAR_HOOK`.

## Foco

`SetFocus()` sobre un control de una ventana oculta puede devolver ese control en `wx.Window.FindFocus()`, pero no lo demuestra: el foco real de Windows (el que sigue el lector de pantalla) solo existe con la ventana visible y activa. Para pruebas de foco fiables, usa `marco_visible` con `@pytest.mark.interfaz`, o prueba la **decisión** («qué control debe recibir el foco al abrir») en una función aparte.

## wx.CallAfter, hilos y temporizadores

- Sin `MainLoop`, los `wx.CallAfter` quedan pendientes. `procesar_eventos()` llama a `ProcessPendingEvents` y `Yield`.
- `wx.Timer` y `wx.CallLater` **no disparan** con un simple `wx.Yield()` si no hay un bucle de eventos activo. `procesar_eventos` y `esperar_hasta` activan uno temporal (`wx.GUIEventLoop` + `wx.EventLoopActivator`) y entonces sí disparan.
- Para un hilo: `hilo.join(timeout=...)` y después `esperar_hasta(lambda: condición)`, que falla con mensaje si se agota el tiempo. Nunca `time.sleep(1)` «por si acaso».
- Las excepciones dentro de un hilo no hacen fallar la prueba por sí solas; con `filterwarnings = ["error::pytest.PytestUnhandledThreadExceptionWarning"]` sí.
- Comprueba también que el hilo **no toca controles** directamente: la prueba de un servicio no debería necesitar wx.

## wx.UIActionSimulator: teclas y ratón reales

Envía entrada real al sistema operativo: la recibe **la ventana que tenga el foco**, sea la nuestra o no. Por eso:

- Solo en pruebas `@pytest.mark.interfaz`, excluidas por defecto.
- `escribir_con_simulador` muestra y trae al frente el marco, y **se salta la prueba** si la ventana no queda en primer plano (Windows impide a veces robar el foco) o si el foco no está en el control.
- `Text()` recibe bytes y solo garantiza letras ASCII: con la distribución de teclado española, dígitos y signos pueden salir cambiados. Para texto con acentos, `SetValue`.
- No lo ejecutes con el lector de pantalla del usuario en uso sin avisar: las ventanas que aparecen y desaparecen se anuncian.

## Accesibilidad con MSAA (comtypes)

`propiedades_msaa(ventana)` consulta `IAccessible` (vía `AccessibleObjectFromWindow` de `oleacc.dll`), la misma interfaz que usan los lectores, y devuelve `nombre`, `rol`, `estado` y `valor`. Necesita Windows y `comtypes` como dependencia de desarrollo.

Comprobaciones útiles:

- Todo control interactivo tiene nombre: el de su etiqueta, creada **antes** que el control con el mismo padre.
- Una casilla expone el estado marcado; un deslizador, su valor.
- Tras cambiar un ajuste, el nombre o el valor cambian donde deben.

Una prueba MSAA correcta **no demuestra** que NVDA o JAWS lo anuncien bien (pueden usar UI Automation o su propio modelo). Es una red contra regresiones; la comprobación con lector sigue pendiente.

## Voz

La fixture `voz_simulada` sustituye la función de voz por una que guarda los textos. Parchea **donde se usa**: si `app/ui/lista.py` hace `from app.core.voz import hablar`, el nombre que hay que parchear es `app.ui.lista.hablar`. Comprueba tanto lo que se anuncia como lo que **no** debe anunciarse (por ejemplo, releer la fila que el lector ya lee al moverse).

## Fuentes

- wx.UIActionSimulator: https://docs.wxpython.org/wx.UIActionSimulator.html
- wx.EventLoopActivator: https://docs.wxpython.org/wx.EventLoopActivator.html
- Excepciones en hilos con pytest: https://docs.pytest.org/en/stable/how-to/failures.html#warning-about-unraisable-exceptions-and-unhandled-thread-exceptions
