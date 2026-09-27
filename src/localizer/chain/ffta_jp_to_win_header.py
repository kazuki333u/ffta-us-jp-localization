#! python3
"""The battle-start header reads 勝利条件 / 課外授業 as in JP.

What the player sees
--------------------

When a battle starts, a small OBJ header slides in above the objective banner.
US draws ``TO WIN :`` (and ``TEAM ACTIVITY :`` for the variant); JP draws
``勝利条件`` (and ``課外授業``).  An earlier build still showed the English header.

Why
-------------------------

The old inventory said "JP retail is English too"; it matched only the English
sheet and never looked at JP's own.  The header is one OBJ sheet, loaded by the
same routine shape in both versions and placed by a small OAM frame table:

* sheet  : US 0x083BEF08 (``TO WIN :`` 0x180 + ``TEAM ACTIVITY :`` 0x280)
           JP 0x083B1984 (``勝利条件`` 0x200 + ``課外授業`` 0x200) - the same 0x400
* loader : US 0x0802B8FA sets the length 0x180 (the variant starts after it) and
           US 0x0802B90C the variant length 0x280; JP 0x0802B434 copies 0x200 and
           starts the variant at +0x200
* frames : US 0x08394088 (32x16 + 16x16) and US 0x08394096 (32x16 + 32x16 + 16x16);
           JP 0x08387418 (32x16 + 32x16) for both

This layer replaces the sheet with JP's (same size, nothing after it moves),
changes the two loader immediates to JP's copy size / step, and writes the JP
frame over the first 14 bytes of both US frames (the second frame's third
entry is no longer read).  Every byte it writes is checked first against the
pristine US ROM and the layer input, and every byte it copies comes from the
pristine JP ROM.  Checked on a copy of the previous layer's ROM: the header reads
勝利条件, the slide-in behaves as before, and the settled header does not overlap the banner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_fixed_starting_names as prev

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/to_win_header"
OUTROM = ROOT / "rom/build/ffta_us_jp_to_win_header.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_to_win_header_repeat.gba"

# Output of ffta_jp_fixed_starting_names, the previous layer.
BASELINE = "E905AF2E3A4D926341D4713BF23DCC2F85E508B1F41969EF8F03FF680AE1C8CE"
# This layer's output.
EXPECTED_PRODUCTION = "5DD2AA3F912F7BBA64722374730D324D8E9CD010967B18317579AF9D43D00FA9"

SHEET_US, SHEET_JP, SHEET_LEN = 0x3BEF08, 0x3B1984, 0x400
# (US ROM offset, retail bytes, JP bytes) - both are Thumb `movs r5,#imm` (+ `lsls`)
LOADER = [(0x2B8FA, bytes.fromhex("c0256d00"), bytes.fromhex("8025ad00")),  # 0x180 -> 0x200
          (0x2B90C, bytes.fromhex("a025"), bytes.fromhex("8025"))]          # variant step
# JP's routine (JP 0x0802B434): variant step `movs r0,#0x80; lsls r0,#2` (0x200)
# and copy `movs r2,#0x80` - the 0x200 / 0x200 split the sheet is cut for.
LOADER_JP = [(0x2B452, bytes.fromhex("80208000")), (0x2B45C, bytes.fromhex("8022"))]
FRAMES_US = 0x394088
FRAMES_US_RETAIL = bytes.fromhex("0200f84000802203f80020402a03"
                                 "0300f84000802203f84020802a03f80040403203")
FRAMES_JP = 0x387418
FRAME_JP = bytes.fromhex("0200f84000802203f84020802a03")


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_retail(raw, label):
    if raw[SHEET_US:SHEET_US + SHEET_LEN] != US_RAW[SHEET_US:SHEET_US + SHEET_LEN]:
        raise RuntimeError(f"TWH_SHEET_NOT_RETAIL {label}")
    for off, old, _new in LOADER:
        if bytes(raw[off:off + len(old)]) != old:
            raise RuntimeError(f"TWH_LOADER_BYTES {label} 0x{off:X} {bytes(raw[off:off + len(old)]).hex()}")
    if bytes(raw[FRAMES_US:FRAMES_US + len(FRAMES_US_RETAIL)]) != FRAMES_US_RETAIL:
        raise RuntimeError(f"TWH_FRAMES_NOT_RETAIL {label}")


def check_jp(jp):
    for off, new in LOADER_JP:
        if jp[off:off + len(new)] != new:
            raise RuntimeError(f"TWH_JP_LOADER 0x{off:X}")
    if jp[FRAMES_JP:FRAMES_JP + len(FRAME_JP)] != FRAME_JP:
        raise RuntimeError("TWH_JP_FRAME")


US_RAW = US.read_bytes()


def build():
    base = prev.build()[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"TWH_BASELINE_MISMATCH {sha(base)}")
    jp = JP.read_bytes()
    check_retail(US_RAW, "us_retail")
    check_retail(base, "layer_input")
    check_jp(jp)
    raw = bytearray(base)
    raw[SHEET_US:SHEET_US + SHEET_LEN] = jp[SHEET_JP:SHEET_JP + SHEET_LEN]
    for off, _old, new in LOADER:
        raw[off:off + len(new)] = new
    raw[FRAMES_US:FRAMES_US + len(FRAME_JP)] = FRAME_JP
    raw[FRAMES_US + 14:FRAMES_US + 14 + len(FRAME_JP)] = FRAME_JP
    return bytes(raw), base, {"sheet_us_rom": f"0x{SHEET_US:08X}", "sheet_jp_rom": f"0x{SHEET_JP:08X}",
                              "sheet_len": SHEET_LEN,
                              "loader_us_rom": [f"0x{off:08X}" for off, _o, _n in LOADER],
                              "frames_us_rom": [f"0x{FRAMES_US:08X}", f"0x{FRAMES_US + 14:08X}"],
                              "frame_jp_rom": f"0x{FRAMES_JP:08X}"}


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


ALLOWED = [(SHEET_US, SHEET_US + SHEET_LEN), (FRAMES_US, FRAMES_US + 28)] + \
          [(off, off + len(new)) for off, _o, new in LOADER]


def validate(product, base):
    if len(product) != len(base):
        raise RuntimeError("TWH_ROM_SIZE_CHANGED")
    changed = changed_ranges(base, product)
    outside = [(a, b) for a, b in changed if not any(lo <= a and b <= hi for lo, hi in ALLOWED)]
    if outside:
        raise RuntimeError(f"TWH_BINARY_TOUCH {[(hex(a), hex(b)) for a, b in outside][:4]}")
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
        raise RuntimeError("TWH_BUILD_NONDETERMINISTIC")
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
        "milestone": "JP battle-start header 勝利条件 / 課外授業",
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
