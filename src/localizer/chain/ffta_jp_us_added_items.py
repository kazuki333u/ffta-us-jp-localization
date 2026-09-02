#! python3
# -*- coding: utf-8 -*-
"""US-added *item identity* over a real JP slot: the Stuffed Bear, localized (RC24).

The defect
----------

RC23 prints JP retail's own name for mission item **0x48**.  That is wrong,
because the two builds do not put the same object in that slot:

* the **US** build's item 0x48 is ``Stuffed Bear``, Mewt's keepsake, and its
  mission-table entry for the US-added postgame mission **383 ``Memories``**
  (`US 0x0855AE92 + 70*382`, byte ``+0x36`` = ``0x48``) is the *only* reference
  to it in either image;
* the **JP** build's item 0x48 is a time-magic tome, and **no JP mission
  requires it** -- the aligned JP entry for mission 383 is empty (the whole
  377..406 block is US-added).

So JP retail has no name for this object, and the source-transfer layers
(`ffta_jp_chr_half_universal_repoint` for the name, `ffta_jp_fx_text_recovered`
for the description) took the JP record at the same index anyway.

Why RC16/RC17 did not catch it
------------------------------

`ffta_placeholder_transfer_audit.py` asks whether the aligned JP record matches
a **developer placeholder** pattern (``クエスト<n>``, ``ダミー<n>``, ``予備<n>``).
Here the JP record is real, shipped, player-visible Japanese, so the guard does
not fire.  Same failure *mode* -- an aligned index treated as an identity -- and
a different failure *class*.  `ffta_us_added_item_identity_audit.py` is that
class's audit: it decides identity from the mission table instead of the text,
over all 127 mission-item slots, and finds exactly this one.

What this layer does
--------------------

The translation is not computed here.  It is read from the tracked manifest
``data/us_added_items_translations.json`` -- the editorial decision of record,
a **project-authored translation**, not a JP retail asset -- and every claim it
makes is re-proved on every build:

* the US record still reads the English the manifest was authored against;
* the JP record at the same index is **not** a developer placeholder, and the
  base image still ships exactly that JP record put through the chain's glyph
  allocation (so this really is the transfer being corrected);
* the item is a ``US_ADDED_ITEM_IDENTITY`` by the audit's own mission-table
  test, re-run here;
* every kanji names a JP retail record that actually uses that glyph code, and
  every glyph is already in the chain's allocation (**zero new glyphs**);
* every line is measured against the family's JP originals, with the ``{5103}``
  reference expanded to ``ミュート`` so the substitution is not scored as zero;
* the ``{5103}`` reference of the US original survives, English typesetting does
  not, and the record's **statistics page is retained from the base image
  byte-for-byte** -- the builder replaces the prose page only.

Mechanism: ``words:content`` is direct-addressed (one 4-byte root field,
repointed at a new payload in the free tail); ``fx_text/25`` is a ``text_page``
leaf, re-parsed out of the in-process product, re-serialized with only the one
target replaced, placed in the free tail and repointed.  **Zero executable
bytes, zero graphics bytes, zero font or metadata writes.**

Layer position: a new terminal layer above RC23's ``ffta_jp_item_job_strip``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_item_job_strip as prev
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_added_missions as uam
import ffta_jp_us_only_fx_text as fxt
import ffta_jp_us_only_system_battle as sysbat
import ffta_placeholder_transfer_audit as pta
import ffta_us_added_item_identity_audit as identity
from ffta_sect import (c_ffta_sect_rom, c_ffta_sect_text_buf,       # noqa: E402
                       _trim_raw_len, _words_sect_info)
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_added_items_translations.json"
RUN_BASE = HERE / "build/us_added_items"
OUTROM = ROOT / "rom/build/ffta_us_jp_us_added_items.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_us_added_items_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_item_job_strip (RC23).
BASELINE = "6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6"
# Pinned after the first deterministic build of this layer (RC24).
EXPECTED_PRODUCTION = "F1D673A1966C6C42B6F2CEF157F11EF984BB61E6A2184D7F0F47AC17EE2CA695"

TOTAL = 2
FAMILIES = {"words:content": 1, "fx_text/25": 1}
EXPECTED_INDEXES = {"words:content": [570], "fx_text/25": [71]}
ITEM_ID = 0x48
US_CONTENT_ROOT = (0x18DA4, 0x2F2)     # ffta_sect.load_rom_us words:content
FX_LEAF = 25
FX_LEAF_TSIZE = 127
JP_FX_LEAF = 24                        # US leaf 24 is US-only, so JP is one lower
FX_ROOT_FIELD = fxt.FX_ROOT_FIELD      # 0x0036D678

LINE_BREAK, PAGE_BREAK = 0x4D, 0x4F
CTR_CLOSE, CTR_TERMINATOR, CTR_WORD_SPACE = 0x40, 0x42, 0x52
# Controls that are typesetting, not meaning.  Everything else in the US
# original is a reference this translation has to keep.
LAYOUT_CONTROLS = {0x40, 0x42, 0x4D, 0x4F, 0x52, 0x56,
                   0x1D01, 0x1D02, 0x1D03, 0x1D04, 0x1D08, 0x1D09,
                   0x1D0F, 0x1D10, 0x1D1B, 0x1D20}
FORBIDDEN_CONTROLS = {0x1D02, 0x1D03, 0x1D04}
SUBST_TOKEN_MASK, SUBST_TOKEN = 0xFF00, 0x0400
ENCODING_BITS = 0x0003                 # bit1 compression, bit0 the YA text_buf
# The renderer draws the item name directly above the description, so the
# description's own bound is the leaf's; the name's is words:content's.
NAME_BOUND_PX = 86                     # JP words:content/507
PROSE_BOUND_PX = 192                   # JP fx_text/24 prose pages (29, 115)
PROSE_MAX_LINES = 2
REFERENCE_EXPANSION = {0x5103: "ミュート"}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def tokens_of(line):
    return uam.tokens_of(line)


def u32(blob, offset):
    return struct.unpack_from("<I", blob, offset)[0]


def us_words(raw):
    data = bytes(raw)
    return c_ffta_sect_rom(data, 0).setup(
        _words_sect_info({"content": US_CONTENT_ROOT}),
        _trim_raw_len(data, 0xF00000)).tabs["words"]


def pages_of(tokens):
    """Split a record's tokens at its page terminators, terminator included."""
    pages, current = [], []
    for kind, value in tokens:
        current.append((kind, value))
        if kind == "CTR_FUNC" and value == CTR_TERMINATOR:
            pages.append(current)
            current = []
    if current:
        pages.append(current)
    return pages


