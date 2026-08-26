"""claude_pet_usage: normalizacion del endpoint /api/oauth/usage.

El endpoint devuelve utilization float y resets_at ISO-8601; la mascota espera
used_percentage int y resets_at epoch, porque hace `resets_at - time.time()`.
Sin esta traduccion revienta con TypeError.
"""

import json
from datetime import datetime

import pytest

import claude_pet_usage as usage

ISO = "2026-08-25T15:40:00.883877+00:00"
EPOCH = datetime.fromisoformat(ISO).timestamp()


# ---------------------------------------------------------------- _epoch

def test_epoch_convierte_iso8601():
    assert usage._epoch(ISO) == EPOCH


@pytest.mark.parametrize("malo", [None, "", "no es una fecha", 1787672400, {}])
def test_epoch_devuelve_none_en_vez_de_tirar(malo):
    assert usage._epoch(malo) is None


# --------------------------------------------------------------- _window

def test_window_normaliza_al_shape_de_la_mascota():
    w = usage._window({"utilization": 40.2, "resets_at": ISO})
    assert w == {"used_percentage": 40, "resets_at": EPOCH}
    assert isinstance(w["used_percentage"], int)


@pytest.mark.parametrize("util,esperado", [(40.2, 40), (40.6, 41), (0.0, 0), (100.0, 100)])
def test_window_redondea(util, esperado):
    assert usage._window({"utilization": util, "resets_at": ISO})["used_percentage"] == esperado


@pytest.mark.parametrize("malo", [None, "five_hour", 42, {}, {"resets_at": ISO}])
def test_window_devuelve_none_sin_utilization(malo):
    assert usage._window(malo) is None


def test_window_tolera_resets_at_ausente():
    """Mejor una ventana con resets_at None que perder el porcentaje entero."""
    assert usage._window({"utilization": 51.0}) == {"used_percentage": 51, "resets_at": None}


# ----------------------------------------------------------- fetch_usage

class _FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, *a):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    """Mockea el HTTP y el token. Ni red ni credencial real."""
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "test-token"}}),
                     encoding="utf-8")
    monkeypatch.setattr(usage, "CREDS_PATH", creds)

    capturado = {}

    def _responder(payload):
        def _urlopen(req, timeout=None):
            capturado["req"] = req
            return _FakeResponse(payload)
        monkeypatch.setattr(usage.urllib.request, "urlopen", _urlopen)
        return capturado

    return _responder


def test_fetch_usage_normaliza_las_ventanas(endpoint):
    endpoint({
        "five_hour": {"utilization": 40.2, "resets_at": ISO},
        "seven_day": {"utilization": 52.0, "resets_at": ISO},
        "limits": [{"kind": "five_hour", "severity": "normal"},
                   {"kind": "seven_day", "severity": "warning"}],
    })

    out = usage.fetch_usage()
    assert out["source"] == "oauth_usage"
    assert out["five_hour"] == {"used_percentage": 40, "resets_at": EPOCH}
    assert out["seven_day"] == {"used_percentage": 52, "resets_at": EPOCH}
    assert out["severity"] == {"five_hour": "normal", "seven_day": "warning"}
    assert isinstance(out["ts"], float)


def test_fetch_usage_omite_ventanas_ausentes(endpoint):
    endpoint({"five_hour": {"utilization": 40.0, "resets_at": ISO}})

    out = usage.fetch_usage()
    assert "five_hour" in out
    assert "seven_day" not in out
    assert "severity" not in out


def test_fetch_usage_manda_el_bearer(endpoint):
    capturado = endpoint({"five_hour": {"utilization": 1.0, "resets_at": ISO}})
    usage.fetch_usage()

    req = capturado["req"]
    assert req.full_url == usage.USAGE_URL
    assert req.get_header("Authorization") == "Bearer test-token"


def test_poll_once_no_propaga_el_fallo(endpoint, tmp_path, monkeypatch):
    """Token vencido, red caida o schema cambiado: la mascota se queda con el
    ultimo valor y reintenta, nunca rompe la UI."""
    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage.json")
    endpoint({})

    def _explota(req, timeout=None):
        raise OSError("red caida")
    monkeypatch.setattr(usage.urllib.request, "urlopen", _explota)

    assert usage.poll_once() is False
    assert "OSError" in usage.LAST_ERROR
    assert not (tmp_path / "usage.json").exists()


