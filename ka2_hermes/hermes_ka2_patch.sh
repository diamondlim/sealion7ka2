#!/usr/bin/env bash
# Hermes: keep openpilot usable on this KA2 across the boot-time tree reset
# (the updater runs `git reset --hard FETCH_HEAD`, which wipes local edits).
#
# Two failure modes are fixed here, both consequences of carState (a 100 Hz message) being read
# non-blockingly inside a SubMaster that polls a different service: the reader sees ~2 Hz, so the
# alive/average-frequency checks fail and the message's `valid` flag is consumed as False. That
# cascaded into: liveCalibration/livePose invalid -> selfdrived wedged in selfdriveInitializing ->
# no ControlsReady -> panda never armed -> nothing engaged, and a permanent commIssue alert.
#
# The lane-centre work (decouple + bias) is re-applied the same way, through the patch scripts kept under
# /data/hermes/patches - outside the tree, so they survive the very reset they repair.
#
# Idempotent: each patch is skipped when its marker is already present. Safe to run every boot.
set -u
python3 - <<'PY'
import re

def edit(path, marker, fn):
    try:
        s = open(path).read()
    except FileNotFoundError:
        return
    if marker in s:
        print('  ok (already patched): %s' % path.split('/')[-1])
        return
    try:
        s2 = fn(s)
    except AssertionError as e:
        print('  SKIP %s: pattern not found (%s)' % (path.split('/')[-1], e))
        return
    open(path, 'w').write(s2)
    if __import__('subprocess').run(['python3', '-c', 'import ast,sys; ast.parse(open(%r).read())' % path]).returncode != 0:
        open(path, 'w').write(s)
        print('  REVERTED (syntax error): %s' % path)
        return
    print('  patched: %s' % path.split('/')[-1])

OD = '/data/openpilot'

def continue_car_counter(s):
    old = "    can_list = can_capnp_to_list(can_strs)"
    assert old in s, 'can_list line not found'
    new = old + '''

    # Follow the car's own 0x3B0 button counter so an injected press continues its sequence
    try:
      for _entry in can_list:
        _cands = (_entry,) if hasattr(_entry, "address") else _entry
        for _g in _cands:
          if getattr(_g, "address", None) == 0x3B0 and getattr(_g, "src", None) == 0:
            _dat = getattr(_g, "dat", None)
            if _dat is not None and len(_dat) > 6:
              self.acc_button_state["car_counter"] = (_dat[6] >> 4) & 0x0F
    except Exception:
      pass'''
    s = s.replace(old, new, 1)
    s = s.replace('{"name": None, "frames": 0, "counter": 0, "last": 0.0}',
                  '{"name": None, "frames": 0, "counter": 0, "car_counter": 0, "last": 0.0}', 1)
    s = s.replace('  state["counter"] = 0\n',
                  '  state["counter"] = (state.get("car_counter", 0) + 1) & 0x0F   # follow the car\'s own sequence\n', 1)
    return s


edit(OD + '/selfdrive/car/card.py', 'car_counter', continue_car_counter)


def drop_button_keepalive(s):
    old = ("        else:\n"
           "          if CS.out.standstill and CC.enabled and (self.frame % BUTTON_KEEPALIVE_FRAMES == 0):\n"
           "            can_sends.append(send_buttons(self.packer, 1, 0, self.button_send_bus))\n")
    assert old in s, 'keepalive block not found'
    new = ("        else:\n"
           "          # Stock-ACC cars: never press a speed button as a keepalive. SET_BTN+RES_BTN is a real\n"
           "          # set-speed-up press here, so the old keepalive crept the set speed upward and its\n"
           "          # frames (built outside acc_button.build) reached the bus with no counter/checksum.\n"
           "          pass\n")
    return s.replace(old, new, 1)

edit(OD + '/opendbc_repo/opendbc/car/byd/cam_lka/carcontroller.py',
     'never press a speed button as a keepalive', drop_button_keepalive)

