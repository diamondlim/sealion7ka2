#!/bin/sh
# KA2 hotspot: AP on wlan1 (2.4GHz), internet comes in on wlan0 (STA, 5GHz)
IF=wlan1
IP=10.42.0.1
case "$1" in
start)
  iw dev $IF del 2>/dev/null
  iw dev wlan0 interface add $IF type __ap || exit 1
  nmcli device set $IF managed no 2>/dev/null
  ip addr flush dev $IF 2>/dev/null
  ip addr add $IP/24 dev $IF
  ip link set $IF up
  hostapd -B /data/hermes/hostapd_ka2.conf || exit 1
  dnsmasq --conf-file=/data/hermes/dnsmasq_ka2.conf || exit 1
  iptables -t nat -C POSTROUTING -o wlan0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
  iptables -C FORWARD -i $IF -o wlan0 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i $IF -o wlan0 -j ACCEPT
  iptables -C FORWARD -i wlan0 -o $IF -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wlan0 -o $IF -m state --state RELATED,ESTABLISHED -j ACCEPT
  echo "hotspot started on $IF ($IP)"
  ;;
stop)
  pkill -f "hostapd /data/hermes/hostapd_ka2.conf"
  pkill -f "dnsmasq --conf-file=/data/hermes/dnsmasq_ka2.conf"
  iptables -t nat -D POSTROUTING -o wlan0 -j MASQUERADE 2>/dev/null
  iptables -D FORWARD -i $IF -o wlan0 -j ACCEPT 2>/dev/null
  iptables -D FORWARD -i wlan0 -o $IF -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null
  iw dev $IF del 2>/dev/null
  echo "hotspot stopped"
  ;;
status)
  iw dev $IF info 2>/dev/null || echo "no $IF"
  pgrep -a hostapd; pgrep -a dnsmasq
  iptables -t nat -S POSTROUTING | grep MASQUERADE
  ;;
*) echo "usage: $0 start|stop|status" ;;
esac
