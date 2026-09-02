#! python3
"""Gameplay-critical non-text graphics: the unit status / equip stat labels.

Why this layer exists
---------------------

The JP UI graphics milestone (``ffta_jp_ui_graphics``) deferred
``US 0x083B4214`` -- the ``Move`` / ``Jump`` / ``Evade`` / ``Weapon Atk`` /
``Def`` / ``Magic Pow`` / ``Res`` / ``Speed`` sheet -- as LOCAL_REBUILD.  It
had swapped the sheet alone, seen the panel render fragmented, and reverted.
The note it left said the screens "draw each label as its own span at a
hard-coded x and a hard-coded length"; what is actually there is one step
simpler, and it is data, not code.

The mechanism, established at runtime (see
``build/gameplay_gfx_final/<run>/``)
----------------------------------------------------------------------------

The panel is a rectangle of **BG tilemap cells**.  ``US 0x08035578`` is a
generic helper::

    copy_map_add(src=r0, dst=r1, count=r2, base=r3):
        for i in range(count): dst[i] = src[i] + base

Twelve sites call it.  Exactly **three** pass ``base = 0xFE`` -- the charblock
index the loader puts the stat-label tiles at -- and those three are the whole
of this panel:

======================  ======  =====  ==================  ==================
call site (US)          count   base   US table            JP table
======================  ======  =====  ==================  ==================
``0x080757F2``          0xC0    0xFE   ``0x0894F7DC``      ``0x08901028``
``0x08076162``          0xC0    0xFE   ``0x0894F7DC``      ``0x08901028``
``0x0808BA68``          0xE0    0xFE   ``0x0894F95C``      ``0x089011A8``
======================  ======  =====  ==================  ==================

The eight bytes before each ``bl`` are **byte-identical in the JP ROM** at the
matching sites (``…0968 C022 FE23`` / ``…0968 E022 FE23``), so the two ROMs
agree on the count, the base and the argument shape.  Only the *tables*
differ, and each table is a plain array of ``u16`` cells whose value is
``0`` for a blank cell and ``n`` for label tile ``n - 1``.  Both tables top
out at **76** on both sides -- exactly the length of the sheet's divergent
run, tiles 107..182.

So the port is three verbatim data blocks and no code:

* the sheet ``US 0x083B4214`` <- ``JP 0x083A7004`` (227 tiles both sides,
  blank indices identical, divergent runs 107..182 and 190..191).  Tiles
  190..191 are **not** label glyphs -- they are one animation frame of the
  menu cursor -- so the shipped sheet keeps the US pair and is re-encoded by
  ``gfx.encode_b``.  It therefore differs from the RC4 sheet in exactly the
  76 label tiles and in nothing else;
* the two map tables, which are adjacent in both ROMs
  (``0x0894F7DC``/``0x0894F95C`` and ``0x08901028``/``0x089011A8``), moved as
  one 832-byte block.

Why the earlier swap failed is now exact: the JP sheet packs its 76 label
tiles in the JP panel's cell order, and the US tables address them in the US
panel's cell order.  Swapping either one alone re-indexes every label after
the first, which is why ``いどう`` rendered and the rest of the panel did not.

What this layer deliberately does not do
----------------------------------------

* **No code patch.**  Zero executable bytes change; only relocated data and
  the literal-pool words that point at it, exactly as the previous layer.
* **Nothing whose JP twin needs a re-encoded sheet.**  ``0x083AEEA4``
  (``Suspended:``) and ``0x083B53A4`` (``Sort Items``) have different tile
  counts on the two sides, so no verbatim block exists to move.
* **Nothing whose geometry lives in OAM.**  The ``MISSION CLEARED!`` banners
  and the world-map place-name / month-name labels compose several sprites of
  per-label shape; see the milestone document.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gfx
import ffta_jp_coverage_audit as coverage
import ffta_jp_ui_graphics as prev
import ffta_jp_s_text_leaf_repoint as stext

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/gameplay_gfx_final"
OUTROM = ROOT / "rom/build/ffta_us_jp_gameplay_gfx.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_gameplay_gfx_repeat.gba"

ROM = 0x08000000

# Output of ffta_jp_ui_graphics -- the RC4 build.
BASELINE = "61C746714157CD5CBD7CFE58852CC225213EEA08BC8AABFEE9877497763038D3"
# Terminal artifact of the production chain: the single canonical final-SHA
# authority once this layer is in the chain.
EXPECTED_PRODUCTION = "F696951432446BECB013E5A630E0D2F7418293846C226D5D3716BD20E562072E"

# The stat-label tiles live at charblock index 0xFE + n in both ROMs.
LABEL_BASE = 0xFE
# The sheet's divergent run: the 76 label tiles the two map tables address.
LABEL_TILES = (107, 182)
# The other divergent tiles of the sheet, which are not label glyphs.
OTHER_DIVERGENT = (190, 191)

# (US bl address, count, US table, JP table) -- the whole of the panel.
CALL_SITES = (
    (0x080757F2, 0xC0, 0x0894F7DC, 0x08901028),
    (0x08076162, 0xC0, 0x0894F7DC, 0x08901028),
    (0x0808BA68, 0xE0, 0x0894F95C, 0x089011A8),
)
# The eight bytes ending at each bl: ... ldr r1,[r1] ; movs r2,#count ;
# movs r3,#0xFE.  Identical in both ROMs, which is the contract this layer
# relies on.
CALL_PREFIX = {
    0x080757F2: (0x0806FE06, bytes.fromhex("b1180968c022fe23")),
    0x08076162: (0x08070772, bytes.fromhex("89180968c022fe23")),
    0x0808BA68: (0x08084280, bytes.fromhex("b4310968e022fe23")),
}

# The third panel table.  It is not next to the other two -- it sits in the
# graphics region -- and it is drawn by two hand-rolled loops instead of the
# generic ``copy_map_add`` helper.  Those loops carry the panel width as a
# code immediate, and the two ROMs disagree: the first sub-table is 8 rows of
# **10** cells in the US and 8 rows of **9** in JP (the second loop copies the
# same 80 against 72 cells flat into the same 10-wide window buffer).
#
# Rather than patch the width, this layer **re-packs** the JP sub-table into
# the US width: each JP row of 9 cells plus one blank cell.  A blank cell is
# the window's background tile and the panel is anchored at the window's
# column 0, so the drawn result is the JP panel exactly, with one unused blank
# column on the right where the US panel's tenth column already was.  The
# shipped block is then the same 228 bytes as the US one and **no code byte
# changes** -- the same discipline as the previous layer.
# (us_off, jp_off, us_cells, jp_cells, us_width, jp_width)
TABLE_C_PARTS = ((0x000, 0x000, 80, 72, 10, 9), (0x0A0, 0x090, 34, 34, 34, 34))
CODE_PATCHES = ()
# Windows that must be byte-identical in the two ROMs apart from the bytes
# named: the proof that only the panel width differs between the two versions
# of these loops.  (offset in window, US byte, JP byte)
# Every place in the pristine US ROM that addresses the label tiles.  A panel
# table is recognisable without any code: a run of eight or more ascending
# u16 cells whose palette nibble is D/E/F and whose tile index is inside the
# sheet's 76-tile label run.  The scan finds exactly these, and this layer
# fails if it ever finds one more -- that is the guard that caught the third
# table after the first two had already been ported and looked correct.
LABEL_TABLE_SIGNATURE = {
    0x083AF62C: "map C part 1", 0x083AF640: "map C part 1",
    0x083AF67C: "map C part 1", 0x083AF6F0: "map C part 2",
    0x0894F85E: "map A", 0x0894F87E: "map A", 0x0894F8DE: "map A",
    0x0894F95C: "map B", 0x0894F994: "map B", 0x0894FA3C: "map B",
    # not label panels: these index other sheets at bases 0x84 / 0x95
    0x0839220A: "other sheet",
    0x0895527E: "other sheet",
}

CODE_CONTRACT = (
    ("user1_part1", 0x08068220, 0x080642D4, 0x26, ((10, 0x09, 0x08),)),
    ("user1_part2", 0x0806829A, 0x0806434E, 0x26, ()),
    ("user2_part1", 0x0806F028, 0x08069C00, 0x1E, ((16, 0xC0, 0xA0), (26, 0x4F, 0x47))),
    ("user2_part2", 0x0806F2E0, 0x08069EB8, 0x36, ((42, 0xC0, 0xA0),)),
)

ASSETS = (
    dict(key="stat_label_tiles", kind="tiles_spliced",
         us=0x003B4214, jp=0x003A7004,
         # tiles 190..191 also diverge, and they are not label glyphs: they
         # are one animation frame of the menu cursor.  The shipped sheet
         # keeps the US pair so this milestone changes nothing but the panel.
         keep_us_tiles=OTHER_DIVERGENT,
         # every aligned word of the pristine US ROM that points at the sheet
         # us_delta -> (delta in the relocated block, literal slots)
         slots={0: (0, (0x0004DD48, 0x00065758, 0x00066D60,
                        0x0006EAB4, 0x00087460, 0x00137FEC))},
         note="unit status / equip stat label sheet (JP labels, US cursor tiles)"),
    dict(key="stat_label_maps", kind="raw",
         us=0x0094F7DC, jp=0x00901028, length=0x180 + 0x1C0,
         slots={0x000: (0x000, (0x000758DC, 0x000763F8)),
                0x180: (0x180, (0x0008BACC,))},
         note="the two stat-label panel tilemaps (0xC0 and 0xE0 cells)"),
    dict(key="stat_label_map_c", kind="raw",
         us=0x003AF62C, jp=0x003A242C, length=0x0A0 + 0x044,
         us_length=0x0A0 + 0x044, repack=True,
         slots={0x000: (0x000, (0x000682D8, 0x0006F04C)),
                0x0A0: (0x0A0, (0x000682E0, 0x0006F318))},
         note="the third stat-label panel tilemap (80/72 + 34 cells)"),
)

JP_TEXT = {
    "stat_label_tiles": [
        "Move -> いどう, Jump -> ジャンプ, Evade -> かいひ",
        "Weapon Atk -> 武器こうげき, Def -> ぼうぎょ",
        "Magic Pow -> 魔法こうげき, Res -> ていこう, Speed -> はやさ",
        "(the exact JP wording is whatever JP retail draws; the tiles are its "
        "own artwork, moved verbatim)",
    ],
    "stat_label_maps": [
        "the JP panel's cell layout for those labels -- without it the JP "
        "glyphs land at the US labels' cell offsets and the panel fragments",
    ],
    "stat_label_map_c": [
        "the same, for the third panel (the shop's buy/sell comparison and "
        "the window this layer's fixtures call panel C)",
    ],
}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ------------------------------------------------------------------ assets ---

def payload(rom: bytes, spec, base: int, us_side: bool = False):
    """The verbatim ROM bytes of one asset, and what it decodes to."""
    if spec["kind"] in ("tiles", "tiles_spliced"):
        data, end = gfx.decode_b(rom, base)
        return rom[base:end], data, {"compressed_bytes": end - base}
    if spec["kind"] == "raw":
        n = spec["us_length"] if (us_side and "us_length" in spec) else spec["length"]
        blob = rom[base:base + n]
        return blob, blob, {"bytes": n}
    raise RuntimeError(f"GG_UNKNOWN_KIND {spec['kind']}")


def shipped_sheet(us_raw: bytes, jp_raw: bytes, spec):
    """The sheet this layer actually writes: the JP sheet, with the tiles in
    ``keep_us_tiles`` restored from the US sheet, re-encoded with
    ``gfx.encode_b``.  The result is a pure function of the two ROMs."""
    _p, du, _m = payload(us_raw, spec, spec["us"])
    _p, dj, _m = payload(jp_raw, spec, spec["jp"])
    out = bytearray(dj)
    lo, hi = spec["keep_us_tiles"]
    for t in range(lo, hi + 1):
        out[t * 32:t * 32 + 32] = du[t * 32:t * 32 + 32]
    decoded = bytes(out)
    blob = gfx.encode_b(decoded)
    back, end = gfx.decode_b(blob, 0)
    if back != decoded or end != len(blob):
        raise RuntimeError("GG_ENCODE_ROUNDTRIP")
    return blob, decoded


def shipped_map_c(jp_raw: bytes, spec) -> bytes:
    """The JP panel-C table re-packed into the US row width (TABLE_C_PARTS)."""
    out = bytearray()
    for _us_off, jp_off, us_cells, jp_cells, us_w, jp_w in TABLE_C_PARTS:
        src = jp_raw[spec["jp"] + jp_off:spec["jp"] + jp_off + jp_cells * 2]
        if us_w == jp_w:
            out += src
            continue
        if (jp_cells % jp_w or us_cells % us_w
                or jp_cells // jp_w != us_cells // us_w):
            raise RuntimeError("GG_MAPC_REPACK_SHAPE")
        pad = bytes(2 * (us_w - jp_w))
        for r in range(jp_cells // jp_w):
            out += src[r * jp_w * 2:(r + 1) * jp_w * 2] + pad
    if len(out) != spec["length"]:
        raise RuntimeError(f"GG_MAPC_REPACK_LENGTH {len(out)}")
    return bytes(out)


def map_cells(blob: bytes):
    return struct.unpack(f"<{len(blob) // 2}H", blob)


def sheet_proof(us_raw: bytes, jp_raw: bytes, spec):
    _p, du, mu = payload(us_raw, spec, spec["us"])
    _p, dj, mj = payload(jp_raw, spec, spec["jp"])
    info = gfx.tile_diff(du, dj)
    if not info["same_tile_count"]:
        raise RuntimeError("GG_TILE_COUNT")
    if not info["blank_tiles_match"]:
        raise RuntimeError("GG_BLANK_TILES")
    if info["divergent_runs"] != [LABEL_TILES, OTHER_DIVERGENT]:
        raise RuntimeError(f"GG_DIVERGENT_RUNS {info['divergent_runs']}")
    lo, hi = LABEL_TILES
    info["label_tiles"] = hi - lo + 1
    info["us_compressed_bytes"] = mu["compressed_bytes"]
    info["jp_compressed_bytes"] = mj["compressed_bytes"]

    blob, decoded = shipped_sheet(us_raw, jp_raw, spec)
    vs_us = gfx.tile_diff(du, decoded)
    vs_jp = gfx.tile_diff(dj, decoded)
    if vs_us["divergent_runs"] != [LABEL_TILES]:
        raise RuntimeError(f"GG_SHIPPED_VS_US {vs_us['divergent_runs']}")
    if vs_jp["divergent_runs"] != [OTHER_DIVERGENT]:
        raise RuntimeError(f"GG_SHIPPED_VS_JP {vs_jp['divergent_runs']}")
    info["shipped"] = {
        "encoder": "ffta_gfx_codec.encode_b (literal runs and zero fills)",
        "compressed_bytes": len(blob),
        "decoded_bytes": len(decoded),
        "differs_from_us_only_in": [list(LABEL_TILES)],
        "differs_from_jp_only_in": [list(OTHER_DIVERGENT)],
        "kept_us_tiles": list(range(OTHER_DIVERGENT[0], OTHER_DIVERGENT[1] + 1)),
    }
    return info


def map_proof(us_raw: bytes, jp_raw: bytes, spec):
    """The two tables address exactly the sheet's divergent label run."""
    us_blob, _d, _m = payload(us_raw, spec, spec["us"])
    jp_blob, _d, _m = payload(jp_raw, spec, spec["jp"])
    if len(us_blob) != len(jp_blob):
        raise RuntimeError("GG_MAP_LENGTH")
    if us_blob == jp_blob:
        raise RuntimeError("GG_MAP_NO_CHANGE")
    lo, hi = LABEL_TILES
    want_max = hi - lo + 1
    out = {}
    for name, blob in (("us", us_blob), ("jp", jp_blob)):
        for off, count in ((0x000, 0xC0), (0x180, 0xE0)):
            cells = map_cells(blob[off:off + count * 2])
            if len(cells) != count:
                raise RuntimeError(f"GG_MAP_COUNT {name} 0x{off:X}")
            idx = [c & 0x3FF for c in cells]
            used = sorted({i for i in idx if i})
            if not used or used[0] != 1 or used[-1] != want_max:
                raise RuntimeError(
                    f"GG_MAP_RANGE {name} 0x{off:X} {used[:1]}..{used[-1:]}")
            out[f"{name}_0x{off:03X}"] = {
                "cells": count,
                "nonzero_cells": sum(1 for i in idx if i),
                "distinct_label_tiles": len(used),
                "min_label_tile": used[0], "max_label_tile": used[-1],
            }
    return out


