"""Gateway de borde (Raspberry Pi): PLC Siemens S7-1200 <-> MQTT de la plataforma.

Lee las variables del PLC por protocolo S7 (python-snap7; requiere PUT/GET
habilitado en TIA Portal) y las publica siguiendo el "Contrato MQTT" del
README raíz:

    <prefix>/<slug>/<variable>        {"value": ..., "token": ..., "unit": ...}
    <prefix>/<slug>/<variable>/set    comandos de los gadgets Botón/Slider

Además publica la telemetría de la propia Raspberry (temperatura de CPU,
carga, memoria, disco y si el PLC está en línea) como un segundo dispositivo.

Uso:
    python gateway.py --config config.toml
"""

import argparse
import json
import logging
import os
import queue
import secrets
import shutil
import time
import tomllib

import paho.mqtt.client as mqtt
from snap7 import util
from snap7.client import Client
from snap7.error import S7ConnectionError, S7PacketLostError, S7TimeoutError
from snap7.type import Area

log = logging.getLogger("gateway")

AREAS = {"I": Area.PE, "Q": Area.PA, "M": Area.MK, "DB": Area.DB}
TYPE_SIZES = {"bool": 1, "byte": 1, "int": 2, "word": 2, "dint": 4, "real": 4}
CONNECTION_ERRORS = (S7ConnectionError, S7TimeoutError, S7PacketLostError, OSError)

# Una lectura S7 no puede superar la PDU del S7-1200 (240 bytes, ~222 útiles).
MAX_READ_SPAN = 200
PLC_RETRY_SECONDS = 5
# Una lectura rechazada (PUT/GET apagado, DB inexistente) se reintenta cada tanto:
# así basta con corregir el PLC en TIA Portal, sin reiniciar el gateway.
BAD_READ_RETRY_SECONDS = 30


class Tag:
    """Una variable del PLC (ej. I0.0, Q0.1, IW64, M10.0, DB1.DBD0)."""

    def __init__(self, cfg):
        self.variable = cfg["variable"]
        self.area = cfg["area"].upper()
        if self.area not in AREAS:
            raise ValueError(f"{self.variable}: área '{self.area}' no válida (usa I, Q, M o DB)")
        self.type = cfg.get("tipo", "bool").lower()
        if self.type not in TYPE_SIZES:
            raise ValueError(f"{self.variable}: tipo '{self.type}' no válido ({', '.join(TYPE_SIZES)})")
        self.db = int(cfg.get("db", 0))
        self.byte = int(cfg["byte"])
        self.bit = int(cfg.get("bit", 0))
        self.unit = cfg.get("unidad", "")
        self.writable = bool(cfg.get("escribible", False))
        # [crudo_min, crudo_max, valor_min, valor_max]: ej. [0, 27648, 0, 10] para 0-10 V
        scale = cfg.get("escala")
        self.scale = tuple(float(x) for x in scale) if scale else None
        self.deadband = float(cfg.get("banda_muerta", 0))

        self.last_value = None
        self.last_published_at = 0.0
        self.force_publish = False

    @property
    def size(self):
        return TYPE_SIZES[self.type]

    @property
    def address(self):
        if self.area == "DB":
            return f"DB{self.db}.{self.byte}" + (f".{self.bit}" if self.type == "bool" else "")
        return f"{self.area}{self.byte}" + (f".{self.bit}" if self.type == "bool" else f" ({self.type})")

    def decode(self, data, offset=0):
        if self.type == "bool":
            return 1.0 if util.get_bool(data, offset, self.bit) else 0.0
        if self.type == "byte":
            raw = util.get_byte(data, offset)
        elif self.type == "int":
            raw = util.get_int(data, offset)
        elif self.type == "word":
            raw = util.get_word(data, offset)
        elif self.type == "dint":
            raw = util.get_dint(data, offset)
        else:
            raw = util.get_real(data, offset)
        return round(self._to_engineering(float(raw)), 3)

    def encode(self, value, current):
        """Devuelve los bytes a escribir. `current` son los bytes actuales (para no pisar los otros bits)."""
        data = bytearray(current)
        if self.type == "bool":
            util.set_bool(data, 0, self.bit, value != 0)
            return data
        raw = self._to_raw(value)
        if self.type == "byte":
            util.set_byte(data, 0, int(round(raw)))
        elif self.type == "int":
            util.set_int(data, 0, int(round(raw)))
        elif self.type == "word":
            util.set_word(data, 0, int(round(raw)))
        elif self.type == "dint":
            util.set_dint(data, 0, int(round(raw)))
        else:
            util.set_real(data, 0, raw)
        return data

    def should_publish(self, value, now, heartbeat, min_interval):
        if self.force_publish or self.last_value is None:
            return True
        if now - self.last_published_at < min_interval:
            return False
        if abs(value - self.last_value) > self.deadband:
            return True
        return now - self.last_published_at >= heartbeat

    def mark_published(self, value, now):
        self.last_value = value
        self.last_published_at = now
        self.force_publish = False

    def _to_engineering(self, raw):
        if not self.scale:
            return raw
        raw_min, raw_max, eng_min, eng_max = self.scale
        return eng_min + (raw - raw_min) * (eng_max - eng_min) / (raw_max - raw_min)

    def _to_raw(self, value):
        if not self.scale:
            return value
        raw_min, raw_max, eng_min, eng_max = self.scale
        return raw_min + (value - eng_min) * (raw_max - raw_min) / (eng_max - eng_min)


