"""_kill_claude_code_processes: filtro correcto, sin tocar la red de verdad.

El caso que importa es el que motivo la funcion: `claude.exe` es un nombre
ambiguo en esta maquina. La app de escritorio de Claude usa el MISMO nombre de
ejecutable que el CLI de Claude Code (WindowsApps\\Claude_...\\app\\claude.exe
vs .../extensions/anthropic.claude-code-*/resources/native-binary/claude.exe).
Filtrar por nombre a secas mataria la app de escritorio tambien -- estos tests
verifican que el filtro real (el marcador de ruta) sea el que se manda a
PowerShell, no solo que "algo" se ejecute.
"""

import subprocess

import pytest

import claude_pet as cp


class FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout


def test_manda_el_marcador_del_cli_no_el_nombre_pelado(monkeypatch):
    """El filtro tiene que distinguir el CLI de la app de escritorio: ambos
    se llaman claude.exe, solo la ruta los diferencia."""
    visto = {}

    def fake_run(cmd, **kwargs):
        visto["cmd"] = cmd
        return FakeCompleted("2\n")

    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(cp.subprocess, "run", fake_run)

    n = cp._kill_claude_code_processes()

    assert n == 2
    script = visto["cmd"][-1]
    assert f"*{cp.CLAUDE_CODE_CLI_MARKER}*" in script
    assert "Stop-Process" in script and "-Force" in script
    # el -Filter por nombre tiene que estar ANTES del substring match, sino
    # el propio powershell.exe que corre esta consulta calza en su propio
    # filtro (su CommandLine repite, literal, el texto de este -Command) y
    # se mataria a si mismo a mitad de la ejecucion -- bug real, encontrado
    # corriendo el filtro contra procesos vivos el 25/8/2026.
    assert "-Filter \"Name = 'claude.exe'\"" in script
    assert script.index("-Filter") < script.index(cp.CLAUDE_CODE_CLI_MARKER)


def test_cero_procesos_es_cero_no_error(monkeypatch):
    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakeCompleted("0\n"))

    assert cp._kill_claude_code_processes() == 0


def test_stdout_vacio_es_cero(monkeypatch):
    """Un Measure-Object sobre una coleccion vacia a veces no imprime nada."""
    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakeCompleted(""))

    assert cp._kill_claude_code_processes() == 0


def test_timeout_devuelve_menos_uno_no_revienta(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=15)

    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(cp.subprocess, "run", fake_run)

    assert cp._kill_claude_code_processes() == -1


def test_powershell_ausente_devuelve_menos_uno(monkeypatch):
    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(
        cp.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))

    assert cp._kill_claude_code_processes() == -1


def test_stdout_no_numerico_devuelve_menos_uno(monkeypatch):
    monkeypatch.setattr(cp.sys, "platform", "win32")
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakeCompleted("no-es-un-numero"))

    assert cp._kill_claude_code_processes() == -1


def test_linux_todavia_no_esta_portado(monkeypatch):
    """Linux sigue sin rama: -1 sin intentar nada. darwin SI esta portado
    ahora, por eso salio de este parametrize (ver la suite de macOS abajo)."""
    llamado = []
    monkeypatch.setattr(cp.sys, "platform", "linux")
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: llamado.append(1))

    assert cp._kill_claude_code_processes() == -1
    assert llamado == []


# --------------------------------------------------------------- macOS
#
# Salida real de `ps -axo pid=,comm=` en la Mac donde se porto esto
# (25/8/2026). Los 8 primeros son sesiones de Claude Code que consumen la
# ventana de 5h; los 4 ultimos son la app de escritorio y su widget, que no
# tienen nada que ver y NO se deben tocar.
PS_REAL = """\
27251 /Users/fede/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/native-binary/claude
33997 /Users/fede/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/native-binary/claude
34887 /Users/fede/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/native-binary/claude
35204 /Users/fede/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/native-binary/claude
53586 /Users/fede/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/native-binary/claude
66333 /Users/fede/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/native-binary/claude
99118 /Users/fede/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/native-binary/claude
31663 claude
 4502 /Applications/Claude.app/Contents/MacOS/Claude
 4516 /Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app/Contents/MacOS/Claude Helper (Renderer)
 4529 /Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper
  924 /Applications/Opus Chat.app/Contents/PlugIns/ClaudeUsageWidgetExtension.appex/Contents/MacOS/ClaudeUsageWidgetExtension
"""

VSCODE_PIDS = [27251, 33997, 34887, 35204, 53586, 66333, 99118]
TERMINAL_PID = 31663
APP_PIDS = [4502, 4516, 4529, 924]


class FakePs:
    returncode = 0

    def __init__(self, stdout=PS_REAL):
        self.stdout = stdout


