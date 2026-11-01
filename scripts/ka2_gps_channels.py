"""Does the fork's software stack hold a GPS fix? Read the cereal channels, don't infer.

Runs two ways: file, or stdin. When the fork's root is not sys.path[0] (i.e. running this as a path
outside it), cereal cannot be imported from the script's own directory - so add the fork root explicitly.
"""
import os
import sys
import time

sys.path.insert(0, "/data/openpilot")

from cereal import messaging

CHANS = ["gpsLocationExternal", "gpsLocation", "livePose"]

sm = messaging.SubMaster(CHANS)
t0 = time.time()
while time.time() - t0 < 12:
    sm.update(500)

for c in CHANS:
    try:
        valid = bool(sm.valid[c])
    except Exception:
        valid = False
    try:
        alive = bool(sm.alive[c])
    except Exception:
        alive = False
    print("%-22s valid=%-5s alive=%-5s" % (c, valid, alive))
    if valid:
        m = sm[c]
        for name in ("gpsLocationExternal", "gpsLocation", "liveLocationKalman", "livePose"):
            g = getattr(m, name, None)
            if g is None:
                continue
            fields = []
            for f in ("latitude", "longitude", "horizontalAccuracy", "verticalAccuracy",
                      "speed", "bearingDeg", "source", "unixTimestampMillis", "gpsOK"):
                v = getattr(g, f, None)
                if v is not None:
                    fields.append("%s=%s" % (f, v))
            if fields:
                print("   %s: %s" % (name, "  ".join(fields)))
print("done")
