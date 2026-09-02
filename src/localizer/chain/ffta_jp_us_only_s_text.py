#! python3
"""Editorial localization of the US-only s_text special-character scenes.

Fourth *class 5* milestone, and the first s_text editorial batch.  Five whole
US scene-dialogue leaves that exist only in the US ROM:

* s_text top 44 (52 lines) -- バブズと王子の忘れ物 (pub -> 琥珀の谷 -> queen -> クマさん)
* s_text top 45 (34 lines) -- リッツと雪の仕事 (snowball-fight callback, Ritz joins)
* s_text top 46 (47 lines) -- シャアラ救出 (antlion cave, Shara joins)
* s_text top 48 (26 lines) -- リッツとシャアラ (pub scene)
* s_text top 49 (28 lines) -- シドとバブズ (アルタ·オロン。ソンドス·カミーラ)

187 lines: 182 of the exact 958-entry editorial scope plus the five line-0
scene openers.  Line 0 of each top is excluded from the 958 only because the
aligner pairs it with the JP *page-level* repeat marker at the same top (JP
tops 36..59 are aliases of the JP top-35 cut-label page, "PRESENT but empty");
those five lines have no JP original either and the scenes cannot ship with
their opening line in English.  They are labelled ``SCENE_COMPLETION`` in the
manifest and are deliberately reported apart from the 958-scope coverage.

The translations are NOT computed here.  They are read from the tracked
manifest ``data/us_only_s_text_special_scenes.json``, the editorial decision of
record; this module encodes and installs it, and revalidates it against both
ROMs on every build.

Why the targets really have no JP original
------------------------------------------
Proved on every build with the aligner the source-transfer layers use: no
target is a source-transfer target (the aligner puts no JP entry into any of
them), and each names a structural reason read off the alignment -- the JP side
aligned to the line is absent (``NO_JP_ENTRY_ALIGNED``) or is the page-level
repeat marker (``JP_REPEAT_PAGE_MARKER``, the five line 0s).  Independently,
the five tops are disjoint from every s_text top an earlier layer writes
(leaf repoint: 0..34, 61, 62; scene-script recovery: 35/37/38/39/42/50), and
that is asserted too.

Architecture: relocated full leaves + root repoint (established)
----------------------------------------------------------------
The five leaves are untouched by every earlier layer, so they are serialized
from the pristine US image with all 187 records replaced, placed in the tail
after the fx_text editorial block, and the five 4-byte root-relative offset
fields at ``s_text_root + top*4`` are repointed.  s_text root fields are
root-relative (not CPU pointers) -- same convention as
``ffta_jp_s_text_recovered``.  Every kanji is pinned by a JP-original
attestation revalidated against the JP ROM on every build; the production
JP-to-US glyph allocation already holds every character these translations
use.

Consequences: **zero new glyph records, zero font or metadata writes, zero ROM
code bytes patched.**  Only five root fields and one relocated tail block.

Width and page discipline are bound by the JP originals of the same family:
the widest rendered line across JP s_text story pages 0..34 and the maximum
lines-per-page (3) bound every translated line, measured with the JP font
advance metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import ffta_jp_coverage_audit as coverage
from ffta_modifier import CONF, c_tab_align_iter
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_s_text_recovered as recovered
import ffta_jp_us_only_system_battle as sysbat
import ffta_jp_us_only_fx_text as prev
from ffta_sect import (c_ffta_sect_text_page, c_ffta_sect_text_buf,
                       c_ffta_sect_text_line)
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_only_s_text_special_scenes.json"
RUN_BASE = HERE / "build/us_only_s_text_localization"
OUTROM = ROOT / "rom/build/ffta_us_jp_s_text_scenes.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_s_text_scenes_repeat.gba"

# Output of ffta_jp_us_only_fx_text, the previous production layer.  That
# module's EXPECTED_PRODUCTION is now a private per-layer drift gate.
BASELINE = "27AA2CA34D6DE12F192DCD37BC3F3D873DEC0FBDD0D63ACA3417C1EAB1A31590"
# Output of this layer.  Superseded as the terminal layer by
# ffta_jp_us_only_s_text_judge_ezel (which asserts this value as its BASELINE);
# this constant is now a private per-layer drift gate, never "the production
# SHA".
EXPECTED_PRODUCTION = "1A37D0E231172AA027BF169C1260672C0AAAC058E0F61F771228B842FC08142C"

S_TEXT_TABLE_POINTER = 0x009A88
TARGET_TOPS = (44, 45, 46, 48, 49)
TOP_TSIZE = {44: 52, 45: 34, 46: 47, 48: 26, 49: 28}
TOTAL = 187
IN_SCOPE = 182
SCENE_COMPLETION = 5

CTR_TERMINATOR = 0x42
CTR_WORD_SPACE = 0x52
COMPRESSION_BIT = 0x0002
LINE_BREAK, PAGE_BREAK = 0x4D, 0x4F
CLOSE_CONTROLS = {0x40, 0x42, 0x56}
LAYOUT_CONTROLS = {0x40, 0x42, 0x4D, 0x4F, 0x52, 0x56}
MARKUP = re.compile(r"\{([0-9A-F]{1,4}|EOS)\}")

# JP story pages that bound width and lines-per-page for this family.
BOUND_TOPS = range(0, 35)


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


# --------------------------------------------------------------- manifest ---

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


def alignment_status(jp, us):
    """(top, line) -> aligned-JP status, from the source-transfer aligner.

    ``HAS_JP`` marks a genuine source-transfer pairing; the two structural
    US-only reasons are ``NO_JP_ENTRY_ALIGNED`` (the aligner puts nothing at
    all opposite the US line) and ``JP_REPEAT_PAGE_MARKER`` (the aligned JP
    object is a page-level alias marker, present but empty).
    """
    jtabs, _ = coverage.grouped_tabs(jp)
    utabs, _ = coverage.grouped_tabs(us)
    status = {}
    for (jpath, jline), (upath, uline) in c_tab_align_iter(
            jtabs.get("s_text"), utabs.get("s_text"),
            align_map=CONF["text"]["align"].get("s_text", []),
            trim_page=CONF["text"]["trim"].get("s_text", [])).iter():
        if not upath or upath[0] not in TARGET_TOPS or uline is None:
            continue
        value = coverage.entry(jline) if jline is not None else None
        if value is None:
            state = "NO_JP_ENTRY_ALIGNED"
        elif "error" in value or not value.get("tokens"):
            state = ("JP_REPEAT_PAGE_MARKER" if len(tuple(jpath)) == 1
                     else "JP_EMPTY_RECORD")
        else:
            state = "HAS_JP"
        key = (upath[0], upath[-1])
        if status.get(key) == "HAS_JP":
            continue
        status[key] = state
    return status


def kanji_table(doc, jp):
    """Kanji -> JP glyph code, each revalidated against its JP attestation."""
    table = {}
    for char, row in doc["kanji_codes"].items():
        code = int(row["jp_glyph_code"], 16)
        if code < 0x122:
            raise RuntimeError(f"SSC_KANJI_CODE_NOT_IN_KANJI_SPAN {char}")
        path = row["attested_at"]
        if not path:
            raise RuntimeError(f"SSC_KANJI_ATTESTATION_MISSING {char}")
        if not any(kind == "CHR_FULL" and value == code
                   for kind, value in sysbat.jp_entry(jp, path)):
            raise RuntimeError(f"SSC_KANJI_ATTESTATION_FAILED {char} "
                               f"0x{code:04X} {path}")
        if char in table:
            raise RuntimeError(f"SSC_KANJI_DUPLICATE {char}")
        table[char] = code
    return table


def load_manifest(jp, us, decode):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = doc["entries"]
    if len(rows) != TOTAL or doc["count"] != TOTAL:
        raise RuntimeError(f"SSC_MANIFEST_COUNT {len(rows)}/{doc['count']}")
    if doc["baseline_production_sha256"] != BASELINE:
        raise RuntimeError("SSC_MANIFEST_BASELINE_DRIFT")
    if (doc["translated_count"] != TOTAL or doc["no_change_required_count"] != 0
            or doc["in_editorial_scope_count"] != IN_SCOPE
            or doc["scene_completion_count"] != SCENE_COMPLETION):
        raise RuntimeError("SSC_MANIFEST_STATUS_COUNTS")

    # the earlier s_text layers' top sets must stay disjoint from ours
    owned_elsewhere = set(stext.EXPECTED_TOPS) | set(recovered.TARGET_TOPS)
    if owned_elsewhere & set(TARGET_TOPS):
        raise RuntimeError("SSC_TOP_OWNED_BY_ANOTHER_LAYER")

    status = alignment_status(jp, us)
    root = us.tabs["s_text"]
    got = {}
    for row in rows:
        top, line = row["us_top"], row["us_line"]
        got.setdefault(top, []).append(line)
        if row["us_logical_path"] != f"s_text/{top}/{line}":
            raise RuntimeError(f"SSC_MANIFEST_PATH {row['us_logical_path']}")
        if row["status"] != "TRANSLATED_REVIEWED":
            raise RuntimeError(f"SSC_MANIFEST_STATUS {row['us_logical_path']}")
        if row["scope"] not in ("EDITORIAL_958", "SCENE_COMPLETION"):
            raise RuntimeError(f"SSC_MANIFEST_SCOPE {row['us_logical_path']}")
        if (row["scope"] == "SCENE_COMPLETION") != (line == 0):
            raise RuntimeError(f"SSC_SCOPE_LINE0_MISMATCH {row['us_logical_path']}")
        if not row["japanese"]:
            raise RuntimeError(f"SSC_MANIFEST_EMPTY {row['us_logical_path']}")
        page = root[top]
        if not isinstance(page, c_ffta_sect_text_page):
            raise RuntimeError(f"SSC_TARGET_NOT_A_LEAF {top}")
        if page.tsize != TOP_TSIZE[top]:
            raise RuntimeError(f"SSC_US_LEAF_TSIZE {top} {page.tsize}")
        if not _us_source.matches(row, visible(tokens_of(page[line]), decode)):
            raise RuntimeError(f"SSC_MANIFEST_ENGLISH_DRIFT {row['us_logical_path']}")
        state = status.get((top, line), "NO_JP_ENTRY_ALIGNED")
        if state == "HAS_JP":
            raise RuntimeError(f"SSC_TARGET_HAS_JP_ORIGINAL {row['us_logical_path']}")
        if line == 0 and state != "JP_REPEAT_PAGE_MARKER":
            raise RuntimeError(f"SSC_LINE0_REASON {row['us_logical_path']} {state}")
        row["_us_only_reason"] = state
    if {k: sorted(v) for k, v in got.items()} != \
            {t: list(range(TOP_TSIZE[t])) for t in TARGET_TOPS}:
        raise RuntimeError(f"SSC_MANIFEST_NOT_WHOLE_PAGES {sorted(got)}")
    return doc, rows


# --------------------------------------------------------------- encoding ---

def parse_markup(japanese, reverse, kanji):
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
            raise RuntimeError(f"SSC_UNENCODABLE {char!r} in {japanese!r}")
        i += 1
    return out


def line_widths(tokens, jp_raw):
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


def family_bounds(jp, jp_raw):
    """Width / lines-per-page bounds from the JP originals of the same family."""
    root = jp.tabs["s_text"]
    width = lines = 0
    for top in BOUND_TOPS:
        page = root[top]
        if not isinstance(page, c_ffta_sect_text_page):
            raise RuntimeError(f"SSC_BOUND_PAGE_NOT_A_LEAF {top}")
        for index in range(page.tsize):
            toks = tokens_of(page[index])
            if not toks:
                continue
            width = max([width] + line_widths(toks, jp_raw))
            lines = max([lines] + lines_per_page(toks))
    if not width or not lines:
        raise RuntimeError("SSC_BOUND_EMPTY")
    return {"reference": "JP s_text story pages 0..34",
            "max_line_px": width, "max_lines_per_page": lines}


def closing(tokens):
    out = []
    for kind, value in reversed(tokens):
        if kind == "CTR_FUNC" and value in CLOSE_CONTROLS:
            out.append(value)
        else:
            break
    return list(reversed(out))


def check_controls(path, original, translated):
    """Reference controls survive per-line as a multiset; word spaces do not."""
    def refs(tokens):
        return sorted(value for kind, value in tokens
                      if kind == "CTR_FUNC" and value not in LAYOUT_CONTROLS)
    us_refs, jp_refs = refs(original), refs(translated)
    if us_refs != jp_refs:
        raise RuntimeError(f"SSC_REFERENCES_CHANGED {path} "
                           f"us={[hex(v) for v in us_refs]} "
                           f"jp={[hex(v) for v in jp_refs]}")
    if any(k == "CTR_FUNC" and v == CTR_WORD_SPACE for k, v in translated):
        raise RuntimeError(f"SSC_WORD_SPACE_KEPT {path}")
    if sum(1 for k, _ in original if k == "CTR_EOS") != \
            sum(1 for k, _ in translated if k == "CTR_EOS"):
        raise RuntimeError(f"SSC_EOS_SEPARATOR_COUNT {path}")
    if closing(original) != closing(translated):
        raise RuntimeError(f"SSC_CLOSE_SEQUENCE {path}")
    if len(translated) < 2 or translated[-2:] != \
            [("CTR_FUNC", 0x40), ("CTR_FUNC", CTR_TERMINATOR)]:
        raise RuntimeError(f"SSC_TERMINATOR_MISSING {path}")
    return {"references_preserved": [f"0x{v:04X}" for v in us_refs],
            "close_sequence": [f"0x{v:04X}" for v in closing(translated)]}


def to_slots(tokens, alloc):
    out = []
    for kind, value in tokens:
        if kind == "CHR_FULL":
            if value not in alloc:
                raise RuntimeError(f"SSC_GLYPH_NOT_ALLOCATED 0x{value:04X}")
            out.append(("CHR_FULL", alloc[value]))
        else:
            out.append((kind, value))
    return out


def encode(row, reverse, kanji, alloc, jp_raw, bounds, original):
    path = row["us_logical_path"]
    jp_tokens = parse_markup(row["japanese"], reverse, kanji)
    control = check_controls(path, original, jp_tokens)
    widths = line_widths(jp_tokens, jp_raw)
    if max(widths) > bounds["max_line_px"]:
        raise RuntimeError(f"SSC_WIDTH_OVER_BOUND {path} "
                           f"{max(widths)}>{bounds['max_line_px']}")
    pages = lines_per_page(jp_tokens)
    if max(pages) > bounds["max_lines_per_page"]:
        raise RuntimeError(f"SSC_LINES_OVER_BOUND {path} {pages}")
    expected = to_slots(jp_tokens, alloc)
    data = stext.encode_standard(expected)
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"SSC_SERIALIZER_ROUNDTRIP_FAILED {path}")
    if data[-1] != 0:
        raise RuntimeError(f"SSC_EOS_MISSING {path}")
    return jp_tokens, expected, data, max(widths), len(pages), control


# ------------------------------------------------------------------ build ---

def product_root(data):
    """The s_text root offset of a product image, by its real pointer.

    The class parser (``c_ffta_sect_text``) cannot re-parse a product image:
    it guesses the root's entry count from "the lowest referenced offset is
    the end of the table", which stopped holding the moment the leaf-repoint
    layer relocated every leaf into the tail.  Readback therefore follows the
    pointers by hand -- which is also the more independent parse.
    """
    return int.from_bytes(
        data[S_TEXT_TABLE_POINTER:S_TEXT_TABLE_POINTER + 4], "little") \
        - 0x08000000


def product_leaf(data, root, top):
    """(leaf_offset, tsize) of one top, read off the image itself."""
    rel = int.from_bytes(data[root + top * 4:root + top * 4 + 4], "little")
    leaf = root + rel
    count = 0
    while True:
        value = int.from_bytes(data[leaf + count * 2:leaf + count * 2 + 2],
                               "little")
        if value == 0xFFFF:
            return rel, leaf, count
        count += 1
        if count > 4096:
            raise RuntimeError(f"SSC_PRODUCT_LEAF_UNTERMINATED {top}")


def product_line_tokens(data, leaf, offsets, index):
    start = leaf + offsets[index]
    stop = leaf + offsets[index + 1] if index + 1 < len(offsets) \
        else min(start + 0x8000, len(data))
    probe = c_ffta_sect_text_line(bytearray(data), start)
    probe.parse_size(stop - start, 2)
    probe.set_nondeterm()
    probe.parse()
    return list(probe.text.tokens)


def product_leaf_tokens(data, root, top, expected_tsize):
    rel, leaf, count = product_leaf(data, root, top)
    if count != expected_tsize:
        raise RuntimeError(f"SSC_PRODUCT_LEAF_TSIZE {top} {count}")
    offsets = [int.from_bytes(data[leaf + i * 2:leaf + i * 2 + 2], "little")
               for i in range(count)]
    return rel, [product_line_tokens(data, leaf, offsets, i)
                 for i in range(count)]


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"PRODUCTION_BASELINE_MISMATCH {sha(base)}")
    meta, alloc = previous[2], previous[3]
    prev_block_end = previous[11]
    jp, us = meta["jp"], meta["us"]
    jp_raw = JP.read_bytes()
    us_raw = US.read_bytes()

    decode, reverse = sysbat.charset_tables()
    doc, rows = load_manifest(jp, us, decode)
    kanji = kanji_table(doc, jp)
    bounds = family_bounds(jp, jp_raw)

    raw = bytearray(base)
    root = us.tabs["s_text"]
    if int.from_bytes(us_raw[S_TEXT_TABLE_POINTER:S_TEXT_TABLE_POINTER + 4],
                      "little") - 0x08000000 != root.real_offset:
        raise RuntimeError("SSC_ROOT_DECL_DRIFT")

    # leaf sharing safety: no other top may alias a target leaf
    offsets = {}
    for top in range(root.tsize):
        try:
            item = root[top]
        except Exception:
            continue
        off = getattr(item, "real_offset", None)
        if off is not None:
            offsets.setdefault(off, []).append(top)
    for top in TARGET_TOPS:
        sharers = [t for t in offsets.get(root[top].real_offset, []) if t != top]
        if sharers:
            raise RuntimeError(f"SSC_TARGET_LEAF_SHARED {top} {sharers}")

    records, replacements, expectations = [], {}, {}
    for row in rows:
        top, line = row["us_top"], row["us_line"]
        record = root[top][line]
        original = tokens_of(record)
        _tokens, expected, data, width, pages, control = encode(
            row, reverse, kanji, alloc, jp_raw, bounds, original)
        flags = record.U16(0) & ~COMPRESSION_BIT
        replacements.setdefault(top, {})[line] = \
            flags.to_bytes(2, "little") + data
        expectations.setdefault(top, {})[line] = expected
        records.append({
            "us_logical_path": row["us_logical_path"], "us_top": top,
            "us_line": line, "scene": row["scene"], "speaker": row["speaker"],
            "scope": row["scope"], "original_english_sha256": _us_source.digest(row),
            "japanese": row["japanese"], "status": row["status"],
            "us_only_reason": row["_us_only_reason"],
            "record_flags": f"0x{flags:04X}", "payload_length": len(data),
            "rendered_width_px": width, "pages": pages,
            "width_bound_px": bounds["max_line_px"],
            "eos": True, "roundtrip": "PASS", "controls": control,
            "_expected": expected, "_data": data})

    cursor = block_start = stext.align(prev_block_end, 4)
    leaf_records = []
    for top in TARGET_TOPS:
        page = root[top]
        if len(replacements[top]) != page.tsize:
            raise RuntimeError(f"SSC_LEAF_REPLACEMENT_COUNT {top}")
        blob = stext.serialize_leaf(us_raw, page, replacements[top])
        stext.validate_leaf(blob, us_raw, page, replacements[top],
                            expectations[top])
        field = root.real_offset + top * 4
        old_relative = int.from_bytes(raw[field:field + 4], "little")
        new_relative = cursor - root.real_offset
        raw[cursor:cursor + len(blob)] = blob
        raw[field:field + 4] = new_relative.to_bytes(4, "little")
        leaf_records.append({
            "us_top": top, "root_field_us_rom": f"0x{field:08X}",
            "old_relative": f"0x{old_relative:08X}",
            "new_relative": f"0x{new_relative:08X}",
            "previous_leaf_us_rom": f"0x{page.real_offset:08X}",
            "previous_size": page.sect_top,
            "new_leaf_us_rom": f"0x{cursor:08X}", "new_size": len(blob),
            "entries": page.tsize, "records_replaced": len(replacements[top]),
            "records_preserved": 0})
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("SSC_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("SSC_BLOCK_OUTSIDE_TAIL")
    return (bytes(raw), base, meta, alloc, records, doc, rows, leaf_records,
            expectations, replacements, block_start, block_end, kanji, bounds)


# --------------------------------------------------------------- validate ---

def validate(product, base, meta, alloc, records, rows, leaf_records,
             expectations, block_start, block_end, decode, doc):
    us = meta["us"]
    root = us.tabs["s_text"]
    audits = {}

    # -- independent ROM readback ------------------------------------------
    if sha(OUTROM) != sha(product):
        raise RuntimeError("SSC_READBACK_ROM_MISMATCH")
    written = Path(OUTROM).read_bytes()
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

    check_root = product_root(written)
    live_root = product_root(base)
    if check_root != live_root:
        raise RuntimeError("SSC_READBACK_ROOT_OFFSET")
    leaf_tokens, leaf_relatives = {}, {}
    for top in TARGET_TOPS:
        rel, tokens = product_leaf_tokens(written, check_root, top,
                                          TOP_TSIZE[top])
        expected_relative = next(int(x["new_relative"], 16)
                                 for x in leaf_records if x["us_top"] == top)
        if rel != expected_relative:
            raise RuntimeError(f"SSC_READBACK_ROOT_POINTER {top}")
        leaf_tokens[top], leaf_relatives[top] = tokens, rel
    readback = []
    for row, record in zip(rows, records):
        top, line = row["us_top"], row["us_line"]
        tokens = leaf_tokens[top][line]
        got = render(tokens)
        if got != row["japanese"]:
            raise RuntimeError(f"SSC_READBACK_TEXT_MISMATCH "
                               f"{row['us_logical_path']} {got!r}")
        if tokens != record["_expected"]:
            raise RuntimeError(f"SSC_READBACK_TOKEN_MISMATCH "
                               f"{row['us_logical_path']}")
        readback.append({"us_logical_path": row["us_logical_path"],
                         "root_relative": f"0x{leaf_relatives[top]:08X}",
                         "decoded": got, "result": "PASS"})
    audits["readback"] = {"entries": len(readback), "failures": 0,
                          "source": "independent hand parse of the written ROM "
                                    "file: 0x9A88 pointer -> root -> relative "
                                    "field -> leaf offset table -> "
                                    "c_ffta_sect_text_line per record",
                          "result": "PASS", "rows": readback}

    # -- sibling / alias ----------------------------------------------------
    # the product image must carry exactly the expected tokens in the five
    # rebuilt leaves; every other top's root field is byte-identical, and the
    # binary-touch audit below proves every byte outside the five fields and
    # the new tail block unchanged, which covers every sibling leaf body.
    for top in TARGET_TOPS:
        _rel, tokens = product_leaf_tokens(product, live_root, top,
                                           TOP_TSIZE[top])
        for index in range(TOP_TSIZE[top]):
            if tokens[index] != expectations[top][index]:
                raise RuntimeError(f"SSC_LEAF_TOKEN_MISMATCH {top}/{index}")
    root_tsize = 63
    moved = [t for t in range(root_tsize) if t not in TARGET_TOPS
             and product[live_root + t * 4:live_root + t * 4 + 4]
             != base[live_root + t * 4:live_root + t * 4 + 4]]
    if moved:
        raise RuntimeError(f"SSC_SIBLING_ROOT_MOVED {moved}")
    relatives = [int.from_bytes(product[live_root + t * 4:
                                        live_root + t * 4 + 4], "little")
                 for t in range(root_tsize)]
    ours = [relatives[t] for t in TARGET_TOPS]
    if len(set(ours)) != len(ours):
        raise RuntimeError("SSC_POINTER_COLLISION")
    foreign = {relatives[t] for t in range(root_tsize) if t not in TARGET_TOPS}
    if set(ours) & foreign:
        raise RuntimeError("SSC_ALIAS_PROPAGATION")
    for record in leaf_records:
        lo = int(record["new_relative"], 16)
        hi = lo + record["new_size"]
        if any(lo <= relatives[t] < hi for t in range(root_tsize)
               if t not in TARGET_TOPS):
            raise RuntimeError(f"SSC_ALIAS_HAZARD {record['us_top']}")
    audits["sibling"] = {
        "target_leaves_rebuilt": len(TARGET_TOPS),
        "non_target_root_fields_changed": 0,
        "non_target_leaf_bodies": "byte-identical by the binary-touch audit "
                                  "(no byte outside the five root fields and "
                                  "the new tail block changed)",
        "unintended_record_changes": 0, "pointer_collisions": 0,
        "alias_propagation": 0, "result": "PASS"}

    # -- other production families untouched --------------------------------
    fx_span = (prev.FX_ROOT_FIELD, prev.FX_ROOT_FIELD + prev.FX_TSIZE * 4)
    if product[fx_span[0]:fx_span[1]] != base[fx_span[0]:fx_span[1]]:
        raise RuntimeError("SSC_FX_TEXT_ROOT_DISTURBED")
    families = {}
    for name, pool in us.tabs["words"].items():
        span = pool.real_offset, pool.real_offset + pool.tsize * 4
        if product[span[0]:span[1]] != base[span[0]:span[1]]:
            raise RuntimeError(f"SSC_WORDS_FAMILY_DISTURBED words:{name}")
        families[f"words:{name}"] = pool.tsize
    if product[sysbat.PAGES_ROOT_FIELD:sysbat.PAGES_ROOT_FIELD + 4] != \
       base[sysbat.PAGES_ROOT_FIELD:sysbat.PAGES_ROOT_FIELD + 4]:
        raise RuntimeError("SSC_PAGES_BATTLE_DISTURBED")
    audits["other_families"] = {"fx_text_root_fields_unchanged": prev.FX_TSIZE,
                                "words_root_tables_unchanged": len(families),
                                "pages_battle_root_unchanged": True,
                                "result": "PASS"}

    # -- glyph / asset ------------------------------------------------------
    font = us.tabs["font"]
    fbase = font.real_offset
    if product[fbase:fbase + font.tsize * stext.FONT_STRIDE] != \
       base[fbase:fbase + font.tsize * stext.FONT_STRIDE]:
        raise RuntimeError("SSC_FONT_TABLE_CHANGED")
    if product[stext.US_METADATA:stext.US_METADATA + font.tsize] != \
       base[stext.US_METADATA:stext.US_METADATA + font.tsize]:
        raise RuntimeError("SSC_FONT_METADATA_CHANGED")
    used = sorted({v for r in records for k, v in r["_expected"]
                   if k == "CHR_FULL"})
    allocated = set(alloc.values())
    stray = [v for v in used if v not in allocated]
    if stray:
        raise RuntimeError(f"SSC_SLOT_NOT_FROM_JP_ALLOCATION {stray[:8]}")
    jp_raw = JP.read_bytes()
    for slot in used:
        code = inverse[slot]
        joff = meta["jp"].tabs["font"].real_offset + code * stext.FONT_STRIDE
        uoff = fbase + slot * stext.FONT_STRIDE
        if product[uoff:uoff + stext.FONT_STRIDE] != \
                jp_raw[joff:joff + stext.FONT_STRIDE]:
            raise RuntimeError(f"SSC_SLOT_NOT_JP_IDENTICAL 0x{slot:04X}")
    audits["glyph"] = {"new_glyph_records": 0, "font_table_writes": 0,
                       "metadata_writes": 0, "low_slot_overwrites": 0,
                       "existing_mappings_changed": 0,
                       "distinct_slots_used": len(used),
                       "kanji_slots_used": sum(1 for s in used
                                               if inverse[s] >= 0x122),
                       "all_slots_are_native_jp_records": True,
                       "last_allocated_slot": f"0x{max(allocated):04X}",
                       "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    permitted = [(root.real_offset + top * 4, root.real_offset + top * 4 + 4)
                 for top in TARGET_TOPS]
    permitted.append((block_start, block_end))
    permitted.sort()
    merged = []
    for lo, hi in permitted:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    changed = list(stext.changed_ranges(base, product))
    unexplained = [(f"0x{lo:08X}", f"0x{hi:08X}") for lo, hi in changed
                   if not any(a <= lo and hi <= b for a, b in merged)]
    if unexplained:
        raise RuntimeError(f"SSC_BINARY_TOUCH_REGRESSION {unexplained[:8]}")
    fields = {root.real_offset + top * 4 for top in TARGET_TOPS}
    code_ranges = [c for c in changed if c[0] < 0x400000
                   and not any(f <= c[0] and c[1] <= f + 4 for f in fields)]
    if code_ranges:
        raise RuntimeError(f"SSC_CODE_TOUCHED {code_ranges[:4]}")
    audits["binary"] = {
        "result": "PASS", "changed_ranges": len(changed),
        "root_pointer_fields": len(fields), "relocated_payload_blocks": 1,
        "new_font_slots": 0, "unexplained_ranges": 0,
        "rom_executable_code_bytes_changed": 0,
        "block": {"start": f"0x{block_start:08X}", "end": f"0x{block_end:08X}",
                  "bytes": block_end - block_start,
                  "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY
                                    - block_end}}
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
        raise RuntimeError("SSC_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[10], second[11]) or leaf_records != second[7]:
        raise RuntimeError("SSC_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    decode, _reverse = sysbat.charset_tables()
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
                  "in_editorial_958_scope": sum(
                      1 for r in records if r["scope"] == "EDITORIAL_958"),
                  "scene_completion_line0": sum(
                      1 for r in records if r["scope"] == "SCENE_COMPLETION"),
                  "tops": {str(x["us_top"]): x["records_replaced"]
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
            "class_5_editorial_produced_before": 806,
            "class_5_translated_by_this_layer_in_958_scope": sum(
                1 for r in records if r["scope"] == "EDITORIAL_958"),
            "class_5_scene_completion_outside_958_scope": sum(
                1 for r in records if r["scope"] == "SCENE_COMPLETION"),
            "note": "Recompute the exact remainder with "
                    "ffta_jp_us_only_editorial_scope.py -- never by arithmetic."},
    }
    write(out / "translation_manifest_echo.json",
          {"entries": strip(records), "kanji_codes": doc["kanji_codes"]})
    write(out / "production_readback.json", audits["readback"])
    write(out / "sibling_alias_audit.json", audits["sibling"])
    write(out / "other_families_audit.json", audits["other_families"])
    write(out / "glyph_audit.json", audits["glyph"])
    write(out / "binary_touch.json", audits["binary"])
    write(out / "determinism.json", determinism)
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
