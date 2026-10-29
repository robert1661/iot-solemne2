# Conexión PLC – Raspberry Pi – ESP32 (vía CBOX) con la plataforma IoT

Guía paso a paso para dejar los tres equipos hablando con la plataforma Django
de este repositorio. Todo termina en el mismo lugar: el broker MQTT
(Mosquitto), con el formato del "Contrato MQTT" del `README.md` raíz.

## 1. Cómo queda conectado

```
                   Red del laboratorio 10.41.32.x
 ┌───────────────┐  Ethernet  ┌──────────────────┐   WiFi abierta "BOX-708106-18125"
 │ PLC S7-1200   │────────────│ CBOX Haiwell     │ ))) ───────────────────────┐
 │ CPU 1214C     │            │ 10.41.32.52      │                            │
 │ 10.41.32.11   │            └──────────────────┘                            │
 └───────────────┘                                                            │
        ▲  protocolo S7 (puerto 102, PUT/GET)                                 │
        │                                                                     │
 ┌──────┴──────────────────────────────┐        MQTT (1883)          ┌───────┴──────┐
 │ Raspberry Pi                         │◄────────────────────────────│ ESP32        │
 │  • edge/raspberry/gateway.py         │                             │ sensor + LED │
 │      PLC ⇄ MQTT  y  ESP32 → PLC      │                             └──────────────┘
 │  • docker compose: Mosquitto, Django,│
 │    PostgreSQL, mqtt_listener         │
 └──────┬───────────────────────────────┘
        │  http://<ip-de-la-raspberry>:8000/monitoring/
   Notebook o celular (dashboard)
```

| Equipo | Qué hace |
|---|---|
| **PLC S7-1200** | Controla el proceso: entradas I0.0–I1.5, salidas Q0.0–Q1.1, analógicas IW64/IW66 (0–10 V). |
| **CBOX Haiwell** | Puente de red: lleva el PLC (por cable) al WiFi. También deja programar el PLC con TIA Portal por ese WiFi. No hay que configurarle nada para esta tarea. |
| **Raspberry Pi** | *Gateway*: lee y escribe el PLC por protocolo S7 y lo traduce a MQTT. Además corre la plataforma (Docker) y publica su propia telemetría. |
| **ESP32** | Sensor IoT inalámbrico: publica directo por MQTT y recibe comandos (LED/relé). |
| **Django** | Dashboard, usuarios y roles, base de datos PostgreSQL. |

> **¿Por qué no usar el MQTT del propio CBOX?** El CBOX publica con el formato
> de Haiwell y se configura con su software (Haiwell Cloud SCADA). La plataforma
> espera un mensaje por variable, con el token del dispositivo
> (`ecs/<slug>/<variable>` → `{"value": ..., "token": ...}`). Pasando por la
> Raspberry el formato queda bajo nuestro control y, de paso, la Raspberry
> cumple un rol real en el sistema.

## 2. Prueba de red (5 minutos, en el laboratorio)

Con el notebook conectado al WiFi `BOX-708106-18125`, en PowerShell:

```powershell
ipconfig                                   # anoten la IP que les dio el CBOX
ping 10.41.32.11                           # el PLC
Test-NetConnection 10.41.32.11 -Port 102   # el puerto S7 del PLC
```

- **`TcpTestSucceeded : True`** → el CBOX ya enruta el WiFi hacia el PLC.
  **Opción A:** Raspberry, ESP32 y notebook se conectan todos al WiFi del CBOX.
- **`False`** → **Opción B:** la Raspberry va por **cable** a la red del PLC
  (switch del laboratorio o la LAN libre del CBOX) con una IP fija libre de la
  red 10.41.32.x (pregúntenle al profesor cuál usar para no chocar con otro
  puesto), y por **WiFi** al CBOX o a un hotspot donde también esté la ESP32.
  La Raspberry queda así entre las dos redes:

  ```bash
  # IP fija en el cable (Raspberry Pi OS Bookworm, NetworkManager). Sin puerta de enlace,
  # para que internet siga saliendo por el WiFi.
  sudo nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 10.41.32.60/24
  sudo nmcli con up "Wired connection 1"
  ping 10.41.32.11
  ```

> Las redes `UNAB-WIFI6-*` no sirven para la ESP32: son de usuario y contraseña
> (WPA2-Enterprise) y normalmente aíslan a los equipos entre sí.

## 3. PLC en TIA Portal

1. **Habilitar PUT/GET** (sin esto la Raspberry no puede leer el PLC):
   PLC_1 → Propiedades → *Protection & Security* → *Connection mechanisms* →
   ☑ **Permit access with PUT/GET communication from remote partner**.
   En *Access level* dejen **Full access**.
