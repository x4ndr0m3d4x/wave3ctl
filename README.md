# wave3ctl

A Linux kernel module that acts as a USB Audio Class control proxy for the Elgato Wave:3 microphone. It sends control requests via `usb_control_msg()`, bypassing the `usbdevfs` interface-claim check that blocks userspace access. The `snd-usb-audio` driver stays loaded and audio is unaffected.

## How it works

The module creates a `/dev/wave3ctl` misc device that accepts ioctls containing raw USB control transfers, forwarding them directly to the Wave:3 hardware. This avoids the need to detach the device from the kernel driver (which in my experience breaks settings on Discord and such).

---

## Prerequisites

- Linux kernel headers for your running kernel
- DKMS (for automatic rebuilds on kernel updates)
- GCC and make

On Arch Linux:
```bash
sudo pacman -S linux-headers dkms base-devel
```

---

## Building and installing

### One-time setup with DKMS

DKMS will automatically rebuild the module whenever your kernel updates, so you only need to do this once.

**1. Copy the source into the DKMS source directory:**

```bash
sudo mkdir -p /usr/src/wave3ctl-1.0
sudo cp wave3ctl_kmod.c Makefile dkms.conf /usr/src/wave3ctl-1.0/
```

**2. Register, build, and install with DKMS:**

```bash
sudo dkms add -m wave3ctl -v 1.0
sudo dkms build -m wave3ctl -v 1.0
sudo dkms install -m wave3ctl -v 1.0
```

**3. Load the module now (without rebooting):**

```bash
sudo modprobe wave3ctl_kmod
```

**4. Enable auto-load on boot:**

```bash
echo "wave3ctl_kmod" | sudo tee /etc/modules-load.d/wave3ctl.conf
```

The module will now load on every boot and rebuild automatically after kernel updates.

---

## Verifying it works

Check the module is loaded:
```bash
lsmod | grep wave3
```

Check the device node exists:
```bash
ls -la /dev/wave3ctl
```

---

## Manual build (without DKMS)

If you just want to build and load the module without setting up DKMS:

```bash
make -C /lib/modules/$(uname -r)/build M=$PWD modules
sudo insmod wave3ctl_kmod.ko
```

To unload:
```bash
sudo rmmod wave3ctl_kmod
```

---

### `fatal error: generated/autoconf.h: No such file or directory`

Your kernel headers are missing or not installed. Install them:

```bash
# Arch Linux
sudo pacman -S linux-headers
```

---

## Usage (wave3ctl.py)

A command-line tool for controlling the Elgato Wave:3 microphone on Linux. It talks to the kernel module via `/dev/wave3ctl` to read and set microphone and headphone parameters over USB.

> **Requires the kernel module to be loaded first.** See [README.md](README.md) for setup.

```
wave3ctl <command> [argument]
```

---

### Commands

#### `status`
Shows the current state of the device: mic mute, mic gain, headphone mute, and headphone volume.

```
$ wave3ctl status
Elgato Wave:3 Status
========================================
  Mic:       🎤 LIVE
  Mic Gain:  72% (+18.3 dB)
  Headphone: 🔊 ON
  HP Volume: 55% (-12.0 dB)
```

---

#### `mute`
Toggles microphone mute when called with no argument, or sets it explicitly.

```bash
wave3ctl mute          # toggle
wave3ctl mute on       # mute
wave3ctl mute off      # unmute
```

Accepted values for on/off: `on`, `off`, `true`, `false`, `1`, `0`.

---

#### `volume`
Shows or sets the headphone volume as a percentage (0–100).

```bash
wave3ctl volume        # show current volume
wave3ctl volume 75     # set to 75%
```

---

#### `gain`
Shows or sets the microphone gain as a percentage (0–100).

```bash
wave3ctl gain          # show current gain
wave3ctl gain 60       # set to 60%
```

---

#### `monitor`
Watches for hardware changes in real time — knob turns, button presses, mute toggles. Polls at 200ms intervals. Press Ctrl-C to stop.

```
$ wave3ctl monitor
Monitoring Wave:3 — Ctrl-C to stop

Current state:
  Mic:    🎤 LIVE
  Gain:   72% (+18.3 dB)
  HP:     🔊 ON
  Volume: 55% (-12.0 dB)

  🔊 Vol  → 60% (-10.5 dB)
  🎤 Mic  → MUTED 🔇
```

---

#### `discover`
Probes the USB Audio Class Feature Units and prints raw capabilities: mute state, current volume/gain in dB, and the full range and step size. Useful for debugging or understanding what the hardware exposes.

```
$ wave3ctl discover
Elgato Wave:3 — USB Audio Class Feature Units

  Headphone (Entity 5):
    Mute:   OFF 🔊
    Volume: 55% (-12.0 dB)
    Range:  -73.0 … 0.0 dB  (step 0.25 dB)

  Microphone (Entity 6):
    Mute:   OFF 🔊
    Volume: 72% (+18.3 dB)
    Range:  0.0 … 24.0 dB  (step 0.25 dB)
```
