"""Offline stroke analysis. Requires NumPy and Pillow; the web service does not.

Names identify the blood8 reference cohort only. Library-wide distances use
normalized vector ink, pen-lift counts and arc-length sampled stroke order.
"""
import argparse
import collections
import hashlib
import itertools
import json
import math
import pathlib
import re
import struct
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from backend.gfont import GFont

POOL=('永国田日目中你好字体天地人山水木火土金风雨云春夏秋冬年月时光清音明白'
      '的一了是在不有和我这他大来上个到说们为子生道出也得就那要下以会可成'
      '发对作能于之与自心长面家前看只如其学高多用行方经么去法都过定所动'
      '还进好小部事理些现无最重并当起同开合因从力实意天公问使情头物体每'
      '至社分总文手身相后向路声美知新应正外两间将本很品书海思远山川溪谷'
      '林竹花叶鸟鱼龙飞安宁和乐信友仁义礼智诚坚勇温柔轻快点横竖撇捺折钩'
      '石门车马言语衣食住走足口耳手心青红黑白东南西北电温度实验记录数据'
      '仪器控制系统笔记技术曲线灵敏观察测量变化光栅波长世界生活努力未来')


def signature(glyph):
    strokes=[np.asarray(s,dtype=np.float32) for s in glyph.strokes() if s]
    if not strokes:return None
    ink=np.concatenate(strokes)
    low,high=ink.min(axis=0),ink.max(axis=0)
    scale=float(max(high-low))
    if scale<=0:return None
    strokes=[(s-(low+high)/2)/scale for s in strokes]
    raster=Image.new('L',(32,32))
    draw=ImageDraw.Draw(raster)
    orientation=np.zeros(8,dtype=np.float32)
    lengths=[];segments=[]
    for stroke in strokes:
        points=[tuple(map(float,p*26+15.5)) for p in stroke]
        if len(points)>1:draw.line(points,fill=255,width=1)
        else:draw.point(points[0],fill=255)
        for a,b in zip(stroke,stroke[1:]):
            delta=b-a;length=float(np.linalg.norm(delta))
            if length:
                orientation[int((math.atan2(float(delta[1]),float(delta[0]))+math.pi)/(2*math.pi)*8)%8]+=length
                lengths.append(length);segments.append((a,b))
    image=np.asarray(raster.filter(ImageFilter.GaussianBlur(.65)).resize((16,16)),dtype=np.float32).ravel()/255
    image/=max(float(np.linalg.norm(image)),1e-9)
    orientation=np.sqrt(orientation/max(float(orientation.sum()),1e-9))
    feature=np.concatenate((image*math.sqrt(.85),orientation*math.sqrt(.15)))
    samples=np.zeros((32,2),dtype=np.float32)
    if lengths:
        cumulative=np.cumsum(lengths)
        targets=np.linspace(0,float(cumulative[-1]),32)
        indices=np.minimum(np.searchsorted(cumulative,targets,side='right'),len(lengths)-1)
        for i,(target,index) in enumerate(zip(targets,indices)):
            start=0 if index==0 else cumulative[index-1]
            a,b=segments[index]
            samples[i]=a+(b-a)*float((target-start)/lengths[index])
    raw=hashlib.sha256(struct.pack('>'+str(len(glyph.coordinates))+'f',*glyph.coordinates)+bytes(glyph.types)).digest()[:16]
    return feature,samples,len(strokes),raw


