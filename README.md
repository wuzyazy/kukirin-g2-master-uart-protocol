# KuKirin G2 Master 2025 (Version C, Lenzod) — display ↔ controller UART protocol

Reverse-engineered protocol of the single data wire between the **display** and the **controller** of the
**KuKirin G2 Master, late-2025 "Version C"** with **Lenzod** electronics (52 V / 14S, dual motor).

The goal of this work is to replace the stock controller with a VESC (Spintend Ubox) while keeping the
stock display fully working: speed, battery, modes, limited ("police") mode, lights, turn signals and the
single/dual motor switch.

> ⚠️ **Not the same as the G2 Pro / Luckygroup protocol.** Earlier G2 Master units (2024 and the first
> 2025 versions) used Luckygroup electronics with a different 6-pin cable and protocol. This document only
> covers the Lenzod version (display label `TFM13-FEIMI-11 Version B`, controller
> `C-LENZOD52V25A-FM-G2-Master`).

## Physical layer

| Item | Value |
|---|---|
| Wire | **Green** in the display cable (one wire, both directions, half-duplex) |
| Ground | Black |
| Levels | 0 V / ~5 V |
| Baud rate | **230400** |
| Format | **8N1** |
| Polarity | **Inverted** — the line idles **low**, start bit is high |

Both sides transmit on their own timer on the same wire, so frames occasionally collide. Receivers must
drop frames whose CRC does not match (≈0.1 % in our captures).

A normal USB-UART adapter (CP2102 etc.) will show **nothing** unless it supports 230400 baud **and**
inverted RX. A cheap 24 MHz logic analyzer (Saleae clone, sigrok `fx2lafw`) at 1–2 MHz sample rate is
enough to capture it.

### Other wires in the display cable (measured)

| Colour | Function |
|---|---|
| Black | GND |
| Green | **Data** (this protocol) |
| Blue | Ignition (0 V when off, battery voltage when on) |
| Purple | Analog throttle (~0.8 V rest → ~4 V full) |
| Yellow | ~4.8 V supply |
| White | Single/dual motor button (read by the controller) |
| Red | ~22 V, not steady (30–40 V spikes); role not confirmed — do **not** connect logic to it |

## Frame format

```
66 66 | 00 LEN | payload (LEN bytes) | CRC_hi CRC_lo
```

* `66 66` — header
* `00 LEN` — payload length (big-endian; only `0x0A` and `0x0E` were ever seen)
* **CRC** — **CRC-16/MODBUS** (poly `0x8005` reflected = `0xA001`, init `0xFFFF`, no final XOR)
  computed over the **payload only**, sent **high byte first**

```python
def crc_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc
```

Verified on 12,651 of 12,663 captured frames (the 12 failures are collisions).

Multi-byte values are **little-endian**. Payload offsets below start at 0 (= byte 4 of the frame).

## Display → controller (`LEN = 0x0A`, every ~20 ms)

Example: `66 66 00 0A 01 00 65 00 14 03 00 00 00 00 C1 A0`

| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | Flags: `0x01` display ready (0 for the first ~1 s after power-on), `0x08` headlight, `0x10` left turn signal, `0x20` right turn signal |
| 1 | 1 | 0 |
| 2 | 1 | **Mode**: `0x63/0x64/0x65` = mode 1/2/3, `0x60/0x61/0x62` = **limited** mode 1/2/3 |
| 3 | 1 | 0 |
| 4–5 | 2 | **Throttle**, 12-bit ADC (≈788 at rest, 4092 full) |
| 6–7 | 2 | **Brake**: `0x0FFF` while the (right) brake lever is pulled, else 0 |
| 8–9 | 2 | 0 (possibly the second brake — the left sensor on the test scooter was broken) |

### Modes and limited ("police") mode

There is no separate flag: the display changes the mode value. **Limited = normal − 3.**
The combo (hold right brake + press the power button twice) toggles it; the display does the combo
logic itself and shows the icon.