def line_widths_expanded(tokens, jp_raw, reverse):
    """Line widths with every reference control expanded to what it prints.

    ``fxt.line_widths`` scores a ``{51xx}`` reference as zero, because it is a
    control token.  At runtime it prints a name, so the line is that much wider;
    the bound has to be checked against the width the player actually sees.
    """
    expanded = []
    for kind, value in tokens:
        text = REFERENCE_EXPANSION.get(value) if kind == "CTR_FUNC" else None
        if text is None:
            expanded.append((kind, value))
            continue
        for char in text:
            if char not in reverse:
                raise RuntimeError(f"UAI_EXPANSION_UNENCODABLE {char!r}")
            expanded.append(("CHR_FULL", reverse[char]))
    return fxt.line_widths(expanded, jp_raw)


def significant(tokens):
    """The controls that carry meaning: references, not typesetting."""
    return {value for kind, value in tokens
            if kind == "CTR_FUNC" and value not in LAYOUT_CONTROLS}


def load_manifest(jp, us, us_raw, decode):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = doc["entries"]
    if len(rows) != TOTAL or doc["count"] != TOTAL or doc["translated_count"] != TOTAL:
        raise RuntimeError(f"UAI_MANIFEST_COUNT {len(rows)}/{doc['count']}")
    if doc["baseline_production_sha256"] != BASELINE:
        raise RuntimeError("UAI_MANIFEST_BASELINE_DRIFT")
    if doc.get("families") != FAMILIES:
        raise RuntimeError("UAI_MANIFEST_FAMILIES")
    if not doc.get("provenance", "").startswith("PROJECT_AUTHORED_TRANSLATION"):
        raise RuntimeError("UAI_MANIFEST_PROVENANCE")
    got = {}
    us_fx = us.tabs["fx_text"][FX_LEAF]
    for row in rows:
        fam = row["family"]
        if fam not in FAMILIES:
            raise RuntimeError(f"UAI_MANIFEST_FAMILY {fam}")
        index = row["us_entry"] if fam == "fx_text/25" else row["us_index"]
        got.setdefault(fam, []).append(index)
        if row["us_logical_path"] != f"{fam}/{index}":
            raise RuntimeError(f"UAI_MANIFEST_PATH {row['us_logical_path']}")
        if row["status"] != "TRANSLATED_REVIEWED" or row.get("source") != "EDITORIAL":
            raise RuntimeError(f"UAI_MANIFEST_STATUS {row['us_logical_path']}")
        if not row["japanese"]:
            raise RuntimeError(f"UAI_MANIFEST_EMPTY {row['us_logical_path']}")
        if row.get("item_id") != "0x%02X" % ITEM_ID:
            raise RuntimeError(f"UAI_MANIFEST_ITEM_ID {row['us_logical_path']}")
        original = (tokens_of(us_fx[index]) if fam == "fx_text/25"
                    else tokens_of(us.tabs["words"]["content"][index]))
        if not _us_source.matches(row, fxt.visible(original, decode)):
            raise RuntimeError(f"UAI_MANIFEST_ENGLISH_DRIFT {row['us_logical_path']}")
        # every later check reads the English out of the ROM, not the manifest:
        # the manifest may carry only a digest of it (public builds).
        row["_us_text"] = fxt.visible(original, decode)
        row["_original"] = original
        row["_jp"] = (tokens_of(jp.tabs["fx_text"][JP_FX_LEAF][index])
                      if fam == "fx_text/25"
                      else tokens_of(jp.tabs["words"]["content"][index]))
    if {k: sorted(v) for k, v in got.items()} != EXPECTED_INDEXES:
        raise RuntimeError(f"UAI_MANIFEST_INDEX_SET {sorted(got)}")
    return doc, rows