def map_c_proof(us_raw: bytes, jp_raw: bytes, spec):
    """Panel C: two sub-tables, US wider than JP by one cell per row."""
    lo, hi = LABEL_TILES
    want_max = hi - lo + 1
    out = {}
    for us_off, jp_off, us_cells, jp_cells, _uw, _jw in TABLE_C_PARTS:
        for name, rom, base, off, cells in (
                ("us", us_raw, spec["us"], us_off, us_cells),
                ("jp", jp_raw, spec["jp"], jp_off, jp_cells)):
            blob = rom[base + off:base + off + cells * 2]
            idx = [c & 0x3FF for c in map_cells(blob)]
            used = sorted({i for i in idx if i})
            if not used or used[0] < 1 or used[-1] > want_max:
                raise RuntimeError(f"GG_MAPC_RANGE {name} 0x{off:X} {used[:1]}..{used[-1:]}")
            out[f"{name}_0x{off:03X}"] = {
                "cells": cells, "nonzero_cells": sum(1 for i in idx if i),
                "distinct_label_tiles": len(used),
                "min_label_tile": used[0], "max_label_tile": used[-1]}
    us_blob = us_raw[spec["us"]:spec["us"] + spec["us_length"]]
    shipped = shipped_map_c(jp_raw, spec)
    if us_blob == shipped:
        raise RuntimeError("GG_MAPC_NO_CHANGE")
    if len(us_blob) != len(shipped):
        raise RuntimeError("GG_MAPC_LENGTH")
    kept = [c for c in map_cells(shipped) if c]
    from_jp = [c for c in map_cells(jp_raw[spec["jp"]:spec["jp"] + 0x090 + 0x044]) if c]
    if kept != from_jp:
        raise RuntimeError("GG_MAPC_REPACK_CONTENT")
    out["us_bytes"] = len(us_blob)
    out["shipped_bytes"] = len(shipped)
    out["repack"] = "8 JP rows of 9 cells, each padded to the US width of 10"
    out["nonzero_cells_preserved"] = len(kept)
    return out


