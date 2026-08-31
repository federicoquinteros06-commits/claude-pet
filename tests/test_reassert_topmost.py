"""_reassert_topmost: reafirma el z-order real, no solo el flag de Qt.

El caso que motivo la funcion (visto en vivo el 31/8): la mascota quedaba
invisible con el proceso vivo, bien posicionada, y con WS_EX_TOPMOST todavia
marcado -- pero el z-order real (GetWindow/GW_HWNDNEXT) la tenia detras de
diez ventanas comunes. self.raise_() de Qt no alcanza (equivale a HWND_TOP:
respeta la banda en la que la ventana YA esta). Estos tests verifican que se
llame a SetWindowPos con HWND_TOPMOST de verdad, y que un widget sin winId()
utilizable (Qt no inicializado, plataforma no-Windows) no rompa tick().
"""

import sys

import pytest

import claude_pet as cp


class FakeWidget:
    def __init__(self, hwnd=12345):
        self._hwnd = hwnd

    def winId(self):
        return self._hwnd


class FakeUser32:
    def __init__(self):
        self.calls = []

    def SetWindowPos(self, hwnd, insert_after, x, y, cx, cy, flags):
        self.calls.append((hwnd, insert_after, x, y, cx, cy, flags))
        return True


class FakeWindll:
    def __init__(self):
        self.user32 = FakeUser32()


def test_llama_a_setwindowpos_con_hwnd_topmost(monkeypatch):
    """El fix real: HWND_TOPMOST (-1), no HWND_TOP -- eso es lo que Qt ya
    hace solo y no alcanzo en el incidente."""
    fake_windll = FakeWindll()
    monkeypatch.setattr(cp, "HAS_WINDLL", True)
    monkeypatch.setattr(cp.ctypes, "windll", fake_windll, raising=False)

    cp._reassert_topmost(FakeWidget(hwnd=999))

    assert len(fake_windll.user32.calls) == 1
    hwnd, insert_after, *_ = fake_windll.user32.calls[0]
    assert hwnd == 999
    assert insert_after == cp._HWND_TOPMOST == -1


def test_noop_fuera_de_windows(monkeypatch):
    """En mac/Linux (HAS_WINDLL False) no debe tocar ctypes.windll ni el
    widget para nada."""
    monkeypatch.setattr(cp, "HAS_WINDLL", False)

    class BoomWidget:
        def winId(self):
            raise AssertionError("no deberia llamarse con HAS_WINDLL=False")

    cp._reassert_topmost(BoomWidget())  # no debe tirar


def test_no_rompe_el_tick_si_setwindowpos_falla(monkeypatch):
    """Un widget con winId() roto, o un SetWindowPos que tira, nunca debe
    escalar -- esto corre en tick() cada 1s, junto al resto del render."""
    class BoomWidget:
        def winId(self):
            raise RuntimeError("ventana nativa no lista todavia")

    monkeypatch.setattr(cp, "HAS_WINDLL", True)
    cp._reassert_topmost(BoomWidget())  # no debe propagar la excepcion


@pytest.mark.skipif(sys.platform != "win32", reason="requiere la API real de Windows")
def test_reassert_topmost_marca_ws_ex_topmost_en_una_ventana_de_verdad():
    """Regresion real -- no mockeada. Los tres tests de arriba reemplazan
    ctypes.windll entero por un doble en Python, asi que nunca ejercitan el
    marshaling real: es exactamente el hueco por el que paso el bug real del
    31/8 (SetWindowPos sin argtypes truncaba el HWND_TOPMOST de 64 bits a 32,
    no tiraba excepcion, y no reafirmaba nada -- verificado en vivo, no en
    teoria). Crea una ventana nativa real (clase STATIC, incorporada en
    Windows, no hace falta RegisterClass) y prueba contra la API de verdad.

    WS_VISIBLE + un pump minimo de mensajes es necesario para que el test sea
    determinista: contra una ventana invisible y sin cola de mensajes
    bombeada, SetWindowPos devuelve exito (ret=1) pero el bit a veces no
    llega a asentarse -- 1 de 3 corridas en la version sin esto. La mascota
    real siempre es visible y tiene el loop de Qt bombeando mensajes
    (app.exec()), asi que esto se ajusta a esa condicion real, no la elude."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = wintypes.HWND
    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST = 0x8

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD), ("pt", wintypes.POINT)]

    def _pump(n=20):
        msg = MSG()
        for _ in range(n):
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

    hwnd = user32.CreateWindowExW(
        0, "STATIC", "pytest_topmost_probe", WS_POPUP | WS_VISIBLE,
        0, 0, 10, 10, None, None, None, None,
    )
    assert hwnd, "no se pudo crear la ventana nativa de prueba"
    try:
        _pump()
        assert not (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST), (
            "precondicion: la ventana recien creada no deberia ser topmost"
        )

        class W:
            def winId(self):
                return hwnd

        cp._reassert_topmost(W())
        _pump()

        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        assert ex_style & WS_EX_TOPMOST, (
            "WS_EX_TOPMOST no quedo puesto -- el marshaling de ctypes esta roto"
        )
    finally:
        user32.DestroyWindow(hwnd)
