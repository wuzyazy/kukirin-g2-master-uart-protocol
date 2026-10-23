"""Software UART decoder for one channel of a sigrok raw binary capture.

8N1, optional inversion. Bytes are grouped into bursts separated by idle gaps
longer than GAP_US; each burst is printed with its start time and the gap
before it.

Usage: la_uart.py FILE SAMPLERATE_HZ CHANNEL BAUD INVERT(0/1) [GAP_US] [START_S] [DUR_S]
"""
import sys


def main():
    path, rate, ch, baud, inv = sys.argv[1], int(float(sys.argv[2])), int(sys.argv[3]), float(sys.argv[4]), sys.argv[5] == "1"
    gap_us = float(sys.argv[6]) if len(sys.argv) > 6 else 500.0
    start_s = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0
    dur_s = float(sys.argv[8]) if len(sys.argv) > 8 else 1e9
    with open(path, "rb") as f:
        f.seek(int(start_s * rate))
        data = f.read(int(dur_s * rate) if dur_s < 1e8 else -1)
    mask = 1 << ch
    spb = rate / baud
    idle = 0 if inv else 1

    def lvl(i):
        b = 1 if data[i] & mask else 0
        return b ^ 1 if inv else b

    n = len(data)
    i = 0
    bursts = []
    cur = None
    last_end = None
    errors = 0
    while i < n - int(spb * 10):
        # find start bit: transition from idle (logical 1) to logical 0
        if lvl(i) == 1:
            i += 1
            continue
        mid = i + spb / 2
        if lvl(int(mid)) != 0:
            i += 1
            continue
        byte = 0
        for b in range(8):
            if lvl(int(mid + spb * (b + 1))):
                byte |= 1 << b
        stop_ok = lvl(int(mid + spb * 9)) == 1
        if not stop_ok:
            errors += 1
        t = (i / rate) + start_s
        if last_end is None or (i - last_end) / rate * 1e6 > gap_us:
            gap = None if last_end is None else (i - last_end) / rate * 1e3
            cur = [t, gap, []]
            bursts.append(cur)
        cur[2].append(byte if stop_ok else -byte - 1)
        i = int(mid + spb * 9)
        # skip rest of stop bit
        while i < n and lvl(i) == 0:
            i += 1
        last_end = i

    for t, gap, bs in bursts:
        hexs = " ".join(f"{b:02X}" if b >= 0 else f"!{-b - 1:02X}" for b in bs)
        g = "   start" if gap is None else f"{gap:7.2f}ms"
        print(f"{t:9.4f}s  +{g}  [{len(bs):2d}]  {hexs}")
    print(f"# bursts {len(bursts)}, framing errors {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
