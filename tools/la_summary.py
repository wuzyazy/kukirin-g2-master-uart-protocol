"""Summarise a sigrok raw binary capture (1 byte per sample, bit N = channel DN).

Per channel: share of time high, edge count, shortest/median pulse (hints at
serial baud rate) and edges per second. Pure Python (no numpy needed).

Usage: la_summary.py FILE SAMPLERATE_HZ
"""
import itertools
import statistics
import sys


def main():
    path, rate = sys.argv[1], int(float(sys.argv[2]))
    data = open(path, "rb").read()
    secs = len(data) / rate
    print(f"{len(data)} samples, {secs:.2f} s at {rate} Hz")

    # Runs of identical whole-byte values: (start index, value)
    runs = []
    pos = 0
    for val, grp in itertools.groupby(data):
        runs.append((pos, val))
        pos += sum(1 for _ in grp)

    for ch in range(8):
        m = 1 << ch
        edges = []
        prev = runs[0][1] & m
        high = 0
        for i, (start, val) in enumerate(runs):
            end = runs[i + 1][0] if i + 1 < len(runs) else len(data)
            bit = val & m
            if bit:
                high += end - start
            if i and bit != prev:
                edges.append(start)
            prev = bit
        line = f"D{ch}: high {high / len(data) * 100:5.1f}%  edges {len(edges):7d}"
        if len(edges) > 1:
            widths = [b - a for a, b in zip(edges, edges[1:])]
            line += (f"  shortest {min(widths) / rate * 1e6:8.1f} us"
                     f"  median {statistics.median(widths) / rate * 1e6:8.1f} us")
        per_s = [0] * (int(secs) + 1)
        for e in edges:
            per_s[e // rate] += 1
        line += f"\n     edges/s: {per_s[:int(secs)]}"
        print(line)


if __name__ == "__main__":
    main()