def analyze(font_dir,output,report_path,sample_count=128):
    started=time.monotonic()
    files=sorted(pathlib.Path(font_dir).glob('*.gfont'))
    fonts=[];coverage=[];frequency=collections.Counter();errors=[]
    for path in files:
        try:
            with GFont(path) as font:
                chars=frozenset(font.index)
                frequency.update(c for c in chars if 0x4e00<=c<=0x9fff)
            coverage.append(chars)
            fonts.append(dict(id=hashlib.sha256(path.name.encode()).hexdigest()[:16],name=path.stem,
                              file=path.name,bytes=path.stat().st_size,glyph_count=len(chars)))
        except Exception as error:errors.append(dict(file=path.name,error=str(error)))
    n=len(fonts)
    if not n:raise ValueError('字体目录中没有可分析的 .gfont 文件')
    eligible=[c for c in dict.fromkeys(POOL) if frequency[ord(c)]>=n*.8]
    for code,count in frequency.most_common():
        if chr(code) not in eligible and count>=n*.8:eligible.append(chr(code))
    chars=eligible[:sample_count];s=len(chars)
    if s<32:
        # Sparse / English-only libraries stay usable without fabricated similarities.
        result=dict(version=2, fonts=[dict(f, nearest=[],fallback_rankings=[]) for f in fonts], recommendations=[],
                    sample_text=''.join(dict.fromkeys(POOL))[:sample_count], duplicate_pairs=[], errors=errors,
                    note='共同汉字样本不足 32 个，暂不计算相似度。')
        pathlib.Path(output).write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')
        pathlib.Path(report_path).write_text(result['note'],encoding='utf8')
        return
    print(f'Indexed {n} fonts; comparing {s} common Chinese characters.',flush=True)
    features=np.zeros((n,s,264),dtype=np.float32)
    points=np.zeros((n,s,32,2),dtype=np.float32)
    counts=np.zeros((n,s),dtype=np.float32)
    hashes=np.zeros((n,s),dtype='S16')
    mask=np.zeros((n,s),dtype=np.float32)
    for i,info in enumerate(fonts):
        with GFont(pathlib.Path(font_dir)/info['file']) as font:
            for k,char in enumerate(chars):
                if ord(char) not in font.index:continue
                try:value=signature(font.glyph(char))
                except (ValueError,KeyError):continue
                if value:
                    features[i,k],points[i,k],counts[i,k],hashes[i,k]=value
                    mask[i,k]=1
        if i%30==0:print(f'Extracted {i+1}/{n}',flush=True)
    common=mask@mask.T
    cosine=features.reshape(n,-1)@features.reshape(n,-1).T/np.maximum(common,1)
    shape=np.maximum(0,1-cosine)
    distance=np.full((n,n),np.inf,dtype=np.float32)
    exact=np.zeros((n,n),dtype=np.float32)
    changed=np.zeros((n,n),dtype=np.float32)
    count_agree=np.zeros((n,n),dtype=np.float32)
    sequence_distance=np.zeros((n,n),dtype=np.float32)
    stroke_distance=np.zeros((n,n),dtype=np.float32)
    for i in range(n):
        shared=mask[i][None,:]*mask
        denom=np.maximum(shared.sum(axis=1),1)
        seq=np.sqrt(np.mean(np.sum((points-points[i])**2,axis=3),axis=2))
        stroke_diff=np.abs(counts-counts[i])/np.maximum(np.maximum(counts,counts[i]),1)
        sequence_distance[i]=(seq*shared).sum(axis=1)/denom
        stroke_distance[i]=(stroke_diff*shared).sum(axis=1)/denom
        distance[i]=.6*shape[i]+.3*sequence_distance[i]+.1*stroke_distance[i]
        exact[i]=((hashes==hashes[i])*shared).sum(axis=1)/denom
        per_shape=np.maximum(0,1-np.einsum('sk,nsk->ns',features[i],features))
        changed[i]=(((seq>.012)|(per_shape>.025)|(counts!=counts[i]))*shared).sum(axis=1)/denom
        count_agree[i]=((counts==counts[i])*shared).sum(axis=1)/denom
    distance[common<max(32,s*.5)]=np.inf
    np.fill_diagonal(distance,np.inf)
    anchors=[i for i,f in enumerate(fonts) if f['name'].startswith('blood8真迹') and '_kvenjoy_' in f['name']]
    anchor_pairs=[(i,j) for i,j in itertools.combinations(anchors,2) if np.isfinite(distance[i,j]) and exact[i,j]<.97]
    anchor_dist=[float(distance[i,j]) for i,j in anchor_pairs]
    threshold=float(np.quantile(anchor_dist,.9)) if anchor_dist else .26882
    scale=max(threshold/-math.log(.82),.01)
    scores=100*np.exp(-distance/scale)
    print(f'Reference pairs {len(anchor_pairs)}; threshold {threshold:.4f}',flush=True)
    # Verify apparent duplicates on every shared glyph, not just the sample.
    duplicate_candidates=[(i,j) for i,j in itertools.combinations(range(n),2)
                          if common[i,j]>=s*.65 and exact[i,j]>=.995]
    full_hashes={}
    for i in sorted({k for pair in duplicate_candidates for k in pair}):
        with GFont(pathlib.Path(font_dir)/fonts[i]['file']) as font:
            full_hashes[i]={c:hashlib.sha256(font.archive.read(entry)).digest() for c,entry in font.index.items()}
    aliases={};duplicates=[]
    for i,j in duplicate_candidates:
        shared=coverage[i]&coverage[j]
        equal=sum(full_hashes[i][c]==full_hashes[j][c] for c in shared)
        ratio=equal/max(len(shared),1)
        if ratio>=.97:
            keep,drop=sorted((i,j),key=lambda k:(-len(coverage[k]),'_kvenjoy_' not in fonts[k]['name'],fonts[k]['name']))
            aliases[drop]=keep
            duplicates.append(dict(a=fonts[i]['id'],b=fonts[j]['id'],shared_glyphs=len(shared),identical_pct=round(ratio*100,2)))

    def pair(i,j):
        return dict(score=round(float(scores[i,j]),1),sample_characters=int(common[i,j]),
                    changed_pct=round(float(changed[i,j])*100,1),identical_sample_pct=round(float(exact[i,j])*100,1),
                    same_stroke_count_pct=round(float(count_agree[i,j])*100,1),shared_glyphs=len(coverage[i]&coverage[j]),
                    ink_distance=round(float(shape[i,j]),5),path_distance=round(float(sequence_distance[i,j]),5))

    def compatible(i,j):
        return (np.isfinite(distance[i,j]) and distance[i,j]<=threshold and changed[i,j]>=.08 and
                exact[i,j]<.97 and len(coverage[i]&coverage[j])>=1800)

    valid=[i for i,f in enumerate(fonts) if i not in aliases and len(coverage[i])>=2000 and mask[i].sum()>=s*.75]
    groups=[];used=set()
    def canonical(i):
        while i in aliases:i=aliases[i]
        return i
    anchor_canonical={canonical(i) for i in anchors}
    anchor_valid=sorted(i for i in anchor_canonical if i in valid)
    anchor_combos=[c for c in itertools.combinations(anchor_valid,3) if all(compatible(i,j) for i,j in itertools.combinations(c,2))]
    if anchor_combos:
        groups.append(min(anchor_combos,key=lambda c:sum(distance[i,j] for i,j in itertools.combinations(c,2))))
        used.update(groups[-1])
    candidates=[]
    for i in valid:
        neighbors=[j for j in np.argsort(distance[i]) if j in valid and j!=i and compatible(i,j)][:10]
        for j,k in itertools.combinations(neighbors,2):
            if compatible(j,k):
                group=tuple(sorted((i,int(j),int(k))))
                cost=max(float(distance[a,b]) for a,b in itertools.combinations(group,2))
                candidates.append((cost,group))
    for cost,group in sorted(set(candidates)):
        if len(groups)>=7:break
        if used.intersection(group):continue
        # Offer different neighborhoods rather than seven nearly identical mixes.
        if any(np.mean([distance[a,b] for a in group for b in prior])<threshold*.45 for prior in groups):continue
        groups.append(group);used.update(group)
    suggestions=[]
    for index,group in enumerate(groups):
        group=sorted(group,key=lambda i:sum(distance[i,j] for j in group if j!=i))
        pairs=[pair(i,j) for i,j in itertools.combinations(group,2)]
        name='blood8 真迹 · 自然微差' if index==0 and all(i in anchor_canonical for i in group) else fonts[group[0]]['name'].split('_')[0]+' · 相近笔形'
        suggestions.append(dict(id='similar-'+hashlib.sha256('|'.join(fonts[i]['id'] for i in group).encode()).hexdigest()[:12],
            name=name,mix=[dict(font_id=fonts[i]['id'],name=fonts[i]['name'],weight=w) for i,w in zip(group,(60,25,15))],
            score=round(min(p['score'] for p in pairs),1),changed_pct=round(float(np.mean([p['changed_pct'] for p in pairs])),1),
            sample_characters=min(p['sample_characters'] for p in pairs),shared_glyphs=len(set.intersection(*(set(coverage[i]) for i in group))),pairs=pairs))
    for i,info in enumerate(fonts):
        info['sample_characters']=int(mask[i].sum())
        info['nearest']=[dict(font_id=fonts[int(j)]['id'],**pair(i,int(j))) for j in np.argsort(distance[i])[:6] if np.isfinite(distance[i,j])]
        info['fallback_rankings']=[[fonts[int(j)]['id'],round(float(distance[i,j]),8),round(float(scores[i,j]),1)]
                                   for j in np.argsort(distance[i]) if np.isfinite(distance[i,j])]
        if i in aliases:info['duplicate_of']=fonts[aliases[i]]['id']
    references=[dict(a=fonts[i]['name'],b=fonts[j]['name'],**pair(i,j)) for i,j in anchor_pairs]
    finite_scores=scores[np.triu(np.isfinite(distance),1)]
    distribution=list(map(float,np.quantile(finite_scores,[.05,.5,.95]))) if finite_scores.size else [None,None,None]
    result=dict(version=2,font_count=n,sample_text=''.join(chars),reference_family='blood8真迹',reference_pairs=references,
                metric='0.60 × normalized ink distance + 0.30 × ordered arc-length stroke distance + 0.10 × pen-lift-count difference',
                calibration=dict(reference_pairs=len(anchor_pairs),reference_distance_min=min(anchor_dist,default=None),reference_distance_max=max(anchor_dist,default=None),
                                 recommendation_distance_max=threshold,reference_score_min=round(min(float(scores[i,j]) for i,j in anchor_pairs),1) if anchor_pairs else None,
                                 fallback=not bool(anchor_pairs)),
                score_distribution=dict(zip(('p05','median','p95'),distribution)),
                fonts=fonts,duplicate_pairs=duplicates,recommendations=suggestions,errors=errors)
    pathlib.Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    lines=['# 字体笔画相似性分析','',f'分析 {n} 个字体文件，抽样 {s} 个常用汉字；以 blood8 真迹系列的 {len(anchor_pairs)} 对非重复变体作为距离参照。',
           '', '评分基于归一化实际笔画的墨迹位置、书写路径顺序和抬笔分段，不代表同一作者的概率。文件名仅用于建立参照组，整个库的近邻搜索使用笔画数据。',
           '',f'推荐阈值采用参照组距离的第 90 百分位：{threshold:.5f}，对应评分 82/100。疑似重复文件对使用全部共同字的原始字形记录复核，发现 {len(duplicates)} 对高度重复/子集关系；不将重复项作为混写变化来源。',
           '',f'全库有效配对评分中位数：{distribution[1]}；第 95 百分位：{distribution[2]}。推荐组合要求三者两两接近，并保留可测得的字形变化。',
           '', '## blood8 真迹参照组','', '| 字体 A | 字体 B | 评分 | 笔画段数一致占比 | 抽样不同字形占比 |','|---|---|---:|---:|---:|']
    for p in references:lines.append(f'| {p["a"]} | {p["b"]} | {p["score"]} | {p["same_stroke_count_pct"]}% | {p["changed_pct"]}% |')
    if not anchor_pairs:lines+=['','当前字库没有可用参照组，采用已有参照距离阈值 0.26882。']
    lines+=['','从参照样本可见，同系列的字形也会有不同的抬笔分段。风格相似性需要综合墨迹形状、走势和分段来判断；段数一致占比描述字体文件中的实际路径分段。',
            '','## 推荐组合','', '| 组合 | 字体及权重 | 最低相似评分 | 抽样不同字形占比 | 三者共同字 |','|---|---|---:|---:|---:|']
    for g in suggestions:
        lines.append('| '+g['name']+' | '+' / '.join(f'{f["name"]} {f["weight"]}%' for f in g['mix'])+f' | {g["score"]} | {g["changed_pct"]}% | {g["shared_glyphs"]} |')
    lines+=['','“变化字”指归一化路径或笔画分段达到差异阈值的抽样字；不是排版抖动或字体数量。推荐使用 60/25/15 作为起点，可按预览调整。',
            '','局限：抽样不能保证每个字都相似；相同字覆盖少的字体不会强行评分。字形尺度被归一化，但倾斜、宽扁比例和笔画顺序保留。实际文本缺字仍由已有混排回退规则处理。']
    pathlib.Path(report_path).write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps(dict(fonts=n,groups=len(suggestions),duplicates=len(duplicates),seconds=round(time.monotonic()-started,1),
                         recommendations=[dict(name=g['name'],score=g['score'],changed_pct=g['changed_pct'],fonts=[m['name'] for m in g['mix']]) for g in suggestions]),ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--font-dir',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    analyze(args.font_dir,args.output,args.report)
