# Trabajar en este repo

Claude Pet: overlay PySide6 always-on-top con el % de las ventanas de uso de
Claude Code — avisos (toast + tono), alarma con pantalla completa, y corte
automático que mata las sesiones de Claude Code antes del límite duro. Para
operar la mascota, ver [README.md](README.md); esto es lo que necesitás para
tocarle el código.

## Trampas del entorno

Esta seccion tiene dos mitades: la maquina Windows donde nacio el proyecto y
la Mac donde se porto el 25/8/2026. Ninguna aplica a la otra.

### macOS (portado el 25/8/2026, macOS 26.6.2 arm64)

**El `python3` del sistema es 3.9.6 de CommandLineTools.** No hay Homebrew. Se
usa un venv aparte, con `uv`, que ya estaba instalado:

```
~/.claude/pet/.venv/bin/python        # 3.13.15, con PySide6 6.11.2 y pytest
uv venv --python 3.13 ~/.claude/pet/.venv
uv pip install --python ~/.claude/pet/.venv PySide6 pytest
```

El venv vive DENTRO de `~/.claude/pet/` a proposito: el plist de `launchd`
apunta ahi y no depende de ningun PATH.

**`~/.claude/.credentials.json` no existe en Mac.** El token esta en el
Keychain (`Claude Code-credentials`). Si `_creds()` empieza a fallar, el
diagnostico es:

```bash
security find-generic-password -s "Claude Code-credentials"   # sin -w: metadata, no el secreto
```

Con `-w` imprime el JSON del token — util para debuggear, pero es un secreto:
no lo pegues en logs ni en el repo. Si aparece el dialogo de autorizacion,
"Permitir siempre", o bajo `launchd` no hay nadie que lo conteste.

**El diagnostico de notificaciones es otro.** El `Get-ItemProperty
HKCU:\...\PushNotifications` de mas abajo es de Windows. En Mac, si no llega
un aviso:

```bash
osascript -e 'display notification "test" with title "Claude Code"'; echo $?
```

Exit 0 y sin notificacion visible = falta el permiso en Ajustes del Sistema ->
Notificaciones. Y ojo: `tray.showMessage()` NO es una via valida de
diagnostico en Mac, porque no funciona nunca (ver PORTING.md).

**Un señuelo para probar el corte no puede ser un `cp` de un binario del
sistema.** Copiar `/bin/sleep` a un archivo llamado `claude` rompe su firma de
codigo y macOS lo mata al arrancar — el proceso muere antes de que `ps` lo
vea, y parece que el filtro fallo. Lo que si funciona es un hardlink al python
de `uv` con `PYTHONHOME` apuntado a su prefix:

```bash
PYHOME=~/.local/share/uv/python/cpython-3.13.15-macos-aarch64-none
ln -f $PYHOME/bin/python3.13 /tmp/decoy/claude
PYTHONHOME=$PYHOME /tmp/decoy/claude -c "import time; time.sleep(300)" &
```

**Para auditar el corte sin matar nada** esta `_pids_darwin()`, separada de
`_kill_darwin()` justamente para eso:

```bash
~/.claude/pet/.venv/bin/python -c "import claude_pet; print(claude_pet._pids_darwin())"
```

Cuidado al probar el corte de verdad: en esta Mac el filtro incluye las
sesiones de terminal, o sea **la sesion desde la que estas trabajando**.

### Windows

**El `python` del PATH es el stub de Microsoft Store y no funciona.** Siempre:

```
C:/Users/Usuario/AppData/Local/Programs/Python/Python310/python.exe   # 3.10.6
C:/Users/Usuario/AppData/Local/Programs/Python/Python310/pythonw.exe  # sin consola
```

Ahí viven PySide6 6.11.2 y pytest. No es un venv: se instala con
`python.exe -m pip install`.

`claude` en el PATH es un shim (`~/bin/claude` bash, `~/bin/claude.cmd`) que
resuelve el `claude.exe` más nuevo de la extensión de VS Code sin fijar versión.

**El toast del tray (`tray.showMessage()`) puede fallar en silencio si Windows
tiene las notificaciones apagadas a nivel sistema — no es un bug de Qt ni de la
mascota.** `QSystemTrayIcon.supportsMessages()` da `True` y `showMessage()` no
tira excepción igual; Windows corta antes de intentar dibujar nada. Se
diagnostica con:

```powershell
Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications -Name ToastEnabled
```

`0` = todos los toasts del sistema apagados, para todas las apps, no solo esta.
Se arregla en Configuración → Sistema → Notificaciones → activar el toggle de
arriba (`Start-Process ms-settings:notifications` la abre directo). Verificado
en esta máquina el 25/8: estaba en 0, se prendió, y el toast pasó a llegar.

