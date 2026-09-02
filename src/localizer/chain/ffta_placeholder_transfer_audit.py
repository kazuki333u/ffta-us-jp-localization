#! python3
# -*- coding: utf-8 -*-
"""US-added content transferred over a JP *developer placeholder*.

The failure class
-----------------

`ffta_jp_us_only_editorial_scope.py` finds US-only content by asking whether the
aligned JP record is missing or empty.  That question has a blind spot, and this
tool is the blind spot's audit.

FFTA's US build is not only a translation of the Japanese one: it **adds
content** into slots the Japanese build shipped as developer placeholders.
Proved on the two pristine images:

* `words:quest` 377..396 -- JP ships the literal names `クエスト377` … `クエスト396`;
  the US ships twenty real mission names (`Reconcilliation`, `Left Behind`,
  `No Arms Rule`, …).  The mission records themselves are **empty in JP and
  populated in US** (41 of 511 mission entries differ; 377..406 are the block),
  so these are US-added missions, not renamed ones.
* `words:battle` -- JP 757 entries, US 769: twelve US-added judge / law ability
  names.
* `words:system` -- JP 73, US 84.  `words:name` -- JP 128, US 725.
* `words:content` 122 -- JP `予備2` (a reserve slot), US `Official`, whose
  description is the one record RC15 left `DEFECT_OPEN` (`fx_text/22/121`).

Where the JP record is *empty*, source transfer writes nothing and the scope
tool notices.  Where the JP record is a **non-empty placeholder**, source
transfer writes the placeholder, the tool sees a successful transfer, and the
shipped build renders `クエスト377` in the pub mission list under fully
localized Japanese dialogue.

What this tool does
-------------------

For every family whose JP and US tables align, it decodes the record in all
three images -- pristine JP, pristine US, production -- and reports every index
where the JP text matches a developer-placeholder pattern while the US text is
real content.  Placeholder patterns are declared, not guessed:

    クエスト<n>  ダミークラン<n>  予備<n>  予約<n>  ダミー<n>  クエスト<n>

and the US side must not itself be `dummy` / `-`.

    python ffta_placeholder_transfer_audit.py            # human readable
    python ffta_placeholder_transfer_audit.py --json out.json

Read-only.  Launches nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import types
from pathlib import Path

_stub = types.ModuleType("ffta_font_generator")
_stub.make_ffta_font_gen = None
sys.modules.setdefault("ffta_font_generator", _stub)

import ffta_qa_glyphs as glyphs                                     # noqa: E402
from ffta_sect import (c_ffta_sect_rom, _pages_sect_info, _trim_raw_len,   # noqa: E402
                       load_rom_jp)

# the same four `pages:*` roots `ffta_us_only_scenario_inventory` reads
PAGES_ROOTS = {"battle": 0x237F4, "quest/0": 0x13CD8, "quest/1": 0x13CC0,
               "condi": 0x13D98}


def _pages(path):
    raw = Path(path).read_bytes()
    return c_ffta_sect_rom(raw, 0).setup(_pages_sect_info(PAGES_ROOTS),
                                         _trim_raw_len(raw, 0xF00000)).tabs["pages"]

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
JP_ROM = ROOT / "rom/original/FFTA_JP.gba"
US_ROM = ROOT / "rom/original/FFTA_US.gba"
PRODUCTION = ROOT / "rom/build/ffta_us_jp_us_added_missions.gba"
# tracked, because it is the editorial specification of the next milestone: the
# US original of every record production still ships as a JP placeholder
DEFAULT_JSON = HERE / "data/us_added_placeholder_transfer.json"

# JP developer placeholders.  Each is a name the Japanese build ships for a slot
# its own developers had not filled: a numbered quest / clan stub, or one of the
# two reserve pools (予約 = "booked", 予備 = "spare") whose consumed members were
# renamed (予約1 -> デプス at content 80, 予備1 -> ボルゾイクラン at content 121).
# `charset_cn.json` renders some JP kanji in their simplified forms (備->备,
# 約->约), so both spellings are listed; neither is a guess about the ROM.
JP_PLACEHOLDER = re.compile(
    r"^(クエスト|ダミークラン|ダミー|予備|予备|予約|予约|よび|ダミ)\s*[0-9０-９]*$")
# a US side that is itself a placeholder is not a finding: both builds left the
# slot unfilled.
US_PLACEHOLDER = re.compile(r"^(dummy|-|CRN_[A-Z0-9_]*|)$")

# the families `ffta_qa_glyphs` aligns between the two retail images
FAMILIES = ("content", "battle", "quest", "refer", "rumor", "system",
            "clan", "name", "title", "uitm")


def _decoders():
    jp_names = glyphs.charset()
    us_dec, _enc = json.loads((HERE / "charset_us.json").read_text(encoding="utf-8"))
    us_names = {int(k): v for k, v in us_dec.items()}
    # production stores US font slots; recover the JP glyph code by bitmap so the
    # production record can be printed in the same alphabet as the JP one
    ubm = glyphs.bitmaps(glyphs.font_of(str(PRODUCTION), "us"), 0xC67)
    jbm = glyphs.bitmaps(glyphs.font_of(str(JP_ROM), "jp"), 0xC66)
    by_bitmap = {}
    for code, bm in jbm.items():
        by_bitmap.setdefault(bm, code)
    return jp_names, us_names, ubm, by_bitmap


def _text(entry, names, slot_map=None):
    out = []
    for kind, val in getattr(entry, "text", entry).tokens:
        if not kind.startswith("CHR"):
            out.append("{%X}" % val)
            continue
        if slot_map is not None:
            code = slot_map(val)
            out.append(names.get(code, "<%04X>" % val) if code is not None
                       else "<%04X>" % val)
        else:
            out.append(names.get(val, "<%04X>" % val))
    return "".join(out)


def _clean(text):
    """Text with the control words and the US word separator removed."""
    return re.sub(r"\{[0-9A-F]+\}", "", text.replace("{52}", " ")).strip()


def audit():
    jp_names, us_names, ubm, by_bitmap = _decoders()

    def prod_slot(slot):
        return by_bitmap.get(ubm.get(slot))

    jp = load_rom_jp(str(JP_ROM))
    us = glyphs.us_rom(str(US_ROM))
    pr = glyphs.us_rom(str(PRODUCTION))

    findings, checked = [], 0

    def compare(path, jent, uent, pent):
        nonlocal checked
        try:
            jtxt = _clean(_text(jent, jp_names))
            utxt = _clean(_text(uent, us_names))
        except Exception:                                            # noqa: BLE001
            return
        checked += 1
        if not JP_PLACEHOLDER.match(jtxt) or US_PLACEHOLDER.match(utxt):
            return
        try:
            ptxt = _clean(_text(pent, jp_names, prod_slot))
        except Exception:                                            # noqa: BLE001
            ptxt = "<unreadable>"
        findings.append({"path": path, "jp": jtxt,
                         "us": utxt[:120],
                         # the full US original, controls intact: this is the
                         # editorial input for the layer that fixes the family
                         "us_source": _text(uent, us_names),
                         "production": ptxt,
                         "still_placeholder": ptxt == jtxt})

    # -- pages:* -- the mission briefing prose and the battle message table
    jpg = jp.tabs["pages"]
    upg = _pages(US_ROM)
    ppg = _pages(PRODUCTION)
    for fam in ("battle", "quest/0", "condi"):
        if fam not in jpg:
            continue
        jt, ut, pt = jpg[fam], upg[fam], ppg[fam]
        for i in range(min(jt.tsize, ut.tsize, pt.tsize)):
            compare("pages:%s/%d" % (fam, i), jt[i], ut[i], pt[i])
    # `pages:quest/1` is the second half of the mission table (missions 201..511);
    # the JP root carries it inside `quest/0`, so it is compared by mission index.
    jq, uq1, pq1 = jpg["quest/0"], upg["quest/1"], ppg["quest/1"]
    for i in range(min(uq1.tsize, pq1.tsize)):
        j = 200 + i
        if j >= jq.tsize:
            break
        compare("pages:quest/1/%d" % i, jq[j], uq1[i], pq1[i])

    # -- fx_text -- the leaf-aligned half
    jfx, ufx, pfx = jp.tabs["fx_text"], glyphs.us_fx_text(str(US_ROM)), glyphs.us_fx_text(str(PRODUCTION))
    us_leaves = [u for u in range(ufx.tsize) if u not in set(glyphs.FX_LEAF_US_ONLY)]
    for jleaf, uleaf in zip(range(jfx.tsize), us_leaves):
        try:
            jl, ul, pl = jfx[jleaf], ufx[uleaf], pfx[uleaf]
        except Exception:                                            # noqa: BLE001
            continue
        skip = set(glyphs.FX_ENTRY_US_ONLY.get(uleaf, []))
        ui = [i for i in range(getattr(ul, "tsize", 0)) if i not in skip]
        for j, u in zip(range(getattr(jl, "tsize", 0)), ui):
            if u >= getattr(pl, "tsize", 0):
                break
            compare("fx_text/%d/%d" % (uleaf, u), jl[j], ul[u], pl[u])

    for fam in FAMILIES:
        jt, ut, pt = jp.tabs["words"][fam], us.tabs["words"][fam], pr.tabs["words"][fam]
        us_indices = [i for i in range(ut.tsize)
                      if i not in set(glyphs.US_ONLY.get(fam, []))]
        jp_indices = [i for i in range(jt.tsize)
                      if i not in set(glyphs.JP_SKIP.get(fam, []))]
        for j, u in zip(jp_indices, us_indices):
            if u >= pt.tsize:
                break
            compare("words:%s/%d" % (fam, u), jt[j], ut[u], pt[u])
    return {"records_compared": checked, "findings": findings,
            "finding_count": len(findings),
            "still_placeholder": sum(1 for f in findings if f["still_placeholder"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()
    res = audit()
    print("records compared:       ", res["records_compared"])
    print("US-added over JP placeholder:", res["finding_count"])
    print("still placeholder in production:", res["still_placeholder"])
    for f in res["findings"]:
        print("  %-22s JP=%-16s US=%-24s production=%s%s"
              % (f["path"], f["jp"], f["us"], f["production"],
                 "   <-- DEFECT" if f["still_placeholder"] else ""))
    text = json.dumps(res, ensure_ascii=False, indent=1) + "\n"
    dests = {DEFAULT_JSON}
    if args.json:
        dests.add(Path(args.json))
    for dest in sorted(dests):
        dest.write_text(text, encoding="utf-8")
        print("->", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
