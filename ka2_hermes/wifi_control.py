#!/usr/bin/env python3
"""Wi-Fi control, so the app can move the box onto another network.

The box runs NetworkManager, so nmcli does the work. Two things matter more than the plumbing:

  * **A Wi-Fi password must never reach a log.** The service logs a session's opening commands, and the
    audit log records accepted changes, so the WIFI commands are the one place where a secret can travel
    through this protocol. `redact()` is applied to every logged line, and nmcli's own output is never
    echoed back to the phone (it can quote the password on failure).
  * **A SSID may contain a colon and a password may contain anything**, so nmcli's terse output is
    unescaped properly here rather than split on ":".
"""
import json
import os
import subprocess
import time

NMCLI = "/usr/bin/nmcli"
IW = "/usr/sbin/iw"
IPTABLES = "/usr/sbin/iptables"
HOTSPOT_CON = "KA2-Hotspot"
SIM_CON = "digi"
LEASES = "/var/lib/NetworkManager/dnsmasq-wlan0.leases"
# Digi (and several Malaysian carriers) drop forwarded traffic whose TTL is not 64: a hotspot client's
# packets arrive here as 63 after the Wi-Fi hop. The MSS clamp covers a smaller path MTU on the LTE side.
TTL_RULE = ["-t", "mangle", "POSTROUTING", "-o", "wwan0", "-j", "TTL", "--ttl-set", "64"]
MSS_RULE = ["-t", "mangle", "FORWARD", "-o", "wwan0", "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
            "-j", "TCPMSS", "--clamp-mss-to-pmtu"]
MAX_SSID = 64          # 802.11 limit, in bytes


def redact(line):
  """The line as it may be written to a log: a Wi-Fi password never goes in one."""
  text = (line or "").strip()
  head = " ".join(text.split()[:2]).upper()
  if head in ("WIFI CONNECT", "UPLINK WIFI"):
    return head + " [password redacted]"
  return text


def split_terse(line):
  """nmcli -t separates fields with ':' and escapes a literal ':' inside a value as '\\:'."""
  out, current, escaped = [], "", False
  for ch in line:
    if escaped:
      current += ch
      escaped = False
    elif ch == "\\":
      escaped = True
    elif ch == ":":
      out.append(current)
      current = ""
    else:
      current += ch
  out.append(current)
  return out


def nmcli(args, timeout=25):
  """Run nmcli. Returns stdout, or '' on any failure - callers report, they never raise."""
  try:
    done = subprocess.run([NMCLI] + list(args), capture_output=True, text=True, timeout=timeout)
    return done.stdout or ""
  except Exception:
    return ""


def wifi_status(ip_of):
  """The network the box is on now: what the phone would call it, and the address it holds."""
  status = {"ssid": "", "device": "", "ip": "", "signal": "", "connected": False}
  for line in nmcli(["-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"]).splitlines():
    parts = split_terse(line)
    # nmcli names the type "802-11-wireless" (older builds say "wifi"); matching only the short form
    # silently produced an empty status while the list worked perfectly
    if len(parts) >= 3 and "wireless" in parts[2]:
      status["ssid"] = parts[0]
      status["device"] = parts[1]
      status["connected"] = True
  if status["device"]:
    status["ip"] = ip_of(status["device"]) or ""
    for line in nmcli(["-t", "-f", "IN-USE,SIGNAL", "device", "wifi", "list"]).splitlines():
      parts = split_terse(line)
      if len(parts) >= 2 and parts[0].strip() == "*":
        status["signal"] = parts[1]
  return status


def wifi_list():
  """Nearby networks, strongest first, one entry per SSID."""
  found = {}
  for line in nmcli(["-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list"]).splitlines():
    parts = split_terse(line)
    if len(parts) < 4:
      continue
    ssid, signal, security, in_use = parts[0], parts[1], parts[2], parts[3]
    if not ssid:
      continue
    try:
      strength = int(signal)
    except Exception:
      strength = 0
    entry = {"ssid": ssid, "signal": strength,
             "sec": "" if security in ("", "--") else security,
             "in_use": in_use.strip() == "*"}
    if ssid not in found or strength > found[ssid]["signal"]:
      found[ssid] = entry
  return sorted(found.values(), key=lambda e: -e["signal"])


def wifi_connect(payload):
  """Join a network. The reply never contains nmcli's output, which can quote the password."""
  ssid = str(payload.get("ssid", "")).strip()
  password = str(payload.get("password", ""))
  if not ssid or len(ssid.encode("utf-8")) > MAX_SSID:
    return "ERR WIFI bad-ssid (1 to %d bytes)" % MAX_SSID
  if len(password) > 128:
    return "ERR WIFI bad-password"
  args = ["device", "wifi", "connect", ssid]
  if password:
    args += ["password", password]
  out = nmcli(args, timeout=45).lower()
  if "successfully activated" in out or "connection successfully activated" in out:
    return "OK WIFI ssid=%s connected" % ssid
  # No detail: an nmcli failure message can contain the password.
  return "ERR WIFI could-not-connect %s (check the password, or the network's security)" % ssid


def wifi_forget(ssid):
  """Drop a saved network, so a stale one cannot be picked up again by itself."""
  ssid = (ssid or "").strip()
  if not ssid:
    return "ERR WIFI bad-ssid"
  nmcli(["connection", "delete", ssid], timeout=25)
  return "OK WIFI ssid=%s forgotten" % ssid


