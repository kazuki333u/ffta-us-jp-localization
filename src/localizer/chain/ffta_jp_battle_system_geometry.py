#! python3
"""In-battle SYSTEM menu and the OPTIONS value column: JP retail's own numbers.

The defects (both reported from play on RC11)
---------------------------------------------

1. **The in-battle SYSTEM menu** (``START`` during a battle) clips two of its
   six items: ``アクティブターン`` shows as ``アクティブター`` and
   ``バトルから逃げる`` as ``バトルから逃げ``.  The window is 11 cells with
   **9 text columns** (72 px) and the two items measure 81 px and 83 px through
   the game's own advance table.  The items are ``words:battle`` 1..6 and are
   never measured -- the same "a fixed command the layout does not measure"
   class as RC7's ``雪玉を投げる`` and RC8's ``たたかう``.

2. **The OPTIONS screen** (world map ``システム`` -> ``オプション`` and the
   in-battle SYSTEM menu -> ``オプション``) clips the value of
   ``Lボタン割り当て``: ``アクティブターン`` (81 px) shows as ``アクティブタ``
   plus half a glyph, and ``ジャッジポイント取得表示`` runs into its ``ON``.

What JP retail does -- measured, not guessed
--------------------------------------------

*It widens the box for the SYSTEM menu and it moves the value column for the
OPTIONS row.  It never scrolls the text.*

* The battle window geometry is the 20-byte descriptor template table
  ``US 0x08391454`` / **``JP 0x08384998``** (RC11 named the MENU's record
  ``JP 0x083849AC``; that is record **1**, so the JP table base is 20 bytes
  lower).  With that base the two tables line up record for record and only
  four records differ.  Record **0** is the SYSTEM menu: US x ``0x13`` /
  9 columns, **JP x ``0x11`` / 11 columns** (a 13-cell box at 17..29).
* The OPTIONS screen's window template (``US 0x0836D4C4`` /
  ``JP 0x083614EC``) is **byte-identical** in both ROMs -- 22 text columns --
  so JP does not widen that window.  What differs is the per-option table
  right after it (``US 0x0836D4D4`` / ``JP 0x083614FC``, 20-byte records of
  ``u16 value count, u16 value column, u16 value word ids``): JP puts the
  ON/OFF-style values at column **17** where the US puts them at 16, and the
  ``L BUTTON`` row's values at column **11** where the US puts them at 13.
  Column 11 leaves 11 columns (88 px) for ``アクティブターン``.

The fix -- 11 data bytes, 0 executable bytes
--------------------------------------------

* Descriptor record 0: x ``0x13`` -> ``0x11`` (JP's own value) and text
  columns ``9`` -> **``0x0A``**.
* OPTIONS value column: records 0..7 ``16`` -> ``17``, record 8 ``13`` ->
  ``11`` (all JP retail's own values).

**Why 10 columns and not JP's 11.** Descriptor record 0 is shared: the same
record also serves the battle's turn-order LIST (``アクティブターン``, a
14-column list of the units in turn order).  With 11 columns the SYSTEM menu
itself is perfect, and so are the mission panel, the OPTIONS screen and the
law screen reached from it -- but the turn-order LIST then draws its frame and
**no text at all** and stops accepting input, on RC11 and on this layer alike
(``build/rc12_gameplay_audit/20260901_run``: ``bs_w_r0jp`` and ``bs_w_r0c``
against ``bsys_r11``; every EWRAM arena stays clean, so it is not the RC9
heap-overflow class).  Ten columns is the largest value at which the LIST is
still correct (``bs_c10``, ``bs_x10``).  With x ``0x11`` the box is 12 cells
at 18..29 and all ten columns are on screen (at the US x ``0x13`` the tenth
column falls off the right edge, which is why the column count alone changes
nothing).  ``アクティブターン`` (81 px) is then complete and
``バトルから逃げる`` (83 px) loses 3 px of its final ``る`` instead of the
whole glyph.  **Residual:** those 3 px, and the turn-order LIST's own limit,
which is not diagnosed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_mission_panel_templates as prev
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/battle_system_geometry"
OUTROM = ROOT / "rom/build/ffta_us_jp_battle_system_geometry.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_battle_system_geometry_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_mission_panel_templates.
BASELINE = "E019B74FBE7EC7C9F6759CABDFC3179162740108450F837BC0C3E59F29BACBED"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "9098F025FDDD00119E0FD833199F5E9BBD1F66DCBEAD865D60749C3C4E652532"

US_DESC = 0x00391454              # battle window descriptor templates, 20-byte records
JP_DESC = 0x00384998              # the same table in JP retail (record 1 = RC11's 0x083849AC)
DESC_RECORDS = 11
SYSTEM_RECORD = 0                 # the in-battle SYSTEM menu (and the turn-order LIST)
MENU_RECORD = 1                   # the battle MENU, ported by RC11
SYSTEM_X_JP = 0x11
SYSTEM_COLUMNS_JP = 0x0B
SYSTEM_COLUMNS_SHIPPED = 0x0A     # capped: 11 breaks the turn-order LIST (see the docstring)

US_OPT = 0x0036D4D4               # OPTIONS per-option records: u16 count, u16 value column, u16 words
JP_OPT = 0x003614FC
OPT_RECORDS = 9                   # record 9 is the terminator (count 0)
OPT_STRIDE = 20
US_OPT_TEMPLATE = 0x0036D4C4      # the OPTIONS window template (identical in both ROMs)
JP_OPT_TEMPLATE = 0x003614EC
OPT_TEMPLATE_LEN = 16

# What the census must find before anything is written.
EXPECTED_OPT_COLUMNS = {i: (16, 17) for i in range(8)}
EXPECTED_OPT_COLUMNS[8] = (13, 11)


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def desc(img, base, index):
    off = base + 20 * index
    return bytes(img[off:off + 20])


def jp_gate(jp_raw, base):
    """Re-derive the JP table's alignment instead of trusting the address."""
    # RC11 ported the MENU from JP record 1; after that layer the US record
    # equals the JP one byte for byte, which pins the base.
    if desc(base, US_DESC, MENU_RECORD) != desc(jp_raw, JP_DESC, MENU_RECORD):
        raise RuntimeError("BSG_JP_TABLE_ALIGNMENT")
    exact = sum(1 for i in range(3, DESC_RECORDS)
                if desc(base, US_DESC, i) == desc(jp_raw, JP_DESC, i))
    if exact < 6:
        raise RuntimeError(f"BSG_JP_TABLE_ALIGNMENT_WEAK {exact}")
    jp0 = desc(jp_raw, JP_DESC, SYSTEM_RECORD)
    if jp0[0] != SYSTEM_X_JP or jp0[2] != SYSTEM_COLUMNS_JP:
        raise RuntimeError(f"BSG_JP_SYSTEM_RECORD_DRIFT {jp0.hex()}")
    if jp_raw[JP_OPT_TEMPLATE:JP_OPT_TEMPLATE + OPT_TEMPLATE_LEN] != \
            base[US_OPT_TEMPLATE:US_OPT_TEMPLATE + OPT_TEMPLATE_LEN]:
        raise RuntimeError("BSG_OPT_TEMPLATE_DIVERGED")


