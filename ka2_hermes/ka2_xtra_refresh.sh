#!/usr/bin/env bash
# Refresh gpsOneXTRA predicted orbits in the modem's GNSS engine.
#
# The module reports the window LENGTH in AT+QGPSXTRADATA? (`10080,"<injected date>"`), not the time
# remaining -- so the old check ("> 2880 minutes left") read 10080 forever and only refreshed AFTER the
# data had already expired. Age is computed from the injected date instead.
set -u
LOG=/data/hermes/logs/ka2_xtra_refresh.log
# Durable location: /tmp is cleared on every reboot, which is how this script silently did nothing for
# days ("injector missing at /tmp/ka2_xtra_inject.py - nothing done").
TUNE=/data/hermes/ka2_xtra_inject.py
FILE=/tmp/xtra2.bin
URL=http://xtrapath4.izatcloud.net/xtra2.bin
mkdir -p "$(dirname "$LOG")"
stamp() { date -Is; }
say() { echo "$(stamp) $* " >> "$LOG"; }

if [ ! -f "$TUNE" ]; then say "injector missing at $TUNE - nothing done"; exit 0; fi

state=$(sudo -n mmcli -m 0 --command 'AT+QGPSXTRADATA?' 2>/dev/null | sed -n 's/.*+QGPSXTRADATA: \([0-9]*\),"\([^"]*\)".*/\1 \2/p')
window=${state%% *}
injected=${state#* }
stale=1
if [ -n "${window:-}" ] && [ "${window:-0}" -gt 0 ]; then
  # refresh once the orbits are ~4 days old (the window is 7 days)
  age=$(python3 - "$injected" <<'PY'
import sys, time
try:
    t = time.mktime(time.strptime(sys.argv[1], "%Y/%m/%d,%H:%M:%S"))
    print(int((time.time() - t) / 86400))
except Exception:
    print(99)
PY
)
  say "orbits: window ${window} min, injected ${injected}, age ${age} d"
  [ "${age:-99}" -lt 4 ] && { say "still fresh - no action"; exit 0; }
fi

say "assistance data stale or absent (${state:-no answer}) - refreshing"
curl -s -m 120 -o "$FILE" "$URL" || { say "download failed"; exit 0; }
size=$(stat -c %s "$FILE" 2>/dev/null || echo 0)
[ "$size" -gt 10000 ] || { say "downloaded file too small ($size bytes)"; exit 0; }

sudo -n systemctl stop ka2-gnss-at.service
out=$(python3 "$TUNE" --variants 2>&1)
sudo -n systemctl start ka2-gnss-at.service
echo "$out" >> "$LOG"
if echo "$out" | grep -q "orbit data injected and held"; then
  say "OK: re-injected $size bytes of predicted orbits"
else
  say "FAILED to inject - see above"
fi