def code_proof(us_raw: bytes, jp_raw: bytes, base_raw: bytes):
    """Both hand-rolled panel-C loops are byte-identical in the two ROMs
    apart from the named bytes: the panel width, and a tile base this layer
    keeps at the US screen's own value."""
    out = {}
    for name, ua, ja, n, allowed in CODE_CONTRACT:
        u = us_raw[ua - ROM:ua - ROM + n]
        b = base_raw[ua - ROM:ua - ROM + n]
        j = jp_raw[ja - ROM:ja - ROM + n]
        if u != b:
            raise RuntimeError(f"GG_CODE_DRIFT {name}")
        diffs = tuple((k, u[k], j[k]) for k in range(n) if u[k] != j[k])
        if diffs != allowed:
            raise RuntimeError(f"GG_CODE_CONTRACT {name} {diffs}")
        out[name] = {"us": f"0x{ua:08X}", "jp": f"0x{ja:08X}", "bytes": n,
                     "differing": [{"offset": k, "us": f"0x{a:02X}", "jp": f"0x{c:02X}"}
                                   for k, a, c in diffs]}
    for off, was, now, why in CODE_PATCHES:
        if us_raw[off:off + len(was)] != was or base_raw[off:off + len(was)] != was:
            raise RuntimeError(f"GG_PATCH_PRISTINE 0x{off:06X}")
        out[f"patch_0x{off:06X}"] = {"was": was.hex().upper(), "now": now.hex().upper(),
                                     "why": why}
    return out


