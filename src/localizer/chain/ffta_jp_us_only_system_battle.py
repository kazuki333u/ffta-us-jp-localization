#! python3
"""Editorial localization of the US-only system, icon, battle and refer text.

Second *class 5* milestone.  49 entries that exist only in the US ROM and have
no JP original anywhere (the last two were added by the runtime-validation
milestone, which corrected the ``words:rumor`` anchors):

* ``words:system`` 73..83   -- mission/dispatch/search system messages (11)
* ``words:ico`` 0..9        -- the eight element abbreviations plus two pure
                               symbol rows (10)
* ``words:battle`` 176..178, 534..542 -- judge/law ability names (12)
* ``pages:battle`` 51..53, 393..400   -- judge/law effect captions (11)
* ``words:refer`` 104..106  -- the three US-only judge names (3)
* ``words:rumor`` 59, 60    -- the two US-only pub rumour titles, "Unfair
                               Judges?" and "Blank Cards" (2)

47 are newly written Japanese.  ``words:ico/8`` (``/``) and ``words:ico/9``
(a symbol row) carry no natural-language content and are recorded
``NO_CHANGE_REQUIRED``: they are already correct for Japanese and this layer
does not rewrite their pointers.

The translations are NOT computed here.  They are read from the tracked
manifest ``data/us_only_system_battle_translations.json``, which is the
editorial decision of record; this module encodes and installs it, and
revalidates it against both ROMs on every build.

Why this family needs no new architecture
-----------------------------------------
* Every written entry uses the ``CHR_FULL`` lane.  Kanji cannot use
  ``CHR_HALF`` -- ``ffta_jp_chr_half_universal_repoint.encode_half_payload``
  emits one byte per value -- and every target family already ships
  ``CHR_FULL`` JP originals, so the lane is attested per family, not assumed.
* A ``CHR_FULL`` token value is a *US font slot*.  The production JP-to-US
  allocation built by the earlier layers already holds a native JP glyph
  record for every character these translations use, so this layer allocates
  **zero** new glyphs and writes **zero** font or metadata bytes.
* ``words:*`` entries are direct-entry repoints: one 4-byte root field each.
* ``pages:battle`` is a single leaf that the page-leaf layer already relocated
  into the tail.  It is re-parsed out of the in-process product, re-serialized
  with the 11 replacements, placed further down the tail and repointed, so all
  390 previously localized entries survive byte-for-byte.

Consequences: **zero new glyph records, zero font or metadata writes, zero ROM
code bytes patched.**
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_us_only_words_name as prev
from ffta_sect import (c_ffta_sect_rom, c_ffta_sect_text_buf,
                       c_ffta_sect_text_page, _trim_raw_len, _words_sect_info)
import ffta_us_source as _us_source

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / "rom/original/FFTA_JP.gba"
US = ROOT / "rom/original/FFTA_US.gba"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "data/us_only_system_battle_translations.json"
CHARSET = HERE / "charset_us.json"
RUN_BASE = HERE / "build/us_only_system_battle_localization"
OUTROM = ROOT / "rom/build/ffta_us_jp_system_battle_localized.gba"
OUTROM2 = ROOT / "rom/build/ffta_us_jp_system_battle_localized_repeat.gba"

ROM = 0x08000000
# Output of ffta_jp_us_only_words_name, the previous production layer.
BASELINE = "1786C8A35140E4EE437516A9F259C60EA5E6FBE2BD583A854069A1368562FE3F"
# Terminal artifact of the production chain after this layer.  This module is
# now the last layer, so its output IS the canonical production ROM and this is
# the single canonical final-SHA authority.
EXPECTED_PRODUCTION = "8BCD0FB7CB3D5AA9A0168C1EDC5EC88EABD2FC943F5FFF223EB05E98B715F14C"

PAGES_ROOT_FIELD = 0x237F4        # US pages:battle root pointer field
PAGES_TSIZE = 401                 # 390 JP-matched + 11 US-only insertions
WORDS_FAMILIES = ("system", "ico", "battle", "refer", "rumor")
CTR_TERMINATOR = 0x42
CTR_NAME_REF = 0x441
CTR_WORD_SPACE = 0x52
COMPRESSION_BIT = 0x0002
# The widest single rendered line among the already-localized JP originals of
# each family, in JP font advance pixels.  words:ico has no JP counterpart
# table at all (JP tsize 0), so its bound is the widest US original ("FIRE").
WIDTH_BOUND = {"words:system": 192, "words:battle": 127, "words:refer": 74,
               "pages:battle": 192, "words:ico": 27, "words:rumor": 106}
LINE_BREAKS = {0x1D02, 0x4D}
# Root pointer fields and entry counts of the four US words tables this layer
# touches, as declared by ffta_sect.load_rom_us.  They are repeated here so the
# independent readback can parse the written ROM without the full US section
# map: the whole-ROM loader trips over its own table-size heuristics on every
# production ROM (it already fails on the baseline), because tail-relocated
# payloads sit outside the ranges it guesses.  Both the offsets and the sizes
# are cross-checked against the parsed pristine US ROM on every build.
US_WORDS_ROOTS = {"ico": (0x198D0, 0xA), "refer": (0x9A08, 0x6B),
                  "battle": (0x9028, 0x301), "system": (0x39F54, 0x54),
                  "rumor": (0x5FD9C, 0x5C)}


def sha(data):
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ---------------------------------------------------------------- charset ---

def charset_tables():
    """charset index -> character, and its inverse, for the shared low span.

    ``charset_us.json`` is tracked.  Below 0x122 the JP glyph code, the US font
    slot and this index are the same number -- that is why decoding JP original
    text with this table produces correct Japanese.
    """
    decode, _encode = json.loads(CHARSET.read_text(encoding="utf-8"))
    table = {int(k): v for k, v in decode.items() if int(k) < 0x122}
    reverse = {}
    for code, char in sorted(table.items()):
        reverse.setdefault(char, code)
    for code, char in ((0x4F, "を"), (0xA5, "ー"), (0xEB, "!"), (0xE5, "「")):
        if table.get(code) != char:
            raise RuntimeError(f"CHARSET_ANCHOR_FAILED 0x{code:02X}")
    return table, reverse


def jp_entry(jp, path):
    """Resolve a JP logical path such as ``s_text/8/47`` to its token list."""
    if path.startswith("pages:"):
        family, index = path[len("pages:"):].rsplit("/", 1)
        line = jp.tabs["pages"][family][int(index)]
    elif path.startswith("words:"):
        family, index = path[len("words:"):].rsplit("/", 1)
        line = jp.tabs["words"][family][int(index)]
    else:
        family, leaf, index = path.split("/")
        line = jp.tabs[family][int(leaf)][int(index)]
    text = getattr(line, "text", line)
    return list(getattr(text, "tokens", []) or [])


def kanji_table(doc, jp):
    """Kanji -> JP glyph code, each revalidated against its JP attestation.

    Production never trusts a font-alignment guess.  Every kanji in the
    manifest names a JP-original entry that actually uses that glyph code, and
    the claim is re-checked here on every build.
    """
    table = {}
    for char, row in doc["kanji_codes"].items():
        code = int(row["jp_glyph_code"], 16)
        if code < 0x122:
            raise RuntimeError(f"KANJI_CODE_NOT_IN_KANJI_SPAN {char}")
        path = row["attested_at"]
        if not path:
            raise RuntimeError(f"KANJI_ATTESTATION_MISSING {char}")
        if not any(kind == "CHR_FULL" and value == code
                   for kind, value in jp_entry(jp, path)):
            raise RuntimeError(f"KANJI_ATTESTATION_FAILED {char} 0x{code:04X} {path}")
        table[char] = code
    return table


# --------------------------------------------------------------- manifest ---

def visible(tokens, decode):
    out = []
    for kind, value in tokens:
        if kind.startswith("CHR"):
            out.append(decode.get(value, f"<{value:04X}>"))
        elif kind == "CTR_FUNC" and value == CTR_WORD_SPACE:
            out.append(" ")
        else:
            out.append(f"[{value:X}]")
    return "".join(out)


def us_tokens(us, family, index):
    kind, name = family.split(":")
    table = us.tabs["pages"][name] if kind == "pages" else us.tabs["words"][name]
    line = table[index]
    return list(getattr(line, "text", line).tokens)


def load_manifest(us, decode):
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = doc["entries"]
    if len(rows) != 49 or doc["count"] != 49:
        raise RuntimeError(f"SYSBAT_MANIFEST_COUNT {len(rows)}/{doc['count']}")
    if doc["baseline_production_sha256"] != BASELINE:
        raise RuntimeError("SYSBAT_MANIFEST_BASELINE_DRIFT")
    expected = {
        "words:system": list(range(73, 84)),
        "words:ico": list(range(0, 10)),
        "words:battle": [176, 177, 178] + list(range(534, 543)),
        "pages:battle": [51, 52, 53] + list(range(393, 401)),
        "words:refer": [104, 105, 106],
        "words:rumor": [59, 60],
    }
    got = {}
    for row in rows:
        got.setdefault(row["family"], []).append(row["us_index"])
        if row["us_logical_path"] != f"{row['family']}/{row['us_index']}":
            raise RuntimeError(f"SYSBAT_MANIFEST_PATH {row['us_logical_path']}")
        if not _us_source.matches(row, visible(
                us_tokens(us, row["family"], row["us_index"]), decode)):
            raise RuntimeError(
                f"SYSBAT_MANIFEST_ENGLISH_DRIFT {row['us_logical_path']}")
        if row["status"] == "NO_CHANGE_REQUIRED":
            if row["japanese"] is not None:
                raise RuntimeError(f"SYSBAT_NO_CHANGE_HAS_TEXT {row['us_logical_path']}")
            # A NO_CHANGE_REQUIRED entry must genuinely carry no natural
            # language: no letter, digit, kana or kanji, only symbols.
            for kind, value in us_tokens(us, row["family"], row["us_index"]):
                if not kind.startswith("CHR"):
                    continue
                char = decode.get(value, "")
                if value >= 0x122 or char.isalnum():
                    raise RuntimeError(
                        f"SYSBAT_NO_CHANGE_NOT_SYMBOLIC {row['us_logical_path']}")
        elif row["status"] == "TRANSLATED_REVIEWED":
            if not row["japanese"]:
                raise RuntimeError(f"SYSBAT_MANIFEST_EMPTY {row['us_logical_path']}")
        else:
            raise RuntimeError(f"SYSBAT_MANIFEST_STATUS {row['us_logical_path']}")
    if got != expected:
        raise RuntimeError(f"SYSBAT_MANIFEST_INDEX_SET {got}")
    translated = [r for r in rows if r["status"] == "TRANSLATED_REVIEWED"]
    unchanged = [r for r in rows if r["status"] == "NO_CHANGE_REQUIRED"]
    if (len(translated), len(unchanged)) != (doc["translated_count"],
                                             doc["no_change_required_count"]):
        raise RuntimeError("SYSBAT_MANIFEST_STATUS_COUNTS")
    if (len(translated), len(unchanged)) != (47, 2):
        raise RuntimeError(f"SYSBAT_BATCH_SHAPE {len(translated)}/{len(unchanged)}")
    return doc, rows, translated, unchanged


# --------------------------------------------------------------- encoding ---

def parse_markup(japanese, reverse, kanji):
    """``[441]はJPをゲット![42]`` -> JP-glyph-code token list."""
    tokens = []
    i = 0
    while i < len(japanese):
        char = japanese[i]
        if char == "[":
            j = japanese.index("]", i)
            tokens.append(("CTR_FUNC", int(japanese[i + 1:j], 16)))
            i = j + 1
            continue
        if char in kanji:
            tokens.append(("CHR_FULL", kanji[char]))
        elif char in reverse:
            tokens.append(("CHR_FULL", reverse[char]))
        else:
            raise RuntimeError(f"SYSBAT_UNENCODABLE {char!r} in {japanese!r}")
        i += 1
    return tokens


def rendered_width(tokens, jp_raw):
    """Widest rendered line, in JP font advance pixels."""
    widest = segment = 0
    for kind, value in tokens:
        if kind.startswith("CHR"):
            segment += jp_raw[stext.JP_METADATA + value]
        elif value in LINE_BREAKS:
            widest, segment = max(widest, segment), 0
    return max(widest, segment)


def to_slots(tokens, alloc):
    out = []
    for kind, value in tokens:
        if kind == "CHR_FULL":
            if value not in alloc:
                raise RuntimeError(f"SYSBAT_GLYPH_NOT_ALLOCATED 0x{value:04X}")
            out.append(("CHR_FULL", alloc[value]))
        else:
            out.append((kind, value))
    return out


def encode(row, reverse, kanji, alloc, jp_raw, want_terminator):
    jp_tokens = parse_markup(row["japanese"], reverse, kanji)
    if any(kind == "CTR_FUNC" and value == CTR_WORD_SPACE
           for kind, value in jp_tokens):
        raise RuntimeError(f"SYSBAT_WORD_SPACE_KEPT {row['us_logical_path']}")
    width = rendered_width(jp_tokens, jp_raw)
    bound = WIDTH_BOUND[row["family"]]
    if width > bound:
        raise RuntimeError(
            f"SYSBAT_WIDTH_OVER_FAMILY_BOUND {row['us_logical_path']} {width}>{bound}")
    original = us_tokens_cache[row["us_logical_path"]]
    had_terminator = any(kind == "CTR_FUNC" and value == CTR_TERMINATOR
                         for kind, value in original)
    has_terminator = any(kind == "CTR_FUNC" and value == CTR_TERMINATOR
                         for kind, value in jp_tokens)
    if had_terminator != has_terminator or has_terminator != want_terminator:
        raise RuntimeError(f"SYSBAT_TERMINATOR_MISMATCH {row['us_logical_path']}")
    for kind, value in original:
        if kind == "CTR_FUNC" and value == CTR_NAME_REF:
            if not any(k == "CTR_FUNC" and v == CTR_NAME_REF for k, v in jp_tokens):
                raise RuntimeError(f"SYSBAT_NAME_REF_DROPPED {row['us_logical_path']}")
    expected = to_slots(jp_tokens, alloc)
    data = stext.encode_standard(expected)
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if probe.tokens != expected:
        raise RuntimeError(f"SYSBAT_SERIALIZER_ROUNDTRIP_FAILED {row['us_logical_path']}")
    if data[-1] != 0:
        raise RuntimeError(f"SYSBAT_EOS_MISSING {row['us_logical_path']}")
    return jp_tokens, expected, data, width


us_tokens_cache: dict[str, list] = {}


# ------------------------------------------------------------------ build ---

def independent_rom(path):
    """Parse the written ROM file, following its actual root pointers.

    Deliberately shares no structure with the builder: it re-reads the bytes
    from disk and re-resolves every root, so a readback failure cannot be
    masked by a builder-side object.
    """
    raw = Path(path).read_bytes()
    return c_ffta_sect_rom(raw, 0).setup({
        **_words_sect_info({name: (root, size)
                            for name, (root, size) in US_WORDS_ROOTS.items()}),
        "pages:battle": (PAGES_ROOT_FIELD, c_ffta_sect_text_page),
    }, _trim_raw_len(raw, 0xF00000))


def product_pages_leaf(product):
    """Re-parse the already relocated pages:battle leaf out of the product."""
    rom = c_ffta_sect_rom(bytes(product), 0).setup(
        {"pages:battle": (PAGES_ROOT_FIELD, c_ffta_sect_text_page)},
        _trim_raw_len(product, 0xF00000))
    leaf = rom.tabs["pages"]["battle"]
    if leaf.tsize != PAGES_TSIZE:
        raise RuntimeError(f"SYSBAT_PAGES_TSIZE {leaf.tsize}")
    return leaf


def build():
    previous = prev.build()
    base = previous[0]
    if sha(base) != BASELINE:
        raise RuntimeError(f"PRODUCTION_BASELINE_MISMATCH {sha(base)}")
    meta, alloc, tail_end = previous[2], previous[3], previous[9]
    jp, us = meta["jp"], meta["us"]
    jp_raw = JP.read_bytes()

    decode, reverse = charset_tables()
    doc, rows, translated, unchanged = load_manifest(us, decode)
    kanji = kanji_table(doc, jp)
    us_tokens_cache.clear()
    for row in rows:
        us_tokens_cache[row["us_logical_path"]] = us_tokens(
            us, row["family"], row["us_index"])

    raw = bytearray(base)
    cursor = block_start = stext.align(tail_end, 4)
    records = []

    # -- words:*  direct-entry repoint -------------------------------------
    for name in WORDS_FAMILIES:
        family = f"words:{name}"
        pool = us.tabs["words"][name]
        for row in [r for r in translated if r["family"] == family]:
            index = row["us_index"]
            jp_tokens, expected, data, width = encode(
                row, reverse, kanji, alloc, jp_raw, want_terminator=False)
            field = pool.real_offset + index * 4
            old = int.from_bytes(raw[field:field + 4], "little")
            raw[cursor:cursor + len(data)] = data
            raw[field:field + 4] = (ROM + cursor).to_bytes(4, "little")
            records.append({
                "family": family, "us_index": index,
                "us_logical_path": row["us_logical_path"],
                "original_english_sha256": _us_source.digest(row),
                "japanese": row["japanese"], "route": "WORDS_DIRECT_REPOINT",
                "rendered_width_px": width,
                "root_pointer_field_us_rom": f"0x{field:08X}",
                "original_cpu_pointer": f"0x{old:08X}",
                "new_cpu_pointer": f"0x{ROM + cursor:08X}",
                "payload_offset_us_rom": f"0x{cursor:08X}",
                "payload_length": len(data), "eos": True, "roundtrip": "PASS",
                "_expected": expected, "_data": data})
            cursor = stext.align(cursor + len(data), 4)
    words_block_end = cursor

    # -- pages:battle  whole-leaf recomposition ----------------------------
    leaf = product_pages_leaf(raw)
    replacements, leaf_expected = {}, {}
    for row in [r for r in translated if r["family"] == "pages:battle"]:
        index = row["us_index"]
        jp_tokens, expected, data, width = encode(
            row, reverse, kanji, alloc, jp_raw, want_terminator=True)
        flags = leaf[index].U16(0) & ~COMPRESSION_BIT
        replacements[index] = flags.to_bytes(2, "little") + data
        leaf_expected[index] = expected
        records.append({
            "family": "pages:battle", "us_index": index,
            "us_logical_path": row["us_logical_path"],
            "original_english_sha256": _us_source.digest(row),
            "japanese": row["japanese"], "route": "PAGES_LEAF_RECOMPOSE",
            "rendered_width_px": width,
            "record_flags": f"0x{flags:04X}", "payload_length": len(data),
            "eos": True, "roundtrip": "PASS",
            "_expected": expected, "_data": data})
    if len(replacements) != 11:
        raise RuntimeError(f"SYSBAT_PAGES_REPLACEMENT_COUNT {len(replacements)}")
    blob = stext.serialize_leaf(bytes(raw), leaf, replacements)
    leaf_offset = cursor
    old_leaf_pointer = int.from_bytes(raw[PAGES_ROOT_FIELD:PAGES_ROOT_FIELD + 4], "little")
    raw[cursor:cursor + len(blob)] = blob
    raw[PAGES_ROOT_FIELD:PAGES_ROOT_FIELD + 4] = (ROM + cursor).to_bytes(4, "little")
    cursor = stext.align(cursor + len(blob), 4)
    block_end = cursor

    leaf_record = {
        "family": "pages:battle", "root_field_us_rom": f"0x{PAGES_ROOT_FIELD:08X}",
        "old_cpu_pointer": f"0x{old_leaf_pointer:08X}",
        "new_cpu_pointer": f"0x{ROM + leaf_offset:08X}",
        "previous_leaf_us_rom": f"0x{leaf.real_offset:08X}",
        "previous_size": leaf.sect_top, "new_leaf_us_rom": f"0x{leaf_offset:08X}",
        "new_size": len(blob), "entries": leaf.tsize,
        "localized_entries_replaced": len(replacements),
        "preserved_entries": leaf.tsize - len(replacements)}

    if len(raw) != len(base):
        raise RuntimeError("SYSBAT_ROM_SIZE_CHANGED")
    if not (stext.TAIL_START <= block_start
            and block_end <= stext.TAIL_START + stext.TAIL_CAPACITY):
        raise RuntimeError("SYSBAT_BLOCK_OUTSIDE_TAIL")
    return (bytes(raw), base, meta, alloc, records, doc, rows, unchanged,
            leaf_record, leaf_expected, replacements, block_start,
            words_block_end, leaf_offset, block_end, kanji)


# --------------------------------------------------------------- validate ---

def validate(product, base, meta, alloc, records, rows, unchanged, leaf_record,
             leaf_expected, block_start, words_block_end, leaf_offset, block_end,
             decode, doc):
    us = meta["us"]
    audits = {}

    # -- installed payload / roundtrip (builder side) -----------------------
    for rec in [r for r in records if r["route"] == "WORDS_DIRECT_REPOINT"]:
        field = int(rec["root_pointer_field_us_rom"], 16)
        start = int(rec["payload_offset_us_rom"], 16)
        if int.from_bytes(product[field:field + 4], "little") != ROM + start:
            raise RuntimeError(f"SYSBAT_ROOT_POINTER_AUDIT_FAILED {rec['us_logical_path']}")
        if product[start:start + rec["payload_length"]] != rec["_data"]:
            raise RuntimeError(f"SYSBAT_PAYLOAD_AUDIT_FAILED {rec['us_logical_path']}")
        if not block_start <= start < words_block_end:
            raise RuntimeError(f"SYSBAT_PAYLOAD_OUTSIDE_BLOCK {rec['us_logical_path']}")

    # -- independent ROM readback ------------------------------------------
    inverse = {slot: code for code, slot in alloc.items()}
    # The readback decoder must know the kanji too, and it learns them from the
    # tracked manifest, whose codes were revalidated against the JP ROM.
    decode = dict(decode)
    decode.update({int(row["jp_glyph_code"], 16): char
                   for char, row in doc["kanji_codes"].items()})

    def char(value):
        if value in inverse:
            return decode.get(inverse[value], f"<jp:{inverse[value]:04X}>")
        return decode.get(value, f"<us:{value:04X}>")

    def render(tokens):
        out = []
        for kind, value in tokens:
            if kind.startswith("CHR"):
                out.append(char(value))
            elif kind == "CTR_FUNC" and value == CTR_WORD_SPACE:
                out.append(" ")
            else:
                out.append(f"[{value:X}]")
        return "".join(out)

    if sha(OUTROM) != sha(product):
        raise RuntimeError("SYSBAT_READBACK_ROM_MISMATCH")
    for name, (root, size) in US_WORDS_ROOTS.items():
        pool = us.tabs["words"][name]
        if pool.tsize != size:
            raise RuntimeError(f"SYSBAT_WORDS_ROOT_DECL_DRIFT words:{name}")
    check = independent_rom(OUTROM)
    readback_rows = []
    for row in rows:
        kind, name = row["family"].split(":")
        table = (check.tabs["pages"][name] if kind == "pages"
                 else check.tabs["words"][name])
        line = table[row["us_index"]]
        tokens = list(getattr(line, "text", line).tokens)
        got = render(tokens)
        want = row["original_english"] if row["status"] == "NO_CHANGE_REQUIRED" \
            else row["japanese"]
        if got != want:
            raise RuntimeError(
                f"SYSBAT_READBACK_TEXT_MISMATCH {row['us_logical_path']} "
                f"{got!r} != {want!r}")
        if row["family"] == "pages:battle":
            if not tokens or tokens[-1] != ("CTR_FUNC", CTR_TERMINATOR):
                raise RuntimeError(f"SYSBAT_READBACK_TERMINATOR {row['us_logical_path']}")
        else:
            if any(k == "CTR_FUNC" and v == CTR_TERMINATOR for k, v in tokens):
                raise RuntimeError(f"SYSBAT_READBACK_STRAY_TERMINATOR {row['us_logical_path']}")
        if any(k == "CTR_FUNC" and v == CTR_WORD_SPACE for k, v in tokens) \
                and row["status"] != "NO_CHANGE_REQUIRED":
            raise RuntimeError(f"SYSBAT_READBACK_WORD_SPACE {row['us_logical_path']}")
        readback_rows.append({"us_logical_path": row["us_logical_path"],
                              "status": row["status"], "decoded": got,
                              "result": "PASS"})
    audits["readback"] = {"entries": len(readback_rows), "failures": 0,
                          "translated": sum(r["status"] == "TRANSLATED_REVIEWED"
                                            for r in readback_rows),
                          "no_change_required": sum(r["status"] == "NO_CHANGE_REQUIRED"
                                                    for r in readback_rows),
                          "source": "independent parse of the written ROM file",
                          "result": "PASS", "rows": readback_rows}

    # -- sibling / alias ----------------------------------------------------
    sibling = {}
    for name in WORDS_FAMILIES:
        pool = us.tabs["words"][name]
        owned = {r["us_index"] for r in records
                 if r["family"] == f"words:{name}" and r["route"] == "WORDS_DIRECT_REPOINT"}
        moved = [i for i in range(pool.tsize) if i not in owned
                 and product[pool.real_offset + i * 4: pool.real_offset + i * 4 + 4]
                 != base[pool.real_offset + i * 4: pool.real_offset + i * 4 + 4]]
        if moved:
            raise RuntimeError(f"SYSBAT_SIBLING_ROOT_MOVED words:{name} {moved}")
        pointers = [int.from_bytes(product[pool.real_offset + i * 4:
                                           pool.real_offset + i * 4 + 4], "little")
                    for i in range(pool.tsize)]
        ours = {p for i, p in enumerate(pointers) if i in owned}
        if len(ours) != len(owned):
            raise RuntimeError(f"SYSBAT_POINTER_COLLISION words:{name}")
        if ours & {p for i, p in enumerate(pointers) if i not in owned}:
            raise RuntimeError(f"SYSBAT_ALIAS_PROPAGATION words:{name}")
        surviving = {p for i, p in enumerate(pointers) if i not in owned}
        for rec in [r for r in records if r["family"] == f"words:{name}"]:
            lo = ROM + int(rec["payload_offset_us_rom"], 16)
            hi = lo + rec["payload_length"]
            if any(lo <= p < hi for p in surviving):
                raise RuntimeError(f"SYSBAT_ALIAS_HAZARD {rec['us_logical_path']}")
        sibling[f"words:{name}"] = {"family_entries": pool.tsize, "owned": len(owned),
                                    "sibling_root_fields_changed": 0,
                                    "pointer_collisions": 0, "alias_propagation": 0}
    # NO_CHANGE_REQUIRED entries must keep their pristine root pointer.
    for row in unchanged:
        pool = us.tabs["words"][row["family"].split(":")[1]]
        field = pool.real_offset + row["us_index"] * 4
        if product[field:field + 4] != base[field:field + 4]:
            raise RuntimeError(f"SYSBAT_NO_CHANGE_POINTER_REWRITTEN {row['us_logical_path']}")
    sibling["no_change_required_pointers_preserved"] = len(unchanged)

    # words:name -- the previous editorial milestone -- must be untouched.
    name_pool = us.tabs["words"]["name"]
    span = name_pool.real_offset, name_pool.real_offset + name_pool.tsize * 4
    if product[span[0]:span[1]] != base[span[0]:span[1]]:
        raise RuntimeError("SYSBAT_WORDS_NAME_PRODUCTION_DISTURBED")
    sibling["words_name_entries_preserved"] = name_pool.tsize
    audits["sibling"] = {**sibling, "result": "PASS"}

    # -- pages:battle leaf: every non-target record byte-identical ----------
    leaf = product_pages_leaf(product)
    before = product_pages_leaf(base)
    if leaf.tsize != PAGES_TSIZE or before.tsize != PAGES_TSIZE:
        raise RuntimeError("SYSBAT_LEAF_TSIZE_CHANGED")
    if leaf.real_offset != leaf_offset:
        raise RuntimeError("SYSBAT_LEAF_NOT_AT_EXPECTED_OFFSET")
    changed = []
    for index in range(leaf.tsize):
        after_bytes = bytes(leaf[index].BYTES(0, leaf[index].raw_len))
        prior_bytes = bytes(before[index].BYTES(0, before[index].raw_len))
        if after_bytes != prior_bytes:
            changed.append(index)
    if sorted(changed) != sorted(leaf_expected):
        raise RuntimeError(f"SYSBAT_LEAF_UNEXPECTED_RECORD_CHANGE {changed}")
    for index, expected in leaf_expected.items():
        if list(leaf[index].text.tokens) != expected:
            raise RuntimeError(f"SYSBAT_LEAF_TOKEN_MISMATCH {index}")
    audits["leaf"] = {"entries": leaf.tsize, "records_replaced": len(leaf_expected),
                      "records_byte_preserved": leaf.tsize - len(leaf_expected),
                      "previous_leaf": leaf_record["previous_leaf_us_rom"],
                      "new_leaf": leaf_record["new_leaf_us_rom"],
                      "new_size": leaf_record["new_size"], "result": "PASS"}

    # -- glyph / asset ------------------------------------------------------
    font = us.tabs["font"]
    fbase = font.real_offset
    if product[fbase:fbase + font.tsize * stext.FONT_STRIDE] != \
       base[fbase:fbase + font.tsize * stext.FONT_STRIDE]:
        raise RuntimeError("SYSBAT_FONT_TABLE_CHANGED")
    if product[stext.US_METADATA:stext.US_METADATA + font.tsize] != \
       base[stext.US_METADATA:stext.US_METADATA + font.tsize]:
        raise RuntimeError("SYSBAT_FONT_METADATA_CHANGED")
    used = sorted({v for rec in records for k, v in rec["_expected"] if k == "CHR_FULL"})
    allocated = set(alloc.values())
    stray = [v for v in used if v not in allocated]
    if stray:
        raise RuntimeError(f"SYSBAT_SLOT_NOT_FROM_JP_ALLOCATION {stray[:8]}")
    jp_raw = JP.read_bytes()
    for slot in used:
        code = inverse[slot]
        joff = meta["jp"].tabs["font"].real_offset + code * stext.FONT_STRIDE
        uoff = fbase + slot * stext.FONT_STRIDE
        if product[uoff:uoff + stext.FONT_STRIDE] != jp_raw[joff:joff + stext.FONT_STRIDE]:
            raise RuntimeError(f"SYSBAT_SLOT_NOT_JP_IDENTICAL 0x{slot:04X}")
    audits["glyph"] = {"new_glyph_records": 0, "font_table_writes": 0,
                       "metadata_writes": 0, "low_slot_overwrites": 0,
                       "existing_mappings_changed": 0,
                       "distinct_slots_used": len(used),
                       "all_slots_are_native_jp_records": True,
                       "last_allocated_slot": f"0x{max(allocated):04X}",
                       "result": "PASS"}

    # -- binary touch -------------------------------------------------------
    permitted = [(int(r["root_pointer_field_us_rom"], 16),
                  int(r["root_pointer_field_us_rom"], 16) + 4)
                 for r in records if r["route"] == "WORDS_DIRECT_REPOINT"]
    permitted.append((PAGES_ROOT_FIELD, PAGES_ROOT_FIELD + 4))
    permitted.append((block_start, block_end))
    permitted.sort()
    merged = []
    for lo, hi in permitted:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    changed_ranges = list(stext.changed_ranges(base, product))
    unexplained = [(f"0x{lo:08X}", f"0x{hi:08X}") for lo, hi in changed_ranges
                   if not any(a <= lo and hi <= b for a, b in merged)]
    if unexplained:
        raise RuntimeError(f"SYSBAT_BINARY_TOUCH_REGRESSION {unexplained[:8]}")
    fields = {int(r["root_pointer_field_us_rom"], 16)
              for r in records if r["route"] == "WORDS_DIRECT_REPOINT"}
    fields.add(PAGES_ROOT_FIELD)
    code_ranges = [c for c in changed_ranges if c[0] < 0x400000
                   and not any(f <= c[0] and c[1] <= f + 4 for f in fields)]
    if code_ranges:
        raise RuntimeError(f"SYSBAT_CODE_TOUCHED {code_ranges[:4]}")
    audits["binary"] = {
        "result": "PASS", "changed_ranges": len(changed_ranges),
        "root_pointer_fields": len(fields), "relocated_payload_blocks": 2,
        "new_font_slots": 0, "unexplained_ranges": 0,
        "rom_executable_code_bytes_changed": 0,
        "block": {"start": f"0x{block_start:08X}",
                  "words_end": f"0x{words_block_end:08X}",
                  "leaf_start": f"0x{leaf_offset:08X}",
                  "end": f"0x{block_end:08X}",
                  "bytes": block_end - block_start,
                  "remaining_tail": stext.TAIL_START + stext.TAIL_CAPACITY - block_end}}
    return audits


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20260830_production")
    ap.add_argument("--print-sha", action="store_true",
                    help="build once, print the resulting SHA-256 and exit "
                         "without writing a ROM; used only to adopt the "
                         "constant after a deliberate change")
    args = ap.parse_args()
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError("ORIGINAL_ROM_HASH_MISMATCH")

    first = build()
    if args.print_sha:
        print(sha(first[0]))
        return

    second = build()
    (product, base, meta, alloc, records, doc, rows, unchanged, leaf_record,
     leaf_expected, replacements, bs, wbe, lo, be, kanji) = first
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rs]
    if sha(product) != sha(second[0]) or strip(records) != strip(second[4]):
        raise RuntimeError("SYSBAT_BUILD_NONDETERMINISTIC")
    if (bs, wbe, lo, be) != (second[11], second[12], second[13], second[14]):
        raise RuntimeError("SYSBAT_LAYOUT_NONDETERMINISTIC")

    out = RUN_BASE / args.run
    out.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    OUTROM.write_bytes(product)
    OUTROM2.write_bytes(second[0])
    if sha(OUTROM) != sha(product) or sha(OUTROM2) != sha(second[0]):
        raise RuntimeError("PRODUCTION_ROM_REREAD_FAILED")

    decode, _reverse = charset_tables()
    audits = validate(product, base, meta, alloc, records, rows, unchanged,
                      leaf_record, leaf_expected, bs, wbe, lo, be, decode, doc)
    if sha(product) != EXPECTED_PRODUCTION:
        raise RuntimeError(
            f"CANONICAL_PRODUCTION_MISMATCH {sha(product)} != {EXPECTED_PRODUCTION}")

    determinism = {"sha256_1": sha(product), "sha256_2": sha(second[0]),
                   "identical": True, "record_set_identical": True,
                   "pointer_layout_identical":
                       [r.get("new_cpu_pointer") for r in records] ==
                       [r.get("new_cpu_pointer") for r in second[4]],
                   "serialized_text_identical":
                       [r["japanese"] for r in records] ==
                       [r["japanese"] for r in second[4]],
                   "glyph_allocation_identical": alloc == second[3],
                   "baseline_sha256": sha(base)}
    coverage_report = {
        "class_1_production_jp_source_transfer": 11168,
        "class_5_editorial_produced_before": 597,
        "class_5_editorial_translated_by_this_layer": doc["translated_count"],
        "class_5_no_change_required_by_this_layer": doc["no_change_required_count"],
        "note": "the two NO_CHANGE_REQUIRED symbol rows carry no natural language; "
                "they leave editorial scope as satisfied, not as translated. "
                "Recompute the exact remainder with "
                "ffta_jp_us_only_editorial_scope.py -- never by arithmetic."}
    summary = {"status": "PRODUCTION_VALIDATED", "milestone": args.run,
               "baseline_sha256": sha(base), "production_sha256": sha(product),
               "entries": {"resolved": doc["count"],
                           "translated": doc["translated_count"],
                           "no_change_required": doc["no_change_required_count"],
                           "by_family": {f: sum(1 for r in records if r["family"] == f)
                                         for f in ("words:system", "words:ico",
                                                   "words:battle", "pages:battle",
                                                   "words:refer")}},
               "kanji_verified_from_jp_originals": len(kanji),
               "audits": {k: v["result"] for k, v in audits.items()},
               "determinism": determinism, "coverage": coverage_report}
    write(out / "summary.json", summary)
    write(out / "translation_records.json", strip(records))
    write(out / "readback.json", audits["readback"])
    write(out / "sibling_alias_audit.json", audits["sibling"])
    write(out / "leaf_audit.json", audits["leaf"])
    write(out / "glyph_audit.json", audits["glyph"])
    write(out / "binary_touch.json", audits["binary"])
    write(out / "leaf_manifest.json", leaf_record)
    write(out / "determinism.json", determinism)
    write(out / "coverage.json", coverage_report)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
