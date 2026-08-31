#!/usr/bin/env python3
"""
claude_pet_usage.py — poller del uso, independiente de la UI.

Por que existe: el statusLine solo corre en la TUI. Si usas el panel de la
extension de VS Code (que es un webview, no una terminal) nunca se ejecuta, y
la mascota se queda sin el % de las ventanas. Este poller consulta
`GET /api/oauth/usage` — el mismo endpoint que el panel usa para su seccion
"Usage" — asi que anda con cualquier UI, e incluso con Claude Code cerrado.

De paso arregla el congelamiento que anotamos: el statusLine reporta el ultimo
response cacheado, asi que una sesion ociosa deja el numero viejo clavado. El
poller siempre trae el valor actual.

Escribe ~/.claude/pet/usage.json de forma atomica. `read_sessions()` en
claude_pet.py lo prefiere para las ventanas cuando esta fresco, y sigue usando
sessions/*.json para el conteo de instancias, el contexto y el costo (datos
por sesion que el endpoint no conoce).

AVISO: el endpoint es interno y no esta documentado. Puede cambiar sin aviso.
Por eso todo fallo aca es no-fatal: la mascota sigue andando y el collector del
statusLine queda como fallback. No lo borres.
"""

import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PET_DIR = Path.home() / ".claude" / "pet"
USAGE_PATH = PET_DIR / "usage.json"
CREDS_PATH = Path.home() / ".claude" / ".credentials.json"

# En macOS el archivo de credenciales NO EXISTE: Claude Code guarda el mismo
# JSON en el Keychain del login, bajo este servicio. Verificado el 25/8/2026
# contra un Mac real (macOS 26.6.2) donde ~/.claude/ tiene 20 entradas y
# ninguna es .credentials.json. Ver _creds().
KEYCHAIN_SERVICE = "Claude Code-credentials"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# usage.json se considera vigente si se escribio hace menos que esto. Con un
# poll de 60s deja margen para un par de fallos seguidos antes de que la
# mascota vuelva a confiar en lo que reporten las sesiones.
USAGE_FRESH = 900

# El backoff jamas debe superar USAGE_FRESH: si lo hace, la mascota se queda sin
# dato valido y sin nadie que lo renueve (en el panel no hay statusLine que haga
# de fallback). Con 180s siempre entra un reintento antes de que el dato venza.
MAX_BACKOFF = 180

# Un 429 es distinto de un corte de red: el server nos esta pidiendo que
# paremos. A veces dice por cuanto (Retry-After: 300), pero no siempre: el 429
# de la rafaga lo traia y el que comio la mascota en produccion no. Cuando falta,
# el respaldo es la ventana medida completa. 120s era la vieja adivinanza y cae
# dentro de una ventana todavia saturada.
THROTTLE_BACKOFF = 300
MAX_THROTTLE_BACKOFF = 600

# Un Retry-After absurdo (negativo, cero, o de horas) no debe convertirse en una
# espera absurda: fuera de este rango preferimos nuestro propio backoff.
RETRY_AFTER_MAX = 3600

# Limite del endpoint, medido el 25/8/2026 en una hora sin nadie mas usando la
# maquina (36 intentos, 5 fallos, sin statusLine contaminando):
#
#   - todo intento con <= 6 requests en la ventana previa devuelve 200
#   - todo intento con 7 devuelve 429, sin una sola excepcion
#   - el ancho de esa ventana cae entre 422s y 652s. Cotas duras: un intento
#     EXITOSO tenia su 7a request previa a 652s (con una ventana mas ancha
#     habria contado 7 y tenia que fallar), y uno FALLIDO tenia 7 dentro de 422s
#
# O sea: 6 requests por ventana deslizante de ~7-11 min. El modelo anterior
# ("token bucket de 5 reponiendo 1 cada 60s") era una lectura ingenua de una
# rafaga contaminada: cuando medimos "5 pasan y la 6a falla", la mascota acababa
# de pollear y esa era la sexta.
#
# Para que nunca entren 7 polls PROPIOS en una ventana hace falta
# P > W/6 = 652/6 = 108.7s. A 70s entran 10: el ciclo medido fue 6 exitos, un
# 429 y 300s de espera, repitiendose cada 722-723s con precision de reloj.
MIN_POLL_SECONDS = 110

