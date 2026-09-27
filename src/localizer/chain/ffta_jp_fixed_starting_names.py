#! python3
"""The four starting members keep the names JP gives them.

What the player sees
--------------------

The clan starts with Marche, Montblanc and four generated units (a soldier, a
white monk, a nu mou white mage and a viera archer).  JP retail names those
four the same way on every new game; US retail gives them random names.

Why
--------------------------------

Both versions draw the four names from ``words:name`` with the same generator
while the game boots, so the draw at boot is the same every time: indices
25 / 243 / 225 / 342.  US added one routine that runs when the player confirms
Marche's name (US 0x080CEA80, called from US 0x080CEAE6's caller at
US 0x0812A3B0): it re-seeds and calls the name generator US 0x080C9E00 again,
so US starting names are random.  JP has no such call.

This layer removes that one call -- the ``BL`` at US 0x080CEAE6 becomes two
Thumb ``NOP`` (``mov r8, r8``) -- and nothing else.  The random words the
routine records at +0x98 / +0x9C are left as they are.  Measured with
this patch:
after the name is confirmed the four units keep 25 / 243 / 225 / 342, whatever
the wait before confirming.  The readings of those indices are JP's own since
``ffta_jp_us_only_words_name`` transfers the whole JP table.

This is the one production layer that changes ROM code, so it checks the code
it changes before writing: the four bytes must be the retail ``BL`` and must
decode to US 0x080C9E00, in both the pristine US ROM and the layer input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_us_added_items as prev

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/fixed_starting_names"
OUTROM = ROOT / "rom/build/ffta_us_jp_fixed_starting_names.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_fixed_starting_names_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_us_added_items, the previous production layer.
BASELINE = "E4A28B6EBFCA3D2ADE48E7535552A186F00501E7E64DF8036C516A06A320B9BC"
# The canonical production SHA (terminal layer).
EXPECTED_PRODUCTION = "E905AF2E3A4D926341D4713BF23DCC2F85E508B1F41969EF8F03FF680AE1C8CE"

REDRAW_CALL = 0x0CEAE6                  # US ROM offset of the BL
RETAIL_BL = bytes.fromhex("fbf78bf9")   # BL US 0x080C9E00
NAME_GENERATOR = 0x080C9E00
THUMB_NOP2 = bytes.fromhex("c046c046")  # mov r8, r8 ; mov r8, r8


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bl_target(raw, offset):
    """Target of a Thumb BL pair at ROM ``offset`` (two halfwords)."""
    hi = int.from_bytes(raw[offset:offset + 2], "little")
    lo = int.from_bytes(raw[offset + 2:offset + 4], "little")
    if hi >> 11 != 0b11110 or lo >> 11 != 0b11111:
        return None
    upper = hi & 0x7FF
    if upper & 0x400:
        upper -= 0x800
    return ROM + offset + 4 + (upper << 12) + ((lo & 0x7FF) << 1)


def check_call_site(raw, label):
    if bytes(raw[REDRAW_CALL:REDRAW_CALL + 4]) != RETAIL_BL:
        raise RuntimeError(f"FSN_CALL_SITE_BYTES {label} "
                           f"{bytes(raw[REDRAW_CALL:REDRAW_CALL + 4]).hex()}")
    if bl_target(raw, REDRAW_CALL) != NAME_GENERATOR:
        raise RuntimeError(f"FSN_CALL_SITE_TARGET {label}")


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"FSN_BASELINE_MISMATCH {sha(base)}")
    check_call_site(US.read_bytes(), "us_retail")
    check_call_site(base, "layer_input")
    raw = bytearray(base)
    raw[REDRAW_CALL:REDRAW_CALL + 4] = THUMB_NOP2
    return bytes(raw), base, {"call_site_us_rom": f"0x{REDRAW_CALL:08X}"}


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


def validate(product, base):
    if len(product) != len(base):
        raise RuntimeError("FSN_ROM_SIZE_CHANGED")
    changed = changed_ranges(base, product)
    if changed != [(REDRAW_CALL, REDRAW_CALL + 4)]:
        raise RuntimeError(f"FSN_BINARY_TOUCH {[(hex(a), hex(b)) for a, b in changed][:4]}")
    return {"changed_ranges": [[f"0x{a:08X}", f"0x{b:08X}"] for a, b in changed],
            "bytes_before": RETAIL_BL.hex(), "bytes_after": THUMB_NOP2.hex(),
            "removed_call_target": f"0x{NAME_GENERATOR:08X}",
            "other_code_bytes_changed": 0, "result": "PASS"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260919_production")
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
        raise RuntimeError("FSN_BUILD_NONDETERMINISTIC")
    product, base, meta = first
    audit = validate(product, base)
    if sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "milestone": "JP fixed starting-member names",
        "baseline_sha256": sha(base), "production_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True},
        "patch": meta, "audit": audit}
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
