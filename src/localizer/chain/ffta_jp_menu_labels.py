#! python3
"""Menu label sheets: the Japanese words JP retail draws on them.

Two codec-B sheets were deferred by the gameplay-gfx-final milestone as
MODERATE, on the grounds that "their JP twins have a **different tile count**,
so no verbatim block exists to move: the sheet has to be spliced and
re-encoded *and* every panel table that indexes it has to be found and
ported".

The first half of that is right and the second half is not, and the difference
is what makes this layer cheap.  Rendering both sides tile by tile shows the
two sheets are **index-aligned**: every tile below the divergent run is
byte-identical at the *same index*, and the JP sheet is simply shorter because
it stops after its last label.  So the port is a pixel splice at unchanged
indices -- no tilemap moves, no index constant changes, and the RC5 hazard
(the JP sheet packing its tiles in the JP panel's cell order) cannot arise.

===============  =======  =======  =====================================
US sheet         US       JP       divergent tiles (identical indices)
===============  =======  =======  =====================================
``0x083B53A4``   68       62       24, 28..31 and 55..61
clan menu strip                    ``MISSION`` -> ``QUEST``;
                                   ``:Sort Items`` -> ``：アイテムソート``
``0x083AEEA4``   90       78       55..77
clan-guide panel                   ``CLEARED`` -> ``CLEAR``;
                                   ``Battle(s)`` -> ``Battle``;
                                   ``Suspended:`` -> ``出場停止：``
===============  =======  =======  =====================================

Everything else on both sheets -- ``CLAN FUNDS`` ``Info`` ``Remove`` ``Lv``
``RANK`` ``PAGE`` ``in Jail`` and the digit set -- is English in JP retail
too, and is left alone by construction: this layer copies the JP bytes only
where the two sheets already disagree.

``START: Sort Items`` is the reason this is worth a layer.  Its sibling on the
same help bar, ``START: Item List``, has been Japanese since RC4
(``0x083BB7F0``), so the item menu currently shows one Japanese help bar and
one English one.

Each spliced sheet is re-encoded with ``gfx.encode_b``, relocated into the
tail, and the literal-pool words that point at it are rewritten.  **Zero
executable bytes change.**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gfx
import ffta_jp_coverage_audit as coverage
import ffta_jp_worldmap_labels as prev
import ffta_jp_s_text_leaf_repoint as stext

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/menu_labels"
OUTROM = ROOT / "rom/build/ffta_us_jp_menu_labels.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_menu_labels_repeat.gba"

ROM = 0x08000000
BASELINE = "B466D7370975B64C1D23F05D82F2E98C013EBA996FCFC2B7E0BBC3DAB94D6F54"
EXPECTED_PRODUCTION = "F2573B1F4E5DB3762B57F36155CC1AE7E32E5328B3694C072CFFC3BD782F8D4D"

ASSETS = (
    dict(key="clan_menu_strip", us=0x003B53A4, jp=0x003A8144,
         us_tiles=68, jp_tiles=62,
         slots=(0x00061770, 0x000655C8, 0x00087480, 0x0013800C),
         japanese=["START: Sort Items -> START：アイテムソート",
                   "MISSION -> QUEST (JP retail's own wording)"],
         note="clan / item help strip"),
    dict(key="clan_guide_panel", us=0x003AEEA4, jp=0x003A1D44,
         us_tiles=90, jp_tiles=78,
         slots=(0x00022A80, 0x00031340, 0x0004DD38, 0x0005C558,
                0x0006EAE8, 0x0008C140),
         japanese=["Suspended: -> 出場停止：",
                   "CLEARED -> CLEAR, Battle(s) -> Battle "
                   "(JP retail's own wording)"],
         note="clan-guide / rank panel"),
)


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def tiles(data: bytes):
    return [data[i * 32:(i + 1) * 32] for i in range(len(data) // 32)]


def splice(us_raw: bytes, jp_raw: bytes, spec):
    """The shipped sheet: the US sheet with every divergent tile taken from JP.

    Also returns the proof that the two sheets are index-aligned, which is the
    whole reason this is safe.
    """
    du, end_u = gfx.decode_b(us_raw, spec["us"])
    dj, _end_j = gfx.decode_b(jp_raw, spec["jp"])
    tu, tj = tiles(du), tiles(dj)
    if len(tu) != spec["us_tiles"] or len(tj) != spec["jp_tiles"]:
        raise RuntimeError(f"ML_TILE_COUNT {spec['key']} {len(tu)} {len(tj)}")
    if len(tj) >= len(tu):
        raise RuntimeError(f"ML_JP_NOT_SHORTER {spec['key']}")
    diverge = [i for i in range(len(tj)) if tu[i] != tj[i]]
    if not diverge:
        raise RuntimeError(f"ML_NO_DIVERGENCE {spec['key']}")
    # index alignment: the run below the first divergence must be identical,
    # and the blank tiles of the JP sheet must be blank in the US sheet too.
    first = diverge[0]
    if tu[:first] != tj[:first]:
        raise RuntimeError(f"ML_PREFIX_NOT_IDENTICAL {spec['key']}")
    for i in range(len(tj)):
        if (not any(tj[i])) != (not any(tu[i])) and i not in diverge:
            raise RuntimeError(f"ML_BLANK_MISMATCH {spec['key']} {i}")
    out = bytearray(du)
    for i in diverge:
        out[i * 32:(i + 1) * 32] = tj[i]
    decoded = bytes(out)
    blob = gfx.encode_b(decoded)
    back, end = gfx.decode_b(blob, 0)
    if back != decoded or end != len(blob):
        raise RuntimeError(f"ML_ENCODE_ROUNDTRIP {spec['key']}")
    proof = {
        "us_tiles": len(tu), "jp_tiles": len(tj),
        "identical_below_first_divergence": first,
        "divergent_tiles": diverge,
        "us_only_tiles_kept": list(range(len(tj), len(tu))),
        "us_compressed_bytes": end_u - spec["us"],
        "shipped_compressed_bytes": len(blob),
        "differs_from_us_only_in": diverge,
        "equals_jp_on_every_divergent_tile": True,
    }
    return blob, decoded, proof


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"ML_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]

    jp_raw = JP.read_bytes()
    us_raw = US.read_bytes()
    raw = bytearray(base)

    proofs = {}
    for spec in ASSETS:
        want = ROM + spec["us"]
        for name, blob in (("US", us_raw), ("BASE", raw)):
            for slot in spec["slots"]:
                if struct.unpack_from("<I", blob, slot)[0] != want:
                    raise RuntimeError(
                        f"ML_LITERAL_DRIFT_{name} {spec['key']} 0x{slot:06X}")
        found = [i for i in range(0, len(us_raw) - 3, 4)
                 if struct.unpack_from("<I", us_raw, i)[0] == want]
        if sorted(found) != sorted(spec["slots"]):
            raise RuntimeError(f"ML_REFERENCE_SET {spec['key']} "
                               f"{[hex(f) for f in found]}")
        _blob, _dec, proofs[spec["key"]] = splice(us_raw, jp_raw, spec)

    cursor = block_start = stext.align(prev_block_end, 4)
    placement = {}
    for spec in ASSETS:
        blob, _dec, _p = splice(us_raw, jp_raw, spec)
        raw[cursor:cursor + len(blob)] = blob
        placement[spec["key"]] = {
            "us_rom_offset": f"0x{cursor:06X}",
            "cpu_pointer": f"0x{ROM + cursor:08X}",
            "bytes": len(blob),
            "repointed_literals": [f"0x{s:06X}" for s in spec["slots"]],
        }
        for slot in spec["slots"]:
            raw[slot:slot + 4] = (ROM + cursor).to_bytes(4, "little")
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("ML_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("ML_BLOCK_OUTSIDE_TAIL")
    if block_start < prev_block_end:
        raise RuntimeError("ML_BLOCK_OVERLAPS_PREVIOUS")
    return (bytes(raw), base, {"placement": placement, "proofs": proofs},
            block_start, block_end)


def validate(product, base, meta, block_start, block_end):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    audits = {"assets": {}}
    all_slots = []
    for spec in ASSETS:
        want, decoded, proof = splice(us_raw, jp_raw, spec)
        here = int(meta["placement"][spec["key"]]["us_rom_offset"], 16)
        if product[here:here + len(want)] != want:
            raise RuntimeError(f"ML_ASSET_NOT_WRITTEN {spec['key']}")
        back, _e = gfx.decode_b(product, here)
        if back != decoded:
            raise RuntimeError(f"ML_PRODUCT_DECODE {spec['key']}")
        for slot in spec["slots"]:
            if struct.unpack_from("<I", product, slot)[0] != ROM + here:
                raise RuntimeError(f"ML_LITERAL_NOT_REPOINTED {spec['key']}")
        stale = [i for i in range(0, len(product) - 3, 4)
                 if struct.unpack_from("<I", product, i)[0] == ROM + spec["us"]]
        if stale:
            raise RuntimeError(f"ML_STALE_REFERENCE {spec['key']} "
                               f"{[hex(s) for s in stale]}")
        us_blob, _e = gfx.decode_b(us_raw, spec["us"])
        n = len(gfx.encode_b(us_blob))
        if product[spec["us"]:spec["us"] + 4] != us_raw[spec["us"]:spec["us"] + 4]:
            raise RuntimeError(f"ML_US_ASSET_DISTURBED {spec['key']}")
        all_slots += list(spec["slots"])
        audits["assets"][spec["key"]] = {
            "note": spec["note"], "japanese": spec["japanese"],
            "us": f"0x{ROM + spec['us']:08X}", "jp": f"0x{ROM + spec['jp']:08X}",
            "placement": meta["placement"][spec["key"]], "proof": proof,
            "pristine_us_asset_retained_unreferenced": True,
        }

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
    allowed = [(block_start, block_end)] + [(s, s + 4) for s in all_slots]

    def covered(a, b, windows):
        cur = a
        for lo, hi in sorted(windows):
            if lo <= cur < hi:
                cur = max(cur, hi)
        return cur >= b

    bad = [(a, b) for a, b in merged if not covered(a, b, allowed)]
    if bad:
        raise RuntimeError(f"ML_UNEXPLAINED_BYTES {[(hex(a), hex(b)) for a, b in bad]}")
    literal_windows = [(s, s + 4) for s in all_slots]
    for a, b in merged:
        if block_start <= a < block_end:
            continue
        if not covered(a, b, literal_windows):
            raise RuntimeError(f"ML_NON_LITERAL_CHANGE 0x{a:06X}-0x{b:06X}")

    for spec in prev.ASSETS:
        for _n, sl in spec["slots"].values():
            for slot in sl:
                if product[slot:slot + 4] != base[slot:slot + 4]:
                    raise RuntimeError(f"ML_WORLDMAP_TOUCHED {spec['key']}")
    audits["binary_touch"] = {
        "changed_ranges": len(merged), "executable_bytes_changed": 0,
        "literal_words_repointed": len(all_slots), "code_patches": 0,
        "relocated_block": f"0x{block_start:06X}-0x{block_end:06X}",
        "bytes_relocated": block_end - block_start,
        "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end,
    }
    audits["earlier_milestones_unchanged"] = True
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
        raise RuntimeError("ML_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[3], second[4]) or meta["placement"] != second[2]["placement"]:
        raise RuntimeError("ML_LAYOUT_NONDETERMINISTIC")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, bs, be)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    summary = {
        "milestone": "menu-labels",
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
