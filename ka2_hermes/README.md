# KA2 box-side tooling

Custom code that runs on the KommuAssist KA2 fitted to a BYD Sealion 7 (bukapilot fork). None of it is part
of openpilot, and the fork's updater never installs it: it lives on the box under `/data/hermes` and is
started by systemd units. This directory is the versioned copy, so the box can be rebuilt from this repo
plus the tracked fork branch.

## Active

- **`ka2_vision_acc.py`** + **`ka2_acc_press.py`** — the vision -> stock-ACC bridge
  (`ka2-vision-acc.service`). It watches `modelV2` and lowers the ACC *setpoint* with button presses when a
  bend ahead or a car ahead demands less speed, then hands the speed back. It never actuates the brakes: on
  this platform the setpoint is the only lever the box has, and the car's own ACC does the decelerating.
  Every behaviour is a live key in `/data/hermes/tuning.json`; the press tool owns its own gates and audit.
- **`bt_settings_service.py`** + **`wifi_control.py`** — the phone app's settings service
  (`ka2bt.service`), including every live tuning row the app shows. It reads each knob's range and shipped
  default out of the source files themselves (`ast`, never `exec`), so a row appears only for a knob the
  deployed code actually reads, and a half-wired knob silently disappears instead of lying.
- **`hermes_ka2_patch.sh`** — re-applied at every boot (`hermes-ka2-patch.service`, the timer, and a line in
  `/data/continue.sh`) so the openpilot tree stays patched even after the updater hard-resets it. The
  patches themselves are committed on the tracked branch now, so this is the fallback for a branch change
  rather than the only copy.
- **`deploy/`** — the units and the `/data/continue.sh` hook, kept for rebuilding the box.

## Diagnostics (run by hand, read-only)

`drivewatch.py`, `engagewatch.py`, `acc_decode.py`, `log_summary.py`, `route_gps.py`, `centering_study.py`,
`ka2_acc_watch_press.py`, `probe_*.py` — recorders and probes used to establish what the car, the CAN bus
and the box were doing during a drive.

## Historical

`ka2_gnss_*.py|sh`, `ka2_pose_pub.py`, `ka2_xtra_refresh.sh` — an earlier attempt to feed this box its own
GNSS and pose; superseded by the fork-side fixes. `bt_settings_service.v1.py` and
`bt_settings_service.pre-acc.py` — earlier revisions, kept for comparison.

## Tuning keys

- Bend auto-slow: `VIS_TURN_ACC_{ENABLED, MIN_SETPOINT_KMH, MAX_RESTORE_KMH, MAX_STEPS, COOLDOWN_S,
  TRIGGER_S, LOOKAHEAD_MAX, A_LAT, MIN_RADIUS, MIN_V_KMH, MARGIN_KMH, RESTORE_MARGIN_KMH, RESTORE}`.
- Car ahead: `VIS_LEAD_ACC_{ENABLED, LOOKAHEAD_M, MARGIN_KMH, MIN_PROB, MAX_STEPS}` — ships **off**.

Every key is re-read about once a second; a missing file or an unreadable value falls back to the last good
value, and every shipped default is the least aggressive setting in its range. The auto-slow floor is the
hard bound on how far the setpoint can be walked down.

## Notes

- **`op-long-findings.md`** — why openpilot longitudinal is off on this car, the three gates, the counter
  that actually blocks the accel command, the parser-bus coupling, and what would have to be built. Read this
  before re-litigating "can we use openpilot ACC".

## Rebuilding the box

1. Clone the tracked fork branch — the openpilot-side fixes (rate/alive gating, the radar assumption) are
   already committed there.
2. Copy the `*.py` files to `/data/hermes`, `chown kommu:kommu`, mode 644.
3. Copy `hermes_ka2_patch.sh` to `/usr/kommu/`, mode 755.
4. Install `deploy/*.service` and the timer, `systemctl daemon-reload`, then enable the timer and both
   services.
5. Add the hook line to `/data/continue.sh` immediately before `./launch_openpilot.sh`.

## 2026-10-07 - what changed on the car

### The app's ACC+ was dropping the ACC

Reported exactly as it behaves: press "+" in the app and the car disengages. The full-rate rlog of that drive
shows the box's own `0x3B0` frames (`byte0=0x1c`, set+res) landing **0.1-3.6 s before** each drop, at `v=0`,
`standstill=True`, `lead=none`, with the car on its own brake hold. On this car "+" at a standstill is
**resume**, not a set-speed step; with nothing ahead to resume into, its ACC aborts -
`CRUISE_STATE Failure(8) -> StandBy`. The qlog reported no button pressed; that was the qlog's decimation,
not evidence.

The gate that let it through checked only `cruiseState.enabled` - and the BYD port reports that true for
`CRUISE_STATE in (3,5,6,7)`, where **6 and 7 are the standstill states**. `ka2_acc_press.py` now refuses
"step" and bare "res" at a standstill or below `ACC_PLUS_MIN_KPH` (read from `tuning.json`, default 20 km/h;
set 1.0 for a true standstill only). The "set" (decrease) side is deliberately untouched. Takes effect on the
next press - no ignition cycle, unlike card-side changes.

### SNG ported - queue resume

`sng_helper.py` (51 lines) plus its four integration points, ported from `kommuai/opendbc@8519177e`. It
presses resume while the car's own ACC is in `cruise_standstill` with a latched lead that has started moving,
and the driver is not on gas/brake/RES. It is a **resume press, not a takeover**: it cannot rescue an already
dropped ACC and does not touch `Failure(8)`. Apply with `ka2_sng_port.py` (idempotent, `--check` first).
Loads on the next ignition cycle.

### The counter the box had lost

`card.py` tracks the car's own `0x3B0` counter and continues it when injecting a press. That block was
**missing on the box** (375 lines live against 391 in the mirror), so every press had been sending counter 1.
Restored - and the first restore was itself wrong: `can_capnp_to_list` returns
`(logMonoTime, [(addr, dat, src), ...])` batches of **plain tuples**, not `CanData` objects, so a block keyed
on `.address`/`.dat` matched nothing and its `try/except` hid that. Verified against the live bus before
trusting it (60 src-0 idle frames in 3 s, counter rolling 6,7,8..15,0,1) and unit-tested for the batch, flat,
mirror, pressed and junk-entry shapes.

### vision-acc rows restored to the app

The 19 hidden rows are back with `VIS_TURN_ACC_ENABLED=1` and the service enabled, as **one step**:
`ka2_restore_vis_all.sh --apply` (rows + flag + service together - rows alone are knobs that do nothing).
Rollback is printed by the tool. The settings service caches its schema at import, so it must be restarted
before the app sees them; verified over its own protocol (`SCHEMA` on 127.0.0.1:9911 -> 53 rows, 19 of them
vision-acc).

### Mirror list

`ka2_acc_press.py` was missing from it, so the repo held the fork's stale in-tree copy (pre-gate) while the
box ran the gated one. Added, together with `ka2_acc_watch_press.py`, `ka2_restore_vis_all.sh`,
`tuning.json`, and `ka2_sync_to_github.sh` itself - the last so the repo records the rule that decides what
gets mirrored.
