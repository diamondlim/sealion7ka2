#!/usr/bin/env bash
# Keep the modem's GNSS engine running on the KA2.
#
# Why this exists: the GNSS engine is OFF after every power cycle and nothing on the device turns it on
# again. The discrete module has its own bring-up (ka2-gnss-bringup.service) but the modem's engine is a
# separate receiver, and while it is off the module's own position stream still reaches the logs - just
# from fewer satellites, and on a bad day from none. A drive logged with the engine off has no usable
# track, which reads downstream as "this box has no GPS".
#
# Order matters, and it is the order the EC2x GNSS application note gives:
#   1. a wrong clock makes orbit injection fail, so fix the clock first (a power cycle leaves 1980);
#   2. top up gpsOneXTRA predicted orbits (handled by ka2_xtra_refresh.sh, which only acts when stale);
#   3. start the engine, then RE-READ the flag - a write that was accepted and ignored is the failure
#      mode this whole chain keeps meeting.
#
# Usage
#   ka2_gnss_engine_up.sh          # act if needed; prints only when it changed something
#   ka2_gnss_engine_up.sh --check  # print the state, change nothing
set -u

LOG=/data/hermes/logs/ka2_gnss_engine.log
DIR=/data/hermes
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

say() { echo "$(date -Is) $*" >>"$LOG"; }
at() { sudo -n mmcli -m 0 --command "$1" 2>/dev/null; }
field() { sed -n "s/.*$1: *\([0-9-]*\).*/\1/p" | head -1; }

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

# --- 1. the engine flag, before anything else --------------------------------------------------------
engine=$(at 'AT+QGPS?' | field '+QGPS')
engine=${engine:-}

# --- 2. the modem's clock, which orbit injection depends on -------------------------------------------
modem_clock=$(at 'AT+CCLK?' | sed -n 's/.*"\([^"]*\)".*/\1/p' | head -1)
modem_date=${modem_clock%%+*}
modem_date=${modem_date%,*}
now_utc=$(date -u '+%y/%m/%d,%H:%M:%S')
# Compare the calendar day only: a quarter-hour zone offset must not read as a wrong clock.
clock_ok=0
[ -n "$modem_date" ] && [ "${modem_date%%%,*}" = "${now_utc%%%,*}" ] && clock_ok=1

# --- 3. the publisher for the discrete module, if it was installed -----------------------------------
pub_state="not installed"
if systemctl list-unit-files 2>/dev/null | grep -q '^ka2-gnss-pub.service'; then
  pub_state=$(systemctl is-active ka2-gnss-pub.service 2>/dev/null || true)
fi

if [ "$CHECK" = "1" ]; then
  fix=$(at 'AT+QGPSLOC?' | sed -n 's/.*+QGPSLOC: *//p' | head -1)
  echo "engine:      ${engine:-no answer}   (1 = running)"
  echo "modem clock: ${modem_clock:-no answer}   (host UTC ${now_utc#*,})"
  echo "fix:         ${fix:-none reported}"
  echo "publisher:   ${pub_state}"
  exit 0
fi

changed=""

if [ "$clock_ok" != "1" ]; then
  at "AT+CCLK=\"${now_utc}+00\"" >/dev/null
  # Also let the modem keep its own RTC in step with the network from here on, so a power cycle does not
  # leave it in 1980 again. Harmless to repeat; it is only reached when the clock was actually wrong.
  at 'AT+CTZU=1' >/dev/null
  recheck=$(at 'AT+CCLK?' | sed -n 's/.*"\([^"]*\)".*/\1/p' | head -1)
  changed="$changed clock=${modem_clock:-none}->${recheck:-?}"
  say "clock was wrong (${modem_clock:-none}) - set to ${now_utc}, now ${recheck:-?}"
fi

# Orbits: the refresher decides for itself whether the injected window is stale (its own log holds the
# detail; nothing is printed here when there is nothing to do).
if [ -x "$DIR/ka2_xtra_refresh.sh" ]; then
  "$DIR/ka2_xtra_refresh.sh" >/dev/null 2>&1 || true
fi

if [ "${engine:-0}" != "1" ]; then
  at 'AT+QGPS=1' >/dev/null
  sleep 3
  after=$(at 'AT+QGPS?' | field '+QGPS')
  if [ "${after:-0}" = "1" ]; then
    changed="$changed engine=on"
    say "engine was off - started, verified running"
  else
    say "engine was off - AT+QGPS=1 did NOT take (re-read: ${after:-no answer})"
    changed="$changed engine=FAILED"
  fi
fi

if [ "$pub_state" = "inactive" ] || [ "$pub_state" = "failed" ]; then
  systemctl start ka2-gnss-pub.service 2>/dev/null && changed="$changed publisher=started"
fi

# Silent when there was nothing to do: this runs every ten minutes from a timer and a chatty log is a
# log nobody reads.
[ -n "$changed" ] && echo "ka2 gnss engine:$changed"
exit 0