def opt_census(us_img, jp_raw):
    """Per-option value column, US vs JP, and the value word ids they index."""
    rows = []
    for i in range(OPT_RECORDS):
        a = US_OPT + OPT_STRIDE * i
        b = JP_OPT + OPT_STRIDE * i
        ua, uc = u16(us_img, a), u16(us_img, a + 2)
        ja, jc = u16(jp_raw, b), u16(jp_raw, b + 2)
        if ua != ja:
            raise RuntimeError(f"BSG_OPT_VALUE_COUNT {i} {ua} != {ja}")
        want = EXPECTED_OPT_COLUMNS[i]
        if (uc, jc) != want:
            raise RuntimeError(f"BSG_OPT_COLUMN_DRIFT {i} ({uc},{jc}) != {want}")
        rows.append({"option": i, "values": ua, "us_column": uc, "jp_column": jc})
    if u16(us_img, US_OPT + OPT_STRIDE * OPT_RECORDS) != 0:
        raise RuntimeError("BSG_OPT_TERMINATOR")
    return rows


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"BSG_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    # nothing earlier in the chain has touched either table
    if base[US_DESC:US_DESC + 20 * DESC_RECORDS] != us_raw[US_DESC:US_DESC + 20 * DESC_RECORDS] and \
            desc(base, US_DESC, MENU_RECORD) == desc(us_raw, US_DESC, MENU_RECORD):
        raise RuntimeError("BSG_DESC_TABLE_UNEXPECTED")
    if base[US_OPT:US_OPT + OPT_STRIDE * (OPT_RECORDS + 1)] != \
            us_raw[US_OPT:US_OPT + OPT_STRIDE * (OPT_RECORDS + 1)]:
        raise RuntimeError("BSG_OPT_TABLE_MODIFIED_EARLIER")
    jp_gate(jp_raw, base)
    rows = opt_census(base, jp_raw)

    rec = desc(base, US_DESC, SYSTEM_RECORD)
    if rec != desc(us_raw, US_DESC, SYSTEM_RECORD):
        raise RuntimeError("BSG_SYSTEM_RECORD_MODIFIED_EARLIER")
    if rec[0] != 0x13 or rec[2] != 0x09:
        raise RuntimeError(f"BSG_SYSTEM_RECORD_PRISTINE {rec.hex()}")

    raw = bytearray(base)
    raw[US_DESC + 20 * SYSTEM_RECORD] = SYSTEM_X_JP
    raw[US_DESC + 20 * SYSTEM_RECORD + 2] = SYSTEM_COLUMNS_SHIPPED
    for row in rows:
        struct.pack_into("<H", raw, US_OPT + OPT_STRIDE * row["option"] + 2, row["jp_column"])

    meta = {
        "battle_descriptor": {
            "us_table": f"0x{ROM + US_DESC:08X}", "jp_table": f"0x{ROM + JP_DESC:08X}",
            "record": SYSTEM_RECORD,
            "x": f"0x13 -> 0x{SYSTEM_X_JP:02X} (JP retail)",
            "text_columns": f"9 -> {SYSTEM_COLUMNS_SHIPPED} (JP retail {SYSTEM_COLUMNS_JP}, capped: "
                            f"11 breaks the turn-order LIST that shares this record)",
        },
        "options_value_column": {
            "us_table": f"0x{ROM + US_OPT:08X}", "jp_table": f"0x{ROM + JP_OPT:08X}",
            "window_template_identical": True,
            "rows": rows,
        },
    }
    return bytes(raw), base, meta, previous[3], previous[4]


