# EtherCAT Servo Test Bench

A clean motion playground for a CiA402 servo drive: **jog, run-at-speed, move-to-position,
home, and play a speed-vs-time profile**, with a live velocity scope.

It runs **today against a simulated motor** (no hardware). When your StepperOnline
EtherCAT drive is on the bus, you switch the backend to *EtherCAT* and the same
controls drive the real motor — the GUI never changes.

## Files

| File | What it is |
|------|------------|
| `bench.py` | The GUI (PySide6). Run this. |
| `drive.py` | The drive abstraction + `SimDrive` (simulator) + `SoemDrive` (real EtherCAT skeleton). |
| `profile.py` | Speed/position-vs-time profiles (the "draw a curve and play it" part). |
| `xylod_link.py` | Third backend: live link to the Xylosome daemon (xylod :5510). Select "Xylosome xylod (live)" → the scope shows the scan axis in real time while the Pi pendant executes — and the bench controls drive the axis through xylod. |

The whole app only ever talks to the `MotionDrive` interface, so sim ↔ hardware is a
one-line backend swap.

## Run it (on the Beckhoff desktop)

It's a graphical app, so run it from the **XFCE desktop** (the monitor), not plain SSH.

Install the GUI toolkit once:

```bash
sudo apt install -y python3-pip
pip install --user --break-system-packages PySide6
# PySide6 from pip needs a few X11 runtime libs Ubuntu Server omits:
sudo apt install -y libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render-util0 libxcb-shape0
```

Then:

```bash
cd ~/motosome_01      # wherever you put these files
python3 bench.py
```

Click **Connect** (Backend = *Simulated*), **Enable**, then jog / run / move / play a
profile and watch the scope. Everything works against the simulated motor.

> Tip: to drive it over SSH with the window shown on your Mac, you'd use X-forwarding
> (`ssh -X`) + XQuartz on the Mac — but running on the box's own monitor is simplest.

## When the real drive arrives

1. Wire the StepperOnline drive to the Beckhoff's **second NIC (`enp4s0`)** over EtherCAT.
2. Install the master binding: `pip install --user pysoem`.
3. Confirm the bus sees it (SOEM ships a `slaveinfo` example; it should list your drive).
4. In `drive.py`, fill in the `SoemDrive` skeleton: the PDO mapping (controlword,
   statusword, target/actual position+velocity) and cyclic loop come straight from the
   drive's ESI/manual. The CiA402 object indices and state-machine constants are already
   stubbed in.
5. Set `counts_per_rev` (in `bench.py`, `DriveLimits`) to match the drive's encoder /
   electronic-gear setting.
6. Run with raw-socket access: `sudo python3 bench.py` (or grant
   `CAP_NET_RAW`), pick Backend = *EtherCAT*, Connect.

For smooth high-rate motion you'll also want a **low-latency / real-time kernel** — not
needed for jogging and bench work, but worth it before tight profile playback.

## Why this is the right foundation

The profile player here is the same idea as the Xylosome HMI curve editor: draw a
velocity/position-vs-time shape, press play, the axis follows it. Prove it on the bench
with the simulator now, swap in the real drive next, and this becomes the core of the
Xylosome motion backend — nothing thrown away.

## The dip between passes (do not "fix")

After each scan pass the scope shows a negative-velocity dip the Pi never
draws: the carriage return, the axis driving back to arc start. The pendant
deliberately hides it (artist's view = intent); the bench deliberately shows
it (desk view = physics). Its depth is the return speed, its width is the
inter-pass dead time — and on real hardware a widening dip means mechanical
drag before you can feel it. Verified and explicitly kept, 2026-06-10.
