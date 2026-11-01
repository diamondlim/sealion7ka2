#!/usr/bin/env bash
# Installs smbclient on the box, so the image can be pushed straight to the NAS from the car
# instead of crawling through the tailnet. Runs detached: the link to the car drops often.
set -uo pipefail
echo "=== installing smbclient, start $(date -u +%FT%TZ)"
echo "links: $(cat /data/params/d/IsOnroad 2>/dev/null | sed 's/^/onroad=/')  free: $(df -h /data | awk 'NR==2{print $4}')"
echo "--- apt-get update"
sudo apt-get update 2>&1 | tail -6
echo "--- apt-get install smbclient"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y smbclient 2>&1 | tail -20
echo "=== result: $(command -v smbclient || echo NOT-INSTALLED)"
smbclient --version 2>/dev/null | sed 's/^/  /'
echo "INSTALL_DONE"
