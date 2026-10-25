"""Read-only device audit plus preview generation against actual missing CJK glyphs."""
import contextlib
import hashlib
import json
import pathlib
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

BASE='http://127.0.0.1:8080'


def request(path,data=None):
    req=urllib.request.Request(BASE+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:return response.read()


def api(path,data=None):return json.loads(request(path,data))


before=api('/api/state')
deadline=time.monotonic()+240
while True:
    status=api('/api/font-status')
    if not status['running']:
        if status['error']:raise RuntimeError(status['error'])
        break
    if time.monotonic()>deadline:raise RuntimeError('Font ranking rebuild did not finish')
    time.sleep(4)

release=pathlib.Path.cwd();sys.path.insert(0,str(release))
from backend.gfont import GFont
config=json.loads((release/'deploy/services.json').read_text(encoding='utf-8-sig'))
storage=pathlib.Path(r'C:/Users/102/plotter-web-20261014/backend-jobs/.font-analysis')
manifest=json.loads((storage/'current.json').read_text(encoding='utf8'))
report=json.loads((storage/manifest['generation']/'font_similarity.json').read_text(encoding='utf8'))
assert report['version']>=2
fonts={f['id']:f for f in report['fonts']}
source_ids=[next(f['id'] for f in fonts.values() if f['name'].startswith(prefix)) for prefix in ('blood8真迹4_','blood8真迹5_')]
fontdir=pathlib.Path(config['FontDir'])
with contextlib.ExitStack() as stack:
    opened={}
    def get(key):
        if key not in opened:opened[key]=stack.enter_context(GFont(fontdir/fonts[key]['file']))
        return opened[key]
    source_chars=set.union(*(set(get(key).index) for key in source_ids))
    ranked=sorted((distance,-weight,key,target,score) for key,weight in zip(source_ids,(70,30))
                  for target,distance,score in fonts[key]['fallback_rankings'] if target not in source_ids)
    test=None
    for distance,weight,reference,target,score in ranked:
        alternatives=sorted(c for c in get(target).index if 0x4e00<=c<=0x9fff and c not in source_chars)
        for code in alternatives:
            if any(len(s)>=2 for s in get(target).glyph(code).strokes()):
                test=(chr(code),target,reference,score);break
        if test:break
    assert test,'No testable missing glyph found'
    char,target,reference,score=test
    for mode in ('notebook','grid'):
        payload=dict(layout_mode=mode,font_mix=[dict(font_id=key,weight=weight) for key,weight in zip(source_ids,(70,30))],
                     text='本'+char+'字'+char,width=100,height=100,margin=10,size=5,rows=4,seed='online-fallback-audit')
        preview=api('/api/preview',payload);page=preview['pages'][0];m=page['metadata']
        assert m['external_fallback_characters']==2,m
        assert all(f['font_id']==target and f['reference_font_id']==reference and f['character']==char for f in m['glyph_fallbacks'])
        nodes=[n for n in ET.fromstring(page['svg']).iter() if n.get('data-fallback')=='true']
        assert nodes and all(n.get('data-character')==char and n.get('stroke')=='#c25b13' for n in nodes)
        code=request('/api/jobs/'+page['job_id']+'/gcode')
        assert hashlib.sha256(code).hexdigest()==m['sha256']
    # Leave a real notebook preview available for optional browser inspection.
    payload['layout_mode']='notebook';preview=api('/api/preview',payload)
after=api('/api/state')
assert before['job_id']==after['job_id'] and after['acknowledged']>=before['acknowledged'] and not after['error']
result=dict(font_count=len(fonts),character=char,target=fonts[target]['name'],reference=fonts[reference]['name'],score=score,
            notebook=True,grid=True,highlight=True,download_hash=True,controller_unchanged=True,
            job_id=after['job_id'],acknowledged_before=before['acknowledged'],acknowledged_after=after['acknowledged'],phase=after['phase'],
            payload=payload,preview=preview)
out=pathlib.Path(r'C:/Users/102/plotter-web-20261014/deploy/glyph-fallback-verification.json')
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in result.items() if k not in ('payload','preview')},ensure_ascii=True),flush=True)
