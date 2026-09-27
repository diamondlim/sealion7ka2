#!/usr/bin/env python3
"""Decouple the lane-centre correction from the lane's own curvature (openpilot controlsd.py).

Usage: patch_decouple.py <controlsd.py>   (run as root on the box, or on a local copy)

Adds LANE_CORRECTION_DECOUPLE (tuning key + constant) and subtracts the lane's own curvature from the
requested offset via y(L) - y(2L)/4, testing the plausibility bound against that residual. See
references/bukapilot-byd-lateral.md. Every edit is asserted to match exactly once; nothing is written
unless all of them do, and the result is py_compile'd in place.
"""
import io
import py_compile
import sys

P = sys.argv[1] if len(sys.argv) > 1 else "/data/openpilot/selfdrive/controls/controlsd.py"
EDITS = []

EDITS.append((
'''LANE_CORRECTION_MIN_LOOKAHEAD_M = 10.0  # floor on the lookahead at low speed
''',
'''LANE_CORRECTION_MIN_LOOKAHEAD_M = 10.0  # floor on the lookahead at low speed

# The request is a pursuit of the lane centre, so it also contains the *lane's own* curvature: for a lane
# curving at k, y(L) ~ 0.5*k*L^2, hence 2*y(L)/L^2 ~ k regardless of the lookahead. The plan already
# carries that curvature, so without this the correction asks for the bend roughly twice over - measured
# on the owner's own logs, 92% of the request in gentle bends was the bend's own curvature. That is felt
# as turning in early and then holding the line, and because the demand exceeds the budget in any real
# bend the correction sits at its cap there. 1.0 subtracts it: y(L) - y(2L)/4 keeps the position error
# and cancels a quadratic lane exactly, with no derivative estimate to amplify noise (a pure lateral
# offset is then weighted 0.75x, which the gain covers). 0.0 is the plain pursuit request, for A/B.
LANE_CORRECTION_DECOUPLE = 1.0
'''))

EDITS.append((
'''  "LANE_MEMORY_MAX_YAW_RATE": (0.0, 0.15),            # trust the held offset in less curvature
}''',
'''  "LANE_MEMORY_MAX_YAW_RATE": (0.0, 0.15),            # trust the held offset in less curvature
  "LANE_CORRECTION_DECOUPLE": (0.0, 1.0),             # 0.0 = plain pursuit, no lane-curvature cancellation
}'''))

EDITS.append((
'''def lane_centre_offset(model_v2, v_ego):
  """Lateral offset of the lane centre from the car, from the model's current-lane lines.
''',
'''def lane_centre_offset(model_v2, v_ego, decouple=None):
  """Lateral offset of the lane centre from the car, from the model's current-lane lines.

  With `decouple` (default: the live LANE_CORRECTION_DECOUPLE value) the lane's own curvature is removed
  from the returned offset, so what is left is the position error rather than the bend. Callers that
  want the raw geometry pass decouple=False.
'''))

EDITS.append((
'''  def y_at_lookahead(line):
    xs = np.asarray(line.x, dtype=float)
    ys = np.asarray(line.y, dtype=float)
    if xs.size < 2 or ys.size != xs.size:
      return None
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    if not (xs[0] <= lookahead <= xs[-1]):
      return None
    y = float(np.interp(lookahead, xs, ys))
    return y if math.isfinite(y) else None

  y_left = y_at_lookahead(lines[1]) if probs[1] >= LANE_CORRECTION_MIN_PROB else None
  y_right = y_at_lookahead(lines[2]) if probs[2] >= LANE_CORRECTION_MIN_PROB else None
''',
'''  def y_at(line, x_target):
    xs = np.asarray(line.x, dtype=float)
    ys = np.asarray(line.y, dtype=float)
    if xs.size < 2 or ys.size != xs.size:
      return None
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    if not (xs[0] <= x_target <= xs[-1]):
      return None
    y = float(np.interp(x_target, xs, ys))
    return y if math.isfinite(y) else None

  y_left = y_at(lines[1], lookahead) if probs[1] >= LANE_CORRECTION_MIN_PROB else None
  y_right = y_at(lines[2], lookahead) if probs[2] >= LANE_CORRECTION_MIN_PROB else None
'''))

EDITS.append((
'''  centre_offset = 0.5 * (y_left + y_right)       # + means the lane centre is right of the car
  if abs(centre_offset) > LANE_CORRECTION_MAX_OFFSET_M:
    return None

  return centre_offset
''',
'''  centre_offset = 0.5 * (y_left + y_right)       # + means the lane centre is right of the car

  if decouple is None:
    decouple = bool(LANE_CORRECTION_DECOUPLE)
  if decouple:
    # Remove the lane's own curvature (see LANE_CORRECTION_DECOUPLE): the same pair, twice the lookahead.
    # The lane-width test is repeated there because a line that has wandered into another lane is worthless
    # as a curvature reference. No 2L sample (short line) -> the plain offset, i.e. the previous behaviour.
    y2_left = y_at(lines[1], 2.0 * lookahead) if probs[1] >= LANE_CORRECTION_MIN_PROB else None
    y2_right = y_at(lines[2], 2.0 * lookahead) if probs[2] >= LANE_CORRECTION_MIN_PROB else None
    if y2_left is not None and y2_right is not None and 1.5 <= (y2_right - y2_left) <= 6.0:
      centre_offset -= 0.25 * 0.5 * (y2_left + y2_right)

  # The plausibility bound applies to what is left, i.e. to the position error: a bend's own geometry can
  # push the raw offset far past any sensible bound, and rejecting those frames is what switched the
  # correction off in exactly the corners it exists for.
  if abs(centre_offset) > LANE_CORRECTION_MAX_OFFSET_M:
    return None

  return centre_offset
'''))

src = io.open(P, encoding="utf-8").read()
for i, (old, new) in enumerate(EDITS, 1):
  n = src.count(old)
  if n != 1:
    raise SystemExit("ABORT edit %d: pattern matched %d times, not 1 - nothing written" % (i, n))
  src = src.replace(old, new)

io.open(P, "w", encoding="utf-8").write(src)
py_compile.compile(P, doraise=True)
print("patched %d edits, compiles clean: %s" % (len(EDITS), P))
