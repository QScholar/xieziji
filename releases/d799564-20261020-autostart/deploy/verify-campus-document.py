import json
import urllib.request

opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def call(path,data=None):
    body=None if data is None else json.dumps(data,ensure_ascii=False).encode('utf8')
    request=urllib.request.Request('http://127.0.0.1:8080'+path,data=body,
        headers={} if data is None else {'Content-Type':'application/json'})
    with opener.open(request,timeout=15) as response:
        return json.load(response)

fonts=call('/api/fonts')['fonts']
font=next(f for f in fonts if f['name']=='blood8-2_kvenjoy_blood8')
document=call('/api/preview',dict(layout_mode='notebook',font_mix=[dict(font_id=font['id'],weight=100)],
    pagination='auto',seed='startup-acceptance',pages=[dict(text='你好世界。Hello world!'),dict(text=r'中文与英文可以一起排版。 $x^2+\frac{1}{2}$')]))
assert document['page_count']==2
assert document['pages'][1]['metadata']['formula_count']==1
grid=call('/api/coordinate-preview',dict(layout_mode='grid',width=157,height=215,margin=0,rows=20,
    grid=dict(columns=20,row_gap=2.9,column_gap=0)))
with opener.open('http://127.0.0.1:8080/api/documents/'+document['document_id']+'/archive',timeout=15) as response:
    archive=response.read();assert archive.startswith(b'PK')
print(json.dumps(dict(pages=document['page_count'],formulas=document['pages'][1]['metadata']['formula_count'],
    cell_width=grid['metadata']['grid']['cell_width'],cell_height=grid['metadata']['grid']['cell_height'],zip_bytes=len(archive)),ensure_ascii=True))
