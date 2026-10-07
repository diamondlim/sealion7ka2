# Why openpilot longitudinal is off on this car, and what would change it

Findings from 6-7 Oct 2026, read out of the port's own source and measured live on the box. Written down
because the same three gates get rediscovered every time "can we use openpilot's ACC instead of the car's?"
comes up, and because two of the obvious explanations are wrong.

## Where the capability is declared

`opendbc_repo/opendbc/car/byd/interface.py::_get_params`. The shared path sets
`ret.openpilotLongitudinalControl = True` (the BYD default). Three branches force it back off:

- `PLATFORM_MPC_LKA` — `False`, `pcmCruise = True`, `radarUnavailable = True`, `safetyParam = 4`.
- `(BYD_ATTO3, BYD_M6, BYD_SEAL6)` — keeps `True`, `safetyParam` 1/3, and is listed in `BYD_OP_LONG_PLATFORMS`.
- `(BYD_SEAL, BYD_SEALION7, BYD_SHARK)` — **this car**: `safetyParam = 2`,
  `openpilotLongitudinalControl = False`, `radarUnavailable = True`.

`radarUnavailable = True` is **not** the blocker. Vision longitudinal is designed to run without radar; that
flag is what a vision-longitudinal car wants. Citing it as the obstacle is a wrong refusal.

## Gate 2: the platform tuple decides whether anything is transmitted

`cam_lka/carcontroller.py` (`if carFingerprint in BYD_OP_LONG_PLATFORMS and openpilotLongitudinalControl`).
`BYD_OP_LONG_PLATFORMS` holds only ATTO3, M6, SEAL6. With the flag forced `True` on this car, openpilot
computed a full longitudinal plan and transmitted **none** of it: `carControl.longActive = True`,
`actuators.accel = -1.65`, while the box's only output was `0x1E2`, `0x316` and `0x1FC` — every one lateral.
Reading the flag alone would say "openpilot is in longitudinal control".

## What happens when both gates are opened (measured)

Adding `BYD_SEALION7` to `BYD_OP_LONG_PLATFORMS` and forcing the flag: the box begins writing `0x32E` at
50 Hz — verified by subscribing a second process to `sendcan` (msgq is one publisher, many subscribers, so
this is read-only and safe). But card then logs, once a second:

```
CANParser: 0x32e ACC_CMD not valid (timeout or missing)
CANParser: 0x32d ACC_HUD_ADAS not valid (timeout or missing)
```

and openpilot raises `canError` (`Unknown Vehicle Variant`) and disables itself. Live at the same time:
`safetyModel=byd`, `safetyParam=2`, `controlsAllowed=True`, `faults=[]`, 16k CAN frames in 3 s — the panda
was healthy and the car was awake. Only the parser was unhappy.

## The real blocker is the counter, not the shared bus

Two transmitters on one id is **not** automatically fatal — `acc_button.py:31` says so outright:
`BUS = 0  # Sealion 7 takes the press on bus 0, the same bus the car's own module uses`, and that path works
every day. The difference is in the DBC:

```
BO_ 814 ACC_CMD:      SG_ COUNTER : 51|4@0+   (byte 6, high nibble)
                      SG_ CHECKSUM : 63|8@0+  (byte 7)
BO_ 944 PCM_BUTTONS:  button bits only — NO counter, NO checksum
```

card's `CANParser` validates `ACC_CMD` against that counter. With the car's module and the box both writing
`0x32E` on bus 0, each with its own independent counter, the parser sees it step non-monotonically and marks
the message invalid — which is what produces the `canError`. The button frame survives the identical bus
sharing because its DBC carries no counter to violate.

So the fix space is "make the box's `ACC_CMD` counter coherent with the car's" — the same class of work
`acc_button.py` documents for `0x3B0` ("both fields are mandatory, not decoration"). It is unfinished
engineering, not an impossibility.

## A second, independent break from the same edit

`cam_lka/carstate.py` uses the *same* flag for an unrelated job:

```
def _select_long_parser(self, cp, cp_cam):
    if self.CP.carFingerprint in BYD_OP_LONG_PLATFORMS:
        self.op_long = True
        return cp_cam          # ACC_CMD / ACC_HUD_ADAS read from the CAM bus (2)
    self.op_long = False
    return cp               # read from the MAIN bus (0)
```

