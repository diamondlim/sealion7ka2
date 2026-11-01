#!/usr/bin/env python3
"""Port Kommu's stock-ACC SNG (stop-and-go resume) into the KA2's fork.

Runs ON the KA2. This is a port of commit 8519177e ("Proton and BYD stock ACC SNG") on kommuai/opendbc,
branch byd_sng_ka2 - their branch shares no history with this tree, so the changes are applied by hand,
file by file, and only the SNG parts: their Proton file and their PLATFORM_CAM_LKA refactor are left alone.

What it makes the car do: while the car's own ACC is holding it at a standstill (CRUISE_STATE 6 or 7 with
standstill), and a lead the box has latched starts moving (>0.1 m/s, >2 m ahead), press the car's own
SET+RES button pattern so the car resumes by itself - the stop-and-go the SL7 makes you do with the rocker.
First press 30 s after entering the hold (their tuning, Seal/SL7/Shark), then about 1 s apart, and any of
your own gas, brake or RES press defers it.

Safety posture, all of it theirs: it presses only SET+RES (never RES alone - that cancels on this car -
never LKAS, never CANCEL), only while the car's ACC is in control, and any driver input steps it back. The
new behaviour is the car moving off by itself at a standstill, which is the point.

One adaptation the fork needs: their idle-frame capture hooks the newer `update(can_packets)` API, which
this fork does not have. Here the car's own idle 0x3B0 frame is captured in card.py, which already scans
that id at 100 Hz to follow the button counter, and handed to the car controller as CS.sng_pcm_idle.

Use (on the box):
  sudo /usr/local/venv/bin/python3 /data/hermes/ka2_sng_port.py --check
  sudo /usr/local/venv/bin/python3 /data/hermes/ka2_sng_port.py --apply --backup-dir /data/hermes/backups
Takes effect on the next ignition cycle - it is car-daemon code, so nothing is restarted here on purpose.
"""
import argparse
import os
import py_compile
import shutil
import sys

ROOT = "/data/openpilot"
SNG_HELPER = """from time import monotonic
import cereal.messaging as messaging
from opendbc.car import DT_CTRL

SNG_DREL_MIN_M = 2
SNG_DELAY_MARGIN_S = 0.4
SNG_VLEAD_RESUME_THRESHOLD = 0.1  # m/s
SNG_LEAD_TOLERANCE_S = 0.2


class SngHelper:
  def __init__(self, initial_delay_s):
    self.initial_delay = round((initial_delay_s + SNG_DELAY_MARGIN_S) / DT_CTRL)
    self.ready_enter_delay = round(SNG_DELAY_MARGIN_S / DT_CTRL)
    self.repeat_interval = round((1 + SNG_DELAY_MARGIN_S) / DT_CTRL)
    self.next = 0
    self.entered = False
    self.lead_latched = False
    self.lead_gap_at = None
    self.radar_sm = None

  def lead_ready(self):
    self.radar_sm = self.radar_sm or messaging.SubMaster(["radarState"])
    self.radar_sm.update(0)
    if (lead := self.radar_sm["radarState"].leadOne).status:
      self.lead_gap_at = None
    elif self.lead_latched:
      now = monotonic()
      if self.lead_gap_at is None:
        self.lead_gap_at = now
      elif now - self.lead_gap_at >= SNG_LEAD_TOLERANCE_S:
        self.lead_latched = False
    return self.lead_latched and lead.status and lead.vLead > SNG_VLEAD_RESUME_THRESHOLD and lead.dRel > SNG_DREL_MIN_M

  def pressed(self, frame):
    self.next = frame + self.repeat_interval

  def tick(self, frame, active, gas, res, brake, should_resume, hold_complete=False, ready_on_enter=False):
    if not active:
      self.entered = self.lead_latched = False
      self.lead_gap_at = None
      return False
    if not self.entered:
      self.entered = self.lead_latched = True
      self.lead_gap_at = None
      self.next = frame + (self.ready_enter_delay if ready_on_enter else self.initial_delay)
      return None
    if gas or res or brake or hold_complete:
      self.next = max(self.next, frame + self.repeat_interval)
      return None
    return should_resume and frame > self.next
"""

