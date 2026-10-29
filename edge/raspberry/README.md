# Gateway de la Raspberry Pi (PLC S7-1200 ⇄ MQTT)

`gateway.py` conecta el PLC Siemens S7-1200 con la plataforma:

- Lee entradas, salidas, marcas, analógicas y DB del PLC por protocolo S7
  (python-snap7) y las publica en `ecs/<slug>/<variable>`, con el token del
  dispositivo y la unidad.
- Publica cuando el valor cambia (con banda muerta para las analógicas), y
  como latido cada `intervalo_publicacion_s` aunque no cambie.
- Escucha `ecs/<slug>/<variable>/set` (gadgets Botón/Slider) y escribe en el
  PLC solo las variables marcadas `escribible = true`; después publica el
  valor real leído para que el dashboard confirme.
- Opcional: copia la telemetría de la ESP32 a una variable del PLC (`[[puentes]]`).
- Publica la telemetría de la Raspberry: `cpu_temperatura`, `cpu_carga`,
  `memoria_uso`, `disco_uso` y `plc_en_linea`.
- Si se cae el PLC o el broker, reintenta solo. Los comandos que llegan con el
  PLC desconectado se descartan (no se ejecutan tarde).

| Archivo | Para qué |
|---|---|
| `gateway.py` | El gateway. `python gateway.py --config config.toml -v` |
| `config.example.toml` | Plantilla de configuración (copiar a `config.toml`). |
| `plc_simulador.py` | S7-1200 simulado para probar sin el PLC real. |
| `plataforma.sh` | Arranca/detiene web + listener + gateway sin Docker (`iniciar`, `simulado`, `detener`, `estado`). |
| `abrir_mqtt_red.sh` | Abre Mosquitto a la red con usuario y clave, para la ESP32 (`sudo`). |
| `iot-gateway.service` | Servicio systemd para que arranque solo (alternativa a `plataforma.sh`). |
| `requirements.txt` | `python-snap7` y `paho-mqtt`. Requiere Python 3.11+. |

Instalación, red, TIA Portal y ESP32: [docs/CONEXION-PLC-RASPBERRY-ESP32.md](../../docs/CONEXION-PLC-RASPBERRY-ESP32.md).
