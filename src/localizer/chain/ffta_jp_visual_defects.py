#! python3
"""RC21 - the five manually-reported visual defects, fixed at their owners.

All five were found by the user actually playing RC17 (docs/RC21_VISUAL_DEFECTS.md
carries the full evidence); this layer fixes the four that are real defects.
The fifth (the ability-screen "Action" label and the three-letter job badges)
is byte-identical in JP retail (US 0x083BBC7C == JP 0x083AE574 and its two
sibling sheets, referenced from JP code) - JP retail's own presentation, so
nothing to change.

1. Quest deadline ``15日日日日日`` (and ``－日``)
------------------------------------------------

The ``{04 xx}`` substitution token in the mission-panel page templates is a
**two-sided contract**: the per-field value function (function table
``US 0x0836D768``, called through ``US 0x08018CEC``) returns, for the *empty*
("－") variant, **how many template bytes to skip** after the token; for a
real value it returns 0 and the template's following characters are printed
as the unit suffix.  The constants are compiled for each region's template:

===========  =============================  =============================
field        US consumer (US template)      JP consumer (JP template)
===========  =============================  =============================
0x18 skills  13 = 5 chars + the {041A} tok  11 = 4 chars + the {041A} tok
0x1D expiry  8  = ``days`` (4 chars)        2  = ``日`` (1 char)
===========  =============================  =============================

RC12 padded the JP template runs to the *US* byte counts, which is correct
only for the "－" variant; a numeric deadline skips nothing and prints every
pad: ``15日日日日日``, and the dash variant prints the fifth pad: ``－日``.
The fix restores JP retail's own template runs (lines 2 and 3 of the
relocated JP leaf, verbatim) and ports the two JP constants into the US value
functions - 2 code bytes, the only executable bytes this layer touches.
Field 0x1C also differs (US 8 / JP 2) but no template of leaf 9 gives it a
suffix in either region, so it is left as US retail compiled it.

2. In-battle SYSTEM menu clipping (アクティブターン 81px, バトルから逃げる 83px in 80px)
--------------------------------------------------------------------------------------

Ten text columns is a hard bound (11 relocates the turn-order LIST's staging
buffer onto its own context - docs/RC13_QA_HARNESS.md section 2; re-widening is
forbidden).  The text engine has a per-token negative-kern control
``{1B n}`` (drawcore handler US 0x080152FE / 0x08015744 negates the operand
into the pen-advance call US 0x08013728), so the two records are re-encoded
with 1px kerns at gaps flanking narrow glyphs: ``アクティブター{1B}ン`` 80px and
``バト{1B}ル{1B}から{1B}逃げる`` 80px.  Owner-side, no geometry change.

3. OPTIONS Lボタン割り当て value clipping (バトルから逃げる)
------------------------------------------------------------

The current-value renderer of the OPTIONS L row (US 0x08009030) advances the
pen by a **per-value offset table** at ``US 0x0836D59C`` before drawing -
tuned so每value ends flush at the window's right edge.  RC12 ported the value
*column* (13 -> JP's 11) but not this table; the US offsets are tuned for the
English value widths (offset 24px for value 4 puts バトルから逃げる 19px past
the window).  JP retail's own six bytes ``07 1A 25 25 05 23``
(``JP 0x083615C4``) restore ``col*8 + offset + width == 176`` for every value.

4. アドバンスト・ロウ clipping on the ability screen
----------------------------------------------------

``words:battle/176`` (US "Advanced Law", Cid's ability set) is 90px; the
ability screen's set-name panel draws from its fixed x to the *screen* edge,
which leaves 88px - the last 2px of ``ウ`` fall off screen (reproduced by a
consumer redirect, ``build/rc21_visual_defects/20260902_run/fx_abl``).  The
name itself stays: JP retail's own scene text uses アドバンスト・ロウ verbatim
(s_text/8/47 and 8/52, the judge-arc scenes), so it is retail terminology, and
アドバンスド would contradict it (and is 1px wider).  Three 1px kerns bring the
record to 87px: ``アドバン{1B}スト{1B}・{1B}ロウ``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_us_added_missions as prev
import ffta_jp_mission_panel_templates as mpt
import ffta_jp_coverage_audit as coverage

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
RUN_BASE = HERE / "build/visual_defects"
OUTROM = ROOT / "rom/build/ffta_us_jp_visual_defects.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_visual_defects_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_us_added_missions (RC17).
BASELINE = "9F5EBA6C4408CAA419DE1DC96AE20B6B8A43785230C1B14C21BA14D9C5D4195E"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "F3B0F990B416C0AEFBEB44EE468A16B2D7FA0FDDDA19F9E7CF5E99F91EEEE49B"

# ---- defect 1: the deadline / skills substitution contract --------------
NEW_LEAF = 0x00B10000            # RC12's mission-panel leaf (rebuilt in place)
LEAF_LEN = mpt.US_LEAF_END - mpt.US_LEAF          # 1736
JP_TEMPLATE_LINES = (2, 3)       # page-3 and page-4 templates
# value-function skip constants: (file offset, expected, new) - Thumb movs r0,#imm
US_SKIP18 = (0x00019044, 0x0D, 0x0B)   # US fn 0x08018FF1, JP fn 0x08018F01
US_SKIP1D = (0x0001925A, 0x08, 0x02)   # US fn 0x0801920D, JP fn 0x0801911D
JP_SKIP_EVIDENCE = ((0x00018F54, 0x0B), (0x0001916A, 0x02))
# the JP-retail substitution shapes the restored lines must carry
EXPECTED_RUNS = {2: {0x18: (4, 8)}, 3: {0x1D: (1, 2)}}

# ---- defect 3 (OPTIONS): the per-value pen-offset table ------------------
US_LOFF = 0x0036D59C
JP_LOFF = 0x003615C4
LOFF_LEN = 6
US_LOFF_BYTES = bytes.fromhex("07180b18180f")
JP_LOFF_BYTES = bytes.fromhex("071a25250523")
OPT_VALUE_COLUMN = 11            # RC12's ported column (u16 at 0x0036D576)
OPT_TEXT_COLUMNS = 22

# ---- defects 2/4: kerned words:battle records ----------------------------
WORDS_BATTLE_ROOT = 0x005567F0   # words:battle root pointer table (769 entries)
ADVANCE = 0x004966B0             # the game's own glyph advance table
KERN = bytes((0x40, 0x3C, 0x01))  # {1B 01}: pen -= 1px
NEW_WORDS = 0x00B1AC00           # first free 0xFF run after RC17's tail block
# index -> (expected glyph slots, kern after Nth glyph (1-based), target px)
KERNED = {
    1:   ((0x052, 0x05F, 0x076, 0x053, 0x086, 0x06F, 0x6D9, 0x0A1),
          (7,), 80),                                   # アクティブターン 81 -> 80
    5:   ((0x19D, 0x195, 0x1B6, 0x12C, 0x167, 0x409, 0x133, 0x169),
          (2, 3, 5), 80),                              # バトルから逃げる 83 -> 80
    176: ((0x170, 0x196, 0x19D, 0x1BB, 0x187, 0x195, 0x1D3, 0x1B8, 0x174),
          (4, 6, 7), 87),                              # アドバンスト・ロウ 90 -> 87
}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


# ---------------------------------------------------------------- leaf ----

def find_relocated_leaf(img: bytes) -> int:
    """The relocated JP leaf 9: the signature candidate that is neither the
    pristine US leaf nor RC12's rebuilt leaf."""
    cands = []
    i = -1
    while True:
        i = img.find(mpt.LEAF0_SIGNATURE, i + 1)
        if i < 0:
            break
        base = i - 2 * (mpt.NLINES + 1)
        if base < 0 or base & 1:
            continue
        try:
            mpt.line_segments(img, base)
        except RuntimeError:
            continue
        cands.append(base)
    others = [c for c in cands if c not in (mpt.US_LEAF, NEW_LEAF)]
    if len(cands) != 3 or len(others) != 1:
        raise RuntimeError("VD_LEAF_CANDIDATES %s" % [hex(c + ROM) for c in cands])
    return others[0]


