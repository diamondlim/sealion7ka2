#!/usr/bin/env python3
"""Inject gpsOneXTRA predicted-orbit assistance into this modem's GNSS engine.

Why: the module reports gpsOneXTRA *enabled* but holding no data at all
(`AT+QGPSXTRADATA?` -> `0,"1980/01/05,19:00:00"`). Predicted orbits are the documented way to acquire
and hold satellites when signals are marginal - which is precisely this KA2's condition (satellites
tracked at 15-25 dB-Hz, never four at once, never a fix). Nothing has ever been loaded here, so this is
the one software lever that was actually untouched.

Sequence, verbatim from the EC2x GNSS Application Note section 3.3:

    engine off -> AT+QGPSXTRA=1 -> AT+QFUPL the .bin -> AT+QGPSXTRATIME -> AT+QGPSXTRADATA
                -> verify via AT+QGPSXTRADATA? -> AT+QGPS=1 -> delete the temporary file

The upload is why this talks to a serial port directly instead of using ModemManager: `AT+QFUPL` expects
the raw file streamed down the same AT channel that carries the commands, and mmcli cannot do that. This
uses the secondary AT port (ttyUSB3), leaving ModemManager on ttyUSB2 undisturbed.

Usage (runs on the box):
    ka2_xtra_inject.py --check                        # conversation test + current state; changes nothing
    ka2_xtra_inject.py --file /tmp/xtra3grcej.bin     # upload, inject, verify, clean up
"""
import argparse
import os
import sys
import termios
import time


class AtPort:
    def __init__(self, path, baud=115200, flow=True):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        iflag = 0
        oflag = 0
        lflag = 0
        cflag = termios.CS8 | termios.CREAD | termios.CLOCAL
        if flow:
            cflag |= termios.CRTSCTS
        speed = getattr(termios, "B%d" % baud)
        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, speed, speed, cc])
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        total = 0
        while total < len(data):
            try:
                total += os.write(self.fd, data[total:])
            except BlockingIOError:
                time.sleep(0.01)

    def read_for(self, seconds):
        deadline = time.time() + seconds
        out = b""
        while time.time() < deadline:
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                chunk = b""
            except OSError:
                break
            if chunk:
                out += chunk
                deadline = time.time() + 0.4        # keep reading while it is still talking
            else:
                time.sleep(0.02)
        return out.decode(errors="replace")

    def command(self, text, wait=2.5, quiet=False):
        """Send one AT command and return its full reply."""
        self.write(text + "\r")
        reply = self.read_for(wait)
        if not quiet:
            print("  %-58s -> %s" % (text, " | ".join(l.strip() for l in reply.splitlines() if l.strip())))
        return reply

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def check(port):
    print("talking to %s:" % port.path)
    saw_ok = False
    reply = port.command("AT", wait=2.0)
    if "OK" in reply:
        saw_ok = True
    else:
        # close and retry with flow control off: some ports dislike RTS/CTS with no cable in play
        port.close()
        port = AtPort(port.path, flow=False)
        reply = port.command("AT", wait=2.0)
        saw_ok = "OK" in reply
    if not saw_ok:
        print("no AT answer on this port - try another (ttyUSB2 is busy with ModemManager)")
        return False, port
    for query in ("AT+QGPS?", "AT+QGPSXTRA?", "AT+QGPSXTRADATA?", 'AT+QGPSCFG="gnssconfig"',
                  'AT+QGPSCFG="dpoenable"'):
        port.command(query)
    return True, port


def upload(port, local_path, remote="UFS:xtra3grcej.bin", timeout_s=300):
    size = os.path.getsize(local_path)
    print("\nuploading %s (%d bytes) to %s" % (local_path, size, remote))
    port.write('AT+QFUPL="%s",%d,%d\r' % (remote, size, timeout_s))
    banner = port.read_for(8.0)
    print("  banner: %s" % (" | ".join(l.strip() for l in banner.splitlines() if l.strip()) or "(silent)"))
    if "ERROR" in banner:
        return False, banner
    data = open(local_path, "rb").read()
    print("  streaming %d bytes..." % len(data))
    port.write(data)
    reply = port.read_for(30.0)
    print("  reply: %s" % " | ".join(l.strip() for l in reply.splitlines() if l.strip()))
    ok = "OK" in reply and "+QFUPL" in reply
    if ok:
        try:
            returned = int(reply.split("+QFUPL:")[1].split(",")[0].strip())
            print("  module accepted %d of %d bytes" % (returned, size))
            ok = returned == size
        except Exception:
            pass
    return ok, reply



