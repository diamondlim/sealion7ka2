#!/usr/bin/env python3
"""Watch the car's own ACC button presses on the CAN bus, live and read-only.

Listens on the `can` cereal channel (published by card) and reports every press of 0x3B0 PCM_BUTTONS:
which bit lit, how long it was held, which bus, and the raw bytes. Pure listener - it never transmits
anything, so it is safe in a car that is driving.

Bit layout and hold length come from the port's own decoder, opendbc/car/byd/acc_button.py, which was
written against this car's live CAN: byte0 bit3 SET, bit4 RES, bit6 LKAS_ON, byte1 bit7 DEC_DISTANCE,
byte2 bit0 INC_DISTANCE, byte2 bit3 ACC_ON/cancel. The car's own button module transmits the same frame
the box would, so a press seen here cannot be attributed to driver or box by bits alone - only by knowing
what was sent. Byte0 bit2 and byte1 bit4 are constants and are not reported.

Usage (on the box, as the device user, with cereal importable):

  nohup /usr/local/venv/bin/python3 /data/hermes/ka2_acc_button_watch.py --seconds 900 \
      > /data/hermes/logs/ka2_acc_button_watch.log 2>&1 &
"""
import argparse
import json
import sys
import time

sys.path.insert(0, "/data/openpilot")

from cereal import log as capnp_log
from cereal import messaging  # noqa: E402

ADDR_PCM_BUTTONS = 0x3B0
BUS = None                    # None = every bus; set to 0 to watch only the bus the car's module uses
BITS = {
    (0, 3): "set",
    (0, 4): "res",
    (0, 6): "lkas_on",
    (1, 7): "dec_dist",
    (2, 0): "inc_dist",
    (2, 3): "acc_on_cancel",
}


def decode(data):
    return [name for (byte, bit), name in BITS.items() if len(data) > byte and (data[byte] >> bit) & 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--log", default="/data/hermes/acc/button_watch.jsonl")
    args = ap.parse_args()

    sm = messaging.SubMaster(["can"])
    held = {}
    presses = 0
    t0 = time.time()
    print("%s  listening for 0x3B0 presses for %.0fs - press the buttons now" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), args.seconds), flush=True)

    while time.time() - t0 < args.seconds:
        sm.update(50)
        if not sm.updated["can"]:
            continue
        try:
            for frame in sm["can"]:
                if frame.address != ADDR_PCM_BUTTONS:
                    continue
                if BUS is not None and frame.src != BUS:
                    continue
                data = bytes(frame.dat)
                names = decode(data)
                key = (frame.src, frame.address)
                if names:
                    if key not in held:
                        held[key] = [names, time.time(), 1, data]
                        presses += 1
                        print("%s  PRESS  %-16s bus=%d  bytes=%s" % (
                            time.strftime("%H:%M:%S"), "+".join(names), frame.src, data.hex()), flush=True)
                        with open(args.log, "a") as handle:
                            handle.write(json.dumps({
                                "t": time.time(), "button": "+".join(names), "bus": frame.src,
                                "bytes": data.hex(), "release_t": None, "frames": None,
                            }) + "\n")
                    else:
                        held[key][2] += 1
                        held[key][3] = data
                elif key in held:
                    names, started, frames, first = held.pop(key)
                    millis = frames * 10
                    print("%s  release %-16s held %d frames (~%d ms)  first=%s last=%s" % (
                        time.strftime("%H:%M:%S"), "+".join(names), frames, millis,
                        first.hex(), data.hex()), flush=True)
                    with open(args.log, "a") as handle:
                        handle.write(json.dumps({
                            "t": started, "button": "+".join(names), "bus": key[0],
                            "bytes": first.hex(), "release_t": time.time(), "frames": frames,
                        }) + "\n")
        except Exception as exc:      # a malformed message must not end the watch
            print("  (skipped a message: %s)" % exc, flush=True)

    print("%s  done: %d presses seen" % (time.strftime("%Y-%m-%d %H:%M:%S"), presses), flush=True)


if __name__ == "__main__":
    sys.exit(main())
