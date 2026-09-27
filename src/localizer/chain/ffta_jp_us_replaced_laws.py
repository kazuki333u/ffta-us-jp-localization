#! python3
# -*- coding: utf-8 -*-
"""The six laws US retail replaced, named for what the US engine forbids.

The defect
----------

Law names live at ``words:content/(625 + law_id)``, and the chain transfers JP
``words:content`` into the US image **by index**.  That is correct for 79 of
the 85 laws, because both images hold the same prohibition at the same law id.
For six of them it is not: US retail kept the id and replaced the *rule*, so
the faithfully transferred JP name describes a law the US engine never
enforces.  Law 51 is the harm in one line -- the player reads a damage cap and
is carded for casting dark magic.

An identity oracle cannot see this.  The record really is the JP record for
that index; nothing in the text is a placeholder, nothing is unaligned.  Only
the two **law tables** can decide it, and before this layer only the US one had been
read:

* ``US 0x08528F34`` and ``JP 0x084FED94``, 16 bytes per law, ``+4`` the law
  type and ``+5`` its value.  The JP table was found by scanning every 4-byte
  aligned offset of the JP image for a 16-byte-stride window whose ``+4/+5``
  sequence matches US laws 0..50: an exact match finds nothing, allowing six
  mismatches leaves exactly one candidate.
* Comparing all 85 laws, exactly six ``(type, value)`` pairs differ: **25, 51,
  52, 53, 54, 55**.  An earlier analysis had listed 51..60 -- it over-counted (56..60 agree in
  both images, so the Japanese there is JP retail's own wording for the very
  rule the US engine runs, which is what a JP localization should print) and
  under-counted (law 25: US forbids wind alone, JP forbids four elements).

Why the names are authored, not borrowed
----------------------------------------

None of the six US prohibitions exists anywhere in the JP law table, so there
is no JP retail name to reuse -- not even at another id.  The six names are
project-authored Japanese written in the family's own register, and every
kanji in them is a glyph JP retail already draws, attested at a JP record that
uses that glyph code.  **Zero new glyphs.**

What this layer does
--------------------

Nothing here is trusted.  The manifest ``data/us_replaced_laws_translations.json``
is the editorial decision of record; every claim it makes is re-proved on every
build, from the pristine ROMs:

* both law tables are read again and the divergent set must be exactly the
  manifest's six -- if a future layer, or a corrected reading, changes that
  set, this build fails rather than shipping a stale fix list;
* each row's ``us_law`` / ``jp_law`` pair must be what the two tables say;
* the US record still reads the English the manifest was authored against, and
  the base image still ships exactly the JP record for that index put through
  the chain's glyph allocation -- so the bytes being replaced really are the
  mis-transfer;
* every glyph is already allocated, every line fits the JP law-name family's
  own measured bound, and the 79 other law names come out byte-identical.

Mechanism: ``words:content`` is direct-addressed -- one 4-byte root field per
record, repointed at a new payload in the free tail.  **Zero executable bytes,
zero graphics bytes, zero font or metadata writes.**

Layer position: a new terminal layer above ``ffta_jp_fixed_starting_names``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_fixed_starting_names as prev
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_added_missions as uam
import ffta_jp_us_only_fx_text as fxt
import ffta_jp_us_only_system_battle as sysbat
import ffta_placeholder_transfer_audit as pta
from ffta_sect import (c_ffta_sect_rom, c_ffta_sect_text_buf,       # noqa: E402
                       _trim_raw_len, _words_sect_info)
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_replaced_laws_translations.json"
RUN_BASE = HERE / "build/us_replaced_laws"
OUTROM = ROOT / "rom/build/ffta_us_jp_us_replaced_laws.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_us_replaced_laws_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_fixed_starting_names.
BASELINE = "E905AF2E3A4D926341D4713BF23DCC2F85E508B1F41969EF8F03FF680AE1C8CE"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "EC5C9717A25078B85C8A510EB897C376191619277EE0179949D6B3E9471C49AD"

US_CONTENT_ROOT = (0x18DA4, 0x2F2)     # ffta_sect.load_rom_us words:content
LAW_TABLE_US = 0x00528F34              # US 0x08528F34
LAW_TABLE_JP = 0x004FED94              # JP 0x084FED94
LAW_STRIDE = 16
LAW_TYPE_OFF, LAW_VALUE_OFF = 4, 5
LAW_COUNT = 85
NAME_BASE = 625                        # words:content/(625 + law_id)
DIVERGENT = (25, 51, 52, 53, 54, 55)
# the retraction, re-proved here: 56..60 hold the same rule in both images.
RETRACTED = (56, 57, 58, 59, 60)
# The JP law-name family's own widest name, re-measured every build.
FAMILY_BOUND_PX = 77


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


def law_pairs(raw, table):
    """(type, value) for every law in one image's law table."""
    return [(raw[table + i * LAW_STRIDE + LAW_TYPE_OFF],
             raw[table + i * LAW_STRIDE + LAW_VALUE_OFF])
            for i in range(LAW_COUNT)]


