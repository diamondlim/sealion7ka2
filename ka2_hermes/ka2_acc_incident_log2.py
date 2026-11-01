#!/usr/bin/env python3
"""Catch the moment the ACC disengages, with everything around it.

Timeline from four moving parts (the app's commands, every button press, the automatic speed tool, the
car's own state), plus a rolling buffer of the raw CAN. When the ACC or selfdrive changes state it writes
a per-incident directory holding:

  context.jsonl   the last ~60 merged events before the change
  state.json      a full snapshot: carState, selfdriveState, controlsState, panda state, device state,
                  pose validity, and the openpilot route directory + log file names
  can_window.jsonl every CAN frame in the rolling buffer (bus, address, bytes, timestamp)
  summary.txt     one readable paragraph

It also watches the panda: a safetyModel/controlsAllowed change (arming, or losing the CAN interface) is
logged, because that is what makes openpilot unable to steer even while every process runs.

And it verifies presses: every press is followed 2.5 s later by a PRESS_RESULT recording the real set-speed
delta, so a press that moves the wrong way cannot hide the way the inverted button did.

Read-only: it subscribes and tails logs, it never transmits.

Run on the box as the device user (msgq refuses root):

  setsid nohup /usr/local/venv/bin/python3 /data/hermes/ka2_acc_incident_log2.py --hours 6 \
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
ADDR_ACC_CMD = 0x32E
BITS = {(0, 3): "set", (0, 4): "res", (0, 6): "lkas", (1, 7): "dec_dist", (2, 0): "inc_dist", (2, 3): "cancel"}
TAILS = {
    "app": "/data/hermes/bt_audit.log",
    "press": "/data/hermes/acc_presses.jsonl",
    "vision": "/data/hermes/acc/vision_acc.jsonl",
}
OUT_DIR = "/data/hermes/acc"
RING_FRAMES = 10000         # ~5 s of bus traffic at this box's rate (~5000 frames/s)
CONTEXT_EVENTS = 60
PRESS_VERIFY_S = 2.5


class Tailer:
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


def route_dir():
    """The newest openpilot route directory and the log files in it, if logging is running."""
    try:
        base = "/data/media/0/realdata"
        routes = sorted((os.path.join(base, d) for d in os.listdir(base)), key=os.path.getmtime)
        if not routes:
            return {}
        newest = routes[-1]
        return {"route": newest, "files": sorted(os.listdir(newest))[:20],
                "age_s": round(time.time() - os.path.getmtime(newest), 1)}
    except OSError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--outdir", default=OUT_DIR)
    args = ap.parse_args()

    sm = messaging.SubMaster(["carState", "can", "sendcan", "selfdriveState", "controlsState",
                              "pandaStates", "deviceState", "livePose"])
    tails = {name: Tailer(path) for name, path in TAILS.items()}
    buf = collections.deque(maxlen=600)
    ring = collections.deque(maxlen=RING_FRAMES)
    last_seen = {}
    pending = []            # presses awaiting their set-speed verdict
    prev = {}
    incidents = 0
    last_set_kmh = None

    def emit(kind, **fields):
        rec = {"t": round(time.time(), 3), "at": time.strftime("%H:%M:%S"), "kind": kind}
        rec.update(fields)
        buf.append(rec)
        try:
            with open(os.path.join(args.outdir, "incidents.jsonl"), "a") as handle:
                handle.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        return rec

    def capture(headline, snap):
        """Write a per-incident directory: context, state, the raw CAN window, a summary."""
        nonlocal incidents
        incidents += 1
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(args.outdir, "incident_%s" % stamp)
        try:
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "context.jsonl"), "w") as handle:
                for rec in list(buf)[-CONTEXT_EVENTS:]:
                    handle.write(json.dumps(rec) + "\n")
            with open(os.path.join(path, "state.json"), "w") as handle:
                json.dump(snap, handle, indent=1)
            with open(os.path.join(path, "can_window.jsonl"), "w") as handle:
                for t, bus, addr, data in ring:
                    handle.write(json.dumps({"t": t, "bus": bus, "addr": "0x%03X" % addr, "bytes": data}) + "\n")
            with open(os.path.join(path, "summary.txt"), "w") as handle:
                handle.write(headline + "\n\n")
                handle.write("route: %s\n" % json.dumps(snap.get("route", {})))
                handle.write("\nrecent timeline:\n")
                for rec in list(buf)[-CONTEXT_EVENTS:]:
                    handle.write("  %s %-14s %s\n" % (rec["at"], rec["kind"],
                                                      " ".join("%s=%s" % (k, v) for k, v in rec.items()
                                                               if k not in ("t", "at", "kind"))[:200]))
            print("\n*** INCIDENT %d: %s" % (incidents, headline), flush=True)
            print("    captured to %s (context.jsonl, state.json, can_window.jsonl)" % path, flush=True)
        except OSError as exc:
            print("  capture failed: %s" % exc, flush=True)

    def snapshot():
        snap = {"route": route_dir()}
        try:
            cs = sm["carState"]
            snap["carState"] = {"vEgo_kph": round(float(cs.vEgo) * 3.6, 2), "gear": str(cs.gearShifter),
                                "brake": bool(cs.brakePressed), "gas": bool(cs.gasPressed),
                                "cruise_available": bool(cs.cruiseState.available),
                                "cruise_enabled": bool(cs.cruiseState.enabled),
                                "cruise_set_kph": round(float(cs.cruiseState.speedCluster) * 3.6, 1)}
        except Exception as exc:
            snap["carState"] = "unreadable: %s" % exc
        try:
            ss = sm["selfdriveState"]
            snap["selfdriveState"] = {"enabled": bool(ss.enabled), "state": str(ss.state),
                                      "alert1": str(getattr(ss, "alertText1", ""))[:120]}
        except Exception:
            snap["selfdriveState"] = "unreadable"
        try:
            ctl = sm["controlsState"]
            snap["controlsState"] = {"state": str(getattr(ctl, "state", "")),
                                     "enabled": bool(getattr(ctl, "enabled", False)),
                                     "active": bool(getattr(ctl, "active", False))}
        except Exception:
            snap["controlsState"] = "unreadable"
        try:
            snap["panda"] = [{"ignitionLine": bool(p.ignitionLine), "ignitionCan": bool(p.ignitionCan),
                              "safetyModel": str(p.safetyModel), "controlsAllowed": bool(p.controlsAllowed)}
                             for p in sm["pandaStates"]]
        except Exception:
            snap["panda"] = "unreadable"
        try:
            snap["deviceState"] = {"started": bool(sm["deviceState"].started),
                                   "thermal": str(sm["deviceState"].thermalStatus)}
        except Exception:
            snap["deviceState"] = "unreadable"
        try:
            snap["livePose_valid"] = bool(sm["livePose"].valid)
        except Exception:
            snap["livePose_valid"] = "unreadable"
        return snap

    deadline = time.time() + args.hours * 3600
    print("%s  watching for ACC disengages until %s (read-only, incident capture on)" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), time.strftime("%H:%M", time.localtime(deadline))), flush=True)

    while time.time() < deadline:
        sm.update(50)

        for name, tail in tails.items():
            for line in tail.read():
                emit({"app": "APP", "press": "PRESS", "vision": "VISION"}[name], line=line[:400])
                if name in ("press", "vision") and last_set_kmh is not None:
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    pending.append((time.time() + PRESS_VERIFY_S,
                                    payload.get("button") or payload.get("kind"), last_set_kmh))

        if sm.updated["carState"]:
            cs = sm["carState"]
            last_set_kmh = round(float(cs.cruiseState.speedCluster) * 3.6, 1)
            state = (bool(cs.cruiseState.enabled), bool(cs.cruiseState.available))
            if prev.get("cs") != state:
                headline = "ACC %s at %s (set %s kph, vEgo %s, brake %s, gear %s)" % (
                    "ENGAGED" if state[0] else "DISENGAGED", time.strftime("%H:%M:%S"), last_set_kmh,
                    round(float(cs.vEgo) * 3.6, 1), bool(cs.brakePressed), str(cs.gearShifter))
                emit("ACC_ON" if state[0] else "ACC_OFF", set_kmh=last_set_kmh,
                     v_kmh=round(float(cs.vEgo) * 3.6, 1), brake=bool(cs.brakePressed),
                     gas=bool(cs.gasPressed), gear=str(cs.gearShifter), available=state[1])
                if prev.get("cs") is not None and not state[0]:
                    capture(headline, snapshot())
                prev["cs"] = state

        if sm.updated["can"]:
            now = time.time()
            for frame in sm["can"]:
                data = bytes(frame.dat)
                ring.append((round(now, 3), frame.src, frame.address, data.hex()))
                if frame.address != ADDR_BUTTONS:
                    continue
                names = [n for (b, bit), n in sorted(BITS.items()) if len(data) > b and (data[b] >> bit) & 1]
                if not names:
                    continue
                key = (frame.src, "+".join(names))
                if now - last_seen.get(key, 0) < 0.15:
                    continue
                last_seen[key] = now
                emit("BUS_PRESS", who={0: "driver", 128: "box", 130: "mirror"}.get(frame.src, "bus%d" % frame.src),
                     buttons="+".join(names), bus=frame.src, bytes=data.hex())

        if sm.updated["sendcan"]:
            for frame in sm["sendcan"]:
                if frame.address != ADDR_BUTTONS:
                    continue
                data = bytes(frame.dat)
                names = [n for (b, bit), n in sorted(BITS.items()) if len(data) > b and (data[b] >> bit) & 1]
                emit("BOX_TX", buttons="+".join(names) or "idle", bytes=data.hex(),
                     low_byte_ok=(sum(data) & 0xFF) == 0xFF, counter=(data[6] >> 4))

        if sm.updated["pandaStates"]:
            for p in sm["pandaStates"]:
                key = (str(p.safetyModel), bool(p.controlsAllowed))
                if prev.get("panda") != key:
                    emit("PANDA", safetyModel=str(p.safetyModel), controlsAllowed=bool(p.controlsAllowed),
                         ignitionLine=bool(p.ignitionLine), ignitionCan=bool(p.ignitionCan))
                    if prev.get("panda") is not None:
                        print("  ** CAN interface changed: safetyModel=%s controlsAllowed=%s" % key, flush=True)
                    prev["panda"] = key

        if sm.updated["selfdriveState"]:
            ss = sm["selfdriveState"]
            enabled = bool(getattr(ss, "enabled", False))
            if prev.get("ss") is not None and enabled != prev["ss"]:
                emit("SELFDRIVE_ON" if enabled else "SELFDRIVE_OFF", state=str(getattr(ss, "state", "")),
                     alert=str(getattr(ss, "alertText1", ""))[:120])
            prev["ss"] = enabled

        now = time.time()
        while pending and pending[0][0] <= now:
            _, button, before = pending.pop(0)
            after = last_set_kmh
            moved = None if (before is None or after is None) else round(after - before, 1)
            emit("PRESS_RESULT", button=button, set_before=before, set_after=after, delta_kmh=moved,
                 verdict=("no change" if moved == 0 else ("raised" if (moved or 0) > 0 else "lowered")))

    print("%s  stopped (%d incidents captured)" % (time.strftime("%Y-%m-%d %H:%M:%S"), incidents), flush=True)


if __name__ == "__main__":
    sys.exit(main())
