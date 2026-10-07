#!/usr/bin/env python3
"""Put the vision-acc rows back into the box's settings service.

Runs ON the KA2. Restores exactly the three blocks that were removed on 7 Oct 2026 when the vision-acc bridge
(ka2-vision-acc) was stopped and disabled, so the app went back to offering only parameters that do
something:

  1. TUNING_TITLES - the 19 VIS_* rows' wording ("Auto-slow for bends (on/off)", "Slow for a car ahead
     (on/off)", ...). The step sizes in TUNING_STEPS were left in place as inert metadata, so nothing has
     to be retyped for the rows to come back with correct steps.
  2. TUNING_SOURCES - the vision-acc source dict, which is what makes the service parse those rows out of
     /data/hermes/ka2_vision_acc.py in the first place.
  3. the inert ExperimentalMode note - kept for the record only; it is never offered in the app either way.

All three blocks are the pre-hide text verbatim from commit 58aaf675 of diamondlim/sealion7ka2 on
longitudinal_off - the last version before the removal - embedded here so the box needs no network.

The rows are useless on their own: they write to a config the bridge reads, so re-enable ka2-vision-acc
(VIS_TURN_ACC_ENABLED = 1 in /data/hermes/tuning.json) in the same change, or the app offers knobs that do
nothing - which is the state this undoes.

Use (on the box):
  sudo /usr/bin/python3 /data/hermes/ka2_restore_vis_rows.py --check
  sudo /usr/bin/python3 /data/hermes/ka2_restore_vis_rows.py --apply --backup /data/hermes/backups/bt_settings_service.py.pre-restore
"""
import argparse
import shutil
import sys

SERVICE = "/data/hermes/bt_settings_service.py"