def divergent_laws(us_raw, jp_raw):
    """The law ids whose prohibition differs between the two retail images."""
    us_laws, jp_laws = law_pairs(us_raw, LAW_TABLE_US), law_pairs(jp_raw, LAW_TABLE_JP)
    return us_laws, jp_laws, tuple(i for i in range(LAW_COUNT)
                                   if us_laws[i] != jp_laws[i])


def render(tokens, names):
    return "".join(names.get(v, "<%04X>" % v) if k.startswith("CHR")
                   else "{%X}" % v for k, v in tokens)


def load_manifest(doc, jp, us, us_raw, jp_raw, decode, jp_names):
    """Re-prove every claim the manifest makes, from the ROMs themselves."""
    rows = doc["rows"]
    if len(rows) != doc["count"] or len(rows) != len(DIVERGENT):
        raise RuntimeError(f"URL_MANIFEST_COUNT {len(rows)}/{doc['count']}")
    if doc.get("family") != "words:content":
        raise RuntimeError("URL_MANIFEST_FAMILY")
    if doc.get("law_name_base") != NAME_BASE:
        raise RuntimeError("URL_MANIFEST_NAME_BASE")
    tables = doc["law_tables"]
    if (int(tables["us_rom"], 16) != ROM + LAW_TABLE_US
            or int(tables["jp_rom"], 16) != ROM + LAW_TABLE_JP
            or tables["stride"] != LAW_STRIDE
            or tables["type_field_offset"] != LAW_TYPE_OFF
            or tables["value_field_offset"] != LAW_VALUE_OFF
            or tables["laws"] != LAW_COUNT):
        raise RuntimeError("URL_MANIFEST_TABLE_DECL_DRIFT")

    # -- the fix list is decided by the ROMs, not by the manifest -----------
    us_laws, jp_laws, divergent = divergent_laws(us_raw, jp_raw)
    if divergent != DIVERGENT:
        raise RuntimeError("URL_DIVERGENT_SET_DRIFT %s" % (divergent,))
    if tuple(r["law_id"] for r in rows) != DIVERGENT:
        raise RuntimeError("URL_MANIFEST_LAW_IDS %s" % [r["law_id"] for r in rows])
    # the retraction, re-proved: these five are *not* transfer defects.
    for law in RETRACTED:
        if us_laws[law] != jp_laws[law]:
            raise RuntimeError(f"URL_RETRACTION_BROKEN law {law}")

    content = us.tabs["words"]["content"]
    jp_content = jp.tabs["words"]["content"]
    for row in rows:
        law, index = row["law_id"], row["us_index"]
        path = row["us_logical_path"]
        if index != NAME_BASE + law or path != f"words:content/{index}":
            raise RuntimeError(f"URL_MANIFEST_PATH {path}")
        if (row["us_law"]["type"], row["us_law"]["value"]) != us_laws[law]:
            raise RuntimeError(f"URL_MANIFEST_US_LAW law {law} {us_laws[law]}")
        if (row["jp_law"]["type"], row["jp_law"]["value"]) != jp_laws[law]:
            raise RuntimeError(f"URL_MANIFEST_JP_LAW law {law} {jp_laws[law]}")
        if not row.get("japanese"):
            raise RuntimeError(f"URL_MANIFEST_EMPTY {path}")
        original = tokens_of(content[index])
        if not _us_source.matches(row, fxt.visible(original, decode)):
            raise RuntimeError(f"URL_MANIFEST_ENGLISH_DRIFT {path} "
                               f"{fxt.visible(original, decode)!r}")
        jp_tokens = tokens_of(jp_content[index])
        if not _us_source.jp_matches(row, pta._clean(render(jp_tokens, jp_names))):
            raise RuntimeError(f"URL_MANIFEST_JP_RECORD_DRIFT {path} "
                               f"{pta._clean(render(jp_tokens, jp_names))!r}")
        row["_us_text"] = fxt.visible(original, decode)
        row["_original"] = original
        row["_jp"] = jp_tokens
    return rows, us_laws, jp_laws


