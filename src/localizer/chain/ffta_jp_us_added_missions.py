#! python3
"""US-added placeholder-transfer family: the twenty US-added missions and the
``Official`` unit kind, localized (RC17).

The defect (RC16, ``ffta_placeholder_transfer_audit.py``)
--------------------------------------------------------

FFTA's US build adds content into slots the Japanese build shipped as
developer placeholders.  ``ffta_jp_us_only_editorial_scope.py`` asks whether
the aligned JP record is *missing or empty*; where the JP record is a
**non-empty placeholder** the source-transfer layers copy the placeholder, the
scope tool records a successful transfer, and production ships it.  Measured
on the pristine images, 42 records:

* ``words:quest`` 377..396 -- JP ``クエスト377`` … ``クエスト396``; the US ships
  the twenty real mission names (``Reconcilliation`` … ``Decision Time``).
  The mission-table entries themselves are empty in JP and populated in US:
  these are US-added missions, not renamed ones.
* ``pages:quest/1`` 176..195 -- JP ``ダミー377`` … ``ダミー396``; the US ships
  the five-line briefings with a named requester.
* ``words:content/122`` -- JP ``予備2`` (a reserve slot); US ``Official``, the
  palace officials of the judge arc.
* ``fx_text/22/121`` -- empty in JP; the US description of that unit kind
  (``fx_text/22[i]`` describes ``words:content[i+1]``).

Policy (Main ``docs/decisions.md``, 2026-09-01): JP retail is the authority for
*player-visible wording*, not for developer placeholders.  A JP placeholder
opposite real US content is US-added content to be localized.

What this layer does
--------------------

The translations are NOT computed here.  They are read from the tracked
manifest ``data/us_added_missions_translations.json`` -- the editorial
decision of record -- and every claim it makes is re-proved on every build:

* the target is a placeholder transfer: the JP record at the aligned index
  matches the audit's placeholder pattern (or is empty, for the description),
  the US record is real content, and the base image still ships the placeholder;
* every kanji names a JP retail record that actually uses that glyph code;
* every line is measured with the JP font advance table against the widest
  JP original of its own family, and the record shape (lines, pages, payload
  bytes) against the family's JP maximum -- the mission panel draws the
  briefing through the same renderer that draws JP retail's, so the JP shape
  is the invariant;
* every reference control of the US original survives, English typesetting
  does not, the close sequence is identical, and the encoded bytes round-trip
  through the established parser.

Mechanism, all data: ``words:quest`` / ``words:content`` are direct-addressed
(one 4-byte root field each, repointed at a new payload in the free tail);
``pages:quest/1`` and ``fx_text/22`` are ``text_page`` leaves, re-parsed out of
the in-process product, re-serialized with only the targets replaced, placed
in the free tail after RC12's mission-panel leaf and repointed.  Every glyph is
a native JP record the production font already carries: **zero new glyph
records, zero font or metadata writes, zero executable bytes.**

Layer position: a new terminal layer above RC12's
``ffta_jp_battle_system_geometry`` (the twenty-nine layers below assert their
predecessors' SHA and are untouched).  The JP->US glyph allocation is not
carried past layer 19, so it is re-derived once per process from
``ffta_jp_us_only_s_text_final.build()`` and gated on that layer's own SHA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import ffta_jp_battle_system_geometry as prev
import ffta_jp_coverage_audit as coverage
import ffta_jp_name_entry as name_entry
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_only_fx_text as fxt
import ffta_jp_us_only_s_text_final as glyph_source
import ffta_jp_us_only_system_battle as sysbat
import ffta_placeholder_transfer_audit as pta
from ffta_sect import (c_ffta_sect_rom, c_ffta_sect_text_buf,
                       c_ffta_sect_text_page, _pages_sect_info, _trim_raw_len,
                       _words_sect_info)
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_added_missions_translations.json"
RUN_BASE = HERE / "build/us_added_missions"
OUTROM = ROOT / "rom/build/ffta_us_jp_us_added_missions.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_us_added_missions_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_battle_system_geometry (RC12).
BASELINE = "9098F025FDDD00119E0FD833199F5E9BBD1F66DCBEAD865D60749C3C4E652532"
# Pinned after the first deterministic build of this layer (RC17).
EXPECTED_PRODUCTION = "9F5EBA6C4408CAA419DE1DC96AE20B6B8A43785230C1B14C21BA14D9C5D4195E"

TOTAL = 42
FAMILIES = {"words:quest": 20, "pages:quest/1": 20, "words:content": 1, "fx_text/22": 1}
EXPECTED_INDEXES = {"words:quest": list(range(377, 397)),
                    "pages:quest/1": list(range(176, 196)),
                    "words:content": [122], "fx_text/22": [121]}
# Root pointer field and entry count of the two US words tables this layer
# touches, as ffta_sect.load_rom_us / ffta_qa_glyphs.US_WORDS_ROOTS declare them.
US_WORDS_ROOTS = {"quest": (0x191F0, 0x20F), "content": (0x18DA4, 0x2F2)}
PAGES_QUEST1_FIELD = 0x13CC0          # US pages:quest/1 root pointer field
PAGES_QUEST1_TSIZE = 311
FX_ROOT_FIELD = fxt.FX_ROOT_FIELD     # 0x0036D678
FX_LEAF = 22
FX_LEAF_TSIZE = 123
JP_QUEST_BASE = 200                   # JP pages:quest/0 ordinal = 200 + US pages:quest/1 index

CTR_TERMINATOR = 0x42
CTR_CLOSE = 0x40
CTR_WORD_SPACE = 0x52
LINE_BREAK, PAGE_BREAK = 0x4D, 0x4F
REQUESTER_INDENT = 0x1D10
COMPRESSION_BIT = 0x0002
YA_ENCODING_BIT = 0x0001               # text_line flag bit 0: the YA text_buf encoding
ENCODING_BITS = COMPRESSION_BIT | YA_ENCODING_BIT
LAYOUT_CONTROLS = {0x40, 0x42, 0x4D, 0x4F, 0x52, 0x56, 0x1B01, 0x1D02, 0x1D03,
                   0x1D04, 0x1D0A, 0x1D10}
FORBIDDEN_CONTROLS = {0x1B01, 0x1D02, 0x1D03, 0x1D04}
SUBST_TOKEN_MASK = 0xFF00             # {04 xx}: the template-slot-eating token of RC12
SUBST_TOKEN = 0x0400
FREE_END = 0x00FE0000                 # the name-entry artwork starts here (RC3)
BLOCK_ALIGN = 0x100

MARKUP = re.compile(r"\{([0-9A-F]{1,4})\}")

_alloc_cache = {}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def tokens_of(line):
    text = getattr(line, "text", line)
    return list(getattr(text, "tokens", []) or [])


def glyph_allocation():
    """The chain's JP->US font-slot allocation, re-derived once per process.

    Layers 20..29 drop it from their return tuples, so the last text layer is
    built again and gated on the SHA the name-entry layer asserts for it.
    """
    if "alloc" not in _alloc_cache:
        result = glyph_source.build()
        if sha(result[0]) != name_entry.BASELINE:
            raise RuntimeError(f"UAM_GLYPH_SOURCE_DRIFT {sha(result[0])}")
        _alloc_cache["alloc"] = dict(result[3])
        _alloc_cache["meta"] = result[2]
    return _alloc_cache["alloc"], _alloc_cache["meta"]


# --------------------------------------------------------------- manifest ---

def jp_entry(jp, path):
    """A JP logical path (words:/pages:/fx_text/s_text, any depth) -> tokens."""
    if path.startswith("s_text/"):
        node = jp.tabs["s_text"]
        for part in path.split("/")[1:]:
            node = node[int(part)]
        return tokens_of(node)
    return sysbat.jp_entry(jp, path)


def kanji_table(doc, jp):
    """Kanji -> JP glyph code, each revalidated against its JP attestation."""
    table = {}
    for char, row in doc["kanji_codes"].items():
        code = int(row["jp_glyph_code"], 16)
        if code < 0x122:
            raise RuntimeError(f"UAM_KANJI_CODE_NOT_IN_KANJI_SPAN {char}")
        path = row.get("attested_at")
        if not path:
            raise RuntimeError(f"UAM_KANJI_ATTESTATION_MISSING {char}")
        if not any(kind == "CHR_FULL" and value == code
                   for kind, value in jp_entry(jp, path)):
            raise RuntimeError(f"UAM_KANJI_ATTESTATION_FAILED {char} 0x{code:04X} {path}")
        if char in table:
            raise RuntimeError(f"UAM_KANJI_DUPLICATE {char}")
        table[char] = code
    return table


def us_pages(raw):
    data = bytes(raw)
    return c_ffta_sect_rom(data, 0).setup(
        _pages_sect_info({"quest/1": PAGES_QUEST1_FIELD}),
        _trim_raw_len(data, 0xF00000)).tabs["pages"]["quest/1"]


def us_words(raw):
    data = bytes(raw)
    return c_ffta_sect_rom(data, 0).setup(
        _words_sect_info({n: (r, s) for n, (r, s) in US_WORDS_ROOTS.items()}),
        _trim_raw_len(data, 0xF00000)).tabs["words"]


def original_tokens(us, us_raw, row):
    fam = row["family"]
    if fam == "words:quest":
        return tokens_of(us.tabs["words"]["quest"][row["us_index"]])
    if fam == "words:content":
        return tokens_of(us.tabs["words"]["content"][row["us_index"]])
    if fam == "pages:quest/1":
        return tokens_of(us_pages(us_raw)[row["us_index"]])
    return tokens_of(us.tabs["fx_text"][FX_LEAF][row["us_entry"]])


def jp_counterpart(jp, row):
    fam = row["family"]
    if fam == "words:quest":
        return tokens_of(jp.tabs["words"]["quest"][row["us_index"]])
    if fam == "words:content":
        return tokens_of(jp.tabs["words"]["content"][row["us_index"]])
    if fam == "pages:quest/1":
        return tokens_of(jp.tabs["pages"]["quest/0"][JP_QUEST_BASE + row["us_index"]])
    return tokens_of(jp.tabs["fx_text"][FX_LEAF][row["us_entry"]])


def check_placeholder_transfer(jp, jp_names, row, us_text):
    """Prove the record is a US-added transfer over a JP developer placeholder."""
    toks = jp_counterpart(jp, row)
    jp_text = pta._clean("".join(jp_names.get(v, "<%04X>" % v) if k.startswith("CHR")
                                 else "{%X}" % v for k, v in toks))
    if pta.US_PLACEHOLDER.match(pta._clean(us_text)):
        raise RuntimeError(f"UAM_US_SIDE_IS_PLACEHOLDER {row['us_logical_path']}")
    if not any(k.startswith("CHR") for k, _ in toks):
        return "JP_RECORD_EMPTY", jp_text
    if pta.JP_PLACEHOLDER.match(jp_text):
        return "JP_DEVELOPER_PLACEHOLDER", jp_text
    raise RuntimeError(f"UAM_TARGET_HAS_JP_ORIGINAL {row['us_logical_path']} {jp_text!r}")


def load_manifest(jp, us, us_raw, decode, jp_names):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = doc["entries"]
    if len(rows) != TOTAL or doc["count"] != TOTAL or doc["translated_count"] != TOTAL:
        raise RuntimeError(f"UAM_MANIFEST_COUNT {len(rows)}/{doc['count']}")
    if doc["baseline_production_sha256"] != BASELINE:
        raise RuntimeError("UAM_MANIFEST_BASELINE_DRIFT")
    if doc.get("families") != FAMILIES:
        raise RuntimeError("UAM_MANIFEST_FAMILIES")
    got = {}
    for row in rows:
        fam = row["family"]
        if fam not in FAMILIES:
            raise RuntimeError(f"UAM_MANIFEST_FAMILY {fam}")
        index = row["us_entry"] if fam == "fx_text/22" else row["us_index"]
        got.setdefault(fam, []).append(index)
        want_path = (f"fx_text/{FX_LEAF}/{index}" if fam == "fx_text/22"
                     else f"{fam}/{index}")
        if row["us_logical_path"] != want_path:
            raise RuntimeError(f"UAM_MANIFEST_PATH {row['us_logical_path']}")
        if row["status"] != "TRANSLATED_REVIEWED" or row.get("source") != "EDITORIAL":
            raise RuntimeError(f"UAM_MANIFEST_STATUS {row['us_logical_path']}")
        if not row["japanese"]:
            raise RuntimeError(f"UAM_MANIFEST_EMPTY {row['us_logical_path']}")
        original = original_tokens(us, us_raw, row)
        us_text = fxt.visible(original, decode)
        if not _us_source.matches(row, us_text):
            raise RuntimeError(f"UAM_MANIFEST_ENGLISH_DRIFT {row['us_logical_path']}")
        row["_original"] = original
        row["_us_only_reason"], row["_jp_text"] = check_placeholder_transfer(
            jp, jp_names, row, us_text)
        if row.get("jp_placeholder") != row["_jp_text"]:
            raise RuntimeError(f"UAM_MANIFEST_JP_PLACEHOLDER_DRIFT {row['us_logical_path']}")
    if {k: sorted(v) for k, v in got.items()} != EXPECTED_INDEXES:
        raise RuntimeError(f"UAM_MANIFEST_INDEX_SET {sorted(got)}")
    return doc, rows


# --------------------------------------------------------------- encoding ---

def parse_markup(japanese, reverse, kanji):
    out, i = [], 0
    while i < len(japanese):
        match = MARKUP.match(japanese, i)
        if match:
            out.append(("CTR_FUNC", int(match.group(1), 16)))
            i = match.end()
            continue
        char = japanese[i]
        if char in kanji:
            out.append(("CHR_FULL", kanji[char]))
        elif char in reverse:
            out.append(("CHR_FULL", reverse[char]))
        else:
            raise RuntimeError(f"UAM_UNENCODABLE {char!r} in {japanese!r}")
        i += 1
    return out


def family_bounds(jp, jp_raw):
    """Per-family width / shape bounds, from the JP originals of the same family."""
    out = {}

    def scan(records):
        width = lines = raw = 0
        for rec in records:
            if rec is None or isinstance(rec, list):
                continue
            toks = tokens_of(rec)
            if not any(k.startswith("CHR") for k, _ in toks):
                continue
            width = max([width] + fxt.line_widths(toks, jp_raw))
            lines = max([lines] + fxt.lines_per_page(toks))
            raw = max(raw, getattr(rec, "raw_len", 0))
        return {"max_line_px": width, "max_lines_per_page": lines, "max_raw_len": raw}

    def safe(table):
        for i in range(table.tsize):
            try:
                yield table[i]
            except Exception:                                        # noqa: BLE001
                continue

    out["words:quest"] = scan(safe(jp.tabs["words"]["quest"]))
    out["words:content"] = scan(safe(jp.tabs["words"]["content"]))
    out["pages:quest/1"] = scan(safe(jp.tabs["pages"]["quest/0"]))
    out["fx_text/22"] = scan(safe(jp.tabs["fx_text"][FX_LEAF]))
    for fam, b in out.items():
        if not b["max_line_px"] or not b["max_lines_per_page"]:
            raise RuntimeError(f"UAM_BOUND_EMPTY {fam}")
    return out


def closing(tokens):
    out = []
    for kind, value in reversed(tokens):
        if kind == "CTR_FUNC" and value in (0x40, 0x42, 0x56):
            out.append(value)
        else:
            break
    return list(reversed(out))


def check_controls(path, family, original, translated):
    """Every meaning-bearing US control survives; English typesetting does not."""
    def significant(tokens):
        return {value for kind, value in tokens
                if kind == "CTR_FUNC" and value not in LAYOUT_CONTROLS}
    us_refs, jp_refs = significant(original), significant(translated)
    if us_refs != jp_refs:
        raise RuntimeError(f"UAM_CONTROL_SET {path} us={sorted(map(hex, us_refs))} "
                           f"jp={sorted(map(hex, jp_refs))}")
    if any(k == "CTR_FUNC" and v in FORBIDDEN_CONTROLS for k, v in translated):
        raise RuntimeError(f"UAM_ENGLISH_TYPESETTING_KEPT {path}")
    if any(k == "CTR_FUNC" and v == CTR_WORD_SPACE for k, v in translated):
        raise RuntimeError(f"UAM_WORD_SPACE_KEPT {path}")
    if any(k == "CTR_FUNC" and (v & SUBST_TOKEN_MASK) == SUBST_TOKEN and v != 0x400
           for k, v in translated):
        # {04 xx} eats template slots (RC12); a briefing is a value, never a template
        raise RuntimeError(f"UAM_SUBSTITUTION_TOKEN_IN_VALUE {path}")
    if any(k == "CTR_EOS" for k, _ in translated) or any(k == "CTR_EOS" for k, _ in original):
        raise RuntimeError(f"UAM_EOS_SEPARATOR {path}")
    if closing(original) != closing(translated):
        raise RuntimeError(f"UAM_CLOSE_SEQUENCE {path}")
    want_terminator = family in ("pages:quest/1", "fx_text/22")
    has_terminator = bool(translated) and translated[-1] == ("CTR_FUNC", CTR_TERMINATOR)
    if has_terminator != want_terminator:
        raise RuntimeError(f"UAM_TERMINATOR {path}")
    if family in ("words:quest", "words:content"):
        if any(k == "CTR_FUNC" for k, _ in translated):
            raise RuntimeError(f"UAM_WORDS_CONTROL {path}")
    if family == "pages:quest/1":
        # the requester line JP retail's own briefings carry
        if ("CTR_FUNC", REQUESTER_INDENT) not in translated:
            raise RuntimeError(f"UAM_REQUESTER_INDENT_MISSING {path}")
        # every line non-empty, no double break, no trailing empty line
        body = translated[:-1]
        prev_break = True
        for kind, value in body:
            is_break = kind == "CTR_FUNC" and value == LINE_BREAK
            if is_break and prev_break:
                raise RuntimeError(f"UAM_EMPTY_LINE {path}")
            prev_break = is_break
        if prev_break:
            raise RuntimeError(f"UAM_TRAILING_BREAK {path}")
    return {"references_preserved": sorted(f"0x{v:04X}" for v in us_refs),
            "close_sequence": [f"0x{v:02X}" for v in closing(translated)]}


def to_slots(tokens, alloc):
    """JP glyph codes -> production font slots, through the chain's allocation.

    Every code goes through the allocation, the low span included: the chain
    installs JP retail's kana and punctuation glyphs in allocated slots as well
    (0x00A5 ー -> 0x01BF, 0x00EB ! -> 0x01D2, ...), because the US font's low
    slots draw different pixels for them.  A record encoded with a raw low code
    would draw the US glyph -- the record oracle's self-test is what catches
    that, by bitmap.
    """
    out = []
    for kind, value in tokens:
        if kind == "CHR_FULL":
            if value not in alloc:
                raise RuntimeError(f"UAM_GLYPH_NOT_ALLOCATED 0x{value:04X}")
            out.append(("CHR_FULL", alloc[value]))
        else:
            out.append((kind, value))
    return out


def encode(row, reverse, kanji, alloc, jp_raw, bounds):
    path, family = row["us_logical_path"], row["family"]
    jp_tokens = parse_markup(row["japanese"], reverse, kanji)
    control = check_controls(path, family, row["_original"], jp_tokens)
    bound = bounds[family]
    widths = fxt.line_widths(jp_tokens, jp_raw)
    if max(widths) > bound["max_line_px"]:
        raise RuntimeError(f"UAM_WIDTH_OVER_FAMILY_BOUND {path} "
                           f"{max(widths)}>{bound['max_line_px']}")
    pages = fxt.lines_per_page(jp_tokens)
    if max(pages) > bound["max_lines_per_page"]:
        raise RuntimeError(f"UAM_LINES_OVER_FAMILY_BOUND {path} "
                           f"{max(pages)}>{bound['max_lines_per_page']}")
    if family == "pages:quest/1" and max(pages) > 6:
        raise RuntimeError(f"UAM_BRIEFING_LINES {path} {max(pages)}>6")
    expected = to_slots(jp_tokens, alloc)
    data = stext.encode_standard(expected)
    if family in ("pages:quest/1", "fx_text/22") and len(data) + 2 > bound["max_raw_len"]:
        raise RuntimeError(f"UAM_PAYLOAD_OVER_JP_MAX {path} {len(data) + 2}>{bound['max_raw_len']}")
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"UAM_SERIALIZER_ROUNDTRIP_FAILED {path}")
    if data[-1] != 0:
        raise RuntimeError(f"UAM_EOS_MISSING {path}")
    return jp_tokens, expected, data, widths, pages, control


# ------------------------------------------------------------------ build ---

def leaf_standard_flags(leaf):
    """The text_line flags every standard record of this leaf already carries.

    Bit 1 is the compression flag and bit 0 selects the YA text_buf encoding
    (ffta_sect.c_ffta_sect_text_line.parse); with both cleared, the records of
    a leaf share one flag word -- 0x1868 for the 376 JP-transferred briefings
    of pages:quest/1, 0x1828 for the 119 descriptions of fx_text/22.  A
    replacement takes exactly that word, so it has the shape of its siblings.
    """
    from collections import Counter
    counts = Counter()
    for i in range(leaf.tsize):
        try:
            counts[leaf[i].U16(0) & ~ENCODING_BITS] += 1
        except Exception:                                            # noqa: BLE001
            continue
    (flags, n), = counts.most_common(1)
    if n < leaf.tsize // 2:
        raise RuntimeError(f"UAM_LEAF_FLAGS_NOT_UNIFORM {dict(counts)}")
    return flags


def record_flags(pristine_record, base_record, standard):
    """Flags for one replacement: the leaf's standard word, which the pristine
    US record or the base record (the JP transfer) must already agree with
    outside the two encoding bits.  The JP *empty* records carry the YA bit
    (0x1811), which a standard payload must not inherit."""
    pristine = pristine_record.U16(0) & ~ENCODING_BITS
    current = base_record.U16(0) & ~ENCODING_BITS
    if standard not in (pristine, current):
        raise RuntimeError(f"UAM_RECORD_FLAGS_DIVERGED 0x{pristine:04X} 0x{current:04X} "
                           f"0x{standard:04X}")
    return standard


def free_block_start(base, prev_block_end):
    """The first byte after everything the chain has placed below FREE_END.

    RC12's mission-panel leaf sits above the text layers' tail cursor, so the
    tail cursor alone is not the free start; the image itself is asked.
    """
    used = len(bytes(base[prev_block_end:FREE_END]).rstrip(b"\xff"))
    return stext.align(prev_block_end + used, BLOCK_ALIGN)


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"UAM_BASELINE_MISMATCH {sha(base)}")
    prev_block_end = previous[4]
    alloc, meta = glyph_allocation()
    jp, us = meta["jp"], meta["us"]
    jp_raw, us_raw = JP.read_bytes(), US.read_bytes()

    decode, reverse = sysbat.charset_tables()
    jp_names = pta.glyphs.charset()
    doc, rows = load_manifest(jp, us, us_raw, decode, jp_names)
    kanji = kanji_table(doc, jp)
    bounds = family_bounds(jp, jp_raw)

    raw = bytearray(base)
    snapshot = bytes(raw)
    block_start = free_block_start(base, prev_block_end)
    cursor = block_start
    records = []

    # -- words:*  direct-entry repoint -------------------------------------
    words = us.tabs["words"]
    for name in ("quest", "content"):
        pool = words[name]
        if pool.tsize != US_WORDS_ROOTS[name][1]:
            raise RuntimeError(f"UAM_WORDS_ROOT_DECL_DRIFT words:{name}")
        for row in [r for r in rows if r["family"] == f"words:{name}"]:
            index = row["us_index"]
            jp_tokens, expected, data, widths, pages, control = encode(
                row, reverse, kanji, alloc, jp_raw, bounds)
            field = pool.real_offset + index * 4
            old = int.from_bytes(raw[field:field + 4], "little")
            raw[cursor:cursor + len(data)] = data
            raw[field:field + 4] = (ROM + cursor).to_bytes(4, "little")
            records.append({
                "us_logical_path": row["us_logical_path"], "family": row["family"],
                "us_index": index, "original_english_sha256": _us_source.digest(row),
                "jp_placeholder": row["_jp_text"], "japanese": row["japanese"],
                "status": row["status"], "us_only_reason": row["_us_only_reason"],
                "route": "WORDS_DIRECT_REPOINT",
                "rendered_width_px": max(widths), "line_widths_px": widths,
                "width_bound_px": bounds[row["family"]]["max_line_px"],
                "root_pointer_field_us_rom": f"0x{field:08X}",
                "original_cpu_pointer": f"0x{old:08X}",
                "new_cpu_pointer": f"0x{ROM + cursor:08X}",
                "payload_offset_us_rom": f"0x{cursor:08X}",
                "payload_length": len(data), "eos": True, "roundtrip": "PASS",
                "controls": control, "_expected": expected, "_data": data})
            cursor = stext.align(cursor + len(data), 4)
    words_block_end = cursor

    # -- pages:quest/1  whole-leaf recomposition ----------------------------
    leaf = us_pages(snapshot)
    pristine_leaf = us_pages(us_raw)
    if leaf.tsize != PAGES_QUEST1_TSIZE or pristine_leaf.tsize != PAGES_QUEST1_TSIZE:
        raise RuntimeError(f"UAM_PAGES_TSIZE {leaf.tsize}")
    replacements, leaf_expected = {}, {}
    pages_flags = leaf_standard_flags(leaf)
    for row in [r for r in rows if r["family"] == "pages:quest/1"]:
        index = row["us_index"]
        jp_tokens, expected, data, widths, pages, control = encode(
            row, reverse, kanji, alloc, jp_raw, bounds)
        # the record flags are the US consumer's own (the pristine US record),
        # with the encoding bits cleared: a standard, uncompressed text_buf.
        # The base record inherited the JP placeholder's flags, and the JP
        # *empty* records use the YA encoding (bit 0), which a standard
        # payload must not carry.
        flags = record_flags(pristine_leaf[index], leaf[index], pages_flags)
        replacements[index] = flags.to_bytes(2, "little") + data
        leaf_expected[index] = expected
        records.append({
            "us_logical_path": row["us_logical_path"], "family": row["family"],
            "us_index": index, "mission": row.get("mission"),
            "original_english_sha256": _us_source.digest(row),
            "jp_placeholder": row["_jp_text"], "japanese": row["japanese"],
            "status": row["status"], "us_only_reason": row["_us_only_reason"],
            "route": "PAGES_LEAF_RECOMPOSE",
            "rendered_width_px": max(widths), "line_widths_px": widths,
            "lines": max(pages), "width_bound_px": bounds[row["family"]]["max_line_px"],
            "record_flags": f"0x{flags:04X}", "payload_length": len(data),
            "payload_bound": bounds[row["family"]]["max_raw_len"] - 2,
            "eos": True, "roundtrip": "PASS", "controls": control,
            "_expected": expected, "_data": data})
    if len(replacements) != FAMILIES["pages:quest/1"]:
        raise RuntimeError("UAM_PAGES_REPLACEMENT_COUNT")
    blob = stext.serialize_leaf(snapshot, leaf, replacements)
    pages_offset = cursor
    old_pages = int.from_bytes(raw[PAGES_QUEST1_FIELD:PAGES_QUEST1_FIELD + 4], "little")
    raw[cursor:cursor + len(blob)] = blob
    raw[PAGES_QUEST1_FIELD:PAGES_QUEST1_FIELD + 4] = (ROM + cursor).to_bytes(4, "little")
    cursor = stext.align(cursor + len(blob), 4)
    pages_record = {
        "family": "pages:quest/1", "root_field_us_rom": f"0x{PAGES_QUEST1_FIELD:08X}",
        "old_cpu_pointer": f"0x{old_pages:08X}", "new_cpu_pointer": f"0x{ROM + pages_offset:08X}",
        "previous_leaf_us_rom": f"0x{leaf.real_offset:08X}", "previous_size": leaf.sect_top,
        "new_leaf_us_rom": f"0x{pages_offset:08X}", "new_size": len(blob),
        "entries": leaf.tsize, "records_replaced": len(replacements),
        "records_preserved": leaf.tsize - len(replacements)}

    # -- fx_text/22  whole-leaf recomposition -------------------------------
    table = fxt.product_fx_text(snapshot)
    page = table[FX_LEAF]
    if page.tsize != FX_LEAF_TSIZE:
        raise RuntimeError(f"UAM_FX_LEAF_TSIZE {page.tsize}")
    fx_replacements, fx_expected = {}, {}
    pristine_page = us.tabs["fx_text"][FX_LEAF]
    fx_flags = leaf_standard_flags(page)
    for row in [r for r in rows if r["family"] == "fx_text/22"]:
        index = row["us_entry"]
        jp_tokens, expected, data, widths, pages, control = encode(
            row, reverse, kanji, alloc, jp_raw, bounds)
        flags = record_flags(pristine_page[index], page[index], fx_flags)
        fx_replacements[index] = flags.to_bytes(2, "little") + data
        fx_expected[index] = expected
        records.append({
            "us_logical_path": row["us_logical_path"], "family": row["family"],
            "us_leaf": FX_LEAF, "us_entry": index,
            "original_english_sha256": _us_source.digest(row),
            "jp_placeholder": row["_jp_text"], "japanese": row["japanese"],
            "status": row["status"], "us_only_reason": row["_us_only_reason"],
            "route": "FX_LEAF_RECOMPOSE",
            "rendered_width_px": max(widths), "line_widths_px": widths,
            "pages": pages, "width_bound_px": bounds[row["family"]]["max_line_px"],
            "record_flags": f"0x{flags:04X}", "payload_length": len(data),
            "eos": True, "roundtrip": "PASS", "controls": control,
            "_expected": expected, "_data": data})
    blob = stext.serialize_leaf(snapshot, page, fx_replacements)
    fx_offset = cursor
    fx_field = FX_ROOT_FIELD + FX_LEAF * 4
    old_fx = int.from_bytes(raw[fx_field:fx_field + 4], "little")
    raw[cursor:cursor + len(blob)] = blob
    raw[fx_field:fx_field + 4] = (ROM + cursor).to_bytes(4, "little")
    cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor
    fx_record = {
        "family": "fx_text/22", "root_field_us_rom": f"0x{fx_field:08X}",
        "old_cpu_pointer": f"0x{old_fx:08X}", "new_cpu_pointer": f"0x{ROM + fx_offset:08X}",
        "previous_leaf_us_rom": f"0x{page.real_offset:08X}", "previous_size": page.sect_top,
        "new_leaf_us_rom": f"0x{fx_offset:08X}", "new_size": len(blob),
        "entries": page.tsize, "records_replaced": len(fx_replacements),
        "records_preserved": page.tsize - len(fx_replacements)}

    if len(raw) != len(base):
        raise RuntimeError("UAM_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start and block_end <= FREE_END):
        raise RuntimeError("UAM_BLOCK_OUTSIDE_FREE_TAIL")
    if set(base[block_start:block_end]) != {0xFF} or set(us_raw[block_start:block_end]) != {0xFF}:
        raise RuntimeError("UAM_BLOCK_NOT_FREE")
    # No pointer the chain has written may already point into the block.  A
    # word that reads as an address in this range but is byte-identical to
    # pristine US is data that happens to look like one (four such words exist
    # for a 60 KB window; the block was 0xFF in pristine US, so nothing live
    # can point there), and is counted, not refused.
    n_words = len(base) // 4
    coincidences = 0
    for off, (v, w) in enumerate(zip(struct.unpack_from("<%dI" % n_words, base, 0),
                                     struct.unpack_from("<%dI" % n_words, us_raw, 0))):
        if ROM + block_start <= v < ROM + block_end:
            if v != w:
                raise RuntimeError("UAM_BLOCK_REFERENCED 0x%08X" % (ROM + off * 4))
            coincidences += 1
    layout = {"block_start": block_start, "words_block_end": words_block_end,
              "pages_leaf": pages_offset, "fx_leaf": fx_offset, "block_end": block_end,
              "pristine_words_resembling_block_addresses": coincidences}
    return (bytes(raw), base, meta, alloc, records, doc, rows, pages_record, fx_record,
            leaf_expected, fx_expected, layout, kanji, bounds)


# --------------------------------------------------------------- validate ---

def validate(product, base, meta, alloc, records, rows, pages_record, fx_record,
             leaf_expected, fx_expected, layout, decode, doc, bounds):
    us = meta["us"]
    audits = {}
    block_start, block_end = layout["block_start"], layout["block_end"]

    # -- builder-side payload audit ----------------------------------------
    for rec in [r for r in records if r["route"] == "WORDS_DIRECT_REPOINT"]:
        field = int(rec["root_pointer_field_us_rom"], 16)
        start = int(rec["payload_offset_us_rom"], 16)
        if int.from_bytes(product[field:field + 4], "little") != ROM + start:
            raise RuntimeError(f"UAM_ROOT_POINTER_AUDIT_FAILED {rec['us_logical_path']}")
        if product[start:start + rec["payload_length"]] != rec["_data"]:
            raise RuntimeError(f"UAM_PAYLOAD_AUDIT_FAILED {rec['us_logical_path']}")
        if not block_start <= start < layout["words_block_end"]:
            raise RuntimeError(f"UAM_PAYLOAD_OUTSIDE_BLOCK {rec['us_logical_path']}")

    # -- independent ROM readback ------------------------------------------
    if sha(OUTROM) != sha(product):
        raise RuntimeError("UAM_READBACK_ROM_MISMATCH")
    inverse = {slot: code for code, slot in alloc.items()}
    decode = dict(decode)
    decode.update({int(row["jp_glyph_code"], 16): char
                   for char, row in doc["kanji_codes"].items()})

    def char(value):
        if value in inverse:
            return decode.get(inverse[value], f"<jp:{inverse[value]:04X}>")
        return f"<us:{value:04X}>"

    def render(tokens):
        return "".join(char(v) if k.startswith("CHR") else "{%X}" % v for k, v in tokens)

    disk = Path(OUTROM).read_bytes()
    words_check = us_words(disk)
    pages_check = us_pages(disk)
    fx_check = fxt.product_fx_text(disk)
    readback = []
    by_path = {r["us_logical_path"]: r for r in records}
    if len(by_path) != len(rows):
        raise RuntimeError("UAM_RECORD_SET_SIZE")
    for row in rows:
        rec = by_path[row["us_logical_path"]]
        fam = row["family"]
        if fam == "words:quest":
            line = words_check["quest"][row["us_index"]]
        elif fam == "words:content":
            line = words_check["content"][row["us_index"]]
        elif fam == "pages:quest/1":
            line = pages_check[row["us_index"]]
        else:
            line = fx_check[FX_LEAF][row["us_entry"]]
        tokens = tokens_of(line)
        got = render(tokens)
        if got != row["japanese"]:
            raise RuntimeError(f"UAM_READBACK_TEXT_MISMATCH {row['us_logical_path']} {got!r}")
        if tokens != rec["_expected"]:
            raise RuntimeError(f"UAM_READBACK_TOKEN_MISMATCH {row['us_logical_path']}")
        if pta.JP_PLACEHOLDER.match(pta._clean(got)):
            raise RuntimeError(f"UAM_STILL_PLACEHOLDER {row['us_logical_path']}")
        readback.append({"us_logical_path": row["us_logical_path"], "decoded": got,
                         "result": "PASS"})
    pages_ptr = int.from_bytes(disk[PAGES_QUEST1_FIELD:PAGES_QUEST1_FIELD + 4], "little")
    if pages_ptr != int(pages_record["new_cpu_pointer"], 16):
        raise RuntimeError("UAM_READBACK_PAGES_ROOT")
    fx_field = FX_ROOT_FIELD + FX_LEAF * 4
    if int.from_bytes(disk[fx_field:fx_field + 4], "little") != int(fx_record["new_cpu_pointer"], 16):
        raise RuntimeError("UAM_READBACK_FX_ROOT")
    audits["readback"] = {"entries": len(readback), "failures": 0,
                          "source": "independent parse of the written ROM file, following "
                                    "its own words / pages / fx_text root pointers",
                          "result": "PASS", "rows": readback}

    # -- sibling / alias: words --------------------------------------------
    sibling = {}
    for name in ("quest", "content"):
        pool = us.tabs["words"][name]
        owned = {r["us_index"] for r in records if r["family"] == f"words:{name}"}
        moved = [i for i in range(pool.tsize) if i not in owned
                 and product[pool.real_offset + i * 4:pool.real_offset + i * 4 + 4]
                 != base[pool.real_offset + i * 4:pool.real_offset + i * 4 + 4]]
        if moved:
            raise RuntimeError(f"UAM_SIBLING_ROOT_MOVED words:{name} {moved[:8]}")
        pointers = [int.from_bytes(product[pool.real_offset + i * 4:pool.real_offset + i * 4 + 4],
                                   "little") for i in range(pool.tsize)]
        ours = {p for i, p in enumerate(pointers) if i in owned}
        if len(ours) != len(owned):
            raise RuntimeError(f"UAM_POINTER_COLLISION words:{name}")
        surviving = {p for i, p in enumerate(pointers) if i not in owned}
        if ours & surviving:
            raise RuntimeError(f"UAM_ALIAS_PROPAGATION words:{name}")
        if any(ROM + block_start <= p < ROM + block_end for p in surviving):
            raise RuntimeError(f"UAM_ALIAS_HAZARD words:{name}")
        # the neighbours of the family keep their tokens
        before, after = us_words(base)[name], words_check[name]
        for i in range(pool.tsize):
            if i in owned:
                continue
            try:
                if tokens_of(before[i]) != tokens_of(after[i]):
                    raise RuntimeError(f"UAM_SIBLING_TOKEN_CHANGED words:{name}/{i}")
            except RuntimeError:
                raise
            except Exception:                                        # noqa: BLE001
                continue
        sibling[f"words:{name}"] = {"family_entries": pool.tsize, "owned": len(owned),
                                    "sibling_root_fields_changed": 0,
                                    "pointer_collisions": 0, "alias_propagation": 0}
    for name, pool in us.tabs["words"].items():
        if name in ("quest", "content"):
            continue
        span = pool.real_offset, pool.real_offset + pool.tsize * 4
        if product[span[0]:span[1]] != base[span[0]:span[1]]:
            raise RuntimeError(f"UAM_WORDS_FAMILY_DISTURBED words:{name}")
    sibling["other_words_root_tables_unchanged"] = len(us.tabs["words"]) - 2

    # -- sibling: pages:quest/1 and fx_text/22 leaves ----------------------
    def leaf_audit(new, old, expected, label):
        if new.tsize != old.tsize:
            raise RuntimeError(f"UAM_LEAF_TSIZE_CHANGED {label}")
        changed = [i for i in range(new.tsize) if fxt.payload(new, i) != fxt.payload(old, i)]
        if sorted(changed) != sorted(expected):
            raise RuntimeError(f"UAM_UNEXPECTED_RECORD_CHANGE {label} "
                               f"{sorted(set(changed) ^ set(expected))[:8]}")
        for i in range(new.tsize):
            if i in expected:
                if tokens_of(new[i]) != expected[i]:
                    raise RuntimeError(f"UAM_LEAF_TOKEN_MISMATCH {label}/{i}")
            elif tokens_of(new[i]) != tokens_of(old[i]):
                raise RuntimeError(f"UAM_SIBLING_TOKEN_CHANGED {label}/{i}")
        return {"entries": new.tsize, "records_replaced": len(expected),
                "records_byte_preserved": new.tsize - len(expected)}
    sibling["pages:quest/1"] = leaf_audit(us_pages(product), us_pages(base),
                                          leaf_expected, "pages:quest/1")
    fx_after, fx_before = fxt.product_fx_text(product), fxt.product_fx_text(base)
    sibling["fx_text/22"] = leaf_audit(fx_after[FX_LEAF], fx_before[FX_LEAF],
                                       fx_expected, "fx_text/22")
    moved = [i for i in range(fxt.FX_TSIZE) if i != FX_LEAF
             and product[FX_ROOT_FIELD + i * 4:FX_ROOT_FIELD + i * 4 + 4]
             != base[FX_ROOT_FIELD + i * 4:FX_ROOT_FIELD + i * 4 + 4]]
    if moved:
        raise RuntimeError(f"UAM_FX_SIBLING_ROOT_MOVED {moved}")
    fx_ptrs = [int.from_bytes(product[FX_ROOT_FIELD + i * 4:FX_ROOT_FIELD + i * 4 + 4], "little")
               for i in range(fxt.FX_TSIZE)]
    if any(ROM + block_start <= p < ROM + block_end for i, p in enumerate(fx_ptrs) if i != FX_LEAF):
        raise RuntimeError("UAM_FX_ALIAS_HAZARD")
    other_pages = {"quest/0": 0x13CD8, "battle": 0x237F4, "condi": 0x13D98}
    for fam, field in other_pages.items():
        if product[field:field + 4] != base[field:field + 4]:
            raise RuntimeError(f"UAM_PAGES_ROOT_DISTURBED {fam}")
    sibling["other_pages_roots_unchanged"] = len(other_pages)
    sibling["mission_panel_leaf_9_untouched"] = (
        product[FX_ROOT_FIELD + 9 * 4:FX_ROOT_FIELD + 9 * 4 + 4]
        == base[FX_ROOT_FIELD + 9 * 4:FX_ROOT_FIELD + 9 * 4 + 4])
    audits["sibling"] = {**sibling, "result": "PASS"}

    # -- glyph / asset ------------------------------------------------------
    font = us.tabs["font"]
    fbase = font.real_offset
    if product[fbase:fbase + font.tsize * stext.FONT_STRIDE] != \
       base[fbase:fbase + font.tsize * stext.FONT_STRIDE]:
        raise RuntimeError("UAM_FONT_TABLE_CHANGED")
    if product[stext.US_METADATA:stext.US_METADATA + font.tsize] != \
       base[stext.US_METADATA:stext.US_METADATA + font.tsize]:
        raise RuntimeError("UAM_FONT_METADATA_CHANGED")
    used = sorted({v for r in records for k, v in r["_expected"] if k == "CHR_FULL"})
    allocated = set(alloc.values())
    high = list(used)
    stray = [v for v in high if v not in allocated]
    if stray:
        raise RuntimeError(f"UAM_SLOT_NOT_FROM_JP_ALLOCATION {stray[:8]}")
    jp_raw = JP.read_bytes()
    jfont = meta["jp"].tabs["font"].real_offset
    for slot in high:
        code = inverse[slot]
        joff = jfont + code * stext.FONT_STRIDE
        uoff = fbase + slot * stext.FONT_STRIDE
        if product[uoff:uoff + stext.FONT_STRIDE] != jp_raw[joff:joff + stext.FONT_STRIDE]:
            raise RuntimeError(f"UAM_SLOT_NOT_JP_IDENTICAL 0x{slot:04X}")
    audits["glyph"] = {"new_glyph_records": 0, "font_table_writes": 0, "metadata_writes": 0,
                       "distinct_slots_used": len(used),
                       "kanji_slots_used": sum(1 for s in high if inverse[s] >= 0x122),
                       "all_slots_are_native_jp_records": True, "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    fields = {int(r["root_pointer_field_us_rom"], 16) for r in records
              if r["route"] == "WORDS_DIRECT_REPOINT"}
    fields |= {PAGES_QUEST1_FIELD, fx_field}
    permitted = sorted([(f, f + 4) for f in fields] + [(block_start, block_end)])
    merged = []
    for lo, hi in permitted:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    changed_ranges = list(stext.changed_ranges(base, product))
    unexplained = [(f"0x{lo:08X}", f"0x{hi:08X}") for lo, hi in changed_ranges
                   if not any(a <= lo and hi <= b for a, b in merged)]
    if unexplained:
        raise RuntimeError(f"UAM_BINARY_TOUCH_REGRESSION {unexplained[:8]}")
    code_ranges = [c for c in changed_ranges if c[0] < 0x400000
                   and not any(f <= c[0] and c[1] <= f + 4 for f in fields)]
    if code_ranges:
        raise RuntimeError(f"UAM_CODE_TOUCHED {code_ranges[:4]}")
    audits["binary"] = {
        "result": "PASS", "changed_ranges": len(changed_ranges),
        "root_pointer_fields": len(fields), "relocated_payload_blocks": 1,
        "new_font_slots": 0, "unexplained_ranges": 0,
        "rom_executable_code_bytes_changed": 0,
        "block": {"start": f"0x{block_start:08X}", "words_end": f"0x{layout['words_block_end']:08X}",
                  "pages_leaf": f"0x{layout['pages_leaf']:08X}", "fx_leaf": f"0x{layout['fx_leaf']:08X}",
                  "end": f"0x{block_end:08X}", "bytes": block_end - block_start,
                  "free_below": f"0x{FREE_END:08X}"}}

    # -- the RC16 placeholder-transfer audit, on the written image -----------
    saved = pta.PRODUCTION
    try:
        pta.PRODUCTION = OUTROM
        census = pta.audit()
    finally:
        pta.PRODUCTION = saved
    if census["finding_count"] != 41 or census["still_placeholder"] != 0:
        raise RuntimeError(f"UAM_PLACEHOLDER_AUDIT {census['finding_count']}/"
                           f"{census['still_placeholder']}")
    # The audit decodes production by glyph *bitmap* (RC14: distinct JP codes
    # draw identical pixels, and charset_cn.json names some kanji in simplified
    # form), so its rendering is a placeholder detector, not a text identity;
    # the token-level identity above is the readback.  What it must say here is
    # that every finding is real content now and none matches a placeholder.
    ours = {r["us_logical_path"] for r in rows}
    for f in census["findings"]:
        if f["path"] not in ours:
            raise RuntimeError(f"UAM_PLACEHOLDER_AUDIT_UNEXPECTED_PATH {f['path']}")
        if f["still_placeholder"] or pta.JP_PLACEHOLDER.match(f["production"])                 or f["production"] == f["jp"] or not f["production"].strip():
            raise RuntimeError(f"UAM_PLACEHOLDER_AUDIT_TEXT {f['path']} {f['production']!r}")
    audits["placeholder_audit"] = {"records_compared": census["records_compared"],
                                   "findings": census["finding_count"],
                                   "still_placeholder_in_production": 0,
                                   "fx_text_22_121_nonempty": True, "result": "PASS",
                                   "production_as_decoded_by_audit":
                                       {f["path"]: f["production"] for f in census["findings"]}}
    return audits


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260901_production")
    ap.add_argument("--print-sha", action="store_true",
                    help="build once, print the SHA-256 and exit without writing a ROM")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")

    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return
    second = build()
    (product, base, meta, alloc, records, doc, rows, pages_record, fx_record,
     leaf_expected, fx_expected, layout, kanji, bounds) = first
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rs]
    if sha(product) != sha(second[0]) or strip(records) != strip(second[4]):
        raise RuntimeError("UAM_BUILD_NONDETERMINISTIC")
    if layout != second[11] or pages_record != second[7] or fx_record != second[8]:
        raise RuntimeError("UAM_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    decode, _reverse = sysbat.charset_tables()
    audits = validate(product, base, meta, alloc, records, rows, pages_record, fx_record,
                      leaf_expected, fx_expected, layout, decode, doc, bounds)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)} != {EXPECTED_PRODUCTION}")

    determinism = {"sha256_1": sha(product), "sha256_2": sha(second[0]), "identical": True,
                   "record_set_identical": True,
                   "layout_identical": layout == second[11],
                   "glyph_allocation_identical": alloc == second[3],
                   "baseline_sha256": sha(base)}
    summary = {
        "milestone": doc["milestone"],
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "batch": {"entries": len(records), "translated": len(records), "no_change_required": 0,
                  "by_family": {f: sum(1 for r in records if r["family"] == f) for f in FAMILIES},
                  "us_only_proof": {r: sum(1 for x in records if x["us_only_reason"] == r)
                                    for r in sorted({x["us_only_reason"] for x in records})}},
        "leaves": [pages_record, fx_record],
        "layout": {k: (f"0x{v:08X}" if k != "pristine_words_resembling_block_addresses" else v)
                   for k, v in layout.items()},
        "bounds": bounds,
        "kanji_verified_from_jp_originals": len(kanji),
        "audits": {k: v["result"] for k, v in audits.items()},
        "determinism": determinism,
    }
    write(out / "translation_records.json", strip(records))
    write(out / "readback.json", audits["readback"])
    write(out / "sibling_alias_audit.json", audits["sibling"])
    write(out / "glyph_audit.json", audits["glyph"])
    write(out / "binary_touch.json", audits["binary"])
    write(out / "placeholder_audit.json", audits["placeholder_audit"])
    write(out / "determinism.json", determinism)
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