def validate(product, base, meta, block_start, block_end):
    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = {US_DESC + 20 * SYSTEM_RECORD, US_DESC + 20 * SYSTEM_RECORD + 2}
    for i in range(OPT_RECORDS):
        expected |= {US_OPT + OPT_STRIDE * i + 2, US_OPT + OPT_STRIDE * i + 3}
    if diff - expected:
        raise RuntimeError(f"BSG_UNEXPLAINED_BYTES {sorted(hex(d) for d in diff - expected)[:8]}")
    rec = desc(product, US_DESC, SYSTEM_RECORD)
    if rec[0] != SYSTEM_X_JP or rec[2] != SYSTEM_COLUMNS_SHIPPED:
        raise RuntimeError("BSG_SYSTEM_RECORD_NOT_WRITTEN")
    if desc(product, US_DESC, MENU_RECORD) != desc(base, US_DESC, MENU_RECORD):
        raise RuntimeError("BSG_MENU_RECORD_TOUCHED")   # RC11's own fix stays put
    for i in range(OPT_RECORDS):
        want = EXPECTED_OPT_COLUMNS[i][1]
        if u16(product, US_OPT + OPT_STRIDE * i + 2) != want:
            raise RuntimeError(f"BSG_OPT_NOT_WRITTEN {i}")
    return {
        "patch": meta,
        "binary_touch": {"changed_ranges": 1 + OPT_RECORDS, "executable_bytes_changed": 0,
                         "literal_words_repointed": 0, "code_patches": 0, "relocated_bytes": 0,
                         "data_bytes_changed": 2 + OPT_RECORDS},
        "known_residual": "battle SYSTEM menu: バトルから逃げる loses 3 px of its final glyph "
                          "(10 columns, not JP retail's 11 -- 11 breaks the turn-order LIST)",
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
        raise RuntimeError("BSG_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "battle-system-geometry",
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
