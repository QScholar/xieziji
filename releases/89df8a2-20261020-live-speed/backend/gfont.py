"""Read legacy .gfont vector glyphs without executing the vendor application.

Wrapper versions 1/3/5/6/7 contain a standard ZIP archive. Each glyph holds:
big-endian uint16 character, uint32 coordinate count, float32 coordinates,
uint32 segment count, then byte segment types (Java path codes 0..4).
The wrapper's protected metadata is not needed to read the vector records.
"""
from __future__ import annotations
import argparse
import collections
import dataclasses
import json
import io
import math
import pathlib
import statistics
import struct
import zipfile

ARITY={0:2,1:2,2:4,3:6,4:0}


@dataclasses.dataclass(frozen=True)
class Glyph:
    codepoint:int
    coordinates:tuple[float,...]
    types:tuple[int,...]

    @property
    def character(self): return chr(self.codepoint)

    @property
    def bounds(self):
        if not self.coordinates:return (0.0,0.0,0.0,0.0)
        xs=self.coordinates[::2];ys=self.coordinates[1::2]
        return (min(xs),min(ys),max(xs),max(ys))

    def strokes(self,tolerance=0.5):
        """Keep pen-lift boundaries; flatten curves using an error tolerance."""
        result=[]; current=[]; cursor=(0.0,0.0); start=cursor; offset=0
        def flat_curve(points,depth=0):
            a,b=points[0],points[-1]
            dx,dy=b[0]-a[0],b[1]-a[1]
            denom=math.hypot(dx,dy)
            deviation=max((abs(dx*(p[1]-a[1])-dy*(p[0]-a[0]))/denom if denom else math.dist(a,p)) for p in points[1:-1])
            polygon=sum(math.dist(x,y) for x,y in zip(points,points[1:]))
            if depth>=16 or (deviation<=tolerance and polygon-math.dist(a,b)<=tolerance):return [b]
            levels=[points]
            while len(levels[-1])>1:
                levels.append([((x[0]+y[0])/2,(x[1]+y[1])/2) for x,y in zip(levels[-1],levels[-1][1:])])
            left=[level[0] for level in levels];right=[level[-1] for level in reversed(levels)]
            return flat_curve(left,depth+1)+flat_curve(right,depth+1)
        for kind in self.types:
            count=ARITY[kind];values=self.coordinates[offset:offset+count];offset+=count
            points=list(zip(values[::2],values[1::2]))
            if kind==0:
                if current:result.append(current)
                cursor=points[0];start=cursor;current=[cursor]
            elif kind==1:
                cursor=points[0];current.append(cursor)
            elif kind in (2,3):
                current.extend(flat_curve([cursor]+points));cursor=points[-1]
            else:
                if current and current[-1]!=start:current.append(start)
                cursor=start
        if current:result.append(current)
        return result


def parse_glyph(data:bytes)->Glyph:
    if len(data)<10:raise ValueError('Glyph shorter than its header')
    codepoint,count=struct.unpack_from('>HI',data)
    if count>2_000_000 or count%2:raise ValueError('Invalid coordinate count')
    offset=6+4*count
    if offset+4>len(data):raise ValueError('Coordinate array truncated')
    coords=struct.unpack_from(f'>{count}f',data,6)
    segments=struct.unpack_from('>I',data,offset)[0]
    types=tuple(data[offset+4:])
    if segments!=len(types):raise ValueError('Segment count does not match record length')
    if not all(kind in ARITY for kind in types):raise ValueError('Unknown path segment type')
    if sum(ARITY[kind] for kind in types)!=count:raise ValueError('Segment/coordinate counts disagree')
    if types and types[0]!=0:raise ValueError('Glyph path does not begin with a move')
    if not all(math.isfinite(v) and abs(v)<1e7 for v in coords):raise ValueError('Invalid coordinate')
    return Glyph(codepoint,coords,types)


