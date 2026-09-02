#! python3
"""Battle ACTION window: room for ``雪玉を投げる`` in the snowball fight.

The defect
----------

In the opening snowball fight the ACTION window shows its one command,
``雪玉を投げる`` (JP ``words:battle/13``), cut off after ``雪玉を投(`` --
measured on the shipped RC6 image in mGBA
(``build/rc7_gameplay_audit/20260831_run/snow_step1``).  JP retail draws the
same command in a window two cells wider (10 cells, 8 text columns).

Root cause -- a US-only minimum width, not a text or font problem
-----------------------------------------------------------------

``US 0x080261D8`` lays out the battle ACTION window.  It initialises the
text width to a **minimum of 6 columns** (``movs r0, #6 / mov sl, r0`` at
``0x080261EC``) and then only *measures* the unit's ability-set names
(``words:battle`` through the set table ``US 0x08527244``, measurer
``0x080161BC`` = pixel width of the JP tokens rounded up to 8-pixel cells);
the fixed commands (``たたかう`` / item / the snowball throw) are never
measured because every English fixed command fits in 6 cells.  The window is
right-anchored (``x = 28 - width``), so a too-small width clips the text on
the right.  ``雪玉を投げる`` measures 64 px = 8 columns with the game's own
advance table (``US 0x084966B0``; identical values in the JP table), so the
shipped text and font are right and the *minimum* is what is wrong.  The JP
build lays these menus out in a different code shape (three per-group
functions with their own measurement), so there is no constant to port
verbatim.

The snowball branch is already separate in the US code: ``0x080c9540(2)``
is non-zero in the snowball fight, and in that branch the function stores
the throw command (item ``0xD``) instead of the normal fight command
(``0xC``).  That branch also carries three dead instructions --
``ldrb r0,[r4,#2] / subs r0,#4 / strb r0,[r4,#2]`` at ``0x08026238`` --
which shrink the descriptor's width field, a value the function
unconditionally overwrites with the measured width at ``0x08026548`` before
anything reads it.

The fix -- 6 bytes of executable code, inside the snowball branch only
----------------------------------------------------------------------

====================  ==============================  =============================
US address            pristine                        this layer
====================  ==============================  =============================
``0x08026238``        ``78A0  ldrb r0, [r4, #2]``     ``2008  movs r0, #8``
``0x0802623A``        ``3804  subs r0, #4``           ``4682  mov  sl, r0``
``0x0802623C``        ``70A0  strb r0, [r4, #2]``     ``46C0  nop``
====================  ==============================  =============================

In the snowball fight the minimum text width becomes 8 columns -- exactly
the measured width of ``雪玉を投げる`` and exactly JP retail's window.
Normal battles never execute these halfwords, so their ACTION window is
byte-for-byte the US layout (minimum 6, measured ability-set names).  The
function has exactly one caller (checked on every build), no other byte of
it changes, and the replaced instructions were dead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_banner_maps as prev
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/action_window"
OUTROM = ROOT / "rom/build/ffta_us_jp_action_window.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_action_window_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_banner_maps.
BASELINE = "5C2190610186B1F879C26DBDDCBCD2719756905EB8BB41244DA9B9A6A468D54A"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "4D4D8A265A8DCBCB723BC34AAEBF8A86C9B59B4864379BB30297A1ED9C739673"

FUNC_START = 0x080261D8            # battle ACTION window layout
FUNC_END = 0x08026580
EXPECTED_CALLER = 0x08027F96       # the single BL into the function (checked in plan())
MIN_WIDTH_INIT = 0x080261EC        # movs r0, #6 / mov sl, r0
PATCH_ADDR = 0x08026238
PRISTINE = bytes.fromhex("a078" "0438" "a070")      # ldrb r0,[r4,#2]; subs r0,#4; strb r0,[r4,#2]
PATCHED = bytes.fromhex("0820" "8246" "c046")       # movs r0,#8; mov sl,r0; nop
SNOWBALL_MIN_COLUMNS = 8
FINALIZE_WIDTH_STORE = 0x08026548  # strb r0, [r4, #2] with r0 = measured max
SNOWBALL_CHECK = 0x0802622C        # movs r0,#2 / bl 0x080c9540


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def bl_targets(data: bytes, lo: int, hi: int):
    """Thumb BL call sites in [lo, hi) as (site, target)."""
    out = []
    for off in range(lo - ROM, hi - ROM, 2):
        hi_hw = int.from_bytes(data[off:off + 2], "little")
        if hi_hw & 0xF800 != 0xF000:
            continue
        lo_hw = int.from_bytes(data[off + 2:off + 4], "little")
        if lo_hw & 0xF800 != 0xF800:
            continue
        imm = ((hi_hw & 0x7FF) << 12) | ((lo_hw & 0x7FF) << 1)
        if imm & 0x400000:
            imm -= 0x800000
        out.append((ROM + off, ROM + off + 4 + imm))
    return out


def callers(data: bytes, target: int):
    return [s for s, t in bl_targets(data, ROM, ROM + len(data)) if t == target]


def plan(us_raw: bytes):
    # the function is entered from exactly one call site
    sites = callers(us_raw, FUNC_START)
    if sites != [EXPECTED_CALLER]:
        raise RuntimeError(f"AW_CALLER_SET {[hex(s) for s in sites]}")
    # the halfwords we replace are what we think they are
    if us_raw[PATCH_ADDR - ROM:PATCH_ADDR - ROM + 6] != PRISTINE:
        raise RuntimeError("AW_PRISTINE_MISMATCH")
    # the minimum-width init and the finalize store are where the analysis put them
    if us_raw[MIN_WIDTH_INIT - ROM:MIN_WIDTH_INIT - ROM + 4] != bytes.fromhex("0620" "8246"):
        raise RuntimeError("AW_MIN_INIT_MISMATCH")
    if struct.unpack_from("<H", us_raw, FINALIZE_WIDTH_STORE - ROM)[0] != 0x70A0:
        raise RuntimeError("AW_FINALIZE_MISMATCH")
    if us_raw[SNOWBALL_CHECK - ROM:SNOWBALL_CHECK - ROM + 2] != bytes.fromhex("0220"):
        raise RuntimeError("AW_SNOWBALL_CHECK_MISMATCH")
    # nothing else in the ROM branches into the patched halfwords: no Thumb
    # conditional/unconditional branch in the function targets 0x0802623A/3C
    for off in range(FUNC_START - ROM, FUNC_END - ROM, 2):
        hw = struct.unpack_from("<H", us_raw, off)[0]
        tgt = None
        if hw & 0xF000 == 0xD000 and (hw & 0x0F00) != 0x0F00:
            tgt = ROM + off + 4 + (struct.unpack("<b", bytes([hw & 0xFF]))[0] * 2)
        elif hw & 0xF800 == 0xE000:
            imm = hw & 0x7FF
            if imm & 0x400:
                imm -= 0x800
            tgt = ROM + off + 4 + imm * 2
        if tgt in (PATCH_ADDR + 2, PATCH_ADDR + 4):
            raise RuntimeError(f"AW_BRANCH_INTO_PATCH 0x{ROM + off:08X}")
    return {"caller": f"0x{sites[0]:08X}"}


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"AW_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]
    us_raw = US.read_bytes()
    info = plan(us_raw)
    raw = bytearray(base)
    if raw[PATCH_ADDR - ROM:PATCH_ADDR - ROM + 6] != PRISTINE:
        raise RuntimeError("AW_BASE_DRIFT")
    # the whole function is pristine in the baseline
    if raw[FUNC_START - ROM:FUNC_END - ROM] != us_raw[FUNC_START - ROM:FUNC_END - ROM]:
        raise RuntimeError("AW_FUNCTION_DRIFT")
    raw[PATCH_ADDR - ROM:PATCH_ADDR - ROM + 6] = PATCHED
    meta = {"patch": {"address": f"0x{PATCH_ADDR:08X}", "pristine": PRISTINE.hex(),
                      "patched": PATCHED.hex(), "bytes": 6,
                      "snowball_min_columns": SNOWBALL_MIN_COLUMNS},
            "function": {"start": f"0x{FUNC_START:08X}", "end": f"0x{FUNC_END:08X}", **info}}
    # this layer relocates nothing: the block is empty, at the previous end
    return bytes(raw), base, meta, prev_block_end, prev_block_end


def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    if product[PATCH_ADDR - ROM:PATCH_ADDR - ROM + 6] != PATCHED:
        raise RuntimeError("AW_NOT_PATCHED")
    diff = [i for i in range(len(base)) if product[i] != base[i]]
    if diff != list(range(PATCH_ADDR - ROM, PATCH_ADDR - ROM + 6)):
        raise RuntimeError(f"AW_UNEXPLAINED_BYTES {len(diff)}")
    # the rest of the function is pristine US
    f = product[FUNC_START - ROM:FUNC_END - ROM]
    g = bytearray(us_raw[FUNC_START - ROM:FUNC_END - ROM])
    g[PATCH_ADDR - FUNC_START:PATCH_ADDR - FUNC_START + 6] = PATCHED
    if f != bytes(g):
        raise RuntimeError("AW_FUNCTION_TOUCHED_ELSEWHERE")
    return {
        "patch": meta["patch"], "function": meta["function"],
        "binary_touch": {"changed_ranges": 1, "executable_bytes_changed": 6,
                         "halfwords_replaced": 3, "code_patches": 1,
                         "relocated_bytes": 0},
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
        raise RuntimeError("AW_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "action-window",
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True},
        "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