def check_target(row, alloc, base_tokens, jp_names):
    """Prove the bytes being replaced are the mis-transfer, not something else.

    The JP record here is real shipped Japanese, not a developer placeholder --
    that is exactly why earlier builds' audits cannot see this class -- and the
    base image must still ship it, put through the chain's glyph allocation.
    """
    path = row["us_logical_path"]
    jp_tokens = row["_jp"]
    if not any(k.startswith("CHR") for k, _ in jp_tokens):
        raise RuntimeError(f"URL_JP_RECORD_EMPTY {path}")
    if pta.JP_PLACEHOLDER.match(pta._clean(render(jp_tokens, jp_names))):
        raise RuntimeError(f"URL_JP_IS_PLACEHOLDER {path}")
    if pta.US_PLACEHOLDER.match(pta._clean(row["_us_text"])):
        raise RuntimeError(f"URL_US_SIDE_IS_PLACEHOLDER {path}")
    if base_tokens != uam.to_slots(jp_tokens, alloc):
        raise RuntimeError(f"URL_BASE_IS_NOT_THE_JP_TRANSFER {path}")
    return "US_REPLACED_THE_RULE_AT_THIS_LAW_ID"


def family_bound(jp, jp_raw):
    """The widest name in JP retail's own law-name family."""
    content = jp.tabs["words"]["content"]
    widths = [(max(fxt.line_widths(tokens_of(content[NAME_BASE + i]), jp_raw)), i)
              for i in range(LAW_COUNT)]
    return max(widths)


def encode(row, reverse, kanji, alloc, jp_raw, bound):
    path = row["us_logical_path"]
    tokens = uam.parse_markup(row["japanese"], reverse, kanji)
    if any(k == "CTR_FUNC" for k, _ in tokens):
        raise RuntimeError(f"URL_WORDS_CONTROL {path}")
    if any(k == "CTR_EOS" for k, _ in tokens):
        raise RuntimeError(f"URL_EOS_SEPARATOR {path}")
    widths = fxt.line_widths(tokens, jp_raw)
    lines = fxt.lines_per_page(tokens)
    if max(widths) > bound:
        raise RuntimeError(f"URL_WIDTH_OVER_FAMILY_BOUND {path} {max(widths)}>{bound}")
    if row.get("line_widths_px") != widths or row.get("lines") != lines[0]:
        raise RuntimeError(f"URL_MANIFEST_WIDTH_DRIFT {path} {widths} {lines}")
    if lines[0] != 1:
        raise RuntimeError(f"URL_NAME_IS_MULTILINE {path}")
    expected = uam.to_slots(tokens, alloc)
    data = stext.encode_standard(expected)
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"URL_SERIALIZER_ROUNDTRIP_FAILED {path}")
    if data[-1] != 0:
        raise RuntimeError(f"URL_EOS_MISSING {path}")
    return tokens, expected, data, widths