def run_census(seg):
    """{field: (chars, suffix bytes)} for one template line segment."""
    _fl, toks, _p = mpt.line_tokens(seg)
    out = {}
    for f, n, k in mpt.substitution_runs(toks):
        blen = sum(toks[j][3] for j in range(k - n + 1, k + 1)) if n else 0
        out[f] = (n, blen)
    return out


def fix_leaf(raw: bytearray, base: bytes):
    rel = find_relocated_leaf(base)
    segs17 = mpt.line_segments(base, NEW_LEAF)
    segsjp = mpt.line_segments(base, rel)
    for i in range(mpt.NLINES):
        if i in JP_TEMPLATE_LINES:
            continue
        if segs17[i] != segsjp[i]:
            raise RuntimeError("VD_LEAF_LINE_DIVERGES %d" % i)
    new = list(segs17)
    for i in JP_TEMPLATE_LINES:
        census = run_census(segsjp[i])
        for f, (n, blen) in EXPECTED_RUNS[i].items():
            if census.get(f) != (n, blen):
                raise RuntimeError("VD_JP_RUN_SHAPE line %d field %02X %s"
                                   % (i, f, census.get(f)))
        new[i] = segsjp[i]
    tail = bytes(base[NEW_LEAF + mpt.US_TAIL_OFS:NEW_LEAF + LEAF_LEN])
    leaf = mpt.assemble(new, tail)
    if len(leaf) != LEAF_LEN:
        raise RuntimeError("VD_LEAF_LEN %d" % len(leaf))
    raw[NEW_LEAF:NEW_LEAF + LEAF_LEN] = leaf
    return {"relocated_source_leaf": f"0x{ROM + rel:08X}",
            "restored_lines": list(JP_TEMPLATE_LINES),
            "leaf": f"0x{ROM + NEW_LEAF:08X}", "leaf_bytes": LEAF_LEN}