**El poller puede fallar con `PermissionError` por el antivirus, no por un bug
del código.** Visto en vivo el 25/8: `~/.claude/pet/poller_state.json` mostró
`"error": "PermissionError: [Errno 13] Permission denied:
'\\\\.\\avgMonFltProxy\\<hash>'"` — el driver de filtro de archivos de AVG
interceptando la escritura atómica (`tmp` + `os.replace()`) de `usage.json`.
49 fallos consecutivos, ventana quieta ~2.5h. **Efecto colateral serio:**
`usage.json` envejeció mas allá de `USAGE_FRESH` (900s), `read_sessions()` dejó
de confiar en el dato, `pct` cayó a `None`, y `_mood_for(None)` devuelve
`"calm"` — la mascota se veía **verde y tranquila** mientras en realidad no
tenía ningún dato, no porque el uso real fuera bajo. `AlertEngine.check()`
tampoco dispara nada con `pct is None`, así que ni las alertas ni el corte
automático podrían haber reaccionado en esa ventana ciega.

Se resolvió solo (transitorio): un `poll_once()` manual minutos después
escribió sin error, y reiniciar la mascota confirmó `consecutive_failures: 0`.
No se tocó la config de AVG a pedido del usuario — el poller ya es
autorecuperable (reintenta cada `MAX_BACKOFF` sin crashear nada), así que se
dejó así. Si vuelve a pasar seguido, la mitigación pendiente es una excepción
de AVG para `~/.claude/pet/`, no un cambio de código: el bug está en cómo el
antivirus intercepta la escritura, no en el patrón atómico en sí.

**Corolario para diagnóstico futuro:** un mood "calm" con `DATO VIEJO` en la
etiqueta no significa que el uso este bajo — significa que la mascota no tiene
dato confiable. Mirar el tooltip (pasa el mouse) o `poller_state.json`
directo antes de asumir que "verde" es "tranquilo".

## Tests

```bash
C:/Users/Usuario/AppData/Local/Programs/Python/Python310/python.exe -m pytest
```

`pytest.ini` pone `pythonpath = .` porque los módulos viven en la raíz y los
tests en `tests/`.

**Los tests redirigen las rutas con `monkeypatch`, no con `HOME`.** Los tres
módulos resuelven `PET_DIR = Path.home() / ".claude" / "pet"` como constante
evaluada en el import: para cuando corre un test ya está congelada. El fixture
`pet` en `tests/conftest.py` reasigna los atributos de módulo contra `tmp_path`,
incluidos `USAGE_PATH` y `STATE_PATH`, que `claude_pet` importó **por nombre**
desde `claude_pet_usage` y por lo tanto son referencias propias suyas.

Ningún test toca `~/.claude/pet/` real ni pega contra la API.

## Las dos copias

El código vive en dos lugares y se mantienen a mano:

```
d:\OneDrive\Documentos\CLAUDE PET\     ← se edita acá   (Windows)
~/claude-pet/                           ← se edita acá   (Mac, clon de git)
~/.claude/pet/                          ← es lo que corre (ambas)
```

La suite de pytest es la red de seguridad más rápida, y la copia instalada
funciona como punto de retorno. De ahí la regla:

> Nada se copia a `~/.claude/pet/` hasta que `pytest` esté verde.

Deploy y verificación:

```bash
MODS="claude_pet.py claude_pet_collector.py claude_pet_usage.py"
cp $MODS ~/.claude/pet/
for f in $MODS; do diff -q "$f" ~/.claude/pet/"$f"; done   # sin salida = sincronizadas
```

