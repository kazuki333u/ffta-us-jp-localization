#! python3
"""World-map month names: the Japanese words, drawn from the JP font.

Why this layer is not a verbatim asset move
-------------------------------------------

The two ROMs draw the month differently, and this is the reason every earlier
pass called it ARCHITECTURAL:

* **US** pre-renders each month as artwork.  ``US 0x083ACD68`` is an A7
  container of 30 sub-images of 8x16; six of them are one month, and the
  object list ``US 0x083919B0`` draws them as a 32x16 object at OBJ tile 94
  plus a 16x16 object at tile 102 -- 48x16 in all.  Sub-images 4 and 5 of
  every group are byte-identical: they are the shared ``oon`` of
  ``Kingmoon`` / ``Madmoon`` / ``Sagemoon`` / ``Huntmoon`` / ``Bardmoon``.
* **JP** has no month artwork at all.  ``JP 0x08384E7A`` is five ordinary
  three-token text records -- two ``0x8000|slot`` glyphs and the ``0x4063``
  terminator -- and ``JP 0x08034C44`` renders them through the game's own
  sprite text renderer into OBJ tile 94.  The JP object list
  ``JP 0x08384EF4`` therefore has **one** object, 32x16, and nothing at tile
  102.  The words are 王者 / 猛者 / 賢者 / 狩人 / 才人.

So there is no JP block to move.  What this layer does instead is render the
JP retail words **from the JP font**, at the size and in the colours the
game's own renderer uses, and write them into the US container:

* the font is 16x16 4bpp (``ffta_sect`` shape ``(4, 8, 16, 2)``), so two
  glyphs are exactly 32x16 -- the first four sub-images of a group;
* the renderer writes font values through unchanged as palette indices.  That
  is measured, not assumed: OBJ tile 110 of a live world-map capture is the
  day number the same renderer drew, and comparing it with the font bitmap of
  the digit gives value ``v`` -> palette index ``v`` for 1, 2 and 3, with the
  same pixel counts (``build/worldmap_labels/<run>/renderer_colours.py``);
* sub-images 4 and 5 -- the shared ``oon`` -- are blanked, because the
  Japanese word ends at 32 pixels.

The container is re-encoded with ``gfx.encode_a`` (literal runs only) and
relocated; the two literal-pool words that point at it are rewritten.  **Zero
executable bytes change.**

What this layer deliberately leaves alone
-----------------------------------------

The word ``day`` between the month and the number is **not** in this
container and not in OAM: it is on the background layer.  Reordering the line
into the JP form would need that BG asset and the object list as well.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gfx
import ffta_jp_coverage_audit as coverage
import ffta_jp_menu_labels as prev
import ffta_jp_worldmap_labels as wm
import ffta_jp_s_text_leaf_repoint as stext
import ffta_sect

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/month_names"
OUTROM = ROOT / "rom/build/ffta_us_jp_month_names.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_month_names_repeat.gba"

ROM = 0x08000000
BASELINE = "F2573B1F4E5DB3762B57F36155CC1AE7E32E5328B3694C072CFFC3BD782F8D4D"
EXPECTED_PRODUCTION = "B80F206732635D3F3913CEBAA8F2E72498887D8AC65FD6B304E499C216E54281"

US_CONTAINER = 0x003ACD68
JP_RECORDS = 0x08384E7A
N_MONTHS = 5
SUBS_PER_MONTH = 6
NAME_SUBS = 4                     # 32x16 = the two JP glyphs
SUB_BYTES = 64
# Two literal-pool words point at the month sheet, and only ONE of them is the
# world-map date.  ``0x00057C80`` belongs to a second consumer
# (``US 0x08057C20``) that decodes the whole container and copies its first 15
# sub-images to OBJ VRAM ``0x06015C00`` -- it uses those tiles as ordinary HUD
# sprites, not as month names.  Repointing it too changed the clan-formation
# screen's left/right arrows, which the regression caught; that slot is
# therefore left pointing at the pristine US sheet, which this layer does not
# move.  See PRISTINE_SLOTS.
CONTAINER_SLOTS = (0x00035DD0,)
PRISTINE_SLOTS = (0x00057C80,)

WORDS = ("王者", "猛者", "賢者", "狩人", "才人")
ENGLISH = ("Kingmoon", "Madmoon", "Sagemoon", "Huntmoon", "Bardmoon")


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ------------------------------------------------------------------ render ---

def month_slots(jp_raw: bytes):
    """The two font slots of each JP month record, and the record bytes."""
    out = []
    for i in range(N_MONTHS):
        o = JP_RECORDS - ROM + i * 8
        toks = struct.unpack_from(">4H", jp_raw, o)
        if toks[2] != 0x4063 or toks[3] != 0x0000:
            raise RuntimeError(f"MN_RECORD_SHAPE {i} {toks}")
        if not all(t & 0x8000 for t in toks[:2]):
            raise RuntimeError(f"MN_RECORD_TOKENS {i} {toks}")
        out.append(([t & 0x7FFF for t in toks[:2]], jp_raw[o:o + 8]))
    return out


def font_glyph(font, slot):
    g = [list(r) for r in font.gen_char(slot)]
    if len(g) != 16 or any(len(r) != 16 for r in g):
        raise RuntimeError(f"MN_GLYPH_SHAPE {slot}")
    if any(v > 3 for r in g for v in r):
        raise RuntimeError(f"MN_GLYPH_VALUE {slot}")
    return g


def name_tiles(font, slots) -> bytes:
    """Two 16x16 glyphs as eight 4bpp tiles in 1D OBJ order (4 across, 4)."""
    px = [[0] * 32 for _ in range(16)]
    for k, s in enumerate(slots):
        g = font_glyph(font, s)
        for y in range(16):
            px[y][k * 16:(k + 1) * 16] = g[y]
    out = bytearray()
    for ty in range(2):
        for tx in range(4):
            for y in range(8):
                for x in range(0, 8, 2):
                    out.append((px[ty * 8 + y][tx * 8 + x] & 0xF)
                               | ((px[ty * 8 + y][tx * 8 + x + 1] & 0xF) << 4))
    if len(out) != NAME_SUBS * SUB_BYTES:
        raise RuntimeError("MN_NAME_TILE_LENGTH")
    return bytes(out)


def encode_a_literal(subs) -> bytes:
    """An A7 container whose sub-images are literal runs only.

    Codec A's literal opcode is ``0x80 | (n - 1)`` followed by ``n`` bytes.
    Emitting nothing else means no back-reference ever reads the dictionary,
    so the block is self-contained -- but the 1,024-byte dictionary is still
    written, because ``US 0x08005318`` computes its address unconditionally.
    """
    count = len(subs)
    head = bytearray(b"A7" + struct.pack(">H", count))
    body = bytearray()
    offs = []
    for s in subs:
        if len(s) != SUB_BYTES:
            raise RuntimeError("MN_SUB_LENGTH")
        offs.append(8 + 4 * count + len(body))
        body += bytes((0x80 | (SUB_BYTES - 1),)) + s
    total = 8 + 4 * count + len(body)
    head += struct.pack(">I", total)
    for o in offs:
        head += struct.pack(">I", o)
    blob = bytes(head) + bytes(body)
    if len(blob) != total:
        raise RuntimeError("MN_CONTAINER_TOTAL")
    # codec block: mode 0, shift 5, u16be sub-image size, then the dictionary
    return blob + bytes((0, 5)) + struct.pack(">H", SUB_BYTES) + bytes(1024)


def shipped_container(us_raw: bytes, jp_raw: bytes, font):
    us_subs = gfx.container_subs(us_raw, US_CONTAINER)
    if len(us_subs) != N_MONTHS * SUBS_PER_MONTH:
        raise RuntimeError(f"MN_US_SUB_COUNT {len(us_subs)}")
    if any(len(s) != SUB_BYTES for s in us_subs):
        raise RuntimeError("MN_US_SUB_SIZE")
    tail = us_subs[NAME_SUBS:SUBS_PER_MONTH]
    for g in range(1, N_MONTHS):
        o = g * SUBS_PER_MONTH + NAME_SUBS
        if us_subs[o:o + 2] != tail:
            raise RuntimeError(f"MN_SHARED_OON {g}")
    out, proof = [], []
    for g, (slots, rec) in enumerate(month_slots(jp_raw)):
        blk = name_tiles(font, slots)
        for k in range(NAME_SUBS):
            out.append(blk[k * SUB_BYTES:(k + 1) * SUB_BYTES])
        for _ in range(SUBS_PER_MONTH - NAME_SUBS):
            out.append(bytes(SUB_BYTES))
        proof.append({
            "index": g, "us_retail": ENGLISH[g], "jp_retail": WORDS[g],
            "jp_record": f"0x{JP_RECORDS + g * 8:08X}",
            "jp_record_bytes": rec.hex().upper(),
            "font_slots": [f"0x{s:04X}" for s in slots],
            "name_sub_images": NAME_SUBS,
            "blanked_sub_images": SUBS_PER_MONTH - NAME_SUBS,
        })
    if len(out) != len(us_subs):
        raise RuntimeError("MN_SUB_COUNT_CHANGED")
    return encode_a_literal(out), out, proof


# ------------------------------------------------------------------- build ---

def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"MN_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]

    jp_raw = JP.read_bytes()
    us_raw = US.read_bytes()
    raw = bytearray(base)

    want = ROM + US_CONTAINER
    for name, blob in (("US", us_raw), ("BASE", raw)):
        for slot in CONTAINER_SLOTS:
            if struct.unpack_from("<I", blob, slot)[0] != want:
                raise RuntimeError(f"MN_LITERAL_DRIFT_{name} 0x{slot:06X}")
    for slot in PRISTINE_SLOTS:
        if struct.unpack_from("<I", raw, slot)[0] != want:
            raise RuntimeError(f"MN_PRISTINE_SLOT_DRIFT 0x{slot:06X}")
    found = [i for i in range(0, len(us_raw) - 3, 4)
             if struct.unpack_from("<I", us_raw, i)[0] == want]
    if sorted(found) != sorted(CONTAINER_SLOTS + PRISTINE_SLOTS):
        raise RuntimeError(f"MN_REFERENCE_SET {[hex(f) for f in found]}")

    font = ffta_sect.load_rom_jp(str(JP)).tabs["font"]
    blob, subs, proof = shipped_container(us_raw, jp_raw, font)
    back = gfx.container_subs(blob, 0)
    if [bytes(s) for s in back] != [bytes(s) for s in subs]:
        raise RuntimeError("MN_ENCODE_ROUNDTRIP")
    if gfx.container_dictionary_high_water(blob, 0) != 0:
        raise RuntimeError("MN_DICTIONARY_USED")

    cursor = block_start = stext.align(prev_block_end, 4)
    raw[cursor:cursor + len(blob)] = blob
    placement = {
        "us_rom_offset": f"0x{cursor:06X}", "cpu_pointer": f"0x{ROM + cursor:08X}",
        "bytes": len(blob), "sub_images": len(subs),
        "repointed_literals": [f"0x{s:06X}" for s in CONTAINER_SLOTS],
    }
    for slot in CONTAINER_SLOTS:
        raw[slot:slot + 4] = (ROM + cursor).to_bytes(4, "little")
    block_end = cursor = stext.align(cursor + len(blob), 4)

    if len(raw) != len(base):
        raise RuntimeError("MN_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("MN_BLOCK_OUTSIDE_TAIL")
    if block_start < prev_block_end:
        raise RuntimeError("MN_BLOCK_OVERLAPS_PREVIOUS")
    return (bytes(raw), base, {"placement": placement, "months": proof},
            block_start, block_end)


def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    font = ffta_sect.load_rom_jp(str(JP)).tabs["font"]
    want_blob, want_subs, _p = shipped_container(us_raw, jp_raw, font)
    here = int(meta["placement"]["us_rom_offset"], 16)
    if product[here:here + len(want_blob)] != want_blob:
        raise RuntimeError("MN_ASSET_NOT_WRITTEN")
    got = gfx.container_subs(product, here)
    if [bytes(s) for s in got] != [bytes(s) for s in want_subs]:
        raise RuntimeError("MN_PRODUCT_DECODE")
    for slot in CONTAINER_SLOTS:
        if struct.unpack_from("<I", product, slot)[0] != ROM + here:
            raise RuntimeError("MN_LITERAL_NOT_REPOINTED")
    remaining = [i for i in range(0, len(product) - 3, 4)
                 if struct.unpack_from("<I", product, i)[0] == ROM + US_CONTAINER]
    if sorted(remaining) != sorted(PRISTINE_SLOTS):
        raise RuntimeError(f"MN_STALE_REFERENCE {[hex(s) for s in remaining]}")
    us_len, _m = wm.container_extent(us_raw, US_CONTAINER)
    if product[US_CONTAINER:US_CONTAINER + us_len] != us_raw[US_CONTAINER:US_CONTAINER + us_len]:
        raise RuntimeError("MN_US_ASSET_DISTURBED")

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
    allowed = [(block_start, block_end)] + [(s, s + 4) for s in CONTAINER_SLOTS]

    def covered(a, b, windows):
        cur = a
        for lo, hi in sorted(windows):
            if lo <= cur < hi:
                cur = max(cur, hi)
        return cur >= b

    bad = [(a, b) for a, b in merged if not covered(a, b, allowed)]
    if bad:
        raise RuntimeError(f"MN_UNEXPLAINED_BYTES {[(hex(a), hex(b)) for a, b in bad]}")

    for spec in prev.ASSETS:
        for slot in spec["slots"]:
            if product[slot:slot + 4] != base[slot:slot + 4]:
                raise RuntimeError(f"MN_MENU_LABELS_TOUCHED {spec['key']}")
    for spec in prev.prev.ASSETS:
        for _n, sl in spec["slots"].values():
            for slot in sl:
                if product[slot:slot + 4] != base[slot:slot + 4]:
                    raise RuntimeError(f"MN_WORLDMAP_TOUCHED {spec['key']}")
    return {
        "months": meta["months"],
        "placement": meta["placement"],
        "encoder": "literal-run codec A; dictionary written but never read",
        "second_consumer_left_on_the_pristine_sheet": {
            "slot": [f"0x{s:06X}" for s in PRISTINE_SLOTS],
            "consumer": "0x08057C20",
            "why": "it copies the first 15 sub-images to OBJ VRAM 0x06015C00 "
                   "and draws them as HUD sprites, not as month names; "
                   "repointing it changed the clan-formation arrows",
        },
        "binary_touch": {
            "changed_ranges": len(merged), "executable_bytes_changed": 0,
            "literal_words_repointed": len(CONTAINER_SLOTS), "code_patches": 0,
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
        raise RuntimeError("MN_BUILD_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "month-names",
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
