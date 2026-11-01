#!/usr/bin/env python3
"""What faulted, and was the box writing ACC_CMD when it did. Read-only."""
import sys

sys.path.insert(0, "/data/openpilot")
from cereal import messaging  # noqa: E402

sm = messaging.SubMaster(["carState", "controlsState", "selfdriveState", "pandaStates", "onroadEvents", "can"])
for _ in range(150):
    sm.update(10)


def safe(fn, default="?"):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return "err:%s" % type(e).__name__


print("  selfdrive: state=%s enabled=%s alertType=%s" % (
    safe(lambda: str(sm["selfdriveState"].state)), safe(lambda: sm["selfdriveState"].enabled),
    safe(lambda: str(sm["selfdriveState"].alertType))))
print("  alert1   : %r" % safe(lambda: sm["selfdriveState"].alertText1))
print("  alert2   : %r" % safe(lambda: sm["selfdriveState"].alertText2))
print("  carState : vEgo=%s gear=%s brake=%s" % (
    safe(lambda: sm["carState"].vEgo), safe(lambda: str(sm["carState"].gearShifter)), safe(lambda: sm["carState"].brakePressed)))
print("  cruise   : enabled=%s available=%s" % (
    safe(lambda: sm["carState"].cruiseState.enabled), safe(lambda: sm["carState"].cruiseState.available)))

try:
    ps = sm["pandaStates"][0]
    print("  panda    : safetyModel=%s controlsAllowed=%s" % (str(ps.safetyModel), ps.controlsAllowed))
    try:
        print("  panda faults      : %s" % list(ps.faults))
    except Exception:  # noqa: BLE001
        print("  panda faults      : %s" % ps.faults)
    try:
        print("  panda rxInvalid   : %s" % ps.safetyRxChecksInvalid)
    except Exception:  # noqa: BLE001
        pass
    for extra in ("safetyTxBlocked", "controlsState"):
        try:
            print("  panda %-12s: %s" % (extra, getattr(ps, extra)))
        except Exception:  # noqa: BLE001
            pass
except Exception as e:  # noqa: BLE001
    print("  panda    : err %s" % e)

print("\n  onroad events:")
try:
    seen = []
    for e in sm["onroadEvents"]:
        s = "%s | %s | %s" % (str(e.alertType), str(e.eventType), e.name)
        if s not in seen:
            seen.append(s)
    for s in seen[-10:]:
        print("    %s" % s)
except Exception as e:  # noqa: BLE001
    print("    err %s" % e)

counts = {}
for _ in range(200):
    sm.update(10)
    for m in sm["can"]:
        if m.address in (0x32e, 0x32d):
            counts[(m.address, m.src)] = counts.get((m.address, m.src), 0) + 1
print("\n  bus ~2s:")
for (addr, src), n in sorted(counts.items(), key=lambda kv: -kv[1]):
    print("    0x%03x src=%-4d %4d  (%s)" % (addr, src, n, "BOX TX" if src & 0x80 else "car"))
