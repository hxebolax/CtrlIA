# Pruebas de accesibilidad

Tres niveles, de más barato a más fiable. Los dos primeros son automáticos y **no demuestran** que un lector concreto lo anuncie bien: son una red contra regresiones.

## 1. Lógica sin wx (pytest)

`tests/test_composicion.py`: la composición del anuncio de celda. Datos: fila con índice interno 2 (se anuncia la 3), cabecera «Estado», valor «Terminado».

| Fila | Cabecera | Contenido | Resultado |
|---|---|---|---|
| Sí | Sí | Sí | Fila 3, Estado, Terminado |
| Sí | Sí | No | Fila 3, Estado |
| Sí | No | Sí | Fila 3, Terminado |
| Sí | No | No | Fila 3 |
| No | Sí | Sí | Estado, Terminado |
| No | Sí | No | Estado |
| No | No | Sí | Terminado |
| No | No | No | Terminado (respaldo) |

Casos adicionales: valor vacío o solo espacios («Sin datos»), cabecera vacía, Unicode, saltos de línea, texto de más de 1000 caracteres (sin recortar), preferencias guardadas antiguas sin las claves nuevas (se usan los valores por defecto), combinación con restricciones locales y campo protegido que no se revela.

## 2. Controles y MSAA (pytest con wx y comtypes)

- `tests/test_controles_wx.py`: ventanas sin mostrar y voz sustituida por una lista. Navegación por columnas, preferencias leídas en cada anuncio, texto largo íntegro, lista vacía, inserción conservando el elemento del usuario, columna protegida, Intro que activa, casillas, botones en español, mensajes largos en campo de solo lectura.
- `tests/test_msaa.py` con `tests/msaa.py`: consulta la interfaz MSAA real (`AccessibleObjectFromWindow` + `IAccessible`), la misma que usan los lectores:
  - el campo toma el nombre y el atajo de su etiqueta;
  - un control creado antes que su etiqueta queda sin nombre (documenta el error);
  - el CheckListBox expone rol de casilla y estado marcado;
  - el deslizador expone «5 de 10»;
  - el selector de ruta y los campos internos tienen nombre.

Para una ventana nueva del proyecto, añade una prueba MSAA que recorra sus controles y compruebe que ninguno interactivo queda sin nombre:

```python
def test_dialogo_sin_controles_anonimos(app):
	dialogo = DialogoPerfil(None)
	for control in dialogo.GetChildren()[0].GetChildren():
		if isinstance(control, (wx.TextCtrl, wx.Choice, wx.ListCtrl, wx.CheckBox, wx.Button)):
			assert msaa.propiedades(control)["nombre"], f"{type(control).__name__} sin nombre"
	dialogo.Destroy()
```

## 3. Con tecnología de apoyo real (manual)

Herramientas:

- **NVDA** con el Visor de voz (Herramientas → Visor de voz), el visor de braille, NVDA+Tab (repite el foco) y NVDA+F1 (información técnica del objeto).
- **Accessibility Insights for Windows** (recomendada por Microsoft) o **Inspect.exe** (Windows SDK): nombre, rol, estado, valor y atajo de cada control.
- **AccEvent** (Accessible Event Watcher): comprueba que llegan los eventos de cambio de estado o de nombre.
- JAWS y Narrador si están disponibles: el resultado de uno **no vale para los demás**.

### Teclado y foco

- Tab y Mayús+Tab por toda la ventana en orden lógico; aceleradores Alt+letra; Intro y Escape desde cada punto.
- Lista vacía, con una fila, primera y última fila, primera y última columna, fila seleccionada sin foco y lista sin selección.
- Ctrl+1…9 y Ctrl+0; columna que no existe; consultar una columna no activa nada ni cambia la selección.
- Espacio: marca las casillas o hace la acción documentada.
- Al volver de Preferencias, de un error o de una confirmación, el foco queda en un lugar útil.
- Insertar filas antes de la actual, borrar la actual, ordenar y filtrar mantienen una posición lógica.
- Una actualización en segundo plano no roba el foco ni relee la lista.

### Lector de pantalla

- Nombre, rol, valor y estado de cada control al tabular.
- Diferencia entre la lectura nativa de filas y la lectura por columnas; texto largo completo por cada vía.
- Navegación rápida: sin anuncios dobles ni colas de filas ya abandonadas.
- Casillas: nombre y estado al cambiar, sin oírse dos veces.
- Con la voz de la aplicación desactivada: la información importante sigue siendo consultable.
- Progreso, final, error y cancelación anunciados sin saturar.

### Preferencias y persistencia

1. Abrir una lista y dejarla abierta.
2. En Preferencias, elegir solo «contenido»; Aceptar.
3. Volver a la lista: Izquierda/Derecha usan ya la nueva preferencia.
4. Reabrir Preferencias: la opción sigue marcada.
5. Cambiarla y Cancelar: se conserva la anterior.
6. Reiniciar: se conserva.
7. Desactivar los avisos del sistema: la lectura de columnas sigue funcionando.

### Visual y rendimiento

Escalado al 100/150/200 %, alto contraste, foco visible, redimensionado sin cortes, lista grande con refrescos mientras se navega sin tirones.

## Registro de una prueba manual

| Campo | Valor |
|---|---|
| Caso / tarea | |
| Entorno (Windows, wxPython, lector y versión) | |
| Pasos | |
| Resultado esperado | |
| Resultado observado | |
| Evidencia | código / prueba automática / observación real |
| Estado | Aprobado / Fallido / Pendiente / No aplicable |

Anota solo pruebas realizadas. Una inspección de código no es una aprobación de accesibilidad.
