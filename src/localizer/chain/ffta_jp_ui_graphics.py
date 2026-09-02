#! python3
"""Japanese UI graphics for the US ROM -- JP retail assets, drop-in.

Scope
-----

The ROM is text-localized (18 text families, residual Latin = 0) and the
name-entry keyboard is Japanese.  What is left in English is *graphics*: UI
chrome FFTA draws as tiles and sprites rather than through the text engine.
This layer localizes the part of that chrome which the JP retail ROM draws in
Japanese **and** stores in a form the US engine can consume unchanged.

The test a candidate has to pass
--------------------------------

A tile sheet is a **drop-in** only when the JP and US versions have the same
tile count, the same blank-tile indices, and the divergence confined to whole
tiles -- and, crucially, when the screen draws the sheet as **one contiguous
run**.  ``ffta_gfx_codec.tile_diff`` computes the first three; the fourth has
to be established per asset and then *seen* at runtime.

``US 0x083B4214`` (the unit status / equip stat labels) is the counter-example
and the reason this warning is here.  It passes every static part of the test
-- 227 tiles both sides, blank indices identical, tiles 0..106 byte-identical,
one contiguous divergent run 107..182 -- and it is still **wrong**, because
those screens draw each label as its own span at a hard-coded x and a
hard-coded length sized for the English word.  The JP labels have different
lengths, so from the second label on the glyphs sit at different indices and
the panel renders fragmented.  At 240x160 the result looks fine; the breakage
only shows zoomed.  It was built, run, rejected and reverted; see
``build/jp_ui_graphics/<run>/rejected_status_labels/``.

What this layer swaps
---------------------

===========================  ==================  ====================================
US asset                     JP source           what the player sees
===========================  ==================  ====================================
``0x083BB7F0`` (codec B)     ``0x083AE0E0``      the clan/item help bar
                                                 ``START: Item List`` ->
                                                 ``START: アイテムリスト``
                                                 (``[L] CHANGE`` and ``[R] SORT``
                                                 are English in JP retail too and
                                                 travel unchanged)
``0x083C1834`` (A7)          ``0x083B3FF0``      the battle **objective banners**:
                                                 DEFEAT THE BOSS! / DEFEAT ALL
                                                 ENEMIES! / SURVIVE! / DESTROY ALL
                                                 TARGETS! / SNOWBALL FIGHT! ->
                                                 ボスをたおせ! ほか
===========================  ==================  ====================================

Both are drawn as a single unit -- the help bar is one 14-tile row per line,
the banner is one fixed-shape sprite composite -- so the length of the
Japanese inside them never moves anything else.

The A7 container carries its **codec block** with it -- four descriptor bytes
plus the 1024-byte back-reference dictionary that sits after the container and
is addressed as ``base + declared_size`` by ``US 0x08005318``.  Relocating a
container without that block silently produces garbage, not an error
(PROJECT_STATE section 6; the name-entry milestone hit both failure modes).

What this layer deliberately does not do
----------------------------------------

* **No code patch.**  Zero executable bytes change; only relocated data and
  the literal-pool words that point at it.
* **No name-entry change.**  The keyboard, its artwork and its five constants
  are inherited from the previous layer untouched.
* **No re-layout.**  Assets whose JP tile count differs from the US one
  (``0x083AEEA4`` 90/78, ``0x083B53A4`` 68/62, ``0x083BBA38`` 52/46,
  ``0x083BE1F4`` 74/72, ``0x083C4888`` 46/30 ...) and ``0x083B4214`` above
  would need the per-label position and length tables rebuilt as well:
  LOCAL_REBUILD, out of scope.  Assets whose JP version uses a different
  internal sprite arrangement (``0x083C05E8`` MISSION CLEARED!,
  ``0x083BF308`` MISSION CLEAR) would need the OAM composition ported:
  EXPENSIVE_ARCHITECTURAL.
* **Nothing where JP retail is English too.**  The window title bars
  (``US 0x083D5070`` / ``JP 0x083C7B24``: MENU / HELP / EQUIP / LIST / INFO /
  ACTION / RUMORS ...), the mission-briefing serif captions
  (``US 0x083C11BC``: "Mission:" / JP "The Quest for...") and the law screen
  (``US 0x083AD5F8``: FORBIDDEN / RECOMMEND / JP "WORLD LAW") are English in
  the Japanese retail game.  JP retail is the visual authority, so they stay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gfx
import ffta_jp_coverage_audit as coverage
import ffta_jp_name_entry as prev
import ffta_jp_s_text_leaf_repoint as stext

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/jp_ui_graphics"
OUTROM = ROOT / "rom/build/ffta_us_jp_ui_graphics.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_ui_graphics_repeat.gba"

ROM = 0x08000000

# Output of ffta_jp_name_entry -- the feature-frozen RC3 build.
BASELINE = "BE03434D7FA2558836E409EE2225D3DC5BE3E77B9132D49202FCC282A405D27B"
# Terminal artifact of the production chain: the single canonical final-SHA
# authority once this layer is in the chain.
EXPECTED_PRODUCTION = "61C746714157CD5CBD7CFE58852CC225213EEA08BC8AABFEE9877497763038D3"

# The codec-block dictionary the A7 loader addresses.  ``shift`` is 5 for both
# ROMs' banner containers, so the mask is 0x3FF and the block is 4 + 1024
# bytes.  Measured high-water marks are 733 (US) and 823 (JP): inside 1024.
A7_BLOCK_BYTES = 4 + 0x400

# (key, kind, us_offset, jp_offset, literal slots, pristine literal, note)
ASSETS = (
    dict(key="item_list_bar", kind="tiles",
         us=0x003BB7F0, jp=0x003AE0E0,
         slots=(0x0007397C, 0x00087468, 0x0008BDDC),
         note="item/shop help bar START: Item List"),
    dict(key="objective_banners", kind="a7",
         us=0x003C1834, jp=0x003B3FF0,
         slots=(0x0008FA70,),
         note="battle objective banners"),
)

# The Japanese each swapped asset brings, for the report.
JP_TEXT = {
    "item_list_bar": [
        "START: Item List -> START: アイテムリスト (runtime-confirmed); "
        "[L] CHANGE and [R] SORT are English in JP retail and travel unchanged",
    ],
    "objective_banners": [
        "sub 0  DEFEAT THE BOSS!      -> JP retail wording",
        "sub 1  DEFEAT ALL ENEMIES!   -> ひとり残らずたおせ！ (runtime-confirmed)",
        "sub 2  SURVIVE!              -> JP retail wording",
        "sub 3  DESTROY ALL TARGETS!  -> JP retail wording",
        "sub 4  already Japanese in the pristine US ROM, byte-identical, unchanged",
        "sub 5  SNOWBALL FIGHT!       -> JP retail wording",
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

def payload(rom: bytes, kind: str, base: int):
    """The verbatim ROM bytes that make up one asset, and what it decodes to."""
    if kind == "tiles":
        data, end = gfx.decode_b(rom, base)
        return rom[base:end], data, {"compressed_bytes": end - base}
    if kind == "a7":
        count, total, _offs = gfx.container(rom, base)
        high = gfx.container_dictionary_high_water(rom, base)
        if high > A7_BLOCK_BYTES - 4:
            raise RuntimeError(f"UG_A7_DICTIONARY_TOO_SMALL {high}")
        raw = rom[base:base + total + A7_BLOCK_BYTES]
        subs = gfx.container_subs(rom, base)
        meta = {"sub_images": count, "container_bytes": total,
                "codec_block_bytes": A7_BLOCK_BYTES,
                "dictionary_high_water": high,
                "bytes_per_sub_image": len(subs[0])}
        return raw, b"".join(subs), meta
    raise RuntimeError(f"UG_UNKNOWN_KIND {kind}")


def dropin_proof(us_raw: bytes, jp_raw: bytes, spec):
    """Assert the JP asset is an index-for-index replacement for the US one."""
    _pu, du, mu = payload(us_raw, spec["kind"], spec["us"])
    _pj, dj, mj = payload(jp_raw, spec["kind"], spec["jp"])
    info = gfx.tile_diff(du, dj)
    if not info["same_tile_count"]:
        raise RuntimeError(f"UG_TILE_COUNT {spec['key']} "
                           f"{info['tiles_a']} != {info['tiles_b']}")
    if info["differing_tiles"] == 0:
        raise RuntimeError(f"UG_NO_CHANGE {spec['key']}")
    if spec["kind"] == "tiles":
        # A tilemap-indexed sheet: the blank slots are structural, so the two
        # sheets have to agree on them index for index.
        if not info["blank_tiles_match"]:
            raise RuntimeError(f"UG_BLANK_TILES {spec['key']}")
    if spec["kind"] == "a7":
        # A sprite composite of fixed shape: a shorter phrase simply leaves
        # more of the composite's padding cells empty, so the JP blanks need
        # only sit on the same padding lattice the US container already uses.
        us_subs = gfx.container_subs(us_raw, spec["us"])
        jp_subs = gfx.container_subs(jp_raw, spec["jp"])
        blanks = lambda d: {t for t in range(len(d) // 32)
                            if not any(d[t * 32:t * 32 + 32])}
        lattice = set().union(*(blanks(s) for s in us_subs))
        jp_blanks = set().union(*(blanks(s) for s in jp_subs))
        if not jp_blanks <= lattice:
            raise RuntimeError(f"UG_A7_PADDING_LATTICE {spec['key']} "
                               f"{sorted(jp_blanks - lattice)}")
        info["us_padding_lattice"] = sorted(lattice)
        info["jp_padding_cells"] = sorted(jp_blanks)
        info["per_sub_blank_cells"] = {
            i: {"us": sorted(blanks(us_subs[i])), "jp": sorted(blanks(jp_subs[i]))}
            for i in range(len(us_subs))}
        if mu["sub_images"] != mj["sub_images"]:
            raise RuntimeError(f"UG_A7_SUB_COUNT {spec['key']}")
        if mu["bytes_per_sub_image"] != mj["bytes_per_sub_image"]:
            raise RuntimeError(f"UG_A7_SUB_SIZE {spec['key']}")
        for name, rom, base in (("us", us_raw, spec["us"]), ("jp", jp_raw, spec["jp"])):
            _c, total, _o = gfx.container(rom, base)
            mode, shift, size, _dic, dlen = gfx.codec_a_descriptor(rom, base + total)
            if (mode, shift) != (0, 5) or dlen != 0x400:
                raise RuntimeError(f"UG_A7_DESCRIPTOR {spec['key']} {name}")
            info[f"{name}_codec_descriptor"] = {
                "mode": mode, "shift": shift, "output_size": size,
                "dictionary_bytes": dlen}
    info.update({f"us_{k}": v for k, v in mu.items()})
    info.update({f"jp_{k}": v for k, v in mj.items()})
    return info


# ------------------------------------------------------------------- build ---

def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"UG_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]

    jp_raw = JP.read_bytes()
    us_raw = US.read_bytes()
    raw = bytearray(base)

    # Every literal we are about to rewrite must still hold its pristine value,
    # both in the pristine US ROM and in the baseline we build on.
    for spec in ASSETS:
        want = ROM + spec["us"]
        for slot in spec["slots"]:
            for name, blob in (("US", us_raw), ("BASE", raw)):
                if struct.unpack_from("<I", blob, slot)[0] != want:
                    raise RuntimeError(
                        f"UG_LITERAL_DRIFT_{name} {spec['key']} 0x{slot:06X}")
        # and no other aligned word in the ROM points at it
        found = [i for i in range(0, len(us_raw) - 3, 4)
                 if struct.unpack_from("<I", us_raw, i)[0] == want]
        if sorted(found) != sorted(spec["slots"]):
            raise RuntimeError(f"UG_REFERENCE_SET {spec['key']} "
                               f"{[hex(f) for f in found]}")

    proofs = {spec["key"]: dropin_proof(us_raw, jp_raw, spec) for spec in ASSETS}

    cursor = block_start = stext.align(prev_block_end, 4)
    placement = {}
    for spec in ASSETS:
        blob, _decoded, _meta = payload(jp_raw, spec["kind"], spec["jp"])
        raw[cursor:cursor + len(blob)] = blob
        placement[spec["key"]] = {
            "jp_rom_offset": f"0x{spec['jp']:06X}",
            "jp_cpu_address": f"0x{ROM + spec['jp']:08X}",
            "us_rom_offset": f"0x{cursor:06X}",
            "cpu_pointer": f"0x{ROM + cursor:08X}",
            "bytes": len(blob),
            "kind": spec["kind"],
            "repointed_literals": [f"0x{s:06X}" for s in spec["slots"]],
        }
        for slot in spec["slots"]:
            raw[slot:slot + 4] = (ROM + cursor).to_bytes(4, "little")
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("UG_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("UG_BLOCK_OUTSIDE_TAIL")
    if block_start < prev_block_end:
        raise RuntimeError("UG_BLOCK_OVERLAPS_PREVIOUS")

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
        want, _d, _m = payload(jp_raw, spec["kind"], spec["jp"])
        got = product[here:here + len(want)]
        if got != want:
            raise RuntimeError(f"UG_ASSET_NOT_VERBATIM {spec['key']}")

        # the relocated asset decodes, in the product, to the JP artwork
        _blob, decoded_here, _m2 = payload(product, spec["kind"], here)
        _blob, decoded_jp, _m3 = payload(jp_raw, spec["kind"], spec["jp"])
        if decoded_here != decoded_jp:
            raise RuntimeError(f"UG_ASSET_DECODE_MISMATCH {spec['key']}")

        # every literal now points here, and none still points at the US asset
        old = ROM + spec["us"]
        new = ROM + here
        for slot in spec["slots"]:
            if struct.unpack_from("<I", product, slot)[0] != new:
                raise RuntimeError(f"UG_LITERAL_NOT_REPOINTED {spec['key']}")
        stale = [i for i in range(0, len(product) - 3, 4)
                 if struct.unpack_from("<I", product, i)[0] == old]
        if stale:
            raise RuntimeError(f"UG_STALE_REFERENCE {spec['key']} "
                               f"{[hex(s) for s in stale]}")
        # the pristine US asset is still in the ROM, simply unreferenced
        us_blob, _d, _m = payload(us_raw, spec["kind"], spec["us"])
        if product[spec["us"]:spec["us"] + len(us_blob)] != us_blob:
            raise RuntimeError(f"UG_US_ASSET_DISTURBED {spec['key']}")

        audits["assets"][spec["key"]] = {
            "note": spec["note"],
            "japanese": JP_TEXT[spec["key"]],
            "placement": p,
            "dropin_proof": meta["proofs"][spec["key"]],
            "pristine_us_asset_retained_unreferenced": True,
        }

    # every byte that changed is explained
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
        allowed += [(s, s + 4) for s in spec["slots"]]
    unexplained = [(a, b) for a, b in merged
                   if not any(lo <= a and b <= hi for lo, hi in allowed)]
    if unexplained:
        raise RuntimeError(
            f"UG_UNEXPLAINED_BYTES {[(hex(a), hex(b)) for a, b in unexplained]}")

    exe = [(a, b) for a, b in merged if not (block_start <= a < block_end)]
    for a, b in exe:
        # a repointed literal changes at most its four bytes; the high byte of
        # the CPU address is 0x08 on both sides, so the run is usually shorter
        if not any(s <= a and b <= s + 4 for spec in ASSETS for s in spec["slots"]):
            raise RuntimeError(f"UG_NON_LITERAL_CHANGE 0x{a:06X}-0x{b:06X}")
    audits["binary_touch"] = {
        "changed_ranges": len(merged),
        "executable_bytes_changed": 0,
        "literal_words_repointed": sum(len(s["slots"]) for s in ASSETS),
        "relocated_block": f"0x{block_start:06X}-0x{block_end:06X}",
        "bytes_relocated": block_end - block_start,
        "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end,
        "unexplained_executable_changes": 0,
        "unexplained_data_changes": 0,
    }

    # the name-entry milestone is untouched by this layer
    ne = prev.US_LITERALS
    for name, (slots, _pristine) in ne.items():
        for slot in slots:
            if product[slot:slot + 4] != base[slot:slot + 4]:
                raise RuntimeError(f"UG_NAME_ENTRY_TOUCHED {name}")
    for off, _was, now, _why in prev.CODE_PATCHES:
        if product[off:off + len(now)] != now:
            raise RuntimeError(f"UG_NAME_ENTRY_CODE_TOUCHED 0x{off:06X}")
    audits["name_entry_unchanged"] = True
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
        raise RuntimeError("UG_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[3], second[4]) or meta["placement"] != second[2]["placement"]:
        raise RuntimeError("UG_LAYOUT_NONDETERMINISTIC")

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
        "milestone": "jp-ui-graphics",
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