La mascota carga los módulos al arrancar, así que un cambio de comportamiento
necesita reinicio:

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
  Where-Object { $_.CommandLine -like "*claude_pet.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Process "...\pythonw.exe" -ArgumentList "C:\Users\Usuario\.claude\pet\claude_pet.py" `
  -WorkingDirectory "C:\Users\Usuario\.claude\pet" -WindowStyle Hidden
```

Hay guard de instancia única (`QSharedMemory`), así que una segunda instancia
muere sola en vez de duplicar beeps.

## Dos fuentes de datos, y cuál aplica

| Fuente | Cadencia | Cuándo |
|---|---|---|
| `claude_pet_usage.py` (poller sobre `/api/oauth/usage`) | 140 s | siempre |
| `claude_pet_collector.py` (statusLine) | 10 s + cada tool call | **solo en la TUI** |

**El statusLine no corre en el panel de VS Code** — el webview no tiene dónde
ejecutarlo. Si trabajás desde el panel, el poller es la única fuente. El
collector no se borra: el endpoint es interno y sin documentar, y es lo único
que aporta contexto% y costo por sesión.

`read_sessions()` arbitra quedándose con el dato más nuevo de las dos.

## No bajar el intervalo de poll

El endpoint permite **6 requests por ventana deslizante de ~7-11 minutos**
(medido el 25/8/2026 en una hora limpia; ver los comentarios de
`MIN_POLL_SECONDS` y `DEFAULT_POLL_SECONDS`). Todo intento con ≤ 6 requests
previas en la ventana devolvió 200; todo intento con 7 devolvió 429, sin
excepciones en 36 muestras.

- **110 s** es el piso aritmético: `P > W/6 = 652/6 = 108.7`. Por debajo, el
  poller solo ya se autogenera el 429 — a 70 s el ciclo medido fue 6 éxitos, un
  429 y 300 s de espera, repitiéndose cada 722 s con precisión de reloj.
- **140 s** es el default: deja un lugar libre en la ventana para una request
  ajena. Abrir el panel de Usage de VS Code hace una consulta, y **cada reinicio
  de la mascota dispara un poll inmediato**.

Bajar el intervalo no trae más datos. El endpoint entrega 6 lecturas cada ~12
minutos pase lo que pase: a 70 s son 31 por hora con 371 s de ceguera máxima, a
140 s son 26 por hora con 140 s. Sólo cambia si llegan parejas o en ráfagas con
un apagón detrás.

Un 429 cuesta 300 s. A veces trae `Retry-After` y a veces no —en la hora
monitoreada, ninguno de los 5 lo traía— así que el respaldo fijo de 300 s es el
camino normal, no un caso raro.

## Slack: implementado, verificado, y desactivado a propósito (25/8)

Se armó un bot de Slack (DM al celular vía `chat.postMessage`) y se verificó
end-to-end contra un workspace real: workspace "test fede", bot `claude_pet`
(`U0BSSA6C3SQ`), destino `U0BS5RU1VRV` → DM `D0BSQHP5TFT`. Se confirmaron dos
rutas en el DM: el test de `slack_setup.py` (transporte), y **una alerta
real** — la ventana cruzó 93% y la mascota la mandó sola, probando la cadena
completa `tick → AlertEngine → _fire → notify_slack → hilo →
chat.postMessage`. `<!here>` se descartaba correctamente en DM.

Se sacó después: lo que el usuario pedía en realidad era notificación en la
MISMA máquina (el toast de Windows ya lo cubre, ver sección de abajo), y para
un Slack corporativo la fricción de admin + visibilidad de la app en el
workspace no vale el beneficio. Detalle completo y cómo reactivarlo: sección 3
del README. `claude_pet_slack.py` sigue en el repo, funcional, con
`tests/test_slack.py` cubriéndolo — solo que `claude_pet.py` ya no lo importa.
Se limpiaron `slack_bot_token` (el `xoxb-` real) y el resto de las claves
`slack_*` de `~/.claude/pet/config.json`; la app de Slack sigue instalada en el
workspace "test fede" por si se retoma (no se desinstaló).

**Para disparar un umbral a mano contra la mascota viva** (util para cualquier
canal de alerta, no solo Slack): la sesión sintética necesita `rl_ts = now`.
`read_sessions()` se queda con el dato **más nuevo**, no siempre con el del
poller, así que con un `rl_ts` viejo el archivo no gana, no dispara nada y
parece que el código está roto. Y usá un `resets_at` lejos del real (más que
`WINDOW_MATCH`, 600 s): si consumís la clave `five_hour:96` de la ventana de
verdad, el cruce real de 96% no avisa después. Restaurá `fired.json` desde
backup y reiniciá al terminar, para soltar las claves de prueba que quedan en
memoria del proceso.

## Sonido: tonos propios, no el beep genérico

`_play_alert()` en `claude_pet.py` evita `QApplication.beep()` en las dos
plataformas soportadas, porque con el beep genérico aviso y alarma **suenan
igual** y solo se distinguen contando beeps — que es justamente la
información que se necesita a las apuradas.

- **Windows**: `winsound.Beep()` con dos patrones (`ALERT_TONES`), dos notas
  subiendo para aviso, sirena alternada x3 para alarma.
- **macOS** (25/8/2026): no hay equivalente a `winsound.Beep(freq, ms)` en la
  stdlib, así que va `afplay` sobre los `.aiff` del sistema (`MAC_SOUNDS`):
  Tink+Glass para aviso, Funk↔Basso x2 para alarma. Sin dependencias nuevas.
  **Elegir por timbre, no por volumen**: el primer intento fue Ping+Glass /
  Sosumi x3 y hubo que cambiarlo porque los tres son campanitas agudas y no se
  distinguía aviso de alarma sin mirar la pantalla.
- **Linux y último recurso**: el beep de Qt repetido, mismo patrón de conteo
  que había antes de este cambio.

Corre en un hilo daemon porque **las dos** APIs son sincrónicas
(`winsound.Beep()` y `afplay` por igual) y bloquearían el tick de 1s de Qt.

## La animación se pausa por debajo del 50% (26/8)

`_sync_anim()` apaga el timer de 50ms cuando `mood == "calm"`, y lo prende de
vuelta en cualquier otro estado o durante un flash. Se llama desde `tick()` y
desde `_fire()` (este último para que el flash arranque al instante y no
espere hasta un segundo).

**El motivo principal no es la batería, es el jitter.** `_animate` solo llama
a `update()` en alarm/credit/flash, así que en calm el único repintado era el
del tick de 1s — y para entonces `pulse` ya había avanzado 20 pasos
(`0.12 * 20 = 2.4 rad`, ~137°). O sea que el logo pegaba un saltito de tamaño
por segundo, al azar, en vez de respirar. Al pausar se fija `pulse = 0.0`
(`sin(0) = 0` → `breathe = 1.0`, el tamaño de reposo) y se hace un último
`update()`.

**El ahorro de CPU es real pero chico**, medido sobre 20s aislando el timer:

| | corriendo | pausado |
|---|---|---|
| despertadas | 407 | 0 |
| CPU | 0.202s | 0.148s (−27%) |
| context switches | 433 | 227 (−48%) |
| % de un core | 0.99% | 0.75% |

O sea ~0.24 puntos de un core. No se nota en la batería; no vender esto como
si se notara.

**Por qué solo calm y no también watch/warn**, que tampoco dibujan: decisión
explícita del 26/8 — del 50% para arriba se prefiere no tocar nada. El
`_anim_en_pausa()` está escrito para que ampliar el criterio sea cambiar una
condición, si algún día se quiere.

## AlertScreen: pantalla completa en alarmas (25/8), verificada en vivo

Ante el pedido explícito de un canal que no dependa de ver el toast ni de
escuchar el sonido — notificaciones muteadas, sin auriculares, mirando otro de
los varios monitores — se agregó `AlertScreen` (`claude_pet.py`, antes de
`Pet`). Se dispara solo en `level == "alarm"` (en ese momento 96/98%; hoy es
solo 90% — ver el rediseño más abajo), en
`QApplication.primaryScreen()`, que es el mismo monitor que Windows marca
como principal en Configuración → Sistema → Pantalla — no necesariamente el
monitor donde vive el widget de la mascota.

**Deliberadamente no pasa por el corte de `self.cfg.get("muted")` en
`_fire()`** — esa es la clave de diseño: existe precisamente para el momento
en que ese corte ya silenció todo lo demás. Kill-switch propio y separado del
mute: `fullscreen_alert_enabled` (default `true`).

`AlertScreen` NO usa `Qt.Tool` como `Pet` (que lo usa para nunca robar foco):
acá se necesita foco de verdad, porque `keyPressEvent`/`mousePressEvent` son
el mecanismo de cierre. Se instancia una sola vez en `Pet.__init__` y se
reutiliza — importa cuando dos umbrales de alarma disparan en el mismo tick
(pasa en la práctica: una sesión que salta de golpe cruza varios umbrales
juntos — con los valores de esa fecha eran 90, 93, 96 y 98; ver más abajo el
rediseño del 25/8 tarde que los cambió a 25/50/75/85/90/95), la segunda
llamada a `show_alert()` solo actualiza el texto de la ventana ya abierta en
vez de apilar una encima de otra.

Verificado en vivo el 25/8 con el mismo patrón de sesión sintética que el
resto de las alertas (`rl_ts = now`, `resets_at` lejos de `WINDOW_MATCH`):
apareció en el monitor correcto, con el texto legible, y cerró bien con clic.

## Corte automático: mata procesos de verdad (25/8), verificado con señuelo

> **macOS (25/8/2026)**: todo lo de abajo describe la rama de Windows, que
> ahora vive en `_kill_win32()`. La rama de Mac es `_kill_darwin()` y usa un
> filtro **distinto por un motivo de fondo**: en Windows el nombre de proceso
> es ambiguo (`claude.exe` lo comparte la app de escritorio) y hay que
> desempatar por ruta; en Mac pasa lo contrario — el nombre alcanza
> (`Claude` != `claude`), pero la ruta NO sirve, porque el CLI nativo sale
> pelado en `ps`. Ver `PORTING.md`.

`_kill_claude_code_processes()` (`claude_pet.py`, cerca de `_play_alert`) mata
los procesos de Claude Code al cruzar `kill_threshold` (95% default,
`auto_kill_enabled` default `true`). Wireado en `tick()` como un namespace de
dedupe separado (`"five_hour_kill"`, vía `AlertEngine.check` reutilizado con
`warns=[]`), y disparado por `Pet._trigger_kill()` → hilo daemon →
`Pet.kill_result` signal → `_on_kill_result()` actualiza el `AlertScreen` ya
abierto. (El 26/8 se sumó un segundo disparador por la ventana semanal y la
elección de namespace se movió a `kill_events()` — ver más abajo.) A pedido explícito: **sin cuenta regresiva, sin cancelación** — el
usuario comparó las alternativas y prefirió corte inmediato una vez confirmado
que solo mata el proceso `claude.exe`, no VS Code ni la terminal.

**Dos bugs reales encontrados verificando esto en vivo, no en teoría:**

1. **`claude.exe` es un nombre de proceso ambiguo en esta máquina.** La app de
   escritorio de Claude (`WindowsApps\Claude_...\app\claude.exe`, Electron,
   ~9 procesos: crashpad-handler, gpu-process, utility, renderer x2, audio,
   video-capture) usa el MISMO nombre que el CLI de Claude Code
   (`.../extensions/anthropic.claude-code-*/resources/native-binary/
   claude.exe`). Matar por nombre pelado se hubiera llevado puesta la app de
   escritorio. Filtro real: `CLAUDE_CODE_CLI_MARKER =
   "native-binary\\claude.exe"`, contra `CommandLine`, no contra `Name`.
2. **El propio `powershell.exe` que ejecuta el filtro puede matchear su
   propio filtro.** El script pasado a `-Command` contiene, literal, el texto
   `native-binary\claude.exe` (porque ES el patrón del `Where-Object`) — y
   `CommandLine` de ese mismo `powershell.exe` incluye ese texto. Sin acotar
   antes por `-Filter "Name = 'claude.exe'"` en la consulta CIM, el script se
   mataría a sí mismo a mitad de ejecución. Encontrado con un dry-run manual
   (`Where-Object` sin `Stop-Process`) contra los procesos reales: devolvía el
   PID de `powershell.exe` junto con los 3 de Claude Code.

**Verificación end-to-end sin arriesgar sesiones reales.** Esta conversación
corre dentro de uno de los `claude.exe` reales — correr el kill real contra el
marcador real lo hubiera matado a mitad de la prueba, sin nadie para reportar
el resultado. Se probó con un **señuelo**: se copió `timeout.exe` a
`%TEMP%\claude_pet_kill_decoy\native-binary\claude.exe` (nombre real
`claude.exe`, para pasar el `-Filter` de `Name`), se lanzó con
`/t 600 /nobreak`, y se llamó a `_kill_claude_code_processes()` **sin
mockear** pero con `CLAUDE_CODE_CLI_MARKER` pisado a un prefijo único del
señuelo (`claude_pet_kill_decoy\native-binary\claude.exe`) — subprocess real,
PowerShell real, `Stop-Process -Force` real. Resultado: `1`, el señuelo murió,
las 3 sesiones reales (incluida esta) siguieron vivas. Aparte, el wiring de UI
(`_trigger_kill` → `AlertScreen` → `_on_kill_result`) se probó por separado con
`_kill_claude_code_processes` mockeada (sin tocar nada real), confirmado
visualmente por el usuario.

`tests/test_kill.py` cubre `_kill_claude_code_processes` con `subprocess.run`
mockeado — incluye un test que verifica explícitamente que el `-Filter` de
`Name` va ANTES del substring match, para no reintroducir el bug 2.

**Verificado además contra una sesión real** (no un señuelo), a pedido del
usuario: se identificó por eliminación qué PID correspondía a una ventana de
VS Code distinta de esta conversación (cada ventana tiene su propio
"extension host" — `Code.exe --type=utility --utility-sub-type=
node.mojom.NodeService` — y se rastreó cuál hijo `claude.exe` colgaba de
cuál), se confirmó con el usuario antes de tocar nada, y se mató ESE PID
puntual con `Stop-Process -Id <pid> -Force` (no la función de producción
completa, que hubiera matado las 3 sesiones vivas a la vez — acá solo una
debía morir). El panel de esa sesión mostró
`Claude Code process exited with code 4294967295` (0xFFFFFFFF sin signo = -1,
la salida típica de una terminación forzada) — confirmación visual del
usuario. Esta conversación y la otra sesión siguieron con vida. **Gotcha de
verificación (no del código):** `Get-CimInstance Win32_Process` mostró el PID
como "vivo" varios cientos de ms después de matarlo — caché de WMI. `Get-Process`
(no-WMI) sí reflejaba el estado real al instante. La función de producción no
tiene este problema porque cuenta sobre la colección `$p` ya capturada, nunca
re-consulta después de matar.

## La ventana semanal, escalera completa (26/8)

Antes la semanal era un par de umbrales de aviso colgados del mismo código que
la de 5h. Ahora tiene escalera propia, con corte, y **texto y color propios**:

| semanal | qué pasa | dónde |
|---|---|---|
| 85 / 95% | aviso: notif + sonido, **sin** pantalla completa | `seven_day_thresholds`, todos `warn` |
| 98% | pantallazo violeta + corte, **una vez** | `kill_events()` |
| 98–99%, c/60 min | pantallazo violeta, sin cortar | `seven_day_reminder()` |
| 100%+ | pantallazo violeta + corte **cada `usage_poll_seconds`** | `hard_kill_due()` |

Las tres funciones de decisión (`kill_events`, `seven_day_reminder`,
`hard_kill_due`) son de módulo, fuera de `Pet`, por la misma razón que
`_pids_darwin()`: es la única forma de auditar **cuándo** se corta sin levantar
Qt y sin matar nada. `tests/test_kill_windows.py` las cubre con 39 tests.

### Rojo es la de 5h, violeta es la semanal

`WINDOW_UI` es la única tabla que decide cómo se ve cada ventana:

```python
WINDOW_UI = {
    "five_hour": ("Claude Code · 5h",     "de la ventana de 5h",    "alarm"),
    "seven_day": ("Claude Code · semana", "de la ventana semanal",  "credit"),
}
```

El motivo no es estético. Los dos cortes se veían **idénticos** —mismo rojo,
mismo texto "uso de sesion N%", mismo título "Claude Code"— y no significan lo
mismo: la de 5h se destraba en horas, la semanal puede tardar 7 días. En la
bandeja de notificaciones el título es lo único que se lee de reojo, así que
ahí va el `· 5h` / `· semana`.

Efecto secundario: se arregló un texto que estaba mal desde antes. `_fire()`
decía `"uso de sesion {pct}%"` para las **dos** ventanas, así que un aviso del
95% semanal se anunciaba como si fuera de sesión.

`_fire()` ahora recibe la ventana como primer parámetro; `tick()` se la
antepone a lo que devuelve `AlertEngine.check()`, que quedó sin tocar.

### El corte duro no dedupea, y es la excepción

Todo lo demás pasa por `AlertEngine`, que dispara una vez por ventana. El corte
del 100% no: `hard_kill_due()` es una función de tiempo, no de umbral cruzado,
y se repite cada `usage_poll_seconds`. A pedido explícito — "por si se me
escapa algo".

Se ancla a `usage_poll_seconds` y no a un intervalo propio porque entre poll y
poll el `%` es el mismo número viejo: cortar más seguido no aportaría nada.

`tick()` llama a `kill_events()` **siempre**, aun cuando gane el corte duro, y
recién después elige. Saltearlo dejaría el umbral del 98% sin marcar y por lo
tanto pendiente para más tarde en la misma ventana.

### Firma del resultado

`Pet.kill_result` es `Signal(int, float, str, bool)`: n, pct, ventana, y si el
corte se repite. El `bool` cambia el mensaje final — "se cerraron 3 sesiones" a
secas deja creer que se puede volver a abrir, y arriba del 100% no.

`Pet.recordatorio_at` y `Pet.corte_duro_at` arrancan en `0.0` a propósito
(actúan en el primer tick si ya estás pasado) y viven en memoria, no en disco:
un reinicio re-avisando es correcto, y persistirlos sería un archivo más que
puede quedar viejo.

### Verificado en vivo, no solo en tests

Con `_kill_claude_code_processes` y `_play_alert` reemplazados por stubs (no
murió ningún proceso), la escalera entera en una corrida:

| semana | resultado |
|---|---|
| 85 | 🔔 `[Claude Code · semana]` aviso, sin pantalla |
| 95 | 🔔 aviso, sin pantalla, sin corte |
| 98 | 🟣 CORTANDO + CORTADO, 1 corte |
| 98, +1s | silencio |
| 98, +1h | 🟣 SEMANA AL LIMITE, **sin** cortar |
| 100 | 🟣 CORTANDO + CORTADO + "va a seguir cortando cada 140s" |
| 100, +1s | silencio |
| 100, +140s | 🟣 corta otra vez |
| 5h a 90 (control) | 🔴 pantallazo rojo con wording de 5h |

4 cortes en total, exactamente donde correspondía.

## Alertas y semáforo rediseñados (25/8, tarde)

Segundo pedido del usuario sobre el mismo mecanismo, ya con el corte
automático probado y funcionando. Dos cambios independientes:

**Calendario de alertas**, ahora seis escalones en vez de dos pares:

| uso | notif+sonido | pantalla completa | corte |
|---|---|---|---|
| 25 / 50 / 75 / 85% (`warn_thresholds`) | sí | no | no |
| 90% (`alarm_thresholds`, ahora un solo valor) | sí | sí | no |
| 95%+ (`kill_threshold`, sin cambios) | **no** | sí | sí |

`_trigger_kill()` perdió las llamadas a `_play_alert("alarm")` y
`tray.showMessage(...)` — a pedido explícito, 95%+ ya no suena ni notifica,
solo pantalla completa + kill. `seven_day_thresholds` quedó **sin tocar**
(`[85, 95]`) porque no se mencionó; asumido a propósito, no verificado con el
usuario.

**Semáforo de colores**, desacoplado de los umbrales de alerta. Antes
`_mood_for` leía `self.cfg["warn_thresholds"][0]` / `["alarm_thresholds"][0]`
directo — con 4 avisos en `warn_thresholds` eso ya no da 4 colores limpios.
Ahora usa una constante propia, `MOOD_BREAKPOINTS = (50, 75, 90)`, que vive
en el módulo, no en la config: verde/amarillo/naranja/rojo en < 50 / 50-74 /
75-89 / ≥90. `credit` (violeta, ≥99%) queda fuera de este semáforo a
propósito — es la señal de "estás por entrar en créditos", no un quinto
escalón de severidad normal, y el usuario no pidió tocarla.

`MOODS["calm"]` pasó de `#D97757` (naranja de marca — documentado
explícitamente en el código como decisión de diseño previa: "el estado normal
ES el color de marca") a `#2E9E56` (verde). Esa justificación anterior ya no
aplica; el pedido de esta sesión fue explícito por un semáforo clásico.

## Auditoría pre-release (25/8, noche)

Antes de preparar el repo para publicarlo, pasada completa por los 5 módulos,
los 5 docs de test y los dos `.md`. Lo que se encontró y arregló:

- **`used_percentage` sin normalizar en el collector** (bug real, no solo
  cosmético). El poller redondea `utilization` a int; el collector pasaba el
  crudo del statusLine tal cual. Un `56.999999999999` se DIBUJABA como "57%"
  (el f-string redondea al mostrar) pero el umbral 57 comparaba contra el
  numero crudo y no disparaba — la pantalla decia que ya paso, la alerta no.
  Fix: `normalize_window()` en `claude_pet_collector.py`, aplicado antes de
  `render()` y del payload (mismo dict normalizado en los dos lugares, para
  que no puedan divergir). `tests/test_collector.py` nuevo, 13 casos.
- **`_mood_for` ya no puede reventar con umbrales vacíos** — arreglado sin
  querer, como efecto secundario del rediseño del semáforo: antes indexaba
  `self.cfg["alarm_thresholds"][0]` sin guarda (un `[]` en config.json tiraba
  `IndexError` en cada tick); ahora usa la constante fija `MOOD_BREAKPOINTS`,
  que nunca esta vacia porque no vive en la config. Verificado con
  `grep -n "thresholds'\]\[0\]"` sobre todo el archivo: cero resultados.
- **Comentarios y docstrings con los umbrales viejos** (90/93/96/98) en el
  header del módulo, en `AlertScreen.__init__`, y en `DEFAULT_CONFIG` —
  sobrevivieron al rediseño de la tarde porque estaban en comentarios, no en
  código que un test pudiera atrapar. Actualizados a 25/50/75/85/90/95.
- **`tests/conftest.py` decía cubrir "los tres módulos" pero el fixture `pet`
  solo parcheaba dos** (`claude_pet`, `claude_pet_usage`) — `claude_pet_collector`
  quedaba afuera, así que `test_collector.py` tuvo que traer su propio fixture
  duplicado. Unificado: el fixture compartido ahora parchea los tres, el
  duplicado se borró.
- **Sección "Notificación nativa de Windows" duplicada** en este archivo, casi
  palabra por palabra contra la de "Trampas del entorno" — quedó de una
  edición donde el mismo hallazgo se escribió dos veces. Una sola copia ahora.
- **`README.md` decía "88 tests" en `test_slack.py`**; son 34 (88 era el total
  del repo en un momento intermedio, mal atribuido a un solo archivo).

Suite completa: **109 tests, 0 fallos.**

## Mascota invisible por un monitor desconectado (28/8)

Sintoma reportado: "no veo la mascota". El proceso estaba vivo (`pythonw.exe`
con `claude_pet.py`), el tray tambien — pero cero pixeles en pantalla.

Causa: `position.json` tenia `x: -264`, guardado cuando habia un monitor a la
izquierda del primario. Al quedar un solo monitor (`0..1920`), esa region del
escritorio virtual dejo de existir, y con 262px de ancho la mascota quedaba
enteramente afuera (`-264 + 262 = -2`). `_restore_position()` hacia `move()`
con la posicion guardada **sin validarla contra las pantallas actuales**.

**Un proceso vivo dibujado fuera de pantalla es indistinguible de uno que no
arranco** — y no se puede arreglar arrastrandola, porque no hay de donde
agarrarla. Antes de revisar el codigo, comparar `position.json` contra
`[System.Windows.Forms.Screen]::AllScreens`.

Fix: `clamp_to_screens()` (funcion pura de modulo, junto a `_play_alert`) mas
`Pet._screen_areas()` que traduce de `QScreen` a tuplas. Respeta una posicion
que ya cae adentro — incluida una mordiendo el borde a proposito, que es una
decision del usuario al arrastrar — y solo reubica cuando queda menos de
`MIN_VISIBLE` (40px) dentro de **alguna** pantalla. El rescate va a
`screens[0]`, por eso `_screen_areas()` pone la primaria al frente a mano:
`QApplication.screens()` no promete ningun orden.

La aritmetica se separo de Qt justamente para poder testearla:
`tests/test_position.py`, 12 casos, incluido el caso real de esta fecha.
`_screen_areas()` sigue sin cobertura, como todo lo demas que toca Qt.

Verificado en vivo: tras el reinicio, `GetWindowRect` devolvio
`L=0 T=40 R=262 B=192`, dentro del monitor y con `IsWindowVisible` en `True`.
Suite completa: **124 tests, 0 fallos.**

De paso, el poller estaba en `429` con `consecutive_failures: 4` y un
`Retry-After: 3600` que el codigo obedecio (cae dentro de `RETRY_AFTER_MAX`),
con `usage.json` de hacia 15.7h — o sea la mascota tampoco tenia dato
confiable. Se recupero solo con el reinicio, que dispara un poll inmediato:
`ok: true`, `consecutive_failures: 0`. Transitorio, como el caso de AVG.

## Poller: jitter al arrancar para separar el poll inmediato del boot (30/8)

Reportado como "cada vez que reinicio la compu no arranca bien la mascota".
No era un bug del arranque en si: `poller_state.json` tenia
`next_retry_in: 3600` — un 429 con `Retry-After: 3600` (contra el ~300s
habitual medido el 25/8), la mascota sin dato fresco por una hora entera
justo al reiniciar, que es cuando mas se la mira.

Cada reinicio de la mascota dispara un poll inmediato (a proposito, para
tener dato fresco al toque) — pero en un reboot de Windows todo lo demas que
habla con el mismo endpoint (VS Code, otras sesiones de Claude Code, el
panel de Usage) tambien despierta en el mismo instante. Sin jitter, la
mascota es sistematicamente una de las requests que revienta el budget de
6/ventana justo cuando el usuario la esta mirando recien arrancada.

Fix: `STARTUP_JITTER_MAX = 30` + `startup_jitter()` en `claude_pet_usage.py`
— `_loop()` espera un random de 0-30s antes del primer poll del proceso, en
vez de dispararlo en el instante exacto del boot. Sigue siendo "dato fresco
al reiniciar" (llega dentro del primer medio minuto, no en el proximo ciclo
de 140s), pero ya no coincide milimetricamente con todo lo demas que
despierta en ese instante. No es garantia absoluta contra el 429 al boot,
baja la chance de colision, no la elimina.