def label_table_scan(rom: bytes):
    """Every ascending run of >= 8 u16 cells that addresses the label tiles."""
    n = len(rom) // 2
    cells = struct.unpack_from(f"<{n}H", rom, 0)
    lo, hi = LABEL_TILES
    top = hi - lo + 1
    out, i = {}, 0
    while i < n - 8:
        v = cells[i]
        if (v >> 12) in (0xD, 0xE, 0xF) and 1 <= (v & 0xFFF) <= top:
            j = i
            while (j + 1 < n and cells[j + 1] == cells[j] + 1
                   and (cells[j + 1] & 0xFFF) <= top):
                j += 1
            if j - i + 1 >= 8:
                out[ROM + 2 * i] = j - i + 1
                i = j + 1
                continue
        i += 1
    return out


def completeness_proof(us_raw: bytes, product: bytes, block_start: int, block_end: int):
    """No consumer of the label tiles is left addressing a US table."""
    found = label_table_scan(us_raw)
    unknown = sorted(a for a in found if a not in LABEL_TABLE_SIGNATURE)
    if unknown:
        raise RuntimeError(f"GG_UNKNOWN_LABEL_TABLE {[hex(a) for a in unknown]}")
    missing = sorted(a for a in LABEL_TABLE_SIGNATURE if a not in found)
    if missing:
        raise RuntimeError(f"GG_LABEL_TABLE_VANISHED {[hex(a) for a in missing]}")
    other = {a for a, why in LABEL_TABLE_SIGNATURE.items() if why == "other sheet"}
    stray = sorted(a for a in label_table_scan(product)
                   if not (ROM + block_start <= a < ROM + block_end)
                   and a not in found)
    if stray:
        raise RuntimeError(f"GG_STRAY_LABEL_TABLE {[hex(a) for a in stray]}")
    return {
        "runs_in_pristine_us": {f"0x{a:08X}": {"cells": n,
                                               "belongs_to": LABEL_TABLE_SIGNATURE[a]}
                                for a, n in sorted(found.items())},
        "runs_not_label_panels": sorted(f"0x{a:08X}" for a in other),
        "us_tables_unreferenced_in_product": True,
    }


