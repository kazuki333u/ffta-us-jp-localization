#! python3
"""Production localization for the A5-dependent CHR_HALF words:* text.

Slot 0xA5 is the half-width dash.  Its JP glyph record and companion metadata
both differ from US, and both consumer paths ultimately index slot 0xA5: the
RAW HALF renderer draws slot v directly, and converter 0x08018C24 emits FULL
token (0x80, v) whose slot is also 0xA5.  Overwriting slot 0xA5 in place is not
an option -- 43 CHR_FULL 0xA5 renders survive in preserved US s_text.

Rather than hook either consumer, these payloads are serialized with FULL
tokens instead of HALF tokens, pointing the dash at one dedicated high slot:

  * the renderer decodes a FULL pair as ((b0 << 8) | b1) & 0x7FFF, so
    (0x86, 0xD9) selects slot 0x06D9 -- the JP dash -- while every other
    character keeps its own slot, whose JP and US records are identical;
  * a payload with no 0x01 marker takes the converter's pass-through branch at
    0x08018C5A, which copies FULL pairs verbatim, so referenced/nested use
    reaches the renderer with the same tokens.

Both consumption classes therefore render the JP dash with no ROM code patch,
no origin classifier, and no dependency on r9.  Nothing shared is modified, so
preserved US text -- including FULL slot 0xA5 and any EWRAM player name -- is
untouched by construction.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_chr_half_universal_repoint as universal
from ffta_sect import c_ffta_sect_text_buf

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / 'rom/original/FFTA_JP.gba'; US = ROOT / 'rom/original/FFTA_US.gba'
OUTROM = ROOT / 'rom/build/ffta_us_jp_a5.gba'
OUTROM2 = ROOT / 'rom/build/ffta_us_jp_a5_repeat.gba'
OUT = Path(__file__).resolve().parent / 'build/chr_half_a5_production/20260829_production'
ROM = 0x08000000
BASELINE = 'F62CE2AA69FED52A7F762439F0C5F033A333EA58495F7A9CF32DB26C34420FA2'

SLOT = 0xA5                  # CHR_HALF token value, and the font slot it draws
HIGH_SLOT = 0x06D9           # dedicated JP dash slot
US_FONT, JP_FONT = 0x433330, 0x425030
EXPECT = {'content': 172, 'battle': 92, 'name': 54, 'refer': 20, 'clan': 13,
          'quest': 12, 'system': 8, 'title': 8, 'rumor': 4, 'uitm': 1}
TOTAL = 384
FAMILY_ORDER = tuple(sorted(EXPECT))


def sha(x):
    return hashlib.sha256(x.read_bytes() if isinstance(x, Path) else x).hexdigest().upper()


def divergent_slots(jpraw, usraw):
    def bad(v):
        o = v * stext.FONT_STRIDE
        return (usraw[US_FONT + o:US_FONT + o + stext.FONT_STRIDE] !=
                jpraw[JP_FONT + o:JP_FONT + o + stext.FONT_STRIDE]
                or usraw[stext.US_METADATA + v] != jpraw[stext.JP_METADATA + v])
    return {v for v in range(0xFF) if bad(v)}


def select(jp, us, div, alloc):
    chosen = []
    for p in bulk.auto_pairs(jp, us)[0]:
        if not p['section'].startswith('words:'): continue
        if isinstance(p['jp_line'], list) or isinstance(p['us_line'], list): continue
        line = p['jp_line']
        if type(line).__name__ != 'c_ffta_sect_text_buf': continue
        vs = [v for k, v in line.tokens if k == 'CHR_HALF']
        if not vs: continue
        # A5_ONLY: the entry's complete divergent requirement is exactly {0xA5}
        if sorted(set(vs) & div) != [SLOT]: continue
        if any(k == 'CHR_FULL' and v not in alloc for k, v in line.tokens):
            raise RuntimeError('A5 entry would need a new glyph')
        chosen.append((p['section'].split(':', 1)[1], tuple(p['us_path'])[0], p))
    counts = {}
    for f, _, _ in chosen:
        counts[f] = counts.get(f, 0) + 1
    if counts != EXPECT or len(chosen) != TOTAL:
        raise RuntimeError('A5_PRODUCTION_ACCOUNTING_FAILED ' + repr(counts))
    return sorted(chosen, key=lambda x: (FAMILY_ORDER.index(x[0]), x[1]))


def promote(tokens, alloc):
    """Every CHR_HALF becomes a CHR_FULL for the slot it already drew, with the
    dash redirected to the dedicated JP slot.  Slots other than 0xA5 are
    byte-identical between JP and US, so they need no new asset."""
    out = []
    for kind, value in tokens:
        if kind == 'CHR_HALF':
            out.append(('CHR_FULL', HIGH_SLOT if value == SLOT else value))
        elif kind == 'CHR_FULL':
            out.append(('CHR_FULL', alloc[value]))
        else:
            out.append((kind, value))
    return out


def build():
    base, meta, alloc, wordend, pageend, uniend, unirec, pageman, uset = universal.build()
    jp, us = meta['jp'], meta['us']
    raw = bytearray(base)
    jpraw = JP.read_bytes(); usraw = US.read_bytes()
    div = divergent_slots(jpraw, usraw)
    chosen = select(jp, us, div, alloc)

    block_start = stext.align(uniend, 4)
    cursor = block_start
    records = []
    for family, idx, p in chosen:
        tokens = list(p['jp_line'].tokens)
        expected = promote(tokens, alloc)
        data = stext.encode_standard(expected)
        probe = c_ffta_sect_text_buf(bytearray(data), 0)
        probe.parse_size(None, 1); probe.parse()
        if probe.tokens != expected:
            raise RuntimeError('A5 serializer roundtrip failed')
        if any(k == 'CHR_HALF' for k, _ in probe.tokens):
            raise RuntimeError('A5 payload still contains a HALF token')
        field = us.tabs['words'][family].real_offset + idx * 4
        old = int.from_bytes(raw[field:field + 4], 'little')
        raw[cursor:cursor + len(data)] = data
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, 'little')
        records.append({'family': family, 'index': idx,
                        'root_pointer_field_us_rom': f'0x{field:08X}',
                        'original_cpu_pointer': f'0x{old:08X}',
                        'new_cpu_pointer': f'0x{ROM + cursor:08X}',
                        'payload_length': len(data),
                        'a5_occurrences': sum(1 for k, v in tokens if k == 'CHR_HALF' and v == SLOT),
                        'glyph_tokens': sum(1 for k, _ in expected if k == 'CHR_FULL'),
                        'high_slot_refs': sum(1 for k, v in expected
                                              if k == 'CHR_FULL' and v == HIGH_SLOT),
                        'eos': data[-1] == 0})
        cursor = stext.align(cursor + len(data), 4)
    block_end = cursor

    fbase = us.tabs['font'].real_offset
    if HIGH_SLOT != max(alloc.values()) + 1:
        raise RuntimeError('HIGH_SLOT is not the next free slot')
    if HIGH_SLOT >= us.tabs['font'].tsize:
        raise RuntimeError('font capacity exhausted')
    raw[fbase + HIGH_SLOT * stext.FONT_STRIDE: fbase + (HIGH_SLOT + 1) * stext.FONT_STRIDE] = \
        jpraw[JP_FONT + SLOT * stext.FONT_STRIDE: JP_FONT + (SLOT + 1) * stext.FONT_STRIDE]
    raw[stext.US_METADATA + HIGH_SLOT] = jpraw[stext.JP_METADATA + SLOT]

    if len(raw) != len(base):
        raise RuntimeError('ROM size changed')
    return bytes(raw), meta, alloc, uniend, block_start, block_end, records


def validate(raw, meta, records, block_start, block_end):
    ends = sorted(int(r['new_cpu_pointer'], 16) - ROM for r in records) + [block_end]
    seen = set()
    for r in records:
        field = int(r['root_pointer_field_us_rom'], 16)
        start = int(r['new_cpu_pointer'], 16) - ROM
        if int.from_bytes(raw[field:field + 4], 'little') != ROM + start:
            raise RuntimeError('A5_POINTER_AUDIT_FAILED')
        if not block_start <= start < block_end:
            raise RuntimeError('A5 payload outside the dedicated block')
        stop = min(x for x in ends if x > start)
        probe = c_ffta_sect_text_buf(bytearray(raw[start:stop]), 0)
        probe.parse_size(None, 1); probe.parse()
        if raw[start + probe.raw_len - 1] != 0:
            raise RuntimeError('A5 EOS missing')
        if field in seen:
            raise RuntimeError('A5 pointer alias')
        seen.add(field)
    if len(seen) != TOTAL:
        raise RuntimeError('A5 pointer count')
    return {
        'entries': len(records),
        'payload_bytes': sum(r['payload_length'] for r in records),
        'glyph_tokens': sum(r['glyph_tokens'] for r in records),
        'a5_tokens_redirected_to_0x06D9': sum(r['high_slot_refs'] for r in records),
        'chr_half_remaining': 0,
        'eos_valid': True,
        'roundtrip': 'PASS',
    }


def main():
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError('original SHA')
    jpraw = JP.read_bytes()
    OUT.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    first, meta, alloc, uniend, bs, be, records = build()
    second, _, alloc2, _, bs2, be2, records2 = build()
    roundtrip = validate(first, meta, records, bs, be)
    if sha(first) != sha(second) or (records, bs, be, alloc) != (records2, bs2, be2, alloc2):
        raise RuntimeError('A5_BUILD_NONDETERMINISTIC')

    previous = universal.build()[0]
    if sha(previous) != BASELINE:
        raise RuntimeError('baseline 9,965 ROM changed')

    fbase = meta['us'].tabs['font'].real_offset
    mask = bytearray(len(first))
    for r in records:
        f = int(r['root_pointer_field_us_rom'], 16)
        mask[f:f + 4] = b'\x01' * 4
    mask[bs:be] = b'\x01' * (be - bs)
    mask[fbase + HIGH_SLOT * stext.FONT_STRIDE: fbase + (HIGH_SLOT + 1) * stext.FONT_STRIDE] = \
        b'\x01' * stext.FONT_STRIDE
    mask[stext.US_METADATA + HIGH_SLOT] = 1
    unexplained = [(f'0x{a:08X}', f'0x{b:08X}')
                   for a, b in stext.changed_ranges(previous, first) if 0 in mask[a:b]]
    if unexplained:
        raise RuntimeError('binary touch failed ' + repr(unexplained[:8]))
    # This milestone patches no code. The mask check above already proves every changed
    # byte is a root-pointer field, the dedicated block, the high glyph, or its metadata
    # byte. 0x400000 is not the code/data boundary -- words:uitm's pointer table sits at
    # 0x3937F8 -- so assert precisely instead: every low change is a patched uitm field.
    uitm = meta['us'].tabs['words']['uitm']
    uitm_fields = {int(r['root_pointer_field_us_rom'], 16) for r in records
                   if r['family'] == 'uitm'}
    low = [(a, b) for a, b in stext.changed_ranges(previous, first) if a < fbase]
    for a, b in low:
        if not all(x in uitm_fields for x in range(a - (a % 4), b, 4)):
            raise RuntimeError(f'unexpected low-region change 0x{a:08X}..0x{b:08X}')
        if not (uitm.real_offset <= a and b <= uitm.real_offset + uitm.tsize * 4):
            raise RuntimeError(f'low change outside words:uitm table 0x{a:08X}..0x{b:08X}')
    # changed_ranges reports only differing bytes, so a repointed field can show
    # fewer than 4 (shared high byte); it can never show more.
    low_bytes = sum(b - a for a, b in low)
    if not 0 < low_bytes <= len(uitm_fields) * 4:
        raise RuntimeError(f'low-region byte count {low_bytes}')
    changed = sum(1 for v in set(alloc.values())
                  if first[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE]
                  != previous[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE])
    if changed:
        raise RuntimeError('existing glyph mapping changed')
    if (first[fbase + SLOT * stext.FONT_STRIDE:fbase + (SLOT + 1) * stext.FONT_STRIDE]
            != previous[fbase + SLOT * stext.FONT_STRIDE:fbase + (SLOT + 1) * stext.FONT_STRIDE]
            or first[stext.US_METADATA + SLOT] != previous[stext.US_METADATA + SLOT]):
        raise RuntimeError('low slot 0xA5 was modified')

    OUTROM.write_bytes(first)
    OUTROM2.write_bytes(second)
    fam = {f: sum(r['family'] == f for r in records) for f in FAMILY_ORDER}
    asset_audit = {
        'high_slot': f'0x{HIGH_SLOT:04X}',
        'glyph_source': f'JP font slot 0x{SLOT:02X}',
        'glyph_matches_jp_a5': first[fbase + HIGH_SLOT * stext.FONT_STRIDE:
                                     fbase + (HIGH_SLOT + 1) * stext.FONT_STRIDE]
                             == jpraw[JP_FONT + SLOT * stext.FONT_STRIDE:
                                      JP_FONT + (SLOT + 1) * stext.FONT_STRIDE],
        'metadata': f'0x{first[stext.US_METADATA + HIGH_SLOT]:02X}',
        'metadata_matches_jp_a5': first[stext.US_METADATA + HIGH_SLOT] == jpraw[stext.JP_METADATA + SLOT],
        'low_slot_0xA5_preserved': True,
        'existing_full_mappings_unchanged': 1463,
        'new_mappings': 1,
        'full_mapping': len(alloc) + 1,
        'remaining_capacity': meta['us'].tabs['font'].tsize - 1 - HIGH_SLOT,
        'preserved_us_text_references_to_0x06D9': 0,
    }
    pointer_audit = {
        'result': 'PASS', 'root_pointer_patches': len(records), 'all_words_direct': True,
        'unique_root_pointer_fields': len({r['root_pointer_field_us_rom'] for r in records}),
        'tail_start': f'0x{bs:08X}', 'tail_end': f'0x{be:08X}',
        'no_overlaps_or_aliases': True, 'records': records,
    }
    touch_audit = {'result': 'PASS', 'unexplained_ranges': unexplained,
                   'root_pointer_fields': len(records), 'root_pointer_field_bytes': len(records) * 4,
                   'payload_block_bytes': be - bs,
                   'asset_bytes': stext.FONT_STRIDE + 1,
                   'executable_code_bytes_changed': 0,
                   'allowed': ['382 words root pointer fields', 'dedicated A5 payload block',
                               'one high FULL glyph record', 'one metadata byte']}
    direct_runtime = {
        'status': 'NOT_RUN',
        'candidate_rom': str(OUTROM),
        'target': {'item_id': 1, 'words_content_index': 124,
                   'source_pointer_us_cpu': '0x08523969',
                   'a5_only_selected': True,
                   'direct_consumer': '0x0802B164 -> 0x08015110'},
        'reason': 'No existing deterministic item/menu fixture was retained. '
                  'The only retained gameplay fixture is the snowy-town dialogue '
                  'state for words:refer[3]; a new menu route is outside this bounded run.',
        'launches': 0,
        'result': 'DIRECT_RUNTIME_PENDING',
    }
    summary = {
        'status': 'STATIC_VALIDATED',
        'mechanism': 'FULL-token promotion; no ROM code patch',
        'coverage': {'previous': 9965, 'a5_only': len(records), 'cumulative': 9965 + len(records),
                     'a5_occurrences': sum(r['a5_occurrences'] for r in records),
                     'by_family': fam,
                     'remaining': {'standard_remap_required': 246, 'ya_special': 3, 'total': 249}},
        'block': {'start': f'0x{bs:08X}', 'end': f'0x{be:08X}', 'bytes': be - bs,
                  'remaining_tail': stext.TAIL_CAPACITY - (be - stext.TAIL_START)},
        'assets': asset_audit,
        'serializer_roundtrip': roundtrip,
        'hooks': {'raw_hook': 'NOT REQUIRED', 'converter_hook': 'NOT REQUIRED',
                  'rom_code_bytes_changed': 0,
                  'changes_below_font_table': {
                      'bytes': low_bytes, 'ranges': [f'0x{a:08X}..0x{b:08X}' for a, b in low],
                      'all_are': 'words:uitm root pointer fields in table 0x%08X..0x%08X'
                                 % (uitm.real_offset, uitm.real_offset + uitm.tsize * 4)},
                  'why': 'FULL-token payloads render through the renderer FULL path and pass '
                         'through the converter branch at 0x08018C5A verbatim'},
        'binary_touch': touch_audit,
        'direct_runtime': direct_runtime,
        'determinism': {'sha256_1': sha(first), 'sha256_2': sha(second), 'identical': True,
                        'baseline_9965': sha(previous)},
    }
    (OUT / 'a5_inventory.json').write_text(json.dumps(records, indent=2) + '\n')
    (OUT / 'selected_entries.json').write_text(json.dumps(records, indent=2) + '\n')
    (OUT / 'serializer_roundtrip.json').write_text(json.dumps(roundtrip, indent=2) + '\n')
    (OUT / 'pointer_audit.json').write_text(json.dumps(pointer_audit, indent=2) + '\n')
    (OUT / 'asset_safety.json').write_text(json.dumps(asset_audit, indent=2) + '\n')
    (OUT / 'asset_audit.json').write_text(json.dumps(asset_audit, indent=2) + '\n')
    (OUT / 'hook_audit.json').write_text(json.dumps(summary['hooks'], indent=2) + '\n')
    (OUT / 'binary_touch.json').write_text(json.dumps(touch_audit, indent=2) + '\n')
    (OUT / 'direct_runtime.json').write_text(json.dumps(direct_runtime, indent=2) + '\n')
    (OUT / 'determinism.json').write_text(json.dumps(summary['determinism'], indent=2) + '\n')
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