class Plc:
    def __init__(self, cfg):
        self.ip = cfg["ip"]
        self.rack = int(cfg.get("rack", 0))
        self.slot = int(cfg.get("slot", 1))
        self.port = int(cfg.get("puerto", 102))
        self.tags = [Tag(t) for t in cfg.get("tags", [])]
        self.client = Client()
        self.online = False
        self._retry_at = 0.0
        self._groups = _group_tags(self.tags)
        self._bad_groups = {}  # grupo -> momento del próximo reintento

    def ensure_connected(self):
        if self.online:
            return True
        if time.monotonic() < self._retry_at:
            return False
        try:
            self.client.connect(self.ip, self.rack, self.slot, self.port)
        except Exception as exc:
            log.warning("No se pudo conectar al PLC %s:%s (%s). Reintento en %ss.",
                        self.ip, self.port, exc, PLC_RETRY_SECONDS)
            self._retry_at = time.monotonic() + PLC_RETRY_SECONDS
            return False
        self.online = True
        self._bad_groups.clear()
        log.info("PLC conectado: %s (rack %s, slot %s)", self.ip, self.rack, self.slot)
        return True

    def drop(self, reason):
        log.warning("Se perdió la comunicación con el PLC: %s", reason)
        try:
            self.client.disconnect()
        except Exception:
            pass
        self.online = False
        self._retry_at = time.monotonic() + PLC_RETRY_SECONDS

    def read_all(self):
        """Lee todas las variables agrupando por área para hacer pocas peticiones al PLC."""
        values = []
        for key, (start, size, tags) in self._groups.items():
            if time.monotonic() < self._bad_groups.get(key, 0):
                continue
            area, db = key[0], key[1]
            try:
                data = self.client.read_area(AREAS[area], db, start, size)
            except CONNECTION_ERRORS:
                raise
            except Exception as exc:
                # PUT/GET apagado, dirección inexistente, DB optimizado...: se avisa una vez,
                # se sigue con el resto y se reintenta cada BAD_READ_RETRY_SECONDS.
                if key not in self._bad_groups:
                    names = ", ".join(t.variable for t in tags)
                    log.error("No se pudo leer %s (bytes %s-%s): %s. Variables omitidas: %s. "
                              "¿PUT/GET habilitado en TIA Portal? (en un DB: ¿sin 'Optimized block "
                              "access'?). Reintento cada %ss.", _area_name(area, db), start,
                              start + size - 1, exc, names, BAD_READ_RETRY_SECONDS)
                self._bad_groups[key] = time.monotonic() + BAD_READ_RETRY_SECONDS
                continue
            if self._bad_groups.pop(key, None) is not None:
                log.info("Lectura de %s recuperada.", _area_name(area, db))
            for tag in tags:
                values.append((tag, tag.decode(data, tag.byte - start)))
        return values

    def write(self, tag, value):
        current = self.client.read_area(AREAS[tag.area], tag.db, tag.byte, tag.size)
        data = tag.encode(value, current)
        self.client.write_area(AREAS[tag.area], tag.db, tag.byte, data)
        log.info("Escrito %s (%s) = %s", tag.variable, tag.address, value)

    def writable_tag(self, variable):
        for tag in self.tags:
            if tag.variable == variable:
                return tag if tag.writable else None
        return None