def wifi_reply(command, payload, ip_of):
  """Every WIFI subcommand, returning the lines to send back."""
  if command == "STATUS":
    return ["W " + json.dumps(wifi_status(ip_of), separators=(",", ":"))]
  if command == "LIST":
    rows = wifi_list()
    out = ["W " + json.dumps(row, separators=(",", ":")) for row in rows]
    out.append("E %d" % len(rows))
    return out
  if command == "CONNECT":
    return [wifi_connect(payload or {})]
  if command == "FORGET":
    return [wifi_forget((payload or {}).get("ssid", ""))]
  return ["ERR WIFI unknown-subcommand (STATUS, LIST, CONNECT, FORGET)"]


# ---------------------------------------------------------------- hotspot / uplink

def _run(argv, timeout=30):
  try:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
  except Exception:
    return None


def _iptables(args):
  return _run([IPTABLES] + list(args), timeout=20)


def ensure_hotspot_nat():
  """Re-arm the carrier workarounds; they are runtime state and vanish on reboot."""
  for rule in (TTL_RULE, MSS_RULE):
    if _iptables(["-C"] + rule) is None or _iptables(["-C"] + rule).returncode != 0:
      _iptables(["-A"] + rule)


def wlan0_type():
  out = _run([IW, "dev", "wlan0", "info"], timeout=10)
  for line in (out.stdout if out else "").splitlines():
    if line.strip().startswith("type"):
      return line.split()[-1]
  return ""


def hotspot_clients():
  try:
    with open(LEASES) as fh:
      return sum(1 for line in fh if line.strip())
  except Exception:
    return 0


def hotspot_status(ip_of):
  kind = wlan0_type().upper()
  return {"on": kind == "AP", "ssid": HOTSPOT_CON, "device": "wlan0",
          "ip": ip_of("wlan0") or "", "clients": hotspot_clients(),
          "iftype": kind or "unknown"}


def hotspot_on(ip_of):
  nmcli(["connection", "up", HOTSPOT_CON], timeout=45)
  for _ in range(20):
    if wlan0_type().upper() == "AP":
      break
    time.sleep(1)
  ensure_hotspot_nat()
  st = hotspot_status(ip_of)
  if st["on"]:
    return "OK HOTSPOT on ssid=%s ip=%s" % (HOTSPOT_CON, st["ip"] or "?")
  return "ERR HOTSPOT did-not-start (wlan0 type is %s)" % st["iftype"]


def hotspot_off():
  nmcli(["connection", "down", HOTSPOT_CON], timeout=30)
  return "OK HOTSPOT off"


def hotspot_reply(command, payload, ip_of):
  if command == "ON":
    return [hotspot_on(ip_of)]
  if command == "OFF":
    return [hotspot_off()]
  if command == "STATUS":
    return ["H " + json.dumps(hotspot_status(ip_of), separators=(",", ":"))]
  return ["ERR HOTSPOT unknown-subcommand (ON, OFF, STATUS)"]


def default_dev():
  out = _run(["/usr/sbin/ip", "route", "show", "default"], timeout=10)
  for line in (out.stdout if out else "").splitlines():
    if " dev " in line:
      return line.split(" dev ")[1].split()[0]
  return ""


def uplink_status(ip_of):
  sim, wifi = False, ""
  for line in nmcli(["-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"]).splitlines():
    parts = split_terse(line)
    if len(parts) < 3:
      continue
    if parts[1] == "cdc-wdm0":
      sim = True
    if "wireless" in parts[2] and parts[0] != HOTSPOT_CON:
      wifi = parts[0]
  return {"default": default_dev(), "sim": sim, "wifi": wifi,
          "hotspot": hotspot_status(ip_of)["on"]}


def uplink_sim(ip_of):
  nmcli(["connection", "up", SIM_CON], timeout=45)
  ensure_hotspot_nat()
  return "OK UPLINK sim default=%s" % (default_dev() or "?")


def uplink_wifi(payload, ip_of):
  ssid = str(payload.get("ssid", "")).strip()
  password = str(payload.get("password", ""))
  if not ssid or len(ssid.encode("utf-8")) > MAX_SSID:
    return "ERR UPLINK bad-ssid (1 to %d bytes)" % MAX_SSID
  if len(password) > 128:
    return "ERR UPLINK bad-password"
  # Joining a network takes the radio out of AP mode: this box cannot be AP and station at once.
  args = ["device", "wifi", "connect", ssid]
  if password:
    args += ["password", password]
  out = nmcli(args, timeout=60).lower()
  if "successfully activated" in out or "connection successfully activated" in out:
    return "OK UPLINK wifi ssid=%s default=%s" % (ssid, default_dev() or "?")
  return "ERR UPLINK could-not-connect %s (check the password; joining Wi-Fi also stops the hotspot)" % ssid


def uplink_reply(command, payload, ip_of):
  if command == "SIM":
    return [uplink_sim(ip_of)]
  if command == "WIFI":
    return [uplink_wifi(payload or {}, ip_of)]
  if command == "STATUS":
    return ["U " + json.dumps(uplink_status(ip_of), separators=(",", ":"))]
  return ["ERR UPLINK unknown-subcommand (SIM, WIFI, STATUS)"]
