# Accesibilidad visual

Personas con baja visión usan ampliación, escalado de Windows (150-200 %), temas de alto contraste y fuentes grandes. Un recorrido de teclado correcto no sustituye estas comprobaciones.

## Reglas

- **Colores del sistema.** No fijes `SetBackgroundColour`/`SetForegroundColour`. Si dibujas, usa `wx.SystemSettings.GetColour(...)` (`color_sistema()`), y en alto contraste (`alto_contraste_activo()`) no pintes colores propios.
- **La información nunca depende solo del color** (rojo = error, verde = correcto): añade texto o un estado legible.
- **Foco visible.** Los controles nativos lo dibujan; los dibujados a mano deben dibujar el rectángulo de foco.
- **Escalado (DPI).** Declara la conciencia de DPI antes de crear `wx.App` (`activar_conciencia_dpi()`) o con un manifiesto. Mide con `ventana.FromDIP(n)`; no uses tamaños fijos en píxeles; usa sizers y `SetSizerAndFit`, e iconos con `wx.BitmapBundle`.
- **Fuentes del sistema.** `wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)`; no fijes tamaños de letra pequeños.
- **Redimensionado.** Los diálogos con mucho contenido admiten `wx.RESIZE_BORDER` y nada se corta ni se oculta al reducirlos. Los botones siempre visibles.
- **Contraste** de textos e iconos propios: al menos 4,5:1 para texto normal (criterio WCAG 1.4.3).

## Comprobaciones

1. Escalado de Windows al 100 %, 150 % y 200 %: nada cortado, nada solapado, todo alcanzable.
2. Tema de alto contraste (Configuración → Accesibilidad → Temas de contraste): textos legibles y foco visible.
3. Lupa de Windows (Windows + Más): el foco se sigue al tabular.
4. Tamaño de texto de Windows aumentado (Accesibilidad → Tamaño del texto).