class GFont:
    def __init__(self,path):
        self.path=pathlib.Path(path)
        with self.path.open('rb') as f:self.version=int.from_bytes(f.read(4),'big')
        if self.version not in {1,3,5,6,7}:raise ValueError(f'Unsupported wrapper version {self.version}')
        self.trailing_padding=0
        self._archive_buffer=None
        try:
            self.archive=zipfile.ZipFile(self.path)
        except zipfile.BadZipFile:
            # One installed font has >400 KiB of zero padding after a valid
            # EOCD. zipfile only searches the last 64 KiB; inspect a bounded
            # end record and trim only an all-zero suffix in memory.
            blob=self.path.read_bytes()
            end=blob.rfind(b'PK\x05\x06')
            if end<0 or end+22>len(blob):raise
            _,disk,central_disk,on_disk,total,size,offset,comment=struct.unpack_from('<4sHHHHIIH',blob,end)
            archive_end=end+22+comment
            if disk or central_disk or on_disk!=total or archive_end>len(blob) or any(blob[archive_end:]):raise
            self.trailing_padding=len(blob)-archive_end
            self._archive_buffer=io.BytesIO(blob[:archive_end])
            self.archive=zipfile.ZipFile(self._archive_buffer)
        self.index={};self.cache={}
        names=[entry.filename for entry in self.archive.infolist() if not entry.is_dir()]
        numeric_names=all(name.isdecimal() for name in names) and self.version!=1
        for entry in self.archive.infolist():
            if entry.is_dir():continue
            name=entry.filename
            # Numeric entry names predominate in newer wrappers; old ones use characters.
            if numeric_names:codepoint=int(name)
            elif len(name)==1:codepoint=ord(name)
            elif name.isdecimal() and 0<=int(name)<=65535:codepoint=int(name)
            else:raise ValueError(f'Unrecognized glyph entry name: {name!r}')
            if codepoint in self.index:raise ValueError(f'Duplicate character U+{codepoint:04X}')
            self.index[codepoint]=entry
        self._metrics=None
        self._cjk_metrics=None

    def close(self):
        self.archive.close()
        if self._archive_buffer is not None:self._archive_buffer.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def glyph(self,character):
        codepoint=ord(character) if isinstance(character,str) else character
        if codepoint not in self.index:raise KeyError(chr(codepoint))
        if codepoint not in self.cache:
            glyph=parse_glyph(self.archive.read(self.index[codepoint]))
            if glyph.codepoint!=codepoint:raise ValueError(f'Character key mismatch U+{codepoint:04X} vs U+{glyph.codepoint:04X}')
            self.cache[codepoint]=glyph
        return self.cache[codepoint]

    @property
    def metrics(self):
        if self._metrics is None:
            latin=[self.glyph(c).bounds for c in 'AEFHIKLMNTUVWXZ' if ord(c) in self.index]
            cjk=[self.glyph(c).bounds for c in '国田日目中永你好字体' if ord(c) in self.index]
            source=latin if len(latin)>=4 else cjk
            if not source:source=[self.glyph(k).bounds for k in list(self.index)[:64]]
            heights=[b[3]-b[1] for b in source if b[3]>b[1]]
            if not heights:raise ValueError('Font has no drawable glyphs')
            height=statistics.median(heights)
            top=statistics.median(b[1] for b in source if b[3]>b[1])
            self._metrics=dict(reference_height=height,reference_top=top,
                               reference_source='Latin capitals' if len(latin)>=4 else 'CJK or sampled glyphs')
        return self._metrics

    def missing(self,text):return sorted(set(c for c in text if not c.isspace() and ord(c) not in self.index))

    @property
    def cjk_metrics(self):
        """A Chinese em box, independent of a font's smaller Latin capitals."""
        if self._cjk_metrics is None:
            bounds=[self.glyph(c).bounds for c in '国田日目中永你好字体写字机'
                    if ord(c) in self.index and self.glyph(c).bounds[3]>self.glyph(c).bounds[1]]
            if bounds:
                top=min(b[1] for b in bounds)
                self._cjk_metrics=dict(reference_height=max(b[3] for b in bounds)-top,
                                       reference_top=top,reference_source='CJK em bounds')
            else:
                self._cjk_metrics=self.metrics
        return self._cjk_metrics

    def text(self,text,height=5.0,origin=(0.0,0.0),spacing=None,line_pitch=None,*,reference_metrics=None):
        """Return millimetre strokes with uniform scale and shared baseline.

        Advances are inferred from ink bounds; original metadata advances have
        not been recovered. Every glyph keeps its original aspect and stroke order.
        """
        missing=self.missing(text)
        if missing:raise ValueError('Missing characters: '+''.join(missing))
        reference=self.metrics if reference_metrics is None else reference_metrics
        scale=height/reference['reference_height']
        top=reference['reference_top']
        spacing=height*0.18 if spacing is None else spacing
        line_pitch=height*1.6 if line_pitch is None else line_pitch
        ox,oy=origin;x,y=ox,oy;result=[]
        for char in text:
            if char=='\n':x=ox;y+=line_pitch;continue
            if char.isspace():x+=height*0.5;continue
            glyph=self.glyph(char);xmin,ymin,xmax,ymax=glyph.bounds
            for stroke in glyph.strokes(tolerance=0.02/scale):
                if len(stroke)<2:continue
                result.append([(x+(px-xmin)*scale,y+(py-top)*scale) for px,py in stroke])
            x+=max((xmax-xmin)*scale,height*0.12)+spacing
        return result

    def width(self,text,height=5.0):
        strokes=self.text(text,height)
        return max((x for s in strokes for x,y in s),default=0.0)

    def wrap(self,text,width,height=5.0):
        lines=[];line=''
        for word in text.split():
            trial=(line+' '+word).strip()
            if self.width(trial,height)<=width:line=trial
            else:
                if not line:raise ValueError(f'Word exceeds text box width: {word}')
                lines.append(line);line=word
                if self.width(line,height)>width:raise ValueError(f'Word exceeds text box width: {word}')
        if line:lines.append(line)
        return lines


