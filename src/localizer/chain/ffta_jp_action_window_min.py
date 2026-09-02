#! python3
"""Battle ACTION window: a 7-column minimum for every battle.

The defect
----------

RC7 (``ffta_jp_action_window``) raised the ACTION window's minimum text
width to 8 columns **inside the snowball branch only**, so ``雪玉を投げる``
fits.  Every other battle still runs the pristine minimum of **6 columns**
(``movs r0, #6 / mov sl, r0`` at ``US 0x080261EC``), and the normal fight
command ``たたかう`` (``words:battle/12``) measures 50 px = 7 columns with the
game's own advance table.  When the unit's ability-set name is short
(``戦技``, 22 px) nothing else widens the window, and the right edge of
``う`` disappears under the border -- reported by the user from play and
reproduced in the first Ivalice battle
(``build/rc7_diversity_audit/20260831_run/act_norm``: BG2 cells 22..29,
6 text columns).

The fix -- 2 bytes of executable code
-------------------------------------

====================  ==============================  =============================
US address            RC7                             this layer
====================  ==============================  =============================
``0x080261EC``        ``2006  movs r0, #6``           ``2007  movs r0, #7``
====================  ==============================  =============================

The general minimum becomes 7 columns: ``たたかう`` fits, and every measured
ability-set name still widens the window as before.  The snowball branch keeps
its own minimum of 8 (``movs r0, #8 / mov sl, r0 / nop`` at ``0x08026238``,
unchanged).  Verified on the patched image: normal ACTION window 7 text
columns with ``たたかう`` complete (``min7_norm``), snowball ACTION window still
8 columns (``min7_snow``).  The function has exactly one caller, and no other
byte of it changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_stale_literals as prev
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/action_window_min"
OUTROM = ROOT / "rom/build/ffta_us_jp_action_window_min.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_action_window_min_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_stale_literals (RC7).
BASELINE = "F654E8640F2E200C8C3ECD1819BDBD1D266C75E2C9CBB9FB2D440DC8F867FBF0"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "988CA7989A5386C2E3E20C9A088C9B86E19CA3142F0B4DFEDE6275E9084BCC73"

FUNC = 0x000261D8            # layout function (US 0x080261D8)
FUNC_END = 0x00026560        # exclusive; the finalize store sits at 0x08026548
MIN_INIT = 0x000261EC        # movs r0, #6 / mov sl, r0
PATCH = (MIN_INIT, b"\x06\x20", b"\x07\x20")
SNOWBALL_PATCH = (0x00026238, b"\x08\x20\x82\x46\xc0\x46")   # RC7's own bytes, must be present
CALLER = 0x00027F96


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bl_sites(img: bytes, target: int):
    sites = []
    for off in range(0, 0x400000, 2):
        hi = int.from_bytes(img[off:off + 2], "little")
        if hi & 0xF800 != 0xF000:
            continue
        lo = int.from_bytes(img[off + 2:off + 4], "little")
        if lo & 0xF800 != 0xF800:
            continue
        oh = hi & 0x7FF
        if oh & 0x400:
            oh -= 0x800
        t = (ROM + off + 4) + (oh << 12) + ((lo & 0x7FF) << 1)
        if (t & 0xFFFFFFFF) == target:
            sites.append(off)
    return sites


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"AWM_BASELINE_MISMATCH {sha(base)}")
    off, old, new = PATCH
    if base[off:off + 2] != old:
        raise RuntimeError("AWM_MIN_INIT_DRIFT")
    if base[off + 2:off + 4] != b"\x82\x46":           # mov sl, r0
        raise RuntimeError("AWM_MIN_INIT_SHAPE")
    so, sb = SNOWBALL_PATCH
    if base[so:so + len(sb)] != sb:
        raise RuntimeError("AWM_SNOWBALL_BRANCH_DRIFT")
    us_raw = US.read_bytes()
    if bl_sites(us_raw, ROM + FUNC) != [CALLER]:
        raise RuntimeError("AWM_CALLER_SET_CHANGED")
    raw = bytearray(base)
    raw[off:off + 2] = new
    meta = {"patch": {"offset": f"0x{off:06X}", "old": old.hex(), "new": new.hex(),
                      "meaning": "ACTION window minimum text width 6 -> 7 columns"}}
    return bytes(raw), base, meta, previous[3], previous[4]


def validate(product, base, meta, block_start, block_end):
    off, old, new = PATCH
    diff = [i for i in range(len(base)) if product[i] != base[i]]
    if diff != [off]:
        raise RuntimeError(f"AWM_UNEXPLAINED_BYTES {[hex(d) for d in diff][:8]}")
    if product[off:off + 2] != new:
        raise RuntimeError("AWM_NOT_PATCHED")
    # nothing else in the function changed relative to RC7
    if product[FUNC:off] != base[FUNC:off] or product[off + 2:FUNC_END] != base[off + 2:FUNC_END]:
        raise RuntimeError("AWM_FUNCTION_DRIFT")
    return {
        "patch": meta["patch"],
        "binary_touch": {"changed_ranges": 1, "executable_bytes_changed": 1,
                         "literal_words_repointed": 0, "code_patches": 1, "relocated_bytes": 0},
        "function": {"start": f"0x{ROM + FUNC:08X}", "callers": [f"0x{ROM + CALLER:08X}"],
                     "snowball_minimum": 8, "general_minimum": 7},
        "earlier_milestones_unchanged": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260831_production")
    ap.add_argument("--print-sha", action="store_true")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return
    second = build()
    product, base, meta, bs, be = first
    if sha(product) != sha(second[0]):
        raise RuntimeError("AWM_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "action-window-min",
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]), "identical": True},
        "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
