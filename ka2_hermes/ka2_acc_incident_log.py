#!/usr/bin/env python3
"""Correlate the car's ACC drop-outs with everything that can cause them.

One timeline from four sources, because the ACC can be moved by any of them:
  * the app's own commands          /data/hermes/bt_audit.log            (tailed)
  * every button press made on the car   /data/hermes/acc_presses.jsonl  (tailed)
  * the automatic speed tool        /data/hermes/acc/vision_acc.jsonl    (tailed)
  * the car's ACC state, the press frames on the bus, and the box's own transmissions
    (cereal: carState, can, sendcan, selfdriveState)

On every ACC or selfdrive state change it prints the preceding seconds of the merged timeline, so the
cause is visible instead of guessed at. Read-only: it subscribes, it never transmits.

Run on the box as the device user (msgq refuses root):

  setsid nohup /usr/local/venv/bin/python3 /data/hermes/ka2_acc_incident_log.py --hours 6 \
      > /data/hermes/acc/incidents.log 2>&1 < /dev/null &
"""
import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, "/data/openpilot")

from cereal import messaging  # noqa: E402

ADDR_BUTTONS = 0x3B0
BITS = {(0, 3): "set", (0, 4): "res", (0, 6): "lkas", (1, 7): "dec_dist", (2, 0): "inc_dist", (2, 3): "cancel"}
TAILS = {
    "app": "/data/hermes/bt_audit.log",
    "press": "/data/hermes/acc_presses.jsonl",
    "vision": "/data/hermes/acc/vision_acc.jsonl",
}
OUT_JSONL = "/data/hermes/acc/incidents.jsonl"
DEDUPE_S = 0.15          # a press spans several frames on two buses - report it once


class Tailer:
    """Follow a file, starting at its end, tolerating rotation and unreadable files."""

    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.ino = None
        try:
            st = os.stat(path)
            self.pos, self.ino = st.st_size, st.st_ino
        except OSError:
            pass

    def read(self):
        try:
            st = os.stat(self.path)
            if self.ino != st.st_ino or st.st_size < self.pos:
                self.pos, self.ino = 0, st.st_ino
            if st.st_size == self.pos:
                return []
            with open(self.path, "r", errors="replace") as handle:
                handle.seek(self.pos)
                data = handle.read()
                self.pos = handle.tell()
            return [line.strip() for line in data.splitlines() if line.strip()]
        except OSError:
            return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--jsonl", default=OUT_JSONL)
    args = ap.parse_args()

    sm = messaging.SubMaster(["carState", "can", "sendcan", "selfdriveState"])
    tails = {name: Tailer(path) for name, path in TAILS.items()}
    buf = collections.deque(maxlen=500)
    last_seen = {}
    prev = {}

    def emit(kind, **fields):
        rec = {"t": round(time.time(), 3), "kind": kind, "at": time.strftime("%H:%M:%S")}
        rec.update(fields)
        buf.append(rec)
        try:
            with open(args.jsonl, "a") as handle:
                handle.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        return rec

    def dump_context(headline):
        print("\n===== %s =====" % headline, flush=True)
        for rec in list(buf)[-22:]:
            print("  %s %-14s %s" % (rec["at"], rec["kind"],
                                     " ".join("%s=%s" % (k, v) for k, v in rec.items()
                                              if k not in ("t", "at", "kind"))[:220]), flush=True)

    deadline = time.time() + args.hours * 3600
    print("%s  logging ACC incidents until %s (read-only)" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), time.strftime("%H:%M", time.localtime(deadline))), flush=True)

    while time.time() < deadline:
        sm.update(50)

        for name, tail in tails.items():
            for line in tail.read():
                emit({"app": "APP", "press": "PRESS", "vision": "VISION"}[name], line=line[:400])

        if sm.updated["carState"]:
            cs = sm["carState"]
            state = (bool(cs.cruiseState.enabled), bool(cs.cruiseState.available))
            if prev.get("cs") != state:
                rec = emit("ACC_ON" if state[0] else "ACC_OFF",
                           set_kmh=round(float(cs.cruiseState.speedCluster) * 3.6, 1),
                           v_kmh=round(float(cs.vEgo) * 3.6, 1),
                           brake=bool(cs.brakePressed), gas=bool(cs.gasPressed),
                           gear=str(cs.gearShifter), available=state[1])
                if prev.get("cs") is not None:
                    dump_context("%s at %s (set %s kph, vEgo %s, brake %s)" % (
                        rec["kind"], rec["at"], rec["set_kmh"], rec["v_kmh"], rec["brake"]))
                prev["cs"] = state

        if sm.updated["can"]:
            for frame in sm["can"]:
                if frame.address != ADDR_BUTTONS:
                    continue
                data = bytes(frame.dat)
                names = [n for (b, bit), n in sorted(BITS.items()) if len(data) > b and (data[b] >> bit) & 1]
                if not names:
                    continue
                key = (frame.src, "+".join(names))
                if time.time() - last_seen.get(key, 0) < DEDUPE_S:
                    continue
                last_seen[key] = time.time()
                emit("BUS_PRESS", who={0: "driver", 128: "box", 130: "mirror"}.get(frame.src, "bus%d" % frame.src),
                     buttons="+".join(names), bus=frame.src, bytes=data.hex())

        if sm.updated["sendcan"]:
            for frame in sm["sendcan"]:
                if frame.address != ADDR_BUTTONS:
                    continue
                data = bytes(frame.dat)
                names = [n for (b, bit), n in sorted(BITS.items()) if len(data) > b and (data[b] >> bit) & 1]
                emit("BOX_TX", buttons="+".join(names) or "idle", bytes=data.hex(),
                     low_byte_ok=(sum(data) & 0xFF) == 0xFF,
                     counter=(data[6] >> 4) if len(data) > 6 else None)

        if sm.updated["selfdriveState"]:
            ss = sm["selfdriveState"]
            enabled = bool(getattr(ss, "enabled", False))
            if prev.get("ss") is not None and enabled != prev["ss"]:
                rec = emit("SELFDRIVE_ON" if enabled else "SELFDRIVE_OFF",
                           state=str(getattr(ss, "state", "")),
                           alert=str(getattr(ss, "alertText1", ""))[:120])
                dump_context("selfdrive %s at %s" % (rec["kind"], rec["at"]))
            prev["ss"] = enabled

    print("%s  stopped" % time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)


if __name__ == "__main__":
    sys.exit(main())
