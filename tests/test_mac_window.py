"""_mac_keep_visible: que la mascota no desaparezca de la pantalla.

El bug que motivo esto: `Qt.Tool` en macOS se traduce a un NSPanel con
hidesOnDeactivate=YES, y la mascota NUNCA es la app activa (usa Qt.Tool
justamente para no robar foco). Resultado: el overlay se ocultaba apenas
tocabas cualquier otra ventana.

Medido en vivo el 25/8/2026 sobre la ventana real, antes del fix:

    collectionBehavior = 258   (MoveToActiveSpace | FullScreenAuxiliary)
    level              = 8
    hidesOnDeactivate  = True

y despues:

    collectionBehavior = 257   (CanJoinAllSpaces | FullScreenAuxiliary)
    level              = 25    (NSStatusWindowLevel)
    hidesOnDeactivate  = False

El 258 explica la segunda mitad del sintoma: MoveToActiveSpace mueve la
ventana al Space activo CUANDO LA APP SE ACTIVA, y esta app no se activa
nunca, asi que se quedaba en el Space donde nacio.

Estos tests no abren ventanas (no hay display garantizado en CI): cubren el
guard de plataforma y las constantes, que es lo que se puede verificar sin
un NSWindow de verdad.
"""

import claude_pet as cp


def test_constantes_son_las_de_appkit():
    """Si alguna cambia, el fix deja de hacer lo que dice hacer."""
    assert cp.NS_ALL_SPACES == 1           # NSWindowCollectionBehaviorCanJoinAllSpaces
    assert cp.NS_FULLSCREEN_AUX == 256     # NSWindowCollectionBehaviorFullScreenAuxiliary
    assert cp.NS_STATUS_WINDOW_LEVEL == 25 # NSStatusWindowLevel

    # el valor combinado que se le manda a setCollectionBehavior:
    assert cp.NS_ALL_SPACES | cp.NS_FULLSCREEN_AUX == 257


def test_no_hace_nada_fuera_de_mac(monkeypatch):
    """En Windows/Linux ni siquiera debe tocar el widget: alla el overlay
    no tiene este problema y WA_MacAlwaysShowToolWindow no significa nada."""
    tocado = []

    class FakeWidget:
        def setAttribute(self, *a):
            tocado.append(a)

        def winId(self):
            tocado.append("winId")
            return 1

    for plataforma in ("win32", "linux"):
        monkeypatch.setattr(cp.sys, "platform", plataforma)
        cp._mac_keep_visible(FakeWidget())

    assert tocado == []


def test_objc_es_none_fuera_de_mac(monkeypatch):
    monkeypatch.setattr(cp.sys, "platform", "win32")
    assert cp._objc() is None


def test_sin_runtime_objc_no_revienta(monkeypatch):
    """Si el runtime no carga, la mascota tiene que seguir andando: perder el
    fix de visibilidad es feo, crashear al arrancar es peor."""
    monkeypatch.setattr(cp.sys, "platform", "darwin")
    monkeypatch.setattr(cp, "_objc", lambda: None)

    class FakeWidget:
        def setAttribute(self, *a):
            pass

    cp._mac_keep_visible(FakeWidget())  # no debe tirar


def test_widget_sin_nswindow_no_revienta(monkeypatch):
    """Entre show() y el NSWindow real hay una ventana de tiempo; si winId()
    todavia no tiene ventana detras, se sale sin tocar nada."""
    monkeypatch.setattr(cp.sys, "platform", "darwin")

    import ctypes
    fake_objc = type("O", (), {
        "objc_msgSend": lambda *a, **k: None,
        "sel_registerName": lambda s: None,
    })()
    monkeypatch.setattr(cp, "_objc", lambda: (fake_objc, ctypes))
    monkeypatch.setattr(cp, "_msg", lambda *a, **k: None)  # window -> nil

    class FakeWidget:
        def setAttribute(self, *a):
            pass

        def winId(self):
            return 12345

    cp._mac_keep_visible(FakeWidget())  # no debe tirar
