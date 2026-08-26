"""Fixtures comunes.

Los tres modulos (claude_pet, claude_pet_usage, claude_pet_collector) resuelven
sus rutas como constantes de MODULO evaluadas en el import
(`PET_DIR = Path.home() / ".claude" / "pet"` y todo lo que cuelga de ahi).
Parchear HOME no sirve: para cuando corre el test las constantes ya estan
congeladas. Hay que reasignar los atributos del modulo, y ademas los que
claude_pet importo POR NOMBRE desde claude_pet_usage (USAGE_PATH, STATE_PATH):
esas son referencias propias de claude_pet, no alias.
"""

import json

import pytest

import claude_pet
import claude_pet_collector
import claude_pet_usage


@pytest.fixture
def pet(tmp_path, monkeypatch):
    """Redirige todo el estado en disco de la mascota a tmp_path.

    Ningun test debe tocar ~/.claude/pet/ real: ahi viven fired.json con
    disparos de verdad, la posicion de la ventana y la config del usuario.
    """
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    monkeypatch.setattr(claude_pet, "PET_DIR", tmp_path)
    monkeypatch.setattr(claude_pet, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(claude_pet, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(claude_pet, "FIRED_PATH", tmp_path / "fired.json")
    monkeypatch.setattr(claude_pet, "POS_PATH", tmp_path / "position.json")

    # las copias que claude_pet trajo con `from claude_pet_usage import ...`
    monkeypatch.setattr(claude_pet, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.setattr(claude_pet, "STATE_PATH", tmp_path / "poller_state.json")

    monkeypatch.setattr(claude_pet_usage, "PET_DIR", tmp_path)
    monkeypatch.setattr(claude_pet_usage, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.setattr(claude_pet_usage, "STATE_PATH", tmp_path / "poller_state.json")
    # sin esto, el LAST_USAGE que dejo un test de poller anterior se filtra
    # a read_sessions() de otro test via get_last_usage()
    monkeypatch.setattr(claude_pet_usage, "LAST_USAGE", None)

    monkeypatch.setattr(claude_pet_collector, "PET_DIR", tmp_path)
    monkeypatch.setattr(claude_pet_collector, "SESSIONS_DIR", sessions)

    # por defecto apagado: cada test de poller lo enciende explicitamente
    monkeypatch.setattr(claude_pet, "HAS_POLLER", False)

    return tmp_path


@pytest.fixture
def write_session(pet):
    """Escribe un archivo de sesion con el shape que produce el collector."""
    def _write(name, **fields):
        payload = {
            "session_id": name,
            "model": "Opus",
            "cost_usd": 0.0,
            "context_pct": 0,
            "five_hour": None,
            "seven_day": None,
        }
        payload.update(fields)
        path = pet / "sessions" / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path
    return _write
