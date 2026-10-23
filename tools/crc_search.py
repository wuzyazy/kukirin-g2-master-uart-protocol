"""Brute-force common CRC16 parameter sets / sums over sample frames to find
which one produces the last two bytes, for several start offsets.
"""
FRAMES = [
    "66 66 00 0A 01 00 65 00 14 03 00 00 00 00 C1 A0",
    "66 66 00 0E 08 00 00 00 10 27 1B 17 00 00 00 00 01 00 83 3A",
    "66 66 00 0A 00 00 63 00 18 03 00 00 00 00 E2 71",
    "66 66 00 0A 00 00 63 00 14 03 00 00 00 00 2E 71",
    "66 66 00 0E 08 00 00 00 00 00 00 00 00 00 00 00 01 00 59 A3",
    "66 66 00 0A 01 00 63 00 14 03 00 00 00 00 EB 20",
]


def crc16(data, poly, init, refin, refout, xorout):
    crc = init
    for b in data:
        if refin:
            b = int(f"{b:08b}"[::-1], 2)
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    if refout:
        crc = int(f"{crc:016b}"[::-1], 2)
    return crc ^ xorout


def main():
    frames = [bytes.fromhex(f.replace(" ", "")) for f in FRAMES]
    polys = [0x8005, 0x1021, 0x3D65, 0x0589, 0x8BB7, 0xA097, 0xC867, 0x5935, 0x755B, 0x6F63]
    inits = [0x0000, 0xFFFF, 0x1D0F, 0x800D, 0xB2AA, 0x89EC, 0xC6C6]
    hits = []
    for start in range(0, 5):
        for poly in polys:
            for init in inits:
                for refin in (False, True):
                    for xorout in (0x0000, 0xFFFF):
                        ok_le = ok_be = True
                        for fr in frames:
                            c = crc16(fr[start:-2], poly, init, refin, refin, xorout)
                            ok_le &= c == (fr[-2] | fr[-1] << 8)
                            ok_be &= c == (fr[-2] << 8 | fr[-1])
                        if ok_le or ok_be:
                            hits.append((start, hex(poly), hex(init), refin, hex(xorout), "LE" if ok_le else "BE"))
    for h in hits:
        print("CRC hit: start", h)
    for start in range(0, 5):
        for name, fn in (("sum16", lambda d: sum(d) & 0xFFFF),):
            for fr_ok in [all(fn(fr[start:-2]) in ((fr[-2] | fr[-1] << 8), (fr[-2] << 8 | fr[-1])) for fr in frames)]:
                if fr_ok:
                    print("sum hit start", start)
    if not hits:
        print("no CRC16 hit")


if __name__ == "__main__":
    main()
