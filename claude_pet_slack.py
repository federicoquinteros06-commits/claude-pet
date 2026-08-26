#!/usr/bin/env python3
"""
claude_pet_slack.py — envio de alertas a Slack, por bot o por webhook.

Por que un bot y no el Incoming Webhook: el webhook solo puede postear en el
canal que elegiste al crearlo. Si lo que queres es que te LLEGUE la
notificacion al celular, el mensaje tiene que caer en tu DM, y para eso hace
falta un bot token (`xoxb-...`) llamando a `chat.postMessage`. Slack manda push
de todos los DM sin depender de como tengas configuradas las menciones.

Los dos transportes conviven. Prioridad:
    1. `slack_bot_token` + `slack_target`  -> Web API (DM o canal)
    2. `slack_webhook_url`                 -> Incoming Webhook (legacy)

Todo el modulo es sincronico y sin dependencias: quien llama decide si corre en
un hilo (`send()`) o si espera el resultado (`send_sync()`, para el "Probar
Slack" del tray, que necesita poder mostrar el error).

Scopes que necesita el bot:
    chat:write        obligatorio, es el que postea
    im:write          solo para DM, abre la conversacion con vos
    users:read.email  solo si en `slack_target` va un email en vez del member ID
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://slack.com/api/"
TIMEOUT = 8

# Un fallo de red no deberia perder la alerta, pero tampoco puede colgar el hilo
# eternamente. Ojo: el dedupe de AlertEngine ya marco el umbral como disparado
# cuando llegamos aca, asi que si esto falla del todo no hay segunda chance.
MAX_RETRIES = 2
RETRY_AFTER_MAX = 30

# Traduccion de los errores de Slack a algo accionable. La Web API responde
# HTTP 200 con {"ok": false, "error": "..."}: mirar solo el status no ve nada.
ERROR_HINTS = {
    "invalid_auth": "el token no es valido (si cambiaste scopes hay que reinstalar la app)",
    "not_authed": "falta el token",
    "token_revoked": "el token fue revocado, reinstala la app en el workspace",
    "account_inactive": "el bot esta desactivado en el workspace",
    "missing_scope": "al bot le faltan scopes",
    "not_in_channel": "el bot no esta en ese canal: invitalo con /invite @tu-bot",
    "channel_not_found": "no encuentro ese canal/usuario (usa el ID, no el nombre)",
    "is_archived": "el canal esta archivado",
    "users_not_found": "ese email no es de nadie del workspace",
    "cannot_dm_bot": "no se puede mandar DM a otro bot",
    "no_permission": "el bot no tiene permiso sobre esa conversacion",
    "ratelimited": "Slack nos esta limitando",
    "msg_too_long": "el mensaje es demasiado largo",
}

# Resolver el target (email -> user id -> canal de DM) cuesta 1-2 llamadas y no
# cambia nunca. La mascota es un proceso largo: alcanza con cachear en memoria,
# sin archivo que quede desincronizado si cambias de workspace.
_TARGET_CACHE = {}
_CACHE_LOCK = threading.Lock()


class SlackError(Exception):
    """Fallo ya traducido, listo para mostrarle al usuario."""


# ------------------------------------------------------------------ transporte

def _api(token, method, payload, form=False):
    """Llama un metodo de la Web API. Devuelve el JSON si ok, o tira SlackError.

    `form=True` para los metodos que no aceptan JSON: users.lookupByEmail es
    uno, con application/json responde invalid_arguments.
    """
    if form:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        ctype = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(payload).encode("utf-8")
        ctype = "application/json; charset=utf-8"

    delay = 1.0
    for intento in range(MAX_RETRIES + 1):
        req = urllib.request.Request(
            API_BASE + method,
            data=data,
            headers={"Content-Type": ctype, "Authorization": "Bearer " + token},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 429 es el unico caso donde Slack dice cuanto esperar (Retry-After).
            # 5xx es problema de ellos: tambien vale reintentar.
            if e.code == 429 and intento < MAX_RETRIES:
                time.sleep(_retry_after(e.headers, delay))
                delay *= 2
                continue
            if 500 <= e.code < 600 and intento < MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
                continue
            raise SlackError("HTTP {} en {}".format(e.code, method))
        except (urllib.error.URLError, OSError, ValueError) as e:
            if intento < MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
                continue
            raise SlackError("no pude hablar con Slack: {}".format(e))

        if body.get("ok"):
            return body

        err = body.get("error", "error desconocido")
        if err == "ratelimited" and intento < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
            continue
        raise SlackError(_explain(method, err, body))

    raise SlackError("{}: sin respuesta tras {} intentos".format(method, MAX_RETRIES + 1))


def _retry_after(headers, fallback):
    try:
        secs = float(headers.get("Retry-After", ""))
    except (TypeError, ValueError, AttributeError):
        return fallback
    # Un Retry-After absurdo no debe convertirse en una espera absurda.
    if secs <= 0 or secs > RETRY_AFTER_MAX:
        return fallback
    return secs


def _explain(method, err, body):
    hint = ERROR_HINTS.get(err)
    if err == "missing_scope":
        hint = "al bot le falta el scope '{}' (tiene: {})".format(
            body.get("needed", "?"), body.get("provided", "?"))
    return "{}: {}{}".format(method, err, " - " + hint if hint else "")


def _post_webhook(url, text):
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except Exception as e:
        raise SlackError("el webhook fallo: {}".format(e))


# --------------------------------------------------------------------- target

def _resolve(token, target):
    """`target` -> (channel_id, es_dm). Cachea porque no cambia.

    Acepta cuatro formas, de mas a menos recomendada:
      U0123ABC / W0123ABC  tu member ID  -> abre el DM
      vos@empresa.com      tu email      -> busca el member ID y abre el DM
      C0123ABC / D0123ABC  ID de canal   -> se usa tal cual
      #general             nombre        -> se manda tal cual (deprecado por Slack)
    """
    target = (target or "").strip()
    if not target:
        raise SlackError("falta `slack_target`: tu member ID de Slack, tu email "
                         "o el ID del canal")

    key = (token[-12:], target)
    with _CACHE_LOCK:
        hit = _TARGET_CACHE.get(key)
    if hit:
        return hit

    if target.startswith("@"):
        raise SlackError(
            "no puedo resolver '{}': Slack no busca por @nombre. Usa tu member "
            "ID (tu perfil -> ... -> Copiar member ID) o tu email".format(target))
    if "@" in target:
        user = _api(token, "users.lookupByEmail", {"email": target}, form=True)
        resuelto = _open_dm(token, user["user"]["id"])
    elif target[0] in "UW" and len(target) >= 9 and target[1:].isalnum():
        resuelto = _open_dm(token, target)
    else:
        # ID de canal (C.../G...) o "#nombre". Un D... ya ES un DM.
        resuelto = (target, target.startswith("D"))

    with _CACHE_LOCK:
        _TARGET_CACHE[key] = resuelto
    return resuelto


def _open_dm(token, user_id):
    """Abre el DM con `user_id`.

    Si falta `im:write` cae a mandarle al user ID directo: chat.postMessage
    tambien acepta un user ID como channel, asi que con solo `chat:write` la
    alerta igual llega. Una app mal scopeada no deberia dejarte sin aviso.
    """
    try:
        r = _api(token, "conversations.open", {"users": user_id})
        return (r["channel"]["id"], True)
    except SlackError as e:
        if "missing_scope" in str(e):
            return (user_id, True)
        raise


def reset_cache():
    with _CACHE_LOCK:
        _TARGET_CACHE.clear()


# ----------------------------------------------------------------------- envio

def is_configured(cfg):
    return bool((cfg.get("slack_bot_token") and cfg.get("slack_target"))
                or cfg.get("slack_webhook_url"))


def describe(cfg):
    """Una linea para el tooltip/menu: por donde va a salir la alerta."""
    if cfg.get("slack_bot_token") and cfg.get("slack_target"):
        return "bot -> {}".format(cfg["slack_target"])
    if cfg.get("slack_webhook_url"):
        return "webhook"
    return "sin configurar"


def send_sync(cfg, text, mention=""):
    """Manda y devuelve una linea de estado. Tira SlackError si fallo.

    `mention` se agrega SOLO si el destino es un canal: en un DM un `<!here>`
    no menciona a nadie (el push llega igual) y solo ensucia el mensaje.
    """
    token = (cfg.get("slack_bot_token") or "").strip()
    target = (cfg.get("slack_target") or "").strip()

    if token and target:
        channel, es_dm = _resolve(token, target)
        cuerpo = text if es_dm else " ".join(x for x in (mention, text) if x)
        _api(token, "chat.postMessage", {
            "channel": channel,
            "text": cuerpo,
            # sin esto un link en el texto se despliega en una preview enorme
            "unfurl_links": False,
            "unfurl_media": False,
        })
        return "bot -> {} ({})".format(channel, "DM" if es_dm else "canal")

    webhook = (cfg.get("slack_webhook_url") or "").strip()
    if webhook:
        _post_webhook(webhook, " ".join(x for x in (mention, text) if x))
        return "webhook -> ok"

    raise SlackError("Slack no esta configurado: falta `slack_bot_token` + "
                     "`slack_target`, o `slack_webhook_url`")


def send(cfg, text, mention="", done=None):
    """Fire and forget en un hilo daemon. Nunca puede romper la UI.

    Esto sale del tick de la mascota (cada 1s): un POST de 8s en el hilo de Qt
    congelaria el overlay y, peor, retrasaria la deteccion del umbral
    siguiente. `done(ok, detalle)` se llama DESDE el hilo: si toca UI, que sea
    via signal de Qt.
    """
    def _run():
        try:
            detalle = send_sync(cfg, text, mention)
            ok = True
        except SlackError as e:
            detalle, ok = str(e), False
        except Exception as e:  # un bug aca no puede matar la alerta local
            detalle, ok = "error inesperado: {}".format(e), False
        if done:
            try:
                done(ok, detalle)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
