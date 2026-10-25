"""Request-local nearest-font lookup; paths carry preview-only provenance."""
import html
import json
import zipfile


class Stroke(list):
    def __init__(self, points, fallback=None, glyph=None):
        super().__init__(points)
        self.fallback=fallback
        self.glyph=glyph


def moved(stroke, points):
    fallback, glyph = getattr(stroke,'fallback',None), getattr(stroke,'glyph',None)
    return Stroke(points,fallback,glyph) if fallback or glyph else list(points)


def fallback_metadata(paths):
    seen=set();result=[]
    for stroke in paths:
        info=getattr(stroke,'fallback',None)
        if info and info['occurrence'] not in seen:
            seen.add(info['occurrence']);result.append(dict(info))
    return result


def preview_polyline(stroke):
    info=getattr(stroke,'fallback',None)
    glyph=getattr(stroke,'glyph',None)
    attributes=''
    title=''
    if info:
        attributes=' class="fallback-glyph" data-fallback="true" data-character="'+html.escape(info['character'],quote=True)+'"'
        title='<title>'+html.escape(info['character']+'：'+info['reference_font']+' → '+info['font']+f"（相似评分 {info['score']}）")+'</title>'
    if glyph:
        attributes+=' data-glyph="'+html.escape(glyph['character'],quote=True)+'" data-font-id="'+html.escape(glyph['font_id'],quote=True)+'" data-occurrence="'+html.escape(glyph['occurrence'],quote=True)+'"'
        if not title:
            title='<title>'+html.escape(glyph['character']+'：'+glyph['font'])+'</title>'
    return '<polyline'+attributes+' points="'+' '.join(f'{x},{y}' for x,y in stroke)+'" fill="none" stroke="'+('#c25b13' if info else '#243e42')+'" stroke-width=".22" stroke-linecap="round" stroke-linejoin="round">'+title+'</polyline>'


class GlyphFallback:
    def __init__(self, library, choices, stack, loader):
        self.library=library;self.stack=stack;self.loader=loader
        self.excluded={c['font_id'] for c in choices}
        self.choices=[c for c in choices if c['weight']>0]
        self.opened={};self.cache={}
        try:data=json.loads(library.report_path.read_text(encoding='utf8'))
        except (OSError,ValueError):data={}
        self.verified={f['id']:f for f in data.get('fonts',[]) if library.matches(f)}
        candidates=[]
        for choice in self.choices:
            for target,distance,score in self.verified.get(choice['font_id'],{}).get('fallback_rankings',[]):
                if target in self.verified and target not in self.excluded:
                    candidates.append((distance,-choice['weight'],choice['font_id'],target,score))
        self.candidates=sorted(candidates)

    def find(self,char,reference_ids=None):
        allowed=set(reference_ids) if reference_ids is not None else {c['font_id'] for c in self.choices}
        key=(char,tuple(sorted(allowed)))
        if key in self.cache:return self.cache[key]
        checked=set()
        for distance,weight,reference,target,score in self.candidates:
            if reference not in allowed:continue
            if target in checked:continue
            checked.add(target)
            try:
                if target not in self.opened:
                    self.opened[target]=self.stack.enter_context(self.loader(self.library.fonts[target]))
                font=self.opened[target]
                if ord(char) not in font.index:continue
                if not any(len(s)>=2 for s in font.glyph(char).strokes()):continue
                info=dict(character=char,font_id=target,font=self.library.fonts[target].name,
                          reference_font_id=reference,reference_font=self.library.fonts[reference].name,score=score)
                self.cache[key]=(font,info)
                return self.cache[key]
            except (OSError,ValueError,KeyError,zipfile.BadZipFile):continue
        self.cache[key]=None
        return None
