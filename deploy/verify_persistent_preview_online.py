"""Read-only controller checks and preview-only persistence verification."""
import argparse,hashlib,io,json,pathlib,sys,time,urllib.request,zipfile
p=argparse.ArgumentParser();p.add_argument('--release');p.add_argument('--jobs');p.add_argument('--fonts');p.add_argument('--cases');p.add_argument('--report');args=p.parse_args()
sys.path.insert(0,args.release)
base='http://127.0.0.1:8080'
def call(path,data=None):
    body=None if data is None else json.dumps(data,ensure_ascii=False).encode('utf8')
    req=urllib.request.Request(base+path,data=body,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:return response.read()
def api(path,data=None):return json.loads(call(path,data))
sha=lambda data:hashlib.sha256(data).hexdigest()
before=api('/api/state')
cases=json.loads(pathlib.Path(args.cases).read_text(encoding='utf-8-sig'));cases=cases if isinstance(cases,list) else [cases]
recovered=[]
for case in cases:
    gcode=call('/api/jobs/'+case['job_id']+'/gcode')
    assert sha(gcode)==case['sha256'],case['job_id']
    recovered.append(dict(case,status_after=200,hash_matches=True))
font=next(f for f in api('/api/fonts')['fonts'] if f['name']=='blood8真迹4_kvenjoy_blood8')
document=api('/api/preview',dict(font_id=font['id'],layout_mode='grid',width=100,height=100,margin=10,rows=2,grid=dict(columns=5),seed='persistent-online-'+str(int(time.time())),pages=[dict(text='A'),dict(text='B')]))
original=[call('/api/jobs/'+page['job_id']+'/gcode') for page in document['pages']]
for i in range(9):api('/api/preview',dict(font_id=font['id'],text='A'))
for page,gcode in zip(document['pages'],original):assert call('/api/jobs/'+page['job_id']+'/gcode')==gcode
archive=zipfile.ZipFile(io.BytesIO(call('/api/documents/'+document['document_id']+'/archive')))
assert json.loads(archive.read('manifest.json'))['job_ids']==[page['job_id'] for page in document['pages']]
for i,gcode in enumerate(original,1):assert archive.read(f'page-{i:03d}.gcode')==gcode
from backend.server import Application
class NoMachine:
    def __getattr__(self,name):raise AssertionError('Unexpected machine access: '+name)
restored=Application(args.fonts,args.jobs,controller=NoMachine())
assert not restored.cache
reopened=zipfile.ZipFile(io.BytesIO(restored.archive(document['document_id'])))
for name in archive.namelist():assert reopened.read(name)==archive.read(name),name
for case in cases:assert sha(restored.job(case['job_id'])[1]['gcode'].encode('ascii'))==case['sha256']
after=api('/api/state')
assert after['job_id']==before['job_id'] and after['acknowledged']>=before['acknowledged']
report=dict(passed=True,recovered_legacy_pages=recovered,new_document_pages=document['page_count'],eviction_survived=True,fresh_application_archive_identical=True,no_regeneration=True,no_machine_actions=True,job_id=after['job_id'],phase=after['phase'],acknowledged_before=before['acknowledged'],acknowledged_after=after['acknowledged'])
pathlib.Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=False))
