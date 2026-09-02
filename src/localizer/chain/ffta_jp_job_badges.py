#! python3
"""RC22 - the job badges: JP retail's own 44-badge sheet, drop-in.

What this fixes
---------------

A **job badge** is the 32x16 graphic that names a job everywhere the game has
to show one: a face on the left, a short label on the right.  US retail draws
a three-letter Latin abbreviation in that label (``SLD`` ``PAL`` ``FGT``
``THF`` ``NIN`` ``WHT`` ``BLK`` ...).  **JP retail draws a Japanese one**
(``ソ`` ``パ`` ``闘`` ``シ`` ``忍`` ``白`` ``黒`` ...) - a different, JP-native
asset that this project had never transferred, so every badge in the
production ROM was still English.

The two assets are structurally identical and the swap is therefore the
``CHEAP_NATIVE_SWAP`` class of ``ffta_gfx_inventory``: both are ``A7``
containers of **44 sub-images x 8 tiles** (32x16 px, 256 bytes decoded),
both compiled with codec-A descriptor ``mode 0 / shift 5`` and a 1024-byte
back-reference dictionary, and the JP art uses the same palette indices as the
US art - the faces and the badge frame are the same pixels in the same colour
slots, only the label glyphs differ (and sub 43, US ``EXPERT`` / JP
``SPECIAL``).  Sub 42 (``MONSTER``) is byte-identical in the two ROMs.

===========================  ==================  ==============================
US asset                     JP source           what the player sees
===========================  ==================  ==============================
``0x083B9004`` (A7, 44x8)    ``0x083AB784``      every job badge: ``SLD`` ->
                                                 ``ソ``, ``WHT`` -> ``白``,
                                                 ``MOG`` -> ``モ``, ...
===========================  ==================  ==============================

How the engine reads it
-----------------------

One function owns the sheet in each ROM, and both are the same function::

    US 0x080CB9E0 (JP 0x080C2B28)
        r0 = destination, r1 = job id
        if r1 == 0x4D:        r1 = 0x2D      <- US-only arm
        elif r1 >  0x52:      r1 = 0x2D      -> sub 43  EXPERT / SPECIAL
        elif r1 >  0x2B:      r1 = 0x2C      -> sub 42  MONSTER
        r0 = &container                       <- the only reference in the ROM
        r2 = r1 - 2                           -> sub = job id - 2
        bl  decode_sub(container, dest, sub)  (US 0x08005318 / JP 0x0800535C)

so the badge index is **the job id minus two**, with no lookup table in
between, and jobs 2..43 are subs 0..41.  The pointer word the function loads
(``US 0x000CBA10`` / ``JP 0x000C2B54``) is the *only* aligned word in either
ROM that holds the container's address - the reference set is a singleton, so
repointing it moves every consumer at once.  Seventeen US call sites reach
that function (twelve in JP retail); the surfaces they draw include the shop's
job list, the ability screen, unit info, job change and the formation screen.

Why the earlier reading was wrong
---------------------------------

RC21 answered the same user report ("the job icons are still the US ones")
with NOT_A_DEFECT, on a runtime match that named ``US 0x083BBC7C``,
``0x083EAB98`` and ``0x083ADB08``.  Those three are the **helmet item icons**,
the small-label sheet and the A/R/S/C category icons - all genuinely
byte-identical to JP retail, and none of them a job badge.  The badge sheet is
this one, and ``ffta_gfx_inventory.KNOWN`` mislabelled it "world-map location
icons -- pictograms, no lettering", which is why it sat in
``NON_TEXT_DECORATIVE`` and was never looked at.  Both labels are corrected in
that module by this milestone.

The evidence, end to end, is in ``docs/RC22_JOB_BADGES.md``.

What this layer does not do
---------------------------

* **No code patch.**  Zero executable bytes change: one relocated data blob and
  one literal-pool word.
* **No re-drawing.**  Every pixel comes verbatim from the pristine JP ROM;
  nothing is generated, traced or hand-edited.
* **No mapping change.**  ``sub = job id - 2`` is JP retail's own mapping and
  the US build already uses it unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_gfx_codec as gfx
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_visual_defects as prev

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/job_badges"
OUTROM = ROOT / "rom/build/ffta_us_jp_job_badges.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_job_badges_repeat.gba"

ROM = 0x08000000

# Output of ffta_jp_visual_defects -- the RC21 production ROM.
BASELINE = "F3B0F990B416C0AEFBEB44EE468A16B2D7FA0FDDDA19F9E7CF5E99F91EEEE49B"
# Terminal artifact of the production chain: this layer is now the last one, so
# its output IS the canonical production ROM.
EXPECTED_PRODUCTION = "6A9A686F1D281AEF0B5F81A337EE6339C8B862EC7732A23329B08F9EF8969D3D"

# The codec block the A7 loader addresses as ``base + declared_size``:
# four descriptor bytes plus the back-reference dictionary.  shift is 5 in both
# ROMs, so the dictionary is 0x400 bytes; measured high-water marks are 298
# (US) and 194 (JP), both inside it.
A7_BLOCK_BYTES = 4 + 0x400

US_BADGES = 0x003B9004
JP_BADGES = 0x003AB784
BADGE_SLOTS = (0x000CBA10,)         # the single literal-pool word, US
BADGE_LOADER = 0x080CB9E0           # the function that loads it, US
SUB_IMAGES = 44
BYTES_PER_SUB = 256                 # 8 tiles of 32 bytes = 32x16 px
IDENTICAL_SUBS = (42,)              # MONSTER: already the same drawing


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


# ------------------------------------------------------------------ asset ---

def a7_payload(rom: bytes, base: int):
    """The verbatim ROM bytes of an A7 container *including* its codec block."""
    count, total, _offs = gfx.container(rom, base)
    high = gfx.container_dictionary_high_water(rom, base)
    if high > A7_BLOCK_BYTES - 4:
        raise RuntimeError(f"JB_A7_DICTIONARY_TOO_SMALL {high}")
    mode, shift, out_size, _dic, dlen = gfx.codec_a_descriptor(rom, base + total)
    if (mode, shift, dlen) != (0, 5, 0x400):
        raise RuntimeError(f"JB_A7_DESCRIPTOR {mode} {shift} {dlen}")
    subs = gfx.container_subs(rom, base)
    meta = {"sub_images": count, "container_bytes": total,
            "codec_block_bytes": A7_BLOCK_BYTES, "dictionary_high_water": high,
            "declared_sub_output_size": out_size,
            "bytes_per_sub_image": len(subs[0])}
    return rom[base:base + total + A7_BLOCK_BYTES], b"".join(subs), meta


def dropin_proof(us_raw: bytes, jp_raw: bytes):
    """Assert the JP sheet is an index-for-index replacement for the US one."""
    _pu, du, mu = a7_payload(us_raw, US_BADGES)
    _pj, dj, mj = a7_payload(jp_raw, JP_BADGES)
    for name, m in (("us", mu), ("jp", mj)):
        if m["sub_images"] != SUB_IMAGES:
            raise RuntimeError(f"JB_SUB_COUNT_{name.upper()} {m['sub_images']}")
        if m["bytes_per_sub_image"] != BYTES_PER_SUB:
            raise RuntimeError(f"JB_SUB_SIZE_{name.upper()}")
    info = gfx.tile_diff(du, dj)
    if not info["same_tile_count"] or info["tiles_a"] != SUB_IMAGES * 8:
        raise RuntimeError("JB_TILE_COUNT %d/%d" % (info["tiles_a"], info["tiles_b"]))
    if info["differing_tiles"] == 0:
        raise RuntimeError("JB_NO_CHANGE")
    us_subs = gfx.container_subs(us_raw, US_BADGES)
    jp_subs = gfx.container_subs(jp_raw, JP_BADGES)
    blanks = lambda d: {t for t in range(len(d) // 32)
                        if not any(d[t * 32:t * 32 + 32])}
    lattice = set().union(*(blanks(s) for s in us_subs))
    jp_blanks = set().union(*(blanks(s) for s in jp_subs))
    if not jp_blanks <= lattice:
        raise RuntimeError("JB_PADDING_LATTICE %s" % sorted(jp_blanks - lattice))
    same = tuple(i for i in range(SUB_IMAGES) if us_subs[i] == jp_subs[i])
    if same != IDENTICAL_SUBS:
        raise RuntimeError("JB_IDENTICAL_SUBS %s" % (same,))
    # The two sheets have to speak the same palette: a sub-image that used
    # colour slots the US art never uses would be drawn with the wrong colours
    # by the screens' own palettes, which this layer does not touch.
    slots = lambda d: {n for b in d for n in (b & 0xF, b >> 4)}
    if not slots(dj) <= slots(du):
        raise RuntimeError("JB_PALETTE_SLOTS %s" % sorted(slots(dj) - slots(du)))
    info.update({f"us_{k}": v for k, v in mu.items()})
    info.update({f"jp_{k}": v for k, v in mj.items()})
    info["identical_sub_images"] = list(same)
    info["colour_slots_used"] = {"us": sorted(slots(du)), "jp": sorted(slots(dj))}
    return info


# ------------------------------------------------------------------- build ---

def build():
    product_prev, _base_prev, meta_prev = prev.build()
    base = product_prev
    if sha(base) != BASELINE:
        raise RuntimeError(f"JB_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    raw = bytearray(base)

    want = ROM + US_BADGES
    for name, blob in (("US", us_raw), ("BASE", base)):
        found = [i for i in range(0, len(blob) - 3, 4) if u32(blob, i) == want]
        if tuple(found) != BADGE_SLOTS:
            raise RuntimeError("JB_REFERENCE_SET_%s %s"
                               % (name, [hex(f) for f in found]))

    proof = dropin_proof(us_raw, jp_raw)
    blob, _decoded, _m = a7_payload(jp_raw, JP_BADGES)

    # First free byte after RC21's relocated words payload.
    cursor = stext.align(prev.NEW_WORDS + meta_prev["words_block_bytes"], 4)
    if set(base[cursor:cursor + len(blob)]) != {0xFF} or \
            set(us_raw[cursor:cursor + len(blob)]) != {0xFF}:
        raise RuntimeError("JB_DESTINATION_NOT_FREE")
    # Nothing may already point into the destination.  Over 11 KB of address
    # space, text payloads produce aligned words that *look* like addresses by
    # accident, so the test is on what a real reference must be: an A7
    # container base is 4-aligned, so only a 4-aligned value is a candidate.
    # The rest are recorded, with the proof that they are not references --
    # they sit in bytes this build never touches and their neighbours are not
    # addresses either.
    coincidental = []
    for off in range(0, len(base) - 3, 4):
        v = u32(base, off)
        if not (ROM + cursor <= v < ROM + cursor + len(blob)):
            continue
        if v % 4 == 0:
            raise RuntimeError("JB_DESTINATION_REFERENCED 0x%08X -> 0x%08X"
                               % (ROM + off, v))
        coincidental.append((ROM + off, v))
    raw[cursor:cursor + len(blob)] = blob
    for slot in BADGE_SLOTS:
        struct.pack_into("<I", raw, slot, ROM + cursor)
    block_end = cursor + len(blob)

    if len(raw) != len(base):
        raise RuntimeError("JB_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= cursor
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("JB_BLOCK_OUTSIDE_TAIL")

    meta = {
        "asset": {
            "us_rom_address": f"0x{ROM + US_BADGES:08X}",
            "jp_rom_address": f"0x{ROM + JP_BADGES:08X}",
            "new_rom_address": f"0x{ROM + cursor:08X}",
            "bytes": len(blob),
            "repointed_literals": [f"0x{ROM + s:08X}" for s in BADGE_SLOTS],
            "loader": f"0x{BADGE_LOADER:08X}",
        },
        "block": {"start": f"0x{cursor:06X}", "end": f"0x{block_end:06X}"},
        "unaligned_lookalike_words": [f"0x{a:08X}=0x{v:08X}" for a, v in coincidental],
        "proof": proof,
    }
    return bytes(raw), base, meta


# ---------------------------------------------------------------- validate ---

def validate(product: bytes, base: bytes, meta: dict):
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    start = int(meta["block"]["start"], 16)
    end = int(meta["block"]["end"], 16)

    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = set(range(start, end))
    for slot in BADGE_SLOTS:
        expected |= set(range(slot, slot + 4))
    if diff - expected:
        raise RuntimeError("JB_UNEXPLAINED_BYTES %s"
                           % sorted(hex(d) for d in diff - expected)[:8])

    new_addr = u32(product, BADGE_SLOTS[0])
    if new_addr != ROM + start:
        raise RuntimeError("JB_LITERAL_NOT_REPOINTED 0x%08X" % new_addr)

    # The relocated container parses out of the *product* and decodes to
    # exactly the JP retail sheet, sub image for sub image.
    got = gfx.container_subs(product, new_addr - ROM)
    wanted = gfx.container_subs(jp_raw, JP_BADGES)
    if len(got) != SUB_IMAGES or any(a != b for a, b in zip(got, wanted)):
        raise RuntimeError("JB_RELOCATED_DECODE_MISMATCH")
    _blob, decoded, m = a7_payload(product, new_addr - ROM)
    if m["dictionary_high_water"] > A7_BLOCK_BYTES - 4:
        raise RuntimeError("JB_RELOCATED_DICTIONARY")
    if decoded != b"".join(wanted):
        raise RuntimeError("JB_RELOCATED_PIXELS")

    # The pristine US sheet is still in the image, untouched, and nothing
    # points at it any more.
    if product[US_BADGES:US_BADGES + 0x2900] != us_raw[US_BADGES:US_BADGES + 0x2900]:
        raise RuntimeError("JB_ORIGINAL_ASSET_TOUCHED")
    stale = [i for i in range(0, len(product) - 3, 4)
             if u32(product, i) == ROM + US_BADGES]
    if stale:
        raise RuntimeError("JB_STALE_REFERENCE %s" % [hex(s) for s in stale])

    # No executable byte moved: the loader and its neighbours are pristine.
    lo = BADGE_LOADER - ROM
    if product[lo:lo + 0x30] != us_raw[lo:lo + 0x30]:
        raise RuntimeError("JB_LOADER_TOUCHED")

    return {
        "patch": meta,
        "binary_touch": {
            "relocated_asset_bytes": end - start,
            "literal_words_repointed": len(BADGE_SLOTS),
            "executable_bytes_changed": 0,
            "block": [f"0x{ROM + start:08X}", f"0x{ROM + end:08X}"],
        },
        "runtime_contract": {
            "loader": f"0x{BADGE_LOADER:08X}",
            "sub_index": "job_id - 2 (0x2C for job_id > 0x2B, 0x2D for 0x4D "
                         "and job_id > 0x52)",
            "us_call_sites": 17, "jp_call_sites": 12,
        },
    }


# ------------------------------------------------------------------ census ---

# sub index -> (job as the JP ROM's own words:content names it, US label, JP
# label).  The labels are the glyphs drawn in the badge; the job names are read
# out of words:content (Morpher = content/35 = めたもる士, Gadgeteer =
# content/12 = カラクリ士, ...), and the order is the game's own: the badge for
# job id N is sub N-2, and the shop's job list lays subs 0..41 out in that order.
JOB_LABELS = (
    ("ソルジャー", "SLD", "ソ"), ("パラディン", "PAL", "パ"),
    ("闘士", "FGT", "闘"), ("シーフ", "THF", "シ"),
    ("忍者", "NIN", "忍"), ("白魔道士", "WHT", "白"),
    ("黒魔道士", "BLK", "黒"), ("幻術士", "ILL", "幻"),
    ("青魔道士", "BLU", "青"), ("弓使い", "ARC", "弓"),
    ("狩人", "HNT", "狩"), ("ウォリアー", "WAR", "ウ"),
    ("竜騎士", "DRG", "竜"), ("守護騎士", "DEF", "守"),
    ("グラディエーター", "GLD", "グ"), ("ホワイトモンク", "MNK", "ホ"),
    ("ビショップ", "BIS", "ビ"), ("神殿騎士", "TEM", "神"),
    ("白魔道士 (nu mou)", "WHT", "白"), ("黒魔道士 (nu mou)", "BLK", "黒"),
    ("時魔道士", "TIM", "時"), ("幻術士 (nu mou)", "ILL", "幻"),
    ("錬金術士", "ALC", "錬"), ("魔獣使い", "BST", "獣"),
    ("めたもる士", "MOR", "め"), ("セージ", "SAG", "セ"),
    ("フェンサー", "FEN", "フ"), ("精霊使い", "EMT", "精"),
    ("赤魔道士", "RED", "赤"), ("白魔道士 (viera)", "WHT", "白"),
    ("召喚士", "SUM", "召"), ("弓使い (viera)", "ARC", "弓"),
    ("アサシン", "ASN", "ア"), ("スナイパー", "SNP", "ス"),
    ("動物使い", "ANM", "動"), ("モーグルナイト", "MOG", "モ"),
    ("銃使い", "GUN", "銃"), ("シーフ (moogle)", "THF", "シ"),
    ("曲芸士", "JGL", "曲"), ("カラクリ士", "GDT", "カ"),
    ("黒魔道士 (moogle)", "BLK", "黒"), ("時魔道士 (moogle)", "TIM", "時"),
    ("(モンスター)", "MONSTER", "MONSTER"), ("(特殊ジョブ)", "EXPERT", "SPECIAL"),
)


def census(path=HERE / "data/job_badge_census.csv"):
    """Every badge, both ROMs, pixel-equal or not, and where it comes from."""
    import csv
    us_raw, jp_raw = US.read_bytes(), JP.read_bytes()
    us_subs = gfx.container_subs(us_raw, US_BADGES)
    jp_subs = gfx.container_subs(jp_raw, JP_BADGES)
    prod = OUTROM.read_bytes() if OUTROM.exists() else None
    new = (u32(prod, BADGE_SLOTS[0]) - ROM) if prod else None
    pr_subs = gfx.container_subs(prod, new) if prod else None
    rows = []
    for i in range(SUB_IMAGES):
        job, us_lbl, jp_lbl = JOB_LABELS[i]
        differing = sum(1 for t in range(8)
                        if us_subs[i][t * 32:(t + 1) * 32]
                        != jp_subs[i][t * 32:(t + 1) * 32])
        rows.append({
            "sub_index": i,
            "job_id": i + 2 if i < 42 else "",
            "job": job,
            "us_label": us_lbl,
            "jp_label": jp_lbl,
            "us_sub_address": "0x%08X (sub %d of the container)" % (ROM + US_BADGES, i),
            "jp_sub_address": "0x%08X (sub %d of the container)" % (ROM + JP_BADGES, i),
            "tiles_differing_us_vs_jp": differing,
            "pixels_equal_us_vs_jp": differing == 0,
            "rc22_equals_jp": (pr_subs[i] == jp_subs[i]) if pr_subs else "",
            "mapping_equal": True,       # sub = job id - 2 in both ROMs
            "player_visible": True,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fd:
        w = csv.DictWriter(fd, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    same = sum(1 for r in rows if r["pixels_equal_us_vs_jp"])
    print("%s: %d badges, %d pixel-identical US/JP, %d divergent"
          % (path, len(rows), same, len(rows) - same))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260902_production")
    ap.add_argument("--print-sha", action="store_true")
    ap.add_argument("--census", action="store_true",
                    help="regenerate data/job_badge_census.csv and stop")
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
        raise RuntimeError("JB_BUILD_NONDETERMINISTIC")
    audits = validate(product, base, meta)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    summary = {
        "milestone": "job badges (RC22)",
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