@pytest.fixture
def mac(monkeypatch):
    """darwin + ps mockeado + os.kill registrado en vez de ejecutado."""
    monkeypatch.setattr(cp.sys, "platform", "darwin")
    monkeypatch.setattr(cp.time, "sleep", lambda s: None)

    matados = []

    def fake_kill(pid, sig):
        matados.append((pid, sig))
        if sig == 0:
            # por defecto: el SIGTERM funciono, no queda nadie vivo
            raise ProcessLookupError()

    monkeypatch.setattr(cp.os, "kill", fake_kill)
    return matados


def test_mata_panel_de_vscode_y_terminal(mac, monkeypatch):
    """El caso que motivo el filtro en Mac: hay DOS formas de correr Claude
    Code y las dos queman la ventana de 5h. El panel de VS Code sale con
    ruta completa en `comm`, el CLI nativo sale como 'claude' pelado porque
    ps no resuelve el symlink de ~/.local/bin/claude. Un filtro por marcador
    de ruta (el de Windows) dejaria vivas todas las de terminal."""
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakePs())

    assert cp._kill_claude_code_processes() == 8

    terminados = [pid for pid, sig in mac if sig == cp.signal.SIGTERM]
    assert sorted(terminados) == sorted(VSCODE_PIDS + [TERMINAL_PID])


def test_no_toca_la_app_de_escritorio(mac, monkeypatch):
    """El equivalente Mac del test de claude.exe: que el filtro no se lleve
    puesta la app de escritorio de Claude. Aca alcanza con que sea
    case-sensitive ('Claude' != 'claude'), sin el desempate por ruta que
    Windows necesita."""
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakePs())

    cp._kill_claude_code_processes()

    tocados = {pid for pid, _ in mac}
    for pid in APP_PIDS:
        assert pid not in tocados


def test_sigkill_solo_a_los_que_sobreviven(monkeypatch):
    """SIGKILL es el -Force de Stop-Process, pero no se reparte a todos: solo
    al que sigue vivo despues del SIGTERM."""
    monkeypatch.setattr(cp.sys, "platform", "darwin")
    monkeypatch.setattr(cp.time, "sleep", lambda s: None)
    monkeypatch.setattr(cp.subprocess, "run",
                        lambda *a, **k: FakePs(f"{TERMINAL_PID} claude\n99118 x/claude\n"))

    matados = []
    duro = 99118  # este ignora el SIGTERM

    def fake_kill(pid, sig):
        matados.append((pid, sig))
        if sig == 0 and pid != duro:
            raise ProcessLookupError()

    monkeypatch.setattr(cp.os, "kill", fake_kill)

    assert cp._kill_claude_code_processes() == 2
    assert (duro, cp.signal.SIGKILL) in matados
    assert (TERMINAL_PID, cp.signal.SIGKILL) not in matados


def test_proceso_que_murio_solo_no_rompe(mac, monkeypatch):
    """Carrera normal: entre el ps y el kill la sesion se cerro sola."""
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakePs())
    monkeypatch.setattr(
        cp.os, "kill",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))

    assert cp._kill_claude_code_processes() == 8


def test_se_excluye_a_si_misma(monkeypatch):
    """Si la mascota apareciera en el listado, no debe suicidarse."""
    monkeypatch.setattr(cp.sys, "platform", "darwin")
    monkeypatch.setattr(cp.time, "sleep", lambda s: None)
    monkeypatch.setattr(cp.os, "getpid", lambda: 31663)
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakePs())
    monkeypatch.setattr(cp.os, "kill", lambda pid, sig: None)

    assert cp._kill_claude_code_processes() == 7
    assert TERMINAL_PID not in cp._pids_darwin()


def test_sin_sesiones_es_cero(mac, monkeypatch):
    """Sin sesiones de Claude Code no es un error: es 0, y _on_kill_result ya
    lo distingue del -1."""
    solo_app = "\n".join(l for l in PS_REAL.splitlines() if "Applications" in l)
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakePs(solo_app))

    assert cp._kill_claude_code_processes() == 0
    assert mac == []


def test_ps_que_falla_devuelve_menos_uno(mac, monkeypatch):
    fallado = FakePs("")
    fallado.returncode = 1
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: fallado)

    assert cp._kill_claude_code_processes() == -1


def test_ps_ausente_devuelve_menos_uno(mac, monkeypatch):
    monkeypatch.setattr(
        cp.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))

    assert cp._kill_claude_code_processes() == -1


def test_timeout_de_ps_devuelve_menos_uno(mac, monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ps", timeout=15)

    monkeypatch.setattr(cp.subprocess, "run", fake_run)

    assert cp._kill_claude_code_processes() == -1


def test_linea_basura_no_rompe(mac, monkeypatch):
    """ps puede traer encabezados o lineas raras: se ignoran, no explotan."""
    monkeypatch.setattr(
        cp.subprocess, "run",
        lambda *a, **k: FakePs(f"PID COMM\n\n  \nxyz claude\n{TERMINAL_PID} claude\n"))

    assert cp._kill_claude_code_processes() == 1