def call_site_proof(us_raw: bytes, jp_raw: bytes, base_raw: bytes):
    """The copy contract -- count, base and argument shape -- is identical in
    both ROMs and untouched by every layer below this one."""
    out = {}
    for site, count, us_tbl, jp_tbl in CALL_SITES:
        jp_site, prefix = CALL_PREFIX[site]
        u = site - ROM - len(prefix)
        j = jp_site - ROM - len(prefix)
        if us_raw[u:u + len(prefix)] != prefix:
            raise RuntimeError(f"GG_CALL_PREFIX_US 0x{site:08X}")
        if base_raw[u:u + len(prefix)] != prefix:
            raise RuntimeError(f"GG_CALL_PREFIX_BASE 0x{site:08X}")
        if jp_raw[j:j + len(prefix)] != prefix:
            raise RuntimeError(f"GG_CALL_PREFIX_JP 0x{jp_site:08X}")
        if prefix[4:6] != bytes((count, 0x22)) or prefix[6:8] != bytes((LABEL_BASE, 0x23)):
            raise RuntimeError(f"GG_CALL_ARGS 0x{site:08X}")
        out[f"0x{site:08X}"] = {
            "count": count, "base": LABEL_BASE,
            "us_table": f"0x{us_tbl:08X}", "jp_table": f"0x{jp_tbl:08X}",
            "jp_call_site": f"0x{jp_site:08X}",
            "prefix_identical_in_both_roms": True,
        }
    return out