def check_target(jp_names, row, alloc, base_tokens):
    """Prove the target is this failure class, not RC17's, and still untouched.

    * the aligned JP record is real content, not a developer placeholder --
      otherwise this would be RC17's family and RC17's layer would own it;
    * the base image ships exactly that JP record, put through the chain's own
      glyph allocation -- so the bytes being replaced are the mis-transfer.
    """
    jp_tokens = row["_jp"]
    jp_text = pta._clean("".join(jp_names.get(v, "<%04X>" % v) if k.startswith("CHR")
                                 else "{%X}" % v for k, v in jp_tokens))
    if not any(k.startswith("CHR") for k, _ in jp_tokens):
        raise RuntimeError(f"UAI_JP_RECORD_EMPTY {row['us_logical_path']}")
    if pta.JP_PLACEHOLDER.match(jp_text):
        raise RuntimeError(f"UAI_JP_IS_PLACEHOLDER {row['us_logical_path']} {jp_text!r}")
    if row.get("jp_same_slot_is_placeholder") is not False:
        raise RuntimeError(f"UAI_MANIFEST_PLACEHOLDER_CLAIM {row['us_logical_path']}")
    if pta.US_PLACEHOLDER.match(pta._clean(row["_us_text"])):
        raise RuntimeError(f"UAI_US_SIDE_IS_PLACEHOLDER {row['us_logical_path']}")
    if base_tokens != uam.to_slots(jp_tokens, alloc):
        raise RuntimeError(f"UAI_BASE_IS_NOT_THE_JP_TRANSFER {row['us_logical_path']}")
    return "JP_SAME_SLOT_IS_A_DIFFERENT_OBJECT"


def check_controls(row, translated, retained):
    """Reference controls survive; English typesetting does not."""
    path = row["us_logical_path"]
    us_refs = significant(row["_original"])
    jp_refs = significant(translated) | significant(retained)
    if us_refs != jp_refs:
        raise RuntimeError(f"UAI_CONTROL_SET {path} us={sorted(map(hex, us_refs))} "
                           f"jp={sorted(map(hex, jp_refs))}")
    if any(k == "CTR_FUNC" and v in FORBIDDEN_CONTROLS for k, v in translated):
        raise RuntimeError(f"UAI_ENGLISH_TYPESETTING_KEPT {path}")
    if any(k == "CTR_FUNC" and v == CTR_WORD_SPACE for k, v in translated):
        raise RuntimeError(f"UAI_WORD_SPACE_KEPT {path}")
    if any(k == "CTR_FUNC" and (v & SUBST_TOKEN_MASK) == SUBST_TOKEN
           for k, v in translated):
        raise RuntimeError(f"UAI_SUBSTITUTION_TOKEN_IN_VALUE {path}")
    if any(k == "CTR_EOS" for k, _ in translated):
        raise RuntimeError(f"UAI_EOS_SEPARATOR {path}")
    if row["family"] == "words:content" and any(k == "CTR_FUNC" for k, _ in translated):
        raise RuntimeError(f"UAI_WORDS_CONTROL {path}")
    return {"references_preserved": sorted("0x%04X" % v for v in us_refs)}


RATING = re.compile(r":(.)")


