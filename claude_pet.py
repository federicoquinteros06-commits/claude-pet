#!/usr/bin/env python3
"""
claude_pet.py — mascota de escritorio siempre visible con el uso de Claude Code.

Lee ~/.claude/pet/sessions/*.json (que escribe claude_pet_collector.py),
muestra el % de la ventana de 5h y de 7d, y dispara:
  - avisos  en 25/50/75/85% (notificacion + sonido)
  - alarma  en 90% (+ pantalla completa en el monitor principal)
  - corte   en 95%+ (mata las sesiones de Claude Code, sin sonido ni notif)
Cada umbral se dispara UNA sola vez por ventana. Cuando la ventana se resetea
cambia resets_at y los umbrales se re-arman solos.

Requisitos:  pip install PySide6
Config:      ~/.claude/pet/config.json  (se crea solo la primera vez)
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtSvg, QtWidgets
from PySide6.QtCore import Qt

PET_DIR = Path.home() / ".claude" / "pet"
SESSIONS_DIR = PET_DIR / "sessions"
CONFIG_PATH = PET_DIR / "config.json"
FIRED_PATH = PET_DIR / "fired.json"
POS_PATH = PET_DIR / "position.json"

FRESH_WINDOW = 300  # una sesion cuenta como viva si escribio hace < 5 min
STALE_HINT = 90     # a partir de aca la mascota avisa que el dato envejece
POLL_MS = 1000

# El poller de /api/oauth/usage es opcional: si falta el modulo la mascota
# sigue andando con lo que reporte el collector del statusLine.
try:
    from claude_pet_usage import USAGE_PATH, USAGE_FRESH, STATE_PATH, start_poller
    HAS_POLLER = True
except Exception:
    HAS_POLLER = False

# winsound es exclusivo de Windows: es lo unico que permite tonos propios sin
# cargar un .wav. En mac/Linux cae al beep generico de Qt (ver _play_alert).
try:
    import winsound
    HAS_WINSOUND = sys.platform == "win32"
except ImportError:
    HAS_WINSOUND = False

DEFAULT_CONFIG = {
    # Aviso (notif + sonido): 25/50/75/85. Alarma (+ pantalla completa): 90.
    # 95%+ es el corte automatico, mas abajo, y deliberadamente NO suena ni
    # notifica -- a esa altura la unica alerta que importa es la que corta.
    "warn_thresholds": [25, 50, 75, 85],
    "alarm_thresholds": [90],
    "watch_seven_day": True,          # tambien alertar sobre la ventana semanal
    "seven_day_thresholds": [85, 95],
    "muted": False,
    # Pantalla completa en la alarma (90%). Deliberadamente NO depende de
    # `muted`: es el canal para cuando el resto esta silenciado.
    "fullscreen_alert_enabled": True,
    # Corte automatico: mata los procesos de Claude Code (no VS Code, no la
    # terminal, no la app de escritorio) al cruzar `kill_threshold`.
    "auto_kill_enabled": True,
    "kill_threshold": 95,
    "scale": 1.0,
    "usage_poller_enabled": True,   # consulta /api/oauth/usage; anda sin TUI
    "usage_poll_seconds": 140,  # ver DEFAULT_POLL_SECONDS en claude_pet_usage
}

# --- paleta por estado -------------------------------------------------------
# Semaforo clasico en vez de la rampa anclada en el naranja de marca: verde
# hasta MOOD_BREAKPOINTS[0], amarillo hasta [1], naranja hasta [2], rojo de
# ahi en mas. "credit" (violeta) queda aparte de este semaforo -- es la senal
# especifica de estar entrando en creditos de uso (>=99%), no un escalon mas
# de severidad normal.
MOODS = {
    "calm":    ("#2E9E56", "tranqui"),
    "watch":   ("#E8B04B", "ojo"),
    "warn":    ("#E2601F", "che"),
    "alarm":   ("#C0271B", "PARA"),
    "credit":  ("#8E2A5B", "CREDITOS"),
    # Gris a proposito, ni verde ni rojo: "no se" no es lo mismo que "estas
    # bien". Antes pct=None caia en "calm" -- visto en vivo el 25/8, un
    # PermissionError de un antivirus interceptando la escritura del poller
    # dejo el dato tan viejo que read_sessions() lo descarto, pct quedo en
    # None, y la mascota se veia VERDE Y TRANQUILA durante ~2.5h sin tener
    # ningun dato real. Verde ahora significa "se que estas bien", no "no se".
    "unknown": ("#6B6B76", "SIN DATOS"),
}

# Bordes del semaforo, independientes de warn_thresholds/alarm_thresholds:
# esos son CUANDO avisar, esto es de que color se ve el widget. Antes estaban
# atados al primer warn/alarm threshold, pero con 4 avisos (25/50/75/85) eso
# ya no da un semaforo de 4 colores limpio -- se separan a proposito.
MOOD_BREAKPOINTS = (50, 75, 90)  # verde | amarillo | naranja | rojo

# Logo oficial de Claude, tomado de resources/claude-logo.svg de la extension
# de VS Code. Embebido para no depender de la ruta, que lleva la version y
# cambia con cada update.
CLAUDE_LOGO_SVG = '<svg height="1em" style="flex:none;line-height:1" viewBox="0 0 24 24" width="1em" xmlns="http://www.w3.org/2000/svg"><title>Claude</title><path d="M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z" fill="#D97757" fill-rule="nonzero"></path></svg>'
_LOGO_CACHE = {}


def logo_renderer(hex_color):
    """QSvgRenderer cacheado por color. Sin cache reparsearia el SVG 20 veces
    por segundo (tick de 1s + animacion de 50ms)."""
    r = _LOGO_CACHE.get(hex_color)
    if r is None:
        r = QtSvg.QSvgRenderer(
            CLAUDE_LOGO_SVG.replace("#D97757", hex_color).encode("utf-8"))
        _LOGO_CACHE[hex_color] = r
    return r


# ============================================================ config / estado

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    guardado = load_json(CONFIG_PATH, {})
    cfg.update(guardado)
    # Una clave nueva tiene que APARECER en el archivo, no solo en el default:
    # el usuario edita config.json desde el tray, y una opcion que no esta
    # escrita ahi es una opcion que nadie descubre.
    if any(k not in guardado for k in DEFAULT_CONFIG):
        save_json(CONFIG_PATH, cfg)
    return cfg


def read_sessions() -> dict:
    """Agrega todas las sesiones vivas. rate_limits es a nivel cuenta,
    asi que tomamos el reporte mas reciente."""
    now = time.time()
    live = []
    try:
        for f in SESSIONS_DIR.glob("*.json"):
            d = load_json(f, None)
            if d and now - d.get("ts", 0) < FRESH_WINDOW:
                live.append(d)
    except OSError:
        pass

    if not live:
        out = {"live": 0, "five_hour": None, "seven_day": None,
               "context_pct": None, "cost_usd": 0.0, "model": None}
    else:
        # por frescura del DATO, no del proceso: ver rl_ts en el collector
        live.sort(key=lambda d: d.get("rl_ts") or d.get("ts", 0), reverse=True)
        newest = live[0]
        out = {
            "live": len(live),
            "five_hour": newest.get("five_hour"),
            "seven_day": newest.get("seven_day"),
            # el contexto si es por sesion: mostramos el mas cargado
            "context_pct": max((d.get("context_pct") or 0) for d in live),
            "cost_usd": sum((d.get("cost_usd") or 0) for d in live),
            "model": newest.get("model"),
            "data_ts": newest.get("rl_ts") or newest.get("ts"),
        }

    # Las ventanas las manda el poller cuando esta fresco: es a nivel cuenta,
    # anda con cualquier UI (el statusLine solo existe en la TUI) y nunca queda
    # congelado por una sesion ociosa. Las sesiones siguen aportando el conteo
    # de instancias, el contexto y el costo, que el endpoint no conoce.
    if HAS_POLLER:
        u = load_json(USAGE_PATH, None)
        if u:
            age = now - u.get("ts", 0)
            out["usage_age"] = age
            # Preferir el dato MAS NUEVO, no siempre el del poller: con
            # USAGE_FRESH alto, un valor viejo del poller tapaba uno recien
            # traido por el statusLine (que llega gratis en los headers y no
            # gasta requests). Si el poller esta limitado por un 429, la
            # sesion de TUI pasa a mandar sola.
            session_ts = (live[0].get("rl_ts") or live[0].get("ts", 0)) if live else 0
            if age < USAGE_FRESH and u.get("ts", 0) >= session_ts:
                for k in ("five_hour", "seven_day"):
                    if u.get(k):
                        out[k] = u[k]
                out["usage_source"] = "poller"
                out["data_ts"] = u.get("ts")
        st = load_json(STATE_PATH, None)
        if st:
            # cuando toca el proximo intento, segun el propio poller: sirve
            # para distinguir un hueco normal de la cadencia de un cuelgue.
            if st.get("ts") and st.get("next_retry_in"):
                out["next_due"] = st["ts"] + st["next_retry_in"]
            # Dos relojes distintos: cuando se INTENTO y cuando entro un dato
            # VALIDO. Con uno solo un 429 congelaba la hora sin decir por que,
            # mientras la regresiva "prox" se reiniciaba igual y la mascota
            # parecia sana. No se distinguia un hueco normal de un poller roto.
            out["attempt_ts"] = st.get("ts")
            out["attempt_ok"] = bool(st.get("ok"))
            if not st.get("ok"):
                out["poller_error"] = st.get("error")
                out["poller_fails"] = st.get("consecutive_failures", 0)
    return out


# ==================================================================== alertas

# (frecuencia_hz, duracion_ms). Aviso: dos notas subiendo, tipo timbre suave.
# Alarma: sirena alternando dos tonos agudos, repetida 3 veces — pensada para
# distinguirse del beep generico de Windows sin sonar a incendio de oficina.
ALERT_TONES = {
    "warn":  [(988, 130), (1319, 170)],
    "alarm": [(1568, 120), (1175, 120)] * 3,
}


# macOS no tiene equivalente a winsound.Beep(freq, ms) en la stdlib, pero si
# trae `afplay` y un set de .aiff del sistema. Alcanza para conservar lo que
# importa del diseno de Windows: que aviso y alarma suenen DISTINTO, no solo
# una cantidad distinta de veces del mismo beep.
#
# La eleccion es por TIMBRE, no por volumen. El primer intento fue
# Ping+Glass / Sosumi x3 y no servia: los tres son campanitas agudas, asi que
# escuchandolos sin mirar la pantalla no se distinguia un aviso de una alarma
# — exactamente el problema que se queria evitar. Probado a oido el 26/8/2026.
#
#   warn   Tink -> Glass       agudo y breve, se lo escucha y se sigue
#   alarm  Funk <-> Basso x2   alterna grave/medio, insiste
#
# Es la traduccion mas cercana a ALERT_TONES de Windows (dos notas subiendo
# para aviso, sirena alternada para alarma).
MAC_SOUNDS_DIR = "/System/Library/Sounds"
MAC_SOUNDS = {
    "warn":  ["Tink", "Glass"],
    "alarm": ["Funk", "Basso"] * 2,
}


def _play_alert(level: str) -> None:
    """Reproduce el patron de `level` en un hilo aparte.

    `winsound.Beep()` es sincronico: llamarlo en el hilo de Qt bloquearia el
    tick de 1s y la animacion de 50ms mientras dura la secuencia (hasta ~700ms
    en la alarma). `afplay` en macOS tambien es sincronico, asi que el hilo
    daemon vale igual para las dos plataformas.

    Ultimo recurso (Linux, o si afplay falla): el beep generico de Qt repetido,
    donde aviso y alarma solo se distinguen por el conteo.
    """
    def _qt_beep():
        for _ in range(3 if level == "alarm" else 1):
            QtWidgets.QApplication.beep()
            time.sleep(0.22)

    def _run():
        if HAS_WINSOUND:
            for freq, dur in ALERT_TONES.get(level, ALERT_TONES["warn"]):
                try:
                    winsound.Beep(freq, dur)
                except RuntimeError:
                    break  # ej. sin dispositivo de audio: no reintentar en loop
        elif sys.platform == "darwin":
            for nombre in MAC_SOUNDS.get(level, MAC_SOUNDS["warn"]):
                try:
                    subprocess.run(
                        ["afplay", f"{MAC_SOUNDS_DIR}/{nombre}.aiff"],
                        capture_output=True, timeout=5)
                except Exception:
                    _qt_beep()  # sin afplay o sin audio: que al menos suene algo
                    break
        else:
            _qt_beep()

    threading.Thread(target=_run, daemon=True).start()


def _notify(tray, title: str, body: str) -> None:
    """Notificacion nativa del SO.

    En Windows/Linux es QSystemTrayIcon.showMessage() y listo. En macOS NO:
    Qt rutea showMessage() por UNUserNotificationCenter, que exige que el
    proceso tenga bundle identifier. Corriendo como `python claude_pet.py` no
    hay bundle, [NSBundle mainBundle].bundleIdentifier es nil, y la
    notificacion no aparece — fallando en SILENCIO, que es el peor modo de
    fallar para un canal de alerta: parece que anda.

    `osascript` no necesita bundle propio, asi que es la via que si llega.
    """
    if sys.platform == "darwin":
        # Escapado manual: el body trae % y · y lo arma f-string, pero una
        # comilla doble suelta romperia el script de AppleScript.
        esc = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{esc(body)}" with title "{esc(title)}"'],
                capture_output=True, timeout=10)
            return
        except Exception:
            pass  # cae al tray de Qt, que en el peor caso es un no-op

    try:
        tray.showMessage(title, body,
                         QtWidgets.QSystemTrayIcon.Warning, 8000)
    except Exception:
        pass


# Segmento de ruta EXCLUSIVO del binario nativo que lanza la extension de
# Claude Code en VS Code (.../extensions/anthropic.claude-code-*/resources/
# native-binary/claude.exe). La app de escritorio de Claude usa el MISMO
# nombre de ejecutable (WindowsApps\Claude_...\app\claude.exe) para un
# producto totalmente distinto que no consume la ventana de 5h: filtrar por
# nombre de proceso a secas mataria esa app tambien. Verificado contra los
# procesos reales de esta maquina el 25/8/2026.
CLAUDE_CODE_CLI_MARKER = "native-binary\\claude.exe"

# En macOS el problema es EL OPUESTO al de Windows, y por eso el filtro es
# otro. Aca el nombre NO es ambiguo: la app de escritorio corre como 'Claude'
# (mayuscula), sus helpers de Electron como 'Claude Helper (Renderer)' y el
# widget como 'ClaudeUsageWidgetExtension'. Ninguno es 'claude' exacto, asi
# que un match case-sensitive sobre el basename los excluye a los tres solo,
# sin necesidad del desempate por ruta que hizo falta en Windows.
#
# Y hace falta que sea por basename, no por ruta, porque las DOS formas de
# correr Claude Code en un Mac se ven distinto en `ps -axo comm=`:
#
#   .../anthropic.claude-code-*/resources/native-binary/claude   <- panel VS Code
#   claude                                                       <- CLI nativo
#
# El CLI nativo (~/.local/bin/claude -> ~/.local/share/claude/versions/X) sale
# PELADO: ps no resuelve el symlink. Filtrar por marcador de ruta como en
# Windows dejaria vivas todas las sesiones de terminal, que queman la ventana
# de 5h igual que las del panel. Verificado contra los 10 procesos reales de
# esta maquina el 25/8/2026.
CLAUDE_CODE_CLI_COMM = "claude"

# Gracia entre el SIGTERM y el SIGKILL. El CLI cierra la sesion limpio si le
# dan el TERM; el KILL es el equivalente al -Force de Stop-Process y solo va
# para los que no se fueron solos.
KILL_GRACE_S = 1.5


def _kill_claude_code_processes() -> int:
    """Mata los procesos de Claude Code (CLI) que esten corriendo. No toca
    VS Code, la terminal que los lanzo, ni la app de escritorio de Claude.

    Devuelve cuantos mato, o -1 si no se pudo confirmar (no rompe la mascota
    por esto: se reporta el -1 tal cual en la pantalla completa).

    Cada plataforma identifica al CLI de forma distinta, y no por capricho:
    en Windows el nombre de proceso es ambiguo y hay que desempatar por ruta,
    en macOS pasa exactamente lo contrario. Ver cada rama.
    """
    if sys.platform == "win32":
        return _kill_win32()
    if sys.platform == "darwin":
        return _kill_darwin()
    return -1


def _kill_win32() -> int:
    """PowerShell + WMI. Ver CLAUDE_CODE_CLI_MARKER para por que el filtro es
    por ruta y no por nombre."""
    # -Filter "Name = 'claude.exe'" acota en la consulta WMI ANTES del
    # substring match. Sin eso, el propio powershell.exe que ejecuta esta
    # consulta calza en el filtro: su CommandLine incluye, literal, el texto
    # de este mismo -Command (que contiene "native-binary\claude.exe" como
    # patron) y terminaria matandose a si mismo a mitad de la ejecucion.
    # Verificado en vivo el 25/8/2026: sin el -Filter, la consulta devolvia
    # el PID de powershell.exe junto con los 3 de Claude Code.
    script = (
        "$p = Get-CimInstance Win32_Process -Filter \"Name = 'claude.exe'\" | "
        "Where-Object { "
        f"$_.CommandLine -like '*{CLAUDE_CODE_CLI_MARKER}*' }}; "
        "$p | ForEach-Object { "
        "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "($p | Measure-Object).Count"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15,
        )
        return int(r.stdout.strip() or 0)
    except Exception:
        return -1


def _pids_darwin() -> list:
    """PIDs de las sesiones de Claude Code vivas en esta Mac.

    Separado de _kill_darwin() a proposito: es la unica forma de auditar el
    filtro sin matar nada. Ver la seccion de macOS del README.

    Devuelve None si no se pudo consultar (que _kill_darwin traduce al -1 del
    contrato), lista vacia si no habia ninguna.
    """
    try:
        r = subprocess.run(["ps", "-axo", "pid=,comm="],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0:
        return None

    yo = os.getpid()
    pids = []
    for linea in r.stdout.splitlines():
        pid, _, comm = linea.strip().partition(" ")
        if not pid.isdigit():
            continue
        # basename: cubre tanto la ruta completa del panel de VS Code como el
        # 'claude' pelado del CLI nativo. Case-sensitive: ver
        # CLAUDE_CODE_CLI_COMM para que es lo que se esta excluyendo.
        if os.path.basename(comm.strip()) != CLAUDE_CODE_CLI_COMM:
            continue
        if int(pid) != yo:  # la mascota corre bajo python, pero es gratis
            pids.append(int(pid))
    return pids


def _kill_darwin() -> int:
    """SIGTERM y, a los que sobrevivan, SIGKILL.

    Mucho mas simple que la rama de Windows: no hay WMI, y sobre todo no
    existe el problema de que la propia consulta se automatchee (`ps` no
    inyecta el patron de busqueda en su linea de comandos, `Get-CimInstance
    -Command <script con el patron>` si).
    """
    pids = _pids_darwin()
    if pids is None:
        return -1

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    if pids:
        time.sleep(KILL_GRACE_S)

    for pid in pids:
        try:
            os.kill(pid, 0)  # signal 0 = sondear, no mata
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    return len(pids)


# Dos ventanas reales estan separadas por horas (5h) o dias (7d), asi que
# cualquier resets_at a menos de esto es la MISMA ventana vista con otra
# precision. Ver AlertEngine._canon.
WINDOW_MATCH = 600


class AlertEngine:
    """Dispara cada umbral una sola vez por ventana."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.fired = set(load_json(FIRED_PATH, []))
        # resets_at canonicos ya vistos, por ventana. Se reconstruye de las
        # claves persistidas para que el dedupe sobreviva al reinicio.
        self.seen = {}
        for k in self.fired:
            parts = k.split(":")
            if len(parts) == 3:
                try:
                    self.seen.setdefault(parts[0], []).append(float(parts[2]))
                except ValueError:
                    pass

    def _canon(self, window: str, resets_at):
        """Devuelve un id de ventana estable a partir de resets_at.

        El endpoint /api/oauth/usage manda resets_at ISO-8601 con microsegundos
        que NO son estables entre respuestas: el mismo reset vuelve como
        ...399.55252, ...399.565691, ...400.492996. El statusLine, en cambio,
        reporta el epoch entero (...400). Interpolar ese float crudo en la clave
        fabricaba una clave nueva por cada poll, asi que el dedupe no dedupeaba:
        fired.json llego a 88 entradas de UNA sola ventana (24 disparos del
        umbral 90) y el usuario se comio 88 notificaciones + beeps donde debia
        haber 4.

        Redondear no alcanza: deja un borde en el .5 y el jitter observado
        (0.94s) lo cruza. Comparamos por cercania, que es lo que el problema
        realmente pide: "es la misma ventana".
        """
        if resets_at is None:
            return "none"
        r = float(resets_at)
        vistos = self.seen.setdefault(window, [])
        for v in vistos:
            if abs(v - r) < WINDOW_MATCH:
                return f"{v:.0f}"
        vistos.append(r)
        return f"{r:.0f}"

    def _key(self, window: str, threshold: int, resets_at) -> str:
        return f"{window}:{threshold}:{self._canon(window, resets_at)}"

    def check(self, window: str, info: dict, warns, alarms):
        """Devuelve lista de (nivel, umbral, pct, resets_at) recien cruzados."""
        if not info:
            return []
        pct = info.get("used_percentage")
        resets_at = info.get("resets_at")
        if pct is None:
            return []

        out = []
        for level, thresholds in (("warn", warns), ("alarm", alarms)):
            for t in thresholds:
                if pct >= t:
                    k = self._key(window, t, resets_at)
                    if k not in self.fired:
                        self.fired.add(k)
                        out.append((level, t, pct, resets_at))

        if out:
            # podamos para que el archivo no crezca infinito
            save_json(FIRED_PATH, sorted(self.fired)[-200:])
        return out


