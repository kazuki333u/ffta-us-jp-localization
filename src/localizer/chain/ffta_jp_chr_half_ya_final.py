#! python3
"""Production localization for the final 3 YA_SPECIAL CHR_HALF entries.

These are the last parser-visible CHR_HALF entries in the project.  Their JP
source lines are YA buffers (text-line flags bit 0), whose grammar is unrelated
to the standard buffer: 0xFF is EOS, a byte with bit 7 set is a control routed
through _CTR_TRANS, and any other byte begins a two-byte character whose first
byte the parser discards.  The YA grammar has no CHR_FULL encoding at all, so
FULL promotion cannot be expressed in YA storage.

It does not need to be.  The shipped US ROM already stores all three of these
lines as standard buffers (flags bit 0 clear) -- the retail game renders them
through the ordinary path today -- and the YA parser normalises its control
bytes into the same CTR_FUNC value space the standard parser produces, so the
JP token stream re-encodes exactly.  Each line is therefore emitted as a
standard buffer with the YA and compression flag bits cleared, using the
already-proven standard serializer and the hookless FULL promotion.

All nine CHR_HALF slots are universal -- their JP and US glyph records and
metadata bytes are byte-identical -- so each promotes to CHR_FULL at its own
slot and no new high mapping is allocated.  The 1,492 existing FULL mappings,
slot 0x06D9 and the standard residual mappings through 0x06F5 are untouched.

Storage reuses the confirmed page-leaf architecture: the three owning leaves are
re-serialized with serialize_leaf, carrying both the replacements the no-HALF
milestone already makes and these three, and their root pointers are repointed
into fresh tail space.  No recursive repacker, no ROM code change.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_page_leaf_repoint as pages
import ffta_jp_chr_half_remaining as remaining
from ffta_sect import c_ffta_sect_text_buf, c_ffta_sect_text_buf_ya

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / 'rom/original/FFTA_JP.gba'
US = ROOT / 'rom/original/FFTA_US.gba'
OUTROM = ROOT / 'rom/build/ffta_us_jp_ya_final.gba'
OUTROM2 = ROOT / 'rom/build/ffta_us_jp_ya_final_repeat.gba'
OUT = Path(__file__).resolve().parent / 'build/chr_half_ya_final/20260830_run'
ROM = 0x08000000
US_FONT, JP_FONT = remaining.US_FONT, remaining.JP_FONT
BASELINE = 'A17F611AC395EC5C8088CC0383C15A50AB3EE2C0DBFF1CA6B4652EA3E433BE8C'

# section -> JP/US logical path of the YA entry
TARGETS = {'fx_text': (16, 48), 'pages:choice': (39,), 'pages:condi': (15,)}
TOTAL, OCCURRENCES = 3, 9


def sha(x):
    return hashlib.sha256(x.read_bytes() if isinstance(x, Path) else x).hexdigest().upper()


def universal_slots(jpraw, usraw):
    def ok(v):
        o = v * stext.FONT_STRIDE
        return (usraw[US_FONT + o:US_FONT + o + stext.FONT_STRIDE] ==
                jpraw[JP_FONT + o:JP_FONT + o + stext.FONT_STRIDE]
                and usraw[stext.US_METADATA + v] == jpraw[stext.JP_METADATA + v])
    return {v for v in range(0xFF) if ok(v)}


def select(jp, us):
    """The YA_SPECIAL set: pairs whose JP inner buffer is a YA buffer carrying
    at least one CHR_HALF token."""
    chosen = []
    for p in bulk.auto_pairs(jp, us)[0]:
        if isinstance(p['jp_line'], list) or isinstance(p['us_line'], list):
            continue
        jb = getattr(p['jp_line'], 'text', p['jp_line'])
        if not isinstance(jb, c_ffta_sect_text_buf_ya):
            continue
        if not any(k == 'CHR_HALF' for k, _ in jb.tokens):
            continue
        chosen.append(p)
    keys = {(p['section'], tuple(p['us_path'])) for p in chosen}
    if len(chosen) != TOTAL or keys != {(s, t) for s, t in TARGETS.items()}:
        raise RuntimeError('YA_SPECIAL_ACCOUNTING_REGRESSION ' + repr(sorted(keys)))
    occ = sum(1 for p in chosen
              for k, _ in getattr(p['jp_line'], 'text').tokens if k == 'CHR_HALF')
    if occ != OCCURRENCES:
        raise RuntimeError(f'YA_SPECIAL_ACCOUNTING_REGRESSION occurrences={occ}')
    return sorted(chosen, key=lambda p: (p['section'], tuple(p['us_path'])))


def ya_replacement(jp_line, universal, alloc):
    """Emit the JP YA line as a standard buffer: clear the YA and compression
    flag bits and re-encode with the proven standard serializer."""
    source = jp_line.text
    if not isinstance(source, c_ffta_sect_text_buf_ya):
        raise TypeError('not a YA source')
    tokens = []
    for kind, value in source.tokens:
        if kind == 'CHR_HALF':
            if value not in universal:
                raise RuntimeError(f'YA slot 0x{value:02X} is not universal')
            tokens.append(('CHR_FULL', value))
        elif kind == 'CHR_FULL':
            tokens.append(('CHR_FULL', alloc[value]))
        elif kind.startswith('ERR_'):
            raise RuntimeError(f'YA source carries {kind}')
        else:
            tokens.append((kind, value))
    data = stext.encode_standard(tokens)
    probe = c_ffta_sect_text_buf(bytearray(data), 0)
    probe.parse_size(None, 1)
    probe.parse()
    if list(probe.tokens) != tokens:
        raise RuntimeError('YA serializer roundtrip failed')
    if any(k == 'CHR_HALF' for k, _ in probe.tokens):
        raise RuntimeError('YA payload still contains a HALF token')
    flags = jp_line.U16(0) & ~0x0003          # clear YA bit and compression bit
    return flags.to_bytes(2, 'little') + data, tokens


def build():
    base, meta, alloc, hmap, need, installed, last_slot, rbs, rbe, rrecords, _ = remaining.build()
    jp, us = meta['jp'], meta['us']
    raw = bytearray(base)
    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    universal = universal_slots(jpraw, usraw)
    chosen = select(jp, us)

    # The three owning leaves, with the replacements the no-HALF milestone
    # already applies, so the rebuild is a superset rather than a regression.
    sel = pages.get_remaining(jp, us)
    objs = pages.leaves(us, sel)

    def leaf_key(p):
        if p['section'] == 'fx_text':
            return ('fx_text', p['us_path'][0])
        return (p['section'], p['us_table'])

    groups = {}
    for p in chosen:
        groups.setdefault(leaf_key(p), []).append(p)

    cursor = stext.align(rbe, 4)
    manifest = []
    ya_records = []
    for key in sorted(groups, key=lambda k: (str(k[0]), str(k[1]))):
        obj = objs[key]
        leaf, field = obj['leaf'], obj['field']
        repl, expected = {}, {}
        for p in obj['items']:                       # existing no-HALF entries
            idx = p['us_path'][-1]
            repl[idx] = stext.replacement_line(p['jp_line'], alloc)
            expected[idx] = [(k, alloc[v]) if k == 'CHR_FULL' else (k, v)
                             for k, v in p['jp_line'].text.tokens]
        pre_existing = len(repl)
        for p in groups[key]:                        # the YA entries
            idx = p['us_path'][-1]
            if idx in repl:
                raise RuntimeError('YA entry collides with a no-HALF replacement')
            blob, toks = ya_replacement(p['jp_line'], universal, alloc)
            repl[idx] = blob
            expected[idx] = toks
            ya_records.append({
                'section': p['section'], 'us_path': list(p['us_path']),
                'jp_path': list(p['jp_path']),
                'jp_line_us_rom': f"0x{p['jp_line'].real_offset:08X}",
                'us_line_us_rom': f"0x{p['us_line'].real_offset:08X}",
                'leaf_root_field': f'0x{field:08X}',
                'chr_half_values': [f'0x{v:02X}' for k, v in p['jp_line'].text.tokens
                                    if k == 'CHR_HALF'],
                'chr_half_count': sum(1 for k, _ in p['jp_line'].text.tokens
                                      if k == 'CHR_HALF'),
                'promoted_tokens': [[k, v] for k, v in toks],
                'replacement_bytes': blob.hex(' '),
                'replacement_length': len(blob),
                'new_mappings_required': 0,
            })
        blob = stext.serialize_leaf(usraw, leaf, repl)
        old = int.from_bytes(raw[field:field + 4], 'little')
        raw[cursor:cursor + len(blob)] = blob
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, 'little')
        manifest.append({'leaf': key[0], 'root_path': str(key[1]),
                         'root_field_us_rom': f'0x{field:08X}',
                         'original_leaf_us_rom': f'0x{leaf.real_offset:08X}',
                         'old_cpu_pointer': f'0x{old:08X}',
                         'new_leaf_us_rom': f'0x{cursor:08X}',
                         'new_cpu_pointer': f'0x{ROM + cursor:08X}',
                         'new_size': len(blob),
                         'entries_replaced': len(repl),
                         'pre_existing_no_half_replacements': pre_existing,
                         'ya_entries_added': len(groups[key]),
                         '_leaf': leaf, '_blob': blob, '_repl': repl, '_expected': expected})
        cursor = stext.align(cursor + len(blob), 4)

    if len(raw) != len(base):
        raise RuntimeError('ROM size changed')
    return bytes(raw), meta, alloc, manifest, ya_records, rbe, cursor, base


def validate(raw, manifest, block_start, block_end):
    pristine = US.read_bytes()
    for x in manifest:
        field = int(x['root_field_us_rom'], 16)
        start = int(x['new_leaf_us_rom'], 16)
        if int.from_bytes(raw[field:field + 4], 'little') != ROM + start:
            raise RuntimeError('YA_SPECIAL_POINTER_LEAF_AUDIT_FAILED: root pointer')
        if not block_start <= start < block_end:
            raise RuntimeError('YA_SPECIAL_POINTER_LEAF_AUDIT_FAILED: outside block')
        if not stext.TAIL_START <= start < stext.TAIL_START + stext.TAIL_CAPACITY:
            raise RuntimeError('YA_SPECIAL_POINTER_LEAF_AUDIT_FAILED: outside tail')
        # every untouched sibling must stay byte/logically equivalent
        stext.validate_leaf(x['_blob'], pristine, x['_leaf'], x['_repl'], x['_expected'])
    fields = [x['root_field_us_rom'] for x in manifest]
    if len(set(fields)) != len(fields):
        raise RuntimeError('YA_SPECIAL_POINTER_LEAF_AUDIT_FAILED: alias')


def main():
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError('original ROM SHA-256 mismatch')
    OUT.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    first, meta, alloc, manifest, ya_records, bs, be, prev = build()
    second, _, _, manifest2, ya2, _, be2, _ = build()
    validate(first, manifest, bs, be)
    strip = lambda m: [{k: v for k, v in x.items() if not k.startswith('_')} for x in m]
    if sha(first) != sha(second) or (strip(manifest), ya_records, be) != \
       (strip(manifest2), ya2, be2):
        raise RuntimeError('YA_SPECIAL_BUILD_NONDETERMINISTIC')
    if sha(prev) != BASELINE:
        raise RuntimeError('confirmed standard production ROM changed')

    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    fbase = meta['us'].tabs['font'].real_offset

    # asset audit: all nine occurrences must resolve to the intended JP assets
    glyph_ok = meta_ok = 0
    for r in ya_records:
        for k, v in r['promoted_tokens']:
            if k != 'CHR_FULL':
                continue
            o = v * stext.FONT_STRIDE
            if first[fbase + o:fbase + o + stext.FONT_STRIDE] == \
               jpraw[JP_FONT + o:JP_FONT + o + stext.FONT_STRIDE]:
                glyph_ok += 1
            else:
                raise RuntimeError(f'YA_SPECIAL_ASSET_AUDIT_FAILED glyph 0x{v:02X}')
            if first[stext.US_METADATA + v] == jpraw[stext.JP_METADATA + v]:
                meta_ok += 1
            else:
                raise RuntimeError(f'YA_SPECIAL_ASSET_AUDIT_FAILED metadata 0x{v:02X}')
    if glyph_ok != OCCURRENCES or meta_ok != OCCURRENCES:
        raise RuntimeError(f'YA_SPECIAL_ASSET_AUDIT_FAILED counts {glyph_ok}/{meta_ok}')

    # binary touch: only the three root pointer fields and the new leaf region
    mask = bytearray(len(first))
    for x in manifest:
        f = int(x['root_field_us_rom'], 16)
        mask[f:f + 4] = b'\x01' * 4
    mask[bs:be] = b'\x01' * (be - bs)
    unexplained = [(f'0x{a:08X}', f'0x{b:08X}')
                   for a, b in stext.changed_ranges(prev, first) if 0 in mask[a:b]]
    if unexplained:
        raise RuntimeError('YA_SPECIAL_BINARY_TOUCH_REGRESSION ' + repr(unexplained[:8]))
    if first[fbase:fbase + meta['us'].tabs['font'].tsize * stext.FONT_STRIDE] != \
       prev[fbase:fbase + meta['us'].tabs['font'].tsize * stext.FONT_STRIDE]:
        raise RuntimeError('font table changed')
    if first[stext.US_METADATA:stext.US_METADATA + meta['us'].tabs['font'].tsize] != \
       prev[stext.US_METADATA:stext.US_METADATA + meta['us'].tabs['font'].tsize]:
        raise RuntimeError('metadata table changed')

    OUTROM.write_bytes(first)
    OUTROM2.write_bytes(second)
    summary = {
        'status': 'STATIC_VALIDATED',
        'mechanism': 'YA source re-emitted as a standard buffer with FULL promotion; '
                     'no new glyph mapping, no ROM code change',
        'inventory': {'entries': len(ya_records), 'expected': TOTAL,
                      'chr_half_occurrences': sum(r['chr_half_count'] for r in ya_records),
                      'sections': sorted(r['section'] for r in ya_records)},
        'format': {'ya_eos': '0xFF', 'ya_control': 'bit 7 set, routed through _CTR_TRANS',
                   'ya_character': 'two bytes; the parser discards the first',
                   'ya_has_chr_full': False,
                   'us_ships_these_lines_as': 'standard buffer (flags bit 0 clear)',
                   'flag_bits_cleared': '0x0001 (YA) and 0x0002 (compression)'},
        'mapping': {'new_mappings': 0, 'reused_existing_mappings': OCCURRENCES,
                    'total_full_mappings': 1492, 'last_slot': '0x06F5',
                    'remaining_capacity': 1393,
                    'all_ya_slots_universal': True},
        'serializer': {'chr_half_remaining': 0, 'roundtrip': 'PASS'},
        'storage': {'architecture': 'page-leaf repoint (serialize_leaf), no recursive repacker',
                    'leaves_rebuilt': len(manifest),
                    'block_start': f'0x{bs:08X}', 'block_end': f'0x{be:08X}',
                    'bytes': be - bs,
                    'remaining_tail': stext.TAIL_CAPACITY - (be - stext.TAIL_START)},
        'assets': {'glyph_matches': glyph_ok, 'metadata_matches': meta_ok,
                   'new_low_slot_overwrites': 0, 'font_table_changed': False,
                   'metadata_table_changed': False},
        'binary_touch': {'result': 'PASS', 'unexplained_ranges': unexplained,
                         'rom_code_bytes_changed': 0,
                         'allowed': ['3 leaf root pointer fields', 'new rebuilt leaf region']},
        'determinism': {'sha256_1': sha(first), 'sha256_2': sha(second), 'identical': True,
                        'previous_baseline': sha(prev)},
        'coverage': {'previous': 10593, 'ya': len(ya_records),
                     'total': 10593 + len(ya_records),
                     'chr_half_remaining_standard': 0, 'chr_half_remaining_ya': 0,
                     'chr_half_remaining_total': 0},
    }

    def w(name, obj):
        (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n',
                                encoding='utf-8')

    w('ya_inventory.json', ya_records)
    w('ya_format_contract.json', summary['format'])
    w('ya_storage.json', strip(manifest))
    w('ya_full_compatibility.json', {'classification': 'YA_HOOKLESS_FULL_COMPATIBLE',
                                     'entries': TOTAL, 'occurrences': OCCURRENCES,
                                     'new_mappings': 0, 'all_slots_universal': True})
    w('serializer_roundtrip.json', summary['serializer'])
    w('pointer_leaf_audit.json', strip(manifest))
    w('asset_audit.json', summary['assets'])
    w('binary_touch.json', summary['binary_touch'])
    w('determinism.json', summary['determinism'])
    w('summary.json', summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
