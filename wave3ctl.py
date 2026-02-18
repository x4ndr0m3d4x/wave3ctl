#!/usr/bin/env python3
"""
wave3ctl — Elgato Wave:3 control for Linux

Controls the Wave:3 via a kernel module that proxies USB Audio Class
control transfers.  snd-usb-audio stays loaded, audio stays working.

Usage:
    wave3ctl status              Show current device state
    wave3ctl mute                Toggle microphone mute
    wave3ctl mute on|off         Set microphone mute
    wave3ctl volume              Show headphone volume
    wave3ctl volume <0-100>      Set headphone volume
    wave3ctl gain                Show microphone gain
    wave3ctl gain <0-100>        Set microphone gain
    wave3ctl monitor             Watch for knob / button changes
    wave3ctl discover            Probe Feature Unit controls
"""

from __future__ import annotations

import errno
import fcntl
import os
import struct
import sys
import time

# ── Constants ────────────────────────────────────────────────────────────

# Must match the kernel module's struct + ioctl number
_XFER_FMT = "<BBHHH64s"  # 72 bytes packed
_XFER_SIZE = struct.calcsize(_XFER_FMT)  # 72
_WAVE3_CTL = (3 << 30) | (_XFER_SIZE << 16) | (ord("W") << 8) | 0

# USB Audio Class 1.0
_GET_CUR, _SET_CUR = 0x81, 0x01
_GET_MIN, _GET_MAX, _GET_RES = 0x82, 0x83, 0x84
_BM_IN = 0xA1  # IN  | Class | Interface
_BM_OUT = 0x21  # OUT | Class | Interface

# Control selectors
_MUTE = 0x01
_VOLUME = 0x02

# Feature Unit entity IDs (from Wireshark captures)
_HP_FU = 5  # headphone output
_MIC_FU = 6  # microphone input
_AC_IF = 0  # AudioControl interface


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ── /dev/wave3ctl low-level interface ────────────────────────────────────


class _DevProxy:
    """Talks to the kernel module via /dev/wave3ctl."""

    def __init__(self) -> None:
        try:
            self._fd = os.open("/dev/wave3ctl", os.O_RDWR)
        except FileNotFoundError:
            _die("/dev/wave3ctl not found — load the kernel module:")
        except PermissionError:
            _die(
                "/dev/wave3ctl: permission denied.\n"
                "    sudo chmod 666 /dev/wave3ctl   (or reload the module)"
            )

    def close(self) -> None:
        os.close(self._fd)

    def ctrl_transfer(
        self,
        request_type: int,
        request: int,
        value: int,
        index: int,
        data_or_len: int | bytes,
    ) -> bytes:
        if isinstance(data_or_len, int):
            length = data_or_len
            data_pad = b"\x00" * 64
        else:
            length = len(data_or_len)
            data_pad = bytes(data_or_len) + b"\x00" * (64 - len(data_or_len))

        buf = bytearray(
            struct.pack(
                _XFER_FMT, request_type, request, value, index, length, data_pad
            )
        )

        try:
            fcntl.ioctl(self._fd, _WAVE3_CTL, buf)
        except OSError as e:
            if e.errno == errno.ENODEV:
                _die("Wave:3 not found — is it connected?")
            raise

        resp_len = struct.unpack_from("<H", buf, 6)[0]
        return bytes(buf[8 : 8 + resp_len])


# ── Wave:3 controller ───────────────────────────────────────────────────


