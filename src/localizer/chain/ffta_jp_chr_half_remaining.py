#! python3
"""Production localization for the remaining standard CHR_HALF words:* text.

These 246 entries each require at least one divergent font slot other than the
dash, so the universal-safe HALF serializer cannot represent them and the A5
milestone's single dedicated slot is not enough.

They are produced with exactly the A5 architecture, generalized.  The glyph
fetch at 0x0801344C computes the glyph address as US_FONT + (slot << 7) and the
advance as US_METADATA[slot] -- pure indexing with no range check and no lookup
table.  Its one branch tests a RAM fixed-width flag, not the slot.  So for any
slot H the rendered assets are exactly whatever is installed at H, and a
divergent token v can be redirected to a dedicated high slot H(v) holding the
native JP glyph and JP metadata for v.

Both consumer lanes follow, as for A5:

  * DIRECT -- the renderer decodes a FULL pair as ((b0 << 8) | b1) & 0x7FFF and
    calls the same 0x0801360C helper the HALF path uses;
  * NESTED -- a payload with no 0x01 marker takes the converter's pass-through
    branch at 0x08018C5A, which copies b0 and then b1 unconditionally, so FULL
    pairs survive verbatim including low bytes 0x00 and 0x01.

No ROM code is patched, no low slot is overwritten, and slot 0x06D9 keeps the
A5 mapping the previous milestone installed.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_chr_half_a5 as a5
from ffta_sect import c_ffta_sect_text_buf

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / 'rom/original/FFTA_JP.gba'
US = ROOT / 'rom/original/FFTA_US.gba'
OUTROM = ROOT / 'rom/build/ffta_us_jp_remaining.gba'
OUTROM2 = ROOT / 'rom/build/ffta_us_jp_remaining_repeat.gba'
OUT = Path(__file__).resolve().parent / 'build/chr_half_remaining_full/20260830_run'
ROM = 0x08000000
A5_BASELINE = 'CCF242B94AEF90A05A600092A018F682BC7DC82EBEF8B5C9D368E8128243A846'

A5_SLOT, A5_HIGH = a5.SLOT, a5.HIGH_SLOT      # 0xA5 -> 0x06D9, installed already
FIRST_NEW_HIGH = A5_HIGH + 1                  # 0x06DA
US_FONT, JP_FONT = a5.US_FONT, a5.JP_FONT
# rumor was 13 and TOTAL 248 before the words:rumor anchor correction: US 60
# and 61 no longer pair with the HALF-lane numeric placeholders JP 060 / 062.
EXPECT = {'battle': 17, 'clan': 72, 'content': 14, 'quest': 121, 'refer': 1,
          'rumor': 11, 'system': 9, 'title': 1}
TOTAL = 246
FAMILY_ORDER = tuple(sorted(EXPECT))


def sha(x):
    return hashlib.sha256(x.read_bytes() if isinstance(x, Path) else x).hexdigest().upper()


def select(jp, us, div, alloc):
    """Residual = standard words CHR_HALF entries needing a divergent slot other
    than the dash alone.  A5_PLUS_OTHER entries belong here, not to A5."""
    chosen = []
    for p in bulk.auto_pairs(jp, us)[0]:
        if not p['section'].startswith('words:'):
            continue
        if isinstance(p['jp_line'], list) or isinstance(p['us_line'], list):
            continue
        line = p['jp_line']
        if type(line).__name__ != 'c_ffta_sect_text_buf':
            continue
        vs = [v for k, v in line.tokens if k == 'CHR_HALF']
        if not vs:
            continue
        need = sorted(set(vs) & div)
        if not need or need == [A5_SLOT]:
            continue
        if any(k == 'CHR_FULL' and v not in alloc for k, v in line.tokens):
            raise RuntimeError('residual entry would need a new JP glyph')
        chosen.append((p['section'].split(':', 1)[1], tuple(p['us_path'])[0], p, need))
    counts = {}
    for f, _, _, _ in chosen:
        counts[f] = counts.get(f, 0) + 1
    if counts != EXPECT or len(chosen) != TOTAL:
        raise RuntimeError('CHR_HALF_RESIDUAL_ACCOUNTING_FAILED ' + repr(counts))
    return sorted(chosen, key=lambda x: (FAMILY_ORDER.index(x[0]), x[1]))


def slot_map(chosen, tsize):
    """Ascending divergent token -> ascending new high slot, after 0x06D9.
    The dash keeps the mapping the A5 milestone already installed."""
    need = sorted({v for _, _, _, n in chosen for v in n})
    hmap, nxt = {}, FIRST_NEW_HIGH
    for v in need:
        if v == A5_SLOT:
            hmap[v] = A5_HIGH
            continue
        if nxt >= tsize:
            raise RuntimeError('CHR_HALF_HIGH_SLOT_CAPACITY_FAILED')
        hmap[v] = nxt
        nxt += 1
    return hmap, need, nxt - 1


def promote(tokens, alloc, hmap):
    """Divergent HALF tokens go to their dedicated high slot; every other HALF
    token keeps its own slot, whose JP and US assets are identical."""
    out = []
    for kind, value in tokens:
        if kind == 'CHR_HALF':
            out.append(('CHR_FULL', hmap.get(value, value)))
        elif kind == 'CHR_FULL':
            out.append(('CHR_FULL', alloc[value]))
        else:
            out.append((kind, value))
    return out


def converter_passthrough(data):
    """Byte-exact model of the branch at 0x08018C5A, used to prove the nested
    lane preserves every emitted FULL pair."""
    out = bytearray()
    i = 0
    if not data or data[0] == 0:
        return bytes(out)
    if data[0] == 0x01:
        raise RuntimeError('payload would enter the converter HALF loop')
    while i < len(data) and data[i] != 0:
        b = data[i]
        out.append(b)
        i += 1
        if b & 0x80:
            out.append(data[i])
            i += 1
    return bytes(out)


def build():
    base, meta, alloc, uniend, a5_start, a5_end, a5_records = a5.build()
    jp, us = meta['jp'], meta['us']
    raw = bytearray(base)
    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    div = a5.divergent_slots(jpraw, usraw)
    chosen = select(jp, us, div, alloc)
    tsize = us.tabs['font'].tsize
    hmap, need, last_slot = slot_map(chosen, tsize)

    block_start = stext.align(a5_end, 4)
    cursor = block_start
    records = []
    for family, idx, p, entry_need in chosen:
        tokens = list(p['jp_line'].tokens)
        expected = promote(tokens, alloc, hmap)
        data = stext.encode_standard(expected)
        probe = c_ffta_sect_text_buf(bytearray(data), 0)
        probe.parse_size(None, 1)
        probe.parse()
        if probe.tokens != expected:
            raise RuntimeError('residual serializer roundtrip failed')
        if any(k == 'CHR_HALF' for k, _ in probe.tokens):
            raise RuntimeError('residual payload still contains a HALF token')
        # nested lane: the converter must reproduce the payload body exactly
        if converter_passthrough(data) != data[:-1]:
            raise RuntimeError('converter pass-through would not preserve payload')
        field = us.tabs['words'][family].real_offset + idx * 4
        old = int.from_bytes(raw[field:field + 4], 'little')
        raw[cursor:cursor + len(data)] = data
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, 'little')
        records.append({'family': family, 'index': idx,
                        'root_pointer_field_us_rom': f'0x{field:08X}',
                        'original_cpu_pointer': f'0x{old:08X}',
                        'new_cpu_pointer': f'0x{ROM + cursor:08X}',
                        'payload_length': len(data),
                        'half_occurrences': sum(1 for k, _ in tokens if k == 'CHR_HALF'),
                        'divergent_required': [f'0x{v:02X}' for v in entry_need],
                        'glyph_tokens': sum(1 for k, _ in expected if k == 'CHR_FULL'),
                        'high_slot_refs': sum(1 for k, v in expected
                                              if k == 'CHR_FULL' and v in set(hmap.values())),
                        'eos': data[-1] == 0})
        cursor = stext.align(cursor + len(data), 4)
    block_end = cursor

    fbase = us.tabs['font'].real_offset
    installed = []
    for v in need:
        h = hmap[v]
        if v == A5_SLOT:
            continue                       # already installed by the A5 milestone
        if h <= A5_HIGH or h >= tsize:
            raise RuntimeError('high slot outside the newly allocated range')
        raw[fbase + h * stext.FONT_STRIDE: fbase + (h + 1) * stext.FONT_STRIDE] = \
            jpraw[JP_FONT + v * stext.FONT_STRIDE: JP_FONT + (v + 1) * stext.FONT_STRIDE]
        raw[stext.US_METADATA + h] = jpraw[stext.JP_METADATA + v]
        installed.append((v, h))

    if len(raw) != len(base):
        raise RuntimeError('ROM size changed')
    return (bytes(raw), meta, alloc, hmap, need, installed, last_slot,
            block_start, block_end, records, base)


def validate(raw, records, block_start, block_end):
    ends = sorted(int(r['new_cpu_pointer'], 16) - ROM for r in records) + [block_end]
    seen = set()
    for r in records:
        field = int(r['root_pointer_field_us_rom'], 16)
        start = int(r['new_cpu_pointer'], 16) - ROM
        if int.from_bytes(raw[field:field + 4], 'little') != ROM + start:
            raise RuntimeError('residual pointer audit failed')
        if not block_start <= start < block_end:
            raise RuntimeError('residual payload outside the dedicated block')
        if not stext.TAIL_START <= start < stext.TAIL_START + stext.TAIL_CAPACITY:
            raise RuntimeError('residual payload outside the verified tail')
        stop = min(x for x in ends if x > start)
        probe = c_ffta_sect_text_buf(bytearray(raw[start:stop]), 0)
        probe.parse_size(None, 1)
        probe.parse()
        if raw[start + probe.raw_len - 1] != 0:
            raise RuntimeError('residual EOS missing')
        if field in seen:
            raise RuntimeError('residual pointer alias')
        seen.add(field)
    if len(seen) != TOTAL:
        raise RuntimeError('residual pointer count')


def main():
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError('original ROM SHA-256 mismatch')
    OUT.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    first, meta, alloc, hmap, need, installed, last_slot, bs, be, records, a5rom = build()
    second, _, alloc2, hmap2, _, installed2, _, bs2, be2, records2, _ = build()
    validate(first, records, bs, be)
    if sha(first) != sha(second) or (records, hmap, installed, bs, be, alloc) != \
       (records2, hmap2, installed2, bs2, be2, alloc2):
        raise RuntimeError('CHR_HALF_BUILD_NONDETERMINISTIC')
    if sha(a5rom) != A5_BASELINE:
        raise RuntimeError('A5 production ROM changed')

    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    fbase = meta['us'].tabs['font'].real_offset
    tsize = meta['us'].tabs['font'].tsize

    # asset audit: every rendered token must resolve to the intended JP assets
    mism = []
    for v, h in installed:
        if first[fbase + h * stext.FONT_STRIDE: fbase + (h + 1) * stext.FONT_STRIDE] != \
           jpraw[JP_FONT + v * stext.FONT_STRIDE: JP_FONT + (v + 1) * stext.FONT_STRIDE]:
            mism.append(('glyph', f'0x{v:02X}', f'0x{h:04X}'))
        if first[stext.US_METADATA + h] != jpraw[stext.JP_METADATA + v]:
            mism.append(('metadata', f'0x{v:02X}', f'0x{h:04X}'))
    if mism:
        raise RuntimeError('CHR_HALF_ASSET_AUDIT_FAILED ' + repr(mism[:8]))
    for v in range(0xFF):
        o = v * stext.FONT_STRIDE
        if first[fbase + o:fbase + o + stext.FONT_STRIDE] != usraw[fbase + o:fbase + o + stext.FONT_STRIDE] \
           or first[stext.US_METADATA + v] != usraw[stext.US_METADATA + v]:
            raise RuntimeError(f'low slot 0x{v:02X} was modified')

    # binary touch vs the confirmed A5 production ROM
    mask = bytearray(len(first))
    for r in records:
        f = int(r['root_pointer_field_us_rom'], 16)
        mask[f:f + 4] = b'\x01' * 4
    mask[bs:be] = b'\x01' * (be - bs)
    for _, h in installed:
        mask[fbase + h * stext.FONT_STRIDE: fbase + (h + 1) * stext.FONT_STRIDE] = \
            b'\x01' * stext.FONT_STRIDE
        mask[stext.US_METADATA + h] = 1
    unexplained = [(f'0x{a:08X}', f'0x{b:08X}')
                   for a, b in stext.changed_ranges(a5rom, first) if 0 in mask[a:b]]
    if unexplained:
        raise RuntimeError('CHR_HALF_BINARY_TOUCH_REGRESSION ' + repr(unexplained[:8]))
    changed = sum(1 for v in set(alloc.values()) | {A5_HIGH}
                  if first[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE]
                  != a5rom[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE])
    if changed:
        raise RuntimeError('an existing FULL mapping changed')

    OUTROM.write_bytes(first)
    OUTROM2.write_bytes(second)
    fam = {f: sum(r['family'] == f for r in records) for f in FAMILY_ORDER}
    glyph_tokens = sum(r['glyph_tokens'] for r in records)
    summary = {
        'status': 'STATIC_VALIDATED',
        'mechanism': 'hookless FULL promotion, generalized to every divergent slot',
        'residual': {'entries': len(records), 'expected': TOTAL, 'by_family': fam,
                     'half_occurrences': sum(r['half_occurrences'] for r in records)},
        'divergent': {'required_tokens': [f'0x{v:02X}' for v in need],
                      'required_count': len(need),
                      'reused_a5': f'0x{A5_SLOT:02X} -> 0x{A5_HIGH:04X}',
                      'newly_installed': len(installed)},
        'mapping': {'token_to_high_slot': {f'0x{v:02X}': f'0x{h:04X}'
                                           for v, h in sorted(hmap.items())},
                    'first_new_slot': f'0x{FIRST_NEW_HIGH:04X}',
                    'last_slot': f'0x{last_slot:04X}',
                    'total_full_mappings': len(alloc) + 1 + len(installed),
                    'remaining_capacity': tsize - 1 - last_slot,
                    'existing_mappings_changed': 0, 'low_slot_overwrites': 0},
        'block': {'start': f'0x{bs:08X}', 'end': f'0x{be:08X}', 'bytes': be - bs,
                  'payload_bytes': sum(r['payload_length'] for r in records),
                  'remaining_tail': stext.TAIL_CAPACITY - (be - stext.TAIL_START)},
        'serializer': {'glyph_tokens': glyph_tokens, 'chr_half_remaining': 0,
                       'roundtrip': 'PASS', 'converter_passthrough': 'PASS'},
        'pointers': {'patched': len(records), 'aliases': 0, 'overlaps': 0},
        'binary_touch': {'result': 'PASS', 'unexplained_ranges': unexplained,
                         'rom_code_bytes_changed': 0,
                         'allowed': ['246 words root pointer fields', 'new payload block',
                                     f'{len(installed)} high FULL glyph records',
                                     f'{len(installed)} metadata bytes']},
        'determinism': {'sha256_1': sha(first), 'sha256_2': sha(second), 'identical': True,
                        'a5_baseline': sha(a5rom)},
        'coverage': {'safe_no_half': 9424, 'universal_safe': 541, 'a5_only': 382,
                     'residual': len(records), 'total': 9424 + 541 + 382 + len(records),
                     'standard_chr_half_remaining': 0, 'ya_special_remaining': 3},
    }

    def w(name, obj):
        (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n',
                                encoding='utf-8')

    w('residual_inventory.json', records)
    w('divergent_slot_map.json', dict(summary['divergent'], **summary['mapping']))
    w('serializer_roundtrip.json', summary['serializer'])
    w('asset_audit.json', {'installed': [{'token': f'0x{v:02X}', 'high_slot': f'0x{h:04X}'}
                                         for v, h in installed], 'mismatches': 0,
                           'low_slots_modified': 0})
    w('pointer_audit.json', [{k: r[k] for k in ('family', 'index', 'root_pointer_field_us_rom',
                                                'original_cpu_pointer', 'new_cpu_pointer',
                                                'payload_length')} for r in records])
    w('binary_touch.json', summary['binary_touch'])
    w('determinism.json', summary['determinism'])
    w('summary.json', summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