def _group_tags(tags):
    """Agrupa variables de la misma área/DB en una sola lectura contigua (si el tramo es corto)."""
    groups = {}
    by_area = {}
    for tag in tags:
        by_area.setdefault((tag.area, tag.db), []).append(tag)
    for (area, db), area_tags in by_area.items():
        area_tags.sort(key=lambda t: t.byte)
        chunk = []
        for tag in area_tags:
            if chunk and tag.byte + tag.size - chunk[0].byte > MAX_READ_SPAN:
                _add_group(groups, area, db, chunk)
                chunk = []
            chunk.append(tag)
        if chunk:
            _add_group(groups, area, db, chunk)
    return groups


def _add_group(groups, area, db, tags):
    start = tags[0].byte
    end = max(t.byte + t.size for t in tags)
    groups[(area, db, start)] = (start, end - start, tags)


def _area_name(area, db):
    return f"DB{db}" if area == "DB" else f"área {area}"


def read_raspberry_metrics():
    """Telemetría de la propia Raspberry. En Windows (pruebas) solo devuelve lo disponible."""
    metrics = {}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            metrics["cpu_temperatura"] = (round(int(f.read().strip()) / 1000, 1), "°C")
    except (OSError, ValueError):
        pass
    if hasattr(os, "getloadavg"):
        metrics["cpu_carga"] = (round(os.getloadavg()[0], 2), "")
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                meminfo[key] = int(value.split()[0])
        used = 100 * (1 - meminfo["MemAvailable"] / meminfo["MemTotal"])
        metrics["memoria_uso"] = (round(used, 1), "%")
    except (OSError, KeyError, ValueError):
        pass
    disk = shutil.disk_usage("/")
    metrics["disco_uso"] = (round(100 * disk.used / disk.total, 1), "%")
    return metrics


class Platform:
    """Conexión MQTT con la plataforma. Los comandos recibidos se encolan para el lazo principal.

    `bridges` copia la telemetría de otro dispositivo (ej. la ESP32) a una variable
    escribible del PLC: {"<slug>/<variable>": {"token": ..., "destino": ...}}.
    """

    def __init__(self, cfg, plc_device, bridges=None):
        self.prefix = cfg.get("prefix", "ecs")
        self.plc_device = plc_device
        self.bridges = bridges or {}
        self.commands = queue.Queue()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.get("client_id") or f"gateway-{plc_device['slug']}",
        )
        if cfg.get("username"):
            self.client.username_pw_set(cfg["username"], cfg.get("password", ""))
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 1883))

    def start(self):
        # connect_async + loop_start: si el broker aún no está arriba, paho reintenta solo.
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def publish(self, device, variable, value, unit=""):
        payload = {"value": value, "token": device["token"]}
        if unit:
            payload["unit"] = unit
        # Sin "timestamp": la Raspberry no tiene reloj propio y en el laboratorio no hay
        # internet para NTP, así que es más confiable que el servidor selle la hora.
        topic = f"{self.prefix}/{device['slug']}/{variable}"
        self.client.publish(topic, json.dumps(payload), qos=1)
        log.debug("-> %s %s", topic, value)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("El broker MQTT rechazó la conexión: %s", reason_code)
            return
        topic = f"{self.prefix}/{self.plc_device['slug']}/+/set"
        client.subscribe(topic, qos=1)
        log.info("MQTT conectado a %s:%s, escuchando comandos en %s", self.host, self.port, topic)
        for source, bridge in self.bridges.items():
            client.subscribe(f"{self.prefix}/{source}", qos=1)
            log.info("Puente activo: %s -> PLC %s", source, bridge["destino"])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("MQTT desconectado (%s), reintentando...", reason_code)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) == 4 and parts[3] == "set":
            expected_token, target = self.plc_device["token"], parts[2]
        elif len(parts) == 3 and f"{parts[1]}/{parts[2]}" in self.bridges:
            bridge = self.bridges[f"{parts[1]}/{parts[2]}"]
            expected_token, target = bridge["token"], bridge["destino"]
        else:
            return
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("Mensaje con JSON inválido en %s, se ignora.", msg.topic)
            return
        if not secrets.compare_digest(str(data.get("token", "")), expected_token):
            log.warning("Mensaje con token inválido en %s, se ignora.", msg.topic)
            return
        value = data.get("value")
        if not isinstance(value, (int, float)):
            log.warning("Mensaje con valor no numérico en %s: %r", msg.topic, value)
            return
        self.commands.put((target, float(value)))


