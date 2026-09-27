#! python3
"""Read-only coverage inventory for original-JP-to-US text import."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import types
from collections import Counter, defaultdict
from pathlib import Path

# The modifier imports its optional CN TTF generator at module load. The
# read-only audit needs only its alignment definitions, never font generation.
_font_stub = types.ModuleType("ffta_font_generator")
_font_stub.make_ffta_font_gen = None
sys.modules.setdefault("ffta_font_generator", _font_stub)
from ffta_modifier import CONF, c_ffta_modifier, c_tab_align_iter
from ffta_sect import load_rom_jp, load_rom_us

MAIN = Path(__file__).resolve().parents[3]
JP_ROM = MAIN / "rom/original/FFTA_JP.gba"
US_ROM = MAIN / "rom/original/FFTA_US.gba"
OUT = MAIN / "analysis/dynamic/runtime/jp_adapter_coverage"
JP_SHA = "B13DD536808EF5D0FD4494386A9499F6FEB8310835D3F867CD17CC340D82BF9A"
US_SHA = "43FC8204C6DCEEE58828AEBC7AF0C72EB807E99F35AD641C8BB0A4FA8B6EDC19"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def category(name):
    if name == "s_text": return "scenario_dialogue"
    if name == "pages:battle" or name == "words:battle": return "battle"
    if name.startswith("pages:quest") or name == "words:quest": return "mission"
    if name in {"words:uitm", "words:content"}: return "item_description"
    if name in {"words:refer", "words:system", "pages:config", "pages:condi"}: return "system_help"
    if name in {"pages:choice", "words:ico", "words:title", "words:utitle"}: return "menu_ui"
    if name in {"words:name", "words:clan"}: return "names_labels"
    if name == "words:rumor": return "rumor"
    if name == "fx_text": return "system_message"
    return "unknown"


def grouped_tabs(rom):
    grouped, classes = {}, defaultdict(set)
    pending, previous = [], None
    for name, tab in c_ffta_modifier._iter_txttab(rom):
        parts = name.split("/")
        group = parts[0] + ("/" if len(parts) > 1 else "")
        index = int(parts[1]) if len(parts) > 1 else 0
        if previous is not None and group != previous:
            grouped[previous] = merge_tabs(pending)
            pending = []
        while len(pending) <= index:
            pending.append(None)
        pending[index] = tab
        classes[group].add(type(tab).__name__)
        previous = group
    if previous is not None:
        grouped[previous] = merge_tabs(pending)
    return grouped, classes


def merge_tabs(tabs):
    merged = len(tabs) > 1
    result = {}
    for outer, tab in enumerate(tabs):
        if tab is None:
            continue
        for path, line in tab.iter_item(skiprep=True):
            path = tuple(path)
            result[(outer, *path) if merged else path] = line
    return result


def entry(line):
    if line is None:
        return None
    if isinstance(line, list):
        return {"repeat": True, "raw_length": 0, "tokens": [], "control": ()}
    try:
        text = getattr(line, "text", line)
        tokens = list(text.tokens)
        return {
            "repeat": False,
            "raw_length": int(text.raw_len),
            "tokens": tokens,
            "control": tuple(kind for kind, _ in tokens if kind != "CHR_FULL"),
        }
    except Exception as exc:
        return {"error": type(exc).__name__}


def metrics(value):
    if not value or "error" in value:
        return 0, 0, ()
    return value["raw_length"], len(value["tokens"]), value["control"]


def path_text(path):
    return "/".join(map(str, path))


def add_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as fd:
        writer = csv.DictWriter(fd, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if OUT.exists():
        raise RuntimeError(f"refusing to overwrite existing audit output: {OUT}")
    if digest(JP_ROM) != JP_SHA or digest(US_ROM) != US_SHA:
        raise RuntimeError("original ROM SHA-256 mismatch")
    jp, us = load_rom_jp(JP_ROM), load_rom_us(US_ROM)
    jtabs, jclasses = grouped_tabs(jp)
    utabs, uclasses = grouped_tabs(us)
    rows, sections, glyphs = [], [], set()
    totals = Counter()
    for name in sorted(set(jtabs) | set(utabs)):
        jt, ut = jtabs.get(name), utabs.get(name)
        alignment = CONF["text"]["align"].get(name, [])
        iterator = c_tab_align_iter(jt, ut, align_map=alignment,
                                    trim_page=CONF["text"]["trim"].get(name, [])).iter()
        local = Counter()
        for (jpath, jline), (upath, uline) in iterator:
            je, ue = entry(jline), entry(uline)
            if je and "error" in je or ue and "error" in ue:
                status = "PARSE_ERROR"
            elif je and ue:
                status = "AUTO_MATCH"
            elif je:
                status = "JP_ONLY"
            elif ue:
                status = "US_ONLY"
            else:
                status = "AMBIGUOUS"
            jl, jtok, jctrl = metrics(je)
            ul, utok, uctrl = metrics(ue)
            structure = ""
            readiness = ""
            if status == "AUTO_MATCH":
                structure = "STANDARD_REPACK" if jctrl == uctrl else "STRUCTURAL_DIFFERENCE"
                readiness = "READY_FOR_AUTOMATED_IMPORT" if structure == "STANDARD_REPACK" else "NEEDS_SPECIAL_TOKEN_HANDLING"
                glyphs.update(value for kind, value in je["tokens"] if kind == "CHR_FULL")
            elif status == "US_ONLY": readiness = "US_ONLY_TRANSLATION"
            elif status == "AMBIGUOUS": readiness = "NEEDS_CORRESPONDENCE_RULE"
            else: readiness = "UNKNOWN"
            row = {
                "section": name, "category": category(name), "status": status,
                "readiness": readiness, "structure": structure,
                "jp_path": path_text(jpath) if jpath else "", "us_path": path_text(upath) if upath else "",
                "jp_bytes": jl, "us_bytes": ul, "jp_tokens": jtok, "us_tokens": utok,
                "jp_control": ";".join(jctrl), "us_control": ";".join(uctrl),
            }
            rows.append(row); totals[status] += 1; totals[readiness] += 1; local[status] += 1
        sections.append({"section": name, "category": category(name), "jp_entries": len(jt or {}),
                         "us_entries": len(ut or {}), "jp_class": ";".join(sorted(jclasses[name])),
                         "us_class": ";".join(sorted(uclasses[name])), "text_token_bearing": True,
                         "alignment_exceptions": len(alignment), **local})
    if sum(totals[s] for s in ("AUTO_MATCH", "US_ONLY", "JP_ONLY", "AMBIGUOUS", "PARSE_ERROR")) != len(rows):
        raise RuntimeError("classification totals do not sum")
    known = [r for r in rows if r["section"] == "s_text" and r["jp_path"] == "1/3" and r["us_path"] == "1/3"]
    if len(known) != 1 or known[0]["status"] != "AUTO_MATCH":
        raise RuntimeError("known s_text[1][3] correspondence missing")
    us_only = [r for r in rows if r["status"] == "US_ONLY"]
    summary = {
        "status": "COMPLETE", "rom_sha256": {"jp": JP_SHA, "us": US_SHA},
        "entries": {key: totals[key] for key in ("AUTO_MATCH", "US_ONLY", "JP_ONLY", "AMBIGUOUS", "PARSE_ERROR")},
        "readiness": {key: totals[key] for key in ("READY_FOR_AUTOMATED_IMPORT", "NEEDS_CORRESPONDENCE_RULE", "NEEDS_SPECIAL_TOKEN_HANDLING", "US_ONLY_TRANSLATION", "UNKNOWN")},
        "parser_visible": {"jp": sum(len(x) for x in jtabs.values()), "us": sum(len(x) for x in utabs.values())},
        "us_only_workload": {"entries": len(us_only), "encoded_bytes": sum(int(r["us_bytes"]) for r in us_only), "tokens": sum(int(r["us_tokens"]) for r in us_only), "categories": Counter(r["category"] for r in us_only)},
        "jp_auto_match_unique_glyph_indices": len(glyphs),
        "us_dynamic_allocation": {"base": "0x0122", "font_entries": us.tabs["font"].tsize, "capacity": us.tabs["font"].tsize - 0x122},
        "known_s_text_1_3": known[0],
    }
    OUT.mkdir(parents=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=dict) + "\n", encoding="utf-8")
    fields = list(rows[0])
    add_csv(OUT / "entries.csv", rows, fields)
    add_csv(OUT / "us_only.csv", us_only, fields)
    add_csv(OUT / "ambiguous.csv", [r for r in rows if r["status"] in {"AMBIGUOUS", "PARSE_ERROR"}], fields)
    add_csv(OUT / "sections.csv", sections, sorted({key for row in sections for key in row}))
    print(json.dumps(summary, indent=2, default=dict))


if __name__ == "__main__":
    main()
