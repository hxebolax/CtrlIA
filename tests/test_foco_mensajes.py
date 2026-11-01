"""El foco de la lista de mensajes se queda donde lo dejaste (1.5.3).

Si dejabas el foco en el último mensaje y te ibas al cuadro de respuesta, cada
mensaje nuevo se lo llevaba al final: al volver con Alt+2 no seguías leyendo desde
donde estabas. Nada se muestra: ``FindFocus`` no da la lista, como cuando el foco
está en el cuadro de respuesta.
"""

from __future__ import annotations

import pytest

pytest.importorskip("wx")

from app.services.gestor import MensajeVista  # noqa: E402
from tests.test_ventana_principal import ventana  # noqa: E402, F401


def _con_mensajes(ventana, cuantos: int = 3) -> list[MensajeVista]:  # noqa: F811
	mensajes = [MensajeVista("asistente", f"Mensaje {n}", float(n), id=n) for n in range(1, cuantos + 1)]
	ventana.sesion_actual = "ctrlia:prueba"
	ventana._anteriores = []  # noqa: SLF001
	ventana.mostrar_mensajes(mensajes)
	return mensajes


def test_al_abrir_el_foco_va_al_ultimo(ventana):  # noqa: F811
	_con_mensajes(ventana)
	assert ventana.lista_mensajes.GetFocusedItem() == 2


def test_dejado_en_el_ultimo_no_lo_arrastran_los_nuevos(ventana):  # noqa: F811
	_con_mensajes(ventana)
	for numero in (4, 5, 6):
		ventana.anadir_o_actualizar_mensaje(MensajeVista("asistente", f"Mensaje {numero}", float(numero), id=numero))
	assert ventana.lista_mensajes.GetItemCount() == 6
	assert ventana.lista_mensajes.GetFocusedItem() == 2
	assert ventana.mensaje_actual().texto == "Mensaje 3"


def test_dejado_en_medio_tampoco_se_mueve(ventana):  # noqa: F811
	_con_mensajes(ventana)
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	ventana.anadir_o_actualizar_mensaje(MensajeVista("asistente", "Nuevo", 4.0, id=4))
	assert ventana.lista_mensajes.GetFocusedItem() == 0


def test_actualizar_un_mensaje_no_mueve_el_foco(ventana):  # noqa: F811
	_con_mensajes(ventana)
	ventana.lista_mensajes.enfocar_fila(1, anunciar=False)
	ventana.anadir_o_actualizar_mensaje(MensajeVista("asistente", "Cambiado", 3.0, id=3))
	assert ventana.lista_mensajes.GetFocusedItem() == 1
	assert ventana._mensajes[2].texto == "Cambiado"  # noqa: SLF001


def test_el_primer_mensaje_de_una_lista_vacia_recibe_el_foco(ventana):  # noqa: F811
	ventana.sesion_actual = "ctrlia:prueba"
	ventana.anadir_o_actualizar_mensaje(MensajeVista("usuario", "Hola", 1.0, id=1))
	assert ventana.lista_mensajes.GetFocusedItem() == 0


def test_recargar_vuelve_al_mensaje_donde_estabas(ventana, monkeypatch):  # noqa: F811
	mensajes = _con_mensajes(ventana)
	ventana.lista_mensajes.enfocar_fila(0, anunciar=False)
	pendientes = []
	monkeypatch.setattr(type(ventana), "en_segundo_plano",
		lambda self, funcion, *args, al_terminar=None, al_fallar=None, clave=None: pendientes.append(al_terminar))
	ventana.recargar_mensajes()
	pendientes[-1]([*mensajes, MensajeVista("asistente", "Nuevo", 4.0, id=4)])
	assert ventana.lista_mensajes.GetItemCount() == 4
	assert ventana.mensaje_actual().texto == "Mensaje 1"


def test_recargar_sin_el_mensaje_deja_el_foco_como_al_abrir(ventana, monkeypatch):  # noqa: F811
	_con_mensajes(ventana)
	pendientes = []
	monkeypatch.setattr(type(ventana), "en_segundo_plano",
		lambda self, funcion, *args, al_terminar=None, al_fallar=None, clave=None: pendientes.append(al_terminar))
	ventana.recargar_mensajes()
	pendientes[-1]([MensajeVista("asistente", "Otro", 1.0, id=50), MensajeVista("asistente", "Otro más", 2.0, id=51)])
	assert ventana.lista_mensajes.GetFocusedItem() == 1
