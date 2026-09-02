#! python3
"""Bulk-import only parser-confirmed original-JP AUTO_MATCH entries into US."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import ffta_jp_coverage_audit as coverage
from ffta_modifier import CONF, c_tab_align_iter, c_ffta_modifier
from ffta_sect import load_rom_jp, load_rom_us

MAIN = Path(__file__).resolve().parents[3]
JP_ROM = MAIN / "rom/original/FFTA_JP.gba"
US_ROM = MAIN / "rom/original/FFTA_US.gba"
OUT_ROM = MAIN / "rom/build/ffta_us_jp_bulk_automatch_poc.gba"
OUT_REPEAT = MAIN / "rom/build/ffta_us_jp_bulk_automatch_poc_repeat.gba"
OUT = MAIN / "analysis/dynamic/runtime/jp_bulk_automatch"
ALLOC_START = 0x122
FONT_STRIDE = 0x80
# 10637 before the words:rumor anchor correction: US 59/60 are
# US-only rumour titles with no JP original, and the placeholder JP
# records that used to pair with them are dropped.
EXPECTED_AUTO = 10636
EXPECTED_GLYPHS = 1463


def digest(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def path_text(path):
    return "/".join(map(str, path))


def tokens(line):
    return list(getattr(line, "text", line).tokens)


def record(font, index):
    return bytes(font.BYTES(index * FONT_STRIDE, FONT_STRIDE))


def direct_paths(rom):
    """Map coverage's merged group/path form back to a repacker table/path."""
    groups = {}
    for name, tab in c_ffta_modifier._iter_txttab(rom):
        parts = name.split("/")
        group = parts[0] + ("/" if len(parts) > 1 else "")
        outer = int(parts[1]) if len(parts) > 1 else 0
        groups.setdefault(group, {})[outer] = (name, tab)
    result = {}
    for group, tabs in groups.items():
        merged = len(tabs) > 1
        for outer, (name, tab) in tabs.items():
            for path, line in tab.iter_item(skiprep=True):
                path = tuple(path)
                result[(group, (outer, *path) if merged else path)] = (name, path, line)
    return result


def auto_pairs(jp, us):
    jtabs, _ = coverage.grouped_tabs(jp)
    utabs, _ = coverage.grouped_tabs(us)
    jdirect, udirect = direct_paths(jp), direct_paths(us)
    pairs = []
    counts = Counter()
    for name in sorted(set(jtabs) | set(utabs)):
        iterator = c_tab_align_iter(
            jtabs.get(name), utabs.get(name),
            align_map=CONF["text"]["align"].get(name, []),
            trim_page=CONF["text"]["trim"].get(name, []),
        ).iter()
        for (jpath, jline), (upath, uline) in iterator:
            je, ue = coverage.entry(jline), coverage.entry(uline)
            if not (je and ue) or "error" in je or "error" in ue:
                continue
            jpath, upath = tuple(jpath), tuple(upath)
            try:
                jname, jnative, _ = jdirect[(name, jpath)]
                uname, unative, _ = udirect[(name, upath)]
            except KeyError as exc:
                raise RuntimeError(f"coverage path cannot resolve: {name} {jpath} {upath}") from exc
            pairs.append({
                "section": name, "category": coverage.category(name),
                "jp_group_path": jpath, "us_group_path": upath,
                "jp_table": jname, "jp_path": jnative,
                "us_table": uname, "us_path": unative,
                "jp_line": jline, "us_line": uline,
            })
            counts[name] += 1
    if len(pairs) != EXPECTED_AUTO:
        raise RuntimeError(f"AUTO_MATCH mismatch: {len(pairs)} != {EXPECTED_AUTO}")
    return pairs, counts