| Value | Meaning | Stock behaviour observed |
|---|---|---|
| `0x63` | Mode 1 | 20 km/h at half throttle (wheel in the air) |
| `0x64` | Mode 2 | |
| `0x65` | Mode 3 | 79 km/h (wheel in the air) |
| `0x60` | Limited mode 1 | |
| `0x61` | Limited mode 2 | |
| `0x62` | Limited mode 3 | 25.4 km/h |

Pressing the power button also cycles the mode value, so the combo shows up as e.g. `65 → 63 → 64 → 61`.

## Controller → display (`LEN = 0x0E`, every ~130 ms)

Example: `66 66 00 0E 08 00 00 00 10 27 1B 17 00 00 00 00 01 00 83 3A`

| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | Flags: `0x08` always set, `0x02` motor driving (throttle applied), `0x10` **dual-motor** mode |
| 1–3 | 3 | 0 |
| 4–5 | 2 | `10000` (`0x2710`) — meaning unknown, rarely dips to 9999/0; send 10000 |
| 6–7 | 2 | **Battery voltage**, 0.01 V (5914 = 59.14 V, multimeter 59.1 V) |
| 8–9 | 2 | **Speed**, 0.01 km/h (2030 → display 20, 7919 → display 79) |
| 10–11 | 2 | 0 |
| 12 | 1 | 1 (briefly 0 now and then) |
| 13 | 1 | 0 |

At power-on the first controller frame is all zeros except `08 … 01 00`, then `0x2710` and the voltage appear.

## Not covered yet

* P-settings menu — nothing extra is sent during normal riding; the settings either stay in the display
  or are sent only when edited.
* Left brake (sensor broken on the test unit), cruise control, error codes.

## Relation to the Lenzod Bluetooth protocol

The Lenzod Pro app talks to the scooter over BLE with a **different** framing
(`FF 55 CMD LEN DATA SUM`, SUM = byte sum mod 256, one message per value, speed in 0.001 km/h,
gear 2/3/4). The display seems to translate the wire data into those BLE messages; turn signals,
brake, single/dual and limited mode are not exposed over BLE.

## Tools

All Python 3, no extra packages. Captures are sigrok raw binary (`-O binary`, 1 byte per sample,
bit N = channel DN).

```
sigrok-cli -d fx2lafw --config samplerate=2m --time 60000 -O binary -o green.bin
```

| Script | Purpose |
|---|---|
| `tools/la_summary.py FILE RATE` | Edges per channel per second, shortest pulse |
| `tools/la_pulses.py FILE RATE CH [START] [MS]` | Pulse-width histogram + timeline (find baud and polarity) |
| `tools/la_uart.py FILE RATE CH BAUD INVERT [GAP_US] [START_S] [DUR_S]` | Software UART decoder, prints bursts |
| `tools/frames_fields.py FRAMES.txt [--quiet 0E:10,11]` | Field-level change log of `66 66` frames |
| `tools/crc_search.py` | Brute-force CRC16 parameter search used to find the checksum |
| `dashboard/scooter_live.py` | Live animated dashboard (needs sigrok-cli; streams from the analyzer) |

`samples/decoded_boot_excerpt.txt` is a decoded excerpt starting at power-on.

### Live dashboard

```
python dashboard/scooter_live.py --channel D0 --log ride_log.csv
```

Shows speed gauge, mode / limited mode, throttle, battery, brake, headlight, turn signals and
single/dual in real time. Expects `sigrok-cli.exe` in `%LOCALAPPDATA%\sigrok-cli\` (edit `SIGROK` at the
top otherwise) and the analyzer with the WinUSB driver (Zadig). Close PulseView first.
Keys: F11 fullscreen, R reset max speed, D raw frames.

## Disclaimer

Measured on one scooter. Working on a 52 V+ system is dangerous: never connect logic-level tools to
the battery, ignition or red wires, and keep a common ground. Use at your own risk.
