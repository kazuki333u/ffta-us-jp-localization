#! python3
"""The save-slot window reads CLEAR QUEST as in JP.

What the player sees
--------------------

START -> System -> Load (and the title's SAVED GAME) lists the save slots.  US
labels the cleared-mission count ``MISSION CLEARED``; JP labels it ``CLEAR QUEST``
(the same BG sheet also holds the slot window's other labels).  An earlier build still
showed the US words.

How the window is built
---------------------------------------------

Top-level mode 6 (US 0x08138EFD) and 7 (US 0x08139C71) call US 0x08137EC4, which
decodes the codec-B sheet named by the literal at US 0x08137FF4 (US 0x083BBA38,
52 tiles) and copies 0x340 halfwords to BG tile 0x177.  The labels are drawn by
US 0x08035678 as runs of consecutive tiles (0xD177 x5, 0xD181 x8, 0xD191 x10 ...);
JP (sheet JP 0x083AE344, 46 tiles, filler JP 0x08034564) makes the same calls
with the same constants.  US tiles 46..51 (0x1A5..0x1AA) are a US-only mark drawn
by US 0x0813882E when rec[+0x11] bit 1 is set (flag 0x5AE, all 300 story quests
cleared), so they are kept.

The port: JP tiles 0..45 + US tiles 46..51 (0x680 bytes), codec B, placed at the
first free block of the tail (0x3A8 bytes; the old room is 0x244), and the one
literal (US 0x08137FF4, the only aligned reference) repointed.  Code, copy
length and layout are unchanged.  Checked on a copy of the previous layer's ROM:
VRAM tile 0x177.. holds
the 46 JP tiles and the slot window shows CLEAR QUEST in place of MISSION CLEARED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gc
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_added_missions as uam
import ffta_jp_victory_banner as prev

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/save_slot_sheet"
OUTROM = ROOT / "rom/build/ffta_us_jp_save_slot_sheet.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_save_slot_sheet_repeat.gba"

# Output of ffta_jp_victory_banner, the previous layer.
BASELINE = "06FCC177557B3873252BB16272CAF4C92236981EE89C2AC1CA22415698F66F5B"
# This layer's output.
EXPECTED_PRODUCTION = "5E611FD2EB7CD7568ACDFAF3AEA28C425C6B60EF3EAB0587F2114CEBC56DB7DA"

ROM = 0x08000000
US_SHEET, JP_SHEET, LITERAL = 0x083BBA38, 0x083AE344, 0x08137FF4
US_LEN, JP_LEN = 0x680, 0x5C0


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sheet(us, jp, base):
    us_tiles = gc.decode_b(us, US_SHEET - ROM)[0]
    jp_tiles = gc.decode_b(jp, JP_SHEET - ROM)[0]
    if (len(us_tiles), len(jp_tiles)) != (US_LEN, JP_LEN):
        raise RuntimeError("SS_SHEET_SIZE")
    if gc.decode_b(base, US_SHEET - ROM)[0] != us_tiles:
        raise RuntimeError("SS_LAYER_INPUT_SHEET_CHANGED")
    data = jp_tiles + us_tiles[JP_LEN:]
    blob = gc.encode_b(data)
    if gc.decode_b(blob + b"\xff" * 16, 0)[0] != data:
        raise RuntimeError("SS_ENCODE_ROUND_TRIP")
    return blob


def build():
    base = prev.build()[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"SS_BASELINE_MISMATCH {sha(base)}")
    us, jp = US.read_bytes(), JP.read_bytes()
    blob = sheet(us, jp, base)
    if struct.unpack_from("<I", base, LITERAL - ROM)[0] != US_SHEET:
        raise RuntimeError("SS_LITERAL")
    refs = [ROM + o for o in range(0, len(base) - 3, 4)
            if struct.unpack_from("<I", base, o)[0] == US_SHEET]
    if refs != [LITERAL]:
        raise RuntimeError(f"SS_SHEET_REFERENCES {[hex(r) for r in refs]}")
    start = uam.free_block_start(base, stext.TAIL_START)
    end = start + len(blob)
    if end > uam.FREE_END:
        raise RuntimeError("SS_NO_ROOM")
    if set(base[start:end]) != {0xFF} or set(us[start:end]) != {0xFF}:
        raise RuntimeError("SS_DESTINATION_NOT_FREE")
    for o in range(0, len(base) - 3, 4):
        if ROM + start <= struct.unpack_from("<I", base, o)[0] < ROM + end:
            raise RuntimeError(f"SS_DESTINATION_REFERENCED 0x{ROM + o:08X}")
    raw = bytearray(base)
    raw[start:end] = blob
    struct.pack_into("<I", raw, LITERAL - ROM, ROM + start)
    return bytes(raw), base, {
        "sheet": [f"US 0x{US_SHEET:08X}", f"JP 0x{JP_SHEET:08X}", "JP tiles 0..45 + US tiles 46..51"],
        "placed": [f"0x{ROM + start:08X}", f"0x{len(blob):X}"],
        "literal": f"US 0x{LITERAL:08X}", "_allowed": [(start, end), (LITERAL - ROM, LITERAL - ROM + 4)]}


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


def validate(product, base, ok):
    if len(product) != len(base):
        raise RuntimeError("SS_ROM_SIZE_CHANGED")
    changed = changed_ranges(base, product)
    outside = [(a, b) for a, b in changed if not any(lo <= a and b <= hi for lo, hi in ok)]
    if outside:
        raise RuntimeError(f"SS_BINARY_TOUCH {[(hex(a), hex(b)) for a, b in outside][:4]}")
    return {"changed_ranges": [[f"0x{a:08X}", f"0x{b:08X}"] for a, b in changed],
            "bytes_changed": sum(b - a for a, b in changed),
            "outside_allowed": 0, "result": "PASS"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260920_candidate")
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
        raise RuntimeError("SS_BUILD_NONDETERMINISTIC")
    product, base, meta = first
    audit = validate(product, base, meta.pop("_allowed"))
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
        "milestone": "JP save-slot window label CLEAR QUEST",
        "baseline_sha256": sha(base), "candidate_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]), "identical": True},
        "patch": meta, "audit": audit}
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
