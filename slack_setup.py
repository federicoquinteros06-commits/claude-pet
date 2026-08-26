#!/usr/bin/env python3
"""
slack_setup.py — deja el bot de Slack listo, sin copiar IDs a mano.

Lo unico que no puede hacer este script es lo que pasa en el navegador: crear
la app y darle "Install to Workspace" pide tu consentimiento OAuth, y ese paso
existe justamente para que nadie emita un token en tu nombre. Todo lo de
despues lo hace aca.

    1. verifica el token contra `auth.test` y dice a que workspace entro
    2. busca tu member ID por email (users.list) y lo escribe en config.json
    3. manda un DM de prueba de verdad, y reporta el error real si falla

Uso tipico, una vez que pegaste el `xoxb-...` en ~/.claude/pet/config.json:

    python slack_setup.py

Si preferis no dejar el token en un archivo:

    CLAUDE_PET_SLACK_BOT_TOKEN=xoxb-... python slack_setup.py --email vos@empresa.com

Y si no queres darle los scopes de lectura de usuarios, pasale el destino a mano
(perfil -> ... -> Copiar member ID):

    python slack_setup.py --target U0123ABCD
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Mismo truco que el collector: si corres esto desde la carpeta del proyecto,
# el modulo esta al lado. Si lo corres desde ~/.claude/pet, tambien.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_pet_slack as slack  # noqa: E402

PET_DIR = Path.home() / ".claude" / "pet"
CONFIG_PATH = PET_DIR / "config.json"


def leer_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_config(cfg):
    """Escritura atomica, igual que el resto del proyecto: un config.json a
    medio escribir se lo come la mascota en el proximo arranque."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def buscar_por_email(token, email):
    """Recorre users.list buscando el email. `users.lookupByEmail` seria una
    llamada sola, pero falla con `users_not_found` cuando el email de Slack no
    es el que vos creias — y ahi no te dice cuales SI existen. Listar cuesta
    una llamada mas y permite mostrar los candidatos."""
    objetivo = (email or "").strip().lower()
    humanos = []
    cursor = ""
    while True:
        args = {"limit": 200}
        if cursor:
            args["cursor"] = cursor
        r = slack._api(token, "users.list", args, form=True)
        for u in r.get("members", []):
            if u.get("deleted") or u.get("is_bot") or u.get("id") == "USLACKBOT":
                continue
            perfil = u.get("profile") or {}
            humanos.append((u["id"],
                            perfil.get("real_name") or u.get("name") or "?",
                            (perfil.get("email") or "").lower()))
        cursor = (r.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break

    if objetivo:
        for uid, nombre, mail in humanos:
            if mail == objetivo:
                return uid, nombre
    return None, humanos


def main():
    ap = argparse.ArgumentParser(description="Configura el bot de Slack de Claude Pet")
    ap.add_argument("--token", help="xoxb-... (por defecto: config.json o el entorno)")
    ap.add_argument("--email", help="tu email en Slack, para encontrar tu member ID")
    ap.add_argument("--target", help="member ID o ID de canal, si ya lo tenes")
    ap.add_argument("--no-test", action="store_true", help="no mandar el mensaje de prueba")
    args = ap.parse_args()

    cfg = leer_config()
    token = (args.token or os.environ.get("CLAUDE_PET_SLACK_BOT_TOKEN")
             or cfg.get("slack_bot_token") or "").strip()

    if not token:
        print("No encuentro el bot token.\n\n"
              "  1. api.slack.com/apps -> Create New App -> From an app manifest\n"
              "     (pega slack_app_manifest.yaml) -> Install to Workspace\n"
              "  2. OAuth & Permissions -> copia el 'Bot User OAuth Token' (xoxb-...)\n"
              "  3. pegalo en {} como \"slack_bot_token\"\n"
              "  4. volve a correr este script".format(CONFIG_PATH))
        return 1

    # 1. el token sirve?
    try:
        quien = slack._api(token, "auth.test", {})
    except slack.SlackError as e:
        print("El token no paso auth.test: {}".format(e))
        return 1
    print("Token OK · workspace '{}' · bot '{}' ({})".format(
        quien.get("team"), quien.get("user"), quien.get("user_id")))

    # 2. destino
    target = (args.target or cfg.get("slack_target") or "").strip()
    if target:
        print("Destino ya configurado: {}".format(target))
    else:
        email = args.email or cfg.get("slack_email") or ""
        if not email:
            print("\nFalta el destino. Pasame --email tu@empresa.com (el de tu "
                  "cuenta de Slack) o --target U0123ABCD.")
            return 1
        try:
            uid, info = buscar_por_email(token, email)
        except slack.SlackError as e:
            print("\nNo pude listar usuarios: {}\n"
                  "Si no le diste los scopes users:read / users:read.email, "
                  "pasame tu member ID con --target.".format(e))
            return 1
        if not uid:
            print("\nNinguna cuenta del workspace tiene el email '{}'.".format(email))
            print("Estas son las que hay (usa --target con el ID de la tuya):")
            for u, nombre, mail in info[:25]:
                print("  {:<12} {:<28} {}".format(u, nombre[:28], mail or "(sin email visible)"))
            if len(info) > 25:
                print("  ... y {} mas".format(len(info) - 25))
            return 1
        target = uid
        print("Sos {} en este workspace ({})".format(uid, info))

    # 3. guardar (el token solo si vino por argumento; si lo pasaste por
    #    entorno fue justamente para que no quedara en disco)
    cfg["slack_target"] = target
    if args.token:
        cfg["slack_bot_token"] = token
    guardar_config(cfg)
    print("Guardado en {}".format(CONFIG_PATH))

    # 4. probar de verdad. Un setup que no manda un mensaje real no probo nada:
    #    auth.test pasa aunque falte chat:write o im:write.
    if args.no_test:
        return 0
    try:
        detalle = slack.send_sync(
            {"slack_bot_token": token, "slack_target": target},
            ":wave: Claude Pet quedo configurado. Las alertas de uso van a "
            "llegar por aca.")
    except slack.SlackError as e:
        print("\nEl mensaje de prueba fallo: {}".format(e))
        return 1
    print("Mensaje de prueba enviado · {}".format(detalle))
    print("\nUltimo paso: reinicia la mascota, la config se lee al arrancar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