def build():
    base = prev.build()[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"URL_BASELINE_MISMATCH {sha(base)}")
    alloc, meta = uam.glyph_allocation()
    jp, us = meta["jp"], meta["us"]
    jp_raw, us_raw = JP.read_bytes(), US.read_bytes()
    decode, reverse = sysbat.charset_tables()
    jp_names = pta.glyphs.charset()

    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows, us_laws, jp_laws = load_manifest(doc, jp, us, us_raw, jp_raw, decode, jp_names)
    kanji = uam.kanji_table(doc, jp)
    bound_px, bound_law = family_bound(jp, jp_raw)
    if bound_px != FAMILY_BOUND_PX:
        raise RuntimeError(f"URL_FAMILY_BOUND_DRIFT {bound_px}")

    raw = bytearray(base)
    snapshot = bytes(raw)
    base_words = us_words(snapshot)["content"]
    if base_words.tsize != US_CONTENT_ROOT[1]:
        raise RuntimeError("URL_WORDS_ROOT_DECL_DRIFT")

    block_start = uam.free_block_start(base, stext.TAIL_START)
    cursor = block_start
    records = []
    for row in rows:
        index = row["us_index"]
        base_tokens = tokens_of(base_words[index])
        reason = check_target(row, alloc, base_tokens, jp_names)
        _tokens, expected, data, widths = encode(row, reverse, kanji, alloc,
                                                 jp_raw, bound_px)
        field = base_words.real_offset + index * 4
        old = u32(raw, field)
        raw[cursor:cursor + len(data)] = data
        struct.pack_into("<I", raw, field, ROM + cursor)
        records.append({
            "law_id": row["law_id"], "us_logical_path": row["us_logical_path"],
            "family": "words:content", "us_index": index,
            "original_english_sha256": _us_source.digest(row),
            "us_law": row["us_law"], "jp_law": row["jp_law"],
            "jp_record_now_sha256": _us_source.jp_digest(row), "japanese": row["japanese"],
            "us_only_reason": reason, "route": "WORDS_DIRECT_REPOINT",
            "rendered_width_px": max(widths), "line_widths_px": widths,
            "width_bound_px": bound_px,
            "width_bound_source": f"JP words:content/{NAME_BASE + bound_law} "
                                  f"(law {bound_law})",
            "root_pointer_field_us_rom": f"0x{field:08X}",
            "original_cpu_pointer": f"0x{old:08X}",
            "new_cpu_pointer": f"0x{ROM + cursor:08X}",
            "payload_offset_us_rom": f"0x{cursor:08X}",
            "payload_length": len(data), "eos": True, "roundtrip": "PASS",
            "_expected": expected, "_data": data})
        cursor = stext.align(cursor + len(data), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("URL_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("URL_BLOCK_OUTSIDE_TAIL")
    if set(base[block_start:block_end]) != {0xFF} or \
            set(us_raw[block_start:block_end]) != {0xFF}:
        raise RuntimeError("URL_BLOCK_NOT_FREE")
    # Nothing already in the image may reach into the new block.  Words inside
    # the tail are payload the chain wrote (text bytes, and the u16 offset
    # arrays of relocated leaves, whose adjacent halves regularly read as a
    # tail address); a word *below* the tail is refused unless pristine US
    # already had that value.
    coincidences = []
    for off in range(0, len(base) - 3, 4):
        value = u32(base, off)
        if not (ROM + block_start <= value < ROM + block_end):
            continue
        if off < stext.TAIL_START and value != u32(us_raw, off):
            raise RuntimeError("URL_BLOCK_REFERENCED 0x%08X" % (ROM + off))
        if value == ROM + block_start:
            raise RuntimeError("URL_BLOCK_START_ALREADY_REFERENCED 0x%08X" % (ROM + off))
        coincidences.append("0x%08X=0x%08X" % (ROM + off, value))

    layout = {"block_start": block_start, "block_end": block_end,
              "words_resembling_block_addresses": coincidences}
    law_tables = {
        "us_rom": f"0x{ROM + LAW_TABLE_US:08X}", "jp_rom": f"0x{ROM + LAW_TABLE_JP:08X}",
        "laws_compared": LAW_COUNT,
        "divergent": list(DIVERGENT),
        "retraction_reproved": {str(law): {"us": list(us_laws[law]),
                                           "jp": list(jp_laws[law])}
                                for law in RETRACTED},
        "us": {str(law): list(us_laws[law]) for law in DIVERGENT},
        "jp": {str(law): list(jp_laws[law]) for law in DIVERGENT}}
    return (bytes(raw), base, meta, alloc, records, doc, rows, layout, kanji,
            law_tables, (bound_px, bound_law))


def validate(product, base, meta, alloc, records, rows, layout, doc, law_tables,
             bound):
    jp_raw = JP.read_bytes()
    block_start, block_end = layout["block_start"], layout["block_end"]
    audits = {"law_tables": law_tables}

    # -- nothing outside the new block and the six repointed root fields ----
    fields = [int(r["root_pointer_field_us_rom"], 16) for r in records]
    diff = {i for i in range(len(base)) if product[i] != base[i]}
    allowed = set(range(block_start, block_end))
    for field in fields:
        allowed |= set(range(field, field + 4))
    if diff - allowed:
        raise RuntimeError("URL_UNEXPLAINED_BYTES %s"
                           % sorted(hex(d) for d in diff - allowed)[:8])
    audits["binary_touch"] = {
        "bytes_changed": len(diff),
        "new_block_bytes": block_end - block_start,
        "root_fields_repointed": [f"0x{f:08X}" for f in fields],
        "executable_bytes_changed": 0,
        "graphics_bytes_changed": 0,
        "font_bytes_changed": 0,
    }
    if product[stext.US_METADATA:stext.US_METADATA + 0xC67] != \
            base[stext.US_METADATA:stext.US_METADATA + 0xC67]:
        raise RuntimeError("URL_FONT_METADATA_TOUCHED")

    # -- independent ROM readback -------------------------------------------
    if sha(OUTROM) != sha(product):
        raise RuntimeError("URL_READBACK_ROM_MISMATCH")
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

    words_check = us_words(disk)["content"]
    readback = []
    for rec in records:
        tokens = tokens_of(words_check[rec["us_index"]])
        if tokens != rec["_expected"]:
            raise RuntimeError(f"URL_READBACK_TOKEN_MISMATCH {rec['us_logical_path']}")
        got = "".join(char(v) if k.startswith("CHR") else "{%X}" % v
                      for k, v in tokens)
        if got != rec["japanese"]:
            raise RuntimeError(f"URL_READBACK_TEXT_MISMATCH "
                               f"{rec['us_logical_path']} {got!r}")
        if u32(disk, int(rec["root_pointer_field_us_rom"], 16)) != \
                int(rec["new_cpu_pointer"], 16):
            raise RuntimeError(f"URL_READBACK_ROOT {rec['us_logical_path']}")
        readback.append({"us_logical_path": rec["us_logical_path"],
                         "law_id": rec["law_id"], "decoded": got, "result": "PASS"})
    audits["readback"] = {"entries": len(readback), "failures": 0,
                          "source": "independent parse of the written ROM file, "
                                    "following its own words root pointer",
                          "result": "PASS", "rows": readback}

    # -- the mis-transferred JP text is gone from all six -------------------
    by_path = {r["us_logical_path"]: r for r in rows}
    for rec in records:
        jp_slots = uam.to_slots(by_path[rec["us_logical_path"]]["_jp"], alloc)
        if tokens_of(words_check[rec["us_index"]]) == jp_slots:
            raise RuntimeError(f"URL_STILL_JP_SAME_SLOT {rec['us_logical_path']}")
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
                raise RuntimeError("URL_SLOT_NOT_ALLOCATED 0x%04X" % slot)
            if pr_font.get(slot) != jp_font.get(code):
                raise RuntimeError("URL_GLYPH_PIXELS 0x%04X->0x%04X" % (code, slot))
            if product[stext.US_METADATA + slot] != jp_raw[stext.JP_METADATA + code]:
                raise RuntimeError("URL_GLYPH_WIDTH 0x%04X->0x%04X" % (code, slot))
            checked += 1
    audits["glyphs"] = {"tokens_checked": checked, "new_glyph_records": 0,
                        "kanji_attested": sorted(doc["kanji_codes"]),
                        "result": "PASS"}

    # -- the other 79 law names are untouched -------------------------------
    pool_before = us_words(base)["content"]
    owned = {r["us_index"] for r in records}
    unchanged_laws = 0
    for law in range(LAW_COUNT):
        index = NAME_BASE + law
        if index in owned:
            continue
        if tokens_of(pool_before[index]) != tokens_of(words_check[index]):
            raise RuntimeError(f"URL_OTHER_LAW_NAME_CHANGED law {law}")
        unchanged_laws += 1
    audits["law_names"] = {"laws": LAW_COUNT, "replaced": sorted(owned),
                           "unchanged": unchanged_laws,
                           "retracted_left_as_jp_retail": list(RETRACTED),
                           "result": "PASS"}

    # -- siblings: no other words:content root moved, no aliasing -----------
    moved = [i for i in range(words_check.tsize) if i not in owned
             and product[words_check.real_offset + i * 4:words_check.real_offset + i * 4 + 4]
             != base[pool_before.real_offset + i * 4:pool_before.real_offset + i * 4 + 4]]
    if moved:
        raise RuntimeError(f"URL_SIBLING_ROOT_MOVED {moved[:8]}")
    pointers = [u32(product, words_check.real_offset + i * 4)
                for i in range(words_check.tsize)]
    ours = {p for i, p in enumerate(pointers) if i in owned}
    surviving = {p for i, p in enumerate(pointers) if i not in owned}
    if len(ours) != len(owned):
        raise RuntimeError("URL_PAYLOAD_ALIAS")
    if ours & surviving:
        raise RuntimeError("URL_ALIAS_PROPAGATION")
    if any(ROM + block_start <= p < ROM + block_end for p in surviving):
        raise RuntimeError("URL_ALIAS_HAZARD")
    unchanged = 0
    for i in range(words_check.tsize):
        if i in owned:
            continue
        if tokens_of(pool_before[i]) != tokens_of(words_check[i]):
            raise RuntimeError(f"URL_SIBLING_TEXT_CHANGED words:content/{i}")
        unchanged += 1
    audits["siblings"] = {"records_unchanged": unchanged, "result": "PASS"}

    # -- the whole tracked text corpus is otherwise identical to the baseline
    audits["corpus"] = corpus_identity(base, product, records)
    audits["width_bound"] = {
        "bound_px": bound[0],
        "source": f"JP words:content/{NAME_BASE + bound[1]} (law {bound[1]})",
        "widest_new_name_px": max(r["rendered_width_px"] for r in records),
        "result": "PASS"}
    audits["patch"] = {"block": [f"0x{ROM + block_start:08X}",
                                 f"0x{ROM + block_end:08X}"], "layout": layout}
    return audits


def corpus_identity(base, product, records):
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
        b.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
    expected = sorted(r["us_logical_path"] for r in records)
    if sorted(changed) != expected:
        raise RuntimeError("URL_CORPUS_DRIFT %s" % changed[:8])
    return {"records_compared": total, "records_changed": sorted(changed),
            "result": "PASS"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260922_production")
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
        raise RuntimeError("URL_BUILD_NONDETERMINISTIC")
    (product, base, meta, alloc, records, doc, rows, layout, _kanji, law_tables,
     bound) = first
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("URL_DISK_READBACK_MISMATCH")
    audits = validate(product, base, meta, alloc, records, rows, layout, doc,
                      law_tables, bound)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "milestone": "laws US retail replaced -- six names",
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "evidence": doc["evidence"],
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
