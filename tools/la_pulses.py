"""Pulse-width histogram and a timeline excerpt for one channel of a sigrok
raw binary capture.

Usage: la_pulses.py FILE SAMPLERATE_HZ CHANNEL [START_S] [EXCERPT_MS]
"""
import collections
import itertools
import sys


def runs_for(data, mask):
    """List of (level, length) runs for one channel."""
    out = []
    for level, grp in itertools.groupby(data, key=lambda b: 1 if b & mask else 0):
        out.append((level, sum(1 for _ in grp)))
    return out


def main():
    path, rate, ch = sys.argv[1], int(float(sys.argv[2])), int(sys.argv[3])
    start = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    excerpt_ms = float(sys.argv[5]) if len(sys.argv) > 5 else 20.0
    with open(path, "rb") as f:
        f.seek(int(start * rate))
        data = f.read(int(5 * rate))  # 5 s window
    runs = runs_for(data, 1 << ch)
    us = 1e6 / rate

    for level in (1, 0):
        hist = collections.Counter(round(n * us) for lv, n in runs[1:-1] if lv == level)
        print(f"\n{'HIGH' if level else 'LOW'} pulse widths (us: count), top 25:")
        for w, c in sorted(hist.items(), key=lambda kv: -kv[1])[:25]:
            print(f"  {w:8d}: {c}")

    print(f"\nTimeline, first {excerpt_ms} ms from {start}s (level:duration_us):")
    t = 0.0
    parts = []
    for lv, n in runs:
        parts.append(f"{'H' if lv else 'L'}{n * us:.1f}")
        t += n * us
        if t > excerpt_ms * 1000:
            break
    print(" ".join(parts))


if __name__ == "__main__":
    main()