def make_build():
    jp, us = load_rom_jp(JP_ROM), load_rom_us(US_ROM)
    pairs, section_counts = auto_pairs(jp, us)
    glyph_indices = sorted({value for pair in pairs if not isinstance(pair["jp_line"], list)
                            for kind, value in tokens(pair["jp_line"]) if kind == "CHR_FULL"})
    if len(glyph_indices) != EXPECTED_GLYPHS or 0x4B not in glyph_indices:
        raise RuntimeError(f"unexpected JP glyph set: {len(glyph_indices)}")
    jp_font, us_font = jp.tabs["font"], us.tabs["font"]
    if (jp_font.real_offset, us_font.real_offset) != (0x425030, 0x433330):
        raise RuntimeError("unexpected JP/US font bases")
    if jp_font._TAB_WIDTH != FONT_STRIDE or us_font._TAB_WIDTH != FONT_STRIDE:
        raise RuntimeError("unexpected font record stride")
    allocation = {index: ALLOC_START + pos for pos, index in enumerate(glyph_indices)}
    if allocation[glyph_indices[-1]] >= us_font.tsize:
        raise RuntimeError("US dynamic glyph capacity exceeded")

    updates, converted, references = defaultdict(dict), {}, []
    skipped = []
    for pair in pairs:
        if isinstance(pair["jp_line"], list):
            # Repeated/reference-only entries carry no token stream. Their
            # matching US reference semantics are intentionally left intact.
            references.append(pair)
            continue
        if any(kind == "CHR_HALF" for kind, _ in tokens(pair["jp_line"])):
            skipped.append({"section": pair["section"], "jp_path": path_text(pair["jp_group_path"]),
                            "us_path": path_text(pair["us_group_path"]), "reason": "CHR_HALF_US_ENCODING"})
            continue
        try:
            converted_tokens = [
                (kind, allocation[value]) if kind == "CHR_FULL" else (kind, value)
                for kind, value in tokens(pair["jp_line"])
            ]
        except (KeyError, TypeError, ValueError) as exc:
            skipped.append({"section": pair["section"], "jp_path": path_text(pair["jp_group_path"]),
                            "us_path": path_text(pair["us_group_path"]), "reason": type(exc).__name__})
            continue
        key = (pair["us_table"], pair["us_path"])
        if key in converted:
            raise RuntimeError(f"duplicate US destination: {key}")
        converted[key] = converted_tokens
        updates[pair["us_table"]][pair["us_path"]] = converted_tokens
    if len(skipped) > min(500, EXPECTED_AUTO // 20):
        raise RuntimeError(f"special-token skip threshold exceeded: {len(skipped)}")

    new_font = us_font.repack_copy()
    for jp_index, us_index in allocation.items():
        new_font.WBYTES(record(jp_font, jp_index), us_index * FONT_STRIDE)
    us_font._repack_end(new_font)
    changes = dict(updates)
    changes["font"] = new_font
    rebuilt, dirty = us.repack_with(changes)
    if not dirty:
        raise RuntimeError("reference repacker reported no changes")
    return rebuilt.raw, {
        "pairs": pairs, "section_counts": section_counts, "allocation": allocation,
        "converted": converted, "references": references, "skipped": skipped,
        "original_us": us, "jp": jp, "changed_tables": sorted(updates),
    }


def table(rom, name):
    current = rom.tabs
    for part in name.split(":"):
        current = current[part]
    return current


def validate(raw, meta):
    check = load_rom_us_bytes(raw)

    for (name, path), expected in meta["converted"].items():

        # Native table/path avoids ambiguity from output grouping changes.
        target = table(check, name)
        for index in path:
            target = target[index]
        if tokens(target) != expected:
            raise RuntimeError(f"converted tokens did not survive: {name} {path}")
        if not 0 <= target.real_offset < len(raw):
            raise RuntimeError(f"out-of-range rebuilt text pointer: {name} {path}")
    check_font = check.tabs["font"]
    for jp_index, us_index in meta["allocation"].items():
        if record(check_font, us_index) != record(meta["jp"].tabs["font"], jp_index):
            raise RuntimeError(f"native glyph mismatch JP {jp_index:04X}")
    if record(check_font, meta["allocation"][0x4B]) != record(meta["jp"].tabs["font"], 0x4B):
        raise RuntimeError("JP 0x4B oracle record mismatch")

    samples = []
    seen = set()
    for pair in meta["pairs"]:
        if isinstance(pair["jp_line"], list) or pair["category"] in seen:
            continue
        key = (pair["us_table"], pair["us_path"])
        if key not in meta["converted"]:
            continue
        samples.append({"kind": "converted", "category": pair["category"], "section": pair["section"],
                        "jp_path": path_text(pair["jp_group_path"]), "us_path": path_text(pair["us_group_path"]),
                        "result": "PASS"})
        seen.add(pair["category"])
    return check, samples


def load_rom_us_bytes(raw):
    # Reuse the established parser without altering its loader API.
    temp = OUT / "_parse_input.gba"
    temp.write_bytes(raw)
    try:
        return load_rom_us(temp)
    finally:
        temp.unlink()


def unchanged_samples(original, check, pairs):
    udirect = direct_paths(original)
    jtabs, _ = coverage.grouped_tabs(original)
    # The scope-critical unmatched samples: scenario and Mission page split.
    result = []
    for group in ("s_text", "pages:quest/"):
        for path, line in coverage.grouped_tabs(original)[0][group].items():
            if (group, path) not in udirect or isinstance(line, list):
                continue
            matched = any(p["section"] == group and p["us_group_path"] == path for p in pairs)
            if matched:
                continue
            name, native, before = udirect[(group, path)]
            _, _, after = direct_paths(check)[(group, path)]
            if tokens(before) != tokens(after):
                raise RuntimeError(f"unmatched US entry changed: {group} {path}")
            result.append({"kind": "preserved_us", "category": coverage.category(group), "section": group,
                           "jp_path": "", "us_path": path_text(path), "result": "PASS"})
            break
    return result


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as fd:
        writer = csv.DictWriter(fd, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    paths = (OUT_ROM, OUT_REPEAT, OUT / "summary.json", OUT / "skipped.csv", OUT / "sample_validation.csv")
    if any(path.exists() for path in paths):
        raise RuntimeError("refusing to overwrite existing bulk PoC artifacts")
    if digest(JP_ROM) != coverage.JP_SHA or digest(US_ROM) != coverage.US_SHA:
        raise RuntimeError("original ROM SHA-256 mismatch")
    OUT.mkdir(parents=True, exist_ok=True)
    raw_a, meta = make_build()
    raw_b, _ = make_build()
    if digest(raw_a) != digest(raw_b):
        raise RuntimeError("non-deterministic bulk rebuild")
    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(raw_a)
    OUT_REPEAT.write_bytes(raw_b)
    check, samples = validate(raw_a, meta)
    samples += unchanged_samples(meta["original_us"], check, meta["pairs"])
    relocated = [name for name in meta["changed_tables"]
                 if table(meta["original_us"], name).real_offset != table(check, name).real_offset]
    summary = {
        "status": "STATIC_VALIDATED", "auto_match_expected": EXPECTED_AUTO,
        "auto_match_observed": len(meta["pairs"]), "raw_text_imported": len(meta["converted"]),
        "preserved_reference_entries": len(meta["references"]), "skipped_special": len(meta["skipped"]),
        "jp_unique_glyphs": len(meta["allocation"]),
        "us_slots": {"first": f"0x{ALLOC_START:04X}", "last": f"0x{max(meta['allocation'].values()):04X}",
                     "capacity_remaining": check.tabs["font"].tsize - 1 - max(meta["allocation"].values())},
        "changed_text_tables": meta["changed_tables"], "relocated_tables": relocated,
        "rom": {"jp_sha256": digest(JP_ROM), "us_sha256": digest(US_ROM),
                "build_sha256": digest(raw_a), "build_size": len(raw_a),
                "us_original_size": US_ROM.stat().st_size, "path": str(OUT_ROM)},
        "known_s_text_1_3": "converted", "jp_4b_slot": f"0x{meta['allocation'][0x4B]:04X}",
    }
    write_csv(OUT / "skipped.csv", meta["skipped"], ["section", "jp_path", "us_path", "reason"])
    write_csv(OUT / "sample_validation.csv", samples, ["kind", "category", "section", "jp_path", "us_path", "result"])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
