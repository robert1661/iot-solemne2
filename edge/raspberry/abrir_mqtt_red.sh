#!/usr/bin/env bash
# Abre el Mosquitto de la Raspberry a la red (para que la ESP32 pueda publicar),
# exigiendo el usuario y la clave MQTT_USERNAME / MQTT_PASSWORD del .env del repo.
# Por defecto Mosquitto 2 solo escucha en la propia Raspberry (127.0.0.1).
#
#   sudo bash abrir_mqtt_red.sh

set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ejecútalo con sudo: sudo bash $0"
    exit 1
fi

MQTT_USERNAME="$(grep -E '^MQTT_USERNAME=' "$REPO/.env" | cut -d= -f2-)"
MQTT_PASSWORD="$(grep -E '^MQTT_PASSWORD=' "$REPO/.env" | cut -d= -f2-)"
if [ -z "$MQTT_USERNAME" ] || [ -z "$MQTT_PASSWORD" ]; then
    echo "Faltan MQTT_USERNAME o MQTT_PASSWORD en $REPO/.env"
    exit 1
fi

cat >/etc/mosquitto/conf.d/iot-solemne2.conf <<EOF
# Plataforma IoT (iot-solemne2): escucha en toda la red (IPv4 e IPv6), solo con usuario y clave.
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd
EOF

mosquitto_passwd -c -b /etc/mosquitto/passwd "$MQTT_USERNAME" "$MQTT_PASSWORD"
chown root:mosquitto /etc/mosquitto/passwd
chmod 640 /etc/mosquitto/passwd
systemctl restart mosquitto
sleep 1
systemctl is-active mosquitto && echo "Mosquitto escuchando en el puerto 1883 de toda la red, con usuario '$MQTT_USERNAME'."