# ============================================================ widget

class AlertScreen(QtWidgets.QWidget):
    """Toma toda la pantalla PRINCIPAL de Windows en la alarma (90%) y en el
    corte automatico (95%+).

    Existe para el escenario que ni el toast ni el sonido cubren: notificaciones
    muteadas, sin auriculares puestos, o mirando otro monitor de los varios que
    usa el usuario. `primaryScreen()` es el mismo monitor que Windows llama
    "Pantalla principal" en Configuracion -> Sistema -> Pantalla, asi que cae
    donde el usuario lo espera sin importar en cual esta la mascota.

    A diferencia de `Pet`, que usa `Qt.Tool` para no robar foco nunca, esta
    ventana SI necesita foco: es lo que hace que una tecla o un click la
    cierren, y sin foco Windows a veces la deja atras de otras ventanas
    always-on-top en vez de al frente.
    """

    AUTO_CLOSE_S = 25

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.StrongFocus)

        self.label = QtWidgets.QLabel(alignment=Qt.AlignCenter)
        self.label.setStyleSheet(
            "color: white; font-size: 120px; font-weight: 600;")
        self.detail = QtWidgets.QLabel(alignment=Qt.AlignCenter)
        self.detail.setStyleSheet("color: white; font-size: 32px;")
        self.hint = QtWidgets.QLabel(
            "clic o cualquier tecla para cerrar", alignment=Qt.AlignCenter)
        self.hint.setStyleSheet("color: rgba(255,255,255,170); font-size: 18px;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self.label)
        layout.addWidget(self.detail)
        layout.addStretch()
        layout.addWidget(self.hint)
        layout.setContentsMargins(40, 40, 40, 60)

        # Se cierra sola si nadie la toca: es un aviso, no un secuestro de
        # pantalla. 25s alcanza para leerla sin quedar plantada si el usuario
        # esta lejos de las tres pantallas.
        self._close_timer = QtCore.QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close)

    def show_alert(self, headline: str, detail: str, color: str) -> None:
        screen = QtWidgets.QApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        self.setStyleSheet(f"background-color: {color};")
        self.label.setText(headline)
        self.detail.setText(detail)
        self.showFullScreen()
        # Sin esto la alarma no aparece sobre una app en pantalla completa, que
        # es JUSTO cuando mas se la necesita: es el unico canal que no se corta
        # con `muted`, el respaldo para cuando no estas mirando la mascota.
        _mac_keep_visible(self)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._close_timer.start(self.AUTO_CLOSE_S * 1000)

    def mousePressEvent(self, e):
        self.close()

    def keyPressEvent(self, e):
        self.close()


