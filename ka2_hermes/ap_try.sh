#!/bin/sh
LOG=/data/hermes/logs/ap_try.log
: > $LOG
exec >>$LOG 2>&1
echo "start $(date)"
pkill -x hostapd
iw dev wlan1 del 2>/dev/null
sleep 1
iw dev wlan0 interface add wlan1 type __ap || { echo CREATE_FAIL; exit 1; }
nmcli device set wlan1 managed no 2>/dev/null
ip link set wlan1 up
echo "type before hostapd: $(iw dev wlan1 info | grep type)"
timeout 10 hostapd -dd /data/hermes/hostapd_ka2_5g.conf
echo "hostapd exited rc=$?"
echo "type after: $(iw dev wlan1 info | grep type)"
echo "uplink: $(iw dev wlan0 link | head -1)"