# El piso deja entrar exactamente 6 polls propios, o sea el limite justo y cero
# margen: cualquier request ajena -- abrir el panel de Usage de VS Code, o un
# reinicio de la mascota, que dispara un poll inmediato -- suma la septima y da
# 429. A 140s entran 5 y queda un lugar libre.
#
# No se pierde informacion por subirlo: el endpoint entrega 6 lecturas cada
# ~12 min pase lo que pase. A 70s se obtienen 31 por hora con 371s de ceguera
# maxima; a 140s, 26 por hora con 140s. Bajar el intervalo no trae mas datos,
# los reparte peor.
DEFAULT_POLL_SECONDS = 140

# Cada reinicio de la mascota dispara un poll inmediato (a proposito, para
# tener dato fresco al toque) -- pero en un reboot de Windows todo lo demas
# que habla con el mismo endpoint (VS Code, otras sesiones de Claude Code, el
# panel de Usage) tambien despierta en el mismo instante. Sin jitter, la
# mascota es sistematicamente una de las requests que revienta el budget de
# 6/ventana justo cuando el usuario la esta mirando recien arrancada -- visto
# en vivo el 30/8: 429 con Retry-After de 1h al boot, mascota sin dato por una
# hora entera. STARTUP_JITTER_MAX separa el primer poll del instante exacto
# del boot sin resignar el "dato fresco al reiniciar": sigue llegando dentro
# del primer medio minuto, no en el proximo ciclo de 140s.
STARTUP_JITTER_MAX = 30

STATE_PATH = PET_DIR / "poller_state.json"
LAST_ERROR = None       # motivo del ultimo fallo, para diagnostico
LAST_THROTTLED = False  # si el ultimo fallo fue un 429
LAST_RETRY_AFTER = None  # segundos que pidio el server, si los mando

# Ultimo dato traido con exito de la red, aunque no se haya podido persistir a
# disco. El fetch y la escritura son pasos separados: un filtro de archivos de
# antivirus (visto en produccion: avgMonFltProxy interceptando la escritura
# atomica de usage.json, ver CLAUDE.md) puede bloquear el segundo durante
# minutos u horas sin que el primero falle nunca. Sin este cache, esos
# bloqueos tiran usage.json fuera de USAGE_FRESH y la mascota se queda "SIN
# DATOS" pese a que el poller sigue trayendo numeros validos ciclo a ciclo.
LAST_USAGE = None


def _epoch(iso):
    """'2026-08-25T15:40:00.883877+00:00' -> epoch float.

    El endpoint devuelve ISO-8601 pero la mascota hace `resets_at - time.time()`,
    asi que si le pasaramos el string crudo reventaria con TypeError.
    """
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


def _window(w):
    """Normaliza una ventana al shape que ya consume la mascota."""
    if not isinstance(w, dict):
        return None
    util = w.get("utilization")
    if util is None:
        return None
    return {"used_percentage": int(round(util)), "resets_at": _epoch(w.get("resets_at"))}


