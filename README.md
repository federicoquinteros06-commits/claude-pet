# Claude Pet — mascota de uso siempre visible

Overlay always-on-top con el % de la ventana de 5h de Claude Code: avisos en
25/50/75/85%, alarma con pantalla completa en 90%, y corte automatico de las
sesiones de Claude Code en 95%+ — con notificacion nativa del SO y sonido.

```
[ poller ]     GET /api/oauth/usage cada 140s, hilo dentro de la mascota
      |        escribe ~/.claude/pet/usage.json          <- fuente principal
      |
[ collector ]  statusLine de Claude Code, corre local, 0 tokens
      |        escribe ~/.claude/pet/sessions/<id>.json  <- solo en la TUI
      v
[ mascota ]    PySide6, lee cada 1s, dibuja, dispara toast + tono
```

Las dos fuentes reportan las mismas ventanas y la mascota se queda con la mas
nueva. La diferencia esta en cuando aplican: **el statusLine no corre en el
panel de VS Code** (el webview no tiene donde ejecutarlo), asi que si trabajas
desde el panel el poller es lo unico que tenes. El collector sigue siendo el
unico que aporta contexto% y costo por sesion.

> **Windows y macOS.** Se desarrollo en Windows y se porto a macOS el
> 25/8/2026, verificando cada pieza contra un Mac real (macOS 26.6.2, arm64):
> credenciales, corte automatico, sonido, notificaciones e instancia unica.
> Linux sigue sin portar — el corte automatico ahi es un no-op. Ver
> [PORTING.md](PORTING.md) para el detalle de que cambio y por que.

---

## 0. Antes que nada: verifica que tu cuenta exponga el uso

Un solo comando, sin instalar nada todavia. Lee el token OAuth de
`~/.claude/.credentials.json` y consulta el endpoint:

```bash
python3 claude_pet_usage.py
```

Si imprime un JSON con `five_hour` y `seven_day`, estas listo. Si tira un error
de auth, tu cuenta no expone el dato (el endpoint es para suscriptores de
Claude.ai; en Team/Enterprise puede no venir): la mascota igual arranca, pero
muestra `--` en el % de 5h y los umbrales no van a poder dispararse.

Como segunda via, el statusLine trae las mismas ventanas dentro de su payload,
pero **solo si usas la TUI** y solo despues de la primera respuesta de API de la
sesion. Para inspeccionarlo, corre el collector con `--dump` y mira
`~/.claude/pet/last_raw.json`.

---

## 1. Instalar

```bash
pip install PySide6
mkdir -p ~/.claude/pet
cp claude_pet_collector.py claude_pet.py claude_pet_usage.py ~/.claude/pet/
```

`claude_pet_slack.py`, `slack_setup.py` y `slack_app_manifest.yaml` no hacen
falta para este paso — quedan en el repo como referencia (ver seccion 3).

**macOS**: si el `python3` del sistema es viejo (en macOS 26 es 3.9.6, de
CommandLineTools) conviene un venv aparte antes que instalar PySide6 encima:

```bash
uv venv --python 3.13 ~/.claude/pet/.venv
uv pip install --python ~/.claude/pet/.venv PySide6
mkdir -p ~/.claude/pet
cp claude_pet_collector.py claude_pet.py claude_pet_usage.py ~/.claude/pet/
~/.claude/pet/.venv/bin/python ~/.claude/pet/claude_pet.py
```

## 2. Enganchar el collector como statusLine

En `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/pet/claude_pet_collector.py",
    "refreshInterval": 10
  }
}
```

`refreshInterval` hace que el script tambien corra cada 10s ademas de los
eventos. Sin eso, si la sesion queda idle (por ejemplo esperando subagentes en
background) el % se congela.

**Windows:** Claude Code rutea el comando por Git Bash si esta instalado. Usa
barras normales en la ruta, nunca `\`, o el comando falla en silencio:

```json
{ "statusLine": { "type": "command",
  "command": "python C:/Users/fede/.claude/pet/claude_pet_collector.py",
  "refreshInterval": 10 } }
