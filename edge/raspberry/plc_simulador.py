"""Simulador de un S7-1200 para probar el gateway sin el PLC real.

Levanta un servidor S7 (python-snap7) con las mismas direcciones que usa
config.example.toml:

    I0.0          pulsador que cambia cada 3 s
    I0.1          sensor que se activa cuando la analógica supera 7 V
    IW64 / IW66   analógicas 0-10 V (0-27648) con una onda lenta
    M10.0         marca de mando remoto (la escribe el dashboard)
    Q0.0          "programa": Q0.0 = M10.0     Q0.1 = I0.1
    DB1           100 bytes para setpoints

Uso:
    python plc_simulador.py               (puerto 1102, sin permisos de root)
    en config.toml:  ip = "127.0.0.1"  y  puerto = 1102
"""

import argparse
import logging
import math
import time

from snap7 import util
from snap7.server import Server
from snap7.type import SrvArea

log = logging.getLogger("plc_simulador")


def main():
    parser = argparse.ArgumentParser(description="Simulador S7-1200 para pruebas")
    parser.add_argument("--puerto", type=int, default=1102)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("snap7").setLevel(logging.WARNING)

    inputs = bytearray(128)
    outputs = bytearray(128)
    marks = bytearray(128)
    db1 = bytearray(100)

    server = Server(log=False)
    server.register_area(SrvArea.PE, 0, inputs)
    server.register_area(SrvArea.PA, 0, outputs)
    server.register_area(SrvArea.MK, 0, marks)
    server.register_area(SrvArea.DB, 1, db1)
    server.start(tcp_port=args.puerto)
    log.info("PLC simulado escuchando en el puerto %s (Ctrl+C para salir)", args.puerto)

    start = time.monotonic()
    last_q00 = None
    try:
        while True:
            t = time.monotonic() - start
            ai0 = 5 + 4 * math.sin(t / 10)
            ai1 = 2.5 + 2 * math.sin(t / 7)

            util.set_bool(inputs, 0, 0, int(t // 3) % 2 == 1)
            util.set_bool(inputs, 0, 1, ai0 > 7)
            util.set_int(inputs, 64, int(ai0 / 10 * 27648))
            util.set_int(inputs, 66, int(ai1 / 10 * 27648))

            # "Programa" del PLC: la salida sigue a la marca de mando remoto.
            q00 = util.get_bool(marks, 10, 0)
            util.set_bool(outputs, 0, 0, q00)
            util.set_bool(outputs, 0, 1, util.get_bool(inputs, 0, 1))
            if q00 != last_q00:
                log.info("M10.0 = %s  ->  Q0.0 = %s", q00, q00)
                last_q00 = q00

            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        server.destroy()


if __name__ == "__main__":
    main()
