# Portar a macOS (y notas de Linux) — análisis, no implementación

Este documento existe porque **Claude Pet se desarrolló y se probó enteramente
en Windows**, y hoy tiene piezas centrales (sonido, la pantalla completa de
alarma, y sobre todo el corte automático de sesiones) atadas a APIs de
Windows. Nada de esto se portó todavía — es un mapa de qué tocaría cambiar si
alguien (yo mismo, en mi Mac de trabajo) quisiera correrlo ahí. No se probó
nada de lo que sigue contra un Mac real: son inferencias sobre PySide6, la
librería estándar de Python, y cómo está estructurado el resto del código, no
verificaciones en vivo como las que respaldan el resto de este repo.

## Resumen: qué anda tal cual, qué no anda, qué falta escribir

| Pieza | En macOS | Por qué |
|---|---|---|
| `claude_pet_collector.py` | **Anda tal cual** | Solo stdlib (`json`, `os`, `sys`, `time`, `pathlib`). El fix de `sys.stdout.reconfigure` es un no-op inofensivo fuera de Windows. |
| `claude_pet_usage.py` (poller) | **Anda tal cual, asumiendo** | Solo `urllib`+stdlib. Asume que `~/.claude/.credentials.json` vive en la misma ruta relativa en Mac — no verificado. |
| `claude_pet_slack.py` + `slack_setup.py` | **Anda tal cual** | Solo `urllib`. Ya está desconectado de la mascota (ver README sección 3), asi que ni siquiera hace falta para correrla. |
| Overlay, colores, barra, tray icon | **Debería andar via Qt** | `QSystemTrayIcon`, `QPainter`, `QSvgRenderer` son todos cross-platform. No probado en un Mac real. |
| Sonido (`_play_alert`) | **Degrada, no rompe** | Cae al beep genérico de Qt (`QApplication.beep()`) en vez de los tonos propios. Pierde el diseño (timbre de 2 notas / sirena) pero no falla. |
| Toast del SO | **Debería andar, permisos distintos** | `QSystemTrayIcon.showMessage()` es cross-platform, pero el modelo de permisos de macOS es por-app (primer uso pide permiso), no un toggle global como el de Windows. Sin código de diagnóstico para Mac (ver abajo). |
| Pantalla completa (`AlertScreen`) | **Debería andar, sin verificar** | `QApplication.primaryScreen()` + `showFullScreen()` son cross-platform. Sin probar contra Spaces/Mission Control/notch. |
| **Corte automático** (`_kill_claude_code_processes`) | **NO ANDA** | 100% Windows: PowerShell + WMI. Sin fallback, sin puerto. En Mac hoy esto es un no-op silencioso (`sys.platform != "win32"` devuelve `-1`). |
| Instancia única (`QSharedMemory`) | **Riesgo distinto** | Cross-platform en la API, pero el comportamiento ante un crash difiere — ver mas abajo. |
| Autostart | **Ya documentado** | El README ya trae la receta de `launchd` para macOS. No hace falta escribir nada nuevo. |

## Por módulo

### `claude_pet.py` — el núcleo

**`winsound` (líneas ~47-51).** Exclusivo de Windows; el import ya falla
gracioso (`HAS_WINSOUND = False` en cualquier otro `sys.platform`) y
`_play_alert()` ya tiene el fallback escrito: repite `QApplication.beep()`
en vez de tocar `ALERT_TONES`. **No hace falta ningún cambio para que ande**,
pero se pierde el diseño de sonido (timbre de dos notas para aviso, sirena
para alarma) — se escucha el beep genérico del sistema, sin distinguir aviso
de alarma más que por la cantidad de beeps. Para recuperar tonos propios en
Mac: `afplay` sobre un `.aiff`/`.wav` generado al vuelo (no hay equivalente
directo a `winsound.Beep(freq, ms)` en la stdlib de macOS), o `AppKit.NSSound`
via PyObjC — una dependencia nueva que hoy el proyecto no tiene.

**`_kill_claude_code_processes()` (el corte automático) — esto es lo que
realmente falta escribir.** Hoy es PowerShell + `Get-CimInstance Win32_Process`
+ `Stop-Process -Force`, sin ninguna rama para otro SO. Para portarlo:

1. **Encontrar el proceso.** En Mac, el equivalente seria `pgrep -f` o iterar
   `ps aux` buscando el patrón de ruta del binario nativo de la extensión de
   VS Code. La ruta en Windows es
   `.../extensions/anthropic.claude-code-*/resources/native-binary/claude.exe`;
   en Mac casi seguro es la misma estructura sin el `.exe`
   (`.../native-binary/claude`), pero **no está verificado** — habria que
   inspeccionar `~/.vscode/extensions/anthropic.claude-code-*/` en un Mac real
   antes de escribir el filtro.
2. **El problema del nombre ambiguo, ¿existe en Mac?** En Windows, `claude.exe`
   lo comparten el CLI de Claude Code y la app de escritorio de Claude (Electron,
   ~9 procesos). En macOS, la app de escritorio es un `.app` bundle
   (`/Applications/Claude.app/Contents/MacOS/Claude`, casi seguro, con su
   propio `CFBundleIdentifier`) — el nombre del proceso probablemente sea
   distinto del binario CLI (`claude`), lo que significaria que la ambigüedad
   de Windows **no se repite** en Mac. Pero esto es una suposición basada en
   cómo empaquetan Electron normalmente en Mac, no algo confirmado contra el
   `Info.plist` real de la app.
