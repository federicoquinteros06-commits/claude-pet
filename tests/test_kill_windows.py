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


# ============================================ recordatorio de la semanal
# El corte semanal es UNO por ventana. Como el % semanal no baja hasta el
# reset (dias despues), sin esto la mascota se queda muda justo cuando peor
# esta la cuenta. seven_day_reminder() es lo que llena ese hueco.

HORA = 3600


def recordatorio(cfg=None, seven=98, ultimo=0.0, ahora=HORA):
    return claude_pet.seven_day_reminder(
        cfg or {}, estado(seven=seven), ultimo, ahora)


def test_arriba_del_umbral_recuerda(pet):
    assert recordatorio(seven=98) == 98


def test_debajo_del_umbral_no_recuerda(pet):
    assert recordatorio(seven=96) is None


def test_justo_en_el_umbral_recuerda(pet):
    assert recordatorio(seven=97) == 97


def test_antes_del_intervalo_no_repite(pet):
    assert recordatorio(seven=99, ultimo=0.0, ahora=HORA - 1) is None


def test_cumplido_el_intervalo_repite(pet):
    assert recordatorio(seven=99, ultimo=0.0, ahora=HORA) == 99


def test_intervalo_configurable(pet):
    cfg = {"seven_day_reminder_minutes": 15}
    assert recordatorio(cfg, ultimo=0.0, ahora=15 * 60 - 1) is None
    assert recordatorio(cfg, ultimo=0.0, ahora=15 * 60) == 98


def test_cero_lo_apaga(pet):
    assert recordatorio({"seven_day_reminder_minutes": 0}) is None


def test_none_lo_apaga(pet):
    assert recordatorio({"seven_day_reminder_minutes": None}) is None


def test_sin_umbral_de_corte_no_hay_que_recordar(pet):
    """Se ancla a seven_day_kill_threshold: sin linea de peligro no hay nada
    que recordar, y tener un segundo numero para lo mismo se desincroniza."""
    assert recordatorio({"seven_day_kill_threshold": None}) is None


def test_sigue_el_umbral_de_corte_que_se_configure(pet):
    cfg = {"seven_day_kill_threshold": 80}
    assert recordatorio(cfg, seven=85) == 85
    assert recordatorio(cfg, seven=79) is None


def test_sin_dato_semanal_no_recuerda(pet):
    assert claude_pet.seven_day_reminder({}, estado(five=99), 0.0, HORA) is None


def test_no_depende_de_auto_kill(pet):
    """Apagar el corte apaga el corte, no la informacion."""
    assert recordatorio({"auto_kill_enabled": False}, seven=99) == 99


def test_al_arrancar_avisa_en_el_primer_tick(pet):
    """recordatorio_at arranca en 0: si prendes la mascota ya pasado el
    umbral, te lo dice ya, no dentro de una hora."""
    assert recordatorio(seven=99, ultimo=0.0, ahora=1e9) == 99


def test_config_por_defecto_trae_el_intervalo(pet):
    assert claude_pet.DEFAULT_CONFIG["seven_day_reminder_minutes"] == 60
