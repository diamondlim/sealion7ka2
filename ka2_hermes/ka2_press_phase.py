#!/usr/bin/env python3
"""Measure the phase between the box's ACC button bursts and the car's own 0x3B0 stream.

Read-only. For every box burst (0x3B0 seen on the TX bus) it records:

  * the gap in ms from the car's last own 0x3B0 frame to the burst's first frame
  * how many car frames land *inside* the burst (a near-collision)
  * how many frames the burst held, and which buttons
  * whether the car's ACC command (0x32E byte0) drops to 0x64 within 3 s

One JSON line per press to /data/hermes/acc/press_phase.jsonl, so a press that drops the ACC
and a press that does not can be compared directly - the frames themselves are identical, so
the phase is the only thing that can explain why one bites and the other does not.

  /usr/local/venv/bin/python3 /data/hermes/ka2_press_phase.py --hours 6
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, "/data/openpilot")

ADDR_BUTTONS = 0x3B0
ADDR_ACC_CMD = 0x32E
OUT = "/data/hermes/acc/press_phase.jsonl"
BURST_GAP_S = 0.30       # a gap longer than this starts a new burst
DROP_WINDOW_S = 6.0      # look this far ahead for the ACC command going to 0x64
BITS = {(0, 3): "set", (0, 4): "res", (0, 6): "lkas", (1, 7): "dec_dist", (2, 0): "inc_dist", (2, 3): "cancel"}


def names_for(dat):
    return "+".join(n for (byte, bit), n in sorted(BITS.items()) if (dat[byte] >> bit) & 1) or "idle"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--budget-s", type=float, default=0.0, help="run for this many seconds (0 = use --hours)")
    a = ap.parse_args()

    from cereal import messaging

    sm = messaging.SubMaster(["can"])
    deadline = time.time() + (a.budget_s if a.budget_s > 0 else a.hours * 3600)
    out = open(OUT, "a", buffering=1)

    car_last_t = None          # last time the car's own module sent 0x3B0
    burst = None               # dict for the burst being collected
    pending = []               # bursts awaiting their drop verdict
    last_acc_byte0 = None
    drops = []                 # times the ACC command went to 0x64

    def close_burst(b):
        pending.append(b)

    def report(b):
        t0, t1 = b["t0"], b["t1"]
        drop_t = next((d for d in drops if 0.0 <= d - t1 < DROP_WINDOW_S), None)
        rec = {
            "t": t0, "at": time.strftime("%H:%M:%S", time.localtime(t0)),
            "buttons": b["buttons"], "frames": b["n"],
            "gap_before_ms": None if b["gap_before"] is None else round(b["gap_before"] * 1000, 1),
            "gap_after_ms": None if b["gap_after"] is None else round(b["gap_after"] * 1000, 1),
            "car_frames_inside": b["inside"],
            "drop_within_3s": drop_t is not None,
            "drop_after_ms": None if drop_t is None else round((drop_t - t1) * 1000),
        }
        out.write(json.dumps(rec) + "\n")
        print("  %s  %-9s frames=%-2d gap_before=%s ms  inside=%d  %s" % (
            rec["at"], rec["buttons"], rec["frames"], rec["gap_before_ms"], rec["inside"],
            "DROP %.0f ms later" % rec["drop_after_ms"] if drop_t else "no drop"), flush=True)

    print("%s  watching press phase until %s (read-only) -> %s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), time.strftime("%H:%M", time.localtime(deadline)), OUT), flush=True)

    while time.time() < deadline:
        sm.update(100)
        now = time.time()

        if sm.updated["can"]:
            for ev in sm["can"]:
                dat = bytes(ev.dat)
                if ev.address == ADDR_ACC_CMD and dat:
                    if last_acc_byte0 != 0x64 and dat[0] == 0x64:
                        drops.append(now)
                    last_acc_byte0 = dat[0]
                elif ev.address == ADDR_BUTTONS:
                    # src carries the bus: the car's own module is src 0 (and its mirror onto bus 2 as
                    # 130), while the box's transmissions are 128 (bus 0 + the TX offset). Getting this
                    # wrong counts the car's 20 Hz mirror as one perpetual "burst".
                    if ev.src != 128:
                        car_last_t = now
                        if burst is not None:
                            burst["inside"] += 1          # a car frame landed inside our burst
                    else:
                        if burst is None or now - burst["t1"] > BURST_GAP_S:
                            if burst is not None:
                                close_burst(burst)
                            burst = {"t0": now, "t1": now, "n": 0, "inside": 0, "buttons": names_for(dat),
                                     "seen": set(),
                                     "gap_before": None if car_last_t is None else now - car_last_t,
                                     "gap_after": None}
                        burst["t1"] = now
                        burst["n"] += 1
                        nm = names_for(dat)
                        if nm != "idle":
                            burst["seen"].update(nm.split("+"))
                        burst["buttons"] = "+".join(sorted(burst["seen"])) or "idle"

        # a burst is complete once the bus has been quiet for longer than the burst gap
        if burst is not None and now - burst["t1"] > BURST_GAP_S:
            burst["gap_after"] = None if car_last_t is None else now - car_last_t
            close_burst(burst)
            burst = None

        # report bursts whose drop window has fully elapsed
        while pending and now - pending[0]["t1"] > DROP_WINDOW_S + 0.2:
            report(pending.pop(0))
        drops = [d for d in drops if now - d < 30]

    if burst is not None:
        close_burst(burst)
    for b in pending:
        report(b)
    print("  window closed", flush=True)


if __name__ == "__main__":
    main()