def fix_skip_constants(raw: bytearray, base: bytes, us_raw: bytes, jp_raw: bytes):
    for off, old, newv in (US_SKIP18, US_SKIP1D):
        for img, who in ((base, "base"), (us_raw, "us")):
            if img[off] != old or img[off + 1] != 0x20:
                raise RuntimeError("VD_SKIP_SITE_%s %05X %02X" % (who, off, img[off]))
        raw[off] = newv
    for off, val in JP_SKIP_EVIDENCE:
        if jp_raw[off] != val or jp_raw[off + 1] != 0x20:
            raise RuntimeError("VD_JP_SKIP_EVIDENCE %05X" % off)
    return {"patched": [f"0x{ROM + o:08X}: {a:#04x}->{b:#04x}" for o, a, b in (US_SKIP18, US_SKIP1D)],
            "executable_bytes_changed": 2}


# ------------------------------------------------------------- options ----

def fix_options(raw: bytearray, base: bytes, us_raw: bytes, jp_raw: bytes):
    if base[US_LOFF:US_LOFF + LOFF_LEN] != US_LOFF_BYTES or \
            us_raw[US_LOFF:US_LOFF + LOFF_LEN] != US_LOFF_BYTES:
        raise RuntimeError("VD_LOFF_US_BYTES")
    if jp_raw[JP_LOFF:JP_LOFF + LOFF_LEN] != JP_LOFF_BYTES:
        raise RuntimeError("VD_LOFF_JP_BYTES")
    if struct.unpack_from("<H", base, 0x0036D574 + 2)[0] != OPT_VALUE_COLUMN:
        raise RuntimeError("VD_OPT_COLUMN_NOT_RC12")
    raw[US_LOFF:US_LOFF + LOFF_LEN] = JP_LOFF_BYTES
    return {"table": f"0x{ROM + US_LOFF:08X}", "us": US_LOFF_BYTES.hex(),
            "jp": JP_LOFF_BYTES.hex()}


