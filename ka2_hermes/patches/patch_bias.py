#!/usr/bin/env python3
"""Add a driver-settable lane-centre bias to the lane-centring correction (openpilot controlsd.py).

Run as root on the box, or on a local copy: patch_bias.py <controlsd.py>

What it adds:
  * LANE_CORRECTION_BIAS_M - where in the lane to sit, in metres, positive = right of lane centre. It is
    added to the correction's target offset, so unlike modeld's DrivePathOffset (read once at startup, +-0.25 m)
    it can be trimmed while driving.
  * the range in TUNING_LIMITS, so the file can carry it and the Bluetooth settings service offers it;
  * the bias is applied after the plausibility bound, so the bound keeps testing raw geometry.

Every edit is asserted to match exactly once: nothing is written unless all of them do.
"""
import io
import py_compile
import sys

P = sys.argv[1] if len(sys.argv) > 1 else "/data/openpilot/selfdrive/controls/controlsd.py"
EDITS = []

# 1. The constant, next to the decouple switch it complements.
EDITS.append((
'''LANE_CORRECTION_DECOUPLE = 1.0

# Memory and shaping for the lane geometry.''',
'''LANE_CORRECTION_DECOUPLE = 1.0

# Where in the lane to sit, in metres, signed like the offset above: positive holds the car to the right of
# the lane centre, negative to the left. It is added to the correction's target, so unlike the model's own
# path skew (DrivePathOffset, read once at modeld startup) it can be trimmed while driving.
LANE_CORRECTION_BIAS_M = 0.0

# Memory and shaping for the lane geometry.'''))

# 2. The comment above TUNING_LIMITS says every range is one-sided; the bias is the exception, and the
#    reason matters to whoever reads this next.
EDITS.append((
'''TUNING_LIMITS = {''',
'''# One key is two-sided on purpose: LANE_CORRECTION_BIAS_M says where in the lane to sit, which has no
# "gentler" direction. It carries no extra authority - the range below and the lateral budget still clamp it.
TUNING_LIMITS = {'''))

# 3. The range the file may carry, and the range the app will show.
EDITS.append((
'''  "LANE_CORRECTION_DECOUPLE": (0.0, 1.0),             # 0.0 = plain pursuit, no lane-curvature cancellation
}''',
'''  "LANE_CORRECTION_DECOUPLE": (0.0, 1.0),             # 0.0 = plain pursuit, no lane-curvature cancellation
  "LANE_CORRECTION_BIAS_M": (-0.30, 0.30),            # where to sit in the lane; two-sided by nature
}'''))

# 4. The bias itself, after the bound: the bound tests the raw geometry, the bias is an instruction.
EDITS.append((
'''  if abs(centre_offset) > LANE_CORRECTION_MAX_OFFSET_M:
    return None

  return centre_offset''',
'''  if abs(centre_offset) > LANE_CORRECTION_MAX_OFFSET_M:
    return None

  # The owner's chosen position in the lane, added after the bound so the bound keeps testing the raw
  # geometry rather than the instruction. It costs budget in proportion to its size (~0.27 m/s^2 at the
  # 0.30 m limit, independent of speed because the lookahead grows with speed), so a large bias leaves the
  # correction less authority to hold the offset it is asking for.
  return centre_offset + LANE_CORRECTION_BIAS_M'''))

# 5. Remember the last tuning dict we logged, so a knob move shows up in the journal once.
EDITS.append((
'''TUNING_BASE = {name: globals()[name] for name in TUNING_LIMITS}''',
'''TUNING_BASE = {name: globals()[name] for name in TUNING_LIMITS}
_LANE_TUNING_LAST = None             # the tuning dict as last logged, so a change appears once in the journal'''))

# 6. One journal line per knob change: this is how a drive is later tied to the values it ran with.
EDITS.append((
'''    self.lane_slew.max_rate = tuning.get("LANE_CORRECTION_MAX_ACC_RATE",
                                         TUNING_BASE["LANE_CORRECTION_MAX_ACC_RATE"])''',
'''    self.lane_slew.max_rate = tuning.get("LANE_CORRECTION_MAX_ACC_RATE",
                                         TUNING_BASE["LANE_CORRECTION_MAX_ACC_RATE"])

    # Logged on change only, never per frame: this is how a drive can later be tied to the knob values it
    # ran with, and how a knob that never lands becomes visible instead of silent.
    global _LANE_TUNING_LAST
    live = {name: globals()[name] for name in TUNING_LIMITS}
    if live != _LANE_TUNING_LAST:
      _LANE_TUNING_LAST = live
      cloudlog.info("lane tuning now: %s", json.dumps(live, sort_keys=True))'''))

src = io.open(P, encoding="utf-8").read()
for i, (old, new) in enumerate(EDITS, 1):
  n = src.count(old)
  if n != 1:
    raise SystemExit("ABORT edit %d: pattern matched %d times, not 1 - nothing written" % (i, n))
  src = src.replace(old, new)

io.open(P, "w", encoding="utf-8").write(src)
py_compile.compile(P, doraise=True)
print("patched %d edits, compiles clean: %s" % (len(EDITS), P))