2. **Mando remoto:** el dashboard escribe la marca **M10.0** (variable
   `marcha_remota`). En el programa (OB1) pongan un contacto M10.0 en serie con
   su lógica de marcha, o para la primera prueba un segmento
   `M10.0 --| |-- ( Q0.0 )`. No escriban la salida Q directo: si el programa
   usa esa salida, el PLC la sobrescribe en cada ciclo.
3. **Si usan un DB** (setpoints, valores de la ESP32): propiedades del DB →
   *Attributes* → **desmarcar "Optimized block access"**, y compilar. Los
   offsets (0.0, 4.0, …) aparecen en la columna *Offset* del DB.
4. Compilar (hardware y software), cargar al PLC y ponerlo en **RUN**.

## 4. Raspberry Pi

> **Hagan la instalación donde haya internet** (casa o hotspot): Docker
> descarga imágenes y librerías. En el laboratorio, el WiFi del CBOX no tiene
> internet, pero una vez instalado todo funciona sin conexión.

### 4.0 Sin Docker (lo que quedó instalado en la Raspberry del grupo)

La Raspberry `proyectotitulo` corre la plataforma sin Docker: Django con
gunicorn, base de datos **SQLite** (`DB_ENGINE=sqlite` en `.env`) y el
Mosquitto del sistema. Todo vive en `~/iot-solemne2` y se maneja con un script:

```bash
cd ~/iot-solemne2/edge/raspberry
./plataforma.sh estado      # qué está corriendo
./plataforma.sh iniciar     # web + listener + gateway contra el PLC real
./plataforma.sh simulado    # lo mismo, pero con plc_simulador.py (para mostrar sin el PLC)
./plataforma.sh detener
tail -f ~/iot-solemne2/logs/gateway.log
```

Arranca sola al prender la Raspberry (`crontab -l` muestra la línea
`@reboot`). El usuario del dashboard es `admin`; su clave está en
`~/iot-solemne2/.env` (`DJANGO_SUPERUSER_PASSWORD`). Para que la ESP32 pueda
publicar desde otro equipo, Mosquitto tiene que escuchar en la red: una sola
vez, `sudo bash ~/iot-solemne2/edge/raspberry/abrir_mqtt_red.sh`.

### 4.1 Docker y la plataforma

Raspberry Pi OS **64 bits** (Bookworm o más nuevo). Si es una Pi 3 o de 1 GB,
mejor corran la plataforma en un notebook con Docker Desktop y usen la Pi solo
como gateway (en `config.toml`, `mqtt.host` = IP del notebook).

```bash
curl -fsSL https://get.docker.com | sh       # instalador oficial de Docker
sudo usermod -aG docker $USER                # cerrar sesión y volver a entrar

git clone https://github.com/<su-usuario>/<su-repo>.git ~/iot-solemne2
cd ~/iot-solemne2
cp .env.example .env
nano .env        # cambiar contraseñas; en DJANGO_ALLOWED_HOSTS agregar la IP de la Raspberry
docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" eclipse-mosquitto:2 \
  sh -c "mosquitto_passwd -b -c /mosquitto/config/passwd ecs <MQTT_PASSWORD del .env> && chmod 644 /mosquitto/config/passwd"

docker compose up -d --build web mqtt_listener   # sin el simulador: los datos ahora son reales
```

Dashboard: `http://<ip-de-la-raspberry>:8000/monitoring/` (usuario y clave
`DJANGO_SUPERUSER_*` del `.env`).

### 4.2 Registrar los dispositivos

En **Dispositivos → + Agregar sensor**, uno por equipo, y copien el token que
muestra cada uno:

| ID del dispositivo (slug) | Dónde va el token |
|---|---|
| `plc-s7-1200` | `edge/raspberry/config.toml` → `[plc] token` |
| `raspberry-pi` | `edge/raspberry/config.toml` → `[raspberry] token` |
| `esp32-01` | `firmware/esp32_ecs_iot/config.h` → `DEVICE_TOKEN` |

Las variables (sensores) se crean solas con el primer mensaje que llega.

### 4.3 El gateway

```bash
cd ~/iot-solemne2/edge/raspberry
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml
nano config.toml          # tokens, IP del PLC y las variables de SU proyecto
.venv/bin/python gateway.py -v
```

Debería decir `PLC conectado: 10.41.32.11` y `MQTT conectado`. Para que
arranque solo al prender la Raspberry, sigan las instrucciones de
`iot-gateway.service`.

En `config.toml` cada `[[plc.tags]]` es una tarjeta del dashboard. Ya vienen
I0.0, I0.1, Q0.0, Q0.1, las dos analógicas (escaladas a 0–10 V) y
`marcha_remota` (M10.0). Cambien nombres y direcciones según lo que tengan
cableado; por ejemplo `variable = "sensor_nivel_alto"` en lugar de
`entrada_i0_1`.

