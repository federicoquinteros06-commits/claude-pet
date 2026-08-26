"""read_sessions(): agregacion de sesiones vivas y arbitraje con el poller."""

import json
import time

import claude_pet
import claude_pet_usage

FIVE = {"used_percentage": 40, "resets_at": 1787672400}
SEVEN = {"used_percentage": 52, "resets_at": 1787997600}


def test_sin_sesiones_es_distinto_de_sin_rate_limits(pet):
    """Bug 3 de HANDOFF: 'no hay nadie corriendo' no es 'la cuenta no expone
    rate_limits'. Con live == 0 la mascota dice una cosa; con live > 0 y
    ventanas en None, otra."""
    out = claude_pet.read_sessions()
    assert out["live"] == 0
    assert out["five_hour"] is None and out["seven_day"] is None
    assert out["cost_usd"] == 0.0


def test_sesion_viva_sin_rate_limits(pet, write_session):
    now = time.time()
    write_session("a", ts=now, rl_ts=now, context_pct=37, cost_usd=1.5)

    out = claude_pet.read_sessions()
    assert out["live"] == 1
    assert out["five_hour"] is None
    assert out["context_pct"] == 37


def test_sesion_stale_se_descarta(pet, write_session):
    """FRESH_WINDOW son 300s. HANDOFF: la sesion vieja quedo en 491s y la
    mascota la descarto bien."""
    now = time.time()
    write_session("viva", ts=now, rl_ts=now, five_hour=FIVE, cost_usd=2.0)
    write_session("zombie", ts=now - 491, rl_ts=now - 491, five_hour=FIVE, cost_usd=99.0)

    out = claude_pet.read_sessions()
    assert out["live"] == 1
    assert out["cost_usd"] == 2.0   # el costo de la zombie no se suma


def test_agrega_varias_sesiones(pet, write_session):
    """Costo se suma, contexto es el MAS cargado, rate_limits es el mas reciente."""
    now = time.time()
    write_session("vieja", ts=now - 60, rl_ts=now - 60, context_pct=80,
                  cost_usd=1.0, five_hour={"used_percentage": 30, "resets_at": 1})
    write_session("nueva", ts=now, rl_ts=now, context_pct=20,
                  cost_usd=2.5, five_hour=FIVE, seven_day=SEVEN)

    out = claude_pet.read_sessions()
    assert out["live"] == 2
    assert out["cost_usd"] == 3.5
    assert out["context_pct"] == 80
    assert out["five_hour"] == FIVE   # gana la de rl_ts mas nuevo


def test_poller_gana_cuando_su_dato_es_mas_nuevo(pet, write_session, monkeypatch):
    monkeypatch.setattr(claude_pet, "HAS_POLLER", True)
    now = time.time()
    write_session("a", ts=now, rl_ts=now - 100,
                  five_hour={"used_percentage": 30, "resets_at": 1})
    (pet / "usage.json").write_text(json.dumps({
        "ts": now - 10, "five_hour": FIVE, "seven_day": SEVEN,
    }), encoding="utf-8")

    out = claude_pet.read_sessions()
    assert out["usage_source"] == "poller"
    assert out["five_hour"] == FIVE


def test_poller_viejo_no_tapa_un_dato_recien_traido_por_el_statusline(
        pet, write_session, monkeypatch):
    """El poller solo manda si su dato es MAS NUEVO. Con USAGE_FRESH alto (900s)
    un valor viejo del poller tapaba uno fresco del statusLine, que llega gratis
    en los headers. Importa cuando un 429 deja al poller sin refrescar."""
    monkeypatch.setattr(claude_pet, "HAS_POLLER", True)
    now = time.time()
    fresco = {"used_percentage": 88, "resets_at": 1787672400}
    write_session("a", ts=now, rl_ts=now - 10, five_hour=fresco)
    (pet / "usage.json").write_text(json.dumps({
        "ts": now - 100, "five_hour": FIVE,
    }), encoding="utf-8")

    out = claude_pet.read_sessions()
    assert out.get("usage_source") is None
    assert out["five_hour"] == fresco


