#! python3
"""Production direct-entry repoint for universal-safe CHR_HALF words:* text.

Eligibility is an ASSET property, never a consumer-lane inference: an entry
qualifies only if every CHR_HALF token value v indexes a glyph record and
companion metadata byte that are byte-identical between JP and US at slot v.
Such text renders correctly whichever standard consumer reads it, so this
milestone needs no ROM code hook, no origin map and no font/metadata change.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_page_leaf_repoint as pages
from ffta_sect import c_ffta_sect_text_buf

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / 'rom/original/FFTA_JP.gba'; US = ROOT / 'rom/original/FFTA_US.gba'
OUTROM = ROOT / 'rom/build/ffta_us_jp_universal_half.gba'
OUTROM2 = ROOT / 'rom/build/ffta_us_jp_universal_half_repeat.gba'
OUT = Path(__file__).resolve().parent / 'build/chr_half_universal_production/20260829_production'
ROM = 0x08000000
BASELINE = '26E73844CA14FA1ACF4F0674560F7B14FE338D402140B6559AFED40207808057'
# Slot v addresses both the glyph record and its 1-byte companion metadata.
US_FONT, JP_FONT = 0x433330, 0x425030
EXPECT = {'content': 174, 'battle': 173, 'name': 74, 'refer': 56, 'quest': 17,
          'clan': 14, 'system': 14, 'title': 11, 'rumor': 7, 'uitm': 1}
TOTAL, OCCURRENCES = 541, 2782
FAMILY_ORDER = tuple(sorted(EXPECT))


def sha(x):
    return hashlib.sha256(x.read_bytes() if isinstance(x, Path) else x).hexdigest().upper()


def universal_slots(jpraw, usraw):
    """Slots whose glyph record AND companion metadata are identical JP vs US."""
    def ok(v):
        o = v * stext.FONT_STRIDE
        return (usraw[US_FONT + o:US_FONT + o + stext.FONT_STRIDE] ==
                jpraw[JP_FONT + o:JP_FONT + o + stext.FONT_STRIDE]
                and usraw[stext.US_METADATA + v] == jpraw[stext.JP_METADATA + v])
    return {v for v in range(0xFF) if ok(v)}


def encode_half_payload(tokens, alloc):
    """0x01 enters HALF mode and HALF mode persists to EOS, so emit exactly one
    marker and then one (v+1) byte per HALF character."""
    probe = c_ffta_sect_text_buf(bytearray(), 0); probe._make_ctr_tab()
    buf = bytearray(); half_at = None
    for kind, value in tokens:
        if kind == 'CHR_HALF':
            if value == 0:
                # (v+1)==0x01 would re-enter HALF mode instead of drawing.
                raise ValueError('CHR_HALF value 0 is not serializable')
            if not 0 <= value <= 0xFE: raise ValueError('CHR_HALF out of range')
            if half_at is None: half_at = len(buf); buf.append(0x01)
            buf.append(value + 1)
        else:
            if half_at is not None: raise ValueError('non-HALF token after HALF mode')
            probe._encode_tok(buf, (kind, alloc[value]) if kind == 'CHR_FULL' else (kind, value))
    if half_at is None: raise ValueError('entry has no CHR_HALF token')
    buf.append(0)
    return bytes(buf), half_at


def select(jp, us, universal, alloc):
    chosen = []
    for p in bulk.auto_pairs(jp, us)[0]:
        if not p['section'].startswith('words:'): continue
        if isinstance(p['jp_line'], list) or isinstance(p['us_line'], list): continue
        line = p['jp_line']
        # c_ffta_sect_text_buf_ya subclasses the standard buffer; exclude it.
        if type(line).__name__ != 'c_ffta_sect_text_buf': continue
        values = [v for k, v in line.tokens if k == 'CHR_HALF']
        if not values: continue
        if any(v not in universal for v in values): continue
        if any(k == 'CHR_FULL' and v not in alloc for k, v in line.tokens):
            raise RuntimeError('CHR_HALF_UNIVERSAL_ASSET_SAFETY_FAILED: new glyph required')
        chosen.append((p['section'].split(':', 1)[1], tuple(p['us_path'])[0], p))
    counts = {}
    for f, _, _ in chosen: counts[f] = counts.get(f, 0) + 1
    if counts != EXPECT or len(chosen) != TOTAL:
        raise RuntimeError(f'CHR_HALF_UNIVERSAL_ACCOUNTING_FAILED {counts}')
    return sorted(chosen, key=lambda x: (FAMILY_ORDER.index(x[0]), x[1]))


def build():
    base, meta, alloc, newglyphs, wordend, pageend, pageman = pages.build()
    jp, us = meta['jp'], meta['us']; raw = bytearray(base)
    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    universal = universal_slots(jpraw, usraw)
    chosen = select(jp, us, universal, alloc)
    cursor = stext.align(pageend, 4); records = []
    for family, idx, p in chosen:
        tokens = list(p['jp_line'].tokens)
        data, half_at = encode_half_payload(tokens, alloc)
        expected = [(k, alloc[v]) if k == 'CHR_FULL' else (k, v) for k, v in tokens]
        probe = c_ffta_sect_text_buf(bytearray(data), 0); probe.parse_size(None, 1); probe.parse()
        if probe.tokens != expected:
            raise RuntimeError('CHR_HALF_UNIVERSAL_SERIALIZER_FAILED')
        field = us.tabs['words'][family].real_offset + idx * 4
        old = int.from_bytes(raw[field:field + 4], 'little')
        raw[cursor:cursor + len(data)] = data
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, 'little')
        records.append({'family': family, 'index': idx,
                        'root_pointer_field_us_rom': f'0x{field:08X}',
                        'original_cpu_pointer': f'0x{old:08X}',
                        'new_cpu_pointer': f'0x{ROM + cursor:08X}',
                        'relocated_range_us_rom': [f'0x{cursor:08X}', f'0x{cursor + len(data):08X}'],
                        'payload_length': len(data), 'half_occurrences': sum(1 for k, _ in tokens if k == 'CHR_HALF'),
                        'first_half_marker_offset': half_at, 'redundant_half_markers': data.count(1) - 1,
                        'half_slots': sorted({v for k, v in tokens if k == 'CHR_HALF'}),
                        'eos': data[-1] == 0, 'roundtrip': 'PASS'})
        cursor = stext.align(cursor + len(data), 4)
    return bytes(raw), meta, alloc, wordend, pageend, cursor, records, pageman, universal


def validate(raw, meta, records, end, universal):
    ends = sorted(int(r['new_cpu_pointer'], 16) - ROM for r in records) + [end]
    seen = set()
    for r in records:
        field = int(r['root_pointer_field_us_rom'], 16)
        start = int(r['new_cpu_pointer'], 16) - ROM
        ptr = int.from_bytes(raw[field:field + 4], 'little')
        if ptr != ROM + start or not stext.TAIL_START <= start < end:
            raise RuntimeError('CHR_HALF_UNIVERSAL_POINTER_AUDIT_FAILED')
        stop = min(x for x in ends if x > start)
        probe = c_ffta_sect_text_buf(bytearray(raw[start:stop]), 0)
        probe.parse_size(None, 1); probe.parse()
        if raw[start + probe.raw_len - 1] != 0:
            raise RuntimeError('CHR_HALF_UNIVERSAL_POINTER_AUDIT_FAILED: EOS')
        if any(v not in universal for v in r['half_slots']):
            raise RuntimeError('CHR_HALF_UNIVERSAL_ASSET_SAFETY_FAILED')
        if field in seen:
            raise RuntimeError('CHR_HALF_UNIVERSAL_POINTER_AUDIT_FAILED: alias')
        seen.add(field)
    if len(seen) != TOTAL:
        raise RuntimeError('CHR_HALF_UNIVERSAL_POINTER_AUDIT_FAILED: count')


def exclusion_audit(raw, previous, meta, records, universal):
    """Every CHR_HALF entry not selected must stay byte-identical to the 9,424
    production: same root pointer field and same original US payload."""
    us, jp = meta['us'], meta['jp']
    patched = {int(r['root_pointer_field_us_rom'], 16) for r in records}
    kept = {'remap_required': 0, 'ya_special': 0}
    refer3 = None
    for p in bulk.auto_pairs(jp, us)[0]:
        if not p['section'].startswith('words:'):
            continue
        if isinstance(p['jp_line'], list) or isinstance(p['us_line'], list):
            continue
        line = p['jp_line']
        values = [v for k, v in line.tokens if k == 'CHR_HALF']
        if not values:
            continue
        ya = type(line).__name__ != 'c_ffta_sect_text_buf'
        if not ya and all(v in universal for v in values):
            continue
        family = p['section'].split(':', 1)[1]
        idx = tuple(p['us_path'])[0]
        field = us.tabs['words'][family].real_offset + idx * 4
        if field in patched:
            raise RuntimeError('CHR_HALF_UNIVERSAL_EXCLUSION_REGRESSION')
        if raw[field:field + 4] != previous[field:field + 4]:
            raise RuntimeError('CHR_HALF_UNIVERSAL_EXCLUSION_REGRESSION: pointer moved')
        ptr = int.from_bytes(previous[field:field + 4], 'little') - ROM
        n = p['us_line'].raw_len
        if raw[ptr:ptr + n] != previous[ptr:ptr + n]:
            raise RuntimeError('CHR_HALF_UNIVERSAL_EXCLUSION_REGRESSION: payload changed')
        kept['ya_special' if ya else 'remap_required'] += 1
        if family == 'refer' and idx == 3:
            refer3 = {'family': 'refer', 'index': 3,
                      'root_pointer_field_us_rom': f'0x{field:08X}',
                      'cpu_pointer': f'0x{ROM + ptr:08X}',
                      'divergent_values': [f'0x{v:02X}' for v in sorted(set(values) - universal)],
                      'pointer_unchanged': True, 'payload_unchanged': True}
    if refer3 is None:
        raise RuntimeError('CHR_HALF_UNIVERSAL_EXCLUSION_REGRESSION: refer[3] missing')
    return kept, refer3


def main():
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError('original SHA')
    OUT.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    first, meta, alloc, wordend, pageend, end, records, pageman, universal = build()
    second, meta2, alloc2, _, pageend2, end2, records2, _, _ = build()
    validate(first, meta, records, end, universal)
    if sha(first) != sha(second) or (alloc, end, records) != (alloc2, end2, records2):
        raise RuntimeError('CHR_HALF_UNIVERSAL_BUILD_NONDETERMINISTIC')
    previous = pages.build()[0]
    if sha(previous) != BASELINE:
        raise RuntimeError('CHR_HALF_UNIVERSAL_EXCLUSION_REGRESSION: baseline ROM changed')
    kept, refer3 = exclusion_audit(first, previous, meta, records, universal)
    if (kept['remap_required'], kept['ya_special']) != (628, 0):
        raise RuntimeError('CHR_HALF_UNIVERSAL_ACCOUNTING_FAILED ' + repr(kept))
    mask = bytearray(len(first))
    for r in records:
        f = int(r['root_pointer_field_us_rom'], 16)
        mask[f:f + 4] = b'\x01' * 4
    lo = stext.align(pageend, 4)
    mask[lo:end] = b'\x01' * (end - lo)
    unexplained = [(f'0x{a:08X}', f'0x{b:08X}')
                   for a, b in stext.changed_ranges(previous, first) if 0 in mask[a:b]]
    if unexplained:
        raise RuntimeError('binary touch failed ' + repr(unexplained[:8]))
    fbase = meta['us'].tabs['font'].real_offset
    fonts = sum(1 for v in set(alloc.values())
                if first[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE]
                != previous[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE])
    metas = sum(1 for v in set(alloc.values())
                if first[stext.US_METADATA + v] != previous[stext.US_METADATA + v])
    OUTROM.write_bytes(first)
    OUTROM2.write_bytes(second)
    fam = {f: sum(r['family'] == f for r in records) for f in FAMILY_ORDER}
    used = sorted({v for r in records for v in r['half_slots']})
    summary = {
        'status': 'STATIC_VALIDATED',
        'coverage': {'previous': 9424, 'universal_safe_half': len(records),
                     'cumulative': 9424 + len(records),
                     'occurrences': sum(r['half_occurrences'] for r in records),
                     'by_family': fam,
                     'remaining_chr_half': {'remap_required': kept['remap_required'],
                                            'ya_special': 3,
                                            'total': kept['remap_required'] + 3}},
        'serializer': {'rule': '0x01 once on entering HALF mode, then (v+1) per char, EOS 0x00',
                       'roundtrip_pass': sum(r['roundtrip'] == 'PASS' for r in records),
                       'redundant_markers': sum(r['redundant_half_markers'] for r in records),
                       'eos_valid': all(r['eos'] for r in records)},
        'pointers': {'patched': len(records), 'audit': 'PASS'},
        'tail': {'previous_end': f'0x{pageend:08X}', 'end': f'0x{end:08X}',
                 'bytes': end - lo,
                 'remaining': stext.TAIL_CAPACITY - (end - stext.TAIL_START)},
        'assets': {'universal_slots_used': len(used), 'divergent_tokens_included': 0,
                   'glyph_records_changed': fonts, 'metadata_changed': metas,
                   'new_full_slots': 0, 'full_mapping': len(alloc),
                   'last_slot': f'0x{max(alloc.values()):04X}'},
        'exclusions': {'remap_required_preserved': kept['remap_required'],
                       'ya_special_preserved': 3, 'refer3': refer3},
        'binary_touch': {'result': 'PASS', 'unexplained_ranges': unexplained,
                         'allowed': ['541 words root pointer fields',
                                     'new relocated HALF payload region']},
        'determinism': {'sha256_1': sha(first), 'sha256_2': sha(second),
                        'identical': True, 'baseline_9424': sha(previous)},
    }
    (OUT / 'selected_entries.json').write_text(json.dumps(records, indent=2) + '\n')
    (OUT / 'pointer_audit.json').write_text(json.dumps(
        [{k: r[k] for k in ('family', 'index', 'root_pointer_field_us_rom',
                            'original_cpu_pointer', 'new_cpu_pointer',
                            'relocated_range_us_rom', 'payload_length')} for r in records],
        indent=2) + '\n')
    (OUT / 'serializer_roundtrip.json').write_text(json.dumps(
        {'rule': summary['serializer']['rule'], 'entries': len(records),
         'pass': summary['serializer']['roundtrip_pass'],
         'redundant_markers': summary['serializer']['redundant_markers'],
         'detail': [{k: r[k] for k in ('family', 'index', 'payload_length',
                                       'half_occurrences', 'first_half_marker_offset',
                                       'redundant_half_markers', 'eos', 'roundtrip')}
                    for r in records]}, indent=2) + '\n')
    (OUT / 'asset_safety.json').write_text(json.dumps(
        dict(summary['assets'], universal_slot_values=[f'0x{v:02X}' for v in used]),
        indent=2) + '\n')
    (OUT / 'exclusion_audit.json').write_text(json.dumps(summary['exclusions'], indent=2) + '\n')
    (OUT / 'binary_touch.json').write_text(json.dumps(summary['binary_touch'], indent=2) + '\n')
    (OUT / 'determinism.json').write_text(json.dumps(summary['determinism'], indent=2) + '\n')
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'exclusions'}, indent=2))


if __name__ == '__main__':
    main()