```

Probalo a mano antes:

```bash
echo '{"session_id":"t","model":{"display_name":"Opus"},"workspace":{"current_dir":"/tmp"},"context_window":{"used_percentage":37},"rate_limits":{"five_hour":{"used_percentage":91,"resets_at":9999999999}}}' \
  | python3 ~/.claude/pet/claude_pet_collector.py
```

## 3. Slack — implementado, probado, y deshabilitado a proposito

Hubo una version con bot de Slack (DM al celular via `chat.postMessage`).
Quedo funcionando de punta a punta: se creo la app, se instalo, y una alerta
real llego al DM. Se desactivo despues por dos razones, no porque algo fallara:

1. **El pedido real era otro.** "Push del sistema" resulto significar
   notificacion en la misma maquina donde corre la mascota — eso es el toast de
   Windows (seccion de abajo), no algo que necesite salir a internet.
2. **En un Slack corporativo, Slack trae friccion que no vale la pena.**
   *Create New App* suele requerir permiso de admin, y aunque lo tengas, la app
   queda listada en "Agentes y aplicaciones" para cualquiera del workspace que
   la busque — nadie mas recibe alertas, pero la visibilidad no se puede
   evitar. Para un aviso personal de uso, es una superficie que no justifica el
   beneficio.

**Los archivos siguen en el repo, funcionales y probados, por si algun dia hace
falta un canal remoto (fuera de la maquina) de nuevo:**

- [`claude_pet_slack.py`](claude_pet_slack.py) — transporte (bot `chat.postMessage`
  o Incoming Webhook), resolucion de destino, reintentos. `tests/test_slack.py`
  lo sigue cubriendo (34 tests, sin red).
- [`slack_setup.py`](slack_setup.py) + [`slack_app_manifest.yaml`](slack_app_manifest.yaml)
  — creacion asistida de la app y resolucion del member ID.

Ninguno esta importado desde `claude_pet.py`: son codigo huerfano a proposito,
no una feature medio prendida. Para reactivarlo hay que volver a cablear estos
puntos en `claude_pet.py` (todos existian antes, se sacaron enteros):

1. `DEFAULT_CONFIG`: agregar `slack_bot_token`, `slack_target`,
   `slack_webhook_url`, `slack_mention_on_alarm`.
2. Import opcional `import claude_pet_slack as slack` con el mismo patron
   try/except que ya usa `claude_pet_usage` (`HAS_POLLER`) para no romper si
   el archivo no esta copiado.
3. Una funcion `notify_slack(cfg, text, mention, done)` que delega en
   `slack.send()` y avisa por `done()` si el modulo falta o no hay config.
4. En `Pet`: una `QtCore.Signal` para volver del hilo de envio al hilo de Qt
   (tocar el tray desde otro hilo crashea), item de menu "Probar Slack", y la
   llamada a `notify_slack` dentro de `_fire()`.

**Verificado el 25/8/2026** contra un workspace real ("test fede"): DM
resuelto por member ID, `<!here>` correctamente omitido en DM, error real de
Slack (`invalid_auth`) traducido en castellano. El modulo funciona; la decision
de no usarlo es de producto, no tecnica.

## 4. Correr la mascota

```bash
python3 ~/.claude/pet/claude_pet.py
```

Arrastrala con el mouse (guarda la posicion). Click derecho en el icono de
bandeja: silenciar, abrir config, salir.

Para que arranque sola:
- **Windows**: acceso directo a `pythonw claude_pet.py` en `shell:startup`
  (`pythonw` evita la ventana de consola)
- **Linux**: `.desktop` en `~/.config/autostart/`
- **macOS**: `launchd`. Guardar como
  `~/Library/LaunchAgents/com.claudepet.overlay.plist` y cargar con
  `launchctl load -w ~/Library/LaunchAgents/com.claudepet.overlay.plist`:

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0">
  <dict>
    <key>Label</key><string>com.claudepet.overlay</string>
    <key>ProgramArguments</key>
    <array>
      <string>/Users/TUUSUARIO/.claude/pet/.venv/bin/python</string>
      <string>/Users/TUUSUARIO/.claude/pet/claude_pet.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
  </dict>
  </plist>
  ```

  `launchd` no expande `~`: las rutas van absolutas. Y ojo con el Keychain —
  si nunca le diste "Permitir siempre" desde una corrida manual, bajo
  `launchd` no hay nadie para autorizar el dialogo.