def test_poller_usa_memoria_cuando_el_disco_no_se_pudo_escribir(
        pet, write_session, monkeypatch):
    """Un antivirus puede bloquear la escritura de usage.json sin que el
    fetch de red falle (visto en produccion con avgMonFltProxy, ver
    CLAUDE.md). LAST_USAGE -- lo ultimo que el poller trajo con exito -- tiene
    que taparle el paso a un usage.json vencido o inexistente en disco."""
    monkeypatch.setattr(claude_pet, "HAS_POLLER", True)
    now = time.time()
    write_session("a", ts=now, rl_ts=now - 100,
                  five_hour={"used_percentage": 30, "resets_at": 1})
    # no hay usage.json en disco: la escritura del poller nunca se completo
    monkeypatch.setattr(claude_pet_usage, "LAST_USAGE", {
        "ts": now, "five_hour": FIVE, "seven_day": SEVEN,
    })

    out = claude_pet.read_sessions()
    assert out["usage_source"] == "poller"
    assert out["five_hour"] == FIVE


def test_disco_gana_si_es_mas_nuevo_que_la_memoria(pet, write_session, monkeypatch):
    """La memoria es un fallback, no una preferencia ciega: si el disco tiene
    un dato mas nuevo que LAST_USAGE, gana el disco."""
    monkeypatch.setattr(claude_pet, "HAS_POLLER", True)
    now = time.time()
    write_session("a", ts=now, rl_ts=now - 100,
                  five_hour={"used_percentage": 30, "resets_at": 1})
    (pet / "usage.json").write_text(json.dumps({
        "ts": now, "five_hour": FIVE, "seven_day": SEVEN,
    }), encoding="utf-8")
    monkeypatch.setattr(claude_pet_usage, "LAST_USAGE", {
        "ts": now - 50,
        "five_hour": {"used_percentage": 99, "resets_at": 1},
        "seven_day": SEVEN,
    })

    out = claude_pet.read_sessions()
    assert out["five_hour"] == FIVE


def test_poller_vencido_no_se_usa(pet, write_session, monkeypatch):
    """Mas viejo que USAGE_FRESH (900s) el dato del poller no vale."""
    monkeypatch.setattr(claude_pet, "HAS_POLLER", True)
    now = time.time()
    write_session("a", ts=now, rl_ts=now, five_hour=None)
    (pet / "usage.json").write_text(json.dumps({
        "ts": now - 1000, "five_hour": FIVE,
    }), encoding="utf-8")

    out = claude_pet.read_sessions()
    assert out.get("usage_source") is None
    assert out["five_hour"] is None


def test_error_del_poller_queda_visible(pet, write_session, monkeypatch):
    """Sin esto un poller caido es invisible: la mascota mostraria un dato viejo
    sin decir por que dejo de moverse."""
    monkeypatch.setattr(claude_pet, "HAS_POLLER", True)
    now = time.time()
    write_session("a", ts=now, rl_ts=now)
    (pet / "poller_state.json").write_text(json.dumps({
        "ts": now, "ok": False, "error": "HTTPError: 429",
        "consecutive_failures": 3, "next_retry_in": 120,
    }), encoding="utf-8")

    out = claude_pet.read_sessions()
    assert out["attempt_ok"] is False
    assert out["poller_error"] == "HTTPError: 429"
    assert out["poller_fails"] == 3
    assert out["next_due"] == now + 120


def test_json_corrupto_no_rompe_la_lectura(pet, write_session):
    now = time.time()
    write_session("buena", ts=now, rl_ts=now, five_hour=FIVE)
    (pet / "sessions" / "rota.json").write_text("{a medio escri", encoding="utf-8")

    out = claude_pet.read_sessions()
    assert out["live"] == 1
    assert out["five_hour"] == FIVE