3. **Matar el proceso.** `os.kill(pid, signal.SIGTERM)` (o `SIGKILL` si hace
   falta forzar, equivalente a `-Force`) reemplaza a `Stop-Process`. Mucho más
   simple que el WMI de Windows — sin el problema de "la propia consulta se
   automatchea" que hubo que resolver en Windows, porque `pgrep`/`ps` no
   inyectan su propio patrón de búsqueda en su propia línea de comandos de la
   misma manera que `Get-CimInstance ... -Command <script con el patron>` lo
   hacía.
4. **`sys.platform`.** Hoy `_kill_claude_code_processes()` corta en seco con
   `if sys.platform != "win32": return -1`. Un puerto a Mac necesita una rama
   `elif sys.platform == "darwin":` con la implementación de arriba, y
   `tests/test_kill.py` ya tiene los tests parametrizados por plataforma
   (`test_no_windows_no_intenta_nada`) — esos tests HABRIA que reescribirlos
   para "darwin" en vez de asumir que ahí no se hace nada.

**`AlertScreen` (pantalla completa).** Usa solo `QApplication.primaryScreen()`,
`showFullScreen()`, `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint` — todo
API de Qt, no de Windows. Debería andar sin cambios de código. Lo que no está
probado: cómo se comporta `WindowStaysOnTopHint` contra Mission Control /
Spaces (¿la ventana se ve si cambiás de Space?), y si el fullscreen tapa la
barra de menú de macOS o el notch en modelos que lo tienen.

**El toast (`tray.showMessage()`).** También es Qt puro, pero el modelo de
permisos de macOS es otro: cada app pide permiso de notificaciones la primera
vez (no hay un toggle global equivalente al `ToastEnabled` de Windows). El
diagnóstico que documenta [CLAUDE.md](CLAUDE.md) (`Get-ItemProperty
HKCU:\...\PushNotifications`) es específico de Windows; en Mac el chequeo
seria via Configuración del Sistema → Notificaciones → Claude Pet, o
programáticamente con `UNUserNotificationCenter` (PyObjC), que hoy no es una
dependencia del proyecto.

**`QSharedMemory` (instancia única).** La API es cross-platform, pero el
comentario en el código documenta explícitamente el supuesto de Windows: *"En
Windows el bloque de memoria lo libera el SO al morir el proceso, así que un
crash no deja un lock huérfano."* En sistemas POSIX (Mac y Linux), los
segmentos de memoria compartida históricamente pueden sobrevivir a un crash
sin liberarse solos — es un gotcha conocido de `QSharedMemory` en Unix. Si se
porta, hay que probar explícitamente qué pasa si la mascota crashea en Mac: si
el guard queda "tomado" para siempre, un reinicio legítimo nunca podría volver
a levantar la mascota sin borrar el segmento a mano.

### `claude_pet_collector.py`, `claude_pet_usage.py`, `claude_pet_slack.py`, `slack_setup.py`

Ningún cambio de código necesario, con una salvedad: `claude_pet_usage.py`
asume `~/.claude/.credentials.json` para el token OAuth. Es la misma ruta que
usa Claude Code en Windows; probablemente sea igual en Mac (Claude Code guarda
su config bajo `~/.claude/` en las plataformas que soporta), pero esto no se
confirmó contra una instalación de Mac real.

### Autostart

El README ya trae la receta de macOS (`launchd`,
`~/Library/LaunchAgents/claude-pet.plist`, `RunAtLoad`) — no hace falta
escribir nada nuevo ahí, esa parte ya se pensó multi-plataforma desde el
principio.

### `pythonw.exe` / el shim de `claude`

Toda la sección de "Trampas del entorno" en [CLAUDE.md](CLAUDE.md) (el stub de
Python de Microsoft Store, el shim `~/bin/claude`) es específica de **esta
máquina Windows**, no del proyecto — no aplica a Mac y no hace falta portarla.
En Mac, `python3` del sistema (o de Homebrew) alcanza; no existe el problema
del stub de la Store.

## Qué haría falta para un port real (orden sugerido)

1. Confirmar en un Mac real la ruta del binario nativo de la extensión de
   Claude Code (`~/.vscode/extensions/anthropic.claude-code-*/resources/
   native-binary/`) y si la app de escritorio de Claude comparte nombre de
   proceso con el CLI (el problema que motivó `CLAUDE_CODE_CLI_MARKER` en
   Windows). Esto define si `_kill_claude_code_processes` en Mac necesita el
   mismo cuidado de filtrado o puede ser más simple.
2. Escribir la rama `darwin` de `_kill_claude_code_processes` (`pgrep`/`ps` +
   `os.kill`), actualizando `tests/test_kill.py` para cubrirla en vez de solo
   afirmar que en no-Windows no se hace nada.
3. Probar `AlertScreen` y el toast contra un Mac real: primer plano sobre
   Spaces, comportamiento del permiso de notificaciones.
4. Probar el crash-recovery de `QSharedMemory` en Mac antes de confiar en el
   mismo comentario que documenta el comportamiento de Windows.
5. Sonido: decidir si vale la pena el tono propio (via `afplay` o PyObjC) o si
   el fallback a `QApplication.beep()` alcanza — es una degradación cosmética,
   no un bloqueante.

## Linux, de paso

No fue pedido, pero la mayoría de lo de arriba aplica igual: el corte
automático necesitaría la misma rama `pgrep`/`os.kill` que macOS (con su
propia verificación de rutas — la extensión de VS Code en Linux probablemente
viva bajo `~/.vscode/extensions/` también), el toast pasaría por
`QSystemTrayIcon` con el backend nativo que exponga el entorno de escritorio
(GNOME/KDE via D-Bus, con sus propios permisos), y `QSharedMemory` comparte el
mismo gotcha POSIX que macOS. El README ya documenta autostart para Linux
(`.desktop` en `~/.config/autostart/`).
