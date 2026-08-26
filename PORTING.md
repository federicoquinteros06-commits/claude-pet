# Portar a macOS — hecho y verificado (y notas de Linux)

Este documento existia como **analisis previo**: Claude Pet se desarrollo y
probo enteramente en Windows, y lo que seguia era un mapa de que habria que
cambiar, explicitamente sin verificar ("son inferencias sobre PySide6, la
libreria estandar de Python, y como esta estructurado el resto del codigo, no
verificaciones en vivo").

**El port a macOS se hizo el 25/8/2026** contra un Mac real (macOS 26.6.2,
arm64, Apple Silicon). Este documento pasa a ser el registro de que resulto
cierto y que no. Linux sigue sin portar.

## Lo primero: donde el analisis previo se equivoco

Dos de los cuatro supuestos centrales no sobrevivieron al contacto con la
maquina, y el que fallo peor era justamente el que estaba marcado como
"deberia andar tal cual".

### ❌ El poller NO andaba tal cual — este era el bloqueante real

El analisis decia: *"`claude_pet_usage.py` (poller): **Anda tal cual,
asumiendo**. Asume que `~/.claude/.credentials.json` vive en la misma ruta
relativa en Mac — no verificado."*

En Mac ese archivo **no existe**. Claude Code guarda el mismo JSON en el
Keychain del login (servicio `Claude Code-credentials`). Verificado listando
`~/.claude/`: 20 entradas, ninguna es `.credentials.json`.

Es el fallo mas caro de los dos, porque es **silencioso**: `_token()` tira
`FileNotFoundError` en cada poll, se lo traga el `try/except` del poller,
`usage.json` nunca se escribe, y la mascota muestra `--`. No parece un error:
parece que todavia no llegaste a ningun umbral. Ninguna alerta, ninguna
alarma, ningun corte automatico — nunca. Todo el resto del port (matar
procesos, sonar, notificar) cuelga de que este dato exista.

Fix: `_creds()` en `claude_pet_usage.py`, con `security find-generic-password
-s "Claude Code-credentials" -w`. Chequea el archivo **primero** y cae al
Keychain despues, no al reves: si algun dia Claude Code vuelve al archivo en
Mac, sigue andando sin tocar codigo.

### ❌ El filtro del corte por ruta era incompleto

El analisis planteaba portar el filtro de Windows (marcador de ruta del
binario de la extension de VS Code) tal cual, sin `.exe`. La ruta se confirmo
—  `~/.vscode/extensions/anthropic.claude-code-2.1.241-darwin-arm64/resources/
native-binary/claude`, misma estructura — pero el filtro igual quedaba corto.

Lo que el analisis no contemplo: en Mac hay **dos formas** de correr Claude
Code a la vez, y solo una se ve por ruta. Medido sobre los procesos reales:

```
7 procesos  .../native-binary/claude   <- panel de VS Code
1 proceso   claude                      <- CLI nativo, desde terminal
```

El CLI nativo (`~/.local/bin/claude` -> `~/.local/share/claude/versions/X`)
sale **pelado** en `ps -axo comm=`: es un symlink y `ps` no lo resuelve. Un
filtro por marcador de ruta habria matado los 7 del panel y dejado viva la
sesion de terminal, que quema la ventana de 5h exactamente igual.

Fix: comparar `os.path.basename(comm)` con `"claude"`, case-sensitive.

## Lo que el analisis previo acerto

### ✅ La ambiguedad de nombre de Windows no se repite

Era una suposicion ("basada en como empaquetan Electron normalmente en Mac, no
algo confirmado contra el `Info.plist` real"). Confirmada:

```
/Applications/Claude.app/Contents/MacOS/Claude                    <- 'Claude'
.../Claude Helper (Renderer).app/Contents/MacOS/Claude Helper...  <- 'Claude Helper'
.../ClaudeUsageWidgetExtension.appex/.../ClaudeUsageWidgetExtension
```

Ninguno es `claude` exacto, asi que el match case-sensitive por basename los
excluye a los tres **solo**. No hizo falta nada parecido al `-Filter "Name =
'claude.exe'"` que en Windows evita que la consulta se automatchee: `ps` no
inyecta el patron de busqueda en su propia linea de comandos.

Verificado en vivo: el filtro devuelve 8 objetivos y deja los 10 procesos de
`Claude.app` intactos.

### ✅ Overlay, tray, colores, barra

Andan por Qt sin un solo cambio, como se esperaba.

### ✅ `QSharedMemory` tenia el gotcha POSIX que se sospechaba

*"En sistemas POSIX los segmentos de memoria compartida historicamente pueden
sobrevivir a un crash sin liberarse solos."* Correcto, y se arreglo con el
`attach()`+`detach()` estandar de Qt en Unix (`_single_instance_guard()`).

Probado explicitamente, que era lo que el analisis pedia: se lanza la mascota,
se la mata con `kill -9`, y se relanza. **Arranca.** Antes del fix ese `-9`
habria dejado el lock tomado para siempre.

## Lo que el analisis no vio venir

### El toast de Qt no degrada: no funciona

El analisis lo daba como *"Deberia andar, permisos distintos"*, esperando que
la diferencia fuera el modelo de permisos. Es peor que eso.
`QSystemTrayIcon.showMessage()` en Mac pasa por `UNUserNotificationCenter`,
que **exige bundle identifier**. Corriendo como `python claude_pet.py` no hay
bundle — `lsappinfo` lo confirma: `bundleID=[ NULL ]` — y la notificacion no
aparece.

Y falla **en silencio**, que para un canal de alerta es el peor modo posible
de fallar: la llamada no tira, no loguea, simplemente no pasa nada. Igual que
el bug de las credenciales, se veria como "todavia no llego a ningun umbral".

Fix: `_notify()` usa `osascript -e 'display notification ...'` en darwin, que
no necesita bundle propio. Verificado: notificaciones reales en pantalla.

### El icono del Dock

No estaba en el analisis. PySide6 sin bundle abre icono en el Dock y entra en
Cmd+Tab, para un overlay que vive en la barra de menu. `_hide_dock_icon()` lo
resuelve con `NSApplicationActivationPolicyAccessory` (el `LSUIElement` de un
bundle) por el runtime de ObjC via `ctypes` — sin arrastrar PyObjC, que habria
sido una dependencia nueva. Verificado: `lsappinfo` reporta `type="UIElement"`.

## Sonido: se hizo el tono propio

El analisis lo daba como degradacion aceptable (`QApplication.beep()`
repetido) y sugeria `afplay` o PyObjC como mejora opcional. Se hizo con
`afplay`, porque lo que se perdia no era cosmetico: con el beep generico,
aviso y alarma **suenan igual** y solo se distinguen contando beeps. Ping+Glass
para aviso (dos notas), Sosumi x3 para alarma (sirena). Sin dependencias
nuevas.

## Tabla final

| Pieza | En macOS | Estado |
|---|---|---|
| `claude_pet_collector.py` | Anda tal cual | ✅ verificado |
| `claude_pet_usage.py` (poller) | **Necesitaba el Keychain** | ✅ portado |
| `claude_pet_slack.py` + `slack_setup.py` | Anda tal cual (y sigue desconectado) | — |
| Overlay, colores, barra, tray icon | Anda por Qt | ✅ verificado |
| Sonido | `afplay` + .aiff del sistema | ✅ portado |
| Notificacion del SO | **Qt no sirve**, va por `osascript` | ✅ portado |
| Pantalla completa (`AlertScreen`) | Anda, con el fix de Spaces | ✅ portado |
| **Corte automatico** | `ps` + basename + `SIGTERM`/`SIGKILL` | ✅ portado |
| Instancia unica (`QSharedMemory`) | **Necesitaba el fix POSIX** | ✅ portado |
| Icono en el Dock | `ctypes` + activation policy | ✅ portado |
| Visibilidad (Spaces, deactivate) | `_mac_keep_visible()` | ✅ portado |
| Autostart | `launchd`, receta completa en el README | ✅ |

### La mascota directamente desaparecia (encontrado usandola)

El analisis previo marcaba Spaces como "sin verificar" y sospechaba del
comportamiento de `WindowStaysOnTopHint`. El problema real era anterior y mas
basico, y aparecio a los minutos de usarla: **`Qt.Tool` en macOS es un NSPanel
con `hidesOnDeactivate=YES`**. La ventana se esconde sola cuando la app no es
la activa — y la mascota **nunca** es la activa, que es exactamente para lo
que se eligio `Qt.Tool` (no robar foco). El overlay se ocultaba apenas tocabas
otra ventana.

Medido sobre la ventana real, antes y despues:

| | antes | despues |
|---|---|---|
| `hidesOnDeactivate` | `True` | `False` |
| `collectionBehavior` | `258` | `257` |
| `level` | `8` | `25` |

El `258` (`MoveToActiveSpace | FullScreenAuxiliary`) explica la segunda mitad:
MoveToActiveSpace mueve la ventana al Space activo *cuando la app se activa*,
y esta app no se activa nunca — asi que se quedaba en el Space donde nacio. Y
el nivel 8 queda por debajo de una app en fullscreen.

Fix: `_mac_keep_visible()` — `Qt.WA_MacAlwaysShowToolWindow` mas
`setCollectionBehavior:` / `setLevel:` / `setHidesOnDeactivate:` por ctypes.
Se aplica a la mascota y tambien a `AlertScreen`, donde importa mas: la
pantalla completa es el unico canal que no se corta con `muted`.

## Lo que sigue sin verificar

Si el fullscreen de `AlertScreen` tapa la barra de menu o el notch en los
modelos que lo tienen. Y vale la limitacion que el README ya documenta para
Windows: una app en fullscreen **exclusivo** (algunos juegos, algunos
reproductores) puede tapar cualquier ventana always-on-top, esta incluida.

## Tests

`tests/test_kill.py` tenia un `test_no_windows_no_intenta_nada` parametrizado
sobre `["darwin", "linux"]` que afirmaba que en Mac no se hacia nada. Dejo de
ser cierto: `darwin` salio del parametrize (quedo
`test_linux_todavia_no_esta_portado`) y hay una suite nueva de 11 tests que
corre la salida REAL de `ps` de esta maquina como fixture, cubriendo que se
maten las dos formas de correr Claude Code, que **no** se toque la app de
escritorio, la escalada SIGTERM -> SIGKILL solo para sobrevivientes, y los
modos de fallo (`ps` ausente, timeout, returncode ≠ 0).

`tests/test_usage_normalize.py` suma 5 tests del Keychain: que se lea con
`-w`, que el archivo gane si existe, que un error explique el motivo, y que en
Windows el fallo siga siendo `FileNotFoundError`.

Suite completa: **128 tests**, sin red, corriendo desde el Mac (los tests
mockean `sys.platform`, asi que la rama de Windows se sigue verificando).

## Linux, de paso

Sigue sin portar: `_kill_claude_code_processes()` devuelve `-1` en linux y
`test_linux_todavia_no_esta_portado` lo fija. La rama seria muy parecida a la
de darwin (`ps` + `os.kill`), pero **necesita su propia verificacion**: no
esta comprobado como se ve el CLI de Claude Code en `ps` ahi, ni si la app de
escritorio comparte nombre. Justamente el tipo de supuesto que en Mac fallo
dos de cuatro veces.

`_play_alert` y `_notify` ya caen a `QApplication.beep()` y
`QSystemTrayIcon.showMessage()` respectivamente en linux, que es lo razonable
hasta que alguien lo pruebe. El fix POSIX de `QSharedMemory` **si** aplica a
linux y ya esta puesto (la rama es `!= "win32"`, no `== "darwin"`). El README
ya documenta autostart con `.desktop` en `~/.config/autostart/`.
