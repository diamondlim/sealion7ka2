#!/bin/sh
LOG=/data/hermes/logs/ap_nm.log
exec >>$LOG 2>&1
echo "=== $(date) switching wlan0 to AP"
nmcli con down "TP-LINK_POCKET_3020_2348D8"
sleep 3
nmcli con up KA2-Hotspot
sleep 14
echo "-- wlan0"; iw dev wlan0 info 2>&1 | grep -E "type|ssid|channel"
echo "-- addr"; ip -4 addr show wlan0 | grep inet
echo "-- default"; ip route | grep default
echo "-- inet test"; curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://www.google.com
echo "=== done $(date)"