def inspect_font(path):
    path=pathlib.Path(path)
    with GFont(path) as font:
        counts=collections.Counter();bounds=[];empty=0
        for codepoint in font.index:
            glyph=font.glyph(codepoint);counts.update(glyph.types)
            if glyph.coordinates:bounds.append(glyph.bounds)
            else:empty+=1
        return dict(file=path.name,version=font.version,bytes=path.stat().st_size,
            glyph_count=len(font.index),empty_glyphs=empty,segment_type_counts=dict(counts),
            trailing_zero_padding=font.trailing_padding,
            english_missing=font.missing('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,'),
            chinese_sample_missing=font.missing('你好世界写字机'),metrics=font.metrics,
            coordinate_bounds=[min(b[0] for b in bounds),min(b[1] for b in bounds),max(b[2] for b in bounds),max(b[3] for b in bounds)])


def inventory(directory,output):
    records=[];total_glyphs=0;errors=[]
    files=sorted(pathlib.Path(directory).glob('*.gfont'))
    for number,path in enumerate(files,1):
        try:
            record=inspect_font(path)
            total_glyphs+=record['glyph_count'];records.append(record)
        except Exception as error:
            errors.append(dict(file=path.name,error=str(error)))
        if number%20==0:print(f'Validated {number}/{len(files)} fonts; {total_glyphs} glyphs; {len(errors)} errors',flush=True)
    result=dict(fonts=records,errors=errors,font_count=len(files),valid_fonts=len(records),validated_glyphs=total_glyphs)
    pathlib.Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('fonts','errors')},ensure_ascii=False),flush=True)
    if errors:print(json.dumps(errors[:12],ensure_ascii=False),flush=True)
    return result


def export_text(font_path,text,output,height=5.0):
    directory=pathlib.Path(output);directory.mkdir(parents=True,exist_ok=True)
    with GFont(font_path) as font:
        strokes=font.text(text,height)
        points=[point for stroke in strokes for point in stroke]
        if not points:raise ValueError('Text contains no drawable strokes')
        xmin=min(x for x,y in points);ymin=min(y for x,y in points)
        normalized=[[(x-xmin+2,y-ymin+2) for x,y in stroke] for stroke in strokes]
        w=max(x for stroke in normalized for x,y in stroke)+2
        h=max(y for stroke in normalized for x,y in stroke)+2
        svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w*6:.1f}" height="{h*6:.1f}" viewBox="0 0 {w:.3f} {h:.3f}">',
             f'<rect width="{w}" height="{h}" fill="white"/>']
        svg += ['<polyline points="'+' '.join(f'{x:.4f},{y:.4f}' for x,y in s)+'" fill="none" stroke="#172a46" stroke-width="0.18" stroke-linecap="round" stroke-linejoin="round"/>' for s in normalized]
        svg.append('</svg>')
        (directory/'text.svg').write_text('\n'.join(svg),encoding='utf8')
        (directory/'paths.json').write_text(json.dumps(dict(source=str(pathlib.Path(font_path).resolve()),text=text,height_mm=height,
            metrics=font.metrics,strokes=normalized),ensure_ascii=False),encoding='utf8')
        try:
            from PIL import Image,ImageDraw
            image=Image.new('RGB',(math.ceil(w*6),math.ceil(h*6)),'white');draw=ImageDraw.Draw(image)
            for stroke in normalized:draw.line([(x*6,y*6) for x,y in stroke],fill='#172a46',width=2)
            image.save(directory/'text.png')
        except ImportError:pass
        print(json.dumps(dict(font=pathlib.Path(font_path).name,text=text,strokes=len(strokes),width_mm=w,height_mm=h),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    scan=sub.add_parser('inventory');scan.add_argument('directory');scan.add_argument('output')
    render=sub.add_parser('text');render.add_argument('font');render.add_argument('text');render.add_argument('output');render.add_argument('--height',type=float,default=5)
    args=p.parse_args()
    if args.command=='inventory':inventory(args.directory,args.output)
    else:export_text(args.font,args.text,args.output,args.height)