class Wave3:
    def __init__(self) -> None:
        self._dev = _DevProxy()

        # Verify communication
        try:
            self._get_cur(_MIC_FU, _MUTE, 1)
        except OSError as e:
            _die(f"Cannot communicate with Wave:3: {e}")

        # Cache volume ranges (they never change)
        self._hp_range = self._get_range(_HP_FU)
        self._mic_range = self._get_range(_MIC_FU)

    # ── USB transfers ──

    def _get_cur(
        self, entity: int, selector: int, length: int, channel: int = 0
    ) -> bytes:
        return self._dev.ctrl_transfer(
            _BM_IN,
            _GET_CUR,
            (selector << 8) | channel,
            (entity << 8) | _AC_IF,
            length,
        )

    def _set_cur(
        self, entity: int, selector: int, data: bytes, channel: int = 0
    ) -> None:
        self._dev.ctrl_transfer(
            _BM_OUT,
            _SET_CUR,
            (selector << 8) | channel,
            (entity << 8) | _AC_IF,
            data,
        )

    def _get_range(self, entity: int, channel: int = 0) -> tuple[int, int, int]:
        wV = (_VOLUME << 8) | channel
        wI = (entity << 8) | _AC_IF
        try:
            lo = struct.unpack(
                "<h", self._dev.ctrl_transfer(_BM_IN, _GET_MIN, wV, wI, 2)
            )[0]
            hi = struct.unpack(
                "<h", self._dev.ctrl_transfer(_BM_IN, _GET_MAX, wV, wI, 2)
            )[0]
            res = struct.unpack(
                "<h", self._dev.ctrl_transfer(_BM_IN, _GET_RES, wV, wI, 2)
            )[0]
            return lo, hi, max(res, 1)
        except OSError:
            return 0, 0, 1

    # ── helpers ──

    def _raw_pct(self, rng: tuple, raw: int) -> int:
        lo, hi, _ = rng
        return max(0, min(100, round((raw - lo) / max(hi - lo, 1) * 100)))

    def _pct_raw(self, rng: tuple, pct: int) -> int:
        lo, hi, _ = rng
        return int(lo + (hi - lo) * max(0, min(100, pct)) / 100)

    @staticmethod
    def _db(raw: int) -> float:
        return raw / 256.0

    # ── Mic Mute (Entity 6) ──

    def get_mic_mute(self) -> bool | None:
        try:
            return bool(self._get_cur(_MIC_FU, _MUTE, 1)[0])
        except OSError:
            return None

    def set_mic_mute(self, muted: bool) -> bool:
        try:
            self._set_cur(_MIC_FU, _MUTE, bytes([int(muted)]))
            return True
        except OSError:
            return False

    def toggle_mic_mute(self) -> bool | None:
        cur = self.get_mic_mute()
        if cur is None:
            return None
        self.set_mic_mute(not cur)
        return not cur

    # ── HP Mute (Entity 5) ──

    def get_hp_mute(self) -> bool | None:
        try:
            return bool(self._get_cur(_HP_FU, _MUTE, 1)[0])
        except OSError:
            return None

    def set_hp_mute(self, muted: bool) -> bool:
        try:
            self._set_cur(_HP_FU, _MUTE, bytes([int(muted)]))
            return True
        except OSError:
            return False

    # ── HP Volume (Entity 5) ──

    def get_volume(self) -> dict | None:
        try:
            raw = struct.unpack("<h", self._get_cur(_HP_FU, _VOLUME, 2))[0]
            return {
                "raw": raw,
                "pct": self._raw_pct(self._hp_range, raw),
                "db": self._db(raw),
            }
        except OSError:
            return None

    def set_volume_pct(self, pct: int) -> bool:
        raw = self._pct_raw(self._hp_range, pct)
        try:
            self._set_cur(_HP_FU, _VOLUME, struct.pack("<h", raw))
            return True
        except OSError:
            return False

    # ── Mic Gain (Entity 6) ──

    def get_mic_gain(self) -> dict | None:
        try:
            raw = struct.unpack("<h", self._get_cur(_MIC_FU, _VOLUME, 2))[0]
            return {
                "raw": raw,
                "pct": self._raw_pct(self._mic_range, raw),
                "db": self._db(raw),
            }
        except OSError:
            return None

    def set_mic_gain_pct(self, pct: int) -> bool:
        raw = self._pct_raw(self._mic_range, pct)
        try:
            self._set_cur(_MIC_FU, _VOLUME, struct.pack("<h", raw))
            return True
        except OSError:
            return False


# ── CLI commands ─────────────────────────────────────────────────────────


def cmd_status(w: Wave3) -> None:
    print("Elgato Wave:3 Status")
    print("=" * 40)
    mm = w.get_mic_mute()
    if mm is not None:
        print(f"  Mic:       {'🔇 MUTED' if mm else '🎤 LIVE'}")
    g = w.get_mic_gain()
    if g:
        print(f"  Mic Gain:  {g['pct']}% ({g['db']:+.1f} dB)")
    hm = w.get_hp_mute()
    if hm is not None:
        print(f"  Headphone: {'🔇 MUTED' if hm else '🔊 ON'}")
    v = w.get_volume()
    if v:
        print(f"  HP Volume: {v['pct']}% ({v['db']:+.1f} dB)")


