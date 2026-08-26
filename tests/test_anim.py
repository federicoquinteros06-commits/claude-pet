"""_sync_anim: apagar la animacion cuando no hay nada que animar.

El timer de animacion corre a 50ms, o sea 20 despertadas por segundo. En
macOS eso impide que el CPU entre en reposo profundo, que pesa mas en la
bateria que el 0.3% de CPU que se mide.

Y en "calm" no compraba nada: `_animate` solo llama a update() en
alarm/credit/flash, asi que el unico repintado era el del tick de 1s — y para
entonces `pulse` ya avanzo 20 pasos (0.12 * 20 = 2.4 rad, ~137 grados). El
logo pegaba un saltito de tamaño por segundo en vez de respirar.

Decision de producto: se pausa SOLO en calm (< 50%). Del 50% para arriba la
mascota se sigue moviendo igual que siempre, aunque en "watch" y "warn"
tampoco dibuje.
"""

import time

import pytest

import claude_pet as cp


class FakeTimer:
    """QTimer minimo: solo lo que _sync_anim usa."""

    def __init__(self):
        self.activo = False
        self.intervalo = None
        self.starts = 0

    def isActive(self):
        return self.activo

    def start(self, ms):
        self.activo = True
        self.intervalo = ms
        self.starts += 1

    def stop(self):
        self.activo = False


class FakePet:
    """Pet sin Qt: _sync_anim solo toca mood, flash_until, anim, pulse y update."""

    _anim_en_pausa = cp.Pet._anim_en_pausa
    _sync_anim = cp.Pet._sync_anim

    def __init__(self, mood="calm", flash_until=0.0, activo=True):
        self.mood = mood
        self.flash_until = flash_until
        self.pulse = 3.7          # a mitad del ciclo, como quedaria al pausar
        self.anim = FakeTimer()
        self.anim.activo = activo
        self.updates = 0

    def update(self):
        self.updates += 1


def test_calm_apaga_la_animacion():
    pet = FakePet(mood="calm")
    pet._sync_anim()

    assert not pet.anim.isActive()


def test_al_apagar_deja_el_logo_en_reposo():
    """pulse=0 -> sin(0)=0 -> breathe=1.0, el tamaño de reposo. Sin esto el
    logo queda congelado en un punto arbitrario del ciclo."""
    pet = FakePet(mood="calm")
    pet._sync_anim()

    assert pet.pulse == 0.0
    assert pet.updates == 1  # un ultimo repintado para que se vea el reposo


@pytest.mark.parametrize("mood", ["watch", "warn", "alarm", "credit", "unknown"])
def test_del_50_para_arriba_no_se_toca(mood):
    """Decision explicita: la pausa es solo para calm. watch (50-74%) y warn
    (75-89%) tampoco dibujan, pero se dejan como estaban."""
    pet = FakePet(mood=mood, activo=False)
    pet._sync_anim()

    assert pet.anim.isActive()
    assert pet.anim.intervalo == cp.ANIM_MS


def test_un_flash_en_calm_no_se_pausa():
    """El flash es una alerta en curso: aunque el estado sea calm (por ejemplo
    un aviso del 25% con el uso todavia bajo), tiene que animarse."""
    pet = FakePet(mood="calm", flash_until=time.time() + 5, activo=False)
    pet._sync_anim()

    assert pet.anim.isActive()


def test_flash_vencido_vuelve_a_pausar():
    pet = FakePet(mood="calm", flash_until=time.time() - 1)
    pet._sync_anim()

    assert not pet.anim.isActive()


def test_no_reinicia_un_timer_que_ya_corre():
    """Idempotente: tick() lo llama cada segundo. Un start() por tick
    reiniciaria el intervalo y haria temblar la animacion."""
    pet = FakePet(mood="alarm", activo=False)
    pet._sync_anim()
    pet._sync_anim()
    pet._sync_anim()

    assert pet.anim.starts == 1


def test_no_repinta_de_mas_si_ya_estaba_pausado():
    """Tambien idempotente del otro lado: sin el guard, cada tick en calm
    forzaria un update() extra — justo lo que se quiere evitar."""
    pet = FakePet(mood="calm", activo=False)
    pet._sync_anim()
    pet._sync_anim()

    assert pet.updates == 0


def test_transicion_calm_alarma_calm():
    """El ciclo completo, que es lo que pasa en la vida real."""
    pet = FakePet(mood="calm")
    pet._sync_anim()
    assert not pet.anim.isActive()

    pet.mood = "alarm"
    pet._sync_anim()
    assert pet.anim.isActive()

    pet.mood = "calm"
    pet._sync_anim()
    assert not pet.anim.isActive()
    assert pet.pulse == 0.0
