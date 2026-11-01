#!/usr/bin/env bash
# Install the GNSS-engine keeper on the KA2. Runs ON THE BOX as root, idempotent.
#
#   ka2_gnss_engine_install.sh            # install/refresh the units and start them
#   ka2_gnss_engine_install.sh --check    # print the engine state and exit
#
# What it installs: ka2-gnss-engine.service (runs the keeper once) and ka2-gnss-engine.timer (every ten
# minutes plus two minutes after boot), so the modem's GNSS engine is started and verified without anyone
# remembering to do it. The script itself lives at /data/hermes/ka2_gnss_engine_up.sh so it survives the
# vendor resetting the read-only root filesystem.
set -u

DIR=/data/hermes
UNITS=/etc/systemd/system
SCRIPT=$DIR/ka2_gnss_engine_up.sh

if [ "${1:-}" = "--check" ]; then
  exec bash "$SCRIPT" --check
fi

if [ ! -f "$SCRIPT" ]; then
  echo "missing $SCRIPT - deploy it first" >&2
  exit 1
fi
chmod +x "$SCRIPT"

for unit in ka2-gnss-engine.service ka2-gnss-engine.timer; do
  src=$DIR/systemd/$unit
  [ -f "$src" ] || { echo "missing $src" >&2; exit 1; }
  install -m 644 "$src" "$UNITS/$unit"
done

systemctl daemon-reload
systemctl enable --now ka2-gnss-engine.timer >/dev/null 2>&1
# Run it now rather than waiting for the timer: the car may have just been started.
systemctl start ka2-gnss-engine.service

echo "installed: ka2-gnss-engine.timer $(systemctl is-enabled ka2-gnss-engine.timer 2>/dev/null || echo '?') / $(systemctl is-active ka2-gnss-engine.timer 2>/dev/null || echo '?')"
echo "--- engine state ---"
bash "$SCRIPT" --check
