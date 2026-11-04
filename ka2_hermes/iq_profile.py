#!/usr/bin/env python3
"""Camera ISP profiles: day/night x dry/rain.

The 3A server reads the IQ file when it starts, so switching a profile means restarting the
camera stack. That is only safe with the car stationary and disengaged - apply() refuses
otherwise and says why, rather than blinding a moving car for ten seconds.

Every profile is generated from the *stock* tuning kept in STORE/base, never from the files
currently in /etc/iqfiles. Otherwise each switch would compound the previous one's gamma and
sharpening until the image was unusable.

  iq_profile.py status
  iq_profile.py build            # (re)generate all four from base
  iq_profile.py apply <name>
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

IQ_DIR = "/etc/iqfiles"
STORE = "/data/hermes/iq_profiles"
STATE = "/data/hermes/iq_profile.json"
POSE = "/dev/shm/ka2_pose.json"
ENGAGED = "/data/params/d/IsEngaged"
SENSOR_GLOB = "ox03c10_*.json"

# gamma: exponent on the normalised curve (<1 lifts mid-tones). shadow=True applies the lift
# only to the dark end and leaves the highlights alone - what rainy night needs, because the
# glare is in the highlights and the markings are in the shadows.
PROFILES = {
  "day_dry":    {"gamma": 0.85, "shadow": False, "contrast": 140, "sharp": 21,
                 "why": "paint on dark tarmac, no glare to protect"},
  "night_dry":  {"gamma": 0.80, "shadow": False, "contrast": 145, "sharp": 21,
                 "why": "more mid-tone lift for unlit road"},
  "day_rain":   {"gamma": 0.95, "shadow": False, "contrast": 150, "sharp": 17,
                 "why": "stop amplifying wet-road glare, keep edges honest"},
  "night_rain": {"gamma": 0.78, "shadow": True,  "contrast": 150, "sharp": 15,
                 "why": "lift only the shadows: headlights and standing water stay unblown"},
}
ORDER = ("day_dry", "night_dry", "day_rain", "night_rain")


def log(msg):
  print("PROFILE %s" % msg, flush=True)


def run(cmd, timeout=60):
  """Run a command non-interactively.

  stdin is /dev/null and sudo gets -n on purpose: this is called from a service that may have
  inherited a tty, and a sudo password prompt there does not fail - it waits forever, taking the
  whole settings endpoint down with it.
  """
  if cmd and cmd[0] == "sudo":
    cmd = ["sudo", "-n"] + cmd[1:]
  try:
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
  except Exception as exc:
    log("run failed: %s (%s)" % (" ".join(cmd[:3]), exc))
    return None


def base_dir():
  return os.path.join(STORE, "base")


def ensure_base():
  """Copy the stock IQ files into STORE/base once.

  The stock set is the oldest .orig backup made before any tuning; the current files in
  /etc/iqfiles already carry a profile and must never be used as a base.
  """
  d = base_dir()
  if glob.glob(os.path.join(d, SENSOR_GLOB)):
    return d
  os.makedirs(d, exist_ok=True)
  candidates = sorted(glob.glob(os.path.join(IQ_DIR, "ox03c10_*.orig-*.json")))
  if candidates:
    for src in candidates:
      name = os.path.basename(src).split(".orig-")[0] + ".json"
      shutil.copy2(src, os.path.join(d, name))
    return d
  # No backup: fall back to the live files, which is only correct on a profile-less box.
  for src in glob.glob(os.path.join(IQ_DIR, SENSOR_GLOB)):
    shutil.copy2(src, os.path.join(d, os.path.basename(src)))
  return d


def shape_curve(curve, gamma, shadow):
  """Apply a tone curve to a 0..N gamma table."""
  if not curve:
    return curve
  top = max(curve) or 4095
  out = []
  for v in curve:
    x = max(0.0, min(1.0, float(v) / top))
    lifted = x ** gamma
    if shadow:
      # Blend: full lift below 0.55, none above 0.85, linear in between.
      w = 1.0 if x <= 0.55 else (0.0 if x >= 0.85 else (0.85 - x) / 0.30)
      y = lifted * w + x * (1.0 - w)
    else:
      y = lifted
    out.append(int(round(top * y)))
  out[0] = curve[0]
  out[-1] = curve[-1]
  return out


def build_one(name, spec, outdir):
  os.makedirs(outdir, exist_ok=True)
  written = []
  for src in sorted(glob.glob(os.path.join(ensure_base(), SENSOR_GLOB))):
    d = json.load(open(src))
    for scene in d.get("main_scene", []):
      for sub in scene.get("sub_scene", []):
        isp = sub.get("scene_isp30", {})
        gt = isp.get("agamma_calib_v11", {}).get("GammaTuningPara", {})
        if isinstance(gt.get("Gamma_curve"), list):
          gt["Gamma_curve"] = shape_curve(gt["Gamma_curve"], spec["gamma"], spec["shadow"])
        cp = isp.get("cproc", {}).get("param", {})
        if "contrast" in cp:
          cp["contrast"] = spec["contrast"]
        sharp = isp.get("sharp_v4", {}).get("TuningPara", {})
        for setting in sharp.get("Setting", []):
          for iso in setting.get("Tuning_ISO", []):
            if isinstance(iso.get("sharp_ratio"), int):
              iso["sharp_ratio"] = spec["sharp"]
    dst = os.path.join(outdir, os.path.basename(src))
    with open(dst, "w") as fh:
      json.dump(d, fh, separators=(",", ":"))
    written.append(dst)
  return written


def build_all():
  for name in ORDER:
    files = build_one(name, PROFILES[name], os.path.join(STORE, name))
    log("built %s (%d files)" % (name, len(files)))


def vehicle_still():
  """(ok, reason): the camera stack may only be restarted with the car stopped."""
  try:
    if open(ENGAGED).read().strip() not in ("", "0"):
      return False, "engaged"
  except OSError:
    pass
  try:
    pose = json.load(open(POSE))
    if float(pose.get("v", 0.0)) > 0.5:
      return False, "moving (%.1f m/s)" % float(pose.get("v", 0.0))
  except Exception:
    pass
  return True, ""


def restart_camera():
  """Reload the 3A server and camerad so the new IQ file is actually read.

  The IQ file is opened by rkaiq_3A_server, not by camerad, so both are restarted and in that
  order. Killing the 3A server by hand was the earlier mistake: nothing supervises a killed
  process, so the camera came back with no AE/AWB at all.
  """
  run(["sudo", "systemctl", "restart", "rkaiq_3A.service"], timeout=60)
  time.sleep(3)
  run(["sudo", "systemctl", "restart", "kommu"], timeout=120)
  time.sleep(3)
  return True


def last_init():
  """(timestamp, iq path) of the most recent camera initialisation, or (None, "")."""
  res = run(["sudo", "grep", "-a", "rk_aiq_uapi_sysctl_init", "/var/log/syslog"], timeout=15)
  out = res.stdout if res else ""
  lines = [ln for ln in out.splitlines() if "iq:" in ln]
  if not lines:
    return None, ""
  parts = lines[-1].split("iq:")
  return parts[0].split(" ")[0], parts[-1].strip()


def loaded_iq():
  try:
    res = run(["sudo", "grep", "-a", "rk_aiq_uapi_sysctl_init", "/var/log/syslog"], timeout=15)
    out = res.stdout if res else ""
    lines = [ln for ln in out.splitlines() if "iq:" in ln]
    return lines[-1].split("iq:")[-1].strip() if lines else ""
  except Exception:
    return ""


def apply(name):
  if name not in PROFILES:
    return {"ok": False, "error": "unknown profile", "profiles": list(ORDER)}
  ok, why = vehicle_still()
  if not ok:
    return {"ok": False, "error": "refused: car is %s" % why}
  src = os.path.join(STORE, name)
  if not glob.glob(os.path.join(src, SENSOR_GLOB)):
    build_one(name, PROFILES[name], src)
  before, _ = last_init()
  for f in glob.glob(os.path.join(src, SENSOR_GLOB)):
    run(["sudo", "cp", "-f", f, os.path.join(IQ_DIR, os.path.basename(f))], timeout=20)
  restart_camera()
  after, iq = last_init()
  active = run(["sudo", "systemctl", "is-active", "rkaiq_3A.service"], timeout=10)
  reloaded = bool(after) and after != before and (active is not None and active.stdout.strip() == "active")
  state = {"ok": reloaded, "profile": name, "at": time.time(), "why": PROFILES[name]["why"], "iq": iq,
           "error": None if reloaded else "camera did not reinitialise - check rkaiq_3A.service"}
  with open(STATE, "w") as fh:
    json.dump(state, fh)
  return state


def status():
  try:
    state = json.load(open(STATE))
  except Exception:
    state = {"profile": None}
  state.setdefault("profiles", list(ORDER))
  state["profiles_why"] = {k: PROFILES[k]["why"] for k in ORDER}
  state["iq_loaded"] = loaded_iq()
  return state


def main(argv):
  cmd = argv[1] if len(argv) > 1 else "status"
  # Every result is prefixed with "PROFILE " so the settings service and the app can pick it out
  # of the stream without guessing which line is the answer.
  if cmd == "status":
    print("PROFILE " + json.dumps(status(), separators=(",", ":")))
  elif cmd == "build":
    build_all()
    print("PROFILE " + json.dumps({"ok": True, "profiles": list(ORDER)}))
  elif cmd == "apply":
    name = argv[2] if len(argv) > 2 else ""
    print("PROFILE " + json.dumps(apply(name), separators=(",", ":")))
  else:
    print("PROFILE " + json.dumps({"ok": False, "error": "unknown command"}))
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