# --------------------------------------------------------------- words ----

def decode_record(img: bytes, ptr: int):
    """(slots, kern_px, end) of a words payload: full-width CHRs, an optional
    half-lane switch (0x01: every later byte is slot ``byte - 1``), and
    {1B} kerns.  The advance table covers both lanes."""
    p = ptr
    slots, kern = [], 0
    half = False
    while img[p] != 0:
        c = img[p]
        if c == 0x01 and not half:
            half = True
            p += 1
        elif half:
            slots.append(c - 1)
            p += 1
        elif c >= 0x80:
            slots.append(((c << 8) | img[p + 1]) & 0x7FFF)
            p += 2
        elif c == 0x40 and img[p + 1] == 0x3C:
            kern += img[p + 2]
            p += 3
        else:
            raise RuntimeError("VD_RECORD_TOKEN %02X at %06X" % (c, p))
    return slots, kern, p + 1


def fix_words(raw: bytearray, base: bytes, us_raw: bytes):
    n_words = len(base) // 4
    gap_hi = NEW_WORDS + 0x80
    if set(base[NEW_WORDS:gap_hi]) != {0xFF} or set(us_raw[NEW_WORDS:gap_hi]) != {0xFF}:
        raise RuntimeError("VD_GAP_NOT_FREE")
    for off, v in enumerate(struct.unpack_from("<%dI" % n_words, base, 0)):
        if ROM + NEW_WORDS <= v < ROM + gap_hi:
            raise RuntimeError("VD_GAP_REFERENCED 0x%08X" % (ROM + off * 4))
    pos = NEW_WORDS
    meta = {}
    for idx, (slots, gaps, target) in sorted(KERNED.items()):
        field = WORDS_BATTLE_ROOT + idx * 4
        ptr = u32(base, field) - ROM
        got, kern0, _e = decode_record(base, ptr)
        if tuple(got) != slots or kern0 != 0:
            raise RuntimeError("VD_RECORD_SHAPE battle/%d %s" % (idx, [hex(s) for s in got]))
        width0 = sum(base[ADVANCE + s] for s in slots)
        if width0 - len(gaps) != target:
            raise RuntimeError("VD_KERN_TARGET battle/%d %d-%d != %d"
                               % (idx, width0, len(gaps), target))
        out = bytearray()
        for i, s in enumerate(slots, 1):
            out += bytes((0x80 | (s >> 8), s & 0xFF))
            if i in gaps:
                out += KERN
        out += b"\x00"
        if len(out) & 1:
            out += b"\x00"
        raw[pos:pos + len(out)] = out
        struct.pack_into("<I", raw, field, ROM + pos)
        meta["battle/%d" % idx] = {
            "old": f"0x{ROM + ptr:08X}", "new": f"0x{ROM + pos:08X}",
            "width": f"{width0}px -> {target}px", "kerns": list(gaps),
            "bytes": len(out)}
        pos += len(out)
    return meta, pos - NEW_WORDS


# --------------------------------------------------------------- build ----

def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"VD_BASELINE_MISMATCH {sha(base)}")
    us_raw = US.read_bytes()
    jp_raw = JP.read_bytes()
    raw = bytearray(base)
    meta = {
        "leaf": fix_leaf(raw, base),
        "skip_constants": fix_skip_constants(raw, base, us_raw, jp_raw),
        "options_value_offsets": fix_options(raw, base, us_raw, jp_raw),
    }
    meta["kerned_words"], words_len = fix_words(raw, base, us_raw)
    meta["words_block_bytes"] = words_len
    return bytes(raw), base, meta