def set_clock(port):
    """Give the modem a real clock. Its RTC reads 1980-01-06, and a receiver with no time reference has
    nothing to validate assistance data against - worth fixing before blaming the payload."""
    # AT+CCLK wants LOCAL time plus the zone offset, so gmtime() here declares UTC as
    # GMT+8 and leaves the module 8 h behind.
    stamp = time.strftime("%y/%m/%d,%H:%M:%S", time.localtime())
    port.command('AT+CCLK="%s+32"' % stamp, wait=3.0)   # +32 quarters = GMT+8, the car's local zone
    port.command("AT+CCLK?", wait=3.0)


def inject(port, local_path, remote):
    """One full attempt: upload, inject time, inject data, restart the engine, THEN verify.

    The order matters and bit the first attempt: the module only reports the injected data's validity once
    the engine is running again, so verifying while the engine was still off said "nothing injected" even
    though the commands had all returned OK.
    """
    ok, _ = upload(port, local_path, remote=remote)
    if not ok:
        print("  upload to %s failed" % remote)
        return False, ""
    now = time.strftime("%Y/%m/%d,%H:%M:%S", time.gmtime())
    port.command('AT+QGPSXTRATIME=0,"%s",1,1,3500' % now, wait=4.0)
    port.command('AT+QGPSXTRADATA="%s"' % remote, wait=8.0)
    port.command("AT+QGPS=1", wait=4.0)
    time.sleep(3.0)                                   # let it parse before asking
    validity = port.command("AT+QGPSXTRADATA?", wait=5.0)
    port.command("AT+QGPSEND", wait=3.0)
    port.command('AT+QFDEL="%s"' % remote, wait=4.0)
    held = "1980" not in validity and "+QGPSXTRADATA" in validity
    print("  => %s" % ("VALID" if held else "still empty"))
    return held, validity


def try_variants(port, candidates):
    """Try each file/path combination, because the note warns that not every module accepts every variant."""
    set_clock(port)
    for local_path, remote in candidates:
        if not os.path.exists(local_path):
            print("\n--- skipping %s (not present) ---" % local_path)
            continue
        print("\n--- trying %s -> %s ---" % (os.path.basename(local_path), remote))
        port.command("AT+QGPSEND", wait=3.0)
        port.command("AT+QGPSXTRA=1", wait=3.0)
        held, validity = inject(port, local_path, remote)
        if held:
            print("\nWORKS: %s in %s" % (os.path.basename(local_path), remote))
            port.command("AT+QGPS=1", wait=4.0)
            return True
    port.command("AT+QGPS=1", wait=4.0)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyUSB3")
    parser.add_argument("--file")
    parser.add_argument("--remote", default="UFS:xtra3grcej.bin")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--variants", action="store_true",
                        help="try every documented file/path combination until one is accepted")
    args = parser.parse_args()

    port = AtPort(args.port)
    good, port = check(port)
    if not good:
        return 2
    if args.variants:
        candidates = [("/tmp/xtra2.bin", "RAM:xtra2.bin"),
                      ("/tmp/xtra3grc.bin", "RAM:xtra3grc.bin"),
                      ("/tmp/xtra3grc.bin", "UFS:xtra3grc.bin"),
                      ("/tmp/xtra3grcej.bin", "UFS:xtra3grcej.bin")]
        ok = try_variants(port, candidates)
        port.close()
        print("\nRESULT: %s" % ("orbit data injected and held" if ok else "no variant was accepted"))
        return 0 if ok else 1
    if args.check or not args.file:
        port.close()
        return 0

    if not os.path.exists(args.file):
        print("no such file: %s" % args.file)
        port.close()
        return 2

    print("\n--- engine off (required before touching the assistance function) ---")
    port.command("AT+QGPSEND", wait=3.0)
    time.sleep(1.0)
    port.command("AT+QGPSXTRA=1")

    ok, _ = upload(port, args.file, remote=args.remote)
    if not ok:
        print("\nUFS upload failed; trying RAM instead")
        ok, _ = upload(port, args.file, remote="RAM:%s" % os.path.basename(args.file))
        if not ok:
            port.command("AT+QGPS=1")
            port.close()
            print("\nupload failed on both paths - nothing injected, engine left running")
            return 1
        args.remote = "RAM:%s" % os.path.basename(args.file)

    ok, validity = inject(port, args.file, args.remote)
    port.close()
    print("\nRESULT: %s" % ("orbit data injected and held" if ok
                            else "module still reports no valid data - the injection did not take"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
