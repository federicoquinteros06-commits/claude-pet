"""kill_events(): que ventana dispara el corte automatico, y cuando.

La decision de cortar vive afuera de Pet.tick() justamente para poder probarla
sin Qt y sin matar nada. Estos tests cubren la eleccion de umbral y el dedupe;
lo que pasa DESPUES (que procesos mueren) es test_kill.py.
"""

import claude_pet

R5 = 1787763000      # resets_at de la ventana de 5h
R7 = 1788350400      # resets_at de la ventana semanal

SEMANAL = "de la ventana semanal"
CINCO_H = "de la ventana de 5h"


def estado(five=None, seven=None):
    def w(pct, resets_at):
        return None if pct is None else {"used_percentage": pct,
                                         "resets_at": resets_at}
    return {"five_hour": w(five, R5), "seven_day": w(seven, R7)}


def eventos(engine, cfg=None, **kw):
    return claude_pet.kill_events(engine, cfg or {}, estado(**kw))


# --------------------------------------------------------------- la semanal

def test_semanal_al_97_corta(pet):
    engine = claude_pet.AlertEngine({})
    assert eventos(engine, seven=97) == [(SEMANAL, 97, 97)]


def test_semanal_al_96_no_corta(pet):
    engine = claude_pet.AlertEngine({})
    assert eventos(engine, seven=96) == []


def test_semanal_corta_una_sola_vez_por_ventana(pet):
    """El punto delicado del feature: la ventana semanal puede tardar dias en
    resetear. Si el corte se repitiera en cada poll, cruzar el 97% dejaria la
    maquina sin Claude Code hasta el reset."""
    engine = claude_pet.AlertEngine({})
    assert len(eventos(engine, seven=97)) == 1
    assert eventos(engine, seven=98) == []
    assert eventos(engine, seven=100) == []


def test_semanal_se_rearma_con_la_ventana_nueva(pet):
    engine = claude_pet.AlertEngine({})
    assert len(eventos(engine, seven=99)) == 1
    otra = {"five_hour": None,
            "seven_day": {"used_percentage": 99, "resets_at": R7 + 604800}}
    assert claude_pet.kill_events(engine, {}, otra) == [(SEMANAL, 97, 99)]


def test_umbral_semanal_configurable(pet):
    engine = claude_pet.AlertEngine({})
    cfg = {"seven_day_kill_threshold": 80}
    assert eventos(engine, cfg, seven=85) == [(SEMANAL, 80, 85)]


def test_none_apaga_solo_la_semanal(pet):
    engine = claude_pet.AlertEngine({})
    cfg = {"seven_day_kill_threshold": None}
    assert eventos(engine, cfg, five=96, seven=99) == [(CINCO_H, 95, 96)]


def test_sin_dato_semanal_no_rompe(pet):
    """El poller puede devolver five_hour y seven_day=None."""
    engine = claude_pet.AlertEngine({})
    assert eventos(engine, five=96, seven=None) == [(CINCO_H, 95, 96)]


# ------------------------------------------------- la de 5h no se rompe

def test_cinco_horas_al_95_sigue_cortando(pet):
    engine = claude_pet.AlertEngine({})
    assert eventos(engine, five=95, seven=3) == [(CINCO_H, 95, 95)]


def test_none_apaga_solo_la_de_5h(pet):
    engine = claude_pet.AlertEngine({})
    cfg = {"kill_threshold": None}
    assert eventos(engine, cfg, five=99, seven=98) == [(SEMANAL, 97, 98)]


# ------------------------------------------------------------ las dos juntas

def test_las_dos_a_la_vez_devuelve_la_semanal_ultima(pet):
    """Pet.tick() anuncia cortes[-1]: cuando cruzan las dos, el usuario tiene
    que leer la semanal, que es la que no se destraba en horas."""
    engine = claude_pet.AlertEngine({})
    ev = eventos(engine, five=96, seven=98)
    assert ev == [(CINCO_H, 95, 96), (SEMANAL, 97, 98)]
    assert ev[-1][0] == SEMANAL


def test_cada_ventana_dedupea_por_su_cuenta(pet):
    """Que la de 5h ya haya cortado no consume el corte de la semanal."""
    engine = claude_pet.AlertEngine({})
    assert eventos(engine, five=96, seven=3) == [(CINCO_H, 95, 96)]
    assert eventos(engine, five=97, seven=97) == [(SEMANAL, 97, 97)]


def test_el_aviso_semanal_no_consume_el_corte(pet):
    """seven_day_thresholds (aviso) y seven_day_kill (corte) miran la misma
    ventana con namespaces distintos: cruzar 95 avisando no puede comerse la
    clave del corte de 97."""
    engine = claude_pet.AlertEngine({})
    info = {"used_percentage": 98, "resets_at": R7}
    avisos = engine.check("seven_day", info, [85], [95])
    assert [t for _l, t, _p, _r in avisos] == [85, 95]
    assert eventos(engine, seven=98) == [(SEMANAL, 97, 98)]


# ------------------------------------------------------- interruptor maestro

def test_auto_kill_apagado_no_corta_ninguna(pet):
    engine = claude_pet.AlertEngine({})
    cfg = {"auto_kill_enabled": False}
    assert eventos(engine, cfg, five=100, seven=100) == []


def test_auto_kill_apagado_no_quema_los_umbrales(pet):
    """Apagar y volver a prender tiene que dejar el corte disponible: si
    kill_events marcara los umbrales con el interruptor en off, prenderlo de
    nuevo dentro de la misma ventana ya no cortaria nunca."""
    engine = claude_pet.AlertEngine({})
    assert eventos(engine, {"auto_kill_enabled": False}, seven=99) == []
    assert eventos(engine, {}, seven=99) == [(SEMANAL, 97, 99)]


def test_config_por_defecto_trae_el_umbral_semanal(pet):
    assert claude_pet.DEFAULT_CONFIG["seven_day_kill_threshold"] == 97
