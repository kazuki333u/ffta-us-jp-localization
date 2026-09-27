#! python3
"""Final safe no-HALF page-leaf production builder; no recursive repacker."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
import ffta_jp_bulk_import_poc as bulk
import ffta_jp_coverage_audit as coverage
import ffta_jp_s_text_leaf_repoint as stext
import ffta_jp_words_direct_repoint as words

ROOT=Path(__file__).resolve().parents[3];JP=ROOT/'rom/original/FFTA_JP.gba';US=ROOT/'rom/original/FFTA_US.gba'
OUTROM=ROOT/'rom/build/ffta_us_jp_all_safe_no_half.gba';OUT2=ROOT/'rom/build/ffta_us_jp_all_safe_no_half_repeat.gba'
OUT=Path(__file__).resolve().parent/'build/all_safe_no_half_production/20260829_production';ROM=0x8000000
FAMS=('fx_text','pages:battle','pages:choice','pages:condi','pages:config','pages:quest/')
EXPECT={'fx_text':2091,'pages:battle':390,'pages:choice':50,'pages:condi':20,'pages:config':10,'pages:quest/':2}
ROOTS={'pages:battle':0x237f4,'pages:choice':0x3f6b8,'pages:condi':0x13d98,'pages:config':0x94b8,'pages:quest/0':0x13cd8,'pages:quest/1':0x13cc0}
def sha(x):return hashlib.sha256(x.read_bytes() if isinstance(x,Path) else x).hexdigest().upper()
def get_remaining(jp,us):
 d={f:[] for f in FAMS}
 for p in bulk.auto_pairs(jp,us)[0]:
  if p['section'] not in d or isinstance(p['jp_line'],list) or isinstance(p['us_line'],list):continue
  if any(k=='CHR_HALF' for k,v in p['jp_line'].text.tokens):continue
  d[p['section']].append(p)
 if {f:len(d[f]) for f in FAMS}!=EXPECT:raise RuntimeError('SAFE_SET_ACCOUNTING_REGRESSION')
 return {f:sorted(d[f],key=lambda p:(p['us_table'],tuple(p['us_path']))) for f in FAMS}
def leaves(us, selected):
 out={}
 for fam,ps in selected.items():
  for p in ps:
   if fam=='fx_text': key=(fam,p['us_path'][0]);leaf=us.tabs['fx_text'][p['us_path'][0]];field=us.tabs['fx_text'].real_offset+p['us_path'][0]*4
   else:
    name=p['us_table']; page_key=name.split(':',1)[1]; leaf=us.tabs['pages'][page_key]
    key=(fam,name);field=ROOTS[name]
   out.setdefault(key,{'leaf':leaf,'field':field,'items':[]})['items'].append(p)
 return out
def build():
 base,meta,alloc,wordglyphs,wordrecords,wordend,half=words.build();jp=meta['jp'];us=meta['us'];raw=bytearray(base);sel=get_remaining(jp,us);objs=leaves(us,sel)
 # Preserve all established mappings; allocate each newly required JP glyph once.
 new=sorted({v for ps in sel.values() for p in ps for k,v in p['jp_line'].text.tokens if k=='CHR_FULL'}-set(alloc));alloc=dict(alloc)
 for n,g in enumerate(new):alloc[g]=max(alloc.values())+1
 if max(alloc.values())>=us.tabs['font'].tsize:raise RuntimeError('SAFE_GLYPH_MAPPING_REGRESSION')
 jpraw=JP.read_bytes()
 for g in new:
  slot=alloc[g];jo=jp.tabs['font'].real_offset+g*stext.FONT_STRIDE;uo=us.tabs['font'].real_offset+slot*stext.FONT_STRIDE
  raw[uo:uo+stext.FONT_STRIDE]=jpraw[jo:jo+stext.FONT_STRIDE];raw[stext.US_METADATA+slot]=jpraw[stext.JP_METADATA+g]
 cursor=stext.align(wordend,4);manifest=[]
 for key,obj in sorted(objs.items(),key=lambda x:(FAMS.index(x[0][0]),str(x[0][1]))):
  repl={};expected={}
  for p in obj['items']:
   idx=p['us_path'][-1];repl[idx]=stext.replacement_line(p['jp_line'],alloc);expected[idx]=[(k,alloc[v]) if k=='CHR_FULL' else (k,v) for k,v in p['jp_line'].text.tokens]
  blob=stext.serialize_leaf(US.read_bytes(),obj['leaf'],repl);field=obj['field'];old=int.from_bytes(raw[field:field+4],'little');raw[cursor:cursor+len(blob)]=blob;raw[field:field+4]=(ROM+cursor).to_bytes(4,'little')
  manifest.append({'family':key[0],'root_path':str(key[1]),'root_field_us_rom':f'0x{field:08X}','original_leaf_us_rom':f'0x{obj["leaf"].real_offset:08X}','original_size':obj['leaf'].sect_top,'new_leaf_us_rom':f'0x{cursor:08X}','new_size':len(blob),'localized_entries':len(repl),'old_cpu_pointer':f'0x{old:08X}','new_cpu_pointer':f'0x{ROM+cursor:08X}','expected':expected,'leaf':obj['leaf'],'blob':blob,'repl':repl})
  cursor=stext.align(cursor+len(blob),4)
 return bytes(raw),meta,alloc,new,wordend,cursor,manifest
def validate(raw,meta,manifest,end):
 pristine=US.read_bytes()
 for x in manifest:
  field=int(x['root_field_us_rom'],16);start=int(x['new_leaf_us_rom'],16);ptr=int.from_bytes(raw[field:field+4],'little')
  if ptr!=ROM+start or not stext.TAIL_START<=start<end:raise RuntimeError('REMAINING_ROOT_POINTER_AUDIT_FAILED')
  stext.validate_leaf(x['blob'],pristine,x['leaf'],x['repl'],x['expected'])
def main():
 if sha(JP)!=coverage.JP_SHA or sha(US)!=coverage.US_SHA:raise RuntimeError('original SHA')
 OUT.mkdir(parents=True,exist_ok=True);OUTROM.parent.mkdir(parents=True,exist_ok=True)
 first,meta,alloc,new,wordend,end,man=build();second,meta2,alloc2,new2,wordend2,end2,man2=build();validate(first,meta,man,end)
 # The source-of-truth mapping exposes pages:quest/0 and pages:quest/1 as
 # distinct root-owned pages.  The earlier aggregate estimate counted this as
 # one family leaf; byte-level root inspection proves 27 physical leaves.
 if len(man)!=27 or sum(x['localized_entries'] for x in man)!=2560:raise RuntimeError('REMAINING_PAGE_LEAF_MICRO_CHECK_FAILED')
 if sha(first)!=sha(second) or (alloc,end,[(x['family'],x['new_leaf_us_rom']) for x in man])!=(alloc2,end2,[(x['family'],x['new_leaf_us_rom']) for x in man2]):raise RuntimeError('ALL_SAFE_BUILD_NONDETERMINISTIC')
 # Existing combined production bytes through word tail must be bit-identical.
 previous,pmeta,prior_alloc,_,prior_records,prior_end,_=words.build()
 words.validate(previous,pmeta,prior_alloc,prior_records,prior_end)
 if prior_end!=wordend or any(alloc[g]!=slot for g,slot in prior_alloc.items()):raise RuntimeError('SAFE_GLYPH_MAPPING_REGRESSION')
 # Below the tail this builder is allowed to touch exactly two things: the newly
 # required native JP glyph records/metadata, and the 27 page root pointer
 # fields it repoints.  A byte mask (not range containment) is used because
 # consecutive fx_text root fields merge into a single changed range.
 mask=bytearray(wordend)
 for g in new:
  slot=alloc[g];off=meta['us'].tabs['font'].real_offset+slot*stext.FONT_STRIDE
  mask[off:off+stext.FONT_STRIDE]=b''*stext.FONT_STRIDE;mask[stext.US_METADATA+slot]=1
 for x in man:
  f=int(x['root_field_us_rom'],16);mask[f:f+4]=b''*4
 unexplained=[(f'0x{a:08X}',f'0x{b:08X}') for a,b in stext.changed_ranges(previous[:wordend],first[:wordend]) if 0 in mask[a:b]]
 if unexplained:raise RuntimeError(f'prior production regression {unexplained[:8]}')
 rootpatched=sum(previous[int(x['root_field_us_rom'],16):int(x['root_field_us_rom'],16)+4]!=first[int(x['root_field_us_rom'],16):int(x['root_field_us_rom'],16)+4] for x in man)
 if rootpatched!=27:raise RuntimeError(f'ROOT_POINTER_PATCH_COUNT_MISMATCH {rootpatched}')
 OUTROM.write_bytes(first);OUT2.write_bytes(second)
 clean=[{k:v for k,v in x.items() if k not in ('expected','leaf','blob','repl')} for x in man]
 (OUT/'leaf_manifest.json').write_text(json.dumps(clean,indent=2)+'\n');(OUT/'root_pointer_audit.json').write_text(json.dumps(clean,indent=2)+'\n')
 (OUT/'page_validation.json').write_text(json.dumps([{'family':x['family'],'root_path':x['root_path'],'result':'PASS'} for x in man],indent=2)+'\n')
 (OUT/'glyph_map.json').write_text(json.dumps({'prior':1282,'incremental':len(new),'cumulative':len(alloc),'last_slot':f'0x{max(alloc.values()):04X}','remaining':meta['us'].tabs['font'].tsize-1-max(alloc.values())},indent=2)+'\n')
 summary={'status':'STATIC_VALIDATED','coverage':{'s_text':5445,'words':1419,'remaining':2560,'total':9424},'leaves':{'physical':len(man),'by_family':{f:sum(x['family']==f for x in man) for f in FAMS}},'pointers':{'patched':len(man),'audit':'PASS'},'tail':{'previous_end':f'0x{wordend:08X}','end':f'0x{end:08X}','bytes':end-wordend,'remaining':stext.TAIL_CAPACITY-(end-stext.TAIL_START)},'font':{'prior':1282,'incremental':len(new),'cumulative':len(alloc),'last_slot':f'0x{max(alloc.values()):04X}','remaining':meta['us'].tabs['font'].tsize-1-max(alloc.values())},'regression':{'s_text':'PASS','words':'PASS'},'determinism':{'sha256_1':sha(first),'sha256_2':sha(second),'identical':True}}
 (OUT/'coverage.json').write_text(json.dumps(summary['coverage'],indent=2)+'\n');(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(OUT/'determinism.json').write_text(json.dumps(summary['determinism'],indent=2)+'\n');(OUT/'binary_touch.json').write_text(json.dumps({'result':'PASS','root_pointer_fields_patched':rootpatched,'tail_leaves_added':len(man),'new_glyph_records':len(new),'unexplained_prior_ranges':unexplained,'prior_bytes_stable':not unexplained},indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