## 5. ESP32

Usen el firmware del repositorio (`firmware/esp32_ecs_iot/`, ver su README).
En `config.h`:

```c
#define DEVICE_SLUG   "esp32-01"
#define DEVICE_TOKEN  "<token de esp32-01>"
#define WIFI_SSID     "BOX-708106-18125"   // red abierta del CBOX (o su hotspot)
#define WIFI_PASSWORD ""                   // vacío = red sin clave
#define MQTT_BROKER_HOST "<ip de la Raspberry en esa misma red>"
#define MQTT_BROKER_PORT 1883
#define MQTT_USERNAME    "ecs"
#define MQTT_PASSWORD    "<MQTT_PASSWORD del .env>"
```

y ajusten `SENSOR_VARIABLE`, `SENSOR_PIN` y la escala a su sensor. Sin
internet no hay hora por NTP: la ESP32 omite el `timestamp` y el servidor
pone la hora de llegada, así que no es problema.

### ESP32 → PLC (opcional)

Si quieren que el PLC use un valor de la ESP32 (ej. partir una bomba cuando
la humedad baja de 40 %), descomenten el bloque `[[puentes]]` de
`config.toml`: el gateway escucha `ecs/esp32-01/<variable>`, valida el token
de la ESP32 y escribe el valor en el DB del PLC. Así los tres equipos quedan
conectados entre sí y no solo con el dashboard.

## 6. Prueba de punta a punta

1. `gateway.py -v` muestra publicaciones cada segundo.
2. En el dashboard aparecen las tarjetas del PLC, de la Raspberry y de la ESP32.
3. Activen una entrada física del PLC (I0.0) → la tarjeta cambia en 1–3 s.
4. En `marcha_remota` elijan el gadget **Botón** (⚙ de la tarjeta) → al
   apretarlo se enciende la salida del PLC y `salida_q0_0` pasa a 1.
5. Gadgets sugeridos: entradas y `plc_en_linea` → **Indicador LED**;
   analógicas → **Gráfico de línea** o **Medidor**; setpoints → **Slider**.

## 7. Probar en casa sin el PLC

`edge/raspberry/plc_simulador.py` levanta un S7-1200 de mentira con las mismas
direcciones (entradas que cambian, analógicas con una onda, Q0.0 = M10.0):

```bash
.venv/bin/python plc_simulador.py          # escucha en el puerto 1102
# en config.toml:  ip = "127.0.0.1"   puerto = 1102
.venv/bin/python gateway.py -v
```

## 8. Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| `No se pudo conectar al PLC ... timed out / refused` | Red: repetir la prueba del punto 2 desde la Raspberry (`ping`, `nc -zv 10.41.32.11 102`). |
| Conecta, pero falla al leer (acceso denegado / función no disponible) | Falta PUT/GET o el nivel de acceso no es *Full access*. Cargar de nuevo la configuración hardware. |
| `Object does not exist` al leer un DB | El DB no existe, tiene otro número o es "optimizado". |
| El botón cambia pero la salida no | El programa sobrescribe esa Q: usar la marca M10.0 en la lógica. |
| ESP32 `Fallo MQTT, rc=-2` | No llega al broker: IP equivocada o no están en la misma red. `rc=4`/`rc=5`: usuario o clave MQTT. |
| No aparecen datos en el dashboard | Dispositivo no registrado o token distinto: revisar **Admin → Unregistered readings**. |
| La Raspberry no tiene la hora correcta | Normal sin internet (no tiene reloj propio): por eso el gateway no manda `timestamp`. |

## 9. Lo que falta para la rúbrica

1. **Repositorio en su cuenta:** creen un repo vacío en GitHub y desde esta
   carpeta: `git remote add origin <url>` y `git push -u origin main` (el
   repo del profesor quedó como remoto `profesor`).
2. **Diseño propio:** colores en `backend/monitoring/static/monitoring/css/dashboard.css`
   (`--brand-primary`, `--brand-accent`), logo e imagen desde
   `/monitoring/branding/`, nombre en `SITE_NAME` / `SITE_OWNER` de `settings.py`.
3. **Sensor IoT en tiempo real:** ESP32 (punto 5).
4. **Usuarios con base de datos:** ya viene (PostgreSQL + roles Global /
   Administrador / Operador); creen una cuenta por integrante.
5. **Demo al profesor:** punto 6.

No suban a GitHub `config.toml`, `config.h` ni `.env` (llevan tokens y
contraseñas; ya están en `.gitignore`).
