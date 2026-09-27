#! python3
"""JP retail glyph code -> character, from Japanese sources only.

Why this exists
---------------

Every audit that prints JP retail text used to name glyph codes through
``charset_cn.json``, the table of the Chinese build this builder descends
from.  That table is not "JP with simplified forms": the Chinese build
re-assigned slots, so it prints simplified forms (備 as U+5907) and, worse, other
characters altogether (JP 0x930 峠 as 薇, 0x7FC 貸 as 涅, 0x3CC 拠 as 孩).
Production never used it -- the encoder takes kanji from
the manifests' attested ``kanji_codes`` -- but the audits' printed text and
one gate key did.

This table is built from three sources, none of them Chinese, and with no
guessing between them:

1. ``charset_us.json`` below 0x122 -- there the JP glyph code, the US font
   slot and the charset index are the same number
   (``ffta_jp_us_only_system_battle.charset_tables``).
2. every editorial manifest's ``kanji_codes`` -- each code revalidated against
   the JP retail record it is attested at on every build.
3. ``data/jp_kanji_glyph_reviewed.json`` -- the few codes an audit needs that
   no manifest attests, each named from the JP retail font bitmap itself and
   the JP record it appears in.  ``--show`` prints a bitmap for that review.

Any other code prints as ``〔JXXX〕``.  An unnamed glyph is visible, never
silently wrong.

Usage
-----

    python ffta_jp_charset.py --stats
    python ffta_jp_charset.py --show 0xB7D 0xA12
"""
from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CHARSET_US = HERE / "charset_us.json"
REVIEWED = DATA / "jp_kanji_glyph_reviewed.json"
FIRST_KANJI = 0x122
LAST_CODE = 0xC65


def unnamed(code):
    return "〔J%03X〕" % code


def low_span():
    decode, _encode = json.loads(CHARSET_US.read_text(encoding="utf-8"))
    return {int(k): v for k, v in decode.items() if int(k) < FIRST_KANJI}


def attested():
    """JP glyph code -> kanji over every manifest's ``kanji_codes``."""
    out, where = {}, {}
    for f in sorted(DATA.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        kc = doc.get("kanji_codes") if isinstance(doc, dict) else None
        for char, row in (kc or {}).items():
            code = int(row["jp_glyph_code"], 16)
            if code < FIRST_KANJI:
                raise RuntimeError(f"JPCS_KANJI_BELOW_SPAN {char} {f.name}")
            if out.setdefault(code, char) != char:
                raise RuntimeError(f"JPCS_KANJI_CONFLICT 0x{code:03X} "
                                   f"{out[code]}({where[code]}) {char}({f.name})")
            where.setdefault(code, f.name)
    return out


def reviewed():
    doc = json.loads(REVIEWED.read_text(encoding="utf-8"))
    return {int(k, 16): row["char"] for k, row in doc["codes"].items()}


@lru_cache(maxsize=1)
def _table():
    table = low_span()
    kanji = attested()
    for code, char in reviewed().items():
        if code < FIRST_KANJI:
            raise RuntimeError(f"JPCS_REVIEWED_BELOW_SPAN 0x{code:03X}")
        if kanji.setdefault(code, char) != char:
            raise RuntimeError(f"JPCS_REVIEWED_CONFLICT 0x{code:03X} {kanji[code]} {char}")
    seen = {}
    for code, char in kanji.items():
        if seen.setdefault(char, code) != code:
            raise RuntimeError(f"JPCS_KANJI_TWO_CODES {char} 0x{seen[char]:03X} 0x{code:03X}")
    table.update(kanji)
    return table


def decoder():
    """Every JP glyph code 0..LAST_CODE -> its character, or ``〔JXXX〕``."""
    table = _table()
    return {code: table.get(code, unnamed(code)) for code in range(LAST_CODE + 1)}


def show(code):
    from ffta_qa_glyphs import JP_ROM, bitmaps, font_of
    bm = bitmaps(font_of(str(JP_ROM), "jp"), LAST_CODE + 1)[code]
    # value 3 is the stroke, 2 its shadow, 1 the outline
    return "\n".join("".join("#" if v == 3 else "." for v in row) for row in bm)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--show", nargs="*")
    args = ap.parse_args()
    if args.stats:
        table = _table()
        kanji = sum(1 for c in table if c >= FIRST_KANJI)
        print(f"low span {sum(1 for c in table if c < FIRST_KANJI)}  kanji named {kanji}"
              f"  (attested {len(attested())}, reviewed {len(reviewed())})"
              f"  unnamed {LAST_CODE + 1 - FIRST_KANJI - kanji}")
    for code in args.show or []:
        c = int(code, 16)
        print(f"0x{c:03X} {_table().get(c, unnamed(c))}")
        print(show(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