---

## macOS: lo que es distinto

Portado y verificado el 25/8/2026 contra macOS 26.6.2 (arm64). Todo lo de este
README aplica igual salvo lo siguiente.

**Las credenciales no estan en un archivo, estan en el Keychain.** En Windows
y Linux el token OAuth vive en `~/.claude/.credentials.json`. En Mac ese
archivo **no existe**: Claude Code guarda el mismo JSON en el Keychain del
login, bajo el servicio `Claude Code-credentials`. `_creds()` en
`claude_pet_usage.py` lo lee con `security find-generic-password -w`.

Era el unico bloqueante real del port, y el mas silencioso: sin ese cambio
`_token()` tira `FileNotFoundError` en cada poll, `usage.json` nunca se
escribe, la mascota muestra `--` para siempre y **ningun umbral llega a
dispararse** — el corte automatico incluido. Se ve igual que "todavia no
llegaste al 25%".

Si la primera lectura abre el dialogo del Keychain, dale **"Permitir
siempre"**: bajo `launchd` no hay nadie para autorizarlo y el poller queda
sin token.

**El corte automatico mata las sesiones de terminal tambien.** En Windows el
filtro es por ruta, porque `claude.exe` es un nombre ambiguo que comparte con
la app de escritorio. En Mac el problema es el opuesto:

| proceso | `ps -axo comm=` | corte |
|---|---|---|
| panel de VS Code | `.../native-binary/claude` | mata |
| CLI nativo en terminal | `claude` | mata |
| app de escritorio | `/Applications/Claude.app/Contents/MacOS/Claude` | **no toca** |
| helpers de Electron | `Claude Helper (Renderer)` | **no toca** |

El nombre no es ambiguo (`Claude` != `claude`), asi que alcanza con comparar
el **basename, case-sensitive**. Y tiene que ser por basename y no por ruta,
porque el CLI nativo (`~/.local/bin/claude`) sale **pelado** en `ps`: es un
symlink y `ps` no lo resuelve. Un filtro por marcador de ruta como el de
Windows dejaria vivas todas las sesiones de terminal, que queman la ventana
de 5h igual que las del panel.

Para auditar el filtro sin matar nada:

```bash
~/.claude/pet/.venv/bin/python -c "import claude_pet; print(claude_pet._pids_darwin())"
```

**Las notificaciones no salen por Qt.** `QSystemTrayIcon.showMessage()` en Mac
pasa por `UNUserNotificationCenter`, que exige bundle identifier. Corriendo
como `python claude_pet.py` no hay bundle (`lsappinfo` lo confirma:
`bundleID=[ NULL ]`) y la notificacion **falla en silencio** — el peor modo de
fallar para un canal de alerta. Por eso `_notify()` usa `osascript` en darwin.
La primera vez puede pedir permiso en Ajustes del Sistema -> Notificaciones.

**El sonido usa `afplay`.** No hay equivalente a `winsound.Beep(freq, ms)` en
la stdlib, pero con los `.aiff` del sistema se conserva lo que importa: que
aviso y alarma suenen **distinto**, no solo una cantidad distinta de veces del
mismo beep. Ping+Glass para aviso, Sosumi x3 para alarma.

**La instancia unica necesitaba un fix.** El comentario original decia que en
Windows el SO libera el bloque de `QSharedMemory` al morir el proceso, asi que
un crash no deja lock huerfano. En POSIX **no es asi**: el segmento sobrevive
y `create()` fallaria para siempre — la mascota no volveria a arrancar nunca
sin borrarlo a mano. `_single_instance_guard()` hace el `attach()`+`detach()`
que es el remedio estandar de Qt en Unix.

**Sin icono en el Dock.** `_hide_dock_icon()` pone
`NSApplicationActivationPolicyAccessory` (el `LSUIElement` de un bundle) por
el runtime de ObjC via `ctypes`, sin arrastrar PyObjC. Es cosmetico: si falla,
queda el icono y nada mas.

**Sin verificar todavia**: como se comporta la pantalla completa contra
Mission Control / Spaces, y si una app en fullscreen exclusivo la tapa.

---