Verificado en vivo: mate el proceso que seguia bloqueado hasta las 19:25:37
por el `Retry-After` viejo, y arranque uno nuevo a las 19:27:44. Con el
jitter aplicado, polleo solo ~23s despues y salio bien al primer intento —
`poller_state.json` paso a `ok: true, consecutive_failures: 0`.

Tests: 2 nuevos en `tests/test_poller_backoff.py` (bounds del jitter, y que
`_loop` lo espera antes de tocar la red). Suite completa: **126 tests, 0
fallos.**

## Mascota invisible por perder el topmost real, no el flag de Qt (31/8)

Segundo "no veo la mascota", causa distinta a la del 28/8: el proceso estaba
vivo, `position.json` bien (dentro de pantalla), y Windows reportaba la
ventana `visible=True` — pero el z-order real (recorrido con
`GetWindow`/`GW_HWNDNEXT`) la tenia en el puesto #11, detras de Chrome, VS
Code y Excel, PESE a que su `WS_EX_TOPMOST` seguia marcado (confirmado con
`GetWindowLong`). El bit se pone una vez al mostrar la ventana pero no
garantiza la posicion real en el z-order; algun evento (otra app pidiendo
topmost, un cambio de pantalla, el snipping tool activandose) la entierra
igual. `self.raise_()` de Qt no alcanza: en Windows equivale a `HWND_TOP`,
que solo reordena dentro de la banda en la que la ventana YA esta — si se
cayo a la banda normal, se queda ahi.

