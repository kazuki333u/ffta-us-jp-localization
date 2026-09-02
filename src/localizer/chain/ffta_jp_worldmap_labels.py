#! python3
"""World-map place-name labels: the Japanese artwork and its two index tables.

Why this layer exists
---------------------

The gameplay-gfx-final milestone (``ffta_jp_gameplay_gfx``) deferred the
world-map place labels (``Cyril``, ``Sprohm``, ...) as ARCHITECTURAL.  Its
note said the labels live in an indexed archive decompressed into the EWRAM
staging buffer, and that the blocker was "the per-label **OAM composition**".

**Both halves of that are wrong.**  The labels are not in the codec-B archive
at ``US 0x083E105C`` -- that archive's 103 entries are 102 JP/US-identical
world-map terrain sheets plus one US-only extra, and it never holds a label.
The labels are the sub-images of an ordinary **A7 sprite container**, missed
by the earlier scan only because ``ffta_gfx_codec.find_containers`` caps the
sub-image count at 64 and this container has 225.  And the OAM composition is
not per label: it is a five-entry table indexed by the label's *width*, and
that table is **byte-identical in the two ROMs**.

The mechanism, read out of the two ROMs
---------------------------------------

``US 0x080366C0`` and ``JP 0x080355F8`` are the same function::

    idx    = (state[+0x8C] - 1) & 0x7F           # the place the cursor is on
    nsubs  = WIDTH[idx] + 5                      # label width in 8px columns
    first  = FIRST[idx] >> 1                     # its first sub-image
    decode_subs(container, staging, first, nsubs)        # US 0x08005318
    dma16(staging -> 0x06010980, nsubs * 32 halfwords)   # US 0x08000A98
    x = clamp(cursor_x - nsubs*4, 0, 240 - nsubs*8)      # centred, clamped
    objects = SHAPE[WIDTH[idx]]                  # 2 or 3 OAM entries

``US 0x08036908`` is the second consumer (the mission-target screen).  It
caches ``WIDTH[idx] << 2`` in a state byte and reads the same three tables.

=========================  ==================  ==================
role                       US                  JP
=========================  ==================  ==================
A7 container (225/220)     ``0x083AB42C``      ``0x0839E17C``
``WIDTH`` u8[30]           ``0x08391DE4``      ``0x083852AC``
``FIRST`` u16[30]          ``0x08391E02``      ``0x083852CA``
``SHAPE`` -- 5 object      ``0x08391A48``      ``0x08384F88``
lists, byte-identical
OBJ VRAM destination       ``0x06010980``      ``0x06010980``
=========================  ==================  ==================

So the port is three facts and no code:

* both ROMs hold **exactly 30** place labels, in the same order (index 0 is
  Bervenia Palace, index 2 Sprohm, index 29 Ambervale -- the full
  correspondence is in ``LABELS``);
* ``FIRST`` and ``WIDTH`` tile the container exactly, on both sides
  (``sum(WIDTH[i] + 5) == sub_count``: 225 in US, 220 in JP);
* the widest label is 9 sub-images on **both** sides, so the label never
  runs past OBJ tile 94, where the month name starts.

What this layer writes
----------------------

* the JP container relocated verbatim into the tail -- header, sub-image
  streams, the four codec-block descriptor bytes and the full 1,024-byte
  back-reference dictionary, because a codec-A back-reference reads the
  dictionary and never the output (PROJECT_STATE section 6);
* the JP ``WIDTH`` + ``FIRST`` pair, which are adjacent in both ROMs, as one
  90-byte block;
* the six literal-pool words that point at them.

**Zero executable bytes change.**  ``SHAPE`` is not written: it is already
byte-identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gfx
import ffta_jp_coverage_audit as coverage
import ffta_jp_gameplay_gfx as prev
import ffta_jp_s_text_leaf_repoint as stext

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/worldmap_labels"
OUTROM = ROOT / "rom/build/ffta_us_jp_worldmap_labels.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_worldmap_labels_repeat.gba"

ROM = 0x08000000

# Output of ffta_jp_gameplay_gfx -- the RC5 build.
BASELINE = "F696951432446BECB013E5A630E0D2F7418293846C226D5D3716BD20E562072E"
EXPECTED_PRODUCTION = "B466D7370975B64C1D23F05D82F2E98C013EBA996FCFC2B7E0BBC3DAB94D6F54"

N_LABELS = 30
# OBJ VRAM tile the label is copied to, and the tile the month name starts at.
LABEL_TILE = 76
MONTH_TILE = 94

SHAPE_US = 0x08391A48
SHAPE_JP = 0x08384F88

# Both consumers, in both ROMs: (name, US address, JP address, bytes).  These
# windows carry the whole contract this layer depends on -- how the index
# reaches WIDTH, how FIRST reaches the decoder, how the copy length is
# computed from the width, and how the second consumer caches it.
LOADER_CONTRACT = (
    ("fn1_index_to_width",   0x080366E8, 0x08035620,  6),
    ("fn1_first_and_count",  0x08036712, 0x0803564A, 14),
    ("fn1_copy_length",      0x0803672A, 0x08035662, 12),
    ("fn2_width_to_field",   0x08036916, 0x0803585E, 20),
    ("fn2_first_and_count",  0x08036946, 0x0803588C, 12),
)

# The 30 labels, in table order, read off the rendered sheets of both ROMs
# (build/worldmap_labels/<run>/render/places_us.png and places_jp.png).
# Documentation of what ships; the build asserts only the count.
LABELS = (
    ("Bervenia Palace", "王宮ベルベニア"),
    ("Cyril", "始まりの街シリル"),
    ("Sprohm", "山岳都市スプロム"),
    ("Muscadet", "隠れ里ミュスカデ"),
    ("Cadoan", "叡智の都カドアン"),
    ("Baguba Port", "貿易港バグーバ"),
    ("Nubswood", "ヌーベスの森"),
    ("Giza Plains", "ギーザ平原"),
    ("Kudik Peaks", "クディアス山岳"),
    ("Uladon Bog", "ウラドン湿地"),
    ("Jeraw Sands", "ジェラワット砂漠"),
    ("Lutia Pass", "ルテティア峠"),
    ("Koringwood", "コリング樹林"),
    ("Ulei River", "ユレー川"),
    ("Aisenfield", "アイセン平原"),
    ("Roda Volcano", "ローダ火山"),
    ("Eluut Sands", "イルート砂漠"),
    ("Nargai Cave", "ナルガエ洞窟"),
    ("Salikawood", "サリカ樹林"),
    ("Delia Dunes", "デライア砂丘"),
    ("Gotor Sands", "ゴトランド砂原"),
    ("Ozmonfield", "オズモーネ平原"),
    ("Deti Plains", "ディーラ丘陵"),
    ("Siena Gorge", "シエンナ渓谷"),
    ("Materiwood", "マテリアの森"),
    ("Tubola Cave", "ツボラ洞窟"),
    ("Jagd Dorsa", "ヤクトドルーサ"),
    ("Jagd Helje", "ヤクトヘルジェ"),
    ("Jagd Ahli", "ヤクトアーリー"),
    ("Ambervale", "琥珀の谷"),
)

ASSETS = (
    dict(key="place_label_container", kind="container",
         us=0x003AB42C, jp=0x0039E17C,
         slots={0: (0, (0x00036760, 0x000369A8))},
         note="the world-map place-name sprite container (A7, 225 US / 220 JP "
              "sub-images of 8x16)"),
    dict(key="place_label_tables", kind="raw",
         us=0x00391DE4, jp=0x003852AC, length=N_LABELS * 3,
         slots={0x00: (0x00, (0x00036750, 0x000369A0)),
                0x1E: (0x1E, (0x00036764, 0x000369AC))},
         note="WIDTH u8[30] and FIRST u16[30], adjacent in both ROMs"),
)


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ------------------------------------------------------------------ tables ---

def read_tables(rom: bytes, base: int):
    """(WIDTH, FIRST, raw FIRST) from a 90-byte WIDTH+FIRST block."""
    width = list(rom[base:base + N_LABELS])
    raw_first = list(struct.unpack_from(f"<{N_LABELS}H", rom, base + N_LABELS))
    return width, [v >> 1 for v in raw_first], raw_first


def container_extent(rom: bytes, base: int):
    """(length, meta) of the verbatim block a relocated container needs."""
    count, total, _offs = gfx.container(rom, base)
    block = base + total
    mode, shift, size, dic, dlen = gfx.codec_a_descriptor(rom, block)
    if mode != 0 or size != 64:
        raise RuntimeError(f"WM_CONTAINER_DESCRIPTOR {mode} {size}")
    high = gfx.container_dictionary_high_water(rom, base)
    if high > dlen:
        raise RuntimeError(f"WM_DICTIONARY_OVERRUN {high} > {dlen}")
    length = (dic - base) + dlen
    return length, {
        "sub_images": count, "declared_total": total,
        "codec_block": f"0x{ROM + block:08X}", "mode": mode, "shift": shift,
        "sub_image_bytes": size, "dictionary_bytes": dlen,
        "dictionary_high_water": high, "verbatim_bytes": length,
    }


def table_proof(rom: bytes, tbl_base: int, cnt_base: int, tag: str):
    """WIDTH and FIRST tile the container exactly, with no gap and no overlap."""
    width, first, raw = read_tables(rom, tbl_base)
    count, _total, _offs = gfx.container(rom, cnt_base)
    if len(width) != N_LABELS or len(first) != N_LABELS:
        raise RuntimeError(f"WM_TABLE_LENGTH {tag}")
    if any(w > 4 for w in width):
        raise RuntimeError(f"WM_WIDTH_OUT_OF_RANGE {tag} {width}")
    if any(v & 1 for v in raw):
        raise RuntimeError(f"WM_FIRST_ODD {tag}")
    cursor = 0
    for i in range(N_LABELS):
        if first[i] != cursor:
            raise RuntimeError(f"WM_TABLE_NOT_CONTIGUOUS {tag} {i} "
                               f"{first[i]} != {cursor}")
        cursor += width[i] + 5
    if cursor != count:
        raise RuntimeError(f"WM_TABLE_DOES_NOT_TILE {tag} {cursor} != {count}")
    top = LABEL_TILE + 2 * max(w + 5 for w in width)
    if top > MONTH_TILE:
        raise RuntimeError(f"WM_LABEL_OVERRUNS_MONTH {tag} {top}")
    return {
        "labels": N_LABELS, "sub_images": count,
        "widths_in_columns": [w + 5 for w in width],
        "first_sub_image": first,
        "widest_label_columns": max(w + 5 for w in width),
        "highest_obj_tile_used": top - 1,
        "month_name_starts_at_tile": MONTH_TILE,
        "tables_tile_the_container": True,
    }


def shape_proof(us_raw: bytes, jp_raw: bytes):
    """The width-indexed OAM object lists, and why they need no port."""
    sizes = {(0, 0): (1, 1), (0, 1): (2, 2), (0, 2): (4, 4), (0, 3): (8, 8),
             (1, 0): (2, 1), (1, 1): (4, 1), (1, 2): (4, 2), (1, 3): (8, 4),
             (2, 0): (1, 2), (2, 1): (1, 4), (2, 2): (2, 4), (2, 3): (4, 8)}
    up = struct.unpack_from("<5I", us_raw, SHAPE_US - ROM)
    jp = struct.unpack_from("<5I", jp_raw, SHAPE_JP - ROM)
    out = {}
    for w in range(5):
        uo, jo = up[w] - ROM, jp[w] - ROM
        n = us_raw[uo]
        if jp_raw[jo] != n:
            raise RuntimeError(f"WM_SHAPE_COUNT {w}")
        span = 2 + 6 * n
        if us_raw[uo:uo + span] != jp_raw[jo:jo + span]:
            raise RuntimeError(f"WM_SHAPE_NOT_IDENTICAL {w}")
        covered, px = [], 0
        for i in range(n):
            a0, a1, a2 = struct.unpack_from("<3H", us_raw, uo + 2 + 6 * i)
            tw, th = sizes[((a0 >> 14) & 3, (a1 >> 14) & 3)]
            tile, x = a2 & 0x3FF, a1 & 0x1FF
            if x != px:
                raise RuntimeError(f"WM_SHAPE_X_GAP {w} {x} != {px}")
            if th != 2:
                raise RuntimeError(f"WM_SHAPE_HEIGHT {w}")
            covered += list(range(tile, tile + tw * th))
            px += tw * 8
        want = list(range(LABEL_TILE, LABEL_TILE + (w + 5) * 2))
        if sorted(covered) != want or px != (w + 5) * 8:
            raise RuntimeError(f"WM_SHAPE_COVERAGE {w} {covered} {px}")
        out[f"width_{w + 5}_columns"] = {
            "objects": n, "pixel_width": px,
            "obj_tiles": [want[0], want[-1]],
            "us_address": f"0x{up[w]:08X}", "jp_address": f"0x{jp[w]:08X}",
            "byte_identical": True,
        }
    return out


def loader_proof(us_raw: bytes, jp_raw: bytes, base_raw: bytes):
    out = {}
    for name, ua, ja, n in LOADER_CONTRACT:
        u = us_raw[ua - ROM:ua - ROM + n]
        b = base_raw[ua - ROM:ua - ROM + n]
        j = jp_raw[ja - ROM:ja - ROM + n]
        if u != b:
            raise RuntimeError(f"WM_LOADER_DRIFT {name}")
        if u != j:
            raise RuntimeError(f"WM_LOADER_NOT_IDENTICAL {name} "
                               f"{u.hex().upper()} {j.hex().upper()}")
        out[name] = {"us": f"0x{ua:08X}", "jp": f"0x{ja:08X}", "bytes": n,
                     "byte_identical_in_both_roms": True}
    for name, addr in (("us_obj_vram_dest", 0x08036768),
                       ("us_obj_vram_dest_2", 0x08036A44),
                       ("jp_obj_vram_dest", 0x080356A0),
                       ("jp_obj_vram_dest_2", 0x08035984)):
        blob = jp_raw if name.startswith("jp") else us_raw
        v = struct.unpack_from("<I", blob, addr - ROM)[0]
        if v != 0x06010980:
            raise RuntimeError(f"WM_VRAM_DEST {name} 0x{v:08X}")
        out[name] = f"0x{v:08X}"
    return out


# ------------------------------------------------------------------- build ---

def payload(rom: bytes, spec, base: int):
    if spec["kind"] == "container":
        n, meta = container_extent(rom, base)
        return rom[base:base + n], meta
    if spec["kind"] == "raw":
        return rom[base:base + spec["length"]], {"bytes": spec["length"]}
    raise RuntimeError(f"WM_UNKNOWN_KIND {spec['kind']}")


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"WM_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]

    jp_raw = JP.read_bytes()
    us_raw = US.read_bytes()
    raw = bytearray(base)

    for spec in ASSETS:
        for delta, (_new, slots) in spec["slots"].items():
            want = ROM + spec["us"] + delta
            for name, blob in (("US", us_raw), ("BASE", raw)):
                for slot in slots:
                    if struct.unpack_from("<I", blob, slot)[0] != want:
                        raise RuntimeError(
                            f"WM_LITERAL_DRIFT_{name} {spec['key']} 0x{slot:06X}")
            found = [i for i in range(0, len(us_raw) - 3, 4)
                     if struct.unpack_from("<I", us_raw, i)[0] == want]
            if sorted(found) != sorted(slots):
                raise RuntimeError(f"WM_REFERENCE_SET {spec['key']} +0x{delta:X} "
                                   f"{[hex(f) for f in found]}")

    if len(LABELS) != N_LABELS:
        raise RuntimeError("WM_LABEL_LIST_LENGTH")

    proofs = {
        "us_container": container_extent(us_raw, ASSETS[0]["us"])[1],
        "jp_container": container_extent(jp_raw, ASSETS[0]["jp"])[1],
        "us_tables": table_proof(us_raw, ASSETS[1]["us"], ASSETS[0]["us"], "us"),
        "jp_tables": table_proof(jp_raw, ASSETS[1]["jp"], ASSETS[0]["jp"], "jp"),
        "shape": shape_proof(us_raw, jp_raw),
        "loaders": loader_proof(us_raw, jp_raw, base),
    }

    cursor = block_start = stext.align(prev_block_end, 4)
    placement = {}
    for spec in ASSETS:
        blob, meta = payload(jp_raw, spec, spec["jp"])
        raw[cursor:cursor + len(blob)] = blob
        placement[spec["key"]] = {
            "jp_rom_offset": f"0x{spec['jp']:06X}",
            "jp_cpu_address": f"0x{ROM + spec['jp']:08X}",
            "us_rom_offset": f"0x{cursor:06X}",
            "cpu_pointer": f"0x{ROM + cursor:08X}",
            "bytes": len(blob), "kind": spec["kind"], "meta": meta,
            "repointed_literals": {f"us+0x{d:03X} -> new+0x{n:03X}":
                                   [f"0x{s:06X}" for s in sl]
                                   for d, (n, sl) in spec["slots"].items()},
        }
        for _delta, (new, slots) in spec["slots"].items():
            for slot in slots:
                raw[slot:slot + 4] = (ROM + cursor + new).to_bytes(4, "little")
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("WM_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("WM_BLOCK_OUTSIDE_TAIL")
    if block_start < prev_block_end:
        raise RuntimeError("WM_BLOCK_OVERLAPS_PREVIOUS")

    return (bytes(raw), base, {"placement": placement, "proofs": proofs},
            block_start, block_end)


# ---------------------------------------------------------------- validate ---

def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    audits = {"assets": {}}

    for spec in ASSETS:
        p = meta["placement"][spec["key"]]
        here = int(p["us_rom_offset"], 16)
        want, _m = payload(jp_raw, spec, spec["jp"])
        if product[here:here + len(want)] != want:
            raise RuntimeError(f"WM_ASSET_NOT_VERBATIM {spec['key']}")
        for delta, (nd, slots) in spec["slots"].items():
            old = ROM + spec["us"] + delta
            new = ROM + here + nd
            for slot in slots:
                if struct.unpack_from("<I", product, slot)[0] != new:
                    raise RuntimeError(f"WM_LITERAL_NOT_REPOINTED {spec['key']}")
            stale = [i for i in range(0, len(product) - 3, 4)
                     if struct.unpack_from("<I", product, i)[0] == old]
            if stale:
                raise RuntimeError(f"WM_STALE_REFERENCE {spec['key']} "
                                   f"{[hex(s) for s in stale]}")
        us_blob, _m = payload(us_raw, spec, spec["us"])
        if product[spec["us"]:spec["us"] + len(us_blob)] != us_blob:
            raise RuntimeError(f"WM_US_ASSET_DISTURBED {spec['key']}")
        audits["assets"][spec["key"]] = {
            "note": spec["note"], "placement": p,
            "pristine_us_asset_retained_unreferenced": True,
        }

    # The product resolves the way the engine resolves it: read the relocated
    # tables and the relocated container back out of the built ROM.
    cont = int(meta["placement"]["place_label_container"]["us_rom_offset"], 16)
    tbls = int(meta["placement"]["place_label_tables"]["us_rom_offset"], 16)
    audits["product_tables"] = table_proof(product, tbls, cont, "product")
    subs = gfx.container_subs(product, cont)
    jp_subs = gfx.container_subs(jp_raw, ASSETS[0]["jp"])
    if len(subs) != 220 or any(len(s) != 64 for s in subs):
        raise RuntimeError("WM_PRODUCT_CONTAINER_DECODE")
    if [bytes(s) for s in subs] != [bytes(s) for s in jp_subs]:
        raise RuntimeError("WM_PRODUCT_CONTAINER_NOT_JP")
    audits["product_container"] = {
        "sub_images": len(subs), "sub_image_bytes": 64,
        "decodes_identically_to_jp_retail": True,
    }
    width, first, _r = read_tables(product, tbls)
    audits["labels"] = [
        {"index": i, "us_retail": LABELS[i][0], "jp_retail": LABELS[i][1],
         "columns": width[i] + 5, "first_sub_image": first[i]}
        for i in range(N_LABELS)]
    audits["shape"] = meta["proofs"]["shape"]
    audits["loaders"] = meta["proofs"]["loaders"]

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
    # Two of the repointed literals are adjacent in the pool (the container
    # word and the FIRST word are four bytes apart at 0x036760/0x036764), so a
    # changed range can span more than one allowed window.  Explain a range
    # against the *union* of the allowed windows, byte by byte.
    all_slots = [s for spec in ASSETS for _n, sl in spec["slots"].values() for s in sl]
    allowed = [(block_start, block_end)] + [(s, s + 4) for s in all_slots]

    def covered(a, b, windows):
        cur = a
        for lo, hi in sorted(windows):
            if lo <= cur < hi:
                cur = max(cur, hi)
        return cur >= b

    unexplained = [(a, b) for a, b in merged if not covered(a, b, allowed)]
    if unexplained:
        raise RuntimeError(
            f"WM_UNEXPLAINED_BYTES {[(hex(a), hex(b)) for a, b in unexplained]}")
    literal_windows = [(s, s + 4) for s in all_slots]
    for a, b in merged:
        if block_start <= a < block_end:
            continue
        if not covered(a, b, literal_windows):
            raise RuntimeError(f"WM_NON_LITERAL_CHANGE 0x{a:06X}-0x{b:06X}")
    audits["binary_touch"] = {
        "changed_ranges": len(merged), "executable_bytes_changed": 0,
        "literal_words_repointed": len(all_slots), "code_patches": 0,
        "relocated_block": f"0x{block_start:06X}-0x{block_end:06X}",
        "bytes_relocated": block_end - block_start,
        "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end,
    }

    # Every earlier milestone is untouched.
    for spec in prev.ASSETS:
        for _n, sl in spec["slots"].values():
            for slot in sl:
                if product[slot:slot + 4] != base[slot:slot + 4]:
                    raise RuntimeError(f"WM_GAMEPLAY_GFX_TOUCHED {spec['key']}")
    ui = prev.prev
    for spec in ui.ASSETS:
        for slot in spec["slots"]:
            if product[slot:slot + 4] != base[slot:slot + 4]:
                raise RuntimeError(f"WM_UI_GRAPHICS_TOUCHED {spec['key']}")
    name_entry = ui.prev
    for name, (slots, _pristine) in name_entry.US_LITERALS.items():
        for slot in slots:
            if product[slot:slot + 4] != base[slot:slot + 4]:
                raise RuntimeError(f"WM_NAME_ENTRY_TOUCHED {name}")
    for off, _was, now, _why in name_entry.CODE_PATCHES:
        if product[off:off + len(now)] != now:
            raise RuntimeError(f"WM_NAME_ENTRY_CODE_TOUCHED 0x{off:06X}")
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
        raise RuntimeError("WM_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[3], second[4]) or meta["placement"] != second[2]["placement"]:
        raise RuntimeError("WM_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(
            f"CANONICAL_PRODUCTION_MISMATCH {sha(product)} != {EXPECTED_PRODUCTION}")

    summary = {
        "milestone": "worldmap-labels",
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