REPLACEMENTS = [
  # --- TUNING_TITLES
  (
  r'''TUNING_TITLES = {
  "LANE_CORRECTION_GAIN": ("Lane-centre correction gain",
                           "Live tuning. 0 turns the correction off, i.e. stock lateral behaviour; "
                           "the car's own value is what the deployed branch commits."),
  "LANE_CORRECTION_LOOKAHEAD_S": ("Correction lookahead (s)",
                                  "Live tuning. How far ahead the offset is measured, as a time at "
                                  "the current speed. Longer is gentler."),
  "LANE_CORRECTION_MIN_PROB": ("Minimum lane-line probability",
                               "Live tuning. How confident the model must be in both lane lines "
                               "before the correction acts. Stricter only."),
  "LANE_CORRECTION_MAX_OFFSET_M": ("Maximum lane-centre offset (m)",
                                   "Live tuning. Offsets beyond this are rejected as implausible. "
                                   "Stricter only."),
  "LANE_CORRECTION_MAX_LAT_ACC": ("Maximum extra lateral acceleration (m/s2)",
                                  "Live tuning. The correction's own budget. Can be reduced, never "
                                  "raised past the committed design value."),
  "LANE_CORRECTION_MIN_SPEED": ("Minimum speed (m/s)",
                                "Live tuning. Below this the correction is inactive. Can only be "
                                "raised."),
  "LANE_CORRECTION_FILTER_TAU_S": ("Offset filter time constant (s)",
                                   "Live tuning. Higher smooths the lane offset more; the committed "
                                   "value is the most responsive allowed."),
  "LANE_CORRECTION_MAX_ACC_RATE": ("Rate limit on the correction (m/s2 per s)",
                                   "Live tuning. Lower makes the correction change more slowly. "
                                   "0 means unlimited, as in the code."),
  "LANE_MEMORY_HOLD_S": ("Memory hold after a dropout (s)",
                         "Live tuning. How long a remembered offset survives losing the lane "
                         "lines. Shorter only."),
  "LANE_MEMORY_MAX_YAW_RATE": ("Yaw-rate limit for a held offset (rad/s)",
                               "Live tuning. Above this curvature rate a remembered offset is "
                               "never trusted. Tighter only."),
  "LANE_CORRECTION_DECOUPLE": ("Work on the lane, not on the bend (0/1)",
                               "1 = the correction subtracts the lane's own curvature, so through a corner it "
                               "acts on where you sit in the lane instead of on the bend itself - it stops "
                               "turning in early and holding the line. 0 = the plain request, which counts the "
                               "bend's curvature a second time. Applies within about a second of a change."),
  "LANE_CORRECTION_BIAS_M": ("Where to sit in the lane (m, + = right of centre)",
                             "Position to hold the car at within its lane, in metres, added to the "
                             "lane-centring correction's target. Positive sits to the right of the lane "
                             "centre, negative to the left. Unlike the Path Skew Offset in Device Settings "
                             "this is read continuously, so it can be trimmed while driving, and the two add "
                             "up. The bias is spent from the correction's lateral budget, so a large one "
                             "leaves it less authority to hold the position."),
  # --- the vision -> stock-ACC bridge. These are read by the bridge tool, not by controlsd; it re-reads
  #     the same tuning file about once a second.
}
''',
  r'''TUNING_TITLES = {
  "LANE_CORRECTION_GAIN": ("Lane-centre correction gain",
                           "Live tuning. 0 turns the correction off, i.e. stock lateral behaviour; "
                           "the car's own value is what the deployed branch commits."),
  "LANE_CORRECTION_LOOKAHEAD_S": ("Correction lookahead (s)",
                                  "Live tuning. How far ahead the offset is measured, as a time at "
                                  "the current speed. Longer is gentler."),
  "LANE_CORRECTION_MIN_PROB": ("Minimum lane-line probability",
                               "Live tuning. How confident the model must be in both lane lines "
                               "before the correction acts. Stricter only."),
  "LANE_CORRECTION_MAX_OFFSET_M": ("Maximum lane-centre offset (m)",
                                   "Live tuning. Offsets beyond this are rejected as implausible. "
                                   "Stricter only."),
  "LANE_CORRECTION_MAX_LAT_ACC": ("Maximum extra lateral acceleration (m/s2)",
                                  "Live tuning. The correction's own budget. Can be reduced, never "
                                  "raised past the committed design value."),
  "LANE_CORRECTION_MIN_SPEED": ("Minimum speed (m/s)",
                                "Live tuning. Below this the correction is inactive. Can only be "
                                "raised."),
  "LANE_CORRECTION_FILTER_TAU_S": ("Offset filter time constant (s)",
                                   "Live tuning. Higher smooths the lane offset more; the committed "
                                   "value is the most responsive allowed."),
  "LANE_CORRECTION_MAX_ACC_RATE": ("Rate limit on the correction (m/s2 per s)",
                                   "Live tuning. Lower makes the correction change more slowly. "
                                   "0 means unlimited, as in the code."),
  "LANE_MEMORY_HOLD_S": ("Memory hold after a dropout (s)",
                         "Live tuning. How long a remembered offset survives losing the lane "
                         "lines. Shorter only."),
  "LANE_MEMORY_MAX_YAW_RATE": ("Yaw-rate limit for a held offset (rad/s)",
                               "Live tuning. Above this curvature rate a remembered offset is "
                               "never trusted. Tighter only."),
  "LANE_CORRECTION_DECOUPLE": ("Work on the lane, not on the bend (0/1)",
                               "1 = the correction subtracts the lane's own curvature, so through a corner it "
                               "acts on where you sit in the lane instead of on the bend itself - it stops "
                               "turning in early and holding the line. 0 = the plain request, which counts the "
                               "bend's curvature a second time. Applies within about a second of a change."),
  "LANE_CORRECTION_BIAS_M": ("Where to sit in the lane (m, + = right of centre)",
                             "Position to hold the car at within its lane, in metres, added to the "
                             "lane-centring correction's target. Positive sits to the right of the lane "
                             "centre, negative to the left. Unlike the Path Skew Offset in Device Settings "
                             "this is read continuously, so it can be trimmed while driving, and the two add "
                             "up. The bias is spent from the correction's lateral budget, so a large one "
                             "leaves it less authority to hold the position."),
  # --- the vision -> stock-ACC bridge. These are read by the bridge tool, not by controlsd; it re-reads
  #     the same tuning file about once a second.
  "VIS_TURN_ACC_MIN_SETPOINT_KMH": ("Auto-slow floor (km/h)",
                                    "The bridge slows the car for a bend it sees by stepping the ACC "
                                    "setpoint down 5 km/h at a time. It will never step below this, so "
                                    "this is the floor of the automatic slowing. Can only be raised."),
  "VIS_TURN_ACC_MAX_RESTORE_KMH": ("Auto-raise ceiling (km/h)",
                                   "After the bend the bridge hands the speed back - never above the "
                                   "setpoint you had set yourself, and never above this. Can only be "
                                   "lowered."),
  "VIS_TURN_ACC_MAX_STEPS": ("Max auto-slow steps per bend",
                             "How many 5 km/h steps the bridge may take off the ACC setpoint for one "
                             "bend: 3 = up to 15 km/h, 6 = up to 30 km/h. It still stops at the "
                             "auto-slow floor and only gives speed back up to the setpoint you set "
                             "yourself."),
  "VIS_TURN_ACC_RESTORE": ("Hand the speed back after a bend (on/off)",
                           "1 = once the bend is behind you the bridge steps the setpoint back up - never "
                           "above the setpoint you set yourself. 0 = it only ever slows; you raise the "
                           "speed again yourself. Lower only."),
  "VIS_TURN_ACC_A_LAT": ("Bend comfort (m/s2 lateral)",
                         "How much cornering force the model allows: the comfort speed is "
                         "sqrt(A_LAT / curvature). Lower means slower and more cautious through bends, "
                         "higher lets the car carry more speed. Raise only."),
  "VIS_TURN_ACC_TRIGGER_S": ("Start slowing this long before a bend (s)",
                             "Seconds before the bend entry at which the first step is taken. Longer "
                             "starts the slowing earlier and more gradually. Moves either way."),
  "VIS_TURN_ACC_LOOKAHEAD_MAX": ("How far ahead to look for bends (m)",
                                 "How much of the model's path is scanned for a bend. Moves either way; "
                                 "the trigger above decides when to act on it."),
  "VIS_TURN_ACC_MIN_RADIUS": ("Ignore bends gentler than this radius (m)",
                              "Bends wider than this produce no automatic slowing at all. Raise it to "
                              "leave gentle highway curves alone. Raise only."),
  "VIS_TURN_ACC_MIN_V_KMH": ("Don't auto-slow below this speed (km/h)",
                             "Below this speed bends are left to you - useful in town. Raise only."),
  "VIS_TURN_ACC_MARGIN_KMH": ("Only slow if the bend needs this much less (km/h)",
                              "The model's comfort speed must be at least this far below your setpoint "
                              "before a step is taken. Raise it to stop small nuisance steps. Raise only."),
  "VIS_TURN_ACC_RESTORE_MARGIN_KMH": ("Speed-back headroom (km/h)",
                                      "How much faster the road must allow before a step back up is taken. "
                                      "Raise only."),
  "VIS_LEAD_ACC_ENABLED": ("Slow for a car ahead (on/off)",
                          "1 = the bridge may lower the ACC setpoint for a car the camera model sees "
                          "ahead, before the car's own ACC has resolved it - so the slowing starts "
                          "sooner and more gently. It only ever lowers; the ACC still does the "
                          "following. 0 = off."),
  "VIS_LEAD_ACC_LOOKAHEAD_M": ("How far ahead a car is acted on (m)",
                               "A car further ahead than this is ignored, so the bridge cannot slow you "
                               "for distant traffic."),
  "VIS_LEAD_ACC_MARGIN_KMH": ("Aim this much faster than the car ahead (km/h)",
                              "The setpoint is walked down toward the lead car's own speed plus this "
                              "margin, never below the auto-slow floor."),
  "VIS_LEAD_ACC_MIN_PROB": ("Confidence before a car counts as your lead",
                            "How sure the camera model must be (0-1) before a car ahead is acted on. "
                            "Raise it if a shadow or an oncoming vehicle ever causes an unnecessary "
                            "slow-down."),
  "VIS_LEAD_ACC_MAX_STEPS": ("Max steps per car ahead",
                             "How many 5 km/h steps may be taken off the setpoint for one car ahead: "
                             "4 = up to 20 km/h. The budget re-arms once that car is no longer in "
                             "front of you. The auto-slow floor still binds."),
  "VIS_TURN_ACC_COOLDOWN_S": ("Seconds between auto-slow steps",
                              "How long the bridge waits before taking the next 5 km/h step off the ACC "
                              "setpoint. Longer only: 2.5 s is the fastest cadence the car tolerates, and "
                              "stretching it makes the slowing gentler and easier to follow. The handback "
                              "cadence below is set separately."),
  "VIS_TURN_ACC_UP_INTERVAL_S": ("Seconds between auto speed increase steps",
                                 "How long the bridge waits between the + steps that hand the speed back "
                                 "after a bend: 1 s means a 5 km/h step a second while the road stays "
                                 "clear. It never raises above the setpoint you set yourself, and it does "
                                 "not change the auto-slow cadence above."),
  "VIS_TURN_ACC_ENABLED": ("Auto-slow for bends (on/off)",
                           "1 = the bridge may step the ACC setpoint down for a bend the model sees "
                           "ahead, and hand it back afterwards. 0 = off. Re-read from the tuning file "
                           "about once a second, so it applies without a restart."),
}
''',
  ),
  # --- TUNING_SOURCES
  (
  r'''TUNING_SOURCES = (
  {"path": CONTROLD, "keys": {k: k for k in TUNING_TITLES if k.startswith("LANE_")}},
  # The vision-acc bridge (VIS_*) source was removed on 7 Oct 2026: the bridge, ka2-vision-acc,
  # is stopped and disabled, so all 19 of its rows wrote to a config nothing read. Putting the
  # source back is all that is needed to offer them again, once TUNING_TITLES carries their wording.
)
''',
  r'''TUNING_SOURCES = (
  {"path": CONTROLD, "keys": {k: k for k in TUNING_TITLES if k.startswith("LANE_")}},
  {"path": VISION_ACC_TOOL, "keys": {"VIS_TURN_ACC_MIN_SETPOINT_KMH": "MIN_SETPOINT_KMH",
                                     "VIS_TURN_ACC_MAX_RESTORE_KMH": "MAX_RESTORE_KMH",
                                     "VIS_TURN_ACC_MAX_STEPS": "MAX_STEPS",
                                     "VIS_TURN_ACC_COOLDOWN_S": "COOLDOWN_S",
                                     "VIS_TURN_ACC_UP_INTERVAL_S": "UP_INTERVAL_S",
                                     "VIS_TURN_ACC_RESTORE": "RESTORE",
                                     "VIS_TURN_ACC_A_LAT": "A_LAT",
                                     "VIS_TURN_ACC_TRIGGER_S": "TRIGGER_S",
                                     "VIS_TURN_ACC_LOOKAHEAD_MAX": "LOOKAHEAD_MAX",
                                     "VIS_TURN_ACC_MIN_RADIUS": "MIN_RADIUS",
                                     "VIS_TURN_ACC_MIN_V_KMH": "MIN_V_KMH",
                                     "VIS_TURN_ACC_MARGIN_KMH": "MARGIN_KMH",
                                     "VIS_TURN_ACC_RESTORE_MARGIN_KMH": "RESTORE_MARGIN_KMH",
                                     "VIS_LEAD_ACC_ENABLED": "LEAD_ENABLED",
                                     "VIS_LEAD_ACC_LOOKAHEAD_M": "LEAD_LOOKAHEAD_M",
                                     "VIS_LEAD_ACC_MARGIN_KMH": "LEAD_MARGIN_KMH",
                                     "VIS_LEAD_ACC_MIN_PROB": "LEAD_MIN_PROB",
                                     "VIS_LEAD_ACC_MAX_STEPS": "LEAD_MAX_STEPS",
                                     "VIS_TURN_ACC_ENABLED": "ENABLED"}},
)
''',
  ),
  # --- ExperimentalMode note (inert - never shown in the app)
  (
  r'''  {"k": "RecordFront", "type": "bool", "mode": "live", "sec": "sw", "default": "0",
   "lock_key": "RecordFrontLock",
   "title": "Record and Upload Driver Camera", "desc": "Upload driver-facing camera data to help "
           "improve the driver monitoring algorithm."},
''',
  r'''  {"k": "RecordFront", "type": "bool", "mode": "live", "sec": "sw", "default": "0",
   "lock_key": "RecordFrontLock",
   "title": "Record and Upload Driver Camera", "desc": "Upload driver-facing camera data to help "
           "improve the driver monitoring algorithm."},
  {"k": "ExperimentalMode", "type": "bool", "mode": "inert", "sec": "sw", "default": "0",
   "title": "Experimental Mode",
   "desc": "Not offered: selfdrived deletes this key on every start for this car, because the "
           "Sealion 7 has no openpilot longitudinal control. A write cannot hold."},
''',
  ),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--apply", action="store_true", help="write the file")
    ap.add_argument("--backup", help="where to copy the current file before writing")
    a = ap.parse_args()
    if not (a.check or a.apply):
        ap.error("--check or --apply")

    src = open(SERVICE).read()
    out = src
    for i, (old, new) in enumerate(REPLACEMENTS):
        if out.count(old) == 0:
            if out.count(new) == 1:
                print("  block %d: already restored" % (i + 1))
                continue
            print("FAIL: block %d matches neither the pre-hide nor the restored text" % (i + 1))
            return 1
        if out.count(old) != 1:
            print("FAIL: block %d matches %d times" % (i + 1, out.count(old)))
            return 1
        out = out.replace(old, new)
        print("  block %d: restored" % (i + 1))

    if out == src:
        print("nothing to do - already restored")
        return 0
    if a.check:
        print("check only: %d bytes would change" % (len(out) - len(src)))
        return 0

    if a.backup:
        shutil.copy2(SERVICE, a.backup)
        print("backup: %s" % a.backup)
    open(SERVICE, "w").write(out)
    print("written: %s" % SERVICE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
