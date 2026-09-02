#! python3
"""Battle MENU: JP retail's own geometry, ported in the descriptor template.

The defect this layer replaces
------------------------------

The battle MENU (``いどう`` / ``アクション`` / ``たいき`` / ``ステータス``) is drawn
8 cells wide with 6 text columns on the pristine US layout, and ``アクション``
(56 px) and ``ステータス`` (50 px) lose their last glyph under the right
border.  RC9 (``ffta_jp_battle_window_floor``, **retracted**) widened it by
raising the column count to 7 inside the *common box function*
``US 0x08017528``, keyed to the battle's right-anchored x cell 0x17.

**That was a release-blocking defect.**  The column count is not only a
drawing parameter: the window's owner allocates the list tilemap buffer
*before* the box function runs, and sizes it from its own column count
(``US 0x08013B50`` → ``columns × 2 × (items + 1)`` halfwords, malloc'd at
``US 0x08027FF4``).  Raising the count inside the box function left the
buffer at the old size, so the list tilemap writer ``US 0x080176C4``
(called from ``0x08017AE3``) ran 10 halfwords past its end and destroyed the
next heap block header.  In the opening snowball fight this is reproducible
at 100%: open the MENU, choose ``アクション``, and the ACTION window's own
context — the block that would have been allocated right there — never
comes back; the battle is left with no command window at all, plus visible
BG corruption.  Evidence:
``build/rc10_snowball_crash/20260901_run`` (``wp_ovf``: a write of ``0xC240``
to ``0x0202DED0`` from ``pc=0x080176C4`` with ``r10 = 7``; ``bi_*``: the
layer-by-layer bisection; ``v_t_rev``: reverting the RC9 site alone restores
the ACTION window).

Root cause of the *original* clipping, and the JP-retail fix
------------------------------------------------------------

The battle window geometry is not computed at all: it is a **20-byte
descriptor template**, one per window kind, copied by ``US 0x08027DA0``
(``memcpy`` at ``0x08027DBE``, ``r2 = 0x14``) from the table
``US 0x08391454`` into the battle UI context at ``ctx + 0x80``.  Byte +0 is
the x cell, +2 the **text column count**, +3 the item count.  For the MENU
(table index 1, ``US 0x08391468``) the US ships x = 0x15, columns = 7; the
4-item variant then narrows it by one at ``US 0x08027EB8..0x08027EC8``
(``columns -= 1``, ``x += 1``) to x = 0x16 / 6 columns.  ``US 0x08013B50``
turns those into cells (``columns + 2``) and sizes the buffer from them, so
everything downstream — the malloc, the layout-init ``US 0x08017490``, the
box function and the renderer — follows the template.

**JP retail keeps the same code and the same table with different numbers**
(``JP 0x083849AC``: x = 0x14, columns = 8; the other 18 bytes of the record
are byte-identical to the US one).  Narrowed for 4 items that is x = 0x15 /
7 columns / 9 cells — exactly the MENU JP retail draws.

The fix -- 2 data bytes, 0 executable bytes
-------------------------------------------

====================  ==============================  =============================
US address            pristine                        this layer
====================  ==============================  =============================
``0x08391468``        ``15``  (x cell)                ``14``
``0x0839146A``        ``07``  (text columns)          ``08``
====================  ==============================  =============================

After this layer the US record is byte-identical to JP retail's, which the
build re-verifies.  Because the change is in the descriptor the whole chain
is consistent: the buffer is malloc'd 140 bytes instead of 120, the heap
block that follows it moves, and nothing overruns.  Runtime on the
candidate: MENU 9 cells / 7 text columns with ``アクション`` and
``ステータス`` complete, ACTION window 8 columns with ``雪玉を投げる``
complete, the snowball throw selectable and executable, the battle advances.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_mission_panel_leaf as prev
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/battle_menu_geometry"
OUTROM = ROOT / "rom/build/ffta_us_jp_battle_menu_geometry.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_battle_menu_geometry_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_mission_panel_leaf (RC8).
BASELINE = "3F3F05C2E6FA7519EB2033EB4B4DEF30A8DF2D51E46829D7668B9395C926ADD5"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "A0715F8ACD4E4A66F79BC86A31344FBDDFE9A71D2D01194073206F32365C1F38"

US_TABLE = 0x00391454              # battle window descriptor templates, 20 bytes each
JP_TABLE = 0x00384998              # JP retail's counterpart
ENTRY = 1                          # the battle MENU
ENTRY_SIZE = 20
US_ENTRY = US_TABLE + ENTRY * ENTRY_SIZE          # US 0x08391468
JP_ENTRY = JP_TABLE + ENTRY * ENTRY_SIZE          # JP 0x083849AC
US_PRISTINE_ENTRY = bytes.fromhex("15060705050040010200050103000000 02000500")
JP_ENTRY_BYTES = bytes.fromhex("14060805050040010200050103000000 02000500")
PATCH_OFFSETS = (0, 2)             # x cell, text column count

# The RC9 hook this layer replaces must not be present in the baseline.
RC9_SITE = 0x00017558
RC9_SITE_PRISTINE = bytes.fromhex("0006000E")
RC9_STUB = 0x0036D370
RC9_STUB_LEN = 0x10

TEMPLATE_CONSUMERS = (0x08027E24, 0x0802829C)     # the only aligned words naming the table


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def words_naming(img: bytes, lo: int, hi: int):
    """Aligned ROM words whose value lands in [lo, hi)."""
    hits = []
    for off in range(0, len(img) & ~3, 4):
        v = int.from_bytes(img[off:off + 4], "little")
        if ROM + lo <= v < ROM + hi:
            hits.append(ROM + off)
    return hits


def jp_gate(jp_raw: bytes):
    """Re-verify JP retail's record on every build; never trust the docstring."""
    entry = jp_raw[JP_ENTRY:JP_ENTRY + ENTRY_SIZE]
    if entry != JP_ENTRY_BYTES:
        raise RuntimeError(f"BMG_JP_TEMPLATE_DRIFT {entry.hex()}")
    # the two records may differ only in the two bytes this layer ports
    diff = [i for i in range(ENTRY_SIZE) if entry[i] != US_PRISTINE_ENTRY[i]]
    if diff != list(PATCH_OFFSETS):
        raise RuntimeError(f"BMG_US_JP_DIFF_SHAPE {diff}")


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"BMG_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    jp_gate(JP.read_bytes())
    if base[US_ENTRY:US_ENTRY + ENTRY_SIZE] != US_PRISTINE_ENTRY \
            or us_raw[US_ENTRY:US_ENTRY + ENTRY_SIZE] != US_PRISTINE_ENTRY:
        raise RuntimeError("BMG_US_TEMPLATE_DRIFT")
    # the table is named by exactly its two literal-pool words, and nothing
    # names the interior of the record this layer edits
    if words_naming(base, US_TABLE, US_TABLE + 1) != list(TEMPLATE_CONSUMERS):
        raise RuntimeError("BMG_TABLE_CONSUMERS")
    if words_naming(base, US_ENTRY, US_ENTRY + ENTRY_SIZE):
        raise RuntimeError("BMG_ENTRY_REFERENCED")
    # RC9's box-function hook is retracted: it must be absent from the baseline
    if base[RC9_SITE:RC9_SITE + 4] != RC9_SITE_PRISTINE:
        raise RuntimeError("BMG_RC9_SITE_PRESENT")
    if set(base[RC9_STUB:RC9_STUB + RC9_STUB_LEN]) != {0xFF}:
        raise RuntimeError("BMG_RC9_STUB_PRESENT")
    raw = bytearray(base)
    for i in PATCH_OFFSETS:
        raw[US_ENTRY + i] = JP_ENTRY_BYTES[i]
    if bytes(raw[US_ENTRY:US_ENTRY + ENTRY_SIZE]) != JP_ENTRY_BYTES:
        raise RuntimeError("BMG_ENTRY_NOT_JP")
    meta = {
        "table": f"0x{ROM + US_TABLE:08X}", "entry": ENTRY,
        "entry_address": f"0x{ROM + US_ENTRY:08X}",
        "jp_entry_address": f"0x{ROM + JP_ENTRY:08X}",
        "pristine": US_PRISTINE_ENTRY.hex(), "patched": JP_ENTRY_BYTES.hex(),
        "bytes": {f"0x{ROM + US_ENTRY + i:08X}": f"{US_PRISTINE_ENTRY[i]:02X} -> {JP_ENTRY_BYTES[i]:02X}"
                  for i in PATCH_OFFSETS},
        "rule": "battle MENU descriptor template: x 0x15 -> 0x14, text columns 7 -> 8 "
                "(JP retail); the 4-item variant narrows both by one to x 0x15 / 7 columns / 9 cells",
    }
    return bytes(raw), base, meta, previous[3], previous[4]


