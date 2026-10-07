#!/usr/bin/env python3
"""A/B the stock-ACC press: how long must the button be held before the car always takes it?

Runs ON the KA2. For each hold length it presses the same button N times, reading the car's ACC set speed
before and after every press, and reports how many landed plus the size of each step. The question it
answers: does the ~1-in-4-8 miss come from holding the button for less than the car's own sampling period?

Design notes that matter for the answer being trustworthy:
  * configs are interleaved (4,8,12,4,8,12,...) so a change in traffic or road grade cannot land on one
    config only, and the press order is not confounded with the hold length.
  * a press is scored by the set-speed delta, not by "did something happen": +1 step = landed, +2 steps =
    the car read the press twice (the cost of holding too long), 0 = missed.
  * it stops immediately if ACC disengages or the car drops below DRIVE_MIN_KPH, because at a standstill
    the + pattern means resume/cancel, not a set-speed step - that would score nonsense.
  * the tool's own press() is used for the press itself, so its ACC-engaged gate, min gap, request file
    and audit log all still apply. Nothing here writes to CAN.

Use (on the box):
  /usr/local/venv/bin/python3 /data/hermes/ka2_press_ab.py --state
  /usr/local/venv/bin/python3 /data/hermes/ka2_press_ab.py --configs 4,8,12 --n 10 --button step
"""
import argparse
import importlib.util
import json
import os
import sys
import time

TOOL_PATH = "/data/hermes/ka2_acc_press.py"
DRIVE_MIN_KPH = 20.0          # below this a + press is resume/cancel, not a set-speed step
LOG = "/data/hermes/acc/press_ab.jsonl"


def load_tool():
    spec = importlib.util.spec_from_file_location("ka2_acc_press", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_state(sm, timeout_s=1.5):
    """(available, enabled, set_kph, vehicle_kph) as the car reports them right now."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sm.update(50)
        if sm.updated["carState"]:
            cs = sm["carState"]
            return (bool(cs.cruiseState.available), bool(cs.cruiseState.enabled),
                    float(cs.cruiseState.speedCluster) * 3.6, float(cs.vEgo) * 3.6)
    return None, None, None, None


def order(configs, n):
    """Interleave the configs: [4,8,12,4,8,12,...]."""
    seq = []
    for _ in range(n):
        seq.extend(configs)
    return seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--button", default="step", help="button to press (default step = the car's own speed pattern)")
    ap.add_argument("--configs", default="4,8,12", help="hold lengths in 100 Hz frames (4 = the current 40 ms)")
    ap.add_argument("--n", type=int, default=10, help="presses per config")
    ap.add_argument("--gap", type=float, default=6.0, help="seconds between presses")
    ap.add_argument("--settle", type=float, default=1.2, help="seconds to wait after a press before reading back")
    ap.add_argument("--state", action="store_true", help="print the car's state and exit")
    a = ap.parse_args()

    from cereal import messaging
    sm = messaging.SubMaster(["carState"])

    if a.state:
        avail, enabled, set_kph, veh = read_state(sm)
        print("ACC available=%s enabled=%s set=%s kph  car=%s kph" % (
            avail, enabled, "%.0f" % set_kph if set_kph is not None else "-",
            "%.0f" % veh if veh is not None else "-"))
        return 0

    tool = load_tool()
    configs = [int(c) for c in a.configs.split(",") if c.strip()]
    seq = order(configs, a.n)
    results = {c: [] for c in configs}

    print("button %s, %d presses per config (%s), ~%.0f s apart" % (
        a.button, a.n, ",".join(str(c) for c in configs), a.gap))

    for i, held in enumerate(seq, 1):
        avail, enabled, set_kph, veh = read_state(sm)
        if not enabled:
            print("STOP: ACC not engaged (available=%s) after %d presses" % (avail, i - 1))
            break
        if set_kph is None or veh is None:
            print("STOP: cannot read the car (%d presses done)" % (i - 1))
            break
        if veh < DRIVE_MIN_KPH:
            print("SKIP press %d: car is at %.0f kph - a + press there is resume/cancel, not a step" % (i, veh))
            time.sleep(a.gap)
            continue

        before = set_kph
        ok, sentence = tool.press(a.button, settle_s=a.settle, frames=held)
        if not ok:
            print("STOP: press refused: %s" % sentence)
            break
        time.sleep(a.gap - a.settle if a.gap > a.settle else 0)
        _, _, after, _ = read_state(sm)
        steps = None if after is None else round((after - before) / 5.0)     # the car steps in 5 kph units
        results[held].append(steps)
        entry = {"t": time.time(), "button": a.button, "frames": held,
                 "set_before": before, "set_after": after, "steps": steps}
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass
        print("  %2d/%d  hold %2d frames (%3d ms): set %.0f -> %.0f  steps=%s" % (
            i, len(seq), held, held * 10, before, after if after is not None else -1,
            "miss" if steps == 0 else steps))

    print()
    print("== result ==")
    for c in configs:
        r = [x for x in results[c] if x is not None]
        if not r:
            print("  %2d frames (%3d ms): no scored presses" % (c, c * 10))
            continue
        landed = sum(1 for x in r if x >= 1)
        singles = sum(1 for x in r if x == 1)
        doubles = sum(1 for x in r if x >= 2)
        misses = sum(1 for x in r if x == 0)
        print("  %2d frames (%3d ms): %2d/%2d landed (%3d%%)  singles=%d doubles=%d missed=%d" % (
            c, c * 10, landed, len(r), round(100.0 * landed / len(r)), singles, doubles, misses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
