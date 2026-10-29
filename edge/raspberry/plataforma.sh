#!/usr/bin/env bash
# Arranca o detiene la plataforma en la Raspberry SIN Docker:
#   web (Django con gunicorn en el puerto 8000), mqtt_listener y el gateway del PLC.
# Mosquitto corre aparte, como servicio del sistema.
#
#   ./plataforma.sh iniciar | detener | reiniciar | estado
#   ./plataforma.sh simulado     igual que iniciar, pero con plc_simulador.py en vez del PLC real
#
# Para que arranque sola al prender la Raspberry (no necesita sudo):
#   (crontab -l 2>/dev/null; echo "@reboot sleep 30 && $HOME/iot-solemne2/edge/raspberry/plataforma.sh iniciar") | crontab -

set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LOGS="$REPO/logs"
PIDS="$LOGS/pids"
mkdir -p "$PIDS"
export PYTHONUNBUFFERED=1

is_running() {
    [ -f "$PIDS/$1.pid" ] && kill -0 "$(cat "$PIDS/$1.pid")" 2>/dev/null
}

start_one() {  # nombre directorio comando...
    local name="$1" dir="$2"
    shift 2
    if is_running "$name"; then
        echo "$name ya estaba corriendo (pid $(cat "$PIDS/$name.pid"))"
        return
    fi
    # Solo el programa va a segundo plano (así $! es su PID real) y sin la entrada/salida
    # de la sesión: si no, una sesión SSH queda colgada esperando que termine.
    (
        cd "$dir" || exit 1
        nohup "$@" </dev/null >>"$LOGS/$name.log" 2>&1 &
        echo $! >"$PIDS/$name.pid"
    )
    echo "$name iniciado (pid $(cat "$PIDS/$name.pid")) -> logs/$name.log"
}

stop_one() {
    if is_running "$1"; then
        kill "$(cat "$PIDS/$1.pid")" && echo "$1 detenido"
    fi
    rm -f "$PIDS/$1.pid"
}

start_all() {  # archivo de configuración del gateway
    start_one web "$REPO/backend" \
        "$REPO/backend/.venv/bin/gunicorn" config.wsgi:application --bind 0.0.0.0:8000 --workers 2
    start_one listener "$REPO/backend" \
        "$REPO/backend/.venv/bin/python" manage.py run_mqtt_listener
    start_one gateway "$REPO/edge/raspberry" \
        "$REPO/edge/raspberry/.venv/bin/python" gateway.py --config "$1"
}

start_simulated() {
    # Misma configuración, pero apuntando al PLC simulado de la propia Raspberry.
    local dir="$REPO/edge/raspberry"
    sed -e 's/^ip = .*/ip = "127.0.0.1"/' -e 's/^puerto = .*/puerto = 1102/' \
        "$dir/config.toml" >"$dir/config_simulador.toml"
    chmod 600 "$dir/config_simulador.toml"
    start_one simulador "$dir" "$dir/.venv/bin/python" plc_simulador.py --puerto 1102
    sleep 2
    start_all config_simulador.toml
}

stop_all() {
    stop_one gateway
    stop_one simulador
    stop_one listener
    stop_one web
}

case "${1:-estado}" in
    iniciar) start_all config.toml ;;
    simulado) start_simulated ;;
    detener) stop_all ;;
    reiniciar) stop_all; sleep 2; start_all config.toml ;;
    estado)
        for name in web listener gateway simulador; do
            if is_running "$name"; then echo "$name: corriendo (pid $(cat "$PIDS/$name.pid"))"; else echo "$name: detenido"; fi
        done
        ;;
    *) echo "Uso: $0 iniciar | simulado | detener | reiniciar | estado"; exit 1 ;;
esac