def validate(product, base, meta, block_start, block_end):
    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = {US_ENTRY + i for i in PATCH_OFFSETS}
    if diff != expected:
        raise RuntimeError(f"BMG_UNEXPLAINED_BYTES {sorted(hex(d) for d in diff ^ expected)[:8]}")
    jp_raw = JP.read_bytes()
    if product[US_ENTRY:US_ENTRY + ENTRY_SIZE] != jp_raw[JP_ENTRY:JP_ENTRY + ENTRY_SIZE]:
        raise RuntimeError("BMG_NOT_JP_IDENTICAL")
    # no executable byte moved anywhere in the ROM
    us_raw = US.read_bytes()
    if product[RC9_SITE:RC9_SITE + 4] != RC9_SITE_PRISTINE \
            or product[RC9_SITE:RC9_SITE + 4] != us_raw[RC9_SITE:RC9_SITE + 4]:
        raise RuntimeError("BMG_BOX_FUNCTION_TOUCHED")
    if set(product[RC9_STUB:RC9_STUB + RC9_STUB_LEN]) != {0xFF}:
        raise RuntimeError("BMG_PADDING_TOUCHED")
    return {
        "patch": meta,
        "binary_touch": {"changed_ranges": 1, "executable_bytes_changed": 0,
                         "data_bytes_changed": len(PATCH_OFFSETS),
                         "literal_words_repointed": 0, "code_patches": 0, "relocated_bytes": 0},
        "us_record_equals_jp_retail": True,
        "rc9_box_function_hook_retracted": True,
        "earlier_milestones_unchanged": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260901_production")
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
        raise RuntimeError("BMG_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "battle-menu-geometry",
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
