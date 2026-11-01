#!/bin/sh
# Ensure the KA2 hotspot really beacons after boot, LTE has a real address, and NAT is sane.
LOG=/data/hermes/logs/ka2_post_boot.log
exec >>$LOG 2>&1
echo "=== $(date) post-boot"

for i in $(seq 1 30); do
  nmcli -t -f RUNNING general 2>/dev/null | grep -q running && break
  sleep 2
done

# LTE: NetworkManager can report "connected" while the PDP context never got an address,
# so wait for an actual IPv4 on wwan0 and bounce the profile if it does not appear.
lte_ip() { ip -4 addr show wwan0 2>/dev/null | grep -q "inet "; }

nmcli con up digi 2>&1 | tail -1
for i in $(seq 1 20); do lte_ip && break; sleep 2; done
if ! lte_ip; then
  echo "wwan0 has no address after 40s, bouncing digi"
  nmcli con down digi 2>&1 | tail -1
  sleep 3
  nmcli con up digi 2>&1 | tail -1
  for i in $(seq 1 20); do lte_ip && break; sleep 2; done
fi
echo "wwan0: $(ip -4 addr show wwan0 2>/dev/null | grep -o 'inet [0-9.]*/[0-9]*')"

nmcli con down KA2-Hotspot 2>/dev/null
sleep 2
nmcli con up KA2-Hotspot 2>&1 | tail -1
sleep 6
echo "wlan0 type: $(iw dev wlan0 info 2>/dev/null | grep type)"

iptables -t mangle -C POSTROUTING -o wwan0 -j TTL --ttl-set 64 2>/dev/null || iptables -t mangle -A POSTROUTING -o wwan0 -j TTL --ttl-set 64
iptables -t mangle -C FORWARD -o wwan0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || iptables -t mangle -A FORWARD -o wwan0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

echo "=== done $(date)"