BYDCAN_NEW = '''def create_resume_sequence(packer, idle_dat: bytes, bus: int):
  """Stock ACC SNG SET+RES burst for Seal/SL7/Shark; clone idle PCM_BUTTONS, overlay press."""
  if len(idle_dat) < 8:
    return None
  _, press_dat, _ = send_buttons(packer, 1, 0, bus)
  msgs, counter = [], (idle_dat[6] >> 4) & 0xF
  for _ in range(3):
    counter = (counter + 1) & 0xF
    dat = bytearray(idle_dat)
    dat[0], dat[1] = press_dat[0], press_dat[1]
    dat[6] = (dat[6] & 0x0F) | ((counter & 0xF) << 4)
    dat[7] = byd_checksum(0x3B0, None, dat)
    msgs.append((0x3B0, bytes(dat), bus))
  return msgs


'''

CC_METHODS = '''  def _start_sng_resume_sequence(self, CS):
    if not (idle := CS.sng_pcm_idle) or len(idle) < 8 or not (msgs := create_resume_sequence(self.packer, idle, self.button_send_bus)):
      return None
    self.resume_sequence_frames = msgs[1:]
    self.resume_sequence_next_frame = self.frame + SNG_RESUME_STEP_FRAMES
    return msgs[0]

  def _update_sng(self, CC, CS, can_sends):
    if self.sng is None or self.CP.openpilotLongitudinalControl:
      return

    # Seal-style stock ACC SNG: drain SET+RES burst on main bus.
    if frames := self.resume_sequence_frames:
      if self.frame >= self.resume_sequence_next_frame:
        can_sends.append(frames.pop(0))
        self.resume_sequence_next_frame = self.frame + SNG_RESUME_STEP_FRAMES
        if not frames:
          self.sng.pressed(self.frame)
      if CS.res_btn or CS.out.gasPressed or CS.out.brakePressed:
        self.sng.pressed(self.frame)
      return

    # Latch ACC rising while already stopped so HUD lag still uses 0s not 30s delay.
    self.sng_ready_enter = ((cruise_enabled := CS.out.cruiseState.enabled) and not self.prev_cruise_enabled or self.sng_ready_enter) and CS.out.standstill
    self.prev_cruise_enabled = cruise_enabled

    if not CS.cruise_standstill:
      self.sng_saw_wait = self.sng_tx_count = 0
    elif CS.standstill_wait:
      self.sng_saw_wait = True

    if self.sng.tick(
      self.frame,
      CS.cruise_standstill,
      CS.out.gasPressed,
      CS.res_btn,
      CS.out.brakePressed,
      self.sng.lead_ready(),
      ready_on_enter=self.sng_ready_enter,
    ) is True and (CS.standstill_wait or not self.sng_saw_wait and not self.sng_tx_count):
      if frame := self._start_sng_resume_sequence(CS):
        can_sends.append(frame)
        self.sng_tx_count += 1

'''