## Config (`~/.claude/pet/config.json`)

| clave | default | que hace |
|---|---|---|
| `warn_thresholds` | `[25, 50, 75, 85]` | avisos: notif + tono de 2 notas |
| `alarm_thresholds` | `[90]` | alarma: notif + sirena + flash + pantalla completa |
| `watch_seven_day` | `true` | tambien vigila la ventana semanal |
| `seven_day_thresholds` | `[85, 95]` | primero = aviso, resto = alarma |
| `muted` | `false` | silencia sonido y notificacion (la pantalla completa NO se corta con esto) |
| `fullscreen_alert_enabled` | `true` | pantalla completa en alarmas, ver abajo |
| `auto_kill_enabled` | `true` | mata las sesiones de Claude Code al llegar a `kill_threshold` |
| `kill_threshold` | `95` | umbral del corte automatico, ver abajo |
| `scale` | `1.0` | tamaño del widget |
| `usage_poller_enabled` | `true` | consulta `/api/oauth/usage`; anda sin TUI |
| `usage_poll_seconds` | `140` | cada cuanto consulta. **No bajarlo**, ver abajo |

## Estados de la mascota (semaforo)

| estado | umbral 5h | color |
|---|---|---|
| SIN DATOS | sin dato confiable | gris |
| tranqui | < 50% | verde |
| ojo | 50–74% | amarillo |
| che | 75–89% | naranja |
| PARA | >= 90% | rojo, pulsa |
| CREDITOS | >= 99% | violeta |

El color es puramente visual y corre independiente de los umbrales de alerta
de abajo (`MOOD_BREAKPOINTS` en el código, fijo en 50/75/90). CREDITOS es la
señal de que estas por entrar (o entraste) en creditos de uso — un caso
aparte del semaforo, no un quinto escalon de severidad. Si lo ves, Esc y
cortar.

**SIN DATOS (gris) no es "tranqui" (verde).** Verde significa que la mascota
*sabe* que estas bajo, no que no tiene informacion. Si el poller se cae por
mas de `USAGE_FRESH` (15 min por defecto) y no hay sesion de TUI viva para
usar de respaldo, el % cae a `None` y el estado pasa a gris — nunca a verde.
Visto en vivo: un antivirus bloqueando la escritura del poller dejo la
mascota casi 3 horas sin dato, y antes de este fix se veia verde y tranquila
en vez de avisar que no sabia nada.

## Que hace cada umbral

| uso | notif + sonido | pantalla completa | corte de sesiones |
|---|---|---|---|
| 25 / 50 / 75 / 85% | si | no | no |
| 90% (`alarm_thresholds`) | si | si | no |
| 95%+ (`kill_threshold`) | **no** | si | si |

A partir de 95% la unica alerta que queda es la que corta de verdad: sumar
sonido o toast ahi no aporta nada, así que se apagan a proposito.

## Alarma en pantalla completa

En 90% (`alarm_thresholds`), ademas del toast y el sonido, se abre una
pantalla completa en tu **monitor principal** — el mismo que Windows llama
"Pantalla principal" en Configuracion -> Sistema -> Pantalla, sin importar en
cual de tus monitores este la mascota. Pensada para cuando las notificaciones
estan muteadas, no tenes los auriculares puestos, o estas mirando otro
monitor: es el unico canal que no depende de verla ni de escucharla a tiempo.

Por eso **no se corta con `muted`**: es a proposito, es el respaldo para
cuando el resto esta silenciado. Se cierra con un clic, cualquier tecla, o
sola a los 25 segundos si no la tocas. Para apagarla del todo (por ejemplo si
vas a compartir pantalla seguido y no queres el riesgo de que se abra en medio
de una reunion), `fullscreen_alert_enabled: false` en la config.

**Limitacion conocida:** una app en pantalla completa exclusiva (algunos
juegos, algunos reproductores de video) puede tapar cualquier ventana
always-on-top, esta incluida. Para el uso normal (codigo, navegador, oficina)
no es un problema.

## Corte automatico: mata las sesiones de Claude Code al 95%

Pensado para cuando Claude esta trabajando en segundo plano (una tarea larga,
un agente en background) y vos no estas mirando ninguna alerta — en una
reunion, por ejemplo. Al cruzar `kill_threshold` (95% por defecto) en la
ventana de 5h, la mascota:

1. Mata los procesos de Claude Code que esten corriendo en esta maquina —
   **inmediato, sin cuenta regresiva ni forma de cancelar**, a proposito.
2. Muestra el resultado en la pantalla completa del monitor principal
   ("CORTADO · se cerraron N sesiones de Claude Code"). **Sin sonido ni
   notificacion del SO** — a esta altura ya sonaron cinco avisos antes (25,
   50, 75, 85, 90%); la unica alerta que falta es la que corta de verdad.

**Que NO toca.** Solo el proceso `claude.exe` del agente — el que
efectivamente consume la ventana de 5h. VS Code, la terminal que lo lanzo, y
la app de escritorio de Claude siguen abiertos: la ventana/pestaña donde
estaba esa sesion va a mostrar que se desconecto, pero el resto del programa
sigue andando. No cierra editores, no pierde el resto de tu trabajo.

**Por que no basta con matar por nombre de proceso.** `claude.exe` es
ambiguo: la app de escritorio de Claude usa el MISMO nombre de ejecutable
(`...\WindowsApps\Claude_...\app\claude.exe`) para un producto que no tiene
nada que ver con la ventana de 5h. Filtrar por nombre a secas mataria esa app
tambien. El filtro real usa la ruta especifica del binario nativo que trae la
extension de VS Code
(`.../extensions/anthropic.claude-code-*/resources/native-binary/claude.exe`),
verificado contra los procesos reales de esta maquina.

**Solo alcanza a esta maquina.** Si usas Claude Code desde otra computadora,
o via un agente en la nube, el corte automatico no llega ahi — protege
unicamente lo que corre localmente, donde vive la mascota.

Config: `auto_kill_enabled` (`true` por defecto) y `kill_threshold` (`95`).
Para apagarlo, `auto_kill_enabled: false`.

---

## Detalles de implementacion que importan

**El sonido corre en un hilo aparte.** `winsound.Beep()` es sincronico: llamarlo
en el hilo de Qt bloquearia el tick de 1s y la animacion de 50ms mientras dura
la secuencia (hasta ~700ms en la alarma). `_play_alert()` lo tira en un hilo
daemon; sin winsound (mac/Linux) cae al beep generico de Qt.

**Por que el collector no hace HTTP.** El statusLine bloquea la actualizacion de
la barra mientras corre, y si llega un update nuevo mientras el script sigue
vivo, Claude Code lo cancela. Cualquier POST de un par de segundos ahi te deja
la barra congelada y podes perder alertas. Por eso el collector solo escribe un
archivo; cualquier I/O de red le corresponde a la mascota, que es el proceso
largo (ver seccion 3: es el motivo por el que `claude_pet_slack.py` esta
pensado para correr en un hilo daemon, aunque hoy no este cableado).

**Escritura atomica.** El collector escribe a `.tmp` y hace `os.replace()`. Sin
eso, la mascota eventualmente lee un JSON a medio escribir y parpadea.

**Multiples instancias de Claude Code.** Cada sesion escribe su propio archivo
por `session_id`. La mascota lee todas las que escribieron hace menos de 5 min,
toma el `rate_limits` mas reciente (es a nivel cuenta, todas reportan lo mismo),
suma los costos y muestra el contexto de la sesion mas cargada. El contador
"N sesion(es)" te sirve para detectar instancias zombie quemando cuota.

**Dedupe de alertas.** La clave es `(ventana, umbral, resets_at)`, persistida en
`~/.claude/pet/fired.json`. Cada umbral suena una sola vez por ventana; cuando la
ventana rota, `resets_at` cambia y todo se re-arma solo. Sin esa clave, con
`refreshInterval: 10` te comerias una alerta cada 10 segundos durante horas.

**El jitter del endpoint rompia esto y no se notaba.** `/api/oauth/usage`
devuelve `resets_at` con microsegundos que cambian entre respuestas: el mismo
reset vuelve como `...399.55252`, `...399.565691`, `...400.492996`. Interpolar
ese float en la clave fabricaba una clave nueva por cada poll, asi que el dedupe
no dedupeaba: `fired.json` llego a 88 entradas de UNA sola ventana (24 disparos
del umbral 90) con un spread total de 0.94s. La verificacion manual nunca lo
detecto porque usaba un `resets_at` sintetico fijo. Hoy `AlertEngine._canon()`
compara por cercania y hay tests que lo cubren:

```bash
C:/Users/Usuario/AppData/Local/Programs/Python/Python310/python.exe -m pytest
```

La suite captura la escalera 70 -> 99%, el re-armado al rotar la ventana, el
descarte de sesiones stale, el arbitraje poller/statusLine y la normalizacion
del ISO-8601. Ver [CLAUDE.md](CLAUDE.md) para como estan escritos.

**El limite del endpoint, medido.** No esta documentado, no viene en headers de
respuesta, y los issues 30930, 31021 y 31637 de `anthropics/claude-code` fueron
cerrados sin respuesta de Anthropic. Medido el 25/8/2026 durante una hora sin
nadie mas usando la maquina, 36 intentos:

> **6 requests por ventana deslizante de ~7-11 minutos.**
> Todo intento con <= 6 requests previas en la ventana devolvio 200. Todo
> intento con 7 devolvio 429. Sin una sola excepcion.

El ancho de la ventana queda acotado entre 422 y 652 segundos por dos casos
opuestos: un intento *exitoso* tenia su 7a request previa a 652s (con una
ventana mas ancha habria contado 7 y tenia que fallar), y uno *fallido* tenia 7
dentro de 422s.

De ahi salen los dos numeros del config:

| | |
|---|---|
| piso `110s` | `P > W/6 = 652/6 = 108.7`. Por debajo, el poller solo se autogenera el 429 |
| default `140s` | deja un lugar libre para una request ajena en cada ventana |

**Bajar el intervalo no trae mas datos.** El endpoint entrega 6 lecturas cada
~12 minutos pase lo que pase. A 70s se obtienen 31 por hora pero con 371s de
ceguera maxima, porque el ciclo real es: 6 exitos, un 429, 300s a oscuras, otra
vez -- se repitio 4 veces con precision de reloj, cada 722-723 segundos. A 140s
son 26 por hora con 140s de ceguera. Practicamente los mismos datos, mucho mejor
repartidos.

Ojo con dos consumidores que no son el poller: abrir el panel de Usage de VS
Code hace una consulta, y **cada arranque de la mascota dispara un poll
inmediato** (reiniciarla varias veces seguidas consume la ventana).

Un 429 cuesta 300s. A veces declara `Retry-After` y a veces no -- de los 5 de la
hora monitoreada, ninguno lo traia -- asi que el respaldo fijo es el camino
normal. Los 5 esperaron 300s y los 5 recuperaron al primer intento.

El cliente oficial, por su parte, **no pollea**: consulta cuando abris el panel
de Usage, y el resto del tiempo lee las ventanas de los headers
`anthropic-ratelimit-unified-*` que viajan en el trafico normal. El endpoint
esta dimensionado para eso, no para monitoreo.

---

## Ideas para la v2

- Extrapolar la pendiente de consumo y estimar a que hora vas a chocar el limite;
  alertar por tiempo restante en vez de por umbral fijo.
- Loggear cada muestra a SQLite/Parquet y sacar un dashboard de consumo por
  proyecto (`cwd`) y por modelo. Con `cost.total_cost_usd` por sesion tenes
  atribucion gratis.
- ~~Bloqueo activo: cortar la sesion por encima de X%.~~ **Hecho** (corte
  automatico al 95%, ver arriba) — pero via `Stop-Process`, matando el proceso
  entero. Un hook `PreToolUse` que rechace la siguiente tool call seria mas
  fino (no interrumpe una escritura en curso), a costo de mas superficie de
  implementacion y de no cubrir una sesion ya bloqueada esperando una
  respuesta larga del modelo.
- ~~Portar el corte automatico y la pantalla completa a macOS/Linux.~~
  **macOS hecho** (25/8/2026, ver `PORTING.md` y la seccion de macOS arriba).
  Linux sigue pendiente: necesita la misma rama `ps`/`os.kill` que darwin,
  pero con su propia verificacion de como se ve el CLI en `ps` ahi.
