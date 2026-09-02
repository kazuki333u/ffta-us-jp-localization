#! python3
"""Editorial localization of the final s_text remainder -- the US-only story
lines that live *inside* JP leaves the leaf-repoint layer already rewrites.

Seventh *class 5* milestone, fourth s_text editorial batch, and the terminal
production layer.  Five leaves, 69 lines:

* s_text top 0  (4..8)      shared text0: special characters leaving the clan
* s_text top 1  (118..170)  snowball tutorial, post-fight bullies, Randell at
                            work (the US rewrite of the drunk scene), tutorial
                            repeat
* s_text top 19 (101..106)  Cid remembers (the US rewrite of JP 19/47, 53..57)
* s_text top 26 (71)        Mewt's refusal (the US rewrite of JP 26/20)
* s_text top 34 (22..25)    epilogue: Biggs offers Randell a job

Like the top-61 milestone this one is two things.  **(a) A correspondence
correction**: in tops 1, 19 and 26 the US ROM inserts its new dialogue
*before* the JP leaf's trailing developer cut-index labels, so the raw-index
pairing wrote a JP ``カット番号`` label over each inserted English line (11 in
top 1, 6 in top 19, 1 in top 26 -- hidden English present in every production
ROM since layer 1) and left the US copies of the labels unpaired.  Three
anchors in ``CONF['text']['align']['s_text']`` fix that; they add and remove
no pair (s_text AUTO_MATCH stays 5,470).  **(b) The editorial batch** above.

It also closes the last "remaining" class without writing anything: US
s_text top 36 is a dead leaf -- Japanese in the retail US ROM, a near-verbatim
copy of JP top 35, shared by root tops 40/41/43/54/56/57/58/59, and no
scene-script program of either ROM issues a 0x0F text command against any of
those owners.  Its 97 visible lines are ``NON_PRODUCTION_INTERNAL`` in the
manifest, and the dead-leaf claim is re-proved mechanically on every build.

The translations are NOT computed here.  They are read from the tracked
manifest ``data/us_only_s_text_final_remainder.json`` and revalidated against
both ROMs on every build.  Mechanism: each target leaf is recomposed from the
*product* image (the leaf-repoint layer already relocated it into the tail
with its JP lines transferred), every non-target record preserved
byte-for-byte, the new leaf is tail-relocated after the top-61 block, and the
leaf's root-relative field is rewritten.

It also restores two retail **aliases** that earlier layers had broken by
repointing only one member of a pointer pair: s_text root field 60 aliases
root field 0 in both retail ROMs (the jagd-exit scene, FAT 169, owns text
page 60 and reads the shared lines 0xF1..0xF8), and fx_text root field 24
aliases field 23 (the law descriptions).  Layer 1 relocated leaf 0 and the
fx_text editorial layer relocated leaf 23 without moving the alias fields,
so the retail English payloads stayed reachable through 60 and 24.  Both
fields are set to their partner's product value; an alias audit proves every
retail alias group of every text family intact on every build.

Consequences: **zero new glyph records, zero font or metadata writes, zero ROM
code bytes patched.**  Seven root fields (five leaves + two alias
restorations) and one relocated tail block.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import ffta_jp_coverage_audit as coverage
from ffta_modifier import CONF, c_tab_align_iter
from ffta_parser import make_script_parser
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_only_system_battle as sysbat
import ffta_jp_us_only_fx_text as fxed
import ffta_jp_us_only_s_text_judge_ezel as sje
import ffta_jp_us_only_s_text_top61 as prev
from ffta_sect import c_ffta_sect_text_page
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_only_s_text_final_remainder.json"
RUN_BASE = HERE / "build/us_only_s_text_localization"
OUTROM = ROOT / "rom/build/ffta_us_jp_s_text_final.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_s_text_final_repeat.gba"

# Output of ffta_jp_us_only_s_text_top61 on the corrected chain.  That
# module's EXPECTED_PRODUCTION is now a private per-layer drift gate.
BASELINE = "0934DDA961C206CAAC64E1B2F80385734240A7BB4B4C55050EADC0B2BA7B5C96"
# Terminal artifact of the production chain: the single canonical final-SHA
# authority.
EXPECTED_PRODUCTION = "A7E97A64497BAACCAED10E17D5ED0E22A49ABC094A6419E7822917D833E33A34"

TARGET_TOPS = (0, 1, 19, 26, 34)
TOP_TSIZE = {0: 9, 1: 182, 19: 116, 26: 78, 34: 26}
TARGET_LINES = {0: range(4, 9), 1: range(118, 171), 19: range(101, 107),
                26: range(71, 72), 34: range(22, 26)}
TRANSLATED = 69
INTERNAL_TOP = 36
INTERNAL = 97
STORY_HINTS = (((1, 118), (1, 171)), ((19, 101), (19, 107)), ((26, 71), (26, 72)))
ALIAS_TOP, ALIAS_OF = 60, 0          # s_text root 60 aliases root 0 (retail JP and US)
FX_ALIAS, FX_ALIAS_OF = 24, 23       # fx_text root 24 aliases root 23 (retail US)
ROOT_TSIZE = 63
COMPRESSION_BIT = 0x0002
LATIN = re.compile(r"[A-Za-z]")
sha = sje.sha
write = sje.write
tokens_of = sje.tokens_of


# --------------------------------------------------------------- manifest ---

def alignment_status(jp, us):
    """(top, line) -> aligned-JP status for the target tops and the dead top."""
    tops = set(TARGET_TOPS) | {INTERNAL_TOP}
    jtabs, _ = coverage.grouped_tabs(jp)
    utabs, _ = coverage.grouped_tabs(us)
    status = {}
    for (jpath, jline), (upath, uline) in c_tab_align_iter(
            jtabs.get("s_text"), utabs.get("s_text"),
            align_map=CONF["text"]["align"].get("s_text", []),
            trim_page=CONF["text"]["trim"].get("s_text", [])).iter():
        if not upath or upath[0] not in tops or uline is None or len(tuple(upath)) != 2:
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


def dead_leaf_proof(us):
    """Mechanical proof that US s_text top 36 is unreachable: every root top
    sharing its leaf, every scene FAT record owning any of those tops, and
    every 0x0F text command of every parsable scene program."""
    root = us.tabs["s_text"]
    us_raw = US.read_bytes()
    rel = [int.from_bytes(us_raw[root.real_offset + t * 4:root.real_offset + t * 4 + 4],
                          "little") for t in range(root.tsize)]
    aliases = sorted(t for t, r in enumerate(rel) if r == rel[INTERNAL_TOP])
    psr = make_script_parser(us, "scene")
    fat, scr = psr.sects["fat"], psr.sects["script"]
    owners, programs, text_cmds, parsed = [], 0, 0, 0
    for i in range(1, fat.tsize):
        sp, si, tp = fat.get_entry(i)
        if tp in aliases:
            group_pages = scr[sp].tsize if sp < scr.tsize else 0
            owners.append({"fat": i, "script_group": sp, "script_page": si,
                           "owner": tp, "group_pages": group_pages})
        try:
            prog = psr.get_program(i)
        except Exception:
            prog = None
        if not prog:
            continue
        parsed += 1
        for _ofs, cmd in prog.cmds.items():
            if cmd.op != 0x0F:
                continue
            tidx = cmd.prms[0]
            owner = 0 if (tidx & 0xF0) == 0xF0 else prog.text_idx
            if owner in aliases:
                text_cmds += 1
    if text_cmds:
        raise RuntimeError(f"SFR_DEAD_LEAF_HAS_CONSUMER {text_cmds}")
    for o in owners:
        # a record owning the dead leaf must either have no script page at all
        # or a program that issues no text command (checked above)
        if o["group_pages"] and o["script_page"] >= o["group_pages"]:
            raise RuntimeError(f"SFR_DEAD_LEAF_OWNER_SHAPE {o}")
    return {"top": INTERNAL_TOP, "aliases_sharing_the_leaf": aliases,
            "fat_records_owning_an_alias": owners,
            "scene_programs_parsed": parsed, "text_commands_against_aliases": 0,
            "result": "PASS"}


def load_manifest(jp, us, decode):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows, internal = doc["entries"], doc["internal_entries"]
    if doc["count"] != TRANSLATED + INTERNAL or len(rows) != TRANSLATED or len(internal) != INTERNAL:
        raise RuntimeError(f"SFR_MANIFEST_COUNT {len(rows)}/{len(internal)}/{doc['count']}")
    if doc["baseline_production_sha256"] != BASELINE:
        raise RuntimeError("SFR_MANIFEST_BASELINE_DRIFT")
    if (doc["translated_count"] != TRANSLATED or doc["no_change_required_count"] != 0
            or doc["non_production_internal_count"] != INTERNAL):
        raise RuntimeError("SFR_MANIFEST_STATUS_COUNTS")
    hints = set(CONF["text"]["align"]["s_text"])
    for hint in STORY_HINTS:
        if hint not in hints:
            raise RuntimeError(f"SFR_STORY_HINT_MISSING {hint}")
    declared = {(tuple(h["jp"]), tuple(h["us"])) for h in doc["align_hints"]}
    if declared != set(STORY_HINTS):
        raise RuntimeError("SFR_MANIFEST_HINTS_DRIFT")
    if set(stext.EXPECTED_TOPS) & set(TARGET_TOPS) != set(TARGET_TOPS):
        raise RuntimeError("SFR_TARGET_TOP_NOT_OWNED_BY_LEAF_REPOINT")
    root = us.tabs["s_text"]
    for top in TARGET_TOPS:
        page = root[top]
        if not isinstance(page, c_ffta_sect_text_page) or page.tsize != TOP_TSIZE[top]:
            raise RuntimeError(f"SFR_US_LEAF_SHAPE {top}")
    status = alignment_status(jp, us)
    got = defaultdict(set)
    for row in rows:
        top, line = row["us_top"], row["us_line"]
        if row["us_logical_path"] != f"s_text/{top}/{line}" or top not in TARGET_TOPS \
                or line not in TARGET_LINES[top]:
            raise RuntimeError(f"SFR_MANIFEST_PATH {row['us_logical_path']}")
        if row["status"] != "TRANSLATED_REVIEWED" or row["scope"] != "EDITORIAL_REMAINDER":
            raise RuntimeError(f"SFR_MANIFEST_STATUS {row['us_logical_path']}")
        if not row["japanese"]:
            raise RuntimeError(f"SFR_MANIFEST_EMPTY {row['us_logical_path']}")
        if not _us_source.matches(row, sje.visible(tokens_of(root[top][line]), decode)):
            raise RuntimeError(f"SFR_MANIFEST_ENGLISH_DRIFT {row['us_logical_path']}")
        state = status.get((top, line), "NO_JP_ENTRY_ALIGNED")
        if state != "NO_JP_ENTRY_ALIGNED":
            raise RuntimeError(f"SFR_TARGET_NOT_US_ONLY {row['us_logical_path']} {state}")
        row["_us_only_reason"] = state
        got[top].add(line)
    # the target set is exactly every line of the target leaves that has no
    # aligned JP entry and carries Latin text -- no more, no less
    for top in TARGET_TOPS:
        want = set()
        for line in range(TOP_TSIZE[top]):
            toks = tokens_of(root[top][line])
            if not any(k.startswith("CHR") for k, _ in toks):
                continue
            if status.get((top, line), "NO_JP_ENTRY_ALIGNED") != "NO_JP_ENTRY_ALIGNED":
                continue
            if LATIN.search(sje.visible(toks, decode)):
                want.add(line)
        if want != set(TARGET_LINES[top]) or got[top] != want:
            raise RuntimeError(f"SFR_TARGET_SET_MISMATCH {top} {sorted(want ^ got[top])}")
    # the closed class: every visible line of the dead leaf, all Japanese
    leaf = root[INTERNAL_TOP]
    visible_lines = [i for i in range(leaf.tsize)
                     if any(k.startswith("CHR") for k, _ in tokens_of(leaf[i]))]
    listed = []
    for row in internal:
        if row["us_top"] != INTERNAL_TOP or row["status"] != "NON_PRODUCTION_INTERNAL" \
                or row["us_logical_path"] != f"s_text/{INTERNAL_TOP}/{row['us_line']}":
            raise RuntimeError(f"SFR_INTERNAL_ROW {row['us_logical_path']}")
        rendered = sje.visible(tokens_of(leaf[row["us_line"]]), decode)
        if LATIN.search(re.sub(r"\[[0-9A-F]+\]|<[0-9A-F]{4}>", "", rendered)):
            raise RuntimeError(f"SFR_INTERNAL_LINE_HAS_LATIN {row['us_logical_path']}")
        listed.append(row["us_line"])
    if sorted(listed) != visible_lines:
        raise RuntimeError("SFR_INTERNAL_SET_MISMATCH")
    proof = dead_leaf_proof(us)
    return doc, rows, internal, proof


# ------------------------------------------------------------------ build ---

def product_relatives(data, root):
    return [int.from_bytes(data[root + t * 4:root + t * 4 + 4], "little")
            for t in range(ROOT_TSIZE)]


def product_leaf_records(data, root, top, expected_tsize):
    """(relative, leaf_offset, offsets, records) of one relocated leaf page."""
    rel, leaf, count = sje.product_leaf(data, root, top)
    if count != expected_tsize:
        raise RuntimeError(f"SFR_PRODUCT_LEAF_TSIZE {top} {count}")
    offsets = [int.from_bytes(data[leaf + i * 2:leaf + i * 2 + 2], "little")
               for i in range(count)]
    later = [root + r for r in product_relatives(data, root) if root + r > leaf]
    end = min(later) if later else None
    return rel, leaf, offsets, prev.product_records(data, leaf, offsets, end)


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
    doc, rows, internal, proof = load_manifest(jp, us, decode)
    kanji = sje.kanji_table(doc, jp)
    bounds = sje.family_bounds(jp, jp_raw)

    root = us.tabs["s_text"]
    if int.from_bytes(us_raw[sje.S_TEXT_TABLE_POINTER:sje.S_TEXT_TABLE_POINTER + 4],
                      "little") - 0x08000000 != root.real_offset:
        raise RuntimeError("SFR_ROOT_DECL_DRIFT")
    proot = sje.product_root(base)
    if proot != root.real_offset:
        raise RuntimeError("SFR_PRODUCT_ROOT_OFFSET")
    relatives = product_relatives(base, proot)
    for top in TARGET_TOPS:
        sharers = [t for t, r in enumerate(relatives) if r == relatives[top] and t != top]
        if sharers:
            raise RuntimeError(f"SFR_TARGET_LEAF_SHARED {top} {sharers}")
        if not (stext.TAIL_START <= proot + relatives[top] < stext.TAIL_START + stext.TAIL_CAPACITY):
            raise RuntimeError(f"SFR_TARGET_LEAF_NOT_RELOCATED {top}")

    current = {top: product_leaf_records(base, proot, top, TOP_TSIZE[top]) for top in TARGET_TOPS}
    records, replacements, expectations = [], defaultdict(dict), defaultdict(dict)
    for row in rows:
        top, line = row["us_top"], row["us_line"]
        pristine = root[top][line]
        original = tokens_of(pristine)
        _tokens, expected, data, width, pages, control = sje.encode(
            row, reverse, kanji, alloc, jp_raw, bounds, original)
        flags = pristine.U16(0) & ~COMPRESSION_BIT
        replacements[top][line] = flags.to_bytes(2, "little") + data
        expectations[top][line] = expected
        records.append({
            "us_logical_path": row["us_logical_path"], "us_top": top, "us_line": line,
            "scene": row["scene"], "speaker": row["speaker"], "scope": row["scope"],
            "original_english_sha256": _us_source.digest(row), "japanese": row["japanese"],
            "status": row["status"], "us_only_reason": row["_us_only_reason"],
            "record_flags": f"0x{flags:04X}", "payload_length": len(data),
            "rendered_width_px": width, "pages": pages,
            "width_bound_px": bounds["max_line_px"],
            "eos": True, "roundtrip": "PASS", "controls": control,
            "_expected": expected, "_data": data})

    raw = bytearray(base)
    cursor = block_start = stext.align(prev_block_end, 4)
    leaf_records, preserved, new_layout = [], {}, {}
    for top in TARGET_TOPS:
        rel, leaf, offsets, recs = current[top]
        composed = [replacements[top].get(i, recs[i][0]) for i in range(TOP_TSIZE[top])]
        preserved[top] = {i: recs[i][1] for i in range(TOP_TSIZE[top]) if i not in replacements[top]}
        blob = prev.serialize_records(composed)
        field = proot + top * 4
        new_relative = cursor - proot
        raw[cursor:cursor + len(blob)] = blob
        raw[field:field + 4] = new_relative.to_bytes(4, "little")
        leaf_records.append({
            "us_top": top, "root_field_us_rom": f"0x{field:08X}",
            "old_relative": f"0x{rel:08X}", "new_relative": f"0x{new_relative:08X}",
            "previous_leaf_us_rom": f"0x{leaf:08X}",
            "new_leaf_us_rom": f"0x{cursor:08X}", "new_size": len(blob),
            "entries": TOP_TSIZE[top], "records_replaced": len(replacements[top]),
            "records_preserved": TOP_TSIZE[top] - len(replacements[top])})
        new_layout[top] = (cursor, len(blob))
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    # retail alias restoration: the partner fields must alias in the retail
    # ROM, and the alias member must still carry its retail (pristine) value
    # in the base image -- i.e. it is the untouched half of a broken pair
    def u32(b, o):
        return int.from_bytes(b[o:o + 4], "little")
    sfield, sof = proot + ALIAS_TOP * 4, proot + ALIAS_OF * 4
    if u32(us_raw, sfield) != u32(us_raw, sof):
        raise RuntimeError("SFR_S_TEXT_ALIAS_NOT_RETAIL")
    if u32(base, sfield) != u32(us_raw, sfield):
        raise RuntimeError("SFR_S_TEXT_ALIAS_FIELD_ALREADY_MOVED")
    raw[sfield:sfield + 4] = raw[sof:sof + 4]
    ffield, fof = fxed.FX_ROOT_FIELD + FX_ALIAS * 4, fxed.FX_ROOT_FIELD + FX_ALIAS_OF * 4
    if u32(us_raw, ffield) != u32(us_raw, fof):
        raise RuntimeError("SFR_FX_TEXT_ALIAS_NOT_RETAIL")
    if u32(base, ffield) != u32(us_raw, ffield):
        raise RuntimeError("SFR_FX_TEXT_ALIAS_FIELD_ALREADY_MOVED")
    raw[ffield:ffield + 4] = base[fof:fof + 4]
    alias_records = [
        {"family": "s_text", "field": ALIAS_TOP, "aliases": ALIAS_OF,
         "field_us_rom": f"0x{sfield:08X}", "retail_value": f"0x{u32(us_raw, sfield):08X}",
         "base_value": f"0x{u32(base, sfield):08X}", "new_value": f"0x{u32(raw, sfield):08X}",
         "reason": "jagd-exit scene FAT 169 owns text page 60 and reads shared lines 0xF1..0xF8; "
                   "retail JP and US both alias 60 to 0"},
        {"family": "fx_text", "field": FX_ALIAS, "aliases": FX_ALIAS_OF,
         "field_us_rom": f"0x{ffield:08X}", "retail_value": f"0x{u32(us_raw, ffield):08X}",
         "base_value": f"0x{u32(base, ffield):08X}", "new_value": f"0x{u32(raw, ffield):08X}",
         "reason": "retail US aliases fx_text 24 to 23 (law descriptions); the fx_text editorial "
                   "layer relocated 23 only"}]
    new_layout["_alias"] = alias_records

    if len(raw) != len(base):
        raise RuntimeError("SFR_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("SFR_BLOCK_OUTSIDE_TAIL")
    # in-process check of every composed leaf before anything is written
    for top in TARGET_TOPS:
        _rel, leaf, offsets, got = product_leaf_records(bytes(raw), proot, top, TOP_TSIZE[top])
        if leaf != new_layout[top][0]:
            raise RuntimeError(f"SFR_COMPOSED_LEAF_OFFSET {top}")
        for i in range(TOP_TSIZE[top]):
            want = expectations[top][i] if i in expectations[top] else preserved[top][i]
            if got[i][1] != want:
                raise RuntimeError(f"SFR_COMPOSED_TOKEN_MISMATCH {top}/{i}")
    return (bytes(raw), base, meta, alloc, records, doc, rows, leaf_records,
            expectations, preserved, block_start, block_end, kanji, bounds,
            {"root": proot, "relatives": relatives, "layout": new_layout},
            internal, proof)


# --------------------------------------------------------------- validate ---

def validate(product, base, meta, alloc, records, rows, leaf_records, expectations,
             preserved, block_start, block_end, decode, doc, layout, proof):
    us = meta["us"]
    audits = {}
    if sha(OUTROM) != sha(product):
        raise RuntimeError("SFR_READBACK_ROM_MISMATCH")
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
    proot = sje.product_root(written)
    if proot != layout["root"] or proot != sje.product_root(base):
        raise RuntimeError("SFR_READBACK_ROOT_OFFSET")
    relatives = product_relatives(written, proot)
    leaves = {}
    for rec in leaf_records:
        top = rec["us_top"]
        if relatives[top] != int(rec["new_relative"], 16):
            raise RuntimeError(f"SFR_READBACK_ROOT_FIELD {top}")
        _rel, leaf, _offsets, recs = product_leaf_records(written, proot, top, TOP_TSIZE[top])
        if leaf != int(rec["new_leaf_us_rom"], 16):
            raise RuntimeError(f"SFR_READBACK_LEAF {top}")
        leaves[top] = recs
    readback = []
    for row, record in zip(rows, records):
        top, line = row["us_top"], row["us_line"]
        tokens = leaves[top][line][1]
        text = render(tokens)
        if text != row["japanese"]:
            raise RuntimeError(f"SFR_READBACK_TEXT_MISMATCH {row['us_logical_path']} {text!r}")
        if tokens != record["_expected"]:
            raise RuntimeError(f"SFR_READBACK_TOKEN_MISMATCH {row['us_logical_path']}")
        readback.append({"us_logical_path": row["us_logical_path"],
                         "root_relative": f"0x{relatives[top]:08X}",
                         "decoded": text, "result": "PASS"})
    preserved_count = 0
    for top, lines in preserved.items():
        for i, toks in lines.items():
            if leaves[top][i][1] != toks:
                raise RuntimeError(f"SFR_READBACK_PRESERVED_MISMATCH {top}/{i}")
            preserved_count += 1
    audits["readback"] = {"entries": len(readback), "failures": 0,
                          "preserved_records_token_identical": preserved_count,
                          "source": "independent hand parse of the written ROM file: "
                                    "0x9A88 pointer -> root -> root-relative field -> "
                                    "relocated leaf -> offset table -> "
                                    "c_ffta_sect_text_line per record",
                          "result": "PASS", "rows": readback}

    # -- sibling / alias ----------------------------------------------------
    base_rel = product_relatives(base, proot)
    us_raw = US.read_bytes()
    retail_rel = product_relatives(us_raw, proot)
    moved = [t for t in range(ROOT_TSIZE) if relatives[t] != base_rel[t]]
    if sorted(moved) != sorted(TARGET_TOPS + (ALIAS_TOP,)):
        raise RuntimeError(f"SFR_ROOT_FIELDS_MOVED {moved}")
    if relatives[ALIAS_TOP] != relatives[ALIAS_OF]:
        raise RuntimeError("SFR_S_TEXT_ALIAS_NOT_RESTORED")
    spans = {top: (relatives[top], relatives[top] + layout["layout"][top][1]) for top in TARGET_TOPS}
    for top, (lo, hi) in spans.items():
        partners = {top} | ({ALIAS_TOP} if top == ALIAS_OF else set())
        if any(lo <= r < hi for t, r in enumerate(relatives) if t not in partners):
            raise RuntimeError(f"SFR_ALIAS_HAZARD {top}")
    # every retail alias group of the s_text root is intact in the product
    groups = defaultdict(list)
    for t, r in enumerate(retail_rel):
        groups[r].append(t)
    for members in groups.values():
        if len(members) > 1 and len({relatives[t] for t in members}) != 1:
            raise RuntimeError(f"SFR_RETAIL_ALIAS_GROUP_BROKEN {members}")
    if len(set(relatives)) != len(set(retail_rel)):
        raise RuntimeError("SFR_ROOT_SHARING_CHANGED")
    # every other relocated/pristine leaf page is token-identical to the base
    # image, and the top-61 bundle did not move
    identical = 0
    # the restored alias must decode exactly as its partner leaf
    _r, al, ao, arecs = product_leaf_records(written, proot, ALIAS_TOP, TOP_TSIZE[ALIAS_OF])
    _r, pl0, po0, precs0 = product_leaf_records(written, proot, ALIAS_OF, TOP_TSIZE[ALIAS_OF])
    if (al, ao) != (pl0, po0) or [r[1] for r in arecs] != [r[1] for r in precs0]:
        raise RuntimeError("SFR_ALIAS_LEAF_NOT_IDENTICAL_TO_PARTNER")
    for top in range(ROOT_TSIZE):
        if top in TARGET_TOPS or top == 61 or top == ALIAS_TOP:
            continue
        _r, bl, bo, brecs = product_leaf_records(base, proot, top, sje.product_leaf(base, proot, top)[2])
        _r, pl, po, precs = product_leaf_records(written, proot, top, sje.product_leaf(written, proot, top)[2])
        if (bl, bo) != (pl, po) or [r[1] for r in brecs] != [r[1] for r in precs]:
            raise RuntimeError(f"SFR_SIBLING_LEAF_CHANGED {top}")
        identical += 1
    if prev.product_top61(written) != prev.product_top61(base):
        raise RuntimeError("SFR_TOP61_BUNDLE_MOVED")
    audits["sibling"] = {
        "target_leaves_rebuilt": len(TARGET_TOPS), "root_fields_changed": len(TARGET_TOPS) + 1,
        "alias_fields_restored": {"s_text": f"{ALIAS_TOP}->{ALIAS_OF}", "fx_text": f"{FX_ALIAS}->{FX_ALIAS_OF}"},
        "retail_alias_groups_intact": sum(1 for m in groups.values() if len(m) > 1),
        "alias_leaf_token_identical_to_partner": True,
        "non_target_root_fields_changed": 0, "non_target_leaves_token_identical": identical,
        "top61_bundle_and_child_pointers_unchanged": True,
        "unintended_record_changes": 0, "pointer_collisions": 0,
        "alias_propagation": 0, "stale_pointers": 0, "result": "PASS"}

    # -- other production families untouched --------------------------------
    fx_changed = [i for i in range(fxed.FX_TSIZE)
                  if product[fxed.FX_ROOT_FIELD + i * 4:fxed.FX_ROOT_FIELD + i * 4 + 4]
                  != base[fxed.FX_ROOT_FIELD + i * 4:fxed.FX_ROOT_FIELD + i * 4 + 4]]
    if fx_changed != [FX_ALIAS]:
        raise RuntimeError(f"SFR_FX_TEXT_ROOT_DISTURBED {fx_changed}")
    fx_fields = lambda b: [int.from_bytes(b[fxed.FX_ROOT_FIELD + i * 4:fxed.FX_ROOT_FIELD + i * 4 + 4], "little")
                           for i in range(fxed.FX_TSIZE)]
    pf, rf = fx_fields(product), fx_fields(us_raw)
    if pf[FX_ALIAS] != pf[FX_ALIAS_OF]:
        raise RuntimeError("SFR_FX_TEXT_ALIAS_NOT_RESTORED")
    fgroups = defaultdict(list)
    for i, v in enumerate(rf):
        fgroups[v].append(i)
    for members in fgroups.values():
        if len(members) > 1 and len({pf[i] for i in members}) != 1:
            raise RuntimeError(f"SFR_FX_RETAIL_ALIAS_GROUP_BROKEN {members}")
    families = {}
    for name, pool in us.tabs["words"].items():
        span = pool.real_offset, pool.real_offset + pool.tsize * 4
        if product[span[0]:span[1]] != base[span[0]:span[1]]:
            raise RuntimeError(f"SFR_WORDS_FAMILY_DISTURBED words:{name}")
        families[f"words:{name}"] = pool.tsize
    if product[sysbat.PAGES_ROOT_FIELD:sysbat.PAGES_ROOT_FIELD + 4] != \
       base[sysbat.PAGES_ROOT_FIELD:sysbat.PAGES_ROOT_FIELD + 4]:
        raise RuntimeError("SFR_PAGES_BATTLE_DISTURBED")
    audits["other_families"] = {"fx_text_root_fields_unchanged": fxed.FX_TSIZE - 1,
                                "fx_text_alias_field_restored": f"{FX_ALIAS}->{FX_ALIAS_OF}",
                                "fx_text_retail_alias_groups_intact": sum(1 for m in fgroups.values() if len(m) > 1),
                                "words_root_tables_unchanged": len(families),
                                "pages_battle_root_unchanged": True,
                                "s_text_non_target_root_fields_unchanged": ROOT_TSIZE - len(TARGET_TOPS),
                                "result": "PASS"}

    # -- glyph / asset ------------------------------------------------------
    font = us.tabs["font"]
    fbase = font.real_offset
    if product[fbase:fbase + font.tsize * stext.FONT_STRIDE] != \
       base[fbase:fbase + font.tsize * stext.FONT_STRIDE]:
        raise RuntimeError("SFR_FONT_TABLE_CHANGED")
    if product[stext.US_METADATA:stext.US_METADATA + font.tsize] != \
       base[stext.US_METADATA:stext.US_METADATA + font.tsize]:
        raise RuntimeError("SFR_FONT_METADATA_CHANGED")
    used = sorted({v for r in records for k, v in r["_expected"] if k == "CHR_FULL"})
    allocated = set(alloc.values())
    stray = [v for v in used if v not in allocated]
    if stray:
        raise RuntimeError(f"SFR_SLOT_NOT_FROM_JP_ALLOCATION {stray[:8]}")
    jp_raw = JP.read_bytes()
    for slot in used:
        code = inverse[slot]
        joff = meta["jp"].tabs["font"].real_offset + code * stext.FONT_STRIDE
        uoff = fbase + slot * stext.FONT_STRIDE
        if product[uoff:uoff + stext.FONT_STRIDE] != jp_raw[joff:joff + stext.FONT_STRIDE]:
            raise RuntimeError(f"SFR_SLOT_NOT_JP_IDENTICAL 0x{slot:04X}")
    audits["glyph"] = {"new_glyph_records": 0, "font_table_writes": 0,
                       "metadata_writes": 0, "low_slot_overwrites": 0,
                       "existing_mappings_changed": 0,
                       "distinct_slots_used": len(used),
                       "kanji_slots_used": sum(1 for s in used if inverse[s] >= 0x122),
                       "all_slots_are_native_jp_records": True,
                       "last_allocated_slot": f"0x{max(allocated):04X}",
                       "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    permitted = sorted([(proot + top * 4, proot + top * 4 + 4) for top in TARGET_TOPS]
                       + [(proot + ALIAS_TOP * 4, proot + ALIAS_TOP * 4 + 4),
                          (fxed.FX_ROOT_FIELD + FX_ALIAS * 4, fxed.FX_ROOT_FIELD + FX_ALIAS * 4 + 4),
                          (block_start, block_end)])
    changed = list(stext.changed_ranges(base, product))
    unexplained = [(f"0x{lo:08X}", f"0x{hi:08X}") for lo, hi in changed
                   if not any(a <= lo and hi <= b for a, b in permitted)]
    if unexplained:
        raise RuntimeError(f"SFR_BINARY_TOUCH_REGRESSION {unexplained[:8]}")
    # nothing below the data/tail boundary may change except the declared
    # pointer fields (the fx_text root table at 0x36D678 is data, not code)
    code_ranges = [c for c in changed if c[0] < 0x400000
                   and not any(a <= c[0] and c[1] <= b for a, b in permitted)]
    if code_ranges:
        raise RuntimeError(f"SFR_CODE_TOUCHED {code_ranges[:4]}")
    audits["binary"] = {
        "result": "PASS", "changed_ranges": len(changed),
        "root_pointer_fields": len(TARGET_TOPS) + 2, "child_pointer_fields": 0,
        "alias_restorations": layout["layout"]["_alias"],
        "relocated_payload_blocks": 1, "new_font_slots": 0, "unexplained_ranges": 0,
        "rom_executable_code_bytes_changed": 0,
        "root_fields": [f"0x{proot + top * 4:08X}" for top in TARGET_TOPS + (ALIAS_TOP,)]
                       + [f"0x{fxed.FX_ROOT_FIELD + FX_ALIAS * 4:08X}"],
        "block": {"start": f"0x{block_start:08X}", "end": f"0x{block_end:08X}",
                  "bytes": block_end - block_start,
                  "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end}}
    audits["dead_leaf"] = proof
    return audits


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260830_final_production")
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
     preserved, bs, be, kanji, bounds, layout, internal, proof) = first
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rs]
    if sha(product) != sha(second[0]) or strip(records) != strip(second[4]):
        raise RuntimeError("SFR_BUILD_NONDETERMINISTIC")
    if (bs, be) != (second[10], second[11]) or leaf_records != second[7]:
        raise RuntimeError("SFR_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    decode, _reverse = sysbat.charset_tables()
    audits = validate(product, base, meta, alloc, records, rows, leaf_records,
                      expectations, preserved, bs, be, decode, doc, layout, proof)
    if sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(
            f"CANONICAL_PRODUCTION_MISMATCH {sha(product)} != {EXPECTED_PRODUCTION}")

    determinism = {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                   "identical": True, "record_set_identical": True,
                   "pointer_layout_identical": leaf_records == second[7],
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
                  "by_top": {str(t): sum(1 for r in records if r["us_top"] == t) for t in TARGET_TOPS},
                  "scenes": sorted({r["scene"] for r in records}),
                  "us_only_proof": {r: sum(1 for x in records if x["us_only_reason"] == r)
                                    for r in sorted({x["us_only_reason"] for x in records})},
                  "non_production_internal": len(internal),
                  "story_align_hints": [list(map(list, h)) for h in STORY_HINTS],
                  "alias_restorations": layout["layout"]["_alias"]},
        "leaves": leaf_records,
        "width_bounds": bounds,
        "kanji_verified_from_jp_originals": len(kanji),
        "audits": {k: v["result"] for k, v in audits.items()},
        "determinism": determinism,
        "coverage": {"note": "Recompute the exact remainder with "
                             "ffta_jp_us_only_editorial_scope.py -- never by arithmetic."},
    }
    write(out / "translation_manifest_echo.json",
          {"entries": strip(records), "internal_entries": internal,
           "kanji_codes": doc["kanji_codes"]})
    write(out / "production_readback.json", audits["readback"])
    write(out / "sibling_alias_audit.json", audits["sibling"])
    write(out / "other_families_audit.json", audits["other_families"])
    write(out / "glyph_audit.json", audits["glyph"])
    write(out / "binary_touch.json", audits["binary"])
    write(out / "dead_leaf_proof.json", audits["dead_leaf"])
    write(out / "determinism.json", determinism)
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