class Pet(QtWidgets.QWidget):
    # _kill_claude_code_processes corre en un hilo aparte (es un subprocess,
    # hasta 15s de timeout): esta signal es como su resultado vuelve al hilo
    # de Qt para poder tocar el tray/AlertScreen sin crashear.
    kill_result = QtCore.Signal(int, float)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.engine = AlertEngine(cfg)
        self.alert_screen = AlertScreen()
        self.kill_result.connect(self._on_kill_result)
        self.state = read_sessions()
        self.mood = "unknown"  # hasta el primer tick() no hay dato de verdad
        self.pulse = 0.0
        self.flash_until = 0.0
        self.drag_offset = None
        # 1.5x el intervalo de poll: por debajo de eso un dato "viejo"
        # es solo el lag normal entre consultas.
        self.stale_hint = max(STALE_HINT,
                              int(cfg.get("usage_poll_seconds", 60)) * 1.5)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Claude Pet")

        s = float(cfg.get("scale", 1.0))
        self.resize(int(262 * s), int(152 * s))
        self._restore_position()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(POLL_MS)

        self.anim = QtCore.QTimer(self)
        self.anim.timeout.connect(self._animate)
        self.anim.start(50)

        self._build_tray()

    # ---------------------------------------------------------------- tray
    def _build_tray(self):
        pm = QtGui.QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setBrush(QtGui.QColor(MOODS["calm"][0]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.end()

        self.tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(pm), self)
        menu = QtWidgets.QMenu()
        self.act_mute = menu.addAction("Silenciar alertas")
        self.act_mute.setCheckable(True)
        self.act_mute.setChecked(bool(self.cfg.get("muted")))
        self.act_mute.toggled.connect(self._toggle_mute)
        menu.addAction("Abrir config").triggered.connect(
            lambda: QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(CONFIG_PATH))
            )
        )
        menu.addSeparator()
        menu.addAction("Salir").triggered.connect(QtWidgets.QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _toggle_mute(self, on: bool):
        self.cfg["muted"] = on
        stored = load_json(CONFIG_PATH, {})
        stored["muted"] = on
        save_json(CONFIG_PATH, stored)

    # ------------------------------------------------------------ posicion
    def _restore_position(self):
        pos = load_json(POS_PATH, None)
        if pos:
            self.move(int(pos.get("x", 60)), int(pos.get("y", 60)))
        else:
            screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 30, screen.top() + 40)

    def _save_position(self):
        save_json(POS_PATH, {"x": self.x(), "y": self.y()})

    # ----------------------------------------------------------- interaccion
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, e):
        self.drag_offset = None
        self._save_position()

    # ------------------------------------------------------------ logica
    def _mood_for(self, pct):
        if pct is None:
            return "unknown"
        if pct >= 99:
            return "credit"
        if pct >= MOOD_BREAKPOINTS[2]:
            return "alarm"
        if pct >= MOOD_BREAKPOINTS[1]:
            return "warn"
        if pct >= MOOD_BREAKPOINTS[0]:
            return "watch"
        return "calm"

    def tick(self):
        self.state = read_sessions()
        five = self.state.get("five_hour")
        pct = (five or {}).get("used_percentage")
        self.mood = self._mood_for(pct)

        events = self.engine.check(
            "five_hour", five,
            self.cfg["warn_thresholds"], self.cfg["alarm_thresholds"],
        )
        if self.cfg.get("watch_seven_day"):
            sd = self.cfg.get("seven_day_thresholds", [])
            events += self.engine.check(
                "seven_day", self.state.get("seven_day"), sd[:1], sd[1:]
            )

        for level, threshold, value, resets_at in events:
            self._fire(level, threshold, value, resets_at)

        if self.cfg.get("auto_kill_enabled", True):
            # namespace distinto ("five_hour_kill") para que este dedupe no
            # comparta claves con warn_thresholds/alarm_thresholds: son
            # eventos independientes aunque miren la misma ventana.
            kill_events = self.engine.check(
                "five_hour_kill", five, [], [self.cfg.get("kill_threshold", 95)]
            )
            for _level, _threshold, value, resets_at in kill_events:
                self._trigger_kill(value)

        # el detalle completo del fallo no entra en el widget: va al tooltip
        err = self.state.get("poller_error")
        lineas = []
        if err:
            lineas.append("poller: {} · fallos consecutivos: {}".format(
                err, self.state.get("poller_fails", 0)))
        self.setToolTip("\n".join(lineas))

        self.update()

    def _fire(self, level, threshold, value, resets_at):
        when = ""
        if resets_at:
            mins = max(0, int((resets_at - time.time()) // 60))
            when = f" · resetea en {mins // 60}h{mins % 60:02d}m"

        title = "ALARMA" if level == "alarm" else "Aviso"
        body = f"{title} · uso de sesion {value:.0f}% (umbral {threshold}%){when}"

        # Pantalla completa: el unico canal que no se corta con `muted`, a
        # proposito. Es el respaldo para cuando el sonido esta silenciado, los
        # auriculares no estan puestos, o el usuario mira otro monitor.
        if level == "alarm" and self.cfg.get("fullscreen_alert_enabled", True):
            self.alert_screen.show_alert(
                f"{value:.0f}%", body, MOODS["alarm"][0])

        if self.cfg.get("muted"):
            return

        # Notificacion del SO
        _notify(self.tray, "Claude Code", body)

        _play_alert(level)

        self.flash_until = time.time() + (4 if level == "alarm" else 1.5)

    def _trigger_kill(self, value):
        """Corte inmediato al cruzar `kill_threshold` (95% por defecto). Sin
        cuenta regresiva ni forma de cancelar, a pedido explicito: mata los
        procesos de Claude Code ya, no VS Code ni la terminal que los lanzo.

        A esta altura NO suena ni notifica — a proposito. 25/50/75/85/90 ya
        avisaron con notif+sonido (90 con pantalla completa incluida); a
        95%+ la unica alerta que queda es la que corta de verdad, y sumar
        sonido/toast encima no aporta nada."""
        self.alert_screen.show_alert(
            "CORTANDO", f"{value:.0f}% de uso · cerrando las sesiones de "
            "Claude Code...", MOODS["alarm"][0])

        def _bg():
            n = _kill_claude_code_processes()
            self.kill_result.emit(n, value)

        threading.Thread(target=_bg, daemon=True).start()

    def _on_kill_result(self, n: int, value: float):
        if n < 0:
            headline, detail = "ATENCION", (
                f"{value:.0f}% de uso · no se pudo confirmar el corte "
                "automatico — revisalo a mano.")
        elif n == 0:
            headline, detail = "CORTE AUTOMATICO", (
                f"{value:.0f}% de uso · no habia sesiones de Claude Code "
                "corriendo en esta maquina.")
        else:
            plural = "es" if n != 1 else ""
            headline, detail = "CORTADO", (
                f"{value:.0f}% de uso · se cerraron {n} sesion{plural} de "
                "Claude Code.")
        self.alert_screen.show_alert(headline, detail, MOODS["alarm"][0])

    def _animate(self):
        self.pulse += 0.12
        if self.mood in ("alarm", "credit") or time.time() < self.flash_until:
            self.update()

    # ------------------------------------------------------------- dibujo
    def paintEvent(self, _):
        import math
        s = float(self.cfg.get("scale", 1.0))
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        color = QtGui.QColor(MOODS[self.mood][0])
        flashing = time.time() < self.flash_until
        alpha = 235
        if flashing or self.mood == "alarm":
            alpha = int(180 + 60 * abs(math.sin(self.pulse)))

        # panel
        panel = QtGui.QColor(22, 22, 26, 225)
        p.setBrush(panel)
        p.setPen(QtGui.QPen(color, 2))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14 * s, 14 * s)

        five = self.state.get("five_hour") or {}
        pct = five.get("used_percentage")
        resets_at = five.get("resets_at")

        age = self.state.get("usage_age")
        stale = age is not None and age > self.stale_hint

        # logo oficial de Claude, recoloreado segun el humor, respirando
        r = 18 * s
        breathe = 1 + 0.05 * math.sin(self.pulse * (3 if self.mood == "alarm" else 1))
        p.save()
        p.translate(38 * s, 30 * s)
        p.scale(breathe, breathe)
        p.setOpacity(alpha / 255)
        logo_renderer(MOODS[self.mood][0]).render(
            p, QtCore.QRectF(-r, -r, 2 * r, 2 * r))
        p.restore()

        # porcentaje grande
        big = QtGui.QFont()
        big.setPointSizeF(20 * s)
        big.setBold(True)
        p.setFont(big)
        p.setPen(color)
        p.drawText(QtCore.QRectF(66 * s, 11 * s, 86 * s, 38 * s),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"{pct:.0f}%" if pct is not None else "--")

        small = QtGui.QFont()
        small.setPointSizeF(9.5 * s)
        p.setFont(small)

        # columna derecha: ventana semanal y reloj actual. El "ahora" es la
        # referencia contra la que se leen "dato" e "intento": sin el hay que
        # ir a mirar el reloj del sistema para saber si el % es fresco o fosil.
        seven = (self.state.get("seven_day") or {}).get("used_percentage")
        if seven is not None:
            p.setPen(QtGui.QColor(150, 150, 158))
            p.drawText(QtCore.QRectF(154 * s, 12 * s, 90 * s, 17 * s),
                       Qt.AlignRight | Qt.AlignVCenter, f"7d {seven:.0f}%")
        p.setPen(QtGui.QColor(120, 120, 128))
        p.drawText(QtCore.QRectF(154 * s, 30 * s, 90 * s, 17 * s),
                   Qt.AlignRight | Qt.AlignVCenter,
                   "ahora " + time.strftime("%H:%M:%S"))

        # linea 1: cuando resetea, en reloj Y en cuenta regresiva
        p.setPen(QtGui.QColor(170, 170, 178))
        if resets_at:
            secs = max(0, int(resets_at - time.time()))
            clock = time.strftime("%H:%M", time.localtime(resets_at))
            line1 = f"resetea {clock} · en {secs // 3600}h{(secs % 3600) // 60:02d}m"
        elif pct is None:
            if HAS_POLLER and age is not None and age >= USAGE_FRESH:
                err = self.state.get("poller_error")
                # solo el nombre de la excepcion: el detalle va al tooltip
                line1 = f"poller: {err.split(':')[0]}" if err else "poller sin responder"
            else:
                line1 = ("sin datos de rate_limits" if self.state.get("live")
                         else "sin sesiones activas")
        else:
            line1 = "ventana de 5h"
        p.drawText(QtCore.QRectF(64 * s, 52 * s, 180 * s, 17 * s),
                   Qt.AlignLeft, line1)

        # linea 2: hora del dato + cuenta regresiva al proximo poll. El reloj
        # solo no alcanzaba: con un intervalo de 5 min, un hueco normal se ve
        # igual que un cuelgue. La regresiva corre cada segundo, asi que si
        # avanza la mascota esta viva, y si se pasa de cero esta atrasada.
        bits = []
        dts = self.state.get("data_ts")
        if dts:
            bits.append("dato " + time.strftime("%H:%M:%S", time.localtime(dts)))
        else:
            bits.append("sin actualizar")

        late = False
        nd = self.state.get("next_due")
        if nd:
            left = int(nd - time.time())
            if left >= 60:
                bits.append(f"prox {left // 60}m{left % 60:02d}s")
            elif left >= 0:
                bits.append(f"prox {left}s")
            else:
                late = -left > 15  # margen: el hilo despierta con algo de jitter
                over = min(abs(left), 99 * 60 + 59)
                bits.append(f"tarde {over // 60}m{over % 60:02d}s")

        p.setPen(QtGui.QColor(MOODS["watch"][0]) if late
                 else QtGui.QColor(140, 140, 148))
        p.drawText(QtCore.QRectF(64 * s, 69 * s, 180 * s, 17 * s),
                   Qt.AlignLeft, " · ".join(bits))

        # linea 3: el ultimo INTENTO, haya servido o no. La linea 2 solo avanza
        # cuando entra un dato valido, asi que sola no separa "todo bien, falta
        # para el proximo poll" de "viene fallando hace rato". Aca se ve si el
        # % de arriba esta confirmado o es el ultimo valor conocido.
        ats = self.state.get("attempt_ts")
        if ats:
            at = time.strftime("%H:%M:%S", time.localtime(ats))
            if self.state.get("attempt_ok"):
                p.setPen(QtGui.QColor(110, 110, 118))
                line3 = f"intento {at} · ok"
            else:
                err = self.state.get("poller_error") or ""
                # el detalle completo queda en el tooltip; aca solo la etiqueta
                tag = "429" if "429" in err else "error"
                n = self.state.get("poller_fails", 0)
                p.setPen(QtGui.QColor(MOODS["alarm"][0]))
                line3 = f"intento {at} · {tag}" + (f" x{n}" if n > 1 else "")
            p.drawText(QtCore.QRectF(64 * s, 86 * s, 180 * s, 17 * s),
                       Qt.AlignLeft, line3)

        # etiqueta de humor. Ya no lleva "N ses" ni el costo: el conteo solo
        # veia las sesiones con statusLine vivo (el panel de VSCode es invisible
        # para el collector) y el costo sumaba nada mas esas, asi que los dos
        # numeros mentian por defecto y no habia forma de notarlo desde la UI.
        p.setPen(color)
        mood_font = QtGui.QFont()
        mood_font.setPointSizeF(11 * s)
        mood_font.setBold(True)
        p.setFont(mood_font)
        label = MOODS[self.mood][1]
        if stale:
            label += " · DATO VIEJO"
        p.drawText(QtCore.QRectF(18 * s, 107 * s, 226 * s, 20 * s),
                   Qt.AlignLeft | Qt.AlignVCenter, label)

        # barra, abajo del todo
        bar_x, bar_y, bar_w, bar_h = 18 * s, 132 * s, self.width() - 36 * s, 10 * s
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(48, 48, 54))
        p.drawRoundedRect(QtCore.QRectF(bar_x, bar_y, bar_w, bar_h), 5 * s, 5 * s)
        if pct:
            p.setBrush(color)
            p.drawRoundedRect(
                QtCore.QRectF(bar_x, bar_y, bar_w * min(pct, 100) / 100, bar_h),
                5 * s, 5 * s,
            )
        # marcas de umbral
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 110), 1))
        for t in self.cfg["warn_thresholds"] + self.cfg["alarm_thresholds"]:
            x = bar_x + bar_w * t / 100
            p.drawLine(QtCore.QPointF(x, bar_y - 2), QtCore.QPointF(x, bar_y + bar_h + 2))
        p.end()


