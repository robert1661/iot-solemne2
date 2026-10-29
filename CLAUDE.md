# iot-solemne2 en esta Raspberry: estado y reglas

Esta carpeta también se maneja por SSH desde el notebook de Ignacio con otro
Claude Code. **Que una sola persona o agente cambie cosas a la vez.**

## Qué está corriendo (sin Docker)

- Django con gunicorn en el puerto 8000, `mqtt_listener` y el gateway del PLC
  (`edge/raspberry/gateway.py`). Se manejan **solo** con
  `edge/raspberry/plataforma.sh iniciar | simulado | detener | reiniciar | estado`.
  Logs en `logs/`. Arranca sola con el crontab (`@reboot`).
- Mosquitto es el del sistema (puerto 1883), con el usuario y la clave de `.env`
  (`edge/raspberry/abrir_mqtt_red.sh`).
- Base de datos SQLite: `backend/db.sqlite3` (`DB_ENGINE=sqlite` en `.env`).
  El usuario del dashboard es `admin`; su clave está en `.env`.
- Dashboard: `http://<ip-wlan0>:8000/monitoring/`.

## Red

- `wlan0`: WiFi UNAB-WIFI6-Academica por DHCP (suele ser 10.41.213.89).
- `eth0`: conexión NetworkManager `plc-lan`, IP fija 10.41.32.60/24 sin puerta
  de enlace, hacia el PLC.
- PLC S7-1200 (CPU 1214C): 10.41.32.11, rack 0, slot 1, PUT/GET habilitado.
  CBOX Haiwell: 10.41.32.52.

## Variables del PLC (`edge/raspberry/config.toml`)

Se leen I0.0, I0.1, Q0.0, Q0.1, Q0.2, AI0 (IW64), AI1 (IW66) y M10.0.
La **única escribible** es `mando_q0_2` = **M0.2** (botón del dashboard). En el
programa del PLC va `M0.2 --| |-- ( Q0.2 )`. Q0.2 la maneja el programa, así
que no se escribe directo.

## Reglas

1. No levantar un segundo gateway, Django ni `docker compose`: choca con los
   puertos 8000 y 1883 y duplica lecturas. Para reiniciar, `plataforma.sh`.
2. No tocar la IP de `eth0` ni la conexión `plc-lan`.
3. No escribir marcas M10.x en el PLC: las usa el programa del grupo. Escribir
   solo lo que tenga `escribible = true` en `config.toml`.
4. `.env` y `edge/raspberry/config.toml` tienen claves y tokens: no subirlos a
   git ni mostrarlos.
5. Antes de cambiar el programa del PLC o la red, avisar al grupo.

Guía completa: `docs/CONEXION-PLC-RASPBERRY-ESP32.md`.
