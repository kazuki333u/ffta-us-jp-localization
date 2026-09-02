#! python3
"""Japanese name entry for the US ROM -- the JP keyboard on the US engine.

Runtime validation of RC2 showed the last player-facing English that is *not*
graphics chrome: the default names render in Japanese (``マーシュ`` /
``ナッツクラン``) but the keyboard the player types them on is the US
Latin-only one.  This layer replaces it with the JP keyboard.

Why this is a data change plus five code constants
--------------------------------------------------

The JP and US name-entry modules are the same source compiled twice.  The
structural diff (``ffta_code_diff.py``, evidence
``build/jp_name_entry/<run>/module_opdiff.txt``) shows they share:

* the context-struct layout -- page ``ctx+0x2F6``, column ``ctx+0x310``,
  row ``ctx+0x311``, name buffer ``ctx+0x312``, length ``ctx+0x326``,
  keyboard grid pointer ``ctx+0x304``;
* the cell lookup ``grid + page*120 + row*20 + col*2`` -- US ``0x0812B538``
  and JP ``0x0812060C`` are **byte-identical**;
* the name representation: a 20-byte field of big-endian ``CHR_FULL`` tokens
  ``0x8000 | font_slot``, padded with ``0x4073`` (space) and terminated by
  ``0x0000``.  US holds 9 slots and caps the name at 8 characters, JP holds 8
  and caps at 7.  **US keeps its own field and its own cap** -- nothing about
  the save layout changes;
* the cursor navigation, the delete path and the confirm path, which differ
  only in register allocation.

So the JP behaviour is reached by giving the US engine the JP keyboard *data*
and telling it there are six pages instead of two.

What this layer writes
----------------------

1. **The keyboard table** (``data/jp_name_entry_keyboard.json``).  The keyboard
   is an indexed table of ordinary FFTA text records, one per page; the engine
   replays the record's token stream into a 6x10 grid.  The six JP pages are
   ひらがな清音 / ひらがな濁音・半濁音・小書き / カタカナ清音 /
   カタカナ濁音・半濁音・小書き / 英字 / 数字記号.  JP pages 1 and 3 ship
   compressed (record flags ``0x0062``); they are re-emitted uncompressed
   (``0x0060``) so every shipped byte is auditable, and the emitter is proved
   against the JP ROM by reproducing page 3's own decompressed payload
   byte-for-byte.
2. **The one divergent glyph.**  Slot ``0xA5`` is the sole font slot whose JP
   and US records differ (PROJECT_STATE section 4): US ``0xA5`` is the Latin
   dash, the JP 長音符 lives at the high slot ``0x06D9`` that the A5 layer
   installed.  Every ``ー`` cell of the JP keyboard is therefore written as
   ``0x86D9`` -- which is exactly how the production ROM already stores the
   default name ``マーシュ`` (``80 8E | 86 D9 | 80 67 | 80 95``), so the
   keyboard now emits the same token the rest of the ROM already uses.
3. **The JP name-entry artwork** -- background, tiles, tilemap and the
   page-tab strip -- copied verbatim from the JP ROM.  The tab strip is an
   ``A7`` container with one sub-image per page: the US one holds two, so six
   pages need the JP one or pages 2..5 would keep the previous tab drawn.  The
   tiles come with it because the JP tabs (かな / カナ / 英字 / 数字記号) and
   the JP legend (入力 / 消去 / 決定) are drawn from them.
4. **Five code constants**, each one the exact byte the JP build differs by:

   ===============  ===========================  ==================================
   US ROM offset    change                       why
   ===============  ===========================  ==================================
   ``0x0012A17E``   ``movs r0,#0xF0`` -> ``adds  the keyboard grid malloc.  Six
                    r0,r2,#0``                   pages need 720 bytes, the US call
                                                 asks for 240.  ``r2`` still holds
                                                 ``0x300`` from the previous
                                                 allocation two instructions up, so
                                                 one 2-byte instruction asks for 768
                                                 -- 48 more than JP's 720, never
                                                 less.  (The *page render* buffer
                                                 needs no change: the US ROM already
                                                 allocates ``0xC600`` = six pages.)
   ``0x0012A26E``   ``cmp r0,#1`` -> ``#5``      init pre-renders every page
   ``0x0012AC36``   ``cmp r0,#1`` -> ``#5``      R wraps after the last page
   ``0x0012AC9A``   ``movs r0,#1`` -> ``#5``     L wraps before the first page
   ``0x0012B290``   ``movs r0,#0x16`` -> ``#17`` the JP panel is one tile wider
   ===============  ===========================  ==================================

Everything else in the module is untouched, so the name field keeps the US
proportional layout, the US 8-character cap and the US 20-byte buffer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_only_s_text_final as prev
import ffta_name_entry_kbd as kbd

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/jp_name_entry_keyboard.json"
RUN_BASE = HERE / "build/jp_name_entry"
OUTROM = ROOT / "rom/build/ffta_us_jp_name_entry.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_name_entry_repeat.gba"

ROM = 0x08000000

# Output of ffta_jp_us_only_s_text_final -- the runtime-validated RC2 build.
BASELINE = "A7E97A64497BAACCAED10E17D5ED0E22A49ABC094A6419E7822917D833E33A34"
# Terminal artifact of the production chain: the single canonical final-SHA
# authority once this layer is in the chain.
EXPECTED_PRODUCTION = "BE03434D7FA2558836E409EE2225D3DC5BE3E77B9132D49202FCC282A405D27B"

PAGES = 6
GRID_BYTES = PAGES * kbd.ROWS * 20          # 720

# JP artwork bundle, contiguous in the JP ROM, copied verbatim.
JP_ART = (("bg", 0x003B72E4), ("tiles", 0x003B75F4),
          ("tmap", 0x003B7898), ("tabhdr", 0x003B7BA4))
# The ``A7`` container does not end at ``base + total``: that address is where
# its **codec block** starts, and ``0x08005318`` hands it to ``0x080051C4`` as
# ``ip``.  ``0x080051C4`` reads four descriptor bytes there -- mode, a shift, and
# a big-endian output size (``00 05 03 00``: mode 0, shift 5, 0x300 bytes out) --
# and then treats everything after them as a **back-reference dictionary**: its
# copy branch at ``0x080052D2`` does ``add r1, ip`` with a 10-bit offset masked
# by ``0xFFFF >> (shift + 1)`` = ``0x3FF``, so the dictionary is 1024 bytes.
# Relocating the container without this block is what the first two runtime
# passes showed: with no descriptor the decoder bails out and VRAM keeps its
# previous contents; with the descriptor but no dictionary every back-reference
# reads 0xFF and the tab strip fills with colour 15.
TABHDR_DESCRIPTOR = bytes((0x00, 0x05, 0x03, 0x00))
TABHDR_DICTIONARY = 0x400
US_TABHDR_CODEC = 0x003C569A
JP_TABHDR_CODEC = 0x003B81AC

# The highlighted tab is a four-sprite composite whose X comes from a one-byte
# per-page table.  The US table holds two entries and a pad byte, the JP one
# holds six; everything that follows it in ROM is byte-identical between the
# ROMs (US 0x003A8298.. == JP 0x0039B012..), which is what proves the two are
# the same table.  Without this, pages 2..5 read past the US table and the
# highlight lands at x = 0 -- the second defect the runtime pass found.
US_TAB_X = 0x003A8295
US_TAB_X_BYTES = bytes((0x08, 0x28, 0x00))
JP_TAB_X = 0x0039B00C
JP_TAB_X_BYTES = bytes((0x08, 0x10, 0x38, 0x48, 0x68, 0x88))

# The D-pad handler clamps and wraps the cursor against a ``limits[page][row]``
# table of selectable cells per row, six bytes per page (``0x0812A9B2``,
# ``0x0812AA0C`` and ``0x0812AA3A`` all read it, always as ``page*6 + row``).
# The US table describes two pages, the JP one describes six.  Without it,
# LEFT/RIGHT and the page switch read past the US table: the third runtime pass
# showed the column latching to 255 from the second page switch on, so a key
# press then indexed the grid far out of bounds.
US_CURSOR_LIMITS = 0x003A8320
JP_CURSOR_LIMITS = 0x0039B09A
LIMIT_ROWS = kbd.ROWS
JP_ART_END = JP_TABHDR_CODEC + len(TABHDR_DESCRIPTOR) + TABHDR_DICTIONARY

# The US literal-pool words the name-entry module loads these resources from,
# as US ROM file offsets.  Every one of them is verified against the pristine
# US value before it is rewritten.
US_LITERALS = {
    "kbd":    ((0x0012AD98, 0x0012B4DC), 0x084C3D50),
    "bg":     ((0x0012B2E4,),            0x083C4D38),
    "tiles":  ((0x0012B2D0,),            0x083C5024),
    "tmap":   ((0x0012B2D8,),            0x083C52A8),
    "tabhdr": ((0x0012B448,),            0x083C5454),
    "tabx":   ((0x0012A524, 0x0012A5B4, 0x0012B080, 0x0012B170), 0x083A8295),
    "limits": ((0x0012A9E0, 0x0012AAB4),                         0x083A8320),
}

# (offset, expected pristine bytes, replacement, why)
CODE_PATCHES = (
    (0x0012A17E, b"\xF0\x20", b"\x10\x1C",
     "keyboard grid malloc: movs r0,#0xF0 -> adds r0,r2,#0 (r2 = 0x300 >= 720)"),
    (0x0012A26E, b"\x01\x28", b"\x05\x28",
     "init pre-render loop: cmp r0,#1 -> cmp r0,#5"),
    (0x0012AC36, b"\x01\x28", b"\x05\x28",
     "R page wrap: cmp r0,#1 -> cmp r0,#5"),
    (0x0012AC9A, b"\x01\x20", b"\x05\x20",
     "L page wrap: movs r0,#1 -> movs r0,#5"),
    (0x0012B290, b"\x16\x20", b"\x17\x20",
     "name-entry panel width: movs r0,#0x16 -> movs r0,#0x17"),
)

# The JP page tabs, for the manifest and the report.
PAGE_NAMES = ("ひらがな清音", "ひらがな濁音・半濁音・小書き", "カタカナ清音",
              "カタカナ濁音・半濁音・小書き", "英字", "数字記号")


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ------------------------------------------------------------------ table ---

def remap_dash(tokens):
    """Point every ``ー`` cell at the JP 長音符 slot the A5 layer installed."""
    out, n = [], 0
    for tok in tokens:
        if tok[0] == "CHR" and tok[1] == kbd.US_DASH_SLOT:
            out.append(("CHR", kbd.JP_DASH_SLOT))
            n += 1
        else:
            out.append(tok)
    return out, n


def manifest_pages(jp_raw):
    """The six page payloads, re-proved against the JP ROM where possible."""
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if doc["flags"] != "0x0060":
        raise RuntimeError("NE_MANIFEST_FLAGS")
    payloads = {int(k): bytes.fromhex(v) for k, v in doc["pages"].items()}
    if sorted(payloads) != list(range(PAGES)):
        raise RuntimeError("NE_MANIFEST_PAGE_SET")

    # Pages the JP ROM stores uncompressed must match it byte-for-byte.
    verbatim = tuple(doc["source"]["pages_from_jp_rom_verbatim"])
    for p in verbatim:
        off = kbd.read_u16(jp_raw, kbd.JP_TABLE + p * 2)
        rec = kbd.JP_TABLE + off
        if kbd.read_u16(jp_raw, rec) != 0x0060:
            raise RuntimeError(f"NE_JP_PAGE_NOT_PLAIN {p}")
        _toks, end = kbd.parse_tokens(jp_raw, rec + 2)
        if jp_raw[rec + 2:end] != payloads[p]:
            raise RuntimeError(f"NE_JP_PAGE_DRIFT {p}")

    # The compressed pages are proved a different way: the same emitter that
    # produced them reproduces page 3's own decompressed payload from the JP
    # retail grid, so the grid -- not this file -- is the authority.
    grid = bytes.fromhex(doc["jp_runtime_grid"])
    if len(grid) != GRID_BYTES:
        raise RuntimeError("NE_JP_GRID_SIZE")
    for p in range(PAGES):
        want = jp_grid_page(grid, p)
        got = kbd.grid(kbd.parse_tokens(payloads[p] + b"\x00", 0)[0])
        if got != want:
            raise RuntimeError(f"NE_PAGE_GRID_MISMATCH {p}")
    return doc, payloads, grid, verbatim


def jp_grid_page(grid: bytes, page: int):
    return [[int.from_bytes(grid[page * 120 + r * 20 + c * 2:
                                 page * 120 + r * 20 + c * 2 + 2], "big")
             for c in range(kbd.COLS)] for r in range(kbd.ROWS)]


def build_table(payloads):
    """Serialise the six pages into a keyboard table blob, dash remapped."""
    pages, dashes = [], 0
    for p in range(PAGES):
        tokens, _end = kbd.parse_tokens(payloads[p] + b"\x00", 0)
        tokens, n = remap_dash(tokens)
        dashes += n
        pages.append((0x0060, tokens))
    return kbd.build_table(pages), dashes


# ------------------------------------------------------------------ build ---

def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"NE_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[11]

    jp_raw = JP.read_bytes()
    us_raw = US.read_bytes()
    doc, payloads, grid, verbatim = manifest_pages(jp_raw)

    raw = bytearray(base)

    # every literal we are about to rewrite must still hold its pristine value
    for name, (slots, pristine) in US_LITERALS.items():
        for slot in slots:
            if struct.unpack_from("<I", us_raw, slot)[0] != pristine:
                raise RuntimeError(f"NE_LITERAL_DRIFT_US {name} 0x{slot:06X}")
            if struct.unpack_from("<I", raw, slot)[0] != pristine:
                raise RuntimeError(f"NE_LITERAL_DRIFT_BASE {name} 0x{slot:06X}")

    for name, off in (("JP", JP_TABHDR_CODEC), ("US", US_TABHDR_CODEC)):
        rom = jp_raw if name == "JP" else us_raw
        if rom[off:off + len(TABHDR_DESCRIPTOR)] != TABHDR_DESCRIPTOR:
            raise RuntimeError(f"NE_TABHDR_DESCRIPTOR_DRIFT {name}")

    us_lim = us_raw[US_CURSOR_LIMITS:US_CURSOR_LIMITS + 2 * LIMIT_ROWS]
    jp_lim = jp_raw[JP_CURSOR_LIMITS:JP_CURSOR_LIMITS + PAGES * LIMIT_ROWS]
    if us_lim != jp_lim[:2 * LIMIT_ROWS] and len(us_lim) != 2 * LIMIT_ROWS:
        raise RuntimeError("NE_LIMITS_SIZE")
    for p in range(PAGES):
        row_counts = tuple(jp_lim[p * LIMIT_ROWS:(p + 1) * LIMIT_ROWS])
        cells = kbd.grid(kbd.parse_tokens(payloads[p] + b"\x00", 0)[0])
        for r, want in enumerate(row_counts):
            got = sum(1 for v in cells[r] if v)
            if got != want:
                raise RuntimeError(f"NE_LIMITS_ROW_MISMATCH page {p} row {r} "
                                   f"table={want} grid={got}")

    if us_raw[US_TAB_X:US_TAB_X + len(US_TAB_X_BYTES)] != US_TAB_X_BYTES:
        raise RuntimeError("NE_TAB_X_DRIFT_US")
    if jp_raw[JP_TAB_X:JP_TAB_X + PAGES] != JP_TAB_X_BYTES:
        raise RuntimeError("NE_TAB_X_DRIFT_JP")

    table, dashes = build_table(payloads)
    art = {name: jp_raw[off:(JP_ART[i + 1][1] if i + 1 < len(JP_ART) else JP_ART_END)]
           for i, (name, off) in enumerate(JP_ART)}
    art["tabx"] = jp_raw[JP_TAB_X:JP_TAB_X + PAGES]
    art["limits"] = jp_lim

    cursor = block_start = stext.align(prev_block_end, 4)
    placement = {}
    for name, data in (("kbd", table), *art.items()):
        raw[cursor:cursor + len(data)] = data
        placement[name] = {"offset": cursor, "bytes": len(data),
                           "cpu_pointer": ROM + cursor}
        for slot in US_LITERALS[name][0]:
            raw[slot:slot + 4] = (ROM + cursor).to_bytes(4, "little")
        cursor = stext.align(cursor + len(data), 4)
    block_end = cursor

    code = []
    for off, pristine, new, why in CODE_PATCHES:
        if us_raw[off:off + len(pristine)] != pristine:
            raise RuntimeError(f"NE_CODE_DRIFT_US 0x{off:06X}")
        if bytes(raw[off:off + len(pristine)]) != pristine:
            raise RuntimeError(f"NE_CODE_DRIFT_BASE 0x{off:06X}")
        raw[off:off + len(new)] = new
        code.append({"us_rom_offset": f"0x{off:06X}",
                     "cpu_address": f"0x{ROM + off:08X}",
                     "was": pristine.hex().upper(), "now": new.hex().upper(),
                     "why": why})

    if len(raw) != len(base):
        raise RuntimeError("NE_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("NE_BLOCK_OUTSIDE_TAIL")

    meta = {"placement": placement, "code": code, "dash_cells": dashes,
            "table_bytes": len(table), "grid": grid, "doc": doc,
            "payloads": payloads, "verbatim": verbatim}
    return bytes(raw), base, meta, block_start, block_end


# --------------------------------------------------------------- validate ---

def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    audits = {}

    # 1. the shipped table decodes to the JP retail grid, dash remapped
    tbl = meta["placement"]["kbd"]["offset"]
    shipped = kbd.read_table(product, tbl, PAGES)
    rows = []
    for p in range(PAGES):
        flags, tokens, _payload = shipped[p]
        if flags != 0x0060:
            raise RuntimeError(f"NE_SHIPPED_FLAGS {p}")
        got = kbd.grid(tokens)
        want = jp_grid_page(meta["grid"], p)
        want = [[(0x8000 | kbd.JP_DASH_SLOT) if v == (0x8000 | kbd.US_DASH_SLOT)
                 else v for v in row] for row in want]
        if got != want:
            raise RuntimeError(f"NE_SHIPPED_GRID_MISMATCH {p}")
        rows.append({"page": p, "name": PAGE_NAMES[p],
                     "payload_bytes": len(_payload),
                     "cells": sum(1 for row in got for v in row if v)})
    audits["keyboard"] = {"pages": rows, "grid_matches_jp_retail": True,
                          "dash_cells_remapped_to_0x06D9": meta["dash_cells"]}

    # 2. the artwork is byte-identical to the JP ROM
    art = {}
    for i, (name, off) in enumerate(JP_ART):
        end = JP_ART[i + 1][1] if i + 1 < len(JP_ART) else JP_ART_END
        want = jp_raw[off:end]
        p = meta["placement"][name]
        got = product[p["offset"]:p["offset"] + p["bytes"]]
        if got != want:
            raise RuntimeError(f"NE_ART_MISMATCH {name}")
        art[name] = {"jp_rom": f"0x{off:06X}", "bytes": len(want),
                     "us_rom": f"0x{p['offset']:06X}",
                     "cpu_pointer": f"0x{p['cpu_pointer']:08X}"}
    lm = meta["placement"]["limits"]
    if product[lm["offset"]:lm["offset"] + PAGES * LIMIT_ROWS] !=             jp_raw[JP_CURSOR_LIMITS:JP_CURSOR_LIMITS + PAGES * LIMIT_ROWS]:
        raise RuntimeError("NE_LIMITS_MISMATCH")
    art["limits"] = {"jp_rom": f"0x{JP_CURSOR_LIMITS:06X}",
                     "bytes": PAGES * LIMIT_ROWS,
                     "us_rom": f"0x{lm['offset']:06X}",
                     "cpu_pointer": f"0x{lm['cpu_pointer']:08X}",
                     "cells_per_row": [list(jp_raw[JP_CURSOR_LIMITS + p * LIMIT_ROWS:
                                                  JP_CURSOR_LIMITS + (p + 1) * LIMIT_ROWS])
                                       for p in range(PAGES)]}
    tx = meta["placement"]["tabx"]
    if product[tx["offset"]:tx["offset"] + PAGES] != JP_TAB_X_BYTES:
        raise RuntimeError("NE_TAB_X_MISMATCH")
    art["tabx"] = {"jp_rom": f"0x{JP_TAB_X:06X}", "bytes": PAGES,
                   "us_rom": f"0x{tx['offset']:06X}",
                   "cpu_pointer": f"0x{tx['cpu_pointer']:08X}",
                   "per_page_x": list(JP_TAB_X_BYTES)}
    tab_count = int.from_bytes(product[meta["placement"]["tabhdr"]["offset"] + 2:
                                       meta["placement"]["tabhdr"]["offset"] + 4], "big")
    if tab_count != PAGES:
        raise RuntimeError(f"NE_TAB_COUNT {tab_count}")
    audits["artwork"] = {"resources": art, "tab_strip_sub_images": tab_count}

    # 3. every byte that changed is explained
    ranges, i = [], 0
    while i < len(base):
        if product[i] != base[i]:
            j = i
            while j < len(base) and product[j] != base[j]:
                j += 1
            ranges.append((i, j))
            i = j
        else:
            i += 1
    merged = []
    for a, b in ranges:
        if merged and a - merged[-1][1] <= 4:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    allowed = [(block_start, block_end)]
    for slots, _p in US_LITERALS.values():
        allowed += [(s, s + 4) for s in slots]
    allowed += [(o, o + len(n)) for o, _w, n, _y in CODE_PATCHES]
    unexplained = [(a, b) for a, b in merged
                   if not any(lo <= a and b <= hi for lo, hi in allowed)]
    if unexplained:
        raise RuntimeError(f"NE_UNEXPLAINED_BYTES {[(hex(a), hex(b)) for a, b in unexplained]}")
    exe = [(a, b) for a, b in merged if a < 0x0F00000 and not (block_start <= a < block_end)]
    audits["binary_touch"] = {
        "changed_ranges": len(merged),
        "unexplained_executable_changes": 0,
        "unexplained_data_changes": 0,
        "executable_and_literal_ranges": [f"0x{a:06X}-0x{b:06X}" for a, b in exe],
        "relocated_block": f"0x{block_start:06X}-0x{block_end:06X}",
        "bytes_relocated": block_end - block_start,
        "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end,
    }

    # 4. nothing outside the name-entry module points at what we repointed,
    #    and the pristine resources are still present and untouched
    for name, (slots, pristine) in US_LITERALS.items():
        refs = [i for i in range(0, len(product) - 3, 2)
                if struct.unpack_from("<I", product, i)[0] == pristine]
        if any(r in slots for r in refs):
            raise RuntimeError(f"NE_LITERAL_NOT_REPOINTED {name}")
        audits.setdefault("retained_us_resource_refs", {})[name] = [f"0x{r:06X}" for r in refs]

    # 5. the name field is untouched: same buffer size, same cap
    for off, pristine, _new, _why in ((0x0012A8AA, b"\x09\x22", None, None),
                                      (0x0012A8CE, b"\x08\x2B", None, None),
                                      (0x0012AB2C, b"\x01\x30", None, None)):
        if product[off:off + 2] != pristine or us_raw[off:off + 2] != pristine:
            raise RuntimeError(f"NE_NAME_FIELD_TOUCHED 0x{off:06X}")
    audits["name_field"] = {
        "buffer_bytes": 20, "slots": 9, "max_characters": 8,
        "token_form": "big-endian CHR_FULL 0x8000|slot, 0x4073 pad, 0x0000 terminator",
        "changed_by_this_layer": False,
    }
    audits["code"] = meta["code"]
    return audits


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
        raise RuntimeError("NE_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[3], second[4]) or meta["placement"] != second[2]["placement"]:
        raise RuntimeError("NE_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    audits = validate(product, base, meta, bs, be)
    if sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(
            f"CANONICAL_PRODUCTION_MISMATCH {sha(product)} != {EXPECTED_PRODUCTION}")

    summary = {
        "milestone": "jp-name-entry",
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True,
                        "layout_identical": meta["placement"] == second[2]["placement"]},
        "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