def cmd_discover(w: Wave3) -> None:
    print("Elgato Wave:3 — USB Audio Class Feature Units\n")
    for fu, label, rng in [
        (_HP_FU, "Headphone (Entity 5)", w._hp_range),
        (_MIC_FU, "Microphone (Entity 6)", w._mic_range),
    ]:
        print(f"  {label}:")
        try:
            d = w._get_cur(fu, _MUTE, 1)
            print(f"    Mute:   {'ON 🔇' if d[0] else 'OFF 🔊'}")
        except OSError:
            print("    Mute:   (unavailable)")
        try:
            raw = struct.unpack("<h", w._get_cur(fu, _VOLUME, 2))[0]
            lo, hi, res = rng
            pct = w._raw_pct(rng, raw)
            print(f"    Volume: {pct}% ({raw / 256:+.1f} dB)")
            print(
                f"    Range:  {lo / 256:.1f} … {hi / 256:.1f} dB"
                f"  (step {res / 256:.2f} dB)"
            )
        except OSError:
            print("    Volume: (unavailable)")
        print()


def cmd_monitor(w: Wave3) -> None:
    print("Monitoring Wave:3 — Ctrl-C to stop\n")

    last_mm = w.get_mic_mute()
    last_hm = w.get_hp_mute()
    last_vol = w.get_volume()
    last_gain = w.get_mic_gain()

    print("Current state:")
    if last_mm is not None:
        print(f"  Mic:    {'🔇 MUTED' if last_mm else '🎤 LIVE'}")
    if last_gain:
        print(f"  Gain:   {last_gain['pct']}% ({last_gain['db']:+.1f} dB)")
    if last_hm is not None:
        print(f"  HP:     {'🔇 MUTED' if last_hm else '🔊 ON'}")
    if last_vol:
        print(f"  Volume: {last_vol['pct']}% ({last_vol['db']:+.1f} dB)")
    print()

    while True:
        time.sleep(0.2)
        try:
            mm = w.get_mic_mute()
            if mm is not None and mm != last_mm:
                print(f"  🎤 Mic  → {'MUTED 🔇' if mm else 'LIVE 🎤'}")
                last_mm = mm

            hm = w.get_hp_mute()
            if hm is not None and hm != last_hm:
                print(f"  🎧 HP   → {'MUTED 🔇' if hm else 'ON 🔊'}")
                last_hm = hm

            vol = w.get_volume()
            if vol and last_vol and vol["raw"] != last_vol["raw"]:
                print(f"  🔊 Vol  → {vol['pct']}% ({vol['db']:+.1f} dB)")
                last_vol = vol

            gain = w.get_mic_gain()
            if gain and last_gain and gain["raw"] != last_gain["raw"]:
                print(f"  🎤 Gain → {gain['pct']}% ({gain['db']:+.1f} dB)")
                last_gain = gain
        except OSError:
            pass  # transient USB error — retry next cycle


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        sys.exit(0 if len(sys.argv) > 1 else 1)

    cmd = sys.argv[1].lower()
    w = Wave3()

    if cmd == "status":
        cmd_status(w)

    elif cmd == "discover":
        cmd_discover(w)

    elif cmd == "mute":
        if len(sys.argv) > 2:
            arg = sys.argv[2].lower()
            if arg in ("on", "true", "1"):
                w.set_mic_mute(True)
                print("🔇 Mic muted")
            elif arg in ("off", "false", "0"):
                w.set_mic_mute(False)
                print("🎤 Mic unmuted")
            else:
                _die(f"Bad argument '{arg}' — use on / off")
        else:
            r = w.toggle_mic_mute()
            if r is not None:
                print("🔇 Mic muted" if r else "🎤 Mic unmuted")
            else:
                _die("Cannot read mic mute state")

    elif cmd == "volume":
        if len(sys.argv) > 2:
            try:
                pct = int(sys.argv[2])
            except:
                _die(f"Not a number: {sys.argv[2]}")
            pct = max(0, min(100, pct))
            w.set_volume_pct(pct)
            print(f"🔊 Volume → {pct}%")
        else:
            v = w.get_volume()
            if v:
                print(f"🔊 Volume: {v['pct']}% ({v['db']:+.1f} dB)")
            else:
                _die("Cannot read volume")

    elif cmd == "gain":
        if len(sys.argv) > 2:
            try:
                pct = int(sys.argv[2])
            except:
                _die(f"Not a number: {sys.argv[2]}")
            pct = max(0, min(100, pct))
            w.set_mic_gain_pct(pct)
            print(f"🎤 Gain → {pct}%")
        else:
            g = w.get_mic_gain()
            if g:
                print(f"🎤 Gain: {g['pct']}% ({g['db']:+.1f} dB)")
            else:
                _die("Cannot read gain")

    elif cmd == "monitor":
        try:
            cmd_monitor(w)
        except KeyboardInterrupt:
            print("\nStopped.")

    else:
        _die(f"Unknown command '{cmd}' — run with --help")


if __name__ == "__main__":
    main()