# ------------------------------------------------------------------- build ---

def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"GG_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]

    jp_raw = JP.read_bytes()
    us_raw = US.read_bytes()
    raw = bytearray(base)

    # Every literal we rewrite must still hold its pristine value in the
    # pristine US ROM and in the baseline, and no other aligned word may
    # point at the asset.
    for spec in ASSETS:
        for delta, (_new, slots) in spec["slots"].items():
            want = ROM + spec["us"] + delta
            for slot in slots:
                for name, blob in (("US", us_raw), ("BASE", raw)):
                    if struct.unpack_from("<I", blob, slot)[0] != want:
                        raise RuntimeError(
                            f"GG_LITERAL_DRIFT_{name} {spec['key']} 0x{slot:06X}")
            found = [i for i in range(0, len(us_raw) - 3, 4)
                     if struct.unpack_from("<I", us_raw, i)[0] == want]
            if sorted(found) != sorted(slots):
                raise RuntimeError(f"GG_REFERENCE_SET {spec['key']} +0x{delta:X} "
                                   f"{[hex(f) for f in found]}")

    proofs = {
        "stat_label_tiles": sheet_proof(us_raw, jp_raw, ASSETS[0]),
        "stat_label_maps": map_proof(us_raw, jp_raw, ASSETS[1]),
        "stat_label_map_c": map_c_proof(us_raw, jp_raw, ASSETS[2]),
        "call_sites": call_site_proof(us_raw, jp_raw, base),
        "code": code_proof(us_raw, jp_raw, base),
    }

    cursor = block_start = stext.align(prev_block_end, 4)
    placement = {}
    for spec in ASSETS:
        if spec["kind"] == "tiles_spliced":
            blob, _decoded = shipped_sheet(us_raw, jp_raw, spec)
        elif spec.get("repack"):
            blob = shipped_map_c(jp_raw, spec)
        else:
            blob, _decoded, _meta = payload(jp_raw, spec, spec["jp"])
        raw[cursor:cursor + len(blob)] = blob
        placement[spec["key"]] = {
            "jp_rom_offset": f"0x{spec['jp']:06X}",
            "jp_cpu_address": f"0x{ROM + spec['jp']:08X}",
            "us_rom_offset": f"0x{cursor:06X}",
            "cpu_pointer": f"0x{ROM + cursor:08X}",
            "bytes": len(blob),
            "kind": spec["kind"],
            "repointed_literals": {f"us+0x{d:03X} -> new+0x{n:03X}":
                                   [f"0x{s:06X}" for s in sl]
                                   for d, (n, sl) in spec["slots"].items()},
        }
        for _delta, (new, slots) in spec["slots"].items():
            for slot in slots:
                raw[slot:slot + 4] = (ROM + cursor + new).to_bytes(4, "little")
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    for off, _was, now, _why in CODE_PATCHES:
        raw[off:off + len(now)] = now

    if len(raw) != len(base):
        raise RuntimeError("GG_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("GG_BLOCK_OUTSIDE_TAIL")
    if block_start < prev_block_end:
        raise RuntimeError("GG_BLOCK_OVERLAPS_PREVIOUS")

    meta = {"placement": placement, "proofs": proofs}
    return bytes(raw), base, meta, block_start, block_end


# ---------------------------------------------------------------- validate ---

def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    audits = {"assets": {}}

    for spec in ASSETS:
        p = meta["placement"][spec["key"]]
        here = int(p["us_rom_offset"], 16)
        if spec["kind"] == "tiles_spliced":
            want, want_decoded = shipped_sheet(us_raw, jp_raw, spec)
        elif spec.get("repack"):
            want = want_decoded = shipped_map_c(jp_raw, spec)
        else:
            want, want_decoded, _m = payload(jp_raw, spec, spec["jp"])
        if product[here:here + len(want)] != want:
            raise RuntimeError(f"GG_ASSET_NOT_VERBATIM {spec['key']}")

        _blob, decoded_here, _m2 = payload(product, spec, here)
        if decoded_here != want_decoded:
            raise RuntimeError(f"GG_ASSET_DECODE_MISMATCH {spec['key']}")

        for delta, (nd, slots) in spec["slots"].items():
            old = ROM + spec["us"] + delta
            new = ROM + here + nd
            for slot in slots:
                if struct.unpack_from("<I", product, slot)[0] != new:
                    raise RuntimeError(f"GG_LITERAL_NOT_REPOINTED {spec['key']}")
            stale = [i for i in range(0, len(product) - 3, 4)
                     if struct.unpack_from("<I", product, i)[0] == old]
            if stale:
                raise RuntimeError(f"GG_STALE_REFERENCE {spec['key']} "
                                   f"{[hex(s) for s in stale]}")

        us_blob, _d, _m = payload(us_raw, spec, spec["us"], us_side=True)
        if product[spec["us"]:spec["us"] + len(us_blob)] != us_blob:
            raise RuntimeError(f"GG_US_ASSET_DISTURBED {spec['key']}")

        audits["assets"][spec["key"]] = {
            "note": spec["note"],
            "japanese": JP_TEXT[spec["key"]],
            "placement": p,
            "proof": meta["proofs"][spec["key"]],
            "pristine_us_asset_retained_unreferenced": True,
        }
    audits["call_sites"] = meta["proofs"]["call_sites"]
    audits["code"] = meta["proofs"]["code"]
    audits["completeness"] = completeness_proof(us_raw, product, block_start, block_end)

    # The panel the two tables draw, resolved the way the engine resolves it:
    # cell value 0 -> blank, n -> label tile (n - 1) of the sheet.  Proves the
    # shipped tables and the shipped sheet address the same 76 tiles.
    here_tiles = int(meta["placement"]["stat_label_tiles"]["us_rom_offset"], 16)
    here_maps = int(meta["placement"]["stat_label_maps"]["us_rom_offset"], 16)
    sheet, _end = gfx.decode_b(product, here_tiles)
    lo, _hi = LABEL_TILES
    panels = {}
    for off, count in ((0x000, 0xC0), (0x180, 0xE0)):
        cells = map_cells(product[here_maps + off:here_maps + off + count * 2])
        used = sorted({c & 0x3FF for c in cells} - {0})
        for n in used:
            t = lo + n - 1
            if not (0 <= t * 32 < len(sheet)):
                raise RuntimeError(f"GG_PANEL_TILE_OUT_OF_RANGE {n}")
        blank = [n for n in used if not any(sheet[(lo + n - 1) * 32:(lo + n - 1) * 32 + 32])]
        if blank:
            raise RuntimeError(f"GG_PANEL_REFERENCES_BLANK_TILE {blank}")
        panels[f"0x{off:03X}"] = {
            "cells": count, "distinct_label_tiles": len(used),
            "sheet_tile_range": [lo + used[0] - 1, lo + used[-1] - 1],
            "every_referenced_tile_non_blank": True,
        }
    audits["panel_resolution"] = panels

    # Every byte that changed is explained.
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
    for spec in ASSETS:
        allowed += [(s, s + 4) for _n, sl in spec["slots"].values() for s in sl]
    allowed += [(off, off + len(now)) for off, _w, now, _y in CODE_PATCHES]
    unexplained = [(a, b) for a, b in merged
                   if not any(lo2 <= a and b <= hi2 for lo2, hi2 in allowed)]
    if unexplained:
        raise RuntimeError(
            f"GG_UNEXPLAINED_BYTES {[(hex(a), hex(b)) for a, b in unexplained]}")

    all_slots = [s for spec in ASSETS for _n, sl in spec["slots"].values() for s in sl]
    patch_bytes = 0
    for a, b in merged:
        if block_start <= a < block_end:
            continue
        if any(s <= a and b <= s + 4 for s in all_slots):
            continue
        hit = [(off, now) for off, _w, now, _y in CODE_PATCHES
               if off <= a and b <= off + len(now)]
        if not hit:
            raise RuntimeError(f"GG_NON_LITERAL_CHANGE 0x{a:06X}-0x{b:06X}")
        patch_bytes += b - a
    audits["binary_touch"] = {
        "changed_ranges": len(merged),
        "executable_bytes_changed": patch_bytes,
        "literal_words_repointed": len(all_slots),
        "code_patches": len(CODE_PATCHES),
        "relocated_block": f"0x{block_start:06X}-0x{block_end:06X}",
        "bytes_relocated": block_end - block_start,
        "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end,
    }

    # Every earlier milestone is untouched by this layer: the UI-graphics
    # literals still point where that layer put them, and the name-entry
    # literals and code patches are byte-for-byte the baseline's.
    name_entry = prev.prev
    for spec in prev.ASSETS:
        for slot in spec["slots"]:
            if product[slot:slot + 4] != base[slot:slot + 4]:
                raise RuntimeError(f"GG_UI_GRAPHICS_TOUCHED {spec['key']}")
    for name, (slots, _pristine) in name_entry.US_LITERALS.items():
        for slot in slots:
            if product[slot:slot + 4] != base[slot:slot + 4]:
                raise RuntimeError(f"GG_NAME_ENTRY_TOUCHED {name}")
    for off, _was, now, _why in name_entry.CODE_PATCHES:
        if product[off:off + len(now)] != now:
            raise RuntimeError(f"GG_NAME_ENTRY_CODE_TOUCHED 0x{off:06X}")
    audits["earlier_milestones_unchanged"] = True
    return audits


# -------------------------------------------------------------------- main ---

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
        raise RuntimeError("GG_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[3], second[4]) or meta["placement"] != second[2]["placement"]:
        raise RuntimeError("GG_LAYOUT_NONDETERMINISTIC")

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
        "milestone": "gameplay-gfx-final",
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