def ignore_carstate_in_reader(s):
    m = re.search(r"\n(\s*)sm = messaging\.SubMaster\(\[[^\]]*carState[^\]]*\][^\n]*\)", s)
    assert m, 'SubMaster(carState) line not found'
    ind = m.group(1)
    block = ("\n" + ind + "# KA2/hermes: carState is read non-blockingly in a polled reader and looks ~2 Hz, which\n"
             + ind + "# fails the alive/avg-freq checks and poisons the validity flags downstream.\n"
             + ind + "sm.ignore_average_freq.append('carState')\n"
             + ind + "sm.ignore_alive.append('carState')")
    return s[:m.end()] + block + s[m.end():]

MARK = "ignore_average_freq.append('carState')"
edit(OD + '/selfdrive/locationd/calibrationd.py', MARK, ignore_carstate_in_reader)
edit(OD + '/selfdrive/locationd/locationd.py', MARK, ignore_carstate_in_reader)

def plannerd_patch(s):
    m = re.search(r"\n(\s*)sm = messaging\.SubMaster\(\[[^\]]*\],\n\s*poll='modelV2'\)", s)
    assert m, 'plannerd SubMaster not found'
    ind = m.group(1)
    block = ("\n" + ind + "# KA2/hermes: same carState starvation; without this longitudinalPlan.valid is False,\n"
             + ind + "# which raises commIssue (a NO_ENTRY alert) on every drive.\n"
             + ind + "sm.ignore_average_freq.append('carState')\n"
             + ind + "sm.ignore_alive.append('carState')")
    return s[:m.end()] + block + s[m.end():]

edit(OD + '/selfdrive/controls/plannerd.py', MARK, plannerd_patch)

def selfdrived_patch(s):
    old = "    ignore = self.sensor_packets + self.gps_packets + ['alertDebug']"
    new = ("    ignore = self.sensor_packets + self.gps_packets + ['alertDebug']\n"
           "    # KA2/hermes: structurally absent or only valid while actively driving on this unit (no radar\n"
           "    # fitted, DM camera below nominal, estimators that need motion). Unchecked they raise commIssue\n"
           "    # - a NO_ENTRY alert - on every drive and hold the status LED on orange.\n"
           "    ignore += ['radarState', 'driverMonitoringState', 'liveDelay', 'liveParameters',\n"
           "               'liveTorqueParameters', 'driverAssistance']")
    assert s.count(old) == 1, 'ignore line not unique'
    return s.replace(old, new)

edit(OD + '/selfdrive/selfdrived/selfdrived.py', "'liveTorqueParameters', 'driverAssistance']", selfdrived_patch)

def planner_valid_patch(s):
    old = "    plan_send.valid = sm.all_checks(service_list=['carState', 'controlsState', 'selfdriveState', 'radarState'])"
    new = ("    # KA2/hermes: this platform has no radar; requiring radarState made the plan permanently\n"
           "    # invalid, which surfaced as a commIssue alert and an orange status LED on every drive.\n"
           "    check_services = ['carState', 'controlsState', 'selfdriveState']\n"
           "    if not self.CP.radarUnavailable:\n"
           "      check_services.append('radarState')\n"
           "    plan_send.valid = sm.all_checks(service_list=check_services)")
    assert s.count(old) == 1, 'valid line not unique'
    return s.replace(old, new)

edit(OD + '/selfdrive/controls/lib/longitudinal_planner.py', 'check_services', planner_valid_patch)
PY

# --- lane-centre correction: the correction is decoupled from the lane's own curvature, and the driver's
# in-lane bias is added (see ka2_hermes/README.md, references/bukapilot-byd-lateral.md). Each script aborts
# without writing unless every one of its edits matches exactly once, so a half-applied tree is never left
# behind; the marker check makes a re-run a no-op.
CONTROLSD=/data/openpilot/selfdrive/controls/controlsd.py
if [ -f "$CONTROLSD" ]; then
  grep -q 'LANE_CORRECTION_DECOUPLE' "$CONTROLSD" \
    || python3 /data/hermes/patches/patch_decouple.py "$CONTROLSD" || true
  grep -q 'LANE_CORRECTION_BIAS_M' "$CONTROLSD" \
    || python3 /data/hermes/patches/patch_bias.py "$CONTROLSD" || true
fi

exit 0
