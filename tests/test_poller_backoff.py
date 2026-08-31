"""Backoff del poller tras un 429.

Medido contra el endpoint real el 25/8/2026, en una hora sin nadie mas usando
la maquina (36 intentos, 5 fallos, sin contaminacion de statusLine):

  - todo intento con <= 6 requests en la ventana previa devuelve 200
  - todo intento con 7 devuelve 429, sin una sola excepcion
  - el ancho de esa ventana esta entre 422s y 652s: un intento EXITOSO tenia su
    7a request previa a 652s (si la ventana fuera mas ancha habria fallado), y
    uno FALLIDO tenia 7 dentro de 422s
  - a 70s el ciclo se repitio 4 veces con precision de reloj: 6 exitos, un 429,
    300s de espera, otra vez -- cada 722-723 segundos

Modelo: **6 requests por ventana deslizante de ~7-11 min.** Reemplaza al de
"token bucket de 5 reponiendo 1 cada 60s", que era una lectura ingenua de una
rafaga contaminada: cuando medimos "5 pasan y la 6a falla", la mascota acababa
de hacer un poll y esa era la sexta.

Consecuencia contraintuitiva: a 70s NO se obtienen mas datos que a 140s. El
endpoint entrega 6 cada ~12 min y punto; bajar el intervalo solo reparte esos 6
en rafagas con 300s de ceguera detras.
"""

import email.message
import json
import urllib.error

import pytest

import claude_pet_usage as usage


@pytest.fixture
def creds(tmp_path, monkeypatch):
    p = tmp_path / ".credentials.json"
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": "t"}}), encoding="utf-8")
    monkeypatch.setattr(usage, "CREDS_PATH", p)
    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage.json")
    return p


def _http_error(code, headers=None):
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("http://x", code, "err", h, None)


def _falla_con(monkeypatch, exc):
    def _urlopen(req, timeout=None):
        raise exc
    monkeypatch.setattr(usage.urllib.request, "urlopen", _urlopen)


# ------------------------------------------------- captura del Retry-After

def test_429_captura_el_retry_after(creds, monkeypatch):
    _falla_con(monkeypatch, _http_error(429, {"Retry-After": "300"}))

    assert usage.poll_once() is False
    assert usage.LAST_THROTTLED is True
    assert usage.LAST_RETRY_AFTER == 300


def test_429_sin_retry_after(creds, monkeypatch):
    _falla_con(monkeypatch, _http_error(429))

    assert usage.poll_once() is False
    assert usage.LAST_THROTTLED is True
    assert usage.LAST_RETRY_AFTER is None


@pytest.mark.parametrize("valor", ["", "manana", "-5", "0", "999999"])
def test_429_con_retry_after_invalido(creds, monkeypatch, valor):
    """Un header roto o absurdo no debe convertirse en una espera absurda."""
    _falla_con(monkeypatch, _http_error(429, {"Retry-After": valor}))

    usage.poll_once()
    assert usage.LAST_RETRY_AFTER is None


def test_error_de_red_no_es_throttle(creds, monkeypatch):
    _falla_con(monkeypatch, OSError("red caida"))

    assert usage.poll_once() is False
    assert usage.LAST_THROTTLED is False
    assert usage.LAST_RETRY_AFTER is None


