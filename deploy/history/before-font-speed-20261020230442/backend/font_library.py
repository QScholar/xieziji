"""Actual vector font samples and a precomputed, measured similarity catalog."""
import collections
import json
import math
import pathlib
import threading

from backend.gfont import GFont

SAMPLE_TEXT='春风十里山水清音'


def vector_sample(path, text):
    paths=[];missing=[];boxes=[];cursor=5.;points=0
    with GFont(path) as font:
        for char in text:
            if char.isspace():cursor+=8;continue
            if ord(char) not in font.index:
                missing.append(char);boxes.append(cursor);cursor+=17;continue
            strokes=font.text(char,height=16,spacing=0)
            points+=sum(len(s) for s in strokes)
            if points>20000:raise ValueError('预览字形过于复杂，请减少预览文字')
            ink=[p for s in strokes for p in s]
            if ink:
                width=max(x for x,y in ink)
                paths.extend([[(x+cursor,y) for x,y in s] for s in strokes])
                cursor+=max(width,6)+3
            else:cursor+=9
    ink=[p for stroke in paths for p in stroke]
    top=min([0.]+[y for x,y in ink])-5
    bottom=max([16.]+[y for x,y in ink])+5
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {top:.3f} {cursor+3:.3f} {bottom-top:.3f}" role="img" aria-label="真实字体笔画预览">',
         '<g fill="none" stroke="#284b4a" stroke-width="0.5" stroke-linecap="round" stroke-linejoin="round">']
    for stroke in paths:
        svg.append('<polyline points="'+' '.join(f'{x:.3f},{y:.3f}' for x,y in stroke)+'"/>')
    svg.append('</g><g fill="none" stroke="#abb6b0" stroke-width=".5" stroke-dasharray="2 2">')
    for x in boxes:svg.append(f'<rect x="{x:.3f}" y="1" width="13" height="15"/>')
    svg.append('</g></svg>')
    return dict(svg=''.join(svg),missing=list(dict.fromkeys(missing)),text=text)


class FontLibrary:
    def __init__(self, fonts, report=None):
        self.fonts=fonts
        self.lock=threading.Lock()
        self.samples=collections.OrderedDict()
        self.report_path=pathlib.Path(report) if report else pathlib.Path(__file__).with_name('font_similarity.json')

    def previews(self, data):
        ids=data.get('font_ids')
        text=data.get('text',SAMPLE_TEXT)
        if (not isinstance(ids,list) or not 1<=len(ids)<=12 or any(not isinstance(i,str) or i not in self.fonts for i in ids)
                or len(set(ids))!=len(ids)):
            raise ValueError('请选择 1–12 个可用字体进行预览')
        if not isinstance(text,str) or not 1<=len(text)<=24 or not text.strip() or not text.isprintable():
            raise ValueError('预览文字须为 1–24 个可打印字符，不能包含换行')
        results=[]
        for font_id in ids:
            path=self.fonts[font_id]
            stat=path.stat()
            key=(font_id,text,stat.st_size,stat.st_mtime_ns)
            with self.lock:
                cached=self.samples.get(key)
                if cached:self.samples.move_to_end(key)
            if cached is None:
                try:cached=vector_sample(path,text)
                except (ValueError,KeyError,OSError) as error:cached=dict(error=str(error),text=text)
                with self.lock:
                    self.samples[key]=cached
                    while len(self.samples)>192:self.samples.popitem(last=False)
            results.append(dict(font_id=font_id,**cached))
        return dict(previews=results)

    def recommendations(self):
        try:data=json.loads(self.report_path.read_text(encoding='utf8'))
        except (OSError,ValueError):
            return dict(recommendations=[],summary=dict(font_count=len(self.fonts),analyzed_fonts=0),
                        note='当前字库尚无笔画分析结果，仍可预览和手动混选。')
        verified={}
        for info in data.get('fonts',[]):
            path=self.fonts.get(info.get('id'))
            if path and path.name==info.get('file') and path.stat().st_size==info.get('bytes'):
                verified[info['id']]=info
        groups=[g for g in data.get('recommendations',[]) if all(f['font_id'] in verified for f in g['mix'])]
        details={key:dict(duplicate_of=info.get('duplicate_of'),nearest=[p for p in info.get('nearest',[]) if p['font_id'] in verified],
                          glyph_count=info['glyph_count']) for key,info in verified.items()}
        return dict(recommendations=groups,fonts=details,summary=dict(font_count=len(self.fonts),analyzed_fonts=len(verified),
            sample_characters=len(data.get('sample_text','')),reference_family=data.get('reference_family'),
            duplicate_pairs=sum(p['a'] in verified and p['b'] in verified for p in data.get('duplicate_pairs',[]))),
            note='按实际笔画抽样比较，评分不是同作者概率；高度重复字形不作为混写变化来源。')