def check_ratings(row, jp_names, retained_len):
    """The retained statistics page states the US item's own four ratings.

    The page is JP retail's, so keeping it is only correct if the US record it
    replaces says the same thing.  Both sides are read out, not assumed: the US
    original from its own decoded English, the retained page from the JP retail
    record's own trailing tokens.
    """
    us_ratings = RATING.findall(re.sub(r"\[[0-9A-F]+\]", "", row["_us_text"]))
    jp_tail = row["_jp"][-retained_len:]
    jp_text = "".join(jp_names.get(v, "<%04X>" % v) if k.startswith("CHR")
                      else "{%X}" % v for k, v in jp_tail)
    jp_ratings = RATING.findall(re.sub(r"\{[0-9A-F]+\}", "", jp_text))
    if len(us_ratings) != 4 or len(jp_ratings) != 4:
        raise RuntimeError(f"UAI_RATING_COUNT us={us_ratings} jp={jp_ratings}")
    if set(us_ratings) != {"-"} or set(jp_ratings) != {"-"}:
        raise RuntimeError(f"UAI_RATING_DIVERGED us={us_ratings} jp={jp_ratings}")
    return {"us_ratings": us_ratings, "retained_page_ratings": jp_ratings}


def encode(row, reverse, kanji, alloc, jp_raw, base_tokens):
    path, family = row["us_logical_path"], row["family"]
    jp_tokens = uam.parse_markup(row["japanese"], reverse, kanji)
    # The manifest declares the *prose*: its widths and line count are measured
    # before the page terminator is appended, so the empty segment after the
    # break is not scored as a line.
    prose = list(jp_tokens)

    retained = []
    if family == "fx_text/25":
        pages = pages_of(base_tokens)
        if len(pages) != 2:
            raise RuntimeError(f"UAI_BASE_PAGE_COUNT {path} {len(pages)}")
        retained = pages[1]
        if row.get("pages_retained_from_base") != 1:
            raise RuntimeError(f"UAI_MANIFEST_RETAINED_PAGES {path}")
        # the prose page carries the page break; the retained page the close
        jp_tokens = jp_tokens + [("CTR_FUNC", PAGE_BREAK), ("CTR_FUNC", CTR_TERMINATOR)]
        if retained[-2:] != [("CTR_FUNC", CTR_CLOSE), ("CTR_FUNC", CTR_TERMINATOR)]:
            raise RuntimeError(f"UAI_RETAINED_CLOSE {path}")

    control = check_controls(row, jp_tokens, retained)
    widths = fxt.line_widths(prose, jp_raw)
    shown = line_widths_expanded(prose, jp_raw, reverse)
    lines = fxt.lines_per_page(prose)
    bound = NAME_BOUND_PX if family == "words:content" else PROSE_BOUND_PX
    if max(shown) > bound or max(widths) > bound:
        raise RuntimeError(f"UAI_WIDTH_OVER_FAMILY_BOUND {path} {max(shown)}>{bound}")
    if family == "fx_text/25" and lines[0] > PROSE_MAX_LINES:
        raise RuntimeError(f"UAI_LINES_OVER_FAMILY_BOUND {path} {lines[0]}")
    if row.get("line_widths_px") != widths or row.get("lines") != lines[0]:
        raise RuntimeError(f"UAI_MANIFEST_WIDTH_DRIFT {path} {widths} {lines}")
    if family == "fx_text/25" and row.get("line_widths_px_reference_expanded") != shown:
        raise RuntimeError(f"UAI_MANIFEST_EXPANDED_WIDTH_DRIFT {path} {shown}")

    # `retained` came out of the base image and is already in production font
    # slots; only the new prose goes through the allocation.
    expected = uam.to_slots(jp_tokens, alloc) + retained
    data = stext.encode_standard(expected)
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"UAI_SERIALIZER_ROUNDTRIP_FAILED {path}")
    if data[-1] != 0:
        raise RuntimeError(f"UAI_EOS_MISSING {path}")
    return jp_tokens, retained, expected, data, widths, shown, lines, control


