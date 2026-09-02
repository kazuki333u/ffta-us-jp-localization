#! python3
"""Battle objective banners: the JP tilemaps that lay out the JP sheet.

Why the RC4 banner swap was incomplete
--------------------------------------

RC4 (``ffta_jp_ui_graphics``) moved the JP objective-banner sheet -- the A7
container of six 1,472-byte sub-images (``US 0x083C1834`` <- ``JP 0x083B3FF0``)
-- and left the **tilemaps** that address it alone, on the belief that the
banner is "one fixed-shape sprite composite".  It is not.  ``US 0x0808F8E8``
loads the sub-image to BG VRAM ``0x06009C00`` and then blits a **24x4 BG
tilemap** picked from the six-entry pointer table ``US 0x08394058`` (entry
sizes ``US 0x08394070``, all ``0xC0``).  Each tilemap places the 46 sheet tiles
plus the bubble-frame tiles, and the JP tilemaps differ from the US ones for
four of the six banners:

====  ======================  =========  =========  ======================
sub   banner                  US width   JP width   JP sheet vs US tilemap
====  ======================  =========  =========  ======================
0     DEFEAT THE BOSS!        20 cells   24 cells   JP text tiles 0-1 and
                                                    18-21 never drawn
1     DEFEAT ALL ENEMIES!     24 cells   22 cells   JP tiles 0/19/20/39
                                                    (blank in JP, unused by
                                                    the JP map) drawn at both
                                                    ends: the sand-coloured
                                                    bars seen in RC4-RC6
2     SURVIVE!                20 cells   20 cells   different cell order
3     DESTROY ALL TARGETS!    24 cells   22 cells   as sub 1
4     (already Japanese)      24 cells   24 cells   identical
5     SNOWBALL FIGHT!         20 cells   20 cells   identical
====  ======================  =========  =========  ======================

Measured on the shipped RC6 image in mGBA: ``TO WIN : ひとり残らずたおせ！``
renders with a tan block on each side of the text, exactly the tiles the JP
map does not address (``build/rc7_gameplay_audit/20260831_run``).  Same
failure class as the RC4 stat-label rejection and the RC5 fix: **a sheet is
only half an asset; the table that indexes it must travel with it.**

What this layer does
--------------------

Relocates the four JP tilemaps whose bytes differ (subs 0..3, 4 x 192 bytes)
into the tail and rewrites the four pointer words of the US table
``0x08394058`` to point at them.  Subs 4 and 5 are byte-identical in the two
ROMs and are left on the pristine US maps.  **Zero executable bytes change.**
The pointer table itself is referenced from exactly one literal-pool word
(``US 0x0808FA78``) and no map is referenced from anywhere but the table, so
the change reaches every consumer by construction (both checked on every
build).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_month_names as prev
import ffta_jp_s_text_leaf_repoint as stext

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/banner_maps"
OUTROM = ROOT / "rom/build/ffta_us_jp_banner_maps.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_banner_maps_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_month_names -- the RC6 build.
BASELINE = "B80F206732635D3F3913CEBAA8F2E72498887D8AC65FD6B304E499C216E54281"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "5C2190610186B1F879C26DBDDCBCD2719756905EB8BB41244DA9B9A6A468D54A"

US_TABLE = 0x00394058          # u32[6] tilemap pointers (file offset)
US_SIZES = 0x00394070          # u32[6] tilemap byte sizes, all 0xC0
JP_TABLE = 0x003873E8
JP_SIZES = 0x00387400
US_TABLE_REF = 0x0008FA78      # the single literal-pool word that names the table
N_SUBS = 6
MAP_BYTES = 0xC0               # 24 x 4 cells x u16
MAP_COLS, MAP_ROWS = 24, 4
# The banner loader; ``r8[0x22]`` selects the sub-image / tilemap.
LOADER = 0x0808F8E8

NAMES = ("DEFEAT THE BOSS!", "DEFEAT ALL ENEMIES!", "SURVIVE!",
         "DESTROY ALL TARGETS!", "(already Japanese in US)", "SNOWBALL FIGHT!")


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def table(rom: bytes, off: int):
    return [struct.unpack_from("<I", rom, off + 4 * i)[0] for i in range(N_SUBS)]


def maps(rom: bytes, tbl: int, sizes: int):
    ptrs, szs = table(rom, tbl), table(rom, sizes)
    if any(s != MAP_BYTES for s in szs):
        raise RuntimeError(f"BM_SIZE_TABLE {szs}")
    out = []
    for p in ptrs:
        if not (ROM <= p < ROM + len(rom)):
            raise RuntimeError(f"BM_BAD_MAP_POINTER 0x{p:08X}")
        out.append(bytes(rom[p - ROM:p - ROM + MAP_BYTES]))
    return ptrs, out


def used_width(m: bytes) -> int:
    cells = struct.unpack("<%dH" % (MAP_BYTES // 2), m)
    cols = [c for c in range(MAP_COLS) if any(cells[r * MAP_COLS + c] for r in range(MAP_ROWS))]
    return max(cols) + 1 if cols else 0


def refs(rom: bytes, value: int):
    needle = struct.pack("<I", value)
    out, i = [], rom.find(needle)
    while i != -1:
        out.append(i)
        i = rom.find(needle, i + 1)
    return out


def plan(us_raw: bytes, jp_raw: bytes):
    us_ptrs, us_maps = maps(us_raw, US_TABLE, US_SIZES)
    jp_ptrs, jp_maps = maps(jp_raw, JP_TABLE, JP_SIZES)
    moved = [i for i in range(N_SUBS) if us_maps[i] != jp_maps[i]]
    kept = [i for i in range(N_SUBS) if us_maps[i] == jp_maps[i]]
    if moved != [0, 1, 2, 3] or kept != [4, 5]:
        raise RuntimeError(f"BM_UNEXPECTED_DIVERGENCE moved={moved}")
    # the table is named once, by the loader's literal pool; no map is named at all
    if refs(us_raw, ROM + US_TABLE) != [US_TABLE_REF]:
        raise RuntimeError(f"BM_TABLE_REFERENCES {[hex(r) for r in refs(us_raw, ROM + US_TABLE)]}")
    for i, p in enumerate(us_ptrs):
        r = refs(us_raw, p)
        if r != [US_TABLE + 4 * i]:
            raise RuntimeError(f"BM_MAP_REFERENCES sub{i} {[hex(x) for x in r]}")
    report = []
    for i in range(N_SUBS):
        report.append({
            "sub": i, "banner": NAMES[i],
            "us_map": f"0x{us_ptrs[i]:08X}", "jp_map": f"0x{jp_ptrs[i]:08X}",
            "us_used_cells": used_width(us_maps[i]), "jp_used_cells": used_width(jp_maps[i]),
            "action": "relocate JP map" if i in moved else "identical, pristine US map kept",
        })
    return us_ptrs, us_maps, jp_maps, moved, kept, report


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"BM_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]

    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    raw = bytearray(base)
    us_ptrs, us_maps, jp_maps, moved, kept, report = plan(us_raw, jp_raw)
    # the table and the maps must be pristine in the baseline
    if raw[US_TABLE:US_TABLE + 4 * N_SUBS] != us_raw[US_TABLE:US_TABLE + 4 * N_SUBS]:
        raise RuntimeError("BM_TABLE_DRIFT")
    for p, m in zip(us_ptrs, us_maps):
        if raw[p - ROM:p - ROM + MAP_BYTES] != m:
            raise RuntimeError(f"BM_US_MAP_DRIFT 0x{p:08X}")

    cursor = block_start = stext.align(prev_block_end, 4)
    placement = []
    for i in moved:
        raw[cursor:cursor + MAP_BYTES] = jp_maps[i]
        raw[US_TABLE + 4 * i:US_TABLE + 4 * i + 4] = (ROM + cursor).to_bytes(4, "little")
        placement.append({"sub": i, "us_rom_offset": f"0x{cursor:06X}",
                          "cpu_pointer": f"0x{ROM + cursor:08X}", "bytes": MAP_BYTES,
                          "table_slot": f"0x{US_TABLE + 4 * i:06X}"})
        cursor += MAP_BYTES
    block_end = cursor = stext.align(cursor, 4)

    if len(raw) != len(base):
        raise RuntimeError("BM_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("BM_BLOCK_OUTSIDE_TAIL")
    if block_start < prev_block_end:
        raise RuntimeError("BM_BLOCK_OVERLAPS_PREVIOUS")
    meta = {"placement": placement, "maps": report, "moved": moved, "kept": kept}
    return bytes(raw), base, meta, block_start, block_end


def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    us_ptrs, us_maps, jp_maps, moved, kept, _r = plan(us_raw, jp_raw)
    got_ptrs, got_maps = maps(product, US_TABLE, US_SIZES)
    for i in range(N_SUBS):
        if got_maps[i] != jp_maps[i]:
            raise RuntimeError(f"BM_PRODUCT_MAP_NOT_JP sub{i}")
        if i in kept and got_ptrs[i] != us_ptrs[i]:
            raise RuntimeError(f"BM_KEPT_POINTER_MOVED sub{i}")
        if i in moved and not (block_start <= got_ptrs[i] - ROM < block_end):
            raise RuntimeError(f"BM_MOVED_POINTER_OUTSIDE_BLOCK sub{i}")
        # the pristine US maps are left in place, untouched
        p = us_ptrs[i] - ROM
        if product[p:p + MAP_BYTES] != us_maps[i]:
            raise RuntimeError(f"BM_US_MAP_DISTURBED sub{i}")
    if product[US_SIZES:US_SIZES + 4 * N_SUBS] != us_raw[US_SIZES:US_SIZES + 4 * N_SUBS]:
        raise RuntimeError("BM_SIZE_TABLE_TOUCHED")
    # the loader code is byte-identical to pristine US
    if product[LOADER - ROM:0x0808FA5C - ROM] != us_raw[LOADER - ROM:0x0808FA5C - ROM]:
        raise RuntimeError("BM_LOADER_CODE_CHANGED")

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
    allowed = [(block_start, block_end)] + [(US_TABLE + 4 * s, US_TABLE + 4 * s + 4) for s in moved]

    def covered(a, b, windows):
        cur = a
        for lo, hi in sorted(windows):
            if lo <= cur < hi:
                cur = max(cur, hi)
        return cur >= b

    bad = [(a, b) for a, b in merged if not covered(a, b, allowed)]
    if bad:
        raise RuntimeError(f"BM_UNEXPLAINED_BYTES {[(hex(a), hex(b)) for a, b in bad]}")
    changed_words = sum(1 for s in moved if product[US_TABLE + 4 * s:US_TABLE + 4 * s + 4] != base[US_TABLE + 4 * s:US_TABLE + 4 * s + 4])
    if changed_words != len(moved):
        raise RuntimeError("BM_POINTER_COUNT")

    # earlier milestones' literal slots untouched
    for slot in prev.CONTAINER_SLOTS + prev.PRISTINE_SLOTS:
        if product[slot:slot + 4] != base[slot:slot + 4]:
            raise RuntimeError("BM_MONTH_NAMES_TOUCHED")
    for spec in prev.prev.ASSETS:
        for slot in spec["slots"]:
            if product[slot:slot + 4] != base[slot:slot + 4]:
                raise RuntimeError(f"BM_MENU_LABELS_TOUCHED {spec['key']}")
    return {
        "maps": meta["maps"],
        "placement": meta["placement"],
        "binary_touch": {
            "changed_ranges": len(merged), "executable_bytes_changed": 0,
            "pointer_words_repointed": len(moved), "code_patches": 0,
            "relocated_block": f"0x{block_start:06X}-0x{block_end:06X}",
            "bytes_relocated": block_end - block_start,
            "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end,
        },
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
        raise RuntimeError("BM_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "banner-maps",
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
