#! python3
"""Editorial localization of the US-only ``fx_text`` entries.

Third *class 5* milestone, and the one that closes the ``fx_text`` family.
164 entries that exist only in the US ROM and have no JP original anywhere:

* ``fx_text`` leaf 1, 40..46 (7)    -- clan / save system prompts
* ``fx_text`` leaf 8, 58 and 59 (2) -- two US-only rumours
* ``fx_text`` leaf 10, 3..6 (4)     -- the lesson-mode duplicates of those prompts
* ``fx_text`` leaf 11, 48..87 (40)  -- mission reports from five named characters
* ``fx_text`` leaf 15, 15..29 (15)  -- five farewell lines, each shipped三 times
* ``fx_text`` leaf 17, 364..372 (9) -- judge / law ability descriptions
* ``fx_text`` leaf 18, 70..72 (3)   -- judge job commands
* ``fx_text`` leaf 23, 262..345 (84)-- the US-only judge-point *bonus* laws

All 164 are newly written Japanese; nothing is closed ``NO_CHANGE_REQUIRED``.

The translations are NOT computed here.  They are read from the tracked manifest
``data/us_only_fx_text_translations.json``, which is the editorial decision of
record; this module encodes and installs it, and revalidates it against both
ROMs on every build.

Why the targets really have no JP original
------------------------------------------
Every target is proved US-only on every build, by two statements that must both
hold.  First, the record is **not** a source-transfer target: the same aligner,
tables and hints the earlier layers use put no JP original into it, so this
layer can neither overwrite one nor leave one unwritten.  Second, a structural
reason for that, read off the JP ROM -- the index is past the end of the JP leaf
(162 targets), the JP record at that index carries no ``CHR`` token at all (one
target, ``fx_text/8/59``), or the JP entry at that index is itself transferred
into a *different* US record because the US ROM inserted entries ahead of it
(one target, ``fx_text/8/58``).  ``check_us_only`` asserts both.

``fx_text`` leaf 8 is the shifted one: the US ROM inserts the ``[Unfair
Judges?]`` and ``[Blank Cards]`` rumours at 58 and 59, so JP 8/58 belongs to US
8/60 and JP 8/60 to US 8/61.  Both leaves still hold 91 entries because JP 8/59
and 8/61 are empty ``[42]`` records.

Why this family needs no new architecture
-----------------------------------------
* Every entry uses the ``CHR_FULL`` lane, which every ``fx_text`` JP original
  already ships.
* A ``CHR_FULL`` token value is a *US font slot*.  The production JP-to-US
  allocation built by the earlier layers already holds a native JP glyph record
  for all 303 distinct characters these translations use -- 159 of them kanji --
  so this layer allocates **zero** new glyphs and writes **zero** font or
  metadata bytes.
* All eight target leaves were already relocated into the tail by earlier
  layers.  Each is re-parsed out of the in-process product, re-serialized with
  only its own targets replaced, placed further down the tail and repointed, so
  every previously localized sibling record survives byte-for-byte.
* ``fx_text`` leaf 24 is the US-only alias leaf whose root pointer equals leaf
  23's *pristine* address.  Production has always left it pointing at the
  pristine payload; this layer does not touch it, and asserts so.

Consequences: **zero new glyph records, zero font or metadata writes, zero ROM
code bytes patched.**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import ffta_jp_coverage_audit as coverage
# imported after the audit module, which installs the font-generator stub
# ffta_modifier needs at import time
from ffta_modifier import CONF, c_tab_align_iter
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_only_system_battle as prev
from ffta_sect import (c_ffta_sect_rom, c_ffta_sect_fixed_text,
                       c_ffta_sect_text_buf, _trim_raw_len)
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_only_fx_text_translations.json"
RUN_BASE = HERE / "build/us_only_fx_text_localization"
OUTROM = ROOT / "rom/build/ffta_us_jp_fx_text_localized.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_fx_text_localized_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_us_only_system_battle, the previous production layer.
BASELINE = "8BCD0FB7CB3D5AA9A0168C1EDC5EC88EABD2FC943F5FFF223EB05E98B715F14C"
# Private per-layer drift gate.  This module is no longer the terminal layer --
# ffta_jp_us_only_s_text.py is the terminal layer and owns EXPECTED_PRODUCTION,
# the single canonical final-SHA authority.  Never quote the value below as
# "the production SHA".
EXPECTED_PRODUCTION = "27AA2CA34D6DE12F192DCD37BC3F3D873DEC0FBDD0D63ACA3417C1EAB1A31590"

# Address of the 4-byte pointer that locates the fx_text leaf-pointer table,
# as declared by ffta_sect.load_rom_us; and the table itself, whose entry L is
# leaf L's root pointer field.  Both are cross-checked against the parsed
# pristine US ROM on every build.
FX_TABLE_POINTER = 0x018050
FX_ROOT_FIELD = 0x0036D678
FX_TSIZE = 27
ALIAS_LEAF = 24                   # US-only alias leaf, repeats leaf 23's pristine payload
TARGET_LEAVES = (1, 8, 10, 11, 15, 17, 18, 23)
LEAF_TSIZE = {1: 47, 8: 91, 10: 7, 11: 88, 15: 30, 17: 373, 18: 73, 23: 346}
EXPECTED_INDEXES = {
    1: list(range(40, 47)),
    8: [58, 59],
    10: list(range(3, 7)),
    11: list(range(48, 88)),
    15: list(range(15, 30)),
    17: list(range(364, 373)),
    18: list(range(70, 73)),
    23: list(range(262, 346)),
}
TOTAL = 164

CTR_TERMINATOR = 0x42
CTR_WORD_SPACE = 0x52
COMPRESSION_BIT = 0x0002
LINE_BREAK, PAGE_BREAK = 0x4D, 0x4F
CLOSE_CONTROLS = {0x40, 0x42, 0x56}
# Layout-only controls.  Everything else a US original carries is a reference,
# a selector or an emphasis control and must survive translation.
LAYOUT_CONTROLS = {0x40, 0x42, 0x4D, 0x4F, 0x52, 0x56,
                   0x1B01, 0x1D02, 0x1D03, 0x1D04, 0x1D0A}
# English typesetting that carries no meaning in Japanese and must be gone.
FORBIDDEN_CONTROLS = {0x1B01, 0x1D02, 0x1D03, 0x1D04}
# fx_text leaf 10 holds three short JP treasure lines that do not bound the box;
# its US-only entries are the lesson-mode duplicates of the leaf 1 prompts.
WIDTH_REFERENCE_LEAF = {10: 1}

MARKUP = re.compile(r"\{([0-9A-F]{1,4}|EOS)\}")


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


def payload(page, index):
    """A record's own bytes, without leaf alignment padding.

    ``serialize_leaf`` aligns each record to 2 bytes and the finished leaf to 4,
    and the parser hands the last record a ``raw_len`` that runs to the end of
    the leaf -- so a record's ``raw_len`` absorbs padding whose size depends on
    the lengths of *other* records.  Comparing that padding would report a
    content change for a record that did not change.  All padding is zeros and
    every record ends in its own ``0x00`` EOS byte, so trimming trailing zeros
    from both sides compares payloads.  Record identity is not weakened by this:
    ``sibling`` also compares the token list of every record in the leaf.
    """
    record = page[index]
    return bytes(record.BYTES(0, record.raw_len)).rstrip(b"\x00")


# --------------------------------------------------------------- manifest ---

def kanji_table(doc, jp):
    """Kanji -> JP glyph code, each revalidated against its JP attestation.

    Production never trusts a font-alignment or font-matching guess.  Every
    kanji in the manifest names a JP-original entry that actually uses that
    glyph code, and the claim is re-checked here on every build.
    """
    table = {}
    for char, row in doc["kanji_codes"].items():
        code = int(row["jp_glyph_code"], 16)
        if code < 0x122:
            raise RuntimeError(f"FXT_KANJI_CODE_NOT_IN_KANJI_SPAN {char}")
        path = row["attested_at"]
        if not path:
            raise RuntimeError(f"FXT_KANJI_ATTESTATION_MISSING {char}")
        if not any(kind == "CHR_FULL" and value == code
                   for kind, value in prev.jp_entry(jp, path)):
            raise RuntimeError(f"FXT_KANJI_ATTESTATION_FAILED {char} "
                               f"0x{code:04X} {path}")
        if char in table:
            raise RuntimeError(f"FXT_KANJI_DUPLICATE {char}")
        table[char] = code
    return table


def visible(tokens, decode):
    out = []
    for kind, value in tokens:
        if kind.startswith("CHR"):
            out.append(decode.get(value, f"<{value:04X}>"))
        elif kind == "CTR_EOS":
            out.append("[0]")
        elif kind == "CTR_FUNC" and value == CTR_WORD_SPACE:
            out.append(" ")
        else:
            out.append(f"[{value:X}]")
    return "".join(out)


def us_leaf(us, leaf):
    page = us.tabs["fx_text"][leaf]
    if page is None or isinstance(page, list):
        raise RuntimeError(f"FXT_LEAF_NOT_A_PAGE {leaf}")
    return page


def source_transfer_pairs(jp, us):
    """US ``fx_text`` record -> the JP record transferred into it.

    Built with the aligner, tables and hints the earlier source-transfer layers
    use, so this is exactly the set of ``fx_text`` records production fills from
    the JP ROM.  Both directions matter here: a US record that is a target must
    not also be an editorial target, and a JP entry that is a *source* proves
    the US record sharing its index has no JP original of its own.
    """
    jtabs, _ = coverage.grouped_tabs(jp)
    utabs, _ = coverage.grouped_tabs(us)
    pairs = {}
    for (jpath, jline), (upath, uline) in c_tab_align_iter(
            jtabs.get("fx_text"), utabs.get("fx_text"),
            align_map=CONF["text"]["align"].get("fx_text", []),
            trim_page=CONF["text"]["trim"].get("fx_text", [])).iter():
        if not (coverage.entry(jline) and coverage.entry(uline)):
            continue
        target, source = tuple(upath), tuple(jpath)
        if target in pairs:
            raise RuntimeError(f"FXT_DUPLICATE_TRANSFER_TARGET {target}")
        pairs[target] = source
    return pairs


def check_us_only(jp, leaf, index, pairs, sources):
    """Prove the target has no JP original, from the ROMs and the aligner."""
    if (leaf, index) in pairs:
        raise RuntimeError(f"FXT_TARGET_IS_SOURCE_TRANSFER {leaf}/{index} "
                           f"<- {pairs[(leaf, index)]}")
    page = jp.tabs["fx_text"][leaf]
    if page is None or isinstance(page, list):
        return "JP_LEAF_ABSENT"
    if index >= page.tsize:
        return "PAST_JP_LEAF_END"
    record = page[index]
    if isinstance(record, list):
        raise RuntimeError(f"FXT_JP_REPEAT_AT_TARGET {leaf}/{index}")
    if not any(kind.startswith("CHR") for kind, _ in tokens_of(record)):
        return "JP_RECORD_EMPTY"
    # The JP leaf carries text at this index, but it belongs to a different US
    # record: the US ROM inserted entries ahead of it.  Only then is the US
    # record at this index genuinely without a JP original.
    if (leaf, index) in sources:
        return "JP_INDEX_TRANSFERRED_TO_ANOTHER_US_ENTRY"
    raise RuntimeError(f"FXT_TARGET_HAS_JP_ORIGINAL {leaf}/{index}")


def load_manifest(jp, us, decode):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pairs = source_transfer_pairs(jp, us)
    sources = set(pairs.values())
    rows = doc["entries"]
    if len(rows) != TOTAL or doc["count"] != TOTAL:
        raise RuntimeError(f"FXT_MANIFEST_COUNT {len(rows)}/{doc['count']}")
    if doc["baseline_production_sha256"] != BASELINE:
        raise RuntimeError("FXT_MANIFEST_BASELINE_DRIFT")
    got = {}
    for row in rows:
        leaf, index = row["us_leaf"], row["us_entry"]
        got.setdefault(leaf, []).append(index)
        if row["us_logical_path"] != f"fx_text/{leaf}/{index}":
            raise RuntimeError(f"FXT_MANIFEST_PATH {row['us_logical_path']}")
        if row["status"] != "TRANSLATED_REVIEWED":
            raise RuntimeError(f"FXT_MANIFEST_STATUS {row['us_logical_path']}")
        if not row["japanese"]:
            raise RuntimeError(f"FXT_MANIFEST_EMPTY {row['us_logical_path']}")
        page = us_leaf(us, leaf)
        if page.tsize != LEAF_TSIZE[leaf]:
            raise RuntimeError(f"FXT_US_LEAF_TSIZE {leaf} {page.tsize}")
        if not _us_source.matches(row, visible(tokens_of(page[index]), decode)):
            raise RuntimeError(f"FXT_MANIFEST_ENGLISH_DRIFT {row['us_logical_path']}")
        row["_us_only_reason"] = check_us_only(jp, leaf, index, pairs, sources)
    if {k: sorted(v) for k, v in got.items()} != EXPECTED_INDEXES:
        raise RuntimeError(f"FXT_MANIFEST_INDEX_SET {sorted(got)}")
    if doc["translated_count"] != TOTAL or doc["no_change_required_count"] != 0:
        raise RuntimeError("FXT_MANIFEST_STATUS_COUNTS")
    return doc, rows


# --------------------------------------------------------------- encoding ---

def parse_markup(japanese, reverse, kanji):
    """``…{4D}一覧{40}{42}`` -> JP-glyph-code token list.

    ``[`` and ``]`` are literal characters in this family (mission names are
    printed as ``[name]``), which is why controls are braced rather than
    bracketed.
    """
    out, i = [], 0
    while i < len(japanese):
        match = MARKUP.match(japanese, i)
        if match:
            group = match.group(1)
            out.append(("CTR_EOS", 0) if group == "EOS"
                       else ("CTR_FUNC", int(group, 16)))
            i = match.end()
            continue
        char = japanese[i]
        if char in kanji:
            out.append(("CHR_FULL", kanji[char]))
        elif char in reverse:
            out.append(("CHR_FULL", reverse[char]))
        else:
            raise RuntimeError(f"FXT_UNENCODABLE {char!r} in {japanese!r}")
        i += 1
    return out


def line_widths(tokens, jp_raw):
    """Rendered width of each line, in JP font advance pixels."""
    widths, current = [], 0
    for kind, value in tokens:
        if kind.startswith("CHR"):
            current += jp_raw[stext.JP_METADATA + value]
        elif kind == "CTR_FUNC" and value in (LINE_BREAK, PAGE_BREAK):
            widths.append(current)
            current = 0
    widths.append(current)
    return widths


def lines_per_page(tokens):
    pages = [1]
    for kind, value in tokens:
        if kind != "CTR_FUNC":
            continue
        if value == LINE_BREAK:
            pages[-1] += 1
        elif value == PAGE_BREAK:
            pages.append(1)
    return pages


def leaf_bounds(jp, jp_raw):
    """Per-leaf width and lines-per-page bounds, from the JP originals."""
    bounds = {}
    for leaf in TARGET_LEAVES:
        reference = WIDTH_REFERENCE_LEAF.get(leaf, leaf)
        page = jp.tabs["fx_text"][reference]
        width = lines = 0
        for index in range(page.tsize):
            record = page[index]
            if isinstance(record, list):
                continue
            toks = tokens_of(record)
            width = max([width] + line_widths(toks, jp_raw))
            lines = max([lines] + lines_per_page(toks))
        if not width or not lines:
            raise RuntimeError(f"FXT_LEAF_BOUND_EMPTY {leaf}")
        bounds[leaf] = {"reference_leaf": reference, "max_line_px": width,
                        "max_lines_per_page": lines}
    return bounds


def to_slots(tokens, alloc):
    out = []
    for kind, value in tokens:
        if kind == "CHR_FULL":
            if value not in alloc:
                raise RuntimeError(f"FXT_GLYPH_NOT_ALLOCATED 0x{value:04X}")
            out.append(("CHR_FULL", alloc[value]))
        else:
            out.append((kind, value))
    return out


def closing(tokens):
    out = []
    for kind, value in reversed(tokens):
        if kind == "CTR_FUNC" and value in CLOSE_CONTROLS:
            out.append(value)
        else:
            break
    return list(reversed(out))


def check_controls(path, source, original, translated):
    """Every meaning-bearing US control survives; English typesetting does not."""
    def significant(tokens):
        return {value for kind, value in tokens
                if kind == "CTR_FUNC" and value not in LAYOUT_CONTROLS}
    us_refs, jp_refs = significant(original), significant(translated)
    if not us_refs <= jp_refs:
        raise RuntimeError(f"FXT_CONTROL_DROPPED {path} "
                           f"{sorted(hex(v) for v in us_refs - jp_refs)}")
    added = jp_refs - us_refs
    if added:
        raise RuntimeError(f"FXT_CONTROL_ADDED {path} "
                           f"{sorted(hex(v) for v in added)}")
    bad = {v for k, v in translated if k == "CTR_FUNC" and v in FORBIDDEN_CONTROLS}
    if bad:
        raise RuntimeError(f"FXT_ENGLISH_TYPESETTING_KEPT {path} "
                           f"{sorted(hex(v) for v in bad)}")
    if any(k == "CTR_FUNC" and v == CTR_WORD_SPACE for k, v in translated):
        raise RuntimeError(f"FXT_WORD_SPACE_KEPT {path}")
    if sum(1 for k, _ in original if k == "CTR_EOS") != \
            sum(1 for k, _ in translated if k == "CTR_EOS"):
        raise RuntimeError(f"FXT_EOS_SEPARATOR_COUNT {path}")
    if closing(original) != closing(translated):
        raise RuntimeError(f"FXT_CLOSE_SEQUENCE {path}")
    if not translated or translated[-1] != ("CTR_FUNC", CTR_TERMINATOR):
        raise RuntimeError(f"FXT_TERMINATOR_MISSING {path}")
    return {"references_preserved": sorted(f"0x{v:04X}" for v in us_refs),
            "close_sequence": [f"0x{v:04X}" for v in closing(translated)],
            "source": source}


def encode(row, reverse, kanji, alloc, jp_raw, bounds, original):
    path = row["us_logical_path"]
    if row["source"] != "EDITORIAL":
        raise RuntimeError(f"FXT_SOURCE_NOT_EDITORIAL {path} {row['source']}")
    jp_tokens = parse_markup(row["japanese"], reverse, kanji)
    control = check_controls(path, row["source"], original, jp_tokens)
    bound = bounds[row["us_leaf"]]
    widths = line_widths(jp_tokens, jp_raw)
    if max(widths) > bound["max_line_px"]:
        raise RuntimeError(f"FXT_WIDTH_OVER_LEAF_BOUND {path} "
                           f"{max(widths)}>{bound['max_line_px']}")
    pages = lines_per_page(jp_tokens)
    if max(pages) > bound["max_lines_per_page"]:
        raise RuntimeError(f"FXT_LINES_OVER_LEAF_BOUND {path} "
                           f"{max(pages)}>{bound['max_lines_per_page']}")
    expected = to_slots(jp_tokens, alloc)
    data = stext.encode_standard(expected)
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"FXT_SERIALIZER_ROUNDTRIP_FAILED {path}")
    if data[-1] != 0:
        raise RuntimeError(f"FXT_EOS_MISSING {path}")
    return jp_tokens, expected, data, max(widths), len(pages), control


# ------------------------------------------------------------------ build ---

def product_fx_text(raw):
    """Re-parse the fx_text table out of a product image, by its real pointers."""
    data = bytes(raw)
    rom = c_ffta_sect_rom(data, 0).setup(
        {"fx_text": (FX_TABLE_POINTER, c_ffta_sect_fixed_text,
                     c_ffta_sect_rom.ARG_SELF, FX_TSIZE)},
        _trim_raw_len(data, 0xF00000))
    table = rom.tabs["fx_text"]
    if table.tsize != FX_TSIZE or table.real_offset != FX_ROOT_FIELD:
        raise RuntimeError("FXT_ROOT_DECL_DRIFT")
    return table


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"PRODUCTION_BASELINE_MISMATCH {sha(base)}")
    meta, alloc, tail_end = previous[2], previous[3], previous[14]
    jp, us = meta["jp"], meta["us"]
    jp_raw = JP.read_bytes()

    decode, reverse = prev.charset_tables()
    doc, rows = load_manifest(jp, us, decode)
    kanji = kanji_table(doc, jp)
    bounds = leaf_bounds(jp, jp_raw)

    raw = bytearray(base)
    table = product_fx_text(raw)
    # Snapshot taken before any tail write: the leaf objects address this image.
    snapshot = bytes(raw)
    if table.real_offset != us.tabs["fx_text"].real_offset:
        raise RuntimeError("FXT_ROOT_OFFSET_DRIFT")

    records, replacements, expectations = [], {}, {}
    for row in rows:
        leaf, index = row["us_leaf"], row["us_entry"]
        original = tokens_of(us_leaf(us, leaf)[index])
        _tokens, expected, data, width, pages, control = encode(
            row, reverse, kanji, alloc, jp_raw, bounds, original)
        record = table[leaf][index]
        flags = record.U16(0) & ~COMPRESSION_BIT
        replacements.setdefault(leaf, {})[index] = \
            flags.to_bytes(2, "little") + data
        expectations.setdefault(leaf, {})[index] = expected
        records.append({
            "us_logical_path": row["us_logical_path"], "us_leaf": leaf,
            "us_entry": index, "original_english_sha256": _us_source.digest(row),
            "japanese": row["japanese"], "status": row["status"],
            "us_only_reason": row["_us_only_reason"],
            "record_flags": f"0x{flags:04X}", "payload_length": len(data),
            "rendered_width_px": width, "pages": pages,
            "width_bound_px": bounds[leaf]["max_line_px"],
            "eos": True, "roundtrip": "PASS", "controls": control,
            "_expected": expected, "_data": data})

    cursor = block_start = stext.align(tail_end, 4)
    leaf_records = []
    for leaf in TARGET_LEAVES:
        page = table[leaf]
        if page.tsize != LEAF_TSIZE[leaf]:
            raise RuntimeError(f"FXT_PRODUCT_LEAF_TSIZE {leaf} {page.tsize}")
        if len(replacements[leaf]) != len(EXPECTED_INDEXES[leaf]):
            raise RuntimeError(f"FXT_LEAF_REPLACEMENT_COUNT {leaf}")
        blob = stext.serialize_leaf(snapshot, page, replacements[leaf])
        field = FX_ROOT_FIELD + leaf * 4
        old = int.from_bytes(raw[field:field + 4], "little")
        raw[cursor:cursor + len(blob)] = blob
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, "little")
        leaf_records.append({
            "us_leaf": leaf, "root_field_us_rom": f"0x{field:08X}",
            "old_cpu_pointer": f"0x{old:08X}",
            "new_cpu_pointer": f"0x{ROM + cursor:08X}",
            "previous_leaf_us_rom": f"0x{page.real_offset:08X}",
            "previous_size": page.sect_top, "new_leaf_us_rom": f"0x{cursor:08X}",
            "new_size": len(blob), "entries": page.tsize,
            "records_replaced": len(replacements[leaf]),
            "records_preserved": page.tsize - len(replacements[leaf])})
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("FXT_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("FXT_BLOCK_OUTSIDE_TAIL")
    return (bytes(raw), base, meta, alloc, records, doc, rows, leaf_records,
            expectations, replacements, block_start, block_end, kanji, bounds)


# --------------------------------------------------------------- validate ---

def validate(product, base, meta, alloc, records, rows, leaf_records,
             expectations, block_start, block_end, decode, doc):
    us = meta["us"]
    audits = {}

    # -- independent ROM readback ------------------------------------------
    if sha(OUTROM) != sha(product):
        raise RuntimeError("FXT_READBACK_ROM_MISMATCH")
    inverse = {slot: code for code, slot in alloc.items()}
    decode = dict(decode)
    decode.update({int(row["jp_glyph_code"], 16): char
                   for char, row in doc["kanji_codes"].items()})

    def char(value):
        if value in inverse:
            return decode.get(inverse[value], f"<jp:{inverse[value]:04X}>")
        return decode.get(value, f"<us:{value:04X}>")

    def render(tokens):
        out = []
        for kind, value in tokens:
            if kind.startswith("CHR"):
                out.append(char(value))
            elif kind == "CTR_EOS":
                out.append("{EOS}")
            else:
                out.append("{%X}" % value)
        return "".join(out)

    check = product_fx_text(Path(OUTROM).read_bytes())
    readback = []
    for row, record in zip(rows, records):
        leaf, index = row["us_leaf"], row["us_entry"]
        tokens = tokens_of(check[leaf][index])
        got = render(tokens)
        if got != row["japanese"]:
            raise RuntimeError(f"FXT_READBACK_TEXT_MISMATCH "
                               f"{row['us_logical_path']} {got!r}")
        if tokens != record["_expected"]:
            raise RuntimeError(f"FXT_READBACK_TOKEN_MISMATCH "
                               f"{row['us_logical_path']}")
        if tokens[-1] != ("CTR_FUNC", CTR_TERMINATOR):
            raise RuntimeError(f"FXT_READBACK_TERMINATOR "
                               f"{row['us_logical_path']}")
        pointer = int.from_bytes(
            Path(OUTROM).read_bytes()[FX_ROOT_FIELD + leaf * 4:
                                      FX_ROOT_FIELD + leaf * 4 + 4], "little")
        expected_pointer = next(int(x["new_cpu_pointer"], 16)
                                for x in leaf_records if x["us_leaf"] == leaf)
        if pointer != expected_pointer:
            raise RuntimeError(f"FXT_READBACK_ROOT_POINTER {leaf}")
        readback.append({"us_logical_path": row["us_logical_path"],
                         "root_cpu_pointer": f"0x{pointer:08X}",
                         "leaf_record": f"{leaf}/{index}",
                         "decoded": got, "result": "PASS"})
    audits["readback"] = {"entries": len(readback), "failures": 0,
                          "source": "independent parse of the written ROM file, "
                                    "following its own fx_text root pointers",
                          "result": "PASS", "rows": readback}

    # -- sibling / alias ----------------------------------------------------
    after = product_fx_text(product)
    before = product_fx_text(base)
    sibling = {"leaves_rebuilt": len(TARGET_LEAVES), "unintended_record_changes": 0,
               "leaves": {}}
    for leaf in TARGET_LEAVES:
        new, old = after[leaf], before[leaf]
        if new.tsize != LEAF_TSIZE[leaf] or old.tsize != LEAF_TSIZE[leaf]:
            raise RuntimeError(f"FXT_LEAF_TSIZE_CHANGED {leaf}")
        changed = [i for i in range(new.tsize)
                   if payload(new, i) != payload(old, i)]
        if sorted(changed) != sorted(expectations[leaf]):
            raise RuntimeError(f"FXT_UNEXPECTED_RECORD_CHANGE {leaf} "
                               f"{sorted(set(changed) ^ set(expectations[leaf]))}")
        for index in range(new.tsize):
            if index in expectations[leaf]:
                if tokens_of(new[index]) != expectations[leaf][index]:
                    raise RuntimeError(f"FXT_LEAF_TOKEN_MISMATCH {leaf}/{index}")
            elif tokens_of(new[index]) != tokens_of(old[index]):
                raise RuntimeError(f"FXT_SIBLING_TOKEN_CHANGED {leaf}/{index}")
        sibling["leaves"][str(leaf)] = {
            "entries": new.tsize, "records_replaced": len(expectations[leaf]),
            "records_byte_preserved": new.tsize - len(expectations[leaf])}
    # every fx_text root field except the eight targets is byte-identical, and
    # the alias leaf still points at the pristine payload it always pointed at.
    owned = set(TARGET_LEAVES)
    moved = [i for i in range(FX_TSIZE) if i not in owned
             and product[FX_ROOT_FIELD + i * 4:FX_ROOT_FIELD + i * 4 + 4]
             != base[FX_ROOT_FIELD + i * 4:FX_ROOT_FIELD + i * 4 + 4]]
    if moved:
        raise RuntimeError(f"FXT_SIBLING_ROOT_MOVED {moved}")
    pristine = US.read_bytes()
    alias_field = FX_ROOT_FIELD + ALIAS_LEAF * 4
    if product[alias_field:alias_field + 4] != pristine[alias_field:alias_field + 4]:
        raise RuntimeError("FXT_ALIAS_LEAF_DISTURBED")
    pointers = [int.from_bytes(product[FX_ROOT_FIELD + i * 4:
                                       FX_ROOT_FIELD + i * 4 + 4], "little")
                for i in range(FX_TSIZE)]
    ours = [pointers[i] for i in owned]
    if len(set(ours)) != len(ours):
        raise RuntimeError("FXT_POINTER_COLLISION")
    if set(ours) & {pointers[i] for i in range(FX_TSIZE) if i not in owned}:
        raise RuntimeError("FXT_ALIAS_PROPAGATION")
    for leaf, record in zip(TARGET_LEAVES, leaf_records):
        lo = int(record["new_cpu_pointer"], 16)
        hi = lo + record["new_size"]
        if any(lo <= pointers[i] < hi for i in range(FX_TSIZE) if i not in owned):
            raise RuntimeError(f"FXT_ALIAS_HAZARD {leaf}")
    sibling.update({"non_target_root_fields_changed": 0, "pointer_collisions": 0,
                    "alias_propagation": 0,
                    "alias_leaf_24_pointer": f"0x{pointers[ALIAS_LEAF]:08X}",
                    "alias_leaf_24": "unchanged, still the pristine US payload",
                    "result": "PASS"})
    audits["sibling"] = sibling

    # -- other production families untouched --------------------------------
    families = {}
    for name, pool in us.tabs["words"].items():
        span = pool.real_offset, pool.real_offset + pool.tsize * 4
        if product[span[0]:span[1]] != base[span[0]:span[1]]:
            raise RuntimeError(f"FXT_WORDS_FAMILY_DISTURBED words:{name}")
        families[f"words:{name}"] = pool.tsize
    if product[prev.PAGES_ROOT_FIELD:prev.PAGES_ROOT_FIELD + 4] != \
       base[prev.PAGES_ROOT_FIELD:prev.PAGES_ROOT_FIELD + 4]:
        raise RuntimeError("FXT_PAGES_BATTLE_DISTURBED")
    audits["other_families"] = {"words_root_tables_unchanged": len(families),
                                "pages_battle_root_unchanged": True,
                                "result": "PASS"}

    # -- glyph / asset ------------------------------------------------------
    font = us.tabs["font"]
    fbase = font.real_offset
    if product[fbase:fbase + font.tsize * stext.FONT_STRIDE] != \
       base[fbase:fbase + font.tsize * stext.FONT_STRIDE]:
        raise RuntimeError("FXT_FONT_TABLE_CHANGED")
    if product[stext.US_METADATA:stext.US_METADATA + font.tsize] != \
       base[stext.US_METADATA:stext.US_METADATA + font.tsize]:
        raise RuntimeError("FXT_FONT_METADATA_CHANGED")
    used = sorted({v for r in records for k, v in r["_expected"] if k == "CHR_FULL"})
    allocated = set(alloc.values())
    stray = [v for v in used if v not in allocated]
    if stray:
        raise RuntimeError(f"FXT_SLOT_NOT_FROM_JP_ALLOCATION {stray[:8]}")
    jp_raw = JP.read_bytes()
    for slot in used:
        code = inverse[slot]
        joff = meta["jp"].tabs["font"].real_offset + code * stext.FONT_STRIDE
        uoff = fbase + slot * stext.FONT_STRIDE
        if product[uoff:uoff + stext.FONT_STRIDE] != jp_raw[joff:joff + stext.FONT_STRIDE]:
            raise RuntimeError(f"FXT_SLOT_NOT_JP_IDENTICAL 0x{slot:04X}")
    audits["glyph"] = {"new_glyph_records": 0, "font_table_writes": 0,
                       "metadata_writes": 0, "low_slot_overwrites": 0,
                       "existing_mappings_changed": 0,
                       "distinct_slots_used": len(used),
                       "kanji_slots_used": sum(1 for s in used if inverse[s] >= 0x122),
                       "all_slots_are_native_jp_records": True,
                       "last_allocated_slot": f"0x{max(allocated):04X}",
                       "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    permitted = [(FX_ROOT_FIELD + leaf * 4, FX_ROOT_FIELD + leaf * 4 + 4)
                 for leaf in TARGET_LEAVES]
    permitted.append((block_start, block_end))
    permitted.sort()
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
        raise RuntimeError(f"FXT_BINARY_TOUCH_REGRESSION {unexplained[:8]}")
    fields = {FX_ROOT_FIELD + leaf * 4 for leaf in TARGET_LEAVES}
    code_ranges = [c for c in changed_ranges if c[0] < 0x400000
                   and not any(f <= c[0] and c[1] <= f + 4 for f in fields)]
    if code_ranges:
        raise RuntimeError(f"FXT_CODE_TOUCHED {code_ranges[:4]}")
    audits["binary"] = {
        "result": "PASS", "changed_ranges": len(changed_ranges),
        "root_pointer_fields": len(fields), "relocated_payload_blocks": 1,
        "new_font_slots": 0, "unexplained_ranges": 0,
        "rom_executable_code_bytes_changed": 0,
        "block": {"start": f"0x{block_start:08X}", "end": f"0x{block_end:08X}",
                  "bytes": block_end - block_start,
                  "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end}}
    return audits


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260830_production")
    ap.add_argument("--print-sha", action="store_true",
                    help="build once, print the resulting SHA-256 and exit "
                         "without writing a ROM; used only to adopt the "
                         "constant after a deliberate change")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")

    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return

    second = build()
    (product, base, meta, alloc, records, doc, rows, leaf_records, expectations,
     replacements, bs, be, kanji, bounds) = first
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("_")}
                        for r in rs]
    if sha(product) != sha(second[0]) or strip(records) != strip(second[4]):
        raise RuntimeError("FXT_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[10], second[11]) or leaf_records != second[7]:
        raise RuntimeError("FXT_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    decode, _reverse = prev.charset_tables()
    audits = validate(product, base, meta, alloc, records, rows, leaf_records,
                      expectations, bs, be, decode, doc)
    if sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(
            f"CANONICAL_PRODUCTION_MISMATCH {sha(product)} != {EXPECTED_PRODUCTION}")

    determinism = {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                   "identical": True, "record_set_identical": True,
                   "pointer_layout_identical": leaf_records == second[7],
                   "serialized_text_identical":
                       [r["japanese"] for r in records] ==
                       [r["japanese"] for r in second[4]],
                   "glyph_allocation_identical": alloc == second[3],
                   "baseline_sha256": sha(base)}
    summary = {
        "milestone": doc["milestone"],
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "batch": {"entries": len(records), "translated": len(records),
                  "no_change_required": 0,
                  "leaves": {str(x["us_leaf"]): x["records_replaced"]
                             for x in leaf_records},
                  "us_only_proof": {r: sum(1 for x in records
                                           if x["us_only_reason"] == r)
                                    for r in sorted({x["us_only_reason"]
                                                     for x in records})}},
        "leaves": leaf_records,
        "width_bounds": bounds,
        "kanji_verified_from_jp_originals": len(kanji),
        "audits": {k: v["result"] for k, v in audits.items()},
        "determinism": determinism,
        "coverage": {
            "class_1_production_jp_source_transfer": 11168,
            "class_5_editorial_produced_before": 642,
            "class_5_editorial_translated_by_this_layer": len(records),
            "class_5_no_change_required_before": 2,
            "note": "Recompute the exact remainder with "
                    "ffta_jp_us_only_editorial_scope.py -- never by arithmetic."},
    }
    write(out / "translation_manifest_echo.json",
          {"entries": strip(records), "kanji_codes": doc["kanji_codes"]})
    write(out / "production_readback.json", audits["readback"])
    write(out / "sibling_alias_audit.json", audits["sibling"])
    write(out / "glyph_audit.json", audits["glyph"])
    write(out / "binary_touch.json", audits["binary"])
    write(out / "determinism.json", determinism)
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