`get_can_parser`/`get_cam_can_parser` mirror it. On this car the ACC frames are on bus 0, so adding the
platform name also moves the parsers to cam bus 2, where those frames do not exist — producing the same
`not valid` errors for a completely different reason. A trial that only adds the platform name therefore
breaks in two independent ways; the result must not be attributed to the bus collision alone.

`mpc_lka/carstate.py` already solves this properly with a separate flag, `BydFlags.ACC_ON_ESC` ("Flipped
harness: ACC_HUD_ADAS + ACC_CMD on ESC bus 0"), consumed in exactly three places, all in `mpc_lka`.
`cam_lka` never got the equivalent. This car shows exactly that flipped-harness layout (`0x32D` and `0x32E`
both on bus 0) with no flag set — this is the missing piece.

## The panda safety is readable, and says what each param is for

`opendbc_repo/opendbc/safety/modes/byd.h` ships in the tree. `#define BYD_ACC_CMD 0x32EU // 814`.
`byd_init(param)` maps `safetyParam` onto five flags:

- **1** ATTO3 — torque spoof; default TX list, which includes `0x32E` on bus 0.
- **2** Seal / **Sealion 7** / Shark — alt engage + torque spoof + relax controls; rx checks keyed on `813`
  (`0x32D`) on bus 0 instead of `814` on bus 2.
- **3** M6 / Seal 6 — alt engage + torque spoof.
- **4** Song Plus — MPC-LKA engage; TX list is only `0x316`, `0x318`.
- **5** Seal 6 *stock* long — `byd_stock_long = true`, `0x32E` **omitted from the TX list** ("so OP cannot
  conflict").

`byd_fwd_hook()` blocks the camera's `ACC_CMD` on **bus 2** when `!byd_stock_long`, and blocks
`STEERING_TORQUE` on bus 0 under the spoof flag. There is **no `ACC_CMD` block on bus 0** — where a
flipped-harness car's own ACC module transmits. Params 1/3 are for cars with the seat empty; param 5 is the
deliberate "openpilot must not touch it" case; nobody wrote the middle case.

## The radar exists and its DBC does not match this car

`radar_interface.py` is ~474 lines of real work (track history matching, plausibility gates, `MISS_MAX`
keep-alive so `radard`'s filter is not reset, comments citing real highway logs). `interface.py` wires
`RadarInterface` for every BYD platform; `CamLkaPlatformConfig` defaults to
`dbc_dict("byd_general_pt", "byd_radar_fd")`; `CANBUS.radar_bus = 1`.

It expects `RADAR_TRACK_00..09` at `0x280` (or `0x670`, 16 msgs, for the Seal 6) at 20 Hz on bus 1. Measured
here: bus 1 is alive (3,966 frames in 6 s: `0x04B` 49 Hz, `0x050` 44 Hz, `0x095/0x096/0x098` ~49 Hz,
`0x120-0x123` 15 Hz) but **nothing in `0x280-0x28F` or `0x670-0x67F`**. Each platform needed its own radar
DBC (`byd_radar_fd` vs `byd_radar_seal6_fd`); this car's is unwritten. So `radarUnavailable = True` is not
the only thing standing between this car and radar.

## What would actually have to be built

1. `create_accel_command` given the same counter/checksum treatment `acc_button.py` gives `0x3B0`.
2. The parser bus decoupled from `BYD_OP_LONG_PLATFORMS` in `cam_lka` — a `BydFlags.ACC_ON_ESC` equivalent.
3. A radar DBC for this platform, derived from logs, before the radar interface means anything.

Until then the stock-ACC button path is the correct architecture for this car, not a workaround: openpilot is
lateral-only, the car's own ACC does the following, and the setpoint is the only lever the box has.

## Reverting a forced trial

`interface.py` back to `openpilotLongitudinalControl = False` on the SEAL/SEALION7/SHARK branch, drop
`BYD_SEALION7` from `BYD_OP_LONG_PLATFORMS`, `Params().put_bool("ExperimentalLongitudinalEnabled", False)`,
then `systemctl restart kommu.service` (the change is inert until a card start; a mid-on restart can wedge
the panda, which only an ignition cycle clears). Verify with a `sendcan` census: `0x32E` must be absent.
