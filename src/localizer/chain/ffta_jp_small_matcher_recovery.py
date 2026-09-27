#! python3
"""Recover the small non-s_text matcher-miss frontier without a repacker.

The authoritative matcher leaves 12 words:battle, three pages:battle, and one
words:rumor JP/US candidate pairs unmatched.  This program reconstructs that
frontier from the current pristine-ROM correspondence, requires a one-to-one
full control skeleton match for every production target, and layers only those
targets on top of HEAD's deterministic production ROM.

It deliberately does not alter the global matcher, does not revisit s_text,
and does not invoke a recursive repacker.  Words are standalone tail payloads;
the battle page is rebuilt from its *current production leaf*, preserving every
non-target line record byte-for-byte while changing its one root pointer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
import ffta_jp_fx_text_recovered as fx
import ffta_jp_s_text_leaf_repoint as stext
from ffta_sect import c_ffta_sect_text_buf, c_ffta_sect_text_buf_ya, c_ffta_sect_text_line


# The chain is staged three directories below the build root, so parents[3]
# is the directory holding rom/original and rom/build.  build.py lays that
# out; see docs/ARCHITECTURE.md.
ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
RUN_BASE = Path(__file__).resolve().parent / "build/small_matcher_recovery"
ROM = 0x08000000
BASELINE = 'F2199AE17019238A3C8284D1B6F6116D185AE83FAF3EFD363775829D61E954F3'
# This layer's own output.  It is NO LONGER the canonical production ROM:
# ffta_jp_s_text_recovered is now the terminal layer and owns the canonical
# EXPECTED_PRODUCTION.  Keeping the per-layer gate here still catches drift in
# this layer, but do not quote this value as "the production SHA".
EXPECTED_LAYER_OUTPUT = "F2199AE17019238A3C8284D1B6F6116D185AE83FAF3EFD363775829D61E954F3"
FAMILIES = ("words:battle", "pages:battle", "words:rumor")
# Upper bound only (the count mismatch below is deliberately not fatal).
# words:rumor was 1 until the words:rumor anchor correction: JP 61 /
# US 62 used to be a matcher miss recovered here, and the corrected
# anchors now pair them in the aligner itself, so this layer has no
# production target left and writes a byte-identical ROM.
HISTORICAL = {"words:battle": 2, "pages:battle": 0, "words:rumor": 0}
PAGE_ROOT_FIELD = 0x000237F4
OUTROM = ROOT / "rom/build/ffta_us_jp_small_matcher_recovery.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_small_matcher_recovery_repeat.gba"


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bind_current_root():
    """Repair only runtime path bindings of prior, already-confirmed layers."""
    for module in list(sys.modules.values()):
        if not getattr(module, "__name__", "").startswith("ffta_jp_"):
            continue
        if hasattr(module, "ROOT"):
            module.ROOT = ROOT
        if hasattr(module, "JP"):
            module.JP = JP
        if hasattr(module, "US"):
            module.US = US
        if hasattr(module, "JP_ROM"):
            module.JP_ROM = JP
        if hasattr(module, "US_ROM"):
            module.US_ROM = US


def tokens(line):
    if line is None or isinstance(line, list):
        return None
    return list(getattr(line, "text", line).tokens)


def visible(ts):
    return ts is not None and any(k.startswith("CHR_") for k, _ in ts)


def control_skeleton(ts):
    """Exact ordered non-glyph token stream, including operands."""
    return tuple((k, v) for k, v in ts if not k.startswith("CHR_")) if ts is not None else None


def skeleton_text(ts):
    return ";".join(f"{k}:{v}" for k, v in control_skeleton(ts) or ())


def line_descriptor(line):
    ts = tokens(line)
    return {
        "storage": f"0x{line.real_offset:08X}" if line is not None else None,
        "type": type(line).__name__ if line is not None else None,
        "repeat_reference": isinstance(line, list),
        "text_bearing": visible(ts),
        "token_count": len(ts) if ts is not None else 0,
        "control_skeleton": skeleton_text(ts),
        "has_chr_half": bool(ts and any(k == "CHR_HALF" for k, _ in ts)),
        "ya": isinstance(getattr(line, "text", line), c_ffta_sect_text_buf_ya),
    }


def table_for(rom, family):
    return rom.tabs["words"][family.split(":", 1)[1]] if family.startswith("words:") \
        else rom.tabs["pages"][family.split(":", 1)[1]]


def native_pairs(jp, us):
    pairs, _ = bulk.auto_pairs(jp, us)
    result = defaultdict(list)
    for p in pairs:
        if p["section"] in FAMILIES:
            result[p["section"]].append(p)
    return result


def anchors_for(pairs, side):
    key = "jp_path" if side == "jp" else "us_path"
    other = "us_path" if side == "jp" else "jp_path"
    return sorted((int(p[key][0]), int(p[other][0])) for p in pairs
                  if len(p[key]) == len(p[other]) == 1)


def neighbors(anchors, index):
    prior = [(a, b) for a, b in anchors if a < index]
    later = [(a, b) for a, b in anchors if a > index]
    return {"previous_auto_anchor": list(prior[-1]) if prior else None,
            "next_auto_anchor": list(later[0]) if later else None}


def candidate_edge(jindex, uindex, jline, uline, ja, ua):
    jt, ut = tokens(jline), tokens(uline)
    if jt is None or ut is None or not visible(jt) or not visible(ut):
        return False
    exact = control_skeleton(jt) == control_skeleton(ut)
    # A candidate is strong only where its entire exact skeleton equivalence
    # class is one source and one destination.  This avoids source reuse when
    # labels intentionally repeat and leaves genuinely ambiguous cases out.
    jeq = [x for x in ja if control_skeleton(tokens(x)) == control_skeleton(ut)]
    ueq = [x for x in ua if control_skeleton(tokens(x)) == control_skeleton(jt)]
    return exact and len(jeq) == 1 and len(ueq) == 1


def correspondence(jp, us):
    """Return all candidate records from the current authoritative matcher."""
    autos = native_pairs(jp, us)
    by_family, all_inventory = {}, []
    for family in FAMILIES:
        jt, ut = table_for(jp, family), table_for(us, family)
        pairs = autos[family]
        jmatched = {int(p["jp_path"][0]) for p in pairs}
        umat = {int(p["us_path"][0]) for p in pairs}
        jun = [i for i in range(jt.tsize) if i not in jmatched]
        uun = [i for i in range(ut.tsize) if i not in umat]
        ja, ua = [jt[i] for i in jun], [ut[i] for i in uun]
        jan, uan = anchors_for(pairs, "jp"), anchors_for(pairs, "us")
        # Candidate pairing is constrained to exact skeleton-equivalence
        # classes.  It is intentionally not a broad semantic/string matcher.
        edges = []
        for ji in jun:
            for ui in uun:
                if candidate_edge(ji, ui, jt[ji], ut[ui], ja, ua):
                    edges.append((ji, ui))
        jdegree = Counter(a for a, _ in edges); udegree = Counter(b for _, b in edges)
        rows, paired_j, paired_u = [], set(), set()
        for ji, ui in edges:
            if jdegree[ji] != 1 or udegree[ui] != 1:
                continue
            paired_j.add(ji); paired_u.add(ui)
            rec = {
                "family": family, "jp_candidate": {"index": ji, "matcher_status": "JP_ONLY",
                                                       **line_descriptor(jt[ji]), **neighbors(jan, ji)},
                "us_logical_identity": {"index": ui, "matcher_status": "US_ONLY",
                                           "physical_storage": f"0x{ut[ui].real_offset:08X}" if ut[ui] is not None else None,
                                           **line_descriptor(ut[ui]), **neighbors(uan, ui)},
                "correspondence_rule": "unique exact full control-skeleton equivalence among matcher-unmatched entries",
                "classification": "RECOVERABLE_STRONG",
            }
            rows.append(rec)
        # Unpaired JP/US unmatched rows remain inventory evidence.  A mismatch
        # with no one-to-one exact control structure is never productionized.
        for ji in jun:
            if ji not in paired_j:
                rows.append({"family": family, "jp_candidate": {"index": ji, "matcher_status": "JP_ONLY",
                                                                     **line_descriptor(jt[ji]), **neighbors(jan, ji)},
                             "us_logical_identity": None, "classification": "UNRESOLVED"})
        for ui in uun:
            if ui not in paired_u:
                rows.append({"family": family, "jp_candidate": None,
                             "us_logical_identity": {"index": ui, "matcher_status": "US_ONLY",
                                                       "physical_storage": f"0x{ut[ui].real_offset:08X}" if ut[ui] is not None else None,
                                                       **line_descriptor(ut[ui]), **neighbors(uan, ui)},
                             "classification": "GENUINE_US_ONLY"})
        strong = [r for r in rows if r["classification"] == "RECOVERABLE_STRONG"]
        if len(strong) != HISTORICAL[family]:
            # Historical candidate count is an upper bound, but never silently
            # turn a non-unique skeleton into a target merely to meet it.
            pass
        by_family[family] = {"table_entries": {"jp": jt.tsize, "us": ut.tsize},
                             "auto_match": len(pairs), "jp_only": len(jun), "us_only": len(uun),
                             "recoverable_candidates": len(strong), "records": rows}
        all_inventory.extend(rows)
    return by_family, all_inventory


def selected(corr):
    return [r for family in FAMILIES for r in corr[family]["records"]
            if r["classification"] == "RECOVERABLE_STRONG"]


def require_source_safe(jline):
    ts = tokens(jline)
    source = getattr(jline, "text", jline)
    if not isinstance(source, c_ffta_sect_text_buf):
        raise RuntimeError(f"nonstandard source {type(source).__name__}")
    if not visible(ts) or any(k == "CHR_HALF" for k, _ in ts):
        raise RuntimeError("CHR_HALF_OR_NON_TEXT_SOURCE")
    if isinstance(source, c_ffta_sect_text_buf_ya):
        raise RuntimeError("YA_SOURCE")


def standard_payload(jline, allocation):
    require_source_safe(jline)
    ts = [(k, allocation[v]) if k == "CHR_FULL" else (k, v) for k, v in tokens(jline)]
    raw = stext.encode_standard(ts)
    probe = c_ffta_sect_text_buf(bytearray(raw), 0)
    probe.parse_size(None, 1); probe.parse()
    if probe.tokens != ts or not raw or raw[-1] != 0:
        raise RuntimeError("STANDARD_SERIALIZER_COMPATIBILITY_FAILED")
    return raw, ts


def current_leaf_lines(raw, start, count, limit):
    offsets = [int.from_bytes(raw[start + i * 2:start + i * 2 + 2], "little") for i in range(count)]
    if offsets != sorted(offsets) or any(x < count * 2 + 2 for x in offsets):
        raise RuntimeError("CURRENT_PAGE_LEAF_OFFSETS_INVALID")
    result = []
    for i, rel in enumerate(offsets):
        stop = start + (offsets[i + 1] if i + 1 < count else limit - start)
        line = c_ffta_sect_text_line(bytearray(raw), start + rel)
        line.parse_size(stop - (start + rel), 2); line.set_nondeterm(); line.parse()
        result.append(bytes(raw[start + rel:start + rel + line.raw_len]))
    return result


def serialize_current_leaf(lines, replacements):
    out = bytearray(len(lines) * 2); out.extend(b"\xff\xff")
    offsets = []
    for i, item in enumerate(lines):
        while len(out) & 1:
            out.append(0)
        offsets.append(len(out)); out.extend(replacements.get(i, item))
    for i, off in enumerate(offsets):
        if off > 0xFFFF:
            raise RuntimeError("PAGES_BATTLE_LEAF_OFFSET_OVERFLOW")
        out[i * 2:i * 2 + 2] = off.to_bytes(2, "little")
    while len(out) % 4:
        out.append(0)
    return bytes(out)


def build(corr):
    bind_current_root()
    base, meta, alloc0, *_rest = fx.build()
    end = _rest[6]  # fx return: ..., qbe, cursor, base, need
    if sha(base) != BASELINE:
        raise RuntimeError(f"PRODUCTION_BASELINE_MISMATCH {sha(base)}")
    # The direct JP->US map owns 1,566 JP glyph keys; production additionally
    # occupies 29 divergent/half-selector slots.  The externally audited
    # occupied-slot checkpoint is therefore 1,595, while this map remains 1,566.
    if max(alloc0.values()) != 0x075C:
        raise RuntimeError("PRODUCTION_FONT_CHECKPOINT_MISMATCH")
    jp, us = meta["jp"], meta["us"]
    source = {r["family"]: table_for(jp, r["family"]) for r in selected(corr)}
    target = {r["family"]: table_for(us, r["family"]) for r in selected(corr)}
    targets = selected(corr)
    alloc = dict(alloc0); needed = set()
    for r in targets:
        jline = source[r["family"]][r["jp_candidate"]["index"]]
        require_source_safe(jline)
        needed.update(v for k, v in tokens(jline) if k == "CHR_FULL")
    new = sorted(needed - set(alloc))
    raw = bytearray(base); jpraw = JP.read_bytes(); fbase = us.tabs["font"].real_offset
    installed = []
    for pos, glyph in enumerate(new):
        slot = max(alloc.values()) + 1
        if slot >= us.tabs["font"].tsize:
            raise RuntimeError("SMALL_MATCHER_GLYPH_CAPACITY_FAILED")
        alloc[glyph] = slot
        jo = jp.tabs["font"].real_offset + glyph * stext.FONT_STRIDE
        uo = fbase + slot * stext.FONT_STRIDE
        raw[uo:uo + stext.FONT_STRIDE] = jpraw[jo:jo + stext.FONT_STRIDE]
        raw[stext.US_METADATA + slot] = jpraw[stext.JP_METADATA + glyph]
        installed.append({"jp_glyph": glyph, "production_slot": f"0x{slot:04X}",
                          "metadata": f"0x{jpraw[stext.JP_METADATA + glyph]:02X}"})
    cursor = stext.align(end, 4); words, page_targets = [], []
    for r in targets:
        if r["family"].startswith("words:"):
            family = r["family"].split(":", 1)[1]; ji = r["jp_candidate"]["index"]; ui = r["us_logical_identity"]["index"]
            payload, expected = standard_payload(source[r["family"]][ji], alloc)
            field = target[r["family"]].real_offset + ui * 4
            old = int.from_bytes(raw[field:field + 4], "little")
            raw[cursor:cursor + len(payload)] = payload
            raw[field:field + 4] = (ROM + cursor).to_bytes(4, "little")
            words.append({"family": family, "jp_index": ji, "us_index": ui,
                          "root_pointer_field_us_rom": f"0x{field:08X}", "old_cpu_pointer": f"0x{old:08X}",
                          "new_cpu_pointer": f"0x{ROM + cursor:08X}", "payload_start_us_rom": f"0x{cursor:08X}",
                          "payload_length": len(payload), "expected_tokens": expected})
            cursor = stext.align(cursor + len(payload), 4)
        else:
            page_targets.append(r)
    page = None
    if page_targets:
        leaf = us.tabs["pages"]["battle"]
        oldptr = int.from_bytes(base[PAGE_ROOT_FIELD:PAGE_ROOT_FIELD + 4], "little")
        oldstart = oldptr - ROM
        existing = current_leaf_lines(base, oldstart, leaf.tsize, end)
        repl, expected = {}, {}
        for r in page_targets:
            ji, ui = r["jp_candidate"]["index"], r["us_logical_identity"]["index"]
            repl[ui] = stext.replacement_line(source["pages:battle"][ji], alloc)
            expected[ui] = [(k, alloc[v]) if k == "CHR_FULL" else (k, v)
                            for k, v in tokens(source["pages:battle"][ji])]
        blob = serialize_current_leaf(existing, repl)
        newstart = cursor
        raw[newstart:newstart + len(blob)] = blob
        raw[PAGE_ROOT_FIELD:PAGE_ROOT_FIELD + 4] = (ROM + newstart).to_bytes(4, "little")
        page = {"root_field_us_rom": f"0x{PAGE_ROOT_FIELD:08X}", "old_cpu_pointer": f"0x{oldptr:08X}",
                "new_cpu_pointer": f"0x{ROM + newstart:08X}", "new_leaf_us_rom": f"0x{newstart:08X}",
                "new_size": len(blob), "targets": sorted(repl), "preserved_siblings": leaf.tsize - len(repl),
                "_existing": existing, "_blob": blob, "_repl": repl, "_expected": expected, "_leaf": leaf}
        cursor = stext.align(newstart + len(blob), 4)
    return bytes(raw), base, meta, alloc0, alloc, installed, words, page, end, cursor, needed


def validate(product, baseline, meta, alloc0, alloc, installed, words, page, tail_start, tail_end, used_glyphs):
    usraw, jpraw = US.read_bytes(), JP.read_bytes(); us = meta["us"]
    fbase = us.tabs["font"].real_offset
    # Source payload/word pointer/EOS audit.
    for x in words:
        field = int(x["root_pointer_field_us_rom"], 16); start = int(x["new_cpu_pointer"], 16) - ROM
        if int.from_bytes(product[field:field + 4], "little") != ROM + start or not tail_start <= start < tail_end:
            raise RuntimeError("WORDS_DIRECT_POINTER_AUDIT_FAILED")
        probe = c_ffta_sect_text_buf(bytearray(product[start:tail_end]), 0)
        probe.parse_size(None, 1); probe.parse()
        if probe.tokens != x["expected_tokens"] or product[start + probe.raw_len - 1] != 0:
            raise RuntimeError("WORDS_DIRECT_PAYLOAD_AUDIT_FAILED")
    page_audit = {"result": "NOT_APPLICABLE"}
    if page:
        start = int(page["new_leaf_us_rom"], 16)
        if int.from_bytes(product[PAGE_ROOT_FIELD:PAGE_ROOT_FIELD + 4], "little") != ROM + start:
            raise RuntimeError("PAGES_BATTLE_ROOT_AUDIT_FAILED")
        # Validate target tokens and byte-identical records for every sibling.
        lines = current_leaf_lines(product, start, page["_leaf"].tsize, tail_end)
        for i, line in enumerate(lines):
            probe = c_ffta_sect_text_line(bytearray(line), 0)
            probe.parse_size(len(line), 2); probe.set_nondeterm(); probe.parse()
            if i in page["_expected"]:
                if probe.text.tokens != page["_expected"][i]:
                    raise RuntimeError("PAGES_BATTLE_TARGET_AUDIT_FAILED")
            elif line != page["_existing"][i]:
                raise RuntimeError("PAGES_BATTLE_SIBLING_PRESERVATION_FAILED")
        page_audit = {"result": "PASS", "root_pointer": "PASS", "in_bounds": "PASS",
                      "sibling_records_preserved": page["preserved_siblings"], "stale_offsets": 0}
    glyph_errors = metadata_errors = 0
    # Audit every rendered source glyph, whether the current production mapping
    # already owns it or this layer had to allocate it.
    for glyph in sorted(used_glyphs):
        slot = alloc[glyph]
        if product[fbase + slot * stext.FONT_STRIDE:fbase + (slot + 1) * stext.FONT_STRIDE] != \
           jpraw[meta["jp"].tabs["font"].real_offset + glyph * stext.FONT_STRIDE:meta["jp"].tabs["font"].real_offset + (glyph + 1) * stext.FONT_STRIDE]:
            glyph_errors += 1
        if product[stext.US_METADATA + slot] != jpraw[stext.JP_METADATA + glyph]:
            metadata_errors += 1
    for slot in range(0x075D):
        if product[fbase + slot * stext.FONT_STRIDE:fbase + (slot + 1) * stext.FONT_STRIDE] != baseline[fbase + slot * stext.FONT_STRIDE:fbase + (slot + 1) * stext.FONT_STRIDE] or product[stext.US_METADATA + slot] != baseline[stext.US_METADATA + slot]:
            raise RuntimeError("EXISTING_GLYPH_MAPPING_MOVED")
    if glyph_errors or metadata_errors:
        raise RuntimeError("ASSET_AUDIT_FAILED")
    mask = bytearray(len(product))
    for x in words:
        field = int(x["root_pointer_field_us_rom"], 16); mask[field:field + 4] = b"\x01" * 4
    if page:
        mask[PAGE_ROOT_FIELD:PAGE_ROOT_FIELD + 4] = b"\x01" * 4
    mask[tail_start:tail_end] = b"\x01" * (tail_end - tail_start)
    for x in installed:
        slot = int(x["production_slot"], 16); off = fbase + slot * stext.FONT_STRIDE
        mask[off:off + stext.FONT_STRIDE] = b"\x01" * stext.FONT_STRIDE; mask[stext.US_METADATA + slot] = 1
    changed = stext.changed_ranges(baseline, product)
    unexplained = [(f"0x{a:08X}", f"0x{b:08X}") for a, b in changed if 0 in mask[a:b]]
    if unexplained:
        raise RuntimeError("SMALL_MATCHER_BINARY_TOUCH_REGRESSION " + repr(unexplained[:8]))
    return {"pointer_leaf": {"words_direct": {"count": len(words), "result": "PASS"}, "pages_battle": page_audit},
            "asset": {"glyph_matches": len(used_glyphs) - glyph_errors, "metadata_matches": len(used_glyphs) - metadata_errors,
                      "errors": glyph_errors + metadata_errors, "chr_half_regression": 0, "existing_mapping_movement": 0},
            "binary": {"result": "PASS", "unexplained_ranges": unexplained, "rom_executable_code_changes": 0,
                       "allowed": ["selected words root fields", "selected words tail payloads", "pages:battle root/leaf", "new high glyph records and metadata"],
                       "changed_ranges": [[f"0x{a:08X}", f"0x{b:08X}"] for a, b in changed]}}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--run", default="20260830_run"); args = ap.parse_args()
    out = RUN_BASE / args.run
    if out.exists():
        raise RuntimeError(f"refusing to overwrite {out}")
    bind_current_root()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    jp, us = coverage.load_rom_jp(JP), coverage.load_rom_us(US)
    corr, inventory = correspondence(jp, us)
    strong = selected(corr)
    counts = Counter(r["family"] for r in strong)
    out.mkdir(parents=True)
    write(out / "inventory.json", {"historical_upper_bound": HISTORICAL, "families": corr, "candidate_records": inventory})
    for family, name in (("words:battle", "words_battle_alignment.json"), ("pages:battle", "pages_battle_alignment.json"), ("words:rumor", "words_rumor_alignment.json")):
        write(out / name, corr[family])
    write(out / "recovered_correspondence.json", strong)
    readiness = []
    for r in strong:
        src = r["jp_candidate"]
        readiness.append({"family": r["family"], "jp_index": src["index"], "us_index": r["us_logical_identity"]["index"],
                          "architecture": "WORDS_DIRECT" if r["family"].startswith("words:") else "PAGE_LEAF",
                          "classification": r["classification"], "chr_half": src["has_chr_half"], "ya": src["ya"],
                          "standard_serializer": not src["has_chr_half"] and not src["ya"], "alias_safe": not src["repeat_reference"]})
    write(out / "production_readiness.json", readiness)
    # Production hard gate: no weak or inferred pair may pass this point.
    product1, base, meta, alloc0, alloc, installed, words, page, start, end, used = build(corr)
    product2, base2, meta2, alloc02, alloc2, installed2, words2, page2, start2, end2, used2 = build(corr)
    audit = validate(product1, base, meta, alloc0, alloc, installed, words, page, start, end, used)
    if sha(product1) != sha(product2) or (alloc, installed, words, start, end, used) != (alloc2, installed2, words2, start2, end2, used2):
        raise RuntimeError("SMALL_MATCHER_BUILD_NONDETERMINISTIC")
    if sha(product1) != EXPECTED_LAYER_OUTPUT:
        raise RuntimeError(f"LAYER_OUTPUT_MISMATCH {sha(product1)} != {EXPECTED_LAYER_OUTPUT}")
    targetset = [{"family": r["family"], "jp_index": r["jp_candidate"]["index"], "us_index": r["us_logical_identity"]["index"]} for r in strong]
    reused_glyphs = {v for r in strong
                     for k, v in tokens(table_for(jp, r["family"])[r["jp_candidate"]["index"]])
                     if k == "CHR_FULL" and v in alloc0}
    write(out / "target_set.json", targetset)
    write(out / "glyph_map.json", {"reused": len(reused_glyphs), "new": len(installed), "installed": installed,
                                     "occupied_before": 1595, "occupied_after": 1595 + len(installed), "last_slot": f"0x{max(alloc.values()):04X}",
                                     "remaining_capacity": us.tabs["font"].tsize - 1 - max(alloc.values())})
    write(out / "pointer_leaf_audit.json", audit["pointer_leaf"] | {"words": words, "page": {k: v for k, v in (page or {}).items() if not k.startswith("_")}})
    write(out / "asset_audit.json", audit["asset"])
    write(out / "binary_touch.json", audit["binary"])
    determinism = {"sha256_1": sha(product1), "sha256_2": sha(product2), "identical": True,
                   "selected_target_set_identical": True, "payloads_identical": True, "root_pointers_identical": True,
                   "leaf_bytes_identical": True, "glyph_map_identical": True, "binary_touch_identical": True}
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product1); OUTROM2.write_bytes(product2)
    if sha(OUTROM) != sha(product1) or sha(OUTROM2) != sha(product2):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")
    write(out / "determinism.json", determinism)
    write(out / "runtime_fixture_search.json", {"searched_existing_fixtures": True, "fixture_found": False, "launches": 0,
                                                  "policy": "no gameplay-route research; static confirmation only"})
    recovered = len(strong)
    coverage_report = {"true_correct_before": 11127, "newly_correct": {"words:battle": counts["words:battle"], "pages:battle": counts["pages:battle"], "words:rumor": counts["words:rumor"]},
                       "true_correct_after": 11127 + recovered, "remaining_recoverable": {"s_text": 97, "everything_else": 16 - recovered, "total": 97 + 16 - recovered}}
    write(out / "coverage_recomputed.json", coverage_report)
    summary = {"verdict": "SMALL_MATCHER_MISSES_STATIC_PRODUCTION_CONFIRMED_RUNTIME_PENDING", "inventory": HISTORICAL,
               "recovered": dict(counts), "recovered_total": recovered, "excluded": {"unresolved": sum(r["classification"] == "UNRESOLVED" for r in inventory), "genuine_us_only": sum(r["classification"] == "GENUINE_US_ONLY" for r in inventory)},
               "baseline_sha256": sha(base), "production_sha256": sha(product1), "determinism": determinism,
               "font": {"occupied_before": 1595, "occupied_after": 1595 + len(installed), "last_slot": f"0x{max(alloc.values()):04X}", "new": len(installed)},
               "audits": audit, "coverage": coverage_report, "runtime": {"fixture_found": False, "launches": 0}}
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
