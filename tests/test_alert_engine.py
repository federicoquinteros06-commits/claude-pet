"""AlertEngine: cada umbral dispara UNA vez por ventana.

Captura como regresion la escalera que HANDOFF.md dice haber verificado a mano
contra la mascota viva ("Punto 4 OK").
"""

import json

import claude_pet

WARNS = [90, 93]
ALARMS = [96, 98]
RESETS = 1787672400


def check(engine, pct, resets_at=RESETS):
    return engine.check("five_hour",
                        {"used_percentage": pct, "resets_at": resets_at},
                        WARNS, ALARMS)


def thresholds(fired):
    return [t for _lvl, t, _pct, _r in fired]


def test_escalera_dispara_cada_umbral_una_sola_vez(pet):
    engine = claude_pet.AlertEngine({})

    assert check(engine, 70) == []           # por debajo de todo
    assert thresholds(check(engine, 91)) == [90]
    assert thresholds(check(engine, 94)) == [93]
    assert thresholds(check(engine, 97)) == [96]
    assert thresholds(check(engine, 99)) == [98]

    # repetir el mismo pct no vuelve a disparar nada
    assert check(engine, 99) == []


def test_niveles_warn_y_alarm(pet):
    engine = claude_pet.AlertEngine({})
    niveles = {t: lvl for lvl, t, _p, _r in check(engine, 99)}
    assert niveles == {90: "warn", 93: "warn", 96: "alarm", 98: "alarm"}


def test_cambiar_resets_at_rearma_los_umbrales(pet):
    """Cuando la ventana rota cambia resets_at y todo se re-arma solo."""
    engine = claude_pet.AlertEngine({})
    assert thresholds(check(engine, 99)) == [90, 93, 96, 98]
    assert check(engine, 99) == []

    assert thresholds(check(engine, 99, resets_at=RESETS + 18000)) == [90, 93, 96, 98]


def test_dedupe_sobrevive_al_reinicio_del_proceso(pet):
    """La clave se persiste en fired.json: una mascota reiniciada no re-suena."""
    primera = claude_pet.AlertEngine({})
    assert thresholds(check(primera, 97)) == [90, 93, 96]

    segunda = claude_pet.AlertEngine({})
    assert check(segunda, 97) == []
    assert json.loads((pet / "fired.json").read_text(encoding="utf-8"))


def test_sin_datos_no_dispara_ni_escribe(pet):
    engine = claude_pet.AlertEngine({})

    assert engine.check("five_hour", None, WARNS, ALARMS) == []
    assert engine.check("five_hour", {}, WARNS, ALARMS) == []
    assert engine.check("five_hour", {"used_percentage": None, "resets_at": RESETS},
                        WARNS, ALARMS) == []

    assert not (pet / "fired.json").exists()


def test_ventanas_distintas_no_se_pisan(pet):
    """five_hour y seven_day llevan contadores independientes."""
    engine = claude_pet.AlertEngine({})
    assert thresholds(check(engine, 91)) == [90]

    siete = engine.check("seven_day", {"used_percentage": 91, "resets_at": RESETS},
                         WARNS, ALARMS)
    assert thresholds(siete) == [90]


# --------------------------------------------------- regresion: jitter de resets_at

def test_jitter_de_microsegundos_no_rearma_los_umbrales(pet):
    """El poller devuelve resets_at ISO-8601 con microsegundos que NO son
    estables entre respuestas: el mismo instante de reset vuelve como
    1787672399.55252, .565691, .614179... _epoch() los preserva como float y
    _key() los interpola en un string, asi que cada poll fabrica una clave
    nueva y el dedupe nunca acierta.

    Evidencia real: ~/.claude/pet/fired.json llego a 88 claves de UNA sola
    ventana (24 disparos del umbral 90), con un spread total de 0.94s entre el
    resets_at minimo y el maximo. O sea: 88 notificaciones + beeps + Slack
    donde debia haber 4.

    Dos ventanas de 5h reales estan separadas por 5 HORAS. Cualquier diferencia
    sub-segundo es la misma ventana.
    """
    engine = claude_pet.AlertEngine({})

    assert thresholds(check(engine, 99, resets_at=1787672399.55252)) == [90, 93, 96, 98]

    # mismo reset, otro microsegundo: no debe volver a sonar nada
    assert check(engine, 99, resets_at=1787672399.565691) == []
    assert check(engine, 99, resets_at=1787672400.492996) == []


def test_poller_y_statusline_comparten_la_misma_clave(pet):
    """Las dos fuentes reportan el mismo reset con distinta precision: el
    statusLine manda un epoch entero (1787672400) y el poller un float
    (1787672399.55252). read_sessions() alterna entre ambas segun cual sea mas
    fresca, asi que si la clave distingue entre las dos, cada alternancia
    re-dispara la ventana entera."""
    engine = claude_pet.AlertEngine({})

    assert thresholds(check(engine, 97, resets_at=1787672400)) == [90, 93, 96]
    assert check(engine, 97, resets_at=1787672399.55252) == []