def build():
    product_prev, _base_prev, _meta_prev = prev.build()
    base = product_prev
    if sha(base) != BASELINE:
        raise RuntimeError(f"UAI_BASELINE_MISMATCH {sha(base)}")
    alloc, meta = uam.glyph_allocation()
    jp, us = meta["jp"], meta["us"]
    jp_raw, us_raw = JP.read_bytes(), US.read_bytes()
    decode, reverse = sysbat.charset_tables()
    jp_names = pta.glyphs.charset()

    # The audit's own mission-table test, re-run: this item, and only this item,
    # is a US-added identity in the whole 127-slot family.
    audit_summary, _rows = identity.audit(production=None)
    if audit_summary["us_added_item_identities"] != ["0x%02X" % ITEM_ID]:
        raise RuntimeError("UAI_IDENTITY_AUDIT_DISAGREES %s"
                           % audit_summary["us_added_item_identities"])
    if audit_summary["items_checked"] != identity.ITEM_COUNT:
        raise RuntimeError("UAI_IDENTITY_AUDIT_SIZE")

    doc, rows = load_manifest(jp, us, us_raw, decode)
    kanji = uam.kanji_table(doc, jp)

    raw = bytearray(base)
    snapshot = bytes(raw)
    block_start = uam.free_block_start(base, int(_meta_prev["block"]["end"], 16))
    cursor = block_start
    records = []

    base_words = us_words(snapshot)["content"]
    base_leaf = fxt.product_fx_text(snapshot)[FX_LEAF]
    if base_leaf.tsize != FX_LEAF_TSIZE:
        raise RuntimeError(f"UAI_FX_LEAF_TSIZE {base_leaf.tsize}")
    if base_words.tsize != US_CONTENT_ROOT[1]:
        raise RuntimeError("UAI_WORDS_ROOT_DECL_DRIFT")

    # -- words:content  direct-entry repoint --------------------------------
    row = next(r for r in rows if r["family"] == "words:content")
    index = row["us_index"]
    base_tokens = tokens_of(base_words[index])
    reason = check_target(jp_names, row, alloc, base_tokens)
    (_jp_tokens, _retained, expected, data, widths, shown, lines,
     control) = encode(row, reverse, kanji, alloc, jp_raw, base_tokens)
    field = base_words.real_offset + index * 4
    old = u32(raw, field)
    raw[cursor:cursor + len(data)] = data
    struct.pack_into("<I", raw, field, ROM + cursor)
    records.append({
        "us_logical_path": row["us_logical_path"], "family": row["family"],
        "us_index": index, "item_id": row["item_id"],
        "original_english_sha256": _us_source.digest(row),
        "japanese": row["japanese"], "status": row["status"],
        "us_only_reason": reason, "route": "WORDS_DIRECT_REPOINT",
        "rendered_width_px": max(widths), "line_widths_px": widths,
        "width_bound_px": NAME_BOUND_PX,
        "root_pointer_field_us_rom": f"0x{field:08X}",
        "original_cpu_pointer": f"0x{old:08X}",
        "new_cpu_pointer": f"0x{ROM + cursor:08X}",
        "payload_offset_us_rom": f"0x{cursor:08X}",
        "payload_length": len(data), "eos": True, "roundtrip": "PASS",
        "controls": control, "_expected": expected, "_data": data})
    words_block_end = cursor + len(data)
    cursor = stext.align(words_block_end, 4)

    # -- fx_text/25  whole-leaf recomposition -------------------------------
    row = next(r for r in rows if r["family"] == "fx_text/25")
    entry = row["us_entry"]
    base_tokens = tokens_of(base_leaf[entry])
    reason = check_target(jp_names, row, alloc, base_tokens)
    (_jp_tokens, retained, expected, data, widths, shown, lines,
     control) = encode(row, reverse, kanji, alloc, jp_raw, base_tokens)
    ratings = check_ratings(row, jp_names, len(retained))
    flags = base_leaf[entry].U16(0) & ~ENCODING_BITS
    replacements = {entry: flags.to_bytes(2, "little") + data}
    blob = stext.serialize_leaf(snapshot, base_leaf, replacements)
    fx_offset = cursor
    fx_field = FX_ROOT_FIELD + FX_LEAF * 4
    old_fx = u32(raw, fx_field)
    raw[cursor:cursor + len(blob)] = blob
    struct.pack_into("<I", raw, fx_field, ROM + cursor)
    block_end = cursor + len(blob)
    cursor = stext.align(block_end, 4)
    records.append({
        "us_logical_path": row["us_logical_path"], "family": row["family"],
        "us_leaf": FX_LEAF, "us_entry": entry, "item_id": row["item_id"],
        "original_english_sha256": _us_source.digest(row),
        "japanese": row["japanese"], "status": row["status"],
        "us_only_reason": reason, "route": "FX_LEAF_RECOMPOSE",
        "rendered_width_px": max(widths), "line_widths_px": widths,
        "line_widths_px_reference_expanded": shown,
        "width_bound_px": PROSE_BOUND_PX, "prose_lines": lines[0],
        "pages_retained_from_base": 1,
        "retained_page_tokens": len(retained), "ratings": ratings,
        "record_flags": f"0x{flags:04X}", "payload_length": len(data),
        "eos": True, "roundtrip": "PASS", "controls": control,
        "_expected": expected, "_data": data})
    fx_record = {
        "family": "fx_text/25", "root_field_us_rom": f"0x{fx_field:08X}",
        "old_cpu_pointer": f"0x{old_fx:08X}",
        "new_cpu_pointer": f"0x{ROM + fx_offset:08X}",
        "previous_leaf_us_rom": f"0x{base_leaf.real_offset:08X}",
        "previous_size": base_leaf.sect_top,
        "new_leaf_us_rom": f"0x{fx_offset:08X}", "new_size": len(blob),
        "entries": base_leaf.tsize, "records_replaced": 1,
        "records_preserved": base_leaf.tsize - 1}

    if len(raw) != len(base):
        raise RuntimeError("UAI_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("UAI_BLOCK_OUTSIDE_TAIL")
    if set(base[block_start:block_end]) != {0xFF} or \
            set(us_raw[block_start:block_end]) != {0xFF}:
        raise RuntimeError("UAI_BLOCK_NOT_FREE")
    # Nothing already in the image may reach into the new block.  A word is a
    # real reference only if it can be *loaded* as one: the roots and literal
    # pools all live below the free tail, and the tail itself holds only payload
    # the chain wrote -- text bytes and the u16 offset arrays of relocated
    # leaves, whose adjacent halves regularly read as a tail address (72 such
    # words exist in the RC23 image).  So a word inside the tail is data, and a
    # word below it is refused unless pristine US already had that value.
    coincidences = []
    for off in range(0, len(base) - 3, 4):
        value = u32(base, off)
        if not (ROM + block_start <= value < ROM + block_end):
            continue
        if off < stext.TAIL_START and value != u32(us_raw, off):
            raise RuntimeError("UAI_BLOCK_REFERENCED 0x%08X" % (ROM + off))
        if value == ROM + block_start:
            raise RuntimeError("UAI_BLOCK_START_ALREADY_REFERENCED 0x%08X" % (ROM + off))
        coincidences.append("0x%08X=0x%08X" % (ROM + off, value))

    layout = {"block_start": block_start, "words_block_end": words_block_end,
              "fx_leaf": fx_offset, "block_end": block_end,
              "words_resembling_block_addresses": coincidences}
    return (bytes(raw), base, meta, alloc, records, doc, rows, fx_record, layout,
            kanji, audit_summary)


def validate(product, base, meta, alloc, records, rows, fx_record, layout, doc,
             audit_summary):
    us = meta["us"]
    us_raw, jp_raw = US.read_bytes(), JP.read_bytes()
    block_start, block_end = layout["block_start"], layout["block_end"]
    audits = {}

    # -- nothing outside the new block and the two repointed root fields ----
    fx_field = FX_ROOT_FIELD + FX_LEAF * 4
    name_field = int(records[0]["root_pointer_field_us_rom"], 16)
    diff = {i for i in range(len(base)) if product[i] != base[i]}
    expected = (set(range(block_start, block_end))
                | set(range(name_field, name_field + 4))
                | set(range(fx_field, fx_field + 4)))
    if diff - expected:
        raise RuntimeError("UAI_UNEXPLAINED_BYTES %s"
                           % sorted(hex(d) for d in diff - expected)[:8])
    audits["binary_touch"] = {
        "bytes_changed": len(diff),
        "new_block_bytes": block_end - block_start,
        "root_fields_repointed": [f"0x{name_field:08X}", f"0x{fx_field:08X}"],
        "executable_bytes_changed": 0,
        "graphics_bytes_changed": 0,
        "font_bytes_changed": 0,
    }
    # the font and its metadata are untouched: no new glyph was made
    if product[stext.US_METADATA:stext.US_METADATA + 0xC67] != \
            base[stext.US_METADATA:stext.US_METADATA + 0xC67]:
        raise RuntimeError("UAI_FONT_METADATA_TOUCHED")

    # -- independent ROM readback -------------------------------------------
    if sha(OUTROM) != sha(product):
        raise RuntimeError("UAI_READBACK_ROM_MISMATCH")
    disk = Path(OUTROM).read_bytes()
    inverse = {slot: code for code, slot in alloc.items()}
    decode, _reverse = sysbat.charset_tables()
    decode = dict(decode)
    decode.update({int(r["jp_glyph_code"], 16): ch
                   for ch, r in doc["kanji_codes"].items()})

    def char(value):
        if value in inverse:
            return decode.get(inverse[value], f"<jp:{inverse[value]:04X}>")
        return f"<us:{value:04X}>"

    def render(tokens):
        return "".join(char(v) if k.startswith("CHR") else "{%X}" % v
                       for k, v in tokens)

    words_check = us_words(disk)["content"]
    fx_check = fxt.product_fx_text(disk)[FX_LEAF]
    readback = []
    for rec in records:
        if rec["route"] == "WORDS_DIRECT_REPOINT":
            tokens = uam.tokens_of(words_check[rec["us_index"]])
            want = rec["japanese"]
        else:
            tokens = uam.tokens_of(fx_check[rec["us_entry"]])
            want = rec["japanese"] + "{4F}{42}"
        if tokens != rec["_expected"]:
            raise RuntimeError(f"UAI_READBACK_TOKEN_MISMATCH {rec['us_logical_path']}")
        got = render(tokens)
        if not got.startswith(want):
            raise RuntimeError(f"UAI_READBACK_TEXT_MISMATCH {rec['us_logical_path']} {got!r}")
        readback.append({"us_logical_path": rec["us_logical_path"], "decoded": got,
                         "result": "PASS"})
    if u32(disk, fx_field) != int(fx_record["new_cpu_pointer"], 16):
        raise RuntimeError("UAI_READBACK_FX_ROOT")
    audits["readback"] = {"entries": len(readback), "failures": 0,
                          "source": "independent parse of the written ROM file, "
                                    "following its own words / fx_text root pointers",
                          "result": "PASS", "rows": readback}

    # -- the JP retail text is gone from both records -----------------------
    jp_names = pta.glyphs.charset()
    by_path = {r["us_logical_path"]: r for r in rows}
    for rec in records:
        jp_slots = uam.to_slots(by_path[rec["us_logical_path"]]["_jp"], alloc)
        if rec["route"] == "WORDS_DIRECT_REPOINT":
            if uam.tokens_of(words_check[rec["us_index"]]) == jp_slots:
                raise RuntimeError("UAI_STILL_JP_SAME_SLOT name")
        else:
            got = uam.tokens_of(fx_check[rec["us_entry"]])
            if got == jp_slots:
                raise RuntimeError("UAI_STILL_JP_SAME_SLOT description")
            # ... but the statistics page is exactly the JP retail page
            keep = rec["retained_page_tokens"]
            if got[-keep:] != jp_slots[-keep:]:
                raise RuntimeError("UAI_RETAINED_PAGE_DIVERGED")
    audits["jp_same_slot_removed"] = {"records": len(records), "result": "PASS"}

    # -- every glyph draws JP retail's own pixels at JP retail's own width ---
    jp_font = pta.glyphs.bitmaps(pta.glyphs.font_of(str(JP), "jp"), 0xC66)
    tmp = RUN_BASE / "_font_probe.gba"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(product)
    pr_font = pta.glyphs.bitmaps(pta.glyphs.font_of(str(tmp), "us"), 0xC67)
    tmp.unlink()
    checked = 0
    for rec in records:
        for kind, slot in rec["_expected"]:
            if kind != "CHR_FULL":
                continue
            code = inverse.get(slot)
            if code is None:
                raise RuntimeError("UAI_SLOT_NOT_ALLOCATED 0x%04X" % slot)
            if pr_font.get(slot) != jp_font.get(code):
                raise RuntimeError("UAI_GLYPH_PIXELS 0x%04X->0x%04X" % (code, slot))
            if product[stext.US_METADATA + slot] != jp_raw[stext.JP_METADATA + code]:
                raise RuntimeError("UAI_GLYPH_WIDTH 0x%04X->0x%04X" % (code, slot))
            checked += 1
    audits["glyphs"] = {"tokens_checked": checked, "new_glyph_records": 0,
                        "result": "PASS"}

    # -- siblings: no other words:content entry and no other fx record moved -
    pool_before, pool_after = us_words(base)["content"], words_check
    owned = {records[0]["us_index"]}
    moved = [i for i in range(pool_after.tsize) if i not in owned
             and product[pool_after.real_offset + i * 4:pool_after.real_offset + i * 4 + 4]
             != base[pool_before.real_offset + i * 4:pool_before.real_offset + i * 4 + 4]]
    if moved:
        raise RuntimeError(f"UAI_SIBLING_ROOT_MOVED {moved[:8]}")
    pointers = [u32(product, pool_after.real_offset + i * 4)
                for i in range(pool_after.tsize)]
    ours = {p for i, p in enumerate(pointers) if i in owned}
    surviving = {p for i, p in enumerate(pointers) if i not in owned}
    if ours & surviving:
        raise RuntimeError("UAI_ALIAS_PROPAGATION")
    if any(ROM + block_start <= p < ROM + block_end for p in surviving):
        raise RuntimeError("UAI_ALIAS_HAZARD")
    unchanged = 0
    for i in range(pool_after.tsize):
        if i in owned:
            continue
        if uam.tokens_of(pool_before[i]) != uam.tokens_of(pool_after[i]):
            raise RuntimeError(f"UAI_SIBLING_TEXT_CHANGED words:content/{i}")
        unchanged += 1
    leaf_before = fxt.product_fx_text(base)[FX_LEAF]
    for i in range(leaf_before.tsize):
        if i == records[1]["us_entry"]:
            continue
        if uam.tokens_of(leaf_before[i]) != uam.tokens_of(fx_check[i]):
            raise RuntimeError(f"UAI_SIBLING_TEXT_CHANGED fx_text/{FX_LEAF}/{i}")
        unchanged += 1
    audits["siblings"] = {"records_unchanged": unchanged, "result": "PASS"}

    # -- the whole tracked text corpus is otherwise identical to the baseline
    audits["corpus"] = corpus_identity(base, product)
    audits["identity_audit"] = {
        "items_checked": audit_summary["items_checked"],
        "records_checked": audit_summary["records_checked"],
        "us_added_item_identities": audit_summary["us_added_item_identities"],
        "verdicts": audit_summary["verdicts"],
        "semantic_review": audit_summary["semantic_review"],
        "records_production_must_not_source_from_jp":
            audit_summary["records_production_must_not_source_from_jp"],
        "records_fixed_here": len(records),
        "remaining": audit_summary["records_production_must_not_source_from_jp"] - len(records),
    }
    if audits["identity_audit"]["remaining"] != 0:
        raise RuntimeError("UAI_FAMILY_NOT_CLOSED")
    audits["patch"] = {"block": [f"0x{ROM + block_start:08X}", f"0x{ROM + block_end:08X}"],
                       "fx_leaf": fx_record, "layout": layout}
    return audits


def corpus_identity(base, product):
    """Every words / pages / fx_text record, both images, token by token."""
    changed, total = [], 0
    tmp_dir = RUN_BASE / "_corpus"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    a, b = tmp_dir / "a.gba", tmp_dir / "b.gba"
    a.write_bytes(base)
    b.write_bytes(product)
    try:
        ra, rb = pta.glyphs.us_rom(str(a)), pta.glyphs.us_rom(str(b))
        fa, fb = pta.glyphs.us_fx_text(str(a)), pta.glyphs.us_fx_text(str(b))
        for fam in sorted(ra.tabs["words"].keys()):
            ta, tb = ra.tabs["words"][fam], rb.tabs["words"][fam]
            for i in range(min(ta.tsize, tb.tsize)):
                total += 1
                if uam.tokens_of(ta[i]) != uam.tokens_of(tb[i]):
                    changed.append("words:%s/%d" % (fam, i))
        for leaf in range(fa.tsize):
            try:
                la, lb = fa[leaf], fb[leaf]
            except Exception:                                        # noqa: BLE001
                continue
            for i in range(min(getattr(la, "tsize", 0), getattr(lb, "tsize", 0))):
                total += 1
                if uam.tokens_of(la[i]) != uam.tokens_of(lb[i]):
                    changed.append("fx_text/%d/%d" % (leaf, i))
    finally:
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
    if sorted(changed) != ["fx_text/25/71", "words:content/570"]:
        raise RuntimeError("UAI_CORPUS_DRIFT %s" % changed[:8])
    return {"records_compared": total, "records_changed": sorted(changed),
            "result": "PASS"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260903_production")
    ap.add_argument("--print-sha", action="store_true")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return 0
    second = build()
    if sha(first[0]) != sha(second[0]):
        raise RuntimeError("UAI_BUILD_NONDETERMINISTIC")
    (product, base, meta, alloc, records, doc, rows, fx_record, layout, _kanji,
     audit_summary) = first
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    audits = validate(product, base, meta, alloc, records, rows, fx_record, layout,
                      doc, audit_summary)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "milestone": "US-added item identity -- Stuffed Bear (RC24)",
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "provenance": doc["provenance"],
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True},
        "records": [{k: v for k, v in r.items() if not k.startswith("_")}
                    for r in records],
        "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
