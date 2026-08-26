"""claude_pet_collector: normalizacion de used_percentage y escritura de sesion.

El statusLine manda used_percentage como venga del lado de Claude Code -- si
llega un float como 56.999999999999, un f-string lo DIBUJA como "57%" (redondea
al mostrar) pero la comparacion de umbrales en claude_pet.py usa el numero
crudo, que todavia no llega a 57. La pantalla dice que ya paso el umbral y la
alerta no dispara. `normalize_window` existe para que el collector redondee
ANTES de escribir, igual que ya hace el poller (claude_pet_usage._window).
"""

import json

import pytest

import claude_pet_collector as collector


# ------------------------------------------------------------ normalize_window

@pytest.mark.parametrize("crudo,esperado", [
    (56.999999999999, 57),
    (40.2, 40),
    (40.6, 41),
    (0.0, 0),
    (100.0, 100),
])
def test_redondea_used_percentage(crudo, esperado):
    w = collector.normalize_window({"used_percentage": crudo, "resets_at": 123})
    assert w["used_percentage"] == esperado
    assert isinstance(w["used_percentage"], int)
    assert w["resets_at"] == 123   # el resto de la ventana no se toca


@pytest.mark.parametrize("malo", [None, {}, {"resets_at": 123}])
def test_devuelve_tal_cual_sin_used_percentage(malo):
    assert collector.normalize_window(malo) == malo


def test_no_pisa_el_dict_original():
    """Si mutara el dict de entrada, el `rate_limits` original (compartido
    con render()) quedaria con el redondeado tambien -- no es lo que rompe
    nada hoy, pero un dict nuevo es la garantia barata de que no importe."""
    original = {"used_percentage": 56.7, "resets_at": 123}
    w = collector.normalize_window(original)
    assert original["used_percentage"] == 56.7
    assert w is not original


# --------------------------------------------------------------------- main()

def _run(monkeypatch, capsys, payload):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.argv", ["claude_pet_collector.py"])
    collector.main()
    return capsys.readouterr()


def test_escribe_la_sesion_con_el_porcentaje_redondeado(pet, monkeypatch, capsys):
    _run(monkeypatch, capsys, {
        "session_id": "abc-123",
        "model": {"display_name": "Opus"},
        "workspace": {"current_dir": "/tmp"},
        "context_window": {"used_percentage": 10},
        "rate_limits": {
            "five_hour": {"used_percentage": 56.999999999999, "resets_at": 999},
            "seven_day": {"used_percentage": 40.2, "resets_at": 999},
        },
    })

    escrito = json.loads((pet / "sessions" / "abc-123.json").read_text(encoding="utf-8"))
    assert escrito["five_hour"]["used_percentage"] == 57
    assert escrito["seven_day"]["used_percentage"] == 40


def test_la_linea_impresa_coincide_con_lo_escrito(pet, monkeypatch, capsys):
    """render() y el payload leen el MISMO rate_limits normalizado -- no dos
    numeros redondeados por separado que puedan divergir."""
    out = _run(monkeypatch, capsys, {
        "session_id": "xyz",
        "model": {"display_name": "Opus"},
        "workspace": {"current_dir": "/tmp"},
        "context_window": {"used_percentage": 0},
        "rate_limits": {"five_hour": {"used_percentage": 56.999999999999, "resets_at": 999}},
    })

    assert "5h 57%" in out.out
    escrito = json.loads((pet / "sessions" / "xyz.json").read_text(encoding="utf-8"))
    assert escrito["five_hour"]["used_percentage"] == 57


def test_json_invalido_no_rompe(pet, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("no es json"))
    monkeypatch.setattr("sys.argv", ["claude_pet_collector.py"])
    collector.main()   # no debe tirar

    assert list((pet / "sessions").glob("*.json")) == []


def test_sin_rate_limits_no_revienta(pet, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, {
        "session_id": "sin-limites",
        "model": {"display_name": "Opus"},
        "workspace": {"current_dir": "/tmp"},
        "context_window": {"used_percentage": 5},
    })

    assert "(sin rate_limits)" in out.out
    escrito = json.loads((pet / "sessions" / "sin-limites.json").read_text(encoding="utf-8"))
    assert escrito["five_hour"] is None
    assert escrito["seven_day"] is None
