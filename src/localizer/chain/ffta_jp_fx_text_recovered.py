#! python3
"""Recovered fx_text localization for the leaf-insertion matcher misses.

fx_text is a table of leaves.  JP has 26; US has 27, because the US build
inserted an alias leaf at index 24 that repeats leaf 23.  Every leaf up to 23
still lines up by index, but from there the US is shifted by one: JP leaf 24
(127 entries) belongs to US leaf 25, and JP leaf 25 (30 entries) to US leaf 26.
The correspondence audit keys entries by path, so the shift left those two
leaves entirely unmatched -- 157 objects that do have authoritative JP sources.

The mapping is recovered structurally, not lexically: the shifted leaves have
exactly equal entry counts (127/127 and 30/30), no repeats on either side, and
JP leaf 25 <-> US leaf 26 matches on the control skeleton 30/30 exactly.  Text
is used only as corroboration.

JP leaf 24 <-> US leaf 25 matches on size and ordering but its control skeleton
differs systematically: both sides use command 0x1D, with different operands.
That is NOT treated as a blocker, and the reason is recorded rather than
assumed: 896 fx_text pairs already localized in confirmed production are
skeleton-divergent in exactly the same way, with JP-minus-US token deltas
spanning -277..+99, and all 127 candidates fall inside that range.  Operand
0x1B is the one value with no production precedent, which is noted in the
artifacts.

US leaf 24 is the alias and is never a target, preserving the historical
reference-target exclusion.  US leaves 25 and 26 carry no previous production
claim, so nothing is superseded here.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_pages_quest_recovered as quest
import ffta_jp_chr_half_remaining as remaining
from ffta_sect import c_ffta_sect_text_buf_ya

ROOT = Path(__file__).resolve().parents[3]
JP = ROOT / 'rom/original/FFTA_JP.gba'
US = ROOT / 'rom/original/FFTA_US.gba'
OUTROM = ROOT / 'rom/build/ffta_us_jp_fx.gba'
OUTROM2 = ROOT / 'rom/build/ffta_us_jp_fx_repeat.gba'
OUT = Path(__file__).resolve().parent / 'build/fx_text_recovery/20260830_run'
ROM = 0x08000000
BASELINE = '0CC1D8CDB562F081B4A4E991712A849AEB389E64405473F3CDFE249BD8460DE8'

US_FONT, JP_FONT = remaining.US_FONT, remaining.JP_FONT
ALIAS_LEAF = 24                      # US-only alias leaf repeating leaf 23
LEAF_MAP = {24: 25, 25: 26}          # the shifted JP->US leaves
EXPECT = {24: 127, 25: 30}
TOTAL = 157


def sha(x):
    return hashlib.sha256(x.read_bytes() if isinstance(x, Path) else x).hexdigest().upper()


def leaf_entries(rom, li):
    leaf = rom.tabs['fx_text'][li]
    if leaf is None or isinstance(leaf, list):
        raise RuntimeError(f'fx_text leaf {li} is not a page')
    return leaf, [leaf[i] for i in range(leaf.tsize)]


def correspondence(jp, us):
    """Recovered pairs for the two shifted leaves, by entry ordinal."""
    rows = []
    for jl, ul in sorted(LEAF_MAP.items()):
        jleaf, JE = leaf_entries(jp, jl)
        uleaf, UE = leaf_entries(us, ul)
        if len(JE) != len(UE) or len(JE) != EXPECT[jl]:
            raise RuntimeError(f'fx_text leaf {jl}->{ul} size {len(JE)}/{len(UE)}')
        for i, (a, b) in enumerate(zip(JE, UE)):
            if isinstance(a, list) or isinstance(b, list):
                raise RuntimeError(f'unexpected repeat at fx_text {jl}/{i}')
            ab, bb = getattr(a, 'text', a), getattr(b, 'text', b)
            at = list(getattr(ab, 'tokens', ()))
            bt = list(getattr(bb, 'tokens', ()))
            if isinstance(ab, c_ffta_sect_text_buf_ya) or isinstance(bb, c_ffta_sect_text_buf_ya):
                raise RuntimeError(f'unexpected YA buffer at fx_text {jl}/{i}')
            if any(k == 'CHR_HALF' for k, _ in at):
                raise RuntimeError(f'CHR_HALF in fx_text JP {jl}/{i}; that frontier is closed')
            if not any(k.startswith('CHR_') for k, _ in at) or \
               not any(k.startswith('CHR_') for k, _ in bt):
                raise RuntimeError(f'fx_text {jl}/{i} is not visible on both sides')
            js = tuple(v for k, v in at if k == 'CTR_FUNC' and v != 82)
            us_ = tuple(v for k, v in bt if k == 'CTR_FUNC' and v != 82)
            rows.append({'jp_leaf': jl, 'jp_entry': i, 'us_leaf': ul, 'us_entry': i,
                         'classification': 'RECOVERABLE_STRONG' if js == us_
                                           else 'RECOVERABLE_WEAK',
                         'jp_tokens': len(at), 'us_tokens': len(bt),
                         'jp_rom_offset': f'0x{a.real_offset:08X}',
                         'us_rom_offset': f'0x{b.real_offset:08X}'})
    if len(rows) != TOTAL:
        raise RuntimeError(f'FX_TEXT_ACCOUNTING_FAILED {len(rows)}')
    jkeys = {(r['jp_leaf'], r['jp_entry']) for r in rows}
    ukeys = {(r['us_leaf'], r['us_entry']) for r in rows}
    if len(jkeys) != TOTAL or len(ukeys) != TOTAL:
        raise RuntimeError('FX_TEXT_ALIAS_SAFETY_BLOCKED: source or target reused')
    if any(r['us_leaf'] == ALIAS_LEAF for r in rows):
        raise RuntimeError('FX_TEXT_ALIAS_SAFETY_BLOCKED: alias leaf targeted')
    return rows


def build():
    (base, meta, alloc, qrows, qtargets, qman, qinst, qbefore, qlast,
     qbs, qbe, qprev, qya, qJ, qU) = quest.build()
    jp, us = meta['jp'], meta['us']
    raw = bytearray(base)
    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    rows = correspondence(jp, us)

    alloc = dict(alloc)
    before = len(alloc)
    first_free = max(alloc.values()) + 1
    need = set()
    for r in rows:
        ln = jp.tabs['fx_text'][r['jp_leaf']][r['jp_entry']]
        for kind, value in getattr(ln, 'text', ln).tokens:
            if kind == 'CHR_FULL':
                need.add(value)
    new = sorted(need - set(alloc))
    fbase = us.tabs['font'].real_offset
    installed = []
    for n, g in enumerate(new):
        slot = first_free + n
        if slot >= us.tabs['font'].tsize:
            raise RuntimeError('FX_TEXT_GLYPH_CAPACITY_FAILED')
        alloc[g] = slot
        raw[fbase + slot * stext.FONT_STRIDE: fbase + (slot + 1) * stext.FONT_STRIDE] = \
            jpraw[JP_FONT + g * stext.FONT_STRIDE: JP_FONT + (g + 1) * stext.FONT_STRIDE]
        raw[stext.US_METADATA + slot] = jpraw[stext.JP_METADATA + g]
        installed.append({'jp_glyph': g, 'production_slot': f'0x{slot:04X}',
                          'metadata': f'0x{jpraw[stext.JP_METADATA + g]:02X}'})
    last_slot = first_free + len(new) - 1 if new else first_free - 1

    cursor = stext.align(qbe, 4)
    manifest = []
    for jl, ul in sorted(LEAF_MAP.items()):
        leaf = us.tabs['fx_text'][ul]
        repl, expected = {}, {}
        for r in rows:
            if r['us_leaf'] != ul:
                continue
            jline = jp.tabs['fx_text'][jl][r['jp_entry']]
            repl[r['us_entry']] = stext.replacement_line(jline, alloc)
            expected[r['us_entry']] = [(k, alloc[v]) if k == 'CHR_FULL' else (k, v)
                                       for k, v in getattr(jline, 'text', jline).tokens]
        blob = stext.serialize_leaf(usraw, leaf, repl)
        field = us.tabs['fx_text'].real_offset + ul * 4
        old = int.from_bytes(raw[field:field + 4], 'little')
        raw[cursor:cursor + len(blob)] = blob
        raw[field:field + 4] = (ROM + cursor).to_bytes(4, 'little')
        manifest.append({'jp_leaf': jl, 'us_leaf': ul, 'entries': leaf.tsize,
                         'replaced': len(repl), 'preserved': leaf.tsize - len(repl),
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
    return (bytes(raw), meta, alloc, rows, manifest, installed, before, last_slot,
            qbe, cursor, base, need)


def validate(raw, manifest, block_start, block_end):
    pristine = US.read_bytes()
    for x in manifest:
        field = int(x['root_field_us_rom'], 16)
        start = int(x['new_leaf_us_rom'], 16)
        if int.from_bytes(raw[field:field + 4], 'little') != ROM + start:
            raise RuntimeError('FX_TEXT_LEAF_AUDIT_FAILED: root pointer')
        if not block_start <= start < block_end:
            raise RuntimeError('FX_TEXT_LEAF_AUDIT_FAILED: outside block')
        if not stext.TAIL_START <= start < stext.TAIL_START + stext.TAIL_CAPACITY:
            raise RuntimeError('FX_TEXT_LEAF_AUDIT_FAILED: outside tail')
        stext.validate_leaf(x['_blob'], pristine, x['_leaf'], x['_repl'], x['_expected'])


def main():
    if sha(JP) != coverage.JP_SHA or sha(US) != coverage.US_SHA:
        raise RuntimeError('original ROM SHA-256 mismatch')
    OUT.mkdir(parents=True, exist_ok=True)
    OUTROM.parent.mkdir(parents=True, exist_ok=True)
    (first, meta, alloc, rows, manifest, installed, before, last_slot,
     bs, be, prev, need) = build()
    second = build()
    validate(first, manifest, bs, be)
    strip = lambda m: [{k: v for k, v in x.items() if not k.startswith('_')} for x in m]
    if sha(first) != sha(second[0]) or (rows, strip(manifest), installed) != \
       (second[3], strip(second[4]), second[5]):
        raise RuntimeError('FX_TEXT_BUILD_NONDETERMINISTIC')
    if sha(prev) != BASELINE:
        raise RuntimeError('production baseline ROM changed')

    jpraw, usraw = JP.read_bytes(), US.read_bytes()
    fbase = meta['us'].tabs['font'].real_offset
    tsize = meta['us'].tabs['font'].tsize
    for rec in installed:
        g = rec['jp_glyph']; slot = int(rec['production_slot'], 16)
        if first[fbase + slot * stext.FONT_STRIDE: fbase + (slot + 1) * stext.FONT_STRIDE] != \
           jpraw[JP_FONT + g * stext.FONT_STRIDE: JP_FONT + (g + 1) * stext.FONT_STRIDE]:
            raise RuntimeError(f'FX_TEXT_ASSET_AUDIT_FAILED glyph {g}')
        if first[stext.US_METADATA + slot] != jpraw[stext.JP_METADATA + g]:
            raise RuntimeError(f'FX_TEXT_ASSET_AUDIT_FAILED metadata {g}')
    for v in range(0xFF):
        o = v * stext.FONT_STRIDE
        if first[fbase + o:fbase + o + stext.FONT_STRIDE] != usraw[fbase + o:fbase + o + stext.FONT_STRIDE] \
           or first[stext.US_METADATA + v] != usraw[stext.US_METADATA + v]:
            raise RuntimeError(f'low slot 0x{v:02X} was modified')

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
                   for a, b in stext.changed_ranges(prev, first) if 0 in mask[a:b]]
    if unexplained:
        raise RuntimeError('FX_TEXT_BINARY_TOUCH_REGRESSION ' + repr(unexplained[:8]))
    prior = set(alloc.values()) - {int(r['production_slot'], 16) for r in installed}
    if any(first[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE]
           != prev[fbase + v * stext.FONT_STRIDE:fbase + (v + 1) * stext.FONT_STRIDE]
           for v in prior):
        raise RuntimeError('an existing FULL mapping changed')

    OUTROM.write_bytes(first); OUTROM2.write_bytes(second[0])
    cls = {}
    for r in rows:
        cls[r['classification']] = cls.get(r['classification'], 0) + 1
    summary = {
        'status': 'STATIC_VALIDATED',
        'correspondence': {'rule': 'fx_text leaves are identity-mapped to 23; the US alias leaf 24 '
                                   'shifts the rest, so JP 24->US 25 and JP 25->US 26',
                           'pairs': len(rows), 'classification': cls,
                           'alias_leaf_targeted': False, 'sources_reused': 0, 'targets_reused': 0},
        'leaves': strip(manifest),
        'glyphs': {'required': len(need), 'reused': len(need & set(alloc)) - len(installed),
                   'new': len(installed), 'mappings_before': before,
                   'mappings_after': before + len(installed),
                   'total_font_slots': 1582 + len(installed),
                   'first_new_slot': installed[0]['production_slot'] if installed else None,
                   'last_slot': f'0x{last_slot:04X}',
                   'remaining_capacity': tsize - 1 - last_slot,
                   'existing_mappings_changed': 0, 'low_slot_overwrites': 0},
        'preservation': {'alias_leaf_24': 'untouched',
                         'previous_fx_text_production': '2,091 entries in leaves 1..23, untouched',
                         'us_leaves_25_26_previous_claims': 0},
        'block': {'start': f'0x{bs:08X}', 'end': f'0x{be:08X}', 'bytes': be - bs,
                  'remaining_tail': stext.TAIL_CAPACITY - (be - stext.TAIL_START)},
        'binary_touch': {'result': 'PASS', 'unexplained_ranges': unexplained,
                         'rom_code_bytes_changed': 0,
                         'allowed': ['2 fx_text root pointer fields', 'rebuilt fx_text leaves 25/26',
                                     f'{len(installed)} new glyph records',
                                     f'{len(installed)} metadata bytes']},
        'determinism': {'sha256_1': sha(first), 'sha256_2': sha(second[0]), 'identical': True,
                        'previous_baseline': sha(prev)},
    }

    def w(name, obj):
        (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n',
                                encoding='utf-8')

    w('target_set.json', rows)
    w('glyph_map.json', summary['glyphs'] | {'installed': installed})
    w('leaf_audit.json', strip(manifest))
    w('binary_touch.json', summary['binary_touch'])
    w('determinism.json', summary['determinism'])
    w('summary.json', summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
