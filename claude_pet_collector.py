#!/usr/bin/env python3
"""
claude_pet_collector.py — statusLine de Claude Code.

Hace dos cosas:
  1. imprime la linea de estado en la terminal (stdout)
  2. escribe el JSON de la sesion en ~/.claude/pet/sessions/<session_id>.json

NO hace red ni nada lento: el statusLine bloquea la UI mientras corre.
Las alertas y Slack los maneja claude_pet.py.

Uso normal (via settings.json, ver README).
Debug:  echo '{...}' | python claude_pet_collector.py --dump
        -> vuelca el JSON crudo a ~/.claude/pet/last_raw.json para que
           verifiques si tu cuenta expone rate_limits.
"""

import json
import os
import sys
import time
from pathlib import Path

PET_DIR = Path.home() / ".claude" / "pet"
SESSIONS_DIR = PET_DIR / "sessions"
STALE_AFTER = 15 * 60  # limpiar sesiones muertas despues de 15 min

# Windows: la consola default es cp1252 y los bloques del bar (Unicode) revientan
# con UnicodeEncodeError -> el statusLine sale exit 1 y no imprime nada.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def atomic_write(path: Path, payload: dict) -> None:
    """Escritura atomica: tmp + rename. Evita que la mascota lea un JSON a medio escribir."""
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def cleanup_stale() -> None:
    """Borra archivos de sesiones que ya no estan vivas."""
    now = time.time()
    try:
        for f in SESSIONS_DIR.glob("*.json"):
            if now - f.stat().st_mtime > STALE_AFTER:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def normalize_window(w):
    """Redondea `used_percentage` a int, igual que hace el poller
    (claude_pet_usage._window). Sin esto, un valor como 56.999999999999
    se DIBUJA como "57%" (el f-string redondea al mostrar) pero la
    comparacion de umbrales en claude_pet.py usa el numero crudo, que
    todavia no llega a 57 -- el umbral no dispara cuando la pantalla ya
    dice que si."""
    if not w or w.get("used_percentage") is None:
        return w
    w = dict(w)
    w["used_percentage"] = int(round(w["used_percentage"]))
    return w


def bar(pct: float, width: int = 10) -> str:
    filled = int(pct * width / 100)
    return "█" * filled + "░" * (width - filled)


def render(data: dict) -> str:
    """La linea que ves en la terminal."""
    model = (data.get("model") or {}).get("display_name", "?")
    ctx = (data.get("context_window") or {}).get("used_percentage") or 0

    rate = data.get("rate_limits") or {}
    five = (rate.get("five_hour") or {}).get("used_percentage")
    seven = (rate.get("seven_day") or {}).get("used_percentage")

    parts = [f"[{model}]", f"ctx {bar(ctx)} {ctx:.0f}%"]
    if five is not None:
        parts.append(f"5h {five:.0f}%")
    if seven is not None:
        parts.append(f"7d {seven:.0f}%")
    if five is None and seven is None:
        parts.append("(sin rate_limits)")
    return " | ".join(parts)


def main() -> None:
    raw = sys.stdin.read()

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    if "--dump" in sys.argv:
        (PET_DIR / "last_raw.json").write_text(raw, encoding="utf-8")

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print("[pet] json invalido")
        return

    # Normalizado ACA, antes de render() y del payload: las dos lecturas
    # de rate_limits mas abajo tienen que ver el mismo numero redondeado.
    rl = data.get("rate_limits")
    if rl:
        rl["five_hour"] = normalize_window(rl.get("five_hour"))
        rl["seven_day"] = normalize_window(rl.get("seven_day"))

    session_id = data.get("session_id") or "unknown"
    rate = data.get("rate_limits") or {}

    # Nombre de archivo seguro (session_id es un uuid, pero por las dudas)
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64] or "unknown"
    path = SESSIONS_DIR / f"{safe}.json"

    # El statusLine se re-dibuja cada pocos segundos aunque la sesion este
    # ociosa, arrastrando el ultimo rate_limits conocido sin cambio alguno.
    # Entonces "ts" mide que el statusLine esta vivo, NO que el dato sea nuevo:
    # una sesion quieta re-sellaba un porcentaje viejo con hora actual y le
    # ganaba al poller, que traia el valor bueno. rl_ts marca cuando el VALOR
    # cambio de verdad, que es la frescura que hay que comparar.
    prev = {}
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    if (prev.get("rl_ts")
            and prev.get("five_hour") == rate.get("five_hour")
            and prev.get("seven_day") == rate.get("seven_day")):
        rl_ts = prev["rl_ts"]
    else:
        rl_ts = time.time()

    payload = {
        "ts": time.time(),
        "rl_ts": rl_ts,
        "session_id": session_id,
        "session_name": data.get("session_name"),
        "model": (data.get("model") or {}).get("display_name"),
        "cwd": (data.get("workspace") or {}).get("current_dir"),
        "cost_usd": (data.get("cost") or {}).get("total_cost_usd") or 0.0,
        "context_pct": (data.get("context_window") or {}).get("used_percentage"),
        "five_hour": rate.get("five_hour"),
        "seven_day": rate.get("seven_day"),
    }

    try:
        atomic_write(path, payload)
    except OSError:
        pass

    # Limpieza barata: 1 de cada ~20 corridas
    if int(time.time()) % 20 == 0:
        cleanup_stale()

    print(render(data))


if __name__ == "__main__":
    main()