def _objc():
    """Runtime de ObjC por ctypes, o None si no se puede.

    Se usa para tres cosas que PySide6 no expone y que en un overlay de macOS
    no son opcionales: sacar el icono del Dock, evitar que la ventana se
    esconda sola, y que sobreviva a un cambio de Space. ctypes viene en la
    stdlib; PyObjC seria una dependencia nueva solo para esto.
    """
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_msgSend.restype = ctypes.c_void_p
        return objc, ctypes
    except Exception:
        return None


def _msg(objc, ctypes, receptor, selector, arg=None, argtype=None):
    """Un objc_msgSend con la firma bien declarada.

    objc_msgSend es variadica: si no se redeclaran los argtypes en CADA
    llamada, en arm64 los argumentos viajan por el registro equivocado y el
    resultado es basura (o un crash). De ahi que esto no se pueda cachear.
    """
    if arg is None:
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        return objc.objc_msgSend(receptor, objc.sel_registerName(selector))
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, argtype]
    return objc.objc_msgSend(receptor, objc.sel_registerName(selector), arg)


def _hide_dock_icon() -> None:
    """NSApplicationActivationPolicyAccessory, o sea el LSUIElement de un
    bundle, pero sin bundle.

    La mascota es un overlay que vive en la barra de menu: no tiene por que
    ocupar un lugar en el Dock ni aparecer en Cmd+Tab.

    Puramente cosmetico: si falla, queda el icono y nada mas.
    """
    got = _objc()
    if not got:
        return
    objc, ctypes = got
    try:
        shared = _msg(objc, ctypes, objc.objc_getClass(b"NSApplication"),
                      b"sharedApplication")
        _msg(objc, ctypes, shared, b"setActivationPolicy:", 1, ctypes.c_long)
    except Exception:
        pass