Diagnostico con una captura de pantalla y hit-test (`WindowFromPoint` +
`GetAncestor`) sobre el punto exacto donde vive la mascota: confirmo que
Chrome, no topmost, era el dueño real de esos pixeles.

Fix: `_reassert_topmost()` en `claude_pet.py`, llamada desde `tick()` cada
1s — `SetWindowPos(HWND_TOPMOST)` por ctypes, asi el drift dura como maximo
un tick en vez de hasta el proximo reinicio manual.

**Bug real en el primer intento del fix, atajado por un test contra la API
de verdad, no mockeada:** `SetWindowPos` de ctypes sin `argtypes`
declarados marshalea el `HWND_TOPMOST` (-1) como `int` de 32 bits en vez de
puntero de 64 — la llamada no tira excepcion (`SetWindowPos` devuelve
`BOOL` igual) pero no mueve nada. Los tests que mockean `ctypes.windll`
entero pasaban igual porque nunca ejercitan el marshaling real; el que crea
una ventana nativa de verdad (clase `STATIC`, sin `RegisterClass`) lo
atrapo. Fix real: declarar `argtypes`/`restype` sobre `SetWindowPos` al
importar el modulo.

Ese mismo test fue flaky al principio (1 de 3 corridas fallaba) contra una
ventana invisible y sin bombeo de mensajes — se corrigio con `WS_VISIBLE` +
un pump minimo de `PeekMessage`/`DispatchMessage`, que se ajusta a la
condicion real (la mascota siempre es visible y tiene el loop de Qt
bombeando mensajes via `app.exec()`), no la elude.

