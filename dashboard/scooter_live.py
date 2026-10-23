"""Live dashboard for the KuKirin G2 Master (Lenzod) display <-> controller bus.

Reads the green wire through a Saleae-clone logic analyzer (sigrok-cli,
fx2lafw, channel D0), decodes the 230400 baud inverted UART with sigrok's
UART decoder, rebuilds the 66 66 frames, checks the CRC-16/MODBUS and shows
the values on an animated full-window dashboard.

Frame: 66 66 00 LEN payload[LEN] CRC_hi CRC_lo (CRC over payload only)
  LEN 0x0A  display -> controller, every 20 ms
  LEN 0x0E  controller -> display, every 130 ms

Keys: F11 fullscreen, Esc leave fullscreen, R reset max speed, D toggle raw frames.

Usage: python scooter_live.py [--channel D0] [--log file.csv] [--gauge-max 100]
"""
import argparse
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk

SIGROK = os.path.join(os.environ.get("LOCALAPPDATA", ""), "sigrok-cli", "sigrok-cli.exe")

# Display byte 2 = (PB e-brake strength << 5) | mode; mode 3/4/5 normal, 0/1/2 limited.
MODES = {
    3: ("1", False), 4: ("2", False), 5: ("3", False),
    0: ("1", True), 1: ("2", True), 2: ("3", True),
}
THR_MIN, THR_MAX = 788, 4092
CELLS = 14  # 52 V pack
FPS = 40

BG = "#07090d"
DIM = "#1a1f29"
GRID = "#2a3140"
TEXT = "#e8edf5"
MUTED = "#6b7688"
CYAN = "#29d3ff"
ORANGE = "#ffa62b"
RED = "#ff3b4e"
GREEN = "#3dffa0"
YELLOW = "#ffe066"
FONT = "Segoe UI"


def crc_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def s16(lo, hi):
    """Little-endian signed 16-bit."""
    v = lo | hi << 8
    return v - 0x10000 if v & 0x8000 else v


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    """Blend two #rrggbb colours."""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(lerp(x, y, t)):02x}" for x, y in zip(a, b))


def speed_colour(frac):
    """Cyan -> green -> yellow -> orange -> red along the gauge."""
    stops = [(0.0, CYAN), (0.35, GREEN), (0.6, YELLOW), (0.8, ORANGE), (1.0, RED)]
    for (f1, c1), (f2, c2) in zip(stops, stops[1:]):
        if frac <= f2:
            return mix(c1, c2, (frac - f1) / (f2 - f1))
    return RED


class Bus:
    """Runs sigrok-cli, turns its decoded bytes into checked frames."""

    def __init__(self, channel, out_q):
        self.channel = channel
        self.q = out_q
        self.proc = None
        self.buf = bytearray()
        self.stats = {"ok": 0, "bad": 0, "bytes": 0}

    def start(self):
        cmd = [SIGROK, "-d", "fx2lafw", "--config", "samplerate=1m", "--continuous",
               "-P", f"uart:rx={self.channel}:baudrate=230400:invert_rx=yes",
               "-A", "uart=rx-data"]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        # In --continuous mode sigrok-cli stops on "any key"; an empty stdin counts
        # as one, so hand it a pipe that stays open for the whole session.
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0, creationflags=flags)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def _read_err(self):
        for line in iter(self.proc.stderr.readline, b""):
            self.q.put(("err", line.decode(errors="replace").strip()))

    def _read(self):
        pending = b""
        while True:
            chunk = self.proc.stdout.read(512)
            if not chunk:
                self.q.put(("err", "sigrok-cli stopped (analyzer unplugged or PulseView open?)"))
                return
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                if b": " not in line:
                    continue
                try:
                    self.buf.append(int(line.rsplit(b": ", 1)[1].strip(), 16))
                except ValueError:
                    continue
                self.stats["bytes"] += 1
                self._parse()

    def _parse(self):
        b = self.buf
        while len(b) >= 4:
            if b[0] != 0x66 or b[1] != 0x66:
                del b[0]
                continue
            ln = b[3]
            if b[2] != 0 or ln not in (0x0A, 0x0E):
                del b[0]
                continue
            total = 4 + ln + 2
            if len(b) < total:
                return
            fr = bytes(b[:total])
            payload = fr[4:-2]
            if crc_modbus(payload) == (fr[-2] << 8 | fr[-1]):
                self.stats["ok"] += 1
                self.q.put(("frame", ln, payload, fr))
                del b[:total]
            else:
                self.stats["bad"] += 1
                del b[0]


