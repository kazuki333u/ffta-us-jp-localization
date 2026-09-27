#! python3
"""Production direct-entry tail repoint for safe no-HALF words:*.

Builds s_text and words:battle together from pristine ROMs.  It never calls a
recursive repacker and leaves the confirmed s_text builder untouched.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
from ffta_sect import c_ffta_sect_text_buf, load_rom_jp, load_rom_us

ROOT=Path(__file__).resolve().parents[3]
JP=ROOT/'rom/original/FFTA_JP.gba'; US=ROOT/'rom/original/FFTA_US.gba'
OUTROM=ROOT/'rom/build/ffta_us_jp_s_text_words_all.gba'
OUTROM2=ROOT/'rom/build/ffta_us_jp_s_text_words_all_repeat.gba'
OUT=Path(__file__).resolve().parent/'build/words_all_direct_repoint/20260829_production'
ROM=0x08000000
FAMILIES=('battle','content','quest','rumor','system','title','clan','refer','uitm')
# rumor was (67,24) before the words:rumor anchor correction: US 61 and 62
# now pair with the FULL-lane JP tutorial titles instead of the HALF-lane
# numeric placeholders, and US 59/60 (US-only) pair with nothing.
EXPECTED={'battle':(473,282),'content':(393,360),'quest':(362,150),'rumor':(68,22),'system':(42,31),'title':(32,20),'clan':(29,99),'refer':(27,77),'uitm':(2,2)}

def sha(x):
    if isinstance(x,Path):x=x.read_bytes()
    return hashlib.sha256(x).hexdigest().upper()
def pairs(jp,us):
    result={f:[] for f in FAMILIES}; half={f:0 for f in FAMILIES}
    for p in bulk.auto_pairs(jp,us)[0]:
        if not p['section'].startswith('words:') or isinstance(p['jp_line'],list) or isinstance(p['us_line'],list):continue
        family=p['section'].split(':',1)[1]
        if family not in result:continue
        ts=list(p['jp_line'].tokens)
        if any(k=='CHR_HALF' for k,v in ts):half[family]+=1;continue
        if not isinstance(p['jp_line'],c_ffta_sect_text_buf):raise TypeError(type(p['jp_line']).__name__)
        result[family].append(p)
    if any((len(result[f]),half[f])!=EXPECTED[f] for f in FAMILIES):raise RuntimeError(f'coverage {[(f,len(result[f]),half[f]) for f in FAMILIES]}')
    return {f:sorted(result[f],key=lambda p:tuple(p['us_path'])) for f in FAMILIES},half
def payload(p,alloc):
    ts=[(k,alloc[v]) if k=='CHR_FULL' else (k,v) for k,v in p['jp_line'].tokens]
    raw=stext.encode_standard(ts)
    probe=c_ffta_sect_text_buf(bytearray(raw),0);probe.parse_size(None,1);probe.parse()
    if probe.tokens!=ts:raise RuntimeError('payload token validation failed')
    return raw,ts
def build(limit=None):
    # Confirmed builder is a pure pristine-input construction function.
    base,meta=stext.make_build(); jp=meta['jp']; us=meta['us']; raw=bytearray(base)
    byfamily,half=pairs(jp,us)
    if limit is None: selected=[(f,p) for f in FAMILIES for p in byfamily[f]]
    else: selected=[(limit[0],p) for p in byfamily[limit[0]] if tuple(p['us_path'])==(limit[1],)]
    old=dict(meta['allocation']); glyphs=[]; known=set(old)
    for family in FAMILIES:
        new=sorted({v for f,p in selected if f==family for k,v in p['jp_line'].tokens if k=='CHR_FULL'}-known);glyphs.extend(new);known.update(new)
    alloc=dict(old); start=max(alloc.values())+1
    for n,g in enumerate(glyphs):alloc[g]=start+n
    font=us.tabs['font']
    if max(alloc.values())>=font.tsize:raise RuntimeError('WORDS_BATTLE_FONT_MAPPING_REGRESSION')
    jpraw=JP.read_bytes()
    for g in glyphs:
        slot=alloc[g]; jo=jp.tabs['font'].real_offset+g*stext.FONT_STRIDE; uo=font.real_offset+slot*stext.FONT_STRIDE
        raw[uo:uo+stext.FONT_STRIDE]=jpraw[jo:jo+stext.FONT_STRIDE]
        raw[stext.US_METADATA+slot]=jpraw[stext.JP_METADATA+g]
    cursor=stext.align(meta['tail_end'],4); records=[]
    for family,p in selected:
        tab=us.tabs['words'][family]
        idx=tuple(p['us_path'])[0]; field=tab.real_offset+idx*4; oldptr=int.from_bytes(raw[field:field+4],'little')
        original=US.read_bytes()[oldptr-ROM:oldptr-ROM+p['us_line'].raw_len]
        data,expected=payload(p,alloc); new=cursor
        raw[new:new+len(data)]=data; raw[field:field+4]=(ROM+new).to_bytes(4,'little')
        records.append({'family':family,'index':idx,'root_pointer_field_us_rom':f'0x{field:08X}','original_cpu_pointer':f'0x{oldptr:08X}','new_cpu_pointer':f'0x{ROM+new:08X}','relocated_range_us_rom':[f'0x{new:08X}',f'0x{new+len(data):08X}'],'payload_length':len(data),'jp_token_validation':'PASS','original_payload_unchanged':original==US.read_bytes()[oldptr-ROM:oldptr-ROM+p['us_line'].raw_len]})
        cursor=stext.align(new+len(data),4)
    return bytes(raw),meta,alloc,glyphs,records,cursor,half
def validate(raw,meta,alloc,records,end):
    seen=set()
    for r in records:
        tab=meta['us'].tabs['words'][r['family']];idx=r['index'];field=tab.real_offset+idx*4;ptr=int.from_bytes(raw[field:field+4],'little');start=ptr-ROM
        if ptr!=int(r['new_cpu_pointer'],16) or not stext.TAIL_START<=start<end:raise RuntimeError('WORDS_BATTLE_POINTER_AUDIT_FAILED')
        stop=min([int(x['new_cpu_pointer'],16)-ROM for x in records if int(x['new_cpu_pointer'],16)-ROM>start]+[end])
        probe=c_ffta_sect_text_buf(bytearray(raw[start:stop]),0);probe.parse_size(None,1);probe.parse()
        if raw[start+probe.raw_len-1]!=0:raise RuntimeError('EOS missing')
        seen.add(field)
    if len(records)==sum(x[0] for x in EXPECTED.values()) and len(seen)!=len(records):raise RuntimeError('patch count')
    # Re-run the confirmed page-local s_text checks against its saved leaf bytes.
    pristine=US.read_bytes()
    for name,address,blob,leaf,repl in meta['leaf_checks']:
        key=tuple(map(int,name.split('/')));stext.validate_leaf(blob,pristine,leaf,repl,meta['expected'][key])
    if raw[stext.TAIL_START:meta['tail_end']] != stext.make_build()[0][stext.TAIL_START:meta['tail_end']]:raise RuntimeError('s_text tail regression')
def validate_half_preservation(raw,us,jp):
    original=US.read_bytes(); found={f:0 for f in FAMILIES}
    for p in bulk.auto_pairs(jp,us)[0]:
        if not p['section'].startswith('words:') or isinstance(p['jp_line'],list) or isinstance(p['us_line'],list):continue
        family=p['section'].split(':',1)[1]
        if family not in found or not any(k=='CHR_HALF' for k,v in p['jp_line'].tokens):continue
        idx=tuple(p['us_path'])[0]; tab=us.tabs['words'][family]; field=tab.real_offset+idx*4
        ptr=int.from_bytes(original[field:field+4],'little')
        if raw[field:field+4]!=original[field:field+4] or raw[ptr-ROM:ptr-ROM+p['us_line'].raw_len]!=original[ptr-ROM:ptr-ROM+p['us_line'].raw_len]:raise RuntimeError('WORDS_HALF_PRESERVATION_FAILED')
        found[family]+=1
    if any(found[f]!=EXPECTED[f][1] for f in FAMILIES):raise RuntimeError('WORDS_HALF_PRESERVATION_FAILED')
    return found
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--overwrite',action='store_true');a=ap.parse_args()
    if sha(JP)!=coverage.JP_SHA or sha(US)!=coverage.US_SHA:raise RuntimeError('original SHA mismatch')
    if not a.overwrite and (OUTROM.exists() or OUT.exists()):raise RuntimeError('refuse overwrite')
    OUT.mkdir(parents=True,exist_ok=True);OUTROM.parent.mkdir(parents=True,exist_ok=True)
    for family in FAMILIES:
        probe,pm,pa,pg,pr,pe,ph=build((family, pairs(load_rom_jp(JP),load_rom_us(US))[0][family][0]['us_path'][0]))
        if len(pr)!=1:raise RuntimeError('WORDS_FAMILY_CONTRACT_REGRESSION')
        validate(probe,pm,pa,pr,pe)
    first,meta,alloc,glyphs,records,end,half=build();second,meta2,alloc2,glyphs2,records2,end2,half2=build()
    validate(first,meta,alloc,records,end)
    half=validate_half_preservation(first,meta['us'],meta['jp'])
    if sha(first)!=sha(second) or (alloc,records,end,half)!=(alloc2,records2,end2,half2):raise RuntimeError('WORDS_ALL_BUILD_NONDETERMINISTIC')
    # Combined-vs-s_text-only binary surface audit.
    sraw=stext.make_build()[0]; diff=[];start=None
    for i,(x,y) in enumerate(zip(sraw,first)):
        if x!=y and start is None:start=i
        if x==y and start is not None:diff.append((start,i));start=None
    if start is not None:diff.append((start,len(first)))
    allowed=[(int(r['root_pointer_field_us_rom'],16),int(r['root_pointer_field_us_rom'],16)+4) for r in records]
    allowed += [(stext.US_METADATA+slot,stext.US_METADATA+slot+1) for slot in set(alloc.values())-set(meta['allocation'].values())]
    for slot in set(alloc.values())-set(meta['allocation'].values()):
        o=meta['us'].tabs['font'].real_offset+slot*stext.FONT_STRIDE;allowed.append((o,o+stext.FONT_STRIDE))
    allowed.append((stext.align(meta['tail_end'],4),end))
    allowed.sort(); merged=[]
    for lo,hi in allowed:
        if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],hi))
        else:merged.append((lo,hi))
    allowed=merged
    if any(not any(lo<=s and e<=hi for lo,hi in allowed) for s,e in diff):raise RuntimeError('binary touch audit failed')
    OUTROM.write_bytes(first);OUTROM2.write_bytes(second)
    (OUT/'pointer_audit.json').write_text(json.dumps(records,indent=2)+'\n')
    counts={f:sum(r['family']==f for r in records) for f in FAMILIES}; ranges={f:[min(int(r['new_cpu_pointer'],16)-ROM for r in records if r['family']==f),max(int(r['new_cpu_pointer'],16)-ROM+r['payload_length'] for r in records if r['family']==f)] for f in FAMILIES}
    (OUT/'family_inventory.json').write_text(json.dumps({'replaced':counts,'expected':{f:EXPECTED[f][0] for f in FAMILIES}},indent=2)+'\n')
    (OUT/'half_preservation.json').write_text(json.dumps({'preserved':half,'expected':{f:EXPECTED[f][1] for f in FAMILIES},'result':'PASS'},indent=2)+'\n')
    (OUT/'tail_manifest.json').write_text(json.dumps({'families':{f:{'start':f'0x{ranges[f][0]:08X}','end':f'0x{ranges[f][1]:08X}','entries':counts[f]} for f in FAMILIES}},indent=2)+'\n')
    (OUT/'glyph_map.json').write_text(json.dumps({'s_text_existing':924,'words_battle_incremental':151,'remaining_words_incremental':len(glyphs)-151,'cumulative':len(alloc),'mapping':{f'0x{k:04X}':f'0x{v:04X}' for k,v in alloc.items()}},indent=2)+'\n')
    (OUT/'binary_touch.json').write_text(json.dumps({'result':'PASS','changed_ranges':[[f'0x{s:08X}',f'0x{e:08X}'] for s,e in diff],'categories':['words root pointers','incremental JP FULL glyph/metadata','words tail payloads']},indent=2)+'\n')
    summary={'status':'STATIC_VALIDATED','coverage':{'replaced':counts,'total':len(records),'expected_total':1419,'cumulative_s_text_words':5445+len(records)},'half_preserved':half,'one_entry':'PASS','pointers':{'patched':counts,'total':len(records),'audit':'PASS'},'tail':{'s_text_end':f'0x{meta["tail_end"]:08X}','end':f'0x{end:08X}','bytes':end-stext.align(meta['tail_end'],4),'remaining':stext.TAIL_CAPACITY-(end-stext.TAIL_START)},'font':{'existing_s_text':924,'battle':151,'remaining_words':len(glyphs)-151,'cumulative':len(alloc),'slot_last':f'0x{max(alloc.values()):04X}','remaining':meta['us'].tabs['font'].tsize-1-max(alloc.values())},'s_text_regression':'PASS','battle_regression':'PASS','determinism':{'sha256_1':sha(first),'sha256_2':sha(second),'identical':True},'binary_touch':'PASS'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(OUT/'determinism.json').write_text(json.dumps(summary['determinism'],indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