def _creds() -> dict:
    """El JSON de credenciales, venga de donde venga segun la plataforma.

    En Windows y Linux vive en ~/.claude/.credentials.json. En macOS ese
    archivo NO EXISTE — Claude Code guarda el mismo JSON en el Keychain del
    login. Es el unico bloqueante real del port: sin esto _token() tira
    FileNotFoundError en cada poll, usage.json nunca se escribe, la mascota
    muestra "--" para siempre y NINGUN umbral llega a dispararse (ni el corte
    automatico). PORTING.md daba por sentado que la ruta era la misma en Mac;
    verificado el 25/8/2026, no lo es.

    Se chequea el archivo PRIMERO, no `sys.platform`: si algun dia Claude Code
    vuelve al archivo en Mac, esto sigue andando sin tocar nada.
    """
    if CREDS_PATH.exists():
        return json.loads(CREDS_PATH.read_text(encoding="utf-8"))

    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"keychain: {e}") from e
        if r.returncode != 0:
            # 44 = no existe esa entrada (no logueado con Claude Code);
            # 128 = el usuario cancelo el dialogo de autorizacion.
            raise RuntimeError(
                f"keychain ({KEYCHAIN_SERVICE}): "
                f"{r.stderr.strip() or f'exit {r.returncode}'}")
        return json.loads(r.stdout)

    raise FileNotFoundError(CREDS_PATH)


def _token():
    """Se re-lee en cada poll a proposito: Claude Code refresca el token solo,
    y si lo cachearamos nos quedariamos con uno vencido."""
    return _creds()["claudeAiOauth"]["accessToken"]