def test_poll_once_escribe_atomico(endpoint, tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage.json")
    endpoint({"five_hour": {"utilization": 40.0, "resets_at": ISO}})

    assert usage.poll_once() is True
    assert usage.LAST_ERROR is None
    escrito = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert escrito["five_hour"]["used_percentage"] == 40
    assert not list(tmp_path.glob("*.tmp"))   # el tmp se renombro, no quedo basura


# ------------------------------------------------------- credenciales
#
# En macOS ~/.claude/.credentials.json NO EXISTE: el token vive en el Keychain.
# Es el unico bloqueante real del port — sin esto _token() tira
# FileNotFoundError en cada poll y la mascota nunca recibe un dato.

CREDS_JSON = '{"claudeAiOauth": {"accessToken": "sk-ant-oat-XXX"}}'


class FakeSecurity:
    def __init__(self, stdout=CREDS_JSON, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_mac_lee_el_token_del_keychain(tmp_path, monkeypatch):
    visto = {}

    def fake_run(cmd, **kwargs):
        visto["cmd"] = cmd
        return FakeSecurity()

    monkeypatch.setattr(usage, "CREDS_PATH", tmp_path / "no-existe.json")
    monkeypatch.setattr(usage.sys, "platform", "darwin")
    monkeypatch.setattr(usage.subprocess, "run", fake_run)

    assert usage._token() == "sk-ant-oat-XXX"
    assert visto["cmd"][:2] == ["security", "find-generic-password"]
    assert usage.KEYCHAIN_SERVICE in visto["cmd"]
    assert "-w" in visto["cmd"]  # sin -w devuelve metadata, no el secreto


def test_el_archivo_gana_si_existe(tmp_path, monkeypatch):
    """Se chequea el archivo primero, no sys.platform: si Claude Code volviera
    al archivo en Mac, esto sigue andando sin tocar codigo."""
    creds = tmp_path / ".credentials.json"
    creds.write_text('{"claudeAiOauth": {"accessToken": "del-archivo"}}',
                     encoding="utf-8")

    llamado = []
    monkeypatch.setattr(usage, "CREDS_PATH", creds)
    monkeypatch.setattr(usage.sys, "platform", "darwin")
    monkeypatch.setattr(usage.subprocess, "run",
                        lambda *a, **k: llamado.append(1))

    assert usage._token() == "del-archivo"
    assert llamado == []  # no se molesto al Keychain


def test_keychain_sin_entrada_explica_el_motivo(tmp_path, monkeypatch):
    """El poller se traga las excepciones pero deja LAST_ERROR: el mensaje
    tiene que servir para diagnosticar, no ser un exit code pelado."""
    monkeypatch.setattr(usage, "CREDS_PATH", tmp_path / "no-existe.json")
    monkeypatch.setattr(usage.sys, "platform", "darwin")
    monkeypatch.setattr(
        usage.subprocess, "run",
        lambda *a, **k: FakeSecurity(
            "", returncode=44, stderr="SecKeychainSearchCopyNext: no encontrado"))

    with pytest.raises(RuntimeError, match="keychain"):
        usage._token()


def test_keychain_cancelado_no_cuelga(tmp_path, monkeypatch):
    """Si el usuario cancela el dialogo o `security` no esta, es un error
    normal del poll, no un crash de la mascota."""
    monkeypatch.setattr(usage, "CREDS_PATH", tmp_path / "no-existe.json")
    monkeypatch.setattr(usage.sys, "platform", "darwin")
    monkeypatch.setattr(
        usage.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(RuntimeError):
        usage._token()


def test_windows_sin_archivo_sigue_siendo_filenotfound(tmp_path, monkeypatch):
    """La rama del Keychain es solo de darwin: en Windows el error tiene que
    seguir siendo el de siempre."""
    monkeypatch.setattr(usage, "CREDS_PATH", tmp_path / "no-existe.json")
    monkeypatch.setattr(usage.sys, "platform", "win32")

    with pytest.raises(FileNotFoundError):
        usage._token()
