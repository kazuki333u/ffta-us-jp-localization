#! python3
"""Editorial localization of s_text 61/16 -- the six US-only judge missions of
the top-61 mission-battle dialogue table.

Sixth *class 5* milestone, third s_text editorial batch, and the terminal
production layer.  It completes the top-61 milestone whose other half is a
**correspondence correction**, not a translation: ``CONF['text']['align']
['s_text']`` now anchors every JP 15-line mission block onto its US 24-line
block (JP 61/s/15b+k = US 61/s/24b+k), so sub-pages 2..12 and 25 -- the 409
lines the editorial scope tool had reported as "US-only" plus the ~600 lines
blocks 1..5 had received from the wrong JP slot -- are produced by source
transfer in layer 1 like the rest of the table.

Sub-page 16 has no JP counterpart at all (JP 61/16 is 150 empty ``[40][42]``
records), so its 144 English lines are translated here: 54 lines with no
aligned JP entry (the visible editorial scope) and 90 lines the index-based
transfer had overwritten with an empty JP record (``EMPTY_JP_RECORD_TRANSFER``).

The translations are NOT computed here.  They are read from the tracked
manifest ``data/us_only_s_text_top61_judge_missions.json`` and revalidated
against both ROMs on every build.  Mechanism: the sub-leaf is recomposed from
the *product* image (the leaf-repoint layer already relocated the whole top-61
bundle into the tail), every non-target record preserved byte-for-byte, the
new sub-leaf is tail-relocated after the judge/Ezel block, and the one child
pointer field inside the relocated bundle is rewritten.  The root field of top
61 and the other 25 child pointers do not move.

Consequences: **zero new glyph records, zero font or metadata writes, zero ROM
code bytes patched.**  One child pointer field and one relocated tail block.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_coverage_audit as coverage
from ffta_modifier import CONF, c_tab_align_iter
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_only_system_battle as sysbat
import ffta_jp_us_only_fx_text as fxed
import ffta_jp_us_only_s_text_judge_ezel as prev
from ffta_sect import c_ffta_sect_text_line, c_ffta_sect_text_sub
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_only_s_text_top61_judge_missions.json"
RUN_BASE = HERE / "build/us_only_s_text_localization"
OUTROM = ROOT / "rom/build/ffta_us_jp_s_text_top61.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_s_text_top61_repeat.gba"

# Output of ffta_jp_us_only_s_text_judge_ezel on the corrected chain.  That
# module's EXPECTED_PRODUCTION is now a private per-layer drift gate.
BASELINE = "A6C533D06EE9EB237860F410A1B0FDF949257F0B9CF5EC3CDD3B5F1D055CBE9D"
# Terminal artifact of the production chain: the single canonical final-SHA
# authority.
EXPECTED_PRODUCTION = "0934DDA961C206CAAC64E1B2F80385734240A7BB4B4C55050EADC0B2BA7B5C96"

TOP, SUB, SUB_TSIZE, CHILDREN = 61, 16, 240, 26
TOTAL, IN_SCOPE, EMPTY_JP = 144, 54, 90
BLOCK_US, BLOCK_JP = 24, 15
COMPRESSION_BIT = 0x0002
sha = prev.sha
write = prev.write
tokens_of = prev.tokens_of


# --------------------------------------------------------------- manifest ---

def alignment_status(jp, us):
    """line -> aligned-JP status for US 61/16, from the source-transfer aligner."""
    jtabs, _ = coverage.grouped_tabs(jp)
    utabs, _ = coverage.grouped_tabs(us)
    status = {}
    for (jpath, jline), (upath, uline) in c_tab_align_iter(
            jtabs.get("s_text"), utabs.get("s_text"),
            align_map=CONF["text"]["align"].get("s_text", []),
            trim_page=CONF["text"]["trim"].get("s_text", [])).iter():
        if not upath or tuple(upath)[:2] != (TOP, SUB) or uline is None:
            continue
        value = coverage.entry(jline) if jline is not None else None
        if value is None:
            state = "NO_JP_ENTRY_ALIGNED"
        elif value.get("repeat"):
            state = "JP_REPEAT_PAGE_MARKER"
        elif "error" in value or not any(k.startswith("CHR") for k, _ in value["tokens"]):
            state = "JP_EMPTY_RECORD"
        else:
            state = "HAS_JP"
        key = upath[-1]
        if status.get(key) == "HAS_JP":
            continue
        status[key] = state
    return status


def load_manifest(jp, us, decode):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = doc["entries"]
    if len(rows) != TOTAL or doc["count"] != TOTAL:
        raise RuntimeError(f"ST61_MANIFEST_COUNT {len(rows)}/{doc['count']}")
    if doc["baseline_production_sha256"] != BASELINE:
        raise RuntimeError("ST61_MANIFEST_BASELINE_DRIFT")
    if (doc["translated_count"] != TOTAL or doc["no_change_required_count"] != 0
            or doc["in_editorial_scope_count"] != IN_SCOPE
            or doc["empty_jp_record_transfer_count"] != EMPTY_JP):
        raise RuntimeError("ST61_MANIFEST_STATUS_COUNTS")
    # the hints this milestone depends on must be present: every JP block of
    # every sub-page anchored onto its US block
    hints = set(CONF["text"]["align"]["s_text"])
    for sub in range(CHILDREN):
        for block in range(1, 10 if sub < 25 else 5):
            if ((TOP, sub, BLOCK_JP * block), (TOP, sub, BLOCK_US * block)) not in hints:
                raise RuntimeError(f"ST61_STRIDE_HINT_MISSING {sub}/{block}")
    item = us.tabs["s_text"][TOP]
    if not isinstance(item, c_ffta_sect_text_sub) or item.tsize != CHILDREN:
        raise RuntimeError("ST61_TOP_SHAPE")
    leaf = item[SUB]
    if leaf.tsize != SUB_TSIZE:
        raise RuntimeError(f"ST61_US_LEAF_TSIZE {leaf.tsize}")
    jleaf = jp.tabs["s_text"][TOP][SUB]
    if any(any(k.startswith("CHR") for k, _ in tokens_of(jleaf[i])) for i in range(jleaf.tsize)):
        raise RuntimeError("ST61_JP_SUB_NOT_EMPTY")
    status = alignment_status(jp, us)
    got = []
    for row in rows:
        line = row["us_line"]
        got.append(line)
        if row["us_logical_path"] != f"s_text/{TOP}/{SUB}/{line}" or row["us_top"] != TOP \
                or row["us_sub"] != SUB:
            raise RuntimeError(f"ST61_MANIFEST_PATH {row['us_logical_path']}")
        if row["status"] != "TRANSLATED_REVIEWED":
            raise RuntimeError(f"ST61_MANIFEST_STATUS {row['us_logical_path']}")
        if not row["japanese"]:
            raise RuntimeError(f"ST61_MANIFEST_EMPTY {row['us_logical_path']}")
        if not _us_source.matches(row, prev.visible(tokens_of(leaf[line]), decode)):
            raise RuntimeError(f"ST61_MANIFEST_ENGLISH_DRIFT {row['us_logical_path']}")
        state = status.get(line, "NO_JP_ENTRY_ALIGNED")
        if state == "HAS_JP":
            raise RuntimeError(f"ST61_TARGET_HAS_JP_ORIGINAL {row['us_logical_path']}")
        expected_scope = {"NO_JP_ENTRY_ALIGNED": "EDITORIAL_557",
                          "JP_EMPTY_RECORD": "EMPTY_JP_RECORD_TRANSFER"}.get(state)
        if row["scope"] != expected_scope:
            raise RuntimeError(f"ST61_SCOPE_MISMATCH {row['us_logical_path']} {state} {row['scope']}")
        row["_us_only_reason"] = state
    # the target set is exactly every visible English line of the sub-page
    visible_lines = [i for i in range(SUB_TSIZE)
                     if any(k.startswith("CHR") for k, _ in tokens_of(leaf[i]))]
    if sorted(got) != visible_lines:
        raise RuntimeError("ST61_MANIFEST_NOT_ALL_VISIBLE_LINES")
    return doc, rows


# ------------------------------------------------------------------ build ---

def product_top61(data):
    """(root, rel61, bundle, child_relatives) read off a product image."""
    root = prev.product_root(data)
    rel = int.from_bytes(data[root + TOP * 4:root + TOP * 4 + 4], "little")
    bundle = root + rel
    children = [int.from_bytes(data[bundle + s * 4:bundle + s * 4 + 4], "little")
                for s in range(CHILDREN)]
    return root, rel, bundle, children


def product_sub_leaf(data, root, children, sub):
    """(leaf_offset, offsets, end) of one sub-leaf; the leaf ends where the
    next child starts (the bundle lays its leaves out in child order)."""
    leaf = root + children[sub]
    count = 0
    while int.from_bytes(data[leaf + count * 2:leaf + count * 2 + 2], "little") != 0xFFFF:
        count += 1
        if count > 4096:
            raise RuntimeError("ST61_PRODUCT_LEAF_UNTERMINATED")
    offsets = [int.from_bytes(data[leaf + i * 2:leaf + i * 2 + 2], "little")
               for i in range(count)]
    later = [root + c for c in children if root + c > leaf]
    end = min(later) if later else None
    return leaf, offsets, end


def product_records(data, leaf, offsets, end):
    """Per-record (raw_bytes, tokens) of a product sub-leaf."""
    out = []
    for i, off in enumerate(offsets):
        start = leaf + off
        stop = leaf + offsets[i + 1] if i + 1 < len(offsets) else \
            (end if end is not None else min(start + 0x8000, len(data)))
        probe = c_ffta_sect_text_line(bytearray(data), start)
        probe.parse_size(stop - start, 2)
        probe.set_nondeterm()
        probe.parse()
        raw_len = int(probe.raw_len)
        if raw_len > stop - start:
            raise RuntimeError(f"ST61_PRODUCT_RECORD_OVERRUN {i}")
        out.append((bytes(data[start:start + raw_len]), list(probe.text.tokens)))
    return out


def serialize_records(records):
    out = bytearray(len(records) * 2)
    out.extend(b"\xff\xff")
    offsets = []
    for rec in records:
        while len(out) & 1:
            out.append(0)
        offsets.append(len(out))
        out.extend(rec)
    for i, off in enumerate(offsets):
        if not 0 <= off <= 0xFFFF:
            raise ValueError("leaf offset overflow")
        out[i * 2:i * 2 + 2] = off.to_bytes(2, "little")
    while len(out) % 4:
        out.append(0)
    return bytes(out)


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
    kanji = prev.kanji_table(doc, jp)
    bounds = prev.family_bounds(jp, jp_raw)

    root = us.tabs["s_text"]
    if int.from_bytes(us_raw[prev.S_TEXT_TABLE_POINTER:prev.S_TEXT_TABLE_POINTER + 4],
                      "little") - 0x08000000 != root.real_offset:
        raise RuntimeError("ST61_ROOT_DECL_DRIFT")
    proot, rel61, bundle, children = product_top61(base)
    if proot != root.real_offset:
        raise RuntimeError("ST61_PRODUCT_ROOT_OFFSET")
    if not (stext.TAIL_START <= bundle < stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("ST61_BUNDLE_NOT_RELOCATED")
    if len(set(children)) != CHILDREN:
        raise RuntimeError("ST61_CHILD_POINTER_SHARED")
    leaf, offsets, end = product_sub_leaf(base, proot, children, SUB)
    if len(offsets) != SUB_TSIZE:
        raise RuntimeError(f"ST61_PRODUCT_LEAF_TSIZE {len(offsets)}")
    current = product_records(base, leaf, offsets, end)

    pristine_leaf = root[TOP][SUB]
    records, new_records, expectations = [], [], {}
    for row in rows:
        line = row["us_line"]
        original = tokens_of(pristine_leaf[line])
        _tokens, expected, data, width, pages, control = prev.encode(
            row, reverse, kanji, alloc, jp_raw, bounds, original)
        flags = pristine_leaf[line].U16(0) & ~COMPRESSION_BIT
        new_records.append((line, flags.to_bytes(2, "little") + data))
        expectations[line] = expected
        records.append({
            "us_logical_path": row["us_logical_path"], "us_top": TOP, "us_sub": SUB,
            "us_line": line, "mission": row["mission"], "speaker": row["speaker"],
            "scope": row["scope"], "original_english_sha256": _us_source.digest(row),
            "japanese": row["japanese"], "status": row["status"],
            "us_only_reason": row["_us_only_reason"],
            "record_flags": f"0x{flags:04X}", "payload_length": len(data),
            "rendered_width_px": width, "pages": pages,
            "width_bound_px": bounds["max_line_px"],
            "eos": True, "roundtrip": "PASS", "controls": control,
            "_expected": expected, "_data": data})
    replacement = dict(new_records)
    composed = [replacement.get(i, current[i][0]) for i in range(SUB_TSIZE)]
    preserved = {i: current[i][1] for i in range(SUB_TSIZE) if i not in replacement}
    blob = serialize_records(composed)

    cursor = block_start = stext.align(prev_block_end, 4)
    raw = bytearray(base)
    raw[cursor:cursor + len(blob)] = blob
    field = bundle + SUB * 4
    old_relative = children[SUB]
    new_relative = cursor - proot
    raw[field:field + 4] = new_relative.to_bytes(4, "little")
    block_end = stext.align(cursor + len(blob), 4)
    leaf_record = {
        "us_top": TOP, "us_sub": SUB, "root_field_us_rom": f"0x{proot + TOP * 4:08X}",
        "root_relative_61": f"0x{rel61:08X}", "bundle_us_rom": f"0x{bundle:08X}",
        "child_field_us_rom": f"0x{field:08X}",
        "old_relative": f"0x{old_relative:08X}", "new_relative": f"0x{new_relative:08X}",
        "previous_leaf_us_rom": f"0x{leaf:08X}",
        "previous_size": (end - leaf) if end else None,
        "new_leaf_us_rom": f"0x{cursor:08X}", "new_size": len(blob),
        "entries": SUB_TSIZE, "records_replaced": len(replacement),
        "records_preserved": SUB_TSIZE - len(replacement)}

    if len(raw) != len(base):
        raise RuntimeError("ST61_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("ST61_BLOCK_OUTSIDE_TAIL")
    # in-process check of the composed leaf before anything is written
    check_leaf, check_offsets, _ = product_sub_leaf(bytes(raw), proot,
                                                    [c if s != SUB else new_relative
                                                     for s, c in enumerate(children)], SUB)
    got = product_records(bytes(raw), check_leaf, check_offsets, check_leaf + len(blob))
    for i in range(SUB_TSIZE):
        want = expectations[i] if i in expectations else preserved[i]
        if got[i][1] != want:
            raise RuntimeError(f"ST61_COMPOSED_TOKEN_MISMATCH {i}")
    return (bytes(raw), base, meta, alloc, records, doc, rows, leaf_record,
            expectations, preserved, block_start, block_end, kanji, bounds,
            {"root": proot, "bundle": bundle, "children": children, "field": field})


# --------------------------------------------------------------- validate ---

def validate(product, base, meta, alloc, records, rows, leaf_record, expectations,
             preserved, block_start, block_end, decode, doc, layout):
    us = meta["us"]
    root = us.tabs["s_text"]
    audits = {}
    if sha(OUTROM) != sha(product):
        raise RuntimeError("ST61_READBACK_ROM_MISMATCH")
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

    # -- independent ROM readback ------------------------------------------
    proot, rel61, bundle, children = product_top61(written)
    broot, brel61, bbundle, bchildren = product_top61(base)
    if (proot, rel61, bundle) != (broot, brel61, bbundle):
        raise RuntimeError("ST61_READBACK_ROOT_OR_BUNDLE_MOVED")
    if children[SUB] != int(leaf_record["new_relative"], 16):
        raise RuntimeError("ST61_READBACK_CHILD_POINTER")
    leaf, offsets, end = product_sub_leaf(written, proot, children, SUB)
    if leaf != int(leaf_record["new_leaf_us_rom"], 16) or len(offsets) != SUB_TSIZE:
        raise RuntimeError("ST61_READBACK_LEAF")
    got = product_records(written, leaf, offsets, block_end)
    readback = []
    for row, record in zip(rows, records):
        line = row["us_line"]
        tokens = got[line][1]
        text = render(tokens)
        if text != row["japanese"]:
            raise RuntimeError(f"ST61_READBACK_TEXT_MISMATCH {row['us_logical_path']} {text!r}")
        if tokens != record["_expected"]:
            raise RuntimeError(f"ST61_READBACK_TOKEN_MISMATCH {row['us_logical_path']}")
        readback.append({"us_logical_path": row["us_logical_path"],
                         "child_relative": f"0x{children[SUB]:08X}",
                         "decoded": text, "result": "PASS"})
    for i, toks in preserved.items():
        if got[i][1] != toks:
            raise RuntimeError(f"ST61_READBACK_PRESERVED_MISMATCH {i}")
    audits["readback"] = {"entries": len(readback), "failures": 0,
                          "preserved_records_token_identical": len(preserved),
                          "source": "independent hand parse of the written ROM file: "
                                    "0x9A88 pointer -> root -> field 61 -> relocated "
                                    "bundle -> child 16 -> leaf offset table -> "
                                    "c_ffta_sect_text_line per record",
                          "result": "PASS", "rows": readback}

    # -- sibling / alias ----------------------------------------------------
    root_tsize = 63
    moved = [t for t in range(root_tsize)
             if product[proot + t * 4:proot + t * 4 + 4] != base[proot + t * 4:proot + t * 4 + 4]]
    if moved:
        raise RuntimeError(f"ST61_ROOT_FIELD_MOVED {moved}")
    other = [s for s in range(CHILDREN) if s != SUB and children[s] != bchildren[s]]
    if other:
        raise RuntimeError(f"ST61_SIBLING_CHILD_MOVED {other}")
    if children[SUB] in {c for s, c in enumerate(children) if s != SUB}:
        raise RuntimeError("ST61_POINTER_COLLISION")
    lo, hi = children[SUB], children[SUB] + leaf_record["new_size"]
    if any(lo <= c < hi for s, c in enumerate(children) if s != SUB):
        raise RuntimeError("ST61_ALIAS_HAZARD")
    relatives = [int.from_bytes(product[proot + t * 4:proot + t * 4 + 4], "little")
                 for t in range(root_tsize)]
    if any(lo <= r < hi for r in relatives):
        raise RuntimeError("ST61_ROOT_ALIAS_HAZARD")
    # every other sub-leaf of the bundle is token-identical to the base image
    for s in range(CHILDREN):
        if s == SUB:
            continue
        pl, po, pe = product_sub_leaf(product, proot, children, s)
        bl, bo, be = product_sub_leaf(base, broot, bchildren, s)
        if (pl, po) != (bl, bo) or product_records(product, pl, po, pe) != \
                product_records(base, bl, bo, be):
            raise RuntimeError(f"ST61_SIBLING_LEAF_CHANGED {s}")
    audits["sibling"] = {
        "target_sub_leaves_rebuilt": 1, "root_fields_changed": 0,
        "non_target_child_pointers_changed": 0,
        "non_target_sub_leaves_token_identical": CHILDREN - 1,
        "unintended_record_changes": 0, "pointer_collisions": 0,
        "alias_propagation": 0, "stale_child_pointers": 0, "result": "PASS"}

    # -- other production families untouched --------------------------------
    fx_span = (fxed.FX_ROOT_FIELD, fxed.FX_ROOT_FIELD + fxed.FX_TSIZE * 4)
    if product[fx_span[0]:fx_span[1]] != base[fx_span[0]:fx_span[1]]:
        raise RuntimeError("ST61_FX_TEXT_ROOT_DISTURBED")
    families = {}
    for name, pool in us.tabs["words"].items():
        span = pool.real_offset, pool.real_offset + pool.tsize * 4
        if product[span[0]:span[1]] != base[span[0]:span[1]]:
            raise RuntimeError(f"ST61_WORDS_FAMILY_DISTURBED words:{name}")
        families[f"words:{name}"] = pool.tsize
    if product[sysbat.PAGES_ROOT_FIELD:sysbat.PAGES_ROOT_FIELD + 4] != \
       base[sysbat.PAGES_ROOT_FIELD:sysbat.PAGES_ROOT_FIELD + 4]:
        raise RuntimeError("ST61_PAGES_BATTLE_DISTURBED")
    audits["other_families"] = {"fx_text_root_fields_unchanged": fxed.FX_TSIZE,
                                "words_root_tables_unchanged": len(families),
                                "pages_battle_root_unchanged": True,
                                "s_text_root_fields_unchanged": root_tsize,
                                "result": "PASS"}

    # -- glyph / asset ------------------------------------------------------
    font = us.tabs["font"]
    fbase = font.real_offset
    if product[fbase:fbase + font.tsize * stext.FONT_STRIDE] != \
       base[fbase:fbase + font.tsize * stext.FONT_STRIDE]:
        raise RuntimeError("ST61_FONT_TABLE_CHANGED")
    if product[stext.US_METADATA:stext.US_METADATA + font.tsize] != \
       base[stext.US_METADATA:stext.US_METADATA + font.tsize]:
        raise RuntimeError("ST61_FONT_METADATA_CHANGED")
    used = sorted({v for r in records for k, v in r["_expected"] if k == "CHR_FULL"})
    allocated = set(alloc.values())
    stray = [v for v in used if v not in allocated]
    if stray:
        raise RuntimeError(f"ST61_SLOT_NOT_FROM_JP_ALLOCATION {stray[:8]}")
    jp_raw = JP.read_bytes()
    for slot in used:
        code = inverse[slot]
        joff = meta["jp"].tabs["font"].real_offset + code * stext.FONT_STRIDE
        uoff = fbase + slot * stext.FONT_STRIDE
        if product[uoff:uoff + stext.FONT_STRIDE] != jp_raw[joff:joff + stext.FONT_STRIDE]:
            raise RuntimeError(f"ST61_SLOT_NOT_JP_IDENTICAL 0x{slot:04X}")
    audits["glyph"] = {"new_glyph_records": 0, "font_table_writes": 0,
                       "metadata_writes": 0, "low_slot_overwrites": 0,
                       "existing_mappings_changed": 0,
                       "distinct_slots_used": len(used),
                       "kanji_slots_used": sum(1 for s in used if inverse[s] >= 0x122),
                       "all_slots_are_native_jp_records": True,
                       "last_allocated_slot": f"0x{max(allocated):04X}",
                       "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    field = layout["field"]
    permitted = sorted([(field, field + 4), (block_start, block_end)])
    changed = list(stext.changed_ranges(base, product))
    unexplained = [(f"0x{lo:08X}", f"0x{hi:08X}") for lo, hi in changed
                   if not any(a <= lo and hi <= b for a, b in permitted)]
    if unexplained:
        raise RuntimeError(f"ST61_BINARY_TOUCH_REGRESSION {unexplained[:8]}")
    code_ranges = [c for c in changed if c[0] < 0x400000]
    if code_ranges:
        raise RuntimeError(f"ST61_CODE_TOUCHED {code_ranges[:4]}")
    audits["binary"] = {
        "result": "PASS", "changed_ranges": len(changed),
        "root_pointer_fields": 0, "child_pointer_fields": 1,
        "relocated_payload_blocks": 1, "new_font_slots": 0, "unexplained_ranges": 0,
        "rom_executable_code_bytes_changed": 0,
        "child_field": f"0x{field:08X}",
        "block": {"start": f"0x{block_start:08X}", "end": f"0x{block_end:08X}",
                  "bytes": block_end - block_start,
                  "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end}}
    return audits


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260830_top61_production")
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
    (product, base, meta, alloc, records, doc, rows, leaf_record, expectations,
     preserved, bs, be, kanji, bounds, layout) = first
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rs]
    if sha(product) != sha(second[0]) or strip(records) != strip(second[4]):
        raise RuntimeError("ST61_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[10], second[11]) or leaf_record != second[7]:
        raise RuntimeError("ST61_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    decode, _reverse = sysbat.charset_tables()
    audits = validate(product, base, meta, alloc, records, rows, leaf_record,
                      expectations, preserved, bs, be, decode, doc, layout)
    if sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(
            f"CANONICAL_PRODUCTION_MISMATCH {sha(product)} != {EXPECTED_PRODUCTION}")

    determinism = {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                   "identical": True, "record_set_identical": True,
                   "pointer_layout_identical": leaf_record == second[7],
                   "serialized_text_identical":
                       [r["japanese"] for r in records] == [r["japanese"] for r in second[4]],
                   "glyph_allocation_identical": alloc == second[3],
                   "baseline_sha256": sha(base)}
    summary = {
        "milestone": doc["milestone"],
        "baseline_sha256": sha(base),
        "production_sha256": sha(product),
        "rom": str(OUTROM),
        "batch": {"entries": len(records), "translated": len(records),
                  "in_editorial_557_scope": sum(1 for r in records if r["scope"] == "EDITORIAL_557"),
                  "empty_jp_record_transfer": sum(1 for r in records
                                                  if r["scope"] == "EMPTY_JP_RECORD_TRANSFER"),
                  "missions": sorted({r["mission"] for r in records}),
                  "us_only_proof": {r: sum(1 for x in records if x["us_only_reason"] == r)
                                    for r in sorted({x["us_only_reason"] for x in records})}},
        "leaf": leaf_record,
        "width_bounds": bounds,
        "kanji_verified_from_jp_originals": len(kanji),
        "audits": {k: v["result"] for k, v in audits.items()},
        "determinism": determinism,
        "coverage": {"note": "Recompute the exact remainder with "
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
