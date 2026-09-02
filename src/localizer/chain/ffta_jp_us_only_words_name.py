#! python3
"""Editorial localization of the US-only ``words:name`` given-name pool.

This is the first *class 5* milestone: genuine US additions with no JP original
wording, localized by new translation rather than by source transfer.

``words:name`` is the pool of default given names the game assigns to generated
units.  JP ships 128 entries; US ships 725.  The first 128 are an AUTO_MATCH
pair set already produced by the CHR_HALF layers, so the JP originals occupy US
0..127.  Indices 128..724 -- 597 entries -- are US additions: they have no JP
counterpart at all, and every one of them still renders as English inside an
otherwise Japanese ROM.

The translations are NOT computed here.  They are read from the tracked
manifest ``data/us_only_words_name_translations.json``, which is the editorial
decision of record; this module only encodes and installs it.

Why this family needs no new architecture
-----------------------------------------
* The JP originals are written in half-width katakana, i.e. pure ``CHR_HALF``
  tokens, and so are the new translations.  A ``CHR_HALF`` token value ``v`` is
  the same charset index the ``CHR_FULL`` lane uses; katakana occupy 81..162 and
  the長音符 ``ー`` is 165 (0xA5).  The table is not assumed -- it is validated on
  every build against ``charset_us.json`` and against the JP ROM itself.
* Of the 85 slots in 81..165, exactly 84 are byte-identical between JP and US,
  so they render correctly whichever standard consumer reads them.  The single
  divergent slot is 0xA5, and the shipped A5 route already solves it: such a
  payload is serialized with FULL tokens, with the dash redirected to the
  dedicated high slot 0x06D9 that the A5 layer installed.
* ``words:*`` entries are direct-entry repoints: one 4-byte root field each.

Consequences: **zero new glyph records, zero font or metadata writes, zero ROM
code bytes patched**, and no new hook, converter or repacker work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_chr_half_universal_repoint as universal
import ffta_jp_chr_half_a5 as a5
import ffta_jp_s_text_recovered as prev
from ffta_sect import c_ffta_sect_text_buf
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_only_words_name_translations.json"
CHARSET = HERE / "charset_us.json"
RUN_BASE = HERE / "build/us_only_localization_words_name"
OUTROM = ROOT / "rom/build/ffta_us_jp_words_name_localized.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_words_name_localized_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_s_text_recovered, the previous production layer.
BASELINE = "E8FB19E2A88DBA56721CE05E87061CA8FAB4CE4A619E3CBDA98D33932FAB9CB7"
# Private per-layer drift gate.  This module is no longer the terminal layer --
# ffta_jp_us_only_system_battle.py runs after it and owns EXPECTED_PRODUCTION,
# the single canonical final-SHA authority.  Never quote the value below as
# "the production SHA".
EXPECTED_LAYER_OUTPUT = "1786C8A35140E4EE437516A9F259C60EA5E6FBE2BD583A854069A1368562FE3F"

FAMILY = "name"
JP_POOL = 128                 # JP words:name entries, already produced into US 0..127
US_POOL = 725                 # US words:name entries
FIRST, LAST = 128, 724        # the US-only span this layer owns
TOTAL = LAST - FIRST + 1      # 597
KANA_LO, KANA_HI = 81, 165    # charset indices this family is allowed to use
DASH = 0xA5                   # the one divergent slot, handled by the A5 route
MAX_HALF_CHARS = 8            # JP originals reach 7; US originals reach 10


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def katakana_table():
    """charset index -> character, restricted to the katakana span.

    ``charset_us.json`` is tracked, and it is the same index space both text
    lanes use.  Restricting to 81..165 keeps this family from silently reaching
    into the hiragana, Latin or symbol ranges.
    """
    decode, _encode = json.loads(CHARSET.read_text(encoding="utf-8"))
    table = {int(k): v for k, v in decode.items() if KANA_LO <= int(k) <= KANA_HI}
    if len(table) != KANA_HI - KANA_LO + 1:
        raise RuntimeError(f"CHARSET_KATAKANA_SPAN_INCOMPLETE {len(table)}")
    reverse = {}
    for code, char in table.items():
        if char in reverse:
            raise RuntimeError(f"CHARSET_KATAKANA_AMBIGUOUS {char!r}")
        reverse[char] = code
    for code, char in ((82, "ア"), (161, "ン"), (162, "ヴ"), (165, "ー")):
        if table.get(code) != char:
            raise RuntimeError(f"CHARSET_KATAKANA_ANCHOR_FAILED {code}")
    return table, reverse


def validate_charset_against_rom(jp, table):
    """Re-derive the anchor readings from the JP ROM, never from memory.

    The JP ``words:name`` pool is the corpus this table was read off, so decode
    a few of its entries and require the known English/Japanese pairs to hold.
    """
    pool = jp.tabs["words"][FAMILY]
    checks = {0: "アーサー", 2: "アイザック", 34: "オスカー", 70: "ケネス", 104: "スチュアート"}
    for index, expected in checks.items():
        line = pool[index]
        tokens = list(getattr(line, "text", line).tokens)
        if any(kind != "CHR_HALF" for kind, _ in tokens):
            raise RuntimeError(f"JP_NAME_NOT_PURE_HALF {index}")
        got = "".join(table[value] for _, value in tokens)
        if got != expected:
            raise RuntimeError(f"CHARSET_ROM_VALIDATION_FAILED {index}: {got} != {expected}")
    return len(checks)


def load_manifest(reverse, us_pool_text):
    """The tracked editorial decision, revalidated on every build."""
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = doc["entries"]
    if len(rows) != TOTAL or doc["count"] != TOTAL:
        raise RuntimeError(f"NAME_MANIFEST_COUNT {len(rows)} != {TOTAL}")
    if [r["us_index"] for r in rows] != list(range(FIRST, LAST + 1)):
        raise RuntimeError("NAME_MANIFEST_INDEX_SET")
    seen = set()
    for r in rows:
        index = r["us_index"]
        if r["us_logical_path"] != f"words:name/{index}" or r["family"] != "words:name":
            raise RuntimeError(f"NAME_MANIFEST_PATH {index}")
        if r["status"] != "TRANSLATED_REVIEWED":
            raise RuntimeError(f"NAME_MANIFEST_UNREVIEWED {index}")
        if not _us_source.matches(r, us_pool_text[index]):
            raise RuntimeError(f"NAME_MANIFEST_ENGLISH_DRIFT {index}: "
                               f"{_us_source.digest(r)} != {_us_source.of(us_pool_text[index])}")
        japanese = r["japanese"]
        if not japanese:
            raise RuntimeError(f"NAME_MANIFEST_EMPTY {index}")
        if len(japanese) > MAX_HALF_CHARS:
            raise RuntimeError(f"NAME_MANIFEST_TOO_LONG {index}: {japanese}")
        missing = [c for c in japanese if c not in reverse]
        if missing:
            raise RuntimeError(f"NAME_MANIFEST_UNENCODABLE {index}: {missing}")
        if japanese[0] in "ァィゥェォャュョッヮー":
            raise RuntimeError(f"NAME_MANIFEST_BAD_INITIAL {index}: {japanese}")
        seen.add(japanese)
    # The pool must not hold two identical readings unless the US pool itself
    # repeats the English spelling at exactly those indices.
    by_reading = {}
    for r in rows:
        by_reading.setdefault(r["japanese"], []).append(r["us_index"])
    collisions = {k: v for k, v in by_reading.items() if len(v) > 1}
    if collisions:
        raise RuntimeError(f"NAME_MANIFEST_DUPLICATE_READING {collisions}")
    return doc, rows, len(seen)


def encode(japanese, reverse, alloc, universal_slots):
    """Serialize one name, choosing the shipped route its slots require."""
    tokens = [("CHR_HALF", reverse[c]) for c in japanese]
    values = {v for _, v in tokens}
    if any(v < KANA_LO or v > KANA_HI for v in values):
        raise RuntimeError(f"NAME_SLOT_OUT_OF_RANGE {japanese}")
    divergent = sorted(v for v in values if v not in universal_slots)
    if not divergent:
        route = "UNIVERSAL_HALF"
        data, _marker = universal.encode_half_payload(tokens, alloc)
        expected = tokens
    elif divergent == [DASH]:
        route = "A5_HOOKLESS_FULL"
        expected = a5.promote(tokens, alloc)
        data = stext.encode_standard(expected)
    else:
        raise RuntimeError(f"NAME_UNSUPPORTED_DIVERGENT_SLOTS {japanese}: {divergent}")
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"NAME_SERIALIZER_ROUNDTRIP_FAILED {japanese}")
    if data[-1] != 0:
        raise RuntimeError(f"NAME_EOS_MISSING {japanese}")
    return route, data, expected


def build():
    base, _b, meta, _a0, alloc, _inst, _man, _rows, _doc, _bf, _ff, _ls, _bs, tail_end = prev.build()
    if sha(base) != BASELINE:
        raise RuntimeError(f"PRODUCTION_BASELINE_MISMATCH {sha(base)}")
    jp, us = meta["jp"], meta["us"]
    jp_raw, us_raw = JP.read_bytes(), US.read_bytes()

    table, reverse = katakana_table()
    anchors = validate_charset_against_rom(jp, table)
    universal_slots = universal.universal_slots(jp_raw, us_raw)
    divergent_in_span = sorted(v for v in range(KANA_LO, KANA_HI + 1)
                               if v not in universal_slots)
    if divergent_in_span != [DASH]:
        raise RuntimeError(f"NAME_SLOT_LANDSCAPE_CHANGED {divergent_in_span}")

    pool = us.tabs["words"][FAMILY]
    if pool.tsize != US_POOL or jp.tabs["words"][FAMILY].tsize != JP_POOL:
        raise RuntimeError("NAME_POOL_SIZE_CHANGED")
    us_pool_text = {}
    for index in range(FIRST, LAST + 1):
        line = pool[index]
        tokens = list(getattr(line, "text", line).tokens)
        if any(kind != "CHR_HALF" for kind, _ in tokens):
            raise RuntimeError(f"US_NAME_NOT_PURE_HALF {index}")
        us_pool_text[index] = "".join(
            chr(v - 0xB0 + ord("A")) if 0xB0 <= v < 0xCA else
            chr(v - 0xCA + ord("a")) if 0xCA <= v < 0xE4 else
            chr(v - 0xA6 + ord("0")) if 0xA6 <= v < 0xB0 else "."
            for _, v in tokens)
    doc, rows, distinct = load_manifest(reverse, us_pool_text)

    raw = bytearray(base)
    cursor = block_start = stext.align(tail_end, 4)
    records = []
    for r in rows:
        index = r["us_index"]
        route, data, expected = encode(r["japanese"], reverse, alloc, universal_slots)
        field = pool.real_offset + index * 4
        old = int.from_bytes(raw[field:field + 4], "little")
        raw[cursor:cursor + len(data)] = data
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, "little")
        records.append({
            "us_index": index, "us_logical_path": r["us_logical_path"],
            "original_english_sha256": _us_source.digest(r), "japanese": r["japanese"],
            "route": route, "half_chars": len(r["japanese"]),
            "root_pointer_field_us_rom": f"0x{field:08X}",
            "original_cpu_pointer": f"0x{old:08X}",
            "new_cpu_pointer": f"0x{ROM + cursor:08X}",
            "payload_offset_us_rom": f"0x{cursor:08X}", "payload_length": len(data),
            "eos": True, "roundtrip": "PASS",
            "_expected": expected, "_data": data})
        cursor = stext.align(cursor + len(data), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("ROM size changed")
    if not stext.TAIL_START <= block_start and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY:
        raise RuntimeError("NAME_BLOCK_OUTSIDE_TAIL")
    return (bytes(raw), base, meta, alloc, records, doc, table, universal_slots,
            block_start, block_end, anchors, distinct)


def validate(product, base, meta, alloc, records, universal_slots, block_start, block_end):
    us = meta["us"]
    pool = us.tabs["words"][FAMILY]
    audits = {}

    # -- entry / roundtrip --------------------------------------------------
    routes = {}
    for rec in records:
        routes[rec["route"]] = routes.get(rec["route"], 0) + 1
        field = int(rec["root_pointer_field_us_rom"], 16)
        start = int(rec["payload_offset_us_rom"], 16)
        if int.from_bytes(product[field:field + 4], "little") != ROM + start:
            raise RuntimeError(f"NAME_ROOT_POINTER_AUDIT_FAILED {rec['us_index']}")
        if product[start:start + rec["payload_length"]] != rec["_data"]:
            raise RuntimeError(f"NAME_PAYLOAD_AUDIT_FAILED {rec['us_index']}")
        if not block_start <= start < block_end:
            raise RuntimeError(f"NAME_PAYLOAD_OUTSIDE_BLOCK {rec['us_index']}")
        probe = c_ffta_sect_text_buf(bytearray(product[start:start + rec["payload_length"]]), 0)
        probe.parse_size(None, 1)
        probe.parse()
        if probe.tokens != rec["_expected"]:
            raise RuntimeError(f"NAME_INSTALLED_ROUNDTRIP_FAILED {rec['us_index']}")
    audits["entry"] = {"entries": len(records), "routes": routes,
                       "roundtrip_failures": 0, "eos_failures": 0, "result": "PASS"}

    # -- sibling / alias ----------------------------------------------------
    owned = {r["us_index"] for r in records}
    moved_siblings = [i for i in range(pool.tsize)
                      if i not in owned and
                      product[pool.real_offset + i * 4: pool.real_offset + i * 4 + 4] !=
                      base[pool.real_offset + i * 4: pool.real_offset + i * 4 + 4]]
    if moved_siblings:
        raise RuntimeError(f"NAME_SIBLING_ROOT_MOVED {moved_siblings}")
    pointers = [int.from_bytes(product[pool.real_offset + i * 4:
                                       pool.real_offset + i * 4 + 4], "little")
                for i in range(pool.tsize)]
    if len(set(pointers)) != pool.tsize:
        raise RuntimeError("NAME_POINTER_ALIASING")
    # nothing this layer writes may land on a pristine-US payload another entry
    # still points at
    surviving = set(pointers[i] for i in range(pool.tsize) if i not in owned)
    for rec in records:
        lo = ROM + int(rec["payload_offset_us_rom"], 16)
        hi = lo + rec["payload_length"]
        if any(lo <= p < hi for p in surviving):
            raise RuntimeError(f"NAME_ALIAS_HAZARD {rec['us_index']}")
    audits["alias"] = {"family_entries": pool.tsize, "owned": len(owned),
                       "sibling_root_fields_changed": 0, "pointer_aliasing": 0,
                       "jp_original_span_preserved": JP_POOL,
                       "overwritten_pristine_payloads": 0, "result": "PASS"}

    # -- glyph / asset ------------------------------------------------------
    jp_raw, us_raw = JP.read_bytes(), US.read_bytes()
    fbase = us.tabs["font"].real_offset
    if product[fbase:fbase + us.tabs["font"].tsize * stext.FONT_STRIDE] != \
       base[fbase:fbase + us.tabs["font"].tsize * stext.FONT_STRIDE]:
        raise RuntimeError("NAME_FONT_TABLE_CHANGED")
    if product[stext.US_METADATA:stext.US_METADATA + us.tabs["font"].tsize] != \
       base[stext.US_METADATA:stext.US_METADATA + us.tabs["font"].tsize]:
        raise RuntimeError("NAME_FONT_METADATA_CHANGED")
    referenced = sorted({v for rec in records for k, v in rec["_expected"] if k == "CHR_HALF"})
    promoted = sorted({v for rec in records for k, v in rec["_expected"] if k == "CHR_FULL"})
    for v in referenced:
        if v not in universal_slots:
            raise RuntimeError(f"NAME_HALF_SLOT_NOT_UNIVERSAL 0x{v:02X}")
    for v in promoted:
        if v == a5.HIGH_SLOT:
            continue
        o = v * stext.FONT_STRIDE
        if product[fbase + o:fbase + o + stext.FONT_STRIDE] != \
           jp_raw[a5.JP_FONT + o:a5.JP_FONT + o + stext.FONT_STRIDE]:
            raise RuntimeError(f"NAME_FULL_SLOT_NOT_JP_IDENTICAL 0x{v:04X}")
    high = a5.HIGH_SLOT * stext.FONT_STRIDE
    if product[fbase + high:fbase + high + stext.FONT_STRIDE] == \
       us_raw[fbase + high:fbase + high + stext.FONT_STRIDE]:
        raise RuntimeError("NAME_A5_HIGH_SLOT_NOT_INSTALLED")
    audits["glyph"] = {"new_glyph_records": 0, "font_table_writes": 0,
                       "metadata_writes": 0, "low_slot_overwrites": 0,
                       "existing_mappings_changed": 0,
                       "distinct_half_slots_used": len(referenced),
                       "distinct_full_slots_used": len(promoted),
                       "a5_high_slot": f"0x{a5.HIGH_SLOT:04X}",
                       "a5_high_slot_preinstalled": True, "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    permitted = [(int(r["root_pointer_field_us_rom"], 16),
                  int(r["root_pointer_field_us_rom"], 16) + 4) for r in records]
    permitted.append((block_start, block_end))
    permitted.sort()
    merged = []
    for lo, hi in permitted:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    changed = list(stext.changed_ranges(base, product))
    unexplained = [(f"0x{lo:08X}", f"0x{hi:08X}")
                   for lo, hi in changed
                   if not any(a <= lo and hi <= b for a, b in merged)]
    if unexplained:
        raise RuntimeError(f"NAME_BINARY_TOUCH_REGRESSION {unexplained[:8]}")
    fields = {int(r["root_pointer_field_us_rom"], 16) for r in records}
    code = [c for c in changed if c[0] < 0x400000 and
            not any(f <= c[0] and c[1] <= f + 4 for f in fields)]
    if code:
        raise RuntimeError(f"NAME_CODE_TOUCHED {code[:4]}")
    audits["binary"] = {"result": "PASS", "changed_ranges": len(changed),
                        "root_pointer_fields": len(fields),
                        "relocated_payload_blocks": 1,
                        "new_font_slots": 0, "unexplained_ranges": 0,
                        "rom_executable_code_bytes_changed": 0,
                        "block": {"start": f"0x{block_start:08X}",
                                  "end": f"0x{block_end:08X}",
                                  "bytes": block_end - block_start,
                                  "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end}}
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
    (product, base, meta, alloc, records, doc, table, universal_slots,
     bs, be, anchors, distinct) = first
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rs]
    if sha(product) != sha(second[0]) or strip(records) != strip(second[4]):
        raise RuntimeError("NAME_BUILD_NONDETERMINISTIC")
    audits = validate(product, base, meta, alloc, records, universal_slots, bs, be)
    if sha(product) != EXPECTED_LAYER_OUTPUT:
        raise RuntimeError(f"LAYER_OUTPUT_MISMATCH {sha(product)} != {EXPECTED_LAYER_OUTPUT}")

    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    routes = audits["entry"]["routes"]
    determinism = {"sha256_1": sha(product), "sha256_2": sha(second[0]), "identical": True,
                   "record_set_identical": True,
                   "manifest_interpretation_identical": strip(records) == strip(second[4]),
                   "pointer_layout_identical": [r["new_cpu_pointer"] for r in records] ==
                                               [r["new_cpu_pointer"] for r in second[4]],
                   "glyph_allocation_identical": True,
                   "baseline_sha256": sha(base)}
    # Class 1 (JP source transfer) and class 5 (editorial translation) are kept
    # apart deliberately: these 597 carry no JP source, so folding them into the
    # 11,168 would destroy the meaning of that figure.
    coverage_report = {
        "class_1_production_jp_source_transfer": 11168,
        "class_5_editorial_produced_by_this_layer": {"words:name": len(records)},
        "total_localized_entries_in_production_rom": 11168 + len(records),
        "editorial_us_only_visible_before": 1169 + len(records),
        "editorial_us_only_visible_after": 1169,
        "unresolved_jp_source_transfer": 0,
        "closed_non_production": 74,
        "recomputed_by": "ffta_jp_us_only_editorial_scope.py (visible US_ONLY 2,386 = "
                         "1,149 localized + 68 already-Japanese + 1,169 remaining)",
        "note": "the historic 1,686 headline was an estimate: it assumed all 97 s_text "
                "candidates would be recovered (25 were produced, 72 closed on the JP side) "
                "and predates the battle align correction, which moved 15 entries out of "
                "US_ONLY. The exact figure before this layer is 1,766"}
    write(out / "batch_selection.json", {
        "family": "words:name", "us_index_range": [FIRST, LAST], "selected": len(records),
        "why": ["genuine US-only: JP ships 128 entries, US ships 725, and 128..724 have no "
                "JP counterpart at all",
                "player-visible: these are the default given names of every generated unit",
                "one coherent table, produced as a complete unit so the name pool holds no "
                "mixed English/Japanese remainder",
                "pure half-width katakana, so the established CHR_HALF direct-entry repoint "
                "and the shipped A5 route cover it with no new architecture",
                "zero new glyph records and zero ROM code changes"],
        "excluded_from_batch": {"words:name/0..127": "JP original counterpart already produced"}})
    write(out / "translation_review.json", {
        "source_manifest": str(MANIFEST.relative_to(HERE)),
        "entries": len(records), "status": "TRANSLATED_REVIEWED",
        "distinct_readings": distinct, "duplicate_readings": 0,
        "english_drift_vs_rom": 0,
        "length_half_chars": {"min": min(r["half_chars"] for r in records),
                              "max": max(r["half_chars"] for r in records),
                              "jp_original_max": 7, "us_original_max": 10},
        "charset_anchors_revalidated_against_jp_rom": anchors,
        "corrections_applied": [
            {"us_index": 390, "issue": "reading collided with JP original ダーシー (index 120)",
             "resolution": "ダーシィ"},
            {"us_index": 675, "issue": "US repeats the spelling \"Luan\" (also 282)",
             "resolution": "ルーアン"},
            {"us_index": 461, "issue": "ムーア over-lengthened a two-syllable name",
             "resolution": "ムア"}]})
    write(out / "production_audit.json", {"entry": audits["entry"], "alias": audits["alias"]})
    write(out / "glyph_audit.json", audits["glyph"])
    write(out / "binary_touch.json", audits["binary"])
    write(out / "determinism.json", determinism)
    write(out / "coverage.json", coverage_report)
    write(out / "target_set.json", strip(records))
    summary = {"verdict": "US_ONLY_WORDS_NAME_STATIC_PRODUCTION_CONFIRMED",
               "baseline_sha256": sha(base), "production_sha256": sha(product),
               "entries": len(records), "routes": routes,
               "determinism": determinism, "audits": audits, "coverage": coverage_report}
    write(out / "summary.json", summary)
    (out / "summary.md").write_text(
        "# US-only editorial localization -- words:name\n\n"
        f"- baseline: `{sha(base)}`\n"
        f"- production: `{sha(product)}`\n"
        f"- entries localized: {len(records)} (US words:name {FIRST}..{LAST})\n"
        f"- routes: {routes}\n"
        f"- new glyph records: 0; ROM executable code bytes changed: 0\n"
        f"- payload block: 0x{bs:08X}..0x{be:08X} ({be - bs} bytes)\n"
        f"- total localized entries: {coverage_report['total_localized_entries_in_production_rom']}\n"
        f"- remaining editorial US-only: {coverage_report['editorial_us_only_visible_after']}\n"
        f"- unresolved JP source transfer: 0\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
