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


@pytest.mark.parametrize("plataforma", ["darwin", "linux"])
def test_no_windows_no_intenta_nada(monkeypatch, plataforma):
    llamado = []
    monkeypatch.setattr(cp.sys, "platform", plataforma)
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: llamado.append(1))

    assert cp._kill_claude_code_processes() == -1
    assert llamado == []
