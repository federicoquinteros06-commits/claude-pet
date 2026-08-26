"""claude_pet_slack: eleccion de transporte, resolucion de target y errores.

Nada de esto toca la red: `_api` se parchea por test. Lo que se verifica es la
logica que rodea al POST, que es donde estan los modos de fallo reales — mandar
un `<!here>` a un DM (no menciona a nadie), no darse cuenta de que Slack
respondio `{"ok": false}` con HTTP 200, o repetir la resolucion del target en
cada alerta.
"""

import pytest

import claude_pet_slack as slack


@pytest.fixture(autouse=True)
def limpiar_cache():
    slack.reset_cache()
    yield
    slack.reset_cache()


@pytest.fixture
def api(monkeypatch):
    """Reemplaza _api por un doble que registra llamadas y responde por metodo."""
    llamadas = []
    respuestas = {}

    def fake(token, method, payload, form=False):
        llamadas.append({"token": token, "method": method,
                         "payload": payload, "form": form})
        r = respuestas.get(method, {"ok": True})
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(slack, "_api", fake)
    fake.llamadas = llamadas
    fake.respuestas = respuestas
    return fake


BOT = {"slack_bot_token": "xoxb-123456789012", "slack_target": "U0123ABCD"}


# ------------------------------------------------------- eleccion de transporte

def test_bot_gana_al_webhook(api, monkeypatch):
    """Si estan los dos configurados manda el bot: el webhook no puede DM."""
    llamado = []
    monkeypatch.setattr(slack, "_post_webhook",
                        lambda url, text: llamado.append(url))
    api.respuestas["conversations.open"] = {"ok": True, "channel": {"id": "D999"}}

    cfg = dict(BOT, slack_webhook_url="https://hooks.slack.com/services/x")
    slack.send_sync(cfg, "hola")

    assert llamado == []
    assert api.llamadas[-1]["method"] == "chat.postMessage"


def test_sin_bot_cae_al_webhook(monkeypatch):
    visto = {}
    monkeypatch.setattr(slack, "_post_webhook",
                        lambda url, text: visto.update(url=url, text=text))

    slack.send_sync({"slack_webhook_url": "https://hooks.slack.com/services/x"},
                    "hola", "<!here>")

    assert visto["url"].endswith("/x")
    assert visto["text"] == "<!here> hola"


def test_sin_nada_configurado_tira_error():
    with pytest.raises(slack.SlackError, match="no esta configurado"):
        slack.send_sync({}, "hola")


@pytest.mark.parametrize("cfg,esperado", [
    ({}, False),
    ({"slack_bot_token": "xoxb-1"}, False),              # token sin destino
    ({"slack_target": "U1"}, False),                     # destino sin token
    ({"slack_bot_token": "xoxb-1", "slack_target": "U1"}, True),
    ({"slack_webhook_url": "https://x"}, True),
])
def test_is_configured(cfg, esperado):
    assert slack.is_configured(cfg) is esperado


# ------------------------------------------------------------ mencion en DM

def test_dm_no_lleva_mencion(api):
    """`<!here>` en un DM no menciona a nadie: el push llega igual y el texto
    queda sucio."""
    api.respuestas["conversations.open"] = {"ok": True, "channel": {"id": "D999"}}

    slack.send_sync(BOT, "uso 96%", "<!here>")

    post = api.llamadas[-1]["payload"]
    assert post["channel"] == "D999"
    assert post["text"] == "uso 96%"


def test_canal_si_lleva_mencion(api):
    slack.send_sync(dict(BOT, slack_target="C0123ABCD"), "uso 96%", "<!here>")

    assert api.llamadas[-1]["payload"]["text"] == "<!here> uso 96%"


def test_no_despliega_previews(api):
    slack.send_sync(dict(BOT, slack_target="C0123ABCD"), "mira http://x")

    post = api.llamadas[-1]["payload"]
    assert post["unfurl_links"] is False and post["unfurl_media"] is False


# ----------------------------------------------------------------- _resolve

def test_member_id_abre_dm(api):
    api.respuestas["conversations.open"] = {"ok": True, "channel": {"id": "D999"}}

    assert slack._resolve("xoxb-123456789012", "U0123ABCD") == ("D999", True)
    assert api.llamadas[0]["payload"] == {"users": "U0123ABCD"}


def test_email_busca_el_id_y_abre_dm(api):
    api.respuestas["users.lookupByEmail"] = {"ok": True, "user": {"id": "U777"}}
    api.respuestas["conversations.open"] = {"ok": True, "channel": {"id": "D777"}}

    assert slack._resolve("xoxb-123456789012", "fede@empresa.com") == ("D777", True)
    lookup = api.llamadas[0]
    assert lookup["payload"] == {"email": "fede@empresa.com"}
    # users.lookupByEmail rechaza application/json con invalid_arguments
    assert lookup["form"] is True


