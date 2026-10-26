"""Offline font speed index, using actual vectors and an optional production inventory."""
import argparse
import hashlib
import json
import pathlib
import sys
import tarfile
import tempfile

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from backend.gfont import GFont
from backend.font_speed import BENCHMARK, assign_scores, motion_cost


def analyze(font_dir,similarity,output,report,inventory=None,overrides=None):
    source=pathlib.Path(font_dir)
    sample=json.loads(pathlib.Path(similarity).read_text(encoding='utf8'))['sample_text']
    records=json.loads(pathlib.Path(inventory).read_text(encoding='utf8')) if inventory else [
        dict(id=hashlib.sha256(p.name.encode()).hexdigest()[:16],file=p.name,bytes=p.stat().st_size) for p in sorted(source.glob('*.gfont'))]
    measurements={};errors=[]
    with tempfile.TemporaryDirectory() as temp:
        replacements={}
        if overrides:
            with tarfile.open(overrides) as archive:
                for item in archive.getmembers():
                    if not item.isfile() or pathlib.Path(item.name).name!=item.name:raise ValueError('Invalid override filename')
                    path=pathlib.Path(temp)/item.name;path.write_bytes(archive.extractfile(item).read());replacements[item.name]=path
        for index,info in enumerate(records):
            path=replacements.get(info['file'],source/info['file'])
            if path.stat().st_size!=info['bytes']:raise ValueError('Font inventory changed: '+info['file'])
            costs={}
            with GFont(path) as font:
                reference=font.cjk_metrics
                for char in sample:
                    if ord(char) not in font.index:continue
                    try:
                        strokes=font.text(char,height=BENCHMARK['size_mm'],spacing=0,reference_metrics=reference)
                        width=max((x for s in strokes for x,y in s),default=0.)
                        cost=motion_cost(strokes,width+BENCHMARK['spacing_mm'])
                        if cost:costs[char]=cost
                    except (ValueError,KeyError) as error:errors.append(dict(font=info['file'],char=char,error=str(error)))
            measurements[info['id']]=costs
            if index%50==0:print(f'Measured {index+1}/{len(records)}',flush=True)
    scores=assign_scores(measurements)
    data=dict(version=1,benchmark=BENCHMARK,sample_text=sample,fonts=[dict(**f,speed=scores[f['id']]) for f in records],errors=errors,
        method='Per-character cost divided by library median for that same character; geometric mean, reciprocal scaled to library median = 100.',
        limitations='模型估计，非实测速度。包括笔画路径、笔间空移、抬落笔及简化加减速；不模拟拐角减速、串口吞吐、行尾换行和起止回位。缺字不按零耗时计，少于96个样本为参考分。')
    pathlib.Path(output).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    lines=['# 字体速度评分','',BENCHMARK['description'],'',data['method'],'',
        '每个字先与全库同一字的耗时中位数比较，再综合评分；缺字不计为零耗时，避免缺字多的字体虚高。少于 96 个共同样本字的评分标为“样本较少”。',
        '',data['limitations'],'','| 字体 | 速度评分 | 样本字 | 平均笔画段数 | 落笔 mm/字 | 空移 mm/字 | 标准百字模型秒数 |','|---|---:|---:|---:|---:|---:|---:|']
    for f in sorted(data['fonts'],key=lambda f:-(f['speed']['score'] or 0)):
        s=f['speed'];lines.append(f"| {pathlib.Path(f['file']).stem} | {s['score']} | {s['sample_characters']} | {s.get('strokes_per_character','—')} | {s.get('draw_mm_per_character','—')} | {s.get('move_mm_per_character','—')} | {s.get('standardized_seconds_per_100','—')} |")
    pathlib.Path(report).write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps(dict(fonts=len(records),scored=sum(s['score'] is not None for s in scores.values()),limited=sum(s['confidence']=='limited' for s in scores.values()),errors=len(errors))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--font-dir',required=True)
    parser.add_argument('--similarity',default='backend/font_similarity.json')
    parser.add_argument('--output',default='backend/font_speed.json')
    parser.add_argument('--report',default='docs/font-speed-report.md')
    parser.add_argument('--inventory')
    parser.add_argument('--overrides')
    args=parser.parse_args()
    analyze(args.font_dir,args.similarity,args.output,args.report,args.inventory,args.overrides)
