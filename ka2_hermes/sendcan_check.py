#!/usr/bin/env python3
"""What is the box itself transmitting? (runs ON the KA2)

Separates the box's own writes (sendcan) from what the bus carries (can). This is the check that says
whether a trial change is really gone: after the openpilot-longitudinal trial was reverted, the box must
not be writing 0x32E at all, and 0x3B0 should only appear when a button press is in flight.

Reading the 'can' topic alone cannot tell you this: in openpilot the panda echoes its own transmissions
into the can stream, and this car's gateway mirrors frames onto a second bus, so the same id shows up more
than once. Only sendcan is what we asked for.
"""
import os
import sys
import time

# openpilot's python modules live in the repo, not in the venv (cereal, capnp, opendbc)
for _p in ("/data/openpilot", "/data/openpilot/opendbc_repo"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import cereal.messaging as messaging

sm = messaging.SubMaster(["can", "sendcan"])
deadline = time.time() + 4.0
tx = {}
rx = {}
while time.time() < deadline:
    sm.update(20)
    if sm.updated["sendcan"]:
        for m in sm["sendcan"]:
            tx[(m.address, m.src)] = tx.get((m.address, m.src), 0) + 1
    if sm.updated["can"]:
        for m in sm["can"]:
            rx[(m.address, m.src)] = rx.get((m.address, m.src), 0) + 1

print("BOX TRANSMITS (sendcan, ~4 s):")
if not tx:
    print("  nothing - the box is not writing CAN at all")
for (addr, src), n in sorted(tx.items(), key=lambda kv: -kv[1]):
    print("  0x%03x src=%-3d  %6d  (%.0f Hz)" % (addr, src, n, n / 4.0))

print()
print("BUS CARRIES (can, ~4 s) - top 14 ids:")
for (addr, src), n in sorted(rx.items(), key=lambda kv: -kv[1])[:14]:
    print("  0x%03x src=%-3d  %6d  (%.0f Hz)" % (addr, src, n, n / 4.0))

print()
trial_ids = sorted(a for a, s in tx if a in (0x32E, 0x32D, 0x1E2, 0x316, 0x1FC, 0x3B0))
print("VERDICT: box writes %s" % ([hex(a) for a in trial_ids] or "nothing"))
print("         0x32E from the box: %s" % ("YES - the trial is NOT fully reverted" if 0x32E in [a for a, s in tx] else "no (as expected)"))
