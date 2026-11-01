#!/bin/sh
# Put the vision-acc parameters back into the app - the whole thing as one step, because the rows are
# useless on their own: they write to a config that ka2-vision-acc reads, so the rows, the enable flag and
# the service have to move together or the app offers knobs that do nothing.
#
#   ka2_restore_vis_all.sh --check    report what would change, touch nothing
#   ka2_restore_vis_all.sh --apply    do it, with backups
#
# Rollback is printed at the end of an --apply.
set -eu

RESTORE=/data/hermes/ka2_restore_vis_rows.py
SVC=/data/hermes/bt_settings_service.py
TUNE=/data/hermes/tuning.json
BACKUPS=/data/hermes/backups
UNIT=ka2-vision-acc

mode="${1:---check}"
[ "$mode" = "--check" ] || [ "$mode" = "--apply" ] || { echo "usage: $0 [--check|--apply]"; exit 2; }

# systemctl prints the state AND exits non-zero for inactive/disabled, so take the line, never the status.
unit_active=$(systemctl is-active "$UNIT" 2>/dev/null | head -1 || true); [ -n "$unit_active" ] || unit_active=inactive
unit_enabled=$(systemctl is-enabled "$UNIT" 2>/dev/null | head -1 || true); [ -n "$unit_enabled" ] || unit_enabled=disabled
rows_now=$(grep -c 'VIS_' "$SVC" || true)
enabled_now=$(grep -o '"VIS_TURN_ACC_ENABLED": *[0-9]*' "$TUNE" | grep -o '[0-9]*$' || true); [ -n "$enabled_now" ] || enabled_now="?"

echo "now: VIS_ refs in service=$rows_now  VIS_TURN_ACC_ENABLED=$enabled_now  $UNIT=$unit_active"
echo
echo "--- the restore's own check ---"
/usr/bin/python3 "$RESTORE" --check || exit 1
echo
echo "--- and these two, which the rows need to mean anything ---"
if [ "$enabled_now" = "1" ]; then
  echo "  VIS_TURN_ACC_ENABLED already 1"
else
  echo "  VIS_TURN_ACC_ENABLED: $enabled_now -> 1   ($TUNE)"
fi
if [ "$unit_active" = "active" ]; then
  echo "  $UNIT already active"
else
  echo "  $UNIT: $unit_active -> enable + start"
fi

if [ "$mode" = "--check" ]; then
  echo
  echo "check only: nothing written"
  exit 0
fi

echo
echo "--- applying, backups first ---"
mkdir -p "$BACKUPS"
cp -a "$SVC" "$BACKUPS/bt_settings_service.py.pre-restore"
cp -a "$TUNE" "$BACKUPS/tuning.json.pre-restore"
echo "  backup: $BACKUPS/bt_settings_service.py.pre-restore"
echo "  backup: $BACKUPS/tuning.json.pre-restore"

/usr/bin/python3 "$RESTORE" --apply --backup "$BACKUPS/bt_settings_service.py.pre-restore.prog" || exit 1

# The flag: edit the value in place so the file's own formatting and any comments survive.
/usr/bin/python3 - "$TUNE" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
new, n = re.subn(r'("VIS_TURN_ACC_ENABLED"\s*:\s*)[0-9]+', r'\g<1>1', s)
if n == 0:
    new = re.sub(r'(\n\s*)\}', r'\1"VIS_TURN_ACC_ENABLED": 1\n}', s, count=1)
    if new == s:
        sys.exit("could not find VIS_TURN_ACC_ENABLED and could not insert it")
open(p, "w").write(new)
print("  set VIS_TURN_ACC_ENABLED = 1")
PY

systemctl enable --now "$UNIT"
sleep 2

echo
echo "--- verify ---"
/usr/bin/python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read()); print('  service parses ok')" "$SVC"
echo "  VIS_ refs in service: $rows_now -> $(grep -c 'VIS_' "$SVC" || true)"
echo "  VIS_TURN_ACC_ENABLED: $(grep -o '\"VIS_TURN_ACC_ENABLED\": *[0-9]*' "$TUNE")"
echo "  $UNIT: $unit_active/$unit_enabled -> $(systemctl is-active "$UNIT" 2>/dev/null | head -1 || true)/$(systemctl is-enabled "$UNIT" 2>/dev/null | head -1 || true)"
systemctl --no-pager --lines=5 status "$UNIT" 2>&1 | tail -5 | sed 's/^/    /' || true

echo
echo "rollback:"
echo "  sudo systemctl disable --now $UNIT"
echo "  sudo cp $BACKUPS/bt_settings_service.py.pre-restore $SVC"
echo "  sudo cp $BACKUPS/tuning.json.pre-restore $TUNE"
