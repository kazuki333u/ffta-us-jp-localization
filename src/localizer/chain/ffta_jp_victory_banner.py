#! python3
"""The battle-end banner reads クエストクリア！ (and its three siblings) as in JP.

What the player sees
--------------------

When a battle ends, a large banner of letters flies in over the map.  US draws
``MISSION CLEARED!`` / ``BATTLE WON!`` / ``MISSION FAILED!`` / ``BATTLE LOST!``;
JP draws クエストクリア！ / バトルクリア！ / クエスト失敗！ / バトル失敗！.  An earlier build
still showed the English banner.

How the banner is built
--------------------------------------------

US 0x0809185C builds the banner object and calls one of four loaders
(US 0x08090A80 / 0x08090BBC / 0x08090CF8 / 0x08090E34; JP 0x0808827C / 0x080883C0 /
0x08088500 / 0x08088640).  Each loader decodes one sub-image of two A7 containers
(the OBJ letters and the BG panel behind them), copies a row table (u32 OAM
frame, u16 tile step, u16 palette, u16 x, u16 0 per letter) into the object,
DMAs the tiles to OBJ 0x06013C00 / BG 0x06009C00 and draws a raw u16 tilemap.
US and JP run the same code; the differences are data and immediates:

* containers: US 0x083BF308 (room 0x12E0) <- JP 0x083B1D84 (0x102A bytes)
              US 0x083C05E8 (room 0xBD4)  <- JP 0x083B2DB0 (0xB07 bytes)
  A7 containers hold only offsets relative to their own start.
* tilemap   : US 0x083C3864 (30x4) <- JP 0x083B5F98 (22x4), the rest zeroed
* row tables: US 0x083941E0 / 0x08394288 / 0x083940B4 / 0x08394168 (14/11/15/10
  rows) <- JP 0x083874EC / 0x08387540 / 0x08387438 / 0x08387498 (7/6/8/7 rows).
  The US loader takes the tile step as the absolute tile, the JP loader sums it,
  so the JP steps are written as running sums.  JP's 32x32 frame is byte-equal to
  US 0x083940AA; its 16x32 frame (the half-width ！) has no US twin and is
  written into the spare tail of the first table at US 0x08394114.  Unused rows
  are zeroed.  Nothing but the four loader pools points into the tables.
* loaders   : row count (u8[0x0200F49C]), left group (u8[0x0200F498]), x base
  (u8[0x0200F4A0]), copy-loop bound, OBJ DMA 0xC00 -> 0x780 halfwords, BG DMA
  0x780 -> 0x580, tilemap destination +0x200 -> +0x208, source pitch 60 -> 44,
  width 30 -> 22 - JP's values (JP writes the same globals at 0x0200F46C..).
* draw loops: US 0x08090A64 `cmp r0,#0xd` -> #6 and US 0x080917E0 `cmp r7,#0xe`
  -> #7 (JP 0x08088260 / 0x08088FF4).

Every byte written is checked first against the pristine US ROM and the layer
input, and every byte copied comes from the pristine JP ROM.  Checked on a copy
of the previous layer's ROM: all four banners render the JP artwork, centred, with the US slide-in motion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gc
import ffta_jp_coverage_audit as coverage
import ffta_jp_to_win_header as prev

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/victory_banner"
OUTROM = ROOT / "rom/build/ffta_us_jp_victory_banner.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_victory_banner_repeat.gba"

# Output of ffta_jp_to_win_header, the previous layer.
BASELINE = "5DD2AA3F912F7BBA64722374730D324D8E9CD010967B18317579AF9D43D00FA9"
# This layer's output.
EXPECTED_PRODUCTION = "06FCC177557B3873252BB16272CAF4C92236981EE89C2AC1CA22415698F66F5B"

ROM = 0x08000000
# (US offset, US room, JP offset, JP length)
CONTAINERS = [(0x3BF308, 0x12E0, 0x3B1D84, 0x102A),
              (0x3C05E8, 0xBD4, 0x3B2DB0, 0xB07)]
TILEMAP_US, TILEMAP_US_LEN, TILEMAP_JP, TILEMAP_JP_LEN = 0x3C3864, 240, 0x3B5F98, 176
TABLES_LO, TABLES_HI = 0x3940B4, 0x39430C
FRAME32_US, FRAME32_JP = 0x083940AA, 0x08387426
FRAME16_JP, FRAME16_US = 0x0838742E, 0x08394114
FRAME32 = bytes.fromhex("0100000000800000")
FRAME16 = bytes.fromhex("0100008000800000")
# loader (US ROM), US table, US rows, US left group, JP table, JP rows, JP x base, JP left group
LOADERS = [(0x08090A80, 0x083941E0, 14, 8, 0x083874EC, 7, 5, 4),
           (0x08090BBC, 0x08394288, 11, 6, 0x08387540, 6, 6, 3),
           (0x08090CF8, 0x083940B4, 15, 8, 0x08387438, 8, 4, 4),
           (0x08090E34, 0x08394168, 10, 6, 0x08387498, 7, 5, 3)]
# JP loaders (JP ROM): the same globals written with the values above
# (`movs r0,#rows` then `movs r0,#left`, `movs r0,#xbase`), loop `cmp r3,#rows-1`
JP_LOADERS = [0x0808827C, 0x080883C0, 0x08088500, 0x08088640]
DRAW = [(0x90A64, "0d28", "0628"), (0x917E0, "0e2f", "072f")]
DRAW_JP = [(0x88260, "0628"), (0x88FF4, "072f")]


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def loader_edits(raw):
    """(offset, old, new) for every loader / draw-loop immediate; patterns found in ``raw``."""
    edits = []
    for start, _ust, usn, usl, _jpt, jpn, xb, left in LOADERS:
        s = start - ROM
        edits += [(s + 0x1C, bytes.fromhex("0022"), bytes([xb, 0x22])),
                  (s + 0x1E, bytes([usn, 0x20]), bytes([jpn, 0x20])),
                  (s + 0x24, bytes([usl, 0x20]), bytes([left, 0x20])),
                  (s + 0x56, bytes([usn - 1, 0x2B]), bytes([jpn - 1, 0x2B])),
                  (s + 0x7E, bytes.fromhex("c0231b01"), bytes.fromhex("f023db00")),
                  (s + 0x90, bytes.fromhex("f023db00"), bytes.fromhex("b023db00"))]
        for pat, new in (("80239b00", "8223"), ("3c23", "2c23"), ("1e23", "1623")):
            b = bytes.fromhex(pat)
            hits = [i for i in range(s + 0x94, s + 0x110, 2) if raw[i:i + len(b)] == b]
            if len(hits) != 1:
                raise RuntimeError(f"VB_LOADER_PATTERN 0x{start:08X} {pat} x{len(hits)}")
            edits.append((hits[0], bytes.fromhex(pat[:4]), bytes.fromhex(new)))
    edits += [(off, bytes.fromhex(old), bytes.fromhex(new)) for off, old, new in DRAW]
    return edits


def check_retail(raw, label):
    for uso, room, _jo, _jl in CONTAINERS:
        if raw[uso:uso + room] != US_RAW[uso:uso + room]:
            raise RuntimeError(f"VB_CONTAINER_NOT_RETAIL {label} 0x{uso:X}")
    if raw[TILEMAP_US:TILEMAP_US + TILEMAP_US_LEN] != US_RAW[TILEMAP_US:TILEMAP_US + TILEMAP_US_LEN]:
        raise RuntimeError(f"VB_TILEMAP_NOT_RETAIL {label}")
    if raw[TABLES_LO:TABLES_HI] != US_RAW[TABLES_LO:TABLES_HI]:
        raise RuntimeError(f"VB_TABLES_NOT_RETAIL {label}")
    if raw[FRAME32_US - ROM:FRAME32_US - ROM + 8] != FRAME32:
        raise RuntimeError(f"VB_FRAME32 {label}")
    for off, old, _new in loader_edits(US_RAW):
        if bytes(raw[off:off + len(old)]) != old:
            raise RuntimeError(f"VB_LOADER_BYTES {label} 0x{off:X} {bytes(raw[off:off + len(old)]).hex()}")


def check_us_pointers():
    """Only the four loader pools may point into the row tables."""
    hits = []
    for i in range(0, len(US_RAW) - 3, 4):
        w = struct.unpack_from("<I", US_RAW, i)[0]
        if ROM + TABLES_LO <= w < ROM + TABLES_HI:
            hits.append(w)
    if sorted(hits) != sorted(t for _l, t, *_r in LOADERS):
        raise RuntimeError(f"VB_TABLE_POINTERS {[hex(h) for h in hits]}")


def check_jp(jp):
    for _uso, _room, jo, jl in CONTAINERS:
        count, total, offs = gc.container(jp, jo)
        block = jo + total
        _mode, _shift, _size, dic, _dlen = gc.codec_a_descriptor(jp, block)
        if dic + gc.container_dictionary_high_water(jp, jo) - jo != jl:
            raise RuntimeError(f"VB_JP_CONTAINER_EXTENT 0x{jo:X}")
    if jp[FRAME32_JP - ROM:FRAME32_JP - ROM + 8] != FRAME32 or \
            jp[FRAME16_JP - ROM:FRAME16_JP - ROM + 8] != FRAME16:
        raise RuntimeError("VB_JP_FRAMES")
    for start, (_l, _ust, _usn, _usl, jpt, jpn, xb, left) in zip(JP_LOADERS, LOADERS):
        body = jp[start - ROM:start - ROM + 0x60]
        movs = {(body[i], body[i + 1] & 0xF8) for i in range(0, len(body) - 1, 2)}
        for imm in (jpn, left, xb):                         # `movs rN,#imm` (0x20 | N)
            if (imm, 0x20) not in movs:
                raise RuntimeError(f"VB_JP_LOADER 0x{start:08X} movs #{imm}")
        if bytes([jpn - 1, 0x2B]) not in body:              # `cmp r3,#rows-1`
            raise RuntimeError(f"VB_JP_LOADER 0x{start:08X} cmp #{jpn - 1}")
        if struct.pack("<I", jpt) not in jp[start - ROM:start - ROM + 0x140]:
            raise RuntimeError(f"VB_JP_TABLE_POOL 0x{start:08X}")
    for off, want in DRAW_JP:
        if jp[off:off + 2] != bytes.fromhex(want):
            raise RuntimeError(f"VB_JP_DRAW 0x{off:X}")


def tables(jp):
    out = {}
    for _l, ust, usn, _usl, jpt, jpn, _xb, _left in LOADERS:
        rows, tile = [], 0
        for i in range(jpn):
            fp, step, pal, x, z = struct.unpack_from("<IhHHH", jp, jpt - ROM + 12 * i)
            usfp = {FRAME32_JP: FRAME32_US, FRAME16_JP: FRAME16_US}[fp]
            rows.append(struct.pack("<IhHHH", usfp, tile, pal, x, z))
            tile += step
        out[ust - ROM] = b"".join(rows) + bytes(12 * (usn - jpn))
    return out


US_RAW = US.read_bytes()


def build():
    base = prev.build()[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"VB_BASELINE_MISMATCH {sha(base)}")
    jp = JP.read_bytes()
    check_retail(US_RAW, "us_retail")
    check_retail(base, "layer_input")
    check_us_pointers()
    check_jp(jp)
    raw = bytearray(base)
    for uso, room, jo, jl in CONTAINERS:
        raw[uso:uso + room] = jp[jo:jo + jl] + bytes(room - jl)
    raw[TILEMAP_US:TILEMAP_US + TILEMAP_US_LEN] = (jp[TILEMAP_JP:TILEMAP_JP + TILEMAP_JP_LEN]
                                                   + bytes(TILEMAP_US_LEN - TILEMAP_JP_LEN))
    for off, blob in tables(jp).items():
        raw[off:off + len(blob)] = blob
    f16 = FRAME16_US - ROM
    if any(raw[f16:f16 + 8]):
        raise RuntimeError("VB_FRAME16_SLOT_NOT_FREE")
    raw[f16:f16 + 8] = FRAME16
    for off, _old, new in loader_edits(base):
        raw[off:off + len(new)] = new
    return bytes(raw), base, {
        "containers": [[f"US 0x{ROM + u:08X}", f"JP 0x{ROM + j:08X}", f"0x{n:X}"] for u, _r, j, n in CONTAINERS],
        "tilemap": [f"US 0x{ROM + TILEMAP_US:08X}", f"JP 0x{ROM + TILEMAP_JP:08X}"],
        "tables": [[f"US 0x{t:08X}", f"JP 0x{j:08X}", n] for _l, t, _u, _ul, j, n, _x, _le in LOADERS],
        "frame16_us": f"0x{FRAME16_US:08X}",
        "loaders_us": [f"0x{l[0]:08X}" for l in LOADERS]}


def changed_ranges(a, b, block=4096):
    """Maximal [start, end) runs where ``a`` and ``b`` differ."""
    out = []
    for lo in range(0, len(a), block):
        if a[lo:lo + block] == b[lo:lo + block]:
            continue
        for i in range(lo, min(lo + block, len(a))):
            if a[i] != b[i]:
                if out and out[-1][1] == i:
                    out[-1] = (out[-1][0], i + 1)
                else:
                    out.append((i, i + 1))
    return out


def allowed():
    rngs = [(u, u + r) for u, r, _j, _n in CONTAINERS]
    rngs += [(TILEMAP_US, TILEMAP_US + TILEMAP_US_LEN), (TABLES_LO, TABLES_HI)]
    rngs += [(off, off + len(new)) for off, _o, new in loader_edits(US_RAW)]
    return rngs


def validate(product, base):
    if len(product) != len(base):
        raise RuntimeError("VB_ROM_SIZE_CHANGED")
    changed = changed_ranges(base, product)
    ok = allowed()
    outside = [(a, b) for a, b in changed if not any(lo <= a and b <= hi for lo, hi in ok)]
    if outside:
        raise RuntimeError(f"VB_BINARY_TOUCH {[(hex(a), hex(b)) for a, b in outside][:4]}")
    return {"changed_ranges": [[f"0x{a:08X}", f"0x{b:08X}"] for a, b in changed],
            "bytes_changed": sum(b - a for a, b in changed),
            "outside_allowed": 0, "result": "PASS"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260919_candidate")
    ap.add_argument("--print-sha", action="store_true")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return 0
    second = build()
    if sha(first[0]) != sha(second[0]):
        raise RuntimeError("VB_BUILD_NONDETERMINISTIC")
    product, base, meta = first
    audit = validate(product, base)
    if sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANDIDATE_MISMATCH {sha(product)}")
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("CANDIDATE_ROM_REREAD_FAILED")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "milestone": "JP battle-end banner クエストクリア！ / バトルクリア！ / クエスト失敗！ / バトル失敗！",
        "baseline_sha256": sha(base), "candidate_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True},
        "patch": meta, "audit": audit}
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
