#! python3
# -*- coding: utf-8 -*-
"""Law names and antilaw cards that read the way the US engine runs them.

The defect
---------------------------------

US retail changed how laws are *presented*.  A law name is a category
("Skills", "Fire", "Dmg2: Human"); the LAW screen's FORBIDDEN / RECOMMEND
headers decide what it means, and the same law id can appear under either
header.  An antilaw card cancels a *pair*: "Nullifies: Forbidden/Skills,
Recommended/Techniques".

The chain transferred JP ``words:content`` by index, so every law name is still
JP retail's *rule* ("術禁止", "人間愛護").  Under RECOMMEND that reads as a
contradiction ("RECOMMEND: 混乱禁止") or the opposite of the rule ("RECOMMEND:
人間愛護" is a bonus for *hitting* humans).  The 66 antilaw cards
(``fx_text/23`` entries 170..254, card ``i`` = law ``i - 169``) say only
"「X禁止」をうち消す。", dropping the recommended half, and card 194 (law 25)
still quotes the name earlier replaced.  Two cards also print a different grade
from US retail (law 25: US R5, law 73: US R5).

The LAW screen headers stay English: they are graphics, and JP retail ships
them in English too (``ffta_jp_ui_graphics``).

What this layer does
--------------------

* **Names** -- every law ``1..83`` whose name ends in 禁止 loses the suffix
  (術禁止 -> 術); the six "X愛護" laws ``77..82`` (US "Dmg2: X") become 対X.
  Names that are not a forbidden-thing (0, 26..29, 32, 56..60, 84) stay JP
  retail's own.  Only glyphs already in the name are reused, plus 対, which the
  chain already allocates.
* **Cards** -- each card whose US text is ``Forbidden/X ... Recommended/Y``
  becomes::

      [Rn]つぎのロウをうち消す。
      バトル中に使うことができる。          (page break)
      禁止「X」
      ボーナス「Y」

  ``n`` is US retail's grade; X and Y are the new names of the laws US names.
  "ボーナス" is the word the recommended laws' own HELP text uses (fx_text/23
  262+, "...ヒットさせると{5117}ポイントボーナス"); 推 and 奨 are not JP retail
  glyphs, and the Latin I and D needed for the screen's own RECOMMEND /
  FORBIDDEN are not allocated by the chain.  Every piece of the card is either
  cut from the card's current tokens ("をうち消す。", the second line, 禁止)
  or kana JP retail already allocates: **zero new glyphs**.
* ``fx_text`` leaf 24 is US retail's alias of leaf 23 (same root pointer in
  pristine US).  Earlier layers left it on the pristine address, whose bytes
  equal leaf 23's; this layer proves that equality and then points leaf 24 at
  the rebuilt leaf 23, restoring the US alias, so the two can never diverge.

Nothing is trusted: ``data/us_law_semantics.json`` is the editorial record of
every new name and card; each build derives them again from the pristine US
text and the base image, and fails on any difference.

Mechanism: ``words:content`` is direct-addressed (one root field per record,
repointed); ``fx_text`` leaf 23 is re-serialized with only its targets replaced
and repointed.  **Zero executable bytes, zero graphics bytes, zero font or
metadata writes.**

Layer position: a new terminal layer above ``ffta_jp_us_replaced_laws``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_us_replaced_laws as prev
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_only_fx_text as fxt
import ffta_placeholder_transfer_audit as pta
from ffta_sect import c_ffta_sect_text_buf                      # noqa: E402
import ffta_us_source as _us_source

uam = prev.uam
sysbat = prev.sysbat

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_law_semantics.json"
RUN_BASE = HERE / "build/us_law_semantics"
OUTROM = ROOT / "rom/build/ffta_us_jp_us_law_semantics.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_us_law_semantics_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_us_replaced_laws (the earlier layer's rebase of an earlier build).
BASELINE = "9AA06994131C9CD8BF51ABC6AE5DE90B085F5F404F80C8599D9206DC21982B8C"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "ED4AA7261AAD5B5D684185D1BE91240F3D4FC4D294E0B2E6F5C28D7A235C87F0"

LAW_COUNT = prev.LAW_COUNT
NAME_BASE = prev.NAME_BASE
FAMILY_BOUND_PX = prev.FAMILY_BOUND_PX
AIGO = (77, 78, 79, 80, 81, 82)          # X愛護 -> 対X  (US "Dmg2: X")
RENAMABLE = range(1, 84)                 # 84 色魔法しばり is not a forbidden-thing
CARD_LEAF, ALIAS_LEAF = 23, fxt.ALIAS_LEAF
CARD_BASE = 169                          # fx_text/23/(169 + law) is law's card
CARD_RANGE = range(170, 255)
STALE_CARD_LAWS = (25,)                  # card quotes the earlier layer's replaced name
GRADE_FIXES = {25: (4, 5), 73: (6, 5)}   # law: (base grade, US grade)
CARD_COUNT = 66
JP_KAN, JP_GO, JP_TAI = 0x407, 0x1BF, 0x7EF   # 禁, 護, 対 (JP glyph codes)
US_CARD = re.compile(r"^\[R(\d)\] ?Nullifies:\[4F\]\[42\]Forbidden/(.+?)\[4D\]"
                     r"Recommended/(.+?)\[40\]\[42\]$")
US_NAME_ALIASES = {"Kngtswords": "Knightswords"}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


tokens_of = prev.tokens_of
u32 = prev.u32


def us_name_key(text):
    text = text.strip()
    text = US_NAME_ALIASES.get(text, text)
    match = re.match(r"^Dmg2:? ?(.+)$", text)
    return f"Dmg2: {match.group(1)}" if match else text


def renderer(alloc):
    names = pta.glyphs.charset()
    inverse = {slot: code for code, slot in alloc.items()}

    def render(tokens):
        out = []
        for kind, value in tokens:
            if kind.startswith("CHR"):
                code = inverse.get(value)
                out.append(names.get(code, "<%04X>" % value) if code is not None
                           else "<us:%04X>" % value)
            else:
                out.append("{%X}" % value)
        return "".join(out)
    return render, inverse


def to_jp(tokens, inverse):
    out = []
    for kind, value in tokens:
        if kind.startswith("CHR"):
            if value not in inverse:
                raise RuntimeError("ULS_SLOT_NOT_ALLOCATED 0x%04X" % value)
            out.append((kind, inverse[value]))
        else:
            out.append((kind, value))
    return out


def kana(text, reverse, alloc):
    return uam.to_slots(uam.parse_markup(text, reverse, {}), alloc)


# ------------------------------------------------------------------ names ---

def new_names(base_words, alloc, jp_raw, inverse):
    kan, shi = alloc[JP_KAN], alloc[0x12D]
    go, tai = alloc[JP_GO], alloc[JP_TAI]
    before, after = {}, {}
    aigo_pair = None
    for law in range(LAW_COUNT):
        tokens = tokens_of(base_words[NAME_BASE + law])
        before[law] = tokens
        if law in AIGO:
            pair = tokens[-2:]
            if pair[1] != ("CHR_FULL", go) or (aigo_pair and pair != aigo_pair):
                raise RuntimeError(f"ULS_AIGO_SHAPE law {law}")
            aigo_pair = pair
            after[law] = [("CHR_FULL", tai)] + tokens[:-2]
        elif law in RENAMABLE and tokens[-2:] == [("CHR_FULL", kan), ("CHR_FULL", shi)] \
                and len(tokens) > 2:
            after[law] = tokens[:-2]
    for law, tokens in after.items():
        if any(k != "CHR_FULL" for k, _ in tokens):
            raise RuntimeError(f"ULS_NAME_NOT_PLAIN law {law}")
        width = max(fxt.line_widths(to_jp(tokens, inverse), jp_raw))
        if width > FAMILY_BOUND_PX:
            raise RuntimeError(f"ULS_NAME_WIDTH law {law} {width}")
    return before, after, [("CHR_FULL", kan), ("CHR_FULL", shi)]


# ------------------------------------------------------------------ cards ---

def us_cards(us, decode):
    """card index -> (grade, forbidden name, recommended name) from US retail."""
    page = fxt.us_leaf(us, CARD_LEAF)
    out = {}
    for index in CARD_RANGE:
        text = fxt.visible(tokens_of(page[index]), decode)
        match = US_CARD.match(text)
        if match:
            out[index] = (int(match.group(1)), us_name_key(match.group(2)),
                          us_name_key(match.group(3)), text)
    if len(out) != CARD_COUNT:
        raise RuntimeError(f"ULS_US_CARD_COUNT {len(out)}")
    return out


def us_law_ids(us, decode):
    content = us.tabs["words"]["content"]
    seen = {}
    for law in range(LAW_COUNT):
        key = us_name_key(fxt.visible(tokens_of(content[NAME_BASE + law]), decode))
        seen.setdefault(key, []).append(law)
    return {k: v[0] for k, v in seen.items() if len(v) == 1}


def card_pieces(tokens, lquote, rquote):
    """[Rn] 「name」 tail {4D} line2 {40}{42} -> its parts, or raise."""
    if tokens[4] != lquote or tokens[-2:] != [("CTR_FUNC", 0x40), ("CTR_FUNC", 0x42)]:
        raise RuntimeError("ULS_CARD_SHAPE")
    close = tokens.index(rquote, 5)
    brk = tokens.index(("CTR_FUNC", fxt.LINE_BREAK), close)
    return {"grade": tokens[:4], "name": tokens[5:close],
            "tail": tokens[close + 1:brk], "line2": tokens[brk + 1:-2]}


def new_cards(base_cards, us_card, law_of, before, after, kinshi, reverse, alloc,
              jp_raw, inverse, bound):
    lquote, rquote = kana("「", reverse, alloc)[0], kana("」", reverse, alloc)[0]
    name = {law: after.get(law, before[law]) for law in range(LAW_COUNT)}
    canon = card_pieces(tokens_of(base_cards[min(us_card)]), lquote, rquote)
    head = kana("つぎのロウ", reverse, alloc)
    bonus = kana("ボーナス", reverse, alloc)
    out = {}
    for index, (grade, forbid, recommend, text) in sorted(us_card.items()):
        law = index - CARD_BASE
        if law_of.get(forbid) != law:
            raise RuntimeError(f"ULS_CARD_FORBIDS_OTHER_LAW {index} {forbid}")
        if recommend not in law_of:
            raise RuntimeError(f"ULS_CARD_RECOMMENDED_UNKNOWN {index} {recommend}")
        rec = law_of[recommend]
        pieces = card_pieces(tokens_of(base_cards[index]), lquote, rquote)
        if pieces["tail"] != canon["tail"] or pieces["line2"] != canon["line2"]:
            raise RuntimeError(f"ULS_CARD_BODY_DRIFT {index}")
        stale = pieces["name"] != before[law]
        if stale != (law in STALE_CARD_LAWS):
            raise RuntimeError(f"ULS_CARD_QUOTE_DRIFT {index} stale={stale}")
        grade_tokens = kana("[R%d]" % grade, reverse, alloc)
        base_grade = int(fxt.visible(to_jp(pieces["grade"][2:3], inverse),
                                     {v: c for c, v in reverse.items()}))
        if (base_grade != grade) != (law in GRADE_FIXES):
            raise RuntimeError(f"ULS_CARD_GRADE_DRIFT {index} {base_grade}/{grade}")
        if law in GRADE_FIXES and GRADE_FIXES[law] != (base_grade, grade):
            raise RuntimeError(f"ULS_GRADE_FIX_DECL {law}")
        if base_grade == grade and grade_tokens != pieces["grade"]:
            raise RuntimeError(f"ULS_GRADE_ENCODING {index}")
        tokens = (grade_tokens + head + canon["tail"] + [("CTR_FUNC", fxt.LINE_BREAK)]
                  + canon["line2"]
                  + [("CTR_FUNC", fxt.PAGE_BREAK), ("CTR_FUNC", 0x42)]
                  + kinshi + [lquote] + name[law] + [rquote]
                  + [("CTR_FUNC", fxt.LINE_BREAK)]
                  + bonus + [lquote] + name[rec] + [rquote]
                  + [("CTR_FUNC", 0x40), ("CTR_FUNC", 0x42)])
        jp_tokens = to_jp(tokens, inverse)
        widths = fxt.line_widths(jp_tokens, jp_raw)
        pages = fxt.lines_per_page(jp_tokens)
        if max(widths) > bound["max_line_px"] or max(pages) > bound["max_lines_per_page"]:
            raise RuntimeError(f"ULS_CARD_OVER_BOUND {index} {widths} {pages}")
        out[index] = {"law": law, "recommended_law": rec, "grade": grade,
                      "base_grade": base_grade, "stale_quote": stale,
                      "us_text": text, "tokens": tokens, "widths": widths,
                      "pages": pages}
    return out


def encode_record(expected, path):
    data = stext.encode_standard(expected)
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"ULS_SERIALIZER_ROUNDTRIP_FAILED {path}")
    if data[-1] != 0:
        raise RuntimeError(f"ULS_EOS_MISSING {path}")
    return data


# ------------------------------------------------------------------ build ---

def derive():
    """Everything the layer writes, derived from the ROMs and the base image."""
    base = prev.build()[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"ULS_BASELINE_MISMATCH {sha(base)}")
    alloc, meta = uam.glyph_allocation()
    jp, us = meta["jp"], meta["us"]
    jp_raw = JP.read_bytes()
    decode, reverse = sysbat.charset_tables()
    render, inverse = renderer(alloc)
    bound_px, _law = prev.family_bound(jp, jp_raw)
    if bound_px != FAMILY_BOUND_PX:
        raise RuntimeError(f"ULS_FAMILY_BOUND_DRIFT {bound_px}")
    bound = fxt.leaf_bounds(jp, jp_raw)[CARD_LEAF]

    base_words = prev.us_words(base)["content"]
    before, after, kinshi = new_names(base_words, alloc, jp_raw, inverse)
    table = fxt.product_fx_text(base)
    cards_page, alias_page = table[CARD_LEAF], table[ALIAS_LEAF]
    if cards_page.tsize != alias_page.tsize or any(
            tokens_of(cards_page[i]) != tokens_of(alias_page[i])
            for i in range(cards_page.tsize)):
        raise RuntimeError("ULS_ALIAS_LEAF_NOT_EQUAL")
    us_card = us_cards(us, decode)
    law_of = us_law_ids(us, decode)
    cards = new_cards(cards_page, us_card, law_of, before, after, kinshi, reverse,
                      alloc, jp_raw, inverse, bound)
    names = [{"law_id": law,
              "us_name": fxt.visible(tokens_of(us.tabs["words"]["content"][NAME_BASE + law]),
                                     decode).strip(),
              "before": render(before[law]), "after": render(after[law])}
             for law in sorted(after)]
    card_rows = [{"us_logical_path": f"fx_text/{CARD_LEAF}/{i}", "law_id": c["law"],
                  "recommended_law_id": c["recommended_law"],
                  "us_text": c["us_text"], "grade": c["grade"],
                  "base_grade": c["base_grade"], "stale_quote": c["stale_quote"],
                  "before": render(tokens_of(cards_page[i])),
                  "after": render(c["tokens"]), "line_widths_px": c["widths"],
                  "lines_per_page": c["pages"]}
                 for i, c in sorted(cards.items())]
    return (base, alloc, meta, render, before, after, cards, names, card_rows,
            bound, table)


def check_manifest(doc, names, card_rows):
    names, card_rows = _us_source.redacted(names), _us_source.redacted(card_rows)
    if doc.get("names") != names:
        got = {r["law_id"]: r for r in names}
        bad = [r["law_id"] for r in doc.get("names", []) if got.get(r["law_id"]) != r]
        raise RuntimeError(f"ULS_MANIFEST_NAMES_DRIFT {bad[:8] or len(names)}")
    if doc.get("cards") != card_rows:
        got = {r["us_logical_path"]: r for r in card_rows}
        bad = [r["us_logical_path"] for r in doc.get("cards", [])
               if got.get(r["us_logical_path"]) != r]
        raise RuntimeError(f"ULS_MANIFEST_CARDS_DRIFT {bad[:8] or len(card_rows)}")


def build():
    (base, alloc, meta, render, before, after, cards, names, card_rows, bound,
     table) = derive()
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check_manifest(doc, names, card_rows)
    us_raw = US.read_bytes()

    raw = bytearray(base)
    snapshot = bytes(raw)
    base_words = prev.us_words(snapshot)["content"]
    if base_words.tsize != prev.US_CONTENT_ROOT[1]:
        raise RuntimeError("ULS_WORDS_ROOT_DECL_DRIFT")
    block_start = uam.free_block_start(base, stext.TAIL_START)
    cursor = block_start
    records = []
    for law in sorted(after):
        index = NAME_BASE + law
        path = f"words:content/{index}"
        data = encode_record(after[law], path)
        field = base_words.real_offset + index * 4
        old = u32(raw, field)
        raw[cursor:cursor + len(data)] = data
        struct.pack_into("<I", raw, field, ROM + cursor)
        records.append({"us_logical_path": path, "law_id": law,
                        "route": "WORDS_DIRECT_REPOINT",
                        "root_pointer_field_us_rom": f"0x{field:08X}",
                        "original_cpu_pointer": f"0x{old:08X}",
                        "new_cpu_pointer": f"0x{ROM + cursor:08X}",
                        "payload_length": len(data), "_expected": after[law]})
        cursor = stext.align(cursor + len(data), 4)

    page = table[CARD_LEAF]
    replacements = {}
    for index, card in sorted(cards.items()):
        path = f"fx_text/{CARD_LEAF}/{index}"
        flags = page[index].U16(0) & ~fxt.COMPRESSION_BIT
        replacements[index] = flags.to_bytes(2, "little") + encode_record(card["tokens"], path)
        records.append({"us_logical_path": path, "law_id": card["law"],
                        "route": "FX_LEAF_REBUILD", "record_flags": f"0x{flags:04X}",
                        "_expected": card["tokens"]})
    blob = stext.serialize_leaf(snapshot, page, replacements)
    leaf_at = cursor
    raw[cursor:cursor + len(blob)] = blob
    fields = {}
    for leaf in (CARD_LEAF, ALIAS_LEAF):
        field = fxt.FX_ROOT_FIELD + leaf * 4
        fields[leaf] = (f"0x{field:08X}", f"0x{u32(raw, field):08X}")
        struct.pack_into("<I", raw, field, ROM + leaf_at)
    cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("ULS_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("ULS_BLOCK_OUTSIDE_TAIL")
    if set(base[block_start:block_end]) != {0xFF} or \
            set(us_raw[block_start:block_end]) != {0xFF}:
        raise RuntimeError("ULS_BLOCK_NOT_FREE")
    for off in range(0, len(base) - 3, 4):
        value = u32(base, off)
        if ROM + block_start <= value < ROM + block_end and off < stext.TAIL_START \
                and value != u32(us_raw, off):
            raise RuntimeError("ULS_BLOCK_REFERENCED 0x%08X" % (ROM + off))
    layout = {"block": [f"0x{ROM + block_start:08X}", f"0x{ROM + block_end:08X}"],
              "fx_leaf_23": {"new_cpu_pointer": f"0x{ROM + leaf_at:08X}",
                             "bytes": len(blob),
                             "root_fields_before": {str(k): v for k, v in fields.items()}}}
    return (bytes(raw), base, meta, alloc, records, doc, names, card_rows, layout,
            render)


# --------------------------------------------------------------- validate ---

def validate(product, base, alloc, records, layout, render):
    audits = {}
    if sha(OUTROM) != sha(product):
        raise RuntimeError("ULS_READBACK_ROM_MISMATCH")
    disk = OUTROM.read_bytes()
    words = prev.us_words(disk)["content"]
    fx = fxt.product_fx_text(disk)
    leaf_ptr = int(layout["fx_leaf_23"]["new_cpu_pointer"], 16)
    for leaf in (CARD_LEAF, ALIAS_LEAF):
        if u32(disk, fxt.FX_ROOT_FIELD + leaf * 4) != leaf_ptr:
            raise RuntimeError(f"ULS_READBACK_FX_ROOT {leaf}")
    readback = 0
    for rec in records:
        path = rec["us_logical_path"]
        if rec["route"] == "WORDS_DIRECT_REPOINT":
            got = [tokens_of(words[int(path.rsplit("/", 1)[1])])]
            if u32(disk, int(rec["root_pointer_field_us_rom"], 16)) != \
                    int(rec["new_cpu_pointer"], 16):
                raise RuntimeError(f"ULS_READBACK_ROOT {path}")
        else:
            index = int(path.rsplit("/", 1)[1])
            got = [tokens_of(fx[CARD_LEAF][index]), tokens_of(fx[ALIAS_LEAF][index])]
        if any(g != rec["_expected"] for g in got):
            raise RuntimeError(f"ULS_READBACK_TOKEN_MISMATCH {path}")
        readback += len(got)
    audits["readback"] = {"records": readback, "failures": 0, "result": "PASS",
                          "source": "independent parse of the written ROM file"}

    # every glyph draws JP retail's pixels at JP retail's width, none new
    jp_raw = JP.read_bytes()
    inverse = {slot: code for code, slot in alloc.items()}
    jp_font = pta.glyphs.bitmaps(pta.glyphs.font_of(str(JP), "jp"), 0xC66)
    pr_font = pta.glyphs.bitmaps(pta.glyphs.font_of(str(OUTROM), "us"), 0xC67)
    checked = 0
    for rec in records:
        for kind, slot in rec["_expected"]:
            if not kind.startswith("CHR"):
                continue
            code = inverse.get(slot)
            if code is None:
                raise RuntimeError("ULS_SLOT_NOT_ALLOCATED 0x%04X" % slot)
            if pr_font.get(slot) != jp_font.get(code):
                raise RuntimeError("ULS_GLYPH_PIXELS 0x%04X" % slot)
            if product[stext.US_METADATA + slot] != jp_raw[stext.JP_METADATA + code]:
                raise RuntimeError("ULS_GLYPH_WIDTH 0x%04X" % slot)
            checked += 1
    if product[stext.US_METADATA:stext.US_METADATA + 0xC67] != \
            base[stext.US_METADATA:stext.US_METADATA + 0xC67]:
        raise RuntimeError("ULS_FONT_METADATA_TOUCHED")
    audits["glyphs"] = {"tokens_checked": checked, "new_glyph_records": 0,
                        "result": "PASS"}

    # the whole tracked text corpus is otherwise identical to the baseline
    expected = sorted(r["us_logical_path"] for r in records) + sorted(
        f"fx_text/{ALIAS_LEAF}/{r['us_logical_path'].rsplit('/', 1)[1]}"
        for r in records if r["route"] == "FX_LEAF_REBUILD")
    changed, total = [], 0
    tmp = RUN_BASE / "_corpus"
    tmp.mkdir(parents=True, exist_ok=True)
    a = tmp / "a.gba"
    a.write_bytes(base)
    try:
        ra, rb = pta.glyphs.us_rom(str(a)), pta.glyphs.us_rom(str(OUTROM))
        fa, fb = pta.glyphs.us_fx_text(str(a)), pta.glyphs.us_fx_text(str(OUTROM))
        for fam in sorted(ra.tabs["words"].keys()):
            ta, tb = ra.tabs["words"][fam], rb.tabs["words"][fam]
            for i in range(min(ta.tsize, tb.tsize)):
                total += 1
                if tokens_of(ta[i]) != tokens_of(tb[i]):
                    changed.append("words:%s/%d" % (fam, i))
        for leaf in range(fa.tsize):
            try:
                la, lb = fa[leaf], fb[leaf]
            except Exception:                                        # noqa: BLE001
                continue
            for i in range(min(getattr(la, "tsize", 0), getattr(lb, "tsize", 0))):
                total += 1
                if tokens_of(la[i]) != tokens_of(lb[i]):
                    changed.append("fx_text/%d/%d" % (leaf, i))
    finally:
        a.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass
    if sorted(changed) != sorted(expected):
        raise RuntimeError("ULS_CORPUS_DRIFT %s" % sorted(set(changed) ^ set(expected))[:8])
    audits["corpus"] = {"records_compared": total, "records_changed": len(changed),
                        "result": "PASS"}

    # no other root moved: words:content and fx_text
    owned = {int(r["us_logical_path"].rsplit("/", 1)[1]) for r in records
             if r["route"] == "WORDS_DIRECT_REPOINT"}
    before = prev.us_words(base)["content"]
    moved = [i for i in range(before.tsize) if i not in owned
             and u32(product, before.real_offset + i * 4) != u32(base, before.real_offset + i * 4)]
    moved += [f"fx{i}" for i in range(fxt.FX_TSIZE) if i not in (CARD_LEAF, ALIAS_LEAF)
              and u32(product, fxt.FX_ROOT_FIELD + i * 4) != u32(base, fxt.FX_ROOT_FIELD + i * 4)]
    if moved:
        raise RuntimeError(f"ULS_SIBLING_ROOT_MOVED {moved[:8]}")
    diff = [i for i in range(len(base)) if product[i] != base[i]]
    lo, hi = int(layout["block"][0], 16) - ROM, int(layout["block"][1], 16) - ROM
    roots = {before.real_offset + i * 4 + k for i in owned for k in range(4)}
    roots |= {fxt.FX_ROOT_FIELD + leaf * 4 + k for leaf in (CARD_LEAF, ALIAS_LEAF)
              for k in range(4)}
    stray = [i for i in diff if not (lo <= i < hi) and i not in roots]
    if stray:
        raise RuntimeError("ULS_UNEXPLAINED_BYTES %s" % [f"0x{i:08X}" for i in stray[:8]])
    audits["binary_touch"] = {"bytes_changed": len(diff), "block_bytes": hi - lo,
                              "root_fields_repointed": len(owned) + 2,
                              "executable_bytes_changed": 0,
                              "graphics_bytes_changed": 0, "font_bytes_changed": 0,
                              "result": "PASS"}
    audits["layout"] = layout
    return audits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20261010_production")
    ap.add_argument("--print-sha", action="store_true")
    ap.add_argument("--emit-manifest", help="write the derived names/cards here and stop")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    if args.emit_manifest:
        _b, _a, _m, _r, _bf, _af, _c, names, card_rows, _bd, _t = derive()
        doc = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
        doc.update({"count_names": len(names), "count_cards": len(card_rows),
                    "names": names, "cards": card_rows})
        write(Path(args.emit_manifest), doc)
        print(f"names {len(names)} cards {len(card_rows)} -> {args.emit_manifest}")
        return 0
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return 0
    second = build()
    if sha(first[0]) != sha(second[0]):
        raise RuntimeError("ULS_BUILD_NONDETERMINISTIC")
    product, base, _meta, alloc, records, doc, names, card_rows, layout, render = first
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("ULS_DISK_READBACK_MISMATCH")
    audits = validate(product, base, alloc, records, layout, render)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "milestone": "law names and antilaw cards in the US law semantics",
        "baseline_sha256": sha(base), "production_sha256": sha(product),
        "rom": str(OUTROM), "evidence": doc.get("evidence"),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True},
        "names": len(names), "cards": len(card_rows),
        "records": [{k: v for k, v in r.items() if not k.startswith("_")}
                    for r in records],
        "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "records"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