@pytest.mark.parametrize("target,esperado", [
    ("C0123ABCD", ("C0123ABCD", False)),
    ("G0123ABCD", ("G0123ABCD", False)),
    ("#general", ("#general", False)),
    ("D0123ABCD", ("D0123ABCD", True)),     # un D... ya ES un DM
])
def test_ids_de_canal_pasan_derecho(api, target, esperado):
    assert slack._resolve("xoxb-123456789012", target) == esperado
    assert api.llamadas == []               # no hace falta resolver nada


def test_arroba_nombre_explica_que_hacer(api):
    with pytest.raises(slack.SlackError, match="member ID"):
        slack._resolve("xoxb-123456789012", "@fede")


def test_target_vacio_explica_que_falta(api):
    with pytest.raises(slack.SlackError, match="slack_target"):
        slack._resolve("xoxb-123456789012", "   ")


def test_resolve_cachea(api):
    api.respuestas["conversations.open"] = {"ok": True, "channel": {"id": "D999"}}

    for _ in range(5):
        slack._resolve("xoxb-123456789012", "U0123ABCD")

    assert len(api.llamadas) == 1


def test_sin_im_write_manda_al_user_id_igual(api):
    """Una app mal scopeada no deberia dejarte sin aviso: chat.postMessage
    tambien acepta un user ID como channel."""
    api.respuestas["conversations.open"] = slack.SlackError(
        "conversations.open: missing_scope - al bot le falta el scope 'im:write'")

    assert slack._resolve("xoxb-123456789012", "U0123ABCD") == ("U0123ABCD", True)


def test_otros_errores_de_open_no_se_tragan(api):
    api.respuestas["conversations.open"] = slack.SlackError(
        "conversations.open: invalid_auth - el token no es valido")

    with pytest.raises(slack.SlackError, match="invalid_auth"):
        slack._resolve("xoxb-123456789012", "U0123ABCD")


# ---------------------------------------------------------- errores de la API

class FakeResp:
    def __init__(self, payload):
        self._b = payload.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ok_false_con_http_200_es_error(monkeypatch):
    """El modo de fallo clasico de Slack: status 200 y {"ok": false} adentro."""
    monkeypatch.setattr(slack.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp('{"ok":false,"error":"not_in_channel"}'))

    with pytest.raises(slack.SlackError, match="/invite"):
        slack._api("xoxb-1", "chat.postMessage", {})


def test_missing_scope_dice_cual_falta(monkeypatch):
    monkeypatch.setattr(
        slack.urllib.request, "urlopen",
        lambda req, timeout=None: FakeResp(
            '{"ok":false,"error":"missing_scope","needed":"im:write","provided":"chat:write"}'))

    with pytest.raises(slack.SlackError, match="im:write"):
        slack._api("xoxb-1", "conversations.open", {})


def test_reintenta_y_termina_bien(monkeypatch):
    intentos = []

    def urlopen(req, timeout=None):
        intentos.append(1)
        if len(intentos) == 1:
            raise slack.urllib.error.URLError("conexion caida")
        return FakeResp('{"ok":true}')

    monkeypatch.setattr(slack.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)

    assert slack._api("xoxb-1", "chat.postMessage", {})["ok"] is True
    assert len(intentos) == 2


def test_deja_de_reintentar(monkeypatch):
    intentos = []

    def urlopen(req, timeout=None):
        intentos.append(1)
        raise slack.urllib.error.URLError("conexion caida")

    monkeypatch.setattr(slack.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)

    with pytest.raises(slack.SlackError, match="no pude hablar con Slack"):
        slack._api("xoxb-1", "chat.postMessage", {})
    assert len(intentos) == slack.MAX_RETRIES + 1


def test_error_de_auth_no_se_reintenta(monkeypatch):
    intentos = []

    def urlopen(req, timeout=None):
        intentos.append(1)
        return FakeResp('{"ok":false,"error":"invalid_auth"}')

    monkeypatch.setattr(slack.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)

    with pytest.raises(slack.SlackError):
        slack._api("xoxb-1", "chat.postMessage", {})
    assert len(intentos) == 1


@pytest.mark.parametrize("header,esperado", [
    ({"Retry-After": "5"}, 5.0),
    ({"Retry-After": "0"}, 9.0),        # absurdo -> nuestro backoff
    ({"Retry-After": "-3"}, 9.0),
    ({"Retry-After": "99999"}, 9.0),    # una hora de espera no sirve
    ({"Retry-After": "no"}, 9.0),
    ({}, 9.0),
])
def test_retry_after_solo_si_es_razonable(header, esperado):
    assert slack._retry_after(header, 9.0) == esperado


# ---------------------------------------------------------------------- send()

def test_send_no_propaga_excepciones(monkeypatch):
    """El hilo sale del tick de la mascota: si revienta, se lleva la alerta."""
    monkeypatch.setattr(slack, "send_sync",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    visto = []

    slack.send({"slack_webhook_url": "x"}, "hola", done=lambda ok, d: visto.append((ok, d)))
    for _ in range(200):
        if visto:
            break
        slack.time.sleep(0.01)

    assert visto and visto[0][0] is False
    assert "boom" in visto[0][1]
