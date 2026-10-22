# wxPython y las traducciones

Hay **dos** sistemas de traducción funcionando a la vez y hay que coordinarlos:

| Sistema | Qué traduce | Dónde está el catálogo |
|---|---|---|
| gettext de Python (`_`) | Todos los textos de la aplicación | `data/locales/<idioma>/LC_MESSAGES/<dominio>.mo` |
| `wx.Locale` de wxWidgets | Botones y diálogos estándar de wx: Aceptar, Cancelar, Sí, No, etiquetas de `wx.ID_*` | `wx/locale/<idioma>/LC_MESSAGES/wxstd.mo` (lo trae wxPython) |

## Orden de arranque

```python
from app.core import i18n

def main():
	idioma = i18n.inicializar(configuracion.get("idioma"))  # 1. antes de importar ventanas
	import wx
	from app.ui.ventana_principal import VentanaPrincipal  # 2. ya con _ instalada
	app = wx.App(False)
	i18n.configurar_wx(idioma)  # 3. después de wx.App
	VentanaPrincipal(None).Show()
	app.MainLoop()
```

- `i18n.inicializar` va primero porque los módulos que se importan después pueden usar `_` al cargarse.
- `configurar_wx` va **después** de `wx.App`: en Windows con Python 3.8 o posterior, `App.InitLocale` fija la configuración regional «C» al crear la aplicación y desharía un `wx.Locale` anterior.
- `configurar_wx` usa el idioma **efectivo** que devolvió `inicializar`: si no había catálogo del idioma pedido, la aplicación está en español y los botones estándar también deben estarlo.

## Conservar la referencia

`wx.Locale` solo funciona mientras el objeto existe. Si se crea en una función y no se guarda, el recolector de basura lo destruye y los botones vuelven al inglés sin ningún error. La plantilla lo guarda en `i18n._locale_wx`; no crees otro en otra parte.

## Qué hace wx.LogNull en configurar_wx

Si el idioma pedido no está instalado en Windows, wx muestra un cuadro de error en inglés que interrumpe el arranque y el lector de pantalla. La plantilla lo silencia con `wx.LogNull` y lo escribe en el registro con `log.warning`.

## Cuadros nativos de Windows

`wx.FileDialog`, `wx.DirDialog`, `wx.ColourDialog` y algunos `wx.MessageBox` los dibuja Windows: salen en el idioma de Windows, no en el de la aplicación. Para `wx.MessageDialog`, fija las etiquetas: `dialogo.SetYesNoLabels(_("&Sí"), _("&No"))`, `SetOKCancelLabels(_("&Aceptar"), _("&Cancelar"))`.

## Botones con ID estándar

- `wx.Button(self, wx.ID_OK)` toma la etiqueta de `wxstd.mo`. Funciona, pero no siempre trae acelerador y su texto no está en tu catálogo.
- Recomendado: ID estándar (para que Intro y Escape funcionen) con etiqueta propia traducida: `wx.Button(self, wx.ID_OK, _("&Aceptar"))`.

## Cambiar de idioma sin reiniciar

| Qué | ¿Cambia en caliente? | Cómo |
|---|---|---|
| Textos pedidos con `_()` después del cambio | Sí | Automático: `builtins._` apunta al catálogo nuevo |
| Menús, etiquetas, columnas, barra de estado ya creados | No | Registrar una función con `i18n.al_cambiar_idioma` que los reconstruya |
| Diálogos que se abran después | Sí | Se construyen de nuevo |
| Botones y diálogos estándar de wx | No | Reiniciar |
| Constantes calculadas al importar con `_()` | No | Usar `N_` y traducir al mostrar |

Lo más fiable, y lo que se recomienda: guardar el idioma en la configuración y ofrecer «Reiniciar ahora» con un diálogo accesible. `i18n.cambiar_idioma` devuelve si hace falta reiniciar.

Un `wx.Locale` nuevo con ventanas abiertas deja la configuración regional a medias: wx exige destruir los `wx.Locale` en orden inverso al de creación y no es seguro hacerlo con la interfaz en marcha.

## Selector de idioma en las preferencias

- Etiqueta `StaticText` «&Idioma:» antes de un `wx.Choice`.
- Opciones: «Automático (idioma del sistema)» y después `i18n.idiomas_disponibles()`, con `i18n.nombre_idioma(codigo)`: cada idioma escrito en su propio idioma (English, Français), para que quien no entiende español encuentre el suyo.
- Guarda el código (`"en"`, `"auto"`), nunca el nombre mostrado.

## wx con catálogos propios

wx también puede leer el `.mo` de la aplicación (`wx.GetTranslation`), pero no hace falta: la aplicación usa gettext de Python. Las pruebas de la plantilla lo usan solo como segundo lector independiente del formato `.mo`. Si algún día se usara, `AddCatalog(dominio, wx.LANGUAGE_SPANISH, "UTF-8")` indica que los originales están en español; sin eso, wx supone inglés.

## Fuentes

- Documentación de gettext en Python: https://docs.python.org/3/library/gettext.html
- Internacionalización en wxWidgets: https://docs.wxwidgets.org/3.2/overview_i18n.html
- Clase wx.Locale en wxPython: https://docs.wxpython.org/wx.Locale.html