# (file, old, new) - every `old` must match exactly once, or the script refuses
EDITS = [
  ("opendbc_repo/opendbc/car/byd/cam_lka/bydcan.py",
   "def send_buttons(packer, state, cancel, bus):",
   BYDCAN_NEW + "def send_buttons(packer, state, cancel, bus):"),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carstate.py",
   "    self.distance_val = 1\n",
   "    self.distance_val = 1\n"
   "    self.cruise_standstill = False\n"
   "    self.standstill_wait = False\n"
   "    self.sng_pcm_idle = None\n"),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carstate.py",
   '    self.lkas_rdy_btn = cp.vl["PCM_BUTTONS"]["LKAS_ON_BTN"]\n'
   '    self.res_btn = cp.vl["PCM_BUTTONS"]["RES_BTN"]\n',
   '    self.lkas_rdy_btn = (pcm := cp.vl["PCM_BUTTONS"])["LKAS_ON_BTN"]\n'
   '    self.res_btn = bool(pcm["RES_BTN"] and (pcm["SET_BTN"] or self.CP.carFingerprint in BYD_OP_LONG_PLATFORMS))\n'),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carstate.py",
   '      cruise_state = parser_alt.vl["ACC_HUD_ADAS"]["CRUISE_STATE"]\n'
   "      ret.cruiseState.enabled = cruise_state in (3, 5, 6, 7)\n",
   '      ret.cruiseState.enabled = (cruise_state := parser_alt.vl["ACC_HUD_ADAS"]["CRUISE_STATE"]) in (3, 5, 6, 7)\n'
   "      self.cruise_standstill = cruise_state in (6, 7) and ret.standstill\n"
   "      self.standstill_wait = cruise_state == 7\n"),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carcontroller.py",
   "  create_lkas_hud,\n",
   "  create_lkas_hud,\n  create_resume_sequence,\n"),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carcontroller.py",
   "from opendbc.car.byd.values import CarControllerParams\n",
   "from opendbc.car.byd.values import CarControllerParams\nfrom opendbc.car.sng_helper import SngHelper\n"),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carcontroller.py",
   "BUTTON_KEEPALIVE_FRAMES = 100\n",
   "BUTTON_KEEPALIVE_FRAMES = 100\n"
   "SNG_INITIAL_PRESS_DELAY_S = 30  # Seal/SL7/Shark only\n"
   "SNG_RESUME_STEP_FRAMES = 5  # 50 ms at 100 Hz\n"),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carcontroller.py",
   "    self.prev_res_press = False\n",
   "    self.prev_res_press = False\n    self.prev_cruise_enabled = self.sng_ready_enter = False\n"),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carcontroller.py",
   "    self.seal6_steer_override = False\n"
   "    self.seal6_override_clear = 0\n"
   "    self.seal6_override_enter = 0\n",
   "    self.seal6_steer_override = False\n"
   "    self.seal6_override_clear = 0\n"
   "    self.seal6_override_enter = 0\n"
   "    self.resume_sequence_frames = []\n"
   "    self.resume_sequence_next_frame = 0\n"
   "    self.sng_saw_wait = False\n"
   "    self.sng_tx_count = 0\n"
   "    self.sng = SngHelper(SNG_INITIAL_PRESS_DELAY_S) if CP.carFingerprint not in BYD_OP_LONG_PLATFORMS else None\n"
   "\n" + CC_METHODS),

  ("opendbc_repo/opendbc/car/byd/cam_lka/carcontroller.py",
   "    self._update_lka_latch_state(CS)\n",
   "    self._update_lka_latch_state(CS)\n    self._update_sng(CC, CS, can_sends)\n"),

  # the adaptation: their idle-frame capture hooks the newer update(can_packets) API, which this fork does
  # not have. card.py already rebuilds the CAN list every frame for the car state, and it is where the
  # button counter is followed, so the idle 0x3B0 frame is captured here - carried on CS so the car
  # controller (which owns CS) can clone it.
  #
  # The shape matters and is easy to get wrong: card's can_list is [(logMonoTime, [(addr, dat, src), ...])]
  # - a list of BATCHES of plain tuples, not flat CanData objects. A block keyed on CanData attributes
  # matches nothing at all and the try/except below hides it, which is exactly what happened to the first
  # version of this block (the counter continuation never ran, so every press sent counter 1). Verified
  # against the live bus: 60 src-0 idle frames in 3 s, counter rolling 6,7,8..15,0,1.
  ("selfdrive/car/card.py",
   "    can_list = can_capnp_to_list(can_strs)\n\n",
   "    can_list = can_capnp_to_list(can_strs)\n"
   "\n"
   "    # Track the car's own 0x3B0 button counter (byte 6 high nibble, src 0 = the car's module) so a press\n"
   "    # we inject continues its sequence instead of restarting at zero. Restarting is the last structural\n"
   "    # difference between the car's own rocker - which never disengages - and our presses, which drop it.\n"
   "    # can_list is a list of (logMonoTime, [(addr, dat, src), ...]) batches; tolerate a flat list too, and\n"
   "    # never let this scan become fatal: an exception in card takes the whole car daemon down (6 Oct 2026).\n"
   "    try:\n"
   "      for _batch in can_list:\n"
   "        _frames = (_batch[1] if (isinstance(_batch, (tuple, list)) and len(_batch) == 2\n"
   "                                 and isinstance(_batch[1], (list, tuple))) else [_batch])\n"
   "        for _f in _frames:\n"
   "          try:\n"
   "            _addr, _dat, _src = _f[0], bytes(_f[1]), _f[2]\n"
   "          except Exception:\n"
   "            continue        # one malformed entry skips itself; it must not end the scan\n"
   "          if _addr == 0x3B0 and _src == 0 and len(_dat) > 6:\n"
   "            self.acc_button_state[\"car_counter\"] = (_dat[6] >> 4) & 0x0F\n"
   "            if not _dat[0] & 0x18:      # no SET/RES bit set = the car's idle frame\n"
   "              self.CI.CS.sng_pcm_idle = _dat   # SNG clones this frame\n"
   "    except Exception:\n"
   "      pass\n"
   "\n"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--apply", action="store_true", help="write the files")
    ap.add_argument("--backup-dir", help="copy each file here before writing")
    ap.add_argument("--root", default=ROOT, help="tree root (for testing against a copy)")
    a = ap.parse_args()
    if not (a.check or a.apply):
        ap.error("--check or --apply")

    # Phase 1: validate every anchor and build the plan. Nothing is written until the whole port fits -
    # a half-applied port would leave openpilot unable to start, so a bad anchor must cost nothing.
    helper_path = os.path.join(a.root, "opendbc_repo/opendbc/car/sng_helper.py")
    if os.path.exists(helper_path) and open(helper_path).read() != SNG_HELPER:
        print("FAIL: sng_helper.py exists but differs from the ported version")
        return 1

    plan, touched = {}, []
    for rel, old, new in EDITS:
        path = os.path.join(a.root, rel)
        if not os.path.exists(path):
            print("FAIL: %s is missing" % rel)
            return 1
        src = plan.get(path) or open(path).read()
        # already-ported must be tested on the REPLACEMENT, not the anchor: the replacement contains the
        # anchor text, so an applied edit still matches `old` and a second run would add it again.
        if new in src:
            print("  %-58s already ported" % rel)
            continue
        n_old = src.count(old)
        if n_old != 1:
            print("FAIL: %-58s anchor matches %d times (expected exactly 1)" % (rel, n_old))
            return 1
        plan[path] = src.replace(old, new)
        touched.append(path)
        print("  %-58s %s" % (rel, "would change (+%d lines)" % (new.count("\n") - old.count("\n")) if a.check else "will be ported"))
    if not os.path.exists(helper_path):
        print("  opendbc/car/sng_helper.py: %s (%d lines)" % (
            "would be created" if a.check else "will be created", SNG_HELPER.count("\n")))

    if a.check:
        print("\ncheck only: %d file(s) would change, nothing written" % len(set(touched)))
        return 0

    # Phase 2: write, each file backed up first.
    if not os.path.exists(helper_path):
        open(helper_path, "w").write(SNG_HELPER)
    for path in dict.fromkeys(touched):
        if a.backup_dir:
            dest = os.path.join(a.backup_dir, os.path.basename(path) + ".pre-sng")
            shutil.copy2(path, dest)
            print("  backup: %s" % dest)
        open(path, "w").write(plan[path])
        print("  written: %s" % path)

    # a syntax check on every file we touched, so a broken port cannot reach the car
    bad = []
    for p in set(touched + [helper_path]):
        try:
            py_compile.compile(p, doraise=True, cfile="/tmp/_sng_check.pyc")
        except py_compile.PyCompileError as exc:
            bad.append((p, str(exc).splitlines()[-1][:120]))
    if bad:
        for p, err in bad:
            print("FAIL: %s does not compile: %s" % (p, err))
        return 1
    print("\nall %d file(s) compile. Takes effect on the next ignition cycle." % len(set(touched) | {helper_path}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
