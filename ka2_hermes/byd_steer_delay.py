#!/usr/bin/env python3
"""Fix the BYD steering actuator delay, and make the change survive an updater git reset.

The port ships ret.steerActuatorDelay = 0.01 (10 ms) in opendbc/car/byd/interface.py. Every comparable
angle-steer platform uses 0.1-0.2 s (Tesla 0.1, VW 0.07-0.2, Honda 0.1-0.15); the only other port with
0.01 is Chery, i.e. it is a template value, not a measured one. The controller uses this as its model of
how long the car takes to follow a steering command, so 10 ms makes it believe its commands land instantly
and keep demanding more steering before the car has responded. On a straight nothing is demanded (the
required angle is ~0) so it looks perfect; in a bend it overshoots - which is the reported behaviour.

What this does, idempotently:
  - rewrites the one line to 0.1 s, keeping a backup and the file's owner/mode
  - optionally (--fold-in) adds a call to itself inside /usr/kommu/hermes_ka2_patch.sh, after the existing
    hermes python step, so the updater's git reset cannot quietly take the change away again
  - refuses loudly if the line is in a state it does not recognise, rather than guessing

Default is a dry run; --apply writes. Takes effect on the next ignition cycle: interface.py runs at
startup, and a changed fork invalidates CarParamsCache so the port rebuilds its parameters.

Usage: byd_steer_delay.py [--apply] [--fold-in] [--value 0.1] [--show]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

TARGET = "/data/openpilot/opendbc_repo/opendbc/car/byd/interface.py"
PATCHER = "/usr/kommu/hermes_ka2_patch.sh"
BACKUP_DIR = "/data/hermes/backups"
SELF = "/data/hermes/byd_steer_delay.py"
MARKER = "HERMES_STEER_DELAY"
ANCHOR = "ka2_sng_port.py"
LINE_RE = re.compile(r"^(\s*)ret\.steerActuatorDelay\s*=\s*([0-9.]+)(.*)$", re.M)
NEW_VALUE = "0.1"


def owner_mode(path):
  st = os.stat(path)
  return st.st_uid, st.st_gid, st.st_mode & 0o777


def restore(path, meta):
  uid, gid, mode = meta
  os.chown(path, uid, gid)
  os.chmod(path, mode)


def apply_file(args):
  if not os.path.exists(TARGET):
    sys.exit("  %s not found" % TARGET)
  src = open(TARGET).read()
  hits = LINE_RE.findall(src)
  if len(hits) != 1:
    sys.exit("  expected exactly one steerActuatorDelay line, found %d - refusing" % len(hits))
  indent, old, tail = hits[0]
  if old == args.value:
    print("  already %.3f - nothing to do" % float(old))
    return 0
  if old != "0.01":
    sys.exit("  delay is %s, not the shipped 0.01 - refusing to touch an unexpected state" % old)
  new_line = "%sret.steerActuatorDelay = %s%s" % (indent, args.value, tail)
  old_line = "%sret.steerActuatorDelay = %s%s" % (indent, old, tail)
  print("  %s" % TARGET)
  print("    - %s" % old_line.strip())
  print("    + %s" % new_line.strip())
  if not args.apply:
    print("  (dry run; pass --apply to write)")
    return 0
  os.makedirs(BACKUP_DIR, exist_ok=True)
  meta = owner_mode(TARGET)
  shutil.copy2(TARGET, os.path.join(BACKUP_DIR, "byd_interface.py.pre-steerdelay"))
  out = src.replace(old_line, new_line, 1)
  with open(TARGET, "w") as fh:
    fh.write(out)
  os.chmod(TARGET, 0o644)
  restore(TARGET, meta)
  check = subprocess.run([sys.executable, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", TARGET],
                         capture_output=True, text=True)
  if check.returncode != 0:
    shutil.copy2(os.path.join(BACKUP_DIR, "byd_interface.py.pre-steerdelay"), TARGET)
    restore(TARGET, meta)
    sys.exit("  syntax check failed, reverted: %s" % check.stderr.strip())
  print("  written; backup at %s/byd_interface.py.pre-steerdelay" % BACKUP_DIR)
  return 0


def fold_in(args):
  if not os.path.exists(PATCHER):
    sys.exit("  %s not found" % PATCHER)
  src = open(PATCHER).read()
  if MARKER in src:
    print("  already folded into %s" % PATCHER)
    return 0
  print("  %s" % PATCHER)
  block = ('\n# %s: the BYD port ships a 10 ms steering actuator delay; comparable angle-steer platforms\n'
           '# use 0.1-0.2 s, so the controller over-commands in bends. Re-applied here because the\n'
           '# updater\'s git reset would otherwise restore the shipped value.\n'
           'if [ -f %s ]; then\n'
           '  python3 %s --apply --value %s || echo "steer delay: failed (see /data/hermes)"\n'
           'fi\n') % (MARKER, SELF, SELF, args.value)
  lines = src.splitlines(keepends=True)
  anchor_idx = None
  for i, ln in enumerate(lines):
    if ANCHOR in ln:
      anchor_idx = i
  if anchor_idx is None:
    sys.exit("  could not find the %s step to insert after - refusing to guess" % ANCHOR)
  # insert after the whole anchored command (its continuation lines included)
  j = anchor_idx
  while j + 1 < len(lines) and (lines[j].rstrip().endswith("\\") or
                                lines[j + 1].lstrip().startswith(("&&", "||", "|"))):
    j += 1
  out = "".join(lines[:j + 1]) + block + "".join(lines[j + 1:])
  if not args.apply:
    print(block)
    print("  (dry run; pass --apply to write)")
    return 0
  meta = owner_mode(PATCHER)
  shutil.copy2(PATCHER, PATCHER + ".pre-steerdelay")
  with open(PATCHER, "w") as fh:
    fh.write(out)
  restore(PATCHER, meta)
  chk = subprocess.run(["bash", "-n", PATCHER], capture_output=True, text=True)
  if chk.returncode != 0:
    shutil.copy2(PATCHER + ".pre-steerdelay", PATCHER)
    restore(PATCHER, meta)
    sys.exit("  bash -n failed, reverted: %s" % chk.stderr.strip())
  print("  folded in; syntax ok; old copy at %s.pre-steerdelay" % PATCHER)
  return 0


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--apply", action="store_true")
  ap.add_argument("--fold-in", action="store_true")
  ap.add_argument("--value", default=NEW_VALUE)
  a = ap.parse_args()
  rc = apply_file(a)
  if a.fold_in:
    rc |= fold_in(a)
  return rc


sys.exit(main())