def test_exito_limpia_el_estado(creds, monkeypatch):
    _falla_con(monkeypatch, _http_error(429, {"Retry-After": "300"}))
    usage.poll_once()
    assert usage.LAST_RETRY_AFTER == 300

    class _R:
        def read(self, *a):
            return json.dumps({"five_hour": {"utilization": 1.0,
                                             "resets_at": "2026-08-25T20:40:00+00:00"}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda r, timeout=None: _R())

    assert usage.poll_once() is True
    assert usage.LAST_THROTTLED is False
    assert usage.LAST_RETRY_AFTER is None


def test_falla_al_escribir_no_pierde_el_dato_en_memoria(creds, monkeypatch):
    """El fetch de red y la escritura a disco son pasos separados: un
    PermissionError al escribir (AVG interceptando la escritura atomica,
    visto en produccion, ver CLAUDE.md) no debe tirar el dato recien traido.
    LAST_USAGE tiene que quedar actualizado igual -- es lo que claude_pet.py
    usa como fallback cuando usage.json no se pudo persistir."""
    class _R:
        def read(self, *a):
            return json.dumps({"five_hour": {"utilization": 42.0,
                                             "resets_at": "2026-08-25T20:40:00+00:00"}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda r, timeout=None: _R())
    monkeypatch.setattr(usage, "_save_atomic",
                         lambda data: (_ for _ in ()).throw(PermissionError("avgMonFltProxy")))

    assert usage.poll_once() is False
    assert usage.get_last_usage()["five_hour"]["used_percentage"] == 42
    assert "PermissionError" in usage.LAST_ERROR


# --------------------------------------------------------- espera tras 429

def test_obedece_el_retry_after_del_server(monkeypatch):
    """300 medido, contra los 120 que adivinaba THROTTLE_BACKOFF."""
    monkeypatch.setattr(usage, "LAST_RETRY_AFTER", 300)
    assert usage.throttle_wait(fails=1, backoff=60) == 300
    assert usage.throttle_wait(fails=5, backoff=600) == 300


def test_fallback_espera_la_ventana_completa():
    """No todos los 429 traen Retry-After (medido: el de la rafaga si, el que
    comio la mascota en produccion no). Cuando falta, el respaldo tiene que ser
    la ventana medida -- 300s -- y no menos: reintentar a los 120s cae dentro de
    una ventana todavia saturada, gasta otra request y, segun los issues 30930 y
    31637, es lo que deja el token en estado degradado y alarga el bloqueo.

    300 salio del unico 429 que trajo Retry-After, y la hora de monitoreo lo
    valido como fallback: los 5 429 esperaron 300s y los 5 recuperaron al primer
    intento, con 6 polls buenos detras. Ninguno de esos 5 traia el header."""
    assert usage.THROTTLE_BACKOFF == 300


def test_sin_retry_after_escala_como_antes(monkeypatch):
    monkeypatch.setattr(usage, "LAST_RETRY_AFTER", None)
    assert usage.throttle_wait(fails=1, backoff=60) == usage.THROTTLE_BACKOFF
    assert usage.throttle_wait(fails=2, backoff=120) == 240
    assert usage.throttle_wait(fails=9, backoff=600) == usage.MAX_THROTTLE_BACKOFF


# ------------------------------------------------------- piso del intervalo

def test_piso_impide_autogenerar_429():
    """Para que nunca entren 7 polls propios en una ventana hace falta
    P > W/6 = 652/6 = 108.7s. Por debajo de eso el 429 es aritmetica, no mala
    suerte: a 70s entran 10 en la ventana y falla cada 12 minutos, medido."""
    assert usage.MIN_POLL_SECONDS == 110
    assert usage.poll_interval({"usage_poll_seconds": 70}) == 110
    assert usage.poll_interval({"usage_poll_seconds": 1}) == 110


def test_respeta_intervalos_mas_largos():
    assert usage.poll_interval({"usage_poll_seconds": 300}) == 300


def test_default_deja_margen_de_recuperacion():
    """El piso y el default son cosas distintas y no deben confundirse.

    MIN_POLL_SECONDS = 110 es el piso aritmetico: por debajo, el poller solo
    ya se autogenera el 429. Pero ahi entran 6 polls propios en la ventana, o
    sea el limite exacto y CERO margen: cualquier request ajena -- abrir el
    panel de Usage de VS Code, o un reinicio de la mascota, que dispara un poll
    inmediato -- suma la septima.

    DEFAULT_POLL_SECONDS = 140 es el primer valor que deja un lugar libre
    (entran 5, el limite es 6). Cuesta 26 polls por hora en vez de 30, y a
    cambio el 429 deja de depender de si abriste un panel.
    """
    assert usage.MIN_POLL_SECONDS == 110
    assert usage.DEFAULT_POLL_SECONDS == 140
    assert usage.poll_interval({}) == 140


# --------------------------------------- jitter de arranque (colision al boot)

def test_startup_jitter_esta_acotado():
    """random.uniform es inclusive en el limite inferior; no debe pasarse del
    superior. 200 muestras para que un off-by-one en el rango no pase de
    casualidad."""
    for _ in range(200):
        j = usage.startup_jitter()
        assert 0 <= j <= usage.STARTUP_JITTER_MAX


def test_loop_espera_el_jitter_antes_del_primer_poll(monkeypatch):
    """El jitter tiene que ser lo primero que hace _loop -- antes de tocar la
    red siquiera -- para separar el proceso del instante exacto del boot."""
    monkeypatch.setattr(usage, "startup_jitter", lambda: 7.5)
    monkeypatch.setattr(usage, "poll_once", lambda: True)

    sleeps = []

    def _sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 2:
            raise SystemExit  # corta el "while True" tras un ciclo completo

    monkeypatch.setattr(usage.time, "sleep", _sleep)

    with pytest.raises(SystemExit):
        usage._loop(140)

    assert sleeps[0] == 7.5