def load_config(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for section in ("mqtt", "plc"):
        if section not in cfg:
            raise SystemExit(f"Falta la sección [{section}] en {path}")
    for key in ("slug", "token", "ip"):
        if not cfg["plc"].get(key):
            raise SystemExit(f"Falta plc.{key} en {path}")
    return cfg


def run(cfg):
    gateway_cfg = cfg.get("gateway", {})
    read_interval = float(gateway_cfg.get("intervalo_lectura_s", 0.5))
    heartbeat = float(gateway_cfg.get("intervalo_publicacion_s", 5))
    min_interval = float(gateway_cfg.get("intervalo_minimo_s", 1))

    plc_device = {"slug": cfg["plc"]["slug"], "token": cfg["plc"]["token"]}
    rpi_cfg = cfg.get("raspberry")
    rpi_device = {"slug": rpi_cfg["slug"], "token": rpi_cfg["token"]} if rpi_cfg else None
    rpi_interval = float(rpi_cfg.get("intervalo_s", 10)) if rpi_cfg else 0

    bridges = {
        b["origen"]: {"token": b["token_origen"], "destino": b["destino"]}
        for b in cfg.get("puentes", [])
    }

    plc = Plc(cfg["plc"])
    for bridge in bridges.values():
        if plc.writable_tag(bridge["destino"]) is None:
            raise SystemExit(f"Puente: '{bridge['destino']}' no existe en [[plc.tags]] o no es escribible")
    platform = Platform(cfg["mqtt"], plc_device, bridges)
    platform.start()
    log.info("Gateway iniciado: %s variables del PLC, lectura cada %ss", len(plc.tags), read_interval)

    next_rpi_at = 0.0
    while True:
        cycle_start = time.monotonic()

        if plc.ensure_connected():
            try:
                _apply_commands(plc, platform)
                now = time.monotonic()
                for tag, value in plc.read_all():
                    if tag.should_publish(value, now, heartbeat, min_interval):
                        platform.publish(plc_device, tag.variable, value, tag.unit)
                        tag.mark_published(value, now)
            except CONNECTION_ERRORS as exc:
                plc.drop(exc)
        else:
            _discard_commands(platform)

        if rpi_device and cycle_start >= next_rpi_at:
            next_rpi_at = cycle_start + rpi_interval
            for variable, (value, unit) in read_raspberry_metrics().items():
                platform.publish(rpi_device, variable, value, unit)
            platform.publish(rpi_device, "plc_en_linea", 1.0 if plc.online else 0.0)

        time.sleep(max(0.0, read_interval - (time.monotonic() - cycle_start)))


def _apply_commands(plc, platform):
    while True:
        try:
            variable, value = platform.commands.get_nowait()
        except queue.Empty:
            return
        tag = plc.writable_tag(variable)
        if tag is None:
            log.warning("Comando para '%s' ignorado: no existe o no está marcada como escribible.", variable)
            continue
        plc.write(tag, value)
        tag.force_publish = True  # el dashboard confirma con el valor real leído del PLC


def _discard_commands(platform):
    # Con el PLC desconectado los comandos se descartan: ejecutar un "partir motor"
    # viejo al reconectar sería peligroso.
    while True:
        try:
            variable, value = platform.commands.get_nowait()
        except queue.Empty:
            return
        log.warning("PLC fuera de línea: se descarta el comando %s = %s", variable, value)


def main():
    parser = argparse.ArgumentParser(description="Gateway PLC S7-1200 <-> MQTT (plataforma IoT)")
    parser.add_argument("--config", default="config.toml", help="ruta al archivo de configuración")
    parser.add_argument("-v", "--verbose", action="store_true", help="muestra cada publicación")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("snap7").setLevel(logging.WARNING)
    try:
        run(load_config(args.config))
    except KeyboardInterrupt:
        log.info("Gateway detenido.")


if __name__ == "__main__":
    main()
