# motosome_01 — Roadmap & Notes

Running notes for the motosome motion test bench. Captures what's done, what's next,
and the decisions/context behind them so we can pick up cold.

---

## Where it stands (done)

- Beckhoff **C6920** running Ubuntu 26.04 + XFCE, static IP `192.168.2.2`, passwordless SSH.
- Test bench app (`bench.py`): connect / enable / **jog** / run-at-speed / move-to-position /
  home / **speed-profile player** (trapezoid + sine) with a live velocity scope.
- Clean **drive abstraction** (`drive.py`): `SimDrive` (works now) + `SoemDrive` (SOEM/CiA402
  backbone — full state machine + cyclic loop written; needs a calibration pass on real HW).
- Speed/position-vs-time **profiles** (`profile.py`).
- **House style** (`theme.py` + `logo_light.svg`): dark instrument look, monospace, honeycomb-modular logo.
- Published: github.com/honeycomb-modular/motosome_01

---

## Next up

### 1. Drag-to-draw curve editor  ← the big one
Replace the trapezoid/sine *presets* with a real **drawable** speed-(or position-)vs-time curve.
- Click/drag breakpoints on a canvas; insert/delete points; snap to grid.
- Reuse the existing `Profile` data model (it's already a list of (t, value) points).
- This is effectively the **Xylosome HMI curve editor** — build it here, port/share later.
- Toggle: velocity-curve vs position-curve (`ProfileKind` already supports both).
- Save / load curves to file (JSON). Add a small library of saved profiles.

### 2. SOEM backend — WRITTEN, needs hardware calibration  ← when the A6-EC is on the bench
`SoemDrive` in `drive.py` is the real backbone now: opens the bus, maps a standard CiA402
PDO set, brings the slave to OP, runs a 1 kHz cyclic thread with the full CiA402 state
machine (CSV jog/curve + CSP move/home). `soem_scan.py` is the first-contact diagnostic.
Bring-up when the drive arrives:
- Drive on the Beckhoff's **2nd NIC `enp4s0`** over EtherCAT, motor connected.
- `pip install --user pysoem`; run `sudo python3 soem_scan.py enp4s0` — confirm vendor/product
  and the RxPDO/TxPDO byte sizes.
- If the process image differs from `drive.CiA402Pdo` defaults (11 B / 11 B), adjust the offsets
  there (and `_setup_pdos` if the A6-EC allows PDO remapping; some drives use fixed PDOs).
- Set `counts_per_rev` (`DriveLimits`) to the drive's encoder / electronic-gear value, and
  calibrate the counts↔drive-units factor if `0x60FF`/`0x6064` aren't already in counts.
- Run the bench with raw-socket rights: `sudo python3 bench.py` or `setcap cap_net_raw`.
- Still simplified vs production: homing drives to absolute 0 (real CiA402 homing = mode 6 +
  the drive's homing method/switch); statusword fault text is generic ("drive fault").

### 3. Real-time / low-latency kernel
- Needed for jitter-free cyclic EtherCAT once we're driving real motion (not for sim/jog).
- Ubuntu low-latency kernel, or PREEMPT-RT (free for personal use via Ubuntu Pro).
- Tune: isolate a CPU for the cyclic loop, set thread priority.

### 4. House-style polish
- Lock the **exact** brand hex values + fonts to match `xylosome-hmi` (pull from that repo's
  theme/QML palette if it defines one). Current palette in `theme.py` is a sensible first pass.
- Use the R/G/B/C channel colours functionally where channels appear.
- Consider a subtle hexagon motif echoing the logo.

### 5. Smaller TODOs
- Add a **position trace** to the scope (currently velocity only); time-axis ticks.
- Make `counts_per_rev` and velocity/accel limits editable in the UI (a Settings panel).
- `requirements.txt` (PySide6) + a `run.sh` / `.desktop` launcher for the XFCE desktop.
- Basic unit tests for `SimDrive` + `Profile` (the headless logic checks we already ran).
- E-stop / big STOP-ALL button, always reachable.
- Soft limits (min/max position) + over-speed guard before touching real hardware.

---

## Decisions / context (so we don't relitigate)

- **Not LinuxCNC**: it's a CNC/G-code machine controller — wrong paradigm for one bespoke
  artistic axis, and we already have a GUI (the Xylosome HMI). We want a *motion backend*, not
  a second front-end.
- **SOEM over IgH EtherCAT master**: userspace, no kernel module, simplest path; CiA402 is
  standard so the StepperOnline drive works like any other (Beckhoff/Delta/etc.).
- **Python/PySide6 for the bench**: fast iteration; fine for a test bench. The Xylosome HMI is
  C++/QML — if we later want to literally share components, revisit. Logic ports cleanly.
- **Sim-first architecture**: everything behind the `MotionDrive` interface, so sim ↔ hardware
  is a one-line backend swap. Nothing built now gets thrown away.

---

## Hardware notes

- Beckhoff C6920-1107-0050 · Celeron 2000E 2.2 GHz · 2 GB DDR3L (⚠ low — a DDR3L SO-DIMM
  bump to 4–8 GB would help a lot) · 8 GB CFast (unused) + 320 GB HGST (OS) · 2× GbE + COM1.
- Onboard NICs: `eno1` (MAC …36, management/SSH) and `enp4s0` (MAC …37, reserve for EtherCAT).
- Shut it down **cleanly** (short power-button tap → ACPI) — avoid hard power cuts.