class Dashboard:
    def __init__(self, root, bus, q, log_path, gauge_max):
        self.root, self.bus, self.q = root, bus, q
        self.gmax = gauge_max
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None
        if self.log and self.log.tell() == 0:
            self.log.write("time,speed_kmh,volts,throttle_raw,mode,limited,brake,headlight,left,right,dual,driving\n")
        self.state = {}
        self.raws = {}
        self.last_rx = {0x0A: 0.0, 0x0E: 0.0}
        self.err_text = ""
        self.max_speed = 0.0
        self.show_raw = False
        self.fullscreen = False
        # animated (displayed) values
        self.a_speed = 0.0
        self.a_thr = 0.0
        self.a_bat = 0.0
        self.glow = {k: 0.0 for k in ("left", "right", "brake", "headlight", "dual", "driving", "link")}
        self.sweep_start = None      # startup needle sweep
        self.mode_flash = 0.0        # flash when the mode changes
        self.prev_mode = None
        self.t0 = time.time()
        self.watched = {}
        self.events = []
        self.mystery_flash = 0.0
        self.mystery_peak = 0
        self.show_lab = True

        root.title("G2 Master — live")
        root.configure(bg=BG)
        root.geometry("1200x780")
        root.minsize(640, 420)
        self.c = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        root.bind("<F11>", lambda e: self.set_fullscreen(not self.fullscreen))
        root.bind("<Escape>", lambda e: self.set_fullscreen(False))
        root.bind("<r>", lambda e: setattr(self, "max_speed", 0.0))
        root.bind("<d>", lambda e: setattr(self, "show_raw", not self.show_raw))
        root.bind("<l>", lambda e: setattr(self, "show_lab", not self.show_lab))
        root.bind("<p>", lambda e: setattr(self, "mystery_peak", 0))
        self.root.after(int(1000 / FPS), self.frame)

    def set_fullscreen(self, on):
        self.fullscreen = on
        self.root.attributes("-fullscreen", on)

    # ---------- data ----------
    def handle(self, ln, p, fr):
        now = time.time()
        if not self.last_rx[0x0A] and not self.last_rx[0x0E]:
            self.sweep_start = now
        self.last_rx[ln] = now
        s = self.state
        if ln == 0x0A:
            s["flags_a"] = p[0]
            s["mode_raw"] = p[2] & 0x1F
            s["pb"] = p[2] >> 5
            s["throttle"] = p[4] | p[5] << 8
            s["brake"] = any(p[6:10])
            if self.prev_mode is not None and s["mode_raw"] != self.prev_mode:
                self.mode_flash = 1.0
            self.prev_mode = s["mode_raw"]
            self.watch("display flags", f"0x{p[0]:02X}")
            self.watch("PB", str(s["pb"]))
            self.watch("display byte 1/3", f"{p[1]:02X} {p[3]:02X}")
        else:
            s["flags_b"] = p[0]
            s["volts"] = (p[6] | p[7] << 8) / 100
            # Speeds are signed int16 in 0.01 km/h (negative = wheel turning backwards).
            s["rear"] = s16(p[8], p[9]) / 100
            s["front"] = s16(p[10], p[11]) / 100   # front motor, only non-zero in dual mode
            s["speed"] = abs(s["rear"])
            s["error"] = p[1]
            s["mystery"] = s["front"]
            if s["mystery"] != self.state.get("_mystery_prev"):
                self.mystery_flash = 1.0
                if s["mystery"] > self.mystery_peak:
                    self.mystery_peak = s["mystery"]
            s["_mystery_prev"] = s["mystery"]
            self.watch("ctrl flags", f"0x{p[0]:02X}")
            self.watch("error", f"0x{p[1]:02X}")
            self.watch("front motor", "stopped" if not s["front"] else ("reverse" if s["front"] < 0 else "turning"))
            self.max_speed = max(self.max_speed, s["speed"])
            if self.log:
                fa = s.get("flags_a", 0)
                name, lim = MODES.get(s.get("mode_raw", 0), ("?", False))
                self.log.write(f"{now:.3f},{s['speed']:.2f},{s['volts']:.2f},{s.get('throttle', 0)},{name},{int(lim)},"
                               f"{int(s.get('brake', False))},{int(bool(fa & 0x08))},{int(bool(fa & 0x10))},"
                               f"{int(bool(fa & 0x20))},{int(bool(p[0] & 0x10))},{int(bool(p[0] & 0x02))}\n")
        self.raws[ln] = " ".join(f"{x:02X}" for x in fr)

    def watch(self, name, value, quiet=False):
        """Log a timestamped event whenever a watched field changes."""
        old = self.watched.get(name)
        self.watched[name] = value
        if old is not None and old != value:
            stamp = time.strftime("%H:%M:%S")
            self.events.append((time.time(), f"{stamp}  {name}: {old} → {value}"))
            self.events = self.events[-7:]
            if self.log:
                self.log.write(f"# {stamp} {name}: {old} -> {value}\n")

    def pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "frame":
                    self.handle(*item[1:])
                elif "stopped" in item[1] or "rror" in item[1]:
                    self.err_text = item[1]
        except queue.Empty:
            pass

    # ---------- animation step ----------
    def frame(self):
        self.pump()
        now = time.time()
        s = self.state
        linked = self.last_rx[0x0E] and now - self.last_rx[0x0E] < 1.0
        target_speed = s.get("speed", 0.0) if linked else 0.0
        if self.sweep_start is not None:
            t = now - self.sweep_start
            if t < 1.6:   # needle sweep up and back down when the scooter wakes
                target_speed = self.gmax * math.sin(min(t / 1.6, 1.0) * math.pi)
                self.a_speed = target_speed
            else:
                self.sweep_start = None
        k = 0.22
        self.a_speed = lerp(self.a_speed, target_speed, k)
        thr_pct = 0.0
        if "throttle" in s and linked:
            thr_pct = max(0.0, min(1.0, (s["throttle"] - THR_MIN) / (THR_MAX - THR_MIN)))
        self.a_thr = lerp(self.a_thr, thr_pct, 0.3)
        bat_pct = 0.0
        if "volts" in s:
            bat_pct = max(0.0, min(1.0, (s["volts"] / CELLS - 3.3) / (4.2 - 3.3)))
        self.a_bat = lerp(self.a_bat, bat_pct, 0.08)

        fa, fb = s.get("flags_a", 0), s.get("flags_b", 0)
        blink = (now * 1.6) % 1.0 < 0.55
        targets = {
            "left": 1.0 if (fa & 0x10 and blink and linked) else 0.0,
            "right": 1.0 if (fa & 0x20 and blink and linked) else 0.0,
            "brake": 1.0 if (s.get("brake") and linked) else 0.0,
            "headlight": 1.0 if (fa & 0x08 and linked) else 0.0,
            "dual": 1.0 if (fb & 0x10 and linked) else 0.0,
            "driving": 1.0 if (fb & 0x02 and linked) else 0.0,
            "link": 1.0 if linked else 0.0,
        }
        for key, tv in targets.items():
            self.glow[key] = lerp(self.glow[key], tv, 0.45 if key in ("left", "right") else 0.25)
        self.mode_flash = max(0.0, self.mode_flash - 1.0 / (FPS * 0.6))
        self.mystery_flash = max(0.0, self.mystery_flash - 1.0 / (FPS * 0.8))

        self.draw(now, linked)
        self.root.after(int(1000 / FPS), self.frame)

    # ---------- drawing ----------
    def draw(self, now, linked):
        c = self.c
        c.delete("all")
        W, H = max(c.winfo_width(), 200), max(c.winfo_height(), 200)
        u = min(W / 1200, H / 780)  # scale unit
        s = self.state

        def f(size, weight="bold"):
            return (FONT, max(6, int(size * u)), weight)

        # --- gauge ---
        cx, cy = W / 2, H * 0.47
        R = min(W * 0.30, H * 0.40)
        start, extent = 225, -270  # degrees, clockwise sweep
        width = max(6, R * 0.11)
        c.create_arc(cx - R, cy - R, cx + R, cy + R, start=start, extent=extent,
                     style="arc", width=width, outline=DIM)
        frac = max(0.0, min(1.0, self.a_speed / self.gmax))
        segs = 90
        lit = int(frac * segs)
        for i in range(lit):
            a0 = start + extent * i / segs
            col = speed_colour(i / segs)
            c.create_arc(cx - R, cy - R, cx + R, cy + R, start=a0, extent=extent / segs - 0.4,
                         style="arc", width=width, outline=col)
        # glow tip
        if frac > 0.002:
            ang = math.radians(start + extent * frac)
            tx, ty = cx + R * math.cos(ang), cy - R * math.sin(ang)
            for rr, t in ((width * 1.3, 0.75), (width * 0.9, 0.45), (width * 0.55, 0.0)):
                c.create_oval(tx - rr, ty - rr, tx + rr, ty + rr, fill=mix(speed_colour(frac), BG, t), outline="")
        # ticks + labels
        step = 10 if self.gmax <= 120 else 20
        for v in range(0, int(self.gmax) + 1, step // 2):
            a = math.radians(start + extent * v / self.gmax)
            major = v % step == 0
            r1 = R - width * (1.05 if major else 0.85)
            r2 = R - width * (1.7 if major else 1.3)
            c.create_line(cx + r1 * math.cos(a), cy - r1 * math.sin(a), cx + r2 * math.cos(a), cy - r2 * math.sin(a),
                          fill=TEXT if major and v <= self.a_speed + 0.5 else GRID, width=max(1, 3 * u if major else 2 * u))
            if major:
                rl = R - width * 2.4
                c.create_text(cx + rl * math.cos(a), cy - rl * math.sin(a), text=str(v), fill=MUTED, font=f(18, "normal"))

        # big speed number
        sp = self.a_speed if linked or self.sweep_start else 0.0
        sp_col = speed_colour(frac) if linked else MUTED
        c.create_text(cx, cy - R * 0.06, text=f"{sp:.0f}", fill=TEXT, font=f(170))
        c.create_text(cx, cy + R * 0.33, text="km/h", fill=sp_col, font=f(30, "normal"))
        c.create_text(cx, cy + R * 0.50, text=f"MAX {self.max_speed:.1f}", fill=MUTED, font=f(18))

        # mode badge (top centre)
        if "mode_raw" in s and linked:
            name, lim = MODES.get(s["mode_raw"], (f"0x{s['mode_raw']:02X}", False))
            label = f"LIMITED {name}" if lim else f"MODE {name}"
            base = ORANGE if lim else CYAN
            pulse = 0.5 + 0.5 * math.sin(now * 5) if lim else 1.0
            col = mix(BG, base, 0.55 + 0.45 * pulse)
            col = mix(col, "#ffffff", self.mode_flash * 0.8)
        else:
            label, col = "MODE –", GRID
        bw, bh = 300 * u * (1.25 if "LIMITED" in label else 1.0), 64 * u
        by = H * 0.045
        self.pill(cx - bw / 2, by, cx + bw / 2, by + bh, col, fill=mix(BG, col, 0.15))
        c.create_text(cx, by + bh / 2, text=label, fill=col, font=f(32))

        # turn arrows
        for side, key in ((-1, "left"), (1, "right")):
            g = self.glow[key]
            ax = cx + side * (R + width * 2.6 + 70 * u)
            ay = cy - R * 0.15
            sz = 70 * u
            pts = [ax + side * sz, ay, ax - side * sz * 0.2, ay - sz * 0.8, ax - side * sz * 0.2, ay - sz * 0.3,
                   ax - side * sz * 1.0, ay - sz * 0.3, ax - side * sz * 1.0, ay + sz * 0.3,
                   ax - side * sz * 0.2, ay + sz * 0.3, ax - side * sz * 0.2, ay + sz * 0.8]
            if g > 0.05:
                glow_pts = [(x - ax) * 1.18 + ax if i % 2 == 0 else (x - ay) * 1.18 + ay for i, x in enumerate(pts)]
                c.create_polygon(glow_pts, fill=mix(BG, GREEN, g * 0.35), outline="")
            c.create_polygon(pts, fill=mix(DIM, GREEN, g), outline=mix(GRID, GREEN, g), width=max(1, 2 * u))

        # indicator chips (row under the gauge)
        chips = [("brake", "BRAKE", RED), ("headlight", "LIGHT", YELLOW), ("dual", "DUAL", GREEN), ("driving", "POWER", CYAN)]
        cw, ch, gap = 170 * u, 58 * u, 22 * u
        total = len(chips) * cw + (len(chips) - 1) * gap
        x0, y0 = cx - total / 2, cy + R + width * 0.9
        for i, (key, text, colr) in enumerate(chips):
            g = self.glow[key]
            x = x0 + i * (cw + gap)
            col = mix(GRID, colr, g)
            if g > 0.05:
                self.pill(x - 6 * u, y0 - 6 * u, x + cw + 6 * u, y0 + ch + 6 * u, mix(BG, colr, g * 0.4), fill=mix(BG, colr, g * 0.12), width=1)
            self.pill(x, y0, x + cw, y0 + ch, col, fill=mix(BG, colr, g * 0.22))
            c.create_text(x + cw / 2, y0 + ch / 2, text=text, fill=mix(MUTED, "#ffffff", g), font=f(22))

        # bottom bars
        by0 = y0 + ch + 34 * u
        bar_w = min(W * 0.36, 520 * u)
        bar_h = 26 * u
        self.bar(W * 0.27 - bar_w / 2, by0, bar_w, bar_h, self.a_thr, mix(CYAN, ORANGE, self.a_thr),
                 "THROTTLE", f"{self.a_thr * 100:.0f} %", f)
        v = s.get("volts")
        bat_col = RED if self.a_bat < 0.2 else (YELLOW if self.a_bat < 0.45 else GREEN)
        self.bar(W * 0.73 - bar_w / 2, by0, bar_w, bar_h, self.a_bat, bat_col,
                 "BATTERY", f"{v:.1f} V  ·  {self.a_bat * 100:.0f} %" if v else "– V", f)

        # status line
        st = self.bus.stats
        lc = mix(RED, GREEN, self.glow["link"])
        c.create_oval(24 * u, H - 34 * u, 40 * u, H - 18 * u, fill=lc, outline="")
        status = "LIVE" if linked else ("NO SIGNAL — turn the scooter on" if not self.err_text else self.err_text)
        c.create_text(52 * u, H - 26 * u, text=status, fill=lc if linked else MUTED, font=f(15), anchor="w")
        c.create_text(W - 24 * u, H - 26 * u, text=f"frames {st['ok']}   bad {st['bad']}   ·   F11 fullscreen   R reset max   D raw   L lab",
                      fill=GRID, font=f(13, "normal"), anchor="e")
        if self.show_raw:
            y = H - 80 * u
            for ln in sorted(self.raws):
                c.create_text(24 * u, y, text=f"{'D→C' if ln == 0x0A else 'C→D'}  {self.raws[ln]}", fill="#4f8f6f",
                              font=("Consolas", max(6, int(13 * u))), anchor="w")
                y += 22 * u

        if self.show_lab:
            self.draw_lab(now, linked, f, u, W, H)

        # waiting overlay
        if not linked and not self.sweep_start:
            pulse = 0.5 + 0.5 * math.sin(now * 3)
            c.create_text(cx, cy + R * 0.72, text="WAITING FOR SCOOTER", fill=mix(GRID, CYAN, pulse * 0.7), font=f(22))

    def draw_lab(self, now, linked, f, u, W, H):
        """Top-left: unknown controller value (bytes 10-11) + change log.
        Top-right: display P-settings and controller status bits."""
        c = self.c
        s = self.state
        # --- mystery value box ---
        x, y, w, h = 20 * u, 20 * u, 300 * u, 150 * u
        val = s.get("mystery", 0) if linked else 0
        fl = self.mystery_flash
        border = mix(GRID, YELLOW, max(fl, 0.35 if val else 0.0))
        c.create_rectangle(x, y, x + w, y + h, outline=border, width=max(1, 2 * u), fill=mix(BG, YELLOW, fl * 0.18))
        rear = s.get("rear", 0.0) if linked else 0.0
        c.create_text(x + 12 * u, y + 18 * u, text="FRONT / REAR MOTOR  km/h", fill=MUTED, font=f(12), anchor="w")
        c.create_text(x + 12 * u, y + 70 * u, text=f"{val:.1f}", fill=mix(TEXT, YELLOW, max(fl, 0.6 if val else 0)), font=f(40), anchor="w")
        c.create_text(x + w - 12 * u, y + 70 * u, text=f"{rear:.1f}", fill=TEXT, font=f(28), anchor="e")
        c.create_text(x + 12 * u, y + 122 * u, text=f"front  ·  peak {self.mystery_peak:.1f} (P)", fill=MUTED, font=f(12, "normal"), anchor="w")
        c.create_text(x + w - 12 * u, y + 122 * u, text="rear", fill=MUTED, font=f(12, "normal"), anchor="e")
        # change log
        ey = y + h + 18 * u
        c.create_text(x, ey, text="CHANGES", fill=MUTED, font=f(12), anchor="w")
        for i, (t, txt) in enumerate(reversed(self.events)):
            age = now - t
            col = mix(YELLOW, MUTED, min(1.0, age / 6))
            c.create_text(x, ey + (i + 1) * 22 * u, text=txt, fill=col, font=("Consolas", max(6, int(12 * u))), anchor="w")

        # --- settings + status (top-right) ---
        fa, fb = s.get("flags_a", 0), s.get("flags_b", 0)
        rows = [
            ("PB e-brake", str(s.get("pb", "–")), CYAN),
            ("P5 cruise", "ON" if fa & 0x02 else "off", GREEN if fa & 0x02 else MUTED),
            ("P6 kick start", "ON" if fa & 0x04 else "off", GREEN if fa & 0x04 else MUTED),
            ("cruise active", "YES" if fb & 0x01 else "no", ORANGE if fb & 0x01 else MUTED),
            ("ctrl flag 0x04", "SET" if fb & 0x04 else "–", YELLOW if fb & 0x04 else MUTED),
            ("error code", f"0x{s.get('error', 0):02X}", RED if s.get("error") else MUTED),
        ]
        rx = W - 20 * u
        for i, (name, value, colr) in enumerate(rows):
            ry = 30 * u + i * 30 * u
            c.create_text(rx - 110 * u, ry, text=name, fill=MUTED, font=f(13, "normal"), anchor="e")
            c.create_text(rx, ry, text=value if linked else "–", fill=colr if linked else GRID, font=f(15), anchor="e")

    def pill(self, x1, y1, x2, y2, outline, fill=BG, width=2):
        r = (y2 - y1) / 2
        c = self.c
        pts = []
        for cxp, a0 in ((x2 - r, -90), (x1 + r, 90)):
            for i in range(0, 181, 15):
                a = math.radians(a0 + i)
                pts += [cxp + r * math.cos(a), (y1 + y2) / 2 - r * math.sin(a)]
        c.create_polygon(pts, fill=fill, outline=outline, width=max(1, width), smooth=False)

    def bar(self, x, y, w, h, frac, col, title, value, f):
        c = self.c
        c.create_text(x, y - 16 * (h / 26), text=title, fill=MUTED, font=f(16), anchor="w")
        c.create_text(x + w, y - 16 * (h / 26), text=value, fill=TEXT, font=f(18), anchor="e")
        self.pill(x, y + 6, x + w, y + 6 + h, GRID, fill=DIM)
        if frac > 0.01:
            fw = max(h, w * frac)
            self.pill(x, y + 6, x + fw, y + 6 + h, col, fill=col)

    def close(self):
        self.bus.stop()
        if self.log:
            self.log.close()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="D0", help="analyzer channel with the green wire (CH1 on the case = D0)")
    ap.add_argument("--log", help="optional CSV file to append decoded values to")
    ap.add_argument("--gauge-max", type=float, default=100, help="top of the speed gauge in km/h")
    args = ap.parse_args()
    if not os.path.exists(SIGROK):
        sys.exit(f"sigrok-cli not found at {SIGROK}")
    q = queue.Queue()
    bus = Bus(args.channel, q)
    root = tk.Tk()
    app = Dashboard(root, bus, q, args.log, args.gauge_max)
    root.protocol("WM_DELETE_WINDOW", app.close)
    bus.start()
    root.mainloop()


if __name__ == "__main__":
    main()
