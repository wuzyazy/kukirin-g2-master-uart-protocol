"""Field-level change log for 66 66 frames from la_uart.py output.

Splits merged bursts into frames (66 66 00 LEN ... + 2 check bytes), then for
each frame type (LEN) prints a line whenever any byte except the last two
changes, showing only the changed byte positions. Bytes listed in --quiet
(type:pos,pos) are tracked but printed only once per second.

Usage: frames_fields.py FRAMES_TXT [--quiet 0E:10,11]
"""
import sys


def frames_from(tokens):
    i = 0
    while i + 4 <= len(tokens):
        if tokens[i] == "66" and tokens[i + 1] == "66":
            if "!" in tokens[i + 3]: i += 1; continue
            ln = int(tokens[i + 3], 16)
            total = 4 + ln + 2
            if i + total <= len(tokens) and not any("!" in x for x in tokens[i:i + total]):
                yield tokens[i:i + total]
            i += total
        else:
            i += 1


def main():
    path = sys.argv[1]
    quiet = {}
    if "--quiet" in sys.argv:
        spec = sys.argv[sys.argv.index("--quiet") + 1]
        for part in spec.split(";"):
            k, v = part.split(":")
            quiet[k] = {int(x) for x in v.split(",")}
    last = {}
    last_quiet_print = {}
    for line in open(path, encoding="utf-8-sig", errors="replace"):
        if "]" not in line:
            continue
        t = float(line.split("s", 1)[0])
        toks = line.split("]", 1)[1].split()
        for fr in frames_from(toks):
            typ = fr[3]
            body = fr[:-2]
            prev = last.get(typ)
            if prev is None:
                print(f"{t:8.3f}s  {typ} first: {' '.join(fr)}")
            else:
                changed = [i for i in range(len(body)) if body[i] != prev[i]]
                loud = [i for i in changed if i not in quiet.get(typ, set())]
                if loud:
                    desc = "  ".join(f"[{i}] {prev[i]}->{body[i]}" for i in loud)
                    print(f"{t:8.3f}s  {typ} {desc}")
                elif changed and t - last_quiet_print.get(typ, -9) >= 1.0:
                    desc = "  ".join(f"[{i}]={body[i]}" for i in changed)
                    print(f"{t:8.3f}s  {typ} (quiet) {desc}")
                    last_quiet_print[typ] = t
            last[typ] = body


if __name__ == "__main__":
    main()
