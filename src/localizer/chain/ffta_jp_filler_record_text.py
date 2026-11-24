#! python3
# -*- coding: utf-8 -*-
"""Three records that carried JP retail filler as text (an earlier build, public Issue #2).

The defect
---------------------------------

``ffta_jp_chr_half_ya_final`` (2026-08-30) moved the last three CHR_HALF
records of the transfer: ``pages:choice/39``, ``pages:condi/15`` and
``fx_text/16/48``.  It assumed the JP record at the same index was YA-format
*text* whose US counterpart was a standard record.  In all three JP retail
holds a type-0x11 *filler* record (the same empty shape as ``pages:choice``
0..31), so the layer re-encoded filler bytes as glyphs:

=====================  ==========================  ==========================
record                 US retail                   an earlier build (Public Beta 9)
=====================  ==========================  ==========================
``pages:choice/39``    ``Deal`` / ``Just take it``  りきる / ばペ
``pages:condi/15``     ``Defeat [X]!``             [X]たせ
``fx_text/16/48``      ``dummy``                   ッポ
=====================  ==========================  ==========================

``pages:choice/39`` is the world-map encounter's pay-or-fight choice in
mission 103 (reported on the public repository, Issue #2).

What this layer does
--------------------

* ``pages:choice/39`` -> とりひきする / 力ずくでうばう, the US record's own
  control skeleton (one ``{42}`` after each option).  力 is JP retail's glyph
  0x182, attested in the neighbouring choice ``pages:choice/48``
  (力ずくでとり返す).
* ``pages:condi/15`` -> ``{453}をたおせ``, in the form of JP retail's own
  victory conditions (``pages:condi/16`` すべての敵をたおせ); ``{453}`` is the
  US record's target reference.
* ``fx_text/16/48`` -> US retail's own record bytes (``dummy``): nothing shows
  it, and the JP index holds no text to translate.

Every glyph is one the chain already allocates: **zero new glyphs**.  Each
leaf is re-serialized with only its target replaced, placed in the free tail
and repointed through *every* word of the image that named the old leaf
(``pages:condi`` is also named by three code literals, see
``ffta_jp_stale_literals``); the set of such words is declared and checked.

Recurrence check
----------------

``filler_transliterations`` scans every ``pages:*`` family and every
``fx_text`` leaf for a product record whose tokens equal the YA
re-encoding of a JP retail YA record with glyph tokens -- the exact output of
the defect.  The base image must show exactly the three records above, the
product none.

Layer position: a new terminal layer above ``ffta_jp_us_law_semantics``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_us_law_semantics as prev
from ffta_sect import (c_ffta_sect_rom, c_ffta_sect_text_buf,           # noqa: E402
                       c_ffta_sect_text_buf_ya, _pages_sect_info, _trim_raw_len)
import ffta_us_source as _us_source

uam, sysbat, stext, fxt, pta = prev.uam, prev.sysbat, prev.stext, prev.fxt, prev.pta

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/filler_record_text.json"
RUN_BASE = HERE / "build/filler_record_text"
OUTROM = ROOT / "rom/build/ffta_us_jp_filler_record_text.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_filler_record_text_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_us_law_semantics (the earlier layer's rebase).
BASELINE = "1A911474ADF954C446BB94B24AB67210220EDF6E22ECB8E4575943E32BD9180C"
# Pinned after the first deterministic build of this layer.
EXPECTED_PRODUCTION = "0A3C9C5A07D458A84163D0EA3816273A343D15126328E7653B9E5B1C1177D47D"

PAGE_ROOTS = {"battle": 0x237F4, "quest/0": 0x13CD8, "quest/1": 0x13CC0,
              "choice": 0x3F6B8, "condi": 0x13D98, "config": 0x94B8}
FX_LEAF, FX_INDEX = 16, 48
# Every aligned word of the image that holds the old leaf pointer.
LEAF_FIELDS = {
    "pages:choice": (0x3F6B8,),
    "pages:condi": (0x13D98, 0x13DE0, 0x13E34, 0x13E98),
    f"fx_text/{FX_LEAF}": (fxt.FX_ROOT_FIELD + FX_LEAF * 4,),
}
TEXT = {                                   # editorial; the manifest repeats it
    "pages:choice/39": "とりひきする{42}力ずくでうばう{42}",
    "pages:condi/15": "{453}をたおせ{42}",
}
US_BYTES = (f"fx_text/{FX_LEAF}/{FX_INDEX}",)
KANJI = {"力": (0x182, "pages:choice/48")}
DEFECT_PATHS = sorted(list(TEXT) + list(US_BYTES))
WORD_SPACE = 0x52
OPTION_END = 0x42


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


tokens_of = fxt.tokens_of
u32 = prev.u32


def product_pages(raw):
    data = bytes(raw)
    return c_ffta_sect_rom(data, 0).setup(
        _pages_sect_info(PAGE_ROOTS), _trim_raw_len(data, 0xF00000)).tabs["pages"]


def split(path):
    head, index = path.rsplit("/", 1)
    return head, int(index)


def leaf_of(path, pages, fx):
    head, index = split(path)
    if head.startswith("pages:"):
        return pages[head[len("pages:"):]], index
    return fx[int(head.split("/")[1])], index


def all_leaves(pages, fx):
    for fam in PAGE_ROOTS:
        yield f"pages:{fam}", pages[fam]
    for leaf in range(fxt.FX_TSIZE):
        try:
            page = fx[leaf]
        except Exception:                                            # noqa: BLE001
            continue
        if page is not None and not isinstance(page, list):
            yield f"fx_text/{leaf}", page


def jp_leaves(jp):
    for fam in PAGE_ROOTS:
        if fam in jp.tabs["pages"]:          # JP retail has no quest/1
            yield f"pages:{fam}", jp.tabs["pages"][fam]
    for leaf in range(fxt.FX_TSIZE):
        try:
            page = jp.tabs["fx_text"][leaf]
        except Exception:                                            # noqa: BLE001
            continue
        if page is not None and not isinstance(page, list):
            yield f"fx_text/{leaf}", page


# --------------------------------------------------------- recurrence check ---

def ya_transliteration(jp_line, alloc):
    """What ffta_jp_chr_half_ya_final wrote for a JP YA record, or None."""
    text = getattr(jp_line, "text", None)
    if not isinstance(text, c_ffta_sect_text_buf_ya):
        return None
    out, glyphs = [], 0
    for kind, value in text.tokens:
        if kind == "CHR_HALF":
            out.append(("CHR_FULL", value))
            glyphs += 1
        elif kind == "CHR_FULL":
            if value not in alloc:
                return None
            out.append(("CHR_FULL", alloc[value]))
            glyphs += 1
        elif kind.startswith("ERR_"):
            return None
        else:
            out.append((kind, value))
    return out if glyphs else None


def filler_transliterations(raw, jp, alloc):
    """Every product record equal to the YA re-encoding of its JP record."""
    pages, fx = product_pages(raw), fxt.product_fx_text(raw)
    product = dict(all_leaves(pages, fx))
    found, scanned = [], 0
    for name, jp_page in jp_leaves(jp):
        page = product.get(name)
        if page is None:
            continue
        for index in range(min(jp_page.tsize, page.tsize)):
            want = ya_transliteration(jp_page[index], alloc)
            if want is None:
                continue
            scanned += 1
            if tokens_of(page[index]) == want:
                found.append(f"{name}/{index}")
    return sorted(found), scanned


# ------------------------------------------------------------------ derive ---

def control_skeleton(tokens):
    return [(k, v) for k, v in tokens if not k.startswith("CHR")
            and not (k == "CTR_FUNC" and v == WORD_SPACE)]


def option_widths(tokens, jp_raw):
    """Width of each {42}-terminated piece, in JP font pixels."""
    widths, current = [], 0
    for kind, value in tokens:
        if kind.startswith("CHR"):
            current += jp_raw[stext.JP_METADATA + value]
        elif kind == "CTR_FUNC" and value in (OPTION_END, fxt.LINE_BREAK):
            widths.append(current)
            current = 0
    if current:
        widths.append(current)
    return widths


def family_bound(jp_page, jp_raw, indexes):
    best = 0
    for index in indexes:
        tokens = tokens_of(jp_page[index])
        if not isinstance(getattr(jp_page[index], "text", None), c_ffta_sect_text_buf_ya):
            best = max([best] + option_widths(tokens, jp_raw))
    return best


def derive():
    base = prev.build()[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"FRT_BASELINE_MISMATCH {sha(base)}")
    alloc, meta = uam.glyph_allocation()
    jp, us = meta["jp"], meta["us"]
    jp_raw, us_raw = JP.read_bytes(), US.read_bytes()
    decode, reverse = sysbat.charset_tables()
    render, inverse = prev.renderer(alloc)

    found, scanned = filler_transliterations(base, jp, alloc)
    if found != DEFECT_PATHS:
        raise RuntimeError(f"FRT_BASE_DEFECT_SET {found}")

    kanji = {}
    for char, (code, path) in KANJI.items():
        head, index = split(path)
        jp_tokens = tokens_of(jp.tabs["pages"][head[len("pages:"):]][index])
        if ("CHR_FULL", code) not in jp_tokens:
            raise RuntimeError(f"FRT_KANJI_ATTESTATION {char} {path}")
        kanji[char] = code

    pages, fx = product_pages(base), fxt.product_fx_text(base)
    us_fx = {FX_LEAF: fxt.us_leaf(us, FX_LEAF)}
    rows, plan = [], {}
    for path in DEFECT_PATHS:
        head, index = split(path)
        page, _ = leaf_of(path, pages, fx)
        if head.startswith("pages:"):
            us_page = us.tabs["pages"][head[len("pages:"):]]
            jp_page = jp.tabs["pages"][head[len("pages:"):]]
        else:
            us_page = us_fx[FX_LEAF]
            jp_page = jp.tabs["fx_text"][FX_LEAF]
        if page.tsize != us_page.tsize:
            raise RuntimeError(f"FRT_LEAF_SIZE {path} {page.tsize}/{us_page.tsize}")
        if not isinstance(getattr(jp_page[index], "text", None), c_ffta_sect_text_buf_ya):
            raise RuntimeError(f"FRT_JP_NOT_FILLER {path}")
        us_line = us_page[index]
        us_tokens = tokens_of(us_line)
        flags = us_line.U16(0) & ~fxt.COMPRESSION_BIT
        if path in TEXT:
            jp_tokens = uam.parse_markup(TEXT[path], reverse, kanji)
            if control_skeleton(jp_tokens) != control_skeleton(us_tokens):
                raise RuntimeError(f"FRT_CONTROL_SKELETON {path}")
            tokens = uam.to_slots(jp_tokens, alloc)
            fam_indexes = [i for i in range(jp_page.tsize) if i != index]
            bound = family_bound(jp_page, jp_raw, fam_indexes)
            widths = option_widths(jp_tokens, jp_raw)
            if max(widths) > bound:
                raise RuntimeError(f"FRT_OVER_BOUND {path} {widths} > {bound}")
            data = flags.to_bytes(2, "little") + prev.encode_record(tokens, path)
            route = "TRANSLATED_US_RECORD"
        else:
            tokens = us_tokens
            data = stext.original_line_bytes(us_raw, us_line)
            widths, bound = [], None
            route = "US_RETAIL_RECORD_BYTES"
        plan[path] = {"bytes": data, "tokens": tokens}
        rows.append({"us_logical_path": path, "route": route,
                     "us_text": fxt.visible(us_tokens, decode),
                     "us_record_flags": f"0x{us_line.U16(0):04X}",
                     "jp_record": "YA filler (type 0x%02X)" % (jp_page[index].U16(0) & 0xFF),
                     "japanese": TEXT.get(path),
                     "before": render(tokens_of(page[index])),
                     # A US_RETAIL row's bytes come from the user's own US
                     # ROM; the manifest carries no copy of them.
                     "after": render(tokens) if path in TEXT else None,
                     "widths_px": widths, "family_bound_px": bound})
    return base, alloc, meta, render, rows, plan, scanned


def check_manifest(doc, rows):
    rows = _us_source.redacted(rows)
    if doc.get("records") != rows:
        got = {r["us_logical_path"]: r for r in rows}
        bad = [r["us_logical_path"] for r in doc.get("records", [])
               if got.get(r["us_logical_path"]) != r]
        raise RuntimeError(f"FRT_MANIFEST_DRIFT {bad or len(rows)}")


# ------------------------------------------------------------------- build ---

def pointer_words(raw, value):
    target = struct.pack("<I", value)
    out, at = [], raw.find(target)
    while at != -1:
        if at % 4 == 0:
            out.append(at)
        at = raw.find(target, at + 1)
    return out


def build():
    base, alloc, meta, render, rows, plan, scanned = derive()
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check_manifest(doc, rows)
    us_raw = US.read_bytes()
    snapshot = bytes(base)
    raw = bytearray(base)
    pages, fx = product_pages(snapshot), fxt.product_fx_text(snapshot)

    block_start = uam.free_block_start(base, stext.TAIL_START)
    cursor = block_start
    layout, records = {}, []
    for leaf_name in LEAF_FIELDS:
        targets = {p: plan[p] for p in DEFECT_PATHS if p.rsplit("/", 1)[0] == leaf_name}
        page, _ = leaf_of(next(iter(targets)), pages, fx)
        old_ptr = ROM + page.real_offset
        fields = pointer_words(snapshot, old_ptr)
        if fields != sorted(LEAF_FIELDS[leaf_name]):
            raise RuntimeError(f"FRT_LEAF_FIELDS {leaf_name} "
                               f"{[hex(f) for f in fields]}")
        blob = stext.serialize_leaf(snapshot, page,
                                    {split(p)[1]: t["bytes"] for p, t in targets.items()})
        raw[cursor:cursor + len(blob)] = blob
        for field in fields:
            struct.pack_into("<I", raw, field, ROM + cursor)
        layout[leaf_name] = {"old_cpu_pointer": f"0x{old_ptr:08X}",
                             "new_cpu_pointer": f"0x{ROM + cursor:08X}",
                             "bytes": len(blob),
                             "pointer_words_us_rom": [f"0x{f:08X}" for f in fields]}
        for path, t in targets.items():
            records.append({"us_logical_path": path, "leaf": leaf_name,
                            "_expected": t["tokens"]})
        cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    if len(raw) != len(base):
        raise RuntimeError("FRT_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("FRT_BLOCK_OUTSIDE_TAIL")
    if set(base[block_start:block_end]) != {0xFF} or \
            set(us_raw[block_start:block_end]) != {0xFF}:
        raise RuntimeError("FRT_BLOCK_NOT_FREE")
    layout["block"] = [f"0x{ROM + block_start:08X}", f"0x{ROM + block_end:08X}"]
    return bytes(raw), base, meta, alloc, records, doc, rows, layout, scanned


# ---------------------------------------------------------------- validate ---

def validate(product, base, meta, alloc, records, layout):
    audits = {}
    if sha(OUTROM) != sha(product):
        raise RuntimeError("FRT_READBACK_ROM_MISMATCH")
    disk = OUTROM.read_bytes()
    pages, fx = product_pages(disk), fxt.product_fx_text(disk)
    for leaf_name, lay in layout.items():
        if leaf_name == "block":
            continue
        for field in lay["pointer_words_us_rom"]:
            if u32(disk, int(field, 16)) != int(lay["new_cpu_pointer"], 16):
                raise RuntimeError(f"FRT_READBACK_ROOT {leaf_name} {field}")
    for rec in records:
        page, index = leaf_of(rec["us_logical_path"], pages, fx)
        if tokens_of(page[index]) != rec["_expected"]:
            raise RuntimeError(f"FRT_READBACK_TOKENS {rec['us_logical_path']}")
    audits["readback"] = {"records": len(records), "failures": 0, "result": "PASS",
                          "source": "independent parse of the written ROM file"}

    # recurrence: no record of the product is a YA re-encoding of JP filler
    found, scanned = filler_transliterations(disk, meta["jp"], alloc)
    if found:
        raise RuntimeError(f"FRT_FILLER_TRANSLITERATION {found}")
    audits["filler_transliteration"] = {"jp_ya_records_scanned": scanned,
                                        "found": 0, "result": "PASS"}

    # glyphs: JP retail pixels and widths, none new
    jp_raw = JP.read_bytes()
    inverse = {slot: code for code, slot in alloc.items()}
    jp_font = pta.glyphs.bitmaps(pta.glyphs.font_of(str(JP), "jp"), 0xC66)
    pr_font = pta.glyphs.bitmaps(pta.glyphs.font_of(str(OUTROM), "us"), 0xC67)
    checked = 0
    for rec in records:
        if rec["us_logical_path"] in US_BYTES:
            continue
        for kind, slot in rec["_expected"]:
            if not kind.startswith("CHR"):
                continue
            code = inverse.get(slot)
            if code is None:
                raise RuntimeError("FRT_SLOT_NOT_ALLOCATED 0x%04X" % slot)
            if pr_font.get(slot) != jp_font.get(code):
                raise RuntimeError("FRT_GLYPH_PIXELS 0x%04X" % slot)
            if product[stext.US_METADATA + slot] != jp_raw[stext.JP_METADATA + code]:
                raise RuntimeError("FRT_GLYPH_WIDTH 0x%04X" % slot)
            checked += 1
    if product[stext.US_METADATA:stext.US_METADATA + 0xC67] != \
            base[stext.US_METADATA:stext.US_METADATA + 0xC67]:
        raise RuntimeError("FRT_FONT_METADATA_TOUCHED")
    audits["glyphs"] = {"tokens_checked": checked, "new_glyph_records": 0,
                        "result": "PASS"}

    # every other pages / fx_text record reads the same as the base
    bp, bf = product_pages(base), fxt.product_fx_text(base)
    before, after = dict(all_leaves(bp, bf)), dict(all_leaves(pages, fx))
    changed, total = [], 0
    for name, page in before.items():
        other = after[name]
        if other.tsize != page.tsize:
            raise RuntimeError(f"FRT_LEAF_SIZE_DRIFT {name}")
        for i in range(page.tsize):
            total += 1
            if tokens_of(page[i]) != tokens_of(other[i]):
                changed.append(f"{name}/{i}")
    if sorted(changed) != DEFECT_PATHS:
        raise RuntimeError(f"FRT_CORPUS_DRIFT {sorted(set(changed) ^ set(DEFECT_PATHS))}")
    audits["corpus"] = {"records_compared": total, "records_changed": len(changed),
                        "result": "PASS"}

    # every changed byte is in the block or a declared pointer word
    lo, hi = (int(v, 16) - ROM for v in layout["block"])
    fields = {f + k for name, fs in LEAF_FIELDS.items() for f in fs for k in range(4)}
    diff = [i for i in range(len(base)) if product[i] != base[i]]
    stray = [i for i in diff if not (lo <= i < hi) and i not in fields]
    if stray:
        raise RuntimeError("FRT_UNEXPLAINED_BYTES %s" % [f"0x{i:08X}" for i in stray[:8]])
    audits["binary_touch"] = {"bytes_changed": len(diff), "block_bytes": hi - lo,
                              "pointer_words_rewritten": sum(len(v) for v in LEAF_FIELDS.values()),
                              "executable_bytes_changed": 0, "graphics_bytes_changed": 0,
                              "font_bytes_changed": 0, "result": "PASS"}
    audits["layout"] = layout
    return audits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20261124_production")
    ap.add_argument("--print-sha", action="store_true")
    ap.add_argument("--emit-manifest", help="write the derived records here and stop")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")
    if args.emit_manifest:
        _b, _a, _m, _r, rows, _p, scanned = derive()
        doc = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
        doc.update({"count": len(rows), "records": rows})
        write(Path(args.emit_manifest), doc)
        print(f"records {len(rows)} (jp ya scanned {scanned}) -> {args.emit_manifest}")
        return 0
    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return 0
    second = build()
    if sha(first[0]) != sha(second[0]):
        raise RuntimeError("FRT_BUILD_NONDETERMINISTIC")
    product, base, meta, alloc, records, doc, rows, layout, _scanned = first
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("FRT_DISK_READBACK_MISMATCH")
    audits = validate(product, base, meta, alloc, records, layout)
    if EXPECTED_PRODUCTION and sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(f"CANONICAL_PRODUCTION_MISMATCH {sha(product)}")
    out = RUN_BASE / args.run
    summary = {
        "milestone": "three records that carried JP filler as text (an earlier build, Issue #2)",
        "baseline_sha256": sha(base), "production_sha256": sha(product),
        "rom": str(OUTROM), "evidence": doc.get("evidence"),
        "determinism": {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                        "identical": True},
        "records": rows, "audits": audits,
    }
    write(out / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "records"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