def fetch_usage(timeout=20) -> dict:
    """Consulta el endpoint y devuelve el dict ya normalizado. Propaga errores."""
    req = urllib.request.Request(
        USAGE_URL,
        headers={"Authorization": f"Bearer {_token()}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.load(r)

    out = {"ts": time.time(), "source": "oauth_usage"}
    for k in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"):
        w = _window(raw.get(k))
        if w:
            out[k] = w

    # severity la calcula el server; sirve para no depender solo de umbrales
    # propios si algun dia cambian los planes.
    sev = {}
    for lim in raw.get("limits") or []:
        if lim.get("kind"):
            sev[lim["kind"]] = lim.get("severity")
    if sev:
        out["severity"] = sev
    return out


def _atomic(path: Path, data: dict) -> None:
    """.tmp + os.replace, o el lector eventualmente ve un JSON a medio escribir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _save_atomic(data: dict) -> None:
    _atomic(USAGE_PATH, data)


def _save_state(ok: bool, error, fails: int, next_in: int) -> None:
    """Deja rastro de cada intento. Sin esto un poller caido es invisible:
    poll_once() se tragaba la excepcion y no quedaba ni log ni contador, asi
    que un dato viejo no se distinguia de uno recien traido."""
    try:
        _atomic(STATE_PATH, {
            "ts": time.time(),
            "ok": ok,
            "error": error,
            "consecutive_failures": fails,
            "next_retry_in": next_in,
        })
    except OSError:
        pass


def _retry_after(e) -> int:
    """Segundos que pide el server en un 429, o None si no los dice."""
    try:
        secs = int(e.headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None
    return secs if 0 < secs <= RETRY_AFTER_MAX else None


def throttle_wait(fails: int, backoff: int) -> int:
    """Cuanto esperar tras un 429.

    El server declara el numero y es honesto (medido: pide 300, recupera a los
    302.5s), asi que obedecerlo le gana a cualquier constante nuestra por los
    dos lados: THROTTLE_BACKOFF reintentaba a los 120s, antes de que el limite
    se soltara -- y reintentar temprano es lo que degrada el token y alarga el
    bloqueo. Solo adivinamos si el header no vino.
    """
    if LAST_RETRY_AFTER:
        return LAST_RETRY_AFTER
    return (THROTTLE_BACKOFF if fails == 1
            else min(backoff * 2, MAX_THROTTLE_BACKOFF))


def poll_interval(cfg: dict) -> int:
    """Intervalo efectivo, nunca por debajo de la tasa de reposicion."""
    return max(MIN_POLL_SECONDS,
               int(cfg.get("usage_poll_seconds", DEFAULT_POLL_SECONDS)))


def get_last_usage() -> dict | None:
    """El fetch mas reciente que salio bien, exista o no en disco.

    claude_pet.py corre el poller en un hilo del mismo proceso, asi que puede
    leer esto directo en vez de pasar siempre por USAGE_PATH -- eso es lo que
    lo saltea a AVG (o cualquier otra cosa que bloquee la escritura) sin tocar
    su configuracion."""
    return LAST_USAGE


def poll_once() -> bool:
    """True si pudo refrescar Y persistir en disco. El motivo del fallo queda
    en LAST_ERROR. LAST_USAGE se actualiza con el dato recien traido aunque la
    escritura a disco falle -- ver el comentario en su declaracion arriba."""
    global LAST_ERROR, LAST_THROTTLED, LAST_RETRY_AFTER, LAST_USAGE
    try:
        data = fetch_usage()
    except Exception as e:
        # Nunca romper la UI por esto: token vencido, red caida, schema
        # cambiado. La mascota se queda con el ultimo valor y reintenta.
        LAST_THROTTLED = (isinstance(e, urllib.error.HTTPError)
                          and e.code == 429)
        LAST_RETRY_AFTER = _retry_after(e) if LAST_THROTTLED else None
        LAST_ERROR = f"{type(e).__name__}: {e}"[:180]
        return False

    LAST_USAGE = data
    try:
        _save_atomic(data)
    except Exception as e:
        # El fetch ya salio bien (LAST_USAGE quedo actualizado arriba); esto
        # es solo la persistencia a disco fallando -- nunca un 429.
        LAST_THROTTLED = False
        LAST_RETRY_AFTER = None
        LAST_ERROR = f"{type(e).__name__}: {e}"[:180]
        return False

    LAST_ERROR = None
    LAST_THROTTLED = False
    LAST_RETRY_AFTER = None
    return True


def startup_jitter() -> float:
    """Segundos a esperar antes del primer poll del proceso. Ver
    STARTUP_JITTER_MAX arriba -- separada en su propia funcion para poder
    fijarla en los tests sin parchear random.uniform a mano."""
    return random.uniform(0, STARTUP_JITTER_MAX)


def _loop(interval: int) -> None:
    time.sleep(startup_jitter())
    backoff = interval
    fails = 0
    while True:
        ok = poll_once()
        if ok:
            backoff = interval
            fails = 0
        elif LAST_THROTTLED:
            # el 429 manda por encima de nuestra preferencia por dato fresco.
            fails += 1
            backoff = throttle_wait(fails, backoff)
        else:
            # fallo transitorio (red, DNS, timeout): el backoff crece pero
            # nunca supera MAX_BACKOFF, por debajo de USAGE_FRESH, asi que
            # siempre entra un reintento antes de que el dato venza.
            fails += 1
            backoff = min(backoff * 2, MAX_BACKOFF)
        _save_state(ok, LAST_ERROR, fails, backoff)
        # OJO: el sleep va DESPUES del trabajo, y eso es load-bearing. El
        # periodo real queda en interval + latencia (~60.45s medido, no 60.000),
        # asi que nunca pedimos exactamente a la tasa de reposicion del bucket.
        # Un refactor a scheduler por deadline (sleep(proximo - ahora)) borraria
        # ese margen sin que se note hasta el primer 429.
        time.sleep(backoff)


def start_poller(cfg: dict) -> None:
    """Arranca el poller en un hilo daemon. No-op si esta deshabilitado."""
    if not cfg.get("usage_poller_enabled", True):
        return
    # Los issues 30930, 31021 y 31637 de anthropics/claude-code afirman que
    # 30/60/120/240/300s fallan todos. Medido, eso es falso para 60s: 12 ciclos
    # seguidos, 0 fallos. Lo que si es cierto es que el bucket es chico (5) y
    # que pasarse deja 5 minutos de bloqueo. Ver MIN_POLL_SECONDS.
    interval = poll_interval(cfg)
    threading.Thread(target=_loop, args=(interval,), daemon=True).start()


if __name__ == "__main__":
    # Modo debug: una consulta y a stdout.
    print(json.dumps(fetch_usage(), indent=2))
