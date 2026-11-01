#!/usr/bin/env python3
"""Push the car's drive logs straight to the NAS over SMB.

Why this exists: the box has no SMB client and cannot install one (its root filesystem is mounted
read-only apart from /var/tmp), so the upload speaks SMB from Python instead. pysmb is pure Python and
sits in /data/hermes/pylibs, which is on the persistent partition - the same trick the rest of
/data/hermes uses to survive the vendor's boot-time resets.

The box is now the writer: segments go to //NAS/home/HA/ka2-logs/<route>/{rlog,qlog}.zst and what has
already been sent is remembered in a state file, so a restart costs a directory listing, not a re-upload.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, "/data/hermes/pylibs")
from smb.SMBConnection import SMBConnection          # noqa: E402

NETRC = "/data/.ka2netrc"
REALDATA = "/data/media/0/realdata"
SHARE = "home"
REMOTE_ROOT = "HA/ka2-logs"
STATE = "/data/hermes/state/ka2_nas_push.json"
TIERS = ("rlog.zst", "qlog.zst")


def creds():
    """host, user, password from the netrc the box already carries (values never printed)."""
    host = user = pw = None
    with open(NETRC) as fh:
        for line in fh:
            field = line.split()
            if len(field) == 2:
                if field[0] == "machine":
                    host = field[1]
                elif field[0] == "login":
                    user = field[1]
                elif field[0] == "password":
                    pw = field[1]
    missing = [n for n, v in (("machine", host), ("login", user), ("password", pw)) if not v]
    if missing:
        raise SystemExit("netrc is missing: " + ", ".join(missing))
    return host, user, pw


def load_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE)


def remote_sizes(conn, route):
    """{filename: size} for a route's directory on the NAS, empty when it is not there yet."""
    try:
        entries = conn.listPath(SHARE, "%s/%s" % (REMOTE_ROOT, route))
    except Exception:
        return {}
    out = {}
    for e in entries:
        if not e.isDirectory:
            out[e.filename] = e.file_size
    return out


def ensure_dir(conn, path):
    parts = path.split("/")
    for i in range(1, len(parts) + 1):
        piece = "/".join(parts[:i])
        try:
            conn.createDirectory(SHARE, piece)
        except Exception:
            pass          # already there is the normal case


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only consider this many newest routes")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--timeout", type=int, default=240, help="stop after this many seconds")
    a = ap.parse_args()

    started = time.time()
    host, user, pw = creds()
    routes = sorted((d for d in os.listdir(REALDATA) if d.startswith("2026-")), reverse=True)
    if a.limit:
        routes = routes[:a.limit]

    conn = SMBConnection(user, pw, "ka2-car", host, use_ntlm_v2=True, is_direct_tcp=True)
    if not conn.connect(host, 445, timeout=20):
        raise SystemExit("cannot reach the NAS at %s:445" % host)

    state = load_state()
    sent = skipped = failed = 0
    bytes_sent = 0
    for route in routes:
        if time.time() - started > a.timeout:
            break
        local = os.path.join(REALDATA, route)
        have = remote_sizes(conn, route)
        for tier in TIERS:
            path = os.path.join(local, tier)
            if not os.path.exists(path):
                continue
            size = os.path.getsize(path)
            if have.get(tier) == size:
                skipped += 1
                continue
            try:
                if not have:
                    ensure_dir(conn, "%s/%s" % (REMOTE_ROOT, route))
                with open(path, "rb") as fh:
                    conn.storeFile(SHARE, "%s/%s/%s" % (REMOTE_ROOT, route, tier), fh)
                sent += 1
                bytes_sent += size
                state[route + "/" + tier] = size
            except Exception as exc:
                failed += 1
                if not a.quiet:
                    print("  FAILED %s/%s: %s: %s" % (route, tier, type(exc).__name__, exc))
    conn.close()
    save_state(state)
    print("nas push: %d uploaded (%.1f MB), %d already there, %d failed in %.0fs"
          % (sent, bytes_sent / 1048576.0, skipped, failed, time.time() - started))


if __name__ == "__main__":
    main()