def validate(product: bytes, base: bytes, meta: dict):
    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = set(range(NEW_LEAF, NEW_LEAF + LEAF_LEN))
    expected |= {US_SKIP18[0], US_SKIP1D[0]}
    expected |= set(range(US_LOFF, US_LOFF + LOFF_LEN))
    expected |= set(range(NEW_WORDS, NEW_WORDS + meta["words_block_bytes"]))
    for idx in KERNED:
        f = WORDS_BATTLE_ROOT + idx * 4
        expected |= set(range(f, f + 4))
    if diff - expected:
        raise RuntimeError("VD_UNEXPLAINED_BYTES %s" %
                           sorted(hex(d) for d in diff - expected)[:8])
    # the rebuilt leaf parses and carries JP retail's own runs
    for i, fields in EXPECTED_RUNS.items():
        census = run_census(mpt.line_segments(product, NEW_LEAF)[i])
        for fld, shape in fields.items():
            if census.get(fld) != shape:
                raise RuntimeError("VD_RESTORED_RUN line %d field %02X" % (i, fld))
    # tail preserved for every consumer past the templates
    if product[NEW_LEAF + mpt.US_TAIL_OFS:NEW_LEAF + LEAF_LEN] != \
            base[NEW_LEAF + mpt.US_TAIL_OFS:NEW_LEAF + LEAF_LEN]:
        raise RuntimeError("VD_TAIL_NOT_PRESERVED")
    # kerned records decode back and hit their widths
    for idx, (slots, gaps, target) in KERNED.items():
        ptr = u32(product, WORDS_BATTLE_ROOT + idx * 4) - ROM
        got, kern, _e = decode_record(product, ptr)
        if tuple(got) != slots or kern != len(gaps):
            raise RuntimeError("VD_KERNED_DECODE battle/%d" % idx)
        if sum(product[ADVANCE + s] for s in slots) - kern != target:
            raise RuntimeError("VD_KERNED_WIDTH battle/%d" % idx)
    # every other words:battle root entry unchanged
    for i in range(769):
        if i in KERNED:
            continue
        f = WORDS_BATTLE_ROOT + i * 4
        if product[f:f + 4] != base[f:f + 4]:
            raise RuntimeError("VD_ROOT_TOUCHED battle/%d" % i)
    # the OPTIONS right-align law with the new widths: col*8 + off + width <= 176
    for i in range(6):
        off = product[US_LOFF + i]
        idx = i + 1
        ptr = u32(product, WORDS_BATTLE_ROOT + idx * 4) - ROM
        slots, kern, _e = decode_record(product, ptr)
        w = sum(product[ADVANCE + s] for s in slots) - kern
        if OPT_VALUE_COLUMN * 8 + off + w > OPT_TEXT_COLUMNS * 8:
            raise RuntimeError("VD_OPT_STILL_CLIPS value %d" % i)
    # font, advance table, code outside the two bytes: untouched by construction
    return {
        "patch": meta,
        "binary_touch": {
            "leaf_bytes": LEAF_LEN, "executable_bytes_changed": 2,
            "options_bytes": LOFF_LEN,
            "words_payload_bytes": meta["words_block_bytes"],
            "root_fields_repointed": len(KERNED)},
        "not_a_defect": "ability-screen Action label / job badges: US 0x083BBC7C,"
                        " 0x083EAB98, 0x083ADB08 byte-identical to JP retail"
                        " (JP 0x083AE574, 0x083DD56C, 0x083A09A8)",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260902_production")
    ap.add_argument("--print-sha", action="store_true")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return
    second = build()
    product, base, meta = first
    if sha(product) != sha(second[0]):
        raise RuntimeError("VD_BUILD_NONDETERMINISTIC")
    audits = validate(product, base, meta)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    summary = {
        "milestone": "visual-defects (RC21)",
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
