#!/bin/sh
# one-shot AP test on the uplink channel; reverts itself if the STA link dies
LOG=/data/hermes/logs/ap_test.log
exec >>$LOG 2>&1
echo "=== $(date) start"
iw dev wlan1 del 2>/dev/null
iw dev wlan0 interface add wlan1 type __ap || { echo "CREATE FAILED"; exit 1; }
ip link set wlan1 up
hostapd -B /data/hermes/hostapd_ka2_5g.conf || { echo "HOSTAPD FAILED"; iw dev wlan1 del; exit 1; }
sleep 6
if iw dev wlan0 link 2>/dev/null | grep -q "Connected to"; then
  echo "UPLINK SURVIVED"
  ip addr add 10.42.0.1/24 dev wlan1 2>/dev/null
  dnsmasq --conf-file=/data/hermes/dnsmasq_ka2.conf
  iptables -t nat -C POSTROUTING -o wlan0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
  iptables -C FORWARD -i wlan1 -o wlan0 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wlan1 -o wlan0 -j ACCEPT
  iptables -C FORWARD -i wlan0 -o wlan1 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wlan0 -o wlan1 -m state --state RELATED,ESTABLISHED -j ACCEPT
  echo "AP UP on wlan1 10.42.0.1 ssid KA2-Hotspot"
else
  echo "UPLINK DIED -> reverting"
  pkill -f hostapd
  iw dev wlan1 del 2>/dev/null
  systemctl restart NetworkManager
fi
echo "=== $(date) done"