Verificado en vivo contra el proceso real: forzando el drift a mano
(`SetWindowPos(HWND_NOTOPMOST)`) el `exStyle` paso de `0x80080` a `0x80088`
solo, dentro de un tick de 2s, y el hit-test final confirmo que los pixeles
donde vive la mascota son de verdad suyos.

Tests: 4 nuevos en `tests/test_reassert_topmost.py` (3 mockeados para la
logica + no-op fuera de Windows, 1 contra una ventana nativa real, la que
atrapo el bug de arriba). Suite completa: **130 tests, 0 fallos.**

## Cobertura de tests: lo que NO está cubierto

Todo lo que es Python puro tiene tests (`AlertEngine`, `read_sessions`, el
poller, el collector, `claude_pet_slack`, `_kill_claude_code_processes`). Lo
que es Qt (`Pet`, `AlertScreen`, `tick()`, `_fire()`, `_trigger_kill()`, todo
el pintado) **no tiene un solo test automatizado** — no hay `pytest-qt` ni
plugin de plataforma offscreen instalado. Cada pieza de UI se verificó a mano,
en vivo, contra la mascota corriendo (ver las secciones fechadas de arriba),
no con una suite que corra en CI. Si esto se publica y alguien quiere aportar,
esta es la brecha más grande: agregar `pytest-qt` + `QT_QPA_PLATFORM=offscreen`
y cubrir al menos `AlertEngine` end-to-end con un `Pet` real, no solo con la
clase aislada.

## Backups sueltos

De cuando se tocaron settings ajenos: `~/.claude/settings.json.bak-pet`,
`%APPDATA%\Code\User\settings.json.bak-pet` y `.bak-pet-poller`. Ninguno hace
falta ya; se pueden borrar cuando quieras.
