#! python3
"""Production s_text JP importer using tail-relocated leaf-page bundles only.

This deliberately does not call ``repack_with``.  The original section model
is used solely to discover the pristine hierarchy and AUTO_MATCH token pairs.
The emitted data is the native text-page format: u16 line offsets, FF FF, and
aligned line records.  Root page 61 is the one existing intermediate table;
it is copied as a new tail-local bundle with its new child pointers, while the
only pointer changed in the original s_text hierarchy remains its root entry.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
from ffta_sect import c_ffta_sect_text_buf, c_ffta_sect_text_buf_ya, c_ffta_sect_text_line, c_ffta_sect_text_page, c_ffta_sect_text_sub, load_rom_jp, load_rom_us

ROOT = Path(__file__).resolve().parents[3]
JP_ROM = ROOT / "rom/original/FFTA_JP.gba"
US_ROM = ROOT / "rom/original/FFTA_US.gba"
OUT_ROM = ROOT / "rom/build/ffta_us_jp_s_text_leaf_repoint.gba"
OUT_REPEAT = ROOT / "rom/build/ffta_us_jp_s_text_leaf_repoint_repeat.gba"
OUT = ROOT / "analysis/dynamic/runtime/s_text_leaf_repoint"

ROM_BASE = 0x08000000
TAIL_START = 0xA39920                 # independently verified pristine-US FF tail
TAIL_CAPACITY = 0x5C66E0
ALLOC_START = 0x122
FONT_STRIDE = 0x80
JP_METADATA = 0x488330
US_METADATA = 0x4966B0
EXPECTED_ENTRIES = 5445
EXPECTED_ROOT_PAGES = 37
EXPECTED_TOPS = set(range(35)) | {61, 62}


def sha(data: bytes | Path) -> str:
    if isinstance(data, Path): data = data.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def align(value: int, width: int) -> int:
    return (value + width - 1) // width * width


def ptext(path) -> str:
    return "/".join(map(str, path))


def text_tokens(line):
    return list(line.text.tokens)


def encode_standard(tokens) -> bytes:
    # The established parser's standard encoder is the token specification;
    # use it only as an encoder, never as a repacker or page serializer.
    probe = c_ffta_sect_text_buf(bytearray(), 0)
    probe._make_ctr_tab()
    return bytes(probe._encode(tokens))


def original_line_bytes(raw: bytes, line) -> bytes:
    """The raw line record, excluding only inter-record alignment padding."""
    return raw[line.real_offset:line.real_offset + line.raw_len]


def replacement_line(jp_line, allocation) -> bytes:
    flags = jp_line.U16(0)
    source = jp_line.text
    if isinstance(source, c_ffta_sect_text_buf_ya):
        # All included YA records contain only controls; keeping their native
        # JP bytes is exact and avoids inventing a YA encoder.
        if any(kind.startswith("CHR_") for kind, _ in source.tokens):
            raise ValueError("CHR-bearing YA source is not safe")
        return bytes(jp_line.BYTES(0, jp_line.raw_len))
    if not isinstance(source, c_ffta_sect_text_buf):
        raise TypeError(type(source).__name__)
    if any(kind == "CHR_HALF" for kind, _ in source.tokens):
        raise ValueError("CHR_HALF is out of scope")
    tokens = [(kind, allocation[value]) if kind == "CHR_FULL" else (kind, value)
              for kind, value in source.tokens]
    # Compression is not part of this leaf contract.  The game accepts the
    # exact same standard record uncompressed; retain all unrelated flag bits.
    flags &= ~0x0002
    return flags.to_bytes(2, "little") + encode_standard(tokens)


def leaf_lines(leaf):
    return [leaf[index] for index in range(leaf.tsize)]


def serialize_leaf(us_raw: bytes, leaf, replacements: dict[int, bytes]) -> bytes:
    lines = leaf_lines(leaf)
    if len(lines) != leaf.tsize:
        raise AssertionError("line count instability")
    out = bytearray(len(lines) * 2)
    out.extend(b"\xff\xff")
    offsets = []
    for index, line in enumerate(lines):
        while len(out) & 1: out.append(0)
        offsets.append(len(out))
        out.extend(replacements.get(index, original_line_bytes(us_raw, line)))
    for index, offset in enumerate(offsets):
        if not 0 <= offset <= 0xFFFF: raise ValueError("leaf offset overflow")
        out[index * 2:index * 2 + 2] = offset.to_bytes(2, "little")
    while len(out) % 4: out.append(0)
    return bytes(out)


def root_bundle(us_raw: bytes, root, top: int, repl_by_leaf):
    """Return root-page bundle bytes and per-real-leaf serialization metadata."""
    item = root[top]
    if isinstance(item, c_ffta_sect_text_page):
        return serialize_leaf(us_raw, item, repl_by_leaf[(top,)]), [(top, item)]
    if not isinstance(item, c_ffta_sect_text_sub): raise TypeError(type(item).__name__)
    # The intermediate table's offsets are relative to the s_text root.  Its
    # own 26 leaf pages are placed after its fixed 4-byte pointer table.
    leaves = [(top, sub, item[sub]) for sub in range(item.tsize)]
    out = bytearray(item.tsize * 4)
    records = []
    for sub, leaf in [(sub, leaf) for _, sub, leaf in leaves]:
        while len(out) % 4: out.append(0)
        local = len(out)
        data = serialize_leaf(us_raw, leaf, repl_by_leaf[(top, sub)])
        records.append((sub, leaf, local, data))
        out.extend(data)
    # Caller fixes absolute-root-relative offsets after tail placement.
    return bytes(out), [(top, sub, leaf, local, data) for sub, leaf, local, data in records]


def resolve_root_page(root, path):
    if not path: raise ValueError("empty path")
    return int(path[0])


def collect(jp, us):
    pairs, _ = bulk.auto_pairs(jp, us)
    selected, excluded = [], {"reference": 0, "chr_half": 0, "unsafe": 0}
    glyphs = set()
    for pair in pairs:
        if pair["section"] != "s_text": continue
        if isinstance(pair["jp_line"], list) or isinstance(pair["us_line"], list):
            excluded["reference"] += 1; continue
        jp_line, us_line = pair["jp_line"], pair["us_line"]
        toks = text_tokens(jp_line)
        if any(kind == "CHR_HALF" for kind, _ in toks):
            excluded["chr_half"] += 1; continue
        if not isinstance(jp_line.text, (c_ffta_sect_text_buf, c_ffta_sect_text_buf_ya)):
            excluded["unsafe"] += 1; continue
        if isinstance(jp_line.text, c_ffta_sect_text_buf_ya) and any(k.startswith("CHR_") for k, _ in toks):
            excluded["unsafe"] += 1; continue
        glyphs.update(v for k, v in toks if k == "CHR_FULL")
        selected.append(pair)
    if len(selected) != EXPECTED_ENTRIES:
        raise RuntimeError(f"safe count {len(selected)} != {EXPECTED_ENTRIES}; {excluded}")
    return selected, excluded, sorted(glyphs)


def make_build():
    jp, us = load_rom_jp(JP_ROM), load_rom_us(US_ROM)
    us_raw, jp_raw = US_ROM.read_bytes(), JP_ROM.read_bytes()
    selected, excluded, glyphs = collect(jp, us)
    font = us.tabs["font"]
    allocation = {glyph: ALLOC_START + n for n, glyph in enumerate(glyphs)}
    if not glyphs or max(allocation.values()) >= font.tsize: raise RuntimeError("JP FULL glyph capacity failed")
    root = us.tabs["s_text"]
    repl = defaultdict(dict)
    for pair in selected:
        path = tuple(pair["us_path"])
        top = resolve_root_page(root, path)
        leaf_key = (top,) if len(path) == 2 else (top, path[1])
        line_index = path[-1]
        if line_index in repl[leaf_key]: raise RuntimeError(f"duplicate replacement {leaf_key}/{line_index}")
        repl[leaf_key][line_index] = replacement_line(pair["jp_line"], allocation)
    if set(top for top, *_ in repl) != EXPECTED_TOPS or len(EXPECTED_TOPS) != EXPECTED_ROOT_PAGES:
        raise RuntimeError("unexpected root page scope")

    raw = bytearray(us_raw)
    # Native JP 0x80-byte glyph records plus the companion byte proved by the
    # existing FULL-slot controls.  Both are deterministic high-slot copies.
    for jp_index, us_index in allocation.items():
        joff = jp.tabs["font"].real_offset + jp_index * FONT_STRIDE
        uoff = font.real_offset + us_index * FONT_STRIDE
        raw[uoff:uoff + FONT_STRIDE] = jp_raw[joff:joff + FONT_STRIDE]
        raw[US_METADATA + us_index] = jp_raw[JP_METADATA + jp_index]

    cursor = TAIL_START
    pages, leaf_checks = [], []
    for top in sorted(EXPECTED_TOPS):
        cursor = align(cursor, 4)
        item = root[top]
        original_ptr_field = root.real_offset + top * 4
        old_relative = int.from_bytes(us_raw[original_ptr_field:original_ptr_field + 4], "little")
        if isinstance(item, c_ffta_sect_text_page):
            blob, leaves = root_bundle(us_raw, root, top, repl)
            raw[cursor:cursor + len(blob)] = blob
            new_relative = cursor - root.real_offset
            raw[original_ptr_field:original_ptr_field + 4] = new_relative.to_bytes(4, "little")
            pages.append({"page": str(top), "original_leaf_rom": item.real_offset, "original_size": item.sect_top,
                          "new_leaf_rom": cursor, "new_size": len(blob), "parent_pointer_field": original_ptr_field,
                          "old_relative": old_relative, "new_relative": new_relative,
                          "jp_replaced_lines": len(repl[(top,)])})
            leaf_checks.append((str(top), cursor, blob, item, repl[(top,)]))
            cursor += len(blob)
        else:
            blob, leaves = root_bundle(us_raw, root, top, repl)
            mutable = bytearray(blob)
            for _, sub, leaf, local, leaf_blob in leaves:
                mutable[sub * 4:sub * 4 + 4] = (cursor + local - root.real_offset).to_bytes(4, "little")
                leaf_checks.append((f"{top}/{sub}", cursor + local, leaf_blob, leaf, repl[(top, sub)]))
            blob = bytes(mutable)
            raw[cursor:cursor + len(blob)] = blob
            new_relative = cursor - root.real_offset
            raw[original_ptr_field:original_ptr_field + 4] = new_relative.to_bytes(4, "little")
            pages.append({"page": str(top), "original_leaf_rom": item.real_offset, "original_size": item.sect_top,
                          "new_leaf_rom": cursor, "new_size": len(blob), "parent_pointer_field": original_ptr_field,
                          "old_relative": old_relative, "new_relative": new_relative,
                          "jp_replaced_lines": sum(len(repl[(top, s)]) for s in range(item.tsize))})
            cursor += len(blob)
    if cursor - TAIL_START > TAIL_CAPACITY: raise RuntimeError("tail capacity exceeded")
    expected = defaultdict(dict)
    for pair in selected:
        path = tuple(pair["us_path"]); top = path[0]
        key = (top,) if len(path) == 2 else (top, path[1])
        expected[key][path[-1]] = [(kind, allocation[value]) if kind == "CHR_FULL" else (kind, value)
                                  for kind, value in text_tokens(pair["jp_line"])]
    return bytes(raw), {"jp": jp, "us": us, "selected": selected, "excluded": excluded, "glyphs": glyphs,
                        "allocation": allocation, "pages": pages, "leaf_checks": leaf_checks, "expected": expected, "tail_end": cursor}


def validate_leaf(blob, us_raw, leaf, replacements, expected):
    n = leaf.tsize; table_end = n * 2
    if len(blob) < table_end + 2 or blob[table_end:table_end + 2] != b"\xff\xff": raise RuntimeError("missing FF FF")
    offsets = [int.from_bytes(blob[i * 2:i * 2 + 2], "little") for i in range(n)]
    if any(not table_end + 2 <= x < len(blob) for x in offsets) or offsets != sorted(offsets): raise RuntimeError("invalid offsets")
    for i, start in enumerate(offsets):
        stop = offsets[i + 1] if i + 1 < n else len(blob)
        # Each copied/rebuilt line starts with flags and has an EOS within its
        # own bounded segment.  Standard EOS=00; YA EOS=FF.
        flags = int.from_bytes(blob[start:start + 2], "little")
        eos = 0xFF if flags & 1 else 0
        if eos not in blob[start + 2:stop]: raise RuntimeError(f"line {i} has no bounded EOS")
        probe = c_ffta_sect_text_line(bytearray(blob), start)
        probe.parse_size(stop - start, 2); probe.set_nondeterm(); probe.parse()
        if i in expected:
            if probe.text.tokens != expected[i]: raise RuntimeError(f"localized tokens differ at line {i}")
        else:
            original = leaf[i]
            if bytes(blob[start:start + original.raw_len]) != original_line_bytes(us_raw, original):
                raise RuntimeError(f"preserved raw line differs at line {i}")
            if probe.text.tokens != original.text.tokens: raise RuntimeError(f"preserved tokens differ at line {i}")
    return {"line_count": n, "replaced": len(replacements), "result": "PASS"}


def changed_ranges(before: bytes, after: bytes):
    result=[]; start=None
    for i,(a,b) in enumerate(zip(before,after)):
        if a!=b and start is None:start=i
        if a==b and start is not None: result.append((start,i));start=None
    if start is not None:result.append((start,len(before)))
    return result


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--overwrite", action="store_true"); args=ap.parse_args()
    if sha(JP_ROM) != coverage.JP_SHA or sha(US_ROM) != coverage.US_SHA: raise RuntimeError("original ROM SHA-256 mismatch")
    outputs=(OUT_ROM, OUT_REPEAT, OUT / "summary.json")
    if not args.overwrite and any(x.exists() for x in outputs): raise RuntimeError("refusing to overwrite build artifacts")
    OUT.mkdir(parents=True, exist_ok=True); OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    first, meta = make_build(); second, repeat = make_build()
    if sha(first) != sha(second): raise RuntimeError("S_TEXT_LEAF_BUILD_NONDETERMINISTIC")
    if meta["pages"] != repeat["pages"] or meta["allocation"] != repeat["allocation"]: raise RuntimeError("deterministic layout mismatch")
    before=US_ROM.read_bytes()
    for name, address, blob, leaf, replacements in meta["leaf_checks"]:
        key = tuple(map(int, name.split("/")))
        validate_leaf(blob, before, leaf, replacements, meta["expected"][key])
    if len(meta["pages"]) != EXPECTED_ROOT_PAGES: raise RuntimeError("pointer count mismatch")
    root = meta["us"].tabs["s_text"]
    for page in meta["pages"]:
        field=page["parent_pointer_field"]; val=int.from_bytes(first[field:field+4],"little")
        if root.real_offset + val != page["new_leaf_rom"]: raise RuntimeError("S_TEXT_LEAF_POINTER_AUDIT_FAILED")
    ranges=changed_ranges(before,first)
    # The only original-region changes are the 37 root u32 pointer fields and
    # native glyph/metadata slots.  Everything at/after TAIL_START is a leaf bundle.
    allowed = [(p["parent_pointer_field"], p["parent_pointer_field"] + 4) for p in meta["pages"]]
    for us_index in meta["allocation"].values():
        base = meta["us"].tabs["font"].real_offset + us_index * FONT_STRIDE
        allowed.extend(((base, base + FONT_STRIDE), (US_METADATA + us_index, US_METADATA + us_index + 1)))
    allowed.append((TAIL_START, meta["tail_end"]))
    allowed.sort()
    merged_allowed = []
    for lo, hi in allowed:
        if merged_allowed and lo <= merged_allowed[-1][1]:
            merged_allowed[-1] = (merged_allowed[-1][0], max(merged_allowed[-1][1], hi))
        else:
            merged_allowed.append((lo, hi))
    for start, end in ranges:
        if not any(lo <= start and end <= hi for lo, hi in merged_allowed):
            raise RuntimeError(f"binary touch escaped allowed categories: {start:08X}-{end:08X}")
    OUT_ROM.write_bytes(first); OUT_REPEAT.write_bytes(second)
    rows=[]
    for name,address,blob,leaf,replacements in meta["leaf_checks"]:
        key = tuple(map(int, name.split("/")))
        rows.append({"leaf":name,"new_rom_address":f"0x{address:08X}","new_size":len(blob),**validate_leaf(blob,before,leaf,replacements,meta["expected"][key])})
    write_csv(OUT / "leaf_validation.csv", rows); write_csv(OUT / "page_allocation.csv", meta["pages"])
    range_rows=[]
    pointer_fields={p["parent_pointer_field"] for p in meta["pages"]}
    first_slot, last_slot = min(meta["allocation"].values()), max(meta["allocation"].values())
    font_spans=[(meta["us"].tabs["font"].real_offset + first_slot * FONT_STRIDE,
                 meta["us"].tabs["font"].real_offset + (last_slot + 1) * FONT_STRIDE)]
    metadata_spans=[(US_METADATA + first_slot, US_METADATA + last_slot + 1)]
    for start,end in ranges:
        if start >= TAIL_START: category="relocated_leaf_bundle"
        elif any(start >= p and end <= p + 4 for p in pointer_fields): category="root_parent_pointer"
        elif any(lo <= start and end <= hi for lo,hi in font_spans): category="jp_full_glyph"
        elif any(lo <= start and end <= hi for lo,hi in metadata_spans): category="jp_full_metadata"
        else: raise RuntimeError("unclassified allowed change")
        range_rows.append({"start":f"0x{start:08X}","end":f"0x{end:08X}","category":category})
    write_csv(OUT / "binary_touch_ranges.csv", range_rows)
    summary={"status":"STATIC_VALIDATED","coverage":{"expected_safe_s_text":EXPECTED_ENTRIES,"replaced":len(meta["selected"]),"excluded":meta["excluded"],"affected_root_pages":len(meta["pages"]),"physical_leaf_pages":len(rows)},
             "leaves":{"total_relocated_bytes":meta["tail_end"]-TAIL_START,"allocation_start":f"0x{TAIL_START:08X}","allocation_end":f"0x{meta['tail_end']:08X}","tail_bytes_remaining":TAIL_CAPACITY-(meta["tail_end"]-TAIL_START),"validation":"PASS"},
             "pointers":{"patched":len(meta["pages"]),"audit":"PASS"},"font":{"unique_jp_full_glyphs":len(meta["glyphs"]),"slot_first":f"0x{min(meta['allocation'].values()):04X}","slot_last":f"0x{max(meta['allocation'].values()):04X}","remaining_capacity":meta["us"].tabs["font"].tsize-1-max(meta["allocation"].values())},
             "determinism":{"sha256_1":sha(first),"sha256_2":sha(second),"identical":True},"binary_touch":{"merged_range_count":len(range_rows),"range_report":str(OUT / "binary_touch_ranges.csv"),"categories":{"root_parent_pointers":len(meta["pages"]),"relocated_leaf_bundle":[f"0x{TAIL_START:08X}",f"0x{meta['tail_end']:08X}"],"jp_full_glyph_slots":[f"0x{min(meta['allocation'].values()):04X}",f"0x{max(meta['allocation'].values()):04X}"],"jp_full_metadata_slots":[f"0x{min(meta['allocation'].values()):04X}",f"0x{max(meta['allocation'].values()):04X}"]},"structural_unrelated_bytes_changed":0},"rom":{"jp_sha256":sha(JP_ROM),"us_sha256":sha(US_ROM)}}
    (OUT / "summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__ == "__main__": main()
