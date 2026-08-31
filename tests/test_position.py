"""clamp_to_screens: la mascota nunca arranca fuera de pantalla.

El bug que motiva esto se vio en produccion el 28/8: `position.json` tenia
x=-264 (guardado cuando habia un monitor a la izquierda del primario), el
monitor se desconecto, y la mascota -- 262px de ancho -- quedo dibujada
enteramente en coordenadas que ya no existian. El proceso corria, el tray
estaba, pero no habia un solo pixel visible: identico a no haber arrancado.

La posicion se persiste en coordenadas del escritorio virtual, que no son
estables entre sesiones. Estos tests fijan las dos mitades del contrato: una
posicion valida se respeta tal cual, y una imposible cae en la primaria.

Es la aritmetica sola, sin Qt: `_screen_areas()` traduce de QScreen a las
tuplas que entran aca, y eso sigue sin cobertura como el resto de lo que es Qt.
"""

import pytest

import claude_pet as cp


HD = (0, 0, 1920, 1040)          # primaria tipica, con la barra de tareas ya descontada
IZQUIERDA = (-1920, 0, 1920, 1040)  # secundaria a la izquierda: coordenadas negativas
W, H = 262, 152                  # la mascota a scale 1.0


def test_posicion_visible_no_se_toca():
    assert cp.clamp_to_screens(300, 200, W, H, [HD]) == (300, 200)


def test_el_caso_real_del_28_8_vuelve_a_la_primaria():
    """x=-264 con 262 de ancho: el borde derecho cae en -2, fuera de todo."""
    x, y = cp.clamp_to_screens(-264, 40, W, H, [HD])
    assert (x, y) == (0, 40)


def test_no_reubica_si_el_monitor_de_la_izquierda_sigue_conectado():
    """Misma posicion guardada, distinto hardware: ahi es valida y se respeta."""
    assert cp.clamp_to_screens(-264, 40, W, H, [HD, IZQUIERDA]) == (-264, 40)


def test_morder_el_borde_es_deliberado_y_se_respeta():
    """Dejarla mitad afuera es una posicion que el usuario eligio arrastrando."""
    x = 1920 - W // 2
    assert cp.clamp_to_screens(x, 500, W, H, [HD]) == (x, 500)


def test_asomando_menos_del_minimo_no_alcanza_para_agarrarla():
    """Con MIN_VISIBLE-1 px adentro no hay de donde arrastrarla de vuelta."""
    x = 1920 - (cp.MIN_VISIBLE - 1)
    nx, ny = cp.clamp_to_screens(x, 500, W, H, [HD])
    assert nx == 1920 - W and ny == 500


def test_justo_el_minimo_alcanza():
    x = 1920 - cp.MIN_VISIBLE
    assert cp.clamp_to_screens(x, 500, W, H, [HD]) == (x, 500)


def test_rescata_por_debajo_del_borde_inferior():
    """El eje Y se corrige igual que el X, no solo el caso horizontal."""
    nx, ny = cp.clamp_to_screens(400, 5000, W, H, [HD])
    assert (nx, ny) == (400, 1040 - H)


def test_rescate_va_a_la_primaria_que_es_la_primera():
    """screens[0] es el destino: _screen_areas() la pone al frente a proposito."""
    nx, ny = cp.clamp_to_screens(-9999, -9999, W, H, [HD, IZQUIERDA])
    assert (nx, ny) == (0, 0)


def test_offset_de_la_primaria_se_respeta_al_rescatar():
    """Una primaria que no arranca en (0,0) no debe recibir coordenadas de (0,0)."""
    desplazada = (100, 50, 1920, 1040)
    assert cp.clamp_to_screens(-9999, -9999, W, H, [desplazada]) == (100, 50)


def test_mas_grande_que_la_pantalla_se_pega_al_borde_no_a_negativo():
    """Con scale absurdo el clamp podria empujarla a coordenadas negativas."""
    chica = (0, 0, 200, 100)
    assert cp.clamp_to_screens(-500, -500, W, H, [chica]) == (0, 0)


def test_sin_pantallas_devuelve_lo_que_le_dieron():
    """Sin nada que consultar, inventar una posicion es peor que no tocar."""
    assert cp.clamp_to_screens(-264, 40, W, H, []) == (-264, 40)


def test_una_mascota_mas_chica_que_el_minimo_no_se_reubica_sola():
    """Si el minimo fuera absoluto, un scale bajo nunca contaria como visible."""
    w = h = cp.MIN_VISIBLE - 10
    assert cp.clamp_to_screens(500, 500, w, h, [HD]) == (500, 500)
