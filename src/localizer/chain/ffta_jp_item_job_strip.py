#! python3
"""RC23 - the item panel's equippable-job strip: the page-family the glyph
allocation never saw.

What the player sees
--------------------

Open any item's info panel (shop -> buy list -> ``R`` -> ``SELECT``) and page
the HELP window forward.  JP retail draws three pages; US retail draws two::

    page 1   JP  革と銅を組みあわせて作られた兜。
             US  Helm of copper and leather.
    page 2   JP  属性：なし　分類：兜 / 効果：なし
             US  Helmet                                  <- the same field,
                                                            inlined as English
    page 3   JP  ソパ－－…－ウ竜守－－－ / 神－－…－モ－－－
             US  (no such page)

**``Helmet`` is the item's category** (JP ``分類：兜``) - US retail wrote the
category, the hand count and the special effects into the record as literal
English and dropped JP retail's third page.  That third page is
``{04 <id>}``, one substitution token, and what it prints is a completely
different thing: **the strip of job initials that can equip the item**, one
column per job, ``－`` where the job cannot.  For a helmet that is Soldier,
Paladin, Warrior, Dragoon, Defender, Templar and Moogle Knight.

In the RC22 production ROM that page came out as::

    ソパ－－－－－－－－－－ウ竜類－－－
    ゆ－－－－－－－－－－－モ－－－

- ``竜`` is right, ``類`` should be ``守`` and ``ゆ`` should be ``神``.

Why
---

``{04 xx}`` with ``xx`` in ``0x90..0xAC`` is not an ordinary field: the value
function table at ``US 0x0836D768`` holds **0** there in *both* ROMs.  The
renderers test ``US 0x08013BEC`` first (``id in {0x13,0x55,0x56,0x80,0x87,
0x89}`` or ``0x90 <= id <= 0xAC``) and, for those, go to ``US 0x08013C20``,
which indexes **a 29-entry page table of its own** with ``id - 0x90``,
expands the entry into EWRAM ``0x02007AB0`` and hands the result to the text
renderer.  That table is::

    US 0x084EF3F4   ==   JP 0x084C6B4C      byte-identical, 29 entries

- one aligned word in the whole ROM points at it (``US 0x08013C84``), it is
**JP-original data that US retail left in place and stopped using**, and its
records are ordinary text records: a ``u16`` flag word plus a (compressed)
standard token stream, exactly the ``pages:`` shape ``c_ffta_sect_text_page``
parses.  ``ffta_sect``'s section map never listed it, so **no pass of this
project ever saw it** - not the coverage audit, not the QA glyph census, and,
fatally, **not the JP->US font-slot allocation**.

The allocation moves every JP full-width glyph this project transfers into a
free US font slot and rewrites the transferred records to the new slot numbers.
The 29 strip records were never rewritten, so they still name **JP** slots.
Sixteen of the 35 slots they name were handed to some other glyph by the
allocation, and those sixteen are what the player sees turn into nonsense::

    JP 0x0164 神 -> product 0x0216      JP 0x060A 守 -> product 0x0353
    JP 0x0166 時 -> product 0x0217      JP 0x060E 狩 -> product 0x0502
    JP 0x018A 動 -> product 0x0230      JP 0x063B 獣 -> product 0x0363
    JP 0x03B9 弓 -> product 0x04EB      JP 0x063E 銃 -> product 0x0504
    JP 0x03FB 曲 -> product 0x04ED      JP 0x0673 召 -> product 0x0370
    JP 0x0495 幻 -> product 0x02F8      JP 0x0731 精 -> product 0x0397
    JP 0x051E 黒 -> product 0x0318      JP 0x073B 青 -> product 0x039B
    JP 0x00FE －  -> product 0x01DC      JP 0x074B 赤 -> product 0x03A0

(the other nineteen kept a slot that still holds the same drawing, which is why
``ソ`` ``パ`` ``ウ`` ``竜`` ``モ`` survived and the rest did not).  ``－`` is in
the list for the same reason: the product's own ``fx_text`` uses ``0x01DC`` for
it 410 times, and ``0x00FE`` is still US retail's 7 px dash against JP retail's
10 px one.

The fix
-------

Put the 29 records through **the same allocation as every other transferred
record** and relocate the table, exactly the way the words/pages leaves are
relocated: ``ffta_jp_s_text_leaf_repoint.replacement_line`` maps every
``CHR_FULL`` and clears the compression bit, ``serialize_leaf`` lays the leaf
out, and the single literal-pool word is repointed.

* **0 executable bytes.**  One relocated leaf and one literal word.
* **No new text.**  Every token comes from pristine JP retail; only the font
  slot numbers change, and they change to the product's own numbers.
* **Family, not instance.**  All 29 categories are rewritten together - the
  361 ``fx_text/21`` item records that print them are all fixed at once.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_job_badges as prev
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_added_missions as uam
import ffta_qa_glyphs as glyphs
from ffta_sect import (c_ffta_sect_font, c_ffta_sect_rom,      # noqa: E402
                       _pages_sect_info, _trim_raw_len)

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/item_job_strip"
OUTROM = ROOT / "rom/build/ffta_us_jp_item_job_strip.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_item_job_strip_repeat.gba"

ROM = 0x08000000

# Output of ffta_jp_job_badges -- the RC22 production ROM.
BASELINE = "6A9A686F1D281AEF0B5F81A337EE6339C8B862EC7732A23329B08F9EF8969D3D"
# Terminal artifact of the production chain: this layer is now the last one.
EXPECTED_PRODUCTION = "6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6"

# The literal-pool word that holds the strip table's address, and the table.
US_STRIP_ROOT = 0x00013C84
JP_STRIP_ROOT = 0x00013BFC
US_STRIP_TABLE = 0x004EF3F4
JP_STRIP_TABLE = 0x004C6B4C
FIELD_FIRST = 0x90                  # {04 90} .. {04 AC}
FIELD_COUNT = 29

# The engine side, for the record and for the validation of "no code changed".
PREDICATE = 0x08013BEC              # is this field id one of the special ones
STRIP_READER = 0x08013C20           # id -> table entry -> EWRAM -> width
DRAW_SITES = (0x08013FBC, 0x08014210)
EWRAM_STAGING = 0x02007AB0          # where the entry is expanded
FX_ITEM_LEAF = 21                   # fx_text leaf holding the item help records

# Every job the strip has a column for, in the strip's own order.  This is
# ffta_jp_job_badges.JOB_LABELS with the race duplicates removed, and the build
# asserts that the de-duplicated sequence is exactly as long as the strip.
def _strip_jobs():
    seen, out = set(), []
    for job, us_label, jp_label in prev.JOB_LABELS:
        if len(jp_label) != 1 or jp_label in seen:
            continue
        seen.add(jp_label)
        out.append((jp_label, job, us_label))
    return tuple(out)


STRIP_JOBS = _strip_jobs()

# The 29 item categories, in field-id order.  The Japanese is JP retail's own
# `分類：` line from the same record's page 2; the English is US retail's own
# page-2 literal from `fx_text/21`.  Both are re-derived and re-checked from
# the two pristine ROMs by `census()`.
CATEGORIES = (
    ("ソード/片手", "Sword/1-hand"), ("ブレード/片手", "Blade/1-hand"),
    ("サーベル/片手", "Saber/1-hand"), ("騎士剣/片手", "Knightsword/1-hand"),
    ("大剣/両手", "Greatsword/2-hand"), ("広刃剣/両手", "Broadsword/2-hand"),
    ("ナイフ/片手", "Knife/1-hand"), ("レイピア/片手", "Rapier/1-hand"),
    ("刀/片手", "Katana/1-hand"), ("杖/片手", "Staff/1-hand"),
    ("ロッド/片手", "Rod/1-hand"), ("メイス/片手", "Mace/1-hand"),
    ("弓/両手", "Bow/2-hand"), ("剛弓/両手", "Greatbow/2-hand"),
    ("槍/片手", "Spear/1-hand"), ("楽器/片手", "Instrument/1-hand"),
    ("ナックル/片手", "Knuckles/1-hand"), ("ソウル/片手", "Soul/1-hand"),
    ("銃/片手", "Gun/1-hand"), ("盾", "Shield"), ("兜", "Helmet"),
    ("リボン", "Ribbon"), ("帽子", "Hat"), ("鎧", "Armor"),
    ("服", "Clothing"), ("ローブ", "Robe"), ("靴", "Shoes"),
    ("小手", "Armlets"), ("アクセサリ", "Accessory"),
)


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


# ------------------------------------------------------------------ parse ---

def strip_table(raw: bytes, root: int):
    """The strip table as the page family it is: u16 offsets -> text records."""
    return c_ffta_sect_rom(bytes(raw), 0).setup(
        _pages_sect_info({"equipjobs": root}),
        _trim_raw_len(bytes(raw), 0xF00000)).tabs["pages"]["equipjobs"]


def table_span(raw: bytes, table: int, leaf) -> int:
    """Bytes the table occupies, offsets plus every record, 4-aligned."""
    end = FIELD_COUNT * 2
    for index in range(leaf.tsize):
        line = leaf[index]
        end = max(end, line.real_offset - table + line.raw_len)
    return stext.align(end, 4)


def tokens_of(line):
    return list(line.text.tokens)


def font_bitmaps(raw: bytes, kind: str):
    """The font of an in-memory image, glyph slot -> bitmap."""
    root, info = glyphs.FONT[kind]
    rom = c_ffta_sect_rom(bytes(raw), 0).setup(
        {"font": (root, c_ffta_sect_font, dict(info))},
        _trim_raw_len(bytes(raw), 0xF00000))
    return glyphs.bitmaps(rom.tabs["font"], info["size"])


def line_widths(tokens, raw: bytes, metadata: int):
    """Rendered width per line: glyph advances, {1B n} tightens, {1D n} widens."""
    widths = [0]
    for kind, value in tokens:
        if kind == "CHR_FULL":
            widths[-1] += raw[metadata + value]
        elif kind == "CHR_HALF":
            raise ValueError("IJS_CHR_HALF_IN_STRIP")
        elif kind == "CTR_FUNC":
            if value >> 8 == 0x1B:
                widths[-1] -= value & 0xFF
            elif value >> 8 == 0x1D:
                widths[-1] += value & 0xFF
            elif value == 0x4D:
                widths.append(0)
    return widths


# ------------------------------------------------------------------- build ---

def build():
    product_prev, _base_prev, meta_prev = prev.build()
    base = product_prev
    if sha(base) != BASELINE:
        raise RuntimeError(f"IJS_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    raw = bytearray(base)

    us_leaf = strip_table(us_raw, US_STRIP_ROOT)
    jp_leaf = strip_table(jp_raw, JP_STRIP_ROOT)
    base_leaf = strip_table(base, US_STRIP_ROOT)
    for name, leaf in (("US", us_leaf), ("JP", jp_leaf), ("BASE", base_leaf)):
        if leaf.tsize != FIELD_COUNT:
            raise RuntimeError(f"IJS_TABLE_SIZE_{name} {leaf.tsize}")

    span = table_span(us_raw, US_STRIP_TABLE, us_leaf)
    if span != table_span(jp_raw, JP_STRIP_TABLE, jp_leaf):
        raise RuntimeError("IJS_SPAN_DIFFERS")
    # The data is JP-original: US retail shipped the JP table unchanged and
    # simply stopped referencing it.  That is what makes a verbatim transfer
    # with nothing but a slot remap the correct fix.
    if us_raw[US_STRIP_TABLE:US_STRIP_TABLE + span] != \
            jp_raw[JP_STRIP_TABLE:JP_STRIP_TABLE + span]:
        raise RuntimeError("IJS_US_JP_TABLE_DIFFERS")
    # ... and no earlier layer of the chain has touched it.
    if base[US_STRIP_TABLE:US_STRIP_TABLE + span] != \
            us_raw[US_STRIP_TABLE:US_STRIP_TABLE + span]:
        raise RuntimeError("IJS_BASELINE_TABLE_ALREADY_MODIFIED")

    # The reference set is a singleton in every ROM involved.
    for name, blob, want in (("US", us_raw, ROM + US_STRIP_TABLE),
                             ("BASE", base, ROM + US_STRIP_TABLE),
                             ("JP", jp_raw, ROM + JP_STRIP_TABLE)):
        found = [i for i in range(0, len(blob) - 3, 4) if u32(blob, i) == want]
        expect = [JP_STRIP_ROOT] if name == "JP" else [US_STRIP_ROOT]
        if found != expect:
            raise RuntimeError("IJS_REFERENCE_SET_%s %s"
                               % (name, [hex(f) for f in found]))

    allocation, _meta = uam.glyph_allocation()
    replacements, remaps = {}, {}
    for index in range(FIELD_COUNT):
        tokens = tokens_of(jp_leaf[index])
        if tokens != tokens_of(us_leaf[index]) or tokens != tokens_of(base_leaf[index]):
            raise RuntimeError(f"IJS_TOKENS_DIFFER {index}")
        for kind, value in tokens:
            if kind == "CHR_HALF":
                raise RuntimeError(f"IJS_CHR_HALF {index}")
            if kind == "CHR_FULL":
                if value not in allocation:
                    raise RuntimeError("IJS_GLYPH_NOT_ALLOCATED 0x%04X" % value)
                remaps[value] = allocation[value]
        replacements[index] = stext.replacement_line(jp_leaf[index], allocation)
    if not remaps:
        raise RuntimeError("IJS_NO_GLYPHS")

    blob = stext.serialize_leaf(us_raw, us_leaf, replacements)

    cursor = stext.align(int(meta_prev["block"]["end"], 16), 4)
    if set(base[cursor:cursor + len(blob)]) != {0xFF} or \
            set(us_raw[cursor:cursor + len(blob)]) != {0xFF}:
        raise RuntimeError("IJS_DESTINATION_NOT_FREE")
    coincidental = []
    for off in range(0, len(base) - 3, 4):
        value = u32(base, off)
        if not (ROM + cursor <= value < ROM + cursor + len(blob)):
            continue
        # A real reference to a page table is 4-aligned (the loader loads it as
        # a word); anything else is a text payload that happens to read as an
        # address, and this build changes none of those bytes.
        if value % 4 == 0:
            raise RuntimeError("IJS_DESTINATION_REFERENCED 0x%08X -> 0x%08X"
                               % (ROM + off, value))
        coincidental.append((ROM + off, value))

    raw[cursor:cursor + len(blob)] = blob
    struct.pack_into("<I", raw, US_STRIP_ROOT, ROM + cursor)
    block_end = cursor + len(blob)

    if len(raw) != len(base):
        raise RuntimeError("IJS_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= cursor
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("IJS_BLOCK_OUTSIDE_TAIL")

    meta = {
        "table": {
            "us_rom_address": f"0x{ROM + US_STRIP_TABLE:08X}",
            "jp_rom_address": f"0x{ROM + JP_STRIP_TABLE:08X}",
            "new_rom_address": f"0x{ROM + cursor:08X}",
            "original_bytes": span,
            "new_bytes": len(blob),
            "entries": FIELD_COUNT,
            "field_ids": "0x%02X..0x%02X" % (FIELD_FIRST,
                                             FIELD_FIRST + FIELD_COUNT - 1),
            "repointed_literals": [f"0x{ROM + US_STRIP_ROOT:08X}"],
            "reader": f"0x{STRIP_READER:08X}",
        },
        "block": {"start": f"0x{cursor:06X}", "end": f"0x{block_end:06X}"},
        "glyph_remap": {"0x%04X" % k: "0x%04X" % v for k, v in sorted(remaps.items())},
        "glyphs_remapped": len(remaps),
        "glyphs_moved": sum(1 for k, v in remaps.items() if k != v),
        "unaligned_lookalike_words": [f"0x{a:08X}=0x{v:08X}" for a, v in coincidental],
    }
    return bytes(raw), base, meta


# ---------------------------------------------------------------- validate ---

def validate(product: bytes, base: bytes, meta: dict):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    start = int(meta["block"]["start"], 16)
    end = int(meta["block"]["end"], 16)

    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = set(range(start, end)) | set(range(US_STRIP_ROOT, US_STRIP_ROOT + 4))
    if diff - expected:
        raise RuntimeError("IJS_UNEXPLAINED_BYTES %s"
                           % sorted(hex(d) for d in diff - expected)[:8])
    if u32(product, US_STRIP_ROOT) != ROM + start:
        raise RuntimeError("IJS_LITERAL_NOT_REPOINTED")

    allocation, _meta = uam.glyph_allocation()
    jp_leaf = strip_table(jp_raw, JP_STRIP_ROOT)
    new_leaf = strip_table(product, US_STRIP_ROOT)
    if new_leaf.tsize != FIELD_COUNT:
        raise RuntimeError("IJS_RELOCATED_TABLE_SIZE %d" % new_leaf.tsize)

    jp_font = glyphs.bitmaps(glyphs.font_of(str(JP), "jp"), 0xC66)
    pr_font = glyphs.bitmaps(glyphs.font_of_product(product), 0xC67) \
        if hasattr(glyphs, "font_of_product") else None
    if pr_font is None:
        tmp = RUN_BASE / "_font_probe.gba"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(product)
        pr_font = glyphs.bitmaps(glyphs.font_of(str(tmp), "us"), 0xC67)
        tmp.unlink()

    checked = 0
    for index in range(FIELD_COUNT):
        want = [(kind, allocation[value]) if kind == "CHR_FULL" else (kind, value)
                for kind, value in tokens_of(jp_leaf[index])]
        got = tokens_of(new_leaf[index])
        if got != want:
            raise RuntimeError(f"IJS_RELOCATED_TOKENS {index}")
        # Every glyph the relocated record names must draw, in the product's
        # own font, the pixels JP retail draws for the glyph it replaced, and
        # advance the pen by the same number of pixels.
        for (kind, new), (_k, old) in zip(got, tokens_of(jp_leaf[index])):
            if kind != "CHR_FULL":
                continue
            if pr_font.get(new) != jp_font.get(old):
                raise RuntimeError("IJS_GLYPH_PIXELS 0x%04X->0x%04X" % (old, new))
            if product[stext.US_METADATA + new] != jp_raw[stext.JP_METADATA + old]:
                raise RuntimeError("IJS_GLYPH_WIDTH 0x%04X->0x%04X" % (old, new))
            checked += 1
        # ... so the strip is laid out at JP retail's own widths.
        if line_widths(got, product, stext.US_METADATA) != \
                line_widths(tokens_of(jp_leaf[index]), jp_raw, stext.JP_METADATA):
            raise RuntimeError(f"IJS_LINE_WIDTH {index}")

    widest = max(max(line_widths(tokens_of(new_leaf[i]), product, stext.US_METADATA))
                 for i in range(FIELD_COUNT))

    # The pristine table is still in the image and nothing points at it.
    span = int(meta["table"]["original_bytes"])
    if product[US_STRIP_TABLE:US_STRIP_TABLE + span] != \
            us_raw[US_STRIP_TABLE:US_STRIP_TABLE + span]:
        raise RuntimeError("IJS_ORIGINAL_TABLE_TOUCHED")
    stale = [i for i in range(0, len(product) - 3, 4)
             if u32(product, i) == ROM + US_STRIP_TABLE]
    if stale:
        raise RuntimeError("IJS_STALE_REFERENCE %s" % [hex(s) for s in stale])

    # No executable byte moved.  The repointed word is a literal-pool constant
    # in the same class as the `pages:` roots earlier layers already repoint
    # (0x013CC0, 0x013CD8, 0x013D98, 0x013DE0, 0x013E34, 0x013E98), so the
    # neighbourhood is compared with the baseline; the *instructions* are then
    # separately shown to be pristine US by excluding every such literal.
    literals = {US_STRIP_ROOT} | {a for a in (0x013CC0, 0x013CD8, 0x013D98,
                                              0x013DE0, 0x013E34, 0x013E98)}
    lo, hi = PREDICATE - ROM, DRAW_SITES[1] - ROM + 0x40
    for offset in range(lo, hi):
        if product[offset] == base[offset]:
            continue
        if offset & ~3 != US_STRIP_ROOT:
            raise RuntimeError("IJS_CODE_TOUCHED 0x%08X" % (ROM + offset))
    for offset in range(lo, hi):
        if product[offset] == us_raw[offset]:
            continue
        if (offset & ~3) not in literals:
            raise RuntimeError("IJS_CODE_DRIFT 0x%08X" % (ROM + offset))

    return {
        "patch": meta,
        "binary_touch": {
            "relocated_leaf_bytes": end - start,
            "literal_words_repointed": 1,
            "executable_bytes_changed": 0,
            "block": [f"0x{ROM + start:08X}", f"0x{ROM + end:08X}"],
        },
        "records": {
            "table_entries": FIELD_COUNT,
            "glyph_tokens_checked": checked,
            "glyph_slots_remapped": meta["glyphs_remapped"],
            "glyph_slots_that_moved": meta["glyphs_moved"],
            "widest_line_px": widest,
        },
        "runtime_contract": {
            "predicate": f"0x{PREDICATE:08X}",
            "reader": f"0x{STRIP_READER:08X}",
            "draw_sites": [f"0x{s:08X}" for s in DRAW_SITES],
            "index": "field id - 0x%02X" % FIELD_FIRST,
            "staging": f"0x{EWRAM_STAGING:08X}",
        },
    }


# ------------------------------------------------------------------ census ---

def _consumers():
    """fx_text/21 records per field id, from pristine JP and US retail."""
    from ffta_sect import c_ffta_sect_fixed_text
    def fx(path, root, leaves):
        raw = Path(path).read_bytes()
        return c_ffta_sect_rom(raw, 0).setup(
            {"fx_text": (root, c_ffta_sect_fixed_text,
                         c_ffta_sect_rom.ARG_SELF, leaves)},
            _trim_raw_len(raw, 0xF00000)).tabs["fx_text"]
    jp = fx(JP, 0x017F6C, 26)[FX_ITEM_LEAF]
    us = fx(US, 0x018050, 27)[FX_ITEM_LEAF]
    per = {i: [] for i in range(FIELD_COUNT)}
    for index in range(jp.tsize):
        try:
            tokens = tokens_of(jp[index])
        except Exception:                                        # noqa: BLE001
            continue
        for kind, value in tokens:
            if kind == "CTR_FUNC" and value >> 8 == 0x04 \
                    and FIELD_FIRST <= (value & 0xFF) < FIELD_FIRST + FIELD_COUNT:
                per[(value & 0xFF) - FIELD_FIRST].append(index)
    return per, jp, us


def census(path=HERE / "data/item_job_strip_census.csv"):
    """Every field id: its category, its jobs, and the records that print it."""
    jp_raw = JP.read_bytes()
    jp_leaf = strip_table(jp_raw, JP_STRIP_ROOT)
    per, _jp_fx, _us_fx = _consumers()
    full = [v for k, v in tokens_of(jp_leaf[FIELD_COUNT - 1]) if k == "CHR_FULL"]
    if len(full) != len(STRIP_JOBS) or len(set(full)) != len(full):
        raise RuntimeError("IJS_STRIP_COLUMN_COUNT %d/%d"
                           % (len(full), len(STRIP_JOBS)))
    # The blank column marker is simply the code no full strip ever uses and
    # every partial strip is full of.
    every = [v for i in range(FIELD_COUNT)
             for k, v in tokens_of(jp_leaf[i]) if k == "CHR_FULL"]
    dash = max(set(every) - set(full), key=every.count)
    product = OUTROM.read_bytes() if OUTROM.exists() else None
    new_leaf = strip_table(product, US_STRIP_ROOT) if product else None
    rows = []
    for index in range(FIELD_COUNT):
        codes = [v for k, v in tokens_of(jp_leaf[index]) if k == "CHR_FULL"]
        if len(codes) != len(STRIP_JOBS):
            raise RuntimeError("IJS_COLUMN_COUNT %d" % index)
        jobs = [STRIP_JOBS[i] for i, c in enumerate(codes) if c != dash]
        if any(codes[i] != full[i] for i, _c in enumerate(codes) if codes[i] != dash):
            raise RuntimeError("IJS_COLUMN_MISMATCH %d" % index)
        ja, en = CATEGORIES[index]
        rows.append({
            "field_id": "0x%02X" % (FIELD_FIRST + index),
            "table_entry": index,
            "category_jp": ja,
            "category_us_literal": en,
            "equippable_jobs": " ".join(j for j, _n, _u in jobs),
            "equippable_job_names": "/".join(n for _j, n, _u in jobs),
            "equippable_count": len(jobs),
            "fx_text_21_records": len(per[index]),
            "first_record": per[index][0] if per[index] else "",
            "jp_glyph_codes": " ".join("%04X" % c for c in codes),
            "product_glyph_codes": (" ".join(
                "%04X" % v for k, v in tokens_of(new_leaf[index])
                if k == "CHR_FULL") if new_leaf else ""),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fd:
        writer = csv.DictWriter(fd, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    total = sum(r["fx_text_21_records"] for r in rows)
    print("%s: %d categories, %d fx_text/%d records affected"
          % (path, len(rows), total, FX_ITEM_LEAF))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260902_production")
    ap.add_argument("--print-sha", action="store_true")
    ap.add_argument("--census", action="store_true")
    args = ap.parse_args()
    if args.census:
        census()
        return
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return
    second = build()
    product, base, meta = first
    if sha(product) != sha(second[0]):
        raise RuntimeError("IJS_BUILD_NONDETERMINISTIC")
    audits = validate(product, base, meta)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    summary = {
        "milestone": "item info equippable-job strip (RC23)",
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
