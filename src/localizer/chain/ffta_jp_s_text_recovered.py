#! python3
"""Production localization for the recovered scene-script s_text correspondences.

The JP and US ROMs ship the *same* scene scripts.  A scene-script 0x0F call is
identified by ``(script_record, opcode_offset)``; across the two ROMs the
selected owner, the portrait operand and the flags operand agree, and only the
s_text line operand differs.  That differing operand is the correspondence.

The resulting 25 pairs are NOT recomputed here.  They are read from the tracked
manifest ``data/s_text_recovered_pairs.json``, which is the production decision;
the ignored ``build/s_text_semantic_recovery`` run remains the detailed proof.

Six US top owners (35, 37, 38, 39, 42, 50) are rebuilt as WHOLE pages -- every
line of each page is an approved target, including ordinal 0, which the historic
516-line candidate pool wrongly excluded.  JP owners 36..59 are logical repeats
of JP page 35 and are never used as sources.

This layer patches no ROM code, adds no renderer or converter hook, and reuses
the established leaf-repoint architecture: leaves are rebuilt in the native page
format and tail-relocated, and only the six root fields change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_small_matcher_recovery as small
from ffta_sect import c_ffta_sect_text_page

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
PAIRS = Path(__file__).resolve().parent / "data/s_text_recovered_pairs.json"
RUN_BASE = Path(__file__).resolve().parent / "build/s_text_recovered_production"
OUTROM = ROOT / "rom/build/ffta_us_jp_s_text_recovered.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_s_text_recovered_repeat.gba"

# Output of ffta_jp_small_matcher_recovery, the previous production layer.
BASELINE = "F2199AE17019238A3C8284D1B6F6116D185AE83FAF3EFD363775829D61E954F3"
# Private per-layer drift gate.  This module is no longer the terminal layer --
# ffta_jp_us_only_system_battle.py is the terminal layer and owns EXPECTED_PRODUCTION, the
# single canonical final-SHA authority.  Never quote the value below as "the
# production SHA".
EXPECTED_LAYER_OUTPUT = "E8FB19E2A88DBA56721CE05E87061CA8FAB4CE4A619E3CBDA98D33932FAB9CB7"

TARGET_TOPS = (35, 37, 38, 39, 42, 50)
TOTAL_PAIRS = 25
JP_SOURCE_TOP = 35


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_pairs():
    """The tracked production decision, revalidated on every build."""
    doc = json.loads(PAIRS.read_text(encoding="utf-8"))
    rows = doc["pairs"]
    if len(rows) != TOTAL_PAIRS:
        raise RuntimeError(f"PAIR_MANIFEST_COUNT {len(rows)} != {TOTAL_PAIRS}")
    if {r["jp_source_path"] for r in rows}.__len__() != TOTAL_PAIRS:
        raise RuntimeError("PAIR_MANIFEST_JP_SOURCE_REUSED")
    if {r["us_target_path"] for r in rows}.__len__() != TOTAL_PAIRS:
        raise RuntimeError("PAIR_MANIFEST_US_TARGET_REUSED")
    if sorted({r["us_top_owner"] for r in rows}) != sorted(TARGET_TOPS):
        raise RuntimeError("PAIR_MANIFEST_UNEXPECTED_TOPS")
    for r in rows:
        top, line = r["jp_source_path"].split("/")
        if int(top) != JP_SOURCE_TOP or int(line) != r["jp_line_operand"]:
            raise RuntimeError(f"PAIR_MANIFEST_JP_PATH {r['jp_source_path']}")
        top, line = r["us_target_path"].split("/")
        if int(top) != r["us_top_owner"] or int(line) != r["us_line_operand"]:
            raise RuntimeError(f"PAIR_MANIFEST_US_PATH {r['us_target_path']}")
        if r["confidence_class"] != "A_SEMANTIC_EXACT_WITH_CONTEXT":
            raise RuntimeError(f"PAIR_MANIFEST_WEAK_CLASS {r['confidence_class']}")
    # ordinal 0 of every target page must be present
    zeros = {r["us_target_path"] for r in rows if r["us_line_operand"] == 0}
    if zeros != {f"{t}/0" for t in TARGET_TOPS}:
        raise RuntimeError(f"PAIR_MANIFEST_MISSING_ORDINAL_ZERO {sorted(zeros)}")
    return doc, rows


def build():
    small.bind_current_root()
    jp, us = coverage.load_rom_jp(JP), coverage.load_rom_us(US)
    corr, _inventory = small.correspondence(jp, us)
    prev = small.build(corr)
    base, meta, alloc0, cursor = prev[0], prev[2], prev[4], prev[9]
    if sha(base) != BASELINE:
        raise RuntimeError(f"PRODUCTION_BASELINE_MISMATCH {sha(base)}")
    jp, us = meta["jp"], meta["us"]
    doc, rows = load_pairs()

    root = us.tabs["s_text"]
    jroot = jp.tabs["s_text"]
    us_raw, jp_raw = US.read_bytes(), JP.read_bytes()
    raw = bytearray(base)

    # Every target page must be a plain leaf page, unshared, and fully covered.
    by_top = {t: sorted((r for r in rows if r["us_top_owner"] == t),
                        key=lambda r: r["us_line_operand"]) for t in TARGET_TOPS}
    for top, group in by_top.items():
        page = root[top]
        if not isinstance(page, c_ffta_sect_text_page):
            raise RuntimeError(f"S_TEXT_TARGET_NOT_A_LEAF {top}")
        if [r["us_line_operand"] for r in group] != list(range(page.tsize)):
            raise RuntimeError(f"S_TEXT_TARGET_NOT_WHOLE_PAGE {top}")

    # Glyph allocation: append-only, high slots, never touching an existing map.
    needed = set()
    for r in rows:
        line = jroot[JP_SOURCE_TOP][r["jp_line_operand"]]
        toks = list(getattr(line, "text", line).tokens)
        if any(k == "CHR_HALF" for k, _ in toks):
            raise RuntimeError(f"CHR_HALF in {r['jp_source_path']}; that frontier is closed")
        needed.update(v for k, v in toks if k == "CHR_FULL")
    alloc = dict(alloc0)
    before = len(alloc)
    first_free = max(alloc.values()) + 1
    new = sorted(needed - set(alloc))
    font = us.tabs["font"]
    installed = []
    for n, g in enumerate(new):
        slot = first_free + n
        if slot >= font.tsize:
            raise RuntimeError("S_TEXT_RECOVERED_GLYPH_CAPACITY_FAILED")
        alloc[g] = slot
        raw[font.real_offset + slot * stext.FONT_STRIDE:
            font.real_offset + (slot + 1) * stext.FONT_STRIDE] = \
            jp_raw[jp.tabs["font"].real_offset + g * stext.FONT_STRIDE:
                   jp.tabs["font"].real_offset + (g + 1) * stext.FONT_STRIDE]
        raw[stext.US_METADATA + slot] = jp_raw[stext.JP_METADATA + g]
        installed.append({"jp_glyph": g, "production_slot": f"0x{slot:04X}",
                          "metadata": f"0x{jp_raw[stext.JP_METADATA + g]:02X}"})
    if set(alloc0.items()) - set(alloc.items()):
        raise RuntimeError("S_TEXT_RECOVERED_EXISTING_MAPPING_MOVED")
    last_slot = first_free + len(new) - 1 if new else first_free - 1

    # Rebuild the six whole pages and tail-relocate them.
    block_start = stext.align(cursor, 4)
    cursor = block_start
    manifest = []
    for top in TARGET_TOPS:
        page = root[top]
        repl, expected = {}, {}
        for r in by_top[top]:
            jline = jroot[JP_SOURCE_TOP][r["jp_line_operand"]]
            repl[r["us_line_operand"]] = stext.replacement_line(jline, alloc)
            expected[r["us_line_operand"]] = [
                (k, alloc[v]) if k == "CHR_FULL" else (k, v)
                for k, v in getattr(jline, "text", jline).tokens]
        blob = stext.serialize_leaf(us_raw, page, repl)
        cursor = stext.align(cursor, 4)
        field = root.real_offset + top * 4
        old_relative = int.from_bytes(raw[field:field + 4], "little")
        new_relative = cursor - root.real_offset
        raw[cursor:cursor + len(blob)] = blob
        raw[field:field + 4] = new_relative.to_bytes(4, "little")
        manifest.append({"us_top": top, "entries": page.tsize, "replaced": len(repl),
                         "preserved": page.tsize - len(repl),
                         "root_field_us_rom": f"0x{field:08X}",
                         "original_leaf_us_rom": f"0x{page.real_offset:08X}",
                         "original_size": page.sect_top,
                         "new_leaf_us_rom": f"0x{cursor:08X}",
                         "new_size": len(blob),
                         "old_relative": f"0x{old_relative:08X}",
                         "new_relative": f"0x{new_relative:08X}",
                         "_page": page, "_blob": blob, "_repl": repl, "_expected": expected})
        cursor += len(blob)
    block_end = cursor
    if block_end - stext.TAIL_START > stext.TAIL_CAPACITY:
        raise RuntimeError("S_TEXT_RECOVERED_TAIL_CAPACITY_EXCEEDED")
    if len(raw) != len(base):
        raise RuntimeError("ROM size changed")
    return (bytes(raw), base, meta, alloc0, alloc, installed, manifest, rows, doc,
            before, first_free, last_slot, block_start, block_end)


def validate(product, base, meta, alloc0, alloc, manifest, rows):
    """Independently re-read the produced bytes; never trust the build maps."""
    us = meta["us"]
    root = us.tabs["s_text"]
    pristine = US.read_bytes()
    audits = {}

    # -- leaf / root pointer / roundtrip -----------------------------------
    leaf_rows = []
    for x in manifest:
        field = int(x["root_field_us_rom"], 16)
        start = int(x["new_leaf_us_rom"], 16)
        rel = int.from_bytes(product[field:field + 4], "little")
        if rel != start - root.real_offset:
            raise RuntimeError(f"S_TEXT_RECOVERED_ROOT_POINTER {x['us_top']}")
        if not stext.TAIL_START <= start < stext.TAIL_START + stext.TAIL_CAPACITY:
            raise RuntimeError(f"S_TEXT_RECOVERED_OUTSIDE_TAIL {x['us_top']}")
        if product[start:start + len(x["_blob"])] != x["_blob"]:
            raise RuntimeError(f"S_TEXT_RECOVERED_BLOB_MISMATCH {x['us_top']}")
        # the established validator: offsets, bounded EOS, and token roundtrip
        stext.validate_leaf(x["_blob"], pristine, x["_page"], x["_repl"], x["_expected"])
        leaf_rows.append({"us_top": x["us_top"], "entries": x["entries"],
                          "replaced": x["replaced"], "preserved": x["preserved"],
                          "root_relative_verified": f"0x{rel:08X}",
                          "leaf_start": x["new_leaf_us_rom"], "size": x["new_size"],
                          "roundtrip": "PASS"})
    audits["leaf"] = {"pages": leaf_rows, "result": "PASS"}

    # -- alias / repeat safety ---------------------------------------------
    shared = {}
    for top in range(root.tsize):
        try:
            item = root[top]
        except Exception:
            continue
        off = getattr(item, "real_offset", None)
        if off is not None:
            shared.setdefault(off, []).append(top)
    hazards = []
    for x in manifest:
        off = x["_page"].real_offset
        others = [t for t in shared.get(off, []) if t != x["us_top"]]
        if others:
            hazards.append({"us_top": x["us_top"], "shares_leaf_with": others})
    if hazards:
        raise RuntimeError(f"S_TEXT_RECOVERED_ALIAS_HAZARD {hazards}")
    # no root field outside the six may have moved relative to the baseline
    moved = []
    for top in range(root.tsize):
        field = root.real_offset + top * 4
        if product[field:field + 4] != base[field:field + 4] and top not in TARGET_TOPS:
            moved.append(top)
    if moved:
        raise RuntimeError(f"S_TEXT_RECOVERED_FOREIGN_ROOT_MOVED {moved}")
    audits["alias"] = {"alias_hazards": 0, "foreign_root_fields_changed": 0,
                       "repeat_owner_used_as_source": False, "result": "PASS"}

    # -- asset (glyph + metadata) ------------------------------------------
    jp_raw = JP.read_bytes()
    jp = meta["jp"]
    fbase = us.tabs["font"].real_offset
    jfbase = jp.tabs["font"].real_offset
    gm = mm = 0
    for r in rows:
        line = jp.tabs["s_text"][JP_SOURCE_TOP][r["jp_line_operand"]]
        for kind, value in getattr(line, "text", line).tokens:
            if kind != "CHR_FULL":
                continue
            slot = alloc[value]
            if product[fbase + slot * stext.FONT_STRIDE: fbase + (slot + 1) * stext.FONT_STRIDE] != \
               jp_raw[jfbase + value * stext.FONT_STRIDE: jfbase + (value + 1) * stext.FONT_STRIDE]:
                raise RuntimeError(f"S_TEXT_RECOVERED_GLYPH_MISMATCH jp={value} slot={slot}")
            if product[stext.US_METADATA + slot] != jp_raw[stext.JP_METADATA + value]:
                raise RuntimeError(f"S_TEXT_RECOVERED_METADATA_MISMATCH jp={value}")
            gm += 1
            mm += 1
    # every previously established mapping must be byte-identical to the baseline
    for g, slot in alloc0.items():
        a = fbase + slot * stext.FONT_STRIDE
        if product[a:a + stext.FONT_STRIDE] != base[a:a + stext.FONT_STRIDE]:
            raise RuntimeError(f"S_TEXT_RECOVERED_EXISTING_GLYPH_CHANGED slot=0x{slot:04X}")
    audits["asset"] = {"glyph_matches": gm, "metadata_matches": mm, "errors": 0,
                       "existing_mapping_movement": 0, "chr_half_regression": 0,
                       "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    changed = []
    a = 0
    n = len(product)
    while a < n:
        if product[a] != base[a]:
            b = a
            while b < n and product[b] != base[b]:
                b += 1
            changed.append((a, b))
            a = b
        else:
            a += 1
    allowed_fields = {root.real_offset + t * 4 for t in TARGET_TOPS}
    new_slots = {alloc[g] for g in set(alloc) - set(alloc0)}
    # Adjacent relocated leaves are contiguous in the tail, so a run of differing
    # bytes legitimately crosses block boundaries.  Attribute per BYTE against the
    # merged union of permitted regions rather than per contiguous run.
    permitted = [(f, f + 4) for f in allowed_fields]
    permitted += [(int(x["new_leaf_us_rom"], 16),
                   int(x["new_leaf_us_rom"], 16) + x["new_size"]) for x in manifest]
    permitted += [(fbase + s * stext.FONT_STRIDE, fbase + (s + 1) * stext.FONT_STRIDE)
                  for s in new_slots]
    permitted += [(stext.US_METADATA + s, stext.US_METADATA + s + 1) for s in new_slots]
    permitted.sort()
    merged = []
    for lo, hi in permitted:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    def covered(lo, hi):
        for a, b in merged:
            if a <= lo and hi <= b:
                return True
        return False
    unexplained = []
    for lo, hi in changed:
        if not covered(lo, hi):
            unexplained.append((f"0x{lo:08X}", f"0x{hi:08X}", hi - lo))
    if unexplained:
        raise RuntimeError(f"S_TEXT_RECOVERED_UNEXPLAINED_BINARY_CHANGE {unexplained[:6]}")
    lowest = min((lo for lo, _ in changed), default=None)
    code_changes = [c for c in changed if c[0] < 0x400000 and
                    not any(f <= c[0] and c[1] <= f + 4 for f in allowed_fields)]
    if code_changes:
        raise RuntimeError(f"S_TEXT_RECOVERED_CODE_TOUCHED {code_changes[:4]}")
    audits["binary"] = {"result": "PASS", "changed_ranges": len(changed),
                        "root_pointer_fields": len(allowed_fields),
                        "relocated_leaf_blocks": len(manifest),
                        "new_font_slots": len(new_slots),
                        "unexplained_ranges": 0,
                        "rom_executable_code_bytes_changed": 0,
                        "lowest_changed_offset": f"0x{lowest:08X}" if lowest is not None else None}
    return audits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260830_run")
    args = ap.parse_args()
    out = RUN_BASE / args.run
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    out.mkdir(parents=True, exist_ok=True)

    first = build()
    second = build()
    (product, base, meta, alloc0, alloc, installed, manifest, rows, doc,
     before, first_free, last_slot, bs, be) = first
    strip = lambda m: [{k: v for k, v in x.items() if not k.startswith("_")} for x in m]
    if sha(product) != sha(second[0]) or strip(manifest) != strip(second[6]) or installed != second[5]:
        raise RuntimeError("S_TEXT_RECOVERED_BUILD_NONDETERMINISTIC")
    audits = validate(product, base, meta, alloc0, alloc, manifest, rows)
    if sha(product) != EXPECTED_LAYER_OUTPUT:
        raise RuntimeError(f"LAYER_OUTPUT_MISMATCH {sha(product)} != {EXPECTED_LAYER_OUTPUT}")

    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    determinism = {"sha256_1": sha(product), "sha256_2": sha(second[0]), "identical": True,
                   "manifest_identical": True, "glyph_map_identical": True,
                   "pair_manifest_interpretation_identical": rows == second[7]}
    write(out / "determinism.json", determinism)
    write(out / "target_set.json", [{k: v for k, v in r.items()} for r in rows])
    write(out / "pair_manifest_validation.json", {
        "source": str(PAIRS.relative_to(Path(__file__).resolve().parent)),
        "pairs": len(rows), "bijective": True,
        "validation": doc["validation"],
        "ordinal_zero_targets": sorted(r["us_target_path"] for r in rows if r["us_line_operand"] == 0),
        "result": "PASS"})
    write(out / "leaf_audit.json", audits["leaf"])
    write(out / "alias_audit.json", audits["alias"])
    write(out / "asset_audit.json", audits["asset"])
    write(out / "binary_touch.json", audits["binary"])
    write(out / "glyph_map.json", {"required": len(set(
        v for r in rows for k, v in getattr(meta["jp"].tabs["s_text"][JP_SOURCE_TOP][r["jp_line_operand"]],
                                            "text", None).tokens if k == "CHR_FULL")),
        "mappings_before": before, "mappings_after": len(alloc), "new": len(installed),
        "installed": installed, "first_new_slot": f"0x{first_free:04X}",
        "last_slot": f"0x{last_slot:04X}",
        "remaining_capacity": meta["us"].tabs["font"].tsize - 1 - last_slot,
        "existing_mappings_changed": 0, "low_slot_overwrites": 0})
    coverage_report = {"true_correct_before": 11143, "newly_correct": {"s_text": len(rows)},
                       "true_correct_after": 11143 + len(rows),
                       "analysis_approved_not_yet_produced_after": 0,
                       "closed_non_production": 72,
                       "unresolved_source_transfer": {"words:battle": 0, "pages:battle": 0, "total": 0,
                                                      "note": "closed by the battle align correction"},
                       "editorial_us_only_translation_scope":
                           "recompute with ffta_jp_us_only_editorial_scope.py; the historic "
                           "1,686 was an estimate. 1,766 before the words:name milestone, "
                           "1,169 after it"}
    write(out / "coverage_recomputed.json", coverage_report)
    summary = {"verdict": "S_TEXT_RECOVERED_STATIC_PRODUCTION_CONFIRMED",
               "baseline_sha256": sha(base), "production_sha256": sha(product),
               "pairs": len(rows), "us_tops": list(TARGET_TOPS),
               "block": {"start": f"0x{bs:08X}", "end": f"0x{be:08X}", "bytes": be - bs,
                         "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - be},
               "determinism": determinism, "audits": audits, "coverage": coverage_report}
    write(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
