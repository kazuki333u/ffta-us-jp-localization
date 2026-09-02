#! python3
# -*- coding: utf-8 -*-
"""US-added *item identity* over an aligned JP slot that is real JP content.

The failure class
-----------------

`ffta_placeholder_transfer_audit.py` (RC16) found US-added content transferred
over a JP **developer placeholder** -- `クエスト377`, `ダミー377`, `予備2`.  Its
question is "does the JP record match a placeholder pattern?", and RC17 fixed
the 42 records that answered yes.

That question has its own blind spot, and this tool is that blind spot's audit.
Source transfer pairs a US record with the JP record **at the same index** and
treats the JP record as the authority whenever it is non-empty and parseable.
Where the US build re-used a slot for a *different object*, the JP record is
real, shipped, non-placeholder Japanese -- and it is still the wrong text.

Measured on the two pristine images and the production ROM, the family is the
**mission items**: `words:content/499..625` (127 names, item id = index - 498)
and their descriptions `fx_text/25/0..126` (`fx_text/24` on the JP side, whose
leaf numbering is one lower -- US leaf 24 is US-only).

The mechanical question
-----------------------

An item's identity is not its text; it is what the game asks for.  Both builds
carry a 511-entry mission table (`US 0x0855AE92` / `JP 0x0852DF1E`, 70 bytes per
entry, entry *i* = mission id *i+1*, `ffta_mission_availability.py`), and two
bytes of every entry name the mission items the offer requires:

    +0x36   first required mission item id  (1..127, 0 = none)
    +0x37   second required mission item id

**41 of the 511 entries differ between the two images** and every other entry is
byte-identical, so any US-added item reference must be inside those 41.  For
each item id this tool prints the mission ids that require it in each image and
whether every such entry is byte-identical between them, and classifies:

``US_ADDED_ITEM_IDENTITY``
    required by a US mission and by no JP mission, and the requiring US entry is
    **empty in JP** -- the object exists only in the US build, so JP retail has
    no name for it and the aligned JP record is a different item.
``SHARED_ITEM_IDENTITY``
    the same mission ids require it in both images and those entries are
    byte-identical -- the same object, so JP retail's own record is its name.
``UNREFERENCED_BY_EITHER_MISSION_TABLE``
    no mission entry in either image requires it (it is a mission *reward*).
    Both images agree, so nothing distinguishes the slots mechanically.

Anything else is refused: it would be a class this tool has not been shown.

The editorial column
--------------------

`semantic_review` records the reading of all 127 name + description pairs in
both images.  Only the rows that are not a plain translation pair are declared
here, with their reason; every other row is `TRANSLATION_PAIR`.  The two
`US_PROSE_REWRITE_SAME_SLOT` rows are *not* defects: the same mission requires
them in both images through a byte-identical entry, so they are one object the
two localization teams described differently, and this project's policy (Main
`docs/decisions.md`) makes JP retail the authority for player-visible wording.

    python ffta_us_added_item_identity_audit.py            # human readable
    python ffta_us_added_item_identity_audit.py --csv      # rewrite the tracked CSV
    python ffta_us_added_item_identity_audit.py --json out.json

Read-only over the ROMs.  Launches nothing.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import types
from pathlib import Path

_stub = types.ModuleType("ffta_font_generator")
_stub.make_ffta_font_gen = None
sys.modules.setdefault("ffta_font_generator", _stub)

import ffta_qa_glyphs as glyphs                                     # noqa: E402
from ffta_sect import load_rom_jp                                   # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
JP_ROM = ROOT / "rom/original/FFTA_JP.gba"
US_ROM = ROOT / "rom/original/FFTA_US.gba"
PRODUCTION = ROOT / "rom/build/ffta_us_jp_us_added_items.gba"
DEFAULT_CSV = HERE / "data/us_added_item_identity_audit.csv"

ROM = 0x08000000
# The mission tables.  The JP address is found, not assumed: five consecutive
# US entries (missions 100..104) occur exactly once in the JP image, and the
# table start follows from their index.  Recorded here so the audit is cheap.
US_MISSION_TABLE = 0x0855AE92
JP_MISSION_TABLE = 0x0852DF1E
MISSION_SIZE = 70
MISSION_COUNT = 511
REQUIRED_ITEM_FIELDS = (0x36, 0x37)

# The family.  words:content index = 498 + item id; fx_text entry = item id - 1.
ITEM_FIRST, ITEM_COUNT = 1, 127
CONTENT_BASE = 498
US_FX_LEAF, JP_FX_LEAF = 25, 24

# Rows whose US and JP text are not a plain translation pair.  Declared, with
# the reason and the mechanical evidence that decides them.
SEMANTIC_REVIEW = {
    72: ("US_ADDED_ITEM_IDENTITY",
         "US `Stuffed Bear` is Mewt's keepsake, required by the US-added postgame "
         "mission 383 `Memories`; JP `デジョン` is a time-magic tome no JP mission "
         "requires.  Different objects."),
    95: ("US_PROSE_REWRITE_SAME_SLOT",
         "US `Feather Badge` / JP `アイテム眼鏡`: mission 175 requires item 95 in "
         "both images through a byte-identical entry, so this is one object the "
         "two localizations described differently.  JP retail's wording stands."),
    96: ("US_PROSE_REWRITE_SAME_SLOT",
         "US `Insignia` / JP `アイテム眼鏡2`: mission 299 requires item 96 in both "
         "images through a byte-identical entry.  Same object, JP wording stands."),
}
DEFAULT_REVIEW = "TRANSLATION_PAIR"

ALLOWED = {"US_ADDED_ITEM_IDENTITY", "SHARED_ITEM_IDENTITY",
           "UNREFERENCED_BY_EITHER_MISSION_TABLE"}


def _entries(raw: bytes, table: int):
    base = table - ROM
    return [raw[base + MISSION_SIZE * i: base + MISSION_SIZE * (i + 1)]
            for i in range(MISSION_COUNT)]


def _text(entry, names, slot_map=None):
    out = []
    for kind, value in getattr(entry, "text", entry).tokens:
        if not kind.startswith("CHR"):
            out.append("{%X}" % value)
            continue
        if slot_map is not None:
            code = slot_map(value)
            out.append(names.get(code, "<%04X>" % value) if code is not None
                       else "<%04X>" % value)
        else:
            out.append(names.get(value, "<%04X>" % value))
    return "".join(out)


def _clean(text):
    return re.sub(r"\{[0-9A-F]+\}", "", text.replace("{52}", " ")).strip()


def audit(production=PRODUCTION):
    us_raw, jp_raw = US_ROM.read_bytes(), JP_ROM.read_bytes()
    ue = _entries(us_raw, US_MISSION_TABLE)
    je = _entries(jp_raw, JP_MISSION_TABLE)
    differing = [i + 1 for i in range(MISSION_COUNT) if ue[i] != je[i]]
    # Everything the two builds do differently with mission items is inside the
    # differing entries; the audit says so instead of assuming it.
    differing_offsets = sorted({o for i in range(MISSION_COUNT) if ue[i] != je[i]
                               for o in range(MISSION_SIZE) if ue[i][o] != je[i][o]})

    jp_names = glyphs.charset()
    us_dec, _enc = json.loads((HERE / "charset_us.json").read_text(encoding="utf-8"))
    us_names = {int(k): v for k, v in us_dec.items()}
    jp = load_rom_jp(str(JP_ROM))
    us = glyphs.us_rom(str(US_ROM))
    us_fx = glyphs.us_fx_text(str(US_ROM))
    prod = prod_fx = prod_slot = None
    if production and Path(production).is_file():
        prod = glyphs.us_rom(str(production))
        prod_fx = glyphs.us_fx_text(str(production))
        ubm = glyphs.bitmaps(glyphs.font_of(str(production), "us"), 0xC67)
        jbm = glyphs.bitmaps(glyphs.font_of(str(JP_ROM), "jp"), 0xC66)
        by_bitmap = {}
        for code, bm in jbm.items():
            by_bitmap.setdefault(bm, code)

        def prod_slot(slot):                                         # noqa: F811
            return by_bitmap.get(ubm.get(slot))

    rows = []
    for item in range(ITEM_FIRST, ITEM_FIRST + ITEM_COUNT):
        index = CONTENT_BASE + item
        entry = item - 1
        us_req = [i + 1 for i in range(MISSION_COUNT)
                  if any(ue[i][f] == item for f in REQUIRED_ITEM_FIELDS)]
        jp_req = [i + 1 for i in range(MISSION_COUNT)
                  if any(je[i][f] == item for f in REQUIRED_ITEM_FIELDS)]
        same_entries = all(ue[m - 1] == je[m - 1] for m in set(us_req) | set(jp_req))
        jp_side_empty = all(not any(je[m - 1][2:]) for m in us_req) if us_req else False

        if us_req and not jp_req and jp_side_empty:
            verdict = "US_ADDED_ITEM_IDENTITY"
        elif us_req == jp_req and same_entries:
            verdict = ("SHARED_ITEM_IDENTITY" if us_req
                       else "UNREFERENCED_BY_EITHER_MISSION_TABLE")
        else:
            verdict = "UNCLASSIFIED"
        if verdict not in ALLOWED:
            raise RuntimeError("UAII_UNCLASSIFIED item %d us=%s jp=%s" % (item, us_req, jp_req))

        review, reason = SEMANTIC_REVIEW.get(item, (DEFAULT_REVIEW, ""))
        row = {
            "item_id": "0x%02X" % item,
            "item_index": item,
            "name_path": "words:content/%d" % index,
            "description_path": "fx_text/%d/%d" % (US_FX_LEAF, entry),
            "us_name": _clean(_text(us.tabs["words"]["content"][index], us_names)),
            "jp_name": _clean(_text(jp.tabs["words"]["content"][index], jp_names)),
            "production_name": (_clean(_text(prod.tabs["words"]["content"][index],
                                             jp_names, prod_slot)) if prod else ""),
            "us_required_by_missions": " ".join(map(str, us_req)),
            "jp_required_by_missions": " ".join(map(str, jp_req)),
            "requiring_entries_identical": same_entries,
            "verdict": verdict,
            "semantic_review": review,
            "semantic_review_reason": reason,
            "us_description": _clean(_text(us_fx[US_FX_LEAF][entry], us_names)),
            "jp_description": _clean(_text(jp.tabs["fx_text"][JP_FX_LEAF][entry],
                                           jp_names)),
            "production_description": (_clean(_text(prod_fx[US_FX_LEAF][entry],
                                                    jp_names, prod_slot))
                                       if prod_fx else ""),
        }
        rows.append(row)

    wrong = [r for r in rows if r["verdict"] == "US_ADDED_ITEM_IDENTITY"]
    summary = {
        "family": "mission items -- words:content/%d..%d and fx_text/%d/0..%d"
                  % (CONTENT_BASE + ITEM_FIRST, CONTENT_BASE + ITEM_COUNT,
                     US_FX_LEAF, ITEM_COUNT - 1),
        "items_checked": len(rows),
        "records_checked": 2 * len(rows),
        "mission_entries_differing_us_vs_jp": len(differing),
        "differing_mission_ids": differing,
        "differing_byte_offsets": ["0x%02X" % o for o in differing_offsets],
        "required_item_fields": ["0x%02X" % f for f in REQUIRED_ITEM_FIELDS],
        "second_field_ever_differs": REQUIRED_ITEM_FIELDS[1] in differing_offsets,
        "verdicts": {v: sum(1 for r in rows if r["verdict"] == v) for v in sorted(ALLOWED)},
        "semantic_review": {v: sum(1 for r in rows if r["semantic_review"] == v)
                            for v in sorted({r["semantic_review"] for r in rows})},
        "us_added_item_identities": [r["item_id"] for r in wrong],
        "records_production_must_not_source_from_jp": 2 * len(wrong),
    }
    return summary, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", type=Path)
    ap.add_argument("--csv", nargs="?", const=DEFAULT_CSV, type=Path)
    ap.add_argument("--production", type=Path, default=PRODUCTION)
    args = ap.parse_args()
    summary, rows = audit(args.production)
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as fd:
            writer = csv.DictWriter(fd, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print("%s: %d rows" % (args.csv, len(rows)))
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "rows": rows},
                                        ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for row in rows:
        if row["verdict"] != "SHARED_ITEM_IDENTITY" or row["semantic_review"] != DEFAULT_REVIEW:
            if row["verdict"] == "UNREFERENCED_BY_EITHER_MISSION_TABLE" \
                    and row["semantic_review"] == DEFAULT_REVIEW:
                continue
            print("  %s %-16s %-22s %-16s %s" % (
                row["item_id"], row["verdict"], row["us_name"], row["jp_name"],
                row["semantic_review"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