# NSWindowCollectionBehavior. Los dos que importan para un overlay:
#   CanJoinAllSpaces    (1 << 0) la ventana se ve en TODOS los Spaces, no solo
#                               en aquel donde se creo.
#   FullScreenAuxiliary (1 << 8) puede flotar sobre una app en pantalla
#                               completa (que en macOS es un Space propio).
NS_ALL_SPACES = 1 << 0
NS_FULLSCREEN_AUX = 1 << 8

# NSStatusWindowLevel. WindowStaysOnTopHint deja la ventana en el nivel
# flotante normal, que queda POR DEBAJO de una app en fullscreen. 25 es el
# nivel de los items de la barra de menu, que es conceptualmente lo que la
# mascota es.
NS_STATUS_WINDOW_LEVEL = 25


def _mac_keep_visible(widget) -> None:
    """Que la mascota no desaparezca. Hay que arreglar DOS cosas distintas.

    1. `Qt.Tool` en macOS se traduce a un NSPanel con hidesOnDeactivate=YES:
       la ventana se esconde sola cuando la app no es la activa. Y la mascota
       NUNCA es la activa — es justamente el punto de usar Qt.Tool, no robar
       foco. O sea que el overlay se ocultaba apenas tocabas cualquier otra
       ventana. Lo destraba Qt.WA_MacAlwaysShowToolWindow.

    2. Aun visible, la ventana pertenece al Space donde se creo. Cambiar de
       Space (o abrir una app en pantalla completa, que crea el suyo) la deja
       atras. Eso se arregla con el collectionBehavior, y ademas hay que
       subirle el nivel: WindowStaysOnTopHint flota sobre las ventanas
       normales pero no sobre un Space en fullscreen.

    Se llama DESPUES de show(): antes, winId() todavia no tiene NSView.
    """
    if sys.platform != "darwin":
        return

    # 1. el fix de Qt, que no necesita ObjC
    try:
        widget.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
    except AttributeError:
        pass  # nombre distinto en otra version de Qt: no es fatal

    # 2. el fix de Spaces, que si
    got = _objc()
    if not got:
        return
    objc, ctypes = got
    try:
        view = ctypes.c_void_p(int(widget.winId()))
        win = _msg(objc, ctypes, view, b"window")
        if not win:
            return
        _msg(objc, ctypes, win, b"setCollectionBehavior:",
             NS_ALL_SPACES | NS_FULLSCREEN_AUX, ctypes.c_ulong)
        _msg(objc, ctypes, win, b"setLevel:",
             NS_STATUS_WINDOW_LEVEL, ctypes.c_long)
        # hidesOnDeactivate por las dudas: el atributo de Qt lo cubre, pero si
        # esa constante no existiera en esta version de Qt, esto igual lo evita.
        _msg(objc, ctypes, win, b"setHidesOnDeactivate:", 0, ctypes.c_bool)
    except Exception:
        pass


