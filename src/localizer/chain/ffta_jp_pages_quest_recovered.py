#! python3
"""Recovered pages:quest localization, and repair of the false-anchor transfer.

The correspondence audit keys entries by the path shape merge_tabs produces, and
that shape is derived per ROM: JP holds pages:quest in ONE sub-table so its keys
are bare paths, while US splits the same 511 quests across TWO sub-tables so its
keys carry an outer index.  The two key spaces share nothing, so the matcher
reported 2 AUTO_MATCH against 509 JP_ONLY and 509 US_ONLY -- and both of those
"matches" were path-padding collisions.  One was correct by coincidence; the
other paired JP ordinal 2 with US ordinal 200 and was transferred into
production, so the shipped ROM shows the wrong quest description there.

The true correspondence is linear: flatten each side in physical order and pair
by ordinal, US 0..199 being pages:quest/0 and 200..510 being pages:quest/1.  That
rule is implemented here rather than as a hand-maintained table, and rather than
by changing merge_tabs globally, which would perturb key shapes for every other
section.

Only the 376 ordinals where BOTH sides carry visible text are production targets.
129 ordinals have JP text but a control-only US stub, and 6 are control-only on
both sides; all 135 keep their pristine US bytes.  Both quest leaves are rebuilt
from the pristine US ROM, so the corrected content supersedes the historical
false-anchor replacement instead of layering over it.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_chr_half_ya_final as yafinal
import ffta_jp_chr_half_remaining as remaining
from ffta_sect import c_ffta_sect_text_buf

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / 'rom/original/FFTA_JP.gba'
US = ROOT / 'rom/original/FFTA_US.gba'
OUTROM = ROOT / 'rom/build/ffta_us_jp_quest.gba'
OUTROM2 = ROOT / 'rom/build/ffta_us_jp_quest_repeat.gba'
OUT = Path(__file__).resolve().parent / 'build/pages_quest_production/20260830_run'
ROM = 0x08000000
BASELINE = 'F8EB62DA9999997DE6698B56C108BBB8D82E05A25016DBE5BBF4A1008C575157'

US_FONT, JP_FONT = remaining.US_FONT, remaining.JP_FONT
QUEST_ROOT_FIELD = {'quest/0': 0x00013CD8, 'quest/1': 0x00013CC0}
JP_SUBS = ('quest/0',)
US_SUBS = ('quest/0', 'quest/1')
TOTAL_ORDINALS = 376 + 129 + 6          # targets + US stubs + non-text
EXPECT = {'TARGET': 376, 'US_STUB_NO_VISIBLE_TARGET': 129, 'NON_TEXT': 6, 'US_ADDITION': 0}
FALSE_ANCHOR_ORDINAL = 200              # historically fed from JP ordinal 2


def sha(x):
    return hashlib.sha256(x.read_bytes() if isinstance(x, Path) else x).hexdigest().upper()


def flatten(rom, subs):
    """Linear ordinal enumeration over the quest sub-tables in physical order."""
    out = []
    for sub in subs:
        tab = rom.tabs['pages'][sub]
        for index in range(tab.tsize):
            out.append({'sub': sub, 'index': index, 'line': tab[index]})
    return out


def tokens_of(rec):
    line = rec['line']
    if isinstance(line, list):
        return None
    buf = getattr(line, 'text', line)
    return list(getattr(buf, 'tokens', ()))


def visible(toks):
    return bool(toks) and any(k.startswith('CHR_') for k, _ in toks)


def correspondence(jp, us):
    """The recovered rule: JP linear ordinal i <-> US linear ordinal i."""
    J, U = flatten(jp, JP_SUBS), flatten(us, US_SUBS)
    if len(J) != len(U) or len(J) != TOTAL_ORDINALS:
        raise RuntimeError(f'quest ordinal count {len(J)}/{len(U)}')
    rows = []
    for i, (a, b) in enumerate(zip(J, U)):
        ja, ub = tokens_of(a), tokens_of(b)
        if ja is None or ub is None:
            raise RuntimeError(f'unexpected repeat marker at quest ordinal {i}')
        jv, uv = visible(ja), visible(ub)
        cls = ('TARGET' if jv and uv else
               'US_STUB_NO_VISIBLE_TARGET' if jv else
               'US_ADDITION' if uv else 'NON_TEXT')
        rows.append({'ordinal': i, 'jp_sub': a['sub'], 'jp_index': a['index'],
                     'us_sub': b['sub'], 'us_index': b['index'], 'classification': cls,
                     'jp_tokens': len(ja), 'us_tokens': len(ub),
                     'jp_rom_offset': f"0x{a['line'].real_offset:08X}",
                     'us_rom_offset': f"0x{b['line'].real_offset:08X}"})
    counts = {k: 0 for k in EXPECT}
    for r in rows:
        counts[r['classification']] = counts.get(r['classification'], 0) + 1
    if counts != EXPECT:
        raise RuntimeError(f'PAGES_QUEST_CORRESPONDENCE_ACCOUNTING_FAILED {counts}')
    return rows, J, U


def build():
    base, meta, alloc, ya_manifest, ya_records, ybs, ybe, prev = yafinal.build()
    jp, us = meta['jp'], meta['us']
    raw = bytearray(base)
    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    rows, J, U = correspondence(jp, us)
    targets = [r for r in rows if r['classification'] == 'TARGET']

    # ---- glyph extension: allocate only genuinely new JP glyphs -------------
    alloc = dict(alloc)
    before = len(alloc)
    # highest slot already owned by production: the JP->US map, the A5 dash slot,
    # and the 28 divergent slots the residual milestone installed contiguously.
    divergent_last = remaining.FIRST_NEW_HIGH + 28 - 1
    first_free = max(max(alloc.values()), remaining.A5_HIGH, divergent_last) + 1
    if first_free != 0x06F6:
        raise RuntimeError(f'unexpected first free slot 0x{first_free:04X}')
    need = set()
    for r in targets:
        for kind, value in tokens_of(J[r['ordinal']]):
            if kind == 'CHR_FULL':
                need.add(value)
            if kind == 'CHR_HALF':
                raise RuntimeError('CHR_HALF in a quest source; census says none exist')
    new = sorted(need - set(alloc))
    fbase = us.tabs['font'].real_offset
    installed = []
    for n, g in enumerate(new):
        slot = first_free + n
        if slot >= us.tabs['font'].tsize:
            raise RuntimeError('PAGES_QUEST_GLYPH_CAPACITY_FAILED')
        alloc[g] = slot
        raw[fbase + slot * stext.FONT_STRIDE: fbase + (slot + 1) * stext.FONT_STRIDE] = \
            jpraw[JP_FONT + g * stext.FONT_STRIDE: JP_FONT + (g + 1) * stext.FONT_STRIDE]
        raw[stext.US_METADATA + slot] = jpraw[stext.JP_METADATA + g]
        installed.append({'jp_glyph': g, 'production_slot': f'0x{slot:04X}',
                          'metadata': f'0x{jpraw[stext.JP_METADATA + g]:02X}'})
    last_slot = first_free + len(new) - 1 if new else first_free - 1

    # ---- rebuild both US quest leaves from pristine US ----------------------
    cursor = stext.align(ybe, 4)
    manifest = []
    for sub in US_SUBS:
        leaf = us.tabs['pages'][sub]
        repl, expected = {}, {}
        for r in targets:
            if r['us_sub'] != sub:
                continue
            jline = J[r['ordinal']]['line']
            repl[r['us_index']] = stext.replacement_line(jline, alloc)
            expected[r['us_index']] = [(k, alloc[v]) if k == 'CHR_FULL' else (k, v)
                                       for k, v in tokens_of(J[r['ordinal']])]
        blob = stext.serialize_leaf(usraw, leaf, repl)
        field = QUEST_ROOT_FIELD[sub]
        old = int.from_bytes(raw[field:field + 4], 'little')
        raw[cursor:cursor + len(blob)] = blob
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, 'little')
        manifest.append({'sub': sub, 'entries': leaf.tsize, 'replaced': len(repl),
                         'preserved': leaf.tsize - len(repl),
                         'root_field_us_rom': f'0x{field:08X}',
                         'original_leaf_us_rom': f'0x{leaf.real_offset:08X}',
                         'old_cpu_pointer': f'0x{old:08X}',
                         'new_leaf_us_rom': f'0x{cursor:08X}',
                         'new_cpu_pointer': f'0x{ROM + cursor:08X}',
                         'new_size': len(blob),
                         '_leaf': leaf, '_blob': blob, '_repl': repl, '_expected': expected})
        cursor = stext.align(cursor + len(blob), 4)

    if len(raw) != len(base):
        raise RuntimeError('ROM size changed')
    return (bytes(raw), meta, alloc, rows, targets, manifest, installed,
            before, last_slot, ybe, cursor, prev, base, J, U)


def validate(raw, manifest, block_start, block_end):
    pristine = US.read_bytes()
    for x in manifest:
        field = int(x['root_field_us_rom'], 16)
        start = int(x['new_leaf_us_rom'], 16)
        if int.from_bytes(raw[field:field + 4], 'little') != ROM + start:
            raise RuntimeError('PAGES_QUEST_LEAF_AUDIT_FAILED: root pointer')
        if not block_start <= start < block_end:
            raise RuntimeError('PAGES_QUEST_LEAF_AUDIT_FAILED: outside block')
        if not stext.TAIL_START <= start < stext.TAIL_START + stext.TAIL_CAPACITY:
            raise RuntimeError('PAGES_QUEST_LEAF_AUDIT_FAILED: outside tail')
        stext.validate_leaf(x['_blob'], pristine, x['_leaf'], x['_repl'], x['_expected'])


def main():
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError('original ROM SHA-256 mismatch')
    OUT.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    (first, meta, alloc, rows, targets, manifest, installed, before, last_slot,
     bs, be, prev, ya_rom, J, U) = build()
    second = build()
    validate(first, manifest, bs, be)
    strip = lambda m: [{k: v for k, v in x.items() if not k.startswith('_')} for x in m]
    if sha(first) != sha(second[0]) or (rows, targets, strip(manifest), installed) != \
       (second[3], second[4], strip(second[5]), second[6]):
        raise RuntimeError('PAGES_QUEST_BUILD_NONDETERMINISTIC')
    if sha(ya_rom) != BASELINE:
        raise RuntimeError('production baseline ROM changed')

    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    fbase = meta['us'].tabs['font'].real_offset
    tsize = meta['us'].tabs['font'].tsize

    # ---- asset audit -------------------------------------------------------
    glyph_ok = meta_ok = 0
    for rec in installed:
        g = rec['jp_glyph']; slot = int(rec['production_slot'], 16)
        if first[fbase + slot * stext.FONT_STRIDE: fbase + (slot + 1) * stext.FONT_STRIDE] != \
           jpraw[JP_FONT + g * stext.FONT_STRIDE: JP_FONT + (g + 1) * stext.FONT_STRIDE]:
            raise RuntimeError(f'PAGES_QUEST_ASSET_AUDIT_FAILED glyph {g}')
        if first[stext.US_METADATA + slot] != jpraw[stext.JP_METADATA + g]:
            raise RuntimeError(f'PAGES_QUEST_ASSET_AUDIT_FAILED metadata {g}')
        glyph_ok += 1; meta_ok += 1
    for v in range(0xFF):
        o = v * stext.FONT_STRIDE
        if first[fbase + o:fbase + o + stext.FONT_STRIDE] != usraw[fbase + o:fbase + o + stext.FONT_STRIDE] \
           or first[stext.US_METADATA + v] != usraw[stext.US_METADATA + v]:
            raise RuntimeError(f'low slot 0x{v:02X} was modified')

    # ---- binary touch vs the production baseline ---------------------------
    mask = bytearray(len(first))
    for x in manifest:
        f = int(x['root_field_us_rom'], 16)
        mask[f:f + 4] = b'\x01' * 4
    mask[bs:be] = b'\x01' * (be - bs)
    for rec in installed:
        slot = int(rec['production_slot'], 16)
        mask[fbase + slot * stext.FONT_STRIDE: fbase + (slot + 1) * stext.FONT_STRIDE] = \
            b'\x01' * stext.FONT_STRIDE
        mask[stext.US_METADATA + slot] = 1
    unexplained = [(f'0x{a:08X}', f'0x{b:08X}')
                   for a, b in stext.changed_ranges(ya_rom, first) if 0 in mask[a:b]]
    if unexplained:
        raise RuntimeError('PAGES_QUEST_BINARY_TOUCH_REGRESSION ' + repr(unexplained[:8]))
    prior = set(alloc.values()) - {int(r['production_slot'], 16) for r in installed}
    if any(first[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE]
           != ya_rom[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE]
           for v in prior):
        raise RuntimeError('an existing FULL mapping changed')

    OUTROM.write_bytes(first); OUTROM2.write_bytes(second[0])
    counts = {}
    for r in rows:
        counts[r['classification']] = counts.get(r['classification'], 0) + 1
    summary = {
        'status': 'STATIC_VALIDATED',
        'correspondence': {'rule': 'JP linear ordinal i <-> US linear ordinal i '
                                   '(US 0..199 = pages:quest/0, 200..510 = pages:quest/1)',
                           'ordinals': len(rows), 'classification': counts,
                           'unresolved': 0, 'aliases': 0},
        'targets': {'total': len(targets),
                    'false_anchor_ordinal': FALSE_ANCHOR_ORDINAL,
                    'false_anchor_corrected': any(r['ordinal'] == FALSE_ANCHOR_ORDINAL
                                                  for r in targets)},
        'leaves': strip(manifest),
        'glyphs': {'required': len(need_count(targets, J)), 'reused': before,
                   'new': len(installed), 'cumulative': before + len(installed),
                   'first_new_slot': installed[0]['production_slot'] if installed else None,
                   'last_slot': f'0x{last_slot:04X}',
                   'remaining_capacity': tsize - 1 - last_slot,
                   'existing_mappings_changed': 0, 'low_slot_overwrites': 0},
        'assets': {'glyph_matches': glyph_ok, 'metadata_matches': meta_ok, 'errors': 0},
        'preservation': {'us_stubs_preserved': counts['US_STUB_NO_VISIBLE_TARGET'],
                         'non_text_preserved': counts['NON_TEXT'],
                         'method': 'both leaves are rebuilt from the pristine US ROM and only the '
                                   '376 targets are replaced, so every other entry keeps its '
                                   'original US bytes'},
        'block': {'start': f'0x{bs:08X}', 'end': f'0x{be:08X}', 'bytes': be - bs,
                  'remaining_tail': stext.TAIL_CAPACITY - (be - stext.TAIL_START)},
        'binary_touch': {'result': 'PASS', 'unexplained_ranges': unexplained,
                         'rom_code_bytes_changed': 0,
                         'allowed': ['2 pages:quest root pointer fields', 'rebuilt quest leaves',
                                     f'{len(installed)} new glyph records',
                                     f'{len(installed)} metadata bytes']},
        'determinism': {'sha256_1': sha(first), 'sha256_2': sha(second[0]), 'identical': True,
                        'previous_baseline': sha(ya_rom)},
    }

    def w(name, obj):
        (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n',
                                encoding='utf-8')

    w('correspondence_manifest.json', rows)
    w('target_set.json', targets)
    w('glyph_map.json', {'reused': before, 'new': len(installed),
                         'cumulative': before + len(installed),
                         'last_slot': f'0x{last_slot:04X}',
                         'remaining_capacity': tsize - 1 - last_slot,
                         'installed': installed})
    w('leaf_audit.json', strip(manifest))
    w('asset_audit.json', summary['assets'])
    w('preservation_audit.json', summary['preservation'])
    w('binary_touch.json', summary['binary_touch'])
    w('determinism.json', summary['determinism'])
    w('summary.json', summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def need_count(targets, J):
    s = set()
    for r in targets:
        for kind, value in tokens_of(J[r['ordinal']]):
            if kind == 'CHR_FULL':
                s.add(value)
    return s


if __name__ == '__main__':
    main()
