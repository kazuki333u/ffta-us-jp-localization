#! python3
"""Does the localized build draw the glyphs JP retail draws?

Why this exists
---------------

A GBA screen is 240x160 and a kanji is 12x12.  In this session three strings
were read off screenshots as defects -- ``色魔法禁止`` misread as a wrong ``白``,
``レイピア禁止`` misread as ``レイビア``, ``薬草とり`` misread as ``菜草とり`` --
and all three were correct.  Eyes are not evidence at this resolution, and a
"visual anomaly" that is really a reading error costs a whole investigation.

So ask the data instead.  For every entry of a ``words:*`` family this decodes
the JP original into JP glyph codes and the localized build into US font slots,
renders both out of the two ROMs' own font sections, and compares the
**bitmaps**.  A family that comes back clean is proof that every character of it
is JP retail's own glyph, in JP retail's own order -- which retires the whole
class of "is that kanji right?" triage in one run.

Comparing bitmaps rather than character codes also avoids a trap: the JP font
draws ``O`` and ``○`` with identical pixels, so a code-level comparison reports
a difference where the screen cannot show one.

Index alignment
---------------

Where the US table carries entries the JP table does not, the two are aligned by
dropping those indices (``US_ONLY``) and, where the JP table has unused
placeholder slots that the US table fills with content, dropping those too
(``JP_SKIP``).  Both maps are data, and both are stated where they came from.

Usage
-----

    python ffta_qa_glyphs.py <product.gba>                 # every family
    python ffta_qa_glyphs.py <product.gba> --family content --verbose
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ffta_sect import (c_ffta_sect_fixed_text, c_ffta_sect_font, c_ffta_sect_rom,
                       _trim_raw_len, _words_sect_info, load_rom_jp)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
JP_ROM = ROOT / "rom/original/FFTA_JP.gba"

# Root pointer field and entry count of every US words table, as declared by
# ffta_sect.load_rom_us.  They are repeated here because the whole-ROM US loader
# trips over its own table-size heuristics on a production ROM (tail-relocated
# payloads sit outside the ranges it guesses); these five numbers do not.
US_WORDS_ROOTS = {"ico": (0x198D0, 0xA), "uitm": (0x538B8, 0x4),
                  "content": (0x18DA4, 0x2F2), "refer": (0x9A08, 0x6B),
                  "battle": (0x9028, 0x301), "rumor": (0x5FD9C, 0x5C),
                  "quest": (0x191F0, 0x20F), "clan": (0x9A10, 0x80),
                  "system": (0x39F54, 0x54), "name": (0xC9ED0, 0x2D5),
                  "title": (0x192BC, 0x34)}

FONT = {"us": (0x013474, {"shape": (4, 8, 16, 2), "size": 0xC67, "rvsbyt": False}),
        "jp": (0x0133F4, {"shape": (4, 8, 16, 2), "size": 0xC66, "rvsbyt": False})}

# US table entries with no JP counterpart.  PROJECT_STATE section 7 (the
# US-only system/battle milestone) names them: words:battle 176..178 and
# 534..542 are the judge / law ability names the US ROM adds, and words:rumor
# 59..60 are its two extra pub rumour titles.
US_ONLY = {"battle": [176, 177, 178] + list(range(534, 543)),
           "rumor": [59, 60]}
# JP table slots that hold an unused placeholder ("060") where the US table has
# real content; dropping them is what re-aligns words:rumor after its insertion.
JP_SKIP = {"rumor": [60, 62]}

FAMILIES = ("content", "battle", "quest", "refer", "rumor", "system",
            "clan", "name", "title", "uitm")

# `fx_text` is the other half of the player-visible text: the law help lines the
# world-map LAW screen prints (leaf 23), the tutorial and pub prose (leaf 8),
# the mission / quest-accept panel templates (leaf 9).  The US root holds more
# entries per leaf than the JP one; the leading `min(tsize)` align.
FX_ROOT_US = 0x018050
FX_COUNT_US = 27
# Leaves this project *rebuilds* rather than transfers, so a difference there is
# the milestone's own work, not a defect: leaf 9's JP page templates are padded
# per RC12 (`ffta_jp_mission_panel_templates.py`).
FX_REBUILT = {9}
# The US root has 27 leaves to the JP root's 26 -- one US-only leaf at 24 -- and
# US leaf 8 carries two extra entries at 58..59, the bodies of the two US-only
# pub rumours whose titles are words:rumor 59..60.  Both are the same insertion
# the words tables show, so the same kind of map re-aligns them.
FX_LEAF_US_ONLY = [24]
FX_ENTRY_US_ONLY = {8: [58, 59]}


def font_of(path, kind):
    root, info = FONT[kind]
    raw = Path(path).read_bytes()
    rom = c_ffta_sect_rom(raw, 0).setup({"font": (root, c_ffta_sect_font, dict(info))},
                                        _trim_raw_len(raw, 0xF00000))
    return rom.tabs["font"]


def us_rom(path):
    raw = Path(path).read_bytes()
    return c_ffta_sect_rom(raw, 0).setup(
        _words_sect_info({n: (r, s) for n, (r, s) in US_WORDS_ROOTS.items()}),
        _trim_raw_len(raw, 0xF00000))


def bitmaps(font, count):
    out = {}
    for slot in range(count):
        try:
            out[slot] = tuple(tuple(row) for row in font.gen_char(slot))
        except Exception:
            break
    return out


def chars(table, index):
    line = table[index]
    return [v for kind, v in getattr(line, "text", line).tokens if kind.startswith("CHR")]


def charset():
    decode, _ = json.loads((HERE / "charset_cn.json").read_text(encoding="utf-8"))
    return {int(k): v for k, v in decode.items()}


def compare(product, family, jp=None, ubm=None, jbm=None, names=None):
    jp = jp or load_rom_jp(str(JP_ROM))
    jbm = jbm if jbm is not None else bitmaps(font_of(str(JP_ROM), "jp"), 0xC66)
    ubm = ubm if ubm is not None else bitmaps(font_of(product, "us"), 0xC67)
    names = names if names is not None else charset()
    jt = jp.tabs["words"][family]
    ut = us_rom(product).tabs["words"][family]

    us_indices = [i for i in range(ut.tsize) if i not in set(US_ONLY.get(family, []))]
    jp_indices = [i for i in range(jt.tsize) if i not in set(JP_SKIP.get(family, []))]
    pairs = list(zip(jp_indices, us_indices))

    mismatches, compared, skipped = [], 0, 0
    for j, u in pairs:
        try:
            jc, uc = chars(jt, j), chars(ut, u)
        except Exception:
            skipped += 1
            continue
        if not jc:
            skipped += 1
            continue
        compared += 1
        if [jbm.get(c) for c in jc] != [ubm.get(s) for s in uc]:
            mismatches.append({
                "jp_index": j, "product_index": u,
                "jp": "".join(names.get(c, "<%04X>" % c) for c in jc),
                "jp_codes": jc, "product_slots": uc,
            })
    return {"family": family, "compared": compared, "skipped": skipped,
            "pairs": len(pairs), "mismatches": mismatches}


def us_fx_text(path):
    raw = Path(path).read_bytes()
    rom = c_ffta_sect_rom(raw, 0).setup(
        {"fx_text": (FX_ROOT_US, c_ffta_sect_fixed_text,
                     c_ffta_sect_rom.ARG_SELF, FX_COUNT_US)},
        _trim_raw_len(raw, 0xF00000))
    return rom.tabs["fx_text"]


def compare_fx(product, jp, ubm, jbm):
    jfx, ufx = jp.tabs["fx_text"], us_fx_text(product)
    us_leaves = [u for u in range(ufx.tsize) if u not in set(FX_LEAF_US_ONLY)]
    out = []
    for jleaf, uleaf in zip(range(jfx.tsize), us_leaves):
        try:
            jl, ul = jfx[jleaf], ufx[uleaf]
        except Exception:
            continue
        skip = set(FX_ENTRY_US_ONLY.get(uleaf, []))
        ui = [i for i in range(getattr(ul, "tsize", 0)) if i not in skip]
        compared, mismatches = 0, []
        for j, u in zip(range(getattr(jl, "tsize", 0)), ui):
            try:
                jt = [v for k, v in getattr(jl[j], "text", jl[j]).tokens if k.startswith("CHR")]
                ut = [v for k, v in getattr(ul[u], "text", ul[u]).tokens if k.startswith("CHR")]
            except Exception:
                continue
            if not jt:
                continue
            compared += 1
            if [jbm.get(c) for c in jt] != [ubm.get(s) for s in ut]:
                mismatches.append(j)
        row = {"leaf": jleaf, "product_leaf": uleaf, "compared": compared,
               "mismatches": mismatches, "rebuilt": jleaf in FX_REBUILT}
        if mismatches and jleaf not in FX_REBUILT:
            # Index alignment failed for this leaf -- the US ordering differs by
            # more than the insertions above.  Fall back to the weaker but still
            # meaningful question: is every JP entry of this leaf present in the
            # product, glyph for glyph, somewhere?
            def seq(tab, i, bm):
                return tuple(bm.get(v) for k, v in
                             getattr(tab[i], "text", tab[i]).tokens if k.startswith("CHR"))
            present = set()
            for u in range(getattr(ul, "tsize", 0)):
                try:
                    present.add(seq(ul, u, ubm))
                except Exception:
                    pass
            unmatched = []
            for j in range(getattr(jl, "tsize", 0)):
                try:
                    s = seq(jl, j, jbm)
                except Exception:
                    continue
                if s and s not in present:
                    unmatched.append(j)
            row["reordered"] = True
            row["unmatched"] = unmatched
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("product")
    ap.add_argument("--family", action="append", default=[])
    ap.add_argument("--json")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    jp = load_rom_jp(str(JP_ROM))
    jbm = bitmaps(font_of(str(JP_ROM), "jp"), 0xC66)
    ubm = bitmaps(font_of(args.product, "us"), 0xC67)
    names = charset()

    report, bad = {}, 0
    for family in (args.family or FAMILIES):
        result = compare(args.product, family, jp, ubm, jbm, names)
        report[family] = result
        bad += len(result["mismatches"])
        print(f"{family:9s} compared={result['compared']:5d} "
              f"skipped={result['skipped']:3d} mismatches={len(result['mismatches'])}")
        if args.verbose:
            for m in result["mismatches"][:40]:
                print(f"    jp[{m['jp_index']}] {m['jp']}  ->  product[{m['product_index']}] "
                      f"slots={m['product_slots']}")
    total = sum(r["compared"] for r in report.values())
    if not args.family:
        fx = compare_fx(args.product, jp, ubm, jbm)
        report["fx_text"] = fx
        fx_total = sum(r["compared"] for r in fx)
        fx_bad = sum(len(r.get("unmatched", r["mismatches"]))
                     for r in fx if not r["rebuilt"])
        rebuilt = sum(len(r["mismatches"]) for r in fx if r["rebuilt"])
        reordered = [r["leaf"] for r in fx if r.get("reordered")]
        print(f"{'fx_text':9s} compared={fx_total:5d} leaves={len(fx):3d} "
              f"missing={fx_bad} (+{rebuilt} rebuilt in leaf {sorted(FX_REBUILT)}"
              f"{', reordered leaves ' + str(reordered) if reordered else ''})")
        if args.verbose:
            for r in fx:
                if r["mismatches"]:
                    print(f"    leaf {r['leaf']:2d} {'REBUILT ' if r['rebuilt'] else ''}"
                          f"index-aligned {len(r['mismatches'])}/{r['compared']}"
                          + (f", JP entries absent from the product: "
                             f"{len(r['unmatched'])}" if "unmatched" in r else ""))
        total += fx_total
        bad += fx_bad
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"\n{total - bad}/{total} entries render JP retail's exact glyphs")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