def _single_instance_guard():
    """QSharedMemory como lock entre procesos, o None si ya hay una viva.

    Con el atajo de shell:startup (o el launchd de macOS) mas un lanzamiento
    manual terminarias con dos overlays superpuestos, peleando por
    position.json y duplicando beeps: fired.json no protege contra la carrera
    entre procesos.

    En Windows el bloque de memoria lo libera el SO al morir el proceso, asi
    que un crash no deja un lock huerfano. En POSIX (macOS y Linux) NO: el
    segmento sobrevive al proceso que lo creo, y un unico crash dejaria el
    create() fallando PARA SIEMPRE — la mascota no volveria a arrancar nunca
    sin borrar el segmento a mano. El remedio estandar de Qt en Unix es
    attach()+detach(): si el dueno murio, ese detach baja el refcount a cero
    y el sistema libera el segmento, y el segundo create() ya pasa. Si el
    dueno sigue vivo, su propia referencia mantiene el segmento y el segundo
    create() falla igual — que es justo lo que queremos.
    """
    guard = QtCore.QSharedMemory("claude_pet_single_instance")
    if guard.create(1):
        return guard

    if sys.platform != "win32":
        guard.attach()
        guard.detach()
        if guard.create(1):
            return guard

    return None


def main():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    app = QtWidgets.QApplication(sys.argv)
    _hide_dock_icon()

    guard = _single_instance_guard()
    if guard is None:
        return
    app._pet_guard = guard  # referencia viva: si lo junta el GC, se libera

    if HAS_POLLER:
        start_poller(cfg)

    app.setQuitOnLastWindowClosed(False)
    pet = Pet(cfg)
    pet.show()
    # despues de show(): antes, winId() todavia no tiene NSView detras
    _mac_keep_visible(pet)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
